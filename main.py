import os
import argparse
import asyncio
import socket
import json
from datetime import datetime
from contextlib import asynccontextmanager
import logging
import logging.config

from dotenv import load_dotenv
from aiofile import AIOFile
from async_timeout import timeout

import gui
from handy_nursery import create_handy_nursery

DELAY_TO_LOAD_HISTORY = 3
DELAY_TO_RECONNECT = 1
TIMEOUT_WATCH = 30
TIMEOUT_PING_PONG = 30
DELAY_TO_PING_PONG = 3


class InvalidToken(Exception):
    def __init__(self, message):
        self.message = message


class ConnectionError(Exception):
    def __init__(self, message):
        self.message = message


class ChatLogger(logging.Handler):
    def emit(self, record):
        log_entry = self.format(record)
        print(log_entry)


class UserInterrupt(Exception):
    def __init__(self, message):
        self.message = message


dictLogConfig = {
    'version': 1,
    'handlers': {
        'handler': {
            '()': ChatLogger,
            'formatter': 'formatter'
            }
    },
    'loggers': {
        'chat_logger': {
            'handlers': ['handler'],
            'level': 'INFO'
        }
    },
    'formatters': {
        'formatter': {
            'format': '%(asctime)s - %(message)s'
        }
    }
}


logging.config.dictConfig(dictLogConfig)
watchdog_logger = logging.getLogger('chat_logger')
handler = ChatLogger()
watchdog_logger.addHandler(handler)


def sanitize(text):
    return text.replace('\n', '').replace('\r', '')


def get_args(host, port_to_read, port_to_write, token, filepath):
    parser = argparse.ArgumentParser(description='Undergroung Chat CLI')
    parser.add_argument('--host', help='Host', type=str, default=host)
    parser.add_argument('--port_to_read', help='Port to read', type=int, default=port_to_read)
    parser.add_argument('--port_to_write', help='Port to write', type=int, default=port_to_write)
    parser.add_argument('--token', help='Token', type=str, default=token)
    parser.add_argument('--filepath', help='Log file', type=str, default=filepath)
    args = parser.parse_args()
    return vars(args)


@asynccontextmanager
async def get_stream(host, port):
    reader, writer = await asyncio.open_connection(host, port)
    try:
        yield (reader, writer)
    finally:
        writer.close()


async def register(reader, writer, username):
    # Registration and writing token to .env file.
    data = await reader.readline()
    username = sanitize(username)
    writer.write('{}\n'.format(username).encode())
    data = await reader.readline()
    token = json.loads(data.decode())['account_hash']
    username = json.loads(data.decode())['nickname']
    async with AIOFile('.env', 'a+') as afp:
        await afp.write('TOKEN={}'.format(token))
    return username


async def auth(reader, writer, token, queues):
    # Authorization with token.
    data = await reader.readline()
    writer.write('{}\n'.format(token).encode())
    data = await reader.readline()
    data_json = json.loads(data.decode())
    if not data_json:
        username = gui.msg_box(
                        'Invalid token', 'If you\'re registered user, please'
                        ' click "Cancel" and check your .env file.\n If you\'re'
                        ' the new one, enter yor name below and click "OK"')
        if username is None:
            raise UserInterrupt('User Interrupt')
        username = await register(reader, writer, username)
        return username
    return data_json['nickname']


async def log_msgs(filepath, queues):
    # Messages logging to file.
    async with AIOFile(filepath, 'a+') as afp:
        while True:
            data = await queues['logs'].get()
            log = '[{}] {}'.format(datetime.now().strftime('%d.%m.%y %H:%M'), data)
            await afp.write(log)
            await afp.fsync()


async def watch_for_connection(queues):
    # Connection's watchdog.
    while True:
        try:
            async with timeout(TIMEOUT_WATCH):
                msg = await queues['watchdog'].get()
        except (KeyboardInterrupt, gui.TkAppClosed, asyncio.CancelledError):
            raise UserInterrupt('UserInterrupt')
        except asyncio.TimeoutError:
            watchdog_logger.info('%s sec timeout is elapsed' % TIMEOUT_WATCH)
            raise ConnectionError('ConnectionError')


async def ping_pong(reader, writer):
    # Ping the server.
    while True:
        try:
            async with timeout(TIMEOUT_PING_PONG):
                writer.write('\n'.encode())
                data = await reader.readline()
            await asyncio.sleep(DELAY_TO_PING_PONG)
        except socket.gaierror:
            watchdog_logger.info('socket.gaierror')
            queues['statuses'].put_nowait(gui.ReadConnectionStateChanged.CLOSED)
            queues['statuses'].put_nowait(gui.SendingConnectionStateChanged.CLOSED)
            raise ConnectionError('socket.gaierror (no internet connection)')


async def read_msgs(host, port, queues):
    # Read messages from opened stream and update "messages" queue.
    queues['statuses'].put_nowait(gui.ReadConnectionStateChanged.ESTABLISHED)
    async with get_stream(host, port) as (reader, writer):
        while True:
            data = await reader.readline()
            queues['messages'].put_nowait(data.decode())
            queues['logs'].put_nowait(data.decode())
            queues['watchdog'].put_nowait('Connection is alive. New message')


async def send_msgs(writer, queues):
    # Listen "sending" queue and sends messages.
    queues['statuses'].put_nowait(gui.SendingConnectionStateChanged.ESTABLISHED)
    while True:
        msg = await queues['sending'].get()
        writer.write('{}\n'.format(msg).encode())
        queues['watchdog'].put_nowait('Connection is alive. Message sent')


async def handle_connection(host, port_to_read, port_to_write, token, filepath, queues):
    queues['statuses'].put_nowait(gui.ReadConnectionStateChanged.INITIATED)
    queues['statuses'].put_nowait(gui.SendingConnectionStateChanged.INITIATED)
    # Open new stream.
    async with get_stream(host, port_to_write) as (reader, writer):
        queues['watchdog'].put_nowait('Connection is alive. Prompt before auth')
        # Authorization.
        nickname = await auth(reader, writer, token, queues)
        queues['statuses'].put_nowait(gui.NicknameReceived(nickname))
        queues['messages'].put_nowait(
            'WELCOME BACK {}!\nLoading chat history and connectiong to chat '
            'in {} seconds...\n'.format(nickname, DELAY_TO_LOAD_HISTORY))
        await asyncio.sleep(DELAY_TO_LOAD_HISTORY)
        # Read chat history.
        async with AIOFile(filepath, 'a+') as afp:
            history = await afp.read()
            if history:
                queues['messages'].put_nowait(
                                    '*** CHAT HISTORY\n{}***\n'.format(history))
        while True:
            # Run grandchildren tasks.
            async with create_handy_nursery() as nursery:
                nursery.start_soon(log_msgs(filepath, queues))
                nursery.start_soon(read_msgs(host, port_to_read, queues))
                nursery.start_soon(send_msgs(writer, queues))
                nursery.start_soon(watch_for_connection(queues))
                nursery.start_soon(ping_pong(reader, writer))


async def main(host, port_to_read, port_to_write, token, filepath):
    queues = {
        'messages': asyncio.Queue(),
        'logs': asyncio.Queue(),
        'sending': asyncio.Queue(),
        'statuses': asyncio.Queue(),
        'watchdog': asyncio.Queue(),
    }
    try:
        # Run children tasks.
        async with create_handy_nursery() as nursery:
            nursery.start_soon(gui.draw(
                                    queues['messages'],
                                    queues['sending'],
                                    queues['statuses']))
            nursery.start_soon(handle_connection(
                                    host,
                                    port_to_read,
                                    port_to_write,
                                    token,
                                    filepath,
                                    queues))
    except UserInterrupt as e:
        print(e)
    except ConnectionError as e:
        queues['statuses'].put_nowait(gui.ReadConnectionStateChanged.CLOSED)
        queues['statuses'].put_nowait(gui.SendingConnectionStateChanged.CLOSED)
        await asyncio.sleep(DELAY_TO_RECONNECT)
    except (ConnectionError, socket.gaierror) as e:
        print('socket.gaierror (no internet connection)')


if __name__ == '__main__':
    load_dotenv()
    args = get_args(
            os.getenv('HOST'),
            os.getenv('PORT_TO_READ'),
            os.getenv('PORT_TO_WRITE'),
            os.getenv('TOKEN'),
            os.getenv('FILEPATH'))
    # Run main coroutine (initial task).
    asyncio.run(main(**args))

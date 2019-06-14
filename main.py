import os
import argparse
import asyncio
import socket
import json
import time
from datetime import datetime
import contextlib

import requests
from dotenv import load_dotenv
from aiofile import AIOFile
from async_timeout import timeout
import aionursery

import gui
from chat_logging import create_logger

DELAY_TO_LOAD_HISTORY = 3
DELAY_TO_RECONNECT = 2
TIMEOUT_WATCH = 30
TIMEOUT_PING_PONG = 30
DELAY_TO_PING_PONG = 3


class InvalidToken(Exception):
    def __init__(self, message):
        self.message = message


class ConnectionError(Exception):
    def __init__(self, message):
        self.message = message


class UserInterrupt(Exception):
    def __init__(self, message):
        self.message = message


def get_args(host, port_to_read, port_to_write, token, filepath):
    parser = argparse.ArgumentParser(description='Undergroung Chat CLI')
    parser.add_argument('--host', help='Host', type=str, default=host)
    parser.add_argument('--port_to_read', help='Port to read', type=int, default=port_to_read)
    parser.add_argument('--port_to_write', help='Port to write', type=int, default=port_to_write)
    parser.add_argument('--token', help='Token', type=str, default=token)
    parser.add_argument('--filepath', help='Log file', type=str, default=filepath)
    args = parser.parse_args()
    return vars(args)


async def read_msgs(host, port, queues):
    reader, writer = await asyncio.open_connection(host, port)
    while True:
        try:
            data = await reader.readline()
            queues['messages'].put_nowait(data.decode())
            queues['logs'].put_nowait(data.decode())
            queues['watchdog'].put_nowait('Connection is alive. New message in chat')
        except (KeyboardInterrupt, gui.TkAppClosed) as e:
            watchdog_logger.info('KeyboardInterrupt or gui.TkAppClosed')
            raise UserInterrupt


async def log_msgs(filepath, queues):
    async with AIOFile(filepath, 'a+') as afp:
        while True:
            try:
                data = await queues['logs'].get()
                log = '[{}] {}'.format(datetime.now().strftime('%d.%m.%y %H:%M'), data)
                await afp.write(log)
                await afp.fsync()
            except (KeyboardInterrupt, gui.TkAppClosed) as e:
                watchdog_logger.info('KeyboardInterrupt or gui.TkAppClosed')
                raise UserInterrupt


def sanitize(text):
    return text.replace('\n', '').replace('\r', '')


async def send_msgs(host, port, nickname, queues):
    while True:
        try:
            queues['statuses'].put_nowait(gui.SendingConnectionStateChanged.ESTABLISHED)
            msg = await queues['sending'].get()
            msg_chat = '{}: {}\n'.format(nickname, sanitize(msg))
            queues['messages'].put_nowait(msg_chat)
            queues['logs'].put_nowait(msg_chat)
            queues['watchdog'].put_nowait('Connection is alive. Message sent')
        except (KeyboardInterrupt, gui.TkAppClosed) as e:
            watchdog_logger.info('KeyboardInterrupt or gui.TkAppClosed')
            raise UserInterrupt


async def auth(reader, writer, token, queues):
    data = await reader.readline()
    writer.write('{}\n'.format(token).encode())
    data = await reader.readline()
    data_json = json.loads(data.decode())
    if not data_json:
        raise InvalidToken('Invalid token.Please check your .env file')
    return data_json['nickname']


@contextlib.asynccontextmanager
async def create_handy_nursery():
    try:
        async with aionursery.Nursery() as nursery:
            yield nursery
    except aionursery.MultiError as e:
        if len(e.exceptions) == 1:
            raise e.exceptions[0]
        raise


async def watch_for_connection(queues):
    while True:
        try:
            async with timeout(TIMEOUT_WATCH):
                msg = await queues['watchdog'].get()
        except asyncio.TimeoutError:
            watchdog_logger.info('%s sec timeout is elapsed' % TIMEOUT_WATCH)
            raise ConnectionError('ConnectionError')


async def ping_pong(reader, writer):
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


async def handle_connection(host, port_to_read, port_to_write, token, filepath, queues):
    queues['statuses'].put_nowait(gui.ReadConnectionStateChanged.INITIATED)
    queues['statuses'].put_nowait(gui.SendingConnectionStateChanged.INITIATED)
    reader, writer = await asyncio.open_connection(host, port_to_write)
    queues['watchdog'].put_nowait('Connection is alive. Prompt before auth')
    nickname = await auth(reader, writer, token, queues)
    queues['statuses'].put_nowait(gui.NicknameReceived(nickname))
    queues['statuses'].put_nowait(gui.ReadConnectionStateChanged.ESTABLISHED)
    queues['statuses'].put_nowait(gui.SendingConnectionStateChanged.ESTABLISHED)
    queues['messages'].put_nowait(
        'WELCOME BACK {}!\nLoading chat history and connectiong to chat '
        'in {} seconds...\n'.format(nickname, DELAY_TO_LOAD_HISTORY))
    await asyncio.sleep(DELAY_TO_LOAD_HISTORY)
    async with AIOFile(filepath, 'a+') as afp:
        history = await afp.read()
        if history:
            queues['messages'].put_nowait('*** CHAT HISTORY\n{}***\n'.format(history))
    while True:
        try:
            async with create_handy_nursery() as nursery:
                nursery.start_soon(log_msgs(filepath, queues))
                nursery.start_soon(read_msgs(host, port_to_read, queues))
                nursery.start_soon(send_msgs(host, port_to_read, nickname, queues))
                nursery.start_soon(watch_for_connection(queues))
                nursery.start_soon(ping_pong(reader, writer))
                queues['statuses'].put_nowait(gui.ReadConnectionStateChanged.ESTABLISHED)
                queues['statuses'].put_nowait(gui.SendingConnectionStateChanged.ESTABLISHED)
        except ConnectionError as e:
            queues['statuses'].put_nowait(gui.ReadConnectionStateChanged.CLOSED)
            queues['statuses'].put_nowait(gui.SendingConnectionStateChanged.CLOSED)
            await asyncio.sleep(DELAY_TO_RECONNECT)
        finally:
            writer.close()


async def main(host, port_to_read, port_to_write, token, filepath):
    queues = {
        'messages': asyncio.Queue(),
        'logs': asyncio.Queue(),
        'sending': asyncio.Queue(),
        'statuses': asyncio.Queue(),
        'watchdog': asyncio.Queue(),
    }
    try:
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
    except (KeyboardInterrupt, gui.TkAppClosed):
        watchdog_logger.info('KeyboardInterrupt or gui.TkAppClosed')
        raise UserInterrupt('UserInterrupt')
    except InvalidToken as err:
        gui.msg_box('Error', err)


if __name__ == '__main__':
    load_dotenv()
    args = get_args(
            os.getenv('HOST'),
            os.getenv('PORT_TO_READ'),
            os.getenv('PORT_TO_WRITE'),
            os.getenv('TOKEN'),
            os.getenv('FILEPATH'))
    watchdog_logger = create_logger()
    try:
        asyncio.run(main(**args))
    except (ConnectionError, socket.gaierror):
        print('socket.gaierror (no internet connection)')
    except (KeyboardInterrupt, gui.TkAppClosed, UserInterrupt) as e:
        print('UserInterrupt', e)

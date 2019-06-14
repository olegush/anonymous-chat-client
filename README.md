# Anonymous Сhat Сlient

This is [Tkinter GUI](https://docs.python.org/3/library/tkinter.html) based program for chating uses asyncio [streams](https://docs.python.org/3/library/asyncio-stream.html) and [queues](https://docs.python.org/3/library/asyncio-queue.html). Asyncio tasks manage with [aionursery](https://pypi.org/project/aionursery/). User authorization by token, creating new account. The explored chat was created by [Devman team](https://dvmn.org/) especially for educational purposes.


## How to install

1. Python 3.7 and libraries from **requirements.txt** should be installed.

```bash
pip install -r requirements.txt
```

2. Put all necessary parameters to **.env** file. This is default parameters for utility and you can change them by CLI arguments.

```
HOST=host_to_connect
PORT_TO_READ=port_for_reading
PORT_TO_WRITE=port_for_writing
TOKEN=token
FILEPATH=path_to_log_file
```

## Quickstart

Run **server.py** with arguments. Also you can use environment variables as parameters by default.

```bash
python server.py [--host] [--port_to_read] [--port_to_write] [--token] [--filepath]
```


## Project Goals

The code is written for educational purposes on online-course for web-developers [dvmn.org](https://dvmn.org/).

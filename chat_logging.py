import logging
import time

def create_logger():
    class ChatLogger(logging.Handler):
        def emit(self, record):
            log_entry = self.format(record)
            print(log_entry)
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    formatter = logging.Formatter('%(asctime)s - %(message)s', datefmt='%s')
    handler = ChatLogger()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    return logger

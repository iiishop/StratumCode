import socket
import time
from urllib.request import urlopen


def free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]


def wait_for(url, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urlopen(url)
            return
        except OSError:
            time.sleep(0.3)

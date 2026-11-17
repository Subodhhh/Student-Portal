"""Module-level functions run in the isolated extraction process by test_upload_security.py (the child imports them by name)."""
import os
import time


def echo(value):
    return value


def sleep_forever():
    time.sleep(60)


def allocate(megabytes):
    blocks = [bytearray(1024 * 1024) for _ in range(megabytes)]      # far beyond the memory budget
    return len(blocks)


def crash():
    os._exit(3)


def recurse(n=0):
    return recurse(n + 1)


def burn_cpu():
    x = 0
    while True:
        x += 1

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cancellation-aware artwork slots, with foreground priority and a fixed cap."""
from collections import deque
import threading
import time


class ArtworkSlots:
    def __init__(self, limit):
        self.limit = int(limit)
        if self.limit < 1: raise ValueError('Invalid slot count')
        self.condition = threading.Condition()
        self.active = 0
        self.waiters = deque()

    @staticmethod
    def _priority(value):
        return bool(value() if callable(value) else value)

    def acquire(self, blocking=True, timeout=None, priority=False, check=None):
        deadline = None if timeout is None else time.monotonic() + max(0, timeout)
        ticket = (object(), priority)
        with self.condition:
            self.waiters.append(ticket)
            try:
                while True:
                    if check is not None: check()
                    # A promoted neighbor changes priority in place; its
                    # queue position and existing download request survive.
                    first = next((t for t in self.waiters if self._priority(t[1])), self.waiters[0])
                    if self.active < self.limit and first is ticket:
                        self.waiters.remove(ticket)
                        self.active += 1
                        self.condition.notify_all()
                        return True
                    remaining = None if deadline is None else deadline-time.monotonic()
                    if not blocking or (remaining is not None and remaining <= 0): return False
                    self.condition.wait(.05 if remaining is None else min(.05, remaining))
            finally:
                if ticket in self.waiters:
                    self.waiters.remove(ticket)
                    self.condition.notify_all()

    def acquire_for(self, token=None, priority=False):
        return self.acquire(priority=priority, check=token.check if token is not None else None)

    def release(self):
        with self.condition:
            if self.active <= 0: raise ValueError('Artwork slot released too often')
            self.active -= 1
            self.condition.notify_all()

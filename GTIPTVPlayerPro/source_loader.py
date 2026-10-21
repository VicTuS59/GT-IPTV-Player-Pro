# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""A screen-owned latest-request source reader; widgets stay on the GUI loop."""
import threading


class SourceLoader:
    def __init__(self, timer, connect, apply):
        self.timer = timer
        self.apply = apply
        self.condition = threading.Condition()
        self.generation = 0
        self.pending = None
        self.result = None
        self.closed = False
        self.thread = None
        connect(timer, self.poll)

    def request(self, load):
        with self.condition:
            if self.closed:
                return
            self.generation += 1
            self.pending = (self.generation, load)
            self.result = None
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="GT-SourceReader", daemon=True)
                try:
                    self.thread.start()
                except (OSError, RuntimeError) as error:
                    self.thread = None
                    self.pending = None
                    self.result = (self.generation, None, error)
            self.condition.notify_all()
        self.timer.start(40, True)

    def _run(self):
        while True:
            with self.condition:
                while self.pending is None and not self.closed:
                    self.condition.wait()
                if self.closed:
                    return
                generation, load = self.pending
                self.pending = None
            try:
                value, error = load(), None
            except Exception as caught:
                value, error = None, caught
            with self.condition:
                if not self.closed and generation == self.generation:
                    self.result = (generation, value, error)
                    self.condition.notify_all()
            # An idle reader must not retain a second copy of a large result
            # or the previous request's controller through its local closure.
            load = value = error = None

    def poll(self):
        with self.condition:
            result = self.result
            self.result = None
            if self.closed:
                return
        if result is not None and result[0] == self.generation:
            self.apply(result[1], result[2])
        else:
            self.timer.start(40, True)

    def cancel(self):
        """Discard pending/in-flight results while retaining the idle worker."""
        self.timer.stop()
        with self.condition:
            self.generation += 1
            self.pending = self.result = None

    def close(self):
        self.timer.stop()
        with self.condition:
            self.closed = True
            self.generation += 1
            self.pending = self.result = None
            self.apply = None
            self.condition.notify_all()

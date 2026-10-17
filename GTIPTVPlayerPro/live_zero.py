# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Separate fullscreen live TV's short 0 from its held restart gesture."""

import time


HOLD_SECONDS = 1.0


class LiveZeroKey(object):
    def __init__(self, timer_factory, is_active, on_short, on_long,
                 prime_native=None, clock=None):
        self._active = is_active
        self._short = on_short
        self._long = on_long
        self._prime_native = prime_native
        self._clock = clock or time.monotonic
        self._timer = timer_factory()
        signal = getattr(self._timer, "timeout", None)
        connect = getattr(signal, "connect", None)
        self._connection = (
            connect(self._hold_expired) if callable(connect) else None
        )
        if not callable(connect):
            self._timer.callback.append(self._hold_expired)
        self._pressed_at = None
        self._fired = False
        self._priming = False
        self._closed = False

    def cancel(self):
        self._timer.stop()
        self._pressed_at = None
        self._fired = False

    def close(self):
        self.cancel()
        self._closed = True
        self._connection = None

    def _hold_expired(self):
        if self._closed or self._pressed_at is None or self._fired:
            return
        if not self._active():
            self.cancel()
            return
        remaining = HOLD_SECONDS - (self._clock() - self._pressed_at)
        if remaining > 0:
            self._timer.start(max(1, int(remaining * 1000 + 0.999)), True)
            return
        self._fired = True
        self._timer.stop()
        self._long()

    def handle(self, flag):
        if self._priming:
            return 1
        if self._closed or not self._active():
            self.cancel()
            return 0
        if flag == 0:
            if self._pressed_at is None:
                self._pressed_at = self._clock()
                self._fired = False
                self._timer.start(int(HOLD_SECONDS * 1000), True)
            return 1
        if self._pressed_at is None:
            # Stray releases/repeats must not reach the ordinary 0 ActionMap.
            return 1
        if flag == 3 and self._prime_native is not None:
            # OpenPLi drops break after its native long flag; OpenATV may
            # replace it with stop. A consumed make preserves native release
            # bookkeeping without resetting our original one-second deadline.
            self._priming = True
            try:
                self._prime_native()
            finally:
                self._priming = False
        if flag in (1, 5):
            elapsed = self._clock() - self._pressed_at
            fired = self._fired
            self.cancel()
            if not fired:
                (self._long if elapsed >= HOLD_SECONDS else self._short)()
        elif flag in (2, 3):
            self._hold_expired()
        return 1

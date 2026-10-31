# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""A screen-owned latest-request source reader; widgets stay on the GUI loop."""
import threading

try:
    from enigma import ePythonMessagePump
except ImportError:
    ePythonMessagePump = None


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
        self.waiting = False
        self._wake_pending = False
        self._pump_failed = False
        self._pump = self._pump_connection = self._pump_callbacks = None
        connect(timer, self.poll)
        if ePythonMessagePump is not None:
            try:
                pump = ePythonMessagePump()
                signal = pump.recv_msg
                if callable(getattr(signal, "connect", None)):
                    self._pump_connection = signal.connect(self._message)
                else:
                    self._pump_callbacks = signal.get()
                    self._pump_callbacks.append(self._message)
                self._pump = pump
            except (AttributeError, RuntimeError, TypeError, OSError):
                self._disconnect_pump()

    def _disconnect_pump(self):
        connection, self._pump_connection = self._pump_connection, None
        callbacks, self._pump_callbacks = self._pump_callbacks, None
        if connection is not None:
            disconnect = getattr(connection, "disconnect", None)
            if callable(disconnect):
                try:
                    disconnect()
                except (AttributeError, RuntimeError, TypeError, OSError):
                    pass
        if callbacks is not None:
            try:
                callbacks.remove(self._message)
            except ValueError:
                pass
        pump, self._pump = self._pump, None
        if pump is not None:
            stop = getattr(pump, "stop", None)
            if callable(stop):
                try:
                    stop()
                except (AttributeError, RuntimeError, TypeError, OSError):
                    pass

    def _message(self, *unused):
        # Enigma2 dispatches this on eApp's main loop. Workers send only one
        # coalesced integer wakeup; results stay in the existing single slot.
        with self.condition:
            self._wake_pending = False
        self.poll()

    def _notify_result(self):
        # Called with the condition held. close() cannot release a native
        # pump while a worker is sending; one outstanding wake cannot fill it.
        if self._pump is not None and not self._pump_failed and not self._wake_pending:
            self._wake_pending = True
            try:
                self._pump.send(0)
            except (AttributeError, RuntimeError, TypeError, OSError):
                self._wake_pending = False
                self._pump_failed = True

    def expect(self):
        """Arm GUI delivery for work owned by an existing external worker."""
        with self.condition:
            if self.closed:
                return None
            self.generation += 1
            self.pending = self.result = None
            self.waiting = True
            generation = self.generation
        self.timer.start(250 if self._pump is not None and not self._pump_failed else 40, True)
        return generation

    def publish(self, generation, value, error=None):
        """Publish from a worker without touching timers or GUI components."""
        with self.condition:
            if self.closed or generation != self.generation or not self.waiting:
                return False
            self.result = (generation, value, error)
            self._notify_result()
            return True

    def request(self, load):
        with self.condition:
            if self.closed:
                return
            self.generation += 1
            self.pending = (self.generation, load)
            self.result = None
            self.waiting = True
            if self.thread is None:
                self.thread = threading.Thread(target=self._run, name="GT-SourceReader", daemon=True)
                try:
                    self.thread.start()
                except (OSError, RuntimeError) as error:
                    self.thread = None
                    self.pending = None
                    self.result = (self.generation, None, error)
            self.condition.notify_all()
        # The pump delivers completed work immediately. A slow watchdog
        # covers incomplete vendor bindings; older images retain 40 ms polls.
        self.timer.start(250 if self._pump is not None and not self._pump_failed else 40, True)

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
                    self._notify_result()
            # An idle reader must not retain a second copy of a large result
            # or the previous request's controller through its local closure.
            load = value = error = None

    def poll(self):
        self.timer.stop()
        with self.condition:
            result = self.result
            self.result = None
            if self.closed:
                return
            if result is not None and result[0] == self.generation:
                self.waiting = False
                if self._wake_pending:
                    # A watchdog result whose native wake was never dispatched
                    # switches this binding to the legacy delivery path.
                    self._pump_failed = True
            waiting = self.waiting
        if result is not None and result[0] == self.generation:
            self.apply(result[1], result[2])
        elif waiting:
            self.timer.start(250 if self._pump is not None and not self._pump_failed else 40, True)

    def cancel(self):
        """Discard pending/in-flight results while retaining the idle worker."""
        self.timer.stop()
        with self.condition:
            self.generation += 1
            self.pending = self.result = None
            self.waiting = False

    def close(self):
        self.timer.stop()
        with self.condition:
            self.closed = True
            self.generation += 1
            self.pending = self.result = None
            self.apply = None
            self.waiting = False
            self._disconnect_pump()
            self.condition.notify_all()

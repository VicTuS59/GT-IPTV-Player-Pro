# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""One process-owned writer; final saves outlive the player that queued them."""
import atexit
from collections import OrderedDict
from copy import deepcopy
from contextlib import contextmanager
from functools import wraps
import errno
import os
import threading
import time
import weakref

from .diagnostics import log_event


class PersistenceBusy(RuntimeError):
    pass


class PersistenceCoordinator:
    """Reject new mutations while a worker drains and restores stored data.

    Admission never waits on the GUI. Already admitted operations, including
    nested calls and queued writer jobs, finish before the restore body runs.
    The restore worker alone waits, with a bounded deadline.
    """

    def __init__(self):
        self._condition = threading.Condition()
        self._local = threading.local()
        self._active = 0
        self._frozen = False
        self._writers = weakref.WeakSet()
        self._epoch = 0

    def register(self, writer):
        with self._condition:
            self._writers.add(writer)
            self._epoch += 1

    @contextmanager
    def mutation(self, admitted=False):
        depth = getattr(self._local, "depth", 0)
        with self._condition:
            accepted = bool(depth or admitted or not self._frozen)
            if accepted:
                if not depth:
                    self._active += 1
                    self._epoch += 1
                self._local.depth = depth + 1
        try:
            yield accepted
        finally:
            if accepted:
                with self._condition:
                    self._local.depth = depth
                    if not depth:
                        self._active -= 1
                        self._epoch += 1
                        self._condition.notify_all()

    @contextmanager
    def freeze(self, timeout=10.0):
        if getattr(self._local, "depth", 0):
            raise PersistenceBusy("restore requested inside a mutation")
        deadline = time.monotonic() + max(0, float(timeout))
        with self._condition:
            if self._frozen:
                raise PersistenceBusy("another restore is active")
            self._frozen = True
        try:
            while True:
                with self._condition:
                    while self._active:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            raise PersistenceBusy("persistence drain timed out")
                        self._condition.wait(remaining)
                    writers = tuple(self._writers)
                for writer in writers:
                    if not writer.flush(max(0, deadline - time.monotonic())):
                        raise PersistenceBusy("persistence drain timed out")
                with self._condition:
                    epoch = self._epoch
                    writers = tuple(self._writers)
                idle = all(writer.idle() for writer in writers)
                with self._condition:
                    # Do not hold this condition while acquiring writer locks:
                    # producers may already own a writer's reentrant condition.
                    # A stable epoch proves none started/finished or registered
                    # between the independent idle checks and this decision.
                    if idle and not self._active and self._epoch == epoch:
                        break
                if time.monotonic() >= deadline:
                    raise PersistenceBusy("persistence drain timed out")
            yield
        finally:
            with self._condition:
                self._frozen = False
                self._condition.notify_all()


RESTORE_COORDINATOR = PersistenceCoordinator()


def guarded_mutation(function=None, failure=False, busy_error=False):
    """Admit a synchronous read-modify-write without blocking during restore."""
    def decorate(operation):
        @wraps(operation)
        def guarded(*args, **kwargs):
            with RESTORE_COORDINATOR.mutation() as accepted:
                if not accepted:
                    if busy_error:
                        raise OSError(errno.EBUSY, "Persistence is being restored")
                    return failure
                return operation(*args, **kwargs)
        return guarded
    return decorate(function) if function is not None else decorate


class PersistenceWriter:
    def __init__(self, max_pending=250, subtitle_limit=32):
        self._condition = threading.Condition()
        self._pending = OrderedDict()
        self._latest = {}
        self.max_pending = max(1, int(max_pending))
        self.subtitle_limit = max(1, int(subtitle_limit))
        self._active = 0
        self._thread = None
        self._subtitle_thread = None
        RESTORE_COORDINATOR.register(self)

    def submit(self, key, work, cleanup=None, value=None, essential=False):
        with RESTORE_COORDINATOR.mutation() as accepted:
            if not accepted:
                if cleanup is not None:
                    cleanup()
                return False
            return self._submit(key, work, cleanup, value, essential)

    def _submit(self, key, work, cleanup=None, value=None, essential=False):
        ticket = object()
        discarded = []
        with self._condition:
            previous = self._pending.pop(key, None)
            subtitle = isinstance(key, tuple) and key[0] == "subtitle"
            lane = lambda other: (isinstance(other, tuple) and other[0] == "subtitle") == subtitle
            limit = self.subtitle_limit if subtitle else self.max_pending
            if previous is None and sum(1 for other in self._pending if lane(other)) >= limit:
                # Completed-record retention is bounded too. Prefer the newest
                # positions; an accepted deletion is never an eviction target.
                victim = next((other for other, entry in self._pending.items()
                               if lane(other) and not entry[3]), None)
                if victim is None:
                    if cleanup is not None:
                        cleanup()
                    return False
                discarded.append(self._pending.pop(victim))
                self._latest.pop(victim, None)
            self._pending[key] = (ticket, work, cleanup, essential)
            self._latest[key] = (ticket, value)
            attribute = "_subtitle_thread" if subtitle else "_thread"
            if getattr(self, attribute) is None:
                thread = threading.Thread(target=self._run, args=(subtitle,), name="GT-Persistence-Sub" if subtitle else "GT-Persistence", daemon=True)
                setattr(self, attribute, thread)
                try:
                    thread.start()
                except (RuntimeError, OSError):
                    setattr(self, attribute, None)
                    self._pending.pop(key, None)
                    self._latest.pop(key, None)
                    if cleanup is not None:
                        cleanup()
                    raise
            self._condition.notify_all()
        if previous is not None and previous[2] is not None:
            previous[2]()
        for entry in discarded:
            if entry[2] is not None:
                entry[2]()
        return True

    def peek(self, key):
        with self._condition:
            entry = self._latest.get(key)
            return (True, deepcopy(entry[1])) if entry is not None else (False, None)

    def snapshot(self, prefix):
        with self._condition:
            return {key[-1]: deepcopy(value[1]) for key, value in self._latest.items()
                    if isinstance(key, tuple) and key[:-1] == prefix}

    def _run(self, subtitle=False):
        failures = 0
        while True:
            with self._condition:
                def next_key():
                    return next((key for key in self._pending
                                 if (isinstance(key, tuple) and key[0] == "subtitle") == subtitle), None)
                key = next_key()
                while key is None:
                    self._condition.wait()
                    key = next_key()
                ticket, work, cleanup, unused_essential = self._pending.pop(key)
                self._active += 1
            failed = False
            try:
                with RESTORE_COORDINATOR.mutation(admitted=True):
                    succeeded = work()
                if succeeded is False:
                    failed = True
                    log_event("persistence", "save-failed")
            except Exception as error:
                failed = True
                log_event("persistence", "save-failed", error)
            finally:
                if cleanup is not None:
                    try:
                        cleanup()
                    except OSError:
                        pass
                with self._condition:
                    if self._latest.get(key, (None,))[0] is ticket:
                        self._latest.pop(key, None)
                    self._active -= 1
                    self._condition.notify_all()
            failures = min(5, failures + 1) if failed else 0
            if failed:
                threading.Event().wait(min(1.0, 0.025 * 2 ** failures))

    def flush(self, timeout=2):
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._pending or self._active:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
        return True

    def idle(self):
        with self._condition:
            return not self._pending and not self._active


WRITER = PersistenceWriter()
atexit.register(WRITER.flush)


def resume_write_key(store, key):
    return ("resume", os.path.abspath(str(getattr(store, "path", id(store)))), str(key))


def _commit_resume(work):
    # Brief transient filesystem failures may recover. Keep retries bounded
    # and on the worker; retained subtitle descriptors have a separate lane.
    for delay in (0.05, 0.15, None):
        if work() is not False:
            return True
        if delay is not None:
            threading.Event().wait(delay)
    return False


def queue_playback_save(store, key, position, length, item=None, account_scope=""):
    # Content objects contain plain provider data. Capture it before a zap can
    # mutate the player's selected item, and never retain a Screen in this job.
    if position < 0 or length <= 0:
        return False
    item = deepcopy(item)
    value = {"position": position, "length": length, "updated_at": int(time.time())}
    if item is not None:
        from .playback import resume_item_metadata
        metadata = resume_item_metadata(item)
        if metadata:
            value["item"] = metadata
    if account_scope:
        value["account_scope"] = str(account_scope)
    def save():
        if item is None:
            return _commit_resume(lambda: store.save(key, position, length))
        return _commit_resume(lambda: store.save(key, position, length, item=item, account_scope=account_scope))
    try:
        return WRITER.submit(resume_write_key(store, key), save,
                             value=value)
    except (RuntimeError, OSError) as error:
        log_event("persistence", "queue-failed", error)
        return False


def queue_playback_clear(store, key):
    return WRITER.submit(resume_write_key(store, key), lambda: _commit_resume(lambda: store.clear(key)),
                         value={"position": 0, "length": 0}, essential=True)

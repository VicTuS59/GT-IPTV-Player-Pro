# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""One worker-owned artwork budget, including temporary files and active readers."""
from collections import OrderedDict
import errno
import os
import stat
import threading
import time
import uuid


class ArtworkCacheBusy(OSError):
    retryable = True

    def __init__(self):
        OSError.__init__(self, errno.ENOSPC, "artwork cache readers are busy")


class ArtworkCacheBudget(object):
    def __init__(self, directory, maximum=32 * 1024 * 1024, max_files=300,
                 max_age=14 * 86400, scan_interval=300):
        self.directory = os.path.abspath(directory)
        self.maximum, self.max_files = int(maximum), int(max_files)
        self.max_age, self.scan_interval = float(max_age), float(scan_interval)
        self.lock = threading.RLock()
        self.pin_lock = threading.Lock()
        self.pins = {}
        self.evicting = set()
        self.entries = OrderedDict()
        self.total = 0
        self.scanned_at = None
        self.directory_identity = None
        self.needs_scan = False

    def _directory(self, create=False):
        if create:
            os.makedirs(self.directory, mode=0o700, exist_ok=True)
        info = os.lstat(self.directory)
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_mode & 0o022):
            raise OSError("unsafe artwork cache directory")
        identity = (info.st_dev, info.st_ino)
        if identity != self.directory_identity:
            self.entries.clear()
            self.total = 0
            self.scanned_at = None
            self.directory_identity = identity

    def pin(self, path):
        """Lease without disk I/O; retry if an eviction has already won."""
        path = os.path.abspath(path)
        if os.path.dirname(path) != self.directory:
            return None
        ticket = object()
        with self.pin_lock:
            if path in self.evicting:
                raise ArtworkCacheBusy()
            self.pins.setdefault(path, set()).add(ticket)
        def release():
            with self.pin_lock:
                tickets = self.pins.get(path)
                if tickets is not None:
                    tickets.discard(ticket)
                    if not tickets:
                        self.pins.pop(path, None)
        return release

    def _pinned(self):
        with self.pin_lock:
            return frozenset(self.pins)

    def _claim_eviction(self, path):
        # Pin acquisition and eviction have one atomic memory-only decision.
        # Never hold this lock over unlink: GUI readers must not wait for I/O.
        with self.pin_lock:
            if self.pins.get(path) or path in self.evicting:
                return False
            self.evicting.add(path)
            return True

    def _unlink_unpinned(self, path):
        if not self._claim_eviction(path):
            return False
        try:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass
            return True
        finally:
            with self.pin_lock:
                self.evicting.discard(path)

    def touch(self, path):
        # Selections may run on the GUI. A writer owns disk bookkeeping; a
        # missed LRU hint is harmless and must never stall that selection.
        if not self.lock.acquire(False):
            self.needs_scan = True
            return
        try:
            if path in self.entries:
                self.entries.move_to_end(path)
            else:
                self.needs_scan = True
        finally:
            self.lock.release()

    def _scan(self, now):
        entries = []
        for name in os.listdir(self.directory):
            path = os.path.join(self.directory, name)
            try:
                info = os.lstat(path)
                safe = (stat.S_ISREG(info.st_mode) and info.st_uid == os.geteuid()
                        and info.st_nlink == 1)
                age = max(0.0, now - info.st_mtime)
                stale = (info.st_size <= 0 or age >= self.max_age
                         or name.endswith(".tmp") and age >= 3600)
                if not safe or stale:
                    if self._unlink_unpinned(path):
                        continue
                    if not safe:
                        continue
                entries.append((info.st_mtime, path, info.st_size))
            except OSError:
                continue
        entries.sort()
        self.entries = OrderedDict((path, size) for unused, path, size in entries)
        self.total = sum(self.entries.values())
        self.scanned_at = now
        self.needs_scan = False

    def _space(self, extra_bytes=0, extra_files=0, protected=()):
        protected = frozenset(protected)
        for path in list(self.entries):
            if self.total + extra_bytes <= self.maximum and len(self.entries) + extra_files <= self.max_files:
                return True
            if path in protected:
                continue
            try:
                if not self._unlink_unpinned(path):
                    continue
            except OSError:
                continue
            self.total -= self.entries.pop(path)
        return (self.total + extra_bytes <= self.maximum
                and len(self.entries) + extra_files <= self.max_files)

    def maintain(self, force=False, now=None):
        if not self.lock.acquire(False):
            return
        try:
            self._directory()
            now = time.time() if now is None else float(now)
            if force or self.needs_scan or self.scanned_at is None or now - self.scanned_at >= self.scan_interval:
                self._scan(now)
            self._space()
        except OSError:
            pass
        finally:
            self.lock.release()

    def store(self, path, body, check=None):
        path = os.path.abspath(path)
        if os.path.dirname(path) != self.directory or not 0 < len(body) <= self.maximum:
            raise ValueError("invalid artwork cache entry")
        deadline = time.monotonic() + 2.0
        while True:
            if check is not None:
                check()
            if not self.lock.acquire(timeout=0.05):
                if time.monotonic() >= deadline:
                    raise ArtworkCacheBusy()
                continue
            temporary = None
            try:
                self._directory(create=True)
                now = time.time()
                if self.needs_scan or self.scanned_at is None or now - self.scanned_at >= self.scan_interval:
                    self._scan(now)
                # Account for the full temporary body while an old destination
                # still exists. Every writer is serialized by this same lock.
                if self._space(len(body), 1):
                    temporary = os.path.join(self.directory, ".{}.tmp".format(uuid.uuid4().hex))
                    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                    with os.fdopen(os.open(temporary, flags, 0o600), "wb", buffering=0) as handle:
                        remaining = memoryview(body)
                        while remaining:
                            if check is not None:
                                check()
                            written = handle.write(remaining[:65536])
                            if not written:
                                raise OSError("artwork cache write failed")
                            remaining = remaining[written:]
                    if check is not None:
                        check()
                    self._directory()
                    os.replace(temporary, path)
                    temporary = None
                    self.total -= self.entries.pop(path, 0)
                    self.entries[path] = len(body)
                    self.total += len(body)
                    return path
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass
                self.lock.release()
            if time.monotonic() >= deadline:
                raise ArtworkCacheBusy()
            threading.Event().wait(0.025)


PICON_CACHE = ArtworkCacheBudget("/tmp/gtiptvplayer-picons")

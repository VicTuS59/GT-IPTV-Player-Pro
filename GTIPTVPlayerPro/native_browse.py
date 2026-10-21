# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Visited native portal pages, kept in a bounded anonymous temporary file.

No provider scan is started here. Requests keep the portal's original paging;
only evicted, already visited rows are read back by a browser worker.
"""
from contextlib import contextmanager
import json
import os
import struct
import tempfile
import threading
import zlib

from .content import ContentItem, ContentError, MovieSearchPage


class NativeBrowseWindow:
    def __init__(self, content_type, max_bytes=32 * 1024 * 1024):
        self.content_type = content_type
        self.max_bytes = max_bytes
        self._file = None
        self._offsets = bytearray()
        self._size = 0
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self._recent = {}
        self.has_more = False

    @property
    def count(self):
        return len(self._offsets) // 8

    @contextmanager
    def _access(self):
        with self._lock:
            if self._closed.is_set():
                raise ContentError(reason=ContentError.CANCELLED)
            try:
                yield
            finally:
                if self._closed.is_set() and self._file is not None:
                    self._file.close()
                    self._file = None

    @staticmethod
    def _field(value, depth=0):
        if isinstance(value, (str, int, float, bool, type(None))):
            return value
        if depth < 2 and isinstance(value, (tuple, list)) and len(value) <= 128:
            return [NativeBrowseWindow._field(entry, depth + 1) for entry in value]
        return None

    def append(self, entries, has_more=False, token=None):
        accepted = []
        with self._access():
            if self._file is None:
                # Unlinked at creation, 0600, never enters flash or backups.
                self._file = tempfile.TemporaryFile(mode="w+b", buffering=0)
            self._file.seek(self._size)
            for item in entries:
                if token is not None:
                    token.check()
                identity = str(item.stream_id)
                if identity in self._recent:
                    continue
                fields = {key: self._field(value) for key, value in vars(item).items()
                          if not key.startswith("_")}
                fields["content_type"] = self.content_type
                body = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(body) > 32768:
                    raise ContentError(reason=ContentError.CATALOG_ITEM_LIMIT)
                packed = zlib.compress(body, 1)
                line = struct.pack(">I", len(packed)) + packed
                if self._size + len(line) > self.max_bytes:
                    raise ContentError(reason=ContentError.CATALOG_ITEM_LIMIT)
                remaining = memoryview(line)
                while remaining:
                    written = self._file.write(remaining)
                    if not written:
                        raise OSError("Could not write native catalogue page")
                    remaining = remaining[written:]
                self._offsets.extend(struct.pack(">Q", self._size))
                self._size += len(line)
                self._recent[identity] = True
                while len(self._recent) > 560:
                    self._recent.pop(next(iter(self._recent)))
                accepted.append(item)
            self.has_more = bool(has_more)
        return accepted

    def read_window(self, category_id="", offset=0, size=240, hidden_category_ids=(), token=None):
        offset, size = max(0, int(offset)), max(1, min(280, int(size)))
        items = []
        with self._access():
            end = min(self.count, offset + size)
            if offset < end:
                self._file.seek(struct.unpack_from(">Q", self._offsets, offset * 8)[0])
                for unused in range(offset, end):
                    if token is not None:
                        token.check()
                    size = struct.unpack(">I", self._file.read(4))[0]
                    if not 0 < size <= 33000:
                        raise ValueError("Invalid native catalogue record")
                    decoder = zlib.decompressobj()
                    body = decoder.decompress(self._file.read(size), 32769)
                    if len(body) > 32768 or not decoder.eof or decoder.unconsumed_tail:
                        raise ValueError("Invalid native catalogue record")
                    fields = json.loads(body)
                    item = ContentItem(fields["content_type"], fields["stream_id"], fields["name"])
                    vars(item).update(fields)
                    items.append(item)
            page = MovieSearchPage(items, page_size=size, total_items=self.count,
                has_more=end < self.count or self.has_more, source="native")
            page.next_cursor = end
            page.catalog_state = {}
            return page

    def close(self):
        # EXIT never waits for a file read/write. The worker closes its own
        # descriptor if it currently owns the lock.
        self._closed.set()
        if self._lock.acquire(False):
            try:
                if self._file is not None:
                    self._file.close()
                    self._file = None
            finally:
                self._lock.release()

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Visited native portal pages, kept as bounded compressed process-memory rows.

No provider scan is started here. Requests keep the portal's original paging;
only evicted, already visited rows are read back by a browser worker.
"""
from contextlib import contextmanager
import json
import sys
import threading
import weakref
import zlib

from .content import ContentItem, ContentError, MovieSearchPage
from .memory_catalog import budget, ENTRY_OVERHEAD
from .movie_catalog import CatalogLimitError


class _Rows:
    def __init__(self):
        self.values = []
        self.recent = {}
        self.weight = 0

    def clear(self):
        self.values.clear()
        self.recent.clear()
        budget.reserve(-self.weight)
        self.weight = 0


class NativeBrowseWindow:
    def __init__(self, content_type, max_bytes=32 * 1024 * 1024):
        self.content_type = content_type
        self.max_bytes = max_bytes
        self._rows = _Rows()
        self._finalizer = weakref.finalize(self, self._rows.clear)
        self._lock = threading.Lock()
        self._closed = threading.Event()
        self.has_more = False

    @property
    def count(self):
        return len(self._rows.values)

    @contextmanager
    def _access(self):
        with self._lock:
            if self._closed.is_set():
                raise ContentError(reason=ContentError.CANCELLED)
            try:
                yield
            finally:
                if self._closed.is_set():
                    self._finalizer()
                    self.has_more = False

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
            try:
                budget.check(force=True)
            except (CatalogLimitError, OSError):
                raise ContentError(reason=ContentError.CATALOG_ITEM_LIMIT)
            for item in entries:
                if self._closed.is_set():
                    raise ContentError(reason=ContentError.CANCELLED)
                if token is not None:
                    token.check()
                identity = str(item.stream_id)
                if identity in self._rows.recent:
                    continue
                fields = {key: self._field(value) for key, value in vars(item).items()
                          if not key.startswith("_")}
                fields["content_type"] = self.content_type
                body = json.dumps(fields, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(body) > 32768:
                    raise ContentError(reason=ContentError.CATALOG_ITEM_LIMIT)
                packed = zlib.compress(body, 1)
                # Include list capacity, recent IDs and Python object overhead
                # in both this window's limit and the shared catalogue budget.
                weight = sys.getsizeof(packed) + sys.getsizeof(identity) + ENTRY_OVERHEAD
                if self._rows.weight + weight > self.max_bytes:
                    raise ContentError(reason=ContentError.CATALOG_ITEM_LIMIT)
                try:
                    budget.reserve(weight)
                except (CatalogLimitError, OSError):
                    raise ContentError(reason=ContentError.CATALOG_ITEM_LIMIT)
                try:
                    self._rows.values.append(packed)
                except BaseException:
                    budget.reserve(-weight)
                    raise
                self._rows.weight += weight
                self._rows.recent[identity] = True
                while len(self._rows.recent) > 560:
                    self._rows.recent.pop(next(iter(self._rows.recent)))
                accepted.append(item)
            self.has_more = bool(has_more)
        return accepted

    def read_window(self, category_id="", offset=0, size=240, hidden_category_ids=(), token=None):
        offset, size = max(0, int(offset)), max(1, min(280, int(size)))
        items = []
        with self._access():
            end = min(self.count, offset + size)
            if offset < end:
                for position in range(offset, end):
                    if self._closed.is_set():
                        raise ContentError(reason=ContentError.CANCELLED)
                    if token is not None:
                        token.check()
                    packed = self._rows.values[position]
                    if not 0 < len(packed) <= 33000:
                        raise ValueError("Invalid native catalogue record")
                    decoder = zlib.decompressobj()
                    body = decoder.decompress(packed, 32769)
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
        # EXIT never waits for a worker. Its current operation releases the
        # rows and shared budget as soon as it observes cancellation.
        self._closed.set()
        if self._lock.acquire(False):
            try:
                self._finalizer()
                self.has_more = False
            finally:
                self._lock.release()

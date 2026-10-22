# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Packed RAM columns: searchable names and block-compressed display records."""
from array import array
from bisect import bisect_right
from collections import OrderedDict
import json
import sys
import zlib

from .movie_catalog import CatalogLimitError


BLOCK_ROWS = 128


class PackedRows:
    def __init__(self, resize):
        self.resize = resize
        self.by_id = {}
        self.names = bytearray()
        self.ends = array("I", [0])
        self.codes = array("I")
        self.groups = [()]
        self.group_codes = {(): 0}
        self.blocks = []
        self.pending = []
        self.cache = OrderedDict()

    def __len__(self):
        return len(self.codes)

    def clear(self):
        self.by_id.clear()
        self.names.clear()
        self.ends = array("I", [0])
        self.codes = array("I")
        self.groups = [()]
        self.group_codes = {(): 0}
        self.blocks.clear()
        self.pending.clear()
        self.cache.clear()

    @staticmethod
    def identity(value):
        # Keep composite and zero-prefixed provider IDs lossless.
        if value.isdigit() and len(value) < 20 and not value.startswith("0"):
            return int(value)
        return value

    def append(self, record):
        key = self.identity(record["i"])
        previous = self.by_id.get(key)
        group = tuple(record.get("a", ()))
        if previous is not None:
            group = tuple(sorted(set(group).union(self.groups[self.codes[previous]])))
        code = self.group_codes.get(group)
        group_cost = (sys.getsizeof(group) + sum(sys.getsizeof(v) for v in group) + 128
                      if code is None else 0)
        if previous is not None:
            self.resize(group_cost)
            if code is None:
                code = len(self.groups)
                self.groups.append(group)
                self.group_codes[group] = code
            self.codes[previous] = code
            return
        if len(self.pending) == BLOCK_ROWS:
            self.flush()
        body = dict(record)
        name = body.pop("u").replace("\x00", " ").encode("utf-8") + b"\0"
        body.pop("a", None)
        raw = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) > 16384:
            error = CatalogLimitError("record-size")
            error.limit_kind = "record-size"
            raise error
        cost = sys.getsizeof(key) + 128 + sys.getsizeof(raw) + len(name) + 24 + group_cost
        self.resize(cost)
        count, name_size, old_groups = len(self), len(self.names), len(self.groups)
        try:
            if code is None:
                code = len(self.groups)
                self.groups.append(group)
                self.group_codes[group] = code
            self.by_id[key] = count
            self.names.extend(name)
            self.ends.append(len(self.names))
            self.codes.append(code)
            self.pending.append(raw)
        except BaseException:
            self.by_id.pop(key, None)
            del self.names[name_size:]
            del self.ends[count + 1:]
            del self.codes[count:]
            del self.pending[count % BLOCK_ROWS:]
            if len(self.groups) > old_groups:
                self.groups.pop()
                self.group_codes.pop(group, None)
            self.resize(-cost)
            raise
        if len(self.pending) == BLOCK_ROWS:
            self.flush()

    def flush(self):
        if not self.pending:
            return
        packed = zlib.compress(b"\n".join(self.pending), 1)
        delta = sys.getsizeof(packed) + 8 - sum(sys.getsizeof(v) + 8 for v in self.pending)
        if delta > 0:
            self.resize(delta)
        try:
            self.blocks.append(packed)
        except BaseException:
            if delta > 0:
                self.resize(-delta)
            raise
        self.pending.clear()
        if delta < 0:
            self.resize(delta)

    def record(self, position):
        block, offset = divmod(position, BLOCK_ROWS)
        if block == len(self.blocks):
            raw = self.pending[offset]
        else:
            cached = self.cache.get(block)
            if cached is None:
                lines = zlib.decompress(self.blocks[block]).split(b"\n")
                cost = sys.getsizeof(lines) + sum(sys.getsizeof(line) for line in lines) + 128
                try:
                    while len(self.cache) >= 2:
                        unused, (old, weight) = self.cache.popitem(last=False)
                        self.resize(-weight)
                    self.resize(cost)
                except (OSError, CatalogLimitError):
                    # A full RAM budget must still allow bounded reads. Do not
                    # retain this block if reserving its small read cache fails.
                    pass
                else:
                    self.cache[block] = (lines, cost)
                cached = (lines, cost)
            else:
                self.cache.move_to_end(block)
            raw = cached[0][offset]
        record = json.loads(raw)
        record["a"] = self.groups[self.codes[position]]
        return record

    def matches(self, terms, permitted, check):
        needle = max(terms, key=len)
        position, iterations = 0, 0
        while True:
            found = self.names.find(needle, position)
            if found < 0:
                break
            row = bisect_right(self.ends, found) - 1
            start, end = self.ends[row], self.ends[row + 1] - 1
            position = end + 1
            if iterations % 1024 == 0:
                check()
            iterations += 1
            if permitted[self.codes[row]] and all(self.names.find(term, start, end) >= 0 for term in terms):
                yield row

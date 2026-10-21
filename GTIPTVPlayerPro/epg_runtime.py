# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compact local XMLTV index and one bounded decoded-channel cache.

Indexes are anonymous files in the already validated snapshot directory. Their
UTF-8 key tables stay on disk too; Python does not retain every programme or
channel name. Queries use the existing list/player workers, never XML/network.
No optional database or image package is required on receiver images.
"""
from collections import OrderedDict
import json
import errno
import os
import struct
import sys
import tempfile
import threading
import uuid
import zlib


DECODED_CHANNEL_BYTES = 8 * 1024 * 1024
DECODED_CHANNEL_ENTRIES = 256
MAX_CHANNEL_BYTES = 16 * 1024 * 1024


class DecodedChannelCache(object):
    def __init__(self, maximum=DECODED_CHANNEL_BYTES, entries=DECODED_CHANNEL_ENTRIES):
        self.maximum, self.max_entries = max(0, int(maximum)), max(1, int(entries))
        self.lock = threading.Lock()
        self.values = OrderedDict()
        self.total = 0

    def get(self, key):
        with self.lock:
            value = self.values.get(key)
            if value is not None:
                self.values.move_to_end(key)
                return value[0]
        return None

    def put(self, key, rows):
        cost = (sys.getsizeof(key) + sum(sys.getsizeof(value) for value in key)
                + sys.getsizeof(rows) + 128)
        for row in rows:
            cost += sys.getsizeof(row) + sum(sys.getsizeof(value) for value in row)
        if cost > self.maximum:
            return
        with self.lock:
            old = self.values.pop(key, None)
            if old is not None:
                self.total -= old[1]
            self.values[key] = (rows, cost)
            self.total += cost
            while self.total > self.maximum or len(self.values) > self.max_entries:
                unused, old = self.values.popitem(last=False)
                self.total -= old[1]

    def drop(self, ticket):
        with self.lock:
            for key in list(self.values):
                if key[0] is ticket:
                    self.total -= self.values.pop(key)[1]


def _anonymous_file(directory_descriptor):
    if directory_descriptor is not None:
        name = ".runtime-{}.tmp".format(uuid.uuid4().hex)
        descriptor = None
        created = False
        try:
            flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(name, flags, 0o600, dir_fd=directory_descriptor)
            created = True
            os.unlink(name, dir_fd=directory_descriptor)
            created = False
            handle = os.fdopen(descriptor, "w+b", buffering=0)
            descriptor = None
            return handle
        except OSError:
            # Read-only receiver caches still support guide display through
            # a compact temporary index. Persistent snapshots stay untouched.
            pass
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if created:
                try:
                    os.unlink(name, dir_fd=directory_descriptor)
                except OSError:
                    pass
    return tempfile.TemporaryFile(mode="w+b", buffering=0)


class EPGChannelIndex(object):
    def __init__(self, payload, normalise, cache, directory_descriptor=None, check=None):
        self.ticket = object()
        self.cache = cache
        self.lock = threading.Lock()
        self.file = _anonymous_file(directory_descriptor)
        self._fallback_allowed = directory_descriptor is not None
        try:
            locations = {}
            for number, (channel_id, rows) in enumerate(payload["channels"]):
                if check is not None and number % 32 == 0:
                    check()
                raw = json.dumps(rows, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(raw) > MAX_CHANNEL_BYTES:
                    raise ValueError("XMLTV channel exceeds runtime limit")
                packed = zlib.compress(raw, 1)
                locations[channel_id] = self.file.tell()
                self._write(struct.pack(">II", len(raw), len(packed)))
                self._write(packed)
            pairs, normalised = [], {}
            ambiguous = frozenset(payload.get("ambiguous", ()))
            for number, (wanted, channel_id) in enumerate(payload["aliases"]):
                if check is not None and number % 128 == 0:
                    check()
                pairs.append((b"E" + wanted.encode("utf-8"), locations[channel_id]))
                key = normalise(wanted)
                if not key or key in ambiguous:
                    continue
                if key not in normalised:
                    normalised[key] = channel_id
                elif normalised[key] != channel_id:
                    normalised[key] = None
            for key, channel_id in normalised.items():
                if channel_id is not None:
                    pairs.append((b"N" + key.encode("utf-8"), locations[channel_id]))
            pairs.sort()
            offsets = bytearray()
            for number, (key, location) in enumerate(pairs):
                if check is not None and number % 128 == 0:
                    check()
                if len(key) > 65535:
                    raise ValueError("XMLTV runtime key exceeds limit")
                offsets.extend(struct.pack(">Q", self.file.tell()))
                self._write(struct.pack(">H", len(key)) + key + struct.pack(">Q", location))
            self.table_offset = self.file.tell()
            self.count = len(pairs)
            self._write(offsets)
            self.size_bytes = self.file.tell()
            if check is not None:
                check()
        except BaseException:
            self.file.close()
            raise

    def close(self):
        handle = getattr(self, "file", None)
        if handle is not None:
            self.file = None
            handle.close()
        self.cache.drop(self.ticket)

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _write(self, body):
        remaining = memoryview(body)
        while remaining:
            try:
                written = self.file.write(remaining)
            except OSError as error:
                if (not self._fallback_allowed
                        or error.errno not in (errno.ENOSPC, errno.EDQUOT, errno.EROFS)):
                    raise
                self._fallback_allowed = False
                old = self.file
                replacement = tempfile.TemporaryFile(mode="w+b", buffering=0)
                try:
                    old.seek(0)
                    while True:
                        chunk = old.read(65536)
                        if not chunk:
                            break
                        view = memoryview(chunk)
                        while view:
                            amount = replacement.write(view)
                            if not amount:
                                raise OSError("XMLTV temporary index write failed")
                            view = view[amount:]
                except BaseException:
                    replacement.close()
                    raise
                self.file = replacement
                old.close()
                continue
            if not written:
                raise OSError("XMLTV runtime index write failed")
            remaining = remaining[written:]

    def _read(self, count):
        value = self.file.read(count)
        if len(value) != count:
            raise ValueError("incomplete XMLTV runtime index")
        return value

    def _find(self, target):
        low, high = 0, self.count
        while low < high:
            middle = (low + high) // 2
            self.file.seek(self.table_offset + middle * 8)
            location = struct.unpack(">Q", self._read(8))[0]
            self.file.seek(location)
            key = self._read(struct.unpack(">H", self._read(2))[0])
            if key == target:
                return struct.unpack(">Q", self._read(8))[0]
            if key < target:
                low = middle + 1
            else:
                high = middle
        return None

    def get(self, wanted, normalised, cache_only=False):
        key = (self.ticket, wanted)
        rows = self.cache.get(key)
        if rows is not None:
            return rows
        if cache_only:
            return ()
        with self.lock:
            location = self._find(b"E" + wanted.encode("utf-8"))
            if location is None and normalised:
                location = self._find(b"N" + normalised.encode("utf-8"))
            if location is None:
                rows = ()
                packed = None
            else:
                self.file.seek(location)
                raw_size, packed_size = struct.unpack(">II", self._read(8))
                if not 0 <= raw_size <= MAX_CHANNEL_BYTES or not 0 < packed_size <= MAX_CHANNEL_BYTES + 65536:
                    raise ValueError("invalid XMLTV runtime block")
                packed = self._read(packed_size)
        if packed is not None:
            decoder = zlib.decompressobj()
            raw = decoder.decompress(packed, raw_size + 1)
            if (len(raw) != raw_size or not decoder.eof or decoder.unconsumed_tail
                    or decoder.unused_data):
                raise ValueError("invalid XMLTV runtime compression")
            values = json.loads(raw)
            if not isinstance(values, list) or len(values) > 2048:
                raise ValueError("invalid XMLTV runtime rows")
            rows = tuple(tuple(row) for row in values)
        self.cache.put(key, rows)
        return rows

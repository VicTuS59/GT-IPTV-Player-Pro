# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded JSONL fallback for receiver images without usable SQLite.

Published generations are immutable pairs of metadata/membership files. The
small manifest is replaced atomically only after a batch is flushed. Browsing
uses byte cursors; search streams records instead of retaining the catalogue.
An interrupted compatibility build is rebuilt on retry, retaining usable old
data until the new generation has useful rows.
"""
import fcntl
import hashlib
from collections import OrderedDict
import json
import os
import stat
import time

from .content import ContentError, MovieSearchPage, _movie_search_text
from .category_preferences import content_item_category_ids
from .diagnostics import log_event
from .movie_catalog import CatalogStorageError, CatalogLimitError, CATALOG_TTL, _check
from .vod_catalog import _category_values


SCOPE_EXACT_IDS = 2048
SCOPE_BLOOM_BYTES = 64 * 1024
SCOPE_FILTER_CACHE_BYTES = 1024 * 1024


class PortableMovieCatalog(object):
    def __init__(self, owner):
        self.owner = owner
        self.prefix = owner.scope + ".portable"
        self._member_signature = None
        self._member_buckets = OrderedDict()
        self._member_weights = {}
        self._scope_totals = OrderedDict()
        self._scope_filters = OrderedDict()

    def _open(self, suffix, mode):
        directory = self.owner.codec._directory_fd(create=True)
        if directory is None:
            raise CatalogStorageError("private cache directory unavailable")
        descriptor = -1
        try:
            flags = os.O_RDONLY if mode == "rb" else os.O_RDWR | os.O_CREAT
            if mode == "ab":
                flags |= os.O_APPEND
            descriptor = os.open(self.prefix + suffix, flags | os.O_NOFOLLOW, 0o600, dir_fd=directory)
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise CatalogStorageError("unsafe cache file")
            if mode == "wb":
                os.fchmod(descriptor, 0o600)
                os.ftruncate(descriptor, 0)
            result = os.fdopen(descriptor, mode)
            descriptor = -1
            return result
        finally:
            os.close(directory)
            if descriptor >= 0:
                os.close(descriptor)

    def probe(self):
        with self._open(".probe", "wb") as handle:
            handle.write(b"ok")
        self.owner._free_space()
        os.unlink(os.path.join(self.owner.directory, self.prefix + ".probe"))
        self._manifest()

    def _manifest(self):
        try:
            with self._open(".json", "rb") as handle:
                payload = handle.read(16385)
            if len(payload) > 16384:
                raise ValueError("oversized manifest")
            value = json.loads(payload.decode("utf-8"))
            if not isinstance(value, dict):
                raise ValueError("invalid manifest")
            for key in ("active", "work", "hold"):
                record = value.get(key)
                if record is not None:
                    if not isinstance(record, dict) or not 0 < int(record["epoch"]) < 2 ** 40:
                        raise ValueError("invalid generation")
                    for name in ("count", "bytes", "mbytes"):
                        if not 0 <= int(record[name]) <= self.owner.max_bytes:
                            raise ValueError("invalid generation size")
            return value
        except FileNotFoundError:
            return {}
        except (ValueError, KeyError, UnicodeError):
            log_event("movie-cache", "Rebuilding invalid portable catalogue manifest")
            return {}

    def _save(self, value):
        # All readers see either the preceding checkpoint or the complete new
        # one. No pointer is published before both files have been flushed.
        with self.owner.lock:
            with self._open(".json.tmp", "wb") as handle:
                handle.write(json.dumps(value, separators=(",", ":")).encode("utf-8"))
            os.replace(os.path.join(self.owner.directory, self.prefix + ".json.tmp"),
                       os.path.join(self.owner.directory, self.prefix + ".json"))

    @staticmethod
    def _visible(meta):
        for key in ("active", "work", "hold"):
            record = meta.get(key)
            if record and (record.get("count") or key == "active"):
                return record
        return None

    def _state(self, meta):
        visible = self._visible(meta)
        return dict(movies=int(visible.get("count", 0)) if visible else 0,
                    complete=bool(meta.get("active") is not None and not meta.get("work") and not meta.get("reason")),
                    preparing=bool(meta.get("preparing")), reason=meta.get("reason", ""),
                    updated=float((meta.get("active") or {}).get("updated", 0)),
                    done=int(meta.get("done", 0)), total=int(meta.get("total", 0)),
                    backend="portable")

    def state(self):
        return self._state(self._manifest())

    def _records(self, generation, members=False, offset=0, token=None):
        if not generation:
            return
        suffix = ".{}.{}".format(int(generation["epoch"]), "members" if members else "body")
        limit = int(generation["mbytes" if members else "bytes"])
        with self._open(suffix, "rb") as handle:
            handle.seek(offset)
            while handle.tell() < limit:
                _check(token)
                start = handle.tell()
                line = handle.readline(min(16384, limit - start))
                if not line or not line.endswith(b"\n"):
                    raise CatalogStorageError("truncated portable cache record")
                try:
                    record = json.loads(line)
                except (ValueError, UnicodeError):
                    raise CatalogStorageError("invalid portable cache record")
                if not isinstance(record, dict) or not self.owner._valid_id(record.get("i", "")):
                    raise CatalogStorageError("invalid portable movie identifier")
                yield start, handle.tell(), record

    @staticmethod
    def _member_bucket(identity):
        return hashlib.sha256(identity.encode("utf-8")).digest()[0] % 128

    def _member_suffix(self, bucket):
        return ".mi-{}-{}-{}".format(*self._member_signature, bucket)

    def _index_members(self, generation, token):
        signature = (int((generation or {}).get("epoch", 0)), int((generation or {}).get("mbytes", 0)))
        if signature == self._member_signature:
            return
        self._member_signature = None
        self._member_buckets.clear()
        self._member_weights.clear()
        self._scope_totals.clear()
        self._scope_filters.clear()
        # This optional accelerator contains membership IDs only. It is
        # disposable; source generations and the manifest remain authoritative.
        prefix = self.prefix + ".mi-"
        for name in os.listdir(self.owner.directory):
            if name.startswith(prefix):
                os.unlink(os.path.join(self.owner.directory, name))
        self._member_signature = signature
        opened = OrderedDict()
        buffers = {}
        buffered = 0
        def flush(bucket):
            data = buffers[bucket]
            if not data:
                return 0
            _check(token)
            handle = opened.get(bucket)
            if handle is None:
                handle = self._open(self._member_suffix(bucket), "ab")
                opened[bucket] = handle
            opened.move_to_end(bucket)
            handle.write(data)
            count = len(data)
            data.clear()
            if len(opened) > 16:
                opened.popitem(last=False)[1].close()
            return count
        try:
            for unused_start, unused_end, record in self._records(generation, members=True, token=token):
                bucket = self._member_bucket(record["i"])
                data = buffers.setdefault(bucket, bytearray())
                line = (json.dumps(record, separators=(",", ":")) + "\n").encode("utf-8")
                data.extend(line)
                buffered += len(line)
                if len(data) >= 8192:
                    buffered -= flush(bucket)
                # A small write buffer avoids reopening one of 128 bucket
                # files for almost every row while retaining only 16 handles.
                if buffered >= 256 * 1024:
                    for pending in sorted(buffers):
                        buffered -= flush(pending)
            for bucket in sorted(buffers):
                flush(bucket)
        except Exception:
            self._member_signature = None
            raise
        finally:
            for handle in opened.values():
                handle.close()

    def _bucket_members(self, bucket, token):
        cached = self._member_buckets.get(bucket)
        if cached is not None:
            self._member_buckets.move_to_end(bucket)
            return cached
        result = {}
        consumed = 0
        try:
            with self._open(self._member_suffix(bucket), "rb") as handle:
                for line in handle:
                    _check(token)
                    consumed += len(line)
                    if consumed > 4 * 1024 * 1024 or len(line) > 16384:
                        raise CatalogLimitError()
                    record = json.loads(line)
                    result.setdefault(record["i"], set()).update(record.get("a", ()))
                    if len(result) > 4096:
                        raise CatalogLimitError()
        except FileNotFoundError:
            pass
        self._member_buckets[bucket] = result
        self._member_weights[bucket] = consumed
        while len(self._member_buckets) > 8 or (len(self._member_buckets) > 1 and sum(self._member_weights.values()) > 2 * 1024 * 1024):
            removed, unused_data = self._member_buckets.popitem(last=False)
            self._member_weights.pop(removed, None)
        return result

    def _members(self, record, token):
        values = self._bucket_members(self._member_bucket(record["i"]), token)
        return values.get(record["i"], set(record.get("a", ())))

    def _scope(self, generation, category_id, hidden, token):
        positive = hasattr(hidden, "allowed")
        allowed, hidden_ids = frozenset(getattr(hidden, "allowed", ())), frozenset(hidden)
        if not (hidden_ids or positive or category_id):
            return lambda record: True, int((generation or {}).get("count", 0))
        self._index_members(generation, token)
        def accepted(ids):
            return (not ids.intersection(hidden_ids) and (not positive or bool(ids.intersection(allowed)))
                    and (not category_id or category_id in ids))
        def visible(record):
            return accepted(self._members(record, token))
        key = (self._member_signature, category_id, hidden_ids, positive, allowed)
        if (category_id and self.owner.content_type == "movie"
                and str(getattr(self.owner.client, "source_type", "")) == "xtream"):
            return self._category_scope(key, accepted, token)
        total = self._scope_totals.get(key)
        if total is None:
            total = 0
            for bucket in range(128):
                total += sum(1 for ids in self._bucket_members(bucket, token).values() if accepted(ids))
            self._scope_totals[key] = total
            while len(self._scope_totals) > 8:
                self._scope_totals.popitem(last=False)
        return visible, total

    @staticmethod
    def _scope_bits(identity):
        digest = hashlib.sha256(identity.encode("utf-8")).digest()
        mask = SCOPE_BLOOM_BYTES * 8 - 1
        return tuple(int.from_bytes(digest[start:start + 4], "big") & mask
                     for start in (0, 4, 8))

    def _category_scope(self, key, accepted, token):
        # Filtering every body row through the eight-bucket membership LRU
        # thrashes it when provider order differs from the hash order. Build
        # one bounded predicate per category instead of reopening a bucket
        # for each rejected movie. Large categories use a negative filter;
        # possible matches still check authoritative memberships.
        cached = self._scope_filters.get(key)
        if cached is None:
            identities, bloom, total = set(), None, 0
            for bucket in range(128):
                _check(token)
                for identity, memberships in self._bucket_members(bucket, token).items():
                    _check(token)
                    if not accepted(memberships):
                        continue
                    total += 1
                    if bloom is None and len(identities) < SCOPE_EXACT_IDS:
                        identities.add(identity)
                        continue
                    if bloom is None:
                        bloom = bytearray(SCOPE_BLOOM_BYTES)
                        for previous in identities:
                            for bit in self._scope_bits(previous):
                                bloom[bit >> 3] |= 1 << (bit & 7)
                        identities = None
                    for bit in self._scope_bits(identity):
                        bloom[bit >> 3] |= 1 << (bit & 7)
            identities = frozenset(identities) if identities is not None else None
            bloom = bytes(bloom) if bloom is not None else None
            weight = (len(bloom) if bloom is not None else
                      256 + sum(96 + len(value.encode("utf-8")) for value in identities))
            cached = (identities, bloom, total, weight)
            self._scope_filters[key] = cached
            while len(self._scope_filters) > 8 or (len(self._scope_filters) > 1
                    and sum(value[3] for value in self._scope_filters.values()) > SCOPE_FILTER_CACHE_BYTES):
                self._scope_filters.popitem(last=False)
        self._scope_filters.move_to_end(key)
        identities, bloom, total, unused_weight = cached
        if identities is not None:
            return lambda record: record["i"] in identities, total
        def visible(record):
            if not all(bloom[bit >> 3] & (1 << (bit & 7))
                       for bit in self._scope_bits(record["i"])):
                return False
            return accepted(self._members(record, token))
        return visible, total

    def _items(self, rows, generation, category_id, hidden, token):
        filtered = bool(hidden or hasattr(hidden, "allowed") or category_id)
        if filtered:
            self._index_members(generation, token)
        memberships = {record["i"]: self._members(record, token) if filtered else set(record.get("a", ()))
                       for record in rows}
        result = []
        for record in rows:
            item = self.owner._hydrate(record)
            if item is not None:
                item.portal_category_ids = tuple(sorted(memberships[record["i"]]))
                if category_id:
                    item.category_id = item.catalog_category_id = category_id
                result.append(item)
        return result

    def search(self, query, page, page_size, hidden, token):
        with self.owner.lock:
            return self._search(query, page, page_size, hidden, token)

    def _search(self, query, page, page_size, hidden, token):
        meta = self._manifest()
        generation = self._visible(meta)
        terms = tuple(_movie_search_text(query)[:240].split())[:30]
        page, page_size = max(1, int(page)), max(1, min(100, int(page_size)))
        visible, _ = self._scope(generation, "", hidden, token)
        rows, total = [], 0
        start = (page - 1) * page_size
        if terms:
            for _, _, record in self._records(generation, token=token):
                if visible(record) and all(term in record.get("u", "") for term in terms):
                    if start <= total < start + page_size:
                        rows.append(record)
                    total += 1
        result = MovieSearchPage(self._items(rows, generation, "", hidden, token), page, page_size,
                                 total, total > page * page_size, source="catalog")
        result.catalog_state = self._state(meta)
        return result

    def browse(self, category_id, after, page_size, hidden, token):
        with self.owner.lock:
            return self._browse(category_id, after, page_size, hidden, token)

    def _browse(self, category_id, after, page_size, hidden, token):
        meta = self._manifest()
        generation = self._visible(meta)
        epoch = int((generation or {}).get("epoch", 0))
        after, page_size = max(0, int(after)), max(1, min(250, int(page_size)))
        offset = (after & ((1 << 40) - 1)) if after >> 40 == epoch else 0
        visible, total = self._scope(generation, str(category_id or ""), hidden, token)
        rows, cursor, more = [], offset, False
        for start, end, record in self._records(generation, offset=offset, token=token):
            if visible(record):
                if len(rows) >= page_size:
                    more = True
                    break
                rows.append(record)
            cursor = end
        result = MovieSearchPage(self._items(rows, generation, category_id, hidden, token),
                                 page_size=page_size, total_items=total, has_more=more, source="catalog")
        result.next_cursor = (epoch << 40) | cursor
        result.catalog_state = self._state(meta)
        return result

    def _pack(self, item, category_id, category_name):
        record = self.owner._record(item)
        record["t"] = str(getattr(item, "tmdb_id", "") or "")[:32]
        record["b"] = str(getattr(item, "imdb_id", "") or "")[:32]
        record["u"] = _movie_search_text(record["n"] + " " + record["y"])
        ids = set(content_item_category_ids(item))
        if category_id:
            record["c"], record["g"] = category_id, category_name
            ids.add(category_id)
        record["a"] = sorted(str(value)[:40] for value in ids)[:128]
        page = getattr(item, "portal_catalog_page", None)
        if isinstance(page, int) and 0 <= page <= 20000:
            record["j"] = page
        return record

    def prepare(self, token, callback, force):
        lock = self._open(".lock", "ab")
        deadline = time.monotonic() + 15
        try:
            while True:
                _check(token)
                try:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        state = dict(self.state(), preparing=False, complete=False, reason="busy")
                        if callable(callback):
                            callback(state)
                        return state
                    event = getattr(token, "cancel_event", None)
                    event.wait(0.05) if event is not None else time.sleep(0.05)
            return self._prepare(token, callback, force)
        finally:
            lock.close()

    def _prepare(self, token, callback, force):
        meta = self._manifest()
        state = self._state(meta)
        if state["complete"] and not force and 0 <= self.owner.clock() - state["updated"] < CATALOG_TTL:
            if callable(callback):
                callback(dict(state, preparing=False))
            return state
        epoch = max([int((meta.get(key) or {}).get("epoch", 0)) for key in ("active", "work", "hold")] + [0]) + 1
        if not meta.get("active") and meta.get("work", {}).get("count"):
            meta["hold"] = meta["work"]
        work = dict(epoch=epoch, count=0, bytes=0, mbytes=0, updated=0)
        meta.update(work=work, preparing=True, reason="", done=0, total=0)
        self._save(meta)
        seen = set()
        processed = [0]
        retained = meta.get("active") or meta.get("hold") or {}
        retained_bytes = int(retained.get("bytes", 0)) + int(retained.get("mbytes", 0))
        last_notice = [0.0]
        with self._open(".{}.body".format(epoch), "wb") as body, self._open(".{}.members".format(epoch), "wb") as members:
            def checkpoint(final=False):
                body.flush()
                members.flush()
                work.update(bytes=body.tell(), mbytes=members.tell(), count=len(seen))
                self._save(meta)
                now = time.monotonic()
                if final or now - last_notice[0] >= 0.4:
                    last_notice[0] = now
                    if callable(callback):
                        callback(self._state(meta))
            def collect(category_id="", category_name=""):
                self.owner._free_space()
                iterator = getattr(self.owner.client, self.owner.stream_method)(category_id)
                try:
                    with self.owner._request(token):
                        for item in iterator:
                            _check(token)
                            record = self._pack(item, category_id, category_name)
                            if not record["i"]:
                                continue
                            movie_id = record["i"]
                            new_movie = movie_id not in seen
                            line = ((json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")) if new_movie else b""
                            membership = (json.dumps({"i": record["i"], "a": record["a"]}, separators=(",", ":")) + "\n").encode("utf-8")
                            if body.tell() + members.tell() + len(line) + len(membership) + retained_bytes > self.owner.max_bytes:
                                raise CatalogLimitError()
                            if new_movie:
                                if len(seen) >= self.owner.max_items:
                                    raise CatalogLimitError()
                                seen.add(movie_id)
                                body.write(line)
                            members.write(membership)
                            processed[0] += 1
                            if processed[0] % 500 == 0:
                                self.owner._free_space()
                                checkpoint()
                finally:
                    closer = getattr(iterator, "close", None)
                    if callable(closer):
                        closer()
                    checkpoint()
            try:
                checkpoint()
                use_categories = str(getattr(self.owner.client, "source_type", "")) == "stalker"
                if not use_categories:
                    try:
                        collect()
                    except ContentError as error:
                        _check(token)
                        if seen or error.reason in (ContentError.ACCESS_DENIED, ContentError.RATE_LIMITED):
                            raise
                        use_categories = True
                if use_categories:
                    with self.owner._request(token):
                        categories = dict(_category_values(value) for value in self.owner.client.load_categories(self.owner.content_type))
                    categories.pop("", None)
                    categories = categories or {"": ""}
                    meta["total"] = len(categories)
                    for category_id, name in categories.items():
                        collect(category_id, name)
                        meta["done"] += 1
                        checkpoint()
                _check(token)
                checkpoint()
                os.fsync(body.fileno())
                os.fsync(members.fileno())
                work["updated"] = self.owner.clock()
                meta.update(active=dict(work), work=None, hold=None, preparing=False, reason="")
                self._save(meta)
            except Exception as error:
                reason = getattr(error, "reason", "") or ("limit" if isinstance(error, CatalogLimitError) else "storage" if isinstance(error, (CatalogStorageError, OSError)) else "incomplete")
                if reason in (ContentError.CATALOG_ITEM_LIMIT, ContentError.RESPONSE_TOO_LARGE):
                    reason = "limit"
                if getattr(getattr(token, "cancel_event", None), "is_set", lambda: False)():
                    reason = "cancelled"
                meta.update(preparing=False, reason=reason)
                log_event("movie-cache", "Portable catalogue stopped: {}".format(reason), error=error)
                checkpoint(final=True)
        state = self._state(meta)
        if callable(callback):
            callback(dict(state, preparing=False))
        with self.owner.lock:
            self._prune(meta)
        return state

    def _prune(self, meta):
        keep = {int(value["epoch"]) for value in (meta.get(key) for key in ("active", "work", "hold")) if value}
        prefix = self.prefix + "."
        for name in os.listdir(self.owner.directory):
            if not name.startswith(prefix):
                continue
            parts = name[len(prefix):].split(".")
            if len(parts) == 2 and parts[0].isdigit() and parts[1] in ("body", "members") and int(parts[0]) not in keep:
                try:
                    os.unlink(os.path.join(self.owner.directory, name))
                except FileNotFoundError:
                    pass

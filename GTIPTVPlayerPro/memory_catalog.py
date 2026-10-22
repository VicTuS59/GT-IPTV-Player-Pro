# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Process-only movie/series metadata; no cache files, SQLite or spill files."""
from collections import OrderedDict
from array import array
import sys
import threading
import time
import weakref

from .category_preferences import content_item_category_ids
from .category_visibility import category_ids_visible
from .content import ContentError, MovieSearchPage, _movie_search_text
from .movie_catalog import CatalogLimitError, CATALOG_TTL, _check
from .vod_catalog import _category_values
from .packed_catalog import PackedRows


RAM_CATALOG_BYTES = 192 * 1024 * 1024
RAM_RESERVE_BYTES = 64 * 1024 * 1024
# Conservative allowance for an OrderedDict node, table capacity and pointers.
ENTRY_OVERHEAD = 256
_registry = weakref.WeakValueDictionary()
_registry_lock = threading.Lock()


def available_memory():
    values = {}
    with open("/proc/meminfo", "r") as source:
        for line in source:
            name, _, value = line.partition(":")
            if name in ("MemAvailable", "MemFree", "Cached", "Buffers", "Shmem"):
                values[name] = int(value.split()[0]) * 1024
    if "MemAvailable" in values:
        return values["MemAvailable"]
    if "MemFree" not in values:
        raise OSError("available memory is unavailable")
    return max(0, values["MemFree"] + values.get("Cached", 0)
               + values.get("Buffers", 0) - values.get("Shmem", 0))


def limit(kind):
    error = CatalogLimitError(kind)
    error.limit_kind = kind
    return error


class _RamBudget:
    def __init__(self):
        self.lock = threading.RLock()
        self.used = 0
        self.maximum = RAM_CATALOG_BYTES
        self.checked = 0

    def check(self, force=False):
        with self.lock:
            now = time.monotonic()
            if force or now - self.checked >= 0.25:
                if available_memory() < RAM_RESERVE_BYTES:
                    raise limit("available-memory")
                self.checked = now

    def reserve(self, amount):
        with self.lock:
            if amount > 0:
                self.check()
                if self.used + amount > self.maximum:
                    raise limit("catalog-memory")
            self.used = max(0, self.used + amount)


budget = _RamBudget()


class _Generation:
    def __init__(self, epoch, resize):
        self.epoch = epoch
        self.weight = 0
        self.updated = 0
        self.rows = PackedRows(lambda delta: resize(self, delta))

    def clear(self):
        budget.reserve(-self.weight)
        self.rows.clear()
        self.weight = 0


class _Session:
    def __init__(self):
        self.lock = threading.RLock()
        self.producer = threading.Lock()
        self.owners = 0
        self.closed = False
        self.active = self.work = self.hold = None
        self.epoch = self.revision = 0
        self.preparing = False
        self.reason = self.limit_kind = ""
        self.done = self.total = 0
        self.totals = OrderedDict()
        self.queries = OrderedDict()
        self.query_weight = 0
        self.preferred_category = ""

    def clear_queries(self):
        budget.reserve(-self.query_weight)
        self.queries.clear()
        self.query_weight = 0

    def visible(self):
        if self.active is not None:
            return self.active
        if self.work is not None and self.work.rows:
            return self.work
        return self.hold or self.work

    def state(self):
        with self.lock:
            visible = self.visible()
            return dict(movies=len(visible.rows) if visible else 0,
                        complete=self.active is not None and self.work is None and not self.reason,
                        preparing=self.preparing, reason=self.reason,
                        updated=self.active.updated if self.active else 0,
                        done=self.done, total=self.total, backend="memory",
                        limit_kind=self.limit_kind, memory_bytes=self.weight(),
                        generation=visible.epoch if visible is not None else 0)

    def weight(self):
        return self.query_weight + sum(gen.weight for gen in (self.active, self.work, self.hold) if gen is not None)

    def release(self):
        with self.lock:
            self.owners -= 1
            if self.owners > 0:
                return
            self.closed = True
            self.preparing = False
            for gen in (self.active, self.work, self.hold):
                if gen is not None:
                    gen.clear()
            self.active = self.work = self.hold = None
            self.totals.clear()
            self.clear_queries()


def acquire_session(key):
    with _registry_lock:
        session = _registry.get(key)
        if session is not None:
            with session.lock:
                if not session.closed:
                    session.owners += 1
                    return session
        session = _Session()
        session.owners = 1
        _registry[key] = session
        return session


class MemoryCatalog:
    def __init__(self, owner):
        self.owner = weakref.proxy(owner)
        self.session = acquire_session((owner.scope, owner.content_type))
        if owner._preference_set:
            self.session.preferred_category = owner.preferred_category

    def check(self, token=None, memory=False):
        _check(token)
        if self.owner._closed or self.session.closed:
            raise ContentError(reason=ContentError.CANCELLED)
        if memory:
            budget.check(force=True)

    def state(self):
        self.check()
        return self.session.state()

    def pack(self, item, category_id, category_name):
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
        if isinstance(page, int) and 0 <= page <= 250000:
            record["j"] = page
        return record

    def resize(self, generation, delta):
        session = self.session
        with budget.lock:
            held = session.hold.weight if session.hold is not None and generation is session.work else 0
            if delta > 0:
                budget.check()
                if (session.weight() - held + delta > self.owner.max_bytes
                        or budget.used - held + delta > budget.maximum):
                    raise limit("catalog-memory")
                if held:
                    session.hold.clear()
                    session.hold = None
            budget.reserve(delta)
            generation.weight += delta

    def append(self, generation, record, token):
        self.check(token)
        identity = record["i"]
        if not self.owner._valid_id(identity):
            return
        session = self.session
        with session.lock:
            self.check(token)
            if (generation.rows.identity(identity) not in generation.rows.by_id
                    and len(generation.rows) >= self.owner.max_items):
                raise limit("item-count")
            generation.rows.append(record)
            session.revision += 1
            session.totals.clear()
            session.clear_queries()

    def prepare(self, token, callback, force):
        noticed = 0.0
        while not self.session.producer.acquire(timeout=0.05):
            self.check(token)
            now = time.monotonic()
            if callable(callback) and now - noticed >= 0.4:
                callback(self.session.state())
                noticed = now
            if self.session.state()["complete"] and not force:
                return self.session.state()
        try:
            self.check(token)
            return self._prepare(token, callback, force)
        finally:
            self.session.producer.release()

    def _prepare(self, token, callback, force):
        session = self.session
        state = session.state()
        if state["complete"] and not force and 0 <= self.owner.clock() - state["updated"] < CATALOG_TTL:
            if callable(callback):
                callback(state)
            return state
        with session.lock:
            self.check(token, memory=True)
            if session.work is not None:
                if session.active is None and session.work.rows:
                    if session.hold is not None:
                        session.hold.clear()
                    session.hold = session.work
                else:
                    session.work.clear()
            if session.active is not None and session.hold is not None:
                session.hold.clear()
                session.hold = None
            session.epoch += 1
            generation = session.work = _Generation(session.epoch, self.resize)
            session.preparing = True
            session.reason = session.limit_kind = ""
            session.done = session.total = 0
            session.totals.clear()
            session.clear_queries()
        last_notice = [0.0]
        processed = [0]

        def notify(final=False):
            self.check(token)
            now = time.monotonic()
            if final or now - last_notice[0] >= 0.4:
                last_notice[0] = now
                if callable(callback):
                    callback(session.state())

        def collect(category_id="", category_name=""):
            self.check(token, memory=True)
            iterator = getattr(self.owner.client, self.owner.stream_method)(category_id)
            try:
                with self.owner._request(token):
                    for item in iterator:
                        self.append(generation, self.pack(item, category_id, category_name), token)
                        processed[0] += 1
                        if processed[0] % 250 == 0:
                            self.check(token, memory=True)
                            notify()
            finally:
                closer = getattr(iterator, "close", None)
                if callable(closer):
                    closer()

        def collect_portal_categories(categories):
            # Change category at portal-page boundaries. At most two native
            # page iterators remain live; a displaced older category can be
            # revisited without duplicating its existing compact records.
            pending = dict(categories)
            current = parked = None
            def close(entry):
                closer = getattr(entry[1], "close", None) if entry is not None else None
                if callable(closer):
                    closer()
            def opened(category):
                return category, iter(getattr(self.owner.client, self.owner.stream_method)(category))
            try:
                while pending:
                    self.check(token, memory=True)
                    priority = session.preferred_category
                    if priority in pending and (current is None or current[0] != priority):
                        if parked is not None and parked[0] == priority:
                            current, parked = parked, current
                        else:
                            close(parked)
                            parked, current = current, opened(priority)
                    if current is None:
                        if parked is not None:
                            current, parked = parked, None
                        else:
                            current = opened(next(iter(pending)))
                    category, iterator = current
                    with self.owner._request(token):
                        for unused in range(250):
                            try:
                                item = next(iterator)
                            except StopIteration:
                                close(current)
                                current = None
                                pending.pop(category)
                                with session.lock:
                                    session.done += 1
                                break
                            self.append(generation, self.pack(item, category, categories[category]), token)
                            processed[0] += 1
                    notify()
            finally:
                close(current)
                close(parked)

        try:
            notify()
            browse_category = getattr(self.owner, "browse_category", None)
            use_categories = (browse_category is None and
                              str(getattr(self.owner.client, "source_type", "")) == "stalker")
            if browse_category is not None:
                # Ordinary browsing requests exactly the selected category.
                # Empty/error responses must not trigger an account scan.
                collect(browse_category)
            elif not use_categories:
                try:
                    collect()
                except ContentError as error:
                    self.check(token)
                    if generation.rows or error.reason in (ContentError.ACCESS_DENIED, ContentError.RATE_LIMITED):
                        raise
                    use_categories = True
            if use_categories:
                with self.owner._request(token):
                    categories = dict(_category_values(value) for value in
                                      self.owner.client.load_categories(self.owner.content_type))
                categories.pop("", None)
                categories = categories or {"": ""}
                with session.lock:
                    session.total = len(categories)
                if str(getattr(self.owner.client, "source_type", "")) == "stalker":
                    collect_portal_categories(categories)
                else:
                    for category_id, name in categories.items():
                        collect(category_id, name)
                        with session.lock:
                            session.done += 1
                        notify()
            self.check(token)
            with session.lock:
                self.check(token)
                generation.rows.flush()
                if session.active is not None:
                    session.active.clear()
                if session.hold is not None:
                    session.hold.clear()
                    session.hold = None
                generation.updated = self.owner.clock()
                session.active, session.work = generation, None
                session.preparing = False
                session.totals.clear()
                session.clear_queries()
        except Exception as error:
            with session.lock:
                if not session.closed:
                    session.preparing = False
                    session.reason = (getattr(error, "reason", "") or
                                      ("limit" if isinstance(error, CatalogLimitError) else "incomplete"))
                    if session.reason in (ContentError.CATALOG_ITEM_LIMIT, ContentError.RESPONSE_TOO_LARGE):
                        session.reason = "limit"
                    session.limit_kind = getattr(error, "limit_kind", "")
        state = session.state()
        if callable(callback):
            callback(state)
        return state

    def hydrate(self, record, category_id=""):
        item = self.owner._hydrate(record)
        if item is not None:
            item.portal_category_ids = tuple(record.get("a", ()))
            if category_id:
                item.category_id = item.catalog_category_id = category_id
            remember = getattr(self.owner.client, "remember_catalog_item", None)
            if callable(remember):
                remember(item)
        return item

    def search(self, query, page, page_size, hidden, token):
        self.check(token, memory=True)
        page, page_size = max(1, int(page)), max(1, min(100, int(page_size)))
        terms = tuple(term.encode("utf-8") for term in
                      _movie_search_text(query).replace("\x00", " ")[:240].split())[:30]
        rows, total = [], 0
        start = (page - 1) * page_size
        with self.session.lock:
            self.check(token)
            generation = self.session.visible()
            if generation is not None and terms:
                key = (terms, frozenset(hidden or ()), hasattr(hidden, "allowed"),
                       frozenset(getattr(hidden, "allowed", ())), frozenset(getattr(hidden, "known", ())))
                cached = self.session.queries.get(key)
                if cached is None:
                    permitted = tuple(category_ids_visible(group, hidden) for group in generation.rows.groups)
                    matches = array("I", generation.rows.matches(terms, permitted, lambda: self.check(token)))
                    cost = sys.getsizeof(matches) + 512 + sum(sys.getsizeof(term) for term in terms)
                    while len(self.session.queries) >= 4:
                        unused, (old, weight) = self.session.queries.popitem(last=False)
                        self.session.query_weight -= weight
                        budget.reserve(-weight)
                    try:
                        if self.session.weight() + cost > self.owner.max_bytes:
                            raise limit("catalog-memory")
                        budget.reserve(cost)
                    except (CatalogLimitError, OSError):
                        pass
                    else:
                        self.session.queries[key] = (matches, cost)
                        self.session.query_weight += cost
                else:
                    matches = cached[0]
                    self.session.queries.move_to_end(key)
                total = len(matches)
                for position in matches[start:start + page_size]:
                    self.check(token)
                    item = self.hydrate(generation.rows.record(position))
                    if item is not None:
                        rows.append(item)
            result = MovieSearchPage(rows, page, page_size, total, total > page * page_size, source="catalog")
            result.catalog_state = self.session.state()
            return result

    def browse(self, category_id, after, page_size, hidden, token, include_total=True):
        self.check(token, memory=True)
        category_id = str(category_id or "")
        after, page_size = max(0, int(after)), max(1, min(250, int(page_size)))
        with self.session.lock:
            self.check(token)
            generation = self.session.visible()
            epoch = generation.epoch if generation is not None else 0
            offset = (after & ((1 << 40) - 1)) if after >> 40 == epoch else 0
            rows, cursor, more = [], offset, False
            filtered = bool(category_id or hidden or hasattr(hidden, "allowed"))
            total = 0
            if generation is not None:
                permitted = tuple((not category_id or category_id in ids)
                    and category_ids_visible(ids, hidden, category_id) for ids in generation.rows.groups)
                for number in range(offset, len(generation.rows)):
                    if number % 1024 == 0:
                        self.check(token)
                    if permitted[generation.rows.codes[number]]:
                        if len(rows) >= page_size:
                            more = True
                            break
                        item = self.hydrate(generation.rows.record(number), category_id)
                        if item is not None:
                            rows.append(item)
                    cursor = number + 1
                if not filtered:
                    total = len(generation.rows)
                elif include_total:
                    key = (generation.epoch, self.session.revision, category_id, frozenset(hidden or ()),
                           hasattr(hidden, "allowed"), frozenset(getattr(hidden, "allowed", ())),
                           frozenset(getattr(hidden, "known", ())))
                    cached = self.session.totals.get(key)
                    if cached is None:
                        for number, code in enumerate(generation.rows.codes):
                            if number % 1024 == 0:
                                self.check(token)
                            total += bool(permitted[code])
                        self.session.totals[key] = total
                        while len(self.session.totals) > 8:
                            self.session.totals.popitem(last=False)
                    else:
                        total = cached
            result = MovieSearchPage(rows, page_size=page_size, total_items=total,
                                     has_more=more, source="catalog")
            result.next_cursor = (epoch << 40) | cursor
            result.catalog_state = self.session.state()
            return result

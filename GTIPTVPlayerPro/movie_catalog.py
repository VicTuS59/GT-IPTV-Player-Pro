# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Movie/series catalogues held only in process memory until readers close."""
from contextlib import contextmanager
import threading
import time
import weakref

from .content import ContentError, ContentItem
from .portal_identity import safe_series_id
from .vod_catalog import VodCatalogCache, vod_catalog_account_scope


CATALOG_TTL = 24 * 60 * 60
CATALOG_MAX_ITEMS = 250000
CATALOG_MAX_BYTES = 128 * 1024 * 1024
CATALOG_FREE_BYTES = 64 * 1024 * 1024
_locks_guard = threading.Lock()
_locks = weakref.WeakValueDictionary()


def _check(token):
    if token is not None:
        token.check()


class CatalogStorageError(Exception):
    pass


class CatalogLimitError(Exception):
    pass


class MovieCatalogIndex:
    content_type = "movie"
    stream_method = "iter_movie_catalog_items"
    storage_namespace = ""

    def __init__(self, client, directory=None, clock=None, max_items=CATALOG_MAX_ITEMS,
                 max_bytes=CATALOG_MAX_BYTES, free_bytes=CATALOG_FREE_BYTES,
                 allow_fallback=None):
        self.client = client
        self.clock = clock or time.time
        self.scope = vod_catalog_account_scope(client)
        # Keep the existing metadata validation/playback codec; none of its
        # filesystem cache load/store methods are used by this facade.
        self.codec = VodCatalogCache(client)
        self.max_items = max(1, min(CATALOG_MAX_ITEMS, int(max_items)))
        self.max_bytes = max(65536, min(CATALOG_MAX_BYTES, int(max_bytes)))
        self._memory = self._finalizer = None
        self._closed = False
        self._migrate_legacy = directory is None
        self._preferred_category = ""
        self._preference_set = False
        with _locks_guard:
            key = (self.scope, self.content_type)
            self.lock = _locks.setdefault(key, threading.RLock())

    @staticmethod
    def _valid_id(value):
        return str(value or "").isdigit()

    @property
    def preferred_category(self):
        return self._preferred_category

    @preferred_category.setter
    def preferred_category(self, value):
        self._preferred_category = str(value or "")
        self._preference_set = True
        if self._memory is not None:
            self._memory.session.preferred_category = self._preferred_category

    def _record(self, item):
        return self.codec._record(item)

    @property
    def backend_name(self):
        return "memory" if self._memory else ""

    def _select_backend(self, token=None):
        _check(token)
        with self.lock:
            if self._closed:
                raise ContentError(reason=ContentError.CANCELLED)
            if not self.scope:
                raise CatalogStorageError("account identity unavailable")
            if self._memory is None:
                from .memory_catalog import MemoryCatalog
                self._memory = MemoryCatalog(self)
                self._finalizer = weakref.finalize(self, self._memory.session.release)
            return self._memory

    def close(self):
        """Release this reader immediately, including while a worker cancels."""
        with self.lock:
            if self._closed:
                return
            self._closed = True
            if self._finalizer is not None:
                self._finalizer()
            self._memory = None

    def state(self):
        return self._select_backend().state()

    def search(self, query, page=1, page_size=14, hidden_category_ids=(), token=None):
        return self._select_backend(token).search(query, page, page_size, hidden_category_ids, token)

    def browse(self, category_id="", after=0, page_size=80, hidden_category_ids=(), token=None, include_total=True):
        return self._select_backend(token).browse(category_id, after, page_size, hidden_category_ids, token, include_total)

    def prepare(self, token=None, callback=None, force=False):
        backend = None
        try:
            _check(token)
            if self._migrate_legacy:
                from .catalog_cleanup import clean_previous_catalogues
                clean_previous_catalogues(self.codec)
            backend = self._select_backend(token)
            return backend.prepare(token, callback, force)
        except (CatalogStorageError, CatalogLimitError, OSError) as error:
            state = backend.session.state() if backend is not None else dict(movies=0, done=0, total=0)
            state.update(complete=False, preparing=False, backend="memory",
                         reason="limit" if isinstance(error, CatalogLimitError) else "storage",
                         limit_kind=getattr(error, "limit_kind", ""))
            if callable(callback):
                callback(state)
            return state

    def _hydrate(self, record):
        item = self.codec._item(record)
        if item is None and record.get("i") and str(getattr(self.client, "source_type", "")) == "stalker":
            # Recover non-persistable portal commands through authenticated
            # create_link/category lookup at playback, never store signed URLs.
            item = ContentItem("movie", record["i"], record["n"],
                               category_id=record.get("c", ""), extension=record.get("x", "mp4"),
                               year=record.get("y", ""), rating=record.get("r", ""), icon=record.get("p", ""))
            item.catalog_category_id = record.get("c", "")
            item.catalog_category_name = record.get("g", "")
            item.portal_command = ""
            item.portal_command_is_fallback = True
            item.portal_request_type = record.get("q", "vod")
            item.portal_series = "0"
        if item is not None:
            item.content_type = self.content_type
            item.tmdb_id, item.imdb_id = record.get("t", ""), record.get("b", "")
            if "j" in record:
                item.portal_catalog_page = int(record["j"])
        return item

    @contextmanager
    def _request(self, token):
        scope = getattr(self.client, "request_scope", None)
        _check(token)
        if callable(scope):
            with scope(cancel_event=getattr(token, "cancel_event", None),
                       deadline=getattr(token, "deadline", None)):
                yield
        else:
            yield
        _check(token)


class SeriesCatalogIndex(MovieCatalogIndex):
    """Reuse the bounded engine without mixing series and movie identities."""
    content_type = "series"
    stream_method = "iter_series_catalog_items"
    storage_namespace = "series"

    def _valid_id(self, value):
        if str(getattr(self.client, "source_type", "")) == "stalker":
            return bool(safe_series_id(value))
        return super()._valid_id(value)

    def _record(self, item):
        record = super()._record(item)
        if str(getattr(self.client, "source_type", "")) == "stalker":
            record["i"] = safe_series_id(getattr(item, "stream_id", ""))
            request_type = getattr(item, "portal_request_type", "")
            record["q"] = request_type if request_type in ("series", "vod") else "series"
            parameter = getattr(item, "portal_catalog_category_parameter", "")
            record["h"] = parameter if parameter in ("category", "genre") else "category"
            flag = getattr(item, "portal_catalog_series_flag", "")
            record["f"] = flag if flag in ("series", "is_series") else ""
            # Series headers are API parents, never reusable playback links.
            record.pop("m", None)
        return record

    def _hydrate(self, record):
        if str(getattr(self.client, "source_type", "")) != "stalker":
            return super()._hydrate(record)
        identity = safe_series_id(record.get("i", ""))
        if not identity:
            return None
        item = ContentItem("series", identity, record.get("n", ""),
            category_id=record.get("c", ""), extension=record.get("x", "mp4"),
            icon=record.get("p", ""), year=record.get("y", ""), rating=record.get("r", ""),
            tmdb_id=record.get("t", ""), imdb_id=record.get("b", ""))
        item.catalog_category_id = record.get("c", "")
        item.catalog_category_name = record.get("g", "")
        item.portal_command = ""
        item.portal_series = "0"
        item.portal_request_type = record.get("q") if record.get("q") in ("series", "vod") else "series"
        item.portal_catalog_category_parameter = record.get("h") if record.get("h") in ("category", "genre") else "category"
        item.portal_catalog_series_flag = record.get("f") if record.get("f") in ("series", "is_series") else ""
        if isinstance(record.get("j"), int) and 0 <= record["j"] <= 250000:
            item.portal_catalog_page = record["j"]
        return item


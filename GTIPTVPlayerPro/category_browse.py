# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""On-demand category browsing, independent of account-wide search.

Only the selected category owns a producer. A small screen-local LRU retains
completed categories, independently of account-wide search and disk caches.
"""
import hashlib
import weakref
from collections import OrderedDict

from .content import ContentError
from .movie_browse import MovieBrowseCatalog
from .movie_catalog import MovieCatalogIndex, SeriesCatalogIndex, RAM_UNAVAILABLE, CATALOG_TTL


class CategoryBrowseCatalog(MovieBrowseCatalog):
    load_timeout = 30 * 60
    cache_max_bytes = 32 * 1024 * 1024
    cache_max_categories = 3

    def __init__(self, client, content_type="movie", native=False):
        self.client = client
        self.native = bool(native)
        self.content_type = content_type
        self._index_class = SeriesCatalogIndex if content_type == "series" else MovieCatalogIndex
        self._category_id = None
        self.sort_mode = 'provider'
        self._sort_view = None
        self._full_list = None
        self._cached_indexes = OrderedDict()
        super().__init__(client, index=self._empty_index())

    def _empty_index(self):
        index = self._index_class(self.client)
        index.category_list = True
        index.native_category = self.native
        if index.scope:
            index.scope += ':category-list-native' if self.native else ':category-list'
        return index

    def _new_index(self, category_id):
        index = self._empty_index()
        index.browse_category = category_id
        batch_method = ('iter_series_category_batches' if self.content_type == 'series'
                        else 'iter_movie_category_batches')
        index.category_load_mode = ('pending' if callable(getattr(self.client, batch_method, None))
                                    else 'progressive')
        index._category_cache_reclaimer = weakref.WeakMethod(self._reclaim_cached)
        # A category must never share its rows/producer with global search,
        # another category, or the other content type for the same account.
        if index.scope:
            digest = hashlib.sha256(category_id.encode("utf-8")).hexdigest()
            index.scope += ":browse:" + digest
        return index

    @staticmethod
    def _fresh_index(index):
        backend = getattr(index, '_memory', None)
        if backend is None or index._closed: return None
        state = backend.session.state()
        if (state.get('complete') and not state.get('preparing')
                and 0 <= index.clock()-state.get('updated', 0) < CATALOG_TTL):
            return state
        return None

    def _drop_cached(self):
        for index in self._cached_indexes.values(): index.close()
        self._cached_indexes.clear()

    def _reclaim_cached(self):
        # A producer must never wait for the GUI's category-switch lock.
        if not self.condition.acquire(False): return False
        try:
            present = bool(self._cached_indexes)
            self._drop_cached()
            return present
        finally:
            self.condition.release()

    def _park_index(self, category, index):
        from .memory_catalog import available_memory
        state = self._fresh_index(index)
        try: maximum = max(0, min(self.cache_max_bytes, available_memory()//3))
        except (OSError, ValueError): maximum = 0
        if category is None or state is None or state['memory_bytes'] > maximum:
            index.close()
            return
        previous = self._cached_indexes.pop(category, None)
        if previous is not None: previous.close()
        self._cached_indexes[category] = index
        total = sum(value._memory.session.weight() for value in self._cached_indexes.values())
        while self._cached_indexes and (len(self._cached_indexes) > self.cache_max_categories or total > maximum):
            unused, oldest = self._cached_indexes.popitem(last=False)
            total -= oldest._memory.session.weight()
            oldest.close()

    def cached_ready(self, category):
        """Read-only GUI hint: a cache hit needs no navigation debounce."""
        if not self.condition.acquire(False): return False
        try:
            index = self._cached_indexes.get(str(category or ''))
            return index is not None and self._fresh_index(index) is not None
        finally:
            self.condition.release()

    def start(self, force=False, preferred_category=None):
        with self.condition:
            if self._closed:
                return
            category_id = (self._category_id if preferred_category is None
                           else str(preferred_category or ""))
            # Showing/resuming the screen without a selection cannot start
            # an implicit whole-account request.
            if category_id is None:
                return
            if force or category_id != self._category_id:
                self._clear_sort_view()
                previous, previous_category = self.index, self._category_id
                self.stop()
                cached = self._cached_indexes.pop(category_id, None)
                if force:
                    if cached is not None: cached.close()
                    cached = None
                    self._drop_cached()
                    previous.close()
                else:
                    self._park_index(previous_category, previous)
                if cached is not None and self._fresh_index(cached) is None:
                    cached.close()
                    cached = None
                self.index = cached or self._new_index(category_id)
                self._category_id = category_id
                self._positions.clear()
                self._position_epoch = None
                self._state = dict(preparing=False, complete=False, movies=0, reason="")
            complete = self._fresh_index(self.index)
            if complete is not None:
                self._started = True
                self._state = dict(complete, preparing=False)
                self.condition.notify_all()
                return
            super().start(preferred_category=category_id)

    def invalidate(self):
        """GREEN discards the selected category; loading restarts on selection."""
        with self.condition:
            if self._closed:
                return
            previous = self.index
            self._clear_sort_view()
            self._drop_cached()
            self.stop()
            self.index = self._empty_index()
            self._category_id = None
            self._positions.clear()
            self._position_epoch = None
            self._state = dict(preparing=False, complete=False, movies=0, reason="")
            previous.close()

    def borrow_search(self):
        """Only an entirely loaded All list can satisfy account-wide search."""
        if not self.condition.acquire(False):
            return None
        try:
            if (self._closed or self._category_id != '' or self._state.get('preparing')
                    or not self._state.get('complete')):
                return None
            return self.index.borrow_search()
        finally:
            self.condition.release()

    def read(self, category_id="", after=0, hidden_category_ids=(), token=None):
        with self.condition:
            if str(category_id or "") != self._category_id:
                raise ContentError("Request cancelled", ContentError.CANCELLED)
        if self.sort_mode != 'provider':
            from .content_sort import SortUnavailable
            try:
                return self._sorted_view(category_id, hidden_category_ids, token).page(after, 80, category_id, token)
            except SortUnavailable as error:
                raise ContentError(str(error), ContentError.INVALID_RESPONSE)
        page = super().read(category_id, after, hidden_category_ids, token)
        state = page.catalog_state
        if not page.items and not state.get("preparing") and state.get("reason"):
            reason = state["reason"]
            message = ("Could not load series" if self.content_type == "series"
                       else "Could not load movies")
            if state.get('limit_kind') == 'available-memory':
                message = RAM_UNAVAILABLE
            elif reason == "limit":
                message = "Catalogue limit reached. Results may be incomplete."
            elif reason == ContentError.DEADLINE:
                message = "Request timed out"
            raise ContentError(message, reason)
        return page

    def _clear_sort_view(self):
        if self._full_list is not None:
            self._full_list.close()
            self._full_list = None
        view, self._sort_view = self._sort_view, None
        if view is not None: view.close()

    def configure_sort(self, mode, prepared=None):
        with self.condition:
            if self._closed:
                if prepared is not None: prepared.close()
                return
            if mode != self.sort_mode or prepared is not None:
                self._clear_sort_view()
                self.sort_mode = mode
                self._sort_view = prepared
                self._positions.clear()

    def read_all(self, category_id='', hidden_category_ids=(), token=None, _sort_fallback=False):
        """Wait for the provider list, then expose every position to the GUI."""
        from .category_content import CategoryContentList
        from .content import MovieSearchPage
        from .content_sort import SortUnavailable
        with self.condition:
            index, generation, mode = self.index, self._generation, self.sort_mode
            while self._state.get('preparing'):
                if token is not None:
                    token.check()
                if self._closed or index is not self.index or generation != self._generation:
                    raise ContentError(reason=ContentError.CANCELLED)
                self.condition.wait(.2)
            if (self._closed or not self._started or index is not self.index
                    or generation != self._generation or str(category_id or '') != self._category_id):
                raise ContentError(reason=ContentError.CANCELLED)
            state = dict(self._state)
        # Keep a useful partial list after a provider error, but never claim
        # it is complete or request additional rows merely by moving a cover.
        if not state.get('movies') and state.get('reason'):
            message = RAM_UNAVAILABLE if state.get('limit_kind') == 'available-memory' else (
                'Catalogue limit reached. Results may be incomplete.' if state['reason'] == 'limit' else
                'Request timed out' if state['reason'] == ContentError.DEADLINE else
                'Could not load series' if self.content_type == 'series' else 'Could not load movies')
            raise ContentError(message, state['reason'])
        sorted_view, sort_error = None, ''
        if mode != 'provider':
            from .movie_catalog import CatalogLimitError, catalog_failure_message
            try:
                sorted_view = self._sorted_view(category_id, hidden_category_ids, token)
            except SortUnavailable as error:
                if not _sort_fallback:
                    raise ContentError(str(error), ContentError.INVALID_RESPONSE)
                sort_error = str(error)
            except (CatalogLimitError, MemoryError) as error:
                if not _sort_fallback: raise
                sort_error = catalog_failure_message(error)
        full = CategoryContentList(index._select_backend(token), category_id,
                                   hidden_category_ids, sorted_view, token)
        full.sort_error = sort_error
        with self.condition:
            try:
                if token is not None:
                    token.check()
                if (self._closed or index is not self.index or generation != self._generation
                        or mode != self.sort_mode):
                    raise ContentError(reason=ContentError.CANCELLED)
            except BaseException:
                full.close()
                raise
            previous, self._full_list = self._full_list, full
            if previous is not None:
                previous.close()
        page = MovieSearchPage(page_size=max(1, len(full)), total_items=len(full), source='catalog')
        page.items, page.next_cursor, page.catalog_state = full, len(full), state
        return page

    def read_display(self, category_id='', hidden_category_ids=(), token=None):
        """Stalker publishes its first page; Xtream keeps its atomic list."""
        if self.native and str(getattr(self.client,'source_type',''))=='stalker':
            if self._fresh_index(self.index) is None:
                return self.read_ready(category_id,hidden_category_ids,token)
        return self.read_all(category_id, hidden_category_ids, token, _sort_fallback=True)

    def read_ready(self, category_id='', hidden_category_ids=(), token=None):
        """Expose useful rows before the producer has collected the category."""
        from .category_content import GrowingCategoryList
        from .content import MovieSearchPage
        with self.condition:
            index, generation = self.index, self._generation
        view = None
        try:
            while True:
                if token is not None:
                    token.check()
                with self.condition:
                    if (self._closed or not self._started or index is not self.index
                            or generation != self._generation or str(category_id or '') != self._category_id):
                        raise ContentError(reason=ContentError.CANCELLED)
                    state = dict(self._state)
                    if self._sort_view is not None and not state.get('preparing'):
                        return self.read_all(category_id, hidden_category_ids, token)
                backend = index._select_backend(token)
                with backend.session.lock:
                    available = (backend.session.work if backend.session.preparing
                                 else backend.session.visible())
                    if available is not backend.session.visible():
                        available = None
                if available is not None and (available.rows or not state.get('preparing')):
                    if view is None:
                        view = GrowingCategoryList(backend, category_id, hidden_category_ids, token)
                    else:
                        view.refresh(token)
                    if len(view) or (not state.get('preparing') and not view.pending):
                        break
                with self.condition:
                    if not state.get('preparing') and not state.get('movies'):
                        # Reuse established empty/error messages and reasons.
                        return self.read_all(category_id, hidden_category_ids, token)
                    self.condition.wait(.025)
            with self.condition:
                if index is not self.index or generation != self._generation or self._closed:
                    raise ContentError(reason=ContentError.CANCELLED)
                previous, self._full_list = self._full_list, view
                if previous is not None and previous is not view:
                    previous.close()
            page = MovieSearchPage(page_size=max(1, len(view)), total_items=len(view), source='catalog')
            page.items, page.next_cursor, page.catalog_state = view, len(view), state
            view = None
            return page
        finally:
            if view is not None:
                view.close()

    def prepare_sort(self, mode, category_id, hidden=(), token=None, _display=False):
        from .content_sort import SortedIndex, SortUnavailable, INCOMPLETE
        with self.condition:
            index, generation = self.index, self._generation
            while self._state.get('preparing'):
                if token is not None: token.check()
                if self._closed or index is not self.index or generation != self._generation:
                    raise ContentError(reason=ContentError.CANCELLED)
                self.condition.wait(.2)
            if self._closed or index is not self.index or str(category_id or '') != self._category_id:
                raise ContentError(reason=ContentError.CANCELLED)
        if not index.state().get('complete'):
            raise SortUnavailable(INCOMPLETE)
        catalog = self
        class LinkedToken:
            def check(self):
                if token is not None: token.check()
                if catalog._closed or index is not catalog.index or generation != catalog._generation:
                    raise ContentError(reason=ContentError.CANCELLED)
        linked = LinkedToken()
        backend = index._select_backend(linked)
        if getattr(backend.session.active.rows, 'plain', False):
            if _display:
                from .plain_catalog import background_order
                return background_order(backend, mode, str(category_id or ''), hidden, linked)
            from .plain_catalog import PlainSortedIndex
            return PlainSortedIndex(backend, mode, str(category_id or ''), hidden, linked)
        return SortedIndex(backend, mode, str(category_id or ''), hidden, linked)

    def _sorted_view(self, category_id, hidden, token):
        with self.condition:
            view, mode, index = self._sort_view, self.sort_mode, self.index
        if view is not None:
            view.check(token)
            return view
        view = self.prepare_sort(mode, category_id, hidden, token)
        with self.condition:
            if self._closed or index is not self.index or mode != self.sort_mode:
                view.close()
                raise ContentError(reason=ContentError.CANCELLED)
            if self._sort_view is None: self._sort_view = view
            else: view.close()
            return self._sort_view

    def read_window(self, category_id='', offset=0, size=240, hidden_category_ids=(), token=None, _generation_retry=0):
        if self.sort_mode != 'provider':
            return self._sorted_view(category_id, hidden_category_ids, token).page(offset, size, category_id, token)
        return super().read_window(category_id, offset, size, hidden_category_ids, token, _generation_retry)

    def sort_position(self, identity, category_id, hidden, token):
        if self.sort_mode != 'provider':
            return self._sorted_view(category_id, hidden, token).position(identity, token)
        from .category_visibility import category_ids_visible
        backend = self.index._select_backend(token)
        with backend.session.lock:
            backend.check(token)
            generation = backend.session.visible()
            if generation is None: return 0
            rows = generation.rows
            physical = rows.by_id.get(rows.identity(str(identity)))
            if physical is None: return 0
            allowed = tuple((not category_id or category_id in ids) and category_ids_visible(ids, hidden, category_id) for ids in rows.groups)
            position = 0
            for number in range(physical):
                if number % 1024 == 0: backend.check(token)
                position += bool(allowed[rows.codes[number]])
            return position

    def close(self):
        with self.condition:
            self._closed = True
            self._clear_sort_view()
            self._drop_cached()
        super().close()

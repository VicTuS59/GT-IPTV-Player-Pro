# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""On-demand category browsing, independent of account-wide search.

Only the selected category owns a producer and compressed RAM rows. Switching
categories cancels/releases the preceding producer; there is no account scan,
disk cache or dependency on the search screen's catalogue session.
"""
import hashlib

from .content import ContentError
from .movie_browse import MovieBrowseCatalog
from .movie_catalog import MovieCatalogIndex, SeriesCatalogIndex


class CategoryBrowseCatalog(MovieBrowseCatalog):
    def __init__(self, client, content_type="movie"):
        self.client = client
        self.content_type = content_type
        self._index_class = SeriesCatalogIndex if content_type == "series" else MovieCatalogIndex
        self._category_id = None
        self.sort_mode = 'provider'
        self._sort_view = None
        super().__init__(client, index=self._index_class(client))

    def _new_index(self, category_id):
        index = self._index_class(self.client)
        index.browse_category = category_id
        # A category must never share its rows/producer with global search,
        # another category, or the other content type for the same account.
        if index.scope:
            digest = hashlib.sha256(category_id.encode("utf-8")).hexdigest()
            index.scope += ":browse:" + digest
        return index

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
                previous = self.index
                self.stop()
                self.index = self._new_index(category_id)
                self._category_id = category_id
                self._positions.clear()
                self._position_epoch = None
                self._state = dict(preparing=False, complete=False, movies=0, reason="")
                previous.close()
            super().start(preferred_category=category_id)

    def invalidate(self):
        """GREEN discards the selected category; loading restarts on selection."""
        with self.condition:
            if self._closed:
                return
            previous = self.index
            self._clear_sort_view()
            self.stop()
            self.index = self._index_class(self.client)
            self._category_id = None
            self._positions.clear()
            self._position_epoch = None
            self._state = dict(preparing=False, complete=False, movies=0, reason="")
            previous.close()

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
            if reason == "limit":
                message = "Catalogue limit reached. Results may be incomplete."
            elif reason == ContentError.DEADLINE:
                message = "Request timed out"
            raise ContentError(message, reason)
        return page

    def _clear_sort_view(self):
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

    def prepare_sort(self, mode, category_id, hidden=(), token=None):
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
        return SortedIndex(index._select_backend(linked), mode, str(category_id or ''), hidden, linked)

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
        super().close()

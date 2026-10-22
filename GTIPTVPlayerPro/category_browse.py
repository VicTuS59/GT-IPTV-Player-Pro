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

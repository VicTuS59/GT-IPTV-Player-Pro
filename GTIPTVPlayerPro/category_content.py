# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""A complete category backed by one RAM catalogue, without GUI page copies."""
from array import array
from collections import OrderedDict
from collections.abc import Sequence
import operator
import sys
import threading

from .category_visibility import category_filter_active, category_ids_visible
from .content import ContentError
from .content_sort import Lease
from .movie_catalog import CatalogLimitError


class CategoryContentList(Sequence):
    """All positions are immediately accessible; only nearby item objects cache.

    Provider metadata stays in CategoryRows. The GUI and movie player share
    this sequence instead of hydrating/copying the entire catalogue again.
    Indexing performs no provider, artwork or TMDb requests.
    """
    full_category = True
    cache_limit = 128

    def __init__(self, backend, category_id, hidden=(), sorted_view=None, token=None,
                 search_terms=None, discovery=None):
        self.backend, self.category_id = backend, str(category_id or '')
        self.full_search = search_terms is not None
        self.lock = threading.RLock()
        self.closed = False
        self.cache = OrderedDict()
        self.lease = Lease(check_available=False)
        self.sort_weight = 0
        self.native_menu_rows=None
        try:
            with backend.session.lock:
                backend.check(token)
                self.generation = backend.session.visible()
                if self.generation is None:
                    raise ContentError(reason=ContentError.CANCELLED)
                rows = self.generation.rows
                if sorted_view is not None:
                    sorted_view.check(token)
                    if sorted_view.generation is not self.generation:
                        raise ContentError(reason=ContentError.CANCELLED)
                    self.order = sorted_view.order
                    self.sort_weight = sorted_view.lease.amount[0]
                else:
                    direct = (getattr(rows, 'plain', False)
                              and not hidden and not category_filter_active(hidden)
                              and self.category_id == rows.selected)
                    allowed = (True,) if direct and not discovery else tuple((not self.category_id or self.category_id in ids)
                                    and category_ids_visible(ids, hidden, self.category_id) for ids in rows.groups)
                    if self.full_search:
                        # Store only matching integer positions, never a second
                        # full set of ContentItem objects or provider metadata.
                        cost = len(rows) * (6 if discovery else 5) + 4096
                        if backend.session.weight() + cost > backend.owner.max_bytes:
                            raise CatalogLimitError('catalog-memory')
                        self.lease.reserve(cost)
                        self.order = array('I', backend.matching_positions(rows, search_terms, allowed, token, discovery))
                        self.lease.reserve(sys.getsizeof(self.order) + 4096 - cost)
                    elif all(allowed):
                        self.order = range(len(rows))
                    else:
                        cost = len(rows) * 5 + 4096
                        if backend.session.weight() + cost > backend.owner.max_bytes:
                            raise CatalogLimitError('catalog-memory')
                        self.lease.reserve(cost)
                        self.order = array('I')
                        for position, code in enumerate(rows.codes):
                            if position % 1024 == 0:
                                backend.check(token)
                            if allowed[code]:
                                self.order.append(position)
                        self.lease.reserve(sys.getsizeof(self.order) + 4096 - cost)
            if getattr(rows,'native',False):
                labels=rows.native_labels
                self.native_menu_rows=[(labels[i][1],labels[i][0]) for i in self.order]
        except BaseException:
            self.lease.close()
            raise

    def _check(self):
        self.backend.check()
        if self.closed or self.generation is not self.backend.session.visible():
            raise ContentError(reason=ContentError.CANCELLED)

    def __len__(self):
        return 0 if self.closed else len(self.order)

    def __getitem__(self, key):
        if isinstance(key, slice):
            return [self[position] for position in range(*key.indices(len(self)))]
        position = operator.index(key)
        if position < 0:
            position += len(self)
        if not 0 <= position < len(self):
            raise IndexError('category index out of range')
        with self.lock, self.backend.session.lock:
            self._check()
            physical = self.order[position]
            cached = self.cache.get(physical)
            if cached is not None:
                self.cache.move_to_end(physical)
                return cached[0]
            item = self.backend.hydrate(self.generation.rows.record(physical), self.category_id)
            if item is None:
                raise ContentError(reason=ContentError.INVALID_RESPONSE)
            cost = sys.getsizeof(item) + sys.getsizeof(vars(item)) + sum(sys.getsizeof(v) for v in vars(item).values()) + 128
            while len(self.cache) >= self.cache_limit:
                unused, (old, weight) = self.cache.popitem(last=False)
                self.lease.reserve(-weight)
            try:
                # This optional object cache must not make loaded rows unreadable.
                if self.backend.session.weight() + self.sort_weight + self.lease.amount[0] + cost > self.backend.owner.max_bytes:
                    return item
                self.lease.reserve(cost)
            except CatalogLimitError:
                return item
            self.cache[physical] = (item, cost)
            return item

    def position(self, identity):
        """Find a saved selection using IDs/integers, without hydrating all rows."""
        with self.lock, self.backend.session.lock:
            self._check()
            rows = self.generation.rows
            finder=getattr(rows,'find_identity',None)
            physical=(finder(identity) if callable(finder)
                      else rows.by_id.get(rows.identity(str(identity))))
            if physical is not None:
                if isinstance(self.order, range):
                    return physical if physical < len(self.order) else None
                if getattr(self,'progressive',False) and not self.finalized:
                    from bisect import bisect_left
                    index=bisect_left(self.order,physical)
                    return index if index<len(self.order) and self.order[index]==physical else None
                for index, value in enumerate(self.order):
                    if value == physical:
                        return index
        return None

    def labels(self, start, stop):
        """Visible native text rows; never hydrate playback/detail objects."""
        with self.lock, self.backend.session.lock:
            self._check()
            rows = self.generation.rows
            positions = self.order[max(0, start):min(len(self.order), stop)]
            if callable(getattr(rows, 'label', None)):
                return [rows.label(position) for position in positions]
            return [(record['i'], record['n']) for record in
                    (rows.record(position) for position in positions)]

    def cached_items(self):
        with self.lock:
            return [item for item, cost in self.cache.values()]

    def close(self):
        with self.lock:
            self.closed = True
            self.cache.clear()
            self.order = ()
            self.native_menu_rows=None
            self.lease.close()


class GrowingCategoryList(CategoryContentList):
    """A readable prefix of the same category while its producer continues.

    Every position is a real admitted row. No placeholders, GUI page batches
    or network requests are created by indexing or refreshing this view.
    """
    progressive = True

    def __init__(self, backend, category_id, hidden=(), token=None):
        self.backend, self.category_id = backend, str(category_id or '')
        self.hidden, self.full_search = hidden, False
        self.lock, self.closed = threading.RLock(), False
        self.cache, self.lease = OrderedDict(), Lease(check_available=False)
        self.sort_weight, self.position_weight = 0, 0
        self.scanned, self.finalized, self.sort_error = 0, False, ''
        self.native_menu_rows=None
        self.native_revision=0
        self.order = range(0)
        with backend.session.lock:
            backend.check(token)
            self.generation = backend.session.visible()
            if self.generation is None:
                raise ContentError(reason=ContentError.CANCELLED)
            rows = self.generation.rows
            if getattr(rows,'native',False):self.native_menu_rows=[]
            self.direct = (getattr(rows, 'plain', False) and not hidden
                           and not hasattr(hidden, 'allowed') and self.category_id == rows.selected)
            if not self.direct:
                self.order = array('I')
        try:
            self.refresh(token=token)
        except BaseException:
            self.close()
            raise

    @property
    def pending(self):
        return not self.closed and not self.finalized and self.scanned < len(self.generation.rows)

    def refresh(self, token=None, blocking=True, batch=4096):
        if self.closed or self.finalized:
            return False
        with self.lock:
            if not self.backend.session.lock.acquire(blocking=blocking):
                return False
            try:
                self._check()
                self.backend.check(token)
                rows, before = self.generation.rows, len(self.order)
                if self.direct:
                    self.scanned = (min(len(rows),self.scanned+batch)
                                    if self.native_menu_rows is not None else len(rows))
                    self.order = range(self.scanned)
                else:
                    end = min(len(rows), self.scanned + batch)
                    positions = array('I')
                    for i in range(self.scanned, end):
                        if i % 256 == 0:
                            self.backend.check(token)
                        ids = tuple(rows.aliases[i]) if i in rows.aliases else rows.members(rows.values[i])
                        if ((not self.category_id or self.category_id in ids)
                                and category_ids_visible(ids, self.hidden, self.category_id)):
                            positions.append(i)
                    # Reserve before changing a visible list, so a failed
                    # optional position allocation leaves its prefix readable.
                    cost = (len(self.order)+len(positions))*5+4096
                    if self.backend.session.weight()+self.lease.amount[0]+cost-self.position_weight > self.backend.max_bytes:
                        raise CatalogLimitError('catalog-memory')
                    self.lease.reserve(cost-self.position_weight)
                    self.position_weight = cost
                    self.order.extend(positions)
                    self.scanned = end
                changed=len(self.order)!=before
                if changed and self.native_menu_rows is not None:
                    labels=rows.native_labels
                    self.native_menu_rows.extend((labels[i][1],labels[i][0]) for i in self.order[before:])
                    self.native_revision+=1
                return changed
            finally:
                self.backend.session.lock.release()

    def apply_sort(self, view, native_menu_rows=None):
        with self.lock, self.backend.session.lock:
            self._check()
            view.check()
            if view.generation is not self.generation or self.backend.session.preparing:
                raise ContentError(reason=ContentError.CANCELLED)
            self.lease.reserve(-self.position_weight)
            self.position_weight = 0
            self.order, self.sort_weight = view.order, view.lease.amount[0]
            if self.native_menu_rows is not None:
                self.native_menu_rows=native_menu_rows
                self.native_revision+=1
            self.finalized = True

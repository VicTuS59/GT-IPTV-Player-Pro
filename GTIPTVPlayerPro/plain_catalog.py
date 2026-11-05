# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""One provider list; validation of display objects and search/sort is on demand."""
from array import array
from contextlib import nullcontext
import sys
import threading
import weakref
from types import SimpleNamespace

from .content import ContentError, _numeric_id, _clean_text, _movie_search_text
from .category_preferences import content_item_category_ids
from .category_visibility import category_ids_visible
from .packed_catalog import CategoryRows
from .vod_catalog import _compact_text
from .content_sort import (SortedIndex, Lease, SortUnavailable, INCOMPLETE, NO_DATES,
                          assign_sort_metadata, category_fast_order, heap_order)


class PortalRow:
    """Raw portal response plus the paging dialect needed for later playback."""
    __slots__ = ('entry', 'client', 'content_type', 'stream_id', 'request_type',
                 'selected', 'allow_composite', 'portal_catalog_page',
                 'portal_catalog_category_parameter', 'portal_catalog_series_flag')

    def __init__(self, client, entry, kind, request_type, allow_composite, selected):
        self.entry, self.client, self.content_type = entry, client, kind
        self.stream_id = client._media_identifier(entry, kind, allow_series_composite=allow_composite)
        self.request_type, self.selected = request_type, selected
        self.allow_composite = allow_composite
        self.portal_catalog_page = None
        self.portal_catalog_category_parameter, self.portal_catalog_series_flag = 'category', ''

    def record(self):
        from .catalog_records import stalker_record
        record = stalker_record(self.client, self.entry, self.content_type,
                               self.request_type, self.allow_composite, self.selected)
        if record is not None:
            if isinstance(self.portal_catalog_page, int): record['j'] = self.portal_catalog_page
            if self.content_type == 'series':
                record['h'], record['f'] = self.portal_catalog_category_parameter, self.portal_catalog_series_flag
            self.client.remember_category_row(self)
        return record


class SelectedRow:
    """Retain the category context of an account-wide fallback request."""
    __slots__ = ('value', 'category', 'name')
    def __init__(self, value, category, name):
        self.value, self.category, self.name = value, category, name


def raw_entry(value):
    if isinstance(value, SelectedRow): return raw_entry(value.value)
    return value.entry if isinstance(value, PortalRow) else value if isinstance(value, dict) else vars(value)


def raw_weight(value):
    """One bounded accounting pass; no record copies, codecs or normalization."""
    root = raw_entry(value)
    extra = sys.getsizeof(value) if isinstance(value, (PortalRow, SelectedRow)) else 0
    total, pending = 128 + extra, [root]
    while pending:
        current = pending.pop()
        total += sys.getsizeof(current)
        if isinstance(current, dict):
            total += sum(sys.getsizeof(key) for key in current)
            pending.extend(current.values())
        elif isinstance(current, (list, tuple)):
            pending.extend(current)
    return total


class PlainRows(CategoryRows):
    """Raw dictionaries in provider order; optional columns are created only on use."""
    plain = True

    def __init__(self, resize, owner):
        self.resize = resize
        self.owner = owner if isinstance(owner, weakref.ProxyTypes) else weakref.proxy(owner)
        self.client, self.kind = owner.client, owner.content_type
        self.selected = str(getattr(owner, 'browse_category', '') or '')
        self.values, self.by_id, self.aliases = [], {}, {}
        self.blocks = []
        self._groups = self._codes = self._names = self._ends = None
        self._added = self._released = None
        self._has_added = self._has_released = None
        self._columns_weight = 0

    def _discard_columns(self):
        if self._columns_weight:
            self.resize(-self._columns_weight)
        self._columns_weight = 0
        self._groups = self._codes = self._names = self._ends = None
        self._added = self._released = None
        self._has_added = self._has_released = None

    def _column_cost(self, cost):
        self.resize(cost)
        self._columns_weight += cost

    def __len__(self): return len(self.values)

    def raw_identity(self, value):
        if isinstance(value, SelectedRow): value = value.value
        if isinstance(value, PortalRow): return str(value.stream_id or '')
        if isinstance(value, dict):
            return _numeric_id(value.get('series_id' if self.kind == 'series' else 'stream_id'))[:40]
        return str(getattr(value, 'stream_id', '') or '')

    def label(self, position):
        """Read a title/identity without URL, date, synopsis or item codecs."""
        value = self.values[position]
        identity = self.raw_identity(value)
        if isinstance(value, SelectedRow): value = value.value
        if isinstance(value, (dict, PortalRow)):
            entry = raw_entry(value)
            title = (entry.get('name') or entry.get('title') or entry.get('o_name')
                     if isinstance(value, PortalRow) else entry.get('name'))
        else:
            title = getattr(value, 'name', '')
        from .i18n import _
        return identity, _compact_text(_clean_text(title) or _('Untitled content'), 180)

    def extend(self, values, check, valid_id, max_items, commit_lock=None):
        """Admit one decoded JSON batch with one budget/lock transaction.

        Normal unique rows keep the decoder's shared keys and dictionaries.
        Duplicate IDs use the existing membership/date merge rules.
        """
        from .provider_rows import retain
        from .content import _validate_provider_json_depth
        from .memory_catalog import limit
        staged, identities, cost = [], {}, 0
        count = len(self.values)
        for position, value in enumerate(values):
            if position % 256 == 0: check()
            if not isinstance(value, dict): continue
            identity = self.raw_identity(value)
            if not valid_id(identity): continue
            identity = self.identity(identity)
            if identity in self.by_id or identity in identities:
                # Rare provider duplicates need category/date merging. Keep
                # their established semantics instead of silently dropping.
                for number, row in enumerate(values):
                    if number % 256 == 0: check()
                    if not isinstance(row, dict): continue
                    key = self.raw_identity(row)
                    if not valid_id(key): continue
                    if len(self) >= max_items and self.identity(key) not in self.by_id:
                        raise limit('item-count')
                    try: _validate_provider_json_depth(row)
                    except ValueError:
                        raise ContentError('Server returned an invalid content response', ContentError.INVALID_RESPONSE)
                    with commit_lock if commit_lock is not None else nullcontext():
                        check()
                        self.append(row)
                return
            if count + len(staged) >= max_items: raise limit('item-count')
            try: _validate_provider_json_depth(value)
            except ValueError:
                raise ContentError('Server returned an invalid content response', ContentError.INVALID_RESPONSE)
            value = retain(value, shared_keys=True)
            # Flat JSON is the usual API shape; avoid a recursive tree walk
            # and repeated schema-key accounting for every title.
            raw_values = dict.values(value)
            if any(isinstance(v, (dict, list, tuple)) for v in raw_values):
                weight = raw_weight(value)
            else:
                weight = 128 + sys.getsizeof(value) + sum(map(sys.getsizeof, raw_values))
            if weight > 2*1024*1024:
                import json
                from .provider_rows import expanded
                if len(json.dumps(expanded(value), ensure_ascii=False).encode('utf-8')) > 2*1024*1024:
                    raise ContentError('Server content list is too large', ContentError.RESPONSE_TOO_LARGE)
            identities[identity] = count + len(staged)
            staged.append(value)
            cost += weight + sys.getsizeof(identity) + 96
        check()
        if staged:
            # Keys share storage within the decoded response. Account each
            # schema key once, and include list/dict spare capacity overhead.
            keys = {key for row in staged for key in row}
            cost += sum(map(sys.getsizeof, keys)) + sys.getsizeof(identities) + sys.getsizeof(staged)
            # Preparation runs outside the session lock, so cancellation and
            # category changes cannot block behind a whole response scan.
            with commit_lock if commit_lock is not None else nullcontext():
                check()
                self.resize(cost)
                self._discard_columns()
                self.values.extend(staged)
                self.by_id.update(identities)

    def append(self, value):
        from .provider_rows import retain
        if isinstance(value, SelectedRow):
            if isinstance(value.value, dict): value.value = retain(value.value)
            elif isinstance(value.value, PortalRow): value.value.entry = retain(value.value.entry)
        elif isinstance(value, PortalRow): value.entry = retain(value.entry)
        elif isinstance(value, dict): value = retain(value)
        identity = self.identity(self.raw_identity(value))
        old = self.by_id.get(identity)
        if old is not None:
            before = self.aliases.get(old)
            original = set(before or self.members(self.values[old]))
            members = set(original)
            members.update(self.members(value)); members = set(sorted(members)[:128])
            first, later = raw_entry(self.values[old]), raw_entry(value)
            updates = {}
            for field in ('added','date_added','added_at','created_at','created','last_modified',
                          'lastModified','modified_at','updated_at','last_updated',
                          'release_date','releasedate','releaseDate','first_air_date','year','release_year'):
                if not first.get(field) and later.get(field): updates[field] = later[field]
            if members == original and not updates:
                return
            self._discard_columns()
            if members != original:
                cost = sys.getsizeof(members)+sum(sys.getsizeof(v) for v in members)
                previous = (sys.getsizeof(before)+sum(sys.getsizeof(v) for v in before)) if before is not None else 0
                self.resize(cost-previous)
                self.aliases[old] = members
            if updates:
                before_fields = raw_weight(self.values[old])
                first.update(updates)
                self.resize(raw_weight(self.values[old])-before_fields)
            return
        weight = raw_weight(value)
        if weight > 2*1024*1024 and isinstance(value, (dict, PortalRow)):
            import json
            from .provider_rows import expanded
            if len(json.dumps(expanded(raw_entry(value)), ensure_ascii=False).encode('utf-8')) > 2*1024*1024:
                raise ContentError('Server content list is too large', ContentError.RESPONSE_TOO_LARGE)
        self.resize(weight + sys.getsizeof(identity) + 96)
        self._discard_columns()
        self.by_id[identity] = len(self.values)
        self.values.append(value)
        self._groups = self._codes = self._names = self._ends = None
        self._added = self._released = None
        self._has_added = self._has_released = None

    def members(self, value):
        category = value.category if isinstance(value, SelectedRow) else ''
        if isinstance(value, SelectedRow): value = value.value
        if isinstance(value, PortalRow):
            result = set(value.client._category_ids(value.entry))
            if value.selected: result.add(value.selected)
        else:
            result = set(content_item_category_ids(value))
        if self.selected: result.add(self.selected)
        if category: result.add(category)
        return tuple(sorted(result))

    def _group_columns(self):
        if self._groups is not None: return self._groups, self._codes
        groups, lookup, codes = [()], {():0}, array('I')
        for position, value in enumerate(self.values):
            group = tuple(sorted(self.aliases[position])) if position in self.aliases else self.members(value)
            code = lookup.get(group)
            if code is None:
                code = len(groups); lookup[group] = code; groups.append(group)
            codes.append(code)
        from .movie_catalog import CatalogLimitError
        try:
            self._column_cost(sys.getsizeof(codes) + sum(sys.getsizeof(group)+128 for group in groups))
        except CatalogLimitError:
            return groups, codes
        self._groups, self._codes = groups, codes
        return groups, codes

    @property
    def groups(self): return self._group_columns()[0]
    @property
    def codes(self): return self._group_columns()[1]

    def text(self, position):
        if self._names is not None:
            return bytes(self._names[self._ends[position]:self._ends[position+1]-1])
        value = self.values[position]
        if isinstance(value, SelectedRow): value = value.value
        if not isinstance(value, (dict, PortalRow)):
            name, year = value.name, getattr(value, 'year', '')
        else:
            entry = raw_entry(value)
            name = entry.get('name') or entry.get('title') or entry.get('o_name')
            year = (entry.get('year') or entry.get('releaseDate') if not isinstance(value, PortalRow)
                    else entry.get('year') or entry.get('release_year') or entry.get('releasedate') or entry.get('releaseDate'))
        name = _clean_text(name)
        if not name:
            from .i18n import _
            name = _('Untitled content')
        name = _compact_text(name, 180)
        year = _compact_text(_clean_text(year), 20)
        return _movie_search_text(name+' '+year).replace('\x00', ' ').encode('utf-8')

    def dates(self, position):
        if self._added is not None: return self._added[position], self._released[position]
        value = self.values[position]
        if isinstance(value, SelectedRow): value = value.value
        if not isinstance(value, (dict, PortalRow)):
            return int(getattr(value, 'sort_added', 0) or 0), int(getattr(value, 'sort_release', 0) or 0)
        target = SimpleNamespace(content_type=self.kind)
        assign_sort_metadata(target, raw_entry(value))
        return target.sort_added, target.sort_release

    @property
    def has_sort_added(self):
        if self._has_added is None: self._has_added = any(self.dates(i)[0] for i in range(len(self)))
        return self._has_added
    @property
    def has_sort_release(self):
        if self._has_released is None: self._has_released = any(self.dates(i)[1] for i in range(len(self)))
        return self._has_released

    def _name_columns(self, check=None):
        if self._names is not None: return
        names, ends = bytearray(), array('I', [0])
        for i in range(len(self)):
            if check is not None and i % 256 == 0: check()
            names.extend(self.text(i)+b'\0'); ends.append(len(names))
        self._column_cost(sys.getsizeof(names)+sys.getsizeof(ends))
        self._names, self._ends = names, ends
    @property
    def names(self): self._name_columns(); return self._names
    @property
    def ends(self): self._name_columns(); return self._ends

    def _date_columns(self, check=None):
        if self._added is not None: return
        added, released = array('Q'), array('Q')
        for i in range(len(self)):
            if check is not None and i % 256 == 0: check()
            a, r = self.dates(i); added.append(a); released.append(r)
        self._column_cost(sys.getsizeof(added)+sys.getsizeof(released))
        self._added, self._released = added, released
    @property
    def sort_added(self): self._date_columns(); return self._added
    @property
    def sort_release(self): self._date_columns(); return self._released

    def matches(self, terms, permitted, check):
        from .movie_catalog import CatalogLimitError
        cached = False
        try:
            self._name_columns(check)
            if permitted == (True,): permitted = (True,)*len(self.groups)
            cached = True
        except (CatalogLimitError, MemoryError):
            # Loaded rows are still searchable if an optional text cache
            # cannot grow. A single-row normalization needs no full index.
            pass
        if cached:
            yield from super().matches(terms, permitted, check)
            return
        codes = (0 for unused in self.values) if permitted == (True,) else self.codes
        for i, code in enumerate(codes):
            if i % 256 == 0: check()
            if permitted[code]:
                text = self.text(i)
                if all(term in text for term in terms): yield i

    def record(self, position):
        value = self.values[position]
        selected, name = self.selected, ''
        if isinstance(value, SelectedRow):
            selected, name, value = value.category, value.name, value.value
        if isinstance(value, PortalRow): record = value.record()
        elif isinstance(value, dict):
            from .catalog_records import xtream_record
            record = xtream_record(value, self.kind, self.client.account, selected)
        else:
            # Compatibility with provider adapters exposing normalized items.
            from .movie_catalog import MovieCatalogIndex, SeriesCatalogIndex
            index = (SeriesCatalogIndex if self.kind=='series' else MovieCatalogIndex)(self.client, directory='plain-codec')
            record = index._record(value)
            if record is not None:
                record.update(t=str(getattr(value,'tmdb_id','') or '')[:32],
                              b=str(getattr(value,'imdb_id','') or '')[:32],
                              sa=int(getattr(value,'sort_added',0) or 0),
                              sr=int(getattr(value,'sort_release',0) or 0),
                              a=self.members(value))
                if selected: record['c'] = selected
        if record is not None and isinstance(value, (dict, PortalRow)):
            record['_provider_entry'] = raw_entry(value)
        if record is not None and name: record['g'] = name
        if record is not None and selected:
            record['a'] = sorted(set(record.get('a', ())) | {selected})[:128]
        if record is not None and position in self.aliases: record['a'] = sorted(self.aliases[position])
        return record

    def flush(self): pass

    def drop_sort_cache(self, names=False, dates=False):
        cost = 0
        if names and self._names is not None:
            cost += sys.getsizeof(self._names)+sys.getsizeof(self._ends)
            self._names = self._ends = None
        if dates and self._added is not None:
            cost += sys.getsizeof(self._added)+sys.getsizeof(self._released)
            self._added = self._released = None
        if cost:
            self.resize(-cost);self._columns_weight -= cost

    def clear(self):
        self.values.clear(); self.by_id.clear(); self.aliases.clear()
        self._groups = self._codes = self._names = self._ends = None
        self._added = self._released = None
        self._has_added = self._has_released = None
        self._columns_weight = 0


class PlainSortedIndex(SortedIndex):
    """Native sorting requested by the user; no eager search/date columns."""
    def __init__(self, backend, mode, category_id, hidden, token):
        self.backend, self.mode = backend, mode
        self.closed, self.lock = threading.Event(), threading.Lock()
        self.lease = Lease(check_available=False)
        self.order, self.generation, self.sort_method = array('I'), None, 'provider'
        new_names = new_dates = False
        try:
            with self.lock, backend.session.lock:
                self.check(token)
                if not backend.session.state().get('complete'): raise SortUnavailable(INCOMPLETE)
                self.generation = backend.session.active
                rows = self.generation.rows
                cost = len(rows)*5+65536
                if backend.session.weight()+cost > backend.owner.max_bytes:
                    from .memory_catalog import limit
                    raise limit('catalog-memory')
                self.lease.reserve(cost)
                if not hidden and not hasattr(hidden, 'allowed') and category_id == rows.selected:
                    self.order = array('I', range(len(rows)))
                else:
                    allowed = tuple((not category_id or category_id in group)
                        and category_ids_visible(group, hidden, category_id) for group in rows.groups)
                    self.order = array('I', (i for i, code in enumerate(rows.codes) if allowed[code]))
                if mode in ('added','release','year') and self.order:
                    column = 0 if mode=='added' else 1
                    if not any(rows.dates(i)[column] for i in self.order): raise SortUnavailable(NO_DATES)
                if mode != 'provider':
                    from . import content_sort
                    from .movie_catalog import CatalogLimitError
                    estimate = len(rows)*256+65536
                    room = backend.owner.max_bytes-backend.session.weight()-self.lease.amount[0]
                    if room > estimate:
                        old_names, old_dates = rows._names is not None, rows._added is not None
                        try:
                            rows._name_columns(lambda:self.check(token))
                            if mode not in ('alpha','alpha_desc'):
                                rows._date_columns(lambda:self.check(token))
                        except (CatalogLimitError, MemoryError):
                            pass
                        finally:
                            new_names = not old_names and rows._names is not None
                            new_dates = not old_dates and rows._added is not None
                    def key(i):
                        a, r = rows.dates(i) if mode not in ('alpha','alpha_desc') else (0,0)
                        return content_sort.sort_key(mode, rows.text(i), a, r, i)
                    # Reuse the tested bounded native-sort/fallback policy,
                    # without requesting the old packed name/date columns.
                    class Shape:
                        names = ()
                    fast = category_fast_order(self.order, Shape(), key, backend,
                                               self.lease.amount[0], lambda:self.check(token))
                    if fast is None:
                        self.sort_method='heap'; heap_order(self.order, key, lambda:self.check(token))
                    else: self.sort_method='builtin'; self.order=fast
                    if mode=='alpha_desc': self.order.reverse()
                self.lease.reserve(sys.getsizeof(self.order)+4096-self.lease.amount[0])
        except BaseException:
            if self.generation is not None:
                self.generation.rows.drop_sort_cache(new_names, new_dates)
            self.lease.close()
            raise


def background_order(backend, mode, category_id, hidden, token):
    """Sort a reference snapshot without occupying the displayed rows' lock."""
    from .movie_catalog import CatalogLimitError
    scratch = Lease(check_available=False)
    view = snapshot = retained = None
    try:
        class Reader:
            _closed = False
        reader = Reader()
        # Keep provider objects charged if the browser closes while a sort
        # still holds their references. Borrowing also detaches the old
        # registry entry, so a refresh cannot mutate this snapshot.
        while retained is None:
            backend.check(token)
            retained = backend.borrow_complete(reader)
            if retained is None:
                if not backend.session.state().get('complete'):
                    raise SortUnavailable(INCOMPLETE)
                threading.Event().wait(.005)
        with backend.session.lock:
            backend.check(token)
            generation = backend.session.active
            if generation is None or not backend.session.state().get('complete'):
                raise SortUnavailable(INCOMPLETE)
            rows = generation.rows
            baseline = backend.session.weight()
            cost = len(rows)*12+len(rows.aliases)*80+4096
            if baseline+cost > backend.max_bytes:
                raise CatalogLimitError('catalog-memory')
            scratch.reserve(cost)
            def resize(delta):
                if delta > 0 and baseline+scratch.amount[0]+delta > backend.max_bytes:
                    raise CatalogLimitError('catalog-memory')
                scratch.reserve(delta)
            snapshot = PlainRows(resize, backend.owner)
            snapshot.values, snapshot.aliases = tuple(rows.values), dict(rows.aliases)
        def check(other_token=None):
            try:
                backend.check(other_token)
            except ReferenceError:
                raise ContentError(reason=ContentError.CANCELLED)
            if backend.session.active is not generation or backend.session.work is not None:
                raise ContentError(reason=ContentError.CANCELLED)
        session = SimpleNamespace(lock=threading.RLock(), active=SimpleNamespace(rows=snapshot),
            state=lambda:dict(complete=True), weight=lambda:baseline+scratch.amount[0])
        shadow = SimpleNamespace(session=session, owner=backend.owner, max_bytes=backend.max_bytes,
                                 check_available_memory=False, check=check)
        view = PlainSortedIndex(shadow, mode, category_id, hidden, token)
        with backend.session.lock:
            check(token)
            view.backend, view.generation = backend, generation
        result, view = view, None
        return result
    finally:
        if view is not None:
            view.close()
        if snapshot is not None:
            snapshot.values = ()
            snapshot.aliases.clear()
        scratch.close()
        if retained is not None:
            retained.session.release()

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scoped sort preferences and bounded, cancellable catalogue index sorting."""
from array import array
import calendar
import datetime
import hashlib
import re
import sys
import threading
import unicodedata
import weakref

from .i18n import N_

MODES = ('provider', 'added', 'alpha', 'alpha_desc', 'year', 'release', 'manual')
LABELS = {'provider': N_('Provider order'), 'added': N_('Newest added'),
          'alpha': N_('Alphabetical (A–Z)'), 'release': N_('Release date (newest first)'),
          'manual': N_('Saved manual order')}
VOD_LABELS = {'alpha': N_('A–Z'), 'alpha_desc': N_('Z–A'),
              'added': N_('Last added'), 'year': N_('Year'), 'provider': N_('Original')}
SORT_TITLE = N_('Sorting')
SORT_CONTENT = N_('Sort content')
SORT_CHANNELS = N_('Sort channels')
NO_DATES = N_('This source does not provide dates for this sort order.')
INCOMPLETE = N_('Sorting requires the complete category. Please wait or retry.')
SAVE_ERROR = N_('Could not save sorting preference.')
PATH = '/etc/enigma2/gtiptvplayer/content-sorting.json'
EPOCH_OFFSET = 62135596800


def date_value(value, year_only=False):
    """UTC epoch; neither provider IDs nor modification dates imply additions."""
    if value is None or isinstance(value, bool):
        return 0
    text = str(value).strip()[:48]
    try:
        if re.fullmatch(r'\d{4}', text):
            if not year_only: return 0
            return EPOCH_OFFSET + calendar.timegm(datetime.datetime(int(text), 1, 1).timetuple()) if 1900 <= int(text) <= 2200 else 0
        if re.fullmatch(r'\d{10}|\d{13}', text):
            number = int(text) // (1000 if len(text) == 13 else 1)
            return EPOCH_OFFSET + number if 0 < number <= 7289654399 else 0
        parsed = datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))
        if parsed.tzinfo is not None:
            return max(0, EPOCH_OFFSET + int(parsed.timestamp()))
        return max(0, EPOCH_OFFSET + calendar.timegm(parsed.timetuple()))
    except (ValueError, TypeError, OverflowError, OSError):
        return 0


def assign_sort_metadata(item, entry):
    def first(keys, year=False):
        for key in keys:
            value = date_value(entry.get(key), year)
            if value: return value
        return 0
    item.sort_added = first(('added', 'date_added', 'added_at', 'created_at', 'created'))
    item.sort_release = first(('release_date', 'releasedate', 'releaseDate', 'first_air_date', 'year', 'release_year'), True)
    return item


def preference_scope(account_scope, content_type, category_id):
    if not re.fullmatch(r'[0-9a-f]{64}', str(account_scope)) or content_type not in ('movie', 'series', 'live'):
        return ''
    value = '{}\0{}\0{}'.format(account_scope, content_type, category_id or '')
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


class SortStore:
    def __init__(self, path=PATH):
        from .category_preferences import LiveCategoryPreferenceStore
        self.store = LiveCategoryPreferenceStore(path)

    def load(self, scope, default='provider'):
        order = self.store.load(scope).order if scope else ()
        mode = order[0] if order and order[0] in MODES else default
        return 'year' if mode == 'release' else mode

    def save(self, scope, mode):
        return bool(scope and mode in MODES and self.store.save(scope, (mode,), ()))


class SortUnavailable(Exception):
    pass


def normalized_name(value):
    text = unicodedata.normalize('NFKD', str(value or '')[:240].casefold().replace('ı', 'i'))
    return ''.join(c for c in text if not unicodedata.combining(c)).encode('utf-8')


def release_year(value):
    """Read the year from the existing packed date; no additional RAM column."""
    if not value:
        return 0
    try:
        return datetime.date.fromordinal(int(value) // 86400 + 1).year
    except (ValueError, OverflowError, TypeError):
        return 0


def sort_key(mode, name, added, released, position):
    if mode == 'alpha': return (name, position)
    if mode == 'alpha_desc': return (name, -position)
    value = added if mode == 'added' else release_year(released) if mode == 'year' else released
    return (not bool(value), -value, name, position)


def heap_order(values, key, check):
    """In-place heap sort: no per-item key objects or second full catalogue."""
    calls = [0]
    def sift(root, end):
        value = values[root]
        value_key = key(value)
        while root * 2 + 1 < end:
            calls[0] += 1
            if calls[0] % 512 == 0: check()
            child = root * 2 + 1
            child_key = key(values[child])
            if child + 1 < end:
                other = key(values[child + 1])
                if child_key < other:
                    child += 1
                    child_key = other
            if value_key >= child_key: break
            values[root] = values[child]
            root = child
        values[root] = value
    check()
    for root in range(len(values) // 2 - 1, -1, -1): sift(root, len(values))
    for end in range(len(values) - 1, 0, -1):
        values[0], values[end] = values[end], values[0]
        sift(0, end)
    check()


class Lease:
    """All extra indexes/held live rows debit the existing shared RAM budget."""
    def __init__(self):
        from .memory_catalog import budget
        self.budget = budget
        self.amount = [0]
        self.finalizer = weakref.finalize(self, self._release, budget, self.amount)

    @staticmethod
    def _release(budget, amount):
        budget.reserve(-amount[0]);amount[0] = 0

    def reserve(self, amount):
        self.budget.reserve(amount);self.amount[0] += amount

    def close(self): self.finalizer()


class SortedIndex:
    def __init__(self, backend, mode, category_id, hidden, token):
        from .category_visibility import category_ids_visible
        self.backend = backend
        self.mode = mode
        self.closed = threading.Event()
        self.lock = threading.Lock()
        self.lease = Lease()
        self.order = array('I')
        self.generation = None
        try:
            with self.lock, backend.session.lock:
                backend.check(token, memory=True)
                if not backend.session.state().get('complete'):
                    raise SortUnavailable(INCOMPLETE)
                gen = self.generation = backend.session.active
                rows = gen.rows
                if backend.session.weight() + len(rows) * 5 + 65536 > backend.owner.max_bytes:
                    from .memory_catalog import limit
                    raise limit('catalog-memory')
                self.lease.reserve(len(rows) * 5 + 65536)
                if mode == 'added' and not rows.has_sort_added or mode in ('release', 'year') and not rows.has_sort_release:
                    raise SortUnavailable(NO_DATES)
                allowed = tuple((not category_id or category_id in ids) and category_ids_visible(ids, hidden, category_id) for ids in rows.groups)
                found_date = False
                for number, code in enumerate(rows.codes):
                    if number % 1024 == 0: self.check(token)
                    if allowed[code]:
                        self.order.append(number)
                        found_date = found_date or bool(rows.sort_added[number] if mode == 'added' else rows.sort_release[number])
                if mode in ('added', 'release', 'year') and self.order and not found_date:
                    raise SortUnavailable(NO_DATES)
                if mode != 'provider':
                    def key(pos):
                        name = bytes(rows.names[rows.ends[pos]:rows.ends[pos+1]-1])
                        return sort_key(mode, name, rows.sort_added[pos], rows.sort_release[pos], pos)
                    heap_order(self.order, key, lambda:self.check(token))
                    if mode == 'alpha_desc': self.order.reverse()
                actual = sys.getsizeof(self.order) + 4096
                self.lease.reserve(actual - self.lease.amount[0])
        except BaseException:
            self.lease.close()
            raise

    def check(self, token=None):
        from .content import ContentError
        if self.closed.is_set(): raise ContentError(reason=ContentError.CANCELLED)
        self.backend.check(token)

    def page(self, offset, size, category_id, token=None):
        from .content import MovieSearchPage, ContentError
        with self.lock, self.backend.session.lock:
            self.check(token)
            if self.generation is not self.backend.session.active:
                raise ContentError(reason=ContentError.CANCELLED)
            offset, size = max(0, int(offset)), max(1, min(280, int(size)))
            end = min(len(self.order), offset + size)
            items = []
            for position in self.order[offset:end]:
                self.check(token)
                item = self.backend.hydrate(self.generation.rows.record(position), category_id)
                if item is not None: items.append(item)
            result = MovieSearchPage(items, page_size=size, total_items=len(self.order), has_more=end < len(self.order), source='catalog')
            result.next_cursor = end
            result.catalog_state = self.backend.session.state()
            return result

    def position(self, identity, token=None):
        with self.lock, self.backend.session.lock:
            self.check(token)
            physical = self.generation.rows.by_id.get(self.generation.rows.identity(str(identity)))
            if physical is None: return 0
            for offset, position in enumerate(self.order):
                if offset % 1024 == 0: self.check(token)
                if position == physical: return offset
            return 0

    def close(self):
        self.closed.set()
        if self.lock.acquire(False):
            try:
                self.order = array('I');self.lease.close()
            finally: self.lock.release()


def sort_live_rows(entries, mode, token, lease):
    if mode == 'added' and not any(getattr(item, 'sort_added', 0) for item in entries):
        raise SortUnavailable(NO_DATES)
    temporary = Lease()
    try:
        temporary.reserve(len(entries) * 5 + 65536)
        order = array('I', range(len(entries)))
        def key(pos):
            item = entries[pos]
            return sort_key(mode, normalized_name(item.name), getattr(item, 'sort_added', 0), 0, pos)
        if mode not in ('provider', 'manual'): heap_order(order, key, token.check)
        return [entries[pos] for pos in order]
    finally: temporary.close()

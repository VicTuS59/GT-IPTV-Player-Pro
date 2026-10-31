# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Streaming addition-date summaries: one pair per category, no media index."""
import time
from contextlib import nullcontext

from .i18n import N_
from .content_sort import Lease, SortUnavailable, NO_DATES, SAVE_ERROR
from .category_preferences import arrange_categories, content_item_category_ids, MAX_CATEGORIES_PER_SOURCE

RECENT_LABEL = N_('Most recently added content')
RECENT_PROGRESS = N_('Preparing category dates: {}/{} ({} items)')
RECENT_INCOMPLETE = N_('Category dates could not be completed. Select this option again to resume.')
RECENCY_TTL = 15 * 60
WORK_TTL = 24 * 60 * 60


def visible_ids(categories, preferences):
    return [str(c.category_id) for c in arrange_categories(categories, preferences, sort_mode='provider') if c.category_id][:MAX_CATEGORIES_PER_SOURCE]


def fresh(pair, now, ttl=RECENCY_TTL):
    return bool(pair and 0 <= now - pair[1] < ttl)


def needs_refresh(categories, preferences, now=None):
    now = int(time.time()) if now is None else now
    return any(not fresh(preferences.recency.get(c), now) for c in visible_ids(categories, preferences))


def prepare_recency(client, kind, categories, store, key, token, progress, force=False):
    """Only a complete scan can replace the committed category date map."""
    if kind not in ('movie', 'series'):
        raise SortUnavailable(NO_DATES)
    provider = getattr(client, 'source_type', '')
    if provider not in ('xtream', 'stalker'):
        raise SortUnavailable(NO_DATES)
    lease = Lease()
    try:
        # Working/committed dicts, category IDs, JSON serialization and a
        # bounded provider page all debit the existing 192 MiB shared budget.
        lease.reserve(min(len(categories), MAX_CATEGORIES_PER_SOURCE) * 1024 + 16 * 1024 * 1024)
        preferences = store.load(key)
        ids = visible_ids(categories, preferences)
        if not ids:
            raise SortUnavailable(NO_DATES)
        now = int(time.time())
        source = {c: pair for c, pair in preferences.recency_work.items()
                  if fresh(pair, now, WORK_TTL)
                  and pair[1] > preferences.recency.get(c, (0, 0))[1]}
        work = ({c: source[c] for c in ids if c in source} if source else
                {c: preferences.recency[c] for c in ids
                 if not force and fresh(preferences.recency.get(c), now)})
        if len(work) == len(ids) and not any(pair[0] for pair in work.values()):
            # A previous provider response without dates must not prevent a
            # retry from discovering newly available dates for 24 hours.
            work = {}
        processed = 0
        progress(len(work), len(ids), processed)
        iterator_factory = getattr(client, 'iter_movie_catalog_items' if kind == 'movie' else 'iter_series_catalog_items')
        def scoped_iterator(category):
            factory = getattr(client, 'category_dates_scope', None)
            with factory() if callable(factory) else nullcontext():
                yield from iterator_factory(category)

        if len(work) < len(ids) and provider == 'xtream':
            # One incremental JSON stream, retaining no list or content IDs.
            newest = dict.fromkeys(ids, 0)
            iterator = iter(scoped_iterator(''))
            try:
                for item in iterator:
                    token.check()
                    processed += 1
                    if processed % 250 == 0:progress(0, len(ids), processed)
                    date = int(getattr(item, 'sort_added', 0) or 0)
                    if date:
                        for category in content_item_category_ids(item):
                            if category in newest:
                                newest[category] = max(newest[category], date)
            finally:
                closer = getattr(iterator, 'close', None)
                if callable(closer):closer()
            token.check()
            checked = int(time.time())
            work = {c: (date, checked) for c, date in newest.items()}
            progress(len(ids), len(ids), processed)
        elif len(work) < len(ids):
            # Portals differ in server-side date sorting. Traverse their
            # existing paced, bounded iterators and confirm each EOF.
            checkpoint = time.monotonic()
            completed = 0
            try:
                for category in ids:
                    token.check()
                    if category in work:continue
                    newest = 0
                    iterator = iter(scoped_iterator(category))
                    try:
                        for item in iterator:
                            token.check()
                            processed += 1
                            if processed % 250 == 0:progress(len(work), len(ids), processed)
                            newest = max(newest, int(getattr(item, 'sort_added', 0) or 0))
                    finally:
                        closer = getattr(iterator, 'close', None)
                        if callable(closer):closer()
                    token.check()
                    work[category] = (newest, int(time.time()))
                    completed += 1
                    progress(len(work), len(ids), processed)
                    if completed % 10 == 0 or time.monotonic() - checkpoint >= 5:
                        if not store.save_recency(key, work):raise SortUnavailable(SAVE_ERROR)
                        checkpoint = time.monotonic()
            except Exception:
                # Retain only EOF-confirmed categories, never the active page.
                if work:store.save_recency(key, work)
                raise
        token.check()
        if not any(pair[0] for pair in work.values()):
            raise SortUnavailable(NO_DATES)
        if not store.save_recency(key, work, activate=True, check=token.check):
            raise SortUnavailable(SAVE_ERROR)
        return store.load(key)
    finally:
        lease.close()

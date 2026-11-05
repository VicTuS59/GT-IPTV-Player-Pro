# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Reuse the screen's confirmed episode snapshot during cursor movement."""
from .episode_history import episode_watch_key


def watched_view(screen):
    records = screen._watched_records
    mutation = getattr(screen, '_episode_pending_watched', None)
    cached = getattr(screen, '_r27_watched_view', None)
    if cached is not None and cached[0] is records and cached[1] == mutation:
        return cached[2]
    view = records
    if mutation is not None:
        key, watched = mutation
        view = dict(records)
        if watched:
            view.setdefault(key, {'finished_at': 0})
        else:
            view.pop(key, None)
    screen._r27_watched_view = (records, mutation, view)
    return view


def season_completed(screen, records):
    cached = getattr(screen, '_r27_season_completed', None)
    if cached is not None and cached[0] is screen._entries and cached[1] is records:
        return cached[2]
    count = sum(episode_watch_key(screen.client, item) in records for item in screen._entries)
    screen._r27_season_completed = (screen._entries, records, count)
    return count

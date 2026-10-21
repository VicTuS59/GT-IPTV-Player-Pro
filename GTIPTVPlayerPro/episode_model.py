# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-independent season grouping and episode progress presentation."""

import math
import re

from .episode_history import episode_watch_key
from .playback import resume_key, should_save_resume


def _season_key(value):
    value = str(value if value is not None else "").strip()
    if value.isdecimal():
        # Providers sometimes mix integer seasons and zero-padded strings.
        return str(int(value))
    return value


def _season_sort_key(value):
    if not value:
        return (2, ())
    if value.isdecimal():
        return (0, ((0, int(value)),))
    parts = tuple(
        (0, int(part)) if part.isdecimal() else (1, part.casefold())
        for part in re.split(r"(\d+)", value) if part
    )
    return (1, parts)


def group_episode_seasons(entries):
    """Group seasons naturally while retaining provider order within them.

    An empty key is deliberately retained for portals that do not publish
    season information; the caller can localize its displayed caption.
    """
    groups = {}
    for item in entries or ():
        key = _season_key(getattr(item, "season", ""))
        groups.setdefault(key, []).append(item)
    return [(key, groups[key]) for key in sorted(groups, key=_season_sort_key)]


def episode_number(item, fallback):
    value = getattr(item, "episode", "")
    value = str(value if value is not None else "").strip()
    return value or str(fallback)


def episode_title(item, fallback):
    """Return plain one-line provider text without manufacturing a title."""
    value = getattr(item, "name", "")
    value = " ".join(str(value if value is not None else "").split())
    return value or str(fallback)


def _finite_number(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def format_episode_time(seconds):
    value = _finite_number(seconds)
    total = int(value) if value is not None and value > 0 else 0
    hours, remaining = divmod(total, 3600)
    minutes, seconds = divmod(remaining, 60)
    if hours:
        return "{}:{:02d}:{:02d}".format(hours, minutes, seconds)
    return "{:02d}:{:02d}".format(minutes, seconds)


def progress_percent(position, length):
    position, length = _finite_number(position), _finite_number(length)
    if position is None or length is None or position <= 0 or length <= 0:
        return 0
    if position >= length:
        return 100
    return max(0, min(100, int((position / length) * 100)))


def preferred_resume_index(entries, bookmarks, watched_keys, client):
    """Pick the newest valid unfinished bookmark belonging to this account.

    No bookmark means no preferred index: an unplayed series starts at its
    normal first episode. Completed entries never become the resume CTA.
    """
    if not isinstance(bookmarks, dict):
        return None
    watched_keys = watched_keys or ()
    selected, newest = None, None
    for index, item in enumerate(entries or ()):
        watched_key = episode_watch_key(client, item)
        if watched_key and watched_key in watched_keys:
            continue
        key = resume_key(client, item)
        record = bookmarks.get(key) if key else None
        if not isinstance(record, dict):
            continue
        try:
            resumable = should_save_resume(record.get("position"), record.get("length"))
        except (TypeError, ValueError, OverflowError):
            resumable = False
        if not resumable:
            continue
        updated_at = _finite_number(record.get("updated_at", 0))
        updated_at = updated_at if updated_at is not None else 0
        if newest is None or updated_at > newest:
            selected, newest = index, updated_at
    return selected

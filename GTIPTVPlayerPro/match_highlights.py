# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Completed-match search context, without network or playback operations."""
import datetime as dt

from .matches import LEAGUE_NAMES, text
from .matches_text import tr


def available(match):
    return bool(match is not None and match.category == 'finished'
                and match.phase not in ('Cancelled', 'Abandoned'))


def _shorten(value, limit):
    if len(value) <= limit:
        return value
    part = value[:limit]
    boundary = part.rfind(' ')
    return part[:boundary] if boundary >= limit // 2 else part


def search_query(match):
    """Keep both opponents and the device-local match date within 120 chars."""
    if not available(match):
        return ''
    day = dt.datetime.fromtimestamp(match.kickoff).strftime('%Y-%m-%d')
    league = text(LEAGUE_NAMES.get(match.league, ''), 28)
    keyword = text(tr('Match highlights'), 40)
    suffix = ' '.join((day, league, keyword)).strip()
    home, away = text(match.home, 100), text(match.away, 100)
    budget = 120 - len(suffix) - 2
    home_limit = min(len(home), budget // 2)
    away_limit = min(len(away), budget - home_limit)
    home_limit = min(len(home), budget - away_limit)
    return ' '.join((_shorten(home, home_limit), _shorten(away, away_limit), suffix))

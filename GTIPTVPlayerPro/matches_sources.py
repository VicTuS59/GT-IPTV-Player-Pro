# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Adapt Flashscore football results to the scores screen."""

import datetime as dt
import re
import time
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from .matches import (
    FLASH_DATE_LIMIT, FEED_EVENT_LIMIT, JSON_LIMIT, Budget, Cancelled, Match,
    ScoresError, Snapshot, _CLOCK, _SameHostRedirect, _local_day,
    read_score_body, text,
)


FLASH_URL = "https://www.flashscore.mobi/"
_MATCH_LINK = re.compile(r"^/match/([A-Za-z0-9]{8})(?:/|[?#]|$)")
_MATCH_SCORE = re.compile(r"^(\d{1,3})\s*[-:–]\s*(\d{1,3})(?!\d)")
_KICKOFF_TIME = re.compile(r"^([0-2]\d):([0-5]\d)\b")
_FLASH_CATEGORIES = {"sched": "upcoming", "live": "live", "fin": "finished"}

try:
    from zoneinfo import ZoneInfo
    _CENTRAL_EUROPE = ZoneInfo("Europe/Prague")
except (ImportError, OSError, KeyError):
    _CENTRAL_EUROPE = None


def _flash_zone(day, hour=12):
    if _CENTRAL_EUROPE is not None:
        return _CENTRAL_EUROPE
    # The mobile schedule uses Central European time. Some images omit tzdata.
    march = dt.date(day.year, 3, 31)
    october = dt.date(day.year, 10, 31)
    start = march - dt.timedelta(days=(march.weekday() + 1) % 7)
    end = october - dt.timedelta(days=(october.weekday() + 1) % 7)
    summer = (start, 2) <= (day, hour) < (end, 3)
    return dt.timezone(dt.timedelta(hours=2 if summer else 1))


def _status(category, description):
    value = description.casefold().replace("-", " ")
    for keyword, phase_category, phase in (
        ("postpon", "upcoming", "Postponed"), ("delay", "upcoming", "Postponed"),
        ("cancel", "finished", "Cancelled"), ("abandon", "finished", "Abandoned"),
        ("suspend", "live", "Suspended"), ("interrupt", "live", "Suspended"),
    ):
        if keyword in value:
            return phase_category, phase
    if category == "live":
        if value == "ht" or any(label in value for label in ("half time", "halftime", "break time")):
            return category, "Half-time"
        if "first half" in value or "1st half" in value:
            return category, "First half"
        if "second half" in value or "2nd half" in value:
            return category, "Second half"
        if "penalt" in value or "shootout" in value:
            return category, "Penalties"
        if "extra time" in value:
            return category, "Extra time"
    return category, {"upcoming": "Scheduled", "live": "Live", "finished": "Full time"}[category]


class _FlashscorePage(HTMLParser):
    def __init__(self, day, now, budget):
        super().__init__()
        self.day = day
        self.now = now
        self.budget = budget
        self.records = []
        self.complete = False
        self.seen = 0
        self._depth = 0
        self._heading = None
        self._league = ""
        self._inside_link = False
        self._reset_row()

    def _reset_row(self):
        self._time_depth = 0
        self._time = []
        self._teams = []
        self._score = []
        self._match_id = ""
        self._category = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag == "div" and (self._depth or attrs.get("id") == "score-data"):
            self._depth += 1
        if not self._depth:
            return
        if tag == "h4":
            self._finish_row()
            self._heading = []
        elif tag == "span" and self._heading is None and not self._inside_link:
            if self._time_depth or not self._time:
                self._time_depth += 1
        elif tag == "a":
            self._inside_link = True
            link = _MATCH_LINK.match(attrs.get("href") or "")
            if self._heading is None and link:
                self.seen += 1
                self.budget.check()
                if self.seen > FEED_EVENT_LIMIT:
                    raise ScoresError("Too many Flashscore matches")
                self._match_id = link.group(1)
                classes = (attrs.get("class") or "").split()
                self._category = next((_FLASH_CATEGORIES[key] for key in classes if key in _FLASH_CATEGORIES), None)
        elif tag == "br":
            self._finish_row()

    def handle_endtag(self, tag):
        if not self._depth:
            return
        if tag == "h4" and self._heading is not None:
            self._league = text("".join(self._heading))
            self._heading = None
        elif tag == "span" and self._time_depth:
            self._time_depth -= 1
        elif tag == "a":
            self._inside_link = False
        elif tag == "div":
            self._depth -= 1
            if not self._depth:
                self._finish_row()
                self.complete = True

    def handle_data(self, value):
        if not self._depth:
            return
        if self._inside_link:
            if self._match_id:
                self._score.append(value)
        elif self._heading is not None:
            self._heading.append(value)
        elif self._time_depth:
            self._time.append(value)
        else:
            self._teams.append(value)

    def _finish_row(self):
        if self._match_id and self._category and self._league:
            row = self._make_match()
            if row is not None:
                self.records.append((row, self._league))
        self._reset_row()

    def _make_match(self):
        teams = text("".join(self._teams), 225).split(" - ", 1)
        if len(teams) != 2 or not all(teams):
            return None
        timing = text(" ".join(self._time), 48)
        kickoff_time = _KICKOFF_TIME.match(timing)
        status = timing[kickoff_time.end():].strip() if kickoff_time else timing
        category, phase = _status(self._category, status)
        if kickoff_time:
            hour, minute = map(int, kickoff_time.groups())
            if hour > 23:
                return None
            kickoff = dt.datetime.combine(self.day, dt.time(hour, minute), _flash_zone(self.day, hour)).timestamp()
        elif category == "live":
            # The mobile live row displays the current minute in place of kickoff.
            kickoff = self.now
        elif phase == "Scheduled":
            return None
        else:
            kickoff = dt.datetime.combine(self.day, dt.time(12), _flash_zone(self.day)).timestamp()
        clock = ""
        if category == "live" and _CLOCK.fullmatch(status):
            clock = status.replace("'", "′").rstrip("′") + "′"
        score = _MATCH_SCORE.match(text("".join(self._score), 24))
        goals = (None, None)
        if score and category != "upcoming" and phase not in ("Cancelled", "Abandoned"):
            goals = tuple(map(int, score.groups()))
        return Match(self._match_id, "flashscore:" + self._league.casefold(), kickoff,
                     *(team[:100] for team in teams), "", "", *goals, None, None, category, phase, clock)


def _flash_day(opener, day, now, budget):
    utc_now = dt.datetime.fromtimestamp(now, dt.timezone.utc)
    site_today = utc_now.astimezone(_flash_zone(utc_now.date(), utc_now.hour + 1)).date()
    offset = (day - site_today).days
    if abs(offset) > FLASH_DATE_LIMIT:
        raise ScoresError("Date outside the Flashscore calendar")
    request = Request(FLASH_URL + "?d=" + str(offset), headers={
        "Accept": "text/html", "Accept-Encoding": "identity",
        "User-Agent": "GTIPTVPlayerPro/1.9.2 (scores)",
    })
    with opener(request, timeout=budget.timeout()) as response:
        body = read_score_body(response, JSON_LIMIT, budget)
    page = _FlashscorePage(day, now, budget)
    page.feed(body.decode("utf-8-sig"))
    page.close()
    if not page.complete or (page.seen and not page.records):
        raise ScoresError("Unrecognised Flashscore results")
    return page.records


def fetch_flashscore_day(day, cancel=None, opener=None, clock=time.time):
    if type(day) is not dt.date:
        raise ValueError("Expected date")
    budget = Budget(cancel, seconds=12)
    opener = opener or build_opener(_SameHostRedirect()).open
    now = clock()
    zone = _flash_zone(day)
    first = dt.datetime.combine(day, dt.time.min).astimezone(zone).date()
    last = dt.datetime.combine(day, dt.time.max).astimezone(zone).date()
    dates = [first + dt.timedelta(days=offset) for offset in range((last - first).days + 1)]
    dates.sort(key=lambda value: abs((value - day).days))
    failed, matches, leagues = [], {}, {}
    updated_at = None
    for feed_day in dates:
        try:
            rows = _flash_day(opener, feed_day, now, budget)
        except Cancelled:
            raise
        except HTTPError as error:
            failed.append(feed_day.isoformat())
            error.close()
            if error.code in (401, 403, 429):
                break
            continue
        except (OSError, ValueError, ScoresError):
            failed.append(feed_day.isoformat())
            continue
        updated_at = now
        for match, league in rows:
            if _local_day(match.kickoff) == day:
                matches[match.id] = match
                leagues[match.league] = league
    if budget.cancel.is_set():
        raise Cancelled()
    ordered = tuple(sorted(matches.values(), key=lambda m: (m.kickoff, m.league, m.id)))
    options = tuple(sorted(leagues.items(), key=lambda pair: (pair[1].casefold(), pair[0])))
    return Snapshot(day, ordered, updated_at, tuple(failed), False, options)

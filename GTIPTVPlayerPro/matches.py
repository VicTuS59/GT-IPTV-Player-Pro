# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account-independent ESPN scores. No service, player or buffer operations."""
import datetime as dt
import json
import os
import re
import shutil
import struct
import sys
import tempfile
import threading
import time
from collections import OrderedDict, namedtuple
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .matches_stream import ScoreboardStream


LEAGUES = (
    ("tur.1", "Süper Lig"), ("eng.1", "Premier League"),
    ("ger.1", "Bundesliga"), ("ita.1", "Serie A"),
    ("esp.1", "LaLiga"), ("fra.1", "Ligue 1"),
    ("ned.1", "Eredivisie"), ("por.1", "Primeira Liga"),
    ("uefa.champions", "UEFA Champions League"),
    ("uefa.europa", "UEFA Europa League"),
    ("uefa.europa.conf", "UEFA Conference League"),
)
# A bounded build-time ESPN ID/name catalogue labels the daily feed. It does
# not restrict which competitions may be displayed; unknown IDs remain usable.
LEAGUE_NAMES = dict(LEAGUES)
LEAGUE_IDS = {}
try:
    with open(os.path.join(os.path.dirname(__file__), "matches_leagues.json"), "rb") as source:
        catalog = json.loads(source.read(128 * 1024 + 1).decode("utf-8"))
    if not isinstance(catalog, list) or len(catalog) > 256:raise ValueError("Invalid catalogue")
    for row in catalog:
        identifier, slug, name = row["id"], row["slug"], row["name"]
        if (not re.fullmatch(r"[0-9]{1,12}", identifier)
                or not re.fullmatch(r"[a-z0-9._-]{1,80}", slug)
                or not isinstance(name, str) or not 0 < len(name) <= 120):
            raise ValueError("Invalid competition")
        LEAGUE_IDS[identifier] = slug
        LEAGUE_NAMES[slug] = name
except (OSError, ValueError, KeyError, TypeError):
    pass
finally:
    # Do not retain the decoded build catalogue alongside the lookup maps.
    for _variable in ("catalog", "row", "identifier", "slug", "name"):
        globals().pop(_variable, None)

BASE_URL = "https://site.api.espn.com/apis/site/v2/sports/soccer/"
JSON_LIMIT = 2 * 1024 * 1024
LOGO_LIMIT = 512 * 1024
REFRESH_SECONDS = 60
JOB_SECONDS = 40
FEED_EVENT_LIMIT = 1000
CACHE_ENTRY_LIMIT = 99
CACHE_MATCH_LIMIT = 3300
CACHE_BYTES_LIMIT = 2 * JSON_LIMIT
_ID = re.compile(r"^[0-9]{1,12}$")
_CLOCK = re.compile(r"^[0-9]{1,3}(?:['′]?(?:\+[0-9]{1,2}['′]?)?)?$")


class ScoresError(Exception):
    pass


class Cancelled(ScoresError):
    pass


def text(value, limit=120):
    return " ".join("".join(c if c.isprintable() else " " for c in str(value or "")[:512]).split())[:limit]


def _number(value):
    try:
        if isinstance(value, bool):return None
        number = int(value)
        return number if 0 <= number <= 999 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _score(value):
    if isinstance(value, dict):value = value.get("value", value.get("displayValue"))
    return _number(value)


def _first_half(team):
    lines = team.get("linescores")
    if not isinstance(lines, list):return None
    for line in lines:
        if isinstance(line, dict) and line.get("period") == 1:
            return _score(line)
    return None


def _timestamp(value):
    try:
        parsed = dt.datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None or not 1900 <= parsed.year <= 2100:return None
        return parsed.timestamp()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


class Match(namedtuple("Match", (
        "id", "league", "kickoff", "home", "away", "home_id", "away_id",
        "home_score", "away_score", "half_home", "half_away", "category", "phase", "clock"))):
    # Immutable records without a per-instance dictionary or optional module.
    __slots__ = ()

    @property
    def score(self):
        if self.home_score is None or self.away_score is None:return "—"
        return "{} – {}".format(self.home_score, self.away_score)

    @property
    def half_score(self):
        if self.half_home is None or self.half_away is None:return "—"
        return "{} – {}".format(self.half_home, self.half_away)


def event_league(event):
    uid = event.get("uid")
    if uid:
        match = re.fullmatch(r"s:600~l:([0-9]{1,12})~e:([0-9]{1,12})", str(uid))
        if not match or match.group(2) != str(event.get("id", "")):return None
        return LEAGUE_IDS.get(match.group(1), "espn:" + match.group(1))
    # Missing metadata must not silently remove an otherwise valid match.
    return "espn:unknown"


def leagues_for_matches(matches):
    return tuple(sorted(((key, LEAGUE_NAMES.get(key, "")) for key in {m.league for m in matches}),
                        key=lambda pair: ((pair[1] or pair[0]).casefold(), pair[0])))


def _local_day(timestamp):
    return dt.datetime.fromtimestamp(timestamp).date()


def parse_match(event, league=None):
    if not isinstance(event, dict):return None
    league = league or event_league(event)
    if not league:return None
    event_id = str(event.get("id", ""))
    if not _ID.fullmatch(event_id):return None
    competitions = event.get("competitions")
    if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict):return None
    game = competitions[0]
    kickoff = _timestamp(game.get("date") or event.get("date"))
    if kickoff is None:return None
    teams = game.get("competitors")
    if not isinstance(teams, list):return None
    pair = {c.get("homeAway"):c for c in teams if isinstance(c, dict)}
    if "home" not in pair or "away" not in pair:return None
    home, away = pair["home"], pair["away"]
    ht, at = home.get("team"), away.get("team")
    if not isinstance(ht, dict) or not isinstance(at, dict):return None
    names = [text(t.get("displayName") or t.get("name"), 100) for t in (ht, at)]
    if not all(names):return None
    ids = [str(t.get("id", "")) for t in (ht, at)]
    ids = [v if _ID.fullmatch(v) else "" for v in ids]
    status = game.get("status") or event.get("status") or {}
    if not isinstance(status, dict):status = {}
    kind = status.get("type") or {}
    if not isinstance(kind, dict):kind = {}
    name, state = str(kind.get("name", "")), kind.get("state")
    category = {"pre":"upcoming", "in":"live", "post":"finished"}.get(state, "upcoming")
    if kind.get("completed") is True:category = "finished"
    phases = (("POSTPON", "Postponed"), ("CANCEL", "Cancelled"),
              ("ABANDON", "Abandoned"), ("SUSPEND", "Suspended"),
              ("HALFTIME", "Half-time"), ("HALF_TIME", "Half-time"),
              ("SHOOTOUT", "Penalties"), ("PENALT", "Penalties"),
              ("EXTRA", "Extra time"), ("OVERTIME", "Extra time"))
    phase = next((label for token,label in phases if token in name), "")
    if phase == "Postponed":category = "upcoming"
    if phase in ("Cancelled", "Abandoned"):category = "finished"
    if phase == "Suspended":category = "live"
    if not phase:
        if category == "finished":phase = "Full time" if kind.get("completed") else "Finished"
        elif category == "upcoming":phase = "Scheduled"
        else:phase = {1:"First half", 2:"Second half", 3:"Extra time", 4:"Extra time", 5:"Penalties"}.get(status.get("period"), "Live")
    clock = text(status.get("displayClock"), 16)
    if not _CLOCK.fullmatch(clock):clock = ""
    clock = clock.replace("'", "′")
    if clock and not clock.endswith("′"):clock += "′"
    scores = [_score(c.get("score")) for c in (home, away)]
    if category == "upcoming" or phase in ("Cancelled", "Abandoned"):
        scores = [None, None]
    return Match(event_id, league, kickoff, *names, *ids, *scores,
                 _first_half(home), _first_half(away), category, phase, clock)


class _SameHostRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        before, after = urlsplit(req.full_url), urlsplit(newurl)
        if (after.scheme != "https" or after.netloc != before.netloc
                or after.username or after.password):
            raise ScoresError("Unexpected redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class Budget:
    def __init__(self, cancel=None, seconds=JOB_SECONDS):
        self.cancel = cancel or threading.Event()
        self.deadline = time.monotonic() + seconds

    def check(self):
        if self.cancel.is_set():raise Cancelled()
        if time.monotonic() >= self.deadline:raise ScoresError("Request deadline")

    def timeout(self):
        self.check()
        return max(0.1, min(4.0, self.deadline - time.monotonic()))


class Snapshot(namedtuple("Snapshot", (
        "day", "matches", "updated_at", "failed_leagues", "cached", "leagues"), defaults=((),))):
    __slots__ = ()


class ESPNClient:
    def __init__(self, opener=None, clock=time.time):
        self._opener = opener or build_opener(_SameHostRedirect()).open
        self._clock = clock
        self._cache = OrderedDict()
        self._lock = threading.Lock()
        self._cache_sizes = {}
        self._cache_bytes = 0
        self._cache_matches = 0
        self._tv_bytes = 0

    def _read(self, url, limit, budget):
        request = Request(url, headers={"Accept":"application/json,image/png", "Accept-Encoding":"identity",
                                        "User-Agent":"GTIPTVPlayerPro/1.7.0 (scores)"})
        with self._opener(request, timeout=budget.timeout()) as response:
            body = bytearray()
            while True:
                budget.check()
                chunk = response.read(min(65536, limit + 1 - len(body)))
                if not chunk:break
                body.extend(chunk)
                if len(body) > limit:raise ScoresError("Response too large")
        budget.check()
        return bytes(body)

    @staticmethod
    def _entry_bytes(value):
        # Conservative: shared strings are counted again for every record.
        return (512 + sys.getsizeof(value) + sys.getsizeof(value[0]) + sys.getsizeof(value[1])
                + sum(sys.getsizeof(m) + sum(sys.getsizeof(v) for v in m) for m in value[1]))

    def _remember(self, key, value):
        size = self._entry_bytes(value)
        count = len(value[1])
        with self._lock:
            if key in self._cache:
                prior = self._cache.pop(key)
                self._cache_bytes -= self._cache_sizes.pop(key)
                self._cache_matches -= len(prior[1])
            if size > CACHE_BYTES_LIMIT or count > CACHE_MATCH_LIMIT:return
            self._cache[key] = value
            self._cache_sizes[key] = size
            self._cache_bytes += size
            self._cache_matches += count
            while (len(self._cache) > CACHE_ENTRY_LIMIT or self._cache_bytes + self._tv_bytes > CACHE_BYTES_LIMIT
                   or self._cache_matches > CACHE_MATCH_LIMIT):
                old_key, old = self._cache.popitem(last=False)
                self._cache_bytes -= self._cache_sizes.pop(old_key)
                self._cache_matches -= len(old[1])

    def _reserve_tv(self, size):
        with self._lock:
            self._tv_bytes = max(0, min(512 * 1024, int(size)))
            while self._cache and self._cache_bytes + self._tv_bytes > CACHE_BYTES_LIMIT:
                key, value = self._cache.popitem(last=False)
                self._cache_bytes -= self._cache_sizes.pop(key)
                self._cache_matches -= len(value[1])

    def _day(self, day, budget):
        with self._lock:
            cached = self._cache.get(day)
            if cached:self._cache.move_to_end(day)
        if cached and 0 <= self._clock() - cached[0] < REFRESH_SECONDS:
            return cached, False, False
        records = {}
        try:
            url = BASE_URL + "all/scoreboard?" + urlencode({"dates":day.strftime("%Y%m%d"), "limit":FEED_EVENT_LIMIT})
            request = Request(url, headers={"Accept":"application/json", "Accept-Encoding":"identity",
                                            "User-Agent":"GTIPTVPlayerPro/1.7.0 (scores)"})
            with self._opener(request, timeout=budget.timeout()) as response:
                stream = ScoreboardStream(response, budget, FEED_EVENT_LIMIT)
                for event in stream:
                    match = parse_match(event)
                    if match:records[match.id] = match
                if stream.events and not records:raise ScoresError("Unrecognised matches")
                # The public feed has no working offset pagination. A full page
                # is explicitly partial rather than silently claiming completeness.
                if stream.events >= FEED_EVENT_LIMIT:raise ScoresError("Scoreboard result cap")
            value = (self._clock(), tuple(records.values()))
            self._remember(day, value)
            return value, False, False
        except Cancelled:raise
        except Exception:
            if budget.cancel.is_set():raise Cancelled()
            # Keep a successfully parsed prefix when a later read fails. Never
            # replace a complete cached response with an incomplete prefix.
            if records:
                merged = dict(records)
                for match in cached[1] if cached else ():
                    if len(merged) >= FEED_EVENT_LIMIT:break
                    merged.setdefault(match.id, match)
                return (self._clock(), tuple(merged.values())), True, bool(cached)
            return cached, True, cached is not None

    def fetch_day(self, day, cancel=None, live_only=False):
        if not isinstance(day, dt.date) or isinstance(day, dt.datetime):raise ValueError("Expected date")
        budget = Budget(cancel)
        days = [day + dt.timedelta(days=offset) for offset in (0, -1, 1)]
        # Three daily unified feeds cover timezone boundaries for every tab.
        # Two bounded workers and the previous 40-second job budget are retained.
        records={};times=[];failed=set();cached=False
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="gt-scores") as pool:
            futures={pool.submit(self._day, source_day, budget):source_day for source_day in days}
            try:
                for future in as_completed(futures):
                    source_day = futures[future]
                    try:entry, error, reused = future.result()
                    except Cancelled:raise
                    except Exception:
                        failed.add(source_day.isoformat())
                        continue
                    if error:failed.add(source_day.isoformat())
                    cached = cached or reused
                    if entry:
                        times.append(entry[0])
                        for match in entry[1]:
                            include = (_local_day(match.kickoff) == day)
                            if live_only:
                                include = (match.category == 'live' and -3600 <= self._clock()-match.kickoff <= 24*3600
                                           and 0 <= self._clock()-entry[0] <= REFRESH_SECONDS)
                            if include:
                                prior = records.get(match.id)
                                if prior is None or entry[0] >= prior[0]:records[match.id] = (entry[0], match)
            finally:
                if budget.cancel.is_set():
                    for future in futures:future.cancel()
        if budget.cancel.is_set():raise Cancelled()
        matches=tuple(sorted((item[1] for item in records.values()), key=lambda m:(m.kickoff, m.league, m.id)))
        return Snapshot(day, matches, min(times) if times else None, tuple(sorted(failed)), cached,
                        leagues_for_matches(matches))

    def fetch_broadcasts(self, cancel=None):
        from .liveonsat import BroadcastSnapshot, read_listings
        budget = Budget(cancel)
        live = self.fetch_day(dt.date.today(),cancel,live_only=True)
        budget.check()
        if not live.matches:
            self._reserve_tv(0)
            return BroadcastSnapshot((),live.updated_at,bool(live.failed_leagues),bool(live.failed_leagues))
        try:
            records,size,partial = read_listings(self._opener,live.matches,budget)
            self._reserve_tv(size)
            return BroadcastSnapshot(records,self._clock(),False,partial or bool(live.failed_leagues))
        except Cancelled:raise
        except Exception:
            if budget.cancel.is_set():raise Cancelled()
            self._reserve_tv(0)
            return BroadcastSnapshot((),None,True,False)

    def logo(self, team_id, directory, budget):
        if not _ID.fullmatch(str(team_id)):return None
        path=os.path.join(directory, str(team_id)+".png")
        if os.path.isfile(path):return path
        if len(os.listdir(directory)) >= 32:return None
        try:
            body=self._read("https://a.espncdn.com/i/teamlogos/soccer/500/"+str(team_id)+".png", LOGO_LIMIT, budget)
            if len(body)<24 or body[:8]!=b"\x89PNG\r\n\x1a\n" or body[12:16]!=b"IHDR":return None
            width,height=struct.unpack(">II",body[16:24])
            if not (0<width<=1024 and 0<height<=1024):return None
            with open(path,"xb") as output:output.write(body)
            return path
        except Exception:return None


class MatchesLoader:
    """One screen worker, latest request wins; never touches native widgets."""
    def __init__(self, client=None):
        self.client=client or ESPNClient()
        self._condition=threading.Condition()
        self._pending=None;self._result=None;self._active=None
        self._closed=False;self._generation=0;self._thread=None
        self._temp_root=os.path.realpath(tempfile.gettempdir())
        self._directory=tempfile.mkdtemp(prefix="gtiptv-matches-",dir=self._temp_root)

    def request(self, kind, value):
        with self._condition:
            if self._closed:return None
            self._generation+=1
            if self._active:self._active.set()
            self._pending=(self._generation, kind, value)
            self._result=None
            if self._thread is None:
                self._thread=threading.Thread(target=self._work,name="gt-match-page",daemon=True)
                self._thread.start()
            self._condition.notify()
            return self._generation

    def poll(self):
        with self._condition:
            result,self._result=self._result,None
            return result

    def close(self):
        with self._condition:
            if self._closed:return
            self._closed=True;self._pending=None;self._result=None
            if self._active:self._active.set()
            self._condition.notify()
        if self._thread is None:self._cleanup()

    def _cleanup(self):
        root=self._temp_root;target=os.path.realpath(self._directory)
        if os.path.dirname(target)==root and os.path.basename(target).startswith("gtiptv-matches-"):
            shutil.rmtree(target,ignore_errors=True)

    def _work(self):
        try:
            while True:
                with self._condition:
                    while not self._closed and self._pending is None:self._condition.wait()
                    if self._closed:return
                    generation,kind,value=self._pending;self._pending=None
                    cancel=threading.Event();self._active=cancel
                try:
                    if kind=="scores":payload=self.client.fetch_day(value,cancel)
                    elif kind=="broadcasts":payload=self.client.fetch_broadcasts(cancel)
                    else:
                        budget=Budget(cancel,seconds=10)
                        payload=tuple(self.client.logo(team,self._directory,budget) for team in value)
                    error=False
                except Cancelled:continue
                except Exception:payload=None;error=True
                with self._condition:
                    if not self._closed and generation==self._generation:
                        self._result=(generation,kind,payload,error)
        finally:self._cleanup()

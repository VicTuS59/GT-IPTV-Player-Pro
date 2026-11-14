# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Find an official UEFA highlight for an already selected finished fixture."""

import datetime as dt
import json
import re
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError
from difflib import SequenceMatcher
from html.parser import HTMLParser
from urllib.error import HTTPError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .match_highlights import available, fold, search_team_name
from .match_highlights_filter import (
    SCORE_PAIR, alias_positions, aliases, seasons, written_dates,
)
from .match_highlights_languages import international_team, local_country_name
from .matches import Budget, ScoresError, _SameHostRedirect, read_score_body, text


_COMPETITIONS = (
    (("uefa nations league",), "2014", "uefanationsleague"),
    (("champions league women", "uefa champions league women", "uefa women s champions league",
      "womens champions league", "women s champions league"), "28", "womenschampionsleague"),
    (("uefa champions league", "champions league"), "1", "uefachampionsleague"),
    (("uefa europa league", "europa league"), "14", "uefaeuropaleague"),
    (("uefa conference league", "uefa europa conference league", "conference league"),
     "2019", "uefaconferenceleague"),
)
_ESPN_COMPETITIONS = dict(zip(
    ("uefa.nations", "uefa.wchampions", "uefa.champions", "uefa.europa", "uefa.europa.conf"),
    _COMPETITIONS,
))
_CATALOGUES = (("www.uefa.com", "en"), ("fr.uefa.com", "fr"), ("es.uefa.com", "es"))
_HIGHLIGHT_TYPES = frozenset(("highlights", "medium highlights"))
_VIDEO_ID = re.compile(r"[0-9a-f]{4}-[0-9a-f]{12}-[0-9a-f]{12}-[0-9]{4}")
_SEASON = re.compile(r"\bwindow\.currentSeason\s*=\s*['\"]?(20[0-9]{2})\b")
_SQUAD = re.compile(r"\b(?:u\s*[0-9]{2}|reserves?|ii|b|women|w)\b$")
_LOOKUP_BUDGET = 7
_CATALOGUE_LIMIT = 680 * 1024
_MATCH_LIMIT = 1450 * 1024
_CACHE_SECONDS = 180
_CACHE_LIMIT = 12
_CACHE = OrderedDict()
_CACHE_LOCK = threading.Lock()

_ASPOR = "https://www.aspor.com.tr"
_ASPOR_PATH = re.compile(r"/video/uluslar-ligi/[a-z0-9-]{1,140}")
_ASPOR_MEDIA = "lqqqrdujxm.erbvr.com"
_ASPOR_CDN = "lqqqrdujxm.fdc.ercdn.net"
_ASPOR_HIGHLIGHT = re.compile(r"\bmac (?:ozeti|sonucu ozet)$")
_ASPOR_LIMIT = 512 * 1024

# Logistic model fitted to paired UEFA fixture team names, alias examples and
# confusable opponents. Exact, character similarity, token overlap and coverage.
_WEIGHTS = (-4.48334, 2.50411, 2.73943, 1.80773, 2.56189)
_MATCH_THRESHOLD = 1.0
_CLOSE_MATCH_MARGIN = 0.8

_NATIONAL_ALIASES = {
    "czech republic": "czechia", "czechia": "czechia",
    "moldavia": "moldova", "moldova": "moldova",
    "ireland republic": "republic of ireland", "ireland": "republic of ireland",
    "north macedonia": "north macedonia", "macedonia": "north macedonia",
    "turkey": "turkiye", "turkiye": "turkiye",
}


def _competition(league):
    espn = _ESPN_COMPETITIONS.get(league)
    if espn:
        return espn[1:]
    if not league.startswith("flashscore:europe:"):
        return None
    name = fold(league.split(":", 2)[-1])
    for names, identifier, path in _COMPETITIONS:
        if any(name == alias or name.startswith(alias + " ") for alias in names):
            return identifier, path
    return None


def eligible(match):
    return available(match) and None not in (match.home_score, match.away_score) and bool(_competition(match.league))


def _video_url(value, folder, identifier, host):
    if not isinstance(value, str) or len(value) > 260 or not _VIDEO_ID.fullmatch(identifier):
        return ""
    parts = urlsplit(value)
    if (parts.netloc and (parts.scheme != "https" or parts.netloc != host)
            or parts.scheme and parts.scheme != "https" or parts.query or parts.fragment):
        return ""
    if not re.fullmatch(rf"/{folder}/video/(?:highlights/)?{identifier}(?:--[a-z0-9-]+)?/", parts.path):
        return ""
    return "https://" + host + parts.path


class _Catalogue(HTMLParser):
    """Read only UEFA's highlight cards, including their publication times."""

    def __init__(self, folder, host, language):
        super().__init__(convert_charrefs=True)
        self.folder, self.host, self.language = folder, host, language
        self.cards = []
        self.card = None
        self.divs = 0

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        identifier = attrs.get("data-videoid") or ""
        if tag == "div" and _VIDEO_ID.fullmatch(identifier):
            self.card = {"id": identifier, "language": self.language}
            self.divs = 1
            return
        if self.card is None:
            return
        if tag == "div":
            self.divs += 1
            tracking = attrs.get("data-tracking") or ""
            if len(tracking) <= 1024 and '"video card"' in tracking:
                try:
                    item = json.loads(tracking)
                except ValueError:
                    return
                if (isinstance(item, dict) and item.get("id") == self.card["id"]
                        and str(item.get("variant")).casefold() in _HIGHLIGHT_TYPES):
                    self.card["highlight"] = True
                    self.card["title"] = text(item.get("name"), 180)
        elif tag == "a" and not self.card.get("url"):
            url = _video_url(attrs.get("href"), self.folder, self.card["id"], self.host)
            if url:
                self.card["url"] = url
                self.card["title"] = self.card.get("title") or text(attrs.get("title"), 180)
        elif tag == "span" and attrs.get("data-plugin") == "timeago":
            opts = attrs.get("data-options") or "{}"
            if len(opts) <= 512:
                try:
                    data = json.loads(opts)
                except ValueError:
                    return
                if isinstance(data, dict):
                    self.card["published"] = _timestamp(data.get("targetDate"))

    def handle_endtag(self, tag):
        if tag != "div" or self.card is None:
            return
        self.divs -= 1
        if not self.divs:
            if self.card.get("highlight") and self.card.get("url") and self.card.get("published"):
                self.cards.append(self.card)
            self.card = None


def _read(url, budget, limit, accept, opener):
    headers = {"User-Agent": "GTIPTVPlayerPro/1.9.3 (scores)", "Accept": accept,
               "Accept-Encoding": "identity"}
    with opener(Request(url, headers=headers), timeout=budget.timeout()) as response:
        if response.geturl() != url:
            raise ScoresError("Unexpected UEFA redirect")
        return read_score_body(response, limit, budget).decode("utf-8-sig")


def _load_catalogue(folder, host, language, budget, opener):
    url = f"https://{host}/{folder}/"
    now = time.monotonic()
    with _CACHE_LOCK:
        cached = _CACHE.get(url)
        if cached and now - cached[0] < _CACHE_SECONDS:
            _CACHE.move_to_end(url)
            return cached[1]
    html = _read(url, budget, _CATALOGUE_LIMIT, "text/html", opener)
    season = _SEASON.search(html)
    if season is None:
        return "", ()
    parser = _Catalogue(folder, host, language)
    parser.feed(html)
    parser.close()
    result = (season[1], tuple(parser.cards))
    if result[1]:
        with _CACHE_LOCK:
            _CACHE[url] = time.monotonic(), result
            _CACHE.move_to_end(url)
            while len(_CACHE) > _CACHE_LIMIT:
                _CACHE.popitem(last=False)
    return result


def _timestamp(value):
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        date = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.timestamp() if date.tzinfo is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _team_names(team, locale="EN"):
    translations = team.get("translations") or {}
    return {
        name for name in (
            team.get("internationalName"), translations.get("displayName", {}).get(locale),
            translations.get("displayOfficialName", {}).get(locale),
        )
        if isinstance(name, str) and 1 < len(name) <= 120
    }


def _normalize(name):
    value = fold(name)
    return _NATIONAL_ALIASES.get(value, value)


def _score_name(left, right):
    if left == right:
        return sum(_WEIGHTS)
    a, b = set(left.split()), set(right.split())
    shared = len(a & b)
    similarity = SequenceMatcher(None, left.replace(" ", ""), right.replace(" ", ""), autojunk=False).ratio()
    evidence = (1., 0., similarity, shared / max(len(a), len(b)), shared / min(len(a), len(b)))
    return sum(weight * value for weight, value in zip(_WEIGHTS, evidence))


def _fixture_team_score(team, source_name, match):
    if team.get("typeIsNational"):
        _, source_label, suffix = international_team(source_name)
        # An explicit country and squad identifier carry more weight than spelling.
        if suffix:
            return float("-inf")
    else:
        source_label = source_name
        if _competition(match.league)[0] == "28":
            source_label = re.sub(r"\s+(?:w|women)$", "", source_label, flags=re.I)
        if _SQUAD.search(fold(source_label)):
            return float("-inf")
    inputs = {_normalize(source_label), _normalize(search_team_name(source_label, match.league))}
    names = {_normalize(name) for name in _team_names(team)}
    return max((_score_name(left, right) for left in inputs for right in names if left and right),
               default=float("-inf"))


def _select_fixture(records, match, identifier):
    matches = []
    for row in records:
        if not isinstance(row, dict) or row.get("status") != "FINISHED":
            continue
        competition = row.get("competition", {})
        if competition.get("id") != identifier:
            continue
        if competition.get("sex") != ("FEMALE" if identifier == "28" else "MALE"):
            continue
        season = row.get("seasonYear", "")
        if not isinstance(season, str) or not re.fullmatch(r"20[0-9]{2}", season):
            continue
        timing = row.get("kickOffTime", {})
        kickoff = _timestamp(timing.get("dateTime"))
        if kickoff is None or abs(kickoff - match.kickoff) > 60:
            continue
        score = row.get("score", {}).get("total", {})
        if (score.get("home"), score.get("away")) != (match.home_score, match.away_score):
            continue
        match_id = row.get("id")
        if not isinstance(match_id, str) or not re.fullmatch(r"[0-9]{1,10}", match_id):
            continue
        sides = (row.get("homeTeam", {}), row.get("awayTeam", {}))
        points = [_fixture_team_score(team, name, match)
                  for team, name in zip(sides, (match.home, match.away))]
        if min(points) >= _MATCH_THRESHOLD:
            matches.append((sum(points), row))
    matches.sort(key=lambda item: item[0], reverse=True)
    if not matches or len(matches) > 1 and matches[0][0] - matches[1][0] < _CLOSE_MATCH_MARGIN:
        return None
    return matches[0][1]


def _video_names(team, language):
    names = set()
    for name in _team_names(team) | _team_names(team, language.upper()):
        names.update(aliases(name))
        if team.get("typeIsNational"):
            country, _, suffix = international_team(name)
            if country and suffix == "":
                names.update(aliases(local_country_name(country, language)))
    return names


def _title_matches(title, home, away, score, day):
    date_labels = written_dates(title)
    if date_labels and date_labels != {day}:
        return False
    positions = alias_positions(title, home, away)
    scores = {tuple(map(int, pair)) for pair in SCORE_PAIR.findall(title)}
    return bool(positions and scores == {score if positions[0] < positions[1] else score[::-1]})


def _select_video(catalogues, fixture):
    timing = fixture["kickOffTime"]
    day = dt.date.fromisoformat(timing["date"])
    kickoff = _timestamp(timing["dateTime"])
    ended = _timestamp(fixture.get("fullTimeAt")) or kickoff + 90 * 60
    offset = dt.timezone(dt.timedelta(hours=timing["utcOffsetInHours"]))
    score = fixture["score"]["total"]
    final_score = (score["home"], score["away"])
    team_aliases = {}
    for season, cards in catalogues:
        if season != fixture["seasonYear"]:
            continue
        for card in cards:
            publication = card["published"]
            release_day = dt.datetime.fromtimestamp(publication, offset).date()
            if (publication < ended or publication > kickoff + 36 * 3600
                    or not day <= release_day <= day + dt.timedelta(days=1)):
                continue
            language = card["language"]
            if language not in team_aliases:
                team_aliases[language] = (_video_names(fixture["homeTeam"], language),
                                          _video_names(fixture["awayTeam"], language))
            home_names, away_names = team_aliases[language]
            title = card["title"]
            if title and (written_dates(title) or SCORE_PAIR.search(title)):
                if _title_matches(title, home_names, away_names, final_score, day):
                    return card["url"]
            elif "--" in card["url"]:
                # Editorial captions sometimes omit the teams and score.
                # The UEFA video path still carries the full fixture caption.
                title = urlsplit(card["url"]).path.rstrip("/").split("--", 1)[-1]
                title = re.sub(r"(?<!\d)(\d{1,2})-(\d{1,2})(?!\d)", r" \1:\2 ", title)
                title = title.replace("-", " ")
                if _title_matches(title, home_names, away_names, final_score, day):
                    return card["url"]
    return ""


def _verified_uefa_highlight(match, cancel, opener):
    """Find the UEFA fixture and official video with matching season, date and score."""
    if not eligible(match):
        return None
    identifier, folder = _competition(match.league)
    day = dt.datetime.fromtimestamp(match.kickoff, dt.timezone.utc).date()
    parameters = urlencode({"competitionId": identifier,
                            "fromDate": day - dt.timedelta(days=1),
                            "toDate": day + dt.timedelta(days=1), "offset": 0, "limit": 36})
    match_url = "https://match.uefa.com/v5/matches?" + parameters
    budget = Budget(cancel, seconds=_LOOKUP_BUDGET)
    pool = ThreadPoolExecutor(max_workers=3, thread_name_prefix="GT-UEFA")
    futures, results = {}, {}
    try:
        for index, (host, language) in enumerate(_CATALOGUES):
            futures[pool.submit(_load_catalogue, folder, host, language, budget, opener)] = index
        rows = json.loads(_read(match_url, budget, _MATCH_LIMIT, "application/json", opener))
        if not isinstance(rows, list):
            return None
        fixture = _select_fixture(rows, match, identifier)
        if fixture is None:
            return None
        for future in as_completed(futures, timeout=max(0.01, budget.deadline - time.monotonic())):
            budget.check()
            try:
                index = futures[future]
                catalogue = future.result()
            except HTTPError as error:
                error.close()
                continue
            except (ScoresError, OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError):
                continue
            url = _select_video((catalogue,), fixture)
            if url:
                results[index] = url
                if index == 0:
                    break
    except HTTPError as error:
        error.close()
    except (ScoresError, TimeoutError, OSError, ValueError, TypeError, KeyError, AttributeError, RuntimeError):
        pass
    finally:
        for future in futures:
            future.cancel()
        pool.shutdown(wait=False)
    return fixture if results else None


class _ASporPage(HTMLParser):
    """Read A Spor's own video URLs and published article metadata."""

    def __init__(self):
        super().__init__()
        self.links = []
        self.meta = {}
        self.videos = []
        self._json = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "a":
            path, title = attrs.get("href") or "", attrs.get("aria-label") or ""
            if _ASPOR_PATH.fullmatch(path) and _ASPOR_HIGHLIGHT.search(fold(title[:200])):
                self.links.append((path, title[:200]))
        elif tag == "meta":
            key = attrs.get("property")
            if key in ("article:content_tier", "article:published_time", "article:section",
                       "og:video:secure_url"):
                value = attrs.get("content") or ""
                self.meta[key] = value if key not in self.meta else ""
        elif tag == "script":
            self._json = attrs.get("type") == "application/ld+json"

    def handle_endtag(self, tag):
        if tag == "script":
            self._json = False

    def handle_data(self, data):
        if not self._json or len(data) > 96 * 1024:
            return
        try:
            body = json.loads(data)
        except (ValueError, RecursionError):
            return
        rows = body.get("@graph", [body]) if isinstance(body, dict) else body
        if isinstance(rows, list):
            self.videos.extend(row for row in rows[:48] if isinstance(row, dict) and row.get("@type") == "VideoObject")


def _aspor_urls(fixture, names, budget, opener):
    home, away = (local_country_name(team["internationalName"], "tr")
                  for team in (fixture["homeTeam"], fixture["awayTeam"]))
    score = fixture["score"]["total"]
    slug = "{}-{}-{}-{}-mac-ozeti".format(fold(home).replace(" ", "-"), score["home"],
                                         score["away"], fold(away).replace(" ", "-"))
    guessed = "/video/uluslar-ligi/" + slug
    if _ASPOR_PATH.fullmatch(guessed):
        yield guessed

    listing = _ASporPage()
    listing.feed(_read(_ASPOR + "/uluslar-ligi", budget, _ASPOR_LIMIT, "text/html", opener))
    day = dt.date.fromisoformat(fixture["kickOffTime"]["date"])
    listed = dict.fromkeys(path for path, title in listing.links if path != guessed
                          and _title_matches(title, *names, (score["home"], score["away"]), day))
    yield from tuple(listed)[:2]


def aspor_media_url(value):
    """Accept only the MP4 hosts and paths published by the broadcaster."""
    return value if _aspor_media_asset(value) else ""


def _aspor_media_asset(value):
    if not isinstance(value, str) or len(value) > 1024 or any(ord(c) < 33 for c in value):
        return ""
    try:
        parts = urlsplit(value)
    except ValueError:
        return ""
    if parts.scheme != "https" or parts.fragment:
        return ""
    prefixes = {_ASPOR_MEDIA: "", _ASPOR_CDN: r"/[a-z0-9]{4,32}"}
    prefix = prefixes.get(parts.netloc)
    if prefix is None:
        return ""
    media = re.fullmatch(prefix + r"(/vms/[a-zA-Z0-9/_-]+\.mp4)", parts.path)
    return media[1] if media else ""


class _ASporMediaRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        asset = _aspor_media_asset(newurl)
        if not asset or asset != _aspor_media_asset(req.full_url):
            raise ScoresError("Unexpected broadcaster media redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _aspor_playback(body, fixture, names):
    page = _ASporPage()
    page.feed(body)
    if (page.meta.get("article:content_tier") != "free"
            or fold(page.meta.get("article:section")) != "uluslar ligi"):
        return None

    timing = fixture["kickOffTime"]
    day = dt.date.fromisoformat(timing["date"])
    kickoff = _timestamp(timing["dateTime"])
    ended = _timestamp(fixture.get("fullTimeAt")) or kickoff + 90 * 60
    offset = dt.timezone(dt.timedelta(hours=timing["utcOffsetInHours"]))
    season = int(fixture["seasonYear"])
    score = fixture["score"]["total"]
    final_score = (score["home"], score["away"])
    resource = page.meta.get("og:video:secure_url")
    article_time = _timestamp(page.meta.get("article:published_time"))
    if not aspor_media_url(resource) or article_time is None:
        return None

    for clip in page.videos:
        if clip.get("contentUrl") != resource or clip.get("isAccessibleForFree") is False:
            continue
        title = text(clip.get("name"), 180)
        if not (_ASPOR_HIGHLIGHT.search(fold(title)) and _title_matches(title, *names, final_score, day)):
            continue
        description = text(clip.get("description"), 1200)
        if written_dates(description) - {day} or seasons(title + " " + description) - {(season - 1, season)}:
            continue
        upload = _timestamp(clip.get("uploadDate"))
        if upload != article_time or not ended <= article_time <= kickoff + 36 * 3600:
            continue
        if dt.datetime.fromtimestamp(article_time, offset).date() not in (day, day + dt.timedelta(days=1)):
            continue
        duration = re.fullmatch(r"PT(?:(\d{1,2})M)?(\d{1,2})S", str(clip.get("duration", "")))
        clock = "{}:{:02d}".format(int(duration[1] or 0), int(duration[2])) if duration else ""
        return resource, clock
    return None


def _aspor_highlight(fixture, cancel, opener, media_opener):
    names = tuple(_video_names(fixture[key], "tr") for key in ("homeTeam", "awayTeam"))
    # The Turkish broadcaster uses these alternate names for the same national side.
    for team, names_on_side in zip((fixture["homeTeam"], fixture["awayTeam"]), names):
        if _normalize(team["internationalName"]) == "cyprus":
            names_on_side.update(("guney kibris", "g kibris rum kesimi", "guney kibris rum kesimi"))

    budget = Budget(cancel, seconds=6)
    for path in _aspor_urls(fixture, names, budget, opener):
        try:
            body = _read(_ASPOR + path, budget, _ASPOR_LIMIT, "text/html", opener)
            playback = _aspor_playback(body, fixture, names)
            if playback is None:
                continue
            url, duration = playback
            request = Request(url, headers={"Range": "bytes=0-63", "Accept-Encoding": "identity"})
            with media_opener(request, timeout=budget.timeout()) as response:
                if (_aspor_media_asset(response.geturl()) == _aspor_media_asset(url)
                        and response.headers.get_content_type() == "video/mp4"
                        and response.read(12)[4:8] == b"ftyp"):
                    budget.check()
                    return url, duration
        except HTTPError as error:
            error.close()
        except ScoresError:
            budget.check()
        except (OSError, ValueError):
            continue
    return None


def find_highlight(match, cancel, opener=None, media_opener=None):
    """Return a receiver-playable highlight for a verified UEFA fixture."""
    if not eligible(match) or _competition(match.league)[0] != "2014":
        return None
    opener = opener or build_opener(_SameHostRedirect()).open
    fixture = _verified_uefa_highlight(match, cancel, opener)
    if fixture is None:
        return None
    media_opener = media_opener or build_opener(_ASporMediaRedirect()).open
    try:
        playback = _aspor_highlight(fixture, cancel, opener, media_opener)
    except HTTPError as error:
        error.close()
        return None
    except (OSError, ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
        return None
    except ScoresError:
        if cancel.is_set():
            raise
        return None
    if playback is None:
        return None
    url, duration = playback
    return {"id": "uefa:" + fixture["id"], "title": "{} {} {}".format(match.home, match.score, match.away),
            "channel": "A Spor", "duration": duration, "published": "", "thumbnail": "", "official": True,
            "match_date_verified": True, "playback_url": url}

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Find full episodes for a selected series on official YouTube sources."""

import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextvars import copy_context
from functools import partial
from itertools import islice
from threading import Event
from typing import NamedTuple
from urllib.parse import urlencode

from .metadata import MetadataError
from .youtube_live import (LiveLookupError, _PUBLIC_LOOKUP, _PublicLookupTiming, _objects, _page_object,
                           _public_page, _record_lookup_timeout, _time_left)
from .youtube_series_catalog import SERIES_BY_COUNTRY
from .youtube_series_playlists import (_PLAYLIST_ID, _PlaylistPage, _field, _playlist_page, _title,
                                      _watch_playlist_window, channel_tab_contents, episode_position, full_episodes,
                                      is_excerpt, named_episode_position, search_episode_rows, title_key)
from .youtube_series_publishers import (_matches, canonical_playlist_episode, publisher_episode_pages,
                                        publisher_episode_video, publisher_series_channel,
                                        publisher_youtube_sources, verified_publisher_channel, verified_youtube_channel)
from .youtube_series_tmdb import SeriesCandidate, series_publishers


class _Publisher(NamedTuple):
    name: str
    channel_id: str
    minimum_seconds: int
    season_series: str = ""
    collection: str = ""


# The channel IDs are pinned to the publisher or rightsholder. Only public,
# publisher-owned playlists are followed, even when the channel saves others.
PUBLISHERS = {
    "AR": (
        _Publisher("eltrece", "UC0DM_mHV2u6dj8ig51GkQwg", 1200),
        _Publisher("Telefe", "UCHc3el42hXKKhjs_1vzWk9A", 1200),
    ),
    "AU": (_Publisher("Australian Children's Television Foundation", "UCaXbaeWUe9bnjOowu3xSDoQ", 720),),
    "BR": (_Publisher("TV Cultura", "UCjOJvvYe6tyEHY21OD33h8A", 900),),
    "CA": (_Publisher("Red Green / S&S Productions", "UCsfIjK5sy9Upzcf316Xpytw", 900, "The Red Green Show"),),
    "CO": (
        _Publisher("Canal RCN", "UCSCAJJ1RJ4rfRTVW4gjYTpQ", 1200),
        _Publisher("Caracol Televisión", "UCcpE1fmG2qBZatj28bak2ng", 1200),
    ),
    "DE": (_Publisher("SAT.1", "UCjIcbyvVQm_alh_Qn5OIgWQ", 900),),
    "EG": (_Publisher("Maspero Zaman", "UColf1L4k9OVGwzuwNkzEOLg", 1200),),
    "ES": (_Publisher("Pocoyó / Animaj", "UCmmTidQn2bJT8n-ErYCR8fg", 240),),
    "FI": (_Publisher("Moomin Characters", "UC82jKc_wW8iHVXU9GrYRunw", 900, "Moomin"),),
    "FR": (_Publisher("Xilam Animation", "UCFqgL7HJU8np9WhkPeqJR9g", 300),),
    "GB": (_Publisher("Aardman / Shaun the Sheep", "UCS7H8U-n5mINVJjJsaRtGHg", 360, "Shaun the Sheep"),),
    "IN": (
        _Publisher("SET India", "UCpEhnqL0y41EpW2TvWAHD7Q", 1080),
        _Publisher("Sony SAB", "UC6-F5tO8uklgE9Zy8IvbdFw", 1080),
    ),
    "IT": (_Publisher("Rainbow", "UCwk4BQN1c2R6rTZe_5d6kiQ", 900, "Winx Club"),),
    "JP": (
        _Publisher("Toei Tokusatsu", "UCEbPgdUbZgme40WpYmR0ocA", 900),
        _Publisher("GUNDAM CHANNEL INTL", "UCejtUitnpnf8Be-v5NuDSLw", 900),
    ),
    "KR": (
        _Publisher("KBS WORLD TV", "UC5BMQOsAB8hKUyHu9KI6yig", 1200),
        _Publisher("KBS Drama", "UChpjIaEgwtDZtmWEkzFulSA", 1200),
    ),
    "MX": (
        _Publisher("Las Estrellas", "UCYDli2cqXJahEj0hQMmmrGQ", 1200, collection="las estrellas"),
        _Publisher("Canal Once", "UCqaOSuXiGdnuoqXBda-XD2A", 900),
    ),
    "MY": (
        _Publisher("Les' Copaque Production", "UCR1c65UsjpaVgcLKa7eM1tg", 420, collection="full episodes"),
        _Publisher("Monsta", "UCfqDvjXc2jbAzaeqlIggkkg", 840),
    ),
    "NL": (_Publisher("Woezel & Pip", "UCmNBltzc6jnZNEXIeH2Xadg", 180),),
    "PH": (_Publisher("ABS-CBN Entertainment", "UCstEtN0pgOmCf02EdXsGChw", 960),),
    "PK": (
        _Publisher("ARY Digital", "UC4JCksJF76g_MdzPVBJoC3Q", 1500),
        _Publisher("HUM TV", "UCEeEQxm6qc_qaTE7qTV5aLQ", 1500),
        _Publisher("HAR PAL GEO", "UCe9JSDmyqNgA_l2BzGHq1Ug", 1500),
    ),
    "PL": (_Publisher("Studio Filmów Rysunkowych", "UCfXtDCs237pyDFCBIQR46yw", 240),),
    "SG": (_Publisher("Mediacorp Drama", "UCnUQ8urh9t0vlHluTJdVLTg", 600),),
    "TH": (
        _Publisher("one31", "UC8f7MkX4MFOOJ2SerXLInCA", 1200),
        _Publisher("Ch3Thailand", "UC48h7Dst_hX82HxOf3xJw_w", 1200),
    ),
    "TR": (
        _Publisher("Kanal D", "UCFoe1tg8MuHjRzmqXtV816A", 2700),
        _Publisher("atv", "UCUVZ7T_kwkxDOGFcDlFI-hg", 2700, collection="atv dizileri"),
        _Publisher("TRT 1", "UCvFudBDDILdDljN4VIZ4Msw", 2700),
        _Publisher("Show TV", "UC9JMe_We017gYrRc7kZHgmg", 2700),
        _Publisher("Kanal 7", "UC0eqqV7viCLfcT39SphmHBQ", 1800),
        _Publisher("NOW", "UCJe13zu6MyE6Oueac41KAqg", 2700),
    ),
    "UA": (
        _Publisher("1+1 Україна", "UCVEaAWKfv7fE1c-ZuBs7TKQ", 1200),
        _Publisher("FILM.UA Group", "UCa4grme_V_sz4c2P5B-hAIw", 1200),
    ),
    "US": (_Publisher("PBS KIDS", "UCrNnk0wFBnCS1awGjq_ijGQ", 540),),
    "VN": (_Publisher("THVL Phim", "UCrS7tv883mVqv07HKgVuwAA", 1200),),
}

_FULL_LABEL = re.compile(
    r"\b(?:full (?:episodes?|eps?)|(?:all|complete) episodes?|(?:full|tam|tum|butun|4k) bolumler(?:i)?|"
    r"episodios? completos?|capitulos? completos?|tous les episodes|episodes? complets?|"
    r"tutti gli episodi|episodi completi|ganze folgen|vollstandige folgen|"
    r"(?:hele|volledige) afleveringen|(?:semua episod|episod penuh)|tron bo|"
    r"(?:усі|всі|все) (?:сезони та )?сер[іи][іи])\b|الحلقات الكاملة|جميع الحلقات|ตอนเตม|全話|본편|전편",
)
_SEASON_LABEL = re.compile(
    r"\b(?:seasons?|saison|stagione|seizoen|temporada|staffel|sezon|musim|сезон)\s+\d{1,2}\b"
    r"|\b\d{1,2}\s+(?:sezon|staffel)\b|\bilk sezon\b"
)
_PARTS = re.compile(r"\s*[|·–—]\s*|\s+-\s+")
_MEMBER_PLAYLIST_LABELS = frozenset(("members only", "uyelere ozel", "sadece uyeler"))
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_CANDIDATE_SUFFIX = re.compile(
    r"\s+(?:(?:full |all |complete )?episodes?|full eps?|(?:tam |tum |full )?bolumler(?:i)?|"
    r"capitulos?(?: completos?)?|(?:season|sezon|temporada|musim|сезон)\s+\d{1,2})$",
)
_SEARCH_TEXT = {
    "AR": "capítulos completos", "BR": "episódios completos", "CO": "capítulos completos",
    "DE": "ganze Folgen", "EG": "الحلقات الكاملة", "ES": "episodios completos",
    "FR": "épisodes complets", "IT": "episodi completi", "JP": "全話",
    "KR": "전편", "MX": "capítulos completos", "MY": "episod penuh",
    "NL": "volledige afleveringen", "PL": "pełne odcinki", "TH": "ตอนเต็ม",
    "TR": "tam bölümler", "UA": "усі серії", "VN": "trọn bộ",
}
_EPISODE_TEXT = {
    "AR": "capítulo completo", "BR": "episódio completo", "CO": "capítulo completo",
    "DE": "ganze Folge", "EG": "الحلقة الكاملة", "ES": "episodio completo",
    "FR": "épisode complet", "IT": "episodio completo", "JP": "第1話",
    "KR": "본편", "MX": "capítulo completo", "MY": "episod penuh",
    "NL": "volledige aflevering", "PL": "pełny odcinek", "TH": "ตอนเต็ม",
    "TR": "bölüm", "UA": "серія", "VN": "tập full",
}
_LANGUAGE_HINT = {
    "ar": "EG", "de": "DE", "es": "AR", "fr": "FR", "it": "IT", "ja": "JP", "ko": "KR",
    "ms": "MY", "nl": "NL", "pl": "PL", "pt": "BR", "th": "TH", "tr": "TR", "uk": "UA", "vi": "VN",
}
_SEASON_FORMAT = {
    "AR": "{}.ª temporada", "BR": "{}.ª temporada", "CO": "temporada {}", "DE": "Staffel {}",
    "ES": "temporada {}", "FR": "saison {}", "IT": "stagione {}", "MX": "temporada {}",
    "TR": "{}. sezon",
}


_FAILED_LOOKUP = (LiveLookupError, OSError, ValueError, RecursionError)
_LOOKUP_SECONDS = 60.0
_REQUEST_SECONDS = 10.0
_PUBLISHER_LOOKUP_SECONDS = 20.0
_WORKERS = ThreadPoolExecutor(max_workers=3, thread_name_prefix="GT-SeriesEpisodes")


def _playlists(content):
    seen = set()
    for node in _objects(content):
        model = node.get("lockupViewModel")
        if _field(model, "contentType") == "LOCKUP_CONTENT_TYPE_PLAYLIST":
            identifier = model.get("contentId")
            title = _field(model, "metadata", "lockupMetadataViewModel", "title", "content")
        else:
            model = node.get("gridPlaylistRenderer") or node.get("playlistRenderer")
            identifier = _field(model, "playlistId")
            title = _title(_field(model, "title"))
        if (isinstance(identifier, str) and _PLAYLIST_ID.fullmatch(identifier)
                and identifier not in seen and isinstance(title, str)):
            seen.add(identifier)
            yield identifier, title


def _matching_playlist(label, candidate):
    if is_excerpt(label):
        return False
    aliases = {title_key(name) for name in candidate.names}
    parts = tuple(title_key(part) for part in _PARTS.split(label))
    if any(part in _MEMBER_PLAYLIST_LABELS and part not in aliases for part in parts):
        return False
    for part in (title_key(label), *parts):
        value = _CANDIDATE_SUFFIX.sub("", part).strip()
        if value in aliases:
            return True
        title, _, qualifier = value.rpartition(" ")
        if title in aliases and _YEAR.fullmatch(qualifier):
            return not candidate.year or candidate.year == qualifier
        for alias in aliases:
            if alias and value.startswith(alias + " "):
                divider, _, remainder = part[len(alias):].strip().partition(" ")
                if divider in ("i", "l") and (_CANDIDATE_SUFFIX.fullmatch(" " + remainder)
                                               or _FULL_LABEL.fullmatch(remainder)):
                    return True
                tail = value[len(alias):].strip()
                if _FULL_LABEL.match(tail) or _SEASON_LABEL.search(tail):
                    if not candidate.year or all(y == candidate.year for y in re.findall(r"\b(?:19|20)\d{2}\b", tail)):
                        return True
    return False


def _numbered_playlist_position(label, candidate):
    return named_episode_position(label, candidate.names, candidate.year)


def _public_data(path, deadline, cancel):
    return _page_object(_public_page(path, deadline, cancel, "en-US,en;q=0.9"), "ytInitialData")


def _search(name, suffix, filters, deadline, cancel):
    name = name.replace('"', "")
    params = {"search_query": f'"{name}" {suffix}', "sp": filters, "hl": "en"}
    return _public_data("/results?" + urlencode(params), deadline, cancel)


def _channel_data(owner, tab, deadline, cancel, query=""):
    params = {"hl": "en"}
    if query:
        params["query"] = query
    path = f"/channel/{owner}/{tab.lower()}?" + urlencode(params)
    return channel_tab_contents(_public_data(path, deadline, cancel), owner, tab)


def _batch(functions, deadline, cancel):
    pending = {}
    track_timeout = _PUBLIC_LOOKUP.get() is not None
    for index, fn in enumerate(functions):
        if cancel is not None and cancel.is_set():
            break
        if deadline - time.monotonic() <= .2:
            _record_lookup_timeout()
            break
        job = _WORKERS.submit(copy_context().run, fn) if track_timeout else _WORKERS.submit(fn)
        pending[job] = index
    results = {}
    try:
        while pending and deadline > time.monotonic() and not (cancel is not None and cancel.is_set()):
            finished, _ = wait(pending, timeout=.08, return_when=FIRST_COMPLETED)
            for job in finished:
                index = pending.pop(job)
                try:
                    results[index] = job.result()
                except _FAILED_LOOKUP as error:
                    _record_lookup_timeout(error)
                    continue
    finally:
        if pending and time.monotonic() >= deadline and not (cancel is not None and cancel.is_set()):
            _record_lookup_timeout()
        for job in pending:
            job.cancel()
    if cancel is not None and cancel.is_set():
        raise LiveLookupError("cancelled")
    return tuple(results[index] for index in sorted(results))


def _official_pins(candidate, details):
    keys = {title_key(name) for name in candidate.names}
    issuers = tuple(title_key(name) for name in details.names)
    return tuple(show for country in details.origins for show in SERIES_BY_COUNTRY.get(country, ())
                 if title_key(show.title) in keys and (not issuers or _matches(show.publisher, issuers)))


def _series_channel_from_publisher(publisher, candidate, deadline, cancel):
    names = tuple(title_key(name) for name in candidate.names)
    content = _channel_data(publisher, "Search", deadline, cancel, candidate.original_name)
    candidates = dict.fromkeys(row.episode.video_id for owner, row in search_episode_rows({"contents": content}, publisher)
                              if owner == publisher and _matches(row.episode.title, names))
    for identifier in tuple(candidates)[:3]:
        if deadline - time.monotonic() < .4:
            _record_lookup_timeout()
            break
        try:
            owner = publisher_series_channel(identifier, publisher, candidate.names, deadline, cancel)
        except _FAILED_LOOKUP as error:
            _record_lookup_timeout(error)
            continue
        if owner:
            return owner
    return ""


def _publisher_channel_route(candidate, deadline, cancel, known, *, publisher="", issuer=""):
    if issuer:
        publisher = verified_publisher_channel(issuer, deadline, cancel)
        if publisher in known:
            return "", ""
    if not publisher:
        return "", ""
    try:
        series = _series_channel_from_publisher(publisher, candidate, deadline, cancel)
    except _FAILED_LOOKUP as error:
        _record_lookup_timeout(error)
        series = ""
    return publisher, series


def _playlist_lookup(identifier, declared_owner, official_link, owners, details, candidate, deadline, cancel):
    page = _playlist_page(identifier, deadline, cancel)
    owner = page.owner
    if not owner or (declared_owner and declared_owner != owner):
        return (), "", ()
    if owner not in owners:
        if not official_link:
            return (), "", ()
        names = tuple(title_key(name) for name in (*details.names, *candidate.names))
        if verified_youtube_channel("/channel/" + owner, names, deadline, cancel) != owner:
            return (), "", ()
    rows = list(page.entries)
    known = {row.episode.video_id for row in rows}
    # The public watch page can expose the next portion of some long lists.
    # Every row is explicitly tied to the same playlist before it is appended.
    while page.more and rows and deadline - time.monotonic() >= 1.0:
        try:
            incoming = _watch_playlist_window(identifier, rows[-1].episode.video_id, deadline, cancel)
        except _FAILED_LOOKUP:
            break
        additions = tuple(row for row in incoming if row.episode.video_id not in known)
        if not additions:
            break
        rows.extend(additions)
        known.update(row.episode.video_id for row in additions)
    playlist = _PlaylistPage(tuple(rows), page.more, owner)
    verified = full_episodes(playlist, details.minimum_seconds,
                             numbered=True, aliases=candidate.names, bare_title=True)
    if len(verified) == len(rows):
        return verified, owner, ()
    accepted_ids = {episode.video_id for episode in verified}
    eligible = {episode.video_id for episode in full_episodes(playlist, details.minimum_seconds)} - accepted_ids
    return verified, owner, tuple(row for row in rows if row.episode.video_id in eligible)


def _upload_rows(data, owners, candidate, minimum, channel_id=""):
    approved = {}
    for owner, row in search_episode_rows(data, channel_id):
        if owner in owners:
            approved.setdefault(owner, []).append(row)
    return tuple(episode for owner, entries in approved.items() for episode in full_episodes(
        _PlaylistPage(tuple(entries), False, owner), minimum, numbered=True, aliases=candidate.names))


def _ordered_episodes(episodes):
    ids, positions = set(), set()
    result = []
    for episode in episodes:
        position = episode_position(episode.title)
        if episode.video_id in ids or position[1] and position in positions:
            continue
        ids.add(episode.video_id)
        positions.add(position)
        result.append((position, episode))
    result.sort(key=lambda row: (not row[0][1], row[0]))
    return tuple(episode for _, episode in result)


def _missing_episode_queries(details, found, country):
    positions = {episode_position(item.title) for item in found}
    if not positions:
        return ()
    known = {number for season, number in positions if number and season == 0}
    if details.episode_count and known:
        missing = (number for number in range(details.episode_count, 0, -1) if number not in known)
        return tuple(islice((_episode_query(country, number) for number in missing), 3))
    if details.seasons and any(season and number for season, number in positions):
        missing = ((season, number) for season, count in reversed(details.seasons)
                   for number in range(count, 0, -1) if (season, number) not in positions)
        return tuple(islice((f"S{season:02d}E{number:02d}" for season, number in missing), 3))
    return ()


def _missing_publisher_positions(details, found):
    positions = {episode_position(item.title) for item in found}
    if details.seasons and any(season and number for season, number in positions):
        expected = ((season, number) for season, count in details.seasons for number in range(1, count + 1))
    else:
        final = max(details.episode_count, max((number for _, number in positions), default=0))
        expected = ((0, number) for number in range(1, final + 1))
    return tuple(islice((position for position in expected if position not in positions), 6))


def _episode_query(country, number):
    if country == "TR":
        return f'"{number}. bölüm"'
    return f'{_EPISODE_TEXT.get(country, "episode")} {number}'


def _season_queries(details, country):
    if len(details.seasons) < 2:
        return ()
    pattern = _SEASON_FORMAT.get(country, "season {}")
    return tuple(pattern.format(details.seasons[index][0]) for index in (0, -1))


def discover_series_episodes(client, title, country="", cancel=None):
    """Collect playable full episodes for one TMDb title from publisher-owned sources."""
    ident = title.get("id")
    if type(ident) is not int or ident <= 0 or title.get("media_type") != "tv":
        return ()
    name = title.get("original_title") or title.get("title")
    if not isinstance(name, str) or not name.strip():
        return ()
    candidate = SeriesCandidate(ident, name, title["title"], title.get("year", ""))
    deadline = time.monotonic() + _LOOKUP_SECONDS
    timing = _PublicLookupTiming(_REQUEST_SECONDS, Event())
    token = _PUBLIC_LOOKUP.set(timing)
    try:
        found = _collect_series_episodes(client, candidate, country, deadline, cancel)
        if cancel is not None and cancel.is_set():
            raise LiveLookupError("cancelled")
        if not found and (timing.timed_out.is_set() or time.monotonic() >= deadline):
            raise LiveLookupError("timeout")
        return found
    except MetadataError as error:
        if _record_lookup_timeout(error):
            raise LiveLookupError("timeout") from error
        raise
    finally:
        _PUBLIC_LOOKUP.reset(token)


def _collect_series_episodes(client, candidate, country, deadline, cancel):
    name = candidate.original_name
    details = series_publishers(client, candidate, country, deadline, cancel)
    if details is None:
        return ()
    _time_left(deadline, cancel)
    pins = _official_pins(candidate, details)
    site_deadline = min(deadline - 24.0, time.monotonic() + _PUBLISHER_LOOKUP_SECONDS)
    sources = publisher_youtube_sources(details, candidate.names, site_deadline, cancel, candidate.year)
    known_issuers = tuple(title_key(value) for value in details.names)
    trusted = dict.fromkeys(sources.series_channel_ids)
    trusted.update(dict.fromkeys(show.channel_id for show in pins if show.channel_id))
    trusted.update(dict.fromkeys(owner for owner, _ in sources.episodes))
    publisher_channels = dict.fromkeys(sources.channel_ids)
    pinned_issuers = set()
    for code in details.origins:
        for source in PUBLISHERS.get(code, ()):
            if _matches(source.name, known_issuers):
                publisher_channels.setdefault(source.channel_id)
                pinned_issuers.add(title_key(source.name))
    trusted.update(publisher_channels)

    preferred = {identifier: ("", True) for identifier in sources.playlist_ids}
    for show in pins:
        preferred.update({identifier: (show.channel_id, True) for identifier in show.playlist_ids})
    language = (details.original_language or "").split("-", 1)[0]
    locale = next((code for code in (country, *details.origins) if code in _SEARCH_TEXT),
                  _LANGUAGE_HINT.get(language, ""))
    label = _SEARCH_TEXT.get(locale, "full episodes")
    variant = _EPISODE_TEXT.get(locale, "full episode")
    found = []
    localized = {}
    numbered = {}
    collections = {}

    def read_playlists(choices, end):
        approved = frozenset(trusted)
        requests = (partial(_playlist_lookup, identifier, owner, direct, approved, details, candidate, end, cancel)
                    for identifier, (owner, direct) in choices.items())
        for rows, owner, title_checks in _batch(requests, end, cancel):
            if owner:
                trusted.setdefault(owner)
            found.extend(rows)
            for row in title_checks:
                localized.setdefault((owner, row.episode.video_id), row)

    def confirm_localized_titles():
        if not localized or deadline - time.monotonic() < .5:
            return
        accepted_ids = {episode.video_id for episode in found}
        positions = {episode_position(episode.title) for episode in found} - {(0, 0)}
        end = min(deadline, time.monotonic() + 18.0)
        requests = (partial(canonical_playlist_episode, owner, row, candidate, details, end, cancel)
                    for (owner, video_id), row in localized.items()
                    if video_id not in accepted_ids and episode_position(row.episode.title) not in positions)
        found.extend(episode for episode in _batch(requests, end, cancel) if episode is not None)
        localized.clear()

    read_playlists(preferred, min(deadline - 20.0, time.monotonic() + 12.0))
    needs_more = not found or not details.episode_count or len(_ordered_episodes(found)) < details.episode_count
    if needs_more and trusted.keys() <= publisher_channels.keys():
        end = min(deadline - 20.0, time.monotonic() + 24.0)
        known = frozenset(publisher_channels)
        lookup = partial(_publisher_channel_route, candidate, end, cancel, known)
        issuers = (name for name in details.names if title_key(name) not in pinned_issuers)
        fallback = (partial(lookup, issuer=name) for name in islice(issuers, 3))
        published = (partial(lookup, publisher=owner) for owner in tuple(publisher_channels)[:3])
        endorsed = {}
        for publisher, series in _batch((*fallback, *published), end, cancel):
            if publisher:
                trusted.setdefault(publisher)
            if series:
                endorsed.setdefault(series)
        trusted = dict.fromkeys((*endorsed, *trusted))
    if trusted:
        end = min(deadline - 10.0, time.monotonic() + 12.0)

        def from_list(owner):
            return owner, tuple(_playlists(_channel_data(owner, "Playlists", end, cancel)))

        def from_search(suffix):
            content = _search(name, suffix, "EgIQAw==", end, cancel).get("contents")
            return "", tuple(_playlists(content))

        listing = (partial(from_list, owner) for owner in tuple(trusted)[:3])
        phrases = (label, *_season_queries(details, locale)) if needs_more else ()
        searches = (partial(from_search, phrase) for phrase in phrases)
        choices = {}
        for owner, items in _batch((*searches, *listing), end, cancel):
            for identifier, label_text in items:
                if identifier in preferred:
                    continue
                position = _numbered_playlist_position(label_text, candidate) if owner else None
                if position is not None:
                    numbered.setdefault(identifier, (owner, position))
                elif needs_more and _matching_playlist(label_text, candidate):
                    choices.setdefault(identifier, ("", False))
        collections = dict(islice(choices.items(), 10))
        read_playlists(collections, min(deadline, time.monotonic() + 14.0))

    by_owner = {}
    for owner, entry in sources.episodes:
        by_owner.setdefault(owner, []).append(entry)
    for owner, rows in by_owner.items():
        found.extend(full_episodes(_PlaylistPage(tuple(rows), False, owner), details.minimum_seconds,
                                  numbered=True, aliases=candidate.names))
    confirm_localized_titles()

    if sources.episode_pages or sources.episode_archives:
        missing = _missing_publisher_positions(details, found)
        if missing and deadline - time.monotonic() >= 1.0:
            end = min(deadline, time.monotonic() + 14.0)
            pages = publisher_episode_pages(sources, candidate.names, end, cancel, missing)
            requests = (partial(publisher_episode_video, page, candidate.names, details, candidate.year, end, cancel)
                        for page in pages[:6])
            for row in _batch(requests, end, cancel):
                if row is not None:
                    owner, entry = row
                    trusted.setdefault(owner)
                    found.append(entry.episode)

    positions = {episode_position(item.title) for item in found}
    dated = {identifier: (owner, False) for identifier, (owner, position) in numbered.items()
             if identifier not in collections and position not in positions}
    read_playlists(dated, min(deadline, time.monotonic() + 12.0))
    confirm_localized_titles()

    if trusted and deadline - time.monotonic() >= .5:
        end = min(deadline, time.monotonic() + 12.0)
        approved = frozenset(trusted)

        def from_channel(owner):
            content = _channel_data(owner, "Search", end, cancel, name)
            return _upload_rows({"contents": content}, approved, candidate, details.minimum_seconds, owner)

        def from_videos(suffix, stop):
            data = _search(name, suffix, "EgIQAQ==", stop, cancel)
            return _upload_rows(data, approved, candidate, details.minimum_seconds)

        channels = (partial(from_channel, owner) for owner in tuple(trusted)[:3])
        for rows in _batch((*channels, partial(from_videos, variant, end)), end, cancel):
            found.extend(rows)
        unique = _ordered_episodes(found)
        queries = _missing_episode_queries(details, unique, locale)
        if queries and deadline - time.monotonic() >= 1.0:
            for rows in _batch((partial(from_videos, query, deadline) for query in queries), deadline, cancel):
                found.extend(rows)

    found.extend(episode for show in pins for episode in show.episodes)
    if cancel is not None and cancel.is_set():
        raise LiveLookupError("cancelled")
    return _ordered_episodes(found)


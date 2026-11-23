# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Find public full-film uploads from verified production sources."""

import re
import time
from functools import partial
from typing import NamedTuple

from .youtube_live import LiveLookupError, _objects, _time_left
from .youtube_series_discovery import (
    _LOOKUP_SECONDS, _PUBLISHER_LOOKUP_SECONDS, _batch, _channel_data, _public_data, _search,
)
from .youtube_series_playlists import _field, _playlist_page, search_episode_rows, title_key
from .youtube_series_publishers import publisher_youtube_sources, verified_youtube_channel
from .youtube_series_tmdb import PublisherSite, _issuers, publisher_sites


class _MoviePublishers(NamedTuple):
    names: tuple[str, ...]
    sites: tuple[PublisherSite, ...]
    homepage: str


_YEAR = re.compile(r"(?:18|19|20)\d{2}")
_CHANNEL_SUFFIX = re.compile(r"\s+(?:official(?: channel)?|resmi(?: kanal)?|officiel)$")
_FORMAT = re.compile(
    r"\b(?:full (?:movie|film|hd)|complete (?:movie|film)|tam film|tek parca|turk filmi|yerli film|"
    r"film complet|pelicula completa|filme completo|film completo|ganzer film|volledige film|"
    r"полный фильм|фильм полностью|фільм повністю|teljes film|cely film(?: online)?|caly film|"
    r"film lengkap|phim day du|extended (?:cut|edition)|director s cut|restored|remastered|"
    r"english subtitles|original version|turkce dublaj|hd|fhd|uhd|[248]k|[0-9]{3,4}p)\b"
    r"|الفيلم الكامل|完整版|完整电影|完整電影"
)
_SEARCH_TEXT = {
    "ar": "الفيلم الكامل", "cs": "celý film", "de": "ganzer Film", "el": "ολόκληρη ταινία",
    "es": "película completa", "fa": "فیلم کامل", "fr": "film complet", "hi": "पूरी फिल्म",
    "hu": "teljes film", "id": "film lengkap", "it": "film completo", "ja": "映画 本編",
    "ko": "영화 전체", "nl": "volledige film", "pl": "cały film", "pt": "filme completo",
    "ru": "фильм полностью", "ta": "முழு திரைப்படம்", "th": "หนังเต็มเรื่อง", "tr": "tek parça film",
    "uk": "фільм повністю", "vi": "phim đầy đủ", "zh": "完整版",
}
_REGION_LANGUAGE = {
    "AR": "es", "BR": "pt", "DE": "de", "EG": "ar", "ES": "es", "FR": "fr", "IT": "it",
    "JP": "ja", "KR": "ko", "MX": "es", "NL": "nl", "PL": "pl", "PT": "pt", "RU": "ru",
    "TR": "tr", "UA": "uk",
}


def _matching_title(label, titles, year):
    key = " " + title_key(label) + " "
    for title in titles:
        before, match, after = key.partition(" " + title + " ")
        if match:
            qualifiers = _FORMAT.sub(" ", before + " " + after).split()
            if all(_YEAR.fullmatch(value) and (not year or value == year) for value in qualifiers):
                return True
    return False


def _verified_company(owner, names, deadline, cancel):
    path = f"/channel/{owner}?hl=en"
    data = _public_data(path, deadline, cancel)
    meta = _field(data, "metadata", "channelMetadataRenderer")
    title = _field(meta, "title")
    if _field(meta, "externalId") != owner or not isinstance(title, str):
        return ""
    production_name = _CHANNEL_SUFFIX.sub("", title_key(title))
    if production_name not in names and all(name.replace(" ", "") != production_name.replace(" ", "")
                                            for name in names):
        return ""
    verified_title = (title + ", Verified").casefold()
    for node in _objects(data.get("header")):
        style = node.get("style") or node.get("badgeStyle")
        label = node.get("label")
        if style == "BADGE_STYLE_TYPE_VERIFIED" or isinstance(label, str) and label.casefold() == verified_title:
            return owner
    return ""


def _publisher_playlist(identifier, owners, issuer_keys, deadline, cancel):
    page = _playlist_page(identifier, deadline, cancel)
    owner = page.owner
    if not owner:
        return "", ()
    if owner not in owners and verified_youtube_channel("/channel/" + owner, issuer_keys, deadline, cancel) != owner:
        return "", ()
    return owner, tuple((owner, row) for row in page.entries)


def discover_youtube_movie(client, selected, country="", cancel=None):
    identifier = selected.get("id")
    if type(identifier) is not int or identifier <= 0 or selected.get("media_type") != "movie":
        return ()
    deadline = time.monotonic() + _LOOKUP_SECONDS
    _time_left(deadline, cancel)
    with client.request_scope(cancel_event=cancel, deadline=min(deadline, time.monotonic() + 5.0)):
        detail = client._cached_request(f"/movie/{identifier}")
        if detail.get("id") != identifier or detail.get("adult") is True:
            return ()
        companies = tuple(_issuers(detail, "production_companies"))
        if not companies:
            return ()
        sites = publisher_sites(client, "company", companies)

    names = tuple(dict.fromkeys(value.strip()[:240] for value in (
        detail.get("original_title"), selected.get("original_title"), detail.get("title"), selected.get("title"),
    ) if isinstance(value, str) and value.strip()))
    if not names:
        return ()
    names_by_key = tuple(dict.fromkeys(key for name in names if (key := title_key(name))))
    if not names_by_key:
        return ()
    release = detail.get("release_date") or selected.get("year", "")
    year = release[:4] if isinstance(release, str) and _YEAR.fullmatch(release[:4]) else ""
    runtime = detail.get("runtime")
    runtime = runtime if type(runtime) is int and 1 <= runtime <= 600 else 0
    minimum = runtime * 50 if runtime else 45 * 60
    maximum = (runtime + max(15, runtime // 4)) * 60 if runtime else 5 * 60 * 60
    home = detail.get("homepage")
    producer = _MoviePublishers(tuple(name for _, name in companies), sites, home if isinstance(home, str) else "")
    issuer_keys = tuple(title_key(name) for name in producer.names)
    issuer_deadline = min(deadline - 9.0, time.monotonic() + _PUBLISHER_LOOKUP_SECONDS)
    sources = publisher_youtube_sources(producer, names, issuer_deadline, cancel)
    known_owners = dict.fromkeys((*sources.channel_ids, *(owner for owner, _ in sources.episodes)))
    candidates = list(sources.episodes)

    language = detail.get("original_language")
    language = language if isinstance(language, str) and language else _REGION_LANGUAGE.get(country, "en")
    hint = _SEARCH_TEXT.get(language.split("-", 1)[0].lower(), "full movie")
    end = min(deadline - 3.0, time.monotonic() + 8.0)

    def search_public(suffix):
        result = _search(names[0], suffix, "EgIQAQ==", end, cancel)
        return "", tuple(search_episode_rows(result))

    def search_channel(owner):
        result = _channel_data(owner, "Search", end, cancel, names[0])
        return "", tuple(search_episode_rows({"contents": result}, channel_id=owner))

    linked_lists = (partial(_publisher_playlist, ident, frozenset(known_owners), issuer_keys, end, cancel)
                    for ident in sources.playlist_ids)
    channels = (partial(search_channel, owner) for owner in tuple(known_owners)[:2])
    for owner, rows in _batch((*linked_lists, *channels, partial(search_public, hint), partial(search_public, "")),
                              end, cancel):
        if owner:
            known_owners.setdefault(owner)
        candidates.extend(rows)

    matching = [(owner, row.episode) for owner, row in candidates
                if minimum <= row.seconds <= maximum and (not row.uploader or owner == row.uploader)
                and _matching_title(row.episode.title, names_by_key, year)]
    if not any(owner in known_owners for owner, _ in matching):
        unverified = tuple(dict.fromkeys(owner for owner, _ in matching))[:3]
        tasks = (partial(_verified_company, owner, issuer_keys, deadline, cancel) for owner in unverified)
        known_owners.update(dict.fromkeys(owner for owner in _batch(tasks, deadline, cancel) if owner))
    if cancel is not None and cancel.is_set():
        raise LiveLookupError("cancelled")
    matches = {item.video_id: item for owner, item in matching if owner in known_owners}
    return tuple(matches.values())

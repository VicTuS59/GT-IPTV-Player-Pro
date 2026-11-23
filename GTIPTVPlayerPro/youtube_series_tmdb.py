# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""TMDb production and broadcaster metadata for a selected series."""

import time
from typing import NamedTuple

from .metadata import MetadataError
from .youtube_live import _record_lookup_timeout
from .youtube_series_catalog import COUNTRY_CODES


class SeriesCandidate(NamedTuple):
    tmdb_id: int
    original_name: str
    name: str
    year: str

    @property
    def names(self):
        return tuple(dict.fromkeys(name for name in (self.original_name, self.name) if name))


class PublisherSite(NamedTuple):
    name: str
    homepage: str


class SeriesPublishers(NamedTuple):
    names: tuple[str, ...]
    sites: tuple[PublisherSite, ...]
    homepage: str
    original_language: str
    minimum_seconds: int
    origins: tuple[str, ...]
    seasons: tuple[tuple[int, int], ...]
    episode_count: int


def _name(value):
    return " ".join(value.split())[:120] if isinstance(value, str) else ""


def _issuers(detail, field):
    entries = detail.get(field)
    if isinstance(entries, list):
        for entry in entries[:6]:
            if isinstance(entry, dict) and type(entry.get("id")) is int and entry["id"] > 0:
                name = _name(entry.get("name"))
                if name:
                    yield entry["id"], name


def publisher_sites(client, endpoint, entries, *, limit=1):
    sites = []
    for ident, name in entries[:2]:
        try:
            entry = client._cached_request(f"/{endpoint}/{ident}")
        except (MetadataError, OSError, ValueError) as error:
            _record_lookup_timeout(error)
            continue
        homepage = entry.get("homepage")
        if entry.get("id") == ident and isinstance(homepage, str) and homepage.strip():
            sites.append(PublisherSite(name, homepage.strip()))
            if len(sites) >= limit:
                break
    return tuple(sites)


def _minimum_seconds(detail):
    latest = detail.get("last_episode_to_air")
    runtime = latest.get("runtime") if isinstance(latest, dict) else None
    if type(runtime) is not int or not 1 <= runtime <= 240:
        times = detail.get("episode_run_time")
        runtime = next((v for v in times if type(v) is int and 1 <= v <= 240), None) if isinstance(times, list) else None
    return max(60, runtime * 45) if runtime else 900


def series_publishers(client, candidate, country, deadline, cancel):
    with client.request_scope(cancel_event=cancel, deadline=min(deadline, time.monotonic() + 5.0)):
        detail = client._cached_request(f"/tv/{candidate.tmdb_id}")
        if detail.get("id") != candidate.tmdb_id:
            return None
        regions = detail.get("origin_country")
        origins = tuple(code for code in regions if code in COUNTRY_CODES) if isinstance(regions, list) else ()
        if country and origins and country not in origins:
            return None
        origins = origins or ((country,) if country else ())
        networks = tuple(_issuers(detail, "networks"))
        companies = tuple(_issuers(detail, "production_companies"))
        names = tuple(dict.fromkeys(name for _, name in (*companies[:3], *networks[:3])))
        sites = (publisher_sites(client, "company", companies, limit=2)
                 + publisher_sites(client, "network", networks, limit=2))
    homepage = detail.get("homepage")
    language_code = detail.get("original_language")
    seasons = detail.get("seasons")
    counts = []
    if isinstance(seasons, list):
        for item in seasons[:100]:
            if isinstance(item, dict):
                number, count = item.get("season_number"), item.get("episode_count")
                if type(number) is int and 0 < number <= 100 and type(count) is int and 0 < count <= 9999:
                    counts.append((number, count))
    total = detail.get("number_of_episodes")
    total = total if type(total) is int and 0 < total <= 9999 else 0
    return SeriesPublishers(names, sites, homepage if isinstance(homepage, str) else "",
                            language_code if isinstance(language_code, str) else "", _minimum_seconds(detail),
                            origins, tuple(sorted(set(counts))), total)

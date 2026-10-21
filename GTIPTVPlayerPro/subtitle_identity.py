# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded, worker-only TMDb verification for subtitle programme identities."""

from collections import OrderedDict
from hashlib import sha256
import json
import re
import threading
import time
import unicodedata

from .diagnostics import log_event
from .subtitle_jobs import check_subtitle_job, SubtitleJobCancelled


IDENTITY_BUDGET_SECONDS = 4.0
IDENTITY_CACHE_SECONDS = 6 * 60 * 60
IDENTITY_MISS_SECONDS = 60
MAX_IDENTITY_ENTRIES = 128
_RESOLVERS = OrderedDict()
_RESOLVER_LOCK = threading.Lock()
_CONTEXT_FIELDS = ("title", "year", "content_type", "season", "episode",
                   "tmdb_id", "imdb_id", "parent_tmdb_id", "parent_imdb_id",
                   "episode_tmdb_id", "episode_imdb_id")


def subtitle_identity_context(metadata):
    metadata = metadata if isinstance(metadata, dict) else {}
    value = ["subtitle-identity-v1"] + [
        " ".join(str(metadata.get(name) or "").split()).casefold()
        for name in _CONTEXT_FIELDS
    ]
    return sha256(json.dumps(value, ensure_ascii=False).encode("utf-8")).hexdigest()


def matching_metadata(metadata, result=None):
    """Reuse verified IDs only for the search that actually resolved them.

    Rendering and re-ranking call this on the GUI thread; it never does I/O.
    Playback FPS, duration and release hints always come from the live input.
    """
    value = dict(metadata) if isinstance(metadata, dict) else {}
    evidence = result.get("_tmdb_identity") if isinstance(result, dict) else None
    if (isinstance(evidence, dict)
            and evidence.get("query") == subtitle_identity_context(value)
            and isinstance(evidence.get("data"), dict)):
        value.update(evidence["data"])
        value["_tmdb_identity"] = evidence
    return value


def _identifier(value, imdb=False):
    value = str(value or "").strip().lower()
    if imdb and value.startswith("tt"):
        value = value[2:]
    return str(int(value)) if value.isdigit() and 0 < len(value) <= 12 and int(value) > 0 else ""


def _title_key(value):
    value = unicodedata.normalize("NFKD", str(value or "").casefold().replace("ı", "i"))
    return " ".join("".join(character if character.isalnum() else " "
                            for character in value if not unicodedata.combining(character)).split())


def _year(value):
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", str(value or ""))
    return int(match.group(1)) if match else 0


def _aliases(payload):
    values = [payload.get(name) for name in ("title", "original_title", "name", "original_name")]
    alternatives = payload.get("alternative_titles")
    if isinstance(alternatives, dict):
        entries = alternatives.get("titles") or alternatives.get("results") or []
        if isinstance(entries, list):
            values.extend(item.get("title") for item in entries[:50] if isinstance(item, dict))
    return list(dict.fromkeys(" ".join(str(value).split())[:160] for value in values
                             if value and isinstance(value, str)))[:32]


class _JobCancellation(object):
    def is_set(self):
        check_subtitle_job()
        return False


class SubtitleIdentityResolver(object):
    def __init__(self, client, clock=None):
        self.client = client
        self.clock = clock or time.monotonic
        self._lock = threading.Lock()
        self._cache = OrderedDict()
        self._flights = {}
        self._retry_at = 0

    def resolve(self, metadata):
        original = dict(metadata) if isinstance(metadata, dict) else {}
        if not getattr(self.client, "available", False):
            return original
        context = subtitle_identity_context(original)
        deadline = self.clock() + IDENTITY_BUDGET_SECONDS
        while True:
            check_subtitle_job()
            with self._lock:
                cached = self._cache.get(context)
                if cached is not None and cached[0] > self.clock():
                    self._cache.move_to_end(context)
                    return self._apply(original, context, cached[1])
                if self.clock() < self._retry_at:
                    return original
                flight = self._flights.get(context)
                if flight is None:
                    flight = self._flights[context] = threading.Event()
                    break
            if self.clock() >= deadline:
                return original
            flight.wait(min(0.1, max(0.001, deadline - self.clock())))
        try:
            with self.client.request_scope(cancel_event=_JobCancellation(), deadline=deadline):
                data = self._resolve(original)
            check_subtitle_job()
            ttl = IDENTITY_CACHE_SECONDS if data else IDENTITY_MISS_SECONDS
            with self._lock:
                self._cache[context] = (self.clock() + ttl, data)
                self._cache.move_to_end(context)
                while len(self._cache) > MAX_IDENTITY_ENTRIES:
                    self._cache.popitem(last=False)
            log_event("subtitles", "tmdb-identity={} type={}".format(
                "verified" if data else "unresolved", original.get("content_type", "movie")))
            return self._apply(original, context, data)
        except SubtitleJobCancelled:
            raise
        except Exception:
            # A metadata outage must not disable an otherwise working provider.
            with self._lock:
                self._retry_at = self.clock() + IDENTITY_MISS_SECONDS
            log_event("subtitles", "tmdb-identity-unavailable fallback=provider-search")
            return original
        finally:
            with self._lock:
                self._flights.pop(context, None)
                flight.set()

    @staticmethod
    def _apply(original, context, data):
        if not data:
            return dict(original)
        value = dict(original)
        value.update(data)
        value["_tmdb_identity"] = {"query": context, "data": dict(data)}
        return value

    def _get(self, path, parameters=None):
        check_subtitle_job()
        return self.client._request_json(path, parameters or {}, self.client.language)

    def _detail(self, kind, identifier):
        try:
            payload = self._get("/{}/{}".format(kind, identifier),
                                {"append_to_response": "external_ids,alternative_titles"})
        except Exception as error:
            if getattr(error, "http_status", None) == 404:
                return None
            raise
        return payload if isinstance(payload, dict) and _identifier(payload.get("id")) == identifier else None

    @staticmethod
    def _imdb(payload):
        external = payload.get("external_ids")
        external = external if isinstance(external, dict) else {}
        return _identifier(external.get("imdb_id") or payload.get("imdb_id"), imdb=True)

    def _valid_detail(self, meta, payload):
        expected_imdb = _identifier(meta.get("imdb_id"), imdb=True)
        actual_imdb = self._imdb(payload)
        if expected_imdb and actual_imdb and expected_imdb != actual_imdb:
            return False
        if expected_imdb and actual_imdb == expected_imdb:
            return True
        names = {_title_key(value) for value in _aliases(payload)}
        expected = _title_key(meta.get("search_title"))
        if not expected or expected not in names:
            return False
        expected_year = _year(meta.get("year"))
        actual_year = _year(payload.get("release_date") or payload.get("first_air_date"))
        return not expected_year or bool(actual_year and abs(expected_year - actual_year) <= 1)

    def _search(self, meta, kind):
        if len(meta.get("search_title", "")) < 2:
            return None
        params = {"query": meta["search_title"], "include_adult": "false", "page": 1}
        year = _year(meta.get("year"))
        if year:
            params["primary_release_year" if kind == "movie" else "first_air_date_year"] = year
        payload = self._get("/search/" + kind, params)
        rows = payload.get("results") if isinstance(payload, dict) else None
        matches = {}
        for item in rows[:20] if isinstance(rows, list) else ():
            if not isinstance(item, dict):
                continue
            identifier = _identifier(item.get("id"))
            found_year = _year(item.get("release_date") or item.get("first_air_date"))
            if (not identifier or _title_key(meta["search_title"]) not in {
                    _title_key(name) for name in _aliases(item)}
                    or year and (not found_year or abs(year - found_year) > 1)):
                continue
            matches[identifier] = (abs(year - found_year) if year else 0, item)
        if not matches:
            return None
        best = min(value[0] for value in matches.values())
        choices = [identifier for identifier, value in matches.items() if value[0] == best]
        # Do not turn popularity/API order into proof of the intended remake.
        return self._detail(kind, choices[0]) if len(choices) == 1 else None

    def _resolve(self, original):
        from .online_subtitles import normalized_metadata
        meta = normalized_metadata(original, "en")
        kind = "tv" if meta["content_type"] == "series" else "movie"
        if kind == "tv":
            meta["tmdb_id"] = meta.get("parent_tmdb_id") or meta["tmdb_id"]
            meta["imdb_id"] = meta.get("parent_imdb_id") or meta["imdb_id"]
        identifier = _identifier(meta.get("parent_tmdb_id") or meta.get("tmdb_id"))
        payload = self._detail(kind, identifier) if identifier else None
        if payload is not None and not self._valid_detail(meta, payload):
            payload = None
        imdb = _identifier(meta.get("parent_imdb_id") or meta.get("imdb_id"), imdb=True)
        if payload is None and imdb:
            found = self._get("/find/tt" + imdb.zfill(7), {"external_source": "imdb_id"})
            entries = found.get("tv_results" if kind == "tv" else "movie_results") if isinstance(found, dict) else None
            if isinstance(entries, list) and len(entries) == 1 and isinstance(entries[0], dict):
                found_id = _identifier(entries[0].get("id"))
                if found_id:
                    payload = self._detail(kind, found_id)
                    if payload is not None and not self._valid_detail(meta, payload):
                        payload = None
        if payload is None:
            # A portal ID can be stale. Recover only through an unambiguous
            # exact title/year search, then retain the newly verified IDs.
            title_meta = dict(meta, imdb_id="", tmdb_id="")
            payload = self._search(title_meta, kind)
            if payload is not None and not self._valid_detail(title_meta, payload):
                payload = None
        if payload is None:
            return None
        tmdb_id = _identifier(payload.get("id"))
        found_imdb = self._imdb(payload)
        aliases = _aliases(payload)
        title = payload.get("original_title" if kind == "movie" else "original_name") or (aliases[0] if aliases else meta["title"])
        data = {"title": str(title)[:120], "year": _year(
                    payload.get("release_date") or payload.get("first_air_date")) or meta["year"],
                "tmdb_id": tmdb_id, "imdb_id": "tt" + found_imdb.zfill(7) if found_imdb else "",
                "sd_id": "", "_title_aliases": aliases, "_tmdb_verified": True}
        if kind == "tv":
            data.update(parent_tmdb_id=tmdb_id, parent_imdb_id=data["imdb_id"],
                        episode_tmdb_id="", episode_imdb_id="", _episode_verified=False)
            raw_season = original.get("season")
            if raw_season is not None and str(raw_season).strip() and meta["episode"] > 0:
                season, episode = meta["season"], meta["episode"]
                try:
                    detail = self._get("/tv/{}/season/{}/episode/{}".format(tmdb_id, season, episode),
                                       {"append_to_response": "external_ids"})
                    if (isinstance(detail, dict) and detail.get("season_number") == season
                            and detail.get("episode_number") == episode and _identifier(detail.get("id"))):
                        episode_imdb = self._imdb(detail)
                        data.update(episode_tmdb_id=_identifier(detail.get("id")),
                                    episode_imdb_id="tt" + episode_imdb.zfill(7) if episode_imdb else "",
                                    _episode_verified=True)
                except SubtitleJobCancelled:
                    raise
                except Exception:
                    # The verified parent still improves parent-ID + S/E queries.
                    pass
        return data


def resolve_subtitle_metadata(metadata):
    """Use the existing TMDb preference/key; invoke only from search workers."""
    from .settings import load_player_settings
    from .metadata import TMDbMetadataClient
    from .i18n import metadata_language
    try:
        settings = load_player_settings()
        key = getattr(settings, "tmdb_api_key", "")
        if not key or not getattr(settings, "metadata_enabled", True):
            return dict(metadata)
        language = metadata_language()
        scope = sha256((key + "|" + language).encode("utf-8")).hexdigest()
        with _RESOLVER_LOCK:
            resolver = _RESOLVERS.get(scope)
            if resolver is None:
                resolver = SubtitleIdentityResolver(TMDbMetadataClient(
                    api_key=key, language=language, timeout=2.0))
                _RESOLVERS[scope] = resolver
                while len(_RESOLVERS) > 2:
                    _RESOLVERS.popitem(last=False)
            _RESOLVERS.move_to_end(scope)
        return resolver.resolve(metadata)
    except SubtitleJobCancelled:
        raise
    except Exception:
        return dict(metadata)

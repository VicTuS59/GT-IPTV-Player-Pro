# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""On-demand TMDB discovery; never fetches or sends IPTV credentials."""

import copy
import hashlib
import re
import threading
import time
from collections import OrderedDict
from urllib.parse import urlsplit

from .content import ContentError
from .diagnostics import log_event
from .i18n import N_
from .metadata import MetadataError, TMDbMetadataClient, _image_url


DISCOVERY_CACHE_SECONDS = 6 * 60 * 60
_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_TRAILER_LOCK = threading.Lock()
_VIDEO_KEY = re.compile(r"^[A-Za-z0-9_-]{11}$")


def _text(value, limit=2000):
    return " ".join(str(value or "").split())[:limit]


def _number(value, default=0, maximum=1000000000):
    try:
        return max(0, min(maximum, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def movie_record(value):
    if not isinstance(value, dict) or value.get("adult"):
        return None
    movie_id = _number(value.get("id"))
    title = _text(value.get("title") or value.get("original_title"), 240)
    if not movie_id or not title:
        return None
    try:
        rating = max(0.0, min(10.0, float(value.get("vote_average") or 0)))
    except (TypeError, ValueError, OverflowError):
        rating = 0.0
    date = _text(value.get("release_date"), 10)
    year = date[:4] if re.match(r"^\d{4}-\d{2}-\d{2}$", date) else ""
    return {
        "id": movie_id, "title": title,
        "original_title": _text(value.get("original_title"), 240),
        "year": year, "rating": rating,
        "overview": _text(value.get("overview"), 6000),
        "poster": _image_url(value.get("poster_path"), "w342"),
        "backdrop": _image_url(value.get("backdrop_path"), "w1280"),
    }


class TMDbDiscoveryClient(TMDbMetadataClient):
    """Use the existing authenticated, bounded and cancellable transport."""

    def _cached_request(self, path, parameters=None, force=False, language=None):
        self._check_request_limits()
        if not self.available:
            raise MetadataError(N_("TMDb API key is not configured"))
        language = language or self.language
        parameters = dict(parameters or {})
        identity = hashlib.sha256(self.api_key.encode("utf-8")).hexdigest()
        key = (identity, language, path, tuple(sorted(parameters.items())))
        now = time.monotonic()
        if not force:
            with _CACHE_LOCK:
                entry = _CACHE.get(key)
                if entry and 0 <= now - entry[0] < DISCOVERY_CACHE_SECONDS:
                    _CACHE.move_to_end(key)
                    return copy.deepcopy(entry[1])
        result = self._request_json(path, parameters, language=language)
        self._check_request_limits()
        with _CACHE_LOCK:
            _CACHE[key] = (now, copy.deepcopy(result))
            _CACHE.move_to_end(key)
            while len(_CACHE) > 48:
                _CACHE.popitem(last=False)
        return result

    def movies(self, section="popular", page=1, genre_id=0, movie_id=0, force=False):
        page = max(1, _number(page, 1, 500))
        parameters = {"page": page, "include_adult": "false"}
        if section == "top":
            path = "/movie/top_rated"
        elif section == "new":
            path = "/movie/now_playing"
        elif section == "genres":
            path = "/discover/movie"
            parameters["sort_by"] = "popularity.desc"
            if _number(genre_id):
                parameters["with_genres"] = _number(genre_id)
        elif section == "similar" and _number(movie_id):
            path = "/movie/{}/recommendations".format(_number(movie_id))
        else:
            path = "/movie/popular"
        payload = self._cached_request(path, parameters, force=force)
        if not isinstance(payload.get("results"), list):
            raise MetadataError(N_("TMDb returned an invalid response"))
        entries, seen = [], set()
        for value in payload["results"][:20]:
            record = movie_record(value)
            if record and record["id"] not in seen:
                seen.add(record["id"])
                entries.append(record)
        return {
            "entries": entries, "page": page,
            "pages": max(1, _number(payload.get("total_pages"), 1, 500)),
        }

    def genres(self, force=False):
        payload = self._cached_request("/genre/movie/list", force=force)
        values = payload.get("genres")
        if not isinstance(values, list):
            raise MetadataError(N_("TMDb returned an invalid response"))
        return [(_text(x.get("name"), 100), _number(x.get("id")))
                for x in values[:100]
                if isinstance(x, dict) and _number(x.get("id")) and x.get("name")]

    def detail(self, movie_id, force=False):
        movie_id = _number(movie_id)
        if not movie_id:
            raise MetadataError(N_("TMDb returned an invalid response"))
        path = "/movie/{}".format(movie_id)
        payload = self._cached_request(path, {"append_to_response": "credits"}, force=force)
        record = movie_record(payload)
        if not record or record["id"] != movie_id:
            raise MetadataError(N_("TMDb returned an invalid response"))
        if (not record["overview"] or not record["backdrop"]) and not self.language.startswith("en"):
            try:
                fallback = self._cached_request(path, language="en-US", force=force)
                if not record["overview"]:
                    record["overview"] = _text(fallback.get("overview"), 6000)
                if not record["backdrop"]:
                    record["backdrop"] = _image_url(fallback.get("backdrop_path"), "w1280")
            except MetadataError:
                self._check_request_limits()
        record["runtime"] = _number(payload.get("runtime"), maximum=2000)
        genres = payload.get("genres") or []
        record["genres"] = ", ".join(_text(x.get("name"), 70)
                                      for x in genres[:6] if isinstance(x, dict))
        credits = payload.get("credits") or {}
        crew = credits.get("crew", []) if isinstance(credits, dict) else []
        record["director"] = ", ".join(_text(x.get("name"), 100)
                                        for x in crew[:300]
                                        if isinstance(x, dict) and x.get("job") == "Director")[:300]
        return record

    def trailer(self, movie_id):
        path = "/movie/{}/videos".format(_number(movie_id))
        languages = [self.language]
        if not self.language.startswith("en"):
            languages.append("en-US")
        for language in languages:
            payload = self._cached_request(path, language=language)
            videos = payload.get("results")
            if not isinstance(videos, list):
                raise MetadataError(N_("TMDb returned an invalid response"))
            candidates = [x for x in videos[:150]
                          if isinstance(x, dict) and x.get("site") == "YouTube"
                          and x.get("type") in ("Trailer", "Teaser")
                          and _VIDEO_KEY.match(str(x.get("key") or ""))]
            if candidates:
                candidates.sort(key=lambda x: (x.get("type") == "Trailer", bool(x.get("official"))), reverse=True)
                return str(candidates[0]["key"])
        return ""


class DiscoverySearchClient(object):
    """Preserve playback/account guards while prefilling provider movie search."""

    def __init__(self, client, movie, hidden_category_ids):
        self._client = client
        self._movie = dict(movie)
        self._hidden = hidden_category_ids
        self._chosen_query = None
        self._variants = []
        for key in ("title", "original_title"):
            value = _text(movie.get(key), 120)
            if value and value.casefold() not in [x.casefold() for x in self._variants]:
                self._variants.append(value)

    def __getattr__(self, name):
        return getattr(self._client, name)

    def search_movies(self, query, page=1, page_size=14, hidden_category_ids=None):
        hidden = self._hidden if hidden_category_ids is None else hidden_category_ids
        if not self._variants or _text(query, 120).casefold() != self._variants[0].casefold():
            return self._plain_search(query, page, page_size, hidden)
        searcher = getattr(self._client, "search_discovery_movies", None)
        if callable(searcher):
            return searcher(self._movie, page=page, page_size=page_size, hidden_category_ids=hidden)
        choices = [self._chosen_query] if self._chosen_query else self._variants
        result = None
        for candidate in choices:
            result = self._plain_search(candidate, page, page_size, hidden)
            if getattr(result, "items", ()) or getattr(result, "has_more", False):
                self._chosen_query = candidate
                break
        if result is None:
            raise ContentError(N_("Could not load movies"))
        # ID and release year distinguish remakes when the portal supplies them.
        target_id = str(self._movie.get("id") or "")
        target_year = str(self._movie.get("year") or "")
        result.items.sort(key=lambda item: (
            bool(target_id and str(getattr(item, "tmdb_id", "")) == target_id),
            bool(target_year and target_year in str(getattr(item, "year", "") or getattr(item, "name", ""))),
        ), reverse=True)
        return result

    def _plain_search(self, query, page, page_size, hidden):
        if str(getattr(self._client, "source_type", "")) == "xtream":
            return self._client.search_movies(query, page=page, page_size=page_size, hidden_category_ids=hidden)
        return self._client.search_movies(query, page=page, page_size=page_size)


class _BundledTrailerResolver(object):
    """Use the packaged resolver with the receiver's own video preferences."""

    def __init__(self, player):
        from .i18n import metadata_language
        self._preferences = (player.youtube_resolution, metadata_language(),
                             player.youtube_dash, player.youtube_stream_mode,
                             player.youtube_audio_preference)

    def extract(self, video_id):
        # set_preferences is thread-local, so configure the worker that does
        # the extraction, not the GUI thread that opens Discover Movies.
        from .youtube_vendor.config import EXTRACT_LOCK, set_preferences
        with EXTRACT_LOCK:
            set_preferences(*self._preferences)
            from .youtube_vendor.video_url import YouTubeVideoUrl
            return YouTubeVideoUrl().extract(video_id)


def prepare_youtube_trailer():
    """Prepare a trailer without importing a separate YouTube plugin."""
    try:
        from .settings import load_player_settings
        player = load_player_settings()
        service_type = int(player.movie_service_type)
        # Service type 1 is for DVB; trailers need an HTTP playback engine.
        if service_type not in (4097, 5001, 5002):
            service_type = 4097
        return _BundledTrailerResolver(player), service_type
    except Exception as exc:
        log_event("discovery", "trailer initialization failed: {}".format(type(exc).__name__))
        raise MetadataError(N_("The trailer could not be played."))


def resolve_youtube_trailer(video_id, token, resolver):
    """Resolve with the bundled extractor; keep its DASH audio URI intact."""
    if not _VIDEO_KEY.fullmatch(str(video_id or "")):
        raise MetadataError(N_("No trailer is available for this movie."))
    if not _TRAILER_LOCK.acquire(False):
        raise MetadataError(N_("Please wait"))
    try:
        token.check()
        try:
            url = str(resolver.extract(video_id) or "")
            token.check()
            # SUBURI is used by Enigma2 to play separate video/audio streams.
            for part in url.split("&suburi="):
                parsed = urlsplit(part)
                host = str(parsed.hostname or "").lower()
                if (parsed.scheme not in ("http", "https") or not host
                        or not (host.endswith(".googlevideo.com") or host in
                                ("googlevideo.com", "www.youtube.com", "youtube.com"))
                        or any(char in part for char in ("\r", "\n", "\x00"))):
                    raise ValueError("Invalid trailer stream")
        except Exception as exc:
            token.check()
            log_event("discovery", "trailer resolution failed: {}".format(type(exc).__name__))
            raise MetadataError(N_("The trailer could not be played."))
        return url
    finally:
        _TRAILER_LOCK.release()


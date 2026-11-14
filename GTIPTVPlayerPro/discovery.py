# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""On-demand TMDB discovery; never fetches or sends IPTV credentials."""

import copy
import hashlib
import heapq
import re
import threading
import time
from collections import OrderedDict

from .content import ContentError, _movie_search_text
from .i18n import N_
from .metadata import MetadataError, TMDbMetadataClient, _image_url, _year, clean_media_title


DISCOVERY_CACHE_SECONDS = 6 * 60 * 60
_CACHE = OrderedDict()
_CACHE_LOCK = threading.RLock()
_VIDEO_KEY = re.compile(r"^[A-Za-z0-9_-]{11}$")
_LANGUAGE_KEY = re.compile(r"^[a-z]{2}$")


def _text(value, limit=2000):
    return " ".join(str(value or "").split())[:limit]


def _number(value, default=0, maximum=1000000000):
    try:
        return max(0, min(maximum, int(value)))
    except (TypeError, ValueError, OverflowError):
        return default


def _title_record(value, media_type, backdrop_size="w1280"):
    if not isinstance(value, dict) or value.get("adult"):
        return None
    is_series = media_type == "tv"
    title_id = _number(value.get("id"))
    original = _text(value.get("original_name" if is_series else "original_title"), 240)
    title = _text(value.get("name" if is_series else "title"), 240) or original
    if not title_id or not title:
        return None
    try:
        rating = max(0.0, min(10.0, float(value.get("vote_average") or 0)))
    except (TypeError, ValueError, OverflowError):
        rating = 0.0
    date = _text(value.get("first_air_date" if is_series else "release_date"), 10)
    year = date[:4] if re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", date) else ""
    return {
        "id": title_id, "media_type": media_type, "title": title, "original_title": original,
        "original_language": _text(value.get("original_language"), 2).lower(),
        "year": year, "rating": rating,
        "overview": _text(value.get("overview"), 6000),
        "poster": _image_url(value.get("poster_path"), "w342"),
        "backdrop": _image_url(value.get("backdrop_path"), backdrop_size),
    }


def movie_record(value):
    return _title_record(value, "movie")


class TMDbDiscoveryClient(TMDbMetadataClient):
    """Use the existing authenticated, bounded and cancellable transport."""

    def __init__(self, *args, backdrop_size="w1280", **kwargs):
        super().__init__(*args, **kwargs)
        self._backdrop_size = "w780" if backdrop_size == "w780" else "w1280"

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
        return self.titles("movie", section, page, genre_id, movie_id, force)

    def titles(self, media_type, section="popular", page=1, genre_id=0, title_id=0, force=False):
        media_type = "tv" if media_type == "tv" else "movie"
        page = max(1, _number(page, 1, 500))
        parameters = {"page": page, "include_adult": "false"}
        if section == "top":
            path = "/{}/top_rated".format(media_type)
        elif section == "new":
            path = "/tv/on_the_air" if media_type == "tv" else "/movie/now_playing"
        elif section == "genres":
            path = "/discover/{}".format(media_type)
            parameters["sort_by"] = "popularity.desc"
            if _number(genre_id):
                parameters["with_genres"] = _number(genre_id)
        elif section == "similar" and _number(title_id):
            path = "/{}/{}/recommendations".format(media_type, _number(title_id))
        else:
            path = "/{}/popular".format(media_type)
        payload = self._cached_request(path, parameters, force=force)
        if not isinstance(payload.get("results"), list):
            raise MetadataError(N_("TMDb returned an invalid response"))
        entries, seen = [], set()
        for value in payload["results"][:20]:
            record = _title_record(value, media_type, self._backdrop_size)
            if record and record["id"] not in seen:
                seen.add(record["id"])
                entries.append(record)
        return {
            "entries": entries, "page": page,
            "pages": max(1, _number(payload.get("total_pages"), 1, 500)),
        }

    def genres(self, force=False, *, media_type="movie"):
        media_type = "tv" if media_type == "tv" else "movie"
        payload = self._cached_request("/genre/{}/list".format(media_type), force=force)
        values = payload.get("genres")
        if not isinstance(values, list):
            raise MetadataError(N_("TMDb returned an invalid response"))
        return [(_text(x.get("name"), 100), _number(x.get("id")))
                for x in values[:100]
                if isinstance(x, dict) and _number(x.get("id")) and x.get("name")]

    def detail(self, movie_id, force=False, *, media_type="movie", listing=None):
        media_type = "tv" if media_type == "tv" else "movie"
        title_id = _number(movie_id)
        if not title_id:
            raise MetadataError(N_("TMDb returned an invalid response"))
        path = "/{}/{}".format(media_type, title_id)
        parameters = {"append_to_response": "credits"} if media_type == "movie" else None
        payload = self._cached_request(path, parameters, force=force)
        record = _title_record(payload, media_type, self._backdrop_size)
        if not record or record["id"] != title_id:
            raise MetadataError(N_("TMDb returned an invalid response"))
        for field in ("poster", "backdrop", "overview"):
            if listing and not record[field]:
                record[field] = listing.get(field) or ""
        if (not record["overview"] or not record["backdrop"]) and not self.language.startswith("en"):
            try:
                fallback = self._cached_request(path, language="en-US", force=force)
                if not record["overview"]:
                    record["overview"] = _text(fallback.get("overview"), 6000)
                if not record["backdrop"]:
                    record["backdrop"] = _image_url(fallback.get("backdrop_path"), self._backdrop_size)
            except MetadataError:
                self._check_request_limits()
        record["runtime"] = _number(payload.get("runtime"), maximum=2000)
        genres = payload.get("genres") or []
        record["genres"] = ", ".join(_text(x.get("name"), 70)
                                      for x in (genres[:6] if isinstance(genres, list) else ())
                                      if isinstance(x, dict))
        if media_type == "tv":
            record["seasons"] = _number(payload.get("number_of_seasons"), maximum=1000)
            creators = payload.get("created_by")
            record["creators"] = ", ".join(_text(x.get("name"), 100)
                                            for x in (creators[:6] if isinstance(creators, list) else ())
                                            if isinstance(x, dict))[:300]
        else:
            credits = payload.get("credits") or {}
            crew = credits.get("crew") if isinstance(credits, dict) else None
            record["director"] = ", ".join(_text(x.get("name"), 100)
                                            for x in (crew[:300] if isinstance(crew, list) else ())
                                            if isinstance(x, dict) and x.get("job") == "Director")[:300]
        return record

    def trailer(self, movie_id, *, media_type="movie", original_language=""):
        media_type = "tv" if media_type == "tv" else "movie"
        title_id = _number(movie_id)
        if not title_id:
            return ""
        path = "/{}/{}/videos".format(media_type, title_id)
        languages = [self.language, "en-US"]
        if isinstance(original_language, str) and _LANGUAGE_KEY.fullmatch(original_language):
            languages.insert(1, original_language)
        tried = set()

        def pick(payload):
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

        for language in languages:
            code = language.lower().split("-", 1)[0]
            if code in tried:
                continue
            tried.add(code)
            found = pick(self._cached_request(path, language=language))
            if found:
                return found
        if media_type == "tv":
            params = {"include_video_language": ",".join(sorted(tried) + ["null"])}
            return pick(self._cached_request(path, params, language=self.language))
        return ""


def series_lookup(title):
    titles = tuple(dict.fromkeys(_movie_search_text(_text(title.get(key), 120))
                                 for key in ("title", "original_title")))
    return str(_number(title.get("id")) or ""), _year(title.get("year")), tuple(filter(None, titles))


def series_match_positions(rows, permitted, lookup, check):
    """Match both TMDb names and rank provider rows without hydrating the catalogue."""
    tmdb_id, year, titles = lookup
    scores = bytearray(len(rows))
    if tmdb_id:
        for position, group in enumerate(rows.codes):
            if position % 256 == 0:
                check()
            if permitted[group] and str(rows.discovery_identity(position)[0] or "").strip() == tmdb_id:
                scores[position] = 5
    phrases = tuple(" " + title + " " for title in titles)
    candidates = heapq.merge(*(rows.matches((title.encode("utf-8"),), permitted, check)
                               for title in titles))
    previous = -1
    for count, position in enumerate(candidates):
        if count % 256 == 0:
            check()
        if position == previous:
            continue
        previous = position
        if scores[position] == 5:
            continue
        unused_id, name, recorded_year = rows.discovery_identity(position)
        text = " " + _movie_search_text(name) + " "
        if not any(phrase in text for phrase in phrases):
            continue
        clean = _movie_search_text(clean_media_title(name, preserve_years=True))
        exact = clean in titles or bool(year and clean.endswith(" " + year) and clean[:-5] in titles)
        known_year = _year(recorded_year) or (year if year and year in text.split() else "")
        scores[position] = (4 if exact else 2) if year and known_year == year else (3 if exact else 1)
    for rank in range(5, 0, -1):
        needle = bytes((rank,))
        position = scores.find(needle)
        emitted = 0
        while position >= 0:
            if emitted % 256 == 0:
                check()
            yield position
            emitted += 1
            position = scores.find(needle, position + 1)


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

    def search_movies(self, query, page=1, page_size=14, hidden_category_ids=None, preview=None):
        hidden = self._hidden if hidden_category_ids is None else hidden_category_ids
        if not self._variants or _text(query, 120).casefold() != self._variants[0].casefold():
            return self._plain_search(query, page, page_size, hidden)
        searcher = getattr(self._client, "search_discovery_movies", None)
        if callable(searcher):
            options = {"preview": preview} if preview is not None else {}
            return searcher(self._movie, page=page, page_size=page_size,
                            hidden_category_ids=hidden, **options)
        choices = [self._chosen_query] if self._chosen_query else self._variants
        result = None
        for candidate in choices:
            result = self._plain_search(candidate, page, page_size, hidden)
            if not result.supported:
                return result
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

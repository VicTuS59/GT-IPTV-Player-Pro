# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import difflib
import hashlib
import json
import os
import re
import socket
import threading
import time
import unicodedata
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request

from . import PLUGIN_VERSION
from .content import MovieInfo, SeriesInfo
from .diagnostics import log_event
from .http_transport import build_credential_urlopen
from .i18n import N_, _


TMDB_API_BASE = "https://api.themoviedb.org/3"
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"
DEFAULT_METADATA_CACHE_PATH = "/etc/enigma2/gtiptvplayerpro-metadata.json"
METADATA_CACHE_SECONDS = 30 * 24 * 60 * 60
NEGATIVE_CACHE_SECONDS = 24 * 60 * 60
MAX_CACHE_ENTRIES = 1000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
RESPONSE_READ_CHUNK_BYTES = 64 * 1024
DEFAULT_TIMEOUT = 6
CACHE_FILE_VERSION = 2
CACHE_MATCH_VERSION = 3
CACHE_PAYLOAD_VERSION = 3


class MetadataError(Exception):
    pass


def _text(value):
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())


def _year(value):
    match = re.search(r"(?:19|20)\d{2}", _text(value))
    return match.group(0) if match else ""


def clean_media_title(value):
    """Remove common IPTV decorations without damaging the actual title."""
    title = _text(value)
    if not title:
        return ""
    title = re.sub(
        r"^\s*[\[({]\s*(?:TR|TUR|TÜRK|TURK|EN|DE|FR|AR|NL|"
        r"YERLİ\s+DİZİ|YERLI\s+DIZI)\s*[\])}]\s*[|:\-]*\s*",
        "",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"^\s*(?:(?:TR|TUR|TÜRK|TURK|EN|DE|FR|AR|NL|"
        r"YERLİ\s+DİZİ|YERLI\s+DIZI)\s*[|:\-]\s*)+",
        "",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"[\[({]\s*(?:TR|TUR|TÜRK|TURK|YERLİ\s+DİZİ|"
        r"YERLI\s+DIZI)\s*[\])}]",
        " ",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"\bS(?:EASON)?\s*0*\d{1,3}\s*(?:E|B|EP(?:ISODE)?|"
        r"BÖLÜM|BOLUM)\s*0*\d{1,4}\b",
        " ",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"\b\d{1,3}\.?\s*(?:SEZON|SEASON)\b",
        " ",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"\b(?:S|SEZON|SEASON)\s*0*\d{1,3}\b",
        " ",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"\b(?:BÖLÜM|BOLUM|EP(?:ISODE)?)\s*0*\d{1,4}\b",
        " ",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"\b\d{1,4}\.?\s*(?:BÖLÜM|BOLUM)\b",
        " ",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"[\[({][^\])}]*\b(?:19\d{2}|20\d{2}|4K|UHD|FHD|HD|SD|"
        r"DUBLAJ|ALTYAZI|TURKCE|TÜRKÇE)[^\])}]*[\])}]",
        " ",
        title,
        flags=re.I,
    )
    title = re.sub(
        r"\b(?:4K|UHD|FHD|FULL\s*HD|1080P|720P|HDR|WEB[- ]?DL|"
        r"BLURAY|DUBLAJ|TÜRKÇE\s*DUBLAJ|TURKCE\s*DUBLAJ|"
        r"ALTYAZILI?|MULTI)\b",
        " ",
        title,
        flags=re.I,
    )
    title = re.sub(r"[\[({]\s*[\])}]", " ", title)
    title = re.sub(r"\b(?:19|20)\d{2}\b", " ", title)
    title = re.sub(r"\s*[|:_]+\s*", " ", title)
    title = re.sub(r"\s+-\s*$", "", title)
    return " ".join(title.split()).strip(" -._")


def clear_metadata_cache(path=DEFAULT_METADATA_CACHE_PATH):
    removed = False
    for candidate in (path, path + ".tmp"):
        try:
            if os.path.exists(candidate):
                os.unlink(candidate)
                removed = True
        except OSError:
            pass
    return removed


def _normalised(value):
    value = _text(value).lower().replace("ı", "i")
    value = unicodedata.normalize("NFKD", value)
    value = "".join(character for character in value if not unicodedata.combining(character))
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in value
        ).split()
    )


def _has_rating(value):
    try:
        return float(str(value or "").replace(",", ".")) > 0
    except (TypeError, ValueError, OverflowError):
        return False


def _format_rating(value):
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return ""
    if number <= 0:
        return ""
    return "{:.1f}".format(number)


def _image_url(path, size):
    path = str(path or "").strip()
    if not path.startswith("/"):
        return ""
    return "{}/{}/{}".format(TMDB_IMAGE_BASE, size, path.lstrip("/"))


class TMDbMetadataClient(object):
    """Small dependency-free TMDb fallback used only for missing metadata."""

    def __init__(
        self,
        api_key="",
        enabled=True,
        language="tr-TR",
        timeout=DEFAULT_TIMEOUT,
        opener=None,
        cache_path=DEFAULT_METADATA_CACHE_PATH,
        clock=None,
    ):
        self.api_key = str(api_key or "").strip()
        self.enabled = bool(enabled)
        self.language = str(language or "tr-TR").strip() or "tr-TR"
        self.timeout = timeout
        self.opener = opener or build_credential_urlopen(
            ("api_key", "access_token")
        )
        self.cache_path = cache_path
        self.clock = clock or time.time
        self._lock = threading.RLock()
        self._lookup_locks = {}
        self._cache = None
        self._cache_batch_depth = 0
        self._cache_dirty = False
        self._thread_status = threading.local()
        self._request_context = threading.local()
        self.last_error = ""

    @property
    def last_error(self):
        return getattr(self._thread_status, "last_error", "")

    @last_error.setter
    def last_error(self, value):
        self._thread_status.last_error = str(value or "")

    @property
    def available(self):
        return self.enabled and bool(self.api_key)

    @contextmanager
    def request_scope(self, cancel_event=None, deadline=None):
        previous = getattr(self._request_context, "limits", None)
        if previous is not None:
            previous_event, previous_deadline = previous
            if cancel_event is None:
                cancel_event = previous_event
            if previous_deadline is not None:
                deadline = (
                    previous_deadline
                    if deadline is None
                    else min(float(deadline), float(previous_deadline))
                )
        self._request_context.limits = (cancel_event, deadline)
        try:
            yield self
        finally:
            if previous is None:
                try:
                    del self._request_context.limits
                except AttributeError:
                    pass
            else:
                self._request_context.limits = previous

    def _check_request_limits(self, request_deadline=None):
        cancel_event, scoped_deadline = getattr(
            self._request_context,
            "limits",
            (None, None),
        )
        if cancel_event is not None and cancel_event.is_set():
            raise MetadataError(N_("TMDb request cancelled"))
        if scoped_deadline is not None:
            request_deadline = (
                float(scoped_deadline)
                if request_deadline is None
                else min(float(request_deadline), float(scoped_deadline))
            )
        if (
            request_deadline is not None
            and time.monotonic() >= float(request_deadline)
        ):
            raise MetadataError(N_("TMDb request timed out"))

    def _request_timeout(self, request_deadline=None):
        scoped_deadline = getattr(
            self._request_context,
            "limits",
            (None, None),
        )[1]
        if scoped_deadline is not None:
            request_deadline = (
                float(scoped_deadline)
                if request_deadline is None
                else min(float(request_deadline), float(scoped_deadline))
            )
        if request_deadline is None:
            return self.timeout
        remaining = float(request_deadline) - time.monotonic()
        if remaining <= 0:
            raise MetadataError(N_("TMDb request timed out"))
        return min(float(self.timeout), remaining)

    def _absolute_request_deadline(self):
        deadline = time.monotonic() + float(self.timeout)
        scoped_deadline = getattr(
            self._request_context,
            "limits",
            (None, None),
        )[1]
        if scoped_deadline is not None:
            deadline = min(deadline, float(scoped_deadline))
        return deadline

    @staticmethod
    def _set_response_timeout(response, timeout):
        """Best-effort deadline update for urllib's underlying socket."""
        fp = getattr(response, "fp", None)
        raw = getattr(fp, "raw", None)
        candidates = (
            getattr(raw, "_sock", None),
            getattr(raw, "sock", None),
            getattr(fp, "_sock", None),
            getattr(fp, "sock", None),
        )
        seen = set()
        for candidate in candidates:
            if candidate is None or id(candidate) in seen:
                continue
            seen.add(id(candidate))
            setter = getattr(candidate, "settimeout", None)
            if not callable(setter):
                continue
            try:
                setter(float(timeout))
            except (OSError, TypeError, ValueError):
                continue
            return

    def _read_response_body(self, response, request_deadline):
        """Read a bounded body without letting trickle traffic extend the deadline."""
        read1 = getattr(response, "read1", None)
        chunks = []
        total = 0
        limit = MAX_RESPONSE_BYTES + 1
        while total < limit:
            self._check_request_limits(request_deadline)
            self._set_response_timeout(
                response,
                self._request_timeout(request_deadline),
            )
            size = min(RESPONSE_READ_CHUNK_BYTES, limit - total)
            if callable(read1):
                chunk = read1(size)
            else:
                chunk = response.read(size)
            self._check_request_limits(request_deadline)
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            elif not isinstance(chunk, bytes):
                chunk = bytes(chunk)
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)

    def test_connection(self):
        payload = self._request_json("/configuration")
        if not isinstance(payload.get("images"), dict):
            self.last_error = N_("Could not load the TMDb configuration")
            raise MetadataError(self.last_error)
        self.last_error = ""
        return True

    def _uses_bearer_token(self):
        return self.api_key.startswith("eyJ") or len(self.api_key) > 64

    def _request_json(self, path, parameters=None, language=None):
        if not self.available:
            raise MetadataError(N_("TMDb API key is not configured"))
        parameters = dict(parameters or {})
        if language:
            parameters["language"] = language
        headers = {
            "Accept": "application/json",
            "User-Agent": "GT-IPTV-Player-Pro/{}".format(PLUGIN_VERSION),
        }
        if self._uses_bearer_token():
            headers["Authorization"] = "Bearer {}".format(self.api_key)
        else:
            parameters["api_key"] = self.api_key
        url = "{}{}?{}".format(TMDB_API_BASE, path, urlencode(parameters))
        try:
            request_deadline = self._absolute_request_deadline()
            self._check_request_limits(request_deadline)
            response = self.opener(
                Request(url, headers=headers),
                timeout=self._request_timeout(request_deadline),
            )
            try:
                body = self._read_response_body(response, request_deadline)
            finally:
                close = getattr(response, "close", None)
                if close is not None:
                    close()
            self._check_request_limits(request_deadline)
        except HTTPError as error:
            close = getattr(error, "close", None)
            if callable(close):
                try:
                    close()
                except (AttributeError, OSError, ValueError):
                    pass
            log_event("metadata", N_("TMDb HTTP error path={}").format(path), error)
            if error.code in (401, 403):
                raise MetadataError(N_("TMDb API key is invalid"))
            if error.code == 429:
                raise MetadataError(N_("TMDb request limit reached"))
            raise MetadataError(_(N_("TMDb returned HTTP {}")).format(error.code))
        except (URLError, socket.timeout, OSError) as error:
            log_event("metadata", N_("TMDb connection error path={}").format(path), error)
            raise MetadataError(N_("Could not connect to the TMDb service"))
        if len(body) > MAX_RESPONSE_BYTES:
            raise MetadataError(N_("TMDb response is too large"))
        if not isinstance(body, str):
            body = body.decode("utf-8", "replace")
        try:
            payload = json.loads(body)
        except (TypeError, ValueError):
            log_event("metadata", N_("Invalid TMDb JSON path={}").format(path))
            raise MetadataError(N_("TMDb returned an invalid response"))
        if not isinstance(payload, dict):
            raise MetadataError(N_("TMDb returned an invalid response"))
        if payload.get("success") is False:
            status_code = payload.get("status_code")
            if status_code in (3, 7, 10, 14, 16, 30, 31, 32, 33):
                raise MetadataError(N_("TMDb API key is invalid"))
            raise MetadataError(N_("TMDb request was rejected"))
        return payload

    def _load_cache(self):
        if self._cache is not None:
            return self._cache
        try:
            with open(self.cache_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (IOError, OSError, TypeError, ValueError):
            payload = {}
        entries = (
            payload.get("entries")
            if isinstance(payload, dict)
            and payload.get("version") == CACHE_FILE_VERSION
            else {}
        )
        self._cache = entries if isinstance(entries, dict) else {}
        return self._cache

    @contextmanager
    def _lookup_guard(self, key):
        """Serialize one title lookup and release its lock when no caller uses it."""
        with self._lock:
            state = self._lookup_locks.get(key)
            if state is None:
                state = {"lock": threading.Lock(), "users": 0}
                self._lookup_locks[key] = state
            state["users"] += 1
            lookup_lock = state["lock"]
        try:
            with lookup_lock:
                yield
        finally:
            with self._lock:
                current = self._lookup_locks.get(key)
                if current is state:
                    current["users"] -= 1
                    if current["users"] <= 0:
                        self._lookup_locks.pop(key, None)

    def begin_cache_batch(self):
        """Defer cache persistence until every overlapping batch has ended."""
        with self._lock:
            self._cache_batch_depth += 1
        return self

    def end_cache_batch(self):
        """Finish one batch and persist all pending entries at the outer edge."""
        with self._lock:
            if self._cache_batch_depth <= 0:
                raise RuntimeError("TMDb metadata cache batch is not active")
            self._cache_batch_depth -= 1
            if self._cache_batch_depth == 0 and self._cache_dirty:
                if self._save_cache():
                    self._cache_dirty = False

    @contextmanager
    def cache_batch(self):
        """Context manager form that also flushes after exceptional exits."""
        self.begin_cache_batch()
        try:
            yield self
        finally:
            self.end_cache_batch()

    def _cache_changed(self):
        """Persist immediately unless an explicit cache batch is active."""
        self._cache_dirty = True
        if self._cache_batch_depth == 0 and self._save_cache():
            self._cache_dirty = False

    def _save_cache(self):
        directory = os.path.dirname(self.cache_path)
        temporary = self.cache_path + ".tmp"
        try:
            if directory and not os.path.isdir(directory):
                os.makedirs(directory)
            entries = self._load_cache()
            valid_entries = {}
            for key, entry in entries.items():
                if not isinstance(entry, dict):
                    continue
                try:
                    float(entry.get("cached_at", 0))
                except (TypeError, ValueError, OverflowError):
                    continue
                valid_entries[key] = entry
            if len(valid_entries) != len(entries):
                entries.clear()
                entries.update(valid_entries)
            if len(entries) > MAX_CACHE_ENTRIES:
                newest = sorted(
                    entries.items(),
                    key=lambda pair: float(pair[1].get("cached_at", 0)),
                    reverse=True,
                )[:MAX_CACHE_ENTRIES]
                entries.clear()
                entries.update(newest)
            # TMDb summaries are stored as UTF-8 regardless of the process
            # locale used by the receiver image.
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(
                    {"version": CACHE_FILE_VERSION, "entries": entries},
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except (AttributeError, OSError):
                    pass
            try:
                os.chmod(temporary, 0o600)
            except OSError:
                pass
            getattr(os, "replace", os.rename)(temporary, self.cache_path)
            return True
        except (IOError, OSError, TypeError, ValueError):
            try:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            except OSError:
                pass
            return False

    @staticmethod
    def _cache_key(media_type, title, year):
        raw = "{}|{}|{}|{}".format(
            CACHE_MATCH_VERSION,
            media_type,
            _normalised(title),
            year,
        )
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _cached(self, key):
        entry = self._load_cache().get(key)
        if not isinstance(entry, dict):
            return False, None
        ttl = METADATA_CACHE_SECONDS if entry.get("found") else NEGATIVE_CACHE_SECONDS
        try:
            fresh = self.clock() - float(entry.get("cached_at", 0)) < ttl
        except (TypeError, ValueError, OverflowError):
            fresh = False
        if not fresh:
            self._load_cache().pop(key, None)
            return False, None
        data = entry.get("data")
        return True, data if isinstance(data, dict) else None

    @staticmethod
    def _candidate_score(query, requested_year, candidate, media_type):
        title_keys = (
            ("title", "original_title")
            if media_type == "movie"
            else ("name", "original_name")
        )
        query_key = _normalised(query)
        names = [_normalised(candidate.get(key)) for key in title_keys]
        names = [value for value in names if value]
        if not query_key or not names:
            return 0
        ratios = [difflib.SequenceMatcher(None, query_key, name).ratio() for name in names]
        exact = query_key in names
        score = 100 if exact else int(max(ratios) * 75)
        date_key = "release_date" if media_type == "movie" else "first_air_date"
        candidate_year = _year(candidate.get(date_key))
        if requested_year and candidate_year:
            score += 18 if requested_year == candidate_year else -22
        return score

    def _search(self, media_type, title, requested_year):
        path = "/search/movie" if media_type == "movie" else "/search/tv"
        params = {"query": title, "page": 1, "include_adult": "false"}
        year_key = "primary_release_year" if media_type == "movie" else "first_air_date_year"
        attempts = (requested_year, "") if requested_year else ("",)
        languages = []
        for value in (self.language, "en-US"):
            if value not in languages:
                languages.append(value)
        for language in languages:
            for year_value in attempts:
                current = dict(params)
                if year_value:
                    current[year_key] = year_value
                payload = self._request_json(path, current, language)
                results = payload.get("results") or []
                if not isinstance(results, list):
                    results = []
                ranked = [
                    (
                        self._candidate_score(
                            title,
                            requested_year,
                            item,
                            media_type,
                        ),
                        item,
                    )
                    for item in results[:20]
                    if isinstance(item, dict)
                ]
                ranked.sort(key=lambda pair: pair[0], reverse=True)
                if ranked and ranked[0][0] >= 68:
                    return ranked[0][1]
        return None

    @staticmethod
    def _merge_payload(primary, fallback):
        result = dict(primary or {})
        for key, value in (fallback or {}).items():
            if result.get(key) in (None, "", [], {}):
                result[key] = value
        return result

    @staticmethod
    def _provider_identity_matches(media_type, title, requested_year, payload):
        """Reject a Stalker-provided TMDb ID when its record is unrelated.

        Some Ministra forks expose an internal numeric value under the generic
        ``tmdb`` key.  That value can be a valid ID for a completely different
        TMDb title, so an exact-ID response is only a hint until its title and
        optional year agree with the selected portal item.
        """
        if not isinstance(payload, dict):
            return False
        query = _normalised(clean_media_title(title))
        if len(query) < 2:
            return False
        # ``_detail_uncached`` maps both movie and TV original names to this
        # common result key so the verification path stays media agnostic.
        title_keys = ("title", "original_title")
        names = [
            _normalised(clean_media_title(payload.get(key)))
            for key in title_keys
        ]
        names = [value for value in names if value]
        if not names:
            return False
        exact = query in names
        similarity = max(
            difflib.SequenceMatcher(None, query, name).ratio()
            for name in names
        )
        if not exact and similarity < 0.84:
            return False
        expected_year = _year(requested_year) or _year(title)
        candidate_year = _year(payload.get("year"))
        if expected_year and candidate_year:
            try:
                if abs(int(expected_year) - int(candidate_year)) > 1:
                    return False
            except (TypeError, ValueError, OverflowError):
                return False
        return True

    @staticmethod
    def _valid_tmdb_id(value):
        value = str(value or "").strip()
        if not re.match(r"^[1-9][0-9]{0,11}$", value):
            return ""
        return value

    def _detail_uncached(self, media_type, tmdb_id, selected=None):
        path = "/{}/{}".format(
            "movie" if media_type == "movie" else "tv",
            tmdb_id,
        )
        detail = self._request_json(path, {}, self.language)
        detail = self._merge_payload(detail, selected or {})
        if not detail.get("overview"):
            english = self._request_json(path, {}, "en-US")
            detail = self._merge_payload(detail, english)
        date_key = "release_date" if media_type == "movie" else "first_air_date"
        title_key = "title" if media_type == "movie" else "name"
        genres = detail.get("genres") or []
        genre = ", ".join(
            _text(entry.get("name"))
            for entry in genres
            if isinstance(entry, dict) and _text(entry.get("name"))
        )
        runtime = ""
        if media_type == "movie":
            try:
                minutes = int(detail.get("runtime") or 0)
            except (TypeError, ValueError, OverflowError):
                minutes = 0
            if minutes > 0:
                runtime = "{} dk".format(minutes)
        return {
            "tmdb_id": str(detail.get("id") or tmdb_id),
            "title": _text(detail.get(title_key)),
            "original_title": _text(
                detail.get(
                    "original_title"
                    if media_type == "movie"
                    else "original_name"
                )
            ),
            "plot": _text(detail.get("overview"))[:2000],
            "rating": _format_rating(detail.get("vote_average")),
            "year": _year(detail.get(date_key)),
            "duration": runtime,
            "genre": genre[:180],
            "cover": _image_url(detail.get("poster_path"), "w500"),
            "backdrop": _image_url(detail.get("backdrop_path"), "w780"),
        }

    def _lookup_uncached(self, media_type, title, requested_year):
        selected = self._search(media_type, title, requested_year)
        if not selected or not selected.get("id"):
            return None
        return self._detail_uncached(
            media_type,
            str(selected["id"]),
            selected=selected,
        )

    def lookup(
        self,
        media_type,
        title,
        requested_year="",
        required_payload_version=1,
    ):
        if not self.available:
            self.last_error = (
                N_("TMDb metadata enrichment is disabled")
                if not self.enabled
                else N_("TMDb API key is not configured")
            )
            return None
        media_type = "series" if media_type == "series" else "movie"
        query = clean_media_title(title)
        requested_year = _year(requested_year) or _year(title)
        if len(_normalised(query)) < 2:
            self.last_error = N_("No valid title was found for TMDb")
            return None
        key = self._cache_key(media_type, query, requested_year)
        try:
            required_payload_version = max(
                1,
                int(required_payload_version),
            )
        except (TypeError, ValueError, OverflowError):
            required_payload_version = 1
        # Serialize duplicate requests for the same title, but let different
        # visible titles query TMDb in parallel. Network I/O must not hold the
        # global cache lock or every poster waits for the previous one.
        with self._lookup_guard(key):
            with self._lock:
                cached, value = self._cached(key)
                entry = self._load_cache().get(key) if cached else None
                try:
                    payload_version = int(
                        (entry or {}).get("payload_version", 1)
                    )
                except (TypeError, ValueError, OverflowError):
                    payload_version = 1
                if cached and (
                    not value
                    or payload_version >= required_payload_version
                ):
                    self.last_error = (
                        "" if value else N_("No TMDb match was found")
                    )
                    return value
            try:
                value = self._lookup_uncached(media_type, query, requested_year)
            except MetadataError as error:
                self.last_error = str(error)
                raise
            except Exception:
                self.last_error = N_("Could not load TMDb information")
                raise MetadataError(N_("Could not load TMDb information"))
            with self._lock:
                self._load_cache()[key] = {
                    "cached_at": self.clock(),
                    "found": bool(value),
                    "data": value or {},
                    "payload_version": CACHE_PAYLOAD_VERSION,
                }
                self._cache_changed()
            self.last_error = "" if value else N_("No TMDb match was found")
            return value

    def lookup_by_id(
        self,
        media_type,
        tmdb_id,
        required_payload_version=CACHE_PAYLOAD_VERSION,
    ):
        """Load an exact TMDb record when the IPTV provider supplies its ID."""
        if not self.available:
            self.last_error = (
                N_("TMDb metadata enrichment is disabled")
                if not self.enabled
                else N_("TMDb API key is not configured")
            )
            return None
        media_type = "series" if media_type == "series" else "movie"
        tmdb_id = self._valid_tmdb_id(tmdb_id)
        if not tmdb_id:
            self.last_error = N_("No valid title was found for TMDb")
            return None
        key = self._cache_key(
            media_type,
            "tmdb-id:{}".format(tmdb_id),
            "",
        )
        try:
            required_payload_version = max(
                1,
                int(required_payload_version),
            )
        except (TypeError, ValueError, OverflowError):
            required_payload_version = CACHE_PAYLOAD_VERSION
        with self._lookup_guard(key):
            with self._lock:
                cached, value = self._cached(key)
                entry = self._load_cache().get(key) if cached else None
                try:
                    payload_version = int(
                        (entry or {}).get("payload_version", 1)
                    )
                except (TypeError, ValueError, OverflowError):
                    payload_version = 1
                if cached and (
                    not value
                    or payload_version >= required_payload_version
                ):
                    self.last_error = (
                        "" if value else N_("No TMDb match was found")
                    )
                    return value
            try:
                value = self._detail_uncached(media_type, tmdb_id)
            except MetadataError as error:
                self.last_error = str(error)
                raise
            except Exception:
                self.last_error = N_("Could not load TMDb information")
                raise MetadataError(N_("Could not load TMDb information"))
            with self._lock:
                self._load_cache()[key] = {
                    "cached_at": self.clock(),
                    "found": bool(value),
                    "data": value or {},
                    "payload_version": CACHE_PAYLOAD_VERSION,
                }
                self._cache_changed()
            self.last_error = "" if value else N_("No TMDb match was found")
            return value

    def complete_movie(
        self,
        item,
        info=None,
        force_cover_fallback=False,
        require_backdrop=False,
        collect_backdrop_fallback=False,
    ):
        title = getattr(info, "title", "")
        if not title or title == N_("Movie"):
            title = getattr(item, "name", "")
        plot = getattr(info, "plot", "")
        rating = getattr(info, "rating", "") or getattr(item, "rating", "")
        year = getattr(info, "year", "") or getattr(item, "year", "")
        cover = (
            getattr(info, "cover", "")
            or getattr(item, "icon", "")
        )
        backdrop = getattr(info, "backdrop", "")
        tmdb_id = (
            getattr(info, "tmdb_id", "")
            or getattr(item, "tmdb_id", "")
        )
        external = None
        missing = (
            bool(force_cover_fallback)
            or not cover
            or not plot
            or not _has_rating(rating)
            or (bool(require_backdrop) and not backdrop)
            or bool(collect_backdrop_fallback)
        )
        if missing:
            required_version = (
                CACHE_PAYLOAD_VERSION if require_backdrop else 1
            )
            if tmdb_id:
                external = self.lookup_by_id(
                    "movie",
                    tmdb_id,
                    required_payload_version=required_version,
                )
                if (
                    external is not None
                    and getattr(item, "portal_request_type", "")
                    and not self._provider_identity_matches(
                        "movie",
                        title or getattr(item, "name", ""),
                        year,
                        external,
                    )
                ):
                    external = None
                    tmdb_id = ""
            if external is None:
                external = self.lookup(
                    "movie",
                    title or getattr(item, "name", ""),
                    year,
                    required_payload_version=required_version,
                )
        else:
            self.last_error = ""
        external = external or {}
        completed_cover = cover or external.get("cover")
        if force_cover_fallback and external.get("cover"):
            completed_cover = external.get("cover")
        completed = MovieInfo(
            title=title or external.get("title"),
            plot=plot or external.get("plot"),
            rating=rating if _has_rating(rating) else external.get("rating"),
            year=year or external.get("year"),
            duration=getattr(info, "duration", "") or external.get("duration"),
            genre=getattr(info, "genre", "") or external.get("genre"),
            director=getattr(info, "director", ""),
            cast=getattr(info, "cast", ""),
            cover=completed_cover,
            backdrop=backdrop or external.get("backdrop"),
            tmdb_id=tmdb_id or external.get("tmdb_id"),
        )
        external_backdrop = str(external.get("backdrop") or "").strip()
        completed.fallback_backdrop = (
            external_backdrop
            if external_backdrop and external_backdrop != completed.backdrop
            else ""
        )
        return completed

    def complete_movie_cinematic(self, item, info=None):
        """Complete one selected movie, including its optional wide artwork."""
        return self.complete_movie(
            item,
            info,
            require_backdrop=True,
            collect_backdrop_fallback=bool(
                getattr(item, "portal_request_type", "")
            ),
        )

    def complete_series(
        self,
        item,
        info=None,
        force_cover_fallback=False,
        require_backdrop=False,
        collect_backdrop_fallback=False,
    ):
        title = getattr(info, "title", "") or getattr(item, "name", "")
        plot = getattr(info, "plot", "")
        rating = getattr(info, "rating", "") or getattr(item, "rating", "")
        year = getattr(info, "year", "") or getattr(item, "year", "")
        cover = getattr(info, "cover", "") or getattr(item, "icon", "")
        backdrop = getattr(info, "backdrop", "")
        tmdb_id = (
            getattr(info, "tmdb_id", "")
            or getattr(item, "tmdb_id", "")
        )
        external = None
        missing = (
            bool(force_cover_fallback)
            or not cover
            or not plot
            or not _has_rating(rating)
            or (bool(require_backdrop) and not backdrop)
            or bool(collect_backdrop_fallback)
        )
        if missing:
            required_version = (
                CACHE_PAYLOAD_VERSION if require_backdrop else 1
            )
            if tmdb_id:
                external = self.lookup_by_id(
                    "series",
                    tmdb_id,
                    required_payload_version=required_version,
                )
                if (
                    external is not None
                    and getattr(item, "portal_request_type", "")
                    and not self._provider_identity_matches(
                        "series",
                        title or getattr(item, "name", ""),
                        year,
                        external,
                    )
                ):
                    external = None
                    tmdb_id = ""
            if external is None:
                external = self.lookup(
                    "series",
                    title or getattr(item, "name", ""),
                    year,
                    required_payload_version=required_version,
                )
        else:
            self.last_error = ""
        external = external or {}
        completed_cover = cover or external.get("cover")
        if force_cover_fallback and external.get("cover"):
            completed_cover = external.get("cover")
        completed = SeriesInfo(
            title=title or external.get("title"),
            plot=plot or external.get("plot"),
            rating=rating if _has_rating(rating) else external.get("rating"),
            year=year or external.get("year"),
            genre=getattr(info, "genre", "") or external.get("genre"),
            director=getattr(info, "director", ""),
            cast=getattr(info, "cast", ""),
            cover=completed_cover,
            episodes=getattr(info, "episodes", []) or [],
            backdrop=backdrop or external.get("backdrop"),
            tmdb_id=tmdb_id or external.get("tmdb_id"),
        )
        external_backdrop = str(external.get("backdrop") or "").strip()
        completed.fallback_backdrop = (
            external_backdrop
            if external_backdrop and external_backdrop != completed.backdrop
            else ""
        )
        return completed

    def complete_series_cinematic(self, item, info=None):
        """Complete one selected series, including its optional wide artwork."""
        return self.complete_series(
            item,
            info,
            require_backdrop=True,
            collect_backdrop_fallback=bool(
                getattr(item, "portal_request_type", "")
            ),
        )

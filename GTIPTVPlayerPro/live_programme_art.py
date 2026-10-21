# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Optional EPG posters with latest-selection priority and bounded caches."""

from collections import OrderedDict
import hashlib
import json
import os
import re
import stat
import struct
import threading
import time
import unicodedata
import uuid
from urllib.parse import urlencode

from .metadata import TMDbMetadataClient, clean_media_title
from .network_security import fetch_public_url
from .diagnostics import log_event


POSTER_CACHE_DIR = "/tmp/gtiptvplayer-programme-posters"
POSTER_CACHE_BYTES = 10 * 1024 * 1024
POSTER_MAX_BYTES = 512 * 1024
POSTER_TIMEOUT = 24.0
POSTER_DWELL_MS = 400
POSTER_RETRY_SECONDS = 30.0
TRANSIENT_CACHE_SECONDS = 20.0
IMAGE_TIMEOUT_SECONDS = 5.0
TMDB_LOOKUP_SECONDS = 5.0
TVMAZE_LOOKUP_SECONDS = 4.0
POSTER_WORKERS = 2
POSTER_READY_ENTRIES = 128
POSTER_READY_SECONDS = 21600
TVMAZE_RESPONSE_BYTES = 256 * 1024
TVMAZE_SEARCH_URL = "https://api.tvmaze.com/search/shows"
_CACHE_LOCK = threading.RLock()
_POSTER_NAME = re.compile(r"^[0-9a-f]{64}\.(?:jpg|png)$")
_TEMP_NAME = re.compile(r"^\.[0-9a-f]{32}\.tmp$")
_IMAGE_PATH = re.compile(r"^/[A-Za-z0-9_-]+\.(?:jpg|png)$", re.I)
_TVMAZE_IMAGE = re.compile(
    r"https://static\.tvmaze\.com/uploads/images/"
    r"(?:medium_portrait|original_untouched)/[0-9]+/[0-9]+\.(?:jpg|png)", re.I)
_STATUS = (r"yeni\s+b[oö]l[uü]m|yeni|tekrar|canl[iı]|new\s+episode|repeat|live|"
           r"wiederholung|neue\s+folge|dizi|series|film|movie")
_GENERIC = frozenset((
    "film", "movie", "movies", "cinema", "sinema", "dizi", "series", "serie", "serial",
    "news", "haber", "haberler", "ana haber", "nachrichten", "tagesschau", "journal",
    "sport", "sports", "spor", "futbol", "football", "canli", "live", "tekrar",
    "program", "programme", "programmi", "sendepause", "no programme information",
))


def title_key(value):
    value = unicodedata.normalize("NFKD", str(value or "").replace("ı", "i").casefold())
    return " ".join("".join(
        char if char.isalnum() else " " for char in value
        if not unicodedata.combining(char)
    ).split())


def programme_query(events, now=None):
    """Use only the current EPG event, never the channel name or next show."""
    now = time.time() if now is None else float(now)
    for event in list(events or ())[:16]:
        try:
            start = int(getattr(event, "start_timestamp", 0) or 0)
            end = int(getattr(event, "end_timestamp", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            continue
        if start > now or end and end <= now:
            continue
        if not (start and end and start <= now < end) and not getattr(event, "now_playing", False):
            continue
        raw = " ".join(str(getattr(event, "title", "") or "").split())[:256]
        kind = ""
        if re.search(r"\b(?:S\d{1,3}\s*E\d{1,4}|b[oö]l[uü]m|episode|folge)\b|\(\s*(?:dizi|series)\s*\)", raw, re.I):
            kind = "series"
        elif re.search(r"^\s*(?:film|movie|sinema)\s*[:|–-]", raw, re.I):
            kind = "movie"
        cleaned = re.sub(r"[\[({]\s*(?:" + _STATUS + r")\s*[\])}]", " ", raw, flags=re.I)
        cleaned = re.sub(r"\s*[|–-]\s*(?:" + _STATUS + r")\s*$", "", cleaned, flags=re.I)
        cleaned = re.sub(r"\s+(?:yeni\s+b[oö]l[uü]m|new\s+episode|neue\s+folge)\s*$",
                         "", cleaned, flags=re.I)
        cleaned = re.sub(r"\bfolge\s*\d{1,4}\b", " ", cleaned, flags=re.I)
        cleaned = re.sub(r"^\s*(?:film|movie|sinema)\s*[:|–-]\s*", "", cleaned, flags=re.I)
        year_match = re.search(r"[\[(]\s*((?:19|20)\d{2})\s*[\])]", cleaned)
        year = year_match.group(1) if year_match else ""
        title = clean_media_title(cleaned)[:180]
        key = title_key(title)
        if len(key) < 3 or key in _GENERIC:
            return None
        return {"title": title, "key": key, "year": year, "kind": kind,
                "event": (raw, start, end)}
    return None


def _image_suffix(body):
    """Accept small JPEG/PNG headers before passing bytes to the decoder."""
    width = height = 0
    suffix = ""
    try:
        if body.startswith(b"\x89PNG\r\n\x1a\n") and body[12:16] == b"IHDR" and len(body) >= 24:
            width, height = struct.unpack(">II", body[16:24])
            suffix = ".png"
        elif body.startswith(b"\xff\xd8"):
            offset = 2
            while offset + 4 <= len(body):
                if body[offset] != 255:
                    offset += 1
                    continue
                marker = body[offset + 1]
                offset += 2
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    continue
                length = struct.unpack(">H", body[offset:offset + 2])[0]
                if length < 2 or offset + length > len(body):
                    break
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
                              0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF) and length >= 7:
                    height, width = struct.unpack(">HH", body[offset + 3:offset + 7])
                    suffix = ".jpg"
                    break
                offset += length
    except (IndexError, struct.error, TypeError, ValueError):
        return ""
    return suffix if (0 < width <= 1024 and 0 < height <= 1536
                      and width * height <= 800000) else ""


class PosterFileCache(object):
    """Disk methods run on the worker; images and in-flight writes share 10 MiB."""

    def __init__(self, directory=POSTER_CACHE_DIR, maximum=POSTER_CACHE_BYTES):
        self.directory = os.path.abspath(directory)
        self.maximum = max(0, int(maximum))

    def _directory(self, create=False):
        try:
            if create:
                try:
                    os.mkdir(self.directory, 0o700)
                except FileExistsError:
                    pass
            status = os.lstat(self.directory)
            return (stat.S_ISDIR(status.st_mode) and status.st_uid == os.getuid()
                    and not status.st_mode & 0o022)
        except OSError:
            return False

    def _entries(self):
        files = []
        for name in os.listdir(self.directory):
            path = os.path.join(self.directory, name)
            status = os.lstat(path)
            if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
                continue
            if _TEMP_NAME.fullmatch(name):
                os.unlink(path)
            elif _POSTER_NAME.fullmatch(name):
                files.append((status.st_mtime, path, status.st_size))
        return sorted(files)

    def _prune(self, reserve=0):
        files = self._entries()
        total = sum(entry[2] for entry in files)
        for unused_mtime, path, size in files:
            if total + reserve <= self.maximum:
                break
            os.unlink(path)
            total -= size
        return total + reserve <= self.maximum

    @staticmethod
    def _digest(url):
        return hashlib.sha256(url.encode("utf-8")).hexdigest()

    def get(self, url):
        with _CACHE_LOCK:
            if not self._directory():
                return ""
            try:
                self._prune()
                for suffix in (".jpg", ".png"):
                    path = os.path.join(self.directory, self._digest(url) + suffix)
                    try:
                        flags = (os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
                                 | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_CLOEXEC", 0))
                        descriptor = os.open(path, flags)
                    except OSError:
                        continue
                    with os.fdopen(descriptor, "rb") as handle:
                        status = os.fstat(handle.fileno())
                        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1 or not 0 < status.st_size <= POSTER_MAX_BYTES:
                            continue
                        body = handle.read(POSTER_MAX_BYTES + 1)
                    if _image_suffix(body) == suffix:
                        os.utime(path, None, follow_symlinks=False)
                        return path
                    os.unlink(path)
            except OSError:
                pass
            return ""

    def put(self, url, body):
        suffix = _image_suffix(body)
        if not suffix or not 0 < len(body) <= min(POSTER_MAX_BYTES, self.maximum):
            return ""
        with _CACHE_LOCK:
            if not self._directory(create=True):
                return ""
            temporary = ""
            try:
                if not self._prune(reserve=len(body)):
                    return ""
                destination = os.path.join(self.directory, self._digest(url) + suffix)
                temporary = os.path.join(self.directory, ".{}.tmp".format(uuid.uuid4().hex))
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
                with os.fdopen(os.open(temporary, flags, 0o600), "wb") as handle:
                    handle.write(body)
                os.replace(temporary, destination)
                return destination
            except OSError:
                return ""
            finally:
                if temporary:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass


class PosterRequest(object):
    def __init__(self, context, query, options):
        self.context, self.query, self.options = context, dict(query), dict(options)
        self.cancelled, self.done = threading.Event(), threading.Event()
        self.created_at = time.monotonic()
        self.deadline = self.created_at + POSTER_TIMEOUT
        self.path = ""
        self.retryable = False
        self.retry_after = 0.0
        self.source = ""

    def cancel(self):
        self.cancelled.set()

    def active(self):
        return not self.cancelled.is_set() and time.monotonic() < self.deadline

    def finish(self, path=""):
        self.path = str(path or "") if self.active() else ""
        if not self.path and not self.active() and not self.cancelled.is_set():
            self.retryable = True
        self.done.set()


class _AmbiguousProgramme(Exception):
    """An exact namesake/remake must not be resolved by another provider."""


class ProgrammePosterService(object):
    """One latest pending selection, at most two workers, and a RAM-only fast path."""

    def __init__(self, cache=None, client_factory=None, downloader=None, tvmaze_fetcher=None):
        self.cache = cache if cache is not None else PosterFileCache()
        self.client_factory = client_factory or TMDbMetadataClient
        self.downloader = downloader or fetch_public_url
        self.tvmaze_fetcher = tvmaze_fetcher or fetch_public_url
        self._tvmaze_ready_at = 0.0
        self._tmdb_ready_at = 0.0
        self._condition = threading.Condition()
        self._pending = None
        self._active = set()
        self._threads = set()
        self._closed = False
        self._lookups = OrderedDict()
        self._ready = OrderedDict()

    @staticmethod
    def _programme_identity(query, options):
        return (options["language"], query["key"], query["year"], query["kind"],
                hashlib.sha256(str(options.get("api_key") or "").encode("utf-8")).hexdigest())

    def cached(self, query, options):
        """Return a previously validated path without disk or network work on the UI."""
        if not options.get("enabled"):
            return "", ""
        identity = self._programme_identity(query, options)
        with self._condition:
            entry = self._ready.get(identity)
            if self._closed or entry is None:
                return "", ""
            if entry[0] <= time.monotonic():
                self._ready.pop(identity, None)
                return "", ""
            self._ready.move_to_end(identity)
            return entry[1], entry[2]

    def forget(self, query, options, path):
        """A pruned file or failed decode must fall back to the bounded worker."""
        identity = self._programme_identity(query, options)
        with self._condition:
            entry = self._ready.get(identity)
            if entry is not None and entry[1] == path:
                self._ready.pop(identity, None)

    def _remember_ready(self, request, path):
        with self._condition:
            if self._closed or not request.active() or not path:
                return
            identity = self._programme_identity(request.query, request.options)
            self._ready[identity] = (time.monotonic() + POSTER_READY_SECONDS,
                                     path, request.source)
            self._ready.move_to_end(identity)
            while len(self._ready) > POSTER_READY_ENTRIES:
                self._ready.popitem(last=False)

    def submit(self, context, query, options):
        request = PosterRequest(context, query, options)
        with self._condition:
            if self._closed or not options.get("enabled"):
                request.finish()
                return request
            if self._pending is not None:
                self._pending.cancel()
                self._pending.finish()
            for active in self._active:
                active.cancel()
            path, source = self.cached(query, options)
            if path:
                self._pending = None
                request.source = source
                request.finish(path)
                return request
            self._pending = request
            # A cancelled socket may still be unwinding. One spare worker lets
            # the latest channel proceed, without creating a thread per zap.
            needed = min(POSTER_WORKERS, len(self._active) + 1)
            while len(self._threads) < needed:
                worker = threading.Thread(target=self._run, name="GT-EPG-Poster", daemon=True)
                self._threads.add(worker)
                try:
                    worker.start()
                except RuntimeError:
                    self._threads.discard(worker)
                    if not self._threads:
                        request.retryable = True
                        self._pending = None
                        request.finish()
                    break
            self._condition.notify_all()
        return request

    def close(self):
        with self._condition:
            self._closed = True
            for request in [self._pending] + list(self._active):
                if request is not None:
                    request.cancel()
                    request.finish()
            self._pending = None
            self._ready.clear()
            self._lookups.clear()
            self._condition.notify_all()

    def _run(self):
        try:
            while True:
                with self._condition:
                    while self._pending is None and not self._closed:
                        self._condition.wait()
                    if self._closed:
                        return
                    request, self._pending = self._pending, None
                    self._active.add(request)
                started = time.monotonic()
                log_event("programme_art", "stage=worker outcome=start queue_ms={} title={}".format(
                    int((started - request.created_at) * 1000), request.query["title"]))
                path = ""
                try:
                    if request.active():
                        path = self._resolve(request)
                    self._remember_ready(request, path)
                except Exception as error:
                    # Optional artwork failures never interrupt playback.
                    request.retryable = request.active()
                    if not request.cancelled.is_set():
                        log_event("programme_art", "stage=worker outcome=error title={}".format(
                            request.query.get("title", "")), error)
                finally:
                    request.finish(path)
                    log_event("programme_art", "stage=worker outcome={} elapsed_ms={} title={}".format(
                        "cancelled" if request.cancelled.is_set() else "ready" if request.path
                        else "timeout" if not request.active() else "empty",
                        int((time.monotonic() - request.created_at) * 1000), request.query["title"]))
                    with self._condition:
                        self._active.discard(request)
        finally:
            with self._condition:
                self._threads.discard(threading.current_thread())

    def _remember(self, key, value, seconds):
        with self._condition:
            if self._closed:
                return
            self._lookups[key] = (time.monotonic() + seconds, value)
            self._lookups.move_to_end(key)
            while len(self._lookups) > 128:
                self._lookups.popitem(last=False)

    def _cached_lookup(self, key):
        with self._condition:
            cached = self._lookups.get(key)
            if cached is None:
                return None
            if cached[0] <= time.monotonic():
                self._lookups.pop(key, None)
                return None
            self._lookups.move_to_end(key)
            return cached

    def _find_cover(self, client, request):
        query = request.query
        known_kind = query["kind"] if query["kind"] in ("series", "movie") else ""
        # Multi-search returns both types in one response. Preserve exact
        # title/year checks and reject movie/TV collisions before downloading.
        route = ("/search/tv" if known_kind == "series" else "/search/movie"
                 if known_kind == "movie" else "/search/multi")
        for language in dict.fromkeys((request.options["language"], "en-US")):
            if not request.active():
                return "", ""
            matches = {}
            params = {"query": query["title"], "page": 1, "include_adult": "false"}
            if query["year"] and known_kind:
                params["first_air_date_year" if known_kind == "series" else "primary_release_year"] = query["year"]
            payload = client._request_json(route, params, language)
            results = payload.get("results") if isinstance(payload, dict) else None
            if not request.active():
                return "", ""
            for result in results[:20] if isinstance(results, list) else ():
                if not isinstance(result, dict) or result.get("adult"):
                    continue
                kind = known_kind or {"tv": "series", "movie": "movie"}.get(result.get("media_type"))
                if kind not in ("series", "movie"):
                    continue
                names = (result.get("name"), result.get("original_name")) if kind == "series" else (result.get("title"), result.get("original_title"))
                if query["key"] not in [title_key(name) for name in names]:
                    continue
                date = str(result.get("first_air_date" if kind == "series" else "release_date") or "")
                if query["year"] and not date.startswith(query["year"] + "-"):
                    continue
                identity = str(result.get("id") or "")
                if not identity.isdigit():
                    continue
                path = str(result.get("poster_path") or "")
                matches[(kind, identity)] = path if _IMAGE_PATH.fullmatch(path) else ""
            if matches:
                # Remakes, namesakes and movie/TV collisions need more EPG
                # information. Leave the poster area empty rather than guess.
                if len(matches) != 1:
                    raise _AmbiguousProgramme()
                (kind, unused_id), path = next(iter(matches.items()))
                return ("https://image.tmdb.org/t/p/w500" + path if path else ""), kind
        return "", ""

    def _find_tvmaze_cover(self, request):
        while request.active():
            with self._condition:
                delay = self._tvmaze_ready_at - time.monotonic()
                if delay <= 0:
                    self._tvmaze_ready_at = time.monotonic() + 1.0
                    break
            # Normal pacing is at most one second and is interrupted as
            # soon as the selected programme changes. Error backoff never
            # occupies this shared worker for a long wait.
            if delay > 1.0:
                request.retryable = True
                request.retry_after = self._tvmaze_ready_at
                return None
            if request.cancelled.wait(max(0, min(delay, request.deadline - time.monotonic()))):
                return None
        else:
            return None
        try:
            url = TVMAZE_SEARCH_URL + "?" + urlencode({"q": request.query["title"]})
            remaining = min(TVMAZE_LOOKUP_SECONDS, request.deadline - time.monotonic())
            if remaining <= 0:
                return None
            body = self.tvmaze_fetcher(
                url, TVMAZE_RESPONSE_BYTES, remaining, "application/json",
                "GT-IPTV-Programme-Art", max_redirects=0,
                cancel_event=request.cancelled)[0]
            if not request.active():
                return None
            if len(body) > TVMAZE_RESPONSE_BYTES:
                raise ValueError("TVmaze response too large")
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, list):
                raise ValueError("Invalid TVmaze search response")
        except Exception as error:
            if not request.active():
                return None
            # Covers 429/offline/invalid responses. Defer the next request
            # without sleeping in the worker or retrying a failing endpoint.
            rate_limited = getattr(error, "status", None) == 429
            with self._condition:
                self._tvmaze_ready_at = max(self._tvmaze_ready_at,
                    time.monotonic() + (60.0 if rate_limited else 10.0))
                request.retry_after = max(request.retry_after, self._tvmaze_ready_at)
            raise
        matches = {}
        query = request.query
        for entry in payload[:20]:
            show = entry.get("show") if isinstance(entry, dict) else None
            if not isinstance(show, dict) or title_key(show.get("name")) != query["key"]:
                continue
            identity = str(show.get("id") or "")
            if not identity.isdigit():
                continue
            if query["year"] and not str(show.get("premiered") or "").startswith(query["year"] + "-"):
                continue
            image = show.get("image")
            image = image if isinstance(image, dict) else {}
            urls = [str(image.get(size) or "") for size in ("medium", "original")]
            matches[identity] = next((url for url in urls if _TVMAZE_IMAGE.fullmatch(url)), "")
        if len(matches) > 1:
            raise _AmbiguousProgramme()
        return next(iter(matches.values()), "")

    def _lookup_cover(self, request, source):
        started = time.monotonic()
        query, options = request.query, request.options
        api_key = str(options.get("api_key") or "")
        if source == "tmdb" and not api_key:
            return "", ""
        identity = (source, options["language"], query["key"], query["year"], query["kind"],
                    hashlib.sha256(api_key.encode("utf-8")).hexdigest() if source == "tmdb" else "")
        cached = self._cached_lookup(identity)
        if cached is not None:
            if cached[1][1] == "transient":
                request.retryable = True
                request.retry_after = max(request.retry_after, cached[0],
                                          self._tvmaze_ready_at if source == "tvmaze" else 0.0)
            log_event("programme_art", "stage=lookup source={} outcome=cached-{} elapsed_ms={} title={}".format(
                source, "match" if cached[1][0] else cached[1][1] or "empty",
                int((time.monotonic() - started) * 1000), query["title"]))
            return cached[1]
        with self._condition:
            if source == "tmdb" and self._tmdb_ready_at > time.monotonic():
                request.retryable = True
                request.retry_after = max(request.retry_after, self._tmdb_ready_at)
                return "", "transient"
        seconds = 7200
        try:
            if source == "tmdb":
                client = self.client_factory(api_key=api_key, enabled=True,
                                             language=options["language"], timeout=2.5)
                deadline = min(request.deadline, time.monotonic() + TMDB_LOOKUP_SECONDS)
                with client.request_scope(cancel_event=request.cancelled, deadline=deadline):
                    value = self._find_cover(client, request)
            else:
                url = self._find_tvmaze_cover(request)
                if url is None:
                    return "", ""
                value = url, "series"
            if value[0]:
                seconds = 21600
        except _AmbiguousProgramme:
            value = "", "ambiguous"
        except Exception as error:
            if not request.active():
                return "", ""
            request.retryable = True
            value, seconds = ("", "transient"), TRANSIENT_CACHE_SECONDS
            if source == "tmdb" and getattr(error, "http_status", None) == 429:
                with self._condition:
                    self._tmdb_ready_at = max(self._tmdb_ready_at, time.monotonic() + 60.0)
                    request.retry_after = max(request.retry_after, self._tmdb_ready_at)
            log_event("programme_art", "stage=lookup source={} outcome=error title={} reason={}".format(
                source, query["title"], str(error)[:180]), error)
        if not request.active():
            return "", ""
        # Small URL/source records stay in RAM; no EPG metadata goes to flash.
        self._remember(identity, value, seconds)
        log_event("programme_art", "stage=lookup source={} outcome={} elapsed_ms={} title={}".format(
            source, "match" if value[0] else "no-match" if value[1] in ("", "series", "movie") else value[1],
            int((time.monotonic() - started) * 1000), query["title"]))
        return value

    def _download_cover(self, request, url):
        started = time.monotonic()
        if not url or not request.active():
            return ""
        source = "tvmaze" if _TVMAZE_IMAGE.fullmatch(url) else "tmdb"
        path = self.cache.get(url)
        if path:
            request.source = source
            log_event("programme_art", "stage=image source={} outcome=cached elapsed_ms={} title={}".format(
                source, int((time.monotonic() - started) * 1000), request.query["title"]))
            return path if request.active() else ""
        failure_key = ("image", url)
        failure = self._cached_lookup(failure_key)
        if failure is not None:
            request.retryable = True
            request.retry_after = max(request.retry_after, failure[0])
            return ""
        try:
            remaining = min(IMAGE_TIMEOUT_SECONDS, request.deadline - time.monotonic())
            if remaining <= 0 or not request.active():
                return ""
            body = self.downloader(url, POSTER_MAX_BYTES, remaining,
                                   "image/jpeg,image/png", "GT-IPTV-Programme-Art", max_redirects=0,
                                   cancel_event=request.cancelled)[0]
            if request.active():
                path = self.cache.put(url, body)
                if path:
                    request.source = source
                    log_event("programme_art", "stage=image source={} outcome=ready bytes={} elapsed_ms={} title={}".format(
                        source, len(body), int((time.monotonic() - started) * 1000), request.query["title"]))
                    return path
                log_event("programme_art", "stage=image source={} outcome=rejected-image-or-cache title={}".format(
                    source, request.query["title"]))
        except Exception as error:
            if request.active():
                log_event("programme_art", "stage=image source={} outcome=error elapsed_ms={} title={} reason={}".format(
                    source, int((time.monotonic() - started) * 1000), request.query["title"], str(error)[:180]), error)
        if request.active():
            request.retryable = True
            self._remember(failure_key, ("", ""), TRANSIENT_CACHE_SECONDS)
        return ""

    def _resolve(self, request):
        if not request.active() or not request.options.get("enabled"):
            return ""
        url, kind = self._lookup_cover(request, "tmdb")
        if kind == "ambiguous" or not request.active():
            return ""
        path = self._download_cover(request, url)
        if path or kind == "movie" or request.query["kind"] == "movie" or not request.active():
            return path
        url, unused_kind = self._lookup_cover(request, "tvmaze")
        return self._download_cover(request, url)


_SERVICE = None
_SERVICE_LOCK = threading.Lock()


def shared_programme_posters():
    global _SERVICE
    with _SERVICE_LOCK:
        if _SERVICE is None:
            _SERVICE = ProgrammePosterService()
        return _SERVICE

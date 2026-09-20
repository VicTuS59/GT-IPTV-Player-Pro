# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import base64
import html
import json
import re
import socket
import threading
import time
import unicodedata
from collections import OrderedDict
from contextlib import contextmanager
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.request import Request

from . import PLUGIN_VERSION
from .diagnostics import log_event
from .http_transport import build_credential_urlopen
from .i18n import N_, _
from .portal_identity import safe_composite_id
from .category_visibility import category_ids_visible, hidden_category_match_ids


DEFAULT_TIMEOUT = 12
MAX_API_BYTES = 16 * 1024 * 1024
MAX_RESULTS = 20000
MAX_CATALOG_RESULTS = 50000
CONTENT_CACHE_SECONDS = 180
CONTENT_CACHE_MAX_ENTRIES = 3
CONTENT_CACHE_MAX_ITEMS = 5000
MAX_ACCOUNT_API_REQUESTS = 2
API_READ_CHUNK_BYTES = 64 * 1024
MAX_PROVIDER_INTEGER_DIGITS = 128
MAX_PROVIDER_JSON_NESTING = 128
LIVE_INITIAL_ITEMS = 36
LIVE_MORE_ITEMS = 60

CONTENT_LABELS = {
    "live": (N_("Live TV"), N_("All Channels")),
    "movie": (N_("Movies"), N_("All Movies")),
    "series": (N_("Series"), N_("All Series")),
}

CATEGORY_ACTIONS = {
    "live": "get_live_categories",
    "movie": "get_vod_categories",
    "series": "get_series_categories",
}

ITEM_ACTIONS = {
    "live": "get_live_streams",
    "movie": "get_vod_streams",
    "series": "get_series",
}


def _bounded_provider_integer(value):
    """Decode one JSON integer without allowing unbounded decimal work."""
    digit_count = len(value) - (1 if value.startswith("-") else 0)
    if digit_count > MAX_PROVIDER_INTEGER_DIGITS:
        raise ValueError("provider JSON integer is too long")
    return int(value, 10)


def _validate_provider_json_depth(value):
    """Reject provider container trees that exceed the fixed depth budget."""
    if not isinstance(value, (dict, list)):
        return value
    children = value.values() if isinstance(value, dict) else value
    pending = [iter(children)]
    while pending:
        try:
            child = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        if not isinstance(child, (dict, list)):
            continue
        if len(pending) >= MAX_PROVIDER_JSON_NESTING:
            raise ValueError("provider JSON nesting is too deep")
        children = child.values() if isinstance(child, dict) else child
        pending.append(iter(children))
    return value


class ContentError(Exception):
    """Provider failure with a stable, machine-readable reason code.

    The message remains the first exception argument, so existing screens and
    third-party callers which only use ``str(error)`` keep their old behaviour.
    """

    UNKNOWN = "unknown"
    CANCELLED = "cancelled"
    DEADLINE = "deadline"
    UNSUPPORTED_TYPE = "unsupported_type"
    ACCESS_DENIED = "access_denied"
    RATE_LIMITED = "rate_limited"
    HTTP_ERROR = "http_error"
    CONNECTION = "connection"
    RESPONSE_TOO_LARGE = "response_too_large"
    INVALID_RESPONSE = "invalid_response"
    CATEGORY_RESPONSE = "category_response"
    CONTENT_RESPONSE = "content_response"
    CATALOG_ITEM_LIMIT = "catalog_item_limit"
    INVALID_MOVIE_ID = "invalid_movie_id"
    MOVIE_INFO_RESPONSE = "movie_info_response"
    INVALID_SERIES_ID = "invalid_series_id"
    SERIES_INFO_RESPONSE = "series_info_response"
    SERIES_EPISODES_RESPONSE = "series_episodes_response"
    INVALID_CHANNEL_ID = "invalid_channel_id"
    INVALID_PLAYBACK_ITEM = "invalid_playback_item"
    PLAYBACK_URL = "playback_url"

    def __init__(self, message="", reason=UNKNOWN, http_status=None):
        Exception.__init__(self, message)
        self.reason = str(reason or self.UNKNOWN)
        # ``reason_code`` is deliberately an alias: it reads clearly at call
        # sites while ``reason`` remains convenient for older integrations.
        self.reason_code = self.reason
        self.http_status = http_status


class ContentCategory(object):
    def __init__(self, category_id, name):
        self.category_id = str(category_id or "").strip()
        self.name = _clean_text(name, _("Category"))


class ContentItem(object):
    def __init__(
        self,
        content_type,
        stream_id,
        name,
        category_id="",
        extension="",
        icon="",
        season="",
        episode="",
        rating="",
        year="",
        duration="",
        plot="",
        parent_id="",
        tmdb_id="",
    ):
        self.content_type = str(content_type or "").strip().lower()
        self.stream_id = str(stream_id or "").strip()
        self.name = _clean_text(name, _("Untitled content"))
        self.category_id = str(category_id or "").strip()
        self.extension = _safe_extension(extension, self.content_type)
        self.icon = str(icon or "").strip()
        self.season = str(season or "").strip()
        self.episode = str(episode or "").strip()
        self.rating = _clean_text(rating)
        self.year = _clean_text(year)
        self.duration = _clean_text(duration)
        self.plot = _decode_epg_description(plot)
        self.tmdb_id = str(tmdb_id or "").strip()[:32]
        raw_parent_id = str(parent_id or "")
        numeric_parent_id = raw_parent_id.strip()
        if numeric_parent_id.isdigit():
            # Keep the established Xtream/numeric behaviour unchanged.
            self.parent_id = numeric_parent_id[:40]
        else:
            # Composite IDs are Stalker series parents.  They are validated
            # and kept intact so episode/favorite reconstruction is lossless.
            self.parent_id = (
                safe_composite_id(raw_parent_id)
                if self.content_type == "series"
                else ""
            )


class MovieSearchPage(object):
    """One deterministic page from a provider movie-name search."""

    def __init__(
        self,
        items=None,
        page=1,
        page_size=14,
        total_items=0,
        has_more=False,
        source="",
        supported=True,
    ):
        try:
            page = int(page)
        except (TypeError, ValueError, OverflowError):
            page = 1
        try:
            page_size = int(page_size)
        except (TypeError, ValueError, OverflowError):
            page_size = 14
        try:
            total_items = int(total_items)
        except (TypeError, ValueError, OverflowError):
            total_items = 0
        self.items = list(items or [])
        self.page = max(1, page)
        self.page_size = max(1, page_size)
        self.total_items = max(0, total_items)
        self.has_more = bool(has_more)
        self.source = str(source or "")
        self.supported = bool(supported)


class MovieInfo(object):
    """Normalised Xtream VOD metadata used by the television movie screen."""

    def __init__(
        self,
        title="",
        plot="",
        rating="",
        year="",
        duration="",
        genre="",
        director="",
        cast="",
        cover="",
        backdrop="",
        tmdb_id="",
    ):
        self.title = _clean_text(title, _("Movie"))
        self.plot = _decode_epg_description(plot)
        self.rating = _clean_text(rating)
        self.year = _clean_text(year)
        self.duration = _clean_text(duration)
        self.genre = _clean_text(genre)
        self.director = _clean_text(director)
        self.cast = _decode_epg_description(cast)[:500]
        self.cover = str(cover or "").strip()
        self.backdrop = str(backdrop or "").strip()
        self.tmdb_id = str(tmdb_id or "").strip()[:32]


class SeriesInfo(object):
    """Normalised Xtream series metadata plus its playable episodes."""

    def __init__(
        self,
        title="",
        plot="",
        rating="",
        year="",
        genre="",
        director="",
        cast="",
        cover="",
        episodes=None,
        backdrop="",
        tmdb_id="",
    ):
        self.title = _clean_text(title)
        self.plot = _decode_epg_description(plot)
        self.rating = _clean_text(rating)
        self.year = _clean_text(year)
        self.genre = _clean_text(genre)
        self.director = _clean_text(director)
        self.cast = _decode_epg_description(cast)[:500]
        self.cover = str(cover or "").strip()
        self.episodes = list(episodes or [])
        self.backdrop = str(backdrop or "").strip()
        self.tmdb_id = str(tmdb_id or "").strip()[:32]

    @property
    def seasons(self):
        """Return unique season identifiers in provider episode order."""
        values = []
        for episode in self.episodes:
            season = str(getattr(episode, "season", "") or "").strip()
            if season and season not in values:
                values.append(season)
        return values


class EPGEvent(object):
    def __init__(
        self,
        title,
        start="",
        end="",
        now_playing=False,
        description="",
    ):
        self.title = _decode_epg_text(title) or _("No programme information")
        self.description = _decode_epg_description(description)
        self.start_timestamp = _epg_timestamp(start)
        self.end_timestamp = _epg_timestamp(end)
        self.start = _epg_clock(start)
        self.end = _epg_clock(end)
        self.now_playing = bool(now_playing)

    def time_text(self):
        if self.start and self.end:
            return "{}-{}".format(self.start, self.end)
        return self.start or self.end


def _clean_text(value, fallback=""):
    text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
    return (text or fallback)[:180]


def _movie_search_text(value):
    """Return a compact, language-neutral key for local Xtream searching."""
    value = str(value or "").replace("ı", "i").casefold()
    value = unicodedata.normalize("NFKD", value)
    value = "".join(
        character
        for character in value
        if not unicodedata.combining(character)
    )
    return " ".join(
        "".join(
            character if character.isalnum() else " "
            for character in value
        ).split()
    )


def _safe_extension(value, content_type):
    extension = str(value or "").lower().lstrip(".")
    if not extension or not extension.replace("_", "").isalnum():
        return "ts" if content_type == "live" else "mp4"
    return extension[:12]


def _normalise_media_url(value, account=None):
    """Return a decoder-safe absolute HTTP(S) URL from Xtream media fields."""
    if isinstance(value, (list, tuple)):
        for entry in value:
            normalised = _normalise_media_url(entry, account)
            if normalised:
                return normalised
        return ""
    if isinstance(value, dict):
        for key in ("url", "path", "src", "image"):
            if key in value:
                return _normalise_media_url(value.get(key), account)
        return ""
    if isinstance(value, bytes):
        raw = value.decode("utf-8", "replace")
    else:
        raw = str(value or "")
    raw = html.unescape(raw).strip().strip("\"'").replace("\\/", "/")
    if not raw:
        return ""

    scheme = str(getattr(account, "scheme", "") or "http").lower()
    netloc = str(getattr(account, "netloc", "") or "")
    base_path = str(getattr(account, "base_path", "") or "")
    try:
        raw_scheme = urlsplit(raw).scheme
    except Exception:
        return ""
    if raw.startswith("//"):
        raw = "{}:{}".format(scheme, raw)
    elif raw.startswith("/") and netloc:
        raw = urlunsplit((scheme, netloc, raw, "", ""))
    elif netloc and not raw_scheme:
        base = urlunsplit(
            (scheme, netloc, base_path.rstrip("/") + "/", "", "")
        )
        raw = urljoin(base, raw)

    try:
        parsed = urlsplit(raw)
    except Exception:
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return ""
    path = quote(parsed.path, safe="/%:@!$&'()*+,;=-._~")
    query = quote(parsed.query, safe="=&?/:;+,%@[]!$'()*-._~")
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, path, query, parsed.fragment)
    )


def _numeric_id(value):
    candidate = str(value or "").strip()
    return candidate if candidate.isdigit() else ""


def _decode_epg_text(value):
    if isinstance(value, bytes):
        raw = value.decode("utf-8", "replace")
    else:
        raw = str(value or "")
    raw = raw.strip()
    if not raw:
        return ""
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        if decoded and all(character.isprintable() for character in decoded):
            return _clean_text(decoded)
    except (TypeError, ValueError, UnicodeError):
        pass
    return _clean_text(raw)


def _decode_epg_description(value):
    """Decode provider EPG summaries without truncating them like titles."""
    if isinstance(value, bytes):
        raw = value.decode("utf-8", "replace")
    else:
        raw = str(value or "")
    raw = raw.strip()
    if not raw:
        return ""
    try:
        decoded = base64.b64decode(raw, validate=True).decode("utf-8")
        if decoded and all(character.isprintable() for character in decoded):
            raw = decoded
    except (TypeError, ValueError, UnicodeError):
        pass
    return " ".join(raw.replace("\r", " ").replace("\n", " ").split())[:2000]


def _epg_clock(value):
    raw = str(value or "").strip()
    if not raw:
        return ""
    if raw.isdigit():
        try:
            return time.strftime("%H:%M", time.localtime(int(raw)))
        except (OverflowError, OSError, ValueError):
            return ""
    match = re.search(r"(?:^|[ T])(\d{1,2}:\d{2})(?::\d{2})?", raw)
    return match.group(1).zfill(5) if match else ""


def _epg_timestamp(value):
    raw = str(value or "").strip()
    if not raw:
        return 0
    if raw.isdigit():
        try:
            timestamp = int(raw)
            if timestamp > 100000000000:
                timestamp //= 1000
            return timestamp
        except (OverflowError, TypeError, ValueError):
            return 0
    normalised = raw.replace("T", " ").split("+", 1)[0].rstrip("Z")
    for pattern in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return int(time.mktime(time.strptime(normalised, pattern)))
        except (OverflowError, TypeError, ValueError):
            continue
    return 0


def _truthy(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _select_current_epg_window(events, limit=2, now=None):
    """Return the current event and its chronological successors.

    Providers do not consistently order EPG rows.  In particular, some
    Provider variants may return the newest entry first. Selecting an
    index before normalising that order can therefore present an expired
    programme as "now" and discard the real current event.
    """
    events = list(events or [])
    if not events:
        return []
    try:
        limit = max(1, min(12, int(limit)))
    except (TypeError, ValueError):
        limit = 2
    now = int(time.time() if now is None else now)

    # Preserve provider order when timestamps are unavailable.  When at
    # least two rows are timestamped, use a stable chronological order and
    # leave timestamp-less rows at the end.
    timestamped = sum(
        1 for event in events
        if int(getattr(event, "start_timestamp", 0) or 0) > 0
    )
    explicit_untimed_current = any(
        event.now_playing
        and int(getattr(event, "start_timestamp", 0) or 0) <= 0
        for event in events
    )
    if timestamped >= 2 and not explicit_untimed_current:
        indexed = list(enumerate(events))
        indexed.sort(
            key=lambda pair: (
                0
                if int(getattr(pair[1], "start_timestamp", 0) or 0) > 0
                else 1,
                int(getattr(pair[1], "start_timestamp", 0) or 0),
                pair[0],
            )
        )
        events = [entry[1] for entry in indexed]

    selected_index = None
    for index, event in enumerate(events):
        if event.now_playing:
            selected_index = index
            break
    if selected_index is None:
        for index, event in enumerate(events):
            if (
                event.start_timestamp
                and event.end_timestamp
                and event.start_timestamp <= now < event.end_timestamp
            ):
                selected_index = index
                break
    if selected_index is None:
        for index, event in enumerate(events):
            if event.start_timestamp and event.start_timestamp >= now:
                selected_index = index
                break
    if selected_index is None:
        # Timestamp-less provider rows are still useful.  Timestamped rows
        # that are all expired are not: returning them would label old EPG as
        # the current programme.
        if any(
            int(getattr(event, "start_timestamp", 0) or 0) > 0
            for event in events
        ):
            return []
        selected_index = 0
    return events[selected_index : selected_index + limit]


class XtreamContentClient(object):
    def __init__(self, account, timeout=DEFAULT_TIMEOUT, opener=None, clock=None):
        self.account = account
        self.timeout = timeout
        self.opener = opener or build_credential_urlopen(
            ("username", "password", "token")
        )
        self.clock = clock or time.monotonic
        self._request_slots = threading.BoundedSemaphore(
            MAX_ACCOUNT_API_REQUESTS
        )
        self._request_context = threading.local()
        self._cache_lock = threading.RLock()
        self._category_cache = {}
        self._item_cache = OrderedDict()
        self._live_item_states = OrderedDict()
        self._live_item_loads = {}
        self._live_item_epoch = 0
        self._epg_preferred_action = ""
        # Movie search has a separate, screen-lifetime cache.  Compact tuples
        # avoid retaining thousands of ContentItem instances; only the page
        # being displayed is normalised into UI objects.
        self._movie_search_lock = threading.RLock()
        self._movie_search_generation = 0
        self._movie_search_catalog = None
        self._movie_search_query = ""
        self._movie_search_matches = None
        self._movie_search_load = None

    @contextmanager
    def request_scope(self, cancel_event=None, deadline=None, timeout=None):
        """Apply cancellation, deadline and optional socket timeout to a job."""
        if timeout is not None:
            try:
                timeout = max(0.25, float(timeout))
            except (TypeError, ValueError):
                timeout = None
        previous = getattr(self._request_context, "limits", None)
        if previous is not None:
            previous_event, previous_deadline, previous_timeout = previous
            if cancel_event is None:
                cancel_event = previous_event
            if previous_deadline is not None:
                deadline = (
                    previous_deadline
                    if deadline is None
                    else min(float(deadline), float(previous_deadline))
                )
            if timeout is None:
                timeout = previous_timeout
            elif previous_timeout is not None:
                timeout = min(timeout, float(previous_timeout))
        self._request_context.limits = (cancel_event, deadline, timeout)
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

    def _request_limits(self):
        return getattr(self._request_context, "limits", (None, None, None))

    def _check_request_limits(self):
        cancel_event, deadline, unused_timeout = self._request_limits()
        if cancel_event is not None and cancel_event.is_set():
            raise ContentError(
                N_("Request cancelled"),
                ContentError.CANCELLED,
            )
        if deadline is not None and time.monotonic() >= float(deadline):
            raise ContentError(
                N_("Request timed out"),
                ContentError.DEADLINE,
            )

    def _request_timeout(self):
        unused_event, deadline, scoped_timeout = self._request_limits()
        request_timeout = (
            scoped_timeout if scoped_timeout is not None else self.timeout
        )
        if deadline is None:
            return request_timeout
        remaining = float(deadline) - time.monotonic()
        if remaining <= 0:
            raise ContentError(
                N_("Request timed out"),
                ContentError.DEADLINE,
            )
        return max(0.25, min(float(request_timeout), remaining))

    def _acquire_request_slot(self):
        cancel_event, deadline, unused_timeout = self._request_limits()
        if cancel_event is None and deadline is None:
            self._request_slots.acquire()
            return
        while True:
            self._check_request_limits()
            remaining = (
                0.1
                if deadline is None
                else max(0.01, float(deadline) - time.monotonic())
            )
            if self._request_slots.acquire(timeout=min(0.1, remaining)):
                return

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

    def _read_response_body(self, response):
        """Read a bounded body while enforcing the UI job's total deadline."""
        reader = getattr(response, "read1", None)
        if not callable(reader):
            reader = response.read
        chunks = []
        total = 0
        limit = MAX_API_BYTES + 1
        while total < limit:
            self._check_request_limits()
            self._set_response_timeout(response, self._request_timeout())
            size = min(API_READ_CHUNK_BYTES, limit - total)
            chunk = reader(size)
            self._check_request_limits()
            if not chunk:
                break
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8")
            elif not isinstance(chunk, bytes):
                chunk = bytes(chunk)
            chunks.append(chunk)
            total += len(chunk)
        return b"".join(chunks)

    @staticmethod
    def _validate_type(content_type):
        value = str(content_type or "").lower()
        if value not in CONTENT_LABELS:
            raise ContentError(
                N_("Unsupported content type"),
                ContentError.UNSUPPORTED_TYPE,
            )
        return value

    def _request_json(self, action, parameters=None):
        request = Request(
            self.account.api_url(action, parameters),
            headers={
                "Accept": "application/json",
                "User-Agent": "GT-IPTV-Player-Pro/{}".format(PLUGIN_VERSION),
            },
        )
        acquired = False
        try:
            self._check_request_limits()
            self._acquire_request_slot()
            acquired = True
            try:
                self._check_request_limits()
                response = self.opener(
                    request,
                    timeout=self._request_timeout(),
                )
                try:
                    body = self._read_response_body(response)
                finally:
                    close = getattr(response, "close", None)
                    if close is not None:
                        close()
                self._check_request_limits()
            finally:
                if acquired:
                    self._request_slots.release()
                    acquired = False
        except HTTPError as error:
            # HTTPError also owns the response body/socket. Archive failures
            # must release it just like successful API responses do.
            try:
                error.close()
            except Exception:
                pass
            log_event("content", N_("API HTTP error action={}").format(action), error)
            if error.code in (401, 403):
                raise ContentError(
                    N_("Server denied access to content"),
                    ContentError.ACCESS_DENIED,
                    error.code,
                )
            if error.code == 429:
                raise ContentError(
                    N_("Server request limit reached"),
                    ContentError.RATE_LIMITED,
                    error.code,
                )
            raise ContentError(
                _(N_("Server returned HTTP {}")).format(error.code),
                ContentError.HTTP_ERROR,
                error.code,
            )
        except (URLError, socket.timeout, OSError) as error:
            log_event(
                "content",
                N_("API connection error action={}").format(action),
                error,
            )
            raise ContentError(
                N_("Could not connect to the content server"),
                ContentError.CONNECTION,
            )
        finally:
            if acquired:
                self._request_slots.release()

        if len(body) > MAX_API_BYTES:
            raise ContentError(
                N_("Server content list is too large"),
                ContentError.RESPONSE_TOO_LARGE,
            )
        if not isinstance(body, str):
            body = body.decode("utf-8", "replace")
        try:
            payload = json.loads(body, parse_int=_bounded_provider_integer)
            _validate_provider_json_depth(payload)
        except (TypeError, ValueError, RecursionError):
            log_event("content", N_("Invalid JSON action={}").format(action))
            raise ContentError(
                N_("Server returned an invalid content response"),
                ContentError.INVALID_RESPONSE,
            )
        self._check_request_limits()
        return payload

    def _cached_value(self, mapping, key):
        with self._cache_lock:
            entry = mapping.get(key)
            if not entry:
                return None
            cached_at, value = entry
            if self.clock() - cached_at >= CONTENT_CACHE_SECONDS:
                mapping.pop(key, None)
                return None
            if isinstance(mapping, OrderedDict):
                mapping.move_to_end(key)
            return list(value)

    def _store_items(self, key, values):
        if len(values) > CONTENT_CACHE_MAX_ITEMS:
            return
        with self._cache_lock:
            self._item_cache.pop(key, None)
            self._item_cache[key] = (self.clock(), list(values))
            while len(self._item_cache) > CONTENT_CACHE_MAX_ENTRIES:
                self._item_cache.popitem(last=False)

    def invalidate_cache(self, content_type=None, category_id=None):
        """Drop short-lived list data before an explicit user refresh."""
        content_type = str(content_type or "").lower()
        category_id = None if category_id is None else str(category_id)
        if not content_type or content_type == "movie":
            self.release_movie_search()
        with self._cache_lock:
            if not content_type:
                self._live_item_epoch += 1
                active_loads = tuple(self._live_item_loads.values())
                self._live_item_loads.clear()
                self._category_cache.clear()
                self._item_cache.clear()
                self._live_item_states.clear()
                self._epg_preferred_action = ""
                for active_load in active_loads:
                    active_load[2].set()
                return
            self._category_cache.pop(content_type, None)
            for key in list(self._item_cache):
                if key[0] != content_type:
                    continue
                if category_id is None or key[1] == category_id:
                    self._item_cache.pop(key, None)
            if content_type == "live":
                self._live_item_epoch += 1
                active_loads = tuple(self._live_item_loads.values())
                self._live_item_loads.clear()
                for active_load in active_loads:
                    active_load[2].set()
                self._epg_preferred_action = ""
                for key in list(self._live_item_states):
                    if category_id is None or key[1] == category_id:
                        self._live_item_states.pop(key, None)

    def release_movie_search(self):
        """Release the temporary all-movies index used by one search screen.

        An in-flight provider response is made stale by the generation bump;
        it can never repopulate the cache after the search screen has closed.
        The worker's request_scope remains responsible for interrupting I/O.
        """
        with self._movie_search_lock:
            self._movie_search_generation += 1
            self._movie_search_catalog = None
            self._movie_search_query = ""
            self._movie_search_matches = None
            active_load = self._movie_search_load
            self._movie_search_load = None
            if active_load is not None:
                active_load[2].set()

    @staticmethod
    def _movie_search_record(entry):
        """Keep only fields needed to reconstruct a normalised movie card."""
        if not isinstance(entry, dict):
            return None
        stream_id = _numeric_id(entry.get("stream_id"))
        if not stream_id:
            return None
        name = _clean_text(entry.get("name"), _("Untitled content"))
        search_text = _movie_search_text(name)
        icon = (
            entry.get("stream_icon")
            or entry.get("cover")
            or entry.get("movie_image")
            or entry.get("cover_big")
            or entry.get("poster")
            or entry.get("poster_path")
            or entry.get("icon")
        )
        return (
            search_text,
            stream_id,
            name,
            entry.get("category_id"),
            entry.get("container_extension"),
            icon,
            entry.get("rating") or entry.get("rating_5based"),
            entry.get("year") or entry.get("releaseDate"),
        )

    def _movie_search_item(self, record):
        """Re-enter the established Xtream normaliser for a visible row."""
        return self._content_item(
            "movie",
            {
                "stream_id": record[1],
                "name": record[2],
                "category_id": record[3],
                "container_extension": record[4],
                "stream_icon": record[5],
                "rating": record[6],
                "year": record[7],
            },
        )

    def _movie_search_catalog_for(self, generation):
        """Return the one-response Xtream catalogue for this search lifetime."""
        while True:
            self._check_request_limits()
            with self._movie_search_lock:
                if generation != self._movie_search_generation:
                    raise ContentError(
                        N_("Request cancelled"),
                        ContentError.CANCELLED,
                    )
                if self._movie_search_catalog is not None:
                    return self._movie_search_catalog
                active_load = self._movie_search_load
                if active_load is None:
                    active_load = (
                        generation,
                        object(),
                        threading.Event(),
                    )
                    self._movie_search_load = active_load
                    load_here = True
                else:
                    load_here = False

            if not load_here:
                while not active_load[2].wait(0.05):
                    self._check_request_limits()
                    with self._movie_search_lock:
                        if generation != self._movie_search_generation:
                            raise ContentError(
                                N_("Request cancelled"),
                                ContentError.CANCELLED,
                            )
                continue

            try:
                payload = self._request_json(ITEM_ACTIONS["movie"])
                if not isinstance(payload, list):
                    raise ContentError(
                        N_("Could not load the content list"),
                        ContentError.CONTENT_RESPONSE,
                    )
                if len(payload) > MAX_CATALOG_RESULTS:
                    raise ContentError(
                        N_("Server content list is too large"),
                        ContentError.CATALOG_ITEM_LIMIT,
                    )
                records = []
                for index, entry in enumerate(payload):
                    if index % 128 == 0:
                        self._check_request_limits()
                    record = self._movie_search_record(entry)
                    if record is not None:
                        records.append(record)
                self._check_request_limits()
                records = tuple(records)
            except Exception:
                with self._movie_search_lock:
                    if self._movie_search_load is active_load:
                        self._movie_search_load = None
                    active_load[2].set()
                raise

            with self._movie_search_lock:
                current = (
                    generation == self._movie_search_generation
                    and self._movie_search_load is active_load
                )
                if self._movie_search_load is active_load:
                    self._movie_search_load = None
                if current:
                    self._movie_search_catalog = records
                active_load[2].set()
                if current:
                    return self._movie_search_catalog
            raise ContentError(
                N_("Request cancelled"),
                ContentError.CANCELLED,
            )

    def _movie_search_match_indexes(self, catalog, generation, query):
        with self._movie_search_lock:
            if (
                generation != self._movie_search_generation
                or catalog is not self._movie_search_catalog
            ):
                raise ContentError(
                    N_("Request cancelled"),
                    ContentError.CANCELLED,
                )
            if (
                query == self._movie_search_query
                and self._movie_search_matches is not None
            ):
                return self._movie_search_matches

        terms = tuple(dict.fromkeys(query.split()))
        matches = []
        for index, record in enumerate(catalog):
            if index % 128 == 0:
                self._check_request_limits()
            if all(term in record[0] for term in terms):
                matches.append(index)
        self._check_request_limits()
        matches = tuple(matches)

        with self._movie_search_lock:
            if (
                generation != self._movie_search_generation
                or catalog is not self._movie_search_catalog
            ):
                raise ContentError(
                    N_("Request cancelled"),
                    ContentError.CANCELLED,
                )
            self._movie_search_query = query
            self._movie_search_matches = matches
            return matches

    def search_movies(
        self,
        query,
        page=1,
        page_size=14,
        hidden_category_ids=None,
    ):
        """Search one Xtream VOD response locally and return a lazy UI page."""
        try:
            page = max(1, int(page))
        except (TypeError, ValueError, OverflowError):
            page = 1
        try:
            page_size = max(1, int(page_size))
        except (TypeError, ValueError, OverflowError):
            page_size = 14
        query = _movie_search_text(query)[:120]
        if not query:
            return MovieSearchPage(
                page=page,
                page_size=page_size,
                source="xtream-local",
                supported=True,
            )

        with self._movie_search_lock:
            generation = self._movie_search_generation
        catalog = self._movie_search_catalog_for(generation)
        matches = self._movie_search_match_indexes(
            catalog,
            generation,
            query,
        )
        hidden_category_ids = hidden_category_match_ids((), hidden_category_ids)
        if hidden_category_ids:
            matches = tuple(
                index
                for index in matches
                if category_ids_visible(
                    (catalog[index][3],), hidden_category_ids
                )
            )
        start = (page - 1) * page_size
        stop = min(start + page_size, len(matches))
        items = []
        for position in range(start, stop):
            if position % 128 == 0:
                self._check_request_limits()
            item = self._movie_search_item(catalog[matches[position]])
            if item is not None:
                items.append(item)
        self._check_request_limits()
        return MovieSearchPage(
            items=items,
            page=page,
            page_size=page_size,
            total_items=len(matches),
            has_more=stop < len(matches),
            source="xtream-local",
            supported=True,
        )

    def load_categories(self, content_type):
        content_type = self._validate_type(content_type)
        cached = self._cached_value(self._category_cache, content_type)
        if cached is not None:
            return cached
        payload = self._request_json(CATEGORY_ACTIONS[content_type])
        if not isinstance(payload, list):
            raise ContentError(
                N_("Could not load the category list"),
                ContentError.CATEGORY_RESPONSE,
            )
        categories = [ContentCategory("", _(CONTENT_LABELS[content_type][1]))]
        seen = set()
        for entry in payload[:MAX_RESULTS]:
            if not isinstance(entry, dict):
                continue
            category_id = str(entry.get("category_id") or "").strip()
            if not category_id or category_id in seen:
                continue
            seen.add(category_id)
            categories.append(
                ContentCategory(category_id, entry.get("category_name"))
            )
        with self._cache_lock:
            self._category_cache[content_type] = (
                self.clock(),
                list(categories),
            )
        return categories

    def _content_item(self, content_type, entry, catalog_category_id=""):
        if not isinstance(entry, dict):
            return None
        id_key = "series_id" if content_type == "series" else "stream_id"
        stream_id = _numeric_id(entry.get(id_key))
        if not stream_id:
            return None
        item = ContentItem(
            content_type,
            stream_id,
            entry.get("name"),
            category_id=entry.get("category_id"),
            extension=entry.get("container_extension"),
            icon=_normalise_media_url(
                entry.get("stream_icon")
                or entry.get("cover")
                or entry.get("movie_image")
                or entry.get("cover_big")
                or entry.get("poster")
                or entry.get("poster_path")
                or entry.get("icon"),
                self.account,
            ),
            rating=entry.get("rating") or entry.get("rating_5based"),
            year=entry.get("year") or entry.get("releaseDate"),
            tmdb_id=entry.get("tmdb_id") or entry.get("tmdb"),
        )
        item.catalog_category_id = str(catalog_category_id or "")
        return item

    def _new_live_item_state(self, category_id):
        parameters = None
        if category_id:
            parameters = {"category_id": category_id}
        payload = self._request_json(ITEM_ACTIONS["live"], parameters)
        if not isinstance(payload, list):
            raise ContentError(
                N_("Could not load the content list"),
                ContentError.CONTENT_RESPONSE,
            )
        entries = list(payload[:MAX_RESULTS])
        return {
            "cached_at": self.clock(),
            "category_id": category_id,
            "entries": entries,
            "cursor": 0,
            "results": [],
            "result_ids": set(),
            "complete": not entries,
        }

    def _live_item_state(self, category_id):
        key = ("live", category_id)
        while True:
            self._check_request_limits()
            with self._cache_lock:
                state = self._live_item_states.get(key)
                if (
                    state is not None
                    and self.clock() - state["cached_at"]
                    < CONTENT_CACHE_SECONDS
                ):
                    self._live_item_states.move_to_end(key)
                    return state
                epoch = self._live_item_epoch
                active_load = self._live_item_loads.get(key)
                if active_load is None or active_load[0] != epoch:
                    active_load = (epoch, object(), threading.Event())
                    self._live_item_loads[key] = active_load
                    load_here = True
                else:
                    load_here = False

            if not load_here:
                while not active_load[2].wait(0.05):
                    self._check_request_limits()
                    with self._cache_lock:
                        if epoch != self._live_item_epoch:
                            break
                continue

            # Provider I/O must never own the shared cache lock: GREEN refresh
            # and GUI-side invalidation need to remain immediate.
            try:
                candidate = self._new_live_item_state(category_id)
            except Exception:
                with self._cache_lock:
                    if self._live_item_loads.get(key) is active_load:
                        self._live_item_loads.pop(key, None)
                    active_load[2].set()
                raise

            with self._cache_lock:
                current = (
                    epoch == self._live_item_epoch
                    and self._live_item_loads.get(key) is active_load
                )
                if self._live_item_loads.get(key) is active_load:
                    self._live_item_loads.pop(key, None)
                if not current:
                    active_load[2].set()
                    continue
                state = self._live_item_states.get(key)
                if (
                    state is not None
                    and self.clock() - state["cached_at"]
                    < CONTENT_CACHE_SECONDS
                ):
                    self._live_item_states.move_to_end(key)
                    active_load[2].set()
                    return state
                self._live_item_states.pop(key, None)
                self._live_item_states[key] = candidate
                while len(self._live_item_states) > CONTENT_CACHE_MAX_ENTRIES:
                    self._live_item_states.popitem(last=False)
                active_load[2].set()
                return candidate

    def _append_live_items(self, state, target):
        added = []
        entries = state.get("entries") or []
        cursor = int(state.get("cursor", 0) or 0)
        while (
            cursor < len(entries)
            and len(added) < target
            and len(state["results"]) < MAX_RESULTS
        ):
            entry = entries[cursor]
            cursor += 1
            item = self._content_item(
                "live",
                entry,
                state.get("category_id", ""),
            )
            if item is None or item.stream_id in state["result_ids"]:
                continue
            state["result_ids"].add(item.stream_id)
            state["results"].append(item)
            added.append(item)
        state["cursor"] = cursor
        if cursor >= len(entries) or len(state["results"]) >= MAX_RESULTS:
            state["complete"] = True
            state["entries"] = []
        state["cached_at"] = self.clock()
        return added

    def _load_live_items(self, category_id):
        key = ("live", category_id)
        while True:
            state = self._live_item_state(category_id)
            with self._cache_lock:
                if self._live_item_states.get(key) is not state:
                    continue
                if not state["results"] and not state["complete"]:
                    self._append_live_items(state, LIVE_INITIAL_ITEMS)
                return list(state["results"])

    def load_items(self, content_type, category_id=""):
        content_type = self._validate_type(content_type)
        category_id = str(category_id or "")
        if content_type == "live":
            return self._load_live_items(category_id)
        cache_key = (content_type, category_id)
        cached = self._cached_value(self._item_cache, cache_key)
        if cached is not None:
            return cached
        parameters = None
        if category_id:
            parameters = {"category_id": category_id}
        payload = self._request_json(ITEM_ACTIONS[content_type], parameters)
        if not isinstance(payload, list):
            raise ContentError(
                N_("Could not load the content list"),
                ContentError.CONTENT_RESPONSE,
            )

        results = []
        for entry in payload[:MAX_RESULTS]:
            item = self._content_item(content_type, entry, category_id)
            if item is not None:
                results.append(item)
        self._store_items(cache_key, results)
        return results

    def load_vod_catalog_batch(
        self,
        category_id="",
        max_items=MAX_CATALOG_RESULTS,
    ):
        """Return one uncached VOD batch without the normal 20k UI slice.

        Global catalogue collection owns its deduplication and persistence, so
        this route deliberately bypasses the short-lived category cache.  A
        provider response over the declared limit is reported, never sliced.
        """
        category_id = str(category_id or "").strip()
        try:
            max_items = int(max_items)
        except (TypeError, ValueError, OverflowError):
            max_items = MAX_CATALOG_RESULTS
        max_items = max(1, min(MAX_CATALOG_RESULTS, max_items))
        parameters = {"category_id": category_id} if category_id else None
        payload = self._request_json(ITEM_ACTIONS["movie"], parameters)
        if not isinstance(payload, list):
            raise ContentError(
                N_("Could not load the content list"),
                ContentError.CONTENT_RESPONSE,
            )
        if len(payload) > max_items:
            raise ContentError(
                N_("Server content list is too large"),
                ContentError.CATALOG_ITEM_LIMIT,
            )

        results = []
        for index, entry in enumerate(payload):
            if index % 128 == 0:
                self._check_request_limits()
            item = self._content_item("movie", entry, category_id)
            if item is not None:
                results.append(item)
        self._check_request_limits()
        return results

    def load_more_items(self, content_type, category_id=""):
        content_type = self._validate_type(content_type)
        category_id = str(category_id or "")
        if content_type != "live":
            return []
        key = ("live", category_id)
        while True:
            state = self._live_item_state(category_id)
            with self._cache_lock:
                if self._live_item_states.get(key) is not state:
                    continue
                if state["complete"]:
                    return []
                return self._append_live_items(state, LIVE_MORE_ITEMS)

    def has_more_items(self, content_type, category_id=""):
        content_type = self._validate_type(content_type)
        category_id = str(category_id or "")
        if content_type != "live":
            return False
        with self._cache_lock:
            key = ("live", category_id)
            state = self._live_item_states.get(key)
            if state is None:
                return False
            if self.clock() - state["cached_at"] >= CONTENT_CACHE_SECONDS:
                self._live_item_states.pop(key, None)
                return False
            return not state.get("complete", True)

    def load_vod_info(self, stream_id):
        stream_id = _numeric_id(stream_id)
        if not stream_id:
            raise ContentError(
                N_("Invalid movie ID"),
                ContentError.INVALID_MOVIE_ID,
            )
        payload = self._request_json("get_vod_info", {"vod_id": stream_id})
        if not isinstance(payload, dict):
            raise ContentError(
                N_("Could not load movie information"),
                ContentError.MOVIE_INFO_RESPONSE,
            )
        info = payload.get("info") or {}
        movie = payload.get("movie_data") or {}
        if not isinstance(info, dict):
            info = {}
        if not isinstance(movie, dict):
            movie = {}

        def first(*keys):
            for key in keys:
                value = info.get(key)
                if value not in (None, "", []):
                    return value
                value = movie.get(key)
                if value not in (None, "", []):
                    return value
            return ""

        return MovieInfo(
            title=first("name", "o_name", "title"),
            plot=first("plot", "description", "overview"),
            rating=first("rating", "rating_5based"),
            year=first("year", "releasedate", "releaseDate"),
            duration=first("duration", "duration_secs"),
            genre=first("genre"),
            director=first("director"),
            cast=first("cast", "actors"),
            cover=_normalise_media_url(
                first("movie_image", "cover_big", "stream_icon", "cover"),
                self.account,
            ),
            backdrop=_normalise_media_url(
                first(
                    "backdrop_path",
                    "backdrop",
                    "backdrop_image",
                    "background",
                    "screenshot_uri",
                ),
                self.account,
            ),
            tmdb_id=first("tmdb_id", "tmdb"),
        )

    def load_series_info(self, series_id):
        series_id = _numeric_id(series_id)
        if not series_id:
            raise ContentError(
                N_("Invalid series ID"),
                ContentError.INVALID_SERIES_ID,
            )
        payload = self._request_json("get_series_info", {"series_id": series_id})
        if not isinstance(payload, dict):
            raise ContentError(
                N_("Could not load series information"),
                ContentError.SERIES_INFO_RESPONSE,
            )
        info = payload.get("info") or {}
        if not isinstance(info, dict):
            info = {}
        episodes = payload.get("episodes") or {}
        if not isinstance(episodes, dict):
            raise ContentError(
                N_("Could not load series episodes"),
                ContentError.SERIES_EPISODES_RESPONSE,
            )

        results = []
        season_keys = sorted(
            episodes.keys(),
            key=lambda value: int(value) if str(value).isdigit() else 99999,
        )
        for season_key in season_keys:
            season_entries = episodes.get(season_key) or []
            if not isinstance(season_entries, list):
                continue
            for entry in season_entries:
                if not isinstance(entry, dict):
                    continue
                episode_info = entry.get("info") or {}
                if not isinstance(episode_info, dict):
                    episode_info = {}
                stream_id = _numeric_id(entry.get("id") or entry.get("stream_id"))
                if not stream_id:
                    continue
                episode_number = entry.get("episode_num") or ""
                title = entry.get("title") or entry.get("name") or _("Episode")
                prefix = _("S{} E{}").format(
                    season_key,
                    episode_number,
                ).strip()
                results.append(
                    ContentItem(
                        "series",
                        stream_id,
                        "{}  {}".format(prefix, title),
                        extension=entry.get("container_extension"),
                        parent_id=series_id,
                        icon=_normalise_media_url(
                            episode_info.get("movie_image")
                            or episode_info.get("cover_big")
                            or episode_info.get("cover")
                            or entry.get("stream_icon")
                            or entry.get("cover"),
                            self.account,
                        ),
                        season=season_key,
                        episode=episode_number,
                        duration=(
                            entry.get("duration")
                            or episode_info.get("duration")
                            or episode_info.get("duration_secs")
                        ),
                        plot=(
                            entry.get("plot")
                            or episode_info.get("plot")
                            or episode_info.get("description")
                        ),
                    )
                )
                if len(results) >= MAX_RESULTS:
                    break
            if len(results) >= MAX_RESULTS:
                break

        def first(*keys):
            for key in keys:
                value = info.get(key)
                if value not in (None, "", []):
                    return value
                value = payload.get(key)
                if value not in (None, "", []):
                    return value
            return ""

        return SeriesInfo(
            title=first("name", "o_name", "title"),
            plot=first("plot", "description", "overview"),
            rating=first("rating", "rating_5based"),
            year=first("year", "releaseDate", "releasedate"),
            genre=first("genre"),
            director=first("director"),
            cast=first("cast", "actors"),
            cover=_normalise_media_url(
                first("cover", "cover_big", "movie_image", "stream_icon"),
                self.account,
            ),
            episodes=results,
            backdrop=_normalise_media_url(
                first(
                    "backdrop_path",
                    "backdrop",
                    "backdrop_image",
                    "background",
                    "screenshot_uri",
                ),
                self.account,
            ),
            tmdb_id=first("tmdb_id", "tmdb"),
        )

    def load_episodes(self, series_id):
        return self.load_series_info(series_id).episodes

    @staticmethod
    def _epg_events(payload):
        if isinstance(payload, dict):
            listings = []
            for key in (
                "epg_listings", "listings", "events", "data", "items",
                "results",
            ):
                candidate = payload.get(key)
                if isinstance(candidate, list):
                    listings = candidate
                    break
                if isinstance(candidate, dict):
                    nested = candidate.get("epg_listings")
                    if isinstance(nested, list):
                        listings = nested
                        break
        elif isinstance(payload, list):
            listings = payload
        else:
            listings = []
        if not isinstance(listings, list):
            return []

        events = []
        for entry in listings:
            if not isinstance(entry, dict):
                continue
            title = (
                entry.get("title")
                or entry.get("name")
                or entry.get("epg_title")
            )
            start = (
                entry.get("start_timestamp")
                or entry.get("start")
                or entry.get("time")
                or entry.get("begin")
            )
            end = (
                entry.get("stop_timestamp")
                or entry.get("end_timestamp")
                or entry.get("end")
                or entry.get("time_to")
                or entry.get("stop")
            )
            description = (
                entry.get("description")
                or entry.get("descr")
                or entry.get("plot")
                or entry.get("short_description")
                or ""
            )
            events.append(
                EPGEvent(
                    title,
                    start,
                    end,
                    now_playing=_truthy(
                        entry.get("now_playing")
                        or entry.get("is_now")
                        or entry.get("current")
                    ),
                    description=description,
                )
            )
        return events

    def load_simple_epg(self, stream_id, limit=2):
        stream_id = _numeric_id(stream_id)
        if not stream_id:
            raise ContentError(
                N_("Invalid channel ID"),
                ContentError.INVALID_CHANNEL_ID,
            )
        payload = self._request_json(
            "get_simple_data_table",
            {"stream_id": stream_id},
        )
        return _select_current_epg_window(self._epg_events(payload), limit)

    def load_short_epg(self, stream_id, limit=2):
        stream_id = _numeric_id(stream_id)
        if not stream_id:
            raise ContentError(
                N_("Invalid channel ID"),
                ContentError.INVALID_CHANNEL_ID,
            )
        try:
            limit = max(1, min(12, int(limit)))
        except (TypeError, ValueError):
            limit = 2
        payload = self._request_json(
            "get_short_epg",
            {"stream_id": stream_id, "limit": limit},
        )
        return _select_current_epg_window(self._epg_events(payload), limit)

    def load_epg(self, stream_id, limit=2):
        """Load now/next EPG using the provider's two common Xtream routes."""
        with self._cache_lock:
            preferred = self._epg_preferred_action
        routes = [
            ("get_short_epg", self.load_short_epg),
            ("get_simple_data_table", self.load_simple_epg),
        ]
        if preferred == "get_simple_data_table":
            routes.reverse()
        for action, loader in routes:
            try:
                events = loader(stream_id, limit)
            except ContentError:
                events = []
                failed = True
            else:
                failed = False
            if events:
                with self._cache_lock:
                    self._epg_preferred_action = action
                return events
            if preferred and action == preferred and not failed:
                # A known working route returned a genuine channel-level
                # empty result.  Do not double-query the same selection.
                return []
        return []

    def playback_url(self, item):
        if not isinstance(item, ContentItem):
            raise ContentError(
                N_("Invalid playback item"),
                ContentError.INVALID_PLAYBACK_ITEM,
            )
        try:
            return self.account.stream_url(
                item.content_type,
                item.stream_id,
                item.extension,
            )
        except ValueError:
            raise ContentError(
                N_("Could not create the stream URL"),
                ContentError.PLAYBACK_URL,
            )

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

from __future__ import absolute_import

import datetime
import hashlib
import ipaddress
import json
import os
import re
import socket
import stat
import threading
import time
import unicodedata
import weakref
from collections import OrderedDict, deque
from contextlib import contextmanager

try:
    from http.cookiejar import CookieJar
    from urllib.error import HTTPError, URLError
    from urllib.parse import quote, urlencode, urlsplit, urlunsplit
    from urllib.request import (
        HTTPCookieProcessor,
        HTTPRedirectHandler,
        Request,
        build_opener,
    )
except ImportError:  # pragma: no cover - Python 2 images are not a target.
    from cookielib import CookieJar
    from urllib2 import (
        HTTPError,
        HTTPCookieProcessor,
        HTTPRedirectHandler,
        Request,
        URLError,
        build_opener,
    )
    from urllib import quote, urlencode
    from urlparse import urlsplit, urlunsplit

from .content import (
    CONTENT_LABELS,
    CONTENT_CACHE_SECONDS,
    LIVE_INITIAL_ITEMS,
    LIVE_MORE_ITEMS,
    MAX_RESULTS,
    ContentCategory,
    ContentError,
    ContentItem,
    EPGEvent,
    MovieInfo,
    MovieSearchPage,
    SeriesInfo,
    _normalise_media_url,
    _select_current_epg_window,
)
from .diagnostics import log_event
from .i18n import N_, _, device_language
from .network_security import (
    NetworkSecurityError,
    resolve_public_addresses,
    validate_public_url_syntax,
)
from .playlist import PlaylistLoadResult, ServerHealth
from .portal_expiry import portal_expiry
from .portal_identity import (
    is_composite_id,
    safe_composite_id,
    safe_series_id,
)


DEFAULT_PORTAL_PATH = "/etc/enigma2/gtiptvplayer/stalker.txt"
MAX_PORTAL_BYTES = 4 * 1024 * 1024
MAX_API_BYTES = 16 * 1024 * 1024
MAX_PROVIDER_JSON_INTEGER_DIGITS = 128
MAX_PROVIDER_JSON_NESTING = 128
MAX_PORTAL_ACCOUNTS = 500
DEFAULT_TIMEOUT = 12
API_READ_CHUNK_BYTES = 64 * 1024
PORTAL_SESSION_CACHE_LIMIT = 12
PORTAL_SESSION_IDLE_SECONDS = 15 * 60
# MAG tokens do not advertise a portable expiry.  Keep an active session for a
# conservative interval, then renew it once under the shared session lock.  A
# server-side rejection still takes precedence and triggers the bounded retry
# below, so this is a safety ceiling rather than a guessed provider lifetime.
PORTAL_TOKEN_MAX_AGE_SECONDS = 30 * 60
PORTAL_RENEWAL_FAILURE_COOLDOWN_SECONDS = 2.0
PORTAL_ENDPOINT_HINT_LIMIT = 32
PLAYBACK_LINK_CACHE_LIMIT = 48
PLAYBACK_LINK_CACHE_SECONDS = 20
CREATE_LINK_HINT_LIMIT = 8
CREATE_LINK_MAX_DEPTH = 8
CREATE_LINK_MAX_ENTRIES = 128
CREATE_LINK_MAX_NODES = 512
PROFILE_REQUEST_MAX_ATTEMPTS = 2
ACCOUNT_INFO_HEALTH_TIMEOUT = 2.0
LIVE_PLAYBACK_RECOVERY_TIMEOUT = 12.0
PROFILE_ACTIVE_CONNECTION_KEYS = ("active_cons", "active_connections")
PROFILE_MAX_CONNECTION_KEYS = ("max_connections", "max_online")
PROFILE_CONNECTION_WRAPPERS = ("data", "profile", "result")
CREATE_LINK_TERMINAL_REASONS = frozenset(
    (
        ContentError.CANCELLED,
        ContentError.DEADLINE,
        ContentError.ACCESS_DENIED,
        ContentError.RATE_LIMITED,
        ContentError.CONNECTION,
        ContentError.RESPONSE_TOO_LARGE,
        ContentError.INVALID_RESPONSE,
    )
)
HANDSHAKE_TERMINAL_REASONS = frozenset(
    (
        ContentError.CANCELLED,
        ContentError.DEADLINE,
        ContentError.RATE_LIMITED,
    )
)
REQUEST_ABORT_REASONS = frozenset(
    (
        ContentError.CANCELLED,
        ContentError.DEADLINE,
    )
)
PORTAL_CATALOG_PACE_SECONDS = 0.12
PORTAL_CATALOG_STEP_ITEMS = 250
MAX_PORTAL_PAGES = 250
PORTAL_VOD_INITIAL_TARGET = 12
PORTAL_VOD_MORE_TARGET = 20
PORTAL_VOD_REQUEST_BUDGET = 6
PORTAL_VOD_EMPTY_REQUEST_BUDGET = 12
PORTAL_MOVIE_SEARCH_PAGE_SIZE = 14
PORTAL_MOVIE_SEARCH_QUERY_LIMIT = 120
PORTAL_MOVIE_SEARCH_STATE_LIMIT = 8
PORTAL_MOVIE_SEARCH_PAGE_STATE_LIMIT = 16
PORTAL_SERIES_INITIAL_REQUEST_BUDGET = 3
PORTAL_SERIES_MORE_REQUEST_BUDGET = 2
PORTAL_LIVE_INITIAL_REQUEST_BUDGET = 3
PORTAL_LIVE_MORE_REQUEST_BUDGET = 6
PORTAL_PROVIDER = "stalker"
VOD_CATALOG_STATE_LIMIT = 5
VOD_ENTRY_CACHE_LIMIT = 2000
VOD_RAW_CACHE_LIMIT = 3
SERIES_INFO_CACHE_LIMIT = 32
EPISODE_COMMAND_CACHE_LIMIT = 512
LIVE_CHANNEL_META_LIMIT = 2000
EPG_COMPATIBILITY_MAX_ATTEMPTS = 4
STALKER_SHORT_EPG_MIN_EVENTS = 10
STALKER_EPG_MAX_PROGRAMME_SECONDS = 24 * 60 * 60
STALKER_EPG_EMPTY_TTL_SECONDS = 60
STALKER_EPG_EMPTY_CACHE_LIMIT = 128

_MAC_PATTERN = re.compile(
    r"^(?:[0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}$"
)
_CONTROL_PATTERN = re.compile(r"[\x00-\x1f\x7f]")
_COMMAND_PREFIX = re.compile(r"^(?:ffmpeg|ffrt|auto)\s+", re.IGNORECASE)
_MEDIA_COMMAND_PATH_ID = re.compile(
    r"(?:^|/)media/(?:(file)_)?([0-9]{1,40})"
    r"(?::([0-9]{1,10}))?\.mpg$",
    re.IGNORECASE,
)
_TIMEZONE_PATTERN = re.compile(r"^[A-Za-z0-9_+./-]{1,80}$")
_COOKIE_NAME_PATTERN = re.compile(
    r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$"
)
_AUTH_ERROR_WRAPPERS = ("js", "data", "result", "response")
_AUTH_ERROR_FIELDS = ("error", "error_message")
_AUTH_MESSAGE_FIELDS = ("message", "reason", "description")
_AUTH_STATUS_FIELDS = ("status", "code", "error_code")
_CONTENT_IDENTITY_FIELDS = (
    "id", "ch_id", "channel_id", "stream_id", "video_id", "movie_id",
    "series_id", "episode_id", "file_id", "parent_id", "season_id",
    "programme_id", "program_id", "event_id", "epg_id", "category_id",
    "genre_id", "tv_genre_id", "name", "title", "cmd", "url", "link",
)
_MAX_COOKIE_COUNT = 64
_MAX_COOKIE_HEADER_BYTES = 4096
_STREAM_SCHEMES = (
    "http", "https", "rtsp", "rtsps", "rtmp", "rtmps", "rtmpe", "mms",
    "udp", "rtp",
)
_LOOPBACK_HOSTS = (
    "localhost", "localhost.localdomain", "0.0.0.0", "::1",
)
_MAG_USER_AGENT = (
    "Mozilla/5.0 (QtEmbedded; U; Linux; C) "
    "AppleWebKit/533.3 (KHTML, like Gecko) MAG250 stbapp"
)
_MAG_X_USER_AGENT = "Model: MAG250; Link: Ethernet"
_DEVICE_TIMEZONE = [None]
_DEVICE_TIMEZONE_LOCK = threading.Lock()
_PORTAL_SESSIONS = OrderedDict()
_PORTAL_SESSIONS_LOCK = threading.RLock()
_PORTAL_SESSION_INSTANCES = {}
_PORTAL_ENDPOINT_HINTS = OrderedDict()
_PORTAL_ENDPOINT_HINTS_LOCK = threading.RLock()
_PORTAL_FILE_TEMPLATE = (
    "# GT IPTV Player Pro - Stalker / MAC Portal\n"
    "# Add a portal URL, then place one or more MAC addresses below it.\n"
    "# Start another block with the next portal URL.\n"
    "#\n"
    "# http://example.invalid/c/\n"
    "# 00:1A:79:XX:XX:01\n"
).encode("utf-8")


def _cookie_parts(value):
    """Return bounded, header-safe cookie pairs without logging their values."""

    results = []
    for part in str(value or "").split(";"):
        name, separator, cookie_value = part.strip().partition("=")
        name = name.strip()
        cookie_value = cookie_value.strip()
        if (
            not separator
            or not _COOKIE_NAME_PATTERN.match(name)
            or not cookie_value
            or len(cookie_value) > 4096
            or _CONTROL_PATTERN.search(cookie_value)
            or any(character in cookie_value for character in (";", "&"))
        ):
            continue
        results.append((name, cookie_value))
        if len(results) >= _MAX_COOKIE_COUNT:
            break
    return results


def _merge_cookie_headers(primary, secondary):
    """Merge cookies with case-insensitive, first-header precedence."""

    parts = []
    names = set()
    for header in (primary, secondary):
        for name, value in _cookie_parts(header):
            lowered = name.lower()
            if lowered in names:
                continue
            candidate = "{}={}".format(name, value)
            joined_size = sum(len(item) for item in parts) + len(candidate)
            if parts:
                joined_size += 2 * len(parts)
            if joined_size > _MAX_COOKIE_HEADER_BYTES:
                return "; ".join(parts)
            names.add(lowered)
            parts.append(candidate)
            if len(parts) >= _MAX_COOKIE_COUNT:
                return "; ".join(parts)
    return "; ".join(parts)


def _auth_error_text(value, depth=0):
    """Flatten only a small provider error envelope for semantic matching."""

    if depth > 3 or value is None or isinstance(value, bool):
        return ""
    if isinstance(value, (str, bytes, int, float)):
        if isinstance(value, bytes):
            value = value.decode("utf-8", "replace")
        return str(value)[:512].strip().lower()
    if isinstance(value, dict):
        fragments = []
        for field in (
            _AUTH_ERROR_FIELDS + _AUTH_MESSAGE_FIELDS + _AUTH_STATUS_FIELDS
        ):
            if field in value:
                text = _auth_error_text(value.get(field), depth + 1)
                if text:
                    fragments.append(text)
        return " ".join(fragments)[:1024]
    if isinstance(value, (list, tuple)):
        fragments = []
        for item in value[:8]:
            text = _auth_error_text(item, depth + 1)
            if text:
                fragments.append(text)
        return " ".join(fragments)[:1024]
    return ""


def _looks_like_auth_error_text(value):
    text = str(value or "").strip().lower()
    if not text or text in ("0", "false", "none", "null", "ok", "success"):
        return False
    if text in ("401", "403"):
        return True
    if any(
        marker in text
        for marker in (
            "access denied", "unauthorized", "not authorized", "forbidden",
            "authentication failed", "authorization failed",
        )
    ):
        return True
    invalid_markers = (
        "invalid", "not valid", "expired", "missing", "denied", "failed",
        "not found",
    )
    return bool(
        ("token" in text or "session" in text)
        and any(marker in text for marker in invalid_markers)
    )


def _has_content_identity(value, include_labels=True):
    """Whether a mapping is a catalogue/media record rather than an error."""

    if not isinstance(value, dict):
        return False

    def meaningful(field, candidate):
        if candidate in (None, "", [], {}):
            return False
        if isinstance(candidate, (str, bytes)):
            if isinstance(candidate, bytes):
                candidate = candidate.decode("utf-8", "replace")
            candidate = candidate.strip()
            if not candidate:
                return False
            if (
                (field == "id" or field.endswith("_id"))
                and candidate == "0"
            ):
                return False
        elif (field == "id" or field.endswith("_id")) and candidate == 0:
            return False
        return True

    fields = _CONTENT_IDENTITY_FIELDS
    if not include_labels:
        fields = tuple(
            field for field in fields if field not in ("name", "title")
        )
    return any(
        meaningful(field, value.get(field))
        for field in fields
    )


def _semantic_auth_error(value, depth=0):
    """Detect explicit auth failures even beside an empty data/cmd field."""

    if depth > 3 or not isinstance(value, dict):
        return False
    error_values = []
    for field in _AUTH_ERROR_FIELDS:
        if field in value:
            error_values.append(value.get(field))
    if error_values and _looks_like_auth_error_text(
        " ".join(_auth_error_text(item) for item in error_values)
    ):
        return True
    # A few portals use only ``status`` or ``message`` beside an empty data/cmd
    # field. Treat those as an envelope only when no non-empty catalogue
    # identity is present; record status values, movie descriptions and EPG
    # prose must never become authentication signals. Explicit error fields
    # above remain authoritative even on a malformed record-shaped response.
    has_content_identity = _has_content_identity(value)
    has_strong_content_identity = _has_content_identity(
        value,
        include_labels=False,
    )
    status_values = [
        value.get(field)
        for field in _AUTH_STATUS_FIELDS
        if field in value
    ]
    # Evaluate status/code members independently: an auxiliary ``code: 0``
    # must not dilute a direct 401/403 signal.  A title/name by itself is weak
    # envelope decoration, while a real catalogue ID protects record status.
    if not has_strong_content_identity and any(
        _looks_like_auth_error_text(_auth_error_text(item))
        for item in status_values
    ):
        return True
    if not has_content_identity:
        message_text = " ".join(
            _auth_error_text(value.get(field))
            for field in _AUTH_MESSAGE_FIELDS
            if field in value
        )
        if _looks_like_auth_error_text(message_text):
            return True
    else:
        # Once this mapping is recognisably a catalogue/media row, its nested
        # ``data`` or ``result`` dictionaries are record metadata rather than
        # authentication envelopes.  Explicit error/error_message fields on
        # the row itself were already handled above; do not interpret prose or
        # status-like values deeper inside a valid record as session failures.
        return False
    for wrapper in _AUTH_ERROR_WRAPPERS:
        nested = value.get(wrapper)
        # Catalogue rows can legitimately contain arbitrary titles/messages.
        # Descend only through mapping envelopes, never through record lists.
        if isinstance(nested, dict) and _semantic_auth_error(nested, depth + 1):
            return True
    return False


def _device_timezone():
    cached = _DEVICE_TIMEZONE[0]
    if cached:
        return cached
    with _DEVICE_TIMEZONE_LOCK:
        cached = _DEVICE_TIMEZONE[0]
        if cached:
            return cached
        cached = _read_device_timezone()
        _DEVICE_TIMEZONE[0] = cached
        return cached


def _read_device_timezone():
    candidates = [os.environ.get("TZ", "")]
    try:
        if os.path.getsize("/etc/timezone") <= 256:
            with open("/etc/timezone", "r") as handle:
                candidates.append(handle.read(256).strip())
    except (IOError, OSError):
        pass
    for candidate in candidates:
        candidate = str(candidate or "").strip()
        if _TIMEZONE_PATTERN.match(candidate):
            return candidate
    return "UTC"


def _provider_json_integer(value):
    """Reject pathological provider integers before constructing them."""
    text = str(value)
    digits = len(text) - (1 if text.startswith("-") else 0)
    if digits > MAX_PROVIDER_JSON_INTEGER_DIGITS:
        raise ValueError("Portal JSON integer is too large")
    return int(text)


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
            raise ValueError("Portal JSON nesting is too deep")
        children = child.values() if isinstance(child, dict) else child
        pending.append(iter(children))
    return value


def _provider_json_loads(value):
    decoded = json.loads(value, parse_int=_provider_json_integer)
    return _validate_provider_json_depth(decoded)


class PortalAccount(object):
    source_type = PORTAL_PROVIDER
    capabilities = frozenset(
        ("live", "movie", "series", "continue", "favorites")
    )

    def __init__(self, portal_url, mac, source_path=""):
        self.portal_url = normalise_portal_url(portal_url)
        self.mac = normalise_mac(mac)
        parsed = urlsplit(self.portal_url)
        self.scheme = parsed.scheme.lower()
        self.netloc = parsed.netloc
        self.base_path = parsed.path.rstrip("/")
        self.output_format = "portal"
        self.provider = PORTAL_PROVIDER
        self.source_path = str(source_path or "")

    @property
    def host(self):
        try:
            return urlsplit(self.portal_url).hostname or self.netloc
        except ValueError:
            return self.netloc

    @property
    def masked_mac(self):
        parts = self.mac.split(":")
        return "{}:{}:{}:**:**:{}".format(
            parts[0], parts[1], parts[2], parts[5]
        )

    @property
    def display_name(self):
        return "{}  •  {}".format(self.host, self.masked_mac)

    @property
    def display_endpoint(self):
        return self.portal_url.rstrip("/")

    @property
    def source_label(self):
        return "STALKER • MAC"

    def supports(self, capability):
        return str(capability or "").lower() in self.capabilities

    @property
    def identity(self):
        digest = hashlib.sha256(self.mac.encode("ascii")).hexdigest()[:20]
        return (
            PORTAL_PROVIDER,
            self.portal_url.lower(),
            "",
            digest,
        )


class PortalLoadResult(PlaylistLoadResult):
    def __init__(self, accounts=None, sources=None, invalid_lines=0, error=""):
        PlaylistLoadResult.__init__(self, accounts, sources, error)
        self.invalid_lines = int(invalid_lines or 0)


def is_portal_account(account):
    return str(
        getattr(account, "source_type", "")
        or getattr(account, "provider", "")
    ).lower() == PORTAL_PROVIDER


def normalise_mac(value):
    value = str(value or "").strip().upper().replace("-", ":")
    if not _MAC_PATTERN.match(value):
        raise ValueError(N_("Enter a valid MAC address"))
    return value


def normalise_portal_url(value):
    value = str(value or "").strip()
    if not value or _CONTROL_PATTERN.search(value):
        raise ValueError(N_("Enter a valid portal URL"))
    try:
        parsed = urlsplit(value)
    except Exception:
        raise ValueError(N_("Enter a valid portal URL"))
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError(N_("Portal URL must start with http:// or https://"))
    try:
        hostname = parsed.hostname
        username = parsed.username
        password = parsed.password
        port = parsed.port
    except ValueError:
        raise ValueError(N_("Enter a valid portal URL"))
    if not hostname or username or password:
        raise ValueError(N_("Enter a valid portal URL"))
    if port is not None and not 1 <= port <= 65535:
        raise ValueError(N_("Portal URL port is invalid"))
    if parsed.query or parsed.fragment:
        raise ValueError(N_("Do not include a query or fragment in the portal URL"))
    path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
    path = "/" + path.lstrip("/")
    if not path.endswith("/"):
        path += "/"
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, path, "", "")
    )


def portal_endpoint_candidates(portal_url):
    parsed = urlsplit(normalise_portal_url(portal_url))
    supplied_path = (parsed.path or "/").rstrip("/")
    if supplied_path.lower().endswith("/c"):
        supplied_path = supplied_path[:-2].rstrip("/")

    paths = []

    def add(path):
        path = "/" + str(path or "").lstrip("/")
        if path not in paths:
            paths.append(path)

    # Respect the path entered by the user before trying generic roots.  This
    # avoids one or two predictable 404s for the common /c/ and
    # /stalker_portal/c/ forms, which is noticeable on slower receivers.
    if supplied_path:
        add(supplied_path + "/server/load.php")
        add(supplied_path + "/portal.php")
    else:
        add("/server/load.php")
    add("/stalker_portal/server/load.php")
    add("/server/load.php")
    add("/portal.php")

    return tuple(
        urlunsplit((parsed.scheme, parsed.netloc, path, "", ""))
        for path in paths
    )


def _endpoint_hint_key(portal_url):
    parsed = urlsplit(normalise_portal_url(portal_url))
    return parsed.scheme.lower(), parsed.netloc.lower(), parsed.path.lower()


def _remember_endpoint_hint(portal_url, endpoint):
    key = _endpoint_hint_key(portal_url)
    with _PORTAL_ENDPOINT_HINTS_LOCK:
        _PORTAL_ENDPOINT_HINTS.pop(key, None)
        _PORTAL_ENDPOINT_HINTS[key] = str(endpoint or "")
        while len(_PORTAL_ENDPOINT_HINTS) > PORTAL_ENDPOINT_HINT_LIMIT:
            _PORTAL_ENDPOINT_HINTS.popitem(last=False)


def _hinted_endpoint_candidates(portal_url):
    candidates = list(portal_endpoint_candidates(portal_url))
    key = _endpoint_hint_key(portal_url)
    with _PORTAL_ENDPOINT_HINTS_LOCK:
        hint = _PORTAL_ENDPOINT_HINTS.get(key, "")
        if hint:
            _PORTAL_ENDPOINT_HINTS.move_to_end(key)
    if hint in candidates:
        candidates.remove(hint)
        candidates.insert(0, hint)
    return tuple(candidates), hint


_REDIRECT_PRIVATE_HEADERS = frozenset(
    ("authorization", "cookie", "referer")
)
_REDIRECT_MANDATORY_COOKIE_NAMES = frozenset(
    ("mac", "stb_lang", "timezone", "token")
)
_REDIRECT_CREDENTIALS_DISABLED = "_gtiptv_redirect_credentials_disabled"
_REDIRECT_MANDATORY_COOKIE = "_gtiptv_redirect_mandatory_cookie"
_REDIRECT_PORTAL_SCOPE = "_gtiptv_redirect_portal_scope"
_REDIRECT_SCOPE_PUBLIC = "public"
_REDIRECT_SCOPE_PRIVATE = "private"
_REDIRECT_SCOPE_UNKNOWN = "unknown"


def _normalised_origin_hostname(value):
    hostname = str(value or "").strip().lower().rstrip(".")
    if not hostname:
        return ""
    try:
        return ipaddress.ip_address(hostname).compressed.lower()
    except ValueError:
        pass
    try:
        return hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError):
        return ""


def _redirect_origin(value):
    try:
        parsed = urlsplit(str(value or ""))
        scheme = str(parsed.scheme or "").lower()
        hostname = _normalised_origin_hostname(parsed.hostname)
        if parsed.username is not None or parsed.password is not None:
            return ()
        port = parsed.port or (443 if scheme == "https" else 80)
    except (TypeError, ValueError):
        return ()
    if scheme not in ("http", "https") or not hostname:
        return ()
    return scheme, hostname, int(port)


def _public_redirect_url(value, resolver=None):
    """Return True for public, False for private, or None if unresolved."""
    answers = []
    selected_resolver = resolver or socket.getaddrinfo

    try:
        parsed_value = urlsplit(str(value or ""))
        scope_value = urlunsplit(
            (
                parsed_value.scheme,
                parsed_value.netloc,
                parsed_value.path,
                parsed_value.query,
                "",
            )
        )
    except (TypeError, ValueError):
        scope_value = value

    def recording_resolver(*args):
        result = selected_resolver(*args)
        answers.extend(result or ())
        return result

    try:
        # URL fragments never reach the network and do not change the target
        # origin, so they are irrelevant to redirect address classification.
        info = validate_public_url_syntax(scope_value)
    except NetworkSecurityError:
        origin = _redirect_origin(value)
        if not origin:
            return None
        hostname = origin[1]
        if (
            hostname in _LOOPBACK_HOSTS
            or hostname.endswith(".localhost")
            or hostname.endswith(".local")
        ):
            return False
        try:
            return True if ipaddress.ip_address(hostname).is_global else False
        except ValueError:
            return None
    hostname = str(info.get("hostname") or "").lower().rstrip(".")
    if (
        not hostname
        or hostname in _LOOPBACK_HOSTS
        or hostname.endswith(".localhost")
        or hostname.endswith(".local")
    ):
        return False
    try:
        # Redirects are exceptional and the following urllib request would
        # resolve this name anyway.  Reuse the public-address validator
        # synchronously so no extra worker or background polling is created.
        resolve_public_addresses(
            hostname,
            info["port"],
            resolver=recording_resolver,
        )
    except NetworkSecurityError:
        # A completed lookup containing a non-public answer is confirmed
        # private only when every usable answer is non-global.  Mixed or
        # malformed answers are unknown: they must not grant the private-
        # portal compatibility exception to a public/ambiguous origin.
        address_scopes = []
        for answer in answers:
            try:
                address = ipaddress.ip_address(
                    str(answer[4][0]).split("%", 1)[0]
                )
            except (IndexError, TypeError, ValueError):
                address_scopes.append(None)
            else:
                address_scopes.append(bool(address.is_global))
        if address_scopes and all(scope is False for scope in address_scopes):
            return False
        return None
    return True


def _redirect_url_scope(value, resolver=None):
    public = _public_redirect_url(value, resolver=resolver)
    if public is True:
        return _REDIRECT_SCOPE_PUBLIC
    if public is False:
        return _REDIRECT_SCOPE_PRIVATE
    return _REDIRECT_SCOPE_UNKNOWN


def _strip_redirect_private_headers(request):
    for values in (
        getattr(request, "headers", {}),
        getattr(request, "unredirected_hdrs", {}),
    ):
        for name in list(values):
            if str(name).lower() in _REDIRECT_PRIVATE_HEADERS:
                values.pop(name, None)


def _strip_redirect_cookie_header(request):
    for values in (
        getattr(request, "headers", {}),
        getattr(request, "unredirected_hdrs", {}),
    ):
        for name in list(values):
            if str(name).lower() == "cookie":
                values.pop(name, None)


def _mandatory_redirect_cookie(value):
    return "; ".join(
        "{}={}".format(name, cookie_value)
        for name, cookie_value in _cookie_parts(value)
        if name.lower() in _REDIRECT_MANDATORY_COOKIE_NAMES
    )


class _PortalRedirectHandler(HTTPRedirectHandler):
    """Keep MAG credentials on-origin and permanently drop them off-origin."""

    # Run after HTTPCookieProcessor so a tainted redirect cannot regain a
    # Cookie header from the session jar on a later hop back to the portal.
    handler_order = HTTPRedirectHandler.handler_order + 100

    def __init__(self, resolver=None):
        HTTPRedirectHandler.__init__(self)
        self._resolver = resolver

    def http_request(self, request):
        if getattr(request, _REDIRECT_CREDENTIALS_DISABLED, False):
            _strip_redirect_private_headers(request)
            return request
        mandatory = str(
            getattr(request, _REDIRECT_MANDATORY_COOKIE, "") or ""
        )
        if mandatory:
            # HTTPCookieProcessor runs before this handler.  The copied Cookie
            # was removed in redirect_request(), so it has now selected the
            # response-updated jar cookies for the redirected path.  Restore
            # only the mandatory MAG identity ahead of that fresh selection.
            merged = _merge_cookie_headers(
                mandatory,
                request.get_header("Cookie", ""),
            )
            _strip_redirect_cookie_header(request)
            if merged:
                request.add_unredirected_header("Cookie", merged)
        return request

    https_request = http_request

    def redirect_request(self, request, fp, code, msg, headers, new_url):
        source_origin = _redirect_origin(request.full_url)
        target_origin = _redirect_origin(new_url)
        portal_scope = getattr(request, _REDIRECT_PORTAL_SCOPE, None)
        if portal_scope is None:
            portal_scope = _redirect_url_scope(
                request.full_url,
                resolver=self._resolver,
            )
        target_scope = _redirect_url_scope(
            new_url,
            resolver=self._resolver,
        )
        unsafe_scope_change = bool(
            (
                target_scope == _REDIRECT_SCOPE_PRIVATE
                and portal_scope != _REDIRECT_SCOPE_PRIVATE
            )
            or (
                portal_scope == _REDIRECT_SCOPE_PUBLIC
                and target_scope != _REDIRECT_SCOPE_PUBLIC
            )
        )
        if unsafe_scope_change:
            raise HTTPError(
                new_url,
                code,
                "Public portal redirect to a local network address is prohibited",
                headers,
                fp,
            )
        if (
            source_origin
            and target_origin
            and source_origin[0] == "https"
            and target_origin[0] == "http"
        ):
            raise HTTPError(
                new_url,
                code,
                "HTTPS portal request cannot be redirected to HTTP",
                headers,
                fp,
            )

        redirected = HTTPRedirectHandler.redirect_request(
            self,
            request,
            fp,
            code,
            msg,
            headers,
            new_url,
        )
        if redirected is None:
            return None
        setattr(redirected, _REDIRECT_PORTAL_SCOPE, portal_scope)
        credentials_disabled = bool(
            getattr(request, _REDIRECT_CREDENTIALS_DISABLED, False)
            or not source_origin
            or target_origin != source_origin
        )
        if credentials_disabled:
            setattr(
                redirected,
                _REDIRECT_CREDENTIALS_DISABLED,
                True,
            )
            _strip_redirect_private_headers(redirected)
        else:
            # urllib copies an explicit Cookie header across an on-origin
            # redirect.  Leaving it in place prevents HTTPCookieProcessor from
            # applying a Set-Cookie received with the 30x response and from
            # reselecting cookies for the new path.  Carry only the mandatory
            # MAG identity as private request metadata; http_request() merges
            # it after the cookie jar has rebuilt the redirected header.
            mandatory = _mandatory_redirect_cookie(
                request.get_header("Cookie", "")
            )
            _strip_redirect_cookie_header(redirected)
            if mandatory:
                setattr(
                    redirected,
                    _REDIRECT_MANDATORY_COOKIE,
                    mandatory,
                )
        return redirected


class _PortalSession(object):
    """One in-memory MAG session shared by screens for the same account.

    Tokens and cookies never touch disk.  Sharing only the transport/session
    state keeps category and item caches screen-local while avoiding a second
    handshake when the health screen opens the dashboard or the user returns
    to a content section.
    """

    def __init__(self, account, opener=None, clock=None, shared=False):
        self.identity = tuple(getattr(account, "identity", ()) or ())
        self.clock = clock or time.monotonic
        self.shared = bool(shared)
        self.revoked = False
        self.lock = threading.RLock()
        self.request_slot = threading.BoundedSemaphore(1)
        self.priority_condition = threading.Condition(threading.Lock())
        self.playback_waiters = 0
        self.renewal_in_progress = False
        self.renewal_failure = None
        self.endpoint = ""
        self.token = ""
        # The tuple is replaced atomically and lets a queued request identify
        # the exact shared credential generation it used.  This remains useful
        # when a portal returns the same token text after a forced handshake.
        self.token_generation = 0
        self.credential_snapshot = ("", 0)
        self.profile_attempted = False
        self.profile_attempts = 0
        self.profile_generation = 0
        self.force_ch_link_check = False
        self.profile_active_connections = None
        self.profile_max_connections = None
        self.profile_expiry = ""
        self.token_created_at = 0.0
        self.category_cache = {}
        self.create_link_hints = OrderedDict()
        self.clients = weakref.WeakSet()
        self.last_used = self.clock()
        self.cookie_jar = None
        if opener is None:
            self.cookie_jar = CookieJar()
            self.opener = build_opener(
                HTTPCookieProcessor(self.cookie_jar),
                _PortalRedirectHandler(),
            ).open
        else:
            self.opener = opener

    def touch(self):
        self.last_used = self.clock()


class PortalCatalogStep(object):
    """One bounded, progressive slice of a Stalker movie catalogue."""

    def __init__(
        self,
        items=None,
        cursor=0,
        complete=False,
        trusted_complete=False,
        reason="",
        progressed=False,
    ):
        self.items = list(items or [])
        self.cursor = max(0, int(cursor or 0))
        self.complete = bool(complete)
        self.trusted_complete = bool(trusted_complete)
        self.reason = str(reason or "")
        self.progressed = bool(progressed)


def _register_portal_session_locked(session):
    sessions = _PORTAL_SESSION_INSTANCES.get(session.identity)
    if sessions is None:
        sessions = weakref.WeakSet()
        _PORTAL_SESSION_INSTANCES[session.identity] = sessions
    sessions.add(session)


def _portal_session_for(account, opener=None, clock=None):
    share = opener is None and (clock is None or clock is time.monotonic)
    if not share:
        session = _PortalSession(
            account,
            opener=opener,
            clock=clock,
            shared=False,
        )
        with _PORTAL_SESSIONS_LOCK:
            _register_portal_session_locked(session)
        return session

    key = tuple(getattr(account, "identity", ()) or ())
    now = time.monotonic()
    with _PORTAL_SESSIONS_LOCK:
        for cached_key, session in list(_PORTAL_SESSIONS.items()):
            if (
                now - float(session.last_used) > PORTAL_SESSION_IDLE_SECONDS
                and not session.clients
            ):
                _PORTAL_SESSIONS.pop(cached_key, None)
        session = _PORTAL_SESSIONS.get(key)
        if session is None:
            session = _PortalSession(account, shared=True)
            _PORTAL_SESSIONS[key] = session
        else:
            _PORTAL_SESSIONS.move_to_end(key)
            session.touch()
        _register_portal_session_locked(session)
        for registered_key, sessions in list(
            _PORTAL_SESSION_INSTANCES.items()
        ):
            if not sessions:
                _PORTAL_SESSION_INSTANCES.pop(registered_key, None)
        while len(_PORTAL_SESSIONS) > PORTAL_SESSION_CACHE_LIMIT:
            _PORTAL_SESSIONS.popitem(last=False)
        return session


def _clear_portal_cookie_jar(session):
    cookie_jar = session.cookie_jar
    if cookie_jar is None:
        return
    try:
        cookie_jar.clear()
    except Exception:
        # Cleanup is best-effort after the account file has been committed.
        pass


def _clear_portal_client_playback_caches(session, blocking):
    complete = True
    for client in tuple(session.clients):
        acquired = client._playback_link_lock.acquire(bool(blocking))
        if not acquired:
            complete = False
            continue
        try:
            client._playback_link_cache.clear()
        finally:
            client._playback_link_lock.release()
    if complete:
        session.clients.clear()
    return complete


def _clear_portal_session_state(session, blocking=True):
    session.endpoint = ""
    session.token = ""
    session.token_generation = 0
    session.credential_snapshot = ("", 0)
    session.profile_attempted = False
    session.profile_attempts = 0
    session.profile_generation = 0
    session.force_ch_link_check = False
    session.profile_active_connections = None
    session.profile_max_connections = None
    session.profile_expiry = ""
    session.token_created_at = 0.0
    with session.priority_condition:
        session.renewal_in_progress = False
        session.priority_condition.notify_all()
    session.renewal_failure = None
    session.category_cache.clear()
    session.create_link_hints.clear()
    clients_cleared = _clear_portal_client_playback_caches(
        session,
        blocking,
    )
    _clear_portal_cookie_jar(session)
    return clients_cleared


def _finish_portal_session_purge(session):
    """Finish credential cleanup without delaying a committed deletion."""

    try:
        with session.lock:
            session.request_slot.acquire()
            try:
                _clear_portal_session_state(session)
            finally:
                session.request_slot.release()
    except Exception:
        # Revocation remains authoritative even on unusual receiver runtimes.
        pass


def _purge_portal_session(account):
    """Revoke every live session belonging to one deleted portal account."""

    key = tuple(getattr(account, "identity", ()) or ())
    with _PORTAL_SESSIONS_LOCK:
        shared_session = _PORTAL_SESSIONS.pop(key, None)
        sessions = tuple(_PORTAL_SESSION_INSTANCES.pop(key, ()) or ())
    if shared_session is not None and shared_session not in sessions:
        sessions += (shared_session,)
    if not sessions:
        return False
    for session in sessions:
        # Revocation is the security boundary.  It must be visible before
        # waiting for a request/session lock held by an in-flight operation.
        session.revoked = True
    for session in sessions:
        needs_deferred_cleanup = True
        lock_acquired = False
        slot_acquired = False
        try:
            lock_acquired = session.lock.acquire(False)
            if lock_acquired:
                slot_acquired = session.request_slot.acquire(False)
                if slot_acquired:
                    needs_deferred_cleanup = not _clear_portal_session_state(
                        session,
                        blocking=False,
                    )
                else:
                    # Cached access is already blocked by ``revoked``. Clear
                    # client-local link values now when their locks are free;
                    # token/cookie mutation waits for the active request.
                    _clear_portal_client_playback_caches(
                        session,
                        blocking=False,
                    )
        except Exception:
            # Deletion already committed to disk; cleanup cannot reverse it.
            needs_deferred_cleanup = True
        finally:
            if slot_acquired:
                session.request_slot.release()
            if lock_acquired:
                session.lock.release()
        if needs_deferred_cleanup:
            try:
                worker = threading.Thread(
                    target=_finish_portal_session_purge,
                    args=(session,),
                    name="GTIPTVPortalPurge",
                )
                worker.daemon = True
                worker.start()
            except (RuntimeError, OSError):
                # Revoked guards still prevent any credential-bearing reuse.
                pass
    return True


def purge_all_portal_sessions():
    """Revoke every in-memory MAC session after switching account files."""

    class _PortalIdentity(object):
        pass

    with _PORTAL_SESSIONS_LOCK:
        identities = set(_PORTAL_SESSIONS)
        identities.update(_PORTAL_SESSION_INSTANCES)
    purged = False
    for identity in identities:
        account = _PortalIdentity()
        account.identity = identity
        purged = _purge_portal_session(account) or purged
    with _PORTAL_ENDPOINT_HINTS_LOCK:
        _PORTAL_ENDPOINT_HINTS.clear()
    return purged


def _touch_portal_session(session):
    session.touch()
    if not session.shared:
        return
    key = session.identity
    with _PORTAL_SESSIONS_LOCK:
        if _PORTAL_SESSIONS.get(key) is session:
            _PORTAL_SESSIONS.move_to_end(key)


def _ensure_portal_directory(directory):
    if not directory:
        return
    canonical = os.path.abspath(os.path.dirname(DEFAULT_PORTAL_PATH))
    require_private_owner = os.path.abspath(directory) == canonical
    try:
        status = os.lstat(directory)
    except OSError:
        if not require_private_owner:
            raise OSError(N_("The Stalker account directory could not be created"))
        try:
            os.makedirs(directory, 0o700)
            status = os.lstat(directory)
        except OSError:
            raise OSError(N_("The Stalker account directory could not be created"))
    current_uid = getattr(os, "geteuid", lambda: status.st_uid)()
    if (
        stat.S_ISLNK(status.st_mode)
        or not stat.S_ISDIR(status.st_mode)
        or (require_private_owner and status.st_uid != current_uid)
    ):
        raise OSError(N_("The Stalker account directory is unsafe"))
    if require_private_owner:
        os.chmod(directory, 0o700)


def _validate_portal_file_status(status):
    current_uid = getattr(os, "geteuid", lambda: status.st_uid)()
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != current_uid
        or status.st_nlink != 1
    ):
        raise OSError(N_("The Stalker account file is unsafe"))


def ensure_portal_file(path=DEFAULT_PORTAL_PATH):
    directory = os.path.dirname(path)
    _ensure_portal_directory(directory)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError:
        try:
            status = os.lstat(path)
        except OSError:
            raise OSError(N_("The Stalker account file could not be created"))
        _validate_portal_file_status(status)
        created = False
    else:
        payload = _PORTAL_FILE_TEMPLATE
        try:
            while payload:
                written = os.write(descriptor, payload)
                if written <= 0:
                    raise OSError(N_("The Stalker account file could not be created"))
                payload = payload[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            descriptor = None
        created = True
    finally:
        if descriptor is not None:
            os.close(descriptor)
    os.chmod(path, 0o600)
    return created


def _read_portal_lines(path):
    try:
        path_status = os.lstat(path)
    except FileNotFoundError:
        return [], False
    _validate_portal_file_status(path_status)
    if path_status.st_size > MAX_PORTAL_BYTES:
        raise ValueError(N_("The Stalker account file is too large"))
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        status = os.fstat(descriptor)
        _validate_portal_file_status(status)
        if (
            status.st_dev != path_status.st_dev
            or status.st_ino != path_status.st_ino
        ):
            raise OSError(N_("The Stalker account file changed while opening"))
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            payload = handle.read(MAX_PORTAL_BYTES + 1)
    finally:
        if descriptor is not None:
            os.close(descriptor)
    if len(payload) > MAX_PORTAL_BYTES:
        raise ValueError(N_("The Stalker account file is too large"))
    return payload.decode("utf-8-sig", "replace").splitlines(), True


def _parse_portal_lines(lines, limit=None):
    accounts = []
    seen = set()
    invalid_lines = 0
    current_url = ""
    for line in lines:
        value = str(line or "").strip()
        if not value or value.startswith("#"):
            continue
        if value.lower().startswith(("http://", "https://")):
            try:
                current_url = normalise_portal_url(value)
            except ValueError:
                current_url = ""
                invalid_lines += 1
            continue
        try:
            mac = normalise_mac(value)
        except ValueError:
            invalid_lines += 1
            continue
        if not current_url:
            invalid_lines += 1
            continue
        account = PortalAccount(current_url, mac)
        key = (account.portal_url.lower(), account.mac)
        if key in seen:
            continue
        seen.add(key)
        accounts.append(account)
        if limit is not None and len(accounts) >= limit:
            break
    return accounts, invalid_lines


def load_portal_accounts(path=None):
    explicit_path = path is not None
    if not explicit_path:
        try:
            from .playlist_files import selected_playlist_path

            path = selected_playlist_path("stalker")
        except (IOError, OSError, TypeError, ValueError) as error:
            return PortalLoadResult([], [], 1, str(error))
        if not path:
            return PortalLoadResult(
                [],
                [],
                0,
                N_("No valid Stalker / MAC account was found"),
            )
    try:
        if explicit_path:
            ensure_portal_file(path)
        lines, readable = _read_portal_lines(path)
    except (IOError, OSError, ValueError) as error:
        return PortalLoadResult([], [], 1, str(error))
    accounts, invalid_lines = _parse_portal_lines(lines)
    for account in accounts:
        account.source_path = path
    sources = [path] if readable else []
    if not readable:
        error = N_("The Stalker account file was not found")
    elif not accounts:
        error = N_("No valid Stalker / MAC account was found")
    else:
        error = ""
    return PortalLoadResult(accounts, sources, invalid_lines, error)


def _group_accounts(accounts):
    groups = []
    indexes = {}
    for account in accounts:
        key = account.portal_url.lower()
        index = indexes.get(key)
        if index is None:
            indexes[key] = len(groups)
            groups.append([account.portal_url, []])
            index = len(groups) - 1
        if account.mac not in groups[index][1]:
            groups[index][1].append(account.mac)
    return groups


def _check_portal_mutation_limits(cancel_event=None, deadline=None):
    if cancel_event is not None and cancel_event.is_set():
        raise OSError(N_("Request cancelled"))
    if deadline is None:
        return
    try:
        expired = time.monotonic() >= float(deadline)
    except (TypeError, ValueError, OverflowError):
        expired = True
    if expired:
        raise OSError(N_("Request timed out"))


def _write_portal_accounts(
    accounts,
    path,
    cancel_event=None,
    deadline=None,
):
    blocks = []
    for portal_url, macs in _group_accounts(accounts):
        blocks.append("\n".join([portal_url] + macs))
    payload = (("\n\n".join(blocks) + "\n") if blocks else "").encode("utf-8")
    if len(payload) > MAX_PORTAL_BYTES:
        raise ValueError(N_("The Stalker account file is too large"))
    directory = os.path.dirname(path)
    _ensure_portal_directory(directory)
    try:
        existing_status = os.lstat(path)
    except FileNotFoundError:
        existing_status = None
    if existing_status is not None:
        _validate_portal_file_status(existing_status)
    temporary_path = "{}.tmp-{}-{}".format(
        path,
        os.getpid(),
        threading.current_thread().ident or 0,
    )
    descriptor = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(temporary_path, flags, 0o600)
        try:
            while payload:
                written = os.write(descriptor, payload)
                if written <= 0:
                    raise OSError(N_("The Stalker account file could not be saved"))
                payload = payload[written:]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
            descriptor = None
        # This is the final cancellation boundary.  After atomic replace the
        # deletion is authoritative and cleanup must not report it as failed.
        _check_portal_mutation_limits(cancel_event, deadline)
        replace = getattr(os, "replace", os.rename)
        replace(temporary_path, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            # The staged file was created as 0600. A post-commit chmod failure
            # must not turn a completed account deletion into a false error.
            pass
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            temporary_status = os.lstat(temporary_path)
        except OSError:
            temporary_status = None
        if temporary_status is not None and stat.S_ISREG(temporary_status.st_mode):
            try:
                os.unlink(temporary_path)
            except OSError:
                pass


def save_portal_account(portal_url, mac, path=None):
    register_path = path is None
    if register_path:
        from .playlist_files import playlist_write_path

        path = playlist_write_path("stalker", DEFAULT_PORTAL_PATH)
        if not os.path.lexists(path):
            ensure_portal_file(path)
    account = PortalAccount(portal_url, mac)
    lines, unused_readable = _read_portal_lines(path)
    del unused_readable
    accounts, unused_invalid = _parse_portal_lines(lines)
    del unused_invalid
    key = (account.portal_url.lower(), account.mac)
    if any((item.portal_url.lower(), item.mac) == key for item in accounts):
        raise ValueError(N_("This Stalker / MAC account already exists"))
    if len(accounts) >= MAX_PORTAL_ACCOUNTS:
        raise ValueError(N_("The Stalker / MAC account limit was reached"))
    accounts.append(account)
    _write_portal_accounts(accounts, path)
    account.source_path = path
    if register_path:
        from .playlist_files import register_written_playlist

        register_written_playlist("stalker", path)
    return account


def delete_portal_account(
    account,
    path=None,
    cancel_event=None,
    deadline=None,
):
    if not is_portal_account(account):
        raise ValueError(N_("Invalid Stalker / MAC account"))
    _check_portal_mutation_limits(cancel_event, deadline)
    if path is None:
        path = str(getattr(account, "source_path", "") or "")
        if not path:
            try:
                from .playlist_files import selected_playlist_path

                path = selected_playlist_path("stalker") or DEFAULT_PORTAL_PATH
            except (IOError, OSError, TypeError, ValueError):
                path = DEFAULT_PORTAL_PATH
    lines, readable = _read_portal_lines(path)
    if not readable:
        raise ValueError(N_("The Stalker account file was not found"))
    accounts, unused_invalid = _parse_portal_lines(lines)
    del unused_invalid
    key = (account.portal_url.lower(), account.mac)
    remaining = [
        item
        for item in accounts
        if (item.portal_url.lower(), item.mac) != key
    ]
    if len(remaining) == len(accounts):
        raise ValueError(N_("The Stalker / MAC account was not found"))
    _write_portal_accounts(
        remaining,
        path,
        cancel_event=cancel_event,
        deadline=deadline,
    )
    try:
        _purge_portal_session(account)
    except Exception:
        # The account-file replacement has committed. Cleanup is best-effort
        # and cannot change the authoritative success result.
        pass
    return 1


def delete_portal_accounts(
    accounts,
    path=None,
    cancel_event=None,
    deadline=None,
):
    """Remove several portal accounts with one guarded rewrite per TXT file."""
    requested = tuple(accounts or ())
    if not requested:
        return 0
    for account in requested:
        if not is_portal_account(account):
            raise ValueError(N_("Invalid Stalker / MAC account"))

    accounts_by_path = {}
    if path is not None:
        accounts_by_path[str(path)] = list(requested)
    else:
        unresolved = []
        for account in requested:
            source_path = str(getattr(account, "source_path", "") or "")
            if source_path:
                accounts_by_path.setdefault(source_path, []).append(account)
            else:
                unresolved.append(account)
        if unresolved:
            try:
                from .playlist_files import selected_playlist_path

                fallback = selected_playlist_path("stalker") or DEFAULT_PORTAL_PATH
            except (IOError, OSError, TypeError, ValueError):
                fallback = DEFAULT_PORTAL_PATH
            accounts_by_path.setdefault(fallback, []).extend(unresolved)

    _check_portal_mutation_limits(cancel_event, deadline)
    updates = []
    found = set()
    requested_keys = set(
        (account.portal_url.lower(), account.mac)
        for account in requested
    )
    for source_path, path_accounts in accounts_by_path.items():
        lines, readable = _read_portal_lines(source_path)
        if not readable:
            continue
        stored_accounts, unused_invalid = _parse_portal_lines(lines)
        del unused_invalid
        target_keys = set(
            (account.portal_url.lower(), account.mac)
            for account in path_accounts
        )
        remaining = []
        for stored in stored_accounts:
            key = (stored.portal_url.lower(), stored.mac)
            if key in target_keys:
                found.add(key)
            else:
                remaining.append(stored)
        updates.append((source_path, remaining))

    if found != requested_keys:
        raise ValueError(N_("The Stalker / MAC account was not found"))
    _check_portal_mutation_limits(cancel_event, deadline)
    # Once the first authoritative replacement starts, finish every prepared
    # rewrite. A late cancellation must not report failure after data changed.
    for source_path, remaining in updates:
        _write_portal_accounts(remaining, source_path)
    for account in requested:
        try:
            _purge_portal_session(account)
        except Exception:
            pass
    return len(requested_keys)


def load_all_accounts():
    """Compatibility alias for integrations written before source unification."""
    from .playlist import load_sources

    return load_sources()


class StalkerPortalClient(object):
    """Single-account Stalker adapter for the existing GT content/player UI."""

    source_type = PORTAL_PROVIDER
    capabilities = PortalAccount.capabilities

    def __init__(self, account, timeout=DEFAULT_TIMEOUT, opener=None, clock=None):
        if not is_portal_account(account):
            raise ContentError(
                N_("Invalid Stalker / MAC account"),
                ContentError.UNKNOWN,
            )
        self.account = account
        self.timeout = max(1.0, float(timeout or DEFAULT_TIMEOUT))
        self.clock = clock or time.monotonic
        self._session = _portal_session_for(
            account,
            opener=opener,
            clock=clock,
        )
        self._opener = self._session.opener
        self._request_slot = self._session.request_slot
        self._session_lock = self._session.lock
        self._request_context = threading.local()
        self._category_cache = None
        self._category_cached_at = 0.0
        self._channel_cache = None
        self._channel_cached_at = 0.0
        self._live_catalog_states = OrderedDict()
        self._live_catalog_lock = threading.RLock()
        self._vod_category_cache = {}
        self._vod_category_sources = {}
        self._vod_raw_cache = OrderedDict()
        self._vod_entry_cache = OrderedDict()
        self._vod_entry_sources = {}
        self._vod_catalog_states = OrderedDict()
        self._vod_catalog_lock = threading.RLock()
        self._movie_search_state_lock = threading.RLock()
        self._movie_search_dialect_hint = ""
        self._movie_search_page_fingerprints = OrderedDict()
        self._series_info_cache = OrderedDict()
        self._series_parameter_hints = {}
        self._episode_command_cache = OrderedDict()
        self._playback_link_cache = OrderedDict()
        self._playback_link_lock = threading.RLock()
        with self._session_lock:
            self._session.clients.add(self)
        self._live_channel_meta = OrderedDict()
        self._epg_policy = None
        self._epg_empty_cache = OrderedDict()
        self._epg_diagnostic_logged = False

    async_playback_resolution = True
    vod_catalog_pace_seconds = PORTAL_CATALOG_PACE_SECONDS

    def supports(self, capability):
        return str(capability or "").lower() in self.capabilities

    def _shared_categories(self, content_type):
        with self._session_lock:
            cached = self._session.category_cache.get(content_type)
            if not cached:
                return None, ""
            cached_at, categories, request_type = cached
            if self.clock() - float(cached_at) >= CONTENT_CACHE_SECONDS:
                self._session.category_cache.pop(content_type, None)
                return None, ""
            return list(categories), str(request_type or "")

    def _store_shared_categories(
        self,
        content_type,
        categories,
        request_type="",
    ):
        with self._session_lock:
            self._session.category_cache[content_type] = (
                self.clock(),
                tuple(categories or ()),
                str(request_type or ""),
            )

    @property
    def _endpoint(self):
        return self._session.endpoint

    @_endpoint.setter
    def _endpoint(self, value):
        value = str(value or "")
        if self._session.revoked and value:
            return
        self._session.endpoint = value

    @property
    def _token(self):
        return self._session.token

    @_token.setter
    def _token(self, value):
        value = str(value or "")
        if self._session.revoked and value:
            return
        previous = str(self._session.token or "")
        self._session.token = value
        if value != previous:
            self._session.token_generation = int(
                self._session.token_generation or 0
            ) + 1
        self._session.credential_snapshot = (
            value,
            int(self._session.token_generation or 0),
        )

    def _credential_snapshot(self):
        snapshot = getattr(self._session, "credential_snapshot", None)
        if (
            isinstance(snapshot, tuple)
            and len(snapshot) == 2
            and str(snapshot[0] or "") == str(self._session.token or "")
        ):
            return str(snapshot[0] or ""), int(snapshot[1] or 0)
        # Compatibility for a session object populated by an older integration
        # through the public-in-practice ``session.token`` field.
        generation = int(getattr(self._session, "token_generation", 0) or 0)
        token = str(self._session.token or "")
        self._session.credential_snapshot = (token, generation)
        return token, generation

    def _token_is_expired(self):
        if not self._endpoint or not self._token:
            return False
        try:
            created_at = float(self._session.token_created_at or 0.0)
            age = float(self.clock()) - created_at
        except (TypeError, ValueError, OverflowError):
            return False
        # Unknown timestamps are retained for backwards compatibility with
        # integrations that seed an already-authenticated client in memory.
        return bool(
            created_at > 0.0
            and age >= PORTAL_TOKEN_MAX_AGE_SECONDS
        )

    def _recent_renewal_failure(self, generation):
        """Recreate a credential-safe failure shared by queued waiters."""

        record = self._session.renewal_failure
        if not isinstance(record, tuple) or len(record) != 6:
            return None
        (
            failed_generation,
            result_generation,
            failed_at,
            reason,
            http_status,
            message,
        ) = record
        try:
            age = float(self.clock()) - float(failed_at)
        except (TypeError, ValueError, OverflowError):
            self._session.renewal_failure = None
            return None
        if age < 0.0 or age >= PORTAL_RENEWAL_FAILURE_COOLDOWN_SECONDS:
            self._session.renewal_failure = None
            return None
        try:
            generation = int(generation)
        except (TypeError, ValueError, OverflowError):
            return None
        if generation not in (failed_generation, result_generation):
            return None
        return ContentError(message, reason, http_status=http_status)

    def _remember_renewal_failure(self, generation, error):
        """Keep only bounded classification data, never provider secrets."""

        if not isinstance(error, ContentError) or error.reason in (
            ContentError.CANCELLED,
            ContentError.DEADLINE,
        ):
            return
        try:
            http_status = int(error.http_status)
        except (TypeError, ValueError, OverflowError):
            http_status = None
        reason = str(error.reason or ContentError.UNKNOWN)
        message = (
            N_("Portal access denied")
            if reason == ContentError.ACCESS_DENIED
            else N_("Portal authentication failed")
        )
        self._session.renewal_failure = (
            int(generation or 0),
            int(self._credential_snapshot()[1]),
            float(self.clock()),
            reason,
            http_status,
            message,
        )

    @property
    def _profile_attempted(self):
        return bool(self._session.profile_attempted)

    @_profile_attempted.setter
    def _profile_attempted(self, value):
        self._session.profile_attempted = bool(value)

    @property
    def _force_ch_link_check(self):
        return bool(self._session.force_ch_link_check)

    @_force_ch_link_check.setter
    def _force_ch_link_check(self, value):
        self._session.force_ch_link_check = bool(value)

    def profile_connection_limits(self):
        """Return the sanitized connection counters for this MAG session."""
        with self._session_lock:
            return (
                self._session.profile_active_connections,
                self._session.profile_max_connections,
            )

    def profile_connection_snapshot(self):
        """Return the profile generation and its sanitized counters."""
        with self._session_lock:
            return (
                self._session.profile_generation,
                self._session.profile_active_connections,
                self._session.profile_max_connections,
            )

    def _live_category_cache_is_fresh(self):
        now = self.clock()
        if (
            self._category_cache is not None
            and now - self._category_cached_at < CONTENT_CACHE_SECONDS
        ):
            return True
        cached = self._session.category_cache.get("live")
        if not cached:
            return False
        if now - float(cached[0]) < CONTENT_CACHE_SECONDS:
            return True
        self._session.category_cache.pop("live", None)
        return False

    def refresh_profile_for_health(self):
        """Refresh profile-only health data even when catalogues are cached."""
        with self._session_critical():
            previous_attempted = self._profile_attempted
            previous_attempts = self._session.profile_attempts
            generation = self._session.profile_generation
            previous_force = self._force_ch_link_check
            previous_active = self._session.profile_active_connections
            previous_maximum = self._session.profile_max_connections
            previous_expiry = self._session.profile_expiry
            self._profile_attempted = False
            self._session.profile_attempts = 0
            try:
                self._ensure_profile(
                    raise_transient=self._live_category_cache_is_fresh(),
                )
            except Exception:
                self._profile_attempted = previous_attempted
                self._session.profile_attempts = previous_attempts
                self._session.profile_generation = generation
                self._force_ch_link_check = previous_force
                self._session.profile_active_connections = previous_active
                self._session.profile_max_connections = previous_maximum
                self._session.profile_expiry = previous_expiry
                raise
            return generation

    def expiry_for_health(
        self,
        previous_generation,
        deadline,
        cancel_event=None,
    ):
        """Best-effort expiry for this health run, within its original budget."""
        expiry_deadline = min(
            float(deadline), time.monotonic() + ACCOUNT_INFO_HEALTH_TIMEOUT,
        )
        remaining = expiry_deadline - time.monotonic()
        if remaining <= 0:
            return N_("Server does not report expiry")
        acquired = self._session_lock.acquire(timeout=remaining)
        if not acquired:
            return N_("Server does not report expiry")
        try:
            if self._session.revoked:
                return N_("Server does not report expiry")
            if (
                previous_generation is not None
                and self._session.profile_generation != previous_generation
                and self._session.profile_expiry
            ):
                return self._session.profile_expiry
            if (
                not self._endpoint or not self._token
                or expiry_deadline - time.monotonic() < 0.25
            ):
                return N_("Server does not report expiry")
            # Keep the same authenticated MAG session. Do not re-handshake or
            # retry an unsupported optional endpoint, and never cache its raw
            # response (which can contain account credentials/phone numbers).
            with self._session_request_critical():
                with self.request_scope(
                    cancel_event=cancel_event,
                    deadline=expiry_deadline,
                    timeout=ACCOUNT_INFO_HEALTH_TIMEOUT,
                ):
                    value = self._request_endpoint(
                        self._endpoint,
                        (("type", "account_info"), ("action", "get_main_info")),
                    )
                    expiry = portal_expiry(value, account_info=True)
            return expiry or N_("Server does not report expiry")
        except (ContentError, ValueError, TypeError):
            # Categories already proved that the portal is available. A slow,
            # unavailable or unsupported expiry endpoint cannot undo that.
            return N_("Server does not report expiry")
        finally:
            self._session_lock.release()

    @contextmanager
    def request_scope(self, cancel_event=None, deadline=None, timeout=None):
        """Bound all portal requests made by one background UI job."""
        if timeout is not None:
            try:
                timeout = max(0.25, float(timeout))
            except (TypeError, ValueError, OverflowError):
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
                timeout = min(float(timeout), float(previous_timeout))
        self._request_context.limits = (cancel_event, deadline, timeout)
        try:
            self._check_request_limits()
            yield
            self._check_request_limits()
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
        if self._session.revoked:
            raise ContentError(
                N_("Portal request cancelled"),
                ContentError.CANCELLED,
            )
        if cancel_event is not None and cancel_event.is_set():
            raise ContentError(
                N_("Portal request cancelled"),
                ContentError.CANCELLED,
            )
        if deadline is not None and time.monotonic() >= float(deadline):
            raise ContentError(
                N_("Portal request timed out"),
                ContentError.DEADLINE,
            )

    def _paced_catalog_wait(self, seconds=PORTAL_CATALOG_PACE_SECONDS):
        self._check_request_limits()
        cancel_event, deadline, unused_timeout = self._request_limits()
        try:
            delay = max(0.0, float(seconds or 0.0))
        except (TypeError, ValueError, OverflowError):
            delay = PORTAL_CATALOG_PACE_SECONDS
        if deadline is not None:
            delay = min(
                delay,
                max(0.0, float(deadline) - time.monotonic()),
            )
        if delay <= 0:
            self._check_request_limits()
        elif cancel_event is not None:
            cancel_event.wait(delay)
        else:
            time.sleep(delay)
        self._check_request_limits()

    def _request_timeout(self):
        self._check_request_limits()
        unused_event, deadline, scoped_timeout = self._request_limits()
        request_timeout = (
            scoped_timeout if scoped_timeout is not None else self.timeout
        )
        # A UI scope may shorten a socket timeout, but it must never extend
        # the client's transport limit.  Extending it can leave the shared
        # one-request portal slot occupied after a cancelled search screen.
        request_timeout = min(float(self.timeout), float(request_timeout))
        if deadline is None:
            return float(request_timeout)
        return max(
            0.25,
            min(float(request_timeout), float(deadline) - time.monotonic()),
        )

    @contextmanager
    def _session_critical(self):
        """Acquire the shared session lock without defeating job cancellation."""

        while True:
            self._check_request_limits()
            acquired = self._session_lock.acquire(
                timeout=min(0.10, self._request_timeout())
            )
            if acquired:
                break
        try:
            yield
        finally:
            self._session_lock.release()

    @contextmanager
    def _playback_request_priority(self):
        """Let one user-requested link pass queued catalogue/EPG work."""
        depth = int(
            getattr(self._request_context, "playback_priority_depth", 0)
            or 0
        )
        if depth == 0:
            with self._session.priority_condition:
                self._session.playback_waiters += 1
                self._session.priority_condition.notify_all()
        self._request_context.playback_priority_depth = depth + 1
        try:
            yield
        finally:
            next_depth = max(
                0,
                int(
                    getattr(
                        self._request_context,
                        "playback_priority_depth",
                        1,
                    )
                    or 1
                ) - 1,
            )
            if next_depth:
                self._request_context.playback_priority_depth = next_depth
            else:
                try:
                    del self._request_context.playback_priority_depth
                except AttributeError:
                    pass
                with self._session.priority_condition:
                    self._session.playback_waiters = max(
                        0,
                        self._session.playback_waiters - 1,
                    )
                    self._session.priority_condition.notify_all()

    def _has_playback_request_priority(self):
        return bool(
            getattr(self._request_context, "playback_priority_depth", 0)
        )

    @contextmanager
    def _session_request_critical(self):
        """Keep a session-lock owner from yielding its portal request slot.

        A forced handshake owns the shared session lock while it performs its
        network request. If that owner yielded to a newly registered playback
        waiter, playback would wait for the same lock and both threads would
        deadlock. This marker avoids that lock inversion without creating a
        second portal request or inflating the playback waiter count.
        """
        depth = int(
            getattr(self._request_context, "session_critical_depth", 0)
            or 0
        )
        self._request_context.session_critical_depth = depth + 1
        try:
            yield
        finally:
            next_depth = max(
                0,
                int(
                    getattr(
                        self._request_context,
                        "session_critical_depth",
                        1,
                    )
                    or 1
                ) - 1,
            )
            if next_depth:
                self._request_context.session_critical_depth = next_depth
            else:
                try:
                    del self._request_context.session_critical_depth
                except AttributeError:
                    pass

    def _has_session_request_priority(self):
        return bool(
            getattr(self._request_context, "session_critical_depth", 0)
        )

    def _has_request_priority(self):
        return (
            self._has_playback_request_priority()
            or self._has_session_request_priority()
        )

    def _wait_for_playback_request(self):
        if self._has_request_priority():
            return
        while True:
            self._check_request_limits()
            with self._session.priority_condition:
                if self._session.playback_waiters <= 0:
                    return
                self._session.priority_condition.wait(
                    min(0.05, self._request_timeout())
                )

    def _acquire_request_slot(self):
        priority = self._has_request_priority()
        session_critical = self._has_session_request_priority()
        while True:
            if not session_critical:
                with self._session.priority_condition:
                    if self._session.renewal_in_progress:
                        self._session.priority_condition.wait(
                            min(0.05, self._request_timeout())
                        )
                        continue
            if not priority:
                self._wait_for_playback_request()
            self._check_request_limits()
            acquired = self._request_slot.acquire(
                timeout=min(0.10, self._request_timeout())
            )
            if not acquired:
                continue
            with self._session.priority_condition:
                renewal_in_progress = bool(
                    self._session.renewal_in_progress
                )
                playback_waiting = self._session.playback_waiters > 0
            # A renewal can begin after the pre-acquire check.  Give the slot
            # back before an empty/stale credential escapes in that race.
            if renewal_in_progress and not session_critical:
                self._request_slot.release()
                continue
            if priority:
                return
            if not playback_waiting:
                return
            self._request_slot.release()

    @staticmethod
    def _lru_store(cache, key, value, limit):
        evicted = []
        cache.pop(key, None)
        cache[key] = value
        while len(cache) > int(limit):
            evicted.append(cache.popitem(last=False)[0])
        return evicted

    @staticmethod
    def _validate_type(content_type):
        value = str(content_type or "").lower()
        if value not in CONTENT_LABELS:
            raise ContentError(
                N_("Content type unavailable"),
                ContentError.UNSUPPORTED_TYPE,
            )
        return value

    def _server_cookie_header(self, request_url):
        """Select CookieJar values for one exact-origin request URL."""

        cookie_jar = self._session.cookie_jar
        request_url = str(request_url or "").split("#", 1)[0].split("|", 1)[0]
        if (
            cookie_jar is None
            or not request_url
            or self._url_origin(request_url) != self._url_origin(
                self.account.portal_url
            )
        ):
            return ""
        try:
            probe = Request(request_url)
            cookie_jar.add_cookie_header(probe)
            return str(probe.get_header("Cookie", "") or "")
        except Exception:
            # A malformed/expired optional server cookie cannot suppress the
            # mandatory MAG identity cookies below.
            return ""

    def _cookie(self, include_token=False, request_url="", token=None):
        if self._session.revoked:
            return ""
        language = device_language().split("_", 1)[0] or "en"
        mandatory = "mac={}; stb_lang={}; timezone={}".format(
            self.account.mac,
            language,
            quote(_device_timezone(), safe=""),
        )
        if token is None:
            token = self._credential_snapshot()[0]
        if include_token and token:
            encoded_token = quote(str(token), safe="._~+-=/")
            mandatory += "; token={}".format(encoded_token)
        target = str(request_url or self._endpoint or self.account.portal_url)
        server_cookies = self._server_cookie_header(target)
        # A CookieJar token can outlive the bearer credential that superseded
        # it.  Never let that optional server value create two contradictory
        # authentication identities; media requests receive the current token
        # explicitly above, while API requests authenticate with Authorization.
        server_cookies = "; ".join(
            "{}={}".format(name, value)
            for name, value in _cookie_parts(server_cookies)
            if name.lower() != "token"
        )
        # Mandatory MAG identity fields are first and authoritative.  A portal
        # cannot replace them through colliding Set-Cookie names, while every
        # non-conflicting PHP/session cookie is retained.
        return _merge_cookie_headers(mandatory, server_cookies)

    def _headers(self, request_url="", credential=None):
        language = device_language().replace("_", "-") or "en"
        headers = {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "Accept-Language": language,
            "User-Agent": _MAG_USER_AGENT,
        }
        if self._session.revoked:
            return headers
        if credential is None:
            credential = self._credential_snapshot()
        token = str(credential[0] or "")
        headers.update(
            {
                "Cookie": self._cookie(
                    request_url=request_url,
                    token=token,
                ),
                "Referer": self.account.portal_url,
                "X-User-Agent": _MAG_X_USER_AGENT,
            }
        )
        if token:
            headers["Authorization"] = "Bearer {}".format(token)
        return headers

    @staticmethod
    def _url_origin(value):
        return _redirect_origin(value)

    def picon_request_context(self, value):
        """Return MAG headers only for an image on this exact portal origin."""
        if self._session.revoked:
            return {}
        url = _normalise_media_url(value, self.account)
        if not url or self._url_origin(url) != self._url_origin(
            self.account.portal_url
        ):
            return {}
        # The live-list request already established the portal session.  A
        # missing session must not turn a cosmetic picon load into a new,
        # blocking portal handshake that could overlap playback.
        if not self._endpoint or not self._token:
            return {}
        credential = self._credential_snapshot()
        headers = {
            "Cookie": self._cookie(
                include_token=True,
                request_url=url,
                token=credential[0],
            ),
            "Referer": self.account.portal_url,
            "X-User-Agent": _MAG_X_USER_AGENT,
        }
        if credential[0]:
            headers["Authorization"] = "Bearer {}".format(credential[0])
        return {
            "headers": headers,
            "user_agent": _MAG_USER_AGENT,
        }

    @staticmethod
    def _json_value(payload):
        if not isinstance(payload, dict):
            raise ContentError(
                N_("Invalid portal response"),
                ContentError.INVALID_RESPONSE,
            )
        # Some MAG forks return HTTP 200 and keep an empty ``data`` or ``cmd``
        # member beside ``error: not valid token``.  Those members describe an
        # authentication failure, not a successful empty catalogue.  Detect
        # only explicit error-envelope fields and never inspect catalogue rows.
        if _semantic_auth_error(payload):
            raise ContentError(
                N_("Portal access denied"),
                ContentError.ACCESS_DENIED,
            )
        value = payload.get("js", payload)
        if isinstance(value, dict):
            error = value.get("error") or value.get("message")
            if error and not any(
                key in value for key in ("token", "data", "cmd", "id")
            ) and not _has_content_identity(value):
                lowered = str(error).lower()
                if any(
                    marker in lowered
                    for marker in (
                        "auth",
                        "token",
                        "access denied",
                        "not valid",
                    )
                ):
                    raise ContentError(
                        N_("Portal access denied"),
                        ContentError.ACCESS_DENIED,
                    )
                raise ContentError(
                    N_("Portal request failed"),
                    ContentError.CONTENT_RESPONSE,
                )
        elif isinstance(value, str):
            lowered = value.lower()
            if any(
                marker in lowered
                for marker in ("authorization", "invalid token", "access denied")
            ):
                raise ContentError(
                    N_("Portal access denied"),
                    ContentError.ACCESS_DENIED,
                )
        return value

    @staticmethod
    def _set_response_timeout(response, timeout):
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
        reader = getattr(response, "read1", None)
        if not callable(reader):
            reader = response.read
        chunks = []
        total = 0
        limit = MAX_API_BYTES + 1
        while total < limit:
            self._check_request_limits()
            self._set_response_timeout(response, self._request_timeout())
            chunk = reader(min(API_READ_CHUNK_BYTES, limit - total))
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

    def _request_endpoint(self, endpoint, parameters, authenticated=True):
        query = list(parameters or ())
        query.append(("JsHttpRequest", "1-xml"))
        url = endpoint + "?" + urlencode(query)
        request_token = None
        request_generation = None
        try:
            self._acquire_request_slot()
            try:
                self._check_request_limits()
                if bool(
                    getattr(
                        self._request_context,
                        "reset_cookie_jar_before_request",
                        False,
                    )
                ):
                    # Clear immediately after taking the shared transport slot,
                    # so an older request cannot repopulate the jar between the
                    # reset and this forced handshake.
                    _clear_portal_cookie_jar(self._session)
                    self._request_context.reset_cookie_jar_before_request = False
                credential = self._credential_snapshot()
                request_token, request_generation = credential
                request = Request(
                    url,
                    headers=self._headers(url, credential=credential),
                )
                response = self._opener(
                    request,
                    timeout=self._request_timeout(),
                )
                try:
                    body = self._read_response_body(response)
                    self._check_request_limits()
                finally:
                    close = getattr(response, "close", None)
                    if close is not None:
                        close()
            finally:
                self._request_slot.release()
        except HTTPError as error:
            try:
                error.close()
            except Exception:
                pass
            log_event("portal", "Portal HTTP failure", error)
            if error.code in (401, 403):
                if authenticated:
                    access_error = ContentError(
                        N_("Portal access denied"),
                        ContentError.ACCESS_DENIED,
                        http_status=error.code,
                    )
                    access_error.portal_request_token = request_token
                    access_error.portal_request_generation = request_generation
                    raise access_error
                raise ContentError(
                    N_("Portal authentication failed"),
                    ContentError.ACCESS_DENIED,
                    http_status=error.code,
                )
            if error.code == 429:
                raise ContentError(
                    N_("Portal request limit reached"),
                    ContentError.RATE_LIMITED,
                    http_status=error.code,
                )
            raise ContentError(
                N_("Portal HTTP request failed"),
                ContentError.HTTP_ERROR,
                http_status=error.code,
            )
        except (URLError, socket.timeout, OSError) as error:
            log_event("portal", "Portal connection failure", error)
            raise ContentError(
                N_("Portal is unavailable"),
                ContentError.CONNECTION,
            )
        finally:
            if self._session.revoked:
                _clear_portal_cookie_jar(self._session)
            _touch_portal_session(self._session)
        if len(body) > MAX_API_BYTES:
            raise ContentError(
                N_("Portal response is too large"),
                ContentError.RESPONSE_TOO_LARGE,
            )
        if not isinstance(body, str):
            body = body.decode("utf-8-sig", "replace")
        try:
            payload = _provider_json_loads(body)
        except (TypeError, ValueError, RecursionError):
            log_event("portal", "Invalid portal JSON")
            raise ContentError(
                N_("Invalid portal response"),
                ContentError.INVALID_RESPONSE,
            )
        try:
            return self._json_value(payload)
        except ContentError as error:
            if authenticated and error.reason == ContentError.ACCESS_DENIED:
                error.portal_request_token = request_token
                error.portal_request_generation = request_generation
            raise

    def _handshake(self, force=False, retry_access=True):
        with self._session_critical():
            unused_token, starting_generation = self._credential_snapshot()
            needs_renewal = bool(
                force
                or self._token_is_expired()
                or not self._endpoint
                or not self._token
            )
            if needs_renewal and not force:
                recent_failure = self._recent_renewal_failure(
                    starting_generation
                )
                if recent_failure is not None:
                    log_event(
                        "portal",
                        "Stalker session renewal reused recent failure",
                    )
                    raise recent_failure
            if needs_renewal:
                with self._session.priority_condition:
                    self._session.renewal_in_progress = True
                    self._session.priority_condition.notify_all()
            try:
                with self._session_request_critical():
                    try:
                        return self._handshake_locked(
                            force,
                            retry_access=retry_access,
                        )
                    except ContentError as error:
                        if needs_renewal:
                            self._remember_renewal_failure(
                                starting_generation,
                                error,
                            )
                        raise
            finally:
                if needs_renewal:
                    with self._session.priority_condition:
                        self._session.renewal_in_progress = False
                        self._session.priority_condition.notify_all()

    def _handshake_locked(self, force=False, retry_access=True):
        expired = self._token_is_expired()
        if self._endpoint and self._token and not force and not expired:
            return self._token
        renewing = bool(force or expired)
        self._endpoint = ""
        self._token = ""
        self._profile_attempted = False
        self._session.profile_attempts = 0
        self._force_ch_link_check = False
        self._session.profile_active_connections = None
        self._session.profile_max_connections = None
        self._session.profile_expiry = ""
        with self._playback_link_lock:
            self._playback_link_cache.clear()
        last_error = None
        candidates, hint = _hinted_endpoint_candidates(
            self.account.portal_url
        )
        cookie_reset_pending = renewing
        for endpoint in candidates:
            previous_cookie_reset = getattr(
                self._request_context,
                "reset_cookie_jar_before_request",
                None,
            )
            if cookie_reset_pending:
                self._request_context.reset_cookie_jar_before_request = True
                cookie_reset_pending = False
            try:
                try:
                    value = self._request_endpoint(
                        endpoint,
                        (
                            ("type", "stb"),
                            ("action", "handshake"),
                            ("token", ""),
                        ),
                        authenticated=False,
                    )
                finally:
                    if previous_cookie_reset is None:
                        try:
                            del self._request_context.reset_cookie_jar_before_request
                        except AttributeError:
                            pass
                    else:
                        self._request_context.reset_cookie_jar_before_request = (
                            previous_cookie_reset
                        )
            except ContentError as error:
                last_error = error
                if error.reason in HANDSHAKE_TERMINAL_REASONS:
                    raise
                if (
                    not retry_access
                    and error.reason == ContentError.ACCESS_DENIED
                ):
                    raise
                if (
                    hint
                    and endpoint == hint
                    and error.reason == ContentError.ACCESS_DENIED
                ):
                    raise
                continue
            token = value.get("token") if isinstance(value, dict) else ""
            token = str(token or "").strip()
            if not token:
                last_error = ContentError(
                    N_("Portal authentication failed"),
                    ContentError.ACCESS_DENIED,
                )
                if not retry_access:
                    raise last_error
                continue
            self._endpoint = endpoint
            self._token = token
            self._session.token_created_at = self.clock()
            self._session.renewal_failure = None
            _remember_endpoint_hint(self.account.portal_url, endpoint)
            if renewing:
                log_event(
                    "portal",
                    "Stalker session renewed reason={}".format(
                        "expired" if expired and not force else "requested"
                    ),
                )
            return token
        if last_error is not None:
            raise last_error
        raise ContentError(
            N_("Portal authentication failed"),
            ContentError.ACCESS_DENIED,
        )

    def _ensure_profile(self, raise_transient=False, raise_access=False):
        """Load one profile response and report whether it was received."""
        with self._session_critical():
            if (
                self._profile_attempted
                or self._session.profile_attempts
                >= PROFILE_REQUEST_MAX_ATTEMPTS
            ):
                return False
            self._session.profile_attempts += 1
            with self._session_request_critical():
                try:
                    value = self._request_endpoint(
                        self._endpoint,
                        (
                            ("type", "stb"),
                            ("action", "get_profile"),
                            ("hd", "1"),
                            ("stb_type", "MAG250"),
                            ("client_type", "STB"),
                            ("image_version", "218"),
                            ("auth_second_step", "1"),
                            ("video_out", "hdmi"),
                            ("not_valid_token", "0"),
                        ),
                    )
                except ContentError as error:
                    # Older portals do not expose get_profile. The handshake
                    # token is still valid, so category discovery remains the
                    # compatibility test instead of rejecting such a server.
                    # Leave one bounded attempt available for a later caller
                    # so a transient first failure cannot disable profile
                    # discovery for the lifetime of the shared handshake.
                    if error.reason in HANDSHAKE_TERMINAL_REASONS:
                        self._session.profile_attempts = max(
                            0,
                            self._session.profile_attempts - 1,
                        )
                        raise
                    if (
                        raise_access
                        and error.reason == ContentError.ACCESS_DENIED
                    ):
                        raise
                    try:
                        http_status = int(error.http_status)
                    except (TypeError, ValueError, OverflowError):
                        http_status = 0
                    transient = bool(
                        error.reason == ContentError.CONNECTION
                        or (
                            error.reason == ContentError.HTTP_ERROR
                            and (
                                http_status == 408
                                or (
                                    500 <= http_status <= 599
                                    and http_status != 501
                                )
                            )
                        )
                    )
                    if transient and raise_transient:
                        raise
                    if (
                        not transient
                        or self._session.profile_attempts
                        >= PROFILE_REQUEST_MAX_ATTEMPTS
                    ):
                        self._profile_attempted = True
                    return False
            active, maximum = self._profile_connection_counts(value)
            self._session.profile_active_connections = active
            self._session.profile_max_connections = maximum
            self._session.profile_expiry = portal_expiry(value)
            self._force_ch_link_check = self._profile_forces_create_link(value)
            self._session.profile_generation += 1
            self._profile_attempted = True
            return True

    def _renew_session_after_access_denied(self, error=None):
        """Renew once, or reuse a credential another waiter already renewed."""

        failed_token = getattr(error, "portal_request_token", None)
        failed_generation = getattr(
            error,
            "portal_request_generation",
            None,
        )
        with self._session_critical():
            lookup_generation = (
                failed_generation
                if failed_generation is not None
                else self._credential_snapshot()[1]
            )
            recent_failure = self._recent_renewal_failure(
                lookup_generation
            )
            if recent_failure is not None:
                log_event(
                    "portal",
                    "Stalker session renewal reused recent failure",
                )
                raise recent_failure
            current_token, current_generation = self._credential_snapshot()
            newer_generation = bool(
                failed_generation is not None
                and current_token
                and current_generation != failed_generation
                and not self._token_is_expired()
            )
            newer_legacy_token = bool(
                failed_generation is None
                and failed_token is not None
                and current_token
                and current_token != str(failed_token or "")
                and not self._token_is_expired()
            )
            if newer_generation or newer_legacy_token:
                log_event(
                    "portal",
                    "Stalker session retry reused newer credentials",
                )
                return current_token
            # _handshake() uses the same RLock and marks its request as session
            # critical, so it cannot yield its transport slot to a waiter that
            # is blocked on this renewal.
            return self._handshake(force=True)

    def _request(self, request_type, action, parameters=None, retry=True):
        self._handshake()
        if not (request_type == "stb" and action == "get_profile"):
            self._ensure_profile()
        query = [("type", request_type), ("action", action)]
        if parameters:
            query.extend(parameters)
        if (
            request_type == "itv"
            and action == "get_ordered_list"
            and self._force_ch_link_check
            and not any(
                name == "force_ch_link_check" for name, unused_value in query
            )
        ):
            query.append(("force_ch_link_check", "1"))
        try:
            return self._request_endpoint(self._endpoint, query)
        except ContentError as error:
            if retry and error.reason == ContentError.ACCESS_DENIED:
                try:
                    self._renew_session_after_access_denied(error)
                except ContentError as renewal_error:
                    if renewal_error.reason == ContentError.ACCESS_DENIED:
                        # refresh_playback_url has one legacy compatibility
                        # retry for failures raised outside _request(). Mark a
                        # failed in-boundary renewal so that wrapper cannot
                        # start a second forced handshake or bypass cooldown.
                        renewal_error.portal_auth_retry_exhausted = True
                    raise
                return self._request(
                    request_type,
                    action,
                    parameters,
                    retry=False,
                )
            if error.reason == ContentError.ACCESS_DENIED:
                # Public playback wrappers may provide a compatibility retry
                # for errors raised outside this request boundary.  Mark an
                # already-retried portal failure so they cannot start a second
                # handshake cycle.
                error.portal_auth_retry_exhausted = True
            raise

    @staticmethod
    def _data_list(value):
        def records(candidate, depth=0):
            if depth > 4:
                return []
            if isinstance(candidate, (list, tuple)):
                return list(candidate)
            if not isinstance(candidate, dict):
                return []
            for key in ("data", "items", "channels", "results"):
                if key not in candidate:
                    continue
                result = records(candidate.get(key), depth + 1)
                if result:
                    return result
            record_fields = (
                "id", "ch_id", "channel_id", "stream_id", "name",
                "title", "cmd", "url", "tv_genre_id", "genre_id",
                "video_id", "movie_id", "series_id", "episode_id",
                "file_id",
            )
            if any(key in candidate for key in record_fields):
                return [candidate]
            mapped = [
                item
                for item in candidate.values()
                if isinstance(item, dict)
                and any(key in item for key in record_fields)
            ]
            if mapped:
                return mapped
            for nested in candidate.values():
                result = records(nested, depth + 1)
                if result:
                    return result
            return []

        return records(value)

    @staticmethod
    def _total_items(value):
        if not isinstance(value, dict):
            return None
        for key in ("total_items", "total", "count"):
            candidate = value.get(key)
            try:
                if candidate is not None:
                    return max(0, int(candidate))
            except (TypeError, ValueError):
                pass
        for key in ("data", "items", "channels", "results"):
            nested = value.get(key)
            total = StalkerPortalClient._total_items(nested)
            if total is not None:
                return total
        return None

    @staticmethod
    def _movie_search_text(value):
        """Return the same accent-insensitive title key used by the UI."""

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

    @staticmethod
    def _movie_search_dialect_error(error):
        """Whether one alternate request dialect is safe to try once."""

        if not isinstance(error, ContentError):
            return False
        if error.reason in (
            ContentError.UNSUPPORTED_TYPE,
            ContentError.CONTENT_RESPONSE,
            ContentError.INVALID_RESPONSE,
        ):
            return True
        return bool(
            error.reason == ContentError.HTTP_ERROR
            and error.http_status in (400, 404, 405, 406, 415, 422, 501)
        )

    def _movie_search_schema_supported(self, value):
        if isinstance(value, (list, tuple)):
            return True
        if not isinstance(value, dict):
            return False
        if any(
            key in value for key in ("data", "items", "channels", "results")
        ):
            return True
        if self._data_list(value):
            return True
        return self._total_items(value) is not None

    def _movie_search_request_types(self):
        with self._movie_search_state_lock:
            hint = self._movie_search_dialect_hint
        if hint == "movie":
            return ("movie", "vod")
        return ("vod", "movie")

    def _remember_movie_search_dialect(self, request_type):
        request_type = str(request_type or "").strip().lower()
        if request_type not in ("vod", "movie"):
            return
        with self._movie_search_state_lock:
            self._movie_search_dialect_hint = request_type

    @staticmethod
    def _movie_search_query_digest(query_key):
        return hashlib.sha256(
            str(query_key or "").encode("utf-8", "replace")
        ).digest()[:16]

    @staticmethod
    def _movie_search_page_fingerprint(candidates):
        digest = hashlib.sha256()
        for identity, item, unused_entry in candidates:
            digest.update(str(identity[1]).encode("utf-8", "replace"))
            digest.update(b"\0")
            digest.update(
                StalkerPortalClient._movie_search_text(item.name).encode(
                    "utf-8", "replace"
                )
            )
            digest.update(b"\n")
        return digest.digest()[:16]

    def _movie_search_page_repeated(
        self,
        query_digest,
        request_type,
        page,
        fingerprint,
    ):
        key = (bytes(query_digest), str(request_type or ""))
        page = max(1, int(page or 1))
        with self._movie_search_state_lock:
            pages = self._movie_search_page_fingerprints.get(key)
            if pages is None:
                pages = OrderedDict()
                self._movie_search_page_fingerprints[key] = pages
            else:
                self._movie_search_page_fingerprints.move_to_end(key)
            repeated = any(
                other_page < page and other_fingerprint == fingerprint
                for other_page, other_fingerprint in pages.items()
            )
            pages.pop(page, None)
            pages[page] = bytes(fingerprint)
            while len(pages) > PORTAL_MOVIE_SEARCH_PAGE_STATE_LIMIT:
                pages.popitem(last=False)
            while (
                len(self._movie_search_page_fingerprints)
                > PORTAL_MOVIE_SEARCH_STATE_LIMIT
            ):
                self._movie_search_page_fingerprints.popitem(last=False)
            return repeated

    def _unsupported_movie_search_page(self, page, page_size, source):
        return MovieSearchPage(
            items=[],
            page=page,
            page_size=page_size,
            total_items=0,
            has_more=False,
            source=source,
            supported=False,
        )

    def search_movies(self, query, page=1, page_size=14):
        """Search one small portal-side VOD result page.

        Ministra's ``search`` parameter lets the portal do the expensive
        catalogue filtering.  The client therefore requests only the page
        needed by the UI and never falls back to a full catalogue walk.
        """

        try:
            page = max(1, int(page or 1))
        except (TypeError, ValueError, OverflowError):
            page = 1
        try:
            page_size = max(1, min(
                PORTAL_MOVIE_SEARCH_PAGE_SIZE,
                int(page_size or PORTAL_MOVIE_SEARCH_PAGE_SIZE),
            ))
        except (TypeError, ValueError, OverflowError):
            page_size = PORTAL_MOVIE_SEARCH_PAGE_SIZE

        query = str(query or "").strip()[:PORTAL_MOVIE_SEARCH_QUERY_LIMIT]
        query_key = self._movie_search_text(query)
        if not query_key:
            return MovieSearchPage(
                items=[],
                page=page,
                page_size=page_size,
                total_items=0,
                has_more=False,
                source="",
                supported=True,
            )

        parameters = (
            ("search", query),
            ("genre", "*"),
            ("sortby", "name"),
            ("p", str(page)),
        )
        query_terms = tuple(query_key.split())
        query_digest = self._movie_search_query_digest(query_key)

        request_types = self._movie_search_request_types()
        for dialect_index, request_type in enumerate(request_types):
            try:
                value = self._request(
                    request_type,
                    "get_ordered_list",
                    parameters,
                )
            except ContentError as error:
                if not self._movie_search_dialect_error(error):
                    raise
                if dialect_index == 0:
                    continue
                return self._unsupported_movie_search_page(
                    page, page_size, request_type
                )

            if not self._movie_search_schema_supported(value):
                if dialect_index == 0:
                    continue
                return self._unsupported_movie_search_page(
                    page, page_size, request_type
                )

            batch = self._data_list(value)
            if not batch:
                total = self._total_items(value)
                self._remember_movie_search_dialect(request_type)
                return MovieSearchPage(
                    items=[],
                    page=page,
                    page_size=page_size,
                    total_items=max(0, int(total or 0)),
                    has_more=False,
                    source=request_type,
                    supported=True,
                )

            candidates = []
            seen = set()
            for entry in batch[:page_size]:
                if not isinstance(entry, dict):
                    continue
                if self._is_series_entry(entry, False):
                    continue
                item = self._media_item(entry, "movie", request_type)
                if item is None:
                    continue
                identity = ("movie", str(item.stream_id))
                if identity in seen:
                    continue
                seen.add(identity)
                candidates.append((identity, item, entry))

            # Rows were returned, but none use the selected VOD schema.  Give
            # the alternate dialect its single bounded compatibility attempt.
            if not candidates:
                if dialect_index == 0:
                    continue
                return self._unsupported_movie_search_page(
                    page, page_size, request_type
                )

            # A portal which ignores ``search`` usually returns its ordinary
            # first VOD page.  Never expose or deep-scan that unrelated page.
            # Every usable result must contain all normalised title terms.
            if any(
                not all(
                    term in self._movie_search_text(item.name)
                    for term in query_terms
                )
                for unused_identity, item, unused_entry in candidates
            ):
                if dialect_index == 0:
                    continue
                return self._unsupported_movie_search_page(
                    page, page_size, request_type
                )

            self._check_request_limits()
            fingerprint = self._movie_search_page_fingerprint(candidates)
            if self._movie_search_page_repeated(
                query_digest,
                request_type,
                page,
                fingerprint,
            ):
                self._remember_movie_search_dialect(request_type)
                total = self._total_items(value)
                return MovieSearchPage(
                    items=[],
                    page=page,
                    page_size=page_size,
                    total_items=max(0, int(total or 0)),
                    has_more=False,
                    source=request_type,
                    supported=True,
                )

            items = []
            with self._vod_catalog_critical():
                for identity, item, entry in candidates:
                    item.icon = _normalise_media_url(item.icon, self.account)
                    evicted = self._lru_store(
                        self._vod_entry_cache,
                        identity,
                        dict(entry),
                        VOD_ENTRY_CACHE_LIMIT,
                    )
                    for evicted_identity in evicted:
                        self._vod_entry_sources.pop(evicted_identity, None)
                    self._vod_entry_sources[identity] = request_type
                    items.append(item)

            self._remember_movie_search_dialect(request_type)
            total = self._total_items(value)
            offset = (page - 1) * page_size
            observed_total = offset + min(len(batch), page_size)
            total_items = max(int(total or 0), observed_total)
            has_more = bool(
                len(batch) >= page_size
                or (total is not None and page * page_size < int(total))
            )
            return MovieSearchPage(
                items=items,
                page=page,
                page_size=page_size,
                total_items=total_items,
                has_more=has_more,
                source=request_type,
                supported=True,
            )

        return self._unsupported_movie_search_page(page, page_size, "movie")

    @contextmanager
    def _vod_catalog_critical(self):
        """Acquire the search catalogue lock without defeating cancellation."""

        while True:
            self._check_request_limits()
            acquired = self._vod_catalog_lock.acquire(
                timeout=min(0.10, self._request_timeout())
            )
            if acquired:
                break
        try:
            yield
        finally:
            self._vod_catalog_lock.release()

    def invalidate_cache(self, content_type=None, category_id=None):
        content_type = str(content_type or "").strip().lower()
        category_id = None if category_id is None else str(category_id)
        if not content_type or content_type == "live":
            self._category_cache = None
            self._category_cached_at = 0.0
            self._channel_cache = None
            self._channel_cached_at = 0.0
            self._live_channel_meta.clear()
            self._epg_empty_cache.clear()
            with self._live_catalog_lock:
                for key in list(self._live_catalog_states):
                    if category_id is None or key == category_id:
                        self._live_catalog_states.pop(key, None)
            with self._session_lock:
                self._session.category_cache.pop("live", None)
        if not content_type or content_type in ("movie", "series"):
            if content_type:
                self._vod_category_cache.pop(content_type, None)
                self._vod_category_sources.pop(content_type, None)
            else:
                self._vod_category_cache.clear()
                self._vod_category_sources.clear()
            for key in list(self._vod_raw_cache):
                cached_category = key[1]
                if category_id is None or cached_category == category_id:
                    self._vod_raw_cache.pop(key, None)
            for key in list(self._vod_catalog_states):
                cached_type, cached_category = key
                if (
                    (not content_type or cached_type == content_type)
                    and (category_id is None or cached_category == category_id)
                ):
                    self._vod_catalog_states.pop(key, None)
            self._vod_entry_cache.clear()
            self._vod_entry_sources.clear()
            self._series_info_cache.clear()
            self._episode_command_cache.clear()
            with self._session_lock:
                if content_type:
                    self._session.category_cache.pop(content_type, None)
                else:
                    self._session.category_cache.pop("movie", None)
                    self._session.category_cache.pop("series", None)

    def load_categories(self, content_type):
        content_type = self._validate_type(content_type)
        if content_type != "live":
            return self._load_vod_categories(content_type)
        if (
            self._category_cache is not None
            and self.clock() - self._category_cached_at < CONTENT_CACHE_SECONDS
        ):
            return list(self._category_cache)
        shared, unused_request_type = self._shared_categories("live")
        if shared is not None:
            self._category_cache = list(shared)
            self._category_cached_at = self.clock()
            return list(shared)
        value = self._request("itv", "get_genres")
        categories = [ContentCategory("", _(CONTENT_LABELS["live"][1]))]
        seen = set()
        for entry in self._data_list(value)[:MAX_RESULTS]:
            if not isinstance(entry, dict):
                continue
            category_id = str(
                entry.get("id") or entry.get("genre_id") or ""
            ).strip()
            if not category_id or category_id in seen:
                continue
            seen.add(category_id)
            category = ContentCategory(
                category_id,
                entry.get("title") or entry.get("name"),
            )
            category.portal_aliases = self._category_aliases(entry)
            categories.append(category)
        self._category_cache = list(categories)
        self._category_cached_at = self.clock()
        self._store_shared_categories("live", categories)
        return categories

    def _load_vod_categories(self, content_type):
        cached = self._vod_category_cache.get(content_type)
        if cached and self.clock() - cached[0] < CONTENT_CACHE_SECONDS:
            return list(cached[1])
        shared, shared_request_type = self._shared_categories(content_type)
        if shared is not None:
            self._vod_category_cache[content_type] = (
                self.clock(),
                list(shared),
            )
            if shared_request_type:
                self._vod_category_sources[content_type] = shared_request_type
            return list(shared)

        request_types = ["vod"]
        if content_type not in request_types:
            request_types.append(content_type)
        if content_type == "series":
            request_types = ["series", "vod"]
        entries = []
        selected_request_type = ""
        last_error = None
        successful_response = False
        for request_type in request_types:
            for action in ("get_categories", "get_genres"):
                try:
                    value = self._request(request_type, action)
                except ContentError as error:
                    if error.reason in REQUEST_ABORT_REASONS:
                        raise
                    last_error = error
                    continue
                successful_response = True
                entries = self._data_list(value)
                if entries:
                    selected_request_type = request_type
                    break
            if entries:
                break
        # A successful empty response is not a connection failure.  Series
        # portals frequently expose only one of the two Stalker dialects; keep
        # the top-level DİZİLER category visible so the bounded catalogue probe
        # can classify the outcome safely.
        if (
            content_type != "series"
            and not entries
            and not successful_response
            and last_error is not None
        ):
            raise last_error

        categories = [
            ContentCategory("", _(CONTENT_LABELS[content_type][1]))
        ]
        seen = set(("", "*"))
        for entry in entries[:MAX_RESULTS]:
            if not isinstance(entry, dict):
                continue
            category_id = str(
                entry.get("id")
                or entry.get("category_id")
                or entry.get("genre_id")
                or entry.get("category_alias")
                or ""
            ).strip()
            if not category_id or category_id in seen:
                continue
            seen.add(category_id)
            category = ContentCategory(
                category_id,
                entry.get("title")
                or entry.get("name")
                or entry.get("category_name"),
            )
            category.portal_aliases = self._category_aliases(entry)
            categories.append(category)
        self._vod_category_cache[content_type] = (
            self.clock(),
            list(categories),
        )
        if selected_request_type:
            self._vod_category_sources[content_type] = selected_request_type
        self._store_shared_categories(
            content_type,
            categories,
            selected_request_type,
        )
        if content_type == "series" and not entries:
            try:
                log_event(
                    "portal",
                    "Series categories outcome={}".format(
                        "empty" if successful_response else "unavailable"
                    ),
                )
            except Exception:
                pass
        return categories

    @staticmethod
    def _numeric_entry_id(entry, keys, allow_zero=False):
        if not isinstance(entry, dict):
            return ""
        for key in keys:
            value = str(entry.get(key) or "").strip()
            if (
                value
                and len(value) <= 40
                and all("0" <= character <= "9" for character in value)
                and (allow_zero or bool(value.strip("0")))
            ):
                return value
        return ""

    @staticmethod
    def _composite_entry_id(entry, keys):
        """Return an exact, bounded numeric-composite provider object ID."""
        if not isinstance(entry, dict):
            return ""
        for key in keys:
            value = safe_composite_id(entry.get(key))
            if value:
                return value
        return ""

    @staticmethod
    def _series_object_entry_id(entry, keys, allow_zero=False):
        """Accept the legacy numeric ID or the verified series ID dialect."""
        value = StalkerPortalClient._numeric_entry_id(
            entry,
            keys,
            allow_zero=allow_zero,
        )
        if value:
            return value
        return StalkerPortalClient._composite_entry_id(entry, keys)

    @staticmethod
    def _command_media_id(entry, content_type):
        command = StalkerPortalClient._channel_command(entry)
        if (
            not command
            or len(command) > 8192
            or _CONTROL_PATTERN.search(command)
        ):
            return ""
        while _COMMAND_PREFIX.match(command):
            command = _COMMAND_PREFIX.sub("", command, count=1).strip()
        command = command.strip("\"'")
        if "#" in command:
            command = command.split("#", 1)[0]
        if "|" in command:
            candidate, headers = command.split("|", 1)
            if "=" in headers:
                command = candidate
        try:
            path = urlsplit(command).path
        except (TypeError, ValueError):
            return ""
        match = _MEDIA_COMMAND_PATH_ID.search(str(path or ""))
        if match is None:
            return ""
        file_marker, media_id, series_suffix = match.groups()
        if not media_id.strip("0"):
            return ""
        if content_type == "series":
            return "" if file_marker else media_id
        if content_type == "movie":
            return "" if file_marker or series_suffix else media_id
        if content_type == "episode":
            return media_id if file_marker and not series_suffix else ""
        return ""

    @staticmethod
    def _media_identifier(
        entry,
        content_type,
        allow_series_composite=False,
        allow_episode_composite=False,
    ):
        content_type = str(content_type or "").strip().lower()
        if content_type == "series":
            keys = (
                "series_id", "movie_id", "video_id", "id", "stream_id",
            )
        elif content_type == "movie":
            keys = ("id", "video_id", "movie_id", "stream_id")
        elif content_type == "episode":
            keys = ("episode_id", "file_id", "stream_id", "id")
        else:
            keys = (
                "id", "ch_id", "channel_id", "stream_id", "episode_id",
                "file_id", "series_id", "movie_id", "video_id",
            )
        media_id = StalkerPortalClient._numeric_entry_id(entry, keys)
        if media_id:
            return media_id
        media_id = StalkerPortalClient._command_media_id(
            entry,
            content_type,
        )
        if media_id:
            return media_id
        if content_type == "series" and allow_series_composite:
            return StalkerPortalClient._composite_entry_id(
                entry,
                ("series_id", "id", "stream_id"),
            )
        if content_type == "episode" and allow_episode_composite:
            return StalkerPortalClient._composite_entry_id(
                entry,
                ("episode_id", "file_id", "stream_id", "id"),
            )
        return ""

    @staticmethod
    def _media_identity(
        entry,
        content_type="",
        allow_series_composite=False,
    ):
        if not isinstance(entry, dict):
            return ""
        value = StalkerPortalClient._media_identifier(
            entry,
            content_type,
            allow_series_composite=allow_series_composite,
        )
        if value:
            return value
        value = entry.get("cmd") or entry.get("url") or ""
        return str(value).strip()

    def _paged_vod_request(
        self,
        request_type,
        parameters,
        entry_filter=None,
    ):
        entries = []
        seen = set()
        page = 1
        tried_zero_page = False
        while page <= MAX_PORTAL_PAGES and len(entries) < MAX_RESULTS:
            query = list(parameters or ())
            query.append(("p", str(page)))
            try:
                value = self._request(
                    request_type,
                    "get_ordered_list",
                    tuple(query),
                )
            except ContentError as error:
                if error.reason in REQUEST_ABORT_REASONS:
                    raise
                if entries:
                    break
                raise
            raw_batch = self._data_list(value)
            batch = raw_batch
            if entry_filter is not None:
                batch = [
                    entry for entry in batch
                    if entry_filter(entry)
                ]
                if raw_batch and not batch:
                    # The portal answered but ignored this hierarchy dialect.
                    # Stop after the schema probe instead of scanning its
                    # unrelated global catalogue or trying page zero.
                    break
            if not batch and page == 1 and not tried_zero_page:
                page = 0
                tried_zero_page = True
                continue
            if not batch:
                break
            added = 0
            for entry in batch:
                if not isinstance(entry, dict):
                    continue
                identity = self._media_identity(entry)
                if not identity:
                    identity = repr(sorted(entry.items()))
                if identity in seen:
                    continue
                seen.add(identity)
                entries.append(entry)
                added += 1
                if len(entries) >= MAX_RESULTS:
                    break
            if not added:
                break
            if page == 0:
                break
            # VOD totals are advisory on Ministra forks.  Walking to the first
            # empty or repeated page gives a bounded confirmation and prevents
            # a page-sized ``count`` or ``total_items`` from truncating data.
            page += 1
            if page <= MAX_PORTAL_PAGES and len(entries) < MAX_RESULTS:
                self._paced_catalog_wait()
        return entries

    def _load_ordered_media(self, request_type, category_id):
        category_id = str(category_id or "")
        cache_key = (request_type, category_id)
        now = self.clock()
        for stale_key, stale_value in list(self._vod_raw_cache.items()):
            if now - float(stale_value[0]) >= CONTENT_CACHE_SECONDS:
                self._vod_raw_cache.pop(stale_key, None)
        cached = self._vod_raw_cache.get(cache_key)
        if cached:
            self._vod_raw_cache.move_to_end(cache_key)
            return list(cached[1])
        category = category_id or "*"
        entries = self._paged_vod_request(
            request_type,
            (
                ("category", category),
                ("fav", "0"),
                ("sortby", "name"),
                ("not_ended", "0"),
                ("hd", "0"),
            ),
        )
        self._lru_store(
            self._vod_raw_cache,
            cache_key,
            (self.clock(), list(entries)),
            VOD_RAW_CACHE_LIMIT,
        )
        return entries

    def _catalog_request_types(self, content_type):
        preferred = self._vod_category_sources.get(content_type, "")
        defaults = ["vod", "movie"]
        if content_type == "series":
            defaults = ["series", "vod"]
        ordered = []
        for request_type in [preferred] + defaults:
            request_type = str(request_type or "").strip().lower()
            if request_type and request_type not in ordered:
                ordered.append(request_type)
        return ordered

    def _catalog_source_specs(self, content_type):
        request_types = self._catalog_request_types(content_type)
        specs = []
        seen = set()

        def add(
            request_type,
            category_parameter="category",
            extras=(),
            assume=False,
            compatibility=False,
            fast_probe=False,
            shared_catalog_scan=False,
        ):
            key = (
                request_type,
                category_parameter,
                tuple(extras),
                bool(assume),
            )
            if key in seen:
                return
            seen.add(key)
            specs.append(
                {
                    "request_type": request_type,
                    "action": "get_ordered_list",
                    "category_parameter": category_parameter,
                    "extra_parameters": tuple(extras),
                    "assume_series": bool(assume),
                    "compatibility": bool(compatibility),
                    "fast_probe": bool(fast_probe),
                    "shared_catalog_scan": bool(shared_catalog_scan),
                }
            )

        if content_type != "series":
            for request_type in request_types:
                add(request_type)
        else:
            preferred = self._vod_category_sources.get("series", "")
            if preferred == "vod":
                # A successful VOD category response is a strong standard-
                # portal signal.  Preserve that known route before probing
                # fork-specific series dialects; its page cursor still moves
                # progressively when the first page contains only movies.
                add("vod", shared_catalog_scan=True)
            # Probe the inexpensive dedicated series dialects first.  A slow
            # portal must not make the first UI worker walk a large shared VOD
            # catalogue before these targeted routes have had a chance.
            add("series", assume=True, fast_probe=True)
            add(
                "vod",
                extras=(("series", "1"),),
                assume=True,
                compatibility=True,
                fast_probe=True,
            )
            add(
                "vod",
                extras=(("is_series", "1"),),
                assume=True,
                compatibility=True,
                fast_probe=True,
            )
            add(
                "series",
                category_parameter="genre",
                assume=True,
                compatibility=True,
                fast_probe=True,
            )
            # If no dedicated route works, resume one shared VOD cursor in
            # small UI-scheduled batches.  It is deliberately last: shared
            # catalogues can contain thousands of movies before the first
            # series row appears.
            add("vod", shared_catalog_scan=True)
        return specs

    def _new_catalog_state(self, content_type, category_id):
        sources = []
        for spec in self._catalog_source_specs(content_type):
            source = dict(spec)
            source.update(
                {
                    "page": 1,
                    "tried_zero_page": False,
                    "probe_complete": False,
                    "done": False,
                    "raw_count": 0,
                    "page_count": 0,
                    "last_page": None,
                    "stop_reason": "",
                    "seen": set(),
                }
            )
            sources.append(source)
        return {
            "cached_at": self.clock(),
            "content_type": content_type,
            "category_id": category_id,
            "sources": sources,
            "active_source": 0,
            "selected_source": None,
            "results": [],
            "result_ids": set(),
            "last_error": None,
            "request_count": 0,
            "successful_request_count": 0,
            "page_count": 0,
            "last_page": None,
            "raw_count": 0,
            "matching_count": 0,
            "composite_usable_count": 0,
            "rejected_id_fields": set(),
            "stop_reason": "",
            "diagnostic_outcome": "pending",
            "diagnostic_logged": False,
            "complete": not sources,
        }

    def _catalog_state(self, content_type, category_id):
        key = (content_type, category_id)
        state = self._vod_catalog_states.get(key)
        if (
            state is None
            or self.clock() - state["cached_at"] >= CONTENT_CACHE_SECONDS
        ):
            state = self._new_catalog_state(content_type, category_id)
            self._vod_catalog_states.pop(key, None)
            self._vod_catalog_states[key] = state
            while len(self._vod_catalog_states) > VOD_CATALOG_STATE_LIMIT:
                self._vod_catalog_states.popitem(last=False)
        else:
            self._vod_catalog_states.move_to_end(key)
        return state

    @staticmethod
    def _catalog_source_available(state, index):
        sources = state.get("sources") or ()
        return bool(
            0 <= int(index) < len(sources)
            and not sources[int(index)].get("done")
        )

    def _next_catalog_source(self, state):
        selected = state.get("selected_source")
        if selected is not None and self._catalog_source_available(
            state, selected
        ):
            return int(selected)
        if selected is not None:
            # A source that already produced this content is authoritative.
            # Do not scan a second dialect after reaching its final page.
            state["complete"] = True
            return None
        sources = state.get("sources") or ()
        active = int(state.get("active_source", 0) or 0)
        if self._catalog_source_available(state, active):
            return active
        for index, source in enumerate(sources):
            if not source.get("done"):
                state["active_source"] = index
                return index
        state["complete"] = True
        return None

    def _advance_catalog_probe(self, state, current_index):
        if state.get("selected_source") is not None:
            return
        sources = state.get("sources") or ()
        # Finish all targeted one-page dialect probes before beginning the
        # potentially long shared VOD walk.
        for index, source in enumerate(sources):
            if (
                source.get("fast_probe")
                and not source.get("done")
                and not source.get("probe_complete")
            ):
                state["active_source"] = index
                return
        for index, source in enumerate(sources):
            if source.get("shared_catalog_scan") and not source.get("done"):
                state["active_source"] = index
                return
        for index, source in enumerate(sources):
            if index == current_index:
                continue
            if not source.get("done") and not source.get("probe_complete"):
                state["active_source"] = index
                return
        if self._catalog_source_available(state, current_index):
            state["active_source"] = current_index
            return
        for index, source in enumerate(sources):
            if not source.get("done"):
                state["active_source"] = index
                return

    def _catalog_page(self, state, source):
        category = state["category_id"] or "*"
        page = int(source["page"])
        parameters = [
            (source.get("category_parameter", "category"), category),
            ("fav", "0"),
            ("sortby", "name"),
            ("not_ended", "0"),
            ("hd", "0"),
            ("p", str(page)),
        ]
        parameters.extend(source.get("extra_parameters") or ())
        try:
            state["request_count"] += 1
            value = self._request(
                source["request_type"],
                source.get("action", "get_ordered_list"),
                tuple(parameters),
            )
        except ContentError as error:
            if error.reason in (
                ContentError.CANCELLED,
                ContentError.DEADLINE,
            ):
                raise
            state["last_error"] = error
            source["done"] = True
            source["probe_complete"] = True
            source["stop_reason"] = "request-error"
            return []
        batch = self._data_list(value)
        state["successful_request_count"] += 1
        state["page_count"] += 1
        state["last_page"] = page
        source["page_count"] += 1
        source["last_page"] = page
        if not batch and page == 1 and not source["tried_zero_page"]:
            source["page"] = 0
            source["tried_zero_page"] = True
            return []
        source["probe_complete"] = True
        if not batch:
            source["done"] = True
            source["stop_reason"] = "empty-page"
            return []

        unique = []
        for entry in batch:
            if not isinstance(entry, dict):
                continue
            allow_composite_series_id = bool(
                state["content_type"] == "series"
                and (
                    source["request_type"] == "series"
                    or self._is_series_entry(entry, False)
                )
            )
            identity = self._media_identity(
                entry,
                state["content_type"],
                allow_series_composite=allow_composite_series_id,
            )
            if not identity:
                identity = repr(sorted(entry.items()))
            if identity in source["seen"]:
                continue
            source["seen"].add(identity)
            unique.append(entry)
        if not unique:
            source["done"] = True
            source["stop_reason"] = "repeated-page"
            return []
        source["raw_count"] += len(unique)
        state["raw_count"] += len(unique)
        if page == 0:
            source["done"] = True
            source["stop_reason"] = "zero-page-complete"
        else:
            # Some Ministra forks report the current page size as their total.
            # Treat every total as advisory and confirm completion with the
            # following empty page.  A false total therefore cannot truncate
            # either the movie or the series catalogue.
            source["page"] = page + 1
            if source["page"] > MAX_PORTAL_PAGES:
                source["done"] = True
                source["stop_reason"] = "page-limit"
        return unique

    def _catalog_entry_matches(self, entry, content_type, source):
        if content_type == "series":
            return self._is_series_entry(
                entry,
                assume_series=bool(source.get("assume_series")),
            )
        return not self._is_series_entry(entry, False)

    def _load_catalog_batch(self, state, target, request_budget):
        added = []
        requests = 0
        while (
            not state["complete"]
            and len(added) < target
            and requests < request_budget
            and len(state["results"]) < MAX_RESULTS
        ):
            source_index = self._next_catalog_source(state)
            if source_index is None:
                break
            source = state["sources"][source_index]
            batch = self._catalog_page(state, source)
            requests += 1
            matches = 0
            for entry in batch:
                if not self._catalog_entry_matches(
                    entry,
                    state["content_type"],
                    source,
                ):
                    continue
                state["matching_count"] += 1
                explicit_series = bool(
                    state["content_type"] == "series"
                    and self._is_series_entry(entry, False)
                )
                item = self._media_item(
                    entry,
                    state["content_type"],
                    source["request_type"],
                    allow_composite_series_id=bool(
                        source["request_type"] == "series"
                        or explicit_series
                    ),
                )
                if item is None:
                    if state["content_type"] == "series":
                        for key in (
                            "series_id", "movie_id", "video_id", "id",
                            "stream_id", "cmd", "url",
                        ):
                            if entry.get(key) not in (None, "", [], {}):
                                state["rejected_id_fields"].add(key)
                    continue
                identity = (state["content_type"], str(item.stream_id))
                if identity in state["result_ids"]:
                    continue
                item.icon = _normalise_media_url(item.icon, self.account)
                state["result_ids"].add(identity)
                state["results"].append(item)
                if is_composite_id(item.stream_id):
                    state["composite_usable_count"] += 1
                evicted = self._lru_store(
                    self._vod_entry_cache,
                    identity,
                    dict(entry),
                    VOD_ENTRY_CACHE_LIMIT,
                )
                for evicted_identity in evicted:
                    self._vod_entry_sources.pop(evicted_identity, None)
                self._vod_entry_sources[identity] = source["request_type"]
                added.append(item)
                matches += 1
                if len(state["results"]) >= MAX_RESULTS:
                    break
            if matches:
                state["selected_source"] = source_index
                state["active_source"] = source_index
                if not state.get("stop_reason"):
                    state["stop_reason"] = "results-found"
            elif source.get("fast_probe") and source.get("probe_complete"):
                # A targeted route that returned rows but no usable series is
                # not deep-scanned.  The progressive shared VOD source is the
                # safe fallback for that work.
                source["done"] = True
                if not source.get("stop_reason"):
                    source["stop_reason"] = "no-usable-series"
                self._advance_catalog_probe(state, source_index)
            elif source["probe_complete"]:
                self._advance_catalog_probe(state, source_index)
            if source["done"] and state.get("selected_source") == source_index:
                state["complete"] = True
                state["stop_reason"] = (
                    source.get("stop_reason") or "selected-source-complete"
                )
            elif all(item.get("done") for item in state["sources"]):
                state["complete"] = True
                state["stop_reason"] = (
                    source.get("stop_reason") or "sources-exhausted"
                )
        if len(state["results"]) >= MAX_RESULTS:
            state["complete"] = True
            state["stop_reason"] = "result-limit"
        state["cached_at"] = self.clock()
        self._update_catalog_diagnostic(state)
        return added

    @staticmethod
    def _catalog_diagnostic_snapshot(state):
        outcome = str(state.get("diagnostic_outcome", "pending"))
        return {
            "outcome": outcome,
            "complete": bool(state.get("complete")),
            "requests": int(state.get("request_count", 0) or 0),
            "pages": int(state.get("page_count", 0) or 0),
            "last_page": state.get("last_page"),
            "raw": int(state.get("raw_count", 0) or 0),
            "matching": int(state.get("matching_count", 0) or 0),
            "usable": len(state.get("results") or ()),
            "id_classes": (
                "numeric-composite:{}".format(
                    int(state.get("composite_usable_count", 0) or 0)
                )
                if int(state.get("composite_usable_count", 0) or 0)
                else "-"
            ),
            "stop_reason": str(state.get("stop_reason", "") or "pending"),
            "id_fields": ",".join(sorted(
                state.get("rejected_id_fields") or ()
            )) or "-",
        }

    def _update_catalog_diagnostic(self, state):
        usable = len(state.get("results") or ())
        if usable:
            outcome = "available"
        elif not state.get("complete"):
            outcome = "pending"
        elif int(state.get("raw_count", 0) or 0) > 0:
            outcome = "unsupported"
        elif (
            state.get("last_error") is not None
            and not int(state.get("successful_request_count", 0) or 0)
        ):
            outcome = "error"
        else:
            outcome = "empty"
        state["diagnostic_outcome"] = outcome
        if (
            state.get("content_type") == "series"
            and outcome != "pending"
            and not state.get("diagnostic_logged")
        ):
            state["diagnostic_logged"] = True
            diagnostic = self._catalog_diagnostic_snapshot(state)
            try:
                log_event(
                    "portal",
                    (
                        "Series catalogue outcome={outcome} requests={requests} "
                        "pages={pages} last_page={last_page} raw={raw} "
                        "matching={matching} usable={usable} stop={stop_reason} "
                        "id_fields={id_fields} id_classes={id_classes}"
                    ).format(**diagnostic),
                )
            except Exception:
                pass

    def catalog_diagnostic(self, content_type, category_id=""):
        """Return aggregate catalogue status without provider/user data."""
        content_type = self._validate_type(content_type)
        category_id = str(category_id or "")
        with self._vod_catalog_lock:
            state = self._vod_catalog_states.get(
                (content_type, category_id)
            )
            if state is None:
                return {
                    "outcome": "pending",
                    "complete": False,
                    "requests": 0,
                    "pages": 0,
                    "last_page": None,
                    "raw": 0,
                    "matching": 0,
                    "usable": 0,
                    "stop_reason": "pending",
                }
            self._update_catalog_diagnostic(state)
            return self._catalog_diagnostic_snapshot(state)

    @staticmethod
    def _is_series_entry(entry, assume_series=False):
        if not isinstance(entry, dict):
            return False
        for key in ("is_series", "is_serial", "is_tv_series"):
            if key in entry:
                return StalkerPortalClient._portal_flag(entry.get(key))
        kind = str(
            entry.get("content_type")
            or entry.get("media_type")
            or entry.get("type")
            or ""
        ).strip().lower()
        if kind in ("series", "serial", "tv_series", "tvseries"):
            return True
        if kind in ("movie", "film", "vod", "video"):
            return False
        series = entry.get("series")
        if isinstance(series, (list, tuple, set, dict)):
            return bool(series)
        series = str(series or "").strip().lower()
        if series and series not in (
            "0", "false", "none", "null", "[]", "{}", "a:0:{}",
        ):
            return True
        if "series" in entry:
            return False
        seasons = entry.get("seasons") or entry.get("season_count")
        if isinstance(seasons, (list, tuple, set, dict)):
            return bool(seasons)
        try:
            if int(seasons or 0) > 0:
                return True
        except (TypeError, ValueError, OverflowError):
            pass
        if "seasons" in entry or "season_count" in entry:
            return False
        return bool(assume_series)

    @staticmethod
    def _command_extension(command):
        value = str(command or "").split("|", 1)[0].split("#", 1)[0]
        value = value.split("?", 1)[0].rstrip("/")
        match = re.search(r"\.([A-Za-z0-9_]{1,12})$", value)
        return match.group(1) if match else ""

    def _media_item(
        self,
        entry,
        content_type,
        request_type="vod",
        allow_composite_series_id=False,
    ):
        if not isinstance(entry, dict):
            return None
        stream_id = StalkerPortalClient._media_identifier(
            entry,
            content_type,
            allow_series_composite=allow_composite_series_id,
        )
        if not stream_id:
            return None
        command = StalkerPortalClient._channel_command(entry)
        if content_type == "movie" and not command:
            command = "/media/{}.mpg".format(stream_id)
        category_ids = StalkerPortalClient._category_ids(entry)
        icon = self._first_media_url(
            entry,
            "screenshot_uri",
            "movie_image",
            "cover_big",
            "cover",
            "poster",
            "stream_icon",
            "logo",
        )
        item = ContentItem(
            content_type,
            stream_id,
            entry.get("name") or entry.get("title") or entry.get("o_name"),
            category_id=category_ids[0] if category_ids else "",
            extension=(
                entry.get("container_extension")
                or StalkerPortalClient._command_extension(command)
            ),
            icon=icon,
            rating=(
                entry.get("rating")
                or entry.get("rating_imdb")
                or entry.get("rating_kinopoisk")
            ),
            year=entry.get("year") or entry.get("release_year"),
            duration=entry.get("duration") or entry.get("time"),
            plot=(
                entry.get("plot")
                or entry.get("description")
                or entry.get("overview")
            ),
            # Generic ``tmdb`` values on several Ministra forks are internal
            # catalogue numbers, not TMDb identities.  Preserve only fields
            # whose name explicitly identifies a TMDb ID; metadata.py still
            # verifies that ID against the selected title and year.
            tmdb_id=(
                entry.get("tmdb_id")
                or entry.get("tmdbId")
                or entry.get("themoviedb_id")
            ),
        )
        # Preserve wide catalogue artwork independently from the portrait
        # screenshot.  The detail endpoint is optional on several MAG forks,
        # so the cinematic browser must still be able to use this candidate.
        item.backdrop = self._first_media_url(
            entry,
            "backdrop_path", "backdrop", "backdrop_url",
            "backdrop_image", "background", "background_url",
            "background_image", "image_background", "fanart",
            "fanart_url", "screenshots", "screenshot", "screenshot_url",
            "cover_landscape", "landscape", "landscape_url", "hero",
            "hero_image", "banner", "banner_url",
        )
        item.portal_command = command
        item.portal_category_ids = category_ids
        item.portal_request_type = request_type
        item.portal_series = "0"
        # VOD catalogues may expose an absolute command which is only an
        # intermediate or short-lived portal link.  Preserve the same link
        # policy flags that live channel records already carry so playback
        # never mistakes such a command for a reusable static media URL.
        item.portal_use_tmp_link = StalkerPortalClient._portal_flag(
            entry.get("use_http_tmp_link")
        )
        item.portal_use_load_balancing = StalkerPortalClient._portal_flag(
            entry.get("use_load_balancing")
            or entry.get("enable_wowza_load_balancing")
        )
        item.portal_disable_ad = StalkerPortalClient._portal_flag(
            entry.get("disable_ad")
        )
        return item

    def _load_vod_items(self, content_type, category_id):
        with self._vod_catalog_lock:
            state = self._catalog_state(content_type, category_id)
            if not state["results"] and not state["complete"]:
                self._load_catalog_batch(
                    state,
                    PORTAL_VOD_INITIAL_TARGET,
                    (
                        PORTAL_SERIES_INITIAL_REQUEST_BUDGET
                        if content_type == "series"
                        else PORTAL_VOD_EMPTY_REQUEST_BUDGET
                    ),
                )
            if (
                not state["results"]
                and state["complete"]
                and state.get("last_error") is not None
                and not int(state.get("successful_request_count", 0) or 0)
            ):
                raise state["last_error"]
            return list(state["results"])

    def load_more_items(self, content_type, category_id=""):
        content_type = self._validate_type(content_type)
        category_id = str(category_id or "")
        if content_type == "live":
            with self._live_catalog_lock:
                state = self._live_catalog_state(category_id)
                if state["complete"]:
                    return []
                return self._load_live_batch(
                    state,
                    LIVE_MORE_ITEMS,
                    PORTAL_LIVE_MORE_REQUEST_BUDGET,
                )
        with self._vod_catalog_lock:
            state = self._catalog_state(content_type, category_id)
            if state["complete"]:
                return []
            return self._load_catalog_batch(
                state,
                PORTAL_VOD_MORE_TARGET,
                (
                    PORTAL_SERIES_MORE_REQUEST_BUDGET
                    if content_type == "series"
                    else PORTAL_VOD_REQUEST_BUDGET
                ),
            )

    def has_more_items(self, content_type, category_id=""):
        content_type = self._validate_type(content_type)
        category_id = str(category_id or "")
        if content_type == "live":
            with self._live_catalog_lock:
                state = self._live_catalog_states.get(category_id)
                if state is None:
                    return False
                if (
                    self.clock() - state["cached_at"]
                    >= CONTENT_CACHE_SECONDS
                ):
                    self._live_catalog_states.pop(category_id, None)
                    return False
                return not state.get("complete", True)
        with self._vod_catalog_lock:
            state = self._vod_catalog_states.get((content_type, category_id))
            return bool(state is not None and not state.get("complete"))

    def _new_live_catalog_state(self, category_id):
        return {
            "cached_at": self.clock(),
            "category_id": category_id,
            "page": 1,
            "tried_zero_page": False,
            "ordered_done": False,
            "ordered_raw_count": 0,
            "raw_seen": set(),
            "pending": deque(),
            "fallback_loaded": False,
            "results": [],
            "result_ids": set(),
            "last_error": None,
            "complete": False,
        }

    def _live_catalog_state(self, category_id):
        state = self._live_catalog_states.get(category_id)
        if (
            state is None
            or self.clock() - state["cached_at"] >= CONTENT_CACHE_SECONDS
        ):
            state = self._new_live_catalog_state(category_id)
            self._live_catalog_states.pop(category_id, None)
            self._live_catalog_states[category_id] = state
            while len(self._live_catalog_states) > 3:
                self._live_catalog_states.popitem(last=False)
        else:
            self._live_catalog_states.move_to_end(category_id)
        return state

    def _live_order_parameters(self, genre, page):
        parameters = (
            ("genre", genre),
            ("p", str(page)),
            ("fav", "0"),
            ("sortby", "number"),
            ("hd", "0"),
        )
        if self._force_ch_link_check:
            return parameters + (("force_ch_link_check", "1"),)
        return parameters

    def _ordered_live_page(self, state):
        page = int(state["page"])
        genre = state["category_id"] or "*"
        try:
            value = self._request(
                "itv",
                "get_ordered_list",
                self._live_order_parameters(genre, page),
            )
        except ContentError as error:
            if error.reason in REQUEST_ABORT_REASONS:
                raise
            state["last_error"] = error
            state["ordered_done"] = True
            return

        batch = self._data_list(value)
        if not batch and page == 1 and not state["tried_zero_page"]:
            state["page"] = 0
            state["tried_zero_page"] = True
            return
        if not batch:
            state["ordered_done"] = True
            return

        unique = []
        for entry in batch:
            if not isinstance(entry, dict):
                continue
            identity = self._media_identity(entry)
            if not identity:
                identity = repr(sorted(entry.items()))
            if identity in state["raw_seen"]:
                continue
            state["raw_seen"].add(identity)
            unique.append(entry)
        if not unique:
            state["ordered_done"] = True
            return

        category_id = state["category_id"]
        if category_id:
            mapped = []
            for entry in unique:
                category_ids = self._category_ids(
                    entry,
                    include_aliases=False,
                )
                if category_ids:
                    mapped.append(category_ids)
            if mapped and not any(category_id in values for values in mapped):
                # Some forks silently ignore the genre parameter. Stop after
                # the first clearly unfiltered page and use the shared channel
                # list instead of walking the entire catalogue.
                state["ordered_done"] = True
                return

        state["ordered_raw_count"] += len(unique)
        state["pending"].extend((entry, True) for entry in unique)
        total = self._total_items(value)
        if page == 0 or (
            total is not None and state["ordered_raw_count"] >= total
        ):
            state["ordered_done"] = True
        else:
            state["page"] = page + 1
            if state["page"] > MAX_PORTAL_PAGES:
                state["ordered_done"] = True

    def _fallback_live_channels(self, state):
        if (
            self._channel_cache is not None
            and self.clock() - self._channel_cached_at < CONTENT_CACHE_SECONDS
        ):
            entries = list(self._channel_cache)
            state["last_error"] = None
        else:
            try:
                value = self._request("itv", "get_all_channels")
            except ContentError as error:
                if error.reason in REQUEST_ABORT_REASONS:
                    raise
                state["last_error"] = error
                entries = []
            else:
                entries = self._data_list(value)[:MAX_RESULTS]
                state["last_error"] = None
                self._channel_cache = list(entries)
                self._channel_cached_at = self.clock()
        state["fallback_loaded"] = True
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            identity = self._media_identity(entry)
            if not identity:
                identity = repr(sorted(entry.items()))
            if identity in state["raw_seen"]:
                continue
            state["raw_seen"].add(identity)
            state["pending"].append((entry, False))

    def _consume_live_pending(self, state, target, added):
        category_id = state["category_id"]
        while (
            state["pending"]
            and len(added) < target
            and len(state["results"]) < MAX_RESULTS
        ):
            entry, server_filtered = state["pending"].popleft()
            category_ids = self._category_ids(entry)
            if category_id:
                if server_filtered:
                    strict_category_ids = self._category_ids(
                        entry,
                        include_aliases=False,
                    )
                    if (
                        strict_category_ids
                        and category_id not in strict_category_ids
                    ):
                        continue
                elif category_id not in category_ids:
                    continue
            item = self._channel_item(entry)
            if item is None or item.stream_id in state["result_ids"]:
                continue
            item.icon = _normalise_media_url(item.icon, self.account)
            item.catalog_category_id = category_id
            state["result_ids"].add(item.stream_id)
            state["results"].append(item)
            added.append(item)

    def _load_live_batch(self, state, target, request_budget):
        added = []
        requests = 0
        while (
            not state["complete"]
            and len(added) < target
            and len(state["results"]) < MAX_RESULTS
        ):
            self._consume_live_pending(state, target, added)
            if len(added) >= target or len(state["results"]) >= MAX_RESULTS:
                break
            if state["pending"]:
                continue
            if state["fallback_loaded"]:
                state["complete"] = True
                break
            if state["ordered_done"]:
                if state["results"]:
                    state["complete"] = True
                    break
                if requests >= request_budget:
                    break
                self._fallback_live_channels(state)
                requests += 1
                continue
            if requests >= request_budget:
                break
            self._ordered_live_page(state)
            requests += 1

        if len(state["results"]) >= MAX_RESULTS:
            state["complete"] = True
            state["pending"].clear()
        elif state["fallback_loaded"] and not state["pending"]:
            state["complete"] = True
        elif state["ordered_done"] and state["results"] and not state["pending"]:
            state["complete"] = True
        state["cached_at"] = self.clock()
        return added

    def _load_live_items(self, category_id):
        with self._live_catalog_lock:
            state = self._live_catalog_state(category_id)
            if not state["results"] and not state["complete"]:
                self._load_live_batch(
                    state,
                    LIVE_INITIAL_ITEMS,
                    PORTAL_LIVE_INITIAL_REQUEST_BUDGET,
                )
            if (
                not state["results"]
                and state["complete"]
                and state.get("last_error") is not None
            ):
                raise state["last_error"]
            return list(state["results"])

    def _load_all_channels(self):
        if (
            self._channel_cache is not None
            and self.clock() - self._channel_cached_at < CONTENT_CACHE_SECONDS
        ):
            return list(self._channel_cache)
        entries = []
        try:
            value = self._request("itv", "get_all_channels")
            entries = self._data_list(value)
        except ContentError as error:
            if error.reason in REQUEST_ABORT_REASONS:
                raise
            entries = []
        if not entries:
            entries = self._load_ordered_channels("")
        self._channel_cache = list(entries[:MAX_RESULTS])
        self._channel_cached_at = self.clock()
        return list(self._channel_cache)

    def _load_ordered_channels(self, category_id):
        genre = str(category_id or "").strip() or "*"
        entries = []
        seen = set()
        page = 1
        tried_zero_page = False
        while page <= MAX_PORTAL_PAGES and len(entries) < MAX_RESULTS:
            try:
                value = self._request(
                    "itv",
                    "get_ordered_list",
                    self._live_order_parameters(genre, page),
                )
            except ContentError as error:
                if error.reason in REQUEST_ABORT_REASONS:
                    raise
                if entries:
                    break
                raise
            batch = self._data_list(value)
            if not batch and page == 1 and not tried_zero_page:
                # Most Ministra portals are one-based. A smaller group of
                # older forks expects p=0, so try it only when p=1 is empty.
                page = 0
                tried_zero_page = True
                continue
            if not batch:
                break
            added = 0
            for entry in batch:
                if not isinstance(entry, dict):
                    continue
                identity = str(
                    entry.get("id")
                    or entry.get("ch_id")
                    or entry.get("channel_id")
                    or entry.get("stream_id")
                    or entry.get("cmd")
                    or entry.get("url")
                    or ""
                ).strip()
                if not identity:
                    identity = repr(sorted(entry.items()))
                if identity in seen:
                    continue
                seen.add(identity)
                entries.append(entry)
                added += 1
                if len(entries) >= MAX_RESULTS:
                    break
            if added == 0:
                break
            total = self._total_items(value)
            if total is not None and len(entries) >= total:
                break
            if page == 0:
                # p=1 was already tried and returned empty.
                break
            page += 1
        return entries

    @staticmethod
    def _category_aliases(entry):
        """Return bounded category aliases carried by a category row."""

        aliases = []
        seen = set()
        for name in ("category_alias", "alias"):
            raw = entry.get(name)
            if raw in (None, "", [], {}):
                continue
            values = StalkerPortalClient._category_ids(
                {"category_alias": raw}
            )
            for value in values:
                if value in seen:
                    continue
                seen.add(value)
                aliases.append(value)
                if len(aliases) >= 32:
                    return tuple(aliases)
        return tuple(aliases)

    @staticmethod
    def _category_ids(entry, include_aliases=True):
        """Collect every category identity exposed by a portal row."""

        strict_field_modes = (
            ("tv_genre_id", True),
            ("genre_id", True),
            ("category_id", True),
            ("cat_genre_id", True),
            ("category_ids", True),
            ("genre_ids", True),
        )
        alias_field_modes = (
            # These fields can be human-readable aliases.  Preserve spaces so
            # "Science Fiction" is not mistaken for two IDs.
            ("category_alias", False),
            ("category", False),
            ("genres", False),
            ("genre", False),
        )
        field_modes = strict_field_modes
        if include_aliases:
            field_modes += alias_field_modes
        mode_by_name = dict(field_modes)
        mode_by_name["id"] = True
        pending = deque(
            (entry.get(name), split_whitespace, True)
            for name, split_whitespace in field_modes
        )
        container_types = (dict, list, tuple, set, frozenset)
        if not include_aliases:
            # Alias fields are not authoritative as scalars, but containers
            # beneath them can still expose a real nested ``id``.
            pending.extend(
                (value, False, False)
                for value in (
                    entry.get(name) for name, unused in alias_field_modes
                )
                if isinstance(value, container_types)
            )
        seen_containers = set()
        result = []
        seen = set()
        processed = 0
        max_nodes = 1024
        object_field_names = frozenset(
            (
                "name",
                "title",
                "label",
                "value",
                "alias",
                "category_alias",
                "category_name",
                "genre_name",
            )
        )

        def queue_values(values, split_whitespace, allow_scalar=True):
            for value in values:
                if processed + len(pending) >= max_nodes:
                    break
                pending.append((value, split_whitespace, allow_scalar))

        while pending and len(result) < 256 and processed < max_nodes:
            raw, split_whitespace, allow_scalar = pending.popleft()
            processed += 1
            if isinstance(raw, dict):
                marker = id(raw)
                if marker in seen_containers:
                    continue
                seen_containers.add(marker)
                nested = [
                    (raw.get(name), nested_split)
                    for name, nested_split in mode_by_name.items()
                    if raw.get(name) not in (None, "", [], {})
                ]
                if nested:
                    for value, nested_split in nested:
                        queue_values((value,), nested_split, True)
                else:
                    keys = tuple(raw.keys())
                    has_object_fields = any(
                        str(key or "").strip().lower()
                        in object_field_names
                        for key in keys
                    )
                    is_identity_map = (
                        bool(keys)
                        and not has_object_fields
                        and all(
                            not isinstance(value, container_types)
                            for value in raw.values()
                        )
                    )
                    if is_identity_map:
                        # Some portals return an ID-to-title mapping here.
                        queue_values(keys, True, True)
                    else:
                        # Search nested objects without treating descriptive
                        # object fields such as ``name`` as category IDs.
                        nested_values = raw.values()
                        if has_object_fields:
                            nested_values = (
                                value
                                for value in nested_values
                                if isinstance(value, container_types)
                            )
                        queue_values(nested_values, False, False)
                continue
            if isinstance(raw, (list, tuple, set, frozenset)):
                marker = id(raw)
                if marker in seen_containers:
                    continue
                seen_containers.add(marker)
                queue_values(raw, split_whitespace, allow_scalar)
                continue
            if raw is None:
                continue
            exact = str(raw).strip().strip("[](){}").strip().strip("\"'")
            if not exact:
                continue
            if not allow_scalar:
                numeric_values = re.split(r"[,;|\s]+", exact)
                if not numeric_values or not all(
                    value.isdigit() for value in numeric_values
                ):
                    continue
                split_whitespace = True
            if split_whitespace:
                values = re.split(r"[,;|\s]+", exact)
            else:
                values = []
                for value in re.split(r"[,;|]+", exact):
                    numeric_tokens = value.split()
                    if (
                        len(numeric_tokens) > 1
                        and all(token.isdigit() for token in numeric_tokens)
                    ):
                        values.extend(numeric_tokens)
                    else:
                        values.append(value)
            for value in values:
                value = str(value or "").strip().strip("\"'")
                if not value or value in seen:
                    continue
                seen.add(value)
                result.append(value)
                if len(result) >= 256:
                    break
        return tuple(result)

    @staticmethod
    def _channel_command(entry):
        candidates = (
            entry.get("cmd"),
            entry.get("url"),
            entry.get("mc_cmd"),
            entry.get("commands"),
            entry.get("cmds"),
        )
        for candidate in candidates:
            if isinstance(candidate, dict):
                candidate = candidate.get("cmd") or candidate.get("url")
            elif isinstance(candidate, (list, tuple)):
                candidate = candidate[0] if candidate else ""
                if isinstance(candidate, dict):
                    candidate = candidate.get("cmd") or candidate.get("url")
            candidate = str(candidate or "").strip()
            if candidate:
                return candidate
        return ""

    @staticmethod
    def _portal_flag(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() not in (
            "", "0", "false", "no", "off", "none", "null", "undefined",
        )

    @staticmethod
    def _profile_forces_create_link(value, depth=0):
        if depth > 3 or not isinstance(value, dict):
            return False
        if "force_ch_link_check" in value:
            flag = value.get("force_ch_link_check")
            if flag is True:
                return True
            if isinstance(flag, (int, float)):
                return flag == 1
            return str(flag or "").strip().lower() in (
                "1", "true", "yes", "on",
            )
        for key in ("data", "profile", "result"):
            nested = value.get(key)
            if isinstance(nested, dict):
                return StalkerPortalClient._profile_forces_create_link(
                    nested,
                    depth + 1,
                )
        return False

    @staticmethod
    def _profile_connection_integer(value):
        """Accept only bounded, non-negative JSON-style integer counters."""
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            number = value
        elif isinstance(value, str):
            text = value.strip()
            if (
                not text
                or len(text) > 10
                or any(character < "0" or character > "9" for character in text)
            ):
                return None
            number = int(text)
        else:
            return None
        if number < 0 or number > 2147483647:
            return None
        return number

    @staticmethod
    def _profile_connection_alias(value, keys):
        numbers = []
        found = False
        for key in keys:
            if key not in value:
                continue
            found = True
            number = StalkerPortalClient._profile_connection_integer(
                value.get(key)
            )
            if number is None:
                return None, True
            if number not in numbers:
                numbers.append(number)
        if not found or len(numbers) != 1:
            return None, found
        return numbers[0], True

    @staticmethod
    def _profile_connection_counts(value, depth=0):
        """Read one explicit active/maximum pair from known profile wrappers."""
        active, maximum, unused_claimed = (
            StalkerPortalClient._profile_connection_count_state(value, depth)
        )
        return active, maximum

    @staticmethod
    def _profile_connection_count_state(value, depth=0):
        if not isinstance(value, dict):
            return None, None, False
        connection_keys = (
            PROFILE_ACTIVE_CONNECTION_KEYS + PROFILE_MAX_CONNECTION_KEYS
        )
        if depth > 3:
            return None, None, any(key in value for key in connection_keys)
        candidate = None
        claimed = False
        if any(key in value for key in connection_keys):
            claimed = True
            active, active_found = StalkerPortalClient._profile_connection_alias(
                value,
                PROFILE_ACTIVE_CONNECTION_KEYS,
            )
            maximum, maximum_found = StalkerPortalClient._profile_connection_alias(
                value,
                PROFILE_MAX_CONNECTION_KEYS,
            )
            if (
                not active_found
                or not maximum_found
                or active is None
                or maximum is None
                or maximum <= 0
            ):
                return None, None, True
            candidate = (active, maximum)
        for key in PROFILE_CONNECTION_WRAPPERS:
            nested = value.get(key)
            if isinstance(nested, dict):
                active, maximum, nested_claimed = (
                    StalkerPortalClient._profile_connection_count_state(
                        nested,
                        depth + 1,
                    )
                )
                if not nested_claimed:
                    continue
                claimed = True
                if active is None or maximum is None:
                    return None, None, True
                counts = (active, maximum)
                if candidate is not None and counts != candidate:
                    return None, None, True
                candidate = counts
        if candidate is not None:
            return candidate[0], candidate[1], True
        return None, None, claimed

    @staticmethod
    def _channel_epg_identities(entry, stream_id):
        result = []
        seen = set()
        for kind, value in (
            ("id", entry.get("id")),
            ("ch_id", entry.get("ch_id")),
            ("channel_id", entry.get("channel_id")),
            ("stream_id", stream_id),
            ("epg_id", entry.get("epg_id")),
            ("xmltv_id", entry.get("xmltv_id")),
        ):
            value = str(value or "").strip()
            if not value or value in seen or len(value) > 256:
                continue
            seen.add(value)
            result.append((kind, value))
        return tuple(result)

    @staticmethod
    def _embedded_channel_epg(entry):
        candidates = []
        for key in (
            "epg", "epg_data", "events", "programs", "programme", "guide",
        ):
            value = entry.get(key)
            if isinstance(value, (dict, list, tuple)):
                candidates.append(value)
        now = entry.get("now") or entry.get("current_program")
        following = entry.get("next") or entry.get("next_program")
        rows = [
            value for value in (now, following) if isinstance(value, dict)
        ]
        if rows:
            candidates.append(rows)
        return tuple(candidates)

    def _channel_item(self, entry):
        if not isinstance(entry, dict):
            return None
        stream_id = str(
            entry.get("id")
            or entry.get("ch_id")
            or entry.get("channel_id")
            or entry.get("stream_id")
            or ""
        ).strip()
        command = StalkerPortalClient._channel_command(entry)
        if not stream_id or not command:
            return None
        category_ids = StalkerPortalClient._category_ids(entry)
        item = ContentItem(
            "live",
            stream_id,
            entry.get("name") or entry.get("title"),
            category_id=category_ids[0] if category_ids else "",
            icon=(
                entry.get("logo")
                or entry.get("stream_icon")
                or entry.get("logo_url")
                or entry.get("icon")
                or entry.get("picon")
                or entry.get("image")
                or ""
            ),
        )
        identities = self._channel_epg_identities(entry, stream_id)
        item.portal_channel_id = str(entry.get("ch_id") or "").strip()
        item.portal_epg_id = str(entry.get("epg_id") or "").strip()
        item.portal_xmltv_id = str(entry.get("xmltv_id") or "").strip()
        item.portal_epg_identities = identities
        item.portal_command = command
        item.portal_category_ids = category_ids
        item.portal_use_tmp_link = StalkerPortalClient._portal_flag(
            entry.get("use_http_tmp_link")
        )
        item.portal_use_load_balancing = StalkerPortalClient._portal_flag(
            entry.get("use_load_balancing")
            or entry.get("enable_wowza_load_balancing")
        )
        item.portal_disable_ad = StalkerPortalClient._portal_flag(
            entry.get("disable_ad")
        )
        self._lru_store(
            self._live_channel_meta,
            stream_id,
            {
                "identities": identities,
                "embedded": self._embedded_channel_epg(entry),
            },
            LIVE_CHANNEL_META_LIMIT,
        )
        return item

    def load_items(self, content_type, category_id=""):
        content_type = self._validate_type(content_type)
        category_id = str(category_id or "")
        if content_type != "live":
            return self._load_vod_items(content_type, category_id)
        return self._load_live_items(category_id)

    def reset_vod_catalog_search(self):
        """Discard only movie-search state for an explicit GREEN refresh."""

        with self._vod_catalog_critical():
            for key in list(self._vod_catalog_states):
                if key[0] == "movie":
                    self._vod_catalog_states.pop(key, None)
            for key in list(self._vod_entry_cache):
                if key[0] == "movie":
                    self._vod_entry_cache.pop(key, None)
                    self._vod_entry_sources.pop(key, None)

    def load_vod_catalog_step(
        self,
        category_id="",
        cursor=0,
        limit=PORTAL_CATALOG_STEP_ITEMS,
    ):
        """Return at most one request worth of movie-search progress.

        The normal movie browser remains page-on-demand.  Only the explicit
        all-movies search uses this cursor API, allowing the worker to publish
        each useful portal page instead of waiting for the entire catalogue.
        """

        category_id = str(category_id or "")
        try:
            cursor = max(0, int(cursor or 0))
        except (TypeError, ValueError, OverflowError):
            cursor = 0
        try:
            limit = max(1, min(
                PORTAL_CATALOG_STEP_ITEMS,
                int(limit or PORTAL_CATALOG_STEP_ITEMS),
            ))
        except (TypeError, ValueError, OverflowError):
            limit = PORTAL_CATALOG_STEP_ITEMS

        with self._vod_catalog_critical():
            state = self._catalog_state("movie", category_id)
            results = state["results"]
            cursor = min(cursor, len(results))
            before_requests = int(state.get("request_count", 0) or 0)
            before_count = len(results)
            if cursor >= len(results) and not state["complete"]:
                self._load_catalog_batch(state, limit, 1)
                results = state["results"]

            next_cursor = min(len(results), cursor + limit)
            items = list(results[cursor:next_cursor])
            terminal = bool(
                state.get("complete") and next_cursor >= len(results)
            )
            reason = str(state.get("stop_reason", "") or "")
            trusted = bool(
                terminal
                and reason in (
                    "empty-page",
                    "portal-total",
                    "zero-page-complete",
                )
            )
            terminal_error = state.get("last_error")
            if (
                terminal
                and not trusted
                and isinstance(terminal_error, ContentError)
                and terminal_error.reason in (
                    ContentError.ACCESS_DENIED,
                    ContentError.RATE_LIMITED,
                    ContentError.CONNECTION,
                    ContentError.HTTP_ERROR,
                    ContentError.RESPONSE_TOO_LARGE,
                    ContentError.INVALID_RESPONSE,
                )
            ):
                raise terminal_error
            progressed = bool(
                items
                or terminal
                or len(results) != before_count
                or int(state.get("request_count", 0) or 0) > before_requests
            )
            return PortalCatalogStep(
                items,
                cursor=next_cursor,
                complete=terminal,
                trusted_complete=trusted,
                reason=reason,
                progressed=progressed,
            )

    def load_vod_catalog_batch(self, category_id="", limit=MAX_RESULTS):
        """Return one complete, bounded movie batch for global search.

        Normal browsing remains incremental.  Only the explicit all-movies
        search asks for this method, so it may continue the saved portal
        cursor until the category is complete or the caller's deadline fires.
        A short cancellable pause between batches protects slower portals from
        a burst of back-to-back page requests.
        """
        category_id = str(category_id or "")
        try:
            limit = max(1, min(MAX_RESULTS, int(limit)))
        except (TypeError, ValueError, OverflowError):
            limit = MAX_RESULTS
        results = list(self.load_items("movie", category_id) or [])
        no_progress = 0
        while len(results) < limit and self.has_more_items("movie", category_id):
            self._check_request_limits()
            previous_count = len(results)
            self.load_more_items("movie", category_id)
            results = list(self.load_items("movie", category_id) or [])
            if len(results) <= previous_count:
                no_progress += 1
                if no_progress >= 3:
                    raise ContentError(
                        N_("Portal catalogue did not make progress"),
                        ContentError.CONTENT_RESPONSE,
                    )
            else:
                no_progress = 0
            cancel_event, deadline, unused_timeout = self._request_limits()
            delay = PORTAL_CATALOG_PACE_SECONDS
            if deadline is not None:
                delay = min(delay, max(0.0, float(deadline) - time.monotonic()))
            if delay <= 0:
                self._check_request_limits()
            elif cancel_event is not None:
                cancel_event.wait(delay)
            else:
                time.sleep(delay)
        self._check_request_limits()
        return results[:limit]

    @staticmethod
    def _first_entry_value(entry, *keys):
        if not isinstance(entry, dict):
            return ""
        for key in keys:
            value = entry.get(key)
            if value not in (None, "", []):
                return value
        return ""

    @staticmethod
    def _merge_detail_metadata(base, value):
        """Merge bounded movie/series detail wrappers over a catalogue row."""
        result = dict(base or {})
        fields = (
            "name", "title", "o_name", "plot", "description", "overview",
            "rating", "rating_imdb", "rating_kinopoisk", "rating_5based",
            "year", "release_year", "releasedate", "releaseDate",
            "duration", "duration_secs", "time", "genres_str", "genre",
            "genres", "director", "actors", "cast", "tmdb_id", "tmdbId",
            "themoviedb_id", "tmdb",
            "screenshot_uri", "movie_image", "cover_big", "cover",
            "poster", "stream_icon", "backdrop_path", "backdrop",
            "backdrop_url", "backdrop_image", "background",
            "background_url", "background_image", "image_background",
            "fanart", "fanart_url", "screenshots", "screenshot",
            "screenshot_url", "cover_landscape", "landscape",
            "landscape_url", "hero", "hero_image", "banner",
            "banner_url",
        )
        wrappers = (
            "movie", "series", "info", "movie_data", "data", "result",
            "item",
        )
        visited = set()

        def merge(candidate, depth=0):
            if not isinstance(candidate, dict) or depth > 3:
                return
            marker = id(candidate)
            if marker in visited:
                return
            visited.add(marker)
            for key in fields:
                current = candidate.get(key)
                if current not in (None, "", [], {}):
                    result[key] = current
            for key in wrappers:
                merge(candidate.get(key), depth + 1)

        merge(value)
        return result

    def _first_media_url(self, entry, *keys):
        """Return the first valid image candidate, not merely the first value."""
        if not isinstance(entry, dict):
            return ""

        def candidates(value):
            if isinstance(value, (list, tuple)):
                for nested in value:
                    for candidate in candidates(nested):
                        yield candidate
                return
            if isinstance(value, dict):
                for nested_key in ("url", "path", "src", "image"):
                    if nested_key in value:
                        for candidate in candidates(value.get(nested_key)):
                            yield candidate
                return
            yield value

        for key in keys:
            for candidate in candidates(entry.get(key)):
                raw = str(candidate or "").strip().lower()
                if raw in (
                    "", "-", "0", "n/a", "na", "none", "null",
                    "undefined", "about:blank",
                ):
                    continue
                value = _normalise_media_url(candidate, self.account)
                if not value:
                    continue
                try:
                    return validate_public_url_syntax(value)["url"]
                except NetworkSecurityError:
                    continue
        return ""

    def _cached_media_entry(self, content_type, stream_id):
        if content_type == "series":
            stream_id = safe_series_id(stream_id)
            if not stream_id:
                return {}
        else:
            stream_id = str(stream_id or "").strip()
        cache_key = (content_type, stream_id)
        entry = self._vod_entry_cache.get(cache_key)
        if entry is not None:
            self._vod_entry_cache.move_to_end(cache_key)
            return dict(entry)
        try:
            self.load_items(content_type, "")
        except ContentError as error:
            self._raise_terminal_series_error(error)
            return {}
        entry = self._vod_entry_cache.get(cache_key)
        if entry is not None:
            self._vod_entry_cache.move_to_end(cache_key)
        else:
            state = self._vod_catalog_states.get((content_type, ""))
            error = state.get("last_error") if state else None
            if isinstance(error, ContentError):
                self._raise_terminal_series_error(error)
        return dict(entry or {})

    def load_vod_info(self, stream_id):
        stream_id = str(stream_id or "").strip()
        if not stream_id.isdigit():
            raise ContentError(
                N_("Portal request failed"),
                ContentError.CONTENT_RESPONSE,
            )
        entry = self._cached_media_entry("movie", stream_id)
        # The catalogue usually carries only a portrait screenshot. Query the
        # selected title's optional detail record once; several Ministra/MAG
        # forks expose the real backdrop only from get_vod_info.
        if self._endpoint and self._token:
            try:
                value = self._request(
                    "vod",
                    "get_vod_info",
                    (("movie_id", stream_id),),
                )
            except ContentError as error:
                self._raise_terminal_series_error(error)
            else:
                entry = self._merge_detail_metadata(entry, value)
        first = lambda *keys: self._first_entry_value(entry, *keys)
        return MovieInfo(
            title=first("name", "title", "o_name"),
            plot=first("plot", "description", "overview"),
            rating=first(
                "rating", "rating_imdb", "rating_kinopoisk", "rating_5based"
            ),
            year=first("year", "release_year", "releasedate", "releaseDate"),
            duration=first("duration", "time", "duration_secs"),
            genre=first("genres_str", "genre", "genres"),
            director=first("director"),
            cast=first("actors", "cast"),
            cover=self._first_media_url(
                entry,
                "screenshot_uri", "movie_image", "cover_big", "cover",
                "poster", "stream_icon",
            ),
            backdrop=self._first_media_url(
                entry,
                "backdrop_path", "backdrop", "backdrop_url",
                "backdrop_image", "background", "background_url",
                "background_image", "image_background", "fanart",
                "fanart_url", "screenshots", "screenshot", "screenshot_url",
                "cover_landscape", "landscape", "landscape_url", "hero",
                "hero_image", "banner", "banner_url",
            ),
            tmdb_id=first("tmdb_id", "tmdbId", "themoviedb_id"),
        )

    @staticmethod
    def _season_number(entry, fallback=""):
        value = StalkerPortalClient._first_entry_value(
            entry, "season_number", "season", "season_num", "number"
        )
        value = str(value or fallback).strip()
        return value or str(fallback or "1")

    @staticmethod
    def _episode_number(entry, fallback=""):
        value = StalkerPortalClient._first_entry_value(
            entry,
            "series_number",
            "episode_num",
            "episode_number",
            "episode",
            "number",
        )
        value = str(value or fallback).strip()
        return value or str(fallback or "1")

    @staticmethod
    def _looks_like_season(entry):
        if not isinstance(entry, dict):
            return False
        season_number = str(
            StalkerPortalClient._first_entry_value(
                entry,
                "season_number",
            )
        ).strip()
        episode_number = str(
            StalkerPortalClient._first_entry_value(
                entry,
                "series_number",
                "episode_num",
                "episode_number",
            )
        ).strip()
        episode_id = StalkerPortalClient._series_object_entry_id(
            entry,
            ("episode_id", "file_id"),
        )
        return bool(
            StalkerPortalClient._portal_flag(entry.get("is_season"))
            or season_number and not episode_number and not episode_id
        )

    @staticmethod
    def _looks_like_episode(entry):
        if not isinstance(entry, dict):
            return False
        episode_number = str(
            StalkerPortalClient._first_entry_value(
                entry,
                "series_number",
                "episode_num",
                "episode_number",
            )
        ).strip()
        episode_id = StalkerPortalClient._series_object_entry_id(
            entry,
            ("episode_id", "file_id"),
        )
        return bool(
            StalkerPortalClient._portal_flag(entry.get("is_episode"))
            or episode_number
            or episode_id
        )

    def _episode_item(
        self,
        series_id,
        series_entry,
        season_number,
        season_id,
        entry,
        request_type,
        fallback_episode,
    ):
        episode_id = self._media_identifier(
            entry,
            "episode",
            allow_episode_composite=True,
        )
        if not episode_id:
            return None
        episode_number = self._episode_number(entry, fallback_episode)
        title = (
            entry.get("name")
            or entry.get("title")
            or entry.get("series_name")
            or entry.get("series_original_name")
            or _("Episode")
        )
        source_prefix = "S{} E{}".format(season_number, episode_number)
        prefix = _("S{} E{}").format(season_number, episode_number)
        normalized_title = str(title).strip().upper()
        if not any(
            normalized_title.startswith(candidate.upper())
            for candidate in (prefix, source_prefix)
        ):
            title = "{}  {}".format(prefix, title)
        command = self._channel_command(entry)
        command_is_fallback = False
        if not command:
            season_token = self._numeric_entry_id(
                {"season": season_number},
                ("season",),
                allow_zero=True,
            )
            numeric_series_id = self._numeric_entry_id(
                {"series_id": series_id},
                ("series_id",),
            )
            if numeric_series_id:
                if request_type == "series" and season_token:
                    command = "/media/{}:{}.mpg".format(
                        numeric_series_id,
                        season_token,
                    )
                else:
                    command = "/media/{}.mpg".format(
                        numeric_series_id
                    )
                command_is_fallback = True
            else:
                # Composite IDs are API object identifiers, not path
                # segments.  Resolve a real file/cmd response before play.
                command = ""
        icon = self._first_media_url(
            entry,
            "screenshot_uri", "movie_image", "cover_big", "cover",
            "poster", "stream_icon",
        ) or self._first_media_url(
            series_entry,
            "screenshot_uri", "movie_image", "cover_big", "cover",
            "poster", "stream_icon",
        )
        item = ContentItem(
            "series",
            episode_id,
            title,
            extension=(
                entry.get("container_extension")
                or self._command_extension(command)
            ),
            icon=icon,
            season=season_number,
            episode=episode_number,
            duration=entry.get("duration") or entry.get("time"),
            plot=(
                entry.get("plot")
                or entry.get("description")
                or entry.get("overview")
            ),
            parent_id=series_id,
        )
        item.portal_command = command
        item.portal_command_is_fallback = command_is_fallback
        item.portal_request_type = request_type
        item.portal_series = episode_number
        item.portal_parent_id = series_id
        item.portal_season_id = str(season_id or "")
        item.portal_episode_id = episode_id

        def portal_value(*keys):
            for source in (entry, series_entry):
                if not isinstance(source, dict):
                    continue
                for key in keys:
                    if key in source:
                        return source.get(key)
            return None

        item.portal_use_tmp_link = StalkerPortalClient._portal_flag(
            portal_value("use_http_tmp_link")
        )
        item.portal_use_load_balancing = StalkerPortalClient._portal_flag(
            portal_value(
                "use_load_balancing",
                "enable_wowza_load_balancing",
            )
        )
        item.portal_disable_ad = StalkerPortalClient._portal_flag(
            portal_value("disable_ad")
        )
        return item

    def _series_parameter_variants(
        self,
        request_type,
        action,
        series_id,
        extras=(),
        scope="",
    ):
        series_id = safe_series_id(series_id)
        if not series_id:
            return []
        request_type = str(request_type or "vod").strip().lower()
        action = str(action or "get_ordered_list").strip().lower()
        scope = str(scope or "").strip().lower()
        cache_key = (request_type, action, scope)
        preferred = "series_id" if request_type == "series" else "movie_id"
        alternate = "movie_id" if preferred == "series_id" else "series_id"
        styles = []
        for style in (
            self._series_parameter_hints.get(cache_key),
            preferred,
            alternate,
            "both",
        ):
            if style and style not in styles:
                styles.append(style)
        variants = []
        for style in styles:
            if style == "both":
                parameters = [
                    (preferred, series_id),
                    (alternate, series_id),
                ]
            else:
                parameters = [(style, series_id)]
            parameters.extend(tuple(extras or ()))
            variants.append((style, tuple(parameters)))
        return variants

    def _remember_series_parameter_hint(
        self,
        request_type,
        action,
        style,
        scope="",
    ):
        request_type = str(request_type or "vod").strip().lower()
        action = str(action or "get_ordered_list").strip().lower()
        style = str(style or "").strip().lower()
        scope = str(scope or "").strip().lower()
        if style in ("movie_id", "series_id", "both"):
            self._series_parameter_hints[
                (request_type, action, scope)
            ] = style

    @staticmethod
    def _raise_terminal_series_error(error):
        if error.reason in (
            ContentError.CANCELLED,
            ContentError.DEADLINE,
            ContentError.ACCESS_DENIED,
            ContentError.RATE_LIMITED,
        ):
            raise error

    @staticmethod
    def _nested_episode_groups(value, depth=0):
        if not isinstance(value, dict) or depth > 4:
            return []
        episodes = value.get("episodes")
        if not isinstance(episodes, (dict, list, tuple)):
            for key in ("data", "items", "results", "js"):
                groups = StalkerPortalClient._nested_episode_groups(
                    value.get(key),
                    depth + 1,
                )
                if groups:
                    return groups
        if not isinstance(episodes, (dict, list, tuple)):
            return []
        groups = []
        if isinstance(episodes, dict):
            for season, entries in episodes.items():
                if isinstance(entries, dict):
                    entries = StalkerPortalClient._data_list(entries)
                if isinstance(entries, (list, tuple)):
                    groups.append((str(season), list(entries)))
        else:
            groups.append(("1", list(episodes)))
        return groups

    @staticmethod
    def _series_hierarchy_entry(entry):
        return bool(
            StalkerPortalClient._looks_like_season(entry)
            or StalkerPortalClient._series_episode_entry(entry)
        )

    @staticmethod
    def _series_episode_entry(entry):
        if not isinstance(entry, dict):
            return False
        semantic_id = StalkerPortalClient._series_object_entry_id(
            entry,
            ("episode_id", "file_id"),
        )
        file_command_id = StalkerPortalClient._command_media_id(
            entry,
            "episode",
        )
        if semantic_id or file_command_id:
            return True
        if "episode_id" in entry or "file_id" in entry:
            return False
        return bool(
            StalkerPortalClient._looks_like_episode(entry)
            and StalkerPortalClient._series_object_entry_id(
                entry,
                ("stream_id", "id"),
            )
        )

    @staticmethod
    def _series_file_entry(entry, episode_id):
        if not isinstance(entry, dict):
            return False
        expected = safe_series_id(episode_id)
        if not expected:
            return False
        explicit_episode_id = StalkerPortalClient._series_object_entry_id(
            entry,
            ("episode_id",),
        )
        if explicit_episode_id and explicit_episode_id != expected:
            return False
        file_id = StalkerPortalClient._series_object_entry_id(
            entry,
            ("file_id",),
        )
        file_command_id = StalkerPortalClient._command_media_id(
            entry,
            "episode",
        )
        candidate = StalkerPortalClient._series_object_entry_id(
            entry,
            ("id", "stream_id"),
        )
        if file_id or file_command_id:
            return True
        if explicit_episode_id == expected:
            return bool(
                StalkerPortalClient._channel_command(entry)
                or candidate
            )
        if "episode_id" in entry or "file_id" in entry:
            return False
        return bool(candidate == expected)

    @staticmethod
    def _openxc_series_parts(value):
        """Return OpenXC's exact ``series_id:season_num`` pair."""
        value = safe_composite_id(value)
        if not value or value.count(":") != 1:
            return ()
        series_id, season_number = value.split(":", 1)
        series_id = StalkerPortalClient._numeric_entry_id(
            {"value": series_id},
            ("value",),
        )
        season_number = StalkerPortalClient._numeric_entry_id(
            {"value": season_number},
            ("value",),
            allow_zero=True,
        )
        if not series_id or not season_number:
            return ()
        return series_id, season_number

    @staticmethod
    def _openxc_episode_numbers(value):
        """Return a bounded, sorted numeric OpenXC episode vector.

        A scalar ``series=1`` is a common catalogue marker, so scalar values
        are intentionally not accepted as an episode vector.
        """
        if isinstance(value, dict):
            numeric_keys = [
                key for key in value
                if StalkerPortalClient._numeric_entry_id(
                    {"value": key}, ("value",)
                )
            ]
            candidates = numeric_keys or list(value.values())
        elif isinstance(value, (list, tuple, set)):
            candidates = list(value)
        elif isinstance(value, str):
            value = value.strip()
            if not value or len(value) > 4096:
                return []
            if value[:1] in ("[", "{"):
                try:
                    decoded = _provider_json_loads(value)
                except (TypeError, ValueError, RecursionError):
                    return []
                if decoded == value:
                    return []
                return StalkerPortalClient._openxc_episode_numbers(decoded)
            if not re.search(r"[,;|\s]", value):
                return []
            candidates = re.split(r"[,;|\s]+", value)
        else:
            return []

        numbers = []
        seen = set()
        for candidate in candidates:
            number = StalkerPortalClient._numeric_entry_id(
                {"value": candidate},
                ("value",),
            )
            if not number:
                continue
            numeric_value = int(number)
            if numeric_value in seen:
                continue
            seen.add(numeric_value)
            numbers.append(str(numeric_value))
            if len(numbers) >= MAX_RESULTS:
                break
        numbers.sort(key=int)
        return numbers

    @staticmethod
    def _openxc_episode_identity(series_id, season_number, episode_number):
        """Build a stable identity that is never sent as a media path."""
        series_id = StalkerPortalClient._numeric_entry_id(
            {"value": series_id},
            ("value",),
        )
        season_number = StalkerPortalClient._numeric_entry_id(
            {"value": season_number},
            ("value",),
            allow_zero=True,
        )
        episode_number = StalkerPortalClient._numeric_entry_id(
            {"value": episode_number},
            ("value",),
        )
        if not series_id or not season_number or not episode_number:
            return ""
        season_value = int(season_number)
        episode_value = int(episode_number)
        diagonal = season_value + episode_value
        paired = diagonal * (diagonal + 1) // 2 + episode_value + 1
        # Provider season components are at most 40 digits.  Reserving a
        # 41-digit namespace prevents an internal episode ID from ever being
        # mistaken for another OpenXC parent ID while remaining reversible.
        paired_token = str(paired)
        if len(paired_token) < 41:
            paired_token = paired_token.zfill(41)
        return safe_composite_id(
            "{}:{}".format(series_id, paired_token)
        )

    @staticmethod
    def _is_openxc_episode_identity(parent_id, episode_id):
        parent_parts = StalkerPortalClient._openxc_series_parts(parent_id)
        episode_id = safe_composite_id(episode_id)
        if not parent_parts or not episode_id or episode_id.count(":") != 1:
            return False
        series_id, token = episode_id.split(":", 1)
        return bool(
            series_id == parent_parts[0]
            and len(token) >= 41
            and token.isdigit()
        )

    def _openxc_episode_item(
        self,
        parent_id,
        series_entry,
        season_entry,
        season_number,
        episode_number,
        command,
    ):
        """Normalise one OpenXC episode without inventing a media command."""
        parent_parts = self._openxc_series_parts(parent_id)
        episode_id = self._openxc_episode_identity(
            parent_parts[0] if parent_parts else "",
            season_number,
            episode_number,
        )
        if not parent_parts or not episode_id:
            return None
        episode_entry = dict(season_entry or {})
        episode_entry["episode_id"] = episode_id
        episode_entry["episode_number"] = episode_number
        episode_entry["name"] = _("Episode")
        # Keep the opaque command out of generic ID extraction.  Some forks
        # use a /media/file_N command for the entire season; it is not a
        # unique episode object ID.  Attach it only after the stable identity
        # has been created.
        for key in ("cmd", "url", "mc_cmd", "commands", "cmds"):
            episode_entry.pop(key, None)
        item = self._episode_item(
            parent_id,
            series_entry,
            season_number,
            season_number,
            episode_entry,
            "vod",
            episode_number,
        )
        if item is None:
            return None
        item.portal_command = command
        item.portal_openxc_series = True
        item.portal_command_is_fallback = False
        item.portal_request_type = "vod"
        item.portal_series = episode_number
        return item

    def _log_openxc_series_hierarchy(
        self,
        outcome,
        seasons=0,
        episodes=0,
        command_seasons=0,
        stop="response",
    ):
        try:
            log_event(
                "portal",
                (
                    "Series hierarchy profile=openxc outcome={} requests=1 "
                    "seasons={} episodes={} command_seasons={} stop={}"
                ).format(
                    outcome,
                    int(seasons),
                    int(episodes),
                    int(command_seasons),
                    stop,
                ),
            )
        except Exception:
            pass

    def _load_openxc_series_hierarchy(self, series_id, series_entry):
        """Load the verified OpenXC season vectors with one request.

        ``None`` means the response did not match OpenXC and the normal
        Ministra loader must continue.  A matched profile returns a list,
        including an authoritative empty list, without probing other APIs.
        """
        if self._vod_entry_sources.get(("series", series_id), "") != "series":
            return None
        parent_parts = self._openxc_series_parts(series_id)
        if not parent_parts:
            return None
        try:
            value = self._request(
                "series",
                "get_ordered_list",
                (("movie_id", parent_parts[0]), ("p", "1")),
            )
        except ContentError as error:
            self._raise_terminal_series_error(error)
            if error.reason == ContentError.INVALID_RESPONSE:
                self._log_openxc_series_hierarchy(
                    "empty",
                    stop="invalid-response",
                )
                return []
            self._log_openxc_series_hierarchy(
                "unavailable",
                stop="request-error",
            )
            return None

        entries = self._data_list(value)
        if not entries and self._openxc_episode_numbers(
            (series_entry or {}).get("series")
        ):
            entries = [series_entry]

        seasons = []
        for season_entry in entries:
            if not isinstance(season_entry, dict):
                continue
            season_parts = self._openxc_series_parts(
                self._first_entry_value(
                    season_entry,
                    "id", "series_id", "stream_id",
                )
            )
            if not season_parts or season_parts[0] != parent_parts[0]:
                continue
            episode_numbers = self._openxc_episode_numbers(
                season_entry.get("series")
            )
            if not episode_numbers:
                continue
            command = self._channel_command(season_entry)
            if len(command) > 8192 or _CONTROL_PATTERN.search(command):
                command = ""
            seasons.append((
                int(season_parts[1]),
                season_parts[1],
                season_entry,
                episode_numbers,
                command,
            ))

        if not seasons:
            self._log_openxc_series_hierarchy(
                "unsupported",
                stop="schema-mismatch",
            )
            return None

        seasons.sort(key=lambda value: value[0])
        results = []
        seen = set()
        command_seasons = 0
        for unused_order, season_number, season_entry, numbers, command in seasons:
            if command:
                command_seasons += 1
            for episode_number in numbers:
                identity = (season_number, episode_number)
                if identity in seen:
                    continue
                seen.add(identity)
                item = self._openxc_episode_item(
                    series_id,
                    series_entry,
                    season_entry,
                    season_number,
                    episode_number,
                    command,
                )
                if item is not None:
                    results.append(item)
                if len(results) >= MAX_RESULTS:
                    break
            if len(results) >= MAX_RESULTS:
                break
        self._log_openxc_series_hierarchy(
            "available" if results else "empty",
            seasons=len(seasons),
            episodes=len(results),
            command_seasons=command_seasons,
            stop="results-found" if results else "empty-vector",
        )
        return results

    def _load_series_hierarchy(self, series_id, series_entry):
        openxc_results = self._load_openxc_series_hierarchy(
            series_id,
            series_entry,
        )
        if openxc_results is not None:
            return openxc_results
        preferred = self._vod_entry_sources.get(("series", series_id), "")
        request_types = []
        for request_type in (preferred, "series", "vod"):
            request_type = str(request_type or "").strip().lower()
            if request_type and request_type not in request_types:
                request_types.append(request_type)
        for request_type in request_types:
            variants = self._series_parameter_variants(
                request_type,
                "get_series_info",
                series_id,
                scope="info",
            )
            for variant_index, (style, parameters) in enumerate(variants):
                try:
                    value = self._request(
                        request_type,
                        "get_series_info",
                        parameters,
                    )
                except ContentError as error:
                    self._raise_terminal_series_error(error)
                    value = None
                series_entry.update(
                    self._merge_detail_metadata({}, value)
                )
                results = []
                for season_number, episode_entries in self._nested_episode_groups(
                    value
                ):
                    for index, episode_entry in enumerate(episode_entries, 1):
                        if not isinstance(episode_entry, dict):
                            continue
                        item = self._episode_item(
                            series_id,
                            series_entry,
                            season_number,
                            episode_entry.get("season_id") or season_number,
                            episode_entry,
                            request_type,
                            index,
                        )
                        if item is not None:
                            results.append(item)
                if results:
                    self._remember_series_parameter_hint(
                        request_type,
                        "get_series_info",
                        style,
                        scope="info",
                    )
                    return results[:MAX_RESULTS]
                if variant_index + 1 < len(variants):
                    self._paced_catalog_wait()

        for request_type in request_types:
            first_level = []
            variants = self._series_parameter_variants(
                request_type,
                "get_ordered_list",
                series_id,
                scope="hierarchy",
            )
            for variant_index, (style, parameters) in enumerate(variants):
                try:
                    first_level = self._paged_vod_request(
                        request_type,
                        parameters,
                        self._series_hierarchy_entry,
                    )
                except ContentError as error:
                    self._raise_terminal_series_error(error)
                    first_level = []
                if first_level:
                    self._remember_series_parameter_hint(
                        request_type,
                        "get_ordered_list",
                        style,
                        scope="hierarchy",
                    )
                    break
                if variant_index + 1 < len(variants):
                    self._paced_catalog_wait()
            if not first_level:
                continue
            seasons = [entry for entry in first_level if self._looks_like_season(entry)]
            direct_episodes = [
                entry for entry in first_level
                if self._series_episode_entry(entry)
            ]
            if direct_episodes:
                seasons = [
                    {
                        "id": "",
                        "season_number": self._season_number(
                            direct_episodes[0], "1"
                        ),
                        "_episodes": direct_episodes,
                    }
                ]
            results = []
            for season_index, season_entry in enumerate(seasons, 1):
                season_id = str(season_entry.get("id") or "").strip()
                season_number = self._season_number(season_entry, season_index)
                episode_entries = season_entry.get("_episodes")
                if episode_entries is None:
                    episode_entries = []
                    variants = self._series_parameter_variants(
                        request_type,
                        "get_ordered_list",
                        series_id,
                        extras=(("season_id", season_id),),
                        scope="episodes",
                    )
                    for variant_index, (style, parameters) in enumerate(
                        variants
                    ):
                        try:
                            episode_entries = self._paged_vod_request(
                                request_type,
                                parameters,
                                self._series_episode_entry,
                            )
                        except ContentError as error:
                            self._raise_terminal_series_error(error)
                            episode_entries = []
                        if episode_entries:
                            self._remember_series_parameter_hint(
                                request_type,
                                "get_ordered_list",
                                style,
                                scope="episodes",
                            )
                            break
                        if variant_index + 1 < len(variants):
                            self._paced_catalog_wait()
                for episode_index, episode_entry in enumerate(
                    episode_entries or (), 1
                ):
                    if not isinstance(episode_entry, dict):
                        continue
                    item = self._episode_item(
                        series_id,
                        series_entry,
                        season_number,
                        season_id,
                        episode_entry,
                        request_type,
                        episode_index,
                    )
                    if item is not None:
                        results.append(item)
                        if len(results) >= MAX_RESULTS:
                            return results
            if results:
                return results
        return []

    def _series_info_from_entry(self, entry, episodes):
        first = lambda *keys: self._first_entry_value(entry, *keys)
        return SeriesInfo(
            title=first("name", "title", "o_name"),
            plot=first("plot", "description", "overview"),
            rating=first(
                "rating", "rating_imdb", "rating_kinopoisk", "rating_5based"
            ),
            year=first("year", "release_year", "releasedate", "releaseDate"),
            genre=first("genres_str", "genre", "genres"),
            director=first("director"),
            cast=first("actors", "cast"),
            cover=self._first_media_url(
                entry,
                "screenshot_uri", "movie_image", "cover_big", "cover",
                "poster", "stream_icon",
            ),
            episodes=episodes,
            backdrop=self._first_media_url(
                entry,
                "backdrop_path", "backdrop", "backdrop_url",
                "backdrop_image", "background", "background_url",
                "background_image", "image_background", "fanart",
                "fanart_url", "screenshots", "screenshot", "screenshot_url",
                "cover_landscape", "landscape", "landscape_url", "hero",
                "hero_image", "banner", "banner_url",
            ),
            tmdb_id=first("tmdb_id", "tmdbId", "themoviedb_id"),
        )

    def load_series_summary(self, series_id):
        """Return poster/detail metadata without walking the episode tree."""
        series_id = safe_series_id(series_id)
        if not series_id:
            raise ContentError(
                N_("Portal request failed"),
                ContentError.CONTENT_RESPONSE,
            )
        cached = self._series_info_cache.get(series_id)
        if cached and self.clock() - cached[0] < CONTENT_CACHE_SECONDS:
            self._series_info_cache.move_to_end(series_id)
            return cached[1]
        entry = self._cached_media_entry("series", series_id)
        return self._series_info_from_entry(entry, [])

    def load_series_info(self, series_id):
        series_id = safe_series_id(series_id)
        if not series_id:
            raise ContentError(
                N_("Portal request failed"),
                ContentError.CONTENT_RESPONSE,
            )
        cached = self._series_info_cache.get(series_id)
        if cached and self.clock() - cached[0] < CONTENT_CACHE_SECONDS:
            self._series_info_cache.move_to_end(series_id)
            return cached[1]
        self._series_info_cache.pop(series_id, None)
        entry = self._cached_media_entry("series", series_id)
        episodes = self._load_series_hierarchy(series_id, entry)
        try:
            log_event(
                "portal",
                (
                    "Series hierarchy outcome={} episodes={} "
                    "parent_id_class={}"
                ).format(
                    "available" if episodes else "empty",
                    len(episodes),
                    (
                        "numeric-composite"
                        if is_composite_id(series_id)
                        else "numeric"
                    ),
                ),
            )
        except Exception:
            pass
        info = self._series_info_from_entry(entry, episodes)
        self._lru_store(
            self._series_info_cache,
            series_id,
            (self.clock(), info),
            SERIES_INFO_CACHE_LIMIT,
        )
        return info

    def load_episodes(self, series_id):
        return list(self.load_series_info(series_id).episodes)

    @staticmethod
    def _link_value(value, depth=0):
        if depth > 5:
            return ""
        if isinstance(value, bytes):
            value = value.decode("utf-8-sig", "replace")
        if isinstance(value, dict):
            for key in ("cmd", "url", "link", "stream_url"):
                if key not in value:
                    continue
                candidate = StalkerPortalClient._link_value(
                    value.get(key), depth + 1
                )
                if candidate:
                    return candidate
            for key in ("data", "js", "result", "items"):
                if key not in value:
                    continue
                candidate = StalkerPortalClient._link_value(
                    value.get(key), depth + 1
                )
                if candidate:
                    return candidate
            return ""
        if isinstance(value, (list, tuple)):
            for entry in value:
                candidate = StalkerPortalClient._link_value(entry, depth + 1)
                if candidate:
                    return candidate
            return ""
        value = str(value or "").strip()
        if value[:1] in ("{", "[", "\""):
            try:
                decoded = _provider_json_loads(value)
            except (TypeError, ValueError, RecursionError):
                decoded = value
            if decoded is not value and decoded != value:
                candidate = StalkerPortalClient._link_value(
                    decoded, depth + 1
                )
                if candidate:
                    return candidate
        return value

    @staticmethod
    def _playable_url(value):
        value = StalkerPortalClient._link_value(value)
        value = str(value or "").strip().strip("\"'").replace("\\/", "/")
        while _COMMAND_PREFIX.match(value):
            value = _COMMAND_PREFIX.sub("", value, count=1).strip()
            value = value.strip("\"'").strip()
        if (
            not value
            or len(value) > 8192
            or any(character in value for character in ("\r", "\n", "\x00"))
        ):
            raise ContentError(
                N_("Invalid stream link"),
                ContentError.PLAYBACK_URL,
            )
        # Enigma2/GStreamer accepts optional URL header suffixes after `|`.
        # Validate the network URL itself while preserving that suffix.
        network_url = value.split("|", 1)[0].strip()
        try:
            parsed = urlsplit(network_url)
            unused_port = parsed.port
            del unused_port
        except (TypeError, ValueError):
            raise ContentError(
                N_("Invalid stream link"),
                ContentError.PLAYBACK_URL,
            )
        if (
            parsed.scheme.lower() not in _STREAM_SCHEMES
            or not parsed.netloc
            or not parsed.hostname
        ):
            raise ContentError(
                N_("Invalid stream link"),
                ContentError.PLAYBACK_URL,
            )
        return value

    @staticmethod
    def _create_link_candidates(value, depth=0):
        """Collect bounded link candidates from all recognised containers."""

        candidates = []
        seen = set()
        visited = [0]
        link_keys = ("cmd", "url", "link", "stream_url")
        container_keys = (
            "data",
            "js",
            "result",
            "items",
            "cmd",
            "url",
            "link",
            "stream_url",
        )

        def encoded_container(candidate):
            if not isinstance(candidate, str):
                return False
            candidate = candidate.strip()
            return bool(candidate[:1] in ("{", "[", '"'))

        def add(candidate, is_ad):
            if len(candidates) < CREATE_LINK_MAX_ENTRIES:
                candidates.append((candidate, bool(is_ad)))

        def walk(candidate, current_depth, inherited_ad=False):
            if (
                current_depth > CREATE_LINK_MAX_DEPTH
                or visited[0] >= CREATE_LINK_MAX_NODES
                or len(candidates) >= CREATE_LINK_MAX_ENTRIES
            ):
                return
            visited[0] += 1
            if isinstance(candidate, bytes):
                if len(candidate) > MAX_API_BYTES:
                    return
                candidate = candidate.decode("utf-8-sig", "replace")
            if isinstance(candidate, str):
                if encoded_container(candidate):
                    try:
                        encoded_size = len(candidate.encode("utf-8"))
                    except (UnicodeError, ValueError):
                        encoded_size = MAX_API_BYTES + 1
                    if encoded_size <= MAX_API_BYTES:
                        try:
                            decoded = _provider_json_loads(candidate)
                        except (TypeError, ValueError, RecursionError):
                            decoded = candidate
                        if decoded is not candidate and decoded != candidate:
                            walk(decoded, current_depth + 1, inherited_ad)
                            return
                add(candidate, inherited_ad)
                return
            if isinstance(candidate, (list, tuple)):
                identity = id(candidate)
                if identity in seen:
                    return
                seen.add(identity)
                for entry in candidate:
                    walk(entry, current_depth + 1, inherited_ad)
                    if (
                        visited[0] >= CREATE_LINK_MAX_NODES
                        or len(candidates) >= CREATE_LINK_MAX_ENTRIES
                    ):
                        break
                return
            if not isinstance(candidate, dict):
                return
            identity = id(candidate)
            if identity in seen:
                return
            seen.add(identity)
            is_ad = bool(
                inherited_ad
                or str(candidate.get("type") or "").strip().lower() == "ad"
            )
            direct_link = False
            for key in link_keys:
                if key not in candidate:
                    continue
                link_value = candidate.get(key)
                if isinstance(link_value, (dict, list, tuple)):
                    continue
                if encoded_container(link_value):
                    continue
                direct_link = True
                break
            if direct_link:
                add(candidate, is_ad)
            for key in container_keys:
                if key not in candidate:
                    continue
                nested = candidate.get(key)
                if (
                    key in link_keys
                    and not isinstance(nested, (dict, list, tuple))
                    and not encoded_container(nested)
                ):
                    continue
                walk(nested, current_depth + 1, is_ad)
                if (
                    visited[0] >= CREATE_LINK_MAX_NODES
                    or len(candidates) >= CREATE_LINK_MAX_ENTRIES
                ):
                    break

        walk(value, max(0, int(depth or 0)))
        return tuple(candidates)

    @staticmethod
    def _create_link_entries(value, depth=0):
        return tuple(
            entry
            for entry, unused_is_ad in
            StalkerPortalClient._create_link_candidates(value, depth)
        )

    @staticmethod
    def _create_link_playable_url(value):
        candidates = StalkerPortalClient._create_link_candidates(value)
        for entry, is_ad in candidates:
            if is_ad:
                continue
            try:
                return StalkerPortalClient._playable_url(entry)
            except ContentError:
                continue
        for entry, is_ad in candidates:
            if not is_ad:
                continue
            try:
                return StalkerPortalClient._playable_url(entry)
            except ContentError:
                continue
        # Preserve the established parser as the compatibility fallback when
        # a response has no usable non-ad record.
        return StalkerPortalClient._playable_url(value)

    @staticmethod
    def _create_link_playable_choice(value, prefer_alternate=False):
        """Choose a validated create_link field without exposing its value."""

        field_order = (
            ("url", "link", "stream_url", "cmd")
            if prefer_alternate
            else ("cmd", "url", "link", "stream_url")
        )
        choices = []
        seen = set()
        valid_count = 0
        available_fields = []
        candidates = StalkerPortalClient._create_link_candidates(value)
        for wanted_ad in (False, True):
            for entry, is_ad in candidates:
                if bool(is_ad) != wanted_ad:
                    continue
                if isinstance(entry, dict):
                    raw_choices = (
                        (field, entry.get(field))
                        for field in field_order
                        if field in entry
                    )
                else:
                    raw_choices = (("value", entry),)
                for field, raw_value in raw_choices:
                    try:
                        playable = StalkerPortalClient._playable_url(raw_value)
                    except ContentError:
                        continue
                    valid_count += 1
                    if field not in available_fields:
                        available_fields.append(field)
                    if playable in seen:
                        continue
                    seen.add(playable)
                    choices.append((playable, field))
        if not choices:
            playable = StalkerPortalClient._create_link_playable_url(value)
            choices.append((playable, "fallback"))
            valid_count = 1
            available_fields = ["fallback"]

        playable, selected_field = choices[0]
        network_url = playable.split("|", 1)[0].strip()
        extension = "none"
        query_present = False
        try:
            parsed = urlsplit(network_url)
            unused_root, suffix = os.path.splitext(parsed.path or "")
            del unused_root
            if re.match(r"^\.[a-z0-9]{1,8}$", suffix or "", re.I):
                extension = suffix.lower()
            query_present = bool(parsed.query)
        except (TypeError, ValueError):
            pass
        safe_fields = tuple(
            field
            for field in available_fields
            if field in ("cmd", "url", "link", "stream_url", "value", "fallback")
        )
        metadata = {
            "selected": selected_field,
            "valid": valid_count,
            "distinct": len(choices),
            "extension": extension,
            "query": "yes" if query_present else "no",
            "alternate": (
                "yes"
                if selected_field in ("url", "link", "stream_url")
                else "no"
            ),
            "fields": ",".join(safe_fields) or "none",
        }
        return playable, metadata

    @staticmethod
    def _static_playable_url(command):
        try:
            value = StalkerPortalClient._playable_url(command)
        except ContentError:
            return ""
        network_url = value.split("|", 1)[0].strip()
        try:
            hostname = str(urlsplit(network_url).hostname or "").lower()
        except (TypeError, ValueError):
            return ""
        if (
            hostname in _LOOPBACK_HOSTS
            or hostname.endswith(".localhost")
            or hostname.startswith("127.")
        ):
            return ""
        return value

    def _requires_create_link(self, item, static_url):
        content_type = str(
            getattr(item, "content_type", "") or ""
        ).lower()
        return (
            not static_url
            # A Stalker VOD catalogue command is provider metadata, not a
            # durable playback contract.  Resolve it for every explicit open;
            # if the portal cannot create a link, _playback_url retains the
            # established validated-static fallback below.
            or content_type in ("movie", "series")
            or bool(getattr(item, "portal_use_tmp_link", False))
            or bool(getattr(item, "portal_use_load_balancing", False))
            or (
                content_type == "live"
                and self._force_ch_link_check
            )
        )

    @staticmethod
    def _safe_media_header(value):
        value = str(value or "").strip()
        if (
            not value
            or len(value) > 4096
            or any(character in value for character in ("\r", "\n", "\x00", "&"))
        ):
            return ""
        return value

    def _media_header_pairs(self, stream_url):
        headers = [
            ("User-Agent", _MAG_USER_AGENT),
            ("Referer", self.account.portal_url),
            ("X-User-Agent", _MAG_X_USER_AGENT),
        ]
        stream_origin = self._url_origin(stream_url)
        if (
            stream_origin
            and stream_origin == self._url_origin(self.account.portal_url)
            and not self._session.revoked
        ):
            credential = self._credential_snapshot()
            headers.append(
                (
                    "Cookie",
                    self._cookie(
                        include_token=True,
                        request_url=stream_url,
                        token=credential[0],
                    ),
                )
            )
            if credential[0]:
                headers.append(
                    ("Authorization", "Bearer {}".format(credential[0]))
                )
        return headers

    def _with_media_headers(self, stream_url):
        """Attach Enigma2/GStreamer HTTP headers without changing players."""
        value = str(stream_url or "").strip()
        if not value:
            raise ContentError(
                N_("Invalid stream link"),
                ContentError.PLAYBACK_URL,
            )
        base_url = value
        header_text = ""
        if "#" in base_url:
            base_url, header_text = base_url.split("#", 1)
        elif "|" in base_url:
            candidate_url, candidate_headers = base_url.split("|", 1)
            if "=" in candidate_headers:
                base_url = candidate_url
                header_text = candidate_headers
        try:
            scheme = urlsplit(base_url).scheme.lower()
        except (TypeError, ValueError):
            scheme = ""
        # OpenPLi's native service parser supports #Header=Value for HTTP and
        # RTSP sources. Other protocols must keep their original URL intact.
        if not scheme.startswith(("http", "rtsp")):
            return value
        parts = []
        names = set()
        indexes = {}
        for part in header_text.split("&") if header_text else ():
            name, separator, header_value = part.partition("=")
            name = name.strip()
            header_value = self._safe_media_header(header_value)
            if not separator or not name or not header_value:
                continue
            lowered = name.lower()
            if lowered in names:
                continue
            names.add(lowered)
            indexes[lowered] = len(parts)
            parts.append((name, header_value))
        for name, header_value in self._media_header_pairs(base_url):
            lowered = name.lower()
            header_value = self._safe_media_header(header_value)
            if not header_value:
                continue
            if lowered == "cookie" and lowered in names:
                index = indexes[lowered]
                provider_name, provider_cookie = parts[index]
                merged = self._safe_media_header(
                    _merge_cookie_headers(header_value, provider_cookie)
                )
                if merged:
                    parts[index] = (provider_name, merged)
                continue
            if lowered in names:
                continue
            names.add(lowered)
            indexes[lowered] = len(parts)
            parts.append((name, header_value))
        if not parts:
            return base_url
        return "{}#{}".format(
            base_url,
            "&".join("{}={}".format(name, item) for name, item in parts),
        )

    def _remember_episode_command(self, cache_key, item, command):
        # A saved bookmark has no portal command or runtime link policy.
        # Keep those together in memory so a warm resume can restore the
        # episode selector without another hierarchy request.
        metadata = {}
        for attribute in (
            "portal_parent_id", "portal_season_id", "portal_episode_id",
            "portal_request_type", "portal_series", "portal_openxc_series",
            "portal_use_tmp_link", "portal_use_load_balancing",
            "portal_disable_ad",
        ):
            if hasattr(item, attribute):
                metadata[attribute] = getattr(item, attribute)
        metadata["portal_command_is_fallback"] = False
        self._lru_store(
            self._episode_command_cache,
            cache_key,
            {"command": command, "metadata": metadata},
            EPISODE_COMMAND_CACHE_LIMIT,
        )

    def _resolve_episode_command(self, item, fallback_command):
        parent_id = safe_series_id(
            getattr(item, "portal_parent_id", "")
            or getattr(item, "parent_id", "")
        )
        if not parent_id:
            return fallback_command
        openxc_series = bool(
            getattr(item, "portal_openxc_series", False)
            or self._is_openxc_episode_identity(
                parent_id,
                getattr(item, "stream_id", ""),
            )
        )
        cache_key = (
            parent_id,
            str(getattr(item, "season", "") or ""),
            str(getattr(item, "episode", "") or ""),
            str(getattr(item, "stream_id", "") or ""),
        )
        cached = self._episode_command_cache.get(cache_key)
        snapshot = bool(
            not fallback_command
            and not getattr(item, "portal_episode_id", "")
        )
        if cached:
            self._episode_command_cache.move_to_end(cache_key)
            if isinstance(cached, dict):
                for attribute, value in cached.get("metadata", {}).items():
                    setattr(item, attribute, value)
                return str(cached.get("command", "") or "")
            if openxc_series:
                item.portal_openxc_series = True
                item.portal_request_type = "vod"
                item.portal_series = str(
                    getattr(item, "episode", "") or ""
                )
            if not snapshot or openxc_series:
                return cached

        fallback_is_generated = bool(
            getattr(item, "portal_command_is_fallback", False)
        )
        if fallback_command and not fallback_is_generated:
            self._remember_episode_command(cache_key, item, fallback_command)
            return fallback_command

        season_id = str(getattr(item, "portal_season_id", "") or "")
        episode_id = str(getattr(item, "portal_episode_id", "") or "")
        request_type = str(
            getattr(item, "portal_request_type", "") or "vod"
        ).strip().lower()
        if not season_id or not episode_id:
            try:
                episodes = self.load_series_info(parent_id).episodes
            except ContentError as error:
                self._raise_terminal_series_error(error)
                episodes = []
            selected = None
            number_match = None
            saved_id = str(getattr(item, "stream_id", "") or "")
            saved_season = str(getattr(item, "season", "") or "")
            saved_episode = str(getattr(item, "episode", "") or "")
            for candidate in episodes:
                same_id = bool(saved_id) and str(
                    getattr(candidate, "stream_id", "")
                ) == saved_id
                same_number = bool(saved_season and saved_episode) and (
                    str(getattr(candidate, "season", "")) == saved_season
                    and str(getattr(candidate, "episode", "")) == saved_episode
                )
                if same_id:
                    selected = candidate
                    break
                if same_number and number_match is None:
                    number_match = candidate
            selected = selected if selected is not None else number_match
            if snapshot and selected is None:
                return ""
            if selected is not None:
                candidate = selected
                season_id = str(
                    getattr(candidate, "portal_season_id", "") or ""
                )
                episode_id = str(
                    getattr(candidate, "portal_episode_id", "") or ""
                )
                request_type = str(
                    getattr(candidate, "portal_request_type", "") or "vod"
                )
                fallback_command = str(
                    getattr(candidate, "portal_command", "")
                    or fallback_command
                )
                fallback_is_generated = bool(
                    getattr(candidate, "portal_command_is_fallback", False)
                )
                openxc_series = bool(
                    getattr(candidate, "portal_openxc_series", False)
                )
                for attribute, value in (
                    ("portal_season_id", season_id),
                    ("portal_episode_id", episode_id),
                    ("portal_request_type", request_type),
                    (
                        "portal_series",
                        str(getattr(candidate, "portal_series", "") or ""),
                    ),
                    ("portal_openxc_series", openxc_series),
                    ("portal_command_is_fallback", fallback_is_generated),
                    (
                        "portal_use_tmp_link",
                        bool(
                            getattr(
                                candidate,
                                "portal_use_tmp_link",
                                False,
                            )
                        ),
                    ),
                    (
                        "portal_use_load_balancing",
                        bool(
                            getattr(
                                candidate,
                                "portal_use_load_balancing",
                                False,
                            )
                        ),
                    ),
                    (
                        "portal_disable_ad",
                        bool(
                            getattr(candidate, "portal_disable_ad", False)
                        ),
                    ),
                ):
                    setattr(item, attribute, value)
                item.portal_parent_id = parent_id

        if cached:
            self._remember_episode_command(cache_key, item, cached)
            return cached

        if fallback_command and not fallback_is_generated:
            self._remember_episode_command(cache_key, item, fallback_command)
            return fallback_command

        if openxc_series:
            # OpenXC exposes the final opaque season command in the hierarchy
            # response.  Missing means unavailable; generic file probes or a
            # synthetic /media path would target a different protocol.
            return ""

        if season_id and episode_id:
            files = []
            variants = self._series_parameter_variants(
                request_type,
                "get_ordered_list",
                parent_id,
                extras=(
                    ("season_id", season_id),
                    ("episode_id", episode_id),
                ),
                scope="files",
            )
            for variant_index, (style, parameters) in enumerate(variants):
                try:
                    files = self._paged_vod_request(
                        request_type,
                        parameters,
                        lambda entry: self._series_file_entry(
                            entry,
                            episode_id,
                        ),
                    )
                except ContentError as error:
                    self._raise_terminal_series_error(error)
                    files = []
                if files:
                    self._remember_series_parameter_hint(
                        request_type,
                        "get_ordered_list",
                        style,
                        scope="files",
                    )
                    break
                if variant_index + 1 < len(variants):
                    self._paced_catalog_wait()
            for entry in files:
                if not isinstance(entry, dict):
                    continue
                command = self._channel_command(entry)
                file_id = self._numeric_entry_id(
                    entry,
                    ("file_id", "id", "stream_id"),
                )
                if not command and file_id:
                    command = "/media/file_{}.mpg".format(file_id)
                if command:
                    self._remember_episode_command(cache_key, item, command)
                    return command
        return fallback_command

    def _movie_playback_item(self, item):
        """Recover one saved movie's portal command with bounded page loads."""
        stream_id = str(getattr(item, "stream_id", "") or "").strip()
        cache_key = ("movie", stream_id)
        entry = self._vod_entry_cache.get(cache_key)
        if entry is not None:
            self._vod_entry_cache.move_to_end(cache_key)
            request_type = (
                self._vod_entry_sources.get(cache_key)
                or getattr(item, "portal_request_type", "")
                or "vod"
            )
            return self._media_item(entry, "movie", request_type)

        category_id = str(getattr(item, "category_id", "") or "")
        categories = [category_id] if category_id else []
        categories.append("")
        steps = 0
        # The UI already supplies a deadline. Direct integrations also need
        # a finite lookup budget; nested scopes keep the earlier deadline.
        with self.request_scope(deadline=time.monotonic() + self.timeout):
            cached_cursors = {}
            completed_categories = set()
            with self._vod_catalog_critical():
                for category in categories:
                    self._check_request_limits()
                    state = self._vod_catalog_states.get(("movie", category))
                    if (
                        state is None
                        or self.clock() - state["cached_at"]
                        >= CONTENT_CACHE_SECONDS
                    ):
                        continue
                    # The smaller raw-entry LRU may have evicted this movie
                    # while an already loaded catalogue still has it. Read
                    # those rows without page pacing or stale dialect errors.
                    for candidate in state["results"]:
                        if str(candidate.stream_id) == stream_id:
                            return candidate
                    cached_cursors[category] = len(state["results"])
                    if state["complete"]:
                        completed_categories.add(category)
            for category in categories:
                if category in completed_categories:
                    continue
                cursor = cached_cursors.get(category, 0)
                while steps < MAX_PORTAL_PAGES:
                    self._check_request_limits()
                    with self._vod_catalog_critical():
                        state_key = ("movie", category)
                        previous_state = self._vod_catalog_states.get(state_key)
                        previous_requests = int(
                            (previous_state or {}).get("request_count", 0)
                        )
                        previous_successes = int(
                            (previous_state or {}).get(
                                "successful_request_count", 0
                            )
                        )
                        page = self.load_vod_catalog_step(category, cursor)
                        state = self._vod_catalog_states.get(state_key)
                        if state is not previous_state:
                            previous_requests = previous_successes = 0
                        # Each step makes at most one request. A successful
                        # page must not inherit an earlier dialect's error.
                        request_failed = bool(
                            state
                            and state["request_count"] > previous_requests
                            and state["successful_request_count"]
                            == previous_successes
                        )
                        error = state.get("last_error") if state else None
                    steps += 1
                    for candidate in page.items:
                        if str(candidate.stream_id) == stream_id:
                            return candidate
                    if (
                        request_failed
                        and isinstance(error, ContentError)
                        and error.reason in CREATE_LINK_TERMINAL_REASONS
                    ):
                        raise error
                    if page.complete or not page.progressed:
                        break
                    cursor = page.cursor
                    self._paced_catalog_wait()
                if steps >= MAX_PORTAL_PAGES:
                    break
                if category:
                    self._paced_catalog_wait()
        return None

    def _resolve_item_command(self, item):
        command = str(getattr(item, "portal_command", "") or "").strip()
        content_type = str(getattr(item, "content_type", "") or "").lower()
        stream_id = str(getattr(item, "stream_id", "") or "").strip()
        if not command and content_type == "live" and stream_id:
            # Favorite snapshots intentionally do not persist portal commands,
            # because those values can contain temporary URLs.  Rehydrate the
            # selected channel from the authenticated in-memory catalogue.
            for entry in self._load_all_channels():
                candidate = self._channel_item(entry)
                if candidate is None or str(candidate.stream_id) != stream_id:
                    continue
                command = str(
                    getattr(candidate, "portal_command", "") or ""
                ).strip()
                for attribute in (
                    "portal_channel_id",
                    "portal_epg_id",
                    "portal_xmltv_id",
                    "portal_epg_identities",
                    "portal_category_ids",
                    "portal_use_tmp_link",
                    "portal_use_load_balancing",
                    "portal_disable_ad",
                ):
                    setattr(item, attribute, getattr(candidate, attribute, ""))
                if not getattr(item, "icon", ""):
                    item.icon = candidate.icon
                break
        if not command and content_type == "movie" and stream_id.isdigit():
            candidate = self._movie_playback_item(item)
            if candidate is not None:
                command = str(getattr(candidate, "portal_command", "") or "")
                for attribute in (
                    "portal_request_type", "portal_series",
                    "portal_use_tmp_link", "portal_use_load_balancing",
                    "portal_disable_ad",
                ):
                    setattr(item, attribute, getattr(candidate, attribute, ""))
        if content_type == "series":
            command = self._resolve_episode_command(item, command)
        return command

    def _create_playback_url(
        self,
        command,
        request_type="itv",
        series="",
        disable_ad=False,
        retry_auth=True,
    ):
        request_type = str(request_type or "itv").strip().lower()
        # Resolve the endpoint before reading its compatibility hint.  The
        # first playback after login may enter this method with no endpoint
        # even though the successful create_link call establishes one.
        self._handshake()
        if self._endpoint and self._token:
            self._ensure_profile()
        request_types = [request_type]
        if request_type != "itv" and "vod" not in request_types:
            request_types.append("vod")
        candidates = [
            (candidate_type, forced_storage)
            for candidate_type in request_types
            for forced_storage in ("undefined", "false")
        ]
        hint_key = (
            str(self._endpoint or ""),
            request_type,
            bool(str(series or "").strip() not in ("", "0")),
        )
        with self._session_lock:
            hint = self._session.create_link_hints.get(hint_key)
            if hint is not None:
                self._session.create_link_hints.move_to_end(hint_key)
        if hint in candidates:
            candidates.remove(hint)
            candidates.insert(0, hint)
        last_error = None
        for candidate_type, forced_storage in candidates:
            try:
                request_arguments = (
                    ("cmd", command),
                    ("series", series),
                    ("forced_storage", forced_storage),
                    ("disable_ad", "1" if disable_ad else "0"),
                    ("download", "0"),
                    (
                        "force_ch_link_check",
                        "1"
                        if (
                            request_type == "itv"
                            and self._force_ch_link_check
                        )
                        else "0",
                    ),
                )
                if retry_auth:
                    value = self._request(
                        candidate_type,
                        "create_link",
                        request_arguments,
                    )
                else:
                    value = self._request(
                        candidate_type,
                        "create_link",
                        request_arguments,
                        retry=False,
                    )
                selected, choice = self._create_link_playable_choice(
                    value,
                    prefer_alternate=request_type != "itv",
                )
                playable = self._with_media_headers(selected)
                if request_type != "itv":
                    try:
                        log_event(
                            "portal",
                            (
                                "Stalker VOD link choice alternate={} valid={} "
                                "distinct={} selected={} extension={} query={} "
                                "fields={}"
                            ).format(
                                choice["alternate"],
                                choice["valid"],
                                choice["distinct"],
                                choice["selected"],
                                choice["extension"],
                                choice["query"],
                                choice["fields"],
                            ),
                        )
                    except Exception:
                        pass
            except ContentError as error:
                last_error = error
                if error.reason in CREATE_LINK_TERMINAL_REASONS:
                    raise
                continue
            learned = (candidate_type, forced_storage)
            learned_key = (
                str(self._endpoint or ""),
                request_type,
                bool(str(series or "").strip() not in ("", "0")),
            )
            with self._session_lock:
                self._lru_store(
                    self._session.create_link_hints,
                    learned_key,
                    learned,
                    CREATE_LINK_HINT_LIMIT,
                )
            return playable
        if hint is not None:
            with self._session_lock:
                if self._session.create_link_hints.get(hint_key) == hint:
                    self._session.create_link_hints.pop(hint_key, None)
        if last_error is not None:
            raise last_error
        raise ContentError(
            N_("Stream link unavailable"),
            ContentError.PLAYBACK_URL,
        )

    def _create_item_playback_url(
        self,
        item,
        command,
        request_type="itv",
        series="",
    ):
        if bool(getattr(item, "portal_disable_ad", False)):
            return self._create_playback_url(
                command,
                request_type=request_type,
                series=series,
                disable_ad=True,
            )
        return self._create_playback_url(
            command,
            request_type=request_type,
            series=series,
        )

    @staticmethod
    def _playback_cache_key(item, command, request_type, series):
        return (
            str(getattr(item, "content_type", "") or "").lower(),
            str(getattr(item, "stream_id", "") or ""),
            str(command or ""),
            str(request_type or ""),
            str(series or ""),
        )

    def _cached_playback_link(self, key):
        if self._session.revoked:
            return ""
        with self._playback_link_lock:
            if self._session.revoked:
                self._playback_link_cache.clear()
                return ""
            cached = self._playback_link_cache.get(key)
            if cached is None:
                return ""
            created_at, value = cached
            if self.clock() - float(created_at) >= PLAYBACK_LINK_CACHE_SECONDS:
                self._playback_link_cache.pop(key, None)
                return ""
            self._playback_link_cache.move_to_end(key)
            return str(value or "")

    def _remember_playback_link(self, key, value):
        if self._session.revoked:
            return
        with self._playback_link_lock:
            if self._session.revoked:
                self._playback_link_cache.clear()
                return
            self._lru_store(
                self._playback_link_cache,
                key,
                (self.clock(), str(value or "")),
                PLAYBACK_LINK_CACHE_LIMIT,
            )

    def _forget_playback_link(self, key):
        with self._playback_link_lock:
            self._playback_link_cache.pop(key, None)

    def cached_playback_url(self, item):
        """Return a direct or cached link without portal I/O or handshake."""

        if self._session.revoked:
            return ""
        if not isinstance(item, ContentItem):
            return ""
        command = str(getattr(item, "portal_command", "") or "").strip()
        if not command:
            return ""
        content_type = str(
            getattr(item, "content_type", "") or ""
        ).lower()
        request_type = (
            "itv"
            if content_type == "live"
            else str(getattr(item, "portal_request_type", "") or "vod")
        )
        series = (
            ""
            if content_type == "live"
            else str(getattr(item, "portal_series", "") or "0")
        )
        static_url = self._static_playable_url(command)
        if not self._requires_create_link(item, static_url):
            return self._with_media_headers(static_url)
        if content_type in ("live", "movie", "series"):
            # A portal-created live link may be single-use.  Once handed to
            # Enigma2 it is no longer safe for a later A-B-A channel zap.
            # Movie and series links follow the same rule: a later explicit
            # open must mint a fresh URL instead of reusing a consumed token.
            return ""
        cache_key = self._playback_cache_key(
            item,
            command,
            request_type,
            series,
        )
        return self._cached_playback_link(cache_key)

    def _playback_url(self, item, force_link=False):
        if not isinstance(item, ContentItem):
            raise ContentError(
                N_("Invalid playback item"),
                ContentError.INVALID_PLAYBACK_ITEM,
            )
        command = self._resolve_item_command(item)
        if not command:
            raise ContentError(
                N_("Stream link unavailable"),
                ContentError.PLAYBACK_URL,
            )
        content_type = str(getattr(item, "content_type", "") or "").lower()
        # Provider-created Stalker playback URLs may be short-lived or
        # single-use.  Enforce freshness at the private boundary as well as
        # the public wrappers so no internal caller can accidentally reuse a
        # consumed live, movie or episode link.
        force_link = bool(
            force_link or content_type in ("live", "movie", "series")
        )
        stored_request_type = str(
            getattr(item, "portal_request_type", "") or ""
        ).strip().lower()
        legacy_movie_dialect = bool(
            content_type == "movie" and not stored_request_type
        )
        request_type = (
            "itv"
            if content_type == "live"
            else stored_request_type or "vod"
        )
        series = (
            ""
            if content_type == "live"
            else str(getattr(item, "portal_series", "") or "0")
        )
        static_url = self._static_playable_url(command)
        if not self._requires_create_link(item, static_url):
            return self._with_media_headers(static_url)
        cache_key = self._playback_cache_key(
            item,
            command,
            request_type,
            series,
        )
        if not force_link:
            cached = self._cached_playback_link(cache_key)
            if cached:
                if legacy_movie_dialect:
                    item.portal_request_type = "vod"
                return cached
        else:
            self._forget_playback_link(cache_key)
        try:
            value = self._create_item_playback_url(
                item,
                command,
                request_type=request_type,
                series=series,
            )
            self._remember_playback_link(cache_key, value)
            if legacy_movie_dialect:
                item.portal_request_type = "vod"
            return value
        except ContentError as error:
            retry_legacy_movie = bool(
                legacy_movie_dialect
                and (
                    error.reason in (
                        ContentError.UNKNOWN,
                        ContentError.UNSUPPORTED_TYPE,
                        ContentError.CONTENT_RESPONSE,
                        ContentError.INVALID_RESPONSE,
                        ContentError.PLAYBACK_URL,
                    )
                    or (
                        error.reason == ContentError.HTTP_ERROR
                        and error.http_status
                        in (400, 404, 405, 406, 415, 422, 501)
                    )
                )
            )
            if retry_legacy_movie:
                movie_cache_key = self._playback_cache_key(
                    item,
                    command,
                    "movie",
                    series,
                )
                if not force_link:
                    cached = self._cached_playback_link(movie_cache_key)
                    if cached:
                        item.portal_request_type = "movie"
                        return cached
                else:
                    self._forget_playback_link(movie_cache_key)
                try:
                    value = self._create_item_playback_url(
                        item,
                        command,
                        request_type="movie",
                        series=series,
                    )
                except ContentError as movie_error:
                    error = movie_error
                else:
                    item.portal_request_type = "movie"
                    self._remember_playback_link(movie_cache_key, value)
                    return value
            if static_url:
                log_event(
                    "portal",
                    "Temporary stream link unavailable; using static channel link",
                    error,
                )
                return self._with_media_headers(static_url)
            log_event("portal", "Portal stream link unavailable", error)
            raise error

    def playback_url(self, item, cancel_event=None, deadline=None):
        with self.request_scope(cancel_event, deadline):
            with self._playback_request_priority():
                force_link = str(
                    getattr(item, "content_type", "") or ""
                ).lower() in (
                    "live", "movie", "series",
                )
                return self._playback_url(item, force_link=force_link)

    def alternate_playback_url(self, item, cancel_event=None, deadline=None):
        """Try the other safe Stalker link strategy after a black preview."""
        with self.request_scope(cancel_event, deadline):
            with self._playback_request_priority():
                return self._alternate_playback_url(item)

    def _alternate_playback_url(self, item):
        if not isinstance(item, ContentItem):
            raise ContentError(
                N_("Invalid playback item"),
                ContentError.INVALID_PLAYBACK_ITEM,
            )
        command = self._resolve_item_command(item)
        if not command:
            raise ContentError(
                N_("Stream link unavailable"),
                ContentError.PLAYBACK_URL,
            )
        content_type = str(getattr(item, "content_type", "") or "").lower()
        request_type = (
            "itv"
            if content_type == "live"
            else str(getattr(item, "portal_request_type", "") or "vod")
        )
        series = (
            ""
            if content_type == "live"
            else str(getattr(item, "portal_series", "") or "0")
        )
        static_url = self._static_playable_url(command)
        cache_key = self._playback_cache_key(
            item,
            command,
            request_type,
            series,
        )
        self._forget_playback_link(cache_key)
        if not self._requires_create_link(item, static_url):
            # Direct URL was the primary strategy; mint one temporary link.
            return self._create_item_playback_url(
                item,
                command,
                request_type=request_type,
                series=series,
            )
        if static_url:
            # Temporary link was primary; retry once with the portal's static
            # external command and the same Enigma2 media request context.
            return self._with_media_headers(static_url)
        return self._create_item_playback_url(
            item,
            command,
            request_type=request_type,
            series=series,
        )

    def refresh_playback_url(self, item, cancel_event=None, deadline=None):
        with self.request_scope(cancel_event, deadline):
            with self._playback_request_priority():
                force_link = str(
                    getattr(item, "content_type", "") or ""
                ).lower() in (
                    "live", "movie", "series",
                )
                try:
                    # Portal-created playback links can be short-lived or
                    # single-use.  Every explicit live/VOD refresh therefore
                    # mints a new link; validated static live commands still
                    # return before the cache branch.
                    return self._playback_url(item, force_link=force_link)
                except ContentError as error:
                    if (
                        error.reason != ContentError.ACCESS_DENIED
                        or bool(
                            getattr(
                                error,
                                "portal_auth_retry_exhausted",
                                False,
                            )
                        )
                    ):
                        raise
                    # Compatibility for a resolver/provider layer that raises
                    # ACCESS_DENIED before entering _request().  Portal errors
                    # that already used their retry are tagged above.
                    self._check_request_limits()
                    self._handshake(force=True)
                    return self._playback_url(item, force_link=True)

    def recover_live_playback_url(self, item, cancel_event=None, deadline=None):
        """Force one fresh MAG session/link after a black live preview."""
        if (
            not isinstance(item, ContentItem)
            or str(getattr(item, "content_type", "") or "").lower() != "live"
        ):
            raise ContentError(
                N_("Invalid playback item"),
                ContentError.INVALID_PLAYBACK_ITEM,
            )
        recovery_deadline = time.monotonic() + LIVE_PLAYBACK_RECOVERY_TIMEOUT
        if deadline is not None:
            recovery_deadline = min(recovery_deadline, float(deadline))
        unused_token, recovery_generation = self._credential_snapshot()
        with self.request_scope(cancel_event, recovery_deadline):
            with self._playback_request_priority():
                # Multiple screens can share this session.  Bound the lock
                # wait, then keep renewal and create_link atomic so a second
                # waiter reuses the result instead of interleaving credentials.
                while True:
                    self._check_request_limits()
                    if self._session_lock.acquire(
                        timeout=min(0.10, self._request_timeout())
                    ):
                        break
                try:
                    self._check_request_limits()
                    # The black-screen path deliberately starts from a fresh
                    # handshake even when the prior HTTP call looked healthy.
                    # Stop after the first denied endpoint and never let the
                    # following create_link perform another auth renewal.
                    current_token, current_generation = (
                        self._credential_snapshot()
                    )
                    recent_failure = self._recent_renewal_failure(
                        recovery_generation
                    )
                    if recent_failure is not None:
                        log_event(
                            "portal",
                            "Stalker live recovery reused recent failure",
                        )
                        raise recent_failure
                    if (
                        current_token
                        and current_generation != recovery_generation
                        and not self._token_is_expired()
                    ):
                        log_event(
                            "portal",
                            "Stalker live recovery reused newer credentials",
                        )
                    else:
                        self._handshake(force=True, retry_access=False)
                    self._ensure_profile(
                        raise_transient=True,
                        raise_access=True,
                    )
                    command = self._resolve_item_command(item)
                    if not command:
                        raise ContentError(
                            N_("Stream link unavailable"),
                            ContentError.PLAYBACK_URL,
                        )
                    static_url = self._static_playable_url(command)
                    self._check_request_limits()
                    try:
                        return self._create_playback_url(
                            command,
                            request_type="itv",
                            series="",
                            disable_ad=bool(
                                getattr(item, "portal_disable_ad", False)
                            ),
                            retry_auth=False,
                        )
                    except ContentError as error:
                        unsupported_link = bool(
                            error.reason in (
                                ContentError.UNKNOWN,
                                ContentError.UNSUPPORTED_TYPE,
                                ContentError.CONTENT_RESPONSE,
                                ContentError.PLAYBACK_URL,
                            )
                            or (
                                error.reason == ContentError.HTTP_ERROR
                                and error.http_status
                                in (400, 404, 405, 406, 415, 422, 501)
                            )
                        )
                        if not static_url or not unsupported_link:
                            raise
                        self._check_request_limits()
                        return self._with_media_headers(static_url)
                finally:
                    self._session_lock.release()

    @staticmethod
    def _epg_rows_for_channel(value, stream_id, channel_ids=None):
        """Extract only one channel from the common Ministra EPG shapes.

        Standard portals commonly return ``data`` as a map whose keys are
        channel ids.  The generic catalogue parser intentionally returns the
        first record list it finds, which is unsafe here because that list may
        belong to a different channel.
        """
        target = str(stream_id or "").strip()
        channel_ids = tuple(channel_ids or (target,))
        group_ids = (target,) + tuple(
            identity for identity in channel_ids if identity != target
        )

        def is_event(entry):
            if not isinstance(entry, dict):
                return False
            has_title = any(
                entry.get(key) not in (None, "")
                for key in ("name", "title", "epg_title")
            )
            has_time = any(
                entry.get(key) not in (None, "")
                for key in (
                    "start_timestamp", "stop_timestamp", "end_timestamp",
                    "start", "end", "time", "time_to", "begin", "stop",
                )
            )
            return has_title and has_time

        def flatten(candidate, depth=0):
            if depth > 7:
                return []
            if isinstance(candidate, (list, tuple)):
                rows = []
                for nested in candidate:
                    rows.extend(flatten(nested, depth + 1))
                return rows
            if not isinstance(candidate, dict):
                return []
            if is_event(candidate):
                return [candidate]
            rows = []
            for nested in candidate.values():
                if isinstance(nested, (dict, list, tuple)):
                    rows.extend(flatten(nested, depth + 1))
            return rows

        def exact_group(candidate, depth=0):
            if depth > 5 or not isinstance(candidate, dict):
                return None
            for identity in group_ids:
                for key, nested in candidate.items():
                    if str(key or "").strip() == identity:
                        return flatten(nested)
            for wrapper in (
                "js", "data", "epg", "events", "items", "channels", "results",
            ):
                nested = candidate.get(wrapper)
                if isinstance(nested, dict):
                    matched = exact_group(nested, depth + 1)
                    if matched is not None:
                        return matched
            # A map of channel ids to event lists contains no schedule for
            # this channel if none of its keys matched.  Never flatten a
            # different channel's anonymous events into the selected channel.
            if candidate and all(
                isinstance(nested, (list, tuple))
                for nested in candidate.values()
            ) and not any(
                key in candidate
                for key in ("js", "data", "epg", "events", "items", "channels", "results")
            ):
                return []
            return None

        rows = exact_group(value)
        if rows is None:
            rows = flatten(value)

        # Filter flat responses when the events themselves carry a channel
        # identifier.  If ids are present but none matches, returning empty is
        # safer than displaying another channel's programme.
        identified = []
        for entry in rows:
            channel_id = str(
                entry.get("ch_id")
                or entry.get("channel_id")
                or entry.get("stream_id")
                or ""
            ).strip()
            if channel_id:
                identified.append((channel_id, entry))
        if identified:
            return [
                entry
                for channel_id, entry in identified
                if channel_id in channel_ids
            ]
        return rows

    @staticmethod
    def _epg_timestamp(value):
        """Read portal timestamps without discarding decimal values or zones."""
        raw = str(value or "").strip()
        if not raw or len(raw) > 64:
            return 0
        try:
            if re.fullmatch(r"[0-9]{1,13}(?:\.[0-9]{1,6})?", raw):
                stamp = int(float(raw))
                if stamp > 100000000000:
                    stamp //= 1000
            elif re.match(r"^[0-9]{4}-[0-9]{2}-[0-9]{2}[ T]", raw):
                stamp = int(datetime.datetime.fromisoformat(
                    raw.replace("Z", "+00:00")
                ).timestamp())
            else:
                return 0
            return stamp if 0 < stamp < 4102444800 else 0
        except (ValueError, TypeError, OverflowError, OSError):
            return 0

    @staticmethod
    def _epg_times(entry):
        """Keep a complete time pair in the same clock basis.

        MAG time/time_to values follow the session timezone. Prefer that
        complete pair, as eStalker does, over mixing it with unrelated Unix
        fields. An explicit numeric pair remains the fallback.
        """
        timestamp = StalkerPortalClient._epg_timestamp
        maximum = STALKER_EPG_MAX_PROGRAMME_SECONDS
        for start_key, end_key in (
            ("time", "time_to"), ("start", "end"), ("begin", "stop"),
            ("start_timestamp", "stop_timestamp"),
            ("start_timestamp", "end_timestamp"),
        ):
            start, end = timestamp(entry.get(start_key)), timestamp(entry.get(end_key))
            if start and 0 < end - start <= maximum:
                return start, end
        start_values = [entry.get(key) for key in
                        ("time", "start", "begin", "start_timestamp")]
        end_values = [entry.get(key) for key in
                      ("time_to", "end", "stop", "stop_timestamp", "end_timestamp")]
        start = next((timestamp(value) for value in start_values if timestamp(value)), 0)
        end = next((timestamp(value) for value in end_values if timestamp(value)), 0)
        if start and not 0 < end - start <= maximum:
            end = 0
        if start and not end:
            # Native Stalker duration is in seconds. Never use clipped
            # display_duration, or turn negative/malformed durations positive.
            raw_duration = str(entry.get("duration") or "").strip()
            if re.fullmatch(r"[0-9]{1,6}(?:\.[0-9]{1,6})?", raw_duration):
                duration = int(float(raw_duration))
                if 0 < duration <= maximum:
                    end = start + duration
        if start and not end:
            for value in end_values + [entry.get("t_time_to")]:
                raw = str(value or "").strip()
                match = re.fullmatch(r"([0-9]{1,2}):([0-9]{2})(?::([0-9]{2}))?", raw)
                if not match:
                    continue
                try:
                    base = datetime.datetime.fromtimestamp(start)
                    stop = base.replace(hour=int(match.group(1)), minute=int(match.group(2)),
                                        second=int(match.group(3) or 0), microsecond=0)
                    if stop <= base:
                        stop += datetime.timedelta(days=1)
                    candidate = int(stop.timestamp())
                    if 0 < candidate - start <= maximum:
                        end = candidate
                        break
                except (ValueError, OverflowError, OSError):
                    continue
        # Retain the old clock-only display fallback when the provider has
        # supplied no usable date. Do not invent a date or a current programme.
        return (start or next((v for v in start_values if v), ""),
                end or (next((v for v in end_values if v), "") if not start else ""))

    @staticmethod
    def _epg_events(value, target, limit, channel_ids=None):
        events = []
        for entry in StalkerPortalClient._epg_rows_for_channel(
            value, target, channel_ids=channel_ids
        ):
            start, end = StalkerPortalClient._epg_times(entry)
            events.append(
                EPGEvent(
                    (
                        entry.get("name")
                        or entry.get("title")
                        or entry.get("epg_title")
                    ),
                    start,
                    end,
                    now_playing=str(
                        entry.get("now_playing")
                        or entry.get("is_now")
                        or entry.get("current")
                        or ""
                    ).strip().lower() in ("1", "true", "yes", "on"),
                    description=(
                        entry.get("descr")
                        or entry.get("description")
                        or entry.get("plot")
                    ),
                )
            )
        # A started event without an end used to be dropped in favour of the
        # next event. Its next distinct start is a bounded end-time fallback.
        starts = sorted(set(event.start_timestamp for event in events
                            if event.start_timestamp))
        now = int(time.time())
        for event in events:
            if event.start_timestamp and not event.end_timestamp:
                following = next((stamp for stamp in starts if stamp > event.start_timestamp), 0)
                if 0 < following - event.start_timestamp <= STALKER_EPG_MAX_PROGRAMME_SECONDS:
                    event.end_timestamp = following
                    event.end = time.strftime("%H:%M", time.localtime(following))
            if (event.start_timestamp and event.start_timestamp > now) or (
                event.end_timestamp and event.end_timestamp <= now
            ):
                event.now_playing = False
        return _select_current_epg_window(events, limit)

    @staticmethod
    def _epg_has_current(events):
        now = int(time.time())
        for event in events:
            start, end = event.start_timestamp, event.end_timestamp
            if (start and start > now) or (end and end <= now):
                continue
            if (start and end) or event.now_playing or not start:
                return True
        return False

    @staticmethod
    def _epg_parameter_name(identity_kind):
        if identity_kind in ("channel_id", "epg_id", "xmltv_id"):
            return identity_kind
        return "ch_id"

    def _epg_candidates(self, identities):
        # Short EPG takes a channel id, never an epg_id/xmltv_id.  Prefer the
        # catalogue's id even when metadata was restored in an older order.
        channel_kinds = ("id", "ch_id", "channel_id", "stream_id")
        channels = [
            kind for kind in channel_kinds
            if self._identity_value(identities, kind)
        ]
        if not channels:
            return ()
        primary = channels[0]
        result = [
            ("get_short_epg", "ch_id", primary),
            ("get_epg_info", self._epg_parameter_name(primary), primary),
        ]
        result.extend(
            ("get_short_epg", "ch_id", kind) for kind in channels[1:]
        )
        result.extend(
            ("get_epg_info", kind, kind)
            for kind in ("epg_id", "xmltv_id")
            if self._identity_value(identities, kind)
        )
        return tuple(result[:EPG_COMPATIBILITY_MAX_ATTEMPTS])

    @staticmethod
    def _identity_value(identities, identity_kind):
        for kind, value in identities or ():
            if kind == identity_kind:
                return value
        # A policy learned on another channel must not reuse an unrelated id.
        return ""

    def _request_epg_policy(self, policy, identities, limit):
        action, parameter_name, identity_kind = policy
        identity = self._identity_value(identities, identity_kind)
        if not identity:
            return None, []
        if action == "get_short_epg":
            parameters = [
                ("ch_id", identity),
                ("size", str(max(STALKER_SHORT_EPG_MIN_EVENTS, limit))),
            ]
        else:
            parameters = [(parameter_name, identity), ("period", "5")]
            if parameter_name == "ch_id":
                parameters.append(("channel_id", identity))
            elif parameter_name == "channel_id":
                parameters.append(("ch_id", identity))
        try:
            value = self._request("itv", action, tuple(parameters))
        except ContentError as error:
            if error.reason in REQUEST_ABORT_REASONS or error.reason in (
                ContentError.ACCESS_DENIED,
                ContentError.RATE_LIMITED,
                ContentError.CONNECTION,
                ContentError.RESPONSE_TOO_LARGE,
            ):
                raise
            return None, []
        # Some get_epg_info variants accept a guide id but label their rows
        # with the real channel id.  Match only this channel's known aliases.
        channel_ids = tuple(
            channel_id for kind, channel_id in identities
            if kind in ("id", "ch_id", "channel_id", "stream_id")
        )
        return value, self._epg_events(
            value, identity, limit, channel_ids=channel_ids
        )

    def _log_epg_diagnostic(self, outcome, policy=None):
        if self._epg_diagnostic_logged in ("available", outcome):
            return
        self._epg_diagnostic_logged = outcome
        method = "none"
        identity = "none"
        if policy:
            method = str(policy[0] or "none")
            identity = str(policy[2] or "none")
        try:
            log_event(
                "portal",
                "Live EPG outcome={} method={} identity={}".format(
                    outcome, method, identity
                ),
            )
        except Exception:
            pass

    def _remember_empty_epg(self, stream_id):
        self._lru_store(
            self._epg_empty_cache,
            stream_id,
            self.clock(),
            STALKER_EPG_EMPTY_CACHE_LIMIT,
        )

    def load_epg(self, stream_id, limit=2):
        stream_id = str(stream_id or "").strip()
        if not stream_id:
            return []
        try:
            limit = max(1, min(12, int(limit)))
        except (TypeError, ValueError):
            limit = 2
        empty_cached_at = self._epg_empty_cache.get(stream_id)
        if empty_cached_at is not None:
            if self.clock() - empty_cached_at < STALKER_EPG_EMPTY_TTL_SECONDS:
                self._epg_empty_cache.move_to_end(stream_id)
                return []
            self._epg_empty_cache.pop(stream_id, None)
        meta = dict(self._live_channel_meta.get(stream_id, {}))
        identities = tuple(meta.get("identities") or ())
        if not identities:
            identities = (("stream_id", stream_id),)

        upcoming = []
        upcoming_policy = None
        for embedded in meta.get("embedded") or ():
            for unused_kind, identity in identities:
                events = self._epg_events(embedded, identity, limit)
                if events and self._epg_has_current(events):
                    self._epg_empty_cache.pop(stream_id, None)
                    policy = ("embedded", "none", unused_kind)
                    self._log_epg_diagnostic("available", policy)
                    return events
                if events and not upcoming:
                    upcoming = events

        policies = list(self._epg_candidates(identities))
        learned = self._epg_policy
        if learned is not None and self._identity_value(identities, learned[2]):
            if learned in policies:
                policies.remove(learned)
            policies.insert(0, learned)
        # Only a non-empty programme response teaches a route.  An empty
        # channel may try the remaining bounded alternatives, and never
        # disables EPG discovery for other channels in the session.
        followup_budget = None
        for policy in policies[:EPG_COMPATIBILITY_MAX_ATTEMPTS]:
            if followup_budget == 0:
                break
            if followup_budget is not None:
                followup_budget -= 1
            try:
                unused_value, events = self._request_epg_policy(
                    policy, identities, limit
                )
            except ContentError as error:
                if upcoming and error.reason not in REQUEST_ABORT_REASONS:
                    break
                raise
            if not events:
                continue
            if not self._epg_has_current(events):
                if not upcoming:
                    upcoming = events
                    upcoming_policy = policy
                # A future-only reply is incomplete. Try at most one further
                # policy for NOW, preserving NEXT if that query is empty.
                if followup_budget is None:
                    followup_budget = 1
                continue
            self._epg_empty_cache.pop(stream_id, None)
            self._epg_policy = policy
            self._log_epg_diagnostic("available", policy)
            if upcoming:
                seen = set()
                merged = []
                for event in list(events) + list(upcoming):
                    key = (event.start_timestamp, event.end_timestamp, event.title)
                    if key not in seen:
                        seen.add(key)
                        merged.append(event)
                return _select_current_epg_window(merged, limit)
            return events
        if upcoming:
            self._epg_empty_cache.pop(stream_id, None)
            if upcoming_policy is not None:
                self._epg_policy = upcoming_policy
            self._log_epg_diagnostic("upcoming", upcoming_policy)
            return upcoming[:limit]
        self._log_epg_diagnostic("empty", self._epg_policy)
        self._remember_empty_epg(stream_id)
        return []


def check_portal_health(
    account,
    timeout=DEFAULT_TIMEOUT,
    opener=None,
    clock=None,
    cancel_event=None,
    summary_only=False,
):
    clock = clock or time.monotonic
    started = clock()
    try:
        client = StalkerPortalClient(
            account,
            timeout=timeout,
            opener=opener,
            clock=clock,
        )
        # Health discovery performs multiple portal calls (handshake, optional
        # profile and category probe).  Keep them within one transport budget
        # so sequential endpoint timeouts cannot outlive the UI health job.
        # Request scopes use the real monotonic clock; ``clock`` remains the
        # injectable health/cache clock and may have a different epoch.
        deadline = time.monotonic() + client.timeout
        profile_generation = None
        with client.request_scope(
            cancel_event=cancel_event,
            deadline=deadline,
            timeout=client.timeout,
        ):
            client._handshake()
            profile_verified = False
            refresh_profile = getattr(
                client,
                "refresh_profile_for_health",
                None,
            )
            if callable(refresh_profile):
                profile_generation = refresh_profile()
                profile_snapshot = getattr(
                    client,
                    "profile_connection_snapshot",
                    None,
                )
                if callable(profile_snapshot):
                    snapshot = profile_snapshot()
                    profile_verified = bool(
                        profile_generation is not None
                        and isinstance(snapshot, (tuple, list))
                        and len(snapshot) == 3
                        and snapshot[0] != profile_generation
                    )
            # A successful profile response already authenticates the account
            # and carries the requested summary fields. Legacy portals that do
            # not expose get_profile still receive the established category
            # probe so a handshake token alone is never treated as proof.
            if not summary_only or not profile_verified:
                client.load_categories("live")
        # Use a separate optional scope: request_scope checks its deadline on
        # exit, so nesting this in the mandatory health scope would turn an
        # account-info timeout into a false portal outage.
        expiry = N_("Server does not report expiry")
        expiry_for_health = getattr(client, "expiry_for_health", None)
        if callable(expiry_for_health):
            try:
                expiry = expiry_for_health(
                    profile_generation,
                    deadline,
                    cancel_event=cancel_event,
                )
            except TypeError:
                # Preserve integrations that implement the older optional
                # two-argument summary hook.
                expiry = expiry_for_health(profile_generation, deadline)
        latency_ms = max(1, int(round((clock() - started) * 1000)))
        active_connections = None
        max_connections = None
        limits = None
        profile_snapshot = getattr(
            client,
            "profile_connection_snapshot",
            None,
        )
        if profile_generation is not None and callable(profile_snapshot):
            snapshot = profile_snapshot()
            if isinstance(snapshot, (tuple, list)) and len(snapshot) == 3:
                current_generation, active, maximum = snapshot
                if current_generation != profile_generation:
                    limits = (active, maximum)
        elif profile_generation is None:
            connection_limits = getattr(
                client,
                "profile_connection_limits",
                None,
            )
            if callable(connection_limits):
                limits = connection_limits()
        if isinstance(limits, (tuple, list)) and len(limits) == 2:
            active_connections, max_connections = limits
        at_limit = bool(
            active_connections is not None
            and max_connections is not None
            and max_connections > 0
            and active_connections >= max_connections
        )
        health = ServerHealth(
            N_("BUSY") if at_limit else "ONLINE",
            latency_ms=latency_ms,
            account_status=N_("Active"),
            expiry=expiry,
            active_connections=active_connections,
            max_connections=max_connections,
            detail=(
                N_("Connection limit reached")
                if at_limit
                else N_("Portal connection successful")
            ),
            protocol="STALKER",
            output_format="Portal",
            source_type=PORTAL_PROVIDER,
        )
        health.content_client = client
        return health
    except (ContentError, ValueError, TypeError) as error:
        log_event("portal", "Portal health check failed", error)
        latency_ms = max(1, int(round((clock() - started) * 1000)))
        access_denied = bool(
            isinstance(error, ContentError)
            and error.reason == ContentError.ACCESS_DENIED
        )
        return ServerHealth(
            N_("INVALID") if access_denied else N_("ERROR"),
            latency_ms=latency_ms,
            account_status=N_("Invalid") if access_denied else "",
            detail=str(error),
            protocol="STALKER",
            output_format="Portal",
            source_type=PORTAL_PROVIDER,
        )


def check_account_health(account):
    if is_portal_account(account):
        return check_portal_health(account)
    from .playlist import check_server_health

    return check_server_health(account)


def delete_any_account(account):
    if is_portal_account(account):
        return delete_portal_account(account)
    from .playlist import delete_account

    return delete_account(account)

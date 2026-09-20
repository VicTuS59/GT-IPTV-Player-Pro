# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import datetime
import hashlib
import math
import os
import re
import stat
import struct
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict, deque

from urllib.parse import urlsplit

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
try:
    from Components.VideoWindow import VideoWindow
except ImportError:  # Minimal test images may not ship the PIG component.
    VideoWindow = None
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from enigma import ePoint, eSize, eTimer, getDesktop, iServiceInformation
try:
    from enigma import iPlayableService as _PlayableServiceEvents
except ImportError:
    _PlayableServiceEvents = None

from . import PLUGIN_VERSION
from .content import ContentItem, CONTENT_LABELS, ContentError, XtreamContentClient
from .diagnostics import log_event, redact
from .i18n import (
    N_,
    _,
    localized_date_text,
    localized_language_name,
    localized_upper,
    metadata_language,
)
from .live_recovery import media_snapshot as _decoder_media_snapshot
from .background import attach_background, attach_pixmap
from .category_filters import (
    category_filter_ids,
)
from .category_preferences import (
    MAX_CATEGORIES_PER_SOURCE,
    CategoryPreferences,
    LiveCategoryPreferenceStore,
    arrange_categories,
    category_preferences_path,
)
from .category_visibility import (
    category_filter_active,
    category_visibility_ids,
    filter_hidden_category_items,
    hidden_category_match_ids,
)
from .category_lock import CategoryEditLockStore
from .category_lock_ui import (
    GTCategoryCodeInputScreen,
    RECOVERY_REQUEST,
)
from .metadata import MetadataError, TMDbMetadataClient
from .favorites import FavoriteStore, favorite_key
from .network_security import (
    NetworkSecurityError,
    fetch_public_url,
    validate_public_url_syntax,
)
from .playback import (
    PlaybackResumeStore,
    resume_account_scope,
    resume_key,
    should_save_resume,
)
from .paths import plugin_path
from .portal_identity import safe_composite_id
from .remote_footer import (
    decorate_remote_footer,
    footer_item,
    install_remote_footer,
    set_remote_footer,
)
from .scrollbar import (
    hide_scrollbar,
    update_horizontal_scrollbar,
    update_scrollbar,
)
from .smooth_scroll import GTSmoothScrollLabel
from .settings import (
    available_service_types,
    load_player_settings,
    service_engine_label,
)
from .subtitles import MovieSubtitleController
from .typography import (
    clean_dynamic_text,
    ellipsize_dynamic_text,
    fit_dynamic_text,
    font_px,
    scale_skin_fonts,
    set_static_text,
    set_text_if_changed,
    visible_count,
)
from .weather import (
    DEFAULT_CITY,
    WeatherError,
    WeatherService,
    day_name,
    load_weather_settings,
    weather_description,
    weather_icon_name,
)


def content_client_for(account):
    """Create the content adapter for a unified IPTV source."""
    source_type = str(
        getattr(account, "source_type", "xtream") or "xtream"
    ).lower()
    if source_type == "stalker":
        from .stalker import StalkerPortalClient

        return StalkerPortalClient(account)
    if source_type == "m3u":
        from .m3u import M3UContentClient

        # Leave a small margin before the 30-second Enigma2 list-worker
        # deadline so completion cannot lose a race with the GUI poll timer.
        return M3UContentClient(account, timeout=28)
    return XtreamContentClient(account)


def _client_supports(client, capability):
    checker = getattr(client, "supports", None)
    if callable(checker):
        try:
            return bool(checker(capability))
        except Exception:
            return False
    return True


def _validated_content_type(client, content_type):
    value = XtreamContentClient._validate_type(content_type)
    if not _client_supports(client, value):
        raise ContentError(
            N_("This source does not support the selected content type."),
            ContentError.UNSUPPORTED_TYPE,
        )
    return value


def _is_m3u_client(client):
    return str(getattr(client, "source_type", "xtream") or "xtream").lower() == "m3u"


def _is_stalker_client(client):
    return (
        str(getattr(client, "source_type", "xtream") or "xtream").lower()
        == "stalker"
    )


def _series_provider_id(client, value):
    """Keep Xtream numeric-only while admitting bounded Stalker composites."""
    raw_value = str(value or "")
    numeric_value = raw_value.strip()
    if numeric_value.isdigit():
        return numeric_value
    if _is_stalker_client(client):
        return safe_composite_id(raw_value)
    return ""


def _debug_identifier(value):
    """Return a useful diagnostic identity without exposing composite IDs."""
    raw_value = str(value or "")
    candidate = safe_composite_id(raw_value)
    if not candidate:
        candidate = safe_composite_id(raw_value.strip())
    if candidate:
        digest = hashlib.sha256(raw_value.encode("utf-8", "replace")).hexdigest()
        return "portal-composite-{}".format(digest[:10])
    return raw_value


INFO_TIMEOUT_MS = 3000
NAVIGATION_DEBOUNCE_MS = 250
MAX_AUDIO_TRACKS = 64
BLUE_LONG_SHORT_SUPPRESS_SECONDS = 1.5
BLUE_LONG_REPEAT_SUPPRESS_SECONDS = 0.5
LIVE_RECONNECT_RELEASE_DELAY_MS = 1000
# Generic fullscreen timers remain network-silent.  A separate, bounded
# completion path fills missing EPG/picon data and refreshes portal EPG after
# CH+/CH-; channel start and zapping never wait for that optional work.
LIVE_PICON_DOWNLOAD_TIMEOUT = 2
LIVE_EPG_CACHE_TTL_SECONDS = 60 * 60
# Empty/error results are deliberately not cached. A transient provider
# failure must not pin "EPG information unavailable" until the next hour.
LIVE_EPG_EMPTY_TTL_SECONDS = 0
LIVE_EPG_FETCH_LIMIT = 8
LIVE_EPG_CACHE_MAX_ENTRIES = 128
# The visible live page may move repeatedly while LEFT/RIGHT is held.  Provider
# work begins only after the final page has rested for this short interval.
LIVE_DETAIL_DWELL_MS = 100
STALKER_LIVE_DETAIL_DWELL_MS = 350
LIVE_DETAIL_CACHE_TTL_SECONDS = 60 * 60
LIVE_DETAIL_EMPTY_RETRY_SECONDS = 30
LIVE_DETAIL_PREFETCH_DELAY_MS = 450
LIVE_DETAIL_PREFETCH_CHANNELS = 4
LIVE_DETAIL_EPG_TIMEOUT_SECONDS = 10
LIVE_DETAIL_PREFETCH_TIMEOUT_SECONDS = 12
LIVE_DETAIL_PROGRESS_INTERVAL_MS = 10000
LIVE_METADATA_INITIAL_DELAY_MS = 550
LIVE_INFO_METADATA_INITIAL_DELAY_MS = 450
LIVE_METADATA_RETRY_DELAY_MS = 250
LIVE_METADATA_MAX_SAMPLES = 4
LIVE_INFO_READY_MIN_SAMPLES = 2
LIVE_INFO_SETTLE_MS = 700
LIVE_INFO_FALLBACK_MS = 900
STALKER_LIVE_EPG_QUIET_MS = 750
# Logo downloads keep their existing preview delay; EPG takes priority.
STALKER_LIVE_PICON_QUIET_MS = 2500
LIVE_INFO_POLL_MS = 100
LIVE_INFO_EPG_TIMEOUT_SECONDS = 5
# A physical CHANNEL key can be reported twice by a few image/action-map
# combinations.  Keep a short bounce guard, but do not make a deliberate
# second press feel ignored.  Link resolution starts as soon as the
# accepted target is known; this debounce only coalesces the final selection.
CHANNEL_SWITCH_DEBOUNCE_MS = 110
CHANNEL_KEY_DUPLICATE_GUARD_MS = 90
CHANNEL_LINK_POLL_MS = 25
CHANNEL_LINK_TIMEOUT_SECONDS = 5
SEEK_VERIFY_DELAY_MS = 650
SEEK_VERIFY_MAX_CHECKS = 2
RESUME_INITIAL_DELAY_MS = 700
RESUME_RETRY_DELAY_MS = 300
RESUME_MAX_ATTEMPTS = 30
RESUME_4097_STABLE_SAMPLES = 3
RESUME_LENGTH_STABILITY_TOLERANCE_SECONDS = 2
ARCHIVE_STARTUP_SECONDS = 12.0
ARCHIVE_STARTUP_SETTLE_SECONDS = 0.75
ARCHIVE_STARTUP_ACQUIRE_SECONDS = 3.0
ARCHIVE_STARTUP_SAMPLE_MS = 250
ARCHIVE_PROVIDER_RETRY_SECONDS = 6.0
ARCHIVE_DECODER_RELEASE_MS = 500
ARCHIVE_STARTUP_OVERALL_SECONDS = 32.0
ARCHIVE_PORTAL_KEEPALIVE_SECONDS = 30
ARCHIVE_PORTAL_REQUEST_SECONDS = 7
ARCHIVE_DECODER_TRANSITION_TICKS = 90000 * 10
ARCHIVE_POSITION_NEAR_ZERO_TICKS = 90000
MAX_PICON_BYTES = 4 * 1024 * 1024
MAX_PICON_DIMENSION = 4096
MAX_PICON_PIXELS = 8 * 1000 * 1000
CINEMATIC_MIN_HERO_WIDTH = 600
CINEMATIC_MIN_HERO_HEIGHT = 300
LIVE_PICON_MAX_DIMENSION = 2048
LIVE_PICON_MAX_PIXELS = 4 * 1000 * 1000
LIVE_PICON_HEADER_BYTES = 256 * 1024
LIVE_PICON_VALIDATION_CACHE_MAX_ENTRIES = 512
PICON_CACHE_DIR = "/tmp/gtiptvplayer-picons"
PICON_DOWNLOAD_TIMEOUT = 8
PICON_FAST_FALLBACK_TIMEOUT = 3
PICON_DOWNLOAD_WORKERS = 4
BACKGROUND_POSTER_WORKERS = 2
CONTINUE_POSTER_WORKERS = 2
DETAIL_INFO_CACHE_MAX_ENTRIES = 128
PICON_CACHE_MAX_BYTES = 32 * 1024 * 1024
PICON_CACHE_MAX_FILES = 300
PICON_CACHE_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
PICON_FAILURE_TTL_SECONDS = 300
PICON_FAILURE_MAX_ENTRIES = 512
PICON_CLEANUP_INTERVAL_SECONDS = 300
PICON_PATH_CACHE_TTL_SECONDS = 60
PICON_PATH_CACHE_MAX_ENTRIES = 256
PICON_CACHE_SUFFIXES = (".jpg", ".png", ".gif", ".bmp")
_PICON_DOWNLOAD_LOCKS = {}
_PICON_DOWNLOAD_LOCKS_GUARD = threading.Lock()
_PICON_DOWNLOAD_SLOTS = threading.BoundedSemaphore(PICON_DOWNLOAD_WORKERS)
_PICON_FAILURES = {}
_PICON_FAILURES_GUARD = threading.Lock()
_PICON_CLEANUP_GUARD = threading.Lock()
_PICON_LAST_CLEANUP = [0.0]
_PICON_PATH_CACHE = {}
_PICON_PATH_CACHE_GUARD = threading.Lock()
_PICON_VALIDATION_CACHE = OrderedDict()
_PICON_VALIDATION_CACHE_GUARD = threading.Lock()
_EPG_STATE_GUARD = threading.Lock()
WORKER_LIST_TIMEOUT_SECONDS = 30
MOVIE_CATEGORY_TIMEOUT_SECONDS = 15
MOVIE_SEARCH_TIMEOUT_SECONDS = 20
WORKER_DETAIL_TIMEOUT_SECONDS = 18
WORKER_POSTER_TIMEOUT_SECONDS = 16
CINEMATIC_BACKDROP_TIMEOUT_SECONDS = 8
CINEMATIC_CANDIDATE_TIMEOUT_SECONDS = 3
WORKER_WEATHER_TIMEOUT_SECONDS = 10
WORKER_PLAYBACK_LINK_TIMEOUT_SECONDS = 16
SERIES_PROGRESSIVE_SCAN_DELAY_MS = 120
LIVE_STREAM_PAGE_SIZE = 12
LIVE_PREVIEW_LINK_POLL_MS = 50
LIVE_PREVIEW_VIDEO_INITIAL_MS = 40
LIVE_PREVIEW_VIDEO_RETRY_MS = 180
LIVE_PREVIEW_VIDEO_ERROR_CHECKS = 18
LIVE_PREVIEW_VIDEO_MAX_CHECKS = 50
EPISODE_PAGE_SIZE = 10
MOVIE_CATEGORY_PAGE_SIZE = 10
MOVIE_RAIL_PAGE_SIZE = 5
ALL_MOVIES_SEARCH_PAGE_SIZE = 10
M3U_SEARCH_RESULT_LIMIT = 500
SERIES_CATEGORY_PAGE_SIZE = 8
CINEMATIC_CATEGORY_PAGE_SIZE = 12
CINEMATIC_CATEGORY_ROW_HEIGHT = 72
SERIES_GRID_COLUMNS = 4
SERIES_GRID_ROWS = 2
SERIES_GRID_PAGE_SIZE = SERIES_GRID_COLUMNS * SERIES_GRID_ROWS
SERIES_RAIL_PAGE_SIZE = SERIES_GRID_PAGE_SIZE
SERIES_SEARCH_RESULT_LIMIT = 200
LIVE_FULLSCREEN_RESULT_KIND = "gt-live-fullscreen-v1"


def _rating_label(value):
    """Return a display label only for a finite, positive numeric rating."""
    text = str(value or "").strip()
    try:
        number = float(text.split("/", 1)[0].strip().replace(",", "."))
    except (TypeError, ValueError, OverflowError):
        number = 0.0
    if not math.isfinite(number) or number <= 0:
        return _("NO RATING")
    return "★ {}".format(text)


def _year_not_in_title(title, year):
    """Suppress a metadata year already present in the visible title."""
    value = str(year or "").strip()
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", value)
    if match is None:
        return value
    candidate = match.group(1)
    if re.search(r"(?<!\d){}(?!\d)".format(candidate), str(title or "")):
        return ""
    return value


def _detail_cache_get(cache, cache_lock, key):
    """Read one provider detail as an LRU entry under the poster-worker lock."""
    with cache_lock:
        if key not in cache:
            return None
        value = cache.pop(key)
        cache[key] = value
        return value


def _detail_cache_put(cache, cache_lock, key, value):
    """Bound screen-lifetime provider details without changing their payload."""
    with cache_lock:
        cache.pop(key, None)
        cache[key] = value
        while len(cache) > DETAIL_INFO_CACHE_MAX_ENTRIES:
            cache.popitem(last=False)


def _live_detail_dwell_ms(client):
    if _is_stalker_client(client):
        return STALKER_LIVE_DETAIL_DWELL_MS
    return LIVE_DETAIL_DWELL_MS


def _epg_event_starts_in_future(event, now=None):
    """Return whether an event is upcoming rather than currently airing."""
    try:
        start = int(getattr(event, "start_timestamp", 0) or 0)
        current = int(time.time() if now is None else now)
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(start and start > current)


def _allow_live_detail_epg_prefetch(client):
    """Keep speculative Stalker EPG calls out of the playback request lane."""
    return not _is_stalker_client(client)


PLUGIN_PATH = plugin_path()
WEATHER_ICON_DIR = os.path.join(PLUGIN_PATH, "skin", "images", "weather")
APP_BACKGROUND_PATH = os.path.join(
    PLUGIN_PATH,
    "skin",
    "images",
    "global-neon-v0912.png",
)
CINEMATIC_SCRIM_PATH = os.path.join(
    PLUGIN_PATH,
    "skin",
    "images",
    "cinematic-scrim-r112.png",
)
CINEMATIC_FALLBACK_SHADOW_PATH = os.path.join(
    PLUGIN_PATH,
    "skin",
    "images",
    "cinematic-scrim-r110.png",
)
FAVORITE_STAR_PATH = os.path.join(
    PLUGIN_PATH,
    "skin",
    "images",
    "favorite-star-v15.png",
)

ASYNC_FOOTER_ITEMS = (
    footer_item("up_down", "select"),
    footer_item("left_right", "page"),
    footer_item("ok", "open"),
    footer_item("green", "refresh"),
    footer_item("exit", "back"),
)
CONTINUE_FOOTER_ITEMS = (
    footer_item("arrows", "navigate", 1.15),
    footer_item("ok", "continue"),
    footer_item("red", "remove"),
    footer_item("green", "refresh"),
    footer_item("exit", "back"),
)
FAVORITES_FOOTER_ITEMS = (
    footer_item("arrows", "navigate", 1.05),
    footer_item("channel", "page", 1.00),
    footer_item("ok", "open", 0.78),
    footer_item("red", "remove_favorite", 1.05),
    footer_item("yellow", "move", 0.82),
    footer_item("green", "refresh", 0.82),
    footer_item("blue", "filter", 0.78),
    footer_item("exit", "back", 0.74),
)
FAVORITES_MOVE_FOOTER_ITEMS = (
    footer_item("arrows", "move", 1.05),
    footer_item("channel", "move_page", 1.00),
    footer_item("ok", "save", 0.78),
    footer_item("", "", 1.05),
    footer_item("", "", 0.82),
    footer_item("", "", 0.82),
    footer_item("", "", 0.78),
    footer_item("exit", "cancel", 0.74),
)
LIVE_CATEGORY_FOOTER_ITEMS = (
    footer_item("up_down", "select"),
    footer_item("left_right", "page"),
    footer_item("ok", "channel_list", 1.25),
    footer_item("green", "refresh"),
    footer_item("blue", "edit", 0.88),
    footer_item("menu", "settings"),
    footer_item("exit", "back", 0.8),
)
M3U_LIVE_CATEGORY_FOOTER_ITEMS = (
    footer_item("up_down", "select"),
    footer_item("left_right", "page"),
    footer_item("ok", "channel_list", 1.20),
    footer_item("green", "refresh"),
    footer_item("yellow", "edit", 0.88),
    footer_item("blue", "search", 0.90),
    footer_item("menu", "settings"),
    footer_item("exit", "back", 0.8),
)
MOVIE_FOOTER_ITEMS = (
    footer_item("arrows", "navigate", 1.10),
    footer_item("channel", "page", 0.95),
    footer_item("ok", "play", 0.82),
    footer_item("red", "favorite", 0.90),
    footer_item("green", "refresh", 0.85),
    footer_item("yellow", "edit", 0.85),
    footer_item("blue", "search", 0.90),
    footer_item("exit", "back", 0.86),
)
ALL_MOVIES_SEARCH_FOOTER_ITEMS = (
    footer_item("arrows", "navigate", 1.10),
    footer_item("ok", "play", 0.86),
    footer_item("red", "favorite", 0.92),
    footer_item("green", "refresh", 0.88),
    footer_item("blue", "search", 0.88),
    footer_item("exit", "back", 0.86),
)
SERIES_FOOTER_ITEMS = (
    footer_item("arrows", "navigate", 1.15),
    footer_item("channel", "page", 0.95),
    footer_item("ok", "open"),
    footer_item("red", "favorite"),
    footer_item("green", "continue"),
    footer_item("yellow", "edit"),
    footer_item("blue", "search"),
    footer_item("exit", "back"),
)
EPISODE_FOOTER_ITEMS = (
    footer_item("arrows", "navigate", 1.15),
    footer_item("channel", "page", 0.95),
    footer_item("ok", "play"),
    footer_item("red", "favorite"),
    footer_item("green", "refresh"),
    footer_item("exit", "back"),
)
LIVE_STREAM_FOOTER_ITEMS = (
    footer_item("up_down", "select"),
    footer_item("left_right", "page"),
    footer_item("ok", "play", 1.05),
    footer_item("red", "favorite", 0.95),
    footer_item("green", "refresh"),
    footer_item("exit", "back"),
)
M3U_LIVE_STREAM_FOOTER_ITEMS = (
    footer_item("up_down", "select"),
    footer_item("left_right", "page"),
    footer_item("ok", "play", 1.00),
    footer_item("red", "favorite", 0.90),
    footer_item("green", "refresh"),
    footer_item("blue", "search", 0.85),
    footer_item("exit", "back"),
)
M3U_SEARCH_FOOTER_ITEMS = (
    footer_item("up_down", "select"),
    footer_item("left_right", "page"),
    footer_item("ok", "fullscreen", 1.20),
    footer_item("red", "favorite", 0.90),
    footer_item("blue", "search", 0.85),
    footer_item("exit", "back"),
)
CATEGORY_MANAGER_FOOTER_ITEMS = (
    footer_item("ok", "visibility", 0.90),
    footer_item("red", "mark", 0.80),
    footer_item("yellow", "move", 0.85),
    footer_item("green", "save", 0.85),
    footer_item("blue", "reset", 0.82),
    footer_item("menu", "filter_search", 1.30),
    footer_item("exit", "cancel", 0.78),
)
CATEGORY_MANAGER_MOVE_FOOTER_ITEMS = (
    footer_item("up_down", "select", 1.00),
    footer_item("left_right", "page", 0.90),
    footer_item("ok", "confirm", 0.90),
    footer_item("", "", 0.85),
    footer_item("", "", 0.85),
    footer_item("", "", 0.82),
    footer_item("exit", "cancel", 0.78),
)
CATEGORY_FILTER_FOOTER_ITEMS = (
    footer_item("ok", "visibility", 1.00),
    footer_item("red", "clear", 0.85),
    footer_item("green", "save", 0.85),
    footer_item("yellow", "preview", 1.00),
    footer_item("blue", "search", 0.85),
    footer_item("menu", "rescan", 0.95),
    footer_item("exit", "cancel", 0.85),
)

VIDEO_CODECS = {
    0: "MPEG-2",
    1: "H.264",
    3: "VC-1",
    4: "MPEG-4",
    5: "VC-1 SM",
    6: "MPEG-1",
    7: "H.265 / HEVC",
    16: "AVS",
    22: "H.265 / HEVC",
    40: "AVS2",
}


BROWSER_TRACE_ENABLED = str(
    os.environ.get("GTIPTVPLAYERPRO_TRACE", "") or ""
).strip().lower() in ("1", "true", "yes", "on")


def _debug(message):
    # Success-path tracing is opt-in. Synchronous writes on every selection or
    # zap make production receivers feel slower and can contend with startup.
    if not BROWSER_TRACE_ENABLED:
        return
    safe_message = redact(message)
    log_event("browser", safe_message)
    try:
        print("[GTIPTVPlayerPro] {}".format(safe_message))
    except Exception:
        pass


def _movie_search_text(value):
    """Return a language-neutral key without losing non-Latin scripts."""
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


def _movie_item_search_text(item):
    search_text = getattr(item, "_movie_search_text", None)
    if search_text is not None:
        return search_text
    search_text = _movie_search_text(
        "{} {}".format(
            getattr(item, "name", ""),
            getattr(item, "year", ""),
        )
    )
    try:
        item._movie_search_text = search_text
    except Exception:
        pass
    return search_text


class _WorkerCancelled(RuntimeError):
    pass


class _WorkerToken(object):
    """Cooperative cancellation plus one absolute deadline per UI job."""

    def __init__(self, timeout_seconds):
        try:
            timeout_seconds = float(timeout_seconds)
        except (TypeError, ValueError, OverflowError):
            timeout_seconds = 1.0
        self.cancel_event = threading.Event()
        self.deadline = time.monotonic() + max(0.1, timeout_seconds)

    def cancel(self):
        self.cancel_event.set()

    def active(self):
        return (
            not self.cancel_event.is_set()
            and time.monotonic() < self.deadline
        )

    def expired(self):
        return not self.active()

    def past_deadline(self):
        return time.monotonic() >= self.deadline

    def check(self):
        if not self.active():
            raise _WorkerCancelled()


def _cancel_worker_token(token):
    if token is not None:
        try:
            token.cancel()
        except Exception:
            pass


def _set_component_visible(component, visible):
    """Change a GUI component's visibility only when its state differs."""
    target = bool(visible)
    current = getattr(component, "visible", None)
    if current is not None and bool(current) == target:
        return False
    if target:
        component.show()
    else:
        component.hide()
    return True


class _FavoriteSupport(object):
    """Shared O(1) favorite checks for browser and player screens."""

    def _init_favorite_support(self, favorite_store=None, favorite_keys=None):
        self.favorite_store = (
            favorite_store if favorite_store is not None else FavoriteStore()
        )
        self.favorite_scope = resume_account_scope(self.client)
        if favorite_keys is None:
            self._favorite_keys = set()
            self._reload_favorite_keys()
        else:
            self._favorite_keys = set(favorite_keys)
        self._favorite_initial_show = True

    def _reload_favorite_keys(self):
        try:
            keys = self.favorite_store.keys(self.favorite_scope)
        except Exception:
            keys = set()
        changed = set(keys or ()) != getattr(self, "_favorite_keys", set())
        self._favorite_keys = set(keys or ())
        return changed

    def _favorite_screen_shown(self):
        if self._favorite_initial_show:
            self._favorite_initial_show = False
            return False
        return self._reload_favorite_keys()

    def _favorite_key(self, item):
        return favorite_key(self.favorite_scope, item)

    def _is_favorite(self, item):
        key = self._favorite_key(item)
        return bool(key and key in self._favorite_keys)

    def _toggle_favorite_item(self, item):
        key = self._favorite_key(item)
        if not key:
            return None
        try:
            added = self.favorite_store.toggle(self.favorite_scope, item)
        except Exception:
            added = None
        if added is True:
            self._favorite_keys.add(key)
        elif added is False:
            self._favorite_keys.discard(key)
        return added

    @staticmethod
    def _favorite_message(item, added):
        name = str(getattr(item, "name", "") or "GT IPTV")
        if added is True:
            return _("{} added to favorites.").format(name)
        if added is False:
            return _("{} removed from favorites.").format(name)
        return _("Could not update favorites.")


def _bounded_client_call(client, token, callback):
    """Run a provider call inside its optional cancellation-aware scope."""
    if token is not None:
        token.check()
    scope_factory = getattr(client, "request_scope", None)
    if callable(scope_factory) and token is not None:
        with scope_factory(token.cancel_event, token.deadline):
            result = callback()
    else:
        result = callback()
    if token is not None:
        token.check()
    return result


def _acquire_worker_lock(lock, token=None):
    """Acquire a shared worker lock without outliving the job deadline."""
    if token is None:
        lock.acquire()
        return
    while True:
        token.check()
        remaining = max(0.01, token.deadline - time.monotonic())
        if lock.acquire(timeout=min(0.10, remaining)):
            return


def _minute_timer_delay_ms(period_seconds=60):
    """Align clocks to their next visible value instead of polling each second."""
    try:
        period = max(1, int(period_seconds))
        remaining = period - (time.time() % period)
        return max(250, int(round(remaining * 1000)))
    except Exception:
        return 30000


def _client_epg_state(client):
    """Attach one small EPG cache/in-flight registry to a provider client."""
    if client is None:
        return None
    with _EPG_STATE_GUARD:
        state = getattr(client, "_gt_live_epg_state", None)
        if state is None:
            state = {
                "lock": threading.Lock(),
                "cache": {},
                "inflight": {},
                "epoch": 0,
            }
            try:
                setattr(client, "_gt_live_epg_state", state)
            except Exception:
                return None
        state.setdefault("epoch", 0)
        return state


def _load_shared_live_epg(
    client, stream_id, limit, loader, token=None, refresh=False
):
    """Deduplicate preview/fullscreen EPG work without parallel API calls."""
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError, OverflowError):
        limit = 2
    state = _client_epg_state(client)
    if state is None:
        return list(loader(stream_id, limit) or [])
    key = str(stream_id or "")
    while True:
        if token is not None:
            token.check()
        now = time.monotonic()
        leader = False
        leader_epoch = 0
        with state["lock"]:
            for cached_key, cached_value in list(state["cache"].items()):
                loaded_at, cached_events = cached_value
                ttl = (
                    LIVE_EPG_CACHE_TTL_SECONDS
                    if cached_events
                    else LIVE_EPG_EMPTY_TTL_SECONDS
                )
                if now - loaded_at >= ttl:
                    state["cache"].pop(cached_key, None)
            cached = state["cache"].get(key)
            # Show a valid snapshot immediately, but a completed CH+/CH-
            # selection must not reuse the hour-long cache as its new query.
            if cached is not None and not refresh:
                return list(cached[1][:limit])
            pending = state["inflight"].get(key)
            if pending is None:
                leader_epoch = int(state.get("epoch", 0) or 0)
                pending = {
                    "event": threading.Event(),
                    "epoch": leader_epoch,
                    "cancelled": False,
                }
                state["inflight"][key] = pending
                leader = True
        if leader:
            break
        try:
            request_timeout = max(
                3,
                min(15, int(getattr(client, "timeout", 12))),
            )
        except (TypeError, ValueError, OverflowError):
            request_timeout = 12
        # Xtream may try two EPG endpoints. Wait for both bounded attempts
        # before presenting a false empty schedule to the second screen.
        timeout = (request_timeout * 2) + 2
        wait_deadline = time.monotonic() + timeout
        if token is None:
            pending["event"].wait(timeout)
        else:
            wait_deadline = min(wait_deadline, token.deadline)
            while not pending["event"].is_set():
                token.check()
                remaining = wait_deadline - time.monotonic()
                if remaining <= 0:
                    token.check()
                    return []
                pending["event"].wait(min(0.10, remaining))
            token.check()
        if pending["event"].is_set() and pending.get("cancelled"):
            # Fullscreen may inherit an in-flight preview request, or a quick
            # A -> B -> A zap may meet A's cancelled worker. Wait for that
            # request to release the portal slot, then retry within our own
            # deadline instead of treating cancellation as missing EPG.
            continue
        with state["lock"]:
            cached = state["cache"].get(key)
            return list(cached[1][:limit]) if cached is not None else []
    failed = False
    try:
        # Always fill the shared cache with enough rows for the richer preview
        # ribbon. A fullscreen request (two rows) may win the race, but it must
        # not force the preview to perform the same API request again.
        events = list(loader(stream_id, max(limit, LIVE_EPG_FETCH_LIMIT)) or [])
    except Exception:
        failed = True
        events = []
    cancelled = token is not None and not token.active()
    with state["lock"]:
        epoch_is_current = int(state.get("epoch", 0) or 0) == leader_epoch
        if events and not failed and not cancelled and epoch_is_current:
            state["cache"][key] = (time.monotonic(), events)
        elif epoch_is_current and not refresh and not cancelled:
            state["cache"].pop(key, None)
        if len(state["cache"]) > LIVE_EPG_CACHE_MAX_ENTRIES:
            oldest_keys = sorted(
                state["cache"],
                key=lambda cached_key: state["cache"][cached_key][0],
            )
            for cached_key in oldest_keys[
                : len(state["cache"]) - LIVE_EPG_CACHE_MAX_ENTRIES
            ]:
                state["cache"].pop(cached_key, None)
        if state["inflight"].get(key) is pending:
            state["inflight"].pop(key, None)
        pending["cancelled"] = cancelled
        pending["event"].set()
    return [] if cancelled else list(events[:limit])


def _desktop_size():
    try:
        size = getDesktop(0).size()
        width = int(size.width())
        height = int(size.height())
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass
    return 1280, 720


def _front_panel_size():
    """Return the dedicated LCD/VFD desktop size without touching TV output."""
    try:
        size = getDesktop(1).size()
        width = int(size.width())
        height = int(size.height())
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass
    # Common monochrome OLED/VFD fallback. Boxes without a second desktop do
    # not instantiate a Summary screen, so this value never affects them.
    return 132, 64


def _front_panel_layout():
    """Calculate a large-type layout shared by the skin and fill renderer."""
    width, height = _front_panel_size()
    compact = width <= 240 or height <= 120
    if compact:
        margin = max(1, min(width, height) // 32)
        progress_h = max(3, height // 12)
        progress_y = max(margin, height - margin - progress_h)
        detail_h = max(8, height // 5)
        detail_y = max(margin, progress_y - detail_h)
        subtitle_h = max(8, height // 5)
        subtitle_y = max(margin, detail_y - subtitle_h)
        kind_h = max(8, height // 6)
        kind_y = margin
        title_y = kind_y + kind_h
        title_h = max(8, subtitle_y - title_y)
        kind_font = max(8, min(14, kind_h - 1))
        title_font = max(10, min(20, title_h - 1, width // 7))
        subtitle_font = max(8, min(15, subtitle_h - 1, width // 10))
        detail_font = subtitle_font
    else:
        margin = max(8, min(width, height) // 24)
        progress_h = max(8, height // 18)
        progress_y = height - margin - progress_h
        detail_h = max(24, height // 9)
        detail_y = progress_y - max(4, margin // 2) - detail_h
        kind_h = max(22, height // 10)
        kind_y = margin
        title_y = kind_y + kind_h
        title_h = max(36, height // 4)
        subtitle_y = title_y + title_h
        subtitle_room = max(
            20, detail_y - subtitle_y - max(4, margin // 2)
        )
        subtitle_h = min(max(30, height // 5), subtitle_room)
        kind_font = max(16, min(32, kind_h - 2))
        title_font = max(22, min(66, title_h - 4, width // 8))
        subtitle_font = max(16, min(42, subtitle_h - 4, width // 13))
        detail_font = max(15, min(30, detail_h - 2))
    content_w = max(1, width - (margin * 2))
    title_capacity = max(
        8, int(float(content_w) / max(1.0, float(title_font) * 0.58))
    )
    subtitle_capacity = max(
        10,
        int(float(content_w) / max(1.0, float(subtitle_font) * 0.55)),
    )
    return {
        "width": width,
        "height": height,
        "margin": margin,
        "content_w": content_w,
        "kind_y": kind_y,
        "kind_h": kind_h,
        "kind_font": kind_font,
        "title_y": title_y,
        "title_h": title_h,
        "title_font": title_font,
        "title_capacity": title_capacity,
        "subtitle_y": subtitle_y,
        "subtitle_h": subtitle_h,
        "subtitle_font": subtitle_font,
        "subtitle_capacity": subtitle_capacity,
        "detail_y": detail_y,
        "detail_h": detail_h,
        "detail_font": detail_font,
        "progress_y": progress_y,
        "progress_h": progress_h,
    }


def _front_panel_skin():
    """Build an adaptive LCD/VFD skin using reliable label-based bars."""
    layout = _front_panel_layout()
    return """
<screen name="GTPlayerFrontPanelSummary" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#000000">
    <widget name="kind" position="{margin},{kind_y}" size="{content_w},{kind_h}"
            font="Regular;{kind_font}" foregroundColor="#A855F7"
            backgroundColor="#000000" transparent="0" noWrap="1"
            valign="center" halign="center" />
    <widget name="title" position="{margin},{title_y}" size="{content_w},{title_h}"
            font="Regular;{title_font}" foregroundColor="#FFFFFF"
            backgroundColor="#000000" transparent="0" noWrap="1"
            valign="center" halign="center" />
    <widget name="subtitle" position="{margin},{subtitle_y}"
            size="{content_w},{subtitle_h}" font="Regular;{subtitle_font}"
            foregroundColor="#D8E0EA" backgroundColor="#000000"
            transparent="0" noWrap="1" valign="center" halign="center" />
    <widget name="detail" position="{margin},{detail_y}" size="{content_w},{detail_h}"
            font="Regular;{detail_font}" foregroundColor="#22D3EE"
            backgroundColor="#000000" transparent="0" noWrap="1"
            valign="center" halign="center" />
    <widget name="progress_bg" position="{margin},{progress_y}"
            size="{content_w},{progress_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="1" />
    <widget name="progress_fill" position="{margin},{progress_y}"
            size="1,{progress_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
</screen>
""".format(**layout)


def _scale():
    width, height = _desktop_size()
    factor = min(float(width) / 1920.0, float(height) / 1080.0)

    def px(value):
        return max(1, int(round(value * factor)))

    return width, height, px


def _browser_skin(screen_name):
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    footer_y = height - px(92)
    return """
<screen name="{screen_name}" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{margin},{header_y}" size="{header_w},{header_h}"
            font="Regular;{title_font}" foregroundColor="#FFFFFF"
            transparent="1"
            valign="center" halign="left" />
    <widget name="screen_label" position="{screen_x},{header_y}"
            size="{screen_w},{header_h}" font="Regular;{screen_font}"
            foregroundColor="#7F8DA5" transparent="1"
            valign="center" halign="right" />
    <widget name="content_panel" position="{margin},{panel_y}"
            size="{content_width},{panel_h}" font="Regular;1"
            backgroundColor="#0A1120" transparent="0" />
    <widget name="list_accent" position="{margin},{panel_y}"
            size="{accent_w},{panel_h}" font="Regular;1"
            backgroundColor="#8B5CF6" transparent="0" />
    <widget name="breadcrumb" position="{inner_x},{crumb_y}"
            size="{inner_w},{crumb_h}" font="Regular;{crumb_font}"
            foregroundColor="#22D3EE" backgroundColor="#0A1120"
            transparent="0" valign="center" halign="left" />
    <widget name="items" position="{inner_x},{list_y}"
            size="{inner_w},{list_h}" font="Regular;{list_font}"
            foregroundColor="#F3F6FB" backgroundColor="#111B2E"
            transparent="0" valign="top" halign="left" />
    <widget name="list_scroll_track" position="{scroll_x},{list_y}"
            size="{scroll_w},{list_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb" position="{scroll_x},{list_y}"
            size="{scroll_w},{list_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="message" position="{inner_x},{message_y}"
            size="{inner_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#9AA8BE" backgroundColor="#0A1120"
            transparent="0" valign="center" halign="right" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
</screen>
""".format(
        screen_name=screen_name,
        width=width,
        height=height,
        accent_h=px(5),
        margin=margin,
        content_width=content_width,
        header_y=px(28),
        header_w=px(900),
        header_h=px(64),
        title_font=px(34),
        screen_x=width - margin - px(600),
        screen_w=px(600),
        screen_font=px(19),
        panel_y=px(112),
        panel_h=px(828),
        accent_w=px(7),
        inner_x=margin + px(38),
        inner_w=content_width - px(76),
        scroll_x=margin + content_width - px(50),
        scroll_w=px(8),
        crumb_y=px(136),
        crumb_h=px(52),
        crumb_font=px(21),
        list_y=px(205),
        list_h=px(650),
        list_font=px(28),
        message_y=px(865),
        message_h=px(50),
        message_font=px(20),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _episode_list_skin():
    """Native episode rows matching the Live TV list focus treatment."""
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    footer_y = height - px(92)
    panel_y = px(112)
    panel_h = px(828)
    row_x = margin + px(38)
    row_w = content_width - px(100)
    row_start = 224
    row_step = 62
    rows = []
    for index in range(EPISODE_PAGE_SIZE):
        y = px(row_start + (index * row_step))
        rows.append(
            """
    <widget name="episode_row_bg_{index}" position="{row_x},{row_y}"
            size="{row_w},{row_h}" font="Regular;1"
            backgroundColor="#111D31" transparent="0" zPosition="1" />
    <widget name="episode_number_{index}" position="{number_x},{row_y}"
            size="{number_w},{row_h}" font="Regular;{number_font}"
            foregroundColor="#D9E2F1" transparent="1" zPosition="3"
            valign="center" halign="right" />
    <widget name="episode_name_{index}" position="{name_x},{row_y}"
            size="{name_w},{row_h}" font="Regular;{name_font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="3"
            valign="center" halign="left" noWrap="1" />
    <widget name="episode_focus_{index}_fill" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_box_h}" font="Regular;1"
            backgroundColor="#172A45" transparent="0" zPosition="2" />
    <widget name="episode_focus_{index}_top" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#8B5CF6" transparent="0" zPosition="4" />
    <widget name="episode_focus_{index}_bottom"
            position="{focus_x},{focus_bottom_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#8B5CF6" transparent="0" zPosition="4" />
    <widget name="episode_focus_{index}_left" position="{focus_x},{focus_y}"
            size="{focus_h},{focus_box_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="episode_focus_{index}_right"
            position="{focus_right_x},{focus_y}"
            size="{focus_h},{focus_box_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="4" />""".format(
                index=index,
                row_x=row_x,
                row_y=y,
                row_w=row_w,
                row_h=px(55),
                number_x=row_x + px(20),
                number_w=px(110),
                number_font=px(19),
                name_x=row_x + px(150),
                name_w=row_w - px(180),
                name_font=px(23),
                focus_x=row_x - px(2),
                focus_y=y - px(2),
                focus_w=row_w + px(4),
                focus_h=px(3),
                focus_bottom_y=y + px(54),
                focus_box_h=px(59),
                focus_right_x=row_x + row_w - px(1),
            )
        )

    return """
<screen name="GTEpisodeListScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{margin},{header_y}"
            size="{header_w},{header_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" noWrap="1" />
    <widget name="screen_label" position="{screen_x},{header_y}"
            size="{screen_w},{header_h}" font="Regular;{screen_font}"
            foregroundColor="#7F8DA5" transparent="1"
            valign="center" halign="right" />
    <widget name="content_panel" position="{margin},{panel_y}"
            size="{content_width},{panel_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="episode_panel_accent_cyan"
            position="{margin},{panel_y}" size="{panel_accent_w},{panel_accent_h}"
            font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="2" />
    <widget name="episode_panel_accent_magenta"
            position="{panel_accent_x},{panel_y}"
            size="{panel_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="2" />
    <widget name="list_accent" position="{margin},{panel_y}"
            size="{list_accent_w},{panel_h}" font="Regular;1"
            backgroundColor="#8B5CF6" transparent="0" />
    <widget name="breadcrumb" position="{inner_x},{crumb_y}"
            size="{inner_w},{crumb_h}" font="Regular;{crumb_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" noWrap="1" />
    <widget name="episode_caption" position="{inner_x},{caption_y}"
            size="{inner_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#D8E2F1" transparent="1"
            valign="center" halign="left" />
    <widget name="episode_caption_accent"
            position="{inner_x},{caption_accent_y}"
            size="{caption_accent_w},{caption_accent_h}" font="Regular;1"
            backgroundColor="#A855F7" transparent="0" />
    {rows}
    <widget name="items" position="0,0" size="1,1" font="Regular;1"
            transparent="1" zPosition="0" />
    <widget name="list_scroll_track"
            position="{scroll_x},{scroll_y}" size="{scroll_w},{scroll_h}"
            font="Regular;1" backgroundColor="#26344E"
            transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb"
            position="{scroll_x},{scroll_y}" size="{scroll_w},{scroll_h}"
            font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="6" />
    <widget name="message" position="{inner_x},{message_y}"
            size="{inner_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#9AA8BE" transparent="1"
            valign="center" halign="right" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        accent_h=px(5),
        margin=margin,
        header_y=px(28),
        header_w=width - (margin * 2) - px(500),
        header_h=px(64),
        title_font=px(34),
        screen_x=width - margin - px(480),
        screen_w=px(480),
        screen_font=px(19),
        panel_y=panel_y,
        content_width=content_width,
        panel_h=panel_h,
        panel_accent_w=int(content_width / 2),
        panel_accent_h=px(4),
        panel_accent_x=margin + int(content_width / 2),
        list_accent_w=px(7),
        inner_x=margin + px(38),
        inner_w=content_width - px(76),
        crumb_y=px(132),
        crumb_h=px(42),
        crumb_font=px(20),
        caption_y=px(174),
        caption_h=px(36),
        caption_font=px(22),
        caption_accent_y=px(211),
        caption_accent_w=px(260),
        caption_accent_h=px(3),
        rows="".join(rows),
        scroll_x=margin + content_width - px(50),
        scroll_y=px(row_start),
        scroll_w=px(8),
        scroll_h=px(613),
        message_y=px(856),
        message_h=px(52),
        message_font=px(20),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _continue_watching_skin(screen_name="GTContinueWatchingScreen"):
    """Poster-card grid for every unfinished movie and series episode."""
    width, height, px = _scale()
    favorite_star = FAVORITE_STAR_PATH.replace("&", "&amp;").replace(
        '"', "&quot;"
    )
    margin = px(64)
    footer_y = height - px(92)
    panel_y = px(112)
    panel_h = px(828)
    grid_x = px(86)
    grid_y = (px(215), px(545))
    card_w = px(420)
    card_h = px(305)
    card_gap = px(22)
    poster_h = px(188)
    progress_x_pad = px(18)
    progress_w = card_w - (progress_x_pad * 2)
    progress_h = px(9)
    focus_h = px(4)
    cards = []
    for index in range(8):
        row, column = divmod(index, 4)
        card_x = grid_x + (column * (card_w + card_gap))
        card_y = grid_y[row]
        cards.append(
            """
    <widget name="continue_card_{index}" position="{card_x},{card_y}"
            size="{card_w},{card_h}" font="Regular;1"
            backgroundColor="#101A2D" transparent="0" zPosition="2" />
    <widget name="continue_poster_placeholder_{index}"
            position="{poster_x},{poster_y}" size="{poster_w},{poster_h}"
            font="Regular;{placeholder_font}" foregroundColor="#4B6485"
            backgroundColor="#0B1425" transparent="0" zPosition="3"
            valign="center" halign="center" />
    <widget name="continue_poster_{index}"
            position="{poster_x},{poster_y}" size="{poster_w},{poster_h}"
            alphatest="blend" scale="1" zPosition="4" />
    <widget name="continue_favorite_{index}"
            position="{favorite_x},{favorite_y}"
            size="{favorite_size},{favorite_size}"
            pixmap="{favorite_star}" alphatest="blend" scale="1"
            zPosition="7" />
    <widget name="continue_type_{index}" position="{type_x},{type_y}"
            size="{type_w},{type_h}" font="Regular;{type_font}"
            foregroundColor="#22D3EE" backgroundColor="#152746"
            transparent="0" zPosition="6" valign="center" halign="center" />
    <widget name="continue_title_{index}" position="{text_x},{title_y}"
            size="{text_w},{title_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="5"
            valign="center" halign="left" noWrap="1" />
    <widget name="continue_meta_{index}" position="{text_x},{meta_y}"
            size="{text_w},{meta_h}" font="Regular;{meta_font}"
            foregroundColor="#AEBBCD" transparent="1" zPosition="5"
            valign="center" halign="left" />
    <widget name="continue_progress_bg_{index}"
            position="{progress_x},{progress_y}" size="{progress_w},{progress_h}"
            font="Regular;1" backgroundColor="#334155"
            transparent="0" zPosition="5" />
    <widget name="continue_progress_fill_{index}"
            position="{progress_x},{progress_y}" size="{progress_w},{progress_h}"
            font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="6" />
    <widget name="continue_progress_text_{index}"
            position="{text_x},{progress_text_y}" size="{text_w},{progress_text_h}"
            font="Regular;{progress_font}" foregroundColor="#E2E8F0"
            transparent="1" zPosition="5" valign="center" halign="center" />
    <widget name="continue_focus_{index}_top" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="8" />
    <widget name="continue_focus_{index}_bottom"
            position="{focus_x},{focus_bottom_y}" size="{focus_w},{focus_h}"
            font="Regular;1" backgroundColor="#D946EF"
            transparent="0" zPosition="8" />
    <widget name="continue_focus_{index}_left" position="{focus_x},{focus_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="8" />
    <widget name="continue_focus_{index}_right"
            position="{focus_right_x},{focus_y}" size="{focus_h},{card_h}"
            font="Regular;1" backgroundColor="#D946EF"
            transparent="0" zPosition="8" />""".format(
                index=index,
                card_x=card_x,
                card_y=card_y,
                card_w=card_w,
                card_h=card_h,
                poster_x=card_x + px(6),
                poster_y=card_y + px(6),
                poster_w=card_w - px(12),
                poster_h=poster_h,
                favorite_x=card_x + card_w - px(58),
                favorite_y=card_y + px(14),
                favorite_size=px(42),
                favorite_star=favorite_star,
                placeholder_font=px(28),
                type_x=card_x + px(16),
                type_y=card_y + px(16),
                type_w=px(100),
                type_h=px(38),
                type_font=px(20),
                text_x=card_x + px(18),
                text_w=card_w - px(36),
                title_y=card_y + px(194),
                title_h=px(48),
                title_font=px(28),
                meta_y=card_y + px(236),
                meta_h=px(30),
                meta_font=px(19),
                progress_x=card_x + progress_x_pad,
                progress_y=card_y + px(267),
                progress_w=progress_w,
                progress_h=progress_h,
                progress_text_y=card_y + px(278),
                progress_text_h=px(25),
                progress_font=px(19),
                focus_x=card_x,
                focus_y=card_y,
                focus_w=card_w,
                focus_h=focus_h,
                focus_bottom_y=card_y + card_h - focus_h,
                focus_right_x=card_x + card_w - focus_h,
            )
        )
    return """
<screen name="{screen_name}" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{margin},{header_y}"
            size="{header_w},{header_h}" font="Regular;{header_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" />
    <widget name="screen_label" position="{screen_x},{header_y}"
            size="{screen_w},{header_h}" font="Regular;{screen_font}"
            foregroundColor="#AEBBCD" transparent="1"
            valign="center" halign="right" />
    <widget name="content_panel" position="{margin},{panel_y}"
            size="{panel_w},{panel_h}" font="Regular;1"
            backgroundColor="#0A1120" transparent="0" zPosition="1" />
    <widget name="list_accent" position="{margin},{panel_y}"
            size="{accent_w},{panel_h}" font="Regular;1"
            backgroundColor="#8B5CF6" transparent="0" zPosition="2" />
    <widget name="breadcrumb" position="{crumb_x},{crumb_y}"
            size="{crumb_w},{crumb_h}" font="Regular;{crumb_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="subtitle" position="{crumb_x},{subtitle_y}"
            size="{crumb_w},{subtitle_h}" font="Regular;{subtitle_font}"
            foregroundColor="#CBD5E1" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="items" position="1,1" size="1,1" font="Regular;1"
            transparent="1" />
    {cards}
    <widget name="list_scroll_track" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="message" position="{message_x},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#9AA8BE" transparent="1" zPosition="4"
            valign="center" halign="right" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
</screen>
""".format(
        screen_name=screen_name,
        width=width,
        height=height,
        accent_h=px(5),
        margin=margin,
        header_y=px(28),
        header_w=px(1040),
        header_h=px(64),
        header_font=px(34),
        screen_x=width - margin - px(500),
        screen_w=px(500),
        screen_font=px(18),
        panel_y=panel_y,
        panel_w=width - (margin * 2),
        panel_h=panel_h,
        accent_w=px(7),
        crumb_x=px(98),
        crumb_y=px(125),
        crumb_w=px(1450),
        crumb_h=px(42),
        crumb_font=px(20),
        subtitle_y=px(164),
        subtitle_h=px(38),
        subtitle_font=px(21),
        cards="".join(cards),
        scroll_x=width - px(82),
        scroll_y=px(215),
        scroll_w=px(8),
        scroll_h=px(635),
        message_x=width - px(520),
        message_y=px(858),
        message_w=px(430),
        message_h=px(52),
        message_font=px(19),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(18),
    )


def _favorites_skin():
    """Favorites-only 5×3 poster grid; Continue Watching stays 4×2."""
    width, height, px = _scale()
    margin = px(54)
    footer_y = height - px(92)
    panel_y = px(104)
    panel_h = px(836)
    tab_y = px(118)
    tab_w = px(300)
    tab_h = px(58)
    tab_gap = px(12)
    grid_x = px(66)
    grid_y = (px(190), px(405), px(620))
    card_w = px(342)
    card_h = px(200)
    card_gap = px(14)
    poster_h = px(154)
    focus_h = max(2, px(4))
    cards = []
    for index in range(15):
        row, column = divmod(index, 5)
        card_x = grid_x + (column * (card_w + card_gap))
        card_y = grid_y[row]
        hidden = px(1)
        cards.append(
            """
    <widget name="continue_card_{index}" position="{card_x},{card_y}"
            size="{card_w},{card_h}" font="Regular;1"
            backgroundColor="#101A2D" transparent="0" zPosition="2" />
    <widget name="continue_poster_placeholder_{index}"
            position="{poster_x},{poster_y}" size="{poster_w},{poster_h}"
            font="Regular;{placeholder_font}" foregroundColor="#4B6485"
            backgroundColor="#0B1425" transparent="0" zPosition="3"
            valign="center" halign="center" />
    <widget name="continue_poster_{index}"
            position="{poster_x},{poster_y}" size="{poster_w},{poster_h}"
            alphatest="blend" scale="1" zPosition="4" />
    <widget name="continue_type_{index}" position="{type_x},{type_y}"
            size="{type_w},{type_h}" font="Regular;{type_font}"
            foregroundColor="#22D3EE" backgroundColor="#10243C"
            transparent="0" zPosition="6" valign="center" halign="center" />
    <widget name="continue_title_{index}" position="{title_x},{title_y}"
            size="{title_w},{title_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" backgroundColor="#101A2D"
            transparent="0" zPosition="5" valign="center" halign="left"
            noWrap="1" />
    <widget name="continue_favorite_{index}" position="0,0"
            size="{hidden},{hidden}" zPosition="1" />
    <widget name="continue_meta_{index}" position="0,0"
            size="{hidden},{hidden}" font="Regular;1" transparent="1" />
    <widget name="continue_progress_bg_{index}" position="0,0"
            size="{hidden},{hidden}" font="Regular;1" transparent="1" />
    <widget name="continue_progress_fill_{index}" position="0,0"
            size="{hidden},{hidden}" font="Regular;1" transparent="1" />
    <widget name="continue_progress_text_{index}" position="0,0"
            size="{hidden},{hidden}" font="Regular;1" transparent="1" />
    <widget name="continue_focus_{index}_top" position="{card_x},{card_y}"
            size="{card_w},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="8" />
    <widget name="continue_focus_{index}_bottom" position="{card_x},{bottom_y}"
            size="{card_w},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="8" />
    <widget name="continue_focus_{index}_left" position="{card_x},{card_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="8" />
    <widget name="continue_focus_{index}_right" position="{right_x},{card_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="8" />
    <widget name="continue_move_{index}_top" position="{card_x},{card_y}"
            size="{card_w},{focus_h}" font="Regular;1"
            backgroundColor="#FACC15" transparent="0" zPosition="9" />
    <widget name="continue_move_{index}_bottom" position="{card_x},{bottom_y}"
            size="{card_w},{focus_h}" font="Regular;1"
            backgroundColor="#FACC15" transparent="0" zPosition="9" />
    <widget name="continue_move_{index}_left" position="{card_x},{card_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#FACC15" transparent="0" zPosition="9" />
    <widget name="continue_move_{index}_right" position="{right_x},{card_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#FACC15" transparent="0" zPosition="9" />""".format(
                index=index,
                card_x=card_x,
                card_y=card_y,
                card_w=card_w,
                card_h=card_h,
                poster_x=card_x + px(5),
                poster_y=card_y + px(5),
                poster_w=card_w - px(10),
                poster_h=poster_h,
                placeholder_font=max(10, px(24)),
                type_x=card_x + px(12),
                type_y=card_y + px(10),
                type_w=px(92),
                type_h=px(30),
                type_font=max(9, px(16)),
                title_x=card_x + px(12),
                title_y=card_y + px(158),
                title_w=card_w - px(24),
                title_h=px(37),
                title_font=max(10, px(21)),
                hidden=hidden,
                focus_h=focus_h,
                bottom_y=card_y + card_h - focus_h,
                right_x=card_x + card_w - focus_h,
            )
        )
    filters = []
    for index in range(4):
        tab_x = px(66) + (index * (tab_w + tab_gap))
        filters.append(
            """
    <widget name="favorite_filter_focus_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="3" />
    <widget name="favorite_filter_{index}" position="{inner_x},{inner_y}"
            size="{inner_w},{inner_h}" font="Regular;{font}"
            foregroundColor="#FFFFFF" backgroundColor="#101A2D"
            transparent="0" zPosition="4" valign="center" halign="center" />""".format(
                index=index,
                x=tab_x,
                y=tab_y,
                w=tab_w,
                h=tab_h,
                inner_x=tab_x + px(3),
                inner_y=tab_y + px(3),
                inner_w=tab_w - px(6),
                inner_h=tab_h - px(6),
                font=max(10, px(21)),
            )
        )
    return """
<screen name="GTFavoritesScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}" zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{margin},{header_y}" size="{header_w},{header_h}"
            font="Regular;{header_font}" foregroundColor="#FFFFFF"
            transparent="1" valign="center" halign="left" />
    <widget name="screen_label" position="{screen_x},{header_y}"
            size="{screen_w},{header_h}" font="Regular;{screen_font}"
            foregroundColor="#FFFFFF" transparent="1" valign="center" halign="right" />
    <widget name="content_panel" position="{margin},{panel_y}"
            size="{panel_w},{panel_h}" font="Regular;1"
            backgroundColor="#0A1120" transparent="0" zPosition="1" />
    <widget name="list_accent" position="{margin},{panel_y}" size="{accent_w},{panel_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" zPosition="2" />
    <widget name="breadcrumb" position="{margin},{crumb_y}" size="{crumb_w},{crumb_h}"
            font="Regular;{crumb_font}" foregroundColor="#22D3EE"
            transparent="1" valign="center" halign="left" />
    <widget name="subtitle" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="items" position="1,1" size="1,1" font="Regular;1" transparent="1" />
    {filters}
    {cards}
    <widget name="favorite_info" position="{info_x},{info_y}"
            size="{info_w},{info_h}" font="Regular;{info_font}"
            foregroundColor="#60A5FA" backgroundColor="#081426"
            transparent="0" zPosition="4" valign="center" halign="left"
            noWrap="1" />
    <widget name="list_scroll_track" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="message" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0" valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        accent_h=px(5),
        margin=margin,
        header_y=px(20),
        header_w=px(760),
        header_h=px(50),
        header_font=max(12, px(31)),
        screen_x=width - margin - px(620),
        screen_w=px(620),
        screen_font=max(9, px(18)),
        panel_y=panel_y,
        panel_w=width - (margin * 2),
        panel_h=panel_h,
        accent_w=px(5),
        crumb_y=px(68),
        crumb_w=px(1200),
        crumb_h=px(34),
        crumb_font=max(9, px(18)),
        filters="".join(filters),
        cards="".join(cards),
        info_x=px(66),
        info_y=px(835),
        info_w=width - px(132),
        info_h=px(68),
        info_font=max(10, px(21)),
        scroll_x=width - px(73),
        scroll_y=px(190),
        scroll_w=px(7),
        scroll_h=px(630),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=max(9, px(17)),
    )


def _live_category_skin():
    width, height, px = _scale()
    margin = px(64)
    footer_y = height - px(92)
    left_x = margin
    left_w = px(990)
    right_x = left_x + left_w + px(28)
    right_w = width - right_x - margin
    panel_y = px(135)
    panel_h = px(805)
    row_y = (252, 332, 412, 492, 572, 652, 732, 812)
    rows = []
    for index, y_value in enumerate(row_y):
        rows.append(
            """
    <widget name="category_row_{index}" position="{row_x},{row_y}"
            size="{row_w},{row_h}" font="Regular;{row_font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="4"
            valign="center" halign="left" noWrap="1" />
    <widget name="category_focus_{index}_fill" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_box_h}" font="Regular;1"
            backgroundColor="#172A45" transparent="0" zPosition="2" />
    <widget name="category_focus_{index}_top" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="3" />
    <widget name="category_focus_{index}_bottom" position="{focus_x},{focus_bottom_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="3" />
    <widget name="category_focus_{index}_left" position="{focus_x},{focus_y}"
            size="{focus_h},{focus_box_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="3" />
    <widget name="category_focus_{index}_right" position="{focus_right_x},{focus_y}"
            size="{focus_h},{focus_box_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="3" />""".format(
                index=index,
                row_x=left_x + px(42),
                row_y=px(y_value),
                row_w=left_w - px(90),
                row_h=px(66),
                row_font=px(27),
                focus_x=left_x + px(20),
                focus_y=px(y_value - 4),
                focus_w=left_w - px(40),
                focus_h=px(4),
                focus_bottom_y=px(y_value + 66),
                focus_box_h=px(74),
                focus_right_x=left_x + left_w - px(24),
            )
        )
    weather_x = right_x + px(32)
    weather_w = right_w - px(64)
    day_gap = px(8)
    day_w = int((weather_w - (day_gap * 4)) / 5)
    weather_days = []
    for index in range(5):
        day_x = weather_x + (index * (day_w + day_gap))
        weather_days.append(
            """
    <widget name="weather_day_card_{index}" position="{day_x},{day_y}"
            size="{day_w},{day_h}" font="Regular;1"
            transparent="1" zPosition="2" />
    <widget name="weather_day_{index}" position="{day_x},{day_label_y}"
            size="{day_w},{day_label_h}" font="Regular;{day_label_font}"
            foregroundColor="#E2E8F0" transparent="1" zPosition="4"
            valign="center" halign="center" />
    <widget name="weather_day_icon_{index}" position="{day_icon_x},{day_icon_y}"
            size="{day_icon_w},{day_icon_h}" alphatest="blend" scale="1"
            zPosition="4" />
    <widget name="weather_day_high_{index}" position="{day_x},{day_high_y}"
            size="{day_w},{day_high_h}" font="Regular;{day_high_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="4"
            valign="center" halign="center" />
    <widget name="weather_day_low_{index}" position="{day_x},{day_low_y}"
            size="{day_w},{day_low_h}" font="Regular;{day_low_font}"
            foregroundColor="#2FB7FF" transparent="1" zPosition="4"
            valign="center" halign="center" />""".format(
                index=index,
                day_x=day_x,
                day_y=px(510),
                day_w=day_w,
                day_h=px(285),
                day_label_y=px(520),
                day_label_h=px(42),
                day_label_font=px(18),
                day_icon_x=day_x + int((day_w - px(92)) / 2),
                day_icon_y=px(575),
                day_icon_w=px(92),
                day_icon_h=px(92),
                day_high_y=px(682),
                day_high_h=px(48),
                day_high_font=px(27),
                day_low_y=px(730),
                day_low_h=px(42),
                day_low_font=px(21),
            )
        )
    return """
<screen name="GTContentBrowserScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#020617">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{margin},{header_y}"
            size="{header_w},{header_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" />
    <widget name="server" position="{server_x},{header_y}"
            size="{server_w},{header_h}" font="Regular;{server_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="right" />
    <widget name="clock" position="{clock_x},{header_y}"
            size="{clock_w},{header_h}" font="Regular;{clock_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="right" />
    <widget name="left_panel" position="{left_x},{panel_y}"
            size="{left_w},{panel_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="right_panel" position="{right_x},{panel_y}"
            size="{right_w},{panel_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="left_panel_accent_cyan" position="{left_x},{panel_y}"
            size="{left_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="left_panel_accent_magenta" position="{left_accent_x},{panel_y}"
            size="{left_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="2" />
    <widget name="right_panel_accent_cyan" position="{right_x},{panel_y}"
            size="{right_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="right_panel_accent_magenta" position="{right_accent_x},{panel_y}"
            size="{right_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="2" />
    <widget name="breadcrumb" position="{crumb_x},{crumb_y}"
            size="{crumb_w},{crumb_h}" font="Regular;{crumb_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" />
    <widget name="categories_caption" position="{crumb_x},{caption_y}"
            size="{crumb_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" />
    {rows}
    <widget name="list_scroll_track" position="{category_scroll_x},{category_scroll_y}"
            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb" position="{category_scroll_x},{category_scroll_y}"
            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="weather_caption" position="{weather_x},{weather_caption_y}"
            size="{weather_w},{weather_caption_h}" font="Regular;{caption_font}"
            foregroundColor="#A78BFA" transparent="1"
            valign="center" halign="left" />
    <widget name="weather_city" position="{weather_x},{weather_city_y}"
            size="{weather_w},{weather_city_h}" font="Regular;{weather_city_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" />
    <widget name="weather_today_card" position="{weather_x},{weather_card_y}"
            size="{weather_w},{weather_card_h}" font="Regular;1"
            transparent="1" zPosition="2" />
    <widget name="weather_current_icon" position="{current_icon_x},{current_icon_y}"
            size="{current_icon_w},{current_icon_h}" alphatest="blend" scale="1"
            zPosition="4" />
    <widget name="weather_today_label" position="{today_text_x},{today_label_y}"
            size="{today_text_w},{today_label_h}" font="Regular;{today_label_font}"
            foregroundColor="#AAB7CC" transparent="1" zPosition="4"
            valign="center" halign="left" />
    <widget name="weather_temp" position="{today_text_x},{weather_temp_y}"
            size="{temp_w},{weather_temp_h}" font="Regular;{weather_temp_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="4"
            valign="center" halign="left" />
    <widget name="weather_desc" position="{today_text_x},{weather_desc_y}"
            size="{today_text_w},{weather_desc_h}" font="Regular;{weather_desc_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="4"
            valign="center" halign="left" />
    {weather_days}
    <widget name="weather_metrics" position="{weather_x},{metrics_y}"
            size="{weather_w},{metrics_h}" font="Regular;{metrics_font}"
            foregroundColor="#9FB0C8" transparent="1"
            valign="center" halign="center" />
    <widget name="weather_updated" position="{weather_x},{updated_y}"
            size="{weather_w},{updated_h}" font="Regular;{updated_font}"
            foregroundColor="#64748B" transparent="1"
            valign="center" halign="center" />
    <widget name="message" position="{crumb_x},{message_y}"
            size="{crumb_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#9AA8BE" transparent="1"
            valign="center" halign="right" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
    <widget name="screen_label" position="0,0" size="1,1" font="Regular;1"
            transparent="1" />
    <widget name="content_panel" position="0,0" size="1,1" font="Regular;1"
            transparent="1" />
    <widget name="list_accent" position="0,0" size="1,1" font="Regular;1"
            transparent="1" />
    <widget name="items" position="0,0" size="1,1" font="Regular;1"
            transparent="1" />
</screen>
""".format(
        width=width,
        height=height,
        accent_h=px(5),
        margin=margin,
        header_y=px(28),
        header_w=px(630),
        header_h=px(70),
        title_font=px(37),
        server_x=px(730),
        server_w=px(780),
        server_font=px(19),
        clock_x=width - margin - px(230),
        clock_w=px(230),
        clock_font=px(28),
        left_x=left_x,
        left_w=left_w,
        right_x=right_x,
        right_w=right_w,
        left_accent_w=int(left_w / 2),
        left_accent_x=left_x + int(left_w / 2),
        right_accent_w=int(right_w / 2),
        right_accent_x=right_x + int(right_w / 2),
        panel_accent_h=px(3),
        panel_y=panel_y,
        panel_h=panel_h,
        crumb_x=left_x + px(38),
        crumb_y=px(155),
        crumb_w=left_w - px(76),
        crumb_h=px(45),
        crumb_font=px(19),
        caption_y=px(200),
        caption_h=px(48),
        caption_font=px(23),
        rows="".join(rows),
        category_scroll_x=left_x + left_w - px(18),
        category_scroll_y=px(252),
        category_scroll_w=px(8),
        category_scroll_h=px(626),
        weather_x=weather_x,
        weather_w=weather_w,
        weather_caption_y=px(158),
        weather_caption_h=px(38),
        weather_city_y=px(198),
        weather_city_h=px(45),
        weather_city_font=px(25),
        weather_card_y=px(255),
        weather_card_h=px(230),
        current_icon_x=weather_x + px(18),
        current_icon_y=px(278),
        current_icon_w=px(185),
        current_icon_h=px(185),
        today_text_x=weather_x + px(225),
        today_text_w=weather_w - px(250),
        today_label_y=px(272),
        today_label_h=px(35),
        today_label_font=px(17),
        weather_temp_y=px(302),
        weather_temp_h=px(92),
        weather_temp_font=px(72),
        temp_w=weather_w - px(250),
        weather_desc_y=px(392),
        weather_desc_h=px(50),
        weather_desc_font=px(23),
        weather_days="".join(weather_days),
        metrics_y=px(815),
        metrics_h=px(48),
        metrics_font=px(18),
        updated_y=px(858),
        updated_h=px(34),
        updated_font=px(15),
        message_y=px(885),
        message_h=px(42),
        message_font=px(18),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(19),
    )


def _live_category_manager_skin():
    """Category editor using the same visual list as the Live TV screen."""

    width, height, px = _scale()
    margin = px(64)
    footer_y = height - px(92)
    panel_y = px(135)
    panel_h = px(805)
    panel_w = px(990)
    row_y = (252, 332, 412, 492, 572, 652, 732, 812)
    check_x = margin + px(42)
    row_x = margin + px(94)
    state_x = margin + panel_w - px(270)
    checkbox_paths = {
        state: plugin_path(
            "skin", "images", "category-check-{}-r83.png".format(state)
        ).replace("&", "&amp;").replace('"', "&quot;")
        for state in ("empty", "tick")
    }
    rows = []
    for index, y_value in enumerate(row_y):
        rows.append(
            """
    <widget name="category_check_{index}_empty" position="{check_x},{check_y}"
            size="{check_size},{check_size}" pixmap="{check_empty}"
            alphatest="blend" scale="1" zPosition="4" />
    <widget name="category_check_{index}_tick" position="{check_x},{check_y}"
            size="{check_size},{check_size}" pixmap="{check_tick}"
            alphatest="blend" scale="1" zPosition="4" />
    <widget name="category_row_{index}" position="{row_x},{row_y}"
            size="{row_w},{row_h}" font="Regular;{row_font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="4"
            noWrap="1" valign="center" halign="left" />
    <widget name="category_state_{index}" position="{state_x},{row_y}"
            size="{state_w},{row_h}" font="Regular;{state_font}"
            foregroundColor="#D946EF" transparent="1" zPosition="4"
            noWrap="1" valign="center" halign="right" />
    <widget name="category_focus_{index}_fill" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_box_h}" font="Regular;1"
            backgroundColor="#172A45" transparent="0" zPosition="2" />
    <widget name="category_focus_{index}_top" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="3" />
    <widget name="category_focus_{index}_bottom"
            position="{focus_x},{focus_bottom_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="3" />
    <widget name="category_focus_{index}_left" position="{focus_x},{focus_y}"
            size="{focus_h},{focus_box_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="3" />
    <widget name="category_focus_{index}_right"
            position="{focus_right_x},{focus_y}"
            size="{focus_h},{focus_box_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="3" />
    <widget name="category_insert_{index}" position="{focus_x},{insert_y}"
            size="{focus_w},{insert_h}" font="Regular;1"
            backgroundColor="#FACC15" transparent="0" zPosition="5" />""".format(
                index=index,
                check_x=check_x,
                check_y=px(y_value + 13),
                check_size=px(40),
                check_empty=checkbox_paths["empty"],
                check_tick=checkbox_paths["tick"],
                row_x=row_x,
                row_y=px(y_value),
                row_w=state_x - row_x - px(18),
                row_h=px(66),
                row_font=px(27),
                state_x=state_x,
                state_w=px(230),
                state_font=px(20),
                focus_x=margin + px(20),
                focus_y=px(y_value - 4),
                focus_w=panel_w - px(40),
                focus_h=px(4),
                focus_bottom_y=px(y_value + 66),
                focus_box_h=px(74),
                focus_right_x=margin + panel_w - px(24),
                insert_y=px(y_value + 70),
                insert_h=px(3),
            )
        )
    return """
<screen name="GTLiveCategoryManagerScreen" position="0,0"
        size="{width},{height}" flags="wfNoBorder" backgroundColor="#020617">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{margin},{header_y}"
            size="{header_w},{header_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" />
    <widget name="screen_label" position="{screen_x},{header_y}"
            size="{screen_w},{header_h}" font="Regular;{screen_font}"
            foregroundColor="#7F8DA5" transparent="1"
            valign="center" halign="right" />
    <widget name="content_panel" position="{margin},{panel_y}"
            size="{panel_w},{panel_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="list_accent" position="{margin},{panel_y}"
            size="{panel_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="manager_panel_accent_magenta"
            position="{panel_accent_x},{panel_y}"
            size="{panel_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="2" />
    <widget name="breadcrumb" position="{crumb_x},{crumb_y}"
            size="{crumb_w},{crumb_h}" font="Regular;{crumb_font}"
            foregroundColor="#22D3EE" transparent="1"
            noWrap="1" valign="center" halign="left" />
    <widget name="manager_caption" position="{crumb_x},{caption_y}"
            size="{crumb_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" />
    {rows}
    <widget name="manager_selection_count" position="{help_x},{caption_y}"
            size="{help_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#22D3EE" transparent="1"
            noWrap="1" valign="center" halign="left" />
    <widget name="manager_move_help" position="{help_x},{help_y}"
            size="{help_w},{help_h}" font="Regular;{help_font}"
            foregroundColor="#FACC15" transparent="1"
            valign="top" halign="left" />
    <widget name="list_scroll_track" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="message" position="{crumb_x},{message_y}"
            size="{crumb_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#9AA8BE" transparent="1"
            noWrap="1" valign="center" halign="right" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
    <widget name="items" position="0,0" size="1,1" font="Regular;1"
            transparent="1" />
</screen>
""".format(
        width=width,
        height=height,
        accent_h=px(5),
        margin=margin,
        header_y=px(28),
        header_w=px(700),
        header_h=px(70),
        title_font=px(37),
        screen_x=width - margin - px(700),
        screen_w=px(700),
        screen_font=px(19),
        panel_y=panel_y,
        panel_w=panel_w,
        panel_h=panel_h,
        panel_accent_w=int(panel_w / 2),
        panel_accent_x=margin + int(panel_w / 2),
        panel_accent_h=px(3),
        crumb_x=margin + px(38),
        crumb_y=px(155),
        crumb_w=panel_w - px(76),
        crumb_h=px(45),
        crumb_font=px(19),
        caption_y=px(200),
        caption_h=px(48),
        caption_font=px(23),
        help_x=margin + panel_w + px(56),
        help_y=px(265),
        help_w=width - (2 * margin) - panel_w - px(56),
        help_h=px(530),
        help_font=px(29),
        rows="".join(rows),
        scroll_x=margin + panel_w - px(18),
        scroll_y=px(252),
        scroll_w=px(8),
        scroll_h=px(626),
        message_y=px(885),
        message_h=px(42),
        message_font=px(18),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(19),
    )


def _live_stream_skin():
    """Unified live-channel list with a decoder-0 PIG preview."""
    width, height, px = _scale()
    favorite_star = FAVORITE_STAR_PATH.replace("&", "&amp;").replace(
        '"', "&quot;"
    )
    margin = px(64)
    footer_y = height - px(92)
    panel_y = px(65)
    panel_h = px(875)
    left_x = margin
    left_w = px(865)
    right_x = left_x + left_w + px(26)
    right_w = width - right_x - margin
    row_y = tuple(128 + (index * 62) for index in range(LIVE_STREAM_PAGE_SIZE))
    rows = []
    for index, y_value in enumerate(row_y):
        y = px(y_value)
        rows.append(
            """
    <widget name="stream_row_bg_{index}" position="{row_x},{row_y}"
            size="{row_w},{row_h}" font="Regular;1"
            backgroundColor="#111D31" transparent="0" zPosition="1" />
    <widget name="stream_picon_{index}" position="{picon_x},{picon_y}"
            size="{picon_w},{picon_h}" alphatest="blend" scale="1"
            zPosition="6" />
    <widget name="stream_number_{index}" position="{number_x},{row_y}"
            size="{number_w},{row_h}" font="Regular;{number_font}"
            foregroundColor="#D9E2F1" transparent="1" zPosition="3"
            valign="center" halign="right" />
    <widget name="stream_name_{index}" position="{name_x},{row_y}"
            size="{name_w},{row_h}" font="Regular;{name_font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="3"
            valign="center" halign="left" noWrap="1" />
    <widget name="stream_favorite_{index}"
            position="{favorite_x},{favorite_y}"
            size="{favorite_size},{favorite_size}"
            pixmap="{favorite_star}" alphatest="blend" scale="1"
            zPosition="6" />
    <widget name="stream_focus_{index}_fill" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_box_h}" font="Regular;1"
            backgroundColor="#172A45" transparent="0" zPosition="2" />
    <widget name="stream_focus_{index}_top" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#8B5CF6" transparent="0" zPosition="4" />
    <widget name="stream_focus_{index}_bottom" position="{focus_x},{focus_bottom_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#8B5CF6" transparent="0" zPosition="4" />
    <widget name="stream_focus_{index}_left" position="{focus_x},{focus_y}"
            size="{focus_h},{focus_box_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="stream_focus_{index}_right" position="{focus_right_x},{focus_y}"
            size="{focus_h},{focus_box_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="4" />""".format(
                index=index,
                row_x=left_x + px(30),
                row_y=y,
                row_w=left_w - px(60),
                row_h=px(55),
                picon_x=left_x + px(39),
                picon_y=y + px(6),
                picon_w=px(43),
                picon_h=px(43),
                number_x=left_x + px(88),
                number_w=px(80),
                number_font=px(19),
                name_x=left_x + px(175),
                name_w=left_w - px(270),
                name_font=px(22),
                favorite_x=left_x + left_w - px(72),
                favorite_y=y + px(13),
                favorite_size=px(28),
                favorite_star=favorite_star,
                focus_x=left_x + px(28),
                focus_y=y - px(2),
                focus_w=left_w - px(56),
                focus_h=px(3),
                focus_bottom_y=y + px(54),
                focus_box_h=px(59),
                focus_right_x=left_x + left_w - px(31),
            )
        )

    preview_x = right_x + px(24)
    preview_w = right_w - px(48)
    return """
<screen name="GTStreamListScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#020617">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="0,0" size="1,1" font="Regular;1"
            transparent="1" />
    <widget name="screen_label" position="0,0" size="1,1" font="Regular;1"
            transparent="1" />
    <widget name="left_panel" position="{left_x},{panel_y}" size="{left_w},{panel_h}"
            font="Regular;1" backgroundColor="#0D1729" transparent="0" />
    <widget name="right_panel" position="{right_x},{panel_y}" size="{right_w},{panel_h}"
            font="Regular;1" backgroundColor="#0D1729" transparent="0" />
    <widget name="left_panel_accent_cyan" position="{left_x},{panel_y}"
            size="{left_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="left_panel_accent_magenta" position="{left_accent_x},{panel_y}"
            size="{left_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="2" />
    <widget name="right_panel_accent_cyan" position="{right_x},{panel_y}"
            size="{right_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="right_panel_accent_magenta" position="{right_accent_x},{panel_y}"
            size="{right_accent_w},{panel_accent_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="2" />
    <widget name="list_accent" position="{left_x},{panel_y}" size="{accent_w},{panel_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="breadcrumb" position="0,0" size="1,1" font="Regular;1"
            transparent="1" />
    <widget name="list_caption" position="{crumb_x},{caption_y}"
            size="{crumb_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" />
    <widget name="list_caption_accent" position="{crumb_x},{caption_accent_y}"
            size="{caption_accent_w},{caption_accent_h}" font="Regular;1"
            backgroundColor="#A855F7" transparent="0" />
    {rows}
    <widget name="list_scroll_track" position="{list_scroll_x},{list_scroll_y}"
            size="{list_scroll_w},{list_scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb" position="{list_scroll_x},{list_scroll_y}"
            size="{list_scroll_w},{list_scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="epg_ribbon" position="{preview_x},{ribbon_y}"
            size="{preview_w},{ribbon_h}" font="Regular;1"
            backgroundColor="#0F1D33" transparent="0" zPosition="1" />
    <widget name="ribbon_now_caption" position="{ribbon_now_x},{ribbon_caption_y}"
            size="{ribbon_caption_w},{ribbon_caption_h}" font="Regular;{ribbon_caption_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" zPosition="3" />
    <widget name="ribbon_channel" position="{ribbon_now_x},{ribbon_channel_y}"
            size="{ribbon_channel_w},{ribbon_title_h}" font="Regular;{ribbon_channel_font}"
            foregroundColor="#A855F7" transparent="1"
            valign="center" halign="left" noWrap="1" zPosition="3" />
    <widget name="ribbon_now_title" position="{ribbon_title_x},{ribbon_title_y}"
            size="{ribbon_title_w},{ribbon_title_h}" font="Regular;{ribbon_title_font}"
            foregroundColor="#F8FAFC" transparent="1"
            valign="center" halign="left" noWrap="1" zPosition="3" />
    <widget name="ribbon_now_time" position="{ribbon_time_x},{ribbon_time_y}"
            size="{ribbon_time_w},{ribbon_time_h}" font="Regular;{ribbon_time_font}"
            foregroundColor="#CBD5E1" transparent="1"
            valign="center" halign="left" zPosition="3" />
    <widget name="epg_progress_bg" position="{progress_x},{progress_y}"
            size="{progress_w},{progress_h}" font="Regular;1"
            backgroundColor="#334155" transparent="0" zPosition="2" />
    <widget name="epg_progress_fill" position="{progress_x},{progress_y}"
            size="{progress_w},{progress_h}" font="Regular;1"
            backgroundColor="#A855F7" transparent="0" zPosition="3" />
    <widget name="epg_progress" position="{progress_x},{progress_text_y}"
            size="{progress_w},{progress_text_h}" font="Regular;{progress_font}"
            foregroundColor="#C084FC" transparent="1"
            valign="center" halign="right" zPosition="4" />
    <widget name="ribbon_divider" position="{ribbon_divider_x},{ribbon_divider_y}"
            size="{ribbon_divider_w},{ribbon_divider_h}" font="Regular;1"
            backgroundColor="#334155" transparent="0" zPosition="2" />
    <widget name="ribbon_next_caption" position="{ribbon_next_x},{ribbon_next_caption_y}"
            size="{ribbon_caption_w},{ribbon_caption_h}" font="Regular;{ribbon_caption_font}"
            foregroundColor="#D946EF" transparent="1"
            valign="center" halign="left" zPosition="3" />
    <widget name="ribbon_clock" position="{ribbon_clock_x},{ribbon_channel_y}"
            size="{ribbon_clock_w},{ribbon_caption_h}" font="Regular;{ribbon_clock_font}"
            foregroundColor="#94A3B8" transparent="1"
            valign="center" halign="right" zPosition="3" />
    <widget name="ribbon_next_title" position="{ribbon_next_title_x},{ribbon_next_title_y}"
            size="{ribbon_next_title_w},{ribbon_next_title_h}" font="Regular;{ribbon_next_title_font}"
            foregroundColor="#F8FAFC" transparent="1"
            valign="center" halign="left" noWrap="1" zPosition="3" />
    <widget name="ribbon_next_time" position="{ribbon_next_time_x},{ribbon_next_time_y}"
            size="{ribbon_time_w},{ribbon_next_time_h}" font="Regular;{ribbon_time_font}"
            foregroundColor="#CBD5E1" transparent="1"
            valign="center" halign="left" zPosition="3" />
    <widget name="preview_frame" position="{preview_frame_x},{preview_frame_y}"
            size="{preview_frame_w},{preview_frame_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="1" />
    <widget name="preview_video" position="{preview_video_x},{preview_video_y}"
            size="{preview_video_w},{preview_video_h}"
            backgroundColor="transparent" zPosition="2" />
    <widget name="preview_mask" position="{preview_video_x},{preview_video_y}"
            size="{preview_video_w},{preview_video_h}" font="Regular;1"
            backgroundColor="#07101F" transparent="0" zPosition="4" />
    <widget name="preview_hint" position="{preview_video_x},{preview_hint_y}"
            size="{preview_video_w},{preview_hint_h}" font="Regular;{preview_hint_font}"
            foregroundColor="#94A3B8" transparent="1"
            valign="bottom" halign="center" zPosition="9" />
    <widget name="epg_panel" position="{preview_x},{epg_y}"
            size="{preview_w},{epg_h}" font="Regular;1"
            backgroundColor="#0F1D33" transparent="0" zPosition="1" />
    <widget name="epg_title" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#FFFFFF" transparent="1" valign="center" halign="left" zPosition="3" />
    <widget name="epg_now_time" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#E2E8F0" transparent="1" valign="center" halign="left" zPosition="3" />
    <widget name="epg_now_title" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#F8FAFC" transparent="1" valign="center" halign="left" zPosition="3" />
    <widget name="epg_now_status" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#A855F7" transparent="1" valign="center" halign="right" zPosition="3" />
    <widget name="epg_next_time" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#E2E8F0" transparent="1" valign="center" halign="left" zPosition="3" />
    <widget name="epg_next_title" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#F8FAFC" transparent="1" valign="center" halign="left" zPosition="3" />
    <widget name="epg_next_status" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#94A3B8" transparent="1" valign="center" halign="right" zPosition="3" />
    <widget name="epg_third_time" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#E2E8F0" transparent="1" valign="center" halign="left" zPosition="3" />
    <widget name="epg_third_title" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#F8FAFC" transparent="1" valign="center" halign="left" zPosition="3" />
    <widget name="epg_third_status" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#64748B" transparent="1" valign="center" halign="right" zPosition="3" />
    <widget name="epg_fourth_time" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#E2E8F0" transparent="1" valign="center" halign="left" zPosition="3" />
    <widget name="epg_fourth_title" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#F8FAFC" transparent="1" valign="center" halign="left" zPosition="3" />
    <widget name="epg_fourth_status" position="0,0"
            size="1,1" font="Regular;1"
            foregroundColor="#64748B" transparent="1" valign="center" halign="right" zPosition="3" />
    <widget name="summary_caption" position="{summary_x},{summary_caption_y}"
            size="{epg_inner_w},{summary_caption_h}" font="Regular;{summary_caption_font}"
            foregroundColor="#38BDF8" transparent="1" valign="center" halign="left" zPosition="3" />
    <widget name="summary" position="{summary_x},{summary_y}"
            size="{summary_w},{summary_h}" font="Regular;{summary_font}"
            foregroundColor="#CBD5E1" transparent="1" valign="top" halign="left" zPosition="3" />
    <widget name="summary_scroll" position="{scroll_x},{summary_y}"
            size="{scroll_w},{summary_h}" font="Regular;{scroll_font}"
            foregroundColor="#94A3B8" transparent="1" valign="center" halign="center" zPosition="3" />
    <widget name="output_badge" position="{output_badge_x},{badge_y}"
            size="{output_badge_w},{badge_h}" font="Regular;{badge_font}"
            foregroundColor="#CBD5E1" backgroundColor="#111D31" transparent="0"
            valign="center" halign="center" zPosition="3" />
    <widget name="engine_badge" position="{engine_badge_x},{badge_y}"
            size="{engine_badge_w},{badge_h}" font="Regular;{badge_font}"
            foregroundColor="#CBD5E1" backgroundColor="#111D31" transparent="0"
            valign="center" halign="center" zPosition="3" />
    <widget name="message" position="{message_x},{message_y}" size="{message_w},{message_h}"
            font="Regular;{message_font}" foregroundColor="#9AA8BE" transparent="1"
            valign="center" halign="right" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0" valign="center" halign="center" />
    <widget name="content_panel" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="items" position="0,0" size="1,1" font="Regular;1" transparent="1" />
</screen>
""".format(
        width=width, height=height,
        accent_h=px(5),
        left_x=left_x, left_w=left_w, right_x=right_x, right_w=right_w,
        left_accent_w=int(left_w / 2),
        left_accent_x=left_x + int(left_w / 2),
        right_accent_w=int(right_w / 2),
        right_accent_x=right_x + int(right_w / 2),
        panel_accent_h=px(3),
        panel_y=panel_y, panel_h=panel_h, accent_w=px(7),
        crumb_x=left_x + px(46), crumb_w=left_w - px(80),
        caption_y=px(76), caption_h=px(38), caption_font=px(25),
        caption_accent_y=px(116), caption_accent_w=px(190),
        caption_accent_h=px(3), rows="".join(rows),
        list_scroll_x=left_x + left_w - px(18),
        list_scroll_y=px(128), list_scroll_w=px(8), list_scroll_h=px(737),
        ribbon_y=px(65), ribbon_h=px(112),
        ribbon_now_x=preview_x + px(18), ribbon_caption_y=px(96),
        ribbon_caption_w=px(95), ribbon_caption_h=px(28),
        ribbon_caption_font=px(18), ribbon_channel_w=preview_w - px(150),
        ribbon_channel_font=px(18), ribbon_channel_y=px(68),
        ribbon_title_y=px(96),
        ribbon_title_h=px(28), ribbon_title_x=preview_x + px(255),
        ribbon_title_w=preview_w - px(280), ribbon_title_font=px(21),
        ribbon_time_x=preview_x + px(120), ribbon_time_y=px(96),
        ribbon_time_w=px(120),
        ribbon_time_h=px(28), ribbon_time_font=px(18),
        ribbon_divider_x=preview_x + px(18),
        ribbon_divider_y=px(127), ribbon_divider_w=preview_w - px(36),
        ribbon_divider_h=px(1),
        ribbon_next_x=preview_x + px(18),
        ribbon_next_w=preview_w - px(36),
        ribbon_clock_x=preview_x + preview_w - px(105),
        ribbon_clock_w=px(85), ribbon_clock_font=px(18),
        ribbon_next_caption_y=px(136),
        ribbon_next_title_x=preview_x + px(255),
        ribbon_next_title_w=preview_w - px(280),
        ribbon_next_title_y=px(136), ribbon_next_title_h=px(28),
        ribbon_next_title_font=px(20),
        ribbon_next_time_x=preview_x + px(120), ribbon_next_time_y=px(136),
        ribbon_next_time_h=px(28),
        preview_frame_x=preview_x + px(18), preview_frame_y=px(185),
        preview_frame_w=preview_w - px(36), preview_frame_h=px(463),
        preview_video_x=preview_x + px(24), preview_video_y=px(190),
        preview_video_w=preview_w - px(48), preview_video_h=px(453),
        preview_hint_y=px(553), preview_hint_h=px(60),
        preview_hint_font=px(19),
        preview_x=preview_x, preview_w=preview_w,
        epg_y=px(690), epg_h=px(178),
        epg_inner_x=preview_x + px(285), epg_inner_w=preview_w - px(315),
        epg_title_y=px(655), epg_title_h=px(35), epg_title_font=px(23),
        epg_now_y=px(220), epg_next_y=px(705), epg_third_y=px(755),
        epg_fourth_y=px(805), epg_time_w=px(110), epg_row_h=px(40),
        epg_font=px(21), epg_title_x=preview_x + px(405),
        epg_name_w=preview_w - px(560),
        epg_status_x=preview_x + preview_w - px(145), epg_status_w=px(120),
        epg_status_font=px(18),
        upcoming_time_x=preview_x + px(26),
        upcoming_title_x=preview_x + px(145),
        upcoming_status_x=preview_x + preview_w - px(145),
        progress_x=preview_x + px(24), progress_y=px(655),
        progress_w=preview_w - px(48), progress_h=px(9),
        progress_text_y=px(667), progress_text_h=px(23), progress_font=px(17),
        summary_x=preview_x + px(24), summary_caption_y=px(700),
        summary_caption_h=px(27), summary_caption_font=px(23),
        summary_y=px(738), summary_w=preview_w - px(90), summary_h=px(112),
        summary_font=px(24), scroll_x=preview_x + preview_w - px(62),
        scroll_w=px(42), scroll_font=px(23),
        output_badge_x=preview_x + px(24), output_badge_w=px(85),
        engine_badge_x=preview_x + px(130), engine_badge_w=px(245),
        badge_y=px(895), badge_h=px(36), badge_font=px(18),
        message_x=left_x + px(40), message_y=px(870), message_w=left_w - px(80),
        message_h=px(42), message_font=px(18), footer_y=footer_y,
        footer_h=height - footer_y, footer_font=px(18),
    )


def _player_skin():
    width, height = _desktop_size()
    return """
<screen name="GTExternalPlayerScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#FF000000" transparent="1"
        zPosition="-1" />
""".format(width=width, height=height)


def _cinematic_scroll_geometries(width, height, px):
    """Keep runtime cursors pixel-identical to the cinematic skin."""
    list_geometry = (
        px(8 + 348 - 14),
        px(4 + 54),
        px(8),
        (
            CINEMATIC_CATEGORY_PAGE_SIZE
            * px(CINEMATIC_CATEGORY_ROW_HEIGHT)
        ) - px(8),
    )
    rail_x = px(365)
    poster_geometry = (
        rail_x,
        height - px(90) - px(18),
        width - rail_x,
        px(6),
    )
    return list_geometry, poster_geometry


def _movie_browser_skin(cinematic=False):
    """Remote-first VOD library: categories, poster rail and film details."""
    width, height, px = _scale()
    favorite_star = FAVORITE_STAR_PATH.replace("&", "&amp;").replace(
        '"', "&quot;"
    )
    if cinematic:
        footer_y = height - px(90)
        category_x = px(8)
        category_y = px(4)
        category_w = px(348)
        hero_x = px(365)
        # Paint the category surface up to the physical left edge without
        # moving its established text, focus rows or scrollbar coordinates.
        # R116 also closes the old nine-pixel design seam so the rail and hero
        # meet on exactly the same physical column at every resolution.
        category_panel_x = 0
        category_panel_w = hero_x
        category_h = footer_y - category_y - px(4)
        category_row_h = px(CINEMATIC_CATEGORY_ROW_HEIGHT)
        category_list_y = category_y + px(54)
        category_scroll_h = (
            CINEMATIC_CATEGORY_PAGE_SIZE * category_row_h
        ) - px(8)
        # One continuous hero reaches the physical right edge. Copy stays in
        # a bounded text column over the left-side scrim.
        hero_y = 0
        hero_w = width - hero_x
        # The category/count caption is part of the hero.  End the artwork at
        # the first poster pixel so there is no detached navy title band.
        hero_h = px(616)
        detail_x = hero_x
        detail_y = 0
        detail_w = px(530)
        detail_h = hero_h
        shadow_offset = 0
        artwork_x = hero_x
        artwork_y = hero_y
        artwork_w = hero_w
        artwork_h = hero_h
        people_x = hero_x
        people_y = px(500)
        people_w = hero_w
        people_h = px(54)
        rail_x = hero_x
        rail_w = hero_w
        rail_caption_y = px(574)
        rail_y = px(616)
        gap = px(18)
        tile_w = int((rail_w - (gap * 4)) / 5)
        tile_image_h = px(276)
        tile_h = px(344)
        cinematic_scrim = CINEMATIC_SCRIM_PATH.replace(
            "&", "&amp;"
        ).replace('"', "&quot;")
        cinematic_fallback_shadow = (
            CINEMATIC_FALLBACK_SHADOW_PATH.replace(
                "&", "&amp;"
            ).replace('"', "&quot;")
        )

        category_rows = []
        for index in range(CINEMATIC_CATEGORY_PAGE_SIZE):
            row_y = category_list_y + (index * category_row_h)
            category_rows.append(
                """
    <widget name="movie_category_focus_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#172A45"
            transparent="0" zPosition="2" />
    <widget name="movie_category_marker_{index}" position="{x},{y}"
            size="{marker_w},{h}" font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="3" />
    <widget name="movie_category_{index}" position="{text_x},{y}"
            size="{text_w},{h}" font="Regular;{font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="4"
            valign="center" halign="left" noWrap="1" />""".format(
                    index=index,
                    x=category_x + px(14),
                    y=row_y,
                    w=category_w - px(28),
                    h=category_row_h - px(8),
                    marker_w=px(5),
                    text_x=category_x + px(34),
                    text_w=category_w - px(54),
                    font=px(21),
                )
            )

        poster_tiles = []
        for index in range(5):
            tile_x = rail_x + (index * (tile_w + gap))
            poster_tiles.append(
                """
    <widget name="movie_tile_bg_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#111D31"
            transparent="0" zPosition="1" />
    <widget name="movie_tile_focus_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="2" />
    <widget name="movie_tile_inner_{index}" position="{inner_x},{inner_y}"
            size="{inner_w},{inner_h}" font="Regular;1"
            backgroundColor="#081321" transparent="0" zPosition="3" />
    <widget name="movie_tile_placeholder_{index}"
            position="{image_x},{image_y}" size="{image_w},{image_h}"
            font="Regular;{placeholder_font}" foregroundColor="#22D3EE"
            backgroundColor="#101B2D" transparent="0" zPosition="4"
            valign="center" halign="center" />
    <widget name="movie_tile_poster_{index}"
            position="{image_x},{image_y}" size="{image_w},{image_h}"
            alphatest="blend" scale="1" zPosition="5" />
    <widget name="movie_tile_favorite_{index}"
            position="{favorite_x},{favorite_y}"
            size="{favorite_size},{favorite_size}"
            pixmap="{favorite_star}" alphatest="blend" scale="1"
            zPosition="7" />
    <widget name="movie_tile_title_{index}"
            position="{title_x},{title_y}" size="{title_w},{title_h}"
            font="Regular;{title_font}" foregroundColor="#F8FAFC"
            transparent="1" zPosition="6" valign="center"
            halign="center" noWrap="0" />""".format(
                    index=index,
                    x=tile_x,
                    y=rail_y,
                    w=tile_w,
                    h=tile_h,
                    inner_x=tile_x + px(4),
                    inner_y=rail_y + px(4),
                    inner_w=tile_w - px(8),
                    inner_h=tile_h - px(8),
                    image_x=tile_x + px(10),
                    image_y=rail_y + px(10),
                    image_w=tile_w - px(20),
                    image_h=tile_image_h,
                    placeholder_font=px(30),
                    favorite_x=tile_x + tile_w - px(45),
                    favorite_y=rail_y + px(16),
                    favorite_size=px(31),
                    favorite_star=favorite_star,
                    title_x=tile_x + px(8),
                    title_y=rail_y + tile_image_h + px(10),
                    title_w=tile_w - px(16),
                    title_h=tile_h - tile_image_h - px(14),
                    title_font=px(18),
                )
            )

        return """
<screen name="GTContentBrowserScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#020617">
    <widget name="movie_video_guard" position="0,0"
            size="{width},{height}" font="Regular;1"
            backgroundColor="#020617" transparent="0" zPosition="-30" />
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="-20" />
    <widget name="top_accent" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="header" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="movie_server" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />

    <widget name="movie_hero_panel" position="{hero_x},{hero_y}"
            size="{hero_w},{hero_h}" font="Regular;1"
            transparent="1" zPosition="-12" />
    <widget name="movie_backdrop" position="{hero_x},{hero_y}"
            size="{hero_w},{hero_h}" alphatest="blend" scale="1"
            zPosition="-10" />
    <widget name="movie_backdrop_scrim" position="{hero_x},{hero_y}"
            size="{hero_w},{hero_h}" pixmap="{cinematic_scrim}"
            alphatest="blend" scale="1" zPosition="-9" />
    <widget name="movie_detail_shadow"
            position="{detail_shadow_x},{detail_shadow_y}"
            size="{detail_shadow_w},{detail_shadow_h}"
            pixmap="{cinematic_fallback_shadow}" alphatest="blend"
            scale="1" zPosition="-8" />
    <widget name="movie_detail_panel" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="movie_detail_accent" position="{text_x},{divider_y}"
            size="{text_w},{divider_h}" font="Regular;1"
            backgroundColor="#164E63" transparent="0" zPosition="1" />
    <widget name="movie_meta_divider"
            position="{meta_divider_x},{meta_divider_y}"
            size="{meta_divider_w},{meta_divider_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="1" />
    <widget name="movie_people_shadow"
            position="{people_shadow_x},{people_shadow_y}"
            size="{people_shadow_w},{people_shadow_h}"
            pixmap="{cinematic_fallback_shadow}" alphatest="blend"
            scale="1" zPosition="-8" />
    <widget name="movie_rail_shadow"
            position="{rail_shadow_x},{rail_shadow_y}"
            size="{rail_shadow_w},{rail_shadow_h}"
            pixmap="{cinematic_fallback_shadow}" alphatest="blend"
            scale="1" zPosition="-8" />
    <widget name="movie_people_panel" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="movie_poster_frame" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="movie_poster_placeholder" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="movie_poster" position="0,0" size="1,1"
            alphatest="blend" scale="1" />

    <widget name="movie_title" position="{text_x},{title_y}"
            size="{text_w},{title_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" noWrap="0"
            valign="center" halign="left" zPosition="2" />
    <widget name="movie_rating" position="{text_x},{rating_y}"
            size="{rating_w},{rating_h}" font="Regular;{rating_font}"
            foregroundColor="#FACC15" transparent="1"
            valign="center" halign="left" noWrap="1" zPosition="2" />
    <widget name="movie_meta" position="{meta_x},{rating_y}"
            size="{meta_w},{rating_h}" font="Regular;{meta_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" noWrap="1" zPosition="2" />
    <widget name="movie_genre" position="{text_x},{genre_y}"
            size="{text_w},{genre_h}" font="Regular;{genre_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" noWrap="1" zPosition="2" />
    <widget name="movie_plot_caption" position="{text_x},{caption_y}"
            size="{text_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#FACC15" transparent="1"
            valign="center" halign="left" zPosition="2" />
    <widget name="movie_plot" position="{text_x},{plot_y}"
            size="{text_w},{plot_h}" font="Regular;{plot_font}"
            foregroundColor="#FFFFFF" transparent="1" noWrap="0"
            scrollbarMode="showNever" valign="top" halign="left"
            zPosition="2" />
    <widget name="movie_people" position="{people_text_x},{people_y}"
            size="{people_text_w},{people_h}" font="Regular;{people_font}"
            foregroundColor="#FFFFFF" transparent="1" noWrap="0"
            valign="center" halign="left" zPosition="2" />

    <widget name="movie_category_panel" position="{category_panel_x},{category_y}"
            size="{category_panel_w},{category_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="movie_category_accent" position="{category_panel_x},{category_y}"
            size="{category_panel_w},{line_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="movie_category_caption"
            position="{category_caption_x},{category_caption_y}"
            size="{category_caption_w},{category_caption_h}"
            font="Regular;{caption_font}" foregroundColor="#22D3EE"
            transparent="1" valign="center" halign="left" zPosition="4" />
    {category_rows}
    <widget name="list_scroll_track"
            position="{category_scroll_x},{category_scroll_y}"
            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb"
            position="{category_scroll_x},{category_scroll_y}"
            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="message" position="{message_x},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#94A3B8" transparent="1"
            valign="center" halign="center" />

    <widget name="movie_rail_caption" position="{rail_x},{rail_caption_y}"
            size="{rail_w},{rail_caption_h}" font="Regular;{caption_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" />
    {poster_tiles}
    <widget name="poster_scroll_track"
            position="{rail_x},{scroll_y}" size="{rail_w},{scroll_h}"
            font="Regular;1" backgroundColor="#26344E"
            transparent="0" zPosition="7" />
    <widget name="poster_scroll_thumb"
            position="{rail_x},{scroll_y}" size="{rail_w},{scroll_h}"
            font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="8" />

    <widget name="footer" position="0,{footer_y}"
            size="{width},{footer_h}" font="Regular;{footer_font}"
            foregroundColor="#C8D2E3" backgroundColor="#080E1A"
            transparent="0" valign="center" halign="center" />
    <widget name="screen_label" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="content_panel" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="list_accent" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="breadcrumb" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="items" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
</screen>
""".format(
            width=width,
            height=height,
            footer_y=footer_y,
            footer_h=height - footer_y,
            footer_font=px(18),
            hero_x=hero_x,
            hero_y=hero_y,
            hero_w=hero_w,
            hero_h=hero_h,
            artwork_x=artwork_x,
            artwork_y=artwork_y,
            artwork_w=artwork_w,
            artwork_h=artwork_h,
            cinematic_scrim=cinematic_scrim,
            cinematic_fallback_shadow=cinematic_fallback_shadow,
            detail_x=detail_x,
            detail_y=detail_y,
            detail_w=detail_w,
            detail_h=detail_h,
            detail_shadow_x=detail_x + shadow_offset,
            detail_shadow_y=detail_y + shadow_offset,
            detail_shadow_w=min(hero_w, detail_w + px(120)),
            detail_shadow_h=people_y,
            text_x=detail_x + px(38),
            text_w=detail_w - px(76),
            title_y=px(50),
            title_h=px(72),
            title_font=px(38),
            rating_y=px(132),
            rating_w=px(145),
            rating_h=px(42),
            rating_font=px(25),
            meta_divider_x=detail_x + px(188),
            meta_divider_y=px(140),
            meta_divider_w=px(3),
            meta_divider_h=px(26),
            meta_x=detail_x + px(213),
            meta_w=detail_w - px(251),
            meta_font=px(22),
            genre_y=px(176),
            genre_h=px(38),
            genre_font=px(21),
            divider_y=px(214),
            divider_h=px(3),
            caption_y=px(222),
            caption_h=px(38),
            caption_font=px(21),
            plot_y=px(266),
            plot_h=px(220),
            plot_font=px(22),
            people_x=people_x,
            people_y=people_y,
            people_w=people_w,
            people_h=people_h,
            people_shadow_x=people_x + px(7),
            people_shadow_y=people_y + px(7),
            people_shadow_w=max(px(1), people_w - px(14)),
            people_shadow_h=max(px(1), people_h - px(14)),
            people_text_x=people_x + px(32),
            people_text_w=people_w - px(64),
            people_font=px(18),
            rail_shadow_x=rail_x,
            rail_shadow_y=rail_caption_y,
            rail_shadow_w=min(rail_w, px(720)),
            rail_shadow_h=rail_y - rail_caption_y,
            category_x=category_x,
            category_panel_x=category_panel_x,
            category_panel_w=category_panel_w,
            category_y=category_y,
            category_w=category_w,
            category_h=category_h,
            line_h=px(4),
            category_caption_x=category_x + px(24),
            category_caption_y=category_y + px(8),
            category_caption_w=category_w - px(48),
            category_caption_h=px(40),
            category_rows="".join(category_rows),
            category_scroll_x=category_x + category_w - px(14),
            category_scroll_y=category_list_y,
            category_scroll_w=px(8),
            category_scroll_h=category_scroll_h,
            message_x=category_x + px(14),
            message_y=category_list_y + category_scroll_h + px(4),
            message_w=category_w - px(28),
            message_h=max(
                px(24),
                footer_y - category_list_y - category_scroll_h - px(8),
            ),
            message_font=px(17),
            rail_x=rail_x,
            rail_w=rail_w,
            rail_caption_y=rail_caption_y,
            rail_caption_h=px(36),
            poster_tiles="".join(poster_tiles),
            scroll_y=footer_y - px(18),
            scroll_h=px(6),
        )
    footer_y = height - px(82)
    category_x = px(42)
    category_y = px(118)
    category_w = px(340)
    category_row_h = px(72)
    category_h = footer_y - category_y - px(28)
    category_scroll_y = category_y + px(48)
    category_scroll_h = (
        MOVIE_CATEGORY_PAGE_SIZE * category_row_h
    ) - px(8)
    category_message_y = category_scroll_y + category_scroll_h + px(10)
    poster_x = px(430)
    poster_y = px(122)
    poster_w = px(330)
    poster_h = px(470)
    detail_x = px(805)
    detail_y = px(122)
    detail_w = width - detail_x - px(42)
    rail_y = px(640)
    rail_x = poster_x
    rail_w = width - rail_x - px(42)
    gap = px(18)
    tile_w = int((rail_w - (gap * 4)) / 5)
    tile_h = px(255)
    cinematic_base_widget = ""
    cinematic_widgets = ""
    detail_panel_color = "#0D1729"
    if cinematic:
        cinematic_scrim = CINEMATIC_SCRIM_PATH.replace(
            "&",
            "&amp;",
        ).replace('"', "&quot;")
        cinematic_x = poster_x - px(5)
        cinematic_y = poster_y - px(5)
        cinematic_w = width - cinematic_x - px(42)
        cinematic_h = px(490)
        cinematic_base_widget = """
    <widget name="movie_video_guard" position="0,0"
            size="{w},{h}" font="Regular;1"
            backgroundColor="#020617" transparent="0"
            zPosition="0" />""".format(w=width, h=height)
        cinematic_widgets = """
    <widget name="movie_backdrop" position="{x},{y}"
            size="{w},{h}" alphatest="blend" scale="1"
            zPosition="0" />
    <widget name="movie_backdrop_scrim" position="{x},{y}"
            size="{w},{h}" pixmap="{scrim}" alphatest="blend" scale="1"
            zPosition="0" />""".format(
            x=cinematic_x,
            y=cinematic_y,
            w=cinematic_w,
            h=cinematic_h,
            scrim=cinematic_scrim,
        )
    category_rows = []
    for index in range(MOVIE_CATEGORY_PAGE_SIZE):
        y = category_y + px(48) + (index * category_row_h)
        category_rows.append(
            """
    <widget name="movie_category_focus_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#172A45"
            transparent="0" zPosition="2" />
    <widget name="movie_category_marker_{index}" position="{x},{y}"
            size="{marker_w},{h}" font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="3" />
    <widget name="movie_category_{index}" position="{text_x},{y}"
            size="{text_w},{h}" font="Regular;{font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="4"
            valign="center" halign="left" noWrap="1" />""".format(
                index=index,
                x=category_x + px(14),
                y=y,
                w=category_w - px(28),
                h=category_row_h - px(8),
                marker_w=px(5),
                text_x=category_x + px(34),
                text_w=category_w - px(54),
                font=px(21),
            )
        )
    poster_tiles = []
    for index in range(5):
        x = rail_x + (index * (tile_w + gap))
        poster_tiles.append(
            """
    <widget name="movie_tile_bg_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#111D31"
            transparent="0" zPosition="1" />
    <widget name="movie_tile_focus_{index}" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="movie_tile_inner_{index}" position="{inner_x},{inner_y}"
            size="{inner_w},{inner_h}" font="Regular;1"
            backgroundColor="#081321" transparent="0" zPosition="3" />
    <widget name="movie_tile_placeholder_{index}" position="{image_x},{image_y}"
            size="{image_w},{image_h}" font="Regular;{placeholder_font}"
            foregroundColor="#22D3EE" backgroundColor="#101B2D"
            transparent="0" valign="center" halign="center" zPosition="4" />
    <widget name="movie_tile_poster_{index}" position="{image_x},{image_y}"
            size="{image_w},{image_h}" alphatest="blend" scale="1"
            zPosition="5" />
    <widget name="movie_tile_favorite_{index}"
            position="{favorite_x},{favorite_y}"
            size="{favorite_size},{favorite_size}"
            pixmap="{favorite_star}" alphatest="blend" scale="1"
            zPosition="7" />
    <widget name="movie_tile_title_{index}" position="{title_x},{title_y}"
            size="{title_w},{title_h}" font="Regular;{title_font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="6"
            valign="center" halign="center" noWrap="0" />""".format(
                index=index,
                x=x,
                y=rail_y,
                w=tile_w,
                h=tile_h + px(58),
                focus_x=x,
                focus_y=rail_y,
                focus_w=tile_w,
                focus_h=tile_h + px(58),
                inner_x=x + px(4),
                inner_y=rail_y + px(4),
                inner_w=tile_w - px(8),
                inner_h=tile_h + px(50),
                image_x=x + px(10),
                image_y=rail_y + px(10),
                image_w=tile_w - px(20),
                image_h=tile_h - px(18),
                favorite_x=x + tile_w - px(45),
                favorite_y=rail_y + px(16),
                favorite_size=px(31),
                favorite_star=favorite_star,
                placeholder_font=px(30),
                title_x=x + px(8),
                title_y=rail_y + tile_h,
                title_w=tile_w - px(16),
                title_h=px(53),
                title_font=px(18),
            )
        )
    return """
<screen name="GTContentBrowserScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#020617">
    {cinematic_base_widget}
    <widget name="app_bg" position="0,0" size="{width},{height}" zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{header_x},{header_y}" size="{header_w},{header_h}"
            font="Regular;{header_font}" foregroundColor="#FFFFFF"
            transparent="1" valign="center" halign="left" />
    <widget name="movie_server" position="{server_x},{header_y}"
            size="{server_w},{header_h}" font="Regular;{server_font}"
            foregroundColor="#94A3B8" transparent="1"
            valign="center" halign="right" />
    {cinematic_widgets}
    <widget name="movie_category_panel" position="{category_x},{category_y}"
            size="{category_w},{category_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="movie_category_accent" position="{category_x},{category_y}"
            size="{category_w},{line_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="movie_category_caption" position="{category_caption_x},{category_caption_y}"
            size="{category_caption_w},{category_caption_h}"
            font="Regular;{caption_font}" foregroundColor="#22D3EE"
            transparent="1" valign="center" halign="left" />
    {category_rows}
    <widget name="list_scroll_track" position="{category_scroll_x},{category_scroll_y}"
            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb" position="{category_scroll_x},{category_scroll_y}"
            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="movie_poster_frame" position="{poster_frame_x},{poster_frame_y}"
            size="{poster_frame_w},{poster_frame_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="1" />
    <widget name="movie_poster_placeholder" position="{poster_x},{poster_y}"
            size="{poster_w},{poster_h}" font="Regular;{poster_placeholder_font}"
            foregroundColor="#22D3EE" backgroundColor="#0D1729"
            transparent="0" valign="center" halign="center" zPosition="2" />
    <widget name="movie_poster" position="{poster_x},{poster_y}"
            size="{poster_w},{poster_h}" alphatest="blend" scale="1"
            zPosition="3" />
    <widget name="movie_detail_panel" position="{detail_x},{detail_y}"
            size="{detail_w},{detail_h}" font="Regular;1"
            backgroundColor="{detail_panel_color}" transparent="0" />
    <widget name="movie_detail_accent" position="{detail_x},{detail_y}"
            size="{detail_w},{line_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="2" />
    <widget name="movie_title" position="{title_x},{title_y}"
            size="{title_w},{title_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" noWrap="0"
            valign="center" halign="left" />
    <widget name="movie_rating" position="{title_x},{rating_y}"
            size="{rating_w},{rating_h}" font="Regular;{rating_font}"
            foregroundColor="#FACC15" transparent="1"
            valign="center" halign="left" />
    <widget name="movie_meta" position="{meta_x},{rating_y}"
            size="{meta_w},{rating_h}" font="Regular;{meta_font}"
            foregroundColor="#CBD5E1" transparent="1"
            valign="center" halign="left" />
    <widget name="movie_genre" position="{title_x},{genre_y}"
            size="{title_w},{genre_h}" font="Regular;{genre_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" />
    <widget name="movie_plot_caption" position="{title_x},{plot_caption_y}"
            size="{title_w},{plot_caption_h}" font="Regular;{caption_font}"
            foregroundColor="#A855F7" transparent="1"
            valign="center" halign="left" />
    <widget name="movie_plot" position="{title_x},{plot_y}"
            size="{title_w},{plot_h}" font="Regular;{plot_font}"
            foregroundColor="#E2E8F0" transparent="1" noWrap="0"
            valign="top" halign="left" />
    <widget name="movie_people" position="{title_x},{people_y}"
            size="{title_w},{people_h}" font="Regular;{people_font}"
            foregroundColor="#94A3B8" transparent="1" noWrap="0"
            valign="top" halign="left" />
    <widget name="movie_rail_caption" position="{rail_x},{rail_caption_y}"
            size="{rail_w},{rail_caption_h}" font="Regular;{caption_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" />
    {poster_tiles}
    <widget name="poster_scroll_track" position="{rail_scroll_x},{rail_scroll_y}"
            size="{rail_scroll_w},{rail_scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="7" />
    <widget name="poster_scroll_thumb" position="{rail_scroll_x},{rail_scroll_y}"
            size="{rail_scroll_w},{rail_scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="8" />
    <widget name="message" position="{category_x},{message_y}"
            size="{category_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#94A3B8" transparent="1"
            valign="center" halign="center" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
    <widget name="screen_label" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="content_panel" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="list_accent" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="breadcrumb" position="0,0" size="1,1" font="Regular;1" transparent="1" />
    <widget name="items" position="0,0" size="1,1" font="Regular;1" transparent="1" />
</screen>
""".format(
        width=width, height=height, accent_h=px(5), header_x=px(50),
        header_y=px(22), header_w=px(600), header_h=px(72), header_font=px(38),
        server_x=px(700), server_w=width - px(750), server_font=px(19),
        cinematic_base_widget=cinematic_base_widget,
        cinematic_widgets=cinematic_widgets,
        category_x=category_x, category_y=category_y, category_w=category_w,
        category_h=category_h, line_h=px(4),
        category_caption_x=category_x + px(24), category_caption_y=category_y + px(8),
        category_caption_w=category_w - px(48), category_caption_h=px(40),
        caption_font=px(21), category_rows="".join(category_rows),
        category_scroll_x=category_x + category_w - px(14),
        category_scroll_y=category_scroll_y,
        category_scroll_w=px(8), category_scroll_h=category_scroll_h,
        poster_frame_x=poster_x - px(5), poster_frame_y=poster_y - px(5),
        poster_frame_w=poster_w + px(10), poster_frame_h=poster_h + px(10),
        poster_x=poster_x, poster_y=poster_y, poster_w=poster_w, poster_h=poster_h,
        poster_placeholder_font=px(36), detail_x=detail_x, detail_y=detail_y,
        detail_w=detail_w, detail_h=px(480),
        detail_panel_color=detail_panel_color,
        title_x=detail_x + px(32),
        title_y=detail_y + px(22), title_w=detail_w - px(64), title_h=px(102),
        title_font=px(38), rating_y=detail_y + px(132), rating_w=px(220),
        rating_h=px(40), rating_font=px(25), meta_x=detail_x + px(255),
        meta_w=detail_w - px(290), meta_font=px(21), genre_y=detail_y + px(176),
        genre_h=px(38), genre_font=px(20), plot_caption_y=detail_y + px(222),
        plot_caption_h=px(34), plot_y=detail_y + px(260), plot_h=px(125),
        plot_font=px(22), people_y=detail_y + px(394), people_h=px(66),
        people_font=px(18), rail_x=rail_x, rail_w=rail_w,
        rail_caption_y=px(603), rail_caption_h=px(34),
        poster_tiles="".join(poster_tiles), rail_scroll_x=rail_x,
        rail_scroll_y=footer_y - px(25), rail_scroll_w=rail_w,
        rail_scroll_h=px(6), message_y=category_message_y,
        message_h=px(52), message_font=px(17), footer_y=footer_y,
        footer_h=height - footer_y, footer_font=px(18),
    )


def _all_movies_search_skin():
    """Lightweight full-catalog movie search without artwork workers."""
    width, height, px = _scale()
    footer_y = height - px(82)
    query_y = px(108)
    query_h = px(76)
    list_x = px(42)
    list_y = px(205)
    list_w = px(1120)
    list_h = footer_y - list_y - px(28)
    detail_x = px(1200)
    detail_y = list_y
    detail_w = width - detail_x - px(42)
    detail_h = list_h
    row_y = list_y + px(62)
    row_h = px(64)
    rows = []
    for index in range(ALL_MOVIES_SEARCH_PAGE_SIZE):
        y = row_y + (index * row_h)
        rows.append(
            """
    <widget name="catalog_result_focus_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#172A45"
            transparent="0" zPosition="2" />
    <widget name="catalog_result_marker_{index}" position="{x},{y}"
            size="{marker_w},{h}" font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="3" />
    <widget name="catalog_result_{index}" position="{text_x},{y}"
            size="{text_w},{h}" font="Regular;{font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="4"
            noWrap="1" valign="center" halign="left" />""".format(
                index=index,
                x=list_x + px(18),
                y=y,
                w=list_w - px(44),
                h=row_h - px(7),
                marker_w=px(5),
                text_x=list_x + px(40),
                text_w=list_w - px(84),
                font=px(21),
            )
        )
    return """
<screen name="GTAllMoviesSearchScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#020617">
    <widget name="app_bg" position="0,0" size="{width},{height}" zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{header_x},{header_y}"
            size="{header_w},{header_h}" font="Regular;{header_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" />
    <widget name="server" position="{server_x},{header_y}"
            size="{server_w},{header_h}" font="Regular;{server_font}"
            foregroundColor="#94A3B8" transparent="1"
            valign="center" halign="right" />
    <widget name="query_panel" position="{list_x},{query_y}"
            size="{query_w},{query_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="query_accent" position="{list_x},{query_y}"
            size="{line_w},{query_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="2" />
    <widget name="query_caption" position="{query_caption_x},{query_y}"
            size="{query_caption_w},{query_h}" font="Regular;{caption_font}"
            foregroundColor="#D8B4FE" transparent="1"
            valign="center" halign="left" />
    <widget name="query_text" position="{query_text_x},{query_y}"
            size="{query_text_w},{query_h}" font="Regular;{query_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" />
    <widget name="result_panel" position="{list_x},{list_y}"
            size="{list_w},{list_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="result_accent" position="{list_x},{list_y}"
            size="{list_w},{line_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="result_caption" position="{caption_x},{caption_y}"
            size="{caption_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" />
    {rows}
    <widget name="results_scroll_track" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="results_scroll_thumb" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="detail_panel" position="{detail_x},{detail_y}"
            size="{detail_w},{detail_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="detail_accent" position="{detail_x},{detail_y}"
            size="{detail_w},{line_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="2" />
    <widget name="detail_title" position="{detail_text_x},{detail_title_y}"
            size="{detail_text_w},{detail_title_h}"
            font="Regular;{detail_title_font}" foregroundColor="#FFFFFF"
            transparent="1" noWrap="0" valign="center" halign="left" />
    <widget name="detail_rating" position="{detail_text_x},{rating_y}"
            size="{rating_w},{rating_h}" font="Regular;{rating_font}"
            foregroundColor="#FACC15" transparent="1"
            valign="center" halign="left" />
    <widget name="detail_meta" position="{meta_x},{rating_y}"
            size="{meta_w},{rating_h}" font="Regular;{meta_font}"
            foregroundColor="#CBD5E1" transparent="1"
            valign="center" halign="left" />
    <widget name="detail_category" position="{detail_text_x},{category_y}"
            size="{detail_text_w},{category_h}" font="Regular;{category_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" />
    <widget name="detail_plot_caption" position="{detail_text_x},{plot_caption_y}"
            size="{detail_text_w},{plot_caption_h}"
            font="Regular;{caption_font}" foregroundColor="#A855F7"
            transparent="1" valign="center" halign="left" />
    <widget name="detail_plot" position="{detail_text_x},{plot_y}"
            size="{detail_text_w},{plot_h}" font="Regular;{plot_font}"
            foregroundColor="#E2E8F0" transparent="1" noWrap="0"
            valign="top" halign="left" />
    <widget name="status" position="{detail_text_x},{status_y}"
            size="{detail_text_w},{status_h}" font="Regular;{status_font}"
            foregroundColor="#94A3B8" transparent="1" noWrap="0"
            valign="bottom" halign="left" />
    <widget name="message" position="{list_x},{message_y}"
            size="{list_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#94A3B8" transparent="1"
            valign="center" halign="center" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
</screen>
""".format(
        width=width, height=height, accent_h=px(5),
        header_x=px(50), header_y=px(22), header_w=px(760),
        header_h=px(70), header_font=px(38), server_x=px(850),
        server_w=width - px(900), server_font=px(19),
        query_y=query_y, query_h=query_h, query_w=width - px(84),
        line_w=px(6), query_caption_x=list_x + px(28),
        query_caption_w=px(180), query_text_x=list_x + px(218),
        query_text_w=width - list_x - px(260), query_font=px(25),
        list_x=list_x, list_y=list_y, list_w=list_w, list_h=list_h,
        line_h=px(4), caption_x=list_x + px(28),
        caption_y=list_y + px(10), caption_w=list_w - px(56),
        caption_h=px(42), caption_font=px(21), rows="".join(rows),
        scroll_x=list_x + list_w - px(16), scroll_y=row_y,
        scroll_w=px(8), scroll_h=(row_h * ALL_MOVIES_SEARCH_PAGE_SIZE) - px(7),
        detail_x=detail_x, detail_y=detail_y, detail_w=detail_w,
        detail_h=detail_h, detail_text_x=detail_x + px(30),
        detail_text_w=detail_w - px(60), detail_title_y=detail_y + px(30),
        detail_title_h=px(122), detail_title_font=px(35),
        rating_y=detail_y + px(164), rating_w=px(190), rating_h=px(40),
        rating_font=px(24), meta_x=detail_x + px(220),
        meta_w=detail_w - px(250), meta_font=px(20),
        category_y=detail_y + px(214), category_h=px(44),
        category_font=px(20), plot_caption_y=detail_y + px(278),
        plot_caption_h=px(36), plot_y=detail_y + px(324),
        plot_h=px(220), plot_font=px(22),
        status_y=detail_y + detail_h - px(170), status_h=px(140),
        status_font=px(19), message_y=list_y + list_h - px(55),
        message_h=px(45), message_font=px(18), footer_y=footer_y,
        footer_h=height - footer_y, footer_font=px(18),
    )


def _series_browser_skin(cinematic=False):
    """Series categories, eight-card grid and selected-series preview."""
    width, height, px = _scale()
    favorite_star = FAVORITE_STAR_PATH.replace("&", "&amp;").replace(
        '"', "&quot;"
    )
    if cinematic:
        footer_y = height - px(90)
        category_x = px(8)
        category_y = px(4)
        category_w = px(348)
        hero_x = px(365)
        # Extend only the panel paint to x=0.  Category content retains the
        # shared Movie/Series coordinates approved for remote navigation.
        # The paint now ends exactly where the common hero begins.
        category_panel_x = 0
        category_panel_w = hero_x
        category_h = footer_y - category_y - px(4)
        category_row_h = px(CINEMATIC_CATEGORY_ROW_HEIGHT)
        category_list_y = category_y + px(54)
        category_scroll_h = (
            CINEMATIC_CATEGORY_PAGE_SIZE * category_row_h
        ) - px(8)
        # Keep the Series composition pixel-identical to Movies: one full
        # bleed hero, the same category rail and the same five-card row.
        hero_y = 0
        hero_w = width - hero_x
        hero_h = px(616)
        detail_x = hero_x
        detail_y = 0
        detail_w = px(530)
        detail_h = hero_h
        shadow_offset = 0
        artwork_x = hero_x
        artwork_y = hero_y
        artwork_w = hero_w
        artwork_h = hero_h
        people_x = hero_x
        people_y = px(500)
        people_w = hero_w
        people_h = px(54)
        rail_x = hero_x
        rail_w = hero_w
        rail_caption_y = px(574)
        rail_y = px(616)
        gap = px(18)
        tile_w = int((rail_w - (gap * 4)) / 5)
        tile_image_h = px(276)
        tile_h = px(344)
        cinematic_scrim = CINEMATIC_SCRIM_PATH.replace(
            "&", "&amp;"
        ).replace('"', "&quot;")
        cinematic_fallback_shadow = (
            CINEMATIC_FALLBACK_SHADOW_PATH.replace(
                "&", "&amp;"
            ).replace('"', "&quot;")
        )

        category_rows = []
        for index in range(CINEMATIC_CATEGORY_PAGE_SIZE):
            row_y = category_list_y + (index * category_row_h)
            category_rows.append(
                """
    <widget name="series_category_focus_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#172A45"
            transparent="0" zPosition="2" />
    <widget name="series_category_marker_{index}" position="{x},{y}"
            size="{marker_w},{h}" font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="3" />
    <widget name="series_category_{index}" position="{text_x},{y}"
            size="{text_w},{h}" font="Regular;{font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="4"
            valign="center" halign="left" noWrap="1" />""".format(
                    index=index,
                    x=category_x + px(14),
                    y=row_y,
                    w=category_w - px(28),
                    h=category_row_h - px(8),
                    marker_w=px(5),
                    text_x=category_x + px(34),
                    text_w=category_w - px(54),
                    font=px(21),
                )
            )

        series_tiles = []
        for index in range(SERIES_GRID_PAGE_SIZE):
            if index >= 5:
                series_tiles.append(
                    """
    <widget name="series_tile_bg_{index}" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_tile_focus_{index}" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_tile_inner_{index}" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_tile_placeholder_{index}" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_tile_poster_{index}" position="0,0" size="1,1"
            alphatest="blend" scale="1" />
    <widget name="series_tile_favorite_{index}" position="0,0" size="1,1"
            pixmap="{favorite_star}" alphatest="blend" scale="1" />
    <widget name="series_tile_title_{index}" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />""".format(
                        index=index,
                        favorite_star=favorite_star,
                    )
                )
                continue
            tile_x = rail_x + (index * (tile_w + gap))
            series_tiles.append(
                """
    <widget name="series_tile_bg_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#111D31"
            transparent="0" zPosition="1" />
    <widget name="series_tile_focus_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="2" />
    <widget name="series_tile_inner_{index}" position="{inner_x},{inner_y}"
            size="{inner_w},{inner_h}" font="Regular;1"
            backgroundColor="#081321" transparent="0" zPosition="3" />
    <widget name="series_tile_placeholder_{index}"
            position="{image_x},{image_y}" size="{image_w},{image_h}"
            font="Regular;{placeholder_font}" foregroundColor="#22D3EE"
            backgroundColor="#101B2D" transparent="0" zPosition="4"
            valign="center" halign="center" />
    <widget name="series_tile_poster_{index}"
            position="{image_x},{image_y}" size="{image_w},{image_h}"
            alphatest="blend" scale="1" zPosition="5" />
    <widget name="series_tile_favorite_{index}"
            position="{favorite_x},{favorite_y}"
            size="{favorite_size},{favorite_size}"
            pixmap="{favorite_star}" alphatest="blend" scale="1"
            zPosition="7" />
    <widget name="series_tile_title_{index}"
            position="{title_x},{title_y}" size="{title_w},{title_h}"
            font="Regular;{title_font}" foregroundColor="#F8FAFC"
            transparent="1" zPosition="6" valign="center"
            halign="center" noWrap="0" />""".format(
                    index=index,
                    x=tile_x,
                    y=rail_y,
                    w=tile_w,
                    h=tile_h,
                    inner_x=tile_x + px(4),
                    inner_y=rail_y + px(4),
                    inner_w=tile_w - px(8),
                    inner_h=tile_h - px(8),
                    image_x=tile_x + px(10),
                    image_y=rail_y + px(10),
                    image_w=tile_w - px(20),
                    image_h=tile_image_h,
                    placeholder_font=px(29),
                    favorite_x=tile_x + tile_w - px(45),
                    favorite_y=rail_y + px(16),
                    favorite_size=px(31),
                    favorite_star=favorite_star,
                    title_x=tile_x + px(8),
                    title_y=rail_y + tile_image_h + px(10),
                    title_w=tile_w - px(16),
                    title_h=tile_h - tile_image_h - px(14),
                    title_font=px(18),
                )
            )

        return """
<screen name="GTContentBrowserScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#020617">
    <widget name="series_video_guard" position="0,0"
            size="{width},{height}" font="Regular;1"
            backgroundColor="#020617" transparent="0" zPosition="-30" />
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="-20" />
    <widget name="top_accent" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="header" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_server" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />

    <widget name="series_hero_panel" position="{hero_x},{hero_y}"
            size="{hero_w},{hero_h}" font="Regular;1"
            transparent="1" zPosition="-12" />
    <widget name="series_backdrop" position="{hero_x},{hero_y}"
            size="{hero_w},{hero_h}" alphatest="blend" scale="1"
            zPosition="-10" />
    <widget name="series_backdrop_scrim" position="{hero_x},{hero_y}"
            size="{hero_w},{hero_h}" pixmap="{cinematic_scrim}"
            alphatest="blend" scale="1" zPosition="-9" />
    <widget name="series_detail_shadow"
            position="{detail_shadow_x},{detail_shadow_y}"
            size="{detail_shadow_w},{detail_shadow_h}"
            pixmap="{cinematic_fallback_shadow}" alphatest="blend"
            scale="1" zPosition="-8" />
    <widget name="series_grid_panel" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_detail_panel" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_grid_accent" position="{text_x},{divider_y}"
            size="{text_w},{divider_h}" font="Regular;1"
            backgroundColor="#164E63" transparent="0" zPosition="1" />
    <widget name="series_detail_accent" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_meta_divider"
            position="{meta_divider_x},{meta_divider_y}"
            size="{meta_divider_w},{meta_divider_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="1" />
    <widget name="series_people_shadow"
            position="{people_shadow_x},{people_shadow_y}"
            size="{people_shadow_w},{people_shadow_h}"
            pixmap="{cinematic_fallback_shadow}" alphatest="blend"
            scale="1" zPosition="-8" />
    <widget name="series_rail_shadow"
            position="{rail_shadow_x},{rail_shadow_y}"
            size="{rail_shadow_w},{rail_shadow_h}"
            pixmap="{cinematic_fallback_shadow}" alphatest="blend"
            scale="1" zPosition="-8" />
    <widget name="series_people_panel" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_poster_frame" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_poster_placeholder" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="series_poster" position="0,0" size="1,1"
            alphatest="blend" scale="1" />

    <widget name="series_title" position="{text_x},{title_y}"
            size="{text_w},{title_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" noWrap="0"
            valign="center" halign="left" zPosition="2" />
    <widget name="series_rating" position="{text_x},{rating_y}"
            size="{rating_w},{rating_h}" font="Regular;{rating_font}"
            foregroundColor="#FACC15" transparent="1"
            valign="center" halign="left" noWrap="1" zPosition="2" />
    <widget name="series_meta" position="{meta_x},{rating_y}"
            size="{meta_w},{rating_h}" font="Regular;{meta_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" noWrap="1" zPosition="2" />
    <widget name="series_plot_caption" position="{text_x},{caption_y}"
            size="{text_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#FACC15" transparent="1"
            valign="center" halign="left" zPosition="2" />
    <widget name="series_plot" position="{text_x},{plot_y}"
            size="{text_w},{plot_h}" font="Regular;{plot_font}"
            foregroundColor="#FFFFFF" transparent="1" noWrap="0"
            scrollbarMode="showNever" valign="top" halign="left"
            zPosition="2" />
    <widget name="series_people" position="{people_text_x},{people_y}"
            size="{people_text_w},{people_h}" font="Regular;{people_font}"
            foregroundColor="#FFFFFF" transparent="1" noWrap="0"
            valign="center" halign="left" zPosition="2" />

    <widget name="series_category_panel" position="{category_panel_x},{category_y}"
            size="{category_panel_w},{category_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="series_category_accent"
            position="{category_panel_x},{category_y}"
            size="{category_panel_w},{line_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="series_category_caption"
            position="{category_caption_x},{category_caption_y}"
            size="{category_caption_w},{category_caption_h}"
            font="Regular;{caption_font}" foregroundColor="#22D3EE"
            transparent="1" zPosition="4" valign="center" halign="left" />
    {category_rows}
    <widget name="list_scroll_track"
            position="{category_scroll_x},{category_scroll_y}"
            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb"
            position="{category_scroll_x},{category_scroll_y}"
            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="message" position="{message_x},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#94A3B8" transparent="1"
            valign="center" halign="center" />

    <widget name="series_rail_caption" position="{rail_x},{rail_caption_y}"
            size="{rail_w},{rail_caption_h}" font="Regular;{caption_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" />
    <widget name="series_grid_position" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    {series_tiles}
    <widget name="poster_scroll_track"
            position="{rail_x},{scroll_y}" size="{rail_w},{scroll_h}"
            font="Regular;1" backgroundColor="#26344E"
            transparent="0" zPosition="7" />
    <widget name="poster_scroll_thumb"
            position="{rail_x},{scroll_y}" size="{rail_w},{scroll_h}"
            font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="8" />

    <widget name="footer" position="0,{footer_y}"
            size="{width},{footer_h}" font="Regular;{footer_font}"
            foregroundColor="#C8D2E3" backgroundColor="#080E1A"
            transparent="0" valign="center" halign="center" />
    <widget name="screen_label" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="content_panel" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="list_accent" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="breadcrumb" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="items" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
</screen>
""".format(
            width=width,
            height=height,
            footer_y=footer_y,
            footer_h=height - footer_y,
            footer_font=px(18),
            hero_x=hero_x,
            hero_y=hero_y,
            hero_w=hero_w,
            hero_h=hero_h,
            artwork_x=artwork_x,
            artwork_y=artwork_y,
            artwork_w=artwork_w,
            artwork_h=artwork_h,
            cinematic_scrim=cinematic_scrim,
            cinematic_fallback_shadow=cinematic_fallback_shadow,
            detail_x=detail_x,
            detail_y=detail_y,
            detail_w=detail_w,
            detail_h=detail_h,
            detail_shadow_x=detail_x + shadow_offset,
            detail_shadow_y=detail_y + shadow_offset,
            detail_shadow_w=min(hero_w, detail_w + px(120)),
            detail_shadow_h=people_y,
            text_x=detail_x + px(38),
            text_w=detail_w - px(76),
            title_y=px(50),
            title_h=px(72),
            title_font=px(38),
            rating_y=px(132),
            rating_w=px(145),
            rating_h=px(42),
            rating_font=px(25),
            meta_divider_x=detail_x + px(188),
            meta_divider_y=px(140),
            meta_divider_w=px(3),
            meta_divider_h=px(26),
            meta_x=detail_x + px(213),
            meta_w=detail_w - px(251),
            meta_font=px(22),
            divider_y=px(214),
            divider_h=px(3),
            caption_y=px(222),
            caption_h=px(38),
            caption_font=px(21),
            plot_y=px(266),
            plot_h=px(220),
            plot_font=px(22),
            people_x=people_x,
            people_y=people_y,
            people_w=people_w,
            people_h=people_h,
            people_shadow_x=people_x + px(7),
            people_shadow_y=people_y + px(7),
            people_shadow_w=max(px(1), people_w - px(14)),
            people_shadow_h=max(px(1), people_h - px(14)),
            people_text_x=people_x + px(32),
            people_text_w=people_w - px(64),
            people_font=px(18),
            rail_shadow_x=rail_x,
            rail_shadow_y=rail_caption_y,
            rail_shadow_w=min(rail_w, px(720)),
            rail_shadow_h=rail_y - rail_caption_y,
            category_x=category_x,
            category_panel_x=category_panel_x,
            category_panel_w=category_panel_w,
            category_y=category_y,
            category_w=category_w,
            category_h=category_h,
            line_h=px(4),
            category_caption_x=category_x + px(24),
            category_caption_y=category_y + px(8),
            category_caption_w=category_w - px(48),
            category_caption_h=px(40),
            category_rows="".join(category_rows),
            category_scroll_x=category_x + category_w - px(14),
            category_scroll_y=category_list_y,
            category_scroll_w=px(8),
            category_scroll_h=category_scroll_h,
            message_x=category_x + px(14),
            message_y=category_list_y + category_scroll_h + px(4),
            message_w=category_w - px(28),
            message_h=max(
                px(24),
                footer_y - category_list_y - category_scroll_h - px(8),
            ),
            message_font=px(17),
            rail_x=rail_x,
            rail_w=rail_w,
            rail_caption_y=rail_caption_y,
            rail_caption_h=px(36),
            series_tiles="".join(series_tiles),
            scroll_y=footer_y - px(18),
            scroll_h=px(6),
        )
    footer_y = height - px(85)
    content_y = px(112)
    content_h = px(844)

    category_x = px(42)
    category_w = px(300)
    category_row_h = px(88)
    category_list_y = content_y + px(68)
    category_scroll_h = (
        SERIES_CATEGORY_PAGE_SIZE * category_row_h
    ) - px(8)
    category_message_y = (
        category_list_y
        + (SERIES_CATEGORY_PAGE_SIZE * category_row_h)
        + px(4)
    )

    grid_panel_x = px(366)
    grid_panel_w = px(972)
    grid_x = grid_panel_x
    grid_w = grid_panel_w
    tile_gap_x = px(12)
    tile_gap_y = px(16)
    tile_w = int(
        (grid_w - (tile_gap_x * (SERIES_GRID_COLUMNS - 1)))
        / SERIES_GRID_COLUMNS
    )
    tile_h = px(340)
    tile_y = px(174)
    tile_poster_h = px(285)
    series_tiles = []
    for index in range(SERIES_GRID_PAGE_SIZE):
        row, column = divmod(index, SERIES_GRID_COLUMNS)
        x = grid_x + (column * (tile_w + tile_gap_x))
        y = tile_y + (row * (tile_h + tile_gap_y))
        series_tiles.append(
            """
    <widget name="series_tile_bg_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#111D31"
            transparent="0" zPosition="2" />
    <widget name="series_tile_focus_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#22D3EE"
            transparent="0" zPosition="3" />
    <widget name="series_tile_inner_{index}" position="{inner_x},{inner_y}"
            size="{inner_w},{inner_h}" font="Regular;1"
            backgroundColor="#081321" transparent="0" zPosition="4" />
    <widget name="series_tile_placeholder_{index}"
            position="{image_x},{image_y}" size="{image_w},{image_h}"
            font="Regular;{placeholder_font}" foregroundColor="#22D3EE"
            backgroundColor="#101B2D" transparent="0" zPosition="5"
            valign="center" halign="center" />
    <widget name="series_tile_poster_{index}"
            position="{image_x},{image_y}" size="{image_w},{image_h}"
            alphatest="blend" scale="1" zPosition="6" />
    <widget name="series_tile_favorite_{index}"
            position="{favorite_x},{favorite_y}"
            size="{favorite_size},{favorite_size}"
            pixmap="{favorite_star}" alphatest="blend" scale="1"
            zPosition="8" />
    <widget name="series_tile_title_{index}"
            position="{title_x},{title_y}" size="{title_w},{title_h}"
            font="Regular;{title_font}" foregroundColor="#F8FAFC"
            transparent="1" zPosition="7" valign="center"
            halign="center" noWrap="1" />""".format(
                index=index,
                x=x,
                y=y,
                w=tile_w,
                h=tile_h,
                inner_x=x + px(4),
                inner_y=y + px(4),
                inner_w=tile_w - px(8),
                inner_h=tile_h - px(8),
                image_x=x + px(22),
                image_y=y + px(8),
                image_w=tile_w - px(44),
                image_h=tile_poster_h,
                placeholder_font=px(27),
                favorite_x=x + tile_w - px(42),
                favorite_y=y + px(14),
                favorite_size=px(30),
                favorite_star=favorite_star,
                title_x=x + px(7),
                title_y=y + px(298),
                title_w=tile_w - px(14),
                title_h=px(36),
                title_font=max(8, px(19)),
            )
        )

    category_rows = []
    for index in range(SERIES_CATEGORY_PAGE_SIZE):
        y = category_list_y + (index * category_row_h)
        category_rows.append(
            """
    <widget name="series_category_focus_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#172A45"
            transparent="0" zPosition="2" />
    <widget name="series_category_marker_{index}" position="{x},{y}"
            size="{marker_w},{h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="3" />
    <widget name="series_category_{index}" position="{text_x},{y}"
            size="{text_w},{h}" font="Regular;{font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="4"
            valign="center" halign="left" noWrap="1" />""".format(
                index=index,
                x=category_x + px(12),
                y=y,
                w=category_w - px(24),
                h=category_row_h - px(8),
                marker_w=px(5),
                text_x=category_x + px(33),
                text_w=category_w - px(52),
                font=px(20),
            )
        )

    detail_x = px(1362)
    detail_w = width - detail_x - px(42)
    preview_w = px(168)
    preview_h = px(252)
    preview_x = detail_x + px(34)
    preview_y = px(152)
    detail_inner_x = detail_x + px(28)
    detail_inner_w = detail_w - px(56)
    cinematic_base_widget = ""
    cinematic_widgets = ""
    grid_panel_color = "#0A1323"
    detail_panel_color = "#0D1729"
    if cinematic:
        cinematic_scrim = CINEMATIC_SCRIM_PATH.replace(
            "&",
            "&amp;",
        ).replace('"', "&quot;")
        cinematic_w = width - grid_panel_x - px(42)
        cinematic_base_widget = """
    <widget name="series_video_guard" position="0,0"
            size="{w},{h}" font="Regular;1"
            backgroundColor="#020617" transparent="0"
            zPosition="0" />""".format(w=width, h=height)
        cinematic_widgets = """
    <widget name="series_backdrop" position="{x},{y}"
            size="{w},{h}" alphatest="blend" scale="1"
            zPosition="0" />
    <widget name="series_backdrop_scrim" position="{x},{y}"
            size="{w},{h}" pixmap="{scrim}" alphatest="blend" scale="1"
            zPosition="0" />""".format(
            x=grid_panel_x,
            y=content_y,
            w=cinematic_w,
            h=content_h,
            scrim=cinematic_scrim,
        )

    return """
<screen name="GTContentBrowserScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#020617">
    {cinematic_base_widget}
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{header_x},{header_y}"
            size="{header_w},{header_h}" font="Regular;{header_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" />
    <widget name="series_server" position="{server_x},{header_y}"
            size="{server_w},{header_h}" font="Regular;{server_font}"
            foregroundColor="#94A3B8" transparent="1"
            valign="center" halign="right" noWrap="1" />
    {cinematic_widgets}

    <widget name="series_category_panel"
            position="{category_x},{content_y}"
            size="{category_w},{content_h}" font="Regular;1"
            backgroundColor="#0D1729" transparent="0" />
    <widget name="series_category_accent"
            position="{category_x},{content_y}"
            size="{category_w},{line_h}" font="Regular;1"
            backgroundColor="#8B5CF6" transparent="0" zPosition="2" />
    <widget name="series_category_caption"
            position="{category_caption_x},{category_caption_y}"
            size="{category_caption_w},{category_caption_h}"
            font="Regular;{caption_font}" foregroundColor="#C084FC"
            transparent="1" zPosition="4" valign="center" halign="left" />
    {category_rows}
    <widget name="list_scroll_track"
            position="{category_scroll_x},{category_scroll_y}"
            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb"
            position="{category_scroll_x},{category_scroll_y}"
            size="{category_scroll_w},{category_scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="message" position="{message_x},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#94A3B8" transparent="1"
            valign="center" halign="center" />

    <widget name="series_grid_panel"
            position="{grid_panel_x},{content_y}"
            size="{grid_panel_w},{content_h}" font="Regular;1"
            backgroundColor="{grid_panel_color}" transparent="0" />
    <widget name="series_grid_accent"
            position="{grid_panel_x},{content_y}"
            size="{grid_panel_w},{line_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="series_rail_caption"
            position="{grid_caption_x},{grid_caption_y}"
            size="{grid_caption_w},{grid_caption_h}"
            font="Regular;{caption_font}" foregroundColor="#22D3EE"
            transparent="1" valign="center" halign="left" />
    <widget name="series_grid_position"
            position="{position_x},{position_y}"
            size="{position_w},{position_h}"
            font="Regular;{position_font}" foregroundColor="#CBD5E1"
            transparent="1" valign="center" halign="center" />
    {series_tiles}
    <widget name="poster_scroll_track"
            position="{poster_scroll_x},{poster_scroll_y}"
            size="{poster_scroll_w},{poster_scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="7" />
    <widget name="poster_scroll_thumb"
            position="{poster_scroll_x},{poster_scroll_y}"
            size="{poster_scroll_w},{poster_scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="8" />

    <widget name="series_detail_panel" position="{detail_x},{content_y}"
            size="{detail_w},{content_h}" font="Regular;1"
            backgroundColor="{detail_panel_color}" transparent="0" />
    <widget name="series_detail_accent" position="{detail_x},{content_y}"
            size="{detail_w},{line_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="2" />
    <widget name="series_poster_frame"
            position="{poster_frame_x},{poster_frame_y}"
            size="{poster_frame_w},{poster_frame_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="series_poster_placeholder"
            position="{preview_x},{preview_y}" size="{preview_w},{preview_h}"
            font="Regular;{poster_placeholder_font}"
            foregroundColor="#22D3EE" backgroundColor="#101B2D"
            transparent="0" valign="center" halign="center" zPosition="3" />
    <widget name="series_poster"
            position="{preview_x},{preview_y}" size="{preview_w},{preview_h}"
            alphatest="blend" scale="1" zPosition="4" />
    <widget name="series_title"
            position="{title_x},{title_y}"
            size="{title_w},{title_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" noWrap="0"
            valign="center" halign="left" />
    <widget name="series_rating"
            position="{title_x},{rating_y}"
            size="{rating_w},{rating_h}" font="Regular;{rating_font}"
            foregroundColor="#FACC15" transparent="1"
            valign="center" halign="left" />
    <widget name="series_meta" position="{title_x},{meta_y}"
            size="{title_w},{meta_h}" font="Regular;{meta_font}"
            foregroundColor="#CBD5E1" transparent="1"
            valign="top" halign="left" noWrap="0" />
    <widget name="series_plot"
            position="{detail_inner_x},{plot_y}"
            size="{detail_inner_w},{plot_h}" font="Regular;{plot_font}"
            foregroundColor="#E2E8F0" transparent="1" noWrap="0"
            valign="top" halign="left" />

    <widget name="footer" position="0,{footer_y}"
            size="{width},{footer_h}" font="Regular;{footer_font}"
            foregroundColor="#C8D2E3" backgroundColor="#080E1A"
            transparent="0" valign="center" halign="center" />
    <widget name="screen_label" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="content_panel" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="list_accent" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="breadcrumb" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
    <widget name="items" position="0,0" size="1,1"
            font="Regular;1" transparent="1" />
</screen>
""".format(
        width=width,
        height=height,
        accent_h=px(5),
        line_h=px(4),
        header_x=px(50),
        header_y=px(22),
        header_w=px(720),
        header_h=px(72),
        header_font=px(38),
        server_x=px(820),
        server_w=width - px(870),
        server_font=px(18),
        cinematic_base_widget=cinematic_base_widget,
        cinematic_widgets=cinematic_widgets,
        content_y=content_y,
        content_h=content_h,
        category_x=category_x,
        category_w=category_w,
        category_caption_x=category_x + px(24),
        category_caption_y=content_y + px(10),
        category_caption_w=category_w - px(48),
        category_caption_h=px(40),
        caption_font=px(21),
        category_rows="".join(category_rows),
        category_scroll_x=category_x + category_w - px(14),
        category_scroll_y=category_list_y,
        category_scroll_w=px(8),
        category_scroll_h=category_scroll_h,
        message_x=category_x + px(14),
        message_y=category_message_y,
        message_w=category_w - px(28),
        message_h=max(
            px(24),
            content_y + content_h - category_message_y - px(6),
        ),
        message_font=px(17),
        grid_panel_x=grid_panel_x,
        grid_panel_w=grid_panel_w,
        grid_panel_color=grid_panel_color,
        grid_caption_x=grid_x,
        grid_caption_y=content_y + px(8),
        grid_caption_w=grid_w,
        grid_caption_h=px(40),
        position_x=grid_x,
        position_y=px(890),
        position_w=grid_w,
        position_h=px(32),
        position_font=max(8, px(19)),
        series_tiles="".join(series_tiles),
        poster_scroll_x=grid_x,
        poster_scroll_y=px(936),
        poster_scroll_w=grid_w,
        poster_scroll_h=px(8),
        detail_x=detail_x,
        detail_w=detail_w,
        detail_panel_color=detail_panel_color,
        preview_x=preview_x,
        preview_y=preview_y,
        preview_w=preview_w,
        preview_h=preview_h,
        poster_frame_x=preview_x - px(6),
        poster_frame_y=preview_y - px(6),
        poster_frame_w=preview_w + px(12),
        poster_frame_h=preview_h + px(12),
        poster_placeholder_font=px(31),
        detail_inner_x=detail_inner_x,
        detail_inner_w=detail_inner_w,
        title_x=detail_x + px(226),
        title_w=detail_w - px(254),
        title_y=px(146),
        title_h=px(88),
        title_font=max(8, px(30)),
        rating_y=px(240),
        rating_w=detail_w - px(254),
        rating_h=px(34),
        rating_font=max(8, px(22)),
        meta_y=px(282),
        meta_h=px(124),
        meta_font=max(8, px(18)),
        plot_y=px(430),
        plot_h=px(482),
        plot_font=max(8, px(20)),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(18),
    )


def _player_info_progress_geometry(width, px):
    weather_x = width - px(590) - px(20)
    divider_x = weather_x - px(22)
    progress_w = divider_x - px(300) - px(168)
    return max(1, progress_w), max(1, px(6))


def _player_info_skin():
    width, height, px = _scale()
    margin = 0
    bar_h = px(280)
    bar_y = height - bar_h
    bar_width = width
    weather_w = px(590)
    weather_x = bar_width - weather_w - px(20)
    divider_x = weather_x - px(22)
    detail_x = px(300)
    epg_text_x = detail_x + px(130)
    epg_text_w = divider_x - epg_text_x - px(20)
    progress_w, progress_h = _player_info_progress_geometry(width, px)
    return """
<screen name="GTPlayerInfoOverlay" position="{margin},{bar_y}"
        size="{bar_width},{bar_h}" flags="wfNoBorder"
        backgroundColor="#FF000000" zPosition="100">
    <widget name="info_background" position="0,0"
            size="{bar_width},{bar_h}" font="Regular;1"
            backgroundColor="#3008131F"
            transparent="0" zPosition="1" />
    <widget name="top_line" position="0,0"
            size="{bar_width},{line_h}" font="Regular;1"
            backgroundColor="#22D3EE"
            transparent="0" zPosition="2" />
    <widget name="bottom_line" position="0,{bottom_line_y}"
            size="{bar_width},{line_h}" font="Regular;1"
            backgroundColor="#0E7490" transparent="0" zPosition="2" />
    <widget name="picon_frame" position="0,0" size="1,1" font="Regular;1"
            transparent="1" zPosition="2" />
    <widget name="picon_inner" position="0,0" size="1,1" font="Regular;1"
            transparent="1" zPosition="3" />
    <widget name="picon_placeholder" position="0,0" size="1,1"
            font="Regular;1" transparent="1" zPosition="4" />
    <widget name="picon" position="{picon_x},{picon_y}"
            size="{picon_w},{picon_h}" alphatest="blend" scale="1"
            zPosition="5" />
    <widget name="number_badge_frame" position="{number_frame_x},{number_frame_y}"
            size="{number_frame_w},{number_frame_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="number_badge_inner" position="{number_inner_x},{number_inner_y}"
            size="{number_inner_w},{number_inner_h}" font="Regular;1"
            backgroundColor="#071423" transparent="0" zPosition="3" />
    <widget name="channel_number" position="{number_x},{number_y}"
            size="{number_w},{number_h}" font="Regular;{number_font}"
            foregroundColor="#FFFFFF" backgroundColor="#00000000"
            transparent="1" valign="center" halign="center" noWrap="1"
            zPosition="4" />
    <widget name="number_divider" position="0,0" size="1,1"
            font="Regular;1" transparent="1" zPosition="3" />
    <widget name="channel_name" position="{name_x},{title_y}"
            size="{name_w},{title_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" backgroundColor="#00000000"
            transparent="1" valign="center" halign="left" noWrap="1"
            zPosition="3" />
    <widget name="engine" position="{engine_x},{engine_y}"
            size="{engine_w},{engine_h}" font="Regular;{detail_font}"
            foregroundColor="#64748B" backgroundColor="#00000000"
            transparent="1" valign="center" halign="right" noWrap="1"
            zPosition="3" />
    <widget name="resolution" position="{detail_x},{detail_y}"
            size="{resolution_w},{detail_h}" font="Regular;{detail_font}"
            foregroundColor="#D0D8E4" backgroundColor="#180B1726"
            transparent="0" valign="center" halign="center" noWrap="1"
            zPosition="3" />
    <widget name="fps" position="{fps_x},{detail_y}"
            size="{fps_w},{detail_h}" font="Regular;{detail_font}"
            foregroundColor="#D0D8E4" backgroundColor="#180B1726"
            transparent="0" valign="center" halign="center" noWrap="1"
            zPosition="3" />
    <widget name="video_codec" position="{video_x},{detail_y}"
            size="{video_w},{detail_h}" font="Regular;{detail_font}"
            foregroundColor="#19D3E6" backgroundColor="#180B1726"
            transparent="0" valign="center" halign="center" noWrap="1"
            zPosition="3" />
    <widget name="audio_codec" position="{audio_x},{detail_y}"
            size="{audio_w},{detail_h}" font="Regular;{detail_font}"
            foregroundColor="#19D3E6" backgroundColor="#180B1726"
            transparent="0" valign="center" halign="center" noWrap="1"
            zPosition="3" />
    <widget name="epg_separator" position="0,0" size="1,1"
            font="Regular;1" transparent="1" zPosition="2" />
    <widget name="epg_now_label" position="{detail_x},{epg_now_y}"
            size="{epg_label_w},{epg_h}" font="Regular;{epg_now_font}"
            foregroundColor="#22D3EE" backgroundColor="#00000000"
            transparent="1" valign="center" halign="left" noWrap="1"
            zPosition="3" />
    <widget name="epg_now" position="{epg_text_x},{epg_now_y}"
            size="{epg_text_w},{epg_h}" font="Regular;{epg_now_font}"
            foregroundColor="#FFFFFF" backgroundColor="#00000000"
            transparent="1" valign="center" halign="left" noWrap="1"
            zPosition="3" />
    <widget name="epg_next_label" position="{detail_x},{epg_next_y}"
            size="{epg_label_w},{epg_h}" font="Regular;{epg_next_font}"
            foregroundColor="#94A3B8" backgroundColor="#00000000"
            transparent="1" valign="center" halign="left" noWrap="1"
            zPosition="3" />
    <widget name="epg_next" position="{epg_text_x},{epg_next_y}"
            size="{epg_text_w},{epg_h}" font="Regular;{epg_next_font}"
            foregroundColor="#AEBBCD" backgroundColor="#00000000"
            transparent="1" valign="center" halign="left" noWrap="1"
            zPosition="3" />
    <widget name="epg_bottom_separator" position="0,0" size="1,1"
            font="Regular;1" transparent="1" zPosition="2" />
    <widget name="player_progress_bg" position="{progress_x},{progress_y}"
            size="{progress_w},{progress_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="2" />
    <widget name="player_progress_fill" position="{progress_x},{progress_y}"
            size="{progress_w},{progress_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="3" />
    <widget name="player_progress_text" position="{progress_text_x},{progress_text_y}"
            size="{progress_text_w},{progress_text_h}" font="Regular;{progress_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="right" zPosition="3" />
    <widget name="weather_divider" position="{divider_x},{weather_divider_y}"
            size="{separator_h},{weather_divider_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="2" />
    <widget name="weather_card" position="{weather_x},{weather_y}"
            size="{weather_w},{weather_h}" font="Regular;1"
            transparent="1" zPosition="2" />
    <widget name="favorite_badge" position="{favorite_x},{favorite_y}"
            size="{favorite_w},{favorite_h}" font="Regular;{favorite_font}"
            foregroundColor="#22D3EE" backgroundColor="#00000000"
            transparent="1" valign="center" halign="left" noWrap="1"
            zPosition="4" />
    <widget name="weather_icon" position="{weather_icon_x},{weather_icon_y}"
            size="{weather_icon_w},{weather_icon_h}" alphatest="blend" scale="1"
            zPosition="4" />
    <widget name="weather_city" position="{weather_city_x},{weather_city_y}"
            size="{weather_city_w},{weather_city_h}" font="Regular;{weather_city_font}"
            foregroundColor="#E2E8F0" transparent="1"
            valign="center" halign="left" noWrap="1" zPosition="3" />
    <widget name="weather_temp" position="{weather_city_x},{weather_temp_y}"
            size="{weather_city_w},{weather_temp_h}" font="Regular;{weather_temp_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" zPosition="3" />
    <widget name="weather_desc" position="{weather_city_x},{weather_desc_y}"
            size="{weather_city_w},{weather_desc_h}" font="Regular;{weather_desc_font}"
            foregroundColor="#CBD5E1" transparent="1"
            valign="center" halign="left" noWrap="1" zPosition="3" />
    <widget name="clock_divider" position="{clock_divider_x},{clock_divider_y}"
            size="{separator_h},{clock_divider_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="3" />
    <widget name="clock" position="{clock_x},{clock_y}"
            size="{clock_w},{clock_h}" font="Regular;{clock_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="center" zPosition="3" />
    <widget name="date" position="{clock_x},{date_y}"
            size="{clock_w},{date_h}" font="Regular;{date_font}"
            foregroundColor="#CBD5E1" transparent="1"
            valign="center" halign="center" zPosition="3" />
    <widget name="seek_panel" position="0,0" size="{bar_width},{bar_h}"
            font="Regular;1" backgroundColor="#E008131F"
            transparent="0" zPosition="20" />
    <widget name="seek_state" position="{seek_x},{seek_state_y}"
            size="{seek_w},{seek_state_h}" font="Regular;{seek_state_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="21"
            valign="center" halign="left" />
    <widget name="seek_progress_bg" position="{seek_x},{seek_progress_y}"
            size="{seek_w},{seek_progress_h}" font="Regular;1"
            backgroundColor="#334155" transparent="0" zPosition="21" />
    <widget name="seek_progress_fill" position="{seek_x},{seek_progress_y}"
            size="{seek_w},{seek_progress_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="22" />
    <widget name="seek_times" position="{seek_x},{seek_times_y}"
            size="{seek_w},{seek_times_h}" font="Regular;{seek_times_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="21"
            valign="center" halign="center" />
    <widget name="seek_hint" position="{seek_x},{seek_hint_y}"
            size="{seek_w},{seek_hint_h}" font="Regular;{seek_hint_font}"
            foregroundColor="#CBD5E1" transparent="1" zPosition="21"
            valign="center" halign="center" />
</screen>
""".format(
        margin=margin,
        bar_y=bar_y,
        bar_width=bar_width,
        bar_h=bar_h,
        line_h=px(3),
        bottom_line_y=bar_h - px(3),
        picon_x=px(48), picon_y=px(43), picon_w=px(190), picon_h=px(190),
        number_frame_x=detail_x, number_frame_y=px(22),
        number_frame_w=px(94), number_frame_h=px(62),
        number_inner_x=detail_x + px(2), number_inner_y=px(24),
        number_inner_w=px(90), number_inner_h=px(58),
        number_x=detail_x + px(2), number_y=px(22),
        number_w=px(90), number_h=px(62), number_font=px(46),
        name_x=detail_x + px(120), name_w=divider_x - detail_x - px(140),
        title_y=px(20), title_h=px(64), title_font=px(40),
        engine_x=weather_x + px(365), engine_y=px(238),
        engine_w=weather_w - px(385), engine_h=px(32), detail_x=detail_x,
        detail_y=px(88), detail_h=px(36), detail_font=px(20),
        resolution_w=px(200), fps_x=detail_x + px(208), fps_w=px(132),
        video_x=detail_x + px(348), video_w=px(220),
        audio_x=detail_x + px(576), audio_w=px(300),
        separator_h=px(2), epg_now_y=px(132), epg_next_y=px(176),
        epg_label_w=px(112), epg_text_x=epg_text_x, epg_text_w=epg_text_w,
        epg_h=px(40), epg_now_font=px(24), epg_next_font=px(21),
        progress_x=detail_x, progress_y=px(245), progress_w=progress_w,
        progress_h=progress_h, progress_text_x=detail_x + progress_w + px(12),
        progress_text_y=px(225), progress_text_w=px(145), progress_text_h=px(42),
        progress_font=px(20), divider_x=divider_x,
        weather_divider_y=px(18), weather_divider_h=px(236),
        weather_x=weather_x, weather_y=px(22), weather_w=weather_w, weather_h=px(205),
        favorite_x=weather_x + px(24), favorite_y=px(20),
        favorite_w=px(120), favorite_h=px(32), favorite_font=px(17),
        weather_icon_x=weather_x + px(24), weather_icon_y=px(62),
        weather_icon_w=px(120), weather_icon_h=px(120),
        weather_city_x=weather_x + px(158), weather_city_y=px(36),
        weather_city_w=px(195), weather_city_h=px(38), weather_city_font=px(20),
        weather_temp_y=px(72), weather_temp_h=px(76), weather_temp_font=px(50),
        weather_desc_y=px(148), weather_desc_h=px(40), weather_desc_font=px(18),
        clock_divider_x=weather_x + px(365), clock_divider_y=px(36),
        clock_divider_h=px(158), clock_x=weather_x + px(382), clock_y=px(58),
        clock_w=weather_w - px(395), clock_h=px(86), clock_font=px(52),
        date_y=px(146), date_h=px(48), date_font=px(17),
        seek_x=px(80), seek_w=bar_width - px(160), seek_state_y=px(28),
        seek_state_h=px(56), seek_state_font=px(34),
        seek_progress_y=px(108), seek_progress_h=px(14),
        seek_times_y=px(130), seek_times_h=px(52), seek_times_font=px(25),
        seek_hint_y=px(196), seek_hint_h=px(48), seek_hint_font=px(20),
    )


class GTAsyncListScreen(Screen):
    page_size = 10

    def __init__(
        self,
        session,
        screen_name,
        title,
        breadcrumb,
        frameless_footer=False,
    ):
        generic_skin = not getattr(self, "skin", None)
        if generic_skin:
            self.page_size = visible_count(10)
            self.skin = _browser_skin(screen_name)
        footer_items = getattr(
            self,
            "_footer_layout_items",
            ASYNC_FOOTER_ITEMS,
        )
        footer_alternate_items = getattr(
            self,
            "_footer_layout_alternate_items",
            (),
        )
        self.skin = decorate_remote_footer(
            self.skin,
            footer_items,
            alternate_items=footer_alternate_items,
            transparent_panel=bool(frameless_footer),
            show_dividers=not bool(frameless_footer),
        )
        if not getattr(self, "_list_scroll_geometry", None):
            width, height, px = _scale()
            margin = px(64)
            content_width = width - (margin * 2)
            self._list_scroll_geometry = (
                margin + content_width - px(50),
                px(205),
                px(8),
                px(650),
            )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND_PATH)
        # Screen is dict-like and OpenATV calls self.items() while destroying
        # its GUI. Never shadow that method with the loaded content list.
        self._entries = []
        self.selected_index = 0
        self._loading = False
        self._ready = False
        self._closed = False
        self._error = ""
        self._load_generation = 0
        self._load_token = None
        self._timer = eTimer()
        _connect_timer(self._timer, self._poll_result)

        self["top_accent"] = Label("")
        self["header"] = Label(_(title))
        self["screen_label"] = Label(_("CONTENT BROWSER"))
        self["content_panel"] = Label("")
        self["list_accent"] = Label("")
        self["breadcrumb"] = Label("")
        self._dynamic_breadcrumb_text = _(breadcrumb)
        self._refit_dynamic_breadcrumb()
        self["items"] = Label("")
        self["list_scroll_track"] = Label("")
        self["list_scroll_thumb"] = Label("")
        hide_scrollbar(self, "list_scroll")
        self["message"] = Label("")
        # Kept as a one-pixel compatibility component by the footer decorator.
        self["footer"] = Label("")
        install_remote_footer(
            self,
            footer_items,
            slot_count=max(len(footer_items), len(footer_alternate_items)),
        )
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "green": self.start_load,
                "red": self.close,
            },
            -1,
        )
        if hasattr(self, "onShown"):
            self.onShown.append(self._start_initial_load)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refit_dynamic_breadcrumb)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop)
        self.setTitle(_(title))

    def _set_dynamic_breadcrumb(self, value):
        self._dynamic_breadcrumb_text = str(value or "")
        self._refit_dynamic_breadcrumb()

    def _refit_dynamic_breadcrumb(self):
        if not hasattr(self, "__getitem__"):
            return
        try:
            component = self["breadcrumb"]
        except Exception:
            return
        ellipsize_dynamic_text(
            component,
            getattr(self, "_dynamic_breadcrumb_text", ""),
            fallback_chars=90,
        )

    def load_items(self):
        raise NotImplementedError

    def _prepare_loaded_items(self, values):
        """Finish potentially blocking result preparation on the worker."""
        return list(values or [])

    def _commit_loaded_items(self, prepared, generation):
        del generation
        self._entries = list(prepared or [])

    def empty_message(self):
        return N_("No content found.")

    def item_text(self, item):
        return item.name

    def start_load(self):
        if self._loading or self._closed:
            return
        _cancel_worker_token(self._load_token)
        self._load_generation += 1
        generation = self._load_generation
        token = _WorkerToken(WORKER_LIST_TIMEOUT_SECONDS)
        self._load_token = token
        self._loading = True
        self._ready = False
        self._error = ""
        self._entries = []
        self.selected_index = 0
        self["items"].setText(_("Loading content..."))
        self["message"].setText(_("Please wait"))
        hide_scrollbar(self, "list_scroll")
        worker = threading.Thread(
            target=self._run_load,
            args=(generation, token),
        )
        worker.daemon = True
        worker.start()
        self._timer.start(150, True)

    def _start_initial_load(self):
        """Load once; returning from a child screen must preserve selection."""
        if self._ready or self._entries or self._loading:
            return
        self.start_load()

    def _run_load(self, generation=None, token=None):
        if generation is None:
            generation = self._load_generation
        prepared = []
        error = ""
        try:
            prepared = _bounded_client_call(
                getattr(self, "client", None),
                token,
                lambda: self._prepare_loaded_items(self.load_items()),
            )
        except _WorkerCancelled:
            error = (
                N_("Request timed out")
                if token is not None and token.past_deadline()
                else N_("Request cancelled")
            )
        except ContentError as caught:
            error = str(caught)
        except Exception:
            error = N_("Could not load content")
        if (
            self._closed
            or generation != self._load_generation
            or (token is not None and not token.active())
        ):
            return
        self._commit_loaded_items(prepared, generation)
        self._error = error
        self._ready = True

    def _poll_result(self):
        if self._closed:
            return
        if not self._ready:
            if self._load_token is not None and self._load_token.expired():
                self._load_token.cancel()
                self._loading = False
                self._error = N_("Request timed out")
                self._ready = True
                self._refresh()
                return
            self._timer.start(150, True)
            return
        self._loading = False
        self._load_token = None
        self._refresh()

    def _refresh(self):
        if self._error:
            self["items"].setText(_("Could not open the content list."))
            ellipsize_dynamic_text(
                self["message"],
                _(self._error),
                fallback_chars=90,
            )
            hide_scrollbar(self, "list_scroll")
            return
        if not self._entries:
            self["items"].setText(_(self.empty_message()))
            self["message"].setText(_("Press GREEN to try again"))
            hide_scrollbar(self, "list_scroll")
            return
        self.selected_index %= len(self._entries)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start : page_start + self.page_size]
        lines = []
        for offset, item in enumerate(page_items):
            index = page_start + offset
            marker = ">" if index == self.selected_index else " "
            lines.append(
                ellipsize_dynamic_text(
                    self["items"],
                    self.item_text(item),
                    fallback_chars=90,
                    prefix="{} {:04d}. ".format(marker, index + 1),
                )
            )
        self["items"].setText("\n\n".join(lines))
        self["message"].setText(
            "{}  |  {}/{}".format(
                len(self._entries), self.selected_index + 1, len(self._entries)
            )
        )
        update_scrollbar(
            self,
            "list_scroll",
            len(self._entries),
            self.selected_index,
            self.page_size,
            self._list_scroll_geometry,
        )

    def _select(self, index):
        if not self._entries or self._loading:
            return
        self.selected_index = index % len(self._entries)
        self._refresh()

    def move_up(self):
        self._select(self.selected_index - 1)

    def move_down(self):
        self._select(self.selected_index + 1)

    def page_up(self):
        self._select(self.selected_index - self.page_size)

    def page_down(self):
        self._select(self.selected_index + self.page_size)

    def open_selected(self):
        pass

    def _stop(self):
        self._closed = True
        self._load_generation += 1
        _cancel_worker_token(self._load_token)
        self._load_token = None
        try:
            self._timer.stop()
        except Exception:
            pass


class GTLiveCategoryManagerScreen(GTAsyncListScreen):
    """Transactional order, bulk move and visibility editor."""

    page_size = 8

    def __init__(
        self,
        session,
        account_name,
        account_scope,
        categories,
        category_store=None,
        selected_category_id="",
        content_type="live",
    ):
        content_type = str(content_type or "").strip().lower()
        if content_type not in ("live", "movie", "series"):
            content_type = "live"
        self.content_type = content_type
        self._all_category_label = CONTENT_LABELS[content_type][1]
        manager_labels = {
            "live": N_("LIVE TV CATEGORY SETTINGS"),
            "movie": N_("MOVIE CATEGORY SETTINGS"),
            "series": N_("SERIES CATEGORY SETTINGS"),
        }
        manager_label = manager_labels[content_type]
        self.category_store = (
            category_store
            if category_store is not None
            else LiveCategoryPreferenceStore(
                category_preferences_path(content_type)
            )
        )
        self.account_scope = str(account_scope or "")
        self._footer_layout_items = CATEGORY_MANAGER_FOOTER_ITEMS
        self._footer_layout_alternate_items = (
            CATEGORY_MANAGER_MOVE_FOOTER_ITEMS
        )
        self.skin = _live_category_manager_skin()
        unused_width, unused_height, px = _scale()
        del unused_width, unused_height
        self._list_scroll_geometry = (
            px(64 + 990 - 18),
            px(252),
            px(8),
            px(626),
        )
        self._stored_preferences = self.category_store.load(
            self.account_scope
        )
        self._provider_entries = arrange_categories(
            categories,
            CategoryPreferences(),
            include_hidden=True,
        )
        arranged = arrange_categories(
            categories,
            self._stored_preferences,
            include_hidden=True,
        )
        self._move_mode = False
        self._marked = set()
        self._move_ids = set()
        self._move_snapshot = None
        self._move_snapshot_index = 0
        self._move_snapshot_reset_requested = False
        self._reset_requested = False
        self._notice = ""
        GTAsyncListScreen.__init__(
            self,
            session,
            "GTLiveCategoryManagerScreen",
            N_("Category Manager"),
            "{}  |  {}".format(account_name, _("Settings")),
        )
        self["screen_label"].setText(_(manager_label))
        self["manager_panel_accent_magenta"] = Label("")
        self["manager_caption"] = Label(_(manager_label))
        self["manager_selection_count"] = Label("")
        self["manager_move_help"] = Label("")
        for index in range(self.page_size):
            self["category_row_{}".format(index)] = Label("")
            self["category_state_{}".format(index)] = Label("")
            self["category_check_{}_empty".format(index)] = Pixmap()
            self["category_check_{}_tick".format(index)] = Pixmap()
            self["category_insert_{}".format(index)] = Label("")
            for edge in ("fill", "top", "bottom", "left", "right"):
                self[
                    "category_focus_{}_{}".format(index, edge)
                ] = Label("")
        self._entries = list(arranged)
        self._ready = True
        self._loading = False
        self._error = ""
        self._hidden = set(self._stored_preferences.hidden)
        current_ids = set(self._current_ids())
        self._hidden.intersection_update(current_ids)
        self._initial_ids = tuple(self._current_ids())
        self._initial_hidden = frozenset(self._hidden)
        self._include_terms = tuple(self._stored_preferences.include_terms)
        self._exclude_terms = tuple(self._stored_preferences.exclude_terms)
        self._initial_include_terms = self._include_terms
        self._initial_exclude_terms = self._exclude_terms
        self._filter_excluded = set()
        self._update_filter_excluded()
        self._provider_ids = tuple(
            self._entry_id(entry)
            for entry in self._provider_entries
            if self._entry_id(entry)
        )
        self._select_category_id(selected_category_id)
        self["actions"] = ActionMap(
            [
                "OkCancelActions",
                "DirectionActions",
                "ColorActions",
                "MenuActions",
            ],
            {
                "ok": self.open_selected,
                "cancel": self.cancel_or_close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "yellow": self.start_move,
                "red": self.toggle_mark,
                "menu": self.open_filter_search,
                "green": self.save_and_close,
                "blue": self.restore_defaults,
            },
            -1,
        )
        if hasattr(self, "onShown"):
            self.onShown.append(self._refresh)

    @staticmethod
    def _entry_id(entry):
        return str(getattr(entry, "category_id", "") or "").strip()

    def _current_ids(self):
        return [
            self._entry_id(entry)
            for entry in self._entries
            if self._entry_id(entry)
        ]

    def _selected_id(self):
        if not self._entries:
            return ""
        return self._entry_id(self._entries[self.selected_index])

    def _select_category_id(self, category_id):
        category_id = str(category_id or "").strip()
        for index, entry in enumerate(self._entries):
            if self._entry_id(entry) == category_id:
                self.selected_index = index
                return
        self.selected_index = 0

    def load_items(self):
        return list(self._entries)

    def empty_message(self):
        return N_("No category found.")

    def _refresh(self):
        if not self._entries:
            self._clear_category_rows()
            self["items"].setText(_(self.empty_message()))
            self["message"].setText("")
            hide_scrollbar(self, "list_scroll")
            return
        self.selected_index = min(
            len(self._entries) - 1,
            max(0, int(self.selected_index)),
        )
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start : page_start + self.page_size]
        plain_lines = []
        for row_index in range(self.page_size):
            if row_index < len(page_items):
                entry = page_items[row_index]
                index = page_start + row_index
                category_id = self._entry_id(entry)
                name = str(
                    getattr(entry, "name", "") or _("Category")
                )
                row_text = "{:02d}.  {}".format(index + 1, name)
                hidden = bool(
                    category_id and (
                        category_id in self._hidden
                        or category_id in self._filter_excluded
                    )
                )
                state_text = localized_upper(_("Hidden")) if hidden else ""
                rendered_row = ellipsize_dynamic_text(
                    self["category_row_{}".format(row_index)],
                    row_text,
                    fallback_chars=34,
                )
                self["category_state_{}".format(row_index)].setText(
                    state_text
                )
                plain_lines.append(rendered_row)
                selected = index == self.selected_index
            else:
                self["category_row_{}".format(row_index)].setText("")
                self["category_state_{}".format(row_index)].setText("")
                category_id = ""
                selected = False
            marked = bool(category_id and category_id in self._marked)
            for check_state, visible in (
                ("empty", bool(category_id) and not marked),
                ("tick", marked),
            ):
                checkbox = self[
                    "category_check_{}_{}".format(row_index, check_state)
                ]
                if visible:
                    checkbox.show()
                else:
                    checkbox.hide()
            insertion_line = self["category_insert_{}".format(row_index)]
            if self._move_mode and selected:
                insertion_line.show()
            else:
                insertion_line.hide()
            for edge in ("fill", "top", "bottom", "left", "right"):
                focus = self[
                    "category_focus_{}_{}".format(row_index, edge)
                ]
                if selected:
                    focus.show()
                else:
                    focus.hide()
        self["items"].setText("\n".join(plain_lines))
        hidden_count = len(self._hidden.union(self._filter_excluded))
        visible_count = len(self._entries) - hidden_count
        status = "{}: {}  |  {}: {}  |  {}/{}".format(
            _("Visible"),
            visible_count,
            _("Hidden"),
            hidden_count,
            self.selected_index + 1,
            len(self._entries),
        )
        if self._marked:
            status = "{}: {}  |  {}".format(
                _("Marked"), len(self._marked), status
            )
        self["manager_selection_count"].setText(
            "{}: {}".format(_("Marked"), len(self._marked))
            if self._marked else ""
        )
        self["manager_move_help"].setText(
            self._move_guidance() if self._move_mode else ""
        )
        if self._notice and not self._move_mode:
            status = "{}  |  {}".format(status, self._notice)
        ellipsize_dynamic_text(
            self["message"],
            status,
            fallback_chars=70,
        )
        update_scrollbar(
            self,
            "list_scroll",
            len(self._entries),
            self.selected_index,
            self.page_size,
            self._list_scroll_geometry,
        )

    def _clear_category_rows(self):
        self["manager_selection_count"].setText("")
        self["manager_move_help"].setText("")
        for row_index in range(self.page_size):
            self["category_row_{}".format(row_index)].setText("")
            self["category_state_{}".format(row_index)].setText("")
            self["category_check_{}_empty".format(row_index)].hide()
            self["category_check_{}_tick".format(row_index)].hide()
            self["category_insert_{}".format(row_index)].hide()
            for edge in ("fill", "top", "bottom", "left", "right"):
                self[
                    "category_focus_{}_{}".format(row_index, edge)
                ].hide()

    def _select_move_destination(self, distance):
        if not self._move_mode or not self._entries:
            return
        self.selected_index = min(
            len(self._entries) - 1,
            max(0, self.selected_index + int(distance)),
        )
        self._refresh()

    def move_up(self):
        if self._move_mode:
            self._select_move_destination(-1)
            return
        GTAsyncListScreen.move_up(self)

    def move_down(self):
        if self._move_mode:
            self._select_move_destination(1)
            return
        GTAsyncListScreen.move_down(self)

    def page_up(self):
        if self._move_mode:
            self._select_move_destination(-self.page_size)
            return
        GTAsyncListScreen.page_up(self)

    def page_down(self):
        if self._move_mode:
            self._select_move_destination(self.page_size)
            return
        GTAsyncListScreen.page_down(self)

    def open_selected(self):
        if self._move_mode:
            self.finish_move()
            return
        self.toggle_visibility()

    def toggle_visibility(self):
        category_id = self._selected_id()
        if not category_id:
            self._notice = _("{} is always visible.").format(
                _(self._all_category_label)
            )
            self._refresh()
            return
        if category_id in self._hidden:
            self._hidden.discard(category_id)
            self._notice = (
                _("Filter Search") if category_id in self._filter_excluded
                else _("Category shown.")
            )
        else:
            self._hidden.add(category_id)
            self._notice = _("Category hidden.")
        self._refresh()

    def start_move(self):
        if self._move_mode:
            return
        self._marked.intersection_update(self._current_ids())
        if not self._marked:
            self._notice = _("Mark categories with RED first.")
            self._refresh()
            return
        self._move_mode = True
        self._move_snapshot = list(self._entries)
        self._move_snapshot_index = self.selected_index
        self._move_snapshot_reset_requested = self._reset_requested
        self._move_ids = set(self._marked)
        self._notice = self._move_guidance()
        set_remote_footer(self, CATEGORY_MANAGER_MOVE_FOOTER_ITEMS)
        self._refresh()

    @staticmethod
    def _move_guidance():
        return _(
            "Choose the destination with the arrows. OK places marked "
            "categories below the line; EXIT cancels."
        )

    def finish_move(self):
        if not self._move_mode:
            return
        block = [
            entry for entry in self._entries
            if self._entry_id(entry) in self._move_ids
        ]
        # The line is the original gap after the focused row. Removing the
        # marked rows shifts that gap left once for each mark before it.
        insert_at = sum(
            1 for entry in self._entries[:self.selected_index + 1]
            if self._entry_id(entry) not in self._move_ids
        )
        remaining = [
            entry for entry in self._entries
            if self._entry_id(entry) not in self._move_ids
        ]
        self._entries = (
            remaining[:insert_at] + block + remaining[insert_at:]
        )
        self.selected_index = insert_at
        self._move_mode = False
        self._marked.clear()
        self._move_ids.clear()
        self._move_snapshot = None
        self._move_snapshot_index = 0
        self._move_snapshot_reset_requested = False
        self._notice = _("Position updated. Press GREEN to save.")
        set_remote_footer(self, CATEGORY_MANAGER_FOOTER_ITEMS)
        self._refresh()

    def cancel_or_close(self):
        if self._move_mode:
            self._entries = list(self._move_snapshot or self._entries)
            self.selected_index = min(
                len(self._entries) - 1,
                max(0, self._move_snapshot_index),
            )
            self._move_mode = False
            self._move_ids.clear()
            self._move_snapshot = None
            self._move_snapshot_index = 0
            self._reset_requested = self._move_snapshot_reset_requested
            self._move_snapshot_reset_requested = False
            self._notice = _("Move cancelled.")
            set_remote_footer(self, CATEGORY_MANAGER_FOOTER_ITEMS)
            self._refresh()
            return
        self.close(False)

    def restore_defaults(self):
        if self._move_mode:
            return
        selected_id = self._selected_id()
        self._entries = list(self._provider_entries)
        self._hidden.clear()
        self._marked.clear()
        self._include_terms = ()
        self._exclude_terms = ()
        self._update_filter_excluded()
        self._reset_requested = True
        self._select_category_id(selected_id)
        self._notice = _("Default order restored. Press GREEN to save.")
        self._refresh()

    def _has_changes(self):
        if self._reset_requested and (
            self._stored_preferences.order
            or self._stored_preferences.hidden
            or self._stored_preferences.include_terms
            or self._stored_preferences.exclude_terms
        ):
            return True
        return bool(
            tuple(self._current_ids()) != self._initial_ids
            or frozenset(self._hidden) != self._initial_hidden
            or self._include_terms != self._initial_include_terms
            or self._exclude_terms != self._initial_exclude_terms
        )

    def save_and_close(self):
        if self._move_mode:
            self._notice = self._move_guidance()
            self._refresh()
            return
        if not self._has_changes():
            self.close(False)
            return
        ordered_ids = self._current_ids()
        current_id_set = set(ordered_ids)
        ordered_id_set = set(current_id_set)
        if not self._reset_requested:
            missing_budget = max(
                0,
                MAX_CATEGORIES_PER_SOURCE - len(current_id_set),
            )
            retained_missing = 0
            current_positions = {
                category_id: index
                for index, category_id in enumerate(ordered_ids)
            }
            missing_blocks = []
            pending_missing = []
            previous_anchor = ""
            for stored_id in self._stored_preferences.order:
                if stored_id in ordered_id_set:
                    if pending_missing:
                        missing_blocks.append(
                            (
                                previous_anchor,
                                stored_id,
                                list(pending_missing),
                            )
                        )
                        pending_missing = []
                    previous_anchor = stored_id
                    continue
                if retained_missing < missing_budget:
                    pending_missing.append(stored_id)
                    retained_missing += 1
            if pending_missing:
                missing_blocks.append(
                    (previous_anchor, "", list(pending_missing))
                )

            prefix_missing = []
            missing_before = {}
            missing_after = {}
            for previous_id, next_id, missing_ids in missing_blocks:
                if previous_id and next_id:
                    if (
                        current_positions[previous_id]
                        < current_positions[next_id]
                    ):
                        missing_before[next_id] = missing_ids
                    else:
                        missing_after[previous_id] = missing_ids
                elif next_id:
                    missing_before[next_id] = missing_ids
                elif previous_id:
                    missing_after[previous_id] = missing_ids
                else:
                    prefix_missing.extend(missing_ids)

            merged_ids = list(prefix_missing)
            for category_id in ordered_ids:
                merged_ids.extend(missing_before.get(category_id, ()))
                merged_ids.append(category_id)
                merged_ids.extend(missing_after.get(category_id, ()))
            ordered_ids = merged_ids
            ordered_id_set = set(ordered_ids)
        hidden_ids = sorted(
            category_id
            for category_id in self._hidden
            if category_id in current_id_set
        )
        if not self._reset_requested:
            missing_id_set = ordered_id_set.difference(current_id_set)
            hidden_ids = sorted(
                set(hidden_ids).union(
                    category_id
                    for category_id in self._stored_preferences.hidden
                    if category_id in missing_id_set
                )
            )
        if (
            self._reset_requested
            and tuple(ordered_ids) == self._provider_ids
            and not hidden_ids
            and not self._include_terms
            and not self._exclude_terms
        ):
            saved = self.category_store.reset(self.account_scope)
        else:
            saved = self.category_store.save(
                self.account_scope,
                ordered_ids,
                hidden_ids,
                include_terms=self._include_terms,
                exclude_terms=self._exclude_terms,
            )
        if not saved:
            self._notice = _(
                "Settings could not be saved. Check the receiver storage."
            )
            self._refresh()
            return
        self.close(
            True, ordered_ids, hidden_ids,
            self._include_terms, self._exclude_terms,
        )

    def toggle_mark(self):
        if self._move_mode:
            return
        category_id = self._selected_id()
        if not category_id:
            self._notice = _("{} cannot be moved.").format(
                _(self._all_category_label)
            )
        elif category_id in self._marked:
            self._marked.discard(category_id)
            self._notice = ""
        else:
            self._marked.add(category_id)
            self._notice = ""
        self._refresh()

    def _update_filter_excluded(self):
        allowed, unused_excluded = category_filter_ids(
            self._provider_entries, self._include_terms, self._exclude_terms
        )
        self._filter_excluded = set(
            self._entry_id(entry) for entry in self._provider_entries
            if self._entry_id(entry)
        ).difference(allowed)

    def open_filter_search(self):
        if self._move_mode:
            return
        from .category_filter_ui import GTCategoryFilterScreen
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            return
        opener(
            self._filter_search_closed,
            GTCategoryFilterScreen,
            self._provider_entries,
            self._include_terms,
            self._exclude_terms,
            tuple(self._hidden),
        )

    def _filter_search_closed(self, result=None):
        if result is None or self._closed:
            return
        self._include_terms, self._exclude_terms = result
        self._include_terms = tuple(self._include_terms)
        self._exclude_terms = tuple(self._exclude_terms)
        self._update_filter_excluded()
        self._notice = _("Filters updated. Press GREEN to save.")
        self._refresh()


class GTContinueWatchingScreen(GTAsyncListScreen):
    """Poster grid for every unfinished movie and series episode."""

    page_size = 8

    def __init__(
        self,
        session,
        account,
        client=None,
        resume_store=None,
        metadata_client=None,
        grid_screen_name="GTContinueWatchingScreen",
        grid_title=None,
        grid_subtitle=None,
        grid_footer_items=None,
        grid_footer_alternate_items=None,
        grid_skin=None,
        grid_scroll_geometry=None,
        content_type_filter="",
    ):
        # Child screens opened from Continue Watching and Favorites need the
        # original account object.  Keep it explicitly instead of relying on
        # the content client to expose it indirectly.
        self.account = account
        self.client = client or content_client_for(account)
        self.resume_store = resume_store or PlaybackResumeStore()
        self.account_scope = resume_account_scope(self.client)
        self._content_type_filter = str(
            content_type_filter or ""
        ).strip().lower()
        if metadata_client is not None:
            self._metadata_client = metadata_client
        else:
            try:
                settings = load_player_settings()
            except Exception:
                settings = None
            self._metadata_client = TMDbMetadataClient(
                api_key=getattr(settings, "tmdb_api_key", ""),
                enabled=getattr(settings, "metadata_enabled", True),
                language=metadata_language(),
            )
        self._initial_show = True
        self._continue_generation = 0
        self._continue_poster_page_key = None
        self._continue_poster_state = None
        self._footer_layout_items = (
            grid_footer_items or CONTINUE_FOOTER_ITEMS
        )
        self._footer_layout_alternate_items = tuple(
            grid_footer_alternate_items or ()
        )
        self.skin = grid_skin or _continue_watching_skin(grid_screen_name)
        width, height, px = _scale()
        self._continue_progress_width = px(384)
        self._continue_progress_height = px(9)
        self._list_scroll_geometry = grid_scroll_geometry or (
            width - px(82), px(215), px(8), px(635)
        )
        title = grid_title or N_("Continue Watching")
        GTAsyncListScreen.__init__(
            self,
            session,
            grid_screen_name,
            title,
            "{}  >  {}".format(
                account.display_name,
                _(title),
            ),
        )
        self["screen_label"].setText(_(title))
        self["subtitle"] = Label(
            _(grid_subtitle or N_("Unfinished movies and series episodes"))
        )
        for slot in range(self.page_size):
            self["continue_card_{}".format(slot)] = Label("")
            self["continue_poster_placeholder_{}".format(slot)] = Label("")
            self["continue_poster_{}".format(slot)] = Pixmap()
            self["continue_favorite_{}".format(slot)] = Pixmap()
            self["continue_type_{}".format(slot)] = Label("")
            self["continue_title_{}".format(slot)] = Label("")
            self["continue_meta_{}".format(slot)] = Label("")
            self["continue_progress_bg_{}".format(slot)] = Label("")
            self["continue_progress_fill_{}".format(slot)] = Label("")
            self["continue_progress_text_{}".format(slot)] = Label("")
            for edge in ("top", "bottom", "left", "right"):
                self[
                    "continue_focus_{}_{}".format(slot, edge)
                ] = Label("")
        self._poster_timer = eTimer()
        _connect_timer(self._poster_timer, self._poll_continue_posters)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "left": self.move_left,
                "right": self.move_right,
                "up": self.move_grid_up,
                "down": self.move_grid_down,
                "red": self.remove_selected,
                "green": self.start_load,
            },
            -1,
        )
        if hasattr(self, "onShown"):
            self.onShown.append(self._reload_after_return)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop_continue_posters)
        self._clear_continue_cards()

    def _reload_after_return(self):
        if self._initial_show:
            self._initial_show = False
            return
        if not self._loading:
            self.start_load()

    def load_items(self):
        entries = []
        for bookmark in self.resume_store.list_entries(self.account_scope):
            metadata = bookmark.get("item", {})
            content_type = str(
                metadata.get("content_type", "") or ""
            ).strip().lower()
            if (
                self._content_type_filter
                and content_type != self._content_type_filter
            ):
                continue
            item = ContentItem(
                content_type,
                metadata.get("stream_id"),
                metadata.get("name"),
                category_id=metadata.get("category_id"),
                extension=metadata.get("extension"),
                icon=metadata.get("icon"),
                season=metadata.get("season"),
                episode=metadata.get("episode"),
                year=metadata.get("year"),
                parent_id=metadata.get("parent_id"),
            )
            if str(metadata.get("source_type", "") or "").lower() == "stalker":
                item.source_type = "stalker"
                request_type = str(
                    metadata.get("portal_request_type", "") or ""
                ).strip().lower()
                allowed_request_types = {
                    "movie": frozenset(("vod", "movie")),
                    "series": frozenset(("vod", "series")),
                }
                if request_type in allowed_request_types.get(
                    item.content_type,
                    (),
                ):
                    item.portal_request_type = request_type
            item.resume_position = int(bookmark.get("position", 0) or 0)
            item.resume_length = int(bookmark.get("length", 0) or 0)
            item.resume_updated_at = int(bookmark.get("updated_at", 0) or 0)
            item.resume_key = str(bookmark.get("key", "") or "")
            if item.content_type == "series" and item.parent_id:
                parent = ContentItem(
                    "series",
                    item.parent_id,
                    metadata.get("parent_name") or item.name,
                    category_id=item.category_id,
                    icon=item.icon,
                    year=item.year,
                )
                if getattr(item, "source_type", "") == "stalker":
                    parent.source_type = "stalker"
                parent.catalog_category_id = str(item.category_id or "")
                item.favorite_parent = parent
            entries.append(item)
        return entries

    def empty_message(self):
        return N_("No content found.")

    def _clear_continue_cards(self):
        for slot in range(self.page_size):
            for name in (
                "continue_card_{}",
                "continue_poster_placeholder_{}",
                "continue_poster_{}",
                "continue_favorite_{}",
                "continue_type_{}",
                "continue_title_{}",
                "continue_meta_{}",
                "continue_progress_bg_{}",
                "continue_progress_fill_{}",
                "continue_progress_text_{}",
            ):
                self[name.format(slot)].hide()
            for edge in ("top", "bottom", "left", "right"):
                self["continue_focus_{}_{}".format(slot, edge)].hide()

    def _refresh(self):
        self._clear_continue_cards()
        if self._error:
            ellipsize_dynamic_text(
                self["message"],
                _(self._error),
                fallback_chars=80,
            )
            hide_scrollbar(self, "list_scroll")
            return
        if not self._entries:
            self["message"].setText(_(self.empty_message()))
            hide_scrollbar(self, "list_scroll")
            return
        self.selected_index %= len(self._entries)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start : page_start + self.page_size]
        for slot, item in enumerate(page_items):
            self._render_continue_card(
                slot,
                item,
                page_start + slot == self.selected_index,
            )
        self["message"].setText(
            "{} | {}/{}".format(
                len(self._entries),
                self.selected_index + 1,
                len(self._entries),
            )
        )
        update_scrollbar(
            self,
            "list_scroll",
            len(self._entries),
            self.selected_index,
            self.page_size,
            self._list_scroll_geometry,
        )
        self._render_continue_posters(page_items, page_start)

    def _render_continue_card(self, slot, item, selected):
        position = int(getattr(item, "resume_position", 0) or 0)
        length = int(getattr(item, "resume_length", 0) or 0)
        progress = 0 if length <= 0 else int(
            min(100, max(0, round(float(position) * 100.0 / float(length))))
        )
        remaining_minutes = max(1, int(round(max(0, length - position) / 60.0)))
        content_key = (
            N_("Movies") if item.content_type == "movie" else N_("Series")
        )
        content_label = localized_upper(_(content_key))
        meta = ""
        if item.content_type == "series":
            season = str(getattr(item, "season", "") or "").strip()
            episode = str(getattr(item, "episode", "") or "").strip()
            if season or episode:
                meta = _("S{}  •  E{}").format(
                    season or "-",
                    episode or "-",
                )
        elif str(getattr(item, "year", "") or "").strip():
            meta = str(item.year)
        placeholder = self["continue_poster_placeholder_{}".format(slot)]
        placeholder.setText("GT\n{}".format(content_label))
        placeholder.show()
        self["continue_poster_{}".format(slot)].hide()
        self["continue_favorite_{}".format(slot)].hide()
        self["continue_type_{}".format(slot)].setText(content_label)
        ellipsize_dynamic_text(
            self["continue_title_{}".format(slot)],
            item.name,
            fallback_chars=22,
        )
        self["continue_meta_{}".format(slot)].setText(meta)
        self["continue_progress_text_{}".format(slot)].setText(
            _("%{} • {} min remaining").format(
                progress,
                remaining_minutes,
            )
        )
        try:
            self[
                "continue_progress_fill_{}".format(slot)
            ].instance.resize(
                eSize(
                    max(
                        1,
                        int(round(self._continue_progress_width * progress / 100.0)),
                    ),
                    self._continue_progress_height,
                )
            )
        except Exception:
            pass
        for name in (
            "continue_card_{}",
            "continue_type_{}",
            "continue_title_{}",
            "continue_meta_{}",
            "continue_progress_bg_{}",
            "continue_progress_fill_{}",
            "continue_progress_text_{}",
        ):
            self[name.format(slot)].show()
        for edge in ("top", "bottom", "left", "right"):
            widget = self["continue_focus_{}_{}".format(slot, edge)]
            if selected:
                widget.show()
            else:
                widget.hide()

    @staticmethod
    def _continue_candidate(candidates, value):
        url = _safe_picon_url(value)
        if url and url not in candidates:
            candidates.append(url)

    def _continue_provider_candidates(self, item):
        candidates = []
        info = None
        content_type = str(getattr(item, "content_type", "") or "")
        stream_id = str(getattr(item, "stream_id", "") or "")
        if content_type == "movie":
            loader = getattr(self.client, "load_vod_info", None)
            if loader is not None:
                try:
                    info = loader(stream_id)
                    self._continue_candidate(
                        candidates,
                        getattr(info, "cover", ""),
                    )
                except Exception as error:
                    _debug(
                        "continue movie poster lookup failed id={} error={}".format(
                            stream_id,
                            error.__class__.__name__,
                        )
                    )
        elif content_type == "series":
            parent_id = str(getattr(item, "parent_id", "") or "")
            series_id = (
                _series_provider_id(self.client, parent_id)
                or _series_provider_id(self.client, stream_id)
            )
            loader = getattr(self.client, "load_series_summary", None)
            if loader is None:
                loader = getattr(self.client, "load_series_info", None)
            if loader is not None and series_id:
                try:
                    info = loader(series_id)
                    if parent_id:
                        for episode in list(getattr(info, "episodes", []) or []):
                            if str(getattr(episode, "stream_id", "")) == stream_id:
                                self._continue_candidate(
                                    candidates,
                                    getattr(episode, "icon", ""),
                                )
                                break
                    self._continue_candidate(
                        candidates,
                        getattr(info, "cover", ""),
                    )
                except Exception as error:
                    _debug(
                        "continue series poster lookup failed id={} error={}".format(
                            _debug_identifier(stream_id),
                            error.__class__.__name__,
                        )
                    )
        return candidates, info

    def _continue_metadata_candidates(self, item, info, token=None):
        candidates = []
        client = self._metadata_client
        if client is None or not bool(getattr(client, "available", False)):
            return candidates
        try:
            content_type = getattr(item, "content_type", "")
            if content_type == "movie":
                completed = _bounded_client_call(
                    client,
                    token,
                    lambda: client.complete_movie(
                        item,
                        info,
                        force_cover_fallback=True,
                    ),
                )
            elif content_type == "series":
                completed = _bounded_client_call(
                    client,
                    token,
                    lambda: client.complete_series(
                        item,
                        info,
                        force_cover_fallback=True,
                    ),
                )
            else:
                return candidates
            self._continue_candidate(
                candidates,
                getattr(completed, "cover", ""),
            )
        except MetadataError as error:
            _debug(
                "continue TMDb poster fallback failed id={} error={}".format(
                    _debug_identifier(getattr(item, "stream_id", "")),
                    error.__class__.__name__,
                )
            )
        except Exception as error:
            _debug(
                "continue poster fallback failed id={} error={}".format(
                    _debug_identifier(getattr(item, "stream_id", "")),
                    error.__class__.__name__,
                )
            )
        return candidates

    def _download_continue_candidate(self, url, token=None):
        if not url:
            return ""
        if token is not None:
            token.check()
            remaining = max(0.25, token.deadline - time.monotonic())
        else:
            remaining = PICON_DOWNLOAD_TIMEOUT
        result = _download_client_image(
            self.client,
            url,
            _picon_cache_path(url),
            timeout=min(PICON_DOWNLOAD_TIMEOUT, remaining),
            token=token,
        )
        if token is not None:
            token.check()
        return result

    def _render_continue_posters(self, page_items, page_start):
        page_key = tuple(
            (str(item.stream_id), str(getattr(item, "icon", "") or ""))
            for item in page_items
        )
        page_changed = page_key != self._continue_poster_page_key
        if page_changed:
            self._cancel_continue_poster_work()
            self._continue_generation += 1
            self._continue_poster_page_key = page_key
        generation = self._continue_generation
        jobs = []
        for slot, item in enumerate(page_items):
            url = _safe_picon_url(getattr(item, "icon", ""))
            cached = _cached_picon_path(url) if url else ""
            if cached:
                self._apply_continue_poster(
                    slot,
                    cached,
                    item.stream_id,
                    generation,
                    page_start,
                )
            elif page_changed and self._poster_job_allowed(item, url):
                # A bookmark intentionally stores no credential-bearing poster
                # URL. Resolve it again through the authenticated provider API
                # and then use TMDb only if that provider image cannot load.
                jobs.append((slot, item, url, page_start))
        if not page_changed:
            return
        if not jobs:
            self._continue_poster_state = None
            return
        token = _WorkerToken(WORKER_POSTER_TIMEOUT_SECONDS)
        state = {
            "generation": generation,
            "pending": len(jobs),
            "jobs": deque(jobs),
            "results": [],
            "remember": [],
            "lock": threading.Lock(),
            "token": token,
        }
        self._continue_poster_state = state
        for _worker_index in range(min(CONTINUE_POSTER_WORKERS, len(jobs))):
            worker = threading.Thread(
                target=self._load_continue_poster_worker,
                args=(state,),
            )
            worker.daemon = True
            worker.start()
        self._poster_timer.start(120, True)

    def _poster_job_allowed(self, item, stored_url):
        del stored_url
        return getattr(item, "content_type", "") != "live"

    def _load_continue_poster_worker(self, state):
        while True:
            with state["lock"]:
                if not state["jobs"]:
                    return
                job = state["jobs"].popleft()
            self._load_continue_poster_job(job, state)

    def _load_continue_poster_job(self, job, state):
        slot, item, stored_url, page_start = job
        token = state.get("token")
        stream_id = str(getattr(item, "stream_id", "") or "")
        path = ""
        selected_url = ""
        info = None
        candidates = []
        self._continue_candidate(candidates, stored_url)
        try:
            token.check()
            for candidate in candidates:
                path = self._download_continue_candidate(candidate, token)
                if path:
                    selected_url = candidate
                    break
            if not path:
                provider_candidates, info = _bounded_client_call(
                    self.client,
                    token,
                    lambda: self._continue_provider_candidates(item),
                )
                for candidate in provider_candidates:
                    if candidate in candidates:
                        continue
                    candidates.append(candidate)
                    path = self._download_continue_candidate(candidate, token)
                    if path:
                        selected_url = candidate
                        break
            if not path:
                token.check()
                for candidate in self._continue_metadata_candidates(
                    item,
                    info,
                    token,
                ):
                    if candidate in candidates:
                        continue
                    path = self._download_continue_candidate(candidate, token)
                    if path:
                        selected_url = candidate
                        break
        except Exception as error:
            _debug(
                "continue poster failed id={} error={}".format(
                    _debug_identifier(stream_id),
                    error.__class__.__name__,
                )
            )
            path = ""
            selected_url = ""
        with state["lock"]:
            state["results"].append(
                (
                    slot,
                    stream_id,
                    path,
                    page_start,
                    selected_url,
                )
            )
            state["pending"] -= 1

    def _poll_continue_posters(self):
        if self._closed:
            return
        state = self._continue_poster_state
        if state is None:
            return
        token = state.get("token")
        if token is not None and token.expired():
            _cancel_worker_token(token)
            if self._continue_poster_state is state:
                self._continue_poster_state = None
            return
        with state["lock"]:
            results = list(state["results"])
            state["results"][:] = []
            pending = int(state["pending"])
        for slot, stream_id, path, page_start, selected_url in results:
            if path:
                try:
                    current = self._entries[page_start + slot]
                    valid = str(current.stream_id) == str(stream_id)
                except Exception:
                    current = None
                    valid = False
                if valid and selected_url:
                    current.icon = selected_url
                    state["remember"].append(
                        (
                            str(getattr(current, "resume_key", "") or ""),
                            current,
                        )
                    )
                self._apply_continue_poster(
                    slot,
                    path,
                    stream_id,
                    state["generation"],
                    page_start,
                )
        if pending > 0:
            self._poster_timer.start(120, True)
        elif self._continue_poster_state is state:
            remember_many = getattr(self.resume_store, "remember_many", None)
            try:
                if callable(remember_many):
                    remember_many(state["remember"], self.account_scope)
                else:
                    remember = getattr(self.resume_store, "remember", None)
                    if callable(remember):
                        for key, current in state["remember"]:
                            remember(key, current, self.account_scope)
            except Exception:
                pass
            self._continue_poster_state = None

    def _apply_continue_poster(
        self,
        slot,
        path,
        stream_id,
        generation,
        page_start,
    ):
        def loaded(success):
            try:
                current_page = int(self.selected_index / self.page_size) * self.page_size
                current = self._entries[page_start + slot]
                valid = (
                    generation == self._continue_generation
                    and current_page == page_start
                    and str(current.stream_id) == str(stream_id)
                )
                if success and valid:
                    self["continue_poster_{}".format(slot)].show()
                    self[
                        "continue_poster_placeholder_{}".format(slot)
                    ].hide()
                else:
                    self["continue_poster_{}".format(slot)].hide()
            except Exception:
                pass

        attach_pixmap(
            self,
            "continue_poster_{}".format(slot),
            path,
            key="continue-poster-{}".format(slot),
            on_loaded=loaded,
        )

    def _cancel_continue_poster_work(self):
        state = self._continue_poster_state
        if state is not None:
            _cancel_worker_token(state.get("token"))
        self._continue_poster_state = None
        try:
            self._poster_timer.stop()
        except Exception:
            pass

    def _stop_continue_posters(self):
        self._continue_generation += 1
        self._cancel_continue_poster_work()

    def _suspend_for_playback(self):
        self._stop_continue_posters()

    def _select_continue(self, index):
        if not self._entries or self._loading:
            return
        self.selected_index = min(max(0, int(index)), len(self._entries) - 1)
        self._refresh()

    def move_left(self):
        self._select_continue(self.selected_index - 1)

    def move_right(self):
        self._select_continue(self.selected_index + 1)

    def move_grid_up(self):
        if self.selected_index >= 4:
            self._select_continue(self.selected_index - 4)

    def move_grid_down(self):
        self._select_continue(self.selected_index + 4)

    def remove_selected(self):
        if not self._entries or self._loading:
            return
        item = self._entries[self.selected_index]
        key = str(getattr(item, "resume_key", "") or "")
        if not key or not self.resume_store.clear(key):
            return
        self._entries.pop(self.selected_index)
        self.selected_index = min(
            self.selected_index,
            max(0, len(self._entries) - 1),
        )
        self._continue_poster_page_key = None
        self._refresh()

    @staticmethod
    def _time_text(seconds):
        try:
            seconds = max(0, int(seconds or 0))
        except (TypeError, ValueError, OverflowError):
            seconds = 0
        hours, remainder = divmod(seconds, 3600)
        minutes, remaining_seconds = divmod(remainder, 60)
        return "{:02d}:{:02d}:{:02d}".format(
            hours,
            minutes,
            remaining_seconds,
        )

    def item_text(self, item):
        position = int(getattr(item, "resume_position", 0) or 0)
        length = int(getattr(item, "resume_length", 0) or 0)
        progress = 0 if length <= 0 else int(
            min(100, max(0, round(float(position) * 100.0 / float(length))))
        )
        content_label = (
            _("Movies") if item.content_type == "movie" else _("Series")
        )
        return "{}  •  {}  •  %{}  •  {} / {}".format(
            content_label,
            item.name,
            progress,
            self._time_text(position),
            self._time_text(length),
        )

    def open_selected(self):
        if not self._entries or self._loading:
            return
        open_extplayer(
            self.session,
            self.client,
            self._entries[self.selected_index],
            self._entries,
            self.selected_index,
            owner=self,
        )


class GTFavoritesScreen(GTContinueWatchingScreen):
    """Local mixed-media favorites with one filterable poster grid."""

    page_size = 15

    FILTERS = (
        ("", N_("All")),
        ("live", N_("Live TV")),
        ("movie", N_("Movies")),
        ("series", N_("Series")),
    )

    def __init__(
        self,
        session,
        account,
        client=None,
        favorite_store=None,
        metadata_client=None,
    ):
        self._favorite_filter_index = 0
        self._favorite_counts = {"": 0, "live": 0, "movie": 0, "series": 0}
        self._move_active = False
        self._move_snapshot = None
        self._move_snapshot_index = 0
        store = (
            favorite_store if favorite_store is not None else FavoriteStore()
        )
        width, unused_height, px = _scale()
        del unused_height
        GTContinueWatchingScreen.__init__(
            self,
            session,
            account,
            client=client,
            resume_store=store,
            metadata_client=metadata_client,
            grid_screen_name="GTFavoritesScreen",
            grid_title=N_("Favorites"),
            grid_subtitle=(
                N_("Favorite channels, movies and series")
            ),
            grid_footer_items=FAVORITES_FOOTER_ITEMS,
            grid_footer_alternate_items=FAVORITES_MOVE_FOOTER_ITEMS,
            grid_skin=_favorites_skin(),
            grid_scroll_geometry=(
                width - px(73), px(190), px(7), px(630)
            ),
        )
        self.favorite_store = store
        self.favorite_scope = self.account_scope
        for index in range(len(self.FILTERS)):
            self["favorite_filter_{}".format(index)] = Label("")
            self["favorite_filter_focus_{}".format(index)] = Label("")
        self["favorite_info"] = Label("")
        for slot in range(self.page_size):
            for edge in ("top", "bottom", "left", "right"):
                self["continue_move_{}_{}".format(slot, edge)] = Label("")
        self._hide_move_frames()
        self._set_dynamic_breadcrumb(
            "{}  •  {}".format(
                self._safe_source_name(), localized_upper(_("Favorites"))
            ),
        )
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.ok_action,
                "cancel": self.exit_action,
                "left": self.move_left,
                "right": self.move_right,
                "up": self.move_grid_up,
                "down": self.move_grid_down,
                "red": self.remove_selected,
                "yellow": self.start_move,
                "green": self.refresh_favorites,
                "blue": self.cycle_filter,
            },
            -1,
        )
        self["channel_actions"] = ActionMap(
            ["MoviePlayerActions"],
            {
                "channelUp": self.previous_page,
                "channelDown": self.next_page,
            },
            -1,
        )

    def _safe_source_name(self):
        source_type = str(
            getattr(self.account, "source_type", "xtream") or "xtream"
        ).lower()
        if source_type == "stalker":
            return str(getattr(self.account, "host", "") or "STALKER")
        name = str(getattr(self.account, "name", "") or "").strip()
        if name:
            return name
        host = str(getattr(self.account, "host", "") or "").strip()
        return host or localized_upper(_("Source"))

    def _active_filter(self):
        return self.FILTERS[self._favorite_filter_index % len(self.FILTERS)]

    @staticmethod
    def _favorite_item(entry):
        metadata = entry.get("item", {})
        item = ContentItem(
            metadata.get("content_type"),
            metadata.get("stream_id"),
            metadata.get("name"),
            category_id=metadata.get("category_id"),
            extension=metadata.get("extension"),
            icon=metadata.get("icon"),
            rating=metadata.get("rating"),
            year=metadata.get("year"),
            parent_id=metadata.get("parent_id"),
        )
        if str(metadata.get("source_type", "") or "").lower() == "stalker":
            item.source_type = "stalker"
            request_type = str(
                metadata.get("portal_request_type", "") or ""
            ).lower()
            if request_type in ("itv", "vod", "movie", "series"):
                item.portal_request_type = request_type
        item.catalog_category_id = str(
            metadata.get("catalog_category_id", "") or ""
        )
        item.favorite_key = str(entry.get("key", "") or "")
        # The shared poster-grid completion code uses this compatibility name
        # when it safely refreshes a cached artwork URL.
        item.resume_key = item.favorite_key
        item.favorite_updated_at = int(entry.get("updated_at", 0) or 0)
        return item

    def _entries_for_type(self, content_type=""):
        entries = self.favorite_store.list_entries(
            self.favorite_scope,
            content_type,
        )
        return [self._favorite_item(entry) for entry in entries]

    def load_items(self):
        content_type = self._active_filter()[0]
        stored_entries = self.favorite_store.list_entries(self.favorite_scope)
        counts = {"": len(stored_entries), "live": 0, "movie": 0, "series": 0}
        for entry in stored_entries:
            item_type = str(entry.get("item", {}).get("content_type", "") or "")
            if item_type in counts:
                counts[item_type] += 1
        self._favorite_counts = counts
        entries = [
            self._favorite_item(entry)
            for entry in stored_entries
            if not content_type
            or str(entry.get("item", {}).get("content_type", "") or "") == content_type
        ]
        if not _is_m3u_client(self.client):
            return entries
        if not entries:
            return []
        resolver = getattr(self.client, "resolve_items", None)
        if not callable(resolver):
            return []
        resolved_by_id = {
            str(getattr(item, "stream_id", "") or ""): item
            for item in resolver(entries)
        }
        output = []
        for saved in entries:
            stream_id = str(getattr(saved, "stream_id", "") or "")
            item = resolved_by_id.get(stream_id)
            if item is None:
                # Keep stale favorites visible so RED can remove them.  They
                # are never handed to the player.
                item = saved
                item.m3u_stale = True
            item.favorite_key = str(
                getattr(saved, "favorite_key", "") or ""
            )
            item.resume_key = item.favorite_key
            item.favorite_updated_at = int(
                getattr(saved, "favorite_updated_at", 0) or 0
            )
            output.append(item)
        return output

    def _continue_provider_candidates(self, item):
        """Keep favorite artwork work independent from catalog requests.

        Favorite snapshots already contain a credential-free public artwork
        URL when one is available.  A missing image is allowed to use the
        optional metadata fallback, but must not occupy the shared provider
        request slots needed to open a selected series.
        """
        return [], None

    def _render_continue_posters(self, page_items, page_start):
        if self._move_active:
            self._cancel_continue_poster_work()
            return
        return GTContinueWatchingScreen._render_continue_posters(
            self,
            page_items,
            page_start,
        )

    def _poster_job_allowed(self, item, stored_url):
        # Favorites keep only credential-free public artwork URLs.  Download
        # one only when its card is on the visible 15-item page.
        return bool(stored_url) or getattr(item, "content_type", "") != "live"

    def empty_message(self):
        return N_("No favorites have been added.")

    @staticmethod
    def _favorite_content_label(item):
        labels = {
            "live": N_("Live TV"),
            "movie": N_("Movies"),
            "series": N_("Series"),
        }
        return localized_upper(_(labels.get(item.content_type, N_("Favorite"))))

    def _render_continue_card(self, slot, item, selected):
        content_label = self._favorite_content_label(item)
        placeholder = self["continue_poster_placeholder_{}".format(slot)]
        placeholder.setText("GT\n{}".format(content_label))
        placeholder.show()
        self["continue_poster_{}".format(slot)].hide()
        self["continue_type_{}".format(slot)].setText(content_label)
        ellipsize_dynamic_text(
            self["continue_title_{}".format(slot)],
            item.name,
            fallback_chars=25,
        )
        for name in (
            "continue_card_{}",
            "continue_type_{}",
            "continue_title_{}",
        ):
            self[name.format(slot)].show()
        self["continue_meta_{}".format(slot)].hide()
        self["continue_progress_bg_{}".format(slot)].hide()
        self["continue_progress_fill_{}".format(slot)].hide()
        self["continue_progress_text_{}".format(slot)].hide()
        self["continue_favorite_{}".format(slot)].hide()
        for edge in ("top", "bottom", "left", "right"):
            widget = self["continue_focus_{}_{}".format(slot, edge)]
            move_widget = self["continue_move_{}_{}".format(slot, edge)]
            if selected and not self._move_active:
                widget.show()
            else:
                widget.hide()
            if selected and self._move_active:
                move_widget.show()
            else:
                move_widget.hide()

    def start_load(self):
        return GTContinueWatchingScreen.start_load(self)

    def _hide_move_frames(self):
        for slot in range(self.page_size):
            for edge in ("top", "bottom", "left", "right"):
                self["continue_move_{}_{}".format(slot, edge)].hide()

    def _update_filter_tabs(self):
        for index, (content_type, label) in enumerate(self.FILTERS):
            count = int(self._favorite_counts.get(content_type, 0) or 0)
            self["favorite_filter_{}".format(index)].setText(
                "{}  {}".format(localized_upper(_(label)), count)
            )
            focus = self["favorite_filter_focus_{}".format(index)]
            if index == self._favorite_filter_index:
                focus.show()
            else:
                focus.hide()

    def _refresh(self):
        self._clear_continue_cards()
        self._hide_move_frames()
        self._update_filter_tabs()
        total = int(self._favorite_counts.get("", 0) or 0)
        if self._error:
            self["screen_label"].setText(
                "{} {}  •  {} 0 / 0".format(
                    total, localized_upper(_("Favorites")), localized_upper(_("Page"))
                )
            )
            ellipsize_dynamic_text(
                self["favorite_info"],
                _(self._error),
                fallback_chars=110,
            )
            hide_scrollbar(self, "list_scroll")
            return
        if not self._entries:
            self.selected_index = 0
            self["screen_label"].setText(
                "{} {}  •  {} 0 / 0".format(
                    total, localized_upper(_("Favorites")), localized_upper(_("Page"))
                )
            )
            self["favorite_info"].setText(_(self.empty_message()))
            hide_scrollbar(self, "list_scroll")
            return
        self.selected_index = min(max(0, int(self.selected_index)), len(self._entries) - 1)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start : page_start + self.page_size]
        for slot, item in enumerate(page_items):
            self._render_continue_card(
                slot, item, page_start + slot == self.selected_index
            )
        page_count = int((len(self._entries) + self.page_size - 1) / self.page_size)
        page = int(page_start / self.page_size) + 1
        self["screen_label"].setText(
            "{} {}  •  {} {} / {}".format(
                total,
                localized_upper(_("Favorites")),
                localized_upper(_("Page")),
                page,
                page_count,
            )
        )
        selected = self._entries[self.selected_index]
        ellipsize_dynamic_text(
            self["favorite_info"],
            "{}  •  {}  •  {}".format(
                selected.name,
                self._favorite_content_label(selected),
                self._safe_source_name(),
            ),
            fallback_chars=110,
        )
        self["message"].setText(
            "{} | {}/{}".format(len(self._entries), self.selected_index + 1, len(self._entries))
        )
        update_scrollbar(
            self,
            "list_scroll",
            len(self._entries),
            self.selected_index,
            self.page_size,
            self._list_scroll_geometry,
        )
        self._render_continue_posters(page_items, page_start)

    def cycle_filter(self):
        if self._loading or self._move_active:
            return
        self._stop_continue_posters()
        self._favorite_filter_index = (
            self._favorite_filter_index + 1
        ) % len(self.FILTERS)
        self._continue_poster_page_key = None
        self.selected_index = 0
        self.start_load()

    def _select_favorite(self, index):
        if not self._entries or self._loading:
            return
        self.selected_index = min(max(0, int(index)), len(self._entries) - 1)
        self._refresh()

    def _move_reordered_item(self, delta):
        if not self._entries or not self._move_active:
            return
        target = min(
            max(0, self.selected_index + int(delta)),
            len(self._entries) - 1,
        )
        if target == self.selected_index:
            return
        item = self._entries.pop(self.selected_index)
        self._entries.insert(target, item)
        self.selected_index = target
        self._continue_poster_page_key = None
        self._refresh()

    def move_left(self):
        if self._move_active:
            self._move_reordered_item(-1)
        else:
            self._select_favorite(self.selected_index - 1)

    def move_right(self):
        if self._move_active:
            self._move_reordered_item(1)
        else:
            self._select_favorite(self.selected_index + 1)

    def move_grid_up(self):
        if self._move_active:
            self._move_reordered_item(-5)
        elif self.selected_index >= 5:
            self._select_favorite(self.selected_index - 5)

    def move_grid_down(self):
        if self._move_active:
            self._move_reordered_item(5)
        else:
            self._select_favorite(self.selected_index + 5)

    def _change_page(self, direction):
        if not self._entries or self._loading:
            return
        if self._move_active:
            self._move_reordered_item(int(direction) * self.page_size)
            return
        current_start = int(self.selected_index / self.page_size) * self.page_size
        last_start = int((len(self._entries) - 1) / self.page_size) * self.page_size
        target_start = max(
            0,
            min(current_start + (int(direction) * self.page_size), last_start),
        )
        if target_start == current_start:
            return
        slot = self.selected_index - current_start
        target_count = min(self.page_size, len(self._entries) - target_start)
        self._select_favorite(target_start + min(slot, target_count - 1))

    def previous_page(self):
        self._change_page(-1)

    def next_page(self):
        self._change_page(1)

    def start_move(self):
        if self._loading or self._move_active or not self._entries:
            return
        self._stop_continue_posters()
        self._continue_poster_page_key = None
        self._move_snapshot = list(self._entries)
        self._move_snapshot_index = self.selected_index
        self._move_active = True
        set_remote_footer(self, FAVORITES_MOVE_FOOTER_ITEMS)
        self._refresh()

    def _leave_move_mode(self):
        self._move_active = False
        self._move_snapshot = None
        self._continue_poster_page_key = None
        set_remote_footer(self, FAVORITES_FOOTER_ITEMS)

    def save_move(self):
        if not self._move_active:
            return
        keys = [str(getattr(item, "favorite_key", "") or "") for item in self._entries]
        content_type = self._active_filter()[0]
        if not self.favorite_store.reorder(
            self.favorite_scope, keys, content_type=content_type
        ):
            snapshot = list(self._move_snapshot or ())
            self._entries = snapshot
            self.selected_index = min(
                self._move_snapshot_index, max(0, len(snapshot) - 1)
            )
            self._leave_move_mode()
            self._refresh()
            self["favorite_info"].setText(_("Could not save the favorite order."))
            return
        self._leave_move_mode()
        self._refresh()
        self["favorite_info"].setText(_("Favorite order saved."))

    def cancel_move(self):
        if not self._move_active:
            return
        self._entries = list(self._move_snapshot or ())
        self.selected_index = min(
            self._move_snapshot_index,
            max(0, len(self._entries) - 1),
        )
        self._leave_move_mode()
        self._refresh()
        self["favorite_info"].setText(_("Move cancelled."))

    def ok_action(self):
        if self._move_active:
            self.save_move()
        else:
            self.open_selected()

    def exit_action(self):
        if self._move_active:
            self.cancel_move()
        else:
            self.close()

    def refresh_favorites(self):
        if not self._move_active:
            self.start_load()

    def remove_selected(self):
        if not self._entries or self._loading or self._move_active:
            return
        item = self._entries[self.selected_index]
        key = str(getattr(item, "favorite_key", "") or "")
        if not key or not self.favorite_store.remove(key, self.favorite_scope):
            self["message"].setText(_("Could not update favorites."))
            return
        self._entries.pop(self.selected_index)
        item_type = str(getattr(item, "content_type", "") or "")
        self._favorite_counts[""] = max(0, self._favorite_counts.get("", 0) - 1)
        if item_type in self._favorite_counts:
            self._favorite_counts[item_type] = max(
                0, self._favorite_counts.get(item_type, 0) - 1
            )
        self.selected_index = min(
            self.selected_index,
            max(0, len(self._entries) - 1),
        )
        self._continue_poster_page_key = None
        self._refresh()
        ellipsize_dynamic_text(
            self["message"],
            _("{} removed from favorites.").format(item.name),
            fallback_chars=80,
        )

    def open_selected(self):
        if not self._entries or self._loading or self._move_active:
            return
        item = self._entries[self.selected_index]
        if _is_m3u_client(self.client):
            if bool(getattr(item, "m3u_stale", False)):
                self["message"].setText(
                    _("This channel is no longer present in the M3U source.")
                )
                return
            playable = [
                entry
                for entry in self._entries
                if not bool(getattr(entry, "m3u_stale", False))
            ]
            try:
                selected_index = playable.index(item)
                open_extplayer(
                    self.session,
                    self.client,
                    item,
                    playable,
                    selected_index,
                    owner=self,
                )
            except (ContentError, TypeError, ValueError):
                self["message"].setText(
                    _("This channel is no longer present in the M3U source.")
                )
            return
        if item.content_type == "series":
            # Favorites created while an episode was active may carry the
            # episode stream ID together with its parent series ID.  Xtream's
            # get_series_info endpoint accepts only the parent series ID.
            # Keep the stored favorite key on the grid item, but open the
            # episode browser with a canonical parent item.
            parent_id = str(getattr(item, "parent_id", "") or "").strip()
            series = item
            if parent_id and parent_id != str(item.stream_id):
                series = ContentItem(
                    "series",
                    parent_id,
                    item.name,
                    category_id=getattr(item, "category_id", ""),
                    icon=getattr(item, "icon", ""),
                    rating=getattr(item, "rating", ""),
                    year=getattr(item, "year", ""),
                )
                if getattr(item, "source_type", "") == "stalker":
                    series.source_type = "stalker"
                    series.portal_request_type = str(
                        getattr(item, "portal_request_type", "") or ""
                    )
                series.catalog_category_id = str(
                    getattr(item, "catalog_category_id", "") or ""
                )
            self._suspend_for_playback()
            # Poster completion may still be returning from a provider call
            # when OK is pressed.  Give the episode screen its own short-lived
            # client so it never waits behind obsolete artwork requests.
            episode_client = (
                self.client
                if _is_stalker_client(self.client)
                else content_client_for(self.account)
            )
            try:
                self.session.open(
                    GTEpisodeListScreen,
                    self.account,
                    series,
                    episode_client,
                    favorite_store=self.favorite_store,
                )
            except Exception as error:
                log_event(
                    "favorites",
                    "Could not open favorite series id={}".format(
                        _debug_identifier(getattr(series, "stream_id", ""))
                    ),
                    error=error,
                )
                self["message"].setText(
                    _("Could not open the content list.")
                )
            return
        playable = self._entries_for_type(item.content_type)
        selected_index = 0
        item_key = str(getattr(item, "favorite_key", "") or "")
        for index, candidate in enumerate(playable):
            if str(getattr(candidate, "favorite_key", "") or "") == item_key:
                selected_index = index
                break
        if playable:
            open_extplayer(
                self.session,
                self.client,
                playable[selected_index],
                playable,
                selected_index,
                owner=self,
            )


class GTAllMoviesSearchScreen(_FavoriteSupport, Screen):
    """Search movies through the provider's bounded page API."""

    page_size = ALL_MOVIES_SEARCH_PAGE_SIZE
    provider_page_size = 14
    page_cache_limit = 5

    def __init__(
        self,
        session,
        account,
        client=None,
        favorite_store=None,
        hidden_category_ids=None,
    ):
        self.skin = decorate_remote_footer(
            _all_movies_search_skin(),
            ALL_MOVIES_SEARCH_FOOTER_ITEMS,
            transparent_panel=True,
            show_dividers=False,
        )
        Screen.__init__(self, session)
        self.account = account
        self.client = client or content_client_for(account)
        self._init_favorite_support(favorite_store)
        self._hidden_category_ids = hidden_category_match_ids(
            (),
            hidden_category_ids,
        )
        self._closed = False
        self._query = ""
        self._query_key = ""
        self._results = []
        self.selected_index = 0
        self._current_page = 1
        self._result_page_size = self.provider_page_size
        self._total_items = 0
        self._has_more = False
        self._known_last_page = None
        self._supported = True
        self._loading = False
        self._search_reason = ""
        self._search_source = ""
        self._search_generation = 0
        self._search_token = None
        self._requested_page = 0
        self._pending_page_index = 0
        self._page_cache = OrderedDict()
        self._event_lock = threading.Lock()
        self._events = deque()
        self._provider_worker_lock = threading.Lock()
        self._provider_worker_count = 0
        self._provider_workers_idle = threading.Event()
        self._provider_workers_idle.set()
        self._keyboard_requested = False
        self._poll_timer = eTimer()
        _connect_timer(self._poll_timer, self._poll_search)
        self._keyboard_timer = eTimer()
        _connect_timer(self._keyboard_timer, self.open_keyboard)

        attach_background(self, "app_bg", APP_BACKGROUND_PATH)
        self["top_accent"] = Label("")
        self["header"] = Label(_("Search All Movies"))
        self["server"] = Label("")
        self._dynamic_search_server_text = (
            account.host or account.display_name
        )
        self._refit_search_server()
        self["query_panel"] = Label("")
        self["query_accent"] = Label("")
        self["query_caption"] = Label(_("Search"))
        self["query_text"] = Label(_("Enter a movie name."))
        self["result_panel"] = Label("")
        self["result_accent"] = Label("")
        self["result_caption"] = Label(_("All Movies"))
        for index in range(self.page_size):
            self["catalog_result_{}".format(index)] = Label("")
            self["catalog_result_focus_{}".format(index)] = Label("")
            self["catalog_result_marker_{}".format(index)] = Label("")
        self["results_scroll_track"] = Label("")
        self["results_scroll_thumb"] = Label("")
        hide_scrollbar(self, "results_scroll")
        self["detail_panel"] = Label("")
        self["detail_accent"] = Label("")
        self["detail_title"] = Label(_("Selecting movie..."))
        self["detail_rating"] = Label("")
        self["detail_meta"] = Label("")
        self["detail_category"] = Label("")
        self["detail_plot_caption"] = Label(_("MOVIE SUMMARY"))
        self["detail_plot"] = Label(_("Enter a movie name."))
        self["status"] = Label(_("Enter a movie name."))
        self["message"] = Label("")
        self["footer"] = Label("")
        install_remote_footer(self, ALL_MOVIES_SEARCH_FOOTER_ITEMS)
        width, height, px = _scale()
        self._search_title_preferred_font = font_px(
            px,
            35,
            role="title",
        )
        self._search_title_min_font = font_px(
            px,
            35,
            role="title",
            value="standard",
        )
        list_x = px(42)
        list_y = px(205)
        row_y = list_y + px(62)
        row_h = px(64)
        self._scroll_geometry = (
            list_x + px(1120) - px(16),
            row_y,
            px(8),
            (row_h * self.page_size) - px(7),
        )
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "red": self.toggle_favorite,
                "green": self.refresh_catalog,
                "blue": self.open_keyboard,
            },
            -1,
        )
        if hasattr(self, "onShown"):
            self.onShown.append(self._screen_shown)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refit_search_server)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop)
        self.setTitle(_("Search All Movies"))
        self._render()

    def _refit_search_server(self):
        ellipsize_dynamic_text(
            self["server"],
            self._dynamic_search_server_text,
            fallback_chars=80,
        )

    def _screen_shown(self):
        if self._closed:
            return
        if self._favorite_screen_shown():
            self._render()
        if not self._keyboard_requested:
            self._keyboard_requested = True
            self._keyboard_timer.start(40, True)

    def open_keyboard(self):
        if self._closed:
            return
        try:
            self._keyboard_timer.stop()
        except Exception:
            pass
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["status"].setText(
                _("The virtual keyboard is unavailable on this image.")
            )
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["status"].setText(_("Could not open the virtual keyboard."))
            return
        opener(
            self._query_entered,
            VirtualKeyBoard,
            title=_("Search All Movies"),
            text=self._query,
        )

    def _cancel_search_request(self, invalidate_generation=False):
        if invalidate_generation:
            self._search_generation += 1
        _cancel_worker_token(self._search_token)
        self._search_token = None
        self._loading = False
        self._requested_page = 0
        with self._event_lock:
            self._events.clear()
        try:
            self._poll_timer.stop()
        except Exception:
            pass

    def _reset_search_results(self):
        self._results = []
        self.selected_index = 0
        self._current_page = 1
        self._result_page_size = self.provider_page_size
        self._total_items = 0
        self._has_more = False
        self._known_last_page = None
        self._supported = True
        self._search_reason = ""
        self._search_source = ""

    def _query_entered(self, value):
        if self._closed or value is None:
            return
        query = " ".join(str(value or "").split())[:120]
        query_key = _movie_search_text(query)
        changed = query_key != self._query_key
        self._query = query
        ellipsize_dynamic_text(
            self["query_text"],
            query or _("Enter a movie name."),
            fallback_chars=90,
        )
        if changed:
            self._cancel_search_request(invalidate_generation=True)
            self._page_cache.clear()
            self._reset_search_results()
            self._query_key = query_key
        if not query_key:
            self._render()
            return
        self._load_page(1, pending_index=0)

    def _cache_key(self, page):
        return (self._query_key, int(page))

    def _cached_page(self, page):
        key = self._cache_key(page)
        result = self._page_cache.get(key)
        if result is not None:
            self._page_cache.move_to_end(key)
        return result

    def _cache_page(self, result, page):
        key = self._cache_key(page)
        self._page_cache[key] = result
        self._page_cache.move_to_end(key)
        while len(self._page_cache) > self.page_cache_limit:
            self._page_cache.popitem(last=False)

    @staticmethod
    def _page_value(result, name, default=None):
        if isinstance(result, dict):
            return result.get(name, default)
        return getattr(result, name, default)

    def _load_page(self, page, pending_index=0):
        if self._closed or not self._query_key:
            return False
        try:
            page = max(1, int(page))
        except (TypeError, ValueError, OverflowError):
            page = 1
        if (
            self._known_last_page is not None
            and page > self._known_last_page
        ):
            self._has_more = False
            self._render()
            return False
        cached = self._cached_page(page)
        if cached is not None:
            self._apply_search_page(cached, page, pending_index)
            return True
        if self._loading:
            self["status"].setText(_("Please wait"))
            return False
        searcher = getattr(self.client, "search_movies", None)
        if not callable(searcher):
            self._supported = False
            self._search_reason = "unsupported"
            self._results = []
            self._render()
            return False

        token = _WorkerToken(MOVIE_SEARCH_TIMEOUT_SECONDS)
        self._search_token = token
        self._loading = True
        self._requested_page = page
        self._pending_page_index = int(pending_index)
        self._search_reason = ""
        # Give every network attempt a distinct generation.  A timed-out
        # urllib worker can finish later; its error must not cancel a retry
        # for the same query and page.
        self._search_generation += 1
        generation = self._search_generation
        worker = threading.Thread(
            target=self._search_page_worker,
            args=(generation, self._query, page, token),
        )
        worker.daemon = True
        self._provider_worker_started()
        try:
            worker.start()
        except (RuntimeError, OSError):
            self._provider_worker_finished()
            _cancel_worker_token(token)
            if self._search_token is token:
                self._search_token = None
                self._loading = False
                self._requested_page = 0
                self._search_reason = "search_error"
            self._render()
            return False
        self._render()
        self._poll_timer.start(60, True)
        return True

    def _queue_search_event(self, event):
        with self._event_lock:
            if self._closed or event[1] != self._search_generation:
                return
            self._events.append(event)

    def _provider_worker_started(self):
        with self._provider_worker_lock:
            self._provider_worker_count += 1
            self._provider_workers_idle.clear()

    def _provider_worker_finished(self):
        with self._provider_worker_lock:
            self._provider_worker_count = max(
                0,
                self._provider_worker_count - 1,
            )
            if self._provider_worker_count == 0:
                self._provider_workers_idle.set()

    def _search_page_worker(self, generation, query, page, token):
        try:
            try:
                token.check()
                searcher = getattr(self.client, "search_movies", None)
                if not callable(searcher):
                    self._queue_search_event(
                        ("error", generation, page, "unsupported")
                    )
                    return

                def search_page():
                    source_type = str(
                        getattr(self.client, "source_type", "") or ""
                    ).lower()
                    if self._hidden_category_ids and (
                        source_type == "xtream"
                        or isinstance(self.client, XtreamContentClient)
                    ):
                        try:
                            return searcher(
                                query,
                                page=page,
                                page_size=self.provider_page_size,
                                hidden_category_ids=(
                                    self._hidden_category_ids
                                ),
                            )
                        except TypeError:
                            pass
                    return searcher(
                        query,
                        page=page,
                        page_size=self.provider_page_size,
                    )

                request_scope = getattr(self.client, "request_scope", None)
                if callable(request_scope):
                    with request_scope(
                        cancel_event=token.cancel_event,
                        deadline=token.deadline,
                    ):
                        result = search_page()
                else:
                    result = search_page()
                token.check()
                self._queue_search_event(("page", generation, page, result))
            except _WorkerCancelled:
                return
            except ContentError as error:
                self._queue_search_event(
                    (
                        "error",
                        generation,
                        page,
                        str(
                            getattr(error, "reason", "")
                            or "search_error"
                        ),
                    )
                )
            except Exception:
                self._queue_search_event(
                    ("error", generation, page, "search_error")
                )
        finally:
            self._provider_worker_finished()

    def _apply_search_page(self, result, requested_page, pending_index=0):
        previous_page = self._current_page
        previous_page_size = self._result_page_size
        supported = bool(self._page_value(result, "supported", True))
        page = self._page_value(result, "page", requested_page)
        try:
            page = max(1, int(page))
        except (TypeError, ValueError, OverflowError):
            page = max(1, int(requested_page))
        self._search_source = str(
            self._page_value(result, "source", "") or ""
        )
        try:
            result_page_size = int(
                self._page_value(
                    result,
                    "page_size",
                    self.provider_page_size,
                )
                or self.provider_page_size
            )
        except (TypeError, ValueError, OverflowError):
            result_page_size = self.provider_page_size
        result_page_size = max(1, result_page_size)
        if not supported:
            # A later Portal page can be malformed even though page one was
            # valid.  Keep the usable page visible and close the forward
            # boundary instead of replacing good results with an empty view.
            if page > 1 and self._results:
                self._known_last_page = max(1, page - 1)
                self._total_items = (
                    (previous_page - 1) * previous_page_size
                    + len(self._results)
                )
                self._has_more = False
                self._search_reason = "unsupported"
                self._render()
                return
            self._cache_page(result, page)
            self._supported = False
            self._search_reason = "unsupported"
            self._current_page = page
            self._result_page_size = result_page_size
            self._results = []
            self.selected_index = 0
            self._total_items = 0
            self._has_more = False
            self._render()
            return

        self._cache_page(result, page)
        self._supported = True
        self._search_reason = ""
        raw_result_items = list(
            self._page_value(result, "items", ()) or ()
        )
        result_items = filter_hidden_category_items(
            raw_result_items,
            self._hidden_category_ids,
        )
        result_has_more = bool(
            self._page_value(result, "has_more", False)
        )
        try:
            result_total = max(
                0,
                int(self._page_value(result, "total_items", 0) or 0),
            )
        except (TypeError, ValueError, OverflowError):
            result_total = 0

        # Some Ministra forks report the first page size as ``total_items``.
        # The client deliberately permits one lazy confirmation page in that
        # case.  If that next page is empty, or its terminal rows are all from
        # hidden categories, keep the last populated page on screen and
        # remember the boundary; otherwise navigation would land on an empty
        # page with no way back.
        if (
            page > 1
            and not result_items
            and self._results
            and (not raw_result_items or not result_has_more)
        ):
            self._known_last_page = max(1, page - 1)
            self._total_items = (
                (previous_page - 1) * previous_page_size
                + len(self._results)
            )
            self._has_more = False
            self._render()
            return

        self._current_page = page
        self._result_page_size = result_page_size
        self._results = result_items
        self._total_items = result_total
        if not result_has_more:
            self._known_last_page = page
        self._has_more = bool(
            result_has_more
            and (
                self._known_last_page is None
                or page < self._known_last_page
            )
        )
        try:
            pending_index = int(pending_index)
        except (TypeError, ValueError, OverflowError):
            pending_index = 0
        if self._results:
            if pending_index < 0:
                pending_index = len(self._results) + pending_index
            self.selected_index = min(
                len(self._results) - 1,
                max(0, pending_index),
            )
        else:
            self.selected_index = 0
        self._render()

    def _poll_search(self):
        if self._closed:
            return
        events = []
        with self._event_lock:
            while self._events:
                events.append(self._events.popleft())
        changed = False
        for event in events:
            if event[1] != self._search_generation:
                continue
            if event[2] != self._requested_page:
                continue
            self._loading = False
            self._search_token = None
            self._requested_page = 0
            if event[0] == "page":
                pending_index = self._pending_page_index
                self._pending_page_index = 0
                self._apply_search_page(
                    event[3],
                    event[2],
                    pending_index,
                )
            else:
                self._pending_page_index = 0
                self._search_reason = str(event[3] or "search_error")
            changed = True

        # Drain a result that was already queued before declaring timeout.
        # Otherwise a valid response arriving on the exact deadline tick is
        # discarded after ``_requested_page`` has been reset to zero.
        token = self._search_token
        if self._loading and token is not None and token.expired():
            _cancel_worker_token(token)
            if self._search_token is token:
                self._search_token = None
                self._loading = False
                self._requested_page = 0
                self._search_reason = ContentError.DEADLINE
                changed = True
        if changed or (not self._loading and self._search_reason):
            self._render()
        with self._event_lock:
            has_events = bool(self._events)
        if self._loading or has_events:
            self._poll_timer.start(60, True)

    @staticmethod
    def _reason_text(reason):
        reason = str(reason or "")
        if reason in (ContentError.DEADLINE, "deadline"):
            return _("Request timed out")
        if reason in (ContentError.CANCELLED, "cancelled"):
            return _("Request cancelled")
        if reason == ContentError.ACCESS_DENIED:
            return _("Server denied access")
        if reason == ContentError.RATE_LIMITED:
            return _("Server request limit reached")
        if reason == "unsupported":
            return _(
                "This source does not support the selected content type."
            )
        return _("Could not load movies")

    def _status_text(self):
        if not self._query_key:
            return _("Enter a movie name.")
        if not self._supported:
            return _(
                "This source does not support the selected content type."
            )
        if self._loading:
            return _("Please wait")
        if self._search_reason:
            return self._reason_text(self._search_reason)
        return _("{movies} movies • {results} results").format(
            movies=self._total_items or len(self._results),
            results=len(self._results),
        )

    def _render(self):
        if self._results:
            self.selected_index = min(
                len(self._results) - 1,
                max(0, self.selected_index),
            )
        else:
            self.selected_index = 0
        visual_start = int(self.selected_index / self.page_size) * self.page_size
        visible = self._results[visual_start : visual_start + self.page_size]
        global_start = (self._current_page - 1) * self._result_page_size
        for row in range(self.page_size):
            selected = False
            if row < len(visible):
                local_index = visual_start + row
                item = visible[row]
                favorite = "  ★" if self._is_favorite(item) else ""
                year = str(getattr(item, "year", "") or "")
                suffix = "  •  {}".format(year) if year else ""
                ellipsize_dynamic_text(
                    self["catalog_result_{}".format(row)],
                    "{:05d}.  {}{}{}".format(
                        global_start + local_index + 1,
                        str(getattr(item, "name", "") or _("Movie")),
                        suffix,
                        favorite,
                    ),
                    fallback_chars=64,
                )
                selected = local_index == self.selected_index
            else:
                self["catalog_result_{}".format(row)].setText("")
            for name in (
                "catalog_result_focus_{}".format(row),
                "catalog_result_marker_{}".format(row),
            ):
                if selected:
                    self[name].show()
                else:
                    self[name].hide()
        if self._results:
            update_scrollbar(
                self,
                "results_scroll",
                len(self._results),
                self.selected_index,
                self.page_size,
                self._scroll_geometry,
            )
            global_position = global_start + self.selected_index + 1
            total = self._total_items or (global_start + len(self._results))
            self["message"].setText(
                "{}/{}".format(global_position, total)
            )
        else:
            hide_scrollbar(self, "results_scroll")
            if not self._supported:
                self["message"].setText(
                    _(
                        "This source does not support the selected "
                        "content type."
                    )
                )
            elif self._search_reason:
                self["message"].setText(_("Could not load movies"))
            elif self._query_key and not self._loading:
                self["message"].setText(_("No movies matched this search."))
            else:
                self["message"].setText("")
        self["status"].setText(self._status_text())
        self._render_detail()

    def _render_detail(self):
        if not self._results:
            set_static_text(
                self["detail_title"],
                _("Selecting movie..."),
                preferred_size=self._search_title_preferred_font,
            )
            self["detail_rating"].setText("")
            self["detail_meta"].setText("")
            self["detail_category"].setText("")
            if not self._supported:
                text = _(
                    "This source does not support the selected content type."
                )
            elif self._search_reason:
                text = _("Could not load movies")
            elif self._query_key and not self._loading:
                text = _("No movies matched this search.")
            else:
                text = _("Enter a movie name.")
            self["detail_plot"].setText(text)
            return
        item = self._results[self.selected_index]
        item_name = str(getattr(item, "name", "") or "")
        if item_name:
            fit_dynamic_text(
                self["detail_title"],
                item_name,
                max_lines=2,
                preferred_size=self._search_title_preferred_font,
                min_size=self._search_title_min_font,
                fallback_chars=30,
            )
        else:
            set_static_text(
                self["detail_title"],
                _("Movie"),
                preferred_size=self._search_title_preferred_font,
            )
        self["detail_rating"].setText(
            _rating_label(getattr(item, "rating", ""))
        )
        meta = [
            value
            for value in (
                _year_not_in_title(
                    item_name,
                    getattr(item, "year", ""),
                ),
                str(getattr(item, "duration", "") or ""),
            )
            if value
        ]
        ellipsize_dynamic_text(
            self["detail_meta"],
            "  •  ".join(meta) or _("Information unavailable."),
            fallback_chars=34,
        )
        ellipsize_dynamic_text(
            self["detail_category"],
            str(
                getattr(item, "catalog_category_name", "")
                or getattr(item, "group_title", "")
                or ""
            ),
            fallback_chars=45,
        )
        fit_dynamic_text(
            self["detail_plot"],
            str(getattr(item, "plot", "") or "")
            or _("Movie summary unavailable."),
            max_lines=7,
            fallback_chars=48,
        )

    def _select(self, index):
        if self._loading:
            return
        try:
            index = int(index)
        except (TypeError, ValueError, OverflowError):
            return
        if not self._results:
            if index >= 0 and self._has_more:
                self._load_page(self._current_page + 1, pending_index=0)
            elif index < 0 and self._current_page > 1:
                self._load_page(self._current_page - 1, pending_index=-1)
            return
        if 0 <= index < len(self._results):
            self.selected_index = index
            self._render()
            return
        if index >= len(self._results):
            if self._has_more:
                self._load_page(
                    self._current_page + 1,
                    pending_index=index - len(self._results),
                )
            else:
                self.selected_index = len(self._results) - 1
                self._render()
            return
        if self._current_page > 1:
            self._load_page(
                self._current_page - 1,
                pending_index=index,
            )
        else:
            self.selected_index = 0
            self._render()

    def move_up(self):
        self._select(self.selected_index - 1)

    def move_down(self):
        self._select(self.selected_index + 1)

    def page_up(self):
        self._select(self.selected_index - self.page_size)

    def page_down(self):
        self._select(self.selected_index + self.page_size)

    def refresh_catalog(self):
        # Do not start a second full Xtream VOD response while the first
        # worker may still be blocked in urllib.  EXIT remains immediate and
        # a completed/failed request can be refreshed normally.
        if self._loading or not self._provider_workers_idle.is_set():
            self["status"].setText(_("Please wait"))
            return
        if not self._query_key:
            self.open_keyboard()
            return
        self._cancel_search_request(invalidate_generation=True)
        self._release_xtream_search_cache()
        query_key = self._query_key
        for key in tuple(self._page_cache):
            if key[0] == query_key:
                self._page_cache.pop(key, None)
        self._reset_search_results()
        self._query_key = query_key
        self._render()
        self._load_page(1, pending_index=0)

    def toggle_favorite(self):
        if not self._results:
            self["status"].setText(
                _("Select content before using favorites.")
            )
            return
        item = self._results[self.selected_index]
        added = self._toggle_favorite_item(item)
        self._render()
        fit_dynamic_text(
            self["status"],
            self._favorite_message(item, added),
            max_lines=4,
            fallback_chars=48,
        )

    def _release_xtream_search_cache(self):
        source_type = str(
            getattr(self.client, "source_type", "") or ""
        ).lower()
        if not (
            source_type == "xtream"
            or isinstance(self.client, XtreamContentClient)
        ):
            return
        releaser = getattr(self.client, "release_movie_search", None)
        if not callable(releaser):
            return
        try:
            releaser()
        except Exception as error:
            log_event(
                "movies",
                "Could not release movie search cache",
                error=error,
            )

    def open_selected(self):
        if not self._results:
            return
        if self._loading:
            self["status"].setText(_("Please wait"))
            return
        open_extplayer(
            self.session,
            self.client,
            self._results[self.selected_index],
            list(self._results),
            self.selected_index,
            owner=self,
        )

    def _stop(self):
        if self._closed:
            return
        self._closed = True
        self._cancel_search_request(invalidate_generation=True)
        self._page_cache.clear()
        try:
            self._keyboard_timer.stop()
        except Exception:
            pass
        self._release_xtream_search_cache()


class GTM3UChannelSearchScreen(_FavoriteSupport, GTAsyncListScreen):
    """Search the already prepared M3U catalogue without artwork or EPG I/O."""

    def __init__(
        self,
        session,
        account,
        client=None,
        favorite_store=None,
        hidden_category_ids=None,
    ):
        self.account = account
        self.client = client or content_client_for(account)
        self._query = ""
        self._keyboard_requested = False
        self._keyboard_timer = eTimer()
        _connect_timer(self._keyboard_timer, self.open_keyboard)
        self._init_favorite_support(favorite_store)
        self._hidden_category_ids = hidden_category_match_ids(
            (),
            hidden_category_ids,
        )
        self._footer_layout_items = M3U_SEARCH_FOOTER_ITEMS
        GTAsyncListScreen.__init__(
            self,
            session,
            "GTM3UChannelSearchScreen",
            N_("Search All Channels"),
            "{}  >  {}".format(
                account.display_name,
                _("All Channels"),
            ),
        )
        self["screen_label"].setText(_("M3U CHANNEL SEARCH"))
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "red": self.toggle_favorite,
                "blue": self.open_keyboard,
            },
            -1,
        )
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop_search)

    def _start_initial_load(self):
        if self._closed or self._keyboard_requested:
            return
        self._keyboard_requested = True
        self._keyboard_timer.start(40, True)

    def open_keyboard(self):
        if self._closed:
            return
        if self._loading:
            self["message"].setText(_("Please wait"))
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(
                _("The virtual keyboard is unavailable on this image.")
            )
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        opener(
            self._query_entered,
            VirtualKeyBoard,
            title=_("Search All Channels"),
            text=self._query,
        )

    def _query_entered(self, value):
        if self._closed or value is None:
            return
        self._query = " ".join(str(value or "").split())[:120]
        if not self._query:
            self._entries = []
            self._ready = True
            self._error = ""
            self._refresh()
            return
        self.start_load()

    def load_items(self):
        searcher = getattr(self.client, "search_items", None)
        if not callable(searcher):
            return []
        if self._hidden_category_ids:
            try:
                return searcher(
                    self._query,
                    limit=M3U_SEARCH_RESULT_LIMIT,
                    hidden_category_ids=self._hidden_category_ids,
                )
            except TypeError:
                pass
        return filter_hidden_category_items(
            searcher(self._query, limit=M3U_SEARCH_RESULT_LIMIT),
            self._hidden_category_ids,
        )

    def empty_message(self):
        if self._query:
            return N_("No channels matched this search.")
        return N_("Enter a channel name.")

    def item_text(self, item):
        prefix = "★ " if self._is_favorite(item) else ""
        group = str(getattr(item, "group_title", "") or "")
        suffix = "  •  {}".format(group) if group else ""
        return "{}{}{}".format(prefix, item.name, suffix)

    def _refresh(self):
        GTAsyncListScreen._refresh(self)
        if self._entries and not self._error:
            if len(self._entries) >= M3U_SEARCH_RESULT_LIMIT:
                message = _("First {} matching channels").format(
                    M3U_SEARCH_RESULT_LIMIT
                )
            else:
                message = _("{} channels found").format(len(self._entries))
            self["message"].setText(message)

    def toggle_favorite(self):
        if not self._entries or self._loading:
            return
        item = self._entries[self.selected_index]
        added = self._toggle_favorite_item(item)
        self._refresh()
        ellipsize_dynamic_text(
            self["message"],
            self._favorite_message(item, added),
            fallback_chars=80,
        )

    def open_selected(self):
        if not self._entries or self._loading:
            return
        open_extplayer(
            self.session,
            self.client,
            self._entries[self.selected_index],
            self._entries,
            self.selected_index,
            owner=self,
        )

    def _stop_search(self):
        try:
            self._keyboard_timer.stop()
        except Exception:
            pass


class GTContentBrowserScreen(_FavoriteSupport, GTAsyncListScreen):
    def __init__(
        self,
        session,
        account,
        content_type,
        client=None,
        weather_settings_loader=None,
        weather_service=None,
        metadata_client=None,
        player_settings_loader=None,
        favorite_store=None,
        category_store=None,
        category_lock_store=None,
    ):
        self.account = account
        self.client = client or content_client_for(account)
        self.content_type = _validated_content_type(self.client, content_type)
        self._init_favorite_support(favorite_store)
        source_type = str(
            getattr(self.client, "source_type", "xtream") or "xtream"
        ).strip().lower()
        self._category_preferences_enabled = bool(
            self.content_type in ("live", "movie", "series")
            and source_type in ("xtream", "stalker", "m3u")
        )
        # Keep the historical attribute for external skins/tests which still
        # refer to the R68 Live TV implementation.
        self._live_category_preferences_enabled = (
            self._category_preferences_enabled
        )
        self.category_store = (
            category_store
            if category_store is not None
            else LiveCategoryPreferenceStore(
                category_preferences_path(self.content_type)
            )
        )
        self._hidden_category_ids = frozenset()
        self.category_lock_store = (
            category_lock_store
            if category_lock_store is not None
            else CategoryEditLockStore()
        )
        self._all_categories = []
        self._category_preferences_generation = -1
        self._live_all_categories = self._all_categories
        self._live_preferences_generation = (
            self._category_preferences_generation
        )
        self._category_manager_return_id = ""
        self._category_lock_prompt_open = False
        self._category_manager_child_open = False
        try:
            browser_settings = (
                player_settings_loader or load_player_settings
            )()
        except Exception:
            browser_settings = None
        if isinstance(browser_settings, dict):
            cinematic_view = browser_settings.get("cinematic_view", True)
            metadata_api_key = browser_settings.get("tmdb_api_key", "")
            metadata_enabled = browser_settings.get("metadata_enabled", True)
        else:
            cinematic_view = getattr(
                browser_settings,
                "cinematic_view",
                True,
            )
            metadata_api_key = getattr(
                browser_settings,
                "tmdb_api_key",
                "",
            )
            metadata_enabled = getattr(
                browser_settings,
                "metadata_enabled",
                True,
            )
        self._cinematic_enabled = bool(
            self.content_type in ("movie", "series") and cinematic_view
        )
        self._cinematic_generation = 0
        self._cinematic_pending = False
        self._cinematic_result = None
        self._cinematic_token = None
        self._cinematic_surface_prepared = None
        if metadata_client is not None:
            self._metadata_client = metadata_client
        else:
            self._metadata_client = TMDbMetadataClient(
                api_key=metadata_api_key,
                enabled=metadata_enabled,
                language=metadata_language(),
            )
        self._weather_settings_loader = (
            weather_settings_loader or load_weather_settings
        )
        self._weather_service = weather_service or WeatherService()
        self._weather_forecast = None
        self._weather_error = ""
        self._weather_ready = False
        self._weather_loading = False
        self._weather_generation = 0
        self._weather_token = None
        self._weather_started = False
        self._weather_suspended = False
        self._weather_timer = eTimer()
        _connect_timer(self._weather_timer, self._poll_weather)
        if self.content_type == "live":
            self._footer_layout_items = (
                M3U_LIVE_CATEGORY_FOOTER_ITEMS
                if _client_supports(self.client, "search")
                and _is_m3u_client(self.client)
                else LIVE_CATEGORY_FOOTER_ITEMS
            )
            self.skin = _live_category_skin()
            self.page_size = 8
            width, height, px = _scale()
            self._list_scroll_geometry = (
                px(64 + 990 - 18), px(252), px(8), px(626)
            )
        elif self.content_type == "movie":
            self._footer_layout_items = MOVIE_FOOTER_ITEMS
            self.skin = _movie_browser_skin(self._cinematic_enabled)
            self.page_size = (
                CINEMATIC_CATEGORY_PAGE_SIZE
                if self._cinematic_enabled
                else MOVIE_CATEGORY_PAGE_SIZE
            )
            width, height, px = _scale()
            if self._cinematic_enabled:
                (
                    self._list_scroll_geometry,
                    self._poster_scroll_geometry,
                ) = _cinematic_scroll_geometries(
                    width,
                    height,
                    px,
                )
            else:
                self._list_scroll_geometry = (
                    px(42 + 340 - 14),
                    px(118 + 48),
                    px(8),
                    (MOVIE_CATEGORY_PAGE_SIZE * px(72)) - px(8),
                )
                rail_x = px(430)
                self._poster_scroll_geometry = (
                    rail_x,
                    height - px(82) - px(25),
                    width - rail_x - px(42),
                    px(6),
                )
            self._movie_catalog_entries = []
            self._movie_entries = []
            self._movie_selected_index = 0
            self._movie_focus = "categories"
            self._movie_loading = False
            self._movie_ready = False
            self._movie_error = ""
            self._movie_generation = 0
            self._movie_catalog_generation = 0
            self._movie_loaded_category_id = None
            self._movie_category_load_started = False
            self._movie_category_list_generation = -1
            self._movie_detail_result = None
            self._movie_poster_state = None
            self._movie_detail_pending = False
            self._movie_poster_pending = False
            self._movie_pending_category_id = None
            self._movie_pending_detail_generation = None
            self._movie_poster_page_key = None
            self._movie_has_more = False
            self._movie_more_loading = False
            self._movie_more_result = None
            self._movie_pending_advance = False
            self._movie_pending_selection_index = None
            self._movie_load_token = None
            self._movie_more_token = None
            self._movie_detail_token = None
            self._movie_poster_token = None
            self._movie_deferred_poster_jobs = None
            self._movie_info_cache = OrderedDict()
            self._movie_info_cache_lock = threading.Lock()
            self._movie_poster_generation = 0
            self._movie_search_child_open = False
            self._movie_search_child = None
            self._movie_search_drain_event = None
            self._movie_search_resume_categories = False
            self._movie_search_resume_category_id = None
            self._movie_search_resume_more = False
            self._movie_search_resume_detail = False
            self._movie_search_resume_posters = False
            self._movie_timer = eTimer()
            _connect_timer(self._movie_timer, self._poll_movie_result)
            self._movie_category_timer = eTimer()
            _connect_timer(
                self._movie_category_timer,
                self._commit_movie_category_load,
            )
            self._movie_detail_timer = eTimer()
            _connect_timer(
                self._movie_detail_timer,
                self._commit_movie_detail,
            )
            self._movie_search_resume_timer = eTimer()
            _connect_timer(
                self._movie_search_resume_timer,
                self._poll_movie_search_resume,
            )
        elif self.content_type == "series":
            self._footer_layout_items = SERIES_FOOTER_ITEMS
            self.skin = _series_browser_skin(self._cinematic_enabled)
            self.page_size = (
                CINEMATIC_CATEGORY_PAGE_SIZE
                if self._cinematic_enabled
                else SERIES_CATEGORY_PAGE_SIZE
            )
            width, height, px = _scale()
            if self._cinematic_enabled:
                (
                    self._list_scroll_geometry,
                    self._poster_scroll_geometry,
                ) = _cinematic_scroll_geometries(
                    width,
                    height,
                    px,
                )
            else:
                self._list_scroll_geometry = (
                    px(42 + 300 - 14),
                    px(180),
                    px(8),
                    px(696),
                )
                self._poster_scroll_geometry = (
                    px(366), px(936), px(972), px(8)
                )
            self._series_entries = []
            self._series_selected_index = 0
            self._series_focus = "categories"
            self._series_selection_by_category = {}
            self._series_loading = False
            self._series_ready = False
            self._series_error = ""
            self._series_generation = 0
            self._series_catalog_generation = 0
            self._series_loaded_category_id = None
            self._series_category_load_started = False
            self._series_detail_result = None
            self._series_poster_state = None
            self._series_detail_pending = False
            self._series_poster_pending = False
            self._series_pending_category_id = None
            self._series_pending_detail_generation = None
            self._series_poster_page_key = None
            self._series_has_more = False
            self._series_more_loading = False
            self._series_more_result = None
            self._series_pending_advance = False
            self._series_pending_selection_index = None
            self._series_pending_clamp_to_end = False
            self._series_load_token = None
            self._series_more_token = None
            self._series_detail_token = None
            self._series_poster_token = None
            self._series_deferred_poster_jobs = None
            self._series_info_cache = OrderedDict()
            self._series_info_cache_lock = threading.Lock()
            self._series_poster_generation = 0
            self._series_info = None
            self._series_info_stream_id = ""
            self._series_child_open = False
            self._series_search_query = ""
            self._series_timer = eTimer()
            _connect_timer(self._series_timer, self._poll_series_result)
            self._series_scan_timer = eTimer()
            _connect_timer(
                self._series_scan_timer,
                self._resume_series_scan,
            )
            self._series_category_timer = eTimer()
            _connect_timer(
                self._series_category_timer,
                self._commit_series_category_load,
            )
            self._series_detail_timer = eTimer()
            _connect_timer(
                self._series_detail_timer,
                self._commit_series_detail,
            )
        title = CONTENT_LABELS[self.content_type][0]
        GTAsyncListScreen.__init__(
            self,
            session,
            "GTContentBrowserScreen",
            title,
            _("{}  |  Select a category").format(account.display_name),
            frameless_footer=self.content_type in ("movie", "series"),
        )
        if self.content_type == "live":
            self["server"] = Label("")
            self._dynamic_server_widget = "server"
            self._dynamic_server_text = account.host or account.display_name
            ellipsize_dynamic_text(
                self["server"],
                self._dynamic_server_text,
                fallback_chars=55,
            )
            self["clock"] = Label(datetime.datetime.now().strftime("%H:%M"))
            self["left_panel"] = Label("")
            self["right_panel"] = Label("")
            for panel in ("left", "right"):
                for color in ("cyan", "magenta"):
                    self["{}_panel_accent_{}".format(panel, color)] = Label("")
            self["categories_caption"] = Label(_("LIVE TV CATEGORIES"))
            for index in range(8):
                self["category_row_{}".format(index)] = Label("")
                for edge in ("fill", "top", "bottom", "left", "right"):
                    self[
                        "category_focus_{}_{}".format(index, edge)
                    ] = Label("")
            self["weather_caption"] = Label(_("5-DAY WEATHER FORECAST"))
            self["weather_city"] = Label("")
            self["weather_today_card"] = Label("")
            self["weather_current_icon"] = Pixmap()
            self["weather_today_label"] = Label(_("TODAY"))
            self["weather_temp"] = Label("--°")
            self["weather_desc"] = Label(_("Loading weather data"))
            self["weather_metrics"] = Label("")
            self["weather_updated"] = Label("")
            for index in range(5):
                self["weather_day_card_{}".format(index)] = Label("")
                self["weather_day_{}".format(index)] = Label("--")
                self["weather_day_icon_{}".format(index)] = Pixmap()
                self["weather_day_high_{}".format(index)] = Label("--°")
                self["weather_day_low_{}".format(index)] = Label("--°")
            self["actions"] = ActionMap(
                [
                    "OkCancelActions",
                    "DirectionActions",
                    "ColorActions",
                    "MenuActions",
                ],
                {
                    "ok": self.open_selected,
                    "cancel": self.close,
                    "up": self.move_up,
                    "down": self.move_down,
                    "upRepeated": self.move_up,
                    "downRepeated": self.move_down,
                    "left": self.page_up,
                    "right": self.page_down,
                    "leftRepeated": self.page_up,
                    "rightRepeated": self.page_down,
                    "green": self.refresh_all,
                    "yellow": self.open_category_manager,
                    "blue": self.open_live_category_action,
                    "menu": self.open_weather_settings,
                    "red": self.close,
                },
                -1,
            )
            self._render_weather_waiting()
            if hasattr(self, "onShown"):
                self.onShown.append(self.start_weather)
            if hasattr(self, "onClose"):
                self.onClose.append(self._stop_weather)
        elif self.content_type == "movie":
            self._setup_movie_widgets()
        elif self.content_type == "series":
            self._setup_series_widgets()
        if hasattr(self, "onShown"):
            self.onShown.append(self._favorite_browser_shown)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refit_content_dynamic_layout)

    def _refit_content_dynamic_layout(self):
        widget_name = getattr(self, "_dynamic_server_widget", "")
        if widget_name:
            ellipsize_dynamic_text(
                self[widget_name],
                getattr(self, "_dynamic_server_text", ""),
                fallback_chars=(55 if self.content_type == "live" else 80),
            )
        if self.content_type == "live" and hasattr(
            self, "_dynamic_weather_city_text"
        ):
            ellipsize_dynamic_text(
                self["weather_city"],
                self._dynamic_weather_city_text,
                fallback_chars=20,
            )

    def _favorite_browser_shown(self):
        if not self._favorite_screen_shown():
            return
        if self.content_type == "movie" and self._movie_entries:
            self._render_movie_posters()
        elif self.content_type == "series" and self._series_entries:
            self._render_series_tiles()

    def _selected_favorite_item(self):
        if self.content_type == "movie":
            if self._movie_entries and not self._movie_loading:
                return self._movie_entries[self._movie_selected_index]
            return None
        if self.content_type == "series":
            if self._series_entries and not self._series_loading:
                return self._series_entries[self._series_selected_index]
            return None
        return None

    def toggle_selected_favorite(self):
        item = self._selected_favorite_item()
        if item is None:
            self["message"].setText(_("Select content before using favorites."))
            return
        added = self._toggle_favorite_item(item)
        if self.content_type == "movie":
            self._render_movie_posters()
        elif self.content_type == "series":
            self._render_series_tiles()
        ellipsize_dynamic_text(
            self["message"],
            self._favorite_message(item, added),
            fallback_chars=24,
        )

    def load_items(self):
        # Categories must render as soon as their own lightweight API call
        # returns. Downloading every live stream just to calculate badges made
        # providers with large playlists feel needlessly slow.
        return self.client.load_categories(self.content_type)

    def _prepare_loaded_items(self, values):
        """Load and arrange category preferences away from the GUI thread."""
        raw_categories = list(values or [])
        if not self._category_preferences_enabled:
            return {
                "raw": raw_categories,
                "entries": raw_categories,
                "hidden": frozenset(),
            }
        try:
            preferences = self.category_store.load(self.favorite_scope)
            entries = arrange_categories(raw_categories, preferences)
            hidden = category_visibility_ids(raw_categories, preferences)
        except Exception:
            entries = raw_categories
            hidden = frozenset()
        return {
            "raw": raw_categories,
            "entries": entries,
            "hidden": hidden,
        }

    def _commit_loaded_items(self, prepared, generation):
        if not isinstance(prepared, dict):
            prepared = {}
        raw_categories = list(prepared.get("raw", ()))
        self._all_categories = raw_categories
        self._live_all_categories = raw_categories
        self._entries = list(prepared.get("entries", ()))
        self._hidden_category_ids = prepared.get("hidden", frozenset())
        self._category_preferences_generation = generation
        self._live_preferences_generation = generation

    def _poll_result(self):
        if (
            self._category_preferences_enabled
            and self._ready
            and not self._error
            and self._category_preferences_generation
            != self._load_generation
        ):
            raw_categories = list(self._entries)
            self._all_categories = raw_categories
            self._live_all_categories = raw_categories
            try:
                preferences = self.category_store.load(self.favorite_scope)
                self._entries = arrange_categories(
                    raw_categories,
                    preferences,
                )
                self._hidden_category_ids = category_visibility_ids(
                    raw_categories,
                    preferences,
                )
            except Exception:
                self._entries = raw_categories
                self._hidden_category_ids = frozenset()
            self._category_preferences_generation = self._load_generation
            self._live_preferences_generation = self._load_generation
        GTAsyncListScreen._poll_result(self)

    def start_load(self):
        if (
            self._category_preferences_enabled
            and not self._loading
            and not self._closed
        ):
            self._all_categories = []
            self._live_all_categories = self._all_categories
        GTAsyncListScreen.start_load(self)

    def _client_has_more_items(self, content_type, category_id):
        checker = getattr(self.client, "has_more_items", None)
        if not callable(checker):
            return False
        try:
            return bool(checker(content_type, category_id))
        except Exception:
            return False

    def empty_message(self):
        return N_("No category found.")

    def _refresh(self):
        if self.content_type == "movie":
            self._refresh_movie_categories()
            return
        if self.content_type == "series":
            self._refresh_series_categories()
            return
        if self.content_type != "live":
            GTAsyncListScreen._refresh(self)
            return
        self._refresh_live_categories()

    def _refresh_live_categories(self):
        if self._error:
            self["items"].setText(_("Could not open the content list."))
            ellipsize_dynamic_text(
                self["message"],
                _(self._error),
                fallback_chars=70,
            )
            self._clear_live_rows()
            hide_scrollbar(self, "list_scroll")
            return
        if not self._entries:
            self["items"].setText(_(self.empty_message()))
            self["message"].setText(_("No categories found; press GREEN to try again."))
            self._clear_live_rows()
            hide_scrollbar(self, "list_scroll")
            return
        self.selected_index %= len(self._entries)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start : page_start + self.page_size]
        plain_lines = []
        for row_index in range(self.page_size):
            if row_index < len(page_items):
                item = page_items[row_index]
                absolute_index = page_start + row_index
                rendered_row = ellipsize_dynamic_text(
                    self["category_row_{}".format(row_index)],
                    "{:02d}.  {}".format(absolute_index + 1, item.name),
                    fallback_chars=48,
                )
                plain_lines.append(rendered_row)
                selected = absolute_index == self.selected_index
            else:
                self["category_row_{}".format(row_index)].setText("")
                selected = False
            for edge in ("fill", "top", "bottom", "left", "right"):
                focus = self[
                    "category_focus_{}_{}".format(row_index, edge)
                ]
                if selected:
                    focus.show()
                else:
                    focus.hide()
        # Some third-party skins still render the shared text widget.
        self["items"].setText("\n".join(plain_lines))
        self["message"].setText(
            "{}: {} | {}/{}".format(
                _("Categories"),
                len(self._entries),
                self.selected_index + 1,
                len(self._entries),
            )
        )
        update_scrollbar(
            self, "list_scroll", len(self._entries), self.selected_index,
            self.page_size, self._list_scroll_geometry,
        )

    def _clear_live_rows(self):
        for row_index in range(8):
            self["category_row_{}".format(row_index)].setText("")
            for edge in ("fill", "top", "bottom", "left", "right"):
                self[
                    "category_focus_{}_{}".format(row_index, edge)
                ].hide()

    def refresh_all(self):
        invalidator = getattr(self.client, "invalidate_cache", None)
        if invalidator is not None:
            try:
                invalidator(self.content_type)
            except Exception:
                pass
        self.start_load()
        self.start_weather(force=True)

    def open_live_category_action(self):
        if _is_m3u_client(self.client):
            self.open_channel_search()
            return
        self.open_category_manager()

    def open_category_manager(self):
        if (
            not self._category_preferences_enabled
            or self._category_manager_child_open
            or self._category_lock_prompt_open
            or self._loading
            or self._error
            or not self._all_categories
            or not self.favorite_scope
            or (
                self.content_type == "movie"
                and self._movie_search_child_open
            )
            or (
                self.content_type == "series"
                and self._series_child_open
            )
            or not any(
                str(getattr(entry, "category_id", "") or "").strip()
                for entry in self._all_categories
            )
        ):
            return
        if self._entries:
            self._category_manager_return_id = str(
                getattr(
                    self._entries[self.selected_index],
                    "category_id",
                    "",
                )
                or ""
            ).strip()
        else:
            self._category_manager_return_id = ""
        try:
            lock_status = self.category_lock_store.status()
        except Exception:
            lock_status = "corrupt"
        if lock_status not in ("disabled", "enabled"):
            message = _(
                "Category edit lock data is damaged. Remove and "
                "reinstall the plugin to reset only this lock."
            )
            self["message"].setText(message)
            self.session.open(
                MessageBox,
                message,
                type=MessageBox.TYPE_INFO,
            )
            return
        if lock_status == "enabled":
            self._open_category_lock_code(
                self._category_manager_pin_entered,
                _("Category editor locked"),
                _("Enter the 4-digit category edit PIN."),
                allow_recovery=True,
            )
            return
        self._open_category_manager_unlocked()

    def _open_category_lock_code(
        self,
        callback,
        title,
        prompt,
        digits=4,
        allow_recovery=False,
    ):
        if self._category_lock_prompt_open:
            return False
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["message"].setText(
                _("The PIN entry screen is unavailable on this image.")
            )
            return False
        self._category_lock_prompt_open = True

        def completed(value):
            self._category_lock_prompt_open = False
            callback(value)

        try:
            opener(
                completed,
                GTCategoryCodeInputScreen,
                title=title,
                prompt=prompt,
                digits=digits,
                allow_recovery=allow_recovery,
            )
        except Exception:
            self._category_lock_prompt_open = False
            self["message"].setText(
                _("The PIN entry screen is unavailable on this image.")
            )
            return False
        return True

    def _category_manager_pin_entered(self, value):
        if value is None:
            return
        if value == RECOVERY_REQUEST:
            self._open_category_lock_code(
                self._category_manager_recovery_entered,
                _("Category lock recovery"),
                _("Enter the 12-digit recovery code."),
                digits=12,
            )
            return
        try:
            valid = self.category_lock_store.verify_pin(value)
        except Exception:
            valid = False
        if not valid:
            message = _("Incorrect PIN. Category editor remains locked.")
            self["message"].setText(message)
            self.session.open(
                MessageBox,
                message,
                type=MessageBox.TYPE_ERROR,
            )
            return
        self._open_category_manager_unlocked()

    def _category_manager_recovery_entered(self, value):
        if value is None:
            return
        try:
            valid = self.category_lock_store.verify_recovery(value)
        except Exception:
            valid = False
        if not valid:
            message = _(
                "Incorrect recovery code. Category editor remains locked."
            )
            self["message"].setText(message)
            self.session.open(
                MessageBox,
                message,
                type=MessageBox.TYPE_ERROR,
            )
            return
        self._open_category_lock_code(
            self._category_recovery_pin_created,
            _("Create a new category edit PIN"),
            _("Enter a new 4-digit PIN."),
        )

    def _category_recovery_pin_created(self, pin):
        if pin is None:
            return
        self._pending_recovery_pin = str(pin)
        opened = self._open_category_lock_code(
            self._category_recovery_pin_confirmed,
            _("Confirm the new category edit PIN"),
            _("Enter the same 4-digit PIN again."),
        )
        if not opened:
            self._pending_recovery_pin = ""

    def _category_recovery_pin_confirmed(self, confirmation):
        pin = getattr(self, "_pending_recovery_pin", "")
        self._pending_recovery_pin = ""
        if confirmation is None:
            return
        if str(confirmation) != pin:
            message = _("The PIN entries do not match. Nothing was changed.")
            self["message"].setText(message)
            self.session.open(
                MessageBox,
                message,
                type=MessageBox.TYPE_ERROR,
            )
            return
        try:
            recovery_code = self.category_lock_store.configure(
                pin,
                confirmation,
            )
        except Exception:
            recovery_code = ""
        if not recovery_code:
            message = _(
                "The new PIN could not be saved. Category editor "
                "remains locked."
            )
            self["message"].setText(message)
            self.session.open(
                MessageBox,
                message,
                type=MessageBox.TYPE_ERROR,
            )
            return
        message = _(
            "Recovery succeeded. Write down the new recovery code; "
            "it is shown only once:\n\n{}"
        ).format(recovery_code)
        self["message"].setText(_("Category edit PIN renewed."))
        opener = getattr(self.session, "openWithCallback", None)
        if callable(opener):
            opener(
                self._category_recovery_notice_closed,
                MessageBox,
                message,
                type=MessageBox.TYPE_INFO,
            )
            return
        self.session.open(MessageBox, message, type=MessageBox.TYPE_INFO)

    def _category_recovery_notice_closed(self, *unused):
        self._open_category_manager_unlocked()

    def _open_category_manager_unlocked(self):
        if self._category_manager_child_open:
            return
        if self.content_type == "live":
            self._stop_weather(suspend=True)
        arguments = (
            GTLiveCategoryManagerScreen,
            self.account.display_name,
            self.favorite_scope,
            list(self._all_categories),
            self.category_store,
            self._category_manager_return_id,
        )
        self._category_manager_child_open = True
        opener = getattr(self.session, "openWithCallback", None)
        if callable(opener):
            try:
                opener(
                    self._category_manager_closed,
                    *arguments,
                    content_type=self.content_type
                )
            except Exception:
                self._category_manager_child_open = False
                if self.content_type == "live":
                    self._weather_child_closed()
                self["message"].setText(
                    _("Could not open the content list.")
                )
            return
        try:
            dialog = self.session.open(
                *arguments,
                content_type=self.content_type
            )
        except Exception:
            self._category_manager_child_open = False
            if self.content_type == "live":
                self._weather_child_closed()
            self["message"].setText(_("Could not open the content list."))
            return
        callbacks = getattr(dialog, "onClose", None)
        if isinstance(callbacks, list):
            callbacks.append(self._category_manager_closed)

    def _category_manager_closed(
        self,
        changed=False,
        ordered_ids=None,
        hidden_ids=None,
        include_terms=None,
        exclude_terms=None,
    ):
        self._category_manager_child_open = False
        if changed and self._all_categories and not self._error:
            preferences = CategoryPreferences(
                ordered_ids or (),
                hidden_ids or (),
                include_terms or (),
                exclude_terms or (),
            )
            previous_visibility = self._hidden_category_ids
            previous_rules = getattr(
                previous_visibility, "rule_key", ((), ())
            )
            self._hidden_category_ids = category_visibility_ids(
                self._all_categories,
                preferences,
            )
            filters_changed = previous_rules != getattr(
                self._hidden_category_ids, "rule_key", ((), ())
            )
            if category_filter_active(self._hidden_category_ids):
                # Manual hiding also changes shared items in a visible
                # category, even when the filter terms stay the same.
                filters_changed = filters_changed or (
                    previous_visibility != self._hidden_category_ids
                    or getattr(previous_visibility, "allowed", ())
                    != self._hidden_category_ids.allowed
                )
            self._entries = arrange_categories(
                self._all_categories,
                preferences,
            )
            self.selected_index = 0
            return_id_found = False
            for index, entry in enumerate(self._entries):
                if str(
                    getattr(entry, "category_id", "") or ""
                ).strip() == self._category_manager_return_id:
                    self.selected_index = index
                    return_id_found = True
                    break
            if (
                not return_id_found
                and self.content_type == "movie"
                and self._category_manager_return_id
                and len(self._entries) > 1
            ):
                self.selected_index = 1
            self._ready = True
            self._loading = False
            if self.content_type == "live":
                self._refresh_live_categories()
            elif self.content_type == "movie":
                self._movie_focus = "categories"
                category = self._entries[self.selected_index]
                category_id = str(
                    getattr(category, "category_id", "") or ""
                ).strip()
                if filters_changed or category_id != str(
                    self._movie_loaded_category_id or ""
                ).strip():
                    if category_id:
                        self._start_movie_load(category)
                    else:
                        self._render_all_movies_prompt()
                else:
                    self._refresh_movie_categories(schedule_load=False)
                    self._render_movie_posters()
            elif self.content_type == "series":
                self._series_focus = "categories"
                category = self._entries[self.selected_index]
                category_id = str(
                    getattr(category, "category_id", "") or ""
                ).strip()
                if (
                    filters_changed
                    or not category_id
                    or category_id != str(
                        self._series_loaded_category_id or ""
                    ).strip()
                ):
                    self._start_series_load(category)
                else:
                    self._refresh_series_categories()
                    self._render_series_tiles()
        if self.content_type == "live":
            self._weather_child_closed()

    def open_channel_search(self):
        if (
            self.content_type != "live"
            or not _is_m3u_client(self.client)
            or not _client_supports(self.client, "search")
        ):
            return
        if (
            self._loading
            or self._category_preferences_generation != self._load_generation
        ):
            self["message"].setText(_("Please wait"))
            return
        self._stop_weather(suspend=True)
        opener = getattr(self.session, "openWithCallback", None)
        if callable(opener):
            opener(
                self._m3u_search_closed,
                GTM3UChannelSearchScreen,
                self.account,
                self.client,
                self.favorite_store,
                self._hidden_category_ids,
            )
            return
        dialog = self.session.open(
            GTM3UChannelSearchScreen,
            self.account,
            self.client,
            self.favorite_store,
            self._hidden_category_ids,
        )
        callbacks = getattr(dialog, "onClose", None)
        if isinstance(callbacks, list):
            callbacks.append(self._m3u_search_closed)

    def _m3u_search_closed(self, *args):
        self._reload_favorite_keys()
        self._weather_child_closed(*args)

    def _setup_movie_widgets(self):
        unused_width, unused_height, px = _scale()
        del unused_width, unused_height
        self._movie_title_preferred_font = font_px(
            px,
            38,
            role="title",
        )
        self._movie_title_min_font = font_px(
            px,
            28 if self._cinematic_enabled else 38,
            role="title",
            value="standard",
        )
        self["movie_server"] = Label("")
        self._dynamic_server_widget = "movie_server"
        self._dynamic_server_text = (
            self.account.host or self.account.display_name
        )
        ellipsize_dynamic_text(
            self["movie_server"],
            self._dynamic_server_text,
            fallback_chars=80,
        )
        if self._cinematic_enabled:
            self["movie_video_guard"] = Label("")
            self["movie_hero_panel"] = Label("")
            self["movie_backdrop"] = Pixmap()
            self["movie_backdrop_scrim"] = Pixmap()
            self["movie_detail_shadow"] = Pixmap()
            self["movie_meta_divider"] = Label("")
            self["movie_people_shadow"] = Pixmap()
            self["movie_rail_shadow"] = Pixmap()
            self["movie_people_panel"] = Label("")
            self["movie_hero_panel"].hide()
            self["movie_backdrop"].hide()
            # Full-size shading belongs to real artwork.  The transparent
            # fallback exposes the neon background and keeps only bounded
            # shadows underneath the text itself.
            self["movie_backdrop_scrim"].hide()
            self["movie_detail_shadow"].show()
            self["movie_people_shadow"].show()
            self["movie_rail_shadow"].show()
        self["movie_category_panel"] = Label("")
        self["movie_category_accent"] = Label("")
        self["movie_category_caption"] = Label(_("MOVIE CATEGORIES"))
        for index in range(self.page_size):
            self["movie_category_{}".format(index)] = Label("")
            self["movie_category_focus_{}".format(index)] = Label("")
            self["movie_category_marker_{}".format(index)] = Label("")
        self["movie_poster_frame"] = Label("")
        self["movie_poster_placeholder"] = Label(_("GT\nMOVIE"))
        self["movie_poster"] = Pixmap()
        self["movie_detail_panel"] = Label("")
        self["movie_detail_accent"] = Label("")
        self["movie_title"] = Label(_("Selecting movie..."))
        self["movie_rating"] = Label("")
        self["movie_meta"] = Label("")
        self["movie_genre"] = Label("")
        self["movie_plot_caption"] = Label(_("MOVIE SUMMARY"))
        movie_plot_text = _(
            "The selected movie's details will be loaded from the server."
        )
        self["movie_plot"] = (
            GTSmoothScrollLabel(movie_plot_text)
            if self._cinematic_enabled
            else Label(movie_plot_text)
        )
        self["movie_people"] = Label("")
        self["movie_rail_caption"] = Label(_("MOVIES IN THE SELECTED CATEGORY"))
        self["poster_scroll_track"] = Label("")
        self["poster_scroll_thumb"] = Label("")
        hide_scrollbar(self, "poster_scroll")
        for index in range(5):
            self["movie_tile_bg_{}".format(index)] = Label("")
            self["movie_tile_focus_{}".format(index)] = Label("")
            self["movie_tile_inner_{}".format(index)] = Label("")
            self["movie_tile_placeholder_{}".format(index)] = Label("GT")
            self["movie_tile_poster_{}".format(index)] = Pixmap()
            self["movie_tile_favorite_{}".format(index)] = Pixmap()
            self["movie_tile_title_{}".format(index)] = Label("")
        # OpenATV and OpenPLi both publish the physical CHANNEL keys through
        # MoviePlayerActions on the make event.  Do not also bind their
        # Navigation/ChannelSelect aliases: some images publish those on the
        # release event and one press would otherwise page twice.
        self["actions"] = ActionMap(
            [
                "OkCancelActions",
                "DirectionActions",
                "ColorActions",
                "MoviePlayerActions",
            ],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "channelUp": self.channel_page_up,
                "channelDown": self.channel_page_down,
                "green": self.refresh_movies,
                "yellow": self.open_category_manager,
                "blue": self.open_movie_search,
                "red": self.toggle_selected_favorite,
            },
            -1,
        )
        self._clear_movie_posters()
        self["movie_poster"].hide()
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop_movie)

    def _set_movie_title(self, value):
        return fit_dynamic_text(
            self["movie_title"],
            value,
            max_lines=2,
            preferred_size=self._movie_title_preferred_font,
            min_size=self._movie_title_min_font,
            fallback_chars=42,
        )

    def _set_static_movie_title(self, value):
        return set_static_text(
            self["movie_title"],
            value,
            preferred_size=self._movie_title_preferred_font,
        )

    def _set_movie_plot(self, value):
        if getattr(self, "_cinematic_enabled", False):
            rendered = clean_dynamic_text(value)
            self["movie_plot"].setText(rendered)
            return rendered
        return fit_dynamic_text(
            self["movie_plot"],
            value,
            max_lines=4,
            fallback_chars=70,
        )

    def _refresh_movie_categories(self, schedule_load=True):
        try:
            set_visible = _set_component_visible
        except NameError:
            def set_visible(component, visible):
                if visible:
                    component.show()
                else:
                    component.hide()
        try:
            update_text = set_text_if_changed
        except NameError:
            def update_text(component, value):
                text = "" if value is None else str(value)
                current = getattr(component, "message", None)
                if current is None or str(current) != text:
                    component.setText(text)
        if self._error:
            ellipsize_dynamic_text(
                self["message"],
                _(self._error),
                fallback_chars=24,
            )
            self._clear_movie_categories()
            hide_scrollbar(self, "list_scroll")
            return
        if not self._entries:
            update_text(self["message"], _("Movie category not found"))
            self._clear_movie_categories()
            hide_scrollbar(self, "list_scroll")
            return
        self.selected_index %= len(self._entries)
        if self._movie_category_list_generation != self._load_generation:
            self._movie_category_list_generation = self._load_generation
            pending_category_id = str(
                self._movie_pending_category_id or ""
            )
            pending_index = (
                next(
                    (
                        index
                        for index, entry in enumerate(self._entries)
                        if str(getattr(entry, "category_id", "") or "")
                        == pending_category_id
                    ),
                    None,
                )
                if pending_category_id
                else None
            )
            if pending_index is not None:
                self.selected_index = pending_index
            elif not str(
                getattr(self._entries[self.selected_index], "category_id", "")
                or ""
            ):
                for index, entry in enumerate(self._entries):
                    if str(getattr(entry, "category_id", "") or "").strip():
                        self.selected_index = index
                        break
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start : page_start + self.page_size]
        for row in range(self.page_size):
            name = ""
            selected = False
            if row < len(page_items):
                absolute = page_start + row
                name = page_items[row].name
                selected = (
                    self._movie_focus == "categories"
                    and absolute == self.selected_index
                )
            ellipsize_dynamic_text(
                self["movie_category_{}".format(row)],
                name,
                fallback_chars=20,
            )
            for widget_name in (
                "movie_category_focus_{}".format(row),
                "movie_category_marker_{}".format(row),
            ):
                set_visible(self[widget_name], selected)
        category = self._entries[self.selected_index]
        update_text(
            self["message"],
            "{}: {} • {}/{}".format(
                _("Categories"),
                len(self._entries),
                self.selected_index + 1,
                len(self._entries),
            ),
        )
        update_scrollbar(
            self, "list_scroll", len(self._entries), self.selected_index,
            self.page_size, self._list_scroll_geometry,
        )
        if not str(getattr(category, "category_id", "") or "").strip():
            if schedule_load:
                self._render_all_movies_prompt()
            return
        if (
            schedule_load
            and self._movie_loaded_category_id != category.category_id
        ):
            self._schedule_movie_load(
                category,
                immediate=not self._movie_category_load_started,
            )

    def _clear_movie_categories(self):
        for row in range(self.page_size):
            self["movie_category_{}".format(row)].setText("")
            self["movie_category_focus_{}".format(row)].hide()
            self["movie_category_marker_{}".format(row)].hide()

    def open_movie_search(self):
        if self.content_type != "movie" or self._closed:
            return
        if (
            self._loading
            or self._category_preferences_generation != self._load_generation
        ):
            self["message"].setText(_("Please wait"))
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["message"].setText(_("Could not open the content list."))
            return
        if self._movie_search_child_open:
            self["message"].setText(_("Please wait"))
            return
        if self._movie_search_drain_event is not None:
            self["message"].setText(_("Please wait"))
            return
        try:
            search_client = content_client_for(self.account)
        except Exception as error:
            log_event(
                "movies",
                "Could not create movie search client",
                error=error,
            )
            self["message"].setText(_("Could not open the content list."))
            return
        self._pause_movie_work_for_search()
        self._movie_search_child_open = True
        self._movie_search_child = None
        try:
            self._movie_search_child = opener(
                self._movie_search_closed,
                GTAllMoviesSearchScreen,
                self.account,
                search_client,
                self.favorite_store,
                self._hidden_category_ids,
            )
        except Exception:
            self._movie_search_closed()
            source_type = str(
                getattr(search_client, "source_type", "") or ""
            ).lower()
            if (
                source_type == "xtream"
                or isinstance(search_client, XtreamContentClient)
            ):
                releaser = getattr(
                    search_client,
                    "release_movie_search",
                    None,
                )
                if callable(releaser):
                    try:
                        releaser()
                    except Exception:
                        pass
            if not self._closed:
                self["message"].setText(_("Could not open the content list."))

    def _pause_movie_work_for_search(self):
        reset_cinematic = getattr(
            self,
            "_reset_cinematic_backdrop",
            None,
        )
        if callable(reset_cinematic):
            reset_cinematic()
        category_active = bool(self._loading or self._load_token is not None)
        movie_active = bool(
            self._movie_loading or self._movie_load_token is not None
        )
        more_active = bool(
            self._movie_more_loading or self._movie_more_token is not None
        )
        detail_active = bool(
            self._movie_detail_pending
            or self._movie_pending_detail_generation is not None
            or self._movie_detail_token is not None
        )
        poster_active = bool(
            self._movie_poster_pending
            or self._movie_poster_state is not None
            or self._movie_poster_token is not None
            or self._movie_deferred_poster_jobs is not None
        )
        pending_category_id = str(
            self._movie_pending_category_id or ""
        )
        loaded_category_id = str(
            self._movie_loaded_category_id or ""
        )

        self._movie_search_resume_categories = category_active
        self._movie_search_resume_category_id = (
            pending_category_id
            or (loaded_category_id if movie_active else "")
            or None
        )
        self._movie_search_resume_more = more_active
        self._movie_search_resume_detail = detail_active
        self._movie_search_resume_posters = poster_active

        if category_active:
            self._load_generation += 1
            _cancel_worker_token(self._load_token)
            self._load_token = None
            self._loading = False
        if movie_active or more_active or detail_active or poster_active:
            self._movie_generation += 1
        if poster_active:
            self._movie_poster_generation = getattr(
                self, "_movie_poster_generation", self._movie_generation
            ) + 1
        if movie_active or more_active:
            self._movie_catalog_generation += 1
        for token in (
            self._movie_load_token,
            self._movie_more_token,
            self._movie_detail_token,
            self._movie_poster_token,
        ):
            _cancel_worker_token(token)
        poster_state = self._movie_poster_state
        if isinstance(poster_state, dict):
            _cancel_worker_token(poster_state.get("token"))
        self._movie_load_token = None
        self._movie_more_token = None
        self._movie_detail_token = None
        self._movie_poster_token = None
        self._movie_loading = False
        self._movie_more_loading = False
        self._movie_more_result = None
        self._movie_detail_pending = False
        self._movie_detail_result = None
        self._movie_poster_pending = False
        self._movie_poster_state = None
        self._movie_deferred_poster_jobs = None
        self._movie_pending_category_id = None
        self._movie_pending_detail_generation = None
        for timer in (
            self._timer,
            self._movie_timer,
            self._movie_category_timer,
            self._movie_detail_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass

    def _resume_category_list_after_search(self):
        if self._closed or self._loading:
            return
        self._load_generation += 1
        generation = self._load_generation
        token = _WorkerToken(WORKER_LIST_TIMEOUT_SECONDS)
        self._load_token = token
        self._loading = True
        self._ready = False
        self._error = ""
        worker = threading.Thread(
            target=self._run_load,
            args=(generation, token),
        )
        worker.daemon = True
        worker.start()
        self._timer.start(150, True)

    def _resume_movie_category_after_search(self, category_id):
        category_id = str(category_id or "")
        if self._closed or not category_id or self._movie_loading:
            return
        self._movie_generation += 1
        self._movie_catalog_generation += 1
        generation = self._movie_generation
        token = _WorkerToken(MOVIE_CATEGORY_TIMEOUT_SECONDS)
        self._movie_load_token = token
        self._movie_loading = True
        self._movie_ready = False
        self._movie_error = ""
        self._movie_loaded_category_id = category_id
        worker = threading.Thread(
            target=self._load_movie_worker,
            args=(generation, category_id, token),
        )
        worker.daemon = True
        worker.start()
        self._movie_timer.start(120, True)

    def _resume_movie_work_after_search(self):
        resume_categories = self._movie_search_resume_categories
        category_id = self._movie_search_resume_category_id
        resume_more = self._movie_search_resume_more
        resume_detail = self._movie_search_resume_detail
        resume_posters = self._movie_search_resume_posters
        self._movie_search_resume_categories = False
        self._movie_search_resume_category_id = None
        self._movie_search_resume_more = False
        self._movie_search_resume_detail = False
        self._movie_search_resume_posters = False

        if resume_categories:
            self._resume_category_list_after_search()
        if category_id:
            self._resume_movie_category_after_search(category_id)
        elif resume_more and self._movie_has_more:
            self._start_movie_more_load()
        if not category_id and resume_detail and self._movie_entries:
            self._schedule_movie_detail()
        if not category_id and resume_posters and self._movie_entries:
            self._movie_poster_page_key = None
            self._render_movie_posters()

    def _poll_movie_search_resume(self):
        if self._closed:
            self._movie_search_drain_event = None
            return
        if self._movie_search_child_open:
            self._movie_search_resume_timer.start(100, True)
            return
        drain_event = self._movie_search_drain_event
        if drain_event is not None and not drain_event.is_set():
            self._movie_search_resume_timer.start(100, True)
            return
        self._movie_search_drain_event = None
        self._resume_movie_work_after_search()

    def _movie_search_closed(self, *args):
        if self._closed or not self._movie_search_child_open:
            return
        child = self._movie_search_child
        self._movie_search_child = None
        self._movie_search_child_open = False
        self._reload_favorite_keys()
        self._refresh_movie_categories(schedule_load=False)
        self._render_movie_posters()
        drain_event = getattr(child, "_provider_workers_idle", None)
        if drain_event is not None and not drain_event.is_set():
            self._movie_search_drain_event = drain_event
            self._movie_search_resume_timer.start(100, True)
            return
        self._movie_search_drain_event = None
        self._resume_movie_work_after_search()

    def _render_all_movies_prompt(self):
        active = bool(
            self._movie_loading
            or self._movie_entries
            or self._movie_loaded_category_id is not None
            or self._movie_load_token is not None
        )
        if active:
            for token in (
                self._movie_load_token,
                self._movie_more_token,
                self._movie_detail_token,
                self._movie_poster_token,
            ):
                _cancel_worker_token(token)
            self._movie_generation += 1
            self._movie_catalog_generation += 1
            self._movie_poster_generation = getattr(
                self, "_movie_poster_generation", self._movie_generation
            ) + 1
        self._movie_load_token = None
        self._movie_more_token = None
        self._movie_detail_token = None
        self._movie_poster_token = None
        self._movie_loading = False
        self._movie_ready = False
        self._movie_more_loading = False
        self._movie_more_result = None
        self._movie_has_more = False
        self._movie_pending_advance = False
        self._movie_pending_selection_index = None
        self._movie_detail_pending = False
        self._movie_detail_result = None
        self._movie_pending_category_id = None
        self._movie_pending_detail_generation = None
        self._movie_poster_pending = False
        self._movie_poster_state = None
        self._movie_deferred_poster_jobs = None
        self._movie_catalog_entries = []
        self._movie_entries = []
        self._movie_loaded_category_id = None
        self._movie_selected_index = 0
        self._movie_focus = "categories"
        self["movie_rail_caption"].setText(localized_upper(_("All Movies")))
        self._set_static_movie_title(_("Search All Movies"))
        self["movie_rating"].setText("")
        self["movie_meta"].setText("")
        self["movie_genre"].setText("")
        self._set_movie_plot(_("Press BLUE to search all movies."))
        self["movie_people"].setText("")
        self["movie_poster"].hide()
        self["movie_poster_placeholder"].show()
        self._clear_movie_posters()
        for timer in (
            self._movie_timer,
            self._movie_category_timer,
            self._movie_detail_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass

    def _schedule_movie_load(self, category=None, immediate=False):
        if not self._entries or self._closed:
            return
        category = category or self._entries[self.selected_index]
        if not str(getattr(category, "category_id", "") or "").strip():
            self._render_all_movies_prompt()
            return
        self._movie_pending_category_id = category.category_id
        try:
            self._movie_category_timer.stop()
        except Exception:
            pass
        if immediate:
            self._commit_movie_category_load()
        else:
            self._movie_category_timer.start(NAVIGATION_DEBOUNCE_MS, True)

    def _commit_movie_category_load(self):
        if not self._entries or self._closed:
            return
        category = self._entries[self.selected_index]
        if not str(getattr(category, "category_id", "") or "").strip():
            self._movie_pending_category_id = None
            self._render_all_movies_prompt()
            return
        if category.category_id != self._movie_pending_category_id:
            return
        self._movie_pending_category_id = None
        self._start_movie_load(category)

    def _start_movie_load(self, category=None):
        if not self._entries or self._closed:
            return
        category = category or self._entries[self.selected_index]
        if not str(getattr(category, "category_id", "") or "").strip():
            self._render_all_movies_prompt()
            return
        for timer in (
            self._movie_category_timer,
            self._movie_detail_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass
        self._movie_pending_category_id = None
        self._movie_pending_detail_generation = None
        for token in (
            self._movie_load_token,
            self._movie_more_token,
            self._movie_detail_token,
            self._movie_poster_token,
        ):
            _cancel_worker_token(token)
        self._movie_category_load_started = True
        self._movie_generation += 1
        self._movie_catalog_generation += 1
        self._movie_poster_generation = getattr(
            self, "_movie_poster_generation", self._movie_generation
        ) + 1
        generation = self._movie_generation
        self._movie_loading = True
        self._movie_ready = False
        self._movie_error = ""
        self._movie_catalog_entries = []
        self._movie_entries = []
        self._movie_selected_index = 0
        self._movie_loaded_category_id = category.category_id
        self._movie_detail_result = None
        self._movie_detail_pending = False
        self._movie_poster_state = None
        self._movie_poster_pending = False
        self._movie_poster_page_key = None
        self._movie_has_more = False
        self._movie_more_loading = False
        self._movie_more_result = None
        self._movie_pending_advance = False
        self._movie_pending_selection_index = None
        self._movie_more_token = None
        self._movie_detail_token = None
        self._movie_poster_token = None
        self._movie_deferred_poster_jobs = None
        ellipsize_dynamic_text(
            self["movie_rail_caption"],
            _("{} • LOADING MOVIES").format(localized_upper(category.name)),
            fallback_chars=90,
        )
        self._clear_movie_posters()
        self._render_movie_waiting()
        token = _WorkerToken(MOVIE_CATEGORY_TIMEOUT_SECONDS)
        self._movie_load_token = token
        worker = threading.Thread(
            target=self._load_movie_worker,
            args=(generation, category.category_id, token),
        )
        worker.daemon = True
        worker.start()
        self._movie_timer.start(120, True)

    def _load_movie_worker(self, generation, category_id, token=None):
        try:
            entries = list(
                _bounded_client_call(
                    self.client,
                    token,
                    lambda: self.client.load_items("movie", category_id),
                )
                or []
            )
            if category_filter_active(getattr(self, "_hidden_category_ids", ())):
                entries = filter_hidden_category_items(
                    entries, self._hidden_category_ids, category_id
                )
            error = ""
        except _WorkerCancelled:
            return
        except ContentError as caught:
            entries = []
            error = str(caught)
        except Exception:
            entries = []
            error = N_("Could not load movies")
        if (
            self._closed
            or generation != self._movie_generation
            or (token is not None and not token.active())
        ):
            return
        self._movie_catalog_entries = entries
        self._movie_entries = list(entries)
        self._movie_error = error
        self._movie_has_more = self._client_has_more_items(
            "movie", category_id
        )
        self._movie_ready = True

    def _start_movie_more_load(self):
        loader = getattr(self.client, "load_more_items", None)
        if (
            not callable(loader)
            or not self._movie_has_more
            or self._movie_more_loading
            or self._closed
        ):
            return False
        self._movie_more_loading = True
        generation = self._movie_catalog_generation
        category_id = str(self._movie_loaded_category_id or "")
        token = _WorkerToken(MOVIE_CATEGORY_TIMEOUT_SECONDS)
        _cancel_worker_token(self._movie_more_token)
        self._movie_more_token = token
        worker = threading.Thread(
            target=self._load_movie_more_worker,
            args=(generation, category_id, loader, token),
        )
        worker.daemon = True
        worker.start()
        self._movie_timer.start(80, True)
        return True

    def _load_movie_more_worker(
        self,
        generation,
        category_id,
        loader,
        token=None,
    ):
        try:
            entries = list(
                _bounded_client_call(
                    self.client,
                    token,
                    lambda: loader("movie", category_id),
                )
                or []
            )
            if category_filter_active(getattr(self, "_hidden_category_ids", ())):
                entries = filter_hidden_category_items(
                    entries, self._hidden_category_ids, category_id
                )
            error = ""
        except _WorkerCancelled:
            return
        except ContentError as caught:
            entries = []
            error = str(caught)
        except Exception:
            entries = []
            error = N_("Could not load movies")
        has_more = self._client_has_more_items("movie", category_id)
        if (
            self._closed
            or generation != self._movie_catalog_generation
            or (token is not None and not token.active())
        ):
            return
        self._movie_more_result = (
            generation,
            category_id,
            entries,
            has_more,
            error,
        )

    def _maybe_load_more_movies(self):
        if not self._movie_entries or not self._movie_has_more:
            return False
        remaining = len(self._movie_entries) - self._movie_selected_index - 1
        if remaining > 5 and not self._movie_pending_advance:
            return False
        return self._start_movie_more_load()

    def _update_movie_rail_caption(self):
        if not self._entries:
            return
        category = self._entries[self.selected_index]
        ellipsize_dynamic_text(
            self["movie_rail_caption"],
            _("{} • {} MOVIES").format(
                localized_upper(category.name), len(self._movie_entries)
            ),
            fallback_chars=90,
        )

    def _apply_movie_more_result(self):
        result = self._movie_more_result
        if result is None:
            return
        self._movie_more_result = None
        generation, category_id, entries, has_more, error = result
        if (
            generation != self._movie_catalog_generation
            or category_id != str(self._movie_loaded_category_id or "")
        ):
            return
        self._movie_more_loading = False
        self._movie_more_token = None
        self._movie_has_more = bool(has_more)
        if error:
            self._movie_has_more = False
            self._movie_pending_advance = False
            self._movie_pending_selection_index = None
            return
        old_length = len(self._movie_entries)
        known = {
            str(item.stream_id) for item in self._movie_catalog_entries
        }
        for item in entries:
            stream_id = str(getattr(item, "stream_id", ""))
            if not stream_id or stream_id in known:
                continue
            known.add(stream_id)
            self._movie_catalog_entries.append(item)
        self._movie_entries = list(self._movie_catalog_entries)
        self._update_movie_rail_caption()
        if self._movie_pending_advance:
            pending_index = getattr(
                self,
                "_movie_pending_selection_index",
                None,
            )
            if pending_index is None and len(self._movie_entries) > old_length:
                pending_index = old_length
            if (
                pending_index is not None
                and pending_index < len(self._movie_entries)
            ):
                self._movie_pending_advance = False
                self._movie_pending_selection_index = None
                self._select_movie(pending_index)
                return
            if self._movie_has_more:
                self._start_movie_more_load()
                return
            self._movie_pending_advance = False
            self._movie_pending_selection_index = None
            if self._movie_entries:
                last_index = len(self._movie_entries) - 1
                if last_index != self._movie_selected_index:
                    self._select_movie(last_index)
                    return
        self._movie_pending_advance = False
        self._movie_pending_selection_index = None
        self._render_movie_posters()

    def _poll_movie_result(self):
        if self._closed:
            return
        if self._movie_loading:
            if not self._movie_ready:
                if (
                    self._movie_load_token is not None
                    and self._movie_load_token.expired()
                ):
                    _cancel_worker_token(self._movie_load_token)
                    self._movie_loading = False
                    self._movie_error = N_("Movie request timed out")
                    self._render_movie_results()
                    return
                self._movie_timer.start(120, True)
                return
            self._movie_loading = False
            self._movie_load_token = None
            self._render_movie_results()
            return
        if (
            self._movie_more_loading
            and self._movie_more_token is not None
            and self._movie_more_token.expired()
        ):
            _cancel_worker_token(self._movie_more_token)
            self._movie_more_loading = False
            self._movie_more_token = None
            self._movie_pending_advance = False
            self._movie_pending_selection_index = None
        self._apply_movie_more_result()
        detail = self._movie_detail_result
        if detail is not None:
            self._movie_detail_result = None
            self._movie_detail_pending = False
            self._movie_detail_token = None
            (
                generation,
                info,
                poster_path,
                metadata_status,
                poster_url,
                catalogue_poster_url,
            ) = detail
            if generation == self._movie_generation:
                self._apply_movie_detail(
                    info,
                    poster_path,
                    metadata_status,
                    poster_url,
                    catalogue_poster_url,
                )
            self._start_deferred_movie_posters()
        elif (
            self._movie_detail_pending
            and self._movie_detail_token is not None
            and self._movie_detail_token.expired()
        ):
            _cancel_worker_token(self._movie_detail_token)
            self._movie_detail_token = None
            self._movie_detail_pending = False
            self._start_deferred_movie_posters()
        state = self._movie_poster_state
        if state is not None:
            state_token = state.get("token")
            state_expired = (
                state_token is not None and state_token.expired()
            )
            if state_expired:
                _cancel_worker_token(state_token)
            with state["lock"]:
                results = list(state["results"])
                del state["results"][:]
                complete = (
                    state_expired or state["pending_workers"] <= 0
                )
            generation = state["generation"]
            page_is_current = (
                state.get("page_key") == self._movie_poster_page_key
            )
            if page_is_current:
                for (
                    slot,
                    stream_id,
                    path,
                    requested_url,
                    resolved_url,
                    fallback_deferred,
                ) in results:
                    try:
                        page_start = int(state["page_key"][0])
                        page_item = self._movie_entries[page_start + slot]
                        result_is_current = (
                            generation
                            == getattr(
                                self,
                                "_movie_poster_generation",
                                self._movie_generation,
                            )
                            and str(page_item.stream_id) == str(stream_id)
                        )
                    except Exception:
                        page_item = None
                        result_is_current = False
                    if not result_is_current:
                        continue
                    current_url = _safe_picon_url(
                        getattr(page_item, "icon", "")
                    )
                    requested_url = _safe_picon_url(requested_url)
                    resolved_url = _safe_picon_url(resolved_url)
                    if path and current_url not in (
                        "",
                        requested_url,
                        resolved_url,
                    ):
                        continue
                    if path and resolved_url and current_url in (
                        "",
                        requested_url,
                    ):
                        page_item.icon = resolved_url
                    if fallback_deferred and not path:
                        retry_url = _safe_picon_url(
                            getattr(page_item, "icon", "")
                        )
                        self._movie_deferred_poster_jobs = (
                            self._movie_poster_page_key,
                            [(slot, page_item, retry_url)],
                            True,
                        )
                    self._apply_movie_tile_poster(
                        slot,
                        path,
                        stream_id=stream_id,
                        generation=generation,
                    )
                    try:
                        selected_item = self._movie_entries[
                            self._movie_selected_index
                        ]
                    except Exception:
                        selected_item = None
                    if (
                        selected_item is not None
                        and str(selected_item.stream_id) == str(stream_id)
                    ):
                        self._apply_detail_poster(
                            "movie",
                            selected_item,
                            path,
                        )
            if complete or not page_is_current:
                if self._movie_poster_state is state:
                    self._movie_poster_state = None
                    self._movie_poster_pending = False
                    self._movie_poster_token = None
                if page_is_current:
                    self._start_deferred_movie_posters()
        poll_cinematic = getattr(self, "_poll_cinematic_backdrop", None)
        cinematic_pending = bool(
            poll_cinematic() if callable(poll_cinematic) else False
        )
        if (
            self._movie_detail_pending
            or self._movie_poster_pending
            or self._movie_more_loading
            or cinematic_pending
        ):
            self._movie_timer.start(80, True)

    def _apply_detail_poster(self, kind, item, path=""):
        if kind == "movie":
            entries = self._movie_entries
            generation = self._movie_generation
            poster_name = "movie_poster"
            placeholder_name = "movie_poster_placeholder"
            attach_key = "movie-detail"
        elif kind == "series":
            entries = self._series_entries
            generation = self._series_generation
            poster_name = "series_poster"
            placeholder_name = "series_poster_placeholder"
            attach_key = "series-detail"
        else:
            return False
        if item is None or not entries:
            return False
        if not path:
            url = _safe_picon_url(getattr(item, "icon", ""))
            path = _cached_picon_path(url) if url else ""
        if not path:
            return False
        stream_id = str(getattr(item, "stream_id", "") or "")

        def loaded(success):
            try:
                if kind == "movie":
                    current_generation = self._movie_generation
                    current_entries = self._movie_entries
                    current_index = self._movie_selected_index
                else:
                    current_generation = self._series_generation
                    current_entries = self._series_entries
                    current_index = self._series_selected_index
                current = current_entries[current_index]
                valid = (
                    generation == current_generation
                    and str(getattr(current, "stream_id", "")) == stream_id
                )
                if not valid:
                    return
                if success:
                    self[poster_name].show()
                    self[placeholder_name].hide()
                else:
                    self[poster_name].hide()
                    self[placeholder_name].show()
            except Exception:
                pass

        attach_pixmap(
            self,
            poster_name,
            path,
            key=attach_key,
            on_loaded=loaded,
        )
        return True

    @staticmethod
    def _cinematic_widget_names(kind):
        if kind == "movie":
            return "movie_backdrop", "movie_backdrop_scrim"
        if kind == "series":
            return "series_backdrop", "series_backdrop_scrim"
        return "", ""

    @staticmethod
    def _cinematic_video_guard_name(kind):
        if kind == "movie":
            return "movie_video_guard"
        if kind == "series":
            return "series_video_guard"
        return ""

    @staticmethod
    def _cinematic_panel_names(kind):
        if kind == "movie":
            return ("movie_hero_panel",)
        if kind == "series":
            return ("series_hero_panel",)
        return ()

    @staticmethod
    def _cinematic_fallback_shadow_names(kind):
        if kind == "movie":
            return (
                "movie_detail_shadow",
                "movie_people_shadow",
                "movie_rail_shadow",
            )
        if kind == "series":
            return (
                "series_detail_shadow",
                "series_people_shadow",
                "series_rail_shadow",
            )
        return ()

    def _set_cinematic_panels(self, kind, artwork_visible):
        """Keep only bounded text shadows over hero or transparent fallback."""
        try:
            set_visible = _set_component_visible
        except NameError:
            def set_visible(component, visible):
                if visible:
                    component.show()
                else:
                    component.hide()
        guard_name = self._cinematic_video_guard_name(kind)
        if guard_name:
            try:
                set_visible(self[guard_name], True)
            except Exception:
                pass
        for name in self._cinematic_panel_names(kind):
            try:
                set_visible(self[name], False)
            except Exception:
                pass
        unused_backdrop_name, hero_scrim_name = (
            self._cinematic_widget_names(kind)
        )
        if hero_scrim_name:
            try:
                # R116 never shades the complete artwork rectangle.  The
                # bounded masks below protect copy without dulling faces or
                # turning a missing-hero state into a dark box.
                set_visible(self[hero_scrim_name], False)
            except Exception:
                pass
        for name in self._cinematic_fallback_shadow_names(kind):
            try:
                set_visible(self[name], True)
            except Exception:
                pass

    def _reset_cinematic_backdrop(self):
        """Cancel stale hero work and remove artwork while focus is moving."""
        if not getattr(self, "_cinematic_enabled", False):
            return
        self._cinematic_generation = getattr(
            self,
            "_cinematic_generation",
            0,
        ) + 1
        _cancel_worker_token(getattr(self, "_cinematic_token", None))
        self._cinematic_token = None
        self._cinematic_pending = False
        self._cinematic_result = None
        self._cinematic_surface_prepared = None
        loaders = getattr(self, "_gt_pixmap_loaders", {})
        loader = loaders.pop("cinematic-backdrop", None)
        if loader is not None:
            try:
                loader.close()
            except Exception:
                pass
        backdrop_name, unused_scrim_name = self._cinematic_widget_names(
            getattr(self, "content_type", "")
        )
        if backdrop_name:
            try:
                try:
                    set_visible = _set_component_visible
                except NameError:
                    set_visible = lambda component, visible: (
                        component.show() if visible else component.hide()
                    )
                set_visible(self[backdrop_name], False)
            except Exception:
                pass
        set_panels = getattr(self, "_set_cinematic_panels", None)
        if callable(set_panels):
            set_panels(
                getattr(self, "content_type", ""),
                False,
            )

    def _start_cinematic_backdrop(
        self,
        kind,
        item,
        info=None,
        catalogue_poster_url="",
    ):
        """Load a settled selection's best verified cinematic hero image.

        Every candidate must be an explicit wide-art field and must decode as
        genuine landscape artwork. Posters remain available only in the card
        rail and are never enlarged into a cinematic hero.
        """
        if not getattr(self, "_cinematic_enabled", False):
            return False
        stream_id = str(getattr(item, "stream_id", "") or "")
        detail_generation = getattr(
            self,
            "_movie_generation" if kind == "movie" else "_series_generation",
            None,
        )
        prepared = (kind, stream_id, detail_generation)
        # Selection waiting already cancelled and hid the previous hero.  A
        # settled detail result reuses that generation instead of invalidating
        # the complete transparent surface for a second time.  Direct callers
        # without a matching preparation marker retain the safe reset.
        if getattr(self, "_cinematic_surface_prepared", None) == prepared:
            self._cinematic_surface_prepared = None
        else:
            self._reset_cinematic_backdrop()
        candidates = []
        seen = set()
        provider_item = bool(
            str(getattr(item, "portal_request_type", "") or "").strip()
        )
        verified_backdrop = getattr(info, "fallback_backdrop", "")
        if provider_item and verified_backdrop:
            # A Stalker/MAC portal can expose stale or shared catalogue art.
            # Prefer the TMDb candidate already verified against title/year,
            # then retain explicit provider-wide artwork as a fallback.
            ordered_values = (
                verified_backdrop,
                getattr(info, "backdrop", ""),
                getattr(item, "backdrop", ""),
            )
        else:
            ordered_values = (
                getattr(info, "backdrop", ""),
                verified_backdrop,
                getattr(item, "backdrop", ""),
            )
        for value in ordered_values:
            url = _safe_picon_url(value)
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append(url)
        if not candidates:
            return False
        generation = self._cinematic_generation
        first_url = candidates[0]
        cached = _cached_picon_path(first_url)
        if cached:
            if _valid_cinematic_backdrop_file(cached):
                self._apply_cinematic_backdrop(
                    kind,
                    stream_id,
                    cached,
                    generation,
                )
                return True
        token = _WorkerToken(CINEMATIC_BACKDROP_TIMEOUT_SECONDS)
        self._cinematic_token = token
        self._cinematic_pending = True
        worker = threading.Thread(
            target=self._load_cinematic_backdrop_worker,
            args=(
                kind,
                stream_id,
                tuple(candidates),
                generation,
                token,
            ),
        )
        worker.daemon = True
        worker.start()
        timer = getattr(
            self,
            "_movie_timer" if kind == "movie" else "_series_timer",
            None,
        )
        if timer is not None:
            try:
                timer.start(80, True)
            except Exception:
                pass
        return True

    def _load_cinematic_backdrop_worker(
        self,
        kind,
        stream_id,
        candidates,
        generation,
        token,
    ):
        path = ""
        for url in candidates:
            try:
                token.check()
                candidate = _download_client_image(
                    self.client,
                    url,
                    _picon_cache_path(url),
                    timeout=CINEMATIC_CANDIDATE_TIMEOUT_SECONDS,
                    token=token,
                )
                token.check()
                if candidate and _valid_cinematic_backdrop_file(candidate):
                    path = candidate
                    break
            except _WorkerCancelled:
                return
            except Exception as error:
                _debug(
                    "cinematic hero candidate failed kind={} id={} error={}".format(
                        kind,
                        _debug_identifier(stream_id),
                        error.__class__.__name__,
                    )
                )
        if (
            token.active()
            and generation == getattr(self, "_cinematic_generation", -1)
        ):
            self._cinematic_result = (
                kind,
                stream_id,
                path,
                generation,
            )

    def _apply_cinematic_backdrop(
        self,
        kind,
        stream_id,
        path,
        generation,
    ):
        if not path or generation != self._cinematic_generation:
            return False
        backdrop_name, unused_scrim_name = self._cinematic_widget_names(kind)
        if not backdrop_name:
            return False

        try:
            set_visible = _set_component_visible
        except NameError:
            def set_visible(component, visible):
                if visible:
                    component.show()
                else:
                    component.hide()

        def loaded(success):
            try:
                if kind == "movie":
                    current = self._movie_entries[
                        self._movie_selected_index
                    ]
                else:
                    current = self._series_entries[
                        self._series_selected_index
                    ]
                valid = (
                    getattr(self, "_cinematic_enabled", False)
                    and generation == self._cinematic_generation
                    and str(getattr(current, "stream_id", "")) == stream_id
                )
                # An obsolete decoder callback must never clear a newer
                # selection's already visible hero or fallback state.
                if not valid:
                    return
                if success:
                    set_visible(self[backdrop_name], True)
                    set_panels = getattr(
                        self,
                        "_set_cinematic_panels",
                        None,
                    )
                    if callable(set_panels):
                        set_panels(kind, True)
                else:
                    set_visible(self[backdrop_name], False)
                    set_panels = getattr(
                        self,
                        "_set_cinematic_panels",
                        None,
                    )
                    if callable(set_panels):
                        set_panels(kind, False)
            except Exception:
                pass

        attach_pixmap(
            self,
            backdrop_name,
            path,
            key="cinematic-backdrop",
            on_loaded=loaded,
            cover_ratio=_cinematic_cover_ratio(path),
            cover_alignment="top_right",
        )
        return True

    def _poll_cinematic_backdrop(self):
        if not getattr(self, "_cinematic_enabled", False):
            return False
        result = getattr(self, "_cinematic_result", None)
        if result is not None:
            self._cinematic_result = None
            self._cinematic_pending = False
            self._cinematic_token = None
            kind, stream_id, path, generation = result
            if generation == self._cinematic_generation and path:
                self._apply_cinematic_backdrop(
                    kind,
                    stream_id,
                    path,
                    generation,
                )
            return False
        token = getattr(self, "_cinematic_token", None)
        if token is not None and token.expired():
            _cancel_worker_token(token)
            self._cinematic_token = None
            self._cinematic_pending = False
            return False
        return bool(getattr(self, "_cinematic_pending", False))

    def _render_movie_waiting(self, item=None):
        try:
            set_visible = _set_component_visible
        except NameError:
            def set_visible(component, visible):
                if visible:
                    component.show()
                else:
                    component.hide()
        reset_cinematic = getattr(
            self,
            "_reset_cinematic_backdrop",
            None,
        )
        if callable(reset_cinematic):
            reset_cinematic()
        if item is not None and getattr(self, "_cinematic_enabled", False):
            self._cinematic_surface_prepared = (
                "movie",
                str(getattr(item, "stream_id", "") or ""),
                getattr(self, "_movie_generation", None),
            )
        if item is None:
            self._set_static_movie_title(_("Selecting movie..."))
            set_text_if_changed(self["movie_rating"], "")
            set_text_if_changed(self["movie_meta"], "")
        else:
            self._set_movie_title(item.name)
            set_text_if_changed(
                self["movie_rating"],
                _rating_label(item.rating),
            )
            ellipsize_dynamic_text(
                self["movie_meta"],
                _year_not_in_title(item.name, item.year),
                fallback_chars=34,
            )
        set_text_if_changed(self["movie_genre"], "")
        self._set_movie_plot(_("Loading movie information from the server."))
        set_text_if_changed(self["movie_people"], "")
        set_visible(self["movie_poster"], False)
        set_visible(self["movie_poster_placeholder"], True)
        self._apply_detail_poster("movie", item)

    def _render_movie_results(self):
        if self._movie_error:
            self["movie_rail_caption"].setText(_("COULD NOT LOAD MOVIES"))
            self._set_movie_plot(_(self._movie_error))
            self._clear_movie_posters()
            return
        if not self._movie_entries:
            self["movie_rail_caption"].setText(_("NO MOVIES FOUND IN THIS CATEGORY"))
            self._set_movie_plot(_("Try another category."))
            self._clear_movie_posters()
            return
        self._movie_selected_index %= len(self._movie_entries)
        self._update_movie_rail_caption()
        self._schedule_movie_detail()
        self._render_movie_posters()
        self._maybe_load_more_movies()

    def _render_movie_posters(self):
        if self._movie_search_child_open:
            return
        try:
            set_visible = _set_component_visible
        except NameError:
            def set_visible(component, visible):
                if visible:
                    component.show()
                else:
                    component.hide()
        try:
            update_text = set_text_if_changed
        except NameError:
            def update_text(component, value):
                text = "" if value is None else str(value)
                current = getattr(component, "message", None)
                if current is None or str(current) != text:
                    component.setText(text)
        page_start = int(self._movie_selected_index / 5) * 5
        page = self._movie_entries[page_start : page_start + 5]
        page_key = (
            page_start,
            tuple(
                (
                    str(getattr(item, "stream_id", "")),
                    _safe_picon_url(getattr(item, "icon", "")),
                )
                for item in page
            ),
        )
        page_changed = page_key != self._movie_poster_page_key
        if page_changed:
            self._movie_poster_generation = getattr(
                self, "_movie_poster_generation", self._movie_generation
            ) + 1
            _cancel_worker_token(self._movie_poster_token)
            self._movie_poster_token = None
            self._movie_poster_pending = False
            self._movie_poster_state = None
            self._movie_deferred_poster_jobs = None
            self._movie_poster_page_key = page_key
        jobs = []
        for slot in range(5):
            selected = False
            if slot < len(page):
                absolute = page_start + slot
                item = page[slot]
                selected = (
                    self._movie_focus == "posters"
                    and absolute == self._movie_selected_index
                )
                fit_dynamic_text(
                    self["movie_tile_title_{}".format(slot)],
                    item.name,
                    max_lines=2,
                    fallback_chars=24,
                )
                set_visible(
                    self["movie_tile_favorite_{}".format(slot)],
                    self._is_favorite(item),
                )
                if page_changed:
                    set_visible(
                        self["movie_tile_placeholder_{}".format(slot)],
                        True,
                    )
                    url = _safe_picon_url(getattr(item, "icon", ""))
                    if url:
                        cached = _cached_picon_path(url)
                        if cached:
                            self._apply_movie_tile_poster(
                                slot,
                                cached,
                                stream_id=item.stream_id,
                                generation=getattr(
                                    self,
                                    "_movie_poster_generation",
                                    self._movie_generation,
                                ),
                            )
                        else:
                            jobs.append((slot, item, url))
                    elif absolute == self._movie_selected_index:
                        jobs.append((slot, item, ""))
            else:
                update_text(self["movie_tile_title_{}".format(slot)], "")
                set_visible(
                    self["movie_tile_placeholder_{}".format(slot)],
                    False,
                )
                set_visible(
                    self["movie_tile_poster_{}".format(slot)],
                    False,
                )
                set_visible(
                    self["movie_tile_favorite_{}".format(slot)],
                    False,
                )
            set_visible(
                self["movie_tile_focus_{}".format(slot)],
                selected,
            )
        update_horizontal_scrollbar(
            self,
            "poster_scroll",
            len(self._movie_entries),
            self._movie_selected_index,
            5,
            self._poster_scroll_geometry,
        )
        if jobs:
            if self._movie_pending_detail_generation is not None:
                self._movie_deferred_poster_jobs = (page_key, jobs)
            else:
                self._start_movie_poster_jobs(page_key, jobs)

    def _start_movie_poster_jobs(self, page_key, jobs):
        if (
            self._closed
            or page_key != self._movie_poster_page_key
        ):
            return
        selected_slot = self._movie_selected_index - int(page_key[0])
        jobs = sorted(
            jobs,
            key=lambda job: 0 if int(job[0]) == selected_slot else 1,
        )
        if not jobs:
            self._movie_deferred_poster_jobs = None
            return
        generation = getattr(
            self, "_movie_poster_generation", self._movie_generation
        )
        token = _WorkerToken(WORKER_POSTER_TIMEOUT_SECONDS)
        _cancel_worker_token(self._movie_poster_token)
        self._movie_poster_token = token
        self._movie_poster_pending = True
        worker_count = min(BACKGROUND_POSTER_WORKERS, len(jobs))
        state = {
            "generation": generation,
            "page_key": page_key,
            "selected_slot": selected_slot,
            "results": [],
            "pending_workers": worker_count,
            "batch_workers": worker_count,
            "metadata_cache_batch": False,
            "metadata_cache_client": None,
            "lock": threading.Lock(),
            "token": token,
        }
        metadata_client = getattr(self, "_metadata_client", None)
        begin_batch = getattr(
            metadata_client, "begin_cache_batch", None
        )
        if callable(begin_batch):
            try:
                begin_batch()
                state["metadata_cache_batch"] = True
                state["metadata_cache_client"] = metadata_client
            except Exception as error:
                _debug(
                    "movie metadata cache batch failed error={}".format(
                        error.__class__.__name__
                    )
                )
        self._movie_poster_state = state
        self._movie_deferred_poster_jobs = None
        for worker_index in range(worker_count):
            worker = threading.Thread(
                target=self._load_movie_posters_worker,
                args=(
                    jobs[worker_index::worker_count],
                    state,
                    token,
                ),
            )
            worker.daemon = True
            worker.start()
        self._movie_timer.start(50, True)

    def _start_deferred_movie_posters(self):
        deferred = self._movie_deferred_poster_jobs
        if deferred is not None and self._movie_poster_state is None:
            wait_for_detail = len(deferred) > 2 and bool(deferred[2])
            if wait_for_detail and (
                self._movie_detail_pending
                or self._movie_pending_detail_generation is not None
            ):
                return
            self._start_movie_poster_jobs(deferred[0], deferred[1])

    def _load_movie_posters_worker(
        self,
        jobs,
        state,
        token=None,
    ):
        try:
            for slot, item, url in jobs:
                if token is not None:
                    token.check()
                path = ""
                resolved_url = url
                fallback_deferred = False
                is_selected = int(slot) == int(state.get("selected_slot", -1))
                if url:
                    try:
                        path = _download_client_image(
                            self.client,
                            url,
                            _picon_cache_path(url),
                            timeout=_provider_picon_timeout(
                                self._metadata_client,
                                url,
                            ),
                            token=token,
                        )
                    except _WorkerCancelled:
                        raise
                    except Exception as error:
                        _debug(
                            "movie poster failed id={} error={}".format(
                                _debug_identifier(item.stream_id),
                                error.__class__.__name__,
                            )
                        )
                if not path and is_selected:
                    fallback_deferred = bool(
                        self._movie_detail_pending
                        or self._movie_pending_detail_generation is not None
                    )
                    if not fallback_deferred:
                        if token is not None:
                            token.check()
                        try:
                            completed = _bounded_client_call(
                                self._metadata_client,
                                token,
                                lambda: self._metadata_client.complete_movie(
                                    item,
                                    _detail_cache_get(
                                        self._movie_info_cache,
                                        self._movie_info_cache_lock,
                                        item.stream_id,
                                    )
                                    if hasattr(self, "_movie_info_cache_lock")
                                    else self._movie_info_cache.get(item.stream_id),
                                    force_cover_fallback=True,
                                ),
                            )
                        except MetadataError as error:
                            _debug(
                                "TMDb movie poster fallback failed id={} error={}".format(
                                    _debug_identifier(item.stream_id),
                                    error.__class__.__name__,
                                )
                            )
                            completed = None
                        except _WorkerCancelled:
                            raise
                        except Exception as error:
                            _debug(
                                "movie poster fallback failed id={} error={}".format(
                                    _debug_identifier(item.stream_id),
                                    error.__class__.__name__,
                                )
                            )
                            completed = None
                        fallback_url = _safe_picon_url(
                            getattr(completed, "cover", "")
                        )
                        if fallback_url and fallback_url != url:
                            try:
                                path = _download_client_image(
                                    self.client,
                                    fallback_url,
                                    _picon_cache_path(fallback_url),
                                    token=token,
                                )
                            except _WorkerCancelled:
                                raise
                            except Exception as error:
                                _debug(
                                    "movie fallback image failed id={} error={}".format(
                                        _debug_identifier(item.stream_id),
                                        error.__class__.__name__,
                                    )
                                )
                            if path:
                                resolved_url = fallback_url
                with state["lock"]:
                    state["results"].append(
                        (
                            slot,
                            item.stream_id,
                            path,
                            url,
                            resolved_url if path else "",
                            fallback_deferred,
                        )
                    )
        finally:
            finish_batch = False
            with state["lock"]:
                state["pending_workers"] -= 1
                if "batch_workers" in state:
                    state["batch_workers"] -= 1
                    if (
                        state["batch_workers"] <= 0
                        and state.get("metadata_cache_batch")
                    ):
                        state["metadata_cache_batch"] = False
                        finish_batch = True
            if finish_batch:
                try:
                    state["metadata_cache_client"].end_cache_batch()
                except Exception as error:
                    _debug(
                        "movie metadata cache flush failed error={}".format(
                            error.__class__.__name__
                        )
                    )

    def _apply_movie_tile_poster(
        self,
        slot,
        path,
        stream_id="",
        generation=None,
    ):
        if not path:
            return
        generation = (
            getattr(
                self, "_movie_poster_generation", self._movie_generation
            )
            if generation is None
            else generation
        )

        def loaded(success):
            try:
                page_start = int(self._movie_selected_index / 5) * 5
                current = self._movie_entries[page_start + slot]
                valid = (
                    generation
                    == getattr(
                        self,
                        "_movie_poster_generation",
                        self._movie_generation,
                    )
                    and str(current.stream_id) == str(stream_id)
                )
                if success and valid:
                    self["movie_tile_poster_{}".format(slot)].show()
                    self["movie_tile_placeholder_{}".format(slot)].hide()
                else:
                    self["movie_tile_poster_{}".format(slot)].hide()
            except Exception:
                pass

        attach_pixmap(
            self,
            "movie_tile_poster_{}".format(slot),
            path,
            key="movie-tile-{}".format(slot),
            on_loaded=loaded,
        )

    def _start_movie_detail(self):
        if not self._movie_entries:
            return
        item = self._movie_entries[self._movie_selected_index]
        generation = self._movie_generation
        _cancel_worker_token(self._movie_detail_token)
        token = _WorkerToken(WORKER_DETAIL_TIMEOUT_SECONDS)
        self._movie_detail_token = token
        worker = threading.Thread(
            target=self._load_movie_detail_worker,
            args=(generation, item, token),
        )
        worker.daemon = True
        self._movie_detail_pending = True
        worker.start()
        self._start_deferred_movie_posters()
        self._movie_timer.start(80, True)

    def _schedule_movie_detail(self, immediate=False):
        if not self._movie_entries or self._closed:
            return
        self._movie_pending_detail_generation = self._movie_generation
        _cancel_worker_token(self._movie_detail_token)
        self._movie_detail_token = None
        try:
            self._movie_detail_timer.stop()
        except Exception:
            pass
        item = self._movie_entries[self._movie_selected_index]
        self._render_movie_waiting(item)
        if immediate:
            self._commit_movie_detail()
        else:
            self._movie_detail_timer.start(NAVIGATION_DEBOUNCE_MS, True)

    def _commit_movie_detail(self):
        if (
            self._closed
            or self._movie_pending_detail_generation != self._movie_generation
        ):
            return
        self._movie_pending_detail_generation = None
        self._start_movie_detail()

    def _load_movie_detail_worker(self, generation, item, token=None):
        if token is not None:
            token.check()
        catalogue_poster_url = _safe_picon_url(getattr(item, "icon", ""))
        info = (
            _detail_cache_get(
                self._movie_info_cache,
                self._movie_info_cache_lock,
                item.stream_id,
            )
            if hasattr(self, "_movie_info_cache_lock")
            else self._movie_info_cache.get(item.stream_id)
        )
        metadata_status = ""
        loader = getattr(self.client, "load_vod_info", None)
        if info is None and loader is not None:
            try:
                info = _bounded_client_call(
                    self.client,
                    token,
                    lambda: loader(item.stream_id),
                )
                if hasattr(self, "_movie_info_cache_lock"):
                    _detail_cache_put(
                        self._movie_info_cache,
                        self._movie_info_cache_lock,
                        item.stream_id,
                        info,
                    )
                else:
                    self._movie_info_cache[item.stream_id] = info
            except Exception as error:
                _debug(
                    "movie detail failed id={} error={}".format(
                        item.stream_id,
                        error.__class__.__name__,
                    )
                )
                info = None
        if token is not None:
            token.check()
        try:
            completer = None
            if self._cinematic_enabled:
                completer = getattr(
                    self._metadata_client,
                    "complete_movie_cinematic",
                    None,
                )
            if not callable(completer):
                completer = self._metadata_client.complete_movie
            info = _bounded_client_call(
                self._metadata_client,
                token,
                lambda: completer(item, info),
            )
            if hasattr(self, "_movie_info_cache_lock"):
                _detail_cache_put(
                    self._movie_info_cache,
                    self._movie_info_cache_lock,
                    item.stream_id,
                    info,
                )
            else:
                self._movie_info_cache[item.stream_id] = info
            metadata_status = str(
                getattr(self._metadata_client, "last_error", "") or ""
            )
        except MetadataError as error:
            metadata_status = str(error)
            _debug(
                "TMDb movie detail fallback failed id={} error={}".format(
                    item.stream_id,
                    error.__class__.__name__,
                )
            )
        poster_url = _safe_picon_url(
            getattr(info, "cover", "") or getattr(item, "icon", "")
        )
        # Poster I/O runs in the bounded poster pool, so content detail does
        # not wait for a slow or protected artwork endpoint.
        poster_path = _cached_picon_path(poster_url) if poster_url else ""
        if (
            generation == self._movie_generation
            and (token is None or token.active())
        ):
            self._movie_detail_result = (
                generation,
                info,
                poster_path,
                metadata_status,
                poster_url,
                catalogue_poster_url,
            )

    def _apply_movie_detail(
        self,
        info,
        poster_path,
        metadata_status="",
        poster_url="",
        catalogue_poster_url="",
    ):
        cinematic = bool(getattr(self, "_cinematic_enabled", False))

        # Some long-lived integrations reuse this method independently of the
        # browser module.  Keep a tiny fallback for those callers while the
        # normal screen uses typography.set_text_if_changed.
        try:
            update_text = set_text_if_changed
        except NameError:
            def update_text(component, value):
                text = "" if value is None else str(value)
                getter = getattr(component, "getText", None)
                current = (
                    getter()
                    if callable(getter)
                    else getattr(
                        component,
                        "message",
                        getattr(component, "text", None),
                    )
                )
                if current is None or str(current) != text:
                    component.setText(text)

        item = self._movie_entries[self._movie_selected_index]
        poster_url = _safe_picon_url(poster_url)
        catalogue_poster_url = _safe_picon_url(catalogue_poster_url)
        current_poster_url = _safe_picon_url(getattr(item, "icon", ""))
        poster_identity_changed = False
        if poster_url and (
            not current_poster_url
            or current_poster_url == catalogue_poster_url
        ):
            if current_poster_url != poster_url:
                item.icon = poster_url
                current_poster_url = poster_url
                poster_identity_changed = True
        if poster_path and poster_url and current_poster_url != poster_url:
            poster_path = ""
        metadata_status = str(metadata_status or "").strip()[:180]
        if info is None:
            self._set_movie_title(item.name)
            update_text(
                self["movie_rating"],
                _rating_label(item.rating),
            )
            ellipsize_dynamic_text(
                self["movie_meta"],
                _year_not_in_title(item.name, item.year)
                or _("Information unavailable."),
                fallback_chars=34,
            )
            self._set_movie_plot(_("Movie information unavailable."))
            update_text(self["movie_genre"], "")
            update_text(self["movie_people"], "")
        else:
            display_title = info.title or item.name
            self._set_movie_title(display_title)
            update_text(
                self["movie_rating"],
                _rating_label(info.rating),
            )
            meta = [
                value
                for value in (
                    _year_not_in_title(display_title, info.year),
                    info.duration,
                )
                if value
            ]
            ellipsize_dynamic_text(
                self["movie_meta"],
                ("  |  " if cinematic else "  •  ").join(meta)
                or _("Information unavailable."),
                fallback_chars=34,
            )
            ellipsize_dynamic_text(
                self["movie_genre"],
                info.genre or _("Genre unavailable"),
                fallback_chars=55,
            )
            self._set_movie_plot(
                info.plot or _("Movie summary unavailable.")
            )
            people = []
            if info.director:
                people.append(
                    _("Director: {}").format(info.director)
                )
            if info.cast:
                people.append(
                    _("Cast: {}").format(info.cast)
                )
            fit_dynamic_text(
                self["movie_people"],
                ("  |  " if cinematic else " • ").join(people),
                max_lines=2,
                fallback_chars=80,
            )
        start_cinematic = getattr(self, "_start_cinematic_backdrop", None)
        if getattr(self, "_cinematic_enabled", False) and callable(
            start_cinematic
        ):
            start_cinematic(
                "movie",
                item,
                info,
                catalogue_poster_url,
            )
        if self._apply_detail_poster("movie", item, poster_path):
            stream_id = item.stream_id
            slot = self._movie_selected_index % 5
            self._apply_movie_tile_poster(
                slot,
                poster_path,
                stream_id=stream_id,
                generation=getattr(
                    self,
                    "_movie_poster_generation",
                    self._movie_generation,
                ),
            )
        # Provider detail can supply a better cover than the catalogue row.
        # Re-key the shared poster batch without adding a catalogue request.
        if poster_identity_changed:
            self._render_movie_posters()

    def _clear_movie_posters(self):
        for slot in range(5):
            self["movie_tile_title_{}".format(slot)].setText("")
            self["movie_tile_poster_{}".format(slot)].hide()
            self["movie_tile_placeholder_{}".format(slot)].hide()
            self["movie_tile_favorite_{}".format(slot)].hide()
            self["movie_tile_focus_{}".format(slot)].hide()
        hide_scrollbar(self, "poster_scroll")

    def refresh_movies(self):
        if self.content_type != "movie":
            self.start_load()
            return
        if self._movie_search_child_open:
            self["message"].setText(_("Please wait"))
            return
        for current in self._movie_entries:
            url = _safe_picon_url(getattr(current, "icon", ""))
            if url:
                _clear_picon_failure(url)
        invalidator = getattr(self.client, "invalidate_cache", None)
        if invalidator is not None:
            try:
                category_id = (
                    self._entries[self.selected_index].category_id
                    if self._entries
                    else None
                )
                invalidator("movie", category_id)
            except Exception:
                pass
        self._movie_poster_page_key = None
        if self._movie_focus == "categories":
            self.start_load()
        else:
            self._start_movie_load()

    def _select_movie_category(self, index):
        if not self._entries or self._loading:
            return
        self._movie_focus = "categories"
        self._movie_pending_advance = False
        self._movie_pending_selection_index = None
        selected_index = index % len(self._entries)
        self.selected_index = selected_index
        self._movie_loaded_category_id = None
        self._refresh_movie_categories()
        self._render_movie_posters()

    def _select_movie(self, index):
        if not self._movie_entries or self._movie_loading:
            return
        focus_changed = self._movie_focus != "posters"
        self._movie_focus = "posters"
        index = int(index)
        if index >= len(self._movie_entries) and self._movie_has_more:
            self._movie_pending_advance = True
            self._movie_pending_selection_index = index
            self._start_movie_more_load()
            return
        self._movie_pending_advance = False
        self._movie_pending_selection_index = None
        self._movie_selected_index = index % len(self._movie_entries)
        _cancel_worker_token(self._movie_detail_token)
        self._movie_detail_token = None
        self._movie_generation += 1
        self._schedule_movie_detail()
        self._render_movie_posters()
        if focus_changed:
            self._refresh_movie_categories()
        self._maybe_load_more_movies()

    def move_up(self):
        if self.content_type == "series":
            self._series_move_up()
            return
        if self.content_type != "movie":
            GTAsyncListScreen.move_up(self)
            return
        if self._movie_focus == "posters":
            self._movie_focus = "categories"
            self._render_movie_posters()
            self._refresh_movie_categories()
        else:
            self._select_movie_category(self.selected_index - 1)

    def move_down(self):
        if self.content_type == "series":
            self._series_move_down()
            return
        if self.content_type != "movie":
            GTAsyncListScreen.move_down(self)
            return
        if self._movie_focus == "posters":
            return
        self._select_movie_category(self.selected_index + 1)

    def page_up(self):
        if self.content_type == "series":
            self._series_move_left()
            return
        if self.content_type != "movie":
            GTAsyncListScreen.page_up(self)
            return
        if self._movie_focus == "posters":
            if self._movie_selected_index == 0:
                self._movie_focus = "categories"
                self._render_movie_posters()
                self._refresh_movie_categories()
            else:
                self._select_movie(self._movie_selected_index - 1)
        else:
            self._select_movie_category(self.selected_index - self.page_size)

    def page_down(self):
        if self.content_type == "series":
            self._series_move_right()
            return
        if self.content_type != "movie":
            GTAsyncListScreen.page_down(self)
            return
        if not self._movie_entries:
            return
        if self._movie_focus == "categories":
            self._movie_focus = "posters"
            self._schedule_movie_detail()
            self._render_movie_posters()
            self._refresh_movie_categories()
        else:
            self._select_movie(self._movie_selected_index + 1)

    def channel_page_up(self):
        """Move one visible page back without changing the active pane."""
        if self.content_type == "series":
            self._series_page(-1)
            return
        if self.content_type != "movie":
            return
        if self._movie_focus == "posters":
            if not self._movie_entries:
                return
            target = max(0, self._movie_selected_index - MOVIE_RAIL_PAGE_SIZE)
            if target != self._movie_selected_index:
                self._select_movie(target)
            return
        if not self._entries:
            return
        target = max(0, self.selected_index - self.page_size)
        if target != self.selected_index:
            self._select_movie_category(target)

    def channel_page_down(self):
        """Move one visible page forward without changing the active pane."""
        if self.content_type == "series":
            self._series_page(1)
            return
        if self.content_type != "movie":
            return
        if self._movie_focus == "posters":
            if not self._movie_entries:
                return
            target = self._movie_selected_index + MOVIE_RAIL_PAGE_SIZE
            if not self._movie_has_more:
                target = min(target, len(self._movie_entries) - 1)
            if target != self._movie_selected_index:
                self._select_movie(target)
            return
        if not self._entries:
            return
        target = min(
            self.selected_index + self.page_size,
            len(self._entries) - 1,
        )
        if target != self.selected_index:
            self._select_movie_category(target)

    def _stop_movie(self):
        reset_cinematic = getattr(
            self,
            "_reset_cinematic_backdrop",
            None,
        )
        if callable(reset_cinematic):
            reset_cinematic()
        try:
            plot_widget = self["movie_plot"]
        except (KeyError, TypeError):
            plot_widget = None
        stop_scroll = getattr(plot_widget, "stop_scroll", None)
        if callable(stop_scroll):
            stop_scroll()
        self._movie_generation += 1
        self._movie_catalog_generation += 1
        self._movie_poster_generation = getattr(
            self, "_movie_poster_generation", self._movie_generation
        ) + 1
        self._movie_search_child_open = False
        self._movie_search_child = None
        self._movie_search_drain_event = None
        self._movie_search_resume_categories = False
        self._movie_search_resume_category_id = None
        self._movie_search_resume_more = False
        self._movie_search_resume_detail = False
        self._movie_search_resume_posters = False
        self._movie_more_loading = False
        self._movie_more_result = None
        self._movie_pending_advance = False
        self._movie_pending_selection_index = None
        for token in (
            self._movie_load_token,
            self._movie_more_token,
            self._movie_detail_token,
            self._movie_poster_token,
        ):
            _cancel_worker_token(token)
        self._movie_load_token = None
        self._movie_more_token = None
        self._movie_detail_token = None
        self._movie_poster_token = None
        self._movie_poster_state = None
        self._movie_deferred_poster_jobs = None
        movie_cache_lock = getattr(self, "_movie_info_cache_lock", None)
        if movie_cache_lock is None:
            self._movie_info_cache.clear()
        else:
            with movie_cache_lock:
                self._movie_info_cache.clear()
        for timer in (
            self._movie_timer,
            self._movie_category_timer,
            self._movie_detail_timer,
            self._movie_search_resume_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass

    def _setup_series_widgets(self):
        unused_width, unused_height, px = _scale()
        del unused_width, unused_height
        self._series_title_preferred_font = font_px(
            px,
            38 if self._cinematic_enabled else 30,
            role="title",
        )
        self._series_title_min_font = font_px(
            px,
            28 if self._cinematic_enabled else 30,
            role="title",
            value="standard",
        )
        self["series_server"] = Label("")
        self._dynamic_server_widget = "series_server"
        self._dynamic_server_text = _("{}   |   CONTENT BROWSER").format(
            self.account.host or self.account.display_name
        )
        ellipsize_dynamic_text(
            self["series_server"],
            self._dynamic_server_text,
            fallback_chars=80,
        )
        if self._cinematic_enabled:
            self["series_video_guard"] = Label("")
            self["series_hero_panel"] = Label("")
            self["series_backdrop"] = Pixmap()
            self["series_backdrop_scrim"] = Pixmap()
            self["series_detail_shadow"] = Pixmap()
            self["series_meta_divider"] = Label("")
            self["series_people_shadow"] = Pixmap()
            self["series_rail_shadow"] = Pixmap()
            self["series_people_panel"] = Label("")
            self["series_hero_panel"].hide()
            self["series_backdrop"].hide()
            self["series_backdrop_scrim"].hide()
            self["series_detail_shadow"].show()
            self["series_people_shadow"].show()
            self["series_rail_shadow"].show()
        self["series_category_panel"] = Label("")
        self["series_category_accent"] = Label("")
        self["series_category_caption"] = Label(_("CATEGORIES"))
        for index in range(self.page_size):
            self["series_category_{}".format(index)] = Label("")
            self["series_category_focus_{}".format(index)] = Label("")
            self["series_category_marker_{}".format(index)] = Label("")
        self["series_grid_panel"] = Label("")
        self["series_grid_accent"] = Label("")
        self["series_poster_frame"] = Label("")
        self["series_poster_placeholder"] = Label(_("GT\nSERIES"))
        self["series_poster"] = Pixmap()
        self["series_rail_caption"] = Label(_("SERIES"))
        self["series_grid_position"] = Label("")
        self["poster_scroll_track"] = Label("")
        self["poster_scroll_thumb"] = Label("")
        hide_scrollbar(self, "poster_scroll")
        for index in range(SERIES_GRID_PAGE_SIZE):
            self["series_tile_bg_{}".format(index)] = Label("")
            self["series_tile_focus_{}".format(index)] = Label("")
            self["series_tile_inner_{}".format(index)] = Label("")
            self["series_tile_placeholder_{}".format(index)] = Label("GT")
            self["series_tile_poster_{}".format(index)] = Pixmap()
            self["series_tile_favorite_{}".format(index)] = Pixmap()
            self["series_tile_title_{}".format(index)] = Label("")
        self["series_detail_panel"] = Label("")
        self["series_detail_accent"] = Label("")
        self["series_title"] = Label(_("Selecting series..."))
        self["series_rating"] = Label("")
        self["series_meta"] = Label("")
        self["series_plot_caption"] = Label(_("SERIES SUMMARY"))
        series_plot_text = _("Loading series information from the server.")
        self["series_plot"] = (
            GTSmoothScrollLabel(series_plot_text)
            if self._cinematic_enabled
            else Label(series_plot_text)
        )
        self["series_people"] = Label("")
        # Keep one physical-key context only; see the movie browser above.
        self["actions"] = ActionMap(
            [
                "OkCancelActions",
                "DirectionActions",
                "ColorActions",
                "MoviePlayerActions",
            ],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "channelUp": self.channel_page_up,
                "channelDown": self.channel_page_down,
                "green": self.open_series_continue,
                "yellow": self.open_category_manager,
                "blue": self.open_series_search,
                "red": self.toggle_selected_favorite,
            },
            -1,
        )
        self._clear_series_tiles()
        self["series_poster"].hide()
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop_series)

    def _set_series_title(self, value):
        return fit_dynamic_text(
            self["series_title"],
            value,
            max_lines=2,
            preferred_size=self._series_title_preferred_font,
            min_size=self._series_title_min_font,
            fallback_chars=18,
        )

    def _set_static_series_title(self, value):
        return set_static_text(
            self["series_title"],
            value,
            preferred_size=self._series_title_preferred_font,
        )

    def _set_series_plot(self, value):
        if getattr(self, "_cinematic_enabled", False):
            rendered = clean_dynamic_text(value)
            self["series_plot"].setText(rendered)
            return rendered
        return fit_dynamic_text(
            self["series_plot"],
            value,
            max_lines=17,
            fallback_chars=36,
        )

    def _series_rail_page_size(self):
        return (
            5
            if getattr(self, "_cinematic_enabled", False)
            else SERIES_GRID_PAGE_SIZE
        )

    def _refresh_series_categories(self):
        try:
            set_visible = _set_component_visible
        except NameError:
            def set_visible(component, visible):
                if visible:
                    component.show()
                else:
                    component.hide()
        try:
            update_text = set_text_if_changed
        except NameError:
            def update_text(component, value):
                text = "" if value is None else str(value)
                current = getattr(component, "message", None)
                if current is None or str(current) != text:
                    component.setText(text)
        if self._error:
            ellipsize_dynamic_text(
                self["message"],
                _(self._error),
                fallback_chars=20,
            )
            self._clear_series_categories()
            hide_scrollbar(self, "list_scroll")
            return
        if not self._entries:
            update_text(self["message"], _("Series category not found"))
            self._clear_series_categories()
            hide_scrollbar(self, "list_scroll")
            return
        self.selected_index %= len(self._entries)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start : page_start + self.page_size]
        for row in range(self.page_size):
            name = ""
            selected = False
            if row < len(page_items):
                absolute = page_start + row
                name = page_items[row].name
                selected = (
                    self._series_focus == "categories"
                    and absolute == self.selected_index
                )
            ellipsize_dynamic_text(
                self["series_category_{}".format(row)],
                name,
                fallback_chars=18,
            )
            for widget_name in (
                "series_category_focus_{}".format(row),
                "series_category_marker_{}".format(row),
            ):
                set_visible(self[widget_name], selected)
        category = self._entries[self.selected_index]
        update_text(
            self["message"],
            "{}: {} • {}/{}".format(
                _("Categories"),
                len(self._entries),
                self.selected_index + 1,
                len(self._entries),
            ),
        )
        update_scrollbar(
            self, "list_scroll", len(self._entries), self.selected_index,
            self.page_size, self._list_scroll_geometry,
        )
        if self._series_loaded_category_id != category.category_id:
            self._schedule_series_load(
                category,
                immediate=not self._series_category_load_started,
            )

    def _clear_series_categories(self):
        for row in range(self.page_size):
            self["series_category_{}".format(row)].setText("")
            self["series_category_focus_{}".format(row)].hide()
            self["series_category_marker_{}".format(row)].hide()

    def _loaded_series_category(self):
        category_id = str(self._series_loaded_category_id or "")
        for category in self._entries:
            if str(getattr(category, "category_id", "") or "") == category_id:
                return category
        return None

    def _selected_series_category_is_loaded(self):
        if not self._entries:
            return False
        category = self._entries[self.selected_index]
        return str(self._series_loaded_category_id or "") == str(
            getattr(category, "category_id", "") or ""
        )

    def _remember_series_selection(self):
        if not self._series_entries:
            return
        category_id = str(self._series_loaded_category_id or "")
        if not category_id:
            return
        index = max(
            0,
            min(self._series_selected_index, len(self._series_entries) - 1),
        )
        item = self._series_entries[index]
        self._series_selection_by_category[category_id] = (
            index,
            str(getattr(item, "stream_id", "") or ""),
        )

    def _restore_series_selection(self):
        """Restore a category's grid position without wrapping partial pages."""
        if not self._series_entries:
            self._series_selected_index = 0
            return
        category_id = str(self._series_loaded_category_id or "")
        saved = self._series_selection_by_category.get(category_id)
        if not saved:
            self._series_selected_index = 0
            return
        saved_index, saved_stream_id = saved
        saved_stream_id = str(saved_stream_id or "")
        if saved_stream_id:
            for index, item in enumerate(self._series_entries):
                if str(getattr(item, "stream_id", "") or "") == saved_stream_id:
                    self._series_selected_index = index
                    return
        try:
            saved_index = max(0, int(saved_index))
        except (TypeError, ValueError, OverflowError):
            saved_index = 0
        self._series_selected_index = min(
            saved_index,
            len(self._series_entries) - 1,
        )
        if saved_index >= len(self._series_entries) and self._series_has_more:
            self._series_pending_advance = True
            self._series_pending_selection_index = saved_index
            self._series_pending_clamp_to_end = True

    def _schedule_series_load(self, category=None, immediate=False):
        if not self._entries or self._closed:
            return
        category = category or self._entries[self.selected_index]
        self._series_pending_category_id = category.category_id
        try:
            self._series_category_timer.stop()
        except Exception:
            pass
        if immediate:
            self._commit_series_category_load()
        else:
            self._series_category_timer.start(NAVIGATION_DEBOUNCE_MS, True)

    def _commit_series_category_load(self):
        if not self._entries or self._closed:
            return
        category = self._entries[self.selected_index]
        if category.category_id != self._series_pending_category_id:
            return
        self._series_pending_category_id = None
        self._start_series_load(category)

    def _start_series_load(self, category=None):
        if not self._entries or self._closed:
            return
        category = category or self._entries[self.selected_index]
        self._remember_series_selection()
        for timer in (
            self._series_category_timer,
            self._series_detail_timer,
            self._series_scan_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass
        self._series_pending_category_id = None
        self._series_pending_detail_generation = None
        for token in (
            self._series_load_token,
            self._series_more_token,
            self._series_detail_token,
            self._series_poster_token,
        ):
            _cancel_worker_token(token)
        self._series_category_load_started = True
        self._series_generation += 1
        self._series_catalog_generation += 1
        self._series_poster_generation = getattr(
            self, "_series_poster_generation", self._series_generation
        ) + 1
        generation = self._series_generation
        self._series_loading = True
        self._series_ready = False
        self._series_error = ""
        self._series_entries = []
        self._series_selected_index = 0
        self._series_loaded_category_id = category.category_id
        self._series_detail_result = None
        self._series_detail_pending = False
        self._series_poster_state = None
        self._series_poster_pending = False
        self._series_poster_page_key = None
        self._series_has_more = False
        self._series_more_loading = False
        self._series_more_result = None
        self._series_pending_advance = False
        self._series_pending_selection_index = None
        self._series_pending_clamp_to_end = False
        self._series_load_token = None
        self._series_more_token = None
        self._series_detail_token = None
        self._series_poster_token = None
        self._series_deferred_poster_jobs = None
        self._series_info = None
        self._series_info_stream_id = ""
        ellipsize_dynamic_text(
            self["series_rail_caption"],
            _("{} • LOADING SERIES").format(localized_upper(category.name)),
            fallback_chars=65,
        )
        self._clear_series_tiles()
        self._render_series_waiting()
        token = _WorkerToken(WORKER_LIST_TIMEOUT_SECONDS)
        self._series_load_token = token
        worker = threading.Thread(
            target=self._load_series_worker,
            args=(generation, category.category_id, token),
        )
        worker.daemon = True
        worker.start()
        self._series_timer.start(120, True)

    def _load_series_worker(self, generation, category_id, token=None):
        try:
            entries = list(
                _bounded_client_call(
                    self.client,
                    token,
                    lambda: self.client.load_items("series", category_id),
                )
                or []
            )
            if not str(category_id or "").strip() or category_filter_active(
                getattr(self, "_hidden_category_ids", ())
            ):
                entries = filter_hidden_category_items(
                    entries,
                    self._hidden_category_ids,
                    category_id,
                )
            error = ""
        except _WorkerCancelled:
            return
        except ContentError as caught:
            entries = []
            error = str(caught)
        except Exception:
            entries = []
            error = N_("Could not load series")
        if (
            self._closed
            or generation != self._series_generation
            or (token is not None and not token.active())
        ):
            return
        self._series_entries = entries
        self._series_error = error
        self._series_has_more = self._client_has_more_items(
            "series", category_id
        )
        self._series_ready = True

    def _start_series_more_load(self):
        loader = getattr(self.client, "load_more_items", None)
        if (
            not callable(loader)
            or not self._series_has_more
            or self._series_more_loading
            or self._closed
        ):
            return False
        try:
            self._series_scan_timer.stop()
        except Exception:
            pass
        self._series_more_loading = True
        generation = self._series_catalog_generation
        category_id = str(self._series_loaded_category_id or "")
        token = _WorkerToken(WORKER_LIST_TIMEOUT_SECONDS)
        _cancel_worker_token(self._series_more_token)
        self._series_more_token = token
        worker = threading.Thread(
            target=self._load_series_more_worker,
            args=(generation, category_id, loader, token),
        )
        worker.daemon = True
        worker.start()
        self._series_timer.start(80, True)
        return True

    def _load_series_more_worker(
        self,
        generation,
        category_id,
        loader,
        token=None,
    ):
        try:
            entries = list(
                _bounded_client_call(
                    self.client,
                    token,
                    lambda: loader("series", category_id),
                )
                or []
            )
            if not str(category_id or "").strip() or category_filter_active(
                getattr(self, "_hidden_category_ids", ())
            ):
                entries = filter_hidden_category_items(
                    entries,
                    self._hidden_category_ids,
                    category_id,
                )
            error = ""
        except _WorkerCancelled:
            return
        except ContentError as caught:
            entries = []
            error = str(caught)
        except Exception:
            entries = []
            error = N_("Could not load series")
        has_more = self._client_has_more_items("series", category_id)
        if (
            self._closed
            or generation != self._series_catalog_generation
            or (token is not None and not token.active())
        ):
            return
        self._series_more_result = (
            generation,
            category_id,
            entries,
            has_more,
            error,
        )

    def _maybe_load_more_series(self):
        if not self._series_entries or not self._series_has_more:
            return False
        page_size = (
            5
            if getattr(self, "_cinematic_enabled", False)
            else SERIES_GRID_PAGE_SIZE
        )
        remaining = len(self._series_entries) - self._series_selected_index - 1
        if (
            remaining > page_size
            and not self._series_pending_advance
        ):
            return False
        return self._start_series_more_load()

    def _series_catalog_diagnostic(self):
        diagnostic = getattr(self.client, "catalog_diagnostic", None)
        if not callable(diagnostic):
            return {}
        try:
            result = diagnostic(
                "series", str(self._series_loaded_category_id or "")
            )
        except Exception:
            return {}
        return result if isinstance(result, dict) else {}

    def _series_scan_caption(self):
        diagnostic = self._series_catalog_diagnostic()
        pages = int(diagnostic.get("pages", 0) or 0)
        return _("Series scan • {} pages").format(pages)

    def _schedule_series_scan(self, delay_ms=SERIES_PROGRESSIVE_SCAN_DELAY_MS):
        if (
            self._closed
            or self._series_loading
            or self._series_more_loading
            or not self._series_has_more
        ):
            return False
        try:
            self._series_scan_timer.stop()
            self._series_scan_timer.start(max(1, int(delay_ms)), True)
        except Exception:
            return False
        return True

    def _resume_series_scan(self):
        if (
            self._closed
            or self._series_loading
            or self._series_more_loading
            or not self._series_has_more
        ):
            return
        # Detail requests use the same single-request slot.  Let the
        # selected series become usable before this low-priority scan resumes.
        if (
            self._series_detail_pending
            or self._series_pending_detail_generation is not None
        ):
            self._schedule_series_scan()
            return
        self._start_series_more_load()

    def _update_series_rail_caption(self):
        if not self._entries or not self._series_entries:
            return
        category = self._loaded_series_category()
        if category is None:
            return
        ellipsize_dynamic_text(
            self["series_rail_caption"],
            _("{} • {} SERIES").format(
                localized_upper(category.name), len(self._series_entries)
            ),
            fallback_chars=65,
        )

    def _apply_series_more_result(self):
        result = self._series_more_result
        if result is None:
            return
        self._series_more_result = None
        generation, category_id, entries, has_more, error = result
        if (
            generation != self._series_catalog_generation
            or category_id != str(self._series_loaded_category_id or "")
        ):
            return
        self._series_more_loading = False
        self._series_more_token = None
        self._series_has_more = bool(has_more)
        if error:
            self._series_has_more = False
            self._series_pending_advance = False
            self._series_pending_selection_index = None
            self._series_pending_clamp_to_end = False
            return
        old_length = len(self._series_entries)
        known = {str(item.stream_id) for item in self._series_entries}
        for item in entries:
            stream_id = str(getattr(item, "stream_id", ""))
            if not stream_id or stream_id in known:
                continue
            known.add(stream_id)
            self._series_entries.append(item)
        if (
            old_length == 0
            and self._series_entries
            and not self._series_pending_advance
        ):
            self._series_pending_advance = False
            self._series_pending_selection_index = None
            self._series_pending_clamp_to_end = False
            self._render_series_results()
            return
        if not self._series_entries:
            if self._series_has_more and not error:
                self["series_rail_caption"].setText(
                    self._series_scan_caption()
                )
                self._set_series_plot(_("Please wait"))
                self._schedule_series_scan()
                return
            self._series_pending_advance = False
            self._series_pending_selection_index = None
            self._series_pending_clamp_to_end = False
            self._render_series_results()
            return
        self._update_series_rail_caption()
        if self._series_pending_advance:
            pending_index = getattr(
                self,
                "_series_pending_selection_index",
                None,
            )
            if pending_index is None and len(self._series_entries) > old_length:
                pending_index = old_length
            if (
                pending_index is not None
                and pending_index < len(self._series_entries)
            ):
                self._series_pending_advance = False
                self._series_pending_selection_index = None
                self._series_pending_clamp_to_end = False
                self._select_series(pending_index)
                return
            if self._series_has_more:
                self._start_series_more_load()
                return
            clamp_to_end = self._series_pending_clamp_to_end
            self._series_pending_advance = False
            self._series_pending_selection_index = None
            self._series_pending_clamp_to_end = False
            if clamp_to_end and self._series_entries:
                last_index = len(self._series_entries) - 1
                if last_index != self._series_selected_index:
                    self._select_series(last_index)
                    return
        self._series_pending_advance = False
        self._series_pending_selection_index = None
        self._series_pending_clamp_to_end = False
        self._render_series_tiles()
        self._schedule_series_scan()

    def _poll_series_result(self):
        if self._closed:
            return
        if self._series_loading:
            if not self._series_ready:
                if (
                    self._series_load_token is not None
                    and self._series_load_token.expired()
                ):
                    _cancel_worker_token(self._series_load_token)
                    self._series_loading = False
                    self._series_error = N_("Series request timed out")
                    self._render_series_results()
                    return
                self._series_timer.start(120, True)
                return
            self._series_loading = False
            self._series_load_token = None
            self._render_series_results()
            return
        if (
            self._series_more_loading
            and self._series_more_token is not None
            and self._series_more_token.expired()
        ):
            _cancel_worker_token(self._series_more_token)
            self._series_more_loading = False
            self._series_more_token = None
            self._series_pending_advance = False
            self._series_pending_selection_index = None
        self._apply_series_more_result()
        detail = self._series_detail_result
        if detail is not None:
            self._series_detail_result = None
            self._series_detail_pending = False
            self._series_detail_token = None
            (
                generation,
                info,
                poster_path,
                metadata_status,
                poster_url,
                catalogue_poster_url,
            ) = detail
            if generation == self._series_generation:
                self._apply_series_detail(
                    info,
                    poster_path,
                    metadata_status,
                    poster_url,
                    catalogue_poster_url,
                )
            self._start_deferred_series_posters()
        elif (
            self._series_detail_pending
            and self._series_detail_token is not None
            and self._series_detail_token.expired()
        ):
            _cancel_worker_token(self._series_detail_token)
            self._series_detail_token = None
            self._series_detail_pending = False
            self._start_deferred_series_posters()
        state = self._series_poster_state
        if state is not None:
            state_token = state.get("token")
            state_expired = (
                state_token is not None and state_token.expired()
            )
            if state_expired:
                _cancel_worker_token(state_token)
            with state["lock"]:
                results = list(state["results"])
                del state["results"][:]
                complete = (
                    state_expired or state["pending_workers"] <= 0
                )
            generation = state["generation"]
            page_is_current = (
                state.get("page_key") == self._series_poster_page_key
            )
            if page_is_current:
                for (
                    slot,
                    stream_id,
                    path,
                    requested_url,
                    resolved_url,
                    fallback_deferred,
                ) in results:
                    try:
                        page_start = int(state["page_key"][0])
                        page_item = self._series_entries[page_start + slot]
                        result_is_current = (
                            generation
                            == getattr(
                                self,
                                "_series_poster_generation",
                                self._series_generation,
                            )
                            and str(page_item.stream_id) == str(stream_id)
                        )
                    except Exception:
                        page_item = None
                        result_is_current = False
                    if not result_is_current:
                        continue
                    current_url = _safe_picon_url(
                        getattr(page_item, "icon", "")
                    )
                    requested_url = _safe_picon_url(requested_url)
                    resolved_url = _safe_picon_url(resolved_url)
                    if path and current_url not in (
                        "",
                        requested_url,
                        resolved_url,
                    ):
                        continue
                    if path and resolved_url and current_url in (
                        "",
                        requested_url,
                    ):
                        page_item.icon = resolved_url
                    if fallback_deferred and not path:
                        retry_url = _safe_picon_url(
                            getattr(page_item, "icon", "")
                        )
                        self._series_deferred_poster_jobs = (
                            self._series_poster_page_key,
                            [(slot, page_item, retry_url)],
                            True,
                        )
                    self._apply_series_tile_poster(
                        slot,
                        path,
                        stream_id=stream_id,
                        generation=generation,
                    )
                    try:
                        selected_item = self._series_entries[
                            self._series_selected_index
                        ]
                    except Exception:
                        selected_item = None
                    if (
                        selected_item is not None
                        and str(selected_item.stream_id) == str(stream_id)
                    ):
                        self._apply_detail_poster(
                            "series",
                            selected_item,
                            path,
                        )
            if complete or not page_is_current:
                if self._series_poster_state is state:
                    self._series_poster_state = None
                    self._series_poster_pending = False
                    self._series_poster_token = None
                if page_is_current:
                    self._start_deferred_series_posters()
        poll_cinematic = getattr(self, "_poll_cinematic_backdrop", None)
        cinematic_pending = bool(
            poll_cinematic() if callable(poll_cinematic) else False
        )
        if (
            self._series_detail_pending
            or self._series_poster_pending
            or self._series_more_loading
            or cinematic_pending
        ):
            self._series_timer.start(80, True)

    def _render_series_waiting(self, item=None):
        try:
            set_visible = _set_component_visible
        except NameError:
            def set_visible(component, visible):
                if visible:
                    component.show()
                else:
                    component.hide()
        reset_cinematic = getattr(
            self,
            "_reset_cinematic_backdrop",
            None,
        )
        if callable(reset_cinematic):
            reset_cinematic()
        if item is not None and getattr(self, "_cinematic_enabled", False):
            self._cinematic_surface_prepared = (
                "series",
                str(getattr(item, "stream_id", "") or ""),
                getattr(self, "_series_generation", None),
            )
        if item is None:
            self._set_static_series_title(_("Selecting series..."))
            set_text_if_changed(self["series_rating"], "")
            set_text_if_changed(self["series_meta"], "")
        else:
            self._set_series_title(item.name)
            set_text_if_changed(
                self["series_rating"],
                _rating_label(item.rating),
            )
            fit_dynamic_text(
                self["series_meta"],
                _year_not_in_title(item.name, item.year),
                max_lines=4,
                fallback_chars=18,
            )
        self._set_series_plot(_("Loading series information from the server."))
        set_text_if_changed(self["series_people"], "")
        set_visible(self["series_poster"], False)
        set_visible(self["series_poster_placeholder"], True)
        self._apply_detail_poster("series", item)

    def _series_catalog_outcome(self):
        result = self._series_catalog_diagnostic()
        if not result.get("complete"):
            return ""
        return str(result.get("outcome", "") or "")

    def _render_series_results(self):
        if self._series_error:
            self["series_rail_caption"].setText(_("COULD NOT LOAD SERIES"))
            self._set_series_plot(_(self._series_error))
            self._clear_series_tiles()
            return
        if not self._series_entries:
            if self._series_has_more:
                self["series_rail_caption"].setText(
                    self._series_scan_caption()
                )
                self._set_series_plot(_("Please wait"))
                self._clear_series_tiles()
                self._schedule_series_scan()
                return
            outcome = self._series_catalog_outcome()
            if outcome == "unsupported":
                self["series_rail_caption"].setText(_("SERIES DATA COULD NOT BE READ"))
                self._set_series_plot(
                    _("The series response uses an unsupported format. Categories remain available.")
                )
            elif outcome == "error":
                self["series_rail_caption"].setText(_("SERIES DATA UNAVAILABLE"))
                self._set_series_plot(
                    _("The server did not answer the series request. Categories remain available.")
                )
            else:
                self["series_rail_caption"].setText(
                    _("NO SERIES FOUND IN THIS CATEGORY")
                )
                self._set_series_plot(
                    _("No series were found on this server or in this category.")
                )
            self._clear_series_tiles()
            return
        self._restore_series_selection()
        self._update_series_rail_caption()
        self._schedule_series_detail()
        self._render_series_tiles()
        self._maybe_load_more_series()
        self._schedule_series_scan()

    def _render_series_tiles(self):
        try:
            set_visible = _set_component_visible
        except NameError:
            def set_visible(component, visible):
                if visible:
                    component.show()
                else:
                    component.hide()
        try:
            update_text = set_text_if_changed
        except NameError:
            def update_text(component, value):
                text = "" if value is None else str(value)
                current = getattr(component, "message", None)
                if current is None or str(current) != text:
                    component.setText(text)
        page_size = (
            5
            if getattr(self, "_cinematic_enabled", False)
            else SERIES_GRID_PAGE_SIZE
        )
        page_start = (
            int(self._series_selected_index / page_size)
            * page_size
        )
        page = self._series_entries[
            page_start : page_start + page_size
        ]
        page_key = (
            page_start,
            tuple(
                (
                    str(getattr(item, "stream_id", "")),
                    _safe_picon_url(getattr(item, "icon", "")),
                )
                for item in page
            ),
        )
        page_changed = page_key != self._series_poster_page_key
        if page_changed:
            self._series_poster_generation = getattr(
                self, "_series_poster_generation", self._series_generation
            ) + 1
            _cancel_worker_token(self._series_poster_token)
            self._series_poster_token = None
            self._series_poster_pending = False
            self._series_poster_state = None
            self._series_deferred_poster_jobs = None
            self._series_poster_page_key = page_key
        jobs = []
        for slot in range(page_size):
            selected = False
            if slot < len(page):
                absolute = page_start + slot
                item = page[slot]
                selected = (
                    self._series_focus == "series"
                    and absolute == self._series_selected_index
                )
                ellipsize_dynamic_text(
                    self["series_tile_title_{}".format(slot)],
                    item.name,
                    fallback_chars=18,
                )
                set_visible(self["series_tile_bg_{}".format(slot)], True)
                set_visible(self["series_tile_inner_{}".format(slot)], True)
                set_visible(self["series_tile_title_{}".format(slot)], True)
                set_visible(
                    self["series_tile_favorite_{}".format(slot)],
                    self._is_favorite(item),
                )
                if page_changed:
                    set_visible(
                        self["series_tile_placeholder_{}".format(slot)],
                        True,
                    )
                    set_visible(
                        self["series_tile_poster_{}".format(slot)],
                        False,
                    )
                    url = _safe_picon_url(getattr(item, "icon", ""))
                    if url:
                        cached = _cached_picon_path(url)
                        if cached:
                            self._apply_series_tile_poster(
                                slot,
                                cached,
                                stream_id=item.stream_id,
                                generation=getattr(
                                    self,
                                    "_series_poster_generation",
                                    self._series_generation,
                                ),
                            )
                        else:
                            jobs.append((slot, item, url))
                    else:
                        jobs.append((slot, item, ""))
            else:
                update_text(self["series_tile_title_{}".format(slot)], "")
                for family in (
                    "bg",
                    "inner",
                    "title",
                    "placeholder",
                    "poster",
                    "favorite",
                ):
                    set_visible(
                        self["series_tile_{}_{}".format(family, slot)],
                        False,
                    )
            set_visible(
                self["series_tile_focus_{}".format(slot)],
                selected,
            )
        for slot in range(page_size, SERIES_GRID_PAGE_SIZE):
            update_text(self["series_tile_title_{}".format(slot)], "")
            for family in (
                "bg",
                "inner",
                "title",
                "placeholder",
                "poster",
                "favorite",
                "focus",
            ):
                set_visible(
                    self["series_tile_{}_{}".format(family, slot)],
                    False,
                )
        update_horizontal_scrollbar(
            self,
            "poster_scroll",
            len(self._series_entries),
            self._series_selected_index,
            page_size,
            self._poster_scroll_geometry,
        )
        update_text(
            self["series_grid_position"],
            self._series_grid_position_text(),
        )
        if jobs:
            if self._series_pending_detail_generation is not None:
                self._series_deferred_poster_jobs = (page_key, jobs)
            else:
                self._start_series_poster_jobs(page_key, jobs)

    def _series_grid_position_text(self):
        if not self._series_entries:
            return ""
        page_size = (
            5
            if getattr(self, "_cinematic_enabled", False)
            else SERIES_GRID_PAGE_SIZE
        )
        page_start = (
            int(self._series_selected_index / page_size)
            * page_size
        )
        page_end = min(
            page_start + page_size,
            len(self._series_entries),
        )
        page_number = int(page_start / page_size) + 1
        page_label = localized_upper(_("Page"))
        if self._series_has_more:
            return "{}–{} / {}+  •  {} {}".format(
                page_start + 1,
                page_end,
                len(self._series_entries),
                page_label,
                page_number,
            )
        page_count = max(
            1,
            int(
                (len(self._series_entries) + page_size - 1)
                / page_size
            ),
        )
        return "{}–{} / {}  •  {} {}/{}".format(
            page_start + 1,
            page_end,
            len(self._series_entries),
            page_label,
            page_number,
            page_count,
        )

    def _start_series_poster_jobs(self, page_key, jobs):
        if self._closed or page_key != self._series_poster_page_key:
            return
        selected_slot = self._series_selected_index - int(page_key[0])
        jobs = sorted(
            jobs,
            key=lambda job: 0 if int(job[0]) == selected_slot else 1,
        )
        if not jobs:
            self._series_deferred_poster_jobs = None
            return
        generation = getattr(
            self, "_series_poster_generation", self._series_generation
        )
        token = _WorkerToken(WORKER_POSTER_TIMEOUT_SECONDS)
        _cancel_worker_token(self._series_poster_token)
        self._series_poster_token = token
        self._series_poster_pending = True
        worker_count = min(BACKGROUND_POSTER_WORKERS, len(jobs))
        state = {
            "generation": generation,
            "page_key": page_key,
            "selected_slot": selected_slot,
            "results": [],
            "pending_workers": worker_count,
            "batch_workers": worker_count,
            "metadata_cache_batch": False,
            "metadata_cache_client": None,
            "lock": threading.Lock(),
            "token": token,
        }
        metadata_client = getattr(self, "_metadata_client", None)
        begin_batch = getattr(
            metadata_client, "begin_cache_batch", None
        )
        if callable(begin_batch):
            try:
                begin_batch()
                state["metadata_cache_batch"] = True
                state["metadata_cache_client"] = metadata_client
            except Exception as error:
                _debug(
                    "series metadata cache batch failed error={}".format(
                        error.__class__.__name__
                    )
                )
        self._series_poster_state = state
        self._series_deferred_poster_jobs = None
        for worker_index in range(worker_count):
            worker = threading.Thread(
                target=self._load_series_posters_worker,
                args=(
                    jobs[worker_index::worker_count],
                    state,
                    token,
                ),
            )
            worker.daemon = True
            worker.start()
        self._series_timer.start(50, True)

    def _start_deferred_series_posters(self):
        deferred = self._series_deferred_poster_jobs
        if deferred is not None and self._series_poster_state is None:
            wait_for_detail = len(deferred) > 2 and bool(deferred[2])
            if wait_for_detail and (
                self._series_detail_pending
                or self._series_pending_detail_generation is not None
            ):
                return
            self._start_series_poster_jobs(deferred[0], deferred[1])

    def _load_series_posters_worker(
        self,
        jobs,
        state,
        token=None,
    ):
        try:
            for slot, item, url in jobs:
                if token is not None:
                    token.check()
                path = ""
                resolved_url = url
                fallback_deferred = False
                is_selected = int(slot) == int(state.get("selected_slot", -1))
                if url:
                    try:
                        path = _download_client_image(
                            self.client,
                            url,
                            _picon_cache_path(url),
                            timeout=_provider_picon_timeout(
                                self._metadata_client,
                                url,
                            ),
                            token=token,
                        )
                    except _WorkerCancelled:
                        raise
                    except Exception as error:
                        _debug(
                            "series poster failed id={} error={}".format(
                                _debug_identifier(item.stream_id),
                                error.__class__.__name__,
                            )
                        )
                if not path:
                    fallback_deferred = bool(
                        is_selected
                        and (
                            self._series_detail_pending
                            or self._series_pending_detail_generation is not None
                        )
                    )
                    if not fallback_deferred:
                        if token is not None:
                            token.check()
                        try:
                            completed = _bounded_client_call(
                                self._metadata_client,
                                token,
                                lambda: self._metadata_client.complete_series(
                                    item,
                                    _detail_cache_get(
                                        self._series_info_cache,
                                        self._series_info_cache_lock,
                                        item.stream_id,
                                    )
                                    if hasattr(self, "_series_info_cache_lock")
                                    else self._series_info_cache.get(item.stream_id),
                                    force_cover_fallback=True,
                                ),
                            )
                        except MetadataError as error:
                            _debug(
                                "TMDb series poster fallback failed id={} error={}".format(
                                    _debug_identifier(item.stream_id),
                                    error.__class__.__name__,
                                )
                            )
                            completed = None
                        except _WorkerCancelled:
                            raise
                        except Exception as error:
                            _debug(
                                "series poster fallback failed id={} error={}".format(
                                    _debug_identifier(item.stream_id),
                                    error.__class__.__name__,
                                )
                            )
                            completed = None
                        fallback_url = _safe_picon_url(
                            getattr(completed, "cover", "")
                        )
                        if fallback_url and fallback_url != url:
                            try:
                                path = _download_client_image(
                                    self.client,
                                    fallback_url,
                                    _picon_cache_path(fallback_url),
                                    token=token,
                                )
                            except _WorkerCancelled:
                                raise
                            except Exception as error:
                                _debug(
                                    "series fallback image failed id={} error={}".format(
                                        _debug_identifier(item.stream_id),
                                        error.__class__.__name__,
                                    )
                                )
                            if path:
                                resolved_url = fallback_url
                with state["lock"]:
                    state["results"].append(
                        (
                            slot,
                            item.stream_id,
                            path,
                            url,
                            resolved_url if path else "",
                            fallback_deferred,
                        )
                    )
        finally:
            finish_batch = False
            with state["lock"]:
                state["pending_workers"] -= 1
                if "batch_workers" in state:
                    state["batch_workers"] -= 1
                    if (
                        state["batch_workers"] <= 0
                        and state.get("metadata_cache_batch")
                    ):
                        state["metadata_cache_batch"] = False
                        finish_batch = True
            if finish_batch:
                try:
                    state["metadata_cache_client"].end_cache_batch()
                except Exception as error:
                    _debug(
                        "series metadata cache flush failed error={}".format(
                            error.__class__.__name__
                        )
                    )

    def _apply_series_tile_poster(
        self,
        slot,
        path,
        stream_id="",
        generation=None,
    ):
        if not path:
            return
        generation = (
            getattr(
                self, "_series_poster_generation", self._series_generation
            )
            if generation is None
            else generation
        )

        def loaded(success):
            try:
                page_size = (
                    5
                    if getattr(self, "_cinematic_enabled", False)
                    else SERIES_GRID_PAGE_SIZE
                )
                page_start = (
                    int(self._series_selected_index / page_size)
                    * page_size
                )
                current = self._series_entries[page_start + slot]
                valid = (
                    generation
                    == getattr(
                        self,
                        "_series_poster_generation",
                        self._series_generation,
                    )
                    and str(current.stream_id) == str(stream_id)
                )
                if success and valid:
                    self["series_tile_poster_{}".format(slot)].show()
                    self["series_tile_placeholder_{}".format(slot)].hide()
                else:
                    self["series_tile_poster_{}".format(slot)].hide()
            except Exception:
                pass

        attach_pixmap(
            self,
            "series_tile_poster_{}".format(slot),
            path,
            key="series-tile-{}".format(slot),
            on_loaded=loaded,
        )

    def _start_series_detail(self):
        if not self._series_entries:
            return
        item = self._series_entries[self._series_selected_index]
        generation = self._series_generation
        _cancel_worker_token(self._series_detail_token)
        token = _WorkerToken(WORKER_DETAIL_TIMEOUT_SECONDS)
        self._series_detail_token = token
        worker = threading.Thread(
            target=self._load_series_detail_worker,
            args=(generation, item, token),
        )
        worker.daemon = True
        self._series_detail_pending = True
        worker.start()
        self._start_deferred_series_posters()
        self._series_timer.start(80, True)

    def _schedule_series_detail(self, immediate=False):
        if not self._series_entries or self._closed:
            return
        self._series_pending_detail_generation = self._series_generation
        _cancel_worker_token(self._series_detail_token)
        self._series_detail_token = None
        try:
            self._series_detail_timer.stop()
        except Exception:
            pass
        item = self._series_entries[self._series_selected_index]
        self._render_series_waiting(item)
        if immediate:
            self._commit_series_detail()
        else:
            self._series_detail_timer.start(NAVIGATION_DEBOUNCE_MS, True)

    def _commit_series_detail(self):
        if (
            self._closed
            or self._series_pending_detail_generation != self._series_generation
        ):
            return
        self._series_pending_detail_generation = None
        self._start_series_detail()

    def _load_series_detail_worker(self, generation, item, token=None):
        if token is not None:
            token.check()
        catalogue_poster_url = _safe_picon_url(getattr(item, "icon", ""))
        info = (
            _detail_cache_get(
                self._series_info_cache,
                self._series_info_cache_lock,
                item.stream_id,
            )
            if hasattr(self, "_series_info_cache_lock")
            else self._series_info_cache.get(item.stream_id)
        )
        metadata_status = ""
        loader = getattr(self.client, "load_series_info", None)
        if info is None and loader is not None:
            try:
                info = _bounded_client_call(
                    self.client,
                    token,
                    lambda: loader(item.stream_id),
                )
                if hasattr(self, "_series_info_cache_lock"):
                    _detail_cache_put(
                        self._series_info_cache,
                        self._series_info_cache_lock,
                        item.stream_id,
                        info,
                    )
                else:
                    self._series_info_cache[item.stream_id] = info
            except Exception as error:
                _debug(
                    "series detail failed id={} error={}".format(
                        _debug_identifier(item.stream_id),
                        error.__class__.__name__,
                    )
                )
                info = None
        if token is not None:
            token.check()
        try:
            completer = None
            if self._cinematic_enabled:
                completer = getattr(
                    self._metadata_client,
                    "complete_series_cinematic",
                    None,
                )
            if not callable(completer):
                completer = self._metadata_client.complete_series
            info = _bounded_client_call(
                self._metadata_client,
                token,
                lambda: completer(item, info),
            )
            if hasattr(self, "_series_info_cache_lock"):
                _detail_cache_put(
                    self._series_info_cache,
                    self._series_info_cache_lock,
                    item.stream_id,
                    info,
                )
            else:
                self._series_info_cache[item.stream_id] = info
            metadata_status = str(
                getattr(self._metadata_client, "last_error", "") or ""
            )
        except MetadataError as error:
            metadata_status = str(error)
            _debug(
                "TMDb series detail fallback failed id={} error={}".format(
                    _debug_identifier(item.stream_id),
                    error.__class__.__name__,
                )
            )
        poster_url = _safe_picon_url(
            getattr(info, "cover", "") or getattr(item, "icon", "")
        )
        poster_path = _cached_picon_path(poster_url) if poster_url else ""
        if (
            generation == self._series_generation
            and (token is None or token.active())
        ):
            self._series_detail_result = (
                generation,
                info,
                poster_path,
                metadata_status,
                poster_url,
                catalogue_poster_url,
            )

    def _apply_series_detail(
        self,
        info,
        poster_path,
        metadata_status="",
        poster_url="",
        catalogue_poster_url="",
    ):
        if not self._series_entries:
            return
        cinematic = bool(getattr(self, "_cinematic_enabled", False))

        # Preserve compatibility with callers that extract this method from
        # the screen class without importing the complete browser module.
        try:
            update_text = set_text_if_changed
        except NameError:
            def update_text(component, value):
                text = "" if value is None else str(value)
                getter = getattr(component, "getText", None)
                current = (
                    getter()
                    if callable(getter)
                    else getattr(
                        component,
                        "message",
                        getattr(component, "text", None),
                    )
                )
                if current is None or str(current) != text:
                    component.setText(text)

        def set_plot(value):
            setter = getattr(self, "_set_series_plot", None)
            if callable(setter):
                return setter(value)
            return fit_dynamic_text(
                self["series_plot"],
                value,
                max_lines=17,
                fallback_chars=36,
            )

        item = self._series_entries[self._series_selected_index]
        poster_url = _safe_picon_url(poster_url)
        catalogue_poster_url = _safe_picon_url(catalogue_poster_url)
        current_poster_url = _safe_picon_url(getattr(item, "icon", ""))
        poster_identity_changed = False
        if poster_url and (
            not current_poster_url
            or current_poster_url == catalogue_poster_url
        ):
            if current_poster_url != poster_url:
                item.icon = poster_url
                current_poster_url = poster_url
                poster_identity_changed = True
        if poster_path and poster_url and current_poster_url != poster_url:
            poster_path = ""
        metadata_status = str(metadata_status or "").strip()[:180]
        self._series_info = info
        self._series_info_stream_id = str(item.stream_id)
        if info is None:
            self._set_series_title(item.name)
            update_text(
                self["series_rating"],
                _rating_label(item.rating),
            )
            fit_dynamic_text(
                self["series_meta"],
                _year_not_in_title(item.name, item.year)
                or _("Information unavailable."),
                max_lines=4,
                fallback_chars=18,
            )
            set_plot(_("Series summary unavailable."))
            if cinematic:
                update_text(self["series_people"], "")
        else:
            display_title = info.title or item.name
            self._set_series_title(display_title)
            rating = info.rating or item.rating
            update_text(
                self["series_rating"],
                _rating_label(rating),
            )
            seasons = list(getattr(info, "seasons", []) or [])
            meta = [
                _year_not_in_title(
                    display_title,
                    info.year or item.year,
                )
            ]
            if not cinematic and info.genre:
                meta.append(info.genre)
            meta = [value for value in meta if value]
            if seasons:
                meta.append(
                    "{}: {}".format(
                        localized_upper(_("Seasons")) if cinematic
                        else _("Seasons"),
                        len(seasons),
                    )
                )
            fit_dynamic_text(
                self["series_meta"],
                ("  |  " if cinematic else " • ").join(meta)
                or _("Information unavailable."),
                max_lines=4,
                fallback_chars=18,
            )
            set_plot(info.plot or _("Series summary unavailable."))
            if cinematic:
                people = []
                director = getattr(info, "director", "")
                cast = getattr(info, "cast", "")
                if director:
                    people.append(_("Director: {}").format(director))
                if cast:
                    people.append(_("Cast: {}").format(cast))
                fit_dynamic_text(
                    self["series_people"],
                    "  |  ".join(people),
                    max_lines=2,
                    fallback_chars=100,
                )
        start_cinematic = getattr(self, "_start_cinematic_backdrop", None)
        if getattr(self, "_cinematic_enabled", False) and callable(
            start_cinematic
        ):
            start_cinematic(
                "series",
                item,
                info,
                catalogue_poster_url,
            )
        if self._apply_detail_poster("series", item, poster_path):
            stream_id = item.stream_id
            self._apply_series_tile_poster(
                self._series_selected_index % (
                    5
                    if cinematic
                    else SERIES_GRID_PAGE_SIZE
                ),
                poster_path,
                stream_id=stream_id,
                generation=getattr(
                    self,
                    "_series_poster_generation",
                    self._series_generation,
                ),
            )
        if poster_identity_changed:
            self._render_series_tiles()

    def _clear_series_tiles(self):
        for slot in range(SERIES_GRID_PAGE_SIZE):
            self["series_tile_title_{}".format(slot)].setText("")
            self["series_tile_bg_{}".format(slot)].hide()
            self["series_tile_inner_{}".format(slot)].hide()
            self["series_tile_title_{}".format(slot)].hide()
            self["series_tile_poster_{}".format(slot)].hide()
            self["series_tile_placeholder_{}".format(slot)].hide()
            self["series_tile_favorite_{}".format(slot)].hide()
            self["series_tile_focus_{}".format(slot)].hide()
        self["series_grid_position"].setText("")
        hide_scrollbar(self, "poster_scroll")

    def refresh_series(self):
        if self.content_type != "series":
            self.start_load()
            return
        self._remember_series_selection()
        for current in self._series_entries:
            url = _safe_picon_url(getattr(current, "icon", ""))
            if url:
                _clear_picon_failure(url)
        invalidator = getattr(self.client, "invalidate_cache", None)
        if invalidator is not None:
            try:
                category_id = (
                    self._entries[self.selected_index].category_id
                    if self._entries
                    else None
                )
                invalidator("series", category_id)
            except Exception:
                pass
        self._series_poster_page_key = None
        if self._series_focus == "categories":
            self.start_load()
        else:
            self._start_series_load()

    def _select_series_category(self, index):
        if not self._entries or self._loading:
            return
        self._remember_series_selection()
        self._series_focus = "categories"
        self._series_pending_advance = False
        self._series_pending_selection_index = None
        self._series_pending_clamp_to_end = False
        self.selected_index = index % len(self._entries)
        self._series_loaded_category_id = None
        self._refresh_series_categories()
        self._render_series_tiles()

    def _select_series(self, index):
        if not self._series_entries or self._series_loading:
            return
        focus_changed = self._series_focus != "series"
        self._series_focus = "series"
        index = int(index)
        if index < 0:
            return
        if index >= len(self._series_entries) and self._series_has_more:
            self._series_pending_advance = True
            self._series_pending_selection_index = index
            self._start_series_more_load()
            return
        if index >= len(self._series_entries):
            self._series_pending_clamp_to_end = False
            return
        self._series_pending_advance = False
        self._series_pending_selection_index = None
        self._series_pending_clamp_to_end = False
        self._series_selected_index = index
        _cancel_worker_token(self._series_detail_token)
        self._series_detail_token = None
        self._series_generation += 1
        self._series_info = None
        self._series_info_stream_id = ""
        self._remember_series_selection()
        self._schedule_series_detail()
        self._render_series_tiles()
        if focus_changed:
            self._refresh_series_categories()
        self._maybe_load_more_series()

    def _series_move_up(self):
        if self._series_focus == "categories":
            self._select_series_category(self.selected_index - 1)
            return
        if getattr(self, "_cinematic_enabled", False):
            self._series_focus = "categories"
            self._refresh_series_categories()
            self._render_series_tiles()
            return
        target = self._series_selected_index - SERIES_GRID_COLUMNS
        if target >= 0:
            self._select_series(target)

    def _series_move_down(self):
        if self._series_focus == "categories":
            self._select_series_category(self.selected_index + 1)
            return
        if getattr(self, "_cinematic_enabled", False):
            return
        target = self._series_selected_index + SERIES_GRID_COLUMNS
        if target < len(self._series_entries) or self._series_has_more:
            self._series_pending_clamp_to_end = False
            self._select_series(target)

    def _series_move_left(self):
        if self._series_focus == "categories":
            self._select_series_category(self.selected_index - self.page_size)
            return
        if getattr(self, "_cinematic_enabled", False):
            if self._series_selected_index == 0:
                self._series_focus = "categories"
                self._refresh_series_categories()
                self._render_series_tiles()
            else:
                self._select_series(self._series_selected_index - 1)
            return
        if self._series_selected_index % SERIES_GRID_COLUMNS == 0:
            self._series_focus = "categories"
            self._refresh_series_categories()
            self._render_series_tiles()
            return
        self._select_series(self._series_selected_index - 1)

    def _series_move_right(self):
        if self._series_focus == "categories":
            category = self._entries[self.selected_index] if self._entries else None
            if (
                category is not None
                and str(self._series_loaded_category_id or "")
                == str(getattr(category, "category_id", "") or "")
                and self._series_entries
            ):
                self._series_focus = "series"
                self._schedule_series_detail()
                self._refresh_series_categories()
                self._render_series_tiles()
            elif category is not None:
                self._start_series_load(category)
            return
        if getattr(self, "_cinematic_enabled", False):
            target = self._series_selected_index + 1
            if target < len(self._series_entries) or self._series_has_more:
                self._series_pending_clamp_to_end = False
                self._select_series(target)
            return
        if self._series_selected_index % SERIES_GRID_COLUMNS == (
            SERIES_GRID_COLUMNS - 1
        ):
            return
        target = self._series_selected_index + 1
        if target < len(self._series_entries) or self._series_has_more:
            self._series_pending_clamp_to_end = False
            self._select_series(target)

    def _series_page(self, direction):
        """Page the currently focused series pane and keep its focus."""
        step = -1 if int(direction) < 0 else 1
        if self._series_focus == "categories":
            if not self._entries:
                return
            target = self.selected_index + (step * self.page_size)
            target = max(0, min(target, len(self._entries) - 1))
            if target != self.selected_index:
                self._select_series_category(target)
            return
        if not self._series_entries:
            return
        page_size = (
            5
            if getattr(self, "_cinematic_enabled", False)
            else SERIES_RAIL_PAGE_SIZE
        )
        target = self._series_selected_index + (
            step * page_size
        )
        if step < 0:
            target = max(0, target)
        elif not self._series_has_more:
            target = min(target, len(self._series_entries) - 1)
        if target != self._series_selected_index:
            self._series_pending_clamp_to_end = step > 0
            self._select_series(target)

    def _open_series_child(self, screen, *args, **kwargs):
        if self._series_child_open:
            self["message"].setText(_("Please wait"))
            return None
        self._remember_series_selection()
        self._series_child_open = True
        self._suspend_for_playback()
        try:
            opener = getattr(self.session, "openWithCallback", None)
            if callable(opener):
                return opener(
                    self._series_child_closed,
                    screen,
                    *args,
                    **kwargs
                )
            dialog = self.session.open(screen, *args, **kwargs)
            on_close = getattr(dialog, "onClose", None)
            if isinstance(on_close, list):
                on_close.append(self._series_child_closed)
            return dialog
        except Exception as error:
            log_event(
                "series",
                "Could not open the series child screen",
                error=error,
            )
            self._series_child_closed()
            self["message"].setText(_("Could not open the content list."))
            return None

    def _series_child_closed(self, *args):
        if self._closed:
            return
        self._series_child_open = False
        self._reload_favorite_keys()
        self._series_poster_page_key = None
        self._refresh_series_categories()
        category_is_current = self._selected_series_category_is_loaded()
        if not category_is_current:
            self._clear_series_tiles()
            return
        if self._series_loading:
            self._series_timer.start(120, True)
        if self._series_entries:
            self._schedule_series_detail()
        self._render_series_tiles()
        if self._series_entries:
            self._schedule_series_scan()

    def open_series_episode_list(self):
        if (
            self.content_type != "series"
            or self._series_loading
            or not self._series_entries
        ):
            return
        series = self._series_entries[self._series_selected_index]
        episode_client = (
            self.client
            if _is_stalker_client(self.client)
            else content_client_for(self.account)
        )
        self._open_series_child(
            GTEpisodeListScreen,
            self.account,
            series,
            episode_client,
            favorite_store=self.favorite_store,
        )

    def open_series_continue(self):
        if self.content_type != "series":
            return
        if (
            self._loading
            or self._series_loading
            or self._series_pending_category_id is not None
            or (
                self._entries
                and not self._selected_series_category_is_loaded()
            )
        ):
            self["message"].setText(_("Please wait"))
            return
        continue_client = (
            self.client
            if _is_stalker_client(self.client)
            else content_client_for(self.account)
        )
        self._open_series_child(
            GTContinueWatchingScreen,
            self.account,
            continue_client,
            grid_subtitle=N_("Series"),
            content_type_filter="series",
        )

    def open_series_search(self):
        if self.content_type != "series" or self._closed:
            return
        if (
            self._series_loading
            or self._series_more_loading
            or self._series_has_more
            or self._series_pending_category_id is not None
            or (
                self._entries
                and not self._selected_series_category_is_loaded()
            )
        ):
            self["message"].setText(_("Please wait"))
            self._schedule_series_scan(delay_ms=1)
            return
        if not self._series_entries:
            self["message"].setText(_("No content found."))
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(
                _("The virtual keyboard is unavailable on this image.")
            )
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["message"].setText(
                _("Could not open the virtual keyboard.")
            )
            return
        opener(
            self._series_search_entered,
            VirtualKeyBoard,
            title=_("Search"),
            text=self._series_search_query,
        )

    def _series_search_entered(self, value):
        if self._closed or value is None:
            return
        self._series_search_query = " ".join(str(value or "").split())[:120]
        query = _movie_search_text(self._series_search_query)
        if not query:
            return
        terms = query.split()
        matches = []
        for index, item in enumerate(self._series_entries):
            if all(
                term in _movie_search_text(getattr(item, "name", ""))
                for term in terms
            ):
                matches.append((item.name, index))
                if len(matches) >= SERIES_SEARCH_RESULT_LIMIT:
                    break
        if not matches:
            self["message"].setText(_("No content found."))
            return
        if len(matches) == 1:
            self._select_series(matches[0][1])
            return
        try:
            from Screens.ChoiceBox import ChoiceBox
        except ImportError:
            self._select_series(matches[0][1])
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self._select_series(matches[0][1])
            return
        opener(
            self._series_search_choice,
            ChoiceBox,
            title=_("Search"),
            list=matches,
        )

    def _series_search_choice(self, choice):
        if not choice:
            return
        try:
            index = choice[1]
        except (IndexError, KeyError, TypeError):
            return
        try:
            index = int(index)
        except (TypeError, ValueError, OverflowError):
            return
        if 0 <= index < len(self._series_entries):
            self._select_series(index)

    def _stop_series(self):
        reset_cinematic = getattr(
            self,
            "_reset_cinematic_backdrop",
            None,
        )
        if callable(reset_cinematic):
            reset_cinematic()
        try:
            plot_widget = self["series_plot"]
        except (KeyError, TypeError):
            plot_widget = None
        stop_scroll = getattr(plot_widget, "stop_scroll", None)
        if callable(stop_scroll):
            stop_scroll()
        self._series_generation += 1
        self._series_catalog_generation += 1
        self._series_poster_generation = getattr(
            self, "_series_poster_generation", self._series_generation
        ) + 1
        self._series_child_open = False
        self._series_more_loading = False
        self._series_more_result = None
        self._series_pending_advance = False
        self._series_pending_selection_index = None
        self._series_pending_clamp_to_end = False
        for token in (
            self._series_load_token,
            self._series_more_token,
            self._series_detail_token,
            self._series_poster_token,
        ):
            _cancel_worker_token(token)
        self._series_load_token = None
        self._series_more_token = None
        self._series_detail_token = None
        self._series_poster_token = None
        self._series_poster_state = None
        self._series_deferred_poster_jobs = None
        series_cache_lock = getattr(self, "_series_info_cache_lock", None)
        if series_cache_lock is None:
            self._series_info_cache.clear()
        else:
            with series_cache_lock:
                self._series_info_cache.clear()
        for timer in (
            self._series_timer,
            self._series_scan_timer,
            self._series_category_timer,
            self._series_detail_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass

    def _render_weather_waiting(self, settings=None):
        settings = settings or self._weather_settings_loader()
        self._dynamic_weather_city_text = "●  {}".format(
            localized_upper(settings.city)
        )
        ellipsize_dynamic_text(
            self["weather_city"],
            self._dynamic_weather_city_text,
            fallback_chars=20,
        )
        self["weather_current_icon"].hide()
        self["weather_metrics"].setText("")
        self._clear_weather_days()
        if not settings.enabled:
            self["weather_temp"].setText("--")
            self["weather_desc"].setText(_("Weather is disabled"))
            self["weather_updated"].setText(_("You can enable it in Settings."))
            return
        self["weather_temp"].setText("--°")
        self["weather_desc"].setText(_("Loading forecast"))
        self["weather_updated"].setText("Open-Meteo")

    def _clear_weather_days(self):
        for index in range(5):
            self["weather_day_{}".format(index)].setText("--")
            self["weather_day_high_{}".format(index)].setText("--°")
            self["weather_day_low_{}".format(index)].setText("--°")
            self["weather_day_icon_{}".format(index)].hide()

    @staticmethod
    def _weather_icon_path(code):
        return os.path.join(
            WEATHER_ICON_DIR,
            "{}.png".format(weather_icon_name(code)),
        )

    def _apply_weather_icon(self, widget_name, code):
        widget = self[widget_name]
        path = self._weather_icon_path(code)
        if not os.path.isfile(path):
            widget.hide()
            return
        try:
            widget.instance.setPixmapFromFile(path)
            widget.show()
        except Exception:
            widget.hide()

    def start_weather(self, force=False):
        if (
            self.content_type != "live"
            or self._weather_loading
            or self._closed
            or self._weather_suspended
            or (self._weather_started and not force)
        ):
            return
        settings = self._weather_settings_loader()
        self._render_weather_waiting(settings)
        if not settings.enabled:
            self._weather_started = True
            return
        _cancel_worker_token(self._weather_token)
        self._weather_generation += 1
        generation = self._weather_generation
        token = _WorkerToken(WORKER_WEATHER_TIMEOUT_SECONDS)
        self._weather_token = token
        self._weather_settings = settings
        self._weather_forecast = None
        self._weather_error = ""
        self._weather_ready = False
        self._weather_loading = True
        self._weather_started = True
        self._weather_force = bool(force)
        worker = threading.Thread(
            target=self._run_weather,
            args=(generation, token),
        )
        worker.daemon = True
        worker.start()
        self._weather_timer.start(150, True)

    def _run_weather(self, generation=None, token=None):
        if generation is None:
            generation = self._weather_generation
        forecast = None
        error_text = ""
        try:
            if token is not None:
                token.check()
            forecast = self._weather_service.get(
                self._weather_settings,
                force=self._weather_force,
                cancel_event=(token.cancel_event if token is not None else None),
                deadline=(token.deadline if token is not None else None),
            )
            if token is not None:
                token.check()
        except _WorkerCancelled:
            return
        except WeatherError as caught:
            error_text = str(caught)
        except Exception:
            error_text = "Could not load the weather forecast"
        if (
            self._closed
            or self._weather_suspended
            or generation != self._weather_generation
            or (token is not None and not token.active())
        ):
            return
        self._weather_forecast = forecast
        self._weather_error = error_text
        self._weather_ready = True

    def _poll_weather(self):
        if self._closed:
            return
        if not self._weather_ready:
            if self._weather_token is not None and self._weather_token.expired():
                self._weather_token.cancel()
                self._weather_loading = False
                self._weather_error = N_("Weather request timed out")
                self["weather_desc"].setText(_("Weather data unavailable."))
                ellipsize_dynamic_text(
                    self["weather_updated"],
                    _(self._weather_error),
                    fallback_chars=50,
                )
                return
            self._weather_timer.start(150, True)
            return
        self._weather_loading = False
        self._weather_token = None
        if self._weather_forecast is None:
            self["weather_desc"].setText(_("Weather data unavailable."))
            ellipsize_dynamic_text(
                self["weather_updated"],
                _(self._weather_error),
                fallback_chars=50,
            )
            return
        forecast = self._weather_forecast
        degree = forecast.degree
        self._dynamic_weather_city_text = "●  {}".format(
            localized_upper(forecast.city)
        )
        ellipsize_dynamic_text(
            self["weather_city"],
            self._dynamic_weather_city_text,
            fallback_chars=20,
        )
        self._apply_weather_icon("weather_current_icon", forecast.current_code)
        self["weather_temp"].setText(
            "{:.0f}{}".format(forecast.current_temperature, degree)
        )
        self["weather_desc"].setText(
            localized_upper(weather_description(forecast.current_code))
        )
        self["weather_metrics"].setText(
            _("Humidity {}% • Wind {:.0f} km/h").format(
                forecast.current_humidity,
                forecast.current_wind_speed,
            )
        )
        suffix = _("  •  Cached") if forecast.stale else ""
        self["weather_updated"].setText(
            _("Updated: {}{}").format(forecast.updated_at, suffix)
        )
        for index in range(5):
            if index >= len(forecast.days):
                self["weather_day_{}".format(index)].setText("--")
                self["weather_day_high_{}".format(index)].setText("--°")
                self["weather_day_low_{}".format(index)].setText("--°")
                self["weather_day_icon_{}".format(index)].hide()
                continue
            day = forecast.days[index]
            label = _("Today") if index == 0 else day_name(day.date)
            self["weather_day_{}".format(index)].setText(localized_upper(label))
            self["weather_day_high_{}".format(index)].setText(
                "{:.0f}°".format(day.maximum)
            )
            self["weather_day_low_{}".format(index)].setText(
                "{:.0f}°".format(day.minimum)
            )
            self._apply_weather_icon(
                "weather_day_icon_{}".format(index),
                day.code,
            )

    def open_weather_settings(self):
        from .main import GTWeatherSettingsScreen

        if hasattr(self.session, "openWithCallback"):
            self.session.openWithCallback(
                self._weather_settings_closed,
                GTWeatherSettingsScreen,
            )
        else:
            self.session.open(GTWeatherSettingsScreen)

    def _suspend_for_playback(self):
        """Silence optional parent work while a VOD player owns the screen."""
        if self.content_type == "movie":
            reset_cinematic = getattr(
                self,
                "_reset_cinematic_backdrop",
                None,
            )
            if callable(reset_cinematic):
                reset_cinematic()
            for token in (
                self._movie_more_token,
                self._movie_detail_token,
                self._movie_poster_token,
            ):
                _cancel_worker_token(token)
            self._movie_more_token = None
            self._movie_detail_token = None
            self._movie_poster_token = None
            self._movie_more_loading = False
            self._movie_detail_pending = False
            self._movie_poster_pending = False
            self._movie_poster_state = None
            self._movie_deferred_poster_jobs = None
            for timer in (self._movie_timer, self._movie_detail_timer):
                try:
                    timer.stop()
                except Exception:
                    pass
        elif self.content_type == "series":
            reset_cinematic = getattr(
                self,
                "_reset_cinematic_backdrop",
                None,
            )
            if callable(reset_cinematic):
                reset_cinematic()
            for token in (
                self._series_more_token,
                self._series_detail_token,
                self._series_poster_token,
            ):
                _cancel_worker_token(token)
            self._series_more_token = None
            self._series_detail_token = None
            self._series_poster_token = None
            self._series_more_loading = False
            self._series_detail_pending = False
            self._series_poster_pending = False
            self._series_poster_state = None
            self._series_deferred_poster_jobs = None
            for timer in (
                self._series_timer,
                self._series_scan_timer,
                self._series_category_timer,
                self._series_detail_timer,
            ):
                try:
                    timer.stop()
                except Exception:
                    pass

    def _weather_settings_closed(self, *args):
        self.start_weather(force=True)

    def _stop_weather(self, suspend=False):
        self._weather_generation += 1
        _cancel_worker_token(self._weather_token)
        self._weather_token = None
        self._weather_loading = False
        self._weather_ready = bool(self._weather_forecast is not None)
        self._weather_suspended = bool(suspend)
        if self._weather_forecast is None:
            self._weather_started = False
        try:
            self._weather_timer.stop()
        except Exception:
            pass

    def _weather_child_closed(self, *args):
        self._weather_suspended = False
        if self._weather_forecast is None:
            self.start_weather()

    def open_selected(self):
        if not self._entries or self._loading:
            return
        if self.content_type == "movie":
            if self._movie_search_child_open:
                self["message"].setText(_("Please wait"))
                return
            if self._movie_focus == "categories":
                category = self._entries[self.selected_index]
                if not str(
                    getattr(category, "category_id", "") or ""
                ).strip():
                    self.open_movie_search()
                    return
                if self._movie_entries:
                    self._movie_focus = "posters"
                    self._schedule_movie_detail()
                    self._render_movie_posters()
                    self._refresh_movie_categories()
                else:
                    self._start_movie_load()
                return
            if self._movie_entries and not self._movie_loading:
                open_extplayer(
                    self.session,
                    self.client,
                    self._movie_entries[self._movie_selected_index],
                    self._movie_entries,
                    self._movie_selected_index,
                    owner=self,
                )
            return
        if self.content_type == "series":
            if self._series_focus == "categories":
                category = self._entries[self.selected_index]
                loaded = (
                    str(self._series_loaded_category_id or "")
                    == str(getattr(category, "category_id", "") or "")
                )
                if loaded and self._series_entries:
                    self._series_focus = "series"
                    self._schedule_series_detail()
                    self._refresh_series_categories()
                    self._render_series_tiles()
                else:
                    self._start_series_load(category)
                return
            self.open_series_episode_list()
            return
        arguments = (
            GTStreamListScreen,
            self.account,
            self.content_type,
            self._entries[self.selected_index],
            self.client,
        )
        snapshot = self._weather_forecast
        self._stop_weather(suspend=True)
        opener = getattr(self.session, "openWithCallback", None)
        if callable(opener):
            opener(
                self._weather_child_closed,
                *arguments,
                weather_forecast=snapshot,
                favorite_store=self.favorite_store,
                hidden_category_ids=self._hidden_category_ids,
            )
            return
        self.session.open(
            *arguments,
            weather_forecast=snapshot,
            favorite_store=self.favorite_store,
            hidden_category_ids=self._hidden_category_ids,
        )


class _GTStreamListBase(GTAsyncListScreen):
    """Shared catalogue primitives; live browsing never owns a decoder."""

    def refresh_streams(self):
        invalidator = getattr(self.client, "invalidate_cache", None)
        if invalidator is not None:
            try:
                invalidator(self.content_type, self.category.category_id)
            except Exception:
                pass
        self.start_load()

    def start_load(self):
        if self.content_type == "live" and not self._loading:
            _cancel_worker_token(
                getattr(self, "_live_more_token", None)
            )
            self._live_catalog_generation += 1
            self._live_initial_applied = False
            self._live_has_more = False
            self._live_more_loading = False
            self._live_more_token = None
            self._live_more_results = {}
            self._live_pending_target_index = None
            self._live_empty_more_batches = 0
        GTAsyncListScreen.start_load(self)

    def load_items(self):
        entries = self.client.load_items(
            self.content_type,
            self.category.category_id,
        )
        if self.content_type == "live":
            checker = getattr(self.client, "has_more_items", None)
            try:
                self._live_has_more = bool(
                    checker is not None
                    and checker("live", self.category.category_id)
                )
            except Exception:
                self._live_has_more = False
        category_id = str(getattr(self.category, "category_id", "") or "").strip()
        if not category_id or category_filter_active(
            getattr(self, "_hidden_category_ids", ())
        ):
            return filter_hidden_category_items(
                entries,
                getattr(self, "_hidden_category_ids", ()),
                category_id,
            )
        return entries

    def _poll_result(self):
        if self.content_type != "live":
            GTAsyncListScreen._poll_result(self)
            return
        if self._closed:
            return
        if not self._ready:
            if self._load_token is not None and self._load_token.expired():
                self._load_token.cancel()
                self._loading = False
                self._error = N_("Request timed out")
                self._ready = True
            else:
                self._timer.start(150, True)
                return
        if not self._live_initial_applied:
            self._live_initial_applied = True
            self._loading = False
            self._load_token = None
            self._refresh()
            return
        if not self._live_more_loading:
            return
        generation = self._live_catalog_generation
        token = getattr(self, "_live_more_token", None)
        with self._live_more_worker_events_lock:
            result = self._live_more_results.pop(
                (generation, token),
                None,
            )
        if result is None:
            if token is None or token.expired():
                if token is not None:
                    _cancel_worker_token(token)
                    with self._live_more_worker_events_lock:
                        self._live_more_worker_tokens.discard(token)
                if getattr(self, "_live_more_token", None) is token:
                    self._live_more_token = None
                self._live_more_loading = False
                self._live_pending_target_index = None
                return
            self._timer.start(100, True)
            return
        entries, error, has_more = result
        self._live_more_loading = False
        self._live_more_token = None
        self._live_has_more = bool(has_more)
        if error:
            self._live_has_more = False
            if not self._entries:
                self._error = error
            else:
                log_event("content", "Could not load more live channels")
        else:
            existing = {
                str(getattr(item, "stream_id", "") or "")
                for item in self._entries
            }
            appended = []
            for item in entries:
                stream_id = str(getattr(item, "stream_id", "") or "")
                if not stream_id or stream_id in existing:
                    continue
                existing.add(stream_id)
                appended.append(item)
            self._entries.extend(appended)
            if appended:
                self._live_empty_more_batches = 0
            else:
                self._live_empty_more_batches += 1

        target = self._live_pending_target_index
        if target is not None and self._entries:
            if target < len(self._entries):
                self.selected_index = target
                self._live_pending_target_index = None
            elif not self._live_has_more:
                self.selected_index = target % len(self._entries)
                self._live_pending_target_index = None
        self._refresh_live_streams()
        if (
            self._live_pending_target_index is not None
            and self._live_has_more
        ):
            self._start_live_more(force=True)

    def _run_live_more(self, generation, token=None):
        entries = []
        error = ""
        has_more = False
        try:
            loader = getattr(self.client, "load_more_items", None)
            if loader is not None:
                _bounded_client_call(
                    self.client,
                    token,
                    lambda: loader("live", self.category.category_id),
                )
                # Return the accumulated state so a list worker and an open
                # fullscreen player cannot skip each other's completed batch.
                entries = list(
                    _bounded_client_call(
                        self.client,
                        token,
                        lambda: self.client.load_items(
                            "live",
                            self.category.category_id,
                        ),
                    )
                    or []
                )
                if not str(
                    getattr(self.category, "category_id", "") or ""
                ).strip() or category_filter_active(
                    getattr(self, "_hidden_category_ids", ())
                ):
                    entries = filter_hidden_category_items(
                        entries,
                        getattr(self, "_hidden_category_ids", ()),
                        getattr(self.category, "category_id", ""),
                    )
            checker = getattr(self.client, "has_more_items", None)
            has_more = bool(
                checker is not None
                and checker("live", self.category.category_id)
            )
        except _WorkerCancelled:
            return
        except ContentError as caught:
            error = str(caught)
        except Exception:
            error = N_("Could not load content")
        if token is not None:
            try:
                token.check()
            except _WorkerCancelled:
                return
        lock = getattr(self, "_live_more_worker_events_lock", None)
        if lock is None:
            self._live_more_results[(generation, token)] = (
                entries,
                error,
                has_more,
            )
            return
        with lock:
            if (
                token is not None
                and (
                    not token.active()
                    or getattr(self, "_live_more_token", None) is not token
                )
            ):
                return
            self._live_more_results[(generation, token)] = (
                entries,
                error,
                has_more,
            )

    def _maybe_load_more_live(self):
        if self._fullscreen_active:
            return
        if not self._entries:
            self._start_live_more()
            return
        remaining = len(self._entries) - self.selected_index - 1
        threshold = (
            2
            if str(getattr(self.client, "source_type", "") or "")
            == "stalker"
            else max(6, int(self.page_size / 2))
        )
        if remaining <= threshold:
            self._start_live_more()

    @staticmethod
    def _progress_details(event):
        start = int(getattr(event, "start_timestamp", 0) or 0)
        end = int(getattr(event, "end_timestamp", 0) or 0)
        if not start or end <= start:
            return 0.0, ""
        now = int(time.time())
        ratio = max(0.0, min(1.0, float(now - start) / (end - start)))
        remaining_minutes = max(0, int((end - now + 59) / 60))
        return ratio, _("%{}  •  {} MIN REMAINING").format(
            int(round(ratio * 100)),
            remaining_minutes,
        )


class _LiveStartupRecoverySupport(object):
    """Compatibility hooks for the retired automatic live recovery.

    R93 deliberately leaves Stalker/MAC playback passive after every start,
    preview hand-off, channel change and engine change.  A live stream is
    renewed only through :meth:`GTExternalPlayerScreen.restart_live_stream`,
    which is bound to the physical 0 key.  Keeping these no-op hooks avoids a
    risky lifecycle rewrite in the list/player hand-off code while making it
    impossible to create a timer, worker, provider request or decoder replay.
    """

    _startup_preview = False

    def _arm_live_startup(self):
        self._live_startup_recovery = None

    def _cancel_live_startup(self, restore_released=False):
        del restore_released
        self._live_startup_recovery = None
        return True

    def _live_startup_status(self, status):
        del status

    def _live_startup_recovered(self, reference):
        del reference


class GTStreamListScreen(
    _LiveStartupRecoverySupport,
    _FavoriteSupport,
    _GTStreamListBase,
):
    """One live-list implementation for Xtream, Stalker and M3U sources."""

    _startup_preview = True

    def __init__(
        self,
        session,
        account,
        content_type,
        category,
        client=None,
        weather_forecast=None,
        favorite_store=None,
        hidden_category_ids=None,
    ):
        self.account = account
        self.category = category
        self.client = client or content_client_for(account)
        self.content_type = _validated_content_type(self.client, content_type)
        self._init_favorite_support(favorite_store)
        self._hidden_category_ids = hidden_category_match_ids(
            (),
            hidden_category_ids,
        )
        self._weather_forecast_snapshot = weather_forecast
        self._fullscreen_active = False
        self._active_live_service_type = None
        try:
            settings = load_player_settings()
            self._active_live_service_type = int(
                settings.service_type_for("live")
            )
        except Exception:
            self._active_live_service_type = 4097

        # Keep incremental provider pagination without taking decoder
        # ownership or starting row-artwork workers.
        self._live_catalog_generation = 0
        self._live_initial_applied = False
        self._live_has_more = False
        self._live_more_loading = False
        self._live_more_token = None
        self._live_more_results = {}
        self._live_pending_target_index = None
        self._live_empty_more_batches = 0
        self._live_render_page_key = None
        self._live_render_selected_row = -1
        self._live_active_picon_signature = None
        self._live_active_picon_generation = 0
        self._live_active_picon_widget_owners = {}
        self._live_active_picon_fetch_generation = 0
        self._live_active_picon_fetch_item = None
        self._live_active_picon_fetch_key = ""
        self._live_active_picon_fetch_deadline = 0.0
        self._live_active_picon_fetch_state = None
        self._live_active_picon_reload_pending = False

        self._preview_item = None
        self._preview_epg_events = []
        self._preview_epg_loaded_at = 0.0
        self._detail_picon_path = ""
        self._detail_picon_loaded_at = 0.0
        self._detail_selection_key = None
        self._detail_page_key = None
        self._detail_generation = 0
        self._detail_deadline = 0.0
        self._detail_results = {}
        self._detail_results_lock = threading.Lock()
        self._detail_store = {}
        self._detail_store_lock = threading.Lock()
        self._detail_store_epoch = 0
        self._detail_foreground_state = None
        self._detail_prefetch_state = None
        self._detail_prefetch_deadline = 0.0
        self._live_more_worker_tokens = set()
        self._live_more_worker_events_lock = threading.Lock()

        # The list owns decoder 0 only after an explicit OK press has produced
        # a bounded playback link. Highlight movement alone never resolves a
        # link or zaps. The exact reference started here is handed to
        # fullscreen without a second provider request or playService() call.
        self._pre_list_reference = None
        self._pre_list_reference_captured = False
        self._pre_list_restored = False
        self._playing_preview_item = None
        self._playing_preview_key = ""
        self._playing_preview_index = -1
        self._playing_preview_reference = None
        self._preview_service_active = False
        self._preview_service_started_at = 0.0
        self._preview_target_item = None
        self._preview_target_key = ""
        self._preview_target_index = -1
        self._preview_generation = 0
        self._preview_link_lock = threading.RLock()
        self._preview_link_pending = False
        self._preview_link_result = None
        self._preview_link_deadline = 0.0
        self._preview_link_token = None
        self._preview_fullscreen_requested = False
        self._preview_video_attempts = 0
        self._preview_video_geometry = None
        self._preview_fallback_visible = True
        self._preview_auto_suspended_key = ""
        self._fullscreen_link_snapshot = None

        self._detail_dwell_timer = eTimer()
        self._detail_poll_timer = eTimer()
        self._detail_prefetch_timer = eTimer()
        self._detail_progress_timer = eTimer()
        self._epg_clock_timer = eTimer()
        self._preview_dwell_timer = eTimer()
        self._preview_link_timer = eTimer()
        self._preview_video_timer = eTimer()
        self._live_active_picon_timer = eTimer()
        self._live_active_picon_poll_timer = eTimer()
        _connect_timer(self._detail_dwell_timer, self._detail_dwell_fired)
        _connect_timer(self._detail_poll_timer, self._detail_poll)
        _connect_timer(self._detail_prefetch_timer, self._detail_prefetch_fired)
        _connect_timer(self._detail_progress_timer, self._detail_progress_tick)
        _connect_timer(self._epg_clock_timer, self._epg_clock_tick)
        _connect_timer(self._preview_dwell_timer, self._preview_dwell_fired)
        _connect_timer(self._preview_link_timer, self._preview_link_tick)
        _connect_timer(self._preview_video_timer, self._preview_video_tick)
        _connect_timer(
            self._live_active_picon_timer,
            self._live_active_picon_fired,
        )
        _connect_timer(
            self._live_active_picon_poll_timer,
            self._live_active_picon_poll,
        )

        self._footer_layout_items = (
            M3U_LIVE_STREAM_FOOTER_ITEMS
            if _client_supports(self.client, "search")
            and _is_m3u_client(self.client)
            else LIVE_STREAM_FOOTER_ITEMS
        )
        self.skin = _live_stream_skin()
        self.page_size = LIVE_STREAM_PAGE_SIZE
        width, height, px = _scale()
        self._ribbon_now_preferred_font = font_px(
            px,
            21,
            role="title",
        )
        self._ribbon_now_min_font = font_px(
            px,
            21,
            role="title",
            value="standard",
        )
        self._ribbon_next_preferred_font = font_px(
            px,
            20,
            role="title",
        )
        self._ribbon_next_min_font = font_px(
            px,
            20,
            role="title",
            value="standard",
        )
        self._list_scroll_geometry = (
            px(64 + 865 - 18), px(128), px(8), px(737)
        )
        title = CONTENT_LABELS[self.content_type][0]
        GTAsyncListScreen.__init__(
            self,
            session,
            "GTStreamListScreen",
            title,
            "{}  >  {}".format(account.display_name, category.name),
        )
        self._capture_pre_list_service()
        self._setup_live_widgets(px)
        self["actions"] = ActionMap(
            [
                "OkCancelActions",
                "DirectionActions",
                "ColorActions",
                "MoviePlayerActions",
            ],
            {
                "ok": self.open_selected,
                "cancel": self.close_list,
                "stop": self.close_list,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "green": self.refresh_streams,
                "red": self.toggle_selected_favorite,
                "blue": self.open_channel_search,
            },
            -1,
        )
        if hasattr(self, "onShown"):
            self.onShown.append(self.start_epg_clock)
            self.onShown.append(self._favorite_stream_list_shown)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._preview_layout_ready)

    def _setup_live_widgets(self, px):
        self["left_panel"] = Label("")
        self["right_panel"] = Label("")
        for panel in ("left", "right"):
            for color in ("cyan", "magenta"):
                self["{}_panel_accent_{}".format(panel, color)] = Label("")
        self["list_caption"] = Label(_("CHANNEL LIST"))
        self["list_caption_accent"] = Label("")
        for index in range(LIVE_STREAM_PAGE_SIZE):
            self["stream_row_bg_{}".format(index)] = Label("")
            self["stream_picon_{}".format(index)] = Pixmap()
            self["stream_picon_{}".format(index)].hide()
            self["stream_number_{}".format(index)] = Label("")
            self["stream_name_{}".format(index)] = Label("")
            self["stream_favorite_{}".format(index)] = Pixmap()
            self["stream_favorite_{}".format(index)].hide()
            for edge in ("fill", "top", "bottom", "left", "right"):
                self["stream_focus_{}_{}".format(index, edge)] = Label("")

        self["epg_ribbon"] = Label("")
        self["ribbon_now_caption"] = Label(_("NOW"))
        self["ribbon_channel"] = Label(_("SELECT A CHANNEL"))
        self["ribbon_now_title"] = Label(_("Waiting for programme information"))
        self["ribbon_now_time"] = Label("--:--")
        self["ribbon_divider"] = Label("")
        self["ribbon_next_caption"] = Label(_("NEXT"))
        self["ribbon_clock"] = Label("")
        self["ribbon_next_title"] = Label("")
        self["ribbon_next_time"] = Label("")
        self["preview_frame"] = Label("")
        if VideoWindow is not None:
            self["preview_video"] = VideoWindow(
                decoder=0,
                fb_width=_desktop_size()[0],
                fb_height=_desktop_size()[1],
            )
        else:
            self["preview_video"] = Label("")
        self["preview_mask"] = Label("")
        self["preview_hint"] = Label(_("SELECT A CHANNEL"))

        self["epg_panel"] = Label("")
        self["epg_title"] = Label(_("NEXT"))
        for row in ("now", "next", "third", "fourth"):
            self["epg_{}_time".format(row)] = Label("")
            self["epg_{}_title".format(row)] = Label("")
            self["epg_{}_status".format(row)] = Label("")
        self["epg_progress_bg"] = Label("")
        self["epg_progress_fill"] = Label("")
        self["epg_progress"] = Label("")
        self["summary_caption"] = Label(_("PROGRAMME SUMMARY"))
        self["summary"] = Label(_("Waiting for programme information"))
        self["summary_scroll"] = Label("")
        output_format = (
            "M3U"
            if _is_m3u_client(self.client)
            else str(
                getattr(self.account, "output_format", "ts") or "ts"
            ).upper()
        )
        self["output_badge"] = Label(output_format)
        self["engine_badge"] = Label(
            service_engine_label(self._active_live_service_type)
        )
        self._progress_width = max(1, px(805))
        self._progress_height = max(1, px(9))
        self._clear_epg_progress()
        self._clear_live_active_picon()
        self._show_preview_fallback(_("SELECT A CHANNEL"))
        self._update_epg_clock()

    @staticmethod
    def _same_service_reference(left, right):
        if left is None or right is None:
            return False
        if left is right:
            return True
        left_method = getattr(left, "toString", None)
        right_method = getattr(right, "toString", None)
        if left_method is not None and right_method is not None:
            try:
                return left_method() == right_method()
            except Exception:
                pass
        return False

    def _current_navigation_reference(self):
        navigation = getattr(self.session, "nav", None)
        getter = getattr(
            navigation,
            "getCurrentlyPlayingServiceReference",
            None,
        )
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def _capture_pre_list_service(self):
        if self._pre_list_reference_captured:
            return
        self._pre_list_reference = self._current_navigation_reference()
        self._pre_list_reference_captured = True

    def _preview_layout_ready(self):
        self._capture_preview_video_geometry()
        if self._preview_service_active:
            self._resume_preview_pig()
        else:
            self._show_preview_fallback(_("SELECT A CHANNEL"))

    def _capture_preview_video_geometry(self):
        if VideoWindow is None:
            return
        try:
            instance = self["preview_video"].instance
            position = instance.position()
            size = instance.size()
            width = int(size.width())
            height = int(size.height())
            if width > 0 and height > 0:
                self._preview_video_geometry = (
                    int(position.x()),
                    int(position.y()),
                    width,
                    height,
                )
        except Exception:
            pass

    def _show_preview_fallback(self, message=None):
        self._preview_fallback_visible = True
        try:
            self["preview_mask"].show()
            self["preview_hint"].setText(str(message or ""))
            self["preview_hint"].show()
        except Exception:
            pass

    def _hide_preview_fallback(self):
        self._preview_fallback_visible = False
        try:
            self["preview_mask"].hide()
            self["preview_hint"].hide()
        except Exception:
            pass

    def _reapply_preview_video_window(self):
        """Force decoder 0 back into this screen's PIG rectangle."""
        if VideoWindow is None:
            return
        try:
            video = self["preview_video"]
            instance = video.instance
            geometry = self._preview_video_geometry
            if geometry is None:
                self._capture_preview_video_geometry()
                geometry = self._preview_video_geometry
            if geometry is None:
                position = instance.position()
                size = instance.size()
                geometry = (
                    int(position.x()),
                    int(position.y()),
                    int(size.width()),
                    int(size.height()),
                )
            left, top, width, height = geometry
            instance.resize(eSize(width, height))
            # eVideoWidget ignores same-geometry updates on several images.
            instance.move(ePoint(left + 1, top))
            instance.move(ePoint(left, top))
        except Exception:
            try:
                video.hide()
                video.show()
            except Exception:
                pass

    def _preview_video_ready(self):
        navigation = getattr(self.session, "nav", None)
        getter = getattr(navigation, "getCurrentService", None)
        try:
            service = getter() if getter is not None else None
        except Exception:
            service = None
        try:
            info = service.info() if service is not None else None
        except Exception:
            info = None
        width = -1
        height = -1
        if info is not None:
            try:
                width = int(info.getInfo(iServiceInformation.sVideoWidth))
            except Exception:
                width = -1
            try:
                height = int(info.getInfo(iServiceInformation.sVideoHeight))
            except Exception:
                height = -1
        if width <= 0:
            width = _read_proc_number("/proc/stb/vmpeg/0/xres", 16)
        if height <= 0:
            height = _read_proc_number("/proc/stb/vmpeg/0/yres", 16)
        return width > 0 and height > 0

    def _resume_preview_pig(self):
        if not self._preview_service_active or self._fullscreen_active:
            return
        try:
            self["preview_video"].show()
        except Exception:
            pass
        self._preview_video_attempts = 0
        self._show_preview_fallback(_("Please wait"))
        self._reapply_preview_video_window()
        self._preview_video_timer.start(LIVE_PREVIEW_VIDEO_INITIAL_MS, True)

    def _suspend_preview_pig(self):
        try:
            self._preview_video_timer.stop()
        except Exception:
            pass
        if VideoWindow is not None:
            try:
                self._capture_preview_video_geometry()
                video = self["preview_video"]
                instance = video.instance
                restore = getattr(instance, "restoreFullsize", None)
                if callable(restore):
                    restore()
                desktop_width, desktop_height = _desktop_size()
                instance.resize(eSize(desktop_width, desktop_height))
                instance.move(ePoint(0, 0))
            except Exception:
                pass
        try:
            self["preview_video"].hide()
        except Exception:
            pass

    def _preview_video_tick(self):
        if (
            self._closed
            or self._fullscreen_active
            or not self._preview_service_active
        ):
            return
        if VideoWindow is None:
            self._show_preview_fallback(_("Could not open the stream."))
            return
        current = self._current_navigation_reference()
        active = self._same_service_reference(
            current,
            self._playing_preview_reference,
        )
        if active:
            self._reapply_preview_video_window()
            self._ensure_live_active_picon()
        if active and self._preview_video_ready():
            self._hide_preview_fallback()
            return
        self._preview_video_attempts += 1
        if self._preview_video_attempts == LIVE_PREVIEW_VIDEO_ERROR_CHECKS:
            self._show_preview_fallback(_("Could not open the stream."))
        if self._preview_video_attempts < LIVE_PREVIEW_VIDEO_MAX_CHECKS:
            self._preview_video_timer.start(LIVE_PREVIEW_VIDEO_RETRY_MS, True)
            return

    def _favorite_stream_list_shown(self):
        if self._favorite_screen_shown() and self._entries:
            self._live_render_page_key = None
            self._refresh_live_streams()

    def toggle_selected_favorite(self):
        if not self._entries or self._loading or self._fullscreen_active:
            return
        item = self._entries[self.selected_index]
        added = self._toggle_favorite_item(item)
        self._live_render_page_key = None
        self._refresh_live_streams()
        ellipsize_dynamic_text(
            self["message"],
            self._favorite_message(item, added),
            fallback_chars=60,
        )

    def open_channel_search(self):
        if (
            not _is_m3u_client(self.client)
            or not _client_supports(self.client, "search")
            or self._fullscreen_active
        ):
            return
        with self._preview_link_lock:
            self._preview_generation += 1
        self._cancel_preview_resolution(clear_target=True)
        self._suspend_preview_pig()
        self._cancel_selected_detail(clear_selection=False)
        try:
            self._epg_clock_timer.stop()
        except Exception:
            pass
        opener = getattr(self.session, "openWithCallback", None)
        if callable(opener):
            opener(
                self._m3u_search_closed,
                GTM3UChannelSearchScreen,
                self.account,
                self.client,
                self.favorite_store,
                self._hidden_category_ids,
            )
            return
        dialog = self.session.open(
            GTM3UChannelSearchScreen,
            self.account,
            self.client,
            self.favorite_store,
            self._hidden_category_ids,
        )
        callbacks = getattr(dialog, "onClose", None)
        if isinstance(callbacks, list):
            callbacks.append(self._m3u_search_closed)

    def _m3u_search_closed(self, *args):
        if self._closed:
            return
        self._reload_favorite_keys()
        self._live_render_page_key = None
        self._refresh_live_streams()
        self.start_epg_clock()

    def refresh_streams(self):
        if (
            self._hidden_category_ids
            and not str(
                getattr(self.category, "category_id", "") or ""
            ).strip()
            and not self._entries
            and self._live_has_more
            and self._live_empty_more_batches >= 3
            and _is_stalker_client(self.client)
        ):
            # The standard empty-state hint says GREEN retries.  Continue
            # from the current portal cursor instead of reloading the same
            # hidden prefix from page one.
            return self._start_live_more(force=True)
        self._live_active_picon_reload_pending = bool(
            getattr(self, "_preview_service_active", False)
        )
        self._cancel_live_active_picon_fetch(clear_target=True)
        self._clear_live_active_picon()
        self._cancel_selected_detail(clear_selection=True)
        with self._detail_store_lock:
            self._detail_store_epoch += 1
            self._detail_store.clear()
        state = _client_epg_state(self.client)
        if state is not None:
            with state["lock"]:
                state["epoch"] = int(state.get("epoch", 0) or 0) + 1
                state["cache"].clear()
                # Wake followers immediately. Leaders may finish their bounded
                # request, but the epoch check prevents stale cache writes.
                for pending in state["inflight"].values():
                    pending["event"].set()
                state["inflight"].clear()
        return _GTStreamListBase.refresh_streams(self)

    def start_load(self):
        if getattr(self, "_preview_service_active", False):
            self._live_active_picon_reload_pending = True
        with self._preview_link_lock:
            self._preview_generation += 1
        self._cancel_preview_resolution(clear_target=True)
        self._cancel_live_active_picon_fetch(clear_target=True)
        self._cancel_selected_detail(clear_selection=True)
        with self._live_more_worker_events_lock:
            tokens = tuple(self._live_more_worker_tokens)
        for token in tokens:
            _cancel_worker_token(token)
        return _GTStreamListBase.start_load(self)

    def _refresh(self):
        self._refresh_live_streams()
        if self._live_active_picon_reload_pending:
            self._live_active_picon_reload_pending = not bool(
                self._ensure_live_active_picon()
            )

    def _refresh_live_streams(self):
        if self._error:
            ellipsize_dynamic_text(
                self["message"],
                _(self._error),
                fallback_chars=80,
            )
            self._clear_live_stream_rows()
            hide_scrollbar(self, "list_scroll")
            return
        if not self._entries:
            self["message"].setText(_("No channels found; press GREEN to try again."))
            self._clear_live_stream_rows()
            hide_scrollbar(self, "list_scroll")
            self._maybe_load_more_live()
            return
        self.selected_index %= len(self._entries)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start : page_start + self.page_size]
        page_key = (
            page_start,
            tuple(
                (
                    self._selection_key(item),
                    str(getattr(item, "name", "") or ""),
                    self._is_favorite(item),
                )
                for item in page_items
            ),
        )
        selected_row = self.selected_index - page_start
        page_changed = page_key != self._live_render_page_key
        if page_changed:
            plain_lines = []
            for row_index in range(self.page_size):
                present = row_index < len(page_items)
                if present:
                    item = page_items[row_index]
                    absolute_index = page_start + row_index
                    self["stream_number_{}".format(row_index)].setText(
                        "{:04d}.".format(absolute_index + 1)
                    )
                    rendered_name = ellipsize_dynamic_text(
                        self["stream_name_{}".format(row_index)],
                        str(item.name or ""),
                        fallback_chars=38,
                    )
                    plain_lines.append(rendered_name)
                    if self._is_favorite(item):
                        self["stream_favorite_{}".format(row_index)].show()
                    else:
                        self["stream_favorite_{}".format(row_index)].hide()
                else:
                    self["stream_number_{}".format(row_index)].setText("")
                    self["stream_name_{}".format(row_index)].setText("")
                    self["stream_favorite_{}".format(row_index)].hide()
            self["items"].setText("\n".join(plain_lines))
        if page_changed or selected_row != self._live_render_selected_row:
            affected_rows = range(self.page_size) if page_changed else (
                self._live_render_selected_row,
                selected_row,
            )
            for row_index in affected_rows:
                if row_index < 0 or row_index >= self.page_size:
                    continue
                selected = row_index == selected_row
                for edge in ("fill", "top", "bottom", "left", "right"):
                    focus = self["stream_focus_{}_{}".format(row_index, edge)]
                    if selected:
                        focus.show()
                    else:
                        focus.hide()
        self._live_render_page_key = page_key
        self._live_render_selected_row = selected_row
        self._refresh_live_active_picon(
            page_start=page_start,
            page_items=page_items,
        )
        self["message"].setText(
            "{}  |  {}/{}".format(
                len(self._entries), self.selected_index + 1, len(self._entries)
            )
        )
        update_scrollbar(
            self, "list_scroll", len(self._entries), self.selected_index,
            self.page_size, self._list_scroll_geometry,
        )
        self._schedule_selected_detail()
        self._schedule_preview_for_selection()
        self._maybe_load_more_live()

    def _clear_live_stream_rows(self):
        self._clear_live_active_picon()
        self._live_render_page_key = None
        self._live_render_selected_row = -1
        for row_index in range(LIVE_STREAM_PAGE_SIZE):
            self["stream_number_{}".format(row_index)].setText("")
            self["stream_name_{}".format(row_index)].setText("")
            self["stream_picon_{}".format(row_index)].hide()
            self["stream_favorite_{}".format(row_index)].hide()
            for edge in ("fill", "top", "bottom", "left", "right"):
                self["stream_focus_{}_{}".format(row_index, edge)].hide()

    def _select(self, index):
        if self._loading or self._fullscreen_active:
            return
        if not self._entries:
            if index >= 0 and self._live_has_more:
                self._live_pending_target_index = 0
                self._start_live_more(force=True)
            return
        if index >= len(self._entries) and self._live_has_more:
            self._live_pending_target_index = max(
                int(index), int(self._live_pending_target_index or 0)
            )
            self._start_live_more(force=True)
            return
        GTAsyncListScreen._select(self, index)

    def _cancel_preview_resolution(self, clear_target=False):
        for timer in (self._preview_dwell_timer, self._preview_link_timer):
            try:
                timer.stop()
            except Exception:
                pass
        with self._preview_link_lock:
            token = self._preview_link_token
            self._preview_link_token = None
            self._preview_link_pending = False
            self._preview_link_result = None
            self._preview_link_deadline = 0.0
            self._preview_fullscreen_requested = False
            if clear_target:
                self._preview_target_item = None
                self._preview_target_key = ""
                self._preview_target_index = -1
        _cancel_worker_token(token)

    def _schedule_preview_for_selection(self):
        """Keep explicit OK work aligned; highlight movement never tunes."""
        if self._closed or self._fullscreen_active or not self._entries:
            return
        try:
            self._preview_dwell_timer.stop()
        except Exception:
            pass
        item = self._entries[self.selected_index]
        key = self._selection_key(item)
        if not key:
            self._cancel_preview_resolution(clear_target=True)
            self._show_preview_fallback(_("SELECT A CHANNEL"))
            return

        with self._preview_link_lock:
            pending = bool(self._preview_link_pending)
            pending_key = str(self._preview_target_key or "")
        cancelled_pending = False
        if pending and key == pending_key:
            # List redraws may occur while the user's OK request is resolving.
            # Preserve that exact request, including the direct-fullscreen
            # first-OK fallback on PIG-less images, rather than minting
            # another provider link.
            self._show_preview_fallback(_("Please wait"))
            return
        if pending:
            # Moving away from a channel revokes only its unfinished explicit
            # OK request. A late worker result cannot replace the newly
            # highlighted row.
            with self._preview_link_lock:
                self._preview_generation += 1
            self._cancel_preview_resolution(clear_target=True)
            cancelled_pending = True

        current = self._current_navigation_reference()
        if (
            self._preview_service_active
            and self._same_service_reference(
                current,
                self._playing_preview_reference,
            )
        ):
            if key == self._playing_preview_key:
                self._playing_preview_item = item
                self._playing_preview_index = int(self.selected_index)
                self._resume_preview_pig()
            elif cancelled_pending:
                # The revoked request left its wait mask over the still-active
                # mini TV. Reveal that unchanged service; do not resolve or
                # play the newly highlighted row.
                self._resume_preview_pig()
            # When another row is highlighted, keep the explicitly opened
            # channel in mini TV until OK is pressed on the new row.
            return
        self._show_preview_fallback(_("SELECT A CHANNEL"))

    def _preview_dwell_fired(self):
        """Legacy timer callback kept inert: explicit previews require OK."""
        return

    def _begin_preview_link(self, item, index, request_fullscreen=False):
        """Start one resolver with an immutable mini-TV/fullscreen intent."""
        key = self._selection_key(item)
        if not key:
            return
        try:
            timeout = max(3, min(15, int(getattr(self.client, "timeout", 12))))
        except (TypeError, ValueError, OverflowError):
            timeout = 12
        with self._preview_link_lock:
            if self._preview_link_pending and key == self._preview_target_key:
                # Repeated OK while the first request is still resolving is
                # idempotent. Fullscreen is available only after mini TV owns
                # the active reference, so a slow provider cannot skip the
                # visible first-OK stage.
                return
            if (
                callable(getattr(self, "_cancel_live_startup", None))
                and self._cancel_live_startup(restore_released=True) is False
            ):
                return
            old_token = self._preview_link_token
            self._preview_generation += 1
            generation = self._preview_generation
            token = _WorkerToken(
                min(WORKER_PLAYBACK_LINK_TIMEOUT_SECONDS, timeout + 1)
            )
            self._preview_target_item = item
            self._preview_target_key = key
            self._preview_target_index = int(index)
            self._preview_fullscreen_requested = bool(request_fullscreen)
            self._preview_link_pending = True
            self._preview_link_result = None
            self._preview_link_token = token
            self._preview_link_deadline = token.deadline
        _cancel_worker_token(old_token)
        for timer in (self._preview_dwell_timer, self._preview_link_timer):
            try:
                timer.stop()
            except Exception:
                pass
        worker = threading.Thread(
            target=self._resolve_preview_link,
            args=(generation, item, int(index), key, token),
        )
        worker.daemon = True
        worker.start()
        self._show_preview_fallback(_("Please wait"))
        self._preview_link_timer.start(LIVE_PREVIEW_LINK_POLL_MS, True)

    def _resolve_preview_link(
        self,
        generation,
        item,
        index,
        key,
        worker_token=None,
    ):
        # OK starts a newly selected channel.  It uses the ordinary playback
        # resolver; only the player's physical 0 action uses the dedicated
        # Stalker recovery/session refresh API.
        resolver = getattr(self.client, "playback_url", None)
        if not callable(resolver):
            resolver = getattr(self.client, "refresh_playback_url", None)
        try:
            url = (
                str(
                    _bounded_client_call(
                        self.client,
                        worker_token,
                        lambda: resolver(item),
                    )
                    or ""
                ).strip()
                if callable(resolver)
                else ""
            )
        except Exception:
            url = ""
        with self._preview_link_lock:
            if (
                self._closed
                or not self._preview_link_pending
                or generation != self._preview_generation
                or key != self._preview_target_key
                or worker_token is not self._preview_link_token
            ):
                return
            self._preview_link_result = (
                generation,
                item,
                int(index),
                key,
                url,
            )

    def _preview_link_tick(self):
        restart_poll = False
        failed = False
        consumed = None
        with self._preview_link_lock:
            if self._closed or not self._preview_link_pending:
                return
            result = self._preview_link_result
            if result is None:
                if time.monotonic() < self._preview_link_deadline:
                    restart_poll = True
                else:
                    failed = True
            else:
                generation, item, index, key, url = result
                result_matches = bool(
                    generation == self._preview_generation
                    and key == self._preview_target_key
                )
                if not result_matches:
                    # A stale mailbox value must never consume or cancel the
                    # current generation's token. Drop only that value and
                    # keep polling the authoritative request.
                    self._preview_link_result = None
                    if time.monotonic() < self._preview_link_deadline:
                        restart_poll = True
                    else:
                        failed = True
                else:
                    request_fullscreen = self._preview_fullscreen_requested
                    self._preview_link_result = None
                    self._preview_link_pending = False
                    self._preview_link_token = None
                    self._preview_link_deadline = 0.0
                    consumed = (
                        generation,
                        item,
                        int(index),
                        key,
                        url,
                        request_fullscreen,
                    )
        if restart_poll:
            self._preview_link_timer.start(LIVE_PREVIEW_LINK_POLL_MS, True)
            return
        if failed:
            self._preview_link_failed()
            return
        if consumed is None:
            return
        generation, item, index, key, url, request_fullscreen = consumed
        if not url:
            self._preview_link_failed()
            return
        if VideoWindow is None and not request_fullscreen:
            self._preview_link_failed()
            return
        try:
            reference = build_extplayer_reference(
                url,
                item.name,
                getattr(item, "content_type", "live"),
                service_type=self._active_live_service_type,
            )
        except Exception:
            self._preview_link_failed()
            return
        navigation = getattr(self.session, "nav", None)
        if navigation is None:
            self._preview_link_failed()
            return
        try:
            _play_service(navigation, reference)
        except Exception:
            self._preview_link_failed()
            return

        self._pre_list_restored = False
        self._preview_auto_suspended_key = ""
        self._playing_preview_item = item
        self._playing_preview_key = key
        self._playing_preview_index = int(index)
        self._playing_preview_reference = reference
        self._preview_service_active = True
        self._preview_service_started_at = time.monotonic()
        arm_startup = getattr(self, "_arm_live_startup", None)
        if callable(arm_startup):
            arm_startup()
        if not request_fullscreen:
            self._schedule_live_active_picon(item, key)
        with self._preview_link_lock:
            self._preview_fullscreen_requested = False
        _debug(
            "live preview started selected={} engine={} reason={}".format(
                key,
                reference_service_type(reference, 4097),
                "ok_fullscreen" if request_fullscreen else "ok_mini_tv",
            )
        )
        if request_fullscreen:
            self._open_preview_fullscreen(item, index, reference)
            return
        # Playback-link creation and playService() have completed. Only now
        # may the optional EPG worker enter a provider lane.
        self._schedule_selected_detail()
        self._resume_preview_pig()

    def _preview_link_failed(self):
        requested = self._preview_fullscreen_requested
        self._cancel_preview_resolution(clear_target=False)
        self._show_preview_fallback(_("Could not open the stream."))
        if requested:
            self["message"].setText(_("Could not open the stream."))

    def _start_live_more(self, force=False):
        """Start and track every catalogue worker across list refreshes."""
        local_hidden_scan = bool(
            self._hidden_category_ids
            and not str(
                getattr(self.category, "category_id", "") or ""
            ).strip()
            and str(
                getattr(self.client, "source_type", "xtream") or "xtream"
            ).lower()
            in ("xtream", "m3u")
        )
        if (
            self.content_type != "live"
            or self._closed
            or self._fullscreen_active
            or self._loading
            or self._live_more_loading
            or not self._live_has_more
            or (
                not force
                and self._live_empty_more_batches >= 3
                and not local_hidden_scan
            )
        ):
            return False
        self._live_more_loading = True
        generation = self._live_catalog_generation
        worker_token = _WorkerToken(WORKER_LIST_TIMEOUT_SECONDS)
        self._live_more_token = worker_token
        with self._live_more_worker_events_lock:
            self._live_more_worker_tokens.add(worker_token)
        worker = threading.Thread(
            target=self._run_live_more,
            args=(generation, worker_token),
        )
        worker.daemon = True
        worker.start()
        self._timer.start(100, True)
        return True

    def _run_live_more(
        self,
        generation,
        worker_token=None,
    ):
        try:
            return _GTStreamListBase._run_live_more(
                self,
                generation,
                worker_token,
            )
        finally:
            with self._live_more_worker_events_lock:
                self._live_more_worker_tokens.discard(worker_token)

    def _selection_key(self, item):
        return str(getattr(item, "stream_id", "") or "")

    def _shared_epg_cache_snapshot(self, stream_id):
        """Read the provider EPG cache without starting provider work."""
        state = _client_epg_state(self.client)
        if state is None:
            return []
        now = time.monotonic()
        with state["lock"]:
            cached = state["cache"].get(str(stream_id or ""))
            if cached is None:
                return []
            loaded_at, events = cached
            if (
                not events
                or now - float(loaded_at) >= LIVE_EPG_CACHE_TTL_SECONDS
            ):
                return []
            return list(events[:LIVE_EPG_FETCH_LIMIT])

    def _cached_epg_snapshot(self, stream_id):
        """Return current/next from RAM only; never navigate through HTTP."""
        events = self._shared_epg_cache_snapshot(stream_id)
        if events:
            return events
        if _is_m3u_client(self.client):
            # M3UContentClient.load_epg() is explicitly a RAM-snapshot lookup;
            # XML/file/network refresh belongs to the session scheduler.
            loader = getattr(self.client, "load_epg", None)
            if callable(loader):
                try:
                    return list(loader(stream_id, LIVE_EPG_FETCH_LIMIT) or [])
                except Exception:
                    return []
        if _is_stalker_client(self.client):
            # Stalker list payloads may already embed EPG rows. Parse only
            # those rows here; calling load_epg() could use the MAG API lane.
            try:
                meta = dict(
                    getattr(self.client, "_live_channel_meta", {}).get(
                        str(stream_id or ""),
                        {},
                    )
                )
                identities = tuple(meta.get("identities") or ())
                if not identities:
                    identities = (("stream_id", str(stream_id or "")),)
                parser = getattr(self.client, "_epg_events", None)
                if callable(parser):
                    for embedded in meta.get("embedded") or ():
                        for _unused_kind, identity in identities:
                            events = list(
                                parser(
                                    embedded,
                                    identity,
                                    LIVE_EPG_FETCH_LIMIT,
                                )
                                or []
                            )
                            if events:
                                return events
            except Exception:
                return []
        return []

    def _store_detail_component(
        self,
        stream_id,
        component,
        value,
        expected_epoch=None,
    ):
        """Commit a completed component independently of the current row."""
        stream_id = str(stream_id or "")
        if not stream_id:
            return False
        now_mono = time.monotonic()
        now_wall = time.time()
        with self._detail_store_lock:
            # The epoch comparison and mutation must share this lock. Without
            # it, GREEN can clear the store after a worker's comparison but
            # before its write, allowing stale pre-refresh data back in.
            if (
                expected_epoch is not None
                and expected_epoch != self._detail_store_epoch
            ):
                return False
            entry = self._detail_store.setdefault(stream_id, {})
            if component == "events":
                events = list(value or [])
                if events:
                    entry["events"] = events
                    entry["epg_cached_at"] = now_mono
                    entry["epg_captured_at"] = now_wall
                    entry.pop("epg_retry_after", None)
                else:
                    # Do not turn an empty/error response into the one-hour
                    # positive cache. A short cooldown is enough to stop a
                    # provider without EPG from occupying every selection.
                    entry.pop("events", None)
                    entry.pop("epg_cached_at", None)
                    entry.pop("epg_captured_at", None)
                    entry["epg_retry_after"] = (
                        now_mono + LIVE_DETAIL_EMPTY_RETRY_SECONDS
                    )
            elif component == "picon_path":
                path = str(value or "").strip()
                if path and _valid_live_picon_file(path):
                    entry["picon_path"] = path
                    entry["picon_cached_at"] = now_mono
                    entry["picon_captured_at"] = now_wall
                else:
                    entry.pop("picon_path", None)
                    entry.pop("picon_cached_at", None)
                    entry.pop("picon_captured_at", None)
            entry["updated_at"] = now_mono
            if (
                not entry.get("events")
                and not entry.get("picon_path")
                and not entry.get("epg_retry_after")
            ):
                self._detail_store.pop(stream_id, None)
            if len(self._detail_store) > LIVE_EPG_CACHE_MAX_ENTRIES:
                oldest = sorted(
                    self._detail_store,
                    key=lambda key: self._detail_store[key].get(
                        "updated_at", 0.0
                    ),
                )
                for key in oldest[
                    : len(self._detail_store) - LIVE_EPG_CACHE_MAX_ENTRIES
                ]:
                    self._detail_store.pop(key, None)
        return True

    def _detail_store_snapshot(self, stream_id, item=None):
        """Return fresh local EPG/logo data; this method never uses HTTP."""
        stream_id = str(stream_id or "")
        now = time.monotonic()
        with self._detail_store_lock:
            entry = dict(self._detail_store.get(stream_id, {}))
        events = []
        epg_cached_at = float(entry.get("epg_cached_at", 0.0) or 0.0)
        if (
            entry.get("events")
            and now - epg_cached_at < LIVE_DETAIL_CACHE_TTL_SECONDS
        ):
            events = list(entry["events"])
        if not events:
            events = self._cached_epg_snapshot(stream_id)
            if events:
                self._store_detail_component(stream_id, "events", events)
        epg_retry_after = float(
            entry.get("epg_retry_after", 0.0) or 0.0
        )
        epg_checked = bool(events) or now < epg_retry_after

        path = str(entry.get("picon_path", "") or "").strip()
        picon_cached_at = float(entry.get("picon_cached_at", 0.0) or 0.0)
        if (
            not path
            or not _valid_live_picon_file(path)
            or now - picon_cached_at >= LIVE_DETAIL_CACHE_TTL_SECONDS
        ):
            path = ""
        if not path and item is not None:
            url = _safe_picon_url(getattr(item, "icon", ""))
            cached_path = _cached_picon_path(url) if url else ""
            if cached_path and _valid_live_picon_file(cached_path):
                path = cached_path
                self._store_detail_component(stream_id, "picon_path", path)
        else:
            url = ""
        picon_checked = bool(
            path or not url or _picon_failure_active(url)
        )

        with self._detail_store_lock:
            stored = dict(self._detail_store.get(stream_id, {}))
        captured_at = max(
            float(stored.get("epg_captured_at", 0.0) or 0.0),
            float(stored.get("picon_captured_at", 0.0) or 0.0),
        )
        return {
            "stream_id": stream_id,
            "events": events,
            "epg_checked": epg_checked,
            "picon_path": path,
            "picon_checked": picon_checked,
            "captured_at": captured_at or time.time(),
        }

    def _publish_detail_component(
        self,
        generation,
        stream_id,
        component,
        value,
        store_epoch=None,
    ):
        # The durable local store is updated even if the user has already
        # pressed OK. The fullscreen barrier harvests it after the worker's
        # completion event, while stale row callbacks remain generation-safe.
        if self._closed:
            return
        stored = self._store_detail_component(
            stream_id,
            component,
            value,
            expected_epoch=store_epoch,
        )
        if not stored:
            return
        if generation != self._detail_generation:
            return
        with self._detail_results_lock:
            self._detail_results.setdefault(generation, []).append(
                (str(stream_id or ""), component, value)
            )

    def _visible_detail_page(self):
        page_start = int(self.selected_index / self.page_size) * self.page_size
        items = tuple(
            self._entries[page_start : page_start + self.page_size]
        )
        page_key = (
            page_start,
            tuple(self._selection_key(item) for item in items),
        )
        return page_key, items

    def _show_selected_local_detail(self, item, key):
        # Selection changes remain purely local and therefore cannot slow
        # rapid UP/DOWN navigation.
        ellipsize_dynamic_text(
            self["ribbon_channel"],
            localized_upper(item.name),
            fallback_chars=58,
        )
        for timer in (self._detail_progress_timer,):
            try:
                timer.stop()
            except Exception:
                pass
        self._detail_selection_key = key
        self._preview_item = item
        cached = self._detail_store_snapshot(key, item=item)
        self._preview_epg_events = list(cached.get("events", ()))
        self._preview_epg_loaded_at = (
            time.time() if self._preview_epg_events else 0.0
        )
        self._detail_picon_path = str(
            cached.get("picon_path", "") or ""
        )
        self._detail_picon_loaded_at = (
            time.monotonic() if self._detail_picon_path else 0.0
        )
        # Row artwork belongs to the exact service opened with OK, never to
        # the locally highlighted detail record.
        self._refresh_live_active_picon()
        if self._preview_epg_events:
            self._render_selected_epg(self._preview_epg_events)
            self._detail_progress_timer.start(
                LIVE_DETAIL_PROGRESS_INTERVAL_MS,
                True,
            )
        elif cached.get("epg_checked") or self._provider_epg_loader() is None:
            self._render_selected_epg([])
        else:
            self._render_detail_waiting(item)

    def _schedule_selected_detail(self):
        if self._closed or self._fullscreen_active or not self._entries:
            return
        item = self._entries[self.selected_index]
        key = self._selection_key(item)
        page_key = self._visible_detail_page()[0]
        page_changed = page_key != self._detail_page_key
        selection_changed = key != self._detail_selection_key

        if selection_changed or page_changed:
            # Cancel stale priorities immediately. Socket calls already in
            # progress remain bounded by their own deadline, while requested
            # playback is promoted ahead of queued optional portal work.
            self._cancel_selected_detail(clear_selection=False)
        if page_changed:
            self._detail_page_key = page_key
        if selection_changed or page_changed:
            self._show_selected_local_detail(item, key)

        cached = self._detail_store_snapshot(key, item=item)
        epg_complete = bool(
            cached.get("epg_checked")
            or self._selected_epg_loader() is None
        )
        selected_complete = epg_complete
        preview_ready = self._selected_detail_preview_ready(key)
        if selected_complete:
            if preview_ready:
                self._schedule_detail_prefetch()
            return
        if not preview_ready:
            # Highlighting remains local. The successful preview start calls
            # this method again after provider-link work has left the lane.
            return
        if self._detail_state_active(self._detail_foreground_state):
            return

        detail_dwell_ms = _live_detail_dwell_ms(self.client)
        self._detail_deadline = (
            time.monotonic() + (detail_dwell_ms / 1000.0)
        )
        self._detail_dwell_timer.start(detail_dwell_ms, True)
        _debug(
            "page detail dwell scheduled page={} selected={} delay_ms={}".format(
                page_key[0], key, detail_dwell_ms
            )
        )

    @staticmethod
    def _detail_state_active(state):
        if not state:
            return False
        events = state.get("events", {})
        tokens = state.get("tokens", {})
        for component, event in events.items():
            token = tokens.get(component)
            if not event.is_set() and (token is None or token.active()):
                return True
        return False

    @staticmethod
    def _cancel_detail_state(state):
        if not state:
            return
        for token in state.get("tokens", {}).values():
            _cancel_worker_token(token)
        _cancel_worker_token(state.get("token"))

    def _cancel_selected_detail(self, clear_selection=False):
        for timer in (
            self._detail_dwell_timer,
            self._detail_poll_timer,
            self._detail_prefetch_timer,
            self._detail_progress_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass
        self._detail_generation += 1
        self._cancel_detail_state(self._detail_foreground_state)
        self._cancel_detail_state(self._detail_prefetch_state)
        self._detail_foreground_state = None
        self._detail_prefetch_state = None
        self._detail_deadline = 0.0
        self._detail_prefetch_deadline = 0.0
        with self._detail_results_lock:
            self._detail_results = {}
        if clear_selection:
            self._detail_selection_key = None
            self._detail_page_key = None

    def _detail_dwell_fired(self):
        if self._closed or self._fullscreen_active or not self._entries:
            return
        remaining = self._detail_deadline - time.monotonic()
        if remaining > 0.01:
            self._detail_dwell_timer.start(
                max(10, int(round(remaining * 1000))), True
            )
            return
        page_key = self._visible_detail_page()[0]
        if page_key != self._detail_page_key:
            return
        if self._detail_state_active(self._detail_foreground_state):
            return

        selected = self._entries[self.selected_index]
        stream_id = self._selection_key(selected)
        if not stream_id or stream_id != self._detail_selection_key:
            return
        if not self._selected_detail_preview_ready(stream_id):
            return
        if _is_stalker_client(self.client):
            now = time.monotonic()
            started_at = float(self._preview_service_started_at or 0.0)
            elapsed_ms = (
                max(0, int(round((now - started_at) * 1000)))
                if started_at > 0.0
                else 0
            )
            quiet_remaining = STALKER_LIVE_EPG_QUIET_MS - elapsed_ms
            if quiet_remaining > 0:
                delay_ms = max(10, int(quiet_remaining))
                self._detail_deadline = now + (delay_ms / 1000.0)
                self._detail_dwell_timer.start(delay_ms, True)
                return
        epg_loader = self._selected_epg_loader()
        cached = self._detail_store_snapshot(stream_id, item=selected)
        needs_epg = bool(
            epg_loader is not None and not cached.get("epg_checked")
        )
        if not needs_epg:
            self._schedule_detail_prefetch()
            return

        generation = self._detail_generation
        store_epoch = self._detail_store_epoch
        state = {
            "generation": generation,
            "stream_id": stream_id,
            "events": {},
            "tokens": {},
        }
        workers = []
        if needs_epg:
            event = threading.Event()
            token = _WorkerToken(LIVE_DETAIL_EPG_TIMEOUT_SECONDS)
            state["events"]["events"] = event
            state["tokens"]["events"] = token
            workers.append(
                threading.Thread(
                    target=self._load_selected_epg_worker,
                    args=(
                        generation,
                        stream_id,
                        event,
                        store_epoch,
                        token,
                    ),
                )
            )
        self._detail_foreground_state = state
        for worker in workers:
            worker.daemon = True
            worker.start()
        self._detail_poll_timer.start(75, True)
        _debug(
            "selected detail started page={} selected={} epg={}".format(
                page_key[0], stream_id, needs_epg
            )
        )

    def _provider_epg_loader(self):
        loader = getattr(self.client, "load_epg", None)
        if callable(loader):
            return loader
        loader = getattr(self.client, "load_short_epg", None)
        return loader if callable(loader) else None

    def _selected_detail_preview_ready(self, stream_id):
        if (
            VideoWindow is None
            or self._closed
            or self._fullscreen_active
            or not self._preview_service_active
            or str(stream_id or "") != self._playing_preview_key
        ):
            return False
        return self._same_service_reference(
            self._current_navigation_reference(),
            self._playing_preview_reference,
        )

    def _selected_epg_loader(self):
        # Expose provider EPG only for the service already playing in the PIG.
        # UP/DOWN and provider-link creation therefore remain network-silent.
        if not self._selected_detail_preview_ready(
            self._detail_selection_key
        ):
            return None
        return self._provider_epg_loader()

    def _load_selected_epg_worker(
        self,
        generation,
        stream_id,
        worker_event,
        store_epoch=None,
        worker_token=None,
    ):
        events = []
        try:
            if worker_token is not None:
                worker_token.check()
            loader = self._provider_epg_loader()
            if loader is not None:
                events = _bounded_client_call(
                    self.client,
                    worker_token,
                    lambda: _load_shared_live_epg(
                        self.client,
                        stream_id,
                        LIVE_EPG_FETCH_LIMIT,
                        loader,
                        token=worker_token,
                    ),
                )
            if worker_token is not None:
                worker_token.check()
            self._publish_detail_component(
                generation,
                stream_id,
                "events",
                list(events or []),
                store_epoch=store_epoch,
            )
        except _WorkerCancelled:
            pass
        except Exception:
            if worker_token is None or worker_token.active():
                self._publish_detail_component(
                    generation,
                    stream_id,
                    "events",
                    [],
                    store_epoch=store_epoch,
                )
        finally:
            worker_event.set()

    def _load_detail_picon_path(self, item, worker_token=None):
        return _load_live_picon_path(
            self.client,
            item,
            worker_token=worker_token,
        )

    def _cancel_live_active_picon_fetch(self, clear_target=False):
        for timer in (
            self._live_active_picon_timer,
            self._live_active_picon_poll_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass
        self._live_active_picon_fetch_generation += 1
        state = self._live_active_picon_fetch_state
        self._live_active_picon_fetch_state = None
        if state is not None:
            _cancel_worker_token(state.get("token"))
        self._live_active_picon_fetch_deadline = 0.0
        if clear_target:
            self._live_active_picon_fetch_item = None
            self._live_active_picon_fetch_key = ""

    def _schedule_live_active_picon(self, item, key):
        """Queue artwork only for the exact channel opened with OK."""
        self._cancel_live_active_picon_fetch(clear_target=True)
        key = str(key or "")
        if (
            not key
            or item is None
            or not self._selected_detail_preview_ready(key)
        ):
            self._refresh_live_active_picon(force=True)
            return
        self._live_active_picon_fetch_item = item
        self._live_active_picon_fetch_key = key
        self._refresh_live_active_picon(force=True)
        cached = self._detail_store_snapshot(key, item=item)
        if cached.get("picon_checked"):
            if not cached.get("picon_path"):
                self._defer_live_active_picon_failure(item)
            return
        if not _safe_picon_url(getattr(item, "icon", "")):
            return
        delay_ms = _live_detail_dwell_ms(self.client)
        self._live_active_picon_fetch_deadline = (
            time.monotonic() + (delay_ms / 1000.0)
        )
        self._live_active_picon_timer.start(delay_ms, True)

    def _defer_live_active_picon_failure(self, item):
        url = _safe_picon_url(getattr(item, "icon", ""))
        remaining = _picon_failure_remaining(url) if url else 0.0
        if remaining <= 0.0:
            return False
        delay_ms = max(1000, int(math.ceil(remaining * 1000.0)))
        self._live_active_picon_fetch_deadline = (
            time.monotonic() + (delay_ms / 1000.0)
        )
        self._live_active_picon_timer.start(delay_ms, True)
        return True

    def _ensure_live_active_picon(self):
        """Recover deferred exact-reference activation without row input."""
        key = str(getattr(self, "_playing_preview_key", "") or "")
        if not key:
            return False
        item = getattr(self, "_playing_preview_item", None)
        for candidate in tuple(getattr(self, "_entries", ()) or ()):
            if self._selection_key(candidate) == key:
                item = candidate
                break
        if key == self._live_active_picon_fetch_key:
            # The worker may have committed a valid local path while the
            # navigation reference was briefly unavailable. Repaint from the
            # cache without disturbing a live timer/worker. If that cache was
            # later removed, an idle target must be allowed to fetch it again.
            self._refresh_live_active_picon()
            if not self._selected_detail_preview_ready(key) or item is None:
                return True
            cached = self._detail_store_snapshot(key, item=item)
            cached_path = str(cached.get("picon_path", "") or "").strip()
            if cached_path and _valid_live_picon_file(cached_path):
                return True
            if not _safe_picon_url(getattr(item, "icon", "")):
                return True
            if getattr(self, "_live_active_picon_fetch_state", None) is not None:
                return True
            deadline = float(
                getattr(self, "_live_active_picon_fetch_deadline", 0.0) or 0.0
            )
            if deadline > time.monotonic():
                return True
            self._schedule_live_active_picon(item, key)
            return True
        if not self._selected_detail_preview_ready(key):
            return False
        if item is not None:
            self._schedule_live_active_picon(item, key)
            return True
        return False

    def _live_active_picon_fired(self):
        if self._closed or self._fullscreen_active:
            return
        item = self._live_active_picon_fetch_item
        key = str(self._live_active_picon_fetch_key or "")
        if (
            item is None
            or not key
            or not self._selected_detail_preview_ready(key)
        ):
            self._cancel_live_active_picon_fetch(clear_target=True)
            self._refresh_live_active_picon(force=True)
            return
        now = time.monotonic()
        remaining = self._live_active_picon_fetch_deadline - now
        if _is_stalker_client(self.client):
            started_at = float(self._preview_service_started_at or 0.0)
            if started_at > 0.0:
                quiet_remaining = (
                    STALKER_LIVE_PICON_QUIET_MS
                    - max(0, int(round((now - started_at) * 1000)))
                )
                remaining = max(remaining, quiet_remaining / 1000.0)
        if remaining > 0.01:
            self._live_active_picon_timer.start(
                max(10, int(round(remaining * 1000))),
                True,
            )
            return
        cached = self._detail_store_snapshot(key, item=item)
        if cached.get("picon_checked"):
            if not cached.get("picon_path"):
                self._defer_live_active_picon_failure(item)
            self._refresh_live_active_picon(force=True)
            return
        generation = self._live_active_picon_fetch_generation
        event = threading.Event()
        token = _WorkerToken(max(3, LIVE_PICON_DOWNLOAD_TIMEOUT + 1))
        self._live_active_picon_fetch_state = {
            "generation": generation,
            "stream_id": key,
            "event": event,
            "token": token,
        }
        worker = threading.Thread(
            target=self._load_live_active_picon_worker,
            args=(
                generation,
                key,
                item,
                event,
                self._detail_store_epoch,
                token,
            ),
        )
        worker.daemon = True
        worker.start()
        self._live_active_picon_poll_timer.start(75, True)

    def _load_live_active_picon_worker(
        self,
        generation,
        stream_id,
        item,
        worker_event,
        store_epoch=None,
        worker_token=None,
    ):
        try:
            path = self._load_detail_picon_path(item, worker_token)
            if worker_token is not None:
                worker_token.check()
            if generation != self._live_active_picon_fetch_generation:
                return
            self._store_detail_component(
                stream_id,
                "picon_path",
                path,
                expected_epoch=store_epoch,
            )
        except _WorkerCancelled:
            pass
        except Exception:
            url = _safe_picon_url(getattr(item, "icon", ""))
            if url:
                _mark_picon_failure(url)
        finally:
            worker_event.set()

    def _live_active_picon_poll(self):
        if self._closed or self._fullscreen_active:
            return
        state = self._live_active_picon_fetch_state
        if state is None:
            return
        generation = int(state.get("generation", -1))
        event = state.get("event")
        token = state.get("token")
        timed_out = False
        if event is not None and not event.is_set():
            if token is not None and token.expired():
                token.cancel()
                timed_out = True
            else:
                self._live_active_picon_poll_timer.start(75, True)
                return
        if generation != self._live_active_picon_fetch_generation:
            self._live_active_picon_fetch_state = None
            return
        key = str(state.get("stream_id", "") or "")
        if not self._selected_detail_preview_ready(key):
            can_settle_reference = bool(
                not timed_out
                and (event is None or event.is_set())
                and VideoWindow is not None
                and getattr(self, "_preview_service_active", False)
                and key
                == str(getattr(self, "_playing_preview_key", "") or "")
                and getattr(self, "_playing_preview_reference", None) is not None
            )
            if can_settle_reference:
                now = time.monotonic()
                settle_deadline = float(
                    state.get("reference_settle_deadline", 0.0) or 0.0
                )
                if settle_deadline <= 0.0:
                    settle_seconds = (
                        LIVE_PREVIEW_VIDEO_INITIAL_MS
                        + (
                            LIVE_PREVIEW_VIDEO_MAX_CHECKS
                            * LIVE_PREVIEW_VIDEO_RETRY_MS
                        )
                    ) / 1000.0
                    settle_deadline = now + settle_seconds
                    state["reference_settle_deadline"] = settle_deadline
                if now < settle_deadline:
                    self._live_active_picon_poll_timer.start(75, True)
                    return
            self._live_active_picon_fetch_state = None
            return
        self._live_active_picon_fetch_state = None
        item = self._live_active_picon_fetch_item
        if timed_out and item is not None:
            url = _safe_picon_url(getattr(item, "icon", ""))
            if url:
                _mark_picon_failure(url)
        cached = self._detail_store_snapshot(key, item=item)
        if not cached.get("picon_path") and item is not None:
            self._defer_live_active_picon_failure(item)
        self._refresh_live_active_picon(force=True)

    def _load_selected_picon_worker(
        self,
        generation,
        stream_id,
        item,
        worker_event,
        store_epoch=None,
        worker_token=None,
    ):
        try:
            path = self._load_detail_picon_path(item, worker_token)
            self._publish_detail_component(
                generation,
                stream_id,
                "picon_path",
                path,
                store_epoch=store_epoch,
            )
        except _WorkerCancelled:
            pass
        finally:
            worker_event.set()

    def _schedule_detail_prefetch(self):
        # R62 keeps row-artwork network work strictly behind an explicit OK.
        # Neighbouring highlighted channels are never prefetched.
        return

    def _detail_prefetch_fired(self):
        return

    def _load_detail_prefetch_worker(
        self,
        generation,
        jobs,
        worker_event,
        store_epoch=None,
        worker_token=None,
    ):
        try:
            for stream_id, item, needs_epg, needs_picon in jobs:
                if worker_token is not None:
                    worker_token.check()
                if generation != self._detail_generation:
                    return
                if needs_epg:
                    events = []
                    loader = self._selected_epg_loader()
                    if loader is not None:
                        events = _bounded_client_call(
                            self.client,
                            worker_token,
                            lambda sid=stream_id, selected_loader=loader: (
                                _load_shared_live_epg(
                                    self.client,
                                    sid,
                                    LIVE_EPG_FETCH_LIMIT,
                                    selected_loader,
                                    token=worker_token,
                                )
                            ),
                        )
                    if worker_token is not None:
                        worker_token.check()
                    self._store_detail_component(
                        stream_id,
                        "events",
                        list(events or []),
                        expected_epoch=store_epoch,
                    )
                if needs_picon:
                    path = self._load_detail_picon_path(item, worker_token)
                    self._store_detail_component(
                        stream_id,
                        "picon_path",
                        path,
                        expected_epoch=store_epoch,
                    )
        except _WorkerCancelled:
            pass
        finally:
            worker_event.set()

    def _detail_poll(self):
        if self._closed or self._fullscreen_active:
            return
        generation = self._detail_generation
        state = self._detail_foreground_state
        worker_active = False
        worker_finished = False
        timed_out_components = []
        if state is not None and state.get("generation") == generation:
            for component, event in state.get("events", {}).items():
                token = state.get("tokens", {}).get(component)
                if (
                    not event.is_set()
                    and token is not None
                    and token.expired()
                ):
                    token.cancel()
                    timed_out_components.append(component)
            worker_active = self._detail_state_active(state)
            worker_finished = not worker_active
        with self._detail_results_lock:
            results = self._detail_results.pop(generation, [])
        if "events" in timed_out_components and state is not None:
            stream_id = str(state.get("stream_id", "") or "")
            result_already_published = any(
                result[0] == stream_id and result[1] == "events"
                for result in results
            )
            if stream_id and not result_already_published:
                self._store_detail_component(stream_id, "events", [])
                results.append((stream_id, "events", []))
        for stream_id, component, value in results:
            if stream_id != self._detail_selection_key:
                continue
            if component == "events":
                self._preview_epg_events = list(value or [])
                self._preview_epg_loaded_at = time.time()
                self._render_selected_epg(self._preview_epg_events)
                if self._preview_epg_events:
                    self._detail_progress_timer.start(
                        LIVE_DETAIL_PROGRESS_INTERVAL_MS,
                        True,
                    )
            elif component == "picon_path":
                path = str(value or "").strip()
                self._detail_picon_path = (
                    path if path and _valid_live_picon_file(path) else ""
                )
                self._detail_picon_loaded_at = (
                    time.monotonic() if self._detail_picon_path else 0.0
                )
                self._refresh_live_active_picon(force=True)
        if worker_active:
            self._detail_poll_timer.start(75, True)
        elif worker_finished:
            self._detail_foreground_state = None
            self._schedule_detail_prefetch()

    def _detail_window(self, now=None):
        events = list(self._preview_epg_events or [])
        if not events:
            return []
        current_time = int(time.time() if now is None else now)
        for index, event in enumerate(events):
            start = int(getattr(event, "start_timestamp", 0) or 0)
            end = int(getattr(event, "end_timestamp", 0) or 0)
            if (start and end and start <= current_time < end) or (
                end and end > current_time
            ):
                return events[index:]
        if any(not int(getattr(event, "end_timestamp", 0) or 0) for event in events):
            return events
        return []

    def _detail_progress_tick(self):
        if self._closed or self._fullscreen_active:
            return
        window = self._detail_window()
        if not window:
            self._clear_epg_progress()
            return
        if _epg_event_starts_in_future(window[0]):
            self._clear_epg_progress()
            self._detail_progress_timer.start(
                LIVE_DETAIL_PROGRESS_INTERVAL_MS,
                True,
            )
            return
        if window[0] is not self._preview_epg_events[0]:
            self._preview_epg_events = window
            self._render_selected_epg(window)
        else:
            self._set_epg_progress(window[0])
        self._detail_progress_timer.start(
            LIVE_DETAIL_PROGRESS_INTERVAL_MS,
            True,
        )

    def _render_detail_waiting(self, item):
        ellipsize_dynamic_text(
            self["ribbon_channel"],
            localized_upper(item.name),
            fallback_chars=58,
        )
        self["ribbon_now_time"].setText("--:--")
        self._set_static_now_title(
            _("Waiting for programme information")
        )
        self["ribbon_next_caption"].setText(_("NEXT"))
        self["ribbon_next_time"].setText("")
        self._set_static_next_title("")
        for row in ("now", "next", "third", "fourth"):
            self["epg_{}_time".format(row)].setText("")
            self["epg_{}_title".format(row)].setText("")
            self["epg_{}_status".format(row)].setText("")
        self._clear_epg_progress()
        self["summary"].setText(_("Waiting for programme information"))
        self["summary_scroll"].setText("")

    def _render_selected_epg(self, events):
        events = self._detail_window() if events else []
        if not events:
            self["ribbon_now_time"].setText("--:--")
            self._set_static_now_title(
                _("EPG information unavailable.")
            )
            self["ribbon_next_caption"].setText(_("NEXT"))
            self["ribbon_next_time"].setText("")
            self._set_static_next_title(
                _("EPG information unavailable.")
            )
            self["epg_now_time"].setText("")
            self["epg_now_title"].setText("")
            self["epg_now_status"].setText("")
            for row in ("next", "third", "fourth"):
                self["epg_{}_time".format(row)].setText("")
                self["epg_{}_title".format(row)].setText("")
                self["epg_{}_status".format(row)].setText("")
            self._clear_epg_progress()
            self["summary"].setText(
                _("No summary is available for this programme.")
            )
            self["summary_scroll"].setText("")
            return
        future_only = _epg_event_starts_in_future(events[0])
        current = None if future_only else events[0]
        if current is None:
            self["ribbon_now_time"].setText("--:--")
            self._set_static_now_title(
                _("EPG information unavailable.")
            )
            self["epg_now_time"].setText("")
            self["epg_now_title"].setText("")
            self["epg_now_status"].setText("")
            self._clear_epg_progress()
        else:
            self["ribbon_now_time"].setText(current.time_text() or "--:--")
            self._set_now_title(current.title)
            self["epg_now_time"].setText(
                current.start or current.time_text() or "--:--"
            )
            ellipsize_dynamic_text(
                self["epg_now_title"],
                current.title,
                fallback_chars=54,
            )
            self["epg_now_status"].setText(_("NOW"))
            self._set_epg_progress(current)
        rows = ("next", "third", "fourth")
        statuses = (_("NEXT"), _("LATER"), _("LATER"))
        first_upcoming = 0 if future_only else 1
        for row_offset, row in enumerate(rows):
            offset = first_upcoming + row_offset
            event = events[offset] if offset < len(events) else None
            self["epg_{}_time".format(row)].setText(
                (event.start or event.time_text()) if event is not None else ""
            )
            if event is None:
                self["epg_{}_title".format(row)].setText("")
            else:
                ellipsize_dynamic_text(
                    self["epg_{}_title".format(row)],
                    event.title,
                    fallback_chars=54,
                )
            self["epg_{}_status".format(row)].setText(
                statuses[row_offset] if event is not None else ""
            )
        upcoming = (
            events[first_upcoming]
            if first_upcoming < len(events)
            else None
        )
        self["ribbon_next_caption"].setText(_("NEXT"))
        self["ribbon_next_time"].setText(
            (upcoming.start or upcoming.time_text())
            if upcoming is not None
            else ""
        )
        if upcoming is None:
            self._set_static_next_title(
                _("EPG information unavailable.")
            )
        else:
            fit_dynamic_text(
                self["ribbon_next_title"],
                upcoming.title,
                max_lines=1,
                preferred_size=self._ribbon_next_preferred_font,
                min_size=self._ribbon_next_min_font,
                fallback_chars=36,
            )
        summary = (
            getattr(current, "description", "")
            if current is not None
            else ""
        )
        if summary:
            self._set_summary(summary)
        else:
            self["summary"].setText(
                _("No summary is available for this programme.")
            )
            self["summary_scroll"].setText("")

    def _set_now_title(self, value):
        return fit_dynamic_text(
            self["ribbon_now_title"],
            value,
            max_lines=1,
            preferred_size=self._ribbon_now_preferred_font,
            min_size=self._ribbon_now_min_font,
            fallback_chars=36,
        )

    def _set_static_now_title(self, value):
        return set_static_text(
            self["ribbon_now_title"],
            value,
            preferred_size=self._ribbon_now_preferred_font,
        )

    def _set_static_next_title(self, value):
        return set_static_text(
            self["ribbon_next_title"],
            value,
            preferred_size=self._ribbon_next_preferred_font,
        )

    def _clear_live_active_picon(self, reset_signature=True):
        self._live_active_picon_generation += 1
        self._live_active_picon_widget_owners = {}
        try:
            loaders = getattr(self, "_gt_pixmap_loaders", {})
            loader = loaders.pop("live-active-row-picon", None)
            if loader is not None:
                loader.close()
        except Exception:
            pass
        for row_index in range(
            int(getattr(self, "page_size", LIVE_STREAM_PAGE_SIZE))
        ):
            try:
                self["stream_picon_{}".format(row_index)].hide()
            except Exception:
                pass
        if reset_signature:
            self._live_active_picon_signature = None

    def _live_active_picon_target(self, page_start=None, page_items=None):
        """Return the visible exact-service row and its local picon path."""
        entries = list(getattr(self, "_entries", ()) or ())
        if page_start is None:
            selected_index = int(getattr(self, "selected_index", 0) or 0)
            page_size = max(1, int(getattr(self, "page_size", 1) or 1))
            page_start = int(selected_index / page_size) * page_size
        page_start = int(page_start)
        if page_items is None:
            page_size = max(1, int(getattr(self, "page_size", 1) or 1))
            page_items = entries[page_start : page_start + page_size]
        page_items = tuple(page_items or ())
        key = str(getattr(self, "_playing_preview_key", "") or "")
        if (
            not key
            or not getattr(self, "_preview_service_active", False)
            or getattr(self, "_fullscreen_active", False)
            or not self._same_service_reference(
                self._current_navigation_reference(),
                getattr(self, "_playing_preview_reference", None),
            )
        ):
            return (page_start, -1, "", "")

        row_index = -1
        try:
            active_index = int(
                getattr(self, "_playing_preview_index", -1)
            )
        except (TypeError, ValueError, OverflowError):
            active_index = -1
        candidate_row = active_index - page_start
        if 0 <= candidate_row < len(page_items):
            if self._selection_key(page_items[candidate_row]) == key:
                row_index = candidate_row
        if row_index < 0:
            for offset, item in enumerate(page_items):
                if self._selection_key(item) == key:
                    row_index = offset
                    break
        if row_index < 0:
            return (page_start, -1, key, "")

        item = page_items[row_index]
        cached = self._detail_store_snapshot(key, item=item)
        path = str(cached.get("picon_path", "") or "").strip()
        if not path or not _valid_live_picon_file(path):
            path = ""
        return (page_start, row_index, key, path)

    def _refresh_live_active_picon(
        self,
        force=False,
        page_start=None,
        page_items=None,
    ):
        signature = self._live_active_picon_target(
            page_start=page_start,
            page_items=page_items,
        )
        if not force and signature == self._live_active_picon_signature:
            return
        self._clear_live_active_picon(reset_signature=False)
        self._live_active_picon_signature = signature
        unused_page, row_index, unused_key, path = signature
        if row_index < 0 or not path:
            return
        generation = self._live_active_picon_generation
        widget_name = "stream_picon_{}".format(row_index)
        self._live_active_picon_widget_owners[widget_name] = generation

        def loaded(success):
            # A superseded loader may share the same physical row widget with
            # the new active channel. Its late callback must not hide artwork
            # which the current generation has already published.
            if (
                generation != self._live_active_picon_generation
                or signature != self._live_active_picon_signature
            ):
                if widget_name not in self._live_active_picon_widget_owners:
                    try:
                        self[widget_name].hide()
                    except Exception:
                        pass
                return
            if (
                getattr(self, "_closed", False)
                or self._fullscreen_active
                or signature != self._live_active_picon_target()
            ):
                try:
                    self[widget_name].hide()
                except Exception:
                    pass
                return
            if success:
                self[widget_name].show()
            else:
                self[widget_name].hide()

        attach_pixmap(
            self,
            widget_name,
            path,
            key="live-active-row-picon",
            on_loaded=loaded,
        )

    def _update_epg_clock(self):
        try:
            self["ribbon_clock"].setText(time.strftime("%H:%M"))
        except Exception:
            pass

    def start_epg_clock(self):
        if self._closed or self._fullscreen_active:
            return
        self._update_epg_clock()
        self._epg_clock_timer.start(_minute_timer_delay_ms(), True)

    def _epg_clock_tick(self):
        if self._closed or self._fullscreen_active:
            return
        self._update_epg_clock()
        self._epg_clock_timer.start(_minute_timer_delay_ms(), True)

    def _clear_epg_progress(self):
        self["epg_progress"].setText("")
        self["epg_progress_fill"].hide()

    def _set_epg_progress(self, event):
        ratio, value = self._progress_details(event)
        self["epg_progress"].setText(value)
        fill = self["epg_progress_fill"]
        if not value:
            fill.hide()
            return
        try:
            fill.instance.resize(
                eSize(
                    max(1, int(round(self._progress_width * ratio))),
                    self._progress_height,
                )
            )
        except Exception:
            pass
        fill.show()

    def _set_summary(self, summary):
        fit_dynamic_text(
            self["summary"],
            summary,
            max_lines=3,
            fallback_chars=52,
        )
        self["summary_scroll"].setText("")

    def _snapshot_for_selected(self, item):
        stream_id = self._selection_key(item)
        selected = self._detail_store_snapshot(stream_id, item=item)
        matches = (
            self._preview_item is not None
            and self._selection_key(self._preview_item) == stream_id
        )
        now = time.monotonic()
        preview_epg_fresh = bool(
            self._preview_epg_events
            and (
                not self._preview_epg_loaded_at
                or time.time() - self._preview_epg_loaded_at
                < LIVE_DETAIL_CACHE_TTL_SECONDS
            )
        )
        preview_picon_fresh = bool(
            self._detail_picon_path
            and _valid_live_picon_file(self._detail_picon_path)
            and (
                not self._detail_picon_loaded_at
                or now - self._detail_picon_loaded_at
                < LIVE_DETAIL_CACHE_TTL_SECONDS
            )
        )
        if matches and not selected.get("events") and preview_epg_fresh:
            selected["events"] = list(self._preview_epg_events or [])
        if matches and not selected.get("picon_path") and preview_picon_fresh:
            path = str(self._detail_picon_path or "").strip()
            selected["picon_path"] = path

        channels = {}
        with self._detail_store_lock:
            stored_channels = {
                key: dict(value)
                for key, value in self._detail_store.items()
            }
        for key, entry in stored_channels.items():
            events = []
            if (
                entry.get("events")
                and now - float(entry.get("epg_cached_at", 0.0) or 0.0)
                < LIVE_DETAIL_CACHE_TTL_SECONDS
            ):
                events = list(entry["events"])
            path = str(entry.get("picon_path", "") or "").strip()
            if (
                not path
                or not _valid_live_picon_file(path)
                or now - float(entry.get("picon_cached_at", 0.0) or 0.0)
                >= LIVE_DETAIL_CACHE_TTL_SECONDS
            ):
                path = ""
            if not events and not path:
                continue
            channels[str(key)] = {
                "stream_id": str(key),
                "events": events,
                "picon_path": path,
                "captured_at": max(
                    float(entry.get("epg_captured_at", 0.0) or 0.0),
                    float(entry.get("picon_captured_at", 0.0) or 0.0),
                ) or time.time(),
            }
        channels[stream_id] = {
            "stream_id": stream_id,
            "events": list(selected.get("events", ())),
            "picon_path": str(selected.get("picon_path", "") or ""),
            "captured_at": float(
                selected.get("captured_at", time.time()) or time.time()
            ),
        }
        return {
            "stream_id": stream_id,
            "events": list(selected.get("events", ())),
            "picon_path": str(selected.get("picon_path", "") or ""),
            "weather_forecast": self._weather_forecast_snapshot,
            "captured_at": time.time(),
            "channels": channels,
        }

    def open_selected(self):
        if (
            not self._entries
            or self._loading
            or self._fullscreen_active
        ):
            return
        item = self._entries[self.selected_index]
        key = self._selection_key(item)
        current = self._current_navigation_reference()
        if (
            self._preview_service_active
            and key == self._playing_preview_key
            and self._same_service_reference(
                current,
                self._playing_preview_reference,
            )
        ):
            self._open_preview_fullscreen(
                item,
                self.selected_index,
                self._playing_preview_reference,
            )
            return
        if (
            VideoWindow is not None
            and self._preview_service_active
            and key == self._playing_preview_key
        ):
            started_at = float(self._preview_service_started_at or 0.0)
            settle_seconds = (
                LIVE_PREVIEW_VIDEO_INITIAL_MS
                + (LIVE_PREVIEW_VIDEO_MAX_CHECKS * LIVE_PREVIEW_VIDEO_RETRY_MS)
            ) / 1000.0
            if (
                started_at <= 0.0
                or time.monotonic() - started_at <= settle_seconds
            ):
                # playService() has returned, but a few images publish the
                # current reference asynchronously. A second OK in that
                # settle window must not mint another provider ticket.
                self["message"].setText(_("Please wait"))
                self._resume_preview_pig()
                return
            self._open_preview_fullscreen(
                item,
                self.selected_index,
                self._playing_preview_reference,
            )
            return
        with self._preview_link_lock:
            same_request_pending = bool(
                self._preview_link_pending
                and key == self._preview_target_key
            )
        if same_request_pending:
            # Do not turn an unresolved first-OK mini-TV request into
            # fullscreen. Repeated OK is idempotent until the exact reference
            # is active, and no second Stalker ticket is minted.
            self["message"].setText(_("Please wait"))
            return
        try:
            self._preview_dwell_timer.stop()
        except Exception:
            pass
        self._preview_auto_suspended_key = ""
        self._begin_preview_link(
            item,
            self.selected_index,
            # Images without VideoWindow cannot present a safe mini TV. Keep
            # their established direct-fullscreen fallback on the first OK.
            request_fullscreen=VideoWindow is None,
        )

    def _open_preview_fullscreen(self, item, index, reference):
        recovery = getattr(self, "_live_startup_recovery", None)
        if recovery is not None and recovery.busy:
            return
        current = self._current_navigation_reference()
        if not self._same_service_reference(current, reference):
            # Decoder ownership changed after OK. Never adopt a stale
            # reference or leave a just-queued tune unowned. Reassert the
            # pre-list service first so a delayed playService() completion
            # cannot become hidden after state is cleared.
            self._restore_pre_list_service()
            if self._preview_service_active and VideoWindow is None:
                self._relinquish_hidden_preview(self._selection_key(item))
            if VideoWindow is None:
                message = _("Could not open the stream.")
                self["message"].setText(message)
                self._show_preview_fallback(message)
                return
            if self._preview_service_active:
                message = _("Could not open the stream.")
                self["message"].setText(message)
                self._show_preview_fallback(message)
                return
            # On PIG-capable images, rebuild the selected channel as a new
            # first-OK mini-TV request. Fullscreen still requires a later OK.
            self._begin_preview_link(item, index, request_fullscreen=False)
            return
        self._fullscreen_link_snapshot = self._snapshot_for_selected(item)
        self._cancel_preview_resolution(clear_target=True)
        self._cancel_selected_detail(clear_selection=False)
        self._cancel_live_active_picon_fetch(clear_target=True)
        self._clear_live_active_picon()
        with self._live_more_worker_events_lock:
            active_catalog_tokens = tuple(self._live_more_worker_tokens)
            self._live_more_token = None
            self._live_more_results = {}
        for token in active_catalog_tokens:
            _cancel_worker_token(token)
        self._live_more_loading = False
        for timer in (self._timer, self._epg_clock_timer):
            try:
                timer.stop()
            except Exception:
                pass
        if recovery is not None:
            recovery.detach()
        self._fullscreen_active = True
        self._suspend_preview_pig()
        arguments = (
            GTExternalPlayerScreen,
            reference,
            item,
            self.client,
            list(self._entries),
            int(index),
        )
        opener = getattr(self.session, "openWithCallback", None)
        try:
            if opener is not None:
                opener(
                    self._fullscreen_closed,
                    *arguments,
                    keep_playing_on_exit=True,
                    live_snapshot=self._fullscreen_link_snapshot,
                    favorite_store=self.favorite_store,
                    favorite_keys=self._favorite_keys,
                    adopt_playing_service=True,
                    old_reference_override=self._pre_list_reference,
                    live_startup_recovery=recovery,
                )
                return
            dialog = self.session.open(
                *arguments,
                keep_playing_on_exit=True,
                live_snapshot=self._fullscreen_link_snapshot,
                favorite_store=self.favorite_store,
                favorite_keys=self._favorite_keys,
                adopt_playing_service=True,
                old_reference_override=self._pre_list_reference,
                live_startup_recovery=recovery,
            )
            on_close = getattr(dialog, "onClose", None)
            if isinstance(on_close, list):
                on_close.append(
                    lambda: self._fullscreen_closed(
                        getattr(dialog, "_fullscreen_close_result", None)
                    )
                )
                return
        except Exception as error:
            _debug("fullscreen open failed error={}".format(error.__class__.__name__))
        self._fullscreen_open_failed()

    def _fullscreen_open_failed(self):
        self._fullscreen_link_snapshot = None
        self._fullscreen_active = False
        if getattr(self, "_live_startup_recovery", None) is not None:
            arm_startup = getattr(self, "_arm_live_startup", None)
            if callable(arm_startup):
                arm_startup()
        self["message"].setText(_("Could not open the stream."))
        if self._live_more_loading:
            self._timer.start(100, True)
        self.start_epg_clock()
        self._detail_selection_key = None
        if VideoWindow is None:
            suspended_key = self._playing_preview_key
            if self._entries:
                suspended_key = self._selection_key(
                    self._entries[self.selected_index]
                )
            self._relinquish_hidden_preview(suspended_key)
            self._show_preview_fallback(_("Could not open the stream."))
            return
        self._schedule_selected_detail()
        if self._preview_service_active:
            ensure_picon = getattr(self, "_ensure_live_active_picon", None)
            if callable(ensure_picon):
                ensure_picon()
            self._resume_preview_pig()
        else:
            self._show_preview_fallback(_("Could not open the stream."))

    def _fullscreen_closed(self, result=None, *args):
        if self._closed:
            return
        selected_index = self.selected_index
        kept = False
        released = False
        reference = None
        if isinstance(result, dict) and result.get("kind") == LIVE_FULLSCREEN_RESULT_KIND:
            selected_index = result.get("selected_index", selected_index)
            kept = bool(result.get("kept"))
            released = bool(result.get("released")) and not kept
            reference = result.get("reference") if kept else None
        if self._entries:
            try:
                self.selected_index = int(selected_index) % len(self._entries)
            except (TypeError, ValueError, OverflowError):
                pass
        self._fullscreen_link_snapshot = None
        self._fullscreen_active = False
        if kept and reference is not None and self._entries:
            item = self._entries[self.selected_index]
            self._playing_preview_item = item
            self._playing_preview_key = self._selection_key(item)
            self._playing_preview_index = int(self.selected_index)
            self._playing_preview_reference = reference
            self._preview_service_active = True
            self._preview_service_started_at = time.monotonic()
            self._live_startup_recovery = result.get(
                "live_startup_recovery",
                getattr(self, "_live_startup_recovery", None),
            )
            if self._live_startup_recovery is not None:
                arm_startup = getattr(self, "_arm_live_startup", None)
                if callable(arm_startup):
                    arm_startup()
            self._pre_list_restored = False
            try:
                self._active_live_service_type = int(
                    result.get(
                        "service_type",
                        reference_service_type(reference, 4097),
                    )
                )
            except (TypeError, ValueError, OverflowError):
                self._active_live_service_type = reference_service_type(
                    reference,
                    4097,
                )
            self["engine_badge"].setText(
                service_engine_label(self._active_live_service_type)
            )
            if VideoWindow is None:
                # A kept decoder cannot be shown on this image. Restore the
                # pre-list service immediately so fullscreen EXIT never leaves
                # hidden IPTV audio behind the channel list.
                self._relinquish_hidden_preview(self._selection_key(item))
                kept = False
                reference = None
        else:
            suspended_key = ""
            if self._entries:
                suspended_key = self._selection_key(
                    self._entries[self.selected_index]
                )
            if released:
                self._pre_list_restored = True
                self._preview_auto_suspended_key = (
                    suspended_key if VideoWindow is None else ""
                )
                self._clear_preview_service_state()
            elif self._preview_service_active:
                if VideoWindow is None:
                    self._preview_auto_suspended_key = suspended_key
                self._release_preview_for_exit()
            else:
                self._clear_preview_service_state()
                self._pre_list_restored = True
                self._preview_auto_suspended_key = suspended_key
        self._detail_selection_key = None
        self._reload_favorite_keys()
        self._live_render_page_key = None
        if self._live_more_loading:
            self._timer.start(100, True)
        self._refresh_live_streams()
        ensure_picon = getattr(self, "_ensure_live_active_picon", None)
        if callable(ensure_picon):
            ensure_picon()
        self.start_epg_clock()

    def close_list(self):
        if not self._release_preview_for_exit():
            message = _("Keeping the current stream")
            self["message"].setText(message)
            if VideoWindow is None:
                self._show_preview_fallback(message)
            elif self._preview_service_active:
                self._resume_preview_pig()
            return
        self.close()

    def _clear_preview_service_state(self):
        cancel_startup = getattr(self, "_cancel_live_startup", None)
        if callable(cancel_startup):
            cancel_startup()
        cancel_picon = getattr(
            self,
            "_cancel_live_active_picon_fetch",
            None,
        )
        if callable(cancel_picon):
            cancel_picon(clear_target=True)
        self._playing_preview_item = None
        self._playing_preview_key = ""
        self._playing_preview_index = -1
        self._playing_preview_reference = None
        self._preview_service_active = False
        self._preview_service_started_at = 0.0
        self._preview_target_item = None
        self._preview_target_key = ""
        self._preview_target_index = -1
        self._live_active_picon_reload_pending = False
        clear_picon = getattr(self, "_clear_live_active_picon", None)
        if callable(clear_picon):
            clear_picon()

    def _relinquish_hidden_preview(self, suspended_key=""):
        """Restore or stop a retained service which cannot be shown in PIG."""
        self._preview_auto_suspended_key = str(suspended_key or "")
        if not self._preview_service_active:
            return True
        self._restore_pre_list_service()
        if not self._preview_service_active:
            return True
        navigation = getattr(self.session, "nav", None)
        stopped = False
        if navigation is not None:
            try:
                navigation.stopService()
                stopped = True
            except Exception:
                pass
        if stopped:
            self._pre_list_restored = True
            self._clear_preview_service_state()
        # If both operations failed, retain ownership so close/stop can retry;
        # falsely clearing it would make a still-playing hidden stream orphaned.
        return not self._preview_service_active

    def _release_preview_for_exit(self):
        """Relinquish decoder ownership before the list actually closes."""
        if self._preview_service_active:
            suspended_key = ""
            if VideoWindow is None:
                suspended_key = (
                    self._preview_auto_suspended_key
                    or self._playing_preview_key
                )
            return self._relinquish_hidden_preview(suspended_key)
        self._restore_pre_list_service()
        return not self._preview_service_active

    def _restore_pre_list_service(self):
        """Restore the service present before this list, at most once."""
        cancel_startup = getattr(self, "_cancel_live_startup", None)
        if callable(cancel_startup):
            cancel_startup()
        self._cancel_preview_resolution(clear_target=True)
        self._suspend_preview_pig()
        if self._pre_list_restored or not self._preview_service_active:
            return
        navigation = getattr(self.session, "nav", None)
        restored = False
        if navigation is not None:
            if self._pre_list_reference is None:
                try:
                    navigation.stopService()
                    restored = True
                except Exception:
                    pass
            else:
                try:
                    _play_service(navigation, self._pre_list_reference)
                    restored = True
                except Exception:
                    pass
        if restored:
            self._pre_list_restored = True
            self._clear_preview_service_state()

    def _stop(self):
        self._release_preview_for_exit()
        GTAsyncListScreen._stop(self)
        with self._live_more_worker_events_lock:
            active_catalog_tokens = tuple(self._live_more_worker_tokens)
            self._live_more_token = None
            self._live_more_results = {}
        for token in active_catalog_tokens:
            _cancel_worker_token(token)
        self._live_catalog_generation += 1
        self._live_more_loading = False
        self._cancel_selected_detail(clear_selection=True)
        self._cancel_live_active_picon_fetch(clear_target=True)
        self._clear_live_active_picon()
        with self._preview_link_lock:
            self._preview_generation += 1
        self._cancel_preview_resolution(clear_target=True)
        for timer in (
            self._timer,
            self._epg_clock_timer,
            self._preview_dwell_timer,
            self._preview_link_timer,
            self._preview_video_timer,
            self._live_active_picon_timer,
            self._live_active_picon_poll_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass


class GTEpisodeListScreen(_FavoriteSupport, GTAsyncListScreen):
    def __init__(
        self,
        session,
        account,
        series,
        client=None,
        favorite_store=None,
    ):
        self.series = series
        self.client = client or content_client_for(account)
        self._init_favorite_support(favorite_store)
        self._footer_layout_items = EPISODE_FOOTER_ITEMS
        self.skin = _episode_list_skin()
        self.page_size = EPISODE_PAGE_SIZE
        screen_width, screen_height, px = _scale()
        margin = px(64)
        self._list_scroll_geometry = (
            margin + (screen_width - (margin * 2)) - px(50),
            px(224),
            px(8),
            px(613),
        )
        GTAsyncListScreen.__init__(
            self,
            session,
            "GTEpisodeListScreen",
            series.name,
            _("{}  >  Series  >  Select season and episode").format(
                account.display_name
            ),
            frameless_footer=True,
        )
        self._dynamic_episode_header_text = series.name
        self._episode_header_preferred_font = font_px(
            px,
            34,
            role="title",
        )
        self._episode_header_min_font = font_px(
            px,
            34,
            role="title",
            value="standard",
        )
        self._refit_episode_header()
        self["screen_label"].setText(_("EPISODES"))
        for color in ("cyan", "magenta"):
            self["episode_panel_accent_{}".format(color)] = Label("")
        self["episode_caption"] = Label(_("EPISODES"))
        self["episode_caption_accent"] = Label("")
        for index in range(EPISODE_PAGE_SIZE):
            self["episode_row_bg_{}".format(index)] = Label("")
            self["episode_number_{}".format(index)] = Label("")
            self["episode_name_{}".format(index)] = Label("")
            for edge in ("fill", "top", "bottom", "left", "right"):
                self[
                    "episode_focus_{}_{}".format(index, edge)
                ] = Label("")
        self._clear_episode_rows()
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "red": self.toggle_series_favorite,
                "green": self.start_load,
            },
            -1,
        )
        self["channel_actions"] = ActionMap(
            ["MoviePlayerActions"],
            {
                "channelUp": self.previous_page,
                "channelDown": self.next_page,
            },
            -1,
        )
        if hasattr(self, "onShown"):
            self.onShown.append(self._favorite_screen_shown)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refit_episode_header)

    def _refit_episode_header(self):
        fit_dynamic_text(
            self["header"],
            self._dynamic_episode_header_text,
            max_lines=1,
            preferred_size=self._episode_header_preferred_font,
            min_size=self._episode_header_min_font,
            fallback_chars=65,
        )

    def _change_episode_page(self, direction):
        if not self._entries or self._loading:
            return
        current_start = int(self.selected_index / self.page_size) * self.page_size
        last_start = int((len(self._entries) - 1) / self.page_size) * self.page_size
        target_start = max(
            0,
            min(current_start + (int(direction) * self.page_size), last_start),
        )
        if target_start == current_start:
            return
        row = self.selected_index - current_start
        target_count = min(self.page_size, len(self._entries) - target_start)
        self.selected_index = target_start + min(row, target_count - 1)
        self._refresh()

    def previous_page(self):
        self._change_episode_page(-1)

    def next_page(self):
        self._change_episode_page(1)

    def start_load(self):
        if self._loading or self._closed:
            return
        self._clear_episode_rows()
        GTAsyncListScreen.start_load(self)
        self["items"].setText("")

    def load_items(self):
        entries = list(self.client.load_episodes(self.series.stream_id) or [])
        for episode in entries:
            episode.parent_id = str(self.series.stream_id)
            episode.favorite_parent = self.series
            if not getattr(episode, "icon", ""):
                episode.icon = getattr(self.series, "icon", "")
        return entries

    def empty_message(self):
        return N_("No episodes were found for this series.")

    def _refresh(self):
        self["items"].setText("")
        if self._error:
            self._clear_episode_rows()
            ellipsize_dynamic_text(
                self["message"],
                _(self._error),
                fallback_chars=80,
            )
            hide_scrollbar(self, "list_scroll")
            return
        if not self._entries:
            self._clear_episode_rows()
            self["message"].setText(_(self.empty_message()))
            hide_scrollbar(self, "list_scroll")
            return
        self.selected_index %= len(self._entries)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start : page_start + self.page_size]
        plain_lines = []
        for row_index in range(self.page_size):
            row_bg = self["episode_row_bg_{}".format(row_index)]
            number = self["episode_number_{}".format(row_index)]
            name = self["episode_name_{}".format(row_index)]
            if row_index < len(page_items):
                item = page_items[row_index]
                absolute_index = page_start + row_index
                item_name = str(self.item_text(item) or "")
                number.setText("{:04d}.".format(absolute_index + 1))
                rendered_name = ellipsize_dynamic_text(
                    name,
                    item_name,
                    fallback_chars=80,
                )
                plain_lines.append(rendered_name)
                row_bg.show()
                number.show()
                name.show()
                selected = absolute_index == self.selected_index
            else:
                number.setText("")
                name.setText("")
                row_bg.hide()
                number.hide()
                name.hide()
                selected = False
            for edge in ("fill", "top", "bottom", "left", "right"):
                focus = self[
                    "episode_focus_{}_{}".format(row_index, edge)
                ]
                if selected:
                    focus.show()
                else:
                    focus.hide()
        self["items"].setText("\n".join(plain_lines))
        self["message"].setText(
            "{}  |  {}/{}".format(
                len(self._entries), self.selected_index + 1, len(self._entries)
            )
        )
        update_scrollbar(
            self,
            "list_scroll",
            len(self._entries),
            self.selected_index,
            self.page_size,
            self._list_scroll_geometry,
        )

    def _clear_episode_rows(self):
        for row_index in range(EPISODE_PAGE_SIZE):
            for name in ("row_bg", "number", "name"):
                widget = self[
                    "episode_{}_{}".format(name, row_index)
                ]
                if name != "row_bg":
                    widget.setText("")
                widget.hide()
            for edge in ("fill", "top", "bottom", "left", "right"):
                self[
                    "episode_focus_{}_{}".format(row_index, edge)
                ].hide()

    def open_selected(self):
        if not self._entries or self._loading:
            return
        open_extplayer(
            self.session,
            self.client,
            self._entries[self.selected_index],
            self._entries,
            self.selected_index,
            owner=self,
        )

    def toggle_series_favorite(self):
        added = self._toggle_favorite_item(self.series)
        ellipsize_dynamic_text(
            self["message"],
            self._favorite_message(self.series, added),
            fallback_chars=80,
        )


def build_extplayer_reference(
    url,
    title,
    content_type="movie",
    service_type=None,
):
    try:
        from enigma import eServiceReference
    except ImportError:
        raise ContentError(
            N_("Enigma2 player service support is unavailable")
        )
    if service_type is None:
        try:
            service_type = load_player_settings().service_type_for(content_type)
        except Exception:
            service_type = 4097 if content_type == "live" else 5002
    try:
        service_type = int(service_type)
    except (TypeError, ValueError, OverflowError):
        service_type = 4097 if content_type == "live" else 5002
    if content_type == "live" and service_type == 1:
        try:
            parsed = urlsplit(str(url or "").split("#", 1)[0])
            scheme = parsed.scheme.lower()
            path = parsed.path.lower()
            explicit_http_ts = (
                scheme in ("http", "https")
                and path.endswith((".ts", ".mpegts"))
            )
        except (TypeError, ValueError):
            explicit_http_ts = False
        if not explicit_http_ts:
            # Native DVB type 1 is only predictable for an explicit HTTP
            # MPEG-TS URL. Temporary links can be extensionless HLS, and
            # UDP/RTP/RTSP support varies by image, so route every
            # ambiguous live address through the portable 4097 service.
            service_type = 4097
    reference = eServiceReference(service_type, 0, url)
    set_name = getattr(reference, "setName", None)
    if set_name is not None:
        set_name(str(title or "GT IPTV"))
    return reference


def reference_service_type(reference, fallback=5002):
    for attribute in ("type", "service_type"):
        value = getattr(reference, attribute, None)
        try:
            value = value() if callable(value) else value
            return int(value)
        except (TypeError, ValueError, OverflowError):
            pass
    getter = getattr(reference, "getType", None)
    if getter is not None:
        try:
            return int(getter())
        except (TypeError, ValueError, OverflowError):
            pass
    return int(fallback)


def reference_stream_url(reference):
    """Return the original stream URL without depending on one image API."""
    if reference is None:
        return ""
    for name in ("getPath", "getUrl"):
        getter = getattr(reference, name, None)
        if getter is None:
            continue
        try:
            value = getter()
        except Exception:
            value = ""
        if value:
            return str(value)
    for name in ("url", "path"):
        value = getattr(reference, name, "")
        try:
            value = value() if callable(value) else value
        except Exception:
            value = ""
        if value:
            return str(value)
    return ""


def _play_service(navigation, reference):
    """Start a service and normalise Enigma2's image-specific return value."""
    result = navigation.playService(reference)
    # Native Enigma2 uses 0 for success and a non-zero integer for failure.
    # Several Enigma2 images return None instead, which is accepted.
    if isinstance(result, int) and result != 0:
        raise RuntimeError("playService returned {}".format(result))
    return result


_ARCHIVE_RETRY_PENDING = object()
_ARCHIVE_PTS_MODULO = 1 << 33
_ARCHIVE_PTS_MASK = _ARCHIVE_PTS_MODULO - 1


class _ArchivePlaybackWatchdog(object):
    """Bound one TV-archive start to verified decoder progress and one replay.

    A successful ``playService`` call only means that Enigma2 accepted the
    reference.  It does not mean that ServiceApp/GStreamer received media.
    This monitor therefore accepts only an advancing playback position or an
    advancing decoder PTS.  Duration, metadata and ``evStart`` are deliberately
    insufficient: all three can be published by a stalled pipeline.

    Before that replay an optional provider hook may mint one fresh URL (for
    example an expiring Stalker archive ticket).  The sole replay keeps the
    exact engine selected by the user.  When the provider cannot supply a new
    candidate, playback fails without automatically switching 5002/4097.
    """

    def __init__(
        self,
        player,
        timer=None,
        clock=None,
        thread_factory=None,
        snapshot_reader=None,
    ):
        self.player = player
        self.navigation = getattr(player.session, "nav", None)
        self.timer = timer or eTimer()
        self.clock = clock or time.monotonic
        self.thread_factory = thread_factory or threading.Thread
        self.snapshot_reader = snapshot_reader or self._default_snapshot
        _connect_timer(self.timer, self.tick)

        self.phase = "idle"
        self.generation = -1
        self.reference = None
        self.reference_identity = ""
        self.reference_seen = False
        self.attempt = 0
        self.deadline = 0.0
        self.overall_deadline = 0.0
        self.settle_deadline = 0.0
        self.acquire_not_before = 0.0
        self.hard_failure = ""

        self.provider_retry_used = False
        self.latest_url = ""
        self.pending_reference = None
        self.pending_kind = ""
        self.pending_release_fenced = False
        self.decoder_release_fenced = False
        self.release_not_before = 0.0
        self.previous_service = getattr(
            player,
            "_archive_previous_service",
            None,
        )
        self.baseline_pts = getattr(
            player,
            "_archive_previous_pts",
            (None, None),
        )
        self.baseline_position = getattr(
            player,
            "_archive_previous_position",
            None,
        )
        self.target_service = None
        self.target_reference_confirmed = False
        self.service_reference_status = "unknown"
        self.position_target_transition = False
        self.pts_target_transition = False
        self.position_tail_value = self.baseline_position
        self.pts_tail_source, self.pts_tail_value = self.baseline_pts

        self.position_observation = None
        self.pts_observation = None
        self.result = _ARCHIVE_RETRY_PENDING
        self.result_lock = threading.Lock()
        self.resolve_serial = 0
        self.resolve_token = None

        self.listening = False
        self.dispatching = False
        self.cleanup_pending = False

    @staticmethod
    def _identity(reference):
        if reference is None:
            return ""
        for name in ("toCompareString", "toString"):
            getter = getattr(reference, name, None)
            if not callable(getter):
                continue
            try:
                value = getter()
            except Exception:
                value = ""
            if value:
                return str(value)
        return "{}:{}".format(
            reference_service_type(reference, 5002),
            reference_stream_url(reference),
        )

    @staticmethod
    def _default_snapshot(service):
        return _decoder_media_snapshot(service, iServiceInformation)

    @staticmethod
    def _normalise_pts(value):
        try:
            value = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if (
            value < 0
            or value > _ARCHIVE_PTS_MASK
            or value in (0xFFFFFFFF, _ARCHIVE_PTS_MASK)
        ):
            return None
        return value

    @staticmethod
    def _clock_delta(previous, current, allow_wrap=False):
        previous = int(previous)
        current = int(current)
        if current >= previous:
            return current - previous
        if allow_wrap:
            edge = 90000 * 10
            if previous >= _ARCHIVE_PTS_MODULO - edge and current <= edge:
                return (current - previous) & _ARCHIVE_PTS_MASK
        return None

    def _update_position_transition(self, value, sampled_at):
        if value is None or self.position_target_transition:
            return
        if self.previous_service is None or self.position_tail_value is None:
            self.position_target_transition = True
            return
        if self.decoder_release_fenced and sampled_at >= self.acquire_not_before:
            # A retry is committed only after stopService plus the bounded
            # release interval.  With an exact target sServiceref, advancing
            # service-local seek after the stale-tail guard is valid evidence
            # even when both services happened to start at position zero.
            self.position_target_transition = True
            self.position_observation = None
            return
        if (
            sampled_at >= self.acquire_not_before
            and (
                self.target_reference_confirmed
                or self.decoder_release_fenced
            )
            and self.baseline_position <= ARCHIVE_POSITION_NEAR_ZERO_TICKS
            and value <= ARCHIVE_POSITION_NEAR_ZERO_TICKS
        ):
            # A stalled attempt and the replacement can both expose position
            # zero.  If the exact target reference remains parked at its start
            # through the full stale-tail guard, allow subsequent service-local
            # advances to prove playback even when decoder PTS is unavailable.
            self.position_target_transition = True
            self.position_observation = None
            return
        delta = self._clock_delta(
            self.position_tail_value,
            value,
            allow_wrap=False,
        )
        if delta is None or delta > ARCHIVE_DECODER_TRANSITION_TICKS:
            self.position_target_transition = True
            self.position_observation = None
            return
        # Follow a plausible old-decoder tail sample by sample.  Comparing
        # forever with the original baseline would misclassify ten seconds of
        # ordinary stale progress as a large decoder discontinuity.
        self.position_tail_value = value

    def _update_pts_transition(self, source, value):
        if value is None or self.pts_target_transition:
            return
        if self.previous_service is None:
            self.pts_target_transition = True
            return
        if self.pts_tail_value is None:
            # No decoder clock existed before the zap; the first target-bound
            # clock is itself a media transition, including a legitimate 0.
            self.pts_target_transition = True
            return
        if source != self.pts_tail_source:
            # ioctl/proc availability can change the reported sensor without
            # changing the underlying decoder.  Establish a fresh rolling
            # probe instead of treating a source label as a decoder reset.
            self.pts_tail_source = source
            self.pts_tail_value = value
            self.pts_observation = None
            return
        delta = self._clock_delta(
            self.pts_tail_value,
            value,
            allow_wrap=True,
        )
        if delta is None or delta > ARCHIVE_DECODER_TRANSITION_TICKS:
            self.pts_target_transition = True
            self.pts_observation = None
            return
        self.pts_tail_source = source
        self.pts_tail_value = value

    def _listen(self):
        if self.listening or self.navigation is None:
            return
        try:
            self.navigation.event.append(self.service_event)
            self.listening = True
        except Exception:
            # Timer/decoder polling remains sufficient on images without the
            # public navigation event list.
            self.listening = False

    def _unlisten(self):
        if not self.listening:
            return
        try:
            self.navigation.event.remove(self.service_event)
        except (AttributeError, ValueError):
            pass
        self.listening = False

    def _screen_matches(self):
        player = self.player
        return bool(
            player is not None
            and not getattr(player, "_closed", False)
            and getattr(player, "_started", False)
            and getattr(getattr(player, "current_item", None), "content_type", "")
            == "catchup"
            and int(getattr(player, "_generation", -2)) == self.generation
            and self._identity(getattr(player, "reference", None))
            == self.reference_identity
        )

    def _current_reference_identity(self):
        if self.navigation is None:
            return ""
        getter = getattr(
            self.navigation,
            "getCurrentlyPlayingServiceReference",
            None,
        )
        if not callable(getter):
            getter = getattr(
                self.navigation,
                "getCurrentlyPlayingServiceOrGroup",
                None,
            )
        try:
            return self._identity(getter()) if callable(getter) else ""
        except Exception:
            return ""

    def _owns_service(self):
        if not self._screen_matches():
            return False
        matches = self._current_reference_identity() == self.reference_identity
        if matches:
            self.reference_seen = True
        return matches

    def _reference_is_settling(self):
        return bool(
            not self.reference_seen
            and self._screen_matches()
            and self.clock() < self.settle_deadline
        )

    def _released_service_is_owned(self):
        if not self._screen_matches():
            return False
        current = self._current_reference_identity()
        return not current or current == self.reference_identity

    def _current_service(self):
        getter = (
            getattr(self.navigation, "getCurrentService", None)
            if self.navigation is not None
            else None
        )
        try:
            return getter() if callable(getter) else None
        except Exception:
            return None

    def _reference_strings(self, reference):
        values = set()
        for name in ("toCompareString", "toString"):
            getter = getattr(reference, name, None)
            if not callable(getter):
                continue
            try:
                value = getter()
            except Exception:
                value = ""
            if value:
                values.add(str(value))
        identity = self._identity(reference)
        if identity:
            values.add(identity)
        return values

    @staticmethod
    def _canonical_service_reference(value):
        """Return engine/path identity using Enigma2's own ref parser.

        Service-reference text contains colon-delimited, percent-encoded
        fields, so hand splitting is not safe.  ``eServiceReference`` already
        knows each image's representation and exposes the original path.
        """
        reference = value
        if isinstance(value, str):
            try:
                from enigma import eServiceReference

                reference = eServiceReference(value)
            except Exception:
                return None
        path = reference_stream_url(reference).strip()
        if not path:
            return None
        try:
            service_type = reference_service_type(reference, -1)
        except Exception:
            return None
        if service_type < 0:
            return None
        return service_type, path

    def _service_reference_state(self, service):
        """Return True/False for an advertised service ref, else None."""
        key = getattr(iServiceInformation, "sServiceref", None)
        if service is None or key is None:
            return None
        try:
            info = service.info()
            getter = getattr(info, "getInfoString", None)
            value = str(getter(key) if callable(getter) else "").strip()
        except Exception:
            value = ""
        if not value:
            return None
        if value in self._reference_strings(self.reference):
            return True
        advertised = self._canonical_service_reference(value)
        target = self._canonical_service_reference(self.reference)
        if advertised is not None and target is not None:
            return advertised == target
        return False

    def _target_service_ready(self, service):
        if service is None:
            self.service_reference_status = "absent"
            return False
        reference_state = self._service_reference_state(service)
        if reference_state is False:
            self.service_reference_status = "mismatch"
            return False
        if reference_state is True:
            self.service_reference_status = "target"
            if not self.target_reference_confirmed:
                # Observations collected while the decoder proxy was
                # uncorrelated cannot be promoted retroactively.
                self.position_observation = None
                self.pts_observation = None
                self.pts_target_transition = False
            self.target_reference_confirmed = True
            self.target_service = service
        else:
            self.service_reference_status = "unknown"
            if (
                service is self.previous_service
                and not self.decoder_release_fenced
            ):
                # Navigation may expose the new reference before
                # getCurrentService has stopped returning the old decoder.
                return False
        if self.target_service is None:
            self.target_service = service
        elif service is not self.target_service and reference_state is not True:
            return False
        return True

    @classmethod
    def _snapshot_pts(cls, snapshot):
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        video = cls._normalise_pts(snapshot.get("video_pts"))
        if video is not None:
            return snapshot.get("video_pts_source") or "video", video
        audio = cls._normalise_pts(snapshot.get("audio_pts"))
        return ("audio", audio) if audio is not None else (None, None)

    @staticmethod
    def _position_ticks(service):
        try:
            seeker = service.seek()
            error, ticks = seeker.getPlayPosition()
            if int(error) != 0:
                return None
            ticks = int(ticks)
            return ticks if ticks >= 0 else None
        except Exception:
            return None

    def _observe(self, observation, source, value, sampled_at, allow_wrap):
        if value is None:
            return None, False
        if observation is None or observation["source"] != source:
            return {
                "source": source,
                "value": int(value),
                "at": sampled_at,
                "advances": 0,
                "ticks": 0,
            }, False
        elapsed = max(0.0, sampled_at - float(observation["at"]))
        delta = self._clock_delta(
            observation["value"],
            value,
            allow_wrap=allow_wrap,
        )
        max_step = max(450000, int(90000 * max(elapsed, 1.0) * 4))
        if delta is None or delta > max_step:
            return {
                "source": source,
                "value": int(value),
                "at": sampled_at,
                "advances": 0,
                "ticks": 0,
            }, False
        observation["value"] = int(value)
        observation["at"] = sampled_at
        if delta > 0:
            observation["advances"] += 1
            observation["ticks"] += delta
        acquired = bool(
            sampled_at >= self.acquire_not_before
            and delta > 0
            and observation["advances"] >= 2
            and observation["ticks"] >= 45000
        )
        return observation, acquired

    def _sample(self):
        if not self._owns_service() or self.clock() < self.settle_deadline:
            return False
        sampled_at = self.clock()
        service = self._current_service()
        if not self._target_service_ready(service):
            self.position_observation = None
            self.pts_observation = None
            return False
        position = self._position_ticks(service) if service is not None else None
        self._update_position_transition(position, sampled_at)
        self.position_observation, position_advanced = self._observe(
            self.position_observation,
            "position",
            position,
            sampled_at,
            False,
        )
        # seek() is service-local, but some images return a new SWIG proxy for
        # the previous decoder after nav has already changed its reference.
        # Require either a reset/absence transition or the committed decoder
        # release fence before service-local progress can count.
        position_advanced = bool(
            position_advanced
            and self.position_target_transition
        )

        try:
            snapshot = self.snapshot_reader(service) if service is not None else {}
        except Exception:
            snapshot = {}
        pts_source, pts_value = self._snapshot_pts(snapshot)
        if self.target_reference_confirmed:
            self._update_pts_transition(pts_source, pts_value)
        self.pts_observation, pts_advanced = self._observe(
            self.pts_observation,
            pts_source,
            pts_value,
            sampled_at,
            True,
        )
        # No evidence gathered before the stale-tail guard can count later.
        # In particular, a clock that moves twice then freezes must not become
        # "acquired" merely because a zero-delta sample crosses three seconds.
        if sampled_at < self.acquire_not_before:
            self.position_observation = None
            self.pts_observation = None
            return False
        # Decoder PTS can be a global device/proc reading, so accept it only
        # after exact target correlation plus a one-time decoder transition.
        pts_advanced = bool(
            pts_advanced
            and self.target_service is not None
            and self.pts_target_transition
            and sampled_at >= self.acquire_not_before
        )
        if not (position_advanced or pts_advanced):
            return False
        evidence = "position" if position_advanced else "pts"
        self._log("acquired", evidence)
        self._finish("acquired")
        return True

    def _log(self, outcome, reason):
        log_event(
            "playback",
            "TV archive startup outcome={} attempt={} engine={} reason={}".format(
                outcome,
                self.attempt,
                reference_service_type(self.reference, 5002),
                reason,
            ),
        )

    def start(self):
        if self.phase != "idle" or self.navigation is None:
            return False
        if (
            getattr(getattr(self.player, "current_item", None), "content_type", "")
            != "catchup"
        ):
            return False
        self.generation = int(getattr(self.player, "_generation", -1))
        self.reference = getattr(self.player, "reference", None)
        self.reference_identity = self._identity(self.reference)
        self.latest_url = reference_stream_url(self.reference)
        if not self.reference_identity or not self.latest_url:
            return False
        self.overall_deadline = self.clock() + ARCHIVE_STARTUP_OVERALL_SECONDS
        self._listen()
        self._begin_watch()
        return True

    def _begin_watch(self):
        now = self.clock()
        self.phase = "watch"
        self.deadline = min(now + ARCHIVE_STARTUP_SECONDS, self.overall_deadline)
        self.settle_deadline = now + ARCHIVE_STARTUP_SETTLE_SECONDS
        self.acquire_not_before = now + ARCHIVE_STARTUP_ACQUIRE_SECONDS
        self.reference_seen = False
        self.target_service = None
        self.target_reference_confirmed = False
        self.service_reference_status = "unknown"
        self.position_target_transition = False
        self.pts_target_transition = False
        self.position_tail_value = self.baseline_position
        self.pts_tail_source, self.pts_tail_value = self.baseline_pts
        self.hard_failure = ""
        self.position_observation = None
        self.pts_observation = None
        self._log("armed", "monitoring")
        self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)

    @staticmethod
    def _event_values(*names):
        if _PlayableServiceEvents is None:
            return set()
        values = set()
        for name in names:
            value = getattr(_PlayableServiceEvents, name, None)
            if isinstance(value, int):
                values.add(value)
        return values

    def service_event(self, event):
        self.dispatching = True
        try:
            if self.phase not in ("watch", "resolve") or not self._owns_service():
                return
            hard = self._event_values("evTuneFailed", "evEOF", "evStopped")
            user = (
                getattr(_PlayableServiceEvents, "evUser", None)
                if _PlayableServiceEvents is not None
                else None
            )
            if isinstance(user, int):
                hard.update((user + 10, user + 11, user + 12))
            if event in hard:
                # Navigation events do not carry the service reference which
                # emitted them.  A late evStopped/evEOF from the service that
                # was replaced can therefore arrive while nav already exposes
                # the archive reference.  Keep fatal events as a diagnostic
                # hint and request an immediate decoder sample, but never let
                # an uncorrelated event shorten the twelve-second attempt.
                self.hard_failure = "service-event"
                self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)
                return
            # evStart/evUpdatedInfo/video-size events merely request an early
            # decoder sample; they are never accepted as playback by themselves.
            progress = self._event_values(
                "evStart", "evUpdatedInfo", "evVideoSizeChanged"
            )
            if event in progress:
                self._sample()
        finally:
            self.dispatching = False

    def _provider_retry(self):
        self.provider_retry_used = True
        resolver = getattr(
            getattr(self.player, "client", None),
            "decoder_retry_playback_url",
            None,
        )
        if not callable(resolver):
            self._terminal_failure("provider-unavailable")
            return
        remaining = self.overall_deadline - self.clock()
        if remaining <= 0:
            self._terminal_failure("deadline")
            return
        token = _WorkerToken(min(ARCHIVE_PROVIDER_RETRY_SECONDS, remaining))
        self.resolve_token = token
        self.resolve_serial += 1
        serial = self.resolve_serial
        with self.result_lock:
            self.result = _ARCHIVE_RETRY_PENDING
        self.phase = "resolve"

        def resolve():
            url = ""
            reason = ""
            try:
                url = str(
                    resolver(
                        self.player.current_item,
                        self.latest_url,
                        cancel_event=token.cancel_event,
                        deadline=token.deadline,
                    )
                    or ""
                ).strip()
            except ContentError as error:
                reason = str(getattr(error, "reason", "") or "")
                url = ""
            except Exception:
                reason = "unexpected"
                url = ""
            with self.result_lock:
                if (
                    serial == self.resolve_serial
                    and self.phase == "resolve"
                    and token.active()
                ):
                    self.result = (url, reason)

        try:
            worker = self.thread_factory(target=resolve)
            worker.daemon = True
            worker.start()
        except Exception:
            _cancel_worker_token(token)
            self.resolve_token = None
            self._terminal_failure("provider-worker")
            return
        self._log("retry", "provider")
        self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)

    def _prepare_provider_replay(self, url):
        if not self._screen_matches() or self.clock() >= self.overall_deadline:
            self._terminal_failure("deadline")
            return
        if not self._owns_service():
            self.cancel()
            return
        try:
            candidate = build_extplayer_reference(
                url,
                getattr(self.player.current_item, "name", "GT IPTV"),
                "catchup",
                # R100 deliberately preserves the exact selected engine.
                service_type=reference_service_type(self.reference, 4097),
            )
        except Exception:
            self._terminal_failure("reference-rejected")
            return
        _cancel_worker_token(self.resolve_token)
        self.resolve_token = None
        self.latest_url = str(url)
        self.pending_reference = candidate
        self.pending_kind = "provider"
        self.pending_release_fenced = False
        self.phase = "release"
        self.previous_service = self._current_service()
        try:
            previous_snapshot = self.snapshot_reader(self.previous_service)
        except Exception:
            previous_snapshot = {}
        self.baseline_pts = self._snapshot_pts(previous_snapshot)
        self.baseline_position = self._position_ticks(self.previous_service)
        try:
            self.navigation.stopService()
        except Exception:
            self._terminal_failure("release-failed")
            return
        self.pending_release_fenced = True
        self.release_not_before = (
            self.clock() + ARCHIVE_DECODER_RELEASE_MS / 1000.0
        )
        self.timer.start(ARCHIVE_DECODER_RELEASE_MS, True)

    def _commit_replay(self):
        if self.clock() >= self.overall_deadline:
            self._terminal_failure("deadline")
            return
        if not self._released_service_is_owned():
            self.cancel()
            return
        candidate = self.pending_reference
        kind = self.pending_kind
        try:
            if candidate is None:
                raise ValueError("missing archive retry reference")
            _play_service(self.navigation, candidate)
        except Exception:
            self.pending_reference = None
            self.pending_kind = ""
            self._terminal_failure("play-service-rejected")
            return

        player = self.player
        player.reference = candidate
        player._generation += 1
        self.generation = int(player._generation)
        self.reference = candidate
        self.reference_identity = self._identity(candidate)
        self.latest_url = reference_stream_url(candidate)
        self.decoder_release_fenced = self.pending_release_fenced
        self.pending_reference = None
        self.pending_kind = ""
        self.pending_release_fenced = False
        self.attempt += 1
        subtitle_controller = getattr(player, "_subtitle_controller", None)
        if subtitle_controller is not None:
            try:
                subtitle_controller.reset_for_service_change()
            except Exception:
                pass
        player._after_zap()
        # playService() accepting a reference is not decoder success.  Keep
        # this distinct from the later ``acquired`` outcome, which requires
        # service-local position or decoder PTS progress.
        self._log("replay-accepted", kind)
        self._begin_watch()

    def _recover(self, reason):
        if self.clock() >= self.overall_deadline:
            self._terminal_failure("deadline")
            return
        self._log("stalled", reason)
        if not self.provider_retry_used:
            self._provider_retry()
            return
        self._terminal_failure("retry-exhausted")

    def tick(self):
        if self.cleanup_pending:
            self.cleanup_pending = False
            self._unlisten()
        if self.phase == "watch":
            if not self._screen_matches():
                self.cancel()
                return
            if not self._owns_service():
                if self._reference_is_settling():
                    self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)
                else:
                    self.cancel()
                return
            if self.clock() >= self.overall_deadline:
                self._terminal_failure("deadline")
                return
            if self._sample():
                return
            now = self.clock()
            if now >= self.deadline:
                if (
                    not self.target_reference_confirmed
                    and self.service_reference_status == "unknown"
                    and not self.position_target_transition
                ):
                    # Some images expose no usable sServiceref for eServiceMP3.
                    # Without a safe service-local seek transition ownership
                    # remains unknowable: do not tear down playback merely
                    # because a global/proxy sensor looked suspicious.
                    self._log("inconclusive", "service-reference-unavailable")
                    self._finish("inconclusive")
                    return
                self._recover(self.hard_failure or "timeout")
                return
            self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)
            return

        if self.phase == "resolve":
            if not self._screen_matches():
                self.cancel()
                return
            if not self._owns_service():
                if self._reference_is_settling():
                    self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)
                else:
                    self.cancel()
                return
            if self.clock() >= self.overall_deadline:
                self._terminal_failure("deadline")
                return
            if self._sample():
                return
            token = self.resolve_token
            with self.result_lock:
                result = self.result
            if result is _ARCHIVE_RETRY_PENDING:
                if self.clock() >= self.overall_deadline:
                    _cancel_worker_token(token)
                    self.resolve_token = None
                    self._terminal_failure("deadline")
                elif token is None:
                    self.cancel()
                elif token.expired():
                    cancelled = token.cancel_event.is_set() and not token.past_deadline()
                    _cancel_worker_token(token)
                    self.resolve_token = None
                    if cancelled:
                        self.cancel()
                    else:
                        self._terminal_failure("provider-deadline")
                else:
                    self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)
                return
            self.resolve_token = None
            url, reason = result
            terminal_reasons = {
                ContentError.ACCESS_DENIED,
                ContentError.RATE_LIMITED,
                ContentError.CANCELLED,
                ContentError.DEADLINE,
            }
            if reason in terminal_reasons:
                self._log("provider-result", "terminal")
                self._terminal_failure("provider-{}".format(reason))
                return
            if url and url != self.latest_url:
                # Replay the fresh provider ticket through the exact engine
                # already selected by the user.  R100 never toggles 5002/4097.
                self.latest_url = url
                self._log("provider-result", "fresh-ticket")
                self._prepare_provider_replay(url)
                return
            elif url:
                self._log("provider-result", "same-route")
            elif reason:
                self._log("provider-result", "unavailable")
            else:
                # Xtream routes are deterministic, so there is no fresh
                # provider candidate and no automatic engine fallback.
                self._log("provider-result", "same-route")
            self._terminal_failure("provider-unavailable")
            return

        if self.phase == "release":
            if not self._released_service_is_owned():
                self.cancel()
                return
            if self.clock() >= self.overall_deadline:
                self._terminal_failure("deadline")
                return
            remaining = self.release_not_before - self.clock()
            if remaining > 0:
                self.timer.start(max(1, int(round(remaining * 1000))), True)
                return
            self._commit_replay()

    def _finish(self, phase):
        self.phase = phase
        _cancel_worker_token(self.resolve_token)
        self.resolve_token = None
        self.pending_reference = None
        self.pending_kind = ""
        self.pending_release_fenced = False
        try:
            self.timer.stop()
        except Exception:
            pass
        if self.dispatching:
            self.cleanup_pending = True
            self.timer.start(1, True)
        else:
            self.cleanup_pending = False
            self._unlisten()

    def _terminal_failure(self, reason):
        if self.phase in ("failed", "cancelled", "acquired", "inconclusive"):
            return
        self._log("failed", reason)
        self._finish("failed")
        callback = getattr(self.player, "_archive_startup_failed", None)
        if callable(callback):
            callback()

    def cancel(self):
        if self.phase in ("cancelled", "acquired", "failed", "inconclusive"):
            # _finish() defers navigation-list mutation when it is executing
            # inside nav.event.  If EXIT follows before that 1ms callback, do
            # the pending cleanup here so the closed screen cannot remain held
            # alive by navigation.event.
            if self.cleanup_pending and not self.dispatching:
                self.cleanup_pending = False
                try:
                    self.timer.stop()
                except Exception:
                    pass
                self._unlisten()
            return
        self._finish("cancelled")


def _launch_extplayer(
    session,
    client,
    item,
    url,
    items=None,
    selected_index=0,
    favorite_store=None,
    favorite_parent=None,
    favorite_keys=None,
    owner=None,
    archive_initial_playback=None,
):
    reference = build_extplayer_reference(
        url, item.name, getattr(item, "content_type", "movie")
    )
    # OpenATV's MoviePlayer can leave the Session dialog stack non-modal when
    # it is launched from a nested plugin screen.  EXIT then tries to open a
    # second modal dialog and Enigma2 crashes with:
    # "Modal open are allowed only from a screen which is modal".
    #
    # Keep the configured Enigma2 service under our own player screen so
    # STOP/EXIT always returns to the stream list without a modal-stack crash.
    entries = list(items or [])
    store = PlaybackResumeStore()
    key = resume_key(client, item)
    bookmark = store.get(key)
    if bookmark:
        remember = getattr(store, "remember", None)
        if remember is not None:
            try:
                remember(key, item, resume_account_scope(client))
            except Exception:
                pass

    def launch(start_position=0):
        session.open(
            GTExternalPlayerScreen,
            reference,
            item,
            client,
            entries,
            selected_index,
            False,
            None,
            None,
            store,
            key,
            start_position,
            favorite_store=favorite_store,
            favorite_parent=favorite_parent,
            favorite_keys=favorite_keys,
            playback_owner=owner,
            archive_initial_playback=archive_initial_playback,
        )

    if not bookmark or not hasattr(session, "openWithCallback"):
        launch(0)
        return

    def resume_answer(answer):
        if answer:
            launch(bookmark["position"])
        else:
            store.clear(key)
            launch(0)

    session.openWithCallback(
        resume_answer,
        MessageBox,
        _("Resume playback from the saved position?\n\nSaved position: {}").format(
                GTExternalPlayerScreen._format_play_time(bookmark["position"])
            ),
        MessageBox.TYPE_YESNO,
        default=True,
    )


class _ArchiveInitialPlayback(object):
    """Own the one stop/wait/restore transaction before an archive link."""

    def __init__(self, session):
        self.session = session
        self.navigation = getattr(session, "nav", None)
        self.reference_getter = getattr(
            self.navigation, "getCurrentlyPlayingServiceReference", None
        )
        if not callable(self.reference_getter):
            self.reference_getter = getattr(
                self.navigation, "getCurrentlyPlayingServiceOrGroup", None
            )
        if not callable(self.reference_getter):
            raise RuntimeError("Archive navigation reference unavailable")
        # Capture before stopService clears navigation. The player must later
        # restore this reference rather than the empty slot left by release.
        self.previous_reference = self.reference_getter()
        self.released = False
        self.not_before = 0.0

    def release(self):
        self.released = True
        result = self.navigation.stopService()
        if isinstance(result, int) and result != 0:
            raise RuntimeError("Archive decoder release rejected")
        self.not_before = (
            time.monotonic() + ARCHIVE_DECODER_RELEASE_MS / 1000.0
        )
        log_event(
            "tv_archive",
            "Initial playback release accepted delay_ms={}".format(
                ARCHIVE_DECODER_RELEASE_MS
            ),
        )

    def owns_navigation(self):
        if (
            not self.released
            or getattr(self.session, "nav", None) is not self.navigation
        ):
            return False
        try:
            current = self.reference_getter()
        except Exception:
            return False
        return bool(
            current is None
            or GTStreamListScreen._same_service_reference(
                current, self.previous_reference
            )
        )

    def ready(self):
        if time.monotonic() < self.not_before or not self.owns_navigation():
            return False
        try:
            if self.reference_getter() is None:
                return True
            getter = getattr(self.navigation, "getCurrentService", None)
            # Some images retain the old reference briefly after stop. Do
            # not resolve a new portal ticket while that service is running.
            return callable(getter) and getter() is None
        except Exception:
            return False

    def restore(self):
        if not self.released:
            return True
        if not self.owns_navigation():
            # An external zap took the slot. Never overwrite that selection.
            self.released = False
            return True
        if self.previous_reference is not None:
            try:
                _play_service(self.navigation, self.previous_reference)
            except Exception as error:
                log_event("tv_archive", "Initial playback restore failed", error)
                return False
        self.released = False
        return True


class _AsyncPlaybackResolver(object):
    """Resolve provider-created links without blocking Enigma2's GUI."""

    def __init__(
        self,
        session,
        client,
        item,
        items,
        selected_index,
        owner=None,
        favorite_store=None,
        favorite_parent=None,
        favorite_keys=None,
    ):
        self.session = session
        self.client = client
        self.item = item
        self.items = list(items or [])
        self.selected_index = int(selected_index or 0)
        self.owner = owner
        self.favorite_store = favorite_store
        self.favorite_parent = favorite_parent
        self.favorite_keys = favorite_keys
        self.token = _WorkerToken(WORKER_PLAYBACK_LINK_TIMEOUT_SECONDS)
        self.result = None
        self.finished = False
        self.started = False
        self.archive_initial_playback = None
        self.archive_release_pending = False
        self.timer = eTimer()
        _connect_timer(self.timer, self._poll)

    def _set_message(self, value):
        if self.owner is None:
            return False
        try:
            self.owner["message"].setText(value)
            return True
        except Exception:
            return False

    def start(self):
        if self.started or self.finished:
            return self
        self.started = True
        previous = getattr(self.owner, "_playback_link_controller", None)
        if previous is not None and previous is not self:
            previous.cancel()
        if self.owner is not None:
            self.owner._playback_link_controller = self
            callbacks = getattr(self.owner, "onClose", None)
            if isinstance(callbacks, list) and self.cancel not in callbacks:
                callbacks.append(self.cancel)
        self._set_message(_("Please wait"))
        if getattr(self.item, "content_type", "") == "catchup":
            try:
                self.archive_initial_playback = _ArchiveInitialPlayback(
                    self.session
                )
                self.archive_initial_playback.release()
            except Exception as error:
                self._finish(None, error)
                return self
            self.archive_release_pending = True
            self.timer.start(ARCHIVE_DECODER_RELEASE_MS, True)
            return self
        self._start_worker()
        return self

    def _start_worker(self):
        try:
            worker = threading.Thread(target=self._run)
            worker.daemon = True
            worker.start()
        except Exception as error:
            self._finish(None, error)
            return
        self.timer.start(75, True)

    def _run(self):
        try:
            url = _bounded_client_call(
                self.client,
                self.token,
                lambda: self.client.playback_url(self.item),
            )
            self.result = (url, None)
        except Exception as error:
            self.result = (None, error)

    def _poll(self):
        if self.finished:
            return
        if getattr(self.owner, "_closed", False):
            self.cancel()
            return
        if self.archive_release_pending:
            preparation = self.archive_initial_playback
            if self.token.expired() or not preparation.owns_navigation():
                self._finish(None, ContentError(N_("Stream link timed out")))
                return
            if not preparation.ready():
                remaining = preparation.not_before - time.monotonic()
                self.timer.start(max(25, int(round(remaining * 1000))), True)
                return
            self.archive_release_pending = False
            log_event(
                "tv_archive", "Initial archive link resolution after release"
            )
            self._start_worker()
            return
        if self.result is None:
            if self.token.expired():
                self._finish(None, ContentError(N_("Stream link timed out")))
                return
            self.timer.start(75, True)
            return
        self._finish(*self.result)

    def _detach(self):
        if getattr(self.owner, "_playback_link_controller", None) is self:
            self.owner._playback_link_controller = None
        callbacks = getattr(self.owner, "onClose", None)
        if isinstance(callbacks, list):
            try:
                callbacks.remove(self.cancel)
            except ValueError:
                pass

    def _finish(self, url, error):
        if self.finished:
            return
        self.finished = True
        try:
            self.timer.stop()
        except Exception:
            pass
        preparation = self.archive_initial_playback
        if preparation is not None and not preparation.ready():
            error = error or RuntimeError("Archive navigation ownership changed")
        if error is not None or not url or not self.token.active():
            _cancel_worker_token(self.token)
            if preparation is None or preparation.restore():
                self._detach()
            log_event("playback", "Asynchronous stream link resolution failed", error)
            self._resolution_failed()
            return
        self._set_message("")
        try:
            _launch_extplayer(
                self.session,
                self.client,
                self.item,
                url,
                self.items,
                self.selected_index,
                favorite_store=self.favorite_store,
                favorite_parent=self.favorite_parent,
                favorite_keys=self.favorite_keys,
                owner=self.owner,
                archive_initial_playback=preparation,
            )
            # The player now owns cancellation and restoration, including an
            # EXIT that arrives before its first onShown/start_playback call.
            self.archive_initial_playback = None
            self._detach()
        except Exception as launch_error:
            if preparation is None or preparation.restore():
                self._detach()
            log_event("playback", "Resolved player launch failed", launch_error)
            self._resolution_failed()

    def _resolution_failed(self):
        resume = getattr(self.owner, "_resume_after_playback_resolution_failure", None)
        if callable(resume) and not getattr(self.owner, "_closed", False):
            try:
                resume()
            except Exception:
                pass
        message = (_("Could not open the archived programme.")
                   if getattr(self.item, "content_type", "") == "catchup"
                   else _("Could not open the stream."))
        if not self._set_message(message):
            try:
                self.session.open(MessageBox, message, MessageBox.TYPE_ERROR)
            except Exception:
                pass

    def cancel(self, *args):
        del args
        self.finished = True
        _cancel_worker_token(self.token)
        try:
            self.timer.stop()
        except Exception:
            pass
        preparation = self.archive_initial_playback
        if preparation is None or preparation.restore():
            self.archive_initial_playback = None
            self._detach()


def open_extplayer(
    session,
    client,
    item,
    items=None,
    selected_index=0,
    owner=None,
    favorite_store=None,
    favorite_parent=None,
    favorite_keys=None,
):
    if favorite_store is None:
        favorite_store = getattr(owner, "favorite_store", None)
    if favorite_parent is None:
        favorite_parent = getattr(item, "favorite_parent", None)
    if favorite_keys is None and hasattr(owner, "_favorite_keys"):
        favorite_keys = set(owner._favorite_keys)
    suspend = getattr(owner, "_suspend_for_playback", None)
    if callable(suspend):
        suspend()
    if (
        getattr(item, "content_type", "") == "catchup"
        or bool(getattr(client, "async_playback_resolution", False))
    ):
        return _AsyncPlaybackResolver(
            session,
            client,
            item,
            items,
            selected_index,
            owner=owner,
            favorite_store=favorite_store,
            favorite_parent=favorite_parent,
            favorite_keys=favorite_keys,
        ).start()
    url = client.playback_url(item)
    return _launch_extplayer(
        session,
        client,
        item,
        url,
        items,
        selected_index,
        favorite_store=favorite_store,
        favorite_parent=favorite_parent,
        favorite_keys=favorite_keys,
        owner=owner,
    )


def _connect_timer(timer, callback):
    try:
        timer.timeout.connect(callback)
    except AttributeError:
        timer.callback.append(callback)


def _read_proc_number(path, base=10):
    try:
        with open(path, "r") as handle:
            value = handle.read(32).strip()
        return int(value, base)
    except (IOError, OSError, TypeError, ValueError):
        return -1


def _read_proc_text(path):
    try:
        with open(path, "r") as handle:
            return handle.read(64).strip()
    except (IOError, OSError):
        return ""


def _normalise_video_codec(value):
    raw = str(value or "").strip()
    compact = raw.lower().replace(".", "").replace("-", "")
    if "hevc" in compact or "h265" in compact:
        return "H.265 / HEVC"
    if "h264" in compact or "avc" in compact:
        return "H.264"
    if "mpeg2" in compact:
        return "MPEG-2"
    if "mpeg4" in compact:
        return "MPEG-4"
    if "vc1" in compact:
        return "VC-1"
    if "avs2" in compact:
        return "AVS2"
    return raw[:24] if raw else "--"


def _safe_picon_url(value):
    try:
        return validate_public_url_syntax(value)["url"]
    except (NetworkSecurityError, TypeError, ValueError):
        return ""


def _provider_image_request_context(client, url):
    """Return optional provider headers for one already-validated image URL."""
    context_loader = getattr(client, "picon_request_context", None)
    if not callable(context_loader):
        return None, None
    try:
        context = context_loader(url) or {}
    except Exception:
        return None, None
    if not isinstance(context, dict):
        return None, None
    headers = context.get("headers")
    if not isinstance(headers, dict) or not headers:
        headers = None
    user_agent = str(context.get("user_agent") or "").strip() or None
    return headers, user_agent


def _download_client_image(
    client,
    url,
    destination,
    timeout=None,
    token=None,
):
    """Download artwork with credentials only when the provider permits it."""
    request_headers, user_agent = _provider_image_request_context(client, url)
    return _download_picon(
        url,
        destination,
        timeout=timeout,
        token=token,
        request_headers=request_headers,
        user_agent=user_agent,
    )


def _load_live_picon_path(client, item, worker_token=None):
    """Return one validated live logo using the shared bounded downloader."""
    if worker_token is not None:
        worker_token.check()
    url = _safe_picon_url(getattr(item, "icon", ""))
    if not url:
        return ""
    path = _cached_picon_path(url)
    if path:
        if _valid_live_picon_file(path):
            _clear_picon_failure(url)
            return path
        if not _discard_invalid_live_picon_cache(url, path):
            _mark_picon_failure(url)
            return ""

    try:
        path = _download_client_image(
            client,
            url,
            _picon_cache_path(url),
            timeout=LIVE_PICON_DOWNLOAD_TIMEOUT,
            token=worker_token,
        )
    except _WorkerCancelled:
        raise
    except Exception:
        path = ""
    if worker_token is not None:
        worker_token.check()
    if path and _valid_live_picon_file(path):
        _clear_picon_failure(url)
        return path
    if path:
        _discard_invalid_live_picon_cache(url, path)
    _mark_picon_failure(url)
    return ""


def _provider_picon_timeout(metadata_client, url):
    try:
        hostname = str(urlsplit(url).hostname or "").lower()
    except Exception:
        hostname = ""
    is_tmdb = hostname == "image.tmdb.org" or hostname.endswith(
        ".image.tmdb.org"
    )
    if bool(getattr(metadata_client, "available", False)) and not is_tmdb:
        # When TMDb fallback is ready, do not let a dead provider image hold
        # the selected title for the full general image timeout.
        return PICON_FAST_FALLBACK_TIMEOUT
    return PICON_DOWNLOAD_TIMEOUT


def _picon_cache_path(url):
    digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
    try:
        suffix = os.path.splitext(urlsplit(url).path)[1].lower()
    except Exception:
        suffix = ""
    if suffix == ".jpeg":
        suffix = ".jpg"
    if suffix not in PICON_CACHE_SUFFIXES:
        suffix = ".jpg"
    return os.path.join(PICON_CACHE_DIR, "{}{}".format(digest, suffix))


def _discard_invalid_live_picon_cache(url, path):
    """Delete only this URL's verified disposable picon cache candidate."""
    candidate = os.path.abspath(str(path or ""))
    expected_base = os.path.splitext(_picon_cache_path(url))[0]
    allowed = {
        os.path.abspath(expected_base + suffix)
        for suffix in PICON_CACHE_SUFFIXES
    }
    if (
        candidate not in allowed
        or not _ensure_picon_cache_directory(create=False)
        or _safe_cache_file_status(candidate) is None
    ):
        return False
    try:
        os.unlink(candidate)
    except OSError:
        return False
    with _PICON_PATH_CACHE_GUARD:
        remembered = _PICON_PATH_CACHE.get(url)
        if (
            remembered is not None
            and os.path.abspath(remembered[1]) == candidate
        ):
            _PICON_PATH_CACHE.pop(url, None)
    with _PICON_VALIDATION_CACHE_GUARD:
        stale_keys = [
            cache_key
            for cache_key in _PICON_VALIDATION_CACHE
            if cache_key and cache_key[0] == candidate
        ]
        for cache_key in stale_keys:
            _PICON_VALIDATION_CACHE.pop(cache_key, None)
    return True


def _cached_picon_path(url):
    now_mono = time.monotonic()
    with _PICON_PATH_CACHE_GUARD:
        remembered = _PICON_PATH_CACHE.get(url)
    if remembered is not None:
        expires_at, remembered_path = remembered
        status = _safe_cache_file_status(remembered_path)
        if (
            now_mono < expires_at
            and status is not None
            and 0 < status.st_size <= MAX_PICON_BYTES
        ):
            return remembered_path
        with _PICON_PATH_CACHE_GUARD:
            _PICON_PATH_CACHE.pop(url, None)
    preferred = _picon_cache_path(url)
    base = os.path.splitext(preferred)[0]
    candidates = [preferred]
    candidates.extend(
        base + suffix
        for suffix in PICON_CACHE_SUFFIXES
        if base + suffix != preferred
    )
    for candidate in candidates:
        status = _safe_cache_file_status(candidate)
        if status is None:
            continue
        try:
            size = status.st_size
            if size <= 0 or size > MAX_PICON_BYTES:
                os.unlink(candidate)
                continue
            with open(candidate, "rb") as handle:
                signature = handle.read(16)
            suffix = _image_suffix(signature)
            if not suffix or suffix not in PICON_CACHE_SUFFIXES:
                # Remove cached HTTP/error payloads masquerading as images.
                os.unlink(candidate)
                continue
            corrected = base + suffix
            if corrected != candidate:
                if os.path.isfile(corrected):
                    os.unlink(candidate)
                else:
                    os.rename(candidate, corrected)
                candidate = corrected
            with _PICON_PATH_CACHE_GUARD:
                _PICON_PATH_CACHE[url] = (
                    now_mono + PICON_PATH_CACHE_TTL_SECONDS,
                    candidate,
                )
                if len(_PICON_PATH_CACHE) > PICON_PATH_CACHE_MAX_ENTRIES:
                    oldest = min(
                        _PICON_PATH_CACHE,
                        key=lambda key: _PICON_PATH_CACHE[key][0],
                    )
                    _PICON_PATH_CACHE.pop(oldest, None)
            return candidate
        except (IOError, OSError):
            continue
    return ""


def _picon_failure_remaining(url, now=None):
    now = time.time() if now is None else float(now)
    with _PICON_FAILURES_GUARD:
        failed_at = _PICON_FAILURES.get(url)
        if failed_at is None:
            return 0.0
        remaining = PICON_FAILURE_TTL_SECONDS - (now - failed_at)
        if remaining <= 0.0:
            _PICON_FAILURES.pop(url, None)
            return 0.0
        return float(remaining)


def _picon_failure_active(url, now=None):
    return _picon_failure_remaining(url, now=now) > 0.0


def _mark_picon_failure(url):
    now = time.time()
    with _PICON_FAILURES_GUARD:
        expired = [
            failed_url
            for failed_url, failed_at in _PICON_FAILURES.items()
            if now - failed_at >= PICON_FAILURE_TTL_SECONDS
        ]
        for failed_url in expired:
            _PICON_FAILURES.pop(failed_url, None)
        if (
            url not in _PICON_FAILURES
            and len(_PICON_FAILURES) >= PICON_FAILURE_MAX_ENTRIES
        ):
            oldest = min(_PICON_FAILURES, key=_PICON_FAILURES.get)
            _PICON_FAILURES.pop(oldest, None)
        _PICON_FAILURES[url] = now


def _clear_picon_failure(url):
    with _PICON_FAILURES_GUARD:
        _PICON_FAILURES.pop(url, None)


def _prune_picon_cache(force=False, now=None):
    """Keep the temporary image cache bounded on small receiver flash/RAM."""
    now = time.time() if now is None else float(now)
    if not force and now - _PICON_LAST_CLEANUP[0] < PICON_CLEANUP_INTERVAL_SECONDS:
        return
    if not _PICON_CLEANUP_GUARD.acquire(False):
        return
    try:
        if not force and now - _PICON_LAST_CLEANUP[0] < PICON_CLEANUP_INTERVAL_SECONDS:
            return
        _PICON_LAST_CLEANUP[0] = now
        if not _ensure_picon_cache_directory(create=False):
            return
        files = []
        total = 0
        try:
            names = os.listdir(PICON_CACHE_DIR)
        except OSError:
            return
        for name in names:
            path = os.path.join(PICON_CACHE_DIR, name)
            try:
                stat_result = os.lstat(path)
            except OSError:
                continue
            current_uid = getattr(os, "geteuid", lambda: stat_result.st_uid)()
            if (
                not stat.S_ISREG(stat_result.st_mode)
                or stat_result.st_uid != current_uid
                or stat_result.st_nlink != 1
            ):
                try:
                    os.unlink(path)
                except OSError:
                    pass
                continue
            age = max(0.0, now - float(stat_result.st_mtime))
            stale_temporary = name.endswith(".tmp") and age >= 3600
            if (
                stat_result.st_size <= 0
                or age >= PICON_CACHE_MAX_AGE_SECONDS
                or stale_temporary
            ):
                try:
                    os.unlink(path)
                except OSError:
                    pass
                continue
            files.append((float(stat_result.st_mtime), path, stat_result.st_size))
            total += stat_result.st_size
        files.sort()
        while (
            files
            and (
                len(files) > PICON_CACHE_MAX_FILES
                or total > PICON_CACHE_MAX_BYTES
            )
        ):
            path, size = files.pop(0)[1:]
            try:
                os.unlink(path)
                total -= size
            except OSError:
                pass
    finally:
        _PICON_CLEANUP_GUARD.release()


def _image_suffix(body, content_type=""):
    if body.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if body.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if body.startswith((b"GIF87a", b"GIF89a")):
        return ".gif"
    if body.startswith(b"BM"):
        return ".bmp"
    if len(body) >= 12 and body[:4] == b"RIFF" and body[8:12] == b"WEBP":
        return ".webp"
    content_type = str(content_type or "").lower()
    if "jpeg" in content_type or "jpg" in content_type:
        return ".jpg"
    for suffix in ("png", "gif", "bmp", "webp"):
        if suffix in content_type:
            return "." + suffix
    return ""


def _image_dimensions(body, suffix):
    """Read dimensions without decoding attacker-controlled image pixels."""
    try:
        if suffix == ".png" and len(body) >= 24:
            return struct.unpack(">II", body[16:24])
        if suffix == ".gif" and len(body) >= 10:
            return struct.unpack("<HH", body[6:10])
        if suffix == ".bmp" and len(body) >= 26:
            width, height = struct.unpack("<ii", body[18:26])
            return abs(width), abs(height)
        if suffix == ".jpg" and body.startswith(b"\xff\xd8"):
            offset = 2
            while offset + 4 <= len(body):
                if body[offset] != 0xFF:
                    offset += 1
                    continue
                marker = body[offset + 1]
                offset += 2
                if marker in (0xD8, 0xD9) or 0xD0 <= marker <= 0xD7:
                    continue
                if offset + 2 > len(body):
                    break
                segment = struct.unpack(">H", body[offset:offset + 2])[0]
                if segment < 2 or offset + segment > len(body):
                    break
                if marker in (
                    0xC0, 0xC1, 0xC2, 0xC3,
                    0xC5, 0xC6, 0xC7,
                    0xC9, 0xCA, 0xCB,
                    0xCD, 0xCE, 0xCF,
                ) and segment >= 7:
                    height, width = struct.unpack(">HH", body[offset + 3:offset + 7])
                    return width, height
                offset += segment
    except (IndexError, struct.error, TypeError, ValueError):
        pass
    return 0, 0


def _valid_image_dimensions(body, suffix):
    width, height = _image_dimensions(body, suffix)
    return (
        width > 0
        and height > 0
        and width <= MAX_PICON_DIMENSION
        and height <= MAX_PICON_DIMENSION
        and width * height <= MAX_PICON_PIXELS
    )


def _valid_cinematic_backdrop_file(path):
    """Accept only bounded landscape artwork for the wide cinematic hero."""
    status = _safe_cache_file_status(path)
    if status is None or status.st_size <= 0 or status.st_size > MAX_PICON_BYTES:
        return False
    try:
        with open(path, "rb") as handle:
            body = handle.read(min(status.st_size, LIVE_PICON_HEADER_BYTES))
    except (IOError, OSError):
        return False
    suffix = _image_suffix(body)
    width, height = _image_dimensions(body, suffix)
    return (
        bool(suffix)
        and width * 3 >= height * 4
        and width >= CINEMATIC_MIN_HERO_WIDTH
        and height >= CINEMATIC_MIN_HERO_HEIGHT
        and width <= MAX_PICON_DIMENSION
        and height <= MAX_PICON_DIMENSION
        and width * height <= MAX_PICON_PIXELS
    )


def _valid_cinematic_fallback_file(path):
    """Accept a bounded poster when no usable wide hero is available."""
    status = _safe_cache_file_status(path)
    if status is None or status.st_size <= 0 or status.st_size > MAX_PICON_BYTES:
        return False
    try:
        with open(path, "rb") as handle:
            body = handle.read(min(status.st_size, LIVE_PICON_HEADER_BYTES))
    except (IOError, OSError):
        return False
    suffix = _image_suffix(body)
    width, height = _image_dimensions(body, suffix)
    return (
        bool(suffix)
        and width > 0
        and height > 0
        and width <= MAX_PICON_DIMENSION
        and height <= MAX_PICON_DIMENSION
        and width * height <= MAX_PICON_PIXELS
    )


def _cinematic_cover_ratio(path):
    """Return a verified source ratio for centre-cropped, full-bleed heroes."""
    if not _valid_cinematic_fallback_file(path):
        return None
    status = _safe_cache_file_status(path)
    try:
        with open(path, "rb") as handle:
            body = handle.read(min(status.st_size, LIVE_PICON_HEADER_BYTES))
    except (AttributeError, IOError, OSError):
        return None
    suffix = _image_suffix(body)
    width, height = _image_dimensions(body, suffix)
    return (width, height) if width > 0 and height > 0 else None


def _valid_live_picon_file(path):
    """Reject poster-sized images before a channel-row decoder sees them."""
    status = _safe_cache_file_status(path)
    if status is None or status.st_size <= 0 or status.st_size > MAX_PICON_BYTES:
        return False
    cache_key = (
        os.path.abspath(path),
        int(getattr(status, "st_dev", 0) or 0),
        int(getattr(status, "st_ino", 0) or 0),
        int(status.st_size),
        int(
            getattr(
                status,
                "st_mtime_ns",
                int(float(status.st_mtime) * 1000000000),
            )
        ),
    )
    with _PICON_VALIDATION_CACHE_GUARD:
        if cache_key in _PICON_VALIDATION_CACHE:
            cached = _PICON_VALIDATION_CACHE[cache_key]
            _PICON_VALIDATION_CACHE.move_to_end(cache_key)
            return bool(cached)
    try:
        with open(path, "rb") as handle:
            # PNG/GIF/BMP dimensions live in the fixed header and normal JPEG
            # SOF markers occur early. Keep cache validation bounded because
            # this path runs for every visible row on the Enigma2 GUI thread.
            body = handle.read(min(status.st_size, LIVE_PICON_HEADER_BYTES))
    except (IOError, OSError):
        return False
    if not body:
        return False
    suffix = _image_suffix(body)
    width, height = _image_dimensions(body, suffix)
    valid = (
        bool(suffix)
        and width > 0
        and height > 0
        and width <= LIVE_PICON_MAX_DIMENSION
        and height <= LIVE_PICON_MAX_DIMENSION
        and width * height <= LIVE_PICON_MAX_PIXELS
    )
    with _PICON_VALIDATION_CACHE_GUARD:
        _PICON_VALIDATION_CACHE[cache_key] = bool(valid)
        _PICON_VALIDATION_CACHE.move_to_end(cache_key)
        while (
            len(_PICON_VALIDATION_CACHE)
            > LIVE_PICON_VALIDATION_CACHE_MAX_ENTRIES
        ):
            _PICON_VALIDATION_CACHE.popitem(last=False)
    return valid


def _ensure_picon_cache_directory(create=True):
    try:
        status = os.lstat(PICON_CACHE_DIR)
    except OSError:
        if not create:
            return False
        try:
            os.mkdir(PICON_CACHE_DIR, 0o700)
            status = os.lstat(PICON_CACHE_DIR)
        except OSError:
            return False
    current_uid = getattr(os, "geteuid", lambda: status.st_uid)()
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != current_uid:
        return False
    try:
        os.chmod(PICON_CACHE_DIR, 0o700)
    except OSError:
        return False
    return True


def _safe_cache_file_status(path):
    try:
        status = os.lstat(path)
    except OSError:
        return None
    current_uid = getattr(os, "geteuid", lambda: status.st_uid)()
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != current_uid
        or status.st_nlink != 1
    ):
        return None
    return status


def _download_picon(
    url,
    destination,
    timeout=None,
    token=None,
    request_headers=None,
    user_agent=None,
):
    if token is not None:
        token.check()
    cached = _cached_picon_path(url)
    if cached:
        return cached
    if _picon_failure_active(url):
        return ""
    with _PICON_DOWNLOAD_LOCKS_GUARD:
        state = _PICON_DOWNLOAD_LOCKS.get(url)
        if state is None:
            state = {"lock": threading.Lock(), "users": 0}
            _PICON_DOWNLOAD_LOCKS[url] = state
        state["users"] += 1
    try:
        _acquire_worker_lock(state["lock"], token)
        try:
            if token is not None:
                token.check()
            cached = _cached_picon_path(url)
            if cached:
                return cached
            if _picon_failure_active(url):
                return ""
            try:
                downloaded = _download_picon_unlocked(
                    url,
                    destination,
                    timeout,
                    token=token,
                    request_headers=request_headers,
                    user_agent=user_agent,
                )
            except _WorkerCancelled:
                raise
            except Exception:
                _mark_picon_failure(url)
                raise
            if not downloaded:
                _mark_picon_failure(url)
                return ""
            _clear_picon_failure(url)
            # The cache lives under /tmp. Scanning hundreds of files after
            # every logo download causes needless I/O while video is starting;
            # the existing bounded periodic cleanup is sufficient here.
            _prune_picon_cache()
            with _PICON_PATH_CACHE_GUARD:
                _PICON_PATH_CACHE[url] = (
                    time.monotonic() + PICON_PATH_CACHE_TTL_SECONDS,
                    downloaded,
                )
            return downloaded
        finally:
            state["lock"].release()
    finally:
        with _PICON_DOWNLOAD_LOCKS_GUARD:
            state["users"] -= 1
            if state["users"] <= 0 and _PICON_DOWNLOAD_LOCKS.get(url) is state:
                _PICON_DOWNLOAD_LOCKS.pop(url, None)


def _download_picon_unlocked(
    url,
    destination,
    timeout=None,
    token=None,
    request_headers=None,
    user_agent=None,
):
    try:
        timeout = float(
            PICON_DOWNLOAD_TIMEOUT if timeout is None else timeout
        )
    except (TypeError, ValueError, OverflowError):
        timeout = float(PICON_DOWNLOAD_TIMEOUT)
    timeout = max(0.25, timeout)
    if token is not None:
        token.check()
        timeout = min(timeout, max(0.25, token.deadline - time.monotonic()))
    _acquire_worker_lock(_PICON_DOWNLOAD_SLOTS, token)
    try:
        if token is not None:
            token.check()
        fetch_arguments = (
            url,
            MAX_PICON_BYTES,
            timeout,
            "image/jpeg,image/png;q=0.9,*/*;q=0.5",
            str(
                user_agent
                or "Mozilla/5.0 GT-IPTV-Player-Pro/{}".format(PLUGIN_VERSION)
            ),
        )
        if request_headers:
            response = fetch_public_url(
                *fetch_arguments,
                request_headers=request_headers
            )
        else:
            response = fetch_public_url(*fetch_arguments)
        body, headers = response[:2]
        content_type = headers.get("content-type", "")
    finally:
        _PICON_DOWNLOAD_SLOTS.release()
    if token is not None:
        token.check()
    if not body or len(body) > MAX_PICON_BYTES:
        return ""
    suffix = _image_suffix(body, content_type)
    if not suffix or suffix not in PICON_CACHE_SUFFIXES:
        return ""
    if not _valid_image_dimensions(body, suffix):
        return ""
    if not _ensure_picon_cache_directory():
        return ""
    expected_base = os.path.splitext(_picon_cache_path(url))[0]
    if os.path.splitext(os.path.abspath(destination))[0] != expected_base:
        return ""
    destination = expected_base + suffix
    temporary = os.path.join(
        PICON_CACHE_DIR,
        ".{}.tmp".format(uuid.uuid4().hex),
    )
    descriptor = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(temporary, flags, 0o600)
        status = os.fstat(descriptor)
        if not stat.S_ISREG(status.st_mode) or status.st_nlink != 1:
            raise OSError("unsafe picon cache target")
        payload = body
        while payload:
            written = os.write(descriptor, payload)
            if written <= 0:
                raise OSError("picon cache write failed")
            payload = payload[written:]
        # A picon is a disposable /tmp cache entry, not durable user data.
        # Avoid forcing receiver storage to flush while a live stream starts.
        os.close(descriptor)
        descriptor = None
        if not _ensure_picon_cache_directory(create=False):
            raise OSError("unsafe picon cache directory")
        os.replace(temporary, destination)
        os.chmod(destination, 0o600)
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
        try:
            if os.path.lexists(temporary):
                os.unlink(temporary)
        except OSError:
            pass
    return destination


class GTPlayerInfoOverlay(Screen):
    """Non-modal high-z OSD rendered independently from ExtEplayer3."""

    def __init__(self, session):
        self.skin = scale_skin_fonts(_player_info_skin())
        Screen.__init__(self, session)
        self["info_background"] = Label("")
        self["top_line"] = Label("")
        self["bottom_line"] = Label("")
        # Keep the established component names for older Enigma2 skins, but
        # render their 1x1 compatibility widgets fully transparent.  Only a
        # successfully decoded provider picon is allowed to occupy this area.
        self["picon_frame"] = Label("")
        self["picon_inner"] = Label("")
        self["picon_placeholder"] = Label("")
        self["picon_placeholder"].hide()
        self["picon"] = Pixmap()
        self["picon"].hide()
        self["number_badge_frame"] = Label("")
        self["number_badge_inner"] = Label("")
        self["channel_number"] = Label("")
        self["number_divider"] = Label("")
        self["channel_name"] = Label("")
        self["engine"] = Label(_("Player"))
        self["resolution"] = Label(_("Reading resolution"))
        self["fps"] = Label("-- FPS")
        self["video_codec"] = Label(_("VIDEO: --"))
        self["audio_codec"] = Label(_("AUDIO: --"))
        self["epg_separator"] = Label("")
        self["epg_now_label"] = Label(_("NOW"))
        self["epg_now"] = Label(_("Loading EPG..."))
        self["epg_next_label"] = Label(_("NEXT"))
        self["epg_next"] = Label("")
        self["epg_bottom_separator"] = Label("")
        self["player_progress_bg"] = Label("")
        self["player_progress_fill"] = Label("")
        self["player_progress_text"] = Label("")
        self["weather_divider"] = Label("")
        self["weather_card"] = Label("")
        self["favorite_badge"] = Label(localized_upper(_("Favorite")))
        self["favorite_badge"].hide()
        self["weather_icon"] = Pixmap()
        self["weather_city"] = Label(localized_upper(DEFAULT_CITY))
        self["weather_temp"] = Label("--°C")
        self["weather_desc"] = Label(_("Loading weather data"))
        self["clock_divider"] = Label("")
        self["clock"] = Label("--:--")
        self["date"] = Label("")
        self["seek_panel"] = Label("")
        self["seek_state"] = Label("")
        self["seek_progress_bg"] = Label("")
        self["seek_progress_fill"] = Label("")
        self["seek_times"] = Label("")
        self["seek_hint"] = Label("")
        for name in (
            "seek_panel",
            "seek_state",
            "seek_progress_bg",
            "seek_progress_fill",
            "seek_times",
            "seek_hint",
        ):
            self[name].hide()


class GTPlayerFrontPanelSummary(Screen):
    """Mirror the active IPTV programme on supported receiver displays."""

    def __init__(self, session, parent):
        self._layout = _front_panel_layout()
        self.skin = _front_panel_skin()
        try:
            Screen.__init__(self, session, parent=parent)
        except TypeError:
            # Older Enigma2 Screen implementations did not expose parent as a
            # keyword, but still support custom Summary screens.
            Screen.__init__(self, session)
            self.parent = parent
        self._player = parent
        self._active = False
        self._static_live = bool(
            str(
                getattr(
                    getattr(parent, "current_item", None),
                    "content_type",
                    "",
                )
                or ""
            ).lower()
            == "live"
        )
        self._progress_value = 0
        try:
            parent._front_panel_summary = self
        except Exception:
            pass
        self["kind"] = Label("")
        self["title"] = Label("")
        self["subtitle"] = Label("")
        self["detail"] = Label("")
        self["progress_bg"] = Label("")
        self["progress_fill"] = Label("")
        self._refresh_timer = eTimer()
        _connect_timer(self._refresh_timer, self._refresh)
        if hasattr(self, "onShown"):
            self.onShown.append(self._start)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._render)
            self.onLayoutFinish.append(self._apply_progress_fill)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop)
        # Some image families do not emit onShown for the dedicated Summary
        # desktop. Start immediately so the first channel can never remain
        # frozen on the front panel.
        self._start()

    @staticmethod
    def _clock_text(seconds):
        try:
            seconds = max(0, int(seconds))
        except (TypeError, ValueError, OverflowError):
            seconds = 0
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)
        return "{:02d}:{:02d}".format(minutes, seconds)

    @staticmethod
    def _clean_text(value):
        return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").split())

    def _set_front_panel_text(self, name, value):
        capacity = self._layout[
            "title_capacity" if name == "title" else "subtitle_capacity"
        ]
        return ellipsize_dynamic_text(
            self[name],
            self._clean_text(value),
            fallback_chars=capacity,
        )

    def _set_progress(self, percent):
        try:
            value = max(0, min(100, int(round(float(percent)))))
        except (TypeError, ValueError, OverflowError):
            value = 0
        self._progress_value = value
        self._apply_progress_fill()
        return value

    def _apply_progress_fill(self):
        fill = self["progress_fill"]
        if self._progress_value <= 0:
            fill.hide()
            return
        width = max(
            1,
            int(
                round(
                    self._layout["content_w"]
                    * (float(self._progress_value) / 100.0)
                )
            ),
        )
        try:
            fill.instance.resize(eSize(width, self._layout["progress_h"]))
        except Exception:
            pass
        fill.show()

    def _render_live(self, item):
        title = self._clean_text(getattr(item, "name", "")) or "GT IPTV"
        events = list(getattr(self._player, "_current_epg_events", []) or [])
        if not events:
            return N_("LIVE TV"), title, _("EPG information unavailable"), "", 0
        event = events[0]
        subtitle = self._clean_text(getattr(event, "title", ""))
        start = int(getattr(event, "start_timestamp", 0) or 0)
        end = int(getattr(event, "end_timestamp", 0) or 0)
        now = int(time.time())
        if start and start > now:
            detail = ""
            if end > start:
                detail = "{} - {}".format(
                    datetime.datetime.fromtimestamp(start).strftime("%H:%M"),
                    datetime.datetime.fromtimestamp(end).strftime("%H:%M"),
                )
            return (
                N_("LIVE TV"),
                title,
                "{}: {}".format(_("NEXT"), subtitle),
                detail,
                0,
            )
        if not start or not end or end <= start:
            return N_("LIVE TV"), title, subtitle, "", 0
        if self._static_live:
            # Keep one immutable front-panel snapshot per live channel.
            # There is deliberately no one-second progress countdown and no
            # animated text competing with fullscreen playback.
            detail = "{} - {}".format(
                datetime.datetime.fromtimestamp(start).strftime("%H:%M"),
                datetime.datetime.fromtimestamp(end).strftime("%H:%M"),
            )
            return N_("LIVE TV"), title, subtitle, detail, 0
        ratio = min(1.0, max(0.0, float(now - start) / float(end - start)))
        percent = int(round(ratio * 100.0))
        remaining = max(0, end - now)
        detail = "{}%  {} {}".format(
            percent,
            _("REMAINING"),
            self._clock_text(remaining),
        )
        return N_("LIVE TV"), title, subtitle, detail, percent

    def _render_vod(self, item, content_type):
        title = self._clean_text(getattr(item, "name", "")) or "GT IPTV"
        kind = (N_("TV Archive") if content_type == "catchup" else
                N_("SERIES") if content_type == "series" else N_("MOVIES"))
        position, length = self._player._seek_position()
        if position < 0 or length <= 0:
            return (
                kind,
                title,
                _("Please wait"),
                "",
                0,
            )
        position = min(max(0, int(position)), int(length))
        length = max(1, int(length))
        remaining = max(0, length - position)
        percent = int(round((float(position) / float(length)) * 100.0))
        subtitle = "{} / {}".format(
            self._clock_text(position),
            self._clock_text(length),
        )
        detail = "{}%  {} {}".format(
            percent,
            _("REMAINING"),
            self._clock_text(remaining),
        )
        return (
            kind,
            title,
            subtitle,
            detail,
            percent,
        )

    def _render(self):
        item = getattr(self._player, "current_item", None)
        content_type = str(getattr(item, "content_type", "") or "").lower()
        if content_type == "live":
            kind, title, subtitle, detail, percent = self._render_live(item)
        elif content_type in ("movie", "series", "catchup"):
            kind, title, subtitle, detail, percent = self._render_vod(
                item, content_type
            )
        else:
            kind, title, subtitle, detail, percent = (
                "GT IPTV",
                self._clean_text(getattr(item, "name", "")),
                "",
                "",
                0,
            )
        self["kind"].setText(_(kind))
        self._set_front_panel_text("title", title)
        self._set_front_panel_text("subtitle", subtitle)
        self["detail"].setText(detail)
        self._set_progress(percent)

    def _start(self):
        self._active = True
        self._refresh()

    def refresh_now(self):
        """Apply a channel/EPG/playback change without waiting for a timer."""
        self._render()

    def _refresh(self):
        if not self._active:
            return
        self._render()
        if not self._static_live:
            self._refresh_timer.start(1000, True)

    def _stop(self):
        self._active = False
        try:
            self._refresh_timer.stop()
        except Exception:
            pass
        try:
            if self._player._front_panel_summary is self:
                self._player._front_panel_summary = None
        except Exception:
            pass


class GTExternalPlayerScreen(
    _LiveStartupRecoverySupport,
    _FavoriteSupport,
    Screen,
):
    def __init__(
        self,
        session,
        reference,
        item,
        client=None,
        items=None,
        selected_index=0,
        keep_playing_on_exit=False,
        weather_settings_loader=None,
        weather_service=None,
        resume_store=None,
        resume_key_value="",
        start_position=0,
        live_snapshot=None,
        favorite_store=None,
        favorite_parent=None,
        favorite_keys=None,
        adopt_playing_service=False,
        old_reference_override=None,
        live_startup_recovery=None,
        playback_owner=None,
        archive_initial_playback=None,
    ):
        self.skin = _player_skin()
        Screen.__init__(self, session)
        self.reference = reference
        self.client = client
        self._favorite_parent = favorite_parent
        self._init_favorite_support(favorite_store, favorite_keys)
        self.keep_playing_on_exit = bool(keep_playing_on_exit)
        self._adopt_playing_service = bool(adopt_playing_service)
        self._old_reference_override = old_reference_override
        # R92 could transfer an automatic startup monitor between the list and
        # fullscreen player.  R93 intentionally ignores that legacy argument:
        # no MAC stream may refresh or restart without an explicit 0 key.
        del live_startup_recovery
        self._live_startup_recovery = None
        self._playback_owner = playback_owner
        self._archive_initial_playback = archive_initial_playback
        self._archive_startup_watchdog = None
        self._archive_failure_reported = False
        self._archive_previous_service = None
        self._archive_previous_pts = (None, None)
        self._archive_previous_position = None
        self._archive_terminal_timer = None
        self._archive_terminal_attempts = 0
        self._archive_portal_keepalive_token = None
        self._archive_portal_initialized = False
        # Screen.items() is part of Enigma2's GUI teardown contract. Keep the
        # channel collection under a private, non-conflicting name.
        self._entries = list(items or [])
        self.current_item = item if hasattr(item, "name") else None
        self.current_index = 0
        if self._entries:
            try:
                requested_index = int(selected_index)
            except (TypeError, ValueError):
                requested_index = 0
            self.current_index = requested_index % len(self._entries)
            self.current_item = self._entries[self.current_index]
        self.title = getattr(item, "name", str(item or "GT IPTV"))
        self.old_reference = None
        self._started = False
        self._closed = False
        self._fullscreen_close_result = None
        self._front_panel_summary = None
        self._generation = 0
        self._live_snapshot = (
            dict(live_snapshot) if isinstance(live_snapshot, dict) else {}
        )
        self._live_snapshot_events = []
        self._old_reference_restored = False
        self._info_visible = False
        self._picon_visible = False
        self._cached_picon_generation = -1
        self._picon_request_generation = -1
        self._picon_request_token = 0
        self._live_info_ready_samples = 0
        self._live_play_started_at = 0.0
        self._live_info_state = None
        self._live_info_results = {}
        self._live_info_results_lock = threading.Lock()
        self._live_info_queued_generation = -1
        self._live_info_due_at = 0.0
        self._live_info_epg_retry_after = {}
        self._live_info_epg_refresh_generation = -1
        self._metadata_attempts = 0
        self._live_metadata_generation = -1
        self._live_metadata_complete_generation = -1
        self._live_metadata_ready = {}
        self._defer_live_metadata_once = False
        self._live_metadata_settle_pending = False
        self._current_epg_events = []
        self._picon_results = {}
        self._weather_settings_loader = weather_settings_loader or load_weather_settings
        self._weather_service = weather_service or WeatherService()
        self._weather_forecast = None
        self._weather_error = ""
        self._weather_ready = False
        self._weather_loading = False
        self._weather_started = False
        screen_width, screen_height, px = _scale()
        self._progress_width, self._progress_height = (
            _player_info_progress_geometry(screen_width, px)
        )
        self._info_channel_preferred_font = font_px(px, 40, role="body")
        self._info_channel_min_font = font_px(
            px,
            40,
            role="body",
            value="standard",
        )
        self._info_epg_now_preferred_font = font_px(px, 24, role="body")
        self._info_epg_now_min_font = font_px(
            px,
            24,
            role="body",
            value="standard",
        )
        self._info_epg_next_preferred_font = font_px(px, 21, role="body")
        self._info_epg_next_min_font = font_px(
            px,
            21,
            role="body",
            value="standard",
        )
        self._seek_width = max(1, screen_width - px(160))
        self._seek_height = max(1, px(14))
        self._seeking = False
        self._seek_target = 0
        self._seek_length = 0
        self._pending_seek_verification = None
        self._paused = False
        self._blue_long_last_at = 0.0
        self._suppress_next_blue_short_until = 0.0
        self._audio_menu_open = False
        self._subtitle_menu_open = False
        self._subtitle_menu_generation = None
        self._subtitle_message_tokens = set()
        self._subtitle_message_serial = 0
        self._resume_store = resume_store
        self._resume_key = str(resume_key_value or "")
        try:
            self._resume_start_position = max(0, int(start_position or 0))
        except (TypeError, ValueError, OverflowError):
            self._resume_start_position = 0
        self._resume_attempts = 0
        self._resume_last_length = -1
        self._resume_stable_length_samples = 0
        self._resume_saved = False
        self._active_live_service_type = reference_service_type(
            self.reference,
            4097,
        )
        self._live_reconnecting = False
        self._reconnect_generation = -1
        self._reconnect_link_pending = False
        self._reconnect_link_ready = False
        self._reconnect_link_url = ""
        self._reconnect_link_deadline = 0.0
        self._reconnect_link_token = None
        self._reconnect_reason = ""
        self._reconnect_decoder_released = False
        self._reconnect_pending_reference = None
        self._reconnect_queued = False
        self._reconnect_link_lock = threading.RLock()
        self._pending_channel_index = None
        self._channel_link_lock = threading.RLock()
        self._channel_link_pending = False
        self._channel_link_ready = False
        self._channel_link_url = ""
        self._channel_link_generation = -1
        self._channel_link_index = -1
        self._channel_link_deadline = 0.0
        self._channel_link_token = None
        self._channel_key_accepted_at = 0.0
        self._channel_link_ready_at = 0.0
        self._last_channel_key_step = 0
        self._last_channel_key_at = None
        self._pending_engine_reference = None
        self._previous_engine_reference = None
        self._engine_switch_generation = -1
        self._engine_link_pending = False
        self._engine_link_ready = False
        self._engine_link_url = ""
        self._engine_link_deadline = 0.0
        self._engine_link_token = None
        self._engine_decoder_released = False
        self._weather_token = None
        self._picon_worker_token = None

        # ExtEplayer3 can refresh or cover the modal player's own OSD window on
        # OpenATV.  Keep the controller modal, but render the channel banner in
        # a separate high-z, non-modal dialog (the same pattern used by native
        # Enigma2 overlays such as Toast/VolumeControl).
        self._info_dialog = self.session.instantiateDialog(GTPlayerInfoOverlay)
        self._info_dialog.hide()
        self._subtitle_controller = MovieSubtitleController(
            self.session,
            on_message=self._show_subtitle_message,
            on_changed=self._subtitle_changed,
            title_provider=self._subtitle_search_metadata,
            is_paused=lambda: self._paused,
            pause_for_search=self._pause_for_subtitle_search,
            resume_after_search=self._resume_after_subtitle_search,
            on_menu_open=self._subtitle_menu_opened,
            on_menu_close=self._subtitle_menu_closed,
        )

        self._hide_timer = eTimer()
        self._metadata_timer = eTimer()
        self._picon_timer = eTimer()
        self._clock_timer = eTimer()
        self._weather_timer = eTimer()
        self._live_info_timer = eTimer()
        self._playback_timer = eTimer()
        self._resume_timer = eTimer()
        self._seek_verify_timer = eTimer()
        self._reconnect_timer = eTimer()
        self._channel_switch_timer = eTimer()
        self._archive_portal_timer = eTimer()
        _connect_timer(self._hide_timer, self.hide_info)
        _connect_timer(self._metadata_timer, self._metadata_tick)
        _connect_timer(self._picon_timer, self._picon_tick)
        _connect_timer(self._clock_timer, self._clock_tick)
        _connect_timer(self._weather_timer, self._poll_player_weather)
        _connect_timer(self._live_info_timer, self._live_info_tick)
        _connect_timer(self._playback_timer, self._playback_tick)
        _connect_timer(self._resume_timer, self._resume_tick)
        _connect_timer(self._seek_verify_timer, self._verify_seek_result)
        _connect_timer(self._reconnect_timer, self._complete_live_restart)
        self._engine_switch_timer = eTimer()
        _connect_timer(
            self._engine_switch_timer,
            self._complete_live_engine_switch,
        )
        _connect_timer(self._channel_switch_timer, self._commit_channel_switch)
        _connect_timer(
            self._archive_portal_timer,
            self._archive_portal_keepalive,
        )
        exit_action = (
            self.return_to_list
            if self.keep_playing_on_exit
            else self.stop_and_close
        )
        player_contexts = [
            "OkCancelActions",
            "ColorActions",
            "MoviePlayerActions",
            "MediaPlayerActions",
            "DirectionActions",
            "NumberActions",
            "InfobarEPGActions",
            "TVRadioActions",
        ]
        player_actions = {
                "cancel": self.cancel_or_exit,
                "red": self.stop_and_close,
                "blue": self.toggle_current_favorite,
                "stop": self.stop_and_close,
                "ok": self.ok_or_confirm_seek,
                "playpauseService": self.toggle_pause,
                "pauseService": self.pause_playback,
                "unPauseService": self.resume_playback,
                "play": self.resume_playback,
                "pause": self.pause_playback,
                "right": self.seek_right_or_info,
                "left": self.seek_left_or_info,
                "up": self.open_live_channel_list,
                "down": self.open_live_channel_list,
                # OpenATV maps the physical CHANNEL +/- keys to different
                # action names in overlapping contexts. Only MoviePlayer's
                # make event is allowed to change a channel; MediaPlayer and
                # Navigation aliases are intentionally not registered.
                "channelUp": self.next_channel,
                "channelDown": self.previous_channel,
                # Match the familiar IPTV-player controls: 0 manually
                # restarts a live stream, INFO/TV cycles installed engines.
                "0": self.restart_live_stream,
                "info": self.cycle_live_service_type,
                "tv": self.cycle_live_service_type,
                "showEventInfo": self.cycle_live_service_type,
                "showEventView": self.cycle_live_service_type,
                "keyTV": self.cycle_live_service_type,
        }
        if getattr(self.current_item, "content_type", "") in (
            "movie",
            "series",
            "catchup",
        ):
            # Lists retain BLUE search.  Only the modal VOD player uses a
            # short BLUE press for decoder-local audio-track selection.
            player_actions["blue"] = self.open_audio_selection
            # OpenATV publishes this optional long-key alias.  Images without
            # it simply ignore the mapping; the primary short-key path remains
            # portable.  Some keymaps emit BLUE once more on key release, so
            # the long handler suppresses one immediate short event.
            player_actions["bluelong"] = self.toggle_favorite_with_blue_long
            player_actions["blue_long"] = self.toggle_favorite_with_blue_long
        if not self._is_live_item():
            # Several Enigma2 images also publish CHANNEL +/- through one of
            # the seek contexts. Keeping those contexts active for live TV can
            # therefore run seekFwd/seekBack and show "not seekable" instead
            # of zapping. VOD retains its complete short/long seek mapping.
            player_contexts[2:2] = [
                "InfobarSeekActions",
                "InfobarSeekActionsPTS",
            ]
            player_actions.update(
                {
                    "seekFwd": self.seek_forward,
                    "seekBack": self.seek_backward,
                    "SeekbarFwd": self.begin_seek_mode,
                    "SeekbarBack": self.begin_seek_mode,
                    "seekFwdManual": self.begin_seek_mode,
                    "seekBackManual": self.begin_seek_mode,
                    "fastForward": self.seek_forward,
                    "fastforward": self.seek_forward,
                    "rewind": self.seek_backward,
                    # OpenPLi sends held direction keys as separate repeat
                    # actions; keep moving the seek target while held.
                    "rightRepeated": self.seek_right_or_info,
                    "leftRepeated": self.seek_left_or_info,
                }
            )
        if getattr(self.current_item, "content_type", "") in (
            "movie",
            "series",
            "catchup",
        ):
            player_contexts.append("InfobarSubtitleSelectionActions")
            player_actions.update(
                {
                    "yellow": self.open_subtitle_selection,
                    "subtitleSelection": self.open_subtitle_selection,
                }
            )
        self["actions"] = ActionMap(
            player_contexts,
            player_actions,
            -1,
        )
        if hasattr(self, "onShown"):
            self.onShown.append(self.start_playback)
        if hasattr(self, "onClose"):
            self.onClose.append(self._on_close)
        self._clock_tick()
        self._render_player_weather_waiting()
        self._clear_player_progress()
        self._render_channel()
        if getattr(self.current_item, "content_type", "") == "live":
            # The channel list already prepared this immutable data. Rendering
            # it here makes the first infobar frame useful without starting a
            # single EPG, picon or weather request in the player.
            self._apply_live_snapshot()
        self.setTitle(self.title)

        self._exit_action = exit_action

    def createSummary(self):
        """Let Enigma2 attach our adaptive LCD/VFD view when available."""
        return GTPlayerFrontPanelSummary

    def _refresh_front_panel(self):
        summary = self._front_panel_summary
        if summary is None:
            return
        try:
            summary.refresh_now()
        except Exception as error:
            _debug(
                "front panel refresh failed: {}".format(
                    error.__class__.__name__
                )
            )

    def _is_live_item(self):
        return getattr(self.current_item, "content_type", "") == "live"

    def _is_archive_item(self):
        return getattr(self.current_item, "content_type", "") == "catchup"

    def _archive_portal_keepalive(self):
        """Keep only an explicitly opened Stalker archive session active."""
        if self._closed or not self._started or not self._is_archive_item():
            return
        if str(getattr(self.client, "source_type", "") or "").lower() != "stalker":
            return
        sender = getattr(self.client, "archive_playback_keepalive", None)
        if not callable(sender):
            return
        old_token = self._archive_portal_keepalive_token
        _cancel_worker_token(old_token)
        token = _WorkerToken(ARCHIVE_PORTAL_REQUEST_SECONDS)
        self._archive_portal_keepalive_token = token
        generation = self._generation
        initial = not self._archive_portal_initialized
        item = self.current_item

        def run():
            succeeded = False
            error = None
            try:
                succeeded = bool(sender(
                    item,
                    initial=initial,
                    cancel_event=token.cancel_event,
                    deadline=token.deadline,
                ))
            except Exception as caught:
                error = caught
            if (
                succeeded
                and token.active()
                and not self._closed
                and self._generation == generation
                and self.current_item is item
            ):
                self._archive_portal_initialized = True
                log_event(
                    "tv_archive",
                    "Stalker session signal outcome=ok initial={}".format(
                        "yes" if initial else "no",
                    ),
                )
            elif error is not None and token.active():
                log_event(
                    "tv_archive",
                    "Stalker session signal outcome=failed initial={}".format(
                        "yes" if initial else "no",
                    ),
                    error,
                )

        worker = threading.Thread(target=run)
        worker.daemon = True
        try:
            worker.start()
        except Exception as error:
            _cancel_worker_token(token)
            self._archive_portal_keepalive_token = None
            log_event(
                "tv_archive",
                "Stalker session signal outcome=failed initial={}".format(
                    "yes" if initial else "no",
                ),
                error,
            )
            return
        self._archive_portal_timer.start(
            ARCHIVE_PORTAL_KEEPALIVE_SECONDS * 1000,
            True,
        )

    def _stop_archive_portal_keepalive(self):
        try:
            self._archive_portal_timer.stop()
        except Exception:
            pass
        _cancel_worker_token(self._archive_portal_keepalive_token)
        self._archive_portal_keepalive_token = None

    def _arm_archive_startup(self):
        """Start one catchup-only monitor without replacing an active retry."""
        if self._closed or not self._started or not self._is_archive_item():
            return
        watchdog = self._archive_startup_watchdog
        if watchdog is not None and watchdog.phase not in (
            "idle",
            "acquired",
            "failed",
            "cancelled",
            "inconclusive",
        ):
            return
        if watchdog is None or watchdog.phase != "idle":
            watchdog = _ArchivePlaybackWatchdog(self)
            self._archive_startup_watchdog = watchdog
        watchdog.start()

    def _cancel_archive_startup(self):
        watchdog = self._archive_startup_watchdog
        self._archive_startup_watchdog = None
        if watchdog is not None:
            watchdog.cancel()

    def _complete_archive_startup_failure(self):
        timer = getattr(self, "_archive_terminal_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        owner = self._playback_owner
        self._playback_owner = None
        callback = getattr(
            owner,
            "_resume_after_playback_resolution_failure",
            None,
        )
        if callable(callback) and not getattr(owner, "_closed", False):
            try:
                callback()
            except Exception:
                pass
        self.close()

    def _archive_terminal_release(self):
        """Retry one failed decoder release/old-service restore, then stop."""
        if getattr(self, "_closed", False):
            return
        attempts = int(getattr(self, "_archive_terminal_attempts", 0)) + 1
        self._archive_terminal_attempts = attempts
        released = bool(self.stop_playback())
        old_reference = getattr(self, "old_reference", None)
        needs_restore = bool(
            old_reference is not None
            and not getattr(self, "_old_reference_restored", False)
        )
        if attempts > 1 and released and needs_restore:
            # stop_playback() marks _started false after a successful stop,
            # even when replaying the old service failed.  The bounded retry
            # must therefore call the idempotent restore helper explicitly.
            navigation = getattr(getattr(self, "session", None), "nav", None)
            released = bool(self._restore_old_service(navigation))
            needs_restore = not getattr(self, "_old_reference_restored", False)
        if released and not needs_restore:
            self._complete_archive_startup_failure()
            return
        if attempts < 2:
            timer = getattr(self, "_archive_terminal_timer", None)
            if timer is None:
                timer = eTimer()
                self._archive_terminal_timer = timer
                _connect_timer(timer, self._archive_terminal_release)
            timer.start(ARCHIVE_DECODER_RELEASE_MS, True)
            return
        # Never close a screen that may still own navigation, and never resume
        # its underlying archive owner as though restoration succeeded.
        if needs_restore:
            # stop_playback() cleared _started after releasing the archive.
            # Keep EXIT routed through stop_playback so a later user retry can
            # still restore the receiver service instead of closing on empty
            # navigation.
            self._started = True
        self._set_static_info_epg("epg_next", _("Keeping the current stream"))
        self.show_info()

    def _archive_startup_failed(self):
        """Restore navigation, return to the archive list and report once."""
        if self._archive_failure_reported:
            return
        self._archive_failure_reported = True
        self._cancel_archive_startup()
        self._archive_terminal_attempts = 0
        self._archive_terminal_release()

    def _set_info_channel_name(self, value):
        return fit_dynamic_text(
            self._info_dialog["channel_name"],
            value,
            max_lines=1,
            preferred_size=self._info_channel_preferred_font,
            min_size=self._info_channel_min_font,
            fallback_chars=42,
        )

    def _set_info_epg(self, name, value):
        if name == "epg_next":
            preferred = self._info_epg_next_preferred_font
            minimum = self._info_epg_next_min_font
        else:
            preferred = self._info_epg_now_preferred_font
            minimum = self._info_epg_now_min_font
        return fit_dynamic_text(
            self._info_dialog[name],
            value,
            max_lines=1,
            preferred_size=preferred,
            min_size=minimum,
            fallback_chars=54,
        )

    def _set_static_info_channel_name(self, value):
        return set_static_text(
            self._info_dialog["channel_name"],
            value,
            preferred_size=self._info_channel_preferred_font,
        )

    def _set_static_info_epg(self, name, value):
        preferred = (
            self._info_epg_next_preferred_font
            if name == "epg_next"
            else self._info_epg_now_preferred_font
        )
        return set_static_text(
            self._info_dialog[name],
            value,
            preferred_size=preferred,
        )

    def _live_network_silent(self):
        return self._is_live_item()

    def _live_snapshot_for_current_item(self):
        """Return only the list-prepared data belonging to the active channel."""
        if not self._is_live_item() or not self._live_snapshot:
            return {}
        stream_id = str(getattr(self.current_item, "stream_id", "") or "")
        snapshot = dict(self._live_snapshot)
        common = {
            key: snapshot[key]
            for key in ("weather", "weather_forecast", "captured_at")
            if key in snapshot
        }
        channels = snapshot.pop("channels", None)
        if isinstance(channels, dict):
            selected = channels.get(stream_id)
            if not isinstance(selected, dict):
                return common
            snapshot.update(selected)
        snapshot_stream_id = str(snapshot.get("stream_id", "") or "")
        if snapshot_stream_id and snapshot_stream_id != stream_id:
            _debug(
                "live snapshot rejected expected_id={} actual_id={}".format(
                    stream_id,
                    snapshot_stream_id,
                )
            )
            return common
        return snapshot

    def _remember_live_snapshot_component(self, stream_id, component, value):
        """Keep completed live details available for later zaps in this screen."""
        stream_id = str(stream_id or "")
        if not stream_id or component not in ("events", "picon_path"):
            return False
        channels = self._live_snapshot.get("channels")
        if not isinstance(channels, dict):
            channels = {}
            self._live_snapshot["channels"] = channels
        entry = channels.get(stream_id)
        if not isinstance(entry, dict):
            entry = {"stream_id": stream_id}
            channels[stream_id] = entry
        if component == "events":
            entry[component] = list(value or [])
        else:
            entry[component] = str(value or "").strip()
        entry["captured_at"] = time.time()

        top_stream_id = str(self._live_snapshot.get("stream_id", "") or "")
        if top_stream_id == stream_id:
            self._live_snapshot[component] = entry[component]
            self._live_snapshot["captured_at"] = entry["captured_at"]
        return True

    @staticmethod
    def _snapshot_epg_window(events, now=None):
        events = list(events or [])
        if not events:
            return []
        current_time = int(time.time() if now is None else now)
        selected_index = None
        for index, event in enumerate(events):
            start = int(getattr(event, "start_timestamp", 0) or 0)
            end = int(getattr(event, "end_timestamp", 0) or 0)
            if start and end and start <= current_time < end:
                selected_index = index
                break
        if selected_index is None:
            for index, event in enumerate(events):
                start = int(getattr(event, "start_timestamp", 0) or 0)
                end = int(getattr(event, "end_timestamp", 0) or 0)
                if (end and end > current_time) or (start and start >= current_time):
                    selected_index = index
                    break
        if selected_index is None:
            # Providers sometimes omit timestamps. Preserve their order rather
            # than discarding otherwise useful now/next titles.
            if any(
                not int(getattr(event, "end_timestamp", 0) or 0)
                for event in events
            ):
                selected_index = 0
            else:
                return []
        return events[selected_index:]

    def _render_live_snapshot_epg(self, now=None):
        """Render and advance EPG strictly from the in-memory snapshot."""
        window = self._snapshot_epg_window(self._live_snapshot_events, now=now)
        self._current_epg_events = list(window[:2])
        if not window:
            self._set_static_info_epg(
                "epg_now",
                _("EPG information unavailable.")
            )
            self._set_static_info_epg("epg_next", "")
            self._clear_player_progress()
            self._refresh_front_panel()
            return False
        current = window[0]
        future_only = _epg_event_starts_in_future(current, now=now)
        if future_only:
            self._set_static_info_epg(
                "epg_now",
                _("EPG information unavailable.")
            )
            self._clear_player_progress()
            self._set_info_epg("epg_next", self._format_epg(current))
        else:
            self._set_info_epg("epg_now", self._format_epg(current))
            self._set_player_progress(current, now=now)
        if not future_only and len(window) > 1:
            self._set_info_epg("epg_next", self._format_epg(window[1]))
        elif not future_only:
            self._set_static_info_epg("epg_next", "")
        self._refresh_front_panel()
        return True

    def _apply_live_snapshot_picon(self, snapshot=None):
        """Decode snapshot/cache artwork without creating a network request."""
        if (
            not self._is_live_item()
            or not self._started
        ):
            return False
        if snapshot is None:
            snapshot = self._live_snapshot_for_current_item()
        if (
            self._picon_request_generation == self._generation
            and self._cached_picon_generation == self._generation
        ):
            return True
        path = str(snapshot.get("picon_path", "") or "").strip()
        if not path or not _valid_live_picon_file(path):
            url = _safe_picon_url(getattr(self.current_item, "icon", ""))
            path = _cached_picon_path(url) if url else ""
            if path and _valid_live_picon_file(path):
                self._remember_live_snapshot_component(
                    getattr(self.current_item, "stream_id", ""),
                    "picon_path",
                    path,
                )
        if not path or not _valid_live_picon_file(path):
            return False
        self._cached_picon_generation = self._generation
        self._picon_request_generation = self._generation
        self._apply_picon(self._generation, path)
        return True

    def _apply_live_snapshot(self):
        """Apply list-screen EPG/weather/picon without any network I/O."""
        if not self._is_live_item():
            return False
        snapshot = self._live_snapshot_for_current_item()
        events = snapshot.get("events", snapshot.get("epg_events", ()))
        try:
            self._live_snapshot_events = list(events or [])
        except TypeError:
            self._live_snapshot_events = []
        self._render_live_snapshot_epg()

        forecast = snapshot.get(
            "weather_forecast",
            snapshot.get("weather"),
        )
        if isinstance(forecast, dict):
            try:
                forecast = WeatherService._from_dict(forecast)
            except Exception:
                forecast = None
        self._weather_forecast = forecast
        self._weather_started = True
        self._weather_loading = False
        self._weather_ready = bool(forecast is not None)
        if forecast is not None:
            self._render_player_weather(forecast)
        else:
            self._render_player_weather_unavailable()

        # Apply a list-prepared or already-cached local path. A missing logo is
        # handled only by the separate post-decoder completion coordinator.
        self._apply_live_snapshot_picon(snapshot)
        return bool(snapshot)

    def _live_info_loader(self):
        loader = getattr(self.client, "load_epg", None)
        if callable(loader):
            return loader
        loader = getattr(self.client, "load_short_epg", None)
        return loader if callable(loader) else None

    def _live_info_missing_components(self):
        if (
            self._closed
            or not self._started
            or not self._is_live_item()
            or self.client is None
        ):
            return False, False
        snapshot = self._live_snapshot_for_current_item()
        events = snapshot.get("events", snapshot.get("epg_events", ()))
        try:
            has_epg = bool(self._snapshot_epg_window(events))
        except TypeError:
            has_epg = False
        stream_id = str(getattr(self.current_item, "stream_id", "") or "")
        retry_after = float(
            self._live_info_epg_retry_after.get(stream_id, 0.0) or 0.0
        )
        now_mono = time.monotonic()
        if retry_after and now_mono >= retry_after:
            self._live_info_epg_retry_after.pop(stream_id, None)
            retry_after = 0.0
        needs_epg = bool(
            (
                not has_epg
                or self._live_info_epg_refresh_generation == self._generation
            )
            and now_mono >= retry_after
            and self._live_info_loader() is not None
        )

        has_picon = self._apply_live_snapshot_picon(snapshot)
        picon_url = _safe_picon_url(getattr(self.current_item, "icon", ""))
        needs_picon = bool(
            not has_picon
            and picon_url
            and not _picon_failure_active(picon_url)
        )
        return needs_epg, needs_picon

    def _arm_live_info_fallback(self):
        if self._live_info_queued_generation != self._generation:
            return False
        elapsed_ms = max(
            0,
            int(round((time.monotonic() - self._live_play_started_at) * 1000)),
        )
        delay_ms = max(10, LIVE_INFO_FALLBACK_MS - elapsed_ms)
        self._live_info_due_at = time.monotonic() + (delay_ms / 1000.0)
        self._live_info_timer.start(delay_ms, True)
        return True

    def _schedule_live_info_completion(self):
        needs_epg, needs_picon = self._live_info_missing_components()
        if not needs_epg and not needs_picon:
            self._live_info_queued_generation = -1
            self._live_info_due_at = 0.0
            try:
                self._live_info_timer.stop()
            except Exception:
                pass
            return False
        self._live_info_queued_generation = self._generation
        return self._arm_live_info_fallback()

    def _cancel_live_info_state(self):
        state = self._live_info_state
        if state is not None:
            for token in state.get("tokens", {}).values():
                _cancel_worker_token(token)
        self._live_info_state = None
        self._live_info_queued_generation = -1
        self._live_info_due_at = 0.0
        self._live_info_epg_refresh_generation = -1
        with self._live_info_results_lock:
            self._live_info_results.clear()

    def _start_live_info_completion(self, force=False):
        if (
            self._closed
            or self._live_info_queued_generation != self._generation
            or not self._is_live_item()
            or self._live_transition_in_progress()
        ):
            return False
        elapsed_ms = max(
            0,
            int(round((time.monotonic() - self._live_play_started_at) * 1000)),
        )
        decoder_ready = bool(
            self._live_info_ready_samples >= LIVE_INFO_READY_MIN_SAMPLES
            and elapsed_ms >= LIVE_INFO_SETTLE_MS
        )
        if not force and not decoder_ready:
            if self._live_info_ready_samples >= LIVE_INFO_READY_MIN_SAMPLES:
                delay_ms = max(10, LIVE_INFO_SETTLE_MS - elapsed_ms)
                self._live_info_due_at = (
                    time.monotonic() + (delay_ms / 1000.0)
                )
                self._live_info_timer.start(delay_ms, True)
            else:
                self._arm_live_info_fallback()
            return False
        if self._live_info_state is not None:
            self._live_info_timer.start(LIVE_INFO_POLL_MS, True)
            return False

        needs_epg, needs_picon = self._live_info_missing_components()
        defer_stalker_epg = bool(
            needs_epg
            and str(getattr(self.client, "source_type", "") or "")
            == "stalker"
            and elapsed_ms < STALKER_LIVE_EPG_QUIET_MS
        )
        if defer_stalker_epg:
            # A portal EPG request shares the single MAG API slot with
            # create_link and urllib cannot retract a request already sent.
            # Coalesce rapid zaps for a short interval after playback starts;
            # the channel link has already been resolved before this timer.
            needs_epg = False
        stream_id = str(getattr(self.current_item, "stream_id", "") or "")
        generation = self._generation
        state = {
            "generation": generation,
            "stream_id": stream_id,
            "events": {},
            "tokens": {},
            "applied": set(),
        }
        workers = []
        if needs_epg:
            refresh_epg = self._live_info_epg_refresh_generation == generation
            self._live_info_epg_refresh_generation = -1
            event = threading.Event()
            token = _WorkerToken(LIVE_INFO_EPG_TIMEOUT_SECONDS)
            state["events"]["events"] = event
            state["tokens"]["events"] = token
            workers.append(
                threading.Thread(
                    target=self._load_live_info_epg_worker,
                    args=(generation, stream_id, event, token, refresh_epg),
                )
            )
        if needs_picon:
            event = threading.Event()
            token = _WorkerToken(max(3, LIVE_PICON_DOWNLOAD_TIMEOUT + 1))
            state["events"]["picon_path"] = event
            state["tokens"]["picon_path"] = token
            workers.append(
                threading.Thread(
                    target=self._load_live_info_picon_worker,
                    args=(generation, stream_id, self.current_item, event, token),
                )
            )
        if defer_stalker_epg:
            delay_ms = max(10, STALKER_LIVE_EPG_QUIET_MS - elapsed_ms)
            self._live_info_queued_generation = generation
            self._live_info_due_at = (
                time.monotonic() + (delay_ms / 1000.0)
            )
        else:
            self._live_info_queued_generation = -1
            self._live_info_due_at = 0.0
        if not workers:
            if defer_stalker_epg:
                self._live_info_timer.start(delay_ms, True)
            return False
        self._live_info_state = state
        for worker in workers:
            worker.daemon = True
            worker.start()
        self._live_info_timer.start(LIVE_INFO_POLL_MS, True)
        _debug(
            "live info completion started id={} epg={} picon={}".format(
                stream_id,
                needs_epg,
                needs_picon,
            )
        )
        return True

    def _publish_live_info_result(
        self,
        generation,
        stream_id,
        component,
        value,
        token,
    ):
        key = (int(generation), str(stream_id or ""), str(component or ""))
        with self._live_info_results_lock:
            state = self._live_info_state
            if (
                self._closed
                or token is None
                or not token.active()
                or int(generation) != self._generation
                or state is None
                or int(state.get("generation", -1)) != int(generation)
                or str(state.get("stream_id", "") or "") != key[1]
                or state.get("tokens", {}).get(key[2]) is not token
                or key[2] not in state.get("events", {})
            ):
                return
            self._live_info_results[key] = value

    def _load_live_info_epg_worker(
        self,
        generation,
        stream_id,
        worker_event,
        worker_token,
        refresh=False,
    ):
        try:
            loader = self._live_info_loader()
            events = []
            if loader is not None:
                events = _bounded_client_call(
                    self.client,
                    worker_token,
                    lambda: _load_shared_live_epg(
                        self.client,
                        stream_id,
                        LIVE_EPG_FETCH_LIMIT,
                        loader,
                        token=worker_token,
                        refresh=refresh,
                    ),
                )
            self._publish_live_info_result(
                generation,
                stream_id,
                "events",
                list(events or []),
                worker_token,
            )
        except _WorkerCancelled:
            pass
        except Exception:
            self._publish_live_info_result(
                generation,
                stream_id,
                "events",
                [],
                worker_token,
            )
        finally:
            worker_event.set()

    def _load_live_info_picon_worker(
        self,
        generation,
        stream_id,
        item,
        worker_event,
        worker_token,
    ):
        try:
            path = _load_live_picon_path(
                self.client,
                item,
                worker_token=worker_token,
            )
            self._publish_live_info_result(
                generation,
                stream_id,
                "picon_path",
                path,
                worker_token,
            )
        except _WorkerCancelled:
            pass
        except Exception:
            self._publish_live_info_result(
                generation,
                stream_id,
                "picon_path",
                "",
                worker_token,
            )
        finally:
            worker_event.set()

    def _apply_live_info_result(self, state, component, value):
        generation = int(state.get("generation", -1))
        stream_id = str(state.get("stream_id", "") or "")
        if (
            generation != self._generation
            or stream_id
            != str(getattr(self.current_item, "stream_id", "") or "")
            or self._live_transition_in_progress()
        ):
            return False
        if component == "events":
            events = list(value or [])
            if events:
                self._live_info_epg_retry_after.pop(stream_id, None)
                self._remember_live_snapshot_component(
                    stream_id,
                    "events",
                    events,
                )
                self._live_snapshot_events = events
                self._render_live_snapshot_epg(now=time.time())
                if self._info_visible:
                    # Leave enough time to read a reply that arrived near
                    # the end of the banner timeout. Respect a hidden banner.
                    self._hide_timer.start(INFO_TIMEOUT_MS, True)
            else:
                self._live_info_epg_retry_after[stream_id] = (
                    time.monotonic() + LIVE_DETAIL_EMPTY_RETRY_SECONDS
                )
                if (
                    len(self._live_info_epg_retry_after)
                    > LIVE_EPG_CACHE_MAX_ENTRIES
                ):
                    oldest = min(
                        self._live_info_epg_retry_after,
                        key=self._live_info_epg_retry_after.get,
                    )
                    self._live_info_epg_retry_after.pop(oldest, None)
            if not events:
                self._refresh_front_panel()
            return bool(events)
        if component == "picon_path":
            path = str(value or "").strip()
            if not path or not _valid_live_picon_file(path):
                return False
            self._remember_live_snapshot_component(
                stream_id,
                "picon_path",
                path,
            )
            self._cached_picon_generation = generation
            self._picon_request_generation = generation
            self._apply_picon(generation, path)
            return True
        return False

    def _live_info_tick(self):
        if self._closed:
            return
        state = self._live_info_state
        if state is None:
            if self._live_info_queued_generation != self._generation:
                return
            remaining = self._live_info_due_at - time.monotonic()
            if remaining > 0.01:
                self._live_info_timer.start(
                    max(10, int(round(remaining * 1000))),
                    True,
                )
                return
            elapsed_ms = max(
                0,
                int(
                    round(
                        (time.monotonic() - self._live_play_started_at) * 1000
                    )
                ),
            )
            self._start_live_info_completion(
                force=elapsed_ms >= LIVE_INFO_FALLBACK_MS
            )
            return

        generation = int(state.get("generation", -1))
        stream_id = str(state.get("stream_id", "") or "")
        for component, event in state.get("events", {}).items():
            token = state.get("tokens", {}).get(component)
            if not event.is_set() and token is not None and token.expired():
                token.cancel()
            key = (generation, stream_id, component)
            with self._live_info_results_lock:
                present = key in self._live_info_results
                value = self._live_info_results.pop(key, None)
            if present and component not in state["applied"]:
                state["applied"].add(component)
                self._apply_live_info_result(state, component, value)

        finished = all(
            event.is_set()
            or (
                state.get("tokens", {}).get(component) is not None
                and not state["tokens"][component].active()
            )
            for component, event in state.get("events", {}).items()
        )
        if not finished:
            self._live_info_timer.start(LIVE_INFO_POLL_MS, True)
            return
        self._live_info_state = None
        if self._live_info_queued_generation == self._generation:
            elapsed_ms = max(
                0,
                int(
                    round(
                        (time.monotonic() - self._live_play_started_at) * 1000
                    )
                ),
            )
            self._start_live_info_completion(
                force=elapsed_ms >= LIVE_INFO_FALLBACK_MS
            )

    def _restore_old_service(self, navigation=None):
        """Restore the pre-player service at most once."""
        if self._old_reference_restored:
            return True
        navigation = navigation or getattr(self.session, "nav", None)
        if self.old_reference is None:
            self._old_reference_restored = True
            return True
        if navigation is None:
            return False
        try:
            _play_service(navigation, self.old_reference)
        except Exception:
            return False
        self._old_reference_restored = True
        return True

    def start_playback(self):
        if self._closed or self._started:
            return
        navigation = getattr(self.session, "nav", None)
        if navigation is None:
            self._set_static_info_channel_name(
                _("Could not start the player.")
            )
            self.show_info()
            return
        getter = getattr(navigation, "getCurrentlyPlayingServiceReference", None)
        active_reference = None
        preparation = getattr(self, "_archive_initial_playback", None)
        if self._is_archive_item() and preparation is not None:
            if not preparation.ready():
                # A zap/EXIT can win the gap between session.open and onShown.
                # Closing lets stop_playback restore only a slot we still own.
                self.close()
                return
            self.old_reference = preparation.previous_reference
            preparation.released = False
            self._archive_initial_playback = None
        elif getter is not None:
            try:
                active_reference = getter()
                self.old_reference = (
                    self._old_reference_override
                    if self._adopt_playing_service
                    else active_reference
                )
            except Exception:
                self.old_reference = (
                    self._old_reference_override
                    if self._adopt_playing_service
                    else None
                )
        self._old_reference_restored = False
        if self._is_live_item() and self._adopt_playing_service:
            if not GTStreamListScreen._same_service_reference(
                active_reference,
                self.reference,
            ):
                # The list promised an exact decoder handoff. If another
                # service took ownership before this modal screen appeared,
                # never replay or re-resolve the temporary provider URL.
                restored = self._restore_old_service(navigation)
                self._fullscreen_close_result = self._live_fullscreen_result(
                    False,
                    released=restored,
                )
                self.close(self._fullscreen_close_result)
                return
            self.reference = active_reference
            self._active_live_service_type = reference_service_type(
                active_reference,
                4097,
            )
            self._started = True
            self._generation += 1
            _debug(
                "playback adopted content=live actual_engine={} "
                "reason=preview_handoff".format(
                    self._active_live_service_type,
                )
            )
            arm_startup = getattr(self, "_arm_live_startup", None)
            if callable(arm_startup):
                arm_startup()
            self._after_zap()
            return
        if self._is_live_item():
            # Give Enigma2 one fresh reference through one immediate
            # playService() call and let navigation replace the old service
            # atomically, without an avoidable stop/start pair.
            # Do not run stopService() or a release timer here. The explicit
            # 0-key recovery path intentionally keeps stop + 1000-ms restart.
            # Rebuilding this reference preserves the concrete stream URL
            # without issuing a second provider request.
            self._stop_live_auxiliary_timers()
            next_reference = self._fresh_live_reference()
            try:
                _play_service(navigation, next_reference)
            except Exception as error:
                _debug(
                    "live initial start failed error={}".format(
                        error.__class__.__name__
                    )
                )
                self._restore_old_service(navigation)
                self._set_static_info_channel_name(
                    _("Could not open the stream.")
                )
                self.show_info()
                return
            self.reference = next_reference
            self._active_live_service_type = reference_service_type(
                next_reference,
                4097,
            )
            self._started = True
            self._generation += 1
            snapshot = self._live_snapshot_for_current_item()
            _debug(
                "playback started content=live selected_engine={} "
                "actual_engine={} reason=direct_screen_open "
                "snapshot_events={} snapshot_picon={} "
                "snapshot_weather={}".format(
                    self._active_live_service_type,
                    reference_service_type(next_reference, 4097),
                    len(self._live_snapshot_events),
                    bool(snapshot.get("picon_path")),
                    bool(
                        snapshot.get(
                            "weather_forecast",
                            snapshot.get("weather"),
                        )
                    ),
                )
            )
            arm_startup = getattr(self, "_arm_live_startup", None)
            if callable(arm_startup):
                arm_startup()
            self._after_zap()
            return

        if self._is_archive_item():
            service_getter = getattr(navigation, "getCurrentService", None)
            try:
                self._archive_previous_service = (
                    service_getter() if callable(service_getter) else None
                )
            except Exception:
                self._archive_previous_service = None
            try:
                previous_snapshot = _decoder_media_snapshot(
                    self._archive_previous_service,
                    iServiceInformation,
                )
            except Exception:
                previous_snapshot = {}
            self._archive_previous_pts = (
                _ArchivePlaybackWatchdog._snapshot_pts(previous_snapshot)
            )
            self._archive_previous_position = (
                _ArchivePlaybackWatchdog._position_ticks(
                    self._archive_previous_service
                )
            )
        try:
            _play_service(navigation, self.reference)
        except Exception as error:
            _debug(
                "playback start failed content={} error={}".format(
                    getattr(self.current_item, "content_type", "unknown"),
                    error.__class__.__name__,
                )
            )
            self._restore_old_service(navigation)
            if self._is_archive_item():
                self._archive_startup_failed()
                return
            self._set_static_info_channel_name(
                _("Could not open the stream.")
            )
            self.show_info()
            return
        _debug(
            "playback started content={} selected_engine={} actual_engine={} reason=screen_open".format(
                getattr(self.current_item, "content_type", "unknown"),
                self._active_live_service_type,
                reference_service_type(self.reference, 4097),
            )
        )
        self._started = True
        self._generation += 1
        self._after_zap()

    def _after_zap(self, refresh_epg=False):
        self._cancel_seek_verification()
        self._stop_live_auxiliary_timers()
        # Never leave the previous channel logo visible while the new service
        # is starting. The snapshot/completion path restores current artwork.
        self._reset_picon_display()
        self._current_epg_events = []
        self._live_snapshot_events = []
        is_live = self._is_live_item()
        if (
            is_live
            and refresh_epg
            and str(
                getattr(self.client, "source_type", "xtream") or "xtream"
            ).lower() in ("xtream", "stalker")
        ):
            self._live_info_epg_refresh_generation = self._generation
        if is_live:
            # Consume the channel's immutable snapshot before the first banner
            # render so CH+/CH- can never flash the previous channel's EPG.
            self._apply_live_snapshot()
        self._render_channel()
        self._refresh_front_panel()
        self._defer_live_metadata_once = is_live
        try:
            self.show_info()
        finally:
            self._defer_live_metadata_once = False
        self._metadata_attempts = 0
        self._live_metadata_complete_generation = -1
        self._live_metadata_ready = {}
        self._live_play_started_at = time.monotonic()
        self._live_info_ready_samples = 0
        if is_live:
            # The decoder commonly needs a short settling window before it
            # exposes width/FPS/codec/audio. Permit only a small, bounded group
            # of decoder-local samples; this timer starts no network work.
            self._live_metadata_settle_pending = True
        metadata_delay = (
            LIVE_INFO_METADATA_INITIAL_DELAY_MS
            if is_live
            else LIVE_METADATA_INITIAL_DELAY_MS
        )
        self._metadata_timer.start(metadata_delay, True)
        try:
            self._playback_timer.stop()
        except Exception:
            pass
        try:
            self._resume_timer.stop()
        except Exception:
            pass
        self._reset_live_reconnect()
        if not is_live:
            # VOD decoders need a short moment before seek().getLength()
            # becomes available for a freshly opened stream.
            self._playback_timer.start(650, True)
            if self._resume_start_position > 0:
                self._resume_attempts = 0
                self._resume_last_length = -1
                self._resume_stable_length_samples = 0
                self._resume_timer.start(RESUME_INITIAL_DELAY_MS, True)
        if is_live:
            # Snapshot/cache data is already visible. Fill missing details and
            # refresh portal EPG after CH+/CH- without holding up playback.
            self._schedule_live_info_completion()
        else:
            self._start_picon_load()
            self._start_player_weather()
        if self._is_archive_item():
            self._archive_portal_keepalive()
            self._arm_archive_startup()

    def _stop_live_auxiliary_timers(self):
        for timer in (
            self._metadata_timer,
            self._live_info_timer,
            self._picon_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass
        self._live_metadata_settle_pending = False
        self._cancel_live_info_state()
        for attribute in ("_weather_token", "_picon_worker_token"):
            token = getattr(self, attribute, None)
            _cancel_worker_token(token)
            setattr(self, attribute, None)

    def _live_transition_in_progress(self):
        """Return True while current_item/reference are intentionally stale."""
        return bool(
            getattr(self.current_item, "content_type", "") == "live"
            and (
                self._pending_channel_index is not None
                or self._pending_engine_reference is not None
                or self._live_reconnecting
            )
        )

    def _reset_live_reconnect(self):
        """Cancel an in-flight user-requested live restart."""
        try:
            self._reconnect_timer.stop()
        except Exception:
            pass
        with self._reconnect_link_lock:
            token = self._reconnect_link_token
            self._live_reconnecting = False
            self._reconnect_generation = -1
            self._reconnect_link_pending = False
            self._reconnect_link_ready = False
            self._reconnect_link_url = ""
            self._reconnect_link_deadline = 0.0
            self._reconnect_link_token = None
            self._reconnect_reason = ""
            self._reconnect_decoder_released = False
            self._reconnect_pending_reference = None
            self._reconnect_queued = False
        _cancel_worker_token(token)

    def cycle_live_service_type(self):
        """Cycle only through engines installed on the receiver."""
        if getattr(self.current_item, "content_type", "") != "live":
            self.show_info()
            return
        if (
            self._live_reconnecting
            or self._pending_engine_reference is not None
            or self._pending_channel_index is not None
        ):
            self.show_info()
            return
        navigation = getattr(self.session, "nav", None)
        url = reference_stream_url(self.reference)
        if navigation is None or not url:
            self.show_info()
            return

        current_type = reference_service_type(
            self.reference,
            self._active_live_service_type,
        )
        requested_types = list(available_service_types())
        if current_type not in requested_types:
            # ServiceApp may register 5001/5002 without the conventional
            # executable path. Never drop the engine that is already working.
            requested_types.insert(0, current_type)
        candidates = []
        seen = set()
        for requested_type in requested_types:
            try:
                candidate = build_extplayer_reference(
                    url,
                    getattr(self.current_item, "name", self.title),
                    "live",
                    service_type=requested_type,
                )
            except Exception:
                continue
            actual_type = reference_service_type(candidate, 4097)
            # Ambiguous/non-TS URLs map native type 1 to 4097; do not show
            # that same effective engine twice while cycling.
            if actual_type in seen:
                continue
            seen.add(actual_type)
            candidates.append((actual_type, candidate))
        if len(candidates) < 2:
            self.show_info()
            return

        # Only an accepted engine change supersedes a startup watch. An
        # unavailable alternate engine leaves the current attempt untouched.
        if (
            callable(getattr(self, "_cancel_live_startup", None))
            and self._cancel_live_startup(restore_released=True) is False
        ):
            self.show_info()
            return

        candidate_types = [item[0] for item in candidates]
        try:
            target_index = (
                candidate_types.index(current_type) + 1
            ) % len(candidates)
        except ValueError:
            target_index = (
                candidate_types.index(4097)
                if 4097 in candidate_types
                else 0
            )
        target_type, target_reference = candidates[target_index]
        try:
            self._engine_switch_timer.stop()
        except Exception:
            pass
        self._stop_live_auxiliary_timers()
        self._reset_live_reconnect()
        self._generation += 1
        self._pending_engine_reference = target_reference
        self._previous_engine_reference = self.reference
        self._engine_switch_generation = self._generation
        self._engine_link_pending = False
        self._engine_link_ready = False
        self._engine_link_url = ""
        self._engine_link_deadline = 0.0
        self._engine_decoder_released = False
        self._set_static_info_epg(
            "epg_now",
            _("Reconnecting the stream...")
        )
        self._set_static_info_epg("epg_next", _("Please wait"))
        self.show_info()
        # INFO/TV changes only the local Enigma2 engine.  In particular, it
        # never asks a Stalker/MAC portal for a replacement link or session;
        # the physical 0 key is the sole live-refresh control.
        self._release_live_engine_for_switch(
            navigation,
            current_type,
            target_type,
        )

    def _clear_live_engine_switch_state(self):
        try:
            self._engine_switch_timer.stop()
        except Exception:
            pass
        self._pending_engine_reference = None
        self._previous_engine_reference = None
        self._engine_switch_generation = -1
        self._engine_link_pending = False
        self._engine_link_ready = False
        self._engine_link_url = ""
        self._engine_link_deadline = 0.0
        _cancel_worker_token(self._engine_link_token)
        self._engine_link_token = None
        self._engine_decoder_released = False

    def _refresh_engine_switch_link(
        self,
        generation,
        refresher,
        item,
        token=None,
    ):
        try:
            url = str(
                _bounded_client_call(
                    self.client,
                    token,
                    lambda: refresher(item),
                )
                or ""
            ).strip()
        except Exception as error:
            _debug(
                "playback engine switch link failed error={}".format(
                    error.__class__.__name__
                )
            )
            url = ""
        if (
            self._closed
            or (token is not None and not token.active())
            or self._pending_engine_reference is None
            or self._engine_switch_generation != generation
        ):
            return
        self._engine_link_url = url
        self._engine_link_ready = True

    def _release_live_engine_for_switch(
        self,
        navigation,
        previous_type,
        target_type,
    ):
        try:
            navigation.stopService()
        except Exception as error:
            _debug(
                "playback engine release failed from={} to={} error={}".format(
                    previous_type,
                    target_type,
                    error.__class__.__name__,
                )
            )
        self._engine_decoder_released = True
        _debug(
            "playback engine switch scheduled from={} to={} reason=user "
            "release_delay_ms={}".format(
                previous_type,
                target_type,
                LIVE_RECONNECT_RELEASE_DELAY_MS,
            )
        )
        self._engine_switch_timer.start(
            LIVE_RECONNECT_RELEASE_DELAY_MS,
            True,
        )

    def _complete_live_engine_switch(self):
        target_reference = self._pending_engine_reference
        previous_reference = self._previous_engine_reference
        if target_reference is None:
            return
        if (
            self._closed
            or self._engine_switch_generation != self._generation
        ):
            self._clear_live_engine_switch_state()
            return
        navigation = getattr(self.session, "nav", None)
        if navigation is None:
            self._clear_live_engine_switch_state()
            return
        previous_type = reference_service_type(previous_reference, 4097)
        target_type = reference_service_type(target_reference, 4097)
        if self._engine_link_pending:
            if (
                not self._engine_link_ready
                and time.monotonic() < self._engine_link_deadline
            ):
                self._engine_switch_timer.start(100, True)
                return
            refreshed_url = self._engine_link_url if self._engine_link_ready else ""
            if not refreshed_url:
                # The old service was deliberately kept alive while resolving
                # the stream link. Resume its metadata timers and abandon only
                # this engine switch.
                self._clear_live_engine_switch_state()
                self._set_static_info_epg(
                    "epg_next",
                    _("Keeping the current stream")
                )
                arm_startup = getattr(self, "_arm_live_startup", None)
                if callable(arm_startup):
                    arm_startup()
                self._after_zap()
                return
            try:
                target_reference = build_extplayer_reference(
                    refreshed_url,
                    getattr(self.current_item, "name", self.title),
                    "live",
                    service_type=target_type,
                )
                previous_reference = build_extplayer_reference(
                    refreshed_url,
                    getattr(self.current_item, "name", self.title),
                    "live",
                    service_type=previous_type,
                )
            except Exception as error:
                _debug(
                    "playback engine switch reference failed error={}".format(
                        error.__class__.__name__
                    )
                )
                self._clear_live_engine_switch_state()
                arm_startup = getattr(self, "_arm_live_startup", None)
                if callable(arm_startup):
                    arm_startup()
                self._after_zap()
                return
            target_type = reference_service_type(target_reference, 4097)
            previous_type = reference_service_type(previous_reference, 4097)
            self._pending_engine_reference = target_reference
            self._previous_engine_reference = previous_reference
            self._engine_link_pending = False
            self._release_live_engine_for_switch(
                navigation,
                previous_type,
                target_type,
            )
            return
        if not self._engine_decoder_released:
            self._release_live_engine_for_switch(
                navigation,
                previous_type,
                target_type,
            )
            return
        try:
            _play_service(navigation, target_reference)
        except Exception as error:
            _debug(
                "playback engine switch failed from={} to={} error={}".format(
                    previous_type,
                    target_type,
                    error.__class__.__name__,
                )
            )
            if previous_reference is not None:
                try:
                    _play_service(navigation, previous_reference)
                    self.reference = previous_reference
                    self._active_live_service_type = previous_type
                except Exception:
                    pass
            self._clear_live_engine_switch_state()
            arm_startup = getattr(self, "_arm_live_startup", None)
            if callable(arm_startup):
                arm_startup()
            self._after_zap()
            return
        self.reference = target_reference
        self._active_live_service_type = target_type
        self._clear_live_engine_switch_state()
        self._info_dialog["engine"].setText(service_engine_label(target_type))
        _debug(
            "playback engine switched from={} to={} reason=user".format(
                previous_type,
                target_type,
            )
        )
        arm_startup = getattr(self, "_arm_live_startup", None)
        if callable(arm_startup):
            arm_startup()
        self._after_zap()

    def restart_live_stream(self):
        """Restart the current live stream only after an explicit 0 key."""
        if getattr(self.current_item, "content_type", "") != "live":
            self.show_info()
            return
        self._restart_live_stream()

    def _restart_live_stream(self):
        # R93 has no automatic live watch/retry to cancel.  Every visit to this
        # method originates from the physical 0 action mapping.
        self._cancel_live_startup()
        if self._live_reconnecting:
            # Keep one explicit user request instead of silently discarding
            # every 0 press while a provider link is still being prepared.
            self._reconnect_queued = True
            log_event(
                "live_recovery",
                "manual request queued engine={}".format(
                    reference_service_type(self.reference, 4097)
                ),
            )
            self.show_info()
            return
        if self._pending_engine_reference is not None or self._closed:
            return
        if getattr(self.current_item, "content_type", "") != "live":
            return
        self._live_reconnecting = True
        navigation = getattr(self.session, "nav", None)
        if navigation is None:
            self._live_reconnecting = False
            return
        self._reconnect_reason = "manual"
        log_event(
            "live_recovery",
            "manual request started engine={}".format(
                reference_service_type(self.reference, 4097)
            ),
        )
        with self._reconnect_link_lock:
            old_token = self._reconnect_link_token
            self._reconnect_generation = self._generation
            self._reconnect_link_pending = False
            self._reconnect_link_ready = False
            self._reconnect_link_url = ""
            self._reconnect_link_deadline = 0.0
            self._reconnect_decoder_released = False
            self._reconnect_pending_reference = None
            self._reconnect_queued = False
            self._reconnect_link_token = None
        _cancel_worker_token(old_token)
        self._set_static_info_epg(
            "epg_now",
            _("Reconnecting the stream...")
        )
        self._set_static_info_epg("epg_next", _("Please wait"))
        self.show_info()
        self._stop_live_auxiliary_timers()
        refresher = None
        if _is_stalker_client(self.client):
            # Only 0 performs the full MAC-session + fresh-link operation.
            refresher = getattr(
                self.client,
                "recover_live_playback_url",
                None,
            )
        if not callable(refresher):
            refresher = getattr(self.client, "refresh_playback_url", None)
        if callable(refresher) and self.current_item is not None:
            with self._reconnect_link_lock:
                self._reconnect_link_pending = True
            try:
                link_timeout = max(3, min(15, int(self.client.timeout)))
            except (AttributeError, TypeError, ValueError, OverflowError):
                link_timeout = 12
            token = _WorkerToken(
                min(WORKER_PLAYBACK_LINK_TIMEOUT_SECONDS, link_timeout + 1)
            )
            with self._reconnect_link_lock:
                self._reconnect_link_token = token
                self._reconnect_link_deadline = token.deadline
            generation = self._reconnect_generation
            item = self.current_item
            worker = threading.Thread(
                target=self._refresh_reconnect_link,
                args=(generation, refresher, item, token),
            )
            worker.daemon = True
            worker.start()
        else:
            # Stalker tickets must not be reused when their resolver is
            # unavailable. Other live sources retain the established local
            # decoder-restart behavior using the URL in the current reference.
            local_url = ""
            if not _is_stalker_client(self.client):
                local_url = reference_stream_url(self.reference)
            with self._reconnect_link_lock:
                self._reconnect_link_url = str(local_url or "").strip()
                self._reconnect_link_ready = True
        _debug(
            "live restart resolving engine={} reason={} release_delay_ms={}".format(
                reference_service_type(self.reference, 4097),
                self._reconnect_reason,
                LIVE_RECONNECT_RELEASE_DELAY_MS,
            )
        )
        # Keep the current service owned until a replacement URL is ready.
        # This prevents a transient portal failure from turning a recoverable
        # stream into a stopped decoder or replaying its consumed old ticket.
        self._reconnect_timer.start(100, True)

    def _refresh_reconnect_link(
        self,
        generation,
        refresher,
        item,
        token=None,
    ):
        try:
            url = str(
                _bounded_client_call(
                    self.client,
                    token,
                    lambda: refresher(item),
                )
                or ""
            ).strip()
        except Exception as error:
            _debug(
                "live reconnect link refresh failed error={}".format(
                    error.__class__.__name__
                )
            )
            url = ""
        with self._reconnect_link_lock:
            if (
                self._closed
                or token is None
                or not token.active()
                or self._reconnect_link_token is not token
                or not self._live_reconnecting
                or self._reconnect_generation != generation
            ):
                return
            self._reconnect_link_url = url
            self._reconnect_link_ready = True

    def _fresh_live_reference(
        self,
        refreshed_url="",
        require_refreshed=False,
    ):
        url = str(refreshed_url or "").strip()
        if not url:
            url = reference_stream_url(self.reference)
        if not url:
            return self.reference
        content_type = getattr(self.current_item, "content_type", "live")
        fallback_type = 4097 if content_type == "live" else 5002
        try:
            return build_extplayer_reference(
                url,
                getattr(self.current_item, "name", self.title),
                content_type,
                service_type=(
                    self._active_live_service_type
                    if content_type == "live"
                    else reference_service_type(self.reference, fallback_type)
                ),
            )
        except Exception as error:
            _debug(
                "live reconnect reference rebuild failed error={}".format(
                    error.__class__.__name__
                )
            )
            return None if require_refreshed else self.reference

    def _complete_live_restart(self):
        if not self._live_reconnecting or self._closed:
            return
        if self._reconnect_generation != self._generation:
            self._reset_live_reconnect()
            return
        navigation = getattr(self.session, "nav", None)
        if navigation is None:
            self._reset_live_reconnect()
            return

        def resume_preserved_service():
            """Restore local jobs after a manual attempt kept/rolled back live."""

            self._reset_live_reconnect()
            self._after_zap()
            arm_startup = getattr(self, "_arm_live_startup", None)
            if callable(arm_startup):
                arm_startup()

        with self._reconnect_link_lock:
            link_pending = self._reconnect_link_pending
            link_ready = self._reconnect_link_ready
            link_deadline = self._reconnect_link_deadline
        if (
            link_pending
            and not link_ready
            and time.monotonic() < link_deadline
        ):
            # Keep this polling timer short while the provider creates a fresh
            # temporary live link. The current service is still untouched.
            self._reconnect_timer.start(100, True)
            return
        if link_pending and not link_ready:
            with self._reconnect_link_lock:
                self._reconnect_link_ready = True
                self._reconnect_link_url = ""
        with self._reconnect_link_lock:
            link_url = self._reconnect_link_url
        if not link_url:
            queued = bool(self._reconnect_queued)
            log_event(
                "live_recovery",
                "manual link outcome=unavailable current_service=preserved",
            )
            resume_preserved_service()
            self._set_static_info_epg(
                "epg_now",
                _("Could not open the stream."),
            )
            self._set_static_info_epg(
                "epg_next",
                _("Keeping the current stream"),
            )
            self.show_info()
            if queued and not self._closed:
                self._restart_live_stream()
            return
        if self._reconnect_pending_reference is None:
            self._reconnect_pending_reference = self._fresh_live_reference(
                link_url,
                require_refreshed=True,
            )
        next_reference = self._reconnect_pending_reference
        if next_reference is None:
            resume_preserved_service()
            return
        if not self._reconnect_decoder_released:
            try:
                navigation.stopService()
            except Exception as error:
                _debug(
                    "live reconnect teardown failed error={}".format(
                        error.__class__.__name__
                    )
                )
                resume_preserved_service()
                return
            self._reconnect_decoder_released = True
            log_event(
                "live_recovery",
                "manual release started delay_ms={}".format(
                    LIVE_RECONNECT_RELEASE_DELAY_MS
                ),
            )
            try:
                self._reconnect_timer.start(
                    LIVE_RECONNECT_RELEASE_DELAY_MS,
                    True,
                )
            except Exception:
                # stopService has already committed the release. If the GUI
                # timer cannot be armed, complete the prepared replay now so
                # navigation cannot remain empty indefinitely.
                self._complete_live_restart()
            return
        try:
            _play_service(navigation, next_reference)
        except Exception as error:
            _debug(
                "live reconnect failed error={}".format(
                    error.__class__.__name__
                )
            )
            try:
                _play_service(navigation, self.reference)
                log_event(
                    "live_recovery",
                    "manual replay outcome=rollback",
                )
            except Exception:
                pass
            resume_preserved_service()
            return
        # Preserve the accepted service before clearing worker/token state.
        # Repeated 0 presses collapse into one boolean follow-up request.
        self.reference = next_reference
        self._active_live_service_type = reference_service_type(
            next_reference,
            4097,
        )
        queued = bool(self._reconnect_queued)
        reconnect_reason = self._reconnect_reason or "manual"
        _debug(
            "live stream restarted engine={} reason={}".format(
                self._active_live_service_type,
                reconnect_reason,
            )
        )
        self._generation += 1
        log_event(
            "live_recovery",
            "manual replay outcome=started engine={}".format(
                self._active_live_service_type
            ),
        )
        resume_preserved_service()
        if queued and not self._closed:
            self._restart_live_stream()

    def _render_channel(self):
        if self._entries:
            number = self.current_index + 1
            self._info_dialog["channel_number"].setText("{:02d}".format(number))
        else:
            self._info_dialog["channel_number"].setText("")
        title = getattr(self.current_item, "name", self.title)
        self.title = str(title or "GT IPTV")
        self._set_info_channel_name(self.title)
        fallback_type = (
            4097
            if getattr(self.current_item, "content_type", "") == "live"
            else 5002
        )
        self._info_dialog["engine"].setText(
            service_engine_label(
                reference_service_type(self.reference, fallback_type)
            )
        )
        self._info_dialog["resolution"].setText(_("Reading resolution"))
        self._info_dialog["fps"].setText("-- FPS")
        self._info_dialog["video_codec"].setText(_("VIDEO: --"))
        self._info_dialog["audio_codec"].setText(_("AUDIO: --"))
        is_live = getattr(self.current_item, "content_type", "") == "live"
        self._info_dialog["epg_now_label"].setText(
            _("NOW") if is_live else _("PREVIOUS")
        )
        self._info_dialog["epg_next_label"].setText(
            _("NEXT") if is_live else _("REMAINING")
        )
        if is_live:
            epg_text = (
                _("EPG information unavailable.")
                if self._live_info_loader() is None
                else _("Loading EPG...")
            )
            self._set_static_info_epg("epg_now", epg_text)
            self._set_static_info_epg("epg_next", "")
        else:
            self._set_static_info_epg("epg_now", _("Reading duration..."))
            self._set_static_info_epg("epg_next", "--:--:--")
        self._clear_player_progress()
        self._render_favorite_badge()
        self.setTitle(self.title)

    def _current_favorite_item(self):
        item = self.current_item
        if getattr(item, "content_type", "") == "series":
            parent = getattr(
                item,
                "favorite_parent",
                None,
            ) or self._favorite_parent
            if parent is not None:
                return parent
        return item

    def _render_favorite_badge(self):
        item = self._current_favorite_item()
        widget = self._info_dialog["favorite_badge"]
        if item is not None and self._is_favorite(item):
            widget.setText(localized_upper(_("Favorite")))
            widget.show()
        else:
            widget.hide()

    def toggle_current_favorite(self):
        item = self._current_favorite_item()
        if item is None:
            self.show_info()
            return True
        self._toggle_favorite_item(item)
        self._render_favorite_badge()
        self.show_info()
        return True

    def toggle_favorite_with_blue_long(self):
        """Keep fullscreen VOD favorites on images with a BLUE-long action."""
        now = time.monotonic()
        self._suppress_next_blue_short_until = (
            now + BLUE_LONG_SHORT_SUPPRESS_SECONDS
        )
        # A few keymaps repeat their long action while the key stays down.
        # Refresh the timestamp even for a rejected repeat so one held key
        # cannot cross the debounce window and toggle the favorite again.
        previous = self._blue_long_last_at
        self._blue_long_last_at = now
        if now - previous < BLUE_LONG_REPEAT_SUPPRESS_SECONDS:
            return True
        return self.toggle_current_favorite()

    @staticmethod
    def _audio_track_text(value, limit=48):
        text = " ".join(
            str(value or "").replace("\r", " ").replace("\n", " ").split()
        )
        return text[:max(1, int(limit))]

    @classmethod
    def _audio_language_name(cls, value):
        """Return the decoder language without a dynamic gettext lookup."""
        code = cls._audio_track_text(value, 24)
        return localized_language_name(code)

    @classmethod
    def _audio_track_details(cls, track_info):
        description = ""
        language = ""
        if track_info is not None:
            getter = getattr(track_info, "getDescription", None)
            if callable(getter):
                try:
                    description = localized_language_name(
                        cls._audio_track_text(getter(), 48)
                    )
                except Exception:
                    description = ""
            getter = getattr(track_info, "getLanguage", None)
            if callable(getter):
                try:
                    language = cls._audio_language_name(getter())
                except Exception:
                    language = ""
        return description, language

    @classmethod
    def _audio_track_label(cls, track_info, index):
        description, language = cls._audio_track_details(track_info)
        details = []
        for value in (language, description):
            if value and not any(
                value.casefold() == existing.casefold()
                for existing in details
            ):
                details.append(value)
        if not details:
            details.append(_(N_("Audio track")))
        return "{}. {}".format(index + 1, " • ".join(details))[:120]

    @classmethod
    def _audio_track_fingerprint(cls, track_info):
        return tuple(value.casefold() for value in cls._audio_track_details(track_info))

    @staticmethod
    def _reference_identity(reference):
        if reference is None:
            return ""
        for name in ("toCompareString", "toString"):
            getter = getattr(reference, name, None)
            if callable(getter):
                try:
                    value = getter()
                except Exception:
                    value = ""
                if value:
                    return str(value)
        return "{}:{}".format(
            reference_service_type(reference, 5002),
            reference_stream_url(reference),
        )

    def _playing_reference_identity(self):
        navigation = getattr(self.session, "nav", None)
        if navigation is not None:
            for name in (
                "getCurrentlyPlayingServiceReference",
                "getCurrentlyPlayingServiceOrGroup",
            ):
                getter = getattr(navigation, name, None)
                if not callable(getter):
                    continue
                try:
                    reference = getter()
                except Exception:
                    reference = None
                identity = self._reference_identity(reference)
                if identity:
                    return identity
        return self._reference_identity(self.reference)

    def _audio_selection_context(self):
        item = self.current_item
        return (
            int(self._generation),
            id(item),
            str(getattr(item, "content_type", "") or ""),
            str(getattr(item, "stream_id", "") or ""),
            self._playing_reference_identity(),
        )

    def _audio_tracks(self):
        service = self._current_service()
        getter = getattr(service, "audioTracks", None) if service is not None else None
        if not callable(getter):
            return service, None, 0
        try:
            tracks = getter()
            count_getter = getattr(tracks, "getNumberOfTracks", None)
            count = int(count_getter()) if callable(count_getter) else 0
        except Exception:
            return service, None, 0
        return service, tracks, max(0, count)

    def _show_audio_message(self, message):
        self._show_subtitle_message(message)

    def open_audio_selection(self):
        """Open decoder-provided VOD audio tracks without restarting playback."""
        now = time.monotonic()
        if self._suppress_next_blue_short_until:
            suppress = now <= self._suppress_next_blue_short_until
            self._suppress_next_blue_short_until = 0.0
            if suppress:
                return True
        if (
            self._closed
            or not self._started
            or getattr(self.current_item, "content_type", "")
            not in ("movie", "series")
        ):
            return True
        if getattr(self, "_audio_menu_open", False):
            return True

        _service, tracks, track_count = self._audio_tracks()
        if tracks is None or track_count <= 0:
            self._show_audio_message(N_("No audio tracks are available."))
            return True
        if track_count == 1:
            self._show_audio_message(
                N_("This content has only one audio track.")
            )
            return True
        if not callable(getattr(tracks, "selectTrack", None)):
            self._show_audio_message(
                N_("Audio track selection is unavailable on this image.")
            )
            return True
        try:
            from Screens.ChoiceBox import ChoiceBox
        except Exception:
            ChoiceBox = None
        opener = getattr(self.session, "openWithCallback", None)
        if ChoiceBox is None or not callable(opener):
            self._show_audio_message(
                N_("Audio track selection is unavailable on this image.")
            )
            return True

        choices = []
        current_track = -1
        getter = getattr(tracks, "getCurrentTrack", None)
        if callable(getter):
            try:
                current_track = int(getter())
            except Exception:
                current_track = -1
        visible_count = min(track_count, MAX_AUDIO_TRACKS)
        for index in range(visible_count):
            try:
                track_info = tracks.getTrackInfo(index)
            except Exception:
                track_info = None
            payload = (
                index,
                track_count,
                self._audio_track_fingerprint(track_info),
            )
            choices.append((self._audio_track_label(track_info, index), payload))
        expected_context = self._audio_selection_context()

        def selected(selection):
            self._audio_menu_open = False
            self._audio_selection_made(expected_context, selection)

        selection_index = (
            current_track if 0 <= current_track < visible_count else 0
        )
        self.hide_info()
        self._audio_menu_open = True
        try:
            opener(
                selected,
                ChoiceBox,
                _("Audio tracks"),
                choices,
                selection=selection_index,
            )
        except TypeError:
            try:
                opener(
                    selected,
                    ChoiceBox,
                    _("Audio tracks"),
                    choices,
                )
            except Exception:
                self._audio_menu_open = False
                self._show_audio_message(
                    N_("Audio track selection is unavailable on this image.")
                )
        except Exception:
            self._audio_menu_open = False
            self._show_audio_message(
                N_("Audio track selection is unavailable on this image.")
            )
        return True

    def _audio_selection_made(self, expected_context, selection):
        if self._closed or not selection:
            return
        if self._audio_selection_context() != expected_context:
            self._show_audio_message(
                N_("The playing service changed. Open audio selection again.")
            )
            return
        try:
            index, expected_count, expected_fingerprint = selection[1]
            index = int(index)
            expected_count = int(expected_count)
            expected_fingerprint = tuple(expected_fingerprint)
        except (IndexError, KeyError, TypeError, ValueError, OverflowError):
            return

        service, tracks, track_count = self._audio_tracks()
        if (
            tracks is None
            or track_count != expected_count
            or not 0 <= index < track_count
        ):
            self._show_audio_message(
                N_("Audio tracks changed. Open audio selection again.")
            )
            return
        try:
            track_info = tracks.getTrackInfo(index)
        except Exception:
            track_info = None
        if self._audio_track_fingerprint(track_info) != expected_fingerprint:
            self._show_audio_message(
                N_("Audio tracks changed. Open audio selection again.")
            )
            return
        selector = getattr(tracks, "selectTrack", None)
        if not callable(selector):
            self._show_audio_message(
                N_("Audio track selection is unavailable on this image.")
            )
            return
        try:
            # iAudioTrackSelection return values vary across GStreamer,
            # ServiceApp and image builds.  An exception is the only portable
            # failure signal; a non-zero integer is not treated as failure.
            selector(index)
        except Exception:
            self._show_audio_message(
                N_("Could not select the requested audio track.")
            )
            return

        # Selection is decoder-local: never rebuild the reference or call
        # playService(). Refresh only the existing infobar metadata.
        self._metadata_attempts = 0
        self._metadata_tick()
        if service is not None:
            description = self._audio_description(service)
            if description != "--":
                self._info_dialog["audio_codec"].setText(
                    _("AUDIO: {}").format(description)
                )
        self.show_info()

    def next_channel(self):
        self._switch_channel(1)

    def previous_channel(self):
        self._switch_channel(-1)

    def _switch_channel(self, step):
        if (
            not self._entries
            or self.client is None
        ):
            self.show_info()
            return
        if (
            getattr(self, "_live_reconnecting", False)
            and getattr(self, "_reconnect_decoder_released", False)
        ):
            # Manual 0 has already stopped the decoder and owns the only
            # delayed replay.  Cancelling that timer here could leave
            # navigation empty if the requested channel later fails.
            self.show_info()
            return
        if self._pending_engine_reference is not None:
            self.show_info()
            return
        direction = 1 if step > 0 else -1
        now = time.monotonic()
        if direction == self._last_channel_key_step and self._last_channel_key_at is not None:
            elapsed_ms = (now - self._last_channel_key_at) * 1000
            if elapsed_ms < CHANNEL_KEY_DUPLICATE_GUARD_MS:
                _debug(
                    "live channel key bounce ignored direction={} elapsed_ms={}".format(
                        direction,
                        int(elapsed_ms),
                    )
                )
                return
        self._last_channel_key_step = direction
        self._last_channel_key_at = now
        had_pending_target = self._pending_channel_index is not None
        base_index = (
            self._pending_channel_index
            if self._pending_channel_index is not None
            else self.current_index
        )
        if len(self._entries) < 2:
            self.show_info()
            return
        next_index = (base_index + direction) % len(self._entries)
        # The key survived the duplicate guard and names a real zap target.
        # From here on, late startup callbacks belong to the old service.
        if (
            callable(getattr(self, "_cancel_live_startup", None))
            and self._cancel_live_startup(restore_released=True) is False
        ):
            self.show_info()
            return
        _debug(
            "live channel key accepted direction={} current={} base={} target={}".format(
                direction,
                self.current_index,
                base_index,
                next_index,
            )
        )
        self._stop_live_auxiliary_timers()
        # Invalidate an in-flight logo immediately when the viewer expresses a
        # new channel intent. The download may finish in its daemon thread, but
        # its generation/token can no longer paint the next infobar.
        self._reset_picon_display()
        reset_reconnect = getattr(self, "_reset_live_reconnect", None)
        if callable(reset_reconnect):
            reset_reconnect()
        else:
            # Compatibility for reduced integrations that embed only this
            # channel-switch method. Full player screens always use the locked
            # centralized reset above.
            try:
                self._reconnect_timer.stop()
            except Exception:
                pass
            _cancel_worker_token(getattr(self, "_reconnect_link_token", None))
            self._reconnect_link_token = None
        with self._channel_link_lock:
            _cancel_worker_token(self._channel_link_token)
            self._channel_link_token = None
            self._channel_link_pending = False
            self._channel_link_ready = False
            self._channel_link_url = ""
            self._channel_link_generation = -1
            self._channel_link_index = -1
            self._channel_link_deadline = 0.0
            self._channel_key_accepted_at = 0.0
            self._channel_link_ready_at = 0.0
        if next_index == self.current_index:
            self._pending_channel_index = None
            try:
                self._channel_switch_timer.stop()
            except Exception:
                pass
            # The pending zap was cancelled back to the service that never
            # stopped. Re-arm its non-essential metadata jobs after the same
            # quiet startup interval instead of leaving them disabled.
            self._after_zap()
            return

        self._pending_channel_index = next_index
        self._generation += 1
        self._channel_key_accepted_at = now
        next_item = self._entries[next_index]
        self._info_dialog["channel_number"].setText(
            "{:02d}".format(next_index + 1)
        )
        self._set_info_channel_name(next_item.name)
        self._set_static_info_epg("epg_now", _("Changing channel..."))
        self._set_static_info_epg("epg_next", "")
        self._clear_player_progress()
        self.show_info()
        # Direct and short-lived cached links are safe to prepare locally. The
        # first dynamic target starts immediately, preserving single-key zap
        # latency; later keys in the same burst are coalesced until the target
        # debounce expires instead of queueing every discarded create_link.
        cached_ready = self._prepare_cached_channel_link(next_index, next_item)
        if not cached_ready and not had_pending_target:
            self._start_channel_link_resolution(next_index, next_item)
        self._channel_switch_timer.start(CHANNEL_SWITCH_DEBOUNCE_MS, True)

    def _prepare_cached_channel_link(self, next_index, next_item):
        resolver = getattr(self.client, "cached_playback_url", None)
        if not callable(resolver):
            return False
        try:
            url = str(resolver(next_item) or "").strip()
        except Exception:
            url = ""
        if not url:
            return False
        self._clear_channel_link_state(keep_timing=True)
        with self._channel_link_lock:
            self._channel_link_pending = True
            self._channel_link_ready = True
            self._channel_link_url = url
            self._channel_link_generation = self._generation
            self._channel_link_index = next_index
            self._channel_link_ready_at = time.monotonic()
        return True

    def _start_channel_link_resolution(self, next_index, next_item):
        asynchronous = callable(
            getattr(self.client, "refresh_playback_url", None)
        )
        if not asynchronous:
            return False
        try:
            client_timeout = int(self.client.timeout)
        except (AttributeError, TypeError, ValueError, OverflowError):
            client_timeout = CHANNEL_LINK_TIMEOUT_SECONDS
        link_timeout = max(2, min(CHANNEL_LINK_TIMEOUT_SECONDS, client_timeout))
        token = _WorkerToken(link_timeout)
        with self._channel_link_lock:
            if (
                self._channel_link_pending
                and self._channel_link_generation == self._generation
                and self._channel_link_index == next_index
            ):
                return True
            self._clear_channel_link_state(keep_timing=True)
            self._channel_link_pending = True
            self._channel_link_ready = False
            self._channel_link_url = ""
            self._channel_link_generation = self._generation
            self._channel_link_index = next_index
            self._channel_link_token = token
            self._channel_link_deadline = token.deadline
            generation = self._channel_link_generation
        worker = threading.Thread(
            target=self._resolve_channel_link,
            args=(
                generation,
                next_index,
                next_item,
                token,
            ),
        )
        worker.daemon = True
        worker.start()
        return True

    def _commit_channel_switch(self):
        if self._closed or self._pending_channel_index is None:
            return
        next_index = self._pending_channel_index
        next_item = self._entries[next_index]
        asynchronous = callable(
            getattr(self.client, "refresh_playback_url", None)
        )
        next_url = ""
        channel_link_ready_at = 0.0
        if asynchronous:
            with self._channel_link_lock:
                link_matches = bool(
                    self._channel_link_generation == self._generation
                    and self._channel_link_index == next_index
                )
                link_pending = bool(
                    link_matches and self._channel_link_pending
                )
                link_ready = bool(link_matches and self._channel_link_ready)
                link_deadline = (
                    self._channel_link_deadline if link_matches else 0.0
                )
            if not link_pending:
                self._start_channel_link_resolution(next_index, next_item)
                self._channel_switch_timer.start(CHANNEL_LINK_POLL_MS, True)
                return
            if not link_ready and time.monotonic() < link_deadline:
                self._channel_switch_timer.start(CHANNEL_LINK_POLL_MS, True)
                return
            with self._channel_link_lock:
                link_matches = bool(
                    self._channel_link_generation == self._generation
                    and self._channel_link_index == next_index
                )
                if link_matches and not self._channel_link_ready:
                    self._channel_link_ready = True
                    self._channel_link_url = ""
                if not link_matches:
                    self._clear_channel_link_state(keep_timing=True)
                else:
                    next_url = self._channel_link_url
                    channel_link_ready_at = self._channel_link_ready_at
            if not link_matches:
                self._start_channel_link_resolution(next_index, next_item)
                self._channel_switch_timer.start(CHANNEL_LINK_POLL_MS, True)
                return
        self._pending_channel_index = None
        try:
            if asynchronous:
                if not next_url:
                    raise ContentError("Stream link unavailable")
            else:
                next_url = self.client.playback_url(next_item)
            next_reference = build_extplayer_reference(
                next_url,
                next_item.name,
                getattr(next_item, "content_type", "live"),
                service_type=(
                    self._active_live_service_type
                    if getattr(next_item, "content_type", "") == "live"
                    else None
                ),
            )
        except Exception:
            self._clear_channel_link_state()
            self._after_zap()
            self._set_static_info_epg(
                "epg_now",
                _("Could not open the channel.")
            )
            self._set_static_info_epg(
                "epg_next",
                _("Keeping the current stream")
            )
            self.show_info()
            return

        navigation = getattr(self.session, "nav", None)
        if navigation is None:
            self._clear_channel_link_state()
            self._after_zap()
            return
        self._save_resume_position()
        subtitle_controller = getattr(self, "_subtitle_controller", None)
        previous_subtitle = (
            subtitle_controller.snapshot_state()
            if subtitle_controller is not None
            else None
        )
        if subtitle_controller is not None:
            subtitle_controller.reset_for_service_change(resume_search=True)
        try:
            # Enigma2 replaces the current service on the same navigation slot.
            # Avoiding an explicit stop keeps the decoder/network pipeline warm
            # and removes the full teardown delay from ordinary channel zaps.
            _play_service(navigation, next_reference)
        except Exception as error:
            if previous_subtitle is not None and subtitle_controller is not None:
                subtitle_controller.restore_state(previous_subtitle)
            _debug(
                "live soft zap failed id={} error={}".format(
                    getattr(next_item, "stream_id", ""),
                    error.__class__.__name__,
                )
            )
            self._clear_channel_link_state()
            self._after_zap()
            self._set_static_info_epg(
                "epg_now",
                _("Could not open the channel.")
            )
            self._set_static_info_epg(
                "epg_next",
                _("Keeping the current stream")
            )
            self.show_info()
            return
        key_to_link_ms = (
            int(round((channel_link_ready_at - self._channel_key_accepted_at) * 1000))
            if channel_link_ready_at > 0 and self._channel_key_accepted_at > 0
            else 0
        )
        key_to_play_ms = (
            int(round((time.monotonic() - self._channel_key_accepted_at) * 1000))
            if self._channel_key_accepted_at > 0
            else 0
        )
        self._clear_channel_link_state()
        self.current_index = next_index
        self.current_item = next_item
        self.reference = next_reference
        if getattr(next_item, "content_type", "") == "live":
            self._active_live_service_type = reference_service_type(
                next_reference,
                4097,
            )
        _debug(
            "playback channel changed content={} engine={} reason=user "
            "key_to_link_ms={} key_to_play_ms={}".format(
                getattr(next_item, "content_type", "unknown"),
                reference_service_type(next_reference, 4097),
                key_to_link_ms,
                key_to_play_ms,
            )
        )
        self._resume_key = resume_key(self.client, next_item)
        self._resume_start_position = 0
        self._resume_saved = False
        arm_startup = getattr(self, "_arm_live_startup", None)
        if callable(arm_startup):
            arm_startup()
        self._after_zap(refresh_epg=True)

    def _resolve_channel_link(self, generation, index, item, token=None):
        # CH+/CH- is a new channel selection, not a refresh of the current
        # channel.  Use the ordinary playback resolver so only 0 enters the
        # full Stalker recovery/session path.
        resolver = getattr(self.client, "playback_url", None)
        if not callable(resolver):
            resolver = getattr(self.client, "refresh_playback_url", None)
        try:
            url = str(
                _bounded_client_call(
                    self.client,
                    token,
                    lambda: resolver(item) if callable(resolver) else "",
                )
                or ""
            ).strip()
        except Exception:
            url = ""
        with self._channel_link_lock:
            if (
                self._closed
                or (token is not None and not token.active())
                or self._generation != generation
                or self._pending_channel_index != index
                or self._channel_link_generation != generation
                or self._channel_link_index != index
            ):
                return
            self._channel_link_url = url
            self._channel_link_ready = True
            self._channel_link_ready_at = time.monotonic()

    def _clear_channel_link_state(self, keep_timing=False):
        with self._channel_link_lock:
            _cancel_worker_token(self._channel_link_token)
            self._channel_link_token = None
            self._channel_link_pending = False
            self._channel_link_ready = False
            self._channel_link_url = ""
            self._channel_link_generation = -1
            self._channel_link_index = -1
            self._channel_link_deadline = 0.0
            if not keep_timing:
                self._channel_key_accepted_at = 0.0
                self._channel_link_ready_at = 0.0

    def show_info(self):
        if (
            getattr(self, "_subtitle_menu_open", False)
            or getattr(self, "_subtitle_message_tokens", None)
        ):
            return
        if self._seeking or self._paused:
            self._show_seek_overlay(
                N_("PAUSED") if self._paused else N_("SEEK MODE"),
                N_("LEFT / RIGHT to select time   •   OK to continue"),
            )
            return
        if self._is_live_item():
            # These are local, event-like reads performed only when the user
            # opens the banner (channel start/zap also opens it once). While a
            # CH target is pending, keep the explicit "Changing channel" text;
            # current_item still belongs to the service being replaced.
            self._clock_tick()
            if not self._live_transition_in_progress():
                self._render_live_snapshot_epg(now=time.time())
                if not getattr(self, "_defer_live_metadata_once", False):
                    self._refresh_live_metadata_once()
        self._info_visible = True
        self._info_dialog.show()
        try:
            if self._picon_visible:
                self._info_dialog["picon"].show()
            else:
                self._info_dialog["picon"].hide()
            self._info_dialog["picon_placeholder"].hide()
        except Exception:
            pass
        try:
            self._hide_timer.stop()
        except Exception:
            pass
        self._hide_timer.start(INFO_TIMEOUT_MS, True)

    def hide_info(self):
        if self._closed or self._seeking or self._paused:
            return
        try:
            self._hide_timer.stop()
        except Exception:
            pass
        self._info_visible = False
        dialog = getattr(self, "_info_dialog", None)
        if dialog is not None:
            try:
                dialog.hide()
            except Exception:
                pass

    def _show_subtitle_message(self, message):
        """Show a friendly, non-fatal result for an unavailable subtitle."""
        if self._closed:
            return
        text = _(message)
        self._subtitle_message_serial += 1
        token = (self._generation, self._subtitle_message_serial)
        self._subtitle_message_tokens.add(token)

        def finished(*unused_result):
            if token not in self._subtitle_message_tokens:
                return
            self._subtitle_message_tokens.discard(token)
            self._restore_subtitle_overlay(token[0])

        try:
            self._hide_subtitle_overlay()
            self.session.openWithCallback(
                finished,
                MessageBox,
                text,
                MessageBox.TYPE_INFO,
                timeout=INFO_TIMEOUT_MS // 1000,
            )
            return
        except Exception:
            self._subtitle_message_tokens.discard(token)
        try:
            self._set_static_info_epg("epg_now", text)
            self._set_static_info_epg("epg_next", "")
            self.show_info()
        except Exception:
            pass

    def _subtitle_changed(self, selected_track):
        """Keep the player banner from covering newly enabled subtitles."""
        if selected_track is not None:
            self.hide_info()

    def _subtitle_menu_opened(self):
        """Keep the high-z player dialog below the whole subtitle workflow."""
        if self._closed:
            return
        self._subtitle_menu_open = True
        self._subtitle_menu_generation = self._generation
        self._hide_subtitle_overlay()

    def _hide_subtitle_overlay(self):
        """Hide both banner and pause widgets without resuming the decoder."""
        try:
            self._hide_timer.stop()
        except Exception:
            pass
        self._hide_seek_overlay()
        self._info_visible = False
        self._info_dialog.hide()

    def _subtitle_menu_closed(self):
        """Restore a pause/seek panel without changing decoder playback state."""
        was_open = getattr(self, "_subtitle_menu_open", False)
        generation = getattr(self, "_subtitle_menu_generation", None)
        self._subtitle_menu_open = False
        self._subtitle_menu_generation = None
        if was_open:
            self._restore_subtitle_overlay(generation)

    def _restore_subtitle_overlay(self, generation):
        if (
            self._closed
            or generation != self._generation
            or self._subtitle_menu_open
            or self._subtitle_message_tokens
        ):
            return
        if self._seeking:
            self.show_info()
        elif self._paused:
            self._show_seek_overlay(
                N_("PAUSED"),
                N_("PLAY / PAUSE to continue   •   STOP to close the video"),
            )

    def _subtitle_search_metadata(self):
        item = self.current_item
        if getattr(item, "content_type", "") == "series":
            parent = getattr(item, "favorite_parent", None)
            if parent is None:
                parent = getattr(self, "_favorite_parent", None)
            return {
                "title": (
                    getattr(parent, "name", "")
                    or getattr(item, "name", self.title)
                ),
                "year": (
                    getattr(parent, "year", "")
                    or getattr(item, "year", "")
                ),
                "season": getattr(item, "season", ""),
                "episode": getattr(item, "episode", ""),
                "content_type": "series",
                "episode_title": getattr(item, "name", ""),
            }
        return (
            getattr(item, "name", self.title),
            getattr(item, "year", ""),
        )

    def _pause_for_subtitle_search(self):
        """Pause VOD without leaving GT's high-z pause panel over search."""
        was_paused = self._paused
        if not was_paused:
            self.pause_playback()
        paused_by_search = not was_paused and self._paused
        self._hide_seek_overlay()
        self._info_visible = False
        try:
            self._info_dialog.hide()
        except Exception:
            pass
        return paused_by_search

    def _resume_after_subtitle_search(self, paused_by_search=True):
        if paused_by_search and self._paused:
            self.resume_playback()
            subtitle_controller = getattr(self, "_subtitle_controller", None)
            if (
                subtitle_controller is not None
                and subtitle_controller.external_loaded
            ):
                self.hide_info()
        elif self._paused:
            self._show_seek_overlay(
                N_("PAUSED"),
                N_("PLAY / PAUSE to continue   •   STOP to close the video"),
            )

    def open_subtitle_selection(self):
        """Open the safe VOD subtitle menu and consume the remote action."""
        if getattr(self, "_closed", False):
            return True
        if getattr(self.current_item, "content_type", "") not in (
            "movie",
            "series",
            "catchup",
        ):
            self.show_info()
            return True
        self.hide_info()
        subtitle_controller = getattr(self, "_subtitle_controller", None)
        if subtitle_controller is not None:
            subtitle_controller.open_selection()
        # Consume the action even when the decoder exposes no track; otherwise
        # Enigma2 may fall through to a yellow-key action in another context.
        return True

    @staticmethod
    def _format_play_time(seconds):
        try:
            seconds = max(0, int(seconds))
        except (TypeError, ValueError):
            seconds = 0
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return "{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)

    def _current_service(self):
        navigation = getattr(self.session, "nav", None)
        if navigation is None:
            return None
        getter = getattr(navigation, "getCurrentService", None)
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def _seek_interface(self):
        service = self._current_service()
        if service is None:
            return None
        getter = getattr(service, "seek", None)
        if getter is None:
            return None
        try:
            seeker = getter()
            seekable = seeker.isCurrentlySeekable()
            if isinstance(seekable, tuple):
                seekable = seekable[-1]
            return seeker if seekable else None
        except Exception:
            return None

    @staticmethod
    def _seek_seconds(result):
        try:
            error, ticks = result
            if int(error) != 0:
                return -1
            return max(0, int(ticks) // 90000)
        except (TypeError, ValueError, OverflowError):
            return -1

    def _seek_position(self, seeker=None):
        seeker = seeker or self._seek_interface()
        if seeker is None:
            return -1, -1
        try:
            position = self._seek_seconds(seeker.getPlayPosition())
            length = self._seek_seconds(seeker.getLength())
        except Exception:
            return -1, -1
        return position, length

    def _show_seek_overlay(self, state, hint):
        if (
            getattr(self, "_subtitle_menu_open", False)
            or getattr(self, "_subtitle_message_tokens", None)
        ):
            return
        seeker = self._seek_interface()
        position, length = self._seek_position(seeker)
        if self._seeking:
            position = self._seek_target
            length = self._seek_length
        safe_length = max(0, length)
        ratio = (
            min(1.0, max(0.0, float(position) / float(safe_length)))
            if safe_length > 0 and position >= 0
            else 0.0
        )
        self._info_dialog["seek_state"].setText(_(state))
        self._info_dialog["seek_times"].setText(
            "{}   /   {}".format(
                self._format_play_time(position),
                self._format_play_time(safe_length),
            )
        )
        self._info_dialog["seek_hint"].setText(_(hint))
        try:
            self._info_dialog["seek_progress_fill"].instance.resize(
                eSize(
                    max(1, int(round(self._seek_width * ratio))),
                    self._seek_height,
                )
            )
        except Exception:
            pass
        for name in (
            "seek_panel",
            "seek_state",
            "seek_progress_bg",
            "seek_progress_fill",
            "seek_times",
            "seek_hint",
        ):
            self._info_dialog[name].show()
        self._info_dialog.show()
        self._info_visible = True

    def _hide_seek_overlay(self):
        for name in (
            "seek_panel",
            "seek_state",
            "seek_progress_bg",
            "seek_progress_fill",
            "seek_times",
            "seek_hint",
        ):
            self._info_dialog[name].hide()

    def _seek_unavailable(self):
        self._cancel_seek_verification()
        self._seeking = False
        self._show_seek_overlay(
            N_("THIS STREAM CANNOT BE SEEKED"),
            N_("The server or playback format does not support seeking"),
        )
        self._hide_timer.start(INFO_TIMEOUT_MS, True)

    def _begin_or_adjust_seek(self, delta_seconds):
        if getattr(self.current_item, "content_type", "") == "live":
            self._seek_unavailable()
            return
        if not self._seeking:
            self._cancel_seek_verification()
        seeker = self._seek_interface()
        position, length = self._seek_position(seeker)
        if seeker is None or position < 0 or length <= 0:
            self._seek_unavailable()
            return
        if not self._seeking:
            self._seeking = True
            self._seek_target = position
            self._seek_length = length
            try:
                self._hide_timer.stop()
            except Exception:
                pass
        self._seek_target = min(
            max(0, self._seek_target + int(delta_seconds)),
            self._seek_length,
        )
        direction = (
            N_("SEEK FORWARD")
            if delta_seconds >= 0
            else N_("SEEK BACKWARD")
        )
        self._show_seek_overlay(
            direction,
            N_("LEFT / RIGHT to select time   •   OK to continue   •   EXIT to cancel"),
        )

    @staticmethod
    def _seek_call_failed(result):
        """Enigma2 seek calls use negative integers for hard failures."""
        if result is None or isinstance(result, bool):
            return False
        try:
            return int(result) < 0
        except (TypeError, ValueError, OverflowError):
            return False

    @staticmethod
    def _seek_reached(position, origin, target):
        if position < 0:
            return False
        distance = int(target) - int(origin)
        if distance == 0 or abs(int(position) - int(target)) <= 5:
            return True
        # Do not confuse ordinary one-second playback progress with a working
        # seek. A keyframe landing may be a few seconds off the exact target,
        # but it still has to cover at least half of the requested distance.
        required = max(3, int(abs(distance) / 2))
        if distance > 0:
            return int(position) >= int(origin) + required
        return int(position) <= int(origin) - required

    def _cancel_seek_verification(self):
        self._pending_seek_verification = None
        timer = getattr(self, "_seek_verify_timer", None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass

    def _schedule_seek_verification(self, origin, target, method):
        self._pending_seek_verification = {
            "origin": int(origin),
            "target": int(target),
            "method": str(method),
            "checks": 0,
            "stream_id": str(
                getattr(self.current_item, "stream_id", "") or ""
            ),
        }
        self._seek_verify_timer.start(SEEK_VERIFY_DELAY_MS, True)

    def _request_seek(self, seeker, origin, target):
        """Seek absolutely first, then verify and fall back if it was ignored."""
        origin = int(origin)
        target = int(target)
        absolute = getattr(seeker, "seekTo", None)
        if absolute is not None:
            try:
                result = absolute(target * 90000)
                if not self._seek_call_failed(result):
                    self._schedule_seek_verification(origin, target, "absolute")
                    return True
            except Exception as error:
                _debug(
                    "absolute seek request failed error={}".format(
                        error.__class__.__name__
                    )
                )
        relative = getattr(seeker, "seekRelative", None)
        delta = target - origin
        if relative is None or delta == 0:
            return delta == 0
        try:
            result = relative(-1 if delta < 0 else 1, abs(delta) * 90000)
            if self._seek_call_failed(result):
                return False
        except Exception as error:
            _debug(
                "relative seek request failed error={}".format(
                    error.__class__.__name__
                )
            )
            return False
        self._schedule_seek_verification(origin, target, "relative")
        return True

    def _verify_seek_result(self):
        state = self._pending_seek_verification
        if state is None or self._closed:
            return
        current_stream_id = str(
            getattr(self.current_item, "stream_id", "") or ""
        )
        if current_stream_id != state["stream_id"]:
            self._cancel_seek_verification()
            return
        seeker = self._seek_interface()
        position = self._seek_position(seeker)[0]
        if self._seek_reached(position, state["origin"], state["target"]):
            self._cancel_seek_verification()
            self._playback_tick()
            subtitle_controller = getattr(self, "_subtitle_controller", None)
            if subtitle_controller is not None:
                subtitle_controller.after_seek()
            return
        state["checks"] += 1
        if state["checks"] < SEEK_VERIFY_MAX_CHECKS:
            self._seek_verify_timer.start(SEEK_VERIFY_DELAY_MS, True)
            return
        if state["method"] == "absolute" and seeker is not None:
            relative = getattr(seeker, "seekRelative", None)
            fallback_origin = position if position >= 0 else state["origin"]
            delta = state["target"] - fallback_origin
            if relative is not None and delta:
                try:
                    result = relative(
                        -1 if delta < 0 else 1,
                        abs(delta) * 90000,
                    )
                    if not self._seek_call_failed(result):
                        state["origin"] = fallback_origin
                        state["method"] = "relative"
                        state["checks"] = 0
                        self._seek_verify_timer.start(
                            SEEK_VERIFY_DELAY_MS,
                            True,
                        )
                        return
                except Exception as error:
                    _debug(
                        "verified relative fallback failed error={}".format(
                            error.__class__.__name__
                        )
                    )
        self._seek_unavailable()

    def _seek_immediately(self, delta_seconds):
        if getattr(self.current_item, "content_type", "") == "live":
            self._seek_unavailable()
            return
        seeker = self._seek_interface()
        position, length = self._seek_position(seeker)
        if seeker is None or position < 0 or length <= 0:
            self._seek_unavailable()
            return
        delta_seconds = int(delta_seconds)
        pending = self._pending_seek_verification
        base_position = (
            int(pending["target"])
            if pending is not None
            and str(pending.get("stream_id", ""))
            == str(getattr(self.current_item, "stream_id", "") or "")
            else position
        )
        target = min(max(0, base_position + delta_seconds), length)
        actual_delta = target - position
        if actual_delta == 0:
            self._cancel_seek_verification()
            self.show_info()
            return
        self._cancel_seek_verification()
        if not self._request_seek(seeker, position, target):
            self._seek_unavailable()
            return
        self.show_info()

    def seek_forward(self):
        if self._seeking:
            self._begin_or_adjust_seek(30)
        else:
            self._seek_immediately(60)

    def seek_backward(self):
        if self._seeking:
            self._begin_or_adjust_seek(-30)
        else:
            self._seek_immediately(-60)

    def begin_seek_mode(self):
        self._begin_or_adjust_seek(0)

    def seek_right_or_info(self):
        if self._seeking:
            self._begin_or_adjust_seek(30)
        else:
            self.show_info()

    def seek_left_or_info(self):
        if self._seeking:
            self._begin_or_adjust_seek(-30)
        else:
            self.show_info()

    def confirm_seek(self):
        if not self._seeking:
            return False
        seeker = self._seek_interface()
        if seeker is None:
            self._seek_unavailable()
            return True
        position, length = self._seek_position(seeker)
        if position < 0 or length <= 0:
            self._seek_unavailable()
            return True
        target = min(max(0, int(self._seek_target)), int(length))
        self._cancel_seek_verification()
        if not self._request_seek(seeker, position, target):
            self._seek_unavailable()
            return True
        self._seeking = False
        self._hide_seek_overlay()
        if self._paused:
            self.resume_playback()
        else:
            self.show_info()
        return True

    def cancel_seek(self):
        if not self._seeking:
            return False
        self._seeking = False
        self._hide_seek_overlay()
        self.show_info()
        return True

    def toggle_info(self):
        """Toggle only the normal fullscreen banner on an explicit OK key."""
        if getattr(self, "_closed", False):
            return
        if getattr(self, "_info_visible", False):
            self.hide_info()
        else:
            self.show_info()

    def ok_or_confirm_seek(self):
        if not self.confirm_seek():
            self.toggle_info()

    def cancel_or_exit(self):
        if not self.cancel_seek():
            self._exit_action()

    def _pause_interface(self):
        service = self._current_service()
        getter = getattr(service, "pause", None) if service is not None else None
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def pause_playback(self):
        if getattr(self.current_item, "content_type", "") == "live":
            # This player does not create an Enigma2 timeshift buffer for IPTV.
            # ServiceApp may still expose pause(), but using it can leave a
            # linear stream permanently stalled after its network buffer fills.
            self.show_info()
            return
        if self._paused:
            return
        pauseable = self._pause_interface()
        if pauseable is None:
            self._show_seek_overlay(
                N_("PAUSE IS NOT AVAILABLE"),
                N_("This server or playback format does not support pausing"),
            )
            self._hide_timer.start(INFO_TIMEOUT_MS, True)
            return
        try:
            pauseable.pause()
        except Exception:
            return
        self._paused = True
        subtitle_controller = getattr(self, "_subtitle_controller", None)
        if subtitle_controller is not None:
            subtitle_controller.pause()
        try:
            self._hide_timer.stop()
        except Exception:
            pass
        self._show_seek_overlay(
            N_("PAUSED"),
            N_("PLAY / PAUSE to continue   •   STOP to close the video"),
        )

    def resume_playback(self):
        if not self._paused:
            return
        pauseable = self._pause_interface()
        if pauseable is None:
            return
        try:
            pauseable.unpause()
        except Exception:
            return
        self._paused = False
        subtitle_controller = getattr(self, "_subtitle_controller", None)
        if subtitle_controller is not None:
            subtitle_controller.resume()
        self._hide_seek_overlay()
        self.show_info()
        if (
            subtitle_controller is not None
            and subtitle_controller.external_loaded
        ):
            self.hide_info()

    def toggle_pause(self):
        if self._paused:
            self.resume_playback()
        else:
            self.pause_playback()

    def _clock_tick(self, now=None):
        if self._closed:
            return
        current = now or datetime.datetime.now()
        self._info_dialog["clock"].setText(current.strftime("%H:%M"))
        self._info_dialog["date"].setText(localized_date_text(current))
        if not self._live_network_silent():
            self._clock_timer.start(30000, True)

    def _render_player_weather_waiting(self, settings=None):
        if settings is None:
            try:
                settings = self._weather_settings_loader()
            except Exception:
                settings = None
        city = getattr(settings, "city", DEFAULT_CITY)
        ellipsize_dynamic_text(
            self._info_dialog["weather_city"],
            localized_upper(city),
            fallback_chars=15,
        )
        self._info_dialog["weather_icon"].hide()
        if settings is not None and not getattr(settings, "enabled", True):
            self._info_dialog["weather_temp"].setText("--")
            self._info_dialog["weather_desc"].setText(_("Weather is disabled"))
        else:
            self._info_dialog["weather_temp"].setText("--°")
            self._info_dialog["weather_desc"].setText(_("Loading weather data"))

    def _render_player_weather_unavailable(self):
        """Render an honest live placeholder without implying a future fetch."""
        try:
            settings = self._weather_settings_loader()
        except Exception:
            settings = None
        city = getattr(settings, "city", DEFAULT_CITY)
        ellipsize_dynamic_text(
            self._info_dialog["weather_city"],
            localized_upper(city),
            fallback_chars=15,
        )
        self._info_dialog["weather_icon"].hide()
        self._info_dialog["weather_temp"].setText("--")
        if settings is not None and not getattr(settings, "enabled", True):
            description = _("Weather is disabled")
        else:
            description = _("Weather information is not ready")
        self._info_dialog["weather_desc"].setText(description)

    def _start_player_weather(self):
        if self._is_live_item():
            # Live weather is an immutable list-screen snapshot. Never fall
            # through to WeatherService.get() while decoder 0 is active.
            snapshot = self._live_snapshot_for_current_item()
            forecast = snapshot.get(
                "weather_forecast",
                snapshot.get("weather"),
            )
            if isinstance(forecast, dict):
                try:
                    forecast = WeatherService._from_dict(forecast)
                except Exception:
                    forecast = None
            if forecast is not None:
                self._weather_forecast = forecast
                self._render_player_weather(forecast)
            else:
                self._render_player_weather_unavailable()
            return
        if (
            self._closed
            or self._weather_loading
            or self.client is None
        ):
            return
        if self._weather_forecast is not None:
            self._render_player_weather(self._weather_forecast)
            return
        if self._weather_started:
            return
        try:
            settings = self._weather_settings_loader()
        except Exception:
            return
        self._weather_started = True
        self._render_player_weather_waiting(settings)
        if not getattr(settings, "enabled", True):
            return
        self._weather_settings = settings
        self._weather_ready = False
        self._weather_loading = True
        _cancel_worker_token(self._weather_token)
        token = _WorkerToken(WORKER_WEATHER_TIMEOUT_SECONDS)
        self._weather_token = token
        worker = threading.Thread(
            target=lambda: self._run_player_weather(token),
        )
        worker.daemon = True
        worker.start()
        self._weather_timer.start(150, True)

    def _run_player_weather(self, token=None):
        if self._is_live_item():
            return
        try:
            if token is not None:
                token.check()
            forecast = self._weather_service.get(
                self._weather_settings,
                force=False,
                cancel_event=(
                    token.cancel_event if token is not None else None
                ),
                deadline=(token.deadline if token is not None else None),
            )
            if token is not None:
                token.check()
            self._weather_forecast = forecast
        except WeatherError as error:
            self._weather_error = str(error)
        except Exception:
            self._weather_error = N_("Could not load the weather forecast")
        if (
            not self._closed
            and (token is None or token.active())
        ):
            self._weather_ready = True

    def _poll_player_weather(self):
        if self._closed:
            return
        if self._is_live_item():
            # Never re-arm a queued weather poll in fullscreen live mode.
            self._weather_loading = False
            if self._weather_forecast is not None:
                self._render_player_weather(self._weather_forecast)
            else:
                self._render_player_weather_unavailable()
            return
        if not self._weather_ready:
            if self._weather_token is not None and self._weather_token.expired():
                self._weather_token.cancel()
                self._weather_loading = False
                self._weather_token = None
                self._info_dialog["weather_temp"].setText("--")
                self._info_dialog["weather_desc"].setText(
                    _("Weather information unavailable.")
                )
                return
            self._weather_timer.start(150, True)
            return
        self._weather_loading = False
        self._weather_token = None
        if self._weather_forecast is not None:
            self._render_player_weather(self._weather_forecast)
            return
        self._info_dialog["weather_temp"].setText("--")
        ellipsize_dynamic_text(
            self._info_dialog["weather_desc"],
            _(self._weather_error or N_("Weather information unavailable.")),
            fallback_chars=18,
        )

    def _render_player_weather(self, forecast):
        ellipsize_dynamic_text(
            self._info_dialog["weather_city"],
            localized_upper(forecast.city),
            fallback_chars=15,
        )
        self._info_dialog["weather_temp"].setText(
            "{:.0f}°{}".format(forecast.current_temperature, forecast.unit)
        )
        self._info_dialog["weather_desc"].setText(
            weather_description(forecast.current_code)
        )
        path = os.path.join(
            WEATHER_ICON_DIR,
            "{}.png".format(weather_icon_name(forecast.current_code)),
        )
        try:
            widget = self._info_dialog["weather_icon"]
            widget.instance.setPixmapFromFile(path)
            widget.show()
        except Exception:
            self._info_dialog["weather_icon"].hide()

    def _clear_player_progress(self):
        self._info_dialog["player_progress_text"].setText("")
        self._info_dialog["player_progress_fill"].hide()

    def _set_playback_progress(self, position, length):
        if position < 0 or length <= 0:
            self._clear_player_progress()
            return
        position = min(max(0, int(position)), int(length))
        length = max(1, int(length))
        remaining = max(0, length - position)
        ratio = min(1.0, max(0.0, float(position) / float(length)))
        self._info_dialog["player_progress_text"].setText(
            "-{}".format(self._format_play_time(remaining))
        )
        fill = self._info_dialog["player_progress_fill"]
        try:
            fill.instance.resize(
                eSize(
                    max(1, int(round(self._progress_width * ratio))),
                    self._progress_height,
                )
            )
        except Exception:
            pass
        fill.show()

    def _playback_tick(self):
        """Refresh elapsed/remaining VOD time from ExtEplayer3 once a second."""
        if self._closed:
            return
        if getattr(self.current_item, "content_type", "") == "live":
            return
        position, length = self._seek_position()
        if position >= 0 and length > 0:
            position = min(position, length)
            self._set_static_info_epg(
                "epg_now",
                self._format_play_time(position)
            )
            self._set_static_info_epg(
                "epg_next",
                self._format_play_time(max(0, length - position))
            )
            self._set_playback_progress(position, length)
        self._refresh_front_panel()
        # Keep retrying while ExtEplayer3 is opening the stream; getLength()
        # can legitimately be unavailable during the first few callbacks.
        self._playback_timer.start(1000, True)

    def _resume_retry(self, reason, observed_length=-1):
        """Retry a bounded resume probe, then keep playback at the beginning."""
        self._resume_attempts += 1
        if self._resume_attempts < RESUME_MAX_ATTEMPTS:
            self._resume_timer.start(RESUME_RETRY_DELAY_MS, True)
            return True
        _debug(
            "resume skipped engine={} target={} observed_length={} reason={}".format(
                reference_service_type(self.reference, 5002),
                self._resume_start_position,
                observed_length,
                reason,
            )
        )
        self._resume_start_position = 0
        self._reset_resume_length_probe()
        return False

    def _reset_resume_length_probe(self):
        self._resume_last_length = -1
        self._resume_stable_length_samples = 0

    def _resume_length_ready(self, length):
        """Reject provisional VOD durations before applying a saved position."""
        length = int(length)
        if not should_save_resume(self._resume_start_position, length):
            self._reset_resume_length_probe()
            return False

        if (
            self._resume_last_length >= 0
            and abs(length - self._resume_last_length)
            <= RESUME_LENGTH_STABILITY_TOLERANCE_SECONDS
        ):
            self._resume_stable_length_samples += 1
        else:
            self._resume_last_length = length
            self._resume_stable_length_samples = 1

        required_samples = (
            RESUME_4097_STABLE_SAMPLES
            if reference_service_type(self.reference, 5002) == 4097
            else 1
        )
        return self._resume_stable_length_samples >= required_samples

    def _resume_tick(self):
        """Seek only after the decoder exposes a plausible, stable VOD length."""
        if self._closed or self._resume_start_position <= 0:
            return
        seeker = self._seek_interface()
        if seeker is None:
            self._reset_resume_length_probe()
            self._resume_retry("seek-unavailable")
            return
        position, length = self._seek_position(seeker)
        if position < 0 or length <= 0:
            self._reset_resume_length_probe()
            self._resume_retry("length-unavailable", length)
            return
        if not self._resume_length_ready(length):
            self._resume_retry("length-provisional", length)
            return
        target = int(self._resume_start_position)
        self._cancel_seek_verification()
        if not self._request_seek(seeker, position, target):
            self._resume_retry("seek-rejected", length)
            return
        _debug(
            "resume applied engine={} target={} length={} probes={}".format(
                reference_service_type(self.reference, 5002),
                target,
                length,
                self._resume_attempts + 1,
            )
        )
        self._resume_start_position = 0
        self._reset_resume_length_probe()
        self._resume_saved = False
        self._playback_tick()
        self.show_info()

    def _save_resume_position(self):
        if self._resume_saved or self._resume_store is None or not self._resume_key:
            return
        if getattr(self.current_item, "content_type", "") not in (
            "movie", "series"
        ):
            return
        position, length = self._seek_position()
        if position >= 0 and length > 0:
            self._resume_store.save(
                self._resume_key,
                position,
                length,
                item=self.current_item,
                account_scope=resume_account_scope(self.client),
            )
            self._resume_saved = True

    def _set_player_progress(self, event, now=None):
        start = int(getattr(event, "start_timestamp", 0) or 0)
        end = int(getattr(event, "end_timestamp", 0) or 0)
        if not start or not end or end <= start:
            self._clear_player_progress()
            return
        current = int(time.time() if now is None else now)
        ratio = min(1.0, max(0.0, float(current - start) / float(end - start)))
        remaining = max(0, end - current)
        hours, remainder = divmod(remaining, 3600)
        minutes, seconds = divmod(remainder, 60)
        self._info_dialog["player_progress_text"].setText(
            "+{:02d}:{:02d}:{:02d}".format(hours, minutes, seconds)
        )
        fill = self._info_dialog["player_progress_fill"]
        try:
            fill.instance.resize(
                eSize(
                    max(1, int(round(self._progress_width * ratio))),
                    self._progress_height,
                )
            )
        except Exception:
            pass
        fill.show()

    @staticmethod
    def _service_info_value(info, name):
        key = getattr(iServiceInformation, name, None)
        if key is None or info is None:
            return -1
        try:
            return int(info.getInfo(key))
        except Exception:
            return -1

    def _refresh_live_metadata_once(self):
        """Refresh one bounded decoder-local metadata group for the infobar."""
        if (
            self._closed
            or not self._is_live_item()
            or self._live_metadata_complete_generation == self._generation
        ):
            return False
        if self._live_metadata_settle_pending:
            # Preserve the one reliable post-zap sample if the viewer opens
            # the infobar during the decoder's 550-ms settling window.
            return False
        if self._metadata_attempts >= LIVE_METADATA_MAX_SAMPLES:
            # Reopening INFO later is a new user event. Permit one fresh,
            # still-bounded local sample group because some decoders publish
            # audio tracks noticeably later than video geometry.
            self._metadata_attempts = 0
        try:
            self._metadata_timer.stop()
        except Exception:
            pass
        self._metadata_tick()
        return True

    def _metadata_tick(self):
        if self._closed:
            return
        live_local_once = self._is_live_item()
        if live_local_once:
            self._live_metadata_settle_pending = False
        if self._live_network_silent() and not live_local_once:
            try:
                self._metadata_timer.stop()
            except Exception:
                pass
            return
        if self._live_transition_in_progress():
            return
        if live_local_once:
            if self._live_metadata_complete_generation == self._generation:
                return
            if self._live_metadata_generation != self._generation:
                self._live_metadata_generation = self._generation
                self._metadata_attempts = 0
                self._live_metadata_ready = {}
        self._metadata_attempts += 1
        navigation = getattr(self.session, "nav", None)
        service = None
        if navigation is not None:
            getter = getattr(navigation, "getCurrentService", None)
            if getter is not None:
                try:
                    service = getter()
                except Exception:
                    service = None
        info = None
        if service is not None:
            try:
                info = service.info()
            except Exception:
                info = None

        width = self._service_info_value(info, "sVideoWidth")
        height = self._service_info_value(info, "sVideoHeight")
        frame_rate = self._service_info_value(info, "sFrameRate")
        video_type = self._service_info_value(info, "sVideoType")
        if width <= 0:
            width = _read_proc_number("/proc/stb/vmpeg/0/xres", 16)
        if height <= 0:
            height = _read_proc_number("/proc/stb/vmpeg/0/yres", 16)
        if frame_rate <= 0:
            frame_rate = _read_proc_number("/proc/stb/vmpeg/0/framerate", 10)

        if width > 0 and height > 0:
            quality = "SD"
            if width >= 3800 or height >= 2100:
                quality = "UHD"
            elif width >= 1900 or height >= 1050:
                quality = "FHD"
            elif width >= 1200 or height >= 700:
                quality = "HD"
            self._info_dialog["resolution"].setText(
                "{}x{} {}".format(width, height, quality)
            )
            if live_local_once:
                self._live_metadata_ready["resolution"] = True
        elif not live_local_once or not self._live_metadata_ready.get("resolution"):
            self._info_dialog["resolution"].setText(_("Resolution: --"))

        if frame_rate > 0:
            fps = int((frame_rate + 500) / 1000) if frame_rate > 1000 else frame_rate
            self._info_dialog["fps"].setText("{} FPS".format(fps))
            if live_local_once:
                self._live_metadata_ready["fps"] = True
        elif not live_local_once or not self._live_metadata_ready.get("fps"):
            self._info_dialog["fps"].setText("-- FPS")
        video_codec = VIDEO_CODECS.get(video_type, "")
        if not video_codec:
            video_codec = _normalise_video_codec(
                _read_proc_text("/proc/stb/vmpeg/0/codec")
            )
        audio_codec = self._audio_description(service)
        if video_codec != "--":
            self._info_dialog["video_codec"].setText(
                _("VIDEO: {}").format(video_codec)
            )
            if live_local_once:
                self._live_metadata_ready["video"] = True
        elif not live_local_once or not self._live_metadata_ready.get("video"):
            self._info_dialog["video_codec"].setText(_("VIDEO: --"))
        if audio_codec != "--":
            self._info_dialog["audio_codec"].setText(
                _("AUDIO: {}").format(audio_codec)
            )
            if live_local_once:
                self._live_metadata_ready["audio"] = True
        elif not live_local_once or not self._live_metadata_ready.get("audio"):
            self._info_dialog["audio_codec"].setText(_("AUDIO: --"))

        if live_local_once:
            decoder_sample_ready = bool(
                width > 0
                and height > 0
                and (frame_rate > 0 or video_codec != "--")
            )
            if decoder_sample_ready:
                self._live_info_ready_samples += 1
            else:
                self._live_info_ready_samples = 0
            if (
                self._live_info_queued_generation == self._generation
                and self._live_info_ready_samples >= LIVE_INFO_READY_MIN_SAMPLES
            ):
                self._start_live_info_completion(force=False)
            metadata_incomplete = not all(
                self._live_metadata_ready.get(name, False)
                for name in ("resolution", "fps", "video", "audio")
            )
        else:
            metadata_incomplete = (
                width <= 0
                or height <= 0
                or frame_rate <= 0
                or video_codec == "--"
                or audio_codec == "--"
            )
        # Decoder metadata is local. When optional live information is missing,
        # take one second sample before releasing its bounded worker; this keeps
        # provider/image traffic out of the actual playService startup window.
        if live_local_once:
            waiting_for_second_sample = bool(
                self._live_info_queued_generation == self._generation
                and self._live_info_ready_samples < LIVE_INFO_READY_MIN_SAMPLES
            )
            if (
                (metadata_incomplete or waiting_for_second_sample)
                and self._metadata_attempts < LIVE_METADATA_MAX_SAMPLES
            ):
                self._metadata_timer.start(LIVE_METADATA_RETRY_DELAY_MS, True)
            elif not metadata_incomplete:
                self._live_metadata_complete_generation = self._generation
            return
        max_attempts = 4
        if metadata_incomplete and self._metadata_attempts < max_attempts:
            self._metadata_timer.start(LIVE_METADATA_INITIAL_DELAY_MS, True)

    @staticmethod
    def _audio_description(service):
        if service is None:
            return "--"
        try:
            tracks = service.audioTracks()
            if tracks is None or tracks.getNumberOfTracks() <= 0:
                return "--"
            current = tracks.getCurrentTrack()
            track = tracks.getTrackInfo(current)
            description = localized_language_name(
                str(track.getDescription() or "").strip()
            )
            if description:
                return description[:28]
            language_getter = getattr(track, "getLanguage", None)
            if callable(language_getter):
                language = localized_language_name(language_getter())
                if language:
                    return language[:28]
            return "--"
        except Exception:
            return "--"

    @staticmethod
    def _format_epg(event):
        time_text = event.time_text()
        if time_text:
            return "{}  {}".format(time_text, event.title)
        return str(event.title)

    def _reset_picon_display(self):
        try:
            self._picon_timer.stop()
        except Exception:
            pass
        loaders = getattr(self._info_dialog, "_gt_pixmap_loaders", None)
        if loaders is not None:
            previous = loaders.pop("player-picon", None)
            if previous is not None:
                try:
                    previous.close()
                except Exception:
                    pass
        self._picon_visible = False
        self._cached_picon_generation = -1
        self._picon_request_generation = -1
        self._picon_request_token += 1
        _cancel_worker_token(self._picon_worker_token)
        self._picon_worker_token = None
        self._picon_results = {}
        try:
            self._info_dialog["picon"].hide()
            self._info_dialog["picon_placeholder"].hide()
        except Exception:
            pass

    def _start_picon_load(self):
        if self._live_network_silent():
            return
        if self._live_transition_in_progress():
            return
        is_live = self._is_live_item()
        if not is_live:
            self._reset_picon_display()
        generation = self._generation
        if self._picon_request_generation == generation:
            return
        request_token = self._picon_request_token
        # Claim the generation before cache lookup/thread creation so a queued
        # duplicate 250-ms callback cannot launch a second request.
        self._picon_request_generation = generation
        if is_live:
            snapshot = self._live_snapshot_for_current_item()
            path = str(snapshot.get("picon_path", "") or "").strip()
            if path and _valid_live_picon_file(path):
                self._cached_picon_generation = generation
                self._apply_picon(generation, path)
                return
        url = _safe_picon_url(getattr(self.current_item, "icon", ""))
        if not url:
            return
        destination = _picon_cache_path(url)
        cached = _cached_picon_path(url)
        if cached:
            if is_live and not _valid_live_picon_file(cached):
                return
            self._cached_picon_generation = generation
            self._apply_picon(generation, cached)
            return
        self._picon_results = {
            key: value
            for key, value in self._picon_results.items()
            if key >= generation
        }
        token = _WorkerToken(
            LIVE_PICON_DOWNLOAD_TIMEOUT
            if is_live
            else WORKER_POSTER_TIMEOUT_SECONDS
        )
        _cancel_worker_token(self._picon_worker_token)
        self._picon_worker_token = token
        worker = threading.Thread(
            target=self._load_picon_worker,
            args=(
                generation,
                request_token,
                url,
                destination,
                LIVE_PICON_DOWNLOAD_TIMEOUT if is_live else None,
                token,
            ),
        )
        worker.daemon = True
        worker.start()
        self._picon_timer.start(100, True)

    def _load_picon_worker(
        self,
        generation,
        request_token,
        url,
        destination,
        timeout=None,
        token=None,
    ):
        try:
            if timeout is None:
                path = _download_client_image(
                    self.client,
                    url,
                    destination,
                    token=token,
                )
            else:
                path = _download_client_image(
                    self.client,
                    url,
                    destination,
                    timeout=timeout,
                    token=token,
                )
        except Exception:
            path = ""
        if (
            self._closed
            or (token is not None and not token.active())
            or generation != self._generation
            or request_token != self._picon_request_token
        ):
            return
        self._picon_results[generation] = (request_token, path)

    def _picon_tick(self, _unused=None):
        if self._closed:
            return
        if self._live_network_silent():
            try:
                self._picon_timer.stop()
            except Exception:
                pass
            self._picon_results = {}
            return
        if self._generation not in self._picon_results:
            token = self._picon_worker_token
            if token is None or token.expired():
                if token is not None:
                    _cancel_worker_token(token)
                if getattr(self, "_picon_worker_token", None) is token:
                    self._picon_worker_token = None
                return
            self._picon_timer.start(100, True)
            return
        request_token, path = self._picon_results.pop(self._generation)
        self._picon_worker_token = None
        if request_token != self._picon_request_token:
            return
        self._apply_picon(self._generation, path)

    def _apply_picon(self, generation, path):
        if generation != self._generation or not path:
            return
        if self._is_live_item() and not _valid_live_picon_file(path):
            return

        def loaded(success):
            if generation != self._generation:
                return
            self._picon_visible = bool(success)
            try:
                self._info_dialog["picon_placeholder"].hide()
                if success and self._info_visible:
                    self._info_dialog["picon"].show()
                else:
                    self._info_dialog["picon"].hide()
            except Exception:
                self._picon_visible = False

        attach_pixmap(
            self._info_dialog,
            "picon",
            path,
            key="player-picon",
            on_loaded=loaded,
        )

    def stop_playback(self):
        stop_archive_keepalive = getattr(
            self,
            "_stop_archive_portal_keepalive",
            None,
        )
        if callable(stop_archive_keepalive):
            stop_archive_keepalive()
        cancel_archive_startup = getattr(self, "_cancel_archive_startup", None)
        if callable(cancel_archive_startup):
            cancel_archive_startup()
        cancel_startup = getattr(self, "_cancel_live_startup", None)
        if callable(cancel_startup):
            cancel_startup()
        self._pending_channel_index = None
        for attribute in (
            "_engine_link_token",
            "_reconnect_link_token",
            "_weather_token",
            "_picon_worker_token",
        ):
            token = getattr(self, attribute, None)
            _cancel_worker_token(token)
            setattr(self, attribute, None)
        self._clear_channel_link_state()
        # A picon worker cannot be force-killed while its bounded HTTP call is
        # inside the socket layer. Invalidate its token and stop every live
        # polling timer before restoring/stopping services, so a late result
        # can neither repaint a closing infobar nor keep a GUI poll alive.
        self._stop_live_auxiliary_timers()
        self._cancel_live_info_state()
        self._reset_picon_display()
        for timer in (
            self._channel_switch_timer,
            self._reconnect_timer,
            self._engine_switch_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass
        self._live_reconnecting = False
        self._reconnect_generation = -1
        self._clear_live_engine_switch_state()
        if not self._started:
            preparation = getattr(self, "_archive_initial_playback", None)
            if preparation is not None:
                if not preparation.restore():
                    return False
                self._archive_initial_playback = None
            return True
        subtitle_controller = getattr(self, "_subtitle_controller", None)
        if subtitle_controller is not None:
            subtitle_controller.reset_for_service_change()
        navigation = getattr(self.session, "nav", None)
        _debug(
            "playback stopped content={} actual_engine={} reason=screen_exit".format(
                getattr(self.current_item, "content_type", "unknown"),
                reference_service_type(self.reference, 4097),
            )
        )
        stopped = False
        restored = False
        if navigation is not None:
            try:
                navigation.stopService()
                stopped = True
            except Exception:
                pass
            restored = self._restore_old_service(navigation)
        released = bool(
            stopped
            or (self.old_reference is not None and restored)
        )
        if released:
            self._started = False
        return released

    def stop_and_close(self):
        self._save_resume_position()
        if not self.stop_playback():
            self._set_static_info_epg(
                "epg_next",
                _("Keeping the current stream")
            )
            self.show_info()
            return
        if self.keep_playing_on_exit and self._is_live_item():
            self._fullscreen_close_result = self._live_fullscreen_result(
                False,
                released=self._old_reference_restored,
            )
            self.close(self._fullscreen_close_result)
            return
        self.close()

    def _live_fullscreen_result(self, kept, reference=None, released=False):
        service_type = None
        recovery = None
        if kept and reference is not None:
            service_type = reference_service_type(
                reference,
                self._active_live_service_type,
            )
            recovery = getattr(self, "_live_startup_recovery", None)
            if recovery is not None:
                recovery.detach()
                self._live_startup_recovery = None
        else:
            cancel_startup = getattr(self, "_cancel_live_startup", None)
            if callable(cancel_startup):
                cancel_startup()
        return {
            "kind": LIVE_FULLSCREEN_RESULT_KIND,
            "kept": bool(kept),
            "released": bool(released) and not kept,
            "selected_index": self.current_index,
            "reference": reference if kept else None,
            "service_type": service_type,
            "live_startup_recovery": recovery,
        }

    def return_to_list(self):
        """Return an adopted live service to the list without reopening it."""
        recovery = getattr(self, "_live_startup_recovery", None)
        if recovery is not None and recovery.busy:
            # A committed release may temporarily leave navigation empty (or
            # expose the receiver's lagging old reference). Do not detach and
            # transfer ownership until its pending replay has completed.
            self.show_info()
            return
        if (
            self._pending_engine_reference is not None
            or self._live_reconnecting
        ):
            self.show_info()
            return
        if (
            self._adopt_playing_service
            and self._is_live_item()
            and self._started
        ):
            navigation = getattr(self.session, "nav", None)
            getter = getattr(
                navigation,
                "getCurrentlyPlayingServiceReference",
                None,
            )
            active_reference = None
            if getter is not None:
                try:
                    active_reference = getter()
                except Exception:
                    pass
            if GTStreamListScreen._same_service_reference(
                active_reference,
                self.reference,
            ):
                # The list will re-apply its VideoWindow geometry. Mark
                # playback as transferred before close so _on_close cannot
                # stop or restore it.
                reference = active_reference
                self._stop_live_auxiliary_timers()
                self._started = False
                self._fullscreen_close_result = self._live_fullscreen_result(
                    True,
                    reference,
                )
                self.close(self._fullscreen_close_result)
                return
        if not self.stop_playback():
            self._set_static_info_epg(
                "epg_next",
                _("Keeping the current stream")
            )
            self.show_info()
            return
        self._fullscreen_close_result = self._live_fullscreen_result(
            False,
            released=self._old_reference_restored,
        )
        self.close(self._fullscreen_close_result)

    def open_live_channel_list(self):
        """Reveal the existing live list with the playing channel selected."""
        if (
            self.keep_playing_on_exit
            and self._entries
            and getattr(self.current_item, "content_type", "") == "live"
        ):
            self.return_to_list()
            return
        self.show_info()

    def _on_close(self):
        self._save_resume_position()
        stop_archive_keepalive = getattr(
            self,
            "_stop_archive_portal_keepalive",
            None,
        )
        if callable(stop_archive_keepalive):
            stop_archive_keepalive()
        cancel_archive_startup = getattr(self, "_cancel_archive_startup", None)
        if callable(cancel_archive_startup):
            cancel_archive_startup()
        self._subtitle_menu_open = False
        self._subtitle_menu_generation = None
        self._subtitle_message_tokens.clear()
        subtitle_controller = getattr(self, "_subtitle_controller", None)
        self._subtitle_controller = None
        if subtitle_controller is not None:
            subtitle_controller.close()
        self._closed = True
        # A recovery transferred by _live_fullscreen_result() has already
        # been detached and removed from this screen. Any remaining attempt
        # belongs to a service that is being closed and must not outlive it.
        cancel_startup = getattr(self, "_cancel_live_startup", None)
        if callable(cancel_startup):
            cancel_startup()
        should_stop_playback = (
            self._is_live_item() or not self.keep_playing_on_exit
        )
        archive_terminal_timer = getattr(self, "_archive_terminal_timer", None)
        if archive_terminal_timer is not None:
            try:
                archive_terminal_timer.stop()
            except Exception:
                pass
        for timer in (
            self._hide_timer,
            self._metadata_timer,
            self._picon_timer,
            self._clock_timer,
            self._weather_timer,
            self._playback_timer,
            self._resume_timer,
            self._seek_verify_timer,
            self._reconnect_timer,
            self._engine_switch_timer,
            self._channel_switch_timer,
            self._live_info_timer,
            self._archive_portal_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass
        if should_stop_playback:
            self.stop_playback()
        info_dialog = self._info_dialog
        self._info_dialog = None
        if info_dialog is not None:
            try:
                self.session.deleteDialog(info_dialog)
            except Exception:
                try:
                    info_dialog.hide()
                except Exception:
                    pass

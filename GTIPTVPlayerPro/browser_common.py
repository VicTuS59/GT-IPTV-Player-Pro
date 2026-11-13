# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""common; loaded when its screen is needed."""

import hashlib
import math
import os
import re
import stat
import struct
import threading
import time
import unicodedata
from collections import OrderedDict
from copy import copy
from urllib.parse import urlsplit
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen
from enigma import ePoint, eSize, eTimer, getDesktop, iServiceInformation
from .auxiliary_presentation import skin_after as aux_skin_after
from . import PLUGIN_VERSION
from . import sorting_ui as content_sort_ui
from .artwork_cache import PICON_CACHE as _PICON_CACHE_BUDGET
from .artwork_file import ArtworkPath
from .artwork import RETRY_DELAY_SECONDS, artwork_failure_ttl, artwork_request_profile, is_tmdb_artwork_url, prepare_webp, retryable_artwork_error, webp_dimensions
from .content import ContentItem, ContentError, XtreamContentClient
from .diagnostics import log_event, redact
from .movie_catalog import RAM_UNAVAILABLE
from .i18n import N_, _, metadata_language
from .background import attach_background
from .vod_grid import CINEMATIC_POSTER_COLUMNS
from .artwork_priority import ArtworkSlots
from .favorites import FavoriteStore, favorite_key
from .network_security import NetworkSecurityError, fetch_public_url, validate_public_url_syntax
from .playback import resume_account_scope
from .episode_design import EPISODE_ROWS
from .paths import plugin_path
from .portal_identity import safe_composite_id
from .remote_footer import decorate_remote_footer, prepared_skin, footer_item, install_remote_footer
from .scrollbar import hide_scrollbar, update_scrollbar
from .typography import _grapheme_boundaries, _text_units, ellipsize_dynamic_text, visible_count
from .download_guard import protect_client as _protect_download_client

def content_client_for(account):
    """Create the content adapter for a unified IPTV source."""
    source_type = str(getattr(account, 'source_type', 'xtream') or 'xtream').lower()
    if source_type == 'stalker':
        from .stalker import StalkerContentClient
        return _protect_download_client(StalkerContentClient(account))
    if source_type == 'm3u':
        from .m3u import M3UContentClient
        return M3UContentClient(account)
    return _protect_download_client(XtreamContentClient(account))

def _client_supports(client, capability):
    checker = getattr(client, 'supports', None)
    if callable(checker):
        try:
            return bool(checker(capability))
        except Exception:
            return False
    return True

def _validated_content_type(client, content_type):
    value = XtreamContentClient._validate_type(content_type)
    if not _client_supports(client, value):
        raise ContentError(N_('This source does not support the selected content type.'), ContentError.UNSUPPORTED_TYPE)
    return value

def _is_m3u_client(client):
    return str(getattr(client, 'source_type', 'xtream') or 'xtream').lower() == 'm3u'

def _is_stalker_client(client):
    return str(getattr(client, 'source_type', 'xtream') or 'xtream').lower() == 'stalker'

def _series_provider_id(client, value):
    """Keep Xtream numeric-only while admitting bounded Stalker composites."""
    raw_value = str(value or '')
    numeric_value = raw_value.strip()
    if numeric_value.isdigit():
        return numeric_value
    if _is_stalker_client(client):
        return safe_composite_id(raw_value)
    return ''

def _debug_identifier(value):
    """Return a useful diagnostic identity without exposing composite IDs."""
    raw_value = str(value or '')
    candidate = safe_composite_id(raw_value)
    if not candidate:
        candidate = safe_composite_id(raw_value.strip())
    if candidate:
        digest = hashlib.sha256(raw_value.encode('utf-8', 'replace')).hexdigest()
        return 'portal-composite-{}'.format(digest[:10])
    return raw_value

def _stream_diag_pts_progress(previous, current):
    """Accept only forward progress on a 33-bit decoder clock, including wrap."""
    limit = 1 << 33
    try:
        before, after = (int(previous), int(current))
    except (TypeError, ValueError, OverflowError):
        return False
    if not (0 <= before < limit and 0 <= after < limit) or before == 4294967295 or after == 4294967295:
        return False
    delta = (after - before) % limit
    return 0 < delta < limit // 2
SUBTITLE_MESSAGE_TIMEOUT_SECONDS = 3
DIRECTION_HOLD_MS = 650
NAVIGATION_DEBOUNCE_MS = 250
HERO_DEBOUNCE_MS = 100
MAX_AUDIO_TRACKS = 64
BLUE_LONG_SHORT_SUPPRESS_SECONDS = 1.5
BLUE_LONG_REPEAT_SUPPRESS_SECONDS = 0.5
LIVE_RECONNECT_RELEASE_DELAY_MS = 1000
LIVE_PICON_DOWNLOAD_TIMEOUT = 2
LIVE_EPG_CACHE_TTL_SECONDS = 60 * 60
LIVE_EPG_EMPTY_TTL_SECONDS = 0
LIVE_EPG_FETCH_LIMIT = 8
LIVE_EPG_CACHE_MAX_ENTRIES = 128
LIVE_DETAIL_DWELL_MS = 100
STALKER_LIVE_DETAIL_DWELL_MS = 350
LIVE_DETAIL_CACHE_TTL_SECONDS = 60 * 60
LIVE_DETAIL_EMPTY_RETRY_SECONDS = 30
LIVE_DETAIL_EPG_RETRY_LIMIT = 2
LIVE_DETAIL_PREFETCH_DELAY_MS = 450
LIVE_DETAIL_PREFETCH_CHANNELS = 4
LIVE_DETAIL_EPG_TIMEOUT_SECONDS = 10
LIVE_DETAIL_PREFETCH_TIMEOUT_SECONDS = 12
LIVE_DETAIL_PROGRESS_INTERVAL_MS = 10000
LIVE_PAGE_EPG_DWELL_MS = 40
STALKER_LIVE_PAGE_EPG_DWELL_MS = 100
LIVE_PAGE_EPG_POLL_MS = 40
LIVE_PAGE_EPG_REQUEST_PAUSE_MS = 100
LIVE_PAGE_EPG_TIMEOUT_SECONDS = 3
LIVE_PAGE_EPG_RETRY_TIMEOUT_SECONDS = 5
LIVE_METADATA_INITIAL_DELAY_MS = 550
LIVE_INFO_METADATA_INITIAL_DELAY_MS = 450
LIVE_METADATA_RETRY_DELAY_MS = 250
LIVE_METADATA_MAX_SAMPLES = 4
SUBTITLE_AUTO_MATCH_DELAY_MS = 1400
LIVE_INFO_READY_MIN_SAMPLES = 2
LIVE_INFO_SETTLE_MS = 700
LIVE_INFO_FALLBACK_MS = 900
STALKER_LIVE_EPG_QUIET_MS = 750
STALKER_LIVE_PICON_QUIET_MS = 2500
LIVE_INFO_POLL_MS = 100
LIVE_INFO_EPG_TIMEOUT_SECONDS = 5
CHANNEL_SWITCH_DEBOUNCE_MS = 110
CHANNEL_KEY_DUPLICATE_GUARD_MS = 90
CHANNEL_LINK_POLL_MS = 25
CHANNEL_LINK_TIMEOUT_SECONDS = 5
SEEK_VERIFY_DELAY_MS = 650
SEEK_VERIFY_MAX_CHECKS = 2
SEEK_ASYNC_VERIFY_TIMEOUT_SECONDS = 12.0
SEEK_STARTUP_GRACE_SECONDS = 12.0
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
PICON_CACHE_DIR = '/tmp/gtiptvplayer-picons'
PICON_DOWNLOAD_TIMEOUT = 8
PICON_DOWNLOAD_WORKERS = 4
BACKGROUND_POSTER_WORKERS = 2
CONTINUE_POSTER_WORKERS = 2
DETAIL_INFO_CACHE_MAX_ENTRIES = 128
PICON_CACHE_MAX_BYTES = 32 * 1024 * 1024
PICON_CACHE_MAX_FILES = 300
PICON_CACHE_MAX_AGE_SECONDS = 14 * 24 * 60 * 60
PICON_FAILURE_MAX_ENTRIES = 512
PICON_CLEANUP_INTERVAL_SECONDS = 300
PICON_PATH_CACHE_TTL_SECONDS = 60
PICON_PATH_CACHE_MAX_ENTRIES = 256
PICON_CACHE_SUFFIXES = ('.jpg', '.png', '.gif', '.bmp', '.webp')
_PICON_DOWNLOAD_LOCKS = {}
_PICON_DOWNLOAD_LOCKS_GUARD = threading.Lock()
_PICON_DOWNLOAD_SLOTS = ArtworkSlots(PICON_DOWNLOAD_WORKERS)
_PICON_FAILURES = {}
_PICON_FAILURES_GUARD = threading.Lock()
_PICON_CLEANUP_GUARD = threading.Lock()
_PICON_LAST_CLEANUP = [0.0]
_PICON_PATH_CACHE = {}
_PICON_PATH_CACHE_GUARD = threading.Lock()
_PICON_VALIDATION_CACHE = OrderedDict()
_CINEMATIC_PICON_HEADER_CACHE = OrderedDict()
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
LIVE_STREAM_PAGE_SIZE = 10
LIVE_PICON_PREFETCH_WORKERS = 2
LIVE_PICON_PREFETCH_POLL_MS = 100
LIVE_PICON_PREFETCH_MAX_ITEMS = PICON_CACHE_MAX_FILES
LIVE_PREVIEW_LINK_POLL_MS = 50
LIVE_PREVIEW_VIDEO_INITIAL_MS = 40
LIVE_PREVIEW_VIDEO_RETRY_MS = 180
LIVE_PREVIEW_VIDEO_ERROR_CHECKS = 18
LIVE_PREVIEW_VIDEO_MAX_CHECKS = 50
EPISODE_PAGE_SIZE = EPISODE_ROWS
MOVIE_CATEGORY_PAGE_SIZE = 10
MOVIE_RAIL_PAGE_SIZE = CINEMATIC_POSTER_COLUMNS
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
LIVE_FULLSCREEN_RESULT_KIND = 'gt-live-fullscreen-v1'

def _rating_label(value):
    """Return a display label only for a finite, positive numeric rating."""
    text = str(value or '').strip()
    try:
        number = float(text.split('/', 1)[0].strip().replace(',', '.'))
    except (TypeError, ValueError, OverflowError):
        number = 0.0
    if not math.isfinite(number) or number <= 0:
        return _('NO RATING')
    return '★ {}'.format(text)

def _year_not_in_title(title, year):
    """Suppress a metadata year already present in the visible title."""
    value = str(year or '').strip()
    match = re.search('(?<!\\d)((?:19|20)\\d{2})(?!\\d)', value)
    if match is None:
        return value
    candidate = match.group(1)
    if re.search('(?<!\\d){}(?!\\d)'.format(candidate), str(title or '')):
        return ''
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
        start = int(getattr(event, 'start_timestamp', 0) or 0)
        current = int(time.time() if now is None else now)
    except (TypeError, ValueError, OverflowError):
        return False
    return bool(start and start > current)

def _allow_live_detail_epg_prefetch(client):
    """Keep speculative Stalker EPG calls out of the playback request lane."""
    return not _is_stalker_client(client)
PLUGIN_PATH = plugin_path()
LIVE_PREVIEW_FRAME_PATH = plugin_path('skin', 'images', 'live-preview-frame-r16.png')
WEATHER_ICON_DIR = os.path.join(PLUGIN_PATH, 'skin', 'images', 'weather')
APP_BACKGROUND_PATH = os.path.join(PLUGIN_PATH, 'skin', 'images', 'global-neon-v0912.png')
LIVE_GUIDE_BACKGROUND_PATH = os.path.join(PLUGIN_PATH, 'skin', 'images', 'live-guide-background-r83.png')
LIVE_CATEGORY_BACKGROUND_PATH = os.path.join(PLUGIN_PATH, 'skin', 'images', 'live-category-background-r84.png')
CINEMATIC_GLASS_BACKGROUND_PATH = os.path.join(PLUGIN_PATH, 'skin', 'images', 'screen-cinematic-glass-r90.png')
CONTINUE_GLASS_BACKGROUND_PATH = os.path.join(PLUGIN_PATH, 'skin', 'images', 'screen-continue-glass-r91.png')
FAVORITES_GLASS_BACKGROUND_PATH = os.path.join(PLUGIN_PATH, 'skin', 'images', 'screen-favorites-glass-r91.png')
CINEMATIC_SCRIM_PATH = os.path.join(PLUGIN_PATH, 'skin', 'images', 'cinematic-scrim-r112.png')
CINEMATIC_FALLBACK_SHADOW_PATH = os.path.join(PLUGIN_PATH, 'skin', 'images', 'cinematic-scrim-r110.png')
FAVORITE_STAR_PATH = os.path.join(PLUGIN_PATH, 'skin', 'images', 'favorite-star-v15.png')
ASYNC_FOOTER_ITEMS = (footer_item('up_down', 'select'), footer_item('left_right', 'page'), footer_item('ok', 'open'), footer_item('green', 'refresh'), footer_item('exit', 'back'))
CONTINUE_FOOTER_ITEMS = (footer_item('arrows', 'navigate', 1.15), footer_item('ok', 'continue'), footer_item('red', 'remove'), footer_item('green', 'refresh'), footer_item('exit', 'back'))
FAVORITES_FOOTER_ITEMS = (footer_item('arrows', 'navigate', 1.05), footer_item('channel', 'page', 1.0), footer_item('ok', 'open', 0.78), footer_item('red', 'remove_favorite', 1.05), footer_item('yellow', 'move', 0.82), footer_item('green', 'refresh', 0.82), footer_item('blue', 'filter', 0.78), footer_item('exit', 'back', 0.74))
FAVORITES_MOVE_FOOTER_ITEMS = (footer_item('arrows', 'move', 1.05), footer_item('channel', 'move_page', 1.0), footer_item('ok', 'save', 0.78), footer_item('', '', 1.05), footer_item('', '', 0.82), footer_item('', '', 0.82), footer_item('', '', 0.78), footer_item('exit', 'cancel', 0.74))
LIVE_CATEGORY_FOOTER_ITEMS = (footer_item('up_down', 'select'), footer_item('left_right', 'page'), footer_item('ok', 'channel_list', 1.25), footer_item('green', 'refresh'), footer_item('blue', 'edit', 0.88), footer_item('menu', 'settings'), footer_item('exit', 'back', 0.8))
M3U_LIVE_CATEGORY_FOOTER_ITEMS = (footer_item('up_down', 'select'), footer_item('left_right', 'page'), footer_item('ok', 'channel_list', 1.2), footer_item('green', 'refresh'), footer_item('yellow', 'edit', 0.88), footer_item('blue', 'search', 0.9), footer_item('menu', 'settings'), footer_item('exit', 'back', 0.8))
MOVIE_FOOTER_ITEMS = (footer_item('arrows', 'navigate', 1.1), footer_item('channel', 'page', 0.95), footer_item('ok', 'play', 0.82), footer_item('red', 'favorite', 0.9), footer_item('green', 'refresh', 0.85), footer_item('yellow', 'edit', 0.85), footer_item('blue', 'search', 0.9), footer_item('menu', 'download', 1.0), footer_item('exit', 'back', 0.86))
ALL_MOVIES_SEARCH_FOOTER_ITEMS = (footer_item('arrows', 'navigate', 1.1), footer_item('ok', 'play', 0.86), footer_item('red', 'favorite', 0.92), footer_item('green', 'refresh', 0.88), footer_item('blue', 'search', 0.88), footer_item('menu', 'download', 1.0), footer_item('exit', 'back', 0.86))
ALL_SERIES_SEARCH_FOOTER_ITEMS = (footer_item('arrows', 'navigate', 1.1), footer_item('ok', 'open', 0.86), footer_item('red', 'favorite', 0.92), footer_item('green', 'refresh', 0.88), footer_item('blue', 'search', 0.88), footer_item('menu', 'download', 1.0), footer_item('exit', 'back', 0.86))
SERIES_FOOTER_ITEMS = (footer_item('arrows', 'navigate', 1.15), footer_item('channel', 'page', 0.95), footer_item('ok', 'open'), footer_item('red', 'favorite'), footer_item('green', 'continue'), footer_item('yellow', 'edit'), footer_item('blue', 'search'), footer_item('menu', 'download'), footer_item('exit', 'back'))
EPISODE_FOOTER_ITEMS = (footer_item('up_down', 'navigate', 0.9), footer_item('left_right', 'Season', 0.9), footer_item('channel', 'page', 0.9), footer_item('ok', 'play', 0.8), footer_item('ok_long', 'Mark watched', 1.2), footer_item('red', 'favorite', 0.9), footer_item('green', 'refresh', 0.9), footer_item('menu', 'download', 0.9), footer_item('exit', 'back', 0.8))
EPISODE_DOWNLOAD_FOOTER_ITEMS = tuple((footer_item(key, 'download' if key == 'ok' else action, weight) for key, action, weight in EPISODE_FOOTER_ITEMS))
LIVE_STREAM_FOOTER_ITEMS = (footer_item('up_down', 'select'), footer_item('left_right', 'page'), footer_item('ok', 'play', 1.05), footer_item('red', 'favorite', 0.95), footer_item('green', 'refresh'), footer_item('exit', 'back'))
LIVE_CHANNEL_ORDER_FOOTER_ITEMS = LIVE_STREAM_FOOTER_ITEMS[:-1] + (footer_item('yellow', 'edit', 0.85), LIVE_STREAM_FOOTER_ITEMS[-1])
LIVE_CHANNEL_MOVE_FOOTER_ITEMS = (footer_item('up_down', 'move'), footer_item('left_right', 'page'), footer_item('ok', 'save', 1.05), footer_item('', '', 0.95), footer_item('', ''), footer_item('', '', 0.85), footer_item('exit', 'cancel'))
M3U_LIVE_STREAM_FOOTER_ITEMS = (
    footer_item('up_down', 'select'), footer_item('left_right', 'page'),
    footer_item('ok', 'play'), footer_item('red', 'favorite', 0.9),
    footer_item('green', 'refresh'), footer_item('yellow', 'edit', 0.85),
    footer_item('blue', 'search', 0.85), footer_item('exit', 'back'),
)
M3U_LIVE_CHANNEL_MOVE_FOOTER_ITEMS = (
    footer_item('up_down', 'select'), footer_item('left_right', 'page'),
    footer_item('ok', 'place_below', 1.3), footer_item('red', 'mark', 0.9),
    footer_item('green', 'save'), footer_item('yellow', 'place_above', 1.3),
    footer_item('', '', 0.85), footer_item('exit', 'cancel'),
)
M3U_SEARCH_FOOTER_ITEMS = (footer_item('up_down', 'select'), footer_item('left_right', 'page'), footer_item('ok', 'fullscreen', 1.2), footer_item('red', 'favorite', 0.9), footer_item('blue', 'search', 0.85), footer_item('exit', 'back'))
CATEGORY_MANAGER_FOOTER_ITEMS = (footer_item('ok', 'visibility', 0.9), footer_item('red', 'mark', 0.8), footer_item('yellow', 'move', 0.85), footer_item('green', 'save', 0.85), footer_item('blue', 'reset', 0.82), footer_item('menu', 'hide_all', 1.3), footer_item('exit', 'cancel', 0.78))
CATEGORY_MANAGER_MOVE_FOOTER_ITEMS = (footer_item('up_down', 'select', 1.0), footer_item('left_right', 'page', 0.9), footer_item('ok', 'confirm', 0.9), footer_item('', '', 0.85), footer_item('', '', 0.85), footer_item('', '', 0.82), footer_item('exit', 'cancel', 0.78))
VIDEO_CODECS = {0: 'MPEG-2', 1: 'H.264', 3: 'VC-1', 4: 'MPEG-4', 5: 'VC-1 SM', 6: 'MPEG-1', 7: 'H.265 / HEVC', 16: 'AVS', 22: 'H.265 / HEVC', 40: 'AVS2'}
BROWSER_TRACE_ENABLED = str(os.environ.get('GTIPTVPLAYERPRO_TRACE', '') or '').strip().lower() in ('1', 'true', 'yes', 'on')

def _debug(message):
    if not BROWSER_TRACE_ENABLED:
        return
    safe_message = redact(message)
    log_event('browser', safe_message)
    try:
        print('[GTIPTVPlayerPro] {}'.format(safe_message))
    except Exception:
        pass

def _movie_search_text(value):
    """Return a language-neutral key without losing non-Latin scripts."""
    value = str(value or '').replace('ı', 'i').casefold()
    value = unicodedata.normalize('NFKD', value)
    value = ''.join((character for character in value if not unicodedata.combining(character)))
    return ' '.join(''.join((character if character.isalnum() else ' ' for character in value)).split())

def _movie_item_search_text(item):
    search_text = getattr(item, '_movie_search_text', None)
    if search_text is not None:
        return search_text
    search_text = _movie_search_text('{} {}'.format(getattr(item, 'name', ''), getattr(item, 'year', '')))
    try:
        item._movie_search_text = search_text
    except Exception:
        pass
    return search_text

def _pending_vod_detail(info):
    """Mask unverified synopsis text in a display copy while metadata loads."""
    if info is None:
        return None
    view = copy(info)
    language = metadata_language().replace('_', '-').lower()
    requested = str(getattr(info, '_metadata_language', getattr(info, '_movie_metadata_language', ''))).replace('_', '-').lower()
    localized = getattr(info, '_metadata_localized_plot', '')
    view.plot = localized if localized and requested == language else _('Loading...')
    return view

def _attach_subtitle_detail_metadata(item, info):
    """Attach provider/TMDb detail identity without changing UI labels.

    The Stalker browser renders a completed ``MovieInfo``/``SeriesInfo`` but
    playback keeps the original catalogue ``ContentItem``.  Store a bounded
    subtitle-only view on that item so the player can use the better title,
    year and public IDs while preserving the portal's displayed name.
    """
    if item is None or info is None:
        return False
    values = {'subtitle_title': ' '.join(str(getattr(info, 'title', '') or '').split())[:180], 'subtitle_year': str(getattr(info, 'year', '') or '').strip()[:12], 'subtitle_tmdb_id': str(getattr(info, 'tmdb_id', '') or '').strip()[:32], 'subtitle_imdb_id': str(getattr(info, 'imdb_id', '') or '').strip().lower()[:32]}
    if values['subtitle_title'] in {str(N_('Movie')), str(_('Movie')), str(N_('Series')), str(_('Series'))}:
        values['subtitle_title'] = ''
    if values['subtitle_imdb_id'].isdigit():
        values['subtitle_imdb_id'] = 'tt' + values['subtitle_imdb_id']
    changed = False
    for name, value in values.items():
        if not value:
            continue
        if getattr(item, name, '') != value:
            setattr(item, name, value)
            changed = True
    return changed

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
        return not self.cancel_event.is_set() and time.monotonic() < self.deadline

    def expired(self):
        return not self.active()

    def past_deadline(self):
        return time.monotonic() >= self.deadline

    def check(self):
        if not self.active():
            raise _WorkerCancelled()

def _poster_metadata_token(parent):
    token = _WorkerToken(WORKER_DETAIL_TIMEOUT_SECONDS)
    if parent is not None:
        token.cancel_event = parent.cancel_event
        token.deadline = min(token.deadline, parent.deadline)
    return token

def _poster_jobs(jobs, state, token=None):
    if 'queued_jobs' not in state:
        yield from jobs
        return
    while True:
        if token is not None:
            token.check()
        with state['lock']:
            if not state['queued_jobs']:
                return
            job = state['queued_jobs'].popleft()
        yield job

def _cancel_worker_token(token):
    if token is not None:
        try:
            token.cancel()
        except Exception:
            pass

def _set_component_visible(component, visible):
    """Change a GUI component's visibility only when its state differs."""
    target = bool(visible)
    current = getattr(component, 'visible', None)
    if current is not None and bool(current) == target:
        return False
    if target:
        component.show()
    else:
        component.hide()
    return True

class _DownloadSupport(object):
    """Download actions shared by search, history and favorite screens."""

    def request_selected_download(self):
        if getattr(self, '_closed', True) or getattr(self, '_loading', False):
            return
        entries = getattr(self, '_results', None) or getattr(self, '_entries', None) or []
        index = getattr(self, 'selected_index', 0)
        if not entries or index >= len(entries):
            return
        item = entries[index]
        if getattr(item, 'content_type', '') not in ('movie', 'series'):
            return
        if item.content_type == 'series' and (not getattr(item, 'episode', '')):
            parent = getattr(item, 'parent_id', '') or item.stream_id
            series = ContentItem('series', parent, item.name, icon=getattr(item, 'icon', ''))
            from .browser_episodes import GTEpisodeListScreen
            self.session.open(GTEpisodeListScreen, self.account, series, self.client, favorite_store=getattr(self, 'favorite_store', None), download_mode=True)
            return
        from .downloads_ui import request_download
        request_download(self.session, self.client, item)

class _FavoriteSupport(_DownloadSupport):
    """Shared O(1) favorite checks for browser and player screens."""

    def _init_favorite_support(self, favorite_store=None, favorite_keys=None):
        self.favorite_store = favorite_store if favorite_store is not None else FavoriteStore()
        self.favorite_scope = resume_account_scope(self.client)
        self._favorite_mutations = {}
        self._favorite_keys_timer = None
        self._favorite_load_deadline = 0.0
        self._favorite_close_hooked = False
        if favorite_keys is None:
            self._favorite_keys = set()
            self._reload_favorite_keys()
        else:
            self._favorite_keys = set(favorite_keys)
        self._favorite_initial_show = True

    def _reload_favorite_keys(self):
        try:
            cached = getattr(self.favorite_store, 'cached_keys', None)
            if callable(cached):
                keys = cached(self.favorite_scope)
                if keys is None:
                    self.favorite_store.preload()
                    self._arm_favorite_keys()
                    return False
            else:
                keys = self.favorite_store.keys(self.favorite_scope)
        except Exception:
            keys = set()
        self._stop_favorite_keys()
        changed = set(keys or ()) != getattr(self, '_favorite_keys', set())
        self._favorite_keys = set(keys or ())
        return changed

    def _hook_favorite_close(self):
        if not self._favorite_close_hooked and hasattr(self, 'onClose'):
            self.onClose.append(self._stop_favorite_keys)
            self._favorite_close_hooked = True

    def _arm_favorite_keys(self):
        if self._favorite_keys_timer is None:
            self._favorite_keys_timer = eTimer()
            _connect_timer(self._favorite_keys_timer, self._poll_favorite_keys)
        if not self._favorite_load_deadline:
            self._favorite_load_deadline = time.monotonic() + 10.0
        self._hook_favorite_close()
        self._favorite_keys_timer.start(80, True)

    def _stop_favorite_keys(self):
        self._favorite_load_deadline = 0.0
        timer = self._favorite_keys_timer
        if timer is not None:
            timer.stop()

    def _poll_favorite_keys(self):
        pending = getattr(self, '_favorite_mutations', {})
        for key, result in list(pending.items()):
            if result['done']:
                pending.pop(key, None)
                if not result['ok']:
                    self['message'].setText(_('Could not update favorites.'))
        if getattr(self, '_closed', False) or (not pending and time.monotonic() >= self._favorite_load_deadline):
            self._stop_favorite_keys()
            return
        changed = self._reload_favorite_keys()
        if pending:
            self._arm_favorite_keys()
        if not changed:
            return
        kind = getattr(self, 'content_type', '')
        renderer = None
        if kind == 'movie' and getattr(self, '_movie_entries', None):
            renderer = getattr(self, '_render_movie_posters', None)
        elif kind == 'series' and getattr(self, '_series_entries', None):
            renderer = getattr(self, '_render_series_tiles', None)
        if renderer is None:
            renderer = getattr(self, '_render_favorite_badge', None)
        if renderer is None and getattr(self, '_entries', None):
            renderer = getattr(self, '_refresh', None)
        if callable(renderer):
            renderer()

    def _favorite_screen_shown(self):
        self._hook_favorite_close()
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
            submit = getattr(self.favorite_store, 'toggle_async', None)
            if callable(submit):
                added, result = submit(self.favorite_scope, item)
                if result is not None:
                    pending = getattr(self, '_favorite_mutations', None)
                    if pending is None:
                        pending = self._favorite_mutations = {}
                    pending[key] = result
                    self._arm_favorite_keys()
            else:
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
        name = str(getattr(item, 'name', '') or 'GT IPTV')
        if added is True:
            return _('{} added to favorites.').format(name)
        if added is False:
            return _('{} removed from favorites.').format(name)
        return _('Could not update favorites.')

def _bounded_client_call(client, token, callback):
    """Run a provider call inside its optional cancellation-aware scope."""
    if token is not None:
        token.check()
    scope_factory = getattr(client, 'request_scope', None)
    if callable(scope_factory) and token is not None:
        with scope_factory(token.cancel_event, token.deadline):
            result = callback()
    else:
        result = callback()
    if token is not None:
        token.check()
    return result

def _acquire_worker_lock(lock, token=None, priority=False):
    """Acquire a shared worker lock without outliving the job deadline."""
    acquire_for = getattr(lock, 'acquire_for', None)
    if callable(acquire_for):
        acquire_for(token, priority=priority)
        return
    if token is None:
        lock.acquire()
        return
    while True:
        token.check()
        remaining = max(0.01, token.deadline - time.monotonic())
        if lock.acquire(timeout=min(0.1, remaining)):
            return

def _minute_timer_delay_ms(period_seconds=60):
    """Align clocks to their next visible value instead of polling each second."""
    try:
        period = max(1, int(period_seconds))
        remaining = period - time.time() % period
        return max(250, int(round(remaining * 1000)))
    except Exception:
        return 30000

def _client_epg_state(client):
    """Attach one small EPG cache/in-flight registry to a provider client."""
    if client is None:
        return None
    with _EPG_STATE_GUARD:
        state = getattr(client, '_gt_live_epg_state', None)
        if state is None:
            state = {'lock': threading.Lock(), 'cache': {}, 'inflight': {}, 'epoch': 0}
            try:
                setattr(client, '_gt_live_epg_state', state)
            except Exception:
                return None
        state.setdefault('epoch', 0)
        return state

def _load_shared_live_epg(client, stream_id, limit, loader, token=None, refresh=False):
    """Deduplicate preview/fullscreen EPG work without parallel API calls."""
    try:
        limit = max(1, int(limit))
    except (TypeError, ValueError, OverflowError):
        limit = 2
    state = _client_epg_state(client)
    if state is None:
        return list(loader(stream_id, limit) or [])
    key = str(stream_id or '')
    while True:
        if token is not None:
            token.check()
        now = time.monotonic()
        leader = False
        leader_epoch = 0
        with state['lock']:
            for cached_key, cached_value in list(state['cache'].items()):
                loaded_at, cached_events = cached_value
                ttl = LIVE_EPG_CACHE_TTL_SECONDS if cached_events else LIVE_EPG_EMPTY_TTL_SECONDS
                if now - loaded_at >= ttl:
                    state['cache'].pop(cached_key, None)
            cached = state['cache'].get(key)
            if cached is not None and (not refresh):
                return list(cached[1][:limit])
            pending = state['inflight'].get(key)
            if pending is None:
                leader_epoch = int(state.get('epoch', 0) or 0)
                pending = {'event': threading.Event(), 'epoch': leader_epoch, 'cancelled': False}
                state['inflight'][key] = pending
                leader = True
        if leader:
            break
        try:
            request_timeout = max(3, min(15, int(getattr(client, 'timeout', 12))))
        except (TypeError, ValueError, OverflowError):
            request_timeout = 12
        timeout = request_timeout * 2 + 2
        wait_deadline = time.monotonic() + timeout
        if token is None:
            pending['event'].wait(timeout)
        else:
            wait_deadline = min(wait_deadline, token.deadline)
            while not pending['event'].is_set():
                token.check()
                remaining = wait_deadline - time.monotonic()
                if remaining <= 0:
                    token.check()
                    return []
                pending['event'].wait(min(0.1, remaining))
            token.check()
        if pending['event'].is_set() and pending.get('cancelled'):
            continue
        with state['lock']:
            cached = state['cache'].get(key)
            return list(cached[1][:limit]) if cached is not None else []
    failed = False
    try:
        events = list(loader(stream_id, max(limit, LIVE_EPG_FETCH_LIMIT)) or [])
    except Exception:
        failed = True
        events = []
    cancelled = token is not None and (not token.active())
    with state['lock']:
        epoch_is_current = int(state.get('epoch', 0) or 0) == leader_epoch
        if events and (not failed) and (not cancelled) and epoch_is_current:
            state['cache'][key] = (time.monotonic(), events)
        elif epoch_is_current and (not refresh) and (not cancelled):
            state['cache'].pop(key, None)
        if len(state['cache']) > LIVE_EPG_CACHE_MAX_ENTRIES:
            oldest_keys = sorted(state['cache'], key=lambda cached_key: state['cache'][cached_key][0])
            for cached_key in oldest_keys[:len(state['cache']) - LIVE_EPG_CACHE_MAX_ENTRIES]:
                state['cache'].pop(cached_key, None)
        if state['inflight'].get(key) is pending:
            state['inflight'].pop(key, None)
        pending['cancelled'] = cancelled
        pending['event'].set()
    return [] if cancelled else list(events[:limit])

def _desktop_size():
    try:
        size = getDesktop(0).size()
        width = int(size.width())
        height = int(size.height())
        if width > 0 and height > 0:
            return (width, height)
    except Exception:
        pass
    return (1280, 720)

def _scale():
    width, height = _desktop_size()
    factor = min(float(width) / 1920.0, float(height) / 1080.0)

    def px(value):
        return max(1, int(round(value * factor)))
    return (width, height, px)

@aux_skin_after('channels', 'GTM3UChannelSearchScreen' )
def _browser_skin(screen_name):
    width, height, px = _scale()
    margin = px(64)
    content_width = width - margin * 2
    footer_y = height - px(92)
    return '\n<screen name="{screen_name}" position="0,0" size="{width},{height}"\n        flags="wfNoBorder" backgroundColor="#050914">\n    <widget name="app_bg" position="0,0" size="{width},{height}"\n            zPosition="0" />\n    <widget name="top_accent" position="0,0" size="{width},{accent_h}"\n            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />\n    <widget name="header" position="{margin},{header_y}" size="{header_w},{header_h}"\n            font="Regular;{title_font}" foregroundColor="#FFFFFF"\n            transparent="1"\n            valign="center" halign="left" />\n    <widget name="screen_label" position="{screen_x},{header_y}"\n            size="{screen_w},{header_h}" font="Regular;{screen_font}"\n            foregroundColor="#7F8DA5" transparent="1"\n            valign="center" halign="right" />\n    <widget name="content_panel" position="{margin},{panel_y}"\n            size="{content_width},{panel_h}" font="Regular;1"\n            backgroundColor="#0A1120" transparent="0" />\n    <widget name="list_accent" position="{margin},{panel_y}"\n            size="{accent_w},{panel_h}" font="Regular;1"\n            backgroundColor="#8B5CF6" transparent="0" />\n    <widget name="breadcrumb" position="{inner_x},{crumb_y}"\n            size="{inner_w},{crumb_h}" font="Regular;{crumb_font}"\n            foregroundColor="#22D3EE" backgroundColor="#0A1120"\n            transparent="0" valign="center" halign="left" />\n    <widget name="items" position="{inner_x},{list_y}"\n            size="{inner_w},{list_h}" font="Regular;{list_font}"\n            foregroundColor="#F3F6FB" backgroundColor="#111B2E"\n            transparent="0" valign="top" halign="left" />\n    <widget name="list_scroll_track" position="{scroll_x},{list_y}"\n            size="{scroll_w},{list_h}" font="Regular;1"\n            backgroundColor="#26344E" transparent="0" zPosition="5" />\n    <widget name="list_scroll_thumb" position="{scroll_x},{list_y}"\n            size="{scroll_w},{list_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="6" />\n    <widget name="message" position="{inner_x},{message_y}"\n            size="{inner_w},{message_h}" font="Regular;{message_font}"\n            foregroundColor="#9AA8BE" backgroundColor="#0A1120"\n            transparent="0" valign="center" halign="right" />\n    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"\n            font="Regular;{footer_font}" foregroundColor="#C8D2E3"\n            backgroundColor="#080E1A" transparent="0"\n            valign="center" halign="center" />\n</screen>\n'.format(screen_name=screen_name, width=width, height=height, accent_h=px(5), margin=margin, content_width=content_width, header_y=px(28), header_w=px(900), header_h=px(64), title_font=px(34), screen_x=width - margin - px(600), screen_w=px(600), screen_font=px(19), panel_y=px(112), panel_h=px(828), accent_w=px(7), inner_x=margin + px(38), inner_w=content_width - px(76), scroll_x=margin + content_width - px(50), scroll_w=px(8), crumb_y=px(136), crumb_h=px(52), crumb_font=px(21), list_y=px(205), list_h=px(650), list_font=px(28), message_y=px(865), message_h=px(50), message_font=px(20), footer_y=footer_y, footer_h=height - footer_y, footer_font=px(20))

def _wrap_live_summary(text, capacity, fits=None):
    """Keep the complete synopsis, using native pixel widths when available."""
    capacity = max(8, int(capacity))
    if fits is None:
        fits = lambda value: _text_units(value) <= capacity
    lines = []
    current = ''
    for word in str(text or '').split():
        if current and fits(current + ' ' + word):
            current += ' ' + word
            continue
        if current:
            lines.append(current)
            current = ''
        if fits(word):
            current = word
            continue
        boundaries = _grapheme_boundaries(word)
        segment = ''
        for index in range(len(boundaries) - 1):
            cluster = word[boundaries[index]:boundaries[index + 1]]
            if segment and (not fits(segment + cluster)):
                lines.append(segment)
                segment = ''
            segment += cluster
        current = segment
    if current:
        lines.append(current)
    return tuple(lines)

class GTAsyncListScreen(Screen):
    page_size = 10

    def __init__(self, session, screen_name, title, breadcrumb, frameless_footer=False):
        generic_skin = not getattr(self, 'skin', None)
        if generic_skin:
            self.page_size = visible_count(10)
            self.skin = prepared_skin(_browser_skin, screen_name)
        footer_items = getattr(self, '_footer_layout_items', ASYNC_FOOTER_ITEMS)
        footer_alternate_items = getattr(self, '_footer_layout_alternate_items', ())
        footer_stacked = bool(getattr(self, '_footer_layout_stacked', False))
        footer_transparent_panel = bool(getattr(self, '_footer_transparent_panel', bool(frameless_footer)))
        footer_show_dividers = bool(getattr(self, '_footer_show_dividers', not bool(frameless_footer)))
        standard_footer = not getattr(self, '_light_vod_enabled', False) or getattr(self, '_vod_discovery_layout', False)
        if standard_footer:
            self.skin = decorate_remote_footer(self.skin, footer_items, alternate_items=footer_alternate_items, transparent_panel=footer_transparent_panel, show_dividers=footer_show_dividers, skin_fonts_scaled=bool(getattr(self, '_seven_pending_kind', '')) or screen_name == 'GTStreamListScreen' or (screen_name == 'GTContentBrowserScreen' and getattr(self, 'content_type', None) == 'live'), stacked=footer_stacked, panel_height=getattr(self, '_footer_panel_height', None), bottom_padding=getattr(self, '_footer_bottom_padding', None), side_padding=getattr(self, '_footer_side_padding', None), content_y_offset=getattr(self, '_footer_content_y_offset', 0), show_web_hint=getattr(self, '_footer_show_web_hint', True))
        if not getattr(self, '_list_scroll_geometry', None):
            width, height, px = _scale()
            margin = px(64)
            content_width = width - margin * 2
            self._list_scroll_geometry = (margin + content_width - px(50), px(205), px(8), px(650))
        Screen.__init__(self, session)
        screen_background_path = str(getattr(self, '_screen_background_path', '') or '').strip()
        if screen_background_path:
            attach_background(self, 'app_bg', screen_background_path, stretch_to_widget=True)
        elif getattr(self, '_opaque_background', False):
            self['app_bg'] = Pixmap()
            self['app_bg'].hide()
        else:
            attach_background(self, 'app_bg', APP_BACKGROUND_PATH)
        self._entries = []
        self.selected_index = 0
        self._loading = False
        self._ready = False
        self._closed = False
        self._error = ''
        self._load_generation = 0
        self._load_token = None
        self._timer = eTimer()
        _connect_timer(self._timer, self._poll_result)
        self['top_accent'] = Label('')
        self['header'] = Label(_(title))
        self['screen_label'] = Label(_('CONTENT BROWSER'))
        self['content_panel'] = Label('')
        self['list_accent'] = Label('')
        self['breadcrumb'] = Label('')
        self._dynamic_breadcrumb_text = _(breadcrumb)
        self._refit_dynamic_breadcrumb()
        self['items'] = Label('')
        self['list_scroll_track'] = Label('')
        self['list_scroll_thumb'] = Label('')
        hide_scrollbar(self, 'list_scroll')
        self['message'] = Label('')
        self['footer'] = Label('')
        if standard_footer:
            install_remote_footer(self, footer_items, slot_count=max(len(footer_items), len(footer_alternate_items)), stacked=footer_stacked)
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions', 'ColorActions'], {'ok': self.open_selected, 'cancel': self.close, 'up': self.move_up, 'down': self.move_down, 'upRepeated': self.move_up, 'downRepeated': self.move_down, 'left': self.page_up, 'right': self.page_down, 'leftRepeated': self.page_up, 'rightRepeated': self.page_down, 'green': self.start_load, 'red': self.close}, -1)
        if hasattr(self, 'onShown'):
            self.onShown.append(self._start_initial_load)
        if hasattr(self, 'onLayoutFinish'):
            self.onLayoutFinish.append(self._refit_dynamic_breadcrumb)
        if hasattr(self, 'onClose'):
            self.onClose.append(self._stop)
        self.setTitle(_(title))

    def _set_dynamic_breadcrumb(self, value):
        self._dynamic_breadcrumb_text = str(value or '')
        self._refit_dynamic_breadcrumb()

    def _refit_dynamic_breadcrumb(self):
        if getattr(self, '_light_vod_enabled', False):
            return
        if not hasattr(self, '__getitem__'):
            return
        try:
            component = self['breadcrumb']
        except Exception:
            return
        ellipsize_dynamic_text(component, getattr(self, '_dynamic_breadcrumb_text', ''), fallback_chars=90)

    def load_items(self):
        raise NotImplementedError

    def _prepare_loaded_items(self, values):
        """Finish potentially blocking result preparation on the worker."""
        return list(values or [])

    def _commit_loaded_items(self, prepared, generation):
        del generation
        self._entries = list(prepared or [])

    def empty_message(self):
        return N_('No content found.')

    def item_text(self, item):
        return item.name

    def _invalidate_refresh_cache(self, *args):
        self._m3u_refresh_generation = None
        invalidator = getattr(self.client, 'invalidate_cache', None)
        if invalidator is None:
            return
        try:
            if (self.content_type == 'live' and _is_m3u_client(self.client)
                    and callable(getattr(self.client, 'refresh_summary', None))):
                self._m3u_refresh_generation = invalidator(*args, summarize=True)
            else:
                invalidator(*args)
        except Exception:
            pass

    def _show_m3u_refresh_summary(self):
        generation = getattr(self, '_m3u_refresh_generation', None)
        self._m3u_refresh_generation = None
        if generation is None or self._error or getattr(self.session, 'current_dialog', self) is not self:
            return
        summary = self.client.refresh_summary(generation)
        if summary is None:
            return
        from Screens.MessageBox import MessageBox
        status, counts = summary
        message_type = MessageBox.TYPE_INFO
        if status == 'stale':
            message = _('M3U refresh failed. Showing the cached list.')
            message_type = MessageBox.TYPE_ERROR
        elif status == 'baseline':
            message = _('M3U list refreshed. The next refresh will show changes.')
        else:
            added, removed, renamed = counts
            message = _(
                'M3U refresh summary\n\n'
                'New channels ({added})\n'
                'Channels no longer listed ({removed})\n'
                'Renamed channels ({renamed})'
            ).format(added=added, removed=removed, renamed=renamed)
        self.session.open(MessageBox, message, type=message_type)

    def start_load(self):
        if self._loading or self._closed:
            return
        _cancel_worker_token(self._load_token)
        self._load_generation += 1
        generation = self._load_generation
        timeout = WORKER_LIST_TIMEOUT_SECONDS
        client = getattr(self, 'client', None)
        if _is_m3u_client(client) and getattr(getattr(client, 'account', None), 'kind', None) == 'url':
            from .m3u import M3U_REMOTE_PREPARE_TIMEOUT
            timeout = M3U_REMOTE_PREPARE_TIMEOUT
        token = _WorkerToken(content_sort_ui.list_timeout(self, timeout))
        self._load_token = token
        self._loading = True
        self._ready = False
        self._error = ''
        self._entries = []
        self.selected_index = 0
        self['items'].setText(_('Loading content...'))
        self['message'].setText(_('Please wait'))
        hide_scrollbar(self, 'list_scroll')
        self._launch_list_load(generation, token)

    def _launch_list_load(self, generation, token):
        if not _start_browser_worker(self._run_load, (generation, token)):
            token.cancel()
            self._timer.stop()
            self._load_token = None
            self._loading = False
            self._ready = True
            self._error = N_('Could not load content')
            self._m3u_refresh_generation = None
            self._refresh()
            return
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
        error = ''
        try:
            prepared = _bounded_client_call(getattr(self, 'client', None), token, lambda: self._prepare_loaded_items(self.load_items()))
        except _WorkerCancelled:
            error = N_('Request timed out') if token is not None and token.past_deadline() else N_('Request cancelled')
        except ContentError as caught:
            error = str(caught)
        except MemoryError:
            error = RAM_UNAVAILABLE
        except Exception:
            error = N_('Could not load content')
        if self._closed or generation != self._load_generation or (token is not None and (not token.active())):
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
                self._error = N_('Request timed out')
                self._ready = True
                self._m3u_refresh_generation = None
                self._refresh()
                return
            self._timer.start(150, True)
            return
        self._loading = False
        self._load_token = None
        self._refresh()
        self._show_m3u_refresh_summary()

    def _refresh(self):
        if self._error:
            self['items'].setText(_('Could not open the content list.'))
            ellipsize_dynamic_text(self['message'], _(self._error), fallback_chars=90)
            hide_scrollbar(self, 'list_scroll')
            return
        if not self._entries:
            self['items'].setText(_(self.empty_message()))
            self['message'].setText(_('Press GREEN to try again'))
            hide_scrollbar(self, 'list_scroll')
            return
        self.selected_index %= len(self._entries)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start:page_start + self.page_size]
        lines = []
        for offset, item in enumerate(page_items):
            index = page_start + offset
            marker = '>' if index == self.selected_index else ' '
            lines.append(ellipsize_dynamic_text(self['items'], self.item_text(item), fallback_chars=90, prefix='{} {:04d}. '.format(marker, index + 1)))
        self['items'].setText('\n\n'.join(lines))
        self['message'].setText('{}  |  {}/{}'.format(len(self._entries), self.selected_index + 1, len(self._entries)))
        update_scrollbar(self, 'list_scroll', len(self._entries), self.selected_index, self.page_size, self._list_scroll_geometry)

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
_ARCHIVE_RETRY_PENDING = object()
_ARCHIVE_PTS_MODULO = 1 << 33
_ARCHIVE_PTS_MASK = _ARCHIVE_PTS_MODULO - 1

def _trace_fullscreen_osd(screen, stage):
    trace = getattr(screen, '_fullscreen_osd_trace', None)
    if not isinstance(trace, dict):
        return
    try:
        count = int(trace.get('count', 24))
        if count >= 24:
            return
        trace['count'] = count + 1
        instance = getattr(screen, 'instance', None)
        visible = getattr(instance, 'isVisible', None)
        native = int(bool(visible())) if callable(visible) else -1
        current = getattr(getattr(screen, 'session', None), 'current_dialog', None)
        log_event('fullscreen_osd', 'id={} ms={} stage={} screen={} shown={} native={} owner={}'.format(trace['id'], max(0, int((time.monotonic() - trace['started']) * 1000)), stage, screen.__class__.__name__, int(bool(getattr(screen, 'shown', False))), native, current.__class__.__name__ if current is not None else 'none'))
    except Exception:
        pass

def _disable_live_window_animation(screen):
    setter = getattr(screen, 'setAnimationMode', None)
    if callable(setter):
        try:
            setter(0)
        except Exception:
            pass

def _connect_timer(timer, callback):
    try:
        timer.timeout.connect(callback)
    except AttributeError:
        timer.callback.append(callback)


def _start_browser_worker(target, args):
    try:
        threading.Thread(target=target, args=args, daemon=True).start()
    except (RuntimeError, OSError, MemoryError) as error:
        log_event('browser', 'Could not start content worker', error)
        return False
    return True

def _read_proc_number(path, base=10):
    try:
        with open(path, 'r') as handle:
            value = handle.read(32).strip()
        return int(value, base)
    except (IOError, OSError, TypeError, ValueError):
        return -1

def _read_proc_text(path):
    try:
        with open(path, 'r') as handle:
            return handle.read(64).strip()
    except (IOError, OSError):
        return ''

def _normalise_video_codec(value):
    raw = str(value or '').strip()
    compact = raw.lower().replace('.', '').replace('-', '')
    if 'hevc' in compact or 'h265' in compact:
        return 'H.265 / HEVC'
    if 'h264' in compact or 'avc' in compact:
        return 'H.264'
    if 'mpeg2' in compact:
        return 'MPEG-2'
    if 'mpeg4' in compact:
        return 'MPEG-4'
    if 'vc1' in compact:
        return 'VC-1'
    if 'avs2' in compact:
        return 'AVS2'
    return raw[:24] if raw else '--'

def _safe_picon_url(value):
    try:
        return validate_public_url_syntax(value)['url']
    except (NetworkSecurityError, TypeError, ValueError):
        return ''

def _provider_image_request_context(client, url):
    """Return optional provider headers for one already-validated image URL."""
    context_loader = getattr(client, 'picon_request_context', None)
    if not callable(context_loader):
        return (None, None)
    try:
        context = context_loader(url) or {}
    except Exception:
        return (None, None)
    if not isinstance(context, dict):
        return (None, None)
    headers = context.get('headers')
    if not isinstance(headers, dict) or not headers:
        headers = None
    user_agent = str(context.get('user_agent') or '').strip() or None
    return (headers, user_agent)

def _download_client_image(client, url, destination, timeout=None, token=None, priority=False):
    """Download artwork with credentials only when the provider permits it."""
    request_headers, user_agent = _provider_image_request_context(client, url)
    return _download_picon(url, destination, timeout=timeout, token=token, request_headers=request_headers, user_agent=user_agent, **{'priority': priority} if priority else {})

def _load_live_picon_path(client, item, worker_token=None, timeout=None):
    """Return one validated live logo using the shared bounded downloader."""
    if worker_token is not None:
        worker_token.check()
    url = _safe_picon_url(getattr(item, 'icon', ''))
    if not url:
        return ''
    path = _cached_picon_path(url)
    if path:
        if _valid_live_picon_file(path):
            _clear_picon_failure(url)
            return path
        if not _discard_invalid_live_picon_cache(url, path):
            _mark_picon_failure(url)
            return ''
    try:
        path = _download_client_image(client, url, _picon_cache_path(url), timeout=LIVE_PICON_DOWNLOAD_TIMEOUT if timeout is None else timeout, token=worker_token)
    except _WorkerCancelled:
        raise
    except Exception as error:
        if worker_token is not None:
            worker_token.check()
        # Preserve the downloader's transient/rate-limit cooldown instead of
        # replacing a temporary failure with the default five-minute delay.
        if not _picon_failure_active(url):
            _mark_picon_failure(url, error)
        return ''
    if worker_token is not None:
        worker_token.check()
    if path and _valid_live_picon_file(path):
        _clear_picon_failure(url)
        return path
    if path:
        _discard_invalid_live_picon_cache(url, path)
    if not _picon_failure_active(url):
        _mark_picon_failure(url)
    return ''

def _provider_picon_timeout(metadata_client, url):
    return PICON_DOWNLOAD_TIMEOUT

def _picon_cache_path(url):
    digest = hashlib.sha1(url.encode('utf-8')).hexdigest()
    try:
        suffix = os.path.splitext(urlsplit(url).path)[1].lower()
    except Exception:
        suffix = ''
    if suffix == '.jpeg':
        suffix = '.jpg'
    if suffix not in PICON_CACHE_SUFFIXES:
        suffix = '.jpg'
    return os.path.join(PICON_CACHE_DIR, '{}{}'.format(digest, suffix))

def _discard_invalid_live_picon_cache(url, path):
    """Delete only this URL's verified disposable picon cache candidate."""
    candidate = os.path.abspath(str(path or ''))
    expected_base = os.path.splitext(_picon_cache_path(url))[0]
    allowed = {os.path.abspath(expected_base + suffix) for suffix in PICON_CACHE_SUFFIXES}
    if candidate not in allowed or not _ensure_picon_cache_directory(create=False) or _safe_cache_file_status(candidate) is None:
        return False
    try:
        os.unlink(candidate)
    except OSError:
        return False
    with _PICON_PATH_CACHE_GUARD:
        remembered = _PICON_PATH_CACHE.get(url)
        if remembered is not None and os.path.abspath(remembered[1]) == candidate:
            _PICON_PATH_CACHE.pop(url, None)
    with _PICON_VALIDATION_CACHE_GUARD:
        stale_keys = [cache_key for cache_key in _PICON_VALIDATION_CACHE if cache_key and cache_key[0] == candidate]
        for cache_key in stale_keys:
            _PICON_VALIDATION_CACHE.pop(cache_key, None)
    return True

def _compatible_cached_picon(url, path):
    if not is_tmdb_artwork_url(url):
        return True
    try:
        with open(path, 'rb') as handle:
            return _image_suffix(handle.read(16)) in ('.jpg', '.png')
    except OSError:
        return False

def _artwork_snapshot(path, status=None):
    status = status or _safe_cache_file_status(path)
    if status is None or not 0 < status.st_size <= MAX_PICON_BYTES:
        return ''
    identity = (status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)
    header = _cinematic_image_header(path)
    suffix, width, height = header
    if not suffix or width <= 0 or height <= 0:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        try:
            with os.fdopen(os.open(path, flags), 'rb') as handle:
                opened = os.fstat(handle.fileno())
                if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != identity:
                    return ''
                body = handle.read(MAX_PICON_BYTES + 1)
            if not 0 < len(body) <= MAX_PICON_BYTES:
                return ''
            suffix = _image_suffix(body)
            width, height = _image_dimensions(body, suffix)
            header = (suffix, width, height)
        except OSError:
            return ''
    if not suffix or width <= 0 or height <= 0 or (width > MAX_PICON_DIMENSION) or (height > MAX_PICON_DIMENSION) or (width * height > MAX_PICON_PIXELS):
        return ''
    current = _safe_cache_file_status(path)
    if current is None or (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns, current.st_ctime_ns) != identity:
        return ''
    live_valid = width <= LIVE_PICON_MAX_DIMENSION and height <= LIVE_PICON_MAX_DIMENSION and (width * height <= LIVE_PICON_MAX_PIXELS)
    return ArtworkPath(path, identity, header, live_valid)

def _remember_picon_path(url, path):
    path = _artwork_snapshot(path)
    if not path:
        return ''
    now = time.monotonic()
    with _PICON_PATH_CACHE_GUARD:
        for key, (expiry, unused_path) in list(_PICON_PATH_CACHE.items()):
            if expiry <= now:
                _PICON_PATH_CACHE.pop(key, None)
        _PICON_PATH_CACHE.pop(url, None)
        _PICON_PATH_CACHE[url] = (now + PICON_PATH_CACHE_TTL_SECONDS, path)
        while len(_PICON_PATH_CACHE) > PICON_PATH_CACHE_MAX_ENTRIES:
            oldest = min(_PICON_PATH_CACHE, key=lambda key: _PICON_PATH_CACHE[key][0])
            _PICON_PATH_CACHE.pop(oldest, None)
    return path

def _cached_picon_path(url):
    if threading.current_thread() is not threading.main_thread():
        return _validated_cached_picon_path(url)
    now = time.monotonic()
    with _PICON_PATH_CACHE_GUARD:
        remembered = _PICON_PATH_CACHE.get(url)
        if remembered is not None:
            expiry, path = remembered
            if now < expiry and isinstance(path, ArtworkPath):
                return path
    return ''

def _validated_cached_picon_path(url):
    now_mono = time.monotonic()
    with _PICON_PATH_CACHE_GUARD:
        remembered = _PICON_PATH_CACHE.get(url)
    if remembered is not None:
        expires_at, remembered_path = remembered
        status = _safe_cache_file_status(remembered_path)
        if now_mono < expires_at and status is not None and (0 < status.st_size <= MAX_PICON_BYTES):
            if _compatible_cached_picon(url, remembered_path):
                path = _artwork_snapshot(remembered_path, status)
                if path:
                    with _PICON_PATH_CACHE_GUARD:
                        _PICON_PATH_CACHE[url] = (expires_at, path)
                    _PICON_CACHE_BUDGET.touch(path)
                    return path
            _discard_invalid_live_picon_cache(url, remembered_path)
            _clear_picon_failure(url)
        with _PICON_PATH_CACHE_GUARD:
            _PICON_PATH_CACHE.pop(url, None)
    preferred = _picon_cache_path(url)
    base = os.path.splitext(preferred)[0]
    candidates = [preferred]
    candidates.extend((base + suffix for suffix in PICON_CACHE_SUFFIXES if base + suffix != preferred))
    for candidate in candidates:
        status = _safe_cache_file_status(candidate)
        if status is None:
            continue
        try:
            size = status.st_size
            if size <= 0 or size > MAX_PICON_BYTES:
                os.unlink(candidate)
                continue
            with open(candidate, 'rb') as handle:
                signature = handle.read(16)
            suffix = _image_suffix(signature)
            if not suffix or suffix not in PICON_CACHE_SUFFIXES:
                os.unlink(candidate)
                continue
            if is_tmdb_artwork_url(url) and suffix == '.webp':
                _discard_invalid_live_picon_cache(url, candidate)
                _clear_picon_failure(url)
                log_event('artwork', 'stage=cache source=tmdb outcome=discarded-webp')
                continue
            corrected = base + suffix
            if corrected != candidate:
                if os.path.isfile(corrected):
                    os.unlink(candidate)
                else:
                    os.rename(candidate, corrected)
                candidate = corrected
            candidate = _remember_picon_path(url, candidate)
            if candidate:
                _PICON_CACHE_BUDGET.touch(candidate)
                return candidate
        except (IOError, OSError):
            continue
    return ''

def _picon_failure_remaining(url, now=None):
    now = time.time() if now is None else float(now)
    with _PICON_FAILURES_GUARD:
        expires_at = _PICON_FAILURES.get(url)
        if expires_at is None:
            return 0.0
        remaining = expires_at - now
        if remaining <= 0.0:
            _PICON_FAILURES.pop(url, None)
            return 0.0
        return float(remaining)

def _picon_failure_active(url, now=None):
    return _picon_failure_remaining(url, now=now) > 0.0

def _mark_picon_failure(url, error=None):
    now = time.time()
    with _PICON_FAILURES_GUARD:
        expired = [key for key, expiry in _PICON_FAILURES.items() if expiry <= now]
        for key in expired:
            _PICON_FAILURES.pop(key, None)
        if url not in _PICON_FAILURES and len(_PICON_FAILURES) >= PICON_FAILURE_MAX_ENTRIES:
            oldest = min(_PICON_FAILURES, key=_PICON_FAILURES.get)
            _PICON_FAILURES.pop(oldest, None)
        _PICON_FAILURES[url] = now + artwork_failure_ttl(error)

def _clear_picon_failure(url):
    with _PICON_FAILURES_GUARD:
        _PICON_FAILURES.pop(url, None)

def _prune_picon_cache(force=False, now=None):
    """Periodic reconciliation; every cache write already enforces the budget."""
    before = _PICON_CACHE_BUDGET.scanned_at
    _PICON_CACHE_BUDGET.maintain(force=force, now=now)
    if before == _PICON_CACHE_BUDGET.scanned_at and (not force):
        return
    with _PICON_PATH_CACHE_GUARD:
        remembered = list(_PICON_PATH_CACHE.items())
    now_mono = time.monotonic()
    stale = [(url, value) for url, value in remembered if value[0] <= now_mono or _safe_cache_file_status(value[1]) is None]
    with _PICON_PATH_CACHE_GUARD:
        for url, value in stale:
            if _PICON_PATH_CACHE.get(url) == value:
                _PICON_PATH_CACHE.pop(url, None)

def _image_suffix(body, content_type=''):
    if body.startswith(b'\xff\xd8\xff'):
        return '.jpg'
    if body.startswith(b'\x89PNG\r\n\x1a\n'):
        return '.png'
    if body.startswith((b'GIF87a', b'GIF89a')):
        return '.gif'
    if body.startswith(b'BM'):
        return '.bmp'
    if len(body) >= 12 and body[:4] == b'RIFF' and (body[8:12] == b'WEBP'):
        return '.webp'
    content_type = str(content_type or '').lower()
    if 'jpeg' in content_type or 'jpg' in content_type:
        return '.jpg'
    for suffix in ('png', 'gif', 'bmp', 'webp'):
        if suffix in content_type:
            return '.' + suffix
    return ''

def _image_dimensions(body, suffix):
    """Read dimensions without decoding attacker-controlled image pixels."""
    try:
        if suffix == '.webp':
            return webp_dimensions(body)
        if suffix == '.png' and len(body) >= 24:
            return struct.unpack('>II', body[16:24])
        if suffix == '.gif' and len(body) >= 10:
            return struct.unpack('<HH', body[6:10])
        if suffix == '.bmp' and len(body) >= 26:
            width, height = struct.unpack('<ii', body[18:26])
            return (abs(width), abs(height))
        if suffix == '.jpg' and body.startswith(b'\xff\xd8'):
            offset = 2
            while offset + 4 <= len(body):
                if body[offset] != 255:
                    offset += 1
                    continue
                marker = body[offset + 1]
                offset += 2
                if marker in (216, 217) or 208 <= marker <= 215:
                    continue
                if offset + 2 > len(body):
                    break
                segment = struct.unpack('>H', body[offset:offset + 2])[0]
                if segment < 2 or offset + segment > len(body):
                    break
                if marker in (192, 193, 194, 195, 197, 198, 199, 201, 202, 203, 205, 206, 207) and segment >= 7:
                    height, width = struct.unpack('>HH', body[offset + 3:offset + 7])
                    return (width, height)
                offset += segment
    except (IndexError, struct.error, TypeError, ValueError):
        pass
    return (0, 0)

def _valid_image_dimensions(body, suffix):
    width, height = _image_dimensions(body, suffix)
    return width > 0 and height > 0 and (width <= MAX_PICON_DIMENSION) and (height <= MAX_PICON_DIMENSION) and (width * height <= MAX_PICON_PIXELS)

def _cinematic_image_header(path):
    if threading.current_thread() is threading.main_thread():
        return getattr(path, 'image_header', ('', 0, 0))
    status = _safe_cache_file_status(path)
    if status is None or not 0 < status.st_size <= MAX_PICON_BYTES:
        return ('', 0, 0)
    identity = (os.path.abspath(path), status.st_dev, status.st_ino, status.st_size, status.st_mtime_ns, status.st_ctime_ns)
    with _PICON_VALIDATION_CACHE_GUARD:
        cached = _CINEMATIC_PICON_HEADER_CACHE.get(identity)
        if cached is not None:
            _CINEMATIC_PICON_HEADER_CACHE.move_to_end(identity)
            return cached
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0)
        with os.fdopen(os.open(path, flags), 'rb') as handle:
            opened = os.fstat(handle.fileno())
            if (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns) != identity[1:]:
                return ('', 0, 0)
            body = handle.read(min(status.st_size, LIVE_PICON_HEADER_BYTES))
            current = os.fstat(handle.fileno())
            if (current.st_size, current.st_mtime_ns, current.st_ctime_ns) != identity[3:]:
                return ('', 0, 0)
    except (IOError, OSError):
        return ('', 0, 0)
    suffix = _image_suffix(body)
    width, height = _image_dimensions(body, suffix)
    value = (suffix, width, height)
    with _PICON_VALIDATION_CACHE_GUARD:
        _CINEMATIC_PICON_HEADER_CACHE[identity] = value
        _CINEMATIC_PICON_HEADER_CACHE.move_to_end(identity)
        while len(_CINEMATIC_PICON_HEADER_CACHE) > LIVE_PICON_VALIDATION_CACHE_MAX_ENTRIES:
            _CINEMATIC_PICON_HEADER_CACHE.popitem(last=False)
    return value

def _valid_cinematic_backdrop_file(path):
    """Accept only bounded landscape artwork for the wide cinematic hero."""
    if getattr(path, 'hero_composite_key', None) is not None:
        from .hero_artwork import valid_composite
        return valid_composite(path)
    suffix, width, height = _cinematic_image_header(path)
    return bool(suffix) and width * 3 >= height * 4 and (width >= CINEMATIC_MIN_HERO_WIDTH) and (height >= CINEMATIC_MIN_HERO_HEIGHT) and (width <= MAX_PICON_DIMENSION) and (height <= MAX_PICON_DIMENSION) and (width * height <= MAX_PICON_PIXELS)

def _valid_cinematic_fallback_file(path):
    suffix, width, height = _cinematic_image_header(path)
    return bool(suffix) and 0 < width <= MAX_PICON_DIMENSION and (0 < height <= MAX_PICON_DIMENSION) and (width * height <= MAX_PICON_PIXELS)

def _cinematic_cover_ratio(path):
    if not _valid_cinematic_fallback_file(path):
        return None
    unused_suffix, width, height = _cinematic_image_header(path)
    return (width, height)

def _valid_live_picon_file(path):
    """Reject poster-sized images before a channel-row decoder sees them."""
    if threading.current_thread() is threading.main_thread():
        return bool(getattr(path, 'live_valid', False))
    status = _safe_cache_file_status(path)
    if status is None or status.st_size <= 0 or status.st_size > MAX_PICON_BYTES:
        return False
    cache_key = (os.path.abspath(path), int(getattr(status, 'st_dev', 0) or 0), int(getattr(status, 'st_ino', 0) or 0), int(status.st_size), int(getattr(status, 'st_mtime_ns', int(float(status.st_mtime) * 1000000000))))
    with _PICON_VALIDATION_CACHE_GUARD:
        if cache_key in _PICON_VALIDATION_CACHE:
            cached = _PICON_VALIDATION_CACHE[cache_key]
            _PICON_VALIDATION_CACHE.move_to_end(cache_key)
            return bool(cached)
    try:
        with open(path, 'rb') as handle:
            body = handle.read(min(status.st_size, LIVE_PICON_HEADER_BYTES))
    except (IOError, OSError):
        return False
    if not body:
        return False
    suffix = _image_suffix(body)
    width, height = _image_dimensions(body, suffix)
    valid = bool(suffix) and width > 0 and (height > 0) and (width <= LIVE_PICON_MAX_DIMENSION) and (height <= LIVE_PICON_MAX_DIMENSION) and (width * height <= LIVE_PICON_MAX_PIXELS)
    with _PICON_VALIDATION_CACHE_GUARD:
        _PICON_VALIDATION_CACHE[cache_key] = bool(valid)
        _PICON_VALIDATION_CACHE.move_to_end(cache_key)
        while len(_PICON_VALIDATION_CACHE) > LIVE_PICON_VALIDATION_CACHE_MAX_ENTRIES:
            _PICON_VALIDATION_CACHE.popitem(last=False)
    return valid

def _ensure_picon_cache_directory(create=True):
    try:
        status = os.lstat(PICON_CACHE_DIR)
    except OSError:
        if not create:
            return False
        try:
            os.mkdir(PICON_CACHE_DIR, 448)
            status = os.lstat(PICON_CACHE_DIR)
        except OSError:
            return False
    current_uid = getattr(os, 'geteuid', lambda: status.st_uid)()
    if not stat.S_ISDIR(status.st_mode) or status.st_uid != current_uid:
        return False
    try:
        os.chmod(PICON_CACHE_DIR, 448)
    except OSError:
        return False
    return True

def _safe_cache_file_status(path):
    try:
        status = os.lstat(path)
    except OSError:
        return None
    current_uid = getattr(os, 'geteuid', lambda: status.st_uid)()
    if not stat.S_ISREG(status.st_mode) or status.st_uid != current_uid or status.st_nlink != 1:
        return None
    return status

def _download_picon(url, destination, timeout=None, token=None, request_headers=None, user_agent=None, priority=False):
    if token is not None:
        token.check()
    cached = _cached_picon_path(url)
    if cached:
        return cached
    if _picon_failure_active(url):
        return ''
    with _PICON_DOWNLOAD_LOCKS_GUARD:
        state = _PICON_DOWNLOAD_LOCKS.get(url)
        if state is None:
            state = {'lock': threading.Lock(), 'users': 0}
            _PICON_DOWNLOAD_LOCKS[url] = state
        state['users'] += 1
    try:
        _acquire_worker_lock(state['lock'], token)
        try:
            if token is not None:
                token.check()
            cached = _cached_picon_path(url)
            if cached:
                return cached
            if _picon_failure_active(url):
                return ''
            for attempt in range(2):
                try:
                    downloaded = _download_picon_unlocked(url, destination, timeout, token=token, request_headers=request_headers, user_agent=user_agent, **{'priority': priority} if priority else {})
                    break
                except _WorkerCancelled:
                    raise
                except Exception as error:
                    if attempt == 0 and retryable_artwork_error(error):
                        _debug('artwork retry error={} status={}'.format(error.__class__.__name__, getattr(error, 'status', '')))
                        if token is not None:
                            if token.cancel_event.wait(RETRY_DELAY_SECONDS):
                                token.check()
                            token.check()
                        else:
                            threading.Event().wait(RETRY_DELAY_SECONDS)
                        continue
                    _mark_picon_failure(url, error)
                    log_event('artwork', 'stage=download outcome=error source={} status={} cooldown={} reason={}'.format('tmdb' if is_tmdb_artwork_url(url) else 'provider', getattr(error, 'status', ''), artwork_failure_ttl(error), str(error)[:180]), error)
                    raise
            if not downloaded:
                _mark_picon_failure(url)
                log_event('artwork', 'stage=download outcome=rejected source={}'.format('tmdb' if is_tmdb_artwork_url(url) else 'provider'))
                return ''
            _clear_picon_failure(url)
            _prune_picon_cache()
            return _remember_picon_path(url, downloaded)
        finally:
            state['lock'].release()
    finally:
        with _PICON_DOWNLOAD_LOCKS_GUARD:
            state['users'] -= 1
            if state['users'] <= 0 and _PICON_DOWNLOAD_LOCKS.get(url) is state:
                _PICON_DOWNLOAD_LOCKS.pop(url, None)

def _download_picon_unlocked(url, destination, timeout=None, token=None, request_headers=None, user_agent=None, priority=False):
    try:
        timeout = float(PICON_DOWNLOAD_TIMEOUT if timeout is None else timeout)
    except (TypeError, ValueError, OverflowError):
        timeout = float(PICON_DOWNLOAD_TIMEOUT)
    timeout = max(0.25, timeout)
    if token is not None:
        token.check()
        timeout = min(timeout, max(0.25, token.deadline - time.monotonic()))
    _acquire_worker_lock(_PICON_DOWNLOAD_SLOTS, token, priority=priority)
    try:
        if token is not None:
            token.check()
            timeout = min(timeout, max(0.25, token.deadline - time.monotonic()))
        accept, selected_user_agent = artwork_request_profile(url, PLUGIN_VERSION, user_agent)
        fetch_arguments = (url, MAX_PICON_BYTES, timeout, accept, selected_user_agent)
        fetch_options = {}
        if request_headers:
            fetch_options['request_headers'] = request_headers
        if token is not None:
            fetch_options['cancel_event'] = token.cancel_event
        try:
            response = fetch_public_url(*fetch_arguments, **fetch_options)
        except Exception:
            if token is not None:
                token.check()
            raise
        body, headers = response[:2]
        content_type = headers.get('content-type', '')
    finally:
        _PICON_DOWNLOAD_SLOTS.release()
    if token is not None:
        token.check()
    if not body or len(body) > MAX_PICON_BYTES:
        return ''
    suffix = _image_suffix(body, content_type)
    if not suffix or suffix not in PICON_CACHE_SUFFIXES:
        return ''
    if not _valid_image_dimensions(body, suffix):
        return ''
    if suffix == '.webp':
        body, suffix = prepare_webp(body, MAX_PICON_BYTES)
        if token is not None:
            token.check()
        _debug('artwork WebP prepared format={}'.format(suffix))
    expected_base = os.path.splitext(_picon_cache_path(url))[0]
    if os.path.splitext(os.path.abspath(destination))[0] != expected_base:
        return ''
    destination = expected_base + suffix
    return _PICON_CACHE_BUDGET.store(destination, body, check=token.check if token is not None else None)

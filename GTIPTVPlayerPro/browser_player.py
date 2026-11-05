# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""player; loaded when its screen is needed."""

import datetime
import math
import os
import re
import threading
import time
import uuid
from urllib.parse import urlsplit
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from enigma import ePoint, eSize, eTimer, getDesktop, iServiceInformation
try:
    from enigma import iPlayableService as _PlayableServiceEvents
except ImportError:
    _PlayableServiceEvents = None
from .source_loader import SourceLoader
from .front_panel import (GTPlayerFrontPanelSummary, front_panel_size as _front_panel_size,
                          front_panel_layout as _front_panel_layout, front_panel_skin as _front_panel_skin)
from .artwork_file import artwork_path
from .content import ContentError
from .diagnostics import log_event
from .i18n import N_, _, localized_date_text, localized_language_name, localized_upper
from .live_recovery import media_snapshot as _decoder_media_snapshot
from .live_zero import LiveZeroKey
from .live_patch import UnlockCode, activate as activate_live_patch
from .live_patch import enabled as live_patch_enabled, supports as supports_live_patch
from .subtitle_sync import GTSubtitleSyncOverlay, SubtitleSyncKeys
from .live_history import current_live_channel_context, live_channel_key, previous_live_channel_selection, remember_live_channel
from .background import attach_native_alpha_pixmap, attach_pixmap
from .category_visibility import filter_hidden_category_items
from .playback import PlaybackResumeStore, resume_account_scope, resume_key, should_save_resume
from .episode_history import EpisodeCompletionWatcher
from .subtitle_resume import SubtitleResumeStore
from .persistence import queue_playback_save, queue_playback_clear
from .infobar import infobar_progress_geometry, infobar_skin, network_rate_text, remaining_time_text, source_account_text, split_epg_text
from .network_rate import NetworkRateSampler
from .device_usage import DeviceUsageSampler, DeviceUsageGauge, usage_text
from .stream_resolution import ResolutionTracker, resolution_text, youtube_resolution_hint
from .remote_footer import prepared_skin
from .settings import DEFAULT_INFOBAR_TIMEOUT, available_service_types, load_player_settings, normalize_infobar_timeout, serviceapp_uses_async_seek, service_engine_label
from .subtitles import MovieSubtitleController
from .video_aspect import aspect_groups, apply_aspect_choice
from .youtube_playback import youtube_duration_seconds, NATIVE_AUDIO_SERVICE_TYPE, YouTubeEndWatcher
from .youtube_history import youtube_resume_position
from .typography import ellipsize_dynamic_text, fit_dynamic_text, font_px, scale_skin_fonts, set_static_text, set_text_if_changed, visible_count
from .weather import DEFAULT_CITY, WeatherError, WeatherService, load_weather_settings, shared_weather_service, weather_description, weather_icon_name
from .download_guard import allow as _allow_account_playback
from .download_guard import remember as _remember_account_url
from .download_guard import play_service as _guarded_play_service
from .download_guard import cancel_pending as _cancel_download_pending
from .downloads import DownloadError as _DownloadError
from .browser_common import ARCHIVE_DECODER_RELEASE_MS, ARCHIVE_DECODER_TRANSITION_TICKS, ARCHIVE_PORTAL_KEEPALIVE_SECONDS, ARCHIVE_PORTAL_REQUEST_SECONDS, ARCHIVE_POSITION_NEAR_ZERO_TICKS, ARCHIVE_PROVIDER_RETRY_SECONDS, ARCHIVE_STARTUP_ACQUIRE_SECONDS, ARCHIVE_STARTUP_OVERALL_SECONDS, ARCHIVE_STARTUP_SAMPLE_MS, ARCHIVE_STARTUP_SECONDS, ARCHIVE_STARTUP_SETTLE_SECONDS, BLUE_LONG_REPEAT_SUPPRESS_SECONDS, BLUE_LONG_SHORT_SUPPRESS_SECONDS, CHANNEL_KEY_DUPLICATE_GUARD_MS, CHANNEL_LINK_POLL_MS, CHANNEL_LINK_TIMEOUT_SECONDS, CHANNEL_SWITCH_DEBOUNCE_MS, DIRECTION_HOLD_MS, LIVE_DETAIL_EMPTY_RETRY_SECONDS, LIVE_EPG_CACHE_MAX_ENTRIES, LIVE_EPG_FETCH_LIMIT, LIVE_FULLSCREEN_RESULT_KIND, LIVE_INFO_EPG_TIMEOUT_SECONDS, LIVE_INFO_FALLBACK_MS, LIVE_INFO_METADATA_INITIAL_DELAY_MS, LIVE_INFO_POLL_MS, LIVE_INFO_READY_MIN_SAMPLES, LIVE_INFO_SETTLE_MS, LIVE_METADATA_INITIAL_DELAY_MS, LIVE_METADATA_MAX_SAMPLES, LIVE_METADATA_RETRY_DELAY_MS, LIVE_PICON_DOWNLOAD_TIMEOUT, LIVE_RECONNECT_RELEASE_DELAY_MS, MAX_AUDIO_TRACKS, RESUME_4097_STABLE_SAMPLES, RESUME_INITIAL_DELAY_MS, RESUME_LENGTH_STABILITY_TOLERANCE_SECONDS, RESUME_MAX_ATTEMPTS, RESUME_RETRY_DELAY_MS, SEEK_ASYNC_VERIFY_TIMEOUT_SECONDS, SEEK_STARTUP_GRACE_SECONDS, SEEK_VERIFY_DELAY_MS, SEEK_VERIFY_MAX_CHECKS, STALKER_LIVE_EPG_QUIET_MS, SUBTITLE_AUTO_MATCH_DELAY_MS, SUBTITLE_MESSAGE_TIMEOUT_SECONDS, VIDEO_CODECS, WEATHER_ICON_DIR, WORKER_LIST_TIMEOUT_SECONDS, WORKER_PLAYBACK_LINK_TIMEOUT_SECONDS, WORKER_POSTER_TIMEOUT_SECONDS, WORKER_WEATHER_TIMEOUT_SECONDS, _ARCHIVE_PTS_MASK, _ARCHIVE_PTS_MODULO, _ARCHIVE_RETRY_PENDING, _FavoriteSupport, _WorkerCancelled, _WorkerToken, _bounded_client_call, _cached_picon_path, _cancel_worker_token, _connect_timer, _debug, _debug_identifier, _desktop_size, _disable_live_window_animation, _download_client_image, _epg_event_starts_in_future, _is_stalker_client, _load_live_picon_path, _load_shared_live_epg, _normalise_video_codec, _picon_cache_path, _picon_failure_active, _read_proc_number, _read_proc_text, _safe_picon_url, _scale, _trace_fullscreen_osd, _valid_live_picon_file
from .browser_live import _LiveStartupRecoverySupport
from .browser_common import _picon_failure_remaining, _discard_invalid_live_picon_cache
from .live_picon_retry import LivePiconRetry, DOWNLOAD_TIMEOUT as LIVE_INFO_PICON_TIMEOUT, DOWNLOAD_BUDGET as LIVE_INFO_PICON_BUDGET
from .vod_seek import VodSeekSupport

def _player_skin():
    width, height = _desktop_size()
    return '\n<screen name="GTExternalPlayerScreen" position="0,0" size="{width},{height}"\n        flags="wfNoBorder" backgroundColor="#FF000000" transparent="1"\n        zPosition="-1" />\n'.format(width=width, height=height)

def _minute_seek_skin():
    """Compact opaque fallback when the image has no native input dialog."""
    screen_width, screen_height, px = _scale()
    width, height = (px(910), px(380))
    x, y = ((screen_width - width) // 2, (screen_height - height) // 2)
    return '\n<screen name="GTMinuteSeekScreen" position="{x},{y}" size="{w},{h}"\n        flags="wfNoBorder" backgroundColor="#101A2D"\n        zPosition="110">\n    <widget name="panel" position="0,0" size="{w},{h}"\n            font="Regular;1" backgroundColor="#3008131F" transparent="0" zPosition="0" />\n    <widget name="accent" position="0,0" size="{w},{accent}"\n            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" zPosition="1" />\n    <widget name="title" position="{pad},{title_y}" size="{inner},{title_h}"\n            font="Regular;{title_font}" foregroundColor="#FFFFFF"\n            transparent="1" zPosition="2" halign="left" valign="center" />\n    <widget name="input_bg" position="{pad},{input_y}" size="{inner},{input_h}"\n            font="Regular;1" backgroundColor="#14294A" transparent="0" zPosition="1" />\n    <widget name="minutes" position="{input_x},{input_y}" size="{digits_w},{input_h}"\n            font="Regular;{digit_font}" foregroundColor="#FFFFFF"\n            transparent="1" zPosition="2" halign="left" valign="center" />\n    <widget name="unit" position="{unit_x},{input_y}" size="{unit_w},{input_h}"\n            font="Regular;{unit_font}" foregroundColor="#22D3EE"\n            transparent="1" zPosition="2" halign="right" valign="center" />\n    <widget name="position" position="{pad},{position_y}" size="{inner},{position_h}"\n            font="Regular;{position_font}" foregroundColor="#CBD5E1"\n            transparent="1" zPosition="2" halign="center" valign="center" />\n    <widget name="footer" position="{pad},{footer_y}" size="{inner},{footer_h}"\n            font="Regular;{footer_font}" foregroundColor="#CBD5E1"\n            transparent="1" zPosition="2" halign="center" valign="center" />\n</screen>\n'.format(x=x, y=y, w=width, h=height, accent=px(4), pad=px(42), inner=width - px(84), title_y=px(32), title_h=px(65), title_font=px(38), input_y=px(112), input_h=px(105), input_x=px(64), digits_w=px(560), digit_font=px(70), unit_x=width - px(255), unit_w=px(190), unit_font=px(28), position_y=px(239), position_h=px(48), position_font=px(26), footer_y=px(319), footer_h=px(45), footer_font=px(22))

def _player_info_progress_geometry(width, px):
    return infobar_progress_geometry(width, px)

def _player_info_skin(live=True):
    width, height, px = _scale()
    return infobar_skin(width, height, px, live=live)

def build_extplayer_reference(url, title, content_type='movie', service_type=None):
    try:
        from enigma import eServiceReference
    except ImportError:
        raise ContentError(N_('Enigma2 player service support is unavailable'))
    if service_type is None:
        try:
            service_type = load_player_settings().service_type_for(content_type)
        except Exception:
            service_type = 4097 if content_type == 'live' else 5002
    try:
        service_type = int(service_type)
    except (TypeError, ValueError, OverflowError):
        service_type = 4097 if content_type == 'live' else 5002
    if content_type == 'live' and service_type == 1:
        try:
            parsed = urlsplit(str(url or '').split('#', 1)[0])
            scheme = parsed.scheme.lower()
            path = parsed.path.lower()
            explicit_http_ts = scheme in ('http', 'https') and path.endswith(('.ts', '.mpegts'))
        except (TypeError, ValueError):
            explicit_http_ts = False
        if not explicit_http_ts:
            service_type = 4097
    reference = eServiceReference(service_type, 0, url)
    set_name = getattr(reference, 'setName', None)
    if set_name is not None:
        set_name(str(title or 'GT IPTV'))
    return reference

def reference_service_type(reference, fallback=5002):
    for attribute in ('type', 'service_type'):
        value = getattr(reference, attribute, None)
        try:
            value = value() if callable(value) else value
            return int(value)
        except (TypeError, ValueError, OverflowError):
            pass
    getter = getattr(reference, 'getType', None)
    if getter is not None:
        try:
            return int(getter())
        except (TypeError, ValueError, OverflowError):
            pass
    return int(fallback)

def reference_stream_url(reference):
    """Return the original stream URL without depending on one image API."""
    if reference is None:
        return ''
    for name in ('getPath', 'getUrl'):
        getter = getattr(reference, name, None)
        if getter is None:
            continue
        try:
            value = getter()
        except Exception:
            value = ''
        if value:
            return str(value)
    for name in ('url', 'path'):
        value = getattr(reference, name, '')
        try:
            value = value() if callable(value) else value
        except Exception:
            value = ''
        if value:
            return str(value)
    return ''

def _m3u_navigation_changed(navigation, reference=None):
    try:
        import sys
        module = sys.modules.get(__package__ + '.m3u_live')
        if module is None:
            return
        if reference is None:
            getter = getattr(navigation, 'getCurrentlyPlayingServiceReference', None)
            reference = getter() if callable(getter) else None
        module.navigation_changed(reference)
    except Exception as error:
        log_event('m3u_live', 'release-failed error_type=' + type(error).__name__)

def _play_service(navigation, reference):
    """Start a service and normalise Enigma2's image-specific return value."""
    result = _guarded_play_service(navigation, reference)
    if isinstance(result, int) and result != 0:
        raise RuntimeError('playService returned {}'.format(result))
    _m3u_navigation_changed(navigation, reference)
    return result

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

    def __init__(self, player, timer=None, clock=None, thread_factory=None, snapshot_reader=None):
        self.player = player
        self.navigation = getattr(player.session, 'nav', None)
        self.timer = timer or eTimer()
        self.clock = clock or time.monotonic
        self.thread_factory = thread_factory or threading.Thread
        self.snapshot_reader = snapshot_reader or self._default_snapshot
        _connect_timer(self.timer, self.tick)
        self.phase = 'idle'
        self.generation = -1
        self.reference = None
        self.reference_identity = ''
        self.reference_seen = False
        self.attempt = 0
        self.deadline = 0.0
        self.overall_deadline = 0.0
        self.settle_deadline = 0.0
        self.acquire_not_before = 0.0
        self.hard_failure = ''
        self.provider_retry_used = False
        self.latest_url = ''
        self.pending_reference = None
        self.pending_kind = ''
        self.pending_release_fenced = False
        self.decoder_release_fenced = False
        self.release_not_before = 0.0
        self.previous_service = getattr(player, '_archive_previous_service', None)
        self.baseline_pts = getattr(player, '_archive_previous_pts', (None, None))
        self.baseline_position = getattr(player, '_archive_previous_position', None)
        self.target_service = None
        self.target_reference_confirmed = False
        self.service_reference_status = 'unknown'
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
            return ''
        for name in ('toCompareString', 'toString'):
            getter = getattr(reference, name, None)
            if not callable(getter):
                continue
            try:
                value = getter()
            except Exception:
                value = ''
            if value:
                return str(value)
        return '{}:{}'.format(reference_service_type(reference, 5002), reference_stream_url(reference))

    @staticmethod
    def _default_snapshot(service):
        return _decoder_media_snapshot(service, iServiceInformation)

    @staticmethod
    def _normalise_pts(value):
        try:
            value = int(value)
        except (TypeError, ValueError, OverflowError):
            return None
        if value < 0 or value > _ARCHIVE_PTS_MASK or value in (4294967295, _ARCHIVE_PTS_MASK):
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
                return current - previous & _ARCHIVE_PTS_MASK
        return None

    def _update_position_transition(self, value, sampled_at):
        if value is None or self.position_target_transition:
            return
        if self.previous_service is None or self.position_tail_value is None:
            self.position_target_transition = True
            return
        if self.decoder_release_fenced and sampled_at >= self.acquire_not_before:
            self.position_target_transition = True
            self.position_observation = None
            return
        if sampled_at >= self.acquire_not_before and (self.target_reference_confirmed or self.decoder_release_fenced) and (self.baseline_position <= ARCHIVE_POSITION_NEAR_ZERO_TICKS) and (value <= ARCHIVE_POSITION_NEAR_ZERO_TICKS):
            self.position_target_transition = True
            self.position_observation = None
            return
        delta = self._clock_delta(self.position_tail_value, value, allow_wrap=False)
        if delta is None or delta > ARCHIVE_DECODER_TRANSITION_TICKS:
            self.position_target_transition = True
            self.position_observation = None
            return
        self.position_tail_value = value

    def _update_pts_transition(self, source, value):
        if value is None or self.pts_target_transition:
            return
        if self.previous_service is None:
            self.pts_target_transition = True
            return
        if self.pts_tail_value is None:
            self.pts_target_transition = True
            return
        if source != self.pts_tail_source:
            self.pts_tail_source = source
            self.pts_tail_value = value
            self.pts_observation = None
            return
        delta = self._clock_delta(self.pts_tail_value, value, allow_wrap=True)
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
        return bool(player is not None and (not getattr(player, '_closed', False)) and getattr(player, '_started', False) and (getattr(getattr(player, 'current_item', None), 'content_type', '') == 'catchup') and (int(getattr(player, '_generation', -2)) == self.generation) and (self._identity(getattr(player, 'reference', None)) == self.reference_identity))

    def _current_reference_identity(self):
        if self.navigation is None:
            return ''
        getter = getattr(self.navigation, 'getCurrentlyPlayingServiceReference', None)
        if not callable(getter):
            getter = getattr(self.navigation, 'getCurrentlyPlayingServiceOrGroup', None)
        try:
            return self._identity(getter()) if callable(getter) else ''
        except Exception:
            return ''

    def _owns_service(self):
        if not self._screen_matches():
            return False
        matches = self._current_reference_identity() == self.reference_identity
        if matches:
            self.reference_seen = True
        return matches

    def _reference_is_settling(self):
        return bool(not self.reference_seen and self._screen_matches() and (self.clock() < self.settle_deadline))

    def _released_service_is_owned(self):
        if not self._screen_matches():
            return False
        current = self._current_reference_identity()
        return not current or current == self.reference_identity

    def _current_service(self):
        getter = getattr(self.navigation, 'getCurrentService', None) if self.navigation is not None else None
        try:
            return getter() if callable(getter) else None
        except Exception:
            return None

    def _reference_strings(self, reference):
        values = set()
        for name in ('toCompareString', 'toString'):
            getter = getattr(reference, name, None)
            if not callable(getter):
                continue
            try:
                value = getter()
            except Exception:
                value = ''
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
        return (service_type, path)

    def _service_reference_state(self, service):
        """Return True/False for an advertised service ref, else None."""
        key = getattr(iServiceInformation, 'sServiceref', None)
        if service is None or key is None:
            return None
        try:
            info = service.info()
            getter = getattr(info, 'getInfoString', None)
            value = str(getter(key) if callable(getter) else '').strip()
        except Exception:
            value = ''
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
            self.service_reference_status = 'absent'
            return False
        reference_state = self._service_reference_state(service)
        if reference_state is False:
            self.service_reference_status = 'mismatch'
            return False
        if reference_state is True:
            self.service_reference_status = 'target'
            if not self.target_reference_confirmed:
                self.position_observation = None
                self.pts_observation = None
                self.pts_target_transition = False
            self.target_reference_confirmed = True
            self.target_service = service
        else:
            self.service_reference_status = 'unknown'
            if service is self.previous_service and (not self.decoder_release_fenced):
                return False
        if self.target_service is None:
            self.target_service = service
        elif service is not self.target_service and reference_state is not True:
            return False
        return True

    @classmethod
    def _snapshot_pts(cls, snapshot):
        snapshot = snapshot if isinstance(snapshot, dict) else {}
        video = cls._normalise_pts(snapshot.get('video_pts'))
        if video is not None:
            return (snapshot.get('video_pts_source') or 'video', video)
        audio = cls._normalise_pts(snapshot.get('audio_pts'))
        return ('audio', audio) if audio is not None else (None, None)

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
            return (None, False)
        if observation is None or observation['source'] != source:
            return ({'source': source, 'value': int(value), 'at': sampled_at, 'advances': 0, 'ticks': 0}, False)
        elapsed = max(0.0, sampled_at - float(observation['at']))
        delta = self._clock_delta(observation['value'], value, allow_wrap=allow_wrap)
        max_step = max(450000, int(90000 * max(elapsed, 1.0) * 4))
        if delta is None or delta > max_step:
            return ({'source': source, 'value': int(value), 'at': sampled_at, 'advances': 0, 'ticks': 0}, False)
        observation['value'] = int(value)
        observation['at'] = sampled_at
        if delta > 0:
            observation['advances'] += 1
            observation['ticks'] += delta
        acquired = bool(sampled_at >= self.acquire_not_before and delta > 0 and (observation['advances'] >= 2) and (observation['ticks'] >= 45000))
        return (observation, acquired)

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
        self.position_observation, position_advanced = self._observe(self.position_observation, 'position', position, sampled_at, False)
        position_advanced = bool(position_advanced and self.position_target_transition)
        try:
            snapshot = self.snapshot_reader(service) if service is not None else {}
        except Exception:
            snapshot = {}
        pts_source, pts_value = self._snapshot_pts(snapshot)
        if self.target_reference_confirmed:
            self._update_pts_transition(pts_source, pts_value)
        self.pts_observation, pts_advanced = self._observe(self.pts_observation, pts_source, pts_value, sampled_at, True)
        if sampled_at < self.acquire_not_before:
            self.position_observation = None
            self.pts_observation = None
            return False
        pts_advanced = bool(pts_advanced and self.target_service is not None and self.pts_target_transition and (sampled_at >= self.acquire_not_before))
        if not (position_advanced or pts_advanced):
            return False
        evidence = 'position' if position_advanced else 'pts'
        self._log('acquired', evidence)
        self._finish('acquired')
        return True

    def _log(self, outcome, reason):
        log_event('playback', 'TV archive startup outcome={} attempt={} engine={} reason={}'.format(outcome, self.attempt, reference_service_type(self.reference, 5002), reason))

    def start(self):
        if self.phase != 'idle' or self.navigation is None:
            return False
        if getattr(getattr(self.player, 'current_item', None), 'content_type', '') != 'catchup':
            return False
        self.generation = int(getattr(self.player, '_generation', -1))
        self.reference = getattr(self.player, 'reference', None)
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
        self.phase = 'watch'
        self.deadline = min(now + ARCHIVE_STARTUP_SECONDS, self.overall_deadline)
        self.settle_deadline = now + ARCHIVE_STARTUP_SETTLE_SECONDS
        self.acquire_not_before = now + ARCHIVE_STARTUP_ACQUIRE_SECONDS
        self.reference_seen = False
        self.target_service = None
        self.target_reference_confirmed = False
        self.service_reference_status = 'unknown'
        self.position_target_transition = False
        self.pts_target_transition = False
        self.position_tail_value = self.baseline_position
        self.pts_tail_source, self.pts_tail_value = self.baseline_pts
        self.hard_failure = ''
        self.position_observation = None
        self.pts_observation = None
        self._log('armed', 'monitoring')
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
            if self.phase not in ('watch', 'resolve') or not self._owns_service():
                return
            hard = self._event_values('evTuneFailed', 'evEOF', 'evStopped')
            user = getattr(_PlayableServiceEvents, 'evUser', None) if _PlayableServiceEvents is not None else None
            if isinstance(user, int):
                hard.update((user + 10, user + 11, user + 12))
            if event in hard:
                self.hard_failure = 'service-event'
                self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)
                return
            progress = self._event_values('evStart', 'evUpdatedInfo', 'evVideoSizeChanged')
            if event in progress:
                self._sample()
        finally:
            self.dispatching = False

    def _provider_retry(self):
        self.provider_retry_used = True
        resolver = getattr(getattr(self.player, 'client', None), 'decoder_retry_playback_url', None)
        if not callable(resolver):
            self._terminal_failure('provider-unavailable')
            return
        remaining = self.overall_deadline - self.clock()
        if remaining <= 0:
            self._terminal_failure('deadline')
            return
        token = _WorkerToken(min(ARCHIVE_PROVIDER_RETRY_SECONDS, remaining))
        self.resolve_token = token
        self.resolve_serial += 1
        serial = self.resolve_serial
        with self.result_lock:
            self.result = _ARCHIVE_RETRY_PENDING
        self.phase = 'resolve'

        def resolve():
            url = ''
            reason = ''
            try:
                url = str(resolver(self.player.current_item, self.latest_url, cancel_event=token.cancel_event, deadline=token.deadline) or '').strip()
            except ContentError as error:
                reason = str(getattr(error, 'reason', '') or '')
                url = ''
            except Exception:
                reason = 'unexpected'
                url = ''
            with self.result_lock:
                if serial == self.resolve_serial and self.phase == 'resolve' and token.active():
                    self.result = (url, reason)
        try:
            worker = self.thread_factory(target=resolve)
            worker.daemon = True
            worker.start()
        except Exception:
            _cancel_worker_token(token)
            self.resolve_token = None
            self._terminal_failure('provider-worker')
            return
        self._log('retry', 'provider')
        self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)

    def _prepare_provider_replay(self, url):
        if not self._screen_matches() or self.clock() >= self.overall_deadline:
            self._terminal_failure('deadline')
            return
        if not self._owns_service():
            self.cancel()
            return
        try:
            candidate = build_extplayer_reference(url, getattr(self.player.current_item, 'name', 'GT IPTV'), 'catchup', service_type=reference_service_type(self.reference, 4097))
        except Exception:
            self._terminal_failure('reference-rejected')
            return
        _cancel_worker_token(self.resolve_token)
        self.resolve_token = None
        self.latest_url = str(url)
        self.pending_reference = candidate
        self.pending_kind = 'provider'
        self.pending_release_fenced = False
        self.phase = 'release'
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
            self._terminal_failure('release-failed')
            return
        self.pending_release_fenced = True
        self.release_not_before = self.clock() + ARCHIVE_DECODER_RELEASE_MS / 1000.0
        self.timer.start(ARCHIVE_DECODER_RELEASE_MS, True)

    def _commit_replay(self):
        if self.clock() >= self.overall_deadline:
            self._terminal_failure('deadline')
            return
        if not self._released_service_is_owned():
            self.cancel()
            return
        candidate = self.pending_reference
        kind = self.pending_kind
        try:
            if candidate is None:
                raise ValueError('missing archive retry reference')
            _play_service(self.navigation, candidate)
        except Exception:
            self.pending_reference = None
            self.pending_kind = ''
            self._terminal_failure('play-service-rejected')
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
        self.pending_kind = ''
        self.pending_release_fenced = False
        self.attempt += 1
        subtitle_controller = getattr(player, '_subtitle_controller', None)
        if subtitle_controller is not None:
            try:
                subtitle_controller.reset_for_service_change()
            except Exception:
                pass
        player._after_zap()
        self._log('replay-accepted', kind)
        self._begin_watch()

    def _recover(self, reason):
        if self.clock() >= self.overall_deadline:
            self._terminal_failure('deadline')
            return
        self._log('stalled', reason)
        if not self.provider_retry_used:
            self._provider_retry()
            return
        self._terminal_failure('retry-exhausted')

    def tick(self):
        if self.cleanup_pending:
            self.cleanup_pending = False
            self._unlisten()
        if self.phase == 'watch':
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
                self._terminal_failure('deadline')
                return
            if self._sample():
                return
            now = self.clock()
            if now >= self.deadline:
                if not self.target_reference_confirmed and self.service_reference_status == 'unknown' and (not self.position_target_transition):
                    self._log('inconclusive', 'service-reference-unavailable')
                    self._finish('inconclusive')
                    return
                self._recover(self.hard_failure or 'timeout')
                return
            self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)
            return
        if self.phase == 'resolve':
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
                self._terminal_failure('deadline')
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
                    self._terminal_failure('deadline')
                elif token is None:
                    self.cancel()
                elif token.expired():
                    cancelled = token.cancel_event.is_set() and (not token.past_deadline())
                    _cancel_worker_token(token)
                    self.resolve_token = None
                    if cancelled:
                        self.cancel()
                    else:
                        self._terminal_failure('provider-deadline')
                else:
                    self.timer.start(ARCHIVE_STARTUP_SAMPLE_MS, True)
                return
            self.resolve_token = None
            url, reason = result
            terminal_reasons = {ContentError.ACCESS_DENIED, ContentError.RATE_LIMITED, ContentError.CANCELLED, ContentError.DEADLINE}
            if reason in terminal_reasons:
                self._log('provider-result', 'terminal')
                self._terminal_failure('provider-{}'.format(reason))
                return
            if url and url != self.latest_url:
                self.latest_url = url
                self._log('provider-result', 'fresh-ticket')
                self._prepare_provider_replay(url)
                return
            elif url:
                self._log('provider-result', 'same-route')
            elif reason:
                self._log('provider-result', 'unavailable')
            else:
                self._log('provider-result', 'same-route')
            self._terminal_failure('provider-unavailable')
            return
        if self.phase == 'release':
            if not self._released_service_is_owned():
                self.cancel()
                return
            if self.clock() >= self.overall_deadline:
                self._terminal_failure('deadline')
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
        self.pending_kind = ''
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
        if self.phase in ('failed', 'cancelled', 'acquired', 'inconclusive'):
            return
        self._log('failed', reason)
        self._finish('failed')
        callback = getattr(self.player, '_archive_startup_failed', None)
        if callable(callback):
            callback()

    def cancel(self):
        if self.phase in ('cancelled', 'acquired', 'failed', 'inconclusive'):
            if self.cleanup_pending and (not self.dispatching):
                self.cleanup_pending = False
                try:
                    self.timer.stop()
                except Exception:
                    pass
                self._unlisten()
            return
        self._finish('cancelled')

def _launch_extplayer(session, client, item, url, items=None, selected_index=0, favorite_store=None, favorite_parent=None, favorite_keys=None, owner=None, archive_initial_playback=None):
    _remember_account_url(client, url)
    reference = build_extplayer_reference(url, item.name, getattr(item, 'content_type', 'movie'))
    entries = items if getattr(items, 'full_category', False) else list(items or [])
    store = PlaybackResumeStore()
    key = resume_key(client, item)
    bookmark = store.get(key)
    if bookmark:
        remember = getattr(store, 'remember_async', None)
        if remember is not None:
            try:
                remember(key, item, resume_account_scope(client))
            except Exception:
                pass

    def launch(start_position=0):
        if not _allow_account_playback(session, client):
            return
        callback = getattr(owner, '_episode_playback_closed', None)
        opener = session.open
        if callable(callback) and hasattr(session, 'openWithCallback'):
            opener = lambda *args, **kwargs: session.openWithCallback(callback, *args, **kwargs)
        opener(GTExternalPlayerScreen, reference, item, client, entries, selected_index, False, None, None, store, key, start_position, favorite_store=favorite_store, favorite_parent=favorite_parent, favorite_keys=favorite_keys, playback_owner=owner, archive_initial_playback=archive_initial_playback)
    consume_resume = getattr(owner, '_consume_episode_resume_request', None)
    if callable(consume_resume) and consume_resume(key) and bookmark:
        launch(bookmark['position'])
        return
    if not bookmark or not hasattr(session, 'openWithCallback'):
        launch(0)
        return

    def resume_answer(answer):
        if answer:
            launch(bookmark['position'])
        else:
            queue_playback_clear(store, key)
            launch(0)
    session.openWithCallback(resume_answer, MessageBox, _('Resume playback from the saved position?\n\nSaved position: {}').format(GTExternalPlayerScreen._format_play_time(bookmark['position'])), MessageBox.TYPE_YESNO, default=True)

class _ArchiveInitialPlayback(object):
    """Own the one stop/wait/restore transaction before an archive link."""

    def __init__(self, session):
        self.session = session
        self.navigation = getattr(session, 'nav', None)
        self.reference_getter = getattr(self.navigation, 'getCurrentlyPlayingServiceReference', None)
        if not callable(self.reference_getter):
            self.reference_getter = getattr(self.navigation, 'getCurrentlyPlayingServiceOrGroup', None)
        if not callable(self.reference_getter):
            raise RuntimeError('Archive navigation reference unavailable')
        self.previous_reference = self.reference_getter()
        self.released = False
        self.not_before = 0.0

    def release(self):
        self.released = True
        result = self.navigation.stopService()
        if isinstance(result, int) and result != 0:
            raise RuntimeError('Archive decoder release rejected')
        self.not_before = time.monotonic() + ARCHIVE_DECODER_RELEASE_MS / 1000.0
        log_event('tv_archive', 'Initial playback release accepted delay_ms={}'.format(ARCHIVE_DECODER_RELEASE_MS))

    def owns_navigation(self):
        if not self.released or getattr(self.session, 'nav', None) is not self.navigation:
            return False
        try:
            current = self.reference_getter()
        except Exception:
            return False
        from .browser_live import GTStreamListScreen
        return bool(current is None or GTStreamListScreen._same_service_reference(current, self.previous_reference))

    def ready(self):
        if time.monotonic() < self.not_before or not self.owns_navigation():
            return False
        try:
            if self.reference_getter() is None:
                return True
            getter = getattr(self.navigation, 'getCurrentService', None)
            return callable(getter) and getter() is None
        except Exception:
            return False

    def restore(self):
        if not self.released:
            return True
        if not self.owns_navigation():
            self.released = False
            return True
        if self.previous_reference is not None:
            try:
                _play_service(self.navigation, self.previous_reference)
            except Exception as error:
                log_event('tv_archive', 'Initial playback restore failed', error)
                return False
        self.released = False
        return True

class _AsyncPlaybackResolver(object):
    """Resolve provider-created links without blocking Enigma2's GUI."""

    def __init__(self, session, client, item, items, selected_index, owner=None, favorite_store=None, favorite_parent=None, favorite_keys=None):
        self.session = session
        self.client = client
        self.item = item
        self.items = items if getattr(items, 'full_category', False) else list(items or [])
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
            self.owner['message'].setText(value)
            return True
        except Exception:
            return False

    def start(self):
        if self.started or self.finished:
            return self
        self.started = True
        previous = getattr(self.owner, '_playback_link_controller', None)
        if previous is not None and previous is not self:
            previous.cancel()
        if self.owner is not None:
            self.owner._playback_link_controller = self
            callbacks = getattr(self.owner, 'onClose', None)
            if isinstance(callbacks, list) and self.cancel not in callbacks:
                callbacks.append(self.cancel)
        self._set_message(_('Please wait'))
        if getattr(self.item, 'content_type', '') == 'catchup':
            try:
                self.archive_initial_playback = _ArchiveInitialPlayback(self.session)
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
            url = _bounded_client_call(self.client, self.token, lambda: self.client.playback_url(self.item))
            self.result = (url, None)
        except Exception as error:
            self.result = (None, error)

    def _poll(self):
        if self.finished:
            return
        if getattr(self.owner, '_closed', False):
            self.cancel()
            return
        if self.archive_release_pending:
            preparation = self.archive_initial_playback
            if self.token.expired() or not preparation.owns_navigation():
                self._finish(None, ContentError(N_('Stream link timed out')))
                return
            if not preparation.ready():
                remaining = preparation.not_before - time.monotonic()
                self.timer.start(max(25, int(round(remaining * 1000))), True)
                return
            self.archive_release_pending = False
            log_event('tv_archive', 'Initial archive link resolution after release')
            self._start_worker()
            return
        if self.result is None:
            if self.token.expired():
                self._finish(None, ContentError(N_('Stream link timed out')))
                return
            self.timer.start(75, True)
            return
        self._finish(*self.result)

    def _detach(self):
        if getattr(self.owner, '_playback_link_controller', None) is self:
            self.owner._playback_link_controller = None
        callbacks = getattr(self.owner, 'onClose', None)
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
        if preparation is not None and (not preparation.ready()):
            error = error or RuntimeError('Archive navigation ownership changed')
        if error is not None or not url or (not self.token.active()):
            _cancel_worker_token(self.token)
            if preparation is None or preparation.restore():
                self._detach()
            log_event('playback', 'Asynchronous stream link resolution failed', error)
            self._resolution_failed(error)
            return
        self._set_message('')
        try:
            _launch_extplayer(self.session, self.client, self.item, url, self.items, self.selected_index, favorite_store=self.favorite_store, favorite_parent=self.favorite_parent, favorite_keys=self.favorite_keys, owner=self.owner, archive_initial_playback=preparation)
            self.archive_initial_playback = None
            self._detach()
        except Exception as launch_error:
            if preparation is None or preparation.restore():
                self._detach()
            log_event('playback', 'Resolved player launch failed', launch_error)
            self._resolution_failed(launch_error)

    def _resolution_failed(self, error=None):
        _cancel_download_pending(self.client)
        resume = getattr(self.owner, '_resume_after_playback_resolution_failure', None)
        if callable(resume) and (not getattr(self.owner, '_closed', False)):
            try:
                resume()
            except Exception:
                pass
        message = _('Could not open the archived programme.') if getattr(self.item, 'content_type', '') == 'catchup' else _('Could not open the stream.')
        if isinstance(error, _DownloadError):
            message = _(str(error))
        if not self._set_message(message):
            try:
                self.session.open(MessageBox, message, MessageBox.TYPE_ERROR)
            except Exception:
                pass

    def cancel(self, *args):
        del args
        self.finished = True
        _cancel_worker_token(self.token)
        _cancel_download_pending(self.client)
        try:
            self.timer.stop()
        except Exception:
            pass
        preparation = self.archive_initial_playback
        if preparation is None or preparation.restore():
            self.archive_initial_playback = None
            self._detach()

def open_extplayer(session, client, item, items=None, selected_index=0, owner=None, favorite_store=None, favorite_parent=None, favorite_keys=None):
    if not _allow_account_playback(session, client):
        return
    if favorite_store is None:
        favorite_store = getattr(owner, 'favorite_store', None)
    if favorite_parent is None:
        favorite_parent = getattr(item, 'favorite_parent', None)
    if favorite_keys is None and hasattr(owner, '_favorite_keys'):
        favorite_keys = set(owner._favorite_keys)
    suspend = getattr(owner, '_suspend_for_playback', None)
    if callable(suspend):
        suspend()
    if getattr(item, 'content_type', '') == 'catchup' or bool(getattr(client, 'async_playback_resolution', False)):
        return _AsyncPlaybackResolver(session, client, item, items, selected_index, owner=owner, favorite_store=favorite_store, favorite_parent=favorite_parent, favorite_keys=favorite_keys).start()
    try:
        url = client.playback_url(item)
        return _launch_extplayer(session, client, item, url, items, selected_index, favorite_store=favorite_store, favorite_parent=favorite_parent, favorite_keys=favorite_keys, owner=owner)
    except _DownloadError as error:
        _cancel_download_pending(client)
        session.open(MessageBox, _(str(error)), MessageBox.TYPE_INFO)
        return

class GTPlayerInfoOverlay(Screen):
    """Non-modal high-z OSD rendered independently from ExtEplayer3."""

    def __init__(self, session, live=True, fullscreen_osd_trace=None):
        self._infobar_live = bool(live)
        self._youtube_mode = False
        self.skin = scale_skin_fonts(prepared_skin(_player_info_skin, self._infobar_live))
        Screen.__init__(self, session)
        self._fullscreen_osd_trace = fullscreen_osd_trace
        self.onLayoutFinish.append(self._trace_osd_layout)
        self.onShow.append(self._trace_osd_show)
        self.onHide.append(self._trace_osd_hide)
        if self._infobar_live:
            self.onLayoutFinish.append(self._disable_live_animation)
        self['info_background'] = Pixmap()
        self['top_line'] = Label('')
        self['bottom_line'] = Label('')
        self['picon_frame'] = Label('')
        self['picon_inner'] = Label('')
        self['picon_placeholder'] = Label('')
        self['picon_placeholder'].hide()
        self['picon'] = Pixmap()
        self['picon'].hide()
        self['youtube_logo'] = Pixmap()
        self['youtube_logo'].hide()
        self['youtube_number_mask'] = Label('')
        self['youtube_number_mask'].hide()
        self['number_badge_frame'] = Label('')
        self['number_badge_inner'] = Label('')
        self['channel_number'] = Label('')
        self['number_divider'] = Label('')
        self['channel_name'] = Label('')
        self['engine'] = Label(_('Player'))
        self['web_shortcut_hint'] = Label('')
        self['web_shortcut_hint'].hide()
        self['web_shortcut_background'] = Label('')
        for name in ('account_source', 'info_chip_background', 'stream_bitrate_frame', 'stream_bitrate', 'audio_language', 'subtitle_status', 'epg_now_time', 'epg_next_time', 'player_progress_start', 'player_progress_end', 'shortcut_separator', 'shortcut_epg_key', 'shortcut_epg', 'shortcut_audio_dot', 'shortcut_audio', 'shortcut_aspect_dot', 'shortcut_aspect', 'shortcut_web_key'):
            self[name] = Label('')
        self['shortcut_web_key'].hide()
        self.onLayoutFinish.append(self._fit_shortcut_captions)
        self.onShown.append(self._fit_shortcut_captions)
        self.onLayoutFinish.append(self._apply_youtube_layout)
        self.onShown.append(self._apply_youtube_layout)
        self['resolution'] = Label(_('Reading resolution'))
        self['fps'] = Label('-- FPS')
        self['video_codec'] = Label(_('VIDEO: --'))
        self['audio_codec'] = Label(_('AUDIO: --'))
        self['epg_separator'] = Label('')
        self['epg_now_label'] = Label(_('NOW'))
        self['epg_now'] = Label(_('Loading EPG...'))
        self['epg_next_label'] = Label(_('NEXT'))
        self['epg_next'] = Label('')
        self['epg_bottom_separator'] = Label('')
        self['player_progress_bg'] = Label('')
        self['player_progress_fill'] = Label('')
        self['player_progress_text'] = Label('')
        self['weather_divider'] = Label('')
        self['weather_card'] = Label('')
        self['favorite_badge'] = Label(localized_upper(_('Favorite')))
        self['favorite_badge'].hide()
        self['weather_icon'] = Pixmap()
        self['weather_city'] = Label(localized_upper(DEFAULT_CITY))
        self['weather_temp'] = Label('--°C')
        self['weather_desc'] = Label(_('Loading weather data'))
        self['clock_divider'] = Label('')
        self['clock'] = Label('--:--')
        self['clock_seconds'] = Label(':--')
        self['date'] = Label('')
        self['seek_panel'] = Label('')
        self['seek_state'] = Label('')
        self['seek_progress_bg'] = Label('')
        self['seek_progress_fill'] = Label('')
        self['seek_times'] = Label('')
        self['seek_hint'] = Label('')
        for name in ('seek_panel', 'seek_state', 'seek_progress_bg', 'seek_progress_fill', 'seek_times', 'seek_hint'):
            self[name].hide()
        self._device_usage_sampler = DeviceUsageSampler()
        for prefix, kind in (('cpu', 'CPU'), ('ram', 'RAM')):
            self[prefix + '_gauge'] = DeviceUsageGauge()
            self[prefix + '_usage'] = Label(usage_text(kind, None))
        self['audio_language'].hide()
        self['subtitle_status'].hide()
        self.onShow.append(self.refresh_device_usage)
        self.onHide.append(self.reset_device_usage)
        self.onClose.append(self.reset_device_usage)

    def _trace_osd_layout(self):
        _trace_fullscreen_osd(self, 'infobar-layout-ready')

    def _trace_osd_show(self):
        _trace_fullscreen_osd(self, 'infobar-shown')

    def _trace_osd_hide(self):
        _trace_fullscreen_osd(self, 'infobar-hidden')

    def _disable_live_animation(self):
        _disable_live_window_animation(self)





    def reset_device_usage(self):
        self._device_usage_sampler.reset()

    def refresh_device_usage(self):
        if not self.shown or self.instance is None or getattr(self['seek_panel'], 'visible', False):
            self.reset_device_usage()
            return
        cpu, ram = self._device_usage_sampler.sample()
        unused_width, unused_height, px = _scale()
        for prefix, kind, value in (('cpu', 'CPU', cpu), ('ram', 'RAM', ram)):
            self[prefix + '_gauge'].set_value(value)
            fit_dynamic_text(self[prefix + '_usage'], usage_text(kind, value), max_lines=1, preferred_size=font_px(px, 19), min_size=max(6, px(12)))

    def set_youtube_mode(self, enabled):
        self._youtube_mode = bool(enabled)
        self._apply_youtube_layout()

    def _apply_youtube_layout(self):
        youtube = self._youtube_mode
        if youtube:
            self['youtube_number_mask'].show()
            self['channel_number'].hide()
        else:
            self['youtube_number_mask'].hide()
            self['channel_number'].show()
        unused_width, unused_height, px = _scale()
        title_x = 250 if youtube else 345
        title_right = 880 if self._infobar_live else 1185
        for name, x, y, w, h in (('channel_name', title_x, 13, title_right - title_x, 49), ('picon', 30, 74 if youtube else 53, 170, 128 if youtube else 170)):
            instance = getattr(self[name], 'instance', None)
            if instance is not None:
                instance.move(ePoint(px(x), px(y)))
                instance.resize(eSize(px(w), px(h)))

    def _fit_shortcut_captions(self):
        unused_width, unused_height, px = _scale()
        captions = {'shortcut_epg_key': 'EPG', 'shortcut_epg': _('Programme guide'), 'shortcut_audio': _('Audio / Subtitles'), 'shortcut_aspect': _('Aspect ratio')}
        for name, text in captions.items():
            fit_dynamic_text(self[name], text, max_lines=1, preferred_size=font_px(px, 20), min_size=max(6, px(12)))
        self['shortcut_audio_dot'].setText('●')
        self['shortcut_aspect_dot'].setText('●')
        if not self._infobar_live:
            for name in ('shortcut_epg_key', 'shortcut_epg', 'shortcut_aspect_dot', 'shortcut_aspect', 'epg_now_time', 'epg_next_time'):
                self[name].hide()

class GTMinuteSeekScreen(Screen):
    """Enter an integer number of minutes without replacing the video."""

    def __init__(self, session, direction, current_position=0, length=0):
        self.skin = prepared_skin(_minute_seek_skin)
        Screen.__init__(self, session)
        self.direction = 1 if direction >= 0 else -1
        self.current_position = max(0, int(current_position or 0))
        self.length = max(0, int(length or 0))
        self.digits = ''
        for name in ('panel', 'accent', 'input_bg'):
            self[name] = Label('')
        self['title'] = Label(_('SEEK FORWARD' if self.direction > 0 else 'SEEK BACKWARD'))
        self['minutes'] = Label('')
        self['unit'] = Label('')
        self['position'] = Label('')
        self['footer'] = Label('0–9  •  OK  •  EXIT / ' + _('Cancel'))
        digits = {str(number): lambda value=str(number): self.enter_digit(value) for number in range(10)}
        digits.update({'ok': self.confirm, 'cancel': self.cancel, 'back': self.cancel, 'red': self.cancel, 'green': self.confirm, 'deleteBackward': self.backspace, 'backspace': self.backspace})
        self['actions'] = ActionMap(['NumberActions', 'OkCancelActions', 'ColorActions', 'TextEditActions'], digits, -1)
        self._refresh()

    @staticmethod
    def _time_text(seconds):
        hours, remainder = divmod(max(0, int(seconds)), 3600)
        minutes, seconds = divmod(remainder, 60)
        return '{:02d}:{:02d}:{:02d}'.format(hours, minutes, seconds)

    def _refresh(self):
        minutes = int(self.digits or '0')
        target = self.current_position + self.direction * minutes * 60
        if self.length > 0:
            target = min(target, self.length)
        target = max(0, target)
        self['minutes'].setText(self.digits or '0')
        self['unit'].setText(_('{} minutes').format('').strip())
        self['position'].setText('{}  →  {}'.format(self._time_text(self.current_position), self._time_text(target)))

    def enter_digit(self, digit):
        if len(self.digits) < 4:
            self.digits = (self.digits + str(digit)).lstrip('0') or '0'
            self._refresh()

    def backspace(self):
        self.digits = self.digits[:-1]
        self._refresh()

    def confirm(self):
        minutes = int(self.digits or '0')
        self.close(minutes if minutes > 0 else None)

    def cancel(self):
        self.close(None)

class GTExternalPlayerScreen(VodSeekSupport, _LiveStartupRecoverySupport, _FavoriteSupport, Screen):

    def __init__(self, session, reference, item, client=None, items=None, selected_index=0, keep_playing_on_exit=False, weather_settings_loader=None, weather_service=None, resume_store=None, resume_key_value='', start_position=0, live_snapshot=None, favorite_store=None, favorite_parent=None, favorite_keys=None, adopt_playing_service=False, old_reference_override=None, live_startup_recovery=None, playback_owner=None, archive_initial_playback=None, stream_diag_play_id='', stream_diag_started_at=0.0, web_return_player=None, live_hidden_category_ids=None, live_category_id='', live_list_context=None, live_source_categories=None, fullscreen_osd_trace=None):
        self.skin = prepared_skin(_player_skin)
        Screen.__init__(self, session)
        self._fullscreen_osd_trace = fullscreen_osd_trace
        _trace_fullscreen_osd(self, 'player-construct')
        self.reference = reference
        self._live_patch_code = UnlockCode()
        self._live_patch_code_keys = set()
        self._live_patch_code_timer = eTimer()
        _connect_timer(self._live_patch_code_timer, self._flush_live_patch_code)
        self._web_return_player = web_return_player
        self._web_suspended = None
        self._subtitle_play_transition = False
        self._resume_web_return = False
        self._web_resume_subtitle = None
        self._web_resume_paused = False
        self.client = client
        self._favorite_parent = favorite_parent
        self._init_favorite_support(favorite_store, favorite_keys)
        self.keep_playing_on_exit = bool(keep_playing_on_exit)
        self._adopt_playing_service = bool(adopt_playing_service)
        self._old_reference_override = old_reference_override
        del live_startup_recovery
        self._live_startup_recovery = None
        self._playback_owner = playback_owner
        self._catalog_player_base = int(getattr(playback_owner, '_movie_window_base', 0))
        self._catalog_player_request = None
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
        self._entries = items if getattr(items, 'full_category', False) else list(items or [])
        self._live_recall_base_count = len(self._entries)
        self._live_recall_extra_keys = set()
        self._live_recall_hidden_category_ids = live_hidden_category_ids
        self._live_recall_category_id = str(live_category_id or '')
        self._live_list_context = live_list_context
        self._live_source_categories = live_source_categories
        self.current_item = item if hasattr(item, 'name') else None
        self.current_index = 0
        if self._entries:
            try:
                requested_index = int(selected_index)
            except (TypeError, ValueError):
                requested_index = 0
            self.current_index = requested_index % len(self._entries)
            self.current_item = self._entries[self.current_index]
        self.title = getattr(item, 'name', str(item or 'GT IPTV'))
        self.old_reference = None
        self._started = False
        self._closed = False
        try:
            self._infobar_timeout = normalize_infobar_timeout(load_player_settings().infobar_timeout)
        except Exception:
            self._infobar_timeout = DEFAULT_INFOBAR_TIMEOUT
        self._youtube_end_watcher = None
        self._fullscreen_close_result = None
        self._front_panel_summary = None
        self._generation = 0
        self._live_snapshot = dict(live_snapshot) if isinstance(live_snapshot, dict) else {}
        self._live_snapshot_events = []
        self._old_reference_restored = False
        self._info_visible = False
        self._handoff_first_show_pending = bool(self._adopt_playing_service and self._is_live_item())
        self._handoff_info_requested = False
        self._handoff_info_defer_metadata = False
        if self._handoff_first_show_pending:
            self.onShow.append(self._complete_handoff_first_show)
        self._network_rate_sampler = NetworkRateSampler()
        self._picon_visible = False
        self._cached_picon_generation = -1
        self._picon_request_generation = -1
        self._picon_request_token = 0
        self._live_picon_retry = None
        self._live_info_ready_samples = 0
        self._live_play_started_at = 0.0
        candidate_id = str(stream_diag_play_id or '').lower()
        self._stream_diag_play_id = candidate_id if re.fullmatch('[0-9a-f]{10}', candidate_id) else ''
        try:
            self._stream_diag_started_at = float(stream_diag_started_at or 0.0)
        except (TypeError, ValueError, OverflowError):
            self._stream_diag_started_at = 0.0
        self._stream_diag_probe = None
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
        self._weather_service = weather_service or shared_weather_service()
        self._weather_forecast = None
        self._weather_error = ''
        self._weather_ready = False
        self._weather_loading = False
        self._weather_started = False
        self._weather_retry_after = 0.0
        screen_width, screen_height, px = _scale()
        self._progress_width, self._progress_height = _player_info_progress_geometry(screen_width, px)
        self._info_channel_preferred_font = font_px(px, 32, role='body')
        self._info_channel_min_font = font_px(px, 20, role='body', value='standard')
        self._info_epg_now_preferred_font = font_px(px, 36, role='body')
        self._info_epg_now_min_font = font_px(px, 26, role='body', value='standard')
        self._info_epg_next_preferred_font = font_px(px, 26, role='body')
        self._info_epg_next_min_font = font_px(px, 19, role='body', value='standard')
        self._seek_width = max(1, screen_width - px(160))
        self._seek_height = max(1, px(14))
        self._seeking = False
        self._seek_target = 0
        self._seek_length = 0
        self._pending_seek_verification = None
        self._seek_ready = False
        self._seek_startup_deadline = 0.0
        self._paused = False
        self._blue_long_last_at = 0.0
        self._suppress_next_blue_short_until = 0.0
        self._audio_menu_open = False
        self._media_menu_open = False
        self._aspect_menu_open = False
        self._minute_seek_open = False
        self._held_direction = None
        self._held_direction_long = False
        self._player_key_callback = self._handle_player_key
        self._player_keys_bound = False
        self._player_key_map = None
        self._player_key_ids = {}
        self._subtitle_sync_overlay = None
        self._subtitle_sync_keys = SubtitleSyncKeys(self._subtitle_sync_context, self._adjust_subtitle_sync, finished=self._persist_subtitle_sync)
        self._live_zero_key = LiveZeroKey(eTimer, self._live_zero_is_active, self.recall_previous_live_channel, self.restart_live_stream, prime_native=self._prime_live_zero_key)
        self._short_direction_is_seek = True
        self._subtitle_menu_open = False
        self._subtitle_menu_generation = None
        self._subtitle_message_tokens = set()
        self._subtitle_message_serial = 0
        self._subtitle_auto_generation = -1
        self._resume_store = resume_store
        self._resume_key = str(resume_key_value or '')
        try:
            self._resume_start_position = max(0, int(start_position or 0))
        except (TypeError, ValueError, OverflowError):
            self._resume_start_position = 0
        resume_path = getattr(self._resume_store, 'path', '')
        self._subtitle_resume_store = SubtitleResumeStore(os.path.join(os.path.dirname(resume_path), 'gtiptvplayerpro-subtitle-cache')) if isinstance(resume_path, str) and resume_path else SubtitleResumeStore()
        self._resume_subtitle_pending_key = self._resume_key if self._resume_start_position > 0 and (not bool(getattr(self.current_item, 'youtube_native', False))) else ''
        self._resume_subtitle_restore_attempts = 0
        self._resume_attempts = 0
        self._resume_last_length = -1
        self._resume_stable_length_samples = 0
        self._resume_saved = False
        self._episode_watcher = EpisodeCompletionWatcher(self, eof_event=getattr(_PlayableServiceEvents, 'evEOF', None))
        self._active_live_service_type = reference_service_type(self.reference, 4097)
        self._live_reconnecting = False
        self._reconnect_generation = -1
        self._reconnect_link_pending = False
        self._reconnect_link_ready = False
        self._reconnect_link_url = ''
        self._reconnect_link_deadline = 0.0
        self._reconnect_link_token = None
        self._reconnect_reason = ''
        self._reconnect_decoder_released = False
        self._reconnect_pending_reference = None
        self._reconnect_queued = False
        self._reconnect_link_lock = threading.RLock()
        self._pending_channel_index = None
        self._channel_link_lock = threading.RLock()
        self._channel_link_pending = False
        self._channel_link_ready = False
        self._channel_link_url = ''
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
        self._engine_link_url = ''
        self._engine_link_deadline = 0.0
        self._engine_link_token = None
        self._engine_decoder_released = False
        self._engine_switch_resume_position = 0
        self._weather_token = None
        self._picon_worker_token = None
        self._info_dialog = self.session.instantiateDialog(GTPlayerInfoOverlay, live=self._is_live_item(), fullscreen_osd_trace=self._fullscreen_osd_trace)
        self._info_dialog.hide()
        self._subtitle_controller = MovieSubtitleController(self.session, on_message=self._show_subtitle_message, on_changed=self._subtitle_changed, title_provider=self._subtitle_search_metadata, position_provider=self._subtitle_position, is_paused=lambda: self._paused, pause_for_search=self._pause_for_subtitle_search, resume_after_search=self._resume_after_subtitle_search, on_menu_open=self._subtitle_menu_opened, on_menu_close=self._subtitle_menu_closed, native_scope=self._native_subtitle_scope_active)
        self._hide_timer = eTimer()
        self._metadata_timer = eTimer()
        self._picon_timer = eTimer()
        self._clock_timer = eTimer()
        self._infobar_telemetry_timer = eTimer()
        self._weather_timer = eTimer()
        self._live_info_timer = eTimer()
        self._playback_timer = eTimer()
        self._resume_timer = eTimer()
        self._direction_hold_timer = eTimer()
        self._seek_verify_timer = eTimer()
        self._subtitle_auto_timer = eTimer()
        self._reconnect_timer = eTimer()
        self._channel_switch_timer = eTimer()
        self._archive_portal_timer = eTimer()
        _connect_timer(self._hide_timer, self.hide_info)
        _connect_timer(self._metadata_timer, self._metadata_tick)
        _connect_timer(self._picon_timer, self._picon_tick)
        _connect_timer(self._clock_timer, self._clock_tick)
        _connect_timer(self._infobar_telemetry_timer, self._refresh_infobar_telemetry)
        _connect_timer(self._weather_timer, self._poll_player_weather)
        _connect_timer(self._live_info_timer, self._live_info_tick)
        _connect_timer(self._playback_timer, self._playback_tick)
        _connect_timer(self._resume_timer, self._resume_tick)
        _connect_timer(self._direction_hold_timer, self._direction_hold_timeout)
        _connect_timer(self._seek_verify_timer, self._verify_seek_result)
        _connect_timer(self._subtitle_auto_timer, self._start_automatic_subtitle_search)
        _connect_timer(self._reconnect_timer, self._complete_live_restart)
        self._engine_switch_timer = eTimer()
        _connect_timer(self._engine_switch_timer, self._complete_live_engine_switch)
        _connect_timer(self._channel_switch_timer, self._commit_channel_switch)
        _connect_timer(self._archive_portal_timer, self._archive_portal_keepalive)
        exit_action = self.return_to_list if self.keep_playing_on_exit else self.stop_and_close
        player_contexts = ['OkCancelActions', 'ColorActions', 'MoviePlayerActions', 'MediaPlayerActions', 'DirectionActions', 'NumberActions', 'InfobarEPGActions', 'TVRadioActions']
        player_actions = {'cancel': self.cancel_or_exit, 'red': self._toggle_stalker_trial_osd, 'blue': self.toggle_current_favorite, 'stop': self.stop_and_close, 'ok': self.ok_or_confirm_seek, 'playpauseService': self.toggle_pause, 'pauseService': self.pause_playback, 'unPauseService': self.resume_playback, 'play': self.resume_playback, 'pause': self.pause_playback, 'right': self.seek_right_or_info, 'left': self.seek_left_or_info, 'up': self.open_live_channel_list, 'down': self.open_live_channel_list, 'channelUp': self.next_channel, 'channelDown': self.previous_channel, '0': lambda: self._live_patch_digit_action('0', self.recall_previous_live_channel), '1': lambda: self._live_patch_digit_action('1', self.cycle_player_service_type), '4': lambda: self._live_patch_digit_action('4', self.subtitle_sync_minus), '5': lambda: self._live_patch_digit_action('5', self.subtitle_sync_reset), '6': lambda: self._live_patch_digit_action('6', self.subtitle_sync_plus), 'info': self.open_epg_guide, 'tv': self.toggle_info, 'showEventInfo': self.open_epg_guide, 'showEventView': self.open_epg_guide, 'showEventInfoSingleEPG': self.open_epg_guide, 'showEventGuidePlugin': self.open_epg_guide, 'showEventInfoPlugin': self.open_epg_guide, 'EPGPressed': self.open_epg_guide, 'InfoPressed': self.open_epg_guide, 'IPressed': self.open_epg_guide, 'keyTV': self.toggle_info}
        content_type = getattr(self.current_item, 'content_type', '')
        if content_type in ('movie', 'series'):
            player_actions['blue'] = self.ignore_vod_blue
            player_actions['bluelong'] = self.ignore_vod_blue
            player_actions['blue_long'] = self.ignore_vod_blue
        elif content_type == 'catchup':
            player_actions['blue'] = self.open_audio_selection
            player_actions['bluelong'] = self.toggle_favorite_with_blue_long
            player_actions['blue_long'] = self.toggle_favorite_with_blue_long
        if not self._is_live_item():
            player_contexts[2:2] = ['InfobarSeekActions', 'InfobarSeekActionsPTS']
            player_actions.update({'seekFwd': self.seek_forward, 'seekBack': self.seek_backward, 'SeekbarFwd': self.begin_seek_mode, 'SeekbarBack': self.begin_seek_mode, 'seekFwdManual': self.begin_seek_mode, 'seekBackManual': self.begin_seek_mode, 'fastForward': self.seek_forward, 'fastforward': self.seek_forward, 'rewind': self.seek_backward, 'rightRepeated': self.seek_right_or_info, 'leftRepeated': self.seek_left_or_info})
        if content_type == 'live':
            for digit in ('2', '3', '7', '8', '9'):
                player_actions[digit] = lambda value=digit: self._live_patch_digit_action(value)
            player_contexts.append('InfobarSubtitleSelectionActions')
            player_actions.update({'yellow': self.open_media_selection, 'green': self.open_aspect_selection, 'subtitleSelection': self.open_subtitle_selection})
        elif content_type in ('movie', 'series', 'catchup'):
            player_contexts.append('InfobarSubtitleSelectionActions')
            player_actions.update({'yellow': self.open_media_selection if content_type in ('movie', 'series') else self.open_subtitle_selection, 'subtitleSelection': self.open_subtitle_selection})
        self['actions'] = ActionMap(player_contexts, player_actions, -1)
        self.onExecBegin.append(self._bind_player_key_handler)
        self.onExecEnd.append(self._unbind_player_key_handler)
        if self._adopt_playing_service and self._is_live_item():
            self.onLayoutFinish.append(self._disable_handoff_animation)
        if hasattr(self, 'onShown'):
            self.onShown.append(self.start_playback)
        if hasattr(self, 'onClose'):
            self.onClose.append(self._on_close)
        self._clock_tick()
        self._render_player_weather_waiting()
        self._clear_player_progress()
        if not (self._adopt_playing_service and self._is_live_item()):
            self._render_channel()
            if getattr(self.current_item, 'content_type', '') == 'live':
                self._apply_live_snapshot()
        self.setTitle(self.title)
        self._exit_action = exit_action

    def _complete_handoff_first_show(self):
        _trace_fullscreen_osd(self, 'player-shown')
        if not self._handoff_first_show_pending:
            return
        self._handoff_first_show_pending = False
        requested = self._handoff_info_requested
        self._handoff_info_requested = False
        if self._closed or not requested:
            return
        previous = getattr(self, '_defer_live_metadata_once', False)
        self._defer_live_metadata_once = self._handoff_info_defer_metadata
        try:
            self.show_info()
        finally:
            self._defer_live_metadata_once = previous

    def _disable_handoff_animation(self):
        _disable_live_window_animation(self)

    def _live_patch_unlock_allowed(self):
        return bool(not self._closed and self._is_live_item() and supports_live_patch(self.client) and (getattr(self.session, 'current_dialog', None) is self) and (getattr(self, '_web_suspended', None) is None))

    def _reset_live_patch_code(self):
        self._live_patch_code_timer.stop()
        self._live_patch_code.reset()
        self._live_patch_code_keys.clear()

    def _replay_live_patch_digits(self, digits):
        actions = {'0': self.recall_previous_live_channel, '1': self.cycle_player_service_type, '4': self.subtitle_sync_minus, '5': self.subtitle_sync_reset, '6': self.subtitle_sync_plus}
        for digit in digits:
            action = actions.get(digit)
            if action is not None:
                action()

    def _flush_live_patch_code(self):
        self._live_patch_code_timer.stop()
        digits = self._live_patch_code.reset()
        if self._live_patch_unlock_allowed():
            self._replay_live_patch_digits(digits)

    def _live_patch_digit_action(self, digit, ordinary_action=None):
        if not self._live_patch_unlock_allowed() or live_patch_enabled() or (not self._live_patch_code.pending and digit != '4'):
            if ordinary_action is not None:
                ordinary_action()
            return
        matched, released = self._live_patch_code.feed(digit)
        self._live_patch_code_timer.stop()
        self._replay_live_patch_digits(released)
        if matched:
            if self._started and (not self._live_transition_in_progress()) and (getattr(self.session, 'nav', None) is not None) and activate_live_patch():
                log_event('live_patch', 'enabled providers=stalker,xtream,m3u')
                self._restart_live_stream()
        elif self._live_patch_code.pending:
            self._live_patch_code_timer.start(int(UnlockCode.timeout * 1000), True)

    def _handle_live_patch_key(self, key, flag):
        if not self._live_patch_unlock_allowed():
            return False
        if key in self._live_patch_code_keys:
            if flag == 1:
                self._live_patch_code_keys.discard(key)
                return True
            if flag in (2, 3):
                return True
        if live_patch_enabled():
            return False
        for digit in '0123456789':
            if key == self._player_key_ids.get('KEY_' + digit, -1):
                if flag == 0 and (self._live_patch_code.pending or digit == '4'):
                    self._live_patch_code_keys.add(key)
                    self._live_patch_digit_action(digit)
                    return True
                return False
        if flag == 0:
            self._reset_live_patch_code()
        return False

    def _toggle_stalker_trial_osd(self):
        from .live_patch import toggle_osd
        if self._is_live_item() and toggle_osd(self):
            return
        self.stop_and_close()

    def _native_subtitle_scope_active(self):
        """Keep image autoselection out of GT movies, including child dialogs."""
        return bool(not self._closed and getattr(self.current_item, 'content_type', '') in ('movie', 'series') and (not bool(getattr(self.current_item, 'youtube_native', False))) and (self._web_suspended is None or self._subtitle_play_transition))

    def _play_subtitle_service(self, navigation, reference):
        """Protect the pending VOD reference before native startup callbacks."""
        previous_transition = self._subtitle_play_transition
        self._subtitle_play_transition = True
        try:
            controller = getattr(self, '_subtitle_controller', None)
            if controller is not None:
                try:
                    controller.prepare_native_service(reference)
                except Exception as error:
                    _debug('native subtitle guard failed error={}'.format(error.__class__.__name__))
            return _play_service(navigation, reference)
        finally:
            self._subtitle_play_transition = previous_transition

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
            _debug('front panel refresh failed: {}'.format(error.__class__.__name__))

    def _is_live_item(self):
        return getattr(self.current_item, 'content_type', '') == 'live'

    def _player_stream_diag(self, stage, detail='', play_id=None, started_at=None):
        if not self._is_live_item() or not _is_stalker_client(self.client):
            return
        if not play_id and (not self._stream_diag_play_id):
            self._stream_diag_play_id = uuid.uuid4().hex[:10]
        if not started_at and (not self._stream_diag_started_at):
            self._stream_diag_started_at = time.monotonic()
        play_id = play_id or self._stream_diag_play_id
        started_at = started_at or self._stream_diag_started_at
        elapsed_ms = max(0, int((time.monotonic() - started_at) * 1000))
        log_event('stream_diag', 'play_id={} phase=player engine={} elapsed_ms={} stage={} {} geo=unknown'.format(play_id, reference_service_type(self.reference, 4097), elapsed_ms, stage, detail))

    def _stop_player_stream_diag(self):
        probe = getattr(self, '_stream_diag_probe', None)
        self._stream_diag_probe = None
        if probe is not None:
            probe.stop()

    def _new_player_stream_diag(self, reason):
        if not self._is_live_item() or not _is_stalker_client(self.client):
            return
        self._stop_player_stream_diag()
        self._stream_diag_play_id = uuid.uuid4().hex[:10]
        self._stream_diag_started_at = time.monotonic()
        self._player_stream_diag('request', 'reason={}'.format(reason))

    def _watch_player_stream_diag(self, navigation, reference, reason):
        if not self._is_live_item() or not _is_stalker_client(self.client):
            return
        self._stop_player_stream_diag()
        self._player_stream_diag('service-active', 'outcome=accepted reason={}'.format(reason))
        from .browser_live import _StalkerStreamProbe
        self._stream_diag_probe = _StalkerStreamProbe(navigation, reference, self._stream_diag_play_id, started_at=self._stream_diag_started_at, phase='player')
        self._stream_diag_probe.start()

    def _is_archive_item(self):
        return getattr(self.current_item, 'content_type', '') == 'catchup'

    def _archive_portal_keepalive(self):
        """Keep only an explicitly opened Stalker archive session active."""
        if self._closed or not self._started or (not self._is_archive_item()):
            return
        if str(getattr(self.client, 'source_type', '') or '').lower() != 'stalker':
            return
        sender = getattr(self.client, 'archive_playback_keepalive', None)
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
                succeeded = bool(sender(item, initial=initial, cancel_event=token.cancel_event, deadline=token.deadline))
            except Exception as caught:
                error = caught
            if succeeded and token.active() and (not self._closed) and (self._generation == generation) and (self.current_item is item):
                self._archive_portal_initialized = True
                log_event('tv_archive', 'Stalker session signal outcome=ok initial={}'.format('yes' if initial else 'no'))
            elif error is not None and token.active():
                log_event('tv_archive', 'Stalker session signal outcome=failed initial={}'.format('yes' if initial else 'no'), error)
        worker = threading.Thread(target=run)
        worker.daemon = True
        try:
            worker.start()
        except Exception as error:
            _cancel_worker_token(token)
            self._archive_portal_keepalive_token = None
            log_event('tv_archive', 'Stalker session signal outcome=failed initial={}'.format('yes' if initial else 'no'), error)
            return
        self._archive_portal_timer.start(ARCHIVE_PORTAL_KEEPALIVE_SECONDS * 1000, True)

    def _stop_archive_portal_keepalive(self):
        try:
            self._archive_portal_timer.stop()
        except Exception:
            pass
        _cancel_worker_token(self._archive_portal_keepalive_token)
        self._archive_portal_keepalive_token = None

    def _arm_archive_startup(self):
        """Start one catchup-only monitor without replacing an active retry."""
        if self._closed or not self._started or (not self._is_archive_item()):
            return
        watchdog = self._archive_startup_watchdog
        if watchdog is not None and watchdog.phase not in ('idle', 'acquired', 'failed', 'cancelled', 'inconclusive'):
            return
        if watchdog is None or watchdog.phase != 'idle':
            watchdog = _ArchivePlaybackWatchdog(self)
            self._archive_startup_watchdog = watchdog
        watchdog.start()

    def _cancel_archive_startup(self):
        watchdog = self._archive_startup_watchdog
        self._archive_startup_watchdog = None
        if watchdog is not None:
            watchdog.cancel()

    def _complete_archive_startup_failure(self):
        timer = getattr(self, '_archive_terminal_timer', None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        owner = self._playback_owner
        self._playback_owner = None
        callback = getattr(owner, '_resume_after_playback_resolution_failure', None)
        if callable(callback) and (not getattr(owner, '_closed', False)):
            try:
                callback()
            except Exception:
                pass
        self.close()

    def _archive_terminal_release(self):
        """Retry one failed decoder release/old-service restore, then stop."""
        if getattr(self, '_closed', False):
            return
        attempts = int(getattr(self, '_archive_terminal_attempts', 0)) + 1
        self._archive_terminal_attempts = attempts
        released = bool(self.stop_playback())
        old_reference = getattr(self, 'old_reference', None)
        needs_restore = bool(old_reference is not None and (not getattr(self, '_old_reference_restored', False)))
        if attempts > 1 and released and needs_restore:
            navigation = getattr(getattr(self, 'session', None), 'nav', None)
            released = bool(self._restore_old_service(navigation))
            needs_restore = not getattr(self, '_old_reference_restored', False)
        if released and (not needs_restore):
            self._complete_archive_startup_failure()
            return
        if attempts < 2:
            timer = getattr(self, '_archive_terminal_timer', None)
            if timer is None:
                timer = eTimer()
                self._archive_terminal_timer = timer
                _connect_timer(timer, self._archive_terminal_release)
            timer.start(ARCHIVE_DECODER_RELEASE_MS, True)
            return
        if needs_restore:
            self._started = True
        self._set_static_info_epg('epg_next', _('Keeping the current stream'))
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
        return fit_dynamic_text(self._info_dialog['channel_name'], value, max_lines=1, preferred_size=self._info_channel_preferred_font, min_size=self._info_channel_min_font, fallback_chars=42)

    def _set_info_epg(self, name, value):
        clock, value = split_epg_text(value) if self._is_live_item() else ('', value)
        clock_name = 'epg_next_time' if name == 'epg_next' else 'epg_now_time'
        set_text_if_changed(self._info_dialog[clock_name], clock)
        if name == 'epg_next':
            preferred = self._info_epg_next_preferred_font
            minimum = self._info_epg_next_min_font
        else:
            preferred = self._info_epg_now_preferred_font
            minimum = self._info_epg_now_min_font
        return fit_dynamic_text(self._info_dialog[name], value, max_lines=1, preferred_size=preferred, min_size=minimum, fallback_chars=54)

    def _set_static_info_channel_name(self, value):
        return set_static_text(self._info_dialog['channel_name'], value, preferred_size=self._info_channel_preferred_font)

    def _set_static_info_epg(self, name, value):
        clock_name = 'epg_next_time' if name == 'epg_next' else 'epg_now_time'
        set_text_if_changed(self._info_dialog[clock_name], '')
        preferred = self._info_epg_next_preferred_font if name == 'epg_next' else self._info_epg_now_preferred_font
        minimum = self._info_epg_next_min_font if name == 'epg_next' else self._info_epg_now_min_font
        return fit_dynamic_text(self._info_dialog[name], value, max_lines=1, preferred_size=preferred, min_size=minimum, fallback_chars=54)

    def _live_network_silent(self):
        return self._is_live_item()

    def _set_info_chip(self, name, value):
        unused_width, unused_height, px = _scale()
        return fit_dynamic_text(self._info_dialog[name], value, max_lines=1, preferred_size=font_px(px, 19), min_size=max(6, px(12)))

    def _refresh_resolution_info(self, service=None):
        tracker = getattr(self, '_resolution_tracker', None)
        if tracker is None:
            tracker = self._resolution_tracker = ResolutionTracker()
        navigation = getattr(self.session, 'nav', None)
        active = bool(self._started and (not self._closed) and (getattr(self, '_web_suspended', None) is None) and (getattr(self, '_pending_engine_reference', None) is None) and (not self._live_transition_in_progress()))
        try:
            from .browser_live import GTStreamListScreen
            active = active and GTStreamListScreen._same_service_reference(navigation.getCurrentlyPlayingServiceReference(), self.reference)
        except Exception:
            active = False
        if not active:
            tracker.reset()
            self._set_info_chip('resolution', '--')
            return None
        try:
            if service is None:
                service = navigation.getCurrentService()
            reference = self.reference.toString()
        except Exception:
            service, reference = (None, '')
        snapshot = tracker.sample(service, iServiceInformation, (self._generation, reference), item=self.current_item)
        self._set_info_chip('resolution', resolution_text(snapshot, compact=_scale()[0] <= 720))
        return snapshot

    def _refresh_infobar_telemetry(self):
        """Sample receiver RX and local metadata only while the banner is visible."""
        timer = getattr(self, '_infobar_telemetry_timer', None)
        dialog = getattr(self, '_info_dialog', None)
        seek_panel = dialog.get('seek_panel') if dialog is not None else None
        if self._closed or not self._info_visible or (not self._started) or (getattr(self, '_web_suspended', None) is not None) or (dialog is None) or getattr(seek_panel, 'visible', False):
            if timer is not None:
                timer.stop()
            sampler = getattr(self, '_network_rate_sampler', None)
            if sampler is not None:
                sampler.reset()
            resetter = getattr(dialog, 'reset_device_usage', None)
            if callable(resetter):
                resetter()
            return
        service = None
        transitioning = self._live_transition_in_progress()
        if not transitioning:
            navigation = getattr(self.session, 'nav', None)
            try:
                current = navigation.getCurrentlyPlayingServiceReference()
                from .browser_live import GTStreamListScreen
                if not GTStreamListScreen._same_service_reference(current, self.reference):
                    transitioning = True
            except Exception:
                pass
            if not transitioning:
                try:
                    service = navigation.getCurrentService()
                except Exception:
                    pass
        rate = self._network_rate_sampler.sample()
        self._refresh_resolution_info(service)
        screen_width, unused_height, px = _scale()
        values = {'stream_bitrate': network_rate_text(rate, compact=screen_width <= 720)}
        for name, value in values.items():
            fit_dynamic_text(self._info_dialog[name], value, max_lines=1, preferred_size=font_px(px, 20), min_size=max(6, px(12)))
        updater = getattr(dialog, 'refresh_device_usage', None)
        if callable(updater):
            updater()
        self._clock_tick()
        if self._is_live_item() and (not transitioning):
            self._render_live_snapshot_epg(now=time.time())
        if timer is not None:
            timer.start(1000, True)

    def _live_snapshot_for_current_item(self):
        """Return only the list-prepared data belonging to the active channel."""
        if not self._is_live_item() or not self._live_snapshot:
            return {}
        stream_id = str(getattr(self.current_item, 'stream_id', '') or '')
        snapshot = dict(self._live_snapshot)
        common = {key: snapshot[key] for key in ('weather', 'weather_forecast', 'captured_at') if key in snapshot}
        channels = snapshot.pop('channels', None)
        if isinstance(channels, dict):
            selected = channels.get(stream_id)
            if not isinstance(selected, dict):
                return common
            # Only weather belongs to the whole screen. Merging a partial B
            # entry over the top-level A snapshot would borrow A's picon/EPG
            # and incorrectly mark that artwork as B's completed logo.
            snapshot = dict(common)
            snapshot.update(selected)
            snapshot.setdefault('stream_id', stream_id)
        snapshot_stream_id = str(snapshot.get('stream_id', '') or '')
        if not stream_id or snapshot_stream_id != stream_id:
            _debug('live snapshot rejected expected_id={} actual_id={}'.format(stream_id, snapshot_stream_id))
            return common
        return snapshot

    def _remember_live_snapshot_component(self, stream_id, component, value):
        """Keep completed live details available for later zaps in this screen."""
        stream_id = str(stream_id or '')
        if not stream_id or component not in ('events', 'picon_path'):
            return False
        channels = self._live_snapshot.get('channels')
        if not isinstance(channels, dict):
            channels = {}
            self._live_snapshot['channels'] = channels
        entry = channels.get(stream_id)
        if not isinstance(entry, dict):
            entry = {'stream_id': stream_id}
            channels[stream_id] = entry
        if component == 'events':
            entry[component] = list(value or [])
        else:
            entry[component] = str(value or '').strip()
        entry['captured_at'] = time.time()
        top_stream_id = str(self._live_snapshot.get('stream_id', '') or '')
        if top_stream_id == stream_id:
            self._live_snapshot[component] = entry[component]
            self._live_snapshot['captured_at'] = entry['captured_at']
        return True

    @staticmethod
    def _snapshot_epg_window(events, now=None):
        events = list(events or [])
        if not events:
            return []
        current_time = int(time.time() if now is None else now)
        selected_index = None
        for index, event in enumerate(events):
            start = int(getattr(event, 'start_timestamp', 0) or 0)
            end = int(getattr(event, 'end_timestamp', 0) or 0)
            if start and end and (start <= current_time < end):
                selected_index = index
                break
        if selected_index is None:
            for index, event in enumerate(events):
                start = int(getattr(event, 'start_timestamp', 0) or 0)
                end = int(getattr(event, 'end_timestamp', 0) or 0)
                if end and end > current_time or (start and start >= current_time):
                    selected_index = index
                    break
        if selected_index is None:
            if any((not int(getattr(event, 'end_timestamp', 0) or 0) for event in events)):
                selected_index = 0
            else:
                return []
        return events[selected_index:]

    def _render_live_snapshot_epg(self, now=None):
        """Render and advance EPG strictly from the in-memory snapshot."""
        window = self._snapshot_epg_window(self._live_snapshot_events, now=now)
        self._current_epg_events = list(window[:2])
        if not window:
            self._set_static_info_epg('epg_now', _('EPG information unavailable.'))
            self._set_static_info_epg('epg_next', '')
            self._clear_player_progress()
            self._refresh_front_panel()
            return False
        current = window[0]
        future_only = _epg_event_starts_in_future(current, now=now)
        if future_only:
            self._set_static_info_epg('epg_now', _('EPG information unavailable.'))
            self._clear_player_progress()
            self._set_info_epg('epg_next', self._format_epg(current))
        else:
            self._set_info_epg('epg_now', self._format_epg(current))
            self._set_player_progress(current, now=now)
        if not future_only and len(window) > 1:
            self._set_info_epg('epg_next', self._format_epg(window[1]))
        elif not future_only:
            self._set_static_info_epg('epg_next', '')
        self._refresh_front_panel()
        return True

    def _live_picon_status(self):
        key = (self._generation, str(getattr(self.current_item, 'stream_id', '') or ''))
        state = getattr(self, '_live_picon_retry', None)
        if state is None or state.key != key:
            state = self._live_picon_retry = LivePiconRetry(key, time.monotonic())
        return state

    def _defer_live_picon(self):
        if self._closed or not self._is_live_item() or self._live_transition_in_progress():
            return False
        state = self._live_picon_status()
        self._picon_visible = False
        self._cached_picon_generation = self._picon_request_generation = -1
        self._sync_picon_visibility()
        url = _safe_picon_url(getattr(self.current_item, 'icon', ''))
        delay = state.defer(time.monotonic(), _picon_failure_remaining(url) if url else 0.0)
        if delay is None:
            self._picon_timer.stop()
            return False
        self._picon_timer.start(delay, True)
        return True

    def _live_picon_tick(self):
        if self._closed or not self._is_live_item() or self._live_transition_in_progress():
            return
        state = self._live_picon_status()
        now = time.monotonic()
        if state.phase == 'decode':
            remaining = state.decode_deadline - now
            if remaining > 0:
                self._picon_timer.start(max(1, int(round(remaining * 1000))), True)
                return
            # A native decoder which never calls back must not permanently
            # reserve this channel's logo. Reject any late completion first.
            self._picon_request_token += 1
            loaders = getattr(self._info_dialog, '_gt_pixmap_loaders', {})
            previous = loaders.pop('player-picon', None)
            if previous is not None:
                try:
                    previous.close()
                except Exception:
                    pass
            self._defer_live_picon()
            return
        if state.phase:
            return
        if now < state.retry_at:
            if state.retry_at < state.deadline:
                self._picon_timer.start(max(1, int(round((state.retry_at - now) * 1000))), True)
            return
        if not state.ready(now):
            return
        if self._apply_live_snapshot_picon():
            return
        url = _safe_picon_url(getattr(self.current_item, 'icon', ''))
        if not url or self.client is None:
            return
        if _picon_failure_active(url):
            self._defer_live_picon()
            return
        self._live_info_queued_generation = self._generation
        elapsed_ms = max(0, int(round((now - self._live_play_started_at) * 1000)))
        started = self._start_live_info_completion(force=elapsed_ms >= LIVE_INFO_FALLBACK_MS, picon_only=True)
        if not started and state.ready(time.monotonic()):
            # A bounded EPG worker may still own the metadata delivery slot.
            self._picon_timer.start(100, True)

    def _apply_live_snapshot_picon(self, snapshot=None):
        """Decode snapshot/cache artwork without creating a network request."""
        if self._closed or not self._is_live_item() or not self._started or self._live_transition_in_progress():
            return False
        state = self._live_picon_status()
        if state.phase in ('download', 'decode'):
            return True
        if snapshot is None:
            snapshot = self._live_snapshot_for_current_item()
        if self._picon_visible and self._picon_request_generation == self._generation and self._cached_picon_generation == self._generation:
            return True
        if not state.ready(time.monotonic()):
            return False
        path = artwork_path(snapshot.get('picon_path', '') or '')
        if not path or not _valid_live_picon_file(path):
            url = _safe_picon_url(getattr(self.current_item, 'icon', ''))
            path = _cached_picon_path(url) if url else ''
            if path and _valid_live_picon_file(path):
                self._remember_live_snapshot_component(getattr(self.current_item, 'stream_id', ''), 'picon_path', path)
        if not path or not _valid_live_picon_file(path):
            return False
        self._apply_picon(self._generation, path)
        return state.phase in ('decode', 'ready')

    def _apply_live_snapshot(self):
        """Apply list-screen EPG/weather/picon without any network I/O."""
        if not self._is_live_item():
            return False
        snapshot = self._live_snapshot_for_current_item()
        events = snapshot.get('events', snapshot.get('epg_events', ()))
        try:
            self._live_snapshot_events = list(events or [])
        except TypeError:
            self._live_snapshot_events = []
        self._render_live_snapshot_epg()
        forecast = self._weather_forecast
        if forecast is None:
            forecast = snapshot.get('weather_forecast', snapshot.get('weather'))
        if isinstance(forecast, dict):
            try:
                forecast = WeatherService._from_dict(forecast)
            except Exception:
                forecast = None
        self._weather_forecast = forecast
        if forecast is not None:
            self._weather_started = True
            if not self._weather_loading:
                self._weather_ready = True
            self._render_player_weather(forecast)
        elif not self._weather_loading:
            self._render_player_weather_unavailable()
        self._apply_live_snapshot_picon(snapshot)
        return bool(snapshot)

    def _live_info_loader(self):
        loader = getattr(self.client, 'load_epg', None)
        if callable(loader):
            return loader
        loader = getattr(self.client, 'load_short_epg', None)
        return loader if callable(loader) else None

    def _live_info_missing_components(self):
        if self._closed or not self._started or (not self._is_live_item()) or (self.client is None):
            return (False, False)
        snapshot = self._live_snapshot_for_current_item()
        events = snapshot.get('events', snapshot.get('epg_events', ()))
        try:
            has_epg = bool(self._snapshot_epg_window(events))
        except TypeError:
            has_epg = False
        stream_id = str(getattr(self.current_item, 'stream_id', '') or '')
        retry_after = float(self._live_info_epg_retry_after.get(stream_id, 0.0) or 0.0)
        now_mono = time.monotonic()
        if retry_after and now_mono >= retry_after:
            self._live_info_epg_retry_after.pop(stream_id, None)
            retry_after = 0.0
        needs_epg = bool((not has_epg or self._live_info_epg_refresh_generation == self._generation) and now_mono >= retry_after and (self._live_info_loader() is not None))
        has_picon = self._apply_live_snapshot_picon(snapshot)
        picon_url = _safe_picon_url(getattr(self.current_item, 'icon', ''))
        needs_picon = False
        if not has_picon and picon_url and self._live_picon_status().ready(time.monotonic()):
            if _picon_failure_active(picon_url):
                self._defer_live_picon()
            else:
                needs_picon = True
        return (needs_epg, needs_picon)

    def _arm_live_info_fallback(self):
        if self._live_info_queued_generation != self._generation:
            return False
        elapsed_ms = max(0, int(round((time.monotonic() - self._live_play_started_at) * 1000)))
        delay_ms = max(10, LIVE_INFO_FALLBACK_MS - elapsed_ms)
        self._live_info_due_at = time.monotonic() + delay_ms / 1000.0
        self._live_info_timer.start(delay_ms, True)
        return True

    def _schedule_live_info_completion(self):
        needs_epg, needs_picon = self._live_info_missing_components()
        if not needs_epg and (not needs_picon):
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
            for token in state.get('tokens', {}).values():
                _cancel_worker_token(token)
        retry = getattr(self, '_live_picon_retry', None)
        if retry is not None and retry.phase == 'download':
            # Cancelling a metadata slot must not leave its logo permanently
            # marked as pending. A later infobar opening can resume the budget.
            retry.defer(time.monotonic())
            self._picon_request_generation = -1
        self._live_info_state = None
        self._live_info_queued_generation = -1
        self._live_info_due_at = 0.0
        self._live_info_epg_refresh_generation = -1
        with self._live_info_results_lock:
            self._live_info_results.clear()

    def _start_live_info_completion(self, force=False, picon_only=False):
        if self._closed or self._live_info_queued_generation != self._generation or (not self._is_live_item()) or self._live_transition_in_progress():
            return False
        elapsed_ms = max(0, int(round((time.monotonic() - self._live_play_started_at) * 1000)))
        decoder_ready = bool(self._live_info_ready_samples >= LIVE_INFO_READY_MIN_SAMPLES and elapsed_ms >= LIVE_INFO_SETTLE_MS)
        if not force and (not decoder_ready):
            if self._live_info_ready_samples >= LIVE_INFO_READY_MIN_SAMPLES:
                delay_ms = max(10, LIVE_INFO_SETTLE_MS - elapsed_ms)
                self._live_info_due_at = time.monotonic() + delay_ms / 1000.0
                self._live_info_timer.start(delay_ms, True)
            else:
                self._arm_live_info_fallback()
            return False
        if self._live_info_state is not None:
            self._live_info_timer.start(LIVE_INFO_POLL_MS, True)
            return False
        needs_epg, needs_picon = self._live_info_missing_components()
        if picon_only:
            needs_epg = False
        defer_stalker_epg = bool(needs_epg and str(getattr(self.client, 'source_type', '') or '') == 'stalker' and (elapsed_ms < STALKER_LIVE_EPG_QUIET_MS))
        if defer_stalker_epg:
            needs_epg = False
        stream_id = str(getattr(self.current_item, 'stream_id', '') or '')
        generation = self._generation
        state = {'generation': generation, 'stream_id': stream_id, 'events': {}, 'tokens': {}, 'applied': set()}
        workers = []
        if needs_epg:
            refresh_epg = self._live_info_epg_refresh_generation == generation
            self._live_info_epg_refresh_generation = -1
            event = threading.Event()
            token = _WorkerToken(LIVE_INFO_EPG_TIMEOUT_SECONDS)
            state['events']['events'] = event
            state['tokens']['events'] = token
            workers.append(('events', threading.Thread(target=self._load_live_info_epg_worker, args=(generation, stream_id, event, token, refresh_epg))))
        if needs_picon:
            if self._live_picon_status().begin('download', time.monotonic()):
                self._picon_request_token += 1
                self._picon_request_generation = generation
                event = threading.Event()
                token = _WorkerToken(LIVE_INFO_PICON_BUDGET)
                state['events']['picon_path'] = event
                state['tokens']['picon_path'] = token
                workers.append(('picon_path', threading.Thread(target=self._load_live_info_picon_worker, args=(generation, stream_id, self.current_item, event, token))))
        if defer_stalker_epg:
            delay_ms = max(10, STALKER_LIVE_EPG_QUIET_MS - elapsed_ms)
            self._live_info_queued_generation = generation
            self._live_info_due_at = time.monotonic() + delay_ms / 1000.0
        else:
            self._live_info_queued_generation = -1
            self._live_info_due_at = 0.0
        if not workers:
            if defer_stalker_epg:
                self._live_info_timer.start(delay_ms, True)
            return False
        self._live_info_state = state
        for component, worker in workers:
            worker.daemon = True
            try:
                worker.start()
            except (OSError, RuntimeError):
                state['tokens'][component].cancel()
                state['events'][component].set()
        self._live_info_timer.start(LIVE_INFO_POLL_MS, True)
        _debug('live info completion started id={} epg={} picon={}'.format(stream_id, needs_epg, needs_picon))
        return True

    def _publish_live_info_result(self, generation, stream_id, component, value, token):
        key = (int(generation), str(stream_id or ''), str(component or ''))
        with self._live_info_results_lock:
            state = self._live_info_state
            if self._closed or token is None or (not token.active()) or (int(generation) != self._generation) or (state is None) or (int(state.get('generation', -1)) != int(generation)) or (str(state.get('stream_id', '') or '') != key[1]) or (state.get('tokens', {}).get(key[2]) is not token) or (key[2] not in state.get('events', {})):
                return
            self._live_info_results[key] = value

    def _load_live_info_epg_worker(self, generation, stream_id, worker_event, worker_token, refresh=False):
        try:
            loader = self._live_info_loader()
            events = []
            if loader is not None:
                events = _bounded_client_call(self.client, worker_token, lambda: _load_shared_live_epg(self.client, stream_id, LIVE_EPG_FETCH_LIMIT, loader, token=worker_token, refresh=refresh))
            self._publish_live_info_result(generation, stream_id, 'events', list(events or []), worker_token)
        except _WorkerCancelled:
            pass
        except Exception:
            self._publish_live_info_result(generation, stream_id, 'events', [], worker_token)
        finally:
            worker_event.set()

    def _load_live_info_picon_worker(self, generation, stream_id, item, worker_event, worker_token):
        try:
            path = _load_live_picon_path(self.client, item, worker_token=worker_token, timeout=LIVE_INFO_PICON_TIMEOUT)
            self._publish_live_info_result(generation, stream_id, 'picon_path', path, worker_token)
        except _WorkerCancelled:
            pass
        except Exception:
            self._publish_live_info_result(generation, stream_id, 'picon_path', '', worker_token)
        finally:
            worker_event.set()

    def _apply_live_info_result(self, state, component, value):
        generation = int(state.get('generation', -1))
        stream_id = str(state.get('stream_id', '') or '')
        if generation != self._generation or stream_id != str(getattr(self.current_item, 'stream_id', '') or '') or self._live_transition_in_progress():
            return False
        if component == 'events':
            events = list(value or [])
            if events:
                self._live_info_epg_retry_after.pop(stream_id, None)
                self._remember_live_snapshot_component(stream_id, 'events', events)
                self._live_snapshot_events = events
                self._render_live_snapshot_epg(now=time.time())
                if self._info_visible:
                    self._start_info_hide_timer()
            else:
                self._live_info_epg_retry_after[stream_id] = time.monotonic() + LIVE_DETAIL_EMPTY_RETRY_SECONDS
                if len(self._live_info_epg_retry_after) > LIVE_EPG_CACHE_MAX_ENTRIES:
                    oldest = min(self._live_info_epg_retry_after, key=self._live_info_epg_retry_after.get)
                    self._live_info_epg_retry_after.pop(oldest, None)
            if not events:
                self._refresh_front_panel()
            return bool(events)
        if component == 'picon_path':
            path = str(value or '').strip()
            if not path or not _valid_live_picon_file(path):
                self._defer_live_picon()
                return False
            self._remember_live_snapshot_component(stream_id, 'picon_path', path)
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
                self._live_info_timer.start(max(10, int(round(remaining * 1000))), True)
                return
            elapsed_ms = max(0, int(round((time.monotonic() - self._live_play_started_at) * 1000)))
            self._start_live_info_completion(force=elapsed_ms >= LIVE_INFO_FALLBACK_MS)
            return
        generation = int(state.get('generation', -1))
        stream_id = str(state.get('stream_id', '') or '')
        for component, event in state.get('events', {}).items():
            token = state.get('tokens', {}).get(component)
            if not event.is_set() and token is not None and token.expired():
                token.cancel()
            key = (generation, stream_id, component)
            with self._live_info_results_lock:
                present = key in self._live_info_results
                value = self._live_info_results.pop(key, None)
            if present and component not in state['applied']:
                state['applied'].add(component)
                self._apply_live_info_result(state, component, value)
        finished = all((event.is_set() or (state.get('tokens', {}).get(component) is not None and (not state['tokens'][component].active())) for component, event in state.get('events', {}).items()))
        if not finished:
            self._live_info_timer.start(LIVE_INFO_POLL_MS, True)
            return
        if 'picon_path' in state.get('events', {}) and 'picon_path' not in state['applied']:
            if generation == self._generation and stream_id == str(getattr(self.current_item, 'stream_id', '') or ''):
                self._defer_live_picon()
        self._live_info_state = None
        if self._live_info_queued_generation == self._generation:
            elapsed_ms = max(0, int(round((time.monotonic() - self._live_play_started_at) * 1000)))
            self._start_live_info_completion(force=elapsed_ms >= LIVE_INFO_FALLBACK_MS)

    def _restore_old_service(self, navigation=None):
        """Restore the pre-player service at most once."""
        if self._old_reference_restored:
            return True
        returning = getattr(self, '_web_return_player', None)
        if returning is not None:
            parent, state = returning
            if not parent.web_return_valid(state):
                self._web_return_player = None
                self._old_reference_restored = True
                return True
            if not parent.resume_from_web_video(state):
                return False
            self._web_return_player = None
            self._old_reference_restored = True
            return True
        navigation = navigation or getattr(self.session, 'nav', None)
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
        navigation = getattr(self.session, 'nav', None)
        if navigation is None:
            self._set_static_info_channel_name(_('Could not start the player.'))
            self.show_info()
            return
        getter = getattr(navigation, 'getCurrentlyPlayingServiceReference', None)
        active_reference = None
        preparation = getattr(self, '_archive_initial_playback', None)
        if self._is_archive_item() and preparation is not None:
            if not preparation.ready():
                self.close()
                return
            self.old_reference = preparation.previous_reference
            preparation.released = False
            self._archive_initial_playback = None
        elif getter is not None:
            try:
                active_reference = getter()
                self.old_reference = self._old_reference_override if self._adopt_playing_service else active_reference
            except Exception:
                self.old_reference = self._old_reference_override if self._adopt_playing_service else None
        self._old_reference_restored = False
        if self._is_live_item() and self._adopt_playing_service:
            from .browser_live import GTStreamListScreen
            if not GTStreamListScreen._same_service_reference(active_reference, self.reference):
                restored = self._restore_old_service(navigation)
                self._fullscreen_close_result = self._live_fullscreen_result(False, released=restored)
                self.close(self._fullscreen_close_result)
                return
            self.reference = active_reference
            self._active_live_service_type = reference_service_type(active_reference, 4097)
            self._started = True
            self._generation += 1
            self._player_stream_diag('handoff', 'outcome=accepted reason=preview-service')
            self._watch_player_stream_diag(navigation, active_reference, 'preview-handoff')
            _debug('playback adopted content=live actual_engine={} reason=preview_handoff'.format(self._active_live_service_type))
            arm_startup = getattr(self, '_arm_live_startup', None)
            if callable(arm_startup):
                arm_startup()
            self._after_zap()
            return
        if self._is_live_item():
            self._stop_live_auxiliary_timers()
            next_reference = self._fresh_live_reference()
            self._player_stream_diag('service-reference', 'outcome={} actual_engine={}'.format('built' if next_reference is not None else 'unavailable', reference_service_type(next_reference, 4097)))
            try:
                self._play_subtitle_service(navigation, next_reference)
            except Exception as error:
                self._player_stream_diag('play-service', 'outcome=error error_type={}'.format(error.__class__.__name__))
                _debug('live initial start failed error={}'.format(error.__class__.__name__))
                self._restore_old_service(navigation)
                self._set_static_info_channel_name(_('Could not open the stream.'))
                self.show_info()
                return
            self.reference = next_reference
            self._active_live_service_type = reference_service_type(next_reference, 4097)
            self._started = True
            self._generation += 1
            self._watch_player_stream_diag(navigation, next_reference, 'direct-screen-open')
            snapshot = self._live_snapshot_for_current_item()
            _debug('playback started content=live selected_engine={} actual_engine={} reason=direct_screen_open snapshot_events={} snapshot_picon={} snapshot_weather={}'.format(self._active_live_service_type, reference_service_type(next_reference, 4097), len(self._live_snapshot_events), bool(snapshot.get('picon_path')), bool(snapshot.get('weather_forecast', snapshot.get('weather')))))
            arm_startup = getattr(self, '_arm_live_startup', None)
            if callable(arm_startup):
                arm_startup()
            self._after_zap()
            return
        if self._is_archive_item():
            service_getter = getattr(navigation, 'getCurrentService', None)
            try:
                self._archive_previous_service = service_getter() if callable(service_getter) else None
            except Exception:
                self._archive_previous_service = None
            try:
                previous_snapshot = _decoder_media_snapshot(self._archive_previous_service, iServiceInformation)
            except Exception:
                previous_snapshot = {}
            self._archive_previous_pts = _ArchivePlaybackWatchdog._snapshot_pts(previous_snapshot)
            self._archive_previous_position = _ArchivePlaybackWatchdog._position_ticks(self._archive_previous_service)
        try:
            self._play_subtitle_service(navigation, self.reference)
        except Exception as error:
            _debug('playback start failed content={} error={}'.format(getattr(self.current_item, 'content_type', 'unknown'), error.__class__.__name__))
            self._restore_old_service(navigation)
            if self._is_archive_item():
                self._archive_startup_failed()
                return
            self._set_static_info_channel_name(_('Could not open the stream.'))
            self.show_info()
            return
        _debug('playback started content={} selected_engine={} actual_engine={} reason=screen_open'.format(getattr(self.current_item, 'content_type', 'unknown'), self._active_live_service_type, reference_service_type(self.reference, 4097)))
        self._started = True
        self._generation += 1
        self._after_zap()

    def suspend_for_web_video(self):
        """Capture VOD before a web YouTube player replaces its decoder."""
        if self._closed or not self._started or getattr(self.session, 'current_dialog', None) is not self or (getattr(self.current_item, 'content_type', '') not in ('movie', 'series')) or bool(getattr(self.current_item, 'youtube_native', False)) or (getattr(self, '_web_suspended', None) is not None) or (self._pending_engine_reference is not None) or (self._playing_reference_identity() != self._reference_identity(self.reference)):
            return None
        position, _length = self._seek_position()
        if getattr(self, '_resume_web_return', False) and self._resume_start_position > 0:
            position = self._resume_start_position
        controller = getattr(self, '_subtitle_controller', None)
        pending_return = bool(getattr(self, '_resume_web_return', False))
        subtitle_state = self._web_resume_subtitle if pending_return else None
        if controller is not None:
            try:
                if not pending_return:
                    subtitle_state = controller.snapshot_resume_state()
            except Exception:
                pass
        if not pending_return:
            self._resume_saved = False
            self._save_resume_position()
        state = {'generation': self._generation, 'item': self.current_item, 'reference': self.reference, 'position': max(0, int(position)), 'paused': self._web_resume_paused if pending_return else self._paused, 'subtitle': subtitle_state}
        self._web_suspended = state
        self._cancel_seek_verification()
        self._hide_subtitle_overlay()
        self._stop_player_weather()
        self._network_rate_sampler.reset()
        for name in ('_hide_timer', '_metadata_timer', '_picon_timer', '_clock_timer', '_infobar_telemetry_timer', '_weather_timer', '_playback_timer', '_resume_timer', '_direction_hold_timer', '_seek_verify_timer', '_subtitle_auto_timer'):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass
        self._held_direction = None
        self._held_direction_long = False
        if controller is not None:
            try:
                controller.reset_for_service_change()
            except Exception:
                _debug('interrupted VOD subtitle reset failed')
        self._hide_subtitle_overlay()
        return state

    def web_return_valid(self, state):
        return bool(not self._closed and state is getattr(self, '_web_suspended', None) and isinstance(state, dict) and (state['generation'] == self._generation) and (state['item'] is self.current_item) and (state['reference'] is self.reference))

    def resume_from_web_video(self, state):
        """Restore the interrupted VOD and seek automatically, without a prompt."""
        if not self.web_return_valid(state):
            return False
        navigation = getattr(self.session, 'nav', None)
        if navigation is None:
            return False
        try:
            if self._playing_reference_identity() != self._reference_identity(state['reference']):
                self._play_subtitle_service(navigation, state['reference'])
        except Exception:
            return False
        self._web_suspended = None
        self._generation += 1
        self._started = True
        self._seeking = False
        self._paused = False
        self._resume_saved = False
        self._resume_start_position = state['position']
        self._resume_web_return = True
        self._web_resume_paused = state['paused']
        self._web_resume_subtitle = state['subtitle'] or {'kind': 'disabled'}
        self._resume_subtitle_pending_key = ''
        self._after_zap()
        self._subtitle_auto_generation = -1
        self._subtitle_auto_timer.stop()
        self._clock_tick()
        if self._resume_start_position <= 0:
            self._finish_web_resume()
        return True

    def _finish_web_resume(self):
        if not getattr(self, '_resume_web_return', False):
            return
        self._resume_web_return = False
        state = self._web_resume_subtitle
        self._web_resume_subtitle = None
        controller = getattr(self, '_subtitle_controller', None)
        if state is not None and controller is not None:
            try:
                controller.restore_resume_state(state)
            except Exception:
                _debug('interrupted VOD subtitle restore failed')
        paused = self._web_resume_paused
        self._web_resume_paused = False
        if paused:
            self.pause_playback()

    def set_youtube_end_callback(self, callback):
        if not bool(getattr(self.current_item, 'youtube_native', False)):
            return
        if self._youtube_end_watcher is None:
            self._youtube_end_watcher = YouTubeEndWatcher(self, callback)
        else:
            self._youtube_end_watcher.callback = callback
            self._youtube_end_watcher.arm()

    def retry_youtube_start(self, reference, item, resume_store=None, start_position=0):
        """Retry a rejected initial YouTube start in the already open screen."""
        if (self._closed or self._started
                or getattr(self.session, 'current_dialog', None) is not self
                or not bool(getattr(self.current_item, 'youtube_native', False))
                or not bool(getattr(item, 'youtube_native', False))):
            return False
        navigation = getattr(self.session, 'nav', None)
        try:
            current = navigation.getCurrentlyPlayingServiceReference()
        except Exception:
            return False
        if self._reference_identity(current) != self._reference_identity(self.old_reference):
            return False
        self.reference = reference
        self.current_item = item
        self._active_live_service_type = reference_service_type(reference, 4097)
        self._resume_store = resume_store
        self._resume_key = str(getattr(item, 'stream_id', '') or '')
        self._resume_start_position = youtube_resume_position(start_position, youtube_duration_seconds(item))
        self.start_playback()
        return self._started

    def replace_web_video(self, reference, item, resume_store=None, start_position=0):
        """Replace an active web video without pushing another modal screen.

        Keep old_reference from the first video. EXIT then restores the service
        that was playing before the web player was opened, just once.
        """
        if self._closed or not self._started or getattr(self.session, 'current_dialog', None) is not self:
            return False
        navigation = getattr(self.session, 'nav', None)
        if navigation is None:
            return False
        previous_reference = self.reference
        self._save_resume_position()
        try:
            self._play_subtitle_service(navigation, reference)
        except Exception:
            try:
                self._play_subtitle_service(navigation, previous_reference)
            except Exception:
                pass
            return False
        self.reference = reference
        self.current_item = item
        self._active_live_service_type = reference_service_type(reference, 4097)
        self._resume_store = resume_store
        self._resume_key = str(getattr(item, 'stream_id', '') or '')
        self._resume_start_position = youtube_resume_position(start_position, youtube_duration_seconds(item))
        self._resume_subtitle_pending_key = ''
        self._prepare_switched_vod()
        self._generation += 1
        self._after_zap()
        return True

    def _arm_stalker_auto_recovery(self):
        from .live_patch import arm_auto_recovery
        arm_auto_recovery(self)

    def _cancel_stalker_auto_recovery(self):
        recovery = getattr(self, '_stalker_auto_recovery', None)
        if recovery is not None:
            recovery.cancel()

    def _after_zap(self, refresh_epg=False):
        self._episode_watcher.arm()
        self._arm_resume_bookmark_guard()
        if self._started and self._is_live_item():
            remember_live_channel(self.client, self.current_item, resume_account_scope(self.client), category_id=None if live_channel_key(self.current_item) in getattr(self, '_live_recall_extra_keys', ()) else getattr(self, '_live_recall_category_id', None), context=None if live_channel_key(self.current_item) in getattr(self, '_live_recall_extra_keys', ()) else getattr(self, '_live_list_context', None))
        self._cancel_seek_verification()
        self._seek_ready = False
        self._seek_startup_deadline = time.monotonic() + SEEK_STARTUP_GRACE_SECONDS
        self._stop_live_auxiliary_timers()
        self._reset_picon_display()
        self._current_epg_events = []
        self._live_snapshot_events = []
        is_live = self._is_live_item()
        if is_live and refresh_epg and (str(getattr(self.client, 'source_type', 'xtream') or 'xtream').lower() in ('xtream', 'stalker')):
            self._live_info_epg_refresh_generation = self._generation
        if is_live:
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
            self._live_metadata_settle_pending = True
        metadata_delay = LIVE_INFO_METADATA_INITIAL_DELAY_MS if is_live else LIVE_METADATA_INITIAL_DELAY_MS
        self._metadata_timer.start(metadata_delay, True)
        try:
            self._playback_timer.stop()
        except Exception:
            pass
        try:
            self._resume_timer.stop()
        except Exception:
            pass
        try:
            self._subtitle_auto_timer.stop()
        except Exception:
            pass
        self._reset_live_reconnect()
        if not is_live:
            self._playback_timer.start(650, True)
            if self._resume_start_position > 0:
                self._resume_attempts = 0
                self._resume_last_length = -1
                self._resume_stable_length_samples = 0
                self._resume_timer.start(RESUME_INITIAL_DELAY_MS, True)
        if is_live:
            self._schedule_live_info_completion()
        else:
            self._start_picon_load()
            if getattr(self.current_item, 'content_type', '') in ('movie', 'series'):
                self._subtitle_auto_generation = self._generation
                self._subtitle_auto_timer.start(SUBTITLE_AUTO_MATCH_DELAY_MS, True)
        if self._is_archive_item():
            self._archive_portal_keepalive()
            self._arm_archive_startup()
        self._arm_stalker_auto_recovery()

    def _stop_live_auxiliary_timers(self):
        self._cancel_stalker_auto_recovery()
        telemetry = getattr(self, '_infobar_telemetry_timer', None)
        if telemetry is not None:
            telemetry.stop()
        for timer in (self._metadata_timer, self._live_info_timer, self._picon_timer):
            try:
                timer.stop()
            except Exception:
                pass
        self._live_metadata_settle_pending = False
        self._cancel_live_info_state()
        for attribute in ('_picon_worker_token',):
            token = getattr(self, attribute, None)
            _cancel_worker_token(token)
            setattr(self, attribute, None)

    def _live_transition_in_progress(self):
        """Return True while current_item/reference are intentionally stale."""
        return bool(getattr(self.current_item, 'content_type', '') == 'live' and (self._pending_channel_index is not None or self._pending_engine_reference is not None or self._live_reconnecting))

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
            self._reconnect_link_url = ''
            self._reconnect_link_deadline = 0.0
            self._reconnect_link_token = None
            self._reconnect_reason = ''
            self._reconnect_decoder_released = False
            self._reconnect_pending_reference = None
            self._reconnect_queued = False
        _cancel_worker_token(token)

    def _subtitle_sync_context(self, require_focus=True):
        if self._closed or not self._started or getattr(self, '_web_suspended', None) is not None or (getattr(self, '_pending_engine_reference', None) is not None):
            return None
        controller = getattr(self, '_subtitle_controller', None)
        online = getattr(controller, 'online', None)
        if controller is None or getattr(controller, '_closed', False) or online is None or getattr(online, '_closed', False) or (not online.is_loaded):
            return None
        if require_focus and (getattr(self.session, 'current_dialog', None) is not self or not getattr(self.session, 'in_exec', True) or getattr(controller, '_menu_open', False) or getattr(self, '_subtitle_menu_open', False) or getattr(self, '_subtitle_message_tokens', None) or getattr(self, '_audio_menu_open', False) or getattr(self, '_media_menu_open', False) or getattr(self, '_aspect_menu_open', False) or getattr(self, '_minute_seek_open', False)):
            return None
        renderer = online.renderer
        return (self._generation, renderer.content_revision, renderer.loaded_path)

    def _adjust_subtitle_sync(self, delta_ms, reset=False):
        if self._subtitle_sync_context() is None:
            return False
        try:
            offset = self._subtitle_controller.adjust_online_timing(delta_ms, reset=reset)
        except Exception as error:
            log_event('subtitles', 'timing-shortcut-failed', error)
            return False
        if offset is None:
            return False
        self._show_subtitle_sync_offset(offset)
        return True

    def _show_subtitle_sync_offset(self, offset_ms):
        try:
            dialog = self._subtitle_sync_overlay
            if dialog is None:
                dialog = self.session.instantiateDialog(GTSubtitleSyncOverlay)
                self._subtitle_sync_overlay = dialog
            dialog.show_offset(offset_ms)
            return
        except Exception as error:
            log_event('subtitles', 'timing-notice-failed', error)
        text = _('Subtitle timing offset: {:+.1f} seconds').format(offset_ms / 1000.0)
        self._set_static_info_epg('epg_now', text)
        self._set_static_info_epg('epg_next', '')
        self.show_info()

    def _dismiss_subtitle_sync_offset(self):
        dialog = getattr(self, '_subtitle_sync_overlay', None)
        if dialog is not None:
            dialog.dismiss()

    def _persist_subtitle_sync(self, context):
        if context == self._subtitle_sync_context(require_focus=False):
            self._remember_resume_subtitle()

    def _subtitle_sync_shortcut(self, command):
        result = self._subtitle_sync_keys.handle(command, 0)
        self._subtitle_sync_keys.handle(command, 1)
        return result

    def subtitle_sync_minus(self):
        return self._subtitle_sync_shortcut('minus')

    def subtitle_sync_reset(self):
        return self._subtitle_sync_shortcut('reset')

    def subtitle_sync_plus(self):
        return self._subtitle_sync_shortcut('plus')




    def _bind_player_key_handler(self):
        """Receive remote events independently of image-specific keymaps."""
        if self._player_keys_bound or self._closed or getattr(self, '_web_suspended', None) is not None:
            return
        try:
            from enigma import eActionMap
            from keyids import KEYIDS
            self._player_key_ids = {name: KEYIDS[name] for name in ('KEY_0', 'KEY_1', 'KEY_2', 'KEY_3', 'KEY_4', 'KEY_5', 'KEY_6', 'KEY_7', 'KEY_8', 'KEY_9', 'KEY_YELLOW', 'KEY_GREEN', 'KEY_LEFT', 'KEY_RIGHT', 'KEY_EPG', 'KEY_INFO') if name in KEYIDS}
            self._player_key_map = eActionMap.getInstance()
            self._player_key_map.bindAction('', -100, self._player_key_callback)
            self._player_keys_bound = True
            log_event('minute_seek', 'remote-bound kind={}'.format(getattr(self.current_item, 'content_type', 'unknown')))
        except Exception as error:
            log_event('minute_seek', 'remote-bind-failed', error)
            _debug('player remote binding failed error={}'.format(error.__class__.__name__))

    def _unbind_player_key_handler(self):
        self._reset_live_patch_code()
        self._subtitle_sync_keys.cancel()
        self._dismiss_subtitle_sync_offset()
        zero_key = getattr(self, '_live_zero_key', None)
        if zero_key is not None:
            zero_key.cancel()
        try:
            self._direction_hold_timer.stop()
        except Exception:
            pass
        self._held_direction = None
        self._held_direction_long = False
        if not self._player_keys_bound:
            return
        self._player_keys_bound = False
        try:
            self._player_key_map.unbindAction('', self._player_key_callback)
        except Exception as error:
            _debug('player remote unbinding failed error={}'.format(error.__class__.__name__))

    def _handle_player_key(self, key, flag):
        """Consume player keys only while the fullscreen player owns focus."""
        key_ids = self._player_key_ids
        if self._closed or getattr(self.session, 'current_dialog', None) is not self:
            return 0
        kind = getattr(self.current_item, 'content_type', '')
        if self._handle_live_patch_key(key, flag):
            return 1
        for name, command in (('KEY_4', 'minus'), ('KEY_5', 'reset'), ('KEY_6', 'plus')):
            key_id = key_ids.get(name)
            if key_id is not None and key == key_id:
                if flag == 0:
                    self._live_zero_key.cancel()
                return self._subtitle_sync_keys.handle(command, flag)
        if flag == 0:
            self._subtitle_sync_keys.cancel()
        if kind == 'live' and key == key_ids.get('KEY_0'):
            return self._live_zero_key.handle(flag)
        if flag == 0:
            self._live_zero_key.cancel()
        if kind == 'live' and key in (key_ids.get('KEY_EPG'), key_ids.get('KEY_INFO')):
            if flag == 0:
                self.open_epg_guide()
            return 1
        if key == key_ids.get('KEY_1') and kind in ('live', 'movie', 'series'):
            if flag == 0:
                self.cycle_player_service_type()
            return 1
        if kind == 'live' and key in (key_ids.get('KEY_YELLOW'), key_ids.get('KEY_GREEN')):
            if flag == 0:
                if key == key_ids.get('KEY_YELLOW'):
                    self.open_media_selection()
                else:
                    self.open_aspect_selection()
            return 1
        if kind not in ('movie', 'series'):
            return 0
        if key == key_ids.get('KEY_YELLOW'):
            if flag == 0:
                self.open_media_selection()
            return 1
        if key == key_ids.get('KEY_LEFT'):
            direction = -1
        elif key == key_ids.get('KEY_RIGHT'):
            direction = 1
        else:
            return 0
        if flag == 0:
            self._direction_press(direction)
        elif flag == 1:
            self._direction_release(direction)
        elif flag in (2, 3):
            self._direction_long(direction)
        else:
            return 0
        return 1

    def _live_zero_is_active(self):
        return bool(not self._closed and self._started and self._is_live_item() and getattr(self.session, 'in_exec', True) and (getattr(self.session, 'current_dialog', None) is self) and (getattr(self, '_web_suspended', None) is None))

    def _prime_live_zero_key(self):
        key = self._player_key_ids.get('KEY_0')
        if self._player_key_map is not None and key is not None:
            self._player_key_map.keyPressed('dreambox remote control (native)', key, 0)

    def open_epg_guide(self):
        if not self._is_live_item():
            self.toggle_info()
            return
        if self._closed or not self._started or getattr(self, '_epg_guide_open', False) or self._live_transition_in_progress():
            return
        from .epg_guide_ui import GTEPGGuideScreen
        self._epg_guide_open = True
        self._live_zero_key.cancel()
        self.hide_info()
        self._stop_live_auxiliary_timers()
        key = (resume_account_scope(self.client), live_channel_key(self.current_item))
        saved = getattr(self, '_epg_guide_cache', None)
        cached = saved[1] if saved is not None and saved[0] == key else None
        try:
            self.session.openWithCallback(lambda result=None: self._epg_guide_closed(key, result), GTEPGGuideScreen, self.client, self.current_item, self.reference, cached=cached, seed_events=list(getattr(self, '_current_epg_events', ()) or ()))
        except Exception as error:
            self._epg_guide_open = False
            _debug('EPG guide open failed error={}'.format(error.__class__.__name__))
            log_event('epg_guide', 'open-failed', error)
            self._after_zap()
            self.session.open(MessageBox, _('Could not load the programme guide.'), type=MessageBox.TYPE_ERROR, timeout=8)

    def _epg_guide_closed(self, key, result=None):
        self._epg_guide_open = False
        if self._closed:
            return
        if isinstance(result, dict):
            self._epg_guide_cache = (key, result)
        if self._is_live_item():
            self._after_zap()

    def cycle_player_service_type(self):
        content_type = getattr(self.current_item, 'content_type', '')
        if content_type == 'live':
            return self.cycle_live_service_type()
        if content_type in ('movie', 'series'):
            return self.cycle_vod_service_type()
        self.show_info()

    def _engine_candidates(self, url, current_type, content_type):
        requested_types = list(available_service_types())
        try:
            configured_type = int(load_player_settings().service_type_for(content_type))
        except (AttributeError, TypeError, ValueError, OverflowError):
            configured_type = current_type
        if configured_type not in requested_types:
            requested_types.append(configured_type)
        if current_type not in requested_types:
            requested_types.insert(0, current_type)
        if content_type in ('movie', 'series') and current_type != 1:
            requested_types = [kind for kind in requested_types if kind != 1]
        if bool(getattr(self.current_item, 'youtube_separate_audio', False)) or getattr(self.current_item, 'youtube_video_codec', '') == 'vp9':
            requested_types = [NATIVE_AUDIO_SERVICE_TYPE]
        candidates = []
        seen = set()
        for requested_type in requested_types:
            try:
                candidate = build_extplayer_reference(url, getattr(self.current_item, 'name', self.title), content_type, service_type=requested_type)
            except Exception:
                continue
            actual_type = reference_service_type(candidate, 4097)
            if actual_type in seen:
                continue
            seen.add(actual_type)
            candidates.append((actual_type, candidate))
        return candidates

    def cycle_vod_service_type(self):
        """Switch the local movie/episode engine and resume the same title."""
        if self._closed or not self._started or self._pending_engine_reference is not None or self._audio_menu_open or self._subtitle_menu_open or self._media_menu_open or self._minute_seek_open:
            return
        navigation = getattr(self.session, 'nav', None)
        url = reference_stream_url(self.reference)
        if navigation is None or not url:
            self.show_info()
            return
        current_type = reference_service_type(self.reference, 5002)
        candidates = self._engine_candidates(url, current_type, getattr(self.current_item, 'content_type', 'movie'))
        if len(candidates) < 2:
            self.show_info()
            return
        types = [kind for kind, _reference in candidates]
        next_index = (types.index(current_type) + 1) % len(types) if current_type in types else 0
        target_type, target_reference = candidates[next_index]
        position, length = self._seek_position()
        resume_position = position if position > 0 and length > 0 and (position < length - 30) else 0
        previous_reference = self.reference
        self._remember_resume_subtitle(mark_disabled=True)
        self._resume_subtitle_pending_key = self._resume_key
        self._resume_subtitle_restore_attempts = 0
        self._cancel_seek_verification()
        self._seeking = False
        self._hide_seek_overlay()
        for timer in (self._playback_timer, self._resume_timer, self._subtitle_auto_timer):
            try:
                timer.stop()
            except Exception:
                pass
        self._stop_live_auxiliary_timers()
        self._generation += 1
        self._pending_engine_reference = target_reference
        self._previous_engine_reference = previous_reference
        self._engine_switch_generation = self._generation
        self._engine_switch_resume_position = resume_position
        self._engine_link_pending = False
        self._engine_decoder_released = False
        self._set_static_info_epg('epg_now', _('Reconnecting the stream...'))
        self._set_static_info_epg('epg_next', _('Please wait'))
        self.show_info()
        self._release_live_engine_for_switch(navigation, current_type, target_type)

    def cycle_live_service_type(self):
        """Cycle only through engines installed on the receiver."""
        if getattr(self.current_item, 'content_type', '') != 'live':
            self.show_info()
            return
        if self._live_reconnecting or self._pending_engine_reference is not None or self._pending_channel_index is not None:
            self.show_info()
            return
        navigation = getattr(self.session, 'nav', None)
        url = reference_stream_url(self.reference)
        if navigation is None or not url:
            self.show_info()
            return
        current_type = reference_service_type(self.reference, self._active_live_service_type)
        candidates = self._engine_candidates(url, current_type, 'live')
        if len(candidates) < 2:
            self.show_info()
            return
        if callable(getattr(self, '_cancel_live_startup', None)) and self._cancel_live_startup(restore_released=True) is False:
            self.show_info()
            return
        candidate_types = [item[0] for item in candidates]
        try:
            target_index = (candidate_types.index(current_type) + 1) % len(candidates)
        except ValueError:
            target_index = candidate_types.index(4097) if 4097 in candidate_types else 0
        target_type, target_reference = candidates[target_index]
        self._new_player_stream_diag('engine-switch')
        self._player_stream_diag('service-reference', 'outcome=built requested_engine={} actual_engine={}'.format(target_type, reference_service_type(target_reference, 4097)))
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
        self._engine_link_url = ''
        self._engine_link_deadline = 0.0
        self._engine_decoder_released = False
        self._set_static_info_epg('epg_now', _('Reconnecting the stream...'))
        self._set_static_info_epg('epg_next', _('Please wait'))
        self.show_info()
        self._release_live_engine_for_switch(navigation, current_type, target_type)

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
        self._engine_link_url = ''
        self._engine_link_deadline = 0.0
        _cancel_worker_token(self._engine_link_token)
        self._engine_link_token = None
        self._engine_decoder_released = False
        self._engine_switch_resume_position = 0

    def _refresh_engine_switch_link(self, generation, refresher, item, token=None):
        try:
            url = str(_bounded_client_call(self.client, token, lambda: refresher(item)) or '').strip()
        except Exception as error:
            _debug('playback engine switch link failed error={}'.format(error.__class__.__name__))
            url = ''
        if self._closed or (token is not None and (not token.active())) or self._pending_engine_reference is None or (self._engine_switch_generation != generation):
            return
        self._engine_link_url = url
        self._engine_link_ready = True

    def _release_live_engine_for_switch(self, navigation, previous_type, target_type):
        try:
            navigation.stopService()
        except Exception as error:
            _debug('playback engine release failed from={} to={} error={}'.format(previous_type, target_type, error.__class__.__name__))
        self._engine_decoder_released = True
        _debug('playback engine switch scheduled from={} to={} reason=user release_delay_ms={}'.format(previous_type, target_type, LIVE_RECONNECT_RELEASE_DELAY_MS))
        self._engine_switch_timer.start(LIVE_RECONNECT_RELEASE_DELAY_MS, True)

    def _complete_live_engine_switch(self):
        target_reference = self._pending_engine_reference
        previous_reference = self._previous_engine_reference
        resume_position = self._engine_switch_resume_position
        if target_reference is None:
            return
        if self._closed or self._engine_switch_generation != self._generation:
            self._clear_live_engine_switch_state()
            return
        navigation = getattr(self.session, 'nav', None)
        if navigation is None:
            self._clear_live_engine_switch_state()
            return
        previous_type = reference_service_type(previous_reference, 4097)
        target_type = reference_service_type(target_reference, 4097)
        if self._engine_link_pending:
            if not self._engine_link_ready and time.monotonic() < self._engine_link_deadline:
                self._engine_switch_timer.start(100, True)
                return
            refreshed_url = self._engine_link_url if self._engine_link_ready else ''
            if not refreshed_url:
                self._clear_live_engine_switch_state()
                self._set_static_info_epg('epg_next', _('Keeping the current stream'))
                arm_startup = getattr(self, '_arm_live_startup', None)
                if callable(arm_startup):
                    arm_startup()
                self._after_zap()
                return
            try:
                target_reference = build_extplayer_reference(refreshed_url, getattr(self.current_item, 'name', self.title), 'live', service_type=target_type)
                previous_reference = build_extplayer_reference(refreshed_url, getattr(self.current_item, 'name', self.title), 'live', service_type=previous_type)
            except Exception as error:
                _debug('playback engine switch reference failed error={}'.format(error.__class__.__name__))
                self._clear_live_engine_switch_state()
                arm_startup = getattr(self, '_arm_live_startup', None)
                if callable(arm_startup):
                    arm_startup()
                self._after_zap()
                return
            target_type = reference_service_type(target_reference, 4097)
            previous_type = reference_service_type(previous_reference, 4097)
            self._pending_engine_reference = target_reference
            self._previous_engine_reference = previous_reference
            self._engine_link_pending = False
            self._release_live_engine_for_switch(navigation, previous_type, target_type)
            return
        if not self._engine_decoder_released:
            self._release_live_engine_for_switch(navigation, previous_type, target_type)
            return
        try:
            self._play_subtitle_service(navigation, target_reference)
        except Exception as error:
            if getattr(getattr(self, 'current_item', None), 'content_type', '') == 'live' and _is_stalker_client(getattr(self, 'client', None)):
                self._player_stream_diag('play-service', 'outcome=error reason=engine-switch error_type={}'.format(error.__class__.__name__))
            _debug('playback engine switch failed from={} to={} error={}'.format(previous_type, target_type, error.__class__.__name__))
            restored = False
            if previous_reference is not None:
                try:
                    self._play_subtitle_service(navigation, previous_reference)
                    restored = True
                    self.reference = previous_reference
                    self._active_live_service_type = previous_type
                    if not self._is_live_item():
                        self._resume_start_position = resume_position
                except Exception:
                    pass
            self._clear_live_engine_switch_state()
            if not self._is_live_item() and restored:
                self._prepare_switched_vod()
            if not self._is_live_item() and (not restored):
                self._set_static_info_epg('epg_now', _('Could not open the stream.'))
                self.show_info()
                return
            arm_startup = getattr(self, '_arm_live_startup', None)
            if callable(arm_startup):
                arm_startup()
            self._after_zap()
            return
        self.reference = target_reference
        self._active_live_service_type = target_type
        if getattr(getattr(self, 'current_item', None), 'content_type', '') == 'live' and _is_stalker_client(getattr(self, 'client', None)):
            self._watch_player_stream_diag(navigation, target_reference, 'engine-switch')
        self._clear_live_engine_switch_state()
        if not self._is_live_item():
            self._resume_start_position = resume_position
            self._prepare_switched_vod()
        self._info_dialog['engine'].setText(service_engine_label(target_type))
        _debug('playback engine switched from={} to={} reason=user'.format(previous_type, target_type))
        arm_startup = getattr(self, '_arm_live_startup', None)
        if callable(arm_startup):
            arm_startup()
        self._after_zap()

    def _prepare_switched_vod(self):
        self._resume_saved = False
        self._paused = False
        self._seeking = False
        controller = getattr(self, '_subtitle_controller', None)
        if controller is not None:
            controller.reset_for_service_change()

    def restart_live_stream(self):
        """Restart the current live stream after an explicit held 0 key."""
        if getattr(self.current_item, 'content_type', '') != 'live':
            self.show_info()
            return
        self._restart_live_stream()

    def _restart_live_stream(self):
        self._cancel_live_startup()
        if self._live_reconnecting:
            self._reconnect_queued = True
            log_event('live_recovery', 'manual request queued engine={}'.format(reference_service_type(self.reference, 4097)))
            self.show_info()
            return
        if self._pending_engine_reference is not None or self._closed:
            return
        if getattr(self.current_item, 'content_type', '') != 'live':
            return
        self._live_reconnecting = True
        self._new_player_stream_diag('manual-zero')
        navigation = getattr(self.session, 'nav', None)
        if navigation is None:
            self._live_reconnecting = False
            return
        self._reconnect_reason = 'manual'
        log_event('live_recovery', 'manual request started engine={}'.format(reference_service_type(self.reference, 4097)))
        with self._reconnect_link_lock:
            old_token = self._reconnect_link_token
            self._reconnect_generation = self._generation
            self._reconnect_link_pending = False
            self._reconnect_link_ready = False
            self._reconnect_link_url = ''
            self._reconnect_link_deadline = 0.0
            self._reconnect_decoder_released = False
            self._reconnect_pending_reference = None
            self._reconnect_queued = False
            self._reconnect_link_token = None
        _cancel_worker_token(old_token)
        self._set_static_info_epg('epg_now', _('Reconnecting the stream...'))
        self._set_static_info_epg('epg_next', _('Please wait'))
        self.show_info()
        self._stop_live_auxiliary_timers()
        refresher = None
        if _is_stalker_client(self.client):
            refresher = getattr(self.client, 'recover_live_playback_url', None)
        if not callable(refresher):
            refresher = getattr(self.client, 'refresh_playback_url', None)
        if callable(refresher) and self.current_item is not None:
            with self._reconnect_link_lock:
                self._reconnect_link_pending = True
            try:
                link_timeout = max(3, min(15, int(self.client.timeout)))
            except (AttributeError, TypeError, ValueError, OverflowError):
                link_timeout = 12
            token = _WorkerToken(min(WORKER_PLAYBACK_LINK_TIMEOUT_SECONDS, link_timeout + 1))
            with self._reconnect_link_lock:
                self._reconnect_link_token = token
                self._reconnect_link_deadline = token.deadline
            generation = self._reconnect_generation
            item = self.current_item
            worker = threading.Thread(target=self._refresh_reconnect_link, args=(generation, refresher, item, token))
            worker.daemon = True
            worker.start()
        else:
            local_url = ''
            if not _is_stalker_client(self.client):
                local_url = reference_stream_url(self.reference)
            with self._reconnect_link_lock:
                self._reconnect_link_url = str(local_url or '').strip()
                self._reconnect_link_ready = True
        _debug('live restart resolving engine={} reason={} release_delay_ms={}'.format(reference_service_type(self.reference, 4097), self._reconnect_reason, LIVE_RECONNECT_RELEASE_DELAY_MS))
        self._reconnect_timer.start(100, True)

    def _refresh_reconnect_link(self, generation, refresher, item, token=None):
        try:
            url = str(_bounded_client_call(self.client, token, lambda: refresher(item)) or '').strip()
        except Exception as error:
            _debug('live reconnect link refresh failed error={}'.format(error.__class__.__name__))
            url = ''
        with self._reconnect_link_lock:
            if self._closed or token is None or (not token.active()) or (self._reconnect_link_token is not token) or (not self._live_reconnecting) or (self._reconnect_generation != generation):
                return
            self._reconnect_link_url = url
            self._reconnect_link_ready = True
        self._player_stream_diag('link-result', 'outcome={}'.format('ready' if url else 'empty'))

    def _fresh_live_reference(self, refreshed_url='', require_refreshed=False):
        url = str(refreshed_url or '').strip()
        if not url:
            url = reference_stream_url(self.reference)
        if not url:
            return self.reference
        content_type = getattr(self.current_item, 'content_type', 'live')
        fallback_type = 4097 if content_type == 'live' else 5002
        try:
            return build_extplayer_reference(url, getattr(self.current_item, 'name', self.title), content_type, service_type=self._active_live_service_type if content_type == 'live' else reference_service_type(self.reference, fallback_type))
        except Exception as error:
            _debug('live reconnect reference rebuild failed error={}'.format(error.__class__.__name__))
            return None if require_refreshed else self.reference

    def _complete_live_restart(self):
        if not self._live_reconnecting or self._closed:
            return
        if self._reconnect_generation != self._generation:
            self._reset_live_reconnect()
            return
        navigation = getattr(self.session, 'nav', None)
        if navigation is None:
            self._reset_live_reconnect()
            return

        def resume_preserved_service():
            """Restore local jobs after a manual attempt kept/rolled back live."""
            self._reset_live_reconnect()
            self._after_zap()
            arm_startup = getattr(self, '_arm_live_startup', None)
            if callable(arm_startup):
                arm_startup()
        with self._reconnect_link_lock:
            link_pending = self._reconnect_link_pending
            link_ready = self._reconnect_link_ready
            link_deadline = self._reconnect_link_deadline
        if link_pending and (not link_ready) and (time.monotonic() < link_deadline):
            self._reconnect_timer.start(100, True)
            return
        if link_pending and (not link_ready):
            with self._reconnect_link_lock:
                self._reconnect_link_ready = True
                self._reconnect_link_url = ''
        with self._reconnect_link_lock:
            link_url = self._reconnect_link_url
        if not link_url:
            self._player_stream_diag('link-result', 'outcome=unavailable current_service=preserved')
            queued = bool(self._reconnect_queued)
            log_event('live_recovery', 'manual link outcome=unavailable current_service=preserved')
            resume_preserved_service()
            self._set_static_info_epg('epg_now', _('Could not open the stream.'))
            self._set_static_info_epg('epg_next', _('Keeping the current stream'))
            self.show_info()
            if queued and (not self._closed):
                self._restart_live_stream()
            return
        if self._reconnect_pending_reference is None:
            self._reconnect_pending_reference = self._fresh_live_reference(link_url, require_refreshed=True)
        next_reference = self._reconnect_pending_reference
        if next_reference is None:
            self._player_stream_diag('service-reference', 'outcome=unavailable')
            resume_preserved_service()
            return
        self._player_stream_diag('service-reference', 'outcome=built actual_engine={}'.format(reference_service_type(next_reference, 4097)))
        if not self._reconnect_decoder_released:
            try:
                navigation.stopService()
            except Exception as error:
                _debug('live reconnect teardown failed error={}'.format(error.__class__.__name__))
                resume_preserved_service()
                return
            self._reconnect_decoder_released = True
            log_event('live_recovery', 'manual release started delay_ms={}'.format(LIVE_RECONNECT_RELEASE_DELAY_MS))
            try:
                self._reconnect_timer.start(LIVE_RECONNECT_RELEASE_DELAY_MS, True)
            except Exception:
                self._complete_live_restart()
            return
        try:
            self._play_subtitle_service(navigation, next_reference)
        except Exception as error:
            self._player_stream_diag('play-service', 'outcome=error reason=manual-zero error_type={}'.format(error.__class__.__name__))
            _debug('live reconnect failed error={}'.format(error.__class__.__name__))
            try:
                self._play_subtitle_service(navigation, self.reference)
                log_event('live_recovery', 'manual replay outcome=rollback')
            except Exception:
                pass
            resume_preserved_service()
            return
        self.reference = next_reference
        self._active_live_service_type = reference_service_type(next_reference, 4097)
        self._watch_player_stream_diag(navigation, next_reference, 'manual-zero')
        queued = bool(self._reconnect_queued)
        reconnect_reason = self._reconnect_reason or 'manual'
        _debug('live stream restarted engine={} reason={}'.format(self._active_live_service_type, reconnect_reason))
        self._generation += 1
        log_event('live_recovery', 'manual replay outcome=started engine={}'.format(self._active_live_service_type))
        resume_preserved_service()
        if queued and (not self._closed):
            self._restart_live_stream()

    def _render_channel(self):
        screen_width, unused_height, px = _scale()
        youtube = bool(getattr(self.current_item, 'youtube_native', False))
        self._info_dialog.set_youtube_mode(youtube)
        self._set_info_chip('stream_bitrate', network_rate_text(None, compact=screen_width <= 720))
        unused_width, unused_height, px = _scale()
        fit_dynamic_text(self._info_dialog['account_source'], _('Source: {}').format(_('YouTube')) if youtube else source_account_text(self.client), max_lines=1, preferred_size=font_px(px, 19), min_size=max(6, px(12)))
        if youtube or live_channel_key(self.current_item) in self._live_recall_extra_keys:
            self._info_dialog['channel_number'].setText('')
        elif self._entries:
            number = self.current_index + 1
            self._info_dialog['channel_number'].setText('{:02d}'.format(number))
        else:
            self._info_dialog['channel_number'].setText('')
        title = getattr(self.current_item, 'name', self.title)
        self.title = str(title or 'GT IPTV')
        self._set_info_channel_name(self.title)
        fallback_type = 4097 if getattr(self.current_item, 'content_type', '') == 'live' else 5002
        self._info_dialog['engine'].setText(service_engine_label(reference_service_type(self.reference, fallback_type)))
        tracker = getattr(self, '_resolution_tracker', None)
        if tracker is not None:
            tracker.reset()
        self._set_info_chip('resolution', resolution_text(youtube_resolution_hint(self.current_item)))
        self._set_info_chip('fps', '-- FPS')
        self._set_info_chip('video_codec', '--')
        self._set_info_chip('audio_codec', '--')
        is_live = getattr(self.current_item, 'content_type', '') == 'live'
        self._info_dialog['epg_now_label'].setText(_('NOW') if is_live else _('PREVIOUS'))
        self._info_dialog['epg_next_label'].setText(_('NEXT') if is_live else _('REMAINING'))
        if is_live:
            epg_text = _('EPG information unavailable.') if self._live_info_loader() is None else _('Loading EPG...')
            self._set_static_info_epg('epg_now', epg_text)
            self._set_static_info_epg('epg_next', '')
        else:
            self._set_static_info_epg('epg_now', _('Reading duration...'))
            self._set_static_info_epg('epg_next', '--:--:--')
        self._clear_player_progress()
        self._render_favorite_badge()
        self.setTitle(self.title)

    def _current_favorite_item(self):
        item = self.current_item
        if getattr(item, 'content_type', '') == 'series':
            parent = getattr(item, 'favorite_parent', None) or self._favorite_parent
            if parent is not None:
                return parent
        return item

    def _render_favorite_badge(self):
        item = self._current_favorite_item()
        widget = self._info_dialog['favorite_badge']
        if item is not None and self._is_favorite(item):
            widget.setText(localized_upper(_('Favorite')))
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

    def ignore_vod_blue(self):
        """BLUE is no longer an audio shortcut in movies or series."""
        return True

    def toggle_favorite_with_blue_long(self):
        """Keep fullscreen VOD favorites on images with a BLUE-long action."""
        now = time.monotonic()
        self._suppress_next_blue_short_until = now + BLUE_LONG_SHORT_SUPPRESS_SECONDS
        previous = self._blue_long_last_at
        self._blue_long_last_at = now
        if now - previous < BLUE_LONG_REPEAT_SUPPRESS_SECONDS:
            return True
        return self.toggle_current_favorite()

    @staticmethod
    def _audio_track_text(value, limit=48):
        text = ' '.join(str(value or '').replace('\r', ' ').replace('\n', ' ').split())
        return text[:max(1, int(limit))]

    @classmethod
    def _audio_language_name(cls, value):
        """Return the decoder language without a dynamic gettext lookup."""
        code = cls._audio_track_text(value, 24)
        return localized_language_name(code)

    @classmethod
    def _audio_track_details(cls, track_info):
        description = ''
        language = ''
        if track_info is not None:
            getter = getattr(track_info, 'getDescription', None)
            if callable(getter):
                try:
                    description = localized_language_name(cls._audio_track_text(getter(), 48))
                except Exception:
                    description = ''
            getter = getattr(track_info, 'getLanguage', None)
            if callable(getter):
                try:
                    language = cls._audio_language_name(getter())
                except Exception:
                    language = ''
        return (description, language)

    @classmethod
    def _audio_track_label(cls, track_info, index):
        description, language = cls._audio_track_details(track_info)
        details = []
        for value in (language, description):
            if value and (not any((value.casefold() == existing.casefold() for existing in details))):
                details.append(value)
        if not details:
            details.append(_(N_('Audio track')))
        return '{}. {}'.format(index + 1, ' • '.join(details))[:120]

    @classmethod
    def _audio_track_fingerprint(cls, track_info):
        return tuple((value.casefold() for value in cls._audio_track_details(track_info)))

    @staticmethod
    def _reference_identity(reference):
        if reference is None:
            return ''
        for name in ('toCompareString', 'toString'):
            getter = getattr(reference, name, None)
            if callable(getter):
                try:
                    value = getter()
                except Exception:
                    value = ''
                if value:
                    return str(value)
        return '{}:{}'.format(reference_service_type(reference, 5002), reference_stream_url(reference))

    def _playing_reference_identity(self):
        navigation = getattr(self.session, 'nav', None)
        if navigation is not None:
            for name in ('getCurrentlyPlayingServiceReference', 'getCurrentlyPlayingServiceOrGroup'):
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
        return (int(self._generation), id(item), str(getattr(item, 'content_type', '') or ''), str(getattr(item, 'stream_id', '') or ''), self._playing_reference_identity())

    def _audio_tracks(self):
        service = self._current_service()
        getter = getattr(service, 'audioTracks', None) if service is not None else None
        if not callable(getter):
            return (service, None, 0)
        try:
            tracks = getter()
            count_getter = getattr(tracks, 'getNumberOfTracks', None)
            count = int(count_getter()) if callable(count_getter) else 0
        except Exception:
            return (service, None, 0)
        return (service, tracks, max(0, count))

    def _show_audio_message(self, message):
        self._show_subtitle_message(message)

    def open_media_selection(self):
        """Let YELLOW route to the existing manual audio/subtitle controls."""
        if self._closed or not self._started or getattr(self.current_item, 'content_type', '') not in ('live', 'movie', 'series') or self._media_menu_open or self._audio_menu_open or self._subtitle_menu_open or (self._pending_engine_reference is not None) or getattr(self, '_aspect_menu_open', False) or self._live_transition_in_progress():
            return True
        try:
            from Screens.ChoiceBox import ChoiceBox
        except ImportError:
            return self.open_subtitle_selection()
        opener = getattr(self.session, 'openWithCallback', None)
        if not callable(opener):
            return self.open_subtitle_selection()
        generation, item = (self._generation, self.current_item)
        expected_context = self._audio_selection_context()
        self._media_menu_open = True
        self.hide_info()

        def chosen(selection):
            self._media_menu_open = False
            if self._closed or self._generation != generation or self.current_item is not item or (not selection) or self._live_transition_in_progress() or (self._audio_selection_context() != expected_context):
                return
            if selection[1] == 'audio':
                self.open_audio_selection()
            elif selection[1] == 'subtitle':
                self.open_subtitle_selection()
        try:
            opener(chosen, ChoiceBox, _('Select'), [(_('Audio tracks'), 'audio'), (_('Subtitles'), 'subtitle')])
        except Exception:
            self._media_menu_open = False
            self.show_info()
        return True

    def open_audio_selection(self):
        """Open decoder audio tracks without restarting live or VOD playback."""
        now = time.monotonic()
        if self._suppress_next_blue_short_until:
            suppress = now <= self._suppress_next_blue_short_until
            self._suppress_next_blue_short_until = 0.0
            if suppress:
                return True
        if self._closed or not self._started or getattr(self.current_item, 'content_type', '') not in ('live', 'movie', 'series') or self._live_transition_in_progress() or getattr(self, '_aspect_menu_open', False):
            return True
        if getattr(self, '_audio_menu_open', False):
            return True
        _service, tracks, track_count = self._audio_tracks()
        if tracks is None or track_count <= 0:
            self._show_audio_message(N_('No audio tracks are available.'))
            return True
        if track_count == 1:
            self._show_audio_message(N_('This content has only one audio track.'))
            return True
        if not callable(getattr(tracks, 'selectTrack', None)):
            self._show_audio_message(N_('Audio track selection is unavailable on this image.'))
            return True
        try:
            from Screens.ChoiceBox import ChoiceBox
        except Exception:
            ChoiceBox = None
        opener = getattr(self.session, 'openWithCallback', None)
        if ChoiceBox is None or not callable(opener):
            self._show_audio_message(N_('Audio track selection is unavailable on this image.'))
            return True
        choices = []
        current_track = -1
        getter = getattr(tracks, 'getCurrentTrack', None)
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
            payload = (index, track_count, self._audio_track_fingerprint(track_info))
            choices.append((self._audio_track_label(track_info, index), payload))
        expected_context = self._audio_selection_context()

        def selected(selection):
            self._audio_menu_open = False
            self._audio_selection_made(expected_context, selection)
        selection_index = current_track if 0 <= current_track < visible_count else 0
        self.hide_info()
        self._audio_menu_open = True
        try:
            opener(selected, ChoiceBox, _('Audio tracks'), choices, selection=selection_index)
        except TypeError:
            try:
                opener(selected, ChoiceBox, _('Audio tracks'), choices)
            except Exception:
                self._audio_menu_open = False
                self._show_audio_message(N_('Audio track selection is unavailable on this image.'))
        except Exception:
            self._audio_menu_open = False
            self._show_audio_message(N_('Audio track selection is unavailable on this image.'))
        return True

    def _audio_selection_made(self, expected_context, selection):
        if self._closed or not selection:
            return
        if self._audio_selection_context() != expected_context or self._live_transition_in_progress():
            self._show_audio_message(N_('The playing service changed. Open audio selection again.'))
            return
        try:
            index, expected_count, expected_fingerprint = selection[1]
            index = int(index)
            expected_count = int(expected_count)
            expected_fingerprint = tuple(expected_fingerprint)
        except (IndexError, KeyError, TypeError, ValueError, OverflowError):
            return
        service, tracks, track_count = self._audio_tracks()
        if tracks is None or track_count != expected_count or (not 0 <= index < track_count):
            self._show_audio_message(N_('Audio tracks changed. Open audio selection again.'))
            return
        try:
            track_info = tracks.getTrackInfo(index)
        except Exception:
            track_info = None
        if self._audio_track_fingerprint(track_info) != expected_fingerprint:
            self._show_audio_message(N_('Audio tracks changed. Open audio selection again.'))
            return
        selector = getattr(tracks, 'selectTrack', None)
        if not callable(selector):
            self._show_audio_message(N_('Audio track selection is unavailable on this image.'))
            return
        try:
            selector(index)
        except Exception:
            self._show_audio_message(N_('Could not select the requested audio track.'))
            return
        self._metadata_attempts = 0
        self._metadata_tick()
        if service is not None:
            description = self._audio_description(service)
            if description != '--':
                self._set_info_chip('audio_codec', description)
        self.show_info()

    def next_channel(self):
        self._switch_channel(1)

    def previous_channel(self):
        self._switch_channel(-1)

    def recall_previous_live_channel(self):
        """Recall accepted live playback across list/fullscreen handoffs."""
        if not self._is_live_item():
            self.show_info()
            return
        if self._closed or not self._started:
            return
        previous = previous_live_channel_selection(self.client, self.current_item, resume_account_scope(self.client))
        if previous is None:
            _debug('live recall ignored reason=no-previous-channel')
            return
        previous_key = previous.key
        index = next((index for index, item in enumerate(self._entries) if live_channel_key(item) == previous_key), None)
        if index is None:
            hidden = getattr(self, '_live_recall_hidden_category_ids', None)
            if hidden is None or previous.category_id in hidden or (not filter_hidden_category_items([previous.item], hidden, category_id=previous.category_id)):
                _debug('live recall ignored reason=previous-outside-visible-categories')
                return
            if self._pending_engine_reference is not None or (self._live_reconnecting and self._reconnect_decoder_released):
                self.show_info()
                return
            if not hasattr(self, '_live_recall_base_count'):
                self._live_recall_base_count = len(self._entries)
            self._entries.append(previous.item)
            index = len(self._entries) - 1
            self._live_recall_extra_keys.add(previous_key)
        if index == self.current_index or self._pending_channel_index == index:
            return
        _debug('live recall requested target_index={}'.format(index))
        self._switch_channel(0, target_index=index)

    def _request_catalog_player_window(self, direction):
        if getattr(self._entries, 'full_category', False):
            return False
        owner = getattr(self, '_playback_owner', None)
        native = getattr(owner, '_movie_native_window', None)
        catalog = getattr(owner, '_movie_browser_catalog', None) or native
        if catalog is None or getattr(self.item, 'content_type', '') != 'movie':
            return False
        if getattr(self, '_catalog_player_request', None) is not None:
            return True
        target = self._catalog_player_base + self.current_index + direction
        if 0 <= self.current_index + direction < len(self._entries):
            return False
        request = {'done': False, 'page': None, 'target': target}
        self._catalog_player_request = request
        timer = getattr(self, '_catalog_player_timer', None)
        if timer is None:
            timer = self._catalog_player_timer = eTimer()
            _connect_timer(timer, self._poll_catalog_player_window)
            self.onClose.append(timer.stop)
        category = str(getattr(owner, '_movie_loaded_category_id', '') or '')
        hidden = getattr(owner, '_hidden_category_ids', ())
        token = _WorkerToken(WORKER_LIST_TIMEOUT_SECONDS)
        previous = getattr(self, '_catalog_player_token', None)
        _cancel_worker_token(previous)
        self._catalog_player_token = token
        if not getattr(self, '_catalog_player_close_hook', False):
            self._catalog_player_close_hook = True
            self.onClose.append(lambda: _cancel_worker_token(getattr(self, '_catalog_player_token', None)))
        base = max(0, (target - 70) // 70 * 70)

        def load():
            try:
                if native is not None:
                    while target >= native.count and native.has_more:
                        rows = _bounded_client_call(self.client, token, lambda: self.client.load_more_items('movie', category))
                        rows = filter_hidden_category_items(rows, hidden, category)
                        native.append(rows, owner._client_has_more_items('movie', category), token)
                    if target < 0:
                        target_index = native.count - 1
                    elif target >= native.count and (not native.has_more):
                        target_index = 0
                    else:
                        target_index = target
                    native_base = max(0, (target_index - 70) // 70 * 70)
                    page = native.read_window(category, offset=native_base, size=240, token=token)
                    request.update(page=page, base=native_base, target=target_index)
                elif target < 0:
                    cursor, count, tail = (0, 0, [])
                    while True:
                        token.check()
                        page = catalog.index.browse(category, after=cursor, page_size=80, hidden_category_ids=hidden, token=token, include_total=False)
                        tail.extend(page.items)
                        count += len(page.items)
                        tail = tail[-240:]
                        if not page.has_more or not page.items:
                            break
                        cursor = page.next_cursor
                    tail_base = max(0, (count - 240) // 70 * 70)
                    page = catalog.read_window(category, offset=tail_base, size=240, hidden_category_ids=hidden, token=token)
                    if tail_base + len(page.items) < count:
                        tail_base += 70
                        page = catalog.read_window(category, offset=tail_base, size=240, hidden_category_ids=hidden, token=token)
                    request.update(page=page, base=tail_base, target=count - 1)
                else:
                    page = catalog.read_window(category, offset=base, size=240, hidden_category_ids=hidden, token=token)
                    if target >= base + len(page.items) and (not page.has_more):
                        page = catalog.read_window(category, offset=0, size=240, hidden_category_ids=hidden, token=token)
                        request.update(base=0, target=0)
                    else:
                        request['base'] = base
                    request['page'] = page
            except Exception as error:
                _debug('catalogue playback window failed {}'.format(error.__class__.__name__))
            finally:
                request['done'] = True
        try:
            threading.Thread(target=load, name='GT-PlayerCatalogue', daemon=True).start()
        except (OSError, RuntimeError):
            self._catalog_player_request = None
            return True
        timer.start(50, True)
        return True

    def _poll_catalog_player_window(self):
        request = getattr(self, '_catalog_player_request', None)
        if self._closed or request is None:
            return
        if not request['done']:
            self._catalog_player_timer.start(50, True)
            return
        self._catalog_player_request = None
        page = request['page']
        if page is None or not page.items:
            return
        old_id = str(self.item.stream_id)
        self._entries = list(page.items)
        self._catalog_player_base = request['base']
        target = max(0, min(request['target'] - request['base'], len(self._entries) - 1))
        self.current_index = next((i for i, item in enumerate(self._entries) if str(item.stream_id) == old_id), -1)
        self._pending_channel_index = None
        owner = getattr(self, '_playback_owner', None)
        if owner is not None and (not getattr(owner, '_closed', False)):
            owner._movie_entries = owner._movie_catalog_entries = list(page.items)
            owner._movie_window_base = request['base']
            owner._movie_selected_index = target
            owner._movie_known_ids = {str(item.stream_id) for item in page.items}
            owner._movie_browse_cursor = page.next_cursor
            owner._movie_has_more = page.has_more
            owner._movie_poster_page_key = None
        self._switch_channel(0, target_index=target)

    def _switch_channel(self, step, target_index=None):
        if not self._entries or self.client is None:
            self.show_info()
            return
        if getattr(self, '_live_reconnecting', False) and getattr(self, '_reconnect_decoder_released', False):
            self.show_info()
            return
        if self._pending_engine_reference is not None:
            self.show_info()
            return
        request_window = getattr(self, '_request_catalog_player_window', None)
        if target_index is None and callable(request_window) and request_window(1 if step > 0 else -1):
            return
        direction = (1 if step > 0 else -1) if target_index is None else 0
        now = time.monotonic()
        if direction and direction == self._last_channel_key_step and (self._last_channel_key_at is not None):
            elapsed_ms = (now - self._last_channel_key_at) * 1000
            if elapsed_ms < CHANNEL_KEY_DUPLICATE_GUARD_MS:
                _debug('live channel key bounce ignored direction={} elapsed_ms={}'.format(direction, int(elapsed_ms)))
                return
        self._last_channel_key_step = direction
        self._last_channel_key_at = now
        had_pending_target = self._pending_channel_index is not None
        base_index = self._pending_channel_index if self._pending_channel_index is not None else self.current_index
        if len(self._entries) < 2:
            self.show_info()
            return
        if target_index is None:
            count = getattr(self, '_live_recall_base_count', len(self._entries))
            if not count:
                return
            next_index = (base_index + direction) % count if base_index < count else 0 if direction > 0 else count - 1
        else:
            try:
                next_index = int(target_index)
            except (TypeError, ValueError, OverflowError):
                return
            if not 0 <= next_index < len(self._entries):
                return
        if callable(getattr(self, '_cancel_live_startup', None)) and self._cancel_live_startup(restore_released=True) is False:
            self.show_info()
            return
        _debug('live channel key accepted direction={} current={} base={} target={}'.format(direction, self.current_index, base_index, next_index))
        self._stop_live_auxiliary_timers()
        self._reset_picon_display()
        reset_reconnect = getattr(self, '_reset_live_reconnect', None)
        if callable(reset_reconnect):
            reset_reconnect()
        else:
            try:
                self._reconnect_timer.stop()
            except Exception:
                pass
            _cancel_worker_token(getattr(self, '_reconnect_link_token', None))
            self._reconnect_link_token = None
        with self._channel_link_lock:
            _cancel_worker_token(self._channel_link_token)
            self._channel_link_token = None
            self._channel_link_pending = False
            self._channel_link_ready = False
            self._channel_link_url = ''
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
            self._after_zap()
            return
        self._pending_channel_index = next_index
        self._new_player_stream_diag('channel-key')
        self._generation += 1
        self._channel_key_accepted_at = now
        next_item = self._entries[next_index]
        self._info_dialog['channel_number'].setText('' if live_channel_key(next_item) in getattr(self, '_live_recall_extra_keys', ()) else '{:02d}'.format(next_index + 1))
        self._set_info_channel_name(next_item.name)
        self._set_static_info_epg('epg_now', _('Changing channel...'))
        self._set_static_info_epg('epg_next', '')
        self._clear_player_progress()
        self.show_info()
        cached_ready = self._prepare_cached_channel_link(next_index, next_item)
        if not cached_ready and (not had_pending_target):
            self._start_channel_link_resolution(next_index, next_item)
        self._channel_switch_timer.start(CHANNEL_SWITCH_DEBOUNCE_MS, True)

    def _prepare_cached_channel_link(self, next_index, next_item):
        resolver = getattr(self.client, 'cached_playback_url', None)
        if not callable(resolver):
            return False
        try:
            url = str(resolver(next_item) or '').strip()
        except Exception:
            url = ''
        if not url:
            return False
        self._player_stream_diag('link-result', 'outcome=cached')
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
        asynchronous = callable(getattr(self.client, 'refresh_playback_url', None))
        if not asynchronous:
            return False
        try:
            client_timeout = int(self.client.timeout)
        except (AttributeError, TypeError, ValueError, OverflowError):
            client_timeout = CHANNEL_LINK_TIMEOUT_SECONDS
        link_timeout = max(2, min(CHANNEL_LINK_TIMEOUT_SECONDS, client_timeout))
        token = _WorkerToken(link_timeout)
        with self._channel_link_lock:
            if self._channel_link_pending and self._channel_link_generation == self._generation and (self._channel_link_index == next_index):
                return True
            self._clear_channel_link_state(keep_timing=True)
            self._channel_link_pending = True
            self._channel_link_ready = False
            self._channel_link_url = ''
            self._channel_link_generation = self._generation
            self._channel_link_index = next_index
            self._channel_link_token = token
            self._channel_link_deadline = token.deadline
            generation = self._channel_link_generation
        self._player_stream_diag('link-request', 'outcome=started reason=channel-key')
        worker = threading.Thread(target=self._resolve_channel_link, args=(generation, next_index, next_item, token))
        worker.daemon = True
        worker.start()
        return True

    def _commit_channel_switch(self):
        if self._closed or self._pending_channel_index is None:
            return
        next_index = self._pending_channel_index
        next_item = self._entries[next_index]
        asynchronous = callable(getattr(self.client, 'refresh_playback_url', None))
        next_url = ''
        channel_link_ready_at = 0.0
        if asynchronous:
            with self._channel_link_lock:
                link_matches = bool(self._channel_link_generation == self._generation and self._channel_link_index == next_index)
                link_pending = bool(link_matches and self._channel_link_pending)
                link_ready = bool(link_matches and self._channel_link_ready)
                link_deadline = self._channel_link_deadline if link_matches else 0.0
            if not link_pending:
                self._start_channel_link_resolution(next_index, next_item)
                self._channel_switch_timer.start(CHANNEL_LINK_POLL_MS, True)
                return
            if not link_ready and time.monotonic() < link_deadline:
                self._channel_switch_timer.start(CHANNEL_LINK_POLL_MS, True)
                return
            with self._channel_link_lock:
                link_matches = bool(self._channel_link_generation == self._generation and self._channel_link_index == next_index)
                if link_matches and (not self._channel_link_ready):
                    self._channel_link_ready = True
                    self._channel_link_url = ''
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
                    raise ContentError('Stream link unavailable')
            else:
                next_url = self.client.playback_url(next_item)
                self._player_stream_diag('link-result', 'outcome={}'.format('ready' if next_url else 'empty'))
            next_reference = build_extplayer_reference(next_url, next_item.name, getattr(next_item, 'content_type', 'live'), service_type=self._active_live_service_type if getattr(next_item, 'content_type', '') == 'live' else None)
        except Exception as error:
            self._player_stream_diag('start-failed', 'reason=link-or-reference error_type={}'.format(error.__class__.__name__))
            self._clear_channel_link_state()
            self._after_zap()
            self._set_static_info_epg('epg_now', _('Could not open the channel.'))
            self._set_static_info_epg('epg_next', _('Keeping the current stream'))
            self.show_info()
            return
        navigation = getattr(self.session, 'nav', None)
        if navigation is None:
            self._player_stream_diag('start-failed', 'reason=navigation-unavailable')
            self._clear_channel_link_state()
            self._after_zap()
            return
        self._player_stream_diag('service-reference', 'outcome=built actual_engine={}'.format(reference_service_type(next_reference, 4097)))
        self._save_resume_position()
        subtitle_controller = getattr(self, '_subtitle_controller', None)
        previous_subtitle = subtitle_controller.snapshot_state() if subtitle_controller is not None else None
        if subtitle_controller is not None:
            subtitle_controller.reset_for_service_change(resume_search=True)
        try:
            self._play_subtitle_service(navigation, next_reference)
        except Exception as error:
            self._player_stream_diag('play-service', 'outcome=error reason=channel-key error_type={}'.format(error.__class__.__name__))
            if previous_subtitle is not None and subtitle_controller is not None:
                subtitle_controller.restore_state(previous_subtitle)
            _debug('live soft zap failed id={} error={}'.format(_debug_identifier(getattr(next_item, 'stream_id', '')), error.__class__.__name__))
            self._clear_channel_link_state()
            self._after_zap()
            self._set_static_info_epg('epg_now', _('Could not open the channel.'))
            self._set_static_info_epg('epg_next', _('Keeping the current stream'))
            self.show_info()
            return
        key_to_link_ms = int(round((channel_link_ready_at - self._channel_key_accepted_at) * 1000)) if channel_link_ready_at > 0 and self._channel_key_accepted_at > 0 else 0
        key_to_play_ms = int(round((time.monotonic() - self._channel_key_accepted_at) * 1000)) if self._channel_key_accepted_at > 0 else 0
        self._clear_channel_link_state()
        self.current_index = next_index
        self.current_item = next_item
        self.reference = next_reference
        if getattr(next_item, 'content_type', '') == 'live':
            remember_live_channel(self.client, next_item, resume_account_scope(self.client), category_id=None if live_channel_key(next_item) in getattr(self, '_live_recall_extra_keys', ()) else getattr(self, '_live_recall_category_id', None), context=None if live_channel_key(next_item) in getattr(self, '_live_recall_extra_keys', ()) else getattr(self, '_live_list_context', None))
            self._active_live_service_type = reference_service_type(next_reference, 4097)
            self._watch_player_stream_diag(navigation, next_reference, 'channel-key')
        _debug('playback channel changed content={} engine={} reason=user key_to_link_ms={} key_to_play_ms={}'.format(getattr(next_item, 'content_type', 'unknown'), reference_service_type(next_reference, 4097), key_to_link_ms, key_to_play_ms))
        self._resume_key = resume_key(self.client, next_item)
        self._resume_start_position = 0
        self._resume_saved = False
        arm_startup = getattr(self, '_arm_live_startup', None)
        if callable(arm_startup):
            arm_startup()
        self._after_zap(refresh_epg=True)

    def _resolve_channel_link(self, generation, index, item, token=None):
        resolver = getattr(self.client, 'playback_url', None)
        if not callable(resolver):
            resolver = getattr(self.client, 'refresh_playback_url', None)
        try:
            url = str(_bounded_client_call(self.client, token, lambda: resolver(item) if callable(resolver) else '') or '').strip()
        except Exception:
            url = ''
        with self._channel_link_lock:
            if self._closed or (token is not None and (not token.active())) or self._generation != generation or (self._pending_channel_index != index) or (self._channel_link_generation != generation) or (self._channel_link_index != index):
                return
            self._channel_link_url = url
            self._channel_link_ready = True
            self._channel_link_ready_at = time.monotonic()
            diagnostic = (self._stream_diag_play_id, self._stream_diag_started_at)
        self._player_stream_diag('link-result', 'outcome={}'.format('ready' if url else 'empty'), play_id=diagnostic[0], started_at=diagnostic[1])

    def _clear_channel_link_state(self, keep_timing=False):
        with self._channel_link_lock:
            _cancel_worker_token(self._channel_link_token)
            self._channel_link_token = None
            self._channel_link_pending = False
            self._channel_link_ready = False
            self._channel_link_url = ''
            self._channel_link_generation = -1
            self._channel_link_index = -1
            self._channel_link_deadline = 0.0
            if not keep_timing:
                self._channel_key_accepted_at = 0.0
                self._channel_link_ready_at = 0.0

    def _start_info_hide_timer(self):
        """Apply the appearance timeout to every automatic banner-hide path."""
        self._hide_timer.stop()
        if self._closed or self._seeking or self._paused:
            return
        timeout = self._infobar_timeout
        if timeout:
            self._hide_timer.start(timeout * 1000, True)

    def show_info(self):
        if self._handoff_first_show_pending:
            self._handoff_info_requested = True
            self._handoff_info_defer_metadata = bool(getattr(self, '_defer_live_metadata_once', False))
            _trace_fullscreen_osd(self, 'infobar-show-deferred')
            return
        if getattr(self, '_subtitle_menu_open', False) or getattr(self, '_aspect_menu_open', False) or getattr(self, '_subtitle_message_tokens', None):
            return
        if self._seeking or self._paused:
            self._show_seek_overlay(N_('PAUSED') if self._paused else N_('SEEK MODE'), N_('LEFT / RIGHT to select time   •   OK to continue'))
            return
        self._hide_seek_overlay()
        if self._is_live_item():
            self._clock_tick()
            if not self._live_transition_in_progress():
                self._render_live_snapshot_epg(now=time.time())
                if not getattr(self, '_defer_live_metadata_once', False):
                    self._refresh_live_metadata_once()
                    if not self._closed and self._started and not self._picon_visible:
                        retry = self._live_picon_status()
                        if retry.phase == 'decode' or retry.can_wait(time.monotonic()):
                            self._picon_timer.start(1, True)
        self._info_visible = True
        self._refresh_infobar_telemetry()
        self._set_subtitle_infobar_visible(True)
        self._info_dialog.show()
        self._start_player_weather()
        self._sync_picon_visibility()
        self._start_info_hide_timer()

    def hide_info(self):
        if self._handoff_first_show_pending:
            self._handoff_info_requested = False
            return
        if self._closed or self._seeking or self._paused:
            return
        try:
            self._hide_timer.stop()
        except Exception:
            pass
        self._hide_seek_overlay()
        self._info_visible = False
        self._infobar_telemetry_timer.stop()
        self._network_rate_sampler.reset()
        self._set_subtitle_infobar_visible(False)
        dialog = getattr(self, '_info_dialog', None)
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
            self.session.openWithCallback(finished, MessageBox, text, MessageBox.TYPE_INFO, timeout=SUBTITLE_MESSAGE_TIMEOUT_SECONDS)
            return
        except Exception:
            self._subtitle_message_tokens.discard(token)
        try:
            self._set_static_info_epg('epg_now', text)
            self._set_static_info_epg('epg_next', '')
            self.show_info()
        except Exception:
            pass

    def _subtitle_changed(self, selected_track):
        """Keep the player banner from covering newly enabled subtitles."""
        self._subtitle_sync_keys.cancel()
        self._dismiss_subtitle_sync_offset()
        if selected_track is not None:
            self.hide_info()
        controller = getattr(self, '_subtitle_controller', None)
        if controller is None or getattr(controller, '_closed', False) or getattr(controller, '_resume_state_resetting', False):
            return
        self._resume_subtitle_pending_key = ''
        self._resume_subtitle_read_job = None
        self._resume_subtitle_read_state = None
        self._resume_subtitle_restore_attempts = 0
        self._remember_resume_subtitle(mark_disabled=True)

    def _remember_resume_subtitle(self, mark_disabled=False):
        if self._closed or getattr(self.current_item, 'content_type', '') not in ('movie', 'series'):
            return
        store = getattr(self, '_subtitle_resume_store', None)
        controller = getattr(self, '_subtitle_controller', None)
        if store is None or controller is None or (not self._resume_key):
            return
        try:
            state = controller.snapshot_resume_state()
            if state is None and mark_disabled:
                state = {'kind': 'disabled'}
            if state is not None and (not store.remember_async(self._resume_key, state)):
                _debug('resume subtitle save failed')
        except Exception:
            _debug('resume subtitle save failed')

    def _resume_subtitle_context(self):
        controller = getattr(self, '_subtitle_controller', None)
        return (self._generation, self._resume_key, id(controller), getattr(controller, '_service_generation', None), getattr(getattr(controller, 'online', None), '_generation', None))

    def _restore_resume_subtitle(self):
        key = getattr(self, '_resume_subtitle_pending_key', '')
        if not key:
            return False
        controller = getattr(self, '_subtitle_controller', None)
        store = getattr(self, '_subtitle_resume_store', None)
        if key != self._resume_key or controller is None or store is None:
            self._resume_subtitle_pending_key = ''
            return False
        if getattr(self, '_subtitle_menu_open', False):
            return True
        context = self._resume_subtitle_context()
        cached = getattr(self, '_resume_subtitle_read_state', None)
        if cached is not None and cached[0] == context:
            return self._apply_resume_subtitle_state(cached[1])
        if getattr(self, '_resume_subtitle_read_job', None) == context:
            return True
        reader = getattr(self, '_resume_subtitle_reader', None)
        if reader is None:
            reader = self._resume_subtitle_reader = SourceLoader(eTimer(), _connect_timer, self._resume_subtitle_read_done)
            self.onClose.append(reader.close)
        self._resume_subtitle_read_job = context

        def load():
            state = store.get(key, nonblocking=True)
            prepare = getattr(controller, 'prepare_resume_state', None)
            if callable(prepare) and state is not None:
                state = prepare(state)
            return (context, state)
        reader.request(load)
        return True

    def _resume_subtitle_read_done(self, value, error):
        context = getattr(self, '_resume_subtitle_read_job', None)
        self._resume_subtitle_read_job = None
        current = self._resume_subtitle_context()
        if self._closed or context is None or context != current or (not getattr(self, '_resume_subtitle_pending_key', '')) or (getattr(self, '_web_suspended', None) is not None):
            if context is not None and context[:4] == current[:4] and (context != current):
                self._resume_subtitle_pending_key = ''
                self._resume_subtitle_read_state = None
            return
        if error is not None or value is None or value[0] != context:
            value = (context, None)
        self._resume_subtitle_read_state = value
        self._apply_resume_subtitle_state(value[1])
        if not getattr(self, '_resume_subtitle_pending_key', ''):
            self._start_automatic_subtitle_search()

    def _apply_resume_subtitle_state(self, state):
        if getattr(self, '_subtitle_menu_open', False):
            return True
        controller = getattr(self, '_subtitle_controller', None)
        try:
            if state is not None and state.get('kind') == 'pending':
                self._resume_subtitle_read_state = None
                self._subtitle_auto_timer.start(200, True)
                return True
            if state is not None and state.get('kind') in ('embedded', 'external'):
                ready = bool(controller.embedded.available_tracks()) if state['kind'] == 'embedded' else controller.bridge._play_position_available()
                attempts = getattr(self, '_resume_subtitle_restore_attempts', 0)
                if not ready and attempts < 6:
                    self._resume_subtitle_restore_attempts = attempts + 1
                    self._subtitle_auto_timer.start(500, True)
                    return True
            self._resume_subtitle_pending_key = ''
            self._resume_subtitle_read_state = None
            self._resume_subtitle_restore_attempts = 0
            if state is not None and controller.restore_resume_state(state):
                _debug('resume subtitle restored kind={}'.format(state['kind']))
                return True
        except Exception:
            pass
        self._resume_subtitle_pending_key = ''
        self._resume_subtitle_read_state = None
        self._resume_subtitle_restore_attempts = 0
        _debug('resume subtitle unavailable; using configured search mode')
        return False

    def _set_subtitle_infobar_visible(self, visible):
        controller = getattr(self, '_subtitle_controller', None)
        setter = getattr(controller, 'set_infobar_visible', None)
        if callable(setter):
            try:
                setter(visible)
            except Exception:
                pass

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
        self._set_subtitle_infobar_visible(False)
        self._info_dialog.hide()

    def _subtitle_menu_closed(self):
        """Restore a pause/seek panel without changing decoder playback state."""
        was_open = getattr(self, '_subtitle_menu_open', False)
        generation = getattr(self, '_subtitle_menu_generation', None)
        self._subtitle_menu_open = False
        self._subtitle_menu_generation = None
        if was_open:
            self._restore_subtitle_overlay(generation)
            if not self._closed and getattr(self, '_resume_subtitle_pending_key', ''):
                self._subtitle_auto_timer.start(50, True)

    def _restore_subtitle_overlay(self, generation):
        if self._closed or generation != self._generation or self._subtitle_menu_open or self._subtitle_message_tokens:
            return
        if self._seeking:
            self.show_info()
        elif self._paused:
            self._show_seek_overlay(N_('PAUSED'), N_('PLAY / PAUSE to continue   •   STOP to close the video'))

    def _subtitle_playback_profile(self, item):
        """Collect decoder-local matching hints without exposing stream URLs."""
        release_parts = []
        title = ' '.join(str(getattr(item, 'name', '') or '').split())[:160]
        if title:
            release_parts.append(title)
        try:
            stream_path = urlsplit(reference_stream_url(getattr(self, 'reference', '')).split('#', 1)[0]).path
            filename = os.path.basename(stream_path)
            stem = os.path.splitext(filename)[0]
            stem = re.sub('%[0-9A-Fa-f]{2}', ' ', stem)
            stem = ' '.join(re.sub('[^A-Za-z0-9._+\\- ]+', ' ', stem).split())[:160]
            if len(stem) >= 3 and (not stem.isdigit()) and (not re.fullmatch('[0-9a-fA-F_-]{20,}', stem)) and (stem not in release_parts):
                release_parts.append(stem)
        except (AttributeError, TypeError, ValueError):
            pass
        navigation = getattr(self.session, 'nav', None)
        service = None
        if navigation is not None:
            getter = getattr(navigation, 'getCurrentService', None)
            if callable(getter):
                try:
                    service = getter()
                except Exception:
                    service = None
        try:
            info = service.info() if service is not None else None
        except Exception:
            info = None
        frame_rate = self._service_info_value(info, 'sFrameRate')
        width = self._service_info_value(info, 'sVideoWidth')
        height = self._service_info_value(info, 'sVideoHeight')
        if frame_rate <= 0:
            frame_rate = _read_proc_number('/proc/stb/vmpeg/0/framerate', 10)
        if width <= 0:
            width = _read_proc_number('/proc/stb/vmpeg/0/xres', 16)
        if height <= 0:
            height = _read_proc_number('/proc/stb/vmpeg/0/yres', 16)
        profile = {'release_hint': ' | '.join(release_parts)[:240]}
        if frame_rate > 0:
            profile['fps'] = round(frame_rate / 1000.0 if frame_rate > 1000 else float(frame_rate), 3)
        if width >= 3800 or height >= 2100:
            profile['resolution'] = 2160
        elif width >= 1850 or height >= 1050:
            profile['resolution'] = 1080
        elif width >= 1200 or height >= 700:
            profile['resolution'] = 720
        elif height >= 540:
            profile['resolution'] = 576
        elif height > 0:
            profile['resolution'] = 480
        try:
            seeker = self._raw_seek_interface()
            duration = self._seek_seconds_precise(seeker.getLength()) if seeker is not None else -1
        except Exception:
            duration = -1
        if duration > 0:
            profile['duration_seconds'] = round(float(duration), 3)
        return profile

    def _start_automatic_subtitle_search(self):
        """Wait briefly for decoder FPS before ranking automatic results."""
        if self._closed or self._subtitle_auto_generation != self._generation or getattr(self.current_item, 'content_type', '') not in ('movie', 'series'):
            return
        if self._restore_resume_subtitle() and getattr(self, '_resume_subtitle_pending_key', ''):
            return
        subtitle_controller = getattr(self, '_subtitle_controller', None)
        if subtitle_controller is not None:
            subtitle_controller.start_automatic_search()

    def _subtitle_search_metadata(self):
        item = self.current_item
        if getattr(item, 'content_type', '') == 'series':
            parent = getattr(item, 'favorite_parent', None)
            if parent is None:
                parent = getattr(self, '_favorite_parent', None)
            metadata = {'title': getattr(parent, 'subtitle_title', '') or getattr(item, 'subtitle_title', '') or getattr(parent, 'name', '') or getattr(item, 'name', self.title), 'year': getattr(parent, 'subtitle_year', '') or getattr(item, 'subtitle_year', '') or getattr(parent, 'year', '') or getattr(item, 'year', ''), 'season': getattr(item, 'season', ''), 'episode': getattr(item, 'episode', ''), 'content_type': 'series', 'episode_title': getattr(item, 'name', ''), 'tmdb_id': getattr(parent, 'subtitle_tmdb_id', '') or getattr(item, 'subtitle_tmdb_id', '') or getattr(parent, 'tmdb_id', '') or getattr(item, 'tmdb_id', ''), 'imdb_id': getattr(parent, 'subtitle_imdb_id', '') or getattr(item, 'subtitle_imdb_id', '') or getattr(parent, 'imdb_id', '') or getattr(item, 'imdb_id', '')}
        else:
            metadata = {'title': getattr(item, 'subtitle_title', '') or getattr(item, 'name', self.title), 'year': getattr(item, 'subtitle_year', '') or getattr(item, 'year', ''), 'content_type': 'movie', 'tmdb_id': getattr(item, 'subtitle_tmdb_id', '') or getattr(item, 'tmdb_id', ''), 'imdb_id': getattr(item, 'subtitle_imdb_id', '') or getattr(item, 'imdb_id', '')}
        profile_loader = getattr(self, '_subtitle_playback_profile', None)
        if callable(profile_loader):
            try:
                metadata.update(profile_loader(item))
            except Exception as error:
                log_event('subtitles', 'playback-profile-failed', error)
        return metadata

    def _pause_for_subtitle_search(self):
        """Pause VOD without leaving GT's high-z pause panel over search."""
        was_paused = self._paused
        if not was_paused:
            self.pause_playback()
        paused_by_search = not was_paused and self._paused
        self._hide_seek_overlay()
        self._info_visible = False
        self._set_subtitle_infobar_visible(False)
        try:
            self._info_dialog.hide()
        except Exception:
            pass
        return paused_by_search

    def _resume_after_subtitle_search(self, paused_by_search=True):
        if paused_by_search and self._paused:
            self.resume_playback()
            subtitle_controller = getattr(self, '_subtitle_controller', None)
            if subtitle_controller is not None and subtitle_controller.external_loaded:
                self.hide_info()
        elif self._paused:
            self._show_seek_overlay(N_('PAUSED'), N_('PLAY / PAUSE to continue   •   STOP to close the video'))

    def open_subtitle_selection(self):
        """Use decoder-only subtitles for live TV and the full menu for VOD."""
        if getattr(self, '_closed', False):
            return True
        if self._is_live_item():
            if not self._started or self._live_transition_in_progress() or self._media_menu_open or self._audio_menu_open or getattr(self, '_aspect_menu_open', False):
                return True
            self.hide_info()
            controller = getattr(self, '_subtitle_controller', None)
            if controller is not None:
                controller.open_embedded_selection()
            return True
        if getattr(self.current_item, 'content_type', '') not in ('movie', 'series', 'catchup'):
            self.show_info()
            return True
        self.hide_info()
        subtitle_controller = getattr(self, '_subtitle_controller', None)
        if subtitle_controller is not None:
            subtitle_controller.open_selection()
        return True

    def open_aspect_selection(self):
        """Open the installed image's aspect and display policies on GREEN."""
        if self._closed or not self._started or (not self._is_live_item()) or self._live_transition_in_progress() or self._media_menu_open or self._audio_menu_open or self._subtitle_menu_open or self._aspect_menu_open:
            return True
        expected_context = self._audio_selection_context()
        groups = aspect_groups()
        if not groups:
            self._show_subtitle_message(_('Aspect ratio') + ' • ' + _('Not available'))
            return True
        self._aspect_menu_open = True
        self.hide_info()

        def valid_context():
            return not self._closed and self._started and self._is_live_item() and (not self._live_transition_in_progress()) and (self._audio_selection_context() == expected_context)

        def finish(unavailable=False):
            self._aspect_menu_open = False
            if not valid_context():
                return
            if unavailable:
                self._show_subtitle_message(_('Aspect ratio') + ' • ' + _('Not available'))
            else:
                self.show_info()

        def open_choices(callback, title, choices, selected_index=0):
            try:
                from Screens.ChoiceBox import ChoiceBox
                try:
                    self.session.openWithCallback(callback, ChoiceBox, title, choices, selection=selected_index)
                except TypeError:
                    self.session.openWithCallback(callback, ChoiceBox, title, choices)
            except Exception:
                finish(unavailable=True)

        def group_selected(selection):
            if not selection or not valid_context():
                finish()
                return
            group = selection[1]
            if not any((group is known for known in groups)):
                finish()
                return
            current = next((candidate for candidate in aspect_groups() if candidate['field'] == group['field'] and candidate['element'] is group['element']), None)
            if current is None:
                finish(unavailable=True)
                return
            choices = [(label, value) for value, label in current['choices']]
            selected_index = next((index for index, (_label, value) in enumerate(choices) if str(value) == str(current['current'])), 0)

            def value_selected(choice):
                if not choice or not valid_context():
                    finish()
                    return
                applied = apply_aspect_choice(current['field'], current['element'], choice[1])
                finish(unavailable=not applied)
            open_choices(value_selected, _(current['title']), choices, selected_index)
        choices = []
        for group in groups:
            label = next((label for value, label in group['choices'] if str(value) == str(group['current'])), str(group['current']))
            choices.append((_(group['title']) + ' • ' + label, group))
        open_choices(group_selected, _('Aspect ratio'), choices)
        return True

    @staticmethod
    def _format_play_time(seconds):
        try:
            seconds = max(0, int(seconds))
        except (TypeError, ValueError):
            seconds = 0
        hours, remainder = divmod(seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        return '{:02d}:{:02d}:{:02d}'.format(hours, minutes, seconds)

    def _current_service(self):
        if getattr(self, '_web_suspended', None) is not None:
            return None
        navigation = getattr(self.session, 'nav', None)
        if navigation is None:
            return None
        getter = getattr(navigation, 'getCurrentService', None)
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def _raw_seek_interface(self):
        """Return decoder timing even when a linear stream is not seekable."""
        service = self._current_service()
        if service is None:
            return None
        getter = getattr(service, 'seek', None)
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def _seek_interface(self):
        seeker = self._raw_seek_interface()
        if seeker is None:
            return None
        try:
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

    @staticmethod
    def _seek_seconds_precise(result):
        """Convert the 90 kHz decoder clock without discarding milliseconds."""
        try:
            error, ticks = result
            if int(error) != 0:
                return -1
            position = float(ticks) / 90000.0
            return position if math.isfinite(position) and position >= 0 else -1
        except (TypeError, ValueError, OverflowError):
            return -1

    def _subtitle_position(self):
        """Use the native decoder clock, including original YouTube streams."""
        seeker = self._raw_seek_interface()
        if seeker is None:
            return -1
        try:
            return self._seek_seconds_precise(seeker.getPlayPosition())
        except Exception:
            return -1

    def _seek_position(self, seeker=None):
        duration = youtube_duration_seconds(getattr(self, 'current_item', None))
        seeker = seeker or (self._raw_seek_interface() if duration > 0 else self._seek_interface())
        if seeker is None:
            return (-1, duration if duration > 0 else -1)
        try:
            position = self._seek_seconds(seeker.getPlayPosition())
        except Exception:
            position = -1
        try:
            length = self._seek_seconds(seeker.getLength())
        except Exception:
            length = -1
        if length <= 0 and duration > 0:
            length = duration
        if position >= 0 and length > 0:
            self._seek_ready = True
        return (position, length)

    def _show_seek_overlay(self, state, hint):
        if getattr(self, '_subtitle_menu_open', False) or getattr(self, '_subtitle_message_tokens', None):
            return
        seeker = self._seek_interface()
        position, length = self._seek_position(seeker)
        if self._seeking:
            position = self._seek_target
            length = self._seek_length
        safe_length = max(0, length)
        ratio = min(1.0, max(0.0, float(position) / float(safe_length))) if safe_length > 0 and position >= 0 else 0.0
        self._info_dialog['seek_state'].setText(_(state))
        self._info_dialog['seek_times'].setText('{}   /   {}'.format(self._format_play_time(position), self._format_play_time(safe_length)))
        self._info_dialog['seek_hint'].setText(_(hint))
        try:
            self._info_dialog['seek_progress_fill'].instance.resize(eSize(max(1, int(round(self._seek_width * ratio))), self._seek_height))
        except Exception:
            pass
        for name in ('seek_panel', 'seek_state', 'seek_progress_bg', 'seek_progress_fill', 'seek_times', 'seek_hint'):
            self._info_dialog[name].show()
        self._info_dialog.show()
        self._info_visible = True
        self._set_subtitle_infobar_visible(True)

    def _hide_seek_overlay(self):
        for name in ('seek_panel', 'seek_state', 'seek_progress_bg', 'seek_progress_fill', 'seek_times', 'seek_hint'):
            self._info_dialog[name].hide()

    def _seek_not_ready(self):
        if not self._seek_ready and time.monotonic() < self._seek_startup_deadline:
            self._cancel_seek_verification()
            self._seeking = False
            self._show_seek_overlay(N_('Loading...'), N_('Please wait'))
            self._start_info_hide_timer()
            return
        self._seek_unavailable()

    def _seek_unavailable(self):
        self._cancel_seek_verification()
        self._seeking = False
        self._show_seek_overlay(N_('THIS STREAM CANNOT BE SEEKED'), N_('The server or playback format does not support seeking'))
        self._start_info_hide_timer()

    def _begin_or_adjust_seek(self, delta_seconds):
        if getattr(self.current_item, 'content_type', '') == 'live':
            self._seek_unavailable()
            return
        if not self._seeking and not (self._vod_seek_enabled() and self._pending_seek_verification is not None):
            self._cancel_seek_verification()
        seeker = self._seek_interface()
        position, length = self._seek_position(seeker)
        if seeker is None or position < 0 or length <= 0:
            self._seek_not_ready()
            return
        if not self._seeking:
            self._seeking = True
            pending = self._pending_seek_verification
            self._seek_target = (pending.get('queued_target') if pending.get('queued_target') is not None else pending['target']) if self._vod_seek_enabled() and pending is not None else position
            self._seek_length = length
            try:
                self._hide_timer.stop()
            except Exception:
                pass
        self._seek_target = min(max(0, self._seek_target + int(delta_seconds)), self._seek_length)
        direction = N_('SEEK FORWARD') if delta_seconds >= 0 else N_('SEEK BACKWARD')
        self._show_seek_overlay(direction, N_('LEFT / RIGHT to select time   •   OK to continue   •   EXIT to cancel'))

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
        if distance == 0:
            return True
        required = min(abs(distance), max(3, int(abs(distance) / 2)))
        if distance > 0:
            return int(origin) + required <= int(position) <= int(target) + 5
        return int(target) - 5 <= int(position) <= int(origin) - required

    def _prepare_subtitle_seek(self, target, origin=None):
        watcher = getattr(self, '_youtube_end_watcher', None)
        if watcher is not None:
            watcher.reset_position()
        controller = getattr(self, '_subtitle_controller', None)
        preparer = getattr(controller, 'prepare_seek', None)
        if callable(preparer):
            try:
                preparer(target, origin_seconds=origin)
            except Exception as error:
                log_event('subtitles', 'seek-anchor-failed', error)

    def _complete_subtitle_seek(self, position=None):
        controller = getattr(self, '_subtitle_controller', None)
        callback = getattr(controller, 'after_seek', None)
        if callable(callback):
            try:
                callback(position)
            except Exception as error:
                log_event('subtitles', 'seek-resync-failed', error)

    def _cancel_seek_verification(self, resync_subtitles=True):
        had_pending = self._pending_seek_verification is not None
        self._pending_seek_verification = None
        timer = getattr(self, '_seek_verify_timer', None)
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        if had_pending and resync_subtitles:
            controller = getattr(self, '_subtitle_controller', None)
            callback = getattr(controller, 'cancel_seek', None)
            if callable(callback):
                try:
                    callback()
                except Exception as error:
                    log_event('subtitles', 'seek-cancel-resync-failed', error)

    def _schedule_seek_verification(self, origin, target, method, startup_resume=False):
        self._pending_seek_verification = {'origin': int(origin), 'target': int(target), 'method': str(method), 'startup_resume': bool(startup_resume), 'checks': 0, 'async_deadline': time.monotonic() + SEEK_ASYNC_VERIFY_TIMEOUT_SECONDS if serviceapp_uses_async_seek(reference_service_type(self.reference)) else None, 'stream_id': str(getattr(self.current_item, 'stream_id', '') or '')}
        if not startup_resume:
            if self._resume_start_position > 0 and getattr(self, '_resume_bookmark_guard', None) is None:
                self._arm_resume_bookmark_guard()
            self._resume_start_position = 0
            self._resume_timer.stop()
            guard = getattr(self, '_resume_bookmark_guard', None)
            if guard is not None:
                guard['manual_seek'] = {'origin': int(origin), 'target': int(target)}
        if self._vod_seek_enabled():
            self._arm_vod_seek(self._pending_seek_verification)
        self._seek_verify_timer.start(SEEK_VERIFY_DELAY_MS, True)

    def _request_seek(self, seeker, origin, target, startup_resume=False):
        """Seek absolutely first, falling back immediately on explicit failure."""
        origin = int(origin)
        target = int(target)
        if self._vod_seek_enabled() and self._queue_vod_seek(target):
            return True
        if seeker is None:
            return False
        absolute = getattr(seeker, 'seekTo', None)
        if absolute is not None:
            try:
                result = absolute(target * 90000)
                if not self._seek_call_failed(result):
                    self._schedule_seek_verification(origin, target, 'absolute', startup_resume)
                    self._prepare_subtitle_seek(target, origin=origin)
                    return True
            except Exception as error:
                _debug('absolute seek request failed error={}'.format(error.__class__.__name__))
        relative = getattr(seeker, 'seekRelative', None)
        delta = target - origin
        if relative is None or delta == 0:
            if delta == 0 and (not startup_resume):
                self._resume_start_position = 0
                self._resume_bookmark_guard = None
                self._resume_timer.stop()
            return delta == 0
        try:
            result = relative(-1 if delta < 0 else 1, abs(delta) * 90000)
            if self._seek_call_failed(result):
                return False
        except Exception as error:
            _debug('relative seek request failed error={}'.format(error.__class__.__name__))
            return False
        self._schedule_seek_verification(origin, target, 'relative', startup_resume)
        self._prepare_subtitle_seek(target, origin=origin)
        return True

    def _verify_seek_result(self):
        state = self._pending_seek_verification
        if state is None or self._closed:
            return
        if 'guard' in state:
            self._verify_vod_seek(state)
            return
        current_stream_id = str(getattr(self.current_item, 'stream_id', '') or '')
        if current_stream_id != state['stream_id']:
            self._cancel_seek_verification()
            return
        seeker = self._seek_interface()
        position = self._seek_position(seeker)[0]
        if self._seek_reached(position, state['origin'], state['target']):
            self._resume_bookmark_position_ready(position)
            self._cancel_seek_verification(resync_subtitles=False)
            self._playback_tick()
            observed = self._subtitle_position()
            self._complete_subtitle_seek(observed if observed >= 0 else position)
            return
        async_deadline = state['async_deadline']
        if async_deadline is not None:
            if time.monotonic() < async_deadline:
                self._seek_verify_timer.start(SEEK_VERIFY_DELAY_MS, True)
            else:
                _debug('ServiceApp seek position confirmation timed out')
                self._cancel_seek_verification()
            return
        state['checks'] += 1
        if state['checks'] < SEEK_VERIFY_MAX_CHECKS:
            self._seek_verify_timer.start(SEEK_VERIFY_DELAY_MS, True)
            return
        if state['method'] == 'absolute' and seeker is not None:
            relative = getattr(seeker, 'seekRelative', None)
            fallback_origin = position if position >= 0 else state['origin']
            delta = state['target'] - fallback_origin
            if relative is not None and delta:
                try:
                    result = relative(-1 if delta < 0 else 1, abs(delta) * 90000)
                    if not self._seek_call_failed(result):
                        state['origin'] = fallback_origin
                        state['method'] = 'relative'
                        state['checks'] = 0
                        self._prepare_subtitle_seek(state['target'], origin=fallback_origin)
                        self._seek_verify_timer.start(SEEK_VERIFY_DELAY_MS, True)
                        return
                except Exception as error:
                    _debug('verified relative fallback failed error={}'.format(error.__class__.__name__))
        self._cancel_seek_verification()
        self._seek_unavailable()

    def _seek_immediately(self, delta_seconds):
        if getattr(self.current_item, 'content_type', '') == 'live':
            self._seek_unavailable()
            return
        pending = self._pending_seek_verification
        if self._vod_seek_enabled() and isinstance(pending, dict) and 'guard' in pending and self._vod_seek_context(pending):
            base = pending.get('queued_target') if pending.get('queued_target') is not None else pending['target']
            target = min(max(0, int(base) + int(delta_seconds)), max(0, int(pending['length'])))
            self._queue_vod_seek(target)
            self.show_info()
            return
        seeker = self._seek_interface()
        position, length = self._seek_position(seeker)
        if seeker is None or position < 0 or length <= 0:
            self._seek_not_ready()
            return
        delta_seconds = int(delta_seconds)
        pending = self._pending_seek_verification
        base_position = int(pending['target']) if pending is not None and str(pending.get('stream_id', '')) == str(getattr(self.current_item, 'stream_id', '') or '') else position
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

    def _direction_press(self, direction):
        if self._closed or not self._started or self._minute_seek_open or (self._pending_engine_reference is not None):
            return True
        if self._held_direction == direction:
            return True
        self._held_direction = direction
        self._held_direction_long = False
        timer = getattr(self, '_direction_hold_timer', None)
        if timer is not None:
            timer.stop()
            timer.start(DIRECTION_HOLD_MS, True)
        return True

    def _direction_release(self, direction):
        if self._held_direction != direction:
            return True
        timer = getattr(self, '_direction_hold_timer', None)
        if timer is not None:
            timer.stop()
        was_long = self._held_direction_long
        self._held_direction = None
        self._held_direction_long = False
        if not was_long and (not self._minute_seek_open) and (not self._closed):
            (self.seek_forward if direction > 0 else self.seek_backward)()
        return True

    def _direction_hold_timeout(self):
        direction = self._held_direction
        log_event('minute_seek', 'hold-timeout direction={} opened={}'.format(direction, int(self._minute_seek_open)))
        if direction in (-1, 1) and (not self._held_direction_long):
            self._direction_long(direction)

    def _direction_long(self, direction):
        timer = getattr(self, '_direction_hold_timer', None)
        if timer is not None:
            timer.stop()
        if self._closed or not self._started or self._minute_seek_open or (self._pending_engine_reference is not None):
            log_event('minute_seek', 'hold-blocked closed={} started={} open={} switching={}'.format(int(self._closed), int(self._started), int(self._minute_seek_open), int(self._pending_engine_reference is not None)))
            return True
        if self._held_direction not in (None, direction):
            log_event('minute_seek', 'hold-other-direction')
            return True
        self._held_direction = direction
        self._held_direction_long = True
        log_event('minute_seek', 'hold-accepted direction={}'.format(direction))
        self.open_minute_seek(direction)
        return True

    def open_minute_seek(self, direction):
        """Open native minute input above VOD without stopping the video."""
        if self._minute_seek_open or self._closed or (not self._started) or (getattr(self.current_item, 'content_type', '') not in ('movie', 'series')) or (self._pending_engine_reference is not None):
            return
        seeker = self._seek_interface()
        position, length = self._seek_position(seeker)
        position, length = (max(0, position), max(0, length))
        opener = getattr(self.session, 'openWithCallback', None)
        if not callable(opener):
            log_event('minute_seek', 'no-modal-opener')
            return
        self._seeking = False
        self._hide_seek_overlay()
        generation, item, reference = (self._generation, self.current_item, self.reference)
        self._minute_seek_open = True
        self.hide_info()

        def entered(minutes):
            self._minute_seek_open = False
            if minutes is None or self._closed or self._generation != generation or (self.current_item is not item) or (self.reference is not reference):
                return
            try:
                minutes = int(minutes)
            except (ValueError, TypeError, OverflowError):
                return
            if 0 < minutes <= 9999:
                self._seek_immediately(direction * minutes * 60)
        try:
            from Components.Input import Input
            from Screens.InputBox import InputBox
            opener(entered, InputBox, title=_('SEEK FORWARD' if direction > 0 else 'SEEK BACKWARD') + ' (' + _('{} minutes').format('').strip() + ')', text='    ', maxSize=4, type=Input.NUMBER)
            log_event('minute_seek', 'native-dialog-opened')
        except Exception as error:
            log_event('minute_seek', 'native-dialog-failed', error)
            _debug('native minute input failed error={}'.format(error.__class__.__name__))
            try:
                opener(entered, GTMinuteSeekScreen, direction, position, length)
                log_event('minute_seek', 'custom-dialog-opened')
            except Exception as fallback_error:
                log_event('minute_seek', 'custom-dialog-failed', fallback_error)
                self._minute_seek_open = False
                self.show_info()

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
            self._seek_not_ready()
            return True
        position, length = self._seek_position(seeker)
        if position < 0 or length <= 0:
            self._seek_not_ready()
            return True
        target = min(max(0, int(self._seek_target)), int(length))
        if not (self._vod_seek_enabled() and self._pending_seek_verification is not None):
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
        if getattr(self, '_closed', False):
            return
        if getattr(self, '_info_visible', False):
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
        getter = getattr(service, 'pause', None) if service is not None else None
        if getter is None:
            return None
        try:
            return getter()
        except Exception:
            return None

    def pause_playback(self):
        if getattr(self.current_item, 'content_type', '') == 'live':
            self.show_info()
            return
        if self._paused:
            return
        if self._vod_seek_pause_transport():
            self._paused = True
            self._show_seek_overlay(N_('PAUSED'), N_('PLAY / PAUSE to continue   •   STOP to close the video'))
            return
        pauseable = self._pause_interface()
        if pauseable is None:
            self._show_seek_overlay(N_('PAUSE IS NOT AVAILABLE'), N_('This server or playback format does not support pausing'))
            self._start_info_hide_timer()
            return
        try:
            result = pauseable.pause()
            if self._seek_call_failed(result):
                return
        except Exception:
            return
        self._paused = True
        subtitle_controller = getattr(self, '_subtitle_controller', None)
        if subtitle_controller is not None:
            subtitle_controller.pause()
        try:
            self._hide_timer.stop()
        except Exception:
            pass
        self._show_seek_overlay(N_('PAUSED'), N_('PLAY / PAUSE to continue   •   STOP to close the video'))

    def resume_playback(self):
        if not self._paused:
            state = self._pending_seek_verification
            if self._vod_seek_enabled() and isinstance(state, dict) and 'guard' in state and state['phase'] == 'check' and time.monotonic() - state['guard'].started >= 2.0:
                if self._nudge_vod_seek(state):self._seek_verify_timer.start(SEEK_VERIFY_DELAY_MS, True)
            return
        if self._vod_seek_pause_transport():
            self._paused = False
            pauseable = self._pause_interface()
            try:
                if pauseable is not None:pauseable.unpause()
            except Exception:
                pass
            self._hide_seek_overlay()
            self._seek_verify_timer.start(1, True)
            return
        pauseable = self._pause_interface()
        if pauseable is None:
            return
        try:
            result = pauseable.unpause()
            if self._seek_call_failed(result):
                return
        except Exception:
            return
        self._paused = False
        subtitle_controller = getattr(self, '_subtitle_controller', None)
        if subtitle_controller is not None:
            subtitle_controller.resume()
        self._hide_seek_overlay()
        self.show_info()
        if subtitle_controller is not None and subtitle_controller.external_loaded:
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
        unused_width, unused_height, px = _scale()
        fit_dynamic_text(self._info_dialog['clock'], current.strftime('%H:%M'), max_lines=1, preferred_size=font_px(px, 52), min_size=max(6, px(30)))
        fit_dynamic_text(self._info_dialog['clock_seconds'], current.strftime(':%S'), max_lines=1, preferred_size=font_px(px, 30), min_size=max(6, px(18)))
        fit_dynamic_text(self._info_dialog['date'], localized_date_text(current), max_lines=1, preferred_size=font_px(px, 18), min_size=max(6, px(10)))

    def _render_player_weather_waiting(self, settings=None):
        if settings is None:
            try:
                settings = self._weather_settings_loader()
            except Exception:
                settings = None
        city = getattr(settings, 'city', DEFAULT_CITY)
        ellipsize_dynamic_text(self._info_dialog['weather_city'], localized_upper(city), fallback_chars=15)
        self._info_dialog['weather_icon'].hide()
        if settings is not None and (not getattr(settings, 'enabled', True)):
            self._info_dialog['weather_temp'].setText('--')
            self._info_dialog['weather_desc'].setText(_('Weather is disabled'))
        else:
            self._info_dialog['weather_temp'].setText('--°')
            self._info_dialog['weather_desc'].setText(_('Loading weather data'))

    def _render_player_weather_unavailable(self):
        """Render an honest live placeholder without implying a future fetch."""
        try:
            settings = self._weather_settings_loader()
        except Exception:
            settings = None
        city = getattr(settings, 'city', DEFAULT_CITY)
        ellipsize_dynamic_text(self._info_dialog['weather_city'], localized_upper(city), fallback_chars=15)
        self._info_dialog['weather_icon'].hide()
        self._info_dialog['weather_temp'].setText('--')
        if settings is not None and (not getattr(settings, 'enabled', True)):
            description = _('Weather is disabled')
        else:
            description = _('Weather information is not ready')
        self._info_dialog['weather_desc'].setText(description)

    def _start_player_weather(self):
        if self._closed or not self._started or getattr(self, '_web_suspended', None) is not None or (self._info_dialog is None):
            return
        try:
            settings = self._weather_settings_loader()
        except Exception:
            return
        if not getattr(settings, 'enabled', True):
            self._weather_forecast = None
            self._stop_player_weather()
            self._render_player_weather_waiting(settings)
            return
        peek = getattr(self._weather_service, 'peek', None)
        if callable(peek):
            try:
                forecast = peek(settings)
            except Exception:
                forecast = None
            if forecast is not None and WeatherService.matches_settings(forecast, settings):
                self._stop_player_weather()
                self._weather_forecast = forecast
                self._weather_ready = True
                self._weather_started = True
                self._render_player_weather(forecast)
                return
        if self._weather_forecast is not None:
            if WeatherService.matches_settings(self._weather_forecast, settings):
                self._render_player_weather(self._weather_forecast)
                return
            self._weather_forecast = None
            self._stop_player_weather()
        if self._weather_loading:
            if WeatherService.matches_settings(self._weather_settings.as_dict(), settings):
                self._weather_timer.start(150, True)
                return
            self._stop_player_weather()
        if self._weather_started and time.monotonic() < self._weather_retry_after:
            return
        self._weather_started = True
        self._weather_retry_after = time.monotonic() + 60.0
        self._render_player_weather_waiting(settings)
        self._weather_settings = settings
        self._weather_error = ''
        self._weather_ready = False
        self._weather_loading = True
        _cancel_worker_token(self._weather_token)
        token = _WorkerToken(WORKER_WEATHER_TIMEOUT_SECONDS)
        self._weather_token = token
        worker = threading.Thread(target=lambda: self._run_player_weather(token))
        worker.daemon = True
        worker.start()
        self._weather_timer.start(150, True)

    def _stop_player_weather(self):
        _cancel_worker_token(self._weather_token)
        self._weather_token = None
        self._weather_loading = False
        self._weather_ready = bool(self._weather_forecast is not None)
        self._weather_started = bool(self._weather_forecast is not None)
        self._weather_timer.stop()

    def _run_player_weather(self, token=None):
        forecast = None
        error_text = ''
        try:
            if token is not None:
                token.check()
            forecast = self._weather_service.get(self._weather_settings, force=False, cancel_event=token.cancel_event if token is not None else None, deadline=token.deadline if token is not None else None)
            if token is not None:
                token.check()
        except _WorkerCancelled:
            return
        except WeatherError as error:
            error_text = str(error)
        except Exception:
            error_text = N_('Could not load the weather forecast')
        if not self._closed and (token is None or (token is self._weather_token and token.active())):
            self._weather_forecast = forecast
            self._weather_error = error_text
            self._weather_ready = True

    def _poll_player_weather(self):
        if self._closed or getattr(self, '_web_suspended', None) is not None or (not self._weather_loading):
            return
        if not self._weather_ready:
            if self._weather_token is not None and self._weather_token.expired():
                self._weather_token.cancel()
                self._weather_loading = False
                self._weather_token = None
                self._info_dialog['weather_temp'].setText('--')
                self._info_dialog['weather_desc'].setText(_('Weather information unavailable.'))
                return
            self._weather_timer.start(150, True)
            return
        self._weather_loading = False
        self._weather_token = None
        if self._weather_forecast is not None:
            self._render_player_weather(self._weather_forecast)
            return
        self._info_dialog['weather_temp'].setText('--')
        ellipsize_dynamic_text(self._info_dialog['weather_desc'], _(self._weather_error or N_('Weather information unavailable.')), fallback_chars=18)

    def _render_player_weather(self, forecast):
        ellipsize_dynamic_text(self._info_dialog['weather_city'], localized_upper(forecast.city), fallback_chars=15)
        self._info_dialog['weather_temp'].setText('{:.0f}°{}'.format(forecast.current_temperature, forecast.unit))
        self._info_dialog['weather_desc'].setText(weather_description(forecast.current_code))
        path = os.path.join(WEATHER_ICON_DIR, '{}.png'.format(weather_icon_name(forecast.current_code)))
        try:
            widget = self._info_dialog['weather_icon']
            widget.instance.setPixmapFromFile(path)
            widget.show()
        except Exception:
            self._info_dialog['weather_icon'].hide()

    def _clear_player_progress(self):
        self._info_dialog['player_progress_text'].setText('')
        self._info_dialog['player_progress_start'].setText('')
        self._info_dialog['player_progress_end'].setText('')
        self._info_dialog['player_progress_fill'].hide()

    def _set_playback_progress(self, position, length):
        if position < 0 or length <= 0:
            self._clear_player_progress()
            return
        position = min(max(0, int(position)), int(length))
        length = max(1, int(length))
        remaining = max(0, length - position)
        ratio = min(1.0, max(0.0, float(position) / float(length)))
        set_text_if_changed(self._info_dialog['player_progress_text'], remaining_time_text(remaining))
        set_text_if_changed(self._info_dialog['player_progress_start'], self._format_play_time(position))
        set_text_if_changed(self._info_dialog['player_progress_end'], self._format_play_time(length))
        fill = self._info_dialog['player_progress_fill']
        try:
            fill.instance.resize(eSize(max(1, int(round(self._progress_width * ratio))), self._progress_height))
        except Exception:
            pass
        fill.show()

    def _playback_tick(self):
        """Refresh elapsed/remaining VOD time from ExtEplayer3 once a second."""
        if self._closed:
            return
        if getattr(self.current_item, 'content_type', '') == 'live':
            return
        position, length = self._seek_position(self._raw_seek_interface() if self._episode_watcher.key else None)
        if self._episode_watcher.sample(position, length):
            if self._resume_store is not None and self._resume_key:
                queue_playback_clear(self._resume_store, self._resume_key)
        if self._youtube_end_watcher is not None:
            self._youtube_end_watcher.sample(position, length)
        if position >= 0 and length > 0:
            position = min(position, length)
            self._set_static_info_epg('epg_now', self._format_play_time(position))
            self._set_static_info_epg('epg_next', self._format_play_time(max(0, length - position)))
            self._set_playback_progress(position, length)
        self._refresh_front_panel()
        self._playback_timer.start(1000, True)

    def _arm_resume_bookmark_guard(self):
        """Retain a bookmark until the decoder confirms its resumed position."""
        target = self._resume_start_position
        previous = getattr(self, '_resume_bookmark_guard', None)
        same_item = previous is not None and previous['key'] == self._resume_key
        changed_service = same_item and (previous['reference'] is not self.reference or previous['generation'] != self._generation)
        manual = previous.get('manual_seek') if same_item else None
        if changed_service and manual is not None:
            if not self._seek_reached(target, manual['origin'], manual['target']):
                target = manual['target']
            self._resume_start_position = target
        elif same_item and target < previous['target']:
            target = previous['target']
            if previous['reference'] is not self.reference or previous['generation'] != self._generation:
                self._resume_start_position = target
        self._resume_bookmark_guard = {'key': self._resume_key, 'target': target, 'reference': self.reference, 'generation': self._generation} if target > 0 else None

    def _resume_bookmark_position_ready(self, position):
        guard = getattr(self, '_resume_bookmark_guard', None)
        if guard is None:
            return True
        if guard.get('vod_seek_unconfirmed'):
            return False
        if guard['key'] != self._resume_key:
            self._resume_bookmark_guard = None
            return True
        if guard['reference'] is not self.reference or guard['generation'] != self._generation:
            return False
        manual = guard.get('manual_seek')
        if manual is not None:
            ready = self._seek_reached(position, manual['origin'], manual['target'])
        else:
            ready = position >= max(0, guard['target'] - 2)
        if not ready:
            return False
        self._resume_bookmark_guard = None
        return True

    def _resume_retry(self, reason, observed_length=-1):
        """Retry a bounded resume probe, then keep playback at the beginning."""
        self._resume_attempts += 1
        if self._resume_attempts < RESUME_MAX_ATTEMPTS:
            self._resume_timer.start(RESUME_RETRY_DELAY_MS, True)
            return True
        _debug('resume skipped engine={} target={} observed_length={} reason={}'.format(reference_service_type(self.reference, 5002), self._resume_start_position, observed_length, reason))
        self._resume_start_position = 0
        self._reset_resume_length_probe()
        self._finish_web_resume()
        return False

    def _reset_resume_length_probe(self):
        self._resume_last_length = -1
        self._resume_stable_length_samples = 0

    def _resume_length_ready(self, length):
        """Reject provisional VOD durations before applying a saved position."""
        length = int(length)
        if bool(getattr(self.current_item, 'youtube_native', False)):
            eligible = bool(youtube_resume_position(self._resume_start_position, length))
        else:
            eligible = 0 < self._resume_start_position < length if getattr(self, '_resume_web_return', False) else should_save_resume(self._resume_start_position, length)
        if not eligible:
            self._reset_resume_length_probe()
            return False
        if self._resume_last_length >= 0 and abs(length - self._resume_last_length) <= RESUME_LENGTH_STABILITY_TOLERANCE_SECONDS:
            self._resume_stable_length_samples += 1
        else:
            self._resume_last_length = length
            self._resume_stable_length_samples = 1
        required_samples = RESUME_4097_STABLE_SAMPLES if reference_service_type(self.reference, 5002) == 4097 else 1
        return self._resume_stable_length_samples >= required_samples

    def _resume_tick(self):
        """Seek only after the decoder exposes a plausible, stable VOD length."""
        if self._closed or self._resume_start_position <= 0:
            return
        self._arm_resume_bookmark_guard()
        seeker = self._seek_interface()
        if seeker is None:
            self._reset_resume_length_probe()
            self._resume_retry('seek-unavailable')
            return
        position, length = self._seek_position(seeker)
        if position < 0 or length <= 0:
            self._reset_resume_length_probe()
            self._resume_retry('length-unavailable', length)
            return
        if not self._resume_length_ready(length):
            self._resume_retry('length-provisional', length)
            return
        target = int(self._resume_start_position)
        self._cancel_seek_verification()
        if not self._request_seek(seeker, position, target, startup_resume=True):
            self._resume_retry('seek-rejected', length)
            return
        _debug('resume applied engine={} target={} length={} probes={}'.format(reference_service_type(self.reference, 5002), target, length, self._resume_attempts + 1))
        self._resume_start_position = 0
        self._reset_resume_length_probe()
        self._resume_saved = False
        self._finish_web_resume()
        self._playback_tick()
        self.show_info()

    def _save_resume_position(self):
        if getattr(self, '_web_suspended', None) is not None:
            return
        if self._resume_start_position > 0:
            return
        sampled_position = None
        if getattr(self, '_resume_bookmark_guard', None) is not None:
            sampled_position = self._seek_position()
            position, length = sampled_position
            if length <= 0 or not self._resume_bookmark_position_ready(position):
                return
        if self._episode_watcher.sample_current():
            if self._resume_store is not None and self._resume_key:
                queue_playback_clear(self._resume_store, self._resume_key)
        if self._resume_saved or self._resume_store is None or (not self._resume_key):
            return
        if getattr(self.current_item, 'content_type', '') not in ('movie', 'series'):
            return
        if bool(getattr(self.current_item, 'youtube_native', False)):
            position, length = sampled_position or self._seek_position()
            watcher = getattr(self, '_youtube_end_watcher', None)
            if getattr(watcher, 'ended', False):
                position = length
            try:
                self._resume_saved = bool(queue_playback_save(self._resume_store, self._resume_key, position, length))
            except OSError as exc:
                log_event('youtube', 'playback-history-write-failed error_type={}'.format(type(exc).__name__))
            return
        position, length = sampled_position or self._seek_position()
        if position >= 0 and length > 0:
            self._remember_resume_subtitle()
            self._resume_saved = bool(queue_playback_save(self._resume_store, self._resume_key, position, length, item=self.current_item, account_scope=resume_account_scope(self.client)))

    def _set_player_progress(self, event, now=None):
        start = int(getattr(event, 'start_timestamp', 0) or 0)
        end = int(getattr(event, 'end_timestamp', 0) or 0)
        if not start or not end or end <= start:
            self._clear_player_progress()
            return
        current = int(time.time() if now is None else now)
        ratio = min(1.0, max(0.0, float(current - start) / float(end - start)))
        remaining = max(0, end - current)
        set_text_if_changed(self._info_dialog['player_progress_text'], remaining_time_text(remaining))
        set_text_if_changed(self._info_dialog['player_progress_start'], time.strftime('%H:%M', time.localtime(start)))
        set_text_if_changed(self._info_dialog['player_progress_end'], time.strftime('%H:%M', time.localtime(end)))
        fill = self._info_dialog['player_progress_fill']
        try:
            fill.instance.resize(eSize(max(1, int(round(self._progress_width * ratio))), self._progress_height))
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
        if self._closed or not self._is_live_item() or self._live_metadata_complete_generation == self._generation:
            return False
        if self._live_metadata_settle_pending:
            return False
        if self._metadata_attempts >= LIVE_METADATA_MAX_SAMPLES:
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
        if self._live_network_silent() and (not live_local_once):
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
        navigation = getattr(self.session, 'nav', None)
        service = None
        if navigation is not None:
            getter = getattr(navigation, 'getCurrentService', None)
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
        snapshot = self._refresh_resolution_info(service)
        width = snapshot.get('width', 0) if snapshot else 0
        height = snapshot.get('height', 0) if snapshot else 0
        frame_rate = self._service_info_value(info, 'sFrameRate')
        video_type = self._service_info_value(info, 'sVideoType')
        if frame_rate <= 0:
            frame_rate = _read_proc_number('/proc/stb/vmpeg/0/framerate', 10)
        if width > 0 and height > 0 and live_local_once:
            self._live_metadata_ready['resolution'] = True
        if frame_rate > 0:
            fps = int((frame_rate + 500) / 1000) if frame_rate > 1000 else frame_rate
            self._set_info_chip('fps', '{} FPS'.format(fps))
            if live_local_once:
                self._live_metadata_ready['fps'] = True
        elif not live_local_once or not self._live_metadata_ready.get('fps'):
            self._set_info_chip('fps', '-- FPS')
        video_codec = VIDEO_CODECS.get(video_type, '')
        if not video_codec:
            video_codec = _normalise_video_codec(_read_proc_text('/proc/stb/vmpeg/0/codec'))
        audio_codec = self._audio_description(service)
        if video_codec != '--':
            self._set_info_chip('video_codec', video_codec)
            if live_local_once:
                self._live_metadata_ready['video'] = True
        elif not live_local_once or not self._live_metadata_ready.get('video'):
            self._set_info_chip('video_codec', '--')
        if audio_codec != '--':
            self._set_info_chip('audio_codec', audio_codec)
            if live_local_once:
                self._live_metadata_ready['audio'] = True
        elif not live_local_once or not self._live_metadata_ready.get('audio'):
            self._set_info_chip('audio_codec', '--')
        if live_local_once:
            decoder_sample_ready = bool(width > 0 and height > 0 and (frame_rate > 0 or video_codec != '--'))
            if decoder_sample_ready:
                self._live_info_ready_samples += 1
            else:
                self._live_info_ready_samples = 0
            if self._live_info_queued_generation == self._generation and self._live_info_ready_samples >= LIVE_INFO_READY_MIN_SAMPLES:
                self._start_live_info_completion(force=False)
            metadata_incomplete = not all((self._live_metadata_ready.get(name, False) for name in ('resolution', 'fps', 'video', 'audio')))
        else:
            metadata_incomplete = width <= 0 or height <= 0 or frame_rate <= 0 or (video_codec == '--') or (audio_codec == '--')
        if live_local_once:
            waiting_for_second_sample = bool(self._live_info_queued_generation == self._generation and self._live_info_ready_samples < LIVE_INFO_READY_MIN_SAMPLES)
            if (metadata_incomplete or waiting_for_second_sample) and self._metadata_attempts < LIVE_METADATA_MAX_SAMPLES:
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
            return '--'
        try:
            tracks = service.audioTracks()
            if tracks is not None and tracks.getNumberOfTracks() > 0:
                current = tracks.getCurrentTrack()
                track = tracks.getTrackInfo(current)
                description = localized_language_name(str(track.getDescription() or '').strip())
                if description:
                    return description[:28]
                language_getter = getattr(track, 'getLanguage', None)
                if callable(language_getter):
                    language = localized_language_name(language_getter())
                    if language:
                        return language[:28]
        except Exception:
            pass
        try:
            key = getattr(iServiceInformation, 'sTagAudioCodec', None)
            info = service.info() if key is not None else None
            getter = getattr(info, 'getInfoString', None)
            codec = ' '.join(str(getter(key) or '').split()) if callable(getter) else ''
            if codec and codec.lower() not in ('unknown', 'n/a', '--', '-1'):
                return codec[:28]
        except Exception:
            pass
        return '--'

    @staticmethod
    def _format_epg(event):
        time_text = event.time_text()
        if time_text:
            return '{}  {}'.format(time_text, event.title)
        return str(event.title)

    def _reset_picon_display(self):
        try:
            self._picon_timer.stop()
        except Exception:
            pass
        # Invalidate callbacks before closing the old native decoder.
        self._picon_request_token += 1
        self._live_picon_retry = None
        loaders = getattr(self._info_dialog, '_gt_pixmap_loaders', None)
        if loaders is not None:
            previous = loaders.pop('player-picon', None)
            if previous is not None:
                try:
                    previous.close()
                except Exception:
                    pass
        self._picon_visible = False
        self._cached_picon_generation = -1
        self._picon_request_generation = -1
        _cancel_worker_token(self._picon_worker_token)
        self._picon_worker_token = None
        self._picon_results = {}
        self._sync_picon_visibility()

    def _sync_picon_visibility(self):
        """Keep a local YouTube logo visible until current artwork decodes."""
        try:
            show_picon = self._picon_visible and self._info_visible
            show_logo = self._info_visible and (not self._picon_visible) and bool(getattr(self.current_item, 'youtube_native', False))
            for name, visible in (('picon', show_picon), ('youtube_logo', show_logo)):
                if visible:
                    self._info_dialog[name].show()
                else:
                    self._info_dialog[name].hide()
            self._info_dialog['picon_placeholder'].hide()
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
        self._picon_request_generation = generation
        if is_live:
            snapshot = self._live_snapshot_for_current_item()
            path = artwork_path(snapshot.get('picon_path', '') or '')
            if path and _valid_live_picon_file(path):
                self._cached_picon_generation = generation
                self._apply_picon(generation, path)
                return
        url = _safe_picon_url(getattr(self.current_item, 'icon', ''))
        if not url:
            return
        destination = _picon_cache_path(url)
        cached = _cached_picon_path(url)
        if cached:
            if is_live and (not _valid_live_picon_file(cached)):
                return
            self._cached_picon_generation = generation
            self._apply_picon(generation, cached)
            return
        self._picon_results = {key: value for key, value in self._picon_results.items() if key >= generation}
        token = _WorkerToken(LIVE_PICON_DOWNLOAD_TIMEOUT if is_live else WORKER_POSTER_TIMEOUT_SECONDS)
        _cancel_worker_token(self._picon_worker_token)
        self._picon_worker_token = token
        worker = threading.Thread(target=self._load_picon_worker, args=(generation, request_token, url, destination, LIVE_PICON_DOWNLOAD_TIMEOUT if is_live else None, token))
        worker.daemon = True
        worker.start()
        self._picon_timer.start(100, True)

    def _load_picon_worker(self, generation, request_token, url, destination, timeout=None, token=None):
        try:
            if timeout is None:
                path = _download_client_image(self.client, url, destination, token=token)
            else:
                path = _download_client_image(self.client, url, destination, timeout=timeout, token=token)
        except Exception:
            path = ''
        if self._closed or (token is not None and (not token.active())) or generation != self._generation or (request_token != self._picon_request_token):
            return
        self._picon_results[generation] = (request_token, path)

    def _picon_tick(self, _unused=None):
        if self._closed:
            return
        if self._is_live_item():
            self._live_picon_tick()
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
                if getattr(self, '_picon_worker_token', None) is token:
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
        if self._is_live_item() and (not _valid_live_picon_file(path)):
            return
        live = self._is_live_item()
        state = self._live_picon_status() if live else None
        if live:
            if state.phase == 'decode' or not state.begin('decode', time.monotonic()):
                return
            self._picon_request_token += 1
            self._picon_request_generation = generation
            self._cached_picon_generation = -1
            self._picon_timer.start(max(1, int(round((state.decode_deadline - time.monotonic()) * 1000))), True)
        request_token = self._picon_request_token
        completed = [False]

        def loaded(success):
            if completed[0] or self._closed or generation != self._generation or request_token != self._picon_request_token:
                return
            completed[0] = True
            if live:
                if state is not self._live_picon_retry:
                    return
                if not success:
                    # After the alpha path and compatibility decoder both
                    # fail, remove only this URL's recognised disposable file.
                    if state.decode_attempts == 2:
                        url = _safe_picon_url(getattr(self.current_item, 'icon', ''))
                        if url:
                            try:
                                _discard_invalid_live_picon_cache(url, path)
                            except Exception:
                                pass
                    self._defer_live_picon()
                    return
                state.succeeded()
                self._cached_picon_generation = generation
                self._picon_timer.stop()
                if self._info_visible:
                    self._start_info_hide_timer()
            self._picon_visible = bool(success)
            self._sync_picon_visibility()
        loader = attach_native_alpha_pixmap if live and state.decode_attempts == 1 else attach_pixmap
        if live:
            try:
                loader(self._info_dialog, 'picon', path, key='player-picon', on_loaded=loaded)
            except Exception:
                loaded(False)
        else:
            loader(self._info_dialog, 'picon', path, key='player-picon', on_loaded=loaded)

    def stop_playback(self):
        self._stop_player_stream_diag()
        try:
            self._direction_hold_timer.stop()
        except Exception:
            pass
        self._held_direction = None
        self._held_direction_long = False
        stop_archive_keepalive = getattr(self, '_stop_archive_portal_keepalive', None)
        if callable(stop_archive_keepalive):
            stop_archive_keepalive()
        cancel_archive_startup = getattr(self, '_cancel_archive_startup', None)
        if callable(cancel_archive_startup):
            cancel_archive_startup()
        cancel_startup = getattr(self, '_cancel_live_startup', None)
        if callable(cancel_startup):
            cancel_startup()
        self._pending_channel_index = None
        for attribute in ('_engine_link_token', '_reconnect_link_token', '_picon_worker_token'):
            token = getattr(self, attribute, None)
            _cancel_worker_token(token)
            setattr(self, attribute, None)
        self._stop_player_weather()
        self._clear_channel_link_state()
        self._stop_live_auxiliary_timers()
        self._cancel_live_info_state()
        self._reset_picon_display()
        for timer in (self._channel_switch_timer, self._reconnect_timer, self._engine_switch_timer, self._subtitle_auto_timer):
            try:
                timer.stop()
            except Exception:
                pass
        self._live_reconnecting = False
        self._reconnect_generation = -1
        self._clear_live_engine_switch_state()
        if not self._started:
            preparation = getattr(self, '_archive_initial_playback', None)
            if preparation is not None:
                if not preparation.restore():
                    return False
                self._archive_initial_playback = None
            return True
        subtitle_controller = getattr(self, '_subtitle_controller', None)
        if subtitle_controller is not None:
            subtitle_controller.reset_for_service_change()
        navigation = getattr(self.session, 'nav', None)
        _debug('playback stopped content={} actual_engine={} reason=screen_exit'.format(getattr(self.current_item, 'content_type', 'unknown'), reference_service_type(self.reference, 4097)))
        stopped = False
        restored = False
        if navigation is not None:
            try:
                navigation.stopService()
                stopped = True
            except Exception:
                pass
            restored = self._restore_old_service(navigation)
        released = bool(stopped or (self.old_reference is not None and restored))
        if released:
            _m3u_navigation_changed(navigation)
            self._started = False
        return released

    def stop_and_close(self):
        self._save_resume_position()
        if not self.stop_playback():
            self._set_static_info_epg('epg_next', _('Keeping the current stream'))
            self.show_info()
            return
        if self.keep_playing_on_exit and self._is_live_item():
            self._fullscreen_close_result = self._live_fullscreen_result(False, released=self._old_reference_restored)
            self.close(self._fullscreen_close_result)
            return
        self.close()

    def _live_fullscreen_result(self, kept, reference=None, released=False):
        service_type = None
        recovery = None
        if kept and reference is not None:
            service_type = reference_service_type(reference, self._active_live_service_type)
            recovery = getattr(self, '_live_startup_recovery', None)
            if recovery is not None:
                recovery.detach()
                self._live_startup_recovery = None
        else:
            cancel_startup = getattr(self, '_cancel_live_startup', None)
            if callable(cancel_startup):
                cancel_startup()
        return {'kind': LIVE_FULLSCREEN_RESULT_KIND, 'kept': bool(kept), 'released': bool(released) and (not kept), 'selected_index': self.current_index, 'reference': reference if kept else None, 'channel_item': self.current_item if kept else None, 'live_list_context': current_live_channel_context(self.client, self.current_item, resume_account_scope(self.client)) if kept else None, 'service_type': service_type, 'live_startup_recovery': recovery}

    def return_to_list(self):
        """Return an adopted live service to the list without reopening it."""
        recovery = getattr(self, '_live_startup_recovery', None)
        if recovery is not None and recovery.busy:
            self.show_info()
            return
        if self._pending_engine_reference is not None or self._live_reconnecting:
            self.show_info()
            return
        if self._adopt_playing_service and self._is_live_item() and self._started:
            navigation = getattr(self.session, 'nav', None)
            getter = getattr(navigation, 'getCurrentlyPlayingServiceReference', None)
            active_reference = None
            if getter is not None:
                try:
                    active_reference = getter()
                except Exception:
                    pass
            from .browser_live import GTStreamListScreen
            if GTStreamListScreen._same_service_reference(active_reference, self.reference):
                reference = active_reference
                self._stop_player_stream_diag()
                self._stop_live_auxiliary_timers()
                self._started = False
                self._fullscreen_close_result = self._live_fullscreen_result(True, reference)
                self.close(self._fullscreen_close_result)
                return
        if not self.stop_playback():
            self._set_static_info_epg('epg_next', _('Keeping the current stream'))
            self.show_info()
            return
        self._fullscreen_close_result = self._live_fullscreen_result(False, released=self._old_reference_restored)
        self.close(self._fullscreen_close_result)

    def open_live_channel_list(self):
        """Reveal the existing live list with the playing channel selected."""
        if self.keep_playing_on_exit and self._entries and (getattr(self.current_item, 'content_type', '') == 'live'):
            self.return_to_list()
            return
        self.show_info()

    def _on_close(self):
        self._cancel_stalker_auto_recovery()
        self._stop_player_weather()
        self._network_rate_sampler.reset()
        if self._youtube_end_watcher is not None:
            self._youtube_end_watcher.close()
            self._youtube_end_watcher = None
        self._stop_player_stream_diag()
        self._unbind_player_key_handler()
        self._live_zero_key.close()
        self._subtitle_sync_keys.close()
        sync_overlay, self._subtitle_sync_overlay = (self._subtitle_sync_overlay, None)
        if sync_overlay is not None:
            try:
                self.session.deleteDialog(sync_overlay)
            except Exception:
                sync_overlay.dismiss()
        self._held_direction = None
        self._held_direction_long = False
        self._save_resume_position()
        self._episode_watcher.close()
        stop_archive_keepalive = getattr(self, '_stop_archive_portal_keepalive', None)
        if callable(stop_archive_keepalive):
            stop_archive_keepalive()
        cancel_archive_startup = getattr(self, '_cancel_archive_startup', None)
        if callable(cancel_archive_startup):
            cancel_archive_startup()
        self._subtitle_menu_open = False
        self._subtitle_menu_generation = None
        self._subtitle_message_tokens.clear()
        self._aspect_menu_open = False
        subtitle_controller = getattr(self, '_subtitle_controller', None)
        self._subtitle_controller = None
        if subtitle_controller is not None:
            subtitle_controller.close()
        self._closed = True
        cancel_startup = getattr(self, '_cancel_live_startup', None)
        if callable(cancel_startup):
            cancel_startup()
        should_stop_playback = self._is_live_item() or not self.keep_playing_on_exit
        archive_terminal_timer = getattr(self, '_archive_terminal_timer', None)
        if archive_terminal_timer is not None:
            try:
                archive_terminal_timer.stop()
            except Exception:
                pass
        for timer in (self._hide_timer, self._metadata_timer, self._picon_timer, self._clock_timer, self._infobar_telemetry_timer, self._weather_timer, self._playback_timer, self._resume_timer, self._seek_verify_timer, self._subtitle_auto_timer, self._reconnect_timer, self._engine_switch_timer, self._channel_switch_timer, self._live_info_timer, self._archive_portal_timer):
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

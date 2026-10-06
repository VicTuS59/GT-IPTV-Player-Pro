# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent Enigma2 subtitle overlay and online-provider workflow."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from copy import deepcopy
from hashlib import sha256
import json
import threading
import time

from .diagnostics import log_event
from .i18n import N_, _
from .subtitle_clock import SubtitlePlaybackClock
from .online_subtitles import (
    SubtitleError,
    active_subtitle_text,
    annotate_subtitle_results,
    parse_subtitle,
    POLISH_PROVIDERS,
    polish_search_providers,
    provider_display_name,
    retime_subtitle_cues,
    save_subtitle_file,
    subtitle_client,
    subtitle_result_sort_key,
    subtitle_search_available,
)
from .subtitle_settings import PROVIDERS, load_subtitle_settings
from .subtitle_language import normalize_subtitle_language
from .subtitle_search_cache import subtitle_search_context
from .subtitle_jobs import check_subtitle_job, subtitle_request_scope

SEARCH_PROVIDERS = PROVIDERS + POLISH_PROVIDERS

try:
    from Components.Label import Label
    from Screens.Screen import Screen
    from enigma import ePoint, eTimer, getDesktop
except ImportError:  # Unit tests and migration tools run outside Enigma2.
    Label = None
    Screen = object
    ePoint = None
    eTimer = None
    getDesktop = None


POLL_MS = 100
RENDER_MS = 125
CLOCK_LOG_INTERVAL_SECONDS = 5.0
SUBTITLE_OVERLAY_Z = 200
SUBTITLE_BOX_WIDTH = 1360
PLAYER_INFOBAR_HEIGHT = 280
INFOBAR_SUBTITLE_GAP = 24
_NATIVE_SOURCE_EXECUTOR = ThreadPoolExecutor(max_workers=3)


def _result_compatibility_sort(result):
    value = result.get("compatibility_sort") if isinstance(result, dict) else None
    if isinstance(value, (tuple, list)) and len(value) == 8:
        try:
            return tuple(int(item) for item in value)
        except (TypeError, ValueError, OverflowError):
            pass
    return (0, 2, 2, 2, 2, 2, 2, 0)

_ERROR_MESSAGES = {
    "provider_key_missing": N_("Enter a SubDL API key in subtitle settings."),
    "provider_key_rejected": N_("The SubDL API key was rejected."),
    "provider_limit": N_("The SubDL request limit has been reached."),
    "provider_unavailable": N_("SubDL is temporarily unavailable."),
    "provider_invalid_reply": N_("SubDL returned an invalid response."),
    "invalid_search": N_("The programme title is not available for subtitle search."),
    "subtitle_too_large": N_("The selected subtitle file is too large."),
    "subtitle_format_unsupported": N_("The selected subtitle format is not supported."),
    "subtitle_cache_unavailable": N_("The downloaded subtitle could not be saved."),
    "subtitle_no_results": N_("No matching subtitles were found on SubDL."),
}


def _connect_timer(timer, callback):
    if timer is None:
        return False
    try:
        timer.callback.append(callback)
        return True
    except Exception:
        pass
    try:
        timer.timeout.connect(callback)
        return True
    except Exception:
        return False


def _desktop_size():
    if getDesktop is not None:
        try:
            size = getDesktop(0).size()
            return max(1, int(size.width())), max(1, int(size.height()))
        except Exception:
            pass
    return 1920, 1080


def _scaled(value, scale):
    return max(1, int(round(float(value) * scale)))


def _subtitle_overlay_geometry(settings, infobar_visible=False):
    width, height = _desktop_size()
    scale = min(width / 1920.0, height / 1080.0)
    box_width = min(width - _scaled(80, scale), _scaled(SUBTITLE_BOX_WIDTH, scale))
    box_height = _scaled(154, scale)
    x = max(0, (width - box_width) // 2)
    y_by_position = {
        "bottom": height - _scaled(230, scale),
        "lower": height - _scaled(350, scale),
        "middle": (height - box_height) // 2,
    }
    y = max(0, min(height - box_height, y_by_position.get(
        settings.vertical_position, y_by_position["bottom"]
    )))
    if infobar_visible:
        infobar_top = height - _scaled(PLAYER_INFOBAR_HEIGHT, scale)
        raised_y = infobar_top - box_height - _scaled(INFOBAR_SUBTITLE_GAP, scale)
        y = max(0, min(y, raised_y))
    return {
        "width": width,
        "height": height,
        "scale": scale,
        "x": x,
        "y": y,
        "box_width": box_width,
        "box_height": box_height,
    }


def subtitle_overlay_skin(settings):
    geometry = _subtitle_overlay_geometry(settings)
    scale = geometry["scale"]
    font_size = {
        "small": 32,
        "medium": 40,
        "large": 48,
        "extra_large": 58,
    }.get(settings.font_size, 40)
    foreground = {
        "white": "#F8FAFC",
        "yellow": "#FDE047",
        "cyan": "#67E8F9",
    }.get(settings.font_color, "#F8FAFC")
    background = {
        "transparent_dark": "#A0000000",
        "solid_dark": "#020617",
    }.get(settings.background)
    background_xml = (
        'backgroundColor="{}" transparent="0"'.format(background)
        if background
        else 'backgroundColor="#00000000" transparent="1"'
    )
    # Use a compact OSD window rather than a transparent full-screen window.
    # On OpenPLi/ServiceApp some framebuffer drivers accept updates to a
    # full-screen transparent dialog but never composite its child label.
    # The player's working information OSD also uses a bounded high-z window.
    return (
        '<screen name="GTIndependentSubtitleOverlay" position="{},{}" '
        'size="{},{}" flags="wfNoBorder" backgroundColor="#FF000000" '
        'zPosition="{}">'
        '<widget name="subtitle" position="0,0" size="{},{}" '
        'font="Regular;{}" foregroundColor="{}" {} halign="center" '
        'valign="center" borderColor="#000000" borderWidth="{}" '
        'zPosition="1" />'
        '</screen>'
    ).format(
        geometry["x"], geometry["y"],
        geometry["box_width"], geometry["box_height"], SUBTITLE_OVERLAY_Z,
        geometry["box_width"], geometry["box_height"],
        _scaled(font_size, scale), foreground,
        background_xml, _scaled(2, scale),
    )


class GTIndependentSubtitleOverlay(Screen):
    def __init__(self, session, settings):
        if Label is None:
            raise RuntimeError("Enigma2 subtitle widgets are unavailable")
        self.skin = subtitle_overlay_skin(settings)
        Screen.__init__(self, session)
        self["subtitle"] = Label("")
        self._settings = settings.copy()
        self._text = ""
        self._active = False
        self._infobar_visible = False
        try:
            self.onLayoutFinish.append(self._raise_window)
        except Exception:
            pass

    def _raise_window(self):
        """Re-assert the OSD layer after image-specific skin processing."""
        try:
            setter = getattr(getattr(self, "instance", None), "setZPosition", None)
            if callable(setter):
                setter(SUBTITLE_OVERLAY_Z)
        except Exception:
            pass
        self._apply_position()

    def _apply_position(self):
        if ePoint is None:
            return
        geometry = _subtitle_overlay_geometry(
            self._settings, infobar_visible=self._infobar_visible
        )
        try:
            instance = getattr(self, "instance", None)
            mover = getattr(instance, "move", None)
            if callable(mover):
                mover(ePoint(geometry["x"], geometry["y"]))
        except Exception:
            pass

    def set_infobar_visible(self, visible):
        visible = bool(visible)
        if visible == self._infobar_visible:
            return
        self._infobar_visible = visible
        self._apply_position()

    def activate(self):
        # Reuse the dialog between cues and restore its OSD layer on each show.
        self.show()
        self._raise_window()
        self._active = True

    def deactivate(self):
        try:
            self["subtitle"].hide()
        except Exception:
            pass
        self._active = False
        self.hide()

    def set_subtitle(self, value):
        value = str(value or "")
        if value != self._text:
            self._text = value
            self["subtitle"].setText(value)
        if value:
            if not self._active:
                self.activate()
            self["subtitle"].show()
        else:
            # An empty parent can still cover the video or a results screen.
            self.deactivate()


class IndependentSubtitleRenderer(object):
    """Render parsed cues without SubsSupport or decoder subtitle tracks."""

    def __init__(
        self,
        session,
        position_provider,
        timer_factory=None,
        overlay_factory=None,
    ):
        self.session = session
        self.position_provider = position_provider
        self.timer_factory = timer_factory if timer_factory is not None else eTimer
        self.overlay_factory = overlay_factory
        self.timer = self.timer_factory() if callable(self.timer_factory) else None
        _connect_timer(self.timer, self._tick)
        self.overlay = None
        self.cues = ()
        self.starts = ()
        self.end_prefix = ()
        self.loaded_path = None
        self.settings = None
        self.offset_ms = 0
        self.paused = False
        self.closed = False
        self._last_text = ""
        self.clock = SubtitlePlaybackClock()
        self._raw_decoder_ms = None
        self._position_source = ""
        self._clock_logged_at = None
        self._logged_visible_cue = False
        self._infobar_visible = False
        self._ui_obscured = False
        self._ui_obscured_requested = False
        self._ui_obscured_owners = set()
        self.content_revision = 0
        self.content_fingerprint = ""

    @property
    def is_loaded(self):
        return bool(self.cues and self.loaded_path)

    def _style_signature(self, settings):
        return (
            settings.font_size,
            settings.font_color,
            settings.background,
            settings.vertical_position,
        )

    def _new_overlay(self, settings):
        if callable(self.overlay_factory):
            return self.overlay_factory(settings)
        instantiate = getattr(self.session, "instantiateDialog", None)
        if Label is None or not callable(instantiate):
            return None
        try:
            overlay = instantiate(GTIndependentSubtitleOverlay, settings)
            overlay.hide()
            return overlay
        except Exception:
            return None

    def _delete_overlay(self):
        overlay = self.overlay
        self.overlay = None
        self._dispose_overlay(overlay)

    def _dispose_overlay(self, overlay):
        if overlay is None:
            return
        try:
            overlay.set_subtitle("")
        except Exception:
            pass
        try:
            deactivator = getattr(overlay, "deactivate", None)
            if callable(deactivator):
                deactivator()
            else:
                overlay.hide()
        except Exception:
            pass
        deleter = getattr(self.session, "deleteDialog", None)
        if callable(deleter):
            try:
                deleter(overlay)
            except Exception:
                pass

    def _ensure_overlay(self, settings):
        if self.overlay is not None and self.settings is not None:
            if self._style_signature(settings) == self._style_signature(self.settings):
                return self.overlay
        # Keep the current window until the replacement can be created and
        # activated. A skin/memory error must not destroy working subtitles.
        return self._new_overlay(settings)

    @staticmethod
    def _content_fingerprint(cues):
        value = [(cue.start_ms, cue.end_ms, cue.text) for cue in cues]
        return sha256(json.dumps(value, ensure_ascii=False, separators=(",", ":"))
                      .encode("utf-8")).hexdigest()

    def load(self, cues, path, settings, preserve_offset=True):
        if self.closed or not cues or not path:
            return False
        cues = tuple(cues)
        if not cues:
            return False
        overlay = self._ensure_overlay(settings)
        if overlay is None:
            return False
        fingerprint = self._content_fingerprint(cues)
        offset = self.offset_ms if (preserve_offset and self.is_loaded
                                   and fingerprint == self.content_fingerprint) else int(settings.offset_ms)
        try:
            if not self._ui_obscured:
                activator = getattr(overlay, "activate", None)
                if callable(activator):
                    activator()
                else:
                    shower = getattr(overlay, "show", None)
                    if callable(shower):
                        shower()
            infobar_setter = getattr(overlay, "set_infobar_visible", None)
            if callable(infobar_setter):
                infobar_setter(self._infobar_visible)
        except Exception as error:
            log_event("subtitles", "overlay-activate-failed", error)
            if overlay is not self.overlay:
                self._dispose_overlay(overlay)
            return False
        if overlay is not self.overlay:
            previous = self.overlay
            self.overlay = overlay
            self._dispose_overlay(previous)
        self.cues = tuple(cues)
        self.starts = tuple(cue.start_ms for cue in self.cues)
        latest_end = 0
        ends = []
        for cue in self.cues:
            latest_end = max(latest_end, cue.end_ms)
            ends.append(latest_end)
        self.end_prefix = tuple(ends)
        self.loaded_path = str(path)
        self.settings = settings.copy()
        self.offset_ms = offset
        self.content_fingerprint = fingerprint
        self.content_revision += 1
        # A reused overlay can still show text from the previous file. Force
        # the first update even when the new file is currently between cues.
        self._last_text = None
        self._logged_visible_cue = False
        log_event(
            "subtitles",
            "renderer-loaded cues={} first_ms={} last_ms={} overlay_z={} window=compact".format(
                len(self.cues), self.cues[0].start_ms, self.cues[-1].end_ms,
                SUBTITLE_OVERLAY_Z,
            ),
        )
        self._tick()
        return True

    def set_infobar_visible(self, visible):
        self._infobar_visible = bool(visible)
        overlay = self.overlay
        if overlay is None:
            return
        setter = getattr(overlay, "set_infobar_visible", None)
        if callable(setter):
            try:
                setter(self._infobar_visible)
            except Exception as error:
                log_event("subtitles", "overlay-position-failed", error)

    def set_ui_obscured(self, obscured):
        """Set the legacy gate without releasing any open dialog's ownership."""
        self._ui_obscured_requested = bool(obscured)
        return self._update_ui_obscured()

    def acquire_ui_obscured(self):
        """Keep subtitles hidden until this particular dialog has closed."""
        if self.closed:
            return None
        token = object()
        self._ui_obscured_owners.add(token)
        self._update_ui_obscured()
        return token

    def release_ui_obscured(self, token):
        if token not in self._ui_obscured_owners:
            return False
        self._ui_obscured_owners.remove(token)
        self._update_ui_obscured()
        return True

    def _update_ui_obscured(self):
        obscured = bool(self._ui_obscured_requested or self._ui_obscured_owners)
        if obscured == self._ui_obscured:
            return False
        self._ui_obscured = obscured
        overlay = self.overlay
        if obscured:
            if overlay is not None:
                try:
                    deactivator = getattr(overlay, "deactivate", None)
                    if callable(deactivator):
                        deactivator()
                    else:
                        overlay.hide()
                except Exception as error:
                    log_event("subtitles", "overlay-suspend-failed", error)
            log_event("subtitles", "overlay-suspended reason=full-screen-ui")
            return True
        # Force the current cue through even if its text did not change while
        # the full-screen browser was open. A blank cue deliberately leaves the
        # compact window hidden, preventing a transparent video-plane cutout.
        self._last_text = None
        if self.is_loaded and not self.closed:
            self._tick()
        log_event("subtitles", "overlay-restored reason=full-screen-ui")
        return True

    def _clock_engine(self):
        try:
            navigation = getattr(self.session, "nav", None)
            getter = getattr(navigation, "getCurrentlyPlayingServiceReference", None)
            reference = getter() if callable(getter) else None
            for attribute in ("type", "service_type", "getType"):
                value = getattr(reference, attribute, None)
                try:
                    return int(value() if callable(value) else value)
                except (TypeError, ValueError, OverflowError):
                    pass
            return 0
        except Exception:
            return 0

    def _set_position_source(self, source, position_ms, now=None):
        now = time.monotonic() if now is None else now
        changed = source != self._position_source
        self._position_source = source
        elapsed = (
            now - self._clock_logged_at
            if self._clock_logged_at is not None else CLOCK_LOG_INTERVAL_SECONDS
        )
        transition = changed and (
            source not in ("decoder", "decoder-held") or elapsed >= 1.0
        )
        if not transition and not self.clock.event and elapsed < CLOCK_LOG_INTERVAL_SECONDS:
            return
        self._clock_logged_at = now
        log_event(
            "subtitles",
            "clock-source={} decoder_ms={} position_ms={} paused={} "
            "engine={} event={}".format(
                source,
                self._raw_decoder_ms if self._raw_decoder_ms is not None else -1,
                position_ms if position_ms is not None else -1,
                int(self.paused), self._clock_engine(), self.clock.event or "sample",
            ),
        )

    def _read_decoder_ms(self):
        try:
            if not callable(self.position_provider):
                return None
            value = self.position_provider()
            if isinstance(value, (tuple, list)):
                if len(value) == 2:
                    error, ticks = value
                    if int(error) != 0 or float(ticks) < 0:
                        return None
                    return int(round(float(ticks) / 90.0))
                if len(value) != 1:
                    return None
                value = value[0]
            seconds = float(value)
            return int(round(seconds * 1000.0)) if seconds >= 0 else None
        except Exception:
            return None

    def _position_ms(self):
        now = time.monotonic()
        self._raw_decoder_ms = self._read_decoder_ms()
        position = self.clock.update(self._raw_decoder_ms, now)
        self._set_position_source(self.clock.source, position, now)
        return position

    def _start_timer(self):
        if self.timer is None or self.closed or self.paused or not self.is_loaded:
            return
        try:
            self.timer.start(RENDER_MS, True)
        except Exception:
            pass

    def _stop_timer(self):
        if self.timer is not None:
            try:
                self.timer.stop()
            except Exception:
                pass

    def _tick(self):
        if self.closed or not self.is_loaded:
            return
        position_ms = self._position_ms()
        if self._ui_obscured:
            if not self.paused:
                self._start_timer()
            return
        text = active_subtitle_text(
            self.cues, position_ms, self.offset_ms, self.starts, self.end_prefix
        ) if position_ms is not None else ""
        if text != self._last_text:
            self._last_text = text
            try:
                self.overlay.set_subtitle(text)
                if text and not self._logged_visible_cue:
                    self._logged_visible_cue = True
                    log_event(
                        "subtitles",
                        "cue-dispatched position_ms={} offset_ms={}".format(
                            position_ms, self.offset_ms,
                        ),
                    )
            except Exception as error:
                log_event("subtitles", "overlay-update-failed", error)
        if not self.paused:
            self._start_timer()

    def adjust_offset(self, delta_ms=None, absolute_ms=None):
        if absolute_ms is None:
            absolute_ms = self.offset_ms + int(delta_ms or 0)
        self.offset_ms = max(-30 * 60 * 1000, min(30 * 60 * 1000, int(absolute_ms)))
        self._tick()
        return self.offset_ms

    def pause(self):
        self._position_ms()
        self.clock.pause()
        self.paused = True
        self._stop_timer()
        self._tick()

    def resume(self):
        self.clock.resume()
        self.paused = False
        self._tick()

    def prepare_seek(self, target_seconds, origin_seconds=None):
        try:
            target_ms = max(0, int(round(float(target_seconds) * 1000.0)))
        except (TypeError, ValueError, OverflowError):
            return False
        origin_ms = self.clock.last_position_ms
        if origin_seconds is not None:
            try:
                value = float(origin_seconds)
                if value >= 0:
                    origin_ms = int(round(value * 1000.0))
            except (TypeError, ValueError, OverflowError):
                pass
        self.clock.begin_seek(target_ms, origin_ms, time.monotonic())
        self._last_text = None
        log_event(
            "subtitles", "seek-pending target_ms={} origin_ms={}".format(
                target_ms, origin_ms if origin_ms is not None else -1,
            ),
        )
        self._tick()
        return True

    def after_seek(self, position_seconds=None):
        hint_ms = None
        try:
            value = float(position_seconds)
            if value >= 0:
                hint_ms = int(round(value * 1000.0))
        except (TypeError, ValueError, OverflowError):
            pass
        self.clock.complete_seek(hint_ms, time.monotonic())
        self._last_text = None
        log_event("subtitles", "seek-confirmed observed_ms={}".format(
            hint_ms if hint_ms is not None else -1,
        ))
        self._tick()

    def cancel_seek(self):
        self.clock.cancel_seek()
        self._last_text = None
        log_event("subtitles", "seek-cancelled use=decoder")
        self._tick()

    def disable(self):
        had_subtitle = self.is_loaded
        self._stop_timer()
        # File/service changes must not release a still-open dialog's gate.
        self.cues = ()
        self.starts = ()
        self.end_prefix = ()
        self.loaded_path = None
        self.content_fingerprint = ""
        if had_subtitle:
            self.content_revision += 1
        self._last_text = ""
        if self.overlay is not None:
            try:
                self.overlay.set_subtitle("")
            except Exception:
                pass
            try:
                deactivator = getattr(self.overlay, "deactivate", None)
                if callable(deactivator):
                    deactivator()
                else:
                    self.overlay.hide()
            except Exception:
                pass
        return had_subtitle

    def reset_clock(self):
        """Discard every timing sample from the previous playback service."""
        self.clock.reset()
        self.paused = False
        self._raw_decoder_ms = None
        self._position_source = ""
        self._clock_logged_at = None
        self._logged_visible_cue = False

    def snapshot(self):
        if not self.is_loaded:
            return None
        return {
            "path": self.loaded_path,
            "cues": self.cues,
            "settings": self.settings.copy(),
            "offset_ms": self.offset_ms,
            "content_fingerprint": self.content_fingerprint,
        }

    def restore(self, state):
        if not isinstance(state, dict):
            return False
        settings = state.get("settings")
        if settings is None:
            return False
        settings = settings.copy()
        settings.offset_ms = int(state.get("offset_ms", settings.offset_ms))
        return self.load(state.get("cues"), state.get("path"), settings, preserve_offset=False)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.disable()
        self._ui_obscured_owners.clear()
        self._ui_obscured_requested = False
        self._ui_obscured = False
        self._delete_overlay()


class OnlineSubtitleController(object):
    """Move network and ZIP work off the GUI thread and activate on polling."""

    def __init__(
        self,
        session,
        metadata_provider,
        position_provider,
        on_message=None,
        on_changed=None,
        before_activate=None,
        settings_loader=None,
        client_factory=None,
        renderer=None,
        timer_factory=None,
        is_paused=None,
    ):
        self.session = session
        self.metadata_provider = metadata_provider
        self.on_message = on_message
        self.on_changed = on_changed
        self.before_activate = before_activate
        self.is_paused = is_paused
        self.settings_loader = settings_loader or load_subtitle_settings
        self.client_factory = client_factory
        self.renderer = renderer or IndependentSubtitleRenderer(
            session, position_provider, timer_factory=timer_factory
        )
        timer_type = timer_factory if timer_factory is not None else eTimer
        self.poll_timer = timer_type() if callable(timer_type) else None
        _connect_timer(self.poll_timer, self._poll)
        self._events = deque()
        self._event_lock = threading.Lock()
        self._generation = 0
        self._busy = False
        self._automatic = False
        self._finish_callback = None
        self._browser_results_callback = None
        self._last_download_succeeded = False
        self._last_download_status = ""
        self._metadata = {}
        self._settings = None
        self._closed = False
        self._auto_started = False
        self._auto_context = None
        self._browser_state = None
        self._browser_generation = 0
        self._operation_context = None
        self._download_provider = None
        # Reopening a player must not create another set of network workers
        # while the previous player's bounded HTTP calls are still finishing.
        self._source_executor = _NATIVE_SOURCE_EXECUTOR
        self._source_futures = set()
        self._source_lock = threading.Lock()

    @property
    def is_loaded(self):
        return self.renderer.is_loaded

    @property
    def loaded_path(self):
        return self.renderer.loaded_path

    @property
    def busy(self):
        return self._busy

    @property
    def last_download_succeeded(self):
        return self._last_download_succeeded

    @property
    def last_download_status(self):
        return self._last_download_status

    def _notify(self, message):
        if callable(self.on_message):
            try:
                self.on_message(message)
            except Exception:
                pass

    def _changed(self, value):
        if callable(self.on_changed):
            try:
                self.on_changed(value)
            except Exception:
                pass

    def _settings_value(self):
        try:
            return self.settings_loader()
        except Exception:
            return load_subtitle_settings()

    @staticmethod
    def _credentials_configured(settings, provider=None):
        provider = provider or getattr(settings, "provider", "subdl")
        if provider in POLISH_PROVIDERS:
            return provider in polish_search_providers(getattr(settings, "primary_language", ""))
        if provider not in SEARCH_PROVIDERS:
            return False
        checker = getattr(settings, "credentials_configured", None)
        if callable(checker):
            try:
                return bool(checker(provider))
            except TypeError:
                try:
                    return bool(checker())
                except Exception:
                    return False
            except Exception:
                return False
        provider = provider or getattr(settings, "provider", "subdl")
        if provider == "opensubtitles":
            return bool(getattr(settings, "opensubtitles_api_key", ""))
        if provider == "subsource":
            return bool(getattr(settings, "subsource_api_key", ""))
        return bool(getattr(settings, "api_key", ""))

    def _settings_for_provider(self, provider=None):
        settings = self._settings_value()
        if provider in PROVIDERS and getattr(settings, "provider", None) != provider:
            copier = getattr(settings, "copy", None)
            if callable(copier):
                settings = copier()
            settings.provider = provider
        return settings

    def _client(self, settings, provider=None):
        if callable(self.client_factory):
            return self.client_factory(settings.api_key)
        if provider in POLISH_PROVIDERS:
            return subtitle_client(settings, provider=provider)
        return subtitle_client(settings)

    def _current_context(self):
        # Always use the original playback metadata. A corrected search title
        # must not become the identity of the film currently on the decoder.
        return subtitle_search_context(
            self._metadata_value(), self._settings_value(),
            (id(self), self._browser_generation),
        )

    def _context_is_current(self, generation):
        return bool(
            not self._closed and generation == self._generation
            and self._operation_context == self._current_context()
        )

    def _job_is_current(self, generation):
        # Worker checks must not read decoder widgets or settings on the GUI
        # thread. Service/preferences are revalidated when data returns.
        return bool(not self._closed and generation == self._generation)

    def _submit_source_job(self, generation, function, *args):
        if not self._job_is_current(generation):
            return None
        try:
            future = self._source_executor.submit(function, *args)
        except RuntimeError:
            if not self._job_is_current(generation):
                return None
            raise
        with self._source_lock:
            if self._job_is_current(generation):
                self._source_futures.add(future)
            else:
                future.cancel()
        def finished(completed):
            with self._source_lock:
                self._source_futures.discard(completed)
        future.add_done_callback(finished)
        return future

    def _cancel_source_jobs(self):
        with self._source_lock:
            futures = list(self._source_futures)
            self._source_futures.clear()
        for future in futures:
            future.cancel()

    def _fresh_search_metadata(self, search_metadata=None):
        live = self._metadata_value()
        query = dict(search_metadata) if isinstance(search_metadata, dict) else dict(self._metadata or live)
        # A manual query may replace the title and remove stale catalogue IDs.
        # Only decoder-local release/timing hints belong to live playback.
        for name in ("release_hint", "fps", "resolution", "duration_seconds"):
            query[name] = live.get(name, "" if name == "release_hint" else 0)
        return query

    def revalidate_result(self, result, search_metadata=None):
        """Refresh matching hints without rewriting the user's search title."""
        if search_metadata is None and isinstance(result, dict):
            search_metadata = result.get("_search_metadata")
        items = annotate_subtitle_results(self._fresh_search_metadata(search_metadata), [result])
        return items[0] if items else {}

    def refresh_browser_results(self, metadata, results):
        settings = self._settings_value()
        language_order = {language: index for index, language in enumerate((
            settings.primary_language, settings.secondary_language)) if language}
        provider_order = {provider: index for index, provider in enumerate(SEARCH_PROVIDERS)}
        indexed = list(enumerate(annotate_subtitle_results(metadata, results)))
        indexed.sort(key=lambda pair: (
            language_order.get(normalize_subtitle_language(pair[1].get("language")), len(language_order)),
            subtitle_result_sort_key(pair[1]),
            provider_order.get(pair[1].get("provider"), len(provider_order)),
            pair[0],
        ))
        ordered = [item for unused, item in indexed]
        for item in ordered:
            item["_search_metadata"] = dict(metadata)
        return ordered

    def begin_external_operation(self):
        """Let a web selection supersede pending receiver subtitle work."""
        if self._closed:
            return None
        token = self._generation + 1
        self.cancel_pending()
        # A completion callback can start a newer native action. Do not let
        # this external action adopt that newer action's generation token.
        return token

    def operation_is_current(self, token):
        return bool(not self._closed and token is not None
                    and token == self._generation)

    def _provider_search(self, provider, settings, metadata, generation):
        languages = []
        for value in (settings.primary_language, settings.secondary_language):
            language = normalize_subtitle_language(value)
            if language and language not in languages:
                languages.append(language)
        if provider in POLISH_PROVIDERS:
            languages = ["pl"]
        with subtitle_request_scope(lambda: self._job_is_current(generation)):
            entries = self._client(settings, provider).search(metadata, languages)
            check_subtitle_job()
        results = []
        for result in entries:
            if not isinstance(result, dict):
                continue
            language = normalize_subtitle_language(result.get("language"))
            if language not in languages:
                continue
            if not settings.hearing_impaired and result.get("hearing_impaired"):
                continue
            item = dict(result)
            item["provider"] = provider
            item["language"] = language
            results.append(item)
        return results

    def _search_sources(self, configured, metadata, generation):
        """Wait only for bounded provider calls; keep source order stable."""
        combined, failures = [], []
        if not configured:
            return combined, failures
        jobs = []
        for provider, settings in configured:
            future = self._submit_source_job(generation, self._provider_search,
                                             provider, settings, metadata, generation)
            if future is None:
                break
            jobs.append((provider, settings, future))
        for provider, settings, future in jobs:
            while self._job_is_current(generation):
                try:
                    combined.extend(future.result(timeout=.1))
                    break
                except FutureTimeout:
                    continue
                except Exception as error:
                    failures.append((provider, settings, error))
                    break
            if not self._job_is_current(generation):
                for unused_provider, unused_settings, pending in jobs:
                    pending.cancel()
                break
        return combined, failures

    def _provider_text(self, message, settings=None, provider=None):
        settings = settings or self._settings or self._settings_value()
        return _(message).replace(
            "SubDL", provider_display_name(provider or getattr(settings, "provider", "subdl"))
        )

    def configured(self, provider=None):
        settings = self._settings_for_provider(provider)
        return bool(
            settings.enabled and self._credentials_configured(settings, provider)
        )

    def provider_name(self, provider=None):
        settings = self._settings_for_provider(provider)
        return provider_display_name(getattr(settings, "provider", "subdl"))

    @staticmethod
    def manual_providers():
        """Return every receiver-native provider offered by the yellow key."""
        return tuple(
            (provider, provider_display_name(provider))
            for provider in PROVIDERS
        )

    def _credential_message(self, settings):
        provider = getattr(settings, "provider", "subdl")
        if provider == "opensubtitles":
            prefix = provider_display_name(provider) + " • "
            if not getattr(settings, "opensubtitles_api_key", ""):
                return prefix + _("API KEY REQUIRED")
        return self._provider_text(
            _ERROR_MESSAGES["provider_key_missing"], settings
        )

    def _queue(self, generation, name, payload):
        with self._event_lock:
            if self._closed or generation != self._generation:
                return
            self._events.append((generation, name, payload))
        # The worker thread only appends data.  eTimer belongs to Enigma2's
        # GUI thread and is already kept alive while ``_busy`` is true.

    def _arm_poll(self):
        if self.poll_timer is not None and not self._closed:
            try:
                self.poll_timer.start(POLL_MS, True)
            except Exception:
                pass

    def _metadata_value(self):
        try:
            value = self.metadata_provider() if callable(self.metadata_provider) else {}
        except Exception:
            value = {}
        return dict(value) if isinstance(value, dict) else {}

    def start_automatic_search(self):
        """Prepare the combined results list without selecting a subtitle."""
        if self._closed or self._busy:
            return False
        settings = self._settings_value()
        if not settings.enabled or settings.search_mode != "automatic":
            return False
        context = self._current_context()
        if self._auto_started and self._auto_context == context:
            return False
        saved = self._browser_state
        if (isinstance(saved, dict) and saved.get("context") == context
                and (saved.get("searched") or saved.get("manual_title"))):
            self._auto_started = True
            self._auto_context = context
            return False
        return self.search_combined(None, automatic=True)

    def attach_automatic_search(self, on_results, on_finished=None, metadata=None):
        """Let an opened results page follow the already-running search."""
        if (not callable(on_results) or not self._busy or not self._automatic
                or not self._context_is_current(self._generation)):
            return False
        if isinstance(metadata, dict) and subtitle_search_context(
                metadata, self._settings_value(), (id(self), self._browser_generation)
        ) != self._operation_context:
            return False
        self._browser_results_callback = on_results
        self._finish_callback = on_finished
        return True

    def detach_automatic_search(self, on_results):
        """Closing a results page must not cancel background preparation."""
        if not self._automatic or self._browser_results_callback != on_results:
            return False
        self._browser_results_callback = None
        self._finish_callback = None
        return True

    def search(self, automatic=False, on_finished=None, provider=None):
        if automatic:
            # Compatibility callers get the same search-only automatic mode.
            return self.search_combined(None, on_finished, automatic=True)
        if self._busy and self._automatic and not automatic:
            self.cancel_pending()
        if self._closed or self._busy:
            if not automatic:
                self._notify(N_("A subtitle search is already running."))
            return False
        if provider is not None and provider not in SEARCH_PROVIDERS:
            return False
        settings = self._settings_for_provider(provider)
        if not settings.enabled:
            if not automatic:
                self._notify(N_("Independent subtitles are turned off in settings."))
            return False
        requested_provider = provider or settings.provider
        configured = []
        if self._credentials_configured(settings, requested_provider):
            configured.append((requested_provider, settings))
        if not configured:
            if not automatic:
                self._notify(self._credential_message(settings))
            return False
        metadata = self._metadata_value()
        if not subtitle_search_available(metadata):
            if not automatic:
                self._notify(_ERROR_MESSAGES["invalid_search"])
            return False
        self._generation += 1
        generation = self._generation
        self._operation_context = self._current_context()
        self._download_provider = None
        self._busy = True
        self._automatic = bool(automatic)
        self._finish_callback = on_finished
        self._metadata = metadata
        self._settings = settings
        self._last_download_succeeded = False
        self._last_download_status = ""
        def worker():
            try:
                languages = []
                for value in (settings.primary_language, settings.secondary_language):
                    if value and value not in languages:
                        languages.append(value)
                results, failures = self._search_sources(configured, metadata, generation)
                if failures and not results and len(configured) == 1:
                    raise failures[0][2]
                results = annotate_subtitle_results(metadata, results)
                language_order = {
                    language: index
                    for index, language in enumerate(languages)
                }
                indexed = list(enumerate(results))
                indexed.sort(key=lambda pair: (
                    language_order.get(
                        str(pair[1].get("language") or "").lower(),
                        len(language_order),
                    ),
                    _result_compatibility_sort(pair[1]),
                    bool(pair[1].get("hearing_impaired")),
                    pair[0],
                ))
                results = [item for unused_index, item in indexed]
                self._queue(generation, "results", results)
            except Exception as error:
                self._queue(generation, "error", error)

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
        self._arm_poll()
        return True

    def search_combined(
        self, on_results, on_finished=None, metadata_override=None, automatic=False
    ):
        """Search every configured native provider for the receiver page."""
        if self._busy and self._automatic and not automatic:
            self.cancel_pending()
        if self._closed or self._busy:
            if not automatic:
                self._notify(N_("A subtitle search is already running."))
            return False
        if not callable(on_results) and not automatic:
            return False
        settings = self._settings_value()
        if not settings.enabled or (automatic and settings.search_mode != "automatic"):
            if not automatic:
                self._notify(N_("Independent subtitles are turned off in settings."))
            return False
        metadata = (
            dict(metadata_override)
            if isinstance(metadata_override, dict)
            else self._metadata_value()
        )
        if not subtitle_search_available(metadata):
            if not automatic:
                self._notify(_ERROR_MESSAGES["invalid_search"])
            return False

        configured = []
        missing = []
        for provider in PROVIDERS:
            provider_settings = self._settings_for_provider(provider)
            if self._credentials_configured(provider_settings, provider):
                configured.append((provider, provider_settings))
            else:
                missing.append((provider, provider_settings))
        for provider in polish_search_providers(getattr(settings, "primary_language", "")):
            configured.append((provider, settings.copy()))
        if automatic and not configured:
            return False

        self._generation += 1
        generation = self._generation
        self._operation_context = self._current_context()
        self._download_provider = None
        self._busy = True
        self._automatic = bool(automatic)
        self._finish_callback = on_finished
        self._browser_results_callback = on_results
        self._metadata = metadata
        self._settings = settings
        if automatic:
            self._auto_started = True
            self._auto_context = self._operation_context

        def worker():
            try:
                combined, failures = self._search_sources(configured, metadata, generation)
                combined = annotate_subtitle_results(metadata, combined)
                self._queue(
                    generation,
                    "browser_results",
                    (combined, failures, missing),
                )
            except Exception as error:
                self._queue(generation, "error", error)

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
        self._arm_poll()
        return True

    def download_result(self, result, on_finished=None):
        """Download one result selected on the combined receiver page."""
        if self._busy and self._automatic:
            self.cancel_pending()
        if self._closed or self._busy or not isinstance(result, dict):
            if self._busy:
                self._notify(N_("A subtitle search is already running."))
            return False
        provider = str(result.get("provider") or "").lower()
        if provider not in SEARCH_PROVIDERS:
            return False
        settings = self._settings_for_provider(provider)
        if not settings.enabled:
            self._notify(N_("Independent subtitles are turned off in settings."))
            return False
        if not self._credentials_configured(settings, provider):
            self._notify(self._credential_message(settings))
            return False
        requested = {normalize_subtitle_language(settings.primary_language),
                     normalize_subtitle_language(settings.secondary_language)}
        language = normalize_subtitle_language(result.get("language"))
        if (not language or language not in requested
                or (provider in POLISH_PROVIDERS and language != "pl")):
            self._notify(self._provider_text(
                N_("No matching subtitles were found on SubDL."), settings, provider
            ))
            return False
        search_metadata = result.get("_search_metadata")
        metadata = self._fresh_search_metadata(
            search_metadata if isinstance(search_metadata, dict) else self._metadata_value()
        )
        if not subtitle_search_available(metadata):
            self._notify(_ERROR_MESSAGES["invalid_search"])
            return False
        self._generation += 1
        generation = self._generation
        self._operation_context = self._current_context()
        self._busy = True
        self._automatic = False
        self._finish_callback = on_finished
        self._browser_results_callback = None
        self._metadata = metadata
        self._settings = settings
        self._last_download_succeeded = False
        self._last_download_status = ""
        self._start_download(generation, dict(result))
        self._arm_poll()
        return True

    def _error_text_for(self, error, settings, provider=None):
        code = getattr(error, "code", "provider_unavailable")
        if code == "provider_key_missing":
            return self._credential_message(settings)
        if code == "provider_login_required":
            return "OpenSubtitles.com • {} / {}".format(
                _("Enter the username"), _("Enter the password")
            )
        return self._provider_text(
            _ERROR_MESSAGES.get(code, _ERROR_MESSAGES["provider_unavailable"]),
            settings, provider,
        )

    def _error_text(self, error):
        settings = self._settings or self._settings_value()
        return self._error_text_for(error, settings, self._download_provider)

    def _poll(self):
        if self._closed:
            return
        if self._automatic and self._settings_value().search_mode != "automatic":
            self._auto_started = False
            self._auto_context = None
            self.cancel_pending()
            return
        events = []
        with self._event_lock:
            while self._events:
                events.append(self._events.popleft())
        for generation, name, payload in events:
            if generation != self._generation:
                continue
            if not self._context_is_current(generation):
                self._last_download_status = "cancelled"
                self._finish(generation)
                continue
            if name == "results":
                self._results_ready(generation, payload)
            elif name == "browser_results":
                self._browser_results_ready(generation, payload)
            elif name == "download":
                self._download_ready(generation, payload)
            elif name == "error":
                if self._automatic:
                    self._browser_results_ready(generation, (
                        [], [(self._settings.provider, self._settings, payload)], []
                    ))
                else:
                    self._notify(self._error_text(payload))
                    self._finish(generation)
        if self._busy:
            self._arm_poll()

    def _browser_results_ready(self, generation, payload):
        try:
            results, failures, missing = payload
        except (TypeError, ValueError):
            results, failures, missing = [], [], []
        ordered = self.refresh_browser_results(
            self._fresh_search_metadata(), results if isinstance(results, list) else []
        )
        notices = []
        for unused_provider, provider_settings in missing:
            notices.append(self._credential_message(provider_settings))
        for provider, provider_settings, error in failures:
            notices.append(self._error_text_for(error, provider_settings, provider))
        if self._automatic:
            self._browser_state = {
                "context": self._operation_context,
                "content_context": subtitle_search_context(
                    self._metadata_value(), None, (id(self), self._browser_generation)
                ),
                "metadata": deepcopy(self._fresh_search_metadata()),
                "manual_title": False, "searched": True,
                "results": deepcopy(ordered[:100]),
                "provider_filter": "all", "selected": 0, "top": 0,
                "message": "  |  ".join(notices)[:240],
            }
        callback = self._browser_results_callback
        self._browser_results_callback = None
        if callable(callback):
            try:
                callback(ordered, notices)
            except Exception:
                pass
        self._finish(generation)

    def _results_ready(self, generation, results):
        if self._automatic:
            self._browser_results_ready(generation, (list(results or ()), [], []))
            return
        results = self.refresh_browser_results(self._fresh_search_metadata(), results)
        if not results:
            if not self._automatic:
                self._notify(self._provider_text(
                    N_("No matching subtitles were found on SubDL.")
                ))
            self._finish(generation)
            return
        try:
            from Screens.ChoiceBox import ChoiceBox
        except Exception:
            ChoiceBox = None
        opener = getattr(self.session, "openWithCallback", None)
        if ChoiceBox is None or not callable(opener):
            self._notify(N_("Subtitle result selection is unavailable on this image."))
            self._finish(generation)
            return
        choices = []
        for result in results[:40]:
            language = str(result.get("language") or "").upper()
            suffix = " • HI" if result.get("hearing_impaired") else ""
            try:
                fps = float(result.get("fps") or 0)
            except (TypeError, ValueError, OverflowError):
                fps = 0
            if fps > 0:
                fps_text = ("{:.3f}".format(fps)).rstrip("0").rstrip(".")
                suffix = " • {} FPS{}".format(fps_text, suffix)
            label = "{} • {}{}".format(
                str(result.get("release") or _("Subtitle"))[:92],
                language,
                suffix,
            )
            choices.append((label[:120], result))

        def selected(selection):
            if generation != self._generation or self._closed:
                return
            if not selection:
                self._finish(generation)
                return
            try:
                result = selection[1]
            except (IndexError, KeyError, TypeError):
                self._finish(generation)
                return
            self._start_download(generation, result)

        try:
            opener(
                selected,
                ChoiceBox,
                self._provider_text("SubDL subtitles"),
                choices,
            )
        except Exception:
            self._notify(N_("Subtitle result selection is unavailable on this image."))
            self._finish(generation)

    def _start_download(self, generation, result):
        if self._automatic:
            return
        if not self._context_is_current(generation):
            self._finish(generation)
            return
        result = self.revalidate_result(result)
        if result.get("identity_match") is False:
            self._last_download_status = "different"
            if not self._automatic:
                self._notify(_("Different version"))
            self._finish(generation)
            return
        settings = self._settings
        provider = str(result.get("provider") or "").lower()
        if provider in PROVIDERS and provider != getattr(settings, "provider", None):
            settings = settings.copy()
            settings.provider = provider
            self._settings = settings
        self._download_provider = provider
        metadata = dict(self._metadata)
        release = " ".join(str(result.get("release") or "Subtitle").split())[:120]
        language = str(result.get("language") or "").upper()[:3]
        try:
            fps = float(result.get("fps") or 0)
        except (TypeError, ValueError, OverflowError):
            fps = 0
        log_event(
            "subtitles",
            "{}-select language={} fps={} release={}".format(
                "automatic" if self._automatic else "manual",
                language or "--",
                ("{:.3f}".format(fps)).rstrip("0").rstrip(".")
                if fps > 0 else "unknown",
                release,
            ),
        )

        def worker():
            try:
                with subtitle_request_scope(lambda: self._job_is_current(generation)):
                    content = self._client(settings, provider).download(result)
                    check_subtitle_job()
                cues = parse_subtitle(
                    content, hearing_impaired=settings.hearing_impaired,
                    language=result.get("language", ""),
                )
                unused_cues, fps_factor, timeline_state = retime_subtitle_cues(
                    cues, result
                )
                log_event(
                    "subtitles",
                    "timing compatibility={} video_fps={} subtitle_fps={} "
                    "factor={:.6f} timeline={}".format(
                        result.get("compatibility_status") or "unknown",
                        result.get("video_fps") or 0,
                        result.get("subtitle_fps") or 0,
                        fps_factor,
                        timeline_state,
                    ),
                )
                if not self._job_is_current(generation):
                    return
                path = save_subtitle_file(content, metadata, result)
                self._queue(
                    generation,
                    "download",
                    (cues, path, settings, timeline_state, result),
                )
            except Exception as error:
                self._queue(generation, "error", error)

        self._submit_source_job(generation, worker)

    def _download_ready(self, generation, payload):
        if self._automatic:
            return
        if not self._context_is_current(generation):
            self._last_download_status = "cancelled"
            self._finish(generation)
            return
        try:
            if len(payload) == 5:
                cues, path, settings, timeline_state, result = payload
                result = self.revalidate_result(result)
                if result.get("identity_match") is False:
                    timeline_state = "mismatch"
                else:
                    cues, unused_factor, timeline_state = retime_subtitle_cues(cues, result)
            elif len(payload) == 4:
                cues, path, settings, timeline_state = payload
            else:
                cues, path, settings = payload
                timeline_state = "unknown"
        except (TypeError, ValueError):
            self._finish(generation)
            return
        if timeline_state == "mismatch":
            self._last_download_status = "different"
            if not self._automatic:
                self._notify(_("Different version"))
            self._finish(generation)
            return
        # Font, position and global timing edits do not invalidate a search.
        # Apply their current values rather than stale worker-captured values.
        current_settings = self._settings_value()
        settings = settings.copy()
        for name in ("font_size", "font_color", "background", "vertical_position", "offset_ms"):
            if hasattr(current_settings, name):
                setattr(settings, name, getattr(current_settings, name))
        rollback = None
        if callable(self.before_activate):
            try:
                rollback = self.before_activate()
            except Exception:
                if not self._automatic:
                    self._notify(N_("Could not start the independent subtitle renderer."))
                self._finish(generation)
                return
        if not self._context_is_current(generation):
            if callable(rollback):
                try:
                    rollback()
                except Exception:
                    pass
            self._last_download_status = "cancelled"
            self._finish(generation)
            return
        if len(payload) == 5:
            latest = self.revalidate_result(payload[4])
            if latest.get("identity_match") is False:
                state = "mismatch"
            else:
                cues, unused_factor, state = retime_subtitle_cues(payload[0], latest)
            if state == "mismatch":
                if callable(rollback):
                    try:
                        rollback()
                    except Exception:
                        pass
                self._last_download_status = "different"
                if not self._automatic:
                    self._notify(_("Different version"))
                self._finish(generation)
                return
        try:
            loaded = bool(self.renderer.load(cues, path, settings))
        except Exception:
            loaded = False
        if loaded:
            self._last_download_succeeded = True
            self._last_download_status = "loaded"
            if callable(self.is_paused):
                try:
                    if self.is_paused():
                        self.renderer.pause()
                except Exception:
                    pass
            self._changed(("online", path))
        else:
            self._last_download_status = "error"
            if callable(rollback):
                try:
                    rollback()
                except Exception:
                    pass
            if not self._automatic:
                self._notify(N_("Could not start the independent subtitle renderer."))
        self._finish(generation)

    def _finish(self, generation):
        if generation != self._generation:
            return
        callback = self._finish_callback
        self._finish_callback = None
        self._browser_results_callback = None
        self._busy = False
        self._automatic = False
        if callable(callback):
            try:
                callback()
            except Exception:
                pass

    def cancel_pending(self):
        """Invalidate receiver-page work without disabling active subtitles."""
        if self._closed:
            return False
        callback = self._finish_callback
        self._generation += 1
        self._cancel_source_jobs()
        self._busy = False
        self._automatic = False
        self._operation_context = None
        self._last_download_succeeded = False
        self._last_download_status = "cancelled"
        self._finish_callback = None
        self._browser_results_callback = None
        with self._event_lock:
            self._events.clear()
        if self.poll_timer is not None:
            try:
                self.poll_timer.stop()
            except Exception:
                pass
        # Release an open native results page after another action supersedes
        # its request. Closed screens already make their callbacks no-ops.
        if callable(callback):
            try:
                callback()
            except Exception:
                pass
        return True

    def open_sync(self, on_finished=None):
        if not self.is_loaded:
            self._notify(N_("Load an independent subtitle before synchronizing it."))
            return False
        try:
            from Screens.ChoiceBox import ChoiceBox
        except Exception:
            ChoiceBox = None
        opener = getattr(self.session, "openWithCallback", None)
        if ChoiceBox is None or not callable(opener):
            self._notify(N_("Subtitle synchronization is unavailable on this image."))
            return False
        choices = (
            (_("Show 1 second earlier"), -1000),
            (_("Show 0.5 seconds earlier"), -500),
            (_("Reset subtitle timing"), "reset"),
            (_("Show 0.5 seconds later"), 500),
            (_("Show 1 second later"), 1000),
        )
        revision = self.renderer.content_revision

        def selected(selection):
            try:
                if self._closed or self.renderer.content_revision != revision:
                    return
                value = selection[1] if selection else None
                if value == "reset":
                    offset = self.renderer.adjust_offset(absolute_ms=0)
                elif value is not None:
                    offset = self.renderer.adjust_offset(delta_ms=value)
                else:
                    offset = None
                if offset is not None:
                    self._notify(
                        N_("Subtitle timing offset: {:+.1f} seconds").format(
                            offset / 1000.0
                        )
                    )
            finally:
                if callable(on_finished):
                    on_finished()

        try:
            opener(selected, ChoiceBox, _("Independent subtitle synchronization"), choices)
            return True
        except Exception:
            self._notify(N_("Subtitle synchronization is unavailable on this image."))
            return False

    def snapshot(self):
        return self.renderer.snapshot()

    def restore(self, state):
        return self.renderer.restore(state)

    def disable(self, notify=True):
        if notify:
            self.cancel_pending()
        had_subtitle = self.renderer.disable()
        if had_subtitle and notify:
            self._changed(None)
        return True

    def pause(self):
        self.renderer.pause()

    def resume(self):
        self.renderer.resume()

    def prepare_seek(self, target_seconds, origin_seconds=None):
        preparer = getattr(self.renderer, "prepare_seek", None)
        if callable(preparer):
            return preparer(target_seconds, origin_seconds=origin_seconds)
        return False

    def after_seek(self, position_seconds=None):
        self.renderer.after_seek(position_seconds)

    def cancel_seek(self):
        self.renderer.cancel_seek()

    def set_infobar_visible(self, visible):
        setter = getattr(self.renderer, "set_infobar_visible", None)
        if callable(setter):
            setter(visible)

    def set_ui_obscured(self, obscured):
        setter = getattr(self.renderer, "set_ui_obscured", None)
        if callable(setter):
            return bool(setter(obscured))
        return False

    def acquire_ui_obscured(self):
        acquirer = getattr(self.renderer, "acquire_ui_obscured", None)
        if callable(acquirer):
            return acquirer()
        return None

    def release_ui_obscured(self, token):
        releaser = getattr(self.renderer, "release_ui_obscured", None)
        if callable(releaser):
            return bool(releaser(token))
        return False

    def reset_for_service_change(self):
        self._browser_state = None
        self._browser_generation += 1
        self._generation += 1
        self._cancel_source_jobs()
        self._busy = False
        self._finish_callback = None
        self._browser_results_callback = None
        self._last_download_succeeded = False
        self._automatic = False
        self._operation_context = None
        self._auto_started = False
        self._auto_context = None
        with self._event_lock:
            self._events.clear()
        self.renderer.disable()
        reset_clock = getattr(self.renderer, "reset_clock", None)
        if callable(reset_clock):
            reset_clock()

    def close(self):
        if self._closed:
            return
        self._browser_state = None
        self._browser_generation += 1
        self._closed = True
        self._generation += 1
        self._cancel_source_jobs()
        self._busy = False
        self._operation_context = None
        self._finish_callback = None
        self._browser_results_callback = None
        if self.poll_timer is not None:
            try:
                self.poll_timer.stop()
            except Exception:
                pass
        self.renderer.close()

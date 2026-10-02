# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Independent Enigma2 subtitle overlay and online-provider workflow."""

from collections import deque
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
    provider_display_name,
    retime_subtitle_cues,
    save_subtitle_file,
    subtitle_client,
)
from .subtitle_settings import PROVIDERS, load_subtitle_settings

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
        # Keep the compact transparent window alive for the whole subtitle
        # session. Repeatedly hiding/showing a dialog can lose its framebuffer
        # layer on some Vu+/OpenPLi ServiceApp combinations.
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
            self["subtitle"].hide()


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
            self._delete_overlay()
        self.overlay = self._new_overlay(settings)
        return self.overlay

    def load(self, cues, path, settings):
        if self.closed or not cues or not path:
            return False
        overlay = self._ensure_overlay(settings)
        if overlay is None:
            return False
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
            return False
        self.cues = tuple(cues)
        self.starts = tuple(cue.start_ms for cue in self.cues)
        self.loaded_path = str(path)
        self.settings = settings.copy()
        self.offset_ms = int(settings.offset_ms)
        self._last_text = ""
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
        """Hide the compact OSD while a full-screen subtitle UI owns display."""
        obscured = bool(obscured)
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
            log_event("subtitles", "overlay-suspended reason=subtitle-browser")
            return True
        # Force the current cue through even if its text did not change while
        # the full-screen browser was open. A blank cue deliberately leaves the
        # compact window hidden, preventing a transparent video-plane cutout.
        self._last_text = None
        if self.is_loaded and not self.closed:
            self._tick()
        log_event("subtitles", "overlay-restored reason=subtitle-browser")
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
            self.cues, position_ms, self.offset_ms, self.starts
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
        self._ui_obscured = False
        self.cues = ()
        self.starts = ()
        self.loaded_path = None
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
        }

    def restore(self, state):
        if not isinstance(state, dict):
            return False
        settings = state.get("settings")
        if settings is None:
            return False
        settings = settings.copy()
        settings.offset_ms = int(state.get("offset_ms", settings.offset_ms))
        return self.load(state.get("cues"), state.get("path"), settings)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.disable()
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

    def _client(self, settings):
        if callable(self.client_factory):
            return self.client_factory(settings.api_key)
        return subtitle_client(settings)

    def _provider_text(self, message, settings=None):
        settings = settings or self._settings or self._settings_value()
        return _(message).replace(
            "SubDL", provider_display_name(getattr(settings, "provider", "subdl"))
        )

    def configured(self, provider=None):
        settings = self._settings_for_provider(provider)
        return bool(
            settings.enabled and self._credentials_configured(settings)
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
        if self._closed or self._auto_started or self.is_loaded:
            return False
        settings = self._settings_value()
        if not (
            settings.enabled
            and settings.search_mode == "automatic"
            and self._credentials_configured(settings)
        ):
            return False
        opened = self.search(automatic=True)
        self._auto_started = bool(opened)
        return opened

    def search(self, automatic=False, on_finished=None, provider=None):
        if self._closed or self._busy:
            if not automatic:
                self._notify(N_("A subtitle search is already running."))
            return False
        if provider is not None and provider not in PROVIDERS:
            return False
        settings = self._settings_for_provider(provider)
        if not settings.enabled:
            if not automatic:
                self._notify(N_("Independent subtitles are turned off in settings."))
            return False
        if not self._credentials_configured(settings):
            if not automatic:
                self._notify(self._credential_message(settings))
            return False
        metadata = self._metadata_value()
        if len(" ".join(str(metadata.get("title") or "").split())) < 2:
            if not automatic:
                self._notify(_ERROR_MESSAGES["invalid_search"])
            return False
        self._generation += 1
        generation = self._generation
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
                results = self._client(settings).search(
                    metadata, languages
                )
                if not settings.hearing_impaired:
                    results = [
                        result for result in results
                        if not result.get("hearing_impaired")
                    ]
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
        self, on_results, on_finished=None, metadata_override=None
    ):
        """Search every configured native provider for the receiver page."""
        if self._closed or self._busy:
            self._notify(N_("A subtitle search is already running."))
            return False
        if not callable(on_results):
            return False
        settings = self._settings_value()
        if not settings.enabled:
            self._notify(N_("Independent subtitles are turned off in settings."))
            return False
        metadata = (
            dict(metadata_override)
            if isinstance(metadata_override, dict)
            else self._metadata_value()
        )
        if len(" ".join(str(metadata.get("title") or "").split())) < 2:
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

        self._generation += 1
        generation = self._generation
        self._busy = True
        self._automatic = False
        self._finish_callback = on_finished
        self._browser_results_callback = on_results
        self._metadata = metadata
        self._settings = settings

        def worker():
            combined = []
            failures = []
            for provider, provider_settings in configured:
                try:
                    languages = []
                    for value in (
                        provider_settings.primary_language,
                        provider_settings.secondary_language,
                    ):
                        if value and value not in languages:
                            languages.append(value)
                    provider_results = self._client(provider_settings).search(
                        metadata, languages
                    )
                    if not provider_settings.hearing_impaired:
                        provider_results = [
                            result for result in provider_results
                            if not result.get("hearing_impaired")
                        ]
                    for result in provider_results:
                        if not isinstance(result, dict):
                            continue
                        item = dict(result)
                        item["provider"] = provider
                        combined.append(item)
                except Exception as error:
                    failures.append((provider, provider_settings, error))
            combined = annotate_subtitle_results(metadata, combined)
            self._queue(
                generation,
                "browser_results",
                (combined, failures, missing),
            )

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()
        self._arm_poll()
        return True

    def download_result(self, result, on_finished=None):
        """Download one result selected on the combined receiver page."""
        if self._closed or self._busy or not isinstance(result, dict):
            if self._busy:
                self._notify(N_("A subtitle search is already running."))
            return False
        provider = str(result.get("provider") or "").lower()
        if provider not in PROVIDERS:
            return False
        settings = self._settings_for_provider(provider)
        if not settings.enabled:
            self._notify(N_("Independent subtitles are turned off in settings."))
            return False
        if not self._credentials_configured(settings, provider):
            self._notify(self._credential_message(settings))
            return False
        metadata = self._metadata_value()
        if len(" ".join(str(metadata.get("title") or "").split())) < 2:
            self._notify(_ERROR_MESSAGES["invalid_search"])
            return False
        self._generation += 1
        generation = self._generation
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

    def _error_text_for(self, error, settings):
        code = getattr(error, "code", "provider_unavailable")
        if code == "provider_key_missing":
            return self._credential_message(settings)
        if code == "provider_login_required":
            return "OpenSubtitles.com • {} / {}".format(
                _("Enter the username"), _("Enter the password")
            )
        return self._provider_text(
            _ERROR_MESSAGES.get(code, _ERROR_MESSAGES["provider_unavailable"]),
            settings,
        )

    def _error_text(self, error):
        settings = self._settings or self._settings_value()
        return self._error_text_for(error, settings)

    def _poll(self):
        if self._closed:
            return
        events = []
        with self._event_lock:
            while self._events:
                events.append(self._events.popleft())
        for generation, name, payload in events:
            if generation != self._generation:
                continue
            if name == "results":
                self._results_ready(generation, payload)
            elif name == "browser_results":
                self._browser_results_ready(generation, payload)
            elif name == "download":
                self._download_ready(generation, payload)
            elif name == "error":
                if (
                    not self._automatic
                    or getattr(payload, "code", "") == "provider_login_required"
                ):
                    self._notify(self._error_text(payload))
                self._finish(generation)
        if self._busy:
            self._arm_poll()

    def _browser_results_ready(self, generation, payload):
        try:
            results, failures, missing = payload
        except (TypeError, ValueError):
            results, failures, missing = [], [], []
        settings = self._settings or self._settings_value()
        language_order = {
            language: index for index, language in enumerate((
                getattr(settings, "primary_language", ""),
                getattr(settings, "secondary_language", ""),
            )) if language
        }
        provider_order = {
            provider: index for index, provider in enumerate(PROVIDERS)
        }
        indexed = list(enumerate(results if isinstance(results, list) else []))
        indexed.sort(key=lambda pair: (
            language_order.get(
                str(pair[1].get("language") or "").lower(),
                len(language_order),
            ),
            _result_compatibility_sort(pair[1]),
            provider_order.get(pair[1].get("provider"), len(provider_order)),
            pair[0],
        ))
        ordered = [result for unused_index, result in indexed]
        notices = []
        for unused_provider, provider_settings in missing:
            notices.append(self._credential_message(provider_settings))
        for unused_provider, provider_settings, error in failures:
            notices.append(self._error_text_for(error, provider_settings))
        callback = self._browser_results_callback
        self._browser_results_callback = None
        if callable(callback):
            try:
                callback(ordered, notices)
            except Exception:
                pass
        self._finish(generation)

    def _results_ready(self, generation, results):
        if not results:
            if not self._automatic:
                self._notify(self._provider_text(
                    N_("No matching subtitles were found on SubDL.")
                ))
            self._finish(generation)
            return
        if self._automatic:
            self._start_download(generation, results[0])
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
        settings = self._settings
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
                content = self._client(settings).download(result)
                cues = parse_subtitle(
                    content, hearing_impaired=settings.hearing_impaired
                )
                cues, fps_factor, timeline_state = retime_subtitle_cues(
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
                path = save_subtitle_file(content, metadata, result)
                self._queue(
                    generation,
                    "download",
                    (cues, path, settings, timeline_state),
                )
            except Exception as error:
                self._queue(generation, "error", error)

        thread = threading.Thread(target=worker)
        thread.daemon = True
        thread.start()

    def _download_ready(self, generation, payload):
        try:
            if len(payload) == 4:
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
        rollback = None
        if callable(self.before_activate):
            try:
                rollback = self.before_activate()
            except Exception:
                if not self._automatic:
                    self._notify(N_("Could not start the independent subtitle renderer."))
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
        self._generation += 1
        self._busy = False
        self._automatic = False
        self._finish_callback = None
        self._browser_results_callback = None
        with self._event_lock:
            self._events.clear()
        if self.poll_timer is not None:
            try:
                self.poll_timer.stop()
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

        def selected(selection):
            try:
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

    def reset_for_service_change(self):
        self._generation += 1
        self._busy = False
        self._finish_callback = None
        self._browser_results_callback = None
        self._last_download_succeeded = False
        self._automatic = False
        self._auto_started = False
        with self._event_lock:
            self._events.clear()
        self.renderer.disable()
        reset_clock = getattr(self.renderer, "reset_clock", None)
        if callable(reset_clock):
            reset_clock()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._generation += 1
        self._busy = False
        self._finish_callback = None
        self._browser_results_callback = None
        if self.poll_timer is not None:
            try:
                self.poll_timer.stop()
            except Exception:
                pass
        self.renderer.close()

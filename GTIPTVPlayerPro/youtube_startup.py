# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded YouTube player checks and GUI-owned startup verification.

Player detection runs on the resolver worker. The GUI only queries registered
services and samples its current decoder; it never opens a media URL to test it.
"""
import json
import os
import re
import shutil
import subprocess
import threading
import time

from .i18n import N_, _

ERROR_MESSAGES = {
    "youtube_resolve_failed": N_("Could not resolve the YouTube video link. Check the connection or try another video."),
    "youtube_stream_unavailable": N_("No compatible YouTube stream is available at or below the selected quality."),
    "youtube_native_player_required": N_("ServiceApp / ExtEplayer3 is unavailable or cannot run. Install it and restart Enigma2."),
    "youtube_service_unavailable": N_("The YouTube playback service is unavailable. Check the player settings."),
    "youtube_start_failed": N_("YouTube playback did not start. Check the connection and player."),
}
RETRY_MESSAGE = N_("Trying a compatible YouTube stream...")
STARTUP_SECONDS = 20.0
NATIVE_VIDEO_CONFIRM_SECONDS = 1.25
_PLAYER_CACHE = {}
_PLAYER_LOCK = threading.Lock()


class YouTubePlaybackError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def youtube_error_message(code):
    return _(ERROR_MESSAGES.get(code, "Could not start the player."))


def probe_native_player():
    """Verify ExtEplayer3 can run and inspect its separate-audio option.

The no-argument invocation is also used by ServiceApp's player detection.
Some builds print usage in place of the version record.
Do not run a subprocess on the GUI/main thread, even on a cold cache.
"""
    executable = shutil.which("exteplayer3")
    if not executable and os.access("/usr/bin/exteplayer3", os.X_OK):
        executable = "/usr/bin/exteplayer3"
    if not executable:
        return dict(available=False, version=0, reason="missing")
    try:
        details = os.stat(executable)
        key = (executable, details.st_mtime_ns, details.st_size)
    except OSError:
        return dict(available=False, version=0, reason="unreadable")
    with _PLAYER_LOCK:
        cached = _PLAYER_CACHE.get(key)
        if cached and time.monotonic() - cached[0] < 30:
            return dict(cached[1])
    if threading.current_thread() is threading.main_thread():
        return dict(available=False, version=0, reason="worker_required")
    result = dict(available=False, version=0, reason="cannot_run")
    try:
        with subprocess.Popen([executable], stdin=subprocess.DEVNULL,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT) as process:
            try:
                output, unused_error = process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
                output = b""
        text = output[:65536].decode("utf-8", "replace")
        has_usage = re.search(r"(?im)^\s*usage:\s*exteplayer3\b", text) is not None
        advertises_audio = re.search(r"(?:^|[\s\[])-x\b", text) is not None
        separate_audio = True if advertises_audio else False if has_usage else None
        decoder = json.JSONDecoder()
        for index, character in enumerate(text):
            if character != "{":
                continue
            try:
                document, unused_end = decoder.raw_decode(text[index:])
                version = int(document["EPLAYER3_EXTENDED"]["version"])
            except (ValueError, TypeError, KeyError):
                continue
            if version > 0:
                result = dict(available=True, version=version, reason="ready", separate_audio=separate_audio)
                break
        if not result["available"] and has_usage and process.returncode in (0, 1):
            result = dict(available=True, version=0, reason="usage_only", separate_audio=separate_audio)
    except (OSError, subprocess.SubprocessError):
        pass
    with _PLAYER_LOCK:
        _PLAYER_CACHE.clear()
        _PLAYER_CACHE[key] = (time.monotonic(), dict(result))
    return result


def registered_youtube_services():
    """GUI-only static service lookup; creating/playing a test stream is avoided."""
    try:
        from enigma import eServiceCenter, eServiceReference
        center = eServiceCenter.getInstance()
    except Exception:
        return ()
    registered = []
    for kind in (4097, 5001, 5002):
        try:
            if center.info(eServiceReference(kind, 0, "https://www.youtube.com/")) is not None:
                registered.append(kind)
        except Exception:
            continue
    return tuple(registered)


_VP9_FLAGS = ("HasVP9", "CanVP9", "SupportsVP9")
# OpenPLi-meta/meta-vuplus conf/machine/vuultimo4k.conf explicitly enables
# --with-vb9 in DVBMEDIASINK_CONFIG. OpenPLi 9.2 does not publish the flags
# above. Match this exact receiver, never every model containing "4k".
# Native player readiness and actual video progress are checked separately.
_VP9_MODEL_PROFILES = frozenset(("vuultimo4k", "ultimo4k"))
_MODEL_KEYS = ("model", "machine", "machinebuild")
_MODEL_PATHS = ("/proc/stb/info/vumodel", "/proc/stb/info/boxtype", "/proc/stb/info/model")


def _receiver_model(value):
    if not isinstance(value, str) or len(value) > 128:
        return ""
    return "".join(character for character in value.lower() if character.isalnum())


def _model_from_proc(path):
    try:
        with open(path, "r", encoding="ascii", errors="ignore") as handle:
            return handle.read(129).strip()
    except OSError:
        return ""


def receiver_youtube_codecs(identity_reader=None):
    """Use image capabilities or an exact, documented receiver profile.

    An explicit negative capability remains authoritative. Missing flags mean
    unknown, so consult the profile before falling back to AVC. An executable,
    a generic 4K display mode, or a codec enumeration cannot enable VP9.
    """
    getter = None
    try:
        from Components.SystemInfo import BoxInfo
        getter = BoxInfo.getItem
    except (ImportError, AttributeError):
        try:
            from Components.SystemInfo import SystemInfo
            getter = SystemInfo.get
        except (ImportError, AttributeError):
            pass

    def item(key):
        try:
            return getter(key) if getter is not None else None
        except (AttributeError, KeyError, TypeError):
            return None

    flags = tuple(item(key) for key in _VP9_FLAGS)
    if any(value is False for value in flags):
        return frozenset(("h264",))
    if any(value is True for value in flags):
        return frozenset(("h264", "vp9"))
    models = {_receiver_model(item(key)) for key in _MODEL_KEYS}
    if not models.intersection(_VP9_MODEL_PROFILES):
        reader = identity_reader or _model_from_proc
        for path in _MODEL_PATHS:
            try:
                models.add(_receiver_model(reader(path)))
            except (OSError, ValueError, TypeError):
                continue
    return (frozenset(("h264", "vp9")) if models.intersection(_VP9_MODEL_PROFILES)
            else frozenset(("h264",)))


def same_reference(left, right):
    try:
        return left is not None and right is not None and left.toString() == right.toString()
    except (AttributeError, TypeError):
        return left is not None and left is right


class YouTubeStartupMonitor:
    """Confirm the current service from advancing PTS or stable native video info.

The logical player may move from preview to fullscreen during this check.
Callbacks must return None after cancellation and must never return another
service's dimensions. Only the owned 5002 service can confirm an unclocked start.
"""
    def __init__(self, sample, timer=None, clock=None, navigation=None, reference=None,
                 allow_unclocked_video=False):
        self.sample = sample
        self.clock = clock or time.monotonic
        self.started_at = self.last_at = self.clock()
        self.active_seconds = 0.0
        self._clocks = {}
        self._allow_unclocked_video = allow_unclocked_video
        self._service_video_since = None
        self.outcome = ""
        self.confirmation = ""
        self.failure_reason = ""
        self.last_sample = {}
        self.done = threading.Event()
        self.navigation = navigation
        self.reference = reference
        self._events = None
        self._player_exited = False
        self.timer = timer
        self.connection = None
        if self.timer is None:
            from enigma import eTimer
            self.timer = eTimer()
        if hasattr(self.timer, "callback"):
            self.timer.callback.append(self.poll)
        else:
            self.connection = self.timer.timeout.connect(self.poll)
        if navigation is not None:
            try:
                from enigma import iPlayableService
                self._eof_event = iPlayableService.evEOF
                navigation.event.append(self._service_event)
                self._events = navigation.event
            except (ImportError, AttributeError, TypeError):
                pass
        self.timer.start(250, True)

    def _service_event(self, event):
        if event != self._eof_event or self.done.is_set():
            return
        try:
            current = self.navigation.getCurrentlyPlayingServiceReference()
            if not same_reference(self.reference, current):
                return
        except Exception:
            return
        # ServiceApp emits EOF when its external process stops. Defer recovery
        # until Navigation returns from the current event dispatch.
        self._player_exited = True
        self.timer.start(1, True)

    def finish(self, outcome, reason=""):
        if self.done.is_set():
            return
        self.outcome = outcome
        self.failure_reason = reason
        self.timer.stop()
        if hasattr(self.timer, "callback"):
            try:
                self.timer.callback.remove(self.poll)
            except ValueError:
                pass
        self.connection = None
        if self._events is not None:
            try:
                self._events.remove(self._service_event)
            except ValueError:
                pass
            self._events = None
        self.navigation = self.reference = None
        self.sample = None
        self.done.set()

    def close(self):
        self.finish("cancelled")

    def _clock_is_advancing(self, pts, source, now, interval):
        """Distinguish an unavailable clock from a reported clock without progress."""
        if pts is None or pts < 0 or not source:
            return None
        last_pts, last_advance, progress = self._clocks.get(source, (None, None, 0))
        if last_pts is not None:
            delta = pts - last_pts
            if source in ("dvb", "proc_video"):
                delta %= 1 << 33
            # A bookmark seek is not evidence of advancing playback.
            maximum_delta = (interval * 2 + 1) * 90000
            if 0 < delta <= maximum_delta:
                progress += delta
                last_advance = now
            elif delta < 0 or delta > maximum_delta:
                progress = 0
                last_advance = None
        self._clocks[source] = pts, last_advance, progress
        return progress >= 45000 and last_advance is not None and now - last_advance <= 1

    def poll(self):
        if self.done.is_set():
            return
        now = self.clock()
        interval = max(0, now - self.last_at)
        self.last_at = now
        try:
            state = self.sample()
        except Exception:
            state = {}
        if state is None:
            self.finish("cancelled")
            return
        self.last_sample = state
        if state.get("rejected"):
            self.finish("failed", "service_rejected")
            return
        if state.get("owned") and self._player_exited:
            self.finish("failed", "player_exited")
            return
        if not state.get("paused"):
            self.active_seconds += interval
        active = state.get("owned") and not state.get("paused")
        confirming_video_info = False
        if active:
            decoder_advancing = self._clock_is_advancing(
                state.get("video_pts"), state.get("video_pts_source"), now, interval)
            service_advancing = self._clock_is_advancing(
                state.get("pts"), state.get("pts_source", "service"), now, interval)
            # Decoder dimensions may be stale when only the service clock is
            # moving. In that case require video dimensions from the service.
            if (now - self.started_at >= 0.75 and
                    ((state.get("video") and decoder_advancing)
                     or (state.get("service_video", state.get("video")) and service_advancing))):
                self.confirmation = "decoder_pts" if state.get("video") and decoder_advancing else "service_pts"
                self.finish("playing")
                return
            confirming_video_info = (self._allow_unclocked_video and state.get("service_video")
                                     and decoder_advancing is None and service_advancing is None)
        # Some native builds report the new video's dimensions but expose no
        # position sensor. Require sustained service-owned video information.
        if confirming_video_info:
            if self._service_video_since is None:
                self._service_video_since = now
            elif now - self._service_video_since >= NATIVE_VIDEO_CONFIRM_SECONDS:
                self.confirmation = "native_video_info"
                self.finish("playing")
                return
        else:
            self._service_video_since = None
        if self.active_seconds >= STARTUP_SECONDS and not confirming_video_info:
            self.finish("failed", "startup_timeout")
            return
        self.timer.start(250, True)

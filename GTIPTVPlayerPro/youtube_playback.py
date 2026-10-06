# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Direct YouTube playback with the original video and audio URLs.

ServiceApp's ExtEplayer3 backend passes ``&suburi=`` as a separate audio
input. Other service IDs cannot be assumed to support that contract. Keep
the signed URLs byte-for-byte and never start a private FFmpeg remux.
"""

import os
import shutil
import time
from urllib.parse import urlsplit


SUBURI = "&suburi="
NATIVE_AUDIO_SERVICE_TYPE = 5002
MAX_STREAM_URL_BYTES = 16384
MAX_DURATION_SECONDS = 31 * 24 * 60 * 60


class YouTubeNativePlayerUnavailable(RuntimeError):
    """The receiver cannot play the separate audio input directly."""


def youtube_stream_parts(url):
    """Validate one or two signed sources without decoding or changing them."""
    if (not isinstance(url, str) or not url or len(url) > MAX_STREAM_URL_BYTES
            or url.count(SUBURI) > 1
            or any(char in url for char in ("\r", "\n", "\x00"))):
        raise ValueError("invalid YouTube stream")
    video, separator, audio = url.partition(SUBURI)
    for source in ((video, audio) if separator else (video,)):
        try:
            parsed = urlsplit(source)
            host = (parsed.hostname or "").lower()
            port = parsed.port
        except ValueError:
            raise ValueError("invalid YouTube stream") from None
        if (parsed.scheme != "https" or parsed.username or parsed.password
                or port not in (None, 443)
                or not any(host == domain or host.endswith("." + domain)
                           for domain in ("googlevideo.com", "youtube.com"))):
            raise ValueError("invalid YouTube stream")
    return video, audio if separator else ""


def native_audio_player_available():
    """Check the executable on each play, including nonstandard PATH entries."""
    return bool(shutil.which("exteplayer3")) or (
        os.path.isfile("/usr/bin/exteplayer3")
        and os.access("/usr/bin/exteplayer3", os.X_OK)
    )


def youtube_service_type(url, preferred_type=4097, video_codec=""):
    """Select native separate audio/VP9 without rewriting saved preferences."""
    unused_video, audio = youtube_stream_parts(url)
    if audio or video_codec == "vp9":
        if not native_audio_player_available():
            raise YouTubeNativePlayerUnavailable("ServiceApp / ExtEplayer3 (5002)")
        return NATIVE_AUDIO_SERVICE_TYPE
    try:
        preferred_type = int(preferred_type)
    except (TypeError, ValueError, OverflowError):
        preferred_type = 4097
    return preferred_type if preferred_type in (4097, 5001, 5002) else 4097


def youtube_duration_seconds(item):
    """Return bounded metadata duration for the native player's time display."""
    try:
        duration = int(getattr(item, "youtube_duration_seconds", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return duration if 0 < duration <= MAX_DURATION_SECONDS else 0


class YouTubeEndWatcher(object):
    """Dispatch the current video's EOF once, outside Navigation's event loop."""

    def __init__(self, dialog, callback, timer=None, clock=None):
        from enigma import eTimer, iPlayableService
        self.dialog = dialog
        self.callback = callback
        self.navigation = dialog.session.nav
        self.clock = clock or time.monotonic
        self.eof_event = iPlayableService.evEOF
        self.timer = timer if timer is not None else eTimer()
        self.connection = None
        if hasattr(self.timer, "callback"):
            self.timer.callback.append(self._dispatch)
        else:
            self.connection = self.timer.timeout.connect(self._dispatch)
        self.closed = False
        self.navigation.event.append(self._event)
        self.arm()

    def arm(self):
        self.timer.stop()
        self.reference = self.dialog.reference
        self.started = self.clock()
        self.position = -1
        self.length = 0
        self.pending = self.ended = False

    def sample(self, position, length):
        if position >= 0:
            self.position = position
        if length > 0:
            self.length = length

    def _owns_service(self):
        if (self.closed or getattr(self.dialog, "_closed", False)
                or self.dialog.session.current_dialog is not self.dialog):
            return False
        try:
            current = self.navigation.getCurrentlyPlayingServiceReference()
            if hasattr(current, "toString") and hasattr(self.reference, "toString"):
                return current.toString() == self.reference.toString()
            return current is self.reference
        except Exception:
            return False

    def _event(self, event):
        if (event != self.eof_event or self.pending or self.ended
                or not self._owns_service() or self.clock() - self.started < 0.75):
            return
        try:
            position, length = self.dialog._seek_position()
            self.sample(position, length)
        except Exception:
            pass
        # Ignore a premature EOF from a broken input when we have observed
        # its clock. A native EOF without a usable clock remains supported.
        if self.position > 0 and self.length > 0 and self.position < self.length - 1:
            return
        self.pending = True
        self.timer.start(120, True)

    def _dispatch(self):
        if not self.pending or not self._owns_service():
            return
        self.pending = False
        self.ended = True
        self.callback(self.dialog)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.pending = False
        self.timer.stop()
        try:
            self.navigation.event.remove(self._event)
        except (ValueError, AttributeError):
            pass
        if hasattr(self.timer, "callback"):
            try:
                self.timer.callback.remove(self._dispatch)
            except ValueError:
                pass
        self.connection = None
        self.callback = None
        self.dialog = None

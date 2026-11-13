# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Direct YouTube playback with the original video and audio URLs.

ServiceApp's ExtEplayer3 backend passes ``&suburi=`` as a separate audio
input. Other service IDs cannot be assumed to support that contract. Keep
the signed URLs byte-for-byte and never start a private FFmpeg remux.
"""

from contextlib import contextmanager
import os
from pathlib import Path
import re
import secrets
import socket
import stat
import threading
import time
from urllib.parse import urlsplit

from .diagnostics import log_event


SUBURI = "&suburi="
NATIVE_AUDIO_SERVICE_TYPE = 5002
MAX_STREAM_URL_BYTES = 16384
MAX_DURATION_SECONDS = 31 * 24 * 60 * 60
NATIVE_URL_BUFFER_BYTES = 1024
_REDIRECT_LIFETIME = 6 * 60 * 60
_MAX_REDIRECTS = 32
_EXTEPLAYER_STOP_SOCKET = "/tmp/.exteplayerterm.socket"
_NATIVE_SYNC_PATH = Path("/proc/msp/sync00")
_SYNC_FIELDS = re.compile(r"\b(PreSyncStartSysTime|CrtStatus|VidLocalTime|VidLastPts)\s*:\s*(-?\d+|[A-Z]+)")


def reserve_native_standard_fds():
    """Keep legacy ServiceApp's child pipes away from descriptors 0, 1 and 2."""
    if os.name != "posix":
        return
    restored = []
    try:
        for _ in range(3):
            # open() occupies only a free descriptor and leaves existing streams intact.
            descriptor = os.open(os.devnull, os.O_RDWR)
            if descriptor > 2:
                os.close(descriptor)
                break
            os.set_inheritable(descriptor, True)
            restored.append(str(descriptor))
    except OSError as error:
        log_event("youtube", "native-stdio-setup-failed", error)
    if restored:
        log_event("youtube", "native-stdio-restored fd={}".format(",".join(restored)))


@contextmanager
def youtube_native_control(navigation, reference, item):
    """Reserve standard descriptors after Navigation releases the old service."""
    if not getattr(item, "youtube_native", False) or getattr(reference, "type", None) != NATIVE_AUDIO_SERVICE_TYPE:
        yield
        return
    reserve_native_standard_fds()
    started = False

    def on_event(event):
        nonlocal started
        if event == start_event:
            # ServiceApp sends evStart before it launches ExtEplayer3.
            started = True
            reserve_native_standard_fds()

    events = None
    try:
        from enigma import iPlayableService
        start_event = iPlayableService.evStart
        events = navigation.event
        events.append(on_event)
    except (ImportError, AttributeError, TypeError):
        events = None
    try:
        yield
    finally:
        if events is not None:
            try:
                events.remove(on_event)
            except (AttributeError, TypeError, ValueError):
                pass
        if not started:
            # Some images defer startup until after releasing a relayed service.
            reserve_native_standard_fds()


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
    """A runnable native player, rather than just an executable filename."""
    from .youtube_startup import probe_native_player
    return probe_native_player()["available"]


class _NativeStreamRedirects:
    """Give the player short input addresses while preserving signed URLs."""

    def __init__(self):
        self.lock = threading.Lock()
        self.server = None
        self.sources = {}

    def _lookup(self, path):
        with self.lock:
            entry = self.sources.get(path)
            return entry[0] if entry and entry[1] > time.monotonic() else None

    def owns_input(self, url):
        server = self.server
        if server is None:
            return False
        origin = "http://127.0.0.1:{}".format(server.server_port)
        return url.startswith(origin + "/youtube/") and self._lookup(url[len(origin):]) is not None

    def _start_server(self):
        from http.server import BaseHTTPRequestHandler, HTTPServer
        redirects = self

        class Handler(BaseHTTPRequestHandler):
            def setup(self):
                super().setup()
                self.connection.settimeout(3)

            def do_GET(self):
                url = redirects._lookup(self.path)
                self.send_response_only(307 if url else 404)
                if url:
                    self.send_header("Location", url)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", "0")
                self.end_headers()

            do_HEAD = do_GET

            def log_message(self, *_args):
                pass

        server = HTTPServer(("127.0.0.1", 0), Handler)
        try:
            threading.Thread(target=server.serve_forever, name="GT-YouTube-Redirect", daemon=True).start()
        except Exception:
            server.server_close()
            raise
        return server

    def adapt(self, parts):
        expires = time.monotonic() + _REDIRECT_LIFETIME
        with self.lock:
            if self.server is None:
                self.server = self._start_server()
            inputs = []
            for part in parts:
                if len(part.encode("utf-8")) < NATIVE_URL_BUFFER_BYTES:
                    inputs.append(part)
                    continue
                if not part.isascii() or any(not 33 <= ord(char) <= 126 for char in part):
                    raise ValueError("invalid YouTube redirect target")
                path = "/youtube/" + secrets.token_urlsafe(18)
                self.sources[path] = part, expires
                inputs.append("http://127.0.0.1:{}{}".format(self.server.server_port, path))
            while len(self.sources) > _MAX_REDIRECTS:
                self.sources.pop(next(iter(self.sources)))
            return SUBURI.join(inputs)


_NATIVE_STREAM_REDIRECTS = _NativeStreamRedirects()


def native_youtube_service_url(url):
    """Fit legacy ExtEplayer3's per-input buffer without rewriting signatures."""
    video, audio = youtube_stream_parts(url)
    parts = (video, audio) if audio else (video,)
    if all(len(part.encode("utf-8")) < NATIVE_URL_BUFFER_BYTES for part in parts):
        return url
    return _NATIVE_STREAM_REDIRECTS.adapt(parts)


def _stop_listener_inode():
    with open("/proc/net/unix", encoding="ascii", errors="replace") as entries:
        listeners = []
        for line in entries:
            fields = line.split()
            if (len(fields) == 8 and fields[7] == _EXTEPLAYER_STOP_SOCKET
                    and fields[3] == "00010000" and fields[4] == "0001"):
                listeners.append(fields[6])
    return listeners[0] if len(listeners) == 1 else None


def _owns_stop_listener(url, inode):
    """Match the stop listener to a child running this exact YouTube input."""
    marker = url.encode("ascii")
    fd_target = "socket:[{}]".format(inode)
    parent_pid = str(os.getpid())
    for process in Path("/proc").iterdir():
        if not process.name.isdigit():
            continue
        try:
            if (process / "comm").read_text().strip() != "exteplayer3":
                continue
            with (process / "cmdline").open("rb") as command:
                if marker not in command.read(32768).split(b"\0"):
                    continue
            with (process / "status").open() as status:
                parent = next((line.split()[-1] for line in status if line.startswith("PPid:")), "")
            if parent != parent_pid or Path(os.readlink(process / "exe")).name != "exteplayer3":
                continue
            for descriptor in (process / "fd").iterdir():
                try:
                    if os.readlink(descriptor) == fd_target:
                        return True
                except OSError:
                    continue
        except (OSError, ValueError):
            continue
    return False


def request_youtube_native_stop(navigation, reference, item):
    """Ask the old 5002 process to abort its input before Navigation replaces it."""
    from .youtube_startup import same_reference
    if not getattr(item, "youtube_native", False):
        return False
    try:
        if (int(reference.type) != NATIVE_AUDIO_SERVICE_TYPE
                or not same_reference(reference, navigation.getCurrentlyPlayingServiceReference())):
            return False
        address = next((part for part in reference.getPath().split(SUBURI, 1)
                        if _NATIVE_STREAM_REDIRECTS.owns_input(part)), None)
        if not address:
            return False
        before = os.lstat(_EXTEPLAYER_STOP_SOCKET)
        if not stat.S_ISSOCK(before.st_mode):
            return False
        inode = _stop_listener_inode()
        if (not inode or not _owns_stop_listener(address, inode)
                or not os.path.samestat(before, os.lstat(_EXTEPLAYER_STOP_SOCKET))):
            return False
        # ExtEplayer3 watches this socket to interrupt its network reads.
        # A connection requests shutdown, so only connect to the verified owner.
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as control:
            control.settimeout(0.1)
            control.connect(_EXTEPLAYER_STOP_SOCKET)
    except (AttributeError, OSError, OverflowError, RuntimeError, TypeError, ValueError):
        return False
    log_event("youtube", "native-stop-requested engine=5002")
    return True


def youtube_service_type(url, preferred_type=4097, video_codec="", available_types=None,
                         native_available=None):
    """Select native separate audio/VP9 without rewriting saved preferences."""
    unused_video, audio = youtube_stream_parts(url)
    if audio or video_codec == "vp9":
        if ((not native_audio_player_available() if native_available is None else not native_available)
                or available_types is not None and NATIVE_AUDIO_SERVICE_TYPE not in available_types):
            raise YouTubeNativePlayerUnavailable("ServiceApp / ExtEplayer3 (5002)")
        return NATIVE_AUDIO_SERVICE_TYPE
    try:
        preferred_type = int(preferred_type)
    except (TypeError, ValueError, OverflowError):
        preferred_type = 4097
    preferred_type = preferred_type if preferred_type in (4097, 5001, 5002) else 4097
    if available_types is not None and preferred_type not in available_types:
        if 4097 not in available_types:
            from .youtube_startup import YouTubePlaybackError
            raise YouTubePlaybackError("youtube_service_unavailable")
        return 4097
    if preferred_type == 5002 and native_available is False:
        if available_types is None or 4097 in available_types:
            return 4097
        raise YouTubeNativePlayerUnavailable("ServiceApp / ExtEplayer3 (5002)")
    return preferred_type


def youtube_duration_seconds(item):
    """Return bounded metadata duration for the native player's time display."""
    try:
        duration = int(getattr(item, "youtube_duration_seconds", 0) or 0)
    except (TypeError, ValueError, OverflowError):
        return 0
    return duration if 0 < duration <= MAX_DURATION_SECONDS else 0


def _read_native_sync():
    """Read the primary decoder's media clock and last frame PTS in ms."""
    try:
        with _NATIVE_SYNC_PATH.open(encoding="ascii", errors="replace") as handle:
            fields = dict(_SYNC_FIELDS.findall(handle.read(8192)))
        return (int(fields["PreSyncStartSysTime"]), fields.get("CrtStatus"),
                int(fields.get("VidLocalTime", -1)), int(fields.get("VidLastPts", -1)))
    except (OSError, KeyError, ValueError):
        return None


class YouTubeDecoderPosition:
    """Use a fresh, advancing driver clock when the 5002 service omits PTS."""

    def __init__(self, reference, reader=None, clock=None):
        self.reference = reference
        self._read = reader or _read_native_sync
        self._clock = clock or time.monotonic
        previous = self._read()
        self._initial_epoch = previous[0] if previous else None
        self._last_frame = None
        self._position = -1
        self._next_read = 0

    def position(self, navigation, length):
        from .youtube_startup import same_reference
        try:
            if length <= 0 or not same_reference(self.reference, navigation.getCurrentlyPlayingServiceReference()):
                return -1
        except (AttributeError, RuntimeError, TypeError):
            return -1
        now = self._clock()
        if now < self._next_read:
            return min(self._position, length)
        self._next_read = now + 0.25
        sample = self._read()
        last = self._last_frame
        if sample:
            epoch, status, position_ms, frame_ms = sample
            if epoch >= 0 and epoch != self._initial_epoch:
                if status == "PAUSE" and last is not None and last[0] == epoch:
                    return min(self._position, length)
                if (status in ("PLAY", "TPLAY") and 0 <= position_ms <= (length + 1) * 1000
                        and frame_ms >= 0 and abs(frame_ms - position_ms) <= 3000):
                    self._last_frame = (epoch, frame_ms, now)
                    if last is None or last[0] != epoch:
                        self._position = -1
                    else:
                        advance = frame_ms - last[1]
                        if 0 < advance <= ((now - last[2]) * 2 + 1) * 1000:
                            self._position = min(length, position_ms // 1000)
                        elif advance != 0:
                            self._position = -1
                    return self._position
        self._last_frame = None
        self._position = -1
        return -1


def prepare_youtube_decoder_position(reference):
    # Read the previous sync generation before Navigation starts the new video.
    try:
        if _NATIVE_SYNC_PATH.parent.is_dir():
            return YouTubeDecoderPosition(reference)
    except OSError:
        pass
    return None


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
        self.last_positive_position = -1
        self.length = 0
        self.pending = self.ended = False

    def sample(self, position, length):
        if position >= 0:
            self.position = position
            if position > 0:
                self.last_positive_position = position
        if length > 0:
            self.length = length

    def reset_position(self):
        """Discard pre-seek EOF evidence without inventing a landing sample."""
        self.position = self.last_positive_position = -1

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
            # Some decoders reset PTS before delivering EOF. Keep the most
            # recent credible sample, including one captured by the poll
            # before this event, instead of interpreting zero as completion.
            if position <= 1 < self.last_positive_position:
                position = self.last_positive_position
            self.sample(position, length)
        except Exception:
            pass
        # A known duration needs credible near-end evidence. Preserve the
        # existing native EOF policy when no duration is available.
        if self.length > 0 and self.position < self.length - 1:
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

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import datetime
import os
import re
import stat
import threading
from urllib.parse import urlsplit


DEFAULT_LOG_PATH = "/tmp/gtiptvplayerpro.log"
MAX_LOG_BYTES = 256 * 1024

_LOG_LOCK = threading.Lock()
_QUERY_SECRET = re.compile(
    r"(?i)(username|user|password|passwd|pass|api_key|apikey|token|"
    r"access_token|authorization|auth|key|signature|sig)=([^&\s]+)"
)
_STREAM_SECRET = re.compile(
    r"(?i)/(live|movie|series)/([^/\s]+)/([^/\s]+)/"
)
_BEARER_SECRET = re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+")
_MAC_SECRET = re.compile(r"(?i)\b(?:[0-9a-f]{2}:){5}[0-9a-f]{2}\b")
_URL_VALUE = re.compile(
    r"(?i)\b(?:https?|rtsp|rtmp|rtp|udp)://[^\s<>\"']+"
)
_ENCODED_URL_VALUE = re.compile(
    r"(?i)\b(?:https?|rtsp|rtmp|rtp|udp)%3a//[^\s<>\"']+"
)


def _redacted_url(match):
    """Keep only a URL endpoint; playlist paths and queries may be secrets."""
    value = match.group(0)
    try:
        parsed = urlsplit(value)
        hostname = parsed.hostname or ""
        if not hostname:
            return "<redacted-url>"
        if ":" in hostname and not hostname.startswith("["):
            hostname = "[{}]".format(hostname)
        port = parsed.port
        endpoint = "{}://{}".format(parsed.scheme.lower(), hostname)
        if port is not None:
            endpoint += ":{}".format(port)
        if parsed.path and parsed.path != "/":
            endpoint += "/<redacted>"
        elif parsed.path == "/":
            endpoint += "/"
        if parsed.query:
            endpoint += "?<redacted>"
        return endpoint
    except (TypeError, ValueError):
        return "<redacted-url>"


def redact(value):
    """Remove account credentials and API tokens from diagnostic text."""
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = _QUERY_SECRET.sub(lambda match: match.group(1) + "=<redacted>", text)
    text = _STREAM_SECRET.sub(
        lambda match: "/{}/<redacted>/<redacted>/".format(match.group(1)),
        text,
    )
    text = _BEARER_SECRET.sub(lambda match: match.group(1) + "<redacted>", text)
    text = _MAC_SECRET.sub("<redacted-mac>", text)
    text = _ENCODED_URL_VALUE.sub("<redacted-encoded-url>", text)
    text = _URL_VALUE.sub(_redacted_url, text)
    return text[:1000]


def log_event(area, message, error=None, path=DEFAULT_LOG_PATH):
    """Append one private, bounded and credential-redacted diagnostic line."""
    area = redact(area or "core")[:32]
    detail = redact(message)
    if error is not None:
        detail = "{} | {}".format(detail, error.__class__.__name__)
    line = "{} [{}] {}\n".format(
        datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        area,
        detail,
    )
    descriptor = None
    try:
        with _LOG_LOCK:
            # Windows has no O_NOFOLLOW. Reject a visible link before opening;
            # Enigma2/Linux additionally enforces the race-safe O_NOFOLLOW flag.
            if os.path.islink(path):
                raise OSError("unsafe diagnostic log target")
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags, 0o600)
            status = os.fstat(descriptor)
            current_uid = getattr(os, "geteuid", lambda: status.st_uid)()
            if (
                not stat.S_ISREG(status.st_mode)
                or status.st_uid != current_uid
                or status.st_nlink != 1
            ):
                raise OSError("unsafe diagnostic log target")
            fchmod = getattr(os, "fchmod", None)
            if fchmod is not None:
                fchmod(descriptor, 0o600)
            if status.st_size >= MAX_LOG_BYTES:
                os.ftruncate(descriptor, 0)
            payload = line.encode("utf-8", errors="replace")
            while payload:
                written = os.write(descriptor, payload)
                if written <= 0:
                    raise OSError("diagnostic log write failed")
                payload = payload[written:]
            os.close(descriptor)
            descriptor = None
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
        return True
    except (IOError, OSError):
        return False
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass

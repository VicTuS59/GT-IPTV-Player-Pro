# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-independent artwork formats and bounded retry policy."""

import errno
import http.client
import io
import socket
import ssl
import struct
import threading
from urllib.parse import urlsplit, urlunsplit


BROWSER_USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
RETRY_DELAY_SECONDS = 0.25
TRANSIENT_FAILURE_SECONDS = 20
PERMANENT_FAILURE_SECONDS = 300
RATE_LIMIT_SECONDS = 60
_WEBP_CONVERSION_LOCK = threading.Lock()


def is_tmdb_artwork_url(url):
    try:
        return str(urlsplit(str(url)).hostname or "").lower() == "image.tmdb.org"
    except (TypeError, ValueError):
        return False


def tmdb_poster_url(url, width=342):
    """Size only real TMDb posters; leave signed provider URLs untouched."""
    value = str(url or "").strip()
    if not is_tmdb_artwork_url(value):
        return value
    parsed = urlsplit(value)
    parts = parsed.path.split("/")
    if len(parts) != 5 or parts[1:3] != ["t", "p"] or not parts[4]:
        return value
    try:
        target = max(1, int(width))
    except (TypeError, ValueError, OverflowError):
        target = 342
    size = next((size for size in (185, 342, 500, 780) if size >= target), 780)
    parts[3] = "w{}".format(size)
    return urlunsplit((parsed.scheme, parsed.netloc, "/".join(parts), parsed.query, parsed.fragment))


def artwork_request_profile(url, version, user_agent=None):
    """Keep TMDb on receiver-native JPEG/PNG; providers may offer WebP.

    TMDb negotiates WebP even for a .jpg URL when it is advertised. A desktop
    user agent plus image/webp changed the actual wire format in r23.
    """
    if is_tmdb_artwork_url(url):
        return ("image/jpeg,image/png;q=0.9",
                "Mozilla/5.0 GT-IPTV-Player-Pro/{}".format(version))
    return ("image/jpeg,image/png,image/webp;q=0.9,image/*;q=0.8,*/*;q=0.5",
            str(user_agent or BROWSER_USER_AGENT))


class ArtworkError(ValueError):
    """Unusable image data, rather than a temporary connection failure."""


def webp_dimensions(body):
    """Read VP8, VP8L and VP8X canvas sizes without decoding pixels."""
    if len(body) < 20 or body[:4] != b"RIFF" or body[8:12] != b"WEBP":
        return 0, 0
    try:
        end = struct.unpack_from("<I", body, 4)[0] + 8
        # Header-only validation also serves the bounded cache validators.
        if end < 20:
            return 0, 0
        offset = 12
        while offset + 8 <= min(end, len(body)):
            tag = body[offset:offset + 4]
            length = struct.unpack_from("<I", body, offset + 4)[0]
            start = offset + 8
            if start + length > end:
                return 0, 0
            if tag == b"VP8X" and length == 10 and start + 10 <= len(body):
                width = int.from_bytes(body[start + 4:start + 7], "little") + 1
                height = int.from_bytes(body[start + 7:start + 10], "little") + 1
                return width, height
            if tag == b"VP8L" and length >= 5 and start + 5 <= len(body):
                if body[start] != 0x2F:
                    return 0, 0
                bits = struct.unpack_from("<I", body, start + 1)[0]
                if bits >> 29:
                    return 0, 0
                return (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if tag == b"VP8 " and length >= 10 and start + 10 <= len(body):
                if body[start] & 1 or body[start + 3:start + 6] != b"\x9d\x01\x2a":
                    return 0, 0
                width, height = struct.unpack_from("<HH", body, start + 6)
                return width & 0x3FFF, height & 0x3FFF
            offset = start + length + (length & 1)
    except (IndexError, struct.error, TypeError, ValueError):
        pass
    return 0, 0


def prepare_webp(body, maximum_bytes=4 * 1024 * 1024):
    """Convert when Pillow/libwebp is installed; otherwise try native WebP.

    Pillow stays optional on small receiver images. Both the decoded canvas
    and the temporary output are bounded before reaching the GUI decoder.
    """
    width, height = webp_dimensions(body)
    if (not width or not height or max(width, height) > 4096
            or width * height > 8_000_000 or len(body) > maximum_bytes
            or len(body) < 20
            or struct.unpack_from("<I", body, 4)[0] + 8 != len(body)):
        raise ArtworkError("invalid WebP dimensions or container")
    try:
        from PIL import Image, features
        if not features.check("webp"):
            return body, ".webp"
    except (ImportError, AttributeError):
        return body, ".webp"
    try:
        with _WEBP_CONVERSION_LOCK, Image.open(io.BytesIO(body)) as image:
            if image.format != "WEBP" or image.size != (width, height):
                raise ArtworkError("invalid WebP canvas")
            image.seek(0)
            image.load()
            # Old receiver Pillow versions expose LANCZOS directly.
            resampling = getattr(Image, "Resampling", Image)
            image.thumbnail((1024, 1024), resampling.LANCZOS)
            alpha = "A" in image.getbands() or "transparency" in image.info
            with image.convert("RGBA" if alpha else "RGB") as converted:
                output = io.BytesIO()
                converted.save(output, "PNG" if alpha else "JPEG", **(
                    {} if alpha else {"quality": 88}
                ))
                payload = output.getvalue()
        if not payload or len(payload) > maximum_bytes:
            raise ArtworkError("converted artwork is too large")
        return payload, ".png" if alpha else ".jpg"
    except ArtworkError:
        raise
    except Exception as error:
        raise ArtworkError("WebP decode failed") from error


def retryable_artwork_error(error):
    """Do not retry HTTP absence, TLS validation or URL safety failures."""
    status = getattr(error, "status", None)
    if status is not None:
        return status in (408, 425, 500, 502, 503, 504)
    if isinstance(error, (ssl.SSLError, ArtworkError)):
        return False
    if getattr(error, "retryable", False):
        return True
    if isinstance(error, (TimeoutError, ConnectionError,
                          http.client.IncompleteRead, http.client.RemoteDisconnected)):
        return True
    if isinstance(error, socket.gaierror):
        return error.errno == socket.EAI_AGAIN
    if isinstance(error, OSError) and error.errno in (
        errno.ETIMEDOUT, errno.ECONNRESET, errno.ECONNREFUSED,
        errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EPIPE,
    ):
        return True
    cause = getattr(error, "__cause__", None)
    return cause is not None and cause is not error and retryable_artwork_error(cause)


def artwork_failure_ttl(error=None):
    if getattr(error, "status", None) == 429:
        return RATE_LIMIT_SECONDS
    if error is not None and retryable_artwork_error(error):
        return TRANSIENT_FAILURE_SECONDS
    return PERMANENT_FAILURE_SECONDS


def poster_batch_timeout(job_count, workers, image_timeout, metadata_timeout):
    """Budget every visible card, both image attempts and its TMDb fallback."""
    shards = (max(1, int(job_count)) + max(1, int(workers)) - 1) // max(1, int(workers))
    # Initial image, two provider alternatives and one TMDb image, with a
    # bounded provider summary and a separate lightweight TMDb lookup.
    per_card = 8 * float(image_timeout) + 4 * RETRY_DELAY_SECONDS + 2 * float(metadata_timeout)
    return shards * per_card + 2.0

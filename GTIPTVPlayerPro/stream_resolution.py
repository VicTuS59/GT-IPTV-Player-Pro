# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read the current video's dimensions without opening a stream or a URL.

The active service API is authoritative. Decoder-0 input geometry is the local
fallback; framebuffer size and HDMI output mode are deliberately never read.
Callers must verify service ownership before supplying a service here.
"""
import time


def _number(value):
    if isinstance(value, bool):
        return -1
    try:
        return int(value)
    except (ValueError, TypeError, OverflowError):
        return -1


def _info_number(info, information, name):
    key = getattr(information, name, None)
    if info is None or key is None:
        return -1
    try:
        value = info.getInfo(key)
    except Exception:
        return -1
    number = _number(value)
    if number >= 0:
        return number
    # iServiceInformation's sentinels select a string/object representation.
    # Do not stringify arbitrary SWIG objects or scrape human-readable titles.
    method = "getInfoString" if number == -2 else "getInfoObject" if number == -3 else ""
    getter = getattr(info, method, None) if method else None
    if callable(getter):
        try:
            return _number(getter(key))
        except Exception:
            pass
    return -1


def _proc_number(path, base):
    try:
        with open(path, "r") as handle:
            return int(handle.read(32).strip(), base)
    except (OSError, ValueError, TypeError, OverflowError):
        return -1


def _pair(width, height):
    return 16 <= width <= 16384 and 16 <= height <= 16384


def read_resolution(service, information, decoder_reader=None):
    """Return a validated pair from one source, or None while unavailable."""
    if service is None:
        return None
    try:
        info = service.info()
    except Exception:
        info = None
    width = _info_number(info, information, "sVideoWidth")
    height = _info_number(info, information, "sVideoHeight")
    progressive = _info_number(info, information, "sProgressive")
    source = "service"
    if not _pair(width, height):
        reader = decoder_reader or _proc_number
        try:
            # These are decoder input values, represented in hexadecimal on
            # Enigma2's vmpeg interface. Always take the pair together.
            width = _number(reader("/proc/stb/vmpeg/0/xres", 16))
            height = _number(reader("/proc/stb/vmpeg/0/yres", 16))
            progressive = _number(reader("/proc/stb/vmpeg/0/progressive", 10))
        except Exception:
            return None
        source = "decoder"
    if not _pair(width, height):
        return None
    return dict(width=width, height=height,
                progressive=progressive if progressive in (0, 1) else None,
                source=source)


def youtube_resolution_hint(item):
    """Only a resolver-selected rendition may stand in for missing metadata."""
    height = _number(getattr(item, "youtube_selected_quality", 0))
    if 16 <= height <= 4320:
        # The resolver knows the selected height, not necessarily the width.
        return dict(width=0, height=height, progressive=1, source="youtube")
    return None


def resolution_text(snapshot, compact=False):
    if not snapshot:
        return "--"
    width, height = snapshot.get("width", 0), snapshot.get("height", 0)
    suffix = {0: "i", 1: "p"}.get(snapshot.get("progressive"), "")
    if _pair(width, height):
        return ("{}{}".format(height, suffix) if compact
                else "{}×{}{}".format(width, height, suffix))
    if snapshot.get("source") == "youtube" and 16 <= height <= 4320:
        return "{}p".format(height)
    return "--"


class ResolutionTracker(object):
    """Retain a good local sample only for the same playback generation."""
    def __init__(self, clock=None, decoder_reader=None, settle_seconds=0.5):
        self.clock = clock or time.monotonic
        self.decoder_reader = decoder_reader
        self.settle_seconds = max(0.0, float(settle_seconds))
        self.reset()

    def reset(self):
        self.key = None
        self.started_at = self.clock()
        self.last = None

    def sample(self, service, information, key, item=None):
        if key != self.key:
            self.reset()
            self.key = key
        if service is None:
            self.last = None
            return youtube_resolution_hint(item)
        if self.clock() - self.started_at >= self.settle_seconds:
            candidate = read_resolution(service, information, self.decoder_reader)
            if candidate is not None:
                self.last = candidate
        return self.last or youtube_resolution_hint(item)

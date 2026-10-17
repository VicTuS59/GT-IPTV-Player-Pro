# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rank native YouTube audio without converting or rewriting its source."""


AAC_BITRATES = {"139": 48000, "140": 128000, "141": 256000}
ORIGINAL_NAMES = ("original", "orijinal", "originale", "originalton", "الأصل")


def _bounded_integer(value, maximum):
    if isinstance(value, bool):
        return 0
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if 0 < number <= maximum else 0


def audio_track_rank(fmt, preference="original"):
    """Prefer original language, then the default; descriptions come last."""
    track = fmt.get("audioTrack") or {}
    if not isinstance(track, dict):
        track = {}
    name = str(track.get("displayName") or "").lower()
    characteristics = str(track.get("characteristics") or "").lower()
    if "descriptive" in name or "public.accessibility.describes-video" in characteristics:
        return 3
    original = track.get("isOriginal") is True or any(word in name for word in ORIGINAL_NAMES)
    if preference == "original" and original:
        return 0
    return 1 if track.get("audioIsDefault") is True else 2


def audio_bitrate(fmt):
    """Use reported bits per second, with known AAC itags as a fallback."""
    for field in ("averageBitrate", "bitrate"):
        bitrate = _bounded_integer(fmt.get(field), 1000000)
        if bitrate:
            return bitrate
    return AAC_BITRATES.get(str(fmt.get("itag", "")), 0)


def audio_sort_key(fmt, preference="original"):
    """Rank language first, stereo compatibility, then real audio quality.

    Higher bitrate wins within the chosen language. At equal bitrate, keep
    the normal dynamic range before a YouTube DRC representation. Multi-
    channel inputs remain a fallback to avoid forcing surround downmixing.
    """
    channels = _bounded_integer(fmt.get("audioChannels"), 32)
    quality = {"AUDIO_QUALITY_HIGH": 3, "AUDIO_QUALITY_MEDIUM": 2,
               "AUDIO_QUALITY_LOW": 1}.get(fmt.get("audioQuality"), 0)
    return (audio_track_rank(fmt, preference), channels > 2,
            -audio_bitrate(fmt), fmt.get("isDrc") is True,
            -quality, -_bounded_integer(fmt.get("audioSampleRate"), 192000))


def hls_audio_format(info):
    """Map HLS language/default markers to the same audio selection rules."""
    return {"audioTrack": {"displayName": info.get("NAME", ""),
                           "audioIsDefault": info.get("DEFAULT") == "YES",
                           "characteristics": info.get("CHARACTERISTICS", "")},
            "audioChannels": str(info.get("CHANNELS", "")).split("/", 1)[0]}

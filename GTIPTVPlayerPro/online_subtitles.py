# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Online-provider search, safe downloads and native subtitle parsing."""

from bisect import bisect_right
from hashlib import sha256
from html import unescape
import json
import os
import re
import tempfile
from urllib.parse import quote, urlencode, urlsplit

from .diagnostics import log_event
from .subtitle_settings import MAX_OFFSET_MS
from .web_subtitles import SubtitleError, _download, _request


MAX_CUES = 20000
MAX_TEXT_LENGTH = 500
MAX_CACHE_FILES = 16
MAX_CACHE_BYTES = 16 * 1024 * 1024
PROVIDER_NAMES = {
    "subdl": "SubDL",
    "opensubtitles": "OpenSubtitles.com",
    "subsource": "SubSource",
}
_LANGUAGE_ALIASES = {
    "albanian": "sq", "arabic": "ar", "bulgarian": "bg",
    "catalan": "ca", "chinese": "zh", "croatian": "hr",
    "czech": "cs", "danish": "da", "dutch": "nl", "english": "en",
    "estonian": "et", "farsi": "fa", "persian": "fa", "finnish": "fi",
    "french": "fr", "galician": "gl", "german": "de", "greek": "el",
    "hebrew": "he", "hungarian": "hu", "icelandic": "is",
    "indonesian": "id", "italian": "it", "kurdish": "ku",
    "latvian": "lv", "lithuanian": "lt", "macedonian": "mk",
    "norwegian": "nb", "polish": "pl", "portuguese": "pt",
    "romanian": "ro", "russian": "ru", "serbian": "sr",
    "slovak": "sk", "slovenian": "sl", "spanish": "es",
    "swedish": "sv", "tamil": "ta", "thai": "th", "turkish": "tr",
    "ukrainian": "uk", "vietnamese": "vi",
    "alb": "sq", "sqi": "sq", "ara": "ar", "bul": "bg",
    "cat": "ca", "chi": "zh", "zho": "zh", "hrv": "hr",
    "cze": "cs", "ces": "cs", "dan": "da", "dut": "nl",
    "nld": "nl", "eng": "en", "est": "et", "per": "fa",
    "fas": "fa", "fin": "fi", "fre": "fr", "fra": "fr",
    "glg": "gl", "ger": "de", "deu": "de", "gre": "el",
    "ell": "el", "heb": "he", "hun": "hu", "ice": "is",
    "isl": "is", "ind": "id", "ita": "it", "kur": "ku",
    "lav": "lv", "lit": "lt", "mac": "mk", "mkd": "mk",
    "nor": "nb", "pol": "pl", "por": "pt", "rum": "ro",
    "ron": "ro", "rus": "ru", "srp": "sr", "slo": "sk",
    "slk": "sk", "slv": "sl", "spa": "es", "swe": "sv",
    "tam": "ta", "tha": "th", "tur": "tr", "ukr": "uk",
    "vie": "vi",
}
_TIME = re.compile(
    r"(?:(\d{1,3}):)?(\d{1,2}):(\d{2})[,.](\d{3})"
)
_TIMING_LINE = re.compile(
    r"^\s*((?:\d{1,3}:)?\d{1,2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"((?:\d{1,3}:)?\d{1,2}:\d{2}[,.]\d{3})(?:\s+.*)?$"
)
_TAG = re.compile(r"<[^>]{0,200}>")
_STYLE = re.compile(r"\{\\[^}]{0,200}\}")
_SOUND_LINE = re.compile(
    r"^\s*(?:\[[^\]]{1,120}\]|\([^)]{1,120}\)|[♪♫]+[^♪♫]*[♪♫]+)\s*$"
)
_TITLE_YEAR_SUFFIX = re.compile(
    r"(?:\s*[\(\[\{]\s*((?:19|20)\d{2})\s*[\)\]\}]|"
    r"\s+((?:19|20)\d{2}))\s*$"
)
_TITLE_TAG_SUFFIX = re.compile(
    r"\s*[\(\[\{]\s*([^\(\)\[\]\{\}]{1,24})\s*[\)\]\}]\s*$"
)
_TITLE_DELIMITED_TAG_SUFFIX = re.compile(
    r"\s*[|¦•]\s*([^|¦•]{1,24})\s*$"
)
_TITLE_TAG_PREFIX = re.compile(
    r"^\s*[\(\[\{]\s*([^\(\)\[\]\{\}]{1,24})\s*[\)\]\}]\s*"
)
_TITLE_YEAR_PREFIX = re.compile(
    r"^\s*((?:19|20)\d{2})\s*[-–—|:]\s*"
)
_TITLE_PLAIN_TAG_SUFFIX = re.compile(
    r"\s+([^\s\(\)\[\]\{\}|¦•]{1,24})\s*$"
)
_TITLE_PROVIDER_TAGS = frozenset((
    "ide", "tr", "tur", "turkce", "türkçe", "trdub", "dub",
    "dublaj", "altyazi", "altyazili", "altyazı", "altyazılı",
    "sub", "en", "eng", "english", "de", "ger", "german", "ar",
    "ara", "arabic", "fr", "fra", "french", "es", "spa", "spanish",
    "it", "ita", "italian", "pt", "por", "portuguese", "ru", "rus",
    "russian", "nl", "nld", "dutch", "fhd", "uhd", "4k", "8k",
    "hd", "sd", "hdr", "hdr10", "dv", "vod", "multi", "webdl",
    "webrip", "bluray", "brrip", "hdtv", "remux", "x264", "x265",
    "h264", "h265", "hevc", "avc",
))
_TITLE_PROVIDER_TAGS = _TITLE_PROVIDER_TAGS.union(
    re.sub(r"[\s._+\-/]+", "", str(tag).casefold())
    for tag in tuple(_LANGUAGE_ALIASES) + tuple(_LANGUAGE_ALIASES.values())
)
_TITLE_RELEASE_TAGS = frozenset((
    "fhd", "uhd", "4k", "8k", "hd", "sd", "hdr", "hdr10", "dv",
    "webdl", "webrip", "bluray", "brrip", "hdtv", "remux", "x264",
    "x265", "h264", "h265", "hevc", "avc", "proper", "repack",
    "hdrip", "dvdrip", "multi",
))
_RELEASE_DECIMAL_FPS = re.compile(
    r"(?<!\d)((?:1\d|[2-9]\d)[\.,_]\d{2,3})(?:\s*fps)?(?!\d)",
    re.IGNORECASE,
)
_RELEASE_INTEGER_FPS = re.compile(
    r"(?<!\d)(\d{2}|\d{4,5})\s*fps(?!\d)", re.IGNORECASE
)
_FPS_STANDARDS = (23.976, 24.0, 25.0, 29.97, 30.0)
_FPS_DOUBLE_STANDARDS = (
    (47.952, 23.976),
    (48.0, 24.0),
    (50.0, 25.0),
    (59.94, 29.97),
    (60.0, 30.0),
)


class SubtitleCue(object):
    __slots__ = ("start_ms", "end_ms", "text")

    def __init__(self, start_ms, end_ms, text):
        self.start_ms = int(start_ms)
        self.end_ms = int(end_ms)
        self.text = str(text)

    def __eq__(self, other):
        return (
            isinstance(other, SubtitleCue)
            and self.start_ms == other.start_ms
            and self.end_ms == other.end_ms
            and self.text == other.text
        )

    def __repr__(self):
        return "SubtitleCue({!r}, {!r}, {!r})".format(
            self.start_ms, self.end_ms, self.text
        )


def _decode_content(content):
    if isinstance(content, str):
        return content
    if not isinstance(content, bytes):
        raise SubtitleError("subtitle_format_unsupported", 422)
    for encoding in ("utf-8-sig", "cp1254", "iso-8859-9", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            pass
    raise SubtitleError("subtitle_format_unsupported", 422)


def _time_ms(value):
    match = _TIME.fullmatch(str(value or "").strip())
    if match is None:
        raise ValueError("invalid subtitle time")
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2))
    seconds = int(match.group(3))
    milliseconds = int(match.group(4))
    if minutes >= 60 or seconds >= 60:
        raise ValueError("invalid subtitle time")
    return ((hours * 60 + minutes) * 60 + seconds) * 1000 + milliseconds


def _clean_text(lines, hearing_impaired=True):
    output = []
    for line in lines:
        line = re.sub(r"(?i)<br\s*/?>", "\n", str(line or ""))
        line = unescape(_TAG.sub("", _STYLE.sub("", line)))
        for part in line.splitlines() or ("",):
            part = " ".join(part.replace("\ufeff", "").split())
            if not part:
                continue
            if not hearing_impaired and _SOUND_LINE.fullmatch(part):
                continue
            output.append(part)
    # Television subtitle renderers become unreadable with unbounded author
    # notes. Keep the latest three semantic lines and a generous text cap.
    return "\n".join(output[:3])[:MAX_TEXT_LENGTH].strip()


def parse_subtitle(content, hearing_impaired=True):
    """Parse bounded SRT or WebVTT text into sorted, non-empty cues."""
    text = _decode_content(content).replace("\r\n", "\n").replace("\r", "\n")
    if len(text.encode("utf-8", "ignore")) > 2 * 1024 * 1024:
        raise SubtitleError("subtitle_too_large", 422)
    blocks = re.split(r"\n[ \t]*\n", text.strip())
    cues = []
    for block in blocks:
        lines = block.split("\n")
        if not lines:
            continue
        first = lines[0].strip().lstrip("\ufeff")
        if first == "WEBVTT" or first.startswith(("NOTE", "STYLE", "REGION")):
            continue
        timing_index = -1
        timing = None
        for index, line in enumerate(lines[:3]):
            timing = _TIMING_LINE.match(line)
            if timing is not None:
                timing_index = index
                break
        if timing is None:
            continue
        try:
            start_ms = _time_ms(timing.group(1))
            end_ms = _time_ms(timing.group(2))
        except ValueError:
            continue
        if end_ms <= start_ms:
            continue
        cue_text = _clean_text(lines[timing_index + 1:], hearing_impaired)
        if cue_text:
            cues.append(SubtitleCue(start_ms, end_ms, cue_text))
        if len(cues) >= MAX_CUES:
            break
    cues.sort(key=lambda cue: (cue.start_ms, cue.end_ms))
    if not cues:
        raise SubtitleError("subtitle_format_unsupported", 422)
    return tuple(cues)


def active_subtitle_text(cues, position_ms, offset_ms=0, starts=None):
    """Return text active at media position using a logarithmic cue lookup."""
    try:
        position_ms = int(position_ms)
        offset_ms = max(-MAX_OFFSET_MS, min(MAX_OFFSET_MS, int(offset_ms)))
    except (TypeError, ValueError, OverflowError):
        return ""
    if not cues or position_ms < 0:
        return ""
    target = position_ms - offset_ms
    if starts is None:
        starts = tuple(cue.start_ms for cue in cues)
    index = bisect_right(starts, target) - 1
    if index < 0:
        return ""
    # A handful of overlapping cues is common in ASS-converted and WebVTT
    # files. Preserve their order without walking the complete subtitle list.
    active = []
    for candidate in range(max(0, index - 4), index + 1):
        cue = cues[candidate]
        if cue.start_ms <= target < cue.end_ms and cue.text not in active:
            active.append(cue.text)
    return "\n".join(active[-2:])[:MAX_TEXT_LENGTH]


def _number(value):
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _year_number(value):
    direct = _number(value)
    if 1900 <= direct <= 2099:
        return direct
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", str(value or ""))
    return int(match.group(1)) if match is not None else 0


def _catalog_year_matches(expected, candidate):
    """Allow adjacent catalogue/release years without accepting old remakes."""
    expected = _year_number(expected)
    candidate = _year_number(candidate)
    if not expected:
        return True
    return bool(candidate and abs(candidate - expected) <= 1)


def _fps_number(value):
    """Return a bounded decimal frame rate from API or decoder formats."""
    if isinstance(value, bool):
        return 0.0
    try:
        number = float(str(value or "").strip().replace(",", "."))
    except (TypeError, ValueError, OverflowError):
        return 0.0
    # Enigma2 and some subtitle providers expose 23.976 as 23976.
    if number > 1000.0:
        number /= 1000.0
    if not 10.0 <= number <= 120.0:
        return 0.0
    return round(number, 3)


def canonical_subtitle_fps(value):
    """Return a timeline FPS, normalizing common doubled decoder values."""
    number = _fps_number(value)
    if not number:
        return 0.0
    for doubled, canonical in _FPS_DOUBLE_STANDARDS:
        if abs(number - doubled) <= 0.08:
            return canonical
    for standard in _FPS_STANDARDS:
        if abs(number - standard) <= 0.08:
            return standard
    return number


def subtitle_fps_scale(video_fps, subtitle_fps):
    """Return a safe subtitle-time multiplier or zero when it is a guess.

    The timestamp for frame ``n`` is ``n / fps``.  Moving timestamps from a
    subtitle release to the playing video therefore uses
    ``subtitle_fps / video_fps``.  Only well-known PAL/film and NTSC decimal
    pairs are accepted; unrelated rates must never be stretched blindly.
    """
    video = canonical_subtitle_fps(video_fps)
    subtitle = canonical_subtitle_fps(subtitle_fps)
    if not video or not subtitle:
        return 0.0
    if abs(video - subtitle) <= 0.01:
        return 1.0
    film_rates = (23.976, 24.0, 25.0)
    known_pair = (
        video in film_rates and subtitle in film_rates
    ) or {video, subtitle} == {29.97, 30.0}
    if not known_pair:
        return 0.0
    factor = subtitle / video
    if not 0.95 <= factor <= 1.05:
        return 0.0
    return round(factor, 9)


def _duration_seconds(value):
    try:
        number = float(value or 0)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not 1.0 <= number <= 48.0 * 60.0 * 60.0:
        return 0.0
    return round(number, 3)


def _release_fps(value):
    """Extract an explicitly written FPS value without mistaking a year."""
    text = str(value or "")[:240]
    match = _RELEASE_DECIMAL_FPS.search(text)
    if match is not None:
        return _fps_number(match.group(1).replace("_", "."))
    match = _RELEASE_INTEGER_FPS.search(text)
    if match is not None:
        return _fps_number(match.group(1))
    return 0.0


def _normalise_resolution(value):
    try:
        number = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        match = re.search(r"(?<!\d)(2160|1080|720|576|480)[pi]?(?!\d)",
                          str(value or ""), re.IGNORECASE)
        number = int(match.group(1)) if match is not None else 0
    return number if number in (2160, 1080, 720, 576, 480) else 0


def _release_profile(value):
    """Build local-only compatibility hints from a bounded release label."""
    text = _safe_text(value, limit=240).casefold()
    compact = re.sub(r"[^a-z0-9]+", "", text)
    source = ""
    for marker, canonical in (
        ("webdl", "webdl"),
        ("webrip", "webrip"),
        ("bluray", "bluray"),
        ("bdrip", "bluray"),
        ("brrip", "bluray"),
        ("hdtv", "hdtv"),
        ("dvdrip", "dvd"),
        ("dvd", "dvd"),
    ):
        if marker in compact:
            source = canonical
            break
    resolution_match = re.search(
        r"(?<!\d)(2160|1080|720|576|480)[pi](?!\d)", text,
        re.IGNORECASE,
    )
    resolution = (
        int(resolution_match.group(1)) if resolution_match is not None else 0
    )
    codec = ""
    if any(marker in compact for marker in ("x265", "h265", "hevc")):
        codec = "h265"
    elif any(marker in compact for marker in ("x264", "h264", "avc")):
        codec = "h264"
    editions = tuple(
        marker for marker in (
            "extended", "directorscut", "unrated", "theatrical", "imax",
            "remastered",
        )
        if marker in compact
    )
    return {
        "source": source,
        "resolution": resolution,
        "codec": codec,
        "editions": editions,
    }


def _subtitle_fps(candidate, parent, release):
    for item in (candidate, parent):
        if not isinstance(item, dict):
            continue
        for key in ("fps", "framerate", "frame_rate"):
            value = _fps_number(item.get(key))
            if value:
                return value
    return _release_fps(release)


def _release_match_key(meta, result):
    """Rank same-language results without guessing when hints are absent."""
    target = _release_profile(meta.get("release_hint"))
    candidate = _release_profile(result.get("release"))
    target_fps = _fps_number(meta.get("fps"))
    candidate_fps = _fps_number(result.get("fps"))
    penalty = 0
    evidence = 0

    if target_fps:
        if candidate_fps:
            evidence += 1
            difference = abs(target_fps - candidate_fps)
            if difference > 0.15:
                penalty += 12
            elif difference > 0.06:
                penalty += 2
        else:
            penalty += 3

    target_source = target["source"]
    if target_source:
        if candidate["source"] == target_source:
            evidence += 1
        elif candidate["source"]:
            penalty += 8
        else:
            penalty += 2

    target_resolution = (
        _normalise_resolution(meta.get("resolution"))
        or target["resolution"]
    )
    if target_resolution:
        if candidate["resolution"] == target_resolution:
            evidence += 1
        elif candidate["resolution"]:
            penalty += 3
        else:
            penalty += 1

    if target["codec"]:
        if candidate["codec"] == target["codec"]:
            evidence += 1
        elif candidate["codec"]:
            penalty += 2
        else:
            penalty += 1

    target_editions = set(target["editions"])
    candidate_editions = set(candidate["editions"])
    has_target_hint = bool(
        target_fps
        or target_source
        or target_resolution
        or target["codec"]
        or target_editions
    )
    if target_editions:
        if target_editions == candidate_editions:
            evidence += 1
        elif not candidate_editions:
            penalty += 4
        else:
            penalty += 10
    elif has_target_hint and candidate_editions:
        # Prefer an ordinary cut when playback metadata does not name a
        # special edition; special cuts commonly have a different timeline.
        penalty += 2

    return penalty, -evidence


def subtitle_timeline_compatibility(cues, video_duration_seconds):
    """Compare the final cue with the decoder duration conservatively.

    Credits commonly continue after the last spoken line, so a subtitle is
    considered plausible across a deliberately broad window.  Only grossly
    short or overlong timelines are classified as another version.
    """
    duration = _duration_seconds(video_duration_seconds)
    if not cues or not duration:
        return "unknown", 0.0
    try:
        end_seconds = max(0.0, float(cues[-1].end_ms) / 1000.0)
    except (AttributeError, TypeError, ValueError, OverflowError):
        return "unknown", 0.0
    ratio = end_seconds / duration
    if 0.55 <= ratio <= 1.08:
        return "match", round(ratio, 4)
    if ratio < 0.35 or ratio > 1.15:
        return "mismatch", round(ratio, 4)
    return "unknown", round(ratio, 4)


def retime_subtitle_cues(cues, result):
    """Preserve timestamped captions unless conversion was explicitly verified.

    A provider's FPS describes a release, not proof that its SRT timestamps
    need stretching. Compatibility hints alone must never alter movie time.
    """
    entries = tuple(cues or ())
    if not entries or not isinstance(result, dict):
        return entries, 1.0, "unknown"
    try:
        factor = subtitle_fps_scale(
            result.get("video_fps"), result.get("subtitle_fps")
        ) if result.get("fps_conversion_verified") is True else 1.0
    except (TypeError, ValueError, OverflowError):
        factor = 1.0
    if (
        result.get("compatibility_status") != "fps_convert"
        or result.get("fps_conversion_verified") is not True
        or not 0.95 <= factor <= 1.05
        or abs(factor - 1.0) <= 0.00001
    ):
        state, unused_ratio = subtitle_timeline_compatibility(
            entries, result.get("video_duration_seconds")
        )
        return entries, 1.0, state
    scaled = []
    for cue in entries:
        start_ms = max(0, int(round(cue.start_ms * factor)))
        end_ms = max(start_ms + 1, int(round(cue.end_ms * factor)))
        scaled.append(SubtitleCue(start_ms, end_ms, cue.text))
    scaled = tuple(scaled)
    state, ratio = subtitle_timeline_compatibility(
        scaled, result.get("video_duration_seconds")
    )
    # A provider FPS field can occasionally describe a container rather than
    # the subtitle timeline.  Never apply it when it would extend cues far
    # beyond the playing feature.
    if state == "mismatch" and ratio > 1.15:
        original_state, unused_ratio = subtitle_timeline_compatibility(
            entries, result.get("video_duration_seconds")
        )
        return entries, 1.0, original_state
    return scaled, factor, state


def annotate_subtitle_compatibility(metadata, result):
    """Attach a stable compatibility class and priority tuple to one result."""
    item = dict(result) if isinstance(result, dict) else {}
    language = item.get("language") or "en"
    meta = normalized_metadata(metadata, language)
    target = _release_profile(meta.get("release_hint"))
    candidate = _release_profile(item.get("release"))

    identity_mismatch = item.get("identity_match") is False
    expected_season = _number(meta.get("season"))
    expected_episode = _number(meta.get("episode"))
    found_season = _number(item.get("season"))
    found_episode = _number(item.get("episode"))
    if (
        expected_season and expected_episode
        and found_season and found_episode
        and (expected_season, expected_episode) != (found_season, found_episode)
    ):
        identity_mismatch = True

    target_source = target["source"]
    candidate_source = candidate["source"]
    if target_source and candidate_source:
        source_state = (
            "match" if target_source == candidate_source else "mismatch"
        )
    else:
        source_state = "unknown"

    target_editions = set(target["editions"])
    candidate_editions = set(candidate["editions"])
    target_has_release_profile = bool(
        target_source or target["resolution"] or target["codec"]
    )
    if target_editions:
        if target_editions == candidate_editions:
            edition_state = "match"
        elif candidate_editions:
            edition_state = "mismatch"
        else:
            edition_state = "unknown"
    elif candidate_editions and target_has_release_profile:
        edition_state = "mismatch"
    else:
        edition_state = "unknown"

    video_fps = canonical_subtitle_fps(meta.get("fps"))
    subtitle_fps = canonical_subtitle_fps(item.get("fps"))
    fps_scale = subtitle_fps_scale(video_fps, subtitle_fps)
    if video_fps and subtitle_fps:
        if abs(video_fps - subtitle_fps) <= 0.01:
            fps_state = "match"
        elif fps_scale:
            fps_state = "convert"
        else:
            fps_state = "mismatch"
    else:
        fps_state = "unknown"

    duration_state = "unknown"
    video_duration = _duration_seconds(meta.get("duration_seconds"))
    subtitle_duration = _duration_seconds(item.get("subtitle_duration_seconds"))
    if video_duration and subtitle_duration:
        ratio = subtitle_duration / video_duration
        if 0.55 <= ratio <= 1.08:
            duration_state = "match"
        elif ratio < 0.35 or ratio > 1.15:
            duration_state = "mismatch"

    target_resolution = (
        _normalise_resolution(meta.get("resolution"))
        or target["resolution"]
    )
    candidate_resolution = candidate["resolution"]
    if target_resolution and candidate_resolution:
        resolution_state = (
            "match"
            if target_resolution == candidate_resolution
            else "mismatch"
        )
    else:
        resolution_state = "unknown"

    if target["codec"] and candidate["codec"]:
        codec_state = (
            "match" if target["codec"] == candidate["codec"] else "mismatch"
        )
    else:
        codec_state = "unknown"

    score = 50
    score += {"match": 15, "mismatch": -10}.get(source_state, 0)
    score += {"match": 15, "mismatch": -35}.get(edition_state, 0)
    score += {"match": 20, "convert": 14, "mismatch": -25}.get(
        fps_state, 0
    )
    score += {"match": 5, "mismatch": -20}.get(duration_state, 0)
    score += {"match": 3, "mismatch": -1}.get(resolution_state, 0)
    score += {"match": 2, "mismatch": -1}.get(codec_state, 0)
    if identity_mismatch:
        score = 0
    score = max(0, min(100, int(score)))

    different = bool(
        identity_mismatch
        or edition_state == "mismatch"
        or fps_state == "mismatch"
        or duration_state == "mismatch"
    )
    evidence = any(state != "unknown" for state in (
        source_state, edition_state, fps_state, duration_state,
        resolution_state, codec_state,
    ))
    if different:
        status = "different"
    elif fps_state == "convert" and item.get("fps_conversion_verified") is True:
        status = "fps_convert"
    elif source_state == "match" and fps_state == "match" and score >= 80:
        status = "high"
    elif evidence:
        status = "possible"
    else:
        status = "unknown"

    state_rank = {
        "match": 0, "convert": 1, "unknown": 2, "mismatch": 3,
    }
    item.update({
        "identity_match": not identity_mismatch,
        "compatibility_status": status,
        "compatibility_score": score,
        "video_fps": video_fps,
        "subtitle_fps": subtitle_fps,
        "fps_scale": fps_scale if status == "fps_convert" else 1.0,
        "fps_scale_hint": fps_scale if fps_state == "convert" else 1.0,
        "video_duration_seconds": video_duration,
        "compatibility_sort": (
            1 if status == "different" else 0,
            state_rank[source_state],
            state_rank[edition_state],
            state_rank[fps_state],
            state_rank[duration_state],
            state_rank[resolution_state],
            state_rank[codec_state],
            -score,
        ),
    })
    return item


def annotate_subtitle_results(metadata, results):
    return [
        annotate_subtitle_compatibility(metadata, result)
        for result in (results or ())
        if isinstance(result, dict)
    ]


def _title_tag_key(value):
    return re.sub(r"[\s._+\-/]+", "", str(value or "").casefold())


def _plain_release_tag(value):
    """Return a bounded IPTV release tag, never an arbitrary title word."""
    tag = _title_tag_key(value)
    if tag in _TITLE_RELEASE_TAGS:
        return tag
    if re.fullmatch(r"(?:2160|1080|720|576|480)p", tag):
        return tag
    if re.fullmatch(r"(?:ddp?|eac3|ac3|dts|aac)\d{0,3}", tag):
        return tag
    return ""


def _subtitle_title_parts(value, year=0):
    """Return a clean provider title and a conservatively inferred year.

    Stalker/Ministra catalogues frequently decorate a real film name with a
    leading language marker and a trailing release profile, for example
    ``[NL] Film (2022) MULTI 4K HDR``.  Provider identity checks must compare
    the semantic title, while the receiver continues to display the original
    portal text unchanged.
    """
    original = " ".join(str(value or "").split())[:120]
    title = original
    try:
        expected_year = int(year or 0)
    except (TypeError, ValueError, OverflowError):
        expected_year = 0
    inferred_year = expected_year
    release_suffix_removed = False
    for unused_index in range(12):
        changed = False
        match = _TITLE_TAG_PREFIX.search(title)
        if match is not None and _title_tag_key(
            match.group(1)
        ) in _TITLE_PROVIDER_TAGS:
            title = title[match.end():].lstrip(" -|:/")
            changed = True
        match = _TITLE_YEAR_PREFIX.search(title)
        if match is not None:
            found_year = _number(match.group(1))
            if not expected_year or found_year == expected_year:
                inferred_year = inferred_year or found_year
                title = title[match.end():].lstrip(" -|:/")
                changed = True
        match = _TITLE_PLAIN_TAG_SUFFIX.search(title)
        if match is not None:
            tag = _plain_release_tag(match.group(1))
            # MULTI is a real word in a few titles.  Treat it as a bare IPTV
            # marker only after another release tag, or when it follows a
            # bounded release year as in ``(2022) MULTI``.
            multi_has_context = bool(
                release_suffix_removed
                or _TITLE_YEAR_SUFFIX.search(title[:match.start()])
            )
            if tag and (tag != "multi" or multi_has_context):
                title = title[:match.start()].rstrip(" -|:/")
                release_suffix_removed = True
                changed = True
        match = _TITLE_YEAR_SUFFIX.search(title)
        if match is not None:
            found_year = _number(match.group(1) or match.group(2))
            if not expected_year or found_year == expected_year:
                inferred_year = inferred_year or found_year
                title = title[:match.start()].rstrip(" -|:/")
                changed = True
        match = _TITLE_TAG_SUFFIX.search(title)
        if match is not None:
            tag = _title_tag_key(match.group(1))
            if tag in _TITLE_PROVIDER_TAGS:
                title = title[:match.start()].rstrip(" -|:/")
                changed = True
        match = _TITLE_DELIMITED_TAG_SUFFIX.search(title)
        if match is not None:
            tag = _title_tag_key(match.group(1))
            if tag in _TITLE_PROVIDER_TAGS:
                title = title[:match.start()].rstrip(" -|:/")
                changed = True
        if not changed:
            break
    return (title if len(title) >= 2 else original), inferred_year


def _subtitle_search_title(value, year=0):
    """Remove bounded IPTV decorations while preserving the display title."""
    return _subtitle_title_parts(value, year)[0]


def normalized_metadata(metadata, language):
    metadata = metadata if isinstance(metadata, dict) else {}
    title = " ".join(str(metadata.get("title") or "").split())[:120]
    year = _year_number(metadata.get("year"))
    search_title, inferred_year = _subtitle_title_parts(title, year)
    year = year or inferred_year
    tmdb_id = str(metadata.get("tmdb_id") or "").strip()
    imdb_id = str(metadata.get("imdb_id") or "").strip().lower()
    sd_id = str(metadata.get("sd_id") or "").strip()
    return {
        "title": title,
        "search_title": search_title,
        "year": year,
        "season": _number(metadata.get("season")),
        "episode": _number(metadata.get("episode")),
        "content_type": (
            "series"
            if str(metadata.get("content_type") or "").lower() == "series"
            else "movie"
        ),
        "language": str(language or "en").split("_", 1)[0].lower()[:3],
        "tmdb_id": tmdb_id if tmdb_id.isdigit() else "",
        "imdb_id": imdb_id if re.fullmatch(r"tt\d{5,12}", imdb_id) else "",
        "sd_id": sd_id if re.fullmatch(r"[A-Za-z0-9_-]{1,120}", sd_id) else "",
        # These hints are used only to rank the returned provider entries. They
        # are never included in the provider query or request headers.
        "release_hint": _safe_text(metadata.get("release_hint"), limit=240),
        "fps": _fps_number(metadata.get("fps")),
        "resolution": _normalise_resolution(metadata.get("resolution")),
        "duration_seconds": _duration_seconds(
            metadata.get("duration_seconds")
        ),
    }


def _safe_text(value, limit=120):
    if not isinstance(value, (str, int, float)):
        return ""
    value = " ".join(str(value).split())[:limit]
    return "" if "://" in value else value


def _checked_reply(data):
    if not isinstance(data, dict):
        raise SubtitleError("provider_invalid_reply", 502)
    error = data.get("error")
    if isinstance(error, dict):
        code = str(error.get("code") or "").lower()
        if code in ("quota_exceeded", "rate_limit_exceeded"):
            raise SubtitleError("provider_limit", 429)
        if code in ("invalid_api_key", "unauthorized", "forbidden"):
            raise SubtitleError("provider_key_rejected", 401)
        raise SubtitleError("provider_unavailable", 502)
    if data.get("status") is False:
        raise SubtitleError("provider_unavailable", 502)
    return data


def _subtitle_items(data):
    # SubDL can report an ordinary empty search as status=false without an
    # error object.  That is not a provider outage and must reach the UI as an
    # empty result list ("No subtitles found").
    if (
        isinstance(data, dict)
        and data.get("status") is False
        and not isinstance(data.get("error"), dict)
        and data.get("subtitles") in (None, [])
    ):
        return []
    data = _checked_reply(data)
    items = data.get("subtitles", [])
    if not isinstance(items, list):
        raise SubtitleError("provider_invalid_reply", 502)
    return items


def _title_identity(value):
    return re.sub(r"[^\w]+", "", str(value or "").casefold())


def _resolved_sd_id(meta, api_key):
    """Resolve a title/year through SubDL's documented movie-search route."""
    if not meta["year"]:
        return ""
    params = {
        "q": meta["search_title"],
        "type": "tv" if meta["content_type"] == "series" else "movie",
        "limit": 10,
    }
    data = _checked_reply(_request(
        "https://api.subdl.com/api/v2/movies/search?" + urlencode(params),
        ("api.subdl.com",),
        {"Authorization": "Bearer " + api_key},
    ))
    items = data.get("results", [])
    if not isinstance(items, list):
        raise SubtitleError("provider_invalid_reply", 502)
    expected_title = _title_identity(meta["search_title"])
    matches = []
    for index, item in enumerate(items[:30]):
        if not isinstance(item, dict):
            continue
        candidate_year = _year_number(item.get("year"))
        sd_id = str(item.get("sd_id") or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,120}", sd_id):
            continue
        names = (item.get("name"), item.get("original_name"))
        exact = any(_title_identity(name) == expected_title for name in names)
        if exact and _catalog_year_matches(meta["year"], candidate_year):
            matches.append((abs(candidate_year - meta["year"]), index, sd_id))
    matches.sort()
    if not matches and items:
        _provider_candidate_log("subdl", "title-resolve", data)
    return matches[0][2] if matches else ""


def _provider_search_log(provider, stage, meta, requested, raw=None, accepted=None):
    fields = [
        "search provider={}".format(provider),
        "stage={}".format(stage),
        "title={}".format(_safe_text(meta.get("search_title"), limit=120)),
        "year={}".format(_year_number(meta.get("year")) or 0),
        "season={}".format(_number(meta.get("season")) or 0),
        "episode={}".format(_number(meta.get("episode")) or 0),
        "imdb={}".format(meta.get("imdb_id") or "--"),
        "tmdb={}".format(meta.get("tmdb_id") or "--"),
        "languages={}".format(",".join(requested) or "--"),
    ]
    if raw is not None:
        fields.append("raw={}".format(max(0, int(raw))))
    if accepted is not None:
        fields.append("accepted={}".format(max(0, int(accepted))))
    log_event("subtitles", " ".join(fields))


def _provider_candidate_log(provider, stage, data):
    """Log only bounded public catalogue identity fields for rejected hits."""
    candidates = []
    if provider == "subdl" and isinstance(data, dict):
        entries = data.get("results")
        if not isinstance(entries, list) or not entries:
            entries = data.get("subtitles")
        for item in entries[:3] if isinstance(entries, list) else ():
            if not isinstance(item, dict):
                continue
            candidates.append((
                item.get("name")
                or item.get("original_name")
                or item.get("title")
                or item.get("release_name"),
                item.get("year") or item.get("release_year"),
                item.get("imdb_id"),
                item.get("tmdb_id"),
                item.get("sd_id"),
            ))
    elif provider == "opensubtitles" and isinstance(data, dict):
        entries = data.get("data")
        for item in entries[:3] if isinstance(entries, list) else ():
            if not isinstance(item, dict):
                continue
            attributes = item.get("attributes")
            attributes = attributes if isinstance(attributes, dict) else {}
            details = attributes.get("feature_details")
            details = details if isinstance(details, dict) else {}
            candidates.append((
                details.get("movie_name")
                or details.get("title")
                or details.get("parent_title")
                or attributes.get("movie_name")
                or attributes.get("title")
                or attributes.get("release"),
                details.get("year")
                or details.get("release_year")
                or attributes.get("year"),
                details.get("imdb_id") or attributes.get("imdb_id"),
                details.get("tmdb_id") or attributes.get("tmdb_id"),
                "",
            ))
    for index, candidate in enumerate(candidates, 1):
        title, year, imdb_id, tmdb_id, sd_id = candidate
        log_event("subtitles", " ".join((
            "candidate provider={}".format(provider),
            "stage={}".format(stage),
            "index={}".format(index),
            "title={}".format(_safe_text(title, limit=120) or "--"),
            "year={}".format(_year_number(year) or 0),
            "imdb={}".format(_safe_text(imdb_id, limit=24) or "--"),
            "tmdb={}".format(_safe_text(tmdb_id, limit=24) or "--"),
            "sd={}".format(_safe_text(sd_id, limit=120) or "--"),
        )))


def _episode_matches(item, season, episode):
    if not season or not episode:
        return True
    try:
        found_season = int(item.get("season", item.get("season_number")) or 0)
        found_episode = int(item.get("episode", item.get("episode_number")) or 0)
    except (TypeError, ValueError, OverflowError):
        found_season, found_episode = 0, 0
    if found_season and found_episode:
        return (found_season, found_episode) == (season, episode)
    release = _safe_text(
        item.get("release_name") or item.get("name"), limit=240
    )
    match = re.search(
        r"(?i)\bS(\d{1,2})[ ._-]*E(\d{1,3})\b|\b(\d{1,2})x(\d{1,3})\b",
        release,
    )
    if match:
        found = match.group(1, 2) if match.group(1) else match.group(3, 4)
        return tuple(map(int, found)) == (season, episode)
    return False


def _numeric_identifier(value, imdb=False):
    value = str(value or "").strip().lower()
    if imdb and value.startswith("tt"):
        value = value[2:]
    return value if value.isdigit() else ""


def _subdl_response_matches(meta, data):
    """Verify SubDL's returned catalogue identity when it is available."""
    entries = data.get("results") if isinstance(data, dict) else None
    if not isinstance(entries, list) or not entries:
        # Older/lean replies omit the catalogue block. The provider already
        # applied the requested identifier in that response shape.
        return True
    expected_title = _title_identity(meta.get("search_title"))
    expected_year = _year_number(meta.get("year"))
    expected_sd = str(meta.get("sd_id") or "").strip()
    expected_imdb = _numeric_identifier(meta.get("imdb_id"), imdb=True)
    expected_tmdb = _numeric_identifier(meta.get("tmdb_id"))
    for item in entries[:30]:
        if not isinstance(item, dict):
            continue
        candidate_sd = str(item.get("sd_id") or "").strip()
        candidate_imdb = _numeric_identifier(item.get("imdb_id"), imdb=True)
        candidate_tmdb = _numeric_identifier(item.get("tmdb_id"))
        identifier_checks = []
        if expected_sd and candidate_sd:
            identifier_checks.append(expected_sd == candidate_sd)
        if expected_imdb and candidate_imdb:
            identifier_checks.append(expected_imdb == candidate_imdb)
        if expected_tmdb and candidate_tmdb:
            identifier_checks.append(expected_tmdb == candidate_tmdb)
        candidate_year = _year_number(
            item.get("year") or item.get("release_year")
        )
        names = (
            item.get("name"), item.get("original_name"), item.get("title")
        )
        has_candidate_title = any(_safe_text(name) for name in names)
        title_matches = any(
            _title_identity(_subtitle_search_title(
                name, candidate_year or expected_year
            )) == expected_title
            for name in names
            if _safe_text(name)
        )
        if identifier_checks:
            if not all(identifier_checks):
                continue
            # Catch stale IPTV catalogue identifiers when SubDL also supplies
            # a human-readable identity for the referenced feature.
            if has_candidate_title and not title_matches:
                continue
            if (
                meta.get("content_type") != "series"
                and expected_year
                and not _catalog_year_matches(expected_year, candidate_year)
            ):
                continue
            return True
        if not title_matches:
            continue
        if meta.get("content_type") == "series":
            return True
        if _catalog_year_matches(expected_year, candidate_year):
            return True
    return False


def _opensubtitles_identity_matches(meta, attributes, details):
    """Accept only results that can be tied to the requested programme.

    OpenSubtitles normally applies the query identifiers server-side, but a
    broad or partially indexed response can still contain another feature.
    Prefer returned IMDb/TMDb identifiers for films and otherwise require an
    exact normalized title/year pair. Episodes additionally use their exact
    season/episode check in ``search`` below.
    """
    expected_title = _title_identity(meta.get("search_title"))
    candidate_year = _year_number(
        details.get("year")
        or details.get("release_year")
        or attributes.get("year")
    )
    candidate_titles = (
        details.get("movie_name"),
        details.get("title"),
        details.get("parent_title"),
        attributes.get("movie_name"),
        attributes.get("title"),
    )
    has_candidate_title = any(_safe_text(candidate) for candidate in candidate_titles)
    title_matches = any(
        _title_identity(_subtitle_search_title(
            candidate, candidate_year or meta.get("year", 0)
        )) == expected_title
        for candidate in candidate_titles
        if _safe_text(candidate)
    )

    expected_imdb = _numeric_identifier(meta.get("imdb_id"), imdb=True)
    expected_tmdb = _numeric_identifier(meta.get("tmdb_id"))
    candidate_imdb = _numeric_identifier(
        details.get("imdb_id") or attributes.get("imdb_id"), imdb=True
    )
    candidate_tmdb = _numeric_identifier(
        details.get("tmdb_id") or attributes.get("tmdb_id")
    )

    if meta.get("content_type") == "series":
        # Episode IMDb/TMDb identifiers are not necessarily the parent show's
        # identifiers. The returned programme title plus S/E is authoritative;
        # matching IDs remain a safe fallback when a title is omitted.
        return bool(
            title_matches
            or (expected_imdb and candidate_imdb == expected_imdb)
            or (expected_tmdb and candidate_tmdb == expected_tmdb)
        )

    identifier_checks = []
    if expected_imdb and candidate_imdb:
        identifier_checks.append(expected_imdb == candidate_imdb)
    if expected_tmdb and candidate_tmdb:
        identifier_checks.append(expected_tmdb == candidate_tmdb)
    if identifier_checks:
        if not all(identifier_checks):
            return False
        if has_candidate_title and not title_matches:
            return False
        expected_year = _year_number(meta.get("year"))
        if expected_year and not _catalog_year_matches(
            expected_year, candidate_year
        ):
            return False
        return True

    if not title_matches:
        return False
    expected_year = _year_number(meta.get("year"))
    if expected_year:
        return _catalog_year_matches(expected_year, candidate_year)
    return True


def _download_url(candidate, parent):
    path = candidate.get("url") or parent.get("url") or ""
    if isinstance(path, str) and path.startswith("/subtitle/") and ".." not in path:
        return "https://dl.subdl.com" + path
    if isinstance(path, str) and path.startswith("https://dl.subdl.com/subtitle/"):
        return path
    n_id = candidate.get("n_id")
    if candidate is parent and not n_id:
        n_id = parent.get("n_id")
    n_id = str(n_id or "")
    if re.fullmatch(r"[A-Za-z0-9_-]{1,120}", n_id):
        return "https://api.subdl.com/api/v2/subtitles/{}/download?format=file".format(
            quote(n_id, safe="")
        )
    return ""


def _language_code(candidate, parent, requested):
    raw = str(
        candidate.get("language")
        or candidate.get("lang")
        or parent.get("language")
        or parent.get("lang")
        or ""
    ).strip().lower().replace("-", "_")
    raw = _LANGUAGE_ALIASES.get(raw, raw.split("_", 1)[0][:3])
    return raw if raw in requested else (requested[0] if requested else "en")


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


class SubDLClient(object):
    """Minimal first-party provider adapter; API keys never leave SubDL."""

    def __init__(self, api_key):
        self.api_key = "".join(str(api_key or "").split())[:300]

    def _require_key(self):
        if not self.api_key:
            raise SubtitleError("provider_key_missing", 409)

    def test(self):
        self._require_key()
        reply = _request(
            "https://api.subdl.com/api/v2/me",
            ("api.subdl.com",),
            {"Authorization": "Bearer " + self.api_key},
        )
        if not isinstance(reply, dict):
            raise SubtitleError("provider_invalid_reply", 502)
        error = reply.get("error")
        if isinstance(error, dict):
            code = str(error.get("code") or "").lower()
            if code in ("invalid_api_key", "unauthorized", "forbidden"):
                raise SubtitleError("provider_key_rejected", 401)
            raise SubtitleError("provider_unavailable", 502)
        return True

    def search(self, metadata, languages):
        self._require_key()
        requested = []
        for language in languages:
            code = normalized_metadata({}, language)["language"]
            if code not in requested:
                requested.append(code)
        meta = normalized_metadata(metadata, requested[0] if requested else "en")
        if len(meta["title"]) < 2:
            raise SubtitleError("invalid_search", 400)
        base_params = {
            "type": "tv" if meta["content_type"] == "series" else "movie",
            "languages": ",".join(requested or ("en",)),
            "subs_per_page": 30,
            "unpack": 1,
        }
        if meta["content_type"] == "series":
            if meta["season"]:
                base_params["season"] = meta["season"]
            if meta["episode"]:
                base_params["episode"] = meta["episode"]

        def collect(data, match_meta):
            items = _subtitle_items(data)
            if not _subdl_response_matches(match_meta, data):
                return len(items), []
            results = []
            seen = set()
            for parent in items[:50]:
                if not isinstance(parent, dict):
                    continue
                files = parent.get("unpack_files") or []
                candidates = (
                    files if isinstance(files, list) and files else [parent]
                )
                for candidate in candidates[:50]:
                    if not isinstance(candidate, dict) or not _episode_matches(
                        candidate, meta["season"], meta["episode"]
                    ):
                        continue
                    url = _download_url(candidate, parent)
                    if not url:
                        continue
                    release = _safe_text(
                        candidate.get("release_name")
                        or candidate.get("name")
                        or parent.get("release_name")
                    ) or "Subtitle"
                    identity = (url, release)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    results.append({
                        "provider": "subdl",
                        "title": meta["title"],
                        "identity_match": True,
                        "season": meta["season"],
                        "episode": meta["episode"],
                        "release": release,
                        "language": _language_code(candidate, parent, requested),
                        "hearing_impaired": _boolean(
                            candidate.get(
                                "hi",
                                candidate.get(
                                    "hearing_impaired",
                                    parent.get(
                                        "hi",
                                        parent.get("hearing_impaired", False),
                                    ),
                                ),
                            )
                        ),
                        "fps": _subtitle_fps(candidate, parent, release),
                        "url": url,
                    })
            return len(items), results

        language_priority = {
            language: index for index, language in enumerate(requested)
        }

        def ranked(results):
            # SubDL does not guarantee that its response follows the requested
            # language order. Keep provider ranking within each language.
            results.sort(key=lambda result: (
                language_priority.get(
                    result.get("language"), len(language_priority)
                ),
                bool(result.get("hearing_impaired")),
            ) + _release_match_key(meta, result))
            return results

        def request_results(extra, stage, match_meta=None):
            params = dict(base_params)
            params.update(extra)
            _provider_search_log("subdl", stage, meta, requested)
            data = _request(
                "https://api.subdl.com/api/v2/subtitles/search?"
                + urlencode(params),
                ("api.subdl.com",),
                {"Authorization": "Bearer " + self.api_key},
            )
            raw, results = collect(data, match_meta or meta)
            _provider_search_log(
                "subdl", stage, meta, requested, raw, len(results)
            )
            if raw and not results:
                _provider_candidate_log("subdl", stage, data)
            return ranked(results)

        identifier = None
        if meta["sd_id"]:
            identifier = ({"sd_id": meta["sd_id"]}, "sd-id")
        elif meta["imdb_id"]:
            identifier = ({"imdb_id": meta["imdb_id"]}, "imdb-id")
        elif meta["tmdb_id"]:
            identifier = ({"tmdb_id": meta["tmdb_id"]}, "tmdb-id")
        if identifier is not None:
            results = request_results(*identifier)
            if results:
                return results

        title_meta = dict(meta)
        title_meta["sd_id"] = ""
        title_meta["imdb_id"] = ""
        title_meta["tmdb_id"] = ""
        _provider_search_log("subdl", "title-resolve", meta, requested)
        resolved = _resolved_sd_id(meta, self.api_key)
        _provider_search_log(
            "subdl", "title-resolve", meta, requested,
            accepted=1 if resolved else 0,
        )
        if resolved:
            results = request_results(
                {"sd_id": resolved}, "title-sd-id", title_meta
            )
            if results:
                return results
        return request_results(
            {"film_name": meta["search_title"]}, "title", title_meta
        )

    def download(self, result):
        self._require_key()
        if not isinstance(result, dict) or result.get("provider") != "subdl":
            raise SubtitleError("subtitle_not_found", 404)
        secured = dict(result)
        secured["headers"] = {"Authorization": "Bearer " + self.api_key}
        return _download("subdl", secured, extensions=(".srt", ".vtt"))


def provider_display_name(provider):
    return PROVIDER_NAMES.get(str(provider or "").lower(), "SubDL")


def _opensubtitles_api_base(value):
    """Accept only the two API hosts documented in a login response."""
    raw = str(value or "").strip()
    if not raw:
        return "https://api.opensubtitles.com/api/v1"
    if "://" not in raw:
        raw = "https://" + raw
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError:
        raise SubtitleError("provider_invalid_reply", 502)
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or port not in (None, 443)
        or host not in ("api.opensubtitles.com", "vip-api.opensubtitles.com")
    ):
        raise SubtitleError("provider_invalid_reply", 502)
    return "https://{}/api/v1".format(host)


def _opensubtitles_token(value):
    value = str(value or "").strip()
    if (
        not value
        or len(value) > 4096
        or any(ord(character) < 33 for character in value)
    ):
        raise SubtitleError("provider_invalid_reply", 502)
    return value


class OpenSubtitlesClient(object):
    """OpenSubtitles REST adapter with optional account authentication."""

    API_BASE = "https://api.opensubtitles.com/api/v1"
    API_HOSTS = ("api.opensubtitles.com", "vip-api.opensubtitles.com")

    def __init__(self, api_key, username="", password=""):
        self.api_key = "".join(str(api_key or "").split())[:300]
        self.username = str(username or "").strip()[:160]
        self.password = str(password or "")[:300]
        self._token = ""
        self._base_url = self.API_BASE

    def _require_key(self):
        if not self.api_key:
            raise SubtitleError("provider_key_missing", 409)

    def _login_configured(self):
        if not self.username or not self.password:
            return False
        for value in (self.username, self.password):
            if any(ord(character) < 32 for character in value):
                return False
        return True

    def _headers(self, authenticated=False):
        headers = {
            "Api-Key": self.api_key,
            "Content-Type": "application/json",
        }
        if authenticated:
            headers["Authorization"] = "Bearer " + self._token
        return headers

    def _login(self):
        self._require_key()
        if not self._login_configured():
            return ""
        if self._token:
            return self._token
        reply = _request(
            self.API_BASE + "/login",
            self.API_HOSTS,
            self._headers(),
            json.dumps({
                "username": self.username,
                "password": self.password,
            }).encode("utf-8"),
        )
        if not isinstance(reply, dict):
            raise SubtitleError("provider_invalid_reply", 502)
        self._token = _opensubtitles_token(reply.get("token"))
        self._base_url = _opensubtitles_api_base(reply.get("base_url"))
        return self._token

    def test(self):
        self._require_key()
        reply = _request(
            self.API_BASE + "/subtitles?query=Matrix&languages=en&type=movie",
            self.API_HOSTS,
            {"Api-Key": self.api_key},
        )
        if not isinstance(reply, dict):
            raise SubtitleError("provider_invalid_reply", 502)
        return True

    def search(self, metadata, languages):
        self._require_key()
        requested = []
        for language in languages:
            code = normalized_metadata({}, language)["language"]
            if code not in requested:
                requested.append(code)
        meta = normalized_metadata(metadata, requested[0] if requested else "en")
        if len(meta["title"]) < 2:
            raise SubtitleError("invalid_search", 400)
        base_params = {
            "languages": ",".join(requested or ("en",)),
            "type": "episode" if meta["content_type"] == "series" else "movie",
            "order_by": "download_count",
            "order_direction": "desc",
        }
        if meta["content_type"] == "series":
            if meta["season"]:
                base_params["season_number"] = meta["season"]
            if meta["episode"]:
                base_params["episode_number"] = meta["episode"]

        def collect(data, match_meta):
            if (
                not isinstance(data, dict)
                or not isinstance(data.get("data", []), list)
            ):
                raise SubtitleError("provider_invalid_reply", 502)
            items = data.get("data", [])
            results = []
            seen = set()
            for item in items[:50]:
                if not isinstance(item, dict):
                    continue
                attributes = item.get("attributes")
                if not isinstance(attributes, dict):
                    continue
                details = attributes.get("feature_details")
                details = details if isinstance(details, dict) else {}
                episode_data = {
                    "season": details.get("season_number"),
                    "episode": details.get("episode_number"),
                    "release_name": attributes.get("release"),
                }
                if not _episode_matches(
                    episode_data, meta["season"], meta["episode"]
                ):
                    continue
                if not _opensubtitles_identity_matches(
                    match_meta, attributes, details
                ):
                    continue
                files = attributes.get("files")
                if not isinstance(files, list):
                    continue
                raw_language = str(
                    attributes.get("language") or ""
                ).lower().replace("-", "_")
                language = _LANGUAGE_ALIASES.get(
                    raw_language, raw_language.split("_", 1)[0][:3]
                )
                if language not in requested:
                    continue
                for subtitle_file in files[:50]:
                    if not isinstance(subtitle_file, dict):
                        continue
                    try:
                        file_id = int(subtitle_file.get("file_id"))
                    except (TypeError, ValueError, OverflowError):
                        continue
                    if file_id <= 0 or file_id in seen:
                        continue
                    seen.add(file_id)
                    release = _safe_text(
                        attributes.get("release")
                        or subtitle_file.get("file_name"),
                        limit=240,
                    ) or "Subtitle"
                    results.append({
                        "provider": "opensubtitles",
                        "title": meta["title"],
                        "identity_match": True,
                        "season": meta["season"],
                        "episode": meta["episode"],
                        "release": release,
                        "language": language,
                        "hearing_impaired": _boolean(
                            attributes.get("hearing_impaired", False)
                        ),
                        "fps": _subtitle_fps(
                            subtitle_file, attributes, release
                        ),
                        "file_id": file_id,
                    })
            return len(items), results

        language_priority = {
            language: index for index, language in enumerate(requested)
        }

        def ranked(results):
            results.sort(key=lambda result: (
                language_priority.get(
                    result.get("language"), len(language_priority)
                ),
                bool(result.get("hearing_impaired")),
            ) + _release_match_key(meta, result))
            return results[:15]

        def request_results(extra, stage, match_meta):
            params = dict(base_params)
            params.update(extra)
            _provider_search_log("opensubtitles", stage, meta, requested)
            data = _request(
                self.API_BASE + "/subtitles?" + urlencode(params),
                self.API_HOSTS,
                {"Api-Key": self.api_key},
            )
            raw, results = collect(data, match_meta)
            _provider_search_log(
                "opensubtitles", stage, meta, requested, raw, len(results)
            )
            if raw and not results:
                _provider_candidate_log("opensubtitles", stage, data)
            return ranked(results)

        identifier = None
        if meta["imdb_id"]:
            # OpenSubtitles accepts IMDb identifiers without the public tt
            # prefix used by TMDb and IPTV metadata.
            identifier = ({"imdb_id": meta["imdb_id"][2:]}, "imdb-id")
        elif meta["tmdb_id"]:
            identifier = ({"tmdb_id": meta["tmdb_id"]}, "tmdb-id")
        if identifier is not None:
            results = request_results(identifier[0], identifier[1], meta)
            if results:
                return results

        title_params = {"query": meta["search_title"]}
        if meta["year"]:
            title_params["year"] = meta["year"]
        title_meta = dict(meta)
        # A stale provider identifier is exactly why this fallback exists.
        # Match the fallback response strictly by title/year instead.
        title_meta["imdb_id"] = ""
        title_meta["tmdb_id"] = ""
        results = request_results(title_params, "title", title_meta)
        if results or not meta["year"]:
            return results
        # Some IPTV catalogues expose the festival/streaming year while the
        # provider indexes the theatrical year. Retry once without the year;
        # local exact-title and adjacent-year validation remains mandatory.
        return request_results(
            {"query": meta["search_title"]}, "title-no-year", title_meta
        )

    def download(self, result):
        self._require_key()
        if not isinstance(result, dict) or result.get("provider") != "opensubtitles":
            raise SubtitleError("subtitle_not_found", 404)
        try:
            file_id = int(result.get("file_id"))
        except (TypeError, ValueError, OverflowError):
            file_id = 0
        if file_id <= 0:
            raise SubtitleError("subtitle_not_found", 404)
        authenticated = False
        if self._login_configured():
            try:
                self._login()
                authenticated = True
            except SubtitleError as error:
                # Optional/stale account details must not prevent the API-key
                # route from being attempted. Non-authentication failures are
                # still surfaced immediately.
                if getattr(error, "code", "") != "provider_key_rejected":
                    raise
        try:
            reply = _request(
                (self._base_url if authenticated else self.API_BASE)
                + "/download",
                self.API_HOSTS,
                self._headers(authenticated=authenticated),
                json.dumps({"file_id": file_id, "sub_format": "srt"}).encode(
                    "utf-8"
                ),
            )
        except SubtitleError as error:
            # Search remains usable with an API key alone. Some account or
            # quota policies still require a user token when exchanging a
            # file ID; distinguish that from a rejected API key.
            if (
                not authenticated
                and getattr(error, "code", "") == "provider_key_rejected"
            ):
                raise SubtitleError("provider_login_required", 401)
            raise
        if not isinstance(reply, dict) or not isinstance(reply.get("link"), str):
            raise SubtitleError("provider_invalid_reply", 502)
        return _download(
            "opensubtitles_file",
            {"url": reply["link"]},
            extensions=(".srt", ".vtt"),
        )


class SubSourceClient(object):
    """SubSource's official API, authenticated with an API key only."""

    API_BASE = "https://api.subsource.net/api/v1"
    API_HOSTS = ("api.subsource.net",)
    LANGUAGES = {
        "ar": "arabic", "bg": "bulgarian", "ca": "catalan",
        "cs": "czech", "da": "danish", "de": "german", "el": "greek",
        "en": "english", "es": "spanish", "et": "estonian",
        "fa": "farsi_persian", "fi": "finnish", "fr": "french",
        "fy": "frisian", "gl": "galician", "he": "hebrew",
        "hr": "croatian", "hu": "hungarian", "id": "indonesian",
        "is": "icelandic", "it": "italian", "ku": "kurdish",
        "lt": "lithuanian", "lv": "latvian", "mk": "macedonian",
        "nb": "norwegian", "nl": "dutch", "nn": "norwegian",
        "pl": "polish", "pt": "portuguese", "ro": "romanian",
        "ru": "russian", "sk": "slovak", "sl": "slovenian",
        "sq": "albanian", "sr": "serbian", "sv": "swedish",
        "ta": "tamil", "th": "thai", "tr": "turkish",
        "uk": "ukrainian", "vi": "vietnamese", "zh": "chinese_bg_code",
    }

    def __init__(self, api_key):
        self.api_key = "".join(str(api_key or "").split())[:300]

    def _require_key(self):
        if not self.api_key:
            raise SubtitleError("provider_key_missing", 409)

    def _get(self, path, params):
        self._require_key()
        data = _request(
            self.API_BASE + path + "?" + urlencode(params),
            self.API_HOSTS,
            {"X-API-Key": self.api_key},
        )
        data = _checked_reply(data)
        if data.get("success") is False:
            raise SubtitleError("provider_unavailable", 502)
        rows = data.get("data")
        if isinstance(rows, dict):
            rows = rows.get("items", rows.get("results", rows.get("subtitles")))
        if not isinstance(rows, list):
            raise SubtitleError("provider_invalid_reply", 502)
        return rows

    def test(self):
        self._get("/movies/search", {"searchType": "text", "q": "Matrix"})
        return True

    @staticmethod
    def _movie_matches(meta, movie):
        expected_imdb = _numeric_identifier(meta.get("imdb_id"), imdb=True)
        found_imdb = _numeric_identifier(
            movie.get("imdbId") or movie.get("imdb_id") or movie.get("imdb"),
            imdb=True,
        )
        if expected_imdb and found_imdb:
            if expected_imdb != found_imdb:
                return False
            return meta["content_type"] == "series" or _catalog_year_matches(
                meta["year"], movie.get("releaseYear") or movie.get("year")
            )
        expected_title = _title_identity(meta["search_title"])
        year = _year_number(movie.get("releaseYear") or movie.get("year"))
        for value in (movie.get("title"), movie.get("alternateTitle"), movie.get("name")):
            title = _safe_text(value)
            if meta["content_type"] == "series":
                # Catalogue entries may name the season beside the series.
                title = re.sub(
                    r"(?i)\s*[-:([ ]*\bseason\s+\d{1,2}\s*[)\]]?\s*$", "", title
                ).strip()
            if _title_identity(_subtitle_search_title(title, year)) == expected_title:
                return meta["content_type"] == "series" or _catalog_year_matches(
                    meta["year"], year
                )
        return False

    @staticmethod
    def _release_names(item):
        values = item.get("releaseInfo") or item.get("release") or item.get("name")
        values = values if isinstance(values, list) else [values]
        return [_safe_text(value, limit=240) for value in values[:30]
                if _safe_text(value, limit=240)] or ["Subtitle"]

    @staticmethod
    def _episode_release(item, releases, meta):
        if not meta["season"] or not meta["episode"]:
            return releases[0], False
        explicit = {
            "season": item.get("seasonNumber", item.get("season_number", item.get("season"))),
            "episode": item.get("episodeNumber", item.get("episode_number", item.get("episode"))),
        }
        if explicit["season"] and explicit["episode"]:
            return (releases[0], False) if _episode_matches(
                explicit, meta["season"], meta["episode"]
            ) else ("", False)
        for release in releases:
            if _episode_matches({"release_name": release}, meta["season"], meta["episode"]):
                return release, False
        # A season pack is useful only if the matching episode is selected
        # from its ZIP at download time. Never load the first file blindly.
        for release in releases:
            season = re.search(r"(?i)\b(?:S|season[ ._-]*)(\d{1,2})\b", release)
            has_episode = re.search(r"(?i)\bS\d{1,2}[ ._-]*E\d|\b\d{1,2}x\d", release)
            if season and int(season.group(1)) == meta["season"] and not has_episode:
                return release, True
        return "", False

    @staticmethod
    def _hearing_impaired(item):
        for field in ("hearingImpaired", "hearing_impaired", "hi"):
            if field in item:
                return _boolean(item[field])
        commentary = _safe_text(item.get("commentary"), limit=500).casefold()
        if re.search(r"\b(?:non[ ._-]*hi|non[ ._-]*sdh|(?:hi|sdh)[ ._-]*remove)", commentary):
            return False
        return bool(re.search(r"\b(?:hi|sdh|cc)\b|closed caption", commentary))

    def search(self, metadata, languages):
        self._require_key()
        requested = []
        for language in languages:
            code = normalized_metadata({}, language)["language"]
            if code in self.LANGUAGES and code not in requested:
                requested.append(code)
        if not requested:
            return []
        meta = normalized_metadata(metadata, requested[0])
        if len(meta["search_title"]) < 2:
            raise SubtitleError("invalid_search", 400)

        def movies_for(params, match_meta, stage):
            if meta["content_type"] == "series" and meta["season"]:
                params["season"] = meta["season"]
            rows = self._get("/movies/search", params)
            movies = [movie for movie in rows[:20] if isinstance(movie, dict)
                      and self._movie_matches(match_meta, movie)
                      and _numeric_identifier(movie.get("movieId") or movie.get("id"))][:3]
            _provider_search_log("subsource", stage, meta, requested, len(rows), len(movies))
            return movies

        movies = []
        if meta["imdb_id"]:
            movies = movies_for({"searchType": "imdb", "imdb": meta["imdb_id"]}, meta, "imdb-id")
        if not movies:
            title_meta = dict(meta)
            title_meta["imdb_id"] = ""
            movies = movies_for({"searchType": "text", "q": meta["search_title"]}, title_meta, "title")

        results, seen = [], set()
        language_counts = {language: 0 for language in requested}
        for movie in movies:
            movie_id = _numeric_identifier(movie.get("movieId") or movie.get("id"))
            for language in requested:
                if language_counts[language] >= 20:
                    continue
                params = {"movieId": movie_id, "language": self.LANGUAGES[language], "limit": 100}
                if meta["content_type"] == "series":
                    if meta["season"]:
                        params["seasonNumber"] = meta["season"]
                    if meta["episode"]:
                        params["episodeNumber"] = meta["episode"]
                items = self._get("/subtitles", params)
                accepted = 0
                for item in items[:100]:
                    if not isinstance(item, dict):
                        continue
                    subtitle_id = _numeric_identifier(item.get("subtitleId") or item.get("id"))
                    if not re.fullmatch(r"[1-9]\d{0,17}", subtitle_id) or subtitle_id in seen:
                        continue
                    raw_language = str(item.get("language") or "").strip().lower().replace("-", "_")
                    if raw_language:
                        aliases = {"farsi_persian": "fa", "chinese_bg_code": "zh", "brazilian_portuguese": "pt"}
                        code = aliases.get(raw_language, _LANGUAGE_ALIASES.get(raw_language, raw_language))
                        if code != language and raw_language != self.LANGUAGES[language]:
                            continue
                    release, is_pack = self._episode_release(item, self._release_names(item), meta)
                    if not release:
                        continue
                    seen.add(subtitle_id)
                    accepted += 1
                    language_counts[language] += 1
                    results.append({
                        "provider": "subsource", "title": meta["title"],
                        "identity_match": True, "season": meta["season"],
                        "episode": meta["episode"], "release": release,
                        "language": language, "subtitle_id": subtitle_id,
                        "is_pack": is_pack, "hearing_impaired": self._hearing_impaired(item),
                        "fps": _subtitle_fps(item, movie, release),
                    })
                    if language_counts[language] >= 20:
                        break
                _provider_search_log("subsource", "subtitles", meta, [language], len(items), accepted)
        priority = {language: index for index, language in enumerate(requested)}
        results.sort(key=lambda result: (
            priority.get(result["language"], len(priority)), bool(result["hearing_impaired"]),
        ) + _release_match_key(meta, result))
        return results[:40]

    def download(self, result):
        self._require_key()
        if not isinstance(result, dict) or result.get("provider") != "subsource":
            raise SubtitleError("subtitle_not_found", 404)
        subtitle_id = _numeric_identifier(result.get("subtitle_id"))
        if not re.fullmatch(r"[1-9]\d{0,17}", subtitle_id):
            raise SubtitleError("subtitle_not_found", 404)
        secured = dict(result)
        secured["url"] = self.API_BASE + "/subtitles/{}/download".format(subtitle_id)
        secured["headers"] = {"X-API-Key": self.api_key}
        return _download("subsource", secured, extensions=(".srt", ".vtt"))


def subtitle_client(settings):
    """Construct only the selected provider adapter from private settings."""
    if getattr(settings, "provider", "subdl") == "subsource":
        return SubSourceClient(getattr(settings, "subsource_api_key", ""))
    if getattr(settings, "provider", "subdl") == "opensubtitles":
        return OpenSubtitlesClient(
            getattr(settings, "opensubtitles_api_key", ""),
            getattr(settings, "opensubtitles_username", ""),
            getattr(settings, "opensubtitles_password", ""),
        )
    return SubDLClient(
        getattr(settings, "subdl_api_key", getattr(settings, "api_key", ""))
    )


def save_subtitle_file(content, metadata, result, root="/tmp/gtiptvplayerpro-subtitles"):
    """Keep one downloaded subtitle in a private bounded cache directory."""
    if not isinstance(content, bytes) or len(content) > 2 * 1024 * 1024:
        raise SubtitleError("subtitle_too_large", 422)
    identity = "|".join(
        str(value or "")
        for value in (
            (metadata or {}).get("title"),
            (metadata or {}).get("season"),
            (metadata or {}).get("episode"),
            (result or {}).get("language"),
            (result or {}).get("release"),
        )
    )
    filename = sha256(identity.encode("utf-8", "ignore")).hexdigest() + ".srt"
    try:
        if os.path.islink(root):
            raise OSError("unsafe subtitle cache")
        os.makedirs(root, mode=0o700, exist_ok=True)
        if not os.path.isdir(root) or os.path.islink(root):
            raise OSError("unsafe subtitle cache")
        os.chmod(root, 0o700)
        path = os.path.join(root, filename)
        fd, temporary = tempfile.mkstemp(prefix=".download-", dir=root)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        _prune_subtitle_cache(root, path)
        return path
    except OSError:
        raise SubtitleError("subtitle_cache_unavailable", 500)


def _prune_subtitle_cache(root, current_path):
    """Bound only files created by this module; cache cleanup is best effort."""
    try:
        entries = []
        for entry in os.scandir(root):
            if not re.fullmatch(r"[0-9a-f]{64}\.srt", entry.name):
                continue
            if not entry.is_file(follow_symlinks=False):
                continue
            stat = entry.stat(follow_symlinks=False)
            entries.append((entry.path, stat.st_mtime, stat.st_size))
        entries.sort(
            key=lambda item: (item[0] == current_path, item[1]), reverse=True
        )
        total = 0
        for index, (path, unused_mtime, size) in enumerate(entries):
            total += max(0, int(size))
            if index < MAX_CACHE_FILES and total <= MAX_CACHE_BYTES:
                continue
            if path != current_path:
                try:
                    os.unlink(path)
                except OSError:
                    pass
    except OSError:
        pass

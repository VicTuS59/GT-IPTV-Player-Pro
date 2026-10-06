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
from time import monotonic
from urllib.parse import quote, urlencode, urlsplit

from .diagnostics import log_event
from .subtitle_settings import MAX_OFFSET_MS
from .subtitle_language import (
    LANGUAGE_ALIASES as _LANGUAGE_ALIASES, normalize_subtitle_language,
    text_matches_language, subtitle_episode_numbers, subtitle_episode_matches,
)
from .subtitle_decode import decode_subtitle_content
from .polish_subtitles import POLISH_PROVIDERS, polish_search_providers
from .web_subtitles import SubtitleError, _download, _request


MAX_CUES = 20000
MAX_TEXT_LENGTH = 500
MAX_CACHE_FILES = 16
MAX_CACHE_BYTES = 16 * 1024 * 1024
PROVIDER_NAMES = {
    "subdl": "SubDL",
    "opensubtitles": "OpenSubtitles.com",
    "subsource": "SubSource",
    "napiprojekt": "NapiProjekt",
    "napisy24": "Napisy24",
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
_SUBDL_FRAMERATE_CODES = {
    2: 23.976, 6: 23.980, 5: 24.0, 3: 25.0, 4: 29.970, 7: 30.0,
}


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


def _decode_content(content, language=""):
    return decode_subtitle_content(content, language)


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


def parse_subtitle(content, hearing_impaired=True, language=""):
    """Parse bounded SRT or WebVTT text into sorted, non-empty cues."""
    text = _decode_content(content, language).replace("\r\n", "\n").replace("\r", "\n")
    if len(text.encode("utf-8", "ignore")) > 2 * 1024 * 1024:
        raise SubtitleError("subtitle_too_large", 422)
    blocks = re.split(r"\n[ \t]*\n", text.strip())
    cues = []
    for block in blocks:
        lines = block.split("\n")
        if not lines:
            continue
        first = lines[0].strip().lstrip("\ufeff")
        if (first == "WEBVTT" or first.startswith("WEBVTT ")
                or first in ("STYLE", "REGION")
                or re.match(r"^NOTE(?:$|[ \t])", first)):
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
    if not text_matches_language("\n".join(cue.text for cue in cues), language):
        raise SubtitleError("subtitle_no_results", 404)
    return tuple(cues)


def active_subtitle_text(cues, position_ms, offset_ms=0, starts=None, end_prefix=None):
    """Find the current cues without truncating older overlapping captions."""
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
    # The most recent cue does not bound the lifetime of an older overlapping
    # cue. A prefix maximum excludes expired ranges without a five-cue limit.
    if end_prefix is None:
        latest_end, ends = 0, []
        for cue in cues:
            latest_end = max(latest_end, cue.end_ms)
            ends.append(latest_end)
        end_prefix = ends
    first = bisect_right(end_prefix, target, 0, index + 1)
    active = []
    for candidate in range(index, first - 1, -1):
        cue = cues[candidate]
        if cue.start_ms <= target < cue.end_ms and cue.text not in active:
            active.append(cue.text)
            if len(active) == 2:
                break
    return "\n".join(reversed(active))[:MAX_TEXT_LENGTH]


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
    doubled, canonical = min(_FPS_DOUBLE_STANDARDS,
                             key=lambda pair: abs(number - pair[0]))
    if abs(number - doubled) <= 0.08:
        return canonical
    # Choose the nearest standard instead of the first rate in the table:
    # 24 must remain 24, and 30 must not silently become 29.97.
    standard = min(_FPS_STANDARDS, key=lambda rate: abs(number - rate))
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


_TECH_SOURCE = re.compile(
    r"(?<![a-z0-9])(?:web[ ._-]*dl|web[ ._-]*rip|blu[ ._-]*ray|"
    r"bd[ ._-]*rip|br[ ._-]*rip|hdtv|dvd[ ._-]*rip|dvd)(?![a-z0-9])", re.I
)
_TECH_RESOLUTION = re.compile(r"(?<!\d)(2160|1080|720|576|480)[pi](?!\d)", re.I)
_TECH_CODEC = re.compile(r"(?<![a-z0-9])(?:[xh][ ._-]*26[45]|hevc|avc)(?![a-z0-9])", re.I)
_EDITION_MARKERS = (
    (r"extended(?:[ ._-]+cut)?", "extended"),
    (r"(?:director(?:['’]s|s)?[ ._-]*cut|dc)", "directorscut"),
    (r"final[ ._-]+cut", "finalcut"),
    (r"ultimate[ ._-]+(?:cut|edition)", "ultimatecut"),
    (r"special[ ._-]+edition", "specialedition"),
    (r"unrated", "unrated"), (r"uncut", "uncut"),
    (r"(?:theatrical(?:[ ._-]+(?:cut|edition))?|cinema[ ._-]+(?:cut|edition))", "theatrical"),
    (r"imax", "imax"), (r"remastered", "remastered"),
)
_CUT_MARKERS = frozenset((
    "extended", "directorscut", "finalcut", "ultimatecut",
    "specialedition", "unrated", "uncut", "theatrical",
))
_REVISION_MARKER = re.compile(
    r"(?<![a-z0-9])(?:(repack|proper)(?:[ ._-]*v?[ ._-]*([1-9]\d{0,2}))?"
    r"|v([1-9]\d{0,2}))(?![a-z0-9])", re.I
)
_PART_MARKER = re.compile(
    r"(?<![a-z0-9])(?:cd|disc|disk|part|pt)[ ._-]*([1-9]\d?)"
    r"(?:[ ._-]*(?:of|/)[ ._-]*([1-9]\d?))?(?![a-z0-9])", re.I
)
_GROUP_RESERVED = frozenset(tuple(_LANGUAGE_ALIASES) + tuple(_LANGUAGE_ALIASES.values()) + (
    "dl", "rip", "web", "bluray", "hdtv", "dvd", "multi", "dub", "sub", "subs",
    "srt", "vtt", "hi", "sdh", "forced", "full", "cc", "hdr", "hdr10", "dv",
    "proper", "repack", "extended", "remastered", "theatrical", "unrated",
    "uncut", "dc", "cd1", "cd2", "part1", "part2",
    "h264", "h265", "x264", "x265", "hevc", "avc", "fps", "hls", "vod",
))


def _single_release_profile(value):
    text = _safe_text(value, limit=240).casefold().strip()
    text = re.sub(r"\.(?:srt|vtt|sub|txt|mkv|mp4|avi|ts|webm)$", "", text)
    if _TECH_SOURCE.search(text) and (_TECH_RESOLUTION.search(text) or _TECH_CODEC.search(text)):
        marker = re.search(r"[ ._-]([a-z]{2,3})$", text)
        if marker and normalize_subtitle_language(marker.group(1)) in set(_LANGUAGE_ALIASES.values()):
            text = text[:marker.start()]
    source_match = _TECH_SOURCE.search(text)
    resolution_match = _TECH_RESOLUTION.search(text)
    codec_match = _TECH_CODEC.search(text)
    source = ""
    if source_match:
        marker = re.sub(r"[^a-z0-9]", "", source_match.group(0))
        source = {"webdl": "webdl", "webrip": "webrip", "bluray": "bluray",
                  "bdrip": "bluray", "brrip": "bluray", "hdtv": "hdtv",
                  "dvd": "dvd", "dvdrip": "dvd"}[marker]
    resolution = int(resolution_match.group(1)) if resolution_match else 0
    codec = ""
    if codec_match:
        marker = re.sub(r"[^a-z0-9]", "", codec_match.group(0))
        codec = "h265" if marker in ("x265", "h265", "hevc") else "h264"
    technical = [match.start() for match in (source_match, resolution_match, codec_match) if match]
    prefix = text[:min(technical)].strip(" ._-") if technical else text
    year_match = None
    for match in re.finditer(r"(?<!\d)((?:19|20)\d{2})(?!\d)", prefix):
        # The last release year follows a programme name. Preserve leading
        # and interior title numbers (1917, Blade Runner 2049, 2001, etc.).
        before = re.sub(r"[^\w]", "", prefix[:match.start()])
        tail = prefix[match.end():]
        tail = re.sub(r"(?i)(?<![a-z0-9])S\d{1,2}[ ._-]*E\d{1,3}(?![a-z0-9])|(?<![a-z0-9])\d{1,2}x\d{1,3}(?![a-z0-9])", " ", tail)
        for pattern, unused_name in _EDITION_MARKERS:
            tail = re.sub(r"(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])", " ", tail)
        tail = _REVISION_MARKER.sub(" ", tail)
        tail = _PART_MARKER.sub(" ", tail)
        tail = re.sub(r"(?i)(?<![a-z0-9])(?:amzn|nf|dsnp|hmax|atvp)(?![a-z0-9])", " ", tail)
        if len(before) >= 2 and not re.search(r"[a-z0-9]", tail):
            year_match = match
    def version_context(match):
        # Protect title words: The Final Cut, DC League, Film Part 2, etc.
        return bool(
            (technical and match.start() >= min(technical))
            or (year_match and match.start() > year_match.end())
            or (re.search(r"[\[({]\s*$", text[:match.start()])
                and re.match(r"\s*[\])}]", text[match.end():]))
        )

    editions, edition_matches = [], []
    for pattern, name in _EDITION_MARKERS:
        matches = [
            match for match in re.finditer(
                r"(?<![a-z0-9])(?:" + pattern + r")(?![a-z0-9])", text
            ) if version_context(match)
        ]
        if matches:
            editions.append(name)
            edition_matches.extend(matches)
    revision_matches = [
        match for match in _REVISION_MARKER.finditer(text) if version_context(match)
    ]
    revisions = tuple(sorted(set(
        (match.group(1).lower() + (match.group(2) or ""))
        if match.group(1) else "v" + match.group(3)
        for match in revision_matches
    )))
    part_matches = [
        match for match in _PART_MARKER.finditer(text) if version_context(match)
    ]
    parts = {(int(match.group(1)), int(match.group(2) or 0))
             for match in part_matches}
    part = next(iter(parts)) if len(parts) == 1 else (0, 0)
    group = ""
    match = re.search(r"-([a-z][a-z0-9]{1,24})$", text)
    if match and len(technical) >= 2 and match.group(1) not in _GROUP_RESERVED:
        # The group follows a technical release suffix. A hyphen in a film
        # title, WEB-DL, language tag or a bare year is never a group.
        if any(position < match.start() for position in technical):
            group = match.group(1)
    years = [int(year_match.group(1))] if year_match else []
    spans = [(match.start(), match.end())
             for match in edition_matches + revision_matches + part_matches
             if match.end() <= len(prefix)]
    if year_match:
        spans.append((year_match.start(), year_match.end()))
    for start, end in sorted(set(spans), reverse=True):
        prefix = prefix[:start] + " " + prefix[end:]
    prefix = re.sub(r"(?i)(?<![a-z0-9])S\d{1,2}[ ._-]*E\d{1,3}(?![a-z0-9])|(?<![a-z0-9])\d{1,2}x\d{1,3}(?![a-z0-9])", " ", prefix)
    prefix = re.sub(r"(?i)(?<![a-z0-9])(?:amzn|nf|dsnp|hmax|atvp)(?![a-z0-9])", " ", prefix)
    prefix = re.sub(r"[\[({]\s*[\])}]", " ", prefix)
    # Strip only explicit IPTV language decorations from the title prefix.
    prefix = (_subtitle_search_title(
        re.sub(r"[._]+", " ", prefix), years[0] if years else 0
    ) if prefix and (technical or years or editions or revisions or part[0]) else "")
    platforms = tuple(name for name in ("amzn", "nf", "dsnp", "hmax", "atvp")
                      if technical and re.search(r"(?<![a-z0-9])" + name + r"(?![a-z0-9])", text))
    episode = subtitle_episode_numbers(text)
    # A generic quality label is not a complete release identity. Require a
    # programme name, technical suffix and a distinguishing version marker.
    identity = ""
    if prefix and source and resolution and codec and (group or editions or revisions or platforms):
        identity = " ".join(re.findall(r"[^\W_]+", text, re.UNICODE))
    if len(text) >= 240:
        # The boundary may have cut a distinguishing final token. Keep broad
        # hints, but do not claim exact/group evidence from a clipped label.
        identity, group = "", ""
    if len(parts) > 1:
        identity = ""
    return {"source": source, "resolution": resolution, "codec": codec,
            "editions": tuple(editions), "group": group, "identity": identity,
            "title": prefix, "years": tuple(years), "episode": episode,
            "revisions": revisions, "platforms": platforms, "part": part}


def _release_profile(value):
    """Keep bounded release alternatives instead of joining conflicting hints."""
    raw = _safe_text(value, limit=240)
    variants = [_single_release_profile(part) for part in re.split(r"\s*[|;]\s*", raw)[:8] if part.strip()]
    if not variants:
        variants = [_single_release_profile("")]
    profile = dict(variants[0])
    empty = {"source": "", "resolution": 0, "codec": "", "editions": (),
             "group": "", "revisions": (), "part": (0, 0)}
    for name, missing in empty.items():
        known = [variant[name] for variant in variants if variant[name] != missing]
        profile[name] = known[0] if known and all(value == known[0] for value in known) else missing
    profile["variants"] = tuple(variants)
    return profile


def _release_identity_evidence(meta, target, candidate, verified_identity=False):
    expected_title = _title_identity(meta.get("search_title"))
    pairs = []
    title_conflict = False
    for left in target["variants"]:
        for right in candidate["variants"]:
            left_title = _title_identity(left["title"])
            right_title = _title_identity(right["title"])
            if right_title and expected_title and right_title != expected_title:
                title_conflict = True
            # Returned catalogue IDs can verify a decorated/localized IPTV
            # title. The two actual release titles must still agree before
            # granting full-release or group evidence.
            semantic_match = bool(
                expected_title and left_title == expected_title and right_title == expected_title
                or verified_identity and left_title and left_title == right_title
            )
            same_full = bool(semantic_match and left["identity"] and left["identity"] == right["identity"])
            same_group = bool(semantic_match and left["group"] and left["group"] == right["group"])
            # A group can publish multiple cuts/platforms/revisions. One-sided
            # version details are unknown, not proof of the original edition.
            version_supported = all(left[name] == right[name] for name in
                                    ("editions", "revisions", "platforms", "part"))
            year_supported = not (left["years"] and right["years"]) or left["years"] == right["years"]
            pairs.append((same_full, same_group and version_supported and year_supported))
    full = any(pair[0] for pair in pairs)
    group = any(pair[1] for pair in pairs)
    if any(len({variant["identity"] for variant in profile["variants"] if variant["identity"]}) > 1
           or len({variant["group"] for variant in profile["variants"] if variant["group"]}) > 1
           for profile in (target, candidate)):
        full, group = False, False
    # A matching alternative is sufficient, but a translated title alone is
    # not rejected when the adapter verified the programme by a catalogue ID.
    return full, group, title_conflict and not (full or group)


def _release_profiles_support_match(target, candidate):
    """Check known version hints for a strong match without a release group."""
    missing = {
        "source": "", "resolution": 0, "codec": "", "group": "",
        "editions": (), "revisions": (), "platforms": (), "part": (0, 0),
    }
    for profile in (target, candidate):
        for name, empty in missing.items():
            values = {
                variant[name] for variant in profile["variants"]
                if variant[name] != empty
            }
            if len(values) > 1:
                return False
    # Unknown source/group/platform hints are allowed, but contradictory
    # known hints must not be promoted by a matching catalogue identifier.
    for name in ("source", "resolution", "codec", "group", "platforms"):
        left, right = target[name], candidate[name]
        if left != missing[name] and right != missing[name] and left != right:
            return False
    # A one-sided cut, revision or CD marker remains a version uncertainty.
    return all(target[name] == candidate[name]
               for name in ("editions", "revisions", "part"))


def _subtitle_file_part(result):
    """A single file with cd_number=1 is normally a complete subtitle."""
    number = _number(result.get("cd_number"))
    count = _number(result.get("cd_count") or result.get("nb_cd"))
    number = number if number <= 99 else 0
    count = count if count <= 99 else 0
    filename = _safe_text(result.get("file_name"), limit=240)
    profile = _single_release_profile(filename)
    part = profile["part"]
    if not part[0] and not profile["source"] and not profile["years"]:
        # CD/disc tokens in subtitle filenames are unambiguous even when
        # the uploader omitted the year and technical release suffix.
        match = re.search(
            r"(?<![a-z0-9])(?:cd|disc|disk)[ ._-]*([1-9]\d?)(?![a-z0-9])",
            filename, re.I,
        )
        if match:
            part = (int(match.group(1)), 0)
    if part[0]:
        if number and number != part[0] and (count > 1 or number > 1):
            return number, max(count, number, part[1])
        return part[0], max(part[1], count)
    if count > 1 or number > 1:
        return number, max(count, number)
    return 0, 0


def _informative_subtitle_filename(filename, parent=""):
    profile = _single_release_profile(filename)
    if not profile["title"]:
        return False
    if (profile["source"] or profile["resolution"] or profile["codec"]
            or profile["editions"] or profile["revisions"] or profile["part"][0]):
        return True
    if profile["years"]:
        owner = _single_release_profile(parent)
        # A plain Film.Year.srt supplies no version detail. Preserve the
        # descriptive release when its title/year agree with that filename.
        return bool(
            not owner["title"]
            or _title_identity(profile["title"]) != _title_identity(owner["title"])
            or (owner["years"] and owner["years"] != profile["years"])
        )
    return False


def subtitle_result_release(result, default="Subtitle"):
    """Keep CD labels visible in both result lists without new UI strings."""
    release = _safe_text(result.get("release"), limit=240) or default
    number, count = _subtitle_file_part(result)
    if number or count > 1:
        label = "CD {}/{}".format(number or "?", count) if count > 1 else "CD {}".format(number)
        release = "[{}] {}".format(label, release)
    return release[:240]

def _subtitle_fps(candidate, parent, release, framerate_codes=None):
    for item in (candidate, parent):
        if not isinstance(item, dict):
            continue
        for key in ("fps", "framerate", "frame_rate"):
            value = _fps_number(item.get(key))
            if (not value and key == "framerate" and framerate_codes
                    and not isinstance(item.get(key), bool)):
                # SubDL's framerate is an upload enum, not a decimal FPS.
                # Keep this provider-specific: other adapters use real rates.
                try:
                    code = float(str(item.get(key) or "").strip())
                    value = framerate_codes.get(code, 0.0)
                except (TypeError, ValueError, OverflowError):
                    pass
            if value:
                return value
    return _release_fps(release)


def _release_match_key(meta, result):
    """Use the same release evidence order before every provider result cap."""
    return annotate_subtitle_compatibility(meta, result)["compatibility_sort"]


def subtitle_result_sort_key(result):
    """Stable eight-integer key shared by native and web result lists."""
    value = result.get("compatibility_sort") if isinstance(result, dict) else None
    if isinstance(value, (tuple, list)) and len(value) == 8:
        try:
            return tuple(int(item) for item in value)
        except (TypeError, ValueError, OverflowError):
            pass
    return (0, 4, 2, 2, 2, 2, 10, 0)


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
    """Rank known release evidence without calling missing hints a match."""
    item = dict(result) if isinstance(result, dict) else {}
    language = item.get("language") or "en"
    meta = normalized_metadata(metadata, language)
    target = _release_profile(meta.get("release_hint"))
    filename = _safe_text(item.get("file_name"), limit=240)
    candidate = _release_profile(
        filename if _informative_subtitle_filename(
            filename, item.get("release_parent") or item.get("release")
        )
        else item.get("release")
    )
    file_part = _subtitle_file_part(item)
    if file_part != (0, 0):
        candidate["part"] = file_part
        candidate["variants"] = tuple(
            dict(variant, part=file_part) for variant in candidate["variants"]
        )
    identity_mismatch = item.get("identity_match") is False
    identifier_matches = []
    if meta["content_type"] != "series":
        for field, imdb in (("imdb_id", True), ("tmdb_id", False)):
            expected = _numeric_identifier(meta.get(field), imdb=imdb)
            found = _numeric_identifier(item.get(field), imdb=imdb)
            if expected and found:
                identifier_matches.append(expected == found)
        if identifier_matches and not all(identifier_matches):
            identity_mismatch = True
    reliable_same_id = bool(identifier_matches and all(identifier_matches))
    same_full, same_group, release_title_conflict = _release_identity_evidence(
        meta, target, candidate, verified_identity=reliable_same_id,
    )
    expected_year = _year_number(meta.get("year"))
    found_year = _year_number(item.get("year") or item.get("release_year"))
    release_years = {year for variant in candidate["variants"] for year in variant["years"]}
    if meta["content_type"] != "series" and expected_year and not reliable_same_id:
        if found_year and not _catalog_year_matches(expected_year, found_year):
            identity_mismatch = True
        elif not found_year and release_years and not any(_catalog_year_matches(expected_year, year) for year in release_years):
            identity_mismatch = True
    expected_season, expected_episode = _number(meta.get("season")), _number(meta.get("episode"))
    found_season, found_episode = _number(item.get("season")), _number(item.get("episode"))
    parsed_episodes = {variant["episode"] for variant in candidate["variants"] if variant["episode"]}
    if expected_season and expected_episode:
        expected_pair = (expected_season, expected_episode)
        if found_season and found_episode and (found_season, found_episode) != expected_pair:
            identity_mismatch = True
        if parsed_episodes and expected_pair not in parsed_episodes:
            identity_mismatch = True
    expected_title = _title_identity(meta.get("search_title"))
    declared_title = _safe_text(item.get("title"))
    declared_title_conflict = bool(declared_title and expected_title and
        _title_identity(_subtitle_search_title(declared_title, found_year or expected_year)) != expected_title)
    title_uncertain = (release_title_conflict or declared_title_conflict) and not reliable_same_id

    # Adapters may copy the query title/episode into the result. Strong
    # identity support must come from returned IDs or the actual release.
    release_title_matches = bool(expected_title and any(
        _title_identity(variant["title"]) == expected_title
        for variant in candidate["variants"]
    ))
    if meta["content_type"] == "series":
        identity_supported = bool(
            release_title_matches and expected_season and expected_episode
            and parsed_episodes == {(expected_season, expected_episode)}
        )
    else:
        known_years = release_years | ({found_year} if found_year else set())
        identity_supported = bool(reliable_same_id or (
            release_title_matches and expected_year and known_years
            and all(_catalog_year_matches(expected_year, year)
                    for year in known_years)
        ))

    target_source, candidate_source = target["source"], candidate["source"]
    source_state = ("match" if target_source == candidate_source else "mismatch") if target_source and candidate_source else "unknown"
    target_editions, candidate_editions = set(target["editions"]), set(candidate["editions"])
    target_cuts, candidate_cuts = target_editions & _CUT_MARKERS, candidate_editions & _CUT_MARKERS
    if target_cuts and candidate_cuts:
        edition_state = ("match" if target_cuts == candidate_cuts
                         else "unknown" if target_cuts & candidate_cuts
                         else "mismatch")
    elif target_editions and target_editions == candidate_editions:
        edition_state = "match"
    else:
        # An absent cut marker does not establish the theatrical version.
        edition_state = "unknown"
    target_revision, candidate_revision = target["revisions"], candidate["revisions"]
    revision_state = "unknown"
    if target_revision and target_revision == candidate_revision:
        revision_state = "match"
    elif target_revision and candidate_revision:
        def revision_numbers(values):
            output = {}
            for value in values:
                match = re.fullmatch(r"(repack|proper|v)(\d*)", value)
                if match and match.group(2):
                    output[match.group(1)] = match.group(2)
            return output
        left, right = revision_numbers(target_revision), revision_numbers(candidate_revision)
        if any(left[name] != right[name] for name in left.keys() & right.keys()):
            revision_state = "mismatch"
    target_part, candidate_part = target["part"], candidate["part"]
    part_state = "unknown"
    if target_part != (0, 0) and candidate_part != (0, 0):
        if target_part[0] and candidate_part[0]:
            part_state = "match" if target_part[0] == candidate_part[0] else "mismatch"
        if target_part[1] and candidate_part[1] and target_part[1] != candidate_part[1]:
            part_state = "mismatch"
    elif candidate_part != (0, 0):
        # A CD/part subtitle must not be advertised for the complete feature.
        part_state = "mismatch"

    video_fps = canonical_subtitle_fps(meta.get("fps"))
    subtitle_fps = canonical_subtitle_fps(item.get("fps") or _release_fps(item.get("release")))
    fps_scale = subtitle_fps_scale(video_fps, subtitle_fps)
    if video_fps and subtitle_fps:
        fps_state = "match" if abs(video_fps - subtitle_fps) <= 0.01 else "convert" if fps_scale else "mismatch"
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
    # The final spoken cue can precede credits by minutes. Plausible duration
    # is only a sanity check, never evidence for a high release match.
    target_resolution = _normalise_resolution(meta.get("resolution")) or target["resolution"]
    candidate_resolution = candidate["resolution"]
    resolution_state = ("match" if target_resolution == candidate_resolution else "mismatch") if target_resolution and candidate_resolution else "unknown"
    codec_state = ("match" if target["codec"] == candidate["codec"] else "mismatch") if target["codec"] and candidate["codec"] else "unknown"
    # A decoded/container FPS and an uploader's release FPS can differ while
    # timestamped captions still follow the same movie timeline. Strong
    # release evidence must not be discarded because of that hint alone.
    supported_group = bool(same_group and source_state == "match" and
                           (resolution_state == "match" or codec_state == "match" or edition_state == "match"))
    version_matches = sum(state == "match" for state in (
        source_state, resolution_state, codec_state,
    ))
    supported_profile = bool(
        identity_supported and not title_uncertain
        and (fps_state == "match" or version_matches >= 2)
        and all(state != "mismatch" for state in (
            source_state, resolution_state, codec_state,
        ))
        and any(state == "match" for state in (
            source_state, resolution_state, codec_state,
            edition_state, revision_state,
        ))
        and _release_profiles_support_match(target, candidate)
    )
    if title_uncertain:
        same_full, supported_group = False, False
    different = bool(identity_mismatch or edition_state == "mismatch"
                     or revision_state == "mismatch" or part_state == "mismatch"
                     or duration_state == "mismatch")
    evidence = any(state != "unknown" for state in (
        source_state, edition_state, revision_state, part_state,
        fps_state, resolution_state, codec_state,
    ))
    # Preserve concrete release/group evidence ahead of inferred profiles.
    # Within each tier, FPS agreement is useful positive evidence even though
    # disagreement alone must never label timestamped captions incompatible.
    release_strength = (0 if same_full else 1 if supported_group
                        else 2 if supported_profile else 3 if evidence else 4)
    score = 50
    score += {"match": 12, "mismatch": -8}.get(source_state, 0)
    score += {"match": 12, "mismatch": -35}.get(edition_state, 0)
    score += {"match": 3, "mismatch": -15}.get(revision_state, 0)
    score += {"mismatch": -35}.get(part_state, 0)
    score += {"match": 15}.get(fps_state, 0)
    score += {"mismatch": -20}.get(duration_state, 0)
    score += {"match": 3, "mismatch": -1}.get(resolution_state, 0)
    score += {"match": 2, "mismatch": -1}.get(codec_state, 0)
    score += 25 if same_full else 5 if supported_group else 0
    if title_uncertain:
        score = min(score, 60)
    if identity_mismatch:
        score = 0
    score = max(0, min(100, int(score)))
    if different:
        status = "different"
    elif fps_state == "convert" and item.get("fps_conversion_verified") is True:
        status = "fps_convert"
    elif same_full or supported_group or supported_profile:
        # Exact releases and supported groups take priority over FPS hints.
        # Without those, require verified identity and either matching FPS
        # with another hint, or two agreeing technical release details.
        status = "high"
    elif evidence:
        status = "possible"
    else:
        status = "unknown"
    state_rank = {"match": 0, "convert": 1, "unknown": 2, "mismatch": 3}
    item.update({
        "identity_match": not identity_mismatch,
        "compatibility_status": status,
        "compatibility_score": score,
        "video_fps": video_fps,
        "subtitle_fps": subtitle_fps,
        "compatibility_fps_state": fps_state,
        "fps_scale": fps_scale if status == "fps_convert" else 1.0,
        "fps_scale_hint": fps_scale if fps_state == "convert" else 1.0,
        "video_duration_seconds": video_duration,
        "compatibility_sort": (
            1 if status == "different" else 0,
            release_strength,
            state_rank[edition_state],
            0 if fps_state == "match" else 1 if (
                fps_state == "convert" and item.get("fps_conversion_verified") is True
            ) else 2,
            state_rank[source_state],
            state_rank[duration_state],
            state_rank[resolution_state] * 4 + state_rank[codec_state],
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
    # Keep technical details from the original IPTV name before removing
    # decorations from the provider query. Explicit hints remain first.
    hint = _safe_text(metadata.get("release_hint"), limit=240)
    raw_title_hint = _safe_text(title, limit=120)
    if raw_title_hint and raw_title_hint.casefold() not in hint.casefold():
        hint = " | ".join(value for value in (hint, raw_title_hint) if value)[:240]
    raw_profile = _single_release_profile(raw_title_hint)
    if raw_profile["title"] and raw_profile["source"] and raw_profile["resolution"] and raw_profile["codec"]:
        search_title = raw_profile["title"]
        year = year or next(iter(raw_profile["years"]), 0)
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
        "release_hint": hint,
        "fps": _fps_number(metadata.get("fps")) or _release_fps(hint),
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


def subtitle_search_available(metadata):
    """Accept a short film name when a valid catalogue key can resolve it."""
    meta = normalized_metadata(metadata, "en")
    return bool(meta["title"] and (len(meta["search_title"]) >= 2
                or meta["imdb_id"] or meta["tmdb_id"] or meta["sd_id"]))


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
    return subtitle_episode_matches(item, season, episode)


def _numeric_identifier(value, imdb=False):
    value = str(value or "").strip().lower()
    if imdb and value.startswith("tt"):
        value = value[2:]
    # APIs often expose IMDb IDs as integers (111161), while catalogues use
    # their zero-padded public form (tt0111161). Compare numerical identity.
    return (value.lstrip("0") or "0") if value.isdigit() else ""


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
    # The subtitle array belongs to the leading catalogue result. Finding
    # the requested film later in the list cannot validate another film's
    # subtitles; title resolution must query that film's own sd_id instead.
    for item in entries[:1]:
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
            # A translated title needs supporting year/ID evidence. Keep
            # rejecting conflicting catalogue IDs or an unverified name.
            if (has_candidate_title and not title_matches
                    and not (len(identifier_checks) >= 2
                             or (expected_year and candidate_year == expected_year))):
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
        expected_year = _year_number(meta.get("year"))
        if (has_candidate_title and not title_matches
                and not (len(identifier_checks) >= 2
                         or (expected_year and candidate_year == expected_year))):
            return False
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
    raw = normalize_subtitle_language(
        candidate.get("language")
        or candidate.get("lang")
        or parent.get("language")
        or parent.get("lang")
        or ""
    )
    return raw if raw in requested else ""


def _boolean(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _subdl_release_names(candidate, parent):
    """Read release alternatives without replacing a specific unpacked file."""
    filename = _safe_text(candidate.get("name"), limit=240)
    own_release = _safe_text(candidate.get("release_name"), limit=240)
    parent_release = _safe_text(parent.get("release_name"), limit=240)
    if candidate is not parent:
        for value in (filename, own_release):
            if (_informative_subtitle_filename(value, parent_release)
                    or subtitle_episode_numbers(value)):
                # A per-episode/CD filename is more specific than its pack's
                # generic release list. Never borrow another file's version.
                return [value]
    for item in (candidate, parent):
        values = item.get("releases")
        values = values if isinstance(values, (list, tuple)) else [values]
        names = []
        for value in values[:30]:
            if isinstance(value, dict):
                value = value.get("release_name") or value.get("name")
            value = _safe_text(value, limit=240)
            if value and value not in names:
                names.append(value)
        if names:
            descriptive = [value for value in names if (
                _informative_subtitle_filename(value)
                or subtitle_episode_numbers(value)
            )]
            return descriptive or names
    release = next((value for value in (own_release, filename)
                    if _informative_subtitle_filename(value, parent_release)
                    or subtitle_episode_numbers(value)), "")
    release = release or parent_release or own_release or filename or "Subtitle"
    return [release]


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
        if not subtitle_search_available(meta):
            raise SubtitleError("invalid_search", 400)
        base_params = {
            "type": "tv" if meta["content_type"] == "series" else "movie",
            "languages": ",".join(requested or ("en",)),
            "subs_per_page": 30,
            "unpack": 1,
            "releases": 1,
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
            catalog = data.get("results") or []
            owner = catalog[0] if isinstance(catalog, list) and catalog and isinstance(catalog[0], dict) else {}
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
                    if not isinstance(candidate, dict):
                        continue
                    language = _language_code(candidate, parent, requested)
                    if not language:
                        continue
                    url = _download_url(candidate, parent)
                    if not url:
                        continue
                    result = {
                        "provider": "subdl",
                        "title": meta["title"],
                        "identity_match": True,
                        "imdb_id": _numeric_identifier(owner.get("imdb_id"), imdb=True),
                        "tmdb_id": _numeric_identifier(owner.get("tmdb_id")),
                        "year": _year_number(owner.get("year") or owner.get("release_year")),
                        "season": meta["season"],
                        "episode": meta["episode"],
                        "file_name": _safe_text(candidate.get("name"), limit=240),
                        "language": language,
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
                        "url": url,
                    }
                    alternatives = []
                    for release in _subdl_release_names(candidate, parent):
                        episode_data = dict(candidate, release_name=release)
                        if not _episode_matches(
                            episode_data, meta["season"], meta["episode"]
                        ):
                            continue
                        parsed_episode = subtitle_episode_numbers(release)
                        if (meta["season"] and meta["episode"] and parsed_episode
                                and parsed_episode != (meta["season"], meta["episode"])):
                            continue
                        option = dict(result, release=release, fps=_subtitle_fps(
                            candidate, parent, release,
                            framerate_codes=_SUBDL_FRAMERATE_CODES,
                        ))
                        alternatives.append(option)
                    if not alternatives:
                        continue
                    result = min(alternatives, key=lambda option: _release_match_key(meta, option))
                    identity = (url, result["release"], language)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    results.append(result)
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
            ) + _release_match_key(meta, result) + (
                bool(result.get("hearing_impaired")),
            ))
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

        if len(meta["search_title"]) < 2:
            return []

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
    MAX_SEARCH_PAGES = 3
    MAX_SEARCH_REQUESTS = 6
    SEARCH_BUDGET_SECONDS = 18
    RESULTS_PER_LANGUAGE = 20

    def __init__(self, api_key, username="", password="", hearing_impaired=True):
        self.api_key = "".join(str(api_key or "").split())[:300]
        self.username = str(username or "").strip()[:160]
        self.password = str(password or "")[:300]
        self._token = ""
        self._base_url = self.API_BASE
        self.hearing_impaired = bool(hearing_impaired)

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
        if not subtitle_search_available(meta):
            raise SubtitleError("invalid_search", 400)
        search_started = monotonic()
        request_count = 0
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
            # The API can return 60 entries per page. Rank the complete
            # bounded page before applying the result quota.
            for item in items[:100]:
                if not isinstance(item, dict):
                    continue
                attributes = item.get("attributes")
                if not isinstance(attributes, dict):
                    continue
                hearing_impaired = _boolean(attributes.get("hearing_impaired", False))
                if hearing_impaired and not self.hearing_impaired:
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
                cd_numbers = {_number(entry.get("cd_number")) for entry in files[:50]
                              if isinstance(entry, dict)}
                cd_count = _number(attributes.get("nb_cd"))
                if len(cd_numbers - {0}) > 1:
                    cd_count = max(cd_count, max(cd_numbers))
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
                    filename = _safe_text(subtitle_file.get("file_name"), limit=240)
                    filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
                    parent_release = _safe_text(attributes.get("release"), limit=240)
                    release = (
                        filename if _informative_subtitle_filename(filename, parent_release)
                        else parent_release or filename
                    ) or "Subtitle"
                    results.append({
                        "provider": "opensubtitles",
                        "title": meta["title"],
                        "identity_match": True,
                        "imdb_id": _numeric_identifier(details.get("imdb_id") or attributes.get("imdb_id"), imdb=True),
                        "tmdb_id": _numeric_identifier(details.get("tmdb_id") or attributes.get("tmdb_id")),
                        "year": _year_number(details.get("year") or details.get("release_year") or attributes.get("year")),
                        "season": meta["season"],
                        "episode": meta["episode"],
                        "release": release,
                        "release_parent": parent_release,
                        "file_name": filename,
                        "cd_number": _number(subtitle_file.get("cd_number")),
                        "cd_count": cd_count,
                        "language": language,
                        "hearing_impaired": hearing_impaired,
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
            ) + _release_match_key(meta, result) + (
                bool(result.get("hearing_impaired")),
            ))
            output, counts = [], {}
            for result in results:
                language = result["language"]
                if counts.get(language, 0) < self.RESULTS_PER_LANGUAGE:
                    output.append(result)
                    counts[language] = counts.get(language, 0) + 1
            return output

        def request_results(extra, stage, match_meta):
            nonlocal request_count
            results, seen = [], set()
            for page in range(1, self.MAX_SEARCH_PAGES + 1):
                remaining = self.SEARCH_BUDGET_SECONDS - (monotonic() - search_started)
                if request_count >= self.MAX_SEARCH_REQUESTS or remaining <= 0:
                    break
                params = dict(base_params)
                params.update(extra)
                if page > 1:
                    params["page"] = page
                _provider_search_log("opensubtitles", stage, meta, requested)
                request_count += 1
                try:
                    data = _request(
                        self.API_BASE + "/subtitles?" + urlencode(params),
                        self.API_HOSTS, {"Api-Key": self.api_key},
                        timeout=min(6, remaining),
                    )
                    raw, entries = collect(data, match_meta)
                except SubtitleError:
                    # An unavailable extra page must not erase good results.
                    if not results:
                        raise
                    break
                _provider_search_log(
                    "opensubtitles", stage, meta, requested, raw, len(entries)
                )
                if raw and not entries:
                    _provider_candidate_log("opensubtitles", stage, data)
                fresh = [entry for entry in entries if entry["file_id"] not in seen]
                seen.update(entry["file_id"] for entry in fresh)
                results.extend(fresh)
                # Stop when pagination is absent, empty, inconsistent, or
                # repeats a page. Never follow provider-supplied URLs.
                total_pages = _number(data.get("total_pages"))
                reported_page = _number(data.get("page"))
                if (not raw or page >= total_pages
                        or (reported_page and reported_page != page)
                        or (page > 1 and entries and not fresh)):
                    break
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

        if len(meta["search_title"]) < 2:
            return []

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

    def __init__(self, api_key, hearing_impaired=True):
        self.api_key = "".join(str(api_key or "").split())[:300]
        self.hearing_impaired = bool(hearing_impaired)

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
            season = re.search(r"(?i)(?<![a-z0-9])(?:S|season[ ._-]*)(\d{1,2})(?![a-z0-9])", release)
            has_episode = bool(subtitle_episode_numbers(release))
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
        if len(meta["search_title"]) < 2 and not meta["imdb_id"]:
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
        if not movies and len(meta["search_title"]) >= 2:
            title_meta = dict(meta)
            title_meta["imdb_id"] = ""
            movies = movies_for({"searchType": "text", "q": meta["search_title"]}, title_meta, "title")

        results, seen = [], set()
        language_counts = {language: 0 for language in requested}
        for movie in movies:
            movie_id = _numeric_identifier(movie.get("movieId") or movie.get("id"))
            for language in requested:
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
                    if self._hearing_impaired(item) and not self.hearing_impaired:
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
                    alternatives = []
                    for release_name in self._release_names(item):
                        release_value, pack = self._episode_release(item, [release_name], meta)
                        if release_value:
                            evidence = {
                                "provider": "subsource", "title": meta["title"],
                                "identity_match": True, "season": meta["season"],
                                "episode": meta["episode"], "release": release_value,
                                "language": language,
                                "fps": _subtitle_fps(item, movie, release_value),
                            }
                            # An exact episode takes precedence over a pack;
                            # every compatible release of this file is ranked.
                            alternatives.append((pack, _release_match_key(meta, evidence),
                                                 release_value))
                    if not alternatives:
                        continue
                    is_pack, unused_rank, release = min(alternatives)
                    if not release:
                        continue
                    seen.add(subtitle_id)
                    accepted += 1
                    language_counts[language] += 1
                    results.append({
                        "provider": "subsource", "title": meta["title"],
                        "identity_match": True, "season": meta["season"],
                        "episode": meta["episode"], "release": release,
                        "imdb_id": _numeric_identifier(movie.get("imdbId") or movie.get("imdb_id") or movie.get("imdb"), imdb=True),
                        "year": _year_number(movie.get("releaseYear") or movie.get("year")),
                        "language": language, "subtitle_id": subtitle_id,
                        "is_pack": is_pack, "hearing_impaired": self._hearing_impaired(item),
                        "fps": _subtitle_fps(item, movie, release),
                    })
                _provider_search_log("subsource", "subtitles", meta, [language], len(items), accepted)
        priority = {language: index for index, language in enumerate(requested)}
        results.sort(key=lambda result: (
            priority.get(result["language"], len(priority)),
        ) + _release_match_key(meta, result) + (
            bool(result["hearing_impaired"]),
        ))
        ranked, counts = [], {}
        for result in results:
            language = result["language"]
            if counts.get(language, 0) < 20:
                ranked.append(result)
                counts[language] = counts.get(language, 0) + 1
        return ranked[:40]

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


def subtitle_client(settings, provider=None):
    """Construct only the selected provider adapter from private settings."""
    provider = provider or getattr(settings, "provider", "subdl")
    if provider in POLISH_PROVIDERS and not polish_search_providers(
        getattr(settings, "primary_language", "")
    ):
        raise SubtitleError("invalid_provider", 400)
    if provider == "napiprojekt":
        from .polish_subtitles import NapiProjektClient
        return NapiProjektClient()
    if provider == "napisy24":
        from .polish_subtitles import Napisy24Client
        return Napisy24Client()
    if provider not in ("subdl", "subsource", "opensubtitles"):
        raise SubtitleError("invalid_provider", 400)
    if provider == "subsource":
        return SubSourceClient(
            getattr(settings, "subsource_api_key", ""),
            hearing_impaired=getattr(settings, "hearing_impaired", False),
        )
    if provider == "opensubtitles":
        return OpenSubtitlesClient(
            getattr(settings, "opensubtitles_api_key", ""),
            getattr(settings, "opensubtitles_username", ""),
            getattr(settings, "opensubtitles_password", ""),
            hearing_impaired=getattr(settings, "hearing_impaired", False),
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
            sha256(content).hexdigest(),
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

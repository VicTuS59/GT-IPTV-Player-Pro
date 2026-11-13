# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-independent parsing of provider labels, title numbers and release dates."""

from datetime import date
import re

from .subtitle_language import LANGUAGE_ALIASES as _LANGUAGE_ALIASES
from .subtitle_match_model import score_title_year


_TITLE_YEAR = r"(?:19|20)[0-9]{2}"
_TITLE_DATE = (
    rf"(?:{_TITLE_YEAR}[./-][0-9]{{1,2}}[./-][0-9]{{1,2}}|"
    rf"[0-9]{{1,2}}[./-][0-9]{{1,2}}[./-]{_TITLE_YEAR})"
)
_TITLE_DATE_LABEL = re.compile(rf"{_TITLE_DATE}|{_TITLE_YEAR}")
_TITLE_PREFIX_SEPARATOR = r"([|¦•┃:–—]|-(?=\s)|(?<=\s)-)"
_TITLE_DATE_PREFIX = re.compile(rf"^({_TITLE_DATE_LABEL.pattern})\s*{_TITLE_PREFIX_SEPARATOR}\s*")
_TITLE_DATE_SUFFIX = re.compile(
    rf"(?:\s+-\s*|[|¦•┃–—]\s*)({_TITLE_DATE_LABEL.pattern})$|\s+({_TITLE_DATE})$"
)
_TITLE_BARE_YEAR = re.compile(rf"\s+({_TITLE_YEAR})$")
_TITLE_CURRENT_YEAR = max(2026, date.today().year)
_TITLE_TAG_SUFFIX = re.compile(
    r"\s*[\(\[\{]\s*([^\(\)\[\]\{\}]{1,24})\s*[\)\]\}]\s*$"
)
_TITLE_DELIMITED_TAG_SUFFIX = re.compile(
    r"\s*[|¦•┃]\s*([^|¦•┃]{1,24})\s*$"
)
_TITLE_TAG_PREFIX = re.compile(
    r"^\s*[\(\[\{]\s*([^\(\)\[\]\{\}]{1,24})\s*[\)\]\}]\s*"
)
_TITLE_DELIMITED_TAG_PREFIX = re.compile(
    rf"^(?:[|¦┃]\s*)?([^\[\](){{}}|¦•┃:–—-]{{1,24}}?)\s*{_TITLE_PREFIX_SEPARATOR}\s*"
)
_TITLE_MULTI_CODE_PREFIX = re.compile(
    r"^((?:DE|NL)(?:\s*[/+-]\s*|\s+)(?:DE|NL))(?=\s|[|¦•┃:–—-])\s*"
)
_TITLE_BARE_COUNTRY_PREFIX = re.compile(r"^(?:DE|NL)\s+")
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


def year_number(value):
    try:
        direct = int(value or 0)
    except (TypeError, ValueError, OverflowError):
        direct = 0
    if 1900 <= direct <= 2099:
        return direct
    match = re.search(r"(?<!\d)((?:19|20)\d{2})(?!\d)", str(value or ""))
    return int(match.group(1)) if match is not None else 0


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


def _provider_title_tag(value):
    if _title_tag_key(value) in _TITLE_PROVIDER_TAGS:
        return True
    parts = re.split(r"[\s,+/-]+", value.strip())
    return len(parts) > 1 and all(_title_tag_key(part) in _TITLE_PROVIDER_TAGS for part in parts)


def _title_label_year(value):
    if not _TITLE_DATE_LABEL.fullmatch(value):
        return 0
    if len(value) == 4:
        return int(value)
    first, middle, last = re.split(r"[./-]", value)
    if len(first) == 4:
        year, month, day = int(first), int(middle), int(last)
        candidates = ((month, day),)
    else:
        year, month, day = int(last), int(middle), int(first)
        candidates = ((month, day), (day, month))
    for month, day in candidates:
        try:
            date(year, month, day)
        except ValueError:
            continue
        return year
    return 0


def _bare_year_options(title, year, release_suffix):
    full = (title, year)
    match = _TITLE_BARE_YEAR.search(title)
    if not match:
        return (full,)
    number = int(match[1])
    if (year and number != year) or (not year and number > _TITLE_CURRENT_YEAR + 1):
        return (full,)
    stem = title[:match.start()].rstrip(" -|:/")
    # Preserve phrases such as "Class of 1984", including in release labels.
    if not stem or re.search(r"(?i)\b(?:of|in|from|since|de|van|von)$", stem):
        return (full,)
    split = (stem, number)
    score = score_title_year(
        known_year=bool(year),
        recent_year=number >= _TITLE_CURRENT_YEAR - 12,
        release_suffix=release_suffix,
        preceding_number=bool(re.search(r"(?:^|\s)[0-9]{1,4}$", stem)),
    )
    return (split, full) if score > 0 else (full, split)


def title_options(value, year=0, *, bare_year=True, limit=120):
    """Remove explicit catalogue labels and rank ambiguous title/year splits."""
    original = " ".join(str(value or "").split())[:limit]
    title = original
    expected_year = year_number(year)
    marked_year = 0
    release_suffix_removed = False
    for _ in range(12):
        before = title
        for pattern in (_TITLE_TAG_PREFIX, _TITLE_DATE_PREFIX,
                        _TITLE_DELIMITED_TAG_PREFIX, _TITLE_MULTI_CODE_PREFIX):
            match = pattern.match(title)
            if not match:
                continue
            label = match[1].strip()
            found_year = _title_label_year(label)
            if pattern in (_TITLE_DATE_PREFIX, _TITLE_DELIMITED_TAG_PREFIX) and match[2] == ":":
                # Keep title words in "It: Chapter Two" and "2001: A Space Odyssey".
                if label.casefold() == "it" or (found_year and len(label) == 4 and found_year != expected_year):
                    continue
            rest = title[match.end():].lstrip(" -–—|¦•┃:/")
            if found_year and pattern is not _TITLE_TAG_PREFIX and rest.isdecimal():
                other_year = _title_label_year(rest)
                if other_year and (expected_year == other_year or not expected_year
                                   and other_year <= _TITLE_CURRENT_YEAR + 1
                                   and (found_year > _TITLE_CURRENT_YEAR + 1
                                        or found_year < _TITLE_CURRENT_YEAR - 12 <= other_year)):
                    continue
            if rest and (found_year or _provider_title_tag(label)):
                title = rest
                marked_year = marked_year or found_year
                break
        if title != before:
            continue

        match = _TITLE_PLAIN_TAG_SUFFIX.search(title)
        if match:
            tag = _plain_release_tag(match[1])
            stem = title[:match.start()].rstrip(" -|:/")
            bracket = _TITLE_TAG_SUFFIX.search(stem) if tag == "multi" else None
            if stem and tag and (tag != "multi" or release_suffix_removed
                                or _TITLE_DATE_SUFFIX.search(stem) or _TITLE_BARE_YEAR.search(stem)
                                or (bracket and _title_label_year(bracket[1].strip()))):
                title = stem
                release_suffix_removed = True

        for pattern in (_TITLE_TAG_SUFFIX, _TITLE_DATE_SUFFIX, _TITLE_DELIMITED_TAG_SUFFIX):
            match = pattern.search(title)
            if not match:
                continue
            label = (match[1] or match[2]).strip() if pattern is _TITLE_DATE_SUFFIX else match[1].strip()
            found_year = _title_label_year(label)
            stem = title[:match.start()].rstrip(" -–—|¦•┃:/")
            if stem and (found_year or _provider_title_tag(label)):
                title = stem
                marked_year = marked_year or found_year
                break
        if title == before:
            break

    if not any(character.isalnum() for character in title):
        return ((original, expected_year),)
    year = expected_year or marked_year
    if marked_year or not bare_year:
        options = ((title, year),)
    else:
        options = _bare_year_options(title, year, release_suffix_removed)
    # Without a delimiter DE can also be the Dutch article. Try removing an
    # uppercase bare code only after the full title, with a year to validate it.
    country = _TITLE_BARE_COUNTRY_PREFIX.match(options[0][0])
    if bare_year and country:
        for candidate, candidate_year in options:
            if candidate_year and len(candidate) > country.end():
                return (options[0], (candidate[country.end():], candidate_year))
    return options

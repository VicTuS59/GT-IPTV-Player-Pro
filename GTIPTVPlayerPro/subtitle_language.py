# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Language labels shared by subtitle searches and archive selection."""

import os
import re

LANGUAGE_ALIASES = {
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


def normalize_subtitle_language(value):
    """Normalize a declared language without assigning a requested fallback."""
    raw = str(value or "").strip().lower().replace("-", "_")
    code = LANGUAGE_ALIASES.get(raw, raw.split("_", 1)[0])
    return code if re.fullmatch(r"[a-z]{2,3}", code) else ""


_EPISODE_MARKER = re.compile(
    r"(?<![a-z0-9])S(\d{1,2})[ ._-]*E(\d{1,3})(?![a-z0-9])|"
    r"(?<![a-z0-9])(\d{1,2})x(\d{1,3})(?![a-z0-9])", re.I
)


def subtitle_episode_numbers(value):
    """Read one bounded explicit S/E marker, including underscore releases."""
    match = _EPISODE_MARKER.search(str(value or "")[:512])
    if match is None:
        return ()
    parts = match.group(1, 2) if match.group(1) else match.group(3, 4)
    return tuple(int(part) for part in parts)


def _episode_number(value):
    try:
        number = int(value or 0)
        return number if 0 <= number <= 999 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def subtitle_episode_matches(item, season, episode):
    """Share provider and archive S/E checks without guessing unknown episodes."""
    season, episode = _episode_number(season), _episode_number(episode)
    if not season or not episode:
        return True
    item = item if isinstance(item, dict) else {}
    found_season = _episode_number(item.get("season", item.get("season_number", item.get("seasonNumber"))))
    found_episode = _episode_number(item.get("episode", item.get("episode_number", item.get("episodeNumber"))))
    if found_season and found_episode:
        return (found_season, found_episode) == (season, episode)
    release = (item.get("release_name") or item.get("releaseInfo") or
               item.get("release") or item.get("name") or "")
    if isinstance(release, (tuple, list)):
        release = " | ".join(str(value or "")[:240] for value in release[:8])
    return subtitle_episode_numbers(release) == (season, episode)


def text_matches_language(text, language):
    """Reject a wholly foreign alphabet for an explicitly Arabic subtitle.

    This is a contradiction check, not a guess between languages sharing an
    alphabet. Latin-only Polish text cannot be an Arabic-script subtitle.
    """
    if normalize_subtitle_language(language) != "ar":
        return True
    matches = re.finditer(r"[\u0600-\u06ff\u0750-\u077f\u0870-\u089f\u08a0-\u08ff\ufb50-\ufdff\ufe70-\ufeff]", text)
    return any(match.group(0).isalpha() for match in matches)


def filename_languages(value):
    """Read explicit language directories, brackets and filename suffixes."""
    path = str(value or "").replace("\\", "/")
    parts = path.split("/")
    found = set()
    known_codes = set(LANGUAGE_ALIASES.values())

    def marker_code(marker):
        code = normalize_subtitle_language(marker)
        return code if code in known_codes else ""

    for directory in parts[:-1]:
        code = marker_code(directory)
        if code:
            found.add(code)
    stem = os.path.splitext(parts[-1])[0]
    for marker in re.findall(r"[\[({]([^\])}]{2,30})[\])}]", stem):
        code = marker_code(marker)
        if code:
            found.add(code)
    # Do not interpret arbitrary words in a movie title as language codes.
    # Language suffixes may precede HI/SDH/forced accessibility markers.
    stem = re.sub(r"(?i)(?:[ ._-]+(?:hi|sdh|forced|full|cc))+$", "", stem)
    suffix = re.search(r"[ ._-]+([A-Za-z]{2,12}(?:[-_][A-Za-z]{2})?)$", stem)
    if suffix:
        code = marker_code(suffix.group(1))
        if code:
            found.add(code)
    return found

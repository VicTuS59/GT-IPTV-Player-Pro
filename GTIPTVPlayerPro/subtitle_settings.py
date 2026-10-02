# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Persistent settings for the receiver-native online subtitle renderer."""

import json
import os
import tempfile
import threading

from .i18n import device_language, normalize_language


SUBTITLE_SETTINGS_PATH = "/etc/enigma2/gtiptvplayerpro-subtitles.json"
PREFERENCES_KEY = "_preferences"
MAX_API_KEY_LENGTH = 300
MAX_USERNAME_LENGTH = 160
MAX_PASSWORD_LENGTH = 300
MAX_OFFSET_MS = 30 * 60 * 1000
MAX_SETTINGS_BYTES = 64 * 1024

PROVIDERS = ("subdl", "opensubtitles", "subsource")
SEARCH_MODES = ("manual", "automatic")
FONT_SIZES = ("small", "medium", "large", "extra_large")
FONT_COLORS = ("white", "yellow", "cyan")
BACKGROUND_STYLES = ("transparent_dark", "solid_dark", "none")
VERTICAL_POSITIONS = ("bottom", "lower", "middle")

_DOCUMENT_LOCK = threading.RLock()


def _clean_api_key(value):
    value = "".join(str(value or "").split())[:MAX_API_KEY_LENGTH]
    return "" if any(ord(character) < 32 for character in value) else value


def _clean_credential(value, limit, strip=False):
    value = str(value or "")
    if strip:
        value = value.strip()
    value = value[:int(limit)]
    return "" if any(ord(character) < 32 for character in value) else value


def _first_saved_value(mapping, names, fallback=""):
    """Return the first non-empty legacy/canonical credential value."""
    mapping = mapping if isinstance(mapping, dict) else {}
    for name in names:
        value = mapping.get(name)
        if value not in (None, ""):
            return value
    return fallback


def _provider_document(document, canonical, aliases=()):
    """Read provider credentials from canonical and older web layouts."""
    candidates = [document.get(canonical)]
    candidates.extend(document.get(alias) for alias in aliases)
    providers = document.get("providers")
    if isinstance(providers, dict):
        candidates.append(providers.get(canonical))
        candidates.extend(providers.get(alias) for alias in aliases)
    merged = {}
    # Legacy values fill gaps, while the canonical section wins whenever it
    # contains a real value. This also recovers a key saved by an older web UI
    # beside an empty canonical placeholder.
    for candidate in reversed(candidates):
        if not isinstance(candidate, dict):
            continue
        for name, value in candidate.items():
            if value not in (None, ""):
                merged[name] = value
    return merged


def _provider_language(value, fallback="en"):
    """Return the base ISO code accepted by online subtitle providers."""
    code = normalize_language(value, fallback=fallback)
    return code.split("_", 1)[0].lower()


def _default_languages():
    primary = _provider_language(device_language(), fallback="en")
    secondary = "tr" if primary == "en" else "en"
    return primary, secondary


def read_subtitle_document(path=SUBTITLE_SETTINGS_PATH):
    """Read one bounded JSON object without following a substituted symlink."""
    with _DOCUMENT_LOCK:
        descriptor = -1
        try:
            if os.path.islink(path):
                return {}
            flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(path, flags)
            if os.fstat(descriptor).st_size > MAX_SETTINGS_BYTES:
                return {}
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = -1
                document = json.load(handle)
            return document if isinstance(document, dict) else {}
        except (OSError, ValueError, UnicodeError):
            return {}
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass


def write_subtitle_document(document, path=SUBTITLE_SETTINGS_PATH):
    """Atomically replace the subtitle document with receiver-only permissions."""
    if not isinstance(document, dict):
        return False
    with _DOCUMENT_LOCK:
        directory = os.path.dirname(path) or "."
        try:
            if os.path.islink(path):
                return False
            os.makedirs(directory, mode=0o700, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".gt-subtitle-", dir=directory)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(document, handle, ensure_ascii=False, sort_keys=True)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.chmod(temporary, 0o600)
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            return True
        except (OSError, TypeError, ValueError):
            return False


def update_subtitle_document(updater, path=SUBTITLE_SETTINGS_PATH):
    """Apply one read/modify/write operation under the process-wide lock."""
    if not callable(updater):
        return False
    with _DOCUMENT_LOCK:
        document = read_subtitle_document(path)
        try:
            updated = updater(dict(document))
        except Exception:
            return False
        return write_subtitle_document(updated, path)


class SubtitleSettings(object):
    """Validated, copyable values used by both the settings UI and player."""

    def __init__(
        self,
        enabled=True,
        provider="subdl",
        api_key="",
        subdl_api_key=None,
        opensubtitles_api_key=None,
        opensubtitles_username="",
        opensubtitles_password="",
        primary_language=None,
        secondary_language=None,
        search_mode="automatic",
        hearing_impaired=True,
        font_size="medium",
        font_color="white",
        background="transparent_dark",
        vertical_position="bottom",
        offset_ms=0,
        subsource_api_key=None,
    ):
        default_primary, default_secondary = _default_languages()
        self.enabled = bool(enabled)
        self.provider = provider if provider in PROVIDERS else "subdl"
        # ``api_key`` remains a compatibility alias for the selected
        # provider.  Keeping provider-specific fields prevents switching the
        # settings screen from overwriting the other service's credentials.
        if subdl_api_key is None:
            subdl_api_key = api_key if self.provider == "subdl" else ""
        if opensubtitles_api_key is None:
            opensubtitles_api_key = (
                api_key if self.provider == "opensubtitles" else ""
            )
        if subsource_api_key is None:
            subsource_api_key = api_key if self.provider == "subsource" else ""
        self.subdl_api_key = _clean_api_key(subdl_api_key)
        self.opensubtitles_api_key = _clean_api_key(opensubtitles_api_key)
        self.subsource_api_key = _clean_api_key(subsource_api_key)
        self.opensubtitles_username = _clean_credential(
            opensubtitles_username, MAX_USERNAME_LENGTH, strip=True
        )
        self.opensubtitles_password = _clean_credential(
            opensubtitles_password, MAX_PASSWORD_LENGTH
        )
        self.primary_language = _provider_language(
            primary_language or default_primary, fallback=default_primary
        )
        self.secondary_language = _provider_language(
            secondary_language or default_secondary, fallback=default_secondary
        )
        self.search_mode = (
            search_mode if search_mode in SEARCH_MODES else "automatic"
        )
        self.hearing_impaired = bool(hearing_impaired)
        self.font_size = font_size if font_size in FONT_SIZES else "medium"
        self.font_color = font_color if font_color in FONT_COLORS else "white"
        self.background = (
            background if background in BACKGROUND_STYLES else "transparent_dark"
        )
        self.vertical_position = (
            vertical_position
            if vertical_position in VERTICAL_POSITIONS
            else "bottom"
        )
        try:
            offset_ms = int(offset_ms)
        except (TypeError, ValueError, OverflowError):
            offset_ms = 0
        self.offset_ms = max(-MAX_OFFSET_MS, min(MAX_OFFSET_MS, offset_ms))

    @property
    def api_key(self):
        if self.provider == "subsource":
            return self.subsource_api_key
        if self.provider == "opensubtitles":
            return self.opensubtitles_api_key
        return self.subdl_api_key

    @api_key.setter
    def api_key(self, value):
        if self.provider == "subsource":
            self.subsource_api_key = _clean_api_key(value)
        elif self.provider == "opensubtitles":
            self.opensubtitles_api_key = _clean_api_key(value)
        else:
            self.subdl_api_key = _clean_api_key(value)

    def credentials_configured(self, provider=None):
        provider = provider if provider in PROVIDERS else self.provider
        if provider == "subsource":
            return bool(self.subsource_api_key)
        if provider == "opensubtitles":
            # OpenSubtitles search and connection checks need only the API
            # key. Account credentials are an optional enhancement used by
            # the download-ticket request when both fields are available.
            return bool(self.opensubtitles_api_key)
        return bool(self.subdl_api_key)

    def copy(self):
        return self.from_dict(
            self.as_dict(),
            subdl_api_key=self.subdl_api_key,
            opensubtitles_api_key=self.opensubtitles_api_key,
            opensubtitles_username=self.opensubtitles_username,
            opensubtitles_password=self.opensubtitles_password,
            subsource_api_key=self.subsource_api_key,
        )

    def as_dict(self):
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "primary_language": self.primary_language,
            "secondary_language": self.secondary_language,
            "search_mode": self.search_mode,
            "hearing_impaired": self.hearing_impaired,
            "font_size": self.font_size,
            "font_color": self.font_color,
            "background": self.background,
            "vertical_position": self.vertical_position,
            "offset_ms": self.offset_ms,
        }

    @classmethod
    def from_dict(
        cls,
        values,
        api_key="",
        subdl_api_key=None,
        opensubtitles_api_key=None,
        opensubtitles_username="",
        opensubtitles_password="",
        subsource_api_key=None,
    ):
        values = values if isinstance(values, dict) else {}
        return cls(
            enabled=values.get("enabled", True),
            provider=values.get("provider", "subdl"),
            api_key=api_key,
            subdl_api_key=subdl_api_key,
            opensubtitles_api_key=opensubtitles_api_key,
            opensubtitles_username=opensubtitles_username,
            opensubtitles_password=opensubtitles_password,
            subsource_api_key=subsource_api_key,
            primary_language=values.get("primary_language"),
            secondary_language=values.get("secondary_language"),
            search_mode=values.get("search_mode", "automatic"),
            hearing_impaired=values.get("hearing_impaired", True),
            font_size=values.get("font_size", "medium"),
            font_color=values.get("font_color", "white"),
            background=values.get("background", "transparent_dark"),
            vertical_position=values.get("vertical_position", "bottom"),
            offset_ms=values.get("offset_ms", 0),
        )


def load_subtitle_settings(path=SUBTITLE_SETTINGS_PATH):
    document = read_subtitle_document(path)
    subdl = _provider_document(document, "subdl", ("sub_dl",))
    subsource = _provider_document(document, "subsource", ("sub_source",))
    opensubtitles = _provider_document(
        document,
        "opensubtitles",
        ("open_subtitles", "opensubtitles.com", "opensubtitles_com"),
    )
    return SubtitleSettings.from_dict(
        document.get(PREFERENCES_KEY),
        subdl_api_key=_first_saved_value(
            subdl,
            ("key", "api_key", "apikey", "apiKey"),
            document.get("subdl_api_key", ""),
        ),
        opensubtitles_api_key=_first_saved_value(
            opensubtitles,
            ("key", "api_key", "apikey", "apiKey"),
            _first_saved_value(
                document,
                ("opensubtitles_api_key", "opensubtitles_key"),
            ),
        ),
        opensubtitles_username=_first_saved_value(
            opensubtitles,
            ("username", "user", "login"),
            document.get("opensubtitles_username", ""),
        ),
        opensubtitles_password=_first_saved_value(
            opensubtitles,
            ("password", "pass", "passwd"),
            document.get("opensubtitles_password", ""),
        ),
        subsource_api_key=_first_saved_value(
            subsource,
            ("key", "api_key", "apikey", "apiKey"),
            document.get("subsource_api_key", ""),
        ),
    )


def save_subtitle_settings(settings, path=SUBTITLE_SETTINGS_PATH):
    if isinstance(settings, dict):
        settings = SubtitleSettings.from_dict(
            settings,
            api_key=settings.get("api_key", ""),
            subdl_api_key=settings.get("subdl_api_key"),
            opensubtitles_api_key=settings.get("opensubtitles_api_key"),
            opensubtitles_username=settings.get("opensubtitles_username", ""),
            opensubtitles_password=settings.get("opensubtitles_password", ""),
            subsource_api_key=settings.get("subsource_api_key"),
        )
    if not isinstance(settings, SubtitleSettings):
        return False
    def update(document):
        document[PREFERENCES_KEY] = settings.as_dict()
        subdl = document.get("subdl")
        subdl = dict(subdl) if isinstance(subdl, dict) else {}
        subdl["key"] = _clean_api_key(settings.subdl_api_key)
        subdl.setdefault("enabled", True)
        document["subdl"] = subdl

        opensubtitles = document.get("opensubtitles")
        opensubtitles = (
            dict(opensubtitles) if isinstance(opensubtitles, dict) else {}
        )
        opensubtitles["key"] = _clean_api_key(
            settings.opensubtitles_api_key
        )
        opensubtitles["username"] = _clean_credential(
            settings.opensubtitles_username, MAX_USERNAME_LENGTH, strip=True
        )
        opensubtitles["password"] = _clean_credential(
            settings.opensubtitles_password, MAX_PASSWORD_LENGTH
        )
        opensubtitles.setdefault("enabled", True)
        document["opensubtitles"] = opensubtitles
        subsource = document.get("subsource")
        subsource = dict(subsource) if isinstance(subsource, dict) else {}
        subsource["key"] = _clean_api_key(settings.subsource_api_key)
        subsource.setdefault("enabled", True)
        document["subsource"] = subsource
        return document

    return update_subtitle_document(update, path)

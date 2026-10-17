# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Backup sections and lossless merging of independently selected preferences."""

import os

GROUPS = ("accounts", "settings", "favorites", "history", "subtitles", "sources")
PLAYER_SETTINGS = "/etc/enigma2/gtiptvplayerpro-settings.json"
SUBTITLE_SETTINGS = "/etc/enigma2/gtiptvplayerpro-subtitles.json"
SHARED_JSON = frozenset((PLAYER_SETTINGS, SUBTITLE_SETTINGS))
_MISSING = object()


def normalize_groups(groups=None):
    if groups is None:
        return GROUPS
    if isinstance(groups, (str, bytes)):
        raise ValueError("Invalid backup sections")
    values = tuple(groups)
    if not values or any(value not in GROUPS for value in values):
        raise ValueError("Invalid backup sections")
    return tuple(group for group in GROUPS if group in values)


def credential_key(key):
    key = str(key).lower().replace("-", "_")
    return (key in ("key", "apikey", "user", "username", "login", "password", "pass", "passwd",
                    "token", "secret", "credentials")
            or any(word in key for word in ("api_key", "apikey", "password", "passwd", "secret", "token"))
            or key.endswith(("_username", "_login", "_key")))


def native_group(line):
    return "accounts" if credential_key(line.partition("=")[0].rsplit(".", 1)[-1]) else "settings"


def file_group(path, kind="data", hint=None):
    # Registry-selected text documents can live inside DATA_DIR as well as
    # on a mounted disk. Their location does not change their backup section.
    if hint in ("accounts", "sources") and (kind == "external" or
            (kind == "data" and path.lower().endswith((".txt", ".m3u", ".m3u8", ".xml", ".xml.gz")))):
        return hint
    name = os.path.basename(path).lower()
    if kind == "external":
        return "accounts" if name.endswith(".txt") else "sources"
    if path.startswith("/etc/enigma2/gtiptvplayerpro-subtitle-cache/"):
        return "subtitles"
    if "favorite" in name:
        return "favorites"
    if any(word in name for word in ("resume", "history", "downloads")):
        return "history"
    if (name in ("playlists.txt", "stalker.txt", "stalker_portal.txt", "playlist-files.json",
                 "m3u-sources.json", "m3u-xmltv.json")
            or any(word in name for word in ("credential", "account", "web-auth"))
            or name.endswith((".key", ".secret"))):
        return "accounts"
    if name.endswith((".m3u", ".m3u8", ".xml", ".xml.gz")) or "/restored-sources/" in path:
        return "sources"
    if path == SUBTITLE_SETTINGS:
        return "subtitles"
    return "settings"


def project_document(path, document, groups):
    """Split shared JSON by leaf; API keys never hitchhike with UI settings."""
    default = "subtitles" if path == SUBTITLE_SETTINGS else "settings"
    chosen = set(groups)

    def project(value, group):
        if not isinstance(value, dict):
            return value if group in chosen else _MISSING
        result = {}
        for key, item in value.items():
            own = "accounts" if credential_key(key) else group
            part = project(item, own)
            if part is not _MISSING:
                result[key] = part
        if result or (not value and group in chosen):
            return result
        return _MISSING

    result = project(document, default)
    return {} if result is _MISSING else result


def merge_document(path, current, fragment, groups):
    """Replace selected leaves and retain every unselected value."""
    remaining = project_document(path, current, set(GROUPS).difference(groups))

    def overlay(base, extra):
        if not isinstance(base, dict) or not isinstance(extra, dict):
            return extra
        result = dict(base)
        for key, value in extra.items():
            result[key] = overlay(result.get(key), value)
        return result

    return overlay(remaining, fragment)

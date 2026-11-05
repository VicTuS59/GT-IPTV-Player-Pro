# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Persist presentation order independently from player preference values."""
import json
import os
import tempfile

from .persistence import guarded_mutation


DEFAULT_SETTINGS_ORDER = (
    "live_service_type", "movie_service_type", "series_service_type",
    "metadata_enabled", "tmdb_api_key", "dvb_epg", "category_lock",
    "appearance", "subtitles", "playlist_files", "downloads", "weather",
    "web_interface", "youtube", "backup", "restore", "software_update",
)
DEFAULT_ORDER_PATH = "/etc/enigma2/gtiptvplayer/settings-order.json"
MAX_ORDER_BYTES = 4096


def normalize_settings_order(value, defaults=DEFAULT_SETTINGS_ORDER):
    """Ignore unknown/duplicate IDs and append new settings in default order."""
    if not isinstance(value, (list, tuple)):
        value = ()
    result = []
    for key in tuple(value) + tuple(defaults):
        if isinstance(key, str) and key in defaults and key not in result:
            result.append(key)
    return tuple(result)


class SettingsOrderStore:
    def __init__(self, path=DEFAULT_ORDER_PATH):
        self.path = str(path)

    def load(self):
        try:
            with open(self.path, "rb") as handle:
                body = handle.read(MAX_ORDER_BYTES + 1)
            if len(body) > MAX_ORDER_BYTES:
                return DEFAULT_SETTINGS_ORDER
            document = json.loads(body.decode("utf-8"))
            value = (document.get("order") if isinstance(document, dict)
                     and document.get("version") == 1 else ())
            return normalize_settings_order(value)
        except (OSError, ValueError, UnicodeError):
            return DEFAULT_SETTINGS_ORDER

    @guarded_mutation
    def save(self, order):
        directory = os.path.dirname(os.path.abspath(self.path))
        temporary = None
        descriptor = None
        try:
            os.makedirs(directory, exist_ok=True)
            body = json.dumps({"version": 1, "order": normalize_settings_order(order)},
                              separators=(",", ":")).encode("utf-8")
            descriptor, temporary = tempfile.mkstemp(
                prefix=".settings-order-", suffix=".tmp", dir=directory,
            )
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(body)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
            temporary = None
            # Flush the directory entry on receiver filesystems. Some desktop
            # test platforms do not support opening directories for fsync.
            try:
                parent = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    os.fsync(parent)
                finally:
                    os.close(parent)
            except OSError:
                pass
            return True
        except (OSError, TypeError, ValueError):
            return False
        finally:
            if descriptor is not None:
                os.close(descriptor)
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small persistent settings model for the optional DVB EPG feature."""

import json
import os
import threading
import uuid


DVB_EPG_DATA_DIR = "/etc/enigma2/gtiptvplayer/dvb-epg"
DVB_EPG_SETTINGS_PATH = os.path.join(DVB_EPG_DATA_DIR, "settings.json")
DVB_EPG_SETTINGS_VERSION = 2
MAX_SETTINGS_BYTES = 64 * 1024
SUPPORTED_EPG_DAYS = (1, 3, 5, 7)
SUPPORTED_UPDATE_MODES = ("manual", "automatic")
SUPPORTED_UPDATE_INTERVAL_HOURS = (6, 12, 24)
ALL_BOUQUETS = "*"
_SETTINGS_LOCK = threading.RLock()


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(fallback)


def _safe_bouquet_file(value):
    value = str(value or ALL_BOUQUETS).strip()
    if value == ALL_BOUQUETS:
        return value
    if (
        not value
        or len(value) > 255
        or value != os.path.basename(value)
        or not value.startswith("userbouquet.")
        or not value.endswith(".tv")
    ):
        return ALL_BOUQUETS
    return value


class DVBEPGSettings(object):
    """User choices kept separate from the existing R27 player settings."""

    def __init__(
        self,
        enabled=False,
        source_key="",
        bouquet_file=ALL_BOUQUETS,
        epg_days=3,
        last_success_utc=0,
        last_mapping_count=0,
        last_event_count=0,
        update_mode="automatic",
        update_interval_hours=24,
        update_on_startup=True,
        update_in_standby=True,
    ):
        self.enabled = bool(enabled)
        self.source_key = str(source_key or "").strip()[:128]
        self.bouquet_file = _safe_bouquet_file(bouquet_file)
        epg_days = _safe_int(epg_days, 3)
        self.epg_days = epg_days if epg_days in SUPPORTED_EPG_DAYS else 3
        self.last_success_utc = max(0, _safe_int(last_success_utc, 0))
        self.last_mapping_count = max(0, _safe_int(last_mapping_count, 0))
        self.last_event_count = max(0, _safe_int(last_event_count, 0))
        update_mode = str(update_mode or "").strip().lower()
        self.update_mode = (
            update_mode
            if update_mode in SUPPORTED_UPDATE_MODES
            else "automatic"
        )
        update_interval_hours = _safe_int(update_interval_hours, 24)
        self.update_interval_hours = (
            update_interval_hours
            if update_interval_hours in SUPPORTED_UPDATE_INTERVAL_HOURS
            else 24
        )
        self.update_on_startup = bool(update_on_startup)
        self.update_in_standby = bool(update_in_standby)

    @classmethod
    def from_dict(cls, payload):
        payload = payload if isinstance(payload, dict) else {}
        return cls(
            enabled=payload.get("enabled", False),
            source_key=payload.get("source_key", ""),
            bouquet_file=payload.get("bouquet_file", ALL_BOUQUETS),
            epg_days=payload.get("epg_days", 3),
            last_success_utc=payload.get("last_success_utc", 0),
            last_mapping_count=payload.get("last_mapping_count", 0),
            last_event_count=payload.get("last_event_count", 0),
            update_mode=payload.get("update_mode", "automatic"),
            update_interval_hours=payload.get("update_interval_hours", 24),
            update_on_startup=payload.get("update_on_startup", True),
            update_in_standby=payload.get("update_in_standby", True),
        )

    def copy(self):
        return self.from_dict(self.as_dict())

    def as_dict(self):
        return {
            "version": DVB_EPG_SETTINGS_VERSION,
            "enabled": self.enabled,
            "source_key": self.source_key,
            "bouquet_file": self.bouquet_file,
            "epg_days": self.epg_days,
            "last_success_utc": self.last_success_utc,
            "last_mapping_count": self.last_mapping_count,
            "last_event_count": self.last_event_count,
            "update_mode": self.update_mode,
            "update_interval_hours": self.update_interval_hours,
            "update_on_startup": self.update_on_startup,
            "update_in_standby": self.update_in_standby,
        }


def _safe_regular_file(path):
    try:
        status = os.lstat(path)
    except OSError:
        return False
    return os.path.isfile(path) and not os.path.islink(path) and status.st_size <= MAX_SETTINGS_BYTES


def _load_dvb_epg_settings_unlocked(path):
    if not _safe_regular_file(path):
        return DVBEPGSettings()
    try:
        with open(path, "rb") as handle:
            payload = handle.read(MAX_SETTINGS_BYTES + 1)
        if len(payload) > MAX_SETTINGS_BYTES:
            return DVBEPGSettings()
        decoded = json.loads(payload.decode("utf-8-sig"))
    except (IOError, OSError, TypeError, ValueError, UnicodeError):
        return DVBEPGSettings()
    if not isinstance(decoded, dict):
        return DVBEPGSettings()
    version = _safe_int(decoded.get("version", DVB_EPG_SETTINGS_VERSION), 0)
    # Version 1 contains the original R28 DVB EPG fields.  Loading it through
    # ``from_dict`` retains those values and supplies the safe version 2
    # automatic-update defaults; the next normal save writes version 2.
    if version not in (1, DVB_EPG_SETTINGS_VERSION):
        return DVBEPGSettings()
    return DVBEPGSettings.from_dict(decoded)


def load_dvb_epg_settings(path=DVB_EPG_SETTINGS_PATH):
    with _SETTINGS_LOCK:
        return _load_dvb_epg_settings_unlocked(path)


def _prepare_private_directory(directory):
    if not directory:
        return
    if os.path.islink(directory):
        raise OSError("unsafe settings directory")
    if os.path.exists(directory) and not os.path.isdir(directory):
        raise OSError("settings path is not a directory")
    if not os.path.isdir(directory):
        os.makedirs(directory, 0o700)
    try:
        os.chmod(directory, 0o700)
    except OSError:
        pass


def _save_dvb_epg_settings_unlocked(settings, path):
    if not isinstance(settings, DVBEPGSettings):
        settings = DVBEPGSettings.from_dict(settings)
    directory = os.path.dirname(path)
    _prepare_private_directory(directory)
    if os.path.islink(path) or (os.path.exists(path) and not os.path.isfile(path)):
        raise OSError("unsafe settings path")
    temporary = "{}.tmp.{}.{}".format(path, os.getpid(), uuid.uuid4().hex)
    payload = json.dumps(
        settings.as_dict(),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    if len(payload) > MAX_SETTINGS_BYTES:
        raise ValueError("settings payload is too large")
    try:
        descriptor = os.open(
            temporary,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_CLOEXEC", 0)
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
        )
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except OSError:
                pass
        os.chmod(temporary, 0o600)
        replacer = getattr(os, "replace", os.rename)
        replacer(temporary, path)
        try:
            directory_descriptor = os.open(directory or ".", os.O_RDONLY)
            try:
                os.fsync(directory_descriptor)
            finally:
                os.close(directory_descriptor)
        except OSError:
            pass
    finally:
        try:
            if os.path.exists(temporary) or os.path.islink(temporary):
                os.unlink(temporary)
        except OSError:
            pass
    return True


def save_dvb_epg_settings(settings, path=DVB_EPG_SETTINGS_PATH):
    with _SETTINGS_LOCK:
        return _save_dvb_epg_settings_unlocked(settings, path)


def update_dvb_epg_settings(mutator, path=DVB_EPG_SETTINGS_PATH):
    """Atomically read, modify and replace the process-local settings model.

    ``mutator`` receives a private settings copy.  Returning ``None`` aborts
    without writing; otherwise the returned model/dict is validated, saved and
    returned as a new ``DVBEPGSettings`` instance.  Exceptions are propagated
    so callers can report persistence failures accurately.
    """
    if not callable(mutator):
        raise TypeError("settings mutator must be callable")
    with _SETTINGS_LOCK:
        current = _load_dvb_epg_settings_unlocked(path)
        candidate = mutator(current.copy())
        if candidate is None:
            return None
        if isinstance(candidate, dict):
            candidate = DVBEPGSettings.from_dict(candidate)
        elif isinstance(candidate, DVBEPGSettings):
            candidate = DVBEPGSettings.from_dict(candidate.as_dict())
        else:
            raise TypeError("settings mutator must return settings, dict or None")
        _save_dvb_epg_settings_unlocked(candidate, path)
        return candidate.copy()

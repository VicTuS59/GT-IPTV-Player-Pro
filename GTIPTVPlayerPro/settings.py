# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import os

try:
    from Components.config import (
        ConfigSelection,
        ConfigSubsection,
        ConfigText,
        ConfigYesNo,
        config,
        configfile,
    )
except ImportError:  # Allow settings migration outside a running Enigma2 GUI.
    ConfigSelection = None
    ConfigSubsection = None
    ConfigText = None
    ConfigYesNo = None
    config = None
    configfile = None


DEFAULT_SETTINGS_PATH = "/etc/enigma2/gtiptvplayerpro-settings.json"
TEXT_SIZE_CHOICES = ("standard", "large", "very_large")
SUPPORTED_SERVICE_TYPES = (1, 4097, 5001, 5002)
SERVICE_ENGINE_EXECUTABLES = {
    5001: ("/usr/bin/gstplayer", "/usr/bin/gstplayer_gst-1.0"),
    5002: ("/usr/bin/exteplayer3",),
}
_AVAILABLE_SERVICE_TYPES_CACHE = [None]
_CONFIG_SECTION = [None]


def _player_config():
    """Return the standard Enigma2 config section when it is available."""
    if _CONFIG_SECTION[0] is not None:
        return _CONFIG_SECTION[0]
    if config is None or ConfigSubsection is None:
        return None

    plugins = getattr(config, "plugins", None)
    if plugins is None:
        return None
    section = getattr(plugins, "gtiptvplayerpro", None)
    if section is None:
        section = ConfigSubsection()
        plugins.gtiptvplayerpro = section

    if not hasattr(section, "live_service_type"):
        section.live_service_type = ConfigSelection(
            default="4097",
            choices=["1", "4097", "5001", "5002"],
        )
    if not hasattr(section, "movie_service_type"):
        section.movie_service_type = ConfigSelection(
            default="5002",
            choices=["1", "4097", "5001", "5002"],
        )
    if not hasattr(section, "series_service_type"):
        section.series_service_type = ConfigSelection(
            default="5002",
            choices=["1", "4097", "5001", "5002"],
        )
    if not hasattr(section, "metadata_enabled"):
        section.metadata_enabled = ConfigYesNo(default=True)
    if not hasattr(section, "tmdb_api_key"):
        section.tmdb_api_key = ConfigText(default="", fixed_size=False)
    if not hasattr(section, "cinematic_view"):
        section.cinematic_view = ConfigYesNo(default=True)
    if not hasattr(section, "legacy_json_migrated"):
        section.legacy_json_migrated = ConfigYesNo(default=False)
    if not hasattr(section, "weather_enabled"):
        section.weather_enabled = ConfigYesNo(default=True)
    if not hasattr(section, "weather_city"):
        section.weather_city = ConfigText(default="Tekirdağ", fixed_size=False)
    if not hasattr(section, "weather_unit"):
        section.weather_unit = ConfigSelection(
            default="C",
            choices=["C", "F"],
        )
    if not hasattr(section, "ui_text_size"):
        section.ui_text_size = ConfigSelection(
            default="standard",
            choices=list(TEXT_SIZE_CHOICES),
        )
    for name in (
        "weather_latitude",
        "weather_longitude",
        "weather_location_id",
        "weather_country",
        "weather_country_code",
        "weather_admin1",
        "weather_admin2",
        "weather_admin3",
        "weather_admin4",
        "weather_timezone",
    ):
        if not hasattr(section, name):
            setattr(section, name, ConfigText(default="", fixed_size=False))
    if not hasattr(section, "weather_legacy_json_migrated"):
        section.weather_legacy_json_migrated = ConfigYesNo(default=False)

    _CONFIG_SECTION[0] = section
    return section


def plugin_config_section():
    """Return the shared ``config.plugins.gtiptvplayerpro`` subsection."""
    return _player_config()


def set_config_value(element, value):
    """Assign a ConfigElement value across supported Enigma2 images."""
    _set_config_value(element, value)


def save_plugin_config(section):
    """Persist a plugin subsection and the global Enigma2 config file."""
    try:
        section.save()
        if configfile is not None:
            configfile.save()
        return True
    except (AttributeError, IOError, OSError, TypeError, ValueError):
        return False


def available_service_types(path_exists=None):
    """Return only playback engines that can actually run on this receiver."""
    if path_exists is None and _AVAILABLE_SERVICE_TYPES_CACHE[0] is not None:
        return _AVAILABLE_SERVICE_TYPES_CACHE[0]
    available = [1, 4097]
    for service_type in (5001, 5002):
        if any(
            (
                bool(path_exists(path))
                if path_exists is not None
                else os.path.isfile(path) and os.access(path, os.X_OK)
            )
            for path in SERVICE_ENGINE_EXECUTABLES[service_type]
        ):
            available.append(service_type)
    result = tuple(available)
    if path_exists is None:
        _AVAILABLE_SERVICE_TYPES_CACHE[0] = result
    return result


def configurable_service_types():
    """Return every service type the plugin can store and request.

    Executable discovery must not hide a valid Enigma2/ServiceApp service ID:
    some images register the service through components that are not visible
    at the conventional executable paths.  Runtime availability is therefore
    informational only and never limits the settings screen.
    """
    return SUPPORTED_SERVICE_TYPES


def supported_service_type(value, fallback=4097):
    """Validate a configured service type without silently changing it.

    Engine discovery is useful for diagnostics, but it must not rewrite an
    explicit user preference.  ServiceApp can
    register 5001/5002 even on images where its executable is not visible at
    one of the conventional paths checked by :func:`available_service_types`.
    """
    return _choice(value, SUPPORTED_SERVICE_TYPES, fallback)


def service_type_label(service_type):
    try:
        service_type = int(service_type)
    except (TypeError, ValueError, OverflowError):
        service_type = 5002
    return {
        1: "DVB / IPTV (1)",
        4097: "IPTV / GStreamer (4097)",
        5001: "GstPlayer (5001)",
        5002: "ExtEplayer3 (5002)",
    }.get(service_type, "Service ({})".format(service_type))


def service_engine_label(service_type):
    try:
        service_type = int(service_type)
    except (TypeError, ValueError, OverflowError):
        service_type = 5002
    return {
        1: "DVB / IPTV",
        4097: "IPTV / GStreamer",
        5001: "GstPlayer",
        5002: "ExtEplayer3",
    }.get(service_type, "Service {}".format(service_type))


def _choice(value, choices, fallback):
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        return fallback
    return value if value in choices else fallback


def normalize_text_size(value):
    """Return a stable, non-localized R78 text-size profile key."""
    value = str(value or "").strip().lower()
    return value if value in TEXT_SIZE_CHOICES else "standard"


class PlayerSettings(object):
    """Persistent player preferences with conservative receiver defaults."""

    def __init__(
        self,
        live_service_type=4097,
        movie_service_type=5002,
        series_service_type=5002,
        metadata_enabled=True,
        tmdb_api_key="",
        ui_text_size="standard",
        cinematic_view=True,
    ):
        self.live_service_type = supported_service_type(
            live_service_type, 4097
        )
        self.movie_service_type = supported_service_type(
            movie_service_type, 5002
        )
        self.series_service_type = supported_service_type(
            series_service_type, 5002
        )
        self.metadata_enabled = bool(metadata_enabled)
        self.tmdb_api_key = str(tmdb_api_key or "").strip()[:512]
        self.ui_text_size = normalize_text_size(ui_text_size)
        self.cinematic_view = bool(cinematic_view)

    @classmethod
    def from_dict(cls, payload):
        payload = payload if isinstance(payload, dict) else {}
        return cls(
            live_service_type=payload.get("live_service_type", 4097),
            movie_service_type=payload.get("movie_service_type", 5002),
            series_service_type=payload.get("series_service_type", 5002),
            metadata_enabled=payload.get("metadata_enabled", True),
            tmdb_api_key=payload.get("tmdb_api_key", ""),
            ui_text_size=payload.get(
                "ui_text_size",
                payload.get("text_size", "standard"),
            ),
            cinematic_view=payload.get("cinematic_view", True),
        )

    def copy(self):
        return self.from_dict(self.as_dict())

    def as_dict(self):
        return {
            "live_service_type": self.live_service_type,
            "movie_service_type": self.movie_service_type,
            "series_service_type": self.series_service_type,
            "metadata_enabled": self.metadata_enabled,
            "tmdb_api_key": self.tmdb_api_key,
            "ui_text_size": self.ui_text_size,
            "cinematic_view": self.cinematic_view,
        }

    def service_type_for(self, content_type):
        content_type = str(content_type or "").lower()
        if content_type == "live":
            return supported_service_type(self.live_service_type, 4097)
        if content_type == "movie":
            return supported_service_type(self.movie_service_type, 5002)
        if content_type == "series":
            return supported_service_type(self.series_service_type, 5002)
        return supported_service_type(self.movie_service_type, 5002)


def _read_json_settings(path):
    try:
        with open(path, "r") as handle:
            payload = json.load(handle)
    except (IOError, OSError, TypeError, ValueError):
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _write_json_settings(settings, path):
    if not isinstance(settings, PlayerSettings):
        settings = PlayerSettings.from_dict(settings)
    directory = os.path.dirname(path)
    temporary = path + ".tmp"
    try:
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(temporary, "w") as handle:
            payload = settings.as_dict()
            payload["version"] = 6
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except (AttributeError, OSError):
                pass
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        replacer = getattr(os, "replace", os.rename)
        replacer(temporary, path)
        return True
    except (IOError, OSError, TypeError, ValueError):
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass
        return False


def _set_config_value(element, value):
    setter = getattr(element, "setValue", None)
    if setter is not None:
        setter(value)
    else:
        element.value = value


def _settings_from_config(section):
    return PlayerSettings(
        live_service_type=section.live_service_type.value,
        movie_service_type=section.movie_service_type.value,
        series_service_type=section.series_service_type.value,
        metadata_enabled=section.metadata_enabled.value,
        tmdb_api_key=section.tmdb_api_key.value,
        ui_text_size=section.ui_text_size.value,
        cinematic_view=section.cinematic_view.value,
    )


def _store_config_settings(section, settings, migrated=True):
    if not isinstance(settings, PlayerSettings):
        settings = PlayerSettings.from_dict(settings)
    try:
        _set_config_value(
            section.live_service_type,
            str(settings.live_service_type),
        )
        _set_config_value(
            section.movie_service_type,
            str(settings.movie_service_type),
        )
        _set_config_value(
            section.series_service_type,
            str(settings.series_service_type),
        )
        _set_config_value(
            section.metadata_enabled,
            bool(settings.metadata_enabled),
        )
        _set_config_value(section.tmdb_api_key, settings.tmdb_api_key)
        _set_config_value(section.ui_text_size, settings.ui_text_size)
        _set_config_value(
            section.cinematic_view,
            bool(settings.cinematic_view),
        )
        if migrated:
            _set_config_value(section.legacy_json_migrated, True)
        section.save()
        if configfile is not None:
            configfile.save()
        return True
    except (AttributeError, IOError, OSError, TypeError, ValueError):
        return False


def _migrate_legacy_settings(section, path):
    if bool(section.legacy_json_migrated.value):
        return
    payload = _read_json_settings(path)
    settings = PlayerSettings.from_dict(payload) if payload else PlayerSettings()
    _store_config_settings(section, settings, migrated=True)


def load_player_settings(path=DEFAULT_SETTINGS_PATH):
    """Load preferences from Enigma2 config, migrating the v1 JSON once."""
    section = _player_config() if path == DEFAULT_SETTINGS_PATH else None
    if section is None:
        return PlayerSettings.from_dict(_read_json_settings(path))
    _migrate_legacy_settings(section, path)
    return _settings_from_config(section)


def save_player_settings(settings, path=DEFAULT_SETTINGS_PATH):
    """Save preferences through Enigma2's standard ``config.plugins`` tree."""
    if not isinstance(settings, PlayerSettings):
        settings = PlayerSettings.from_dict(settings)
    section = _player_config() if path == DEFAULT_SETTINGS_PATH else None
    if section is not None:
        if _store_config_settings(section, settings, migrated=True):
            return True
        # Preserve settings on an unusual image where configfile.save fails.
    return _write_json_settings(settings, path)


def load_text_size(path=DEFAULT_SETTINGS_PATH):
    """Load the appearance profile independently from player preferences."""
    section = _player_config() if path == DEFAULT_SETTINGS_PATH else None
    if section is not None:
        try:
            return normalize_text_size(section.ui_text_size.value)
        except (AttributeError, TypeError, ValueError):
            return "standard"
    payload = _read_json_settings(path)
    return normalize_text_size(
        payload.get("ui_text_size", payload.get("text_size"))
    )


def save_text_size(value, path=DEFAULT_SETTINGS_PATH):
    """Persist only the appearance profile without overwriting other fields."""
    value = normalize_text_size(value)
    section = _player_config() if path == DEFAULT_SETTINGS_PATH else None
    if section is not None:
        try:
            _set_config_value(section.ui_text_size, value)
            section.ui_text_size.save()
            if configfile is not None:
                configfile.save()
            return True
        except (AttributeError, IOError, OSError, TypeError, ValueError):
            pass

    payload = _read_json_settings(path)
    payload["ui_text_size"] = value
    payload.pop("text_size", None)
    payload["version"] = 6
    directory = os.path.dirname(path)
    temporary = path + ".tmp"
    try:
        if directory and not os.path.isdir(directory):
            os.makedirs(directory)
        with open(temporary, "w") as handle:
            json.dump(
                payload,
                handle,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            handle.flush()
            try:
                os.fsync(handle.fileno())
            except (AttributeError, OSError):
                pass
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        getattr(os, "replace", os.rename)(temporary, path)
        return True
    except (IOError, OSError, TypeError, ValueError):
        try:
            if os.path.exists(temporary):
                os.unlink(temporary)
        except OSError:
            pass
        return False

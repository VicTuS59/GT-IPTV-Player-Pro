# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import os

from .youtube_options import YOUTUBE_QUALITIES, DEFAULT_YOUTUBE_QUALITY

from .channel_highlight import (
    CHANNEL_HIGHLIGHT_KEYS,
    DEFAULT_CHANNEL_HIGHLIGHT,
    normalize_channel_highlight,
    normalize_selection_border,
)

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
    if not hasattr(section, "youtube_resolution"):
        section.youtube_resolution = ConfigSelection(default=DEFAULT_YOUTUBE_QUALITY, choices=list(YOUTUBE_QUALITIES))
    if not hasattr(section, "youtube_autoplay"):
        section.youtube_autoplay = ConfigYesNo(default=False)
    if not hasattr(section, "youtube_dash"):
        section.youtube_dash = ConfigYesNo(default=True)
    if not hasattr(section, "youtube_stream_mode"):
        section.youtube_stream_mode = ConfigSelection(
            default="auto", choices=["auto", "compatible", "dash"]
        )
    if not hasattr(section, "youtube_mode_migrated"):
        section.youtube_mode_migrated = ConfigYesNo(default=False)
    if not hasattr(section, "youtube_audio_preference"):
        section.youtube_audio_preference = ConfigSelection(
            default="default", choices=["default", "original"]
        )
    if not hasattr(section, "youtube_search_language"):
        # Kept for older Enigma2 config files; searches now follow the device
        # or the requesting browser and no longer read this preference.
        section.youtube_search_language = ConfigSelection(default="tr", choices=["tr", "en", "de", "fr", "es", "ar"])
    if not hasattr(section, "cinematic_view"):
        section.cinematic_view = ConfigYesNo(default=True)
    if not hasattr(section, "show_in_main_menu"):
        section.show_in_main_menu = ConfigYesNo(default=True)
    if not hasattr(section, "dashboard_order"):
        section.dashboard_order = ConfigText(default="", fixed_size=False)
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
    if not hasattr(section, "channel_highlight"):
        section.channel_highlight = ConfigSelection(
            default=DEFAULT_CHANNEL_HIGHLIGHT,
            choices=list(CHANNEL_HIGHLIGHT_KEYS),
        )
    if not hasattr(section, "selection_border"):
        section.selection_border = ConfigSelection(
            default=DEFAULT_CHANNEL_HIGHLIGHT,
            choices=list(CHANNEL_HIGHLIGHT_KEYS),
        )
    if not hasattr(section, "selection_border_migrated"):
        section.selection_border_migrated = ConfigYesNo(default=False)
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


def normalize_dashboard_order(value, default_order):
    """Keep known action IDs once each and append newly introduced entries."""
    defaults = tuple(default_order)
    if isinstance(value, str):
        value = value.split(",")
    elif not isinstance(value, (list, tuple)):
        value = ()
    ordered = []
    for action in tuple(value) + defaults:
        if isinstance(action, str):
            action = action.strip()
            if action in defaults and action not in ordered:
                ordered.append(action)
    return tuple(ordered)


def load_dashboard_order(default_order):
    """Load the receiver-wide menu order from native Enigma2 settings."""
    try:
        section = _player_config()
        value = section.dashboard_order.value if section is not None else ""
    except (AttributeError, TypeError, ValueError):
        value = ""
    return normalize_dashboard_order(value, default_order)


def save_dashboard_order(order, default_order):
    """Save only the menu order, retaining the previous value on failure."""
    try:
        section = _player_config()
        if section is None:
            return False
        element = section.dashboard_order
        previous = element.value
    except (AttributeError, TypeError, ValueError):
        return False
    try:
        _set_config_value(
            element, ",".join(normalize_dashboard_order(order, default_order))
        )
        element.save()
        if configfile is not None:
            configfile.save()
        return True
    except (AttributeError, IOError, OSError, TypeError, ValueError):
        try:
            _set_config_value(element, previous)
            element.save()
        except (AttributeError, IOError, OSError, TypeError, ValueError):
            pass
        return False


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
        youtube_resolution=DEFAULT_YOUTUBE_QUALITY,
        youtube_dash=True,
        youtube_search_language="tr",
        youtube_stream_mode=None,
        youtube_audio_preference="default",
        ui_text_size="standard",
        cinematic_view=True,
        show_in_main_menu=True,
        channel_highlight=DEFAULT_CHANNEL_HIGHLIGHT,
        selection_border=None,
        youtube_autoplay=False,
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
        self.youtube_resolution = str(youtube_resolution) if str(youtube_resolution) in YOUTUBE_QUALITIES else DEFAULT_YOUTUBE_QUALITY
        self.youtube_autoplay = bool(youtube_autoplay)
        # Legacy mode/audio values remain readable for upgrades, but YouTube
        # now always uses automatic native playback and the default audio.
        self.youtube_stream_mode = "auto"
        self.youtube_dash = True
        self.youtube_audio_preference = "default"
        self.youtube_search_language = str(youtube_search_language) if str(youtube_search_language) in ("tr", "en", "de", "fr", "es", "ar") else "tr"
        self.ui_text_size = normalize_text_size(ui_text_size)
        self.cinematic_view = bool(cinematic_view)
        self.show_in_main_menu = bool(show_in_main_menu)
        self.channel_highlight = normalize_channel_highlight(channel_highlight)
        self.selection_border = normalize_selection_border(
            selection_border, self.channel_highlight
        )

    @classmethod
    def from_dict(cls, payload):
        payload = payload if isinstance(payload, dict) else {}
        return cls(
            live_service_type=payload.get("live_service_type", 4097),
            movie_service_type=payload.get("movie_service_type", 5002),
            series_service_type=payload.get("series_service_type", 5002),
            metadata_enabled=payload.get("metadata_enabled", True),
            tmdb_api_key=payload.get("tmdb_api_key", ""),
            youtube_resolution=payload.get("youtube_resolution", DEFAULT_YOUTUBE_QUALITY),
            youtube_autoplay=payload.get("youtube_autoplay", False),
            youtube_dash=payload.get("youtube_dash", True),
            youtube_search_language=payload.get("youtube_search_language", "tr"),
            youtube_stream_mode=payload.get("youtube_stream_mode"),
            youtube_audio_preference=payload.get("youtube_audio_preference", "default"),
            ui_text_size=payload.get(
                "ui_text_size",
                payload.get("text_size", "standard"),
            ),
            cinematic_view=payload.get("cinematic_view", True),
            show_in_main_menu=payload.get("show_in_main_menu", True),
            channel_highlight=payload.get(
                "channel_highlight", DEFAULT_CHANNEL_HIGHLIGHT
            ),
            selection_border=payload.get(
                "selection_border", payload.get("channel_highlight")
            ),
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
            "youtube_resolution": self.youtube_resolution,
            "youtube_autoplay": self.youtube_autoplay,
            "youtube_dash": self.youtube_dash,
            "youtube_search_language": self.youtube_search_language,
            "youtube_stream_mode": self.youtube_stream_mode,
            "youtube_audio_preference": self.youtube_audio_preference,
            "ui_text_size": self.ui_text_size,
            "cinematic_view": self.cinematic_view,
            "show_in_main_menu": self.show_in_main_menu,
            "channel_highlight": self.channel_highlight,
            "selection_border": self.selection_border,
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
            payload["version"] = 8
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
    channel_highlight = getattr(
        getattr(section, "channel_highlight", None),
        "value", DEFAULT_CHANNEL_HIGHLIGHT,
    )
    border_element = getattr(section, "selection_border", None)
    border_migrated = getattr(section, "selection_border_migrated", None)
    selection_border = getattr(border_element, "value", None)
    if not bool(getattr(border_migrated, "value", False)):
        selection_border = channel_highlight
    return PlayerSettings(
        live_service_type=section.live_service_type.value,
        movie_service_type=section.movie_service_type.value,
        series_service_type=section.series_service_type.value,
        metadata_enabled=section.metadata_enabled.value,
        tmdb_api_key=section.tmdb_api_key.value,
        youtube_resolution=section.youtube_resolution.value,
        youtube_autoplay=section.youtube_autoplay.value,
        youtube_dash=section.youtube_dash.value,
        youtube_search_language=section.youtube_search_language.value,
        youtube_stream_mode=(
            section.youtube_stream_mode.value
            if section.youtube_mode_migrated.value else None
        ),
        youtube_audio_preference=section.youtube_audio_preference.value,
        ui_text_size=section.ui_text_size.value,
        cinematic_view=section.cinematic_view.value,
        show_in_main_menu=section.show_in_main_menu.value,
        channel_highlight=channel_highlight,
        selection_border=selection_border,
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
        _set_config_value(section.youtube_resolution, settings.youtube_resolution)
        _set_config_value(section.youtube_autoplay, settings.youtube_autoplay)
        _set_config_value(section.youtube_dash, settings.youtube_dash)
        _set_config_value(section.youtube_stream_mode, settings.youtube_stream_mode)
        _set_config_value(section.youtube_mode_migrated, True)
        _set_config_value(section.youtube_audio_preference, settings.youtube_audio_preference)
        _set_config_value(section.youtube_search_language, settings.youtube_search_language)
        _set_config_value(section.ui_text_size, settings.ui_text_size)
        _set_config_value(
            section.cinematic_view,
            bool(settings.cinematic_view),
        )
        _set_config_value(
            section.show_in_main_menu,
            bool(settings.show_in_main_menu),
        )
        if hasattr(section, "channel_highlight"):
            _set_config_value(section.channel_highlight, settings.channel_highlight)
        if hasattr(section, "selection_border"):
            _set_config_value(section.selection_border, settings.selection_border)
        if hasattr(section, "selection_border_migrated"):
            _set_config_value(section.selection_border_migrated, True)
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


def load_main_menu_visibility(path=DEFAULT_SETTINGS_PATH):
    """Read menu visibility without migrating or saving player preferences."""
    section = _player_config() if path == DEFAULT_SETTINGS_PATH else None
    if section is not None:
        return bool(section.show_in_main_menu.value)
    return bool(_read_json_settings(path).get("show_in_main_menu", True))


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


# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import datetime
import os
import platform
import threading
import textwrap
import time

from Components.ActionMap import ActionMap
try:
    from Components.FileList import FileList
except ImportError:
    FileList = None
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.Sources.List import List
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from enigma import eTimer, getDesktop

from . import (
    PLUGIN_HOMEPAGE,
    PLUGIN_ISSUES_URL,
    PLUGIN_LICENSE,
    PLUGIN_LICENSE_URL,
    PLUGIN_NAME,
    PLUGIN_VERSION,
)
from .background import attach_background, attach_pixmap
from .category_lock import CategoryEditLockStore
from .category_lock_ui import (
    GTCategoryCodeInputScreen,
    RECOVERY_REQUEST,
)
from .content import ContentError
from .i18n import (
    N_,
    _,
    device_language_label,
    localized_upper,
    metadata_language,
)
from .metadata import MetadataError, TMDbMetadataClient, clear_metadata_cache
from .health_view import (
    HEALTH_SIGNAL_GREEN,
    HEALTH_SIGNAL_NEUTRAL,
    HEALTH_SIGNAL_RED,
    HEALTH_SIGNAL_YELLOW,
    account_connection_signal,
    account_expiry_signal,
    account_test_is_inactive,
    account_test_signal,
    dashboard_account_text,
    health_account_text,
    health_signal,
    split_health_text,
)
from .paths import plugin_path
from .remote_footer import (
    decorate_remote_footer,
    footer_item,
    install_remote_footer,
    set_remote_footer,
)
from .scrollbar import hide_scrollbar, update_scrollbar
from .settings import (
    PlayerSettings,
    configurable_service_types,
    load_player_settings,
    normalize_text_size,
    save_player_settings,
    service_type_label,
)
from .typography import (
    clean_dynamic_text,
    ellipsize_dynamic_text,
    fit_dynamic_text,
    font_px,
    visible_count,
)
from .playlist import (
    MAX_ACCOUNTS,
    PlaylistLoadResult,
    SERVER_HEALTH_DETAILS_TEMPLATE,
    ServerHealth,
    check_source_health,
    delete_source,
    delete_sources,
    load_accounts,
    load_sources,
    localized_connection_usage_line,
    save_account_credentials,
)
from .playlist_files import (
    MAX_PLAYLIST_FILES_PER_TYPE,
    PLAYLIST_FILE_PAGE_SIZE,
    PLAYLIST_FILES_CONNECTED,
    PlaylistFileRegistry,
    default_registry,
)
from .m3u import (
    LOCAL_M3U_ROOTS,
    MAX_M3U_SOURCES,
    M3U_SOURCE_PATH,
    load_m3u_sources,
    validate_local_m3u_path,
)
from .stalker import (
    MAX_PORTAL_ACCOUNTS,
    load_portal_accounts,
)
from .weather import (
    OpenMeteoClient,
    WeatherError,
    WeatherLocation,
    WeatherService,
    WeatherSettings,
    last_weather_update,
    load_weather_settings,
    save_weather_settings,
)
try:
    from boxbranding import (
        getImageDistro,
        getImageVersion,
        getMachineBrand,
        getMachineName,
    )
except ImportError:
    getImageDistro = None
    getImageVersion = None
    getMachineBrand = None
    getMachineName = None


DEVELOPER_NAME = "VicTuS59"
UI_NETWORK_JOB_TIMEOUT_SECONDS = 30
UI_SOURCE_DELETE_TIMEOUT_SECONDS = 8
WELCOME_ACCOUNT_QUERY_TIMEOUT_SECONDS = 3.0
WELCOME_ACCOUNT_QUERY_DEBOUNCE_MS = 300
WELCOME_ACCOUNT_QUERY_POLL_MS = 100
ACCOUNT_BATCH_QUERY_TIMEOUT_SECONDS = 4.0
ACCOUNT_BATCH_QUERY_POLL_MS = 100
ACCOUNT_BATCH_DELETE_TIMEOUT_SECONDS = 12.0
ACCOUNT_BATCH_OPEN_DELAY_MS = 50
ACCOUNT_BATCH_OPEN_MAX_ATTEMPTS = 3
ACCOUNT_BATCH_CONFIRM_DELAY_MS = 50
ACCOUNT_BATCH_CONFIRM_MAX_ATTEMPTS = 3
DEFAULT_LOCAL_M3U_PATH = "/etc/enigma2/gtiptvplayer/IPTV_Liste.m3u"
M3U_FILE_PATTERN = r"(?i)^.*\.m3u8?$"
TXT_FILE_PATTERN = r"(?i)^.*\.txt$"
M3U_BROWSER_LOCATIONS = (
    ("HDD", "/media/hdd"),
    ("USB", "/media/usb"),
    ("NAS", "/media/net"),
    ("AUTOFS", "/media/autofs"),
    ("ETC", "/etc/enigma2/gtiptvplayer"),
    ("MEDIA", "/media"),
    ("MNT", "/mnt"),
)


class _UIJobToken(object):
    """One deadline/cancellation generation for non-browser UI workers."""

    def __init__(self, timeout=UI_NETWORK_JOB_TIMEOUT_SECONDS):
        self.cancel_event = threading.Event()
        self.deadline = time.monotonic() + max(0.1, float(timeout))

    def cancel(self):
        self.cancel_event.set()

    def active(self):
        return (
            not self.cancel_event.is_set()
            and time.monotonic() < self.deadline
        )

    def expired(self):
        return not self.active()

    def check(self):
        if not self.active():
            raise RuntimeError("cancelled")


def _cancel_ui_job(token):
    if token is not None:
        try:
            token.cancel()
        except Exception:
            pass


def _connect_timer(timer, callback):
    try:
        timer.timeout.connect(callback)
    except AttributeError:
        timer.callback.append(callback)


def _is_modal_open_error(error):
    message = str(error or "").lower()
    return (
        "modal open" in message
        and "screen which is modal" in message
    )


def _source_type(source):
    return str(getattr(source, "source_type", "xtream") or "xtream").lower()


def _source_supports(source, capability):
    checker = getattr(source, "supports", None)
    if callable(checker):
        try:
            return bool(checker(capability))
        except Exception:
            return False
    capabilities = getattr(source, "capabilities", ())
    if capabilities:
        return str(capability or "").lower() in capabilities
    return _source_type(source) == "xtream"


def _source_badge(source):
    label = str(getattr(source, "source_label", "") or "").strip()
    if label:
        return label
    if _source_type(source) == "m3u":
        kind = str(getattr(source, "kind", "") or "").upper()
        return "M3U • {}".format(
            kind or localized_upper(_("Source"))
        )
    output = str(getattr(source, "output_format", "ts") or "ts").upper()
    return "XTREAM • {}".format(output)


SOURCE_TYPES = ("xtream", "m3u", "stalker")
SOURCE_LIMITS = {
    "xtream": MAX_ACCOUNTS,
    "m3u": MAX_M3U_SOURCES,
    "stalker": MAX_PORTAL_ACCOUNTS,
}


def _safe_source_endpoint(source):
    """Return a list/detail label that cannot expose source credentials."""
    source_type = _source_type(source)
    if source_type == "stalker":
        return str(getattr(source, "masked_mac", "") or "")
    host = str(getattr(source, "host", "") or "").strip()
    if source_type == "m3u":
        # M3USource.host is a hostname for URLs and basename for files.
        return os.path.basename(host.rstrip("/")) or host
    return host


def _safe_source_name(source):
    """Credential-free server/domain or local basename for shared panels."""
    host = str(getattr(source, "host", "") or "").strip()
    if _source_type(source) == "m3u":
        return os.path.basename(host.rstrip("/")) or host
    return host


def _source_list_labels(source):
    """Return compact, credential-free primary and secondary row labels."""
    source_type = _source_type(source)
    endpoint = _safe_source_endpoint(source)
    badge = _source_badge(source)
    if source_type == "stalker":
        primary = _safe_source_name(source)
    else:
        primary = str(getattr(source, "display_name", "") or "").strip()
    primary = primary or endpoint or badge
    details = [badge]
    if endpoint and endpoint.casefold() != primary.casefold():
        details.append(endpoint)
    return primary, "  •  ".join(details)


def _source_result_key(source):
    """Return a stable in-memory key without exposing it in labels or logs."""
    identity = tuple(getattr(source, "identity", ()) or ())
    if identity:
        return (_source_type(source), identity)
    return (_source_type(source), id(source))


def _account_test_status_text(result):
    signal_name = account_test_signal(
        getattr(result, "status", ""),
        getattr(result, "account_status", ""),
        getattr(result, "active_connections", None),
        getattr(result, "max_connections", None),
        getattr(result, "expiry", ""),
    )
    return {
        HEALTH_SIGNAL_GREEN: _("Active"),
        HEALTH_SIGNAL_YELLOW: _("Connection limit reached"),
        HEALTH_SIGNAL_RED: _("Inactive"),
    }.get(signal_name, _("Unknown")), signal_name


def _account_test_expiry_text(result):
    expiry = str(getattr(result, "expiry", "") or N_("Unknown"))
    return _localized_runtime_text(expiry)


def _account_test_connection_text(result):
    value = result.connection_text()
    return _("Unknown") if value == N_("Unknown") else str(value)


def _account_test_row_detail(account, result):
    status_text, unused_signal = _account_test_status_text(result)
    del unused_signal
    details = [_source_badge(account)]
    if _source_type(account) == "stalker":
        masked_mac = _safe_source_endpoint(account)
        if masked_mac:
            details.append(masked_mac)
    details.extend(
        (
            _account_test_expiry_text(result),
            _account_test_connection_text(result),
            localized_upper(status_text),
        )
    )
    return "  |  ".join(details)


def _account_test_display_name(account):
    """Identify a tested account without exposing Xtream credentials."""
    primary, unused_secondary = _source_list_labels(account)
    del unused_secondary
    endpoint = _safe_source_endpoint(account)
    if endpoint and endpoint.casefold() != primary.casefold():
        return "{}  •  {}".format(primary, endpoint)
    return primary


def _set_signal_label(
    screen,
    prefix,
    text,
    signal_name,
    fallback_chars=32,
):
    signal_name = (
        signal_name
        if signal_name in (
            HEALTH_SIGNAL_GREEN,
            HEALTH_SIGNAL_YELLOW,
            HEALTH_SIGNAL_RED,
        )
        else HEALTH_SIGNAL_NEUTRAL
    )
    widgets = {
        HEALTH_SIGNAL_NEUTRAL: prefix,
        HEALTH_SIGNAL_GREEN: "{}_green".format(prefix),
        HEALTH_SIGNAL_YELLOW: "{}_yellow".format(prefix),
        HEALTH_SIGNAL_RED: "{}_red".format(prefix),
    }
    fitted = ellipsize_dynamic_text(
        screen[widgets[HEALTH_SIGNAL_NEUTRAL]],
        str(text or ""),
        fallback_chars=fallback_chars,
    )
    for current_signal, widget_name in widgets.items():
        widget = screen[widget_name]
        widget.setText(fitted)
        if current_signal == signal_name:
            widget.show()
        else:
            widget.hide()
    return fitted


def _load_local_source_type(source_type):
    """Load one registry only; never perform DNS, HTTP or a health check."""
    source_type = str(source_type or "").lower()
    if source_type == "xtream":
        return load_accounts()
    if source_type == "m3u":
        try:
            accounts = load_m3u_sources(M3U_SOURCE_PATH)
            sources = [M3U_SOURCE_PATH] if os.path.isfile(M3U_SOURCE_PATH) else []
            error = "" if accounts else N_("No valid M3U source was found")
            return PlaylistLoadResult(accounts, sources, error)
        except (IOError, OSError, TypeError, ValueError) as error:
            return PlaylistLoadResult([], [], str(error))
    if source_type == "stalker":
        try:
            result = load_portal_accounts()
            return PlaylistLoadResult(
                list(result.accounts),
                list(result.sources),
                str(result.error or ""),
            )
        except (IOError, OSError, TypeError, ValueError) as error:
            return PlaylistLoadResult([], [], str(error))
    return PlaylistLoadResult([], [], N_("Unsupported source type"))


def _matching_health_content_client(account, health):
    """Return only a health-check client created for this exact source."""
    candidate = getattr(health, "content_client", None)
    if candidate is None:
        return None
    if _source_type(candidate) != _source_type(account):
        return None
    candidate_account = getattr(candidate, "account", None)
    if candidate_account is account:
        return candidate
    expected = tuple(getattr(account, "identity", ()) or ())
    actual = tuple(getattr(candidate_account, "identity", ()) or ())
    if expected and actual and expected == actual:
        return candidate
    return None


CONTENT_ITEMS = (
    (
        N_("LIVE TV"),
        N_("Access your channels quickly"),
        "live",
    ),
    (
        N_("TV Archive"),
        N_("Watch programmes you missed"),
        "catchup",
    ),
    (
        N_("MOVIES"),
        N_("Explore your movie library"),
        "movie",
    ),
    (
        N_("SERIES"),
        N_("Browse seasons and episodes"),
        "series",
    ),
    (
        N_("CONTINUE WATCHING"),
        N_("Resume unfinished movies and series episodes"),
        "continue",
    ),
    (
        N_("FAVORITES"),
        N_("Favorite channels, movies and series in one place"),
        "favorites",
    ),
    (
        N_("SETTINGS"),
        N_("Manage the player and appearance"),
        "settings",
    ),
)

def _about_notice():
    return _(
        "{} v{}\n"
        "Open-source IPTV player for Enigma2. Supports Xtream Codes accounts "
        "and Live TV from standard local, mounted-network or HTTP(S) M3U "
        "playlists. Plain M3U sources provide Live TV only.\n\n"
        "DEVELOPER\n{}\n\n"
        "LEGAL NOTICE\nThis plugin does not provide channels, streams, accounts "
        "or playlists. The user is responsible for using only content for which "
        "they have access and usage rights.\n\n"
        "PRIVACY\nWhen Open-Meteo is enabled, the selected city and location are "
        "sent to Open-Meteo for the weather forecast. When TMDb is enabled, the "
        "movie/series title and the TMDb key or token entered by the user are sent "
        "to TMDb. The IPTV username and password are not sent to these services."
    )

PROVIDER_CREDITS = (
    "Weather data by Open-Meteo.com — CC BY 4.0\n"
    "https://open-meteo.com/\n"
    "https://creativecommons.org/licenses/by/4.0/\n"
    "Forecast data is reformatted for television display.\n"
    "This product uses the TMDB API but is not endorsed or certified by TMDB.\n"
    "https://www.themoviedb.org"
)


def _about_text():
    notice = _about_notice().format(
        PLUGIN_NAME,
        PLUGIN_VERSION,
        DEVELOPER_NAME,
    )
    labels = (_("SOURCE CODE"), _("REPORT AN ISSUE"), _("LICENSE"))
    links = (
        "{}: {}".format(labels[0], PLUGIN_HOMEPAGE),
        "{}: {}".format(labels[1], PLUGIN_ISSUES_URL),
        "{}: {} — {}".format(
            labels[2],
            PLUGIN_LICENSE,
            PLUGIN_LICENSE_URL,
        ),
    )
    sections = [notice, "\n".join(links)]
    # Provider attribution text is kept verbatim to satisfy both providers'
    # public attribution requirements in every UI language.
    sections.append(PROVIDER_CREDITS)
    return "\n\n".join(sections)


PLUGIN_PATH = plugin_path()
DASHBOARD_BACKGROUND = os.path.join(
    PLUGIN_PATH,
    "skin",
    "images",
    "dashboard-neon-r96.png",
)
ARCHIVE_HERO_PATH = os.path.join(
    PLUGIN_PATH, "skin", "images", "archive-hero-r88.png",
)
WELCOME_BACKGROUND = os.path.join(
    PLUGIN_PATH,
    "skin",
    "images",
    "welcome-neon-v0912.png",
)
ACCOUNTS_BACKGROUND = os.path.join(
    PLUGIN_PATH,
    "skin",
    "images",
    "accounts-neon-v0912.png",
)
APP_BACKGROUND = os.path.join(
    PLUGIN_PATH,
    "skin",
    "images",
    "global-neon-v0912.png",
)
TMDB_LOGO_PATH = os.path.join(
    PLUGIN_PATH,
    "skin",
    "images",
    "tmdb-logo.png",
)
HEALTH_TRAFFIC_PATHS = {
    "base": os.path.join(
        PLUGIN_PATH,
        "skin",
        "images",
        "health-traffic-base-r77.png",
    ),
    HEALTH_SIGNAL_GREEN: os.path.join(
        PLUGIN_PATH,
        "skin",
        "images",
        "health-traffic-green-r77.png",
    ),
    HEALTH_SIGNAL_YELLOW: os.path.join(
        PLUGIN_PATH,
        "skin",
        "images",
        "health-traffic-yellow-r77.png",
    ),
    HEALTH_SIGNAL_RED: os.path.join(
        PLUGIN_PATH,
        "skin",
        "images",
        "health-traffic-red-r77.png",
    ),
}

FEATURE_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("exit", "back"),
)
WEATHER_FOOTER_ITEMS = (
    footer_item("up_down", "select"),
    footer_item("left_right", "change"),
    footer_item("ok", "edit"),
    footer_item("exit", "back"),
)
WEATHER_LOCATION_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "select"),
    footer_item("green", "search"),
    footer_item("exit", "back"),
)
PLAYER_SETTINGS_FOOTER_ITEMS = (
    footer_item("arrows", "select_change", 1.25),
    footer_item("ok", "edit", 0.8),
    footer_item("blue", "metadata", 1.0),
    footer_item("green", "save", 0.85),
    footer_item("exit", "back", 0.75),
)
PLAYLIST_TYPE_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "open"),
    footer_item("exit", "back"),
)
PLAYLIST_MANAGER_FOOTER_ITEMS = (
    footer_item("up_down", "navigate", 0.9),
    footer_item("left_right", "page", 0.9),
    footer_item("ok", "edit", 0.7),
    footer_item("green", "connect_source", 1.15),
    footer_item("yellow", "rename", 0.85),
    footer_item("red", "delete_source", 1.05),
    footer_item("exit", "back", 0.65),
)
TXT_FILE_BROWSER_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "open"),
    footer_item("green", "select"),
    footer_item("blue", "storage"),
    footer_item("exit", "cancel"),
)
APPEARANCE_FOOTER_ITEMS = (
    footer_item("up_down", "select"),
    footer_item("left_right", "change"),
    footer_item("ok", "select"),
    footer_item("exit", "cancel"),
)
ACCOUNTS_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("channel", "page"),
    footer_item("ok", "connect_source", 1.25),
    footer_item("red", "delete_source", 1.2),
    footer_item("green", "new", 0.9),
    footer_item("yellow", "server_test", 0.9),
    footer_item("exit", "back", 0.8),
)
M3U_ACCOUNTS_FOOTER_ITEMS = ACCOUNTS_FOOTER_ITEMS[:-1] + (
    footer_item("blue", "xmltv_epg", 1.25),
) + ACCOUNTS_FOOTER_ITEMS[-1:]
ACCOUNT_BATCH_RUNNING_FOOTER_ITEMS = (
    footer_item("exit", "cancel"),
)
ACCOUNT_BATCH_REVIEW_FOOTER_ITEMS = (
    footer_item("left_right", "page"),
    footer_item("green", "delete_source"),
    footer_item("red", "cancel"),
    footer_item("exit", "back"),
)
MANAGE_SOURCES_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "open"),
    footer_item("exit", "back"),
)
ADD_SOURCE_TYPE_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "continue"),
    footer_item("exit", "cancel"),
)
ADD_M3U_SOURCE_FOOTER_ITEMS = (
    footer_item("arrows", "navigate_change", 1.3),
    footer_item("ok", "edit"),
    footer_item("green", "save"),
    footer_item("exit", "cancel"),
)
M3U_FILE_BROWSER_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "open"),
    footer_item("green", "select"),
    footer_item("blue", "storage"),
    footer_item("exit", "cancel"),
)
ADD_STALKER_SOURCE_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "edit"),
    footer_item("green", "save"),
    footer_item("exit", "cancel"),
)
ADD_ACCOUNT_FOOTER_ITEMS = (
    footer_item("arrows", "select_change", 1.3),
    footer_item("ok", "edit"),
    footer_item("green", "save"),
    footer_item("exit", "cancel"),
)
HEALTH_FOOTER_ITEMS = (
    footer_item("green", "retry"),
    footer_item("exit", "sources"),
)
M3U_HEALTH_FOOTER_ITEMS = (
    footer_item("green", "retry"),
    footer_item("blue", "xmltv_epg", 1.15),
    footer_item("exit", "sources"),
)
DASHBOARD_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "select"),
    footer_item("menu", "settings"),
    footer_item("yellow", "about"),
    footer_item("exit", "exit"),
)
WELCOME_FOOTER_ITEMS = (
    footer_item("left_right", "source_select", 1.1),
    footer_item("up_down", "action_select", 1.05),
    footer_item("ok", "confirm", 0.82),
    footer_item("menu", "settings", 0.9),
    footer_item("exit", "exit", 0.72),
)

# Coordinates use the 1920x1080 design canvas.  Each tuple is the exact top
# and bottom edge of one rounded panel baked into dashboard-neon-r96.png.
# Labels, the moving underline and the neon left edge are all derived from this
# single geometry so they cannot drift when the OSD is scaled to 720x405.
DASHBOARD_MENU_ROWS = (
    (112, 224),
    (236, 348),
    (360, 472),
    (484, 596),
    (608, 720),
    (732, 844),
    (856, 968),
)


def _safe_call(function, fallback=N_("Unknown")):
    if function is None:
        return fallback
    try:
        value = function()
    except Exception:
        return fallback
    value = str(value or "").strip()
    return value or fallback


_SERVER_STATUS_CANONICAL = {
    "active": N_("On"),
    "enabled": N_("On"),
    "online": N_("Connected"),
    "busy": N_("Busy"),
    "stale": N_("Warning"),
    "expired": N_("Off"),
    "disabled": N_("Off"),
    "banned": N_("Blocked"),
    "blocked": N_("Blocked"),
    "inactive": N_("Off"),
    "invalid": N_("Invalid"),
}

M3U_HEALTH_DETAILS_TEMPLATE = N_(
    "Read time: {} ms\n"
    "Source type: {}\n"
    "Size: {} bytes\n"
    "Channels: {}\n"
    "Categories: {}\n"
    "Cache age: {}\n"
    "Last modified: {}\n\n"
    "{}"
)
STALKER_HEALTH_DETAILS_TEMPLATE = N_(
    "Latency: {} ms\n"
    "Account status: {}\n"
    "Protocol: {}\n"
    "Output format: {}\n\n"
    "{}"
)


def _localized_runtime_text(value, fallback=N_("Unknown")):
    """Translate plugin-owned status text without changing server data."""
    text = str(value or fallback).strip() or str(fallback)
    canonical = _SERVER_STATUS_CANONICAL.get(text.casefold())
    return _(canonical or text)


def device_snapshot():
    brand = _safe_call(getMachineBrand, "")
    model = _safe_call(getMachineName, "Enigma2")
    distro = _safe_call(getImageDistro, "Enigma2")
    image_version = _safe_call(getImageVersion, "")
    return {
        "device": "{} {}".format(brand, model).strip(),
        "image": "{} {}".format(distro, image_version).strip(),
        "python": platform.python_version(),
    }


def _desktop_size():
    try:
        size = getDesktop(0).size()
        width = int(size.width())
        height = int(size.height())
        if width > 0 and height > 0:
            return width, height
    except Exception:
        pass
    return 1280, 720


def _scale():
    width, height = _desktop_size()
    factor = min(float(width) / 1920.0, float(height) / 1080.0)

    def px(value):
        return max(1, int(round(value * factor)))

    return width, height, px


def _set_dynamic_lines(component, values, fallback_chars=40, max_lines=12):
    """Fit a bounded set of deliberate rows without flattening the layout."""
    if isinstance(values, str):
        values = values.splitlines()
    rendered = []
    limit = max(1, int(max_lines))
    for index, value in enumerate(values or ()):
        if index >= limit:
            break
        rendered.append(
            ellipsize_dynamic_text(
                component,
                value,
                fallback_chars=fallback_chars,
            )
        )
    component.setText("\n".join(rendered))
    return "\n".join(rendered)


def _ellipsize_inset_text(component, value, fallback_chars=40):
    """Fit one line while preserving deliberate leading-space padding."""
    text = "" if value is None else str(value)
    leading_count = len(text) - len(text.lstrip(" "))
    leading = text[:leading_count]
    return ellipsize_dynamic_text(
        component,
        text[leading_count:],
        fallback_chars=fallback_chars,
        prefix=leading,
    )


def _welcome_skin():
    width, height, px = _scale()
    button_y = px(550)
    button_height = px(288)
    # The centre card is wider in the artwork; focus frames follow it exactly.
    button_width = (px(340), px(351), px(340))
    button_x = (px(705), px(1082), px(1472))
    footer_y = px(922)

    return """
<screen name="GTIPTVPlayerProScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="welcome_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="1,1" font="Regular;1"
            transparent="1" zPosition="1" />
    <widget name="brand_panel" position="0,0" size="1,1" font="Regular;1"
            transparent="1" zPosition="1" />
    <widget name="brand_mark" position="0,0" size="1,1" font="Regular;1"
            transparent="1" zPosition="1" />
    <widget name="header" position="{header_x},{header_y}"
            size="{header_w},{header_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="eyebrow" position="{left_x},{eyebrow_y}"
            size="{left_w},{eyebrow_h}" font="Regular;{eyebrow_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="welcome_title" position="{left_x},{welcome_y}"
            size="{left_w},{welcome_h}" font="Regular;{welcome_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="subtitle" position="{left_x},{subtitle_y}"
            size="{left_w},{subtitle_h}" font="Regular;{subtitle_font}"
            foregroundColor="#C6D0E0" transparent="1" zPosition="2"
            valign="top" halign="left" />
    <widget name="device_info" position="{left_x},{info_y}"
            size="{left_w},{info_h}" font="Regular;{info_font}"
            foregroundColor="#A8B4C8" transparent="1" zPosition="2"
            valign="center" halign="left" />

    <widget name="right_panel" position="0,0" size="1,1" font="Regular;1"
            transparent="1" zPosition="1" />
    <widget name="account_caption" position="{account_caption_x},{account_caption_y}"
            size="{account_caption_w},{account_caption_h}" font="Regular;{caption_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="account_card" position="{account_text_x},{account_text_y}"
            size="{account_text_w},{account_text_h}" font="Regular;{account_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="account_status" position="{account_text_x},{account_status_y}"
            size="{account_text_w},{account_status_h}" font="Regular;{status_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="2"
            valign="top" halign="left" />
    <widget name="account_expiry" position="{account_text_x},{account_expiry_y}"
            size="{account_text_w},{account_detail_h}" font="Regular;{detail_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="account_expiry_green" position="{account_text_x},{account_expiry_y}"
            size="{account_text_w},{account_detail_h}" font="Regular;{detail_font}"
            foregroundColor="#39FF74" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="account_expiry_yellow" position="{account_text_x},{account_expiry_y}"
            size="{account_text_w},{account_detail_h}" font="Regular;{detail_font}"
            foregroundColor="#FACC15" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="account_expiry_red" position="{account_text_x},{account_expiry_y}"
            size="{account_text_w},{account_detail_h}" font="Regular;{detail_font}"
            foregroundColor="#FF477E" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="account_connection" position="{account_text_x},{account_connection_y}"
            size="{account_text_w},{account_detail_h}" font="Regular;{detail_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="account_connection_green" position="{account_text_x},{account_connection_y}"
            size="{account_text_w},{account_detail_h}" font="Regular;{detail_font}"
            foregroundColor="#39FF74" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="account_connection_yellow" position="{account_text_x},{account_connection_y}"
            size="{account_text_w},{account_detail_h}" font="Regular;{detail_font}"
            foregroundColor="#FACC15" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="account_connection_red" position="{account_text_x},{account_connection_y}"
            size="{account_text_w},{account_detail_h}" font="Regular;{detail_font}"
            foregroundColor="#FF477E" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="account_accent" position="0,0" size="1,1" font="Regular;1"
            transparent="1" zPosition="1" />
    <widget name="account_meta" position="{account_meta_x},{account_meta_y}"
            size="{account_meta_w},{account_meta_h}" font="Regular;{meta_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="2"
            valign="center" halign="right" />

    <widget name="connect_button" position="{button_0_x},{button_label_y}"
            size="{button_0_width},{button_label_h}" font="Regular;{button_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="center" />
    <widget name="accounts_button" position="{button_1_x},{button_label_y}"
            size="{button_1_width},{button_label_h}" font="Regular;{button_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="center" />
    <widget name="add_button" position="{button_2_x},{button_label_y}"
            size="{button_2_width},{button_label_h}" font="Regular;{button_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="center" />
    <widget name="focus_0" position="{button_0_x},{focus_y}"
            size="{button_0_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_1" position="{button_1_x},{focus_y}"
            size="{button_1_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_2" position="{button_2_x},{focus_y}"
            size="{button_2_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_0_top" position="{button_0_x},{button_y}"
            size="{button_0_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_0_left" position="{button_0_x},{button_y}"
            size="{focus_h},{button_height}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_0_right" position="{focus_0_right_x},{button_y}"
            size="{focus_h},{button_height}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_1_top" position="{button_1_x},{button_y}"
            size="{button_1_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_1_left" position="{button_1_x},{button_y}"
            size="{focus_h},{button_height}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_1_right" position="{focus_1_right_x},{button_y}"
            size="{focus_h},{button_height}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_2_top" position="{button_2_x},{button_y}"
            size="{button_2_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_2_left" position="{button_2_x},{button_y}"
            size="{focus_h},{button_height}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="focus_2_right" position="{focus_2_right_x},{button_y}"
            size="{focus_h},{button_height}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="hint" position="{hint_x},{hint_y}"
            size="{hint_w},{hint_h}" font="Regular;{hint_font}"
            foregroundColor="#A8B4C8" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="footer" position="{footer_x},{footer_y}"
            size="{footer_w},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            transparent="1" zPosition="2"
            valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        header_x=px(212),
        header_y=px(105),
        header_w=px(370),
        header_h=px(105),
        title_font=px(26),
        left_x=px(92),
        left_w=px(470),
        eyebrow_y=px(250),
        eyebrow_h=px(42),
        eyebrow_font=px(21),
        welcome_y=px(292),
        welcome_h=px(90),
        welcome_font=px(56),
        subtitle_y=px(390),
        subtitle_h=px(120),
        subtitle_font=px(28),
        info_y=px(800),
        info_h=px(55),
        info_font=px(18),
        account_caption_x=px(720),
        account_caption_y=px(105),
        account_caption_w=px(1050),
        account_caption_h=px(42),
        caption_font=px(22),
        account_text_x=px(1050),
        account_text_y=px(225),
        account_text_w=px(680),
        account_text_h=px(100),
        account_font=px(32),
        account_status_y=px(330),
        account_status_h=px(48),
        status_font=px(24),
        account_expiry_y=px(378),
        account_connection_y=px(424),
        account_detail_h=px(42),
        detail_font=px(22),
        account_meta_x=px(1600),
        account_meta_y=px(115),
        account_meta_w=px(170),
        account_meta_h=px(42),
        meta_font=px(19),
        button_y=button_y,
        button_label_y=px(735),
        button_label_h=px(72),
        button_0_x=button_x[0],
        button_1_x=button_x[1],
        button_2_x=button_x[2],
        button_0_width=button_width[0],
        button_1_width=button_width[1],
        button_2_width=button_width[2],
        button_height=button_height,
        focus_0_right_x=button_x[0] + button_width[0] - px(6),
        focus_1_right_x=button_x[1] + button_width[1] - px(6),
        focus_2_right_x=button_x[2] + button_width[2] - px(6),
        button_font=px(25),
        focus_y=button_y + button_height - px(6),
        focus_h=px(6),
        hint_x=px(720),
        hint_y=px(850),
        hint_w=px(1050),
        hint_h=px(45),
        hint_font=px(18),
        footer_x=px(65),
        footer_y=footer_y,
        footer_w=px(1790),
        footer_h=px(110),
        footer_font=px(20),
    )


def _dashboard_skin():
    width, height, px = _scale()
    menu_widgets = []
    for index, (top, bottom) in enumerate(DASHBOARD_MENU_ROWS):
        menu_widgets.append(
            '<widget name="focus_{i}" position="{x},{line_y}" size="{w},{line_h}" '
            'font="Regular;1" backgroundColor="#E600FF" zPosition="4" />'
            '<widget name="focus_edge_{i}" position="{x},{y}" size="{line_h},{h}" '
            'font="Regular;1" backgroundColor="#E600FF" zPosition="4" />'
            '<widget name="card_{i}" position="{text_x},{text_y}" size="{text_w},{text_h}" '
            'font="Regular;{font}" foregroundColor="#F8FAFC" noWrap="1" '
            'transparent="1" zPosition="3" valign="center" halign="left" />'.format(
                i=index, x=px(28), y=px(top), w=px(448), h=px(bottom-top),
                line_y=px(bottom-6), line_h=px(6),
                text_x=px(60), text_y=px(top+12), text_w=px(402),
                text_h=px(bottom-top-24),
                font=px(31 if CONTENT_ITEMS[index][2] == "continue" else 44),
            )
        )
    return """
<screen name="GTDashboardScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#020617">
    <widget name="dashboard_bg" position="0,0" size="{width},{height}"
            zPosition="0" />

    <widget name="brand_gt" position="{brand_gt_x},{header_y}"
            size="{brand_gt_w},{header_h}" font="Regular;{brand_font}"
            foregroundColor="#D946EF" transparent="1" zPosition="5"
            valign="center" halign="left" />
    <widget name="header" position="{brand_x},{header_y}"
            size="{brand_w},{header_h}" font="Regular;{brand_font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="5"
            valign="center" halign="left" />
    <widget name="account_bar" position="{account_x},{header_y}"
            size="{account_w},{header_h}" font="Regular;{top_font}"
            foregroundColor="#F8FAFC" transparent="1" noWrap="1" zPosition="5"
            valign="center" halign="center" />
    <widget name="status_label" position="{status_label_x},{header_y}"
            size="{status_label_w},{header_h}" font="Regular;{status_font}"
            foregroundColor="#BBC5D8" backgroundColor="#030817"
            transparent="1" zPosition="5"
            valign="center" halign="right" />
    <widget name="server_url" position="{server_x},{header_y}"
            size="{server_w},{header_h}" font="Regular;{server_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="5"
            valign="center" halign="left" />
    <widget name="status" position="{status_x},{header_y}"
            size="{status_w},{header_h}" font="Regular;{status_font}"
            foregroundColor="#39FF74" transparent="1" zPosition="5"
            valign="center" halign="center" />
    <widget name="clock" position="{clock_x},{header_y}"
            size="{clock_w},{header_h}" font="Regular;{clock_font}"
            foregroundColor="#F8FAFC" transparent="1" zPosition="5"
            valign="center" halign="right" />

    {menu_widgets}
    <widget name="archive_backdrop" position="{archive_panel_x},{archive_panel_y}"
            size="{archive_panel_w},{archive_panel_h}" font="Regular;1"
            backgroundColor="#020617" transparent="0" zPosition="1" />
    <widget name="archive_hero" position="{archive_panel_x},{archive_panel_y}"
            size="{archive_panel_w},{archive_panel_h}" scale="1" zPosition="2" />

    <widget name="preview_title" position="{preview_x},{preview_title_y}"
            size="{preview_w},{preview_title_h}" font="Regular;{preview_title_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="4"
            valign="center" halign="left" />
    <widget name="continue_preview_title" position="{preview_x},{preview_title_y}"
            size="{preview_w},{preview_title_h}"
            font="Regular;{continue_preview_title_font}" noWrap="1"
            foregroundColor="#FFFFFF" transparent="1" zPosition="4"
            valign="center" halign="left" />
    <widget name="preview_description" position="{preview_x},{preview_desc_y}"
            size="{preview_w},{preview_desc_h}" font="Regular;{preview_desc_font}"
            foregroundColor="#E2E8F0" transparent="1" zPosition="4"
            valign="top" halign="left" />

    <widget name="quick_0" position="{quick_0_x},{quick_y}" size="{quick_w},{quick_h}"
            font="Regular;{quick_font}" foregroundColor="#FFFFFF" transparent="1"
            zPosition="4" valign="bottom" halign="center" />
    <widget name="quick_1" position="{quick_1_x},{quick_y}" size="{quick_w},{quick_h}"
            font="Regular;{quick_font}" foregroundColor="#FFFFFF" transparent="1"
            zPosition="4" valign="bottom" halign="center" />
    <widget name="quick_2" position="{quick_2_x},{quick_y}"
            size="{quick_w},{quick_h}"
            font="Regular;{continue_quick_font}" foregroundColor="#FFFFFF"
            transparent="1"
            zPosition="4" valign="bottom" halign="center" />
    <widget name="quick_3" position="{quick_3_x},{quick_y}" size="{quick_3_w},{quick_h}"
            font="Regular;{quick_font}" foregroundColor="#FFFFFF"
            transparent="1"
            zPosition="4" valign="bottom" halign="center" />
    <widget name="quick_focus_0" position="{quick_line_0_x},{quick_line_y}"
            size="{quick_line_w},{quick_line_h}" font="Regular;1"
            backgroundColor="#D946EF" transparent="0" zPosition="5" />
    <widget name="quick_focus_1" position="{quick_line_1_x},{quick_line_y}"
            size="{quick_line_w},{quick_line_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="5" />
    <widget name="quick_focus_2" position="{quick_line_2_x},{quick_line_y}"
            size="{quick_line_w},{quick_line_h}" font="Regular;1"
            backgroundColor="#FF477E" transparent="0" zPosition="5" />
    <widget name="quick_focus_3" position="{quick_line_3_x},{quick_line_y}"
            size="{quick_line_w},{quick_line_h}" font="Regular;1"
            backgroundColor="#FACC15" transparent="0" zPosition="5" />

    <widget name="hero_glow_top" position="{hero_glow_x},{hero_glow_y}"
            size="{hero_glow_w},{glow_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="6" />
    <widget name="hero_glow_bottom" position="{hero_glow_x},{hero_glow_bottom_y}"
            size="{hero_glow_w},{glow_h}" font="Regular;1"
            backgroundColor="#E600FF" transparent="0" zPosition="6" />
    <widget name="hero_glow_left" position="{hero_glow_x},{hero_glow_y}"
            size="{glow_h},{hero_glow_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="6" />
    <widget name="hero_glow_right" position="{hero_glow_right_x},{hero_glow_y}"
            size="{glow_h},{hero_glow_h}" font="Regular;1"
            backgroundColor="#E600FF" transparent="0" zPosition="6" />

    <widget name="quick_glow_0_top" position="{quick_glow_0_x},{quick_glow_y}"
            size="{quick_glow_0_w},{glow_h}" font="Regular;1"
            backgroundColor="#E600FF" transparent="0" zPosition="6" />
    <widget name="quick_glow_0_bottom" position="{quick_glow_0_x},{quick_glow_bottom_y}"
            size="{quick_glow_0_w},{glow_h}" font="Regular;1"
            backgroundColor="#E600FF" transparent="0" zPosition="6" />
    <widget name="quick_glow_0_left" position="{quick_glow_0_x},{quick_glow_y}"
            size="{glow_h},{quick_glow_h}" font="Regular;1"
            backgroundColor="#E600FF" transparent="0" zPosition="6" />
    <widget name="quick_glow_0_right" position="{quick_glow_0_right_x},{quick_glow_y}"
            size="{glow_h},{quick_glow_h}" font="Regular;1"
            backgroundColor="#E600FF" transparent="0" zPosition="6" />

    <widget name="quick_glow_1_top" position="{quick_glow_1_x},{quick_glow_y}"
            size="{quick_glow_1_w},{glow_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="6" />
    <widget name="quick_glow_1_bottom" position="{quick_glow_1_x},{quick_glow_bottom_y}"
            size="{quick_glow_1_w},{glow_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="6" />
    <widget name="quick_glow_1_left" position="{quick_glow_1_x},{quick_glow_y}"
            size="{glow_h},{quick_glow_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="6" />
    <widget name="quick_glow_1_right" position="{quick_glow_1_right_x},{quick_glow_y}"
            size="{glow_h},{quick_glow_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="6" />

    <widget name="quick_glow_2_top" position="{quick_glow_2_x},{quick_glow_y}"
            size="{quick_glow_2_w},{glow_h}" font="Regular;1"
            backgroundColor="#FF477E" transparent="0" zPosition="6" />
    <widget name="quick_glow_2_bottom" position="{quick_glow_2_x},{quick_glow_bottom_y}"
            size="{quick_glow_2_w},{glow_h}" font="Regular;1"
            backgroundColor="#FF477E" transparent="0" zPosition="6" />
    <widget name="quick_glow_2_left" position="{quick_glow_2_x},{quick_glow_y}"
            size="{glow_h},{quick_glow_h}" font="Regular;1"
            backgroundColor="#FF477E" transparent="0" zPosition="6" />
    <widget name="quick_glow_2_right" position="{quick_glow_2_right_x},{quick_glow_y}"
            size="{glow_h},{quick_glow_h}" font="Regular;1"
            backgroundColor="#FF477E" transparent="0" zPosition="6" />

    <widget name="quick_glow_3_top" position="{quick_glow_3_x},{quick_glow_y}"
            size="{quick_glow_3_w},{glow_h}" font="Regular;1"
            backgroundColor="#FACC15" transparent="0" zPosition="6" />
    <widget name="quick_glow_3_bottom" position="{quick_glow_3_x},{quick_glow_bottom_y}"
            size="{quick_glow_3_w},{glow_h}" font="Regular;1"
            backgroundColor="#FACC15" transparent="0" zPosition="6" />
    <widget name="quick_glow_3_left" position="{quick_glow_3_x},{quick_glow_y}"
            size="{glow_h},{quick_glow_h}" font="Regular;1"
            backgroundColor="#FACC15" transparent="0" zPosition="6" />
    <widget name="quick_glow_3_right" position="{quick_glow_3_right_x},{quick_glow_y}"
            size="{glow_h},{quick_glow_h}" font="Regular;1"
            backgroundColor="#FACC15" transparent="0" zPosition="6" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#F8FAFC"
            backgroundColor="#030817" transparent="1" zPosition="4"
            valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        header_y=px(15),
        header_h=px(70),
        brand_gt_x=px(40),
        brand_gt_w=px(72),
        brand_x=px(112),
        brand_w=px(500),
        brand_font=px(46),
        account_x=px(620),
        account_w=px(330),
        top_font=px(34),
        status_label_x=px(975),
        status_label_w=px(155),
        server_x=px(1145),
        server_w=px(430),
        server_font=px(29),
        status_x=px(1585),
        status_w=px(165),
        status_font=px(31),
        clock_x=px(1770),
        clock_w=px(115),
        clock_font=px(39),
        menu_widgets="\n".join(menu_widgets),
        # Fill the card; the decoder keeps the 3:1 artwork proportional and
        # ePixmap clips only the surrounding background to this viewport.
        # Derive all edges from the scaled border to avoid one-pixel seams.
        archive_panel_x=px(507)+px(6),
        archive_panel_y=px(104)+px(6),
        archive_panel_w=px(1885)-px(507)-px(6),
        archive_panel_h=px(539)-px(104)-px(6),
        preview_x=px(555),
        preview_w=px(700),
        preview_title_y=px(205),
        preview_title_h=px(90),
        preview_title_font=px(76),
        continue_preview_title_font=px(48),
        preview_desc_y=px(300),
        preview_desc_h=px(110),
        preview_desc_font=px(36),
        quick_0_x=px(512),
        quick_1_x=px(861),
        quick_2_x=px(1219),
        quick_3_x=px(1575),
        quick_3_w=px(311),
        quick_y=px(820),
        quick_w=px(320),
        quick_h=px(110),
        quick_font=px(36),
        continue_quick_font=px(27),
        quick_line_0_x=px(632),
        quick_line_1_x=px(985),
        quick_line_2_x=px(1342),
        quick_line_3_x=px(1690),
        quick_line_y=px(935),
        quick_line_w=px(80),
        quick_line_h=px(5),
        glow_h=px(6),
        hero_glow_x=px(507),
        hero_glow_y=px(104),
        hero_glow_w=px(1384),
        hero_glow_h=px(441),
        hero_glow_bottom_y=px(539),
        hero_glow_right_x=px(1885),
        quick_glow_y=px(568),
        quick_glow_h=px(413),
        quick_glow_bottom_y=px(975),
        quick_glow_0_x=px(507),
        quick_glow_0_w=px(330),
        quick_glow_0_right_x=px(831),
        quick_glow_1_x=px(856),
        quick_glow_1_w=px(338),
        quick_glow_1_right_x=px(1188),
        quick_glow_2_x=px(1214),
        quick_glow_2_w=px(337),
        quick_glow_2_right_x=px(1545),
        quick_glow_3_x=px(1570),
        quick_glow_3_w=px(321),
        quick_glow_3_right_x=px(1885),
        footer_y=px(982),
        footer_h=height - px(982),
        footer_font=px(30),
    )


def _feature_skin():
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    footer_y = height - px(92)
    tmdb_h = max(1, px(96))
    tmdb_w = max(1, int(round(tmdb_h * 330.0 / 238.0)))
    return """
<screen name="GTFeatureScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="header" position="{margin},{header_y}" size="{content_width},{header_h}"
            font="Regular;{title_font}" foregroundColor="#FFFFFF"
            transparent="1"
            valign="center" halign="left" />
    <widget name="tmdb_logo" position="{tmdb_x},{tmdb_y}"
            size="{tmdb_w},{tmdb_h}" alphatest="blend" scale="1"
            zPosition="3" />
    <widget name="message_panel" position="{margin},{message_y}"
            size="{content_width},{message_h}" font="Regular;1"
            backgroundColor="#0D1627" transparent="0" />
    <widget name="message" position="{message_x},{message_text_y}"
            size="{message_w},{message_text_h}" font="Regular;{message_font}"
            foregroundColor="#D8E1EF" backgroundColor="#0D1627"
            transparent="1" valign="top" halign="center" />
    <widget name="message_scroll_track" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#25344D" transparent="0" />
    <widget name="message_scroll_thumb" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="4" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        margin=margin,
        content_width=content_width,
        header_y=px(30),
        header_h=px(70),
        title_font=px(38),
        tmdb_x=width - margin - tmdb_w,
        tmdb_y=px(18),
        tmdb_w=tmdb_w,
        tmdb_h=tmdb_h,
        message_y=px(150),
        message_h=px(720),
        message_x=margin + px(36),
        message_text_y=px(174),
        message_w=content_width - px(72),
        message_text_h=px(672),
        message_font=px(28),
        scroll_x=margin + content_width - px(18),
        scroll_y=px(170),
        scroll_w=max(1, px(8)),
        scroll_h=px(680),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _weather_settings_skin():
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    footer_y = height - px(92)
    rows = []
    row_y = (220, 355, 490, 625)
    for index, y_value in enumerate(row_y):
        rows.append(
            """
    <widget name="row_bg_{index}" position="{margin},{row_y}"
            size="{content_width},{row_h}" font="Regular;1"
            backgroundColor="#0D1627" transparent="0" zPosition="1" />
    <widget name="focus_{index}" position="{margin},{focus_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="2" />
    <widget name="label_{index}" position="{label_x},{row_y}"
            size="{label_w},{row_h}" font="Regular;{label_font}"
            foregroundColor="#C8D2E3" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="value_{index}" position="{value_x},{row_y}"
            size="{value_w},{row_h}" font="Regular;{value_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="3"
            valign="center" halign="right" />""".format(
                index=index,
                margin=margin,
                content_width=content_width,
                row_y=px(y_value),
                row_h=px(105),
                focus_y=px(y_value + 100),
                focus_w=content_width,
                focus_h=px(5),
                label_x=margin + px(38),
                label_w=px(760),
                label_font=px(27),
                value_x=margin + px(830),
                value_w=content_width - px(870),
                value_font=px(27),
            )
        )
    return """
<screen name="GTWeatherSettingsScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{margin},{header_y}"
            size="{content_width},{header_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" />
    <widget name="subtitle" position="{margin},{subtitle_y}"
            size="{content_width},{subtitle_h}" font="Regular;{subtitle_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" />
    {rows}
    <widget name="message" position="{margin},{message_y}"
            size="{content_width},{message_h}" font="Regular;{message_font}"
            foregroundColor="#9AA8BE" transparent="1"
            valign="center" halign="left" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        accent_h=px(5),
        margin=margin,
        content_width=content_width,
        header_y=px(35),
        header_h=px(70),
        title_font=px(38),
        subtitle_y=px(115),
        subtitle_h=px(60),
        subtitle_font=px(22),
        rows="".join(rows),
        message_y=px(770),
        message_h=px(95),
        message_font=px(21),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _weather_location_search_skin():
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    row_width = content_width - px(24)
    footer_y = height - px(92)
    rows = []
    for index in range(7):
        row_y = 220 + (index * 88)
        rows.append(
            """
    <widget name="row_bg_{index}" position="{margin},{row_y}"
            size="{row_width},{row_h}" font="Regular;1"
            backgroundColor="#0D1627" transparent="0" zPosition="1" />
    <widget name="focus_{index}" position="{margin},{row_y}"
            size="{row_width},{row_h}" font="Regular;1"
            backgroundColor="#17536A" transparent="0" zPosition="2" />
    <widget name="marker_{index}" position="{margin},{row_y}"
            size="{marker_w},{row_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="3" />
    <widget name="result_{index}" position="{result_x},{row_y}"
            size="{result_w},{row_h}" font="Regular;{result_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="4"
            valign="center" halign="left" />""".format(
                index=index,
                margin=margin,
                row_y=px(row_y),
                row_width=row_width,
                row_h=px(78),
                marker_w=px(8),
                result_x=margin + px(30),
                result_w=row_width - px(48),
                result_font=px(26),
            )
        )
    return """
<screen name="GTWeatherLocationSearchScreen" position="0,0"
        size="{width},{height}" flags="wfNoBorder"
        backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6"
            transparent="0" zPosition="1" />
    <widget name="header" position="{margin},{header_y}"
            size="{content_width},{header_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="subtitle" position="{margin},{subtitle_y}"
            size="{content_width},{subtitle_h}" font="Regular;{subtitle_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="query" position="{margin},{query_y}"
            size="{content_width},{query_h}" font="Regular;{query_font}"
            foregroundColor="#FFFFFF" backgroundColor="#0D1627"
            transparent="0" zPosition="2" valign="center" halign="left" />
    {rows}
    <widget name="location_scroll_track" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="location_scroll_thumb" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="message" position="{margin},{message_y}"
            size="{content_width},{message_h}" font="Regular;{message_font}"
            foregroundColor="#9AA8BE" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        accent_h=px(5),
        margin=margin,
        content_width=content_width,
        header_y=px(30),
        header_h=px(65),
        title_font=px(38),
        subtitle_y=px(100),
        subtitle_h=px(45),
        subtitle_font=px(22),
        query_y=px(150),
        query_h=px(55),
        query_font=px(25),
        rows="".join(rows),
        scroll_x=margin + row_width + px(8),
        scroll_y=px(220),
        scroll_w=px(8),
        scroll_h=px(606),
        message_y=px(835),
        message_h=px(115),
        message_font=px(21),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _player_settings_skin():
    """Full-screen codec and metadata settings layout."""
    width, height, px = _scale()
    row_y = tuple(258 + (index * 62) for index in range(9))
    rows = []
    for index, y_value in enumerate(row_y):
        rows.append(
            """
    <widget name="row_bg_{index}" position="{row_x},{row_y}"
            size="{row_w},{row_h}" font="Regular;1"
            backgroundColor="#101B30" transparent="0" zPosition="2" />
    <widget name="row_focus_{index}" position="{row_x},{row_y}"
            size="{row_w},{row_h}" font="Regular;1"
            backgroundColor="#17536A" transparent="0" zPosition="3" />
    <widget name="row_marker_{index}" position="{row_x},{row_y}"
            size="{marker_w},{row_h}" font="Regular;1"
            backgroundColor="#16C9F4" transparent="0" zPosition="4" />
    <widget name="row_label_{index}" position="{label_x},{row_y}"
            size="{label_w},{row_h}" font="Regular;{row_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="5"
            valign="center" halign="left" />
    <widget name="value_bg_{index}" position="{value_x},{value_y}"
            size="{value_w},{value_h}" font="Regular;1"
            backgroundColor="#091426" transparent="0" zPosition="4" />
    <widget name="row_value_{index}" position="{value_x},{value_y}"
            size="{value_w},{value_h}" font="Regular;{value_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="5"
            valign="center" halign="center" />""".format(
                index=index,
                row_x=px(105),
                row_y=px(y_value),
                row_w=px(1710),
                row_h=px(50),
                marker_w=px(8),
                label_x=px(145),
                label_w=px(880),
                row_font=font_px(
                    px,
                    23,
                    max_height=50,
                    vertical_padding=8,
                ),
                value_x=px(1115),
                value_y=px(y_value + 5),
                value_w=px(660),
                value_h=px(40),
                value_font=font_px(
                    px,
                    22,
                    max_height=40,
                    vertical_padding=6,
                ),
            )
        )
    footer_y = height - px(86)
    return """
<screen name="GTPlayerSettingsScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="{border},{border}"
            size="{accent_w},{accent_h}" font="Regular;1"
            backgroundColor="#8638FF" transparent="0" zPosition="1" />
    <widget name="header" position="{header_x},{header_y}"
            size="{header_w},{header_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="brand" position="{brand_x},{header_y}"
            size="{brand_w},{header_h}" font="Regular;{brand_font}"
            foregroundColor="#16C9F4" transparent="1" zPosition="2"
            valign="center" halign="right" />
    <widget name="panel" position="{panel_x},{panel_y}"
            size="{panel_w},{panel_h}" font="Regular;1"
            backgroundColor="#07101F" transparent="0" zPosition="1" />
    <widget name="player_tab_bg" position="{tab_left_x},{tab_y}"
            size="{tab_w},{tab_h}" font="Regular;1"
            backgroundColor="#16AEE0" transparent="0" zPosition="2" />
    <widget name="player_tab" position="{tab_left_x},{tab_y}"
            size="{tab_w},{tab_h}" font="Regular;{tab_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="3"
            valign="center" halign="center" />
    <widget name="language_tab_bg" position="{tab_right_x},{tab_y}"
            size="{tab_w},{tab_h}" font="Regular;1"
            backgroundColor="#0B1628" transparent="0" zPosition="2" />
    <widget name="language_tab" position="{tab_right_x},{language_y}"
            size="{tab_w},{language_h}" font="Regular;{tab_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="3"
            valign="center" halign="center" />
    <widget name="language_soon" position="{tab_right_x},{soon_y}"
            size="{tab_w},{soon_h}" font="Regular;{soon_font}"
            foregroundColor="#8D9AAF" transparent="1" zPosition="3"
            valign="center" halign="center" />
    {rows}
    <widget name="message" position="{message_x},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#16C9F4" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="footer" position="0,{footer_y}"
            size="{width},{footer_h}" font="Regular;{footer_font}"
            foregroundColor="#C8D2E3" backgroundColor="#080E1A"
            transparent="0" zPosition="4" valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        border=px(14),
        accent_w=width - px(28),
        accent_h=px(3),
        header_x=px(72),
        header_y=px(35),
        header_w=px(700),
        header_h=px(75),
        title_font=font_px(
            px,
            38,
            role="title",
            max_height=75,
            vertical_padding=10,
        ),
        brand_x=px(1350),
        brand_w=px(500),
        brand_font=font_px(
            px,
            22,
            role="title",
            max_height=75,
            vertical_padding=10,
        ),
        panel_x=px(70),
        panel_y=px(120),
        panel_w=px(1780),
        panel_h=px(800),
        tab_left_x=px(100),
        tab_right_x=px(970),
        tab_y=px(145),
        tab_w=px(850),
        tab_h=px(90),
        tab_font=font_px(
            px,
            29,
            role="title",
            max_height=90,
            vertical_padding=14,
        ),
        language_y=px(151),
        language_h=px(52),
        soon_y=px(198),
        soon_h=px(30),
        soon_font=font_px(
            px,
            18,
            max_height=30,
            vertical_padding=5,
        ),
        rows="".join(rows),
        message_x=px(110),
        message_y=px(850),
        message_w=px(1690),
        message_h=px(62),
        message_font=font_px(
            px,
            21,
            max_height=62,
            vertical_padding=10,
        ),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _appearance_settings_skin():
    """List-themed appearance page for text and cinematic VOD settings."""
    width, height, px = _scale()
    footer_y = height - px(86)
    rows = []
    for index in range(2):
        row_y = px(290 + (index * 92))
        rows.append(
            """
    <widget name="row_bg_{index}" position="{row_x},{row_y}"
            size="{row_w},{row_h}" font="Regular;1"
            backgroundColor="#101B30" transparent="0" zPosition="2" />
    <widget name="row_focus_{index}" position="{row_x},{row_y}"
            size="{row_w},{row_h}" font="Regular;1"
            backgroundColor="#17536A" transparent="0" zPosition="3" />
    <widget name="row_marker_{index}" position="{row_x},{row_y}"
            size="{marker_w},{row_h}" font="Regular;1"
            backgroundColor="#16C9F4" transparent="0" zPosition="4" />
    <widget name="row_label_{index}" position="{label_x},{row_y}"
            size="{label_w},{row_h}" font="Regular;{row_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="5"
            valign="center" halign="left" noWrap="1" />
    <widget name="value_bg_{index}" position="{value_x},{value_y}"
            size="{value_w},{value_h}" font="Regular;1"
            backgroundColor="#091426" transparent="0" zPosition="4" />
    <widget name="row_value_{index}" position="{value_x},{value_y}"
            size="{value_w},{value_h}" font="Regular;{value_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="5"
            valign="center" halign="center" noWrap="1" />""".format(
                index=index,
                row_x=px(105),
                row_y=row_y,
                row_w=px(1710),
                row_h=px(72),
                marker_w=px(8),
                label_x=px(145),
                label_w=px(880),
                row_font=font_px(
                    px, 25, max_height=72, vertical_padding=12
                ),
                value_x=px(1115),
                value_y=row_y + px(8),
                value_w=px(660),
                value_h=px(56),
                value_font=font_px(
                    px, 24, max_height=56, vertical_padding=10
                ),
            )
        )
    return """
<screen name="GTAppearanceSettingsScreen" position="0,0"
        size="{width},{height}" flags="wfNoBorder"
        backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="{border},{border}"
            size="{accent_w},{accent_h}" font="Regular;1"
            backgroundColor="#8638FF" transparent="0" zPosition="1" />
    <widget name="header" position="{header_x},{header_y}"
            size="{header_w},{header_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="left" noWrap="1" />
    <widget name="brand" position="{brand_x},{header_y}"
            size="{brand_w},{header_h}" font="Regular;{brand_font}"
            foregroundColor="#16C9F4" transparent="1" zPosition="2"
            valign="center" halign="right" noWrap="1" />
    <widget name="panel" position="{panel_x},{panel_y}"
            size="{panel_w},{panel_h}" font="Regular;1"
            backgroundColor="#07101F" transparent="0" zPosition="1" />
    <widget name="section" position="{section_x},{section_y}"
            size="{section_w},{section_h}" font="Regular;{section_font}"
            foregroundColor="#FFFFFF" backgroundColor="#16AEE0"
            transparent="0" zPosition="2" valign="center"
            halign="center" noWrap="1" />
    {rows}
    <widget name="message" position="{message_x},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#16C9F4" transparent="1" zPosition="3"
            valign="center" halign="left" noWrap="1" />
    <widget name="footer" position="0,{footer_y}"
            size="{width},{footer_h}" font="Regular;{footer_font}"
            foregroundColor="#C8D2E3" backgroundColor="#080E1A"
            transparent="0" zPosition="4" valign="center"
            halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        border=px(14),
        accent_w=width - px(28),
        accent_h=px(3),
        header_x=px(72),
        header_y=px(35),
        header_w=px(900),
        header_h=px(75),
        title_font=font_px(
            px, 38, role="title", max_height=75, vertical_padding=10
        ),
        brand_x=px(1350),
        brand_w=px(500),
        brand_font=font_px(
            px, 22, role="title", max_height=75, vertical_padding=10
        ),
        panel_x=px(70),
        panel_y=px(120),
        panel_w=px(1780),
        panel_h=px(800),
        section_x=px(100),
        section_y=px(155),
        section_w=px(1720),
        section_h=px(78),
        section_font=font_px(
            px, 29, role="title", max_height=78, vertical_padding=14
        ),
        rows="".join(rows),
        message_x=px(110),
        message_y=px(835),
        message_w=px(1690),
        message_h=px(70),
        message_font=font_px(
            px, 21, max_height=70, vertical_padding=12
        ),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _accounts_skin():
    width, height, px = _scale()
    row_y = tuple(190 + (index * 67) for index in range(10))
    focus_y = tuple(184 + (index * 67) for index in range(10))
    rows = []
    separators = []

    def panel_widgets(name, x, y, w, h):
        line = max(1, px(3))
        return """
    <widget name="{name}_bg" position="{x},{y}" size="{w},{h}"
            font="Regular;1" backgroundColor="#071426"
            transparent="0" zPosition="1" />
    <widget name="{name}_top" position="{x},{y}" size="{w},{line}"
            font="Regular;1" backgroundColor="#155E75"
            transparent="0" zPosition="2" />
    <widget name="{name}_bottom" position="{x},{bottom_y}"
            size="{w},{line}" font="Regular;1" backgroundColor="#155E75"
            transparent="0" zPosition="2" />
    <widget name="{name}_left" position="{x},{y}" size="{line},{h}"
            font="Regular;1" backgroundColor="#155E75"
            transparent="0" zPosition="2" />
    <widget name="{name}_right" position="{right_x},{y}"
            size="{line},{h}" font="Regular;1" backgroundColor="#155E75"
            transparent="0" zPosition="2" />""".format(
            name=name,
            x=px(x),
            y=px(y),
            w=px(w),
            h=px(h),
            line=line,
            bottom_y=px(y + h) - line,
            right_x=px(x + w) - line,
        )

    def frame_widgets(name, x, y, w, h):
        line = max(1, px(3))
        return """
    <widget name="{name}_top" position="{x},{y}" size="{w},{line}"
            font="Regular;1" backgroundColor="#155E75"
            transparent="0" zPosition="4" />
    <widget name="{name}_bottom" position="{x},{bottom_y}"
            size="{w},{line}" font="Regular;1" backgroundColor="#155E75"
            transparent="0" zPosition="4" />
    <widget name="{name}_left" position="{x},{y}" size="{line},{h}"
            font="Regular;1" backgroundColor="#155E75"
            transparent="0" zPosition="4" />
    <widget name="{name}_right" position="{right_x},{y}"
            size="{line},{h}" font="Regular;1" backgroundColor="#155E75"
            transparent="0" zPosition="4" />""".format(
            name=name,
            x=px(x),
            y=px(y),
            w=px(w),
            h=px(h),
            line=line,
            bottom_y=px(y + h) - line,
            right_x=px(x + w) - line,
        )

    panels = (
        panel_widgets("account_panel", 35, 170, 1305, 730)
        + panel_widgets("account_detail_panel", 1340, 170, 540, 730)
    )
    # Keep the account/page boards clear of the first list panel at every
    # supported resolution. 104 maps to y=39 at 720x405, leaving one physical
    # pixel above the list border without moving the title or list content.
    badge_frames = (
        frame_widgets("count_frame", 215, 104, 310, 64)
        + frame_widgets("page_frame", 535, 104, 270, 64)
    )
    for index in range(10):
        rows.append(
            """
    <widget name="row_number_{index}" position="{number_x},{row_y}"
            size="{number_w},{row_h}" font="Regular;{row_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="3"
            valign="center" halign="right" />
    <widget name="row_{index}" position="{row_x},{row_y}"
            size="{row_w},{row_h}" font="Regular;{row_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="3"
            valign="center" halign="left" noWrap="1" />
    <widget name="row_detail_{index}" position="{row_x},{detail_y}"
            size="{row_w},{detail_h}" font="Regular;{detail_font}"
            foregroundColor="#9FB2CC" transparent="1" zPosition="3"
            valign="center" halign="left" noWrap="1" />
    <widget name="row_detail_{index}_green" position="{row_x},{detail_y}"
            size="{row_w},{detail_h}" font="Regular;{detail_font}"
            foregroundColor="#39FF74" transparent="1" zPosition="3"
            valign="center" halign="left" noWrap="1" />
    <widget name="row_detail_{index}_yellow" position="{row_x},{detail_y}"
            size="{row_w},{detail_h}" font="Regular;{detail_font}"
            foregroundColor="#FACC15" transparent="1" zPosition="3"
            valign="center" halign="left" noWrap="1" />
    <widget name="row_detail_{index}_red" position="{row_x},{detail_y}"
            size="{row_w},{detail_h}" font="Regular;{detail_font}"
            foregroundColor="#FF477E" transparent="1" zPosition="3"
            valign="center" halign="left" noWrap="1" />
    <widget name="focus_{index}_top" position="{focus_x},{focus_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="4" />
    <widget name="focus_{index}_bottom" position="{focus_x},{focus_bottom_y}"
            size="{focus_w},{focus_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="4" />
    <widget name="focus_{index}_left" position="{focus_x},{focus_y}"
            size="{focus_h},{focus_box_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="4" />
    <widget name="focus_{index}_right" position="{focus_right_x},{focus_y}"
            size="{focus_h},{focus_box_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="4" />""".format(
                index=index,
                number_x=px(75),
                number_w=px(110),
                row_x=px(230),
                row_w=px(1025),
                row_y=px(row_y[index]),
                row_h=px(34),
                row_font=max(11, px(27)),
                detail_y=px(row_y[index] + 33),
                detail_h=px(25),
                detail_font=max(9, px(19)),
                focus_x=px(50),
                focus_y=px(focus_y[index]),
                focus_w=px(1255),
                focus_h=px(5),
                focus_box_h=px(63),
                focus_bottom_y=px(focus_y[index] + 58),
                focus_right_x=px(1300),
            )
        )
        if index < 9:
            separators.append(
                """
    <widget name="row_separator_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#1E344F"
            transparent="0" zPosition="2" />""".format(
                    index=index,
                    x=px(50),
                    y=px(focus_y[index] + 65),
                    w=px(1255),
                    h=max(1, px(2)),
                )
            )
    return """
<screen name="GTAccountsScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#020817">
    <widget name="accounts_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    {panels}
    <widget name="badge_mask" position="{badge_mask_x},{badge_mask_y}"
            size="{badge_mask_w},{badge_mask_h}" font="Regular;1"
            backgroundColor="#071426" transparent="0" zPosition="1" />
    <widget name="header" position="{header_x},{header_y}"
            size="{header_w},{header_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="count_badge" position="{count_x},{badge_y}"
            size="{count_w},{badge_h}" font="Regular;{badge_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="3"
            valign="center" halign="center" noWrap="1" />
    <widget name="source_badge" position="{source_x},{badge_y}"
            size="{source_w},{badge_h}" font="Regular;{badge_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="3"
            valign="center" halign="center" noWrap="1" />
    {badge_frames}
    {rows}
    {separators}
    <widget name="account_scroll_track" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="account_scroll_thumb" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />
    <widget name="summary" position="0,0" size="1,1" font="Regular;1"
            foregroundColor="#020817" transparent="1" zPosition="1" />
    <widget name="accounts" position="0,0" size="1,1" font="Regular;1"
            foregroundColor="#020817" transparent="1" zPosition="1" />
    <widget name="selected_caption" position="{detail_x},{caption_y}"
            size="{detail_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#00E5FF" transparent="1" zPosition="3"
            valign="center" halign="left" noWrap="1" />
    <widget name="selected_account" position="{detail_x},{account_y}"
            size="{detail_w},{account_h}" font="Regular;{account_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="3"
            valign="center" halign="left" noWrap="1" />
    <widget name="selected_status" position="{detail_x},{status_y}"
            size="{detail_w},{status_h}" font="Regular;{status_font}"
            foregroundColor="#9FB2CC" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_status_green" position="{detail_x},{status_y}"
            size="{detail_w},{status_h}" font="Regular;{status_font}"
            foregroundColor="#39FF74" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_status_yellow" position="{detail_x},{status_y}"
            size="{detail_w},{status_h}" font="Regular;{status_font}"
            foregroundColor="#FACC15" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_status_red" position="{detail_x},{status_y}"
            size="{detail_w},{status_h}" font="Regular;{status_font}"
            foregroundColor="#FF477E" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_expiry" position="{detail_x},{expiry_y}"
            size="{detail_w},{meta_h}" font="Regular;{meta_font}"
            foregroundColor="#9FB2CC" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_expiry_green" position="{detail_x},{expiry_y}"
            size="{detail_w},{meta_h}" font="Regular;{meta_font}"
            foregroundColor="#39FF74" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_expiry_yellow" position="{detail_x},{expiry_y}"
            size="{detail_w},{meta_h}" font="Regular;{meta_font}"
            foregroundColor="#FACC15" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_expiry_red" position="{detail_x},{expiry_y}"
            size="{detail_w},{meta_h}" font="Regular;{meta_font}"
            foregroundColor="#FF477E" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_connection" position="{detail_x},{connection_y}"
            size="{detail_w},{meta_h}" font="Regular;{meta_font}"
            foregroundColor="#9FB2CC" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_connection_green" position="{detail_x},{connection_y}"
            size="{detail_w},{meta_h}" font="Regular;{meta_font}"
            foregroundColor="#39FF74" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_connection_yellow" position="{detail_x},{connection_y}"
            size="{detail_w},{meta_h}" font="Regular;{meta_font}"
            foregroundColor="#FACC15" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="selected_connection_red" position="{detail_x},{connection_y}"
            size="{detail_w},{meta_h}" font="Regular;{meta_font}"
            foregroundColor="#FF477E" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="detail_action" position="{action_x},{action_y}"
            size="{action_w},{action_h}" font="Regular;{action_font}"
            foregroundColor="#00E5FF" transparent="1" zPosition="3"
            valign="center" halign="center" noWrap="1" />
    <widget name="detail_top" position="{action_x},{action_y}"
            size="{action_w},{line_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="4" />
    <widget name="detail_bottom" position="{action_x},{action_bottom_y}"
            size="{action_w},{line_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="4" />
    <widget name="detail_left" position="{action_x},{action_y}"
            size="{line_h},{action_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="4" />
    <widget name="detail_right" position="{action_right_x},{action_y}"
            size="{line_h},{action_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="4" />
    <widget name="message" position="{detail_x},{message_y}"
            size="{detail_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#9FB2CC" transparent="1" zPosition="3"
            valign="top" halign="left" noWrap="1" />
    <widget name="footer" position="0,0" size="1,1" font="Regular;1"
            foregroundColor="#020817" transparent="1" zPosition="1" />
</screen>
""".format(
        width=width,
        height=height,
        panels=panels,
        badge_frames=badge_frames,
        # The mask grows upward while its design-space bottom remains at 192,
        # so the baked legacy boards cannot reappear below the raised frames.
        badge_mask_x=px(205), badge_mask_y=px(100),
        badge_mask_w=px(620), badge_mask_h=px(92),
        rows="".join(rows),
        separators="".join(separators),
        scroll_x=px(1320), scroll_y=px(184), scroll_w=px(8), scroll_h=px(668),
        header_x=px(225), header_y=px(35), header_w=px(1000), header_h=px(80),
        title_font=max(13, px(42)),
        count_x=px(215), source_x=px(535), badge_y=px(104),
        count_w=px(310), source_w=px(270), badge_h=px(64), badge_font=max(10, px(22)),
        detail_x=px(1360), detail_w=px(480), caption_y=px(250), caption_h=px(55),
        caption_font=px(30), account_y=px(350), account_h=px(105),
        account_font=max(11, px(34)),
        status_y=px(430), status_h=px(65), status_font=px(29),
        expiry_y=px(510), connection_y=px(590), meta_h=px(58),
        meta_font=max(10, px(23)),
        action_x=px(1365), action_y=px(690), action_w=px(455), action_h=px(70),
        action_font=px(25), line_h=px(4), action_bottom_y=px(756),
        action_right_x=px(1816), message_y=px(785), message_h=px(75),
        message_font=px(22),
    )


def _account_batch_test_skin():
    """Return the modal-style automatic account-test and review surface."""
    width, height, px = _scale()
    rows = []
    separators = []
    for index in range(8):
        y = 275 + (index * 66)
        rows.append(
            """
    <widget name="result_name_{index}" position="{name_x},{y}"
            size="{name_w},{row_h}" font="Regular;{row_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="5"
            valign="center" halign="left" noWrap="1" />
    <widget name="result_status_{index}" position="{status_x},{y}"
            size="{status_w},{row_h}" font="Regular;{status_font}"
            foregroundColor="#9FB2CC" transparent="1" zPosition="5"
            valign="center" halign="right" noWrap="1" />
    <widget name="result_status_{index}_green" position="{status_x},{y}"
            size="{status_w},{row_h}" font="Regular;{status_font}"
            foregroundColor="#39FF74" transparent="1" zPosition="5"
            valign="center" halign="right" noWrap="1" />
    <widget name="result_status_{index}_yellow" position="{status_x},{y}"
            size="{status_w},{row_h}" font="Regular;{status_font}"
            foregroundColor="#FACC15" transparent="1" zPosition="5"
            valign="center" halign="right" noWrap="1" />
    <widget name="result_status_{index}_red" position="{status_x},{y}"
            size="{status_w},{row_h}" font="Regular;{status_font}"
            foregroundColor="#FF477E" transparent="1" zPosition="5"
            valign="center" halign="right" noWrap="1" />""".format(
                index=index,
                name_x=px(455),
                status_x=px(1080),
                y=px(y),
                name_w=px(610),
                status_w=px(385),
                row_h=px(52),
                row_font=max(10, px(26)),
                status_font=max(9, px(23)),
            )
        )
        if index < 7:
            separators.append(
                """
    <widget name="result_separator_{index}" position="{x},{y}"
            size="{w},{h}" font="Regular;1" backgroundColor="#1E344F"
            transparent="0" zPosition="4" />""".format(
                    index=index,
                    x=px(445),
                    y=px(y + 58),
                    w=px(1030),
                    h=max(1, px(2)),
                )
            )

    line = max(1, px(3))
    panel_x = px(360)
    panel_y = px(115)
    panel_w = px(1200)
    panel_h = px(805)
    return """
<screen name="GTAccountBatchTestScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#020817">
    <widget name="accounts_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="test_panel" position="{panel_x},{panel_y}"
            size="{panel_w},{panel_h}" font="Regular;1"
            backgroundColor="#071426" transparent="0" zPosition="2" />
    <widget name="test_panel_top" position="{panel_x},{panel_y}"
            size="{panel_w},{line}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="3" />
    <widget name="test_panel_bottom" position="{panel_x},{panel_bottom_y}"
            size="{panel_w},{line}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="3" />
    <widget name="test_panel_left" position="{panel_x},{panel_y}"
            size="{line},{panel_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="3" />
    <widget name="test_panel_right" position="{panel_right_x},{panel_y}"
            size="{line},{panel_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="3" />
    <widget name="header" position="{header_x},{header_y}"
            size="{header_w},{header_h}" font="Regular;{header_font}"
            foregroundColor="#00E5FF" transparent="1" zPosition="4"
            valign="center" halign="center" noWrap="1" />
    <widget name="subtitle" position="{subtitle_x},{subtitle_y}"
            size="{subtitle_w},{subtitle_h}" font="Regular;{subtitle_font}"
            foregroundColor="#D8E1EF" transparent="1" zPosition="4"
            valign="center" halign="center" />
    <widget name="review_page" position="{page_x},{page_y}"
            size="{page_w},{page_h}" font="Regular;{page_font}"
            foregroundColor="#9FB2CC" transparent="1" zPosition="4"
            valign="center" halign="right" noWrap="1" />
    {rows}
    {separators}
    <widget name="progress_track" position="{progress_x},{progress_y}"
            size="{progress_w},{progress_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="4" />
    <widget name="progress_fill" position="{progress_x},{progress_y}"
            size="1,{progress_h}" font="Regular;1"
            backgroundColor="#00E5FF" transparent="0" zPosition="5" />
    <widget name="progress_text" position="{progress_x},{progress_text_y}"
            size="{progress_w},{progress_text_h}" font="Regular;{progress_font}"
            foregroundColor="#D8E1EF" transparent="1" zPosition="4"
            valign="center" halign="center" noWrap="1" />
    <widget name="current_account" position="{progress_x},{current_y}"
            size="{progress_w},{current_h}" font="Regular;{current_font}"
            foregroundColor="#9FB2CC" transparent="1" zPosition="4"
            valign="center" halign="center" noWrap="1" />
    <widget name="footer" position="0,0" size="1,1" font="Regular;1"
            transparent="1" zPosition="1" />
</screen>
""".format(
        width=width,
        height=height,
        panel_x=panel_x,
        panel_y=panel_y,
        panel_w=panel_w,
        panel_h=panel_h,
        line=line,
        panel_bottom_y=panel_y + panel_h - line,
        panel_right_x=panel_x + panel_w - line,
        header_x=px(420),
        header_y=px(145),
        header_w=px(1080),
        header_h=px(65),
        header_font=max(13, px(38)),
        subtitle_x=px(430),
        subtitle_y=px(205),
        subtitle_w=px(1060),
        subtitle_h=px(62),
        subtitle_font=max(10, px(23)),
        page_x=px(1280),
        page_y=px(218),
        page_w=px(185),
        page_h=px(38),
        page_font=max(9, px(19)),
        rows="".join(rows),
        separators="".join(separators),
        progress_x=px(445),
        progress_y=px(804),
        progress_w=px(1030),
        progress_h=max(3, px(18)),
        progress_text_y=px(825),
        progress_text_h=px(36),
        progress_font=max(9, px(21)),
        current_y=px(861),
        current_h=px(42),
        current_font=max(9, px(20)),
    )


def _add_account_skin():
    width, height, px = _scale()
    panel_width = px(1120)
    panel_x = int((width - panel_width) / 2)
    field_x = panel_x + px(72)
    field_width = panel_width - px(144)
    footer_y = height - px(92)
    return """
<screen name="GTAddAccountScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="panel" position="{panel_x},{panel_y}"
            size="{panel_width},{panel_h}" font="Regular;1"
            backgroundColor="#0B1220" transparent="0" />
    <widget name="header" position="{field_x},{header_y}"
            size="{field_width},{header_h}" font="Regular;{title_font}"
            foregroundColor="#22D3EE" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="left" />
    <widget name="instruction" position="{field_x},{instruction_y}"
            size="{field_width},{instruction_h}" font="Regular;{instruction_font}"
            foregroundColor="#9AA8BE" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="left" />
    <widget name="url_field" position="{field_x},{url_y}"
            size="{field_width},{field_h}" font="Regular;{field_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" />
    <widget name="username_field" position="{field_x},{username_y}"
            size="{field_width},{field_h}" font="Regular;{field_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" />
    <widget name="password_field" position="{field_x},{password_y}"
            size="{field_width},{field_h}" font="Regular;{field_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" />
    <widget name="output_field" position="{field_x},{output_y}"
            size="{field_width},{field_h}" font="Regular;{field_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" />
    <widget name="focus_0" position="{field_x},{url_focus_y}"
            size="{field_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" />
    <widget name="focus_1" position="{field_x},{username_focus_y}"
            size="{field_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" />
    <widget name="focus_2" position="{field_x},{password_focus_y}"
            size="{field_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" />
    <widget name="focus_3" position="{field_x},{output_focus_y}"
            size="{field_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" />
    <widget name="message" position="{field_x},{message_y}"
            size="{field_width},{message_h}" font="Regular;{message_font}"
            foregroundColor="#9AA8BE" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="left" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        accent_h=px(5),
        panel_x=panel_x,
        panel_y=px(55),
        panel_width=panel_width,
        panel_h=px(888),
        field_x=field_x,
        field_width=field_width,
        header_y=px(105),
        header_h=px(72),
        title_font=px(38),
        instruction_y=px(180),
        instruction_h=px(64),
        instruction_font=px(20),
        url_y=px(250),
        username_y=px(365),
        password_y=px(480),
        output_y=px(595),
        field_h=px(94),
        field_font=px(24),
        url_focus_y=px(338),
        username_focus_y=px(453),
        password_focus_y=px(568),
        output_focus_y=px(683),
        focus_h=px(6),
        message_y=px(720),
        message_h=px(150),
        message_font=px(20),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _add_source_type_skin():
    """Return the three-card source chooser used before the account forms."""
    width, height, px = _scale()
    panel_width = px(1320)
    panel_x = int((width - panel_width) / 2)
    content_x = panel_x + px(70)
    content_width = panel_width - px(140)
    card_x = content_x
    card_width = content_width
    icon_x = card_x + px(48)
    text_x = card_x + px(205)
    text_width = card_width - px(275)
    focus_h = px(5)
    footer_y = height - px(92)
    card_0_y = px(220)
    card_1_y = px(400)
    card_2_y = px(580)
    card_h = px(165)

    return """
<screen name="GTAddSourceTypeScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="panel" position="{panel_x},{panel_y}"
            size="{panel_width},{panel_h}" font="Regular;1"
            backgroundColor="#0B1220" transparent="0" zPosition="1" />

    <widget name="header" position="{content_x},{header_y}"
            size="{header_w},{header_h}" font="Regular;{title_font}"
            foregroundColor="#22D3EE" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="left" zPosition="2" />
    <widget name="step" position="{step_x},{step_y}"
            size="{step_w},{step_h}" font="Regular;{step_font}"
            foregroundColor="#B6C2D4" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="right" zPosition="2" />
    <widget name="instruction" position="{content_x},{instruction_y}"
            size="{content_width},{instruction_h}" font="Regular;{instruction_font}"
            foregroundColor="#C8D2E3" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="left" zPosition="2" />

    <widget name="card_0" position="{card_x},{card_0_y}"
            size="{card_width},{card_h}" font="Regular;1"
            backgroundColor="#121D31" transparent="0" zPosition="2" />
    <widget name="card_1" position="{card_x},{card_1_y}"
            size="{card_width},{card_h}" font="Regular;1"
            backgroundColor="#121D31" transparent="0" zPosition="2" />
    <widget name="card_2" position="{card_x},{card_2_y}"
            size="{card_width},{card_h}" font="Regular;1"
            backgroundColor="#121D31" transparent="0" zPosition="2" />

    <widget name="icon_0" position="{icon_x},{icon_0_y}"
            size="{icon_w},{icon_h}" font="Regular;{icon_font}"
            foregroundColor="#22D3EE" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="center" zPosition="3" />
    <widget name="icon_1" position="{icon_x},{icon_1_y}"
            size="{icon_w},{icon_h}" font="Regular;{icon_small_font}"
            foregroundColor="#22D3EE" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="center" zPosition="3" />
    <widget name="icon_2" position="{icon_x},{icon_2_y}"
            size="{icon_w},{icon_h}" font="Regular;{icon_small_font}"
            foregroundColor="#22D3EE" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="center" zPosition="3" />

    <widget name="title_0" position="{text_x},{title_0_y}"
            size="{text_width},{title_h}" font="Regular;{card_title_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="description_0" position="{text_x},{description_0_y}"
            size="{text_width},{description_h}" font="Regular;{description_font}"
            foregroundColor="#B8C3D5" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="capabilities_0" position="{text_x},{capabilities_0_y}"
            size="{text_width},{capabilities_h}" font="Regular;{capabilities_font}"
            foregroundColor="#22D3EE" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />

    <widget name="title_1" position="{text_x},{title_1_y}"
            size="{m3u_text_w},{title_h}" font="Regular;{card_title_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="description_1" position="{text_x},{description_1_y}"
            size="{m3u_text_w},{description_h}" font="Regular;{description_font}"
            foregroundColor="#B8C3D5" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="capabilities_1" position="{text_x},{capabilities_1_y}"
            size="{m3u_text_w},{capabilities_h}" font="Regular;{capabilities_font}"
            foregroundColor="#22D3EE" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="badge_1" position="{badge_x},{badge_y}"
            size="{badge_w},{badge_h}" font="Regular;{badge_font}"
            foregroundColor="#22D3EE" backgroundColor="#0B2734"
            transparent="0" valign="center" halign="center" zPosition="3" />

    <widget name="title_2" position="{text_x},{title_2_y}"
            size="{text_width},{title_h}" font="Regular;{card_title_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="description_2" position="{text_x},{description_2_y}"
            size="{text_width},{description_h}" font="Regular;{description_font}"
            foregroundColor="#B8C3D5" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="capabilities_2" position="{text_x},{capabilities_2_y}"
            size="{text_width},{capabilities_h}" font="Regular;{capabilities_font}"
            foregroundColor="#22D3EE" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />

    <widget name="focus_0_top" position="{card_x},{focus_0_top_y}"
            size="{card_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_0_bottom" position="{card_x},{focus_0_bottom_y}"
            size="{card_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_0_left" position="{card_x},{card_0_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_0_right" position="{focus_right_x},{card_0_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_1_top" position="{card_x},{focus_1_top_y}"
            size="{card_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_1_bottom" position="{card_x},{focus_1_bottom_y}"
            size="{card_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_1_left" position="{card_x},{card_1_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_1_right" position="{focus_right_x},{card_1_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_2_top" position="{card_x},{focus_2_top_y}"
            size="{card_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_2_bottom" position="{card_x},{focus_2_bottom_y}"
            size="{card_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_2_left" position="{card_x},{card_2_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_2_right" position="{focus_right_x},{card_2_y}"
            size="{focus_h},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />

    <widget name="info_panel" position="{card_x},{info_y}"
            size="{card_width},{info_h}" font="Regular;1"
            backgroundColor="#0D1829" transparent="0" zPosition="2" />
    <widget name="info_icon" position="{info_icon_x},{info_icon_y}"
            size="{info_icon_w},{info_icon_h}" font="Regular;{info_icon_font}"
            foregroundColor="#22D3EE" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="center" zPosition="3" />
    <widget name="message" position="{message_x},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#C8D2E3" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="left" zPosition="3" />

    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" zPosition="2" />
</screen>
""".format(
        width=width,
        height=height,
        panel_x=panel_x,
        panel_y=px(55),
        panel_width=panel_width,
        panel_h=px(890),
        content_x=content_x,
        content_width=content_width,
        header_y=px(92),
        header_w=px(640),
        header_h=px(72),
        title_font=px(45),
        step_x=panel_x + panel_width - px(500),
        step_y=px(104),
        step_w=px(430),
        step_h=px(52),
        step_font=px(23),
        instruction_y=px(164),
        instruction_h=px(55),
        instruction_font=px(24),
        card_x=card_x,
        card_width=card_width,
        card_0_y=card_0_y,
        card_1_y=card_1_y,
        card_2_y=card_2_y,
        card_h=card_h,
        icon_x=icon_x,
        icon_0_y=card_0_y + px(20),
        icon_1_y=card_1_y + px(20),
        icon_2_y=card_2_y + px(20),
        icon_w=px(125),
        icon_h=px(125),
        icon_font=px(36),
        icon_small_font=px(27),
        text_x=text_x,
        text_width=text_width,
        title_0_y=card_0_y + px(12),
        title_1_y=card_1_y + px(12),
        title_2_y=card_2_y + px(12),
        title_h=px(46),
        card_title_font=px(28),
        description_0_y=card_0_y + px(57),
        description_1_y=card_1_y + px(57),
        description_2_y=card_2_y + px(57),
        description_h=px(42),
        description_font=px(20),
        capabilities_0_y=card_0_y + px(108),
        capabilities_1_y=card_1_y + px(108),
        capabilities_2_y=card_2_y + px(108),
        capabilities_h=px(39),
        capabilities_font=px(18),
        m3u_text_w=card_width - px(500),
        badge_x=card_x + card_width - px(220),
        badge_y=card_1_y + px(106),
        badge_w=px(170),
        badge_h=px(48),
        badge_font=px(19),
        focus_0_top_y=card_0_y,
        focus_0_bottom_y=card_0_y + card_h - focus_h,
        focus_1_top_y=card_1_y,
        focus_1_bottom_y=card_1_y + card_h - focus_h,
        focus_2_top_y=card_2_y,
        focus_2_bottom_y=card_2_y + card_h - focus_h,
        focus_right_x=card_x + card_width - focus_h,
        focus_h=focus_h,
        info_y=px(765),
        info_h=px(135),
        info_icon_x=card_x + px(38),
        info_icon_y=px(800),
        info_icon_w=px(80),
        info_icon_h=px(65),
        info_icon_font=px(37),
        message_x=card_x + px(135),
        message_y=px(780),
        message_w=card_width - px(175),
        message_h=px(100),
        message_font=px(21),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _manage_sources_skin():
    """Reuse the proven three-card geometry with independent count badges."""
    width, unused_height, px = _scale()
    del unused_height
    panel_width = px(1320)
    panel_x = int((width - panel_width) / 2)
    card_x = panel_x + px(70)
    card_width = panel_width - px(140)
    count_x = card_x + card_width - px(245)
    badge_w = px(205)
    badge_h = px(48)
    widgets = []
    for index, card_y in enumerate((220, 400, 580)):
        widgets.append(
            '<widget name="count_{index}" position="{x},{y}" '
            'size="{w},{h}" font="Regular;{font}" foregroundColor="#22D3EE" '
            'backgroundColor="#0B2734" transparent="0" zPosition="4" '
            'valign="center" halign="center" />'.format(
                index=index,
                x=count_x,
                y=px(card_y + 20),
                w=badge_w,
                h=badge_h,
                font=max(10, px(19)),
            )
        )
    skin = _add_source_type_skin().replace(
        'name="GTAddSourceTypeScreen"',
        'name="GTManageSourcesScreen"',
        1,
    )
    marker = '    <widget name="info_panel"'
    return skin.replace(marker, "\n".join(widgets) + "\n\n" + marker, 1)


def _playlist_type_skin():
    """Two-card chooser for independent Xtream and MAC TXT banks."""
    width, height, px = _scale()
    panel_width = px(1320)
    panel_x = int((width - panel_width) / 2)
    content_x = panel_x + px(70)
    content_width = panel_width - px(140)
    card_y = (px(285), px(520))
    card_h = px(190)
    edge = px(5)
    widgets = []
    for index, y_value in enumerate(card_y):
        widgets.append(
            """
    <widget name="card_{index}" position="{card_x},{card_y}"
            size="{card_w},{card_h}" font="Regular;1"
            backgroundColor="#121D31" transparent="0" zPosition="2" />
    <widget name="icon_{index}" position="{icon_x},{icon_y}"
            size="{icon_w},{icon_h}" font="Regular;{icon_font}"
            foregroundColor="#22D3EE" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="center" zPosition="3" />
    <widget name="title_{index}" position="{text_x},{title_y}"
            size="{text_w},{title_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="description_{index}" position="{text_x},{description_y}"
            size="{text_w},{description_h}" font="Regular;{description_font}"
            foregroundColor="#B8C3D5" transparent="1" zPosition="3"
            valign="center" halign="left" />
    <widget name="focus_{index}_top" position="{card_x},{card_y}"
            size="{card_w},{edge}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_{index}_bottom" position="{card_x},{bottom_y}"
            size="{card_w},{edge}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_{index}_left" position="{card_x},{card_y}"
            size="{edge},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="focus_{index}_right" position="{right_x},{card_y}"
            size="{edge},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />""".format(
                index=index,
                card_x=content_x,
                card_y=y_value,
                card_w=content_width,
                card_h=card_h,
                icon_x=content_x + px(42),
                icon_y=y_value + px(27),
                icon_w=px(136),
                icon_h=px(136),
                icon_font=px(31),
                text_x=content_x + px(215),
                title_y=y_value + px(30),
                text_w=content_width - px(260),
                title_h=px(58),
                title_font=px(31),
                description_y=y_value + px(92),
                description_h=px(70),
                description_font=px(22),
                edge=edge,
                bottom_y=y_value + card_h - edge,
                right_x=content_x + content_width - edge,
            )
        )
    footer_y = height - px(92)
    return """
<screen name="GTPlaylistTypeScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}" zPosition="0" />
    <widget name="panel" position="{panel_x},{panel_y}"
            size="{panel_w},{panel_h}" font="Regular;1"
            backgroundColor="#0B1220" transparent="0" zPosition="1" />
    <widget name="header" position="{content_x},{header_y}"
            size="{header_w},{header_h}" font="Regular;{header_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="step" position="{step_x},{step_y}"
            size="{step_w},{step_h}" font="Regular;{step_font}"
            foregroundColor="#B6C2D4" transparent="1" zPosition="2"
            valign="center" halign="right" />
    <widget name="instruction" position="{content_x},{instruction_y}"
            size="{content_w},{instruction_h}" font="Regular;{instruction_font}"
            foregroundColor="#C8D2E3" transparent="1" zPosition="2"
            valign="center" halign="left" />
    {widgets}
    <widget name="message" position="{content_x},{message_y}"
            size="{content_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#8EA2C4" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0" zPosition="2"
            valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        panel_x=panel_x,
        panel_y=px(55),
        panel_w=panel_width,
        panel_h=px(890),
        content_x=content_x,
        content_w=content_width,
        header_y=px(92),
        header_w=px(700),
        header_h=px(72),
        header_font=px(43),
        step_x=panel_x + panel_width - px(500),
        step_y=px(104),
        step_w=px(430),
        step_h=px(52),
        step_font=px(23),
        instruction_y=px(174),
        instruction_h=px(70),
        instruction_font=px(24),
        widgets="".join(widgets),
        message_y=px(770),
        message_h=px(100),
        message_font=px(22),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _playlist_manager_skin():
    """Show twelve numbered TXT slots at once in a two-column page."""
    width, height, px = _scale()
    margin = px(70)
    gap = px(35)
    column_width = int((width - (margin * 2) - gap) / 2)
    row_h = px(96)
    rows = []
    for index in range(PLAYLIST_FILE_PAGE_SIZE):
        column = index // 6
        row = index % 6
        x_value = margin + (column * (column_width + gap))
        y_value = px(180 + (row * 111))
        rows.append(
            """
    <widget name="row_bg_{index}" position="{x},{y}" size="{w},{h}"
            font="Regular;1" backgroundColor="#101B30" transparent="0" zPosition="1" />
    <widget name="row_focus_{index}" position="{x},{y}" size="{w},{h}"
            font="Regular;1" backgroundColor="#17536A" transparent="0" zPosition="2" />
    <widget name="row_marker_{index}" position="{x},{y}" size="{marker},{h}"
            font="Regular;1" backgroundColor="#22D3EE" transparent="0" zPosition="3" />
    <widget name="row_number_{index}" position="{number_x},{y}"
            size="{number_w},{h}" font="Regular;{number_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="4"
            valign="center" halign="center" />
    <widget name="row_name_{index}" position="{text_x},{name_y}"
            size="{text_w},{name_h}" font="Regular;{name_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="4"
            valign="center" halign="left" noWrap="1" />
    <widget name="row_path_{index}" position="{text_x},{path_y}"
            size="{path_w},{path_h}" font="Regular;{path_font}"
            foregroundColor="#9AA8BE" transparent="1" zPosition="4"
            valign="center" halign="left" noWrap="1" />
    <widget name="row_status_{index}" position="{status_x},{path_y}"
            size="{status_w},{path_h}" font="Regular;{status_font}"
            foregroundColor="#39FF74" transparent="1" zPosition="4"
            valign="center" halign="right" noWrap="1" />""".format(
                index=index,
                x=x_value,
                y=y_value,
                w=column_width,
                h=row_h,
                marker=px(7),
                number_x=x_value + px(12),
                number_w=px(70),
                number_font=px(25),
                text_x=x_value + px(92),
                name_y=y_value + px(8),
                text_w=column_width - px(112),
                name_h=px(48),
                name_font=px(24),
                path_y=y_value + px(53),
                path_w=column_width - px(265),
                path_h=px(34),
                path_font=px(17),
                status_x=x_value + column_width - px(155),
                status_w=px(135),
                status_font=px(17),
            )
        )
    footer_y = height - px(92)
    return """
<screen name="GTPlaylistManagerScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}" zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" zPosition="1" />
    <widget name="header" position="{margin},{header_y}"
            size="{header_w},{header_h}" font="Regular;{header_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="subtitle" position="{margin},{subtitle_y}"
            size="{subtitle_w},{subtitle_h}" font="Regular;{subtitle_font}"
            foregroundColor="#22D3EE" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="page" position="{page_x},{page_y}"
            size="{page_w},{page_h}" font="Regular;{page_font}"
            foregroundColor="#22D3EE" backgroundColor="#0B2734"
            transparent="0" zPosition="2" valign="center" halign="center" />
    {rows}
    <widget name="message" position="{margin},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#C8D2E3" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0" zPosition="4"
            valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        accent_h=px(5),
        margin=margin,
        header_y=px(35),
        header_w=width - (margin * 2) - px(300),
        header_h=px(68),
        header_font=px(38),
        subtitle_y=px(105),
        subtitle_w=width - (margin * 2) - px(300),
        subtitle_h=px(48),
        subtitle_font=px(23),
        page_x=width - margin - px(250),
        page_y=px(68),
        page_w=px(250),
        page_h=px(58),
        page_font=px(22),
        rows="".join(rows),
        message_y=px(858),
        message_w=width - (margin * 2),
        message_h=px(105),
        message_font=px(21),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(19),
    )


def _add_stalker_source_skin():
    """Return the two-field Stalker/MAC portal form."""
    width, height, px = _scale()
    panel_width = px(1120)
    panel_x = int((width - panel_width) / 2)
    field_x = panel_x + px(72)
    field_width = panel_width - px(144)
    footer_y = height - px(92)
    portal_y = px(300)
    mac_y = px(465)
    field_h = px(112)
    focus_h = px(6)

    return """
<screen name="GTAddStalkerSourceScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="panel" position="{panel_x},{panel_y}"
            size="{panel_width},{panel_h}" font="Regular;1"
            backgroundColor="#0B1220" transparent="0" />
    <widget name="header" position="{field_x},{header_y}"
            size="{field_width},{header_h}" font="Regular;{title_font}"
            foregroundColor="#22D3EE" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="left" />
    <widget name="step" position="{step_x},{step_y}"
            size="{step_w},{step_h}" font="Regular;{step_font}"
            foregroundColor="#B6C2D4" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="right" />
    <widget name="instruction" position="{field_x},{instruction_y}"
            size="{field_width},{instruction_h}" font="Regular;{instruction_font}"
            foregroundColor="#9AA8BE" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="left" />

    <widget name="portal_field" position="{field_x},{portal_y}"
            size="{field_width},{field_h}" font="Regular;{field_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" />
    <widget name="mac_field" position="{field_x},{mac_y}"
            size="{field_width},{field_h}" font="Regular;{field_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" />
    <widget name="focus_0" position="{field_x},{portal_focus_y}"
            size="{field_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" />
    <widget name="focus_1" position="{field_x},{mac_focus_y}"
            size="{field_width},{focus_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" />

    <widget name="info_panel" position="{field_x},{info_y}"
            size="{field_width},{info_h}" font="Regular;1"
            backgroundColor="#0D1829" transparent="0" />
    <widget name="info_icon" position="{info_icon_x},{info_icon_y}"
            size="{info_icon_w},{info_icon_h}" font="Regular;{info_icon_font}"
            foregroundColor="#22D3EE" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="center" />
    <widget name="message" position="{message_x},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#C8D2E3" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="left" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" />
</screen>
""".format(
        width=width,
        height=height,
        accent_h=px(5),
        panel_x=panel_x,
        panel_y=px(55),
        panel_width=panel_width,
        panel_h=px(888),
        field_x=field_x,
        field_width=field_width,
        header_y=px(102),
        header_h=px(72),
        title_font=px(38),
        step_x=panel_x + panel_width - px(430),
        step_y=px(112),
        step_w=px(350),
        step_h=px(48),
        step_font=px(22),
        instruction_y=px(190),
        instruction_h=px(68),
        instruction_font=px(21),
        portal_y=portal_y,
        mac_y=mac_y,
        field_h=field_h,
        field_font=px(24),
        portal_focus_y=portal_y + field_h - focus_h,
        mac_focus_y=mac_y + field_h - focus_h,
        focus_h=focus_h,
        info_y=px(635),
        info_h=px(210),
        info_icon_x=field_x + px(28),
        info_icon_y=px(700),
        info_icon_w=px(70),
        info_icon_h=px(65),
        info_icon_font=px(36),
        message_x=field_x + px(115),
        message_y=px(660),
        message_w=field_width - px(155),
        message_h=px(160),
        message_font=px(21),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _add_m3u_source_skin():
    """Return the three-field, remote-friendly M3U source form."""
    width, height, px = _scale()
    panel_width = px(1400)
    panel_x = int((width - panel_width) / 2)
    content_x = panel_x + px(60)
    content_width = panel_width - px(120)
    footer_y = height - px(92)
    name_y = px(220)
    name_h = px(125)
    cards_y = px(370)
    card_h = px(220)
    card_gap = px(30)
    card_width = int((content_width - card_gap) / 2)
    card_0_x = content_x
    card_1_x = card_0_x + card_width + card_gap
    location_y = px(615)
    location_h = px(125)
    info_y = px(765)
    info_h = px(110)
    edge = px(5)
    focus_edge = px(10)

    return """
<screen name="GTAddM3USourceScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="panel" position="{panel_x},{panel_y}"
            size="{panel_width},{panel_h}" font="Regular;1"
            backgroundColor="#0B1220" transparent="0" zPosition="1" />

    <widget name="header" position="{content_x},{header_y}"
            size="{header_w},{header_h}" font="Regular;{title_font}"
            foregroundColor="#22D3EE" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="left" zPosition="2" />
    <widget name="step" position="{step_x},{step_y}"
            size="{step_w},{step_h}" font="Regular;{step_font}"
            foregroundColor="#B6C2D4" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="right" zPosition="2" />
    <widget name="instruction" position="{content_x},{instruction_y}"
            size="{content_width},{instruction_h}" font="Regular;{instruction_font}"
            foregroundColor="#C8D2E3" backgroundColor="#0B1220"
            transparent="0" valign="center" halign="left" zPosition="2" />

    <widget name="name_panel" position="{content_x},{name_y}"
            size="{content_width},{name_h}" font="Regular;1"
            backgroundColor="#121D31" transparent="0" zPosition="2" />
    <widget name="name_caption" position="{field_text_x},{name_caption_y}"
            size="{field_text_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#22D3EE" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="name_value" position="{field_text_x},{name_value_y}"
            size="{field_text_w},{value_h}" font="Regular;{value_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="focus_0" position="{content_x},{name_focus_y}"
            size="{content_width},{edge}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />

    <widget name="kind_card_0" position="{card_0_x},{cards_y}"
            size="{card_width},{card_h}" font="Regular;1"
            backgroundColor="#121D31" transparent="0" zPosition="2" />
    <widget name="kind_card_1" position="{card_1_x},{cards_y}"
            size="{card_width},{card_h}" font="Regular;1"
            backgroundColor="#121D31" transparent="0" zPosition="2" />
    <widget name="kind_icon_0" position="{icon_0_x},{icon_y}"
            size="{icon_w},{icon_h}" font="Regular;{icon_font}"
            foregroundColor="#22D3EE" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="center" zPosition="3" />
    <widget name="kind_icon_1" position="{icon_1_x},{icon_y}"
            size="{icon_w},{icon_h}" font="Regular;{icon_font}"
            foregroundColor="#22D3EE" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="center" zPosition="3" />
    <widget name="kind_title_0" position="{kind_text_0_x},{kind_title_y}"
            size="{kind_text_w},{kind_title_h}" font="Regular;{kind_title_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="kind_title_1" position="{kind_text_1_x},{kind_title_y}"
            size="{kind_text_w},{kind_title_h}" font="Regular;{kind_title_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="kind_description_0" position="{kind_text_0_x},{kind_description_y}"
            size="{kind_text_w},{kind_description_h}" font="Regular;{kind_description_font}"
            foregroundColor="#B8C3D5" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="kind_description_1" position="{kind_text_1_x},{kind_description_y}"
            size="{kind_text_w},{kind_description_h}" font="Regular;{kind_description_font}"
            foregroundColor="#B8C3D5" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="kind_meta_0" position="{kind_text_0_x},{kind_meta_y}"
            size="{kind_text_w},{kind_meta_h}" font="Regular;{kind_meta_font}"
            foregroundColor="#22D3EE" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="kind_meta_1" position="{kind_text_1_x},{kind_meta_y}"
            size="{kind_text_w},{kind_meta_h}" font="Regular;{kind_meta_font}"
            foregroundColor="#22D3EE" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />

    <widget name="selected_0_top" position="{card_0_x},{cards_y}"
            size="{card_width},{edge}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="selected_0_bottom" position="{card_0_x},{card_bottom_y}"
            size="{card_width},{edge}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="selected_0_left" position="{card_0_x},{cards_y}"
            size="{edge},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="selected_0_right" position="{card_0_right_x},{cards_y}"
            size="{edge},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="selected_1_top" position="{card_1_x},{cards_y}"
            size="{card_width},{edge}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="selected_1_bottom" position="{card_1_x},{card_bottom_y}"
            size="{card_width},{edge}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="selected_1_left" position="{card_1_x},{cards_y}"
            size="{edge},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="selected_1_right" position="{card_1_right_x},{cards_y}"
            size="{edge},{card_h}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="5" />
    <widget name="kind_focus_0" position="{card_0_x},{cards_y}"
            size="{focus_edge},{card_h}" font="Regular;1"
            backgroundColor="#67E8F9" transparent="0" zPosition="6" />
    <widget name="kind_focus_1" position="{card_1_x},{cards_y}"
            size="{focus_edge},{card_h}" font="Regular;1"
            backgroundColor="#67E8F9" transparent="0" zPosition="6" />

    <widget name="location_panel" position="{content_x},{location_y}"
            size="{content_width},{location_h}" font="Regular;1"
            backgroundColor="#121D31" transparent="0" zPosition="2" />
    <widget name="location_caption" position="{field_text_x},{location_caption_y}"
            size="{location_text_w},{caption_h}" font="Regular;{caption_font}"
            foregroundColor="#22D3EE" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="location_value" position="{field_text_x},{location_value_y}"
            size="{location_text_w},{value_h}" font="Regular;{path_font}"
            foregroundColor="#FFFFFF" backgroundColor="#121D31"
            transparent="0" valign="center" halign="left" zPosition="3" />
    <widget name="location_action" position="{action_x},{action_y}"
            size="{action_w},{action_h}" font="Regular;{action_font}"
            foregroundColor="#22D3EE" backgroundColor="#0B2734"
            transparent="0" valign="center" halign="center" zPosition="3" />
    <widget name="focus_2" position="{content_x},{location_focus_y}"
            size="{content_width},{edge}" font="Regular;1"
            backgroundColor="#22D3EE" transparent="0" zPosition="6" />

    <widget name="info_panel" position="{content_x},{info_y}"
            size="{content_width},{info_h}" font="Regular;1"
            backgroundColor="#0D1829" transparent="0" zPosition="2" />
    <widget name="info_icon" position="{info_icon_x},{info_icon_y}"
            size="{info_icon_w},{info_icon_h}" font="Regular;{info_icon_font}"
            foregroundColor="#22D3EE" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="center" zPosition="3" />
    <widget name="message" position="{message_x},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#C8D2E3" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="left" zPosition="3" />

    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" zPosition="2" />
</screen>
""".format(
        width=width,
        height=height,
        panel_x=panel_x,
        panel_y=px(55),
        panel_width=panel_width,
        panel_h=px(890),
        content_x=content_x,
        content_width=content_width,
        header_y=px(88),
        header_w=px(720),
        header_h=px(68),
        title_font=px(44),
        step_x=panel_x + panel_width - px(520),
        step_y=px(98),
        step_w=px(460),
        step_h=px(48),
        step_font=px(23),
        instruction_y=px(155),
        instruction_h=px(50),
        instruction_font=px(24),
        name_y=name_y,
        name_h=name_h,
        field_text_x=content_x + px(35),
        field_text_w=content_width - px(70),
        name_caption_y=name_y + px(12),
        name_value_y=name_y + px(48),
        caption_h=px(38),
        caption_font=px(21),
        value_h=px(62),
        value_font=px(33),
        name_focus_y=name_y + name_h - edge,
        edge=edge,
        cards_y=cards_y,
        card_h=card_h,
        card_width=card_width,
        card_0_x=card_0_x,
        card_1_x=card_1_x,
        icon_0_x=card_0_x + px(35),
        icon_1_x=card_1_x + px(35),
        icon_y=cards_y + px(50),
        icon_w=px(120),
        icon_h=px(120),
        icon_font=px(29),
        kind_text_0_x=card_0_x + px(180),
        kind_text_1_x=card_1_x + px(180),
        kind_text_w=card_width - px(210),
        kind_title_y=cards_y + px(22),
        kind_title_h=px(50),
        kind_title_font=px(31),
        kind_description_y=cards_y + px(72),
        kind_description_h=px(82),
        kind_description_font=px(21),
        kind_meta_y=cards_y + px(160),
        kind_meta_h=px(38),
        kind_meta_font=px(20),
        card_bottom_y=cards_y + card_h - edge,
        card_0_right_x=card_0_x + card_width - edge,
        card_1_right_x=card_1_x + card_width - edge,
        focus_edge=focus_edge,
        location_y=location_y,
        location_h=location_h,
        location_caption_y=location_y + px(12),
        location_value_y=location_y + px(48),
        location_text_w=content_width - px(320),
        path_font=px(29),
        action_x=content_x + content_width - px(250),
        action_y=location_y + px(39),
        action_w=px(215),
        action_h=px(54),
        action_font=px(20),
        location_focus_y=location_y + location_h - edge,
        info_y=info_y,
        info_h=info_h,
        info_icon_x=content_x + px(35),
        info_icon_y=info_y + px(22),
        info_icon_w=px(75),
        info_icon_h=px(66),
        info_icon_font=px(36),
        message_x=content_x + px(125),
        message_y=info_y + px(15),
        message_w=content_width - px(165),
        message_h=px(80),
        message_font=px(22),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _m3u_file_browser_skin():
    """Return a native single-list browser for local M3U sources."""
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    footer_y = height - px(92)
    return """
<screen name="GTM3UFileBrowserScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="header" position="{margin},{header_y}"
            size="{content_width},{header_h}" font="Regular;{title_font}"
            foregroundColor="#22D3EE" transparent="1"
            valign="center" halign="left" zPosition="2" />
    <widget name="instruction" position="{margin},{instruction_y}"
            size="{content_width},{instruction_h}"
            font="Regular;{instruction_font}" foregroundColor="#C8D2E3"
            transparent="1" valign="center" halign="left" zPosition="2" />
    <widget name="path_panel" position="{margin},{path_y}"
            size="{content_width},{path_h}" font="Regular;1"
            backgroundColor="#0D1829" transparent="0" zPosition="1" />
    <widget name="path" position="{path_text_x},{path_text_y}"
            size="{path_text_w},{path_text_h}" font="Regular;{path_font}"
            foregroundColor="#D8E1EF" backgroundColor="#0D1829"
            transparent="0" valign="center" halign="left" zPosition="2" />
    <widget name="filelist" position="{margin},{list_y}"
            size="{content_width},{list_h}" font="Regular;{list_font}"
            foregroundColor="#FFFFFF" foregroundColorSelected="#FFFFFF"
            backgroundColor="#0B1220" backgroundColorSelected="#0E7490"
            scrollbarMode="showOnDemand" enableWrapAround="0" zPosition="2" />
    <widget name="message" position="{margin},{message_y}"
            size="{content_width},{message_h}" font="Regular;{message_font}"
            foregroundColor="#8EA2C4" transparent="1"
            valign="center" halign="left" zPosition="2" />
    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" zPosition="2" />
</screen>
""".format(
        width=width,
        height=height,
        margin=margin,
        content_width=content_width,
        header_y=px(42),
        header_h=px(70),
        title_font=px(40),
        instruction_y=px(112),
        instruction_h=px(48),
        instruction_font=px(23),
        path_y=px(175),
        path_h=px(78),
        path_text_x=margin + px(28),
        path_text_y=px(184),
        path_text_w=content_width - px(56),
        path_text_h=px(60),
        path_font=px(24),
        list_y=px(275),
        list_h=px(620),
        list_font=px(25),
        message_y=px(905),
        message_h=px(56),
        message_font=px(21),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _txt_file_browser_skin():
    return _m3u_file_browser_skin().replace(
        'name="GTM3UFileBrowserScreen"',
        'name="GTTXTFileBrowserScreen"',
        1,
    )


def _health_skin():
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    footer_y = height - px(92)
    return """
<screen name="GTServerHealthScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="header" position="{margin},{header_y}" size="{content_width},{header_h}"
            font="Regular;{title_font}" foregroundColor="#FFFFFF"
            transparent="1" zPosition="5"
            valign="center" halign="left" />
    <widget name="account" position="{margin},{account_y}"
            size="{content_width},{account_h}" font="Regular;{account_font}"
            foregroundColor="#A8B4C8" transparent="1" noWrap="1" zPosition="5"
            valign="center" halign="left" />

    <widget name="health_panel" position="{panel_x},{panel_y}"
            size="{panel_w},{panel_h}" font="Regular;1"
            backgroundColor="#061225" transparent="0" zPosition="1" />
    <widget name="panel_top" position="{panel_x},{panel_y}"
            size="{panel_w},{border}" font="Regular;1"
            backgroundColor="#00CDEB" transparent="0" zPosition="2" />
    <widget name="panel_bottom" position="{panel_x},{panel_bottom_y}"
            size="{panel_w},{border}" font="Regular;1"
            backgroundColor="#155E75" transparent="0" zPosition="2" />
    <widget name="panel_left" position="{panel_x},{panel_y}"
            size="{border},{panel_h}" font="Regular;1"
            backgroundColor="#00CDEB" transparent="0" zPosition="2" />
    <widget name="panel_right" position="{panel_right_x},{panel_y}"
            size="{border},{panel_h}" font="Regular;1"
            backgroundColor="#155E75" transparent="0" zPosition="2" />

    <widget name="traffic_base" position="{traffic_x},{traffic_y}"
            size="{traffic_w},{traffic_h}" transparent="1"
            pixmap="{traffic_base_path}"
            alphatest="blend" scale="1" zPosition="4" />
    <widget name="traffic_red" position="{traffic_overlay_x},{traffic_red_y}"
            size="{traffic_overlay_size},{traffic_overlay_size}" transparent="1"
            pixmap="{traffic_red_path}"
            alphatest="blend" scale="1" zPosition="5" />
    <widget name="traffic_yellow" position="{traffic_overlay_x},{traffic_yellow_y}"
            size="{traffic_overlay_size},{traffic_overlay_size}" transparent="1"
            pixmap="{traffic_yellow_path}"
            alphatest="blend" scale="1" zPosition="5" />
    <widget name="traffic_green" position="{traffic_overlay_x},{traffic_green_y}"
            size="{traffic_overlay_size},{traffic_overlay_size}" transparent="1"
            pixmap="{traffic_green_path}"
            alphatest="blend" scale="1" zPosition="5" />

    <widget name="status" position="{status_x},{status_y}"
            size="{status_w},{status_h}" font="Regular;{status_font}"
            foregroundColor="#94A3B8" transparent="1" zPosition="5"
            valign="center" halign="center" />
    <widget name="status_green" position="{status_x},{status_y}"
            size="{status_w},{status_h}" font="Regular;{status_font}"
            foregroundColor="#39FF74" transparent="1" zPosition="6"
            valign="center" halign="center" />
    <widget name="status_yellow" position="{status_x},{status_y}"
            size="{status_w},{status_h}" font="Regular;{status_font}"
            foregroundColor="#FBBF24" transparent="1" zPosition="6"
            valign="center" halign="center" />
    <widget name="status_red" position="{status_x},{status_y}"
            size="{status_w},{status_h}" font="Regular;{status_font}"
            foregroundColor="#FF4D62" transparent="1" zPosition="6"
            valign="center" halign="center" />
    <widget name="status_badge" position="{badge_x},{badge_y}"
            size="{badge_w},{badge_h}" font="Regular;{badge_font}"
            foregroundColor="#D8E1EF" backgroundColor="#0B1A2D"
            transparent="0" zPosition="5" valign="center" halign="center" />

    <widget name="vertical_divider" position="{divider_x},{divider_y}"
            size="{border},{divider_h}" font="Regular;1"
            backgroundColor="#0E7490" transparent="0" zPosition="3" />
    <widget name="details_left_bg" position="{left_bg_x},{cards_y}"
            size="{left_bg_w},{cards_h}" font="Regular;1"
            backgroundColor="#081426" transparent="0" zPosition="2" />
    <widget name="details_right_bg" position="{right_bg_x},{cards_y}"
            size="{right_bg_w},{cards_h}" font="Regular;1"
            backgroundColor="#081426" transparent="0" zPosition="2" />
    <widget name="details_left_top" position="{left_bg_x},{cards_y}"
            size="{left_bg_w},{border}" font="Regular;1"
            backgroundColor="#155E75" transparent="0" zPosition="3" />
    <widget name="details_right_top" position="{right_bg_x},{cards_y}"
            size="{right_bg_w},{border}" font="Regular;1"
            backgroundColor="#155E75" transparent="0" zPosition="3" />
    <widget name="details_left" position="{left_text_x},{cards_text_y}"
            size="{left_text_w},{cards_text_h}" font="Regular;{details_font}"
            foregroundColor="#E2E8F0" transparent="1" zPosition="5"
            valign="center" halign="left" />
    <widget name="details_right" position="{right_text_x},{cards_text_y}"
            size="{right_text_w},{cards_text_h}" font="Regular;{details_font}"
            foregroundColor="#E2E8F0" transparent="1" zPosition="5"
            valign="center" halign="left" />
    <widget name="details" position="{message_x},{message_y}"
            size="{message_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#9DE7D0" transparent="1" zPosition="5"
            valign="center" halign="center" />

    <widget name="legend_bg" position="{legend_x},{legend_y}"
            size="{legend_w},{legend_h}" font="Regular;1"
            backgroundColor="#081426" transparent="0" zPosition="2" />
    <widget name="legend_green_dot" position="{legend_green_dot_x},{legend_dot_y}"
            size="{legend_dot},{legend_dot}" font="Regular;1"
            backgroundColor="#39FF74" transparent="0" zPosition="5" />
    <widget name="legend_green" position="{legend_green_x},{legend_text_y}"
            size="{legend_green_w},{legend_text_h}" font="Regular;{legend_font}"
            foregroundColor="#E2E8F0" transparent="1" zPosition="5"
            valign="center" halign="left" />
    <widget name="legend_yellow_dot" position="{legend_yellow_dot_x},{legend_dot_y}"
            size="{legend_dot},{legend_dot}" font="Regular;1"
            backgroundColor="#FBBF24" transparent="0" zPosition="5" />
    <widget name="legend_yellow" position="{legend_yellow_x},{legend_text_y}"
            size="{legend_yellow_w},{legend_text_h}" font="Regular;{legend_font}"
            foregroundColor="#E2E8F0" transparent="1" zPosition="5"
            valign="center" halign="left" />
    <widget name="legend_red_dot" position="{legend_red_dot_x},{legend_dot_y}"
            size="{legend_dot},{legend_dot}" font="Regular;1"
            backgroundColor="#FF3B4E" transparent="0" zPosition="5" />
    <widget name="legend_red" position="{legend_red_x},{legend_text_y}"
            size="{legend_red_w},{legend_text_h}" font="Regular;{legend_font}"
            foregroundColor="#E2E8F0" transparent="1" zPosition="5"
            valign="center" halign="left" />

    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"
            font="Regular;{footer_font}" foregroundColor="#C8D2E3"
            backgroundColor="#080E1A" transparent="0"
            valign="center" halign="center" zPosition="5" />
</screen>
""".format(
        width=width,
        height=height,
        margin=margin,
        content_width=content_width,
        header_y=px(30),
        header_h=px(58),
        title_font=px(38),
        account_y=px(88),
        account_h=px(46),
        account_font=px(24),
        panel_x=px(48),
        panel_y=px(150),
        panel_w=width - px(96),
        panel_h=px(770),
        panel_bottom_y=px(917),
        panel_right_x=width - px(51),
        border=px(3),
        traffic_x=px(115),
        traffic_y=px(225),
        traffic_w=px(190),
        traffic_h=px(430),
        traffic_overlay_x=px(140),
        traffic_red_y=px(235),
        traffic_yellow_y=px(370),
        traffic_green_y=px(505),
        traffic_overlay_size=px(140),
        traffic_base_path=HEALTH_TRAFFIC_PATHS["base"],
        traffic_red_path=HEALTH_TRAFFIC_PATHS["red"],
        traffic_yellow_path=HEALTH_TRAFFIC_PATHS["yellow"],
        traffic_green_path=HEALTH_TRAFFIC_PATHS["green"],
        status_x=px(310),
        status_y=px(295),
        status_w=px(330),
        status_h=px(110),
        status_font=px(36),
        badge_x=px(330),
        badge_y=px(425),
        badge_w=px(290),
        badge_h=px(66),
        badge_font=px(25),
        divider_x=px(660),
        divider_y=px(210),
        divider_h=px(470),
        left_bg_x=px(700),
        left_bg_w=px(510),
        right_bg_x=px(1230),
        right_bg_w=px(570),
        cards_y=px(210),
        cards_h=px(450),
        left_text_x=px(730),
        left_text_w=px(450),
        right_text_x=px(1260),
        right_text_w=px(510),
        cards_text_y=px(235),
        cards_text_h=px(400),
        details_font=px(27),
        message_x=px(700),
        message_y=px(675),
        message_w=px(1100),
        message_h=px(76),
        message_font=px(24),
        legend_x=px(110),
        legend_y=px(770),
        legend_w=width - px(220),
        legend_h=px(105),
        legend_dot=px(24),
        legend_dot_y=px(811),
        legend_text_y=px(790),
        legend_text_h=px(66),
        legend_font=px(24),
        legend_green_dot_x=px(205),
        legend_green_x=px(250),
        legend_green_w=px(250),
        legend_yellow_dot_x=px(700),
        legend_yellow_x=px(745),
        legend_yellow_w=px(450),
        legend_red_dot_x=px(1270),
        legend_red_x=px(1315),
        legend_red_w=px(330),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


class GTFeatureScreen(Screen):
    def __init__(self, session, title, message):
        self.skin = decorate_remote_footer(
            _feature_skin(),
            FEATURE_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND)
        self["tmdb_logo"] = Pixmap()
        attach_pixmap(
            self,
            "tmdb_logo",
            TMDB_LOGO_PATH,
            key="about-tmdb-logo",
        )
        self["header"] = Label(_(title))
        self["message_panel"] = Label("")
        self["message"] = Label("")
        self["message_scroll_track"] = Label("")
        self["message_scroll_thumb"] = Label("")
        hide_scrollbar(self, "message_scroll")
        width, height, px = _scale()
        self._message_visible_lines = visible_count(18)
        self._message_offset = 0
        self._message_scroll_geometry = (
            width - px(64) - px(18),
            px(170),
            max(1, px(8)),
            px(680),
        )
        wrap_columns = visible_count(
            max(52, int((width - px(200)) / max(1, px(15))))
        )
        self._message_lines = self._wrapped_message_lines(
            _(message),
            wrap_columns,
        )
        self._refresh_message()
        self["footer"] = Label("")
        install_remote_footer(self, FEATURE_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.close,
                "cancel": self.close,
                "red": self.close,
                "up": self.scroll_up,
                "down": self.scroll_down,
                "upRepeated": self.scroll_up,
                "downRepeated": self.scroll_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
            },
            -1,
        )
        self.setTitle(_(title))

    @staticmethod
    def _wrapped_message_lines(message, columns):
        lines = []
        for source_line in str(message or "").splitlines():
            if not source_line.strip():
                lines.append("")
                continue
            lines.extend(
                textwrap.wrap(
                    source_line,
                    width=max(1, int(columns)),
                    break_long_words=False,
                    break_on_hyphens=False,
                    replace_whitespace=False,
                )
                or [""]
            )
        return lines or [""]

    def _refresh_message(self):
        total = len(self._message_lines)
        visible = max(1, int(self._message_visible_lines))
        maximum = max(0, total - visible)
        self._message_offset = min(max(0, self._message_offset), maximum)
        window = self._message_lines[
            self._message_offset : self._message_offset + visible
        ]
        self["message"].setText("\n".join(window))
        if maximum <= 0:
            hide_scrollbar(self, "message_scroll")
            return
        selected = int(
            round(float(self._message_offset) * (total - 1) / maximum)
        )
        update_scrollbar(
            self,
            "message_scroll",
            total,
            selected,
            visible,
            self._message_scroll_geometry,
        )

    def scroll_up(self):
        if self._message_offset > 0:
            self._message_offset -= 1
            self._refresh_message()

    def scroll_down(self):
        maximum = max(
            0,
            len(self._message_lines) - self._message_visible_lines,
        )
        if self._message_offset < maximum:
            self._message_offset += 1
            self._refresh_message()

    def page_up(self):
        self._message_offset -= max(1, self._message_visible_lines - 2)
        self._refresh_message()

    def page_down(self):
        self._message_offset += max(1, self._message_visible_lines - 2)
        self._refresh_message()


class GTWeatherLocationSearchScreen(Screen):
    page_size = 7

    def __init__(self, session, query, searcher=None):
        self.skin = decorate_remote_footer(
            _weather_location_search_skin(),
            WEATHER_LOCATION_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        self.searcher = searcher or OpenMeteoClient().search_locations
        self.query = clean_dynamic_text(query)
        self["location_list"] = List([])
        self.entries = self["location_list"].list
        self.selected_index = 0
        self._closed = False
        self._loading = False
        self._search_ready = False
        self._search_error = ""
        self._search_result = []
        self._search_job = None
        self._timer = eTimer()
        _connect_timer(self._timer, self._poll_search)

        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(_("Weather"))
        self["subtitle"] = Label(_("Search"))
        self["query"] = Label("")
        for index in range(self.page_size):
            self["row_bg_{}".format(index)] = Label("")
            self["focus_{}".format(index)] = Label("")
            self["marker_{}".format(index)] = Label("")
            self["result_{}".format(index)] = Label("")
        self["location_scroll_track"] = Label("")
        self["location_scroll_thumb"] = Label("")
        hide_scrollbar(self, "location_scroll")
        self["message"] = Label("")
        self["footer"] = Label("")
        install_remote_footer(self, WEATHER_LOCATION_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.select_current,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "green": self.new_search,
                "red": self.close,
            },
            -1,
        )
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refresh)
        self.setTitle(_("Weather"))
        self._refresh()
        self._start_search(self.query)

    def _start_search(self, query):
        query = clean_dynamic_text(query)
        if not query or self._loading:
            return
        self.query = query
        self._set_entries([])
        self.selected_index = 0
        self._search_result = []
        self._search_error = ""
        self._search_ready = False
        self._loading = True
        self._refresh()
        _cancel_ui_job(self._search_job)
        job = _UIJobToken(12)
        self._search_job = job
        worker = threading.Thread(
            target=lambda: self._run_search(job),
        )
        worker.daemon = True
        worker.start()
        self._timer.start(150, True)

    def _set_entries(self, entries):
        self.entries = list(entries or [])
        self["location_list"].setList(self.entries)

    def _run_search(self, job=None):
        result = []
        error_text = ""
        try:
            if job is not None:
                job.check()
            values = self.searcher(
                self.query,
                count=10,
                cancel_event=(
                    job.cancel_event if job is not None else None
                ),
                deadline=(job.deadline if job is not None else None),
            )
            entries = []
            seen = set()
            for value in list(values or [])[:10]:
                try:
                    location = (
                        value
                        if isinstance(value, WeatherLocation)
                        else WeatherLocation.from_dict(value)
                    )
                except (TypeError, ValueError):
                    continue
                if location.identity in seen:
                    continue
                seen.add(location.identity)
                entries.append(location)
            result = entries
        except WeatherError as error:
            error_text = str(error)
        except Exception:
            error_text = N_("Weather data unavailable.")
        if self._closed or (job is not None and not job.active()):
            return
        self._search_result = result
        self._search_error = error_text
        self._search_ready = True

    def _poll_search(self):
        if self._closed:
            return
        if not self._search_ready:
            if self._search_job is not None and self._search_job.expired():
                self._loading = False
                self._search_job = None
                self._search_error = N_("Weather data unavailable.")
                self._refresh()
                return
            self._timer.start(150, True)
            return
        self._loading = False
        self._search_job = None
        self._set_entries(self._search_result)
        self.selected_index = 0
        self._refresh()

    def _refresh(self):
        query = clean_dynamic_text(self.query)
        _ellipsize_inset_text(
            self["query"],
            "  {}".format(query),
            fallback_chars=96,
        )
        count = len(self.entries)
        if count:
            page_start = int(self.selected_index / self.page_size) * self.page_size
            page_entries = self.entries[page_start : page_start + self.page_size]
        else:
            page_start = 0
            page_entries = []
        for offset in range(self.page_size):
            row_bg = self["row_bg_{}".format(offset)]
            focus = self["focus_{}".format(offset)]
            marker = self["marker_{}".format(offset)]
            label = self["result_{}".format(offset)]
            if offset >= len(page_entries):
                label.setText("")
                row_bg.hide()
                focus.hide()
                marker.hide()
                continue
            index = page_start + offset
            row_bg.show()
            ellipsize_dynamic_text(
                label,
                page_entries[offset].display_name,
                fallback_chars=88,
            )
            if index == self.selected_index:
                focus.show()
                marker.show()
            else:
                focus.hide()
                marker.hide()
        if self._loading:
            self["message"].setText(_("Loading..."))
        elif self._search_error:
            fit_dynamic_text(
                self["message"],
                clean_dynamic_text(_(self._search_error)),
                max_lines=3,
                fallback_chars=96,
            )
        elif not count:
            self["message"].setText(_("No items found."))
        else:
            self["message"].setText(
                "{} / {}".format(self.selected_index + 1, count)
            )
        width, height, px = _scale()
        update_scrollbar(
            self,
            "location_scroll",
            count,
            self.selected_index,
            self.page_size,
            (px(1840), px(220), px(8), px(606)),
        )

    def _select(self, index):
        if self._loading or not self.entries:
            return
        self.selected_index = index % len(self.entries)
        self._refresh()

    def move_up(self):
        self._select(self.selected_index - 1)

    def move_down(self):
        self._select(self.selected_index + 1)

    def _move_page(self, delta):
        if self._loading or not self.entries:
            return
        current_start = int(self.selected_index / self.page_size) * self.page_size
        target_start = current_start + (int(delta) * self.page_size)
        last_start = int((len(self.entries) - 1) / self.page_size) * self.page_size
        target_start = max(0, min(target_start, last_start))
        if target_start == current_start:
            return
        row = self.selected_index - current_start
        target_count = min(self.page_size, len(self.entries) - target_start)
        self.selected_index = target_start + min(row, target_count - 1)
        self._refresh()

    def page_up(self):
        self._move_page(-1)

    def page_down(self):
        self._move_page(1)

    def select_current(self):
        if self._loading or not self.entries:
            return
        self.close(self.entries[self.selected_index])

    def new_search(self):
        if self._loading:
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(_("The virtual keyboard is unavailable on this image."))
            return
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        self.session.openWithCallback(
            self._query_entered,
            VirtualKeyBoard,
            title=_("Search"),
            text=self.query,
        )

    def _query_entered(self, value):
        query = str(value or "").strip()
        if not query:
            self["message"].setText(_("Cancel"))
            return
        self._start_search(query)

    def _stop(self):
        self._closed = True
        _cancel_ui_job(self._search_job)
        self._search_job = None
        try:
            self._timer.stop()
        except Exception:
            pass


class GTWeatherSettingsScreen(Screen):
    LABELS = (
        N_("Show weather"),
        N_("City"),
        N_("Temperature unit"),
        N_("Last update"),
    )

    def __init__(
        self,
        session,
        settings_loader=None,
        settings_saver=None,
        weather_service=None,
        update_loader=None,
        location_searcher=None,
    ):
        self.skin = decorate_remote_footer(
            _weather_settings_skin(),
            WEATHER_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        self.settings_loader = settings_loader or load_weather_settings
        self.settings_saver = settings_saver or save_weather_settings
        self.weather_service = weather_service or WeatherService(OpenMeteoClient())
        weather_client = getattr(self.weather_service, "client", None)
        self.location_searcher = location_searcher or getattr(
            weather_client,
            "search_locations",
            None,
        )
        if self.location_searcher is None:
            self.location_searcher = OpenMeteoClient().search_locations
        self.update_loader = update_loader or last_weather_update
        try:
            self._last_update_text = self.update_loader()
        except Exception:
            self._last_update_text = ""
        self.settings = self.settings_loader()
        self.selected_index = 0
        self._closed = False
        self._city_result = None
        self._city_error = ""
        self._city_ready = False
        self._city_checking = False
        self._pending_city = ""
        self._pending_location = None
        self._city_job = None
        self._timer = eTimer()
        _connect_timer(self._timer, self._poll_city)

        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(_("Settings"))
        self["subtitle"] = Label(_("WEATHER"))
        for index, label in enumerate(self.LABELS):
            self["row_bg_{}".format(index)] = Label("")
            self["focus_{}".format(index)] = Label("")
            self["label_{}".format(index)] = Label(_(label))
            self["value_{}".format(index)] = Label("")
        self["message"] = Label(
            _("Press OK on the City row to open the keyboard.")
        )
        self["footer"] = Label("")
        install_remote_footer(self, WEATHER_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.activate_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.change_selected,
                "right": self.change_selected,
                "red": self.close,
            },
            -1,
        )
        self._refresh()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refresh)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop)
        self.setTitle(_("Settings - Weather"))

    def _refresh(self):
        values = (
            _("On") if self.settings.enabled else _("Off"),
            self.settings.location_display_name,
            "°{}".format(self.settings.unit),
            self._last_update_text,
        )
        for index, value in enumerate(values):
            widget = self["value_{}".format(index)]
            if index == 1:
                ellipsize_dynamic_text(
                    widget,
                    value,
                    fallback_chars=44,
                )
            else:
                widget.setText(str(value))
            if index == self.selected_index:
                self["focus_{}".format(index)].show()
            else:
                self["focus_{}".format(index)].hide()

    def move_up(self):
        if self._city_checking:
            return
        self.selected_index = (self.selected_index - 1) % len(self.LABELS)
        self._refresh()

    def move_down(self):
        if self._city_checking:
            return
        self.selected_index = (self.selected_index + 1) % len(self.LABELS)
        self._refresh()

    def _save(self):
        try:
            self.settings_saver(self.settings)
        except (IOError, OSError, TypeError, ValueError) as error:
            fit_dynamic_text(
                self["message"],
                _("Settings could not be saved: {}").format(str(error)),
                max_lines=3,
                fallback_chars=96,
            )
            return False
        return True

    def change_selected(self):
        if self._city_checking:
            return
        if self.selected_index == 0:
            self.settings.enabled = not self.settings.enabled
            if self._save():
                self["message"].setText(_("Weather display updated."))
        elif self.selected_index == 2:
            self.settings.unit = "F" if self.settings.unit == "C" else "C"
            if self._save():
                self["message"].setText(_("Temperature unit updated."))
        elif self.selected_index == 1:
            self.edit_city()
            return
        self._refresh()

    def activate_selected(self):
        if self.selected_index in (0, 2):
            self.change_selected()
        elif self.selected_index == 1:
            self.edit_city()
        else:
            self["message"].setText(
                _("The last-update time refreshes when weather data is received.")
            )

    def edit_city(self):
        if self._city_checking:
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(_("The virtual keyboard is unavailable on this image."))
            return
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        self.session.openWithCallback(
            self._city_entered,
            VirtualKeyBoard,
            title=_("City"),
            text=self.settings.city,
        )

    def _city_entered(self, value):
        city = str(value or "").strip()
        if not city:
            self["message"].setText(_("City change cancelled."))
            return
        self.session.openWithCallback(
            self._location_selected,
            GTWeatherLocationSearchScreen,
            city,
            searcher=self.location_searcher,
        )

    def _location_selected(self, location=None):
        if location is None:
            return
        if not isinstance(location, WeatherLocation):
            try:
                location = WeatherLocation.from_dict(location)
            except (TypeError, ValueError):
                self["message"].setText(_("Weather data unavailable."))
                return
        self._pending_location = location
        self._begin_city_check(location.name)

    def _begin_city_check(self, city):
        self._pending_city = city
        self._city_result = None
        self._city_error = ""
        self._city_ready = False
        self._city_checking = True
        fit_dynamic_text(
            self["message"],
            _("Checking {}...").format(city),
            max_lines=3,
            fallback_chars=96,
        )
        _cancel_ui_job(self._city_job)
        job = _UIJobToken(12)
        self._city_job = job
        worker = threading.Thread(
            target=lambda: self._run_city_check(job),
        )
        worker.daemon = True
        worker.start()
        self._timer.start(150, True)

    def _run_city_check(self, job=None):
        result = None
        error_text = ""
        try:
            if job is not None:
                job.check()
            candidate = WeatherSettings(
                True,
                self._pending_city,
                self.settings.unit,
            )
            if self._pending_location is not None:
                candidate.set_location(self._pending_location)
            result = self.weather_service.get(
                candidate,
                force=True,
                cancel_event=(
                    job.cancel_event if job is not None else None
                ),
                deadline=(job.deadline if job is not None else None),
            )
        except WeatherError as error:
            error_text = str(error)
        except Exception:
            error_text = "Weather data unavailable."
        if self._closed or (job is not None and not job.active()):
            return
        self._city_result = result
        self._city_error = error_text
        self._city_ready = True

    def _poll_city(self):
        if self._closed:
            return
        if not self._city_ready:
            if self._city_job is not None and self._city_job.expired():
                self._city_checking = False
                self._city_job = None
                self._pending_location = None
                self["message"].setText(_("Weather data unavailable."))
                return
            self._timer.start(150, True)
            return
        self._city_checking = False
        self._city_job = None
        if self._city_error or self._city_result is None:
            fit_dynamic_text(
                self["message"],
                clean_dynamic_text(
                    _(
                        self._city_error
                        or N_("No weather forecast is available for this city.")
                    )
                ),
                max_lines=3,
                fallback_chars=96,
            )
            self._pending_location = None
            return
        if self._pending_location is not None:
            self.settings.set_location(self._pending_location)
        else:
            self.settings.clear_location()
            self.settings.city = self._city_result.city
        saved_name = self.settings.location_display_name
        self._pending_location = None
        if self._save():
            fit_dynamic_text(
                self["message"],
                _("{} was saved.").format(saved_name),
                max_lines=3,
                fallback_chars=96,
            )
        try:
            self._last_update_text = self.update_loader()
        except Exception:
            pass
        self._refresh()

    def _stop(self):
        self._closed = True
        _cancel_ui_job(self._city_job)
        self._city_job = None
        try:
            self._timer.stop()
        except Exception:
            pass


class GTAppearanceSettingsScreen(Screen):
    """Transactional text-size and cinematic VOD appearance selector."""

    VALUES = ("standard", "large", "very_large")
    VALUE_LABELS = {
        "standard": N_("Standard"),
        "large": N_("Large"),
        "very_large": N_("Very large"),
    }
    LABELS = (N_("Text size"), N_("Cinematic view"))

    def __init__(
        self,
        session,
        value="standard",
        cinematic_view=True,
    ):
        self.skin = decorate_remote_footer(
            _appearance_settings_skin(),
            APPEARANCE_FOOTER_ITEMS,
            skin_fonts_scaled=True,
        )
        Screen.__init__(self, session)
        self.value = normalize_text_size(value)
        self.cinematic_view = bool(cinematic_view)
        self.selected_index = 0

        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(_("Appearance"))
        self["brand"] = Label("GT IPTV PLAYER PRO")
        self["panel"] = Label("")
        self["section"] = Label(_("Appearance"))
        for index, label in enumerate(self.LABELS):
            self["row_bg_{}".format(index)] = Label("")
            self["row_focus_{}".format(index)] = Label("")
            self["row_marker_{}".format(index)] = Label("")
            self["row_label_{}".format(index)] = Label(_(label))
            self["value_bg_{}".format(index)] = Label("")
            self["row_value_{}".format(index)] = Label("")
        self["message"] = Label(_("Select"))
        self["footer"] = Label("")
        install_remote_footer(self, APPEARANCE_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            [
                "OkCancelActions",
                "DirectionActions",
                "ColorActions",
            ],
            {
                "ok": self.select,
                "cancel": self.cancel,
                "back": self.cancel,
                "left": self.change_left,
                "right": self.change_right,
                "up": self.move_up,
                "down": self.move_down,
                "green": self.select,
                "red": self.cancel,
            },
            -1,
        )
        self._refresh()
        self.setTitle("{} - {}".format(_("Settings"), _("Appearance")))

    def _refresh(self):
        self["row_value_0"].setText(_(self.VALUE_LABELS[self.value]))
        self["row_value_1"].setText(
            _("On") if self.cinematic_view else _("Off")
        )
        for index in range(len(self.LABELS)):
            selected = index == self.selected_index
            for name in ("row_focus_{}", "row_marker_{}"):
                if selected:
                    self[name.format(index)].show()
                else:
                    self[name.format(index)].hide()

    def move_up(self):
        self.selected_index = (
            self.selected_index - 1
        ) % len(self.LABELS)
        self._refresh()

    def move_down(self):
        self.selected_index = (
            self.selected_index + 1
        ) % len(self.LABELS)
        self._refresh()

    def _change(self, step):
        if self.selected_index == 1:
            self.cinematic_view = not self.cinematic_view
        else:
            try:
                index = self.VALUES.index(self.value)
            except ValueError:
                index = 0
            self.value = self.VALUES[
                (index + int(step)) % len(self.VALUES)
            ]
        self._refresh()

    def change_left(self):
        self._change(-1)

    def change_right(self):
        self._change(1)

    def select(self):
        self.close(
            {
                "ui_text_size": self.value,
                "cinematic_view": self.cinematic_view,
            }
        )

    def cancel(self):
        self.close(None)


class GTPlayerSettingsScreen(Screen):
    """Player settings page designed for remote-control navigation."""

    LABELS = (
        N_("Live TV service type"),
        N_("Movie service type"),
        N_("Series service type"),
        N_("TMDb metadata enrichment"),
        N_("TMDb API key / token"),
        N_("DVB EPG"),
        N_("Category edit lock"),
        N_("Appearance"),
        N_("Select playlist"),
    )

    def __init__(
        self,
        session,
        settings_loader=None,
        settings_saver=None,
        metadata_client_factory=None,
        metadata_cache_clearer=None,
        category_lock_store=None,
    ):
        self.skin = decorate_remote_footer(
            _player_settings_skin(),
            PLAYER_SETTINGS_FOOTER_ITEMS,
            skin_fonts_scaled=True,
        )
        Screen.__init__(self, session)
        self.settings_loader = settings_loader or load_player_settings
        self.settings_saver = settings_saver or save_player_settings
        self._metadata_client_factory = (
            metadata_client_factory or TMDbMetadataClient
        )
        self._metadata_cache_clearer = (
            metadata_cache_clearer or clear_metadata_cache
        )
        self.category_lock_store = (
            category_lock_store
            if category_lock_store is not None
            else CategoryEditLockStore()
        )
        try:
            loaded = self.settings_loader()
        except Exception:
            loaded = PlayerSettings()
        if isinstance(loaded, PlayerSettings):
            self.settings = loaded.copy()
        elif isinstance(loaded, dict):
            self.settings = PlayerSettings.from_dict(loaded)
        else:
            self.settings = PlayerSettings()
        # Always expose every supported Enigma2/ServiceApp ID.  Older builds
        # may already have rewritten all three stored values to 4097, so using
        # those values or executable discovery to build this list would leave
        # the user with no route back to 5002.
        self._service_types = configurable_service_types()
        self.selected_index = 0
        self._original_metadata_signature = self._metadata_signature()
        self._tmdb_test_pending = False
        self._tmdb_test_result = None
        self._tmdb_test_job = None
        self._category_lock_prompt_open = False
        try:
            self._category_lock_status = self.category_lock_store.status()
        except Exception:
            self._category_lock_status = "corrupt"
        self._tmdb_test_timer = eTimer()
        _connect_timer(self._tmdb_test_timer, self._poll_tmdb_test)

        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(_("Settings"))
        self["brand"] = Label("GT IPTV PLAYER PRO")
        self["panel"] = Label("")
        self["player_tab_bg"] = Label("")
        self["player_tab"] = Label(_("Player / Codec"))
        self["language_tab_bg"] = Label("")
        self["language_tab"] = Label(_("LANGUAGE"))
        self["language_soon"] = Label(device_language_label())
        for index, label in enumerate(self.LABELS):
            self["row_bg_{}".format(index)] = Label("")
            self["row_focus_{}".format(index)] = Label("")
            self["row_marker_{}".format(index)] = Label("")
            self["row_label_{}".format(index)] = Label(_(label))
            self["value_bg_{}".format(index)] = Label("")
            self["row_value_{}".format(index)] = Label("")
        self["message"] = Label(
            _("Press GREEN to save the changes.")
        )
        self["footer"] = Label("")
        install_remote_footer(self, PLAYER_SETTINGS_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            [
                "OkCancelActions",
                "DirectionActions",
                "ColorActions",
                "SetupActions",
            ],
            {
                "ok": self.activate_selected,
                "cancel": self.cancel,
                "back": self.cancel,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.change_left,
                "right": self.change_right,
                "green": self.save,
                "blue": self.test_tmdb,
                "red": self.cancel,
            },
            -1,
        )
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop_tmdb_test)
        if hasattr(self, "onShown"):
            self.onShown.append(self._refresh_device_language)
        self._refresh()
        self.setTitle(_("Settings - Player / Codec"))

    def _refresh_device_language(self):
        self["language_soon"].setText(device_language_label())
        self._refresh()

    def _metadata_signature(self):
        return (
            bool(getattr(self.settings, "metadata_enabled", True)),
            str(getattr(self.settings, "tmdb_api_key", "") or "").strip(),
            metadata_language(),
        )

    def _field_name(self, index):
        return (
            "live_service_type",
            "movie_service_type",
            "series_service_type",
            "metadata_enabled",
            "tmdb_api_key",
        )[index]

    def _options(self, index):
        if index in (0, 1, 2):
            return list(self._service_types)
        if index == 3:
            return [False, True]
        return []

    def _value_text(self, index):
        if index in (5, 7, 8):
            return _("Open")
        if index == 6:
            status = getattr(self, "_category_lock_status", None)
            if status is None:
                try:
                    status = self.category_lock_store.status()
                except Exception:
                    status = "corrupt"
                self._category_lock_status = status
            if status == "enabled":
                return _("On")
            if status == "disabled":
                return _("Off")
            return _("Error")
        value = getattr(self.settings, self._field_name(index))
        if index in (0, 1, 2):
            return service_type_label(value)
        if index == 3:
            return _("On") if value else _("Off")
        if index == 4:
            return (
                _("Configured ••••{}").format(value[-4:])
                if value
                else _("Not configured")
            )
        return str(value)

    def _refresh(self):
        for index in range(len(self.LABELS)):
            self["row_value_{}".format(index)].setText(
                self._value_text(index)
            )
            selected = index == self.selected_index
            for name in ("row_focus_{}", "row_marker_{}"):
                widget = self[name.format(index)]
                if selected:
                    widget.show()
                else:
                    widget.hide()

    def move_up(self):
        self.selected_index = (self.selected_index - 1) % len(self.LABELS)
        self._refresh()

    def move_down(self):
        self.selected_index = (self.selected_index + 1) % len(self.LABELS)
        self._refresh()

    def _change(self, step):
        if self.selected_index == 5:
            self["message"].setText(
                _("Press OK to open the DVB EPG settings.")
            )
            return
        if self.selected_index == 6:
            self["message"].setText(
                _("Press OK to turn the category edit lock on or off.")
            )
            return
        if self.selected_index == 7:
            self["message"].setText(_("Select"))
            return
        if self.selected_index == 8:
            self["message"].setText(_("Select"))
            return
        if self.selected_index == 4:
            self["message"].setText(
                _("Press OK to enter or change the TMDb key.")
            )
            return
        field = self._field_name(self.selected_index)
        options = self._options(self.selected_index)
        current = getattr(self.settings, field)
        try:
            index = options.index(current)
        except ValueError:
            index = 0
        setattr(self.settings, field, options[(index + step) % len(options)])
        self["message"].setText(
            _("Press GREEN to save the changes.")
        )
        self._refresh()

    def change_left(self):
        self._change(-1)

    def change_right(self):
        self._change(1)

    def activate_selected(self):
        if self.selected_index == 5:
            self.open_dvb_epg()
        elif self.selected_index == 6:
            self.toggle_category_lock()
        elif self.selected_index == 7:
            self.open_appearance()
        elif self.selected_index == 8:
            self.open_playlist_files()
        elif self.selected_index == 4:
            self.edit_tmdb_key()
        else:
            self.change_right()

    def _open_category_code(
        self,
        callback,
        title,
        prompt,
        digits=4,
        allow_recovery=False,
    ):
        if self._category_lock_prompt_open:
            return False
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["message"].setText(
                _("The PIN entry screen is unavailable on this image.")
            )
            return False
        self._category_lock_prompt_open = True

        def completed(value):
            self._category_lock_prompt_open = False
            callback(value)

        try:
            opener(
                completed,
                GTCategoryCodeInputScreen,
                title=title,
                prompt=prompt,
                digits=digits,
                allow_recovery=allow_recovery,
            )
        except Exception:
            self._category_lock_prompt_open = False
            self["message"].setText(
                _("The PIN entry screen is unavailable on this image.")
            )
            return False
        return True

    def _show_category_lock_message(self, message, callback=None):
        opener = getattr(self.session, "openWithCallback", None)
        if callback is not None and callable(opener):
            opener(callback, MessageBox, message, type=MessageBox.TYPE_INFO)
            return
        self.session.open(MessageBox, message, type=MessageBox.TYPE_INFO)

    def toggle_category_lock(self):
        status = getattr(self, "_category_lock_status", None)
        if status is None:
            try:
                status = self.category_lock_store.status()
            except Exception:
                status = "corrupt"
            self._category_lock_status = status
        if status not in ("disabled", "enabled"):
            self._show_category_lock_message(
                _(
                    "Category edit lock data is damaged. Remove and "
                    "reinstall the plugin to reset only this lock."
                )
            )
            return
        if status == "enabled":
            if self._category_lock_prompt_open:
                return
            opener = getattr(self.session, "openWithCallback", None)
            if not callable(opener):
                self["message"].setText(
                    _("The confirmation screen is unavailable on this image.")
                )
                return
            self._category_lock_prompt_open = True

            def completed(answer):
                self._category_lock_prompt_open = False
                self._category_lock_disable_confirmed(answer)

            try:
                opener(
                    completed,
                    MessageBox,
                    _("Disable the category edit lock?"),
                    type=MessageBox.TYPE_YESNO,
                    default=False,
                )
            except Exception:
                self._category_lock_prompt_open = False
                self["message"].setText(
                    _("The confirmation screen is unavailable on this image.")
                )
            return
        self._open_category_code(
            self._category_lock_pin_created,
            _("Create category edit PIN"),
            _("Enter a new 4-digit PIN."),
        )

    def _category_lock_pin_created(self, pin):
        if pin is None:
            return
        self._pending_category_pin = str(pin)
        opened = self._open_category_code(
            self._category_lock_pin_confirmed,
            _("Confirm category edit PIN"),
            _("Enter the same 4-digit PIN again."),
        )
        if not opened:
            self._pending_category_pin = ""

    def _category_lock_pin_confirmed(self, confirmation):
        pin = getattr(self, "_pending_category_pin", "")
        self._pending_category_pin = ""
        if confirmation is None:
            return
        if str(confirmation) != pin:
            self["message"].setText(
                _("The PIN entries do not match. Nothing was changed.")
            )
            return
        try:
            recovery_code = self.category_lock_store.configure(
                pin,
                confirmation,
            )
        except Exception:
            recovery_code = ""
        if not recovery_code:
            self["message"].setText(
                _(
                    "Category edit lock could not be saved. "
                    "Check the receiver storage."
                )
            )
            self._refresh()
            return
        self._category_lock_status = "enabled"
        self._refresh()
        self["message"].setText(_("Category edit lock enabled."))
        self._show_category_lock_message(
            _(
                "Write down this recovery code. It is shown only once:\n\n"
                "{}\n\nIf you forget both codes, removing and reinstalling "
                "the plugin resets only this lock."
            ).format(recovery_code)
        )

    def _category_lock_disable_confirmed(self, answer):
        if not answer:
            return
        self._open_category_code(
            self._category_lock_disable_code_entered,
            _("Disable category edit lock"),
            _("Enter the current 4-digit PIN."),
            allow_recovery=True,
        )

    def _category_lock_disable_code_entered(self, value):
        if value is None:
            return
        if value == RECOVERY_REQUEST:
            self._open_category_code(
                self._category_lock_disable_recovery_entered,
                _("Category lock recovery"),
                _("Enter the 12-digit recovery code."),
                digits=12,
            )
            return
        try:
            valid = self.category_lock_store.verify_pin(value)
        except Exception:
            valid = False
        if not valid:
            self["message"].setText(_("Incorrect PIN. Lock unchanged."))
            return
        self._disable_category_lock()

    def _category_lock_disable_recovery_entered(self, value):
        if value is None:
            return
        try:
            valid = self.category_lock_store.verify_recovery(value)
        except Exception:
            valid = False
        if not valid:
            self["message"].setText(
                _("Incorrect recovery code. Lock unchanged.")
            )
            return
        self._disable_category_lock()

    def _disable_category_lock(self):
        try:
            disabled = self.category_lock_store.reset()
        except Exception:
            disabled = False
        if disabled:
            self._category_lock_status = "disabled"
            self["message"].setText(
                _("Category edit lock disabled.")
            )
        else:
            self["message"].setText(
                _(
                    "Category edit lock could not be removed. "
                    "Check the receiver storage."
                )
            )
        self._refresh()

    def open_dvb_epg(self):
        try:
            from .dvb_epg_ui import GTDVBEPGSettingsScreen
        except ImportError:
            self["message"].setText(
                _("DVB EPG settings are unavailable on this image.")
            )
            return
        if hasattr(self.session, "openWithCallback"):
            self.session.openWithCallback(
                self._dvb_epg_closed,
                GTDVBEPGSettingsScreen,
            )
        else:
            self.session.open(GTDVBEPGSettingsScreen)

    def open_appearance(self):
        value = normalize_text_size(
            getattr(self.settings, "ui_text_size", "standard")
        )
        cinematic_view = bool(
            getattr(self.settings, "cinematic_view", True)
        )
        opener = getattr(self.session, "openWithCallback", None)
        if callable(opener):
            opener(
                self._appearance_closed,
                GTAppearanceSettingsScreen,
                value,
                cinematic_view,
            )
        else:
            self.session.open(
                GTAppearanceSettingsScreen,
                value,
                cinematic_view,
            )

    def open_playlist_files(self):
        opener = getattr(self.session, "openWithCallback", None)
        if callable(opener):
            opener(self._playlist_files_closed, GTPlaylistTypeScreen)
        else:
            self.session.open(GTPlaylistTypeScreen)

    def _playlist_files_closed(self, result=None):
        if result == PLAYLIST_FILES_CONNECTED:
            self.close(PLAYLIST_FILES_CONNECTED)
            return
        self["message"].setText(_("Select"))
        self._refresh()

    def _appearance_closed(self, value=None):
        if value is None:
            return
        if isinstance(value, dict):
            text_size = value.get("ui_text_size", self.settings.ui_text_size)
            cinematic_view = value.get(
                "cinematic_view",
                getattr(self.settings, "cinematic_view", True),
            )
        elif isinstance(value, (tuple, list)) and len(value) >= 2:
            text_size, cinematic_view = value[:2]
        else:
            # Retain compatibility with the original R78 callback contract.
            text_size = value
            cinematic_view = getattr(
                self.settings,
                "cinematic_view",
                True,
            )
        self.settings.ui_text_size = normalize_text_size(text_size)
        self.settings.cinematic_view = bool(cinematic_view)
        self["message"].setText(_("Press GREEN to save the changes."))
        self._refresh()

    def _dvb_epg_closed(self, *args):
        self["message"].setText(
            _("DVB EPG settings closed.")
        )
        self._refresh()

    def edit_tmdb_key(self):
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(_("The virtual keyboard is unavailable on this image."))
            return
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        self.session.openWithCallback(
            self._tmdb_key_entered,
            VirtualKeyBoard,
            title=_(
                "Enter the TMDb API key or access token"
            ),
            text=self.settings.tmdb_api_key,
        )

    def _tmdb_key_entered(self, value):
        if value is None:
            self["message"].setText(_("TMDb key change cancelled."))
            return
        key = "".join(str(value or "").split())[:512]
        self.settings.tmdb_api_key = key
        self._tmdb_test_result = None
        self["message"].setText(
            _("TMDb key saved; press BLUE to test and GREEN to save.")
            if key
            else _("TMDb key cleared; press GREEN to save.")
        )
        self._refresh()

    def test_tmdb(self):
        if self._tmdb_test_pending:
            self["message"].setText(_("The TMDb connection test is still running..."))
            return
        if not str(self.settings.tmdb_api_key or "").strip():
            self["message"].setText(
                _("No TMDb key is configured. Press OK on the key row.")
            )
            return
        self._tmdb_test_pending = True
        self._tmdb_test_result = None
        self["message"].setText(_("Testing the TMDb connection and API key..."))
        _cancel_ui_job(self._tmdb_test_job)
        job = _UIJobToken(15)
        self._tmdb_test_job = job
        worker = threading.Thread(
            target=lambda: self._run_tmdb_test(job),
        )
        worker.daemon = True
        worker.start()
        self._tmdb_test_timer.start(80, True)

    def _run_tmdb_test(self, job=None):
        try:
            if job is not None:
                job.check()
            client = self._metadata_client_factory(
                api_key=self.settings.tmdb_api_key,
                enabled=True,
                language=metadata_language(),
            )
            scope_factory = getattr(client, "request_scope", None)
            if callable(scope_factory) and job is not None:
                with scope_factory(job.cancel_event, job.deadline):
                    client.test_connection()
            else:
                client.test_connection()
            if job is not None:
                job.check()
            message = _(
                "TMDb connection successful; the API key is valid."
            )
            if not self.settings.metadata_enabled:
                message += _(
                    " Metadata enrichment is currently disabled."
                )
            result = (True, message)
        except MetadataError as error:
            result = (
                False,
                _("TMDb test failed: {}.").format(_(str(error))),
            )
        except Exception:
            result = (False, _("TMDb test failed: could not connect."))
        if (
            self._tmdb_test_pending
            and (job is None or job.active())
        ):
            self._tmdb_test_result = result

    def _poll_tmdb_test(self):
        if not self._tmdb_test_pending:
            return
        if self._tmdb_test_result is None:
            if (
                self._tmdb_test_job is not None
                and self._tmdb_test_job.expired()
            ):
                self._tmdb_test_pending = False
                self._tmdb_test_job = None
                self["message"].setText(
                    _("TMDb test failed: could not connect.")
                )
                return
            self._tmdb_test_timer.start(80, True)
            return
        message = self._tmdb_test_result[1]
        self._tmdb_test_pending = False
        self._tmdb_test_job = None
        self["message"].setText(message)

    def _stop_tmdb_test(self):
        self._tmdb_test_pending = False
        _cancel_ui_job(self._tmdb_test_job)
        self._tmdb_test_job = None
        try:
            self._tmdb_test_timer.stop()
        except Exception:
            pass

    def save(self):
        try:
            result = self.settings_saver(self.settings)
        except Exception:
            result = False
        if result is False:
            self["message"].setText(
                _("Settings could not be saved. Check the receiver storage.")
            )
            return
        if self._metadata_signature() != self._original_metadata_signature:
            try:
                self._metadata_cache_clearer()
            except Exception:
                pass
        self.close(True)

    def cancel(self):
        self.close(False)


class _DirectAccountConnection(object):
    """Verify an account in-place and open the dashboard without a result screen."""

    def __init__(
        self,
        session,
        account,
        checker=None,
        on_started=None,
        on_success=None,
        on_failure=None,
        on_return=None,
    ):
        self.session = session
        self.account = account
        self.checker = checker or check_source_health
        self.on_started = on_started
        self.on_success = on_success
        self.on_failure = on_failure
        self.on_return = on_return
        self._checking = False
        self._closed = False
        self._result = None
        self._result_ready = False
        self._job = None
        self._timer = eTimer()
        _connect_timer(self._timer, self._poll_result)

    @property
    def active(self):
        return self._checking and not self._closed

    def start(self):
        if self.active:
            return False
        self._checking = True
        self._closed = False
        self._result = None
        self._result_ready = False
        if self.on_started is not None:
            self.on_started(self.account)
        _cancel_ui_job(self._job)
        job = _UIJobToken(UI_NETWORK_JOB_TIMEOUT_SECONDS)
        self._job = job
        worker = threading.Thread(
            target=lambda: self._run_check(job),
        )
        worker.daemon = True
        worker.start()
        self._timer.start(150, True)
        return True

    def _run_check(self, job=None):
        try:
            if job is not None:
                job.check()
            result = self.checker(self.account)
        except Exception:
            result = ServerHealth(
                "ERROR",
                detail=N_("The health check could not be completed."),
            )
        if self._closed or (job is not None and not job.active()):
            return
        self._result = result
        self._result_ready = True

    def _poll_result(self):
        if self._closed:
            return
        if not self._result_ready:
            if self._job is not None and self._job.expired():
                self._checking = False
                self._job = None
                result = ServerHealth(
                    "ERROR",
                    detail=N_("The health check could not be completed."),
                )
                if self.on_failure is not None:
                    self.on_failure(self.account, result)
                return
            self._timer.start(150, True)
            return
        self._checking = False
        self._job = None
        result = self._result
        if result.status in ("ONLINE", "BUSY", "STALE"):
            if self.on_success is not None:
                self.on_success(self.account, result)
            self._open_dashboard(result)
            return
        if self.on_failure is not None:
            self.on_failure(self.account, result)

    def _open_dashboard(self, result):
        try:
            self._timer.stop()
        except Exception:
            pass
        if hasattr(self.session, "openWithCallback"):
            self.session.openWithCallback(
                self._dashboard_closed,
                GTDashboardScreen,
                self.account,
                result,
            )
        else:
            self.session.open(GTDashboardScreen, self.account, result)

    def _dashboard_closed(self, *args):
        if self.on_return is not None:
            self.on_return(*args)

    def stop(self):
        self._closed = True
        self._checking = False
        _cancel_ui_job(self._job)
        self._job = None
        try:
            self._timer.stop()
        except Exception:
            pass


class GTAccountBatchTestScreen(Screen):
    """Sequential, cancellable account scan with inactive-account review."""

    page_size = 8

    def __init__(
        self,
        session,
        accounts,
        account_checker=None,
        batch_deleter=None,
    ):
        self.skin = decorate_remote_footer(
            _account_batch_test_skin(),
            ACCOUNT_BATCH_REVIEW_FOOTER_ITEMS,
            alternate_items=ACCOUNT_BATCH_RUNNING_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        self.accounts = tuple(accounts or ())
        self.account_checker = account_checker or check_source_health
        self.batch_deleter = batch_deleter or delete_sources
        self._batch_deleter_supports_limits = (
            batch_deleter is None or batch_deleter is delete_sources
        )
        self._state = "testing"
        self._started = False
        self._closed = False
        self._completed = 0
        self._current_account = None
        self._current_pending = False
        self._results = {}
        self._indexed_results = [None] * len(self.accounts)
        self._history = []
        self._inactive_accounts = []
        self._review_page = 0
        self._event_lock = threading.Lock()
        self._events = []
        self._cancel_event = threading.Event()
        self._close_after_delete_cancel = False
        self._confirmation_armed = False
        self._confirmation_open = False
        self._confirmation_attempts = 0
        self._confirmation_timer = eTimer()
        _connect_timer(
            self._confirmation_timer,
            self._open_inactive_confirmation,
        )
        self._timer = eTimer()
        _connect_timer(self._timer, self._poll_events)
        unused_width, unused_height, px = _scale()
        del unused_width, unused_height
        self._progress_width = max(1, px(1030))
        self._progress_height = max(3, px(18))

        attach_background(self, "accounts_bg", ACCOUNTS_BACKGROUND)
        for name in (
            "test_panel",
            "test_panel_top",
            "test_panel_bottom",
            "test_panel_left",
            "test_panel_right",
            "progress_track",
            "progress_fill",
        ):
            self[name] = Label("")
        self["header"] = Label(localized_upper(_("Information")))
        self["subtitle"] = Label("")
        self["review_page"] = Label("")
        self["progress_text"] = Label("")
        self["current_account"] = Label("")
        for index in range(self.page_size):
            self["result_name_{}".format(index)] = Label("")
            for suffix in ("", "_green", "_yellow", "_red"):
                self["result_status_{}{}".format(index, suffix)] = Label("")
            if index < self.page_size - 1:
                self["result_separator_{}".format(index)] = Label("")
        self["footer"] = Label("")
        install_remote_footer(
            self,
            ACCOUNT_BATCH_REVIEW_FOOTER_ITEMS,
        )
        set_remote_footer(self, ACCOUNT_BATCH_RUNNING_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            [
                "OkCancelActions",
                "DirectionActions",
                "ColorActions",
                "WizardActions",
            ],
            {
                "ok": self.continue_or_keep,
                "cancel": self.cancel_or_keep,
                "back": self.cancel_or_keep,
                "left": self.previous_page,
                "right": self.next_page,
                "leftRepeated": self.previous_page,
                "rightRepeated": self.next_page,
                "green": self.delete_inactive,
                "red": self.keep_inactive,
            },
            -1,
        )
        self._refresh_testing()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self.start)
            self.onLayoutFinish.append(self._apply_progress_fill)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop)
        self.setTitle(_("Information"))

    def _queue_event(self, *event):
        with self._event_lock:
            if not self._closed:
                self._events.append(event)

    def _drain_events(self):
        with self._event_lock:
            events = self._events
            self._events = []
        return events

    def start(self):
        if self._started or self._closed:
            return False
        self._started = True
        worker = threading.Thread(target=self._run_tests)
        worker.daemon = True
        worker.start()
        self._timer.start(ACCOUNT_BATCH_QUERY_POLL_MS, True)
        return True

    def _run_tests(self):
        for index, account in enumerate(self.accounts):
            if self._cancel_event.is_set():
                return
            self._queue_event("started", index, account)
            try:
                if self.account_checker is check_source_health:
                    result = self.account_checker(
                        account,
                        timeout=ACCOUNT_BATCH_QUERY_TIMEOUT_SECONDS,
                        cancel_event=self._cancel_event,
                        summary_only=True,
                    )
                else:
                    result = self.account_checker(account)
            except Exception:
                result = ServerHealth(
                    "ERROR",
                    detail=N_("The health check could not be completed."),
                    source_type=_source_type(account),
                )
            if self._cancel_event.is_set():
                return
            result = _sanitized_account_preview(result, _source_type(account))
            self._queue_event("result", index, account, result)
        if not self._cancel_event.is_set():
            self._queue_event("complete")

    def _poll_events(self):
        if self._closed:
            return
        terminal = False
        for event in self._drain_events():
            kind = event[0]
            if kind == "started":
                self._current_account = event[2]
                self._current_pending = True
                self._refresh_testing()
            elif kind == "result":
                unused_kind, result_index, account, result = event
                del unused_kind
                self._results[_source_result_key(account)] = result
                if 0 <= result_index < len(self._indexed_results):
                    self._indexed_results[result_index] = result
                self._history.append((account, result))
                self._completed += 1
                self._current_account = None
                self._current_pending = False
                self._refresh_testing()
            elif kind == "complete":
                terminal = True
                self._finish_testing()
            elif kind == "delete_done":
                terminal = True
                self._finish_delete(event[1], event[2])
        if (
            not terminal
            and not self._closed
            and self._state in ("testing", "deleting")
        ):
            self._timer.start(ACCOUNT_BATCH_QUERY_POLL_MS, True)

    def _render_entries(self, entries):
        for offset in range(self.page_size):
            name_widget = self["result_name_{}".format(offset)]
            if offset >= len(entries):
                name_widget.setText("")
                _set_signal_label(
                    self,
                    "result_status_{}".format(offset),
                    "",
                    HEALTH_SIGNAL_NEUTRAL,
                    fallback_chars=28,
                )
                continue
            account, status_text, signal_name = entries[offset]
            ellipsize_dynamic_text(
                name_widget,
                _account_test_display_name(account),
                fallback_chars=44,
            )
            _set_signal_label(
                self,
                "result_status_{}".format(offset),
                localized_upper(status_text),
                signal_name,
                fallback_chars=28,
            )

    def _refresh_testing(self):
        if self._state != "testing":
            return
        total = len(self.accounts)
        percent = int((self._completed * 100) / total) if total else 100
        self["header"].setText(localized_upper(_("Accounts")))
        self["subtitle"].setText(_("TESTING..."))
        self["review_page"].setText("")
        available = self.page_size - (1 if self._current_pending else 0)
        entries = []
        for account, result in self._history[-available:]:
            status_text, signal_name = _account_test_status_text(result)
            entries.append((account, status_text, signal_name))
        if self._current_pending and self._current_account is not None:
            entries.append(
                (
                    self._current_account,
                    _("TESTING..."),
                    HEALTH_SIGNAL_NEUTRAL,
                )
            )
        self._render_entries(entries)
        self["progress_text"].setText(
            "{} / {}  •  {}%".format(self._completed, total, percent)
        )
        if self._current_account is None:
            self["current_account"].setText("")
        else:
            ellipsize_dynamic_text(
                self["current_account"],
                "{}: {}".format(
                    _("Account"),
                    _account_test_display_name(self._current_account),
                ),
                fallback_chars=72,
            )
        self._apply_progress_fill(percent)

    def _apply_progress_fill(self, percent=None):
        if percent is None:
            total = len(self.accounts)
            percent = int((self._completed * 100) / total) if total else 100
        percent = max(0, min(100, int(percent)))
        fill = self["progress_fill"]
        if percent <= 0:
            fill.hide()
            return
        fill.show()
        instance = getattr(fill, "instance", None)
        if instance is None:
            return
        try:
            from enigma import eSize

            width = max(
                1,
                int(round(self._progress_width * (percent / 100.0))),
            )
            instance.resize(eSize(width, self._progress_height))
        except Exception:
            pass

    def _finish_testing(self):
        if self._closed or self._state != "testing":
            return
        self._inactive_accounts = [
            account
            for index, account in enumerate(self.accounts)
            if account_test_is_inactive(self._indexed_results[index])
        ]
        self._current_account = None
        self._current_pending = False
        self._apply_progress_fill(100)
        if not self._inactive_accounts:
            self.close(self._result_payload())
            return
        self._state = "confirming"
        self._review_page = 0
        set_remote_footer(self, ACCOUNT_BATCH_RUNNING_FOOTER_ITEMS)
        self._refresh_review()
        self._arm_inactive_confirmation()

    def _refresh_review(self):
        if self._state not in ("review", "confirming"):
            return
        count = len(self._inactive_accounts)
        page_count = max(1, int((count + self.page_size - 1) / self.page_size))
        self._review_page = max(0, min(self._review_page, page_count - 1))
        start = self._review_page * self.page_size
        page_accounts = self._inactive_accounts[start : start + self.page_size]
        self["header"].setText(localized_upper(_("Inactive")))
        self["subtitle"].setText(
            "{}: {}  •  {}?".format(
                localized_upper(_("Accounts")),
                count,
                localized_upper(_("Delete")),
            )
        )
        self["review_page"].setText(
            "{} / {} {}".format(
                self._review_page + 1,
                page_count,
                localized_upper(_("Page")),
            )
        )
        self._render_entries(
            [
                (account, _("Inactive"), HEALTH_SIGNAL_RED)
                for account in page_accounts
            ]
        )
        self["progress_text"].setText(
            "{}: {}  •  100%".format(_("Accounts"), count)
        )
        self["current_account"].setText("")

    def _arm_inactive_confirmation(self):
        if (
            self._closed
            or self._state != "confirming"
            or not self._inactive_accounts
            or self._confirmation_armed
            or self._confirmation_open
            or self._confirmation_attempts
            >= ACCOUNT_BATCH_CONFIRM_MAX_ATTEMPTS
        ):
            return False
        if not callable(getattr(self.session, "openWithCallback", None)):
            self._show_inline_review()
            return False
        self._confirmation_armed = True
        self._confirmation_timer.start(
            ACCOUNT_BATCH_CONFIRM_DELAY_MS,
            True,
        )
        return True

    def _open_inactive_confirmation(self):
        self._confirmation_armed = False
        try:
            self._confirmation_timer.stop()
        except Exception:
            pass
        if (
            self._closed
            or self._state != "confirming"
            or not self._inactive_accounts
            or self._confirmation_open
            or self._confirmation_attempts
            >= ACCOUNT_BATCH_CONFIRM_MAX_ATTEMPTS
        ):
            return False
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self._show_inline_review()
            return False
        self._confirmation_attempts += 1
        self._confirmation_open = True
        question = "{}: {}\n\n{}".format(
            localized_upper(_("Inactive")),
            len(self._inactive_accounts),
            _("Delete {}?").format(localized_upper(_("Accounts"))),
        )
        try:
            opener(
                self._inactive_confirmation_closed,
                MessageBox,
                question,
                type=MessageBox.TYPE_YESNO,
                default=False,
            )
        except RuntimeError as error:
            self._confirmation_open = False
            if not _is_modal_open_error(error):
                raise
            if self._arm_inactive_confirmation():
                return True
            self._show_inline_review()
            return False
        return True

    def _inactive_confirmation_closed(self, confirmed):
        self._confirmation_open = False
        if self._closed or self._state != "confirming":
            return
        if bool(confirmed):
            self._begin_delete_inactive()
            return
        self.close(self._result_payload())

    def _show_inline_review(self):
        if self._closed or not self._inactive_accounts:
            return
        self._confirmation_armed = False
        self._confirmation_open = False
        self._state = "review"
        set_remote_footer(self, ACCOUNT_BATCH_REVIEW_FOOTER_ITEMS)
        self._refresh_review()

    def previous_page(self):
        if self._state == "review" and self._review_page > 0:
            self._review_page -= 1
            self._refresh_review()

    def next_page(self):
        if self._state != "review":
            return
        last_page = max(
            0,
            int((len(self._inactive_accounts) - 1) / self.page_size),
        )
        if self._review_page < last_page:
            self._review_page += 1
            self._refresh_review()

    def delete_inactive(self):
        if self._state != "review" or not self._inactive_accounts:
            return
        self._begin_delete_inactive()

    def _begin_delete_inactive(self):
        if (
            self._state not in ("review", "confirming")
            or not self._inactive_accounts
        ):
            return
        self._state = "deleting"
        self._close_after_delete_cancel = False
        self._cancel_event.clear()
        self["header"].setText(localized_upper(_("Delete")))
        self["subtitle"].setText(_("Please wait"))
        self["review_page"].setText("")
        set_remote_footer(self, ACCOUNT_BATCH_RUNNING_FOOTER_ITEMS)
        worker = threading.Thread(target=self._run_delete)
        worker.daemon = True
        worker.start()
        self._timer.start(ACCOUNT_BATCH_QUERY_POLL_MS, True)

    def _run_delete(self):
        try:
            deadline = time.monotonic() + ACCOUNT_BATCH_DELETE_TIMEOUT_SECONDS
            if self._batch_deleter_supports_limits:
                removed = self.batch_deleter(
                    self._inactive_accounts,
                    cancel_event=self._cancel_event,
                    deadline=deadline,
                )
            else:
                removed = self.batch_deleter(self._inactive_accounts)
            error = ""
        except Exception as caught:
            removed = 0
            error = str(caught)
        self._queue_event("delete_done", removed, error)

    def _finish_delete(self, removed, error):
        if self._closed or self._state != "deleting":
            return
        if error:
            if self._close_after_delete_cancel:
                self.close(self._result_payload(cancelled=True))
                return
            self._state = "review"
            set_remote_footer(self, ACCOUNT_BATCH_REVIEW_FOOTER_ITEMS)
            self._refresh_review()
            ellipsize_dynamic_text(
                self["current_account"],
                _("Could not delete the source: {}").format(
                    clean_dynamic_text(_localized_runtime_text(error))
                ),
                fallback_chars=72,
            )
            return
        self.close(self._result_payload(deleted=removed))

    def _result_payload(self, deleted=0, cancelled=False):
        return {
            "results": dict(self._results),
            "inactive_count": len(self._inactive_accounts),
            "deleted": max(0, int(deleted or 0)),
            "cancelled": bool(cancelled),
        }

    def keep_inactive(self):
        if self._state == "review":
            self.close(self._result_payload())

    def continue_or_keep(self):
        if self._state == "review":
            self.keep_inactive()

    def cancel_or_keep(self):
        if self._state == "deleting":
            self._close_after_delete_cancel = True
            self._cancel_event.set()
            self["subtitle"].setText(_("Please wait"))
            return
        if self._state == "review":
            self.keep_inactive()
            return
        self._cancel_event.set()
        self.close(self._result_payload(cancelled=True))

    def _stop(self):
        self._closed = True
        self._cancel_event.set()
        self._confirmation_armed = False
        self._confirmation_open = False
        try:
            self._confirmation_timer.stop()
        except Exception:
            pass
        try:
            self._timer.stop()
        except Exception:
            pass


class GTAccountsScreen(Screen):
    page_size = 10

    def __init__(
        self,
        session,
        account_loader=None,
        account_deleter=None,
        account_checker=None,
        account_batch_deleter=None,
        source_type="",
    ):
        source_type = str(source_type or "").lower()
        footer_items = (
            M3U_ACCOUNTS_FOOTER_ITEMS
            if source_type == "m3u"
            else ACCOUNTS_FOOTER_ITEMS
        )
        self.skin = decorate_remote_footer(
            _accounts_skin(),
            footer_items,
            mask_legacy_frame=True,
        )
        Screen.__init__(self, session)
        self.source_type = source_type
        self.account_loader = account_loader or (
            (lambda: _load_local_source_type(self.source_type))
            if self.source_type in SOURCE_TYPES
            else load_sources
        )
        self.account_deleter = account_deleter or delete_source
        self._account_deleter_supports_limits = (
            account_deleter is None or account_deleter is delete_source
        )
        self.account_checker = account_checker or check_source_health
        if account_batch_deleter is not None:
            self.account_batch_deleter = account_batch_deleter
        elif account_deleter is None or account_deleter is delete_source:
            self.account_batch_deleter = delete_sources
        else:
            self.account_batch_deleter = self._delete_accounts_one_by_one
        self.load_result = None
        self.selected_index = 0
        self._health_results = {}
        self._automatic_test_started = False
        self._automatic_test_open = False
        self._automatic_test_armed = False
        self._automatic_test_open_attempts = 0
        self._account_screen_closed = False
        self._pending_delete = None
        self._direct_connector = None
        self._delete_job = None
        self._delete_account = None
        self._delete_result = None
        self._delete_result_ready = False
        self._delete_closed = False
        self._delete_state_lock = threading.Lock()
        self._automatic_test_timer = eTimer()
        _connect_timer(
            self._automatic_test_timer,
            self._start_automatic_test,
        )
        self._delete_timer = eTimer()
        _connect_timer(self._delete_timer, self._poll_delete_result)
        attach_background(self, "accounts_bg", ACCOUNTS_BACKGROUND)
        self["badge_mask"] = Label("")
        for panel in ("account_panel", "account_detail_panel"):
            self["{}_bg".format(panel)] = Label("")
            for edge in ("top", "bottom", "left", "right"):
                self["{}_{}".format(panel, edge)] = Label("")
        for frame in ("count_frame", "page_frame"):
            for edge in ("top", "bottom", "left", "right"):
                self["{}_{}".format(frame, edge)] = Label("")
        for index in range(self.page_size - 1):
            self["row_separator_{}".format(index)] = Label("")
        self["header"] = Label("")
        self["summary"] = Label("")
        self["accounts"] = Label("")
        self["account_scroll_track"] = Label("")
        self["account_scroll_thumb"] = Label("")
        hide_scrollbar(self, "account_scroll")
        self["message"] = Label("")
        self["count_badge"] = Label("")
        self["source_badge"] = Label("")
        self["selected_caption"] = Label(_("SELECTED SOURCE"))
        self["selected_account"] = Label("")
        self["selected_status"] = Label("")
        for suffix in ("_green", "_yellow", "_red"):
            self["selected_status{}".format(suffix)] = Label("")
        for prefix in ("selected_expiry", "selected_connection"):
            self[prefix] = Label("")
            for suffix in ("_green", "_yellow", "_red"):
                self["{}{}".format(prefix, suffix)] = Label("")
        self["detail_action"] = Label(
            "OK  •  {}".format(localized_upper(_("Connect")))
        )
        for edge in ("top", "bottom", "left", "right"):
            self["detail_{}".format(edge)] = Label("")
        for index in range(self.page_size):
            self["row_number_{}".format(index)] = Label("")
            self["row_{}".format(index)] = Label("")
            self["row_detail_{}".format(index)] = Label("")
            for suffix in ("_green", "_yellow", "_red"):
                self["row_detail_{}{}".format(index, suffix)] = Label("")
            for edge in ("top", "bottom", "left", "right"):
                self["focus_{}_{}".format(index, edge)] = Label("")
        self["footer"] = Label("")
        install_remote_footer(self, footer_items)
        actions = {
            "ok": self.connect_selected,
            "cancel": self.close,
            "back": self.close,
            "up": self.move_up,
            "down": self.move_down,
            "upRepeated": self.move_up,
            "downRepeated": self.move_down,
            "left": self.previous_page,
            "right": self.next_page,
            "leftRepeated": self.previous_page,
            "rightRepeated": self.next_page,
            "red": self.delete_selected,
            "green": self.add_new,
            "yellow": self.test_selected,
        }
        if self.source_type == "m3u":
            actions["blue"] = self.open_m3u_epg_settings
        self["actions"] = ActionMap(
            [
                "OkCancelActions",
                "DirectionActions",
                "ColorActions",
                "WizardActions",
            ],
            actions,
            -1,
        )
        # Physical CH keys are bound in one context only.  Keeping them out
        # of DirectionActions prevents images that alias page/channel actions
        # from dispatching the same press twice.
        self["channel_actions"] = ActionMap(
            ["MoviePlayerActions"],
            {
                "channelUp": self.previous_page,
                "channelDown": self.next_page,
            },
            -1,
        )
        self.reload_accounts()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refresh)
        if hasattr(self, "onShown"):
            self.onShown.append(self._arm_automatic_test)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop_automatic_test_timer)
            self.onClose.append(self._stop_direct_connection)
            self.onClose.append(self._stop_delete_job)
        self.setTitle(self._heading())

    def _heading(self):
        return {
            "xtream": _("Xtream Codes Accounts"),
            "m3u": _("M3U Sources"),
            "stalker": _("Stalker / MAC Portal Accounts"),
        }.get(self.source_type, _("Sources"))

    @property
    def accounts(self):
        if self.load_result is None:
            return []
        return self.load_result.accounts

    def reload_accounts(self):
        try:
            self.load_result = self.account_loader()
        except Exception as error:
            self.load_result = PlaylistLoadResult([], [], str(error))
        if self.selected_index >= len(self.accounts):
            self.selected_index = max(0, len(self.accounts) - 1)
        self._refresh()

    def _delete_accounts_one_by_one(self, accounts):
        """Compatibility path for injected legacy single-account deleters."""
        removed = 0
        for account in tuple(accounts or ()):
            removed += int(self.account_deleter(account) or 0)
        return removed

    def _arm_automatic_test(self):
        """Defer the child dialog until this account screen is fully modal."""
        if (
            self._account_screen_closed
            or self._automatic_test_started
            or self._automatic_test_open
            or self._automatic_test_armed
            or self._automatic_test_open_attempts
            >= ACCOUNT_BATCH_OPEN_MAX_ATTEMPTS
            or self.source_type not in ("xtream", "stalker")
            or not self.accounts
        ):
            return False
        self._automatic_test_armed = True
        self._automatic_test_timer.start(
            ACCOUNT_BATCH_OPEN_DELAY_MS,
            True,
        )
        return True

    def _start_automatic_test(self):
        self._automatic_test_armed = False
        try:
            self._automatic_test_timer.stop()
        except Exception:
            pass
        if (
            self._account_screen_closed
            or self._automatic_test_started
            or self._automatic_test_open
            or self._automatic_test_open_attempts
            >= ACCOUNT_BATCH_OPEN_MAX_ATTEMPTS
            or self.source_type not in ("xtream", "stalker")
            or not self.accounts
        ):
            return False
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            return False
        self._automatic_test_open_attempts += 1
        self._automatic_test_started = True
        self._automatic_test_open = True
        try:
            opener(
                self._automatic_test_closed,
                GTAccountBatchTestScreen,
                tuple(self.accounts),
                self.account_checker,
                self.account_batch_deleter,
            )
        except RuntimeError as error:
            self._automatic_test_started = False
            self._automatic_test_open = False
            if not _is_modal_open_error(error):
                raise
            # Some Enigma2 images emit onShown immediately before the parent
            # dialog becomes modal.  Stay on the event loop and retry a small,
            # bounded number of times instead of crashing the GUI.
            return self._arm_automatic_test()
        return True

    def _automatic_test_closed(self, payload=None):
        self._automatic_test_open = False
        if self._account_screen_closed:
            return
        if not isinstance(payload, dict):
            self._refresh()
            return
        results = payload.get("results")
        if isinstance(results, dict):
            self._health_results.update(results)
        try:
            deleted = max(0, int(payload.get("deleted", 0) or 0))
        except (TypeError, ValueError, OverflowError):
            deleted = 0
        try:
            inactive_count = max(
                0,
                int(payload.get("inactive_count", 0) or 0),
            )
        except (TypeError, ValueError, OverflowError):
            inactive_count = 0
        if deleted:
            self.reload_accounts()
            ellipsize_dynamic_text(
                self["message"],
                _("{} was deleted ({} records).").format(
                    localized_upper(_("Accounts")),
                    deleted,
                ),
                fallback_chars=32,
            )
            return
        self._refresh()
        if bool(payload.get("cancelled")):
            self["message"].setText(_("Request cancelled"))
        elif inactive_count:
            self["message"].setText(
                "{}: {}".format(_("Inactive"), inactive_count)
            )

    def _stop_automatic_test_timer(self):
        self._account_screen_closed = True
        self._automatic_test_armed = False
        try:
            self._automatic_test_timer.stop()
        except Exception:
            pass

    def _refresh(self):
        heading = self._heading()
        self["header"].setText(heading)
        self.setTitle(heading)
        count = len(self.accounts)
        page_count = int((count + self.page_size - 1) / self.page_size) if count else 0
        current_page = int(self.selected_index / self.page_size) + 1 if count else 0
        source_limit = SOURCE_LIMITS.get(self.source_type, MAX_ACCOUNTS)
        count_label = _("Sources") if self.source_type == "m3u" else _("Accounts")
        badge_label = _("Source") if self.source_type == "m3u" else _("Account")
        self["count_badge"].setText(
            "{} / {} {}".format(
                count, source_limit, localized_upper(badge_label)
            )
        )
        self["source_badge"].setText(
            "{} / {} {}".format(
                current_page, page_count, localized_upper(_("Page"))
            )
        )
        self["summary"].setText(
            "{}: {} / {}  |  {}: {} / {}".format(
                count_label, count, source_limit,
                _("Page"), current_page, page_count,
            )
        )
        if not count:
            empty_text = _(
                "No source was found.\n\n"
                "Create one from the ADD SOURCE panel on the home screen."
            )
            self["accounts"].setText(empty_text)
            for offset in range(self.page_size):
                self["row_number_{}".format(offset)].setText("")
                self["row_{}".format(offset)].setText("")
                _set_signal_label(
                    self,
                    "row_detail_{}".format(offset),
                    "",
                    HEALTH_SIGNAL_NEUTRAL,
                    fallback_chars=68,
                )
                for edge in ("top", "bottom", "left", "right"):
                    self["focus_{}_{}".format(offset, edge)].hide()
            self["selected_account"].setText(
                "{}: 0".format(count_label)
            )
            _set_signal_label(
                self,
                "selected_status",
                localized_upper(_("Add Source")),
                HEALTH_SIGNAL_NEUTRAL,
                fallback_chars=25,
            )
            _set_signal_label(
                self,
                "selected_expiry",
                "",
                HEALTH_SIGNAL_NEUTRAL,
                fallback_chars=30,
            )
            _set_signal_label(
                self,
                "selected_connection",
                "",
                HEALTH_SIGNAL_NEUTRAL,
                fallback_chars=30,
            )
            self["detail_action"].setText(
                localized_upper(_("Add Source"))
            )
            ellipsize_dynamic_text(
                self["message"],
                clean_dynamic_text(_(self.load_result.error)),
                fallback_chars=32,
            )
            hide_scrollbar(self, "account_scroll")
            return
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_accounts = self.accounts[page_start : page_start + self.page_size]
        lines = []
        for offset, account in enumerate(page_accounts):
            index = page_start + offset
            primary, secondary = _source_list_labels(account)
            result = self._health_results.get(_source_result_key(account))
            if result is not None:
                secondary = _account_test_row_detail(account, result)
                unused_status, row_signal = _account_test_status_text(result)
                del unused_status
            else:
                row_signal = HEALTH_SIGNAL_NEUTRAL
            marker = ">" if index == self.selected_index else " "
            self["row_number_{}".format(offset)].setText(
                "{:02d}.".format(index + 1)
            )
            rendered_primary = ellipsize_dynamic_text(
                self["row_{}".format(offset)],
                primary,
                fallback_chars=50,
            )
            rendered_secondary = _set_signal_label(
                self,
                "row_detail_{}".format(offset),
                secondary,
                row_signal,
                fallback_chars=68,
            )
            # This legacy compatibility label is visually hidden, but it must
            # not receive unbounded provider values either.
            lines.append(
                "{} {:02d}. {}  |  {}".format(
                    marker,
                    index + 1,
                    rendered_primary,
                    rendered_secondary,
                )
            )
            for edge in ("top", "bottom", "left", "right"):
                focus = self["focus_{}_{}".format(offset, edge)]
                if index == self.selected_index:
                    focus.show()
                else:
                    focus.hide()
        for offset in range(len(page_accounts), self.page_size):
            self["row_number_{}".format(offset)].setText("")
            self["row_{}".format(offset)].setText("")
            _set_signal_label(
                self,
                "row_detail_{}".format(offset),
                "",
                HEALTH_SIGNAL_NEUTRAL,
                fallback_chars=68,
            )
            for edge in ("top", "bottom", "left", "right"):
                self["focus_{}_{}".format(offset, edge)].hide()
        self["accounts"].setText("\n\n".join(lines))
        selected = self.accounts[self.selected_index]
        selected_primary, selected_secondary = _source_list_labels(selected)
        ellipsize_dynamic_text(
            self["selected_account"],
            selected_primary,
            fallback_chars=22,
        )
        result = self._health_results.get(_source_result_key(selected))
        if result is None:
            _set_signal_label(
                self,
                "selected_status",
                "{}\n{}".format(
                    localized_upper(_("Ready to connect")),
                    _source_badge(selected),
                ),
                HEALTH_SIGNAL_NEUTRAL,
                fallback_chars=25,
            )
            if _source_type(selected) == "m3u":
                expiry_line = ""
                connection_line = ""
            else:
                expiry_line = _("Expiry: {}").format(_("Unknown"))
                connection_line = localized_connection_usage_line(_("Unknown"))
            _set_signal_label(
                self,
                "selected_expiry",
                expiry_line,
                HEALTH_SIGNAL_NEUTRAL,
                fallback_chars=30,
            )
            _set_signal_label(
                self,
                "selected_connection",
                connection_line,
                HEALTH_SIGNAL_NEUTRAL,
                fallback_chars=30,
            )
        else:
            status_text, result_signal = _account_test_status_text(result)
            _set_signal_label(
                self,
                "selected_status",
                "{}\n{}".format(
                    localized_upper(_("Status")),
                    localized_upper(status_text),
                ),
                result_signal,
                fallback_chars=25,
            )
            expiry_text = _account_test_expiry_text(result)
            expiry_signal = account_expiry_signal(
                getattr(result, "expiry", ""),
                getattr(result, "account_status", ""),
            )
            _set_signal_label(
                self,
                "selected_expiry",
                _("Expiry: {}").format(expiry_text),
                expiry_signal,
                fallback_chars=30,
            )
            _set_signal_label(
                self,
                "selected_connection",
                localized_connection_usage_line(
                    _account_test_connection_text(result)
                ),
                result_signal,
                fallback_chars=30,
            )
        self["detail_action"].setText(
            "OK  •  {}".format(localized_upper(_("Connect")))
        )
        ellipsize_dynamic_text(
            self["message"],
            selected_secondary,
            fallback_chars=32,
        )
        width, height, px = _scale()
        update_scrollbar(
            self,
            "account_scroll",
            count,
            self.selected_index,
            self.page_size,
            (px(1320), px(184), px(8), px(668)),
        )

    def _select(self, index):
        if not self.accounts:
            return
        self.selected_index = max(0, min(int(index), len(self.accounts) - 1))
        self._refresh()

    def move_up(self):
        self._select(self.selected_index - 1)

    def move_down(self):
        self._select(self.selected_index + 1)

    def _move_page(self, delta):
        if not self.accounts:
            return
        current_start = int(self.selected_index / self.page_size) * self.page_size
        target_start = current_start + (int(delta) * self.page_size)
        last_start = int((len(self.accounts) - 1) / self.page_size) * self.page_size
        target_start = max(0, min(target_start, last_start))
        if target_start == current_start:
            return
        row = self.selected_index - current_start
        target_count = min(self.page_size, len(self.accounts) - target_start)
        self.selected_index = target_start + min(row, target_count - 1)
        self._refresh()

    def previous_page(self):
        self._move_page(-1)

    def next_page(self):
        self._move_page(1)

    def add_new(self):
        screen = {
            "xtream": GTAddAccountScreen,
            "m3u": GTAddM3USourceScreen,
            "stalker": GTAddStalkerSourceScreen,
        }.get(self.source_type)
        if screen is None:
            self["message"].setText(_("Choose a source type first."))
            return
        if hasattr(self.session, "openWithCallback"):
            self.session.openWithCallback(
                lambda *unused: self.reload_accounts(),
                screen,
            )
        else:
            self.session.open(screen)

    def delete_selected(self):
        if self._delete_job is not None:
            self["message"].setText(_("Please wait"))
            return
        if not self.accounts:
            self["message"].setText(_("No source is selected for deletion."))
            return
        self._pending_delete = self.accounts[self.selected_index]
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(_("Could not open the deletion confirmation."))
            self._pending_delete = None
            return
        self.session.openWithCallback(
            self._delete_confirmed,
            MessageBox,
            _("Delete {}?").format(
                    self._pending_delete.display_name
                ),
            type=MessageBox.TYPE_YESNO,
            default=False,
        )

    def _delete_confirmed(self, confirmed):
        account = self._pending_delete
        self._pending_delete = None
        if not confirmed or account is None:
            self["message"].setText(_("Source deletion cancelled."))
            return
        job = _UIJobToken(UI_SOURCE_DELETE_TIMEOUT_SECONDS)
        with self._delete_state_lock:
            if self._delete_closed or self._delete_job is not None:
                job.cancel()
                self["message"].setText(_("Please wait"))
                return
            self._delete_job = job
            self._delete_account = account
            self._delete_result = None
            self._delete_result_ready = False
        self["message"].setText(_("Please wait"))
        worker = threading.Thread(
            target=lambda: self._run_delete(account, job),
        )
        worker.daemon = True
        worker.start()
        self._delete_timer.start(100, True)

    def _run_delete(self, account, job):
        try:
            job.check()
            if self._account_deleter_supports_limits:
                removed = self.account_deleter(
                    account,
                    cancel_event=job.cancel_event,
                    deadline=job.deadline,
                )
            else:
                removed = self.account_deleter(account)
            result = (True, removed, "")
        except Exception as error:
            result = (False, 0, str(error))
        with self._delete_state_lock:
            if self._delete_closed or self._delete_job is not job:
                return
            self._delete_result = result
            self._delete_result_ready = True

    def _poll_delete_result(self):
        with self._delete_state_lock:
            job = self._delete_job
            ready = self._delete_result_ready
            result = self._delete_result
            account = self._delete_account
        if job is None or self._delete_closed:
            return
        if not ready:
            if job.expired():
                job.cancel()
                # The deleter receives this job's cancellation event and
                # deadline.  Keep polling for its authoritative result: an
                # atomic account-file replace may have committed just before
                # the deadline, and reporting failure here would disagree
                # with the data already stored on disk.
            self._delete_timer.start(250, True)
            return

        with self._delete_state_lock:
            if self._delete_job is not job:
                return
            self._delete_job = None
            self._delete_account = None
            self._delete_result = None
            self._delete_result_ready = False
        succeeded, removed, detail = result
        if not succeeded:
            ellipsize_dynamic_text(
                self["message"],
                _("Could not delete the source: {}").format(
                    clean_dynamic_text(_localized_runtime_text(detail))
                ),
                fallback_chars=32,
            )
            return
        if str(
            getattr(account, "source_type", "xtream") or "xtream"
        ).lower() == "m3u":
            # Source deletion runs on a worker, but Enigma2 timers are GUI
            # thread owned.  Reload the XMLTV scheduler only after the worker's
            # authoritative result reaches this polling callback.
            try:
                from .m3u_epg_scheduler import notify_settings_changed

                notify_settings_changed()
            except Exception:
                pass
        self.reload_accounts()
        ellipsize_dynamic_text(
            self["message"],
            _("{} was deleted ({} records).").format(
                clean_dynamic_text(account.display_name),
                removed,
            ),
            fallback_chars=32,
        )

    def _stop_delete_job(self):
        with self._delete_state_lock:
            self._delete_closed = True
            job = self._delete_job
            self._delete_job = None
            self._delete_account = None
            self._delete_result = None
            self._delete_result_ready = False
        _cancel_ui_job(job)
        try:
            self._delete_timer.stop()
        except Exception:
            pass

    def open_selected(self):
        """Compatibility alias: OK now follows the direct-connect path."""
        return self.connect_selected()

    def _selected_action_account(self):
        if (
            self._account_screen_closed
            or self._automatic_test_open
            or self._automatic_test_armed
        ):
            return None
        if not self.accounts or not 0 <= self.selected_index < len(self.accounts):
            return None
        if self._delete_job is not None:
            self["message"].setText(_("Please wait"))
            return None
        if self._direct_connector is not None and self._direct_connector.active:
            self["message"].setText(_("A source connection is already in progress."))
            return None
        account = self.accounts[self.selected_index]
        return account if _source_type(account) in SOURCE_TYPES else None

    def _m3u_action_account(self):
        if self.source_type != "m3u":
            return None
        account = self._selected_action_account()
        if account is None:
            return None
        return account if _source_type(account) == "m3u" else None

    def test_selected(self):
        account = self._selected_action_account()
        if account is not None:
            self.session.open(
                GTServerHealthScreen,
                account,
                checker=self.account_checker,
            )

    def open_m3u_epg_settings(self):
        account = self._m3u_action_account()
        if account is None:
            return
        try:
            from .m3u_epg_ui import GTM3UXMLTVSettingsScreen
        except Exception:
            self["message"].setText(_("XMLTV EPG settings could not be opened."))
            return
        self.session.open(GTM3UXMLTVSettingsScreen, account)

    def connect_selected(self):
        if not self.accounts:
            self["message"].setText(_("No source is available to connect."))
            return
        if self._direct_connector is not None and self._direct_connector.active:
            self["message"].setText(_("A source connection is already in progress."))
            return
        account = self.accounts[self.selected_index]
        self._start_connection(account)

    def _start_connection(self, account):
        self._stop_direct_connection()
        self._direct_connector = _DirectAccountConnection(
            self.session,
            account,
            checker=self.account_checker,
            on_started=self._connection_started,
            on_success=self._connection_succeeded,
            on_failure=self._connection_failed,
            on_return=self._connection_returned,
        )
        self._direct_connector.start()

    def _connection_returned(self, *args):
        if args and args[0] == PLAYLIST_FILES_CONNECTED:
            self.close(PLAYLIST_FILES_CONNECTED)
            return
        self._refresh()

    def _connection_started(self, account):
        _set_signal_label(
            self,
            "selected_status",
            _("CONNECTING..."),
            HEALTH_SIGNAL_NEUTRAL,
            fallback_chars=25,
        )
        ellipsize_dynamic_text(
            self["detail_action"],
            "{}  •  {}".format(
                _source_badge(account),
                localized_upper(_("Please wait")),
            ),
            fallback_chars=29,
        )
        ellipsize_dynamic_text(
            self["message"],
            _("Connecting to {}, please wait.").format(
                account.display_name
            ),
            fallback_chars=30,
        )

    def _connection_succeeded(self, account, result):
        del result
        _set_signal_label(
            self,
            "selected_status",
            _("CONNECTED"),
            HEALTH_SIGNAL_GREEN,
            fallback_chars=25,
        )
        self["detail_action"].setText(_("OPENING MAIN PANEL"))
        ellipsize_dynamic_text(
            self["message"],
            _("Connected to {}.").format(account.display_name),
            fallback_chars=30,
        )

    def _connection_failed(self, account, result):
        _set_signal_label(
            self,
            "selected_status",
            _localized_runtime_text(result.status),
            health_signal(
                getattr(result, "status", ""),
                getattr(result, "account_status", ""),
            ),
            fallback_chars=25,
        )
        self["detail_action"].setText(
            "OK  •  {}".format(localized_upper(_("Retry")))
        )
        ellipsize_dynamic_text(
            self["message"],
            _("Could not connect to {}: {}").format(
                account.display_name,
                _localized_runtime_text(result.detail or result.status),
            ),
            fallback_chars=30,
        )

    def _stop_direct_connection(self):
        if self._direct_connector is not None:
            self._direct_connector.stop()


class GTManageSourcesScreen(Screen):
    """Local-only source type chooser used by Manage Sources."""

    FOCUS_EDGES = ("top", "bottom", "left", "right")

    def __init__(self, session):
        self.skin = decorate_remote_footer(
            _manage_sources_skin(),
            MANAGE_SOURCES_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND)
        self.selected_index = 0
        self.results = [PlaylistLoadResult(), PlaylistLoadResult(), PlaylistLoadResult()]
        self["panel"] = Label("")
        self["header"] = Label(localized_upper(_("Manage Sources")))
        self["step"] = Label("")
        self["instruction"] = Label(
            _("Choose the source type you want to manage.")
        )
        for index in range(3):
            self["card_{}".format(index)] = Label("")
            self["count_{}".format(index)] = Label("")
            for edge in self.FOCUS_EDGES:
                self["focus_{}_{}".format(index, edge)] = Label("")
        self["icon_0"] = Label("XC")
        self["icon_1"] = Label("M3U")
        self["icon_2"] = Label("MAC")
        self["title_0"] = Label(localized_upper(_("Xtream Codes Accounts")))
        self["title_1"] = Label(localized_upper(_("M3U Sources")))
        self["title_2"] = Label(localized_upper(_("Stalker / MAC Portal Accounts")))
        self["description_0"] = Label(_("Server URL, username and password"))
        self["description_1"] = Label(_("Local file, NAS or web link"))
        self["description_2"] = Label("{} URL + MAC".format(_("Portal")))
        self["capabilities_0"] = Label(
            "{} | {} | {}".format(
                localized_upper(_("Live TV")),
                localized_upper(_("Movies")),
                localized_upper(_("Series")),
            )
        )
        self["capabilities_1"] = Label(localized_upper(_("Live TV")))
        self["capabilities_2"] = Label(
            "{} | {} | {}".format(
                localized_upper(_("Live TV")),
                localized_upper(_("Movies")),
                localized_upper(_("Series")),
            )
        )
        # This component belongs to the shared geometry; the manage screen
        # uses the dedicated count badges instead.
        self["badge_1"] = Label("")
        self["info_panel"] = Label("")
        self["info_icon"] = Label("i")
        self["message"] = Label("")
        self["footer"] = Label("")
        install_remote_footer(self, MANAGE_SOURCES_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "WizardActions"],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "back": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.move_up,
                "right": self.move_down,
                "leftRepeated": self.move_up,
                "rightRepeated": self.move_down,
            },
            -1,
        )
        self.reload_counts()
        self.setTitle(_("Manage Sources"))

    def reload_counts(self, *unused):
        del unused
        # Each backend has its own guarded load so one malformed registry can
        # never hide the other two counters.
        results = []
        for source_type in SOURCE_TYPES:
            try:
                result = _load_local_source_type(source_type)
            except Exception as error:
                result = PlaylistLoadResult([], [], str(error))
            results.append(result)
        self.results = results
        self._refresh()

    def _refresh(self):
        self["step"].setText(
            "{} / {} | {}".format(
                self.selected_index + 1,
                len(SOURCE_TYPES),
                localized_upper(_("Source type")),
            )
        )
        for index, result in enumerate(self.results):
            count = len(result.accounts)
            unit = _("Source") if index == 1 else _("Account")
            source_type = SOURCE_TYPES[index]
            self["count_{}".format(index)].setText(
                "{} / {} {}".format(
                    count, SOURCE_LIMITS[source_type], localized_upper(unit)
                )
            )
            for edge in self.FOCUS_EDGES:
                focus = self["focus_{}_{}".format(index, edge)]
                if index == self.selected_index:
                    focus.show()
                else:
                    focus.hide()
        selected = self.results[self.selected_index]
        if selected.error:
            fit_dynamic_text(
                self["message"],
                _localized_runtime_text(selected.error),
                max_lines=3,
                fallback_chars=74,
            )
        else:
            self["message"].setText(
                _("Each source type is listed separately. Each section can contain up to 500 sources.")
            )

    def move_up(self):
        self.selected_index = max(0, self.selected_index - 1)
        self._refresh()

    def move_down(self):
        self.selected_index = min(len(SOURCE_TYPES) - 1, self.selected_index + 1)
        self._refresh()

    def open_selected(self):
        source_type = SOURCE_TYPES[self.selected_index]
        if hasattr(self.session, "openWithCallback"):
            self.session.openWithCallback(
                self._accounts_closed,
                GTAccountsScreen,
                source_type=source_type,
            )
        else:
            self.session.open(GTAccountsScreen, source_type=source_type)

    def _accounts_closed(self, *args):
        if args and args[0] == PLAYLIST_FILES_CONNECTED:
            self.close(PLAYLIST_FILES_CONNECTED)
            return
        self.reload_counts()


class GTAddSourceTypeScreen(Screen):
    """Choose a source backend without changing the proven Xtream form."""

    SOURCE_TYPES = ("xtream", "m3u", "stalker")
    FOCUS_EDGES = ("top", "bottom", "left", "right")
    SOURCE_MESSAGES = (
        N_("Xtream Codes provides Live TV, Movies and Series."),
        N_(
            "M3U sources provide Live TV only. Categories, search and "
            "favorites are supported."
        ),
        N_(
            "The Stalker / MAC portal will be checked automatically when "
            "this screen opens."
        ),
    )

    def __init__(self, session):
        self.skin = decorate_remote_footer(
            _add_source_type_skin(),
            ADD_SOURCE_TYPE_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND)
        self.selected_index = 0
        self["panel"] = Label("")
        self["header"] = Label(localized_upper(_("Add Source")))
        self["step"] = Label("")
        self["instruction"] = Label(
            _("Choose the source type to add.")
        )
        for index in range(len(self.SOURCE_TYPES)):
            self["card_{}".format(index)] = Label("")
            for edge in self.FOCUS_EDGES:
                self["focus_{}_{}".format(index, edge)] = Label("")
        self["icon_0"] = Label("XC")
        self["icon_1"] = Label("M3U")
        self["icon_2"] = Label("MAC")
        self["title_0"] = Label("XTREAM CODES")
        self["title_1"] = Label(
            "M3U {}".format(localized_upper(_("Live TV")))
        )
        self["title_2"] = Label("STALKER / MAC PORTAL")
        self["description_0"] = Label(
            _("Server URL, username and password")
        )
        self["description_1"] = Label(
            _("Local file, NAS or web link")
        )
        self["description_2"] = Label(
            "{} URL + MAC".format(_("Portal"))
        )
        self["capabilities_0"] = Label(
            "{} | {} | {}".format(
                localized_upper(_("Live TV")),
                localized_upper(_("Movies")),
                localized_upper(_("Series")),
            )
        )
        self["capabilities_1"] = Label(
            "{} | NAS | HTTP(S)".format(
                localized_upper(_("Local file"))
            )
        )
        self["capabilities_2"] = Label(
            "{} | {} | {}".format(
                localized_upper(_("Live TV")),
                localized_upper(_("Movies")),
                localized_upper(_("Series")),
            )
        )
        self["badge_1"] = Label(localized_upper(_("Live TV")))
        self["info_panel"] = Label("")
        self["info_icon"] = Label("i")
        self["message"] = Label("")
        self["footer"] = Label("")
        install_remote_footer(self, ADD_SOURCE_TYPE_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.move_up,
                "right": self.move_down,
                "leftRepeated": self.move_up,
                "rightRepeated": self.move_down,
                "red": self.close,
            },
            -1,
        )
        self._refresh()
        self.setTitle(_("Add Source"))

    def _refresh(self):
        self["step"].setText(
            "{} / {} | {}".format(
                self.selected_index + 1,
                len(self.SOURCE_TYPES),
                localized_upper(_("Source type")),
            )
        )
        self["message"].setText(_(self.SOURCE_MESSAGES[self.selected_index]))
        for index in range(len(self.SOURCE_TYPES)):
            selected = index == self.selected_index
            for edge in self.FOCUS_EDGES:
                focus = self["focus_{}_{}".format(index, edge)]
                if selected:
                    focus.show()
                else:
                    focus.hide()

    def move_up(self):
        self.selected_index = (
            self.selected_index - 1
        ) % len(self.SOURCE_TYPES)
        self._refresh()

    def move_down(self):
        self.selected_index = (
            self.selected_index + 1
        ) % len(self.SOURCE_TYPES)
        self._refresh()

    def open_selected(self):
        self.close(self.SOURCE_TYPES[self.selected_index])


class GTAddStalkerSourceScreen(Screen):
    """Remote-friendly form for one Stalker/MAC portal account."""

    FIELD_COUNT = 2
    FIELD_TITLES = (
        N_("Enter a valid portal URL"),
        N_("Enter a valid MAC address"),
    )

    def __init__(self, session, portal_saver=None):
        self.skin = decorate_remote_footer(
            _add_stalker_source_skin(),
            ADD_STALKER_SOURCE_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND)
        self.portal_saver = portal_saver
        self.selected_field = 0
        self._editing_field = 0
        self._field_values = ["http://", "00:1A:79:"]
        self["top_accent"] = Label("")
        self["panel"] = Label("")
        self["header"] = Label(
            "{}  •  STALKER / MAC".format(localized_upper(_("Add Source")))
        )
        self["step"] = Label(
            "2 / 2 | {}".format(localized_upper(_("Source details")))
        )
        self["instruction"] = Label(
            "OK  •  {}     |     {}  •  {}".format(
                localized_upper(_("Portal URL / MAC")),
                localized_upper(_("GREEN")),
                localized_upper(_("Save"))
            )
        )
        self["portal_field"] = Label("")
        self["mac_field"] = Label("")
        for index in range(self.FIELD_COUNT):
            self["focus_{}".format(index)] = Label("")
        self["info_panel"] = Label("")
        self["info_icon"] = Label("i")
        self["message"] = Label(
            _(
                "The Stalker / MAC portal will be checked automatically "
                "when this screen opens."
            )
        )
        self["footer"] = Label("")
        install_remote_footer(self, ADD_STALKER_SOURCE_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.edit_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.move_up,
                "right": self.move_down,
                "leftRepeated": self.move_up,
                "rightRepeated": self.move_down,
                "green": self.save,
                "red": self.close,
            },
            -1,
        )
        self._refresh()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refresh)
        self.setTitle("{} • STALKER / MAC".format(_("Add Source")))

    def _refresh(self):
        portal_url = (
            clean_dynamic_text(self._field_values[0])
            if self._field_values[0]
            else "http://"
        )
        mac = (
            clean_dynamic_text(self._field_values[1])
            if self._field_values[1]
            else "00:1A:79:"
        )
        _ellipsize_inset_text(
            self["portal_field"],
            "  {:<16}{}".format(
                localized_upper(_("Portal URL")),
                portal_url,
            ),
            fallback_chars=54,
        )
        _ellipsize_inset_text(
            self["mac_field"],
            "  {:<16}{}".format(
                localized_upper(_("MAC address")),
                mac,
            ),
            fallback_chars=54,
        )
        for index in range(self.FIELD_COUNT):
            focus = self["focus_{}".format(index)]
            if index == self.selected_field:
                focus.show()
            else:
                focus.hide()

    def move_up(self):
        self.selected_field = (
            self.selected_field - 1
        ) % self.FIELD_COUNT
        self._refresh()

    def move_down(self):
        self.selected_field = (
            self.selected_field + 1
        ) % self.FIELD_COUNT
        self._refresh()

    def edit_selected(self):
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(
                _("The virtual keyboard is unavailable on this image.")
            )
            return
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        self._editing_field = self.selected_field
        self.session.openWithCallback(
            self._value_entered,
            VirtualKeyBoard,
            title=_(self.FIELD_TITLES[self.selected_field]),
            text=self._field_values[self.selected_field],
        )

    def _value_entered(self, value):
        if value is None:
            self["message"].setText(_("Field entry cancelled."))
            return
        entered = str(value).strip()
        if self._editing_field == 1:
            entered = entered.upper().replace("-", ":")
        self._field_values[self._editing_field] = entered
        self.selected_field = (
            self._editing_field + 1
        ) % self.FIELD_COUNT
        self["message"].setText(
            _("Information received. Edit the next field or press GREEN to save.")
        )
        self._refresh()

    def save(self):
        saver = self.portal_saver
        if saver is None:
            from .stalker import save_portal_account

            saver = save_portal_account
        try:
            saved = saver(
                self._field_values[0],
                self._field_values[1],
            )
        except (IOError, OSError, TypeError, ValueError) as error:
            fit_dynamic_text(
                self["message"],
                "STALKER / MAC  •  {}".format(
                    _localized_runtime_text(str(error))
                ),
                max_lines=4,
                fallback_chars=64,
            )
            return
        fit_dynamic_text(
            self["message"],
            "{}  •  {}".format(saved.display_name, _("Active")),
            max_lines=4,
            fallback_chars=64,
        )
        self.close(True)


def _path_within_m3u_roots(path, roots=LOCAL_M3U_ROOTS):
    """Return whether a real absolute path stays inside a runtime M3U root."""
    value = str(path or "").strip()
    if not value or not os.path.isabs(value) or "\x00" in value:
        return False
    absolute = os.path.abspath(value)
    real = os.path.realpath(absolute)
    if real != absolute:
        return False
    if isinstance(roots, (str, os.PathLike)):
        roots = (os.fspath(roots),)
    for root in roots:
        allowed_root = os.path.realpath(os.path.abspath(str(root)))
        try:
            if os.path.commonpath((real, allowed_root)) == allowed_root:
                return True
        except ValueError:
            continue
    return False


def _m3u_browser_directory(location, roots=LOCAL_M3U_ROOTS):
    """Choose a safe existing start directory for the native file list."""
    value = str(location or "").strip()
    if not value or not os.path.isabs(value):
        return None
    absolute = os.path.abspath(value)
    directory = absolute if os.path.isdir(absolute) else os.path.dirname(absolute)
    if not os.path.isdir(directory) or not _path_within_m3u_roots(directory, roots):
        return None
    return os.path.join(directory, "")


def _m3u_file_extension(path):
    return str(path or "").lower().endswith((".m3u", ".m3u8"))


def _m3u_file_list_selection(file_list):
    """Normalize old OpenPLi basenames and newer OpenATV absolute paths."""
    filename = file_list.getFilename()
    if not filename:
        return ""
    filename = str(filename)
    if os.path.isabs(filename):
        return os.path.normpath(filename)
    directory = str(file_list.getCurrentDirectory() or "")
    if not directory:
        return ""
    return os.path.normpath(os.path.join(directory, filename))


def _m3u_storage_locations(partitions=None, is_directory=None):
    """Return fixed shortcuts plus allowed mounts in stable display order."""
    is_directory = is_directory or os.path.isdir
    locations = []
    seen = set()

    def add_location(label, path):
        path = os.path.normpath(str(path or ""))
        if (
            path in seen
            or not is_directory(path)
            or not _path_within_m3u_roots(path)
        ):
            return
        seen.add(path)
        locations.append(("{}  •  {}".format(label, path), path))

    for label, path in M3U_BROWSER_LOCATIONS:
        add_location(label, path)

    if partitions is None:
        try:
            from Components.Harddisk import harddiskmanager

            partitions = harddiskmanager.getMountedPartitions()
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError):
            partitions = ()
    for partition in partitions or ():
        path = getattr(partition, "mountpoint", "")
        label = str(getattr(partition, "description", "") or _("Storage"))
        add_location(label, path)
    return locations


class GTM3UFileBrowserScreen(Screen):
    """Select one existing local playlist from device or mounted storage."""

    def __init__(self, session, current_path=""):
        self.skin = decorate_remote_footer(
            _m3u_file_browser_skin(),
            M3U_FILE_BROWSER_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND)
        self["header"] = Label(localized_upper(_("Local / NAS file")))
        self["instruction"] = Label(
            "{}  •  M3U / M3U8".format(
                _("Device, USB or mounted network storage")
            )
        )
        self["path_panel"] = Label("")
        self["path"] = Label("")
        self["message"] = Label("HDD  •  USB  •  NAS  •  /media  •  /mnt")
        start_directory = _m3u_browser_directory(current_path)
        self["filelist"] = FileList(
            start_directory,
            showDirectories=True,
            showFiles=True,
            showMountpoints=True,
            matchingPattern=M3U_FILE_PATTERN,
            useServiceRef=False,
            inhibitDirs=False,
            inhibitMounts=False,
            isTop=False,
            enableWrapAround=False,
        )
        self["footer"] = Label("")
        install_remote_footer(self, M3U_FILE_BROWSER_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.open_current,
                "cancel": self.cancel,
                "red": self.cancel,
                "green": self.select_current,
                "blue": self.show_storage,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
            },
            -1,
        )
        self._refresh_path()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refresh_path)
        self.setTitle(_("Local / NAS file"))

    def _refresh_path(self):
        current = self["filelist"].getCurrentDirectory()
        if current:
            ellipsize_dynamic_text(
                self["path"],
                current,
                fallback_chars=104,
            )
        else:
            self["path"].setText(_("Storage"))

    def _move(self, method):
        method()
        self._refresh_path()

    def move_up(self):
        self._move(self["filelist"].up)

    def move_down(self):
        self._move(self["filelist"].down)

    def page_up(self):
        self._move(self["filelist"].pageUp)

    def page_down(self):
        self._move(self["filelist"].pageDown)

    def open_current(self):
        if self["filelist"].canDescent():
            self["filelist"].descent()
            self._refresh_path()
            return
        self._select_file()

    def select_current(self):
        self.open_current()

    def show_storage(self):
        locations = _m3u_storage_locations()
        try:
            from Screens.ChoiceBox import ChoiceBox
        except ImportError:
            ChoiceBox = None
        if (
            ChoiceBox is None
            or not locations
            or not hasattr(self.session, "openWithCallback")
        ):
            self["filelist"].changeDir(None)
            self._refresh_path()
            return
        self.session.openWithCallback(
            self._storage_selected,
            ChoiceBox,
            title=_("Storage"),
            list=locations,
        )

    def _storage_selected(self, selection):
        if not selection or len(selection) < 2:
            return
        directory = _m3u_browser_directory(selection[1])
        if directory is None:
            self["message"].setText(_("M3U file path is invalid."))
            return
        self["filelist"].changeDir(directory)
        self._refresh_path()

    def _select_file(self):
        path = _m3u_file_list_selection(self["filelist"])
        if not _m3u_file_extension(path):
            self["message"].setText(_("M3U file path is invalid."))
            return
        try:
            path = validate_local_m3u_path(path)
        except ContentError as error:
            ellipsize_dynamic_text(
                self["message"],
                _localized_runtime_text(str(error)),
                fallback_chars=112,
            )
            return
        self.close(path)

    def cancel(self):
        self.close(None)


class GTPlaylistTypeScreen(Screen):
    """Choose which independent TXT playlist bank to manage."""

    SOURCE_TYPES = ("xtream", "stalker")
    FOCUS_EDGES = ("top", "bottom", "left", "right")

    def __init__(self, session):
        self.skin = decorate_remote_footer(
            _playlist_type_skin(),
            PLAYLIST_TYPE_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        self.selected_index = 0
        attach_background(self, "app_bg", APP_BACKGROUND)
        self["panel"] = Label("")
        self["header"] = Label(localized_upper(_("Select playlist")))
        self["step"] = Label("1 / 2  •  {}".format(_("Source type")))
        self["instruction"] = Label(
            _("Choose the source type you want to manage.")
        )
        for index in range(2):
            self["card_{}".format(index)] = Label("")
            for edge in self.FOCUS_EDGES:
                self["focus_{}_{}".format(index, edge)] = Label("")
        self["icon_0"] = Label("IPTV")
        self["icon_1"] = Label("MAC")
        self["title_0"] = Label("IPTV / XTREAM")
        self["title_1"] = Label("STALKER / MAC")
        self["description_0"] = Label(
            "TXT  •  {}".format(_("Server URL, username and password"))
        )
        self["description_1"] = Label(
            "TXT  •  {} URL + MAC".format(_("Portal"))
        )
        self["message"] = Label(
            "100 TXT  •  IPTV / XTREAM  •  STALKER / MAC"
        )
        self["footer"] = Label("")
        install_remote_footer(self, PLAYLIST_TYPE_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "WizardActions"],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "back": self.close,
                "up": self.previous,
                "down": self.next,
                "upRepeated": self.previous,
                "downRepeated": self.next,
                "left": self.previous,
                "right": self.next,
                "leftRepeated": self.previous,
                "rightRepeated": self.next,
            },
            -1,
        )
        self._refresh()
        self.setTitle(_("Select playlist"))

    def _refresh(self):
        self["step"].setText(
            "{} / 2  •  {}".format(
                self.selected_index + 1,
                _("Source type"),
            )
        )
        for index in range(2):
            for edge in self.FOCUS_EDGES:
                widget = self["focus_{}_{}".format(index, edge)]
                if index == self.selected_index:
                    widget.show()
                else:
                    widget.hide()

    def previous(self):
        self.selected_index = (self.selected_index - 1) % 2
        self._refresh()

    def next(self):
        self.selected_index = (self.selected_index + 1) % 2
        self._refresh()

    def open_selected(self):
        source_type = self.SOURCE_TYPES[self.selected_index]
        opener = getattr(self.session, "openWithCallback", None)
        if callable(opener):
            opener(
                self._manager_closed,
                GTPlaylistManagerScreen,
                source_type,
            )
        else:
            self.session.open(GTPlaylistManagerScreen, source_type)

    def _manager_closed(self, result=None):
        if result == PLAYLIST_FILES_CONNECTED:
            self.close(PLAYLIST_FILES_CONNECTED)
            return
        self._refresh()


class GTPlaylistManagerScreen(Screen):
    """Manage one 100-slot TXT bank, twelve visible entries per page."""

    page_size = PLAYLIST_FILE_PAGE_SIZE

    def __init__(self, session, source_type, registry=None):
        self.skin = decorate_remote_footer(
            _playlist_manager_skin(),
            PLAYLIST_MANAGER_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        self.source_type = str(source_type or "").lower()
        if self.source_type not in ("xtream", "stalker"):
            self.source_type = "xtream"
        self.registry = registry or default_registry()
        self.selected_index = 0
        self._slots = [None] * MAX_PLAYLIST_FILES_PER_TYPE
        self._active_index = None
        self._rename_index = None
        self._delete_index = None
        self._delete_was_active = False

        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(localized_upper(_("Select playlist")))
        self["subtitle"] = Label(
            "IPTV / XTREAM"
            if self.source_type == "xtream"
            else "STALKER / MAC"
        )
        self["page"] = Label("")
        for index in range(self.page_size):
            self["row_bg_{}".format(index)] = Label("")
            self["row_focus_{}".format(index)] = Label("")
            self["row_marker_{}".format(index)] = Label("")
            self["row_number_{}".format(index)] = Label("")
            self["row_name_{}".format(index)] = Label("")
            self["row_path_{}".format(index)] = Label("")
            self["row_status_{}".format(index)] = Label("")
        self["message"] = Label("")
        self["footer"] = Label("")
        install_remote_footer(self, PLAYLIST_MANAGER_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.choose_file,
                "cancel": self.close,
                "back": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.previous_page,
                "right": self.next_page,
                "leftRepeated": self.previous_page,
                "rightRepeated": self.next_page,
                "green": self.connect_selected,
                "yellow": self.rename_selected,
                "red": self.delete_selected,
            },
            -1,
        )
        self._load()
        self.setTitle(_("Select playlist"))

    @property
    def page_count(self):
        return (
            MAX_PLAYLIST_FILES_PER_TYPE + self.page_size - 1
        ) // self.page_size

    @property
    def page_index(self):
        return self.selected_index // self.page_size

    def _message(self, value):
        fit_dynamic_text(
            self["message"],
            clean_dynamic_text(str(value or "")),
            max_lines=3,
            fallback_chars=120,
        )

    def _load(self):
        try:
            self._slots = self.registry.slots(self.source_type)
            self._active_index = self.registry.active_index(self.source_type)
        except (IOError, OSError, TypeError, ValueError):
            self._slots = [None] * MAX_PLAYLIST_FILES_PER_TYPE
            self._active_index = None
            self._message(_("Error"))
        self._refresh()

    def _set_row_visible(self, row, visible):
        for prefix in (
            "row_bg_",
            "row_focus_",
            "row_marker_",
            "row_number_",
            "row_name_",
            "row_path_",
            "row_status_",
        ):
            widget = self["{}{}".format(prefix, row)]
            if visible:
                widget.show()
            else:
                widget.hide()

    def _refresh(self):
        start = self.page_index * self.page_size
        end = min(start + self.page_size, MAX_PLAYLIST_FILES_PER_TYPE)
        self["page"].setText(
            "{} / {}  •  {}–{} / {}".format(
                self.page_index + 1,
                self.page_count,
                start + 1,
                end,
                MAX_PLAYLIST_FILES_PER_TYPE,
            )
        )
        for row in range(self.page_size):
            index = start + row
            visible = index < MAX_PLAYLIST_FILES_PER_TYPE
            self._set_row_visible(row, visible)
            if not visible:
                continue
            selected = index == self.selected_index
            path = self._slots[index]
            self["row_number_{}".format(row)].setText(str(index + 1))
            self["row_name_{}".format(row)].setText(
                os.path.basename(path) if path else "+ {}".format(_("Add playlist"))
            )
            ellipsize_dynamic_text(
                self["row_path_{}".format(row)],
                os.path.dirname(path) if path else "TXT / txt",
                fallback_chars=38,
            )
            status = ""
            if self._active_index == index:
                status = _("Active")
                if path and not os.path.isfile(path):
                    status = "{}  •  {}".format(status, _("Error"))
            self["row_status_{}".format(row)].setText(status)
            for prefix in ("row_focus_", "row_marker_"):
                widget = self["{}{}".format(prefix, row)]
                if selected:
                    widget.show()
                else:
                    widget.hide()

    def _move(self, step):
        self.selected_index = (
            self.selected_index + int(step)
        ) % MAX_PLAYLIST_FILES_PER_TYPE
        self._refresh()

    def move_up(self):
        self._move(-1)

    def move_down(self):
        self._move(1)

    def _change_page(self, step):
        offset = self.selected_index % self.page_size
        page = (self.page_index + int(step)) % self.page_count
        self.selected_index = min(
            (page * self.page_size) + offset,
            MAX_PLAYLIST_FILES_PER_TYPE - 1,
        )
        self._refresh()

    def previous_page(self):
        self._change_page(-1)

    def next_page(self):
        self._change_page(1)

    def choose_file(self):
        current = self._slots[self.selected_index] or ""
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener) or FileList is None:
            self._message(_("Not available"))
            return
        opener(self._file_selected, GTTXTFileBrowserScreen, current)

    def _file_selected(self, path=None):
        if not path:
            return
        try:
            self.registry.set_slot(
                self.source_type,
                self.selected_index,
                path,
            )
        except (IOError, OSError, TypeError, ValueError):
            self._message(_("Invalid"))
            return
        self._message(
            "{}  •  {}".format(os.path.basename(path), _("Ready to connect"))
        )
        self._load()

    def connect_selected(self):
        path = self._slots[self.selected_index]
        if not path:
            self._message(_("No source is available to connect."))
            return
        if not os.path.isfile(path) or os.path.islink(path):
            # In particular, never let the legacy Stalker helper recreate a
            # disconnected USB/NAS selection at the stored mount path.
            self._message(_("Not available"))
            return
        try:
            if self.source_type == "xtream":
                result = load_accounts([path])
            else:
                result = load_portal_accounts(path)
            if not result.accounts:
                self._message(
                    _localized_runtime_text(
                        result.error,
                        N_("No valid IPTV source was found"),
                    )
                )
                return
            self.registry.activate(self.source_type, self.selected_index)
        except (IOError, OSError, TypeError, ValueError):
            self._message(_("Error"))
            return
        if self.source_type == "stalker":
            try:
                from .stalker import purge_all_portal_sessions

                purge_all_portal_sessions()
            except Exception:
                pass
        else:
            self._notify_xtream_file_changed()
        self.close(PLAYLIST_FILES_CONNECTED)

    @staticmethod
    def _notify_xtream_file_changed():
        try:
            from .dvb_epg_scheduler import notify_settings_changed

            notify_settings_changed()
        except Exception:
            pass

    def rename_selected(self):
        path = self._slots[self.selected_index]
        if not path:
            self._message(_("Not available"))
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self._message(_("The virtual keyboard is unavailable on this image."))
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self._message(_("Could not open the virtual keyboard."))
            return
        self._rename_index = self.selected_index
        opener(
            self._rename_entered,
            VirtualKeyBoard,
            title=_("Rename"),
            text=os.path.splitext(os.path.basename(path))[0],
        )

    def _rename_entered(self, value=None):
        index = self._rename_index
        self._rename_index = None
        if value is None or index is None:
            return
        try:
            path = self.registry.rename_slot(
                self.source_type,
                index,
                value,
            )
        except (IOError, OSError, TypeError, ValueError):
            self._message(_("Error"))
            return
        self._message(
            "{}  •  {}".format(os.path.basename(path), _("Ready to connect"))
        )
        self._load()

    def delete_selected(self):
        path = self._slots[self.selected_index]
        if not path:
            self._message(_("Not available"))
            return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self._message(_("The confirmation screen is unavailable on this image."))
            return
        self._delete_index = self.selected_index
        self._delete_was_active = self._active_index == self.selected_index
        opener(
            self._delete_confirmed,
            MessageBox,
            _("Delete {}?").format(path),
            type=MessageBox.TYPE_YESNO,
            default=False,
        )

    def _delete_confirmed(self, answer=False):
        index = self._delete_index
        was_active = self._delete_was_active
        self._delete_index = None
        self._delete_was_active = False
        if not answer or index is None:
            return
        try:
            path = self.registry.delete_slot_file(self.source_type, index)
        except (IOError, OSError, TypeError, ValueError):
            self._message(
                _("Could not delete the source: {}").format(_("Error"))
            )
            return
        if was_active and self.source_type == "stalker":
            try:
                from .stalker import purge_all_portal_sessions

                purge_all_portal_sessions()
            except Exception:
                pass
        elif was_active:
            self._notify_xtream_file_changed()
        self._load()
        self._message("{}  •  {}".format(os.path.basename(path), _("Delete")))


def _txt_browser_directory(location):
    value = str(location or "").strip()
    if value and os.path.isabs(value):
        absolute = os.path.abspath(value)
        directory = absolute if os.path.isdir(absolute) else os.path.dirname(absolute)
        if os.path.isdir(directory):
            return os.path.join(directory, "")
    return os.path.join(os.path.sep, "")


def _txt_file_extension(path):
    return str(path or "").lower().endswith(".txt")


def _txt_storage_locations(partitions=None, is_directory=None):
    is_directory = is_directory or os.path.isdir
    candidates = (
        ("ROOT", "/"),
        ("ENIGMA2", "/etc/enigma2"),
    ) + M3U_BROWSER_LOCATIONS
    locations = []
    seen = set()

    def add_location(label, path):
        path = os.path.normpath(str(path or ""))
        if path in seen or not os.path.isabs(path) or not is_directory(path):
            return
        seen.add(path)
        locations.append(("{}  •  {}".format(label, path), path))

    for label, path in candidates:
        add_location(label, path)
    if partitions is None:
        try:
            from Components.Harddisk import harddiskmanager

            partitions = harddiskmanager.getMountedPartitions()
        except (AttributeError, ImportError, OSError, RuntimeError, TypeError):
            partitions = ()
    for partition in partitions or ():
        add_location(
            str(getattr(partition, "description", "") or _("Storage")),
            getattr(partition, "mountpoint", ""),
        )
    return locations


class GTTXTFileBrowserScreen(Screen):
    """Browse the full receiver filesystem and select one existing TXT file."""

    def __init__(self, session, current_path=""):
        self.skin = decorate_remote_footer(
            _txt_file_browser_skin(),
            TXT_FILE_BROWSER_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND)
        self["header"] = Label(localized_upper(_("Select playlist")))
        self["instruction"] = Label(
            "{}  •  TXT / txt".format(
                _("Device, USB or mounted network storage")
            )
        )
        self["path_panel"] = Label("")
        self["path"] = Label("")
        self["message"] = Label("/  •  HDD  •  USB  •  NAS  •  /media  •  /mnt")
        self["filelist"] = FileList(
            _txt_browser_directory(current_path),
            showDirectories=True,
            showFiles=True,
            showMountpoints=True,
            matchingPattern=TXT_FILE_PATTERN,
            useServiceRef=False,
            inhibitDirs=False,
            inhibitMounts=False,
            isTop=False,
            enableWrapAround=False,
        )
        self["footer"] = Label("")
        install_remote_footer(self, TXT_FILE_BROWSER_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.open_current,
                "cancel": self.cancel,
                "red": self.cancel,
                "green": self.select_current,
                "blue": self.show_storage,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
            },
            -1,
        )
        self._refresh_path()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refresh_path)
        self.setTitle(_("Select playlist"))

    def _refresh_path(self):
        current = self["filelist"].getCurrentDirectory()
        if current:
            ellipsize_dynamic_text(self["path"], current, fallback_chars=104)
        else:
            self["path"].setText(_("Storage"))

    def _move(self, method):
        method()
        self._refresh_path()

    def move_up(self):
        self._move(self["filelist"].up)

    def move_down(self):
        self._move(self["filelist"].down)

    def page_up(self):
        self._move(self["filelist"].pageUp)

    def page_down(self):
        self._move(self["filelist"].pageDown)

    def open_current(self):
        if self["filelist"].canDescent():
            self["filelist"].descent()
            self._refresh_path()
            return
        self._select_file()

    def select_current(self):
        self.open_current()

    def show_storage(self):
        locations = _txt_storage_locations()
        try:
            from Screens.ChoiceBox import ChoiceBox
        except ImportError:
            ChoiceBox = None
        if ChoiceBox is None or not hasattr(self.session, "openWithCallback"):
            self["filelist"].changeDir(os.path.join(os.path.sep, ""))
            self._refresh_path()
            return
        self.session.openWithCallback(
            self._storage_selected,
            ChoiceBox,
            title=_("Storage"),
            list=locations,
        )

    def _storage_selected(self, selection):
        if not selection or len(selection) < 2:
            return
        directory = _txt_browser_directory(selection[1])
        self["filelist"].changeDir(directory)
        self._refresh_path()

    def _select_file(self):
        path = _m3u_file_list_selection(self["filelist"])
        if not _txt_file_extension(path):
            self["message"].setText(_("Invalid"))
            return
        try:
            details = os.lstat(path)
        except OSError:
            self["message"].setText(_("Invalid"))
            return
        if not os.path.isfile(path) or os.path.islink(path):
            del details
            self["message"].setText(_("Invalid"))
            return
        self.close(os.path.abspath(path))

    def cancel(self):
        self.close(None)


class GTAddM3USourceScreen(Screen):
    """Remote-friendly form for one standard M3U Live TV source."""

    SOURCE_KINDS = ("local", "remote")
    SELECTION_EDGES = ("top", "bottom", "left", "right")
    FIELD_COUNT = 3
    FIELD_TITLES = (
        N_("Enter a name for the M3U source"),
        "",
        N_("Enter the M3U file path or HTTP(S) URL"),
    )

    def __init__(self, session, source_saver=None):
        self.skin = decorate_remote_footer(
            _add_m3u_source_skin(),
            ADD_M3U_SOURCE_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND)
        self.source_saver = source_saver
        self.selected_field = 0
        self._editing_field = 0
        self._field_values = [
            "",
            "local",
            DEFAULT_LOCAL_M3U_PATH,
        ]
        self["panel"] = Label("")
        self["header"] = Label(localized_upper(_("Add M3U Source")))
        self["step"] = Label(
            "2 / 2 | {}".format(localized_upper(_("Source details")))
        )
        self["instruction"] = Label(
            _("Give the source a name and choose its location.")
        )
        self["name_panel"] = Label("")
        self["name_caption"] = Label(localized_upper(_("Source name")))
        self["name_value"] = Label("")
        self["focus_0"] = Label("")
        for index in range(2):
            self["kind_card_{}".format(index)] = Label("")
            for edge in self.SELECTION_EDGES:
                self["selected_{}_{}".format(index, edge)] = Label("")
            self["kind_focus_{}".format(index)] = Label("")
        self["kind_icon_0"] = Label("NAS")
        self["kind_icon_1"] = Label("URL")
        self["kind_title_0"] = Label(localized_upper(_("Local / NAS")))
        self["kind_title_1"] = Label(localized_upper(_("Web URL")))
        self["kind_description_0"] = Label(
            _("Device, USB or mounted network storage")
        )
        self["kind_description_1"] = Label(
            _("Direct HTTP(S) M3U link")
        )
        self["kind_meta_0"] = Label("HDD  |  USB  |  NAS  |  /mnt")
        self["kind_meta_1"] = Label("HTTP  |  HTTPS")
        self["location_panel"] = Label("")
        self["location_caption"] = Label("")
        self["location_value"] = Label("")
        self["location_action"] = Label("")
        self["focus_2"] = Label("")
        self["info_panel"] = Label("")
        self["info_icon"] = Label("i")
        self["message"] = Label("")
        self["footer"] = Label("")
        install_remote_footer(self, ADD_M3U_SOURCE_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.edit_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.previous_kind,
                "right": self.next_kind,
                "green": self.save,
                "red": self.close,
            },
            -1,
        )
        self._refresh(self._kind_help())
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refresh)
        self.setTitle(_("Add M3U Source"))

    def _kind_help(self):
        if self._field_values[1] == "local":
            return "HDD / USB / NAS  •  /media  •  /mnt"
        return _("M3U URL must start with http:// or https://.")

    def _refresh(self, message=None):
        name = self._field_values[0]
        kind = self._field_values[1]
        local_kind = kind == "local"
        selected_kind = 0 if local_kind else 1
        location = self._field_values[2] or (
            DEFAULT_LOCAL_M3U_PATH if local_kind else "https://"
        )
        if name:
            ellipsize_dynamic_text(
                self["name_value"],
                name,
                fallback_chars=50,
            )
        else:
            self["name_value"].setText(_("Enter a source name"))
        self["location_caption"].setText(
            localized_upper(_("File path") if local_kind else _("Web URL"))
        )
        ellipsize_dynamic_text(
            self["location_value"],
            location,
            fallback_chars=44,
        )
        self["location_action"].setText(
            "OK  {}".format(
                localized_upper(_("Select") if local_kind else _("Edit"))
            )
        )
        if message is not None:
            self["message"].setText(message)

        for index in range(2):
            selected = index == selected_kind
            for edge in self.SELECTION_EDGES:
                marker = self["selected_{}_{}".format(index, edge)]
                if selected:
                    marker.show()
                else:
                    marker.hide()
            kind_focus = self["kind_focus_{}".format(index)]
            if self.selected_field == 1 and selected:
                kind_focus.show()
            else:
                kind_focus.hide()

        for index in (0, 2):
            focus = self["focus_{}".format(index)]
            if index == self.selected_field:
                focus.show()
            else:
                focus.hide()

    def move_up(self):
        self.selected_field = (self.selected_field - 1) % self.FIELD_COUNT
        self._refresh()

    def move_down(self):
        self.selected_field = (self.selected_field + 1) % self.FIELD_COUNT
        self._refresh()

    def _change_kind(self, step):
        if self.selected_field != 1:
            return
        current = self._field_values[1]
        try:
            index = self.SOURCE_KINDS.index(current)
        except ValueError:
            index = 0
        new_kind = self.SOURCE_KINDS[(index + step) % len(self.SOURCE_KINDS)]
        old_default = (
            DEFAULT_LOCAL_M3U_PATH if current == "local" else "https://"
        )
        if not self._field_values[2] or self._field_values[2] == old_default:
            self._field_values[2] = (
                DEFAULT_LOCAL_M3U_PATH if new_kind == "local" else "https://"
            )
        self._field_values[1] = new_kind
        self._refresh(self._kind_help())

    def previous_kind(self):
        self._change_kind(-1)

    def next_kind(self):
        self._change_kind(1)

    def edit_selected(self):
        if self.selected_field == 1:
            self.next_kind()
            return
        if (
            self.selected_field == 2
            and self._field_values[1] == "local"
            and FileList is not None
        ):
            if not hasattr(self.session, "openWithCallback"):
                self["message"].setText(_("M3U file path is invalid."))
                return
            self.session.openWithCallback(
                self._file_selected,
                GTM3UFileBrowserScreen,
                current_path=self._field_values[2],
            )
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(
                _("The virtual keyboard is unavailable on this image.")
            )
            return
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        self._editing_field = self.selected_field
        self.session.openWithCallback(
            self._value_entered,
            VirtualKeyBoard,
            title=_(self.FIELD_TITLES[self.selected_field]),
            text=self._field_values[self.selected_field],
        )

    def _file_selected(self, value):
        if value is None:
            self._refresh(self._kind_help())
            return
        path = str(value or "").strip()
        if not _m3u_file_extension(path):
            self["message"].setText(_("M3U file path is invalid."))
            return
        try:
            path = validate_local_m3u_path(path)
        except ContentError as error:
            fit_dynamic_text(
                self["message"],
                clean_dynamic_text(_localized_runtime_text(str(error))),
                max_lines=2,
                fallback_chars=78,
            )
            return
        self._field_values[2] = path
        self.selected_field = 2
        self._refresh(
            _(
                "Information received. Edit the next field or press GREEN "
                "to save."
            )
        )

    def _value_entered(self, value):
        if value is None:
            self["message"].setText(_("Field entry cancelled."))
            return
        self._field_values[self._editing_field] = str(value).strip()
        self.selected_field = (self._editing_field + 1) % self.FIELD_COUNT
        self._refresh(
            _(
                "Information received. Edit the next field or press GREEN "
                "to save."
            )
        )

    def save(self):
        saver = self.source_saver
        if saver is None:
            from .m3u import M3USource, save_m3u_source

            saver = save_m3u_source
        else:
            M3USource = None
        try:
            source = (
                M3USource(
                    None,
                    self._field_values[0],
                    self._field_values[1],
                    self._field_values[2],
                )
                if M3USource is not None
                else None
            )
            if source is None:
                saved = saver(
                    self._field_values[0],
                    self._field_values[1],
                    self._field_values[2],
                )
            else:
                saved = saver(source)
        except (IOError, OSError, TypeError, ValueError) as error:
            fit_dynamic_text(
                self["message"],
                _("Could not save the M3U source: {}").format(
                    clean_dynamic_text(_localized_runtime_text(str(error)))
                ),
                max_lines=2,
                fallback_chars=78,
            )
            return
        fit_dynamic_text(
            self["message"],
            _("{} was saved as an M3U Live TV source.").format(
                clean_dynamic_text(saved.display_name)
            ),
            max_lines=2,
            fallback_chars=78,
        )
        self.close(True)


class GTAddAccountScreen(Screen):
    OUTPUT_FORMATS = ("ts", "m3u8")
    FIELD_COUNT = 4
    FIELD_TITLES = (
        N_("Enter the server URL"),
        N_("Enter the username"),
        N_("Enter the password"),
    )

    def __init__(
        self,
        session,
        credential_saver=None,
    ):
        self.skin = decorate_remote_footer(
            _add_account_skin(),
            ADD_ACCOUNT_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND)
        self.credential_saver = credential_saver or save_account_credentials
        self.selected_field = 0
        self._editing_field = 0
        # Screen inherits from dict and its real hide() lifecycle calls
        # self.values().  Never shadow that method with form state.
        self._field_values = ["http://", "", "", "ts"]
        self["top_accent"] = Label("")
        self["panel"] = Label("")
        self["header"] = Label(_("ADD NEW ACCOUNT"))
        self["instruction"] = Label(
            _("Press OK in a text field; use LEFT / RIGHT on the output row.")
        )
        self["url_field"] = Label("")
        self["username_field"] = Label("")
        self["password_field"] = Label("")
        self["output_field"] = Label("")
        for index in range(self.FIELD_COUNT):
            self["focus_{}".format(index)] = Label("")
        self["message"] = Label(
            _("Press GREEN to save  |  Select a TS or M3U8 output supported by the server.")
        )
        self["footer"] = Label("")
        install_remote_footer(self, ADD_ACCOUNT_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.edit_selected,
                "cancel": self.close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.previous_output,
                "right": self.next_output,
                "green": self.save,
                "red": self.close,
            },
            -1,
        )
        self._refresh()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refresh)
        self.setTitle(_("Add New Account"))

    def _refresh(self):
        self["header"].setText(_("ADD NEW ACCOUNT"))
        self.setTitle(_("Add New Account"))
        self["instruction"].setText(
            _("Press OK in a text field; use LEFT / RIGHT on the output row.")
        )
        url_value = (
            clean_dynamic_text(self._field_values[0])
            if self._field_values[0]
            else "http://"
        )
        username_value = (
            clean_dynamic_text(self._field_values[1])
            if self._field_values[1]
            else _("Enter the username")
        )
        password_value = "*" * min(len(self._field_values[2]), 24)
        if not password_value:
            password_value = _("Enter the password")
        _ellipsize_inset_text(
            self["url_field"],
            _("  SERVER URL        {}").format(url_value),
            fallback_chars=52,
        )
        _ellipsize_inset_text(
            self["username_field"],
            _("  USERNAME          {}").format(username_value),
            fallback_chars=52,
        )
        self["password_field"].setText(
            _("  PASSWORD          {}").format(password_value)
        )
        self["output_field"].setText(
            _("  OUTPUT FORMAT     {}     Change with LEFT / RIGHT").format(
                self._field_values[3].upper()
            )
        )
        for index in range(self.FIELD_COUNT):
            if index == self.selected_field:
                self["focus_{}".format(index)].show()
            else:
                self["focus_{}".format(index)].hide()

    def move_up(self):
        self.selected_field = (
            self.selected_field - 1
        ) % self.FIELD_COUNT
        self._refresh()

    def move_down(self):
        self.selected_field = (
            self.selected_field + 1
        ) % self.FIELD_COUNT
        self._refresh()

    def _change_output(self, step):
        if self.selected_field != 3:
            return
        current = self._field_values[3]
        try:
            index = self.OUTPUT_FORMATS.index(current)
        except ValueError:
            index = 0
        self._field_values[3] = self.OUTPUT_FORMATS[
            (index + step) % len(self.OUTPUT_FORMATS)
        ]
        message = _("Live TV output selected: {}.").format(
            self._field_values[3].upper()
        )
        self["message"].setText(message)
        self._refresh()

    def previous_output(self):
        self._change_output(-1)

    def next_output(self):
        self._change_output(1)

    def edit_selected(self):
        if self.selected_field == 3:
            self.next_output()
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(_("The virtual keyboard is unavailable on this image."))
            return
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        self._editing_field = self.selected_field
        keyboard_title = _(
            self.FIELD_TITLES[self.selected_field]
        )
        self.session.openWithCallback(
            self._value_entered,
            VirtualKeyBoard,
            title=keyboard_title,
            text=self._field_values[self.selected_field],
        )

    def _value_entered(self, value):
        if value is None:
            self["message"].setText(_("Field entry cancelled."))
            return
        self._field_values[self._editing_field] = str(value).strip()
        self.selected_field = (
            self._editing_field + 1
        ) % self.FIELD_COUNT
        self["message"].setText(
            _("Information received. Edit the next field or press GREEN to save.")
        )
        self._refresh()

    def save(self):
        try:
            saved = self.credential_saver(
                self._field_values[0],
                self._field_values[1],
                self._field_values[2],
                output_format=self._field_values[3],
            )
        except (IOError, OSError, ValueError) as error:
            fit_dynamic_text(
                self["message"],
                _("Could not save the account: {}").format(
                    _localized_runtime_text(str(error))
                ),
                max_lines=4,
                fallback_chars=68,
            )
            return
        output_format = getattr(
            saved, "output_format", self._field_values[3]
        ).upper()
        message = _("{} was saved with {} output.").format(
            clean_dynamic_text(saved.host),
            output_format,
        )
        fit_dynamic_text(
            self["message"],
            message,
            max_lines=4,
            fallback_chars=68,
        )
        self.close(True)


class GTServerHealthScreen(Screen):
    def __init__(self, session, account, checker=None):
        source_type = _source_type(account)
        is_m3u = source_type == "m3u"
        is_stalker = source_type == "stalker"
        footer_items = M3U_HEALTH_FOOTER_ITEMS if is_m3u else HEALTH_FOOTER_ITEMS
        self.skin = decorate_remote_footer(
            _health_skin(),
            footer_items,
        )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND)
        self.account = account
        self.checker = checker or check_source_health
        self._checking = False
        self._result = None
        self._result_ready = False
        self._closed = False
        self._health_job = None
        self._health_signal = HEALTH_SIGNAL_NEUTRAL
        self._timer = eTimer()
        _connect_timer(self._timer, self._poll_result)

        for widget_name in (
            "health_panel",
            "panel_top",
            "panel_bottom",
            "panel_left",
            "panel_right",
            "vertical_divider",
            "details_left_bg",
            "details_right_bg",
            "details_left_top",
            "details_right_top",
            "legend_bg",
            "legend_green_dot",
            "legend_yellow_dot",
            "legend_red_dot",
        ):
            self[widget_name] = Label("")
        self["traffic_base"] = Pixmap()
        self["traffic_red"] = Pixmap()
        self["traffic_yellow"] = Pixmap()
        self["traffic_green"] = Pixmap()
        self["header"] = Label(
            _("Source Health")
            if is_m3u
            else (_("Portal Health") if is_stalker else _("Server Health"))
        )
        account_text = health_account_text(
            source_type,
            getattr(account, "display_name", ""),
            _safe_source_name(account),
            _safe_source_endpoint(account),
        )
        self["account"] = Label("")
        ellipsize_dynamic_text(
            self["account"],
            account_text,
            fallback_chars=108,
        )
        self["status"] = Label(_("WAITING FOR TEST"))
        self["status_green"] = Label("")
        self["status_yellow"] = Label("")
        self["status_red"] = Label("")
        self["status_badge"] = Label(_("Unknown"))
        self["details_left"] = Label("")
        self["details_right"] = Label("")
        details = (
            _("The M3U source will be checked automatically when this screen opens.")
            if is_m3u
            else (
                _(
                    "The Stalker / MAC portal will be checked automatically when this screen opens."
                )
                if is_stalker
                else _(
                    "The Xtream API connection will be tested automatically when this screen opens."
                )
            )
        )
        self["details"] = Label(details)
        self["legend_green"] = Label(_("Active"))
        self["legend_yellow"] = Label(
            "{}  /  {}".format(_("Busy"), _("Blocked"))
        )
        self["legend_red"] = Label(_("Error"))
        self["footer"] = Label("")
        # The skin loads these small RGBA PNGs through native LoadPixmap.
        # ePicLoad composites true-colour PNG alpha onto its background,
        # producing black rectangles around the housing and active lamp.
        self._set_health_visual(
            _("WAITING FOR TEST"),
            HEALTH_SIGNAL_NEUTRAL,
            _("Unknown"),
        )
        install_remote_footer(self, footer_items)
        actions = {
            "ok": self.start_check,
            "cancel": self.close,
            "green": self.start_check,
            "red": self.close,
        }
        if is_m3u:
            actions["blue"] = self.open_m3u_epg_settings
        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            actions,
            -1,
        )
        if hasattr(self, "onShown"):
            self.onShown.append(self.start_check)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._apply_traffic_signal)
            self.onLayoutFinish.append(self._refit_account)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop)
        self.setTitle(
            _("Source Health")
            if is_m3u
            else (_("Portal Health") if is_stalker else _("Server Health"))
        )

    def _refit_account(self):
        account_text = health_account_text(
            _source_type(self.account),
            getattr(self.account, "display_name", ""),
            _safe_source_name(self.account),
            _safe_source_endpoint(self.account),
        )
        ellipsize_dynamic_text(
            self["account"],
            account_text,
            fallback_chars=108,
        )

    def _apply_traffic_signal(self):
        for signal_name in (
            HEALTH_SIGNAL_RED,
            HEALTH_SIGNAL_YELLOW,
            HEALTH_SIGNAL_GREEN,
        ):
            widget = self["traffic_{}".format(signal_name)]
            if signal_name == self._health_signal:
                widget.show()
            else:
                widget.hide()

    def _set_health_visual(self, status_text, signal_name, badge_text):
        signal_name = (
            signal_name
            if signal_name in (
                HEALTH_SIGNAL_GREEN,
                HEALTH_SIGNAL_YELLOW,
                HEALTH_SIGNAL_RED,
            )
            else HEALTH_SIGNAL_NEUTRAL
        )
        status_text = localized_upper(str(status_text or _("Unknown")))
        status_widgets = {
            HEALTH_SIGNAL_NEUTRAL: "status",
            HEALTH_SIGNAL_GREEN: "status_green",
            HEALTH_SIGNAL_YELLOW: "status_yellow",
            HEALTH_SIGNAL_RED: "status_red",
        }
        for widget_name in status_widgets.values():
            widget = self[widget_name]
            ellipsize_dynamic_text(
                widget,
                status_text,
                fallback_chars=18,
            )
            if widget_name == status_widgets[signal_name]:
                widget.show()
            else:
                widget.hide()
        ellipsize_dynamic_text(
            self["status_badge"],
            localized_upper(str(badge_text or _("Unknown"))),
            fallback_chars=20,
        )
        self._health_signal = signal_name
        self._apply_traffic_signal()

    @staticmethod
    def _result_status_text(result):
        status = str(getattr(result, "status", "") or "")
        account_status = str(
            getattr(result, "account_status", "") or ""
        )
        if status.casefold() == "disabled" and account_status:
            return _localized_runtime_text(account_status)
        return _localized_runtime_text(status)

    @staticmethod
    def _result_badge_text(result, signal_name):
        status = str(getattr(result, "status", "") or "").casefold()
        account_status = str(
            getattr(result, "account_status", "") or ""
        ).strip()
        if signal_name == HEALTH_SIGNAL_GREEN:
            return _("Active")
        if signal_name == HEALTH_SIGNAL_YELLOW:
            if status == "stale":
                return _("Warning")
            if status == "busy":
                return _("Busy")
            return _localized_runtime_text(account_status or N_("Blocked"))
        if signal_name == HEALTH_SIGNAL_RED:
            return _("Invalid") if status == "invalid" else _("Error")
        return _("Unknown")

    def _show_result_visual(self, result):
        signal_name = health_signal(
            getattr(result, "status", ""),
            getattr(result, "account_status", ""),
        )
        self._set_health_visual(
            self._result_status_text(result),
            signal_name,
            self._result_badge_text(result, signal_name),
        )

    def open_m3u_epg_settings(self):
        """Open source-scoped XMLTV settings without starting an update."""
        if _source_type(self.account) != "m3u":
            return
        try:
            from .m3u_epg_ui import GTM3UXMLTVSettingsScreen
        except Exception:
            self["details"].setText(_("XMLTV EPG settings could not be opened."))
            return
        if hasattr(self.session, "openWithCallback"):
            self.session.openWithCallback(
                self._m3u_epg_settings_closed,
                GTM3UXMLTVSettingsScreen,
                self.account,
            )
        else:
            self.session.open(GTM3UXMLTVSettingsScreen, self.account)

    def _m3u_epg_settings_closed(self, *unused):
        """Refresh only the small local status record after closing settings."""
        try:
            from .m3u_epg_settings import get_m3u_epg_binding

            binding = get_m3u_epg_binding(self.account.source_id)
        except Exception:
            binding = None
        if binding is None or not bool(getattr(binding, "enabled", False)):
            self["details"].setText(_("XMLTV EPG is disabled for this M3U source."))
            return
        last_success = int(getattr(binding, "last_success_utc", 0) or 0)
        if last_success:
            status = _("XMLTV EPG is ready from the local cache.")
        else:
            status = _("XMLTV EPG is enabled and waiting for its first update.")
        self["details"].setText(status)

    def start_check(self):
        if self._checking or self._closed:
            return
        self._checking = True
        self._result = None
        self._result_ready = False
        self._set_health_visual(
            _("TESTING..."),
            HEALTH_SIGNAL_NEUTRAL,
            _("Unknown"),
        )
        self["details_left"].setText("")
        self["details_right"].setText("")
        source_type = _source_type(self.account)
        details = (
            _("Checking M3U access, catalogue size and cache status.")
            if source_type == "m3u"
            else (
                _("Checking portal authentication, categories and latency.")
                if source_type == "stalker"
                else _(
                    "Checking API access, account status, latency and connection limits."
                )
            )
        )
        self["details"].setText(details)
        _cancel_ui_job(self._health_job)
        job = _UIJobToken(UI_NETWORK_JOB_TIMEOUT_SECONDS)
        self._health_job = job
        worker = threading.Thread(
            target=lambda: self._run_check(job),
        )
        worker.daemon = True
        worker.start()
        self._timer.start(150, True)

    def _run_check(self, job=None):
        try:
            if job is not None:
                job.check()
            result = self.checker(self.account)
        except Exception:
            result = ServerHealth(
                "ERROR",
                detail="The health check could not be completed.",
            )
        if self._closed or (job is not None and not job.active()):
            return
        self._result = result
        self._result_ready = True

    def _poll_result(self):
        if self._closed:
            return
        if not self._result_ready:
            if self._health_job is not None and self._health_job.expired():
                self._checking = False
                self._health_job = None
                self._set_health_visual(
                    _("Error"),
                    HEALTH_SIGNAL_RED,
                    _("Error"),
                )
                self["details_left"].setText("")
                self["details_right"].setText("")
                self["details"].setText(_("The health check could not be completed."))
                return
            self._timer.start(150, True)
            return
        self._checking = False
        self._health_job = None
        result = self._result
        self._show_result_visual(result)
        if (
            str(getattr(result, "source_type", "") or "").lower() == "m3u"
            or _source_type(self.account) == "m3u"
        ):
            source_kind = clean_dynamic_text(
                _localized_runtime_text(
                    getattr(result, "source_kind", "")
                    or _source_badge(self.account)
                )
            )
            source_size = getattr(result, "source_size", None)
            item_count = getattr(result, "item_count", None)
            category_count = getattr(result, "category_count", None)
            cache_age = clean_dynamic_text(
                _localized_runtime_text(
                    getattr(result, "cache_age", "") or N_("Unknown")
                )
            )
            modified = clean_dynamic_text(
                _localized_runtime_text(
                    getattr(result, "modified", "") or N_("Unknown")
                )
            )
            detail = clean_dynamic_text(
                _localized_runtime_text(
                    getattr(result, "detail", ""),
                    N_("Information"),
                )
            )
            rendered = _(M3U_HEALTH_DETAILS_TEMPLATE).format(
                getattr(result, "latency_ms", 0),
                source_kind,
                source_size if source_size is not None else _("Unknown"),
                item_count if item_count is not None else _("Unknown"),
                category_count if category_count is not None else _("Unknown"),
                cache_age,
                modified,
                detail,
            )
            left, right, message = split_health_text(rendered, 4)
        elif (
            str(getattr(result, "source_type", "") or "").lower()
            == "stalker"
            or _source_type(self.account) == "stalker"
        ):
            account_status = clean_dynamic_text(
                _localized_runtime_text(result.account_status)
            )
            connection_value = getattr(result, "connection_text", None)
            connection_value = (
                connection_value()
                if callable(connection_value)
                else N_("Unknown")
            )
            connection_text = clean_dynamic_text(
                _localized_runtime_text(connection_value)
            )
            account_summary = "{}\n{}".format(
                account_status,
                localized_connection_usage_line(connection_text, _),
            )
            rendered = _(STALKER_HEALTH_DETAILS_TEMPLATE).format(
                getattr(result, "latency_ms", 0),
                account_summary,
                clean_dynamic_text(_localized_runtime_text(result.protocol)),
                clean_dynamic_text(_localized_runtime_text(result.output_format)),
                clean_dynamic_text(
                    _localized_runtime_text(result.detail, N_("Information"))
                ),
            )
            left, right, message = split_health_text(rendered, 3)
            expiry = clean_dynamic_text(
                _localized_runtime_text(
                    getattr(result, "expiry", ""),
                    N_("Server does not report expiry"),
                )
            )
            # Give translated expiry values room to wrap before fitting the
            # deliberate protocol/output rows in the same information card.
            expiry_rows = fit_dynamic_text(
                self["details_right"],
                _("Expiry: {}").format(expiry),
                max_lines=4,
                fallback_chars=31,
            )
            right = "{}\n{}".format(expiry_rows, right)
        else:
            account_status = clean_dynamic_text(
                _localized_runtime_text(result.account_status)
            )
            connection_text = clean_dynamic_text(
                _localized_runtime_text(result.connection_text())
            )
            expiry = clean_dynamic_text(_localized_runtime_text(result.expiry))
            server_time = clean_dynamic_text(
                _localized_runtime_text(result.server_time)
            )
            timezone = clean_dynamic_text(_localized_runtime_text(result.timezone))
            protocol = clean_dynamic_text(_localized_runtime_text(result.protocol))
            output_format = clean_dynamic_text(
                _localized_runtime_text(result.output_format)
            )
            server_version = clean_dynamic_text(
                _localized_runtime_text(result.server_version)
            )
            detail = clean_dynamic_text(
                _localized_runtime_text(result.detail, N_("Information"))
            )
            values = (
                result.latency_ms,
                account_status,
                connection_text,
                expiry,
                server_time,
                timezone,
                protocol,
                output_format,
                server_version,
                detail,
            )
            rendered = _(SERVER_HEALTH_DETAILS_TEMPLATE).format(*values)
            left, right, message = split_health_text(rendered, 5)
        _set_dynamic_lines(
            self["details_left"],
            left,
            fallback_chars=28,
            max_lines=10,
        )
        _set_dynamic_lines(
            self["details_right"],
            right,
            fallback_chars=31,
            max_lines=10,
        )
        fit_dynamic_text(
            self["details"],
            message,
            max_lines=2,
            fallback_chars=66,
        )

    def _stop(self):
        self._closed = True
        _cancel_ui_job(self._health_job)
        self._health_job = None
        try:
            self._timer.stop()
        except Exception:
            pass


class GTDashboardScreen(Screen):
    def __init__(self, session, account, health=None):
        self.skin = decorate_remote_footer(
            _dashboard_skin(),
            DASHBOARD_FOOTER_ITEMS,
            transparent_panel=True,
            show_dividers=False,
        )
        Screen.__init__(self, session)
        self.account = account
        self._content_client = _matching_health_content_client(
            account,
            health,
        )
        self.selected_index = 0
        self._dashboard_child_open = False
        self._dashboard_closed = False
        self._clock_timer = eTimer()
        _connect_timer(self._clock_timer, self._update_clock)

        attach_background(self, "dashboard_bg", DASHBOARD_BACKGROUND)
        # The PNG contains artwork only. All changing text and focus indicators
        # are real Enigma2 widgets so remote navigation remains fully live.
        self["brand_gt"] = Label("GT")
        self["header"] = Label("IPTV PLAYER PRO")
        account_type = _source_type(account)
        self["account_bar"] = Label("")
        status = "ONLINE"
        if health is not None and health.status:
            status = health.status
        self._dashboard_status_text = clean_dynamic_text(
            _localized_runtime_text(status)
        )
        self["status_label"] = Label(
            _("Source")
            if account_type == "m3u"
            else (_("Portal") if account_type == "stalker" else _("Server"))
        )
        self["server_url"] = Label("")
        self["status"] = Label("")
        self["clock"] = Label("")
        self["archive_backdrop"] = Label("")
        self["archive_backdrop"].hide()
        self["archive_hero"] = Pixmap()
        self["archive_hero"].hide()
        self._archive_hero_loaded = False
        for index in range(len(CONTENT_ITEMS)):
            self["card_{}".format(index)] = Label("")
            self["focus_{}".format(index)] = Label("")
            self["focus_edge_{}".format(index)] = Label("")
        self["preview_title"] = Label("")
        self["continue_preview_title"] = Label("")
        self["preview_description"] = Label("")
        movie_label = _("MOVIES")
        series_label = _("SERIES")
        continue_label = _("CONTINUE WATCHING")
        if not _source_supports(account, "movie"):
            movie_label = "{}  —".format(movie_label)
        if not _source_supports(account, "series"):
            series_label = "{}  —".format(series_label)
        if not _source_supports(account, "continue"):
            continue_label = "{}  —".format(continue_label)
        self["quick_0"] = Label(movie_label)
        self["quick_1"] = Label(series_label)
        self["quick_2"] = Label(continue_label)
        self["quick_3"] = Label(_("FAVORITES"))
        self._dashboard_quick_labels = (
            movie_label, series_label, continue_label, _("FAVORITES"),
        )
        for index in range(4):
            self["quick_focus_{}".format(index)] = Label("")
            for edge in ("top", "bottom", "left", "right"):
                self["quick_glow_{}_{}".format(index, edge)] = Label("")
        for edge in ("top", "bottom", "left", "right"):
            self["hero_glow_{}".format(edge)] = Label("")
        self["footer"] = Label("")
        self._refit_source_labels()
        install_remote_footer(self, DASHBOARD_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            [
                "OkCancelActions",
                "DirectionActions",
                "SetupActions",
                "WizardActions",
                "ListboxActions",
                "ColorActions",
                "MenuActions",
                "NavigationActions",
                "ChannelSelectBaseActions",
            ],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "back": self.close,
                "left": self.move_left,
                "right": self.move_right,
                "up": self.move_up,
                "down": self.move_down,
                "moveUp": self.move_up,
                "moveDown": self.move_down,
                "pageUp": self.move_up,
                "pageDown": self.move_down,
                "channelUp": self.move_up,
                "channelDown": self.move_down,
                "nextBouquet": self.move_up,
                "prevBouquet": self.move_down,
                "green": self.close,
                "yellow": self.open_about,
                "menu": self.open_settings,
                "red": self.close,
            },
            -1,
        )
        self._refresh_selection()
        self._update_clock()
        self._activate_remote_actions()
        if hasattr(self, "onShown"):
            self.onShown.append(self._activate_remote_actions)
            self.onShown.append(self._dashboard_shown)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refit_source_labels)
            self.onLayoutFinish.append(self._refresh_selection)
        if hasattr(self, "onClose"):
            self.onClose.append(self._shutdown_dashboard)
        self.setTitle("{} v{}".format(PLUGIN_NAME, PLUGIN_VERSION))

    def _refit_source_labels(self):
        account_type = _source_type(self.account)
        ellipsize_dynamic_text(
            self["account_bar"],
            dashboard_account_text(
                account_type,
                getattr(self.account, "display_name", ""),
                _safe_source_endpoint(self.account),
            ),
            fallback_chars=15,
        )
        ellipsize_dynamic_text(
            self["server_url"],
            _safe_source_name(self.account),
            fallback_chars=23,
        )
        ellipsize_dynamic_text(
            self["status"],
            self._dashboard_status_text,
            fallback_chars=9,
        )

    def _refresh_selection(self):
        unused_width, unused_height, px = _scale()
        for index in range(len(CONTENT_ITEMS)):
            card = self["card_{}".format(index)]
            action = CONTENT_ITEMS[index][2]
            label = _(CONTENT_ITEMS[index][0])
            if action == "catchup":
                label = localized_upper(label)
            if not self._action_available(action):
                label = "{}  —".format(label)
            fit_dynamic_text(
                card, label, fallback_chars=30,
                preferred_size=font_px(px, 31 if action == "continue" else 44),
                min_size=px(22),
            )
            if index == self.selected_index:
                self["focus_{}".format(index)].show()
                self["focus_edge_{}".format(index)].show()
            else:
                self["focus_{}".format(index)].hide()
                self["focus_edge_{}".format(index)].hide()
        item = CONTENT_ITEMS[self.selected_index]
        archive_selected = item[2] == "catchup"
        if archive_selected and not self._archive_hero_loaded:
            self._archive_hero_loaded = True
            attach_pixmap(
                self, "archive_hero", ARCHIVE_HERO_PATH,
                on_loaded=self._archive_hero_ready,
                cover_ratio=(3, 1),
            )
        if archive_selected:
            self["archive_backdrop"].show()
            self["archive_hero"].show()
        else:
            self["archive_backdrop"].hide()
            self["archive_hero"].hide()
        if item[2] == "continue":
            self["preview_title"].hide()
            fit_dynamic_text(
                self["continue_preview_title"], _(item[0]), fallback_chars=40,
                preferred_size=font_px(px, 48, role="title"), min_size=px(26),
            )
            self["continue_preview_title"].show()
        else:
            self["continue_preview_title"].hide()
            title = localized_upper(_(item[0])) if archive_selected else _(item[0])
            fit_dynamic_text(
                self["preview_title"], title, fallback_chars=40,
                preferred_size=font_px(px, 76, role="title"), min_size=px(28),
            )
            self["preview_title"].show()
        fit_dynamic_text(
            self["preview_description"], _(item[1]), max_lines=2,
            fallback_chars=90,
            preferred_size=font_px(px, 36), min_size=px(25),
        )
        for index in range(4):
            quick = self["quick_{}".format(index)]
            fit_dynamic_text(
                quick, self._dashboard_quick_labels[index], fallback_chars=32,
                preferred_size=font_px(px, 27 if index == 2 else 36),
                min_size=px(21),
            )
            quick_focus = self["quick_focus_{}".format(index)]
            selected = item[2] == ("movie", "series", "continue", "favorites")[index]
            if selected:
                quick_focus.show()
            else:
                quick_focus.hide()
            for edge in ("top", "bottom", "left", "right"):
                glow = self["quick_glow_{}_{}".format(index, edge)]
                if selected:
                    glow.show()
                else:
                    glow.hide()
        hero_selected = item[2] in ("live", "catchup", "settings")
        for edge in ("top", "bottom", "left", "right"):
            glow = self["hero_glow_{}".format(edge)]
            if hero_selected:
                glow.show()
            else:
                glow.hide()

    def _archive_hero_ready(self, loaded):
        # Decoding may finish after the user has moved to another menu card.
        if self._dashboard_closed or CONTENT_ITEMS[self.selected_index][2] != "catchup":
            self["archive_hero"].hide()
        elif loaded:
            self["archive_hero"].show()

    def _activate_remote_actions(self):
        action_map = self["actions"]
        set_enabled = getattr(action_map, "setEnabled", None)
        if set_enabled is not None:
            set_enabled(True)

    def _update_clock(self):
        if self._dashboard_child_open or self._dashboard_closed:
            return
        now = datetime.datetime.now()
        self["clock"].setText(now.strftime("%H:%M"))
        remaining = 60 - now.second
        self._clock_timer.start(max(250, remaining * 1000), True)

    def _stop_clock(self):
        try:
            self._clock_timer.stop()
        except Exception:
            pass

    def _shutdown_dashboard(self):
        self._dashboard_closed = True
        self._stop_clock()
        self._content_client = None

    def _dashboard_shown(self):
        if self._dashboard_closed:
            return
        self._dashboard_child_open = False
        self._update_clock()

    def _dashboard_child_closed(self, *args):
        if args and args[0] == PLAYLIST_FILES_CONNECTED:
            self.close(PLAYLIST_FILES_CONNECTED)
            return
        self._dashboard_shown()

    def _open_dashboard_child(self, screen, *args):
        self._dashboard_child_open = True
        self._stop_clock()
        opener = getattr(self.session, "openWithCallback", None)
        if callable(opener):
            return opener(self._dashboard_child_closed, screen, *args)
        dialog = self.session.open(screen, *args)
        callbacks = getattr(dialog, "onClose", None)
        if isinstance(callbacks, list):
            callbacks.append(self._dashboard_child_closed)
        return dialog

    def _dashboard_content_client(self):
        if _source_type(self.account) != "stalker":
            return None
        if self._content_client is None:
            from .browser import content_client_for

            self._content_client = content_client_for(self.account)
        return self._content_client

    def _action_available(self, action):
        action = str(action or "")
        if action == "settings":
            return True
        if action == "catchup":
            # Archive support is provider/channel metadata, loaded on entry.
            return _source_type(self.account) in SOURCE_TYPES
        if action == "continue":
            return _source_supports(self.account, "continue")
        return _source_supports(self.account, action)

    def _available_indexes(self):
        return [
            index
            for index, item in enumerate(CONTENT_ITEMS)
            if self._action_available(item[2])
        ]

    def _move(self, step):
        available = self._available_indexes()
        if not available:
            return
        try:
            current = available.index(self.selected_index)
        except ValueError:
            current = 0
        self.selected_index = available[(current + int(step)) % len(available)]
        self._refresh_selection()

    def move_left(self):
        self._move(-1)

    def move_right(self):
        self._move(1)

    def move_up(self):
        self._move(-1)

    def move_down(self):
        self._move(1)

    def open_selected(self):
        item = CONTENT_ITEMS[self.selected_index]
        if not self._action_available(item[2]):
            self._open_feature(
                _("Not available"),
                _("Standard M3U sources support Live TV only."),
            )
            return
        if item[2] in ("live", "movie", "series"):
            from .browser import GTContentBrowserScreen

            self._open_dashboard_child(
                GTContentBrowserScreen,
                self.account,
                item[2],
                self._dashboard_content_client(),
            )
        elif item[2] == "catchup":
            from .catchup_ui import GTArchiveScreen

            self._open_dashboard_child(
                GTArchiveScreen, self.account, self._dashboard_content_client(),
            )
        elif item[2] == "continue":
            from .browser import GTContinueWatchingScreen

            self._open_dashboard_child(
                GTContinueWatchingScreen,
                self.account,
                self._dashboard_content_client(),
            )
        elif item[2] == "favorites":
            from .browser import GTFavoritesScreen

            self._open_dashboard_child(
                GTFavoritesScreen,
                self.account,
                self._dashboard_content_client(),
            )
        elif item[2] == "settings":
            self.open_settings()

    def _open_feature(self, title, message):
        self._open_dashboard_child(GTFeatureScreen, title, message)

    def open_settings(self):
        self._open_dashboard_child(GTPlayerSettingsScreen)

    def open_about(self):
        self._open_feature(
            _("About"),
            _about_text(),
        )


def _sanitized_account_preview(result, source_type):
    """Keep only bounded account-summary fields needed by the welcome card."""
    return ServerHealth(
        status=str(getattr(result, "status", "") or N_("Unknown")),
        account_status=str(getattr(result, "account_status", "") or ""),
        active_connections=getattr(result, "active_connections", None),
        max_connections=getattr(result, "max_connections", None),
        expiry=str(getattr(result, "expiry", "") or N_("Unknown")),
        source_type=source_type,
    )


def _check_account_preview(account, checker, timeout):
    """Run the selected-account summary check with one bounded network budget."""
    if checker is check_source_health:
        result = checker(account, timeout=timeout)
    else:
        # Tests and downstream integrations may provide the established
        # one-argument checker contract. The UI deadline still rejects a late
        # result even when that custom callable has no timeout parameter.
        result = checker(account)
    return _sanitized_account_preview(result, _source_type(account))


class GTIPTVPlayerProScreen(Screen):
    def __init__(self, session, account_loader=None, account_checker=None):
        self.skin = decorate_remote_footer(
            _welcome_skin(),
            WELCOME_FOOTER_ITEMS,
            mask_legacy_frame=True,
        )
        Screen.__init__(self, session)
        snapshot = device_snapshot()
        self.account_loader = account_loader or load_sources
        self.account_checker = account_checker or check_source_health
        self.load_result = None
        self.selected_account_index = 0
        self.selected_action = 0
        self._direct_connector = None
        self._child_screen_open = False
        self._account_preview_closed = False
        self._account_preview_generation = 0
        self._account_preview_job = None
        self._account_preview_result = None
        self._account_preview_result_ready = False
        self._account_preview_cache = {}
        self._account_preview_lock = threading.Lock()
        self._account_preview_debounce_timer = eTimer()
        _connect_timer(
            self._account_preview_debounce_timer,
            self._start_account_preview_check,
        )
        self._account_preview_poll_timer = eTimer()
        _connect_timer(
            self._account_preview_poll_timer,
            self._poll_account_preview_check,
        )

        attach_background(self, "welcome_bg", WELCOME_BACKGROUND)
        self["top_accent"] = Label("")
        self["brand_panel"] = Label("")
        self["brand_mark"] = Label("GT\nTV")
        self["header"] = Label("{}   v{}".format(PLUGIN_NAME, PLUGIN_VERSION))
        self["device_info"] = Label(
            "{}  |  {}  |  Python {}".format(
                snapshot["device"], snapshot["image"], snapshot["python"]
            )
        )
        self["eyebrow"] = Label(_("SECURE SOURCE CONNECTION"))
        self["welcome_title"] = Label(_("Welcome"))
        self["subtitle"] = Label(
            _("Select and connect to a saved source to start watching.")
        )
        self["right_panel"] = Label("")
        self["account_caption"] = Label(_("SAVED SOURCE"))
        self["account_card"] = Label("")
        self["account_status"] = Label("")
        for prefix in ("account_expiry", "account_connection"):
            self[prefix] = Label("")
            for signal_name in (
                HEALTH_SIGNAL_GREEN,
                HEALTH_SIGNAL_YELLOW,
                HEALTH_SIGNAL_RED,
            ):
                self["{}_{}".format(prefix, signal_name)] = Label("")
        self["account_accent"] = Label("")
        self["account_meta"] = Label("")
        self["connect_button"] = Label("")
        self["accounts_button"] = Label("")
        self["add_button"] = Label("")
        self["focus_0"] = Label("")
        self["focus_1"] = Label("")
        self["focus_2"] = Label("")
        for index in range(3):
            for edge in ("top", "left", "right"):
                self["focus_{}_{}".format(index, edge)] = Label("")
        self["hint"] = Label("")
        self["footer"] = Label("")
        install_remote_footer(self, WELCOME_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            [
                "OkCancelActions",
                "DirectionActions",
                "ColorActions",
                "MenuActions",
                "WizardActions",
            ],
            {
                "ok": self.open_selected,
                "cancel": self.close,
                "back": self.close,
                "left": self.previous_account,
                "right": self.next_account,
                "up": self.previous_action,
                "down": self.next_action,
                "green": self.open_accounts,
                "blue": self.connect_selected,
                "menu": self.open_settings,
                "red": self.close,
            },
            -1,
        )
        self.reload_accounts()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refresh)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop_direct_connection)
            self.onClose.append(self._stop_account_preview)
        self.setTitle("{} v{}".format(PLUGIN_NAME, PLUGIN_VERSION))

    @property
    def accounts(self):
        if self.load_result is None:
            return []
        return self.load_result.accounts

    def reload_accounts(self, *args):
        self._cancel_account_preview()
        self._account_preview_cache.clear()
        self.load_result = self.account_loader()
        if self.selected_account_index >= len(self.accounts):
            self.selected_account_index = max(0, len(self.accounts) - 1)
        self._refresh()
        self._schedule_account_preview_check()

    def _refresh(self):
        count = len(self.accounts)
        if count:
            account = self.accounts[self.selected_account_index]
            account_widget = self["account_card"]
            account_label = ellipsize_dynamic_text(
                account_widget,
                account.display_name,
                fallback_chars=32,
            )
            if (
                account.host
                and account.host != account.display_name
            ):
                endpoint = ellipsize_dynamic_text(
                    account_widget,
                    account.host,
                    fallback_chars=32,
                )
                account_label = "{}\n{}".format(account_label, endpoint)
            account_widget.setText(account_label)
            self["account_status"].setText(_("Ready to connect"))
            cached = self._account_preview_cache.get(id(account))
            if cached is not None:
                self._show_account_preview(cached)
            elif _source_type(account) == "m3u":
                self._show_unknown_account_preview()
            else:
                self._show_pending_account_preview()
            account_meta = "{} / {}  •  {}".format(
                self.selected_account_index + 1,
                count,
                _source_badge(account),
            )
            ellipsize_dynamic_text(
                self["account_meta"],
                account_meta,
                fallback_chars=13,
            )
            self["hint"].setText(
                _("Source status is checked automatically when connecting.")
            )
        else:
            self["account_card"].setText(
                _("No saved source found")
            )
            self["account_status"].setText(
                _("Create a new source from the ADD SOURCE panel.")
            )
            self._set_account_preview_line(
                "account_expiry", "", HEALTH_SIGNAL_NEUTRAL
            )
            self._set_account_preview_line(
                "account_connection", "", HEALTH_SIGNAL_NEUTRAL
            )
            self["account_meta"].setText("{}: 0".format(_("Sources")))
            self["hint"].setText(
                _("No saved source. Create one from the ADD SOURCE panel.")
            )

        self["connect_button"].setText(_("CONNECT SOURCE"))
        self["accounts_button"].setText(_("MANAGE SOURCES"))
        self["add_button"].setText(_("ADD SOURCE"))
        for index in range(3):
            if self.selected_action == index:
                self["focus_{}".format(index)].show()
                for edge in ("top", "left", "right"):
                    self["focus_{}_{}".format(index, edge)].show()
            else:
                self["focus_{}".format(index)].hide()
                for edge in ("top", "left", "right"):
                    self["focus_{}_{}".format(index, edge)].hide()

    def previous_account(self):
        if self.accounts:
            self.selected_account_index = (
                self.selected_account_index - 1
            ) % len(self.accounts)
            self._refresh()
            self._schedule_account_preview_check()

    def next_account(self):
        if self.accounts:
            self.selected_account_index = (
                self.selected_account_index + 1
            ) % len(self.accounts)
            self._refresh()
            self._schedule_account_preview_check()

    def previous_action(self):
        self.selected_action = (self.selected_action - 1) % 3
        self._refresh()

    def next_action(self):
        self.selected_action = (self.selected_action + 1) % 3
        self._refresh()

    def _set_account_preview_line(self, prefix, text, signal_name):
        signal_name = (
            signal_name
            if signal_name in (
                HEALTH_SIGNAL_GREEN,
                HEALTH_SIGNAL_YELLOW,
                HEALTH_SIGNAL_RED,
            )
            else HEALTH_SIGNAL_NEUTRAL
        )
        widgets = {
            HEALTH_SIGNAL_NEUTRAL: prefix,
            HEALTH_SIGNAL_GREEN: "{}_green".format(prefix),
            HEALTH_SIGNAL_YELLOW: "{}_yellow".format(prefix),
            HEALTH_SIGNAL_RED: "{}_red".format(prefix),
        }
        fitted = ellipsize_dynamic_text(
            self[widgets[HEALTH_SIGNAL_NEUTRAL]],
            str(text or ""),
            fallback_chars=52,
        )
        for current_signal, widget_name in widgets.items():
            widget = self[widget_name]
            widget.setText(fitted)
            if current_signal == signal_name:
                widget.show()
            else:
                widget.hide()

    def _show_pending_account_preview(self):
        pending = _("TESTING...")
        self._set_account_preview_line(
            "account_expiry",
            _("Expiry: {}").format(pending),
            HEALTH_SIGNAL_NEUTRAL,
        )
        self._set_account_preview_line(
            "account_connection",
            localized_connection_usage_line(pending),
            HEALTH_SIGNAL_NEUTRAL,
        )

    def _show_unknown_account_preview(self):
        unknown = _("Unknown")
        self._set_account_preview_line(
            "account_expiry",
            _("Expiry: {}").format(unknown),
            HEALTH_SIGNAL_NEUTRAL,
        )
        self._set_account_preview_line(
            "account_connection",
            localized_connection_usage_line(unknown),
            HEALTH_SIGNAL_NEUTRAL,
        )

    def _show_account_preview(self, result):
        expiry = str(getattr(result, "expiry", "") or N_("Unknown"))
        account_status = str(
            getattr(result, "account_status", "") or ""
        )
        expiry_signal = account_expiry_signal(expiry, account_status)
        connection_signal = account_connection_signal(
            getattr(result, "status", ""),
            account_status,
            getattr(result, "active_connections", None),
            getattr(result, "max_connections", None),
        )
        if expiry_signal == HEALTH_SIGNAL_RED:
            connection_signal = HEALTH_SIGNAL_RED
        expiry_text = _localized_runtime_text(expiry)
        if connection_signal == HEALTH_SIGNAL_RED:
            inactive_status = (
                N_("Off")
                if expiry_signal == HEALTH_SIGNAL_RED
                else account_status or getattr(result, "status", "")
            )
            inactive_text = _localized_runtime_text(inactive_status)
            connection_line = _("Account status: {}").format(
                inactive_text or _("Off")
            )
        else:
            connection_text = result.connection_text()
            if connection_text == N_("Unknown"):
                connection_text = _("Unknown")
            connection_line = localized_connection_usage_line(
                connection_text
            )
        self._set_account_preview_line(
            "account_expiry",
            _("Expiry: {}").format(expiry_text),
            expiry_signal,
        )
        self._set_account_preview_line(
            "account_connection",
            connection_line,
            connection_signal,
        )

    def _cancel_account_preview(self):
        self._account_preview_generation += 1
        _cancel_ui_job(self._account_preview_job)
        self._account_preview_job = None
        with self._account_preview_lock:
            self._account_preview_result = None
            self._account_preview_result_ready = False
        for timer in (
            self._account_preview_debounce_timer,
            self._account_preview_poll_timer,
        ):
            try:
                timer.stop()
            except Exception:
                pass

    def _schedule_account_preview_check(self):
        self._cancel_account_preview()
        if self._account_preview_closed or not self.accounts:
            return
        account = self.accounts[self.selected_account_index]
        if _source_type(account) == "m3u":
            self._show_unknown_account_preview()
            return
        if id(account) not in self._account_preview_cache:
            self._show_pending_account_preview()
        self._account_preview_debounce_timer.start(
            WELCOME_ACCOUNT_QUERY_DEBOUNCE_MS,
            True,
        )

    def _start_account_preview_check(self):
        if self._account_preview_closed or not self.accounts:
            return
        account = self.accounts[self.selected_account_index]
        if _source_type(account) == "m3u":
            self._show_unknown_account_preview()
            return
        generation = self._account_preview_generation
        job = _UIJobToken(WELCOME_ACCOUNT_QUERY_TIMEOUT_SECONDS)
        self._account_preview_job = job
        with self._account_preview_lock:
            self._account_preview_result = None
            self._account_preview_result_ready = False
        worker = threading.Thread(
            target=lambda: self._run_account_preview_check(
                account,
                generation,
                job,
            )
        )
        worker.daemon = True
        worker.start()
        self._account_preview_poll_timer.start(
            WELCOME_ACCOUNT_QUERY_POLL_MS,
            True,
        )

    def _run_account_preview_check(self, account, generation, job):
        try:
            job.check()
            result = _check_account_preview(
                account,
                self.account_checker,
                WELCOME_ACCOUNT_QUERY_TIMEOUT_SECONDS,
            )
        except Exception:
            result = ServerHealth(
                N_("Unknown"),
                expiry=N_("Unknown"),
                source_type=_source_type(account),
            )
        with self._account_preview_lock:
            if (
                self._account_preview_closed
                or generation != self._account_preview_generation
                or not job.active()
            ):
                return
            self._account_preview_result = (
                generation,
                id(account),
                result,
            )
            self._account_preview_result_ready = True

    def _poll_account_preview_check(self):
        if self._account_preview_closed:
            return
        with self._account_preview_lock:
            ready = self._account_preview_result_ready
            payload = self._account_preview_result
            if ready:
                self._account_preview_result = None
                self._account_preview_result_ready = False
        if not ready:
            job = self._account_preview_job
            if job is None:
                return
            if job.expired():
                _cancel_ui_job(job)
                self._account_preview_job = None
                self._show_unknown_account_preview()
                return
            self._account_preview_poll_timer.start(
                WELCOME_ACCOUNT_QUERY_POLL_MS,
                True,
            )
            return
        self._account_preview_job = None
        if not payload or not self.accounts:
            return
        generation, account_key, result = payload
        selected = self.accounts[self.selected_account_index]
        if (
            generation != self._account_preview_generation
            or account_key != id(selected)
        ):
            return
        self._account_preview_cache[account_key] = result
        self._show_account_preview(result)

    def _stop_account_preview(self):
        self._account_preview_closed = True
        self._cancel_account_preview()

    def open_selected(self):
        if self.selected_action == 0:
            if self.accounts:
                self.connect_selected()
            else:
                self.open_add_source()
            return
        if self.selected_action == 1:
            self.open_accounts()
            return
        self.open_add_source()

    def connect_selected(self):
        if not self.accounts:
            self.open_accounts()
            return
        if self._direct_connector is not None and self._direct_connector.active:
            self["hint"].setText(_("A source connection is already in progress."))
            return
        account = self.accounts[self.selected_account_index]
        self._start_connection(account)

    def _start_connection(self, account):
        self._cancel_account_preview()
        self._stop_direct_connection()
        self._direct_connector = _DirectAccountConnection(
            self.session,
            account,
            checker=self.account_checker,
            on_started=self._connection_started,
            on_success=self._connection_succeeded,
            on_failure=self._connection_failed,
            on_return=self.reload_accounts,
        )
        self._direct_connector.start()

    def _connection_started(self, account):
        self["account_status"].setText(_("CONNECTING..."))
        ellipsize_dynamic_text(
            self["hint"],
            _("Checking {}, please wait.").format(
                account.display_name
            ),
            fallback_chars=94,
        )

    def _connection_succeeded(self, account, result):
        del result
        self["account_status"].setText(_("CONNECTED"))
        ellipsize_dynamic_text(
            self["hint"],
            _("Connected to {}; opening the dashboard.").format(
                account.display_name
            ),
            fallback_chars=94,
        )

    def _connection_failed(self, account, result):
        ellipsize_dynamic_text(
            self["account_status"],
            _localized_runtime_text(result.status),
            fallback_chars=42,
        )
        ellipsize_dynamic_text(
            self["hint"],
            _("Could not connect to {}: {}").format(
                account.display_name,
                _localized_runtime_text(result.detail or result.status),
            ),
            fallback_chars=94,
        )

    def _stop_direct_connection(self):
        if self._direct_connector is not None:
            self._direct_connector.stop()

    def _set_remote_actions_enabled(self, enabled):
        action_map = self["actions"]
        set_enabled = getattr(action_map, "setEnabled", None)
        if set_enabled is not None:
            set_enabled(enabled)

    def _begin_child_screen(self):
        if self._child_screen_open:
            self["hint"].setText(_("Close the open screen before opening another one."))
            return False
        self._child_screen_open = True
        self._set_remote_actions_enabled(False)
        return True

    def _child_screen_closed(self, callback, *args):
        self._child_screen_open = False
        self._set_remote_actions_enabled(True)
        if callback is not None:
            callback(*args)

    def _open_child_screen(self, screen, callback=None, *args):
        if not self._begin_child_screen():
            return False
        try:
            if hasattr(self.session, "openWithCallback"):
                self.session.openWithCallback(
                    lambda *result: self._child_screen_closed(
                        callback,
                        *result
                    ),
                    screen,
                    *args
                )
            else:
                dialog = self.session.open(screen, *args)
                on_close = getattr(dialog, "onClose", None)
                if on_close is not None:
                    on_close.append(
                        lambda: self._child_screen_closed(callback)
                    )
                else:
                    self._child_screen_closed(None)
        except Exception:
            self._child_screen_closed(None)
            raise
        return True

    def open_accounts(self):
        self.selected_action = 1
        self._refresh()
        return self._open_child_screen(
            GTManageSourcesScreen,
            self.reload_accounts,
        )

    def open_add_source(self):
        self.selected_action = 2
        self._refresh()
        return self._open_child_screen(
            GTAddSourceTypeScreen,
            self._source_type_selected,
        )

    def open_add_account(self):
        """Compatibility entry point used by older screen integrations."""
        return self.open_add_source()

    def _source_type_selected(self, source_type=None):
        source_type = str(source_type or "").lower()
        if source_type == "xtream":
            screen = GTAddAccountScreen
        elif source_type == "m3u":
            screen = GTAddM3USourceScreen
        elif source_type == "stalker":
            screen = GTAddStalkerSourceScreen
        else:
            self.reload_accounts()
            return False
        return self._open_child_screen(
            screen,
            self.reload_accounts,
        )

    def open_settings(self):
        return self._open_child_screen(
            GTPlayerSettingsScreen,
            self.reload_accounts,
        )

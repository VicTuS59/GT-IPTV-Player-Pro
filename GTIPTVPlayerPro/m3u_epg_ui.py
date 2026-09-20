# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remote-control settings UI for optional M3U XMLTV guide sources.

This module only reads and writes the small settings sidecar.  XML parsing,
downloads and EPG-cache work belong to the background scheduler and are never
started directly by an Enigma2 screen callback.
"""

import datetime
import os
from urllib.parse import urlsplit

from Components.ActionMap import ActionMap
try:
    from Components.FileList import FileList
except ImportError:
    FileList = None
from Components.Label import Label
from Screens.Screen import Screen
from enigma import getDesktop

from .background import attach_background
from .i18n import N_, _, localized_upper
from .m3u_epg_settings import (
    Binding,
    get_m3u_epg_binding,
    save_m3u_epg_binding,
    update_m3u_epg_binding,
)
from .paths import plugin_path
from .remote_footer import (
    decorate_remote_footer,
    footer_item,
    install_remote_footer,
)
from .typography import clean_dynamic_text, ellipsize_dynamic_text


APP_BACKGROUND = plugin_path("skin", "images", "global-neon-v0912.png")
XMLTV_FILE_PATTERN = r"(?i)^.*(?:\.xml|\.xml\.gz|\.gz)$"
XMLTV_LOCAL_ROOTS = (
    "/etc/enigma2/gtiptvplayer",
    "/media",
    "/mnt",
)
XMLTV_BROWSER_LOCATIONS = (
    ("HDD", "/media/hdd"),
    ("USB", "/media/usb"),
    ("NAS", "/media/net"),
    ("AUTOFS", "/media/autofs"),
    ("ETC", "/etc/enigma2/gtiptvplayer"),
    ("MEDIA", "/media"),
    ("MNT", "/mnt"),
)
SUPPORTED_INTERVALS = (4, 6, 12, 24)
SUPPORTED_KINDS = ("file", "url")
SUPPORTED_UPDATE_MODES = ("manual", "automatic")
REFRESH_POLL_MILLISECONDS = 500
REFRESH_ACTIVE_STATES = frozenset(("queued", "running"))
REFRESH_TERMINAL_STATES = frozenset(("success", "failed", "cancelled"))

XMLTV_SETTINGS_FOOTER_ITEMS = (
    footer_item("arrows", "select_change", 1.15),
    footer_item("ok", "edit", 0.75),
    footer_item("green", "save", 0.8),
    footer_item("yellow", "refresh", 0.95),
    footer_item("exit", "back", 0.75),
)
XMLTV_BROWSER_FOOTER_ITEMS = (
    footer_item("arrows", "navigate", 1.05),
    footer_item("ok", "select", 0.75),
    footer_item("green", "select", 0.8),
    footer_item("blue", "storage", 0.9),
    footer_item("exit", "back", 0.75),
)


def _new_timer():
    try:
        from enigma import eTimer

        return eTimer()
    except (ImportError, RuntimeError):
        return None


def _connect_timer(timer, callback):
    if timer is None:
        return None
    try:
        connection = timer.timeout.connect(callback)
        return ("signal", connection, callback)
    except AttributeError:
        try:
            timer.callback.append(callback)
            return ("callback", None, callback)
        except AttributeError:
            return None


def _disconnect_timer(timer, binding):
    if timer is None or not binding:
        return
    mode, connection, callback = binding
    if mode == "signal":
        try:
            disconnect = getattr(connection, "disconnect", None)
            if callable(disconnect):
                disconnect()
                return
            disconnect = getattr(timer.timeout, "disconnect", None)
            if callable(disconnect):
                disconnect(callback)
        except Exception:
            pass
        return
    try:
        callbacks = timer.callback
        if callback in callbacks:
            callbacks.remove(callback)
    except (AttributeError, ValueError):
        pass


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


def _m3u_xmltv_settings_skin():
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    footer_y = height - px(92)
    rows = []
    for index in range(7):
        row_y = 155 + (index * 88)
        rows.append(
            """
    <widget name="row_bg_{index}" position="{margin},{row_y}"
            size="{content_width},{row_h}" font="Regular;1"
            backgroundColor="#0D1627" transparent="0" zPosition="1" />
    <widget name="focus_{index}" position="{margin},{focus_y}"
            size="{content_width},{focus_h}" font="Regular;1"
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
                row_y=px(row_y),
                row_h=px(68),
                focus_y=px(row_y + 64),
                focus_h=px(4),
                label_x=margin + px(30),
                label_w=px(650),
                label_font=px(24),
                value_x=margin + px(700),
                value_w=content_width - px(730),
                value_font=px(23),
            )
        )
    return """
<screen name="GTM3UXMLTVSettingsScreen" position="0,0"
        size="{width},{height}" flags="wfNoBorder"
        backgroundColor="#050914">
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
        header_y=px(20),
        header_h=px(55),
        title_font=px(35),
        subtitle_y=px(83),
        subtitle_h=px(45),
        subtitle_font=px(21),
        rows="".join(rows),
        message_y=px(788),
        message_h=px(155),
        message_font=px(20),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _m3u_xmltv_browser_skin():
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    footer_y = height - px(92)
    return """
<screen name="GTM3UXMLTVFileBrowserScreen" position="0,0"
        size="{width},{height}" flags="wfNoBorder"
        backgroundColor="#050914">
    <widget name="app_bg" position="0,0" size="{width},{height}"
            zPosition="0" />
    <widget name="top_accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />
    <widget name="header" position="{margin},{header_y}"
            size="{content_width},{header_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1"
            valign="center" halign="left" />
    <widget name="instruction" position="{margin},{instruction_y}"
            size="{content_width},{instruction_h}"
            font="Regular;{instruction_font}" foregroundColor="#22D3EE"
            transparent="1" valign="center" halign="left" />
    <widget name="path_panel" position="{margin},{path_y}"
            size="{content_width},{path_h}" font="Regular;1"
            backgroundColor="#0D1627" transparent="0" zPosition="1" />
    <widget name="path" position="{path_x},{path_y}"
            size="{path_w},{path_h}" font="Regular;{path_font}"
            foregroundColor="#C8D2E3" transparent="1" zPosition="2"
            valign="center" halign="left" />
    <widget name="filelist" position="{margin},{list_y}"
            size="{content_width},{list_h}" font="Regular;{list_font}"
            scrollbarMode="showOnDemand"
            foregroundColor="#FFFFFF" backgroundColor="#0D1627"
            transparent="0" zPosition="2" />
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
        header_y=px(25),
        header_h=px(60),
        title_font=px(36),
        instruction_y=px(90),
        instruction_h=px(45),
        instruction_font=px(21),
        path_y=px(145),
        path_h=px(60),
        path_x=margin + px(24),
        path_w=content_width - px(48),
        path_font=px(20),
        list_y=px(225),
        list_h=px(620),
        list_font=px(23),
        message_y=px(858),
        message_h=px(72),
        message_font=px(19),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _safe_int(value, fallback=0):
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return int(fallback)


def _source_id(source):
    return str(
        getattr(source, "source_id", "")
        or getattr(source, "id", "")
        or ""
    ).strip()


def _source_name(source):
    value = str(
        getattr(source, "display_name", "")
        or getattr(source, "name", "")
        or _("M3U source")
    )
    value = " ".join(value.replace("\x00", " ").split())[:96]
    return _redacted_web_endpoint(value) or value


def _normalise_kind(value):
    return "url" if str(value or "").strip().lower() == "url" else "file"


def _normalise_update_mode(value):
    value = str(value or "").strip().lower()
    return value if value in SUPPORTED_UPDATE_MODES else "automatic"


def _normalise_interval(value):
    value = _safe_int(value, 24)
    return value if value in SUPPORTED_INTERVALS else 24


def _binding_snapshot(binding, source_id):
    """Return a validated copy without carrying arbitrary object attributes."""
    binding = binding if binding is not None else Binding(source_id)
    return Binding(
        source_id=source_id,
        enabled=bool(getattr(binding, "enabled", False)),
        kind=_normalise_kind(getattr(binding, "kind", "file")),
        location=str(getattr(binding, "location", "") or "").strip(),
        update_mode=_normalise_update_mode(
            getattr(binding, "update_mode", "automatic")
        ),
        interval_hours=_normalise_interval(
            getattr(binding, "interval_hours", 24)
        ),
        last_attempt_utc=max(
            0, _safe_int(getattr(binding, "last_attempt_utc", 0), 0)
        ),
        last_success_utc=max(
            0, _safe_int(getattr(binding, "last_success_utc", 0), 0)
        ),
        last_error_code=str(
            getattr(binding, "last_error_code", "") or ""
        ).strip(),
        last_channel_count=max(
            0, _safe_int(getattr(binding, "last_channel_count", 0), 0)
        ),
        last_event_count=max(
            0, _safe_int(getattr(binding, "last_event_count", 0), 0)
        ),
    )


def _xmltv_file_extension(path):
    value = str(path or "").lower()
    return value.endswith((".xml", ".xml.gz", ".gz"))


def _path_within_xmltv_roots(path, roots=XMLTV_LOCAL_ROOTS):
    """Accept only canonical absolute paths inside the XMLTV allowlist."""
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


def _valid_local_xmltv_path(path, require_file=False):
    """Validate a file endpoint without requiring an offline mount to exist."""
    path = str(path or "").strip()
    if not _xmltv_file_extension(path) or not _path_within_xmltv_roots(path):
        return False
    if os.path.lexists(path):
        if os.path.islink(path) or not os.path.isfile(path):
            return False
    elif require_file:
        return False
    return True


def _xmltv_browser_directory(location, roots=XMLTV_LOCAL_ROOTS):
    value = str(location or "").strip()
    if value and os.path.isabs(value):
        absolute = os.path.abspath(value)
        directory = absolute if os.path.isdir(absolute) else os.path.dirname(absolute)
        if os.path.isdir(directory) and _path_within_xmltv_roots(directory, roots):
            return os.path.join(directory, "")
    for root in (roots if not isinstance(roots, (str, os.PathLike)) else (roots,)):
        directory = os.path.abspath(str(root))
        if os.path.isdir(directory) and _path_within_xmltv_roots(directory, roots):
            return os.path.join(directory, "")
    return None


def _xmltv_file_list_selection(file_list):
    """Normalize FileList results across OpenPLi and OpenATV versions."""
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


def _xmltv_storage_locations(partitions=None, is_directory=None):
    is_directory = is_directory or os.path.isdir
    locations = []
    seen = set()

    def add_location(label, path):
        path = os.path.normpath(str(path or ""))
        if (
            path in seen
            or not is_directory(path)
            or not _path_within_xmltv_roots(path)
        ):
            return
        seen.add(path)
        locations.append(("{}  •  {}".format(label, path), path))

    for label, path in XMLTV_BROWSER_LOCATIONS:
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


def _redacted_web_endpoint(location):
    """Return scheme and host only; never expose credentials, path or query."""
    try:
        parsed = urlsplit(str(location or "").strip())
        scheme = parsed.scheme.lower()
        host = parsed.hostname or ""
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    if scheme not in ("http", "https") or not host:
        return ""
    if ":" in host and not host.startswith("["):
        host = "[{}]".format(host)
    default_port = 443 if scheme == "https" else 80
    if port and port != default_port:
        host = "{}:{}".format(host, port)
    return "{}://{}".format(scheme, host)


def _binding_endpoint(binding):
    if _normalise_kind(getattr(binding, "kind", "file")) == "file":
        return str(getattr(binding, "location", "") or "").strip()
    endpoint = str(getattr(binding, "display_endpoint", "") or "").strip()
    return _redacted_web_endpoint(endpoint) or _redacted_web_endpoint(
        getattr(binding, "location", "")
    )


def _utc_text(timestamp):
    timestamp = max(0, _safe_int(timestamp, 0))
    if not timestamp:
        return "-"
    try:
        value = datetime.datetime.fromtimestamp(timestamp, datetime.timezone.utc)
        return value.strftime("%Y-%m-%d %H:%M UTC")
    except (OverflowError, OSError, ValueError):
        return "-"


def _status_text(binding, refresh_state=None):
    state = (
        str(refresh_state.get("state", ""))
        if isinstance(refresh_state, dict)
        else ""
    )
    if state in REFRESH_ACTIVE_STATES:
        return _("Background update requested")
    if state == "cancelled":
        return _("Request cancelled")
    error_code = str(getattr(binding, "last_error_code", "") or "").strip()
    attempt = max(0, _safe_int(getattr(binding, "last_attempt_utc", 0), 0))
    success = max(0, _safe_int(getattr(binding, "last_success_utc", 0), 0))
    if error_code and attempt >= success:
        return _("Update failed; the previous guide remains available.")
    if success:
        return _("Completed: {} channels, {} events").format(
            max(0, _safe_int(getattr(binding, "last_channel_count", 0), 0)),
            max(0, _safe_int(getattr(binding, "last_event_count", 0), 0)),
        )
    if attempt:
        return _("Background update requested")
    return _("No update has been completed yet.")


def _notify_settings_changed():
    """Wake the scheduler lazily without coupling screen import to runtime."""
    try:
        from . import m3u_epg_scheduler

        callback = getattr(m3u_epg_scheduler, "notify_settings_changed", None)
        if callable(callback):
            callback()
    except Exception:
        pass


def _request_refresh(source_id):
    """Queue work by stable ID; downloads never run in the UI module."""
    try:
        from . import m3u_epg_scheduler

        callback = getattr(m3u_epg_scheduler, "request_refresh", None)
        if not callable(callback):
            return False
        return callback(source_id) is not False
    except Exception:
        return False


def _get_refresh_state(source_id):
    """Read a scheduler-owned primitive snapshot without doing worker I/O."""
    try:
        from . import m3u_epg_scheduler

        callback = getattr(m3u_epg_scheduler, "get_refresh_state", None)
        if not callable(callback):
            return None
        value = callback(source_id)
    except Exception:
        return None
    if not isinstance(value, dict):
        return None
    state = str(value.get("state", "") or "")
    if state not in REFRESH_ACTIVE_STATES.union(REFRESH_TERMINAL_STATES):
        return None
    try:
        request_id = max(0, int(value.get("request_id", 0) or 0))
        completed_at = max(0, int(value.get("completed_at", 0) or 0))
    except (TypeError, ValueError, OverflowError):
        return None
    return {
        "request_id": request_id,
        "state": state,
        "error_code": str(value.get("error_code", "") or "")[:48],
        "completed_at": completed_at,
    }


class GTM3UXMLTVFileBrowserScreen(Screen):
    """Select an XMLTV file from local or already mounted storage."""

    def __init__(self, session, current_path=""):
        self.skin = decorate_remote_footer(
            _m3u_xmltv_browser_skin(),
            XMLTV_BROWSER_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(localized_upper(_("XMLTV source")))
        self["instruction"] = Label(
            _("Choose an XML, XML.GZ or GZ file from local or mounted storage.")
        )
        self["path_panel"] = Label("")
        self["path"] = Label("")
        self["message"] = Label("HDD  •  USB  •  NAS  •  /media  •  /mnt")
        if FileList is None:
            raise RuntimeError("Enigma2 FileList is unavailable")
        self["filelist"] = FileList(
            _xmltv_browser_directory(current_path),
            showDirectories=True,
            showFiles=True,
            showMountpoints=True,
            matchingPattern=XMLTV_FILE_PATTERN,
            useServiceRef=False,
            inhibitDirs=False,
            inhibitMounts=False,
            isTop=False,
            enableWrapAround=False,
        )
        self["footer"] = Label("")
        install_remote_footer(self, XMLTV_BROWSER_FOOTER_ITEMS)
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
        self.setTitle(_("XMLTV source"))

    def _refresh_path(self):
        current = self["filelist"].getCurrentDirectory()
        if current:
            ellipsize_dynamic_text(
                self["path"],
                current,
                fallback_chars=122,
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
            previous = self["filelist"].getCurrentDirectory()
            self["filelist"].descent()
            current = self["filelist"].getCurrentDirectory()
            if not _path_within_xmltv_roots(current):
                restore = (
                    previous
                    if previous and _path_within_xmltv_roots(previous)
                    else _xmltv_browser_directory("")
                )
                if restore is not None:
                    self["filelist"].changeDir(restore)
                self["message"].setText(_("This folder is outside the allowed storage locations."))
            self._refresh_path()
            return
        self._select_file()

    def select_current(self):
        self.open_current()

    def show_storage(self):
        locations = _xmltv_storage_locations()
        try:
            from Screens.ChoiceBox import ChoiceBox
        except ImportError:
            ChoiceBox = None
        if (
            ChoiceBox is None
            or not locations
            or not hasattr(self.session, "openWithCallback")
        ):
            self["message"].setText(_("No allowed storage location is available."))
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
        directory = _xmltv_browser_directory(selection[1])
        if directory is None:
            self["message"].setText(_("XMLTV file path is invalid."))
            return
        self["filelist"].changeDir(directory)
        self._refresh_path()

    def _select_file(self):
        path = _xmltv_file_list_selection(self["filelist"])
        if not _valid_local_xmltv_path(path, require_file=True):
            self["message"].setText(_("XMLTV file path is invalid."))
            return
        self.close(path)

    def cancel(self):
        self.close(None)


class GTM3UXMLTVSettingsScreen(Screen):
    """Configure one external XMLTV binding for an existing M3U source."""

    LABELS = (
        N_("Enable external XMLTV EPG"),
        N_("Source type"),
        N_("XMLTV source"),
        N_("Update mode"),
        N_("Update interval"),
        N_("Last update"),
        N_("Status"),
    )

    def __init__(
        self,
        session,
        source,
        settings_loader=None,
        settings_saver=None,
    ):
        self.skin = decorate_remote_footer(
            _m3u_xmltv_settings_skin(),
            XMLTV_SETTINGS_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        self.source = source
        self.source_id = _source_id(source)
        self._uses_default_persistence = (
            settings_loader is None and settings_saver is None
        )
        self.settings_loader = settings_loader or get_m3u_epg_binding
        self.settings_saver = settings_saver or save_m3u_epg_binding
        self.selected_index = 0
        self._editing_location = False
        self._closed = False
        self._refresh_polling = False
        self._refresh_request_id = 0
        self._refresh_runtime_state = None
        self._refresh_timer = _new_timer()
        self._refresh_timer_binding = _connect_timer(
            self._refresh_timer,
            self._poll_refresh,
        )
        load_failed = False
        try:
            loaded = self.settings_loader(self.source_id)
        except Exception:
            loaded = None
            load_failed = True
        self.binding = _binding_snapshot(loaded, self.source_id)
        self._saved_binding = _binding_snapshot(self.binding, self.source_id)

        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(_("External XMLTV EPG"))
        self["subtitle"] = Label("")
        self._fit_source_subtitle()
        for index, label in enumerate(self.LABELS):
            self["row_bg_{}".format(index)] = Label("")
            self["focus_{}".format(index)] = Label("")
            self["label_{}".format(index)] = Label(_(label))
            self["value_{}".format(index)] = Label("")
        self["message"] = Label(
            _("XMLTV settings could not be read; safe defaults are shown.")
            if load_failed
            else _("Updates run in the background. Channel changes never download EPG data.")
        )
        self["footer"] = Label("")
        install_remote_footer(self, XMLTV_SETTINGS_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.activate_selected,
                "cancel": self.close_screen,
                "red": self.close_screen,
                "green": self.save,
                "yellow": self.request_refresh,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.change_left,
                "right": self.change_right,
            },
            -1,
        )
        self._refresh()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._fit_source_subtitle)
            self.onLayoutFinish.append(self._refresh)
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop_refresh_tracking)
        self.setTitle(_("External XMLTV EPG"))

    def _fit_source_subtitle(self):
        ellipsize_dynamic_text(
            self["subtitle"],
            "M3U  •  {}".format(
                clean_dynamic_text(_source_name(self.source))
            ),
            fallback_chars=112,
        )

    def _values(self):
        automatic = self.binding.update_mode == "automatic"
        endpoint = _binding_endpoint(self.binding) or _("Not configured")
        return (
            _("On") if self.binding.enabled else _("Off"),
            _("Local / NAS") if self.binding.kind == "file" else _("Web URL"),
            endpoint,
            _("Automatic") if automatic else _("Manual"),
            _("{} hours").format(self.binding.interval_hours) if automatic else "-",
            _utc_text(self.binding.last_success_utc),
            _status_text(self.binding, self._refresh_runtime_state),
        )

    def _refresh(self):
        has_dynamic_endpoint = bool(_binding_endpoint(self.binding))
        for index, value in enumerate(self._values()):
            widget = self["value_{}".format(index)]
            if index == 2 and has_dynamic_endpoint:
                ellipsize_dynamic_text(
                    widget,
                    value,
                    fallback_chars=50,
                )
            else:
                widget.setText(str(value))
            if index == self.selected_index:
                self["focus_{}".format(index)].show()
            else:
                self["focus_{}".format(index)].hide()

    def move_up(self):
        self.selected_index = (self.selected_index - 1) % len(self.LABELS)
        self._refresh()

    def move_down(self):
        self.selected_index = (self.selected_index + 1) % len(self.LABELS)
        self._refresh()

    def _replace_preferences(self, **changes):
        values = {
            "enabled": self.binding.enabled,
            "kind": self.binding.kind,
            "location": self.binding.location,
            "update_mode": self.binding.update_mode,
            "interval_hours": self.binding.interval_hours,
        }
        values.update(changes)
        endpoint_changed = (
            values["kind"] != self.binding.kind
            or values["location"] != self.binding.location
        )
        self.binding = Binding(
            source_id=self.source_id,
            enabled=values["enabled"],
            kind=values["kind"],
            location=values["location"],
            update_mode=values["update_mode"],
            interval_hours=values["interval_hours"],
            last_attempt_utc=(0 if endpoint_changed else self.binding.last_attempt_utc),
            last_success_utc=(0 if endpoint_changed else self.binding.last_success_utc),
            last_error_code=("" if endpoint_changed else self.binding.last_error_code),
            last_channel_count=(0 if endpoint_changed else self.binding.last_channel_count),
            last_event_count=(0 if endpoint_changed else self.binding.last_event_count),
        )

    def _change(self, step):
        if self.selected_index == 0:
            if not self.binding.enabled and not self.binding.location:
                self["message"].setText(
                    _("Choose an XMLTV source before enabling this option.")
                )
                return
            self._replace_preferences(enabled=not self.binding.enabled)
        elif self.selected_index == 1:
            kind = "url" if self.binding.kind == "file" else "file"
            location = self.binding.location
            if kind == "file" and _redacted_web_endpoint(location):
                location = ""
            elif kind == "url" and location and not _redacted_web_endpoint(location):
                location = ""
            self._replace_preferences(
                enabled=self.binding.enabled if location else False,
                kind=kind,
                location=location,
            )
        elif self.selected_index == 2:
            self.edit_location()
            return
        elif self.selected_index == 3:
            mode = "manual" if self.binding.update_mode == "automatic" else "automatic"
            self._replace_preferences(update_mode=mode)
        elif self.selected_index == 4:
            if self.binding.update_mode != "automatic":
                self["message"].setText(
                    _("Switch Update mode to Automatic to change this option.")
                )
                return
            try:
                index = SUPPORTED_INTERVALS.index(self.binding.interval_hours)
            except ValueError:
                index = len(SUPPORTED_INTERVALS) - 1
            self._replace_preferences(
                interval_hours=SUPPORTED_INTERVALS[
                    (index + step) % len(SUPPORTED_INTERVALS)
                ]
            )
        else:
            self["message"].setText(_("Status information is read-only."))
            return
        self["message"].setText(_("Press GREEN to save the changes."))
        self._refresh()

    def change_left(self):
        self._change(-1)

    def change_right(self):
        self._change(1)

    def activate_selected(self):
        if self.selected_index == 2:
            self.edit_location()
        else:
            self._change(1)

    def edit_location(self):
        if self.binding.kind == "file":
            if FileList is None or not hasattr(self.session, "openWithCallback"):
                self["message"].setText(_("The XMLTV file browser is unavailable."))
                return
            self.session.openWithCallback(
                self._file_selected,
                GTM3UXMLTVFileBrowserScreen,
                current_path=self.binding.location,
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
        self._editing_location = True
        self.session.openWithCallback(
            self._url_entered,
            VirtualKeyBoard,
            title=_("Enter the XMLTV HTTP(S) URL"),
            text=self.binding.location,
        )

    def _file_selected(self, value):
        if value is None:
            self["message"].setText(_("File selection cancelled."))
            return
        path = str(value or "").strip()
        if not _valid_local_xmltv_path(path, require_file=True):
            self["message"].setText(_("XMLTV file path is invalid."))
            return
        try:
            self._replace_preferences(location=path)
        except (TypeError, ValueError):
            self["message"].setText(_("XMLTV file path is invalid."))
            return
        self["message"].setText(_("Press GREEN to save the changes."))
        self._refresh()

    def _url_entered(self, value):
        self._editing_location = False
        if value is None:
            self["message"].setText(_("Field entry cancelled."))
            return
        location = str(value or "").strip()
        if not _redacted_web_endpoint(location):
            self["message"].setText(
                _("XMLTV URL must start with http:// or https://.")
            )
            return
        try:
            self._replace_preferences(location=location)
        except (TypeError, ValueError):
            self["message"].setText(_("XMLTV URL is invalid."))
            return
        self["message"].setText(_("Press GREEN to save the changes."))
        self._refresh()

    def _validate_preferences(self):
        location = str(self.binding.location or "").strip()
        if not location:
            if self.binding.enabled:
                self["message"].setText(_("Choose an XMLTV source before saving."))
                return False
            return True
        if self.binding.kind == "file":
            # Existing removable/NAS sources remain configurable while their
            # mount is offline.  If a filesystem object is present it must be
            # a canonical regular file and may never be a symbolic link.
            valid = _valid_local_xmltv_path(location, require_file=False)
            if not valid:
                self["message"].setText(_("XMLTV file path is invalid."))
                return False
        elif not _redacted_web_endpoint(location):
            self["message"].setText(
                _("XMLTV URL must start with http:// or https://.")
            )
            return False
        return True

    def _save_preferences(self):
        if not self._validate_preferences():
            return False
        candidate = _binding_snapshot(self.binding, self.source_id)
        try:
            if self._uses_default_persistence:
                def merge_preferences(current):
                    endpoint_changed = (
                        current.kind != candidate.kind
                        or current.location != candidate.location
                    )
                    patch = {
                        "enabled": candidate.enabled,
                        "kind": candidate.kind,
                        "location": candidate.location,
                        "update_mode": candidate.update_mode,
                        "interval_hours": candidate.interval_hours,
                    }
                    if endpoint_changed:
                        patch.update(
                            {
                                "last_attempt_utc": 0,
                                "last_success_utc": 0,
                                "last_error_code": "",
                                "last_channel_count": 0,
                                "last_event_count": 0,
                            }
                        )
                    return patch

                saved = update_m3u_epg_binding(
                    self.source_id,
                    merge_preferences,
                )
                if saved is None:
                    saved = save_m3u_epg_binding(candidate)
            else:
                saved = self.settings_saver(candidate)
            if saved is False:
                raise OSError("settings save failed")
            if isinstance(saved, Binding):
                candidate = _binding_snapshot(saved, self.source_id)
        except Exception:
            self["message"].setText(
                _("XMLTV settings could not be saved. Check the receiver storage.")
            )
            return False
        self.binding = candidate
        self._saved_binding = _binding_snapshot(candidate, self.source_id)
        _notify_settings_changed()
        return True

    def save(self):
        if self._save_preferences():
            self["message"].setText(_("External XMLTV EPG settings saved."))
            self._refresh()

    def _arm_refresh_poll(self):
        if (
            self._closed
            or not self._refresh_polling
            or self._refresh_timer is None
        ):
            return
        try:
            self._refresh_timer.start(REFRESH_POLL_MILLISECONDS, True)
        except Exception:
            self._refresh_polling = False

    def _reload_completed_status(self):
        try:
            loaded = self.settings_loader(self.source_id)
            persisted = _binding_snapshot(loaded, self.source_id)
        except Exception:
            return False
        same_endpoint = bool(
            persisted.kind == self.binding.kind
            and persisted.location == self.binding.location
        )
        self._saved_binding = _binding_snapshot(persisted, self.source_id)
        if not same_endpoint:
            return False
        current = self.binding
        self.binding = Binding(
            source_id=self.source_id,
            enabled=current.enabled,
            kind=current.kind,
            location=current.location,
            update_mode=current.update_mode,
            interval_hours=current.interval_hours,
            last_attempt_utc=persisted.last_attempt_utc,
            last_success_utc=persisted.last_success_utc,
            last_error_code=persisted.last_error_code,
            last_channel_count=persisted.last_channel_count,
            last_event_count=persisted.last_event_count,
        )
        return True

    def _apply_refresh_state(self, state):
        if self._closed or not isinstance(state, dict):
            return
        self._refresh_runtime_state = dict(state)
        phase = state.get("state", "")
        if phase in REFRESH_ACTIVE_STATES:
            self._refresh_polling = True
            self["message"].setText(_("Background update requested"))
            self._refresh()
            self._arm_refresh_poll()
            return
        self._refresh_polling = False
        if self._refresh_timer is not None:
            try:
                self._refresh_timer.stop()
            except Exception:
                pass
        self._reload_completed_status()
        self._refresh_runtime_state = dict(state)
        self._refresh()
        if phase == "success":
            self["message"].setText(_status_text(self.binding))
        elif phase == "cancelled":
            self["message"].setText(_("Request cancelled"))
        else:
            self["message"].setText(
                _("Update failed; the previous guide remains available.")
            )
        self._refresh_runtime_state = None

    def _poll_refresh(self):
        if self._closed or not self._refresh_polling:
            return
        state = _get_refresh_state(self.source_id)
        if state is None:
            self._arm_refresh_poll()
            return
        request_id = int(state.get("request_id", 0) or 0)
        if self._refresh_request_id <= 0:
            self._refresh_request_id = request_id
        elif request_id != self._refresh_request_id:
            # The scheduler exposes only the latest request per source.  A
            # newer request therefore supersedes the one first observed by
            # this screen; adopt it instead of waiting for an ID that cannot
            # reappear.
            self._refresh_request_id = request_id
        self._apply_refresh_state(state)

    def _stop_refresh_tracking(self):
        if self._closed:
            return
        self._closed = True
        self._refresh_polling = False
        timer = self._refresh_timer
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        _disconnect_timer(timer, self._refresh_timer_binding)
        self._refresh_timer_binding = None
        self._refresh_timer = None

    def request_refresh(self):
        if not self.binding.enabled:
            self["message"].setText(
                _("Enable external XMLTV EPG before requesting an update.")
            )
            return
        state = _get_refresh_state(self.source_id)
        if (
            isinstance(state, dict)
            and state.get("state") in REFRESH_ACTIVE_STATES
        ):
            # A second YELLOW press only attaches to the active request.
            # Saving here would emit a settings-change notification and
            # cancel the very refresh the screen is trying to observe.
            self._refresh_request_id = int(state.get("request_id", 0) or 0)
            self._refresh_polling = True
            self._refresh_runtime_state = dict(state)
            self["message"].setText(
                _("The XMLTV update was queued in the background.")
            )
            self._refresh()
            self._arm_refresh_poll()
            return
        if not self._save_preferences():
            return
        accepted = _request_refresh(self.source_id)
        state = _get_refresh_state(self.source_id)
        if accepted or (
            isinstance(state, dict)
            and state.get("state") in REFRESH_ACTIVE_STATES
        ):
            self._refresh_request_id = (
                int(state.get("request_id", 0) or 0)
                if isinstance(state, dict)
                else 0
            )
            self._refresh_polling = True
            self._refresh_runtime_state = state or {
                "request_id": 0,
                "state": "queued",
                "error_code": "",
                "completed_at": 0,
            }
            self["message"].setText(
                _("The XMLTV update was queued in the background.")
            )
            self._refresh()
            if state is not None and state.get("state") in REFRESH_TERMINAL_STATES:
                self._apply_refresh_state(state)
            else:
                self._arm_refresh_poll()
        else:
            self["message"].setText(
                _("The XMLTV update could not be queued. Try again shortly.")
            )

    def close_screen(self):
        self._stop_refresh_tracking()
        self.close(self._saved_binding)

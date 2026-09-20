# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remote-control UI for Xtream XMLTV -> native DVB EPG imports.

The core module deliberately has no Enigma2 dependency.  This module keeps
all network/XML work on daemon workers and applies the already validated
event tuples to ``eEPGCache`` only from the GUI timer callback.
"""

import datetime
import os
import threading
import time

from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen
from enigma import eTimer, getDesktop

from .background import attach_background
from .dvb_epg import (
    DEFAULT_IMPORT_PAST_SECONDS,
    DvbEpgError,
    MappingStoreError,
    OperationCancelled,
    block_automatic_mapping,
    build_import_event_groups,
    default_xmltv_cache_path,
    download_xtream_xmltv,
    list_dvb_bouquets,
    load_dvb_services,
    load_manual_mappings,
    match_xmltv_channels,
    normalise_channel_name,
    remove_manual_mapping,
    scan_xmltv,
    set_manual_mapping,
    xtream_source_key,
)
from .dvb_epg_settings import (
    ALL_BOUQUETS,
    DVBEPGSettings,
    SUPPORTED_EPG_DAYS,
    load_dvb_epg_settings,
    save_dvb_epg_settings,
)
from .i18n import N_, _
from .paths import plugin_path
from .playlist import load_sources
from .remote_footer import (
    decorate_remote_footer,
    footer_item,
    install_remote_footer,
)
from .scrollbar import hide_scrollbar, update_scrollbar
from .typography import clean_dynamic_text, ellipsize_dynamic_text, fit_dynamic_text


APP_BACKGROUND = plugin_path("skin", "images", "global-neon-v0912.png")
UI_JOB_TIMEOUT_SECONDS = 180
MAPPING_PAGE_SIZE = 8
PREFERENCE_FIELDS = (
    "enabled",
    "source_key",
    "bouquet_file",
    "epg_days",
    "update_mode",
    "update_interval_hours",
    "update_on_startup",
    "update_in_standby",
)
PIPELINE_PREFERENCE_FIELDS = ("source_key", "bouquet_file", "epg_days")
DEFAULT_SETTINGS_LOADER = load_dvb_epg_settings
DEFAULT_SETTINGS_SAVER = save_dvb_epg_settings
_SETTINGS_FALLBACK_LOCK = threading.RLock()

DVB_SETTINGS_FOOTER_ITEMS = (
    footer_item("arrows", "select_change", 1.2),
    footer_item("ok", "open", 0.75),
    footer_item("green", "save", 0.8),
    footer_item("yellow", "refresh", 0.95),
    footer_item("blue", "channel_list", 1.15),
    footer_item("exit", "back", 0.75),
)
DVB_AUTOMATION_FOOTER_ITEMS = (
    footer_item("arrows", "select_change", 1.2),
    footer_item("green", "save", 0.8),
    footer_item("exit", "back", 0.75),
)
DVB_MAPPING_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "edit"),
    footer_item("red", "change"),
    footer_item("yellow", "refresh"),
    footer_item("exit", "back"),
)
XMLTV_PICKER_FOOTER_ITEMS = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "select"),
    footer_item("green", "search"),
    footer_item("yellow", "clear_search", 1.2),
    footer_item("exit", "back"),
)


class _UIJobToken(object):
    def __init__(self, timeout=UI_JOB_TIMEOUT_SECONDS):
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
            raise OperationCancelled("cancelled")


def _cancel_job(job):
    if job is not None:
        try:
            job.cancel()
        except Exception:
            pass


def _connect_timer(timer, callback):
    try:
        connection = timer.timeout.connect(callback)
        return ("signal", connection, callback)
    except AttributeError:
        timer.callback.append(callback)
        return ("callback", None, callback)


def _disconnect_timer(timer, binding):
    if not binding:
        return
    mode, connection, callback = binding
    if mode == "signal":
        try:
            disconnector = getattr(connection, "disconnect", None)
            if callable(disconnector):
                disconnector()
                return
            disconnector = getattr(timer.timeout, "disconnect", None)
            if callable(disconnector):
                disconnector(callback)
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


def _settings_skin(
    screen_name="GTDVBEPGSettingsScreen",
    row_positions=(190, 290, 390, 490, 590, 690),
    message_position=790,
):
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    footer_y = height - px(92)
    rows = []
    for index, y_value in enumerate(row_positions):
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
                row_y=px(y_value),
                row_h=px(76),
                focus_y=px(y_value + 72),
                focus_h=px(4),
                label_x=margin + px(32),
                label_w=px(760),
                label_font=px(25),
                value_x=margin + px(820),
                value_w=content_width - px(852),
                value_font=px(24),
            )
        )
    return """
<screen name="{screen_name}" position="0,0"
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
        screen_name=screen_name,
        accent_h=px(5),
        margin=margin,
        content_width=content_width,
        header_y=px(25),
        header_h=px(65),
        title_font=px(38),
        subtitle_y=px(96),
        subtitle_h=px(55),
        subtitle_font=px(22),
        rows="".join(rows),
        message_y=px(message_position),
        message_h=px(120),
        message_font=px(20),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _list_skin(screen_name):
    width, height, px = _scale()
    margin = px(64)
    content_width = width - (margin * 2)
    row_width = content_width - px(24)
    footer_y = height - px(92)
    rows = []
    for index in range(MAPPING_PAGE_SIZE):
        row_y = 190 + (index * 82)
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
    <widget name="row_{index}" position="{row_x},{row_y}"
            size="{text_w},{row_h}" font="Regular;{row_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="4"
            valign="center" halign="left" noWrap="1" />""".format(
                index=index,
                margin=margin,
                row_y=px(row_y),
                row_width=row_width,
                row_h=px(70),
                marker_w=px(7),
                row_x=margin + px(27),
                text_w=row_width - px(42),
                row_font=px(23),
            )
        )
    return """
<screen name="{screen_name}" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#050914">
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
    <widget name="summary" position="{margin},{summary_y}"
            size="{content_width},{summary_h}" font="Regular;{summary_font}"
            foregroundColor="#C8D2E3" transparent="1" zPosition="2"
            valign="center" halign="left" />
    {rows}
    <widget name="list_scroll_track" position="{scroll_x},{scroll_y}"
            size="{scroll_w},{scroll_h}" font="Regular;1"
            backgroundColor="#26344E" transparent="0" zPosition="5" />
    <widget name="list_scroll_thumb" position="{scroll_x},{scroll_y}"
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
        screen_name=screen_name,
        width=width,
        height=height,
        accent_h=px(5),
        margin=margin,
        content_width=content_width,
        header_y=px(22),
        header_h=px(62),
        title_font=px(36),
        subtitle_y=px(82),
        subtitle_h=px(45),
        subtitle_font=px(21),
        summary_y=px(128),
        summary_h=px(42),
        summary_font=px(20),
        rows="".join(rows),
        scroll_x=margin + row_width + px(8),
        scroll_y=px(190),
        scroll_w=px(8),
        scroll_h=px(644),
        message_y=px(850),
        message_h=px(80),
        message_font=px(19),
        footer_y=footer_y,
        footer_h=height - footer_y,
        footer_font=px(20),
    )


def _source_type(source):
    return str(getattr(source, "source_type", "xtream") or "xtream").lower()


def _source_name(account):
    return str(
        getattr(account, "display_name", "")
        or getattr(account, "name", "")
        or getattr(account, "host", "")
        or "Xtream"
    )[:160]


def _clock_is_sane(now=None):
    try:
        year = time.gmtime(time.time() if now is None else now).tm_year
    except (OverflowError, OSError, TypeError, ValueError):
        return False
    return 2020 <= int(year) <= 2100


def _safe_utc_text(epoch):
    try:
        value = int(epoch)
        if value <= 0:
            return _("Never")
        return datetime.datetime.fromtimestamp(
            value,
            datetime.timezone.utc,
        ).strftime(
            "%Y-%m-%d %H:%M UTC"
        )
    except (OverflowError, OSError, TypeError, ValueError):
        return _("Never")


def _update_mode(settings):
    value = str(getattr(settings, "update_mode", "automatic") or "").lower()
    return value if value in ("manual", "automatic") else "automatic"


def _update_interval(settings):
    try:
        value = int(getattr(settings, "update_interval_hours", 24))
    except (TypeError, ValueError, OverflowError):
        value = 24
    return value if value in (6, 12, 24) else 24


def _merge_preference_settings(fresh, desired):
    if isinstance(fresh, DVBEPGSettings):
        merged = fresh.copy()
    else:
        merged = DVBEPGSettings.from_dict(fresh)
    previous_pipeline = tuple(
        getattr(merged, field, None) for field in PIPELINE_PREFERENCE_FIELDS
    )
    for field in PREFERENCE_FIELDS:
        if hasattr(desired, field):
            setattr(merged, field, getattr(desired, field))
    # Re-validate all externally supplied choice values before persistence.
    serializer = getattr(merged, "as_dict", None)
    if isinstance(merged, DVBEPGSettings) and callable(serializer):
        merged = DVBEPGSettings.from_dict(serializer())
    current_pipeline = tuple(
        getattr(merged, field, None) for field in PIPELINE_PREFERENCE_FIELDS
    )
    if current_pipeline != previous_pipeline:
        merged.last_success_utc = 0
        merged.last_mapping_count = 0
        merged.last_event_count = 0
    return merged


def _persist_preference_settings(loader, saver, desired):
    try:
        merged = _atomic_settings_update(
            lambda fresh: _merge_preference_settings(fresh, desired),
            loader,
            saver,
        )
    except Exception:
        return False, desired, "failed"
    if merged is None:
        return False, desired, "failed"
    return True, merged, ""


def _normalise_settings_model(settings):
    return (
        settings.copy()
        if isinstance(settings, DVBEPGSettings)
        else DVBEPGSettings.from_dict(settings)
    )


def _atomic_settings_update(mutator, loader=None, saver=None):
    loader = loader or load_dvb_epg_settings
    saver = saver or save_dvb_epg_settings
    if loader is DEFAULT_SETTINGS_LOADER and saver is DEFAULT_SETTINGS_SAVER:
        try:
            from .dvb_epg_settings import update_dvb_epg_settings

            return update_dvb_epg_settings(mutator)
        except ImportError:
            pass
    with _SETTINGS_FALLBACK_LOCK:
        current = _normalise_settings_model(loader())
        candidate = mutator(current.copy())
        if candidate is None:
            return None
        candidate = _normalise_settings_model(candidate)
        result = saver(candidate)
        if result is False:
            raise OSError("DVB EPG settings could not be saved")
        return candidate.copy()


def _reset_persisted_epg_status():
    def reset(settings):
        settings.last_success_utc = 0
        settings.last_mapping_count = 0
        settings.last_event_count = 0
        return settings

    try:
        result = _atomic_settings_update(reset)
    except Exception:
        return False
    return result is not None


def _notify_scheduler_settings_changed():
    """Wake the optional runtime scheduler without making it a UI dependency."""
    try:
        from . import dvb_epg_scheduler

        callback = getattr(dvb_epg_scheduler, "notify_settings_changed", None)
        if callable(callback):
            callback()
    except Exception:
        pass


def _acquire_update_runtime(owner):
    try:
        from .dvb_epg_runtime import acquire_runtime

        return bool(acquire_runtime(owner))
    except ImportError:
        return True
    except Exception:
        return False


def _release_update_runtime(owner):
    try:
        from .dvb_epg_runtime import release_runtime

        release_runtime(owner)
    except Exception:
        pass


def import_event_groups_to_epgcache(groups, cache=None, finalize=True):
    """Apply validated core event groups on the Enigma2 GUI thread."""
    if cache is None:
        try:
            from enigma import eEPGCache
        except ImportError as error:
            raise RuntimeError("native EPG cache is unavailable") from error
        cache = eEPGCache.getInstance()
    if cache is None:
        raise RuntimeError("native EPG cache is unavailable")
    bulk_importer = getattr(cache, "importEvents", None)
    if not callable(bulk_importer):
        raise RuntimeError("native EPG import is unavailable")
    imported = 0
    for services, events in tuple(groups or ()):
        # eEPGCache's SWIG boundary accepts a single Unicode reference or a
        # Python list here; a tuple is silently rejected by common images.
        service_rows = [str(value) for value in services or () if value]
        event_rows = tuple(tuple(value) for value in events or ())
        if not service_rows or not event_rows:
            continue
        if any(len(value) != 6 for value in event_rows):
            raise RuntimeError("invalid EPG event tuple")
        bulk_importer(service_rows, event_rows)
        imported += len(event_rows)
    if finalize:
        saver = getattr(cache, "save", None)
        if callable(saver):
            saver()
    return imported


class GTDVBEPGAutomationScreen(Screen):
    LABELS = (
        N_("Update mode"),
        N_("Update interval"),
        N_("Update on startup if overdue"),
        N_("Update in normal standby"),
        N_("Last update"),
        N_("Next update"),
        N_("Deep standby wake"),
    )

    def __init__(
        self,
        session,
        settings=None,
        settings_saver=None,
        settings_loader=None,
    ):
        self.skin = decorate_remote_footer(
            _settings_skin(
                "GTDVBEPGAutomationScreen",
                (155, 245, 335, 425, 515, 605, 695),
                790,
            ),
            DVB_AUTOMATION_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        self.settings_saver = settings_saver or save_dvb_epg_settings
        self.settings_loader = settings_loader or load_dvb_epg_settings
        if settings is None:
            try:
                settings = self.settings_loader()
            except Exception:
                settings = DVBEPGSettings()
        self.settings = (
            settings.copy()
            if isinstance(settings, DVBEPGSettings)
            else DVBEPGSettings.from_dict(settings)
        )
        self._saved_settings = self.settings.copy()
        self.selected_index = 0

        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(_("Settings"))
        self["subtitle"] = Label(_("DVB EPG AUTOMATIC UPDATES"))
        for index, label in enumerate(self.LABELS):
            self["row_bg_{}".format(index)] = Label("")
            self["focus_{}".format(index)] = Label("")
            self["label_{}".format(index)] = Label(_(label))
            self["value_{}".format(index)] = Label("")
        self["message"] = Label(
            _("XMLTV offsets are converted to UTC automatically; no manual timezone is used.")
        )
        self["footer"] = Label("")
        install_remote_footer(self, DVB_AUTOMATION_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.activate_selected,
                "cancel": self.close_screen,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.change_left,
                "right": self.change_right,
                "green": self.save,
                "red": self.close_screen,
            },
            -1,
        )
        self._refresh()
        self.setTitle(_("Settings - DVB EPG"))

    def _automatic(self):
        return _update_mode(self.settings) == "automatic"

    def _next_update_value(self):
        if not bool(getattr(self.settings, "enabled", False)) or not self._automatic():
            return "-"
        last_success = max(0, int(getattr(self.settings, "last_success_utc", 0) or 0))
        if not last_success:
            return _("Due now")
        due = last_success + (_update_interval(self.settings) * 60 * 60)
        if due <= int(time.time()):
            return _("Overdue: {}").format(_safe_utc_text(due))
        return _safe_utc_text(due)

    def _values(self):
        automatic = self._automatic()
        return (
            _("Automatic") if automatic else _("Manual"),
            _("{} hours").format(_update_interval(self.settings)) if automatic else "-",
            (_("On") if bool(getattr(self.settings, "update_on_startup", True)) else _("Off"))
            if automatic
            else "-",
            (_("On") if bool(getattr(self.settings, "update_in_standby", True)) else _("Off"))
            if automatic
            else "-",
            _safe_utc_text(getattr(self.settings, "last_success_utc", 0)),
            self._next_update_value(),
            _("Always off"),
        )

    def _refresh(self):
        for index, value in enumerate(self._values()):
            self["value_{}".format(index)].setText(str(value))
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

    def _change(self, step):
        if self.selected_index == 0:
            self.settings.update_mode = (
                "manual" if self._automatic() else "automatic"
            )
        elif self.selected_index in (1, 2, 3) and not self._automatic():
            self["message"].setText(
                _("Switch Update mode to Automatic to change this option.")
            )
            return
        elif self.selected_index == 1:
            options = (6, 12, 24)
            current = _update_interval(self.settings)
            self.settings.update_interval_hours = options[
                (options.index(current) + step) % len(options)
            ]
        elif self.selected_index == 2:
            self.settings.update_on_startup = not bool(
                getattr(self.settings, "update_on_startup", True)
            )
        elif self.selected_index == 3:
            self.settings.update_in_standby = not bool(
                getattr(self.settings, "update_in_standby", True)
            )
        elif self.selected_index in (4, 5):
            self["message"].setText(_("Status information is read-only."))
            return
        else:
            if (
                bool(getattr(self.settings, "enabled", False))
                and self._automatic()
                and bool(getattr(self.settings, "update_on_startup", True))
            ):
                message = _(
                    "Deep standby never wakes the receiver; an overdue update "
                    "runs after the next startup."
                )
            else:
                message = "{}: {}".format(
                    _("Deep standby wake"),
                    _("Always off"),
                )
            self["message"].setText(message)
            return
        self["message"].setText(_("Press GREEN to save the changes."))
        self._refresh()

    def change_left(self):
        self._change(-1)

    def change_right(self):
        self._change(1)

    def activate_selected(self):
        self._change(1)

    def save(self):
        saved, merged, _reason = _persist_preference_settings(
            self.settings_loader,
            self.settings_saver,
            self.settings,
        )
        if not saved:
            self["message"].setText(
                _("DVB EPG settings could not be saved. Check the receiver storage.")
            )
            return
        self.settings = merged
        self._saved_settings = self.settings.copy()
        _notify_scheduler_settings_changed()
        self["message"].setText(_("Automatic update settings saved."))

    def close_screen(self):
        self.close(self._saved_settings.copy())


class GTDVBEPGSettingsScreen(Screen):
    LABELS = (
        N_("Enable DVB EPG"),
        N_("Xtream XMLTV source"),
        N_("Target DVB bouquet"),
        N_("EPG range"),
        N_("Manual channel mapping"),
        N_("Automatic updates"),
    )

    def __init__(
        self,
        session,
        settings_loader=None,
        settings_saver=None,
        source_loader=None,
    ):
        self.skin = decorate_remote_footer(
            _settings_skin(),
            DVB_SETTINGS_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        self.settings_loader = settings_loader or load_dvb_epg_settings
        self.settings_saver = settings_saver or save_dvb_epg_settings
        self.source_loader = source_loader or load_sources
        try:
            loaded = self.settings_loader()
        except Exception:
            loaded = DVBEPGSettings()
        self.settings = (
            loaded.copy()
            if isinstance(loaded, DVBEPGSettings)
            else DVBEPGSettings.from_dict(loaded)
        )
        self.selected_index = 0
        self._accounts = []
        self._account_keys = []
        self._bouquets = []
        self._closed = False
        self._bouquet_job = None
        self._bouquet_ready = False
        self._bouquet_result = None
        self._update_job = None
        self._update_ready = False
        self._update_result = None
        self._updating = False
        self._update_generation = 0
        self._update_worker_active = False
        self._runtime_owner = object()
        self._runtime_acquired = False
        self._runtime_lock = threading.Lock()
        self._native_cache = None
        self._native_groups = None
        self._native_group_index = 0
        self._native_event_offset = 0
        self._native_import_count = 0
        self._native_mapping_count = 0
        self._state_lock = threading.Lock()
        self._timer = eTimer()
        self._timer_binding = _connect_timer(self._timer, self._poll_jobs)
        self._load_accounts()

        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(_("Settings"))
        self["subtitle"] = Label(_("DVB SATELLITE EPG"))
        for index, label in enumerate(self.LABELS):
            self["row_bg_{}".format(index)] = Label("")
            self["focus_{}".format(index)] = Label("")
            self["label_{}".format(index)] = Label(_(label))
            self["value_{}".format(index)] = Label("")
        self["message"] = Label(self._summary_text())
        self["footer"] = Label("")
        install_remote_footer(self, DVB_SETTINGS_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.activate_selected,
                "cancel": self.cancel_or_close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.change_left,
                "right": self.change_right,
                "green": self.save,
                "yellow": self.begin_update,
                "blue": self.open_mapping,
                "red": self.cancel_or_close,
            },
            -1,
        )
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop)
        self._refresh()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._refresh)
        self._start_bouquet_scan()
        self.setTitle(_("Settings - DVB EPG"))

    def _load_accounts(self):
        try:
            result = self.source_loader()
            candidates = list(getattr(result, "accounts", ()) or ())
        except Exception:
            candidates = []
        accounts = []
        keys = []
        for account in candidates:
            if _source_type(account) != "xtream":
                continue
            try:
                source_key = xtream_source_key(account)
            except (TypeError, ValueError):
                continue
            accounts.append(account)
            keys.append(source_key)
        self._accounts = accounts
        self._account_keys = keys
        if keys and self.settings.source_key not in keys:
            self.settings.source_key = keys[0]

    def _selected_account(self):
        try:
            index = self._account_keys.index(self.settings.source_key)
        except ValueError:
            return None
        return self._accounts[index]

    def _selected_account_index(self):
        try:
            return self._account_keys.index(self.settings.source_key)
        except ValueError:
            return 0

    def _start_bouquet_scan(self):
        _cancel_job(self._bouquet_job)
        job = _UIJobToken(45)
        self._bouquet_job = job
        self._bouquet_ready = False
        self._bouquet_result = None
        worker = threading.Thread(
            target=lambda: self._run_bouquet_scan(job),
            name="GTDVBBouquetScan",
        )
        worker.daemon = True
        worker.start()
        self._timer.start(120, True)

    def _run_bouquet_scan(self, job):
        try:
            result = list_dvb_bouquets(
                cancel_event=job.cancel_event,
                deadline=job.deadline,
            )
        except Exception:
            result = None
        if self._closed or not job.active():
            return
        with self._state_lock:
            self._bouquet_result = result
            self._bouquet_ready = True

    def _apply_bouquet_result(self):
        with self._state_lock:
            result = self._bouquet_result
            self._bouquet_result = None
            self._bouquet_ready = False
        self._bouquet_job = None
        self._bouquets = list(result or ())
        self._refresh()

    def _bouquet_value(self):
        if self.settings.bouquet_file == ALL_BOUQUETS:
            return _("All TV bouquets")
        for bouquet in self._bouquets:
            if bouquet.filename == self.settings.bouquet_file:
                return bouquet.name
        if self._bouquet_job is not None:
            return _("Scanning DVB bouquets...")
        return _("Selected bouquet is unavailable")

    def _source_value(self):
        account = self._selected_account()
        return _source_name(account) if account is not None else _("No Xtream source")

    def _summary_text(self):
        if not self.settings.last_success_utc:
            return _("No DVB EPG update has been completed yet.")
        return _("Last update: {}  •  {} channels  •  {} events").format(
            _safe_utc_text(self.settings.last_success_utc),
            self.settings.last_mapping_count,
            self.settings.last_event_count,
        )

    def _values(self):
        update_mode = _update_mode(self.settings)
        automatic_value = (
            _("Automatic")
            + "  •  "
            + _("{} hours").format(_update_interval(self.settings))
            if update_mode == "automatic"
            else _("Manual")
        )
        return (
            _("On") if self.settings.enabled else _("Off"),
            self._source_value(),
            self._bouquet_value(),
            _("{} days").format(self.settings.epg_days),
            _("Open ({} mapped)").format(self.settings.last_mapping_count),
            automatic_value,
        )

    def _refresh(self):
        dynamic_indexes = set()
        if self._selected_account() is not None:
            dynamic_indexes.add(1)
        if self.settings.bouquet_file != ALL_BOUQUETS and any(
            bouquet.filename == self.settings.bouquet_file
            for bouquet in self._bouquets
        ):
            dynamic_indexes.add(2)
        for index, value in enumerate(self._values()):
            widget = self["value_{}".format(index)]
            if index in dynamic_indexes:
                ellipsize_dynamic_text(
                    widget,
                    value,
                    fallback_chars=52,
                )
            else:
                widget.setText(str(value))
            if index == self.selected_index:
                self["focus_{}".format(index)].show()
            else:
                self["focus_{}".format(index)].hide()

    def move_up(self):
        if self._updating:
            return
        self.selected_index = (self.selected_index - 1) % len(self.LABELS)
        self._refresh()

    def move_down(self):
        if self._updating:
            return
        self.selected_index = (self.selected_index + 1) % len(self.LABELS)
        self._refresh()

    def _change(self, step):
        if self._updating:
            return
        if self.selected_index == 0:
            self.settings.enabled = not self.settings.enabled
        elif self.selected_index == 1:
            if not self._account_keys:
                self["message"].setText(_("No valid Xtream source was found."))
                return
            index = self._selected_account_index()
            self.settings.source_key = self._account_keys[
                (index + step) % len(self._account_keys)
            ]
        elif self.selected_index == 2:
            if self._bouquet_job is not None:
                self["message"].setText(_("Scanning DVB bouquets..."))
                return
            options = [ALL_BOUQUETS] + [item.filename for item in self._bouquets]
            try:
                index = options.index(self.settings.bouquet_file)
            except ValueError:
                index = 0
            self.settings.bouquet_file = options[(index + step) % len(options)]
        elif self.selected_index == 3:
            options = list(SUPPORTED_EPG_DAYS)
            try:
                index = options.index(self.settings.epg_days)
            except ValueError:
                index = 1
            self.settings.epg_days = options[(index + step) % len(options)]
        elif self.selected_index == 4:
            self.open_mapping()
            return
        else:
            self.open_automation()
            return
        self["message"].setText(_("Press GREEN to save the changes."))
        self._refresh()

    def change_left(self):
        self._change(-1)

    def change_right(self):
        self._change(1)

    def activate_selected(self):
        if self.selected_index == 4:
            self.open_mapping()
        elif self.selected_index == 5:
            self.open_automation()
        else:
            self.change_right()

    def _save_import_status(self, success_utc, mapping_count, event_count):
        def update_status(settings):
            settings.last_success_utc = int(success_utc)
            settings.last_mapping_count = int(mapping_count)
            settings.last_event_count = int(event_count)
            return settings

        try:
            updated = _atomic_settings_update(
                update_status,
                self.settings_loader,
                self.settings_saver,
            )
        except Exception:
            updated = None
        if updated is None:
            self["message"].setText(
                _("DVB EPG settings could not be saved. Check the receiver storage.")
            )
            return False
        self.settings = updated
        return True

    def _save_preferences(self):
        saved, merged, _reason = _persist_preference_settings(
            self.settings_loader,
            self.settings_saver,
            self.settings,
        )
        if not saved:
            self["message"].setText(
                _("DVB EPG settings could not be saved. Check the receiver storage.")
            )
            return False
        self.settings = merged
        return True

    def _claim_runtime(self):
        with self._runtime_lock:
            if self._runtime_acquired:
                return "owned"
            if not _acquire_update_runtime(self._runtime_owner):
                return "busy"
            self._runtime_acquired = True
            return "acquired"

    def _release_runtime(self):
        with self._runtime_lock:
            if not self._runtime_acquired:
                return
            # Release the process-wide owner before exposing the local slot as
            # reusable.  A cancelled worker may execute this method.
            _release_update_runtime(self._runtime_owner)
            self._runtime_acquired = False

    def save(self):
        if self._updating:
            self["message"].setText(_("DVB EPG update is still running..."))
            return
        if self._save_preferences():
            _notify_scheduler_settings_changed()
            self["message"].setText(_("DVB EPG settings saved."))

    def begin_update(self):
        if self._updating:
            self["message"].setText(_("DVB EPG update is still running..."))
            return
        if self._bouquet_job is not None:
            self["message"].setText(_("Scanning DVB bouquets..."))
            return
        if not self.settings.enabled:
            self["message"].setText(_("Enable DVB EPG before starting an update."))
            return
        account = self._selected_account()
        if account is None:
            self["message"].setText(_("Select a valid Xtream XMLTV source."))
            return
        if not _clock_is_sane():
            self["message"].setText(
                _("The receiver clock is not ready. Check network time and try again.")
            )
            return
        if not self._save_preferences():
            return
        _notify_scheduler_settings_changed()
        runtime_status = self._claim_runtime()
        if runtime_status == "owned":
            self["message"].setText(_("DVB EPG update is still running..."))
            return
        if runtime_status != "acquired":
            self["message"].setText(
                _("An automatic DVB EPG update is already running.")
            )
            return
        _cancel_job(self._update_job)
        job = _UIJobToken(UI_JOB_TIMEOUT_SECONDS)
        self._update_job = job
        self._update_ready = False
        self._update_result = None
        self._updating = True
        self._update_generation += 1
        self._update_worker_active = True
        generation = self._update_generation
        snapshot = self.settings.copy()
        self["message"].setText(_("Downloading and preparing the Xtream XMLTV guide..."))
        try:
            worker = threading.Thread(
                target=lambda: self._run_update(job, generation, account, snapshot),
                name="GTDVBEPGUpdate",
            )
            worker.daemon = True
            worker.start()
        except Exception:
            self._update_worker_active = False
            self._update_job = None
            self._updating = False
            self._release_runtime()
            self["message"].setText(
                _("DVB EPG update failed. No existing EPG data was removed.")
            )
            return
        self._timer.start(120, True)

    def _run_update(self, job, generation, account, settings):
        payload = None
        try:
            bouquet_files = (
                None
                if settings.bouquet_file == ALL_BOUQUETS
                else (settings.bouquet_file,)
            )
            services = load_dvb_services(
                bouquet_files=bouquet_files,
                cancel_event=job.cancel_event,
                deadline=job.deadline,
            )
            if not services:
                raise DvbEpgError("no DVB services")
            download = download_xtream_xmltv(
                account,
                cancel_event=job.cancel_event,
                deadline=job.deadline,
            )
            scan = scan_xmltv(
                download.path,
                cancel_event=job.cancel_event,
                deadline=job.deadline,
            )
            manual = load_manual_mappings()
            source_key = xtream_source_key(account)
            matched = match_xmltv_channels(
                scan.channels,
                services,
                manual_entries=manual,
                source_key=source_key,
            )
            imported = build_import_event_groups(
                download.path,
                matched.mappings,
                now=int(time.time()),
                past_seconds=DEFAULT_IMPORT_PAST_SECONDS,
                future_seconds=int(settings.epg_days) * 24 * 60 * 60,
                allowed_services=services,
                cancel_event=job.cancel_event,
                deadline=job.deadline,
            )
            job.check()
            payload = (True, matched, imported)
        except OperationCancelled:
            payload = (False, "cancelled")
        except Exception:
            payload = (False, "failed")
        published = False
        with self._state_lock:
            self._update_worker_active = False
            if (
                not self._closed
                and job.active()
                and self._update_job is job
                and self._update_generation == generation
            ):
                self._update_result = payload
                self._update_ready = True
                published = True
        if not published:
            self._release_runtime()

    def _apply_update_result(self):
        with self._state_lock:
            result = self._update_result
            self._update_result = None
            self._update_ready = False
        self._update_job = None
        if not result or not result[0]:
            self._updating = False
            self._release_runtime()
            self["message"].setText(_("DVB EPG update failed. No existing EPG data was removed."))
            return
        matched, imported = result[1], result[2]
        try:
            from enigma import eEPGCache

            cache = eEPGCache.getInstance()
            if cache is None or not callable(getattr(cache, "importEvents", None)):
                raise RuntimeError("native EPG import is unavailable")
        except Exception:
            self._updating = False
            self._release_runtime()
            self["message"].setText(_("The native Enigma2 EPG cache rejected the update."))
            return
        self._native_cache = cache
        self._native_groups = tuple(imported.groups or ())
        self._native_group_index = 0
        self._native_event_offset = 0
        self._native_import_count = 0
        self._native_mapping_count = len(matched.mappings)
        self["message"].setText(_("Please wait"))

    def _next_native_batch(self, maximum_events=2000, maximum_groups=8):
        if self._native_groups is None:
            return ()
        batch = []
        remaining = int(maximum_events)
        while (
            self._native_group_index < len(self._native_groups)
            and remaining > 0
            and len(batch) < int(maximum_groups)
        ):
            services, events = self._native_groups[self._native_group_index]
            events = tuple(events or ())
            start = self._native_event_offset
            take = min(len(events) - start, remaining)
            if take > 0:
                batch.append((services, events[start : start + take]))
                remaining -= take
                self._native_event_offset += take
            if self._native_event_offset >= len(events):
                self._native_group_index += 1
                self._native_event_offset = 0
        return tuple(batch)

    def _poll_native_import(self):
        if self._native_groups is None:
            return
        try:
            batch = self._next_native_batch()
            if batch:
                self._native_import_count += import_event_groups_to_epgcache(
                    batch,
                    cache=self._native_cache,
                    finalize=False,
                )
            complete = self._native_group_index >= len(self._native_groups)
            if not complete:
                return
            import_event_groups_to_epgcache(
                (),
                cache=self._native_cache,
                finalize=True,
            )
        except Exception:
            self._clear_native_import()
            self._updating = False
            self._release_runtime()
            self["message"].setText(
                _("DVB EPG update failed. No existing EPG data was removed.")
            )
            return
        imported_count = self._native_import_count
        mapping_count = self._native_mapping_count
        self._clear_native_import()
        self._updating = False
        saved = self._save_import_status(
            int(time.time()),
            mapping_count,
            imported_count,
        )
        self._release_runtime()
        if not saved:
            return
        _notify_scheduler_settings_changed()
        self["message"].setText(
            _("DVB EPG updated: {} channels and {} events imported.").format(
                mapping_count,
                imported_count,
            )
        )
        self._refresh()

    def _clear_native_import(self):
        self._native_cache = None
        self._native_groups = None
        self._native_group_index = 0
        self._native_event_offset = 0
        self._native_import_count = 0
        self._native_mapping_count = 0

    def _poll_jobs(self):
        if self._closed:
            return
        if self._bouquet_ready:
            self._apply_bouquet_result()
        elif self._bouquet_job is not None and self._bouquet_job.expired():
            _cancel_job(self._bouquet_job)
            self._bouquet_job = None
            self["message"].setText(_("DVB bouquets could not be scanned."))
            self._refresh()
        if self._update_ready:
            self._apply_update_result()
        elif self._updating and self._update_job is not None and self._update_job.expired():
            _cancel_job(self._update_job)
            with self._state_lock:
                self._update_job = None
                self._update_result = None
                self._update_ready = False
                self._update_generation += 1
                worker_active = self._update_worker_active
            self._updating = False
            if not worker_active:
                self._release_runtime()
            self["message"].setText(_("DVB EPG update timed out."))
        if self._native_groups is not None:
            self._poll_native_import()
        if self._bouquet_job is not None or self._updating:
            self._timer.start(120, True)

    def open_mapping(self):
        if self._updating:
            self["message"].setText(_("DVB EPG update is still running..."))
            return
        account = self._selected_account()
        if account is None:
            self["message"].setText(_("Select a valid Xtream XMLTV source."))
            return
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(_("Manual channel mapping could not be opened."))
            return
        self.session.openWithCallback(
            self._mapping_closed,
            GTDVBEPGMappingScreen,
            account,
            self.settings.bouquet_file,
        )

    def _mapping_closed(self, mapping_count=None, *args):
        try:
            self.settings = _merge_preference_settings(
                self.settings_loader(),
                self.settings,
            )
        except Exception:
            pass
        self["message"].setText(self._summary_text())
        self._refresh()

    def open_automation(self):
        if self._updating:
            self["message"].setText(_("DVB EPG update is still running..."))
            return
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(
                _("Automatic update settings could not be opened.")
            )
            return
        # Keep unsaved choices from this screen, but refresh the read-only
        # scheduler status so Last/Next update never opens with stale data.
        snapshot = self.settings.copy()
        try:
            snapshot = _merge_preference_settings(
                self.settings_loader(),
                snapshot,
            )
        except Exception:
            pass
        self.session.openWithCallback(
            self._automation_closed,
            GTDVBEPGAutomationScreen,
            snapshot,
            self.settings_saver,
            self.settings_loader,
        )

    def _automation_closed(self, settings=None, *args):
        if isinstance(settings, DVBEPGSettings):
            self.settings = settings.copy()
        elif isinstance(settings, dict):
            self.settings = DVBEPGSettings.from_dict(settings)
        self["message"].setText(
            _("XMLTV offsets are converted to UTC automatically; no manual timezone is used.")
        )
        self._refresh()

    def cancel_or_close(self):
        if self._native_groups is not None:
            self["message"].setText(_("DVB EPG update is still running..."))
            return
        if self._updating:
            _cancel_job(self._update_job)
            with self._state_lock:
                self._update_job = None
                self._update_result = None
                self._update_ready = False
                self._update_generation += 1
                worker_active = self._update_worker_active
            self._clear_native_import()
            self._updating = False
            if not worker_active:
                self._release_runtime()
            self["message"].setText(_("DVB EPG update cancelled."))
            return
        self.close(False)

    def _stop(self):
        self._closed = True
        _cancel_job(self._bouquet_job)
        _cancel_job(self._update_job)
        self._bouquet_job = None
        with self._state_lock:
            self._update_job = None
            self._update_result = None
            self._update_ready = False
            self._update_generation += 1
            worker_active = self._update_worker_active
        self._clear_native_import()
        if not worker_active:
            self._release_runtime()
        try:
            self._timer.stop()
        except Exception:
            pass
        _disconnect_timer(self._timer, self._timer_binding)
        self._timer_binding = None


class GTDVBEPGMappingScreen(Screen):
    page_size = MAPPING_PAGE_SIZE

    def __init__(self, session, account, bouquet_file=ALL_BOUQUETS):
        self.skin = decorate_remote_footer(
            _list_skin("GTDVBEPGMappingScreen"),
            DVB_MAPPING_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        self.account = account
        self.source_key = xtream_source_key(account)
        self.bouquet_file = str(bouquet_file or ALL_BOUQUETS)
        self.selected_index = 0
        self._services = []
        self._channels = []
        self._manual_entries = []
        self._match = None
        self._manual_lookup = {}
        self._channel_lookup = {}
        self._mapped_lookup = {}
        self._closed = False
        self._loading = False
        self._job = None
        self._job_generation = 0
        self._result_ready = False
        self._result = None
        self._active_load_jobs = set()
        self._download_runtime_owner = object()
        self._download_runtime_acquired = False
        self._download_job = None
        self._mapping_runtime_owner = object()
        self._mapping_runtime_acquired = False
        self._runtime_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._timer = eTimer()
        self._timer_binding = _connect_timer(self._timer, self._poll_job)

        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(_("Manual channel mapping"))
        self["subtitle"] = Label("")
        self._fit_source_subtitle()
        self["summary"] = Label("")
        for index in range(self.page_size):
            self["row_bg_{}".format(index)] = Label("")
            self["focus_{}".format(index)] = Label("")
            self["marker_{}".format(index)] = Label("")
            self["row_{}".format(index)] = Label("")
        self["list_scroll_track"] = Label("")
        self["list_scroll_thumb"] = Label("")
        hide_scrollbar(self, "list_scroll")
        self["message"] = Label(_("Loading DVB services and cached XMLTV channels..."))
        self["footer"] = Label("")
        install_remote_footer(self, DVB_MAPPING_FOOTER_ITEMS)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {
                "ok": self.open_picker,
                "cancel": self.cancel_or_close,
                "up": self.move_up,
                "down": self.move_down,
                "upRepeated": self.move_up,
                "downRepeated": self.move_down,
                "left": self.page_up,
                "right": self.page_down,
                "leftRepeated": self.page_up,
                "rightRepeated": self.page_down,
                "red": self.toggle_block,
                "yellow": self.refresh_xmltv,
            },
            -1,
        )
        if hasattr(self, "onClose"):
            self.onClose.append(self._stop)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._fit_source_subtitle)
            self.onLayoutFinish.append(self._refresh_rows)
        self._refresh_rows()
        self._start_load(download=False)
        self.setTitle(_("Manual channel mapping"))

    def _fit_source_subtitle(self):
        ellipsize_dynamic_text(
            self["subtitle"],
            _source_name(self.account),
            fallback_chars=108,
        )

    def _start_load(self, download=False, reuse=False):
        if self._loading:
            return
        _cancel_job(self._job)
        job = _UIJobToken(UI_JOB_TIMEOUT_SECONDS if download else 60)
        if download and not self._claim_download_runtime(job):
            self["message"].setText(
                _("An automatic DVB EPG update is already running.")
            )
            return
        self._job = job
        self._job_generation += 1
        generation = self._job_generation
        self._loading = True
        with self._state_lock:
            self._active_load_jobs.add(job)
        self._result_ready = False
        self._result = None
        service_snapshot = tuple(self._services) if reuse else None
        channel_snapshot = tuple(self._channels) if reuse else None
        self["message"].setText(
            _("Downloading the Xtream XMLTV guide...")
            if download
            else _("Loading DVB services and cached XMLTV channels...")
        )
        try:
            worker = threading.Thread(
                target=lambda: self._run_load(
                    job,
                    generation,
                    download,
                    service_snapshot,
                    channel_snapshot,
                ),
                name="GTDVBMappingLoad",
            )
            worker.daemon = True
            worker.start()
        except Exception:
            with self._state_lock:
                self._active_load_jobs.discard(job)
            self._job = None
            self._loading = False
            self._release_download_runtime(job)
            self["message"].setText(_("Channel mapping data could not be loaded."))
            return
        self._timer.start(120, True)

    def _run_load(
        self,
        job,
        generation,
        download,
        service_snapshot,
        channel_snapshot,
    ):
        payload = None
        try:
            if service_snapshot is not None and channel_snapshot is not None:
                services = service_snapshot
                channels = channel_snapshot
            else:
                bouquet_files = (
                    None
                    if self.bouquet_file == ALL_BOUQUETS
                    else (self.bouquet_file,)
                )
                services = load_dvb_services(
                    bouquet_files=bouquet_files,
                    cancel_event=job.cancel_event,
                    deadline=job.deadline,
                )
                path = default_xmltv_cache_path(self.account)
                if download:
                    path = download_xtream_xmltv(
                        self.account,
                        cancel_event=job.cancel_event,
                        deadline=job.deadline,
                    ).path
                channels = ()
                if os.path.isfile(path) and not os.path.islink(path):
                    channels = scan_xmltv(
                        path,
                        cancel_event=job.cancel_event,
                        deadline=job.deadline,
                    ).channels
            manual = load_manual_mappings()
            matched = match_xmltv_channels(
                channels,
                services,
                manual_entries=manual,
                source_key=self.source_key,
            )
            job.check()
            payload = (True, list(services), list(channels), list(manual), matched)
        except OperationCancelled:
            payload = (False, "cancelled")
        except Exception:
            payload = (False, "failed")
        published = False
        with self._state_lock:
            self._active_load_jobs.discard(job)
            if (
                not self._closed
                and job.active()
                and self._job is job
                and self._job_generation == generation
            ):
                self._result = payload
                self._result_ready = True
                published = True
        if download and not published:
            self._release_download_runtime(job)

    def _poll_job(self):
        if self._closed:
            return
        if self._job is None:
            return
        if not self._result_ready:
            if self._job is not None and self._job.expired():
                expired_job = self._job
                _cancel_job(expired_job)
                with self._state_lock:
                    self._job = None
                    self._result = None
                    self._result_ready = False
                    self._job_generation += 1
                    worker_active = expired_job in self._active_load_jobs
                self._loading = False
                if not worker_active:
                    self._release_download_runtime(expired_job)
                self["message"].setText(_("Channel mapping data could not be loaded."))
                return
            self._timer.start(120, True)
            return
        with self._state_lock:
            completed_job = self._job
            result = self._result
            self._result = None
            self._result_ready = False
        self._job = None
        self._loading = False
        self._release_download_runtime(completed_job)
        if not result or not result[0]:
            self["message"].setText(_("Channel mapping data could not be loaded."))
            return
        self._services = result[1]
        self._channels = result[2]
        self._manual_entries = result[3]
        self._match = result[4]
        self._rebuild_lookup_indexes()
        self.selected_index = min(self.selected_index, max(0, len(self._services) - 1))
        if not self._services:
            self["message"].setText(_("No real DVB service was found in the selected bouquet."))
        elif not self._channels:
            self["message"].setText(_("No cached XMLTV guide was found. Press YELLOW to download it."))
        else:
            self["message"].setText(
                _("Press OK to choose an XMLTV channel; RED blocks or restores automatic matching.")
            )
        self._refresh_rows()

    def _rebuild_lookup_indexes(self):
        self._manual_lookup = {
            entry.service_ref: entry
            for entry in self._manual_entries
            if entry.source_key == self.source_key
        }
        self._channel_lookup = {
            channel.xmltv_id: channel for channel in self._channels
        }
        self._mapped_lookup = {
            service_ref: xmltv_id
            for xmltv_id, service_ref in (
                self._match.mappings.items() if self._match is not None else ()
            )
        }

    def _row_status(self, service):
        manual = self._manual_lookup.get(service.service_ref)
        if manual is not None and manual.blocked:
            return _("Blocked")
        if manual is not None:
            channel = self._channel_lookup.get(manual.xmltv_id)
            name = channel.display_name if channel is not None else manual.xmltv_name
            return "{}: {}".format(
                _("Manual"),
                clean_dynamic_text(name or manual.xmltv_id),
            )
        xmltv_id = self._mapped_lookup.get(service.service_ref)
        if xmltv_id:
            channel = self._channel_lookup.get(xmltv_id)
            name = channel.display_name if channel is not None else xmltv_id
            return "{}: {}".format(
                _("Automatic"),
                clean_dynamic_text(name),
            )
        return _("Unmatched")

    def _refresh_match(self):
        self._start_load(download=False, reuse=True)

    def _claim_download_runtime(self, job):
        with self._runtime_lock:
            if self._download_runtime_acquired:
                return False
            if not _acquire_update_runtime(self._download_runtime_owner):
                return False
            self._download_job = job
            self._download_runtime_acquired = True
            return True

    def _release_download_runtime(self, job=None):
        with self._runtime_lock:
            if job is not None and self._download_job is not job:
                return
            if self._download_runtime_acquired:
                # Keep the tracked job and local flag visible until the global
                # release has completed; this method also runs on workers.
                _release_update_runtime(self._download_runtime_owner)
            self._download_job = None
            self._download_runtime_acquired = False

    def _acquire_mapping_runtime(self):
        with self._runtime_lock:
            if self._mapping_runtime_acquired:
                return False
            if not _acquire_update_runtime(self._mapping_runtime_owner):
                self["message"].setText(
                    _("An automatic DVB EPG update is already running.")
                )
                return False
            self._mapping_runtime_acquired = True
            return True

    def _release_mapping_runtime(self):
        with self._runtime_lock:
            if not self._mapping_runtime_acquired:
                return
            _release_update_runtime(self._mapping_runtime_owner)
            self._mapping_runtime_acquired = False

    def _refresh_rows(self):
        count = len(self._services)
        mapped_count = len(self._match.mappings) if self._match is not None else 0
        self["summary"].setText(
            _("{} DVB services  •  {} XMLTV channels  •  {} mapped").format(
                count,
                len(self._channels),
                mapped_count,
            )
        )
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page = self._services[page_start : page_start + self.page_size]
        for offset in range(self.page_size):
            row = self["row_{}".format(offset)]
            focus = self["focus_{}".format(offset)]
            marker = self["marker_{}".format(offset)]
            if offset >= len(page):
                row.setText("")
                focus.hide()
                marker.hide()
                continue
            service = page[offset]
            ellipsize_dynamic_text(
                row,
                "{}  •  {}".format(
                    clean_dynamic_text(service.name),
                    self._row_status(service),
                ),
                fallback_chars=94,
            )
            selected = page_start + offset == self.selected_index
            if selected:
                focus.show()
                marker.show()
            else:
                focus.hide()
                marker.hide()
        width, height, px = _scale()
        update_scrollbar(
            self,
            "list_scroll",
            count,
            self.selected_index,
            self.page_size,
            (width - px(80), px(190), px(8), px(644)),
        )

    def move_up(self):
        if self._loading or not self._services:
            return
        self.selected_index = (self.selected_index - 1) % len(self._services)
        self._refresh_rows()

    def move_down(self):
        if self._loading or not self._services:
            return
        self.selected_index = (self.selected_index + 1) % len(self._services)
        self._refresh_rows()

    def page_up(self):
        if self._loading or not self._services:
            return
        self.selected_index = max(0, self.selected_index - self.page_size)
        self._refresh_rows()

    def page_down(self):
        if self._loading or not self._services:
            return
        self.selected_index = min(
            len(self._services) - 1,
            self.selected_index + self.page_size,
        )
        self._refresh_rows()

    def refresh_xmltv(self):
        if self._loading:
            self["message"].setText(_("Please wait"))
            return
        self._start_load(download=True)

    def open_picker(self):
        if self._loading:
            return
        if not self._services:
            self["message"].setText(_("No real DVB service is available for mapping."))
            return
        if not self._channels:
            self["message"].setText(_("Press YELLOW to download the XMLTV channel list first."))
            return
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(_("The XMLTV channel list could not be opened."))
            return
        service = self._services[self.selected_index]
        self.session.openWithCallback(
            self._channel_selected,
            GTXMLTVChannelSelectScreen,
            self._channels,
            service.name,
        )

    def _channel_selected(self, channel=None, *args):
        if channel is None or not self._services:
            return
        if not self._acquire_mapping_runtime():
            return
        service = self._services[self.selected_index]
        status_saved = False
        try:
            set_manual_mapping(
                self.source_key,
                service.service_ref,
                channel.xmltv_id,
                source_name=_source_name(self.account),
                service_name=service.name,
                xmltv_name=channel.display_name,
            )
            status_saved = _reset_persisted_epg_status()
        except MappingStoreError:
            self["message"].setText(
                _("That XMLTV channel is already assigned to another DVB service.")
            )
            return
        except Exception:
            self["message"].setText(_("The manual channel mapping could not be saved."))
            return
        finally:
            self._release_mapping_runtime()
        self._refresh_match()
        if not status_saved:
            self["message"].setText(
                _("DVB EPG settings could not be saved. Check the receiver storage.")
            )
            self._refresh_rows()
            return
        _notify_scheduler_settings_changed()
        fit_dynamic_text(
            self["message"],
            _("Manual mapping saved: {} -> {}").format(
                service.name,
                channel.display_name,
            ),
            max_lines=3,
            fallback_chars=104,
        )
        self._refresh_rows()

    def toggle_block(self):
        if self._loading or not self._services:
            return
        if not self._acquire_mapping_runtime():
            return
        service = self._services[self.selected_index]
        current = self._manual_lookup.get(service.service_ref)
        status_saved = False
        try:
            if current is not None and current.blocked:
                remove_manual_mapping(self.source_key, service.service_ref)
                message = _("Automatic matching restored for {}.").format(service.name)
            else:
                block_automatic_mapping(
                    self.source_key,
                    service.service_ref,
                    source_name=_source_name(self.account),
                    service_name=service.name,
                )
                message = _("EPG mapping removed and automatic matching blocked for {}.").format(
                    service.name
                )
            status_saved = _reset_persisted_epg_status()
        except Exception:
            self["message"].setText(_("The manual channel mapping could not be saved."))
            return
        finally:
            self._release_mapping_runtime()
        self._refresh_match()
        if not status_saved:
            self["message"].setText(
                _("DVB EPG settings could not be saved. Check the receiver storage.")
            )
            self._refresh_rows()
            return
        _notify_scheduler_settings_changed()
        fit_dynamic_text(
            self["message"],
            message,
            max_lines=3,
            fallback_chars=104,
        )
        self._refresh_rows()

    def cancel_or_close(self):
        if self._loading:
            cancelled_job = self._job
            _cancel_job(cancelled_job)
            with self._state_lock:
                self._job = None
                self._result = None
                self._result_ready = False
                self._job_generation += 1
                worker_active = cancelled_job in self._active_load_jobs
            self._loading = False
            if not worker_active:
                self._release_download_runtime(cancelled_job)
            self["message"].setText(_("Channel mapping refresh cancelled."))
            return
        count = len(self._match.mappings) if self._match is not None else 0
        self.close(count)

    def _stop(self):
        self._closed = True
        stopped_job = self._job
        _cancel_job(stopped_job)
        self._release_mapping_runtime()
        with self._state_lock:
            self._job = None
            self._result = None
            self._result_ready = False
            self._job_generation += 1
            download_worker_active = (
                self._download_job is not None
                and self._download_job in self._active_load_jobs
            )
        # A first Cancel clears ``self._job`` while the cancelled download
        # worker may still be unwinding.  Keep the shared runtime owner until
        # that worker removes itself and performs the unpublished-result
        # release; otherwise a second Cancel/close can overlap another EPG job.
        if not download_worker_active:
            self._release_download_runtime()
        try:
            self._timer.stop()
        except Exception:
            pass
        _disconnect_timer(self._timer, self._timer_binding)
        self._timer_binding = None


class GTXMLTVChannelSelectScreen(Screen):
    page_size = MAPPING_PAGE_SIZE

    def __init__(self, session, channels, service_name=""):
        self.skin = decorate_remote_footer(
            _list_skin("GTXMLTVChannelSelectScreen"),
            XMLTV_PICKER_FOOTER_ITEMS,
        )
        Screen.__init__(self, session)
        self._all_channels = list(channels or ())
        self._entries = list(self._all_channels)
        self.query = ""
        self.selected_index = 0

        attach_background(self, "app_bg", APP_BACKGROUND)
        self["top_accent"] = Label("")
        self["header"] = Label(_("Select XMLTV channel"))
        self.service_name = str(service_name or "")
        self["subtitle"] = Label("")
        self._fit_service_subtitle()
        self["summary"] = Label("")
        for index in range(self.page_size):
            self["row_bg_{}".format(index)] = Label("")
            self["focus_{}".format(index)] = Label("")
            self["marker_{}".format(index)] = Label("")
            self["row_{}".format(index)] = Label("")
        self["list_scroll_track"] = Label("")
        self["list_scroll_thumb"] = Label("")
        hide_scrollbar(self, "list_scroll")
        self["message"] = Label(_("Press GREEN to filter the XMLTV channel list."))
        self["footer"] = Label("")
        install_remote_footer(self, XMLTV_PICKER_FOOTER_ITEMS)
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
                "green": self.open_search,
                "yellow": self.clear_search,
            },
            -1,
        )
        self._refresh_rows()
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._fit_service_subtitle)
            self.onLayoutFinish.append(self._refresh_rows)
        self.setTitle(_("Select XMLTV channel"))

    def _fit_service_subtitle(self):
        ellipsize_dynamic_text(
            self["subtitle"],
            self.service_name,
            fallback_chars=108,
        )

    def _refresh_rows(self):
        count = len(self._entries)
        summary = _("{} XMLTV channels").format(count)
        if self.query:
            summary += "  •  {}: {}".format(_("Search"), self.query)
            ellipsize_dynamic_text(
                self["summary"],
                summary,
                fallback_chars=108,
            )
        else:
            self["summary"].setText(summary)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page = self._entries[page_start : page_start + self.page_size]
        for offset in range(self.page_size):
            row = self["row_{}".format(offset)]
            focus = self["focus_{}".format(offset)]
            marker = self["marker_{}".format(offset)]
            if offset >= len(page):
                row.setText("")
                focus.hide()
                marker.hide()
                continue
            channel = page[offset]
            ellipsize_dynamic_text(
                row,
                "{}  •  {}".format(
                    clean_dynamic_text(channel.display_name),
                    clean_dynamic_text(channel.xmltv_id),
                ),
                fallback_chars=94,
            )
            selected = page_start + offset == self.selected_index
            if selected:
                focus.show()
                marker.show()
            else:
                focus.hide()
                marker.hide()
        width, height, px = _scale()
        update_scrollbar(
            self,
            "list_scroll",
            count,
            self.selected_index,
            self.page_size,
            (width - px(80), px(190), px(8), px(644)),
        )

    def move_up(self):
        if not self._entries:
            return
        self.selected_index = (self.selected_index - 1) % len(self._entries)
        self._refresh_rows()

    def move_down(self):
        if not self._entries:
            return
        self.selected_index = (self.selected_index + 1) % len(self._entries)
        self._refresh_rows()

    def page_up(self):
        if not self._entries:
            return
        self.selected_index = max(0, self.selected_index - self.page_size)
        self._refresh_rows()

    def page_down(self):
        if not self._entries:
            return
        self.selected_index = min(
            len(self._entries) - 1,
            self.selected_index + self.page_size,
        )
        self._refresh_rows()

    def select_current(self):
        if not self._entries:
            self["message"].setText(_("No XMLTV channel matches the current filter."))
            return
        self.close(self._entries[self.selected_index])

    def open_search(self):
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self["message"].setText(_("The virtual keyboard is unavailable on this image."))
            return
        if not hasattr(self.session, "openWithCallback"):
            self["message"].setText(_("Could not open the virtual keyboard."))
            return
        self.session.openWithCallback(
            self._search_entered,
            VirtualKeyBoard,
            title=_("Search XMLTV channels"),
            text=self.query,
        )

    def _search_entered(self, value):
        if value is None:
            return
        self.query = str(value or "").strip()[:128]
        key = normalise_channel_name(self.query)
        if key:
            self._entries = [
                channel
                for channel in self._all_channels
                if key in normalise_channel_name(
                    "{} {}".format(channel.display_name, channel.xmltv_id)
                )
            ]
        else:
            self._entries = list(self._all_channels)
        self.selected_index = 0
        self._refresh_rows()

    def clear_search(self):
        self.query = ""
        self._entries = list(self._all_channels)
        self.selected_index = 0
        self["message"].setText(_("XMLTV channel filter cleared."))
        self._refresh_rows()


# Compatibility spellings for callers following the project's mixed acronym
# style.  The canonical class names above match the Enigma2 screen names.
GTDvbEpgSettingsScreen = GTDVBEPGSettingsScreen
GTDvbEpgAutomationScreen = GTDVBEPGAutomationScreen
GTDvbEpgMappingScreen = GTDVBEPGMappingScreen
GTXmltvChannelSelectScreen = GTXMLTVChannelSelectScreen

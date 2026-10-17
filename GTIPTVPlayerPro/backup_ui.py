# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-local user-data backup and restore, controlled with the remote."""

import datetime
import os
import threading

from Components.ActionMap import ActionMap
from Components.FileList import FileList
from Components.Label import Label
from Components.ProgressBar import ProgressBar
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from enigma import eTimer, getDesktop

from .backup import BackupError, BackupManager, CONFIG_PREFIX, EXTENSION, SETTINGS_PATH
from .background import attach_background
from .diagnostics import log_event
from .i18n import N_, _
from .paths import plugin_path
from .remote_footer import decorate_remote_footer, footer_item, install_remote_footer
from .smooth_scroll import GTSmoothScrollLabel
from .typography import font_px


ERROR_MESSAGES = {
    "invalid": N_("Invalid or damaged backup."),
    "empty": N_("No files in the selected sections."),
    "version": N_("Backup version is not supported."),
    "storage": N_("Storage is unavailable or not writable."),
    "space": N_("Not enough free space."),
    "busy": N_("Pause downloads before backup or restore."),
    "source": N_("A source file is missing or unreadable."),
    "changed": N_("Files changed during backup. Try again."),
    "cancelled": N_("Cancelled"),
    "failed": N_("Backup or restore failed."),
    "recovery": N_("Restore failed. Use the safety backup."),
}
PHASE_MESSAGES = {
    "plan": N_("Please wait"),
    "backup": N_("Creating backup..."),
    "safety": N_("Safety backup"),
    "verify": N_("Checking backup..."),
    "restore": N_("Restoring backup..."),
}
SCOPE_MESSAGE = N_("Includes accounts, passwords, settings, favorites, history and local source files.")
EXCLUSIONS_MESSAGE = N_("Videos, plugin files and rebuildable caches are excluded.")
REQUIRED_SPACE_MESSAGE = N_("Required space")
RESTORE_CONFIRM = N_("Restore this backup? Current plugin data will be replaced. A safety backup will be created first.")


def _scale():
    try:
        size = getDesktop(0).size()
        width, height = int(size.width()), int(size.height())
    except Exception:
        width, height = 1280, 720
    factor = min(width / 1920.0, height / 1080.0)
    return width, height, lambda value: max(1, int(round(value * factor)))


def _timer_connect(timer, callback):
    try:
        return timer.timeout.connect(callback)
    except AttributeError:
        timer.callback.append(callback)


def _size(value):
    for unit, divisor in (("GB", 1024**3), ("MB", 1024**2), ("KB", 1024)):
        if value >= divisor:
            return "{:.1f} {}".format(value / float(divisor), unit)
    return "{} B".format(value)


def _footer(mode, picker=False):
    items = (
        footer_item("arrows", "navigate"),
        footer_item("green", "select" if picker else N_("Restore backup") if mode == "restore" else N_("Backup")),
        footer_item("blue", N_("File path")),
        footer_item("exit", "back"),
    )
    if mode == "restore" and not picker:
        items = items[:-1] + (footer_item("yellow", N_("Safety backup")),) + items[-1:]
    return items


def _skin(mode, picker=False):
    width, height, px = _scale()
    py = lambda value: max(1, int(round(value * height / 1080.0)))
    parts = ['<screen name="{}" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="#050914">'.format(
             "GTBackupPickerScreen" if picker else "GTBackupScreen", width, height),
             '<widget name="app_bg" position="0,0" size="{},{}" zPosition="0" />'.format(width, height)]
    for name, x, y, w, h, size, color in (
            ("title", 100, 90, 1720, 88, 44, "#FFFFFF"),
            ("path", 100, 202, 1720, 92, 24, "#B9D9EC"),
            ("message", 100, 883, 1720, 86, 23, "#16C9F4"),
            ("footer", 0, 994, 1920, 86, 22, "#FFFFFF")):
        parts.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" transparent="1" zPosition="3" valign="center" />'.format(
            name, px(x) if x else 0, py(y), px(w), py(h), font_px(px, size, max_height=h), color))
    if picker:
        parts.append('<widget name="filelist" position="{},{}" size="{},{}" font="Regular;{}" itemHeight="{}" backgroundColor="#0A1425" foregroundColor="#FFFFFF" scrollbarMode="showOnDemand" zPosition="2" />'.format(
            px(100), py(316), px(1720), py(536), font_px(px, 25), py(48)))
    else:
        parts.append('<widget name="detail" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="#FFFFFF" transparent="1" zPosition="3" scrollbarMode="showNever" />'.format(
            px(100), py(326), px(1720), py(446), font_px(px, 28)))
        parts.append('<widget name="progress" position="{},{}" size="{},{}" foregroundColor="#16C9F4" backgroundColor="#24354D" borderWidth="0" zPosition="3" />'.format(
            px(100), py(820), px(1720), py(18)))
    return decorate_remote_footer("\n".join(parts) + "</screen>", _footer(mode, picker),
        transparent_panel=True, show_dividers=False, skin_fonts_scaled=True)


class GTBackupPickerScreen(Screen):
    def __init__(self, session, mode, current="", title=None):
        self.mode = mode
        self.skin = _skin(mode, picker=True)
        Screen.__init__(self, session)
        attach_background(self, "app_bg", plugin_path("skin", "images", "screen-settings-glass-r90.png"))
        title = title or (_("Backup file") if mode == "restore" else _("Backup folder"))
        self["title"] = Label(title)
        self["path"] = Label("")
        self["message"] = Label(_("Storage"))
        self["footer"] = Label("")
        install_remote_footer(self, _footer(mode, picker=True))
        if current and os.path.isfile(current):
            current = os.path.dirname(current)
        directory = os.path.realpath(current) if current and os.path.isdir(current) else "/media/"
        self["filelist"] = FileList(directory, showDirectories=True, showFiles=mode == "restore",
            showMountpoints=True, matchingPattern=r"(?i).*\.gtbackup$" if mode == "restore" else None,
            useServiceRef=False, inhibitDirs=False, inhibitMounts=False)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self.open_current, "green": self.select, "blue": self.storage,
            "cancel": lambda: self.close(None), "red": lambda: self.close(None),
            "up": lambda: self.move("up"), "down": lambda: self.move("down"),
            "upRepeated": lambda: self.move("up"), "downRepeated": lambda: self.move("down"),
            "left": lambda: self.move("pageUp"), "right": lambda: self.move("pageDown"),
        }, -1)
        self.onLayoutFinish.append(self.draw)
        self.setTitle(title)

    def draw(self):
        self["path"].setText(self["filelist"].getCurrentDirectory() or _("Storage"))

    def move(self, method):
        getattr(self["filelist"], method)()
        self.draw()

    def open_current(self):
        if self["filelist"].canDescent():
            self["filelist"].descent()
            self.draw()
        elif self.mode == "restore":
            self.select()

    def select(self):
        if self.mode == "backup":
            path = self["filelist"].getCurrentDirectory()
            if path and os.path.isdir(path) and os.access(path, os.W_OK | os.X_OK):
                self.close(os.path.realpath(path))
                return
        elif not self["filelist"].canDescent():
            filename = self["filelist"].getFilename()
            if filename:
                path = str(filename)
                if not os.path.isabs(path):
                    path = os.path.join(self["filelist"].getCurrentDirectory() or "", path)
                if path.lower().endswith(EXTENSION) and os.path.isfile(path) and not os.path.islink(path):
                    self.close(os.path.realpath(path))
                    return
        self["message"].setText(_(ERROR_MESSAGES["storage"]))

    def storage(self):
        # FileList's virtual root lists both local and mounted network volumes.
        self["filelist"].changeDir(None)
        self.draw()


def _active_downloads():
    from . import downloads
    return any(job.get("status") in downloads.ACTIVE + ("queued", "deleting")
               for job in downloads.snapshot().get("jobs", []))


def apply_native_settings():
    """Update live ConfigElements so GUI shutdown cannot overwrite restored keys."""
    from .settings import plugin_config_section
    section = plugin_config_section()
    if section is None:
        return
    from .backup import native_settings
    with open(SETTINGS_PATH, "rb") as handle:
        fragment = ("\n".join(line for line in handle.read().decode("utf-8").splitlines()
                              if line.startswith(CONFIG_PREFIX)) + "\n").encode("utf-8")
    values = {}
    for line in native_settings(fragment):
        key, _, value = line.partition("=")
        parts = key[len(CONFIG_PREFIX):].split(".")
        cursor = values
        for name in parts[:-1]:
            cursor = cursor.setdefault(name, {})
        cursor[parts[-1]] = value
    section.setSavedValue(values)
    for name, element in section.dict().items():
        element.saved_value = values.get(name)
    section.load()


class GTBackupScreen(Screen):
    def __init__(self, session, mode="backup"):
        self.mode = mode
        from . import backup_dashboard, restore_dashboard
        dashboard = restore_dashboard if mode == "restore" else backup_dashboard
        self.skin = dashboard.skin()
        Screen.__init__(self, session)
        self.manager = BackupManager(active_downloads=_active_downloads)
        self.state = "choose"
        self.location = ""
        self.safety_folder = ""
        self.manifest = None
        self._thread = None
        self._result = None
        self._error = None
        self._progress = ("plan", 0, 0)
        self._cancel = threading.Event()
        self._suspended = False
        self._web_was_active = False
        self._restored = False
        self._first = True
        self._screen_closed = False
        self._notice_pending = False
        self._timer = eTimer()
        self._connection = _timer_connect(self._timer, self.poll)
        self._picker_timer = eTimer()
        self._picker_connection = _timer_connect(self._picker_timer, self._open_initial_picker)
        self._notice_timer = eTimer()
        self._notice_connection = _timer_connect(self._notice_timer, self._show_backup_notice)
        background = dashboard.asset("background")
        attach_background(self, "app_bg", background)
        self["title"] = Label(_("Restore from backup") if mode == "restore" else _("Backup dashboard"))
        self["path"] = Label("")
        # ScrollLabel creates a container and child widgets. Some receiver
        # images do not apply the overlay's z-order to that whole composite.
        # A native Label receives the dashboard skin and zPosition directly.
        self["detail"] = Label(_(SCOPE_MESSAGE))
        self["progress"] = ProgressBar()
        self["message"] = Label("")
        self["footer"] = Label("")
        if mode == "backup":
            from .settings import plugin_config_section
            section = plugin_config_section()
            self.location = getattr(getattr(section, "backup_folder", None), "value", "") or "/media/hdd"
            self["path"].setText(self.location)
        dashboard.setup(self)
        install_remote_footer(self, dashboard.footer(), fit_horizontal=True)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self.toggle_group,
            "green": self.begin_backup, "blue": self.choose,
            "yellow": self.toggle_all if mode == "backup" else self.choose_safety,
            "cancel": self.cancel, "red": self.cancel,
            "up": lambda: self.navigate("up"), "down": lambda: self.navigate("down"),
            "left": lambda: self.navigate("left"), "right": lambda: self.navigate("right"),
            "upRepeated": lambda: self.navigate("up"), "downRepeated": lambda: self.navigate("down"),
            "leftRepeated": lambda: self.navigate("left"), "rightRepeated": lambda: self.navigate("right"),
        }, -1)
        self.onExecBegin.append(self.first_show)
        self.onClose.append(self._closed)
        self.setTitle(_("Restore from backup") if mode == "restore" else _("Backup dashboard"))

    def first_show(self):
        if self._first and not self._screen_closed:
            # OpenPLi applies the skin before Session.open makes this screen
            # modal. Even onExecBegin precedes component activation, so open
            # the child only after the current event-loop turn has finished.
            self._picker_timer.start(0, True)

    def _open_initial_picker(self):
        if (self._screen_closed or not self._first
                or not self.execing or not self.session.in_exec
                or self.session.current_dialog is not self):
            return
        self._first = False
        if self.mode == "backup":
            self._start("preflight")
        else:
            self.refresh_dashboard()

    def refresh_dashboard(self):
        if not self._screen_closed:
            if self.mode == "restore":
                from .restore_dashboard import refresh
            else:
                from .backup_dashboard import refresh
            refresh(self, _size)

    def _show_backup_notice(self):
        if (self._screen_closed or not self._notice_pending
                or self.state != "success"):
            return
        if (not self.execing or not self.session.in_exec
                or self.session.current_dialog is not self):
            # Wait until this screen is modal; opening a MessageBox from an
            # inactive parent can crash OpenPLi. Stop permanently on close.
            self._notice_timer.start(200, True)
            return
        self._notice_pending = False
        self.session.openWithCallback(self._backup_notice_closed, MessageBox,
            self["detail"].getText(), MessageBox.TYPE_INFO)

    def _backup_notice_closed(self, *unused):
        if self._screen_closed or self.state != "success":
            return
        if self.mode == "restore":
            self.restart()
            return
        # The notice was acknowledged: reveal the existing dashboard again
        # instead of leaving a full progress bar covering the selection cards.
        self.state = "ready"
        self["progress"].setValue(0)
        self.refresh_dashboard()
        self["message"].setText(_("Backup completed."))

    def navigate(self, direction):
        if self._thread is not None or self.state != "ready" or self._restored:
            return
        from .backup_scope import GROUPS
        available = (set(GROUPS) if self.mode == "backup" else self.available_groups)
        row, column = divmod(self.focus_index, 2)
        if direction in ("up", "down"):
            step = -1 if direction == "up" else 1
            candidates = [((row + step * offset) % 3) * 2 + column for offset in (1, 2)]
        else:
            step = -1 if direction == "left" else 1
            candidates = [row * 2 + 1 - column]
        # An archive may have only diagonal cards available. Fall back to
        # the next available card so no enabled section becomes unreachable.
        candidates += [(self.focus_index + step * offset) % 6 for offset in range(1, 6)]
        for index in candidates:
            if GROUPS[index] in available:
                self.focus_index = index
                break
        self.refresh_dashboard()

    def toggle_group(self):
        if self._thread is not None:
            return
        if self._restored:
            self.restart()
        elif self.state == "success":
            self.close(True)
        elif self.mode == "restore" and self.manifest is None:
            self.choose()
        elif self.state == "ready":
            from .backup_scope import GROUPS
            group = GROUPS[self.focus_index]
            if self.mode == "restore" and group not in self.available_groups:
                return
            if group in self.selected_groups:
                self.selected_groups.remove(group)
            else:
                self.selected_groups.add(group)
            self.refresh_dashboard()

    def toggle_all(self):
        if self._thread is None and self.state == "ready":
            from .backup_scope import GROUPS
            self.selected_groups = set() if len(self.selected_groups) == len(GROUPS) else set(GROUPS)
            self.refresh_dashboard()

    def begin_backup(self):
        if self._thread is not None:
            return
        if self.mode == "restore" and self.manifest is None:
            self.choose()
            return
        if self.state == "ready":
            if not self.selected_groups:
                self["message"].setText(_("Select at least one section."))
                return
            inventory = self.restore_inventory if self.mode == "restore" else self.manifest["files"]
            records = self.manager.select_records(inventory, self.selected_groups)
            if not records and not (self.mode == "restore" and self.manifest.get("purpose") == "safety"):
                self["message"].setText(_("No files in the selected sections."))
                return
        self.activate()

    def choose(self):
        if self._screen_closed or self._thread is not None or self._restored:
            return
        self._first = False
        self._picker_timer.stop()
        from .settings import plugin_config_section
        section = plugin_config_section()
        field = "restore_folder" if self.mode == "restore" else "backup_folder"
        current = self.location or getattr(getattr(section, field, None), "value", "")
        self.session.openWithCallback(self.selected, GTBackupPickerScreen, self.mode, current)

    def selected(self, path):
        if not path:
            self.refresh_dashboard()
            return
        self.location = path
        self["path"].setText(path)
        from .settings import plugin_config_section, save_plugin_config
        section = plugin_config_section()
        if section is not None:
            if self.mode == "backup":
                section.backup_folder.value = path
            else:
                section.restore_folder.value = os.path.dirname(path)
            save_plugin_config(section)
        if self.mode == "restore" and not self.safety_folder:
            candidates = (os.path.dirname(path), getattr(getattr(section, "backup_folder", None), "value", ""))
            for folder in candidates:
                if not folder:
                    continue
                try:
                    self.safety_folder = self.manager.destination(folder)
                    break
                except (BackupError, OSError):
                    continue
        if self.mode == "restore":
            self.manifest = None
            self.restore_inventory = []
            self.available_groups = set()
            self.selected_groups = set()
        self._start("preflight")

    def choose_safety(self):
        if self.mode != "restore" or self._thread is not None or self._restored:
            return
        self.session.openWithCallback(self.safety_selected, GTBackupPickerScreen, "backup",
            self.safety_folder or os.path.dirname(self.location),
            title=_("Safety backup") + " · " + _("Backup folder"))

    def safety_selected(self, path):
        if not path:
            return
        try:
            self.safety_folder = self.manager.destination(path)
        except (BackupError, OSError):
            self["message"].setText(_(ERROR_MESSAGES["storage"]))
            return
        from .settings import plugin_config_section, save_plugin_config
        section = plugin_config_section()
        if section is not None:
            section.backup_folder.value = self.safety_folder
            save_plugin_config(section)
        if self.state == "ready":
            self.show_summary()

    def _suspend(self):
        if self._suspended:
            return
        self._suspended = True
        from .web_shortcut import uninstall
        uninstall()
        for name in ("dvb_epg_scheduler", "m3u_epg_scheduler"):
            from importlib import import_module
            import_module("." + name, __package__).session_start(1, session=self.session)
        from .web_server import get_runtime
        runtime = get_runtime()
        status = runtime.status()
        self._web_was_active = bool(status.get("active") or status.get("starting"))
        runtime.stop()

    def _resume(self):
        if not self._suspended or self._restored:
            return
        self._suspended = False
        from .web_shortcut import install
        install(self.session)
        for name in ("dvb_epg_scheduler", "m3u_epg_scheduler"):
            try:
                from importlib import import_module
                import_module("." + name, __package__).session_start(0, session=self.session)
            except Exception as error:
                log_event("backup", "EPG scheduler could not resume", error)
        if self._web_was_active:
            try:
                from .web_server import get_runtime
                get_runtime().start()
            except Exception as error:
                log_event("backup", "Web access could not resume", error)

    def _start(self, action):
        if self._thread is not None:
            return
        self._notice_pending = False
        self._notice_timer.stop()
        self.state = action
        self._result = None
        self._error = None
        self._progress = ("plan", 0, 0)
        self._cancel.clear()
        self["progress"].setValue(0)
        self["message"].setText(_("Please wait"))
        try:
            if action != "preflight":
                self._suspend()
        except Exception as error:
            self._error = error
            self.failed()
            return

        def run():
            try:
                if action == "preflight":
                    if self.mode == "restore":
                        self._result = self.manager.inspect(self.location, self.progress, self._cancel, details=True)
                    else:
                        inventory = self.manager.inventory(safety=True)
                        total = sum(entry["size"] for entry in inventory)
                        self._result = dict(files=inventory, total_bytes=total)
                elif self.mode == "restore":
                    self._result = self.manager.restore(self.location, self.progress, self._cancel,
                        approved_manifest=self.manifest, safety_folder=self.safety_folder or None,
                        groups=tuple(self.selected_groups))
                else:
                    self._result = self.manager.create(self.location, self.progress, self._cancel,
                                                       groups=tuple(self.selected_groups))
            except Exception as error:
                self._error = error

        if action != "preflight":
            self["detail"].setText(_("Restoring backup...") if self.mode == "restore" else _("Creating backup..."))
        self.refresh_dashboard()
        self._thread = threading.Thread(target=run, name="GTIPTVBackup", daemon=True)
        self._thread.start()
        self._timer.start(150, False)

    def progress(self, phase, completed, total):
        self._progress = (phase, completed, total)

    def poll(self):
        if self._thread is None:
            return
        phase, completed, total = self._progress
        self["message"].setText(_(PHASE_MESSAGES.get(phase, PHASE_MESSAGES["plan"])))
        self["progress"].setValue(min(100, int(100 * completed / max(1, total))))
        if self._thread.is_alive():
            return
        self._timer.stop()
        self._thread = None
        if self._error is not None:
            self.failed()
            return
        if self.state == "preflight":
            if self.mode == "restore":
                from .backup_scope import GROUPS
                self.manifest = self._result["manifest"]
                self.restore_inventory = self._result["files"]
                self.available_groups = set(self._result["groups"])
                self.selected_groups = set(self.available_groups)
                self.focus_index = next((i for i, group in enumerate(GROUPS) if group in self.available_groups), 0)
            else:
                self.manifest = self._result
            self.state = "ready"
            self.show_summary()
            self["progress"].setValue(0)
            return
        self.state = "success"
        self["progress"].setValue(100)
        if self.mode == "restore":
            from . import downloads
            try:
                downloads.reload_after_restore()
            except Exception as error:
                log_event("backup", "Download state refresh failed", error)
                # Never let current-boot process state hide restored history.
                runtime_state = os.path.join(downloads.RUNTIME, "state.json")
                if os.path.isfile(runtime_state) and not os.path.islink(runtime_state):
                    os.unlink(runtime_state)
            try:
                apply_native_settings()
            except Exception as error:
                log_event("backup", "Live settings refresh failed; interface restart is required", error)
                # Reload the stored values before shutdown as an image fallback.
                from Components.config import configfile
                configfile.load()
                try:
                    apply_native_settings()
                except Exception as retry_error:
                    log_event("backup", "Live settings refresh requires receiver restart", retry_error)
            self._restored = True
            self["detail"].setText(_("Restore completed. Restart the Enigma2 interface.")
                + "\n\n" + _("Safety backup") + ":\n" + self._result["safety"])
            self["message"].setText(_("Restart required"))
            self.refresh_dashboard()
            self._notice_pending = True
            self._notice_timer.start(0, True)
        else:
            self._resume()
            path = self._result["path"]
            # This path is returned only after the archive has been flushed
            # and atomically installed. Size is the completed archive's size.
            try:
                size = _size(os.path.getsize(path))
            except OSError:
                size = _size(self._result["manifest"]["total_bytes"])
            entries = self._result["manifest"]["files"]
            self["detail"].setText(_("Backup completed.") + "\n\n"
                + _("Files: {} | Size: {}").format(len(entries), size)
                + "\n\n" + _("Backup file") + ":\n" + path)
            self["message"].setText(_("Backup completed."))
            self.refresh_dashboard()
            self._notice_pending = True
            self._notice_timer.start(0, True)

    def show_summary(self):
        self.refresh_dashboard()

    def failed(self):
        self.state = "error"
        self._resume()
        code = getattr(self._error, "code", "failed")
        message = _(ERROR_MESSAGES.get(code, ERROR_MESSAGES["failed"]))
        detail = getattr(self._error, "detail", "")
        if code == "space" and hasattr(self._error, "available_bytes"):
            detail = "\n".join((
                _("Storage") + ": " + detail,
                _("Free space") + ": " + _size(self._error.available_bytes),
                _(REQUIRED_SPACE_MESSAGE) + ": " + _size(self._error.required_bytes)))
        self["detail"].setText(message + ("\n\n" + detail if detail else ""))
        self["message"].setText(message)
        log_event("backup", "Backup operation failed: " + code)
        self.refresh_dashboard()

    def activate(self):
        if self._thread is not None:
            return
        if self._restored:
            self.restart()
        elif self.state == "ready":
            if self.mode == "restore" and not self.safety_folder:
                self.choose_safety()
                return
            question = (_("Restore the selected sections? Unselected sections are preserved. A safety backup will be created first.")
                        if self.mode == "restore" else _("Create backup?"))
            self.session.openWithCallback(lambda answer: self._start("running") if answer else None,
                MessageBox, question, MessageBox.TYPE_YESNO, default=False)
        elif self.state == "success":
            self.close(True)
        else:
            self.choose()

    def restart(self):
        if self._restored:
            from Screens.Standby import TryQuitMainloop
            self.session.open(TryQuitMainloop, 3)

    def cancel(self):
        if self._restored:
            self.restart()
        elif self._thread is not None:
            if self.state == "preflight" or self.mode == "backup":
                self._cancel.set()
            self["message"].setText(_("Please wait"))
        else:
            self.close(False)

    def _closed(self):
        self._screen_closed = True
        self._notice_pending = False
        self._notice_timer.stop()
        self._picker_timer.stop()
        self._timer.stop()
        self._cancel.set()
        self._resume()

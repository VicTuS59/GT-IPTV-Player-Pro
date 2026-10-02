# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remote-controlled download history and local recording browser."""
import os
import threading
import time

from Components.ActionMap import ActionMap
from Components.FileList import FileList
from Components.Label import Label
from Components.Pixmap import Pixmap
from Components.ProgressBar import ProgressBar
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from enigma import eTimer, getDesktop

from . import downloads as manager
from .background import attach_background, attach_pixmap
from .channel_highlight import apply_selection_color
from .i18n import N_, _
from .paths import plugin_path
from .remote_footer import decorate_remote_footer, footer_item, install_remote_footer, set_remote_footer
from .typography import font_px, fit_dynamic_text, ellipsize_dynamic_text, clean_dynamic_text
from .smooth_scroll import GTSmoothScrollLabel
from .local_media import MediaFileError, inspect_media
from .diagnostics import log_event


DOWNLOAD_FOOTER = (
    footer_item("red", "cancel"),
    footer_item("green", N_("Pause / Resume")),
    footer_item("yellow", N_("Delete")),
    footer_item("blue", N_("Save folder")),
    footer_item("menu", N_("Speed limit")),
    footer_item("ok", "play"),
    footer_item("exit", "back"),
)
DOWNLOAD_DETAILS_FOOTER = (
    DOWNLOAD_FOOTER[:5] + (footer_item("ok", N_("Details")),) + DOWNLOAD_FOOTER[6:]
)
FOLDER_FOOTER = (
    footer_item("arrows", "navigate"),
    footer_item("ok", "open"),
    footer_item("green", "save"),
    footer_item("exit", "cancel"),
)
FOLDER_DESIGN_SIZE = (1300, 800)


def _scale():
    try:
        size = getDesktop(0).size()
        width, height = int(size.width()), int(size.height())
        if width <= 0 or height <= 0:
            raise ValueError("Desktop dimensions are unavailable")
    except Exception:
        width, height = 1280, 720
    factor = min(float(width) / 1920.0, float(height) / 1080.0)
    return width, height, lambda value: max(1, int(round(value * factor)))


def _timer(timer, callback):
    try:
        return timer.timeout.connect(callback)
    except AttributeError:
        timer.callback.append(callback)


def _size(value):
    value = max(0, int(value or 0))
    if value >= 1024**3:
        return "{:.2f} GB".format(value / float(1024**3))
    if value >= 1024**2:
        return "{:.1f} MB".format(value / float(1024**2))
    return "{} KB".format(value // 1024)


def _rate(value):
    return ("{:g} MB/s".format(value / float(1024**2)) if value >= 1024**2
            else "{:g} KB/s".format(value / 1024.0))


def _eta(value):
    value = max(0, int(value or 0))
    return "{:02d}:{:02d}:{:02d}".format(value // 3600, value // 60 % 60, value % 60) if value else "—"


def _label(name, x, y, w, h, px, size=26, color="#FFFFFF", background=None, align="left", z=3, valign="center"):
    return ('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" '
            'foregroundColor="{}" {} transparent="{}" zPosition="{}" '
            'valign="{}" halign="{}" />').format(name, px(x), px(y), px(w), px(h),
        font_px(px, size, max_height=h, vertical_padding=2), color,
        'backgroundColor="{}"'.format(background) if background else "",
        "0" if background else "1", z, valign, align)


def _scroll_label(name, x, y, w, h, px, size, color="#FFFFFF"):
    return ('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" '
            'foregroundColor="{}" backgroundColor="#0B1731" transparent="0" '
            'zPosition="4" scrollbarMode="showNever" scrollbarWidth="0" scrollbarMargin="0" '
            'valign="top" halign="left" />').format(
                name, px(x), px(y), px(w), px(h), font_px(px, size), color)


def _skin():
    width, height, px = _scale()
    parts = ['<screen name="GTDownloadsScreen" position="0,0" size="{},{}" '
             'flags="wfNoBorder" backgroundColor="#050C22">'.format(width, height),
             '<widget name="app_bg" position="0,0" size="{},{}" zPosition="0" />'.format(width, height)]
    specs = [
        ("brand", 60, 25, 1200, 45, 30, "#D4E2FF"),
        ("crumb", 60, 78, 1300, 38, 24, "#AFC3EC"),
        ("title", 60, 122, 1150, 65, 48, "#FFFFFF"),
        ("summary", 60, 191, 1130, 40, 24, "#AFC3EC"),
        ("free", 1360, 70, 495, 50, 25, "#D4E2FF"),
        ("detail_bg", 1350, 240, 510, 624, 1, "#FFFFFF", "#0B1731"),
        ("detail_heading", 1375, 245, 465, 52, 28, "#FFFFFF"),
        ("poster_placeholder", 1455, 307, 300, 245, 48, "#1CA7D0", "#101F40", "center"),
        ("folder_bg", 60, 881, 1800, 50, 1, "#FFFFFF", "#0C1830"),
        ("folder", 80, 881, 1760, 50, 23, "#BDD0EC"),
        ("message", 60, 935, 1800, 30, 21, "#21CBEC"),
        ("footer", 60, 978, 1800, 98, 22, "#FFFFFF"),
        ("arrow_up", 1295, 240, 40, 45, 28, "#BCD7FF", None, "center"),
        ("arrow_down", 1295, 821, 40, 45, 28, "#BCD7FF", None, "center"),
        ("page", 1195, 191, 135, 40, 22, "#BCD7FF", None, "right"),
    ]
    for spec in specs:
        name, x, y, w, h, size, color = spec[:7]
        parts.append(_label(name, x, y, w, h, px, size, color, *spec[7:]))
    parts.append('<widget name="poster" position="{},{}" size="{},{}" zPosition="4" alphatest="on" />'.format(
        px(1455), px(307), px(300), px(245)))
    parts.append(_scroll_label("detail_title", 1375, 566, 455, 146, px, 28))
    parts.append(_scroll_label("detail", 1375, 724, 455, 130, px, 21, "#C4D5F0"))
    for row in range(5):
        y = 240 + row * 126
        parts.extend([
            _label("row_{}".format(row), 60, y, 1230, 120, px, 1, background="#0C1830", z=1),
            _label("focus_{}".format(row), 60, y, 1230, 120, px, 1, background="#134875", z=2),
            _label("marker_{}".format(row), 60, y, 5, 120, px, 1, background="#17D6F2", z=3),
            _label("placeholder_{}".format(row), 78, y+9, 180, 98, px, 32, "#22CBE9", "#10223C", "center"),
            '<widget name="thumb_{}" position="{},{}" size="{},{}" zPosition="4" alphatest="on" />'.format(row, px(78), px(y+9), px(180), px(98)),
            _label("name_{}".format(row), 285, y+6, 630, 78, px, 26, valign="top"),
            _label("meta_{}".format(row), 285, y+89, 630, 27, px, 19, "#B3C7E6"),
            _label("status_{}".format(row), 935, y+6, 330, 76, px, 25, "#1ED0EE", valign="top"),
            '<widget name="progress_{}" position="{},{}" size="{},{}" borderWidth="0" foregroundColor="#1DD1EC" backgroundColor="#344D74" zPosition="4" />'.format(row, px(940), px(y+96), px(245), px(10)),
            _label("percent_{}".format(row), 1195, y+84, 80, 32, px, 20, "#C6D7F0", align="right"),
        ])
    return decorate_remote_footer(
        "\n".join(parts) + "</screen>", DOWNLOAD_FOOTER,
        alternate_items=DOWNLOAD_DETAILS_FOOTER,
        transparent_panel=True, show_dividers=False, stacked=True, skin_fonts_scaled=True)


def _folder_skin():
    width, height, px = _scale()
    skin = '<screen name="GTDownloadFolderScreen" position="center,center" size="{},{}" backgroundColor="#101C39">'.format(
        px(FOLDER_DESIGN_SIZE[0]), px(FOLDER_DESIGN_SIZE[1]))
    skin += _label("title", 40, 20, 1220, 60, px, 34)
    skin += _label("path", 40, 90, 1220, 55, px, 23, "#21CDEB")
    skin += '<widget name="filelist" position="{},{}" size="{},{}" scrollbarMode="showOnDemand" />'.format(
        px(40), px(165), px(1220), px(510))
    skin += _label("footer", 40, 710, 1220, 60, px, 24) + "</screen>"
    return decorate_remote_footer(
        skin, FOLDER_FOOTER, transparent_panel=True, show_dividers=False,
        stacked=True, skin_fonts_scaled=True, design_size=FOLDER_DESIGN_SIZE)


class GTDownloadsScreen(Screen):
    def __init__(self, session, selected_id=""):
        self.skin = _skin()
        Screen.__init__(self, session)
        self._closed = False
        self._visible = False
        self._entries = []
        self._locals = []
        self._scan_result = None
        self._scan_generation = 0
        self._operation = None
        self._scanning = False
        self._scan_at = 0
        self._scan_folder = ""
        self._free = None
        self._posters = {}
        self.index = 0
        self._start = 0
        self._initial_id = selected_id
        self._queue_held = True
        attach_background(self, "app_bg", plugin_path("skin", "images", "global-neon-v0912.png"))
        for name in ("brand", "crumb", "title", "summary", "free", "detail_bg", "detail_heading",
                     "poster_placeholder", "folder_bg", "folder", "message",
                     "footer", "arrow_up", "arrow_down", "page"):
            self[name] = Label("")
        self["detail_title"] = GTSmoothScrollLabel("")
        self["detail"] = GTSmoothScrollLabel("")
        self["poster"] = Pixmap()
        self["brand"].setText("GT IPTV PLAYER PRO")
        self["crumb"].setText(_("Settings") + "  ›  " + _("Downloads"))
        self["title"].setText(_("Downloads"))
        self["detail_heading"].setText(_("Download details"))
        self["poster_placeholder"].setText("GT")
        install_remote_footer(self, DOWNLOAD_FOOTER, stacked=True)
        self["arrow_up"].setText("▲")
        self["arrow_down"].setText("▼")
        for row in range(5):
            for name in ("row", "focus", "marker", "placeholder", "name", "meta", "status", "percent"):
                self["{}_{}".format(name, row)] = Label("GT" if name == "placeholder" else "")
            self["thumb_{}".format(row)] = Pixmap()
            self["progress_{}".format(row)] = ProgressBar()
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions", "MenuActions"], {
            "ok": self.open_selected, "cancel": self.close, "up": lambda: self.move(-1),
            "down": lambda: self.move(1), "upRepeated": lambda: self.move(-1),
            "downRepeated": lambda: self.move(1), "left": lambda: self.move(-5), "right": lambda: self.move(5),
            "red": self.cancel_selected, "green": self.toggle_pause, "yellow": self.delete_selected,
            "blue": self.choose_folder, "menu": self.choose_speed}, -1)
        self.timer = eTimer()
        self._timer_connection = _timer(self.timer, self.refresh)
        self.onShown.append(self._shown)
        if hasattr(self, "onHide"):
            self.onHide.append(self._suspend)
        self.onLayoutFinish.append(self._fit_labels)
        self.onLayoutFinish.append(self._raise_detail_layers)
        self.onClose.append(self._close)
        self.setTitle(_("Downloads"))

    def _close(self):
        self._closed = True
        self._suspend()

    def _suspend(self):
        self._visible = False
        self.timer.stop()
        self["detail_title"].stop_scroll()
        self["detail"].stop_scroll()

    def _shown(self):
        self._visible = True
        self._raise_detail_layers()
        self.refresh()
        if self._visible and not getattr(self, "_closed", True):
            self["detail_title"].restart_scroll()
            self["detail"].restart_scroll()

    def _raise_detail_layers(self):
        # OpenPLi ScrollLabel applies the XML zPosition to long_text, not
        # its enclosing eWidget. Raise that enclosing widget above the
        # opaque detail panel as well; raising only its child cannot do so.
        # On SF8008 this is the canvas itself, which uses the same layer.
        for name in ("detail_title", "detail"):
            instance = getattr(self[name], "instance", None)
            setter = getattr(instance, "setZPosition", None)
            if callable(setter):
                setter(5)

    def _fit_labels(self):
        for name in ("brand", "crumb", "title", "detail_heading"):
            fit_dynamic_text(self[name], self[name].getText(), max_lines=1)

    def _scan(self, folder):
        self._scanning = True
        mailbox = {"done": threading.Event(), "generation": self._scan_generation}
        self._scan_result = mailbox
        def work():
            try:
                entries = manager.local_files(folder)
                free = manager.free_bytes(folder) if folder else None
            except (OSError, ValueError):
                entries, free = [], None
            mailbox["result"] = (folder, entries, free)
            mailbox["done"].set()
        worker = threading.Thread(target=work)
        worker.daemon = True
        worker.start()

    def refresh(self):
        if getattr(self, "_closed", True):
            return
        try:
            self.timer.stop()
            if self._poll_operation():
                return
            if getattr(self, "_closed", True):
                return
            state = manager.snapshot()
            self._queue_held = bool(state.get("hold"))
            folder = state.get("folder") or ""
            if self._scan_result is not None and self._scan_result["done"].is_set():
                scanned, entries, free = self._scan_result["result"]
                generation = self._scan_result["generation"]
                self._scan_result = None
                self._scanning = False
                if scanned == folder and generation == self._scan_generation:
                    self._locals, self._free = entries, free
                    self._scan_folder = scanned
            if folder != self._scan_folder:
                self._locals, self._free = [], None
            if not self._scanning and (time.monotonic() >= self._scan_at or folder != self._scan_folder):
                self._scan_at = time.monotonic() + 8
                self._scan(folder)
            selected = self.selected().get("id") or self._initial_id
            jobs = list(state.get("jobs") or [])
            known = {j.get("path") for j in jobs if j.get("status") == "completed"}
            self._entries = jobs + [entry for entry in self._locals if entry["path"] not in known]
            if selected:
                self.index = next((i for i, j in enumerate(self._entries) if j["id"] == selected), self.index)
                self._initial_id = ""
            self.index = min(max(0, self.index), max(0, len(self._entries)-1))
            ellipsize_dynamic_text(self["free"], _("Free space") + ": " + (_size(self._free) if self._free is not None else "—"))
            ellipsize_dynamic_text(self["folder"], _("Save folder") + ": " + (folder or _("Not configured")))
            counts = []
            for statuses, label in ((manager.ACTIVE, "Downloading"), (("queued",), "Queued"),
                                    (("completed",), "Completed"), (("error",), "Error")):
                counts.append("{} {}".format(sum(j["status"] in statuses for j in jobs), _(label)))
            ellipsize_dynamic_text(self["summary"], "  •  ".join(counts))
            self["message"].setText(_("Speed limit") + ": " + _rate(state.get("limit") or manager.DEFAULT_SPEED))
            self._draw()
            if self._operation:
                self["message"].setText(_("Please wait"))
            ellipsize_dynamic_text(self["message"], self["message"].getText())
        except Exception as error:
            log_event("downloads", "screen-refresh-failed", error)
            self["message"].setText(_(manager.MESSAGES["failed"]))
        if self._visible:
            self.timer.start(800, True)

    def selected(self):
        return self._entries[self.index] if self._entries and self.index < len(self._entries) else {}

    def move(self, delta):
        if self._entries:
            self.index = (self.index + delta) % len(self._entries)
            self._draw()

    def _poster(self, name, path):
        path = path if path and os.path.isfile(path) else ""
        if self._posters.get(name) == path:
            return
        self._posters[name] = path
        self[name].hide()
        if path:
            attach_pixmap(self, name, path, cover_ratio=(16, 9) if name.startswith("thumb") else None)
        else:
            loader = getattr(self, "_gt_pixmap_loaders", {}).pop(name, None)
            if loader is not None:
                loader.close()

    def _draw(self):
        if self.index < self._start:
            self._start = self.index
        if self.index >= self._start + 5:
            self._start = self.index - 4
        self._start = min(self._start, max(0, len(self._entries)-5))
        self["arrow_up"].show() if self._start else self["arrow_up"].hide()
        self["arrow_down"].show() if self._start + 5 < len(self._entries) else self["arrow_down"].hide()
        self["page"].setText("{} / {}".format(self.index+1 if self._entries else 0, len(self._entries)))
        for row in range(5):
            absolute = self._start + row
            job = self._entries[absolute] if absolute < len(self._entries) else None
            for name in ("row", "placeholder", "name", "meta", "status", "percent", "progress"):
                self["{}_{}".format(name, row)].show() if job else self["{}_{}".format(name, row)].hide()
            for name in ("focus", "marker"):
                self["{}_{}".format(name, row)].show() if job and absolute == self.index else self["{}_{}".format(name, row)].hide()
            self._poster("thumb_{}".format(row), job.get("poster", "") if job else "")
            if not job:
                continue
            status = job.get("status", "error")
            total, got = job.get("total") or 0, job.get("downloaded") or 0
            percent = min(100, int(got * 100 / total)) if total else 0
            fit_dynamic_text(self["name_{}".format(row)], job["name"], max_lines=2)
            source = "Stalker / MAC" if job.get("source") == "stalker" else "IPTV" if job.get("source") == "xtream" else _("Local file")
            ellipsize_dynamic_text(self["meta_{}".format(row)], source + "  •  " + _size(got) + (" / " + _size(total) if total else ""))
            fit_dynamic_text(self["status_{}".format(row)], _(manager.STATUS.get(status, "Error")), max_lines=2)
            self["percent_{}".format(row)].setText("{}%".format(percent) if total else "—")
            self["progress_{}".format(row)].setValue(percent)
            try:
                from skin import parseColor
                color = "#64E87B" if status in ("completed", "local") else "#FF6D79" if status == "error" else "#FFD15A" if status in ("queued", "paused") else "#21CFED"
                self["status_{}".format(row)].instance.setForegroundColor(parseColor(color))
                self["progress_{}".format(row)].instance.setForegroundColor(parseColor(color))
            except Exception:
                pass
        job = self.selected()
        self._poster("poster", job.get("poster", ""))
        self["detail_title"].setText(clean_dynamic_text(job.get("name") or _("No downloads or local videos.")))
        self["detail"].setText(self._details(job) if job else "")
        set_remote_footer(self, DOWNLOAD_FOOTER if job.get("status") in ("completed", "local")
                          else DOWNLOAD_DETAILS_FOOTER)
        if job.get("error"):
            ellipsize_dynamic_text(self["message"], _(manager.MESSAGES.get(job["error"], manager.MESSAGES["failed"])))

    def _details(self, job):
        return "{}: {} / {}\n{}: {}\n{}: {}\n{}".format(
            _("Downloaded"), _size(job.get("downloaded")), _size(job.get("total")) if job.get("total") else "—",
            _("Speed"), _size(job.get("speed")) + "/s", _("Time left"), _eta(job.get("eta")),
            _(manager.STATUS.get(job.get("status"), "Error")))

    def _error(self, error):
        text = manager.MESSAGES.get(getattr(error, "code", "failed"), manager.MESSAGES["failed"])
        self.session.open(MessageBox, _(text), MessageBox.TYPE_INFO)

    def _command(self, action, identity=None, restart=False):
        if self._operation:
            return
        identity = identity or self.selected().get("id")
        if not identity or identity.startswith("local:"):
            return
        try:
            manager.command(identity, action, restart=restart)
        except Exception as error:
            self._error(error)
        self.refresh()

    def cancel_selected(self):
        job = self.selected()
        if not job or job["status"] in manager.TERMINAL + ("local",):
            return
        identity = job["id"]
        self.session.openWithCallback(lambda answer: self._command("cancel", identity) if answer else None,
            MessageBox, _("Cancel this download?") + "\n\n" + job["name"], MessageBox.TYPE_YESNO, default=False)

    def toggle_pause(self):
        job = self.selected()
        if job.get("status") == "queued" and self._queue_held:
            self._command("resume")
        elif job.get("status") in ("resolving", "downloading", "queued"):
            self._command("pause")
        elif job.get("status") == "paused":
            if job.get("error") == "resume":
                identity = job["id"]
                self.session.openWithCallback(lambda answer: self._command("resume", identity, True) if answer else None,
                    MessageBox, _(manager.MESSAGES["resume"]), MessageBox.TYPE_YESNO, default=False)
            else:
                self._command("resume")

    def delete_selected(self):
        job = dict(self.selected())
        if not job or self._operation:
            return
        if job.get("status") in manager.ACTIVE + ("deleting",):
            self._error(manager.DownloadError("delete_active"))
            return
        self.session.openWithCallback(lambda answer: self._begin_operation("delete", job) if answer else None,
            MessageBox, _("Delete this video and its download entry?") + "\n\n" + job["name"] + "\n\n" + job["path"],
            MessageBox.TYPE_YESNO, default=False)

    def _begin_operation(self, kind, entry):
        if getattr(self, "_closed", True) or self._operation:
            return
        mailbox = {"done": threading.Event(), "kind": kind, "entry": dict(entry)}
        self._operation = mailbox
        def work():
            try:
                if kind == "delete":
                    manager.delete_entry(entry)
                else:
                    mailbox["media"] = inspect_media(entry["path"], expected_size=entry.get("total") or 0)
            except MediaFileError as error:
                mailbox["error"] = "media"
                log_event("local_playback", "validation={}".format(str(error)))
            except FileNotFoundError:
                mailbox["error"] = "missing"
            except Exception as error:
                mailbox["error"] = getattr(error, "code", "delete_failed" if kind == "delete" else "media")
                log_event("downloads", "operation={} failed".format(kind), error)
            finally:
                mailbox["done"].set()
        worker = threading.Thread(target=work)
        worker.daemon = True
        worker.start()
        self.refresh()

    def _poll_operation(self):
        mailbox = self._operation
        if mailbox is None or not mailbox["done"].is_set():
            return False
        self._operation = None
        if mailbox.get("error"):
            if mailbox["error"] == "missing":
                self.session.open(MessageBox, _("File not found."), MessageBox.TYPE_ERROR)
            else:
                self._error(manager.DownloadError(mailbox["error"]))
            return True
        if mailbox["kind"] == "delete":
            # Discard scans that began before the deletion completed.
            self._scan_generation += 1
            self._locals = []
            self._scan_at = 0
            return False
        job = mailbox["entry"]
        from .browser import build_extplayer_reference
        from .local_player import GTLocalPlayerScreen
        from .content import ContentItem
        kind = "series" if job.get("kind") == "series" else "movie"
        item = ContentItem(kind, "local", job["name"], extension=mailbox["media"]["format"])
        # Use the existing movie/series preference, without an account client.
        reference = build_extplayer_reference(job["path"], item.name, kind)
        self.session.open(GTLocalPlayerScreen, reference, item, media_info=mailbox["media"])
        return True

    def choose_folder(self):
        if self._operation:
            return
        self.session.openWithCallback(self._folder_chosen, GTDownloadFolderScreen, manager.snapshot().get("folder") or "")

    def _folder_chosen(self, path):
        if path:
            try:
                manager.set_folder(path)
                self._scan_at = 0
            except Exception as error:
                self._error(error)
        self.refresh()

    def choose_speed(self):
        if self._operation:
            return
        self.session.openWithCallback(lambda value: self._speed_chosen(value), GTSpeedLimitScreen,
                                      manager.snapshot().get("limit") or manager.DEFAULT_SPEED)

    def _speed_chosen(self, value):
        if value is not None:
            try:
                manager.set_limit(value)
            except Exception as error:
                self._error(error)
        self.refresh()

    def open_selected(self):
        job = self.selected()
        if not job or self._operation:
            return
        if job["status"] not in ("completed", "local"):
            message = self._details(job) + "\n\n" + job.get("path", "")
            if job.get("error"):
                message += "\n\n" + _(manager.MESSAGES.get(job["error"], manager.MESSAGES["failed"]))
            self.session.open(MessageBox, message, MessageBox.TYPE_INFO)
            return
        if job["path"].endswith(".part"):
            self.session.open(MessageBox, _("File not found."), MessageBox.TYPE_ERROR)
            return
        self._begin_operation("play", dict(job))


class GTSpeedLimitScreen(Screen):
    visible_rows = 9

    def __init__(self, session, current):
        width, height, px = _scale()
        self.skin = '<screen name="GTSpeedLimitScreen" position="center,center" size="{},{}" flags="wfNoBorder" backgroundColor="#101C39">'.format(px(800), px(750))
        self.skin += _label("title", 40, 25, 720, 68, px, 36)
        self.skin += _label("hint", 40, 104, 550, 42, px, 25, "#1DD0EF")
        self.skin += _label("position", 605, 104, 155, 42, px, 24, "#BFD3F0", align="right")
        for index in range(self.visible_rows):
            self.skin += _label("focus_{}".format(index), 35, 165 + index*51, 690, 46, px, 1, background="#136783", z=1)
            self.skin += _label("value_{}".format(index), 60, 165 + index*51, 640, 46, px, 28)
        self.skin += _label("arrow_up", 730, 165, 35, 46, px, 24, "#BFD3F0", align="center")
        self.skin += _label("arrow_down", 730, 165 + (self.visible_rows-1)*51, 35, 46, px, 24, "#BFD3F0", align="center")
        self.skin += _label("footer", 40, 662, 720, 55, px, 25, "#BFD3F0") + "</screen>"
        self.skin = apply_selection_color(self.skin)
        Screen.__init__(self, session)
        self.index = manager.SPEEDS.index(current if current in manager.SPEEDS else manager.DEFAULT_SPEED)
        self._start = 0
        self["title"] = Label(_("Speed limit"))
        self["hint"] = Label(_rate(manager.SPEEDS[0]) + "  —  " + _rate(manager.SPEEDS[-1]))
        self["position"] = Label("")
        self["arrow_up"] = Label("▲")
        self["arrow_down"] = Label("▼")
        self["footer"] = Label("OK / ●  " + _("Save") + "                 EXIT  " + _("Cancel"))
        for index in range(self.visible_rows):
            self["focus_{}".format(index)] = Label("")
            self["value_{}".format(index)] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self.save, "green": self.save, "cancel": lambda: self.close(None), "red": lambda: self.close(None),
            "up": lambda: self.move(-1), "down": lambda: self.move(1), "upRepeated": lambda: self.move(-1),
            "downRepeated": lambda: self.move(1)}, -1)
        self.onLayoutFinish.append(self.draw)
        self.setTitle(_("Speed limit"))

    def move(self, delta):
        self.index = (self.index + delta) % len(manager.SPEEDS)
        self.draw()

    def draw(self):
        if self.index < self._start:
            self._start = self.index
        elif self.index >= self._start + self.visible_rows:
            self._start = self.index - self.visible_rows + 1
        self._start = min(self._start, max(0, len(manager.SPEEDS) - self.visible_rows))
        for row in range(self.visible_rows):
            index = self._start + row
            label = self["value_{}".format(row)]
            focus = self["focus_{}".format(row)]
            if index < len(manager.SPEEDS):
                label.setText(_rate(manager.SPEEDS[index]))
                label.show()
            else:
                label.setText("")
                label.hide()
            focus.show() if index == self.index else focus.hide()
        self["position"].setText("{} / {}".format(self.index + 1, len(manager.SPEEDS)))
        self["arrow_up"].show() if self._start else self["arrow_up"].hide()
        self["arrow_down"].show() if self._start + self.visible_rows < len(manager.SPEEDS) else self["arrow_down"].hide()

    def save(self):
        self.close(manager.SPEEDS[self.index])


class GTDownloadFolderScreen(Screen):
    def __init__(self, session, current=""):
        self.skin = _folder_skin()
        Screen.__init__(self, session)
        self["title"] = Label(_("Save folder"))
        self["path"] = Label("")
        self["footer"] = Label("")
        install_remote_footer(self, FOLDER_FOOTER, stacked=True, design_size=FOLDER_DESIGN_SIZE)
        self["filelist"] = FileList(current if os.path.isdir(current) else "/media/", showDirectories=True,
            showFiles=False, showMountpoints=True, useServiceRef=False, inhibitDirs=False, inhibitMounts=False)
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self.descend, "green": self.save, "cancel": lambda: self.close(None), "red": lambda: self.close(None),
            "up": self["filelist"].up, "down": self["filelist"].down,
            "upRepeated": self["filelist"].up, "downRepeated": self["filelist"].down,
            "left": self["filelist"].pageUp, "right": self["filelist"].pageDown}, -1)
        self.onLayoutFinish.append(self.draw)
        self.setTitle(_("Save folder"))

    def draw(self):
        self["path"].setText(self["filelist"].getCurrentDirectory() or _("Storage"))

    def descend(self):
        if self["filelist"].canDescent():
            self["filelist"].descent()
        self.draw()

    def save(self):
        try:
            path = manager.folder_ready(self["filelist"].getCurrentDirectory())
        except Exception:
            self.session.open(MessageBox, _(manager.MESSAGES["folder"]), MessageBox.TYPE_INFO)
            return
        self.close(path)


def request_download(session, client, item):
    if getattr(getattr(client, "account", None), "source_type", "") not in ("xtream", "stalker"):
        session.open(MessageBox, _(manager.MESSAGES["unsupported"]), MessageBox.TYPE_INFO)
        return
    from .download_guard import install
    install(session)

    def add(folder=None):
        try:
            if folder:
                manager.set_folder(folder)
            from .browser import _cached_picon_path
            poster = _cached_picon_path(getattr(item, "icon", "")) if getattr(item, "icon", "") else ""
            identity = manager.enqueue(client, item, poster=poster or "")
        except Exception as error:
            session.open(MessageBox, _(manager.MESSAGES.get(getattr(error, "code", "failed"), manager.MESSAGES["failed"])), MessageBox.TYPE_INFO)
            return
        session.open(GTDownloadsScreen, identity)

    def accepted(answer):
        if not answer:
            return
        try:
            folder = manager.snapshot().get("folder")
            manager.folder_ready(folder)
        except Exception:
            session.openWithCallback(lambda path: add(path) if path else None, GTDownloadFolderScreen)
            return
        add()

    session.openWithCallback(accepted, MessageBox, _("Download this item?") + "\n\n" + item.name,
                             MessageBox.TYPE_YESNO, default=False)


# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-native selective-backup dashboard, without a browser dependency."""

import os
from xml.sax.saxutils import escape

from Components.Label import Label
from Components.Pixmap import MultiPixmap, Pixmap
from enigma import getDesktop

from .backup import BackupError
from .backup_scope import GROUPS
from .i18n import N_, _
from .paths import plugin_path
from .remote_footer import decorate_remote_footer, footer_item
from .typography import fit_dynamic_text, font_px

CATEGORIES = (
    (N_("Accounts and API keys"), N_("Servers, passwords and API information")),
    (N_("Plugin settings"), N_("Appearance, language and player preferences")),
    (N_("Favorites"), N_("Your saved channels and lists")),
    (N_("History and resume"), N_("Viewing history and playback positions")),
    (N_("Subtitles and sync"), N_("Downloaded subtitles and timing settings")),
    (N_("Local source files"), N_("M3U, XMLTV and source documents")),
)


def geometry():
    try:
        size = getDesktop(0).size()
        width, height = int(size.width()), int(size.height())
    except Exception:
        width, height = 1280, 720
    return width, height, lambda value: max(1, int(round(value * width / 1920.0))), \
        lambda value: max(1, int(round(value * height / 1080.0)))


def asset(name):
    width, height, unused_x, unused_y = geometry()
    variant = "2160" if width > 1920 or height > 1080 else "1080" if width > 1280 else "720"
    if width <= 720:
        variant = "sd576" if height > 480 else "sd640" if width <= 640 else "sd480"
    return plugin_path("skin", "images", "backup-r5-{}-{}.png".format(name, variant))


def footer():
    return (footer_item("arrows", "navigate"), footer_item("ok", N_("Select / Deselect")),
            footer_item("green", N_("Backup")), footer_item("yellow", N_("Select all")),
            footer_item("blue", N_("File path")), footer_item("exit", "back"))


def skin():
    width, height, px, py = geometry()
    factor = min(width / 1920.0, height / 1080.0)
    scale = lambda n: max(1, int(round(n * factor)))
    parts = ['<screen name="GTBackupDashboardScreen" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="#050914">'.format(width, height)]

    def picture(name, x, y, w, h, path=None, z=1, multi=None):
        attrs = ' pixmaps="{}"'.format(escape(",".join(multi), {'"': '&quot;'})) if multi else \
                ' pixmap="{}"'.format(escape(path, {'"': '&quot;'})) if path else ""
        parts.append('<widget name="{}" position="{},{}" size="{},{}" scale="1" alphatest="blend" zPosition="{}"{} />'.format(
            name, px(x) if x else 0, py(y) if y else 0, px(w), py(h), z, attrs))

    def label(name, x, y, w, h, font, color="#FFFFFF", align="left", z=3):
        parts.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" transparent="1" valign="center" halign="{}" zPosition="{}" />'.format(
            name, px(x) if x else 0, py(y) if y else 0, px(w), py(h),
            font_px(scale, font, max_height=h), color, align, z))

    picture("app_bg", 0, 0, 1920, 1080, z=0)
    label("brand", 72, 20, 900, 36, 25, "#BBD1E8")
    label("title", 72, 59, 1200, 87, 64)
    label("intro", 72, 140, 1690, 42, 30, "#C5D5EA")
    label("path_caption", 190, 203, 1310, 34, 25, "#B9D9EC")
    label("path", 190, 239, 1370, 40, 28)
    label("path_action", 1693, 215, 145, 48, 24)
    for index in range(6):
        x, y = 64 + (index % 2) * 666, 310 + (index // 2) * 210
        picture("card_focus_{}".format(index), x - 2, y - 2, 654, 194, asset("focus"), z=2)
        picture("card_check_{}".format(index), x + 30, y + 38, 40, 40,
                multi=(asset("unchecked"), asset("checked")), z=4)
        label("card_title_{}".format(index), x + 180, y + 31, 446, 66, 29)
        label("card_description_{}".format(index), x + 180, y + 101, 300, 70, 23, "#B4C8E1")
        label("card_size_{}".format(index), x + 483, y + 106, 145, 52, 32, "#40E9F1", "right")
    label("summary_title", 1428, 322, 400, 62, 38)
    label("selection_count", 1428, 395, 400, 54, 30, "#41E5F2")
    label("selected_size", 1428, 459, 400, 91, 66, "#42ECF4")
    label("size_caption", 1428, 555, 400, 70, 27, "#B9CFE9")
    label("free_caption", 1530, 648, 292, 42, 26)
    label("free_space", 1530, 696, 292, 65, 47)
    label("backup_hint", 1428, 785, 400, 51, 24, "#8BE66D")
    label("backup_button", 1428, 848, 396, 65, 32, align="center")
    picture("operation_panel", 160, 313, 1600, 610, asset("operation"), z=8)
    label("detail", 230, 351, 1460, 438, 28, z=10)
    parts.append('<widget name="progress" position="{},{}" size="{},{}" foregroundColor="#16C9F4" backgroundColor="#24354D" borderWidth="0" zPosition="10" />'.format(px(230), py(835), px(1460), py(18)))
    label("message", 68, 926, 1780, 35, 22, "#AEC6E1", z=10)
    label("footer", 0, 994, 1920, 86, 22)
    return decorate_remote_footer("\n".join(parts) + "</screen>", footer(),
        transparent_panel=True, show_dividers=False, skin_fonts_scaled=True)


def setup(screen):
    screen.selected_groups = set(GROUPS)
    screen.focus_index = 0
    for name, text in (
            ("brand", "GT IPTV PLAYER PRO"),
            ("intro", _("Select the sections you want to back up.")),
            ("path_caption", _("Save location")), ("path_action", _("File path")),
            ("summary_title", _("Backup summary")),
            ("selection_count", ""), ("selected_size", ""),
            ("size_caption", _("Size of selected files")), ("free_caption", _("Free space")),
            ("free_space", ""), ("backup_hint", _("Press green to start the backup.")),
            ("backup_button", _("Backup"))):
        screen[name] = Label(text)
    for index, (title, description) in enumerate(CATEGORIES):
        screen["card_focus_{}".format(index)] = Pixmap()
        screen["card_check_{}".format(index)] = MultiPixmap()
        screen["card_title_{}".format(index)] = Label(_(title))
        screen["card_description_{}".format(index)] = Label(_(description))
        screen["card_size_{}".format(index)] = Label("…")
    screen["operation_panel"] = Pixmap()
    screen.onLayoutFinish.append(screen.refresh_dashboard)


def refresh(screen, size_format, records=None, update_storage=True):
    files = (screen.manifest or {}).get("files", []) if records is None else records
    for index, group in enumerate(GROUPS):
        checked = group in screen.selected_groups
        screen["card_check_{}".format(index)].setPixmapNum(1 if checked else 0)
        focus = screen["card_focus_{}".format(index)]
        focus.show() if index == screen.focus_index else focus.hide()
        if screen.manifest is not None:
            records = screen.manager.select_records(files, (group,))
            screen["card_size_{}".format(index)].setText(size_format(sum(row["size"] for row in records)))
        for suffix, text, lines in (("title", _(CATEGORIES[index][0]), 2),
                                    ("description", _(CATEGORIES[index][1]), 3)):
            fit_dynamic_text(screen["card_{}_{}".format(suffix, index)], text, max_lines=lines)
    selected = screen.manager.select_records(files, screen.selected_groups) if screen.selected_groups else []
    screen["selected_size"].setText(size_format(sum(row["size"] for row in selected)))
    screen["selection_count"].setText(_("{} / {} sections selected").format(len(screen.selected_groups), len(GROUPS)))
    if update_storage:
        try:
            screen.manager.destination(screen.location)
            info = os.statvfs(screen.location)
            free = size_format(info.f_bavail * (info.f_frsize or info.f_bsize))
        except (BackupError, OSError, ValueError, RuntimeError):
            free = _("Not available")
        screen["free_space"].setText(free)
    fit_dynamic_text(screen["path"], screen.location, max_lines=1)
    overlay = screen.state in ("running", "error", "success")
    for name in ("operation_panel", "detail", "progress"):
        screen[name].show() if overlay else screen[name].hide()
    if screen.state == "ready":
        screen["message"].setText(_("Select sections with OK. The total updates automatically."))
    for name in ("intro", "path_action", "summary_title", "selection_count", "size_caption",
                 "free_caption", "free_space", "backup_hint", "backup_button"):
        widget = screen[name]
        fit_dynamic_text(widget, widget.getText(), max_lines=2)

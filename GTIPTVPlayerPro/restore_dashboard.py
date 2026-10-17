# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verified archive contents and independent receiver-native restore selection."""

import datetime
import xml.etree.ElementTree as ET

from Components.Label import Label
from Components.Pixmap import Pixmap

from . import backup_dashboard as base
from .backup_scope import GROUPS
from .i18n import N_, _
from .remote_footer import decorate_remote_footer, footer_item
from .typography import fit_dynamic_text, font_px

try:
    from skin import parseColor
except ImportError:
    parseColor = None

INTRO = N_("Select a backup file, then choose the sections to restore.")
PRESERVE = N_("Unselected sections are preserved.")


def asset(name):
    return base.asset("background").replace("backup-r5-background-", "restore-r7-" + name + "-")


def footer():
    return (footer_item("arrows", "navigate"), footer_item("ok", N_("Select / Deselect")),
            footer_item("green", N_("Restore")), footer_item("yellow", N_("Safety backup")),
            footer_item("blue", N_("Backup file")), footer_item("exit", "back"))


def skin():
    tree = ET.fromstring(base.skin())
    tree.set("name", "GTRestoreDashboardScreen")
    for node in list(tree):
        if node.attrib.get("name", "").startswith("remote_"):
            tree.remove(node)
    nodes = {node.attrib.get("name"): node for node in tree}
    width, height, px, py = base.geometry()
    factor = min(width / 1920.0, height / 1080.0)
    scale = lambda n: max(1, int(round(n * factor)))

    def move(name, x, y, w, h, font=None):
        node = nodes[name]
        node.set("position", "{},{}".format(px(x) if x else 0, py(y) if y else 0))
        node.set("size", "{},{}".format(px(w), py(h)))
        if font:
            node.set("font", "Regular;{}".format(font_px(scale, font, max_height=h)))

    def label(name, x, y, w, h, font, color="#B9CFE9"):
        node = ET.SubElement(tree, "widget", name=name, foregroundColor=color,
                             transparent="1", valign="center", zPosition="3")
        nodes[name] = node
        move(name, x, y, w, h, font)

    move("title", 72, 59, 1720, 87, 57)
    move("intro", 72, 140, 1740, 42, 28)
    label("archive_date", 80, 290, 410, 48, 25)
    label("archive_version", 510, 290, 390, 48, 25)
    label("archive_verified", 930, 290, 900, 48, 25, "#4AE96E")
    for index in range(6):
        x, y = 64 + index % 2 * 666, 354 + index // 2 * 194
        move("card_focus_{}".format(index), x - 2, y - 2, 654, 178)
        move("card_check_{}".format(index), x + 30, y + 31, 40, 40)
        node = nodes["card_check_{}".format(index)]
        node.set("pixmaps", node.attrib["pixmaps"] + "," + asset("disabled"))
        move("card_title_{}".format(index), x + 180, y + 19, 446, 61, 28)
        move("card_description_{}".format(index), x + 180, y + 87, 300, 68, 22)
        move("card_size_{}".format(index), x + 483, y + 90, 145, 50, 31)
        ET.SubElement(tree, "widget", name="card_disabled_{}".format(index),
            position="{},{}".format(px(x), py(y)), size="{},{}".format(px(650), py(174)),
            pixmap=asset("shade"), scale="1", alphatest="blend", zPosition="2")
    move("summary_title", 1428, 338, 400, 65, 34)
    move("selection_count", 1428, 413, 400, 49, 29)
    move("selected_size", 1428, 477, 400, 88, 64)
    move("size_caption", 1428, 570, 400, 54, 27)
    move("free_caption", 1520, 644, 304, 50, 28)
    move("free_space", 1428, 696, 396, 59, 24)
    label("safety_action", 1480, 759, 344, 42, 23, "#F2D25B")
    move("backup_hint", 1428, 808, 396, 66, 22)
    move("backup_button", 1428, 879, 396, 51, 31)
    return decorate_remote_footer(ET.tostring(tree, encoding="unicode"), footer(),
        transparent_panel=True, show_dividers=False, skin_fonts_scaled=True)


def setup(screen):
    base.setup(screen)
    screen.selected_groups = set()
    screen.available_groups = set()
    screen.restore_inventory = []
    for name in ("archive_date", "archive_version", "archive_verified", "safety_action"):
        screen[name] = Label("")
    for index in range(6):
        screen["card_disabled_{}".format(index)] = Pixmap()
    for name, text in (("intro", _(INTRO)), ("path_caption", _("Backup file")),
            ("path_action", _("Select file")), ("summary_title", _("Restore summary")),
            ("size_caption", _("Data to restore")), ("free_caption", _("Safety backup")),
            ("safety_action", _("Change location")),
            ("backup_hint", _("Current data is backed up before restoring.")),
            ("backup_button", _("Restore"))):
        screen[name].setText(text)


def refresh(screen, size_format):
    base.refresh(screen, size_format, records=screen.restore_inventory, update_storage=False)
    manifest = screen.manifest or {}
    for index, group in enumerate(GROUPS):
        available = group in screen.available_groups
        screen["card_check_{}".format(index)].setPixmapNum(
            1 if group in screen.selected_groups else 0 if available else 2)
        shade = screen["card_disabled_{}".format(index)]
        shade.hide() if available else shade.show()
        if not available:
            screen["card_focus_{}".format(index)].hide()
            screen["card_size_{}".format(index)].setText("—")
        description = _(base.CATEGORIES[index][1]) if available else _("Not in this backup")
        fit_dynamic_text(screen["card_description_{}".format(index)], description, max_lines=3)
        for suffix, normal in (("title", "#FFFFFF"), ("description", "#B4C8E1"), ("size", "#40E9F1")):
            component = screen["card_{}_{}".format(suffix, index)]
            if component.instance is not None and parseColor is not None:
                component.instance.setForegroundColor(parseColor(normal if available else "#7E89A5"))
    created = manifest.get("created", "")
    try:
        created = datetime.datetime.fromisoformat(created).astimezone().strftime("%d.%m.%Y · %H:%M")
    except (ValueError, OverflowError):
        created = "—"
    screen["archive_date"].setText(created)
    screen["archive_version"].setText(_("Version") + ": " + manifest.get("package_version", "—"))
    screen["archive_verified"].setText(("✓ " + _("Backup verified")) if manifest else
        _("Checking backup...") if screen.state == "preflight" else _("Select a backup file first."))
    screen["free_space"].setText(screen.safety_folder or _("Not configured"))
    fit_dynamic_text(screen["path"], screen.location or _("Select a backup file first."), max_lines=1)
    if screen.state in ("choose", "ready"):
        screen["message"].setText(_(PRESERVE) if manifest else _("Select a backup file first."))
    for name in ("title", "archive_date", "archive_version", "archive_verified", "free_space", "safety_action"):
        fit_dynamic_text(screen[name], screen[name].getText(), max_lines=2)

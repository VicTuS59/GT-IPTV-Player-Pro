# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resolution-independent, text-free artwork and native translated labels."""

from .i18n import _
from .paths import plugin_path
from .typography import fit_dynamic_text, font_px


SPINNER_FRAMES = 12
OVERLAY_LABELS = ("shade", "caption", "source", "status", "wait")


def overlay_names():
    return tuple("auto_login_" + name for name in OVERLAY_LABELS + ("panel",)) + tuple(
        "auto_login_spinner_{}".format(index) for index in range(SPINNER_FRAMES)
    )


def auto_login_overlay_skin(width, height):
    factor = min(width / 1920.0, height / 1080.0)
    px = lambda value: max(1, int(round(value * factor)))
    panel_w, panel_h = px(1120), px(540)
    left, top = (width - panel_w) // 2, (height - panel_h) // 2
    widgets = [
        '<widget name="auto_login_shade" position="0,0" size="{},{}" '
        'font="Regular;1" backgroundColor="#80000000" transparent="0" zPosition="100" />'.format(width, height),
        '<widget name="auto_login_panel" position="{},{}" size="{},{}" '
        'pixmap="{}" scale="1" alphatest="blend" transparent="1" zPosition="101" />'.format(
            left, top, panel_w, panel_h, plugin_path("skin", "images", "auto-login-panel-r2.png")),
    ]
    for name, y, h, size, color in (
        ("caption", 44, 72, 30, "#00D9F5"),
        ("source", 120, 110, 48, "#FFFFFF"),
        ("status", 350, 90, 55, "#FFFFFF"),
        ("wait", 440, 64, 32, "#B8C5D9"),
    ):
        widgets.append(
            '<widget name="auto_login_{}" position="{},{}" size="{},{}" '
            'font="Regular;{}" foregroundColor="{}" transparent="1" '
            'zPosition="103" valign="center" halign="center" noWrap="{}" />'.format(
                name, left + px(55), top + px(y), panel_w - px(110), px(h),
                px(size), color, "0" if name in ("source", "caption") else "1",
            )
        )
    for index in range(SPINNER_FRAMES):
        widgets.append(
            '<widget name="auto_login_spinner_{}" position="{},{}" size="{},{}" '
            'pixmap="{}" scale="1" alphatest="blend" transparent="1" zPosition="103" />'.format(
                index, (width - px(128)) // 2, top + px(228), px(128), px(128),
                plugin_path("skin", "images", "auto-login-spinner-r2-{:02d}.png".format(index)),
            )
        )
    return "\n".join(widgets)


def install_auto_login_overlay(screen, width=1920, height=1080):
    from Components.Label import Label
    from Components.Pixmap import Pixmap
    for name in OVERLAY_LABELS:
        screen["auto_login_" + name] = Label("")
    screen["auto_login_panel"] = Pixmap()
    for index in range(SPINNER_FRAMES):
        screen["auto_login_spinner_{}".format(index)] = Pixmap()
    screen["auto_login_caption"].setText(_("AUTOMATIC ACCOUNT LOGIN"))
    screen["auto_login_status"].setText(_("Connecting..."))
    screen["auto_login_wait"].setText(_("Please wait."))
    screen._auto_login_spinner_frame = 0
    screen._auto_login_scale = min(width / 1920.0, height / 1080.0)
    for name in overlay_names():
        screen[name].hide()


def show_auto_login_overlay(screen, visible, source_name=""):
    for name in overlay_names():
        screen[name].hide()
    if visible:
        px = lambda value: max(1, int(round(value * screen._auto_login_scale)))
        for name, text, size, minimum, lines in (
            ("caption", _("AUTOMATIC ACCOUNT LOGIN"), 30, 24, 2),
            ("source", source_name, 48, 32, 2),
            ("status", _("Connecting..."), 55, 32, 1),
            ("wait", _("Please wait."), 32, 24, 1),
        ):
            role = "title" if name in ("source", "status") else "body"
            fit_dynamic_text(
                screen["auto_login_" + name], text, max_lines=lines,
                preferred_size=font_px(px, size, role=role),
                min_size=font_px(px, minimum, value="standard"), fallback_chars=38,
            )
        for name in OVERLAY_LABELS + ("panel",):
            screen["auto_login_" + name].show()
        screen._auto_login_spinner_frame = 0
        screen["auto_login_spinner_0"].show()


def advance_auto_login_spinner(screen):
    previous = screen._auto_login_spinner_frame
    screen["auto_login_spinner_{}".format(previous)].hide()
    screen._auto_login_spinner_frame = (previous + 1) % SPINNER_FRAMES
    screen["auto_login_spinner_{}".format(screen._auto_login_spinner_frame)].show()

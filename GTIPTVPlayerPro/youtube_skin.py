# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Rounded, resolution-adaptive YouTube result cards and video pane."""

import os

from .channel_highlight import saved_selection_border
from .main import _scale
from .remote_footer import decorate_remote_footer, footer_item
from .typography import font_px


def youtube_art(kind, width=None, border=None):
    width = _scale()[0] if width is None else width
    variant = "sd" if width <= 720 else "720" if width <= 1280 else "1080" if width <= 1920 else "2160"
    name = ("yt-focus-{}-r44-{}.png".format(border or saved_selection_border(), variant)
            if kind == "focus" else "yt-{}-r44-{}.png".format(kind, variant))
    return os.path.join(os.path.dirname(__file__), "skin", "images", name)


def youtube_search_skin(footer, rows=5):
    width, height, px = _scale()
    # SD framebuffers may be 4:3 even when their output is anamorphic 16:9.
    # Fill their complete desktop rather than stranding the footer halfway up.
    x = lambda value: max(1, int(round(value * width / 1920.0))) if value else 0
    y = lambda value: max(1, int(round(value * height / 1080.0))) if value else 0
    parts = ['<screen name="GTYouTubeSearchScreen" position="0,0" size="{},{}" '
             'flags="wfNoBorder" backgroundColor="#020617" transparent="0">'.format(width, height)]

    def label(name, left, top, w, h, size=28, color="#F8FAFC", bg=None,
              z=6, align="left", wrap=False, minimum=11):
        font = max(minimum, font_px(px, size))
        parts.append('<widget name="{}" position="{},{}" size="{},{}" '
                     'font="Regular;{}" foregroundColor="{}" zPosition="{}" '
                     'halign="{}" valign="center" noWrap="{}" {} />'.format(
                         name, x(left), y(top), x(w), y(h), font, color, z, align,
                         "0" if wrap else "1", 'backgroundColor="{}" transparent="0"'.format(bg)
                         if bg else 'transparent="1"'))

    def pixmap(name, left, top, w, h, kind=None, z=3):
        art = 'pixmap="{}" '.format(youtube_art(kind, width)) if kind else ""
        parts.append('<widget name="{}" position="{},{}" size="{},{}" {}'
                     'alphatest="blend" scale="1" zPosition="{}" />'.format(
                         name, x(left), y(top), x(w), y(h), art, z))

    label("video_guard", 0, 0, 1920, 1080, 1, bg="#020617", z=-30, minimum=1)
    pixmap("app_bg", 0, 0, 1920, 1080, z=-20)
    label("header", 60, 30, 630, 76, 43, minimum=18)
    label("header_divider", 692, 30, 36, 76, 43, minimum=18)
    pixmap("youtube_logo", 766, 43, 72, 50, "logo", z=5)
    label("youtube_brand", 858, 30, 480, 76, 43, minimum=18)
    pixmap("quality_frame", 1610, 40, 250, 60, "quality")
    label("quality", 1610, 40, 250, 60, 28, "#22D3EE", align="center", minimum=12)
    pixmap("search_frame", 60, 124, 1800, 76, "search")
    pixmap("search_icon", 86, 144, 34, 34, "search-icon", z=5)
    label("search_text", 148, 130, 1676, 64, 36, minimum=16)
    label("section", 60, 210, 1100, 40, 27, "#C9E7FA", minimum=13)
    for index in range(rows):
        top = 258 + index * 126
        pixmap("row_{}".format(index), 60, top, 1136, 116, "row")
        pixmap("focus_{}".format(index), 60, top, 1136, 116, "focus", z=4)
        pixmap("thumb_{}".format(index), 76, top + 10, 176, 96, z=5)
        label("title_{}".format(index), 278, top + 6, 746, 74, 30,
              wrap=True, minimum=14)
        label("detail_{}".format(index), 278, top + 84, 746, 28, 23,
              "#ACCFEA", minimum=11)
        pixmap("live_bg_{}".format(index), 1040, top + 36, 134, 40, "live", z=5)
        label("duration_{}".format(index), 1040, top + 36, 134, 40, 22,
              "#F1F5FC", align="center", wrap=True, minimum=11)

    pixmap("preview_panel", 1220, 224, 640, 670, "panel", z=2)
    parts.append('<widget name="preview_video" position="{},{}" size="{},{}" '
                 'backgroundColor="transparent" zPosition="7" />'.format(x(1246), y(252), x(588), y(331)))
    label("preview_mask", 1246, 252, 588, 331, 1, bg="#07182F", z=8, minimum=1)
    pixmap("preview_art", 1246, 252, 588, 331, z=9)
    label("preview_badge", 1246, 606, 580, 38, 22, "#22D3EE", minimum=12)
    pixmap("preview_live_bg", 1246, 606, 134, 40, "live", z=10)
    label("preview_live", 1246, 606, 134, 40, 22, align="center", wrap=True, z=11, minimum=12)
    label("preview_title", 1246, 654, 588, 108, 34, wrap=True, minimum=16)
    label("preview_detail", 1246, 774, 588, 64, 25, "#ACCFEA", wrap=True, minimum=12)
    label("preview_action", 1246, 845, 588, 36, 26, "#E5F3FF", minimum=12)
    label("pages", 60, 900, 1136, 50, 27, "#22D3EE", align="center", minimum=12)
    label("status", 60, 950, 1800, 34, 22, "#C5D4E8", align="center", minimum=11)
    label("footer", 0, 988, 1920, 92, 24)
    pixmap("footer_frame", 40, 986, 1840, 88, "footer", z=30)
    parts.append("</screen>")
    # The OK caption changes after playback starts. Reserve both translated
    # captions at skin creation so switching to fullscreen cannot clip it.
    alternate_footer = tuple(footer_item(key, "fullscreen" if key == "ok" else action, weight)
                             for key, action, weight in footer)
    return decorate_remote_footer("\n".join(parts), footer, alternate_items=alternate_footer,
                                  transparent_panel=True,
                                  show_dividers=False, skin_fonts_scaled=True,
                                  panel_height=92, bottom_padding=0)

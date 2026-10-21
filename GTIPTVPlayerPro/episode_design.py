# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-independent geometry and artwork for the season/episode browser.

The photographic layer is supplied by the selected series at runtime.  All
bundled artwork is theme/shape-only and needs no Pillow on the receiver.
"""

from xml.sax.saxutils import escape

from .paths import plugin_path
from .channel_highlight import normalize_channel_highlight, normalize_selection_border


EPISODE_ROWS = 8
EPISODE_PIXMAP_NAMES = (
    "episode_backdrop", "episode_scrim", "episode_resume_button",
    "episode_favorite_button", "episode_download_button", "episode_favorite_key",
    "season_tabs_focus",
) + tuple("episode_action_focus_{}".format(i) for i in range(3)) + tuple(
    "episode_{}_{}".format(kind, i)
    for i in range(EPISODE_ROWS) for kind in ("watched", "focus_fill", "focus")
)
EPISODE_LABEL_NAMES = (
    "series_title", "series_meta", "series_genre", "series_plot", "series_cast",
    "series_cast_label", "episode_resume_text", "episode_resume_detail",
    "episode_favorite_text", "episode_download_text", "episode_download_key",
    "season_progress_label", "season_progress_text", "season_progress_track",
    "season_progress_fill", "season_progress_percent", "season_tabs_rule",
    "season_count", "episode_position", "episode_clock",
) + tuple("season_tab_{}".format(i) for i in range(3)) + tuple(
    "season_tab_line_{}".format(i) for i in range(3)
) + tuple(
    "episode_{}_{}".format(kind, i)
    for i in range(EPISODE_ROWS)
    for kind in ("row_bg", "number", "name", "status", "progress_track", "progress_fill")
)


def episode_art(name):
    return plugin_path("skin", "images", "episode-r46-{}.png".format(name))


_ACTION_OUTLINE_SIZES = frozenset((
    (610, 128), (540, 128), (305, 64), (270, 64),
    (203, 43), (180, 43), (193, 35), (160, 35),
    (193, 29), (160, 29), (170, 29), (141, 29),
))


def episode_action_outline_asset(width, height):
    """Keep thin rounded action borders at their native physical pixels.

    The receiver blitter can omit a two-pixel source edge when shrinking the
    shared 600-pixel frame. Known desktop geometries use matching PNG sizes;
    unusual desktops retain the existing scalable artwork.
    """
    width, height = int(width), int(height)
    if (width, height) in _ACTION_OUTLINE_SIZES:
        return (plugin_path("skin", "images",
                            "episode-r48-action-outline-{}x{}.png".format(width, height)), 0)
    return episode_art("action-outline"), 1


def episode_focus_assets(fill="navy", border=None):
    """Use independent saved fill/border colors without 289 combined images."""
    fill = normalize_channel_highlight(fill)
    border = normalize_selection_border(border, fill)
    return (episode_art("focus-fill-" + fill), episode_art("focus-border-" + border))


def episode_geometry(width, height, px):
    """Fill wide and 4:3 desktops, retaining eight readable episode rows."""
    width, height = max(1, int(width)), max(1, int(height))
    compact = width <= 800
    x = lambda value: int(round(value * width / 1920.0))
    y = lambda value: int(round(value * height / 1080.0))
    rect = lambda a, b, c, d: (x(a), y(b), max(1, x(c)), max(1, y(d)))
    font = lambda value, minimum=1: max(minimum, int(px(value)))
    # Leave two native caption lines beneath the 32-unit remote keycaps.
    footer_h = max(font(100), 56 if compact else 1)
    footer_y = height - footer_h
    right_x, right_w = x(1178), x(660)
    if compact:
        # SD uses the same two-area hierarchy; redistribute the empty cinema
        # gutter into useful text width and use real pixel font minima.
        margin = max(17, x(54))
        right_x = int(round(width * .555))
        right_w = width - right_x - margin
        left_w = right_x - margin - 15
        sy = lambda value: int(round(value * height / 480.0))
        cr = lambda a, b, c, d: (int(a), sy(b), max(1, int(c)), max(1, sy(d)))
        left = {
            "title": cr(margin, 64, left_w, 49),
            "meta": cr(margin, 114, left_w, 19),
            "genre": cr(margin, 135, left_w, 18),
            "plot": cr(margin, 157, left_w, 58),
            "cast_label": cr(margin, 219, left_w, 14),
            "cast": cr(margin, 234, left_w, 27),
            "resume": cr(margin, 268, left_w, 32),
            "resume_text": cr(margin + 8, 269, left_w - 16, 30),
            "resume_detail": cr(margin, 303, left_w, 21),
            "favorite": cr(margin, 329, int(left_w * .53), 29),
            "download": cr(margin + int(left_w * .56), 329, int(left_w * .44), 29),
            "progress_label": cr(margin, 367, left_w, 14),
            "progress_text": cr(margin, 383, left_w, 17),
            "season_progress": cr(margin, 406, left_w - 38, 6),
            "progress_percent": cr(margin + left_w - 34, 397, 34, 19),
        }
        favorite, download = left["favorite"], left["download"]
        left["favorite_key"] = (favorite[0] + 7, favorite[1] + max(1, favorite[3] // 3), 13, max(9, favorite[3] // 3))
        left["favorite_text"] = (favorite[0] + 25, favorite[1], favorite[2] - 29, favorite[3])
        left["download_key"] = (download[0] + 5, download[1] + 5, 28, max(1, download[3] - 10))
        left["download_text"] = (download[0] + 37, download[1], max(1, download[2] - 41), download[3])
        tab_y, tab_h, list_y = sy(78), sy(26), sy(113)
        tabs = [(right_x + i * (right_w // 3), tab_y, right_w // 3 - 4, tab_h) for i in range(3)]
        fonts = {
            "title": max(22, int(width * .034)), "meta": 12, "genre": 12,
            "plot": 13 if width < 700 else 14, "cast": 12, "cast_label": 11,
            "resume": 14, "resume_detail": 11, "favorite": 10, "download": 10,
            "progress_label": 11, "progress_text": 12, "progress_percent": 11,
            "name": 14, "status": 11, "number": 17, "season": 12,
            "count": 11, "position": 11, "clock": 18, "breadcrumb": 12,
            "brand": 23, "brand_sub": 14,
        }
        brand = (margin, sy(8), 150, sy(24))
        brand_sub = (margin, sy(33), 150, sy(17))
        breadcrumb = (margin + 157, sy(19), max(1, width - margin - 250), sy(21))
        clock = (width - margin - 58, sy(15), 58, sy(26))
        season_count = (0, 0, 1, 1)
        list_bottom = footer_y - max(23, sy(23))
    else:
        left = {
            "title": rect(92, 160, 1040, 142),
            "meta": rect(92, 316, 1040, 35),
            "genre": rect(92, 350, 990, 29),
            "plot": rect(92, 382, 700, 113),
            "cast_label": rect(92, 506, 800, 28),
            "cast": rect(92, 539, 890, 52),
            "resume": rect(92, 595, 500, 70),
            "resume_text": rect(109, 596, 466, 68),
            "resume_detail": rect(92, 680, 930, 38),
            "favorite": rect(92, 735, 305, 64),
            "download": rect(417, 735, 270, 64),
            "favorite_key": rect(117, 753, 33, 32),
            "favorite_text": rect(166, 735, 218, 64),
            "download_key": rect(428, 747, 67, 39),
            "download_text": rect(504, 735, 170, 64),
            "progress_label": rect(92, 843, 700, 30),
            "progress_text": rect(92, 878, 700, 30),
            "season_progress": rect(92, 909, 500, 14),
            "progress_percent": rect(606, 896, 75, 34),
        }
        tab_y, tab_h, list_y = y(138), y(43), y(196)
        tabs = [rect(1190 + i * 163, 138, 150, 43) for i in range(3)]
        season_count = rect(1734, 139, 104, 40)
        fonts = {name: font(value) for name, value in {
            "title": 64, "meta": 25, "genre": 23, "plot": 27, "cast": 25,
            "cast_label": 22, "resume": 29, "resume_detail": 23,
            "favorite": 22, "download": 21, "progress_label": 22,
            "progress_text": 23, "progress_percent": 22, "name": 27,
            "status": 22, "number": 34, "season": 24, "count": 20,
            "position": 21, "clock": 32, "breadcrumb": 23,
            "brand": 46, "brand_sub": 31,
        }.items()}
        brand, brand_sub = rect(92, 21, 260, 53), rect(92, 73, 260, 35)
        breadcrumb, clock = rect(347, 47, 760, 39), rect(1757, 38, 100, 44)
        list_bottom = min(y(947), footer_y - font(37))

    row_step = max(1, (list_bottom - list_y) // EPISODE_ROWS)
    rows = []
    for i in range(EPISODE_ROWS):
        row_y = list_y + row_step * i
        row_h = row_step - max(1, font(3))
        pad = max(5, x(15))
        number_w = max(25, x(63))
        text_x = right_x + pad + number_w + max(6, x(15))
        text_w = right_x + right_w - text_x - max(24, x(56))
        if compact:
            name_y, name_h = row_y + 1, max(16, int(row_h * .49))
            status_y, status_h = row_y + name_h, max(13, row_h - name_h - 1)
        else:
            name_y, name_h = row_y + y(10), y(38)
            status_y, status_h = row_y + y(51), y(31)
        progress_w = max(50, x(165))
        progress_x = right_x + right_w - progress_w - max(10, x(26))
        progress_h = max(3, y(6))
        progress_y = status_y + max(1, (status_h - progress_h) // 2)
        status_w = max(1, progress_x - text_x - max(8, x(18)))
        tick_size = max(16, font(32))
        rows.append({
            "row": (right_x, row_y, right_w, row_h),
            "rule": (right_x + pad, row_y + row_h, right_w - 2 * pad, 1),
            "number": (right_x + pad, row_y + max(0, (row_h - fonts["number"] - 7) // 2), number_w, fonts["number"] + 7),
            "name": (text_x, name_y, max(1, text_w), max(1, name_h)),
            "status": (text_x, status_y, status_w, max(1, status_h)),
            "watched": (right_x + right_w - tick_size - pad, row_y + max(0, (row_h - tick_size) // 2), tick_size, tick_size),
            "progress": (progress_x, progress_y, progress_w, progress_h),
            "progress_track": (progress_x, progress_y, progress_w, progress_h),
            "progress_fill": (progress_x, progress_y, progress_w, progress_h),
            "progress_width": progress_w,
        })
    backdrop = (0, 0, int(round(width * .62)), footer_y)
    result = dict(left)
    result.update({
        "width": width, "height": height, "compact": compact, "fonts": fonts,
        "footer_y": footer_y, "footer_h": footer_h, "rows": rows,
        "list_scroll": (width - max(11, x(47)), list_y, max(3, x(7)), max(1, list_bottom - list_y)),
        "list_rect": (right_x, list_y, right_w, max(1, list_bottom - list_y)),
        "season_tabs": tabs,
        "season_tab": tabs,
        "season_tabs_focus": (right_x, tab_y - max(1, y(6)), right_w, tab_h + max(4, y(12))),
        "season_tabs_rule": (right_x + max(3, x(12)), tab_y + tab_h + max(3, y(5)), right_w - max(6, x(24)), max(1, y(2))),
        "season_tab_lines": [(a, b + c_h + max(2, y(4)), min(c_w, max(38, x(118))), max(2, y(4))) for a, b, c_w, c_h in tabs],
        "season_count": season_count,
        "episode_position": (right_x + max(3, x(12)), footer_y - max(23, y(37)), right_w - max(6, x(24)), max(17, y(27))),
        "message": (right_x + max(3, x(12)), list_y + max(1, y(40)), right_w - max(6, x(24)), max(28, y(90))),
        "backdrop": backdrop, "scrim": backdrop, "brand": brand,
        "brand_sub": brand_sub, "breadcrumb": breadcrumb, "clock": clock,
        "actions": (left["resume"], left["favorite"], left["download"]),
        "resume_font": fonts["resume"], "name_font": fonts["name"],
        "status_font": fonts["status"], "title_font": fonts["title"],
        "plot_font": fonts["plot"], "cast_font": fonts["cast"],
    })
    return result


def episode_skin(width, height, px, watched_icon):
    """Return plain native skin XML; all provider and translated text is live."""
    g = episode_geometry(width, height, px)
    f = g["fonts"]
    lines = ['<screen name="GTEpisodeListScreen" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="#020617">'.format(width, height)]

    def tag(name, box, font=1, color="#F8FAFC", background=None, pixmap=None,
            z=3, extra="", transparent=1, scale=1):
        a, b, c, d = box
        attrs = 'name="{}" position="{},{}" size="{},{}" zPosition="{}"'.format(name, a, b, c, d, z)
        if pixmap is not None:
            attrs += ' pixmap="{}" alphatest="blend" scale="{}"'.format(escape(pixmap, {'"': '&quot;'}), int(scale))
        elif name not in ("app_bg", "episode_backdrop"):
            attrs += ' font="Regular;{}" foregroundColor="{}" transparent="{}"'.format(max(1, int(font)), color, transparent)
            if background is not None:
                attrs += ' backgroundColor="{}"'.format(background)
        lines.append('    <widget {} {}/>'.format(attrs, extra))

    tag("app_bg", (0, 0, int(width), int(height)), z=-30)
    tag("episode_backdrop", g["backdrop"], z=-20)
    tag("episode_scrim", g["scrim"], pixmap=episode_art("scrim"), z=-19)
    for name in ("top_accent", "header", "screen_label", "content_panel", "list_accent", "items"):
        tag(name, (0, 0, 1, 1), z=-1)
    for text, key, font_key, color in (("GT IPTV", "brand", "brand", "#FFFFFF"), ("PLAYER PRO", "brand_sub", "brand_sub", "#58E7FF")):
        a, b, c, d = g[key]
        lines.append('    <eLabel position="{},{}" size="{},{}" text="{}" font="Regular;{}" foregroundColor="{}" transparent="1" zPosition="5" />'.format(a, b, c, d, text, f[font_key], color))
    tag("breadcrumb", g["breadcrumb"], f["breadcrumb"], "#D7DFEF", extra='valign="center" noWrap="1"')
    tag("episode_clock", g["clock"], f["clock"], "#FFFFFF", extra='halign="right" valign="center" noWrap="1"')
    for name, key, font_key, color, extra in (
        ("series_title", "title", "title", "#FFFFFF", 'valign="center" noWrap="0"'),
        ("series_meta", "meta", "meta", "#D9E3F3", 'valign="center" noWrap="1"'),
        ("series_genre", "genre", "genre", "#67DDF1", 'valign="center" noWrap="1"'),
        ("series_plot", "plot", "plot", "#F1F5FA", 'valign="top" noWrap="0"'),
        ("series_cast_label", "cast_label", "cast_label", "#A8B6CD", 'valign="center"'),
        ("series_cast", "cast", "cast", "#E9EEF8", 'valign="top" noWrap="0"'),
    ):
        tag(name, g[key], f[font_key], color, extra=extra)
    tag("episode_resume_button", g["resume"], pixmap=episode_art("action-yellow"), z=2)
    for name, key in (("episode_favorite_button", "favorite"), ("episode_download_button", "download")):
        artwork, scale = episode_action_outline_asset(*g[key][2:])
        tag(name, g[key], pixmap=artwork, scale=scale, z=2)
    tag("episode_resume_text", g["resume_text"], f["resume"], "#071325", z=5, extra='valign="center" halign="center" noWrap="1"')
    tag("episode_resume_detail", g["resume_detail"], f["resume_detail"], "#C9D7EC", extra='valign="center" noWrap="1"')
    tag("episode_favorite_key", g["favorite_key"], pixmap=plugin_path("skin", "images", "remote-key-red-v42.png"), z=5)
    tag("episode_favorite_text", g["favorite_text"], f["favorite"], "#F8FAFC", z=5, extra='valign="center" halign="center" noWrap="1"')
    tag("episode_download_key", g["download_key"], max(9, int(f["download"] * .78)), "#FFFFFF", "#0A162B", z=5, transparent=0, extra='valign="center" halign="center" noWrap="1"')
    tag("episode_download_text", g["download_text"], f["download"], "#F8FAFC", z=5, extra='valign="center" halign="center" noWrap="1"')
    fill_asset, border_asset = episode_focus_assets()
    for i, action in enumerate(g["actions"]):
        tag("episode_action_focus_{}".format(i), action, pixmap=border_asset, z=7)
    tag("season_tabs_focus", g["season_tabs_focus"], pixmap=border_asset, z=2)
    tag("season_progress_label", g["progress_label"], f["progress_label"], "#A8B6CD", extra='valign="center" noWrap="1"')
    tag("season_progress_text", g["progress_text"], f["progress_text"], "#E7EEF8", extra='valign="center" noWrap="1"')
    tag("season_progress_track", g["season_progress"], background="#3A4765", transparent=0)
    tag("season_progress_fill", g["season_progress"], background="#FFCD44", z=4, transparent=0)
    tag("season_progress_percent", g["progress_percent"], f["progress_percent"], "#C9D7EC", extra='halign="right" valign="center" noWrap="1"')
    tag("season_tabs_rule", g["season_tabs_rule"], background="#15456D", transparent=0)
    for i, tab in enumerate(g["season_tabs"]):
        tag("season_tab_{}".format(i), tab, f["season"], "#FFFFFF", extra='valign="center" halign="center" noWrap="1"')
        tag("season_tab_line_{}".format(i), g["season_tab_lines"][i], background="#22D3EE", z=4, transparent=0)
    tag("season_count", g["season_count"], f["count"] if not g["compact"] else 1, "#A8B6CD", extra='valign="center" halign="right" noWrap="1"')
    for i, row in enumerate(g["rows"]):
        tag("episode_row_bg_{}".format(i), row["rule"], background="#164365", z=1, transparent=0)
        tag("episode_focus_fill_{}".format(i), row["row"], pixmap=fill_asset, z=2)
        tag("episode_focus_{}".format(i), row["row"], pixmap=border_asset, z=5)
        tag("episode_number_{}".format(i), row["number"], f["number"], "#F3F7FC", extra='valign="center" halign="left" noWrap="1"')
        tag("episode_name_{}".format(i), row["name"], f["name"], "#F8FAFC", extra='valign="center" halign="left" noWrap="1"')
        tag("episode_status_{}".format(i), row["status"], f["status"], "#C5D0E4", extra='valign="center" halign="left" noWrap="1"')
        tag("episode_watched_{}".format(i), row["watched"], pixmap=watched_icon, z=6)
        tag("episode_progress_track_{}".format(i), row["progress"], background="#3A4765", transparent=0)
        tag("episode_progress_fill_{}".format(i), row["progress"], background="#FFCD44", z=4, transparent=0)
    tag("list_scroll_track", g["list_scroll"], background="#26344E", z=5, transparent=0)
    tag("list_scroll_thumb", g["list_scroll"], background="#67A3CF", z=6, transparent=0)
    tag("episode_position", g["episode_position"], f["position"], "#B9C6DD", extra='halign="right" valign="center" noWrap="1"')
    tag("message", g["message"], max(12, f["status"]), "#FACC15", z=8, extra='valign="center" halign="center" noWrap="0"')
    tag("footer", (0, g["footer_y"], width, g["footer_h"]), max(10, int(px(20))), "#D8E1EF", "#080E1A", z=10, transparent=0, extra='halign="center" valign="center"')
    lines.append("</screen>")
    return "\n".join(lines)

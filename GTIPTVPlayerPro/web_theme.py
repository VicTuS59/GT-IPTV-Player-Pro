# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Responsive geometry for the receiver's web-session approval screen."""

from xml.sax.saxutils import escape

from .paths import plugin_path
from .typography import text_scale_percent


WEB_ART_SIZES = (
    ("2160", 3840, 2160),
    ("1080", 1920, 1080),
    ("720", 1280, 720),
    ("sd576", 720, 576),
    ("sd480", 720, 480),
    ("sd640", 640, 480),
)


def web_layout(width, height, font_profile=None):
    width, height = max(1, int(width)), max(1, int(height))
    scale = min(width / 1920.0, height / 1080.0)
    compact = width <= 800

    def s(value):
        return max(1, int(round(value * scale)))

    footer_scale = min(width / 1440.0, height / 1080.0) if compact else scale
    footer_height = 128 if compact else 100
    footer_y = height - int(round((footer_height + 36) * footer_scale))
    panel_x, panel_y = int(round(width * 0.096)), int(round(height * 0.151))
    panel_w = width - 2 * panel_x
    panel_h = max(1, footer_y - s(28) - panel_y)
    panel = (panel_x, panel_y, panel_w, panel_h)

    def centered(y, w, h):
        return ((width - w) // 2, panel_y + int(round(y * panel_h)), w, h)

    status = centered(0.202, int(round(panel_w * 0.36)), s(64))
    address = centered(0.405, int(round(panel_w * (0.78 if compact else 0.60))), s(76))
    # Match the browser's 3+3 grouping in one large, centered code field.
    code_surface = centered(0.620, int(round(panel_w * 0.49)),
                            max(s(120), int(round(panel_h * 0.152))))
    text_w = max(1, panel_w - s(96))
    boxes = {
        "header": (int(round(width * 0.03)), int(round(height * 0.028)),
                   int(round(width * 0.52)), s(72)),
        "brand": (int(round(width * 0.57)), int(round(height * 0.028)),
                  int(round(width * 0.40)), s(72)),
        "status": (status[0] + s(12), status[1], status[2] - s(24), status[3]),
        "address_title": centered(0.326, text_w, s(50)),
        "address": (address[0] + s(20), address[1] + s(4),
                    address[2] - s(40), address[3] - s(8)),
        "code_title": centered(0.540, text_w, s(50)),
        "code": (code_surface[0] + s(20), code_surface[1] + s(4),
                 code_surface[2] - s(40), code_surface[3] - s(8)),
        "detail": centered(0.795, text_w, int(round(panel_h * 0.144))),
        "security": centered(0.946, text_w, max(s(32), int(round(panel_h * 0.043)))),
    }
    base_fonts = {"header": 46, "brand": 30, "status": 34,
                  "address_title": 34, "address": 32, "code_title": 34,
                  "detail": 36, "security": 22}
    fonts, minimums = {}, {}
    for name, box in boxes.items():
        code = name == "code"
        role = "display" if code else ("title" if name in ("header", "brand") else "body")
        base = 92 if code else base_fonts[name]
        floor = 36 if code else (18 if name == "header" else (12 if name == "security" else 14))
        preferred = max(floor if compact else 1, s(base))
        preferred = int(round(preferred * text_scale_percent(font_profile, role=role) / 100.0))
        fonts[name] = min(preferred, max(1, int(box[3] * (0.48 if name == "detail" else 0.78))))
        minimums[name] = min(fonts[name], max(10 if compact else 1, s(16 if name == "security" else 24)))

    if compact:
        aspect = float(width) / height
        variant = min(WEB_ART_SIZES[3:], key=lambda item: abs(item[1] / float(item[2]) - aspect))[0]
    elif width <= 1280 and height <= 720:
        variant = "720"
    elif width <= 1920 and height <= 1080:
        variant = "1080"
    else:
        variant = "2160"
    return {
        "width": width, "height": height, "scale": scale, "compact": compact,
        "panel": panel, "status_surface": status, "address_surface": address,
        "code_surface": code_surface, "boxes": boxes, "fonts": fonts, "minimums": minimums,
        "globe": centered(0.045, s(96), s(96)),
        "separator": (int(round(width * 0.03)), int(round(height * 0.116)),
                      int(round(width * 0.94)), s(2)),
        "background": plugin_path("skin", "images", "screen-web-glass-r31-{}.png".format(variant)),
        "footer_options": {"stacked": compact, "panel_height": footer_height,
                           "bottom_padding": 36, "design_size": (1440, 1080) if compact else None,
                           "side_padding": 24 if compact else None, "skin_fonts_scaled": True},
    }


def web_skin(layout):
    """Use native transparent labels over an opaque, text-free rounded backdrop.

    The backdrop avoids image-specific cornerRadius support and the opaque
    label rectangles that previously covered the glass panel on OpenPLi.
    """
    colors = {"brand": "#A261FF", "status": "#DDE8FA", "address": "#EAF4FF",
              "security": "#F7B955", "detail": "#E0EAFA"}
    parts = ['<screen name="GTWebInterfaceScreen" position="0,0" size="{},{}" '
             'flags="wfNoBorder" backgroundColor="#061224">'.format(layout["width"], layout["height"]),
             '<widget name="web_background" position="0,0" size="{},{}" '
             'pixmap="{}" scale="1" zPosition="0" />'.format(
                 layout["width"], layout["height"], escape(layout["background"], {'"': '&quot;'}))]
    for name, box in layout["boxes"].items():
        parts.append('<widget name="{name}" position="{x},{y}" size="{w},{h}" '
                     'font="Regular;{font}" foregroundColor="{color}" '
                     'transparent="1" zPosition="2" halign="{align}" valign="center" '
                     '{wrap}/>'.format(name=name, x=box[0], y=box[1], w=box[2], h=box[3],
                                      font=layout["fonts"][name], color=colors.get(name, "#FFFFFF"),
                                      align="left" if name == "header" else ("right" if name == "brand" else "center"),
                                      wrap='' if name in ("address", "detail") else 'noWrap="1" '))
    parts.append('</screen>')
    return "\n".join(parts)

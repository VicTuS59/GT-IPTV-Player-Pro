# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Text-free glass surfaces for the approved six-row Appearance screen."""

from collections import OrderedDict

from .channel_highlight import (
    DEFAULT_CHANNEL_HIGHLIGHT, channel_highlight_art,
    normalize_channel_highlight, normalize_selection_border, selection_border_art,
)
from .paths import plugin_path
from .i18n import _
from .typography import fit_dynamic_text, font_px


APPEARANCE_DESKTOPS = (
    ("2160", 3840, 2160), ("1080", 1920, 1080), ("720", 1280, 720),
    ("sd576", 720, 576), ("sd480", 720, 480), ("sd640", 640, 480),
)


def set_appearance_pixmap(screen, component, path):
    """Share native RGBA surfaces within this page; never flatten their alpha."""
    instance = getattr(component, "instance", None)
    if instance is None:
        return False
    signature = (id(instance), path)
    if getattr(component, "_appearance_image_signature", None) == signature:
        return True
    pool = getattr(screen, "_appearance_image_pool", None)
    if pool is None:
        pool = screen._appearance_image_pool = OrderedDict()
        if hasattr(screen, "onClose"):
            screen.onClose.append(pool.clear)
    pixmap = pool.get(path)
    if pixmap is None and callable(getattr(instance, "setPixmap", None)):
        try:
            from Tools.LoadPixmap import LoadPixmap
            pixmap = LoadPixmap(path=path)
        except (ImportError, AttributeError, OSError, RuntimeError, TypeError, ValueError):
            pixmap = None
    try:
        if pixmap is not None:
            instance.setPixmap(pixmap)
            pool[path] = pixmap
            pool.move_to_end(path)
            while len(pool) > 10:
                pool.popitem(last=False)
        else:
            instance.setPixmapFromFile(path)
        component._appearance_image_signature = signature
        return True
    except (AttributeError, OSError, RuntimeError, TypeError, ValueError):
        return False


def appearance_pixmap(screen, factory):
    """Keep the exact shipped skin, but load repeated art once during applySkin."""
    component = factory()
    original = getattr(component, "applySkin", None)
    if callable(original):
        def apply_skin(desktop, parent):
            attributes = list(getattr(component, "skinAttributes", ()) or ())
            path = next((value for name, value in attributes if name == "pixmap"),
                        getattr(component, "_appearance_skin_path", None))
            if path is not None:
                component._appearance_skin_path = path
                component.skinAttributes = [(name, value) for name, value in attributes if name != "pixmap"]
            result = original(desktop, parent)
            if path is not None:
                set_appearance_pixmap(screen, component, path)
            return result
        component.applySkin = apply_skin
        if hasattr(screen, "onClose"):
            def cleanup():
                try:
                    del component.applySkin
                except AttributeError:
                    pass
            screen.onClose.append(cleanup)
    return component


def appearance_variant(width, height):
    for name, w, h in APPEARANCE_DESKTOPS:
        if (width, height) == (w, h):
            return name
    if width <= 1280 and height <= 720:
        return "720"
    if width <= 1920 and height <= 1080:
        return "1080"
    return "2160"


def appearance_geometry(width, height):
    """Fill SD desktops too; keep font scaling proportional to the shorter axis."""
    x = lambda v: int(round(v * width / 1920.0))
    y = lambda v: int(round(v * height / 1080.0))
    rect = lambda a, b, c, d: (x(a), y(b), max(1, x(c)), max(1, y(d)))
    return {
        "panel": rect(70, 120, 1780, 800),
        "section": rect(100, 152, 1720, 76),
        "header": rect(70, 35, 900, 75),
        "brand": rect(1340, 35, 510, 75),
        "message": rect(105, 844, 1700, 62),
        "rows": tuple(rect(100, 268 + i * 94, 1720, 78) for i in range(6)),
        "labels": tuple(rect(144, 268 + i * 94, 930, 78) for i in range(6)),
        "values": tuple(rect(1125, 277 + i * 94, 680, 60) for i in range(6)),
        "value_text": tuple(rect(1285 if i in (3, 4) else 1125,
                                  277 + i * 94, 520 if i in (3, 4) else 680, 60)
                            for i in range(6)),
        "swatches": tuple(rect(1145, 285 + i * 94, 112, 44) for i in (3, 4)),
    }


def appearance_art(kind, width, height, color=None):
    if kind not in ("panel", "section", "row", "value", "fill", "frame"):
        raise ValueError("Unknown appearance glass surface")
    suffix = ""
    if kind in ("fill", "frame"):
        normalizer = normalize_channel_highlight if kind == "fill" else normalize_selection_border
        suffix = "-" + normalizer(color)
    return plugin_path("skin", "images", "appearance-r39-{}-{}{}.png".format(
        kind, appearance_variant(width, height), suffix))


def appearance_font(width, height, size, **kwargs):
    factor = min(width / 1920.0, height / 1080.0)
    px = lambda value: max(1, int(round(value * factor)))
    return font_px(px, size, **kwargs)


def glass_appearance_skin(width, height, fill=DEFAULT_CHANNEL_HIGHLIGHT, border=None):
    fill = normalize_channel_highlight(fill)
    border = normalize_selection_border(border, fill)
    geo = appearance_geometry(width, height)
    parts = ['<screen name="GTAppearanceSettingsScreen" position="0,0" size="{},{}" '
             'flags="wfNoBorder" backgroundColor="#050914">'.format(width, height),
             '<widget name="app_bg" position="0,0" size="{},{}" zPosition="0" />'.format(width, height),
             '<widget name="top_accent" position="0,0" size="1,1" font="Regular;1" transparent="1" />']

    def picture(name, box, path, z):
        a, b, c, d = box
        parts.append('<widget name="{}" position="{},{}" size="{},{}" '
                     'pixmap="{}" alphatest="blend" scale="1" zPosition="{}" />'.format(
                         name, a, b, c, d, path, z))

    def label(name, box, size, z=6, align="left", color="#FFFFFF", role="body"):
        a, b, c, d = box
        font = appearance_font(width, height, size, role=role)
        parts.append('<widget name="{}" position="{},{}" size="{},{}" '
                     'font="Regular;{}" foregroundColor="{}" transparent="1" '
                     'zPosition="{}" valign="center" halign="{}" noWrap="1" />'.format(
                         name, a, b, c, d, font, color, z, align))

    picture("panel", geo["panel"], appearance_art("panel", width, height), 1)
    picture("section_glass", geo["section"], appearance_art("section", width, height), 2)
    label("header", geo["header"], 40, role="title")
    label("brand", geo["brand"], 24, align="right", color="#16C9F4", role="title")
    label("section", geo["section"], 32, align="center", role="title")
    for i in range(6):
        picture("row_bg_{}".format(i), geo["rows"][i], appearance_art("row", width, height), 2)
        picture("row_focus_{}".format(i), geo["rows"][i], appearance_art("fill", width, height, fill), 3)
        picture("value_bg_{}".format(i), geo["values"][i], appearance_art("value", width, height), 4)
        picture("row_marker_{}".format(i), geo["rows"][i], appearance_art("frame", width, height, border), 5)
        label("row_label_{}".format(i), geo["labels"][i], 28)
        label("row_value_{}".format(i), geo["value_text"][i], 27, align="center")
    picture("channel_color_swatch", geo["swatches"][0], channel_highlight_art(fill, "swatch"), 7)
    picture("selection_border_swatch", geo["swatches"][1], selection_border_art(border), 7)
    label("message", geo["message"], 22, color="#16C9F4")
    # The existing remote footer compositor supplies the same four native,
    # translated controls; no text or key captions are drawn into a PNG.
    label("footer", (0, height - max(1, height * 86 // 1080), width,
                     max(1, height * 86 // 1080)), 20)
    return "\n".join(parts) + "</screen>"


def refresh_appearance_palette(screen, width, height, fill, border):
    """Update fill and frame independently without touching any other screen."""
    fill = normalize_channel_highlight(fill)
    border = normalize_selection_border(border, fill)
    instances = []
    jobs = []
    for i in range(6):
        for prefix, kind, color in (("row_focus_", "fill", fill), ("row_marker_", "frame", border)):
            component = screen[prefix + str(i)]
            instance = getattr(component, "instance", None)
            if instance is None:
                return False
            instances.append(id(instance))
            jobs.append((component, instance, appearance_art(kind, width, height, color)))
    key = (width, height, fill, border, tuple(instances))
    if getattr(screen, "_appearance_palette_key", None) == key:
        return True
    for component, instance, path in jobs:
        if not set_appearance_pixmap(screen, component, path):
            return False
    screen._appearance_palette_key = key
    pool = getattr(screen, "_appearance_image_pool", {})
    active = {appearance_art(kind, width, height) for kind in ("panel", "section", "row", "value")}
    active.update((appearance_art("fill", width, height, fill),
                   appearance_art("frame", width, height, border),
                   channel_highlight_art(fill, "swatch"), selection_border_art(border)))
    for path in list(pool):
        if path not in active:
            pool.pop(path, None)
    return True


def fit_appearance_text(screen, width, height):
    fonts = {size: appearance_font(width, height, size) for size in (28, 27, 22)}
    minimums = {size: appearance_font(width, height, size, value="standard") for size in (18, 16)}
    for i in range(6):
        for prefix, preferred, minimum, fallback in (
                ("row_label_", 28, 18, 72), ("row_value_", 27, 18, 28)):
            component = screen[prefix + str(i)]
            getter = getattr(component, "getText", None)
            text = (_(screen.LABELS[i]) if prefix == "row_label_" else
                    getter() if callable(getter) else getattr(component, "text", ""))
            fit_dynamic_text(component, text, max_lines=1,
                             preferred_size=fonts[preferred],
                             min_size=minimums[minimum],
                             fallback_chars=fallback)
    component = screen["message"]
    getter = getattr(component, "getText", None)
    text = getter() if callable(getter) else getattr(component, "text", "")
    fit_dynamic_text(component, text, max_lines=1,
                     preferred_size=fonts[22],
                     min_size=minimums[16],
                     fallback_chars=110)

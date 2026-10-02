# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import math
import os
import re
import unicodedata
from types import MethodType

try:
    from Components.Label import Label
    from Components.Pixmap import Pixmap
except ImportError:  # Skin builders are also exercised outside Enigma2.
    Label = None
    Pixmap = None

from .channel_highlight import (
    apply_selection_color,
    refresh_screen_selection,
    saved_selection_color,
    saved_selection_palette,
)
from .i18n import (
    N_,
    _,
    device_language,
    localized_upper,
)
from .typography import (
    current_text_size,
    fit_dynamic_text,
    scale_skin_fonts,
    text_scale_percent,
)


FOOTER_ACTION_COLOR = "#D8E1EF"
FOOTER_DIVIDER_COLOR = "#0E7490"
FOOTER_DESIGN_WIDTH = 1920.0
FOOTER_DESIGN_HEIGHT = 1080.0
FOOTER_PANEL_X = 45
FOOTER_PANEL_Y_FROM_BOTTOM = 112
FOOTER_PANEL_WIDTH = 1820
FOOTER_PANEL_HEIGHT = 100
FOOTER_STAGE_HEIGHT = 184
FOOTER_STAGE_PAD = 8
FOOTER_KEY_HEIGHT = 50
FOOTER_KEY_Y_OFFSET = 27
FOOTER_INNER_GAP = 22
FOOTER_GROUP_GAP = 100
FOOTER_DIVIDER_Y_OFFSET = 25
FOOTER_DIVIDER_HEIGHT = 54
FOOTER_DIVIDER_WIDTH = 3
FOOTER_KEY_WIDTH_FACTOR = 1.55
FOOTER_STACKED_KEY_HEIGHT = 32
FOOTER_STACKED_KEY_WIDTH = 76
FOOTER_STACKED_ACTION_FONT = 28
FOOTER_STACKED_MIN_ACTION_FONT = 26
FOOTER_STACKED_Y_SHIFT = 8

_ASSET_DIR = os.path.join(os.path.dirname(__file__), "skin", "images")
_FOOTER_PANEL_ASSET = os.path.join(
    _ASSET_DIR,
    "remote-footer-panel-v42.png",
)
_FOOTER_STAGE_ASSET = os.path.join(
    _ASSET_DIR,
    "remote-footer-stage-v43.png",
)
_KEY_ASSETS = {
    "cyan": os.path.join(_ASSET_DIR, "remote-key-cyan-v42.png"),
    "red": os.path.join(_ASSET_DIR, "remote-key-red-v42.png"),
    "green": os.path.join(_ASSET_DIR, "remote-key-green-v42.png"),
    "yellow": os.path.join(_ASSET_DIR, "remote-key-yellow-v42.png"),
    "blue": os.path.join(_ASSET_DIR, "remote-key-blue-v42.png"),
    "neutral": os.path.join(_ASSET_DIR, "remote-key-neutral-v42.png"),
}

_FOOTER_WIDGET = re.compile(
    r'<widget\s+name="footer"(?=\s|/?>)[^>]*/>',
    re.DOTALL,
)
_SCREEN_SIZE = re.compile(r'<screen\b[^>]*\bsize="(\d+),(\d+)"')

_KEY_WIDTH_FACTORS = {
    "left_right": FOOTER_KEY_WIDTH_FACTOR,
    "up_down": FOOTER_KEY_WIDTH_FACTOR,
    "arrows": FOOTER_KEY_WIDTH_FACTOR,
    "channel": FOOTER_KEY_WIDTH_FACTOR,
    "channel_page": 3.6,
    "right": FOOTER_KEY_WIDTH_FACTOR,
    "ok": FOOTER_KEY_WIDTH_FACTOR,
    "menu": FOOTER_KEY_WIDTH_FACTOR,
    "exit": FOOTER_KEY_WIDTH_FACTOR,
    "red": FOOTER_KEY_WIDTH_FACTOR,
    "green": FOOTER_KEY_WIDTH_FACTOR,
    "yellow": FOOTER_KEY_WIDTH_FACTOR,
    "blue": FOOTER_KEY_WIDTH_FACTOR,
}

_ACTION_TEXT = {
    "account_select": N_("Account"),
    "source_select": N_("Source"),
    "action_select": N_("Action"),
    "confirm": N_("Select"),
    "settings": N_("Settings"),
    "exit": N_("Exit"),
    "select": N_("Select"),
    "change": N_("Change"),
    "edit": N_("Edit"),
    "rename": N_("Rename"),
    "continue": N_("Continue"),
    "save": N_("Save"),
    "server_test": N_("Test"),
    "api_test": N_("API test"),
    "delete_account": N_("Delete"),
    "connect_account": N_("Connect"),
    "delete_source": N_("Delete"),
    "connect_source": N_("Connect"),
    "retry": N_("Retry"),
    "navigate": N_("Navigate"),
    "page": N_("Page"),
    "open": N_("Open"),
    "refresh": N_("Refresh"),
    "back": N_("Back"),
    "cancel": N_("Cancel"),
    "play": N_("Play"),
    "download": N_("Download"),
    "channel_list": N_("Channels"),
    "xmltv_epg": N_("EPG settings"),
    "metadata": "TMDb",
    "fullscreen": N_("Full screen"),
    "select_change": N_("Change"),
    "navigate_change": N_("Change"),
    "accounts": N_("Accounts"),
    "sources": N_("Sources"),
    "storage": N_("Storage"),
    "remove": N_("Remove"),
    "search": N_("Search"),
    "clear_search": N_("Clear search"),
    "favorite": N_("Favorite"),
    "remove_favorite": N_("Remove"),
    "filter": N_("Filter"),
    "new": N_("New"),
    "move": N_("Move"),
    "move_page": N_("Page"),
    "reset": N_("Reset"),
    "visibility": N_("Show / Hide"),
    "mark": N_("Mark"),
    "hide_all": N_("Hide All"),
    "quality": N_("Quality"),
    "new_search": N_("New search"),
    "clear": N_("Clear"),
    "preview": N_("Preview"),
    "rescan": N_("Rescan"),
    "about": N_("About"),
}


def footer_item(key, action, weight=1.0):
    return (str(key), str(action), float(weight))


def _item_parts(item):
    if len(item) > 2:
        return str(item[0]), str(item[1]), max(0.25, float(item[2]))
    return str(item[0]), str(item[1]), 1.0


def _text_units(value):
    """Approximate proportional-font width without depending on a GUI instance."""
    units = 0.0
    for character in str(value or ""):
        if unicodedata.combining(character):
            continue
        if character.isspace():
            units += 0.50
        elif unicodedata.east_asian_width(character) in ("W", "F"):
            units += 1.80
        elif character in "MW@%&":
            units += 1.35
        elif character in "ilI1|.,'`!:;":
            units += 0.50
        else:
            units += 1.00
    return units


def _footer_scale(screen_w, screen_h, design_size=None):
    design_w, design_h = design_size or (FOOTER_DESIGN_WIDTH, FOOTER_DESIGN_HEIGHT)
    return min(
        float(screen_w) / float(design_w),
        float(screen_h) / float(design_h),
    )


def _scaled(value, scale):
    return max(1, int(math.floor((float(value) * scale) + 0.5)))


def _footer_geometry(skin, panel_height=None, bottom_padding=None, design_size=None):
    screen_match = _SCREEN_SIZE.search(skin)
    screen_w = int(screen_match.group(1)) if screen_match is not None else 1280
    screen_h = int(screen_match.group(2)) if screen_match is not None else 720
    scale = _footer_scale(screen_w, screen_h, design_size)
    x = _scaled(FOOTER_PANEL_X, scale)
    height = FOOTER_PANEL_HEIGHT if panel_height is None else panel_height
    bottom = FOOTER_PANEL_Y_FROM_BOTTOM if bottom_padding is None else height + bottom_padding
    y = max(0, screen_h - _scaled(bottom, scale))
    # Dialogs provide their own design size so key badges and captions retain
    # the same readable size as the full-screen guide, with equal side margins.
    w = (max(1, screen_w - 2 * x) if design_size is not None
         else min(max(1, screen_w - x), _scaled(FOOTER_PANEL_WIDTH, scale)))
    h = _scaled(height, scale)
    h = min(max(1, screen_h - y), h)
    return x, y, w, h, scale, screen_w, screen_h


def _xml_attribute(value):
    return str(value).replace("&", "&amp;").replace('"', "&quot;")


def _key_asset(key):
    if key in ("red", "green", "yellow", "blue"):
        return _KEY_ASSETS[key]
    if key in ("menu", "exit"):
        return _KEY_ASSETS["neutral"]
    return _KEY_ASSETS["cyan"]


def _stacked_action_fonts(scale, font_profile=None):
    factor = text_scale_percent(font_profile) / 100.0
    return (
        _scaled(FOOTER_STACKED_ACTION_FONT, scale * factor),
        _scaled(FOOTER_STACKED_MIN_ACTION_FONT, scale),
    )


def decorate_remote_footer(
    skin,
    items,
    alternate_items=None,
    mask_legacy_frame=False,
    transparent_panel=False,
    show_dividers=True,
    font_profile=None,
    skin_fonts_scaled=False,
    stacked=False,
    panel_height=None,
    bottom_padding=None,
    design_size=None,
    side_padding=None,
    content_y_offset=0,
):
    """Replace every legacy footer with the same bottom-aligned guide."""
    if font_profile is None:
        font_profile = current_text_size()
    if not skin_fonts_scaled:
        skin = scale_skin_fonts(skin, font_profile)
    items = tuple(items or ())
    if not items:
        return apply_selection_color(skin)
    alternate_items = tuple(alternate_items or ())
    layout_sets = (items,) + ((alternate_items,) if alternate_items else ())
    match = _FOOTER_WIDGET.search(skin)
    x, y, w, h, scale, screen_w, screen_h = _footer_geometry(
        skin, panel_height, bottom_padding, design_size)
    if stacked and bottom_padding is None:
        # Lower the entire guide while preserving its two-line caption area.
        # Clamp the origin so the full panel remains inside the screen.
        y = min(max(0, screen_h - h), y + _scaled(FOOTER_STACKED_Y_SHIFT, scale))
    hidden = (
        '<widget name="footer" position="0,0" size="1,1" '
        'font="Regular;1" transparent="1" zPosition="1" />'
    )
    if match is not None:
        skin = _FOOTER_WIDGET.sub(hidden, skin, count=1)
    else:
        skin = skin.replace("</screen>", hidden + "\n</screen>", 1)

    key_h = _scaled(
        FOOTER_STACKED_KEY_HEIGHT if stacked else FOOTER_KEY_HEIGHT,
        scale,
    )
    if side_padding is None:
        padding = _scaled(18, scale) if stacked else key_h
    else:
        padding = _scaled(side_padding, scale)
    max_gap = _scaled(12 if stacked else FOOTER_GROUP_GAP, scale)
    min_gap = _scaled(12 if stacked else 16, scale)
    weights = []
    for index, item in enumerate(items):
        candidates = [item]
        if index < len(alternate_items):
            candidates.append(alternate_items[index])
        weights.append(max(_item_parts(candidate)[2] for candidate in candidates))
    total_weight = sum(weights) or 1.0
    # Some full-screen wallpapers frame the footer below the generic content
    # baseline. Shift only the keys, captions and dividers inside that frame;
    # the shared panel remains anchored to the same screen-bottom geometry.
    content_shift = (
        _scaled(max(0, int(content_y_offset)), scale)
        if content_y_offset else 0
    )
    key_y = y + _scaled(2 if stacked else FOOTER_KEY_Y_OFFSET, scale) + content_shift
    action_y = key_y + key_h + _scaled(3, scale) if stacked else key_y
    action_h = max(1, y + h - action_y) if stacked else key_h
    # Screens opt in to captions below their keys. The default VOD panel
    # remains unchanged; discovery reserves its own space below the cards.
    # Other screens retain the single-row layout and translated-width fitting.
    font_factor = text_scale_percent(font_profile) / 100.0
    footer_font_cap = max(1, key_h - 2)
    key_font = min(
        footer_font_cap,
        max(8, _scaled(20 if stacked else 22, scale * font_factor)),
    )
    action_font = min(
        footer_font_cap,
        max(8, _scaled(22, scale * font_factor)),
    )
    if stacked:
        action_font = _stacked_action_fonts(scale, font_profile)[0]
    inner_gap = _scaled(FOOTER_INNER_GAP, scale)

    def minimum_widths(font_size):
        key_widths = []
        minimums = []
        for index, item in enumerate(items):
            candidates = [layout[index] for layout in layout_sets if index < len(layout)]
            key_factors = [
                _KEY_WIDTH_FACTORS.get(
                    _item_parts(candidate)[0],
                    FOOTER_KEY_WIDTH_FACTOR,
                )
                for candidate in candidates
                if _item_parts(candidate)[0]
            ]
            key_factor = max(key_factors or (FOOTER_KEY_WIDTH_FACTOR,))
            key_width = max(1, int(round(key_h * key_factor)))
            if stacked:
                key_width = _scaled(
                    FOOTER_STACKED_KEY_WIDTH * key_factor / FOOTER_KEY_WIDTH_FACTOR,
                    scale,
                )
                key_widths.append(key_width)
                # Slot geometry is independent of the translation.  A language
                # change can wrap captions without moving or shrinking keys.
                minimums.append(key_width)
                continue
            action_width = font_size
            for candidate in candidates:
                action = _item_parts(candidate)[1]
                if not action:
                    continue
                action_value = localized_upper(remote_action_text(action))
                text_width = int(
                    math.ceil(_text_units(action_value) * font_size * 0.58)
                )
                action_width = max(action_width, text_width + font_size)
            key_widths.append(key_width)
            minimums.append(key_width + inner_gap + action_width)
        return key_widths, minimums

    key_widths, minimums = minimum_widths(action_font)
    divider_count = max(0, len(items) - 1)
    while (
        not stacked
        and action_font > 6
        and sum(minimums) + (padding * 2) + (min_gap * divider_count) > w
    ):
        action_font -= 1
        key_widths, minimums = minimum_widths(action_font)
    if divider_count:
        available_gap = max(
            0,
            (w - (padding * 2) - sum(minimums)) // divider_count,
        )
        gap = max(min_gap, min(max_gap, available_gap))
    else:
        gap = 0
    usable_w = max(1, w - (padding * 2) - (gap * divider_count))
    minimum_total = sum(minimums) or 1
    if minimum_total <= usable_w:
        extra = usable_w - minimum_total
        raw_widths = [
            minimum + (extra * weight / total_weight)
            for minimum, weight in zip(minimums, weights)
        ]
    else:
        shrink = float(usable_w) / float(minimum_total)
        raw_widths = [minimum * shrink for minimum in minimums]
    slot_widths = []
    remaining = usable_w
    for index, raw_width in enumerate(raw_widths):
        remaining_slots = len(raw_widths) - index - 1
        if not remaining_slots:
            slot_width = remaining
        else:
            slot_width = max(
                1,
                min(int(round(raw_width)), remaining - remaining_slots),
            )
        slot_widths.append(slot_width)
        remaining -= slot_width

    if mask_legacy_frame:
        stage_h = min(screen_h, _scaled(FOOTER_STAGE_HEIGHT, scale))
        stage_y = max(0, screen_h - stage_h)
        stage_pad = _scaled(FOOTER_STAGE_PAD, scale)
        stage_x = max(0, x - stage_pad)
        stage_w = min(screen_w - stage_x, w + (stage_pad * 2))
        stage = (
            '<widget name="remote_footer_stage" position="{stage_x},{stage_y}" '
            'size="{stage_w},{stage_h}" pixmap="{pixmap}" '
            'alphatest="blend" scale="1" zPosition="29" />'
        ).format(
            stage_x=stage_x,
            stage_y=stage_y,
            stage_w=stage_w,
            stage_h=stage_h,
            pixmap=_xml_attribute(_FOOTER_STAGE_ASSET),
        )
    else:
        stage = (
            '<widget name="remote_footer_stage" position="0,0" size="1,1" '
            'alphatest="blend" scale="1" zPosition="1" />'
        )
    if transparent_panel:
        panel = (
            '<widget name="remote_footer_bg" position="0,0" size="1,1" '
            'alphatest="blend" scale="1" zPosition="1" />'
        )
    else:
        panel = (
            '<widget name="remote_footer_bg" position="{x},{y}" '
            'size="{w},{h}" pixmap="{pixmap}" '
            'alphatest="blend" scale="1" zPosition="30" />'
        ).format(
            x=x,
            y=y,
            w=w,
            h=h,
            pixmap=_xml_attribute(_FOOTER_PANEL_ASSET),
        )
    widgets = [
        stage,
        panel,
    ]
    cursor = x + padding
    for index, item in enumerate(items):
        parts = _item_parts(item)
        key = parts[0]
        slot_w = slot_widths[index]
        key_w = key_widths[index]
        if stacked:
            key_w = min(key_w, slot_w)
            key_x = cursor + ((slot_w - key_w) // 2)
            action_x = cursor
            action_w = slot_w
        else:
            key_w = min(key_w, max(1, slot_w - inner_gap - 1))
            key_x = cursor
            action_x = cursor + key_w + inner_gap
            action_w = max(1, slot_w - key_w - inner_gap)
        key_pixmap = _key_asset(key)
        widgets.append(
            '<widget name="remote_key_bg_{index}" '
            'position="{key_x},{key_y}" size="{key_w},{key_h}" '
            'pixmap="{pixmap}" alphatest="blend" scale="1" '
            'zPosition="31" />'.format(
                index=index,
                key_x=key_x,
                key_y=key_y,
                key_w=key_w,
                key_h=key_h,
                pixmap=_xml_attribute(key_pixmap),
            )
        )
        widgets.append(
            '<widget name="remote_key_{index}" position="{key_x},{key_y}" '
            'size="{key_w},{key_h}" font="Regular;{key_font}" '
            'foregroundColor="#FFFFFF" transparent="1" zPosition="32" '
            'valign="center" halign="center" />'.format(
                index=index,
                key_x=key_x,
                key_y=key_y,
                key_w=key_w,
                key_h=key_h,
                key_font=key_font,
            )
        )
        widgets.append(
            '<widget name="remote_action_{index}" position="{action_x},{action_y}" '
            'size="{action_w},{action_h}" font="Regular;{action_font}" '
            'foregroundColor="{action_color}" transparent="1" zPosition="32" '
            'valign="{action_valign}" halign="{action_halign}" '
            'noWrap="{action_nowrap}" />'.format(
                index=index,
                action_x=action_x,
                action_y=action_y,
                action_w=action_w,
                action_h=action_h,
                action_font=action_font,
                action_color=FOOTER_ACTION_COLOR,
                action_valign="top" if stacked else "center",
                action_halign="center" if stacked else "left",
                action_nowrap="0" if stacked else "1",
            )
        )
        if index < len(items) - 1:
            divider_x = cursor + slot_w + ((gap + 1) // 2)
            divider_y = y + _scaled(FOOTER_DIVIDER_Y_OFFSET, scale) + content_shift
            divider_h = _scaled(FOOTER_DIVIDER_HEIGHT, scale)
            divider_w = _scaled(FOOTER_DIVIDER_WIDTH, scale)
            if show_dividers:
                widgets.append(
                    '<widget name="remote_divider_{index}" '
                    'position="{divider_x},{divider_y}" size="{divider_w},{divider_h}" '
                    'font="Regular;1" backgroundColor="{divider_color}" transparent="0" '
                    'zPosition="31" />'.format(
                        index=index,
                        divider_x=divider_x,
                        divider_y=divider_y,
                        divider_w=divider_w,
                        divider_h=divider_h,
                        divider_color=FOOTER_DIVIDER_COLOR,
                    )
                )
            else:
                widgets.append(
                    '<widget name="remote_divider_{index}" '
                    'position="{divider_x},{divider_y}" size="{divider_w},{divider_h}" '
                    'font="Regular;1" transparent="1" zPosition="31" />'.format(
                        index=index,
                        divider_x=divider_x,
                        divider_y=divider_y,
                        divider_w=divider_w,
                        divider_h=divider_h,
                    )
                )
        cursor += slot_w + gap
    return apply_selection_color(
        skin.replace("</screen>", "\n".join(widgets) + "\n</screen>", 1)
    )


def remote_action_text(action):
    action = str(action)
    if action == "open_play":
        return _(_ACTION_TEXT["open"])
    return _(_ACTION_TEXT.get(action, action))


def _key_text(key):
    if key == "left_right":
        return "◀▶"
    if key == "up_down":
        return "▲▼"
    if key == "arrows":
        return "▲▼◀▶"
    if key == "channel":
        return "CH±"
    if key == "channel_page":
        return "CH+/CH−"
    if key == "ok":
        return "OK"
    if key == "menu":
        return "MENU"
    if key == "exit":
        return "EXIT"
    if key in ("red", "green", "yellow", "blue"):
        return ""
    return localized_upper(key)


def _set_action_caption(screen, widget, action):
    text = remote_action_text(action)
    if not getattr(screen, "_remote_footer_stacked", False):
        widget.setText(localized_upper(text))
    elif getattr(widget, "instance", None) is None:
        # Native label geometry is not available until onLayoutFinish.
        widget.setText(text)
    else:
        preferred, minimum = screen._remote_footer_action_fonts
        fit_dynamic_text(
            widget,
            text,
            max_lines=2,
            preferred_size=preferred,
            min_size=minimum,
        )


def _refresh_stacked_captions(screen):
    if not getattr(screen, "_remote_footer_stacked", False):
        return
    items = getattr(screen, "_remote_footer_items", ())
    slots = int(getattr(screen, "_remote_footer_slot_count", len(items)))
    for index, item in enumerate(items[:slots]):
        key, action = _item_parts(item)[:2]
        if key:
            _set_action_caption(
                screen,
                screen["remote_action_{}".format(index)],
                action,
            )


def set_remote_footer(screen, items):
    items = tuple(items or ())
    language = device_language()
    if (
        items == getattr(screen, "_remote_footer_items", None)
        and language == getattr(screen, "_remote_footer_language", None)
    ):
        return
    screen._remote_footer_items = items
    screen._remote_footer_language = language
    slots = int(getattr(screen, "_remote_footer_slot_count", len(items)))
    visible = []
    for index in range(slots):
        key_background = screen["remote_key_bg_{}".format(index)]
        key_widget = screen["remote_key_{}".format(index)]
        action_widget = screen["remote_action_{}".format(index)]
        if index >= len(items):
            visible.append(False)
            key_widget.setText("")
            action_widget.setText("")
            key_background.hide()
            key_widget.hide()
            action_widget.hide()
            continue
        key, action = _item_parts(items[index])[:2]
        if not key:
            visible.append(False)
            key_widget.setText("")
            action_widget.setText("")
            key_background.hide()
            key_widget.hide()
            action_widget.hide()
            continue
        visible.append(True)
        key_widget.setText(_key_text(key))
        _set_action_caption(screen, action_widget, action)
        key_background.show()
        key_widget.show()
        action_widget.show()
    for index in range(max(0, slots - 1)):
        divider = screen["remote_divider_{}".format(index)]
        if visible[index] and any(visible[index + 1 :]):
            divider.show()
        else:
            divider.hide()


def _current_selection_palette():
    """Load the R73 fill/frame pair with a pre-R73 compatibility fallback."""
    loader = globals().get("saved_selection_palette")
    if callable(loader):
        palette = loader()
        if isinstance(palette, (tuple, list)) and len(palette) >= 2:
            return palette[0], palette[1]
    color = saved_selection_color()
    return color, color


def install_remote_footer(screen, items, slot_count=None, stacked=False, design_size=None):
    if Label is None or Pixmap is None:
        raise RuntimeError("Enigma2 remote footer widgets are unavailable")
    items = tuple(items or ())
    slots = max(len(items), int(slot_count or 0))
    screen._remote_footer_slot_count = slots
    screen._remote_footer_stacked = bool(stacked)
    if stacked:
        scale = _footer_geometry(screen.skin, design_size=design_size)[4]
        screen._remote_footer_action_fonts = _stacked_action_fonts(scale)
    for index in range(slots):
        screen["remote_key_bg_{}".format(index)] = Pixmap()
        screen["remote_key_{}".format(index)] = Label("")
        screen["remote_action_{}".format(index)] = Label("")
        if index < slots - 1:
            screen["remote_divider_{}".format(index)] = Label("")
    screen["remote_footer_stage"] = Pixmap()
    screen["remote_footer_bg"] = Pixmap()
    set_remote_footer(screen, items)
    screen._selection_palette_applied = _current_selection_palette()

    def refresh_language():
        if device_language() != getattr(screen, "_remote_footer_language", None):
            set_remote_footer(screen, screen._remote_footer_items)
        else:
            _refresh_stacked_captions(screen)

    def refresh_selection():
        palette = _current_selection_palette()
        if palette != getattr(screen, "_selection_palette_applied", None):
            try:
                refreshed = refresh_screen_selection(
                    screen, palette[0], palette[1]
                )
            except TypeError:
                # Keep compatibility with test doubles and third-party code
                # that still implements the pre-R73 two-argument callback.
                refreshed = refresh_screen_selection(screen, palette[0])
            if refreshed:
                screen._selection_palette_applied = palette

    if stacked:
        callbacks = getattr(screen, "onLayoutFinish", None)
        if callbacks is not None:
            # OpenPLi calls bound methods here, but passes other objects to
            # exec(). A lambda/function therefore crashes during applySkin.
            callbacks.append(MethodType(_refresh_stacked_captions, screen))
    callbacks = getattr(screen, "onShown", None)
    if callbacks is not None and refresh_language not in callbacks:
        callbacks.append(refresh_language)
        callbacks.append(refresh_selection)

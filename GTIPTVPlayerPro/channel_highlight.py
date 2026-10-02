# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Fixed, contrast-conscious colors for selection across receiver screens."""

import re

from .paths import plugin_path


# Stable identifiers are stored in Enigma2 settings; never persist a PNG path
# or an arbitrary user-provided color in the receiver configuration.
CHANNEL_HIGHLIGHTS = (
    ("navy", "#14375E", "#1A426D"),
    ("midnight", "#0C233F", "#173251"),
    ("slate", "#263549", "#36485D"),
    ("indigo", "#292F60", "#3B4578"),
    ("sapphire", "#123F74", "#20568C"),
    ("royal", "#184E84", "#27649A"),
    ("lagoon", "#154960", "#205D77"),
    ("teal", "#0D4C58", "#176471"),
    ("forest", "#24452F", "#32563D"),
    ("emerald", "#145043", "#216353"),
    ("olive", "#48512C", "#5B6035"),
    ("amber", "#644B24", "#795B2B"),
    ("copper", "#6C3F2B", "#804D36"),
    ("ruby", "#632B3A", "#773A4C"),
    ("plum", "#52344A", "#68425D"),
    ("violet", "#483676", "#5A4587"),
    ("classic", "#007DDC", "#286CF1"),
)
DEFAULT_CHANNEL_HIGHLIGHT = "navy"
CHANNEL_HIGHLIGHT_KEYS = tuple(item[0] for item in CHANNEL_HIGHLIGHTS)
_COLORS = {item[0]: item[1:] for item in CHANNEL_HIGHLIGHTS}

# Focus frames must stay saturated on a dark television skin.  R73 derived
# these values by mixing the dark fill colors with too much white; on a real
# receiver the result looked almost grey and neighbouring choices were hard to
# distinguish.  Keep an explicit, vivid pair for every one of the 17 choices.
SELECTION_BORDERS = (
    ("navy", "#2F75C9", "#58A6FF"),
    ("midnight", "#1D5C9E", "#3D86D1"),
    ("slate", "#647D9B", "#91A9C4"),
    ("indigo", "#5B63D9", "#858CFF"),
    ("sapphire", "#1479D8", "#42A5FF"),
    ("royal", "#246FE5", "#5B9DFF"),
    ("lagoon", "#118FAB", "#38C4DF"),
    ("teal", "#0BA39B", "#3DD3C7"),
    ("forest", "#34985A", "#63C982"),
    ("emerald", "#11B883", "#49E0AD"),
    ("olive", "#91A729", "#C7D84F"),
    ("amber", "#D58B14", "#FFC44F"),
    ("copper", "#D56832", "#F4945C"),
    ("ruby", "#D83E63", "#FF6D8E"),
    ("plum", "#AD478C", "#DA75BB"),
    ("violet", "#8D4FE8", "#B77BFF"),
    ("classic", "#0088E8", "#35B5FF"),
)
_BORDER_COLORS = {item[0]: item[1:] for item in SELECTION_BORDERS}


def normalize_channel_highlight(value):
    value = str(value or "").strip().lower()
    return value if value in _COLORS else DEFAULT_CHANNEL_HIGHLIGHT


def normalize_selection_border(value, fallback=DEFAULT_CHANNEL_HIGHLIGHT):
    """Return a stored border palette or the selected-fill fallback.

    R73 separates the focus outline from the selected-row fill.  Missing
    values come from pre-R73 installations, so they deliberately inherit the
    fill palette instead of unexpectedly changing the receiver's appearance.
    """
    value = str(value or "").strip().lower()
    if value in _COLORS:
        return value
    return normalize_channel_highlight(fallback)


def channel_highlight_colors(value):
    return _COLORS[normalize_channel_highlight(value)]


def selection_border_colors(value):
    """Return the vivid two-tone outline selected by the user."""
    return _BORDER_COLORS[normalize_selection_border(value)]


def channel_highlight_art(value, kind="focus"):
    if kind not in ("focus", "swatch"):
        raise ValueError("unknown channel highlight asset")
    name = "channel-{}-{}.png".format(kind, normalize_channel_highlight(value))
    return plugin_path("skin", "images", name)


def selection_border_art(value):
    """Return the true outline-only preview used by Appearance."""
    name = "selection-border-swatch-{}.png".format(
        normalize_selection_border(value)
    )
    return plugin_path("skin", "images", name)


_WIDGET = re.compile(r"<widget\b[^>]*?/>", re.DOTALL)
_NAME = re.compile(r'\bname="([^"]+)"')
_BACKGROUND = re.compile(r'\bbackgroundColor="[^"]+"')
_PIXMAP = re.compile(r'\bpixmap="[^"]+"')
_FOCUS = re.compile(r"(?:^|_)(?:focus|marker)(?:_|$)")
_GLOW = re.compile(r"(?:^|_)glow(?:_|$)")
_SELECTION_ROW = re.compile(r"(?:^|_)selected_\d+(?:_|$)")
_SELECTED_BACKGROUND = re.compile(r"(?:^|_)selected_\d+$")
_DASHBOARD_ART = re.compile(r"dashboard-menu-focus-(?:r39|[a-z]+)\.png")
_ROW_ART = re.compile(r"(?:channel-focus-[a-z]+|focus-r64)\.png")


def saved_selection_palette():
    try:
        from .settings import load_player_settings
        settings = load_player_settings()
        fill = normalize_channel_highlight(settings.channel_highlight)
        border = normalize_selection_border(
            getattr(settings, "selection_border", None), fill
        )
        return fill, border
    except (AttributeError, IOError, OSError, TypeError, ValueError):
        return DEFAULT_CHANNEL_HIGHLIGHT, DEFAULT_CHANNEL_HIGHLIGHT


def saved_selection_color():
    """Compatibility helper returning the selected-item fill palette."""
    return saved_selection_palette()[0]


def saved_selection_border():
    return saved_selection_palette()[1]


def apply_selection_color(skin, value=None, border_value=None):
    """Tint selection fills and outlines; keep semantic colors intact.

    All the receiver pages generate native Enigma2 skin XML. Apply this once
    before the screen opens, after its footer has been composed, so category,
    grid, account, settings and player pages share the saved preference.
    """
    if value is None:
        value, saved_border = saved_selection_palette()
        if border_value is None:
            border_value = saved_border
    value = normalize_channel_highlight(value)
    border_value = normalize_selection_border(border_value, value)
    fill = channel_highlight_colors(value)[0]
    border_left, border_right = selection_border_colors(border_value)
    widget_names = set(
        name.group(1)
        for item in _WIDGET.finditer(skin)
        for name in [_NAME.search(item.group(0))]
        if name is not None
    )

    def selection_widget(name):
        return (
            _FOCUS.search(name)
            or _GLOW.search(name)
            or _SELECTION_ROW.search(name)
            or name.startswith("tab_active_")
        )

    def focus_is_fill(name):
        if name.endswith("_fill") or _SELECTED_BACKGROUND.search(name):
            return True
        if name.startswith("row_focus_"):
            return True
        marker_name = name.replace("focus", "marker", 1)
        return marker_name != name and marker_name in widget_names

    def theme_widget(match):
        widget = match.group(0)
        name_match = _NAME.search(widget)
        if name_match is None:
            return widget
        name = name_match.group(1)
        if not selection_widget(name):
            return widget
        if _PIXMAP.search(widget):
            widget = _DASHBOARD_ART.sub(
                "dashboard-menu-focus-{}.png".format(border_value), widget
            )
            widget = _ROW_ART.sub(
                "channel-focus-{}.png".format(border_value), widget
            )
        background = _BACKGROUND.search(widget)
        if background is None:
            return widget
        if name.endswith(("_top", "_left")) or "marker" in name:
            color = border_left
        elif name.endswith(("_bottom", "_right")):
            color = border_right
        elif "glow" in name:
            color = border_left
        elif focus_is_fill(name):
            color = fill
        else:
            # Plain focus widgets normally sit behind a slightly smaller row
            # or tile and are therefore the visible selection frame.
            color = border_left
        return widget[:background.start()] + 'backgroundColor="{}"'.format(color) + widget[background.end():]

    return _WIDGET.sub(theme_widget, skin)


def refresh_screen_selection(screen, value, border_value=None):
    """Restyle an already open parent screen when Settings has been saved."""
    try:
        from skin import parseColor
    except ImportError:
        return False
    skin = getattr(screen, "skin", "")
    if not skin:
        return False
    themed = apply_selection_color(skin, value, border_value)
    refreshed = False
    for match in _WIDGET.finditer(themed):
        tag = match.group(0)
        name_match = _NAME.search(tag)
        if name_match is None:
            continue
        name = name_match.group(1)
        if not (_FOCUS.search(name) or _GLOW.search(name)
                or _SELECTION_ROW.search(name)
                or name.startswith("tab_active_")):
            continue
        try:
            component = screen[name]
            instance = getattr(component, "instance", None)
        except (KeyError, AttributeError, TypeError):
            continue
        if instance is None:
            continue
        color = _BACKGROUND.search(tag)
        if color is not None:
            try:
                instance.setBackgroundColor(parseColor(color.group(0).split('"')[1]))
                refreshed = True
            except (AttributeError, TypeError, ValueError):
                pass
        art = _PIXMAP.search(tag)
        if art is not None and (
            "channel-focus-" in art.group(0)
            or "dashboard-menu-focus-" in art.group(0)
        ):
            path = art.group(0).split('"')[1]
            for target in (component, instance):
                setter = getattr(target, "setPixmapFromFile", None)
                if not callable(setter):
                    continue
                try:
                    setter(path)
                    refreshed = True
                    break
                except (AttributeError, IOError, OSError, TypeError):
                    continue
        invalidator = getattr(instance, "invalidate", None)
        if callable(invalidator):
            try:
                invalidator()
            except (AttributeError, TypeError):
                pass
    try:
        screen.skin = themed
    except (AttributeError, TypeError):
        pass
    return refreshed


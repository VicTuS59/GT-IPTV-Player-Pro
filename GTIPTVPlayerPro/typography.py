# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Text-size profiles and R80 provider-text overflow protection.

Screen geometry continues to use each module's resolution ``px`` helper.
This module changes only real text fonts (never ``Regular;1`` layout labels),
keeps the R77 standard profile byte-for-byte unchanged, and caps every
enlarged font to the height of its own widget.  Dynamic provider/user values
are fitted separately without changing translated interface labels.
"""

import math
import re
import unicodedata
from collections import OrderedDict

try:
    from enigma import gFont
except ImportError:  # Unit tests and a few stripped-down images omit it.
    gFont = None

from .settings import load_text_size, normalize_text_size


TEXT_SIZE_STANDARD = "standard"
TEXT_SIZE_LARGE = "large"
TEXT_SIZE_VERY_LARGE = "very_large"
TEXT_SCALE_PERCENT = {
    TEXT_SIZE_STANDARD: 100,
    TEXT_SIZE_LARGE: 110,
    TEXT_SIZE_VERY_LARGE: 118,
}

_WIDGET_RE = re.compile(r"<widget\b[^>]*?/>", re.DOTALL)
_FONT_RE = re.compile(r'(\bfont="[^"]*;)(\d+)(")')
_SIZE_RE = re.compile(r'\bsize="(\d+),(\d+)"')
_NAME_RE = re.compile(r'\bname="([^"]+)"')

_DISPLAY_NAMES = frozenset(
    (
        "clock",
        "weather_temp",
        "channel_number",
    )
)
_TITLE_TOKENS = (
    "header",
    "title",
    "brand",
    "caption",
    "screen_label",
    "_tab",
)
_MULTILINE_TOKENS = (
    "message",
    "prompt",
    "description",
    "details",
    "summary",
    "plot",
    "help",
    "about",
    "instructions",
)

_DYNAMIC_TEXT_CACHE_LIMIT = 256
_DYNAMIC_TEXT_MEASURE_CACHE_LIMIT = 512
_DYNAMIC_TEXT_MAX_CHARS = 2048
_DYNAMIC_TEXT_CACHE = OrderedDict()
_DYNAMIC_TEXT_MEASURE_CACHE = OrderedDict()
_ELLIPSIS = "…"
_BIDI_FORMAT_CONTROLS = frozenset(
    "\u061c\u200e\u200f"
    "\u202a\u202b\u202c\u202d\u202e"
    "\u2066\u2067\u2068\u2069\u206a\u206b\u206c\u206d\u206e\u206f"
)


def current_text_size():
    """Return the persisted profile, safely falling back to R77 sizing."""
    try:
        return normalize_text_size(load_text_size())
    except Exception:
        return TEXT_SIZE_STANDARD


def _role_for_name(name):
    name = str(name or "").lower()
    if name in _DISPLAY_NAMES or name.startswith("remote_"):
        return "display"
    if any(token in name for token in _TITLE_TOKENS):
        return "title"
    return "body"


def text_scale_percent(value=None, role="body"):
    """Return the profile percentage, with conservative display caps."""
    profile = normalize_text_size(
        current_text_size() if value is None else value
    )
    percent = TEXT_SCALE_PERCENT[profile]
    if role == "display":
        return 100
    if role == "title":
        return min(percent, 110)
    return percent


def _scaled_number(value, percent):
    return max(
        1,
        int(math.floor((float(value) * float(percent) / 100.0) + 0.5)),
    )


def font_px(
    px,
    base,
    role="body",
    max_height=None,
    vertical_padding=0,
    value=None,
):
    """Scale a design-unit font and optionally cap it to a design box."""
    original = max(1, int(px(base)))
    percent = text_scale_percent(value, role=role)
    result = _scaled_number(original, percent)
    if max_height is not None:
        cap = max(1, int(px(max_height)) - max(0, int(px(vertical_padding))))
        result = min(result, cap)
    return max(1, result)


def row_height_px(
    px,
    base_height,
    base_font,
    role="body",
    vertical_padding=12,
    value=None,
):
    """Return a row height that cannot be smaller than its scaled text."""
    original = max(1, int(px(base_height)))
    required = font_px(
        px,
        base_font,
        role=role,
        value=value,
    ) + max(0, int(px(vertical_padding)))
    return max(original, required)


def visible_count(base_count, value=None):
    """Reduce free-form text rows as the selected font grows."""
    try:
        base_count = max(1, int(base_count))
    except (TypeError, ValueError, OverflowError):
        base_count = 1
    percent = text_scale_percent(value, role="body")
    return max(1, int(math.floor(float(base_count) * 100.0 / percent)))


def _bounded_cache_get(cache, key):
    try:
        value = cache.pop(key)
    except KeyError:
        return None
    cache[key] = value
    return value


def _bounded_cache_put(cache, key, value, limit):
    cache[key] = value
    cache.move_to_end(key)
    while len(cache) > int(limit):
        cache.popitem(last=False)


def clear_dynamic_text_cache():
    """Forget cached measurements, mainly for tests and skin reloads."""
    _DYNAMIC_TEXT_CACHE.clear()
    _DYNAMIC_TEXT_MEASURE_CACHE.clear()


def clean_dynamic_text(value):
    """Return bounded, single-paragraph provider/user text.

    Server supplied names occasionally contain line breaks, tabs or very large
    payloads.  Normalising those values before measuring keeps every fitting
    operation bounded and prevents a provider from turning list navigation
    into expensive work.
    """
    text = "" if value is None else str(value)
    clipped = len(text) > _DYNAMIC_TEXT_MAX_CHARS
    if clipped:
        text = text[:_DYNAMIC_TEXT_MAX_CHARS]
        boundaries = _grapheme_boundaries(text)
        # The source continues beyond this bounded slice, so conservatively
        # remove its last cluster.  It may be only the visible prefix of a ZWJ,
        # combining, flag or Indic cluster which crosses the work limit.
        text = text[: boundaries[-2]] if len(boundaries) > 1 else ""
    # Collapse a control-character gap (including its surrounding whitespace)
    # to one safe separator, while preserving intentional ordinary spacing.
    # Category rows use two spaces after their numeric prefix for alignment.
    cleaned = []
    control_gap = False
    for character in text:
        if character in _BIDI_FORMAT_CONTROLS:
            continue
        if unicodedata.category(character) in ("Cc", "Zl", "Zp"):
            while cleaned and cleaned[-1].isspace():
                cleaned.pop()
            control_gap = bool(cleaned)
            continue
        if control_gap:
            if character.isspace():
                continue
            cleaned.append(" ")
            control_gap = False
        cleaned.append(character)
    text = "".join(cleaned).strip()
    if clipped:
        text = text.rstrip("…. ") + _ELLIPSIS if text else _ELLIPSIS
    return text


def _skin_attribute(component, name):
    for key, value in getattr(component, "skinAttributes", ()) or ():
        if str(key or "").lower() == str(name or "").lower():
            return str(value or "")
    return ""


def _skin_font(component):
    value = _skin_attribute(component, "font")
    if ";" not in value:
        return "Regular", 0
    family, size = value.rsplit(";", 1)
    try:
        size = max(1, int(size))
    except (TypeError, ValueError, OverflowError):
        size = 0
    return family or "Regular", size


def _skin_size(component):
    value = _skin_attribute(component, "size")
    if "," not in value:
        return 0, 0
    width, height = value.split(",", 1)
    try:
        return max(0, int(width)), max(0, int(height))
    except (TypeError, ValueError, OverflowError):
        return 0, 0


def _component_geometry(component):
    instance = getattr(component, "instance", None)
    if instance is not None:
        try:
            size = instance.size()
            width = max(0, int(size.width()))
            height = max(0, int(size.height()))
            if width > 0:
                return instance, width, height
        except Exception:
            pass
    width, height = _skin_size(component)
    return instance, width, height


def set_text_if_changed(component, value):
    """Update a component only when its visible text actually changed.

    Enigma2's VariableText forwards every setText call to the native eLabel,
    even for an identical value.  Avoiding that redundant native repaint is
    especially important for transparent cinematic layers.
    """
    text = "" if value is None else str(value)
    try:
        getter = getattr(component, "getText", None)
        if callable(getter):
            current = getter()
        else:
            current = getattr(component, "message", None)
        if current is not None and str(current) == text:
            return False
        component.setText(text)
        return True
    except Exception:
        return None


def _safe_set_text(component, value):
    return set_text_if_changed(component, value) is not None


def _apply_font(component, family, size):
    if gFont is None or not size:
        return False
    instance = getattr(component, "instance", None)
    if instance is None:
        return False
    font_key = (id(instance), str(family or "Regular"), int(size))
    if getattr(component, "_gt_typography_font_key", None) == font_key:
        return True
    try:
        instance.setFont(gFont(font_key[1], font_key[2]))
        component._gt_typography_font_key = font_key
        return True
    except Exception:
        return False


def _text_units(value):
    """Cheap width estimate used only when Enigma2 cannot measure a label."""
    units = 0.0
    for character in ("" if value is None else str(value)):
        if (
            unicodedata.combining(character)
            or unicodedata.category(character) == "Cf"
        ):
            continue
        east_asian = unicodedata.east_asian_width(character)
        if east_asian in ("W", "F"):
            units += 2.0
        elif character in "MW@#%&":
            units += 1.35
        elif character in "ilI1|!.,:;'`":
            units += 0.55
        else:
            units += 1.0
    return units


def _fallback_capacity(width, font_size, fallback_chars):
    try:
        explicit = int(fallback_chars)
    except (TypeError, ValueError, OverflowError):
        explicit = 0
    if explicit > 0:
        return explicit
    try:
        width = int(width)
        font_size = int(font_size)
    except (TypeError, ValueError, OverflowError):
        return 40
    if width > 0 and font_size > 0:
        return max(4, int(float(width) / (float(font_size) * 0.58)))
    return 40


def _measure_text(component, value, font_size=0, nowrap=False):
    instance, unused_width, unused_height = _component_geometry(component)
    del unused_width, unused_height
    if instance is None:
        return None
    key = (
        id(instance),
        int(font_size or 0),
        bool(nowrap),
        "" if value is None else str(value),
    )
    cached = _bounded_cache_get(_DYNAMIC_TEXT_MEASURE_CACHE, key)
    if cached is not None:
        return cached
    restore_nowrap = None
    set_nowrap = getattr(instance, "setNoWrap", None)
    if callable(set_nowrap):
        configured = _skin_attribute(component, "noWrap").strip().lower()
        restore_nowrap = configured in ("1", "true", "yes", "on")
    try:
        if callable(set_nowrap):
            set_nowrap(bool(nowrap))
        if not _safe_set_text(component, value):
            return None
        size = instance.calculateSize()
        measured = max(0, int(size.width())), max(0, int(size.height()))
        if measured[0] <= 0:
            return None
    except Exception:
        return None
    finally:
        if callable(set_nowrap) and restore_nowrap is not None:
            try:
                set_nowrap(restore_nowrap)
            except Exception:
                pass
    _bounded_cache_put(
        _DYNAMIC_TEXT_MEASURE_CACHE,
        key,
        measured,
        _DYNAMIC_TEXT_MEASURE_CACHE_LIMIT,
    )
    return measured


def _line_fits(component, value, width, font_size, fallback_chars):
    measured = _measure_text(
        component,
        value,
        font_size=font_size,
        nowrap=True,
    )
    if measured is not None and width > 0:
        return measured[0] <= width
    capacity = _fallback_capacity(width, font_size, fallback_chars)
    return _text_units(value) <= float(capacity)


def _is_variation_selector(character):
    codepoint = ord(character)
    return (
        0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
    )


def _is_emoji_modifier(character):
    return 0x1F3FB <= ord(character) <= 0x1F3FF


def _is_regional_indicator(character):
    return 0x1F1E6 <= ord(character) <= 0x1F1FF


def _is_emoji_tag(character):
    return 0xE0020 <= ord(character) <= 0xE007F


def _is_grapheme_extend(character):
    return bool(
        unicodedata.category(character) in ("Mn", "Mc", "Me")
        or character == "\u200c"
        or _is_variation_selector(character)
        or _is_emoji_modifier(character)
        or _is_emoji_tag(character)
    )


def _is_virama(character):
    # Canonical combining class 9 is VIRAMA for the Indic scripts covered by
    # Enigma2 fonts.  The name fallback handles a few Unicode versions where
    # a script-specific killer is exposed without that combining class.
    if unicodedata.combining(character) == 9:
        return True
    try:
        return "VIRAMA" in unicodedata.name(character)
    except ValueError:
        return False


def _grapheme_boundaries(value):
    """Return conservative stdlib-only extended-grapheme end offsets."""
    value = "" if value is None else str(value)
    boundaries = [0]
    length = len(value)
    index = 0
    while index < length:
        first = value[index]
        index += 1
        if (
            _is_regional_indicator(first)
            and index < length
            and _is_regional_indicator(value[index])
        ):
            index += 1
        join_next = False
        while index < length:
            character = value[index]
            if character == "\u200d":
                index += 1
                if index < length:
                    index += 1
                join_next = False
                continue
            if _is_grapheme_extend(character):
                join_next = join_next or _is_virama(character)
                index += 1
                continue
            if join_next:
                index += 1
                join_next = False
                continue
            break
        boundaries.append(index)
    return boundaries


def _prefix_that_fits(
    component,
    value,
    width,
    font_size,
    fallback_chars,
    suffix="",
):
    value = "" if value is None else str(value)
    boundaries = _grapheme_boundaries(value)
    low = 0
    high = len(boundaries) - 1
    while low < high:
        middle = (low + high + 1) // 2
        candidate = value[: boundaries[middle]].rstrip() + suffix
        if _line_fits(
            component,
            candidate,
            width,
            font_size,
            fallback_chars,
        ):
            low = middle
        else:
            high = middle - 1
    return boundaries[low]


def _natural_prefix(value, length):
    value = "" if value is None else str(value)
    prefix = value[: max(0, int(length))].rstrip()
    if not prefix or len(prefix) >= len(value):
        return prefix
    boundary = prefix.rfind(" ")
    # Prefer a word boundary, but never throw away most of a useful line.
    if boundary >= max(1, int(len(prefix) * 0.72)):
        prefix = prefix[:boundary].rstrip()
    return prefix


def _ellipsize_line(component, value, width, font_size, fallback_chars):
    value = "" if value is None else str(value)
    if not value or _line_fits(
        component,
        value,
        width,
        font_size,
        fallback_chars,
    ):
        return value
    length = _prefix_that_fits(
        component,
        value,
        width,
        font_size,
        fallback_chars,
        suffix=_ELLIPSIS,
    )
    if length <= 0:
        return _ELLIPSIS if _line_fits(
            component,
            _ELLIPSIS,
            width,
            font_size,
            fallback_chars,
        ) else ""
    prefix = _natural_prefix(value, length)
    if not prefix:
        prefix = value[:length].rstrip()
    return prefix.rstrip("…. ") + _ELLIPSIS


def _wrap_text(
    component,
    value,
    width,
    max_lines,
    font_size,
    fallback_chars,
    ellipsize_last,
):
    remaining = ("" if value is None else str(value)).rstrip()
    if not remaining:
        return "", True
    lines = []
    for line_number in range(max(1, int(max_lines))):
        if _line_fits(
            component,
            remaining,
            width,
            font_size,
            fallback_chars,
        ):
            lines.append(remaining)
            remaining = ""
            break
        if line_number == max_lines - 1:
            if ellipsize_last:
                lines.append(
                    _ellipsize_line(
                        component,
                        remaining,
                        width,
                        font_size,
                        fallback_chars,
                    )
                )
            break
        length = _prefix_that_fits(
            component,
            remaining,
            width,
            font_size,
            fallback_chars,
        )
        if length <= 0:
            break
        prefix = _natural_prefix(remaining, length)
        if not prefix:
            prefix = remaining[:length].rstrip()
        lines.append(prefix)
        remaining = remaining[len(prefix):].lstrip()
    return "\n".join(lines), not bool(remaining)


def _block_fits(component, value, height, font_size, line_count):
    measured = _measure_text(
        component,
        value,
        font_size=font_size,
        nowrap=False,
    )
    if measured is not None and height > 0 and measured[1] > 0:
        return measured[1] <= height
    if height <= 0 or font_size <= 0:
        return True
    return int(math.ceil(float(font_size) * 1.18 * line_count)) <= height


def fit_dynamic_text(
    component,
    value,
    max_lines=1,
    preferred_size=None,
    min_size=None,
    font_name=None,
    fallback_chars=None,
    prefix="",
):
    """Fit server/user text into one visible Label without animation.

    Enigma2's own ``calculateSize`` provides the primary pixel measurement.
    A conservative proportional-character estimate is used before layout or
    on minimal images that do not expose the measurement API.  Supplying both
    ``preferred_size`` and ``min_size`` enables bounded, per-title font
    reduction; lists omit those arguments so every row keeps one uniform font.

    The function touches only the supplied component, runs synchronously for
    the currently visible value, uses no timer, and returns the rendered text.
    """
    # ``prefix`` is reserved for small trusted UI adornments such as a list
    # selection marker and row number.  Provider text is still normalised on
    # its own, so intentional leading padding in the prefix is not stripped.
    trusted_prefix = "" if prefix is None else str(prefix)[:64]
    text = trusted_prefix + clean_dynamic_text(value)
    try:
        max_lines = max(1, min(24, int(max_lines)))
    except (TypeError, ValueError, OverflowError):
        max_lines = 1
    instance, width, height = _component_geometry(component)
    # Several legacy compatibility labels are intentionally parked at 1x1.
    # Keep their value available to external skins, but do not spend any
    # measurement work on a widget that is not visible in the active skin.
    if width == 1 and height == 1:
        _safe_set_text(component, text)
        return text
    skin_family, skin_font_size = _skin_font(component)
    family = str(font_name or skin_family or "Regular")
    try:
        preferred = int(preferred_size) if preferred_size is not None else 0
    except (TypeError, ValueError, OverflowError):
        preferred = 0
    try:
        minimum = int(min_size) if min_size is not None else 0
    except (TypeError, ValueError, OverflowError):
        minimum = 0
    if preferred <= 0:
        preferred = skin_font_size
    adaptive = bool(
        preferred_size is not None
        and min_size is not None
        and preferred > 0
        and minimum > 0
    )
    if adaptive:
        minimum = min(preferred, max(1, minimum))
    else:
        minimum = preferred

    identity = id(instance) if instance is not None else id(component)
    try:
        fallback_key = int(fallback_chars) if fallback_chars is not None else 0
    except (TypeError, ValueError, OverflowError):
        fallback_key = 0
    cache_key = (
        identity,
        text,
        width,
        height,
        max_lines,
        preferred,
        minimum,
        family,
        fallback_key,
    )
    cached = _bounded_cache_get(_DYNAMIC_TEXT_CACHE, cache_key)
    if cached is not None:
        rendered, chosen_size = cached
        if chosen_size:
            _apply_font(component, family, chosen_size)
        _safe_set_text(component, rendered)
        return rendered

    chosen_size = preferred
    sizes = [preferred]
    if adaptive and _apply_font(component, family, preferred):
        sizes = list(range(preferred, minimum - 1, -1))
    elif adaptive:
        # Without a GUI instance we cannot safely alter the configured font.
        sizes = [preferred]

    rendered = text
    for size in sizes:
        if adaptive:
            _apply_font(component, family, size)
        candidate, complete = _wrap_text(
            component,
            text,
            width,
            max_lines,
            size,
            fallback_chars,
            ellipsize_last=False,
        )
        line_count = max(1, candidate.count("\n") + 1)
        if complete and _block_fits(
            component,
            candidate,
            height,
            size,
            line_count,
        ):
            rendered = candidate
            chosen_size = size
            break
    else:
        chosen_size = minimum if adaptive else preferred
        if adaptive:
            _apply_font(component, family, chosen_size)
        # Width may require every allowed line while the actual widget is too
        # short for that many rows at the minimum permitted font.  Reduce only
        # the line budget (never the configured minimum font) until the final
        # ellipsized block fits vertically.
        for line_limit in range(max_lines, 0, -1):
            candidate, unused_complete = _wrap_text(
                component,
                text,
                width,
                line_limit,
                chosen_size,
                fallback_chars,
                ellipsize_last=True,
            )
            del unused_complete
            rendered = candidate
            line_count = max(1, rendered.count("\n") + 1)
            if _block_fits(
                component,
                rendered,
                height,
                chosen_size,
                line_count,
            ):
                break

    if chosen_size:
        _apply_font(component, family, chosen_size)
    _safe_set_text(component, rendered)
    _bounded_cache_put(
        _DYNAMIC_TEXT_CACHE,
        cache_key,
        (rendered, chosen_size),
        _DYNAMIC_TEXT_CACHE_LIMIT,
    )
    return rendered


def ellipsize_dynamic_text(
    component,
    value,
    fallback_chars=None,
    prefix="",
):
    """Keep a dynamic single-line value at the skin's uniform font size."""
    return fit_dynamic_text(
        component,
        value,
        max_lines=1,
        fallback_chars=fallback_chars,
        prefix=prefix,
    )


def set_static_text(component, value, preferred_size=None, font_name=None):
    """Set trusted interface text after restoring its preferred font.

    Unlike the dynamic fitting helpers this deliberately performs no cleaning,
    measuring or truncation.  It is used when a provider title previously
    reduced a shared widget's font and a translated placeholder later reuses
    that widget.
    """
    family, skin_size = _skin_font(component)
    try:
        size = (
            int(preferred_size)
            if preferred_size is not None
            else int(skin_size or 0)
        )
    except (TypeError, ValueError, OverflowError):
        size = 0
    if size > 0:
        _apply_font(component, font_name or family or "Regular", size)
    text = "" if value is None else str(value)
    _safe_set_text(component, text)
    return text


def scale_skin_fonts(skin, value=None):
    """Enlarge fonts in generated skin XML without changing its geometry.

    The widget itself remains the hard clipping boundary.  Centered one-line
    labels are explicitly marked ``noWrap`` when enlarged, while known text
    areas retain their intentional multiline behaviour.
    """
    profile = normalize_text_size(
        current_text_size() if value is None else value
    )
    if profile == TEXT_SIZE_STANDARD:
        return skin

    def scale_widget(match):
        widget = match.group(0)
        font_match = _FONT_RE.search(widget)
        if font_match is None:
            return widget
        original = int(font_match.group(2))
        if original <= 1:
            return widget
        name_match = _NAME_RE.search(widget)
        name = name_match.group(1) if name_match is not None else ""
        role = _role_for_name(name)
        target = _scaled_number(
            original,
            text_scale_percent(profile, role=role),
        )
        size_match = _SIZE_RE.search(widget)
        if size_match is not None:
            height = int(size_match.group(2))
            target = min(target, max(original, height - 2))
        if target == original:
            return widget
        widget = _FONT_RE.sub(
            lambda current: "{}{}{}".format(
                current.group(1),
                target,
                current.group(3),
            ),
            widget,
            count=1,
        )
        lowered = name.lower()
        if (
            'valign="center"' in widget
            and "nowrap=" not in widget.lower()
            and not any(token in lowered for token in _MULTILINE_TOKENS)
        ):
            widget = widget[:-2].rstrip() + ' noWrap="1" />'
        return widget

    return _WIDGET_RE.sub(scale_widget, skin)


# Compatibility aliases for callers/tests that use font terminology.
normalize_font_size = normalize_text_size
current_font_size = current_text_size


def font_factor(value=None):
    return text_scale_percent(value) / 100.0

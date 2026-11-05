# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Word-preserving two-line text for the welcome screen."""
from functools import wraps
from .typography import (
    _apply_font, _component_geometry, _measure_text, _skin_font,
    _text_units, clean_dynamic_text, fit_dynamic_text, _safe_set_text,
    _bounded_cache_get, _bounded_cache_put, _DYNAMIC_TEXT_CACHE,
    _DYNAMIC_TEXT_CACHE_LIMIT,
    _fit_cache_identity, _fit_request, _current_fit, _remember_fit,
)


# These catalogues have no spaces at the natural phrase boundary. Keep their
# approved translation intact, inserting only a line break between phrases.
_TITLE_PHRASES = {
    '准备好观看了吗？': ('准备好', '观看了吗？'),
    '準備好觀看了嗎？': ('準備好', '觀看了嗎？'),
    'พร้อมรับชมหรือยัง?': ('พร้อมรับชม', 'หรือยัง?'),
}


def _remember_caption(method):
    @wraps(method)
    def fitted(component, value, preferred_size, min_size):
        value = '' if value is None else str(value)
        geometry = _component_geometry(component)
        skin_font = _skin_font(component)
        request = _fit_request(geometry, skin_font, value, (method.__name__, preferred_size, min_size))
        rendered = _current_fit(component, request)
        if rendered is not None:
            return rendered
        text = clean_dynamic_text(value)
        if not text:
            if _safe_set_text(component, ''):
                _remember_fit(component, request, '')
            return ''
        instance, width, height = geometry
        family, skin_size = skin_font
        key = (method.__name__, _fit_cache_identity(component, instance),
               text, width, height, family, skin_size, int(preferred_size), int(min_size))
        cached = _bounded_cache_get(_DYNAMIC_TEXT_CACHE, key)
        if cached is not None:
            rendered, size = cached
            font_ready = not size or _apply_font(component, family, size)
            if _safe_set_text(component, rendered) and font_ready:
                _remember_fit(component, request, rendered)
            return rendered
        rendered = method(component, text, preferred_size, min_size)
        font = getattr(component, '_gt_typography_font_key', None)
        size = font[2] if font and instance is not None and font[0] == id(instance) else 0
        _bounded_cache_put(_DYNAMIC_TEXT_CACHE, key, (rendered, size), _DYNAMIC_TEXT_CACHE_LIMIT)
        if size:
            _remember_fit(component, request, rendered)
        return rendered
    return fitted


@_remember_caption
def fit_welcome_title(component, value, preferred_size, min_size):
    """Show the whole localized heading on two lines, never splitting a word.

    Unlike provider titles, this short interface heading must not be truncated
    or cut in the middle of a word to fill a row. Prefer balanced, explicit
    phrase/word breaks and reduce the font only when the full block needs it.
    The skin disables automatic wrapping, so even a minimal image without
    native measurements cannot reintroduce an in-word break.
    """
    text = ' '.join(clean_dynamic_text(value).split())
    words = text.split(' ')
    if text in _TITLE_PHRASES:
        choices = [_TITLE_PHRASES[text]]
    else:
        choices = [(' '.join(words[:i]), ' '.join(words[i:]))
                   for i in range(1, len(words))]
    if not choices:
        choices = [(text,)]

    instance, width, height = _component_geometry(component)
    family, skin_size = _skin_font(component)
    preferred = max(1, int(preferred_size))
    minimum = min(preferred, max(1, int(min_size)))
    can_resize = _apply_font(component, family, preferred)
    sizes = range(preferred, minimum - 1, -1) if can_resize else [skin_size or preferred]
    rendered = text
    for size in sizes:
        if can_resize:
            _apply_font(component, family, size)
        ranked = []
        for lines in choices:
            measurements = [_measure_text(component, line, size, nowrap=True)
                            for line in lines]
            widths = [item[0] if item else _text_units(line) * size * 0.58
                      for line, item in zip(lines, measurements)]
            block = _measure_text(component, '\n'.join(lines), size, nowrap=True)
            block_height = block[1] if block else size * 1.18 * len(lines)
            fits = (width <= 0 or max(widths) <= width) and (
                height <= 0 or block_height <= height)
            ranked.append((not fits, max(widths), abs(widths[0] - widths[-1]), lines))
        best = min(ranked)
        rendered = '\n'.join(best[-1])
        if not best[0]:
            break
    component.setText(rendered)
    return rendered


@_remember_caption
def fit_action_caption(component, value, preferred_size, min_size):
    text = clean_dynamic_text(value)
    instance, width, height = _component_geometry(component)
    family, unused_size = _skin_font(component)
    if instance is not None and width > 0 and height > 0:
        # Deliberate breaks at words keep VERBINDEN/HINZUFÜGEN intact. The
        # generic provider-title fitter may split a word to fill a long row.
        splits = [(text,)] + [(text[:i].rstrip(), text[i+1:].lstrip())
                              for i, char in enumerate(text) if char == ' '
                              and text[:i].strip() and text[i+1:].strip()]
        for size in range(int(preferred_size), int(min_size)-1, -1):
            if not _apply_font(component, family, size):
                break
            fitting = []
            for lines in splits:
                measurements = [_measure_text(component, line, size, nowrap=True) for line in lines]
                if any(item is None for item in measurements):
                    continue
                if max(item[0] for item in measurements) > width:
                    continue
                block = _measure_text(component, '\n'.join(lines), size, nowrap=False)
                if block is None or block[1] > height:
                    continue
                fitting.append((len(lines), max(item[0] for item in measurements), lines))
            if fitting:
                rendered = '\n'.join(min(fitting)[2])
                component.setText(rendered)
                return rendered
    # Before layout or on minimal images retain the existing bounded fitter.
    return fit_dynamic_text(component, text, max_lines=2,
                            preferred_size=preferred_size, min_size=min_size)

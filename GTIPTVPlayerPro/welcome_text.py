# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Word-preserving two-line captions for the welcome action cards only."""
from .typography import (
    _apply_font, _component_geometry, _measure_text, _skin_font,
    clean_dynamic_text, fit_dynamic_text,
)


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

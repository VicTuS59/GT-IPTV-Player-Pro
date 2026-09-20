# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Canonical Enigma2 paths used by the plugin.

Enigma2 images may install plugins below a different library prefix. Resolve
the installed directory through ``Tools.Directories`` when it is available.
"""

import os

try:
    from Tools.Directories import SCOPE_PLUGINS, resolveFilename
except ImportError:  # Allow source-tree maintenance without Enigma2 modules.
    SCOPE_PLUGINS = None
    resolveFilename = None


PLUGIN_RELATIVE_PATH = "Extensions/GTIPTVPlayerPro"
_SOURCE_PLUGIN_PATH = os.path.dirname(os.path.realpath(__file__))


def plugin_path(*parts):
    """Return an absolute path inside the installed plugin directory."""
    base = ""
    if resolveFilename is not None and SCOPE_PLUGINS is not None:
        try:
            base = resolveFilename(
                SCOPE_PLUGINS,
                PLUGIN_RELATIVE_PATH + "/",
            )
        except (AttributeError, TypeError, ValueError):
            base = ""
    if not base:
        base = _SOURCE_PLUGIN_PATH
    return os.path.join(base, *parts) if parts else base

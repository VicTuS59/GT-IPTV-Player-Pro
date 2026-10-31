# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Device-language labels for the independent scores screen."""
from .i18n import _

CONTEXT = "Football scores"


def tr(message):
    key = CONTEXT + "\x04" + message
    result = _(key)
    return _(message) if result == key else result

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Use the native action map with the fields its dispatcher expects."""

from Components.ActionMap import ActionMap as NativeActionMap

from . import PLUGIN_NAME


class ActionMap(NativeActionMap):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Some OpenDroid builds log parentScreen before invoking any action.
        if not hasattr(self, "parentScreen"):
            self.parentScreen = PLUGIN_NAME

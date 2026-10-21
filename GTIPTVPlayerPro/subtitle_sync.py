# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Scoped subtitle timing keys and a reusable, non-modal timing notice."""

import time

from .i18n import _
from .typography import fit_dynamic_text, font_px

try:
    from Components.Label import Label
    from Screens.Screen import Screen
    from enigma import eTimer, getDesktop
except ImportError:
    Label = None
    Screen = object
    eTimer = None
    getDesktop = None


SYNC_LIMIT_MS = 60 * 1000
SYNC_STEP_MS = 1000
SYNC_REPEAT_SECONDS = 0.12
SYNC_NOTICE_MS = 2000
SYNC_NOTICE_Z = 220


def subtitle_sync_offset(current_ms, delta_ms=0, reset=False):
    if reset:
        return 0
    value = int(current_ms) + int(delta_ms)
    return max(-SYNC_LIMIT_MS, min(SYNC_LIMIT_MS, value))


class SubtitleSyncKeys(object):
    """Own make/repeat/break once; native repeats cannot leave a timer running."""

    def __init__(self, context, adjust, finished=None, clock=None):
        self._context_provider = context
        self._adjust = adjust
        self._finished = finished
        self._clock = clock or time.monotonic
        self._closed = False
        self._pressed = None
        self._context = None
        self._last_at = None
        self._dirty = False

    def cancel(self):
        context, dirty = self._context, self._dirty
        self._pressed = self._context = self._last_at = None
        self._dirty = False
        if dirty and callable(self._finished):
            self._finished(context)

    def close(self):
        self.cancel()
        self._closed = True

    def handle(self, command, flag):
        context = None if self._closed else self._context_provider()
        if context is None or command not in ("minus", "reset", "plus"):
            self.cancel()
            return 0
        if flag in (1, 5):
            if self._pressed == command:
                self.cancel()
            return 1
        if flag == 0:
            self.cancel()
            self._pressed, self._context = command, context
        elif flag in (2, 3):
            if self._pressed != command or self._context != context:
                self.cancel()
                return 1
            # Flag 3 is a long-key marker, not another timing step. Some
            # images publish it immediately beside the ordinary repeat.
            if flag == 3 or command == "reset":
                return 1
            if self._last_at is not None and self._clock() - self._last_at < SYNC_REPEAT_SECONDS:
                return 1
        else:
            return 0
        delta = -SYNC_STEP_MS if command == "minus" else SYNC_STEP_MS
        if self._adjust(delta, command == "reset"):
            self._dirty = True
        self._last_at = self._clock()
        return 1


def subtitle_sync_skin(width, height):
    factor = min(width / 1920.0, height / 1080.0)

    def px(value):
        return max(1, int(round(value * factor)))

    box_w = min(width - 2 * px(40), px(1100))
    box_h = px(100)
    return (
        '<screen name="GTSubtitleSyncOverlay" position="{},{}" size="{},{}" '
        'flags="wfNoBorder" backgroundColor="#20081729" zPosition="{}">'
        '<widget name="offset" position="{},{}" size="{},{}" '
        'font="Regular;{}" foregroundColor="#FFFFFF" transparent="1" '
        'halign="center" valign="center" noWrap="1" />'
        '<widget name="keys" position="{},{}" size="{},{}" '
        'font="Regular;{}" foregroundColor="#67E8F9" transparent="1" '
        'halign="center" valign="center" noWrap="1" />'
        '</screen>'
    ).format(
        (width - box_w) // 2, px(64), box_w, box_h, SYNC_NOTICE_Z,
        px(18), px(8), box_w - px(36), px(50), font_px(px, 30, role="body"),
        px(18), px(60), box_w - px(36), px(30), font_px(px, 21, role="body"),
    )


class GTSubtitleSyncOverlay(Screen):
    def __init__(self, session):
        if Label is None or eTimer is None:
            raise RuntimeError("Enigma2 timing notice widgets are unavailable")
        width, height = 1920, 1080
        if getDesktop is not None:
            size = getDesktop(0).size()
            width, height = size.width(), size.height()
        self.skin = subtitle_sync_skin(width, height)
        Screen.__init__(self, session)
        factor = min(width / 1920.0, height / 1080.0)
        px = lambda value: max(1, int(round(value * factor)))
        self._font_size = font_px(px, 30, role="body")
        self._min_font_size = font_px(px, 18, value="standard")
        self["offset"] = Label("")
        self["keys"] = Label("4 −     5 → 0     6 +")
        self._timer = eTimer()
        signal = getattr(self._timer, "timeout", None)
        connect = getattr(signal, "connect", None)
        self._connection = connect(self.dismiss) if callable(connect) else None
        if not callable(connect):
            self._timer.callback.append(self.dismiss)
        self.onLayoutFinish.append(self._raise_window)
        self.onClose.append(self.dismiss)

    def _raise_window(self):
        setter = getattr(getattr(self, "instance", None), "setZPosition", None)
        if callable(setter):
            setter(SYNC_NOTICE_Z)

    def show_offset(self, offset_ms):
        # Translate the format string before inserting its changing number.
        text = _("Subtitle timing offset: {:+.1f} seconds").format(offset_ms / 1000.0)
        fit_dynamic_text(self["offset"], text, max_lines=1,
                         preferred_size=self._font_size, min_size=self._min_font_size,
                         fallback_chars=60)
        self.show()
        self._raise_window()
        self._timer.start(SYNC_NOTICE_MS, True)

    def dismiss(self):
        self._timer.stop()
        self.hide()

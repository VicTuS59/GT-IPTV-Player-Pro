# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Plugin-scoped MENU hold with deferred, image-native short presses."""

import time
from collections import deque

HOLD_SECONDS = 3.0
_PLUGIN_MODULE = __package__
_SHORTCUT = None


def _dialogs(session):
    current = getattr(session, "current_dialog", None)
    if current is not None:
        yield current
    for entry in reversed(getattr(session, "dialog_stack", ()) or ()):
        dialog = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
        if dialog is not None:
            yield dialog


def plugin_owns_focus(session):
    if not getattr(session, "in_exec", True):
        return False
    for dialog in _dialogs(session):
        module = type(dialog).__module__
        # Power/system screens must keep their native MENU even if the plugin
        # was visible when they were opened by a global image action.
        if module == "Screens.Standby" or module.startswith("Screens.Standby."):
            return False
        if module == _PLUGIN_MODULE or module.startswith(_PLUGIN_MODULE + "."):
            return True
    return False


def _connect_timer(timer, callback):
    signal = getattr(timer, "timeout", None)
    connect = getattr(signal, "connect", None)
    if callable(connect):
        return connect(callback)
    timer.callback.append(callback)
    return None


class WebMenuShortcut(object):
    def __init__(self, session, action_map=None, timer_factory=None,
                 clock=None, opener=None, menu_key=None):
        if action_map is None or timer_factory is None:
            from enigma import eActionMap, eTimer
            action_map = action_map or eActionMap.getInstance()
            timer_factory = timer_factory or eTimer
        if menu_key is None:
            from keyids import KEYIDS
            menu_key = KEYIDS["KEY_MENU"]
        if opener is None:
            from .web_ui import open_web_interface
            opener = open_web_interface
        self.session = session
        self._action_map = action_map
        self._menu_key = menu_key
        self._clock = clock or time.monotonic
        self._opener = opener
        self._hold_timer = timer_factory()
        self._short_timer = timer_factory()
        self._connections = (
            _connect_timer(self._hold_timer, self._hold_expired),
            _connect_timer(self._short_timer, self._dispatch_short),
        )
        self._press_dialog = None
        self._pressed_at = None
        self._fired = False
        self._cancelled = False
        self._replaying = False
        self._priming = False
        self._pending_short = deque()
        self._closed = False
        self._focus_callbacks = None
        self._focus_callback = self._focus_left
        # Keep the same bound-method identity for native bind/unbind.
        self._key_callback = self._handle_key
        self._action_map.bindAction("", -1000, self._key_callback)

    def _focus_left(self):
        if self._pressed_at is not None and not self._fired:
            self._cancelled = True
            self._hold_timer.stop()

    def _detach_focus_callback(self):
        if self._focus_callbacks is not None:
            if self._focus_callback in self._focus_callbacks:
                self._focus_callbacks.remove(self._focus_callback)
            self._focus_callbacks = None

    def _still_focused(self):
        return (self._press_dialog is not None
                and getattr(self.session, "current_dialog", None) is self._press_dialog
                and plugin_owns_focus(self.session))

    def _open_long(self):
        if self._fired or self._cancelled:
            return
        if not self._still_focused():
            self._cancelled = True
            return
        # Latch before session.open: the focus changes while MENU remains held.
        self._fired = True
        try:
            self._opener(self.session)
        except Exception as error:
            from .diagnostics import log_event
            log_event("web_shortcut", "Could not open the web access screen", error)

    def _hold_expired(self):
        if self._closed or self._pressed_at is None or self._fired or self._cancelled:
            return
        if not self._still_focused():
            self._cancelled = True
            return
        remaining = HOLD_SECONDS - (self._clock() - self._pressed_at)
        if remaining > 0:
            self._hold_timer.start(max(1, int(remaining * 1000 + 0.999)), True)
        else:
            self._open_long()

    def _handle_key(self, key, flag):
        if self._closed or self._replaying:
            return 0
        if key != self._menu_key:
            if flag == 0 and self._pressed_at is not None:
                # Native wildcard bookkeeping keeps only the most recent make
                # key, so another key's make would hide MENU's eventual break.
                self._hold_timer.stop()
                self._detach_focus_callback()
                self._press_dialog = None
                self._pressed_at = None
                self._fired = False
                self._cancelled = False
            return 0
        if self._priming:
            return 1
        if self._pressed_at is None:
            if flag != 0 or not plugin_owns_focus(self.session):
                return 0
            self._press_dialog = self.session.current_dialog
            self._pressed_at = self._clock()
            self._fired = False
            self._cancelled = False
            callbacks = getattr(self._press_dialog, "onExecEnd", None)
            if isinstance(callbacks, list):
                self._focus_callbacks = callbacks
                callbacks.append(self._focus_callback)
            self._hold_timer.start(int(HOLD_SECONDS * 1000), True)
            return 1
        if not self._fired and not self._still_focused():
            self._cancelled = True
            self._hold_timer.stop()
        if flag == 3:
            # PLi drops the break completely after its native long flag; ATV
            # turns it into stop. Reset that native bookkeeping with a consumed
            # make, while retaining our original three-second start time. The
            # native dispatcher has already built its local callback list, so
            # this nested make cannot invalidate its traversal or leak actions.
            self._priming = True
            try:
                self._action_map.keyPressed(
                    "dreambox remote control (native)", self._menu_key, 0
                )
            finally:
                self._priming = False
        # Accept ATV's stop form too; priming normally preserves a plain break.
        if flag in (1, 5):
            self._hold_timer.stop()
            if not self._fired and not self._cancelled:
                if self._clock() - self._pressed_at >= HOLD_SECONDS:
                    # The GUI may process key-up before the due timer callback.
                    self._open_long()
                else:
                    self._pending_short.append(self._press_dialog)
                    self._short_timer.start(0, True)
            self._detach_focus_callback()
            self._press_dialog = None
            self._pressed_at = None
            self._fired = False
            self._cancelled = False
        # Consume all repeats and image long flags, which commonly arrive well
        # before three seconds. Also consume key-up after opening another dialog.
        return 1

    def _dispatch_short(self):
        if self._closed:
            return
        while self._pending_short:
            dialog = self._pending_short.popleft()
            if (getattr(self.session, "current_dialog", None) is not dialog
                    or not plugin_owns_focus(self.session)):
                continue
            # Re-enter the image keymap only after the physical key-up returned.
            # The recursion guard lets the original ActionMaps handle this one
            # ordinary press, including the authenticated web remote's MENU.
            self._replaying = True
            try:
                device = "dreambox remote control (native)"
                self._action_map.keyPressed(device, self._menu_key, 0)
                self._action_map.keyPressed(device, self._menu_key, 1)
            finally:
                self._replaying = False

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._hold_timer.stop()
        self._short_timer.stop()
        self._pending_short.clear()
        self._detach_focus_callback()
        self._press_dialog = None
        self._pressed_at = None
        self._action_map.unbindAction("", self._key_callback)


def install(session):
    global _SHORTCUT
    if session is None:
        return
    if _SHORTCUT is not None and _SHORTCUT.session is session and not _SHORTCUT._closed:
        return _SHORTCUT
    if _SHORTCUT is not None:
        _SHORTCUT.close()
    _SHORTCUT = WebMenuShortcut(session)
    return _SHORTCUT


def uninstall():
    global _SHORTCUT
    if _SHORTCUT is not None:
        _SHORTCUT.close()
        _SHORTCUT = None

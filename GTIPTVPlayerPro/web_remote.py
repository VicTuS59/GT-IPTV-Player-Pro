# -*- coding: utf-8 -*-
"""Authenticated web remote actions on the Enigma2 GUI thread."""

import threading

from .web_api import WebServiceError

REMOTE_KEYS = {
    "up": "KEY_UP", "down": "KEY_DOWN", "left": "KEY_LEFT",
    "right": "KEY_RIGHT", "ok": "KEY_OK", "back": "KEY_EXIT",
    "menu": "KEY_MENU", "info": "KEY_INFO", "red": "KEY_RED",
    "green": "KEY_GREEN", "yellow": "KEY_YELLOW", "blue": "KEY_BLUE",
    "play": "KEY_PLAY", "pause": "KEY_PAUSE", "playpause": "KEY_PLAYPAUSE",
    "rewind": "KEY_REWIND", "forward": "KEY_FASTFORWARD",
    "volume_up": "KEY_VOLUMEUP", "volume_down": "KEY_VOLUMEDOWN",
    "mute": "KEY_MUTE", "channel_up": "KEY_CHANNELUP",
    "channel_down": "KEY_CHANNELDOWN",
}
REMOTE_KEYS.update((str(index), "KEY_{}".format(index)) for index in range(10))
MAX_PASTE_LENGTH = 2048


class WebRemote(object):
    def __init__(self):
        self.session = None
        self._gui_thread = None

    def bind_session(self, session):
        # Called from the Enigma2 session-start hook on the GUI thread.
        self.session = session
        self._gui_thread = threading.get_ident() if session is not None else None

    def _run_on_gui(self, callback):
        if self.session is None:
            raise WebServiceError("receiver_unavailable", "Receiver unavailable", 503)
        if threading.get_ident() == self._gui_thread:
            return callback()
        try:
            from twisted.internet import reactor
            if not reactor.running:
                raise RuntimeError("GUI loop is not running")
        except Exception:
            raise WebServiceError("receiver_unavailable", "Receiver unavailable", 503)
        ready = threading.Event()
        result = []
        expired = threading.Event()

        def invoke():
            try:
                if expired.is_set():
                    return
                result.append((True, callback()))
            except Exception as error:
                result.append((False, error))
            finally:
                ready.set()

        reactor.callFromThread(invoke)
        if not ready.wait(3):
            expired.set()
            raise WebServiceError("receiver_unavailable", "Receiver unavailable", 503)
        if not result[0][0]:
            raise result[0][1]
        return result[0][1]

    def _keyboard_input(self):
        dialog = getattr(self.session, "current_dialog", None)
        if dialog is None:
            return None
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            try:
                from Screens.VirtualKeyBoard import VirtualKeyboard as VirtualKeyBoard
            except ImportError:
                return None
        if not isinstance(dialog, VirtualKeyBoard):
            return None
        try:
            value = dialog["text"]
            return value if callable(getattr(value, "setText", None)) else None
        except (KeyError, TypeError):
            return None

    def keyboard_status(self):
        return self._run_on_gui(lambda: {"keyboard_open": self._keyboard_input() is not None})

    def paste(self, value):
        if not isinstance(value, str) or not value or len(value) > MAX_PASTE_LENGTH:
            raise WebServiceError("invalid_paste", "Paste text is invalid", 400)
        # Do not echo or log the contents: it may contain credentials.
        def update():
            target = self._keyboard_input()
            if target is None:
                raise WebServiceError("keyboard_closed", "Open the receiver keyboard first", 409)
            maximum = getattr(target, "maxSize", 0)
            if maximum and len(value) > maximum:
                raise WebServiceError("invalid_paste", "Paste text is too long", 400)
            target.allMarked = False
            target.setText(value)
            target.currPos = len(value)
            target.update()
            return {"pasted": True}
        return self._run_on_gui(update)

    def press(self, name):
        key = REMOTE_KEYS.get(str(name or ""))
        if key is None:
            raise WebServiceError("invalid_remote_key", "Invalid remote key", 400)

        def send():
            from enigma import eActionMap
            from keyids import KEYIDS
            code = KEYIDS[key]
            action_map = eActionMap.getInstance()
            device = "dreambox remote control (native)"
            action_map.keyPressed(device, code, 0)
            action_map.keyPressed(device, code, 1)
            return {"pressed": True}
        return self._run_on_gui(send)


WEB_REMOTE = WebRemote()


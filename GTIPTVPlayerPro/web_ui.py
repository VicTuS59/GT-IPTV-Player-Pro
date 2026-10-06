# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native Enigma2 screen that opens and approves temporary web sessions."""

from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen
from enigma import eTimer, getDesktop

try:
    from skin import parseColor
except ImportError:
    parseColor = None

from .background import attach_background
from .i18n import N_, _
from .remote_footer import (
    decorate_remote_footer,
    footer_item,
    install_remote_footer,
)
from .typography import current_text_size, fit_dynamic_text
from .web_server import get_runtime
from .web_theme import web_layout, web_skin


WEB_INTERFACE_FOOTER_ITEMS = (
    footer_item("green", N_("Open / Approve"), 1.10),
    footer_item("red", N_("Stop / Deny"), 1.00),
    footer_item("blue", N_("New code"), 0.90),
    footer_item("exit", N_("Close"), 0.75),
)


def _session_dialogs(session):
    """Yield the current modal and its parents, nearest parent first."""
    current = getattr(session, "current_dialog", None)
    if current is not None:
        yield current
    for entry in reversed(getattr(session, "dialog_stack", ()) or ()):
        dialog = entry[0] if isinstance(entry, (tuple, list)) and entry else entry
        if dialog is not None:
            yield dialog


def _is_gt_player(dialog):
    # Check the inheritance chain without importing the large browser module
    # just to open the web screen from a settings page.
    return any(
        base.__name__ == "GTExternalPlayerScreen"
        and base.__module__ == __package__ + ".browser"
        for base in type(dialog).__mro__
    )


def open_web_interface(session):
    """Open the existing approval UI while preserving its modal parent.

    The web runtime is deliberately untouched: displaying this screen must
    not renew a code, start playback, or approve a browser automatically.
    """
    dialogs = tuple(_session_dialogs(session))
    for dialog in dialogs:
        if isinstance(dialog, GTWebInterfaceScreen):
            return dialog

    player = next((dialog for dialog in dialogs if _is_gt_player(dialog)
                   and not getattr(dialog, "_closed", False)), None)
    restore = None
    if player is not None:
        controller = getattr(player, "_subtitle_controller", None)
        online = getattr(controller, "online", None)
        renderer = getattr(online, "renderer", None)
        obscurer = getattr(online, "set_ui_obscured", None)
        acquirer = getattr(online, "acquire_ui_obscured", None)
        releaser = getattr(online, "release_ui_obscured", None)
        ui_token = None
        info = getattr(player, "_info_dialog", None)
        timer = getattr(player, "_hide_timer", None)
        try:
            timer_active = bool(timer is not None and timer.isActive())
        except Exception:
            timer_active = False
        state = {
            "generation": getattr(player, "_generation", None),
            "item": getattr(player, "current_item", None),
            "info_visible": bool(getattr(player, "_info_visible", False)),
            "menu_open": bool(getattr(player, "_subtitle_menu_open", False)),
            "menu_generation": getattr(player, "_subtitle_menu_generation", None),
            "obscured": bool(getattr(renderer, "_ui_obscured", False)),
            "timer_active": timer_active,
        }
        # The banner and subtitles are independent high-z dialogs. A normal
        # modal push cannot hide them. Preserve the seek widgets themselves
        # so a paused movie or pending seek returns exactly as it was.
        player._subtitle_menu_open = True
        player._info_visible = False
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass
        if info is not None:
            try:
                info.hide()
            except Exception:
                pass
        if callable(acquirer) and callable(releaser):
            try:
                ui_token = acquirer()
            except Exception:
                pass
        if ui_token is None and callable(obscurer):
            try:
                obscurer(True)
            except Exception:
                pass

        restored = False

        def restore(*unused_result):
            nonlocal restored
            if restored:
                return
            restored = True
            if ui_token is not None:
                try:
                    releaser(ui_token)
                except Exception:
                    pass
            if getattr(player, "_closed", False):
                return
            same_menu = (getattr(player, "_subtitle_menu_generation", None)
                         == state["menu_generation"])
            # Release the gate we introduced even if playback changed while
            # the child was open; never leave a new service's banner blocked.
            if same_menu:
                player._subtitle_menu_open = state["menu_open"]
            if (getattr(player, "_subtitle_controller", None) is controller
                    and getattr(online, "renderer", None) is renderer
                    and ui_token is None and callable(obscurer) and same_menu):
                try:
                    obscurer(state["obscured"])
                except Exception:
                    pass
            if (getattr(player, "_generation", None) != state["generation"]
                    or getattr(player, "current_item", None) is not state["item"]):
                return
            if (state["info_visible"]
                    and getattr(player, "_info_dialog", None) is info
                    and not getattr(player, "_subtitle_menu_open", False)
                    and not getattr(player, "_subtitle_message_tokens", None)):
                try:
                    player._info_visible = True
                    info.show()
                    setter = getattr(player, "_set_subtitle_infobar_visible", None)
                    if callable(setter):
                        setter(True)
                    if state["timer_active"] and timer is not None:
                        from .browser import INFO_TIMEOUT_MS
                        timer.start(INFO_TIMEOUT_MS, True)
                except Exception:
                    pass

    try:
        if restore is not None:
            return session.openWithCallback(restore, GTWebInterfaceScreen)
        return session.open(GTWebInterfaceScreen)
    except Exception:
        if restore is not None:
            restore()
        raise


def _connect_timer(timer, callback):
    timeout = getattr(timer, "timeout", None)
    connector = getattr(timeout, "connect", None)
    if callable(connector):
        connector(callback)
    elif hasattr(timer, "callback"):
        timer.callback.append(callback)


def _desktop_size():
    try:
        size = getDesktop(0).size()
        width, height = int(size.width()), int(size.height())
        if width <= 0 or height <= 0:
            raise ValueError("Desktop dimensions are unavailable")
        return width, height
    except Exception:
        return 1280, 720


def _skin(layout=None):
    if layout is None:
        layout = web_layout(*_desktop_size(), font_profile=current_text_size())
    return decorate_remote_footer(
        web_skin(layout), WEB_INTERFACE_FOOTER_ITEMS, **layout["footer_options"]
    )


class GTWebInterfaceScreen(Screen):
    def __init__(self, session, runtime=None):
        self._web_layout = web_layout(*_desktop_size(), font_profile=current_text_size())
        self.skin = _skin(self._web_layout)
        Screen.__init__(self, session)
        self.runtime = runtime or get_runtime()
        self._text_values = {}
        self["header"] = Label(_("Web Interface"))
        self["brand"] = Label("GT IPTV PLAYER PRO")
        self["status"] = Label("")
        self["address_title"] = Label(_("Web URL"))
        self["address"] = Label("")
        self["code_title"] = Label(_("Access code"))
        self["code"] = Label("— —")
        self["detail"] = Label("")
        self["security"] = Label("")
        self["footer"] = Label("")
        attach_background(self, "web_background", self._web_layout["background"])
        install_remote_footer(
            self, WEB_INTERFACE_FOOTER_ITEMS,
            stacked=self._web_layout["compact"],
            design_size=self._web_layout["footer_options"]["design_size"],
        )
        self["actions"] = ActionMap(
            ["OkCancelActions", "ColorActions"],
            {
                "ok": self.green,
                "green": self.green,
                "red": self.red,
                "blue": self.blue,
                "yellow": self.yellow,
                "cancel": self.close,
                "back": self.close,
            },
            -1,
        )
        self._timer = eTimer()
        _connect_timer(self._timer, self.refresh)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._layout_ready)
        if hasattr(self, "onShown"):
            self.onShown.append(self._shown)
        if hasattr(self, "onClose"):
            self.onClose.append(self._closed)
        self.setTitle("GT IPTV Player Pro - {}".format(_("Web Interface")))
        self.refresh()

    def _layout_ready(self):
        # Native sizes/font metrics become available only after applySkin.
        self._text_values.clear()
        self.refresh()

    def _set_text(self, name, value):
        value = str(value or "")
        if self._text_values.get(name) == value:
            return
        self._text_values[name] = value
        fit_dynamic_text(
            self[name], value,
            max_lines=3 if name == "detail" else (2 if name == "address" else 1),
            preferred_size=self._web_layout["fonts"][name],
            min_size=self._web_layout["minimums"][name],
        )

    def _set_status_color(self, color):
        instance = getattr(self["status"], "instance", None)
        if instance is not None and parseColor is not None:
            try:
                instance.setForegroundColor(parseColor(color))
            except (AttributeError, TypeError, ValueError):
                pass

    def _shown(self):
        self._layout_ready()
        self._timer.start(500, False)

    def _closed(self):
        try:
            self._timer.stop()
        except Exception:
            pass

    @staticmethod
    def _formatted_code(value):
        value = str(value or "")
        return "{} {}".format(value[:3], value[3:]) if len(value) == 6 else "— —"

    def refresh(self):
        state = self.runtime.status()
        pending = state.get("pending")
        active = bool(state.get("active"))
        sessions = int(state.get("session_count") or 0)
        urls = list(state.get("urls") or ())
        self._set_text("header", _("Web Interface"))
        self._set_text("brand", "GT IPTV PLAYER PRO")
        self._set_text("address_title", _("Web URL"))
        self._set_text("code_title", _("Access code"))
        color = "#DDE8FA"
        if state.get("starting"):
            self._set_text("status", "{} • {}".format(_("Status"), _("Loading...")))
            self._set_text("detail", _("Please wait"))
            color = "#69CAFF"
        elif state.get("error"):
            self._set_text("status", "{} • {}".format(_("Status"), _("Off")))
            message = (
                _("Network address could not be resolved.")
                if state["error"] == "Network address could not be resolved."
                else _("Could not connect to the server")
            )
            self._set_text("detail", message)
            color = "#FF8497"
        elif pending:
            self._set_text("status", "{} • {}".format(_("Status"), _("Active")))
            self._set_text("detail",
                "{}\n{} • {}".format(
                    _("TV approval required"),
                    pending.get("device") or _("Browser"),
                    pending.get("client_ip") or "",
                )
            )
            color = "#F7B955"
        elif sessions:
            self._set_text("status", "{} • {}".format(_("Status"), _("Connected")))
            self._set_text("detail", "{}: {}".format(_("Connected"), sessions))
            color = "#37E6A5"
        elif active and not urls:
            self._set_text("status", "{} • {}".format(_("Status"), _("Loading...")))
            self._set_text("detail", _("Network address could not be resolved."))
            color = "#69CAFF"
        elif active:
            self._set_text("status", "{} • {}".format(_("Status"), _("Ready to connect")))
            self._set_text("detail",
                _(
                    "Enter this code in your phone browser, then approve the "
                    "request on TV."
                )
            )
            color = "#69CAFF"
        else:
            self._set_text("status", "{} • {}".format(_("Status"), _("Off")))
            self._set_text("detail",
                _("Press GREEN to start a temporary local web session.")
            )

        self._set_status_color(color)
        self._set_text("address", urls[0] if urls else "—")
        self._set_text("code", self._formatted_code(state.get("code") if urls else ""))
        if active and not state.get("encrypted"):
            self._set_text("security",
                _("LAN only • HTTP • Do not expose port 9999 to the internet")
            )
        elif active:
            self._set_text("security", _("LAN • HTTPS"))
        else:
            self._set_text("security", "")

    def green(self):
        state = self.runtime.status()
        try:
            if state.get("starting"):
                return
            if state.get("pending"):
                self.runtime.approve_pending()
            else:
                self.runtime.request_start()
        except Exception as error:
            self._set_text("detail", str(error)[:180])
        self.refresh()

    def red(self):
        state = self.runtime.status()
        try:
            if state.get("pending"):
                self.runtime.deny_pending()
            else:
                self.runtime.stop()
        except Exception as error:
            self._set_text("detail", str(error)[:180])
        self.refresh()

    def blue(self):
        try:
            self.runtime.request_start()
        except Exception as error:
            self._set_text("detail", str(error)[:180])
        self.refresh()

    def yellow(self):
        self.refresh()

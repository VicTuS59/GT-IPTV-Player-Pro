# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native Enigma2 screen that opens and approves temporary web sessions."""

from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen
from enigma import eTimer, getDesktop

from .i18n import N_, _
from .remote_footer import (
    decorate_remote_footer,
    footer_item,
    install_remote_footer,
)
from .web_server import get_runtime


WEB_INTERFACE_FOOTER_ITEMS = (
    footer_item("green", N_("Open / Approve"), 1.10),
    footer_item("red", N_("Stop / Deny"), 1.00),
    footer_item("blue", N_("New code"), 0.90),
    footer_item("exit", N_("Close"), 0.75),
)


def _connect_timer(timer, callback):
    timeout = getattr(timer, "timeout", None)
    connector = getattr(timeout, "connect", None)
    if callable(connector):
        connector(callback)
    elif hasattr(timer, "callback"):
        timer.callback.append(callback)


def _skin():
    try:
        size = getDesktop(0).size()
        width, height = int(size.width()), int(size.height())
        if width <= 0 or height <= 0:
            raise ValueError("Desktop dimensions are unavailable")
    except Exception:
        width, height = 1280, 720
    scale = min(width / 1920.0, height / 1080.0)

    def s(value):
        return max(1, int(round(value * scale)))

    panel_x, panel_y = s(210), s(150)
    panel_w = width - 2 * panel_x
    # Leave the same visual gap above the taller shared remote-control guide
    # that the former one-line footer had below the content panel.
    panel_h = height - panel_y - s(132)
    # OpenPLi renderers do not all assign the same implicit layer to eLabel.
    # Keep decorative surfaces below opaque text widgets explicitly so the
    # pairing details remain visible on both legacy and current framebuffer
    # implementations.
    skin = """
<screen name="GTWebInterfaceScreen" position="0,0" size="{width},{height}"
        flags="wfNoBorder" backgroundColor="#061224">
    <eLabel position="0,0" size="{width},{accent}" backgroundColor="#147DFF"
            zPosition="1" />
    <widget name="header" position="{header_x},{header_y}" size="{header_w},{header_h}"
            font="Regular;{header_font}" foregroundColor="#FFFFFF" backgroundColor="#061224"
            transparent="1" zPosition="2" valign="center" />
    <widget name="brand" position="{brand_x},{header_y}" size="{brand_w},{header_h}"
            font="Regular;{brand_font}" foregroundColor="#8C52FF" backgroundColor="#061224"
            transparent="1" zPosition="2" halign="right" valign="center" />
    <eLabel position="{panel_x},{panel_y}" size="{panel_w},{panel_h}"
            backgroundColor="#0A1B31" zPosition="0" />
    <widget name="status" position="{content_x},{status_y}" size="{content_w},{line_h}"
            font="Regular;{status_font}" foregroundColor="#37E6A5" backgroundColor="#0A1B31"
            transparent="0" zPosition="2" halign="center" valign="center" />
    <widget name="address_title" position="{content_x},{address_title_y}" size="{content_w},{small_h}"
            font="Regular;{small_font}" foregroundColor="#8296B5" backgroundColor="#0A1B31"
            transparent="0" zPosition="2" halign="center" valign="center" />
    <widget name="address" position="{content_x},{address_y}" size="{content_w},{line_h}"
            font="Regular;{address_font}" foregroundColor="#4CB5FF" backgroundColor="#0A1B31"
            transparent="0" zPosition="2" halign="center" valign="center" />
    <widget name="code" position="{content_x},{code_y}" size="{content_w},{code_h}"
            font="Regular;{code_font}" foregroundColor="#FFFFFF" backgroundColor="#0A1B31"
            transparent="0" zPosition="2" halign="center" valign="center" />
    <widget name="detail" position="{content_x},{detail_y}" size="{content_w},{detail_h}"
            font="Regular;{detail_font}" foregroundColor="#BCC8DA" backgroundColor="#0A1B31"
            transparent="0" zPosition="2" halign="center" valign="center" />
    <widget name="security" position="{content_x},{security_y}" size="{content_w},{small_h}"
            font="Regular;{small_font}" foregroundColor="#F7B955" backgroundColor="#0A1B31"
            transparent="0" zPosition="2" halign="center" valign="center" />
</screen>
""".format(
        width=width,
        height=height,
        accent=s(4),
        header_x=s(90),
        header_y=s(45),
        header_w=s(900),
        header_h=s(70),
        header_font=s(40),
        brand_x=width - s(720),
        brand_w=s(620),
        brand_font=s(28),
        panel_x=panel_x,
        panel_y=panel_y,
        panel_w=panel_w,
        panel_h=panel_h,
        content_x=panel_x + s(80),
        content_w=panel_w - s(160),
        status_y=panel_y + s(38),
        line_h=s(62),
        status_font=s(32),
        address_title_y=panel_y + s(125),
        small_h=s(40),
        small_font=s(23),
        address_y=panel_y + s(165),
        address_font=s(31),
        code_y=panel_y + s(255),
        code_h=s(130),
        code_font=s(78),
        detail_y=panel_y + s(405),
        detail_h=s(88),
        detail_font=s(27),
        security_y=panel_y + s(515),
    )
    return decorate_remote_footer(skin, WEB_INTERFACE_FOOTER_ITEMS)


class GTWebInterfaceScreen(Screen):
    def __init__(self, session, runtime=None):
        self.skin = _skin()
        Screen.__init__(self, session)
        self.runtime = runtime or get_runtime()
        self["header"] = Label(_("Web Interface"))
        self["brand"] = Label("GT IPTV PLAYER PRO")
        self["status"] = Label("")
        self["address_title"] = Label(_("Web URL"))
        self["address"] = Label("")
        self["code"] = Label("— — —")
        self["detail"] = Label("")
        self["security"] = Label("")
        self["footer"] = Label("")
        install_remote_footer(self, WEB_INTERFACE_FOOTER_ITEMS)
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
        if hasattr(self, "onShown"):
            self.onShown.append(self._shown)
        if hasattr(self, "onClose"):
            self.onClose.append(self._closed)
        self.setTitle("GT IPTV Player Pro - {}".format(_("Web Interface")))
        self.refresh()

    def _shown(self):
        self.refresh()
        self._timer.start(500, False)

    def _closed(self):
        try:
            self._timer.stop()
        except Exception:
            pass

    @staticmethod
    def _formatted_code(value):
        value = str(value or "")
        return "{} {}".format(value[:3], value[3:]) if len(value) == 6 else "— — —"

    def refresh(self):
        state = self.runtime.status()
        pending = state.get("pending")
        active = bool(state.get("active"))
        sessions = int(state.get("session_count") or 0)
        urls = list(state.get("urls") or ())
        if state.get("starting"):
            self["status"].setText("{} • {}".format(_("Status"), _("Loading...")))
            self["detail"].setText(_("Please wait"))
        elif state.get("error"):
            self["status"].setText("{} • {}".format(_("Status"), _("Off")))
            message = (
                _("Network address could not be resolved.")
                if state["error"] == "Network address could not be resolved."
                else _("Could not connect to the server")
            )
            self["detail"].setText(message)
        elif pending:
            self["status"].setText("{} • {}".format(_("Status"), _("Active")))
            self["detail"].setText(
                "{}\n{} • {}".format(
                    _("TV approval required"),
                    pending.get("device") or _("Browser"),
                    pending.get("client_ip") or "",
                )
            )
        elif sessions:
            self["status"].setText("{} • {}".format(_("Status"), _("Connected")))
            self["detail"].setText("{}: {}".format(_("Connected"), sessions))
        elif active and not urls:
            self["status"].setText("{} • {}".format(_("Status"), _("Loading...")))
            self["detail"].setText(_("Network address could not be resolved."))
        elif active:
            self["status"].setText("{} • {}".format(_("Status"), _("Ready to connect")))
            self["detail"].setText(
                _(
                    "Enter this code in your phone browser, then approve the "
                    "request on TV."
                )
            )
        else:
            self["status"].setText("{} • {}".format(_("Status"), _("Off")))
            self["detail"].setText(
                _("Press GREEN to start a temporary local web session.")
            )

        self["address"].setText(urls[0] if urls else "—")
        self["code"].setText(self._formatted_code(state.get("code") if urls else ""))
        if active and not state.get("encrypted"):
            self["security"].setText(
                _("LAN only • HTTP • Do not expose port 9999 to the internet")
            )
        elif active:
            self["security"].setText(_("LAN • HTTPS"))
        else:
            self["security"].setText("")

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
            self["detail"].setText(str(error)[:180])
        self.refresh()

    def red(self):
        state = self.runtime.status()
        try:
            if state.get("pending"):
                self.runtime.deny_pending()
            else:
                self.runtime.stop()
        except Exception as error:
            self["detail"].setText(str(error)[:180])
        self.refresh()

    def blue(self):
        try:
            self.runtime.request_start()
        except Exception as error:
            self["detail"].setText(str(error)[:180])
        self.refresh()

    def yellow(self):
        self.refresh()


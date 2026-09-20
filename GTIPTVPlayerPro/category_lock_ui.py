# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small remote-control code entry screen for the category edit lock."""

from Components.ActionMap import ActionMap
from Components.Label import Label
from Screens.Screen import Screen
from enigma import getDesktop

from .i18n import _
from .typography import scale_skin_fonts


RECOVERY_REQUEST = "__gt_category_lock_recovery__"


def _code_entry_skin():
    size = getDesktop(0).size()
    desktop_width = int(size.width())
    desktop_height = int(size.height())
    scale = min(
        float(desktop_width) / 1920.0,
        float(desktop_height) / 1080.0,
    )

    def px(value):
        return max(1, int(round(float(value) * scale)))

    width = min(desktop_width - px(60), px(1040))
    height = min(desktop_height - px(60), px(470))
    left = max(0, int((desktop_width - width) / 2))
    top = max(0, int((desktop_height - height) / 2))
    return """
<screen name="GTCategoryCodeInputScreen" position="{left},{top}"
        size="{width},{height}" flags="wfNoBorder"
        backgroundColor="#050914">
    <widget name="panel" position="0,0" size="{width},{height}"
            font="Regular;1" backgroundColor="#07101F"
            transparent="0" zPosition="0" />
    <widget name="accent" position="0,0" size="{width},{accent_h}"
            font="Regular;1" backgroundColor="#16C9F4"
            transparent="0" zPosition="1" />
    <widget name="title" position="{pad},{title_y}"
            size="{text_w},{title_h}" font="Regular;{title_font}"
            foregroundColor="#FFFFFF" transparent="1" zPosition="2"
            valign="center" halign="center" />
    <widget name="prompt" position="{pad},{prompt_y}"
            size="{text_w},{prompt_h}" font="Regular;{prompt_font}"
            foregroundColor="#C8D2E3" transparent="1" zPosition="2"
            valign="center" halign="center" />
    <widget name="code_bg" position="{code_x},{code_y}"
            size="{code_w},{code_h}" font="Regular;1"
            backgroundColor="#091426" transparent="0" zPosition="1" />
    <widget name="code" position="{code_x},{code_y}"
            size="{code_w},{code_h}" font="Regular;{code_font}"
            foregroundColor="#16C9F4" transparent="1" zPosition="2"
            valign="center" halign="center" />
    <widget name="message" position="{pad},{message_y}"
            size="{text_w},{message_h}" font="Regular;{message_font}"
            foregroundColor="#FFB347" transparent="1" zPosition="2"
            valign="center" halign="center" />
    <widget name="footer" position="0,{footer_y}"
            size="{width},{footer_h}" font="Regular;{footer_font}"
            foregroundColor="#C8D2E3" backgroundColor="#080E1A"
            transparent="0" zPosition="3" valign="center" halign="center" />
</screen>
""".format(
        left=left,
        top=top,
        width=width,
        height=height,
        accent_h=px(4),
        pad=px(35),
        text_w=width - px(70),
        title_y=px(28),
        title_h=px(58),
        title_font=px(31),
        prompt_y=px(92),
        prompt_h=px(64),
        prompt_font=px(22),
        code_x=px(55),
        code_y=px(170),
        code_w=width - px(110),
        code_h=px(86),
        code_font=px(34),
        message_y=px(270),
        message_h=px(54),
        message_font=px(20),
        footer_y=height - px(92),
        footer_h=px(92),
        footer_font=px(19),
    )


class GTCategoryCodeInputScreen(Screen):
    """Masked fixed-length numeric input without image-specific keyboards."""

    def __init__(
        self,
        session,
        title,
        prompt,
        digits=4,
        allow_recovery=False,
    ):
        self.skin = scale_skin_fonts(_code_entry_skin())
        Screen.__init__(self, session)
        try:
            requested_digits = int(digits)
        except (TypeError, ValueError, OverflowError):
            requested_digits = 4
        self.required_digits = 12 if requested_digits == 12 else 4
        self.allow_recovery = bool(allow_recovery)
        self._digits = ""

        self["panel"] = Label("")
        self["accent"] = Label("")
        self["title"] = Label(str(title or ""))
        self["prompt"] = Label(str(prompt or ""))
        self["code_bg"] = Label("")
        self["code"] = Label("")
        self["message"] = Label("")
        footer = _(
            "NUMBER KEYS: Enter  |  YELLOW: Delete  |  "
            "OK/GREEN: Confirm  |  BACK: Cancel"
        )
        if self.allow_recovery:
            footer += "  |  " + _("BLUE: Recovery code")
        self["footer"] = Label(footer)

        actions = {
            "ok": self.confirm,
            "green": self.confirm,
            "cancel": self.cancel,
            "back": self.cancel,
            "red": self.cancel,
            "yellow": self.delete_digit,
            "blue": self.request_recovery,
        }
        for digit in "0123456789":
            actions[digit] = self._digit_action(digit)
        self["actions"] = ActionMap(
            ["OkCancelActions", "NumberActions", "ColorActions"],
            actions,
            -1,
        )
        self.setTitle(str(title or ""))
        self._refresh()

    def _digit_action(self, digit):
        return lambda: self.enter_digit(digit)

    def _masked_text(self):
        symbols = [
            "•" if index < len(self._digits) else "_"
            for index in range(self.required_digits)
        ]
        groups = [
            " ".join(symbols[index : index + 4])
            for index in range(0, len(symbols), 4)
        ]
        return "     ".join(groups)

    def _refresh(self):
        self["code"].setText(self._masked_text())
        self["message"].setText(
            _("Digits entered: {}/{}").format(
                len(self._digits),
                self.required_digits,
            )
        )

    def enter_digit(self, digit):
        digit = str(digit or "")
        if digit not in "0123456789" or len(digit) != 1:
            return
        if len(self._digits) >= self.required_digits:
            return
        self._digits += digit
        self._refresh()

    def delete_digit(self):
        if self._digits:
            self._digits = self._digits[:-1]
        self._refresh()

    def confirm(self):
        if len(self._digits) != self.required_digits:
            self["message"].setText(
                _("Enter exactly {} digits.").format(
                    self.required_digits
                )
            )
            return
        self.close(self._digits)

    def request_recovery(self):
        if self.allow_recovery:
            self.close(RECOVERY_REQUEST)

    def cancel(self):
        self.close(None)

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Browse active YouTube broadcasts by category and language."""

from functools import partial
from threading import Event

import enigma
from Components.Label import Label
from Components.MenuList import MenuList
from Screens.Screen import Screen

try:
    from Components.MultiContent import MultiContentEntryText
except (ImportError, AttributeError):
    MultiContentEntryText = None

from .action_map import ActionMap
from .i18n import _, device_language, supported_language_names
from .main import _connect_timer, _scale
from .remote_footer import decorate_remote_footer, footer_item, install_remote_footer, prepared_skin
from .source_loader import SourceLoader
from .typography import clean_dynamic_text, fit_dynamic_text, font_px
from .utility_design import NativeSkin
from .youtube_live import LIVE_CATEGORIES, search_live_broadcasts
from .youtube_startup import RETRY_MESSAGE, youtube_error_message


_FOOTER = (
    footer_item("up_down", "select"),
    footer_item("ok", "play"),
    footer_item("green", "refresh"),
    footer_item("yellow", "Broadcast language"),
    footer_item("blue", "Categories"),
    footer_item("exit", "back"),
)
_ROW_BOX = (38, 205, 1844, 130)
_VISIBLE_ROWS = 5
_CYAN = "#22D3EE"


def _live_skin():
    width, height, px = _scale()
    skin = NativeSkin("GTYouTubeLiveScreen", width, height, px)
    skin.label("brand_gt", (62, 20, 90, 68), 43, _CYAN)
    skin.label("brand_name", (150, 20, 404, 68), 36)
    skin.label("brand_divider", (560, 26, 2, 57), background="#395879")
    skin.label("title", (594, 20, 870, 68), 43)
    skin.label("language", (1506, 20, 344, 68), 35, _CYAN, align="right")
    skin.label("rule", (38, 101, 1844, 3), background="#395879")
    skin.label("section", (52, 119, 1796, 70), 42)
    x, y, list_width, row_height = skin.rect(_ROW_BOX)
    skin.parts.append(
        '<widget name="channels" position="{},{}" size="{},{}" itemHeight="{}" '
        'scrollbarMode="showOnDemand" scrollbarWidth="{}" '
        'backgroundColor="#07182F" foregroundColor="#F4F8FF" '
        'backgroundColorSelected="#0C3655" foregroundColorSelected="#FFFFFF" />'.format(
            x, y, list_width, row_height * _VISIBLE_ROWS, row_height, max(5, px(15))))
    skin.label("status", (50, 868, 1810, 81), 29, "#ACCFEA", lines=2)
    return decorate_remote_footer(skin.finish(), _FOOTER, skin_fonts_scaled=True, show_web_hint=False)


class _BroadcastList(MenuList):
    """Use the image's native text entries for the two-line rows."""

    def __init__(self, width, height, px):
        _x, _y, list_width, row_height = NativeSkin("", width, height, px).rect(_ROW_BOX)
        self._plain_font = max(10, font_px(px, 36))
        self._frame = ()
        content = getattr(enigma, "eListboxPythonMultiContent", None)
        if content is not None and MultiContentEntryText is not None:
            try:
                super().__init__([], content=content)
                self._configure_rows(list_width, row_height, px)
                return
            except (AttributeError, RuntimeError, TypeError, ValueError):
                # Some vendor bindings only expose the basic MenuList.
                self._frame = ()
        super().__init__([])

    def _configure_rows(self, list_width, row_height, px):
        row_width = max(1, list_width - max(12, px(24)))
        edge = max(1, px(2))
        pad = px(36)
        text_width = max(1, row_width - 2 * pad)
        title_y = max(edge, round(row_height * 0.10))
        title_height = max(1, round(row_height * 0.46))
        channel_y = title_y + title_height
        channel_height = max(1, row_height - channel_y - max(edge, px(12)))
        self._title_box = (pad, title_y, text_width, title_height)
        self._channel_box = (pad, channel_y, text_width, channel_height)
        self._flags = (getattr(enigma, "RT_HALIGN_LEFT", 0) | getattr(enigma, "RT_VALIGN_CENTER", 0)
                       | getattr(enigma, "RT_ELLIPSIS", 0))
        self.l.setFont(0, enigma.gFont("Regular", min(self._plain_font + px(5), title_height)))
        self.l.setFont(1, enigma.gFont("Regular", min(max(9, font_px(px, 31)), channel_height)))
        self.l.setItemHeight(row_height)

        # Thin native text backgrounds also work on images with the older
        # progress/rectangle entry signatures.
        middle = max(1, row_height - 2 * edge)
        edges = ((0, 0, row_width, edge), (0, row_height - edge, row_width, edge),
                 (0, edge, edge, middle), (row_width - edge, edge, edge, middle))
        self._frame = tuple(MultiContentEntryText(
            pos=(x, y), size=(w, h), font=0, flags=self._flags, text=" ",
            color=0x395879, color_sel=0x22D3EE,
            backcolor=0x395879, backcolor_sel=0x22D3EE) for x, y, w, h in edges)

    def set_broadcasts(self, entries):
        if not self._frame:
            self.setList([row.title + (" · " + row.channel if row.channel else "") for row in entries])
            return
        self.setList([[row.video_id, *self._frame,
                       self._row_text(self._title_box, 0, row.title, 0xF4F8FF),
                       self._row_text(self._channel_box, 1, row.channel, 0xA7C3E2)]
                      for row in entries])

    def _row_text(self, box, font, value, color):
        x, y, w, h = box
        return MultiContentEntryText(pos=(x, y), size=(w, h), font=font, flags=self._flags,
                                     text=clean_dynamic_text(value), color=color, color_sel=color)

    def set_plain_font(self):
        if self._frame:
            return
        set_font = getattr(self.instance, "setFont", None)
        if callable(set_font):
            try:
                set_font(enigma.gFont("Regular", self._plain_font))
            except (AttributeError, RuntimeError, TypeError):
                pass


class GTYouTubeLiveScreen(Screen):
    def __init__(self, session, service):
        self.skin = prepared_skin(_live_skin)
        Screen.__init__(self, session)
        self._service = service
        self._language = ""
        self._category = "all"
        self._language_names = dict(supported_language_names())
        self._closed = self._started = False
        self._picker = None
        self._entries = []
        self._cancel = None
        self._list_message = self._action_message = self._play_token = ""
        self._reader = SourceLoader(enigma.eTimer(), _connect_timer, self._loaded)
        self._play_timer = enigma.eTimer()
        _connect_timer(self._play_timer, self._poll_play)

        title = "YouTube · " + _("Live")
        self["brand_gt"] = Label("GT")
        self["brand_name"] = Label("IPTV PLAYER PRO")
        self["brand_divider"] = Label("")
        self["title"] = Label(title)
        self["language"] = Label("")
        self["rule"] = Label("")
        self["section"] = Label(_("Live broadcasts"))
        self["channels"] = _BroadcastList(*_scale())
        self["status"] = Label("")
        self["actions"] = ActionMap(["OkCancelActions", "DirectionActions", "ColorActions"], {
            "ok": self._select,
            "cancel": self.close,
            "back": self.close,
            "up": lambda: self._move(-1),
            "down": lambda: self._move(1),
            "upRepeated": lambda: self._move(-1),
            "downRepeated": lambda: self._move(1),
            "green": self._refresh,
            "blue": partial(self._open_picker, False),
            "yellow": partial(self._open_picker, True),
        }, -1)
        install_remote_footer(self, _FOOTER, fit_horizontal=True)
        self.onLayoutFinish.append(self["channels"].set_plain_font)
        self.onShown.append(self._shown)
        self.onClose.append(self._stop)
        self.setTitle(title)

    def _shown(self):
        if self._closed:
            return
        if not self._started:
            self._refresh()
        if self._play_token:
            self._play_timer.start(100, True)

    def _refresh(self):
        if self._closed or self._picker is not None:
            return
        self._started = True
        self._cancel_play_request()
        if self._cancel is not None:
            self._cancel.set()
            self._reader.cancel()
        if self._entries:
            self._entries = []
            self["channels"].set_broadcasts(self._entries)
        self._action_message = ""
        language = self._language or device_language()
        fit_dynamic_text(self["language"], self._language_names.get(language, language), fallback_chars=20)
        heading = _("Live broadcasts")
        if self._category != "all":
            heading += " · " + _(LIVE_CATEGORIES[self._category])
        fit_dynamic_text(self["section"], heading, fallback_chars=92)
        self._list_message = _("Results are loading")
        self._update_status()
        self._cancel = Event()
        self._reader.request(partial(search_live_broadcasts, self._category, language, self._cancel))

    def _loaded(self, entries, error):
        if self._closed:
            return
        self._cancel = None
        if error is not None:
            if getattr(error, "code", None) == "rate_limited":
                self._list_message = _("YouTube is temporarily limiting searches")
            else:
                self._list_message = _("YouTube search is unavailable")
        else:
            self._entries = entries
            self["channels"].set_broadcasts(entries)
            self._list_message = "" if self._entries else _("No results found")
        self._update_status()

    def _open_picker(self, choose_language):
        if self._closed or self._picker is not None:
            return
        if choose_language:
            title, current = _("Broadcast language"), self._language
            device = device_language()
            automatic = _("Automatic") + " · " + self._language_names.get(device, device)
            choices = [(automatic, "")] + [(name, code) for code, name in self._language_names.items()]
        else:
            title, current = _("Categories"), self._category
            choices = [(_(caption), code) for code, caption in LIVE_CATEGORIES.items()]

        codes = [code for _caption, code in choices]
        selection = codes.index(current)
        self._cancel_play_request()
        if self._action_message:
            self._set_action_message("")
        picker = self._picker = object()

        def chosen(answer=None):
            if self._closed or self._picker is not picker:
                return
            self._picker = None
            if not isinstance(answer, (tuple, list)) or len(answer) < 2:
                return
            code = answer[1]
            if not isinstance(code, str) or code == current or code not in codes:
                return
            if choose_language:
                self._language = code
            else:
                self._category = code
            self._refresh()

        try:
            from Screens.ChoiceBox import ChoiceBox
            try:
                self.session.openWithCallback(chosen, ChoiceBox, title, choices, selection=selection)
            except TypeError:
                self.session.openWithCallback(chosen, ChoiceBox, title, choices)
        except Exception:
            if self._picker is picker:
                self._picker = None
                self._set_action_message(title + " · " + _("Temporarily unavailable"))

    def _select(self):
        if self._closed or self._picker is not None or self._play_token or not self._entries:
            return
        index = self["channels"].getSelectedIndex()
        if not isinstance(index, int) or not 0 <= index < len(self._entries):
            return
        row = self._entries[index]
        self._set_action_message(_("Please wait"))
        try:
            job = self._service.play(row.video_id, row.title, _request_owner=self, _live=True)
            self._play_token = job.get("token") or ""
        except Exception:
            self._play_token = ""
        if self._play_token:
            self._play_timer.start(100, True)
        else:
            self._set_action_message(_("Could not start the player."))

    def _poll_play(self):
        if self._closed or not self._play_token:
            return
        try:
            job = self._service.status(self._play_token).get("job") or {}
        except Exception:
            job = {}
        state = job.get("state")
        if state == "resolving":
            if job.get("attempt"):
                self._set_action_message(_(RETRY_MESSAGE))
            self._play_timer.start(150, True)
            return
        if state not in ("playing", "cancelled") and getattr(self.session, "current_dialog", None) is not self:
            # A failed player may still be closing. onShown will check again.
            return
        self._play_token = ""
        message = "" if state in ("playing", "cancelled") else youtube_error_message(job.get("error"))
        self._set_action_message(message)

    def _set_action_message(self, message):
        self._action_message = message
        self._update_status()

    def _update_status(self):
        fit_dynamic_text(self["status"], self._action_message or self._list_message,
                         max_lines=2, fallback_chars=80)

    def _cancel_play_request(self):
        if self._play_token:
            self._play_timer.stop()
            self._service.cancel_play(self._play_token)
            self._play_token = ""

    def _move(self, step):
        if self._closed or self._picker is not None or not self._entries:
            return
        self._cancel_play_request()
        if self._action_message:
            self._set_action_message("")
        menu = self["channels"]
        (menu.up if step < 0 else menu.down)()

    def _stop(self):
        self._closed = True
        self._picker = None
        if self._cancel is not None:
            self._cancel.set()
            self._cancel = None
        self._reader.close()
        self._cancel_play_request()

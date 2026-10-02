# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-native, account-independent YouTube search and playback."""

import threading
from collections import deque

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen
from enigma import eTimer

from .background import attach_background, attach_pixmap
from .i18n import _, N_, device_language
from .main import APP_BACKGROUND, _connect_timer, _scale
from .remote_footer import decorate_remote_footer, footer_item, install_remote_footer
from .settings import load_player_settings, save_player_settings
from .i18n import supported_language_names
from .typography import ellipsize_dynamic_text, font_px
from .web_youtube import WEB_YOUTUBE


ROWS = 7
QUALITY = ("360", "480", "720", "1080", "2160")
STREAM_MODES = ("auto", "compatible", "dash")
AUDIO_PREFERENCES = ("default", "original")
FOOTER = (
    footer_item("up_down", "select"),
    footer_item("left_right", "page"),
    footer_item("ok", "play"),
    footer_item("blue", "new_search"),
    footer_item("yellow", "quality"),
    footer_item("exit", "back"),
)

SETTINGS_FOOTER = (
    footer_item("up_down", "select"),
    footer_item("left_right", "change"),
    footer_item("green", "save"),
    footer_item("exit", "back"),
)


def _settings_skin():
    width, height, px = _scale()
    parts = ['<screen name="GTYouTubeSettingsScreen" position="0,0" size="{},{}" '
             'flags="wfNoBorder" backgroundColor="#020617">'.format(width, height)]

    def label(name, x, y, w, h, size=27, color="#F8FAFC", bg=None, align="left", z=3):
        parts.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" '
                     'foregroundColor="{}" backgroundColor="{}" transparent="{}" '
                     'halign="{}" valign="center" noWrap="1" zPosition="{}" />'.format(
                         name, px(x) if x else 0, px(y) if y else 0,
                         px(w), px(h), font_px(px, size), color,
                         bg or "#020617", "0" if bg else "1", align, z))

    parts.append('<widget name="app_bg" position="0,0" size="{},{}" zPosition="0" />'.format(width, height))
    label("header", 70, 44, 1150, 76, 42)
    label("brand", 1280, 44, 560, 76, 24, "#22D3EE", align="right")
    label("section", 70, 150, 1780, 80, 30, bg="#16475B", align="center")
    for index in range(4):
        y = 280 + index * 108
        label("focus_{}".format(index), 78, y, 1760, 88, 1, bg="#17677C", z=1)
        label("row_{}".format(index), 83, y + 4, 1750, 80, 1, bg="#101B30", z=2)
        label("label_{}".format(index), 115, y + 10, 870, 64, 29)
        label("value_{}".format(index), 1050, y + 10, 725, 64, 27, "#22D3EE", align="center")
    label("message", 95, 770, 1725, 95, 23, "#22D3EE")
    label("footer", 0, 982, 1920, 98, 24, bg="#080E1A", align="center")
    parts.append("</screen>")
    return decorate_remote_footer("\n".join(parts), SETTINGS_FOOTER,
                                  transparent_panel=True, skin_fonts_scaled=True,
                                  panel_height=92, bottom_padding=0)


class GTYouTubeSettingsScreen(Screen):
    """YouTube receiver preferences, committed with the parent Settings page."""

    LABELS = (
        N_("Video quality"), N_("Playback mode"),
        N_("Audio preference"), N_("Search language"),
    )

    def __init__(self, session, settings=None):
        self.skin = _settings_skin()
        Screen.__init__(self, session)
        self.settings = (settings or load_player_settings()).copy()
        self.selected_index = 0
        attach_background(self, "app_bg", APP_BACKGROUND)
        self["header"] = Label(_("YouTube settings"))
        self["brand"] = Label("GT IPTV PLAYER PRO")
        self["section"] = Label("YouTube")
        for index, message in enumerate(self.LABELS):
            self["focus_{}".format(index)] = Label("")
            self["row_{}".format(index)] = Label("")
            self["label_{}".format(index)] = Label(_(message))
            self["value_{}".format(index)] = Label("")
        self["message"] = Label(_("Press GREEN to save the changes."))
        self["footer"] = Label("")
        install_remote_footer(self, SETTINGS_FOOTER)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions"],
            {"up": lambda: self.move(-1), "down": lambda: self.move(1),
             "left": lambda: self.change(-1), "right": lambda: self.change(1),
             "ok": lambda: self.change(1), "green": self.save,
             "red": self.cancel, "cancel": self.cancel, "back": self.cancel}, -1)
        self._refresh()
        self.setTitle(_("YouTube settings"))

    def _refresh(self):
        values = (
            str(self.settings.youtube_resolution) + "P",
            {"auto": _("Automatic"), "compatible": _("Compatible"),
             "dash": "DASH"}.get(self.settings.youtube_stream_mode, _("Automatic")),
            {"default": _("YouTube default"),
             "original": _("Original audio")}.get(
                 self.settings.youtube_audio_preference, _("YouTube default")),
            _("Automatic") + " · " + dict(supported_language_names()).get(device_language(), "English"),
        )
        for index, value in enumerate(values):
            self["value_{}".format(index)].setText(value)
            self["focus_{}".format(index)].show() if index == self.selected_index else self["focus_{}".format(index)].hide()

    def move(self, step):
        self.selected_index = (self.selected_index + step) % len(self.LABELS)
        self._refresh()

    def change(self, step):
        field, options = (
            ("youtube_resolution", QUALITY),
            ("youtube_stream_mode", STREAM_MODES),
            ("youtube_audio_preference", AUDIO_PREFERENCES),
            (None, ()),
        )[self.selected_index]
        if field is None:
            self._refresh()
            return
        current = getattr(self.settings, field)
        try:
            position = options.index(current)
        except ValueError:
            position = 0
        setattr(self.settings, field, options[(position + step) % len(options)])
        self._refresh()

    def save(self):
        self.close({name: getattr(self.settings, name) for name in (
            "youtube_resolution", "youtube_stream_mode",
            "youtube_audio_preference")})

    def cancel(self):
        self.close(None)


def _skin():
    width, height, px = _scale()
    parts = ['<screen name="GTYouTubeSearchScreen" position="0,0" size="{},{}" '
             'flags="wfNoBorder" backgroundColor="#020617" transparent="0">'.format(width, height)]

    def label(name, x, y, w, h, size=25, color="#F8FAFC", background=None, z=3, align="left"):
        parts.append('<widget name="{}" position="{},{}" size="{},{}" '
                     'font="Regular;{}" foregroundColor="{}" zPosition="{}" '
                     'halign="{}" valign="center" noWrap="1" {} />'.format(
                         name, px(x) if x else 0, px(y) if y else 0,
                         px(w), px(h), font_px(px, size), color,
                         z, align, 'backgroundColor="{}" transparent="0"'.format(background)
                         if background else 'transparent="1"'))

    parts.append('<widget name="video_guard" position="0,0" size="{},{}" '
                 'font="Regular;1" backgroundColor="#020617" transparent="0" '
                 'zPosition="-30" />'.format(width, height))
    parts.append('<widget name="app_bg" position="0,0" size="{},{}" zPosition="-20" />'.format(width, height))
    label("header", 50, 32, 1390, 83, 43)
    label("quality", 1610, 42, 240, 60, 27, "#22D3EE", background="#0C1731", align="center")
    label("search_frame", 48, 139, 1824, 100, 1, background="#22D3EE", z=1)
    label("search_body", 52, 143, 1816, 92, 1, background="#091832", z=2)
    label("search_text", 90, 150, 1720, 78, 40)
    label("section", 54, 252, 1530, 44, 29, "#E5EAF3")
    for index in range(ROWS):
        y = 304 + index * 80
        label("focus_{}".format(index), 50, y, 1820, 74, 1, background="#22D3EE", z=2)
        label("row_{}".format(index), 54, y + 3, 1812, 68, 1, background="#0D1730", z=3)
        parts.append('<widget name="thumb_{}" position="{},{}" size="{},{}" '
                     'alphatest="blend" scale="1" zPosition="4" />'.format(
                         index, px(65), px(y + 5), px(114), px(64)))
        label("title_{}".format(index), 205, y + 4, 1450, 34, 29, z=5)
        label("detail_{}".format(index), 205, y + 38, 1450, 27, 21, "#ACBED1", z=5)
        label("duration_{}".format(index), 1685, y + 16, 150, 36, 21,
              "#E5EAF3", z=5, align="right")
    label("pages", 55, 866, 1810, 65, 27, "#22D3EE", align="center")
    label("status", 55, 928, 1810, 48, 23, "#C5D4E8", align="center")
    label("footer", 0, 982, 1920, 98, 24)
    parts.append("</screen>")
    return decorate_remote_footer("\n".join(parts), FOOTER, transparent_panel=True,
                                  show_dividers=False, skin_fonts_scaled=True,
                                  panel_height=92, bottom_padding=0)


class GTYouTubeSearchScreen(Screen):
    """Paged public results; all receiver widgets are updated on the GUI thread."""

    def __init__(self, session, search_service=None):
        self.skin = _skin()
        Screen.__init__(self, session)
        self._service = search_service if search_service is not None else WEB_YOUTUBE
        self._closed = False
        self._query = ""
        self._pages = {}
        self._page = 1
        self._selected = 0
        self._top = 0
        self._generation = 0
        self._busy = False
        self._events = deque()
        self._event_lock = threading.Lock()
        self._play_token = ""
        self._thumb_cancel = None
        self._thumb_key = ()
        self._thumb_active = False
        self._keyboard_open = False
        self._keyboard_requested = False
        self._poll_timer = eTimer()
        self._keyboard_timer = eTimer()
        _connect_timer(self._poll_timer, self._poll)
        _connect_timer(self._keyboard_timer, self.open_keyboard)

        attach_background(self, "app_bg", APP_BACKGROUND)
        for name in ("video_guard", "header", "quality", "search_frame", "search_body",
                     "search_text", "section", "pages", "status", "footer"):
            self[name] = Label("")
        for index in range(ROWS):
            for name in ("focus", "row", "title", "detail", "duration"):
                self["{}_{}".format(name, index)] = Label("")
            self["thumb_{}".format(index)] = Pixmap()
            self["thumb_{}".format(index)].hide()
        self["header"].setText("GT IPTV PLAYER PRO  |  YouTube")
        install_remote_footer(self, FOOTER)
        actions = {
            "ok": self.play_selected, "cancel": self.close, "back": self.close,
            "up": lambda: self.move(-1), "down": lambda: self.move(1),
            "upRepeated": lambda: self.move(-1), "downRepeated": lambda: self.move(1),
            "left": self.previous_page, "right": self.next_page,
            "leftRepeated": self.previous_page, "rightRepeated": self.next_page,
            "channelUp": self.previous_page, "channelDown": self.next_page,
            "blue": self.open_keyboard, "yellow": self.change_quality,
            "green": self.retry, "menu": self.open_keyboard,
        }
        for number in range(1, 10):
            actions[str(number)] = lambda page=number: self.jump_page(page)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions", "MenuActions",
             "NumberActions", "ChannelSelectBaseActions"], actions, -1)
        self.onShown.append(self._shown)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._cover_live_video)
        self.onClose.append(self._stop)
        self.setTitle("YouTube")
        self._render()

    def _cover_live_video(self):
        self["video_guard"].show()

    def _shown(self):
        if self._closed:
            return
        if not self._keyboard_requested:
            self._keyboard_requested = True
            self._keyboard_timer.start(60, True)
        if self._busy or self._thumb_active or self._play_token:
            self._poll_timer.start(100, True)

    def _stop(self):
        self._closed = True
        self._generation += 1
        if self._thumb_cancel is not None:
            self._thumb_cancel.set()
        if self._play_token:
            self._service.cancel_play(self._play_token)
        self._poll_timer.stop()
        self._keyboard_timer.stop()
        with self._event_lock:
            self._events.clear()

    def open_keyboard(self):
        if self._closed or self._keyboard_open:
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            try:
                from Screens.VirtualKeyBoard import VirtualKeyboard as VirtualKeyBoard
            except ImportError:
                self["status"].setText(_("The virtual keyboard is unavailable on this image."))
                return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self["status"].setText(_("Could not open the virtual keyboard."))
            return
        self._keyboard_open = True
        try:
            opener(self._query_entered, VirtualKeyBoard, title="YouTube " + _("Search"),
                   text=self._query)
        except Exception:
            self._keyboard_open = False
            self["status"].setText(_("Could not open the virtual keyboard."))

    def _query_entered(self, value=None):
        self._keyboard_open = False
        if self._closed or value is None:
            return
        query = str(value).strip()[:120]
        if len(query) < 2:
            self["status"].setText(_("Search") + " · 2+")
            return
        self._query = query
        self._generation += 1
        self._busy = False
        if self._play_token:
            self._service.cancel_play(self._play_token)
            self._play_token = ""
        self._pages.clear()
        self._page, self._selected, self._top = 1, 0, 0
        self._request_page(1)
        self._render()

    def _request_page(self, page):
        if self._closed or self._busy or not self._query:
            return
        if page in self._pages:
            self._page, self._selected, self._top = page, 0, 0
            self._render()
            return
        if page != len(self._pages) + 1:
            return
        previous = self._pages.get(page - 1) if page > 1 else None
        if previous is not None and not previous.get("has_more"):
            return
        cursor = previous.get("cursor", "") if previous is not None else ""
        if previous is not None and not cursor:
            return
        self._busy = True
        self["status"].setText(_("Loading data... Please wait."))
        generation, query, service = self._generation, self._query, self._service
        mailbox, mailbox_lock = self._events, self._event_lock

        def work():
            try:
                response, error = service.search(query, cursor), None
            except Exception as exc:
                response, error = None, getattr(exc, "code", "youtube_unavailable")
            with mailbox_lock:
                mailbox.append(("page", generation, page, response, error))

        thread = threading.Thread(target=work, name="GT-YouTube-Search")
        thread.daemon = True
        thread.start()
        self._poll_timer.start(100, True)

    def _poll(self):
        if self._closed:
            return
        with self._event_lock:
            events = list(self._events)
            self._events.clear()
        for event in events:
            if event[0] == "page":
                unused_kind, generation, page, response, error = event
                if generation != self._generation:
                    continue
                self._busy = False
                if error or not isinstance(response, dict):
                    self["status"].setText(_("Could not load content") + " · " + _("Press GREEN to try again"))
                    continue
                self._pages[page] = response
                self._page, self._selected, self._top = page, 0, 0
                self["status"].setText("")
                self._render()
            elif event[0] == "thumb":
                unused_kind, key, index, video_id, path = event
                if key == self._thumb_key and index < len(self._visible()) and path:
                    if self._visible()[index].get("id") == video_id:
                        attach_pixmap(self, "thumb_{}".format(index), path)
            elif event[0] == "thumb_done":
                if event[1] == self._thumb_key:
                    self._thumb_active = False
        if self._play_token:
            try:
                job = self._service.status(self._play_token).get("job") or {}
                state = job.get("state")
            except Exception:
                state = "failed"
            if state != "resolving":
                self._play_token = ""
                if state in ("failed", "cancelled", None):
                    self["status"].setText(_("Could not start the player."))
                else:
                    quality = int(job.get("quality") or 0)
                    requested = str(load_player_settings().youtube_resolution)
                    self["status"].setText(
                        "{} · {}P{}".format(_("Quality"), requested,
                                            " → {}P".format(quality) if quality and str(quality) != requested else "")
                        if quality else ""
                    )
        if self._busy or self._thumb_active or self._play_token:
            self._poll_timer.start(100, True)

    def _visible(self):
        response = self._pages.get(self._page) or {}
        return (response.get("results") or [])[self._top:self._top + ROWS]

    def _load_thumbnails(self, visible):
        key = tuple(item.get("id", "") for item in visible)
        if key == self._thumb_key:
            return
        if self._thumb_cancel is not None:
            self._thumb_cancel.set()
        self._thumb_key = key
        for index in range(ROWS):
            self["thumb_{}".format(index)].hide()
        if not key or not any(item.get("thumbnail") for item in visible):
            self._thumb_active = False
            return
        cancel = threading.Event()
        self._thumb_cancel = cancel
        records = [(index, item.get("id", ""), item.get("thumbnail", ""))
                   for index, item in enumerate(visible)]
        mailbox, mailbox_lock = self._events, self._event_lock
        self._thumb_active = True

        def work():
            try:
                from .browser import _cached_picon_path, _download_picon, _picon_cache_path
                for index, video_id, url in records:
                    if cancel.is_set():
                        break
                    # Only use the exact, server-produced YouTube thumbnail URL.
                    if url != "https://i.ytimg.com/vi/{}/hqdefault.jpg".format(video_id):
                        continue
                    try:
                        path = _cached_picon_path(url) or _download_picon(
                            url, _picon_cache_path(url), timeout=4)
                    except Exception:
                        path = ""
                    if path and not cancel.is_set():
                        with mailbox_lock:
                            mailbox.append(("thumb", key, index, video_id, path))
            finally:
                with mailbox_lock:
                    mailbox.append(("thumb_done", key))

        thread = threading.Thread(target=work, name="GT-YouTube-Thumbs")
        thread.daemon = True
        thread.start()
        self._poll_timer.start(100, True)

    def _render(self):
        if self._closed:
            return
        settings = load_player_settings()
        self["quality"].setText(str(settings.youtube_resolution) + "P")
        ellipsize_dynamic_text(self["search_text"], self._query or (_("Search") + "…"),
                               fallback_chars=58)
        self["section"].setText(_("Search") + " · YouTube")
        response = self._pages.get(self._page) or {}
        entries = response.get("results") or []
        visible = self._visible()
        for index in range(ROWS):
            focused = bool(index < len(visible) and self._top + index == self._selected)
            if focused:
                self["focus_{}".format(index)].show()
            else:
                self["focus_{}".format(index)].hide()
            item = visible[index] if index < len(visible) else {}
            if item:
                self["row_{}".format(index)].show()
            else:
                self["row_{}".format(index)].hide()
            ellipsize_dynamic_text(self["title_{}".format(index)],
                                   item.get("title", ""), fallback_chars=65)
            detail = "  •  ".join(value for value in
                                   (item.get("channel", ""), item.get("published", "")) if value)
            ellipsize_dynamic_text(self["detail_{}".format(index)], detail, fallback_chars=76)
            self["duration_{}".format(index)].setText(str(item.get("duration", ""))[:20])
        self._load_thumbnails(visible)
        if not self._query:
            self["pages"].setText("")
            return
        last = max(self._pages) if self._pages else 0
        first = max(1, min(self._page - 2, max(1, last - 3)))
        numbers = []
        for page in range(first, min(last + 2, first + 5)):
            if page > last and not response.get("has_more"):
                break
            numbers.append("[{}]".format(page) if page == self._page else str(page))
        if response.get("has_more"):
            numbers.append("…  ›")
        self["pages"].setText(_("Page") + "  " + "   ".join(numbers))
        if not entries and not self._busy:
            self["status"].setText(_("No content found."))

    def move(self, step):
        if self._closed or self._keyboard_open:
            return
        entries = (self._pages.get(self._page) or {}).get("results") or []
        if not entries:
            return
        self._selected = max(0, min(len(entries) - 1, self._selected + step))
        if self._selected < self._top:
            self._top = self._selected
        elif self._selected >= self._top + ROWS:
            self._top = self._selected - ROWS + 1
        self._render()

    def previous_page(self):
        if not self._busy and self._page > 1:
            self._request_page(self._page - 1)

    def next_page(self):
        if not self._busy:
            self._request_page(self._page + 1)

    def jump_page(self, page):
        if not self._busy and 1 <= page <= len(self._pages) + 1:
            self._request_page(page)

    def retry(self):
        if not self._busy and self._query:
            self._request_page(len(self._pages) + 1)

    def play_selected(self):
        if self._closed or self._keyboard_open:
            return
        entries = (self._pages.get(self._page) or {}).get("results") or []
        if not entries or self._selected >= len(entries):
            self.open_keyboard()
            return
        video = entries[self._selected]
        if self._play_token:
            self._service.cancel_play(self._play_token)
        try:
            result = self._service.play(video["id"], video.get("title", "YouTube"))
            self._play_token = result["token"]
        except Exception:
            self["status"].setText(_("Could not start the player."))
            return
        self["status"].setText(_("Please wait"))
        self._poll_timer.start(100, True)

    def change_quality(self):
        if self._closed:
            return
        settings = load_player_settings()
        current = str(settings.youtube_resolution)
        settings.youtube_resolution = QUALITY[(QUALITY.index(current) + 1) % len(QUALITY)] if current in QUALITY else "720"
        if save_player_settings(settings):
            self._render()
            self["status"].setText(_("Quality") + " · " + settings.youtube_resolution + "P")
        else:
            self["status"].setText(_("Settings could not be saved. Check the receiver storage."))


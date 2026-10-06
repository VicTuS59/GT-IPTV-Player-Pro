# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-native, account-independent YouTube search and playback."""

import threading
from collections import deque

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
try:
    from Components.VideoWindow import VideoWindow
except ImportError:
    VideoWindow = None
from Screens.Screen import Screen
from enigma import eTimer

from .background import attach_background, attach_pixmap
from .i18n import _, N_, device_language
from .main import APP_BACKGROUND, _connect_timer, _scale
from .remote_footer import decorate_remote_footer, footer_item, install_remote_footer, set_remote_footer
from .settings import load_player_settings, save_player_settings
from .i18n import supported_language_names
from .typography import ellipsize_dynamic_text, fit_dynamic_text, font_px
from .web_youtube import WEB_YOUTUBE
from .youtube_options import YOUTUBE_QUALITIES, DEFAULT_YOUTUBE_QUALITY, quality_label
from .youtube_preview import YouTubePreviewMixin
from .youtube_skin import youtube_search_skin, youtube_art


ROWS = 5
HISTORY_ROWS = 7
QUALITY = YOUTUBE_QUALITIES
FOOTER = (
    footer_item("up_down", "select"),
    footer_item("left_right", "page"),
    footer_item("ok", "play"),
    footer_item("blue", "new_search"),
    footer_item("yellow", "quality"),
    footer_item("red", "search_history"),
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
    for index in range(3):
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
    """Commit shared YouTube preferences from this page's GREEN key."""

    LABELS = (
        N_("Video quality"), N_("Play next video automatically"),
        N_("Search language"),
    )

    def __init__(self, session, settings=None):
        self.skin = _settings_skin()
        Screen.__init__(self, session)
        shared = load_player_settings()
        self.settings = (settings or shared).copy()
        self.settings.youtube_resolution = shared.youtube_resolution
        self.settings.youtube_autoplay = shared.youtube_autoplay
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
            quality_label(self.settings.youtube_resolution),
            _("On") if self.settings.youtube_autoplay else _("Off"),
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
            ("youtube_autoplay", (False, True)),
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
        try:
            shared = load_player_settings()
            shared.youtube_resolution = self.settings.youtube_resolution
            shared.youtube_autoplay = self.settings.youtube_autoplay
            shared.youtube_dash = True
            shared.youtube_stream_mode = "auto"
            shared.youtube_audio_preference = "default"
            saved = save_player_settings(shared)
        except Exception:
            saved = False
        if not saved:
            self["message"].setText(_("Settings could not be saved. Check the receiver storage."))
            return
        self.close({name: getattr(self.settings, name) for name in (
            "youtube_resolution", "youtube_autoplay")})

    def cancel(self):
        self.close(None)


def _history_skin(footer=FOOTER):
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
    for index in range(HISTORY_ROWS):
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
    return decorate_remote_footer("\n".join(parts), footer, transparent_panel=True,
                                  show_dividers=False, skin_fonts_scaled=True,
                                  panel_height=92, bottom_padding=0)


def _skin(footer=FOOTER):
    return youtube_search_skin(footer, ROWS)


class GTYouTubeSearchScreen(YouTubePreviewMixin, Screen):
    """Paged public results; all receiver widgets are updated on the GUI thread."""

    def __init__(self, session, search_service=None):
        self.skin = _skin()
        Screen.__init__(self, session)
        self._service = search_service if search_service is not None else WEB_YOUTUBE
        self._closed = False
        self._query = ""
        self._quality = str(load_player_settings().youtube_resolution)
        self._pages = {}
        self._page = 1
        self._selected = 0
        self._top = 0
        self._generation = 0
        self._busy = False
        self._events = deque()
        self._event_lock = threading.Lock()
        self._play_token = ""
        self._pending_play_id = ""
        self._history_fullscreen_id = ""
        self._thumbnail_paths = {}
        self._preview_records = {}
        self._preview_art_key = None
        self._preview_art_loaded_key = None
        self._youtube_border = None
        self._search_token = ""
        self._search_page = 1
        self._search_signature = None
        self._prefetch = None
        self._thumb_cancel = None
        self._thumb_key = ()
        self._thumb_active = False
        self._keyboard_open = False
        self._keyboard_requested = False
        self._poll_timer = eTimer()
        self._keyboard_timer = eTimer()
        _connect_timer(self._poll_timer, self._poll)
        _connect_timer(self._keyboard_timer, self._open_initial_search)
        self._init_youtube_preview(VideoWindow is not None)

        attach_background(self, "app_bg", APP_BACKGROUND)
        for name in ("video_guard", "header", "header_divider", "youtube_brand", "quality", "search_text", "section",
                     "pages", "status", "footer", "preview_mask", "preview_badge",
                     "preview_title", "preview_detail", "preview_action", "preview_live"):
            self[name] = Label("")
        for name in ("quality_frame", "search_frame", "search_icon", "preview_panel", "preview_art", "preview_live_bg", "youtube_logo", "footer_frame"):
            self[name] = Pixmap()
        width, height, _px = _scale()
        self["preview_video"] = (VideoWindow(decoder=0, fb_width=width, fb_height=height)
                                 if VideoWindow is not None else Label(""))
        self["preview_video"].hide()
        self["preview_art"].hide()
        for index in range(ROWS):
            for name in ("focus", "row"):
                self["{}_{}".format(name, index)] = Pixmap()
            self["live_bg_{}".format(index)] = Pixmap()
            for name in ("title", "detail", "duration"):
                self["{}_{}".format(name, index)] = Label("")
            self["thumb_{}".format(index)] = Pixmap()
            self["thumb_{}".format(index)].hide()
        self["header"].setText("GT IPTV PLAYER PRO")
        self["header_divider"].setText("|")
        self["youtube_brand"].setText("YouTube")
        install_remote_footer(self, FOOTER, fit_horizontal=True)
        actions = {
            "ok": self.play_selected, "cancel": self.close, "back": self.close,
            "up": lambda: self.move(-1), "down": lambda: self.move(1),
            "upRepeated": lambda: self.move(-1), "downRepeated": lambda: self.move(1),
            "left": self.previous_page, "right": self.next_page,
            "leftRepeated": self.previous_page, "rightRepeated": self.next_page,
            "channelUp": self.previous_page, "channelDown": self.next_page,
            "blue": self.open_keyboard, "yellow": self.change_quality,
            "red": self.open_history, "menu": self.retry,
        }
        for number in range(1, 10):
            actions[str(number)] = lambda page=number: self.jump_page(page)
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions", "MenuActions",
             "NumberActions", "ChannelSelectBaseActions"], actions, -1)
        self.onShown.append(self._shown)
        if hasattr(self, "onExecBegin"):
            self.onExecBegin.append(self._sync_quality)
        if hasattr(self, "onLayoutFinish"):
            self.onLayoutFinish.append(self._cover_live_video)
        self.onClose.append(self._stop)
        self.setTitle("YouTube")
        self._render()

    def _cover_live_video(self):
        self["video_guard"].show()
        self._capture_youtube_geometry()
        self._render_preview()

    def _shown(self):
        if self._closed:
            return
        self._sync_quality()
        if not self._keyboard_requested:
            self._keyboard_requested = True
            self._keyboard_timer.start(60, True)
        self._render()
        self._preview_window_applied = False
        self._resume_youtube_window()
        if self._preview_owns_service():
            self._preview_timer.start(200, True)
        self._poll_timer.start(100 if self._busy or self._thumb_active or self._play_token else
                               500 if self._prefetch and self._prefetch["token"] else 1000, True)

    def _sync_quality(self):
        if self._closed or self._keyboard_open:
            return False
        quality = str(load_player_settings().youtube_resolution)
        if quality == self._quality:
            return False
        self._quality = quality
        self._render()
        return True

    def _stop(self):
        self._history_fullscreen_id = ""
        self._close_youtube_preview()
        self._closed = True
        self._generation += 1
        self._cancel_search()
        if self._thumb_cancel is not None:
            self._thumb_cancel.set()
        if self._play_token:
            self._service.cancel_play(self._play_token)
        self._poll_timer.stop()
        self._keyboard_timer.stop()
        with self._event_lock:
            self._events.clear()

    def _open_initial_search(self):
        self.open_history()

    def open_history(self):
        if self._closed or self._keyboard_open:
            return
        self._history_fullscreen_id = ""
        self.session.openWithCallback(self._history_selected, GTYouTubeHistoryScreen, self._service)

    def _history_selected(self, query=None):
        if self._closed or query is None:
            return
        if isinstance(query, dict):
            self._playback_history_selected(query)
            return
        if query:
            self._query_entered(query)
        else:
            self.open_keyboard()

    def _playback_history_selected(self, video=None):
        if self._closed or not isinstance(video, dict):
            return
        self._history_fullscreen_id = video.get("id", "")
        self._play_video(video, video.get("context", ""))
        self._poll_timer.start(100, True)

    def open_keyboard(self):
        if self._closed or self._keyboard_open:
            return
        self._history_fullscreen_id = ""
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
        self._history_fullscreen_id = ""
        self._quality = str(load_player_settings().youtube_resolution)
        self._generation += 1
        self._cancel_search()
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
        self._history_fullscreen_id = ""
        self._sync_quality()
        if page in self._pages:
            self._page, self._selected, self._top = page, 0, 0
            self._render()
            self._prepare_next_page()
            return
        if page != len(self._pages) + 1:
            return
        previous = self._pages.get(page - 1) if page > 1 else None
        if previous is not None and not previous.get("has_more"):
            return
        cursor = previous.get("cursor", "") if previous is not None else ""
        if previous is not None and not cursor:
            return
        prepared = self._prefetch
        if prepared and prepared["page"] == page:
            self._prefetch = None
            response = prepared["response"]
            if not response.get("error"):
                self._busy = True
                self._search_token = prepared["token"]
                self._search_page = page
                self._search_signature = None
                self._selected, self._top = 0, 0
                self._search_update(response)
                self._poll_timer.start(100, True)
                return
            self._cancel_token(prepared["token"])
        self._busy = True
        self["status"].setText(_("Loading data... Please wait."))
        start = getattr(self._service, "search_async", None)
        if callable(start):
            try:
                response = start(self._query, cursor)
                self._search_token = response.get("search_token", "")
                self._search_page = page
                self._search_signature = None
                self._search_update(response)
            except Exception:
                self._busy = False
                self["status"].setText(_("Could not load content") + " · MENU · " + _("Retry"))
            self._poll_timer.start(100, True)
            return
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

    def _cancel_search(self):
        token, self._search_token = self._search_token, ""
        self._search_signature = None
        self._cancel_token(token)
        self._cancel_prefetch()

    def _cancel_token(self, token):
        if token:
            try:
                self._service.cancel_search(token)
            except Exception:
                pass

    def _cancel_prefetch(self):
        prepared, self._prefetch = self._prefetch, None
        if prepared:
            self._cancel_token(prepared["token"])

    def _prepare_next_page(self):
        if self._closed or self._busy or not self._query:
            return
        previous = self._pages.get(self._page) or {}
        page = self._page + 1
        if (page in self._pages or previous.get("pending") or previous.get("error")
                or not previous.get("has_more") or not previous.get("cursor")):
            return
        if self._prefetch and self._prefetch["page"] == page:
            return
        start = getattr(self._service, "search_async", None)
        if not callable(start):
            return
        self._cancel_prefetch()
        try:
            response = start(self._query, previous["cursor"])
            self._prefetch = {"page": page, "response": response,
                              "token": response.get("search_token", "") if response.get("pending") else ""}
        except Exception:
            # A speculative page must not interrupt the visible results.
            self._prefetch = None

    def _poll_prefetch(self):
        prepared = self._prefetch
        if not prepared or not prepared["token"]:
            return
        try:
            response = self._service.search_status(prepared["token"])
            prepared["response"] = response
            if not response.get("pending"):
                prepared["token"] = ""
        except Exception:
            self._cancel_prefetch()

    def _search_update(self, response):
        pending, error = bool(response.get("pending")), response.get("error")
        entries = response.get("results") or []
        signature = (tuple(entry.get("id") for entry in entries), pending, error,
                     response.get("cursor"), response.get("pagination_warning"))
        if signature == self._search_signature:
            return
        self._search_signature = signature
        previous = (self._pages.get(self._search_page) or {}).get("results") or []
        selected_id = previous[self._selected].get("id") if self._selected < len(previous) else ""
        self._busy = pending
        self._pages[self._search_page] = response
        self._page = self._search_page
        self._selected = next((index for index, entry in enumerate(entries)
                               if entry.get("id") == selected_id), 0)
        self._top = min(self._top, self._selected)
        if self._selected >= self._top + ROWS:
            self._top = self._selected - ROWS + 1
        self._render()
        self["status"].setText(
            _("Loading data... Please wait.") if pending else
            _("Could not load content") + " · MENU · " + _("Retry") if error or response.get("pagination_warning") else
            "" if entries else _("No results found"))
        if not pending:
            self._search_token = ""
            self._prepare_next_page()

    def _poll(self):
        if self._closed:
            return
        if getattr(self, "execing", True):
            self._sync_quality()
        if self._search_token:
            try:
                self._search_update(self._service.search_status(self._search_token))
            except Exception as exc:
                self._cancel_search()
                self._busy = False
                if getattr(exc, "code", "") == "youtube_quality_changed":
                    self._query_entered(self._query)
                else:
                    self["status"].setText(_("Could not load content") + " · MENU · " + _("Retry"))
        self._poll_prefetch()
        with self._event_lock:
            events = list(self._events)
            self._events.clear()
        for event in events:
            if event[0] == "page":
                unused_kind, generation, page, response, error = event
                if generation != self._generation:
                    continue
                self._busy = False
                if error == "youtube_quality_changed":
                    self._query_entered(self._query)
                    continue
                if error or not isinstance(response, dict):
                    self["status"].setText(_("Could not load content") + " · MENU · " + _("Retry"))
                    continue
                self._pages[page] = response
                self._page, self._selected, self._top = page, 0, 0
                self["status"].setText("")
                self._render()
            elif event[0] == "thumb":
                unused_kind, key, index, video_id, path = event
                if key == self._thumb_key and index < len(self._visible()) and path:
                    if self._visible()[index].get("id") == video_id:
                        self._thumbnail_paths[video_id] = path
                        attach_pixmap(self, "thumb_{}".format(index), path, cover_ratio=(16, 9))
                        self._render_preview()
            elif event[0] == "thumb_done":
                if event[1] == self._thumb_key:
                    self._thumb_active = False
        if self._play_token:
            job = {}
            try:
                job = self._service.status(self._play_token).get("job") or {}
                state = job.get("state")
            except Exception:
                state = "failed"
            if state != "resolving":
                self._play_token = ""
                self._pending_play_id = ""
                if state in ("failed", "cancelled", None):
                    self["status"].setText(
                        (_("No videos found at the selected quality.") if job.get("error") == "youtube_quality_unavailable"
                         else _("Quality changed. Search again.") if job.get("error") == "youtube_quality_changed"
                         else _("Could not start the player."))
                        + (" · ServiceApp / ExtEplayer3 (5002)"
                           if job.get("error") == "youtube_native_player_required" else "")
                    )
                else:
                    quality = int(job.get("quality") or 0)
                    requested = str(job.get("requested_quality") or load_player_settings().youtube_resolution)
                    self["status"].setText(
                        "{} · {}P{}".format(_("Quality"), requested,
                                            " → {}P".format(quality) if quality and str(quality) != requested else "")
                        if quality else ""
                    )
        if self._history_fullscreen_id and not self._play_token:
            video_id = self._history_fullscreen_id
            self._history_fullscreen_id = ""
            if self._preview_owns_service() and self.current_item.stream_id == video_id:
                # History opens in fullscreen after the resolver has committed
                # playback. Transfer any pending bookmark seek to that player.
                self._open_youtube_fullscreen(allow_pending_resume=True)
        self._poll_timer.start(100 if self._busy or self._thumb_active or self._play_token else
                               500 if self._prefetch and self._prefetch["token"] else 1000, True)

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
        self["quality"].setText(quality_label(settings.youtube_resolution))
        ellipsize_dynamic_text(self["search_text"], self._query or (_("Search") + "…"),
                               fallback_chars=58)
        self["section"].setText(_("Search") + " · YouTube")
        response = self._pages.get(self._page) or {}
        entries = response.get("results") or []
        visible = self._visible()
        self._preview_records.update({item["id"]: item for item in entries if item.get("id")})
        self._refresh_youtube_border()
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
            fit_dynamic_text(self["title_{}".format(index)], item.get("title", ""),
                             max_lines=2, fallback_chars=70)
            self["live_bg_{}".format(index)].show() if item.get("live") else self["live_bg_{}".format(index)].hide()
            detail = "  •  ".join(value for value in
                                   (item.get("channel", ""), item.get("published", "")) if value)
            ellipsize_dynamic_text(self["detail_{}".format(index)], detail, fallback_chars=76)
            self._fit_youtube_caption(
                "duration_{}".format(index),
                _("Live") if item.get("live") else str(item.get("duration", ""))[:20],
                22, minimum=11, max_lines=2 if item.get("live") else 1)
        self._load_thumbnails(visible)
        self._render_preview()
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
        if response.get("pagination_warning") == "youtube_unavailable" and not self._busy:
            self["status"].setText(_("Could not load content") + " · MENU · " + _("Retry"))
        elif not entries and not self._busy and response:
            self["status"].setText(_("No results found"))

    def move(self, step):
        if self._closed or self._keyboard_open:
            return
        entries = (self._pages.get(self._page) or {}).get("results") or []
        if not entries:
            return
        self._history_fullscreen_id = ""
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
            current = self._pages.get(self._page) or {}
            if current.get("error") or current.get("pending"):
                self._query_entered(self._query)
            else:
                self._request_page(len(self._pages) + 1)

    def play_selected(self):
        if self._closed or self._keyboard_open or self._history_fullscreen_id:
            return
        self._sync_quality()
        entries = (self._pages.get(self._page) or {}).get("results") or []
        if not entries or self._selected >= len(entries):
            if self._preview_owns_service():
                video = self._preview_records.get(self.current_item.stream_id,
                         {"id": self.current_item.stream_id, "title": self.current_item.name})
                self._play_video(video, video.get("context", ""))
                return
            self.open_keyboard()
            return
        video = entries[self._selected]
        self._play_video(video, (self._pages.get(self._page) or {}).get("context", ""))

    def _play_video(self, video, context=""):
        if not self._preview_available:
            self["status"].setText(_("Could not start the player."))
            return
        video_id = video.get("id", "")
        if self._play_token and video_id == self._pending_play_id:
            return
        if (not self._play_token and self._preview_owns_service()
                and self.current_item.stream_id == video_id
                and str(self.current_item.youtube_requested_quality) == self._quality):
            self._open_youtube_fullscreen()
            return
        if self._play_token:
            self._service.cancel_play(self._play_token)
        try:
            self._preview_records[video_id] = dict(video)
            result = self._service.play(video_id, video.get("title", "YouTube"), context,
                                        _preview_owner=self)
            self._play_token = result["token"]
            self._pending_play_id = video_id
        except Exception:
            self["status"].setText(_("Could not start the player."))
            return
        self["status"].setText(_("Please wait"))
        self._poll_timer.start(100, True)

    def _refresh_youtube_border(self):
        from .channel_highlight import saved_selection_border
        border = saved_selection_border()
        if border == self._youtube_border:
            return
        try:
            from Tools.LoadPixmap import LoadPixmap
            pixmap = LoadPixmap(path=youtube_art("focus", border=border))
            for index in range(ROWS):
                self["focus_{}".format(index)].instance.setPixmap(pixmap)
            self._youtube_border = border
        except (ImportError, AttributeError, TypeError):
            pass

    def _fit_youtube_caption(self, name, text, size, minimum=12, max_lines=1):
        _width, _height, px = _scale()
        fit_dynamic_text(self[name], text, max_lines=max_lines,
                         preferred_size=max(minimum, font_px(px, size)),
                         min_size=max(5, px(10)))

    def _render_preview(self):
        if self._closed:
            return
        playing = self._preview_owns_service()
        entries = (self._pages.get(self._page) or {}).get("results") or []
        selected = entries[self._selected] if self._selected < len(entries) else {}
        video = (self._preview_records.get(self.current_item.stream_id,
                 {"id": self.current_item.stream_id, "title": self.current_item.name})
                 if playing else selected)
        fit_dynamic_text(self["preview_title"], video.get("title", ""), max_lines=3, fallback_chars=90)
        fit_dynamic_text(self["preview_detail"],
                         " · ".join(str(video.get(field, "")) for field in ("channel", "published") if video.get(field)),
                         max_lines=2, fallback_chars=54)
        badge = (_("Live") if video.get("live") else
                 quality_label(str(self.current_item.youtube_selected_quality)) if playing else _("Preview"))
        self._fit_youtube_caption("preview_badge", badge if video else _("YouTube"), 22)
        live = bool(video.get("live"))
        self._fit_youtube_caption("preview_live", _("Live"), 22, max_lines=2)
        for name in ("preview_live_bg", "preview_live"):
            self[name].show() if live else self[name].hide()
        self["preview_badge"].hide() if live else self["preview_badge"].show()
        fullscreen = bool(playing and (not selected or selected.get("id") == self.current_item.stream_id)
                          and str(self.current_item.youtube_requested_quality) == self._quality)
        self._fit_youtube_caption("preview_action", "OK · " + (_("Full screen") if fullscreen else _("Play")), 26)
        items = list(FOOTER)
        items[2] = footer_item("ok", "fullscreen" if fullscreen else "play")
        if tuple(items) != getattr(self, "_remote_footer_items", None):
            set_remote_footer(self, items)
        if playing:
            self["preview_art"].hide()
            self["preview_mask"].hide()
            return
        self["preview_video"].hide()
        self["preview_mask"].show()
        key = (video.get("id", ""), self._thumbnail_paths.get(video.get("id", ""), ""))
        if key != self._preview_art_key:
            self._preview_art_key = key
            self["preview_art"].hide()
            if key[1]:
                attach_pixmap(self, "preview_art", key[1], cover_ratio=(16, 9),
                              on_loaded=self._preview_art_loaded)
        elif key[1] and self._preview_art_loaded_key == key:
            self["preview_art"].show()

    def _preview_art_loaded(self, loaded):
        if loaded:
            self._preview_art_loaded_key = self._preview_art_key
        if self._closed or self._preview_owns_service():
            self["preview_art"].hide()

    def change_quality(self):
        if self._closed:
            return
        settings = load_player_settings().copy()
        current = str(settings.youtube_resolution)
        settings.youtube_resolution = QUALITY[(QUALITY.index(current) + 1) % len(QUALITY)] if current in QUALITY else DEFAULT_YOUTUBE_QUALITY
        if save_player_settings(settings):
            self._sync_quality()
            if not self._query:
                self["status"].setText(_("Quality") + " · " + quality_label(settings.youtube_resolution))
        else:
            self["status"].setText(_("Settings could not be saved. Check the receiver storage."))



HISTORY_FOOTER = (
    footer_item("up_down", "select"), footer_item("ok", "search"),
    footer_item("red", "delete_account"), footer_item("yellow", "clear"),
    footer_item("green", "playback_history"),
    footer_item("blue", "new_search"), footer_item("exit", "back"),
)

PLAYBACK_HISTORY_FOOTER = (
    footer_item("up_down", "select"), footer_item("left_right", "page"),
    footer_item("ok", "play"), footer_item("red", "delete_account"),
    footer_item("yellow", "delete_all"), footer_item("exit", "back"),
)


class GTYouTubeHistoryScreen(Screen):
    """Use the same adaptive full-screen style and footer as YouTube search."""

    HISTORY_TITLE = N_("Search history")
    HISTORY_FOOTER = HISTORY_FOOTER
    PLAYBACK_HISTORY_ACCESS = True

    def __init__(self, session, service=None):
        self.skin = _history_skin(self.HISTORY_FOOTER).replace('name="GTYouTubeSearchScreen"',
                                                     'name="{}"'.format(type(self).__name__))
        Screen.__init__(self, session)
        self._service = service if service is not None else WEB_YOUTUBE
        self._entries = self._load_history()
        self._selected = self._top = 0
        attach_background(self, "app_bg", APP_BACKGROUND)
        for name in ("video_guard", "header", "quality", "search_frame", "search_body",
                     "search_text", "section", "pages", "status", "footer"):
            self[name] = Label("")
        self["header"].setText("GT IPTV PLAYER PRO  |  YouTube")
        self["search_text"].setText(_(self.HISTORY_TITLE))
        self["section"].setText(_(self.HISTORY_TITLE))
        for index in range(HISTORY_ROWS):
            for name in ("focus", "row", "title", "detail", "duration"):
                self["{}_{}".format(name, index)] = Label("")
            self["thumb_{}".format(index)] = Pixmap()
            self["thumb_{}".format(index)].hide()
        install_remote_footer(self, self.HISTORY_FOOTER)
        actions = {"up": lambda: self.move(-1), "down": lambda: self.move(1),
             "left": lambda: self.move(-HISTORY_ROWS), "right": lambda: self.move(HISTORY_ROWS),
             "channelUp": lambda: self.move(-HISTORY_ROWS), "channelDown": lambda: self.move(HISTORY_ROWS),
             "ok": self.select, "red": self.delete, "yellow": self.clear,
             "blue": lambda: self.close(""), "cancel": self.close, "back": self.close}
        if self.PLAYBACK_HISTORY_ACCESS:
            actions["green"] = self.open_playback_history
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions", "ChannelSelectBaseActions"],
            actions, -1)
        self.setTitle(_(self.HISTORY_TITLE))
        self._render()

    def _load_history(self):
        return self._service.history().get("queries", [])

    def open_playback_history(self):
        self.session.openWithCallback(self._playback_history_selected,
                                      GTYouTubePlaybackHistoryScreen, self._service)

    def _playback_history_selected(self, video=None):
        if isinstance(video, dict):
            self.close(video)

    def move(self, step):
        self._selected = max(0, min(len(self._entries) - 1, self._selected + step))
        if self._selected < self._top:
            self._top = self._selected
        elif self._selected >= self._top + HISTORY_ROWS:
            self._top = self._selected - HISTORY_ROWS + 1
        self._render()

    def _render(self):
        self["quality"].setText(quality_label(load_player_settings().youtube_resolution))
        for index in range(HISTORY_ROWS):
            position = self._top + index
            entry = self._entries[position] if position < len(self._entries) else None
            value = entry.get("title", "") if isinstance(entry, dict) else entry or ""
            ellipsize_dynamic_text(self["title_{}".format(index)], value, fallback_chars=65)
            self["row_{}".format(index)].show() if value else self["row_{}".format(index)].hide()
            self["focus_{}".format(index)].show() if value and position == self._selected else self["focus_{}".format(index)].hide()
            if isinstance(entry, dict):
                self["detail_{}".format(index)].setText(quality_label(entry["quality"]) if entry.get("quality") else "")
                duration = int(entry.get("duration") or 0)
                self["duration_{}".format(index)].setText("{}:{:02d}".format(duration // 60, duration % 60) if duration else "")
            else:
                self["detail_{}".format(index)].setText("")
                self["duration_{}".format(index)].setText("")
        self["pages"].setText("{} / {}".format(self._selected + 1, len(self._entries)) if self._entries else "")
        self["status"].setText("" if self._entries else _("No content found."))

    def select(self):
        self.close(self._entries[self._selected] if self._entries else "")

    def delete(self):
        if self._entries:
            self._remove({"query": self._entries[self._selected]})

    def clear(self):
        self._remove({})

    def _remove(self, payload):
        try:
            self._entries = self._service.delete_history(payload).get("queries", [])
            self.move(0)
        except Exception:
            self["status"].setText(_("Settings could not be saved. Check the receiver storage."))


class GTYouTubePlaybackHistoryScreen(GTYouTubeHistoryScreen):
    """Replay successful starts and edit the persistent recent-video list."""

    HISTORY_TITLE = N_("Playback history")
    HISTORY_FOOTER = PLAYBACK_HISTORY_FOOTER
    PLAYBACK_HISTORY_ACCESS = False

    def _load_history(self):
        response = self._service.playback_history()
        self._context = response.get("context", "")
        return response.get("videos", [])

    def select(self):
        if self._entries:
            self.close(dict(self._entries[self._selected], context=self._context))

    def delete(self):
        if self._entries:
            self._remove({"id": self._entries[self._selected]["id"]})

    def _remove(self, payload):
        try:
            response = self._service.delete_playback_history(payload)
            self._entries = response.get("videos", [])
            self._context = response.get("context", "")
            self.move(0)
        except Exception:
            self["status"].setText(_("Settings could not be saved. Check the receiver storage."))

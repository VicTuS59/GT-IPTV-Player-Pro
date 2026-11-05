# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-native, account-independent YouTube search and playback."""

import threading
import time
from collections import OrderedDict, deque
from .ui_update import set_visible
from .typography import set_text_if_changed

from .auxiliary_presentation import (
    skin_after as aux_skin_after, refresh_after as aux_refresh_after, setup as setup_aux,
    paint_youtube_row, prepare_youtube_results, refresh_history, refresh_history_selection, text as aux_text,
    youtube_preview_after,
)
from .utility_pages import skin_after, refresh_after, setup as setup_utility_pages
from .vod_grid import LatestPosterWorkers

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
try:
    from Components.VideoWindow import VideoWindow
except ImportError:
    VideoWindow = None
from Screens.Screen import Screen
from enigma import eTimer

from .background import attach_pixmap
from .i18n import _, N_, device_language
from .main import _connect_timer, _scale
from .remote_footer import prepared_skin
from .remote_footer import decorate_remote_footer, footer_item, install_remote_footer, set_remote_footer
from .settings import load_player_settings, save_player_settings
from .i18n import supported_language_names
from .typography import ellipsize_dynamic_text, fit_dynamic_text, font_px
from .web_youtube import WEB_YOUTUBE
from .youtube_options import YOUTUBE_QUALITIES, DEFAULT_YOUTUBE_QUALITY, quality_label
from .youtube_preview import YouTubePreviewMixin
from .youtube_startup import youtube_error_message, RETRY_MESSAGE
from .youtube_skin import youtube_search_skin
from .stream_resolution import resolution_text


ROWS = 5
HISTORY_ROWS = 7
ARTWORK_SETTLE_MS = 230
RESULT_REFIT_STEP_MS = 35
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


@skin_after('youtube')
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
        setup_utility_pages(self, 'youtube', SETTINGS_FOOTER)
        self._refresh()
        self.setTitle(_("YouTube settings"))

    @refresh_after
    def _refresh(self):
        values = (
            quality_label(self.settings.youtube_resolution),
            _("On") if self.settings.youtube_autoplay else _("Off"),
            _("Automatic") + " · " + dict(supported_language_names()).get(device_language(), "English"),
        )
        for index, value in enumerate(values):
            set_text_if_changed(self["value_{}".format(index)], value)
            set_visible(self["focus_{}".format(index)], True) if index == self.selected_index else set_visible(self["focus_{}".format(index)], False)

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
            shared.youtube_audio_preference = "original"
            saved = save_player_settings(shared)
        except Exception:
            saved = False
        if not saved:
            set_text_if_changed(self["message"], _("Settings could not be saved. Check the receiver storage."))
            return
        self.close({name: getattr(self.settings, name) for name in (
            "youtube_resolution", "youtube_autoplay")})

    def cancel(self):
        self.close(None)


@aux_skin_after('history' )
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

    RESULTS_FOOTER = FOOTER

    def __init__(self, session, search_service=None):
        self.skin = prepared_skin(_skin)
        Screen.__init__(self, session)
        self._service = search_service if search_service is not None else WEB_YOUTUBE
        self._closed = False
        self._query = ""
        self._quality = str(load_player_settings().youtube_resolution)
        self._pages = OrderedDict()
        self._page_cursors = OrderedDict([(1, "")])
        self._highest_page = 0
        self._page_expired = False
        self._showing_recent_search = False
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
        self._search_token = ""
        self._search_page = 1
        self._search_signature = None
        self._prefetch = None
        self._thumb_cancel = None
        self._thumb_workers = None
        self._thumb_key = ()
        self._thumb_epoch = 0
        self._thumb_active = False
        self._last_navigation = 0.0
        self._result_refit_slot = None
        self._result_preview_pending = False
        self._history_artwork = {}
        self._keyboard_open = False
        self._keyboard_requested = False
        self._poll_timer = eTimer()
        self._keyboard_timer = eTimer()
        self._thumb_timer = eTimer()
        self._result_settle_timer = eTimer()
        _connect_timer(self._poll_timer, self._poll)
        _connect_timer(self._keyboard_timer, self._open_initial_search)
        _connect_timer(self._thumb_timer, self._start_thumbnails)
        _connect_timer(self._result_settle_timer, self._settle_result_navigation)
        self._init_youtube_preview(VideoWindow is not None)

        for name in ("video_guard", "header", "header_divider", "youtube_brand", "quality", "search_text", "section",
                     "pages", "status", "footer", "preview_mask", "preview_badge",
                     "preview_title", "preview_detail", "preview_action", "preview_live", "preview_resolution"):
            self[name] = Label("")
        for name in ("quality_frame", "search_frame", "search_icon", "preview_panel", "preview_art", "preview_live_bg", "youtube_logo", "footer_frame"):
            self[name] = Pixmap()
        width, height, _px = _scale()
        self["preview_video"] = (VideoWindow(decoder=0, fb_width=width, fb_height=height)
                                 if VideoWindow is not None else Label(""))
        set_visible(self["preview_video"], False)
        set_visible(self["preview_art"], False)
        for index in range(ROWS):
            for name in ("focus", "row"):
                self["{}_{}".format(name, index)] = Pixmap()
            self["live_bg_{}".format(index)] = Pixmap()
            for name in ("title", "detail", "duration"):
                self["{}_{}".format(name, index)] = Label("")
            self["thumb_{}".format(index)] = Pixmap()
            set_visible(self["thumb_{}".format(index)], False)
        set_text_if_changed(self["header"], "GT IPTV PLAYER PRO")
        set_text_if_changed(self["header_divider"], "|")
        set_text_if_changed(self["youtube_brand"], "YouTube")
        install_remote_footer(self, self.RESULTS_FOOTER, fit_horizontal=True)
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

        setup_aux(self, 'youtube', self.RESULTS_FOOTER)

    def _cover_live_video(self):
        set_visible(self["video_guard"], True)
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
        if self._thumb_workers is not None:
            self._thumb_workers.close()
        if self._play_token:
            self._service.cancel_play(self._play_token)
        self._poll_timer.stop()
        self._keyboard_timer.stop()
        self._thumb_timer.stop()
        self._result_settle_timer.stop()
        with self._event_lock:
            self._events.clear()
        self._pages.clear()
        self._page_cursors.clear()
        self._preview_records.clear()
        self._thumbnail_paths.clear()

    def _open_initial_search(self):
        self.open_history()

    def open_history(self):
        if self._closed or self._keyboard_open:
            return
        self._history_fullscreen_id = ""
        self.session.openWithCallback(self._history_selected, GTYouTubeHistoryScreen,
                                      self._service, self._history_artwork)

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
                self._set_status(_("The virtual keyboard is unavailable on this image."))
                return
        opener = getattr(self.session, "openWithCallback", None)
        if not callable(opener):
            self._set_status(_("Could not open the virtual keyboard."))
            return
        self._keyboard_open = True
        try:
            opener(self._query_entered, VirtualKeyBoard, title="YouTube " + _("Search"),
                   text=self._query)
        except Exception:
            self._keyboard_open = False
            self._set_status(_("Could not open the virtual keyboard."))

    def _query_entered(self, value=None):
        self._keyboard_open = False
        if self._closed or value is None:
            return
        query = str(value).strip()[:120]
        if len(query) < 2:
            self._set_status(_("Search") + " · 2+")
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
        self._page_cursors = OrderedDict([(1, "")])
        self._highest_page = 0
        self._page_expired = False
        protected = getattr(getattr(self, "current_item", None), "stream_id", "")
        self._preview_records = {key: value for key, value in self._preview_records.items() if key == protected}
        self._thumbnail_paths = {key: value for key, value in self._thumbnail_paths.items() if key == protected}
        self._page, self._selected, self._top = 1, 0, 0
        cached = getattr(self._service, "recent_search", None)
        recent = cached(query) if callable(cached) else None
        self._showing_recent_search = bool(recent)
        if recent:
            self._cache_page(1, recent)
        self._request_page(1, refresh=bool(recent))
        self._render()

    def _cache_page(self, page, response):
        self._pages[page] = response
        self._highest_page = max(self._highest_page, page)
        if response.get("has_more") and response.get("cursor"):
            self._page_cursors[page + 1] = response["cursor"]
            self._page_cursors.move_to_end(page + 1)
        elif not response.get("pending"):
            self._page_cursors.pop(page + 1, None)
        while len(self._pages) > 12:
            victim = max((key for key in self._pages if key != page), key=lambda key: abs(key - page))
            self._pages.pop(victim, None)
        while len(self._page_cursors) > 240:
            victim = next(key for key in self._page_cursors if key != 1)
            self._page_cursors.pop(victim, None)

    def _trim_preview_records(self):
        protected = {entry.get("id") for entry in self._visible()}
        protected.add(getattr(getattr(self, "current_item", None), "stream_id", ""))
        protected.add(self._pending_play_id)
        for values in (self._preview_records, self._thumbnail_paths):
            for key in list(values):
                if len(values) <= 240:
                    break
                if key not in protected:
                    values.pop(key, None)

    def _request_page(self, page, *, refresh=False):
        if self._closed or self._busy or not self._query:
            return
        self._history_fullscreen_id = ""
        self._sync_quality()
        if page in self._pages and not refresh:
            if page != self._page:
                self._last_navigation = time.monotonic()
            self._page, self._selected, self._top = page, 0, 0
            self._render()
            self._prepare_next_page()
            return
        if page < 1 or page > self._highest_page + 1:
            return
        previous = self._pages.get(page - 1) if page > 1 else None
        cursor = self._page_cursors.get(page)
        if cursor is None and previous is not None and previous.get("has_more"):
            cursor = previous.get("cursor")
        if cursor is None or (page > 1 and not cursor):
            if page <= self._highest_page:
                self._page_expired = True
                self._set_status(_("Request timed out") + " · MENU · " + _("Retry"))
            return
        self._page_expired = False
        if page in self._page_cursors:
            self._page_cursors.move_to_end(page)
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
        self._set_status(_("Loading data... Please wait."))
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
                self._mark_recent_search_error()
                self._set_status(_("Could not load content") + " · MENU · " + _("Retry"))
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
        if self._search_page == 1 and self._showing_recent_search:
            if pending:
                return
            if error:
                self._busy = False
                self._search_token = ""
                self._mark_recent_search_error(error)
                self._set_status(_("Could not load content") + " · MENU · " + _("Retry"))
                return
            self._showing_recent_search = False
        entries = response.get("results") or []
        signature = (tuple(entry.get("id") for entry in entries), pending, error,
                     response.get("cursor"), response.get("pagination_warning"))
        if signature == self._search_signature:
            return
        self._search_signature = signature
        previous = (self._pages.get(self._search_page) or {}).get("results") or []
        selected_id = previous[self._selected].get("id") if self._selected < len(previous) else ""
        self._busy = pending
        self._cache_page(self._search_page, response)
        self._page = self._search_page
        self._selected = next((index for index, entry in enumerate(entries)
                               if entry.get("id") == selected_id), 0)
        self._top = min(self._top, self._selected)
        if self._selected >= self._top + ROWS:
            self._top = self._selected - ROWS + 1
        self._render()
        self._set_status(
            _("Loading data... Please wait.") if pending else
            _("Could not load content") + " · MENU · " + _("Retry") if error or response.get("pagination_warning") else
            "" if entries else _("No results found"))
        if not pending:
            self._search_token = ""
            self._prepare_next_page()

    def _mark_recent_search_error(self, error="youtube_unavailable"):
        if self._showing_recent_search:
            self._pages[1] = dict(self._pages[1], error=error or "youtube_unavailable")
            self._showing_recent_search = False

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
                self._mark_recent_search_error()
                if getattr(exc, "code", "") == "youtube_quality_changed":
                    self._query_entered(self._query)
                else:
                    self._set_status(_("Could not load content") + " · MENU · " + _("Retry"))
        self._poll_prefetch()
        with self._event_lock:
            events = list(self._events)
            self._events.clear()
        changed_art = set()
        deferred_art = []
        navigating = time.monotonic() - self._last_navigation < ARTWORK_SETTLE_MS / 1000
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
                    self._mark_recent_search_error(error)
                    self._set_status(_("Could not load content") + " · MENU · " + _("Retry"))
                    continue
                self._showing_recent_search = False
                self._cache_page(page, response)
                self._page, self._selected, self._top = page, 0, 0
                self._set_status("")
                self._render()
            elif event[0] == "thumb":
                unused_kind, epoch, index, video_id, path = event
                if epoch == self._thumb_epoch and index < len(self._thumb_key) and path:
                    if self._thumb_key[index] == video_id:
                        if navigating or len(changed_art) >= 2:
                            deferred_art.append(event)
                            continue
                        self._thumbnail_paths.pop(video_id, None)
                        self._thumbnail_paths[video_id] = path
                        attach_pixmap(self, "thumb_{}".format(index), path, cover_ratio=(16, 9))
                        changed_art.add(video_id)
            elif event[0] == "thumb_done":
                if event[1] == self._thumb_epoch:
                    self._thumb_active = False
        if changed_art:
            self._trim_preview_records()
            entries = (self._pages.get(self._page) or {}).get("results") or []
            if (self._selected < len(entries) and entries[self._selected].get("id") in changed_art
                    and not self._result_preview_pending and not self._preview_owns_service()):
                self._render_preview()
        if deferred_art:
            with self._event_lock:
                self._events.extendleft(reversed(deferred_art))
        if self._play_token:
            job = {}
            try:
                job = self._service.status(self._play_token).get("job") or {}
                state = job.get("state")
            except Exception:
                state = "failed"
            if state == "resolving" and job.get("attempt", 0):
                self._set_status(_(RETRY_MESSAGE))
            if state != "resolving":
                self._play_token = ""
                self._pending_play_id = ""
                if state in ("failed", "cancelled", None):
                    self._set_status(
                        (_("No videos found at the selected quality.") if job.get("error") == "youtube_quality_unavailable"
                         else _("Quality changed. Search again.") if job.get("error") == "youtube_quality_changed"
                         else youtube_error_message(job.get("error")))
                    )
                else:
                    # The right preview pane already reports actual resolution.
                    # A successful start must not place quality over list rows.
                    self._set_status("")
        if self._history_fullscreen_id and not self._play_token:
            video_id = self._history_fullscreen_id
            self._history_fullscreen_id = ""
            if self._preview_owns_service() and self.current_item.stream_id == video_id:
                # History opens in fullscreen after the resolver has committed
                # playback. Transfer any pending bookmark seek to that player.
                self._open_youtube_fullscreen(allow_pending_resume=True)
        self._poll_timer.start(100 if self._busy or self._thumb_active or self._play_token or deferred_art else
                               500 if self._prefetch and self._prefetch["token"] else 1000, True)

    def _visible(self):
        response = self._pages.get(self._page) or {}
        return (response.get("results") or [])[self._top:self._top + ROWS]

    def _hide_result_image(self, name):
        decoder = getattr(self, "_gt_pixmap_loaders", {}).pop(name, None)
        if decoder is not None:
            decoder.close()
        set_visible(self[name], False)

    def _load_thumbnails(self, visible, delay=0):
        key = tuple(item.get("id", "") for item in visible)
        if key == self._thumb_key:
            return
        if self._thumb_cancel is not None:
            self._thumb_cancel.set()
        previous = self._thumb_key
        self._thumb_key = key
        self._thumb_epoch += 1
        self._thumb_active = False
        self._thumb_timer.stop()
        for index in range(ROWS):
            if index >= len(key) or index >= len(previous) or key[index] != previous[index]:
                self._hide_result_image("thumb_{}".format(index))
        if not any(item.get("thumbnail") for item in visible):
            return
        if delay:
            self._thumb_timer.start(delay, True)
        else:
            self._start_thumbnails()

    def _start_thumbnails(self):
        if self._closed:
            return
        remaining = ARTWORK_SETTLE_MS - (time.monotonic() - self._last_navigation) * 1000
        if remaining > 0:
            self._thumb_timer.start(max(1, round(remaining)), True)
            return
        visible = self._visible()
        records = [(index, item.get("id", ""), item.get("thumbnail", ""))
                   for index, item in enumerate(visible)
                   if item.get("thumbnail") and not getattr(self["thumb_{}".format(index)], "visible", False)]
        if not records:
            return
        epoch = self._thumb_epoch
        from .browser import _WorkerToken
        token = _WorkerToken(max(12, len(records) * 5))
        cancel = token.cancel_event
        self._thumb_cancel = cancel
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
                            url, _picon_cache_path(url), timeout=4, token=token)
                    except Exception:
                        path = ""
                    if path and not cancel.is_set():
                        with mailbox_lock:
                            mailbox.append(("thumb", epoch, index, video_id, path))
            finally:
                with mailbox_lock:
                    mailbox.append(("thumb_done", epoch))

        if self._thumb_workers is None:
            self._thumb_workers = LatestPosterWorkers(worker_count=1)
        self._thumb_workers.replace([work])
        self._poll_timer.start(100, True)

    @aux_refresh_after
    def _render(self):
        if self._closed:
            return
        set_text_if_changed(self["quality"], quality_label(self._quality))
        ellipsize_dynamic_text(self["search_text"], self._query or (_("Search") + "…"),
                               fallback_chars=58)
        set_text_if_changed(self["section"], _("Search") + " · YouTube")
        response = self._pages.get(self._page) or {}
        entries = response.get("results") or []
        visible = self._visible()
        for item in entries:
            if item.get("id"):
                self._preview_records.pop(item["id"], None)
                self._preview_records[item["id"]] = item
        self._trim_preview_records()
        delay = ARTWORK_SETTLE_MS if time.monotonic() - self._last_navigation < ARTWORK_SETTLE_MS / 1000 else 0
        self._render_result_rows(visible,thumbnail_delay=delay,cached_only=bool(delay))
        if delay:
            self._schedule_result_settle(refit_rows=True)
        else:
            self._result_settle_timer.stop()
            self._result_refit_slot = None
            self._result_preview_pending = False
            self._render_preview()
        if not self._query:
            set_text_if_changed(self["pages"], "")
            return
        last = self._highest_page
        first = max(1, min(self._page - 2, max(1, last - 3)))
        numbers = []
        for page in range(first, min(last + 2, first + 5)):
            if page > last and not response.get("has_more"):
                break
            numbers.append("[{}]".format(page) if page == self._page else str(page))
        if response.get("has_more"):
            numbers.append("…  ›")
        set_text_if_changed(self["pages"], _("Page") + "  " + "   ".join(numbers))
        if response.get("pagination_warning") == "youtube_unavailable" and not self._busy:
            self._set_status(_("Could not load content") + " · MENU · " + _("Retry"))
        elif not entries and not self._busy and response:
            self._set_status(_("No results found"))

    def _render_result_rows(self, visible, thumbnail_delay=0, *, cached_only=False):
        if visible and getattr(self, "_aux_kind", None) == "youtube":
            prepare_youtube_results(self)
        for index in range(ROWS):
            item = visible[index] if index < len(visible) else {}
            self._render_result_row(index,item,cached_only=cached_only)
        self._load_thumbnails(visible,thumbnail_delay)

    def _render_result_row(self, index, item, *, cached_only=False):
        paint_youtube_row(self,index,bool(item),self._top + index == self._selected)
        live = bool(item.get("live"))
        set_visible(self["live_bg_{}".format(index)], live)
        detail = "  •  ".join(value for value in
                               (item.get("channel", ""), item.get("published", "")) if value)
        duration = _("Live") if live else str(item.get("duration", ""))[:20]
        title_name, detail_name, duration_name = ("{}_{}".format(name,index)
                                                  for name in ("title","detail","duration"))
        if getattr(self, "_aux_kind", None) == "youtube":
            aux_text(self,title_name,item.get("title",""),31,2,cached_only=cached_only)
            aux_text(self,detail_name,detail,24,cached_only=cached_only)
            aux_text(self,duration_name,duration,25,2,cached_only=cached_only)
        else:
            fit_dynamic_text(self[title_name],item.get("title",""),max_lines=2,fallback_chars=70,
                             cached_only=cached_only)
            fit_dynamic_text(self[detail_name],detail,max_lines=1,fallback_chars=76,cached_only=cached_only)
            self._fit_youtube_caption(duration_name,duration,22,minimum=11,max_lines=2 if live else 1,
                                      cached_only=cached_only)

    def _schedule_result_settle(self, *, refit_rows=False):
        if refit_rows:
            self._result_refit_slot = 0
        self._result_preview_pending = True
        self._result_settle_timer.start(ARTWORK_SETTLE_MS, True)

    def _settle_result_navigation(self):
        if self._closed or not getattr(self, "execing", True):
            return
        remaining = ARTWORK_SETTLE_MS - (time.monotonic() - self._last_navigation) * 1000
        if remaining > 0:
            self._result_settle_timer.start(max(1, round(remaining)), True)
            return
        if self._result_preview_pending:
            self._result_preview_pending = False
            self._render_preview()
        elif self._result_refit_slot is not None:
            visible = self._visible()
            slot = self._result_refit_slot
            if slot < len(visible):
                self._render_result_row(slot, visible[slot])
            self._result_refit_slot = slot + 1 if slot + 1 < len(visible) else None
        if self._result_refit_slot is not None:
            self._result_settle_timer.start(RESULT_REFIT_STEP_MS, True)

    def move(self, step):
        if self._closed or self._keyboard_open:
            return
        entries = (self._pages.get(self._page) or {}).get("results") or []
        if not entries:
            return
        self._history_fullscreen_id = ""
        previous, previous_top = self._selected, self._top
        self._last_navigation = time.monotonic()
        self._selected = max(0, min(len(entries) - 1, self._selected + step))
        if self._selected == previous:
            return
        if self._selected < self._top:
            self._top = self._selected
        elif self._selected >= self._top + ROWS:
            self._top = self._selected - ROWS + 1
        moved_window = self._top != previous_top
        if moved_window:
            self._render_result_rows(self._visible(),thumbnail_delay=ARTWORK_SETTLE_MS,cached_only=True)
        else:
            paint_youtube_row(self,previous-self._top,True,False)
            paint_youtube_row(self,self._selected-self._top,True,True)
        self._schedule_result_settle(refit_rows=moved_window)

    def previous_page(self):
        if not self._busy and self._page > 1:
            self._request_page(self._page - 1)

    def next_page(self):
        if not self._busy:
            self._request_page(self._page + 1)

    def jump_page(self, page):
        if not self._busy and 1 <= page <= self._highest_page + 1:
            self._request_page(page)

    def retry(self):
        if not self._busy and self._query:
            current = self._pages.get(self._page) or {}
            if current.get("error") or current.get("pending") or self._page_expired:
                self._query_entered(self._query)
            else:
                self._request_page(self._highest_page + 1)

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
            self._set_status(_("Could not start the player."))
            return
        video_id = video.get("id", "")
        pending_same_video = bool(self._play_token and video_id == self._pending_play_id)
        if ((not self._play_token or pending_same_video) and self._preview_owns_service()
                and self.current_item.stream_id == video_id
                and str(self.current_item.youtube_requested_quality) == self._quality):
            self._open_youtube_fullscreen()
            return
        if pending_same_video:
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
            self._set_status(_("Could not start the player."))
            return
        self._set_status(_("Please wait"))
        self._poll_timer.start(100, True)


    def _set_status(self, value):
        ellipsize_dynamic_text(self["status"], value, fallback_chars=64)

    def _fit_youtube_caption(self, name, text, size, minimum=12, max_lines=1, *, cached_only=False):
        _width, _height, px = _scale()
        fit_dynamic_text(self[name], text, max_lines=max_lines,
                         preferred_size=max(minimum, font_px(px, size)),
                         min_size=max(5, px(10)), cached_only=cached_only)

    @youtube_preview_after
    def _render_preview(self):
        if self._closed:
            return
        playing = self._preview_owns_service()
        entries = (self._pages.get(self._page) or {}).get("results") or []
        selected = entries[self._selected] if self._selected < len(entries) else {}
        video = (self._preview_records.get(self.current_item.stream_id,
                 {"id": self.current_item.stream_id, "title": self.current_item.name})
                 if playing else selected)
        if getattr(self, "_aux_kind", None) != "youtube":
            fit_dynamic_text(self["preview_title"], video.get("title", ""), max_lines=3, fallback_chars=90)
            fit_dynamic_text(self["preview_detail"],
                             " · ".join(str(video.get(field, "")) for field in ("channel", "published") if video.get(field)),
                             max_lines=2, fallback_chars=54)
        badge = (_("Live") if video.get("live") else _("Playing") if playing else _("Preview"))
        self._fit_youtube_caption("preview_badge", badge if video else _("YouTube"), 22)
        live = bool(video.get("live"))
        self._fit_youtube_caption("preview_live", _("Live"), 22, max_lines=2)
        for name in ("preview_live_bg", "preview_live"):
            set_visible(self[name], True) if live else set_visible(self[name], False)
        set_visible(self["preview_badge"], False) if live else set_visible(self["preview_badge"], True)
        self._refresh_youtube_resolution()
        fullscreen = bool(playing and (not selected or selected.get("id") == self.current_item.stream_id)
                          and str(self.current_item.youtube_requested_quality) == self._quality)
        self._fit_youtube_caption("preview_action", "OK · " + (_("Full screen") if fullscreen else _("Play")), 26)
        items = list(self.RESULTS_FOOTER)
        items[2] = footer_item("ok", "fullscreen" if fullscreen else "play")
        if tuple(items) != getattr(self, "_remote_footer_items", None):
            set_remote_footer(self, items)
        if playing:
            if self._preview_art_key is not None:
                self._preview_art_key = self._preview_art_loaded_key = None
                self._hide_result_image("preview_art")
            set_visible(self["preview_mask"], False)
            return
        set_visible(self["preview_video"], False)
        set_visible(self["preview_mask"], True)
        key = (video.get("id", ""), self._thumbnail_paths.get(video.get("id", ""), ""))
        if key != self._preview_art_key:
            self._preview_art_key = key
            self._preview_art_loaded_key = None
            self._hide_result_image("preview_art")
            if key[1]:
                self._start_preview_art()
        elif key[1] and self._preview_art_loaded_key == key:
            set_visible(self["preview_art"], True)

    def _refresh_youtube_resolution(self):
        if self._closed or self._fullscreen_active or not self._preview_owns_service():
            set_text_if_changed(self["preview_resolution"], "")
            set_visible(self["preview_resolution"], False)
            return
        snapshot = self._youtube_resolution_snapshot()
        text = resolution_text(snapshot)
        self._fit_youtube_caption("preview_resolution", text if text != "--" else _("Resolution: --"), 22)
        set_visible(self["preview_resolution"], True)

    def _start_preview_art(self):
        key = self._preview_art_key
        if self._closed or not key or not key[1] or self._preview_owns_service():
            return
        attach_pixmap(self, "preview_art", key[1], cover_ratio=(16, 9), show_on_load=False,
                      on_loaded=lambda loaded: self._preview_art_loaded(key, loaded))

    def _preview_art_loaded(self, key, loaded):
        if loaded and not self._closed and key == self._preview_art_key and not self._preview_owns_service():
            self._preview_art_loaded_key = key
            set_visible(self["preview_art"], True)

    def change_quality(self):
        if self._closed:
            return
        settings = load_player_settings().copy()
        current = str(settings.youtube_resolution)
        settings.youtube_resolution = QUALITY[(QUALITY.index(current) + 1) % len(QUALITY)] if current in QUALITY else DEFAULT_YOUTUBE_QUALITY
        if save_player_settings(settings):
            self._sync_quality()
            if not self._query:
                self._set_status(_("Quality") + " · " + quality_label(settings.youtube_resolution))
        else:
            self._set_status(_("Settings could not be saved. Check the receiver storage."))



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

    def __init__(self, session, service=None, artwork_cache=None):
        self.skin = _history_skin(self.HISTORY_FOOTER).replace('name="GTYouTubeSearchScreen"',
                                                     'name="{}"'.format(type(self).__name__))
        Screen.__init__(self, session)
        self._service = service if service is not None else WEB_YOUTUBE
        self._history_artwork = {} if artwork_cache is None else artwork_cache
        self._entries = self._load_history()
        self._selected = self._top = 0
        for name in ("video_guard", "header", "quality", "search_frame", "search_body",
                     "search_text", "section", "pages", "status", "footer"):
            self[name] = Label("")
        set_text_if_changed(self["header"], "GT IPTV PLAYER PRO  |  YouTube")
        set_text_if_changed(self["search_text"], _(self.HISTORY_TITLE))
        set_text_if_changed(self["section"], _(self.HISTORY_TITLE))
        for index in range(HISTORY_ROWS):
            for name in ("focus", "row", "title", "detail", "duration"):
                self["{}_{}".format(name, index)] = Label("")
            self["thumb_{}".format(index)] = Pixmap()
            set_visible(self["thumb_{}".format(index)], False)
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

        setup_aux(self, 'history', self.HISTORY_FOOTER, artwork_cache=self._history_artwork)

    def _load_history(self):
        return self._service.history().get("queries", [])

    def open_playback_history(self):
        self.session.openWithCallback(self._playback_history_selected,
                                      GTYouTubePlaybackHistoryScreen, self._service,
                                      self._history_artwork)

    def _playback_history_selected(self, video=None):
        if isinstance(video, dict):
            self.close(video)

    def move(self, step):
        previous, previous_top = self._selected, self._top
        self._selected = max(0, min(len(self._entries) - 1, self._selected + step))
        if step and self._selected == previous:
            return
        if self._selected < self._top:
            self._top = self._selected
        elif self._selected >= self._top + HISTORY_ROWS:
            self._top = self._selected - HISTORY_ROWS + 1
        if not step or getattr(self, "_aux_kind", None) != "history":
            self._render()
            return
        self._render_history_position()
        if self._top != previous_top:
            self._render_history_rows()
            refresh_history(self)
        else:
            refresh_history_selection(self, previous)

    @aux_refresh_after
    def _render(self):
        set_text_if_changed(self["quality"], quality_label(load_player_settings().youtube_resolution))
        self._render_history_rows()
        self._render_history_position()
        set_text_if_changed(self["status"], "" if self._entries else _("No content found."))

    def _render_history_rows(self):
        for index in range(HISTORY_ROWS):
            position = self._top + index
            entry = self._entries[position] if position < len(self._entries) else None
            if getattr(self, '_aux_kind', None) != 'history':
                value = entry.get("title", "") if isinstance(entry, dict) else entry or ""
                ellipsize_dynamic_text(self["title_{}".format(index)], value, fallback_chars=65)
                set_visible(self["row_{}".format(index)], bool(value))
                set_visible(self["focus_{}".format(index)], bool(value and position == self._selected))
            if isinstance(entry, dict):
                set_text_if_changed(self["detail_{}".format(index)], quality_label(entry["quality"]) if entry.get("quality") else "")
                duration = int(entry.get("duration") or 0)
                set_text_if_changed(self["duration_{}".format(index)], "{}:{:02d}".format(duration // 60, duration % 60) if duration else "")
            else:
                set_text_if_changed(self["detail_{}".format(index)], "")
                set_text_if_changed(self["duration_{}".format(index)], "")
    def _render_history_position(self):
        set_text_if_changed(self["pages"], "{} / {}".format(self._selected + 1, len(self._entries)) if self._entries else "")

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
            set_text_if_changed(self["status"], _("Settings could not be saved. Check the receiver storage."))


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
            set_text_if_changed(self["status"], _("Settings could not be saved. Check the receiver storage."))

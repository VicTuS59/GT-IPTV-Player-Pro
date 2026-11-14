# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native TMDb discovery for movies and series."""

import threading
import time
from collections import OrderedDict, deque
from .ui_update import set_visible
from .typography import set_text_if_changed

from .vod_grid import LatestPosterWorkers

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from enigma import eTimer

from .background import attach_pixmap
from .discovery_layout import _units, skin as discovery_skin
from .browser import (
    _WorkerToken, _WorkerCancelled,
    PICON_DOWNLOAD_TIMEOUT,
    _cached_picon_path, _cinematic_cover_ratio, _client_supports,
    _download_picon, _picon_cache_path, _valid_cinematic_backdrop_file, content_client_for,
)
from .artwork import RETRY_DELAY_SECONDS
from .category_preferences import LiveCategoryPreferenceStore, category_preferences_path
from .category_visibility import category_visibility_ids
from .content import ContentError
from .diagnostics import log_event
from .discovery import TMDbDiscoveryClient, DiscoverySearchClient
from .i18n import N_, _, localized_upper, metadata_language, ngettext
from .main import _connect_timer, _scale
from .metadata import MetadataError
from .paths import plugin_path
from .playback import resume_account_scope
from .remote_footer import prepared_skin
from .remote_footer import footer_item, remote_action_text, web_shortcut_hint_text
from .settings import load_player_settings
from .source_loader import SourceLoader
from .typography import ellipsize_dynamic_text, fit_dynamic_text, font_px


MEDIA_TABS = (("movie", N_("Movies")), ("tv", N_("Series")))
FILTERS = (("popular", N_("Popular")), ("top", N_("Top Rated")),
           ("new", N_("New Releases")), ("genres", N_("Genres")))
LOADING = N_("Loading data... Please wait.")
KEY_REQUIRED = N_("TMDb API key is not configured")
HINTS = {"movie": N_("Choose a movie, then search in IPTV."),
         "tv": N_("Choose a series, then search in IPTV.")}
SIMILAR = {"movie": N_("Similar Movies"), "tv": N_("Similar Series")}
INFO = {"movie": N_("Movie information"), "tv": N_("Series information")}
SUMMARY_UNAVAILABLE = {"movie": N_("Movie summary unavailable."), "tv": N_("Series summary unavailable.")}
EMPTY = {"movie": N_("No movies found."), "tv": N_("No series found.")}
FOOTER = (
    footer_item("ok", INFO["movie"]),
    footer_item("green", N_("Search in IPTV"), 1.15),
    footer_item("yellow", N_("Genres")),
    footer_item("blue", SIMILAR["movie"]),
    footer_item("play", N_("Trailer")),
    footer_item("menu", N_("Menu")),
    footer_item("exit", "back"),
)


def _asset(name):
    return plugin_path("skin", "images", "discovery-r48", name)


def _skin():
    width, height, px = _scale()
    captions = [remote_action_text(item[1]) for item in FOOTER]
    captions[0] = max((captions[0], _(INFO["tv"])), key=_units)
    captions[3] = max((captions[3], _(SIMILAR["tv"])), key=_units)
    return discovery_skin(width, height, px,
                          captions,
                          [item[0] for item in FOOTER], font_px, _asset)


class GTMovieDiscoveryScreen(Screen):
    def __init__(self, session, account, content_client=None):
        self.skin = prepared_skin(_skin)
        Screen.__init__(self, session)
        from .skin_artwork import install as install_skin_artwork
        install_skin_artwork(self)
        self.account = account
        self._provider = content_client
        self._client = None
        self._closed = False
        self._child_open = False
        self._started = False
        self._busy = False
        self._media_type = "movie"
        self._entries = []
        self._selected_index = 0
        self._page = self._pages = 1
        self._section = "popular"
        self._genre_id = 0
        self._genre_name = ""
        self._similar_id = 0
        self._similar_title = ""
        self._tab_index = 0
        self._tabs_focused = False
        self._history = []
        self._details = OrderedDict()
        self._jobs = {}
        self._events = deque()
        self._event_lock = threading.Lock()
        self._worker_slots = threading.BoundedSemaphore(3)
        self._poster_slots = threading.BoundedSemaphore(2)
        self._channel_workers = {}
        self._visible_ids = ()
        self._hero_identity = None
        self._hero_request = None
        self._hero_generation = 0
        self._pending_index = 0
        self._youtube = None
        self._play_token = ""
        self._play_deadline = 0
        self._poll_timer = eTimer()
        self._start_timer = eTimer()
        self._detail_timer = eTimer()
        self._hero_timer = eTimer()
        _connect_timer(self._poll_timer, self._poll)
        _connect_timer(self._start_timer, self._begin)
        _connect_timer(self._detail_timer, self._load_selected_detail)
        _connect_timer(self._hero_timer, self._start_selected_hero)
        self._delivery = SourceLoader(eTimer(), _connect_timer, self._wake_workers)
        self._delivery_generation = None

        for name in ("backdrop", "hero_fallback", "scrim", "hero_bottom", "hero_top", "tmdb_logo"):
            self[name] = Pixmap()
        # Alpha masks are loaded by the skin, never by ePicLoad.
        for name in ("backdrop", "hero_fallback", "scrim", "hero_bottom", "hero_top"):
            set_visible(self[name], False)
        for name in ("video_guard", "app_bg", "header", "header_divider", "page_title", "tabs_divider",
                     "tmdb_wordmark", "section", "title", "original", "rating_star", "rating",
                     "meta", "genres", "director", "plot", "status", "busy", "counter",
                     "footer", "footer_line", "guide_divider", "web_hint"):
            self[name] = Label("")
        for index in range(len(MEDIA_TABS) + len(FILTERS)):
            for prefix in ("tab", "tab_selected", "tab_focus"):
                self["{}_{}".format(prefix, index)] = Label("")
        for index in range(6):
            for prefix in ("focus", "empty", "name", "marker"):
                self["{}_{}".format(prefix, index)] = Label("")
            self["poster_{}".format(index)] = Pixmap()
        for index, (key, action, weight) in enumerate(FOOTER):
            if key in ("green", "yellow", "blue"):
                self["guide_dot_{}".format(index)] = Label("●")
            else:
                self["guide_bg_{}".format(index)] = Pixmap()
                self["guide_key_{}".format(index)] = Label(key.upper())
            self["guide_action_{}".format(index)] = Label("")
        set_text_if_changed(self["title"], _("Discover"))
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions", "MenuActions",
             "ChannelSelectBaseActions", "InfobarChannelSelection"],
            {"ok": self.open_selected, "cancel": self.go_back, "back": self.go_back,
             "red": self.go_back, "left": lambda: self.move(-1), "right": lambda: self.move(1),
             "leftRepeated": lambda: self.move(-1), "rightRepeated": lambda: self.move(1),
             "up": lambda: self.focus_tabs(True), "down": lambda: self.focus_tabs(False),
             "green": self.search_iptv, "yellow": self.choose_genre, "blue": self.similar,
             "menu": self.open_menu,
             "nextBouquet": lambda: self.move(6), "prevBouquet": lambda: self.move(-6),
             "channelUp": lambda: self.move(-6), "channelDown": lambda: self.move(6)}, -1)
        try:
            from .series_trailer_ui import PLAY_KEY_CONTEXT, _ensure_play_keys
            _ensure_play_keys()
            self["trailer_actions"] = ActionMap([PLAY_KEY_CONTEXT], {"playTrailer": self.play_trailer}, -1)
        except Exception as error:
            log_event("discovery", "Could not bind trailer play key", error)
        self.onShown.append(self._shown)
        self.onLayoutFinish.append(self._layout_ready)
        self.onClose.append(self._stop)
        self.setTitle(_("Discover"))
        self._render()

    def _layout_ready(self):
        from .remote_footer import bind_layout_text
        bind_layout_text(self)
        # Hiding a component before its native instance exists is insufficient
        # on some images; initialise visibility again after the skin is built.
        set_visible(self["video_guard"], True)
        set_visible(self["backdrop"], False)
        set_visible(self["hero_fallback"], False)
        set_visible(self["scrim"], False)
        set_visible(self["hero_bottom"], False)
        set_visible(self["hero_top"], False)
        self._refresh_chrome()
        self._render()

    def _refresh_chrome(self):
        unused_width, unused_height, px = _scale()
        fit_dynamic_text(self["header"], "GT IPTV PLAYER PRO", preferred_size=font_px(px, 30), min_size=px(20))
        fit_dynamic_text(self["page_title"], localized_upper(_("Discover")), preferred_size=font_px(px, 30), min_size=px(20))
        set_text_if_changed(self["tmdb_wordmark"], "TMDB")
        self._refresh_footer(px)
        fit_dynamic_text(self["web_hint"], web_shortcut_hint_text(),
                         preferred_size=font_px(px, 17), min_size=px(11))

    def _refresh_footer(self, px):
        for index, item in enumerate(FOOTER):
            label = {"ok": INFO[self._media_type], "blue": SIMILAR[self._media_type]}.get(item[0], item[1])
            fit_dynamic_text(self["guide_action_{}".format(index)], remote_action_text(label),
                             preferred_size=font_px(px, 23), min_size=px(13))

    def _shown(self):
        self._refresh_chrome()
        self._child_open = False
        if not self._started:
            self._started = True
            self._start_timer.start(50, True)
        elif self._jobs or self._events or self._play_token:
            self._poll_timer.start(30, True)
        if self._entries and self._client is not None and not self._busy:
            self._start_selected_hero()
            self._detail_timer.start(300, True)

    def _begin(self):
        if getattr(self, "_closed", True):
            return
        self.settings = load_player_settings()
        if not str(self.settings.tmdb_api_key or "").strip():
            set_text_if_changed(self["status"], _(KEY_REQUIRED))
            self._open_child(self._key_choice, ChoiceBox, _(KEY_REQUIRED),
                             [(_("Settings"), "settings"), (_("Back"), "back")])
            return
        self._configure_client(self.settings.tmdb_api_key)
        self._load_page(1)

    def _configure_client(self, key):
        self._client = TMDbDiscoveryClient(api_key=key, language=metadata_language(), timeout=8,
                                           backdrop_size="w780" if _scale()[0] <= 1280 else "w1280")

    def _key_choice(self, choice=None):
        if choice and choice[1] == "settings":
            self.open_settings()
        else:
            self.close()

    def open_settings(self):
        from .main import GTPlayerSettingsScreen
        self._open_child(self._settings_closed, GTPlayerSettingsScreen)

    def _settings_closed(self, *args):
        self.settings = load_player_settings()
        key = str(self.settings.tmdb_api_key or "").strip()
        # Settings may remove the key while requests from the old client are
        # still pending. Cancel them before the missing-key early return so
        # late results cannot overwrite the prompt or leave navigation busy.
        self._cancel_playback()
        for channel in tuple(self._jobs):
            self._cancel(channel)
        self._detail_timer.stop()
        self._reset_hero()
        self._poll_timer.stop()
        with self._event_lock:
            self._events.clear()
        self._delivery.cancel()
        self._delivery_generation = None
        self._details.clear()
        self._busy = False
        set_text_if_changed(self["busy"], "")
        if not key:
            self._client = None
            self._entries = []
            self._selected_index = 0
            self._history.clear()
            self._selection_changed()
            self._render()
            set_text_if_changed(self["status"], _(KEY_REQUIRED))
            return
        self._configure_client(key)
        self._load_page(1, force=True)

    def _open_child(self, callback, screen, *args, **kwargs):
        if self._closed or self._child_open:
            return
        self._child_open = True

        def closed(*values):
            if getattr(self, "_closed", True):
                return
            self._child_open = False
            if callback:
                callback(*values)
            if not getattr(self, "_closed", True) and not self._child_open and (self._jobs or self._events or self._play_token):
                self._poll_timer.start(30, True)

        try:
            dialog = self.session.openWithCallback(closed, screen, *args, **kwargs)
        except Exception as error:
            log_event("discovery", "Could not open child screen", error)
        else:
            if dialog is not None and not isinstance(dialog, str):
                return dialog
        self._child_open = False
        set_text_if_changed(self["status"], _("Could not open the content list."))

    def _cancel(self, channel):
        job = self._jobs.pop(channel, None)
        if job:
            job[0].cancel()
        if channel == "hero":
            self._hero_request = None

    def _channel_error(self, channel):
        return (N_("The trailer could not be played.") if channel == "trailer"
                else N_("Could not load series") if self._media_type == "tv"
                else N_("Could not load movies"))

    def _submit(self, channel, work, done, timeout=25):
        self._cancel(channel)
        token = _WorkerToken(timeout)
        self._jobs[channel] = (token, done)
        client = self._client
        failure = self._channel_error(channel)
        slots = self._poster_slots if channel in ("posters", "posters2") else self._worker_slots

        def run():
            acquired = False
            result, error = None, ""
            try:
                while not slots.acquire(timeout=0.1):
                    token.check()
                acquired = True
                token.check()
                with client.request_scope(cancel_event=token.cancel_event, deadline=token.deadline):
                    result = work(token)
                token.check()
            except _WorkerCancelled:
                if not token.cancel_event.is_set():
                    log_event("discovery", "channel={} outcome=timeout".format(channel))
                return
            except (MetadataError, ContentError) as exc:
                error = str(exc)
            except Exception as exc:
                log_event("discovery", "{} failed: {}".format(channel, type(exc).__name__))
                error = failure
            finally:
                if acquired:
                    slots.release()
            self._queue(("done", channel, token, result, error))

        pool = self._channel_workers.get(channel)
        if pool is None:
            pool = self._channel_workers[channel] = LatestPosterWorkers(worker_count=1)
        if not pool.replace([run]):
            self._cancel(channel)
            self._failed(channel, failure)
            return
        self._arm_delivery()
        self._poll_timer.start(40, True)

    def _arm_delivery(self):
        if self._delivery_generation is None:
            self._delivery_generation = self._delivery.expect()

    def _wake_workers(self, unused_value, unused_error):
        self._delivery_generation = None
        self._poll()

    def _queue(self, event):
        lock = getattr(self, "_event_lock", None)
        if lock is None:
            return
        with lock:
            if getattr(self, "_closed", True) or not event[2].active():
                return
            if event[1] in ("hero", "list"):
                self._events.appendleft(event)
            else:
                self._events.append(event)
            generation = self._delivery_generation
            delivery = self._delivery
        if generation is not None:
            delivery.publish(generation, True)

    def _poll(self):
        if getattr(self, "_closed", True):
            return
        self._poll_timer.stop()
        if self._child_open or getattr(self.session, "current_dialog", self) is not self:
            return
        images = 0
        while True:
            with self._event_lock:
                if not self._events or (images >= 2 and self._events[0][0] == "image"):
                    break
                kind, channel, token, result, error = self._events.popleft()
            job = self._jobs.get(channel)
            if not job or job[0] is not token or not token.active():
                continue
            try:
                if kind == "image":
                    images += 1
                    self._apply_image(*result)
                else:
                    self._jobs.pop(channel, None)
                    if error:
                        self._failed(channel, error)
                    else:
                        job[1](result)
            except Exception as exc:
                log_event("discovery", "{} callback failed: {}".format(channel, type(exc).__name__))
                self._failed(channel, self._channel_error(channel))
            if self._closed or self._child_open:
                return
        for channel, job in list(self._jobs.items()):
            if job[0].expired():
                if not job[0].cancel_event.is_set():
                    log_event("discovery", "channel={} outcome=timeout".format(channel))
                self._cancel(channel)
                self._failed(channel, N_("The trailer could not be played.") if channel == "trailer"
                             else N_("Could not connect to the TMDb service")
                             if channel in ("list", "genres", "detail") else N_("Request timed out"))
                if self._closed or self._child_open:
                    return
        self._poll_playback()
        set_text_if_changed(self["busy"], "•" * (1 + int(time.monotonic() * 2) % 3) if self._busy else "")
        if self._jobs and not self._child_open:
            self._arm_delivery()
        if self._jobs or self._events or self._play_token:
            self._poll_timer.start(30 if self._events else 100, True)

    def _hint(self):
        text = _(HINTS[self._media_type] if self._entries else EMPTY[self._media_type])
        ellipsize_dynamic_text(self["status"], text, fallback_chars=100)

    def _failed(self, channel, error):
        if channel == "hero":
            self._hero_request = None
        if channel in ("detail", "posters", "posters2", "hero"):
            return
        self._busy = False
        if channel == "list" and self._entries:
            self._selection_changed(start_now=True)
            self._visible_ids = ()
            self._render()
        set_text_if_changed(self["busy"], "")
        retry = "PLAY  " + _("Trailer") if channel == "trailer" else "MENU  " + _("Retry")
        ellipsize_dynamic_text(self["status"], _(error) + "  —  " + retry, fallback_chars=100)
        if channel == "trailer":
            self._open_child(None, MessageBox, _(error), type=MessageBox.TYPE_INFO)

    def _load_page(self, page, force=False, pending_index=0):
        if self._closed or self._client is None:
            return
        self._busy = True
        self._pending_index = pending_index
        self._detail_timer.stop()
        self._hero_timer.stop()
        for channel in ("detail", "posters", "posters2", "hero"):
            self._cancel(channel)
        self._render()
        ellipsize_dynamic_text(self["status"], _(LOADING), fallback_chars=100)
        mode, section, genre_id, similar_id = self._media_type, self._section, self._genre_id, self._similar_id
        client = self._client
        self._submit("list", lambda token: client.titles(mode, section, page, genre_id, similar_id, force),
                     self._loaded)

    def _loaded(self, result):
        self._busy = False
        self._entries = result["entries"]
        self._page, self._pages = result["page"], result["pages"]
        self._selected_index = (len(self._entries) - 1 if self._pending_index < 0
                                else min(self._pending_index, max(0, len(self._entries) - 1)))
        self._visible_ids = ()
        self._hint()
        self._selection_changed(start_now=True)
        self._render()

    def _selected(self):
        return self._entries[self._selected_index] if self._entries else None

    def _filters(self):
        if self._media_type == "tv":
            return FILTERS[:2] + (("new", N_("On the Air")), FILTERS[3])
        return FILTERS

    @staticmethod
    def _identity(title):
        return title["media_type"], title["id"]

    def _render(self):
        unused_width, unused_height, px = _scale()
        filters = self._filters()
        for index, (key, label) in enumerate(MEDIA_TABS + filters):
            selected = key in (self._media_type, self._section)
            focused = self._tabs_focused and index == self._tab_index
            label_widget, active_widget = self["tab_{}".format(index)], self["tab_selected_{}".format(index)]
            fit_dynamic_text(active_widget if selected else label_widget, _(label),
                             preferred_size=font_px(px, 28), min_size=px(17))
            set_visible(label_widget, not selected)
            set_visible(active_widget, selected)
            set_visible(self["tab_focus_{}".format(index)], focused)
        section = dict(filters).get(self._section, SIMILAR[self._media_type])
        suffix = self._genre_name if self._section == "genres" else self._similar_title if self._section == "similar" else ""
        ellipsize_dynamic_text(self["section"], localized_upper(_(section)) + ("  •  " + suffix if suffix else ""), fallback_chars=65)
        set_text_if_changed(self["counter"], "{}/{}  ·  {}/{}".format(self._page, self._pages,
                                  self._selected_index + 1 if self._entries else 0, len(self._entries)))
        start = self._selected_index // 6 * 6
        visible = self._entries[start:start + 6]
        identities = tuple(self._identity(title) for title in visible)
        changed = identities != self._visible_ids
        for index in range(6):
            present = index < len(visible)
            for prefix in ("empty", "name"):
                widget = self["{}_{}".format(prefix, index)]
                set_visible(widget, True) if present else set_visible(widget, False)
            focus = self["focus_{}".format(index)]
            if present and start + index == self._selected_index and not self._tabs_focused:
                set_visible(focus, True)
                set_visible(self["marker_{}".format(index)], True)
            else:
                set_visible(focus, False)
                set_visible(self["marker_{}".format(index)], False)
            if changed or not present:
                self._hide_art("poster_{}".format(index))
            if present:
                set_text_if_changed(self["empty_{}".format(index)], "TMDB")
                ellipsize_dynamic_text(self["name_{}".format(index)], visible[index]["title"], fallback_chars=24)
        if changed:
            self._visible_ids = identities
            if visible and not self._busy:
                self._load_posters(visible)

    def _hide_art(self, name):
        loader = getattr(self, "_gt_pixmap_loaders", {}).pop(name, None)
        if loader:
            loader.close()
        set_visible(self[name], False)

    def _detail_for(self, title):
        identity = self._identity(title)
        detail = self._details.get(identity)
        if detail is not None:
            self._details.move_to_end(identity)
        return detail or title

    def _selection_changed(self, start_now=False):
        self._cancel("detail")
        self._detail_timer.stop()
        title = self._selected()
        if title is None:
            self._reset_hero()
            set_text_if_changed(self["title"], _("Discover"))
            for name in ("original", "rating", "rating_star", "meta", "genres", "director", "plot"):
                set_text_if_changed(self[name], "")
            return
        if self._hero_identity is None or self._hero_identity[0] != self._identity(title):
            self._reset_hero()
        detail = self._detail_for(title)
        self._show_detail(detail, full=start_now or detail is not title)
        self._load_hero(detail, cached_only=not start_now)
        if not start_now and self._hero_identity is None and self._hero_request is None:
            self._hero_timer.start(90, True)
        self._detail_timer.start(300, True)

    def _reset_hero(self):
        self._hero_timer.stop()
        self._hero_generation += 1
        self._cancel("hero")
        self._hero_identity = None
        self._hide_art("backdrop")
        self._hide_art("hero_fallback")
        for name in ("scrim", "hero_bottom", "hero_top"):
            set_visible(self[name], False)

    def _people(self, title):
        if title.get("creators"):
            return _("Created by {}").format(title["creators"])
        if title.get("director"):
            return _("Director: {}").format(title["director"])
        return ""

    @staticmethod
    def _duration_or_seasons(title):
        seasons = title.get("seasons")
        if seasons:
            return ngettext("{} season", "{} seasons", seasons).format(seasons)
        if title.get("runtime"):
            return _("{} min").format(title["runtime"])
        return ""

    def _show_detail(self, movie, full=True):
        unused_width, unused_height, px = _scale()
        fit_dynamic_text(self["title"], movie["title"], max_lines=2,
                         preferred_size=font_px(px, 72, role="title"), min_size=px(30))
        ellipsize_dynamic_text(self["original"], movie.get("original_title", "")
                              if movie.get("original_title") != movie["title"] else "", fallback_chars=70)
        set_text_if_changed(self["rating_star"], "★" if movie.get("rating") else "")
        set_text_if_changed(self["rating"], "{:.1f}".format(movie["rating"]) if movie.get("rating") else "")
        meta = (movie["year"], self._duration_or_seasons(movie))
        ellipsize_dynamic_text(self["meta"], "  •  ".join(filter(None, meta)), fallback_chars=50)
        ellipsize_dynamic_text(self["genres"], movie.get("genres") or "", fallback_chars=60)
        ellipsize_dynamic_text(self["director"], self._people(movie), fallback_chars=70)
        if full:
            fit_dynamic_text(self["plot"], movie.get("overview") or _(SUMMARY_UNAVAILABLE[self._media_type]),
                             max_lines=3, preferred_size=font_px(px, 26), min_size=px(22), fallback_chars=78)
        else:
            set_text_if_changed(self["plot"], "")

    def _load_selected_detail(self):
        if getattr(self, "_closed", True) or self._busy or self._child_open or self._client is None or not self._selected():
            return
        title = self._selected()
        identity = self._identity(title)
        self._show_detail(self._detail_for(title))
        if identity not in self._details:
            client = self._client
            media_type, title_id = identity

            def ready(detail):
                self._details[identity] = detail
                if len(self._details) > 60:
                    self._details.popitem(last=False)
                if self._selected() and self._identity(self._selected()) == identity:
                    self._show_detail(detail)
                    if detail.get("backdrop") != title.get("backdrop") or detail.get("poster") != title.get("poster"):
                        self._load_hero(detail)

            self._submit("detail", lambda token: client.detail(title_id, media_type=media_type, listing=title), ready)

    def _image_path(self, url, token, priority=False):
        token.check()
        return _cached_picon_path(url) or _download_picon(
            url, _picon_cache_path(url), timeout=PICON_DOWNLOAD_TIMEOUT, token=token, priority=priority)

    def _load_posters(self, visible):
        self._cancel("posters")
        self._cancel("posters2")
        active = self._selected_index % 6
        records = []
        ready_count = 0
        for index in sorted(range(len(visible)), key=lambda index: abs(index - active)):
            title = visible[index]
            url = title.get("poster")
            if not url:
                continue
            name, identity = "poster_{}".format(index), self._identity(title)
            path = _cached_picon_path(url) if ready_count < 2 else ""
            if path:
                self._apply_image(name, path, identity)
                ready_count += 1
            else:
                records.append((name, identity, url))

        def submit(channel, batch):
            if not batch:
                return

            def work(token):
                for name, identity, url in batch:
                    token.check()
                    try:
                        path = self._image_path(url, token)
                        if path:
                            self._queue(("image", channel, token, (name, path, identity), ""))
                    except _WorkerCancelled:
                        raise
                    except Exception as error:
                        log_event("discovery", "Poster download failed for {}".format(identity), error)
                        token.check()
            timeout = len(batch) * (2 * PICON_DOWNLOAD_TIMEOUT + RETRY_DELAY_SECONDS) + 8
            self._submit(channel, work, lambda result: None, timeout=max(30, timeout))
        submit("posters", records[::2])
        submit("posters2", records[1::2])

    def _start_selected_hero(self):
        if getattr(self, "_closed", True):
            return
        title = self._selected()
        if title is not None and self._client is not None and not self._child_open:
            self._load_hero(self._detail_for(title))

    def _load_hero(self, title, cached_only=False):
        identity = self._identity(title)
        backdrop, poster = title.get("backdrop"), title.get("poster")
        request = (identity, backdrop, poster)
        if request in (self._hero_identity, self._hero_request) or not (backdrop or poster):
            return
        if backdrop:
            path = _cached_picon_path(backdrop)
            if path and _valid_cinematic_backdrop_file(path):
                self._cancel("hero")
                self._hero_request = request
                self._apply_image("backdrop", path, identity)
                return
        elif poster:
            path = _cached_picon_path(poster)
            if path:
                self._cancel("hero")
                self._hero_request = request
                self._apply_image("backdrop", path, identity, portrait=True)
                return
        if cached_only:
            return

        def work(token):
            try:
                path = self._image_path(backdrop, token, priority=True) if backdrop else ""
            except _WorkerCancelled:
                raise
            except Exception as error:
                log_event("discovery", "Hero download failed for {}".format(identity), error)
                path = ""
            if path and _valid_cinematic_backdrop_file(path):
                return path, False
            # Portrait art is a last resort and stays on the right side.
            return (self._image_path(poster, token, priority=True) if poster else ""), True

        def ready(result):
            path, portrait = result
            if path:
                self._apply_image("backdrop", path, identity, portrait=portrait)
            else:
                self._hero_request = None

        self._submit("hero", work, ready, timeout=4 * PICON_DOWNLOAD_TIMEOUT + 2 * RETRY_DELAY_SECONDS + 8)
        if "hero" in self._jobs:
            self._hero_request = request

    def _apply_image(self, name, path, identity, portrait=False):
        if self._closed:
            return
        if name == "backdrop":
            if not self._selected() or self._identity(self._selected()) != identity:
                return
            ratio = _cinematic_cover_ratio(path)
            if not ratio or (not portrait and not _valid_cinematic_backdrop_file(path)):
                self._hero_request = None
                return
            self._hero_generation += 1
            generation = self._hero_generation
            request = self._hero_request
            widget_name = "hero_fallback" if portrait else name
            other_name = name if portrait else "hero_fallback"

            def decoded(loaded):
                if (getattr(self, "_closed", True) or generation != self._hero_generation
                        or not self._selected() or self._identity(self._selected()) != identity):
                    return
                if self._hero_request == request:
                    self._hero_request = None
                if loaded:
                    self._hero_identity = request
                    self._hide_art(other_name)
                    set_visible(self[widget_name], True)
                    for mask in ("scrim", "hero_bottom", "hero_top"):
                        set_visible(self[mask], True)
                else:
                    log_event("discovery", "Hero decoding failed for {}".format(identity))
                    set_visible(self[widget_name], False)

            if portrait:
                self._decode_art(widget_name, path, decoded)
            else:
                self._decode_art(widget_name, path, decoded, cover_ratio=ratio, cover_alignment="top_right")
        else:
            index = int(name.rsplit("_", 1)[1])
            if index < len(self._visible_ids) and self._visible_ids[index] == identity:
                def decoded(loaded):
                    if (getattr(self, "_closed", True) or index >= len(self._visible_ids)
                            or self._visible_ids[index] != identity):
                        return
                    set_visible(self[name], loaded)
                    if not loaded:
                        log_event("discovery", "Poster decoding failed for {}".format(identity))
                self._decode_art(name, path, decoded)

    def _decode_art(self, name, path, callback, **options):
        try:
            attach_pixmap(self, name, path, show_on_load=False, on_loaded=callback, **options)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as error:
            log_event("discovery", "Artwork could not be attached", error)
            callback(False)

    def move(self, step):
        if self._closed or self._child_open or not step:
            return
        if self._tabs_focused:
            self._tab_index = (self._tab_index + (1 if step > 0 else -1)) % (len(MEDIA_TABS) + len(FILTERS))
            self._render()
            return
        if self._busy:
            return
        target = self._selected_index + step
        if target >= len(self._entries) and self._page < self._pages:
            self._load_page(self._page + 1)
        elif target < 0 and self._page > 1:
            self._load_page(self._page - 1, pending_index=-1)
        elif self._entries:
            target = min(max(0, target), len(self._entries) - 1)
            if target == self._selected_index:
                return
            self._selected_index = target
            self._selection_changed()
            self._render()

    def focus_tabs(self, value):
        value = bool(value)
        if self._closed or self._child_open or value == self._tabs_focused:
            return
        self._tabs_focused = value
        self._tab_index = 1 if self._media_type == "tv" else 0
        self._render()

    def _change_media(self, mode):
        if mode == self._media_type:
            self.focus_tabs(False)
            return
        self._cancel_playback()
        for channel in tuple(self._jobs):
            self._cancel(channel)
        self._media_type = mode
        self._section = "popular"
        self._genre_id = self._similar_id = 0
        self._genre_name = self._similar_title = ""
        self._history.clear()
        self._entries = []
        self._page = self._pages = 1
        self._selected_index = 0
        self._tabs_focused = False
        self._tab_index = 1 if mode == "tv" else 0
        self._selection_changed()
        self._refresh_footer(_scale()[2])
        self._load_page(1)

    def open_selected(self):
        if self._closed or self._child_open:
            return
        if self._client is None:
            self.open_settings()
            return
        if self._tabs_focused:
            key = (MEDIA_TABS + self._filters())[self._tab_index][0]
            if self._tab_index < len(MEDIA_TABS):
                self._change_media(key)
                return
            if self._busy:
                return
            if key == "genres":
                self.choose_genre()
            else:
                self.focus_tabs(False)
                if key == self._section and self._entries:
                    return
                self._section = key
                self._history = []
                self._load_page(1)
            return
        if self._busy:
            return
        movie = self._selected()
        if movie:
            from .main import GTFeatureScreen
            detail = self._detail_for(movie)
            rating = "TMDB {:.1f}/10".format(detail["rating"]) if detail["rating"] else ""
            metadata = "  •  ".join(filter(None, (detail["year"], rating, self._duration_or_seasons(detail))))
            text = "\n\n".join(filter(None, (detail.get("original_title"), metadata, detail.get("genres"),
                                             self._people(detail), detail.get("overview")
                                             or _(SUMMARY_UNAVAILABLE[self._media_type]))))
            self._open_child(None, GTFeatureScreen, detail["title"], text)

    def choose_genre(self):
        if self._client is None or self._busy or self._child_open:
            return
        self._busy = True
        set_text_if_changed(self["status"], _(LOADING))
        client = self._client
        mode = self._media_type

        def ready(genres):
            self._busy = False
            all_titles = _("All Series") if mode == "tv" else _("All Movies")
            self._open_child(self._genre_chosen, ChoiceBox, _("Genres"), [(all_titles, 0)] + genres)

        self._submit("genres", lambda token: client.genres(media_type=mode), ready)

    def _genre_chosen(self, choice=None):
        if not choice:
            self._hint()
            return
        self._genre_name, self._genre_id = choice
        self._section = "genres"
        self._tabs_focused = False
        self._history = []
        self._load_page(1)

    def similar(self):
        movie = self._selected()
        if self._busy or self._child_open or not movie:
            return
        self._history.append((self._section, self._genre_id, self._genre_name, self._similar_id,
                              self._similar_title, self._page, self._pages, self._selected_index,
                              list(self._entries), self._tab_index))
        self._history = self._history[-10:]
        self._section, self._similar_id, self._similar_title = "similar", movie["id"], movie["title"]
        self._tabs_focused = False
        self._load_page(1)

    def search_iptv(self):
        title = self._selected()
        if self._busy or self._child_open or self._client is None or not title:
            return
        if self._provider is None:
            self._provider = content_client_for(self.account)
        provider = self._provider
        kind = "series" if self._media_type == "tv" else "movie"
        if not _client_supports(provider, kind):
            self._open_child(None, MessageBox, _("This source does not support the selected content type."),
                             type=MessageBox.TYPE_INFO)
            return
        self._busy = True
        set_text_if_changed(self["status"], _("Search in IPTV") + "…")

        def work(token):
            store = LiveCategoryPreferenceStore(category_preferences_path(kind))
            preferences = store.load(resume_account_scope(provider))
            if not (preferences.hidden or preferences.include_terms or preferences.exclude_terms):
                return frozenset()
            with provider.request_scope(cancel_event=token.cancel_event, deadline=token.deadline):
                categories = provider.load_categories(kind)
                return category_visibility_ids(categories, preferences)

        def ready(hidden):
            from .browser_vod import GTAllMoviesSearchScreen, GTAllSeriesSearchScreen

            self._busy = False
            self._hint()
            options = dict(hidden_category_ids=hidden, initial_query=title["title"])
            if kind == "series":
                self._open_child(None, GTAllSeriesSearchScreen, self.account, provider,
                                 discovery_title=title, **options)
            else:
                adapter = DiscoverySearchClient(provider, title, hidden)
                self._open_child(None, GTAllMoviesSearchScreen, self.account, adapter,
                                 discovery_search=True, **options)

        self._submit("search", work, ready, timeout=30)

    def play_trailer(self):
        title = self._selected()
        if self._busy or self._child_open or not title or self._client is None:
            return
        self._busy = True
        set_text_if_changed(self["status"], _("Trailer") + "…")
        client = self._client
        media_type, title_id = self._identity(title)

        def work(token):
            video_id = client.trailer(title_id, media_type=media_type,
                                      original_language=title.get("original_language", ""))
            if not video_id:
                raise MetadataError(N_("No official trailer found."))
            return video_id

        def ready(video_id):
            from .web_remote import WEB_REMOTE
            from .web_youtube import WEB_YOUTUBE

            if WEB_REMOTE.session is not self.session:
                WEB_REMOTE.bind_session(self.session)
            self._youtube = WEB_YOUTUBE
            job = self._youtube.play(video_id, title["title"] + " — " + _("Trailer"),
                                     _request_owner=self, _on_closed=self._trailer_closed,
                                     _original_audio=True, _trailer=True)
            self._play_token = job["token"]
            self._play_deadline = time.monotonic() + 70

        self._submit("trailer", work, ready, timeout=35)

    def _poll_playback(self):
        if not self._play_token:
            return
        try:
            job = self._youtube.status(self._play_token).get("job") or {}
        except Exception as error:
            log_event("discovery", "Trailer status unavailable", error)
            job = {}
        state = job.get("state")
        if state in ("cancelled", "playing", "failed") or time.monotonic() >= self._play_deadline or not state:
            self._cancel_playback()
            self._busy = False
            if state in ("cancelled", "playing"):
                self._hint()
            else:
                set_text_if_changed(self["status"], _("The trailer could not be played."))

    def _cancel_playback(self):
        if self._play_token:
            self._youtube.cancel_play(self._play_token)
            self._play_token = ""

    def _trailer_closed(self, *unused):
        if not getattr(self, "_closed", True):
            self._cancel_playback()
            self._busy = False
            self._hint()

    def open_menu(self):
        mode, label = MEDIA_TABS[0 if self._media_type == "tv" else 1]
        self._open_child(self._menu_chosen, ChoiceBox, _("Discover"),
                         [(_(label), mode), (_("Refresh"), "refresh"), (_("Genres"), "genres"),
                          (_("Trailer"), "trailer"), (_("Settings"), "settings"), (_("Back"), "back")])

    def _menu_chosen(self, choice=None):
        if not choice:
            return
        action = choice[1]
        if action in ("movie", "tv") and self._client is not None:
            self._change_media(action)
        elif action == "settings":
            self.open_settings()
        elif action == "refresh":
            self._cancel_playback()
            for channel in tuple(self._jobs):
                self._cancel(channel)
            self._details.clear()
            if self._client:
                self._load_page(self._page, force=True)
            else:
                self.open_settings()
        elif action == "genres":
            self.choose_genre()
        elif action == "trailer":
            self.play_trailer()
        elif action == "back":
            self.go_back()

    def go_back(self):
        if self._child_open:
            return
        if self._tabs_focused:
            self.focus_tabs(False)
            return
        if "trailer" in self._jobs or self._play_token:
            self._cancel("trailer")
            self._cancel_playback()
            self._busy = False
            self._hint()
            return
        if self._history:
            for channel in tuple(self._jobs):
                self._cancel(channel)
            self._busy = False
            (self._section, self._genre_id, self._genre_name, self._similar_id, self._similar_title,
             self._page, self._pages, self._selected_index, self._entries, self._tab_index) = self._history.pop()
            self._tabs_focused = False
            self._visible_ids = ()
            self._hint()
            self._selection_changed(start_now=True)
            self._render()
        else:
            self.close()

    def _stop(self):
        with self._event_lock:
            self._closed = True
            self._events.clear()
        self._cancel_playback()
        self._delivery.close()
        for pool in self._channel_workers.values():
            pool.close()
        self._channel_workers.clear()
        for timer in (self._poll_timer, self._start_timer, self._detail_timer, self._hero_timer):
            timer.stop()
        for channel in tuple(self._jobs):
            self._cancel(channel)
        self._entries = []
        self._details.clear()
        self._history = []
        if getattr(self._provider, "source_type", "") == "xtream":
            release = getattr(self._provider, "release_movie_search", None)
            if callable(release):
                release()

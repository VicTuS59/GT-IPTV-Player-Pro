# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remote-controlled TMDB movie discovery, independent of provider playback."""

import threading
import time
from collections import deque
from .ui_update import set_visible
from .typography import set_text_if_changed

from .vod_grid import LatestPosterWorkers

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.ChoiceBox import ChoiceBox
from Screens.MessageBox import MessageBox
from Screens.Screen import Screen
from enigma import eServiceReference, eTimer

from .background import attach_pixmap
from .discovery_layout import skin as discovery_skin
from .browser import (
    GTAllMoviesSearchScreen, GTExternalPlayerScreen, _WorkerToken, _WorkerCancelled,
    PICON_DOWNLOAD_TIMEOUT,
    _cached_picon_path, _cinematic_cover_ratio, _client_supports,
    _download_picon, _picon_cache_path, _valid_cinematic_backdrop_file, content_client_for,
)
from .artwork import RETRY_DELAY_SECONDS
from .category_preferences import LiveCategoryPreferenceStore, category_preferences_path
from .category_visibility import category_visibility_ids
from .content import ContentError, ContentItem
from .diagnostics import log_event
from .discovery import (
    TMDbDiscoveryClient, DiscoverySearchClient, prepare_youtube_trailer, resolve_youtube_trailer,
)
from .i18n import N_, _, localized_upper, metadata_language
from .main import _connect_timer, _scale
from .metadata import MetadataError
from .paths import plugin_path
from .playback import resume_account_scope
from .remote_footer import prepared_skin
from .remote_footer import footer_item, remote_action_text, web_shortcut_hint_text
from .settings import load_player_settings
from .typography import ellipsize_dynamic_text, fit_dynamic_text, font_px


SECTIONS = (("popular", N_("Popular")), ("top", N_("Top Rated")),
            ("new", N_("New Releases")), ("genres", N_("Genres")))
LOADING = N_("Loading data... Please wait.")
KEY_REQUIRED = N_("Please enter your TMDB API key to use Discover Movies.")
SHORT_HINT = N_("Choose a movie, then search in IPTV.")
FOOTER = (
    footer_item("ok", N_("Movie information")),
    footer_item("green", N_("Search in IPTV"), 1.15),
    footer_item("yellow", N_("Genres")),
    footer_item("blue", N_("Similar Movies")),
    footer_item("play", N_("Trailer")),
    footer_item("menu", "refresh"),
    footer_item("exit", "back"),
)


def _asset(name):
    return plugin_path("skin", "images", "discovery-r48", name)


def _skin():
    width, height, px = _scale()
    return discovery_skin(width, height, px,
                          [remote_action_text(item[1]) for item in FOOTER],
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
        self._details = {}
        self._jobs = {}
        self._events = deque()
        self._event_lock = threading.Lock()
        self._worker_slots = threading.BoundedSemaphore(3)
        self._channel_workers = {}
        self._visible_ids = ()
        self._hero_identity = None
        self._hero_request = None
        self._hero_generation = 0
        self._pending_index = 0
        self._poll_timer = eTimer()
        self._start_timer = eTimer()
        self._detail_timer = eTimer()
        _connect_timer(self._poll_timer, self._poll)
        _connect_timer(self._start_timer, self._begin)
        _connect_timer(self._detail_timer, self._load_selected_detail)

        for name in ("backdrop", "scrim", "hero_bottom", "hero_top", "tmdb_logo"):
            self[name] = Pixmap()
        # Alpha masks are loaded by the skin, never by ePicLoad.
        for name in ("backdrop", "scrim", "hero_bottom", "hero_top"):
            set_visible(self[name], False)
        for name in ("video_guard", "app_bg", "header", "header_divider", "page_title",
                     "tmdb_wordmark", "section", "title", "original", "rating_star", "rating",
                     "meta", "genres", "director", "plot", "status", "busy", "counter",
                     "footer", "footer_line", "guide_divider", "web_hint"):
            self[name] = Label("")
        for index in range(4):
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
        set_text_if_changed(self["title"], _("Discover Movies"))
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
        # Player contexts also map LEFT/RIGHT on key-up in some images.
        # Keep only trailer actions here so a release cannot navigate again.
        # The normal map has priority for color/channel keys that MediaPlayer
        # aliases to playback (for example GREEN -> play on OpenATV).
        self["trailer_actions"] = ActionMap(
            ["InfobarMoviePlayerActions", "InfobarSeekActions", "MediaPlayerActions"],
            {"play": self.play_trailer, "playpauseService": self.play_trailer,
             "playpause": self.play_trailer, "playService": self.play_trailer}, 0)
        self.onShown.append(self._shown)
        self.onLayoutFinish.append(self._layout_ready)
        self.onClose.append(self._stop)
        self.setTitle(_("Discover Movies"))
        self._render()

    def _layout_ready(self):
        from .remote_footer import bind_layout_text
        bind_layout_text(self)
        # Hiding a component before its native instance exists is insufficient
        # on some images; initialise visibility again after the skin is built.
        set_visible(self["video_guard"], True)
        set_visible(self["backdrop"], False)
        set_visible(self["scrim"], False)
        set_visible(self["hero_bottom"], False)
        set_visible(self["hero_top"], False)
        self._refresh_chrome()
        self._render()

    def _refresh_chrome(self):
        unused_width, unused_height, px = _scale()
        fit_dynamic_text(self["header"], "GT IPTV PLAYER PRO", preferred_size=font_px(px, 30), min_size=px(20))
        fit_dynamic_text(self["page_title"], localized_upper(_("Discover Movies")), preferred_size=font_px(px, 30), min_size=px(20))
        set_text_if_changed(self["tmdb_wordmark"], "TMDB")
        for index, item in enumerate(FOOTER):
            fit_dynamic_text(self["guide_action_{}".format(index)], remote_action_text(item[1]),
                             preferred_size=font_px(px, 23), min_size=px(13))
        fit_dynamic_text(self["web_hint"], web_shortcut_hint_text(),
                         preferred_size=font_px(px, 17), min_size=px(11))

    def _shown(self):
        self._refresh_chrome()
        self._child_open = False
        if not self._started:
            self._started = True
            self._start_timer.start(50, True)
        elif self._jobs or self._events:
            self._poll_timer.start(80, True)
        if self._entries and not self._busy:
            self._detail_timer.start(300, True)

    def _begin(self):
        if self._closed:
            return
        self.settings = load_player_settings()
        if not str(self.settings.tmdb_api_key or "").strip():
            set_text_if_changed(self["status"], _(KEY_REQUIRED))
            self._open_child(self._key_choice, ChoiceBox, title=_(KEY_REQUIRED),
                             list=[(_("Settings"), "settings"), (_("Back"), "back")])
            return
        self._client = TMDbDiscoveryClient(api_key=self.settings.tmdb_api_key,
                                           enabled=True, language=metadata_language(), timeout=8)
        self._load_page(1)

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
        for channel in tuple(self._jobs):
            self._cancel(channel)
        self._detail_timer.stop()
        self._poll_timer.stop()
        with self._event_lock:
            self._events.clear()
        self._details.clear()
        self._busy = False
        set_text_if_changed(self["busy"], "")
        if not key:
            self._client = None
            set_text_if_changed(self["status"], _(KEY_REQUIRED))
            return
        self._client = TMDbDiscoveryClient(api_key=key, enabled=True, language=metadata_language(), timeout=8)
        self._load_page(1, force=True)

    def _open_child(self, callback, screen, *args, **kwargs):
        self._child_open = True

        def closed(*values):
            if self._closed:
                return
            self._child_open = False
            if callback:
                callback(*values)
            if not self._child_open and (self._jobs or self._events):
                self._poll_timer.start(80, True)

        try:
            return self.session.openWithCallback(closed, screen, *args, **kwargs)
        except Exception:
            self._child_open = False
            raise

    def _cancel(self, channel):
        job = self._jobs.pop(channel, None)
        if job:
            job[0].cancel()
        if channel == "hero":
            self._hero_request = None

    @staticmethod
    def _channel_error(channel):
        return (N_("The trailer could not be played.") if channel == "trailer"
                else N_("Could not load movies"))

    def _submit(self, channel, work, done, timeout=25):
        self._cancel(channel)
        token = _WorkerToken(timeout)
        self._jobs[channel] = (token, done)
        client = self._client

        def run():
            acquired = False
            result, error = None, ""
            try:
                while not self._worker_slots.acquire(timeout=0.1):
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
                error = self._channel_error(channel)
            finally:
                if acquired:
                    self._worker_slots.release()
            self._queue(("done", channel, token, result, error))

        pool = self._channel_workers.get(channel)
        if pool is None:
            pool = self._channel_workers[channel] = LatestPosterWorkers(worker_count=1)
        pool.replace([run])
        self._poll_timer.start(80, True)
        return token

    def _queue(self, event):
        with self._event_lock:
            if not self._closed and event[2].active():
                self._events.append(event)

    def _poll(self):
        if self._closed:
            return
        if self._child_open:
            self._poll_timer.start(150, True)
            return
        with self._event_lock:
            events = []
            images = 0
            while self._events:
                event = self._events[0]
                if event[0] == "image":
                    if images >= 2:
                        break
                    images += 1
                events.append(self._events.popleft())
        for kind, channel, token, result, error in events:
            job = self._jobs.get(channel)
            if not job or job[0] is not token or not token.active():
                continue
            if kind == "image":
                self._apply_image(*result)
            else:
                self._jobs.pop(channel, None)
                if error:
                    self._failed(channel, error)
                else:
                    try:
                        job[1](result)
                    except Exception as exc:
                        log_event("discovery", "{} callback failed: {}".format(channel, type(exc).__name__))
                        self._failed(channel, self._channel_error(channel))
        for channel, job in list(self._jobs.items()):
            if job[0].expired():
                if not job[0].cancel_event.is_set():
                    log_event("discovery", "channel={} outcome=timeout".format(channel))
                self._cancel(channel)
                self._failed(channel, N_("The trailer could not be played.") if channel == "trailer"
                             else N_("Could not connect to the TMDb service")
                             if channel in ("list", "genres", "detail") else N_("Request timed out"))
        set_text_if_changed(self["busy"], "•" * (1 + int(time.monotonic() * 2) % 3) if self._busy else "")
        if self._jobs or self._events:
            self._poll_timer.start(100, True)

    def _failed(self, channel, error):
        if channel == "hero":
            self._hero_request = None
        if channel in ("detail", "posters", "posters2", "hero"):
            return
        self._busy = False
        set_text_if_changed(self["busy"], "")
        retry = "PLAY: " + _("Trailer") if channel == "trailer" else "MENU: " + _("Retry")
        set_text_if_changed(self["status"], _(error) + "  —  " + retry)
        if channel == "trailer":
            self._open_child(None, MessageBox, _(error), type=MessageBox.TYPE_INFO)

    def _load_page(self, page, force=False, pending_index=0):
        if self._closed or self._client is None:
            return
        self._busy = True
        self._pending_index = pending_index
        self._detail_timer.stop()
        for channel in ("detail", "posters", "hero"):
            self._cancel(channel)
        set_text_if_changed(self["status"], _(LOADING))
        section, genre_id, similar_id = self._section, self._genre_id, self._similar_id
        client = self._client
        self._submit("list", lambda token: client.movies(section, page, genre_id, similar_id, force),
                     self._loaded)

    def _loaded(self, result):
        self._busy = False
        self._entries = result["entries"]
        self._page, self._pages = result["page"], result["pages"]
        self._selected_index = (len(self._entries) - 1 if self._pending_index < 0
                                else min(self._pending_index, max(0, len(self._entries) - 1)))
        self._visible_ids = ()
        set_text_if_changed(self["status"], _(SHORT_HINT)
                               if self._entries else _("No movies found."))
        self._selection_changed()
        movie = self._selected()
        if movie is not None:
            # Start the selected backdrop before the remaining poster jobs.
            initial = self._details.get(movie["id"], movie)
            self._show_detail(initial)
            self._load_hero(initial)
        self._render()

    def _selected(self):
        return self._entries[self._selected_index] if self._entries else None

    def _active_tab_index(self):
        for index, (key, unused_label) in enumerate(SECTIONS):
            if key == self._section:
                return index
        return -1

    def _render(self):
        unused_width, unused_height, px = _scale()
        active = self._tab_index if self._tabs_focused else self._active_tab_index()
        for index, (key, label) in enumerate(SECTIONS):
            for prefix in ("tab", "tab_selected"):
                fit_dynamic_text(self["{}_{}".format(prefix, index)], _(label),
                                 preferred_size=font_px(px, 28), min_size=px(20))
            for prefix in ("tab_selected", "tab_focus"):
                widget = self["{}_{}".format(prefix, index)]
                set_visible(widget, True) if index == active else set_visible(widget, False)
        section = dict(SECTIONS).get(self._section, N_("Similar Movies"))
        suffix = self._genre_name if self._section == "genres" else self._similar_title if self._section == "similar" else ""
        ellipsize_dynamic_text(self["section"], localized_upper(_(section)) + ("  •  " + suffix if suffix else ""), fallback_chars=65)
        set_text_if_changed(self["counter"], "{}/{}  ·  {}/{}".format(self._page, self._pages,
                                  self._selected_index + 1 if self._entries else 0, len(self._entries)))
        start = self._selected_index // 6 * 6
        visible = self._entries[start:start + 6]
        identities = tuple(movie["id"] for movie in visible)
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

    def _selection_changed(self):
        self._cancel("detail")
        movie = self._selected()
        if movie is None:
            self._reset_hero()
            set_text_if_changed(self["title"], _("Discover Movies"))
            for name in ("original", "rating", "rating_star", "meta", "genres", "director", "plot"):
                set_text_if_changed(self[name], "")
            return
        if self._hero_identity != movie["id"]:
            self._reset_hero()
        self._detail_timer.start(300, True)

    def _reset_hero(self):
        self._hero_generation += 1
        self._cancel("hero")
        self._hero_identity = None
        self._hide_art("backdrop")
        for name in ("scrim", "hero_bottom", "hero_top"):
            set_visible(self[name], False)

    def _show_detail(self, movie):
        unused_width, unused_height, px = _scale()
        fit_dynamic_text(self["title"], movie["title"], max_lines=2,
                         preferred_size=font_px(px, 72, role="title"), min_size=px(30))
        ellipsize_dynamic_text(self["original"], movie.get("original_title", "")
                              if movie.get("original_title") != movie["title"] else "", fallback_chars=70)
        set_text_if_changed(self["rating_star"], "★" if movie.get("rating") else "")
        set_text_if_changed(self["rating"], "{:.1f}".format(movie["rating"]) if movie.get("rating") else "")
        meta = [str(movie.get("year") or "")]
        if movie.get("runtime"):
            meta.append(_("{} min").format(movie["runtime"]))
        ellipsize_dynamic_text(self["meta"], "  •  ".join(x for x in meta if x), fallback_chars=50)
        ellipsize_dynamic_text(self["genres"], movie.get("genres") or "", fallback_chars=60)
        ellipsize_dynamic_text(self["director"], _("Director: {}").format(movie["director"])
                              if movie.get("director") else "", fallback_chars=70)
        fit_dynamic_text(self["plot"], movie.get("overview") or _("Movie summary unavailable."),
                         max_lines=3, preferred_size=font_px(px, 26), min_size=px(22), fallback_chars=78)

    def _load_selected_detail(self):
        if self._closed or self._busy or self._child_open or not self._selected():
            return
        movie = dict(self._selected())
        # The existing selection timer also gates native title/plot fitting.
        # Rapid key repeats must not measure every intermediate long title.
        self._show_detail(self._details.get(movie["id"], movie))
        if movie["id"] not in self._details:
            client = self._client

            def ready(detail):
                self._details[detail["id"]] = detail
                if len(self._details) > 60:
                    self._details.pop(next(iter(self._details)))
                if self._selected() and self._selected()["id"] == detail["id"]:
                    self._show_detail(detail)
                    self._load_hero(detail)

            self._submit("detail", lambda token: client.detail(movie["id"]), ready)
        self._load_hero(self._details.get(movie["id"], movie))

    def _image_path(self, url, token):
        token.check()
        return _cached_picon_path(url) or _download_picon(
            url, _picon_cache_path(url), timeout=PICON_DOWNLOAD_TIMEOUT, token=token)

    def _load_posters(self, visible):
        self._cancel("posters")
        self._cancel("posters2")
        selected = self._selected()
        selected_id = selected.get("id") if selected else None
        records = [(index, dict(movie)) for index, movie in enumerate(visible)]
        records.sort(key=lambda pair: pair[1].get("id") != selected_id)
        def submit(channel, batch):
            def work(token):
                for index, movie in batch:
                    token.check()
                    if not movie.get("poster"):
                        continue
                    try:
                        path = self._image_path(movie["poster"], token)
                        if path:
                            self._queue(("image", channel, token,
                                         ("poster_{}".format(index), path, movie["id"]), ""))
                    except _WorkerCancelled:
                        raise
                    except Exception as error:
                        log_event("discovery", "stage=poster outcome=error movie_id={}".format(movie["id"]), error)
                        token.check()
            timeout = len(batch) * (2 * PICON_DOWNLOAD_TIMEOUT + RETRY_DELAY_SECONDS) + 8
            self._submit(channel, work, lambda result: None, timeout=max(30, timeout))
        submit("posters", records[::2])
        submit("posters2", records[1::2])

    def _load_hero(self, movie):
        # Discovery always includes its own hero, independently of the classic
        # versus cinematic layout preference for the provider's VOD browser.
        if self._hero_identity == movie["id"] or not movie.get("backdrop"):
            return
        movie_id, url = movie["id"], movie["backdrop"]
        identity = (movie_id, url)
        if self._hero_request == identity:
            return

        def ready(path):
            if path and _valid_cinematic_backdrop_file(path):
                self._apply_image("backdrop", path, movie_id)
            else:
                self._hero_request = None

        self._submit("hero", lambda token: self._image_path(url, token), ready,
                     timeout=2 * PICON_DOWNLOAD_TIMEOUT + RETRY_DELAY_SECONDS + 8)
        self._hero_request = identity

    def _apply_image(self, name, path, movie_id):
        if self._closed:
            return
        if name == "backdrop":
            if (not self._selected() or self._selected()["id"] != movie_id
                    or not _valid_cinematic_backdrop_file(path)):
                return
            self._hero_generation += 1
            generation = self._hero_generation

            def decoded(loaded):
                if (self._closed or generation != self._hero_generation
                        or not self._selected() or self._selected()["id"] != movie_id):
                    return
                self._hero_request = None
                if loaded:
                    self._hero_identity = movie_id
                    for mask in ("scrim", "hero_bottom", "hero_top"):
                        set_visible(self[mask], True)
                else:
                    log_event("discovery", "stage=decode kind=backdrop outcome=failed movie_id={}".format(movie_id))
                    self._hero_identity = None
                    set_visible(self["backdrop"], False)
                    for mask in ("scrim", "hero_bottom", "hero_top"):
                        set_visible(self[mask], False)

            attach_pixmap(self, name, path, cover_ratio=_cinematic_cover_ratio(path),
                          cover_alignment="top_right", on_loaded=decoded)
        else:
            index = int(name.rsplit("_", 1)[1])
            if index < len(self._visible_ids) and self._visible_ids[index] == movie_id:
                def decoded(loaded):
                    if (self._closed or index >= len(self._visible_ids)
                            or self._visible_ids[index] != movie_id):
                        return
                    if not loaded:
                        set_visible(self[name], False)
                        log_event("discovery", "stage=decode kind=poster outcome=failed movie_id={}".format(movie_id))
                attach_pixmap(self, name, path, on_loaded=decoded)

    def move(self, step):
        if self._closed or self._child_open or self._busy or not step:
            return
        if self._tabs_focused:
            self._tab_index = (self._tab_index + (1 if step > 0 else -1)) % len(SECTIONS)
            self._render()
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
            self._render()
            self._selection_changed()

    def focus_tabs(self, value):
        value = bool(value)
        if self._closed or self._child_open or value == self._tabs_focused:
            return
        self._tabs_focused = value
        # Leaving an unconfirmed tab returns to the section actually open.
        active_index = self._active_tab_index()
        if active_index >= 0:
            self._tab_index = active_index
        self._render()

    def open_selected(self):
        if self._closed or self._child_open:
            return
        if self._client is None:
            self.open_settings()
            return
        if self._busy:
            return
        if self._tabs_focused:
            key = SECTIONS[self._tab_index][0]
            if key == "genres":
                self.choose_genre()
            else:
                if key == self._section and self._entries:
                    return
                self._section = key
                self._history = []
                self._load_page(1)
            return
        movie = self._selected()
        if movie:
            from .main import GTFeatureScreen
            detail = self._details.get(movie["id"], movie)
            text = "\n\n".join(x for x in (detail.get("original_title"),
                                  "{}  •  TMDB {:.1f}/10".format(detail["year"], detail["rating"]),
                                  detail.get("genres"),
                                  _("Director: {}").format(detail["director"]) if detail.get("director") else "",
                                  detail.get("overview") or _("Movie summary unavailable.")) if x)
            self._open_child(None, GTFeatureScreen, detail["title"], text)

    def choose_genre(self):
        if self._client is None or self._busy:
            return
        self._busy = True
        set_text_if_changed(self["status"], _(LOADING))
        client = self._client

        def ready(genres):
            self._busy = False
            self._open_child(self._genre_chosen, ChoiceBox, title=_("Genres"),
                             list=[(_("All Movies"), 0)] + genres)

        self._submit("genres", lambda token: client.genres(), ready)

    def _genre_chosen(self, choice=None):
        if not choice:
            set_text_if_changed(self["status"], "")
            return
        self._genre_name, self._genre_id = choice
        self._section, self._tab_index = "genres", 3
        self._history = []
        self._load_page(1)

    def similar(self):
        movie = self._selected()
        if self._busy or not movie:
            return
        self._history.append((self._section, self._genre_id, self._genre_name, self._similar_id,
                              self._similar_title, self._page, self._pages, self._selected_index,
                              list(self._entries), self._tab_index))
        self._history = self._history[-10:]
        self._section, self._similar_id, self._similar_title = "similar", movie["id"], movie["title"]
        self._tabs_focused = False
        self._load_page(1)

    def search_iptv(self):
        movie = self._selected()
        if self._busy or not movie:
            return
        movie = dict(movie)
        if self._provider is None:
            self._provider = content_client_for(self.account)
        provider = self._provider
        if not _client_supports(provider, "movie"):
            self._open_child(None, MessageBox, _("This source does not support the selected content type."),
                             type=MessageBox.TYPE_INFO)
            return
        self._busy = True
        set_text_if_changed(self["status"], _("Search in IPTV") + "…")

        def work(token):
            with provider.request_scope(cancel_event=token.cancel_event, deadline=token.deadline):
                categories = provider.load_categories("movie")
                store = LiveCategoryPreferenceStore(category_preferences_path("movie"))
                preferences = store.load(resume_account_scope(provider))
                return category_visibility_ids(categories, preferences)

        def ready(hidden):
            self._busy = False
            adapter = DiscoverySearchClient(provider, movie, hidden)
            set_text_if_changed(self["status"], _(SHORT_HINT))
            self._open_child(None, GTAllMoviesSearchScreen, self.account, adapter,
                             hidden_category_ids=hidden, initial_query=movie["title"])

        self._submit("search", work, ready, timeout=30)

    def play_trailer(self):
        movie = self._selected()
        if self._busy or not movie or self._client is None:
            return
        try:
            resolver, service_type = prepare_youtube_trailer()
        except MetadataError as exc:
            self._failed("trailer", str(exc))
            return
        movie = dict(movie)
        self._busy = True
        set_text_if_changed(self["status"], _("Trailer") + "…")
        client = self._client

        def work(token):
            video_id = client.trailer(movie["id"])
            if not video_id:
                raise MetadataError(N_("No trailer is available for this movie."))
            return resolve_youtube_trailer(video_id, token, resolver)

        def ready(url):
            self._busy = False
            from .youtube_playback import (
                YouTubeNativePlayerUnavailable, youtube_service_type, youtube_stream_parts,
            )
            try:
                native_type = youtube_service_type(url, service_type)
                unused_video, audio = youtube_stream_parts(url)
            except YouTubeNativePlayerUnavailable:
                self._failed("trailer", _("The trailer could not be played.")
                             + " · ServiceApp / ExtEplayer3 (5002)")
                return
            title = movie["title"] + " — " + _("Trailer")
            item = ContentItem("movie", "tmdb-trailer-{}".format(movie["id"]), title, icon=movie.get("poster", ""))
            item.youtube_native = True
            item.youtube_separate_audio = bool(audio)
            item.youtube_requested_quality = getattr(resolver, "requested_quality", "")
            item.youtube_selected_quality = getattr(resolver, "selected_quality", 0)
            item.youtube_duration_seconds = getattr(resolver, "duration_seconds", 0)
            reference = eServiceReference(native_type, 0, url)
            reference.setName(title)
            set_text_if_changed(self["status"], _(SHORT_HINT))
            self._open_child(None, GTExternalPlayerScreen, reference, item)

        self._submit("trailer", work, ready, timeout=35)

    def open_menu(self):
        self._open_child(self._menu_chosen, ChoiceBox, title=_("Discover Movies"),
                         list=[(_("Refresh"), "refresh"), (_("Genres"), "genres"),
                               (_("Trailer"), "trailer"), (_("Settings"), "settings"), (_("Back"), "back")])

    def _menu_chosen(self, choice=None):
        if not choice:
            return
        action = choice[1]
        if action == "settings":
            self.open_settings()
        elif action == "refresh":
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

    def go_back(self):
        if self._history:
            for channel in tuple(self._jobs):
                self._cancel(channel)
            self._busy = False
            (self._section, self._genre_id, self._genre_name, self._similar_id, self._similar_title,
             self._page, self._pages, self._selected_index, self._entries, self._tab_index) = self._history.pop()
            self._tabs_focused = False
            self._visible_ids = ()
            set_text_if_changed(self["status"], _(SHORT_HINT))
            self._render()
            self._selection_changed()
        else:
            self.close()

    def _stop(self):
        self._closed = True
        for pool in self._channel_workers.values():
            pool.close()
        self._channel_workers.clear()
        for timer in (self._poll_timer, self._start_timer, self._detail_timer):
            timer.stop()
        for channel in tuple(self._jobs):
            self._cancel(channel)
        with self._event_lock:
            self._events.clear()
        self._entries = []
        self._details.clear()
        self._history = []

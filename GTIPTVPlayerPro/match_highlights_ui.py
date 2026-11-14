# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Match-scoped results with native TV playback."""
from .auxiliary_presentation import refresh_after as aux_refresh_after
from .diagnostics import log_event
from .match_highlights_search import MatchHighlightsService
from .matches_text import tr
from .remote_footer import footer_item
from .uefa_highlights import aspor_media_url
from .web_youtube import WEB_YOUTUBE
from .youtube_ui import GTYouTubeSearchScreen


FOOTER = tuple(footer_item(key, action) for key, action in (
    ("up_down", "select"), ("left_right", "page"), ("ok", "play"), ("blue", "retry"),
    ("yellow", "quality"), ("red", "refresh"), ("exit", "back"),
))


class GTMatchHighlightsScreen(GTYouTubeSearchScreen):
    RESULTS_FOOTER = FOOTER

    def __init__(self, session, query, match=None, search_service=None):
        self._match_query = self._seed_query = query
        self._highlights_service = MatchHighlightsService(
            match, search_service if search_service is not None else WEB_YOUTUBE,
        )
        super().__init__(session, search_service=self._highlights_service)
        self.setTitle(tr("Match highlights"))

    def _open_initial_search(self):
        if self._closed:
            return
        query, self._match_query = self._match_query, ""
        if query:
            self._query_entered(query)

    def _verified_status(self):
        response = self._pages.get(self._page) or {}
        if response and not self._busy and not response.get("results") and not response.get("error"):
            self._fit_youtube_caption(
                "status", tr("No date-verified highlights found for this match."), 22, minimum=11,
            )

    @aux_refresh_after
    def _render(self):
        super()._render()
        self._fit_youtube_caption("section", tr("Match highlights"), 27, minimum=13)
        self._verified_status()

    def _search_update(self, response):
        super()._search_update(response)
        self._verified_status()

    def _render_preview(self):
        super()._render_preview()
        if self._closed or self._preview_owns_service():
            return
        entries = (self._pages.get(self._page) or {}).get("results") or ()
        if 0 <= self._selected < len(entries):
            selected = entries[self._selected]
            if selected.get("playback_url"):
                self._fit_youtube_caption("preview_badge", selected["channel"], 22)

    def _play_video(self, video, context=""):
        if "playback_url" not in video:
            video_id = video["id"]
            if self._play_token and self._pending_play_id == video_id:
                return
            if self._play_token:
                self._service.cancel_play(self._play_token)
                self._play_token = ""
            try:
                job = self._service.play(video_id, video["title"], context, _request_owner=self)
                self._play_token = job["token"]
            except Exception as error:
                log_event("matches", "Could not start the YouTube highlight", error)
                self._set_status(tr("Could not start the player."))
                return
            self._pending_play_id = video_id
            self._set_status(tr("Please wait"))
            self._poll_timer.start(100, True)
            return
        url = aspor_media_url(video["playback_url"])
        if not url or not video.get("match_date_verified"):
            self._set_status(tr("Could not open the stream."))
            return

        try:
            from .browser_player import GTExternalPlayerScreen, build_extplayer_reference
            from .content import ContentItem
            from .settings import load_player_settings
            from .youtube_startup import registered_youtube_services

            engines = registered_youtube_services()
            selected_engine = load_player_settings().service_type_for("movie")
            engine = selected_engine if selected_engine in engines else 4097
            if engine not in engines:
                self._set_status(tr("Could not start the player."))
                return
            item = ContentItem("movie", video["id"], video["title"], extension="mp4")
            reference = build_extplayer_reference(url, item.name, "movie", service_type=engine)
            self.session.open(GTExternalPlayerScreen, reference, item)
        except Exception as error:
            log_event("matches", "Could not open the highlight player", error)
            self._set_status(tr("Could not start the player."))

    def retry(self):
        if not self._closed and not self._busy:
            self._query_entered(self._seed_query)

    def open_keyboard(self):
        self.retry()

    def open_history(self):
        self.retry()

    def open_playback_history(self):
        self.retry()

    def _stop(self):
        super()._stop()
        self._highlights_service.close()

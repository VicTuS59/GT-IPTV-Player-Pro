# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Non-blocking current-programme posters beneath the live guide's mini-TV."""

import time

from .background import attach_pixmap
from .diagnostics import log_event
from .i18n import metadata_language
from .live_programme_art import POSTER_DWELL_MS, POSTER_RETRY_SECONDS, programme_query, shared_programme_posters


def live_guide_side_geometry(width, height, px):
    """Share guide columns, artwork and scroll geometry across framebuffers."""
    left_x, left_w = px(8), px(414)
    list_x = left_x + left_w + px(10)
    right_w = px(420)
    right_x = width - right_w - px(8)
    list_w = right_x - px(10) - list_x
    footer_y = height - px(112)
    summary_y = px(762)
    summary_h = max(1, footer_y - px(24) - summary_y)
    preview_x, preview_y = right_x + px(18), px(114)
    preview_w, preview_h = right_w - px(36), px(242)
    frame_pad = px(8)
    poster_top = preview_y + preview_h + px(20)
    available_h = max(1, footer_y - px(16) - poster_top)
    available_w = max(1, preview_w)
    poster_w = max(1, min(available_w, int(available_h * 2.0 / 3.0)))
    poster_h = max(1, min(available_h, int(round(poster_w * 1.5))))
    return {
        "left_panel": (left_x, px(88), left_w, footer_y - px(96)),
        "list_panel": (list_x, px(88), list_w, footer_y - px(96)),
        "right_panel": (right_x, px(88), right_w, footer_y - px(96)),
        "list_scrollbar": (list_x + list_w - px(9), px(166), px(4), px(774)),
        "preview": (preview_x, preview_y, preview_w, preview_h),
        "preview_frame": (preview_x - frame_pad, preview_y - frame_pad,
                          preview_w + 2 * frame_pad, preview_h + 2 * frame_pad),
        "poster": (right_x + (right_w - poster_w) // 2,
                   poster_top, poster_w, poster_h),
        "summary": (left_x + px(26), summary_y, left_w - px(52), summary_h),
        "summary_pagination": (left_x + left_w - px(70), summary_y + summary_h + px(3),
                               px(55), px(18)),
        "summary_scrollbar": (left_x + left_w - px(20), summary_y, px(5), summary_h),
    }


class LiveProgrammePosterMixin(object):
    def _init_programme_poster(self, timer_factory, connect_timer, settings):
        self._programme_poster_options = {
            "enabled": bool(getattr(settings, "metadata_enabled", False)),
            "api_key": str(getattr(settings, "tmdb_api_key", "") or "").strip()[:512],
            "language": metadata_language(),
        }
        self._programme_poster_key = None
        self._programme_poster_query = None
        self._programme_poster_request = None
        self._programme_poster_generation = 0
        self._programme_poster_visible = False
        self._programme_poster_retries = 0
        self._programme_poster_cache_recovery = False
        self._programme_poster_started_at = 0.0
        self._programme_poster_diagnostic_state = None
        self._programme_poster_dwell = timer_factory()
        self._programme_poster_poll = timer_factory()
        connect_timer(self._programme_poster_dwell, self._programme_poster_start)
        connect_timer(self._programme_poster_poll, self._programme_poster_tick)
        log_event("programme_art", "stage=init enabled={} tmdb_key={} language={}".format(
            self._programme_poster_options["enabled"], bool(self._programme_poster_options["api_key"]),
            self._programme_poster_options["language"]))

    def _programme_poster_target(self):
        if (self._closed or self._fullscreen_active
                or getattr(self, "_live_modal_active", False) or not self._entries):
            return None, None
        key = self._selection_key(self._entries[self.selected_index])
        if key != self._detail_selection_key:
            return None, None
        query = programme_query(self._preview_epg_events)
        return ((key, query["event"]), query) if query else (None, None)

    def _restore_programme_picon(self):
        # Retain the component for older skins, but the large logo slot has
        # become the upcoming schedule. Missing posters leave the right empty.
        self["detail_picon"].hide()

    def _cancel_programme_poster(self):
        self._programme_poster_generation += 1
        self._programme_poster_dwell.stop()
        self._programme_poster_poll.stop()
        request = self._programme_poster_request
        self._programme_poster_request = None
        if request is not None:
            request.cancel()
        self._programme_poster_key = self._programme_poster_query = None
        self._programme_poster_visible = False
        self._programme_poster_retries = 0
        self._programme_poster_cache_recovery = False
        self._programme_poster_started_at = 0.0
        self._close_live_pixmap_loader(self, "live-programme-poster")
        self["detail_programme_poster"].hide()
        self._restore_programme_picon()

    def _schedule_programme_poster(self):
        options = self._programme_poster_options
        key, query = self._programme_poster_target()
        if not options["enabled"]:
            key = query = None
        state = (bool(options["enabled"]), query["key"] if query else "")
        if state != self._programme_poster_diagnostic_state:
            self._programme_poster_diagnostic_state = state
            log_event("programme_art", "stage=selection outcome={} title={}".format(
                "disabled" if not options["enabled"] else "scheduled" if query else "no-current-epg-or-inactive",
                query["title"] if query else ""))
        if key == self._programme_poster_key:
            return
        self._cancel_programme_poster()
        self._programme_poster_key, self._programme_poster_query = key, query
        if key is not None:
            self._programme_poster_started_at = time.monotonic()
            # This RAM-only lookup bypasses both dwell and network workers.
            # The decoder validates availability; a pruned file falls back
            # once to the ordinary bounded background request.
            path, source = shared_programme_posters().cached(query, options)
            if path:
                log_event("programme_art", "stage=image source={} outcome=memory-ready title={}".format(
                    source, query["title"]))
                self._apply_programme_poster(path, key)
                return
            self._programme_poster_dwell.start(POSTER_DWELL_MS, True)

    def _programme_poster_start(self):
        key, query = self._programme_poster_target()
        if key is None or key != self._programme_poster_key:
            self._schedule_programme_poster()
            return
        self._programme_poster_request = shared_programme_posters().submit(
            key, query, self._programme_poster_options,
        )
        if self._programme_poster_request.done.is_set():
            self._programme_poster_tick()
        else:
            self._programme_poster_poll.start(100, True)

    def _programme_poster_tick(self):
        request = self._programme_poster_request
        key, unused_query = self._programme_poster_target()
        if request is None:
            return
        if key is None or key != request.context or key != self._programme_poster_key:
            self._schedule_programme_poster()
            return
        if request.done.is_set():
            self._programme_poster_request = None
            if request.path:
                self._apply_programme_poster(request.path, key)
            else:
                self._retry_programme_poster(request)
            return
        if request.active():
            self._programme_poster_poll.start(100, True)
        else:
            if not request.cancelled.is_set():
                request.retryable = True
            self._retry_programme_poster(request)
            request.cancel()
            self._programme_poster_request = None

    def _retry_programme_poster(self, request):
        key, unused_query = self._programme_poster_target()
        if (not request.retryable or self._programme_poster_retries >= 1
                or key is None or key != request.context or key != self._programme_poster_key
                or not self._programme_poster_options["enabled"]):
            return
        self._programme_poster_retries += 1
        delay = max(POSTER_RETRY_SECONDS, request.retry_after - time.monotonic())
        log_event("programme_art", "stage=retry delay_seconds={} title={}".format(
            int(delay), request.query["title"]))
        self._programme_poster_dwell.start(max(1, int(delay * 1000)), True)

    def _apply_programme_poster(self, path, key):
        generation = self._programme_poster_generation
        decode_started = time.monotonic()

        def loaded(success):
            current, unused_query = self._programme_poster_target()
            if generation != self._programme_poster_generation:
                return
            if key != current or key != self._programme_poster_key:
                # ePicLoad shows its widget immediately before this callback.
                # A programme boundary can precede the next clock tick; clear
                # that decoded frame now instead of briefly showing old art.
                self._schedule_programme_poster()
                return
            self._programme_poster_visible = bool(success)
            now = time.monotonic()
            selected_at = getattr(self, "_detail_selection_started_at", 0.0) or decode_started
            poster_started = self._programme_poster_started_at or decode_started
            log_event("programme_art", "stage=decode outcome={} decode_ms={} poster_ms={} selection_ms={} title={}".format(
                "shown" if success else "failed", int((now - decode_started) * 1000),
                int((now - poster_started) * 1000), int((now - selected_at) * 1000),
                self._programme_poster_query["title"]
                if self._programme_poster_query else ""))
            if success:
                self["detail_programme_poster"].show()
                self["detail_picon"].hide()
            else:
                self["detail_programme_poster"].hide()
                self._restore_programme_picon()
                shared_programme_posters().forget(
                    self._programme_poster_query, self._programme_poster_options, path,
                )
                if not self._programme_poster_cache_recovery:
                    self._programme_poster_cache_recovery = True
                    self._programme_poster_dwell.start(1, True)

        # The existing decoder fits the complete portrait to this widget;
        # transparent padding preserves the poster's original aspect ratio.
        attach_pixmap(self, "detail_programme_poster", path,
                      key="live-programme-poster", on_loaded=loaded)

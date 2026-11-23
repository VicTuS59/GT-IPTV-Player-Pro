# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Playable YouTube results for a title selected in Discover."""

from functools import partial
from threading import Event
from typing import NamedTuple

from enigma import eTimer
from Components.Label import Label
from Screens.Screen import Screen

from .action_map import ActionMap
from .i18n import _
from .remote_footer import footer_item, install_remote_footer, prepared_skin
from .selected_row_scroll import clear_selection
from .source_loader import SourceLoader
from .typography import fit_dynamic_text
from .youtube_live_ui import _BroadcastList, _connect_timer, _live_skin, _scale, _YouTubePlaybackActions
from .youtube_movie_discovery import discover_youtube_movie
from .youtube_series_discovery import discover_series_episodes


_FOOTER = (
    footer_item("up_down", "select"),
    footer_item("channel", "page"),
    footer_item("ok", "play"),
    footer_item("green", "refresh"),
    footer_item("exit", "back"),
)


class _ResultRow(NamedTuple):
    video_id: str
    title: str
    channel: str = ""


def _results_skin():
    return _live_skin("GTYouTubeSeriesScreen", _FOOTER)


class GTYouTubeSeriesScreen(_YouTubePlaybackActions, Screen):
    def __init__(self, session, service, selection, client, country=""):
        self.skin = prepared_skin(_results_skin)
        Screen.__init__(self, session)
        self._closed = self._started = False
        self._selected_title = dict(selection)
        self._client = client
        self._country = country
        self._rows = ()
        self._list_message = self._action_message = ""
        self._cancel = None
        self._reader = SourceLoader(eTimer(), _connect_timer, self._loaded)
        self._init_playback(service)

        heading = _("Movies") if self._selected_title.get("media_type") == "movie" else _("Episodes")
        title = "YouTube · " + heading
        self["brand_gt"] = Label("GT")
        self["brand_name"] = Label("IPTV PLAYER PRO")
        self["brand_divider"] = Label("")
        self["title"] = Label(title)
        self["language"] = Label("")
        self["rule"] = Label("")
        self["section"] = Label("")
        self["status"] = Label("")
        self["channels"] = _BroadcastList(*_scale())
        self["actions"] = ActionMap(
            ["OkCancelActions", "DirectionActions", "ColorActions", "MoviePlayerActions",
             "ChannelSelectBaseActions", "InfobarChannelSelection"], {
                "ok": self._select,
                "cancel": self.close,
                "back": self.close,
                "green": self._refresh,
                "up": partial(self._move, "up"),
                "down": partial(self._move, "down"),
                "upRepeated": partial(self._move, "up"),
                "downRepeated": partial(self._move, "down"),
                "left": partial(self._move, "pageUp"),
                "right": partial(self._move, "pageDown"),
                "channelUp": partial(self._move, "pageUp"),
                "channelDown": partial(self._move, "pageDown"),
                "prevBouquet": partial(self._move, "pageUp"),
                "nextBouquet": partial(self._move, "pageDown"),
            }, -1)
        install_remote_footer(self, _FOOTER, fit_horizontal=True)
        self["channels"].onSelectionChanged.append(self._scroll_selection)
        self.onLayoutFinish.append(self._layout_ready)
        self.onShown.append(self._shown)
        self.onClose.append(self._stop)
        self.setTitle(title)

    def _layout_ready(self):
        if getattr(self, "_closed", True):
            return
        self["channels"].set_plain_font()
        fit_dynamic_text(self["section"], self._selected_title["title"], fallback_chars=60)

    def _shown(self):
        if getattr(self, "_closed", True):
            return
        self._resume_playback()
        if not self._started:
            self._started = True
            self._refresh()

    def _refresh(self):
        if getattr(self, "_closed", True) or self._play_token:
            return
        if self._cancel is not None:
            self._cancel.set()
        self._cancel = Event()
        self._list_message = _("Results are loading")
        self._set_action_message("")
        lookup = discover_youtube_movie if self._selected_title.get("media_type") == "movie" else discover_series_episodes
        self._reader.request(partial(lookup, self._client, self._selected_title, self._country, self._cancel))

    def _loaded(self, items, error):
        if getattr(self, "_closed", True) or self._cancel is None:
            return
        self._cancel = None
        if error is not None:
            if self._selected_title.get("media_type") == "tv" and getattr(error, "code", None) == "timeout":
                self._list_message = _("Series request timed out")
            else:
                self._list_message = _("Temporarily unavailable")
        elif items:
            self._list_message = ""
            self._render(tuple(_ResultRow(item.video_id, item.title) for item in items))
        else:
            self._list_message = _("No results found")
            self._render(())
        self._update_status()

    def _render(self, rows):
        index = self._selected_index()
        selected = self._rows[index].video_id if index is not None else ""
        clear_selection(self)
        self._rows = rows
        menu = self["channels"]
        menu.set_broadcasts(rows)
        if rows:
            target = next((i for i, row in enumerate(rows) if row.video_id == selected), 0)
            menu.moveToIndex(target)
        fit_dynamic_text(self["language"], str(len(rows)) if rows else "", fallback_chars=10)
        self._scroll_selection()

    def _selected_index(self):
        index = self["channels"].getSelectedIndex()
        return index if isinstance(index, int) and 0 <= index < len(self._rows) else None

    def _scroll_selection(self):
        if getattr(self, "_closed", True):
            return
        index = self._selected_index()
        if index is None:
            clear_selection(self)
        else:
            self["channels"].scroll_selected_title(self, index, self._rows[index].title)

    def _set_action_message(self, message):
        self._action_message = message
        self._update_status()

    def _update_status(self):
        fit_dynamic_text(self["status"], self._action_message or self._list_message,
                         max_lines=2, fallback_chars=80)

    def _select(self):
        if getattr(self, "_closed", True) or self._play_token:
            return
        index = self._selected_index()
        if index is not None:
            item = self._rows[index]
            # Preserve the selected episode while the player is starting.
            if self._cancel is not None:
                self._cancel.set()
                self._cancel = None
                self._reader.cancel()
                self._list_message = ""
            self._play_video(item.video_id, item.title)

    def _move(self, method):
        if getattr(self, "_closed", True) or not self._rows:
            return
        self._cancel_play_request()
        self._set_action_message("")
        getattr(self["channels"], method)()

    def _stop(self):
        self._closed = True
        if self._cancel is not None:
            self._cancel.set()
            self._cancel = None
        try:
            self._reader.close()
        finally:
            self._stop_playback()

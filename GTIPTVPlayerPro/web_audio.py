# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Paired web control for audio tracks already supplied by the VOD service.

Never send a service reference, media URL or provider credentials to the
browser. Selection tokens bind the decoder track list to one playing item.
"""

import secrets
import threading
import time


MAX_TRACKS = 64
TOKEN_LIFETIME = 90


class AudioError(Exception):
    def __init__(self, code, status=400):
        self.code = code
        self.status = status
        self.message = code.replace("_", " ")
        Exception.__init__(self, self.message)


def _active_player(session):
    player = getattr(session, "current_dialog", None)
    item = getattr(player, "current_item", None)
    if (player is None or item is None or getattr(player, "_closed", False) or
            not getattr(player, "_started", False) or
            getattr(item, "content_type", "") not in ("movie", "series")):
        return None
    return player


def _track_fingerprint(player, info):
    try:
        return tuple(player._audio_track_fingerprint(info))
    except Exception:
        return ()


def _label(player, info, index):
    try:
        label = str(player._audio_track_label(info, index))[:120]
    except Exception:
        label = "{}. Audio track".format(index + 1)
    # Some decoders put the source URI in the description. Keep it on the box.
    if "://" in label or "token=" in label.lower() or "password=" in label.lower():
        label = "{}. Audio track".format(index + 1)
    return label


class WebAudio(object):
    def __init__(self):
        self.lock = threading.Lock()
        self.select_lock = threading.Lock()
        self.tokens = {}

    def current(self):
        from .web_remote import WEB_REMOTE

        def snapshot():
            player = _active_player(WEB_REMOTE.session)
            if player is None:
                return {"playing": False, "tracks": []}
            try:
                context = player._audio_selection_context()
                unused_service, tracks, count = player._audio_tracks()
            except Exception:
                tracks, count = None, 0
            count = max(0, int(count))
            can_select = (tracks is not None and count > 1 and
                          callable(getattr(tracks, "selectTrack", None)))
            current_index = -1
            if tracks is not None:
                try:
                    current_index = int(tracks.getCurrentTrack())
                except (AttributeError, TypeError, ValueError, OverflowError):
                    pass
            entries, fingerprints = [], []
            for index in range(min(count, MAX_TRACKS)):
                try:
                    info = tracks.getTrackInfo(index)
                except Exception:
                    info = None
                fingerprints.append(_track_fingerprint(player, info))
                entries.append({"index": index, "name": _label(player, info, index),
                                "selected": index == current_index})
            item = player.current_item
            title = str(getattr(item, "name", "") or "")[:120].strip()
            if "://" in title:
                title = ""
            token = ""
            if can_select:
                token = secrets.token_urlsafe(18)
                with self.lock:
                    now = time.monotonic()
                    self.tokens = {key: value for key, value in self.tokens.items()
                                   if now - value[0] < TOKEN_LIFETIME}
                    self.tokens[token] = (now, context, count, fingerprints)
                    # Bound memory even when the web page is refreshed often.
                    if len(self.tokens) > 16:
                        oldest = min(self.tokens, key=lambda key: self.tokens[key][0])
                        self.tokens.pop(oldest, None)
            return {"playing": True, "title": title or "Movie", "tracks": entries,
                    "selectable": bool(can_select), "token": token,
                    "truncated": count > MAX_TRACKS}

        return WEB_REMOTE._run_on_gui(snapshot)

    def select(self, payload):
        if not isinstance(payload, dict):
            raise AudioError("audio_invalid_track", 400)
        token, index = payload.get("token"), payload.get("index")
        if (not isinstance(token, str) or len(token) > 64 or
                type(index) is not int or index < 0 or index >= MAX_TRACKS):
            raise AudioError("audio_invalid_track", 400)
        if not self.select_lock.acquire(False):
            raise AudioError("audio_busy", 409)
        try:
            with self.lock:
                saved = self.tokens.get(token)
            if saved is None or time.monotonic() - saved[0] >= TOKEN_LIFETIME:
                raise AudioError("audio_tracks_changed", 409)
            unused_created, context, expected_count, fingerprints = saved
            if index >= len(fingerprints):
                raise AudioError("audio_invalid_track", 400)
            from .web_remote import WEB_REMOTE

            def activate():
                player = _active_player(WEB_REMOTE.session)
                if player is None or player._audio_selection_context() != context:
                    raise AudioError("audio_tracks_changed", 409)
                if (getattr(player, "_audio_menu_open", False) or
                        getattr(player, "_media_menu_open", False) or
                        getattr(player, "_pending_engine_reference", None) is not None):
                    raise AudioError("audio_busy", 409)
                unused_service, tracks, count = player._audio_tracks()
                if tracks is None or count != expected_count or index >= count:
                    raise AudioError("audio_tracks_changed", 409)
                try:
                    info = tracks.getTrackInfo(index)
                except Exception:
                    info = None
                if _track_fingerprint(player, info) != fingerprints[index]:
                    raise AudioError("audio_tracks_changed", 409)
                selector = getattr(tracks, "selectTrack", None)
                if not callable(selector):
                    raise AudioError("audio_selection_unavailable", 409)
                try:
                    selector(index)
                except Exception:
                    raise AudioError("audio_selection_failed", 409)
                # A decoder may report the old track until its switch settles.
                # The browser refreshes the actual current track separately.
                return {"requested_index": index}

            return WEB_REMOTE._run_on_gui(activate)
        finally:
            self.select_lock.release()


WEB_AUDIO = WebAudio()


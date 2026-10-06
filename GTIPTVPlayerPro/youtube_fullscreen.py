# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Use the ordinary GT player controls without reopening the mini-TV stream."""

from .browser import GTExternalPlayerScreen
from .youtube_preview import same_reference


class GTYouTubeFullscreenScreen(GTExternalPlayerScreen):
    def __init__(self, session, reference, item, preview_owner, **kwargs):
        self._youtube_preview_owner = preview_owner
        self._youtube_preview_kept = False
        kwargs.update(keep_playing_on_exit=True, adopt_playing_service=True,
                      old_reference_override=preview_owner._preview_old_reference)
        GTExternalPlayerScreen.__init__(self, session, reference, item, **kwargs)

    def set_youtube_end_callback(self, callback):
        GTExternalPlayerScreen.set_youtube_end_callback(self, callback)
        owner = self._youtube_preview_owner
        watcher = self._youtube_end_watcher
        if watcher is not None and same_reference(self.reference, owner.reference):
            if owner._preview_play_started_at:
                watcher.started = owner._preview_play_started_at
            watcher.ended = owner._preview_ended

    def start_playback(self):
        if self._closed or self._started:
            return
        owner = self._youtube_preview_owner
        if owner._closed or not same_reference(self.reference, owner._preview_current_reference()):
            self.close({"youtube_preview_kept": False})
            return
        self.old_reference = owner._preview_old_reference
        self._old_reference_restored = False
        self._started = True
        self._generation += 1
        self._after_zap()

    def return_to_list(self):
        if self._pending_engine_reference is not None or self._live_reconnecting:
            self.show_info()
            return
        self._save_resume_position()
        owner = self._youtube_preview_owner
        kept = self._started and owner._adopt_youtube_return(self)
        if not kept and not self.stop_playback():
            self.show_info()
            return
        self._youtube_preview_kept = bool(kept)
        if kept:
            self._started = False
        self.close({"youtube_preview_kept": bool(kept)})

    def _on_close(self):
        # Also handle external closes. Red/STOP has already released playback;
        # EXIT and ordinary closes transfer it back to the result page.
        owner = self._youtube_preview_owner
        if self._started and not self._youtube_preview_kept:
            self._save_resume_position()
            self._youtube_preview_kept = owner._adopt_youtube_return(self)
            if self._youtube_preview_kept:
                self._started = False
        if not self._youtube_preview_kept:
            self.keep_playing_on_exit = False
        GTExternalPlayerScreen._on_close(self)

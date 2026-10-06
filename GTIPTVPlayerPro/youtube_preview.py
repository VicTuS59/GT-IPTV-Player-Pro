# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""One decoder shared by the YouTube result page and its fullscreen player."""

from enigma import ePoint, eSize, eTimer

from .i18n import _
from .main import _connect_timer
from .youtube_playback import YouTubeEndWatcher, youtube_duration_seconds


def same_reference(left, right):
    if left is None or right is None:
        return False
    if left is right:
        return True
    try:
        return left.toString() == right.toString()
    except (AttributeError, TypeError):
        return False


class YouTubePreviewMixin(object):
    """Playback starts only after OK, never after moving the selection."""

    def _init_youtube_preview(self, video_available):
        self._youtube_preview = True
        self._preview_available = bool(video_available)
        self._started = False
        self.reference = None
        self.current_item = None
        self._preview_old_reference = None
        self._preview_old_captured = False
        self._preview_resume_store = None
        self._preview_resume_position = 0
        self._preview_resume_attempts = 0
        self._preview_geometry = None
        self._preview_window_applied = False
        self._fullscreen_active = False
        self._fullscreen_dialog = None
        self._youtube_end_watcher = None
        self._preview_play_started_at = 0.0
        self._preview_ended = False
        self._preview_timer = eTimer()
        _connect_timer(self._preview_timer, self._preview_tick)

    def _preview_current_reference(self):
        try:
            return self.session.nav.getCurrentlyPlayingServiceReference()
        except Exception:
            return None

    def _preview_owns_service(self):
        return bool(self._started and same_reference(
            self.reference, self._preview_current_reference()))

    def _preview_is_foreground(self):
        return bool(not self._closed and not self._fullscreen_active
                    and self.session.current_dialog is self)

    def _preview_seek(self):
        if not self._preview_owns_service():
            return None
        try:
            service = self.session.nav.getCurrentService()
            return service.seek() if service is not None else None
        except Exception:
            return None

    @staticmethod
    def _preview_seconds(result):
        try:
            error, ticks = result
            return int(ticks) // 90000 if not error and ticks >= 0 else -1
        except (TypeError, ValueError, OverflowError):
            return -1

    def _seek_position(self):
        length = youtube_duration_seconds(self.current_item)
        seeker = self._preview_seek()
        if seeker is None:
            return -1, length or -1
        try:
            position = self._preview_seconds(seeker.getPlayPosition())
        except Exception:
            position = -1
        try:
            native_length = self._preview_seconds(seeker.getLength())
            if native_length > 0:
                length = native_length
        except Exception:
            pass
        return position, length or -1

    def _save_resume_position(self):
        if (not self._preview_owns_service() or self._fullscreen_active
                or self._preview_resume_position > 0
                or self._preview_resume_store is None):
            return
        position, length = self._seek_position()
        if getattr(self._youtube_end_watcher, "ended", False):
            position = length
        try:
            self._preview_resume_store.save(
                self.current_item.stream_id, position, length)
        except (OSError, AttributeError):
            pass

    def replace_web_video(self, reference, item, resume_store=None, start_position=0):
        """The resolver hands the same native video/audio reference to mini TV."""
        if not self._preview_available or not self._preview_is_foreground():
            return False
        self._save_resume_position()
        previous = self._preview_current_reference()
        if not self._preview_old_captured or not self._preview_owns_service():
            self._preview_old_reference = previous
            self._preview_old_captured = True
        try:
            self.session.nav.playService(reference)
        except Exception:
            # A failed start may release the decoder. Restore only that slot,
            # and never replace a different service opened in the meantime.
            current = self._preview_current_reference()
            if previous is not None and (current is None or same_reference(current, reference)):
                try:
                    self.session.nav.playService(previous)
                except Exception:
                    pass
            return False
        self.reference = reference
        self.current_item = item
        self._preview_resume_store = resume_store
        self._preview_resume_position = max(0, int(start_position or 0))
        self._preview_resume_attempts = 0
        self._started = True
        self._preview_window_applied = False
        self._preview_play_started_at = 0.0
        self._preview_ended = False
        self._render_preview()
        self._resume_youtube_window()
        self._preview_timer.start(200, True)
        return True

    def set_youtube_end_callback(self, callback):
        if self._youtube_end_watcher is not None:
            self._youtube_end_watcher.close()
        self._youtube_end_watcher = YouTubeEndWatcher(self, callback)
        if self._preview_play_started_at:
            self._youtube_end_watcher.started = self._preview_play_started_at
        else:
            self._preview_play_started_at = self._youtube_end_watcher.started
        self._youtube_end_watcher.ended = self._preview_ended

    def _capture_youtube_geometry(self):
        if not self._preview_available:
            return
        try:
            instance = self["preview_video"].instance
            position, size = instance.position(), instance.size()
            geometry = (position.x(), position.y(), size.width(), size.height())
            if geometry[2] > 0 and geometry[3] > 0:
                self._preview_geometry = geometry
        except Exception:
            pass

    def _resume_youtube_window(self):
        if not self._preview_is_foreground() or not self._preview_owns_service():
            return
        if self._preview_window_applied:
            return
        if self._preview_geometry is None:
            self._capture_youtube_geometry()
        try:
            self["preview_video"].show()
            instance = self["preview_video"].instance
            if self._preview_geometry is not None:
                left, top, width, height = self._preview_geometry
                instance.resize(eSize(width, height))
                # Some drivers ignore a move to the unchanged coordinates.
                instance.move(ePoint(left + 1, top))
                instance.move(ePoint(left, top))
            self["preview_mask"].hide()
            self["preview_art"].hide()
            self._preview_window_applied = True
        except Exception:
            pass

    def _suspend_youtube_window(self):
        self._preview_window_applied = False
        if not self._preview_available:
            return
        try:
            instance = self["preview_video"].instance
            restore = getattr(instance, "restoreFullsize", None)
            if callable(restore):
                restore()
            from .main import _desktop_size
            width, height = _desktop_size()
            instance.resize(eSize(width, height))
            instance.move(ePoint(0, 0))
            self["preview_video"].hide()
        except Exception:
            pass

    def _preview_tick(self):
        if self._closed or self._fullscreen_active or not self._preview_owns_service():
            return
        if self._preview_is_foreground():
            self._resume_youtube_window()
            position, length = self._seek_position()
            if self._youtube_end_watcher is not None:
                self._youtube_end_watcher.sample(position, length)
            if self._preview_resume_position > 0:
                seeker = self._preview_seek()
                self._preview_resume_attempts += 1
                if seeker is not None:
                    try:
                        target = self._preview_resume_position
                        if position >= target - 2 or seeker.seekTo(target * 90000) == 0:
                            self._preview_resume_position = 0
                    except Exception:
                        pass
                # Do not overwrite a saved bookmark if startup seeking fails.
                if self._preview_resume_attempts >= 60:
                    self._preview_timer.start(1000, True)
                    return
        self._preview_timer.start(250 if self._preview_resume_position else 1000, True)

    def _open_youtube_fullscreen(self, allow_pending_resume=False):
        if (not self._preview_is_foreground() or not self._preview_owns_service()
                or self._preview_resume_position > 0 and not allow_pending_resume):
            return False
        from .youtube_fullscreen import GTYouTubeFullscreenScreen
        self._save_resume_position()
        self._preview_timer.stop()
        if self._youtube_end_watcher is not None:
            self._preview_ended = self._youtube_end_watcher.ended
            self._youtube_end_watcher.close()
            self._youtube_end_watcher = None
        self._fullscreen_active = True
        self._suspend_youtube_window()
        try:
            dialog = self.session.openWithCallback(
                self._youtube_fullscreen_closed, GTYouTubeFullscreenScreen,
                self.reference, self.current_item, preview_owner=self,
                resume_store=self._preview_resume_store,
                resume_key_value=self.current_item.stream_id,
                start_position=self._preview_resume_position if allow_pending_resume else 0)
            if dialog is None:
                raise RuntimeError("fullscreen screen unavailable")
            self._fullscreen_dialog = dialog
            if not self._service.adopt_preview_fullscreen(self, dialog):
                dialog.close()
                raise RuntimeError("preview ownership changed")
            return True
        except Exception:
            self._fullscreen_active = False
            self._fullscreen_dialog = None
            self.set_youtube_end_callback(self._service._video_ended)
            self._resume_youtube_window()
            self._preview_timer.start(250, True)
            self["status"].setText(_("Could not start the player."))
            return False

    def _adopt_youtube_return(self, dialog):
        """Return the decoder and current item, including web/autoplay changes."""
        if self._closed or not same_reference(dialog.reference, self._preview_current_reference()):
            return False
        self.reference = dialog.reference
        self.current_item = dialog.current_item
        self._preview_resume_store = dialog._resume_store
        self._preview_resume_position = max(0, int(getattr(dialog, "_resume_start_position", 0) or 0))
        self._preview_resume_attempts = 0
        watcher = getattr(dialog, "_youtube_end_watcher", None)
        if watcher is not None:
            self._preview_play_started_at = watcher.started
            self._preview_ended = watcher.ended
        self._started = True
        return True

    def _youtube_fullscreen_closed(self, result=None):
        dialog = self._fullscreen_dialog
        kept = bool(isinstance(result, dict) and result.get("youtube_preview_kept")
                    or getattr(dialog, "_youtube_preview_kept", False))
        self._fullscreen_active = False
        self._fullscreen_dialog = None
        if self._closed:
            return
        if not kept:
            self._started = False
            self.reference = self.current_item = None
        self._render_preview()
        self._preview_window_applied = False
        self._resume_youtube_window()
        if self._preview_owns_service():
            self._preview_timer.start(200, True)

    def _close_youtube_preview(self):
        self._preview_timer.stop()
        if self._youtube_end_watcher is not None:
            self._youtube_end_watcher.close()
            self._youtube_end_watcher = None
        owned = self._preview_owns_service() and not self._fullscreen_active
        if owned:
            self._save_resume_position()
        self._suspend_youtube_window()
        if owned:
            try:
                self.session.nav.stopService()
                if self._preview_old_reference is not None:
                    self.session.nav.playService(self._preview_old_reference)
            except Exception:
                pass
        self._started = False

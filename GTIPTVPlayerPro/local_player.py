# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account-free local playback using the configured movie/series engine."""
import time

from enigma import eTimer, iServiceInformation
from Screens.MessageBox import MessageBox

from .browser import (GTExternalPlayerScreen, GTStreamListScreen,
                      _connect_timer, reference_service_type)
from .diagnostics import log_event
from .i18n import _
from .live_recovery import media_snapshot

try:
    from enigma import iPlayableService
except ImportError:
    iPlayableService = None


class GTLocalPlayerScreen(GTExternalPlayerScreen):
    skinName = "GTExternalPlayerScreen"

    def __init__(self, session, reference, item, media_info=None):
        self._local_media = dict(media_info or {})
        self._local_checked = False
        self._local_failed = False
        self._local_closing = False
        self._local_started_at = 0.0
        self._local_position = -1
        self._local_length = -1
        self._local_samples = 0
        self._local_rewound = False
        self._local_error_event = ""
        self._local_events = None
        self._local_video_pts = None
        self._local_pts_samples = 0
        # Deliberately no account, resume store, or provider URL resolution.
        GTExternalPlayerScreen.__init__(self, session, reference, item)
        self._local_timer = eTimer()
        _connect_timer(self._local_timer, self._local_tick)
        # Screen.onClose runs after Session.close has already ended modal
        # execution. Cancel at execEnd as well, including child-dialog opens.
        # Leaving the player is manual control: do not resume the startup
        # probe or perform a delayed seek when focus returns.
        self.onExecEnd.append(self._local_cleanup)
        self.onClose.insert(0, self._local_cleanup)

    def _local_log(self, reason, position=-1, length=-1):
        log_event("local_playback", "engine={} format={} bytes={} position={} length={} outcome={}".format(
            reference_service_type(self.reference, 4097), self._local_media.get("format", "unknown"),
            self._local_media.get("size", 0), position, length, reason))

    def start_playback(self):
        if (self._closed or self._local_closing or self._started
                or self._local_failed or self._local_checked):
            return
        GTExternalPlayerScreen.start_playback(self)
        if not self._local_is_active() or self._local_checked:
            return
        self._local_started_at = time.monotonic()
        self._local_log("opening")
        events = getattr(getattr(self.session, "nav", None), "event", None)
        if events is not None and hasattr(events, "append"):
            events.append(self._local_event)
            self._local_events = events
        self._local_timer.start(500, True)

    def _local_is_active(self):
        session = getattr(self, "session", None)
        return (not getattr(self, "_closed", True)
                and not getattr(self, "_local_closing", True)
                and getattr(session, "current_dialog", None) is self
                and bool(getattr(session, "in_exec", False))
                and bool(getattr(self, "execing", False)))

    def _local_event(self, event):
        if getattr(self, "_closed", True) or self._local_checked or iPlayableService is None:
            return
        # Ignore the old service's stop/EOF during the decoder handoff.
        if time.monotonic() - self._local_started_at < 0.5:
            return
        getter = getattr(self.session.nav, "getCurrentlyPlayingServiceReference", None)
        try:
            current = getter() if callable(getter) else None
        except Exception:
            return
        if not GTStreamListScreen._same_service_reference(current, self.reference):
            return
        for name in ("evEOF", "evTuneFailed"):
            if event == getattr(iPlayableService, name, None):
                self._local_error_event = name

    def _local_tick(self):
        if getattr(self, "_closed", True) or self._local_checked:
            return
        if not self._local_is_active():
            self._local_cleanup()
            return
        if not self._started:
            self._local_failure("service-start-failed")
            return
        if self._paused or self._seeking or self._pending_seek_verification is not None:
            # Never counteract a manual pause or a deliberate seek.
            self._local_checked = True
            self._local_log("manual-control")
            return
        elapsed = time.monotonic() - self._local_started_at
        getter = getattr(self.session.nav, "getCurrentlyPlayingServiceReference", None)
        try:
            current = getter() if callable(getter) else None
        except Exception:
            current = None
        if current is not None and not GTStreamListScreen._same_service_reference(current, self.reference):
            # A different service now owns the decoder; do not seek or stop it.
            self._local_checked = True
            self._local_log("service-changed")
            return
        seeker = self._seek_interface()
        position, length = self._seek_position(seeker)
        # Some local TS services expose advancing decoder PTS before they
        # expose a seek interface. Do not reject visibly playing media solely
        # because a duration is not available yet.
        snapshot = media_snapshot(self._current_service(), iServiceInformation)
        pts = snapshot.get("video_pts")
        if pts is not None and self._local_video_pts is not None:
            delta = (pts - self._local_video_pts) & ((1 << 33) - 1)
            self._local_pts_samples = self._local_pts_samples + 1 if 0 < delta < 90000 * 3 else 0
        self._local_video_pts = pts
        if length > 0 and self._local_length > 0 and abs(length - self._local_length) <= 2:
            self._local_samples += 1
        else:
            self._local_samples = 1 if length > 0 else 0
        # A single bounded seek after stable duration is available. Do not
        # repeatedly seek on a timer, and never copy an online resume point.
        if not self._local_rewound and position > 3 and self._local_samples >= 2:
            absolute = getattr(seeker, "seekTo", None)
            if callable(absolute):
                self._local_rewound = True
                try:
                    result = absolute(0)
                    if self._seek_call_failed(result):
                        self._local_failure("start-seek-rejected", position, length)
                        return
                    self._local_log("start-seek-requested", position, length)
                    self._local_position = -1
                    self._local_length = length
                    self._local_error_event = ""
                    self._local_timer.start(600, True)
                    return
                except Exception:
                    self._local_failure("start-seek-failed", position, length)
                    return
        near_start = 0 <= position <= elapsed + 4
        advancing = self._local_position >= 0 and position > self._local_position
        before_end = length <= 0 or position < length - 1
        decoder_only = (position < 0 and elapsed >= 3 and self._local_pts_samples >= 3
                        and (snapshot.get("width") or 0) > 0)
        short_finished = (self._local_error_event == "evEOF" and 0 < length <= 3
                          and position >= 0 and elapsed >= length)
        if (advancing and near_start and before_end) or decoder_only or short_finished:
            self._local_checked = True
            self._local_log("playing", position, length)
            self._playback_tick()
            return
        if (self._local_error_event and elapsed >= 2) or elapsed >= 18:
            self._local_failure(self._local_error_event or "no-start-progress", position, length)
            return
        self._local_position, self._local_length = position, length
        self._local_timer.start(500, True)

    def _local_failure(self, reason, position=-1, length=-1):
        if getattr(self, "_closed", True) or self._local_failed:
            return
        if not self._local_is_active():
            self._local_cleanup()
            return
        self._local_failed = self._local_checked = True
        self._local_cleanup()
        self._local_log(reason, position, length)
        self.stop_playback()
        # Restoring the previous service can itself change the active dialog
        # (for example, parental control). Never open a modal from behind it.
        # Screen.close defers closure until this player regains execution.
        if not self._local_is_active():
            self.close()
            return
        self.session.openWithCallback(self._local_failure_closed, MessageBox,
            _("This video could not be played. Check the playback engine and file format."),
            MessageBox.TYPE_ERROR)

    def _local_failure_closed(self, unused=None):
        if not getattr(self, "_closed", True):
            self.close()

    def stop_playback(self):
        # Cancel before the base class clears _started or restores DVB;
        # otherwise a queued tick can misread a normal EXIT as a failure.
        self._local_cleanup()
        return GTExternalPlayerScreen.stop_playback(self)

    def close(self, *retval):
        if getattr(self, "_closed", True):
            return
        self._local_closing = True
        self._local_cleanup()
        # Allow Screen.execBegin to call close again when a non-current
        # screen had queued close_on_next_exec instead of closing at once.
        return GTExternalPlayerScreen.close(self, *retval)

    def _local_cleanup(self):
        # Mark finished first so an already queued callback also becomes inert.
        self._local_checked = True
        timer = getattr(self, "_local_timer", None)
        if timer is not None:
            timer.stop()
        if getattr(self, "_local_events", None) is not None:
            try:
                self._local_events.remove(self._local_event)
            except (ValueError, AttributeError):
                pass
            self._local_events = None


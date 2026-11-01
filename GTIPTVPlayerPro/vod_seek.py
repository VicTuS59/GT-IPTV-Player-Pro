"""Serialize movie/episode seeks and recover one owned, stalled transaction."""
import time

from .live_recovery import media_snapshot, reference_identity
from .diagnostics import log_event
from .vod_seek_progress import (SeekProgress, RESUME_SECONDS, ACQUIRE_SECONDS,
                                OVERALL_SECONDS, POLL_MS, RELEASE_MS)


class VodSeekSupport:
    def _vod_seek_enabled(self):
        item = self.current_item
        return (getattr(item, 'content_type', '') in ('movie', 'series') and
                not bool(getattr(item, 'youtube_native', False)))

    def _vod_seek_context(self, state):
        return (not self._closed and self._started and self._vod_seek_enabled() and
                self._generation == state.get('generation') and
                self.reference is state.get('reference') and self.current_item is state.get('item') and
                getattr(self, '_web_suspended', None) is None and
                getattr(self, '_pending_engine_reference', None) is None)

    def _vod_seek_foreground(self):
        return (getattr(self.session, 'current_dialog', self) is self and
                bool(getattr(self.session, 'in_exec', True)) and
                not any(getattr(self, name, False) for name in
                        ('_subtitle_menu_open', '_audio_menu_open', '_media_menu_open', '_minute_seek_open', '_seeking')))

    def _vod_seek_owned(self, state, released=False):
        getter = getattr(getattr(self.session, 'nav', None), 'getCurrentlyPlayingServiceReference', None)
        if not callable(getter):
            return False
        try:
            current = getter()
        except Exception:
            return False
        if current is None:
            return bool(released)
        if current is state['reference']:
            return True
        identity = reference_identity(state['reference'])
        return bool(identity and reference_identity(current) == identity)

    def _arm_vod_seek(self, state):
        now = time.monotonic()
        state.update(guard=SeekProgress(state['origin'], state['target'], now), generation=self._generation,
                     reference=self.reference, item=self.current_item, phase='check', queued_target=None,
                     nudge_used=False, replay_used=False, overall_deadline=now + OVERALL_SECONDS,
                     length=self._seek_position()[1], hold_at=now)

    def _queue_vod_seek(self, target):
        state = self._pending_seek_verification
        if not isinstance(state, dict) or 'guard' not in state or not self._vod_seek_context(state):
            return False
        state['queued_target'] = int(target)
        return True

    def _restore_vod_seek_subtitles(self, state):
        if state.get('replay_used') and not state.get('subtitles_restored'):
            state['subtitles_restored'] = True
            self._subtitle_auto_generation = self._generation
            self._subtitle_auto_timer.start(POLL_MS, True)

    def _finish_vod_seek(self, state, position):
        queued = state.get('queued_target')
        guard = getattr(self, '_resume_bookmark_guard', None)
        if guard is not None:
            guard.pop('vod_seek_unconfirmed', None)
        self._resume_bookmark_position_ready(position)
        self._cancel_seek_verification(resync_subtitles=False)
        self._playback_tick()
        observed = self._subtitle_position()
        self._complete_subtitle_seek(observed if observed >= 0 else position)
        self._restore_vod_seek_subtitles(state)
        if queued is not None and abs(int(queued) - int(state['target'])) > 1 and self._vod_seek_context(state) and self._vod_seek_owned(state):
            seeker = self._seek_interface()
            if not self._request_seek(seeker, position, queued):
                self._seek_unavailable()

    def _fail_vod_seek(self, state):
        log_event('vod_seek', 'verification-failed replay={}'.format(int(state.get('replay_used', False))))
        self._restore_vod_seek_subtitles(state)
        self._seek_unavailable()

    def _nudge_vod_seek(self, state):
        if state.get('nudge_used') or self._paused or not self._vod_seek_context(state) or not self._vod_seek_foreground() or not self._vod_seek_owned(state):
            return False
        state['nudge_used'] = True
        log_event('vod_seek', 'recovery-resume')
        state['guard'].deadline = min(state['overall_deadline'], time.monotonic() + RESUME_SECONDS)
        pauseable = self._pause_interface()
        try:
            if pauseable is not None:
                pauseable.unpause()
        except Exception:
            pass
        return True

    def _replay_vod_seek(self, state):
        if state.get('replay_used') or self._paused or not self._vod_seek_context(state) or not self._vod_seek_foreground() or not self._vod_seek_owned(state):
            return False
        state['replay_used'] = True
        log_event('vod_seek', 'recovery-replay')
        self._remember_resume_subtitle(mark_disabled=True)
        self._resume_subtitle_pending_key = self._resume_key
        self._resume_subtitle_restore_attempts = 0
        # An unsuccessful automatic replay may never replace an older bookmark.
        guard = getattr(self, '_resume_bookmark_guard', None)
        if guard is None and self._resume_key:
            guard = self._resume_bookmark_guard = dict(key=self._resume_key, target=state['target'],
                reference=self.reference, generation=self._generation,
                manual_seek=dict(origin=state['origin'], target=state.get('queued_target') if state.get('queued_target') is not None else state['target']))
        if guard is not None:
            guard['vod_seek_unconfirmed'] = True
        try:
            self.session.nav.stopService()
        except Exception:
            return False
        for timer in (self._resume_timer, self._playback_timer, self._subtitle_auto_timer):
            timer.stop()
        state['phase'] = 'release'
        state['release_at'] = time.monotonic() + RELEASE_MS / 1000.0
        self._set_static_info_epg('epg_now', self._vod_seek_translate('Reconnecting the stream...'))
        self._set_static_info_epg('epg_next', self._vod_seek_translate('Please wait'))
        self.show_info()
        self._seek_verify_timer.start(RELEASE_MS, True)
        return True

    @staticmethod
    def _vod_seek_translate(text):
        from .i18n import _
        return _(text)

    def _verify_vod_seek(self, state):
        now = time.monotonic()
        phase = state['phase']
        if not self._vod_seek_context(state) or not self._vod_seek_owned(state, released=phase in ('release', 'acquire')):
            self._cancel_seek_verification()
            return
        guard = state['guard']
        if not self._vod_seek_foreground():
            held = max(0.0, now - state['hold_at'])
            guard.hold(now)
            state['overall_deadline'] += held
            if 'acquire_deadline' in state:state['acquire_deadline'] += held
            state['hold_at'] = now
            self._seek_verify_timer.start(POLL_MS, True)
            return
        state['hold_at'] = now
        if phase == 'release':
            if now < state['release_at']:
                self._seek_verify_timer.start(RELEASE_MS, True)
                return
            try:
                self._play_subtitle_service(self.session.nav, state['reference'])
            except Exception:
                self._fail_vod_seek(state)
                return
            controller = getattr(self, '_subtitle_controller', None)
            if controller is not None:
                try:
                    controller.reset_for_service_change()
                except Exception as error:
                    log_event('vod_seek', 'subtitle-reset-failed', error)
            if self._paused:
                pauseable = self._pause_interface()
                try:
                    if pauseable is not None:pauseable.pause()
                except Exception:
                    pass
            state['phase'] = 'acquire'
            state['acquire_deadline'] = min(state['overall_deadline'], now + ACQUIRE_SECONDS)
            state['stable_length'] = 0
            state['last_length'] = -1
            self._playback_timer.start(POLL_MS, True)
            self._seek_verify_timer.start(POLL_MS, True)
            return
        seeker = self._seek_interface()
        position, length = self._seek_position(seeker)
        state['hold_at'] = now
        if phase == 'acquire':
            state['stable_length'] = state['stable_length'] + 1 if length > 0 and abs(length - state['last_length']) <= 2 else 1
            state['last_length'] = length
            if seeker is not None and position >= 0 and length > 0 and state['stable_length'] >= 3:
                target = state.get('queued_target')
                target = min(max(0, int(state['target'] if target is None else target)), max(0, length - 1))
                # Dispatch only after the previous decoder was released. Keep
                # the same transaction budget across the new verification.
                self._pending_seek_verification = None
                accepted = self._request_seek(seeker, position, target, startup_resume=state['startup_resume'])
                new = self._pending_seek_verification
                if not accepted or new is None:
                    self._fail_vod_seek(state)
                    return
                new.update(replay_used=True, nudge_used=True, overall_deadline=state['overall_deadline'])
                new['guard'].deadline = min(new['guard'].deadline, state['overall_deadline'])
                if self._paused:
                    pauseable = self._pause_interface()
                    try:
                        if pauseable is not None:pauseable.pause()
                    except Exception:
                        pass
                return
            if now >= state['acquire_deadline']:
                self._fail_vod_seek(state)
            else:
                self._seek_verify_timer.start(POLL_MS, True)
            return
        if self._paused:
            if position >= 0 and abs(position - state['target']) <= 5:
                self._finish_vod_seek(state, position)
            else:
                held = max(0.0, now - guard.last_sample)
                guard.hold(now)
                state['overall_deadline'] += held
                self._seek_verify_timer.start(POLL_MS, True)
            return
        try:
            from enigma import iServiceInformation
            pts = media_snapshot(self._current_service(), iServiceInformation).get('video_pts')
        except Exception:
            pts = None
        if guard.observe(position, pts, now):
            self._finish_vod_seek(state, position)
            return
        if now < min(guard.deadline, state['overall_deadline']):
            self._seek_verify_timer.start(POLL_MS, True)
            return
        # A playing stream that ignored seeking must keep playing. Unknown
        # clocks or a deliberate jump to EOF must never trigger a restart.
        near_end = length > 0 and state['target'] >= length - 2
        if guard.moving(now) or not guard.stalled(now) or near_end:
            self._fail_vod_seek(state)
        elif not state['nudge_used'] and self._nudge_vod_seek(state):
            self._seek_verify_timer.start(POLL_MS, True)
        elif not state['replay_used'] and now < state['overall_deadline'] and self._replay_vod_seek(state):
            return
        else:
            self._fail_vod_seek(state)

    def _vod_seek_pause_transport(self):
        state = self._pending_seek_verification
        return bool(isinstance(state, dict) and 'guard' in state and self._vod_seek_context(state) and state['phase'] in ('release', 'acquire'))

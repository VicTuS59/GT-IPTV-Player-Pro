# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded startup recovery for native Stalker live playback.

Some Enigma2 images accept ``playService`` even when the provider ticket or
the previous decoder/network pipeline never produces media.  This module does
not monitor an established stream.  It watches only the first three seconds of
a native (4097) Stalker start and, when the receiver's decoder PTS is readable
but does not advance, performs a small bounded set of controlled replays.
"""

from __future__ import absolute_import

import errno
import fcntl
import os
import platform
import struct
import threading
import time


_RESULT_PENDING = object()
_PTS_MODULO = 1 << 33
_PTS_MASK = _PTS_MODULO - 1
_DVB_VIDEO_DEVICE = "/dev/dvb/adapter0/video0"


def _normalise_pts(value):
    """Return one plausible 33-bit MPEG clock value or ``None``.

    Several Enigma2 drivers expose an all-bits-set value while the decoder
    clock is unavailable.  Treat those sentinels as UNKNOWN rather than as a
    stable clock that could trigger a restart.
    """

    try:
        raw = int(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if raw < 0 or raw > _PTS_MASK or raw in (0xFFFFFFFF, _PTS_MASK):
        return None
    return raw


def _video_get_pts_request(machine=None):
    """Return this Linux architecture's ``VIDEO_GET_PTS`` ioctl number."""

    value = str(machine or platform.machine() or "").strip().lower()
    # MIPS and PowerPC use three direction bits and thirteen size bits.  The
    # remaining Enigma2 architectures use the asm-generic encoding.
    if value.startswith("mips") or value.startswith(("ppc", "powerpc")):
        return 0x40086F39
    return 0x80086F39


def _read_dvb_video_pts(
    device=_DVB_VIDEO_DEVICE,
    opener=None,
    ioctl=None,
    closer=None,
    machine=None,
):
    """Read one decoder PTS without retaining the DVB device across zaps.

    The open is read-only and non-blocking.  Unsupported or temporarily busy
    drivers are deliberately reported as unavailable so they can never cause
    a healthy stream to be restarted.
    """

    open_device = opener or os.open
    issue_ioctl = ioctl or fcntl.ioctl
    close_device = closer or os.close
    flags = os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0)
    try:
        descriptor = open_device(device, flags)
    except (IOError, OSError):
        return None
    try:
        data = bytearray(8)
        for attempt in (0, 1):
            try:
                issue_ioctl(
                    descriptor,
                    _video_get_pts_request(machine),
                    data,
                    True,
                )
                return _normalise_pts(struct.unpack("=Q", data)[0])
            except (IOError, OSError) as error:
                if getattr(error, "errno", None) == errno.EINTR and attempt == 0:
                    continue
                return None
            except (TypeError, ValueError, struct.error):
                return None
        return None
    finally:
        try:
            close_device(descriptor)
        except (IOError, OSError):
            pass


def reference_identity(reference):
    """Return the stable Enigma2 identity without retaining a reference."""

    if reference is None:
        return ""
    for name in ("toCompareString", "toString"):
        getter = getattr(reference, name, None)
        if not callable(getter):
            continue
        try:
            value = getter()
        except Exception:
            value = ""
        if value:
            return str(value)
    return ""


def _read_proc_pts(path):
    try:
        with open(path, "r") as handle:
            value = handle.read(32).strip()
        return _normalise_pts(int(value, 16))
    except (IOError, OSError, TypeError, ValueError):
        return None


def media_snapshot(
    service,
    information,
    pts_reader=None,
    dvb_pts_reader=None,
):
    """Collect decoder-local values without treating stale metadata as media."""

    snapshot = {
        "width": None,
        "height": None,
        "audio_tracks": None,
        "video_pts": None,
        "video_pts_source": None,
        "audio_pts": None,
    }
    if service is None:
        return snapshot

    try:
        info = service.info()
        snapshot["width"] = int(info.getInfo(information.sVideoWidth))
        snapshot["height"] = int(info.getInfo(information.sVideoHeight))
    except Exception:
        pass
    try:
        snapshot["audio_tracks"] = int(
            service.audioTracks().getNumberOfTracks()
        )
    except Exception:
        pass

    reader = pts_reader or _read_proc_pts
    # Unit tests and non-Enigma consumers can explicitly supply the legacy
    # reader without touching a receiver device.  Production first uses the
    # DVB decoder clock verified on Vu+ and then falls back to a simple proc
    # hexadecimal value on images that expose one.
    decoder_reader = dvb_pts_reader
    if decoder_reader is None and pts_reader is None:
        decoder_reader = _read_dvb_video_pts
    decoder_pts = None
    if callable(decoder_reader):
        try:
            decoder_pts = decoder_reader()
        except Exception:
            decoder_pts = None
    if decoder_pts is not None:
        snapshot["video_pts"] = decoder_pts
        snapshot["video_pts_source"] = "dvb"
    try:
        if snapshot["video_pts"] is None:
            snapshot["video_pts"] = reader("/proc/stb/vmpeg/0/pts")
            if snapshot["video_pts"] is not None:
                snapshot["video_pts_source"] = "proc_video"
    except Exception:
        snapshot["video_pts"] = None
        snapshot["video_pts_source"] = None
    try:
        snapshot["audio_pts"] = reader("/proc/stb/audio/0/pts")
    except Exception:
        snapshot["audio_pts"] = None
    return snapshot


class LiveStartupRecovery(object):
    """Watch decoder progress and spend at most three recovery attempts."""

    STARTUP_SECONDS = 3.0
    REFERENCE_SETTLE_SECONDS = 0.5
    RELEASE_MS = 500
    RELEASE_DELAYS_MS = (500, 750, 1000)
    RESOLVE_SECONDS = 14.0
    OVERALL_SECONDS = 30.0
    SAMPLE_MS = 250
    POLL_MS = 100
    MAX_REPLAYS = 3
    PTS_ADVANCE_MIN = 2
    PTS_ADVANCE_TICKS_MIN = 45000
    PTS_MAX_STEP = 450000
    PTS_STATIC_SECONDS = 2.0
    PTS_VALID_SAMPLES_MIN = 8
    PTS_RECENT_ADVANCE_SECONDS = 1.0

    def __init__(
        self,
        navigation,
        timer,
        information,
        events,
        build_reference,
        play_service,
        reference_type=None,
        clock=None,
        thread_factory=None,
        pts_reader=None,
        dvb_pts_reader=None,
        log=None,
    ):
        self.navigation = navigation
        self.timer = timer
        self.information = information
        self.events = events
        self.build_reference = build_reference
        self.play_service = play_service
        self.reference_type = reference_type
        self.clock = clock or time.monotonic
        self.thread_factory = thread_factory or threading.Thread
        self.pts_reader = pts_reader
        self.dvb_pts_reader = dvb_pts_reader
        self.log = log or (lambda message: None)

        self.owner = None
        self.state = None
        self.status = None
        self.recovered = None
        self.client = None
        self.item = None
        self.reference = None
        self.generation = None

        self.phase = "idle"
        self.used = False
        self.replay_count = 0
        self.deadline = 0.0
        self.reference_settle_deadline = 0.0
        self.resolve_deadline = 0.0
        self.overall_deadline = 0.0
        self.resolve_serial = 0
        self.release_not_before = 0.0
        self.release_committing = False
        self.reference_seen = False
        self.last_snapshot = None
        self.absence_observable = False
        self.pts_source = None
        self.pts_value = None
        self.pts_at = None
        self.pts_window_started_at = None
        self.pts_last_advance_at = None
        self.pts_valid_samples = 0
        self.pts_advance_count = 0
        self.pts_advance_ticks = 0
        self.result = _RESULT_PENDING
        self.pending_reference = None
        self.resolve_method = "none"
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.listening = False
        self.dispatching = False
        self.cleanup_pending = False

    @property
    def busy(self):
        return self.phase in ("release", "resolve")

    def bind(self, owner, state, status, recovered):
        """Move the same attempt between mini TV and fullscreen."""

        self.owner = owner
        self.state = state
        self.status = status
        self.recovered = recovered
        if self.phase == "idle":
            return
        try:
            active, generation, reference = state()
        except Exception:
            self.cancel()
            return
        if (
            not active
            or reference_identity(reference)
            != reference_identity(self.reference)
        ):
            self.cancel()
            return
        self.generation = generation
        if self.phase == "watch":
            if not self._owns() and not self._navigation_reference_settling():
                self.cancel()
            else:
                self._schedule_watch_check()

    def detach(self):
        """Pause a transferable watch while the owning screen changes."""

        if self.busy:
            self.cancel()
        self.owner = None
        try:
            self.timer.stop()
        except Exception:
            pass
        if self.cleanup_pending and not self.dispatching:
            self.cleanup_pending = False
            self._unlisten()

    def start(self, client, item, reference, generation):
        """Begin the bounded startup watch owned by this instance."""

        if self.phase != "idle":
            return
        self.client = client
        self.item = item
        self.reference = reference
        self.generation = generation
        self.used = False
        self.replay_count = 0
        self.cancel_event = threading.Event()
        self.overall_deadline = self.clock() + self.OVERALL_SECONDS
        self._begin_watch_cycle()

    def _listen(self):
        if self.listening:
            return
        try:
            self.navigation.event.append(self.service_event)
            self.listening = True
        except Exception:
            # Decoder PTS polling remains safe without navigation event lists.
            self.listening = False

    def _reset_pts_observation(self):
        self.pts_source = None
        self.pts_value = None
        self.pts_at = None
        self.pts_window_started_at = None
        self.pts_last_advance_at = None
        self.pts_valid_samples = 0
        self.pts_advance_count = 0
        self.pts_advance_ticks = 0
        self.absence_observable = False

    def _begin_watch_cycle(self):
        self.phase = "watch"
        self.release_not_before = 0.0
        self.release_committing = False
        self.reference_seen = False
        self.last_snapshot = None
        self.result = _RESULT_PENDING
        self.pending_reference = None
        self.resolve_method = "none"
        self._reset_pts_observation()
        started_at = self.clock()
        self.deadline = min(
            started_at + self.STARTUP_SECONDS,
            self.overall_deadline or started_at + self.STARTUP_SECONDS,
        )
        self.reference_settle_deadline = (
            started_at + self.REFERENCE_SETTLE_SECONDS
        )
        self._listen()
        self.log(
            "monitor armed engine=4097 attempt={}/{} sensor=dvb_video_pts".format(
                self.replay_count,
                self.MAX_REPLAYS,
            )
        )
        if self._owns():
            self._sample()
        elif not self._navigation_reference_settling():
            self.cancel()
        if self.phase == "watch":
            self._schedule_watch_check()

    def _current_reference(self):
        getter = getattr(
            self.navigation,
            "getCurrentlyPlayingServiceReference",
            None,
        )
        return getter() if callable(getter) else None

    def _state_matches(self):
        try:
            if self.owner is None:
                return False
            active, generation, reference = self.state()
            return bool(
                active
                and generation == self.generation
                and reference_identity(reference)
                == reference_identity(self.reference)
            )
        except Exception:
            return False

    def _navigation_reference_settling(self):
        if self.reference_seen or not self._state_matches():
            return False
        expected = reference_identity(self.reference)
        return bool(
            expected
            and reference_identity(self._current_reference()) != expected
            and self.clock() < self.reference_settle_deadline
        )

    def _owns(self):
        if not self._state_matches():
            return False
        expected = reference_identity(self.reference)
        matches = bool(
            expected
            and reference_identity(self._current_reference()) == expected
        )
        if matches:
            self.reference_seen = True
        return matches

    def _owns_released_service(self):
        """Reject another tune, while tolerating a lagging stopped reference."""

        if not self._state_matches():
            return False
        current = reference_identity(self._current_reference())
        expected = reference_identity(self.reference)
        return not current or current == expected

    def _snapshot(self):
        try:
            getter = getattr(self.navigation, "getCurrentService", None)
            service = getter() if callable(getter) else None
        except Exception:
            service = None
        return media_snapshot(
            service,
            self.information,
            pts_reader=self.pts_reader,
            dvb_pts_reader=self.dvb_pts_reader,
        )

    @staticmethod
    def _snapshot_clock(snapshot):
        if snapshot is None:
            return None, None
        value = _normalise_pts(snapshot.get("video_pts"))
        if value is not None:
            return snapshot.get("video_pts_source") or "video", value
        value = _normalise_pts(snapshot.get("audio_pts"))
        if value is not None:
            return "proc_audio", value
        return None, None

    @staticmethod
    def _pts_delta(previous, current):
        previous = int(previous) & _PTS_MASK
        current = int(current) & _PTS_MASK
        if current >= previous:
            return current - previous
        # Accept the ordinary 33-bit MPEG clock wrap only near the two ends.
        wrap_edge = 90000 * 10
        if previous >= _PTS_MODULO - wrap_edge and current <= wrap_edge:
            return (current - previous) & _PTS_MASK
        return None

    def _start_pts_window(self, source, value, sampled_at):
        self.pts_source = source
        self.pts_value = int(value) & _PTS_MASK
        self.pts_at = sampled_at
        self.pts_window_started_at = sampled_at
        self.pts_last_advance_at = None
        self.pts_valid_samples = 1
        self.pts_advance_count = 0
        self.pts_advance_ticks = 0

    def _observe_decoder_pts(self, snapshot, sampled_at):
        try:
            source, value = self._snapshot_clock(snapshot)
        except (TypeError, ValueError, OverflowError):
            source, value = None, None
        if source is None or value is None:
            # A later successful sample must build a fresh consecutive window;
            # intermittent device access is UNKNOWN, never evidence of black.
            self._reset_pts_observation()
            return False
        if self.pts_source != source or self.pts_value is None:
            self._start_pts_window(source, value, sampled_at)
            return False

        delta = self._pts_delta(self.pts_value, value)
        previous_at = self.pts_at
        if previous_at is None:
            previous_at = sampled_at
        elapsed = max(0.0, sampled_at - float(previous_at))
        max_step = max(
            self.PTS_MAX_STEP,
            int(90000 * max(elapsed, 1.0) * 4),
        )
        if delta is None or delta > max_step:
            # A decoder reset or channel-clock discontinuity cannot be compared
            # with the previous service.  Rebaseline without claiming failure.
            self._start_pts_window(source, value, sampled_at)
            return False

        self.pts_valid_samples += 1
        self.pts_value = int(value) & _PTS_MASK
        self.pts_at = sampled_at
        if delta > 0:
            self.pts_advance_count += 1
            self.pts_advance_ticks += delta
            self.pts_last_advance_at = sampled_at

        window_started_at = self.pts_window_started_at
        if window_started_at is None:
            window_started_at = sampled_at
        window_seconds = sampled_at - float(window_started_at)
        if (
            self.pts_advance_count >= self.PTS_ADVANCE_MIN
            and self.pts_advance_ticks >= self.PTS_ADVANCE_TICKS_MIN
            and window_seconds >= self.REFERENCE_SETTLE_SECONDS
            and self.pts_last_advance_at is not None
            and sampled_at - self.pts_last_advance_at
            <= self.PTS_RECENT_ADVANCE_SECONDS
        ):
            return True

        static_since = self.pts_last_advance_at
        if static_since is None:
            static_since = self.pts_window_started_at
        self.absence_observable = bool(
            self.pts_valid_samples >= self.PTS_VALID_SAMPLES_MIN
            and static_since is not None
            and sampled_at - static_since >= self.PTS_STATIC_SECONDS
        )
        return False

    def _sample(self, event=None):
        if not self._owns():
            return False
        sampled_at = self.clock()
        if sampled_at < self.reference_settle_deadline:
            return False
        snapshot = self._snapshot()
        self.last_snapshot = snapshot
        clock_advancing = self._observe_decoder_pts(snapshot, sampled_at)
        # Observe the complete startup window before accepting a soft-zapped
        # stream.  Otherwise a short tail from the previous decoder pipeline
        # can look healthy and then freeze immediately after the monitor exits.
        # During resolve, fresh late progress may still abort recovery early.
        acquired = bool(
            clock_advancing
            and (self.phase != "watch" or sampled_at >= self.deadline)
        )
        if acquired:
            self.log(
                "monitor acquired engine=4097 attempt={} samples={} advances={}".format(
                    self.replay_count,
                    self.pts_valid_samples,
                    self.pts_advance_count,
                )
            )
            self.log(
                "complete outcome={} engine=4097 attempts={}".format(
                    "recovered" if self.replay_count else "initially_healthy",
                    self.replay_count,
                )
            )
            self._finish("acquired")
            return True
        return False

    def service_event(self, event):
        # OpenPLi can iterate the original observer list. Defer removal until
        # after the current dispatch so the following observer is not skipped.
        self.dispatching = True
        try:
            self._service_event(event)
        finally:
            self.dispatching = False

    def _service_event(self, event):
        if self.owner is None or self.phase != "watch":
            return
        if not self._owns():
            if self._navigation_reference_settling():
                return
            self.cancel()
            return
        user = getattr(self.events, "evUser", None)
        if user is not None and event in (user + 10, user + 11, user + 12):
            # Codec/pipeline support errors cannot be repaired with a new URL.
            self.log(
                "complete outcome=unsupported engine=4097 attempts={}".format(
                    self.replay_count
                )
            )
            self._finish("unsupported")
            return
        if event in (
            getattr(self.events, "evUpdatedInfo", None),
            getattr(self.events, "evVideoSizeChanged", None),
        ):
            self._sample(event=event)

    def _schedule_watch_check(self):
        if self._navigation_reference_settling():
            delay = min(
                self.SAMPLE_MS,
                max(
                    1,
                    int(
                        (self.reference_settle_deadline - self.clock())
                        * 1000
                    ),
                ),
            )
        else:
            delay = min(
                self.SAMPLE_MS,
                max(1, int((self.deadline - self.clock()) * 1000)),
            )
        self.timer.start(delay, True)

    def _start_resolve_attempt(self):
        """Start the next provider strategy while retaining current playback."""

        now = self.clock()
        if self.replay_count >= self.MAX_REPLAYS or now >= self.overall_deadline:
            self._fail()
            return False
        self.used = True
        self.replay_count += 1
        self.resolve_serial += 1
        serial = self.resolve_serial
        self.phase = "resolve"
        self.resolve_deadline = min(
            now + self.RESOLVE_SECONDS,
            self.overall_deadline,
        )
        self.pending_reference = None
        self.resolve_method = "none"
        self._reset_pts_observation()
        with self.lock:
            self.result = _RESULT_PENDING
        worker = self.thread_factory(target=lambda: self._resolve(serial))
        worker.daemon = True
        try:
            worker.start()
        except Exception:
            self._fail()
            return False
        self.timer.start(self.POLL_MS, True)
        return True

    def tick(self):
        if self.cleanup_pending:
            self.cleanup_pending = False
            self._unlisten()
        if self.phase == "watch":
            if not self._owns():
                if self._navigation_reference_settling():
                    self._schedule_watch_check()
                    return
                self.cancel()
                return
            if self._sample():
                return
            if self.clock() < self.deadline:
                self._schedule_watch_check()
                return
            if not self.absence_observable:
                self.log(
                    "monitor unknown engine=4097 attempt={} samples={} advances={}".format(
                        self.replay_count,
                        self.pts_valid_samples,
                        self.pts_advance_count,
                    )
                )
                self.log(
                    "complete outcome=unknown engine=4097 attempts={}".format(
                        self.replay_count
                    )
                )
                self._finish("unknown")
                return
            self.log(
                "monitor stalled engine=4097 attempt={} samples={} advances={}".format(
                    self.replay_count,
                    self.pts_valid_samples,
                    self.pts_advance_count,
                )
            )
            if self.replay_count >= self.MAX_REPLAYS:
                self.log(
                    "complete outcome=exhausted engine=4097 attempts={}".format(
                        self.replay_count
                    )
                )
                self._finish("exhausted")
                return
            self._start_resolve_attempt()
            return

        if self.phase == "resolve":
            if not self._owns():
                self.cancel()
                return
            # A late-starting stream wins over an already-running link worker;
            # no decoder teardown is allowed after progress appears.
            if self._sample():
                return
            with self.lock:
                result = self.result
            if result is _RESULT_PENDING:
                if self.clock() >= self.resolve_deadline:
                    self._fail()
                    return
                self.timer.start(self.POLL_MS, True)
                return
            if not result:
                self._start_resolve_attempt()
                return
            try:
                reference = self.build_reference(result, self.item)
                if callable(self.reference_type):
                    service_type = int(self.reference_type(reference))
                else:
                    service_type = int(
                        reference_identity(reference).split(":", 1)[0]
                    )
                if service_type != 4097:
                    raise ValueError("Unexpected playback engine")
            except Exception:
                self._start_resolve_attempt()
                return
            self.pending_reference = reference
            self.phase = "release"
            self._unlisten()
            try:
                self.status("retry")
            except Exception:
                pass
            if self.phase != "release":
                return
            if not self._owns():
                self.cancel()
                return
            release_ms = self.RELEASE_DELAYS_MS[
                min(self.replay_count, len(self.RELEASE_DELAYS_MS)) - 1
            ]
            # Do not commit a decoder teardown when the bounded recovery has
            # insufficient time left to honour its own release delay.  The
            # currently playing (albeit stalled) service remains recoverable by
            # an explicit user action.
            if (
                self.overall_deadline
                and self.overall_deadline - self.clock()
                <= release_ms / 1000.0
            ):
                self._fail()
                return
            self.log(
                "retry requested engine=4097 attempt={}/{} method={} release_ms={}".format(
                    self.replay_count,
                    self.MAX_REPLAYS,
                    self.resolve_method,
                    release_ms,
                )
            )
            # status/log callbacks are allowed to re-enter screen code.  A
            # terminal transition may have cancelled this attempt while those
            # callbacks ran; never stop a service after ownership was revoked.
            if self.phase != "release":
                return
            if not self._owns():
                self.cancel()
                return
            try:
                self.navigation.stopService()
            except Exception:
                self._fail()
                return
            self.release_not_before = self.clock() + release_ms / 1000.0
            try:
                self.timer.start(release_ms, True)
            except Exception:
                # The decoder is already released. A broken GUI timer must not
                # strand navigation empty; commit the prepared reference now.
                self._commit_pending_reference()
            return

        if self.phase != "release":
            return
        if self.release_committing:
            return
        if not self._owns_released_service():
            self.cancel()
            return
        remaining = self.release_not_before - self.clock()
        if remaining > 0:
            try:
                self.timer.start(max(1, int(remaining * 1000) + 1), True)
            except Exception:
                self._commit_pending_reference()
            return
        self._commit_pending_reference()

    def _commit_pending_reference(self):
        """Play one prepared retry exactly once after decoder release."""

        if self.phase != "release" or self.release_committing:
            return False
        if not self._owns_released_service():
            self.cancel()
            return False
        self.release_committing = True
        reference = self.pending_reference
        try:
            if reference is None:
                raise ValueError("Missing playback reference")
            self.play_service(self.navigation, reference)
        except Exception:
            # The resolver succeeded but Enigma2 rejected the replacement.
            # Best-effort restoration is safer than leaving the navigation
            # slot empty after the deliberate decoder release.
            try:
                self.play_service(self.navigation, self.reference)
            except Exception:
                pass
            self._fail()
            return False
        self.reference = reference
        if self.phase != "release":
            self.release_committing = False
            return False
        try:
            self.recovered(reference)
        except Exception:
            pass
        if self.phase != "release":
            self.release_committing = False
            return False
        self.log(
            "replay outcome=started engine=4097 attempt={}/{}".format(
                self.replay_count,
                self.MAX_REPLAYS,
            )
        )
        if self.phase != "release":
            self.release_committing = False
            return False
        self._begin_watch_cycle()
        return True

    def _resolve(self, serial):
        if self.cancel_event.is_set() or self.clock() >= self.resolve_deadline:
            return
        available = []
        for method in ("refresh", "recover", "alternate"):
            resolver = getattr(
                self.client,
                "{}_playback_url".format(method)
                if method != "recover"
                else "recover_live_playback_url",
                None,
            )
            if callable(resolver):
                available.append((method, resolver))
        if available:
            method, resolver = available[
                (max(1, self.replay_count) - 1) % len(available)
            ]
        else:
            method, resolver = "none", None
        self.resolve_method = method if callable(resolver) else "none"
        try:
            url = str(
                resolver(
                    self.item,
                    cancel_event=self.cancel_event,
                    deadline=self.resolve_deadline,
                )
                or ""
            ).strip() if callable(resolver) else ""
        except Exception:
            url = ""
        with self.lock:
            if (
                not self.cancel_event.is_set()
                and self.clock() < self.resolve_deadline
                and serial == self.resolve_serial
                and self.phase == "resolve"
            ):
                self.result = url

    def _fail(self):
        self._finish("failed")
        self.log(
            "complete outcome=failed engine=4097 attempts={}".format(
                self.replay_count
            )
        )
        try:
            if self.owner is not None:
                self.status("failed")
        except Exception:
            pass

    def _unlisten(self):
        if not self.listening:
            return
        try:
            self.navigation.event.remove(self.service_event)
        except (AttributeError, ValueError):
            pass
        self.listening = False

    def _finish(self, phase):
        self.phase = phase
        self.release_not_before = 0.0
        self.release_committing = False
        self.cancel_event.set()
        try:
            self.timer.stop()
        except Exception:
            pass
        if self.dispatching:
            self.cleanup_pending = True
            self.timer.start(1, True)
        else:
            self.cleanup_pending = False
            self._unlisten()

    def cancel(self, restore_released=False):
        """Cancel work unless a caller asks to preserve a committed release.

        User transitions that promise to keep the current channel on failure
        call this with ``restore_released=True``.  Once ``stopService`` has
        been scheduled, its pending reference and timer remain the sole owner
        of the navigation slot.  Exit/stop paths leave the option false because
        they immediately restore or stop their own service.
        """

        if restore_released and self.phase == "release":
            return False
        self._finish("cancelled")
        return True

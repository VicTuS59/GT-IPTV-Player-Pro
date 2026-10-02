# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Decoder-authoritative subtitle timing, independent of the Enigma2 UI."""

SEEK_TIMEOUT_SECONDS = 12.0
SETTLE_SAMPLES = 3
SETTLE_HINT_TOLERANCE_MS = 2500
STALLED_AFTER_SECONDS = 0.75


class SubtitlePlaybackClock(object):
    """Never manufacture movie time from elapsed wall/monotonic time.

    A seek command only suspends rendering. A confirmed landing is followed
    by advancing decoder samples, or one matching sample when paused. Missing
    PTS suspends captions; frozen valid PTS retains the current frame's cue.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self.last_position_ms = None
        self.last_decoder_ms = None
        self.last_advance_at = None
        self.paused = False
        self.pause_position_ms = None
        self.pending = None
        self.settling = None
        self.source = "waiting-decoder"
        self.event = ""

    def pause(self):
        self.paused = True
        self.pause_position_ms = self.last_position_ms

    def resume(self):
        self.paused = False
        self.pause_position_ms = None

    def begin_seek(self, target_ms, origin_ms, now):
        self.pending = {
            "target_ms": target_ms,
            "origin_ms": origin_ms,
            "deadline": now + SEEK_TIMEOUT_SECONDS,
        }
        self.settling = None
        self.source = "seek-pending"
        self.event = "seek-started"

    def complete_seek(self, hint_ms, now):
        self.pending = None
        self.settling = {
            "hint_ms": hint_ms,
            "deadline": now + SEEK_TIMEOUT_SECONDS,
            "previous_ms": None,
            "previous_at": None,
            "first_ms": None,
            "first_at": None,
            "samples": 0,
        }
        self.source = "seek-settling"
        self.event = "seek-confirmed"

    def cancel_seek(self):
        self.pending = None
        self.settling = None
        self.source = "seek-cancelled"
        self.event = "seek-cancelled"

    def _settled(self, decoder_ms, now):
        state = self.settling
        previous = state["previous_ms"]
        if previous is None:
            hint = state["hint_ms"]
            if hint is not None and abs(decoder_ms - hint) > SETTLE_HINT_TOLERANCE_MS:
                return False
            if self.paused:
                return True
            state["previous_ms"] = decoder_ms
            state["previous_at"] = now
            state["first_ms"] = decoder_ms
            state["first_at"] = now
            state["samples"] = 1
            return False
        delta_ms = decoder_ms - previous
        if delta_ms == 0:
            return False
        elapsed_ms = max(0.0, now - state["previous_at"]) * 1000.0
        # Permit quantized service clocks, but reject backward samples and
        # discontinuities rather than treating them as normal-speed playback.
        maximum_delta = max(1500.0, elapsed_ms * 1.75 + 250.0)
        if delta_ms < 0 or delta_ms > maximum_delta:
            state["previous_ms"] = None
            state["previous_at"] = None
            state["first_ms"] = None
            state["first_at"] = None
            state["samples"] = 0
            return False
        state["previous_ms"] = decoder_ms
        state["previous_at"] = now
        state["samples"] += 1
        if state["samples"] < SETTLE_SAMPLES:
            return False
        total_elapsed_ms = max(0.0, now - state["first_at"]) * 1000.0
        total_delta_ms = decoder_ms - state["first_ms"]
        # A single 1-second quantization edge is possible. Repeated rapid
        # jumps are not evidence of a settled normal-speed decoder clock.
        if total_delta_ms > total_elapsed_ms * 1.75 + 1000.0:
            state["previous_ms"] = None
            state["previous_at"] = None
            state["first_ms"] = None
            state["first_at"] = None
            state["samples"] = 0
            return False
        return True

    def update(self, decoder_ms, now):
        self.event = ""
        if self.pending is not None:
            if now < self.pending["deadline"]:
                self.source = "seek-pending"
                return None
            self.cancel_seek()
            self.event = "seek-timeout"
        if self.settling is not None and now >= self.settling["deadline"]:
            self.settling = None
            self.event = "seek-settle-timeout"

        if decoder_ms is None or decoder_ms < 0:
            self.source = "paused" if self.paused else "invalid-decoder"
            return self.pause_position_ms if self.paused else None

        if self.settling is not None:
            if not self._settled(decoder_ms, now):
                self.source = "seek-settling"
                return None
            self.settling = None
            self.pause_position_ms = decoder_ms if self.paused else None
            self.event = "seek-clock-ready"

        if self.paused:
            if self.pause_position_ms is None:
                self.pause_position_ms = decoder_ms
            self.last_position_ms = self.pause_position_ms
            self.source = "paused"
            return self.pause_position_ms

        if decoder_ms != self.last_decoder_ms:
            self.last_advance_at = now
        self.last_decoder_ms = decoder_ms
        self.last_position_ms = decoder_ms
        held_for = max(0.0, now - self.last_advance_at) if self.last_advance_at is not None else 0.0
        self.source = "decoder-held" if held_for >= STALLED_AFTER_SECONDS else "decoder"
        return decoder_ms

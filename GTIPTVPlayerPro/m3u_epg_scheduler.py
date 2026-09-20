# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Background scheduler for optional per-source M3U XMLTV snapshots.

The scheduler is deliberately session-owned.  It never participates in list
navigation or playback: the UI reads the in-memory store while this module
does all M3U/XMLTV I/O on one daemon worker.
"""

import threading
import time
from collections import OrderedDict

from .diagnostics import log_event
from .dvb_epg_runtime import acquire_runtime, release_runtime, runtime_is_busy
from .m3u import M3UContentClient, load_m3u_sources
from .m3u_epg import get_default_store, refresh_source
from .m3u_epg_settings import (
    load_m3u_epg_bindings,
    update_m3u_epg_binding,
)


STARTUP_DELAY_SECONDS = 90
SETTINGS_DELAY_SECONDS = 5
SETTINGS_RETRY_SECONDS = 5 * 60
RUNTIME_RETRY_SECONDS = 10 * 60
CLOCK_RETRY_SECONDS = 10 * 60
FAILURE_RETRY_SECONDS = 30 * 60
IDLE_RECHECK_SECONDS = 30 * 60
WORKER_TIMEOUT_SECONDS = 180
WORKER_POLL_MILLISECONDS = 500
REFRESH_STATE_LIMIT = 512
SUPPORTED_INTERVALS = (4, 6, 12, 24)

_SAFE_ERROR_REASONS = frozenset(
    (
        "access_denied",
        "cancelled",
        "catalog_item_limit",
        "connection",
        "content_response",
        "deadline",
        "http_error",
        "invalid_response",
        "m3u-source-unavailable",
        "no-tvg-id",
        "rate_limited",
        "response_too_large",
        "invalid-refresh-result",
        "unknown",
    )
)
_SAFE_ERROR_CLASSES = {
    "ContentError": "content-error",
    "IOError": "io-error",
    "M3UEPGCancelled": "cancelled",
    "M3UEPGError": "xmltv-error",
    "M3UEPGSnapshotError": "snapshot-error",
    "M3UEPGSourceError": "xmltv-source-error",
    "OSError": "io-error",
    "RuntimeError": "runtime-error",
    "ValueError": "invalid-data",
    "_RefreshError": "refresh-error",
}


def _connect_timer(timer, callback):
    try:
        return timer.timeout.connect(callback)
    except AttributeError:
        timer.callback.append(callback)
        return None


def _value(binding, name, fallback=None):
    if isinstance(binding, dict):
        return binding.get(name, fallback)
    return getattr(binding, name, fallback)


def _copy_binding(binding):
    copier = getattr(binding, "copy", None)
    if callable(copier):
        return copier()
    if isinstance(binding, dict):
        return dict(binding)
    return binding


def _set_value(binding, name, value):
    if isinstance(binding, dict):
        binding[name] = value
    else:
        setattr(binding, name, value)


def _source_id(binding):
    return str(_value(binding, "source_id", "") or "").strip()


def _interval_hours(binding):
    try:
        interval = int(_value(binding, "interval_hours", 24))
    except (TypeError, ValueError, OverflowError):
        interval = 24
    return interval if interval in SUPPORTED_INTERVALS else 24


def _clock_is_sane(now=None):
    try:
        year = time.gmtime(time.time() if now is None else now).tm_year
    except (OverflowError, OSError, TypeError, ValueError):
        return False
    return 2020 <= int(year) <= 2100


def _enabled(binding):
    return bool(_value(binding, "enabled", False))


def _automatic(binding):
    return _enabled(binding) and str(
        _value(binding, "update_mode", "automatic") or "automatic"
    ).strip().lower() == "automatic"


def _last_success(binding):
    try:
        return max(0, int(_value(binding, "last_success_utc", 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _is_due(binding, now=None):
    now = int(time.time() if now is None else now)
    last_success = _last_success(binding)
    if last_success <= 0:
        return True
    return now >= last_success + (_interval_hours(binding) * 60 * 60)


def _binding_signature(binding):
    """Return configuration fields used for pending/retry decisions.

    Status fields are intentionally omitted.  Schedule fields remain present
    so a deferred job cannot silently retain an obsolete cadence.
    """
    fingerprint = _value(binding, "fingerprint", "")
    if isinstance(fingerprint, (dict, list, tuple, set)):
        fingerprint = repr(fingerprint)
    return (
        _source_id(binding),
        bool(_enabled(binding)),
        str(_value(binding, "kind", "") or ""),
        str(_value(binding, "location", "") or ""),
        str(_value(binding, "update_mode", "") or ""),
        _interval_hours(binding),
        str(fingerprint or ""),
    )


def _pipeline_signature(binding):
    """Return fields which change the fetched snapshot, not its cadence."""
    return (
        _source_id(binding),
        bool(_enabled(binding)),
        str(_value(binding, "kind", "") or ""),
        str(_value(binding, "location", "") or ""),
        str(_value(binding, "fingerprint", "") or ""),
    )


def _snapshot_signature(binding):
    return (
        _source_id(binding),
        str(_value(binding, "fingerprint", "") or ""),
    )


def _safe_count(value):
    if isinstance(value, (str, bytes, bytearray)):
        try:
            return max(0, int(value))
        except (TypeError, ValueError, OverflowError):
            return 0
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        try:
            return max(0, len(value))
        except (TypeError, ValueError, OverflowError):
            return 0


def _error_code(error):
    reason = getattr(error, "reason", "")
    if isinstance(reason, str):
        reason = reason.strip().lower()
        if reason in _SAFE_ERROR_REASONS:
            return reason[:48]
    class_name = error.__class__.__name__ if error is not None else ""
    return _SAFE_ERROR_CLASSES.get(class_name, "failed")


def _bounded_diagnostic_value(value):
    try:
        return min(0x7FFFFFFF, max(0, int(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _log_refresh_timing(source_id, diagnostics, outcome):
    values = diagnostics if isinstance(diagnostics, dict) else {}
    transfer_kind = str(values.get("transfer_kind", "none") or "none")
    if transfer_kind not in ("copy", "download", "none"):
        transfer_kind = "none"
    stage = str(values.get("stage", "prepare") or "prepare")
    if stage not in (
        "prepare",
        "copy",
        "download",
        "gzip",
        "xml-scan",
        "cache-write",
        "complete",
    ):
        stage = "prepare"
    safe_outcome = str(outcome or "failed").strip().lower()
    if safe_outcome not in _SAFE_ERROR_REASONS and safe_outcome not in (
        "success",
        "failed",
        "runtime-error",
        "xmltv-error",
        "xmltv-source-error",
        "snapshot-error",
        "io-error",
        "invalid-data",
    ):
        safe_outcome = "failed"
    message = (
        "External XMLTV timing source={} outcome={} stage={} transfer={} "
        "copy_ms={} download_ms={} gzip_ms={} xml_scan_ms={} "
        "compact_ms={} cache_write_ms={} core_total_ms={} worker_total_ms={} "
        "input_bytes={} xml_bytes={} redirects={} compacted={} "
        "snapshot_before={} snapshot_after={} events_before={} "
        "events_after={} events_dropped={} descriptions_trimmed={}"
    ).format(
        str(source_id or "")[:64],
        safe_outcome,
        stage,
        transfer_kind,
        _bounded_diagnostic_value(values.get("copy_ms")),
        _bounded_diagnostic_value(values.get("download_ms")),
        _bounded_diagnostic_value(values.get("gzip_ms")),
        _bounded_diagnostic_value(values.get("xml_scan_ms")),
        _bounded_diagnostic_value(values.get("snapshot_compact_ms")),
        _bounded_diagnostic_value(values.get("cache_write_ms")),
        _bounded_diagnostic_value(values.get("total_ms")),
        _bounded_diagnostic_value(values.get("worker_total_ms")),
        _bounded_diagnostic_value(values.get("input_bytes")),
        _bounded_diagnostic_value(values.get("xml_bytes")),
        _bounded_diagnostic_value(values.get("redirect_count")),
        _bounded_diagnostic_value(values.get("snapshot_compacted")),
        _bounded_diagnostic_value(values.get("snapshot_original_bytes")),
        _bounded_diagnostic_value(values.get("snapshot_final_bytes")),
        _bounded_diagnostic_value(values.get("snapshot_original_events")),
        _bounded_diagnostic_value(values.get("snapshot_retained_events")),
        _bounded_diagnostic_value(values.get("snapshot_dropped_events")),
        _bounded_diagnostic_value(values.get("snapshot_trimmed_descriptions")),
    )
    try:
        log_event("m3u_epg", message)
    except Exception:
        pass


def _channel_id(value):
    if isinstance(value, dict):
        value = value.get("tvg_id", value.get("tvg-id", ""))
    elif not isinstance(value, str):
        value = getattr(value, "tvg_id", value)
    return str(value or "").strip()[:256]


def _prepared_channel_ids(client):
    """Read all prepared ``tvg-id`` values without invoking network I/O.

    R57 clients expose ``epg_channel_ids``.  The private-record fallback keeps
    this scheduler compatible with an R56 client during staged upgrades and
    is used only after ``prepare`` completed on this worker.
    """
    getter = getattr(client, "epg_channel_ids", None)
    if callable(getter):
        values = getter()
    else:
        state_lock = getattr(client, "_state_lock", None)
        if state_lock is None:
            values = tuple(getattr(client, "_records", ()) or ())
        else:
            with state_lock:
                values = tuple(getattr(client, "_records", ()) or ())
    result = set()
    for value in values or ():
        channel_id = _channel_id(value)
        if channel_id:
            result.add(channel_id)
    return frozenset(result)


class _RefreshError(Exception):
    def __init__(self, reason):
        Exception.__init__(self, reason)
        self.reason = str(reason or "failed")


class _QueuedJob(object):
    def __init__(self, source_id, mode, not_before=0.0, request_id=0):
        self.source_id = str(source_id or "")
        self.mode = "manual" if mode == "manual" else "automatic"
        self.not_before = max(0.0, float(not_before or 0.0))
        self.request_id = max(0, int(request_id or 0))


class M3UEPGScheduler(object):
    """Own one XMLTV refresh queue for an Enigma2 GUI session."""

    def __init__(self, session, predecessor=None):
        self.session = session
        self._gui_thread_id = threading.get_ident()
        self._condition = threading.Condition(threading.RLock())
        self._owner = object()
        self._stopped = False
        self._timer = None
        self._timer_connection = None
        self._timer_purpose = ""
        self._bindings = OrderedDict()
        self._pending = OrderedDict()
        self._snapshot_actions = OrderedDict()
        self._settings_reload_requested = True
        self._settings_reconcile_requested = False
        self._active_source_id = ""
        self._active_mode = ""
        self._active_cancel = None
        self._active_cancel_reason = ""
        self._active_deadline = 0.0
        self._store = None
        self._worker = None
        self._predecessor = predecessor
        self._startup_not_before = time.monotonic() + STARTUP_DELAY_SECONDS
        self._settings_not_before = 0.0
        self._settings_reload_failed = False
        self._settings_retry_not_before = 0.0
        self._refresh_sequence = 0
        self._refresh_states = OrderedDict()
        if predecessor is not None and predecessor.is_alive():
            self._active_mode = "handoff"

        # Fail before creating a thread when this is imported outside an
        # Enigma2 process without the normal eTimer test stub.
        self._ensure_timer()
        self._start_worker()
        self._arm_timer(STARTUP_DELAY_SECONDS, "startup")

    def busy(self):
        with self._condition:
            return bool(
                self._active_mode
                or self._active_source_id
                or self._pending
                or self._snapshot_actions
                or self._settings_reload_requested
            )

    def get_refresh_state(self, source_id):
        source_id = str(source_id or "").strip()
        if not source_id:
            return None
        with self._condition:
            current = self._refresh_states.get(source_id)
            return dict(current) if current is not None else None

    def _cancel_active_locked(self, reason):
        cancel_event = self._active_cancel
        if cancel_event is None:
            return False
        if not self._active_cancel_reason:
            self._active_cancel_reason = (
                "deadline" if reason == "deadline" else "cancelled"
            )
        cancel_event.set()
        return True

    def _set_refresh_state_locked(
        self,
        source_id,
        request_id,
        state,
        error_code="",
    ):
        request_id = max(0, int(request_id or 0))
        if request_id <= 0:
            return False
        if not self._reserve_refresh_state_locked(source_id):
            return False
        state = str(state or "failed")
        if state not in ("queued", "running", "success", "failed", "cancelled"):
            state = "failed"
        self._refresh_states.pop(source_id, None)
        self._refresh_states[source_id] = {
            "request_id": request_id,
            "state": state,
            "mode": "manual",
            "error_code": str(error_code or "")[:48],
            "completed_at": (
                int(time.time())
                if state in ("success", "failed", "cancelled")
                else 0
            ),
        }
        return True

    def _reserve_refresh_state_locked(self, source_id):
        if source_id in self._refresh_states:
            return True
        while len(self._refresh_states) >= REFRESH_STATE_LIMIT:
            removable = next(
                (
                    key
                    for key, value in self._refresh_states.items()
                    if value.get("state")
                    in ("success", "failed", "cancelled")
                ),
                None,
            )
            if removable is None:
                return False
            self._refresh_states.pop(removable, None)
        return True

    def notify_settings_changed(self):
        if self._stopped:
            return
        self._mark_settings_delay()
        with self._condition:
            if (
                self._active_mode in ("manual", "automatic")
                and self._active_cancel is not None
            ):
                # The settings sidecar may only be read by this same worker.
                # Cancel pessimistically so a changed/disabled pipeline cannot
                # keep using its old endpoint until the absolute deadline.
                self._cancel_active_locked("cancelled")
        self._request_settings_reload(reconcile=True)
        if self._on_gui_thread():
            self._arm_poll()

    def request_refresh(self, source_id):
        source_id = str(source_id or "").strip()
        if not source_id or self._stopped:
            return False
        with self._condition:
            if self._stopped:
                return False
            binding = self._bindings.get(source_id)
            reload_pending = bool(
                self._settings_reload_requested
                or self._active_mode == "settings"
            )
            if (
                (binding is None or not _enabled(binding))
                and not reload_pending
            ):
                return False
            if (
                self._active_source_id == source_id
                and self._active_mode in ("manual", "automatic")
            ):
                return False
            if not self._reserve_refresh_state_locked(source_id):
                return False
            existing = self._pending.get(source_id)
            if existing is not None:
                if existing.mode == "manual":
                    return False
                self._refresh_sequence += 1
                existing.mode = "manual"
                existing.not_before = 0.0
                existing.request_id = self._refresh_sequence
            else:
                self._refresh_sequence += 1
                self._pending[source_id] = _QueuedJob(
                    source_id,
                    "manual",
                    request_id=self._refresh_sequence,
                )
            self._set_refresh_state_locked(
                source_id,
                self._refresh_sequence,
                "queued",
            )
            self._condition.notify_all()
        if self._on_gui_thread():
            self._arm_poll()
        return True

    def _request_settings_reload(self, reconcile):
        with self._condition:
            if self._stopped:
                return False
            self._settings_reload_requested = True
            self._settings_reconcile_requested = bool(
                self._settings_reconcile_requested or reconcile
            )
            self._condition.notify_all()
        return True

    def _on_gui_thread(self):
        return threading.get_ident() == self._gui_thread_id

    def _mark_settings_delay(self):
        with self._condition:
            self._settings_not_before = max(
                self._settings_not_before,
                time.monotonic() + SETTINGS_DELAY_SECONDS,
            )
            for job in self._pending.values():
                if job.mode == "automatic":
                    job.not_before = max(
                        job.not_before,
                        self._settings_not_before,
                    )
            self._condition.notify_all()

    def _automatic_not_before(self, binding, monotonic_now, wall_now):
        if not _clock_is_sane(wall_now):
            return monotonic_now + CLOCK_RETRY_SECONDS
        last_success = _last_success(binding)
        due_at = last_success + (_interval_hours(binding) * 60 * 60)
        due_in = 0 if last_success <= 0 else due_at - wall_now
        return monotonic_now + max(
            0.0,
            float(due_in),
            self._startup_not_before - monotonic_now,
            self._settings_not_before - monotonic_now,
        )

    def _queue_automatic_fallback(self):
        """Plan work without touching eTimer when notified off the GUI thread."""
        monotonic_now = time.monotonic()
        wall_now = int(time.time())
        with self._condition:
            if self._stopped:
                return
            for source_id, binding in self._bindings.items():
                if (
                    not _automatic(binding)
                    or source_id == self._active_source_id
                    or source_id in self._pending
                ):
                    continue
                self._pending[source_id] = _QueuedJob(
                    source_id,
                    "automatic",
                    self._automatic_not_before(
                        binding,
                        monotonic_now,
                        wall_now,
                    ),
                )
            self._condition.notify_all()

    def stop(self):
        with self._condition:
            if self._stopped:
                return
            self._stopped = True
            for job in self._pending.values():
                if job.mode == "manual":
                    self._set_refresh_state_locked(
                        job.source_id,
                        job.request_id,
                        "cancelled",
                        "cancelled",
                    )
            active_state = self._refresh_states.get(self._active_source_id)
            if active_state is not None and active_state.get("state") == "running":
                self._set_refresh_state_locked(
                    self._active_source_id,
                    active_state.get("request_id", 0),
                    "cancelled",
                    "cancelled",
                )
            self._pending.clear()
            self._snapshot_actions.clear()
            self._settings_reload_requested = False
            self._settings_reconcile_requested = False
            self._settings_retry_not_before = 0.0
            cancel_event = self._active_cancel
            if cancel_event is not None:
                self._cancel_active_locked("cancelled")
            self._condition.notify_all()
        self._disconnect_timer()
        if not self._active_source_id:
            release_runtime(self._owner)
        self.session = None

    def _ensure_timer(self):
        if self._timer is None:
            from enigma import eTimer

            self._timer = eTimer()
            self._timer_connection = _connect_timer(self._timer, self._on_timer)
        return self._timer

    def _arm_timer(self, seconds, purpose):
        if self._stopped:
            return
        timer = self._ensure_timer()
        self._timer_purpose = str(purpose or "periodic")
        milliseconds = max(1, min(int(float(seconds) * 1000), 2_000_000_000))
        timer.start(milliseconds, True)

    def _arm_poll(self):
        if self._stopped:
            return
        timer = self._ensure_timer()
        self._timer_purpose = "poll"
        timer.start(WORKER_POLL_MILLISECONDS, True)

    def _stop_timer(self):
        timer = self._timer
        self._timer_purpose = ""
        if timer is not None:
            try:
                timer.stop()
            except Exception:
                pass

    def _disconnect_timer(self):
        timer = self._timer
        connection = self._timer_connection
        self._stop_timer()
        if connection is not None:
            disconnect = getattr(connection, "disconnect", None)
            if callable(disconnect):
                try:
                    disconnect()
                except Exception:
                    pass
        elif timer is not None:
            callbacks = getattr(timer, "callback", None)
            if isinstance(callbacks, list):
                try:
                    callbacks.remove(self._on_timer)
                except ValueError:
                    pass
        self._timer_connection = None
        self._timer = None

    def _start_worker(self):
        worker = threading.Thread(
            target=self._worker_main,
            name="GTM3UXMLTV",
        )
        worker.daemon = True
        self._worker = worker
        try:
            worker.start()
        except Exception:
            self._worker = None
            self._disconnect_timer()
            raise

    def _load_bindings_on_worker(self, cancel_stale):
        """Read and reconcile the sidecar only from the daemon worker."""
        try:
            bindings = list(load_m3u_epg_bindings(strict=True) or ())
        except Exception as error:
            log_event("m3u_epg", "External XMLTV settings could not be loaded", error)
            with self._condition:
                if not self._stopped:
                    self._settings_reload_failed = True
                    self._settings_retry_not_before = (
                        time.monotonic() + SETTINGS_RETRY_SECONDS
                    )
                    self._condition.notify_all()
            return False
        loaded = OrderedDict()
        for binding in bindings:
            source_id = _source_id(binding)
            if source_id and source_id not in loaded:
                loaded[source_id] = _copy_binding(binding)
        with self._condition:
            if self._stopped:
                return False
            self._settings_reload_failed = False
            self._settings_retry_not_before = 0.0
            previous = self._bindings
            self._bindings = loaded
            for source_id, job in tuple(self._pending.items()):
                old_binding = previous.get(source_id)
                new_binding = loaded.get(source_id)
                changed = bool(
                    old_binding is None
                    or new_binding is None
                    or _binding_signature(old_binding)
                    != _binding_signature(new_binding)
                )
                discard = bool(new_binding is None or not _enabled(new_binding))
                if not discard and (
                    (
                        job.mode == "automatic"
                        and (not _automatic(new_binding) or changed)
                    )
                    or (
                        job.mode == "manual"
                        and job.not_before > 0
                        and changed
                    )
                ):
                    discard = True
                if discard:
                    self._pending.pop(source_id, None)
                    if job.mode == "manual":
                        self._set_refresh_state_locked(
                            source_id,
                            job.request_id,
                            "cancelled",
                            "cancelled",
                        )
            if cancel_stale and self._active_source_id:
                old_binding = previous.get(self._active_source_id)
                new_binding = loaded.get(self._active_source_id)
                if (
                    new_binding is None
                    or not _enabled(new_binding)
                    or old_binding is None
                    or _pipeline_signature(old_binding)
                    != _pipeline_signature(new_binding)
                ):
                    if self._active_cancel is not None:
                        self._cancel_active_locked("cancelled")
            if cancel_stale:
                for source_id in set(previous).union(loaded):
                    old_binding = previous.get(source_id)
                    new_binding = loaded.get(source_id)
                    old_enabled = old_binding is not None and _enabled(old_binding)
                    new_enabled = new_binding is not None and _enabled(new_binding)
                    if old_enabled and not new_enabled:
                        self._snapshot_actions[source_id] = ("forget", None)
                    elif new_enabled and (
                        not old_enabled
                        or _snapshot_signature(old_binding)
                        != _snapshot_signature(new_binding)
                    ):
                        self._snapshot_actions[source_id] = (
                            "load",
                            _copy_binding(new_binding),
                        )
            self._condition.notify_all()
        return True

    def _on_timer(self):
        if self._stopped:
            return
        purpose = self._timer_purpose
        self._timer_purpose = ""
        with self._condition:
            active = bool(self._active_mode)
            control_pending = bool(
                self._settings_reload_requested
                or (
                    self._snapshot_actions
                    and not self._settings_reload_failed
                )
            )
            if (
                active
                and self._active_deadline > 0
                and time.monotonic() >= self._active_deadline
                and self._active_cancel is not None
            ):
                self._cancel_active_locked("deadline")
        if purpose == "poll":
            if active or control_pending:
                self._arm_poll()
            else:
                self._schedule_next()
            return
        self._request_settings_reload(reconcile=True)
        if not self._stopped:
            self._arm_poll()

    def _schedule_next(self):
        if self._stopped:
            return
        monotonic_now = time.monotonic()
        wall_now = int(time.time())
        next_delay = None
        queued_ready = False
        with self._condition:
            if self._active_mode:
                self._arm_poll()
                return
            settings_failed = self._settings_reload_failed
            if self._snapshot_actions and not settings_failed:
                self._condition.notify_all()
                self._arm_poll()
                return
            if not settings_failed:
                for job in self._pending.values():
                    remaining = job.not_before - monotonic_now
                    if remaining <= 0:
                        queued_ready = True
                        break
                    if next_delay is None or remaining < next_delay:
                        next_delay = remaining

            if settings_failed and (
                next_delay is None or SETTINGS_RETRY_SECONDS < next_delay
            ):
                next_delay = float(SETTINGS_RETRY_SECONDS)

            if not settings_failed:
                for source_id, binding in self._bindings.items():
                    if (
                        not _automatic(binding)
                        or source_id == self._active_source_id
                        or source_id in self._pending
                    ):
                        continue
                    not_before = self._automatic_not_before(
                        binding,
                        monotonic_now,
                        wall_now,
                    )
                    remaining = not_before - monotonic_now
                    if remaining <= 0:
                        self._pending[source_id] = _QueuedJob(
                            source_id,
                            "automatic",
                        )
                        queued_ready = True
                    elif next_delay is None or remaining < next_delay:
                        next_delay = remaining
            if queued_ready:
                self._condition.notify_all()

        if queued_ready:
            self._arm_poll()
        elif next_delay is not None:
            self._arm_timer(max(1.0, next_delay), "periodic")
        else:
            self._arm_timer(IDLE_RECHECK_SECONDS, "settings-recheck")

    def _next_ready_job(self):
        now = time.monotonic()
        for source_id, job in tuple(self._pending.items()):
            if job.not_before <= now:
                self._pending.pop(source_id, None)
                return job
        return None

    def _next_pending_delay(self):
        if self._settings_reload_failed:
            deadlines = []
        else:
            deadlines = [job.not_before for job in self._pending.values()]
        if self._settings_reload_failed and self._settings_retry_not_before > 0:
            deadlines.append(self._settings_retry_not_before)
        if not deadlines:
            return None
        now = time.monotonic()
        return max(
            0.01,
            min(deadlines) - now,
        )

    def _take_settings_reload_locked(self):
        retry_due = bool(
            self._settings_reload_failed
            and self._settings_retry_not_before > 0
            and time.monotonic() >= self._settings_retry_not_before
        )
        if not self._settings_reload_requested and not retry_due:
            return False, False
        reconcile = bool(self._settings_reconcile_requested or retry_due)
        self._settings_reload_requested = False
        self._settings_reconcile_requested = False
        if retry_due:
            self._settings_retry_not_before = 0.0
        self._active_source_id = ""
        self._active_mode = "settings"
        self._active_cancel = None
        self._active_cancel_reason = ""
        self._active_deadline = 0.0
        return True, reconcile

    def _worker_main(self):
        predecessor = self._predecessor
        self._predecessor = None
        if (
            predecessor is not None
            and predecessor is not threading.current_thread()
        ):
            predecessor.join()
        # Drain every settings notification which raced initial loading before
        # exposing cached snapshots from this new session's RAM store.
        while True:
            with self._condition:
                if self._stopped:
                    return
                settings_reload, reconcile = (
                    self._take_settings_reload_locked()
                )
            if settings_reload:
                self._run_settings_reload(reconcile)
                continue
            if self._preload_snapshots():
                break
        while True:
            with self._condition:
                settings_reload = False
                reconcile = False
                action = None
                job = None
                settings_reload, reconcile = self._take_settings_reload_locked()
                if settings_reload:
                    source_id = ""
                elif self._snapshot_actions and not self._settings_reload_failed:
                    source_id, action = self._snapshot_actions.popitem(last=False)
                elif not self._settings_reload_failed:
                    source_id = ""
                    job = self._next_ready_job()
                else:
                    source_id = ""
                while (
                    not self._stopped
                    and not settings_reload
                    and action is None
                    and job is None
                ):
                    self._condition.wait(self._next_pending_delay())
                    settings_reload, reconcile = (
                        self._take_settings_reload_locked()
                    )
                    if settings_reload:
                        source_id = ""
                    elif (
                        self._snapshot_actions
                        and not self._settings_reload_failed
                    ):
                        source_id, action = self._snapshot_actions.popitem(last=False)
                    elif not self._settings_reload_failed:
                        source_id = ""
                        job = self._next_ready_job()
                    else:
                        source_id = ""
                if self._stopped:
                    return
                if settings_reload:
                    pass
                elif action is not None:
                    self._active_source_id = source_id
                    self._active_mode = "snapshot"
                    self._active_cancel = None
                    self._active_cancel_reason = ""
                    self._active_deadline = 0.0
                else:
                    binding = self._bindings.get(job.source_id)
                    if binding is None or not _enabled(binding):
                        self._set_refresh_state_locked(
                            job.source_id,
                            job.request_id,
                            "cancelled",
                            "cancelled",
                        )
                        continue
                    binding = _copy_binding(binding)
                    cancel_event = threading.Event()
                    self._active_source_id = job.source_id
                    self._active_mode = job.mode
                    self._active_cancel = cancel_event
                    self._active_cancel_reason = ""
                    self._active_deadline = (
                        time.monotonic() + WORKER_TIMEOUT_SECONDS
                    )
                    deadline = self._active_deadline
                    self._set_refresh_state_locked(
                        job.source_id,
                        job.request_id,
                        "running",
                    )

            if settings_reload:
                self._run_settings_reload(reconcile)
                continue

            if action is not None:
                try:
                    self._apply_snapshot_action(source_id, action)
                finally:
                    with self._condition:
                        self._active_source_id = ""
                        self._active_mode = ""
                        self._active_cancel = None
                        self._active_cancel_reason = ""
                        self._active_deadline = 0.0
                        self._condition.notify_all()
                continue

            outcome = "failed"
            try:
                outcome = self._refresh_job(binding, cancel_event, deadline)
            except Exception as error:
                log_event("m3u_epg", "External XMLTV worker failed", error)
            finally:
                with self._condition:
                    requeued = False
                    if not self._stopped:
                        current = self._bindings.get(job.source_id)
                        unchanged = bool(
                            current is not None
                            and _binding_signature(current)
                            == _binding_signature(binding)
                        )
                        if (
                            outcome in ("runtime", "clock")
                            and unchanged
                            and _enabled(current)
                        ):
                            self._pending[job.source_id] = _QueuedJob(
                                job.source_id,
                                job.mode,
                                time.monotonic()
                                + (
                                    CLOCK_RETRY_SECONDS
                                    if outcome == "clock"
                                    else RUNTIME_RETRY_SECONDS
                                ),
                                request_id=job.request_id,
                            )
                            requeued = True
                        elif (
                            outcome in ("failed", "status-failed")
                            and unchanged
                            and _automatic(current)
                        ):
                            self._pending[job.source_id] = _QueuedJob(
                                job.source_id,
                                "automatic",
                                time.monotonic() + FAILURE_RETRY_SECONDS,
                            )
                    if job.mode == "manual":
                        if requeued:
                            state = "queued"
                            error_code = outcome
                        elif outcome == "success":
                            state = "success"
                            error_code = ""
                        elif outcome in ("cancelled", "stale"):
                            state = "cancelled"
                            error_code = "cancelled"
                        else:
                            state = "failed"
                            current = self._bindings.get(job.source_id)
                            error_code = _value(
                                current,
                                "last_error_code",
                                outcome,
                            )
                        self._set_refresh_state_locked(
                            job.source_id,
                            job.request_id,
                            state,
                            error_code,
                        )
                    self._active_source_id = ""
                    self._active_mode = ""
                    self._active_cancel = None
                    self._active_cancel_reason = ""
                    self._active_deadline = 0.0
                    self._condition.notify_all()
            self._queue_automatic_fallback()

    def _run_settings_reload(self, reconcile):
        succeeded = False
        try:
            succeeded = self._load_bindings_on_worker(
                cancel_stale=bool(reconcile)
            )
        finally:
            with self._condition:
                self._active_source_id = ""
                self._active_mode = ""
                self._active_cancel = None
                self._active_cancel_reason = ""
                self._active_deadline = 0.0
                self._condition.notify_all()
        if succeeded:
            self._queue_automatic_fallback()

    def _apply_snapshot_action(self, source_id, action):
        operation, binding = action
        try:
            store = self._store
            if store is None:
                store = get_default_store()
                self._store = store
            if operation == "forget":
                forgetter = getattr(store, "forget", None)
                if callable(forgetter):
                    forgetter(source_id)
            else:
                loader = getattr(store, "load_from_disk", None)
                if callable(loader):
                    loader(source_id, binding=binding)
        except Exception as error:
            log_event("m3u_epg", "External XMLTV snapshot state could not change", error)

    def _preload_snapshots(self):
        with self._condition:
            if self._stopped or self._settings_reload_requested:
                return False
            bindings = tuple(
                _copy_binding(binding)
                for binding in self._bindings.values()
                if _enabled(binding)
            )
            self._active_mode = "preload"
        completed = True
        try:
            store = get_default_store()
            self._store = store
            loader = getattr(store, "load_from_disk", None)
            if callable(loader):
                for binding in bindings:
                    with self._condition:
                        if self._stopped or self._settings_reload_requested:
                            completed = False
                            break
                    try:
                        loader(_source_id(binding), binding=binding)
                    except Exception as error:
                        log_event(
                            "m3u_epg",
                            "Cached external XMLTV snapshot could not be loaded",
                            error,
                        )
        except Exception as error:
            log_event("m3u_epg", "External XMLTV store could not be prepared", error)
        finally:
            with self._condition:
                if self._stopped or self._settings_reload_requested:
                    completed = False
                self._active_mode = ""
                self._condition.notify_all()
        return completed

    def _refresh_job(self, binding, cancel_event, deadline):
        source_id = _source_id(binding)
        if not _clock_is_sane():
            return "clock"
        if not acquire_runtime(self._owner):
            return "runtime"
        attempted_at = int(time.time())
        worker_started = time.monotonic()
        diagnostics = {}
        summary = None
        error = None
        try:
            source = self._find_source(source_id)
            if source is None:
                raise _RefreshError("m3u-source-unavailable")
            if cancel_event.is_set():
                raise _RefreshError("cancelled")
            client = M3UContentClient(source)
            request_scope = getattr(client, "request_scope", None)
            if callable(request_scope):
                with request_scope(
                    cancel_event=cancel_event,
                    deadline=deadline,
                ):
                    client.prepare()
                    wanted_ids = _prepared_channel_ids(client)
            else:
                client.prepare()
                wanted_ids = _prepared_channel_ids(client)
            if time.monotonic() >= deadline:
                raise _RefreshError("deadline")
            if cancel_event.is_set():
                raise _RefreshError("cancelled")
            if not wanted_ids:
                raise _RefreshError("no-tvg-id")
            store = self._store
            if store is None:
                store = get_default_store()
                self._store = store
            summary = refresh_source(
                source_id,
                binding,
                wanted_ids,
                store=store,
                cancel_event=cancel_event,
                deadline=deadline,
                diagnostics=diagnostics,
            )
            if not isinstance(summary, dict):
                raise _RefreshError("invalid-refresh-result")
        except Exception as caught:
            error = caught
        finally:
            release_runtime(self._owner)

        diagnostics["worker_total_ms"] = _bounded_diagnostic_value(
            max(0.0, time.monotonic() - worker_started) * 1000.0
        )

        if error is None:
            _log_refresh_timing(source_id, diagnostics, "success")
            persisted = self._persist_status(
                binding,
                attempted_at,
                summary=summary,
            )
            log_event(
                "m3u_epg",
                "External XMLTV refresh completed for source {}".format(source_id),
            )
            if persisted is True:
                return "success"
            return "status-failed" if persisted is False else "stale"
        with self._condition:
            cancel_reason = self._active_cancel_reason
        reached_deadline = bool(time.monotonic() >= deadline)
        error_code = _error_code(error)
        if error_code == "cancelled" and (
            cancel_reason == "deadline"
            or (not cancel_reason and reached_deadline)
        ):
            error_code = "deadline"
        _log_refresh_timing(source_id, diagnostics, error_code)
        cancelled = bool(error_code == "cancelled")
        if self._stopped:
            return "cancelled"
        self._persist_status(
            binding,
            attempted_at,
            error_code=error_code,
        )
        log_event(
            "m3u_epg",
            "External XMLTV refresh failed for source {}".format(source_id),
            error,
        )
        return "cancelled" if cancelled else "failed"

    @staticmethod
    def _find_source(source_id):
        try:
            sources = tuple(load_m3u_sources() or ())
        except Exception:
            return None
        for source in sources:
            if str(getattr(source, "source_id", "") or "") == source_id:
                return source
        return None

    def _persist_status(self, original, attempted_at, summary=None, error_code=""):
        source_id = _source_id(original)
        signature = _pipeline_signature(original)

        def update_status(current):
            if current is None or _pipeline_signature(current) != signature:
                return None
            candidate = _copy_binding(current)
            _set_value(candidate, "last_attempt_utc", max(0, int(attempted_at)))
            if summary is None:
                # A failed fetch must not discard last-good timestamps/counts.
                _set_value(candidate, "last_error_code", str(error_code or "failed")[:48])
            else:
                _set_value(candidate, "last_success_utc", int(time.time()))
                _set_value(candidate, "last_error_code", "")
                _set_value(
                    candidate,
                    "last_channel_count",
                    _safe_count(summary.get("matched_ids", summary.get("channel_count", 0))),
                )
                _set_value(
                    candidate,
                    "last_event_count",
                    _safe_count(summary.get("event_count", 0)),
                )
            return candidate

        try:
            updated = update_m3u_epg_binding(source_id, update_status)
        except Exception as error:
            log_event("m3u_epg", "External XMLTV status could not be saved", error)
            return False
        if updated is None:
            return None
        with self._condition:
            current = self._bindings.get(source_id)
            if current is None or _pipeline_signature(current) != signature:
                return None
            candidate = _copy_binding(current)
            for field in (
                "last_attempt_utc",
                "last_success_utc",
                "last_error_code",
                "last_channel_count",
                "last_event_count",
            ):
                _set_value(candidate, field, _value(updated, field))
            self._bindings[source_id] = candidate
        return True


_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


def session_start(reason, session=None, **kwargs):
    """Create or stop the singleton from ``WHERE_SESSIONSTART``."""
    del kwargs
    global _INSTANCE
    try:
        reason_value = int(reason)
    except (TypeError, ValueError, OverflowError):
        reason_value = 0 if session is not None else 1
    with _INSTANCE_LOCK:
        if reason_value == 0 and session is not None:
            if _INSTANCE is not None and _INSTANCE.session is session:
                return
            previous = _INSTANCE
            predecessor = None
            _INSTANCE = None
            if previous is not None:
                predecessor = previous._worker
                try:
                    previous.stop()
                except Exception as error:
                    log_event("m3u_epg", "Previous XMLTV scheduler could not stop", error)
            try:
                _INSTANCE = M3UEPGScheduler(
                    session,
                    predecessor=predecessor,
                )
            except Exception as error:
                log_event("m3u_epg", "External XMLTV scheduler could not start", error)
        elif reason_value != 0:
            instance = _INSTANCE
            _INSTANCE = None
            if instance is not None:
                try:
                    instance.stop()
                except Exception as error:
                    log_event("m3u_epg", "External XMLTV scheduler could not stop", error)


def notify_settings_changed():
    """Reload bindings and re-arm the live session scheduler."""
    instance = _INSTANCE
    if instance is not None:
        instance.notify_settings_changed()


def request_refresh(source_id):
    """Queue one manual source refresh, deduplicated against active work."""
    instance = _INSTANCE
    if instance is None:
        return False
    return instance.request_refresh(source_id)


def get_refresh_state(source_id):
    """Return one source's latest manual refresh state without blocking."""
    instance = _INSTANCE
    if instance is None:
        return None
    return instance.get_refresh_state(source_id)


def is_busy():
    """Return whether XMLTV or the shared DVB EPG runtime is occupied."""
    instance = _INSTANCE
    return runtime_is_busy() or bool(instance is not None and instance.busy())

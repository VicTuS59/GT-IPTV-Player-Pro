# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Session-owned automatic Xtream XMLTV to native DVB EPG scheduler."""

import threading
import time

from .diagnostics import log_event
from .dvb_epg import (
    DEFAULT_IMPORT_PAST_SECONDS,
    DvbEpgError,
    OperationCancelled,
    build_import_event_groups,
    download_xtream_xmltv,
    load_dvb_services,
    load_manual_mappings,
    match_xmltv_channels,
    scan_xmltv,
    xtream_source_key,
)
from .dvb_epg_runtime import (
    acquire_runtime,
    release_runtime,
    runtime_is_busy,
    runtime_is_owned_by,
)
from .dvb_epg_settings import (
    ALL_BOUQUETS,
    DVBEPGSettings,
    load_dvb_epg_settings,
    update_dvb_epg_settings,
)
from .playlist import load_accounts


STARTUP_DELAY_SECONDS = 90
STANDBY_DELAY_SECONDS = 15
SETTINGS_DELAY_SECONDS = 5
PLAYBACK_RETRY_SECONDS = 10 * 60
FAILURE_RETRY_SECONDS = 30 * 60
CLOCK_RETRY_SECONDS = 10 * 60
WORKER_TIMEOUT_SECONDS = 180
WORKER_POLL_MILLISECONDS = 500
IMPORT_POLL_MILLISECONDS = 120
IMPORT_BATCH_EVENTS = 2000
IMPORT_BATCH_GROUPS = 8
SUPPORTED_INTERVALS = (6, 12, 24)


def _connect_timer(timer, callback):
    try:
        return timer.timeout.connect(callback)
    except AttributeError:
        timer.callback.append(callback)
        return None


def _clock_is_sane(now=None):
    try:
        year = time.gmtime(time.time() if now is None else now).tm_year
    except (OverflowError, OSError, TypeError, ValueError):
        return False
    return 2020 <= int(year) <= 2100


def _source_type(source):
    return str(getattr(source, "source_type", "xtream") or "xtream").lower()


def _interval_hours(settings):
    try:
        value = int(getattr(settings, "update_interval_hours", 24))
    except (TypeError, ValueError, OverflowError):
        value = 24
    return value if value in SUPPORTED_INTERVALS else 24


def _automatic_enabled(settings):
    return bool(getattr(settings, "enabled", False)) and str(
        getattr(settings, "update_mode", "automatic") or "automatic"
    ).lower() == "automatic"


def _settings_copy(settings):
    if isinstance(settings, DVBEPGSettings):
        return settings.copy()
    return DVBEPGSettings.from_dict(settings)


def _pipeline_signature(settings):
    return (
        str(getattr(settings, "source_key", "") or ""),
        str(getattr(settings, "bouquet_file", ALL_BOUQUETS) or ALL_BOUQUETS),
        int(getattr(settings, "epg_days", 3)),
    )


class _JobToken(object):
    """Cancellation/deadline token shared with the bounded core pipeline."""

    def __init__(self, timeout=WORKER_TIMEOUT_SECONDS):
        self.cancel_event = threading.Event()
        self.deadline = time.monotonic() + max(1.0, float(timeout))
        self.cancel_reason = ""

    def cancel(self, reason=""):
        if reason and not self.cancel_reason:
            self.cancel_reason = str(reason)
        self.cancel_event.set()

    def check(self):
        if self.cancel_event.is_set() or time.monotonic() >= self.deadline:
            raise OperationCancelled("cancelled")


class DVBEPGScheduler(object):
    """Own one automatic update lifecycle for an Enigma2 GUI session."""

    def __init__(self, session):
        self.session = session
        self._lock = threading.Lock()
        self._owner = object()
        self._stopped = False
        self._timer = None
        self._timer_connection = None
        self._timer_purpose = ""
        self._settings = DVBEPGSettings()
        self._job = None
        self._worker = None
        self._result = None
        self._result_ready = False
        self._job_source_key = ""
        self._job_signature = None
        self._reconfigure_pending = False
        self._native_cache = None
        self._native_groups = None
        self._native_group_index = 0
        self._native_event_offset = 0
        self._native_import_count = 0
        self._native_mapping_count = 0
        self._standby_counter = None
        self._standby_dialog_id = None
        self._standby_dialog = None
        self._last_defer_reason = ""
        self._session_started_monotonic = time.monotonic()
        self._startup_suppression_pending = False
        self._startup_catchup_blocked = False
        self._startup_catchup_pending = False
        self._job_allow_current_service = False
        self._install_standby_notifier()
        self.notify_settings_changed(startup=True)

    def busy(self):
        """Return whether this scheduler owns or is finishing an update."""
        with self._lock:
            active = (
                self._job is not None
                or self._worker is not None
                or self._result_ready
                or self._native_groups is not None
            )
        return active or runtime_is_owned_by(self._owner)

    def notify_settings_changed(self, startup=False):
        """Reload persisted settings and safely re-arm automatic work."""
        if self._stopped:
            return
        previous = self._settings
        try:
            loaded = load_dvb_epg_settings()
            settings = _settings_copy(loaded)
        except Exception as error:
            log_event("dvb_epg", "Automatic settings could not be loaded", error)
            settings = DVBEPGSettings()
        self._settings = settings

        automatic = _automatic_enabled(settings)
        startup_enabled = bool(
            getattr(settings, "update_on_startup", True)
        )
        clock_ready = _clock_is_sane()
        if startup:
            startup_disabled = not startup_enabled
            self._startup_suppression_pending = bool(
                automatic and startup_disabled and not clock_ready
            )
            self._startup_catchup_blocked = bool(
                automatic
                and startup_disabled
                and clock_ready
                and self._is_due(settings)
            )
            self._startup_catchup_pending = bool(
                automatic
                and startup_enabled
                and (not clock_ready or self._is_due(settings))
            )
        elif not automatic:
            self._startup_catchup_pending = False
            self._job_allow_current_service = False
            self._clear_startup_suppression()
        elif not startup_enabled:
            self._startup_catchup_pending = False
            self._job_allow_current_service = False
        elif not _automatic_enabled(previous) or not bool(
            getattr(previous, "update_on_startup", True)
        ):
            # Enabling catch-up, or intentionally switching into automatic
            # mode after boot, is an explicit request to resume scheduling.
            self._startup_catchup_pending = bool(
                not clock_ready or self._is_due(settings)
            )
            self._clear_startup_suppression()
        elif startup_enabled:
            self._clear_startup_suppression()
        if self.busy():
            if not automatic or (
                self._job_signature is not None
                and self._job_signature != _pipeline_signature(settings)
            ):
                self._reconfigure_pending = True
                if self._native_groups is not None:
                    log_event(
                        "dvb_epg",
                        "Automatic native EPG import stopped after settings changed",
                    )
                    self._clear_native_import()
                    self._finish_cycle(retry_seconds=None)
                    return
                self._cancel_preparation("settings")
            return

        if not automatic:
            self._stop_timer()
            return

        self._reconfigure_pending = False
        if not clock_ready:
            self._defer("clock", CLOCK_RETRY_SECONDS)
            return
        self._resolve_startup_suppression(settings)
        now = int(time.time())
        if not self._is_due(settings, now):
            self._startup_catchup_pending = False
            self._clear_startup_suppression()
            self._arm_due_timer(settings, now)
            return
        suppressed = self._startup_suppression_remaining()
        if suppressed > 0:
            self._arm_timer(suppressed, "startup-suppressed")
            return
        delay = STARTUP_DELAY_SECONDS if startup else SETTINGS_DELAY_SECONDS
        self._arm_timer(delay, "startup" if startup else "settings")

    def stop(self):
        """Stop timers/notifiers and cancel preparation during GUI shutdown."""
        if self._stopped:
            return
        self._stopped = True
        self._disconnect_timer()
        self._remove_standby_notifier()
        self._cancel_preparation("shutdown")
        self._clear_native_import()
        with self._lock:
            worker_running = self._worker is not None
        if not worker_running:
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

    def _arm_poll(self, milliseconds=WORKER_POLL_MILLISECONDS):
        if self._stopped:
            return
        timer = self._ensure_timer()
        self._timer_purpose = "poll"
        timer.start(max(1, int(milliseconds)), True)

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

    def _is_due(self, settings, now=None):
        now = int(time.time() if now is None else now)
        try:
            last_success = max(0, int(getattr(settings, "last_success_utc", 0)))
        except (TypeError, ValueError, OverflowError):
            last_success = 0
        if last_success <= 0:
            return True
        return now >= last_success + (_interval_hours(settings) * 60 * 60)

    def _arm_due_timer(self, settings, now=None):
        now = int(time.time() if now is None else now)
        try:
            last_success = max(0, int(getattr(settings, "last_success_utc", 0)))
        except (TypeError, ValueError, OverflowError):
            last_success = 0
        due_at = last_success + (_interval_hours(settings) * 60 * 60)
        self._arm_timer(max(1, due_at - now), "periodic")

    def _startup_suppression_remaining(self):
        if not self._startup_catchup_blocked:
            return 0
        due_at = self._session_started_monotonic + (
            _interval_hours(self._settings) * 60 * 60
        )
        remaining = due_at - time.monotonic()
        if remaining <= 0:
            self._startup_catchup_blocked = False
            return 0
        return max(1, int(remaining))

    def _resolve_startup_suppression(self, settings):
        if not self._startup_suppression_pending or not _clock_is_sane():
            return
        self._startup_suppression_pending = False
        self._startup_catchup_blocked = self._is_due(settings)

    def _clear_startup_suppression(self):
        self._startup_suppression_pending = False
        self._startup_catchup_blocked = False

    def _on_timer(self):
        if self._stopped:
            return
        self._timer_purpose = ""
        if self.busy():
            self._poll_busy()
            return
        try:
            loaded = load_dvb_epg_settings()
            self._settings = _settings_copy(loaded)
        except Exception as error:
            log_event("dvb_epg", "Automatic settings could not be loaded", error)
            self._defer("settings", FAILURE_RETRY_SECONDS)
            return
        settings = self._settings
        if not _automatic_enabled(settings):
            self._startup_catchup_pending = False
            self._job_allow_current_service = False
            self._clear_startup_suppression()
            self._stop_timer()
            return
        startup_enabled = bool(getattr(settings, "update_on_startup", True))
        if startup_enabled:
            self._clear_startup_suppression()
        else:
            self._startup_catchup_pending = False
            self._job_allow_current_service = False
        if not _clock_is_sane():
            self._defer("clock", CLOCK_RETRY_SECONDS)
            return
        self._resolve_startup_suppression(settings)
        now = int(time.time())
        if not self._is_due(settings, now):
            self._startup_catchup_pending = False
            self._clear_startup_suppression()
            self._arm_due_timer(settings, now)
            return
        in_standby = self._in_standby()
        standby_allowed = bool(getattr(settings, "update_in_standby", True))
        if in_standby and not standby_allowed:
            self._defer("standby-disabled", PLAYBACK_RETRY_SECONDS)
            return
        suppressed = self._startup_suppression_remaining()
        if suppressed > 0 and not (in_standby and standby_allowed):
            self._arm_timer(suppressed, "startup-suppressed")
            return
        allow_current_service = bool(
            self._startup_catchup_pending and startup_enabled
        )
        if self._service_is_playing(
            ignore_current=(
                allow_current_service or (in_standby and standby_allowed)
            )
        ):
            self._defer("playback", PLAYBACK_RETRY_SECONDS)
            return
        if runtime_is_busy(excluding=self._owner):
            self._defer("runtime", PLAYBACK_RETRY_SECONDS)
            return
        self._start_worker(
            settings,
            allow_current_service=allow_current_service,
        )

    def _start_worker(self, settings, allow_current_service=False):
        if self._stopped or self.busy():
            return
        try:
            snapshot = _settings_copy(settings)
            token = _JobToken()
            worker = threading.Thread(
                target=lambda: self._run_worker(token, snapshot),
                name="GTDVBEPGAutomatic",
            )
            worker.daemon = True
        except Exception as error:
            log_event("dvb_epg", "Automatic worker could not be created", error)
            self._defer("worker", FAILURE_RETRY_SECONDS)
            return
        if not acquire_runtime(self._owner):
            self._defer("runtime", PLAYBACK_RETRY_SECONDS)
            return
        with self._lock:
            self._job = token
            self._result = None
            self._result_ready = False
            self._job_source_key = str(getattr(snapshot, "source_key", "") or "")
            self._job_signature = _pipeline_signature(snapshot)
            self._job_allow_current_service = bool(allow_current_service)
            self._worker = worker
        self._last_defer_reason = ""
        log_event("dvb_epg", "Automatic DVB EPG update started")
        try:
            worker.start()
        except Exception as error:
            with self._lock:
                self._job = None
                self._worker = None
                self._job_source_key = ""
                self._job_signature = None
                self._job_allow_current_service = False
            release_runtime(self._owner)
            log_event("dvb_epg", "Automatic worker could not be started", error)
            self._defer("worker", FAILURE_RETRY_SECONDS)
            return
        self._arm_poll()

    def _run_worker(self, token, settings):
        payload = None
        try:
            account = self._find_account(settings.source_key)
            if account is None:
                raise DvbEpgError("configured Xtream source is unavailable")
            bouquet_files = (
                None
                if settings.bouquet_file == ALL_BOUQUETS
                else (settings.bouquet_file,)
            )
            services = load_dvb_services(
                bouquet_files=bouquet_files,
                cancel_event=token.cancel_event,
                deadline=token.deadline,
            )
            if not services:
                raise DvbEpgError("no DVB services")
            download = download_xtream_xmltv(
                account,
                cancel_event=token.cancel_event,
                deadline=token.deadline,
            )
            scan = scan_xmltv(
                download.path,
                cancel_event=token.cancel_event,
                deadline=token.deadline,
            )
            matched = match_xmltv_channels(
                scan.channels,
                services,
                manual_entries=load_manual_mappings(),
                source_key=xtream_source_key(account),
            )
            imported = build_import_event_groups(
                download.path,
                matched.mappings,
                now=int(time.time()),
                past_seconds=DEFAULT_IMPORT_PAST_SECONDS,
                future_seconds=int(settings.epg_days) * 24 * 60 * 60,
                allowed_services=services,
                cancel_event=token.cancel_event,
                deadline=token.deadline,
            )
            token.check()
            payload = (True, len(matched.mappings), imported)
        except OperationCancelled as error:
            payload = (False, "cancelled", error, token.cancel_reason)
        except Exception as error:
            payload = (False, "failed", error)

        release_owner = False
        with self._lock:
            if self._stopped:
                self._job = None
                self._worker = None
                release_owner = True
            elif self._job is token:
                self._worker = None
                self._result = payload
                self._result_ready = True
        if release_owner:
            release_runtime(self._owner)

    def _find_account(self, source_key):
        try:
            candidates = list(getattr(load_accounts(), "accounts", ()) or ())
        except Exception:
            candidates = []
        for account in candidates:
            if _source_type(account) != "xtream":
                continue
            try:
                if xtream_source_key(account) == source_key:
                    return account
            except (TypeError, ValueError):
                continue
        return None

    def _poll_busy(self):
        with self._lock:
            worker = self._worker
            token = self._job
            ready = self._result_ready
        if worker is not None and token is not None:
            if time.monotonic() >= token.deadline:
                token.cancel("timeout")
            in_standby = self._in_standby()
            standby_enabled = bool(
                getattr(self._settings, "update_in_standby", True)
            )
            if in_standby and not standby_enabled:
                token.cancel("standby-disabled")
                self._log_defer_once("standby-disabled")
                self._arm_poll()
                return
            allow_current_service = bool(
                self._job_allow_current_service
                and getattr(self._settings, "update_on_startup", True)
            )
            if self._service_is_playing(
                ignore_current=(in_standby or allow_current_service)
            ):
                token.cancel("playback")
                self._log_defer_once("playback-cancel")
            self._arm_poll()
            return
        if ready:
            self._apply_worker_result()
            return
        if self._native_groups is not None:
            in_standby = self._in_standby()
            standby_enabled = bool(
                getattr(self._settings, "update_in_standby", True)
            )
            if in_standby and not standby_enabled:
                log_event(
                    "dvb_epg",
                    "Automatic native EPG import stopped by standby setting",
                )
                self._clear_native_import()
                self._finish_cycle(retry_seconds=PLAYBACK_RETRY_SECONDS)
                return
            allow_current_service = bool(
                self._job_allow_current_service
                and getattr(self._settings, "update_on_startup", True)
            )
            if self._service_is_playing(
                ignore_current=(in_standby or allow_current_service)
            ):
                log_event(
                    "dvb_epg",
                    "Automatic native EPG import stopped because playback started",
                )
                self._clear_native_import()
                self._finish_cycle(retry_seconds=PLAYBACK_RETRY_SECONDS)
                return
            self._poll_native_import()
            return
        # A cancelled job may have cleared itself between timer callbacks.
        self._finish_cycle(retry_seconds=FAILURE_RETRY_SECONDS)

    def _apply_worker_result(self):
        with self._lock:
            result = self._result
            self._result = None
            self._result_ready = False
            self._job = None
        if not result or not result[0]:
            error = result[2] if result and len(result) > 2 else None
            cancel_reason = result[3] if result and len(result) > 3 else ""
            if result and result[1] == "cancelled":
                log_event("dvb_epg", "Automatic DVB EPG preparation was cancelled", error)
            else:
                log_event("dvb_epg", "Automatic DVB EPG update failed", error)
            retry_seconds = FAILURE_RETRY_SECONDS
            if cancel_reason in ("playback", "standby-disabled"):
                retry_seconds = PLAYBACK_RETRY_SECONDS
            elif cancel_reason == "settings":
                retry_seconds = SETTINGS_DELAY_SECONDS
            self._finish_cycle(retry_seconds=retry_seconds)
            return
        if self._reconfigure_pending:
            log_event("dvb_epg", "Prepared automatic EPG data discarded after settings changed")
            self._finish_cycle(retry_seconds=None)
            return
        in_standby = self._in_standby()
        standby_enabled = bool(
            getattr(self._settings, "update_in_standby", True)
        )
        if in_standby and not standby_enabled:
            log_event(
                "dvb_epg",
                "Automatic DVB EPG update deferred by standby setting",
            )
            self._finish_cycle(retry_seconds=PLAYBACK_RETRY_SECONDS)
            return
        allow_current_service = bool(
            self._job_allow_current_service
            and getattr(self._settings, "update_on_startup", True)
        )
        if self._service_is_playing(
            ignore_current=(in_standby or allow_current_service)
        ):
            log_event("dvb_epg", "Automatic DVB EPG update deferred before native import")
            self._finish_cycle(retry_seconds=PLAYBACK_RETRY_SECONDS)
            return
        mapping_count, imported = result[1], result[2]
        try:
            from enigma import eEPGCache

            cache = eEPGCache.getInstance()
            if cache is None or not callable(getattr(cache, "importEvents", None)):
                raise RuntimeError("native EPG import is unavailable")
        except Exception as error:
            log_event("dvb_epg", "Native EPG cache rejected the automatic update", error)
            self._finish_cycle(retry_seconds=FAILURE_RETRY_SECONDS)
            return
        self._native_cache = cache
        self._native_groups = tuple(imported.groups or ())
        self._native_group_index = 0
        self._native_event_offset = 0
        self._native_import_count = 0
        self._native_mapping_count = int(mapping_count)
        self._arm_poll(IMPORT_POLL_MILLISECONDS)

    def _next_native_batch(self):
        batch = []
        remaining = IMPORT_BATCH_EVENTS
        while (
            self._native_groups is not None
            and self._native_group_index < len(self._native_groups)
            and remaining > 0
            and len(batch) < IMPORT_BATCH_GROUPS
        ):
            services, events = self._native_groups[self._native_group_index]
            events = tuple(events or ())
            start = self._native_event_offset
            take = min(len(events) - start, remaining)
            if take > 0:
                batch.append((services, events[start : start + take]))
                self._native_event_offset += take
                remaining -= take
            if self._native_event_offset >= len(events):
                self._native_group_index += 1
                self._native_event_offset = 0
        return tuple(batch)

    def _poll_native_import(self):
        try:
            batch = self._next_native_batch()
            for services, events in batch:
                service_rows = [str(value) for value in services or () if value]
                event_rows = tuple(tuple(value) for value in events or ())
                if not service_rows or not event_rows:
                    continue
                if any(len(value) != 6 for value in event_rows):
                    raise RuntimeError("invalid EPG event tuple")
                self._native_cache.importEvents(service_rows, event_rows)
                self._native_import_count += len(event_rows)
            complete = self._native_group_index >= len(self._native_groups)
            if not complete:
                self._arm_poll(IMPORT_POLL_MILLISECONDS)
                return
            saver = getattr(self._native_cache, "save", None)
            if callable(saver):
                saver()
        except Exception as error:
            log_event("dvb_epg", "Native automatic EPG import failed", error)
            self._clear_native_import()
            self._finish_cycle(retry_seconds=FAILURE_RETRY_SECONDS)
            return

        imported_count = int(self._native_import_count)
        mapping_count = int(self._native_mapping_count)
        source_key = self._job_source_key
        job_signature = self._job_signature
        self._clear_native_import()
        persisted = self._persist_success(
            source_key,
            job_signature,
            mapping_count,
            imported_count,
        )
        log_event(
            "dvb_epg",
            "Automatic DVB EPG update completed: {} channels, {} events".format(
                mapping_count,
                imported_count,
            ),
        )
        if persisted is True:
            self._startup_catchup_pending = False
            retry_seconds = None
        elif persisted is None:
            retry_seconds = SETTINGS_DELAY_SECONDS
        else:
            retry_seconds = FAILURE_RETRY_SECONDS
        self._finish_cycle(retry_seconds=retry_seconds)

    def _persist_success(
        self,
        source_key,
        job_signature,
        mapping_count,
        event_count,
    ):
        def update_status(settings):
            settings = _settings_copy(settings)
            if (
                settings.source_key != source_key
                or job_signature is None
                or _pipeline_signature(settings) != job_signature
            ):
                return None
            settings.last_success_utc = int(time.time())
            settings.last_mapping_count = max(0, int(mapping_count))
            settings.last_event_count = max(0, int(event_count))
            return settings

        try:
            settings = update_dvb_epg_settings(update_status)
            if settings is None:
                return None
            self._settings = _settings_copy(settings)
            return True
        except Exception as error:
            log_event("dvb_epg", "Automatic update status could not be saved", error)
            return False

    def _finish_cycle(self, retry_seconds=None):
        release_runtime(self._owner)
        self._job_source_key = ""
        self._job_signature = None
        self._job_allow_current_service = False
        if self._stopped:
            self._stop_timer()
            return
        try:
            self._settings = _settings_copy(load_dvb_epg_settings())
        except Exception:
            self._settings = DVBEPGSettings()
        if not _automatic_enabled(self._settings):
            self._startup_catchup_pending = False
            self._reconfigure_pending = False
            self._stop_timer()
            return
        if not bool(getattr(self._settings, "update_on_startup", True)):
            self._startup_catchup_pending = False
        if self._reconfigure_pending:
            self._reconfigure_pending = False
            self._arm_timer(SETTINGS_DELAY_SECONDS, "settings")
            return
        if retry_seconds is not None and self._is_due(self._settings):
            self._arm_timer(retry_seconds, "retry")
            return
        if _clock_is_sane():
            now = int(time.time())
            if self._is_due(self._settings, now):
                self._arm_timer(SETTINGS_DELAY_SECONDS, "retry")
            else:
                self._startup_catchup_pending = False
                self._arm_due_timer(self._settings, now)
        else:
            self._defer("clock", CLOCK_RETRY_SECONDS)

    def _cancel_preparation(self, reason):
        with self._lock:
            token = self._job
            native_started = self._native_groups is not None
        if token is not None and not native_started:
            token.cancel(reason)
            if reason == "settings":
                log_event("dvb_epg", "Automatic preparation cancelled after settings changed")
            self._arm_poll()

    def _clear_native_import(self):
        self._native_cache = None
        self._native_groups = None
        self._native_group_index = 0
        self._native_event_offset = 0
        self._native_import_count = 0
        self._native_mapping_count = 0

    def _service_is_playing(self, ignore_current=False):
        session = self.session
        nav = getattr(session, "nav", None) if session is not None else None
        if nav is None:
            return False
        if bool(getattr(session, "pipshown", False)):
            return True
        recordings = getattr(nav, "getRecordings", None)
        if callable(recordings):
            try:
                if recordings():
                    return True
            except Exception:
                pass
        if ignore_current:
            # Startup catch-up and normal standby may keep the current service;
            # recordings and PiP were still rejected above.
            return False
        try:
            service = nav.getCurrentService()
            if service is not None:
                return True
        except Exception:
            pass
        try:
            reference = nav.getCurrentlyPlayingServiceReference()
            return reference is not None
        except Exception:
            return False

    def _in_standby(self):
        try:
            import Screens.Standby as Standby

            return getattr(Standby, "inStandby", None) is not None
        except Exception:
            return False

    def _install_standby_notifier(self):
        try:
            from Components.config import config

            counter = config.misc.standbyCounter
            try:
                counter.addNotifier(self._on_standby, initial_call=False)
            except TypeError:
                counter.addNotifier(self._on_standby)
            self._standby_counter = counter
        except Exception:
            self._standby_counter = None

    def _remove_standby_notifier(self):
        counter = self._standby_counter
        self._standby_counter = None
        if counter is not None:
            remover = getattr(counter, "removeNotifier", None)
            if callable(remover):
                try:
                    remover(self._on_standby)
                except Exception:
                    pass
        dialog = self._standby_dialog
        self._standby_dialog = None
        self._standby_dialog_id = None
        callbacks = getattr(dialog, "onClose", None)
        if isinstance(callbacks, list):
            try:
                callbacks.remove(self._on_leave_standby)
            except ValueError:
                pass

    def _on_standby(self, *args, **kwargs):
        if self._stopped:
            return
        try:
            import Screens.Standby as Standby

            dialog = getattr(Standby, "inStandby", None)
        except Exception:
            dialog = None
        if dialog is not None and id(dialog) != self._standby_dialog_id:
            callbacks = getattr(dialog, "onClose", None)
            if isinstance(callbacks, list):
                callbacks.append(self._on_leave_standby)
                self._standby_dialog_id = id(dialog)
                self._standby_dialog = dialog
        try:
            self._settings = _settings_copy(load_dvb_epg_settings())
        except Exception:
            return
        if (
            _automatic_enabled(self._settings)
            and bool(getattr(self._settings, "update_in_standby", True))
            and self._is_due(self._settings)
            and not self.busy()
        ):
            self._arm_timer(STANDBY_DELAY_SECONDS, "standby")

    def _on_leave_standby(self, *args, **kwargs):
        self._standby_dialog_id = None
        self._standby_dialog = None
        if self._stopped or self.busy():
            return
        try:
            self._settings = _settings_copy(load_dvb_epg_settings())
        except Exception:
            return
        if _automatic_enabled(self._settings):
            if not _clock_is_sane():
                self._defer("clock", CLOCK_RETRY_SECONDS)
                return
            if bool(getattr(self._settings, "update_on_startup", True)):
                self._clear_startup_suppression()
            self._resolve_startup_suppression(self._settings)
            if self._is_due(self._settings):
                suppressed = self._startup_suppression_remaining()
                if suppressed > 0:
                    self._arm_timer(suppressed, "startup-suppressed")
                else:
                    self._arm_timer(SETTINGS_DELAY_SECONDS, "retry")
            else:
                self._arm_due_timer(self._settings)

    def _defer(self, reason, seconds):
        self._log_defer_once(reason)
        self._arm_timer(seconds, "retry")

    def _log_defer_once(self, reason):
        if self._last_defer_reason == reason:
            return
        self._last_defer_reason = reason
        messages = {
            "clock": "Automatic update deferred until the receiver clock is ready",
            "playback": "Automatic update deferred while a service is playing",
            "playback-cancel": "Automatic preparation cancelled because playback started",
            "runtime": "Automatic update deferred while another DVB EPG update is running",
            "settings": "Automatic update deferred because settings could not be loaded",
            "standby-disabled": "Automatic update deferred during standby by user setting",
            "worker": "Automatic update deferred because its worker could not start",
        }
        log_event("dvb_epg", messages.get(reason, "Automatic DVB EPG update deferred"))


_INSTANCE = None
_INSTANCE_LOCK = threading.Lock()


def session_start(reason, session=None, **kwargs):
    """Create or stop the singleton scheduler from ``WHERE_SESSIONSTART``."""
    global _INSTANCE
    try:
        reason_value = int(reason)
    except (TypeError, ValueError, OverflowError):
        reason_value = 0 if session is not None else 1
    with _INSTANCE_LOCK:
        if reason_value == 0 and session is not None:
            if _INSTANCE is not None and _INSTANCE.session is session:
                return
            if _INSTANCE is not None:
                try:
                    _INSTANCE.stop()
                except Exception as error:
                    log_event("dvb_epg", "Previous automatic scheduler could not stop", error)
            _INSTANCE = None
            try:
                _INSTANCE = DVBEPGScheduler(session)
            except Exception as error:
                log_event("dvb_epg", "Automatic DVB EPG scheduler could not start", error)
        elif reason_value != 0:
            instance = _INSTANCE
            _INSTANCE = None
            if instance is not None:
                try:
                    instance.stop()
                except Exception as error:
                    log_event("dvb_epg", "Automatic DVB EPG scheduler could not stop", error)


def notify_settings_changed():
    """Tell the live scheduler that the DVB EPG settings were saved."""
    instance = _INSTANCE
    if instance is not None:
        instance.notify_settings_changed(startup=False)


def is_busy():
    """Return whether any automatic or manual DVB EPG update owns runtime."""
    instance = _INSTANCE
    return runtime_is_busy() or bool(instance is not None and instance.busy())


def scheduler_is_busy():
    """Return whether the automatic scheduler itself is updating."""
    instance = _INSTANCE
    return bool(instance is not None and instance.busy())

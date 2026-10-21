# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Durable episode completion, independent of expiring resume bookmarks."""

import hashlib
import json
import math
import os
import tempfile
import threading
import time
from collections import OrderedDict

from .diagnostics import log_event
from .persistence import WRITER, guarded_mutation
from .playback import resume_account_scope


DEFAULT_WATCHED_PATH = "/etc/enigma2/gtiptvplayerpro-episode-history.json"
_STORE_LOCK = threading.RLock()
_READ_CACHE_LOCK = threading.Lock()
_READ_CACHE = OrderedDict()
_QUEUE_LOCK = threading.Lock()


def _remember_records(path, signature, records):
    with _READ_CACHE_LOCK:
        _READ_CACHE[path] = (signature, dict(records))
        _READ_CACHE.move_to_end(path)
        while len(_READ_CACHE) > 4:
            _READ_CACHE.popitem(last=False)


def _valid_key(key):
    return (isinstance(key, str) and len(key) == 64
            and all(char in "0123456789abcdef" for char in key))


def _completion_time(value):
    if isinstance(value, bool):
        return 0
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return value if math.isfinite(value) and value > 0 else 0


def _item_record(value):
    # r32-r45 stored only True. Keep that completion without inventing a date.
    if value is True:
        return {"finished_at": 0}
    if isinstance(value, dict) and value.get("watched") is True:
        return {"finished_at": _completion_time(value.get("finished_at"))}
    return None


def _records_from_items(items):
    records = {}
    for key, value in items.items():
        if _valid_key(key):
            record = _item_record(value)
            if record is not None:
                records[key] = record["finished_at"]
    return records


def _history_signature(path):
    try:
        info = os.stat(path)
        return (info.st_ino, info.st_size, info.st_mtime_ns)
    except OSError:
        return None


def refresh_after_restore(paths=()):
    """Publish restored snapshots on the restore worker, including rollback."""
    with _STORE_LOCK:
        with _READ_CACHE_LOCK:
            known_paths = set(_READ_CACHE)
            _READ_CACHE.clear()
        known_paths.update(os.path.abspath(path) for path in paths)
        for path in known_paths:
            store = WatchedEpisodeStore(path)
            items, unused_mutable = store._read()
            store._publish(items)


def episode_watch_key(client, item):
    """Scope a stable episode identity to its account, series and season."""
    if (getattr(item, "content_type", "") != "series"
            or bool(getattr(item, "youtube_native", False))):
        return ""
    scope = resume_account_scope(client)
    stream_id = str(getattr(item, "stream_id", "") or "").strip()
    parent = str(getattr(item, "parent_id", "") or "").strip()
    episode = str(getattr(item, "episode", "") or "").strip()
    if not scope or not stream_id or not (parent or episode):
        return ""
    identity = [scope, stream_id, parent,
                str(getattr(item, "season", "") or "").strip(), episode]
    return hashlib.sha256("\x1f".join(identity).encode("utf-8", "replace")).hexdigest()


def episode_finished(position, length):
    """Use actual decoder position, including a verified jump to the end."""
    try:
        position, length = int(position), int(length)
    except (TypeError, ValueError, OverflowError):
        return False
    return (position > 0 and length > 0 and position >= length - 2
            and float(position) / length >= 0.95)


class WatchedEpisodeStore(object):
    """Atomic completion records; legacy ticks have an unknown date (zero)."""

    def __init__(self, path=DEFAULT_WATCHED_PATH):
        self.path = os.path.abspath(path)
        self._signature = None
        self._records = {}
        self._keys = frozenset()
        self._write_outcomes = OrderedDict()

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return {}, not os.path.lexists(self.path)
        except (OSError, TypeError, ValueError):
            return {}, False
        if (not isinstance(payload, dict) or payload.get("version") not in (1, 2)
                or not isinstance(payload.get("items"), dict)):
            return {}, False
        return payload["items"], True

    def _write_key(self, key):
        return ("episode-watched", self.path, key)

    def _publish(self, items):
        self._signature = _history_signature(self.path)
        self._records = _records_from_items(items)
        self._keys = frozenset(self._records)
        _remember_records(self.path, self._signature, self._records)

    def records(self, refresh=True):
        """Read on the loader; direction-key rendering uses the cached snapshot.

        Cache publication and pending snapshots share the short cache lock.
        Thus an older pending mark cannot resurrect a newer committed removal,
        and a pending removal hides a disk completion before its writer runs.
        """
        with _READ_CACHE_LOCK:
            cached = _READ_CACHE.get(self.path)
        if refresh:
            signature = _history_signature(self.path)
            if cached is None or signature != cached[0]:
                with _STORE_LOCK:
                    items, unused_mutable = self._read()
                    self._publish(items)
        with _READ_CACHE_LOCK:
            cached = _READ_CACHE.get(self.path)
            if cached is not None:
                self._signature, snapshot = cached
                self._records = dict(snapshot)
                self._keys = frozenset(snapshot)
            records = dict(self._records)
            pending = WRITER.snapshot(self._write_key("")[:-1])
        for key, value in pending.items():
            if not _valid_key(key):
                continue
            record = _item_record(value)
            if record is None:
                # False is the explicit asynchronous unmark tombstone.
                if value is False or (isinstance(value, dict)
                                      and value.get("watched") is False):
                    records.pop(key, None)
            elif key not in records or (isinstance(value, dict)
                                        and value.get("replace_finished_at")):
                records[key] = record["finished_at"]
        # Return fresh dictionaries so callers cannot mutate the shared cache.
        return {key: {"finished_at": value} for key, value in records.items()}

    def keys(self, refresh=True):
        return frozenset(self.records(refresh=refresh))

    @guarded_mutation
    def _set_watched(self, key, watched, finished_at=None, replace_finished_at=False):
        if not _valid_key(key):
            return False
        if watched:
            finished_at = _completion_time(time.time() if finished_at is None else finished_at)
            if not finished_at:
                return False
        with _STORE_LOCK:
            items, mutable = self._read()
            if not mutable:
                return False
            previous = _item_record(items.get(key))
            if watched:
                if previous is not None and not replace_finished_at:
                    self._publish(items)
                    return True
                items[key] = {"watched": True, "finished_at": finished_at}
            elif key in items:
                del items[key]
            else:
                self._publish(items)
                return True
            directory = os.path.dirname(self.path)
            temporary = None
            try:
                os.makedirs(directory, exist_ok=True)
                descriptor, temporary = tempfile.mkstemp(
                    prefix=".gtiptv-episode-history-", suffix=".tmp", dir=directory,
                )
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    json.dump({"version": 2, "items": items}, handle,
                              separators=(",", ":"), sort_keys=True, allow_nan=False)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, self.path)
                directory_fd = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
                try:
                    try:
                        os.fsync(directory_fd)
                    except OSError:
                        pass
                finally:
                    os.close(directory_fd)
                self._publish(items)
                return True
            except (OSError, TypeError, ValueError):
                return False
            finally:
                if temporary is not None:
                    try:
                        os.unlink(temporary)
                    except OSError:
                        pass

    def mark(self, key, finished_at=None):
        return self._set_watched(key, True, finished_at=finished_at)

    def unmark(self, key):
        return self._set_watched(key, False)

    def set_watched_async(self, key, watched=True, finished_at=None):
        if not _valid_key(key):
            return False
        watched = bool(watched)
        # Serialize queue decisions, without doing any disk I/O on the GUI.
        with _QUEUE_LOCK:
            replace_finished_at = False
            submitted_at = None
            if watched:
                submitted_at = _completion_time(time.time() if finished_at is None else finished_at)
                if not submitted_at:
                    return False
                present, pending = WRITER.peek(self._write_key(key))
                replace_finished_at = present and (pending is False or (
                    isinstance(pending, dict) and (pending.get("watched") is False
                                                  or pending.get("replace_finished_at") is True)))
                existing = self.records(refresh=False).get(key)
                if existing is not None:
                    captured_time = existing["finished_at"]
                else:
                    captured_time = submitted_at
                    if not captured_time:
                        return False
                value = {"watched": True, "finished_at": captured_time}
                if replace_finished_at:
                    value["replace_finished_at"] = True
            else:
                captured_time = None
                value = {"watched": False}

            outcome = {"watched": watched, "ok": None, "done": threading.Event()}
            outcome["operation"] = id(outcome)
            # This tag belongs only to WRITER's in-memory overlay, never JSON.
            value["_operation"] = outcome["operation"]
            self._write_outcomes[key] = outcome
            self._write_outcomes.move_to_end(key)
            for previous_key in tuple(self._write_outcomes):
                if len(self._write_outcomes) <= 256:
                    break
                previous = self._write_outcomes[previous_key]
                if previous_key != key and previous["done"].is_set():
                    del self._write_outcomes[previous_key]

            def commit():
                succeeded = False
                try:
                    for delay in (0.05, 0.15, None):
                        # Unknown legacy dates are preserved by _set_watched's
                        # existing-record path; use a real time only if the
                        # record disappeared through another completed action.
                        timestamp = (captured_time or submitted_at) if watched else None
                        if self._set_watched(key, watched, finished_at=timestamp,
                                             replace_finished_at=replace_finished_at):
                            succeeded = True
                            return True
                        if delay is not None:
                            threading.Event().wait(delay)
                    return False
                finally:
                    outcome["ok"] = succeeded

            try:
                with _READ_CACHE_LOCK:
                    accepted = WRITER.submit(self._write_key(key), commit,
                                             cleanup=outcome["done"].set,
                                             value=value, essential=True)
            except (RuntimeError, OSError) as error:
                log_event("episode_history", "queue-failed", error)
                accepted = False
            if not accepted:
                outcome["ok"] = False
                outcome["done"].set()
            return accepted

    def confirm_watched(self, key, watched=True, timeout=2.0):
        """Confirm the latest submission on this store, on a loader worker.

        True means the write succeeded and the committed file has the requested
        state. False means a terminal write/read failure or a disk mismatch.
        None means it is still pending or a newer submission superseded it.
        Only this episode is awaited; unrelated persistence work cannot delay
        acknowledgement. Call records(refresh=True) afterwards to reconcile UI.
        """
        if not _valid_key(key):
            return False
        try:
            timeout = float(timeout)
        except (TypeError, ValueError, OverflowError):
            timeout = 0
        if not math.isfinite(timeout):
            timeout = 0
        deadline = time.monotonic() + max(0, timeout)
        watched = bool(watched)
        with _QUEUE_LOCK:
            outcome = self._write_outcomes.get(key)
        if outcome is None:
            return False
        if outcome["watched"] != watched:
            return None
        if not outcome["done"].wait(max(0, deadline - time.monotonic())):
            return None
        while True:
            with _QUEUE_LOCK:
                if self._write_outcomes.get(key) is not outcome:
                    return None
            present, pending = WRITER.peek(self._write_key(key))
            if not present:
                break
            if (not isinstance(pending, dict)
                    or pending.get("_operation") != outcome["operation"]):
                return None
            # WRITER invokes cleanup immediately before removing its overlay.
            # Wait for that removal before reading the durable state.
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            threading.Event().wait(min(0.005, remaining))
        if outcome["ok"] is None:
            return None
        with _STORE_LOCK:
            items, mutable = self._read()
            self._publish(items)
        with _QUEUE_LOCK:
            if self._write_outcomes.get(key) is not outcome:
                return None
            if WRITER.peek(self._write_key(key))[0]:
                return None
        if not outcome["ok"] or not mutable:
            return False
        return (_item_record(items.get(key)) is not None) == watched

    def mark_async(self, key, finished_at=None):
        return self.set_watched_async(key, True, finished_at=finished_at)

    def unmark_async(self, key):
        return self.set_watched_async(key, False)


class EpisodeCompletionWatcher(object):
    """Observe the owned service; navigation events never write to flash."""

    def __init__(self, player, store=None, eof_event=None, clock=None):
        self.player = player
        self.store = store if store is not None else WatchedEpisodeStore()
        self.eof_event = eof_event
        self.clock = clock or time.monotonic
        self.closed = False
        self.events = getattr(getattr(player.session, "nav", None), "event", None)
        if self.events is not None and hasattr(self.events, "append"):
            self.events.append(self._event)
        self.arm()

    def arm(self):
        self.key = episode_watch_key(self.player.client, self.player.current_item)
        self.reference = self.player.reference
        self.generation = self.player._generation
        self.started_at = self.clock()
        self.position = -1
        self.length = 0
        self.stable_lengths = 0
        self.advancing_samples = 0
        self.eof_seek_finished = False
        self.eof = self.recorded = False

    def _owns_service(self):
        player = self.player
        if (self.closed or player is None or not self.key
                or getattr(player, "_closed", False) or not getattr(player, "_started", False)
                or self.generation != player._generation
                or self.reference is not player.reference
                or getattr(player, "_web_suspended", None) is not None
                or getattr(player, "_pending_engine_reference", None) is not None
                or getattr(player, "_subtitle_play_transition", False)):
            return False
        try:
            current = player.session.nav.getCurrentlyPlayingServiceReference()
            if current is self.reference:
                return True
            return bool(current is not None and current.toString() == self.reference.toString())
        except (AttributeError, RuntimeError, OSError, TypeError, ValueError):
            return False

    def _observe(self, position, length):
        try:
            position, length = int(position), int(length)
        except (TypeError, ValueError, OverflowError):
            return
        if length > 0:
            self.stable_lengths = self.stable_lengths + 1 if abs(length - self.length) <= 2 else 1
            self.length = length
        # A few engines reset their clock to zero while emitting EOF. Keep
        # the last real end position until the deferred completion sample.
        if position >= 0 and not (self.eof and position <= 1 < self.position):
            if self.position >= 0 and position > self.position:
                self.advancing_samples += 1
            self.position = position

    def _position(self):
        try:
            return self.player._seek_position(self.player._raw_seek_interface())
        except (AttributeError, RuntimeError, OSError, TypeError, ValueError):
            return -1, -1

    def _event(self, event):
        if (self.eof_event is None or event != self.eof_event or self.recorded
                or not self._owns_service() or self.clock() - self.started_at < 0.75):
            return
        self.eof = True
        self._observe(*self._position())
        # A jump straight to EOF may reset the clock before the next poll.
        # The native EOF plus the accepted, still-pending end seek confirms
        # completion; an unexecuted slider target by itself never does.
        pending = getattr(self.player, "_pending_seek_verification", None)
        if (isinstance(pending, dict)
                and pending.get("stream_id") == str(self.player.current_item.stream_id)):
            self.eof_seek_finished = episode_finished(pending.get("target"), self.length)

    def sample(self, position, length):
        if self.recorded or not self._owns_service():
            return False
        self._observe(position, length)
        if (self.clock() - self.started_at < 0.75
                or getattr(self.player, "_resume_start_position", 0) > 0):
            return False
        finished = episode_finished(self.position, self.length)
        # A known duration must be consistent before a position alone can
        # finish an episode. Unknown-duration EOF needs observed playback,
        # preventing a failed open from becoming an "already watched" tick.
        if not ((finished and (self.stable_lengths >= 2 or self.eof))
                or (self.eof and self.eof_seek_finished)
                or (self.eof and self.length <= 0 and self.position >= 5
                    and self.advancing_samples > 0)):
            return False
        self.recorded = bool(self.store.mark_async(self.key))
        return self.recorded

    def sample_current(self):
        return self.sample(*self._position())

    def close(self):
        if self.closed:
            return
        self.closed = True
        if self.events is not None:
            try:
                self.events.remove(self._event)
            except (AttributeError, ValueError):
                pass
        self.events = None
        self.player = None

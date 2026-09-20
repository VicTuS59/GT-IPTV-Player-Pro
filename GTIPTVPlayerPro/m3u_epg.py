# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded external XMLTV support for M3U live channels.

Refresh work in this module is synchronous and belongs on a worker thread.
The television-facing lookup path is deliberately different: once a snapshot
has been published, :class:`M3UEPGStore` only reads immutable in-memory data.
It never opens a file, resolves a host, or parses XML while changing channels.

Channel association is intentionally conservative.  A playlist ``tvg-id`` is
matched to an XMLTV channel ID first by an exact comparison and then, only if
there is a single candidate, by an NFKC/casefold comparison.  Display names
and fuzzy matching are not used.
"""

import gzip
import hashlib
import heapq
import http.client
import json
import math
import os
import socket
import ssl
import stat
import tempfile
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict, defaultdict
from urllib.parse import urljoin

from . import PLUGIN_VERSION
from . import dvb_epg
from . import m3u
from .content import ContentError, EPGEvent


M3U_EPG_CACHE_DIRECTORY = "/etc/enigma2/gtiptvplayer/m3u-epg-cache"
M3U_EPG_SNAPSHOT_VERSION = 1

MAX_COMPRESSED_XMLTV_BYTES = 32 * 1024 * 1024
MAX_UNCOMPRESSED_XMLTV_BYTES = 128 * 1024 * 1024
# Descriptive compatibility aliases used by bounded-source tests and callers.
MAX_XMLTV_DOWNLOAD_BYTES = MAX_COMPRESSED_XMLTV_BYTES
MAX_XMLTV_BYTES = MAX_UNCOMPRESSED_XMLTV_BYTES
MAX_XMLTV_CHANNELS = 50000
MAX_XMLTV_PROGRAMMES = 500000
MAX_WANTED_TVG_IDS = 20000
MAX_EVENTS_PER_CHANNEL = 2048
MAX_SNAPSHOT_EVENTS = 100000
MAX_SNAPSHOT_TEXT_CHARACTERS = 24 * 1024 * 1024
MAX_SNAPSHOT_BYTES = 16 * 1024 * 1024
TARGET_COMPACTED_SNAPSHOT_BYTES = 15 * 1024 * 1024
COMPACTED_DESCRIPTION_LENGTH = 512
MAX_SNAPSHOT_CACHE_BYTES = 64 * 1024 * 1024
MAX_CACHE_BYTES = MAX_SNAPSHOT_BYTES
MAX_CACHE_TOTAL_BYTES = MAX_SNAPSHOT_CACHE_BYTES
MAX_SNAPSHOT_FILES = 500
MAX_CACHE_DIRECTORY_ENTRIES = 2048
MAX_TITLE_LENGTH = 512
MAX_DESCRIPTION_LENGTH = 2000
MAX_TVG_ID_LENGTH = 256
MAX_REDIRECTS = 3
MAX_TIMEOUT = 180.0
DEFAULT_TIMEOUT = 45.0
DEFAULT_PAST_SECONDS = 12 * 60 * 60
DEFAULT_FUTURE_SECONDS = 8 * 24 * 60 * 60
READ_CHUNK_BYTES = 64 * 1024

LOCAL_XMLTV_ROOTS = (
    "/etc/enigma2/gtiptvplayer",
    "/media",
    "/mnt",
)

_SOURCE_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
_CACHE_LOCK = threading.RLock()
_DEFAULT_STORE = None
_DEFAULT_STORE_LOCK = threading.Lock()


class M3UEPGError(RuntimeError):
    """Base failure raised by the external M3U XMLTV core."""


class M3UEPGSourceError(M3UEPGError):
    """The configured local or remote XMLTV source could not be read."""


class M3UEPGSnapshotError(M3UEPGError):
    """A snapshot is invalid or cannot be stored within its fixed budget."""


class M3UEPGCancelled(M3UEPGError):
    """A bounded worker operation was cancelled or reached its deadline."""


def get_default_store():
    """Return the process-wide RAM snapshot store."""
    global _DEFAULT_STORE
    if _DEFAULT_STORE is None:
        with _DEFAULT_STORE_LOCK:
            if _DEFAULT_STORE is None:
                _DEFAULT_STORE = M3UEPGStore()
    return _DEFAULT_STORE


def snapshot_path(source_id, cache_dir=M3U_EPG_CACHE_DIRECTORY):
    """Return the private per-source snapshot path."""
    source_id = _validated_source_id(source_id)
    cache_dir = _validated_absolute_directory(cache_dir)
    return os.path.join(cache_dir, "{}.snapshot".format(source_id))


def snapshot_paths(source_id, cache_dir=M3U_EPG_CACHE_DIRECTORY):
    """Return paths owned by one source.

    A tuple keeps deletion call sites future-proof without exposing temporary
    implementation files.  R57 currently owns one durable file per source.
    """
    return (snapshot_path(source_id, cache_dir),)


def delete_snapshot(source_id, cache_dir=M3U_EPG_CACHE_DIRECTORY, store=None):
    """Delete one durable snapshot and forget its in-memory copy."""
    source_id = _validated_source_id(source_id)
    cache_dir = _validated_absolute_directory(cache_dir)
    name = "{}.snapshot".format(source_id)
    removed = False
    with _CACHE_LOCK:
        directory_descriptor = None
        try:
            directory_descriptor = _open_private_cache_directory(
                cache_dir,
                create=False,
            )
            try:
                details = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                details = None
            if details is not None:
                if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(
                    details.st_mode
                ):
                    raise M3UEPGSnapshotError(
                        "unsafe M3U EPG snapshot target"
                    )
                os.unlink(name, dir_fd=directory_descriptor)
                try:
                    os.fsync(directory_descriptor)
                except OSError:
                    pass
                removed = True
        except FileNotFoundError:
            pass
        finally:
            if directory_descriptor is not None:
                os.close(directory_descriptor)
            (store or get_default_store()).forget(source_id)
    return removed


class M3UEPGStore(object):
    """Thread-safe collection of immutable, RAM-only runtime indexes."""

    def __init__(self, cache_dir=M3U_EPG_CACHE_DIRECTORY):
        self.cache_dir = _validated_absolute_directory(cache_dir)
        self._lock = threading.RLock()
        self._snapshots = {}
        self._generations = {}

    def publish(
        self,
        source_id,
        snapshot=None,
        binding=None,
        persist=True,
        cache_dir=None,
        _expected_generation=None,
        _cancel_event=None,
        _deadline=None,
    ):
        """Validate and atomically publish one completed snapshot.

        ``source_id`` may also be the snapshot dictionary itself.  The
        dictionary form is useful to workers which build a complete snapshot
        before entering this store.  Durable installation is completed before
        the RAM index is swapped, so a write failure preserves both last-good
        copies.
        """
        if snapshot is None and isinstance(source_id, dict):
            payload = dict(source_id)
            source_id = payload.get("source_id", "")
        else:
            payload = dict(snapshot or {})
            payload.setdefault("source_id", source_id)
        source_id = _validated_source_id(source_id)
        if payload.get("source_id") != source_id:
            raise M3UEPGSnapshotError("snapshot source ID does not match")
        validated = _validate_snapshot(
            payload,
            cancel_event=_cancel_event,
            deadline=_deadline,
        )
        if binding is not None:
            expected = binding_fingerprint(binding, source_id)
            if validated["binding_fingerprint"] != expected:
                raise M3UEPGSnapshotError("snapshot binding fingerprint changed")
        serialized = _serialize_snapshot(validated)
        if _deadline is not None:
            _check_limits(_cancel_event, _deadline)
        runtime = _runtime_snapshot(
            validated,
            cancel_event=_cancel_event,
            deadline=_deadline,
        )
        with _CACHE_LOCK:
            with self._lock:
                generation = int(self._generations.get(source_id, 0) or 0)
            if (
                _expected_generation is not None
                and int(_expected_generation) != generation
            ):
                raise M3UEPGCancelled("external XMLTV publication was superseded")
            if persist:
                target_dir = self.cache_dir if cache_dir is None else cache_dir
                _install_snapshot(
                    source_id,
                    serialized,
                    target_dir,
                    cancel_event=_cancel_event,
                    deadline=_deadline,
                )
            with self._lock:
                if (
                    _expected_generation is not None
                    and int(self._generations.get(source_id, 0) or 0)
                    != generation
                ):
                    raise M3UEPGCancelled(
                        "external XMLTV publication was superseded"
                    )
                self._snapshots[source_id] = runtime
        return _snapshot_summary(validated, len(serialized))

    def load_from_disk(self, source_id, binding=None, cache_dir=None):
        """Preload one snapshot on a scheduler/startup worker.

        Missing, malformed, oversized, or stale-binding files are reported as
        ``False``.  The method never removes the last-good file.  A binding
        mismatch does forget a previously loaded RAM entry so it cannot be
        displayed for a newly configured source that reused the same ID.
        """
        source_id = _validated_source_id(source_id)
        target_dir = self.cache_dir if cache_dir is None else cache_dir
        target_dir = _validated_absolute_directory(target_dir)
        name = "{}.snapshot".format(source_id)
        expected = None
        if binding is not None:
            expected = binding_fingerprint(binding, source_id)
        with _CACHE_LOCK:
            directory_descriptor = None
            try:
                directory_descriptor = _open_private_cache_directory(
                    target_dir,
                    create=False,
                )
                body = _read_snapshot_file(
                    name,
                    directory_descriptor=directory_descriptor,
                )
                payload = json.loads(body.decode("utf-8"))
                validated = _validate_snapshot(payload)
                if validated["source_id"] != source_id:
                    raise M3UEPGSnapshotError("snapshot source ID does not match")
                if (
                    expected is not None
                    and validated["binding_fingerprint"] != expected
                ):
                    self.forget(source_id)
                    return False
                runtime = _runtime_snapshot(validated)
            except (
                OSError,
                UnicodeError,
                ValueError,
                TypeError,
                M3UEPGSnapshotError,
            ):
                if expected is not None:
                    self.forget(source_id)
                return False
            finally:
                if directory_descriptor is not None:
                    os.close(directory_descriptor)
            with self._lock:
                self._snapshots[source_id] = runtime
            return True

    def lookup(self, source_id, tvg_id, now=None):
        """Return ``(current, next)`` :class:`EPGEvent` objects from RAM only."""
        try:
            source_id = _validated_source_id(source_id)
            tvg_id = _validated_tvg_id(tvg_id)
            moment = float(time.time() if now is None else now)
        except (TypeError, ValueError, OverflowError):
            return (None, None)
        if not math.isfinite(moment):
            return (None, None)
        with self._lock:
            runtime = self._snapshots.get(source_id)
        if runtime is None:
            return (None, None)
        xmltv_id = runtime["exact"].get(tvg_id)
        if xmltv_id is None:
            xmltv_id = runtime["normalised"].get(normalise_tvg_id(tvg_id))
        if xmltv_id is None:
            return (None, None)
        events = runtime["events"].get(xmltv_id, ())
        current_index = None
        upcoming_index = None
        for index, event in enumerate(events):
            start, stop = event[0], event[1]
            if start <= moment < stop:
                current_index = index
                break
            if start >= moment and upcoming_index is None:
                upcoming_index = index
                break
        if current_index is not None:
            current = _content_event(events[current_index], True)
            following = None
            for index in range(current_index + 1, len(events)):
                event = events[index]
                if event[0] >= moment:
                    following = _content_event(event, False)
                    break
            return current, following
        if upcoming_index is not None:
            return None, _content_event(events[upcoming_index], False)
        return (None, None)

    def forget(self, source_id):
        """Forget one RAM snapshot without touching its durable last-good file."""
        try:
            source_id = _validated_source_id(source_id)
        except (TypeError, ValueError):
            return False
        with _CACHE_LOCK:
            with self._lock:
                removed = self._snapshots.pop(source_id, None) is not None
                self._generations[source_id] = (
                    int(self._generations.get(source_id, 0) or 0) + 1
                )
                return removed

    def _forget_mismatched_binding(self, source_id, fingerprint):
        """Prevent a reused source ID from exposing another binding's EPG."""
        self._prepare_publication(source_id, fingerprint)

    def _prepare_publication(self, source_id, fingerprint):
        """Forget a mismatched binding and return one atomic generation token."""
        with _CACHE_LOCK:
            with self._lock:
                current = self._snapshots.get(source_id)
                if (
                    current is not None
                    and current.get("binding_fingerprint") != fingerprint
                ):
                    self._snapshots.pop(source_id, None)
                    self._generations[source_id] = (
                        int(self._generations.get(source_id, 0) or 0) + 1
                    )
                return int(self._generations.get(source_id, 0) or 0)

    def _publication_generation(self, source_id):
        with _CACHE_LOCK:
            with self._lock:
                return int(self._generations.get(source_id, 0) or 0)


# A concise name for callers which do not need to distinguish source types.
EPGStore = M3UEPGStore


def refresh_source(
    source_id,
    binding,
    wanted_ids,
    store=None,
    cache_dir=None,
    now=None,
    timeout=None,
    cancel_event=None,
    deadline=None,
    resolver=socket.getaddrinfo,
    local_roots=None,
    stage_dir=None,
    diagnostics=None,
    archive_days=None,
    persist=True,
):
    """Read, match, and publish one external XMLTV source.

    ``binding`` may be a dictionary or an object exposing ``source_id``,
    ``kind``, ``location`` and ``fingerprint``.  The first field is optional;
    when present it must agree with the explicit ``source_id``.  No settings
    class is imported here, keeping the core independent from Enigma2 config.

    Every failure occurs before :meth:`M3UEPGStore.publish`, leaving an older
    snapshot byte-for-byte untouched and available as stale last-good data.
    """
    timing = _reset_refresh_diagnostics(diagnostics)
    source_id = _validated_source_id(source_id)
    kind, location = _binding_location(binding, source_id)
    fingerprint = binding_fingerprint(binding, source_id)
    wanted = _normalise_wanted_ids(wanted_ids)
    if archive_days is not None:
        if (isinstance(archive_days, bool) or not isinstance(archive_days, int)
                or not 1 <= archive_days <= 31 or len(wanted) != 1
                or persist or store is None):
            raise ValueError("archive XMLTV reads require one channel and an isolated nonpersistent store")
    selected_store = store or get_default_store()
    prepare_publication = getattr(selected_store, "_prepare_publication", None)
    generation_getter = getattr(selected_store, "_publication_generation", None)
    if callable(prepare_publication):
        publication_generation = prepare_publication(source_id, fingerprint)
    else:
        forget_mismatch = getattr(
            selected_store,
            "_forget_mismatched_binding",
            None,
        )
        if callable(forget_mismatch):
            forget_mismatch(source_id, fingerprint)
        publication_generation = (
            generation_getter(source_id) if callable(generation_getter) else None
        )
    timeout, deadline = _operation_limits(timeout, deadline)
    try:
        moment = float(time.time() if now is None else now)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("invalid XMLTV refresh time") from error
    if not math.isfinite(moment):
        raise ValueError("invalid XMLTV refresh time")
    _check_limits(cancel_event, deadline)

    workspace = tempfile.mkdtemp(prefix="gtiptvplayerpro-m3u-epg-", dir=stage_dir)
    staged_paths = []
    total_started = time.monotonic()
    try:
        raw_path = os.path.join(workspace, "source-{}.raw".format(uuid.uuid4().hex))
        staged_paths.append(raw_path)
        transfer_started = time.monotonic()
        if kind == "file":
            if timing is not None:
                timing["stage"] = "copy"
                timing["transfer_kind"] = "copy"
            try:
                compressed, input_bytes = _copy_local_source(
                    location,
                    raw_path,
                    cancel_event,
                    deadline,
                    LOCAL_XMLTV_ROOTS if local_roots is None else local_roots,
                )
            finally:
                _record_refresh_elapsed(timing, "copy_ms", transfer_started)
            redirect_count = 0
        else:
            if timing is not None:
                timing["stage"] = "download"
                timing["transfer_kind"] = "download"
            try:
                compressed, input_bytes, redirect_count = _download_remote_source(
                    location,
                    raw_path,
                    timeout,
                    cancel_event,
                    deadline,
                    resolver,
                )
            finally:
                _record_refresh_elapsed(timing, "download_ms", transfer_started)
        if timing is not None:
            timing["input_bytes"] = int(input_bytes)
            timing["redirect_count"] = int(redirect_count)
        if compressed:
            xml_path = os.path.join(
                workspace,
                "source-{}.xml".format(uuid.uuid4().hex),
            )
            staged_paths.append(xml_path)
            if timing is not None:
                timing["stage"] = "gzip"
            gzip_started = time.monotonic()
            try:
                xml_bytes = _expand_gzip(
                    raw_path,
                    xml_path,
                    cancel_event,
                    deadline,
                )
            finally:
                _record_refresh_elapsed(timing, "gzip_ms", gzip_started)
        else:
            xml_path = raw_path
            xml_bytes = input_bytes
        if timing is not None:
            timing["xml_bytes"] = int(xml_bytes)
            timing["stage"] = "xml-scan"
        scan_started = time.monotonic()
        try:
            payload = _build_snapshot(
                source_id,
                fingerprint,
                xml_path,
                wanted,
                moment,
                cancel_event,
                deadline,
                **({"archive_days": archive_days} if archive_days is not None else {}),
            )
        finally:
            _record_refresh_elapsed(timing, "xml_scan_ms", scan_started)
        payload["input_bytes"] = int(input_bytes)
        payload["xml_bytes"] = int(xml_bytes)
        payload["redirect_count"] = int(redirect_count)
        compact_started = time.monotonic()
        try:
            fit_budget = (_fit_archive_snapshot_budget if archive_days is not None
                          else _fit_snapshot_budget)
            payload = fit_budget(
                payload,
                moment,
                cancel_event,
                deadline,
                diagnostics=timing,
            )
        finally:
            _record_refresh_elapsed(
                timing,
                "snapshot_compact_ms",
                compact_started,
            )
        _check_limits(cancel_event, deadline)
        publish_options = {
            "persist": bool(persist),
            "cache_dir": cache_dir,
        }
        if callable(generation_getter):
            publish_options.update(
                {
                    "_expected_generation": publication_generation,
                    "_cancel_event": cancel_event,
                    "_deadline": deadline,
                }
            )
        if timing is not None:
            timing["stage"] = "cache-write"
        cache_started = time.monotonic()
        try:
            summary = selected_store.publish(payload, **publish_options)
        finally:
            _record_refresh_elapsed(timing, "cache_write_ms", cache_started)
        if timing is not None:
            timing["snapshot_final_bytes"] = int(summary.get("size_bytes", 0) or 0)
        summary.update(
            {
                "input_bytes": int(input_bytes),
                "xml_bytes": int(xml_bytes),
                "redirect_count": int(redirect_count),
                "stale": False,
            }
        )
        if timing is not None:
            timing["stage"] = "complete"
        return summary
    except M3UEPGError:
        raise
    except dvb_epg.OperationCancelled as error:
        raise M3UEPGCancelled(str(error)) from error
    except (dvb_epg.XmltvError, OSError, EOFError) as error:
        raise M3UEPGSourceError("external XMLTV guide is invalid") from error
    finally:
        for path in reversed(staged_paths):
            try:
                if os.path.lexists(path):
                    details = os.lstat(path)
                    if stat.S_ISREG(details.st_mode) and not stat.S_ISLNK(details.st_mode):
                        os.unlink(path)
            except OSError:
                pass
        try:
            os.rmdir(workspace)
        except OSError:
            pass
        _record_refresh_elapsed(timing, "total_ms", total_started)


def read_archive_events(source_id, binding, tvg_id, days, **options):
    """Read one channel's past guide on demand without replacing live EPG.

    Transfer, gzip, XML limits and cancellation are shared with ordinary EPG.
    Only the selected channel's programme text is retained.  No archive index
    is persisted and the process-wide current/next store is never involved.
    """
    cancel_event = options.get("cancel_event")
    unused_timeout, deadline = _operation_limits(options.get("timeout"), options.get("deadline"))
    options["deadline"] = deadline

    class ArchiveResult(object):
        def __init__(self):
            self.rows = ()

        def publish(self, payload, **unused):
            aliases = dict(payload["aliases"])
            matched_id = aliases.get(tvg_id)
            selected = []
            for channel_id, rows in payload["channels"]:
                _check_limits(cancel_event, deadline)
                if channel_id != matched_id:
                    continue
                for index, row in enumerate(rows):
                    if index % 64 == 0:
                        _check_limits(cancel_event, deadline)
                    selected.append(tuple(row))
            self.rows = tuple(selected)
            return _snapshot_summary(payload, _snapshot_encoded_size(payload, cancel_event, deadline))

    result = ArchiveResult()
    refresh_source(source_id, binding, (tvg_id,), store=result,
                   archive_days=days, persist=False, **options)
    return result.rows


def binding_fingerprint(binding, source_id=None):
    """Return the non-secret SHA-256 identity stored with a snapshot."""
    if source_id is None:
        source_id = _binding_value(binding, "source_id", "id")
    source_id = _validated_source_id(source_id)
    kind, location = _binding_location(binding, source_id)
    explicit = _binding_value(binding, "fingerprint")
    if callable(explicit):
        explicit = explicit()
    try:
        explicit_body = json.dumps(
            explicit,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("invalid XMLTV binding fingerprint") from error
    material = json.dumps(
        [source_id, kind, location, explicit_body],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def normalise_tvg_id(value):
    """Return the sole fallback key used for non-exact ``tvg-id`` matching."""
    value = unicodedata.normalize("NFKC", str(value or "")).casefold()
    return value.strip()


def _binding_value(binding, *names):
    if isinstance(binding, dict):
        for name in names:
            if name in binding:
                return binding.get(name)
        return None
    for name in names:
        if hasattr(binding, name):
            return getattr(binding, name)
    return None


def _binding_location(binding, source_id):
    bound_id = _binding_value(binding, "source_id", "id")
    if bound_id not in (None, "") and _validated_source_id(bound_id) != source_id:
        raise ValueError("XMLTV binding source ID does not match")
    kind = str(_binding_value(binding, "kind", "source_kind", "type") or "").strip().lower()
    if kind in ("file", "local", "nas"):
        kind = "file"
    elif kind in ("url", "web", "remote", "http", "https"):
        kind = "url"
    else:
        raise ValueError("invalid XMLTV source kind")
    location = str(_binding_value(binding, "location", "path", "url") or "").strip()
    if not location or len(location) > 4096 or any(
        ord(character) < 32 or ord(character) == 127 for character in location
    ):
        raise ValueError("invalid XMLTV source location")
    if kind == "file":
        if not os.path.isabs(location) or len(location) > 1024:
            raise ValueError("invalid XMLTV file path")
    else:
        try:
            location = m3u._remote_url_info(location)["url"]
        except ValueError as error:
            raise ValueError("invalid XMLTV URL") from error
    return kind, location


def _validated_source_id(value):
    value = str(value or "").strip()
    if (
        not value
        or len(value) > 64
        or value in (".", "..")
        or any(character not in _SOURCE_ID_CHARACTERS for character in value)
    ):
        raise ValueError("invalid M3U EPG source ID")
    return value


def _validated_tvg_id(value):
    value = str(value or "").strip()
    if (
        not value
        or len(value) > MAX_TVG_ID_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("invalid tvg-id")
    return value


def _validated_absolute_directory(path):
    if isinstance(path, os.PathLike):
        path = os.fspath(path)
    path = str(path or "")
    if not path or "\x00" in path or not os.path.isabs(path):
        raise ValueError("invalid M3U EPG cache directory")
    return os.path.abspath(path)


def _normalise_wanted_ids(values):
    if isinstance(values, dict):
        candidates = iter(values)
    elif isinstance(values, (str, bytes)):
        candidates = iter((values,))
    else:
        try:
            candidates = iter(values or ())
        except TypeError as error:
            raise ValueError("playlist tvg-id values are invalid") from error
    output = []
    seen = set()
    for examined, candidate in enumerate(candidates, 1):
        if examined > MAX_WANTED_TVG_IDS:
            raise ValueError("too many playlist tvg-id values")
        if isinstance(candidate, dict):
            candidate = candidate.get("tvg_id", candidate.get("id", ""))
        elif isinstance(candidate, (tuple, list)):
            candidate = candidate[0] if candidate else ""
        try:
            value = _validated_tvg_id(candidate)
        except ValueError:
            continue
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return tuple(output)


def _operation_limits(timeout, deadline):
    started = time.monotonic()
    if deadline is not None:
        try:
            supplied = float(deadline)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("invalid XMLTV refresh deadline") from error
        if not math.isfinite(supplied):
            raise ValueError("invalid XMLTV refresh deadline")
        effective = min(supplied, started + MAX_TIMEOUT)
        if timeout is None:
            return max(0.0, effective - started), effective
    elif timeout is None:
        timeout = DEFAULT_TIMEOUT

    try:
        timeout = float(timeout)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("invalid XMLTV refresh timeout") from error
    if not math.isfinite(timeout) or timeout <= 0 or timeout > MAX_TIMEOUT:
        raise ValueError("invalid XMLTV refresh timeout")
    automatic = started + timeout
    if deadline is None:
        return timeout, automatic
    return timeout, min(automatic, supplied)


def _check_limits(cancel_event, deadline):
    if cancel_event is not None:
        try:
            if cancel_event.is_set():
                raise M3UEPGCancelled("external XMLTV refresh was cancelled")
        except AttributeError as error:
            raise ValueError("invalid cancellation event") from error
    if time.monotonic() >= float(deadline):
        raise M3UEPGCancelled("external XMLTV refresh reached its deadline")


def _remaining_timeout(deadline, timeout):
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise M3UEPGCancelled("external XMLTV refresh reached its deadline")
    return min(float(timeout), remaining)


def _reset_refresh_diagnostics(diagnostics):
    if diagnostics is None:
        return None
    if not isinstance(diagnostics, dict):
        raise ValueError("invalid XMLTV diagnostics target")
    diagnostics.clear()
    diagnostics.update(
        {
            "stage": "prepare",
            "transfer_kind": "none",
            "copy_ms": 0,
            "download_ms": 0,
            "gzip_ms": 0,
            "xml_scan_ms": 0,
            "snapshot_compact_ms": 0,
            "cache_write_ms": 0,
            "total_ms": 0,
            "input_bytes": 0,
            "xml_bytes": 0,
            "redirect_count": 0,
            "snapshot_compacted": 0,
            "snapshot_original_bytes": 0,
            "snapshot_final_bytes": 0,
            "snapshot_original_events": 0,
            "snapshot_retained_events": 0,
            "snapshot_dropped_events": 0,
            "snapshot_trimmed_descriptions": 0,
        }
    )
    return diagnostics


def _record_refresh_elapsed(diagnostics, name, started):
    if diagnostics is None:
        return
    try:
        elapsed = max(0.0, time.monotonic() - float(started))
        milliseconds = min(0x7FFFFFFF, int(round(elapsed * 1000.0)))
    except (TypeError, ValueError, OverflowError):
        milliseconds = 0
    diagnostics[str(name)] = milliseconds


def _open_local_source(path, roots):
    try:
        path = m3u._validate_local_path(path, m3u._local_root_tuple(roots))
    except ContentError as error:
        raise M3UEPGSourceError("external XMLTV file is not allowed") from error
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
        listed = os.lstat(path)
        details = os.fstat(descriptor)
        if (
            stat.S_ISLNK(listed.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_dev != listed.st_dev
            or details.st_ino != listed.st_ino
            or details.st_size > MAX_UNCOMPRESSED_XMLTV_BYTES
        ):
            raise OSError("unsafe external XMLTV file")
        return descriptor, details
    except Exception:
        try:
            os.close(descriptor)
        except (OSError, UnboundLocalError):
            pass
        raise


def _file_identity(details):
    return (
        int(details.st_dev),
        int(details.st_ino),
        int(details.st_size),
        int(getattr(details, "st_mtime_ns", int(details.st_mtime * 1000000000))),
    )


def _create_stage_file(path, directory_descriptor=None):
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    if directory_descriptor is None:
        return os.open(path, flags, 0o600)
    return os.open(
        path,
        flags,
        0o600,
        dir_fd=directory_descriptor,
    )


def _write_all(descriptor, body):
    offset = 0
    while offset < len(body):
        written = os.write(descriptor, body[offset:])
        if written <= 0:
            raise OSError("short XMLTV stage write")
        offset += written


def _copy_local_source(path, destination, cancel_event, deadline, roots):
    descriptor, before = _open_local_source(path, roots)
    output = None
    total = 0
    prefix = b""
    compressed = False
    try:
        prefix = os.read(descriptor, 2)
        os.lseek(descriptor, 0, os.SEEK_SET)
        compressed = prefix == b"\x1f\x8b"
        maximum = (
            MAX_COMPRESSED_XMLTV_BYTES
            if compressed
            else MAX_UNCOMPRESSED_XMLTV_BYTES
        )
        if int(before.st_size) > maximum:
            raise M3UEPGSourceError("external XMLTV file exceeds its size limit")
        output = _create_stage_file(destination)
        while True:
            _check_limits(cancel_event, deadline)
            chunk = os.read(descriptor, min(READ_CHUNK_BYTES, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise M3UEPGSourceError("external XMLTV file exceeds its size limit")
            _write_all(output, chunk)
        after = os.fstat(descriptor)
        if _file_identity(before) != _file_identity(after):
            raise M3UEPGSourceError("external XMLTV file changed while being read")
        if total <= 0:
            raise M3UEPGSourceError("external XMLTV file is empty")
        os.fsync(output)
        os.fchmod(output, 0o600)
    finally:
        os.close(descriptor)
        if output is not None:
            os.close(output)
    return compressed, total


def _download_remote_source(
    url,
    destination,
    timeout,
    cancel_event,
    deadline,
    resolver,
):
    try:
        current = m3u._remote_url_info(url)["url"]
    except ValueError as error:
        raise M3UEPGSourceError("external XMLTV URL is invalid") from error
    previous_scheme = ""
    original_scope = None
    for redirect_count in range(MAX_REDIRECTS + 1):
        _check_limits(cancel_event, deadline)
        try:
            info = m3u._remote_url_info(current)
        except ValueError as error:
            raise M3UEPGSourceError("external XMLTV redirect URL is invalid") from error
        if previous_scheme == "https" and info["scheme"] != "https":
            raise M3UEPGSourceError("HTTPS XMLTV cannot redirect to HTTP")
        try:
            addresses, scope = m3u._resolve_remote(
                info,
                resolver,
                original_scope,
                deadline,
                lambda unused: _check_limits(cancel_event, deadline),
            )
        except ContentError as error:
            raise M3UEPGSourceError("external XMLTV host is not allowed") from error
        if original_scope is None:
            original_scope = scope
        last_error = None
        redirect_location = ""
        for address in addresses:
            connection = None
            response = None
            try:
                remaining = _remaining_timeout(deadline, timeout)
                if info["scheme"] == "https":
                    connection = m3u._PinnedHTTPSConnection(
                        info["hostname"],
                        address,
                        info["port"],
                        remaining,
                    )
                else:
                    connection = http.client.HTTPConnection(
                        address,
                        port=info["port"],
                        timeout=remaining,
                    )
                connection.request(
                    "GET",
                    info["path"],
                    headers={
                        "Accept": "application/xml,text/xml,application/gzip,*/*;q=0.1",
                        "Accept-Encoding": "gzip,identity",
                        "Connection": "close",
                        "Host": m3u._host_header(info),
                        "User-Agent": "GT-IPTV-Player-Pro/{}/M3U-EPG".format(
                            PLUGIN_VERSION
                        ),
                    },
                )
                response = connection.getresponse()
                status_code = int(response.status)
                headers = m3u._safe_response_headers(response)
                if status_code in _REDIRECT_STATUSES:
                    redirect_location = headers.get("location", "")
                    result = None
                elif status_code != 200:
                    raise M3UEPGSourceError(
                        "external XMLTV server returned HTTP {}".format(status_code)
                    )
                else:
                    encoding = headers.get("content-encoding", "").lower()
                    if encoding not in ("", "identity", "gzip", "x-gzip"):
                        raise M3UEPGSourceError(
                            "external XMLTV response encoding is not supported"
                        )
                    announced = headers.get("content-length", "")
                    if announced:
                        try:
                            announced_size = int(announced)
                        except (TypeError, ValueError, OverflowError) as error:
                            raise M3UEPGSourceError(
                                "external XMLTV response size is invalid"
                            ) from error
                        announced_maximum = (
                            MAX_COMPRESSED_XMLTV_BYTES
                            if encoding in ("gzip", "x-gzip")
                            else MAX_UNCOMPRESSED_XMLTV_BYTES
                        )
                        if announced_size < 0 or announced_size > announced_maximum:
                            raise M3UEPGSourceError(
                                "external XMLTV response exceeds its size limit"
                            )
                    result = _copy_http_response(
                        response,
                        connection,
                        destination,
                        encoding in ("gzip", "x-gzip"),
                        timeout,
                        cancel_event,
                        deadline,
                    )
                break
            except M3UEPGError:
                raise
            except ContentError as error:
                raise M3UEPGSourceError("external XMLTV response is invalid") from error
            except (OSError, socket.error, http.client.HTTPException, ssl.SSLError) as error:
                _check_limits(cancel_event, deadline)
                last_error = error
                result = None
                status_code = 0
                redirect_location = ""
            finally:
                if response is not None:
                    try:
                        response.close()
                    except Exception:
                        pass
                if connection is not None:
                    try:
                        connection.close()
                    except Exception:
                        pass
            if status_code or result is not None:
                break
        if status_code == 200 and result is not None:
            return result[0], result[1], redirect_count
        if status_code in _REDIRECT_STATUSES:
            if not redirect_location or redirect_count >= MAX_REDIRECTS:
                raise M3UEPGSourceError("external XMLTV redirect limit exceeded")
            previous_scheme = info["scheme"]
            try:
                current = m3u._remote_url_info(
                    urljoin(info["url"], redirect_location)
                )["url"]
            except ValueError as error:
                raise M3UEPGSourceError(
                    "external XMLTV redirect URL is invalid"
                ) from error
            continue
        raise M3UEPGSourceError("external XMLTV source could not be loaded") from last_error
    raise M3UEPGSourceError("external XMLTV redirect limit exceeded")


def _copy_http_response(
    response,
    connection,
    destination,
    encoded_gzip,
    timeout,
    cancel_event,
    deadline,
):
    descriptor = None
    total = 0
    prefix = b""
    compressed = bool(encoded_gzip)
    try:
        descriptor = _create_stage_file(destination)
        while True:
            _check_limits(cancel_event, deadline)
            m3u._set_response_socket_timeout(
                response,
                connection,
                _remaining_timeout(deadline, timeout),
            )
            reader = getattr(response, "read1", None)
            if not callable(reader):
                reader = response.read
            chunk = reader(READ_CHUNK_BYTES)
            _check_limits(cancel_event, deadline)
            if not chunk:
                break
            if len(prefix) < 2:
                prefix = (prefix + bytes(chunk))[:2]
                compressed = compressed or prefix == b"\x1f\x8b"
            total += len(chunk)
            maximum = (
                MAX_COMPRESSED_XMLTV_BYTES
                if compressed
                else MAX_UNCOMPRESSED_XMLTV_BYTES
            )
            if total > maximum:
                raise M3UEPGSourceError(
                    "external XMLTV response exceeds its size limit"
                )
            _write_all(descriptor, chunk)
        if total <= 0:
            raise M3UEPGSourceError("external XMLTV response is empty")
        if prefix == b"\x1f\x8b" and total > MAX_COMPRESSED_XMLTV_BYTES:
            raise M3UEPGSourceError("compressed XMLTV response is too large")
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
            descriptor = None
        try:
            os.unlink(destination)
        except OSError:
            pass
        raise
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return compressed, total


def _expand_gzip(source_path, destination, cancel_event, deadline):
    descriptor = None
    total = 0
    try:
        descriptor = _create_stage_file(destination)
        with open(source_path, "rb") as raw_file:
            with gzip.GzipFile(fileobj=raw_file, mode="rb") as source:
                while True:
                    _check_limits(cancel_event, deadline)
                    chunk = source.read(
                        min(
                            READ_CHUNK_BYTES,
                            MAX_UNCOMPRESSED_XMLTV_BYTES + 1 - total,
                        )
                    )
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > MAX_UNCOMPRESSED_XMLTV_BYTES:
                        raise M3UEPGSourceError(
                            "expanded XMLTV guide exceeds its size limit"
                        )
                    _write_all(descriptor, chunk)
        if total <= 0:
            raise M3UEPGSourceError("expanded XMLTV guide is empty")
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    except (OSError, EOFError, gzip.BadGzipFile) as error:
        raise M3UEPGSourceError("compressed XMLTV guide is invalid") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return total


def _build_snapshot(
    source_id,
    fingerprint,
    xml_path,
    wanted,
    now,
    cancel_event,
    deadline,
    archive_days=None,
):
    normalised_wanted = set(normalise_tvg_id(value) for value in wanted)
    channels = OrderedDict()
    normalised_channels = defaultdict(set)
    channel_elements = 0
    programme_elements = 0

    # The first pass resolves the complete channel-ID namespace.  XMLTV does
    # not require all channel declarations to precede programmes, so deciding
    # a normalised match while streaming the first programme can turn a later
    # collision into an unsafe or needlessly expensive candidate.
    try:
        source_file = dvb_epg._open_regular_file(
            xml_path,
            MAX_UNCOMPRESSED_XMLTV_BYTES,
        )
        with source_file:
            elements = dvb_epg._iter_xmltv_file(
                source_file,
                cancel_event=cancel_event,
                deadline=deadline,
                maximum_bytes=MAX_UNCOMPRESSED_XMLTV_BYTES,
            )
            for tag, element in elements:
                _check_limits(cancel_event, deadline)
                if tag == "channel":
                    channel_elements += 1
                    if channel_elements > MAX_XMLTV_CHANNELS:
                        raise M3UEPGSourceError("XMLTV channel limit exceeded")
                    try:
                        xmltv_id = _validated_tvg_id(element.attrib.get("id", ""))
                    except ValueError:
                        continue
                    channels.setdefault(xmltv_id, None)
                    normalised_channels[normalise_tvg_id(xmltv_id)].add(xmltv_id)
                    continue
                if tag == "programme":
                    programme_elements += 1
                    if programme_elements > MAX_XMLTV_PROGRAMMES:
                        raise M3UEPGSourceError("XMLTV programme limit exceeded")
    except dvb_epg.OperationCancelled as error:
        raise M3UEPGCancelled(str(error)) from error
    except dvb_epg.XmltvError as error:
        raise M3UEPGSourceError("external XMLTV guide is invalid") from error

    aliases = []
    matched_channels = OrderedDict()
    for index, wanted_id in enumerate(wanted):
        if index % 128 == 0:
            _check_limits(cancel_event, deadline)
        selected = None
        if wanted_id in channels:
            selected = wanted_id
        else:
            candidates = normalised_channels.get(normalise_tvg_id(wanted_id), ())
            if len(candidates) == 1:
                selected = next(iter(candidates))
        if selected is None:
            continue
        aliases.append([wanted_id, selected])
        matched_channels.setdefault(selected, None)

    candidate_events = defaultdict(list)
    invalid_programmes = 0
    retained_events = 0
    retained_text = 0
    earliest = int(now) - (archive_days * 86400 if archive_days is not None else DEFAULT_PAST_SECONDS)
    latest = int(now) if archive_days is not None else int(now) + DEFAULT_FUTURE_SECONDS
    selected_ids = frozenset(matched_channels)

    # The second bounded pass collects text only for the already resolved
    # XMLTV IDs.  A colliding but irrelevant channel can therefore never use
    # another exact match's per-channel or total event budget.
    if selected_ids:
        second_programme_count = 0
        try:
            source_file = dvb_epg._open_regular_file(
                xml_path,
                MAX_UNCOMPRESSED_XMLTV_BYTES,
            )
            with source_file:
                elements = dvb_epg._iter_xmltv_file(
                    source_file,
                    cancel_event=cancel_event,
                    deadline=deadline,
                    maximum_bytes=MAX_UNCOMPRESSED_XMLTV_BYTES,
                )
                for tag, element in elements:
                    if tag != "programme":
                        continue
                    second_programme_count += 1
                    if second_programme_count % 128 == 0:
                        _check_limits(cancel_event, deadline)
                    if second_programme_count > MAX_XMLTV_PROGRAMMES:
                        raise M3UEPGSourceError("XMLTV programme limit exceeded")
                    try:
                        xmltv_id = _validated_tvg_id(
                            element.attrib.get("channel", "")
                        )
                    except ValueError:
                        invalid_programmes += 1
                        continue
                    if xmltv_id not in selected_ids:
                        continue
                    try:
                        start, stop = dvb_epg._programme_time_window(element, 0)
                    except ValueError:
                        invalid_programmes += 1
                        continue
                    if stop <= earliest or start >= latest:
                        continue
                    try:
                        event = dvb_epg._programme_event_tuple(element, start, stop)
                    except ValueError:
                        invalid_programmes += 1
                        continue
                    title = _clean_snapshot_text(event[2], MAX_TITLE_LENGTH)
                    description = _clean_snapshot_text(
                        event[4] or event[3],
                        MAX_DESCRIPTION_LENGTH,
                    )
                    events = candidate_events[xmltv_id]
                    if len(events) >= MAX_EVENTS_PER_CHANNEL:
                        raise M3UEPGSourceError(
                            "XMLTV per-channel event limit exceeded"
                        )
                    retained_events += 1
                    if retained_events > MAX_SNAPSHOT_EVENTS:
                        raise M3UEPGSourceError(
                            "XMLTV retained event limit exceeded"
                        )
                    retained_text += len(title) + len(description)
                    if retained_text > MAX_SNAPSHOT_TEXT_CHARACTERS:
                        raise M3UEPGSourceError(
                            "XMLTV retained text limit exceeded"
                        )
                    events.append((int(start), int(stop), title, description))
            if second_programme_count != programme_elements:
                raise M3UEPGSourceError("XMLTV guide changed while being parsed")
        except dvb_epg.OperationCancelled as error:
            raise M3UEPGCancelled(str(error)) from error
        except dvb_epg.XmltvError as error:
            raise M3UEPGSourceError("external XMLTV guide is invalid") from error

    snapshot_channels = []
    event_count = 0
    for channel_index, xmltv_id in enumerate(matched_channels):
        if channel_index % 128 == 0:
            _check_limits(cancel_event, deadline)
        rows = sorted(
            candidate_events.get(xmltv_id, ()),
            key=lambda row: (row[0], row[1], row[2]),
        )
        deduplicated = []
        previous = None
        for event_index, row in enumerate(rows):
            if event_index % 128 == 0:
                _check_limits(cancel_event, deadline)
            key = (row[0], row[1], row[2])
            if key == previous:
                continue
            deduplicated.append(list(row))
            previous = key
        event_count += len(deduplicated)
        snapshot_channels.append([xmltv_id, deduplicated])

    ambiguous = []
    for key_index, (key, values) in enumerate(normalised_channels.items()):
        if key_index % 128 == 0:
            _check_limits(cancel_event, deadline)
        if key in normalised_wanted and len(values) > 1:
            ambiguous.append(key)
    ambiguous.sort()

    payload = {
        "version": M3U_EPG_SNAPSHOT_VERSION,
        "source_id": source_id,
        "binding_fingerprint": fingerprint,
        "cached_at": int(now),
        "aliases": aliases,
        "ambiguous": ambiguous,
        "channels": snapshot_channels,
        "channel_count": channel_elements,
        "programme_count": programme_elements,
        "invalid_programmes": invalid_programmes,
        "matched_ids": len(aliases),
        "event_count": event_count,
    }
    # Validate the complete object before it can reach a last-good cache path.
    # The persisted-size check runs after transfer metadata is attached so the
    # byte budget covers the exact object written to disk.
    _check_limits(cancel_event, deadline)
    validated = _validate_snapshot(
        payload,
        cancel_event=cancel_event,
        deadline=deadline,
    )
    _check_limits(cancel_event, deadline)
    return validated


def _clean_snapshot_text(value, maximum):
    value = unicodedata.normalize("NFC", str(value or ""))
    value = " ".join(value.replace("\r", " ").replace("\n", " ").split())
    return value[: int(maximum)]


def _snapshot_encoded_size(payload, cancel_event=None, deadline=None):
    """Return the exact compact-JSON UTF-8 size without retaining the body."""
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    total = 0
    try:
        for index, chunk in enumerate(encoder.iterencode(payload)):
            if deadline is not None and index % 128 == 0:
                _check_limits(cancel_event, deadline)
            total += len(chunk.encode("utf-8"))
    except (TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise M3UEPGSnapshotError(
            "M3U EPG snapshot cannot be encoded"
        ) from error
    if deadline is not None:
        _check_limits(cancel_event, deadline)
    return total


def _encoded_json_size(value):
    try:
        return len(
            json.dumps(
                value,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )
    except (TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise M3UEPGSnapshotError(
            "M3U EPG snapshot cannot be encoded"
        ) from error


def _compacted_snapshot_target():
    """Return a soft budget below the hard publication ceiling."""
    hard_limit = max(1, int(MAX_SNAPSHOT_BYTES))
    configured = max(1, int(TARGET_COMPACTED_SNAPSHOT_BYTES))
    if configured < hard_limit:
        return configured
    reserve = max(64, hard_limit // 16)
    return max(1, hard_limit - reserve)


def _lookup_event_indices(rows, now):
    """Return rows needed for current/next and active-overlap continuity."""
    current_index = None
    upcoming_index = None
    for index, event in enumerate(rows):
        start, stop = event[0], event[1]
        if start <= now < stop:
            current_index = index
            break
        if start >= now and upcoming_index is None:
            upcoming_index = index
            break
    if current_index is not None:
        essential = [current_index]
        following_index = None
        for index in range(current_index + 1, len(rows)):
            start, stop = rows[index][0], rows[index][1]
            if start <= now < stop:
                essential.append(index)
            if following_index is None and start >= now:
                following_index = index
            if start > now:
                break
        if following_index is not None and following_index not in essential:
            essential.append(following_index)
        return tuple(essential)
    if upcoming_index is not None:
        return (upcoming_index,)
    return ()


def _description_capped_event(event, maximum):
    if maximum is None:
        return event
    # A slice can land on a word separator.  Removing only that trailing
    # separator keeps the value canonical under the publication validator, so
    # the tracked UTF-8 byte size remains the exact persisted size.
    description = _clean_snapshot_text(event[3], maximum).rstrip()
    if description == event[3]:
        return event
    return [event[0], event[1], event[2], description]


def _next_optional_event(rows, essential, start_index, now):
    for event_index in range(start_index, len(rows)):
        event = rows[event_index]
        if event[0] >= now and event_index not in essential:
            return event_index
    return None


def _compact_snapshot_to_budget(
    payload,
    now,
    cancel_event,
    deadline,
):
    """Retain lookup-critical rows, then add a deterministic future prefix."""
    hard_limit = int(MAX_SNAPSHOT_BYTES)
    target_limit = _compacted_snapshot_target()
    original_channels = payload["channels"]
    compacted_channels = []
    essential_by_channel = []
    retained_events = 0

    for channel_index, (xmltv_id, rows) in enumerate(original_channels):
        if channel_index % 128 == 0:
            _check_limits(cancel_event, deadline)
        essential = frozenset(_lookup_event_indices(rows, now))
        essential_by_channel.append(essential)
        kept = [rows[index] for index in sorted(essential)]
        compacted_channels.append([xmltv_id, kept])
        retained_events += len(kept)

    payload["channels"] = compacted_channels
    payload["event_count"] = retained_events
    description_limit = None
    final_size = _snapshot_encoded_size(payload, cancel_event, deadline)

    if final_size > hard_limit:
        for description_limit in (COMPACTED_DESCRIPTION_LENGTH, 0):
            for channel_index, channel in enumerate(compacted_channels):
                if channel_index % 128 == 0:
                    _check_limits(cancel_event, deadline)
                channel[1] = [
                    _description_capped_event(event, description_limit)
                    for event in channel[1]
                ]
            final_size = _snapshot_encoded_size(
                payload,
                cancel_event,
                deadline,
            )
            if final_size <= hard_limit:
                break
        if final_size > hard_limit:
            raise M3UEPGSnapshotError(
                "M3U EPG essential snapshot exceeds its size limit"
            )

    if final_size < target_limit:
        candidates = []
        for channel_index, unused_channel in enumerate(original_channels):
            if channel_index % 128 == 0:
                _check_limits(cancel_event, deadline)
            rows = original_channels[channel_index][1]
            event_index = _next_optional_event(
                rows,
                essential_by_channel[channel_index],
                0,
                now,
            )
            if event_index is not None:
                event = rows[event_index]
                heapq.heappush(
                    candidates,
                    (
                        event[0],
                        event[1],
                        channel_index,
                        event_index,
                    ),
                )

        operation_count = 0
        while candidates:
            if operation_count % 128 == 0:
                _check_limits(cancel_event, deadline)
            unused_start, unused_stop, channel_index, event_index = heapq.heappop(
                candidates
            )
            rows = original_channels[channel_index][1]
            event = _description_capped_event(
                rows[event_index],
                description_limit,
            )
            kept = compacted_channels[channel_index][1]
            event_size = _encoded_json_size(event)
            count_delta = len(str(retained_events + 1)) - len(
                str(retained_events)
            )
            size_delta = event_size + (1 if kept else 0) + count_delta
            if final_size + size_delta > target_limit:
                break
            kept.append(event)
            retained_events += 1
            payload["event_count"] = retained_events
            final_size += size_delta
            next_index = _next_optional_event(
                rows,
                essential_by_channel[channel_index],
                event_index + 1,
                now,
            )
            if next_index is not None:
                next_event = rows[next_index]
                heapq.heappush(
                    candidates,
                    (
                        next_event[0],
                        next_event[1],
                        channel_index,
                        next_index,
                    ),
                )
            operation_count += 1

    _check_limits(cancel_event, deadline)
    trimmed_descriptions = 0
    for channel_index, channel in enumerate(compacted_channels):
        if channel_index % 128 == 0:
            _check_limits(cancel_event, deadline)
        original_rows = original_channels[channel_index][1]
        original_descriptions = {
            (event[0], event[1], event[2]): event[3] for event in original_rows
        }
        for event in channel[1]:
            if original_descriptions.get((event[0], event[1], event[2])) != event[3]:
                trimmed_descriptions += 1
    return payload, final_size, retained_events, trimmed_descriptions


def _fit_archive_snapshot_budget(
    payload,
    now,
    cancel_event=None,
    deadline=None,
    diagnostics=None,
):
    """Keep historical rows when reducing one channel's archive byte size.

    The live compactor deliberately prioritises current/next and future rows,
    so it must never be used for an all-past archive.  Trim descriptions while
    retaining every programme; fail explicitly if titles alone cannot fit.
    """
    if deadline is None:
        deadline = float("inf")
    original_events = int(payload.get("event_count", 0) or 0)
    original_size = _snapshot_encoded_size(payload, cancel_event, deadline)
    if diagnostics is not None:
        diagnostics.update({
            "snapshot_original_bytes": original_size,
            "snapshot_final_bytes": original_size,
            "snapshot_original_events": original_events,
            "snapshot_retained_events": original_events,
            "snapshot_dropped_events": 0,
            "snapshot_trimmed_descriptions": 0,
            "snapshot_compacted": int(original_size > int(MAX_SNAPSHOT_BYTES)),
        })
    if original_size <= int(MAX_SNAPSHOT_BYTES):
        return payload

    for description_limit in (COMPACTED_DESCRIPTION_LENGTH, 0):
        compacted = dict(payload)
        compacted_channels = []
        trimmed = 0
        for channel_id, rows in payload["channels"]:
            _check_limits(cancel_event, deadline)
            retained = []
            for index, row in enumerate(rows):
                if index % 64 == 0:
                    _check_limits(cancel_event, deadline)
                capped = _description_capped_event(row, description_limit)
                trimmed += int(capped[3] != row[3])
                retained.append(capped)
            compacted_channels.append([channel_id, retained])
        compacted["channels"] = compacted_channels
        final_size = _snapshot_encoded_size(compacted, cancel_event, deadline)
        if final_size <= int(MAX_SNAPSHOT_BYTES):
            validated = _validate_snapshot(compacted, cancel_event, deadline)
            if diagnostics is not None:
                diagnostics["snapshot_final_bytes"] = final_size
                diagnostics["snapshot_trimmed_descriptions"] = trimmed
            return validated
    raise M3UEPGSnapshotError("M3U archive snapshot exceeds its size limit")


def _fit_snapshot_budget(
    payload,
    now,
    cancel_event=None,
    deadline=None,
    diagnostics=None,
):
    """Fit an oversized snapshot while leaving ordinary R58 output intact."""
    if deadline is None:
        deadline = float("inf")
    original_events = int(payload.get("event_count", 0) or 0)
    original_size = _snapshot_encoded_size(payload, cancel_event, deadline)
    if diagnostics is not None:
        diagnostics["snapshot_original_bytes"] = original_size
        diagnostics["snapshot_final_bytes"] = original_size
        diagnostics["snapshot_original_events"] = original_events
        diagnostics["snapshot_retained_events"] = original_events
        diagnostics["snapshot_dropped_events"] = 0
        diagnostics["snapshot_trimmed_descriptions"] = 0
    if original_size <= int(MAX_SNAPSHOT_BYTES):
        if diagnostics is not None:
            diagnostics["snapshot_compacted"] = 0
        return payload

    if diagnostics is not None:
        diagnostics["snapshot_compacted"] = 1
    compacted, final_size, retained_events, trimmed = _compact_snapshot_to_budget(
        payload,
        now,
        cancel_event,
        deadline,
    )
    validated = _validate_snapshot(
        compacted,
        cancel_event=cancel_event,
        deadline=deadline,
    )
    if final_size > int(MAX_SNAPSHOT_BYTES):
        raise M3UEPGSnapshotError("M3U EPG snapshot exceeds its size limit")
    if diagnostics is not None:
        diagnostics["snapshot_final_bytes"] = final_size
        diagnostics["snapshot_retained_events"] = retained_events
        diagnostics["snapshot_dropped_events"] = max(
            0,
            original_events - retained_events,
        )
        diagnostics["snapshot_trimmed_descriptions"] = trimmed
    return validated


def _validate_snapshot(payload, cancel_event=None, deadline=None):
    if not isinstance(payload, dict):
        raise M3UEPGSnapshotError("M3U EPG snapshot is not an object")
    if payload.get("version") != M3U_EPG_SNAPSHOT_VERSION:
        raise M3UEPGSnapshotError("M3U EPG snapshot version is not supported")
    source_id = _validated_source_id(payload.get("source_id", ""))
    fingerprint = str(payload.get("binding_fingerprint", ""))
    if len(fingerprint) != 64 or any(
        character not in "0123456789abcdef" for character in fingerprint
    ):
        raise M3UEPGSnapshotError("M3U EPG binding fingerprint is invalid")
    try:
        cached_at = int(payload.get("cached_at"))
    except (TypeError, ValueError, OverflowError) as error:
        raise M3UEPGSnapshotError("M3U EPG snapshot time is invalid") from error
    if cached_at < 0 or cached_at > 0x7FFFFFFFFFFFFFFF:
        raise M3UEPGSnapshotError("M3U EPG snapshot time is invalid")

    raw_channels = payload.get("channels")
    raw_aliases = payload.get("aliases")
    raw_ambiguous = payload.get("ambiguous")
    if (
        not isinstance(raw_channels, list)
        or not isinstance(raw_aliases, list)
        or not isinstance(raw_ambiguous, list)
    ):
        raise M3UEPGSnapshotError("M3U EPG snapshot entries are invalid")
    channels = []
    known_channels = set()
    total_events = 0
    total_text = 0
    for channel_index, raw_channel in enumerate(raw_channels):
        if deadline is not None and channel_index % 128 == 0:
            _check_limits(cancel_event, deadline)
        if not isinstance(raw_channel, list) or len(raw_channel) != 2:
            raise M3UEPGSnapshotError("M3U EPG channel entry is invalid")
        xmltv_id = _validated_tvg_id(raw_channel[0])
        if xmltv_id in known_channels:
            raise M3UEPGSnapshotError("M3U EPG channel is duplicated")
        known_channels.add(xmltv_id)
        if len(known_channels) > MAX_WANTED_TVG_IDS:
            raise M3UEPGSnapshotError("M3U EPG snapshot has too many channels")
        raw_events = raw_channel[1]
        if not isinstance(raw_events, list) or len(raw_events) > MAX_EVENTS_PER_CHANNEL:
            raise M3UEPGSnapshotError("M3U EPG channel events are invalid")
        events = []
        previous_key = None
        for event_index, raw_event in enumerate(raw_events):
            if deadline is not None and event_index % 128 == 0:
                _check_limits(cancel_event, deadline)
            if not isinstance(raw_event, list) or len(raw_event) != 4:
                raise M3UEPGSnapshotError("M3U EPG event entry is invalid")
            try:
                start = int(raw_event[0])
                stop = int(raw_event[1])
            except (TypeError, ValueError, OverflowError) as error:
                raise M3UEPGSnapshotError("M3U EPG event time is invalid") from error
            if start < 0 or stop <= start or stop - start > 14 * 24 * 60 * 60:
                raise M3UEPGSnapshotError("M3U EPG event duration is invalid")
            title = _clean_snapshot_text(raw_event[2], MAX_TITLE_LENGTH)
            description = _clean_snapshot_text(raw_event[3], MAX_DESCRIPTION_LENGTH)
            if not title:
                raise M3UEPGSnapshotError("M3U EPG event title is empty")
            key = (start, stop, title)
            if previous_key is not None and key < previous_key:
                raise M3UEPGSnapshotError("M3U EPG events are not ordered")
            if key == previous_key:
                raise M3UEPGSnapshotError("M3U EPG event is duplicated")
            previous_key = key
            total_events += 1
            total_text += len(title) + len(description)
            if total_events > MAX_SNAPSHOT_EVENTS:
                raise M3UEPGSnapshotError("M3U EPG snapshot event limit exceeded")
            if total_text > MAX_SNAPSHOT_TEXT_CHARACTERS:
                raise M3UEPGSnapshotError("M3U EPG snapshot text limit exceeded")
            events.append([start, stop, title, description])
        channels.append([xmltv_id, events])

    aliases = []
    seen_aliases = set()
    for alias_index, raw_alias in enumerate(raw_aliases):
        if deadline is not None and alias_index % 128 == 0:
            _check_limits(cancel_event, deadline)
        if not isinstance(raw_alias, list) or len(raw_alias) != 2:
            raise M3UEPGSnapshotError("M3U EPG alias entry is invalid")
        wanted_id = _validated_tvg_id(raw_alias[0])
        xmltv_id = _validated_tvg_id(raw_alias[1])
        if wanted_id in seen_aliases or xmltv_id not in known_channels:
            raise M3UEPGSnapshotError("M3U EPG alias is invalid")
        seen_aliases.add(wanted_id)
        aliases.append([wanted_id, xmltv_id])
        if len(aliases) > MAX_WANTED_TVG_IDS:
            raise M3UEPGSnapshotError("M3U EPG alias limit exceeded")

    ambiguous = []
    seen_ambiguous = set()
    for key_index, raw_key in enumerate(raw_ambiguous):
        if deadline is not None and key_index % 128 == 0:
            _check_limits(cancel_event, deadline)
        key = normalise_tvg_id(_validated_tvg_id(raw_key))
        if not key or key in seen_ambiguous:
            raise M3UEPGSnapshotError("M3U EPG ambiguity entry is invalid")
        seen_ambiguous.add(key)
        ambiguous.append(key)
        if len(ambiguous) > MAX_XMLTV_CHANNELS:
            raise M3UEPGSnapshotError("M3U EPG ambiguity limit exceeded")

    output = {
        "version": M3U_EPG_SNAPSHOT_VERSION,
        "source_id": source_id,
        "binding_fingerprint": fingerprint,
        "cached_at": cached_at,
        "aliases": aliases,
        "ambiguous": ambiguous,
        "channels": channels,
    }
    for name in (
        "channel_count",
        "programme_count",
        "invalid_programmes",
        "matched_ids",
        "event_count",
        "input_bytes",
        "xml_bytes",
        "redirect_count",
    ):
        try:
            value = int(payload.get(name, 0) or 0)
        except (TypeError, ValueError, OverflowError) as error:
            raise M3UEPGSnapshotError("M3U EPG snapshot count is invalid") from error
        if value < 0 or value > 0x7FFFFFFFFFFFFFFF:
            raise M3UEPGSnapshotError("M3U EPG snapshot count is invalid")
        output[name] = value
    if output["matched_ids"] != len(aliases) or output["event_count"] != total_events:
        raise M3UEPGSnapshotError("M3U EPG snapshot totals do not match")
    return output


def _serialize_snapshot(payload):
    try:
        body = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError, UnicodeError, OverflowError) as error:
        raise M3UEPGSnapshotError("M3U EPG snapshot cannot be encoded") from error
    if len(body) > MAX_SNAPSHOT_BYTES:
        raise M3UEPGSnapshotError("M3U EPG snapshot exceeds its size limit")
    return body


def _runtime_snapshot(payload, cancel_event=None, deadline=None):
    exact = {}
    normalised_candidates = defaultdict(set)
    events = {}
    for channel_index, (xmltv_id, rows) in enumerate(payload["channels"]):
        if deadline is not None and channel_index % 128 == 0:
            _check_limits(cancel_event, deadline)
        events[xmltv_id] = tuple(tuple(row) for row in rows)
    for alias_index, (wanted_id, xmltv_id) in enumerate(payload["aliases"]):
        if deadline is not None and alias_index % 128 == 0:
            _check_limits(cancel_event, deadline)
        exact[wanted_id] = xmltv_id
        normalised_candidates[normalise_tvg_id(wanted_id)].add(xmltv_id)
    ambiguous = frozenset(payload.get("ambiguous", ()))
    normalised = {
        key: next(iter(values))
        for key, values in normalised_candidates.items()
        if key and key not in ambiguous and len(values) == 1
    }
    if deadline is not None:
        _check_limits(cancel_event, deadline)
    return {
        "source_id": payload["source_id"],
        "binding_fingerprint": payload["binding_fingerprint"],
        "cached_at": payload["cached_at"],
        "exact": exact,
        "normalised": normalised,
        "events": events,
    }


def _content_event(row, now_playing):
    return EPGEvent(
        row[2],
        row[0],
        row[1],
        now_playing=now_playing,
        description=row[3],
    )


def _snapshot_summary(payload, size_bytes):
    return {
        "source_id": payload["source_id"],
        "cached_at": payload["cached_at"],
        "matched_ids": payload.get("matched_ids", len(payload["aliases"])),
        "event_count": payload.get(
            "event_count",
            sum(len(rows) for unused, rows in payload["channels"]),
        ),
        "channel_count": payload.get("channel_count", 0),
        "programme_count": payload.get("programme_count", 0),
        "invalid_programmes": payload.get("invalid_programmes", 0),
        "size_bytes": int(size_bytes),
    }


def _open_private_cache_directory(path, create=False):
    """Open a private cache dir without following any path-component link."""
    path = _validated_absolute_directory(path)
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(os.sep, flags)
    final_created = False
    try:
        components = [component for component in path.split(os.sep) if component]
        for index, component in enumerate(components):
            try:
                next_descriptor = os.open(
                    component,
                    flags,
                    dir_fd=descriptor,
                )
            except FileNotFoundError as error:
                if not create:
                    raise
                if index != len(components) - 1:
                    raise M3UEPGSnapshotError(
                        "M3U EPG cache parent directory is missing"
                    ) from error
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
                next_descriptor = os.open(
                    component,
                    flags,
                    dir_fd=descriptor,
                )
                if index == len(components) - 1:
                    final_created = True
            os.close(descriptor)
            descriptor = next_descriptor
        details = os.fstat(descriptor)
        if not stat.S_ISDIR(details.st_mode) or details.st_uid != os.geteuid():
            raise M3UEPGSnapshotError("M3U EPG cache directory is unsafe")
        permissions = stat.S_IMODE(details.st_mode)
        if final_created:
            os.fchmod(descriptor, 0o700)
            permissions = stat.S_IMODE(os.fstat(descriptor).st_mode)
        if permissions != 0o700:
            raise M3UEPGSnapshotError("M3U EPG cache directory is not private")
        return descriptor
    except FileNotFoundError:
        os.close(descriptor)
        raise
    except M3UEPGSnapshotError:
        os.close(descriptor)
        raise
    except OSError as error:
        os.close(descriptor)
        raise M3UEPGSnapshotError("M3U EPG cache directory is unsafe") from error


def _snapshot_source_from_name(name):
    if not name.endswith(".snapshot"):
        return ""
    source_id = name[: -len(".snapshot")]
    try:
        return _validated_source_id(source_id)
    except ValueError:
        raise M3UEPGSnapshotError("M3U EPG cache entry name is invalid")


def _owned_temporary_source(name):
    if not name.startswith(".") or not name.endswith(".tmp"):
        return ""
    parts = name[1:-4].rsplit(".", 3)
    if (
        len(parts) != 4
        or parts[1] != "snapshot"
        or not parts[2].isdigit()
        or len(parts[3]) != 32
        or any(character not in "0123456789abcdef" for character in parts[3])
    ):
        return ""
    try:
        return _validated_source_id(parts[0])
    except ValueError:
        return ""


def _cache_usage(directory_descriptor):
    total = 0
    count = 0
    examined = 0
    with os.scandir(directory_descriptor) as entries:
        for entry in entries:
            examined += 1
            if examined > MAX_CACHE_DIRECTORY_ENTRIES:
                raise M3UEPGSnapshotError(
                    "M3U EPG cache directory entry limit exceeded"
                )
            name = entry.name
            source_id = _snapshot_source_from_name(name)
            temporary_source = _owned_temporary_source(name)
            if not source_id and not temporary_source:
                continue
            details = os.stat(
                name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                stat.S_ISLNK(details.st_mode)
                or not stat.S_ISREG(details.st_mode)
                or details.st_size > MAX_SNAPSHOT_BYTES
            ):
                raise M3UEPGSnapshotError(
                    "M3U EPG cache contains an unsafe entry"
                )
            count += 1
            total += int(details.st_size)
            if count > MAX_SNAPSHOT_FILES or total > MAX_SNAPSHOT_CACHE_BYTES:
                raise M3UEPGSnapshotError("M3U EPG cache budget is exhausted")
    return total, count


def _install_snapshot(
    source_id,
    body,
    cache_dir,
    cancel_event=None,
    deadline=None,
):
    if len(body) > MAX_SNAPSHOT_BYTES:
        raise M3UEPGSnapshotError("M3U EPG snapshot exceeds its size limit")
    cache_dir = _validated_absolute_directory(cache_dir)
    name = "{}.snapshot".format(source_id)
    temporary = ".{}.{}.{}.tmp".format(
        name,
        os.getpid(),
        uuid.uuid4().hex,
    )
    with _CACHE_LOCK:
        directory_descriptor = _open_private_cache_directory(
            cache_dir,
            create=True,
        )
        try:
            try:
                details = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
            except FileNotFoundError:
                details = None
            if details is not None and (
                stat.S_ISLNK(details.st_mode)
                or not stat.S_ISREG(details.st_mode)
            ):
                raise M3UEPGSnapshotError("M3U EPG snapshot target is unsafe")
            total, count = _cache_usage(directory_descriptor)
            if total + len(body) > MAX_SNAPSHOT_CACHE_BYTES:
                raise M3UEPGSnapshotError("M3U EPG total cache limit exceeded")
            if count >= MAX_SNAPSHOT_FILES:
                raise M3UEPGSnapshotError("M3U EPG snapshot file limit exceeded")
            descriptor = _create_stage_file(
                temporary,
                directory_descriptor=directory_descriptor,
            )
            try:
                offset = 0
                while offset < len(body):
                    if deadline is not None:
                        _check_limits(cancel_event, deadline)
                    end = min(offset + READ_CHUNK_BYTES, len(body))
                    written = os.write(descriptor, body[offset:end])
                    if written <= 0:
                        raise OSError("short M3U EPG snapshot write")
                    offset += written
                os.fsync(descriptor)
                if deadline is not None:
                    _check_limits(cancel_event, deadline)
                os.fchmod(descriptor, 0o600)
            except Exception:
                os.close(descriptor)
                try:
                    os.unlink(temporary, dir_fd=directory_descriptor)
                except OSError:
                    pass
                raise
            else:
                os.close(descriptor)
            try:
                if deadline is not None:
                    _check_limits(cancel_event, deadline)
                os.replace(
                    temporary,
                    name,
                    src_dir_fd=directory_descriptor,
                    dst_dir_fd=directory_descriptor,
                )
                try:
                    os.fsync(directory_descriptor)
                except OSError:
                    pass
            except Exception:
                try:
                    os.unlink(temporary, dir_fd=directory_descriptor)
                except OSError:
                    pass
                raise
        finally:
            os.close(directory_descriptor)


def _read_snapshot_file(path, directory_descriptor=None):
    if directory_descriptor is None:
        listed = os.lstat(path)
    else:
        listed = os.stat(
            path,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    if (
        stat.S_ISLNK(listed.st_mode)
        or not stat.S_ISREG(listed.st_mode)
        or listed.st_size > MAX_SNAPSHOT_BYTES
    ):
        raise M3UEPGSnapshotError("M3U EPG snapshot file is unsafe")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    if directory_descriptor is None:
        descriptor = os.open(path, flags)
    else:
        descriptor = os.open(path, flags, dir_fd=directory_descriptor)
    chunks = []
    total = 0
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_dev != listed.st_dev
            or details.st_ino != listed.st_ino
            or details.st_size > MAX_SNAPSHOT_BYTES
        ):
            raise M3UEPGSnapshotError("M3U EPG snapshot file changed")
        while True:
            chunk = os.read(
                descriptor,
                min(READ_CHUNK_BYTES, MAX_SNAPSHOT_BYTES + 1 - total),
            )
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_SNAPSHOT_BYTES:
                raise M3UEPGSnapshotError("M3U EPG snapshot is too large")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _fsync_directory(directory):
    try:
        descriptor = os.open(
            directory,
            os.O_RDONLY | getattr(os, "O_CLOEXEC", 0),
        )
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


__all__ = (
    "EPGStore",
    "M3UEPGStore",
    "M3UEPGError",
    "M3UEPGSourceError",
    "M3UEPGSnapshotError",
    "M3UEPGCancelled",
    "get_default_store",
    "refresh_source",
    "snapshot_path",
    "snapshot_paths",
    "delete_snapshot",
)

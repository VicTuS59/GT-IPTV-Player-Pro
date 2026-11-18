# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded external XMLTV support for M3U live channels.

Refresh work and cold lookups belong on the existing EPG worker threads.
Published snapshots use compact local indexes and a shared decoded-channel
cache. GUI rendering requests cache-only lookups, which never open a file,
resolve a host, or parse XML while changing channels.

An explicit playlist ``tvg-id`` retains priority. Entries without an ID can
match a unique normalised XMLTV display name or use a saved manual choice.
A small fitted name model runs on unresolved entries during background refresh.
"""

import gzip
import hashlib
import heapq
import http.client
import json
import math
import os
import re
import socket
import ssl
import stat
import sys
import tempfile
import threading
import time
import unicodedata
import uuid
import zlib
from collections import OrderedDict, defaultdict
from difflib import SequenceMatcher
from types import MappingProxyType
from urllib.parse import urljoin

from . import PLUGIN_VERSION
from . import dvb_epg
from . import m3u
from .content import ContentError, EPGEvent
from .epg_runtime import DECODED_CHANNEL_BYTES, DecodedChannelCache, EPGChannelIndex
from .network_security import MAX_PUBLIC_URL_LENGTH, NetworkSecurityError, validate_public_url_syntax


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
MAX_NAME_CHOICES_BYTES = 1024 * 1024
MAX_CHANNEL_ICON_BYTES = 1024 * 1024
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

    def __init__(self, message="", reason=""):
        super().__init__(message)
        self.reason = reason


class M3UEPGSourceError(M3UEPGError):
    """The configured local or remote XMLTV source could not be read."""


class M3UEPGSnapshotError(M3UEPGError):
    """A snapshot is invalid or cannot be stored within its fixed budget."""


class M3UEPGCancelled(M3UEPGError):
    """A bounded worker operation was cancelled or reached its deadline."""


def get_default_store():
    """Return the process-wide compact snapshot store."""
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
    """Compact local indexes with a shared, bounded RAM cache of channel rows."""

    def __init__(self, cache_dir=M3U_EPG_CACHE_DIRECTORY,
                 max_decoded_bytes=DECODED_CHANNEL_BYTES,
                 max_index_bytes=MAX_SNAPSHOT_CACHE_BYTES):
        self.cache_dir = _validated_absolute_directory(cache_dir)
        self._lock = threading.RLock()
        self._snapshots = OrderedDict()
        self._generations = {}
        self._decoded = DecodedChannelCache(maximum=max_decoded_bytes)
        self._index_bytes = 0
        self._index_limit = max(1, int(max_index_bytes))

    def _drop_runtime_locked(self, source_id):
        runtime = self._snapshots.pop(source_id, None)
        index = runtime.get("index") if runtime is not None else None
        if index is not None:
            self._index_bytes -= runtime["size_bytes"]
            self._decoded.drop(index.ticket)
        return runtime

    def _remember_runtime_locked(self, source_id, runtime):
        self._drop_runtime_locked(source_id)
        self._snapshots[source_id] = runtime
        self._index_bytes += runtime["size_bytes"]
        # Normally 64 MiB, matching the existing snapshot-file budget. At
        # least the newest single index stays usable even in tiny test budgets.
        limit = max(self._index_limit, runtime["size_bytes"])
        for other_id, other in self._snapshots.items():
            if self._index_bytes <= limit:
                break
            cold = other.get("index")
            if other_id != source_id and cold is not None:
                self._index_bytes -= other["size_bytes"]
                self._decoded.drop(cold.ticket)
                other["index"] = None
                other["picon_urls"] = None

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
        target_dir = self.cache_dir if cache_dir is None else cache_dir
        runtime = _runtime_snapshot(
            validated,
            cancel_event=_cancel_event,
            deadline=_deadline,
            cache=self._decoded,
            cache_dir=target_dir,
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
                self._remember_runtime_locked(source_id, runtime)
        return _snapshot_summary(validated, len(serialized))

    def load_from_disk(self, source_id, binding=None, cache_dir=None, _expected_runtime=None):
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
            if _expected_runtime is not None:
                with self._lock:
                    current = self._snapshots.get(source_id)
                    if current is not _expected_runtime:
                        return bool(current is not None and current.get("index") is not None)
                expected = _expected_runtime["binding_fingerprint"]
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
                del body
                validated = _validate_snapshot(payload)
                del payload
                if validated["source_id"] != source_id:
                    raise M3UEPGSnapshotError("snapshot source ID does not match")
                if (
                    expected is not None
                    and validated["binding_fingerprint"] != expected
                ):
                    self.forget(source_id)
                    return False
                runtime = _runtime_snapshot(validated, cache=self._decoded,
                    cache_dir=target_dir, directory_descriptor=directory_descriptor)
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
                self._remember_runtime_locked(source_id, runtime)
            return True

    def _lookup_index(self, source_id, cache_only=False):
        with self._lock:
            runtime = self._snapshots.get(source_id)
            if runtime is not None:
                self._snapshots.move_to_end(source_id)
        if runtime is None:
            return None
        index = runtime.get("index")
        if index is None:
            if cache_only:
                return None
            if not self.load_from_disk(source_id, cache_dir=runtime["cache_dir"], _expected_runtime=runtime):
                return None
            with self._lock:
                runtime = self._snapshots.get(source_id)
                index = runtime.get("index") if runtime is not None else None
        return index

    def resolve_name(self, source_id, key, fingerprint=None, legacy_key=""):
        if not m3u.is_epg_name_key(key):
            return ""
        index = self._lookup_index(source_id)
        if index is None:
            return ""
        if fingerprint is not None:
            with self._lock:
                runtime = self._snapshots.get(source_id)
                if (runtime is None or runtime["index"] is not index
                        or runtime["binding_fingerprint"] != fingerprint):
                    return ""
        if not index.scoped_names and m3u.is_epg_name_key(legacy_key):
            key = legacy_key
        return index.resolve_name(key)

    def name_choices(self, source_id, fingerprint):
        with self._lock:
            runtime = self._snapshots.get(source_id)
            if runtime is not None and runtime["binding_fingerprint"] == fingerprint:
                return runtime.get("name_choices", ())
        return ()

    def picon_urls(self, source_id):
        """Return immutable logo URLs for resolved playlist aliases, from RAM."""
        with self._lock:
            runtime = self._snapshots.get(source_id)
            return runtime.get("picon_urls") if runtime is not None else None

    def picon_url(self, source_id, tvg_id, key, legacy_key=""):
        with self._lock:
            runtime = self._snapshots.get(source_id)
            urls = runtime.get("picon_urls") if runtime is not None else None
            if urls is None:
                return ""
            if legacy_key and not runtime["scoped_names"]:
                key = legacy_key
            return urls.get(tvg_id) or urls.get(key, "")

    def snapshot_revision(self, source_id):
        """Identify the published guide while keeping lookups in memory."""
        with self._lock:
            runtime = self._snapshots.get(source_id)
            return runtime["revision"] if runtime is not None else None

    def lookup(self, source_id, tvg_id, now=None, cache_only=False, fallback_name_key="",
               legacy_name_key=""):
        """Read current/next on existing EPG workers; hot channels stay in RAM."""
        try:
            source_id = _validated_source_id(source_id)
            tvg_id = _validated_tvg_id(tvg_id)
            moment = float(time.time() if now is None else now)
        except (TypeError, ValueError, OverflowError):
            return (None, None)
        if not math.isfinite(moment):
            return (None, None)
        index = self._lookup_index(source_id, cache_only)
        if index is None:
            return (None, None)
        if not index.scoped_names and m3u.is_epg_name_key(legacy_name_key):
            fallback_name_key = legacy_name_key
        events = index.get(
            tvg_id, normalise_tvg_id(tvg_id), cache_only=cache_only,
            fallback_name_key=fallback_name_key if m3u.is_epg_name_key(fallback_name_key) else "",
        )
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
                removed = self._drop_runtime_locked(source_id) is not None
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
                    self._drop_runtime_locked(source_id)
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
    guide_all_dates=False,
    wanted_names=None,
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
    wanted_names = wanted_names or {}
    if not isinstance(wanted_names, dict) or len(wanted_names) > MAX_WANTED_TVG_IDS:
        raise ValueError("invalid playlist EPG names")
    if guide_all_dates and (len(wanted) != 1 or persist or store is None or archive_days is not None):
        raise ValueError("full XMLTV guide reads require one channel and an isolated nonpersistent store")
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
        icon_base_url = ""
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
                compressed, input_bytes, redirect_count, icon_base_url = _download_remote_source(
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
                icon_base_url=icon_base_url,
                **({"wanted_names": wanted_names,
                    "name_mappings": _binding_value(binding, "name_mappings"),
                    "guide_country": _binding_value(binding, "free_epg_country")}
                   if wanted_names else {}),
                **({"archive_days": archive_days} if archive_days is not None else {}),
                **({"guide_all_dates": True} if guide_all_dates else {}),
            )
        finally:
            _record_refresh_elapsed(timing, "xml_scan_ms", scan_started)
        payload["input_bytes"] = int(input_bytes)
        payload["xml_bytes"] = int(xml_bytes)
        payload["redirect_count"] = int(redirect_count)
        compact_started = time.monotonic()
        try:
            fit_budget = (_fit_archive_snapshot_budget if archive_days is not None or guide_all_dates
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


def read_guide_events(source_id, binding, tvg_id, **options):
    """Read every dated event for one selected channel without live-cache writes."""
    return read_archive_events(source_id, binding, tvg_id, None, guide_all_dates=True, **options)


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
        raise M3UEPGCancelled("external XMLTV refresh reached its deadline", "deadline")


def _remaining_timeout(deadline, timeout):
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise M3UEPGCancelled("external XMLTV refresh reached its deadline", "deadline")
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
        | getattr(os, "O_BINARY", 0)
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
            if isinstance(error.__cause__, socket.gaierror):
                raise M3UEPGSourceError("external XMLTV host could not be resolved", "dns") from error
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
            return result[0], result[1], redirect_count, info["url"]
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
        if isinstance(last_error, (socket.timeout, TimeoutError)):
            raise M3UEPGSourceError("external XMLTV request timed out", "deadline") from last_error
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
            remaining = getattr(response, "length", None)
            if remaining == 0:
                break
            if getattr(response, "fp", True) is None:
                if remaining is not None:
                    raise http.client.IncompleteRead(b"", remaining)
                break
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
                remaining = getattr(response, "length", None)
                if remaining not in (None, 0):
                    raise http.client.IncompleteRead(b"", remaining)
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
    except (OSError, EOFError, zlib.error) as error:
        raise M3UEPGSourceError("compressed XMLTV guide is invalid") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)
    return total


def _without_epg_quality(name, guide_label=False, provider_label=False):
    parts = name.split()
    labels = _EPG_PROVIDER_QUALITY_LABELS if provider_label else _EPG_QUALITY_LABELS
    while len(parts) > 1 and parts[-1].strip("()[]") in labels:
        if guide_label and parts[-1] in ("4k", "8k"):
            break
        parts.pop()
    return " ".join(parts)


_EPG_COUNTRY_PREFIX = re.compile(r"^(?:\[([a-z]{2})\]|\(([a-z]{2})\)|([a-z]{2})(?:\s*[:|]\s*|\s+[-–—]\s+))\s*")
_EPG_NAME_ANNOTATION = re.compile(r"\s*(?:\(\s*#\s*\)|\[\s*#\s*\])$")
_EPG_PROVIDER_NUMBER = re.compile(r"\s+\(([1-9][0-9]?)\)$")
_EPG_LEADING_QUALITY = re.compile(r"^(?:4k|8k|uhd|fhd|hd|sd)\s+")
_EPG_SERVICE_NUMBER = re.compile(r"(?:\+\s*)?\d+(?:[.,]\d+)?")
_EPG_QUALITY_LABELS = dvb_epg._QUALITY_SUFFIXES | {"hevc", "720p", "1080p", "2160p", "4320p"}
_EPG_PROVIDER_QUALITY_LABELS = _EPG_QUALITY_LABELS | {"hd+", "sd+", "raw", "h264", "h265"}
_EPG_NAME_ALIASES = {
    "tr": {
        "ssport": "s spor",
        "ssport2": "s spor 2",
        "sinema2": "sinema tv 2",
        "sinema1001": "sinema tv 1001",
        "sinema1002": "sinema tv 1002",
        "sinemaaile": "sinema tv aile",
        "sinemaaile2": "sinema tv aile 2",
        "sinemaaksiyon": "sinema tv aksiyon",
        "sinemaaksiyon2": "sinema tv aksiyon 2",
        "sinemakomedi": "sinema tv comedy",
        "sinemakomedi2": "sinema tv comedy 2",
    },
}


def _epg_id_country(xmltv_id):
    if not isinstance(xmltv_id, str):
        return ""
    _, dot, suffix = xmltv_id.rpartition(".")
    return m3u.epg_country_code(suffix) if dot else ""


def _fold_epg_latin(name):
    if name.isascii():
        return name
    result = []
    latin = False
    for char in unicodedata.normalize("NFD", name):
        if unicodedata.combining(char):
            if not latin:
                result.append(char)
            continue
        latin = "a" <= char <= "z" or char == "ı"
        result.append("i" if char == "ı" else char)
    return unicodedata.normalize("NFC", "".join(result))


def _compact_epg_label(name):
    return "".join(char for char in name if not char.isspace() and char not in "._-–—")


def _epg_name_forms(value, guide_label=False, provider_label=False):
    label = m3u.normalise_epg_name(value)
    prefix = _EPG_COUNTRY_PREFIX.match(label)
    country = m3u.epg_country_code(prefix.group(prefix.lastindex)) if prefix else ""
    if country:
        label = label[prefix.end():]
    plain = _EPG_NAME_ANNOTATION.sub("", label)
    name = _without_epg_quality(plain, guide_label, provider_label)
    compact = _compact_epg_label(name)
    folded = _fold_epg_latin(compact)
    short = folded[:-2] if name.endswith(" tv") and len(folded) > 3 else folded
    numbers = tuple(value.replace(" ", "").replace(",", ".") for value in _EPG_SERVICE_NUMBER.findall(name))
    return country, (label, _compact_epg_label(plain), compact, folded, short), numbers


def _epg_provider_label(value):
    label = m3u.normalise_epg_name(value)
    without_quality = _EPG_LEADING_QUALITY.sub("", label, count=1)
    prefix = _EPG_COUNTRY_PREFIX.match(without_quality)
    if prefix and m3u.epg_country_code(prefix.group(prefix.lastindex)):
        return without_quality
    return label


def _epg_provider_numbers(wanted_names, cancel_event, deadline):
    counts = defaultdict(int)
    separate_numbers = set()
    for index, entry in enumerate(wanted_names.values()):
        if index % 128 == 0:
            _check_limits(cancel_event, deadline)
        label = entry[0] if isinstance(entry, tuple) and entry else entry
        if not isinstance(label, str):
            continue
        label = _epg_provider_label(label)
        suffix = _EPG_PROVIDER_NUMBER.search(label)
        if not suffix:
            continue
        number = suffix[1]
        counts[number] += 1
        if number not in separate_numbers:
            _, _, numbers = _epg_name_forms(label[:suffix.start()], provider_label=True)
            if numbers:
                separate_numbers.add(number)
    # Recognize a playlist convention from repeated markers and names that
    # carry a separate service number, such as "TRT 1 HD (6)".
    if len(counts) < 2 or sum(counts.values()) * 4 < len(wanted_names) * 3:
        return frozenset()
    return frozenset(number for number in separate_numbers if counts[number] >= 6)


def _epg_alias_forms(forms, country):
    alias = _EPG_NAME_ALIASES.get(country, {}).get(forms[3])
    return _epg_name_forms(alias, guide_label=True)[1] if alias else ()


def _epg_candidates(index, forms, country):
    unknown = set()
    for rank, name in enumerate(forms):
        entries = index.get((rank, name))
        if not entries:
            continue
        if not country:
            return {xmltv_id for xmltv_id, _ in entries}
        local = {xmltv_id for xmltv_id, region in entries if region == country}
        if local:
            return local
        if not unknown:
            unknown = {xmltv_id for xmltv_id, region in entries if not region}
    return unknown


_EPG_MODEL_GUIDE_LABEL_LIMIT = 24000
_EPG_MODEL_BUCKET_LIMIT = 24
_EPG_MODEL_SECONDS = 0.45
_EPG_MODEL_PROGRAMME_LIMIT = 12000
_EPG_MODEL_EVENT_LIMIT = 4096
_EPG_MODEL_TEXT_LIMIT = 1024 * 1024
_EPG_MODEL_ICON_LIMIT = 64 * 1024
_EPG_MODEL_WORD_SEPARATORS = re.compile(r"[\s._\-–—]+")
_EPG_MODEL_VOWELS = str.maketrans("", "", "aeiou")

# Log-odds fitted by tests/train-epg-name-model.py. The acceptance threshold
# and runner-up margin apply to scores, not to estimated error probabilities.
_EPG_MODEL_BIAS = -3.895851
_EPG_MODEL_WEIGHTS = (1.213951, 1.483976, 0.363738, 3.023139, 2.624506, 1.505732)
_EPG_MODEL_ACCEPT = 3.20
_EPG_MODEL_UNSCOPED_MARGIN = 0.15
_EPG_MODEL_SUGGEST = 0.30
_EPG_MODEL_MARGIN = 0.90


def _epg_model_profile(label):
    # Keep service numbers, plus signs and 4K/8K channel variants distinct.
    name = _fold_epg_latin(_without_epg_quality(_EPG_NAME_ANNOTATION.sub("", label), guide_label=True))
    words = tuple(word for word in _EPG_MODEL_WORD_SEPARATORS.split(name) if word)
    compact = "".join(words)
    if not 6 <= len(compact) <= 64 or not 1 <= len(words) <= 8:
        return None
    numbers = tuple(number.replace(" ", "").replace(",", ".")
                    for number in _EPG_SERVICE_NUMBER.findall(name))
    return compact, words, (len(words), numbers, name.count("+"))


def _epg_model_blocks(profile):
    compact, _, signature = profile
    return (signature, 0, compact[:3]), (signature, 1, compact[-3:])


def _epg_word_edit(left, right):
    """Return 2 for an internal gap or swap, 1 for weaker single edits."""
    if len(left) > len(right):
        left, right = right, left
    if len(left) < 4 or len(right) - len(left) > 1:
        return 0
    first = next((index for index, pair in enumerate(zip(left, right)) if pair[0] != pair[1]), len(left))
    if len(left) < len(right):
        if left[first:] != right[first + 1:]:
            return 0
        return 2 if 0 < first < len(left) else 1
    if (left[first:first + 2] == right[first:first + 2][::-1]
            and left[first + 2:] == right[first + 2:]):
        return 2
    return int(left[first + 1:] == right[first + 1:])


def _epg_abbreviation(left, right):
    short, long = sorted((left, right), key=len)
    if not (3 <= len(short) <= 5 and len(long) - len(short) >= 2 and short[0] == long[0]):
        return 0
    if (short.isascii() and long.isascii() and short[-1] == long[-1]
            and short == long.translate(_EPG_MODEL_VOWELS)):
        return 2
    return int(long.startswith(short))


def _epg_model_features(source, target):
    left, left_words, _ = source
    right, right_words, _ = target
    changes = [(a, b) for a, b in zip(left_words, right_words) if a != b]
    edit = _epg_word_edit(*changes[0]) if len(changes) == 1 else 0
    abbreviations = [_epg_abbreviation(a, b) for a, b in changes]
    # Single substitutions and suffix changes may identify another service.
    strong = edit == 2 or (len(left_words) > 1 and abbreviations == [2])
    left_grams = set(zip(left, left[1:]))
    right_grams = set(zip(right, right[1:]))
    return (
        SequenceMatcher(None, left, right, autojunk=False).ratio(),
        2.0 * len(left_grams & right_grams) / (len(left_grams) + len(right_grams)),
        1.0 - len(changes) / len(left_words),
        float(bool(edit)),
        float(0 < len(changes) <= 2 and all(abbreviations)),
        float(strong),
    )


class _EPGNameModel:
    """One bounded candidate index, owned only by the XMLTV refresh worker."""

    def __init__(self, guide_names, cancel_event, deadline, reserve):
        self.deadline = min(time.monotonic() + _EPG_MODEL_SECONDS, deadline - reserve)
        self.buckets = {}
        for index, (xmltv_id, country, label) in enumerate(guide_names):
            if index % 64 == 0:
                _check_limits(cancel_event, deadline)
                if time.monotonic() >= self.deadline:
                    self.buckets.clear()
                    break
            profile = _epg_model_profile(label)
            if profile is None:
                continue
            for block in _epg_model_blocks(profile):
                by_country = self.buckets.setdefault(block, {})
                entries = by_country.setdefault(country, set())
                if entries is not None:
                    entries.add((xmltv_id, country, profile))
                    if len(entries) > _EPG_MODEL_BUCKET_LIMIT:
                        # An incomplete bucket could hide a competing match.
                        by_country[country] = None

    def match(self, label, country):
        if not self.buckets or time.monotonic() >= self.deadline:
            return "", ()
        source = _epg_model_profile(label)
        if source is None:
            return "", ()
        candidates = set()
        for key in _epg_model_blocks(source):
            by_country = self.buckets.get(key, {})
            buckets = by_country.values()
            if country:
                buckets = (by_country.get(country, ()), by_country.get("", ()), by_country.get(None, ()))
            for entries in buckets:
                if entries is None:
                    return "", ()
                candidates.update(entries)
                if len(candidates) > 2 * _EPG_MODEL_BUCKET_LIMIT:
                    return "", ()
        scores = {}
        for xmltv_id, region, profile in candidates:
            if time.monotonic() >= self.deadline:
                return "", ()
            features = _epg_model_features(source, profile)
            score = _EPG_MODEL_BIAS + sum(weight * value for weight, value in zip(_EPG_MODEL_WEIGHTS, features))
            # A name alone needs stronger evidence when the country is unknown.
            cutoff = _EPG_MODEL_ACCEPT
            if not country or country != region:
                cutoff += _EPG_MODEL_UNSCOPED_MARGIN
            eligible = region is not None and score >= cutoff and bool(features[5])
            scores[xmltv_id] = max(scores.get(xmltv_id, (float("-inf"), False)), (score, eligible))
        ranked = sorted(scores, key=lambda xmltv_id: (-scores[xmltv_id][0], xmltv_id))
        if not ranked:
            return "", ()
        best = ranked[0]
        best_score, eligible = scores[best]
        second_score = scores[ranked[1]][0] if len(ranked) > 1 else float("-inf")
        if eligible and best_score - second_score >= _EPG_MODEL_MARGIN:
            return best, ()
        return "", tuple(xmltv_id for xmltv_id in ranked[:4] if scores[xmltv_id][0] >= _EPG_MODEL_SUGGEST)


def _safe_channel_icon_url(value, base_url=""):
    value = value.strip() if isinstance(value, str) else ""
    if (not value or len(value) > MAX_PUBLIC_URL_LENGTH
            or any(ord(character) < 32 or ord(character) == 127 for character in value)):
        return ""
    try:
        url = urljoin(base_url, value) if base_url else value
        return validate_public_url_syntax(url)["url"]
    except (NetworkSecurityError, TypeError, ValueError):
        return ""


def _guide_event_priority(key, now):
    start, stop, _ = key
    if stop <= now:
        return (0, start, stop)
    return (1, -start, -stop)


def _build_snapshot(
    source_id,
    fingerprint,
    xml_path,
    wanted,
    now,
    cancel_event,
    deadline,
    archive_days=None,
    guide_all_dates=False,
    wanted_names=None,
    name_mappings=None,
    icon_base_url="",
    guide_country="",
):
    normalised_wanted = set(normalise_tvg_id(value) for value in wanted)
    explicit_ids = frozenset(wanted)
    guide_country = m3u.epg_country_code(guide_country)
    provider_numbers = _epg_provider_numbers(wanted_names or {}, cancel_event, deadline)
    name_rows = []
    names_index = {}
    for index, (key, entry) in enumerate((wanted_names or {}).items()):
        if index % 128 == 0:
            _check_limits(cancel_event, deadline)
        if not m3u.is_epg_name_key(key) or key in explicit_ids:
            continue
        if isinstance(entry, tuple) and len(entry) in (2, 4):
            label, tvg_id = entry[:2]
            group_country, alternate = entry[2:] if len(entry) == 4 else ("", "")
        else:
            label, tvg_id = entry, ""
            group_country, alternate = "", ""
        if not isinstance(label, str) or not label.strip():
            continue
        label_country, forms, numbers = _epg_name_forms(label)
        regions = {region for region in (label_country, group_country, _epg_id_country(tvg_id)) if region}
        country_conflict = group_country is None or len(regions) > 1
        country = next(iter(regions)) if len(regions) == 1 else ""
        alternate_forms = ()
        if alternate and alternate != m3u.normalise_epg_name(label):
            alternate_country, extra, alternate_numbers = _epg_name_forms(alternate)
            if (not numbers or numbers == alternate_numbers) and (
                    not country or not alternate_country or country == alternate_country):
                alternate_forms = extra
                if not country and not country_conflict:
                    country = alternate_country
        fallback_forms = numbered_forms = ()
        fallback_country = country or guide_country
        if not country_conflict:
            cleaned = _epg_provider_label(label)
            suffix = _EPG_PROVIDER_NUMBER.search(cleaned)
            marker = suffix[1] if suffix and suffix[1] in provider_numbers else ""
            if marker:
                cleaned = cleaned[:suffix.start()]
            clean_country, cleaned_forms, _ = _epg_name_forms(
                cleaned, guide_label=True, provider_label=True
            )
            if not country or not clean_country or country == clean_country:
                fallback_country = clean_country or country or guide_country
                alias_forms = _epg_alias_forms(cleaned_forms, fallback_country)
                fallback_forms = tuple(
                    variant for variant in (cleaned_forms, alias_forms)
                    if variant and variant not in (forms, alternate_forms)
                )
                if marker and fallback_forms:
                    _, numbered, _ = _epg_name_forms(
                        "{} {}".format(cleaned, marker), guide_label=True, provider_label=True
                    )
                    numbered_forms = (numbered, _epg_alias_forms(numbered, fallback_country))
        for variants in (forms, alternate_forms) + fallback_forms + numbered_forms:
            for rank, name in enumerate(variants):
                if name:
                    names_index[(rank, name)] = None
        name_rows.append((
            key, label, tvg_id, country, country_conflict, forms, alternate_forms,
            fallback_country, fallback_forms, numbered_forms,
        ))
    name_mappings = name_mappings if isinstance(name_mappings, dict) else {}
    channels = OrderedDict()
    normalised_channels = defaultdict(set)
    channel_elements = 0
    programme_elements = 0
    live_guide = archive_days is None and not guide_all_dates
    model_labels = [] if names_index and live_guide else None
    first_scan_started = time.monotonic()

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
                    if names_index:
                        id_country = _epg_id_country(xmltv_id)
                        for child_index, child in enumerate(element):
                            if child_index and child_index % 128 == 0:
                                _check_limits(cancel_event, deadline)
                            if dvb_epg._local_name(child.tag) != "display-name":
                                continue
                            country, forms, _ = _epg_name_forms(child.text, guide_label=True)
                            if model_labels is not None and forms[0]:
                                if len(model_labels) >= _EPG_MODEL_GUIDE_LABEL_LIMIT:
                                    # Only use complete candidate sets for automatic matches.
                                    model_labels = None
                                else:
                                    region = country or id_country
                                    if country and id_country and country != id_country:
                                        region = None
                                    model_labels.append((xmltv_id, region, forms[0]))
                            for rank, name in enumerate(forms):
                                candidate = (rank, name)
                                if candidate in names_index:
                                    bucket = names_index[candidate]
                                    if bucket is None:
                                        bucket = names_index[candidate] = set()
                                    bucket.add((xmltv_id, country or id_country))
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

    matched_wanted = {key for key, _ in aliases}
    name_choices = []
    seen_choices = set()
    choice_bytes = 2
    learned_aliases = []
    name_model = None
    model_reserve = max(0.5, time.monotonic() - first_scan_started)
    for index, row in enumerate(name_rows):
        (key, label, tvg_id, country, conflict, forms, alternate,
         fallback_country, fallback, numbered) = row
        if index % 128 == 0:
            _check_limits(cancel_event, deadline)
        if tvg_id in matched_wanted:
            continue
        candidates = _epg_candidates(names_index, forms, country)
        if alternate:
            candidates.update(_epg_candidates(names_index, alternate, country))
        if not candidates and not conflict:
            for variant in fallback:
                candidates = _epg_candidates(names_index, variant, fallback_country)
                if candidates:
                    # Keep a plausible numbered service available for manual selection.
                    for number_variant in numbered:
                        candidates.update(_epg_candidates(names_index, number_variant, fallback_country))
                    break
        if not candidates and country:
            exact = names_index.get((0, forms[0]))
            if exact:
                candidates = {xmltv_id for xmltv_id, _ in exact}
                conflict = True
        chosen = name_mappings.get(key)
        choice_key = key
        if not chosen and name_mappings:
            legacy = m3u.epg_name_key(label, tvg_id)
            legacy_choice = name_mappings.get(legacy)
            legacy_country = _epg_id_country(legacy_choice)
            if (isinstance(legacy_choice, str) and legacy_choice in channels
                    and (not country or not legacy_country or legacy_country == country)):
                chosen, choice_key = legacy_choice, legacy
        learned_choice, suggestions = "", ()
        if (not chosen and not candidates and not conflict and len(forms[0]) >= 6
                and (model_labels or name_model is not None)):
            id_candidates = normalised_channels.get(normalise_tvg_id(tvg_id), ())
            if len(id_candidates) < 2:
                if name_model is None:
                    name_model = _EPGNameModel(model_labels, cancel_event, deadline, model_reserve)
                    model_labels = None
                learned_choice, suggestions = name_model.match(forms[0], country)
                candidates.update(suggestions)
        if choice_key not in seen_choices and (
                len(candidates) > 1 or (candidates and conflict) or chosen or suggestions):
            caption = label
            if country and key != m3u.epg_name_key(label, tvg_id):
                caption = "{} ({})".format(label, country.upper())
            row = [choice_key, _clean_snapshot_text(caption, 180), sorted(candidates)]
            size = _encoded_json_size(row) + 1
            if choice_bytes + size <= MAX_NAME_CHOICES_BYTES:
                name_choices.append(row)
                choice_bytes += size
                seen_choices.add(choice_key)
        if not chosen and not conflict and not suggestions and len(candidates) == 1:
            chosen = next(iter(candidates))
        if isinstance(chosen, str) and chosen in channels:
            aliases.append([key, chosen])
            matched_channels.setdefault(chosen, None)
        elif learned_choice:
            learned_aliases.append([key, learned_choice])
    del name_model, model_labels

    candidate_events = defaultdict(dict)
    overflow_heaps = {}
    invalid_programmes = 0
    retained_events = 0
    retained_text = 0
    moment = int(now)
    earliest = moment - (archive_days * 86400 if archive_days is not None else DEFAULT_PAST_SECONDS)
    latest = moment if archive_days is not None else moment + DEFAULT_FUTURE_SECONDS
    if guide_all_dates:
        earliest, latest = 0, 4102444800
    model_only_ids = {xmltv_id for _, xmltv_id in learned_aliases if xmltv_id not in matched_channels}
    selected_ids = frozenset(matched_channels).union(model_only_ids)
    model_per_channel = min(MAX_EVENTS_PER_CHANNEL, max(2, _EPG_MODEL_EVENT_LIMIT // max(1, len(model_only_ids))))
    model_programmes = 0
    model_events = 0
    model_text = 0
    channel_icons = {}
    icon_bytes = 2
    model_icons = {}
    model_icon_bytes = 0

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
                    if tag == "channel" and live_guide:
                        xmltv_id = element.attrib.get("id", "").strip()
                        from_model = xmltv_id in model_only_ids
                        icons = model_icons if from_model else channel_icons
                        if xmltv_id in selected_ids and xmltv_id not in icons:
                            for child in element:
                                if dvb_epg._local_name(child.tag) != "icon":
                                    continue
                                url = _safe_channel_icon_url(child.attrib.get("src"), icon_base_url)
                                if url:
                                    row = [xmltv_id, url]
                                    size = _encoded_json_size(row) + bool(icons)
                                    if from_model and model_icon_bytes + size <= _EPG_MODEL_ICON_LIMIT:
                                        model_icons[xmltv_id] = row
                                        model_icon_bytes += size
                                    elif not from_model and icon_bytes + size <= MAX_CHANNEL_ICON_BYTES:
                                        channel_icons[xmltv_id] = row
                                        icon_bytes += size
                                    break
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
                    from_model = xmltv_id in model_only_ids
                    if from_model:
                        if model_programmes >= _EPG_MODEL_PROGRAMME_LIMIT:
                            continue
                        model_programmes += 1
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
                    key = (int(start), int(stop), title)
                    if key in events:
                        continue
                    previous = None
                    text_delta = len(title) + len(description)
                    per_channel_limit = model_per_channel if from_model else MAX_EVENTS_PER_CHANNEL
                    if len(events) >= per_channel_limit:
                        if not live_guide:
                            raise M3UEPGSourceError(
                                "XMLTV per-channel event limit exceeded"
                            )
                        # Keep the current and closest upcoming programmes
                        # even when the XMLTV rows are out of order.
                        heap = overflow_heaps.get(xmltv_id)
                        if heap is None:
                            heap = [(_guide_event_priority(row, moment), row) for row in events]
                            heapq.heapify(heap)
                            overflow_heaps[xmltv_id] = heap
                        candidate = (_guide_event_priority(key, moment), key)
                        if candidate <= heap[0]:
                            continue
                        previous = heap[0][1]
                        text_delta -= len(previous[2]) + len(events[previous])
                    if from_model:
                        if ((previous is None and model_events >= _EPG_MODEL_EVENT_LIMIT)
                                or model_text + text_delta > _EPG_MODEL_TEXT_LIMIT):
                            continue
                        model_events += previous is None
                        model_text += text_delta
                    else:
                        retained_events += previous is None
                        if retained_events > MAX_SNAPSHOT_EVENTS:
                            raise M3UEPGSourceError("XMLTV retained event limit exceeded")
                        retained_text += text_delta
                        if retained_text > MAX_SNAPSHOT_TEXT_CHARACTERS:
                            raise M3UEPGSourceError("XMLTV retained text limit exceeded")
                    if previous is not None:
                        heapq.heapreplace(heap, candidate)
                        del events[previous]
                    events[key] = description
            if second_programme_count != programme_elements:
                raise M3UEPGSourceError("XMLTV guide changed while being parsed")
        except dvb_epg.OperationCancelled as error:
            raise M3UEPGCancelled(str(error)) from error
        except dvb_epg.XmltvError as error:
            raise M3UEPGSourceError("external XMLTV guide is invalid") from error

    # A proposed alias needs actual programme data. Existing matches keep
    # priority if extra channels would exceed the snapshot's event or text cap.
    for index, (key, xmltv_id) in enumerate(learned_aliases):
        if index % 128 == 0:
            _check_limits(cancel_event, deadline)
        if len(aliases) >= MAX_WANTED_TVG_IDS:
            break
        events = candidate_events.get(xmltv_id)
        if not events:
            continue
        if xmltv_id not in matched_channels:
            text_size = sum(len(event[2]) + len(description) for event, description in events.items())
            if (retained_events + len(events) > MAX_SNAPSHOT_EVENTS
                    or retained_text + text_size > MAX_SNAPSHOT_TEXT_CHARACTERS):
                continue
            matched_channels[xmltv_id] = None
            retained_events += len(events)
            retained_text += text_size
            row = model_icons.get(xmltv_id)
            if row:
                size = _encoded_json_size(row) + bool(channel_icons)
                if icon_bytes + size <= MAX_CHANNEL_ICON_BYTES:
                    channel_icons[xmltv_id] = row
                    icon_bytes += size
        aliases.append([key, xmltv_id])

    snapshot_channels = []
    event_count = 0
    for channel_index, xmltv_id in enumerate(matched_channels):
        if channel_index % 128 == 0:
            _check_limits(cancel_event, deadline)
        events = candidate_events.get(xmltv_id, {})
        rows = []
        for event_index, (start, stop, title) in enumerate(sorted(events)):
            if event_index % 128 == 0:
                _check_limits(cancel_event, deadline)
            rows.append([start, stop, title, events[(start, stop, title)]])
        event_count += len(rows)
        snapshot_channels.append([xmltv_id, rows])

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
        "scoped_names": True,
        "ambiguous": ambiguous,
        "channels": snapshot_channels,
        "channel_count": channel_elements,
        "programme_count": programme_elements,
        "invalid_programmes": invalid_programmes,
        "matched_ids": len(aliases),
        "event_count": event_count,
    }
    if name_choices:
        payload["name_choices"] = name_choices
    if channel_icons:
        payload["channel_icons"] = list(channel_icons.values())
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
    original = value
    value = unicodedata.normalize("NFC", str(value or ""))
    value = " ".join(value.replace("\r", " ").replace("\n", " ").split())[: int(maximum)]
    return original if isinstance(original, str) and value == original else value


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
    icons = payload.pop("channel_icons", ())
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
        final_size = _fit_channel_icons(payload, icons, original_size, cancel_event, deadline)
        if diagnostics is not None:
            diagnostics["snapshot_compacted"] = 0
            diagnostics["snapshot_final_bytes"] = final_size
        return payload

    if diagnostics is not None:
        diagnostics["snapshot_compacted"] = 1
    compacted, final_size, retained_events, trimmed = _compact_snapshot_to_budget(
        payload,
        now,
        cancel_event,
        deadline,
    )
    final_size = _fit_channel_icons(compacted, icons, final_size, cancel_event, deadline)
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


def _fit_channel_icons(payload, icons, size, cancel_event, deadline):
    """Keep optional logos within the space left by the programme guide."""
    if not icons:
        return size
    with_key = size + len(',"channel_icons":[]')
    selected = []
    for index, row in enumerate(icons):
        if index % 128 == 0:
            _check_limits(cancel_event, deadline)
        row_size = _encoded_json_size(row) + bool(selected)
        if with_key + row_size <= MAX_SNAPSHOT_BYTES:
            selected.append(row)
            with_key += row_size
    if selected:
        payload["channel_icons"] = selected
        return with_key
    return size


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
    if payload.get("scoped_names") is True:
        output["scoped_names"] = True
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
    name_choices = payload.get("name_choices", [])
    if not isinstance(name_choices, list) or _encoded_json_size(name_choices) > MAX_NAME_CHOICES_BYTES:
        raise M3UEPGSnapshotError("M3U EPG name choices exceed the size limit")
    if name_choices:
        seen_names = set()
        for row in name_choices:
            if not isinstance(row, list) or len(row) != 3:
                raise M3UEPGSnapshotError("M3U EPG name choice is invalid")
            key, label, ids = row
            if (not m3u.is_epg_name_key(key) or key in seen_names or not isinstance(label, str)
                    or not 0 < len(label) <= 180 or not label.isprintable()
                    or not isinstance(ids, list) or len(ids) > MAX_XMLTV_CHANNELS):
                raise M3UEPGSnapshotError("M3U EPG name choice is invalid")
            for xmltv_id in ids:
                if not isinstance(xmltv_id, str) or _validated_tvg_id(xmltv_id) != xmltv_id:
                    raise M3UEPGSnapshotError("M3U EPG name choice is invalid")
            seen_names.add(key)
        output["name_choices"] = name_choices
    icons = payload.get("channel_icons")
    if isinstance(icons, list):
        seen_icons = set()
        clean_icons = []
        size = 2
        for icon_index, row in enumerate(icons):
            if icon_index >= MAX_WANTED_TVG_IDS:
                break
            if deadline is not None and icon_index % 128 == 0:
                _check_limits(cancel_event, deadline)
            if not isinstance(row, list) or len(row) != 2 or not isinstance(row[0], str):
                continue
            xmltv_id, raw_url = row
            if xmltv_id not in known_channels or xmltv_id in seen_icons:
                continue
            url = _safe_channel_icon_url(raw_url)
            if url:
                row = [xmltv_id, url]
                row_size = _encoded_json_size(row) + bool(clean_icons)
                if size + row_size <= MAX_CHANNEL_ICON_BYTES:
                    clean_icons.append(row)
                    seen_icons.add(xmltv_id)
                    size += row_size
        if clean_icons:
            output["channel_icons"] = clean_icons
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


def _runtime_snapshot(payload, cancel_event=None, deadline=None, cache=None,
                      cache_dir=None, directory_descriptor=None):
    own_descriptor = None
    if cache is None:
        cache = DecodedChannelCache()
    if directory_descriptor is None and cache_dir is not None:
        try:
            own_descriptor = _open_private_cache_directory(cache_dir, create=True)
            directory_descriptor = own_descriptor
        except (OSError, M3UEPGSnapshotError):
            pass
    def check():
        if deadline is not None or cancel_event is not None:
            _check_limits(cancel_event, float("inf") if deadline is None else deadline)
    try:
        icons = dict(payload.get("channel_icons", ()))
        picon_urls = {}
        picon_bytes = sum(sys.getsizeof(url) for url in icons.values())
        if icons:
            for number, (alias, channel_id) in enumerate(payload["aliases"]):
                if number % 128 == 0:
                    check()
                if channel_id in icons:
                    picon_urls[alias] = icons[channel_id]
                    picon_bytes += sys.getsizeof(alias)
            picon_bytes += sys.getsizeof(picon_urls)
        index = EPGChannelIndex(payload, normalise_tvg_id, cache,
            directory_descriptor=directory_descriptor, check=check)
        return {
            "source_id": payload["source_id"],
            "binding_fingerprint": payload["binding_fingerprint"],
            "cached_at": payload["cached_at"],
            "cache_dir": cache_dir,
            "index": index,
            "scoped_names": index.scoped_names,
            "revision": index.ticket,
            "size_bytes": index.size_bytes + picon_bytes,
            "picon_urls": MappingProxyType(picon_urls) if picon_urls else None,
            "name_choices": tuple((key, name, tuple(ids)) for key, name, ids in payload.get("name_choices", ())),
        }
    finally:
        if own_descriptor is not None:
            os.close(own_descriptor)


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


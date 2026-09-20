# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import errno
import fcntl
import hashlib
import html
import http.client
import ipaddress
import json
import math
import os
import socket
import ssl
import stat
import threading
import time
import unicodedata
import uuid
from collections import OrderedDict
from contextlib import contextmanager
from urllib.parse import urljoin, urlsplit, urlunsplit

from . import PLUGIN_VERSION
from .content import ContentCategory, ContentError, ContentItem
from .i18n import N_, _
from .category_visibility import category_ids_visible, hidden_category_match_ids


M3U_SOURCE_PATH = "/etc/enigma2/gtiptvplayer/m3u-sources.json"
M3U_CACHE_DIR = "/etc/enigma2/gtiptvplayer/m3u-cache"
M3U_REGISTRY_VERSION = 1
# R87 retains catchup declarations.  Older caches must be reparsed once;
# they cannot tell an ordinary live channel from an archive-capable one.
M3U_CACHE_VERSION = 2

MAX_M3U_BYTES = 16 * 1024 * 1024
MAX_M3U_CHANNELS = 20000
MAX_M3U_CATEGORIES = 500
MAX_M3U_LINE_BYTES = 16 * 1024
MAX_M3U_SOURCES = 500
# 500 accepted 4096-character URLs can exceed 8 MiB when every character is
# represented by four UTF-8 bytes plus JSON escaping/metadata.
MAX_REGISTRY_BYTES = 12 * 1024 * 1024
MAX_CACHE_BYTES = 64 * 1024 * 1024
MAX_CACHE_TOTAL_BYTES = 96 * 1024 * 1024
MIN_CACHE_FREE_BYTES = 16 * 1024 * 1024
MAX_CACHE_HEADER_BYTES = 256 * 1024
MAX_CACHE_LINE_BYTES = 32 * 1024
MAX_REMOTE_URL_LENGTH = 4096
MAX_STREAM_URL_LENGTH = 4096
MAX_REMOTE_REDIRECTS = 3
REMOTE_CACHE_FRESH_SECONDS = 12 * 60 * 60
REMOTE_CACHE_STALE_SECONDS = 24 * 60 * 60
DEFAULT_M3U_TIMEOUT = 30
M3U_INITIAL_ITEMS = 36
M3U_MORE_ITEMS = 60
M3U_PAGE_STATE_MAX = 3
READ_CHUNK_BYTES = 64 * 1024
M3U_TRANSACTION_TIMEOUT = 8.0
M3U_SAVE_TIMEOUT = 1.0

LOCAL_M3U_ROOTS = (
    "/etc/enigma2/gtiptvplayer",
    "/media",
    "/mnt",
)
STREAM_SCHEMES = frozenset(("http", "https", "rtsp", "rtmp", "rtp", "udp"))
M3U_CAPABILITIES = frozenset(("live", "favorites", "search"))
_REDIRECT_STATUSES = frozenset((301, 302, 303, 307, 308))
_DNS_SLOTS = threading.BoundedSemaphore(2)
_M3U_CACHE_LOCK = threading.RLock()
_M3U_REGISTRY_LOCK = threading.RLock()
_SOURCE_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)


def _local_root_tuple(roots):
    """Normalize one root or an iterable without widening the allowlist."""
    if roots is None:
        return tuple(LOCAL_M3U_ROOTS)
    if isinstance(roots, (str, os.PathLike)):
        return (os.fspath(roots),)
    return tuple(roots)


class M3USource(object):
    source_type = "m3u"
    capabilities = M3U_CAPABILITIES

    def __init__(self, source_id, name, kind, location):
        source_id = str(source_id or "").strip()
        if not source_id:
            source_id = uuid.uuid4().hex
        if (
            len(source_id) > 64
            or source_id in (".", "..")
            or any(character not in _SOURCE_ID_CHARACTERS for character in source_id)
        ):
            raise ValueError(N_("M3U source ID is invalid."))

        kind = str(kind or "").strip().lower()
        if kind in ("file", "local", "nas"):
            kind = "file"
        elif kind in ("url", "remote", "web"):
            kind = "url"
        else:
            raise ValueError(N_("M3U source type is invalid."))

        name = _single_line(name, 80)
        location = str(location or "").strip()
        if (
            not location
            or any(ord(character) < 32 or ord(character) == 127 for character in location)
        ):
            raise ValueError(N_("M3U source location is invalid."))
        if kind == "file":
            if len(location) > 1024 or not os.path.isabs(location):
                raise ValueError(N_("M3U file path is invalid."))
        else:
            location = _remote_url_info(location)["url"]

        self.source_id = source_id
        self.name = name
        self.kind = kind
        self.location = location

    @property
    def identity(self):
        return ("m3u", self.source_id)

    @property
    def display_name(self):
        return self.name or self.host or _("M3U source")

    @property
    def source_label(self):
        kind = _("FILE") if self.kind == "file" else _("WEB")
        return "M3U • {}".format(kind)

    @property
    def host(self):
        if self.kind == "file":
            return os.path.basename(self.location.rstrip("/")) or _("Local file")
        try:
            return str(urlsplit(self.location).hostname or "")
        except (TypeError, ValueError):
            return ""

    @property
    def display_endpoint(self):
        if self.kind == "file":
            return self.location
        try:
            parsed = urlsplit(self.location)
            port = parsed.port
        except (TypeError, ValueError):
            return self.host
        endpoint = parsed.hostname or ""
        if ":" in endpoint and not endpoint.startswith("["):
            endpoint = "[{}]".format(endpoint)
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        if port and port != default_port:
            endpoint = "{}:{}".format(endpoint, port)
        return "{}://{}".format(parsed.scheme.lower(), endpoint)

    def supports(self, capability):
        return str(capability or "").strip().lower() in self.capabilities

    def to_dict(self):
        return {
            "id": self.source_id,
            "name": self.name,
            "kind": self.kind,
            "location": self.location,
        }

    def __repr__(self):
        return "M3USource(id={!r}, kind={!r})".format(
            self.source_id,
            self.kind,
        )


def load_m3u_sources(path=M3U_SOURCE_PATH):
    if not os.path.lexists(path):
        return []
    body = _read_private_file(path, MAX_REGISTRY_BYTES)
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (TypeError, ValueError, UnicodeError) as error:
        raise ValueError(N_("M3U source file is invalid.")) from error
    if not isinstance(payload, dict) or payload.get("version") != M3U_REGISTRY_VERSION:
        raise ValueError(N_("M3U source file version is not supported."))
    entries = payload.get("sources")
    if not isinstance(entries, list):
        raise ValueError(N_("M3U source file is invalid."))

    sources = []
    seen_ids = set()
    seen_locations = set()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(N_("M3U source file is invalid."))
        if any(
            not isinstance(entry.get(field), str)
            for field in ("id", "name", "kind", "location")
        ):
            raise ValueError(N_("M3U source file is invalid."))
        if not str(entry.get("id") or "").strip():
            raise ValueError(N_("M3U source file is invalid."))
        source = M3USource(
            entry.get("id"),
            entry.get("name"),
            entry.get("kind"),
            entry.get("location"),
        )
        location_key = (source.kind, source.location)
        # A hand-edited registry can contain repeated records.  They remain a
        # single source for counting and future saves instead of consuming
        # capacity or making the other source types unavailable.
        if source.source_id in seen_ids or location_key in seen_locations:
            continue
        seen_ids.add(source.source_id)
        seen_locations.add(location_key)
        sources.append(source)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return sources


def save_m3u_source(
    source,
    path=M3U_SOURCE_PATH,
    cancel_event=None,
    deadline=None,
):
    if not isinstance(source, M3USource):
        raise TypeError(N_("M3U source is invalid."))
    deadline, check_limits = _bounded_transaction_limits(
        cancel_event,
        deadline,
        timeout=M3U_SAVE_TIMEOUT,
    )
    with _registry_transaction_lock(
        path,
        deadline=deadline,
        check_limits=check_limits,
    ):
        sources = load_m3u_sources(path)
        output = []
        replaced = False
        saved_source = source
        for current in sources:
            if current.source_id == source.source_id:
                if not replaced:
                    output.append(source)
                    replaced = True
                continue
            if (current.kind, current.location) == (source.kind, source.location):
                if not replaced:
                    # Keep the stable ID so favorites and cache entries remain
                    # attached when a duplicate location is saved with a new
                    # generated ID.
                    saved_source = M3USource(
                        current.source_id,
                        source.name,
                        source.kind,
                        source.location,
                    )
                    output.append(saved_source)
                    replaced = True
                continue
            output.append(current)
        if not replaced:
            if len(output) >= MAX_M3U_SOURCES:
                raise ValueError(N_("Too many M3U sources are configured."))
            output.append(source)
        check_limits(deadline)
        _write_registry(path, output)
    return saved_source


def delete_m3u_source(
    source_id,
    path=M3U_SOURCE_PATH,
    cache_dir=M3U_CACHE_DIR,
    cancel_event=None,
    deadline=None,
):
    if isinstance(source_id, M3USource):
        source_id = source_id.source_id
    source_id = str(source_id or "").strip()
    deadline, check_limits = _bounded_transaction_limits(
        cancel_event,
        deadline,
    )
    try:
        transaction = _registry_transaction_lock(
            path,
            deadline=deadline,
            check_limits=check_limits,
        )
        transaction.__enter__()
    except (OSError, TypeError, ValueError, NotImplementedError) as error:
        raise OSError(N_("M3U source cache could not be deleted.")) from error
    try:
        sources = load_m3u_sources(path)
        output = [source for source in sources if source.source_id != source_id]
        if len(output) == len(sources):
            return False
        cache_dir = str(cache_dir or "")
        if cache_dir and os.path.lexists(cache_dir):
            try:
                with _cache_directory_lock(
                    cache_dir,
                    create=False,
                    deadline=deadline,
                    check_limits=check_limits,
                ) as directory_descriptor:
                    _delete_m3u_cache_locked(
                        source_id,
                        directory_descriptor,
                        missing_ok=True,
                    )
                    _write_registry(path, output)
            except (OSError, TypeError, ValueError, NotImplementedError) as error:
                raise OSError(N_("M3U source cache could not be deleted.")) from error
        else:
            check_limits(deadline)
            _write_registry(path, output)
    finally:
        transaction.__exit__(None, None, None)
    return True


def delete_m3u_cache(
    source_id,
    cache_dir=M3U_CACHE_DIR,
    cancel_event=None,
    deadline=None,
):
    source_id = str(source_id or "").strip()
    if (
        not source_id
        or len(source_id) > 64
        or source_id in (".", "..")
        or any(character not in _SOURCE_ID_CHARACTERS for character in source_id)
        or not cache_dir
        or not os.path.isabs(str(cache_dir))
    ):
        return False
    deadline, check_limits = _bounded_transaction_limits(
        cancel_event,
        deadline,
    )
    try:
        with _cache_directory_lock(
            str(cache_dir),
            create=False,
            deadline=deadline,
            check_limits=check_limits,
        ) as directory_descriptor:
            return _delete_m3u_cache_locked(
                source_id,
                directory_descriptor,
                missing_ok=False,
            )
    except (OSError, TypeError, NotImplementedError):
        return False


def _valid_source_id(source_id):
    return bool(
        source_id
        and len(source_id) <= 64
        and source_id not in (".", "..")
        and all(character in _SOURCE_ID_CHARACTERS for character in source_id)
    )


def _bounded_transaction_limits(
    cancel_event=None,
    deadline=None,
    timeout=M3U_TRANSACTION_TIMEOUT,
):
    clock = time.monotonic
    now = clock()
    try:
        deadline = float(deadline)
    except (TypeError, ValueError, OverflowError):
        deadline = now + max(0.1, float(timeout))
    if not math.isfinite(deadline):
        deadline = now + max(0.1, float(timeout))

    def check_limits(unused_deadline):
        del unused_deadline
        if cancel_event is not None and cancel_event.is_set():
            raise OSError(N_("Request cancelled"))
        if clock() >= deadline:
            raise OSError(N_("Request timed out"))

    return deadline, check_limits


@contextmanager
def _directory_file_lock(
    directory,
    create,
    lock_filename,
    process_lock,
    deadline=None,
    check_limits=None,
):
    if not directory or not os.path.isabs(str(directory)):
        raise OSError("invalid lock directory")
    directory = str(directory)
    lock_acquired = False
    if check_limits is None:
        process_lock.acquire()
        lock_acquired = True
    else:
        while not lock_acquired:
            check_limits(deadline)
            lock_acquired = process_lock.acquire(timeout=0.05)
    try:
        if create:
            _ensure_private_directory(directory)
        listed = os.lstat(directory)
        if stat.S_ISLNK(listed.st_mode) or not stat.S_ISDIR(listed.st_mode):
            raise OSError("unsafe lock directory")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        directory_descriptor = os.open(directory, flags)
        lock_descriptor = None
        try:
            opened = os.fstat(directory_descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or opened.st_dev != listed.st_dev
                or opened.st_ino != listed.st_ino
            ):
                raise OSError("unsafe lock directory")
            lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            lock_flags |= getattr(os, "O_NOFOLLOW", 0)
            lock_descriptor = os.open(
                lock_filename,
                lock_flags,
                0o600,
                dir_fd=directory_descriptor,
            )
            if not stat.S_ISREG(os.fstat(lock_descriptor).st_mode):
                raise OSError("unsafe lock file")
            os.fchmod(lock_descriptor, 0o600)
            if check_limits is None:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX)
            else:
                while True:
                    check_limits(deadline)
                    try:
                        fcntl.flock(
                            lock_descriptor,
                            fcntl.LOCK_EX | fcntl.LOCK_NB,
                        )
                        break
                    except OSError as error:
                        if error.errno not in (errno.EACCES, errno.EAGAIN):
                            raise
                        time.sleep(0.05)
                check_limits(deadline)
            yield directory_descriptor
        finally:
            if lock_descriptor is not None:
                try:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(lock_descriptor)
            os.close(directory_descriptor)
    finally:
        if lock_acquired:
            process_lock.release()


@contextmanager
def _cache_directory_lock(
    cache_dir,
    create,
    deadline=None,
    check_limits=None,
):
    with _directory_file_lock(
        cache_dir,
        create,
        ".m3u-cache.lock",
        _M3U_CACHE_LOCK,
        deadline=deadline,
        check_limits=check_limits,
    ) as directory_descriptor:
        yield directory_descriptor


@contextmanager
def _registry_transaction_lock(
    registry_path,
    deadline=None,
    check_limits=None,
):
    registry_path = str(registry_path or "")
    if not registry_path or not os.path.isabs(registry_path):
        raise OSError("invalid registry path")
    directory = os.path.dirname(registry_path) or "/"
    with _directory_file_lock(
        directory,
        True,
        ".m3u-sources.lock",
        _M3U_REGISTRY_LOCK,
        deadline=deadline,
        check_limits=check_limits,
    ):
        yield


@contextmanager
def _no_transaction_lock():
    yield


def _owned_staged_cache_name(source_id, filename):
    prefix = ".{}.jsonl.".format(source_id)
    suffix = ".tmp"
    if not filename.startswith(prefix) or not filename.endswith(suffix):
        return False
    identity = filename[len(prefix) : -len(suffix)]
    process_id, separator, nonce = identity.partition(".")
    return bool(
        separator
        and process_id.isdigit()
        and len(nonce) == 32
        and all(character in "0123456789abcdef" for character in nonce)
    )


def _delete_m3u_cache_locked(source_id, directory_descriptor, missing_ok):
    if not _valid_source_id(source_id):
        raise OSError("invalid cache target")
    filename = source_id + ".jsonl"
    removed = False
    try:
        details = os.stat(
            filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        details = None
    if details is not None:
        if not (stat.S_ISREG(details.st_mode) or stat.S_ISLNK(details.st_mode)):
            raise OSError("unsafe cache target")
        os.unlink(filename, dir_fd=directory_descriptor)
        try:
            os.stat(filename, dir_fd=directory_descriptor, follow_symlinks=False)
        except FileNotFoundError:
            removed = True
        else:
            raise OSError("cache target still exists")

    for candidate in os.listdir(directory_descriptor):
        if not _owned_staged_cache_name(source_id, candidate):
            continue
        try:
            staged = os.stat(
                candidate,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            continue
        if not (stat.S_ISREG(staged.st_mode) or stat.S_ISLNK(staged.st_mode)):
            continue
        os.unlink(candidate, dir_fd=directory_descriptor)
        try:
            os.stat(
                candidate,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            removed = True
        else:
            raise OSError("cache staging target still exists")
    return bool(removed or missing_ok)


def _write_registry(path, sources):
    payload = {
        "version": M3U_REGISTRY_VERSION,
        "sources": [source.to_dict() for source in sources],
    }
    body = (json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
    encoded = body.encode("utf-8")
    if len(encoded) > MAX_REGISTRY_BYTES:
        raise ValueError(N_("M3U source file is too large."))
    _atomic_write(path, encoded, 0o600)


class M3UContentClient(object):
    source_type = "m3u"

    def __init__(
        self,
        source,
        timeout=DEFAULT_M3U_TIMEOUT,
        cache_dir=M3U_CACHE_DIR,
        fetcher=None,
        resolver=None,
        clock=None,
        wall_clock=None,
        local_roots=None,
        registry_path=M3U_SOURCE_PATH,
        epg_store=None,
    ):
        if not isinstance(source, M3USource):
            raise TypeError(N_("M3U source is invalid."))
        try:
            timeout = float(timeout)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError(N_("M3U timeout is invalid.")) from error
        if timeout <= 0 or not math.isfinite(timeout):
            raise ValueError(N_("M3U timeout is invalid."))

        self.account = source
        self.timeout = timeout
        self.cache_dir = str(cache_dir or "")
        self.fetcher = fetcher
        self.resolver = resolver or socket.getaddrinfo
        self.clock = clock or time.monotonic
        self.wall_clock = wall_clock or time.time
        self.local_roots = _local_root_tuple(local_roots)
        self.registry_path = str(registry_path or "")
        if epg_store is None:
            from .m3u_epg import get_default_store

            epg_store = get_default_store()
        self._epg_store = epg_store
        self._request_context = threading.local()
        self._prepare_lock = threading.Lock()
        self._state_lock = threading.RLock()
        self._records = []
        self._record_by_id = {}
        self._category_rows = OrderedDict()
        self._categories = []
        self._page_states = OrderedDict()
        self._diagnostics = _empty_diagnostics(source)
        self._prepared = False
        self._force_refresh = False
        # Explicit refresh may race a worker which is already parsing or
        # downloading the catalogue.  Fence publication so that older work can
        # never undo the refresh request and mark itself prepared afterwards.
        self._catalog_generation = 0

    @property
    def capabilities(self):
        return self.account.capabilities

    @property
    def source_label(self):
        return self.account.source_label

    def supports(self, capability):
        return self.account.supports(capability)

    @contextmanager
    def request_scope(self, cancel_event=None, deadline=None, timeout=None):
        if deadline is not None:
            try:
                deadline = float(deadline)
            except (TypeError, ValueError, OverflowError):
                deadline = self.clock()
            if not math.isfinite(deadline):
                deadline = self.clock()
        if timeout is not None:
            try:
                timeout = max(0.25, float(timeout))
            except (TypeError, ValueError, OverflowError):
                timeout = None
            if timeout is not None and not math.isfinite(timeout):
                timeout = None
        previous = getattr(self._request_context, "limits", None)
        if previous is not None:
            old_event, old_deadline, old_timeout = previous
            if cancel_event is None:
                cancel_event = old_event
            if old_deadline is not None:
                deadline = (
                    old_deadline
                    if deadline is None
                    else min(float(deadline), float(old_deadline))
                )
            if timeout is None:
                timeout = old_timeout
            elif old_timeout is not None:
                timeout = min(timeout, float(old_timeout))
        self._request_context.limits = (cancel_event, deadline, timeout)
        try:
            yield self
        finally:
            if previous is None:
                try:
                    del self._request_context.limits
                except AttributeError:
                    pass
            else:
                self._request_context.limits = previous

    def prepare(self, allow_stale_deadline=False):
        with self._state_lock:
            if self._prepared and not self._force_refresh:
                return dict(self._diagnostics)

        deadline = self._operation_deadline()
        self._acquire_prepare_lock(deadline)
        try:
            while True:
                with self._state_lock:
                    if self._prepared and not self._force_refresh:
                        return dict(self._diagnostics)
                    force_refresh = self._force_refresh
                    generation = self._catalog_generation

                cache = self._load_cache(deadline)
                if self.account.kind == "file":
                    catalog, diagnostic = self._prepare_local(
                        cache,
                        force_refresh,
                        deadline,
                    )
                else:
                    catalog, diagnostic = self._prepare_remote(
                        cache,
                        force_refresh,
                        deadline,
                        allow_stale_deadline=allow_stale_deadline,
                    )
                if allow_stale_deadline and diagnostic.get("stale"):
                    # Health callers need only the diagnostic.  When the forced
                    # network probe consumed its deadline, avoid rebuilding the
                    # in-memory browser catalogue after that deadline has passed.
                    return dict(diagnostic)
                if self._install_catalog(
                    catalog,
                    diagnostic,
                    deadline,
                    expected_generation=generation,
                ):
                    return dict(diagnostic)
                # An explicit refresh landed while the source was being read.
                # Keep ownership of the single prepare slot and satisfy that
                # newer generation rather than publishing the obsolete result.
                self._check_limits(deadline)
        finally:
            self._prepare_lock.release()

    def invalidate_cache(self, content_type=None, category_id=None):
        content_type = str(content_type or "").strip().lower()
        if content_type and content_type != "live":
            return
        with self._state_lock:
            self._catalog_generation += 1
            self._prepared = False
            self._force_refresh = True
            self._records = []
            self._record_by_id = {}
            self._category_rows = OrderedDict()
            self._categories = []
            self._page_states = OrderedDict()

    def load_categories(self, content_type):
        self._validate_live(content_type)
        self.prepare()
        with self._state_lock:
            return list(self._categories)

    def load_items(self, content_type, category_id=""):
        self._validate_live(content_type)
        category_id = str(category_id or "")
        self.prepare()
        with self._state_lock:
            state = self._page_states.get(category_id)
            if state is None:
                rows = self._category_rows.get(category_id)
                if rows is None:
                    return []
                end = min(M3U_INITIAL_ITEMS, len(rows))
                values = [self._content_item(self._records[index]) for index in rows[:end]]
                state = {
                    "cursor": end,
                    "values": values,
                    "complete": end >= len(rows),
                }
                self._page_states[category_id] = state
                while len(self._page_states) > M3U_PAGE_STATE_MAX:
                    self._page_states.popitem(last=False)
            else:
                self._page_states.move_to_end(category_id)
            return list(state["values"])

    def load_more_items(self, content_type, category_id=""):
        self._validate_live(content_type)
        category_id = str(category_id or "")
        self.load_items("live", category_id)
        with self._state_lock:
            state = self._page_states.get(category_id)
            rows = self._category_rows.get(category_id)
            if state is None or rows is None or state["complete"]:
                return []
            start = state["cursor"]
            end = min(start + M3U_MORE_ITEMS, len(rows))
            added = [self._content_item(self._records[index]) for index in rows[start:end]]
            state["values"].extend(added)
            state["cursor"] = end
            state["complete"] = end >= len(rows)
            self._page_states.move_to_end(category_id)
            return added

    def epg_channel_ids(self):
        """Return prepared ``tvg-id`` values without loading the M3U source.

        The external XMLTV scheduler calls this only after ``prepare()`` has
        completed in its own worker.  Keeping this accessor cache-only avoids
        creating a second, hidden playlist request from EPG code.
        """
        with self._state_lock:
            if not self._prepared:
                return ()
            values = {
                str(record.get("tvg_id", "") or "").strip()
                for record in self._records
            }
        values.discard("")
        return tuple(sorted(values))

    def load_epg(self, stream_id, limit=2):
        """Read current/next XMLTV events from the published RAM snapshot.

        This method is used by both the channel-list detail worker and the
        full-screen completion path.  It must therefore remain free of file,
        XML and network access.  Snapshot loading and refresh are owned by the
        session scheduler, never by navigation or zapping.
        """
        stream_id = str(stream_id or "")
        try:
            limit = max(1, min(12, int(limit)))
        except (TypeError, ValueError, OverflowError):
            limit = 2
        with self._state_lock:
            record = self._record_by_id.get(stream_id)
            if record is None:
                return []
            tvg_id = str(record.get("tvg_id", "") or "").strip()
        if not tvg_id or self._epg_store is None:
            return []
        try:
            events = self._epg_store.lookup(
                self.account.source_id,
                tvg_id,
                now=self.wall_clock(),
            )
        except Exception:
            return []
        return [event for event in (events or ()) if event is not None][:limit]

    def has_more_items(self, content_type, category_id=""):
        self._validate_live(content_type)
        category_id = str(category_id or "")
        with self._state_lock:
            state = self._page_states.get(category_id)
            return bool(state is not None and not state.get("complete", True))

    def playback_url(self, item):
        if not isinstance(item, ContentItem) or item.content_type != "live":
            raise ContentError(
                N_("Invalid playback item"),
                ContentError.INVALID_PLAYBACK_ITEM,
            )
        stream_id = str(item.stream_id or "")
        if not _valid_stream_id(stream_id):
            raise ContentError(
                N_("Invalid playback item"),
                ContentError.INVALID_PLAYBACK_ITEM,
            )
        with self._state_lock:
            record = self._record_by_id.get(stream_id)
            if record is None:
                raise ContentError(
                    N_("Channel is no longer present in the M3U source."),
                    ContentError.PLAYBACK_URL,
                )
            return record["url"]

    def resolve_items(self, values):
        """Resolve saved M3U IDs against the current catalogue on a worker."""
        self.prepare()
        resolved = []
        deadline = self._operation_deadline()
        with self._state_lock:
            for index, value in enumerate(values or ()):
                if index % 128 == 0:
                    self._check_limits(deadline)
                stream_id = str(getattr(value, "stream_id", value) or "")
                record = self._record_by_id.get(stream_id)
                if record is not None:
                    resolved.append(self._content_item(record))
        return resolved

    def search_items(self, query, limit=200, hidden_category_ids=None):
        query = _search_text(query)[:180]
        if not query:
            return []
        try:
            limit = int(limit)
        except (TypeError, ValueError, OverflowError):
            limit = 200
        limit = max(1, min(500, limit))
        hidden_category_ids = hidden_category_match_ids((), hidden_category_ids)
        self.prepare()
        matches = []
        deadline = self._operation_deadline()
        with self._state_lock:
            # Catalogue lists are replaced, never mutated.  Pin this immutable
            # generation briefly and release the state lock before the bounded
            # Unicode scan so playback and EPG lookups remain responsive.
            records = self._records
        for index, record in enumerate(records):
            if index % 128 == 0:
                self._check_limits(deadline)
            if not category_ids_visible(
                (record["category_id"],), hidden_category_ids
            ):
                continue
            searchable = _search_text(
                "{} {} {}".format(
                    record["name"],
                    record["group"],
                    record["tvg_id"],
                )
            )
            if query in searchable:
                matches.append(self._content_item(record))
                if len(matches) >= limit:
                    break
        return matches

    def picon_request_context(self, url=""):
        return {}

    def catalog_diagnostics(self):
        with self._state_lock:
            return dict(self._diagnostics)

    def source_health(self, allow_stale_deadline=False):
        try:
            return self.prepare(
                allow_stale_deadline=bool(allow_stale_deadline),
            )
        except ContentError as error:
            result = _empty_diagnostics(self.account)
            result["status"] = "error"
            result["reason"] = str(error)
            result["reason_code"] = error.reason
            return result

    health = source_health

    def _validate_live(self, content_type):
        if str(content_type or "").strip().lower() != "live":
            raise ContentError(
                N_("This M3U source supports Live TV only."),
                ContentError.UNSUPPORTED_TYPE,
            )

    def _request_limits(self):
        return getattr(self._request_context, "limits", (None, None, None))

    def _operation_deadline(self):
        unused_event, scoped_deadline, scoped_timeout = self._request_limits()
        if scoped_deadline is not None:
            return float(scoped_deadline)
        timeout = scoped_timeout if scoped_timeout is not None else self.timeout
        return self.clock() + max(0.25, float(timeout))

    def _check_limits(self, deadline):
        cancel_event, scoped_deadline, unused_timeout = self._request_limits()
        if cancel_event is not None and cancel_event.is_set():
            raise ContentError(N_("Request cancelled"), ContentError.CANCELLED)
        effective = deadline
        if scoped_deadline is not None:
            effective = min(float(effective), float(scoped_deadline))
        if self.clock() >= effective:
            raise ContentError(N_("Request timed out"), ContentError.DEADLINE)

    def _remaining_timeout(self, deadline):
        self._check_limits(deadline)
        unused_event, unused_deadline, scoped_timeout = self._request_limits()
        timeout = scoped_timeout if scoped_timeout is not None else self.timeout
        remaining = float(deadline) - self.clock()
        if remaining <= 0:
            raise ContentError(N_("Request timed out"), ContentError.DEADLINE)
        return min(float(timeout), remaining)

    def _acquire_prepare_lock(self, deadline):
        while True:
            self._check_limits(deadline)
            if self._prepare_lock.acquire(timeout=min(0.10, self._remaining_timeout(deadline))):
                return

    def _prepare_local(self, cache, force_refresh, deadline):
        path = validate_local_m3u_path(
            self.account.location,
            self.local_roots,
        )
        fingerprint = _local_fingerprint(path)
        if (
            cache is not None
            and not force_refresh
            and cache["meta"].get("version") == M3U_CACHE_VERSION
            and cache["meta"].get("fingerprint") == fingerprint
        ):
            diagnostic = self._diagnostic_for_cache(cache, "cache", False)
            return cache, diagnostic

        started = self.clock()
        data, fingerprint = _read_local_m3u(
            path,
            deadline,
            self._check_limits,
        )
        catalog = _parse_m3u(data, self.account, deadline, self._check_limits)
        catalog["meta"].update(
            {
                "cached_at": self.wall_clock(),
                "fingerprint": fingerprint,
                "size_bytes": len(data),
            }
        )
        self._save_cache(catalog, deadline)
        diagnostic = self._diagnostic_for_catalog(
            catalog,
            "source",
            False,
            int(max(0.0, self.clock() - started) * 1000),
        )
        return catalog, diagnostic

    def _prepare_remote(
        self,
        cache,
        force_refresh,
        deadline,
        allow_stale_deadline=False,
    ):
        now = self.wall_clock()
        cache_age = _cache_age(cache, now)
        if (
            cache is not None
            and not force_refresh
            and cache["meta"].get("version") == M3U_CACHE_VERSION
            and cache_age is not None
            and cache_age < REMOTE_CACHE_FRESH_SECONDS
        ):
            diagnostic = self._diagnostic_for_cache(cache, "cache", False)
            return cache, diagnostic

        started = self.clock()
        try:
            data = self._fetch_remote(deadline)
            catalog = _parse_m3u(data, self.account, deadline, self._check_limits)
        except ContentError as error:
            if error.reason == ContentError.CANCELLED:
                raise
            if (
                error.reason == ContentError.DEADLINE
                and not allow_stale_deadline
            ):
                raise
            if (
                cache is not None
                and cache_age is not None
                and cache_age <= REMOTE_CACHE_STALE_SECONDS
            ):
                diagnostic = self._diagnostic_for_cache(cache, "stale-cache", True)
                return cache, diagnostic
            raise

        catalog["meta"].update(
            {
                "cached_at": now,
                "fingerprint": {
                    "sha256": hashlib.sha256(data).hexdigest(),
                    "size": len(data),
                },
                "size_bytes": len(data),
            }
        )
        self._save_cache(catalog, deadline)
        diagnostic = self._diagnostic_for_catalog(
            catalog,
            "source",
            False,
            int(max(0.0, self.clock() - started) * 1000),
        )
        return catalog, diagnostic

    def _fetch_remote(self, deadline):
        self._check_limits(deadline)
        timeout = self._remaining_timeout(deadline)
        if self.fetcher is not None:
            try:
                result = self.fetcher(
                    self.account.location,
                    maximum=MAX_M3U_BYTES,
                    timeout=timeout,
                    max_redirects=MAX_REMOTE_REDIRECTS,
                )
            except ContentError:
                raise
            except Exception as error:
                raise ContentError(
                    N_("Could not load the M3U source."),
                    ContentError.CONNECTION,
                ) from error
            data = result[0] if isinstance(result, tuple) else result
        else:
            data = _fetch_remote_m3u(
                self.account.location,
                MAX_M3U_BYTES,
                timeout,
                deadline,
                self._check_limits,
                self.resolver,
                self._remaining_timeout,
            )
        self._check_limits(deadline)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, (bytes, bytearray)):
            raise ContentError(
                N_("M3U source returned an invalid response."),
                ContentError.INVALID_RESPONSE,
            )
        if len(data) > MAX_M3U_BYTES:
            raise ContentError(
                N_("M3U source exceeds the safe size limit."),
                ContentError.RESPONSE_TOO_LARGE,
            )
        return data

    def _cache_path(self):
        if not self.cache_dir:
            return ""
        return os.path.join(self.cache_dir, self.account.source_id + ".jsonl")

    def _load_cache(self, deadline):
        path = self._cache_path()
        if not path or not os.path.exists(path):
            return None
        try:
            return _read_catalog_cache(
                path,
                self.account,
                deadline,
                self._check_limits,
            )
        except ContentError as error:
            if error.reason in (ContentError.CANCELLED, ContentError.DEADLINE):
                raise
            return None
        except (OSError, ValueError):
            return None

    def _save_cache(self, catalog, deadline):
        path = self._cache_path()
        if not path:
            return
        self._check_limits(deadline)
        try:
            _write_catalog_cache(
                path,
                self.account,
                catalog,
                deadline,
                self._check_limits,
                registry_path=self.registry_path,
            )
        except (OSError, ValueError):
            return
        self._check_limits(deadline)

    def _install_catalog(
        self,
        catalog,
        diagnostic,
        deadline,
        expected_generation=None,
    ):
        with self._state_lock:
            if (
                expected_generation is not None
                and expected_generation != self._catalog_generation
            ):
                return False
        records = list(catalog.get("records") or [])
        category_rows = OrderedDict()
        category_rows[""] = list(range(len(records)))
        rows_by_id = {}
        for index, record in enumerate(records):
            if index % 256 == 0:
                self._check_limits(deadline)
            rows_by_id.setdefault(record["category_id"], []).append(index)
        categories = [ContentCategory("", _("All Channels"))]
        for category in catalog.get("categories") or []:
            category_id = category["id"]
            rows = rows_by_id.get(category_id) or []
            if not rows:
                continue
            category_rows[category_id] = rows
            categories.append(
                ContentCategory(
                    category_id,
                    category["name"] or _("Uncategorized"),
                )
            )
        self._check_limits(deadline)
        with self._state_lock:
            if (
                expected_generation is not None
                and expected_generation != self._catalog_generation
            ):
                return False
            self._records = records
            self._record_by_id = {
                record["stream_id"]: record for record in records
            }
            self._category_rows = category_rows
            self._categories = categories
            self._page_states = OrderedDict()
            self._diagnostics = dict(diagnostic)
            self._prepared = True
            self._force_refresh = False
            return True

    def _content_item(self, record):
        item = ContentItem(
            "live",
            record["stream_id"],
            record["name"],
            category_id=record["category_id"],
            extension=record["extension"],
            icon=record["icon"],
        )
        item.tvg_id = record["tvg_id"]
        item.group_title = record["group"]
        return item

    def _diagnostic_for_cache(self, catalog, cache_state, stale):
        return self._diagnostic_for_catalog(catalog, cache_state, stale, 0)

    def _diagnostic_for_catalog(self, catalog, cache_state, stale, latency_ms):
        meta = catalog.get("meta") or {}
        return {
            "status": "stale" if stale else "online",
            "source_type": "m3u",
            "kind": self.account.kind,
            "endpoint": self.account.display_endpoint,
            "channels": len(catalog.get("records") or []),
            "categories": len(catalog.get("categories") or []),
            "cache_state": cache_state,
            "stale": bool(stale),
            "cached_at": float(meta.get("cached_at", 0) or 0),
            "size_bytes": int(meta.get("size_bytes", 0) or 0),
            "skipped_entries": int(meta.get("skipped_entries", 0) or 0),
            "latency_ms": max(0, int(latency_ms or 0)),
            "reason": "",
        }


def _empty_diagnostics(source):
    return {
        "status": "not-loaded",
        "source_type": "m3u",
        "kind": source.kind,
        "endpoint": source.display_endpoint,
        "channels": 0,
        "categories": 0,
        "cache_state": "none",
        "stale": False,
        "cached_at": 0.0,
        "size_bytes": 0,
        "skipped_entries": 0,
        "latency_ms": 0,
        "reason": "",
        "reason_code": "",
    }


def _parse_m3u(data, source, deadline, check_limits):
    if not isinstance(data, (bytes, bytearray)):
        raise ContentError(
            N_("M3U source returned an invalid response."),
            ContentError.INVALID_RESPONSE,
        )
    if len(data) > MAX_M3U_BYTES:
        raise ContentError(
            N_("M3U source exceeds the safe size limit."),
            ContentError.RESPONSE_TOO_LARGE,
        )
    if b"\x00" in data:
        raise ContentError(
            N_("M3U source contains invalid data."),
            ContentError.INVALID_RESPONSE,
        )

    header_seen = False
    archive_defaults = {}
    pending = None
    next_group = ""
    raw_records = []
    skipped = 0
    for index, raw_line in enumerate(_m3u_byte_lines(data)):
        if index % 128 == 0:
            check_limits(deadline)
        if len(raw_line) > MAX_M3U_LINE_BYTES:
            raise ContentError(
                N_("M3U source contains a line that is too long."),
                ContentError.INVALID_RESPONSE,
            )
        if index == 0 and raw_line.startswith(b"\xef\xbb\xbf"):
            raw_line = raw_line[3:]
        line = raw_line.decode("utf-8", "replace")
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper.startswith("#EXT-X-"):
            raise ContentError(
                N_("The selected file is an HLS stream manifest, not a channel list."),
                ContentError.INVALID_RESPONSE,
            )
        if not header_seen:
            if not upper.startswith("#EXTM3U"):
                raise ContentError(
                    N_("M3U source header is missing."),
                    ContentError.INVALID_RESPONSE,
                )
            header_seen = True
            header_attributes = _parse_attributes(stripped[len("#EXTM3U"):])
            if "catchup-correction" in header_attributes:
                archive_defaults["catchup-correction"] = header_attributes["catchup-correction"]
            continue
        if upper.startswith("#EXTINF:"):
            if pending is not None:
                skipped += 1
            pending = _parse_extinf(stripped[len("#EXTINF:") :], archive_defaults)
            if pending is not None and not pending["group"] and next_group:
                pending["group"] = next_group
            next_group = ""
            if pending is None:
                skipped += 1
            continue
        if upper.startswith("#EXTGRP:"):
            group = _single_line(stripped.split(":", 1)[1], 180)
            if pending is not None:
                if not pending["group"]:
                    pending["group"] = group
            else:
                next_group = group
            continue
        if stripped.startswith("#"):
            continue
        if pending is None:
            skipped += 1
            continue
        stream_url = _safe_stream_url(stripped)
        if not stream_url:
            pending = None
            skipped += 1
            continue
        pending["url"] = stream_url
        raw_records.append(pending)
        pending = None
        if len(raw_records) > MAX_M3U_CHANNELS:
            raise ContentError(
                N_("M3U source contains too many channels."),
                ContentError.CATALOG_ITEM_LIMIT,
            )
    check_limits(deadline)
    if not header_seen:
        raise ContentError(
            N_("M3U source header is missing."),
            ContentError.INVALID_RESPONSE,
        )
    if pending is not None:
        skipped += 1

    category_by_group = OrderedDict()
    records = []
    seen_ids = set()
    seen_entries = set()
    seen_tvg_ids = set()
    for index, record in enumerate(raw_records):
        if index % 128 == 0:
            check_limits(deadline)
        group = record["group"]
        group_key = group.casefold() if group else "\x00uncategorized"
        category = category_by_group.get(group_key)
        if category is None:
            if len(category_by_group) >= MAX_M3U_CATEGORIES:
                raise ContentError(
                    N_("M3U source contains too many categories."),
                    ContentError.CATALOG_ITEM_LIMIT,
                )
            category = {
                "id": _category_id(source, group_key),
                "name": group,
            }
            category_by_group[group_key] = category
        record["category_id"] = category["id"]
        entry_key = (
            record["tvg_id"].casefold(),
            record["group"].casefold(),
            record["name"].casefold(),
            record["url"],
        )
        if entry_key in seen_entries:
            skipped += 1
            continue
        seen_entries.add(entry_key)
        tvg_key = record["tvg_id"].casefold()
        first_tvg_occurrence = bool(tvg_key and tvg_key not in seen_tvg_ids)
        if tvg_key:
            seen_tvg_ids.add(tvg_key)
        record["stream_id"] = _stream_id(
            source,
            record,
            first_tvg_occurrence,
        )
        if record["stream_id"] in seen_ids:
            skipped += 1
            continue
        seen_ids.add(record["stream_id"])
        record["extension"] = _stream_extension(record["url"])
        records.append(record)
    check_limits(deadline)
    if not records:
        raise ContentError(
            N_("M3U source does not contain playable channels."),
            ContentError.CONTENT_RESPONSE,
        )
    return {
        "meta": {
            "cached_at": 0.0,
            "fingerprint": {},
            "size_bytes": len(data),
            "skipped_entries": skipped,
        },
        "categories": list(category_by_group.values()),
        "records": records,
    }


def _m3u_byte_lines(data):
    start = 0
    length = len(data)
    separator = b"\n" if b"\n" in data else b"\r"
    while start < length:
        end = data.find(separator, start)
        if end < 0:
            end = length
            following = length
        else:
            following = end + 1
        line = data[start:end]
        if separator == b"\n" and line.endswith(b"\r"):
            line = line[:-1]
        yield line
        start = following


def _parse_extinf(payload, archive_defaults=None):
    attributes_text, display_name = _split_unquoted_comma(payload)
    attributes_text = attributes_text.strip()
    separator = next(
        (
            index
            for index, character in enumerate(attributes_text)
            if character.isspace()
        ),
        -1,
    )
    if separator < 0:
        metadata = ""
    else:
        metadata = attributes_text[separator + 1 :]
    attributes = _parse_attributes(metadata)
    name = _single_line(display_name, 180) or _single_line(
        attributes.get("tvg-name"),
        180,
    )
    if not name:
        return None
    return {
        "name": name,
        "group": _single_line(attributes.get("group-title"), 180),
        "tvg_id": _single_line(attributes.get("tvg-id"), 256),
        "icon": _safe_icon_url(attributes.get("tvg-logo")),
        "url": "",
        "category_id": "",
        "stream_id": "",
        "extension": "ts",
        "catchup": _archive_attributes(dict(archive_defaults or {}, **attributes)),
    }


def _archive_attributes(attributes):
    """Keep bounded declarations verbatim; malformed templates fail closed."""
    if not isinstance(attributes, dict):
        return {}
    result = {}
    for name in ("catchup", "catchup-source", "catchup-days", "catchup-correction", "timeshift"):
        if name not in attributes:
            continue
        value = attributes[name]
        maximum = MAX_STREAM_URL_LENGTH if name == "catchup-source" else 32
        if (not isinstance(value, str) or len(value) > maximum
                or any(ord(character) < 32 or ord(character) == 127 for character in value)):
            return {"catchup": "unsupported"}
        result[name] = value.strip()
    return result


def _split_unquoted_comma(value):
    quote = ""
    escaped = False
    for index, character in enumerate(str(value or "")):
        if escaped:
            escaped = False
            continue
        if character == "\\" and quote:
            escaped = True
            continue
        if character in ("'", '"'):
            if quote == character:
                quote = ""
            elif not quote:
                quote = character
            continue
        if character == "," and not quote:
            return value[:index], value[index + 1 :]
    return value, ""


def _parse_attributes(value):
    text = str(value or "")
    result = {}
    index = 0
    length = len(text)
    while index < length:
        while index < length and text[index].isspace():
            index += 1
        start = index
        while index < length and (
            text[index].isalnum() or text[index] in ("-", "_", ":")
        ):
            index += 1
        if index == start:
            index += 1
            continue
        name = text[start:index].lower()
        while index < length and text[index].isspace():
            index += 1
        if index >= length or text[index] != "=":
            while index < length and not text[index].isspace():
                index += 1
            continue
        index += 1
        while index < length and text[index].isspace():
            index += 1
        if index < length and text[index] in ("'", '"'):
            quote = text[index]
            index += 1
            characters = []
            while index < length:
                character = text[index]
                index += 1
                if character == quote:
                    break
                if character == "\\" and index < length and text[index] == quote:
                    character = text[index]
                    index += 1
                characters.append(character)
            item = "".join(characters)
        else:
            start = index
            while index < length and not text[index].isspace():
                index += 1
            item = text[start:index]
        if name not in result:
            if name.startswith("catchup") or name == "timeshift":
                # Do not truncate a URL template into a different, valid URL.
                result[name] = html.unescape(item)
            else:
                result[name] = html.unescape(item[:1024])
    return result


def _single_line(value, maximum):
    text = html.unescape(str(value or ""))
    text = " ".join(text.replace("\r", " ").replace("\n", " ").split())
    return text[:maximum]


def _search_text(value):
    value = str(value or "").casefold().replace("ı", "i")
    value = unicodedata.normalize("NFKD", value)
    return "".join(
        character for character in value if not unicodedata.combining(character)
    )


def _safe_stream_url(value):
    value = str(value or "").strip()
    if (
        not value
        or len(value) > MAX_STREAM_URL_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return ""
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError):
        return ""
    scheme = str(parsed.scheme or "").lower()
    if scheme not in STREAM_SCHEMES or not parsed.hostname:
        return ""
    if port is not None and not 1 <= int(port) <= 65535:
        return ""
    return urlunsplit((scheme, parsed.netloc, parsed.path, parsed.query, parsed.fragment))


def _safe_icon_url(value):
    value = str(value or "").strip()
    if (
        not value
        or len(value) > 2048
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        return ""
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        return ""
    if (
        parsed.scheme.lower() not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        return ""
    return urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, parsed.path, parsed.query, "")
    )


def _stream_id(source, record, primary_tvg_id):
    if primary_tvg_id:
        values = ("tvg-id", record["tvg_id"].casefold())
    else:
        values = (
            "stream",
            record["group"].casefold(),
            record["name"].casefold(),
            record["url"],
        )
    raw = "\x1f".join((source.source_id,) + values)
    return "m3u:" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _category_id(source, group_key):
    raw = "\x1f".join((source.source_id, "category", group_key))
    return "m3u-category:" + hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _valid_stream_id(value):
    value = str(value or "")
    suffix = value[4:] if value.startswith("m3u:") else ""
    return len(suffix) == 64 and all(character in "0123456789abcdef" for character in suffix)


def _valid_category_id(value):
    value = str(value or "")
    prefix = "m3u-category:"
    suffix = value[len(prefix) :] if value.startswith(prefix) else ""
    return len(suffix) == 64 and all(character in "0123456789abcdef" for character in suffix)


def _stream_extension(url):
    try:
        parsed = urlsplit(url)
    except (TypeError, ValueError):
        return "ts"
    path = str(parsed.path or "").lower()
    if path.endswith(".m3u8"):
        return "m3u8"
    extension = path.rsplit(".", 1)[-1] if "." in path.rsplit("/", 1)[-1] else ""
    if extension and extension.replace("_", "").isalnum() and len(extension) <= 12:
        return extension
    return "ts"


def _validate_local_path(path, roots):
    path = str(path or "")
    if not os.path.isabs(path) or "\x00" in path:
        raise ContentError(
            N_("M3U file path is invalid."),
            ContentError.INVALID_RESPONSE,
        )
    components = path.split(os.sep)
    if any(component in (".", "..") for component in components):
        raise ContentError(
            N_("M3U file path traversal is not allowed."),
            ContentError.ACCESS_DENIED,
        )
    absolute = os.path.abspath(path)
    real = os.path.realpath(absolute)
    if real != absolute:
        raise ContentError(
            N_("Symbolic links cannot be used as M3U sources."),
            ContentError.ACCESS_DENIED,
        )
    allowed = False
    for root in roots:
        root = os.path.realpath(os.path.abspath(str(root)))
        try:
            if os.path.commonpath((real, root)) == root:
                allowed = True
                break
        except ValueError:
            continue
    if not allowed:
        raise ContentError(
            N_("M3U file is outside the allowed directories."),
            ContentError.ACCESS_DENIED,
        )
    try:
        details = os.lstat(absolute)
    except OSError as error:
        raise ContentError(
            N_("M3U file could not be opened."),
            ContentError.CONNECTION,
        ) from error
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise ContentError(
            N_("M3U source must be a regular file."),
            ContentError.ACCESS_DENIED,
        )
    return absolute


def validate_local_m3u_path(path, roots=None):
    """Validate one selected local playlist against the runtime allowlist."""
    return _validate_local_path(path, _local_root_tuple(roots))


def _open_local_regular(path):
    flags = os.O_RDONLY
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        raise ContentError(
            N_("M3U file could not be opened."),
            ContentError.CONNECTION,
        ) from error
    try:
        details = os.fstat(descriptor)
        listed = os.lstat(path)
        if (
            not stat.S_ISREG(details.st_mode)
            or stat.S_ISLNK(listed.st_mode)
            or details.st_dev != listed.st_dev
            or details.st_ino != listed.st_ino
        ):
            raise ContentError(
                N_("M3U source must be a regular file."),
                ContentError.ACCESS_DENIED,
            )
        if details.st_size > MAX_M3U_BYTES:
            raise ContentError(
                N_("M3U source exceeds the safe size limit."),
                ContentError.RESPONSE_TOO_LARGE,
            )
        return descriptor, details
    except Exception:
        os.close(descriptor)
        raise


def _file_fingerprint(details):
    return {
        "dev": int(details.st_dev),
        "ino": int(details.st_ino),
        "size": int(details.st_size),
        "mtime_ns": int(
            getattr(details, "st_mtime_ns", int(details.st_mtime * 1000000000))
        ),
    }


def _local_fingerprint(path):
    descriptor, details = _open_local_regular(path)
    os.close(descriptor)
    return _file_fingerprint(details)


def _read_local_m3u(path, deadline, check_limits):
    descriptor, before = _open_local_regular(path)
    body = bytearray()
    total = 0
    try:
        while True:
            check_limits(deadline)
            chunk = os.read(descriptor, min(READ_CHUNK_BYTES, MAX_M3U_BYTES + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_M3U_BYTES:
                raise ContentError(
                    N_("M3U source exceeds the safe size limit."),
                    ContentError.RESPONSE_TOO_LARGE,
                )
            body.extend(chunk)
        after = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    if _file_fingerprint(before) != _file_fingerprint(after):
        raise ContentError(
            N_("M3U file changed while it was being read."),
            ContentError.INVALID_RESPONSE,
        )
    return body, _file_fingerprint(after)


def _source_location_key(source):
    raw = "\x1f".join((source.kind, source.location))
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _catalog_cache_lines(source, catalog, deadline, check_limits):
    meta = dict(catalog.get("meta") or {})
    header = {
        "type": "meta",
        "version": M3U_CACHE_VERSION,
        "source_id": source.source_id,
        "location_key": _source_location_key(source),
        "cached_at": float(meta.get("cached_at", 0) or 0),
        "fingerprint": meta.get("fingerprint") or {},
        "size_bytes": int(meta.get("size_bytes", 0) or 0),
        "skipped_entries": int(meta.get("skipped_entries", 0) or 0),
        "categories": [
            [category["id"], category["name"]]
            for category in catalog.get("categories") or []
        ],
    }
    yield json.dumps(header, ensure_ascii=False, separators=(",", ":"))
    for index, record in enumerate(catalog.get("records") or []):
        if index % 128 == 0:
            check_limits(deadline)
        yield json.dumps(
            {
                "type": "item",
                "stream_id": record["stream_id"],
                "name": record["name"],
                "group": record["group"],
                "category_id": record["category_id"],
                "tvg_id": record["tvg_id"],
                "icon": record["icon"],
                "url": record["url"],
                "extension": record["extension"],
                "catchup": _archive_attributes(record.get("catchup")),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


def _catalog_cache_size(source, catalog, deadline, check_limits):
    total = 0
    for index, line in enumerate(
        _catalog_cache_lines(source, catalog, deadline, check_limits)
    ):
        if index % 128 == 0:
            check_limits(deadline)
        total += len((str(line) + "\n").encode("utf-8"))
        if total > MAX_CACHE_BYTES:
            raise ValueError("cache is too large")
    check_limits(deadline)
    return total


def _owned_cache_name(filename):
    if not filename.endswith(".jsonl"):
        return False
    return _valid_source_id(filename[:-6])


def _cache_budget_plan(
    directory_descriptor,
    current_filename,
    serialized_size,
):
    candidates = []
    aggregate = 0
    current_size = 0
    for filename in os.listdir(directory_descriptor):
        if not _owned_cache_name(filename):
            continue
        try:
            details = os.stat(
                filename,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            continue
        if not stat.S_ISREG(details.st_mode):
            continue
        size = max(0, int(details.st_size))
        aggregate += size
        if filename == current_filename:
            current_size = size
            continue
        candidates.append(
            (
                int(
                    getattr(
                        details,
                        "st_mtime_ns",
                        int(details.st_mtime * 1000000000),
                    )
                ),
                filename,
                details,
            )
        )

    filesystem = os.fstatvfs(directory_descriptor)
    block_size = int(filesystem.f_frsize or filesystem.f_bsize or 1)
    available = max(0, int(filesystem.f_bavail)) * block_size
    projected_aggregate = aggregate - current_size + serialized_size
    if available < MIN_CACHE_FREE_BYTES:
        raise OSError("cache free-space reserve would be exhausted")
    candidates.sort(key=lambda entry: (entry[0], entry[1]))
    removals = []
    for unused_mtime, filename, before in candidates:
        if projected_aggregate <= MAX_CACHE_TOTAL_BYTES:
            break
        removals.append((filename, before))
        freed = max(0, int(before.st_size))
        projected_aggregate -= freed

    if projected_aggregate > MAX_CACHE_TOTAL_BYTES:
        raise OSError("aggregate cache quota exceeded")
    return removals


def _cache_stage_has_space(directory_descriptor, serialized_size):
    filesystem = os.fstatvfs(directory_descriptor)
    block_size = int(filesystem.f_frsize or filesystem.f_bsize or 1)
    available = max(0, int(filesystem.f_bavail)) * block_size
    if available < serialized_size + MIN_CACHE_FREE_BYTES:
        raise OSError("cache free-space reserve would be exhausted")


def _stage_catalog_cache_locked(
    directory_descriptor,
    filename,
    lines,
    maximum,
    deadline,
    check_limits,
):
    filesystem = os.fstatvfs(directory_descriptor)
    block_size = int(filesystem.f_frsize or filesystem.f_bsize or 1)
    available = max(0, int(filesystem.f_bavail)) * block_size
    if available < MIN_CACHE_FREE_BYTES:
        raise OSError("cache free-space reserve would be exhausted")
    stage_budget = available - MIN_CACHE_FREE_BYTES
    temporary = ".{}.{}.{}.tmp".format(
        filename,
        os.getpid(),
        uuid.uuid4().hex,
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(
        temporary,
        flags,
        0o600,
        dir_fd=directory_descriptor,
    )
    total = 0
    try:
        for index, line in enumerate(lines):
            if index % 128 == 0:
                check_limits(deadline)
            encoded = (str(line) + "\n").encode("utf-8")
            total += len(encoded)
            if total > maximum:
                raise ValueError("cache is too large")
            if total > stage_budget:
                raise OSError("cache free-space reserve would be exhausted")
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        check_limits(deadline)
        details = os.fstat(descriptor)
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
        os.fsync(directory_descriptor)
    except OSError:
        pass
    return temporary, details, total


def _matching_staged_cache(
    directory_descriptor,
    staged_name,
    staged_details,
    serialized_size,
):
    current = os.stat(
        staged_name,
        dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    return bool(
        stat.S_ISREG(current.st_mode)
        and current.st_dev == staged_details.st_dev
        and current.st_ino == staged_details.st_ino
        and current.st_size == staged_details.st_size
        and current.st_size == serialized_size
    )


def _source_is_registered(source, registry_path):
    if not registry_path:
        return True
    if not os.path.lexists(registry_path):
        return False
    for current in load_m3u_sources(registry_path):
        if (
            current.source_id == source.source_id
            and current.kind == source.kind
            and current.location == source.location
        ):
            return True
    return False


def _discard_staged_cache(directory, staged_name, staged_details):
    if not staged_name or staged_details is None:
        return
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = None
    try:
        descriptor = os.open(directory, flags)
        current = os.stat(
            staged_name,
            dir_fd=descriptor,
            follow_symlinks=False,
        )
        if (
            stat.S_ISREG(current.st_mode)
            and current.st_dev == staged_details.st_dev
            and current.st_ino == staged_details.st_ino
        ):
            os.unlink(staged_name, dir_fd=descriptor)
    except OSError:
        pass
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _discard_staged_cache_locked(
    directory_descriptor,
    staged_name,
    staged_details,
):
    if not staged_name or staged_details is None:
        return
    try:
        current = os.stat(
            staged_name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            stat.S_ISREG(current.st_mode)
            and current.st_dev == staged_details.st_dev
            and current.st_ino == staged_details.st_ino
        ):
            os.unlink(staged_name, dir_fd=directory_descriptor)
    except OSError:
        pass


def _commit_staged_cache_locked(
    directory_descriptor,
    filename,
    staged_name,
    staged_details,
    serialized_size,
    removals,
    deadline,
    check_limits,
):
    if not _matching_staged_cache(
        directory_descriptor,
        staged_name,
        staged_details,
        serialized_size,
    ):
        raise OSError("unsafe staged cache")
    try:
        target = os.stat(
            filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        target = None
    if target is not None and not stat.S_ISREG(target.st_mode):
        raise OSError("unsafe cache target")
    check_limits(deadline)
    os.replace(
        staged_name,
        filename,
        src_dir_fd=directory_descriptor,
        dst_dir_fd=directory_descriptor,
    )
    os.fsync(directory_descriptor)

    for candidate_name, before in removals:
        try:
            current = os.stat(
                candidate_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except OSError:
            continue
        if (
            stat.S_ISREG(current.st_mode)
            and current.st_dev == before.st_dev
            and current.st_ino == before.st_ino
            and current.st_size == before.st_size
        ):
            try:
                os.unlink(candidate_name, dir_fd=directory_descriptor)
            except OSError:
                continue
    try:
        os.fsync(directory_descriptor)
    except OSError:
        pass


def _write_catalog_cache(
    path,
    source,
    catalog,
    deadline,
    check_limits,
    registry_path=None,
):
    directory = os.path.dirname(path) or "."
    filename = os.path.basename(path)
    if filename != source.source_id + ".jsonl":
        raise OSError("invalid cache target")
    staged_name = None
    staged_details = None
    try:
        transaction = (
            _registry_transaction_lock(
                registry_path,
                deadline=deadline,
                check_limits=check_limits,
            )
            if registry_path
            else _no_transaction_lock()
        )
        with transaction:
            if not _source_is_registered(source, registry_path):
                raise OSError("M3U source is no longer registered")
            with _cache_directory_lock(
                directory,
                create=True,
                deadline=deadline,
                check_limits=check_limits,
            ) as directory_descriptor:
                try:
                    staged_name, staged_details, written = _stage_catalog_cache_locked(
                        directory_descriptor,
                        filename,
                        _catalog_cache_lines(source, catalog, deadline, check_limits),
                        MAX_CACHE_BYTES,
                        deadline,
                        check_limits,
                    )
                    serialized_size = written
                    if not _matching_staged_cache(
                        directory_descriptor,
                        staged_name,
                        staged_details,
                        serialized_size,
                    ):
                        raise OSError("unsafe staged cache")
                    removals = _cache_budget_plan(
                        directory_descriptor,
                        filename,
                        serialized_size,
                    )
                    if not _source_is_registered(source, registry_path):
                        raise OSError("M3U source is no longer registered")
                    _commit_staged_cache_locked(
                        directory_descriptor,
                        filename,
                        staged_name,
                        staged_details,
                        serialized_size,
                        removals,
                        deadline,
                        check_limits,
                    )
                    staged_name = None
                    staged_details = None
                finally:
                    _discard_staged_cache_locked(
                        directory_descriptor,
                        staged_name,
                        staged_details,
                    )
                    staged_name = None
                    staged_details = None
    finally:
        _discard_staged_cache(directory, staged_name, staged_details)


def _read_catalog_cache(path, source, deadline, check_limits):
    lines = _iter_private_lines(path, MAX_CACHE_BYTES, MAX_CACHE_HEADER_BYTES)
    try:
        header_line = next(lines)
        if not header_line or len(header_line) > MAX_CACHE_HEADER_BYTES:
            raise ValueError("invalid cache")
        meta = json.loads(header_line.decode("utf-8"))
    except (StopIteration, TypeError, ValueError, UnicodeError) as error:
        raise ValueError("invalid cache") from error
    if (
        not isinstance(meta, dict)
        or meta.get("type") != "meta"
        or meta.get("version") not in (1, M3U_CACHE_VERSION)
        or meta.get("source_id") != source.source_id
        or meta.get("location_key") != _source_location_key(source)
    ):
        raise ValueError("invalid cache")
    raw_categories = meta.get("categories")
    if not isinstance(raw_categories, list) or len(raw_categories) > MAX_M3U_CATEGORIES:
        raise ValueError("invalid cache")
    categories = []
    category_ids = set()
    for entry in raw_categories:
        if not isinstance(entry, list) or len(entry) != 2:
            raise ValueError("invalid cache")
        category_id = str(entry[0] or "")
        name = _single_line(entry[1], 180)
        if not _valid_category_id(category_id) or category_id in category_ids:
            raise ValueError("invalid cache")
        category_ids.add(category_id)
        categories.append({"id": category_id, "name": name})

    records = []
    seen = set()
    for index, line in enumerate(lines):
        if index % 128 == 0:
            check_limits(deadline)
        if index >= MAX_M3U_CHANNELS or not line or len(line) > MAX_CACHE_LINE_BYTES:
            raise ValueError("invalid cache")
        try:
            entry = json.loads(line.decode("utf-8"))
        except (TypeError, ValueError, UnicodeError) as error:
            raise ValueError("invalid cache") from error
        if not isinstance(entry, dict) or entry.get("type") != "item":
            raise ValueError("invalid cache")
        stream_id = str(entry.get("stream_id") or "")
        category_id = str(entry.get("category_id") or "")
        stream_url = _safe_stream_url(entry.get("url"))
        name = _single_line(entry.get("name"), 180)
        if (
            not _valid_stream_id(stream_id)
            or stream_id in seen
            or category_id not in category_ids
            or not stream_url
            or not name
        ):
            raise ValueError("invalid cache")
        seen.add(stream_id)
        records.append(
            {
                "stream_id": stream_id,
                "name": name,
                "group": _single_line(entry.get("group"), 180),
                "category_id": category_id,
                "tvg_id": _single_line(entry.get("tvg_id"), 256),
                "icon": _safe_icon_url(entry.get("icon")),
                "url": stream_url,
                "extension": _stream_extension(stream_url),
                "catchup": _archive_attributes(entry.get("catchup")),
            }
        )
    check_limits(deadline)
    if not records:
        raise ValueError("invalid cache")
    try:
        cached_at = float(meta.get("cached_at", 0) or 0)
        size_bytes = int(meta.get("size_bytes", 0) or 0)
        skipped_entries = int(meta.get("skipped_entries", 0) or 0)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("invalid cache") from error
    if (
        cached_at <= 0
        or not math.isfinite(cached_at)
        or size_bytes < 0
        or size_bytes > MAX_M3U_BYTES
        or skipped_entries < 0
    ):
        raise ValueError("invalid cache")
    return {
        "meta": {
            # A v1 catalogue can still serve as the ordinary 24-hour stale
            # live fallback while offline, but always triggers a refresh.
            "version": meta["version"],
            "cached_at": cached_at,
            "fingerprint": meta.get("fingerprint") or {},
            "size_bytes": size_bytes,
            "skipped_entries": skipped_entries,
        },
        "categories": categories,
        "records": records,
    }


def _cache_age(cache, now):
    if cache is None:
        return None
    try:
        cached_at = float(cache["meta"].get("cached_at", 0) or 0)
        now = float(now)
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    if (
        not math.isfinite(cached_at)
        or not math.isfinite(now)
        or cached_at <= 0
        or cached_at > now + 300
    ):
        return None
    return max(0.0, now - cached_at)


def _read_private_file(path, maximum):
    try:
        listed = os.lstat(path)
    except OSError:
        raise
    if stat.S_ISLNK(listed.st_mode) or not stat.S_ISREG(listed.st_mode):
        raise OSError("not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    chunks = []
    total = 0
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_dev != listed.st_dev
            or details.st_ino != listed.st_ino
            or details.st_size > maximum
        ):
            raise OSError("file is too large")
        while True:
            chunk = os.read(descriptor, min(READ_CHUNK_BYTES, maximum + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise OSError("file is too large")
            chunks.append(chunk)
    finally:
        os.close(descriptor)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return b"".join(chunks)


def _iter_private_lines(path, maximum, maximum_line):
    listed = os.lstat(path)
    if stat.S_ISLNK(listed.st_mode) or not stat.S_ISREG(listed.st_mode):
        raise OSError("not a regular file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    buffer = b""
    total = 0
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_dev != listed.st_dev
            or details.st_ino != listed.st_ino
            or details.st_size > maximum
        ):
            raise OSError("file is too large")
        while True:
            chunk = os.read(descriptor, READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise OSError("file is too large")
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if len(line) > maximum_line:
                    raise OSError("cache line is too large")
                yield line.rstrip(b"\r")
            if len(buffer) > maximum_line:
                raise OSError("cache line is too large")
        if buffer:
            if len(buffer) > maximum_line:
                raise OSError("cache line is too large")
            yield buffer.rstrip(b"\r")
    finally:
        os.close(descriptor)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass


def _ensure_private_directory(path):
    if not path:
        return
    try:
        os.makedirs(path, 0o700)
    except OSError:
        if not os.path.isdir(path):
            raise
    details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise OSError("unsafe directory")
    os.chmod(path, 0o700)


def _atomic_write_lines(
    path,
    lines,
    mode,
    maximum,
    deadline=None,
    check_limits=None,
):
    directory = os.path.dirname(path) or "."
    _ensure_private_directory(directory)
    if os.path.lexists(path):
        details = os.lstat(path)
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise OSError("unsafe target")
    temporary = os.path.join(
        directory,
        ".{}.{}.{}.tmp".format(os.path.basename(path), os.getpid(), uuid.uuid4().hex),
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(temporary, flags, mode)
    total = 0
    try:
        for index, line in enumerate(lines):
            if check_limits is not None and index % 128 == 0:
                check_limits(deadline)
            encoded = (str(line) + "\n").encode("utf-8")
            total += len(encoded)
            if total > maximum:
                raise ValueError("cache is too large")
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError("short write")
                offset += written
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
        if check_limits is not None:
            check_limits(deadline)
    except Exception:
        os.close(descriptor)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    else:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        os.chmod(path, mode)
        _fsync_directory(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _atomic_write(path, body, mode):
    directory = os.path.dirname(path) or "."
    _ensure_private_directory(directory)
    if os.path.lexists(path):
        details = os.lstat(path)
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise OSError("unsafe target")
    temporary = os.path.join(
        directory,
        ".{}.{}.{}.tmp".format(os.path.basename(path), os.getpid(), uuid.uuid4().hex),
    )
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    descriptor = os.open(temporary, flags, mode)
    try:
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written <= 0:
                raise OSError("short write")
            offset += written
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
    except Exception:
        os.close(descriptor)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    else:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        os.chmod(path, mode)
        _fsync_directory(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


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


def _remote_url_info(value):
    value = str(value or "").strip()
    if (
        not value
        or len(value) > MAX_REMOTE_URL_LENGTH
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError(N_("M3U URL is invalid."))
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise ValueError(N_("M3U URL is invalid.")) from error
    scheme = str(parsed.scheme or "").lower()
    if (
        scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise ValueError(N_("M3U URL is not safe."))
    if port is not None and not 1 <= int(port) <= 65535:
        raise ValueError(N_("M3U URL port is invalid."))
    try:
        hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    except (UnicodeError, ValueError) as error:
        raise ValueError(N_("M3U host name is invalid.")) from error
    selected_port = port or (443 if scheme == "https" else 80)
    netloc = hostname
    try:
        if ipaddress.ip_address(hostname).version == 6:
            netloc = "[{}]".format(hostname)
    except ValueError:
        pass
    if selected_port != (443 if scheme == "https" else 80):
        netloc = "{}:{}".format(netloc, selected_port)
    return {
        "url": urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, "")),
        "scheme": scheme,
        "hostname": hostname,
        "port": selected_port,
        "path": urlunsplit(("", "", parsed.path or "/", parsed.query, "")),
    }


def _allowed_remote_address(value):
    try:
        address = ipaddress.ip_address(str(value).split("%", 1)[0])
    except ValueError as error:
        raise ContentError(
            N_("M3U host address is invalid."),
            ContentError.CONNECTION,
        ) from error
    if (
        address.is_unspecified
        or address.is_loopback
        or address.is_link_local
        or address.is_multicast
        or address.is_reserved
    ):
        raise ContentError(
            N_("M3U host address is not allowed."),
            ContentError.ACCESS_DENIED,
        )
    # ``is_private`` does not cover every address which is outside the public
    # Internet.  In particular, the shared 100.64.0.0/10 range used by CGNAT
    # and Tailscale is neither private nor global.  Treat every allowed,
    # non-global address as the local/private scope so a public source cannot
    # reach it through a redirect or a changed DNS answer.
    return str(address), "public" if address.is_global else "private"


def _resolve_remote(
    info,
    resolver,
    expected_scope=None,
    deadline=None,
    check_limits=None,
):
    if not _DNS_SLOTS.acquire(blocking=False):
        raise ContentError(
            N_("M3U host name resolution is busy."),
            ContentError.CONNECTION,
        )
    completed = threading.Event()
    result = {"answers": None, "error": None}

    def lookup():
        try:
            result["answers"] = resolver(
                info["hostname"],
                int(info["port"]),
                socket.AF_UNSPEC,
                socket.SOCK_STREAM,
            )
        except Exception as error:
            result["error"] = error
        finally:
            completed.set()
            _DNS_SLOTS.release()

    worker = threading.Thread(target=lookup)
    worker.daemon = True
    try:
        worker.start()
    except Exception:
        _DNS_SLOTS.release()
        raise
    while not completed.wait(0.05):
        if check_limits is not None and deadline is not None:
            check_limits(deadline)
    if result["error"] is not None:
        error = result["error"]
        raise ContentError(
            N_("M3U host name could not be resolved."),
            ContentError.CONNECTION,
        ) from error
    answers = result["answers"] or []
    addresses = []
    scopes = set()
    for answer in answers:
        sockaddr = answer[4]
        if not sockaddr:
            continue
        address, scope = _allowed_remote_address(sockaddr[0])
        scopes.add(scope)
        if address not in addresses:
            addresses.append(address)
    if not addresses or len(scopes) != 1:
        raise ContentError(
            N_("M3U host resolved to an unsafe address set."),
            ContentError.ACCESS_DENIED,
        )
    scope = next(iter(scopes))
    if expected_scope is not None and scope != expected_scope:
        raise ContentError(
            N_("M3U redirect changed the network scope."),
            ContentError.ACCESS_DENIED,
        )
    return addresses, scope


class _PinnedHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, hostname, address, port, timeout):
        http.client.HTTPSConnection.__init__(
            self,
            hostname,
            port=port,
            timeout=timeout,
            context=ssl.create_default_context(),
        )
        self._pinned_address = address

    def connect(self):
        sock = socket.create_connection(
            (self._pinned_address, self.port),
            self.timeout,
            self.source_address,
        )
        try:
            self.sock = self._context.wrap_socket(sock, server_hostname=self.host)
        except Exception:
            sock.close()
            raise


def _host_header(info):
    hostname = info["hostname"]
    try:
        if ipaddress.ip_address(hostname).version == 6:
            hostname = "[{}]".format(hostname)
    except ValueError:
        pass
    default = 443 if info["scheme"] == "https" else 80
    return hostname if info["port"] == default else "{}:{}".format(hostname, info["port"])


def _set_response_socket_timeout(response, connection, timeout):
    candidates = []
    response_file = getattr(response, "fp", None)
    raw_file = getattr(response_file, "raw", None)
    for candidate in (
        getattr(raw_file, "_sock", None),
        getattr(raw_file, "sock", None),
        getattr(response_file, "_sock", None),
        getattr(response_file, "sock", None),
        getattr(connection, "sock", None),
    ):
        if (
            candidate is not None
            and callable(getattr(candidate, "settimeout", None))
            and all(candidate is not item for item in candidates)
        ):
            candidates.append(candidate)
    if not candidates:
        raise OSError("response socket is unavailable")
    last_error = None
    for candidate in candidates:
        try:
            candidate.settimeout(timeout)
            return
        except OSError as error:
            last_error = error
    raise OSError("response socket timeout could not be set") from last_error


def _read_http_response_body(
    response,
    connection,
    maximum,
    timeout,
    deadline,
    check_limits,
    remaining_timeout,
):
    body = bytearray()
    total = 0
    while True:
        check_limits(deadline)
        remaining_length = getattr(response, "length", None)
        if remaining_length == 0:
            break
        if getattr(response, "fp", None) is None:
            if remaining_length not in (None, 0):
                raise http.client.IncompleteRead(bytes(body), remaining_length)
            break
        remaining = min(float(timeout), remaining_timeout(deadline))
        _set_response_socket_timeout(response, connection, remaining)
        try:
            chunk = response.read1(min(READ_CHUNK_BYTES, maximum + 1 - total))
        except (OSError, socket.error, http.client.HTTPException, ssl.SSLError):
            check_limits(deadline)
            raise
        check_limits(deadline)
        if not chunk:
            break
        total += len(chunk)
        if total > maximum:
            raise ContentError(
                N_("M3U source exceeds the safe size limit."),
                ContentError.RESPONSE_TOO_LARGE,
            )
        body.extend(chunk)
    return body


def _fetch_remote_m3u(
    url,
    maximum,
    timeout,
    deadline,
    check_limits,
    resolver,
    remaining_timeout,
):
    current = _remote_url_info(url)["url"]
    previous_scheme = ""
    original_scope = None
    for redirect_count in range(MAX_REMOTE_REDIRECTS + 1):
        check_limits(deadline)
        info = _remote_url_info(current)
        if previous_scheme == "https" and info["scheme"] != "https":
            raise ContentError(
                N_("An HTTPS M3U request cannot be redirected to HTTP."),
                ContentError.ACCESS_DENIED,
            )
        addresses, scope = _resolve_remote(
            info,
            resolver,
            original_scope,
            deadline,
            check_limits,
        )
        if original_scope is None:
            original_scope = scope
        last_error = None
        for address in addresses:
            check_limits(deadline)
            connection = None
            response = None
            try:
                remaining = min(float(timeout), remaining_timeout(deadline))
                if info["scheme"] == "https":
                    connection = _PinnedHTTPSConnection(
                        info["hostname"], address, info["port"], remaining
                    )
                else:
                    connection = http.client.HTTPConnection(
                        address, port=info["port"], timeout=remaining
                    )
                connection.request(
                    "GET",
                    info["path"],
                    headers={
                        "Accept": "audio/x-mpegurl, application/vnd.apple.mpegurl, text/plain",
                        "Accept-Encoding": "identity",
                        "Connection": "close",
                        "Host": _host_header(info),
                        "User-Agent": "GT-IPTV-Player-Pro/{}".format(PLUGIN_VERSION),
                    },
                )
                response = connection.getresponse()
                status_code = int(response.status)
                headers = _safe_response_headers(response)
                if status_code in _REDIRECT_STATUSES:
                    body = b""
                elif status_code != 200:
                    if status_code in (401, 403):
                        reason = ContentError.ACCESS_DENIED
                    elif status_code == 429:
                        reason = ContentError.RATE_LIMITED
                    else:
                        reason = ContentError.HTTP_ERROR
                    raise ContentError(
                        _(N_("M3U server returned HTTP {}.")).format(status_code),
                        reason,
                        status_code,
                    )
                else:
                    encoding = headers.get("content-encoding", "").lower()
                    if encoding not in ("", "identity"):
                        raise ContentError(
                            N_("Compressed M3U responses are not accepted."),
                            ContentError.INVALID_RESPONSE,
                        )
                    declared = headers.get("content-length")
                    if declared:
                        try:
                            declared_size = int(declared)
                        except ValueError as error:
                            raise ContentError(
                                N_("M3U response size is invalid."),
                                ContentError.INVALID_RESPONSE,
                            ) from error
                        if declared_size < 0 or declared_size > maximum:
                            raise ContentError(
                                N_("M3U source exceeds the safe size limit."),
                                ContentError.RESPONSE_TOO_LARGE,
                            )
                    body = _read_http_response_body(
                        response,
                        connection,
                        maximum,
                        timeout,
                        deadline,
                        check_limits,
                        remaining_timeout,
                    )
                break
            except ContentError:
                raise
            except (OSError, socket.error, http.client.HTTPException, ssl.SSLError) as error:
                check_limits(deadline)
                last_error = error
                body = None
                status_code = 0
                headers = {}
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
            if body is not None:
                break
        if body is None:
            raise ContentError(
                N_("Could not load the M3U source."),
                ContentError.CONNECTION,
            ) from last_error
        if status_code == 200:
            return body
        location = headers.get("location", "")
        if not location:
            raise ContentError(
                N_("M3U redirect URL is missing."),
                ContentError.INVALID_RESPONSE,
            )
        if redirect_count >= MAX_REMOTE_REDIRECTS:
            raise ContentError(
                N_("M3U request was redirected too many times."),
                ContentError.HTTP_ERROR,
            )
        previous_scheme = info["scheme"]
        try:
            current = _remote_url_info(urljoin(info["url"], location))["url"]
        except ValueError as error:
            raise ContentError(
                N_("M3U redirect URL is invalid."),
                ContentError.ACCESS_DENIED,
            ) from error
    raise ContentError(
        N_("M3U redirection could not be completed."),
        ContentError.HTTP_ERROR,
    )


def _safe_response_headers(response):
    headers = {}
    lengths = []
    for key, value in response.getheaders():
        lowered = str(key).lower()
        cleaned = str(value).strip()
        if "\r" in cleaned or "\n" in cleaned:
            raise ContentError(
                N_("M3U response headers are invalid."),
                ContentError.INVALID_RESPONSE,
            )
        if lowered == "content-length":
            lengths.append(cleaned)
        headers[lowered] = cleaned
    if len(set(lengths)) > 1:
        raise ContentError(
            N_("M3U response has conflicting size information."),
            ContentError.INVALID_RESPONSE,
        )
    return headers

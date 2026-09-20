# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private per-M3U settings for optional external XMLTV sources.

The registry deliberately lives beside, rather than inside, ``m3u-sources``.
This keeps the established M3U source schema stable and lets the EPG runtime
update its small status counters without rewriting playlist configuration.
"""

import errno
import fcntl
import hashlib
import json
import os
import stat
import threading
import time
import uuid
from contextlib import contextmanager
from urllib.parse import urlsplit

from .m3u import LOCAL_M3U_ROOTS, _remote_url_info


M3U_XMLTV_SETTINGS_PATH = "/etc/enigma2/gtiptvplayer/m3u-xmltv.json"
# Kept as a descriptive alias for callers which use the feature name rather
# than the on-disk filename.
M3U_EPG_SETTINGS_PATH = M3U_XMLTV_SETTINGS_PATH
M3U_EPG_SETTINGS_VERSION = 1

MAX_M3U_EPG_BINDINGS = 64
MAX_M3U_EPG_SETTINGS_BYTES = 256 * 1024
MAX_SOURCE_ID_LENGTH = 64
MAX_LOCAL_PATH_LENGTH = 1024
MAX_ERROR_CODE_LENGTH = 64
MAX_TIMESTAMP = (1 << 63) - 1
MAX_COUNTER = (1 << 31) - 1
M3U_EPG_LOCK_TIMEOUT = 1.0

SUPPORTED_UPDATE_MODES = ("manual", "automatic")
SUPPORTED_UPDATE_INTERVAL_HOURS = (4, 6, 12, 24)
LOCAL_XMLTV_ROOTS = LOCAL_M3U_ROOTS

_SOURCE_ID_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-"
)
_ERROR_CODE_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._:-"
)
_SETTINGS_LOCK = threading.RLock()
_LOCK_FILENAME = ".m3u-xmltv.lock"
_READ_CHUNK_BYTES = 64 * 1024


def _valid_source_id(value):
    return bool(
        isinstance(value, str)
        and value
        and len(value) <= MAX_SOURCE_ID_LENGTH
        and value not in (".", "..")
        and all(character in _SOURCE_ID_CHARACTERS for character in value)
    )


def _source_id(value):
    value = str(value or "").strip()
    if not _valid_source_id(value):
        raise ValueError("M3U source ID is invalid")
    return value


def _bounded_integer(value, name, maximum):
    if isinstance(value, bool):
        raise ValueError("{} is invalid".format(name))
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("{} is invalid".format(name)) from error
    if value < 0 or value > maximum:
        raise ValueError("{} is invalid".format(name))
    return value


def _error_code(value):
    value = str(value or "").strip()
    if (
        len(value) > MAX_ERROR_CODE_LENGTH
        or any(character not in _ERROR_CODE_CHARACTERS for character in value)
    ):
        raise ValueError("M3U XMLTV error code is invalid")
    return value


def _root_tuple(roots):
    if roots is None:
        return tuple(LOCAL_XMLTV_ROOTS)
    if isinstance(roots, (str, os.PathLike)):
        return (os.fspath(roots),)
    return tuple(roots)


def _local_location(value, roots=None):
    value = os.fspath(value) if isinstance(value, os.PathLike) else str(value or "")
    value = value.strip()
    if (
        not value
        or len(value) > MAX_LOCAL_PATH_LENGTH
        or not os.path.isabs(value)
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        raise ValueError("M3U XMLTV file path is invalid")
    if any(component in (".", "..") for component in value.split(os.sep)):
        raise ValueError("M3U XMLTV file path traversal is not allowed")

    absolute = os.path.abspath(value)
    real = os.path.realpath(absolute)
    # Existing symbolic-link components are rejected.  The final file is
    # allowed to be absent so a removable/NAS source can be configured while
    # it is temporarily offline.
    if real != absolute:
        raise ValueError("Symbolic links cannot be used as M3U XMLTV sources")

    allowed = False
    for root in _root_tuple(roots):
        root = os.path.realpath(os.path.abspath(os.fspath(root)))
        try:
            if os.path.commonpath((real, root)) == root:
                allowed = True
                break
        except ValueError:
            continue
    if not allowed:
        raise ValueError("M3U XMLTV file is outside the allowed directories")

    if os.path.lexists(absolute):
        details = os.lstat(absolute)
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise ValueError("M3U XMLTV source must be a regular file")
    return absolute


class Binding(object):
    """One stable M3U source-to-XMLTV binding.

    ``source_id`` is read-only after construction.  Runtime fields remain
    mutable so ``update_m3u_epg_binding`` can use a compact copy/mutate/return
    callback without exposing the live registry object.
    """

    __slots__ = (
        "_source_id",
        "enabled",
        "kind",
        "location",
        "update_mode",
        "interval_hours",
        "last_attempt_utc",
        "last_success_utc",
        "last_error_code",
        "last_channel_count",
        "last_event_count",
    )

    def __init__(
        self,
        source_id,
        enabled=False,
        kind="file",
        location="",
        update_mode="automatic",
        interval_hours=24,
        last_attempt_utc=0,
        last_success_utc=0,
        last_error_code="",
        last_channel_count=0,
        last_event_count=0,
        local_roots=None,
    ):
        self._source_id = _source_id(source_id)
        self.enabled = bool(enabled)

        kind = str(kind or "").strip().lower()
        if kind not in ("file", "url"):
            raise ValueError("M3U XMLTV source type is invalid")
        self.kind = kind
        raw_location = (
            os.fspath(location)
            if isinstance(location, os.PathLike)
            else str(location or "")
        ).strip()
        if not raw_location and not self.enabled:
            # A disabled empty binding is the safe first-open UI model.  It
            # cannot schedule I/O and becomes fully validated as soon as the
            # user enables it or chooses an endpoint.
            self.location = ""
        elif kind == "file":
            self.location = _local_location(location, local_roots)
        else:
            # Reuse the M3U provider's canonical URL validator.  In
            # particular this rejects credentials, fragments, unsafe schemes,
            # invalid ports and control characters without ever resolving or
            # contacting the endpoint.
            self.location = _remote_url_info(location)["url"]

        update_mode = str(update_mode or "").strip().lower()
        self.update_mode = (
            update_mode if update_mode in SUPPORTED_UPDATE_MODES else "automatic"
        )
        try:
            interval_hours = int(interval_hours)
        except (TypeError, ValueError, OverflowError):
            interval_hours = 24
        self.interval_hours = (
            interval_hours
            if interval_hours in SUPPORTED_UPDATE_INTERVAL_HOURS
            else 24
        )
        self.last_attempt_utc = _bounded_integer(
            last_attempt_utc,
            "last attempt time",
            MAX_TIMESTAMP,
        )
        self.last_success_utc = _bounded_integer(
            last_success_utc,
            "last success time",
            MAX_TIMESTAMP,
        )
        self.last_error_code = _error_code(last_error_code)
        self.last_channel_count = _bounded_integer(
            last_channel_count,
            "last channel count",
            MAX_COUNTER,
        )
        self.last_event_count = _bounded_integer(
            last_event_count,
            "last event count",
            MAX_COUNTER,
        )

    @property
    def source_id(self):
        return self._source_id

    @property
    def fingerprint(self):
        raw = "\x1f".join((self.source_id, self.kind, self.location))
        return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()

    @property
    def display_endpoint(self):
        if self.kind == "file":
            return self.location
        try:
            parsed = urlsplit(self.location)
            port = parsed.port
        except (TypeError, ValueError):
            return ""
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = "[{}]".format(hostname)
        default_port = 443 if parsed.scheme.lower() == "https" else 80
        if port and port != default_port:
            hostname = "{}:{}".format(hostname, port)
        return "{}://{}".format(parsed.scheme.lower(), hostname)

    def as_dict(self):
        return {
            "source_id": self.source_id,
            "enabled": self.enabled,
            "kind": self.kind,
            "location": self.location,
            "update_mode": self.update_mode,
            "interval_hours": self.interval_hours,
            "last_attempt_utc": self.last_attempt_utc,
            "last_success_utc": self.last_success_utc,
            "last_error_code": self.last_error_code,
            "last_channel_count": self.last_channel_count,
            "last_event_count": self.last_event_count,
        }

    to_dict = as_dict

    @classmethod
    def from_dict(cls, payload, local_roots=None):
        if not isinstance(payload, dict):
            raise ValueError("M3U XMLTV binding is invalid")
        required = ("source_id", "enabled", "kind", "location")
        if any(field not in payload for field in required):
            raise ValueError("M3U XMLTV binding is invalid")
        if not isinstance(payload.get("source_id"), str):
            raise ValueError("M3U XMLTV binding is invalid")
        if not isinstance(payload.get("enabled"), bool):
            raise ValueError("M3U XMLTV binding is invalid")
        if not isinstance(payload.get("kind"), str) or not isinstance(
            payload.get("location"),
            str,
        ):
            raise ValueError("M3U XMLTV binding is invalid")
        if "update_mode" in payload and not isinstance(
            payload.get("update_mode"),
            str,
        ):
            raise ValueError("M3U XMLTV binding is invalid")
        if "last_error_code" in payload and not isinstance(
            payload.get("last_error_code"),
            str,
        ):
            raise ValueError("M3U XMLTV binding is invalid")
        for field in (
            "interval_hours",
            "last_attempt_utc",
            "last_success_utc",
            "last_channel_count",
            "last_event_count",
        ):
            if field in payload and (
                isinstance(payload.get(field), bool)
                or not isinstance(payload.get(field), int)
            ):
                raise ValueError("M3U XMLTV binding is invalid")
        update_mode = payload.get("update_mode", "automatic")
        interval_hours = payload.get("interval_hours", 24)
        if update_mode not in SUPPORTED_UPDATE_MODES or (
            interval_hours not in SUPPORTED_UPDATE_INTERVAL_HOURS
        ):
            raise ValueError("M3U XMLTV update schedule is invalid")
        return cls(
            source_id=payload.get("source_id"),
            enabled=payload.get("enabled"),
            kind=payload.get("kind"),
            location=payload.get("location"),
            update_mode=update_mode,
            interval_hours=interval_hours,
            last_attempt_utc=payload.get("last_attempt_utc", 0),
            last_success_utc=payload.get("last_success_utc", 0),
            last_error_code=payload.get("last_error_code", ""),
            last_channel_count=payload.get("last_channel_count", 0),
            last_event_count=payload.get("last_event_count", 0),
            local_roots=local_roots,
        )

    def copy(self):
        return self.from_dict(self.as_dict())

    def __repr__(self):
        return "Binding(source_id={!r}, kind={!r}, enabled={!r})".format(
            self.source_id,
            self.kind,
            self.enabled,
        )


class M3UEPGSettings(object):
    """Validated versioned envelope used by the private sidecar."""

    __slots__ = ("bindings",)

    def __init__(self, bindings=()):
        rows = []
        seen = set()
        for binding in bindings or ():
            if isinstance(binding, dict):
                binding = Binding.from_dict(binding)
            elif isinstance(binding, Binding):
                binding = Binding.from_dict(binding.as_dict())
            else:
                raise TypeError("M3U XMLTV binding is invalid")
            if binding.source_id in seen:
                raise ValueError("Duplicate M3U XMLTV source ID")
            seen.add(binding.source_id)
            rows.append(binding)
            if len(rows) > MAX_M3U_EPG_BINDINGS:
                raise ValueError("Too many M3U XMLTV bindings are configured")
        self.bindings = tuple(rows)

    @classmethod
    def from_dict(cls, payload):
        if (
            not isinstance(payload, dict)
            or isinstance(payload.get("version"), bool)
            or not isinstance(payload.get("version"), int)
            or payload.get("version") != M3U_EPG_SETTINGS_VERSION
            or not isinstance(payload.get("bindings"), list)
            or len(payload.get("bindings")) > MAX_M3U_EPG_BINDINGS
        ):
            raise ValueError("M3U XMLTV settings are invalid")
        return cls(Binding.from_dict(row) for row in payload.get("bindings"))

    def as_dict(self):
        return {
            "version": M3U_EPG_SETTINGS_VERSION,
            "bindings": [binding.as_dict() for binding in self.bindings],
        }

    to_dict = as_dict


def _settings_path(path):
    path = os.fspath(path) if isinstance(path, os.PathLike) else str(path or "")
    if (
        not path
        or not os.path.isabs(path)
        or os.path.basename(path) in ("", ".", "..", _LOCK_FILENAME)
        or "\x00" in path
    ):
        raise OSError("invalid M3U XMLTV settings path")
    return os.path.abspath(path)


def _prepare_directory(directory):
    if os.path.lexists(directory):
        details = os.lstat(directory)
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
            raise OSError("unsafe M3U XMLTV settings directory")
    else:
        try:
            os.makedirs(directory, 0o700)
        except OSError:
            if not os.path.isdir(directory):
                raise
        details = os.lstat(directory)
    if os.path.realpath(directory) != os.path.abspath(directory):
        raise OSError("unsafe M3U XMLTV settings directory")
    return details


@contextmanager
def _transaction(path):
    path = _settings_path(path)
    directory = os.path.dirname(path) or "/"
    filename = os.path.basename(path)
    with _SETTINGS_LOCK:
        listed = _prepare_directory(directory)
        directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)
        directory_descriptor = os.open(directory, directory_flags)
        lock_descriptor = None
        try:
            opened = os.fstat(directory_descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or opened.st_dev != listed.st_dev
                or opened.st_ino != listed.st_ino
            ):
                raise OSError("unsafe M3U XMLTV settings directory")
            lock_flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            lock_flags |= getattr(os, "O_NOFOLLOW", 0)
            lock_descriptor = os.open(
                _LOCK_FILENAME,
                lock_flags,
                0o600,
                dir_fd=directory_descriptor,
            )
            lock_details = os.fstat(lock_descriptor)
            if not stat.S_ISREG(lock_details.st_mode) or lock_details.st_nlink != 1:
                raise OSError("unsafe M3U XMLTV settings lock")
            os.fchmod(lock_descriptor, 0o600)
            deadline = time.monotonic() + M3U_EPG_LOCK_TIMEOUT
            while True:
                try:
                    fcntl.flock(
                        lock_descriptor,
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    break
                except OSError as error:
                    if error.errno == errno.EINTR:
                        continue
                    if error.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.monotonic() >= deadline:
                        raise OSError("M3U XMLTV settings lock timed out")
                    time.sleep(0.05)
            current_lock = os.stat(
                _LOCK_FILENAME,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                current_lock.st_dev != lock_details.st_dev
                or current_lock.st_ino != lock_details.st_ino
            ):
                raise OSError("unsafe M3U XMLTV settings lock")
            yield directory_descriptor, filename
        finally:
            if lock_descriptor is not None:
                try:
                    fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(lock_descriptor)
            os.close(directory_descriptor)


def _target_details(directory_descriptor, filename):
    try:
        details = os.stat(
            filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(details.st_mode)
        or not stat.S_ISREG(details.st_mode)
        or details.st_nlink != 1
    ):
        raise OSError("unsafe M3U XMLTV settings target")
    return details


def _read_payload(directory_descriptor, filename):
    listed = _target_details(directory_descriptor, filename)
    if listed is None:
        return None
    if listed.st_size > MAX_M3U_EPG_SETTINGS_BYTES:
        raise ValueError("M3U XMLTV settings file is too large")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
    chunks = []
    total = 0
    try:
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or opened.st_dev != listed.st_dev
            or opened.st_ino != listed.st_ino
            or opened.st_size > MAX_M3U_EPG_SETTINGS_BYTES
        ):
            raise OSError("unsafe M3U XMLTV settings target")
        while True:
            chunk = os.read(
                descriptor,
                min(
                    _READ_CHUNK_BYTES,
                    MAX_M3U_EPG_SETTINGS_BYTES + 1 - total,
                ),
            )
            if not chunk:
                break
            total += len(chunk)
            if total > MAX_M3U_EPG_SETTINGS_BYTES:
                raise ValueError("M3U XMLTV settings file is too large")
            chunks.append(chunk)
        try:
            os.fchmod(descriptor, 0o600)
        except OSError:
            pass
    finally:
        os.close(descriptor)
    return b"".join(chunks)


def _load_unlocked(directory_descriptor, filename, unsafe_empty=False):
    try:
        body = _read_payload(directory_descriptor, filename)
    except OSError:
        if unsafe_empty:
            return M3UEPGSettings()
        raise
    if body is None:
        return M3UEPGSettings()
    try:
        payload = json.loads(body.decode("utf-8-sig"))
        return M3UEPGSettings.from_dict(payload)
    except (TypeError, ValueError, UnicodeError):
        # A corrupt or future-version sidecar never enables an endpoint.  A
        # later explicit save may safely replace the regular corrupt file.
        return M3UEPGSettings()


def _write_unlocked(directory_descriptor, filename, settings):
    _target_details(directory_descriptor, filename)
    body = (
        json.dumps(
            settings.as_dict(),
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")
    if len(body) > MAX_M3U_EPG_SETTINGS_BYTES:
        raise ValueError("M3U XMLTV settings file is too large")

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
    try:
        offset = 0
        while offset < len(body):
            written = os.write(descriptor, body[offset:])
            if written <= 0:
                raise OSError("short M3U XMLTV settings write")
            offset += written
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
        staged = os.fstat(descriptor)
        if not stat.S_ISREG(staged.st_mode) or staged.st_nlink != 1:
            raise OSError("unsafe staged M3U XMLTV settings file")
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
        os.replace(
            temporary,
            filename,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        try:
            os.fsync(directory_descriptor)
        except OSError:
            # Some Enigma2 filesystems do not implement directory fsync.  The
            # staged regular file itself was still fully flushed before the
            # atomic replacement.
            pass
    except Exception:
        try:
            os.unlink(temporary, dir_fd=directory_descriptor)
        except OSError:
            pass
        raise


def _binding_copy(binding):
    return Binding.from_dict(binding.as_dict())


def load_m3u_epg_bindings(path=M3U_XMLTV_SETTINGS_PATH, strict=False):
    """Load validated bindings; malformed/future data fails closed to empty.

    ``strict`` is reserved for the background scheduler.  It surfaces only
    operational storage failures so a temporary lock or mount error does not
    erase the scheduler's last known-good in-memory configuration.  Invalid,
    oversized and unsupported data remains fail-closed in both modes.
    """
    try:
        with _transaction(path) as (directory_descriptor, filename):
            settings = _load_unlocked(
                directory_descriptor,
                filename,
                unsafe_empty=not bool(strict),
            )
            return [_binding_copy(binding) for binding in settings.bindings]
    except (OSError, NotImplementedError):
        if strict:
            raise
        return []
    except (TypeError, ValueError):
        return []


def get_m3u_epg_binding(source_id, path=M3U_XMLTV_SETTINGS_PATH):
    source_id = _source_id(source_id)
    for binding in load_m3u_epg_bindings(path):
        if binding.source_id == source_id:
            return binding
    return None


def save_m3u_epg_binding(binding, path=M3U_XMLTV_SETTINGS_PATH):
    if not isinstance(binding, Binding):
        raise TypeError("M3U XMLTV binding is invalid")
    candidate = _binding_copy(binding)
    with _transaction(path) as (directory_descriptor, filename):
        settings = _load_unlocked(directory_descriptor, filename)
        output = []
        replaced = False
        for current in settings.bindings:
            if current.source_id == candidate.source_id:
                if not replaced:
                    output.append(candidate)
                    replaced = True
                continue
            output.append(current)
        if not replaced:
            if len(output) >= MAX_M3U_EPG_BINDINGS:
                raise ValueError("Too many M3U XMLTV bindings are configured")
            output.append(candidate)
        _write_unlocked(directory_descriptor, filename, M3UEPGSettings(output))
    return _binding_copy(candidate)


def update_m3u_epg_binding(
    source_id,
    mutator,
    path=M3U_XMLTV_SETTINGS_PATH,
):
    source_id = _source_id(source_id)
    if not callable(mutator):
        raise TypeError("M3U XMLTV binding mutator must be callable")
    with _transaction(path) as (directory_descriptor, filename):
        settings = _load_unlocked(directory_descriptor, filename)
        current = next(
            (
                binding
                for binding in settings.bindings
                if binding.source_id == source_id
            ),
            None,
        )
        if current is None:
            return None
        candidate = mutator(_binding_copy(current))
        if candidate is None:
            return None
        if isinstance(candidate, dict):
            payload = current.as_dict()
            payload.update(candidate)
            candidate = Binding.from_dict(payload)
        elif isinstance(candidate, Binding):
            candidate = _binding_copy(candidate)
        else:
            raise TypeError("M3U XMLTV mutator must return Binding, dict or None")
        if candidate.source_id != source_id:
            raise ValueError("M3U XMLTV source ID cannot be changed")
        output = [
            candidate if binding.source_id == source_id else binding
            for binding in settings.bindings
        ]
        _write_unlocked(directory_descriptor, filename, M3UEPGSettings(output))
    return _binding_copy(candidate)


def delete_m3u_epg_binding(source_id, path=M3U_XMLTV_SETTINGS_PATH):
    source_id = _source_id(source_id)
    with _transaction(path) as (directory_descriptor, filename):
        settings = _load_unlocked(directory_descriptor, filename)
        output = [
            binding
            for binding in settings.bindings
            if binding.source_id != source_id
        ]
        if len(output) == len(settings.bindings):
            return False
        _write_unlocked(directory_descriptor, filename, M3UEPGSettings(output))
    return True

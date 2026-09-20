# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small, Enigma-independent lock for the Live TV category editor."""

import errno
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import threading
import time


DEFAULT_CATEGORY_EDIT_LOCK_PATH = (
    "/etc/enigma2/gtiptvplayer/category-edit-lock.json"
)
MAX_CATEGORY_EDIT_LOCK_BYTES = 16 * 1024
PBKDF2_ITERATIONS = 120000

_FORMAT_VERSION = 1
_SALT_BYTES = 16
_DIGEST_BYTES = 32
_LOCK_TIMEOUT_SECONDS = 2.0
_STORE_LOCK = threading.RLock()
_MISSING = object()
_CORRUPT = object()
_PIN_PATTERN = re.compile(r"\A[0-9]{4}\Z")
_RECOVERY_PATTERN = re.compile(r"\A[0-9]{12}\Z")
_VERIFIER_PATTERN = re.compile(
    r"\Apbkdf2-sha256\$([0-9]+)\$([0-9a-f]{32})\$([0-9a-f]{64})\Z"
)
_PIN_DOMAIN = b"GTIPTV-CATEGORY-PIN\0"
_RECOVERY_DOMAIN = b"GTIPTV-CATEGORY-RECOVERY\0"


def _valid_pin(value):
    return isinstance(value, str) and _PIN_PATTERN.fullmatch(value) is not None


def _normalise_recovery(value):
    if not isinstance(value, str):
        return ""
    value = value.replace("-", "").replace(" ", "")
    return value if _RECOVERY_PATTERN.fullmatch(value) is not None else ""


def _format_recovery(value):
    return "-".join(value[index:index + 4] for index in range(0, 12, 4))


def _derive(secret, salt, domain):
    return hashlib.pbkdf2_hmac(
        "sha256",
        domain + secret.encode("ascii"),
        salt,
        PBKDF2_ITERATIONS,
        dklen=_DIGEST_BYTES,
    )


def _make_verifier(secret, salt, domain):
    digest = _derive(secret, salt, domain)
    return "pbkdf2-sha256${}${}${}".format(
        PBKDF2_ITERATIONS,
        salt.hex(),
        digest.hex(),
    )


def _parse_verifier(value):
    if not isinstance(value, str):
        return None
    matched = _VERIFIER_PATTERN.fullmatch(value)
    if matched is None:
        return None
    try:
        iterations = int(matched.group(1), 10)
        salt = bytes.fromhex(matched.group(2))
        digest = bytes.fromhex(matched.group(3))
    except (TypeError, ValueError, OverflowError):
        return None
    if (
        iterations != PBKDF2_ITERATIONS
        or len(salt) != _SALT_BYTES
        or len(digest) != _DIGEST_BYTES
    ):
        return None
    return salt, digest


def _valid_payload(payload):
    if not isinstance(payload, dict):
        return False
    if set(payload) != {"version", "pin", "recovery"}:
        return False
    if type(payload.get("version")) is not int:
        return False
    if payload.get("version") != _FORMAT_VERSION:
        return False
    return (
        _parse_verifier(payload.get("pin")) is not None
        and _parse_verifier(payload.get("recovery")) is not None
    )


def _strict_json_object(pairs):
    payload = {}
    for key, value in pairs:
        if key in payload:
            raise ValueError("duplicate category edit lock field")
        payload[key] = value
    return payload


class CategoryEditLockStore(object):
    """Persist and verify the optional category-editor PIN."""

    def __init__(self, path=DEFAULT_CATEGORY_EDIT_LOCK_PATH):
        if (
            not isinstance(path, str)
            or not path
            or "\0" in path
            or not os.path.basename(path)
        ):
            raise ValueError("invalid category edit lock path")
        self.path = path

    @property
    def _directory(self):
        return os.path.dirname(self.path) or "."

    @property
    def _filename(self):
        return os.path.basename(self.path)

    @property
    def _lock_filename(self):
        return self._filename + ".lock"

    def _open_parent_directory(self, create):
        directory = self._directory
        if create:
            try:
                listed = os.lstat(directory)
            except FileNotFoundError:
                os.makedirs(directory, mode=0o700, exist_ok=True)
                listed = os.lstat(directory)
        else:
            listed = os.lstat(directory)

        if (
            stat.S_ISLNK(listed.st_mode)
            or not stat.S_ISDIR(listed.st_mode)
            or listed.st_uid != os.geteuid()
            or stat.S_IMODE(listed.st_mode) & 0o022
        ):
            raise OSError(errno.EINVAL, "unsafe category edit lock directory")

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_DIRECTORY", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(directory, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or opened.st_uid != os.geteuid()
                or stat.S_IMODE(opened.st_mode) & 0o022
                or opened.st_dev != listed.st_dev
                or opened.st_ino != listed.st_ino
            ):
                raise OSError(
                    errno.EINVAL,
                    "category edit lock directory changed",
                )
            return descriptor
        except Exception:
            os.close(descriptor)
            raise

    def _acquire_file_lock(self, exclusive, create_parent):
        directory_descriptor = self._open_parent_directory(create_parent)
        lock_descriptor = None
        try:
            flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            lock_descriptor = os.open(
                self._lock_filename,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
            opened = os.fstat(lock_descriptor)
            listed = os.stat(
                self._lock_filename,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_uid != os.geteuid()
                or not stat.S_ISREG(listed.st_mode)
                or opened.st_dev != listed.st_dev
                or opened.st_ino != listed.st_ino
            ):
                raise OSError(errno.EINVAL, "unsafe category edit lock file")
            os.fchmod(lock_descriptor, 0o600)

            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            deadline = time.monotonic() + _LOCK_TIMEOUT_SECONDS
            while True:
                try:
                    fcntl.flock(
                        lock_descriptor,
                        operation | fcntl.LOCK_NB,
                    )
                    break
                except OSError as caught:
                    if caught.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.monotonic() >= deadline:
                        raise OSError(
                            errno.ETIMEDOUT,
                            "category edit lock is busy",
                        )
                    time.sleep(0.025)
            return directory_descriptor, lock_descriptor
        except Exception:
            if lock_descriptor is not None:
                os.close(lock_descriptor)
            os.close(directory_descriptor)
            raise

    @staticmethod
    def _release_file_lock(directory_descriptor, lock_descriptor):
        if lock_descriptor is not None:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except OSError:
                pass
            try:
                os.close(lock_descriptor)
            except OSError:
                pass
        if directory_descriptor is not None:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass

    def _load_locked(self, directory_descriptor):
        try:
            listed = os.stat(
                self._filename,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return _MISSING
        except (OSError, TypeError, ValueError):
            return _CORRUPT

        if (
            not stat.S_ISREG(listed.st_mode)
            or listed.st_nlink != 1
            or listed.st_uid != os.geteuid()
            or stat.S_IMODE(listed.st_mode) & 0o077
            or listed.st_size <= 0
            or listed.st_size > MAX_CATEGORY_EDIT_LOCK_BYTES
        ):
            return _CORRUPT

        descriptor = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                self._filename,
                flags,
                dir_fd=directory_descriptor,
            )
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_dev != listed.st_dev
                or opened.st_ino != listed.st_ino
                or opened.st_size != listed.st_size
                or opened.st_size > MAX_CATEGORY_EDIT_LOCK_BYTES
            ):
                return _CORRUPT
            chunks = []
            remaining = MAX_CATEGORY_EDIT_LOCK_BYTES + 1
            while remaining > 0:
                chunk = os.read(descriptor, min(4096, remaining))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            encoded = b"".join(chunks)
            if len(encoded) > MAX_CATEGORY_EDIT_LOCK_BYTES:
                return _CORRUPT
            payload = json.loads(
                encoded.decode("utf-8", "strict"),
                object_pairs_hook=_strict_json_object,
            )
        except (
            OSError,
            UnicodeError,
            TypeError,
            ValueError,
            MemoryError,
            RecursionError,
        ):
            return _CORRUPT
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass

        return payload if _valid_payload(payload) else _CORRUPT

    def _check_replace_target(self, directory_descriptor):
        try:
            listed = os.stat(
                self._filename,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return
        if (
            not stat.S_ISREG(listed.st_mode)
            or listed.st_nlink != 1
            or listed.st_uid != os.geteuid()
        ):
            raise OSError(errno.EINVAL, "unsafe category edit lock target")

    def _write_locked(self, payload, directory_descriptor):
        if not _valid_payload(payload):
            raise ValueError("invalid category edit lock payload")
        encoded = json.dumps(
            payload,
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("ascii")
        if not encoded or len(encoded) > MAX_CATEGORY_EDIT_LOCK_BYTES:
            raise OSError(errno.EFBIG, "category edit lock payload is too large")

        self._check_replace_target(directory_descriptor)
        temporary = ".{}.tmp-{}-{}".format(
            self._filename,
            os.getpid(),
            secrets.token_hex(8),
        )
        descriptor = None
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                temporary,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
            os.fchmod(descriptor, 0o600)
            offset = 0
            while offset < len(encoded):
                written = os.write(descriptor, encoded[offset:])
                if written <= 0:
                    raise OSError(errno.EIO, "short category edit lock write")
                offset += written
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(
                temporary,
                self._filename,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            try:
                os.fsync(directory_descriptor)
            except OSError:
                pass
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                os.unlink(temporary, dir_fd=directory_descriptor)
            except OSError:
                pass

    def status(self):
        """Return ``disabled``, ``enabled`` or ``corrupt``."""
        with _STORE_LOCK:
            directory_descriptor = None
            lock_descriptor = None
            try:
                directory_descriptor, lock_descriptor = (
                    self._acquire_file_lock(False, False)
                )
                payload = self._load_locked(directory_descriptor)
            except FileNotFoundError:
                return "disabled"
            except (OSError, TypeError, ValueError):
                return "corrupt"
            finally:
                self._release_file_lock(
                    directory_descriptor,
                    lock_descriptor,
                )
        if payload is _MISSING:
            return "disabled"
        return "corrupt" if payload is _CORRUPT else "enabled"

    def configure(self, pin, confirmation):
        """Set or replace the PIN and return the one-time recovery code."""
        if not _valid_pin(pin) or not _valid_pin(confirmation):
            raise ValueError("PIN must contain exactly four ASCII digits")
        if not hmac.compare_digest(pin, confirmation):
            raise ValueError("PIN entries do not match")

        recovery = "".join(secrets.choice("0123456789") for unused in range(12))
        pin_salt = secrets.token_bytes(_SALT_BYTES)
        recovery_salt = secrets.token_bytes(_SALT_BYTES)
        while hmac.compare_digest(pin_salt, recovery_salt):
            recovery_salt = secrets.token_bytes(_SALT_BYTES)
        payload = {
            "version": _FORMAT_VERSION,
            "pin": _make_verifier(pin, pin_salt, _PIN_DOMAIN),
            "recovery": _make_verifier(
                recovery,
                recovery_salt,
                _RECOVERY_DOMAIN,
            ),
        }

        with _STORE_LOCK:
            directory_descriptor = None
            lock_descriptor = None
            try:
                directory_descriptor, lock_descriptor = (
                    self._acquire_file_lock(True, True)
                )
                self._write_locked(payload, directory_descriptor)
            except ValueError:
                raise
            except (OSError, TypeError) as caught:
                if isinstance(caught, OSError):
                    raise
                raise OSError(errno.EIO, "category edit lock could not be saved")
            finally:
                self._release_file_lock(
                    directory_descriptor,
                    lock_descriptor,
                )
        return _format_recovery(recovery)

    def _verify(self, secret, field, domain):
        with _STORE_LOCK:
            directory_descriptor = None
            lock_descriptor = None
            try:
                directory_descriptor, lock_descriptor = (
                    self._acquire_file_lock(False, False)
                )
                payload = self._load_locked(directory_descriptor)
                if payload is _MISSING or payload is _CORRUPT:
                    return False
                parsed = _parse_verifier(payload.get(field))
                if parsed is None:
                    return False
                salt, expected = parsed
                actual = _derive(secret, salt, domain)
                return hmac.compare_digest(actual, expected)
            except (OSError, TypeError, ValueError, UnicodeError):
                return False
            finally:
                self._release_file_lock(
                    directory_descriptor,
                    lock_descriptor,
                )

    def verify_pin(self, pin):
        """Return whether *pin* is the configured four-digit PIN."""
        if not _valid_pin(pin):
            return False
        return self._verify(pin, "pin", _PIN_DOMAIN)

    def verify_recovery(self, code):
        """Accept grouped or ungrouped recovery digits and verify them."""
        recovery = _normalise_recovery(code)
        if not recovery:
            return False
        return self._verify(recovery, "recovery", _RECOVERY_DOMAIN)

    def reset(self):
        """Remove only this lock's state; return False for unsafe targets."""
        with _STORE_LOCK:
            directory_descriptor = None
            lock_descriptor = None
            try:
                directory_descriptor, lock_descriptor = (
                    self._acquire_file_lock(True, False)
                )
                try:
                    listed = os.stat(
                        self._filename,
                        dir_fd=directory_descriptor,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    return True
                if (
                    not stat.S_ISREG(listed.st_mode)
                    or listed.st_nlink != 1
                    or listed.st_uid != os.geteuid()
                ):
                    return False
                os.unlink(self._filename, dir_fd=directory_descriptor)
                try:
                    os.fsync(directory_descriptor)
                except OSError:
                    pass
                return True
            except FileNotFoundError:
                return True
            except (OSError, TypeError, ValueError):
                return False
            finally:
                self._release_file_lock(
                    directory_descriptor,
                    lock_descriptor,
                )

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import json
import os
import socket
import stat
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, quote, urlencode, urlsplit, urlunsplit
from urllib.request import Request

from . import PLUGIN_VERSION
from .diagnostics import log_event
from .http_transport import build_credential_urlopen
from .i18n import N_, _


GT_PLAYLIST_PATH = "/etc/enigma2/gtiptvplayer/playlists.txt"
DEFAULT_PLAYLIST_PATHS = (GT_PLAYLIST_PATH,)
MAX_PLAYLIST_BYTES = 4 * 1024 * 1024
MAX_ACCOUNTS = 500
DEFAULT_TIMEOUT = 7
DEFAULT_M3U_HEALTH_TIMEOUT = 25
DEFAULT_STALKER_HEALTH_TIMEOUT = 12
HEALTH_READ_CHUNK_BYTES = 64 * 1024
MAX_HEALTH_CONNECTION_COUNT = 2147483647
MAX_PROVIDER_INTEGER_DIGITS = 128
MAX_PROVIDER_JSON_NESTING = 128
SUPPORTED_OUTPUT_FORMATS = ("ts", "m3u8")
_PLAYLIST_FILE_MODE = 0o600
_PLAYLIST_DIRECTORY_MODE = 0o700
_PLAYLIST_STAGE_ATTEMPTS = 8
_PLAYLIST_TRANSACTION_LOCK = threading.RLock()
_PLAYLIST_STORAGE_ERROR = N_(
    "Settings could not be saved. Check the receiver storage."
)
SERVER_HEALTH_DETAILS_TEMPLATE = N_(
    "Latency: {} ms\n"
    "Account status: {}\n"
    "Active / Maximum: {}\n"
    "Expiry: {}\n"
    "Server time: {}\n"
    "Time zone: {}\n"
    "Protocol: {}\n"
    "Output format: {}\n"
    "Server version: {}\n\n"
    "{}"
)
_CONNECTION_USAGE_SENTINEL = "__GTIPTV_CONNECTION_USAGE__"


def _bounded_provider_integer(value):
    """Decode one provider JSON integer with a small, fixed digit budget."""
    digit_count = len(value) - (1 if value.startswith("-") else 0)
    if digit_count > MAX_PROVIDER_INTEGER_DIGITS:
        raise ValueError("provider JSON integer is too long")
    return int(value, 10)


def _validate_provider_json_depth(value):
    """Reject provider container trees that exceed the fixed depth budget."""
    if not isinstance(value, (dict, list)):
        return value
    children = value.values() if isinstance(value, dict) else value
    pending = [iter(children)]
    while pending:
        try:
            child = next(pending[-1])
        except StopIteration:
            pending.pop()
            continue
        if not isinstance(child, (dict, list)):
            continue
        if len(pending) >= MAX_PROVIDER_JSON_NESTING:
            raise ValueError("provider JSON nesting is too deep")
        children = child.values() if isinstance(child, dict) else child
        pending.append(iter(children))
    return value


def _normalise_output_format(value, fallback="ts"):
    value = str(value or "").strip().lower()
    return value if value in SUPPORTED_OUTPUT_FORMATS else fallback


class PlaylistAccount(object):
    source_type = "xtream"
    capabilities = frozenset(
        ("live", "movie", "series", "continue", "favorites")
    )

    def __init__(
        self,
        name,
        scheme,
        netloc,
        base_path,
        username,
        password,
        output_format="ts",
        source_path="",
    ):
        self.name = name
        self.scheme = scheme
        self.netloc = netloc
        self.base_path = base_path
        self.username = username
        self.password = password
        self.output_format = _normalise_output_format(output_format)
        self.source_path = str(source_path or "")

    @property
    def host(self):
        try:
            return (
                urlsplit("{}://{}".format(self.scheme, self.netloc)).hostname
                or self.netloc
            )
        except ValueError:
            return self.netloc

    @property
    def display_name(self):
        return self.name or self.host

    @property
    def display_endpoint(self):
        path = self.base_path.rstrip("/")
        return urlunsplit(
            (self.scheme, self.netloc, path or "/", "", "")
        )

    @property
    def source_label(self):
        return "XTREAM • {}".format(self.output_format.upper())

    def supports(self, capability):
        return str(capability or "").lower() in self.capabilities

    @property
    def identity(self):
        return (
            self.scheme.lower(),
            self.netloc.lower(),
            self.base_path,
            self.username,
        )

    def api_url(self, action="", parameters=None):
        path = (self.base_path.rstrip("/") + "/player_api.php").replace("//", "/")
        query_values = {
            "username": self.username,
            "password": self.password,
        }
        if action:
            query_values["action"] = action
        if parameters:
            query_values.update(parameters)
        query = urlencode(query_values)
        return urlunsplit((self.scheme, self.netloc, path, query, ""))

    def stream_url(self, content_type, stream_id, extension="ts"):
        content_type = str(content_type or "").lower()
        if content_type not in ("live", "movie", "series"):
            raise ValueError(N_("Unsupported content type"))
        stream_id = str(stream_id or "").strip()
        if not stream_id or not stream_id.isdigit():
            raise ValueError(N_("Invalid stream ID"))
        if content_type == "live":
            # The account's selected Xtream output must control both preview
            # and fullscreen playback. Live API entries often omit a
            # container extension, so trusting the item fallback would force
            # every account back to TS.
            extension = self.output_format
        else:
            extension = str(extension or "").lower().lstrip(".")
        if not extension or not extension.replace("_", "").isalnum():
            extension = "ts" if content_type == "live" else "mp4"
        path = "{}/{}/{}/{}/{}.{}".format(
            self.base_path.rstrip("/"),
            content_type,
            quote(self.username, safe=""),
            quote(self.password, safe=""),
            stream_id,
            extension,
        ).replace("//", "/")
        return urlunsplit((self.scheme, self.netloc, path, "", ""))


class PlaylistLoadResult(object):
    def __init__(self, accounts=None, sources=None, error=""):
        self.accounts = accounts or []
        self.sources = sources or []
        self.error = error


class ServerHealth(object):
    def __init__(
        self,
        status,
        latency_ms=0,
        account_status="",
        active_connections=None,
        max_connections=None,
        expiry="",
        server_time="",
        detail="",
        timezone="",
        protocol="",
        output_format="",
        server_version="",
        source_type="xtream",
        source_kind="",
        source_size=None,
        item_count=None,
        category_count=None,
        cache_age="",
        modified="",
    ):
        self.status = status
        self.latency_ms = latency_ms
        self.account_status = account_status
        self.active_connections = active_connections
        self.max_connections = max_connections
        self.expiry = expiry
        self.server_time = server_time
        self.detail = detail
        self.timezone = timezone
        self.protocol = protocol
        self.output_format = output_format
        self.server_version = server_version
        self.source_type = str(source_type or "xtream")
        self.source_kind = str(source_kind or "")
        self.source_size = source_size
        self.item_count = item_count
        self.category_count = category_count
        self.cache_age = str(cache_age or "")
        self.modified = str(modified or "")

    def connection_text(self):
        if self.active_connections is None and self.max_connections is None:
            return N_("Unknown")
        active = "?" if self.active_connections is None else str(self.active_connections)
        maximum = "?" if self.max_connections is None else str(self.max_connections)
        return "{} / {}".format(active, maximum)


def localized_connection_usage_line(value, translator=None):
    """Reuse the translated health label without adding a catalog key."""
    translate = translator or _
    value = str(value or "")
    try:
        rendered = translate(SERVER_HEALTH_DETAILS_TEMPLATE).format(
            "",
            "",
            _CONNECTION_USAGE_SENTINEL,
            "",
            "",
            "",
            "",
            "",
            "",
            "",
        )
    except (IndexError, KeyError, ValueError):
        return value
    for line in rendered.splitlines():
        if _CONNECTION_USAGE_SENTINEL in line:
            return line.replace(_CONNECTION_USAGE_SENTINEL, value)
    return value


def _current_uid(status):
    return getattr(os, "geteuid", lambda: status.st_uid)()


def _is_canonical_playlist_directory(path):
    directory = os.path.abspath(os.path.dirname(path) or ".")
    canonical = os.path.abspath(os.path.dirname(GT_PLAYLIST_PATH))
    return directory == canonical


def _validate_playlist_directory(status, require_private_owner):
    if (
        stat.S_ISLNK(status.st_mode)
        or not stat.S_ISDIR(status.st_mode)
        or (
            require_private_owner
            and status.st_uid != _current_uid(status)
        )
    ):
        raise OSError(_PLAYLIST_STORAGE_ERROR)


def _open_playlist_directory(path, create):
    directory = os.path.dirname(path) or "."
    require_private_owner = _is_canonical_playlist_directory(path)
    if create:
        try:
            os.makedirs(
                directory,
                _PLAYLIST_DIRECTORY_MODE if require_private_owner else 0o755,
            )
        except OSError:
            try:
                listed = os.lstat(directory)
            except OSError:
                raise OSError(_PLAYLIST_STORAGE_ERROR)
            _validate_playlist_directory(listed, require_private_owner)
    try:
        listed = os.lstat(directory)
    except OSError:
        raise OSError(_PLAYLIST_STORAGE_ERROR)
    _validate_playlist_directory(listed, require_private_owner)

    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(directory, flags)
    except OSError:
        raise OSError(_PLAYLIST_STORAGE_ERROR)
    try:
        opened = os.fstat(descriptor)
        _validate_playlist_directory(opened, require_private_owner)
        if opened.st_dev != listed.st_dev or opened.st_ino != listed.st_ino:
            raise OSError(_PLAYLIST_STORAGE_ERROR)
        if create and require_private_owner:
            if stat.S_IMODE(opened.st_mode) != _PLAYLIST_DIRECTORY_MODE:
                os.fchmod(descriptor, _PLAYLIST_DIRECTORY_MODE)
                opened = os.fstat(descriptor)
            if stat.S_IMODE(opened.st_mode) != _PLAYLIST_DIRECTORY_MODE:
                raise OSError(_PLAYLIST_STORAGE_ERROR)
    except Exception:
        os.close(descriptor)
        raise
    return directory, descriptor


def _validate_playlist_file(status):
    if (
        not stat.S_ISREG(status.st_mode)
        or status.st_uid != _current_uid(status)
        or status.st_nlink != 1
    ):
        raise OSError(_PLAYLIST_STORAGE_ERROR)


def _playlist_status(directory_descriptor, filename):
    try:
        details = os.stat(
            filename,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return None
    _validate_playlist_file(details)
    return details


def _same_playlist_file(left, right):
    if left is None or right is None:
        return left is right
    return (
        left.st_dev,
        left.st_ino,
        left.st_size,
        getattr(left, "st_mtime_ns", int(left.st_mtime * 1000000000)),
    ) == (
        right.st_dev,
        right.st_ino,
        right.st_size,
        getattr(right, "st_mtime_ns", int(right.st_mtime * 1000000000)),
    )


def _read_playlist_payload(path, maximum=MAX_PLAYLIST_BYTES):
    directory, directory_descriptor = _open_playlist_directory(path, False)
    del directory
    descriptor = None
    try:
        filename = os.path.basename(path)
        listed = _playlist_status(directory_descriptor, filename)
        if listed is None:
            return None, None
        if listed.st_size > maximum:
            raise ValueError(N_("Playlist file is too large"))
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(filename, flags, dir_fd=directory_descriptor)
        opened = os.fstat(descriptor)
        _validate_playlist_file(opened)
        if opened.st_dev != listed.st_dev or opened.st_ino != listed.st_ino:
            raise OSError(_PLAYLIST_STORAGE_ERROR)
        with os.fdopen(descriptor, "rb") as handle:
            descriptor = None
            payload = handle.read(maximum + 1)
        if len(payload) > maximum:
            raise ValueError(N_("Playlist file is too large"))
        return payload, opened
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_descriptor)


def _fsync_playlist_directory(directory_descriptor):
    try:
        os.fsync(directory_descriptor)
    except OSError:
        # Some receiver filesystems do not support fsync on directories.
        pass


def _open_playlist_stage(directory_descriptor, filename, flags):
    thread_id = threading.current_thread().ident or 0
    monotonic_ns = getattr(time, "monotonic_ns", None)
    for attempt in range(_PLAYLIST_STAGE_ATTEMPTS):
        timestamp = (
            monotonic_ns()
            if monotonic_ns is not None
            else int(time.monotonic() * 1000000000)
        )
        temporary_name = ".{}.{}.{}.{}.{}.tmp".format(
            filename,
            os.getpid(),
            thread_id,
            timestamp,
            attempt,
        )
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                _PLAYLIST_FILE_MODE,
                dir_fd=directory_descriptor,
            )
        except FileExistsError:
            continue
        return temporary_name, descriptor
    raise OSError(_PLAYLIST_STORAGE_ERROR)


class _PlaylistWriteStage(object):
    __slots__ = (
        "directory_descriptor",
        "filename",
        "temporary_name",
        "temporary_status",
        "expected_status",
        "closed",
    )

    def __init__(
        self,
        directory_descriptor,
        filename,
        expected_status,
    ):
        self.directory_descriptor = directory_descriptor
        self.filename = filename
        self.temporary_name = ""
        self.temporary_status = None
        self.expected_status = expected_status
        self.closed = False


def _discard_playlist_stage(stage):
    if stage is None or stage.closed:
        return
    try:
        staged = None
        if stage.temporary_name:
            try:
                staged = os.stat(
                    stage.temporary_name,
                    dir_fd=stage.directory_descriptor,
                    follow_symlinks=False,
                )
            except OSError:
                pass
        if (
            staged is not None
            and stage.temporary_status is not None
            and stat.S_ISREG(staged.st_mode)
            and staged.st_dev == stage.temporary_status.st_dev
            and staged.st_ino == stage.temporary_status.st_ino
        ):
            try:
                os.unlink(
                    stage.temporary_name,
                    dir_fd=stage.directory_descriptor,
                )
            except OSError:
                pass
    finally:
        os.close(stage.directory_descriptor)
        stage.closed = True


def _prepare_playlist_write(path, payload, expected_status):
    unused_directory, directory_descriptor = _open_playlist_directory(path, True)
    del unused_directory
    stage = _PlaylistWriteStage(
        directory_descriptor,
        os.path.basename(path),
        expected_status,
    )
    descriptor = None
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        stage.temporary_name, descriptor = _open_playlist_stage(
            directory_descriptor,
            stage.filename,
            flags,
        )
        stage.temporary_status = os.fstat(descriptor)
        _validate_playlist_file(stage.temporary_status)
        offset = 0
        while offset < len(payload):
            written = os.write(descriptor, payload[offset:])
            if written <= 0:
                raise OSError(_PLAYLIST_STORAGE_ERROR)
            offset += written
        os.fchmod(descriptor, _PLAYLIST_FILE_MODE)
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None

        current_status = _playlist_status(
            directory_descriptor,
            stage.filename,
        )
        if not _same_playlist_file(current_status, expected_status):
            raise OSError(_PLAYLIST_STORAGE_ERROR)
        return stage
    except Exception:
        if descriptor is not None:
            os.close(descriptor)
        _discard_playlist_stage(stage)
        raise


def _commit_playlist_stage(stage):
    if stage is None or stage.closed:
        raise OSError(_PLAYLIST_STORAGE_ERROR)
    current_status = _playlist_status(
        stage.directory_descriptor,
        stage.filename,
    )
    if not _same_playlist_file(current_status, stage.expected_status):
        raise OSError(_PLAYLIST_STORAGE_ERROR)
    os.replace(
        stage.temporary_name,
        stage.filename,
        src_dir_fd=stage.directory_descriptor,
        dst_dir_fd=stage.directory_descriptor,
    )
    installed = _playlist_status(
        stage.directory_descriptor,
        stage.filename,
    )
    if (
        installed is None
        or installed.st_dev != stage.temporary_status.st_dev
        or installed.st_ino != stage.temporary_status.st_ino
    ):
        raise OSError(_PLAYLIST_STORAGE_ERROR)
    _fsync_playlist_directory(stage.directory_descriptor)


def _atomic_write_playlist(path, payload, expected_status):
    stage = _prepare_playlist_write(path, payload, expected_status)
    try:
        _commit_playlist_stage(stage)
    finally:
        _discard_playlist_stage(stage)


def ensure_playlist_file(path=GT_PLAYLIST_PATH):
    """Create the private GT playlist file without replacing existing data."""
    unused_directory, directory_descriptor = _open_playlist_directory(path, True)
    del unused_directory
    filename = os.path.basename(path)
    descriptor = None
    created = False
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        try:
            descriptor = os.open(
                filename,
                flags,
                _PLAYLIST_FILE_MODE,
                dir_fd=directory_descriptor,
            )
        except FileExistsError:
            listed = _playlist_status(directory_descriptor, filename)
            if listed is None:
                raise OSError(_PLAYLIST_STORAGE_ERROR)
            read_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            read_flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                filename,
                read_flags,
                dir_fd=directory_descriptor,
            )
            opened = os.fstat(descriptor)
            _validate_playlist_file(opened)
            if opened.st_dev != listed.st_dev or opened.st_ino != listed.st_ino:
                raise OSError(_PLAYLIST_STORAGE_ERROR)
        else:
            opened = os.fstat(descriptor)
            _validate_playlist_file(opened)
            created = True
        mode_changed = stat.S_IMODE(opened.st_mode) != _PLAYLIST_FILE_MODE
        if mode_changed:
            os.fchmod(descriptor, _PLAYLIST_FILE_MODE)
        if created or mode_changed:
            os.fsync(descriptor)
        if created:
            _fsync_playlist_directory(directory_descriptor)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(directory_descriptor)
    return created


def _clean_name(fragment):
    value = (fragment or "").replace("\r", " ").replace("\n", " ").strip()
    if value.startswith("#"):
        value = value[1:].strip()
    return value[:80]


def _split_line_and_name(line):
    value = (line or "").strip()
    if not value or value.startswith("#"):
        return "", ""
    marker = value.find(" #")
    if marker >= 0:
        return value[:marker].strip(), _clean_name(value[marker + 1 :])
    return value, ""


def parse_playlist_line(line):
    raw_url, name = _split_line_and_name(line)
    if not raw_url:
        return None
    try:
        parsed = urlsplit(raw_url)
    except Exception:
        return None
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        return None
    try:
        if not parsed.hostname or parsed.username or parsed.password:
            return None
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            return None
    except ValueError:
        return None
    query = parse_qs(parsed.query, keep_blank_values=True)
    username = (query.get("username") or [""])[0].strip()
    password = (query.get("password") or [""])[0].strip()
    output_format = _normalise_output_format(
        (query.get("output") or ["ts"])[0]
    )
    if not username or not password:
        return None
    path = parsed.path or ""
    if "/" in path:
        base_path = path.rsplit("/", 1)[0]
    else:
        base_path = ""
    return PlaylistAccount(
        name=name,
        scheme=parsed.scheme.lower(),
        netloc=parsed.netloc,
        base_path=base_path,
        username=username,
        password=password,
        output_format=output_format,
    )


def _playlist_url(account):
    path = (account.base_path.rstrip("/") + "/get.php").replace("//", "/")
    query = urlencode(
        (
            ("username", account.username),
            ("password", account.password),
            ("type", "m3u_plus"),
            (
                "output",
                _normalise_output_format(
                    getattr(account, "output_format", "ts")
                ),
            ),
        )
    )
    return urlunsplit((account.scheme, account.netloc, path, query, ""))


def save_account(url, name="", path=None):
    """Validate and atomically save an Xtream account on the receiver."""
    register_path = path is None
    if register_path:
        from .playlist_files import playlist_write_path

        path = playlist_write_path("xtream", GT_PLAYLIST_PATH)
    with _PLAYLIST_TRANSACTION_LOCK:
        account = _save_account_locked(url, name, path)
    account.source_path = path
    if register_path:
        from .playlist_files import register_written_playlist

        register_written_playlist("xtream", path)
    return account


def _save_account_locked(url, name, path):
    account = parse_playlist_line(url)
    if account is None:
        raise ValueError(N_("Enter a valid Xtream URL"))

    requested_name = _clean_name(name)
    unused_directory, directory_descriptor = _open_playlist_directory(path, True)
    del unused_directory
    os.close(directory_descriptor)
    existing, existing_status = _read_playlist_payload(path)
    existing_lines = (
        existing.decode("utf-8-sig", "replace").splitlines()
        if existing is not None
        else []
    )

    # The limit applies only to a genuinely new identity.  Updating an
    # existing account must remain possible at the limit (and even for a
    # hand-maintained file that already exceeds it).  Duplicate lines count
    # once and are folded into the replacement below.
    existing_identities = set()
    existing_name = ""
    for line in existing_lines:
        parsed = parse_playlist_line(line)
        if parsed is not None:
            existing_identities.add(parsed.identity)
            if parsed.identity == account.identity and not existing_name:
                existing_name = parsed.name
    if (
        account.identity not in existing_identities
        and len(existing_identities) >= MAX_ACCOUNTS
    ):
        raise ValueError(N_("The Xtream account limit was reached"))

    clean_name = requested_name or existing_name or account.display_name
    account.name = clean_name

    saved_line = "{} # {}".format(_playlist_url(account), clean_name)
    output_lines = []
    replaced = False
    for line in existing_lines:
        parsed = parse_playlist_line(line)
        if parsed is not None and parsed.identity == account.identity:
            if not replaced:
                output_lines.append(saved_line)
                replaced = True
            continue
        output_lines.append(line)
    if not replaced:
        output_lines.append(saved_line)

    payload = ("\n".join(output_lines).rstrip("\n") + "\n").encode("utf-8")
    if len(payload) > MAX_PLAYLIST_BYTES:
        raise ValueError(N_("Playlist file is too large"))

    _atomic_write_playlist(path, payload, existing_status)
    return account


def save_account_credentials(
    server_url,
    username,
    password,
    name="",
    path=None,
    output_format="ts",
):
    """Build and save an Xtream account from separate, keyboard-friendly fields."""
    server_url = str(server_url or "").strip()
    username = str(username or "").strip()
    password = str(password or "").strip()
    if not server_url or not username or not password:
        raise ValueError(N_("Server URL, username and password are required"))
    output_format = str(output_format or "").strip().lower()
    if output_format not in SUPPORTED_OUTPUT_FORMATS:
        raise ValueError(N_("Output format must be TS or M3U8"))

    try:
        parsed = urlsplit(server_url)
    except Exception:
        raise ValueError(N_("Enter a valid server URL"))
    if parsed.scheme.lower() not in ("http", "https") or not parsed.netloc:
        raise ValueError(N_("Server URL must start with http:// or https://"))
    try:
        if not parsed.hostname or parsed.username or parsed.password:
            raise ValueError(N_("Do not include a username or password in the server URL"))
        port = parsed.port
        if port is not None and not 1 <= port <= 65535:
            raise ValueError(N_("Server URL port is invalid"))
    except ValueError as error:
        raise ValueError(str(error))

    base_path = (parsed.path or "").rstrip("/")
    if base_path.lower().endswith("/get.php"):
        base_path = base_path[:-8]
    elif base_path.lower().endswith("/player_api.php"):
        base_path = base_path[:-15]
    path_value = (base_path.rstrip("/") + "/get.php").replace("//", "/")
    query = urlencode(
        (
            ("username", username),
            ("password", password),
            ("type", "m3u_plus"),
            ("output", output_format),
        )
    )
    playlist_url = urlunsplit(
        (parsed.scheme.lower(), parsed.netloc, path_value, query, "")
    )
    return save_account(playlist_url, name, path)


def delete_account(account, paths=None):
    """Atomically remove one account identity from the GT playlist file."""
    with _PLAYLIST_TRANSACTION_LOCK:
        return _delete_account_locked(account, paths)


def _check_playlist_mutation_limits(cancel_event=None, deadline=None):
    if cancel_event is not None and cancel_event.is_set():
        raise OSError(N_("Request cancelled"))
    if deadline is None:
        return
    try:
        expired = time.monotonic() >= float(deadline)
    except (TypeError, ValueError, OverflowError):
        expired = True
    if expired:
        raise OSError(N_("Request timed out"))


def delete_accounts(
    accounts,
    paths=None,
    cancel_event=None,
    deadline=None,
):
    """Atomically remove several Xtream identities with one file rewrite.

    The source manager can test hundreds of entries. Rewriting the same TXT
    file once per inactive account would be needlessly slow and could leave a
    partially processed list, so all requested identities are validated and
    staged together before the authoritative replace.
    """
    requested = tuple(accounts or ())
    if not requested:
        return 0
    identities = set()
    for account in requested:
        identity = getattr(account, "identity", account)
        if not isinstance(identity, tuple) or len(identity) != 4:
            raise ValueError(N_("The account selected for deletion is invalid"))
        identities.add(identity)

    if paths is not None:
        candidates = tuple(dict.fromkeys(str(path) for path in paths if path))
        identities_by_path = {
            path: set(identities)
            for path in candidates
        }
    else:
        identities_by_path = {}
        unresolved = set()
        for account in requested:
            identity = getattr(account, "identity", account)
            source_path = str(getattr(account, "source_path", "") or "")
            if source_path:
                identities_by_path.setdefault(source_path, set()).add(identity)
            else:
                unresolved.add(identity)
        if unresolved:
            try:
                from .playlist_files import selected_playlist_paths

                candidates = selected_playlist_paths("xtream")
            except (IOError, OSError, TypeError, ValueError):
                candidates = ()
            if not candidates:
                candidates = DEFAULT_PLAYLIST_PATHS
            for path in candidates:
                identities_by_path.setdefault(path, set()).update(unresolved)

    _check_playlist_mutation_limits(cancel_event, deadline)
    updates = []
    found = set()
    removed_count = 0
    with _PLAYLIST_TRANSACTION_LOCK:
        for path, target_identities in identities_by_path.items():
            if not os.path.lexists(path):
                continue
            content, existing_status = _read_playlist_payload(path)
            if content is None:
                continue
            output_lines = []
            removed_here = 0
            for line in content.decode("utf-8-sig", "replace").splitlines():
                parsed = parse_playlist_line(line)
                if parsed is not None and parsed.identity in target_identities:
                    found.add(parsed.identity)
                    removed_here += 1
                    continue
                output_lines.append(line)
            if removed_here:
                payload = (
                    ("\n".join(output_lines).rstrip("\n") + "\n").encode("utf-8")
                    if output_lines
                    else b""
                )
                updates.append((path, payload, existing_status))
                removed_count += removed_here

        if found != identities:
            raise ValueError(
                N_("The selected account was not found in the account files")
            )
        _check_playlist_mutation_limits(cancel_event, deadline)
        stages = []
        try:
            for path, payload, existing_status in updates:
                stages.append(
                    _prepare_playlist_write(path, payload, existing_status)
                )
            _check_playlist_mutation_limits(cancel_event, deadline)
            # Once the first replace starts, finish every prepared commit. A
            # late cancellation must not report failure after durable data has
            # already changed.
            for stage in stages:
                _commit_playlist_stage(stage)
        finally:
            for stage in stages:
                _discard_playlist_stage(stage)
    return removed_count


def _delete_account_locked(account, paths):
    identity = getattr(account, "identity", account)
    if not isinstance(identity, tuple) or len(identity) != 4:
        raise ValueError(N_("The account selected for deletion is invalid"))

    if paths is None:
        source_path = str(getattr(account, "source_path", "") or "")
        if source_path:
            candidates = (source_path,)
        else:
            try:
                from .playlist_files import selected_playlist_paths

                candidates = selected_playlist_paths("xtream")
            except (IOError, OSError, TypeError, ValueError):
                candidates = ()
            if not candidates:
                candidates = DEFAULT_PLAYLIST_PATHS
    else:
        candidates = tuple(paths)
    updates = []
    removed_count = 0
    for path in candidates:
        if not os.path.lexists(path):
            continue
        content, existing_status = _read_playlist_payload(path)
        if content is None:
            continue

        lines = content.decode("utf-8-sig", "replace").splitlines()
        output_lines = []
        removed_here = 0
        for line in lines:
            parsed = parse_playlist_line(line)
            if parsed is not None and parsed.identity == identity:
                removed_here += 1
                continue
            output_lines.append(line)
        if removed_here:
            payload = (
                ("\n".join(output_lines).rstrip("\n") + "\n").encode("utf-8")
                if output_lines
                else b""
            )
            updates.append((path, payload, existing_status))
            removed_count += removed_here

    if not removed_count:
        raise ValueError(N_("The selected account was not found in the account files"))

    stages = []
    try:
        for path, payload, existing_status in updates:
            stages.append(
                _prepare_playlist_write(path, payload, existing_status)
            )
        for stage in stages:
            _commit_playlist_stage(stage)
    finally:
        for stage in stages:
            _discard_playlist_stage(stage)
    return removed_count


def load_accounts(paths=None):
    if paths is None:
        try:
            from .playlist_files import selected_playlist_paths

            candidates = selected_playlist_paths("xtream")
        except (IOError, OSError, TypeError, ValueError) as error:
            return PlaylistLoadResult([], [], str(error))
    else:
        candidates = tuple(paths)
    accounts = []
    sources = []
    seen = set()
    readable_file_found = False

    for path in candidates:
        try:
            if not os.path.lexists(path):
                continue
            content, unused_status = _read_playlist_payload(path)
            del unused_status
        except (IOError, OSError, ValueError):
            continue
        if content is None:
            continue
        readable_file_found = True
        lines = content.decode("utf-8-sig", "replace").splitlines()
        sources.append(path)
        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            account = parse_playlist_line(stripped)
            if account is None:
                continue
            if account.identity in seen:
                continue
            seen.add(account.identity)
            account.source_path = path
            accounts.append(account)

    error = ""
    if not readable_file_found:
        error = N_("playlists.txt was not found")
    elif not accounts:
        error = N_("No valid Xtream account was found")
    return PlaylistLoadResult(accounts, sources, error)


def load_sources(paths=None, m3u_path=None, portal_path=None):
    """Load Xtream, M3U and Stalker/MAC sources as one UI collection."""
    xtream_result = load_accounts(paths)
    sources = list(xtream_result.accounts)
    source_files = list(xtream_result.sources)
    m3u_error = ""
    try:
        from .m3u import M3U_SOURCE_PATH, load_m3u_sources

        registry_path = m3u_path or M3U_SOURCE_PATH
        m3u_sources = load_m3u_sources(registry_path)
        sources.extend(m3u_sources)
        if os.path.isfile(registry_path):
            source_files.append(registry_path)
    except (IOError, OSError, TypeError, ValueError) as error:
        m3u_error = str(error)

    portal_error = ""
    try:
        from .stalker import load_portal_accounts

        portal_result = (
            load_portal_accounts()
            if portal_path is None
            else load_portal_accounts(portal_path)
        )
        sources.extend(portal_result.accounts)
        for source_file in portal_result.sources:
            if source_file not in source_files:
                source_files.append(source_file)
        if not portal_result.accounts:
            portal_error = str(portal_result.error or "")
    except (IOError, OSError, TypeError, ValueError) as error:
        portal_error = str(error)

    # An empty playlists.txt is created by older releases.  It must not turn
    # a valid M3U-only setup into an error state.
    error = ""
    if not sources:
        if portal_error and not source_files:
            error = portal_error
        elif m3u_error:
            error = m3u_error
        elif xtream_result.error and not source_files:
            error = N_("No saved IPTV source was found")
        else:
            error = N_("No valid IPTV source was found")
    return PlaylistLoadResult(sources, source_files, error)


def delete_source(
    source,
    paths=None,
    m3u_path=None,
    m3u_cache_dir=None,
    m3u_epg_settings_path=None,
    m3u_epg_cache_dir=None,
    portal_path=None,
    cancel_event=None,
    deadline=None,
):
    """Delete one source without exposing its storage format to the UI."""
    source_type = str(
        getattr(source, "source_type", "xtream") or "xtream"
    ).lower()
    if source_type == "stalker":
        from .stalker import delete_portal_account

        return delete_portal_account(
            source,
            path=portal_path,
            cancel_event=cancel_event,
            deadline=deadline,
        )
    if source_type != "m3u":
        return delete_account(source, paths=paths)

    from .m3u import M3U_CACHE_DIR, M3U_SOURCE_PATH, delete_m3u_source

    registry_path = m3u_path or M3U_SOURCE_PATH
    cache_dir = m3u_cache_dir or M3U_CACHE_DIR
    if not delete_m3u_source(
        source,
        path=registry_path,
        cache_dir=cache_dir,
        cancel_event=cancel_event,
        deadline=deadline,
    ):
        raise ValueError(N_("The selected M3U source was not found"))

    # The playlist registry is authoritative.  Once its transaction succeeds,
    # discard only plugin-owned XMLTV metadata and snapshots on a best-effort
    # basis.  The configured XMLTV document itself is user data and is never
    # removed here.
    source_id = str(getattr(source, "source_id", source) or "").strip()
    try:
        from .m3u_epg_settings import (
            M3U_XMLTV_SETTINGS_PATH,
            delete_m3u_epg_binding,
        )

        delete_m3u_epg_binding(
            source_id,
            path=m3u_epg_settings_path or M3U_XMLTV_SETTINGS_PATH,
        )
    except (IOError, OSError, TypeError, ValueError, NotImplementedError) as error:
        log_event("m3u_epg", "Deleted M3U XMLTV settings could not be cleaned", error)
    try:
        from .m3u_epg import (
            M3U_EPG_CACHE_DIRECTORY,
            delete_snapshot,
            get_default_store,
        )

        delete_snapshot(
            source_id,
            cache_dir=m3u_epg_cache_dir or M3U_EPG_CACHE_DIRECTORY,
            store=get_default_store(),
        )
    except (IOError, OSError, TypeError, ValueError, RuntimeError, NotImplementedError) as error:
        log_event("m3u_epg", "Deleted M3U XMLTV snapshot could not be cleaned", error)
    return 1


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_health_count(value, positive=False):
    """Accept only plain, non-negative decimal counters from account data."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        parsed = value
    elif isinstance(value, str):
        cleaned = value.strip()
        if len(cleaned) > 10 or not cleaned or any(
            character < "0" or character > "9"
            for character in cleaned
        ):
            return None
        try:
            parsed = int(cleaned, 10)
        except (TypeError, ValueError, OverflowError):
            return None
    else:
        return None
    if (
        parsed < 0
        or parsed > MAX_HEALTH_CONNECTION_COUNT
        or (positive and parsed <= 0)
    ):
        return None
    return parsed


def _safe_expiry(value):
    timestamp = _as_int(value)
    if not timestamp or timestamp <= 0:
        return N_("Never / unknown")
    try:
        return time.strftime("%d.%m.%Y %H:%M", time.localtime(timestamp))
    except (OverflowError, OSError, ValueError):
        return N_("Unknown")


def _health_from_payload(payload, latency_ms, account=None):
    if not isinstance(payload, dict):
        return ServerHealth(N_("ERROR"), latency_ms, detail=N_("API response is invalid"))
    user = payload.get("user_info")
    server = payload.get("server_info")
    if user is None:
        user = {}
    if server is None:
        server = {}
    if not isinstance(user, dict) or not isinstance(server, dict):
        return ServerHealth(
            N_("ERROR"),
            latency_ms,
            detail=N_("API response is invalid"),
        )
    authenticated = str(user.get("auth", "0")).lower() in ("1", "true")
    account_status = str(user.get("status") or "").strip()
    active = _as_health_count(user.get("active_cons"))
    maximum = _as_health_count(user.get("max_connections"), positive=True)
    expiry = _safe_expiry(user.get("exp_date"))
    allowed_outputs = user.get("allowed_output_formats") or []
    if isinstance(allowed_outputs, (list, tuple)):
        output_format = ", ".join(str(value) for value in allowed_outputs if value)
    else:
        output_format = str(allowed_outputs or "")
    protocol = str(getattr(account, "scheme", "") or "").upper()

    if not authenticated:
        status = N_("INVALID")
        detail = N_("Server did not validate the account")
    elif account_status.lower() in (
        "expired",
        "disabled",
        "banned",
        "blocked",
        "inactive",
    ):
        status = N_("DISABLED")
        detail = _(N_("Account status: {}")).format(account_status)
    elif (
        maximum is not None
        and active is not None
        and active >= maximum
    ):
        status = N_("BUSY")
        detail = N_("Connection limit reached")
    else:
        status = "ONLINE"
        detail = N_("API connection successful")

    return ServerHealth(
        status=status,
        latency_ms=latency_ms,
        account_status=account_status or N_("Unknown"),
        active_connections=active,
        max_connections=maximum,
        expiry=expiry,
        server_time=str(server.get("time_now") or N_("Unknown")),
        detail=detail,
        timezone=str(server.get("timezone") or N_("Unknown")),
        protocol=protocol or str(server.get("protocol") or N_("Unknown")).upper(),
        output_format=output_format or N_("Unknown"),
        server_version=str(
            server.get("version") or payload.get("version") or N_("Unknown")
        ),
    )


def _set_response_timeout(response, timeout):
    """Best-effort deadline update for urllib's underlying socket."""
    fp = getattr(response, "fp", None)
    raw = getattr(fp, "raw", None)
    candidates = (
        getattr(raw, "_sock", None),
        getattr(raw, "sock", None),
        getattr(fp, "_sock", None),
        getattr(fp, "sock", None),
    )
    seen = set()
    for candidate in candidates:
        if candidate is None or id(candidate) in seen:
            continue
        seen.add(id(candidate))
        setter = getattr(candidate, "settimeout", None)
        if not callable(setter):
            continue
        try:
            setter(float(timeout))
        except (OSError, TypeError, ValueError):
            continue
        return


def _read_health_response(
    response,
    deadline,
    clock,
    cancel_event=None,
):
    """Read the bounded health payload without extending its total timeout."""
    read1 = getattr(response, "read1", None)
    chunks = []
    total = 0
    limit = MAX_PLAYLIST_BYTES + 1
    while total < limit:
        if cancel_event is not None and cancel_event.is_set():
            raise OSError(N_("Request cancelled"))
        remaining = float(deadline) - clock()
        if remaining <= 0:
            raise socket.timeout()
        _set_response_timeout(response, remaining)
        size = min(HEALTH_READ_CHUNK_BYTES, limit - total)
        if callable(read1):
            chunk = read1(size)
        else:
            chunk = response.read(size)
        if cancel_event is not None and cancel_event.is_set():
            raise OSError(N_("Request cancelled"))
        if clock() >= float(deadline):
            raise socket.timeout()
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        elif not isinstance(chunk, bytes):
            chunk = bytes(chunk)
        chunks.append(chunk)
        total += len(chunk)
    return b"".join(chunks)


def check_server_health(
    account,
    timeout=DEFAULT_TIMEOUT,
    opener=None,
    clock=None,
    cancel_event=None,
):
    opener = opener or build_credential_urlopen(
        ("username", "password", "token")
    )
    clock = clock or time.monotonic
    started = clock()
    request = Request(
        account.api_url(),
        headers={
            "Accept": "application/json",
            "User-Agent": "GT-IPTV-Player-Pro/{}".format(PLUGIN_VERSION),
        },
    )
    try:
        if cancel_event is not None and cancel_event.is_set():
            raise OSError(N_("Request cancelled"))
        deadline = started + float(timeout)
        remaining = deadline - clock()
        if remaining <= 0:
            raise socket.timeout()
        response = opener(request, timeout=min(float(timeout), remaining))
        try:
            body = _read_health_response(
                response,
                deadline,
                clock,
                cancel_event=cancel_event,
            )
        finally:
            close = getattr(response, "close", None)
            if close is not None:
                close()
        latency_ms = max(1, int(round((clock() - started) * 1000)))
        if len(body) > MAX_PLAYLIST_BYTES:
            return ServerHealth(N_("ERROR"), latency_ms, detail=N_("API response is too large"))
        if not isinstance(body, str):
            body = body.decode("utf-8", "replace")
        payload = json.loads(body, parse_int=_bounded_provider_integer)
        _validate_provider_json_depth(payload)
        return _health_from_payload(payload, latency_ms, account=account)
    except HTTPError as error:
        try:
            error.close()
        except Exception:
            pass
        log_event("health", N_("Server health check HTTP error"), error)
        latency_ms = max(1, int(round((clock() - started) * 1000)))
        if error.code in (401, 403):
            detail = N_("Server denied access")
        elif error.code == 429:
            detail = N_("Server request limit reached")
        else:
            detail = _(N_("Server returned HTTP {}")).format(error.code)
        return ServerHealth(N_("ERROR"), latency_ms, detail=detail)
    except (
        URLError,
        socket.timeout,
        ValueError,
        TypeError,
        OSError,
        RecursionError,
    ) as error:
        log_event("health", N_("Server health check connection error"), error)
        latency_ms = max(1, int(round((clock() - started) * 1000)))
        return ServerHealth(N_("ERROR"), latency_ms, detail=N_("Could not connect to the server"))


def _m3u_cache_age_text(cached_at, now=None):
    try:
        cached_at = float(cached_at or 0)
    except (TypeError, ValueError, OverflowError):
        cached_at = 0
    if cached_at <= 0:
        return N_("Not cached")
    now = time.time() if now is None else float(now)
    seconds = max(0, int(now - cached_at))
    if seconds < 60:
        return N_("Less than one minute")
    if seconds < 60 * 60:
        return _(N_("{} minutes")).format(max(1, int(seconds / 60)))
    return _(N_("{} hours")).format(max(1, int(seconds / (60 * 60))))


def check_source_health(
    source,
    timeout=None,
    opener=None,
    clock=None,
    cancel_event=None,
    summary_only=False,
):
    """Run the health check appropriate for an Xtream, M3U or portal source."""
    source_type = str(
        getattr(source, "source_type", "xtream") or "xtream"
    ).lower()
    if source_type == "stalker":
        from .stalker import check_portal_health

        return check_portal_health(
            source,
            timeout=(
                DEFAULT_STALKER_HEALTH_TIMEOUT
                if timeout is None
                else timeout
            ),
            opener=opener,
            clock=clock,
            cancel_event=cancel_event,
            summary_only=summary_only,
        )
    if cancel_event is not None and cancel_event.is_set():
        return ServerHealth(
            N_("ERROR"),
            detail=N_("Request cancelled"),
            source_type=source_type,
        )
    if source_type != "m3u":
        health_kwargs = {
            "timeout": DEFAULT_TIMEOUT if timeout is None else timeout,
            "opener": opener,
            "clock": clock,
        }
        if cancel_event is not None:
            health_kwargs["cancel_event"] = cancel_event
        return check_server_health(source, **health_kwargs)

    from .m3u import M3UContentClient

    started = time.monotonic()
    try:
        effective_timeout = (
            DEFAULT_M3U_HEALTH_TIMEOUT if timeout is None else float(timeout)
        )
        client = M3UContentClient(source, timeout=max(1, effective_timeout))
        # A health check must exercise the configured source even while the
        # normal 12-hour catalogue cache is fresh.  This marks only this new
        # client for refresh; it does not delete the last-good disk cache, so
        # an unavailable source can still be reported accurately as STALE.
        client.invalidate_cache("live")
        diagnostic = client.source_health(allow_stale_deadline=True)
    except Exception:
        diagnostic = {
            "status": "error",
            "reason": N_("The M3U source could not be checked."),
        }
    latency_ms = int(diagnostic.get("latency_ms", 0) or 0)
    if latency_ms <= 0:
        latency_ms = max(1, int(round((time.monotonic() - started) * 1000)))
    state = str(diagnostic.get("status", "error") or "error").lower()
    cache_state = str(diagnostic.get("cache_state", "") or "").lower()
    if state == "online":
        status = "ONLINE"
        if cache_state == "cache":
            detail = N_("M3U catalogue loaded from cache.")
        else:
            detail = N_("M3U source is accessible.")
    elif state == "stale":
        status = "STALE"
        detail = N_("The source is unavailable; a stale cache is in use.")
    else:
        status = N_("ERROR")
        detail = str(
            diagnostic.get("reason")
            or N_("The M3U source could not be checked.")
        )

    modified = N_("Unknown")
    if getattr(source, "kind", "") == "file":
        try:
            modified = time.strftime(
                "%d.%m.%Y %H:%M",
                time.localtime(os.stat(source.location).st_mtime),
            )
        except (IOError, OSError, OverflowError, ValueError):
            pass
    kind = (
        N_("Local / NAS file")
        if getattr(source, "kind", "") == "file"
        else N_("Web URL")
    )
    return ServerHealth(
        status=status,
        latency_ms=latency_ms,
        detail=detail,
        source_type="m3u",
        source_kind=kind,
        source_size=diagnostic.get("size_bytes"),
        item_count=diagnostic.get("channels"),
        category_count=diagnostic.get("categories"),
        cache_age=_m3u_cache_age_text(diagnostic.get("cached_at")),
        modified=modified,
    )


def delete_sources(
    sources,
    paths=None,
    portal_path=None,
    cancel_event=None,
    deadline=None,
):
    """Bulk-delete one homogeneous account type for the source-manager UI."""
    requested = tuple(sources or ())
    if not requested:
        return 0
    source_types = set(
        str(getattr(source, "source_type", "xtream") or "xtream").lower()
        for source in requested
    )
    if len(source_types) != 1:
        raise ValueError(N_("The account selected for deletion is invalid"))
    source_type = next(iter(source_types))
    if source_type == "stalker":
        from .stalker import delete_portal_accounts

        return delete_portal_accounts(
            requested,
            path=portal_path,
            cancel_event=cancel_event,
            deadline=deadline,
        )
    if source_type == "m3u":
        raise ValueError(N_("The account selected for deletion is invalid"))
    return delete_accounts(
        requested,
        paths=paths,
        cancel_event=cancel_event,
        deadline=deadline,
    )

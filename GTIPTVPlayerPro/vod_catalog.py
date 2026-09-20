# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import hashlib
import json
import os
import stat
import time
from urllib.parse import unquote, urlsplit

from .content import ContentError, ContentItem, MAX_CATALOG_RESULTS


DEFAULT_VOD_CATALOG_DIR = "/etc/enigma2/gtiptvplayer/vod-catalog"
VOD_CATALOG_TTL_SECONDS = 24 * 60 * 60
VOD_CATALOG_PRIMARY_SECONDS = 30.0
VOD_CATALOG_PACE_SECONDS = 0.65
VOD_CATALOG_MAX_ITEMS = MAX_CATALOG_RESULTS
VOD_CATALOG_MAX_BYTES = 32 * 1024 * 1024
VOD_CATALOG_EVENT_BATCH_ITEMS = 250
VOD_CATALOG_CACHE_SCHEMA = 3
VOD_CATALOG_LEGACY_SCHEMA = 1

EVENT_BATCH = "batch"
EVENT_PROGRESS = "progress"
EVENT_FINISHED = "finished"

SOURCE_CACHE = "cache"
SOURCE_PRIMARY = "primary"
SOURCE_CATEGORY = "category"

REASON_CATEGORY_ERRORS = "category_errors"
REASON_NO_CATEGORIES = "no_categories"
REASON_SIZE_LIMIT = "catalog_size_limit"

_STOP_REASONS = frozenset(
    (ContentError.ACCESS_DENIED, ContentError.RATE_LIMITED)
)


def _compact_text(value, limit):
    value = str(value or "").replace("\r", " ").replace("\n", " ")
    return " ".join(value.split())[:limit]


def _numeric_id(value):
    value = str(value or "").strip()
    return value[:40] if value.isdigit() else ""


def _category_id(value):
    return _compact_text(value, 40)


def _safe_extension(value):
    value = str(value or "").strip().lower().lstrip(".")
    if not value or not value.replace("_", "").isalnum():
        return "mp4"
    return value[:12]


def _account_identity(client):
    account = getattr(client, "account", None)
    identity = getattr(account, "identity", ())
    if not identity and account is not None:
        identity = (
            getattr(account, "scheme", ""),
            getattr(account, "netloc", ""),
            getattr(account, "base_path", ""),
            getattr(account, "username", ""),
        )
    return tuple(str(value or "") for value in (identity or ()))


def vod_catalog_account_scope(client):
    """Return an anonymous, stable filename scope for one Xtream account."""
    identity = _account_identity(client)
    if not any(identity):
        return ""
    value = "\x1f".join(identity).encode("utf-8", "replace")
    return hashlib.sha256(value).hexdigest()


def _safe_poster_url(value, account=None):
    """Keep only poster URLs which cannot persist provider credentials."""
    value = str(value or "").replace("\r", "").replace("\n", "").strip()
    if not value or len(value) > 1024:
        return ""
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return ""
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    lowered = unquote(value).lower()
    if any(
        marker in lowered
        for marker in ("username=", "password=", "token=", "api_key=")
    ):
        return ""
    path_lower = unquote(parsed.path).lower()
    if any(
        marker in path_lower
        for marker in (
            "/token/",
            "/tokens/",
            "/api_key/",
            "/apikey/",
            "/authorization/",
        )
    ):
        return ""
    for attribute in ("username", "password"):
        credential = str(getattr(account, attribute, "") or "").strip()
        if credential and credential.lower() in lowered:
            return ""
    return value


def _safe_portal_command(value, account=None):
    """Return a reusable Stalker command without persisting credentials.

    Catalogue commands are provider data, not create_link responses. Persist
    only relative media commands and loopback pseudo-URLs which still require
    the authenticated portal to mint a link. External URLs can contain opaque
    path credentials even without a query string, so they stay memory-only.
    """

    value = str(value or "").strip()
    if (
        not value
        or len(value) > 4096
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
        or any(character in value for character in ("|", "#"))
    ):
        return ""

    command = value
    for unused_index in range(3):
        lowered = command.lower()
        matched = False
        for prefix in ("ffmpeg", "ffrt", "auto"):
            marker = prefix + " "
            if lowered.startswith(marker):
                command = command[len(marker) :].strip()
                matched = True
                break
        if not matched:
            break
    if not command or command.lower().startswith(("ffmpeg ", "ffrt ", "auto ")):
        return ""
    if any(character.isspace() for character in command):
        return ""

    decoded = command
    for unused_index in range(4):
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            break
        decoded = next_decoded
    else:
        # Excessive encoding depth is not required by valid portal commands
        # and can otherwise hide credentials from the persistence filter.
        return ""
    decoded = decoded.lower()
    if (
        any(ord(character) < 32 or ord(character) == 127 for character in decoded)
        or any(character.isspace() for character in decoded)
        or any(character in decoded for character in ("|", "#"))
        or any(
            marker in decoded
            for marker in (
                "username=",
                "password=",
                "passwd=",
                "token=",
                "api_key=",
                "apikey=",
                "authorization=",
                "/token/",
                "/tokens/",
                "/api_key/",
                "/apikey/",
                "/authorization/",
            )
        )
    ):
        return ""
    credentials = []
    for attribute in ("username", "password", "mac"):
        credential = str(getattr(account, attribute, "") or "").strip().lower()
        if credential:
            credentials.extend((credential, credential.replace(":", "")))
    if any(credential and credential in decoded for credential in credentials):
        return ""

    try:
        parsed = urlsplit(decoded)
        parsed.port
    except (TypeError, ValueError):
        return ""
    if decoded.startswith("/"):
        if (
            not decoded.startswith("/media/")
            or parsed.scheme
            or parsed.netloc
            or parsed.query
            or parsed.fragment
            or "\\" in parsed.path
            or ".." in parsed.path.split("/")
        ):
            return ""
    else:
        hostname = str(parsed.hostname or "").lower()
        host_parts = hostname.split(".")
        numeric_loopback = bool(
            len(host_parts) == 4
            and host_parts[0] == "127"
            and all(
                part
                and len(part) <= 3
                and all("0" <= character <= "9" for character in part)
                and 0 <= int(part) <= 255
                for part in host_parts
            )
        )
        if (
            parsed.scheme.lower() not in ("http", "https", "rtsp", "rtsps")
            or (
                hostname not in (
                    "localhost",
                    "localhost.localdomain",
                    "0.0.0.0",
                    "::1",
                )
                and not numeric_loopback
            )
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or "\\" in parsed.path
            or ".." in parsed.path.split("/")
        ):
            return ""
    return value


def _safe_portal_request_type(value):
    value = str(value or "").strip().lower()
    return value if value in ("vod", "movie") else "vod"


def _category_values(category):
    if isinstance(category, dict):
        category_id = category.get("category_id")
        name = category.get("name") or category.get("category_name")
    elif isinstance(category, (tuple, list)):
        category_id = category[0] if category else ""
        name = category[1] if len(category) > 1 else ""
    else:
        category_id = getattr(category, "category_id", "")
        name = getattr(category, "name", "")
    category_id = _compact_text(category_id, 40)
    return category_id, _compact_text(name, 180)


def _real_categories(categories):
    results = []
    seen = set()
    for category in categories or ():
        category_id, name = _category_values(category)
        if not category_id or category_id in seen:
            continue
        seen.add(category_id)
        results.append((category_id, name))
    return results


class VodCatalogEvent(object):
    """One worker-side update; GUI callers should enqueue it for polling."""

    def __init__(
        self,
        kind,
        items=(),
        source="",
        category_id="",
        category_name="",
        loaded=0,
        categories_done=0,
        categories_total=0,
        complete=False,
        reason="",
    ):
        self.kind = str(kind or "")
        self.items = tuple(items or ())
        self.source = str(source or "")
        self.category_id = str(category_id or "")
        self.category_name = str(category_name or "")
        self.loaded = int(loaded or 0)
        self.categories_done = int(categories_done or 0)
        self.categories_total = int(categories_total or 0)
        self.complete = bool(complete)
        self.reason = str(reason or "")


class VodCatalogResult(object):
    """Final catalogue state returned to the worker which started the scan."""

    def __init__(
        self,
        items=None,
        complete=False,
        source="",
        reason="",
        from_cache=False,
        cache_saved=False,
        categories_done=0,
        categories_total=0,
    ):
        self.items = list(items or [])
        self.complete = bool(complete)
        self.source = str(source or "")
        self.reason = str(reason or "")
        self.from_cache = bool(from_cache)
        self.cache_saved = bool(cache_saved)
        self.categories_done = int(categories_done or 0)
        self.categories_total = int(categories_total or 0)


class VodCatalogCache(object):
    """Credential-free JSONL cache for one account's completed catalogue."""

    def __init__(
        self,
        client,
        directory=DEFAULT_VOD_CATALOG_DIR,
        ttl=VOD_CATALOG_TTL_SECONDS,
        clock=None,
    ):
        self.client = client
        self.account = getattr(client, "account", None)
        self.directory = str(directory or DEFAULT_VOD_CATALOG_DIR)
        self.ttl = max(1, int(ttl))
        self.clock = clock or time.time
        scope = vod_catalog_account_scope(client)
        self.filename = "{}.jsonl".format(scope) if scope else ""
        self.path = (
            os.path.join(self.directory, self.filename)
            if scope
            else ""
        )

    def _directory_fd(self, create=False):
        """Open every path component without following symbolic links."""
        absolute = os.path.abspath(self.directory)
        if absolute == os.path.sep or not self.filename:
            return None
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        directory_flag = getattr(os, "O_DIRECTORY", 0)
        if not no_follow or not directory_flag:
            return None
        flags = os.O_RDONLY | no_follow | directory_flag
        flags |= getattr(os, "O_CLOEXEC", 0)
        current_fd = -1
        try:
            current_fd = os.open(os.path.sep, flags)
        except OSError:
            return None
        try:
            for component in absolute.split(os.path.sep):
                if not component:
                    continue
                try:
                    component_status = os.stat(
                        component,
                        dir_fd=current_fd,
                        follow_symlinks=False,
                    )
                except FileNotFoundError:
                    if not create:
                        return None
                    try:
                        os.mkdir(component, 0o700, dir_fd=current_fd)
                    except FileExistsError:
                        pass
                    except OSError:
                        return None
                    try:
                        component_status = os.stat(
                            component,
                            dir_fd=current_fd,
                            follow_symlinks=False,
                        )
                    except OSError:
                        return None
                except OSError:
                    return None
                if (
                    stat.S_ISLNK(component_status.st_mode)
                    or not stat.S_ISDIR(component_status.st_mode)
                ):
                    return None
                try:
                    next_fd = os.open(component, flags, dir_fd=current_fd)
                except OSError:
                    return None
                previous_fd = current_fd
                current_fd = next_fd
                try:
                    os.close(previous_fd)
                except OSError:
                    return None
            if create:
                try:
                    os.fchmod(current_fd, 0o700)
                except OSError:
                    return None
            result_fd = current_fd
            current_fd = -1
            return result_fd
        except Exception:
            return None
        finally:
            if current_fd >= 0:
                try:
                    os.close(current_fd)
                except OSError:
                    pass

    def _safe_scope_target(self, directory_fd):
        try:
            target_status = os.stat(
                self.filename,
                dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            return True
        except OSError:
            return False
        return (
            stat.S_ISREG(target_status.st_mode)
            and target_status.st_nlink == 1
        )

    def _record(self, item):
        category_id = _category_id(
            getattr(item, "catalog_category_id", "")
            or getattr(item, "category_id", "")
        )
        record = {
            "i": _numeric_id(getattr(item, "stream_id", "")),
            "n": _compact_text(getattr(item, "name", ""), 180),
            "c": category_id,
            "g": _compact_text(
                getattr(item, "catalog_category_name", ""),
                180,
            ),
            "x": _safe_extension(getattr(item, "extension", "")),
            "y": _compact_text(getattr(item, "year", ""), 20),
            "r": _compact_text(getattr(item, "rating", ""), 20),
            "p": _safe_poster_url(
                getattr(item, "icon", ""),
                self.account,
            ),
        }
        if str(getattr(self.client, "source_type", "") or "") == "stalker":
            record["m"] = _safe_portal_command(
                getattr(item, "portal_command", ""),
                self.account,
            )
            record["q"] = _safe_portal_request_type(
                getattr(item, "portal_request_type", "")
            )
        return record

    def record_line(self, item):
        record = self._record(item)
        if not record["i"]:
            return b""
        value = json.dumps(
            record,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )
        return value.encode("utf-8") + b"\n"

    def _item(self, record):
        if not isinstance(record, dict):
            return None
        stream_id = _numeric_id(record.get("i"))
        if not stream_id:
            return None
        category_id = _category_id(record.get("c"))
        item = ContentItem(
            "movie",
            stream_id,
            _compact_text(record.get("n"), 180),
            category_id=category_id,
            extension=_safe_extension(record.get("x")),
            icon=_safe_poster_url(record.get("p")),
            rating=_compact_text(record.get("r"), 20),
            year=_compact_text(record.get("y"), 20),
        )
        item.catalog_category_id = category_id
        item.catalog_category_name = _compact_text(record.get("g"), 180)
        if str(getattr(self.client, "source_type", "") or "") == "stalker":
            raw_command = str(record.get("m") or "")
            safe_command = _safe_portal_command(raw_command, self.account)
            if not raw_command or safe_command != raw_command:
                return None
            item.portal_command = safe_command
            item.portal_request_type = _safe_portal_request_type(
                record.get("q")
            )
            item.portal_series = "0"
            item.portal_category_ids = (category_id,) if category_id else ()
            item.portal_command_is_fallback = False
        return item

    def load(self, token=None):
        if not self.path:
            return None
        directory_fd = self._directory_fd(create=False)
        if directory_fd is None:
            return None
        descriptor = -1
        handle = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                self.filename,
                flags,
                dir_fd=directory_fd,
            )
            file_status = os.fstat(descriptor)
            if (
                not stat.S_ISREG(file_status.st_mode)
                or file_status.st_nlink != 1
                or file_status.st_size <= 0
                or file_status.st_size > VOD_CATALOG_MAX_BYTES
            ):
                return None
            handle = os.fdopen(descriptor, "rb")
            descriptor = -1
            header_line = handle.readline(4097)
            if not header_line or len(header_line) > 4096:
                return None
            try:
                header = json.loads(header_line.decode("utf-8"))
            except (TypeError, ValueError, UnicodeError):
                return None
            if (
                not isinstance(header, dict)
                or header.get("v") not in (
                    VOD_CATALOG_LEGACY_SCHEMA,
                    VOD_CATALOG_CACHE_SCHEMA,
                )
                or header.get("complete") != 1
            ):
                return None
            if (
                header.get("v") == VOD_CATALOG_LEGACY_SCHEMA
                and str(getattr(self.client, "source_type", "") or "")
                == "stalker"
            ):
                # R30 could mark a partial Stalker wildcard page as complete.
                # Rebuild only portal catalogues; valid Xtream caches remain
                # usable across the package revision.
                return None
            try:
                created = float(header.get("created"))
                expected_count = int(header.get("count"))
            except (TypeError, ValueError, OverflowError):
                return None
            age = float(self.clock()) - created
            if age < -300 or age >= self.ttl:
                return None
            if expected_count < 0 or expected_count > VOD_CATALOG_MAX_ITEMS:
                return None

            items = []
            known = set()
            total_bytes = len(header_line)
            for line_number, line in enumerate(handle, 1):
                if line_number % 128 == 0:
                    _check_token(token)
                total_bytes += len(line)
                if (
                    total_bytes > VOD_CATALOG_MAX_BYTES
                    or len(items) >= VOD_CATALOG_MAX_ITEMS
                ):
                    return None
                try:
                    record = json.loads(line.decode("utf-8"))
                except (TypeError, ValueError, UnicodeError):
                    return None
                item = self._item(record)
                if item is None or item.stream_id in known:
                    return None
                known.add(item.stream_id)
                items.append(item)
            if len(items) != expected_count:
                return None
            _check_token(token)
            return items
        except OSError:
            return None
        finally:
            if handle is not None:
                handle.close()
            elif descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                os.close(directory_fd)
            except OSError:
                pass

    def store(self, items, token=None):
        if not self.path:
            return False
        if str(getattr(self.client, "source_type", "") or "") == "stalker":
            # A complete on-disk Stalker hit must remain playable after restart.
            # If even one command cannot be persisted without credentials, keep
            # the completed catalogue in memory and decline the disk cache.
            for index, item in enumerate(items):
                if index % 128 == 0:
                    _check_token(token)
                command = str(
                    getattr(item, "portal_command", "") or ""
                ).strip()
                if (
                    not command
                    or _safe_portal_command(command, self.account) != command
                ):
                    return False
        directory_fd = self._directory_fd(create=True)
        if directory_fd is None:
            return False
        if not self._safe_scope_target(directory_fd):
            try:
                os.close(directory_fd)
            except OSError:
                pass
            return False

        header = {
            "v": VOD_CATALOG_CACHE_SCHEMA,
            "created": int(self.clock()),
            "complete": 1,
            "count": len(items),
        }
        header_line = (
            json.dumps(
                header,
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("ascii")
            + b"\n"
        )
        temporary_name = ".{}.tmp-{}-{}".format(
            self.filename,
            os.getpid(),
            id(items),
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(
                temporary_name,
                flags,
                0o600,
                dir_fd=directory_fd,
            )
        except OSError:
            try:
                os.close(directory_fd)
            except OSError:
                pass
            return False
        stored = False
        try:
            os.fchmod(descriptor, 0o600)
            handle = os.fdopen(descriptor, "wb")
            descriptor = -1
            try:
                handle.write(header_line)
                total_bytes = len(header_line)
                for index, item in enumerate(items):
                    if index % 128 == 0:
                        _check_token(token)
                    line = self.record_line(item)
                    if not line:
                        return False
                    total_bytes += len(line)
                    if total_bytes > VOD_CATALOG_MAX_BYTES:
                        return False
                    handle.write(line)
            finally:
                handle.close()
            _check_token(token)
            if not self._safe_scope_target(directory_fd):
                return False
            os.replace(
                temporary_name,
                self.filename,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
            )
            stored = True
            return True
        except OSError:
            return False
        finally:
            if descriptor >= 0:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if not stored:
                try:
                    os.unlink(temporary_name, dir_fd=directory_fd)
                except OSError:
                    pass
            try:
                os.close(directory_fd)
            except OSError:
                pass


class _CatalogAccumulator(object):
    def __init__(self, cache, category_names=None):
        self.cache = cache
        self.category_names = dict(category_names or {})
        self.items = []
        self.known = set()
        self.encoded_bytes = 4096

    def add(self, batch, category_id="", category_name="", token=None):
        added = []
        category_id = _category_id(category_id)
        category_name = _compact_text(category_name, 180)
        for index, item in enumerate(batch or ()):
            if index % 128 == 0:
                _check_token(token)
            if not isinstance(item, ContentItem):
                continue
            stream_id = _numeric_id(getattr(item, "stream_id", ""))
            if not stream_id or stream_id in self.known:
                continue
            if len(self.items) >= VOD_CATALOG_MAX_ITEMS:
                return added, ContentError.CATALOG_ITEM_LIMIT
            item_category_id = _category_id(
                getattr(item, "catalog_category_id", "")
                or getattr(item, "category_id", "")
                or category_id
            )
            item.catalog_category_id = item_category_id
            item.catalog_category_name = (
                category_name
                or getattr(item, "catalog_category_name", "")
                or self.category_names.get(item_category_id, "")
                or ""
            )
            line = self.cache.record_line(item)
            if not line:
                continue
            if self.encoded_bytes + len(line) > VOD_CATALOG_MAX_BYTES:
                return added, REASON_SIZE_LIMIT
            self.encoded_bytes += len(line)
            self.known.add(stream_id)
            self.items.append(item)
            added.append(item)
        return added, ""


def _check_token(token):
    if token is None:
        raise ValueError("A catalogue worker token is required")
    cancel_event = getattr(token, "cancel_event", None)
    if cancel_event is not None and cancel_event.is_set():
        checker = getattr(token, "check", None)
        if callable(checker):
            checker()
        raise ContentError("Request cancelled", ContentError.CANCELLED)
    try:
        deadline = float(getattr(token, "deadline"))
    except (TypeError, ValueError, OverflowError):
        raise ValueError("The catalogue worker token requires a deadline")
    if time.monotonic() >= deadline:
        raise ContentError("Request timed out", ContentError.DEADLINE)
    checker = getattr(token, "check", None)
    if callable(checker):
        checker()


def _token_deadline(token):
    _check_token(token)
    return float(getattr(token, "deadline"))


def _request_batch(client, category_id, token, deadline, timeout=None):
    _check_token(token)
    loader = getattr(client, "load_vod_catalog_batch", None)
    if not callable(loader):
        raise ContentError(
            "VOD catalogue loading is unavailable",
            ContentError.CONTENT_RESPONSE,
        )
    scope_factory = getattr(client, "request_scope", None)
    if not callable(scope_factory):
        result = loader(category_id, VOD_CATALOG_MAX_ITEMS)
        _check_token(token)
        return result
    try:
        scope = scope_factory(
            getattr(token, "cancel_event", None),
            deadline,
            timeout=timeout,
        )
    except TypeError:
        scope = scope_factory(
            getattr(token, "cancel_event", None),
            deadline,
        )
    with scope:
        result = loader(category_id, VOD_CATALOG_MAX_ITEMS)
    _check_token(token)
    return result


def _request_step(client, category_id, cursor, token, deadline):
    """Run one progressive provider step inside the worker's deadline."""

    _check_token(token)
    loader = getattr(client, "load_vod_catalog_step", None)
    if not callable(loader):
        raise ContentError(
            "Progressive VOD catalogue loading is unavailable",
            ContentError.CONTENT_RESPONSE,
        )
    scope_factory = getattr(client, "request_scope", None)
    if not callable(scope_factory):
        result = loader(
            category_id,
            cursor,
            VOD_CATALOG_EVENT_BATCH_ITEMS,
        )
        _check_token(token)
        return result
    scope = scope_factory(
        getattr(token, "cancel_event", None),
        deadline,
    )
    with scope:
        result = loader(
            category_id,
            cursor,
            VOD_CATALOG_EVENT_BATCH_ITEMS,
        )
    _check_token(token)
    return result


def _reset_progressive_catalog(client, token, deadline):
    resetter = getattr(client, "reset_vod_catalog_search", None)
    if not callable(resetter):
        return
    _check_token(token)
    scope_factory = getattr(client, "request_scope", None)
    if not callable(scope_factory):
        resetter()
    else:
        with scope_factory(
            getattr(token, "cancel_event", None),
            deadline,
        ):
            resetter()
    _check_token(token)


def _catalog_pace_seconds(client):
    try:
        value = float(
            getattr(
                client,
                "vod_catalog_pace_seconds",
                VOD_CATALOG_PACE_SECONDS,
            )
        )
    except (TypeError, ValueError, OverflowError):
        value = VOD_CATALOG_PACE_SECONDS
    return max(0.05, min(2.0, value))


def _paced_wait(token, seconds=VOD_CATALOG_PACE_SECONDS):
    _check_token(token)
    deadline = _token_deadline(token)
    remaining = min(float(seconds), max(0.0, deadline - time.monotonic()))
    if remaining <= 0:
        raise ContentError("Request timed out", ContentError.DEADLINE)
    cancel_event = getattr(token, "cancel_event", None)
    if cancel_event is not None:
        if cancel_event.wait(remaining):
            _check_token(token)
    else:
        time.sleep(remaining)
    _check_token(token)


def _emit(callback, event):
    if not callable(callback):
        return
    try:
        callback(event)
    except Exception:
        # UI updates are advisory. A closed screen must not abort a catalogue
        # which may still be useful to a replacement screen or the cache.
        pass


def _emit_batches(
    callback,
    items,
    source,
    category_id,
    category_name,
    loaded,
    categories_done,
    categories_total,
    token,
):
    first_loaded = max(0, int(loaded) - len(items))
    for offset in range(0, len(items), VOD_CATALOG_EVENT_BATCH_ITEMS):
        _check_token(token)
        batch = items[offset : offset + VOD_CATALOG_EVENT_BATCH_ITEMS]
        _emit(
            callback,
            VodCatalogEvent(
                EVENT_BATCH,
                batch,
                source=source,
                category_id=category_id,
                category_name=category_name,
                loaded=first_loaded + offset + len(batch),
                categories_done=categories_done,
                categories_total=categories_total,
            ),
        )


def _finished_event(callback, result):
    _emit(
        callback,
        VodCatalogEvent(
            EVENT_FINISHED,
            source=result.source,
            loaded=len(result.items),
            categories_done=result.categories_done,
            categories_total=result.categories_total,
            complete=result.complete,
            reason=result.reason,
        ),
    )
    return result


def _result(
    accumulator,
    source,
    complete,
    reason,
    cache,
    token,
    categories_done,
    categories_total,
):
    cache_saved = False
    if complete:
        try:
            cache_saved = cache.store(accumulator.items, token)
        except ContentError as error:
            if error.reason == ContentError.CANCELLED:
                raise
            if error.reason == ContentError.DEADLINE:
                complete = False
                reason = ContentError.DEADLINE
            cache_saved = False
    return VodCatalogResult(
        accumulator.items,
        complete=complete,
        source=source,
        reason=reason,
        cache_saved=cache_saved,
        categories_done=categories_done,
        categories_total=categories_total,
    )


def _load_progressive_vod_catalog(
    client,
    categories,
    token,
    callback,
    force_refresh,
    cache,
):
    """Stream Stalker pages through the existing worker event queue."""

    deadline = _token_deadline(token)
    if force_refresh:
        _reset_progressive_catalog(client, token, deadline)

    category_names = dict(categories)
    accumulator = _CatalogAccumulator(cache, category_names)
    categories_total = len(categories)
    scan_entries = [("", "", False)]
    scan_entries.extend(
        (category_id, category_name, True)
        for category_id, category_name in categories
    )
    categories_done = 0
    failures = []
    stop_reason = ""
    primary_complete = False
    categories_trusted = True
    pace_seconds = _catalog_pace_seconds(client)
    final_source = SOURCE_PRIMARY

    for category_id, category_name, is_category in scan_entries:
        source = SOURCE_CATEGORY if is_category else SOURCE_PRIMARY
        final_source = source
        entry_deadline = (
            deadline
            if is_category
            else min(
                deadline,
                time.monotonic() + VOD_CATALOG_PRIMARY_SECONDS,
            )
        )
        cursor = 0
        terminal = False
        trusted = False
        terminal_reason = ""
        entry_failure = ""
        while True:
            try:
                step = _request_step(
                    client,
                    category_id,
                    cursor,
                    token,
                    entry_deadline,
                )
            except ContentError as error:
                if error.reason == ContentError.CANCELLED:
                    raise
                terminal_reason = error.reason or REASON_CATEGORY_ERRORS
                primary_error_fallback = bool(
                    not is_category
                    and categories
                    and error.reason not in _STOP_REASONS
                    and (
                        error.reason != ContentError.DEADLINE
                        or time.monotonic() < deadline
                    )
                )
                if primary_error_fallback:
                    entry_failure = terminal_reason
                elif error.reason in _STOP_REASONS or error.reason in (
                    ContentError.DEADLINE,
                    ContentError.CONNECTION,
                    ContentError.HTTP_ERROR,
                    ContentError.RESPONSE_TOO_LARGE,
                    ContentError.INVALID_RESPONSE,
                ):
                    stop_reason = terminal_reason
                else:
                    entry_failure = terminal_reason
                break

            items = list(getattr(step, "items", ()) or ())
            try:
                next_cursor = max(
                    cursor,
                    int(getattr(step, "cursor", cursor) or cursor),
                )
            except (TypeError, ValueError, OverflowError):
                next_cursor = cursor
            terminal = bool(getattr(step, "complete", False))
            trusted = bool(getattr(step, "trusted_complete", False))
            terminal_reason = str(getattr(step, "reason", "") or "")
            progressed = bool(
                getattr(step, "progressed", False)
                or items
                or next_cursor > cursor
                or terminal
            )

            added, limit_reason = accumulator.add(
                items,
                category_id,
                category_name,
                token,
            )
            _emit_batches(
                callback,
                added,
                source,
                category_id,
                category_name,
                len(accumulator.items),
                categories_done,
                categories_total,
                token,
            )
            _emit(
                callback,
                VodCatalogEvent(
                    EVENT_PROGRESS,
                    source=source,
                    category_id=category_id,
                    category_name=category_name,
                    loaded=len(accumulator.items),
                    categories_done=categories_done,
                    categories_total=categories_total,
                    reason=terminal_reason if terminal else "",
                ),
            )
            if limit_reason:
                stop_reason = limit_reason
                terminal = True
                trusted = False
                break
            if terminal:
                if not trusted:
                    entry_failure = terminal_reason or REASON_CATEGORY_ERRORS
                break
            if not progressed:
                entry_failure = terminal_reason or REASON_CATEGORY_ERRORS
                break
            cursor = next_cursor
            _paced_wait(token, pace_seconds)

        if is_category:
            categories_done += 1
            if not terminal or not trusted:
                categories_trusted = False
                failures.append(entry_failure or REASON_CATEGORY_ERRORS)
        elif terminal and trusted:
            # Some Stalker forks return a trusted empty wildcard page even
            # though their real category routes are populated. An empty
            # wildcard is authoritative only when no fallback categories exist.
            primary_complete = bool(accumulator.items or not categories)
        elif not categories and not stop_reason:
            failures.append(entry_failure or REASON_CATEGORY_ERRORS)
        _emit(
            callback,
            VodCatalogEvent(
                EVENT_PROGRESS,
                source=source,
                category_id=category_id,
                category_name=category_name,
                loaded=len(accumulator.items),
                categories_done=categories_done,
                categories_total=categories_total,
                reason=terminal_reason,
            ),
        )
        if stop_reason or primary_complete:
            break
        if is_category and categories_done < categories_total:
            _paced_wait(token, pace_seconds)
        elif not is_category and categories:
            # The wildcard route is cheapest and often complete.  Only an
            # untrusted/unsupported terminal falls through to real categories.
            _paced_wait(token, pace_seconds)

    complete = bool(
        not stop_reason
        and (
            primary_complete
            or (
                bool(categories)
                and categories_trusted
                and not failures
                and categories_done == categories_total
            )
        )
    )
    reason = ""
    if not complete:
        reason = stop_reason or (
            failures[0]
            if len(set(failures)) == 1
            else REASON_CATEGORY_ERRORS
        )
    result = _result(
        accumulator,
        final_source,
        complete,
        reason,
        cache,
        token,
        0 if primary_complete else categories_done,
        0 if primary_complete else categories_total,
    )
    return _finished_event(callback, result)


def load_vod_catalog(
    client,
    categories,
    token,
    callback=None,
    force_refresh=False,
    cache_dir=DEFAULT_VOD_CATALOG_DIR,
):
    """Load a searchable movie catalogue inside the caller's worker thread.

    ``token`` is the browser's existing cancellation token. Its absolute
    deadline owns the 90-second ceiling; this module does not start a second
    timer or a background thread. Callback events are emitted from the same
    worker thread and must therefore be queued before touching Enigma2 widgets.
    """
    total_deadline = _token_deadline(token)
    cache = VodCatalogCache(client, cache_dir)
    categories = _real_categories(categories)
    categories_total = len(categories)

    if not force_refresh:
        cached = cache.load(token)
        if cached is not None:
            for offset in range(0, len(cached), VOD_CATALOG_EVENT_BATCH_ITEMS):
                _check_token(token)
                batch = cached[offset : offset + VOD_CATALOG_EVENT_BATCH_ITEMS]
                _emit(
                    callback,
                    VodCatalogEvent(
                        EVENT_BATCH,
                        batch,
                        source=SOURCE_CACHE,
                        loaded=min(offset + len(batch), len(cached)),
                        categories_done=categories_total,
                        categories_total=categories_total,
                    ),
                )
            result = VodCatalogResult(
                cached,
                complete=True,
                source=SOURCE_CACHE,
                from_cache=True,
                categories_done=categories_total,
                categories_total=categories_total,
            )
            return _finished_event(callback, result)

    if callable(getattr(client, "load_vod_catalog_step", None)):
        return _load_progressive_vod_catalog(
            client,
            categories,
            token,
            callback,
            force_refresh,
            cache,
        )

    accumulator = _CatalogAccumulator(cache, dict(categories))
    primary_reason = ""
    primary_deadline = min(
        total_deadline,
        time.monotonic() + VOD_CATALOG_PRIMARY_SECONDS,
    )
    try:
        primary_items = _request_batch(
            client,
            "",
            token,
            primary_deadline,
            timeout=VOD_CATALOG_PRIMARY_SECONDS,
        )
    except ContentError as error:
        if error.reason == ContentError.CANCELLED:
            raise
        if error.reason in _STOP_REASONS:
            result = _result(
                accumulator,
                SOURCE_PRIMARY,
                False,
                error.reason,
                cache,
                token,
                0,
                categories_total,
            )
            return _finished_event(callback, result)
        if error.reason == ContentError.DEADLINE and time.monotonic() >= total_deadline:
            result = _result(
                accumulator,
                SOURCE_PRIMARY,
                False,
                error.reason,
                cache,
                token,
                0,
                categories_total,
            )
            return _finished_event(callback, result)
        primary_items = []
        primary_reason = error.reason

    if primary_items:
        added, limit_reason = accumulator.add(primary_items, token=token)
        _emit_batches(
            callback,
            added,
            SOURCE_PRIMARY,
            "",
            "",
            len(accumulator.items),
            0,
            categories_total,
            token,
        )
        complete = not limit_reason
        result = _result(
            accumulator,
            SOURCE_PRIMARY,
            complete,
            limit_reason,
            cache,
            token,
            0,
            categories_total,
        )
        return _finished_event(callback, result)

    if not categories:
        result = _result(
            accumulator,
            SOURCE_CATEGORY,
            False,
            primary_reason or REASON_NO_CATEGORIES,
            cache,
            token,
            0,
            0,
        )
        return _finished_event(callback, result)

    categories_done = 0
    failures = []
    consecutive_connections = 0
    stop_reason = ""
    for index, (category_id, category_name) in enumerate(categories):
        try:
            _check_token(token)
            if index:
                _paced_wait(token)
            batch = _request_batch(
                client,
                category_id,
                token,
                total_deadline,
            )
        except ContentError as error:
            if error.reason == ContentError.CANCELLED:
                raise
            categories_done += 1
            if error.reason in _STOP_REASONS:
                stop_reason = error.reason
            elif error.reason == ContentError.DEADLINE:
                stop_reason = error.reason
            elif error.reason == ContentError.CONNECTION:
                failures.append(error.reason)
                consecutive_connections += 1
                if consecutive_connections >= 2:
                    stop_reason = error.reason
            else:
                failures.append(error.reason or REASON_CATEGORY_ERRORS)
                consecutive_connections = 0
            _emit(
                callback,
                VodCatalogEvent(
                    EVENT_PROGRESS,
                    source=SOURCE_CATEGORY,
                    category_id=category_id,
                    category_name=category_name,
                    loaded=len(accumulator.items),
                    categories_done=categories_done,
                    categories_total=categories_total,
                    reason=error.reason,
                ),
            )
            if stop_reason:
                break
            continue

        categories_done += 1
        consecutive_connections = 0
        added, limit_reason = accumulator.add(
            batch,
            category_id,
            category_name,
            token,
        )
        _emit_batches(
            callback,
            added,
            SOURCE_CATEGORY,
            category_id,
            category_name,
            len(accumulator.items),
            categories_done,
            categories_total,
            token,
        )
        _emit(
            callback,
            VodCatalogEvent(
                EVENT_PROGRESS,
                source=SOURCE_CATEGORY,
                category_id=category_id,
                category_name=category_name,
                loaded=len(accumulator.items),
                categories_done=categories_done,
                categories_total=categories_total,
                reason=limit_reason,
            ),
        )
        if limit_reason:
            stop_reason = limit_reason
            break

    complete = (
        not stop_reason
        and not failures
        and categories_done == categories_total
    )
    reason = ""
    if not complete:
        reason = stop_reason or (
            failures[0]
            if len(set(failures)) == 1
            else REASON_CATEGORY_ERRORS
        )
    result = _result(
        accumulator,
        SOURCE_CATEGORY,
        complete,
        reason,
        cache,
        token,
        categories_done,
        categories_total,
    )
    return _finished_event(callback, result)

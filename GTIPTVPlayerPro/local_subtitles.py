# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded local subtitle browsing and receiver-selected download storage.

Filesystem work belongs on the existing subtitle/UI workers. Native receiver
users can browse the full directory tree and select a writable destination.
Persistent user folders are never chmodded, swept or silently substituted.
"""

import hashlib
import errno
from contextlib import contextmanager
import json
import os
import re
import stat
import tempfile
import threading
import time
import unicodedata

from .persistence import guarded_mutation
from .subtitle_decode import MAX_SUBTITLE_BYTES, decode_subtitle_content
from .subtitle_settings import (
    SUBTITLE_SETTINGS_PATH, read_subtitle_document, update_subtitle_document,
)


DEFAULT_DOWNLOAD_DIR = "/tmp/gtiptvplayerpro-subtitles"
IMPORT_DIRECTORY = "/tmp/gtiptvplayerpro-subtitle-imports"
LOCAL_PREFERENCES = "_local_subtitles"
SUPPORTED_EXTENSIONS = (".srt", ".vtt")
MAX_DIRECTORY_ENTRIES = 2000
MAX_SEARCH_RESULTS = 500
MAX_SEARCH_ENTRIES = 12000
MAX_SEARCH_DIRECTORIES = 500
MAX_SEARCH_DEPTH = 8
SEARCH_SECONDS = 5.0
MAX_CACHE_FILES = 16
MAX_CACHE_BYTES = 16 * 1024 * 1024
_FILE_LOCK = threading.RLock()
_MANIFEST_NAME = ".gtiptv-subtitles.json"
_LANGUAGE_NAMES = {
    "arabic": "ar", "turkish": "tr", "english": "en", "german": "de",
    "french": "fr", "spanish": "es", "italian": "it", "polish": "pl",
    "russian": "ru", "portuguese": "pt", "persian": "fa", "hebrew": "he",
}
_LANGUAGES = frozenset((
    "ar", "bg", "ca", "cs", "da", "de", "el", "en", "es", "et", "fa",
    "fi", "fr", "fy", "gl", "he", "hr", "hu", "id", "is", "it", "ja",
    "ko", "ku", "lt", "lv", "mk", "nb", "nl", "nn", "pl", "pt", "ro",
    "ru", "sk", "sl", "sq", "sr", "sv", "ta", "th", "tr", "uk", "vi", "zh",
))


class LocalSubtitleError(OSError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


class FolderEntries(list):
    limited = False


def _cancelled(cancel):
    return bool(cancel() if callable(cancel) else
                getattr(cancel, "is_set", lambda: False)())


def _check_cancel(cancel):
    if _cancelled(cancel):
        raise LocalSubtitleError("local_cancelled")


def _within(path, root):
    try:
        return os.path.commonpath((path, root)) == root
    except (ValueError, TypeError):
        return False


def _mount_unescape(value):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), value)


def _mounted_storage_roots():
    roots = set()
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as handle:
            for line in handle:
                fields = line.split()
                if len(fields) < 4:
                    continue
                folder = os.path.abspath(_mount_unescape(fields[1]))
                if not (folder.startswith("/media/") or folder.startswith("/mnt/")):
                    continue
                if fields[2] in ("autofs", "proc", "sysfs", "devtmpfs"):
                    continue
                if os.path.isdir(folder):
                    roots.add(os.path.realpath(folder))
    except OSError:
        pass
    # Some images expose /media/hdd as a symlink to the actual mounted disk.
    for folder in ("/media/hdd", "/media/usb"):
        real = os.path.realpath(folder)
        if os.path.isdir(real) and os.path.ismount(real) and real != "/":
            roots.add(real)
    return sorted(roots)


def _clean_path(value):
    if not isinstance(value, str) or not value or len(value) > 4096:
        return None
    if not os.path.isabs(value) or any(ord(char) < 32 for char in value):
        return None
    return os.path.abspath(value)


def _allowed_root(path, for_write=False):
    # Native selection is intentionally not restricted to removable media.
    # Actual access, regular-file type and write checks remain at the I/O site.
    return os.path.sep if _clean_path(path) is not None else None


def _temporary_download_root():
    # Resolve image-specific /tmp aliases, never a substituted leaf directory.
    temporary = os.path.abspath(DEFAULT_DOWNLOAD_DIR)
    return os.path.join(os.path.realpath(os.path.dirname(temporary)),
                        os.path.basename(temporary))


def _checked_folder(folder, for_write=False, create_default=False):
    path = _clean_path(folder)
    if path is None or _allowed_root(path, for_write=for_write) is None:
        raise LocalSubtitleError("download_folder_unavailable" if for_write else "local_read_failed")
    if create_default and path in (os.path.abspath(DEFAULT_DOWNLOAD_DIR), _temporary_download_root()):
        if os.path.lexists(path) and os.path.islink(path):
            raise LocalSubtitleError("download_folder_unavailable")
        os.makedirs(path, mode=0o700, exist_ok=True)
    if not os.path.isdir(path):
        raise LocalSubtitleError("download_folder_unavailable" if for_write else "local_read_failed")
    if for_write:
        # Permit user-selected directory aliases while pinning the actual
        # destination with O_NOFOLLOW during the later write transaction.
        path = os.path.realpath(path)
        details = os.statvfs(path)
        if (details.f_flag & getattr(os, "ST_RDONLY", 1)
                or not os.access(path, os.W_OK | os.X_OK)):
            raise LocalSubtitleError("download_folder_unavailable")
    elif not os.access(path, os.R_OK | os.X_OK):
        raise LocalSubtitleError("local_read_failed")
    return path


def _same_directory(path, descriptor):
    try:
        current, pinned = os.stat(path), os.fstat(descriptor)
        return (current.st_dev, current.st_ino) == (pinned.st_dev, pinned.st_ino)
    except OSError:
        return False


@contextmanager
def _pinned_directory(folder, require_storage=True):
    """Keep I/O on the selected filesystem even through a lazy unmount.

    Linux /proc/self/fd paths refer to the opened directory, never to an
    unmounted /media directory exposed on the receiver's internal flash.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(folder, flags)
    try:
        def current():
            return (_same_directory(folder, descriptor)
                    and (not require_storage or _allowed_root(folder, for_write=True) is not None))
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode) or not current():
            raise LocalSubtitleError("download_folder_unavailable")
        pinned = "/proc/self/fd/{}".format(descriptor)
        if not os.path.isdir(pinned):
            raise LocalSubtitleError("download_folder_unavailable")
        yield pinned, current
    finally:
        os.close(descriptor)


def load_download_folder(settings_path=None):
    document = read_subtitle_document(settings_path or SUBTITLE_SETTINGS_PATH)
    local = document.get(LOCAL_PREFERENCES)
    value = local.get("download_folder") if isinstance(local, dict) else None
    # Keep an unavailable saved disk visible; never silently write elsewhere.
    return _clean_path(value) or DEFAULT_DOWNLOAD_DIR


@guarded_mutation()
def save_download_folder(folder, settings_path=None, cancel=None):
    try:
        _check_cancel(cancel)
        selected_folder = _clean_path(folder)
        folder = _checked_folder(folder, for_write=True, create_default=True)
        # A real open/fsync proves the receiver can use this destination.
        with _pinned_directory(folder) as (pinned, current):
            descriptor, probe = tempfile.mkstemp(prefix=".gtiptv-check-", dir=pinned)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.flush()
                    os.fsync(handle.fileno())
            finally:
                if os.path.exists(probe):
                    os.unlink(probe)
            _check_cancel(cancel)
            if not current():
                return False
            def update(document):
                _check_cancel(cancel)
                if not current():
                    raise LocalSubtitleError("download_folder_unavailable")
                local = document.get(LOCAL_PREFERENCES)
                local = dict(local) if isinstance(local, dict) else {}
                local["download_folder"] = selected_folder
                document[LOCAL_PREFERENCES] = local
                return document
            return update_subtitle_document(update, settings_path or SUBTITLE_SETTINGS_PATH, cancel=cancel)
    except (OSError, TypeError, ValueError):
        return False


def storage_roots():
    entries = [dict(kind="directory", name=os.path.sep, path=os.path.sep, label="")]
    entries.extend(dict(kind="directory", name=os.path.basename(path) or path,
                        path=path, label="") for path in _mounted_storage_roots())
    entries.append(dict(kind="directory", name=DEFAULT_DOWNLOAD_DIR,
                        path=DEFAULT_DOWNLOAD_DIR, label="Temporary folder"))
    return entries


def _fold(value):
    text = unicodedata.normalize("NFKD", str(value or "")).casefold().replace("ı", "i")
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(re.sub(r"[^\w]+", " ", text, flags=re.UNICODE).split())


def infer_subtitle_language(path):
    stem = os.path.splitext(os.path.basename(str(path or "")))[0]
    parts = re.split(r"[\s._\-\[\]()]+", stem.casefold())
    for part in reversed(parts):
        if part in _LANGUAGE_NAMES:
            return _LANGUAGE_NAMES[part]
        if part in _LANGUAGES:
            return part
    return ""


def _positive_int(value):
    try:
        return max(0, min(9999, int(value or 0)))
    except (TypeError, ValueError, OverflowError):
        return 0


def _match_score(name, metadata):
    metadata = metadata if isinstance(metadata, dict) else {}
    title = _fold(metadata.get("title"))
    folded = _fold(name)
    score = 100 if title and title in folded else 0
    if title and not score:
        words = title.split()
        score = sum(word in folded.split() for word in words) * 30 // max(1, len(words))
    season, episode = _positive_int(metadata.get("season")), _positive_int(metadata.get("episode"))
    match = re.search(r"(?i)(?:s(\d{1,3})[ ._-]*e(\d{1,4})|(\d{1,3})x(\d{1,4}))", name)
    if match and (season or episode):
        found = (int(match.group(1) or match.group(3)), int(match.group(2) or match.group(4)))
        score += 80 if found == (season, episode) else -100
    return score


def _entry(entry, metadata):
    # Include hidden and linked directories: images commonly link /tmp,
    # /hdd and NAS locations. Recursive search deduplicates directory inodes.
    if entry.is_dir(follow_symlinks=True):
        return dict(kind="directory", name=entry.name, path=entry.path, label="", score=0)
    extension = os.path.splitext(entry.name)[1].lower()
    if extension not in SUPPORTED_EXTENSIONS or not entry.is_file(follow_symlinks=False):
        return None
    return dict(kind="file", name=entry.name, path=entry.path, label="",
                language=infer_subtitle_language(entry.name),
                size=entry.stat(follow_symlinks=False).st_size,
                score=_match_score(entry.name, metadata))


def _entry_order(entry):
    return (entry["kind"] != "directory", -entry.get("score", 0), _fold(entry["name"]))


def list_subtitle_folder(folder, metadata=None, cancel=None):
    _check_cancel(cancel)
    if not folder:
        entries = storage_roots()
        saved = load_download_folder()
        entries.insert(0, dict(kind="directory", name=saved, path=saved,
                               label="GT subtitle downloads", score=0))
        return entries
    folder = _checked_folder(folder, create_default=True)
    entries = FolderEntries()
    try:
        with os.scandir(folder) as iterator:
            for index, entry in enumerate(iterator):
                _check_cancel(cancel)
                if index >= MAX_DIRECTORY_ENTRIES:
                    entries.limited = True
                    break
                try:
                    value = _entry(entry, metadata)
                except OSError:
                    continue
                if value is not None:
                    entries.append(value)
    except OSError as error:
        if isinstance(error, LocalSubtitleError):
            raise
        raise LocalSubtitleError("local_read_failed")
    entries.sort(key=_entry_order)
    if folder != os.path.sep:
        entries.insert(0, dict(kind="directory", name="..", path=os.path.dirname(folder),
                               label="Up one folder", parent=True, score=0))
    return entries


def search_subtitle_files(folder, query, metadata=None, cancel=None):
    folder = _checked_folder(folder, create_default=True)
    wanted = _fold(query).split()
    if not wanted:
        wanted = _fold((metadata or {}).get("title", "")).split()
    pending = [(folder, 0)]
    visited = set()
    results = []
    scanned = directories = 0
    limited = False
    deadline = time.monotonic() + SEARCH_SECONDS
    while pending:
        _check_cancel(cancel)
        if time.monotonic() >= deadline or directories >= MAX_SEARCH_DIRECTORIES:
            limited = True
            break
        current, depth = pending.pop()
        if _allowed_root(current) is None:
            continue
        try:
            details = os.stat(current)
            identity = (details.st_dev, details.st_ino)
            if identity in visited or not stat.S_ISDIR(details.st_mode):
                continue
            visited.add(identity)
            directories += 1
            with os.scandir(current) as iterator:
                for entry in iterator:
                    _check_cancel(cancel)
                    scanned += 1
                    if scanned > MAX_SEARCH_ENTRIES or time.monotonic() >= deadline:
                        limited = True
                        break
                    try:
                        value = _entry(entry, metadata)
                    except OSError:
                        continue
                    if value is None:
                        continue
                    if value["kind"] == "directory":
                        if depth < MAX_SEARCH_DEPTH and len(pending) < MAX_SEARCH_DIRECTORIES:
                            pending.append((entry.path, depth + 1))
                        else:
                            limited = True
                    elif all(word in _fold(value["name"]) for word in wanted):
                        results.append(value)
                        if len(results) >= MAX_SEARCH_RESULTS:
                            limited = True
                            break
        except OSError as error:
            if isinstance(error, LocalSubtitleError):
                raise
            continue
        if limited and (scanned > MAX_SEARCH_ENTRIES or len(results) >= MAX_SEARCH_RESULTS
                        or time.monotonic() >= deadline):
            break
    return dict(entries=sorted(results, key=_entry_order), limited=limited)


def _regular_bytes(path):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        details = os.fstat(handle.fileno())
        if not stat.S_ISREG(details.st_mode):
            raise LocalSubtitleError("local_read_failed")
        if details.st_size > MAX_SUBTITLE_BYTES:
            raise LocalSubtitleError("local_too_large")
        content = handle.read(MAX_SUBTITLE_BYTES + 1)
    if len(content) > MAX_SUBTITLE_BYTES:
        raise LocalSubtitleError("local_too_large")
    return content


def _private_directory(root):
    if os.path.lexists(root) and os.path.islink(root):
        raise LocalSubtitleError("local_read_failed")
    os.makedirs(root, mode=0o700, exist_ok=True)
    details = os.lstat(root)
    if (not stat.S_ISDIR(details.st_mode)
            or details.st_uid != getattr(os, "geteuid", lambda: details.st_uid)()):
        raise LocalSubtitleError("local_read_failed")
    return root


def _atomic_bytes(path, content):
    descriptor, temporary = tempfile.mkstemp(prefix=".gtiptv-subtitle-", dir=os.path.dirname(path))
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _publish_without_replace(staged, target, content):
    try:
        os.link(staged, target)
        return
    except FileExistsError:
        raise
    except OSError as error:
        if error.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOSYS,
                               errno.EXDEV, getattr(errno, "ENOTSUP", errno.EOPNOTSUPP)):
            raise
    # FAT/EXFAT and some SMB shares cannot hard-link. Exclusive creation still
    # guarantees an existing user's subtitle is never overwritten. A failed
    # write removes only the inode this call created.
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(target, flags, 0o600)
    own = os.fstat(descriptor)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
    except BaseException:
        try:
            current = os.stat(target, follow_symlinks=False)
            if (current.st_dev, current.st_ino) == (own.st_dev, own.st_ino):
                os.unlink(target)
        except OSError:
            pass
        raise


@guarded_mutation(busy_error=True)
def import_local_subtitle(path, metadata, language="", cancel=None, root=None, preserve_paths=()):
    try:
        _check_cancel(cancel)
        if (not _clean_path(path) or os.path.splitext(path)[1].lower() not in SUPPORTED_EXTENSIONS):
            raise LocalSubtitleError("local_format_unsupported")
        # Import only a deliberately selected regular SRT/VTT file; parsing
        # succeeds before a private copy is published or playback is changed.
        if _allowed_root(path) is None:
            raise LocalSubtitleError("local_read_failed")
        language = str(language or infer_subtitle_language(path)).lower()
        content = _regular_bytes(path)
        _check_cancel(cancel)
        content = decode_subtitle_content(content, language=language).encode("utf-8")
        # Validate before publishing an imported copy or altering playback.
        from .online_subtitles import parse_subtitle
        parse_subtitle(content)
        _check_cancel(cancel)
        folder = _private_directory(root or IMPORT_DIRECTORY)
        name = hashlib.sha256(content).hexdigest() + ".srt"
        target = os.path.join(folder, name)
        with _FILE_LOCK:
            _check_cancel(cancel)
            _atomic_bytes(target, content)
            _prune_owned_imports(folder, target, preserve_paths=preserve_paths)
        return content, target
    except LocalSubtitleError:
        raise
    except Exception as error:
        code = getattr(error, "code", "")
        if code in ("subtitle_too_large", "subtitle_format_unsupported"):
            raise LocalSubtitleError("local_too_large" if code == "subtitle_too_large" else "local_format_unsupported")
        raise LocalSubtitleError("local_read_failed")


def _prune_owned_imports(folder, current, preserve_paths=()):
    protected = {os.path.abspath(value) for value in preserve_paths if isinstance(value, str)}
    protected.add(os.path.abspath(current))
    entries = []
    for entry in os.scandir(folder):
        if re.fullmatch(r"[0-9a-f]{64}\.srt", entry.name) and entry.is_file(follow_symlinks=False):
            details = entry.stat(follow_symlinks=False)
            entries.append((entry.path, details.st_mtime, details.st_size))
    entries.sort(key=lambda value: (os.path.abspath(value[0]) in protected,
                                    value[0] == current, value[1]), reverse=True)
    total = 0
    for index, (path, unused_time, size) in enumerate(entries):
        total += size
        if os.path.abspath(path) not in protected and (index >= MAX_CACHE_FILES or total > MAX_CACHE_BYTES):
            try:
                os.unlink(path)
            except OSError:
                pass


def _safe_component(value, fallback="Subtitle", limit=100):
    value = unicodedata.normalize("NFC", str(value or ""))
    value = "".join(c for c in value if c.isalnum() or c in (" ", "_", "-", "."))
    value = re.sub(r"[\s._-]+", "_", value).strip("_") or fallback
    while len(value.encode("utf-8")) > limit:
        value = value[:-1]
    return value or fallback


def readable_subtitle_filename(metadata, result):
    metadata = metadata if isinstance(metadata, dict) else {}
    result = result if isinstance(result, dict) else {}
    parts = [_safe_component(metadata.get("title"))]
    season, episode = _positive_int(metadata.get("season")), _positive_int(metadata.get("episode"))
    if season or episode:
        parts.append("S{:02d}E{:02d}".format(season, episode))
    elif _positive_int(metadata.get("year")):
        parts.append(str(_positive_int(metadata["year"])))
    language = str(result.get("language") or "").strip().lower().replace("-", "_")
    parts.append(_safe_component(language.upper(), fallback="UND", limit=16))
    return "_".join(parts) + ".srt"


def _read_manifest(root):
    try:
        path = os.path.join(root, _MANIFEST_NAME)
        raw = _regular_bytes(path)
        value = json.loads(raw.decode("utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, UnicodeError):
        return {}


def _prune_downloads(root, current, manifest):
    entries = []
    for name, digest in list(manifest.items()):
        if (os.path.basename(name) != name or not name.endswith(".srt")
                or not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest)):
            manifest.pop(name, None)
            continue
        path = os.path.join(root, name)
        try:
            content = _regular_bytes(path)
            if hashlib.sha256(content).hexdigest() != digest:
                manifest.pop(name, None)
                continue
            details = os.stat(path, follow_symlinks=False)
            entries.append((path, details.st_mtime, len(content)))
        except OSError:
            manifest.pop(name, None)
    entries.sort(key=lambda row: (row[0] == current, row[1]), reverse=True)
    total = 0
    for index, (path, unused_time, size) in enumerate(entries):
        total += size
        if path != current and (index >= MAX_CACHE_FILES or total > MAX_CACHE_BYTES):
            try:
                os.unlink(path)
                manifest.pop(os.path.basename(path), None)
            except OSError:
                pass


@guarded_mutation(busy_error=True)
def save_downloaded_subtitle(content, metadata, result, root=None, settings_path=None):
    if not isinstance(content, bytes) or len(content) > MAX_SUBTITLE_BYTES:
        raise LocalSubtitleError("local_too_large")
    folder = root if root is not None else load_download_folder(settings_path)
    # Explicit roots are a retained internal/testing API. Runtime user choices
    # are checked for actual write access and pinned for the transaction.
    explicit = root is not None
    if explicit:
        folder = _private_directory(folder)
    else:
        folder = _checked_folder(folder, for_write=True, create_default=True)
    if os.path.islink(folder):
        raise LocalSubtitleError("download_folder_unavailable")
    language = str((result or {}).get("language") or "")
    content = decode_subtitle_content(content, language=language).encode("utf-8")
    base = readable_subtitle_filename(metadata, result)
    digest = hashlib.sha256(content).hexdigest()
    temporary_cache = _within(os.path.abspath(folder), _temporary_download_root()) or explicit
    with _FILE_LOCK, _pinned_directory(folder, require_storage=not explicit) as (pinned, current):
        for suffix in range(1000):
            if not current():
                raise LocalSubtitleError("download_folder_unavailable")
            name = base if not suffix else base[:-4] + "_{}.srt".format(suffix + 1)
            target = os.path.join(pinned, name)
            final_path = os.path.join(folder, name)
            if os.path.lexists(target):
                try:
                    if not os.path.islink(target) and _regular_bytes(target) == content:
                        if not current():
                            raise LocalSubtitleError("download_folder_unavailable")
                        return final_path
                except LocalSubtitleError as error:
                    if error.code == "download_folder_unavailable":
                        raise
                except OSError:
                    pass
                continue
            descriptor, staged = tempfile.mkstemp(prefix=".gtiptv-subtitle-", dir=pinned)
            try:
                with os.fdopen(descriptor, "wb") as handle:
                    handle.write(content)
                    handle.flush()
                    os.fsync(handle.fileno())
                if not current():
                    raise LocalSubtitleError("download_folder_unavailable")
                try:
                    _publish_without_replace(staged, target, content)
                except FileExistsError:
                    continue
            finally:
                if os.path.exists(staged):
                    os.unlink(staged)
            if not current():
                # This call published the destination. Remove it through the
                # pinned filesystem rather than any newly exposed flash path.
                try:
                    os.unlink(target)
                except OSError:
                    pass
                raise LocalSubtitleError("download_folder_unavailable")
            if temporary_cache:
                manifest = _read_manifest(pinned)
                manifest[name] = digest
                _prune_downloads(pinned, target, manifest)
                try:
                    _atomic_bytes(os.path.join(pinned, _MANIFEST_NAME), json.dumps(
                        manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                except OSError:
                    pass
            return final_path
    raise LocalSubtitleError("download_folder_unavailable")

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Compact user-data backups with verified, journalled file restoration.

Archives contain persistent user data and only this plugin's Enigma2 config
keys. Code, artwork, downloaded videos and rebuildable caches are excluded.
External documents are recovered to private storage when their original mount
is unavailable. Legacy full archives are imported as user data only.
No archive path is ever passed to ZipFile.extract or extractall.
"""

import datetime
import errno
import hashlib
import json
import os
import re
import stat
import tempfile
import threading
import uuid
import zipfile
from contextlib import contextmanager, ExitStack

from .backup_scope import (GROUPS, SHARED_JSON, file_group, merge_document,
                           native_group, normalize_groups, project_document)
from .persistence import RESTORE_COORDINATOR, PersistenceBusy
from . import PLUGIN_VERSION


PACKAGE_VERSION = PLUGIN_VERSION
PLUGIN_DIR = "/usr/lib/enigma2/python/Plugins/Extensions/GTIPTVPlayerPro"
DATA_DIR = "/etc/enigma2/gtiptvplayer"
SUBTITLE_DIR = "/etc/enigma2/gtiptvplayerpro-subtitle-cache"
SETTINGS_PATH = "/etc/enigma2/settings"
CONFIG_PREFIX = "config.plugins.gtiptvplayerpro."
JOURNAL_PATH = "/etc/enigma2/gtiptvplayerpro-restore-journal.json"
EXTENSION = ".gtbackup"
FORMAT_VERSION = 3
BACKUP_SCOPE = "settings"
LEGACY_VERSIONS = frozenset(("1.3.0-r2", "1.3.0-r3"))
MAX_FILES = 40000
MAX_MANIFEST_BYTES = 16 * 1024 * 1024
MAX_DOCUMENT_BYTES = 32 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024
SPACE_MARGIN = 16 * 1024 * 1024
DATA_FILES = (
    "/etc/enigma2/gtiptvplayerpro-settings.json",
    "/etc/enigma2/gtiptvplayerpro-favorites.json",
    "/etc/enigma2/gtiptvplayerpro-resume.json",
    "/etc/enigma2/gtiptvplayerpro-episode-history.json",
    "/etc/enigma2/gtiptvplayerpro-subtitles.json",
    "/etc/enigma2/gtiptvplayerpro-weather.json",
    "/etc/enigma2/gtiptvplayerpro-weather-cache.json",
    "/etc/enigma2/gtiptvplayerpro-metadata.json",
    "/etc/epgimport/gtiptvplayerpro.channels.xml",
    "/etc/epgimport/gtiptvplayerpro.sources.xml",
)
REBUILDABLE_DIRECTORIES = (
    DATA_DIR + "/vod-catalog",
    DATA_DIR + "/m3u-cache",
    DATA_DIR + "/m3u-epg-cache",
)
REBUILDABLE_FILES = frozenset((
    "/etc/enigma2/gtiptvplayerpro-weather-cache.json",
    "/etc/enigma2/gtiptvplayerpro-metadata.json",
))
VIDEO_EXTENSIONS = frozenset((
    ".mp4", ".mkv", ".avi", ".ts", ".m2ts", ".mts", ".mov", ".m4v",
    ".mpg", ".mpeg", ".vob", ".webm", ".wmv", ".flv",
))
_HASH = re.compile(r"[0-9a-f]{64}\Z")
_KEY = re.compile(r"config\.plugins\.gtiptvplayerpro\.[A-Za-z0-9_.]+\Z")
_LOCK = threading.Lock()


class BackupError(Exception):
    def __init__(self, code, detail=""):
        self.code = code
        self.detail = detail
        Exception.__init__(self, code)


def _within(path, directory):
    return path == directory or path.startswith(directory.rstrip("/") + "/")


def _path(value):
    if (not isinstance(value, str) or not value.startswith("/")
            or len(value) > 4096 or "\\" in value
            or any(ord(char) < 32 for char in value)
            or os.path.normpath(value) != value
            or value == "/" or value.startswith("//")):
        raise BackupError("invalid")
    return value


def _transient(name):
    return (name == "__pycache__" or name.endswith((".pyc", ".pyo", ".lock", ".tmp"))
            or name.startswith((".save-", ".gtiptv-restore-", ".gtiptv-backup-")))


def native_settings(payload):
    """Validate a private config fragment, never a receiver-wide settings file."""
    if len(payload) > MAX_DOCUMENT_BYTES:
        raise BackupError("invalid")
    try:
        lines = payload.decode("utf-8").splitlines()
    except UnicodeError:
        raise BackupError("invalid")
    result = []
    seen = set()
    for line in lines:
        if not line:
            continue
        key, separator, value = line.partition("=")
        if not separator or not _KEY.fullmatch(key) or key in seen or "\x00" in value:
            raise BackupError("invalid")
        seen.add(key)
        result.append(line)
    return result


def merge_native_settings(current, fragment, groups=None):
    lines = current.decode("utf-8").splitlines()
    retained = [line for line in lines if not line.startswith(CONFIG_PREFIX)
                or (groups is not None and native_group(line) not in groups)]
    incoming = [line for line in native_settings(fragment)
                if groups is None or native_group(line) in groups]
    return ("\n".join(retained + incoming) + "\n").encode("utf-8")


def _replace_strings(value, replacements):
    if isinstance(value, str):
        return replacements.get(value, value)
    if isinstance(value, list):
        return [_replace_strings(item, replacements) for item in value]
    if isinstance(value, dict):
        return {key: _replace_strings(item, replacements) for key, item in value.items()}
    return value


def _sync_directory(directory):
    descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        try:
            os.fsync(descriptor)
        except OSError as error:
            # Some network filesystems do not implement directory fsync.
            if error.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
    finally:
        os.close(descriptor)


class BackupManager(object):
    def __init__(self, root="/", active_downloads=None, plugin_dir=None):
        self.root = os.path.realpath(root)
        if plugin_dir is None and self.root == "/":
            from .paths import plugin_path
            plugin_dir = plugin_path()
        self.plugin_host = os.path.realpath(plugin_dir or os.path.join(self.root, PLUGIN_DIR.lstrip("/")))
        self.active_downloads = active_downloads or (lambda: False)

    def host(self, logical):
        if _within(_path(logical), PLUGIN_DIR):
            return self.plugin_host + logical[len(PLUGIN_DIR):]
        return os.path.join(self.root, _path(logical).lstrip("/"))

    def logical(self, host):
        real = os.path.realpath(host)
        if not _within(real, self.root):
            raise BackupError("storage")
        if _within(real, self.plugin_host):
            return PLUGIN_DIR + real[len(self.plugin_host):]
        return _path("/" + os.path.relpath(real, self.root))

    def _plain_parent(self, host, create=False):
        parent = os.path.dirname(host)
        if not _within(parent, self.root):
            raise BackupError("storage")
        relative = os.path.relpath(parent, self.root)
        cursor = self.root
        for name in (() if relative == "." else relative.split(os.sep)):
            cursor = os.path.join(cursor, name)
            if create and not os.path.lexists(cursor):
                os.mkdir(cursor, 0o700 if _within(cursor, self.host(DATA_DIR)) else 0o755)
            info = os.lstat(cursor)
            if not stat.S_ISDIR(info.st_mode):
                raise BackupError("storage")
        if os.path.lexists(host) and not stat.S_ISREG(os.lstat(host).st_mode):
            raise BackupError("storage")

    def _read(self, logical, limit=MAX_DOCUMENT_BYTES, optional=False):
        host = self.host(logical)
        if optional and not os.path.lexists(host):
            return b""
        self._plain_parent(host)
        descriptor = os.open(host, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
                raise BackupError("source")
            payload = handle.read(limit + 1)
        if len(payload) > limit:
            raise BackupError("source")
        return payload

    def _json(self, logical, default=None):
        payload = self._read(logical, optional=True)
        if not payload:
            return default
        try:
            return json.loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise BackupError("source")

    def _check_idle(self):
        if self.active_downloads():
            raise BackupError("busy")

    @contextmanager
    def _operation(self, quiesce=False, restoring=False):
        if not _LOCK.acquire(False):
            raise BackupError("busy")
        try:
            self._check_idle()
            with ExitStack() as stack:
                publish_downloads = None
                if quiesce:
                    stack.enter_context(RESTORE_COORDINATOR.freeze())
                    if restoring and self.root == "/":
                        # The detached download worker is a separate process;
                        # its state flock must span replacement and recovery.
                        from .downloads import restore_guard
                        publish_downloads = stack.enter_context(restore_guard())
                try:
                    yield publish_downloads
                finally:
                    if restoring:
                        self._refresh_store_caches()
        except BackupError:
            raise
        except PersistenceBusy:
            raise BackupError("busy")
        except zipfile.BadZipFile:
            raise BackupError("invalid")
        except (OSError, ValueError, UnicodeError, RuntimeError) as error:
            if getattr(error, "code", None) == "busy":
                raise BackupError("busy")
            raise BackupError("failed")
        except Exception as error:
            if getattr(error, "code", None) == "busy":
                raise BackupError("busy")
            raise
        finally:
            _LOCK.release()

    def _refresh_store_caches(self):
        from . import episode_history, favorites
        with favorites._STORE_LOCK:
            with favorites._READ_CACHE_LOCK:
                favorites._READ_CACHE.clear()
        episode_history.refresh_after_restore(
            (self.host(episode_history.DEFAULT_WATCHED_PATH),))

    def inventory(self, safety=False, targets=(), groups=None):
        """Snapshot user choices and sources, without executable or video files."""
        self._check_idle()
        chosen = normalize_groups(groups)
        records = {}

        def add(logical, kind, required=False, group=None):
            original = _path(logical)
            if (kind == "external" and _within(original, DATA_DIR)
                    and self._allowed(original, "external")):
                kind = "data"
            if not self._settings_file(original, kind):
                return
            category = file_group(original, kind, group)
            if original in SHARED_JSON:
                if not set(chosen).intersection(("accounts", file_group(original))):
                    return
            elif category not in chosen:
                return
            host = self.host(original)
            if not os.path.lexists(host):
                if required:
                    raise BackupError("source", original)
                return
            # Canonicalise normal mount aliases, but never follow file symlinks.
            if stat.S_ISLNK(os.lstat(host).st_mode):
                raise BackupError("source", original)
            logical = self.logical(host)
            self._plain_parent(self.host(logical))
            info = os.stat(host)
            if not stat.S_ISREG(info.st_mode):
                raise BackupError("source", original)
            if logical not in records:
                records[logical] = dict(path=logical, kind=kind, size=info.st_size,
                    mode=stat.S_IMODE(info.st_mode) & 0o777, uid=info.st_uid, gid=info.st_gid,
                    fingerprint=(info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns))
            if group is not None:
                records[logical]["group"] = group
            if logical in SHARED_JSON:
                payload = self._read(logical)
                try:
                    document = json.loads(payload.decode("utf-8"))
                    if not isinstance(document, dict):
                        raise ValueError("Settings must be an object")
                except (UnicodeError, ValueError):
                    raise BackupError("source", logical)
                if not set(chosen).intersection(("accounts", file_group(logical))):
                    records.pop(logical, None)
                    return
                fragment = project_document(logical, document, chosen)
                record = records[logical]
                record["payload"] = json.dumps(fragment, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                record["size"] = len(record["payload"])
                record["partial_json"] = True
            if original != logical:
                records[logical].setdefault("aliases", []).append(original)
            if len(records) > MAX_FILES:
                raise BackupError("source")

        for directory, kind in ((DATA_DIR, "data"), (SUBTITLE_DIR, "data")):
            host = self.host(directory)
            if not os.path.lexists(host):
                continue
            if not stat.S_ISDIR(os.lstat(host).st_mode):
                raise BackupError("source")
            for parent, directories, files in os.walk(host, followlinks=False):
                for name in list(directories):
                    if (_transient(name) or not self._settings_file(
                            self.logical(os.path.join(parent, name)), kind)):
                        directories.remove(name)
                    elif not stat.S_ISDIR(os.lstat(os.path.join(parent, name)).st_mode):
                        raise BackupError("source")
                for name in sorted(files):
                    if not _transient(name):
                        if not stat.S_ISREG(os.lstat(os.path.join(parent, name)).st_mode):
                            raise BackupError("source")
                        add(self.logical(os.path.join(parent, name)), kind, True)
        for logical in DATA_FILES:
            add(logical, "data")

        def reference_json(logical, default):
            if not set(chosen).intersection(("accounts", "sources")):
                return default
            try:
                return self._json(logical, default)
            except (BackupError, OSError):
                if not safety:
                    raise
                # Owned files have already been recorded byte for byte. A
                # damaged registry must not prevent restoring a healthy copy.
                return default

        def reference_file(logical, kind, required=False, group=None):
            try:
                add(logical, kind, required, group)
            except (BackupError, OSError):
                if not safety:
                    raise
                # A disconnected source has no current content to preserve.
                # Existing restore targets are captured strictly below.

        def reference_list(value):
            if isinstance(value, list):
                return value
            if not safety:
                raise BackupError("source")
            return []

        registry = reference_json(DATA_DIR + "/playlist-files.json", {})
        if isinstance(registry, dict):
            types = registry.get("types", {})
            if isinstance(types, dict):
                for source_type, source in types.items():
                    if isinstance(source, dict):
                        for path in reference_list(source.get("slots", [])):
                            if path:
                                reference_file(path, "external", True,
                                    "accounts" if source_type in ("xtream", "stalker") else "sources")
        sources = reference_json(DATA_DIR + "/m3u-sources.json", [])
        if isinstance(sources, dict):
            sources = sources.get("sources", [])
        if isinstance(sources, list):
            for source in sources:
                if isinstance(source, dict) and source.get("kind") == "file":
                    reference_file(source.get("location"), "external", True)
        bindings = reference_json(DATA_DIR + "/m3u-xmltv.json", {})

        def xmltv_files(value):
            if isinstance(value, dict):
                kind = value.get("kind") or value.get("source_kind")
                location = value.get("location") or value.get("source_location")
                if kind == "file" and location:
                    reference_file(location, "external", True)
                for nested in value.values():
                    xmltv_files(nested)
            elif isinstance(value, list):
                for nested in value:
                    xmltv_files(nested)

        xmltv_files(bindings)
        # Corrupt or edited registries can hide a file which restoration will
        # overwrite. Preserve every existing destination even in that case.
        for entry, target in targets:
            if entry["kind"] == "external":
                add(target, entry["kind"], group=entry.get("group"))

        native = self._read(SETTINGS_PATH, optional=True)
        fragment = ("\n".join(line for line in native.decode("utf-8").splitlines()
                              if line.startswith(CONFIG_PREFIX) and native_group(line) in chosen) + "\n").encode("utf-8")
        native_settings(fragment)
        if set(chosen).intersection(("accounts", "settings")):
            records[SETTINGS_PATH] = dict(path=SETTINGS_PATH, kind="native", size=len(fragment),
                                         mode=0o600, uid=0, gid=0, payload=fragment)
        return sorted(records.values(), key=lambda entry: entry["path"])

    @staticmethod
    def _storage_path(path):
        return _within(path, "/media") or _within(path, "/mnt")

    def _media_path(self, path):
        try:
            path = _path(path)
        except BackupError:
            return False
        extension = os.path.splitext(path[:-5] if path.endswith(".part") else path)[1].lower()
        return self._storage_path(path) and (extension in VIDEO_EXTENSIONS or
            ("/.gtiptv-covers/" in path and extension in (".jpg", ".jpeg", ".png", ".webp")))

    def _allowed(self, path, kind):
        path = _path(path)
        if kind == "plugin":
            return _within(path, PLUGIN_DIR) and path != PLUGIN_DIR and not _transient(os.path.basename(path))
        if kind == "data":
            return (_within(path, DATA_DIR) or _within(path, SUBTITLE_DIR) or path in DATA_FILES)
        if kind == "native":
            return path == SETTINGS_PATH
        if kind == "media":
            return self._media_path(path)
        if kind == "external":
            return path.lower().endswith((".txt", ".m3u", ".m3u8", ".xml", ".xml.gz", ".gz"))
        return False

    def _settings_file(self, path, kind):
        """Shared policy for creation, legacy import and safety snapshots."""
        path = _path(path)
        if kind not in ("data", "external", "native") or not self._allowed(path, kind):
            return False
        if kind == "native":
            return True
        if path in REBUILDABLE_FILES or any(
                _within(path, directory) for directory in REBUILDABLE_DIRECTORIES):
            return False
        name = os.path.basename(path)
        if (_transient(name) or name.lower().endswith((".part", EXTENSION))
                or os.path.splitext(name)[1].lower() in VIDEO_EXTENSIONS):
            return False
        return True

    def restore_entries(self, manifest, groups=None):
        """Select only user data, including when reading an r2/r3 full archive."""
        entries = [entry for entry in manifest["files"]
                   if self._settings_file(entry["path"], entry["kind"])]
        if groups is None:
            return entries
        chosen = normalize_groups(groups)
        result = []
        for entry in entries:
            path, kind = entry["path"], entry["kind"]
            if path in SHARED_JSON:
                if set(chosen).intersection(("accounts", file_group(path))):
                    result.append(entry)
            elif kind == "native":
                if set(chosen).intersection(("accounts", "settings")):
                    result.append(entry)
            elif file_group(path, kind, entry.get("group")) in chosen:
                result.append(entry)
        return result

    def _archive_details(self, archive, manifest):
        """Prepare local UI data without changing the approved archive manifest."""
        records, available = [], set()
        declared = set(manifest.get("groups", GROUPS))
        for entry in self.restore_entries(manifest):
            record = dict(entry)
            path, kind = entry["path"], entry["kind"]
            if path in SHARED_JSON or kind == "native":
                if entry["size"] > MAX_DOCUMENT_BYTES:
                    raise BackupError("invalid")
                record["payload"] = archive.read(entry["member"])
                if path in SHARED_JSON:
                    try:
                        document = json.loads(record["payload"].decode("utf-8"))
                        if not isinstance(document, dict):
                            raise ValueError("Settings must be an object")
                    except (UnicodeError, ValueError):
                        raise BackupError("invalid")
                    for group in ("accounts", file_group(path)):
                        if project_document(path, document, (group,)):
                            available.add(group)
                else:
                    available.update(native_group(line) for line in native_settings(record["payload"]))
            else:
                available.add(file_group(path, kind, entry.get("group")))
            records.append(record)
        # An empty safety section represents the state before any such data
        # existed, and must remain selectable so the new files can be undone.
        if manifest.get("purpose") == "safety":
            available.update(declared)
        return dict(manifest=manifest, files=records,
                    groups=[group for group in GROUPS if group in available and group in declared])

    @staticmethod
    def select_records(records, groups):
        """Project the scanned snapshot for fast, exact dashboard totals."""
        chosen = normalize_groups(groups)
        selected = []
        for original in records:
            record = dict(original)
            path = record["path"]
            if path in SHARED_JSON:
                if not set(chosen).intersection(("accounts", file_group(path))):
                    continue
                document = json.loads(record["payload"].decode("utf-8"))
                record["payload"] = json.dumps(project_document(path, document, chosen),
                    ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                record["size"] = len(record["payload"])
            elif record["kind"] == "native":
                if not set(chosen).intersection(("accounts", "settings")):
                    continue
                lines = native_settings(record["payload"])
                record["payload"] = ("\n".join(line for line in lines
                    if native_group(line) in chosen) + "\n").encode("utf-8")
                record["size"] = len(record["payload"])
            elif file_group(path, record["kind"], record.get("group")) not in chosen:
                continue
            selected.append(record)
        return selected

    @staticmethod
    def backup_bytes(records):
        # Budget the actual manifest/ZIP overhead instead of reserving the
        # full 16 MiB parser limit for even a few tiny settings files.
        total = sum(entry["size"] for entry in records)
        metadata = sum(len(json.dumps({key: value for key, value in entry.items()
                                      if key not in ("payload", "fingerprint")},
                                      ensure_ascii=False).encode("utf-8")) + 512
                       for entry in records)
        return total + total // 100 + metadata + 64 * 1024

    def destination(self, folder):
        folder = os.path.realpath(folder)
        logical = self.logical(os.path.join(folder, "placeholder"))
        if any(_within(logical, owned) for owned in (PLUGIN_DIR, DATA_DIR, SUBTITLE_DIR)):
            raise BackupError("storage")
        if not os.path.isdir(folder) or not os.access(folder, os.W_OK | os.X_OK):
            raise BackupError("storage")
        if os.statvfs(folder).f_flag & getattr(os, "ST_RDONLY", 1):
            raise BackupError("storage")
        if (self.root == "/" and self._storage_path(folder)
                and not self._storage_available(logical)):
            raise BackupError("storage", folder)
        self._plain_parent(os.path.join(folder, "placeholder"))
        return folder

    @staticmethod
    def _cancel(cancel):
        if cancel is not None and cancel.is_set():
            raise BackupError("cancelled")

    @staticmethod
    def _space(folder, needed):
        info = os.statvfs(folder)
        available = info.f_bavail * (info.f_frsize or info.f_bsize)
        required = needed + SPACE_MARGIN
        if available < required:
            error = BackupError("space", folder)
            error.available_bytes = available
            error.required_bytes = required
            raise error

    def create(self, folder, progress=None, cancel=None, label="settings", groups=None):
        with self._operation(quiesce=True):
            return self._create(folder, progress, cancel, label, groups=groups)

    def _create(self, folder, progress=None, cancel=None, label="settings", records=None, groups=None):
        folder = self.destination(folder)
        chosen = normalize_groups(groups)
        if records is None:
            records = self.inventory(groups=chosen)
        if not records and label != "safety":
            raise BackupError("empty")
        total = sum(entry["size"] for entry in records)
        self._space(folder, self.backup_bytes(records))
        timestamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = "GTIPTVPlayerPro-{}-{}-{}-{}{}".format(
            PACKAGE_VERSION, label, timestamp, uuid.uuid4().hex[:8], EXTENSION)
        output = os.path.join(folder, filename)
        fd, temporary = tempfile.mkstemp(prefix=".gtiptv-backup-", dir=folder)
        os.close(fd)
        manifest = dict(format="GTIPTVPlayerPro", format_version=FORMAT_VERSION,
            scope=BACKUP_SCOPE, groups=list(chosen), purpose=label,
            package_version=PACKAGE_VERSION, created=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            files=[], total_bytes=total)
        processed = 0
        try:
            with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED,
                                 compresslevel=6, allowZip64=True) as archive:
                for index, record in enumerate(records):
                    self._cancel(cancel)
                    if not self._settings_file(record["path"], record["kind"]):
                        raise BackupError("source", record["path"])
                    entry = {key: value for key, value in record.items()
                             if key not in ("payload", "fingerprint")}
                    entry["member"] = "files/{:08d}".format(index)
                    digest = hashlib.sha256()
                    written = 0
                    source = None
                    if "payload" not in record:
                        host = self.host(record["path"])
                        self._plain_parent(host)
                        source = os.fdopen(os.open(host, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)), "rb")
                    try:
                        with archive.open(entry["member"], "w", force_zip64=True) as target:
                            if source is None:
                                payload = record["payload"]
                                digest.update(payload)
                                target.write(payload)
                                written = len(payload)
                            else:
                                before = os.fstat(source.fileno())
                                if (before.st_dev, before.st_ino, before.st_size,
                                        before.st_mtime_ns) != record["fingerprint"]:
                                    raise BackupError("changed")
                                while True:
                                    self._cancel(cancel)
                                    chunk = source.read(CHUNK_BYTES)
                                    if not chunk:
                                        break
                                    target.write(chunk)
                                    digest.update(chunk)
                                    written += len(chunk)
                                    if progress:
                                        progress("safety" if label == "safety" else "backup", processed + written, total)
                                after = os.fstat(source.fileno())
                                if (after.st_dev, after.st_ino, after.st_size,
                                        after.st_mtime_ns) != record["fingerprint"]:
                                    raise BackupError("changed")
                    finally:
                        if source is not None:
                            source.close()
                    if written != entry["size"]:
                        raise BackupError("changed")
                    entry["sha256"] = digest.hexdigest()
                    manifest["files"].append(entry)
                    processed += written
                encoded = json.dumps(manifest, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                if len(encoded) > MAX_MANIFEST_BYTES:
                    raise BackupError("source")
                archive.writestr("manifest.json", encoded)
            self._cancel(cancel)
            with open(temporary, "rb") as handle:
                os.fsync(handle.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, output)
            _sync_directory(folder)
            return dict(path=output, manifest=manifest)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _manifest(self, archive):
        infos = archive.infolist()
        if len(infos) > MAX_FILES + 1 or len({info.filename for info in infos}) != len(infos):
            raise BackupError("invalid")
        try:
            info = archive.getinfo("manifest.json")
        except KeyError:
            raise BackupError("invalid")
        if (info.file_size > MAX_MANIFEST_BYTES
                or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                or info.flag_bits & 1):
            raise BackupError("invalid")
        try:
            manifest = json.loads(archive.read(info).decode("utf-8"))
        except (UnicodeError, ValueError, zipfile.BadZipFile):
            raise BackupError("invalid")
        if (not isinstance(manifest, dict) or manifest.get("format") != "GTIPTVPlayerPro"
                or type(manifest.get("format_version")) is not int
                or manifest.get("format_version") not in (1, 2, FORMAT_VERSION)):
            raise BackupError("invalid")
        selectable = manifest["format_version"] == FORMAT_VERSION
        try:
            chosen = normalize_groups(manifest["groups"]) if selectable else GROUPS
        except (KeyError, TypeError, ValueError):
            raise BackupError("invalid")
        if selectable and list(chosen) != manifest["groups"]:
            raise BackupError("invalid")
        if selectable and manifest.get("purpose") not in ("settings", "safety"):
            raise BackupError("invalid")
        legacy = manifest["format_version"] == 1
        package_version = manifest.get("package_version")
        if (not isinstance(package_version, str)
                or (legacy and package_version not in LEGACY_VERSIONS)
                or (not legacy and not re.fullmatch(r"1\.(?:3|4)\.0(?:-r[0-9]+)?", package_version))):
            raise BackupError("version")
        if not legacy and manifest.get("scope") != BACKUP_SCOPE:
            raise BackupError("invalid")
        if legacy and info.compress_type != zipfile.ZIP_STORED:
            raise BackupError("invalid")
        if not isinstance(manifest.get("created"), str) or len(manifest["created"]) > 80:
            raise BackupError("invalid")
        try:
            created = datetime.datetime.fromisoformat(manifest["created"])
            if created.tzinfo is None:
                raise ValueError("Backup timestamp must include its timezone")
        except (ValueError, OverflowError):
            raise BackupError("invalid")
        entries = manifest.get("files")
        if (not isinstance(entries, list) or len(entries) > MAX_FILES
                or (not entries and not (selectable and manifest.get("purpose") == "safety"))):
            raise BackupError("invalid")
        paths = set()
        members = {"manifest.json"}
        total = 0
        native_count = 0
        for entry in entries:
            if not isinstance(entry, dict):
                raise BackupError("invalid")
            path, kind = entry.get("path"), entry.get("kind")
            member = entry.get("member")
            size, mode = entry.get("size"), entry.get("mode")
            if (not self._allowed(path, kind) or path in paths
                    or (not legacy and not self._settings_file(path, kind))
                    or not isinstance(member, str) or not re.fullmatch(r"files/[0-9]{8}", member)
                    or member in members or type(size) is not int or size < 0
                    or type(mode) is not int or not 0 <= mode <= 0o777
                    or not isinstance(entry.get("sha256"), str) or not _HASH.fullmatch(entry["sha256"])):
                raise BackupError("invalid")
            for key in ("uid", "gid"):
                if type(entry.get(key)) is not int or not 0 <= entry[key] < 2**31:
                    raise BackupError("invalid")
            aliases = entry.get("aliases", [])
            if not isinstance(aliases, list) or len(aliases) > 1000:
                raise BackupError("invalid")
            for alias in aliases:
                _path(alias)
            try:
                info = archive.getinfo(member)
            except KeyError:
                raise BackupError("invalid")
            if (info.file_size != size
                    or info.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or (legacy and info.compress_type != zipfile.ZIP_STORED)
                    or info.flag_bits & 1 or stat.S_ISLNK(info.external_attr >> 16)):
                raise BackupError("invalid")
            if kind == "native":
                native_count += 1
                if size > MAX_DOCUMENT_BYTES:
                    raise BackupError("invalid")
                fragment = archive.read(info)
                lines = native_settings(fragment)
                if selectable and any(native_group(line) not in chosen for line in lines):
                    raise BackupError("invalid")
            if selectable:
                if path in SHARED_JSON:
                    if entry.get("partial_json") is not True or size > MAX_DOCUMENT_BYTES:
                        raise BackupError("invalid")
                    try:
                        document = json.loads(archive.read(info).decode("utf-8"))
                        if not isinstance(document, dict) or document != project_document(path, document, chosen):
                            raise ValueError("Wrong backup section")
                    except (UnicodeError, ValueError):
                        raise BackupError("invalid")
                elif kind != "native" and (entry.get("partial_json") or
                        file_group(path, kind, entry.get("group")) not in chosen):
                    raise BackupError("invalid")
            paths.add(path)
            members.add(member)
            total += size
        if ((native_count > 1 if selectable else native_count != 1)
                or set(info.filename for info in infos) != members
                or type(manifest.get("total_bytes")) is not int or manifest["total_bytes"] != total):
            raise BackupError("invalid")
        if legacy and not any(entry["path"] == PLUGIN_DIR + "/plugin.py" for entry in entries):
            raise BackupError("invalid")
        return manifest

    def _open_archive(self, path):
        if os.path.islink(path):
            raise BackupError("invalid")
        logical = self.logical(path)
        host = self.host(logical)
        self._plain_parent(host)
        fd = os.open(host, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        handle = os.fdopen(fd, "rb")
        if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
            handle.close()
            raise BackupError("invalid")
        return handle

    def inspect(self, path, progress=None, cancel=None, details=False):
        with self._operation():
            with self._open_archive(path) as handle, zipfile.ZipFile(handle, "r") as archive:
                manifest = self._manifest(archive)
                self._verify(archive, manifest, progress, cancel)
                return self._archive_details(archive, manifest) if details else manifest

    def _verify(self, archive, manifest, progress=None, cancel=None):
        done = 0
        entries = self.restore_entries(manifest)
        total = sum(entry["size"] for entry in entries)
        for entry in entries:
            digest = hashlib.sha256()
            with archive.open(entry["member"], "r") as source:
                while True:
                    self._cancel(cancel)
                    chunk = source.read(CHUNK_BYTES)
                    if not chunk:
                        break
                    digest.update(chunk)
                    done += len(chunk)
                    if progress:
                        progress("verify", done, total)
            if digest.hexdigest() != entry["sha256"]:
                raise BackupError("invalid")

    def _storage_available(self, path):
        host = self.host(path)
        parent = os.path.dirname(host)
        if not os.path.isdir(parent):
            return False
        if self.root != "/":
            return True  # Isolated receiver filesystem used by offline tests.
        # Some images mount /media itself as a tiny tmpfs or autofs root.
        # That container does not mean a receiver's old disk path is mounted.
        # Require a mount below /media or /mnt before retaining that path.
        while self._storage_path(parent) and parent not in ("/media", "/mnt"):
            if os.path.ismount(parent):
                return True
            parent = os.path.dirname(parent)
        return False

    def _restore_target(self, entry):
        path, kind = entry["path"], entry["kind"]
        if kind == "external":
            parent = os.path.dirname(self.host(path))
            if (self._storage_path(path) and self._storage_available(path)
                    and os.access(parent, os.W_OK | os.X_OK)
                    and not os.statvfs(parent).f_flag & getattr(os, "ST_RDONLY", 1)):
                return path
            basename = os.path.basename(path)
            digest = hashlib.sha256(path.encode("utf-8")).hexdigest()[:24]
            return DATA_DIR + "/restored-sources/" + digest + "/" + basename
        if kind == "media" and not self._storage_available(path):
            raise BackupError("storage")
        return path

    def _atomic_json(self, path, value):
        host = self.host(path)
        self._plain_parent(host, create=True)
        fd, temporary = tempfile.mkstemp(prefix=".gtiptv-restore-", dir=os.path.dirname(host))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, host)
            _sync_directory(os.path.dirname(host))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def restore(self, path, progress=None, cancel=None, approved_manifest=None, safety_folder=None, groups=None):
        with self._operation(quiesce=True, restoring=True) as publish_downloads:
            if os.path.lexists(self.host(JOURNAL_PATH)):
                self._recover()
            with self._open_archive(path) as handle, zipfile.ZipFile(handle, "r") as archive:
                manifest = self._manifest(archive)
                if approved_manifest is not None and manifest != approved_manifest:
                    raise BackupError("changed")
                self._verify(archive, manifest, progress, cancel)
                chosen = manifest.get("groups") if manifest["format_version"] == FORMAT_VERSION else None
                if groups is not None:
                    try:
                        requested = normalize_groups(groups)
                    except (TypeError, ValueError):
                        raise BackupError("invalid")
                    available = set(self._archive_details(archive, manifest)["groups"])
                    if not set(requested).issubset(available):
                        raise BackupError("invalid")
                    chosen = requested
                entries = self.restore_entries(manifest, chosen)
                total = sum(entry["size"] for entry in entries)
                targets = [(entry, self._restore_target(entry)) for entry in entries]
                if len({target for _, target in targets}) != len(targets):
                    raise BackupError("invalid")
                replacements = {}
                for entry, target in targets:
                    if entry["kind"] in ("external", "media"):
                        replacements[entry["path"]] = target
                        for alias in entry.get("aliases", []):
                            replacements[alias] = target
                # Fail before any write if a destination has an unsafe ancestor.
                needed = {}
                for entry, target in targets:
                    host = self.host(target)
                    parent = os.path.dirname(host)
                    while not os.path.exists(parent):
                        parent = os.path.dirname(parent)
                    self._plain_parent(os.path.join(parent, "placeholder"))
                    if os.path.lexists(host):
                        self._plain_parent(host)
                    device = os.stat(parent).st_dev
                    folder, count = needed.get(device, (parent, 0))
                    extra = len(self._read(SETTINGS_PATH, optional=True)) if entry["kind"] == "native" else 0
                    needed[device] = (folder, count + entry["size"] + extra)
                safety_folder = self.destination(safety_folder or os.path.dirname(os.path.realpath(path)))
                safety_records = self.inventory(safety=True, targets=targets, groups=chosen)
                safety_device = os.stat(safety_folder).st_dev
                folder, count = needed.get(safety_device, (safety_folder, 0))
                needed[safety_device] = (folder, count + self.backup_bytes(safety_records))
                for folder, count in needed.values():
                    self._space(folder, count)
                safety = self._create(safety_folder, progress, cancel, "safety", records=safety_records, groups=chosen)
                journal = dict(version=1, phase="staging", safety=safety["path"], entries=[])
                # Persist the complete plan once instead of rewriting the
                # journal for each source or settings file.
                for _, target in targets:
                    host = self.host(target)
                    self._plain_parent(host, create=True)
                    token = uuid.uuid4().hex
                    journal["entries"].append(dict(target=target,
                        stage=self.logical(os.path.join(os.path.dirname(host), ".gtiptv-restore-" + token + ".new")),
                        previous=self.logical(os.path.join(os.path.dirname(host), ".gtiptv-restore-" + token + ".old")),
                        existed=os.path.exists(host)))
                expected = {target for _, target in targets}
                for entry in safety["manifest"]["files"]:
                    target = entry["path"]
                    if (entry["kind"] != "data" or target in expected
                            or (chosen is not None and target in SHARED_JSON)):
                        continue
                    host = self.host(target)
                    self._plain_parent(host)
                    token = uuid.uuid4().hex
                    journal["entries"].append(dict(target=target,
                        stage=self.logical(os.path.join(os.path.dirname(host), ".gtiptv-restore-" + token + ".new")),
                        previous=self.logical(os.path.join(os.path.dirname(host), ".gtiptv-restore-" + token + ".old")),
                        existed=True, delete=True))
                self._atomic_json(JOURNAL_PATH, journal)
                done = 0
                def recovery_error():
                    if self.root == "/" and any(entry["kind"] == "native" for entry, unused_target in targets):
                        from .settings import defer_native_restore
                        defer_native_restore()
                    error = BackupError("recovery", safety["path"])
                    error.restart_required = True
                    return error
                try:
                    for (entry, target), row in zip(targets, journal["entries"]):
                        self._cancel(cancel)
                        host = self.host(target)
                        self._plain_parent(host)
                        stage = self.host(row["stage"])
                        fd = os.open(stage, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                        digest = hashlib.sha256()
                        with os.fdopen(fd, "wb") as output, archive.open(entry["member"]) as source:
                            if entry["kind"] == "native":
                                payload = source.read(MAX_DOCUMENT_BYTES + 1)
                                digest.update(payload)
                                output.write(merge_native_settings(self._read(SETTINGS_PATH, optional=True), payload, chosen))
                                done += len(payload)
                            elif target in SHARED_JSON and (chosen is not None or entry.get("partial_json")):
                                payload = source.read(MAX_DOCUMENT_BYTES + 1)
                                digest.update(payload)
                                fragment = project_document(target, json.loads(payload.decode("utf-8")), chosen or GROUPS)
                                current = self._json(target, {})
                                value = merge_document(target, current, fragment, chosen or GROUPS)
                                output.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                                done += len(payload)
                            elif replacements and target in (
                                    DATA_DIR + "/playlist-files.json", DATA_DIR + "/m3u-sources.json",
                                    DATA_DIR + "/m3u-xmltv.json", DATA_DIR + "/downloads.json",
                                    DATA_DIR + "/downloads-removed.json"):
                                payload = source.read(MAX_DOCUMENT_BYTES + 1)
                                if len(payload) > MAX_DOCUMENT_BYTES:
                                    raise BackupError("invalid")
                                digest.update(payload)
                                try:
                                    value = _replace_strings(json.loads(payload.decode("utf-8")), replacements)
                                except (UnicodeError, ValueError):
                                    # Safety archives retain damaged registries
                                    # verbatim so the previous state is recoverable.
                                    output.write(payload)
                                else:
                                    output.write(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
                                done += len(payload)
                            else:
                                while True:
                                    self._cancel(cancel)
                                    chunk = source.read(CHUNK_BYTES)
                                    if not chunk:
                                        break
                                    digest.update(chunk)
                                    output.write(chunk)
                                    done += len(chunk)
                                    if progress:
                                        progress("restore", done, total)
                            if digest.hexdigest() != entry["sha256"]:
                                raise BackupError("invalid")
                            output.flush()
                            os.fsync(output.fileno())
                            os.fchmod(output.fileno(), entry["mode"])
                            if os.geteuid() == 0:
                                os.fchown(output.fileno(), entry["uid"], entry["gid"])
                    self._cancel(cancel)
                    self._check_idle()
                    journal["phase"] = "committing"
                    self._atomic_json(JOURNAL_PATH, journal)
                    for row in journal["entries"]:
                        host, stage = self.host(row["target"]), self.host(row["stage"])
                        self._plain_parent(host)
                        if row["existed"]:
                            os.replace(host, self.host(row["previous"]))
                        if not row.get("delete"):
                            os.replace(stage, host)
                    for directory in {os.path.dirname(self.host(row["target"])) for row in journal["entries"]}:
                        _sync_directory(directory)
                    if publish_downloads is not None:
                        publish_downloads()
                    journal["phase"] = "committed"
                    self._atomic_json(JOURNAL_PATH, journal)
                    # A completed transaction must never be rolled back because
                    # deleting an obsolete staging file failed. Startup retries
                    # cleanup from the durable committed journal.
                    try:
                        self._cleanup_journal(journal)
                    except OSError:
                        pass
                    if self.root == "/" and any(entry["kind"] == "native" for entry, unused_target in targets):
                        # GUI ConfigElements are reloaded on the GUI poll.
                        # Reject native settings saves during that short gap.
                        from .settings import defer_native_restore
                        defer_native_restore()
                    return dict(safety=safety["path"], manifest=manifest,
                                downloads_published=publish_downloads is not None)
                except Exception:
                    if journal["phase"] == "committed":
                        # Publishing the marker can succeed before directory
                        # fsync raises. Never roll targets back while recovery
                        # could still observe a committed cleanup-only marker.
                        rollback_intent = dict(journal, phase="committing")
                        try:
                            self._atomic_json(JOURNAL_PATH, rollback_intent)
                        except Exception:
                            # Keep every promoted file and its prior copy.
                            # Startup can safely choose the durable old/new
                            # journal phase without finding a partial rollback.
                            raise recovery_error()
                        journal = rollback_intent
                    try:
                        self._rollback(journal)
                    except Exception:
                        raise recovery_error()
                    raise

    def _journal(self):
        value = self._json(JOURNAL_PATH)
        if (not isinstance(value, dict) or value.get("version") != 1
                or value.get("phase") not in ("staging", "committing", "committed")
                or not isinstance(value.get("entries"), list) or len(value["entries"]) > 2 * MAX_FILES):
            raise BackupError("recovery")
        for row in value["entries"]:
            if not isinstance(row, dict) or type(row.get("existed")) is not bool:
                raise BackupError("recovery")
            target = _path(row.get("target"))
            if not (self._allowed(target, "data") or self._allowed(target, "plugin")
                    or self._allowed(target, "native") or self._media_path(target)
                    or (self._storage_path(target) and self._allowed(target, "external"))):
                raise BackupError("recovery")
            for key, suffix in (("stage", ".new"), ("previous", ".old")):
                temporary = _path(row.get(key))
                if (os.path.dirname(temporary) != os.path.dirname(target)
                        or not re.fullmatch(r"\.gtiptv-restore-[0-9a-f]{32}" + re.escape(suffix), os.path.basename(temporary))):
                    raise BackupError("recovery")
                self._plain_parent(self.host(temporary))
        return value

    def _cleanup_journal(self, journal):
        for row in journal["entries"]:
            for key in ("stage", "previous"):
                host = self.host(row[key])
                if os.path.exists(host):
                    os.unlink(host)
        for directory in {os.path.dirname(self.host(row["target"])) for row in journal["entries"]}:
            _sync_directory(directory)
        os.unlink(self.host(JOURNAL_PATH))
        _sync_directory(os.path.dirname(self.host(JOURNAL_PATH)))

    def _rollback(self, journal):
        for row in reversed(journal["entries"]):
            host, stage, previous = (self.host(row[key]) for key in ("target", "stage", "previous"))
            self._plain_parent(host)
            if os.path.exists(previous):
                os.replace(previous, host)
            elif journal["phase"] != "staging" and not row["existed"] and not os.path.exists(stage) and os.path.exists(host):
                os.unlink(host)
            if os.path.exists(stage):
                os.unlink(stage)
        for directory in {os.path.dirname(self.host(row["target"])) for row in journal["entries"]}:
            _sync_directory(directory)
        os.unlink(self.host(JOURNAL_PATH))
        _sync_directory(os.path.dirname(self.host(JOURNAL_PATH)))

    def recover(self):
        if not os.path.lexists(self.host(JOURNAL_PATH)):
            return False
        with self._operation(quiesce=True):
            journal = self._journal()
            if journal["phase"] == "committed":
                # Cleanup-only recovery must not discard later paused download
                # payloads: the archive and runtime were already committed.
                self._cleanup_journal(journal)
                return True
            with ExitStack() as stack:
                if self.root == "/":
                    from .downloads import restore_guard
                    stack.enter_context(restore_guard())
                try:
                    return self._recover()
                finally:
                    self._refresh_store_caches()

    def _recover(self):
        journal = self._journal()
        if journal["phase"] == "committed":
            self._cleanup_journal(journal)
        else:
            self._rollback(journal)
        return True

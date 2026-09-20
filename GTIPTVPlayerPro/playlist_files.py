# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

"""Persistent TXT playlist-file selection for Xtream and Stalker sources.

The registry stores paths only.  Credentials remain in the user-selected TXT
files and an inactive file is never parsed by the player.
"""

from __future__ import absolute_import

import json
import os
import stat
import threading
import time


PLAYLIST_FILE_TYPES = ("xtream", "stalker")
MAX_PLAYLIST_FILES_PER_TYPE = 100
PLAYLIST_FILE_PAGE_SIZE = 12
PLAYLIST_FILES_CONNECTED = "__GTIPTV_PLAYLIST_FILES_CONNECTED__"
DEFAULT_PLAYLIST_FILE_REGISTRY_PATH = (
    "/etc/enigma2/gtiptvplayer/playlist-files.json"
)
LEGACY_XTREAM_PATH = "/etc/enigma2/gtiptvplayer/playlists.txt"
LEGACY_STALKER_PATH = "/etc/enigma2/gtiptvplayer/stalker.txt"
_REGISTRY_VERSION = 1
_REGISTRY_MODE = 0o600
_REGISTRY_DIRECTORY_MODE = 0o700
_REGISTRY_MAX_BYTES = 256 * 1024
_LOCK = threading.RLock()


class PlaylistFileRegistryError(ValueError):
    pass


def _empty_document():
    return {
        "version": _REGISTRY_VERSION,
        "legacy_migrated": False,
        "types": {
            source_type: {
                "slots": [None] * MAX_PLAYLIST_FILES_PER_TYPE,
                "active": None,
            }
            for source_type in PLAYLIST_FILE_TYPES
        },
    }


def _source_type(value):
    value = str(value or "").strip().lower()
    if value not in PLAYLIST_FILE_TYPES:
        raise PlaylistFileRegistryError("Unknown playlist file type")
    return value


def _slot_index(value):
    try:
        value = int(value)
    except (TypeError, ValueError, OverflowError):
        raise PlaylistFileRegistryError("Invalid playlist slot")
    if not 0 <= value < MAX_PLAYLIST_FILES_PER_TYPE:
        raise PlaylistFileRegistryError("Invalid playlist slot")
    return value


def _normalise_txt_path(path, must_exist=True):
    path = os.path.abspath(os.path.expanduser(str(path or "").strip()))
    if not path or not path.lower().endswith(".txt"):
        raise PlaylistFileRegistryError("Only TXT files are supported")
    if must_exist:
        try:
            details = os.lstat(path)
        except OSError:
            raise PlaylistFileRegistryError("The playlist file was not found")
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            raise PlaylistFileRegistryError("The playlist file is unsafe")
    return path


def _path_identity(path):
    absolute = os.path.abspath(path)
    try:
        details = os.stat(absolute)
    except OSError:
        return ("path", os.path.normcase(os.path.realpath(absolute)))
    return ("inode", int(details.st_dev), int(details.st_ino))


def _normalise_document(value):
    if not isinstance(value, dict):
        raise PlaylistFileRegistryError("The playlist registry is invalid")
    result = _empty_document()
    result["legacy_migrated"] = bool(value.get("legacy_migrated", False))
    types = value.get("types")
    if not isinstance(types, dict):
        raise PlaylistFileRegistryError("The playlist registry is invalid")
    seen = set()
    for source_type in PLAYLIST_FILE_TYPES:
        source = types.get(source_type)
        if not isinstance(source, dict):
            raise PlaylistFileRegistryError("The playlist registry is invalid")
        slots = source.get("slots")
        if not isinstance(slots, list):
            raise PlaylistFileRegistryError("The playlist registry is invalid")
        cleaned = [None] * MAX_PLAYLIST_FILES_PER_TYPE
        for index, path in enumerate(slots[:MAX_PLAYLIST_FILES_PER_TYPE]):
            if path in (None, ""):
                continue
            path = _normalise_txt_path(path, must_exist=False)
            identity = _path_identity(path)
            if identity in seen:
                raise PlaylistFileRegistryError(
                    "The playlist registry contains a duplicate path"
                )
            seen.add(identity)
            cleaned[index] = path
        active = source.get("active")
        if active is not None:
            active = _slot_index(active)
            if cleaned[active] is None:
                active = None
        result["types"][source_type]["slots"] = cleaned
        result["types"][source_type]["active"] = active
    return result


def _has_user_data(path):
    try:
        details = os.lstat(path)
        if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
            return False
        with open(path, "rb") as handle:
            payload = handle.read(4 * 1024 * 1024 + 1)
    except (IOError, OSError):
        return False
    if len(payload) > 4 * 1024 * 1024:
        return False
    for line in payload.decode("utf-8-sig", "replace").splitlines():
        value = line.strip()
        if value and not value.startswith("#"):
            return True
    return False


class PlaylistFileRegistry(object):
    def __init__(
        self,
        path=DEFAULT_PLAYLIST_FILE_REGISTRY_PATH,
        legacy_xtream_path=LEGACY_XTREAM_PATH,
        legacy_stalker_path=LEGACY_STALKER_PATH,
    ):
        self.path = os.path.abspath(path)
        self.legacy_paths = {
            "xtream": os.path.abspath(legacy_xtream_path),
            "stalker": os.path.abspath(legacy_stalker_path),
        }

    def _ensure_directory(self):
        directory = os.path.dirname(self.path) or "."
        try:
            details = os.lstat(directory)
        except OSError:
            os.makedirs(directory, _REGISTRY_DIRECTORY_MODE)
            details = os.lstat(directory)
        current_uid = getattr(os, "geteuid", lambda: details.st_uid)()
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISDIR(details.st_mode)
            or details.st_uid != current_uid
        ):
            raise PlaylistFileRegistryError("The playlist registry directory is unsafe")
        canonical = os.path.abspath(
            os.path.dirname(DEFAULT_PLAYLIST_FILE_REGISTRY_PATH)
        )
        if os.path.abspath(directory) == canonical:
            os.chmod(directory, _REGISTRY_DIRECTORY_MODE)
        return directory

    def _read(self):
        try:
            details = os.lstat(self.path)
        except FileNotFoundError:
            return None
        if (
            stat.S_ISLNK(details.st_mode)
            or not stat.S_ISREG(details.st_mode)
            or details.st_uid
            != getattr(os, "geteuid", lambda: details.st_uid)()
            or details.st_nlink != 1
            or details.st_size > _REGISTRY_MAX_BYTES
        ):
            raise PlaylistFileRegistryError("The playlist registry file is unsafe")
        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0)
        descriptor = os.open(self.path, flags)
        try:
            opened = os.fstat(descriptor)
            if (
                opened.st_dev != details.st_dev
                or opened.st_ino != details.st_ino
            ):
                raise PlaylistFileRegistryError(
                    "The playlist registry changed while opening"
                )
            with os.fdopen(descriptor, "rb") as handle:
                descriptor = None
                payload = handle.read(_REGISTRY_MAX_BYTES + 1)
        finally:
            if descriptor is not None:
                os.close(descriptor)
        if len(payload) > _REGISTRY_MAX_BYTES:
            raise PlaylistFileRegistryError("The playlist registry is too large")
        try:
            value = json.loads(payload.decode("utf-8"))
        except (TypeError, ValueError, UnicodeError):
            raise PlaylistFileRegistryError("The playlist registry is invalid")
        return _normalise_document(value)

    def _write(self, document):
        document = _normalise_document(document)
        directory = self._ensure_directory()
        payload = (
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")
        if len(payload) > _REGISTRY_MAX_BYTES:
            raise PlaylistFileRegistryError("The playlist registry is too large")
        basename = os.path.basename(self.path)
        temporary = os.path.join(
            directory,
            ".{}.{}.{}.tmp".format(
                basename,
                os.getpid(),
                int(time.monotonic() * 1000000),
            ),
        )
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        flags |= getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
        descriptor = None
        try:
            descriptor = os.open(temporary, flags, _REGISTRY_MODE)
            offset = 0
            while offset < len(payload):
                written = os.write(descriptor, payload[offset:])
                if written <= 0:
                    raise OSError("Playlist registry write failed")
                offset += written
            os.fchmod(descriptor, _REGISTRY_MODE)
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = None
            os.replace(temporary, self.path)
            try:
                directory_descriptor = os.open(
                    directory,
                    os.O_RDONLY
                    | getattr(os, "O_CLOEXEC", 0)
                    | getattr(os, "O_DIRECTORY", 0),
                )
                try:
                    os.fsync(directory_descriptor)
                finally:
                    os.close(directory_descriptor)
            except OSError:
                pass
        finally:
            if descriptor is not None:
                os.close(descriptor)
            try:
                details = os.lstat(temporary)
            except OSError:
                details = None
            if details is not None and stat.S_ISREG(details.st_mode):
                try:
                    os.unlink(temporary)
                except OSError:
                    pass

    def _migrate(self, document):
        if document["legacy_migrated"]:
            return False
        used = set()
        for source_type in PLAYLIST_FILE_TYPES:
            source = document["types"][source_type]
            for path in source["slots"]:
                if path:
                    used.add(_path_identity(path))
        for source_type in PLAYLIST_FILE_TYPES:
            path = self.legacy_paths[source_type]
            if not _has_user_data(path):
                continue
            identity = _path_identity(path)
            if identity in used:
                continue
            source = document["types"][source_type]
            source["slots"][0] = path
            source["active"] = 0
            used.add(identity)
        document["legacy_migrated"] = True
        return True

    def _load(self):
        document = self._read()
        changed = False
        if document is None:
            document = _empty_document()
            changed = True
        if self._migrate(document):
            changed = True
        if changed:
            self._write(document)
        return document

    def slots(self, source_type):
        source_type = _source_type(source_type)
        with _LOCK:
            return list(self._load()["types"][source_type]["slots"])

    def active_index(self, source_type):
        source_type = _source_type(source_type)
        with _LOCK:
            return self._load()["types"][source_type]["active"]

    def active_path(self, source_type):
        source_type = _source_type(source_type)
        with _LOCK:
            source = self._load()["types"][source_type]
            active = source["active"]
            return source["slots"][active] if active is not None else None

    def paths(self, source_type):
        return [path for path in self.slots(source_type) if path]

    def set_slot(self, source_type, index, path):
        source_type = _source_type(source_type)
        index = _slot_index(index)
        path = _normalise_txt_path(path)
        identity = _path_identity(path)
        with _LOCK:
            document = self._load()
            for registered_type in PLAYLIST_FILE_TYPES:
                for registered_index, registered_path in enumerate(
                    document["types"][registered_type]["slots"]
                ):
                    if not registered_path:
                        continue
                    if (
                        registered_type == source_type
                        and registered_index == index
                    ):
                        continue
                    if _path_identity(registered_path) == identity:
                        raise PlaylistFileRegistryError(
                            "The playlist file is already registered"
                        )
            source = document["types"][source_type]
            previous_path = source["slots"][index]
            source["slots"][index] = path
            if (
                source["active"] == index
                and previous_path
                and _path_identity(previous_path) != identity
            ):
                # Choosing a replacement only fills the slot.  It becomes the
                # live source after the user explicitly presses Connect.
                source["active"] = None
            self._write(document)
        return path

    def activate(self, source_type, index):
        source_type = _source_type(source_type)
        index = _slot_index(index)
        with _LOCK:
            document = self._load()
            path = document["types"][source_type]["slots"][index]
            if not path:
                raise PlaylistFileRegistryError("The playlist slot is empty")
            _normalise_txt_path(path)
            document["types"][source_type]["active"] = index
            self._write(document)
        return path

    def register_written_path(self, source_type, path):
        source_type = _source_type(source_type)
        path = _normalise_txt_path(path)
        identity = _path_identity(path)
        with _LOCK:
            document = self._load()
            match = None
            empty = None
            for registered_type in PLAYLIST_FILE_TYPES:
                slots = document["types"][registered_type]["slots"]
                for index, registered_path in enumerate(slots):
                    if registered_path is None and registered_type == source_type:
                        if empty is None:
                            empty = index
                        continue
                    if registered_path and _path_identity(registered_path) == identity:
                        if registered_type != source_type:
                            raise PlaylistFileRegistryError(
                                "The playlist file is registered for another type"
                            )
                        match = index
            if match is None:
                if empty is None:
                    raise PlaylistFileRegistryError("The playlist file limit was reached")
                match = empty
                document["types"][source_type]["slots"][match] = path
            document["types"][source_type]["active"] = match
            self._write(document)
        return match

    def clear_slot(self, source_type, index):
        source_type = _source_type(source_type)
        index = _slot_index(index)
        with _LOCK:
            document = self._load()
            source = document["types"][source_type]
            path = source["slots"][index]
            if path is None:
                return None
            source["slots"][index] = None
            if source["active"] == index:
                source["active"] = None
            self._write(document)
        return path

    def rename_slot(self, source_type, index, new_name):
        source_type = _source_type(source_type)
        index = _slot_index(index)
        new_name = str(new_name or "").strip()
        if (
            not new_name
            or new_name in (".", "..")
            or os.path.basename(new_name) != new_name
            or any(ord(character) < 32 for character in new_name)
        ):
            raise PlaylistFileRegistryError("The playlist file name is invalid")
        with _LOCK:
            document = self._load()
            source = document["types"][source_type]
            old_path = source["slots"][index]
            if not old_path:
                raise PlaylistFileRegistryError("The playlist slot is empty")
            old_path = _normalise_txt_path(old_path)
            old_extension = os.path.splitext(old_path)[1]
            if not new_name.lower().endswith(".txt"):
                new_name += old_extension
            new_path = os.path.join(os.path.dirname(old_path), new_name)
            new_path = _normalise_txt_path(new_path, must_exist=False)
            if os.path.normcase(os.path.abspath(new_path)) == os.path.normcase(
                os.path.abspath(old_path)
            ):
                return old_path
            if os.path.lexists(new_path):
                raise PlaylistFileRegistryError("A file with this name already exists")
            os.rename(old_path, new_path)
            source["slots"][index] = new_path
            try:
                self._write(document)
            except Exception:
                try:
                    if not os.path.lexists(old_path):
                        os.rename(new_path, old_path)
                except OSError:
                    pass
                raise
        return new_path

    def delete_slot_file(self, source_type, index):
        source_type = _source_type(source_type)
        index = _slot_index(index)
        with _LOCK:
            document = self._load()
            source = document["types"][source_type]
            path = source["slots"][index]
            if not path:
                raise PlaylistFileRegistryError("The playlist slot is empty")
            path = _normalise_txt_path(path)
            previous_active = source["active"]
            source["slots"][index] = None
            if previous_active == index:
                source["active"] = None
            self._write(document)
            try:
                os.unlink(path)
            except Exception:
                source["slots"][index] = path
                source["active"] = previous_active
                self._write(document)
                raise
        return path


def default_registry():
    return PlaylistFileRegistry()


def selected_playlist_path(source_type):
    return default_registry().active_path(source_type)


def selected_playlist_paths(source_type):
    path = selected_playlist_path(source_type)
    return (path,) if path else ()


def playlist_write_path(source_type, legacy_path):
    """Return the explicit active file, or the legacy file for first setup.

    A registered removable-media path is authoritative.  If it disappeared,
    writing must fail instead of silently recreating it or changing location.
    """
    source_type = _source_type(source_type)
    registry = default_registry()
    path = registry.active_path(source_type)
    if path:
        return _normalise_txt_path(path)
    legacy_path = _normalise_txt_path(legacy_path, must_exist=False)
    legacy_identity = _path_identity(legacy_path)
    free_slot = False
    for registered_type in PLAYLIST_FILE_TYPES:
        for registered_path in registry.slots(registered_type):
            if registered_path is None:
                if registered_type == source_type:
                    free_slot = True
                continue
            if _path_identity(registered_path) != legacy_identity:
                continue
            if registered_type != source_type:
                raise PlaylistFileRegistryError(
                    "The legacy playlist file is registered for another type"
                )
            return legacy_path
    if not free_slot:
        raise PlaylistFileRegistryError("The playlist file limit was reached")
    return legacy_path


def register_written_playlist(source_type, path):
    return default_registry().register_written_path(source_type, path)

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Private, bounded subtitle files associated with existing VOD resume keys."""

import hashlib
import json
import os
import re
import stat
import tempfile
import threading

from .subtitle_settings import MAX_OFFSET_MS
from .subssupport_bridge import normalize_external_subtitle_sync


DEFAULT_SUBTITLE_RESUME_DIR = "/etc/enigma2/gtiptvplayerpro-subtitle-cache"
MAX_SUBTITLE_BYTES = 2 * 1024 * 1024
MAX_METADATA_BYTES = 4096
MAX_CACHE_ENTRIES = 250
MAX_CACHE_BYTES = 32 * 1024 * 1024
_KEY = re.compile(r"[0-9a-f]{64}\Z")
_FILE = re.compile(r"[0-9a-f]{64}-[0-9a-f]{64}\.(srt|ass|ssa|sub|vtt|txt)\Z")
_LOCK = threading.RLock()


def _read_file(path, limit):
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    if os.path.islink(path):
        raise OSError("subtitle file is a symlink")
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise OSError("subtitle file is not a bounded regular file")
        content = handle.read(limit + 1)
    if len(content) > limit:
        raise OSError("subtitle file exceeds size limit")
    return content


class SubtitleResumeStore(object):
    def __init__(self, root=DEFAULT_SUBTITLE_RESUME_DIR):
        self.root = os.path.abspath(root)

    def _directory(self, create=False):
        if create:
            os.makedirs(self.root, mode=0o700, exist_ok=True)
        info = os.lstat(self.root)
        if not stat.S_ISDIR(info.st_mode):
            raise OSError("unsafe subtitle resume directory")
        if create:
            os.chmod(self.root, 0o700)

    def _write(self, name, content):
        descriptor, temporary = tempfile.mkstemp(prefix=".save-", dir=self.root)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, os.path.join(self.root, name))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    @staticmethod
    def _track(value):
        if not isinstance(value, (tuple, list)) or len(value) != 5:
            return None
        if any(not isinstance(item, (int, str)) or isinstance(item, bool)
               or (isinstance(item, str) and len(item) > 256) for item in value):
            return None
        return list(value)

    @staticmethod
    def _metadata(key, content):
        value = json.loads(content.decode("utf-8"))
        if not isinstance(value, dict) or value.get("version") != 1:
            return None
        kind = value.get("kind")
        if kind == "disabled":
            return {"kind": "disabled"}
        if kind == "embedded":
            track = SubtitleResumeStore._track(value.get("track"))
            return {"kind": kind, "track": track} if track is not None else None
        name = value.get("file")
        if (
            kind not in ("online", "external")
            or not isinstance(name, str)
            or not _FILE.fullmatch(name)
            or not name.startswith(key + "-")
        ):
            return None
        offset = max(-MAX_OFFSET_MS, min(MAX_OFFSET_MS, int(value.get("offset_ms", 0))))
        return {"kind": kind, "file": name, "offset_ms": offset,
                "external_sync": normalize_external_subtitle_sync(value.get("external_sync"))}

    def get(self, key):
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            return None
        with _LOCK:
            try:
                self._directory()
                value = self._metadata(key, _read_file(
                    os.path.join(self.root, key + ".json"), MAX_METADATA_BYTES
                ))
                if not value or value["kind"] in ("disabled", "embedded"):
                    return value
                path = os.path.join(self.root, value["file"])
                content = _read_file(path, MAX_SUBTITLE_BYTES)
                if hashlib.sha256(content).hexdigest() != value["file"][65:129]:
                    return None
                return {"kind": value["kind"], "path": path,
                        "offset_ms": value["offset_ms"],
                        "external_sync": value["external_sync"]}
            except (OSError, TypeError, ValueError, OverflowError):
                return None

    def remember(self, key, state):
        """Commit the selected file before its small, credential-free index."""
        if not isinstance(key, str) or not _KEY.fullmatch(key):
            return False
        if not isinstance(state, dict):
            return False
        with _LOCK:
            try:
                kind = state.get("kind")
                if kind == "disabled":
                    value = {"version": 1, "kind": "disabled"}
                    content = None
                elif kind == "embedded":
                    track = self._track(state.get("track"))
                    if track is None:
                        return False
                    value = {"version": 1, "kind": kind, "track": track}
                    content = None
                elif kind in ("online", "external"):
                    path = state.get("path")
                    if not isinstance(path, str) or not path:
                        return False
                    content = _read_file(path, MAX_SUBTITLE_BYTES)
                    if not content:
                        return False
                    digest = hashlib.sha256(content).hexdigest()
                    extension = os.path.splitext(path)[1].lower().lstrip(".")
                    if kind == "online":
                        extension = "srt"
                    if extension not in ("srt", "ass", "ssa", "sub", "vtt", "txt"):
                        return False
                    name = "{}-{}.{}".format(key, digest, extension)
                    offset = max(-MAX_OFFSET_MS, min(
                        MAX_OFFSET_MS, int(state.get("offset_ms", 0))
                    ))
                    value = {"version": 1, "kind": kind,
                             "file": name, "offset_ms": offset}
                    if kind == "external":
                        value["external_sync"] = normalize_external_subtitle_sync(
                            state.get("external_sync")
                        )
                else:
                    return False
                self._directory(create=True)
                if content is not None:
                    self._write(value["file"], content)
                self._write(key + ".json", json.dumps(
                    value, separators=(",", ":"), sort_keys=True
                ).encode("utf-8"))
                self._prune(key)
                return True
            except (OSError, TypeError, ValueError, OverflowError):
                return False

    def _prune(self, current_key):
        """Remove only this cache's expired indices and unreferenced files."""
        try:
            entries = []
            for entry in os.scandir(self.root):
                if not entry.name.endswith(".json") or not entry.is_file(follow_symlinks=False):
                    continue
                key = entry.name[:-5]
                if not _KEY.fullmatch(key):
                    continue
                try:
                    value = self._metadata(key, _read_file(entry.path, MAX_METADATA_BYTES))
                    if not value:
                        continue
                    name = value.get("file", "")
                    size = 0
                    if name:
                        info = os.lstat(os.path.join(self.root, name))
                        if not stat.S_ISREG(info.st_mode):
                            continue
                        size = info.st_size
                    entries.append((key, entry.stat(follow_symlinks=False).st_mtime,
                                    name, max(0, size)))
                except (OSError, TypeError, ValueError, OverflowError):
                    continue
            entries.sort(key=lambda value: (value[0] == current_key, value[1]), reverse=True)
            retained = set()
            total = 0
            for index, (key, unused_mtime, name, size) in enumerate(entries):
                if index < MAX_CACHE_ENTRIES and total + size <= MAX_CACHE_BYTES:
                    total += size
                    if name:
                        retained.add(name)
                    continue
                os.unlink(os.path.join(self.root, key + ".json"))
            for entry in os.scandir(self.root):
                if (_FILE.fullmatch(entry.name) and entry.name not in retained
                        and entry.is_file(follow_symlinks=False)):
                    os.unlink(entry.path)
        except OSError:
            pass

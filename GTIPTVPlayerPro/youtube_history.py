# -*- coding: utf-8 -*-
"""Bounded, persistent YouTube search and successful-playback histories."""
import json
import os
import re
import tempfile
import threading
import unicodedata

HISTORY_PATH = "/etc/enigma2/gtiptvplayer/youtube-history.json"
HISTORY_LIMIT = 50
PLAYBACK_HISTORY_PATH = "/etc/enigma2/gtiptvplayer/youtube-playback-history.json"
_VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
_PLAYBACK_HISTORY_LOCK = threading.RLock()
_MAX_DURATION = 31 * 24 * 60 * 60


def youtube_resume_position(position, length):
    """Keep seconds from an unfinished video, including short YouTube clips."""
    try:
        position, length = int(position), int(length)
    except (TypeError, ValueError, OverflowError):
        return 0
    return position if 5 <= position < length - 3 and length <= _MAX_DURATION else 0


def _atomic_write(path, payload, prefix):
    parent = os.path.dirname(path) or "."
    os.makedirs(parent, mode=0o700, exist_ok=True)
    temporary = ""
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=parent,
                                         prefix=prefix, delete=False) as stream:
            temporary = stream.name
            json.dump(payload, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def normalize_query(value):
    if not isinstance(value, str):
        return ""
    value = " ".join(value.split())
    if not 2 <= len(value) <= 120 or any(unicodedata.category(c) == "Cc" for c in value):
        return ""
    return value


def _key(value):
    return unicodedata.normalize("NFKC", value).casefold()


class YouTubeHistory(object):
    def __init__(self, path=HISTORY_PATH):
        self.path = path
        self._lock = threading.RLock()

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                data = json.loads(stream.read(65537))
            values = data.get("queries", []) if isinstance(data, dict) else []
        except (OSError, ValueError):
            return []
        result, seen = [], set()
        for value in values if isinstance(values, list) else []:
            query = normalize_query(value)
            key = _key(query)
            if query and key not in seen:
                seen.add(key)
                result.append(query)
            if len(result) == HISTORY_LIMIT:
                break
        return result

    def _write(self, queries):
        _atomic_write(self.path, {"queries": queries}, ".youtube-history-")

    def list(self):
        with self._lock:
            return self._read()

    def add(self, query):
        query = normalize_query(query)
        if not query:
            raise ValueError("invalid search query")
        with self._lock:
            queries = [query] + [value for value in self._read() if _key(value) != _key(query)]
            self._write(queries[:HISTORY_LIMIT])

    def delete(self, query=None):
        with self._lock:
            values = self._read()
            if query is not None:
                query = normalize_query(query)
                if not query:
                    raise ValueError("invalid search query")
                values = [value for value in values if _key(value) != _key(query)]
            else:
                values = []
            self._write(values)
            return values


class YouTubePlaybackHistory(object):
    """Recent videos and saved seconds; never persist signed media URLs."""

    def __init__(self, path=PLAYBACK_HISTORY_PATH):
        self.path = path
        self._lock = _PLAYBACK_HISTORY_LOCK

    @staticmethod
    def _entry(value):
        if not isinstance(value, dict):
            return None
        video_id = value.get("id")
        if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id):
            return None
        title = value.get("title")
        title = " ".join(title.split())[:160] if isinstance(title, str) else ""
        title = "".join(char for char in title if unicodedata.category(char) != "Cc") or "YouTube"
        try:
            height = int(str(value.get("quality", "")))
        except (TypeError, ValueError, OverflowError):
            height = 0
        quality = str(height) if 0 < height <= 2160 else ""
        try:
            duration = int(value.get("duration") or 0)
        except (TypeError, ValueError, OverflowError):
            duration = 0
        entry = {"id": video_id, "title": title,
                 "quality": quality,
                 "duration": duration if 0 < duration <= _MAX_DURATION else 0}
        position = youtube_resume_position(value.get("position", 0), entry["duration"])
        if position:
            entry["position"] = position
        return entry

    def _read(self):
        try:
            with open(self.path, "r", encoding="utf-8") as stream:
                data = json.loads(stream.read(131073))
            values = data.get("videos", []) if isinstance(data, dict) else []
        except (OSError, ValueError):
            return []
        result, seen = [], set()
        for value in values if isinstance(values, list) else []:
            entry = self._entry(value)
            if entry is not None and entry["id"] not in seen:
                seen.add(entry["id"])
                result.append(entry)
            if len(result) == HISTORY_LIMIT:
                break
        return result

    def list(self):
        with self._lock:
            return self._read()

    def get(self, video_id):
        with self._lock:
            return next((dict(value) for value in self._read()
                         if value["id"] == video_id and value.get("position")), None)

    def add(self, video_id, title, quality, duration=0):
        entry = self._entry({"id": video_id, "title": title, "quality": quality, "duration": duration})
        if entry is None:
            raise ValueError("invalid video selection")
        with self._lock:
            values = self._read()
            previous = next((value for value in values if value["id"] == video_id), {})
            position = youtube_resume_position(previous.get("position", 0), entry["duration"])
            if position:
                entry["position"] = position
            values = [entry] + [value for value in values if value["id"] != video_id]
            _atomic_write(self.path, {"videos": values[:HISTORY_LIMIT]}, ".youtube-playback-history-")

    def save(self, video_id, position, length, **unused_metadata):
        """Update an existing card without recreating a deleted history entry."""
        try:
            position, length = int(position), int(length)
        except (TypeError, ValueError, OverflowError):
            return False
        if position < 0 or not 0 < length <= _MAX_DURATION:
            return False
        with self._lock:
            values = self._read()
            entry = next((value for value in values if value["id"] == video_id), None)
            if entry is None:
                return False
            previous = dict(entry)
            entry["duration"] = length
            entry.pop("position", None)
            saved_position = youtube_resume_position(position, length)
            if saved_position:
                entry["position"] = saved_position
            if entry != previous:
                _atomic_write(self.path, {"videos": values}, ".youtube-playback-history-")
            return True

    def finish(self, video_id):
        with self._lock:
            values = self._read()
            entry = next((value for value in values if value["id"] == video_id), None)
            if entry is None:
                return False
            if entry.pop("position", None) is not None:
                _atomic_write(self.path, {"videos": values}, ".youtube-playback-history-")
            return True

    def delete(self, video_id=None):
        if video_id is not None and (not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id)):
            raise ValueError("invalid video selection")
        with self._lock:
            values = [value for value in self._read() if value["id"] != video_id] if video_id is not None else []
            _atomic_write(self.path, {"videos": values}, ".youtube-playback-history-")
            return values

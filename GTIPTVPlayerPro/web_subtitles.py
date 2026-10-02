# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded subtitle search for the paired local web interface.

Provider credentials and download URLs stay on the receiver.  The browser
receives only short-lived, opaque result IDs; playback is changed on the
Enigma2 GUI thread after the active VOD item has been checked again.
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from io import BytesIO
import json
import os
import re
import secrets
import tempfile
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from . import PLUGIN_VERSION
from .subtitle_settings import (
    read_subtitle_document,
    update_subtitle_document,
)


SETTINGS_PATH = "/etc/enigma2/gtiptvplayerpro-subtitles.json"
PROVIDERS = ("subtitles_website", "subdl", "subsource", "opensubtitles")
PROVIDER_NAMES = {
    "subtitles_website": "Subtitle Search Engine",
    "subdl": "SubDL",
    "subsource": "SubSource",
    "opensubtitles": "OpenSubtitles.com",
}
LANGUAGES = {"tr": "turkish", "en": "english", "de": "german",
             "fr": "french", "es": "spanish", "ar": "arabic",
             "pt": "portuguese", "fa": "farsi_persian"}
MAX_RESPONSE = 2 * 1024 * 1024
MAX_RESULTS = 60
MAX_SYNC_MS = 30 * 60 * 1000
SRT_TIMING = re.compile(
    r"^(\d{2,}:\d{2}:\d{2}[,.]\d{3})\s*-->\s*"
    r"(\d{2,}:\d{2}:\d{2}[,.]\d{3})(.*)$"
)


class SubtitleError(Exception):
    def __init__(self, code, status=400):
        self.code = code
        self.status = status
        self.message = code.replace("_", " ")
        Exception.__init__(self, self.message)


class _SafeRedirect(HTTPRedirectHandler):
    def __init__(self, allowed):
        self.allowed = allowed

    def redirect_request(self, request, fp, code, msg, headers, newurl):
        if not _allowed_url(newurl, self.allowed):
            raise SubtitleError("provider_redirect_blocked", 502)
        return HTTPRedirectHandler.redirect_request(
            self, request, fp, code, msg, headers, newurl
        )


def _allowed_url(value, allowed):
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        return (parsed.scheme == "https" and not parsed.username and
                not parsed.password and parsed.port in (None, 443) and
                any(host == domain or host.endswith("." + domain)
                    for domain in allowed))
    except (TypeError, ValueError):
        return False


def _request(url, allowed, headers=None, payload=None, json_response=True):
    if not _allowed_url(url, allowed):
        raise SubtitleError("provider_url_blocked", 502)
    request_headers = {"User-Agent": "GTIPTVPlayerPro/{}".format(PLUGIN_VERSION),
                       "Accept": "application/json" if json_response else "*/*"}
    request_headers.update(headers or {})
    request = Request(url, data=payload, headers=request_headers)
    opener = build_opener(_SafeRedirect(allowed))
    try:
        with opener.open(request, timeout=6) as response:
            content = response.read(MAX_RESPONSE + 1)
    except HTTPError as error:
        # Never put the provider's error text, URL or API key into the UI/log.
        if error.code in (401, 403):
            raise SubtitleError("provider_key_rejected", 401)
        if error.code == 429:
            raise SubtitleError("provider_limit", 429)
        raise SubtitleError("provider_unavailable", 502)
    except (URLError, OSError, ValueError):
        raise SubtitleError("provider_unavailable", 502)
    if len(content) > MAX_RESPONSE:
        raise SubtitleError("subtitle_too_large", 502)
    if not json_response:
        return content
    try:
        data = json.loads(content.decode("utf-8"))
        if not isinstance(data, (dict, list)):
            raise ValueError("not an object")
        return data
    except (UnicodeError, ValueError):
        raise SubtitleError("provider_invalid_reply", 502)


def _text(value, limit=120):
    if not isinstance(value, (str, int, float)):
        return ""
    value = " ".join(str(value).split())[:limit]
    return "" if "://" in value else value


def _number(value):
    try:
        number = int(value)
        return number if 0 <= number <= 999 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("data", "results", "items", "subtitles"):
            value = data.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                for nested in ("results", "items", "subtitles"):
                    if isinstance(value.get(nested), list):
                        return value[nested]
    return []


def _episode_matches(item, season, episode):
    if not season or not episode:
        return True
    item_season = _number(item.get("season", item.get("season_number")))
    item_episode = _number(item.get("episode", item.get("episode_number")))
    if item_season and item_episode:
        return (item_season, item_episode) == (season, episode)
    name = _text(item.get("release_name") or item.get("releaseInfo") or
                 item.get("release") or item.get("name"), 240)
    match = re.search(r"(?i)\bS(\d{1,2})[ ._-]*E(\d{1,3})\b|\b(\d{1,2})x(\d{1,3})\b", name)
    if match:
        found = match.group(1, 2) if match.group(1) else match.group(3, 4)
        return tuple(map(int, found)) == (season, episode)
    # A complete-season ZIP without an explicit episode cannot safely be
    # chosen as an individual episode subtitle.
    return False


def _subdl(meta, key):
    params = {"api_key": key, "film_name": meta["title"],
              "type": "tv" if meta["content_type"] == "series" else "movie",
              "languages": meta["language"].upper(), "subs_per_page": 30,
              "unpack": 1, "client": "custom_integration"}
    if meta["year"]:
        params["year"] = meta["year"]
    if meta["content_type"] == "series":
        params["season_number"] = meta["season"]
        params["episode_number"] = meta["episode"]
    data = _request("https://api.subdl.com/api/v1/subtitles?" + urlencode(params),
                    ("api.subdl.com",))
    if data.get("status") is False:
        raise SubtitleError("provider_unavailable", 502)
    found = []
    for item in _items(data.get("subtitles", []))[:50]:
        if not isinstance(item, dict):
            continue
        files = item.get("unpack_files") or []
        for candidate in (files if files else [item]):
            if not isinstance(candidate, dict) or not _episode_matches(
                    candidate, meta["season"], meta["episode"]):
                continue
            path = candidate.get("url") or item.get("url") or ""
            if not isinstance(path, str) or not path.startswith("/subtitle/") or ".." in path:
                continue
            found.append({"title": meta["title"],
                          "release": _text(candidate.get("release_name") or candidate.get("name") or
                                           item.get("release_name")) or "Subtitle",
                          "language": meta["language"],
                          "hearing_impaired": bool(
                              candidate.get("hi", candidate.get("hearing_impaired",
                                  item.get("hi", item.get("hearing_impaired", False))))
                          ),
                          "url": "https://dl.subdl.com" + path})
    return found


def _subsource(meta, key):
    # Import lazily: the native adapter uses this module's bounded HTTP/ZIP
    # helpers. Both interfaces share the API contract and the saved key.
    from .online_subtitles import SubSourceClient
    found = SubSourceClient(key).search(meta, (meta["language"],))
    for result in found:
        result["url"] = SubSourceClient.API_BASE + "/subtitles/{}/download".format(result["subtitle_id"])
        result["headers"] = {"X-API-Key": key}
    return found


def _opensubtitles(meta, key):
    params = {"query": meta["title"], "languages": meta["language"], "order_by": "download_count"}
    if meta["content_type"] == "series":
        params["type"] = "episode"
        params["season_number"] = meta["season"]
        params["episode_number"] = meta["episode"]
    else:
        params["type"] = "movie"
        if meta["year"]:
            params["year"] = meta["year"]
    headers = {"Api-Key": key}
    data = _request("https://api.opensubtitles.com/api/v1/subtitles?" + urlencode(params),
                    ("api.opensubtitles.com",), headers)
    found = []
    for item in _items(data)[:30]:
        if not isinstance(item, dict):
            continue
        attr = item.get("attributes") or {}
        details = attr.get("feature_details") or {}
        if meta["season"] and meta["episode"] and not _episode_matches(
                {"season": details.get("season_number"),
                 "episode": details.get("episode_number")}, meta["season"], meta["episode"]):
            continue
        files = attr.get("files") or []
        if not files or not str(files[0].get("file_id", "")).isdigit():
            continue
        found.append({"title": _text(details.get("movie_name") or details.get("title")) or meta["title"],
                      "release": _text(attr.get("release") or files[0].get("file_name")) or "Subtitle",
                      "language": _text(attr.get("language"), 10) or meta["language"],
                      "file_id": int(files[0]["file_id"]), "headers": headers})
    return found


def _subtitles_website(meta, unused_key):
    # Guest access has a small daily quota. One request is charged per search.
    params = {"title": meta["title"], "language": meta["language"]}
    data = _request("https://subtitles.website/strapi/api/search/subtitles?" + urlencode(params),
                    ("subtitles.website",))
    found = []
    for item in _items(data)[:30]:
        if not isinstance(item, dict) or not _episode_matches(item, meta["season"], meta["episode"]):
            continue
        sid = item.get("id")
        if not isinstance(sid, str) or not re.fullmatch(r"[a-zA-Z0-9_-]{1,80}", sid):
            continue
        found.append({"title": _text(item.get("film_title")) or meta["title"],
                      "release": _text(item.get("release")) or "Subtitle",
                      "language": _text((item.get("language") or {}).get("code"), 10) or meta["language"],
                      "sid": sid})
    return found


SEARCHERS = {"subdl": _subdl, "subsource": _subsource,
             "opensubtitles": _opensubtitles,
             "subtitles_website": _subtitles_website}


def _download(provider, result, extensions=(".srt",)):
    if provider == "opensubtitles_file":
        # The independent OpenSubtitles client has already exchanged its
        # authenticated file_id for this short-lived link.  API credentials
        # are deliberately not forwarded to the download host.
        url = result.get("url", "")
        allowed = ("opensubtitles.com",)
        headers = {}
    elif provider == "opensubtitles":
        data = _request("https://api.opensubtitles.com/api/v1/download",
                        ("api.opensubtitles.com",),
                        dict(result["headers"], **{"Content-Type": "application/json"}),
                        json.dumps({"file_id": result["file_id"]}).encode("utf-8"))
        url = data.get("link", "")
        allowed = ("opensubtitles.com",)
        headers = {}
    elif provider == "subtitles_website":
        data = _request("https://subtitles.website/strapi/api/search/download/" + result["sid"],
                        ("subtitles.website",))
        path = data.get("download_url", "")
        if not isinstance(path, str) or not path.startswith("/uploads/") or ".." in path:
            raise SubtitleError("provider_invalid_reply", 502)
        url = "https://subtitles.website/strapi" + path
        allowed = ("subtitles.website",)
        headers = {}
    else:
        url = result["url"]
        allowed = ("subdl.com",) if provider == "subdl" else ("api.subsource.net",)
        headers = result.get("headers") or {}
    content = _request(url, allowed, headers, json_response=False)
    if content.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                entries = archive.infolist()
                if len(entries) > 5000:
                    raise SubtitleError("subtitle_format_unsupported", 422)
                extensions = tuple(
                    str(extension).lower() for extension in extensions
                    if str(extension).startswith(".")
                ) or (".srt",)
                candidates = [
                    entry for entry in entries
                    if entry.filename.lower().endswith(extensions)
                    and not entry.is_dir()
                    and entry.file_size <= MAX_RESPONSE
                ]
                if not candidates:
                    raise SubtitleError("subtitle_format_unsupported", 422)
                if provider == "subsource" and result.get("season") and result.get("episode"):
                    season, episode = _number(result["season"]), _number(result["episode"])
                    matching = [entry for entry in candidates if _episode_matches(
                        {"name": os.path.basename(entry.filename)}, season, episode
                    )]
                    if matching:
                        candidates = matching
                    elif result.get("is_pack") or len(candidates) > 1 or any(
                        re.search(r"(?i)\bS\d{1,2}[ ._-]*E\d|\b\d{1,2}x\d", entry.filename)
                        for entry in candidates
                    ):
                        raise SubtitleError("subtitle_not_found", 404)
                # Never unpack arbitrary paths; only read one bounded text
                # subtitle, preferring SRT when an archive contains both.
                candidates.sort(key=lambda entry: (
                    not entry.filename.lower().endswith(".srt"),
                    entry.filename.lower(),
                ))
                with archive.open(candidates[0], "r") as subtitle_file:
                    content = subtitle_file.read(MAX_RESPONSE + 1)
                if len(content) > MAX_RESPONSE:
                    raise SubtitleError("subtitle_too_large", 422)
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
            raise SubtitleError("subtitle_format_unsupported", 422)
    elif provider == "subsource" and result.get("is_pack"):
        raise SubtitleError("subtitle_not_found", 404)
    try:
        decoded = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        decoded = content.decode("cp1254", "replace")
    if "-->" not in decoded[:16384] or len(decoded) > MAX_RESPONSE:
        raise SubtitleError("subtitle_format_unsupported", 422)
    return decoded.encode("utf-8")


def _time_ms(value):
    """Parse an SRT cue time or a user-entered MM:SS / HH:MM:SS time."""
    if not isinstance(value, str):
        raise SubtitleError("subtitle_sync_invalid_time", 400)
    match = re.fullmatch(r"(\d{1,3}:)?\d{1,3}:\d{2}(?:[,.]\d{3})?", value.strip())
    if not match:
        raise SubtitleError("subtitle_sync_invalid_time", 400)
    parts = value.strip().replace(",", ".").split(":")
    seconds = parts[-1].split(".")
    if int(seconds[0]) >= 60 or int(parts[-2]) >= 60 and len(parts) == 3:
        raise SubtitleError("subtitle_sync_invalid_time", 400)
    hours = int(parts[0]) if len(parts) == 3 else 0
    minutes = int(parts[-2])
    total = ((hours * 60 + minutes) * 60 + int(seconds[0])) * 1000
    total += int(seconds[1]) if len(seconds) == 2 else 0
    if total > 12 * 60 * 60 * 1000:
        raise SubtitleError("subtitle_sync_invalid_time", 400)
    return total


def _format_ms(value):
    value = max(0, int(value))
    hours, remainder = divmod(value, 3600000)
    minutes, remainder = divmod(remainder, 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return "{:02d}:{:02d}:{:02d},{:03d}".format(hours, minutes, seconds, milliseconds)


def _shift_srt(content, offset_ms):
    """Shift cue timing from the original file; never compound past changes."""
    if not isinstance(content, bytes) or len(content) > MAX_RESPONSE:
        raise SubtitleError("subtitle_format_unsupported", 422)
    try:
        lines = content.decode("utf-8-sig").splitlines(keepends=True)
    except UnicodeError:
        raise SubtitleError("subtitle_format_unsupported", 422)
    changed = 0
    for index, line in enumerate(lines):
        match = SRT_TIMING.match(line.rstrip("\r\n"))
        if not match:
            continue
        start = max(0, _time_ms(match.group(1)) + offset_ms)
        end = max(start + 1, _time_ms(match.group(2)) + offset_ms)
        newline = "\r\n" if line.endswith("\r\n") else "\n" if line.endswith("\n") else ""
        lines[index] = "{} --> {}{}{}".format(
            _format_ms(start), _format_ms(end), match.group(3), newline
        )
        changed += 1
    if not changed:
        raise SubtitleError("subtitle_format_unsupported", 422)
    shifted = "".join(lines).encode("utf-8")
    if len(shifted) > MAX_RESPONSE:
        raise SubtitleError("subtitle_too_large", 422)
    return shifted


def _cue_matches(content, query, position_ms, offset_ms):
    """Return a few cues without putting the whole subtitle file on the LAN."""
    blocks = re.split(r"\r?\n\s*\r?\n", content.decode("utf-8-sig"))
    cues = []
    for block in blocks:
        lines = block.splitlines()
        for index, line in enumerate(lines[:2]):
            match = SRT_TIMING.match(line)
            if not match:
                continue
            title = " ".join(re.sub(r"<[^>]{0,100}>", "", item) for item in lines[index + 1:])[:160].strip()
            if title:
                cues.append({"time": match.group(1).replace(",", "."), "text": title,
                             "ms": _time_ms(match.group(1))})
            break
    if query:
        cues = [cue for cue in cues if query.casefold() in cue["text"].casefold()][:15]
    elif position_ms is not None:
        target = max(0, position_ms - offset_ms)
        cues = sorted(cues, key=lambda cue: abs(cue["ms"] - target))[:10]
        cues.sort(key=lambda cue: cue["ms"])
    else:
        cues = cues[:10]
    return [{"time": cue["time"], "text": cue["text"]} for cue in cues]


class WebSubtitles(object):
    def __init__(self, settings_path=SETTINGS_PATH):
        self.settings_path = settings_path
        self.lock = threading.RLock()
        self.search_lock = threading.Lock()
        self.results = {}
        self.last_search = None
        self.sync_lock = threading.Lock()
        self.sync_state = None

    def _settings(self):
        data = read_subtitle_document(self.settings_path)
        return {name: {"key": entry.get("key") if isinstance(entry.get("key"), str)
                       and len(entry["key"]) <= 300 else "",
                       "enabled": entry.get("enabled") is not False}
                for name, entry in data.items()
                if name in PROVIDERS and isinstance(entry, dict)}

    def providers(self):
        with self.lock:
            saved = self._settings()
        return [{"id": name, "name": PROVIDER_NAMES[name],
                 "configured": bool(saved.get(name, {}).get("key")) or name == "subtitles_website",
                 "enabled": saved.get(name, {}).get("enabled", True) is not False,
                 "needs_key": name != "subtitles_website"} for name in PROVIDERS]

    def update_provider(self, name, payload):
        if name not in PROVIDERS or not isinstance(payload, dict):
            raise SubtitleError("invalid_provider", 400)
        key = payload.get("key", "")
        if not isinstance(key, str) or len(key) > 300 or any(ord(char) < 32 for char in key):
            raise SubtitleError("invalid_provider_key", 400)
        with self.lock:
            if "enabled" in payload and not isinstance(payload["enabled"], bool):
                raise SubtitleError("invalid_provider", 400)

            def update(data):
                current = data.get(name) or {}
                current = dict(current) if isinstance(current, dict) else {}
                if payload.get("clear") is True:
                    current.pop("key", None)
                elif key.strip() and name != "subtitles_website":
                    current["key"] = key.strip()
                if "enabled" in payload:
                    current["enabled"] = payload["enabled"]
                data[name] = current
                return data

            if not update_subtitle_document(update, self.settings_path):
                raise SubtitleError("subtitle_settings_write_failed", 500)
            self.results.clear()
            self.last_search = None
        return self.providers()

    def test_provider(self, name):
        if name not in PROVIDERS or name == "subtitles_website":
            raise SubtitleError("invalid_provider", 400)
        with self.lock:
            key = (self._settings().get(name) or {}).get("key", "")
        if not key:
            raise SubtitleError("provider_key_missing", 409)
        if name == "subdl":
            reply = _request("https://api.subdl.com/api/v1/me?" + urlencode({"api_key": key}),
                             ("api.subdl.com",))
            if isinstance(reply, dict) and reply.get("status") is False:
                raise SubtitleError("provider_key_rejected", 401)
        elif name == "subsource":
            from .online_subtitles import SubSourceClient
            SubSourceClient(key).test()
        else:
            _request("https://api.opensubtitles.com/api/v1/subtitles?query=Matrix&languages=en",
                     ("api.opensubtitles.com",), {"Api-Key": key})
        return {"connected": True}

    def _active(self):
        from .web_remote import WEB_REMOTE

        def snapshot():
            session = WEB_REMOTE.session
            player = getattr(session, "current_dialog", None)
            item = getattr(player, "current_item", None)
            controller = getattr(player, "_subtitle_controller", None)
            if (player is None or item is None or controller is None or
                    getattr(player, "_closed", False) or
                    getattr(item, "content_type", "") not in ("movie", "series")):
                raise SubtitleError("vod_not_playing", 409)
            metadata = controller._search_metadata()
            state = self.sync_state
            bridge = controller.bridge
            synced = bool(state and tuple(state["identity"]) == (id(player), id(item))
                          and bridge.is_loaded and bridge.loaded_path == state["current"])
            position_ms = None
            try:
                service = session.nav.getCurrentService()
                result = service.seek().getPlayPosition()
                if isinstance(result, (tuple, list)) and int(result[0]) == 0:
                    position_ms = max(0, int(result[1]) // 90)
            except (AttributeError, TypeError, ValueError, OverflowError):
                pass
            return {"identity": [id(player), id(item)],
                    "title": _text(metadata.get("title"), 120),
                    "year": _number(metadata.get("year")),
                    "season": _number(metadata.get("season")),
                    "episode": _number(metadata.get("episode")),
                    "content_type": metadata.get("content_type") or "movie",
                    "can_load": bool(bridge.available()),
                    "position_ms": position_ms,
                    "sync": {"available": synced,
                             "offset_ms": state["offset_ms"] if synced else 0}}
        return WEB_REMOTE._run_on_gui(snapshot)

    def current(self):
        try:
            result = self._active()
            result.pop("identity", None)
            return result
        except SubtitleError as error:
            if error.code == "vod_not_playing":
                return {"playing": False}
            raise

    def cues(self, query=""):
        if not isinstance(query, str) or len(query) > 80:
            raise SubtitleError("subtitle_sync_invalid_time", 400)
        active = self._active()
        if not active["sync"]["available"]:
            raise SubtitleError("subtitle_sync_unavailable", 409)
        state = self.sync_state
        if not state or tuple(active["identity"]) != state["identity"]:
            raise SubtitleError("vod_changed", 409)
        with open(state["source"], "rb") as handle:
            content = handle.read(MAX_RESPONSE + 1)
        if len(content) > MAX_RESPONSE:
            raise SubtitleError("subtitle_too_large", 422)
        return {"items": _cue_matches(content, query.strip(), active["position_ms"],
                                      state["offset_ms"])}

    def sync(self, payload):
        if not isinstance(payload, dict):
            raise SubtitleError("subtitle_sync_invalid_time", 400)
        if not self.sync_lock.acquire(False):
            raise SubtitleError("subtitle_sync_busy", 409)
        try:
            active = self._active()
            if not active["sync"]["available"]:
                raise SubtitleError("subtitle_sync_unavailable", 409)
            state = self.sync_state
            if not state or tuple(active["identity"]) != state["identity"]:
                raise SubtitleError("vod_changed", 409)
            if "film_time" in payload and "subtitle_time" in payload:
                offset = _time_ms(payload["film_time"]) - _time_ms(payload["subtitle_time"])
            elif set(payload) == {"delta_ms"} and type(payload["delta_ms"]) is int:
                if abs(payload["delta_ms"]) > 5000:
                    raise SubtitleError("subtitle_sync_invalid_time", 400)
                offset = state["offset_ms"] + payload["delta_ms"]
            elif payload == {"reset": True}:
                offset = 0
            else:
                raise SubtitleError("subtitle_sync_invalid_time", 400)
            if abs(offset) > MAX_SYNC_MS:
                raise SubtitleError("subtitle_sync_out_of_range", 400)
            previous_path = state["current"]
            path = state["source"]
            if offset != 0:
                with open(state["source"], "rb") as handle:
                    original = handle.read(MAX_RESPONSE + 1)
                shifted = _shift_srt(original, offset)
                fd, path = tempfile.mkstemp(prefix="synced-", suffix=".srt",
                                            dir=os.path.dirname(state["source"]))
                try:
                    with os.fdopen(fd, "wb") as handle:
                        handle.write(shifted)
                    os.chmod(path, 0o600)
                except Exception:
                    os.unlink(path)
                    raise
            try:
                from .web_remote import WEB_REMOTE

                def activate():
                    session = WEB_REMOTE.session
                    player = getattr(session, "current_dialog", None)
                    item = getattr(player, "current_item", None)
                    controller = getattr(player, "_subtitle_controller", None)
                    if ((id(player), id(item)) != state["identity"] or
                            getattr(player, "_closed", True) or
                            controller is None or
                            controller.bridge.loaded_path != previous_path or
                            self.sync_state is not state):
                        raise SubtitleError("vod_changed", 409)
                    if path != previous_path and not controller.bridge.load(
                            path, paused=controller._paused(), notify=False):
                        raise SubtitleError("subtitle_load_failed", 409)
                    self.sync_state = dict(state, current=path, offset_ms=offset)
                    controller._changed(("external", path))
                    return {"offset_ms": offset}

                result = WEB_REMOTE._run_on_gui(activate)
            except Exception as error:
                # A timed-out GUI call may still complete; keep the file in
                # that case so the decoder never points at a deleted path.
                if path != state["source"] and getattr(error, "code", "") != "receiver_unavailable":
                    os.unlink(path)
                raise
            if previous_path != state["source"] and previous_path != path:
                try:
                    os.unlink(previous_path)
                except OSError:
                    pass
            return result
        finally:
            self.sync_lock.release()

    def search(self, payload):
        if not isinstance(payload, dict):
            raise SubtitleError("invalid_search", 400)
        language = str(payload.get("language") or "tr").lower()
        if not re.fullmatch(r"[a-z]{2,3}", language):
            raise SubtitleError("invalid_language", 400)
        if not self.search_lock.acquire(False):
            raise SubtitleError("subtitle_search_busy", 409)
        try:
            active = self._active()
            meta = dict(active, language=language)
            meta["title"] = _text(payload.get("title") or active["title"], 120)
            if len(meta["title"]) < 2:
                raise SubtitleError("invalid_search", 400)
            with self.lock:
                saved = self._settings()
                selected = [(name, (saved.get(name) or {}).get("key", ""))
                            for name in PROVIDERS if (saved.get(name) or {}).get("enabled", True)
                            and (name == "subtitles_website" or (saved.get(name) or {}).get("key"))]
                cached = self.last_search
                cache_key = (tuple(active["identity"]), language, meta["title"],
                             tuple((name, key) for name, key in selected))
                if cached and cached[0] == cache_key and time.time() - cached[1] < 45:
                    return cached[2]
            statuses, output, new_results = [], [], {}
            provider_entries = {}
            if selected:
                with ThreadPoolExecutor(max_workers=min(4, len(selected))) as executor:
                    jobs = {executor.submit(SEARCHERS[name], meta, key): name
                            for name, key in selected}
                    for future in as_completed(jobs):
                        name = jobs[future]
                        try:
                            entries = future.result()
                            statuses.append({"provider": name, "status": "ok", "count": len(entries)})
                            provider_entries[name] = entries
                        except SubtitleError as error:
                            statuses.append({"provider": name, "status": error.code, "count": 0})
                            continue
                        except Exception:
                            statuses.append({"provider": name, "status": "provider_unavailable", "count": 0})
                            continue
                    for name in PROVIDERS:
                        for result in provider_entries.get(name, [])[:15]:
                            if len(output) >= MAX_RESULTS:
                                break
                            token = secrets.token_urlsafe(20)
                            new_results[token] = (name, result, tuple(active["identity"]), time.time())
                            output.append({"id": token, "provider": name,
                                           "title": result["title"], "release": result["release"],
                                           "language": result["language"]})
            statuses.sort(key=lambda item: PROVIDERS.index(item["provider"]))
            response = {"items": output, "providers": statuses, "can_load": active["can_load"]}
            with self.lock:
                self.results = new_results
                self.last_search = (cache_key, time.time(), response)
            return response
        finally:
            self.search_lock.release()

    def apply(self, result_id):
        if not isinstance(result_id, str):
            raise SubtitleError("subtitle_not_found", 404)
        with self.lock:
            stored = self.results.get(result_id)
        if not stored or time.time() - stored[3] > 600:
            raise SubtitleError("subtitle_not_found", 404)
        provider, result, identity, unused_time = stored
        active = self._active()
        if tuple(active["identity"]) != identity:
            raise SubtitleError("vod_changed", 409)
        if not active["can_load"]:
            raise SubtitleError("subssupport_required", 409)
        content = _download(provider, result)
        # Network and ZIP work have finished; the GUI thread only loads a
        # bounded local file, never calls a provider or touches a ZIP archive.
        folder = tempfile.mkdtemp(prefix="gt-sub-", dir="/tmp")
        path = os.path.join(folder, "downloaded.srt")
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            from .web_remote import WEB_REMOTE

            def load():
                session = WEB_REMOTE.session
                player = getattr(session, "current_dialog", None)
                item = getattr(player, "current_item", None)
                if (id(player), id(item)) != identity or getattr(player, "_closed", True):
                    raise SubtitleError("vod_changed", 409)
                controller = player._subtitle_controller
                previous = controller.embedded.selected_track

                def before_activate():
                    return controller.embedded.disable() if previous is not None else True

                loaded = controller.bridge.load(path, paused=controller._paused(),
                                                before_activate=before_activate)
                if not loaded:
                    if previous is not None:
                        controller.embedded.restore(previous)
                    raise SubtitleError("subtitle_load_failed", 409)
                controller._changed(("external", path))
                self.sync_state = {"identity": identity, "source": path,
                                   "current": path, "offset_ms": 0}
                return {"loaded": True, "provider": provider}
            return WEB_REMOTE._run_on_gui(load)
        except Exception as error:
            # A GUI timeout can occur after loading has already begun. Keep
            # the small file in that case so the renderer never loses it.
            if getattr(error, "code", "") != "receiver_unavailable":
                if os.path.exists(path):
                    os.unlink(path)
                os.rmdir(folder)
            raise


WEB_SUBTITLES = WebSubtitles()

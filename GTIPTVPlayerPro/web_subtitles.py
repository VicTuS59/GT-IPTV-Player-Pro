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
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import zipfile

from . import PLUGIN_VERSION
from .subtitle_settings import (
    PROVIDERS,
    load_subtitle_settings,
    read_subtitle_document,
    update_subtitle_document,
    clear_subtitle_credentials,
)
from .subtitle_language import (
    filename_languages, normalize_subtitle_language,
    subtitle_episode_matches, subtitle_episode_numbers,
)
from .subtitle_search_cache import subtitle_search_context
from .subtitle_decode import decode_subtitle_content
from .subtitle_jobs import (
    SubtitleJobCancelled, check_subtitle_job, subtitle_request_scope,
)


SETTINGS_PATH = "/etc/enigma2/gtiptvplayerpro-subtitles.json"
PROVIDER_NAMES = {
    "subdl": "SubDL",
    "subsource": "SubSource",
    "opensubtitles": "OpenSubtitles.com",
}
POLISH_PROVIDERS = ("napiprojekt", "napisy24")
SEARCH_PROVIDERS = PROVIDERS + POLISH_PROVIDERS
PROVIDER_NAMES.update({"napiprojekt": "NapiProjekt", "napisy24": "Napisy24"})
MAX_RESPONSE = 2 * 1024 * 1024
MAX_RESULTS = 60
MAX_SYNC_MS = 30 * 60 * 1000


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
        check_subtitle_job()
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


def _request(url, allowed, headers=None, payload=None, json_response=True, timeout=6):
    check_subtitle_job()
    if not _allowed_url(url, allowed):
        raise SubtitleError("provider_url_blocked", 502)
    request_headers = {"User-Agent": "GTIPTVPlayerPro/{}".format(PLUGIN_VERSION),
                       "Accept": "application/json" if json_response else "*/*"}
    request_headers.update(headers or {})
    request = Request(url, data=payload, headers=request_headers)
    opener = build_opener(_SafeRedirect(allowed))
    try:
        with opener.open(request, timeout=timeout) as response:
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


def _episode_matches(item, season, episode):
    return subtitle_episode_matches(item, season, episode)


def _result_compatibility_sort(result):
    """Use the shared private matching priority, without changing the API."""
    from .online_subtitles import subtitle_result_sort_key
    return subtitle_result_sort_key(result)


def _result_release_label(result):
    from .online_subtitles import subtitle_result_release
    return _text(subtitle_result_release(result)) or "Subtitle"


def _search_independent(provider, metadata, settings):
    # The native and web pages use the same adapters and private credentials.
    # Import lazily because those adapters reuse this module's HTTP helpers.
    from .online_subtitles import annotate_subtitle_results, subtitle_client

    selected = settings.copy()
    selected.provider = provider
    if provider in POLISH_PROVIDERS:
        # A web search is explicit; it need not match the receiver default.
        selected.primary_language = metadata["language"]
    results = subtitle_client(selected).search(metadata, (metadata["language"],))
    results = [result for result in results if isinstance(result, dict)
               and result.get("provider") == provider
               and normalize_subtitle_language(result.get("language")) == metadata["language"]
               and (settings.hearing_impaired or not result.get("hearing_impaired"))]
    annotated = annotate_subtitle_results(metadata, results)
    annotated.sort(key=_result_compatibility_sort)
    return annotated


def _download(provider, result, extensions=(".srt",)):
    if provider == "opensubtitles_file":
        # The independent OpenSubtitles client has already exchanged its
        # authenticated file_id for this short-lived link.  API credentials
        # are deliberately not forwarded to the download host.
        url = result.get("url", "")
        allowed = ("opensubtitles.com",)
        headers = {}
    elif provider in ("subdl", "subsource"):
        url = result["url"]
        allowed = ("subdl.com",) if provider == "subdl" else ("api.subsource.net",)
        headers = result.get("headers") or {}
    else:
        raise SubtitleError("invalid_provider", 400)
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
                if result.get("season") and result.get("episode"):
                    season, episode = _number(result["season"]), _number(result["episode"])
                    matching = [entry for entry in candidates if _episode_matches(
                        {"name": os.path.basename(entry.filename)}, season, episode
                    )]
                    if matching:
                        candidates = matching
                    elif result.get("is_pack") or len(candidates) > 1 or any(
                        subtitle_episode_numbers(entry.filename) for entry in candidates
                    ):
                        raise SubtitleError("subtitle_not_found", 404)
                language = normalize_subtitle_language(result.get("language"))
                if not language:
                    raise SubtitleError("subtitle_no_results", 404)
                labelled = [(entry, filename_languages(entry.filename))
                            for entry in candidates]
                matching = [entry for entry, codes in labelled if codes == {language}]
                if matching:
                    candidates = matching
                else:
                    # An explicitly different language is never a fallback.
                    candidates = [entry for entry, codes in labelled if not codes]
                    stems = {os.path.splitext(entry.filename.lower())[0]
                             for entry in candidates}
                    if not candidates or len(stems) != 1:
                        raise SubtitleError("subtitle_no_results", 404)
                # Several releases for the same episode/language are still
                # ambiguous. SRT/VTT variants of one logical file are safe.
                stems = {os.path.splitext(entry.filename.lower())[0]
                         for entry in candidates}
                if len(stems) != 1:
                    raise SubtitleError("subtitle_no_results", 404)
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
    elif result.get("is_pack"):
        raise SubtitleError("subtitle_not_found", 404)
    decoded = decode_subtitle_content(content, result.get("language", ""))
    if len(decoded) > MAX_RESPONSE:
        raise SubtitleError("subtitle_format_unsupported", 422)
    # Inspect actual visible cues. VTT NOTE/STYLE blocks and comments cannot
    # satisfy the language check for unrelated subtitle dialogue.
    from .online_subtitles import parse_subtitle
    if not parse_subtitle(decoded, language=result.get("language", "")):
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


class WebSubtitles(object):
    def __init__(self, settings_path=SETTINGS_PATH):
        self.settings_path = settings_path
        self.lock = threading.RLock()
        self.search_lock = threading.Lock()
        self.results = {}
        self.last_search = None
        self.results_context = None
        self.apply_serial = 0
        self.sync_lock = threading.Lock()
        self.sync_state = None

    def _settings(self):
        document = read_subtitle_document(self.settings_path)
        settings = load_subtitle_settings(self.settings_path)
        return {name: {"key": getattr(settings, name + "_api_key", ""),
                       "enabled": (document.get(name) or {}).get("enabled") is not False
                       if isinstance(document.get(name), dict) else True}
                for name in PROVIDERS}

    @staticmethod
    def _clear_provider_key(document, name):
        # The native loader accepts older layouts too. An explicit key clear
        # must not silently fall back to an older copy of the same credential.
        clear_subtitle_credentials(document, name)

    def providers(self):
        with self.lock:
            saved = self._settings()
        return [{"id": name, "name": PROVIDER_NAMES[name],
                 "configured": bool(saved[name]["key"]),
                 "enabled": saved[name]["enabled"],
                 "needs_key": True} for name in PROVIDERS]

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
                if payload.get("clear") is True:
                    self._clear_provider_key(data, name)
                current = data.get(name) or {}
                current = dict(current) if isinstance(current, dict) else {}
                if payload.get("clear") is True:
                    current.pop("key", None)
                elif key.strip():
                    current["key"] = key.strip()
                if "enabled" in payload:
                    current["enabled"] = payload["enabled"]
                data[name] = current
                return data

            if not update_subtitle_document(update, self.settings_path):
                raise SubtitleError("subtitle_settings_write_failed", 500)
            self.results.clear()
            self.last_search = None
            self.results_context = None
            self.apply_serial += 1
        return self.providers()

    def test_provider(self, name):
        if name not in PROVIDERS:
            raise SubtitleError("invalid_provider", 400)
        with self.lock:
            key = (self._settings().get(name) or {}).get("key", "")
        if not key:
            raise SubtitleError("provider_key_missing", 409)
        from .online_subtitles import subtitle_client

        settings = load_subtitle_settings(self.settings_path)
        settings.provider = name
        subtitle_client(settings).test()
        return {"connected": True}

    def _active(self, include_metadata=False):
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
            online = getattr(controller, "online", None)
            renderer = getattr(online, "renderer", None)
            settings = load_subtitle_settings(self.settings_path)
            can_load = bool(online is not None and not getattr(online, "_closed", True)
                            and renderer is not None and not renderer.closed
                            and settings.enabled)
            synced = bool(can_load and online.is_loaded)
            with self.lock:
                state = self.sync_state
                if synced:
                    identity = (id(player), id(item))
                    revision = getattr(renderer, "content_revision", 0)
                    if (not state or state["identity"] != identity
                            or state.get("content_revision") != revision
                            or state["current"] != online.loaded_path
                            or state["cues"] is not renderer.cues):
                        state = {"identity": identity, "source": online.loaded_path,
                                 "current": online.loaded_path, "cues": renderer.cues,
                                 "content_revision": revision,
                                 "revision": secrets.token_urlsafe(20)}
                        self.sync_state = state
                else:
                    self.sync_state = None
            position_ms = None
            try:
                service = session.nav.getCurrentService()
                result = service.seek().getPlayPosition()
                if isinstance(result, (tuple, list)) and int(result[0]) == 0:
                    position_ms = max(0, int(result[1]) // 90)
            except (AttributeError, TypeError, ValueError, OverflowError):
                pass
            from .online_subtitles import normalized_metadata
            result = {"identity": [id(player), id(item)],
                    "title": _text(metadata.get("title"), 120),
                    "year": normalized_metadata(metadata, "en")["year"],
                    "season": _number(metadata.get("season")),
                    "episode": _number(metadata.get("episode")),
                    "content_type": metadata.get("content_type") or "movie",
                    "can_load": can_load,
                    "independent_enabled": settings.enabled,
                    "position_ms": position_ms,
                    "sync": {"available": synced,
                             "offset_ms": renderer.offset_ms if synced else 0,
                             "revision": state["revision"] if synced else None}}
            result["search_context"] = subtitle_search_context(
                metadata, settings,
                (id(player), id(item), getattr(online, "_browser_generation", 0)),
                self._settings(),
            )
            if include_metadata:
                result["_metadata"] = dict(metadata)
            return result
        return WEB_REMOTE._run_on_gui(snapshot)

    def current(self):
        try:
            result = self._active()
            result.pop("identity", None)
            result["cached_results"] = None
            with self.lock:
                cached = self.last_search
                if (result["independent_enabled"] and cached
                        and cached[2].get("context") == result["search_context"]):
                    now = time.time()
                    for token, stored in list(self.results.items()):
                        self.results[token] = stored[:3] + (now,)
                    result["cached_results"] = dict(cached[2], can_load=result["can_load"])
                elif cached:
                    self.last_search = None
                    self.results = {}
                    self.results_context = None
            return result
        except SubtitleError as error:
            if error.code == "vod_not_playing":
                with self.lock:
                    self.last_search = None
                    self.results = {}
                    self.results_context = None
                    self.sync_state = None
                return {"playing": False}
            raise

    def cues(self, query="", revision=None):
        if not isinstance(query, str) or len(query) > 80:
            raise SubtitleError("subtitle_sync_invalid_time", 400)
        active = self._active()
        if not active["sync"]["available"]:
            raise SubtitleError("subtitle_sync_unavailable", 409)
        if revision and revision != active["sync"]["revision"]:
            raise SubtitleError("subtitle_operation_cancelled", 409)
        state = self.sync_state
        if not state or tuple(active["identity"]) != state["identity"]:
            raise SubtitleError("vod_changed", 409)
        cues = state["cues"]
        if query.strip():
            wanted = query.strip().casefold()
            selected = [cue for cue in cues if wanted in cue.text.casefold()][:15]
        elif active["position_ms"] is not None:
            target = max(0, active["position_ms"] - active["sync"]["offset_ms"])
            selected = sorted(cues, key=lambda cue: abs(cue.start_ms - target))[:10]
            selected.sort(key=lambda cue: cue.start_ms)
        else:
            selected = cues[:10]
        confirmed = self._active()
        if (not confirmed["sync"]["available"]
                or confirmed["sync"]["revision"] != state["revision"]):
            raise SubtitleError("subtitle_operation_cancelled", 409)
        return {"revision": state["revision"],
                "items": [{"time": _format_ms(cue.start_ms).replace(",", "."),
                           "text": cue.text[:160]} for cue in selected]}

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
            parameters = dict(payload)
            revision = parameters.pop("revision", None)
            if "film_time" in parameters and "subtitle_time" in parameters:
                offset = _time_ms(payload["film_time"]) - _time_ms(payload["subtitle_time"])
            elif set(parameters) == {"delta_ms"} and type(payload["delta_ms"]) is int:
                if abs(payload["delta_ms"]) > 5000:
                    raise SubtitleError("subtitle_sync_invalid_time", 400)
                offset = active["sync"]["offset_ms"] + payload["delta_ms"]
            elif parameters == {"reset": True}:
                offset = 0
            else:
                raise SubtitleError("subtitle_sync_invalid_time", 400)
            if abs(offset) > MAX_SYNC_MS:
                raise SubtitleError("subtitle_sync_out_of_range", 400)
            if not revision or revision != state["revision"]:
                raise SubtitleError("subtitle_operation_cancelled", 409)
            from .web_remote import WEB_REMOTE

            def activate():
                player = getattr(WEB_REMOTE.session, "current_dialog", None)
                item = getattr(player, "current_item", None)
                controller = getattr(player, "_subtitle_controller", None)
                online = getattr(controller, "online", None)
                if ((id(player), id(item)) != state["identity"] or
                        getattr(player, "_closed", True) or online is None or
                        not online.is_loaded or online.loaded_path != state["current"] or
                        online._closed or online.renderer.closed or
                        online.renderer.cues is not state["cues"] or
                        getattr(online.renderer, "content_revision", 0) != state["content_revision"] or
                        self.sync_state is not state):
                    raise SubtitleError("vod_changed", 409)
                if not load_subtitle_settings(self.settings_path).enabled:
                    raise SubtitleError("independent_subtitles_disabled", 409)
                adjusted = online.renderer.adjust_offset(absolute_ms=offset)
                self.sync_state = dict(state, offset_ms=adjusted)
                controller._changed(("online", state["current"]))
                return {"offset_ms": adjusted, "revision": state["revision"]}

            return WEB_REMOTE._run_on_gui(activate)
        finally:
            self.sync_lock.release()

    def search(self, payload):
        if not isinstance(payload, dict):
            raise SubtitleError("invalid_search", 400)
        language = str(payload.get("language") or "tr").lower()
        if not re.fullmatch(r"[a-z]{2,3}", language):
            raise SubtitleError("invalid_language", 400)
        language = normalize_subtitle_language(language)
        if not self.search_lock.acquire(False):
            raise SubtitleError("subtitle_search_busy", 409)
        try:
            active = self._active(include_metadata=True)
            if not active["independent_enabled"]:
                raise SubtitleError("independent_subtitles_disabled", 409)
            meta = dict(active.pop("_metadata"), language=language)
            meta["title"] = _text(payload.get("title") or active["title"], 120)
            if meta["title"].casefold() != active["title"].casefold():
                meta["year"] = 0
                for field in ("tmdb_id", "imdb_id", "sd_id"):
                    meta[field] = ""
                from .online_subtitles import normalized_metadata
                meta["year"] = normalized_metadata(meta, language)["year"]
            from .online_subtitles import subtitle_search_available
            if not subtitle_search_available(meta):
                raise SubtitleError("invalid_search", 400)
            from .web_remote import WEB_REMOTE

            def supersede_pending_apply():
                player = getattr(WEB_REMOTE.session, "current_dialog", None)
                item = getattr(player, "current_item", None)
                online = getattr(getattr(player, "_subtitle_controller", None), "online", None)
                if (tuple(active["identity"]) != (id(player), id(item))
                        or online is None or online._closed):
                    raise SubtitleError("vod_changed", 409)
                with self.lock:
                    self.apply_serial += 1
                online.begin_external_operation()

            WEB_REMOTE._run_on_gui(supersede_pending_apply)
            with self.lock:
                saved = self._settings()
                settings = load_subtitle_settings(self.settings_path)
                selected = [name for name in PROVIDERS if saved[name]["enabled"]
                            and settings.credentials_configured(name)]
                if language == "pl":
                    selected.extend(POLISH_PROVIDERS)
                cached = self.last_search
                cache_key = (tuple(active["identity"]), language,
                             tuple(sorted((key, str(value)) for key, value in meta.items())),
                             tuple((name, saved[name]["key"])
                                   for name in selected if name in saved),
                             settings.opensubtitles_username, settings.opensubtitles_password,
                             settings.hearing_impaired)
                if (not payload.get("refresh") and cached and cached[0] == cache_key
                        and time.time() - cached[1] < 45):
                    return dict(cached[2], can_load=active["can_load"])
            statuses, output, new_results = [], [], {}
            provider_entries = {}
            if selected:
                with ThreadPoolExecutor(max_workers=min(5, len(selected))) as executor:
                    jobs = {executor.submit(_search_independent, name, meta, settings): name
                            for name in selected}
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
                    per_provider = min(15, MAX_RESULTS // max(1, len(selected)))
                    for name in SEARCH_PROVIDERS:
                        entries = [entry for entry in provider_entries.get(name, [])
                                   if isinstance(entry, dict) and entry.get("provider", name) == name]
                        # Keep the source quota while applying the same match
                        # priority before truncation and across all sources.
                        entries.sort(key=_result_compatibility_sort)
                        for result in entries[:per_provider]:
                            if len(output) >= MAX_RESULTS:
                                break
                            if not isinstance(result, dict) or result.get("provider", name) != name:
                                continue
                            token = secrets.token_urlsafe(20)
                            result = dict(result, _search_metadata=dict(meta))
                            new_results[token] = (name, result, tuple(active["identity"]), time.time())
                            output.append({"id": token, "provider": name,
                                           "title": _text(result.get("title")) or meta["title"],
                                           "release": _result_release_label(result),
                                           "language": _text(result.get("language"), 10) or language,
                                           "subtitle_fps": result.get("subtitle_fps") or result.get("fps") or 0,
                                           "video_fps": result.get("video_fps") or 0,
                                           "compatibility_status": result.get("compatibility_status") or "unknown",
                                           "hearing_impaired": bool(result.get("hearing_impaired"))})
            output.sort(key=lambda row: (
                _result_compatibility_sort(new_results[row["id"]][1]),
                SEARCH_PROVIDERS.index(row["provider"]),
            ))
            statuses.sort(key=lambda item: SEARCH_PROVIDERS.index(item["provider"]))
            confirmed = self._active()
            if confirmed["search_context"] != active["search_context"]:
                raise SubtitleError("vod_changed", 409)
            response = {"items": output, "providers": statuses, "can_load": active["can_load"],
                        "context": active["search_context"], "language": language,
                        "title": meta["title"], "selected_id": None}
            with self.lock:
                self.results = new_results
                self.results_context = active["search_context"]
                self.last_search = (cache_key, time.time(), response)
            return response
        finally:
            self.search_lock.release()

    def apply(self, result_id):
        if not isinstance(result_id, str):
            raise SubtitleError("subtitle_not_found", 404)
        active = self._active(include_metadata=True)
        if not active["independent_enabled"]:
            raise SubtitleError("independent_subtitles_disabled", 409)
        with self.lock:
            stored = self.results.get(result_id)
            stored_context = self.results_context
        if not stored or stored[0] not in SEARCH_PROVIDERS:
            raise SubtitleError("subtitle_not_found", 404)
        provider, result, identity, unused_time = stored
        if tuple(active["identity"]) != identity:
            raise SubtitleError("vod_changed", 409)
        if stored_context and active["search_context"] != stored_context:
            raise SubtitleError("subtitle_not_found", 404)
        if time.time() - stored[3] > 600:
            with self.lock:
                cached = self.last_search
                if (not cached or cached[2].get("context") != active["search_context"]
                        or not any(row.get("id") == result_id for row in cached[2].get("items", []))):
                    raise SubtitleError("subtitle_not_found", 404)
                self.results[result_id] = stored[:3] + (time.time(),)
        if not active["independent_enabled"]:
            raise SubtitleError("independent_subtitles_disabled", 409)
        if not active["can_load"]:
            raise SubtitleError("subtitle_load_failed", 409)
        from .web_remote import WEB_REMOTE

        with self.lock:
            self.apply_serial += 1
            serial = self.apply_serial

        def begin():
            player = getattr(WEB_REMOTE.session, "current_dialog", None)
            item = getattr(player, "current_item", None)
            controller = getattr(player, "_subtitle_controller", None)
            online = getattr(controller, "online", None)
            with self.lock:
                if serial != self.apply_serial:
                    raise SubtitleError("subtitle_operation_cancelled", 409)
            if ((id(player), id(item)) != identity or getattr(player, "_closed", True)
                    or online is None or online._closed):
                raise SubtitleError("vod_changed", 409)
            current_settings = load_subtitle_settings(self.settings_path)
            if not current_settings.enabled:
                raise SubtitleError("independent_subtitles_disabled", 409)
            current_context = subtitle_search_context(
                controller._search_metadata(), current_settings,
                (id(player), id(item), getattr(online, "_browser_generation", 0)),
                self._settings(),
            )
            if stored_context and current_context != stored_context:
                raise SubtitleError("subtitle_not_found", 404)
            # Claim the receiver operation before starting network work. Off,
            # a native selection or another web selection invalidates it.
            return online, online.begin_external_operation()

        operation_online, operation = WEB_REMOTE._run_on_gui(begin)
        from .online_subtitles import (
            parse_subtitle, retime_subtitle_cues, save_subtitle_file, subtitle_client,
        )

        settings = load_subtitle_settings(self.settings_path)
        settings.provider = provider
        if provider in POLISH_PROVIDERS:
            settings.primary_language = "pl"
        def continuing():
            with self.lock:
                newest = serial == self.apply_serial
            return newest and operation_online.operation_is_current(operation)

        try:
            with subtitle_request_scope(continuing):
                content = subtitle_client(settings).download(result)
                check_subtitle_job()
        except SubtitleJobCancelled:
            raise SubtitleError("subtitle_operation_cancelled", 409)
        original_cues = parse_subtitle(content, hearing_impaired=settings.hearing_impaired,
                                       language=result.get("language", ""))
        query_metadata = result.get("_search_metadata")
        fresh_result = WEB_REMOTE._run_on_gui(lambda: operation_online.revalidate_result(
            result, search_metadata=query_metadata,
        ))
        cues, unused_factor, timeline_state = retime_subtitle_cues(original_cues, fresh_result)
        if not cues or fresh_result.get("identity_match") is False or timeline_state == "mismatch":
            raise SubtitleError("subtitle_load_failed", 409)
        if not continuing():
            raise SubtitleError("subtitle_operation_cancelled", 409)
        path = save_subtitle_file(content, active["_metadata"], result)

        def load():
            player = getattr(WEB_REMOTE.session, "current_dialog", None)
            item = getattr(player, "current_item", None)
            controller = getattr(player, "_subtitle_controller", None)
            online = getattr(controller, "online", None)
            current_settings = load_subtitle_settings(self.settings_path)
            if ((id(player), id(item)) != identity or getattr(player, "_closed", True)
                    or online is None or online._closed):
                raise SubtitleError("vod_changed", 409)
            if not current_settings.enabled:
                raise SubtitleError("independent_subtitles_disabled", 409)
            with self.lock:
                newest = serial == self.apply_serial
            if (not newest or online is not operation_online
                    or not online.operation_is_current(operation)):
                raise SubtitleError("subtitle_operation_cancelled", 409)
            current_context = subtitle_search_context(
                controller._search_metadata(), current_settings,
                (id(player), id(item), getattr(online, "_browser_generation", 0)),
                self._settings(),
            )
            if stored_context and current_context != stored_context:
                raise SubtitleError("subtitle_not_found", 404)
            # Recalculate from the original parsed cues, never from a previous
            # conversion. Decoder FPS/duration can arrive while saving a file.
            latest_result = online.revalidate_result(result, search_metadata=query_metadata)
            ready_cues, unused_factor, timeline_state = retime_subtitle_cues(original_cues, latest_result)
            if not ready_cues or latest_result.get("identity_match") is False or timeline_state == "mismatch":
                raise SubtitleError("subtitle_load_failed", 409)
            # Explicit web selection wins over a pending receiver auto-search.
            # The existing coordinator disables other subtitle overlays and
            # supplies a rollback if the independent renderer cannot start.
            previous_online = online.snapshot()
            rollback = controller._before_online_enable()
            try:
                loaded = online.renderer.load(ready_cues, path, current_settings)
            except Exception:
                loaded = False
            if not loaded:
                try:
                    restored = previous_online is not None and online.restore(previous_online)
                except Exception:
                    restored = False
                if not restored and callable(rollback):
                    online.renderer.disable()
                    try:
                        rollback()
                    except Exception:
                        pass
                raise SubtitleError("subtitle_load_failed", 409)
            if controller._paused():
                online.pause()
            else:
                online.resume()
            with self.lock:
                self.sync_state = None
            controller._changed(("online", path))
            with self.lock:
                if self.last_search and self.last_search[2].get("context") == stored_context:
                    self.last_search[2]["selected_id"] = result_id
            return {"loaded": True, "provider": provider}

        return WEB_REMOTE._run_on_gui(load)


WEB_SUBTITLES = WebSubtitles()

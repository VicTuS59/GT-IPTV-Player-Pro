# -*- coding: utf-8 -*-
"""Keyless web search and receiver-local YouTube video playback."""
import html
import json
import re
import secrets
import threading
import time
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from .i18n import locale_for_language, metadata_language
from .settings import load_player_settings, save_player_settings
from .youtube_history import YouTubeHistory, YouTubePlaybackHistory, normalize_query, youtube_resume_position
from .youtube_options import YOUTUBE_QUALITIES

from .web_api import WebServiceError
from .web_remote import WEB_REMOTE
from .youtube_vendor.config import EXTRACT_LOCK, set_preferences
from .diagnostics import log_event
from .youtube_playback import (
    YouTubeNativePlayerUnavailable, youtube_service_type, youtube_stream_parts,
    native_audio_player_available,
)


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PAGE_SIZE = 12
CURSOR_LIFETIME = 1800
SEARCH_SECONDS = 25
SEARCH_CONTINUATIONS = 8
SEARCH_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"


def _search_locale(value):
    """Validate a browser/device locale and return YouTube language and region."""
    value = str(value or "").strip().replace("_", "-")[:36]
    match = re.fullmatch(r"([a-zA-Z]{2,3})(?:-([a-zA-Z]{4}))?(?:-([a-zA-Z]{2}))?", value)
    if not match:
        return "en", "US", "en-US"
    language = match.group(1).lower()
    script = (match.group(2) or "").lower()
    region = (match.group(3) or ("TW" if language == "zh" and script == "hant"
                                  else locale_for_language(language).rsplit("-", 1)[-1])).upper()
    if language == "zh":
        youtube_language = "zh-HK" if region in ("HK", "TW", "MO") or script == "hant" else "zh-CN"
    elif language == "pt" and region in ("PT", "BR"):
        youtube_language = "pt-" + region
    else:
        youtube_language = language
    return youtube_language, region, "{}-{}".format(language, region)


def _page_config(page):
    config = {}
    for found in list(re.finditer(r"ytcfg\.set\s*\(", page))[:40]:
        try:
            value, unused_end = json.JSONDecoder().raw_decode(page[found.end():])
        except ValueError:
            continue
        if isinstance(value, dict):
            for key in ("INNERTUBE_API_KEY", "INNERTUBE_CONTEXT", "INNERTUBE_CONTEXT_CLIENT_NAME"):
                if key in value:
                    config[key] = value[key]
    # Some layouts place these fields in page JSON rather than a ytcfg.set
    # call. Read only the named JSON values and validate them below.
    for name in ("INNERTUBE_API_KEY", "INNERTUBE_CONTEXT", "INNERTUBE_CONTEXT_CLIENT_NAME"):
        if name in config:
            continue
        found = re.search(r'"' + name + r'"\s*:\s*', page)
        if found:
            try:
                config[name], unused_end = json.JSONDecoder().raw_decode(page[found.end():])
            except ValueError:
                pass
    key = config.get("INNERTUBE_API_KEY")
    context = config.get("INNERTUBE_CONTEXT")
    if (not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{20,200}", key)
            or not isinstance(context, dict) or not isinstance(context.get("client"), dict)
            or len(json.dumps(context)) > 32768):
        return None
    return config


def _search_page(query, language):
    # Search the public YouTube page. This is a web page, not the Data API:
    # no user-supplied developer key or third-party instance is involved.
    youtube_language, region, accept_language = _search_locale(language)
    parameters = {"search_query": query, "hl": youtube_language, "gl": region}
    url = "https://www.youtube.com/results?{}".format(urlencode(parameters))
    request = Request(url, headers={
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Language": accept_language + ",en;q=0.8",
        "User-Agent": SEARCH_USER_AGENT,
    })
    try:
        with urlopen(request, timeout=9) as response:
            payload = response.read(4 * 1024 * 1024 + 1)
    except HTTPError as error:
        if error.code == 429:
            raise WebServiceError("youtube_rate_limited", "YouTube is temporarily limiting searches", 429)
        raise WebServiceError("youtube_unavailable", "YouTube search is unavailable", 502)
    except (URLError, OSError):
        raise WebServiceError("youtube_unavailable", "YouTube search is unavailable", 502)
    try:
        if len(payload) > 4 * 1024 * 1024:
            raise ValueError("large response")
        page = payload.decode("utf-8", "replace")
        for marker in (r"(?:var\s+)?ytInitialData\s*=\s*", r"window\[\"ytInitialData\"\]\s*=\s*"):
            found = re.search(marker, page)
            if found:
                data, unused_end = json.JSONDecoder().raw_decode(page[found.end():])
                if isinstance(data, dict):
                    return data, _page_config(page)
        raise ValueError("search data missing")
    except (UnicodeError, ValueError):
        raise WebServiceError("youtube_unavailable", "YouTube search is unavailable", 502)


def _search_items(data):
    """Return search-result items, excluding sidebars and tracking commands."""
    if not isinstance(data, dict):
        return []
    root = data.get("contents")
    initial = root.get("twoColumnSearchResultsRenderer") if isinstance(root, dict) else None
    if isinstance(initial, dict):
        primary = initial.get("primaryContents") or {}
        items = primary.get("sectionListRenderer", {}).get("contents") if isinstance(primary, dict) else None
        if isinstance(items, list):
            return items
    items = []
    for key in ("onResponseReceivedActions", "onResponseReceivedCommands", "onResponseReceivedEndpoints"):
        actions = data.get(key)
        for action in actions if isinstance(actions, list) else []:
            if not isinstance(action, dict):
                continue
            for name in ("appendContinuationItemsAction", "reloadContinuationItemsCommand"):
                part = action.get(name)
                if isinstance(part, dict) and isinstance(part.get("continuationItems"), list):
                    items.extend(part["continuationItems"])
    if items:
        return items
    contents = data.get("continuationContents")
    for value in (contents if isinstance(contents, dict) else {}).values():
        if isinstance(value, dict):
            for name in ("contents", "items"):
                if isinstance(value.get(name), list):
                    return value[name]
    return [data]


def _continuation(data):
    """Use only the continuation attached to the actual search result list."""
    pending = list(_search_items(data))
    visited = 0
    while pending and visited < 12000:
        node = pending.pop()
        visited += 1
        if isinstance(node, list):
            pending.extend(node)
        elif isinstance(node, dict):
            renderer = node.get("continuationItemRenderer")
            if isinstance(renderer, dict):
                endpoint = renderer.get("continuationEndpoint") or {}
                command = endpoint.get("continuationCommand") if isinstance(endpoint, dict) else None
                if isinstance(command, dict):
                    token = command.get("token")
                    if isinstance(token, str) and 1 <= len(token) <= 4096:
                        result = {"continuation": token}
                        tracking = endpoint.get("clickTrackingParams")
                        if isinstance(tracking, str) and len(tracking) <= 2048:
                            result["clickTracking"] = {"clickTrackingParams": tracking}
                        return result
            if "videoRenderer" not in node:
                pending.extend(node.values())
    # Older continuation responses expose nextContinuationData separately.
    contents = data.get("continuationContents") if isinstance(data, dict) else None
    for value in (contents if isinstance(contents, dict) else {}).values():
        if isinstance(value, dict):
            for item in value.get("continuations") or []:
                next_data = item.get("nextContinuationData") if isinstance(item, dict) else None
                if isinstance(next_data, dict):
                    token = next_data.get("continuation")
                    if isinstance(token, str) and 1 <= len(token) <= 4096:
                        return {"continuation": token}
    return None


def _continuation_page(config, continuation, language):
    if not config:
        raise WebServiceError("youtube_page_unavailable", "More results could not be loaded", 502)
    youtube_language, region, accept_language = _search_locale(language)
    context = dict(config["INNERTUBE_CONTEXT"])
    client = dict(context["client"])
    client.update({"hl": youtube_language, "gl": region})
    context["client"] = client
    body = {"context": context}
    body.update(continuation)
    headers = {
        "Accept": "application/json", "Content-Type": "application/json",
        "Origin": "https://www.youtube.com", "User-Agent": SEARCH_USER_AGENT,
        "Accept-Language": accept_language + ",en;q=0.8",
        "X-YouTube-Client-Name": str(config.get("INNERTUBE_CONTEXT_CLIENT_NAME") or "1"),
        "X-YouTube-Client-Version": str(client.get("clientVersion") or "")[:80],
    }
    visitor = client.get("visitorData")
    if isinstance(visitor, str) and len(visitor) <= 2048:
        headers["X-Goog-Visitor-Id"] = visitor
    request = Request("https://www.youtube.com/youtubei/v1/search?" + urlencode({"key": config["INNERTUBE_API_KEY"]}),
                      data=json.dumps(body, separators=(",", ":")).encode("utf-8"), headers=headers)
    try:
        with urlopen(request, timeout=9) as response:
            payload = response.read(3 * 1024 * 1024 + 1)
        if len(payload) > 3 * 1024 * 1024:
            raise ValueError("large response")
        data = json.loads(payload.decode("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("invalid response")
        return data
    except HTTPError as error:
        if error.code == 429:
            raise WebServiceError("youtube_rate_limited", "YouTube is temporarily limiting searches", 429)
        raise WebServiceError("youtube_page_unavailable", "More results could not be loaded", 502)
    except (URLError, OSError, UnicodeError, ValueError):
        raise WebServiceError("youtube_page_unavailable", "More results could not be loaded", 502)


def _label(renderer, name, maximum=180):
    value = renderer.get(name) or {}
    if not isinstance(value, dict):
        return ""
    runs = value.get("runs") or ()
    raw = "".join(str(run.get("text") or "") for run in runs if isinstance(run, dict)) if isinstance(runs, list) else ""
    raw = raw or str(value.get("simpleText") or "")
    return " ".join(html.unescape(raw).split())[:maximum]


def _lockup_video(model):
    """Normalize YouTube's newer video cards to the search-result fields."""
    if model.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
        return None
    metadata = model.get("metadata") or {}
    metadata = metadata.get("lockupMetadataViewModel") if isinstance(metadata, dict) else None
    if not isinstance(metadata, dict):
        return None
    title = metadata.get("title") or {}
    content = metadata.get("metadata") or {}
    content = content.get("contentMetadataViewModel") if isinstance(content, dict) else None
    channel, published = "", ""
    for row in (content.get("metadataRows") or []) if isinstance(content, dict) else []:
        parts = row.get("metadataParts") if isinstance(row, dict) else None
        for part in parts if isinstance(parts, list) else []:
            label = part.get("text") if isinstance(part, dict) else None
            if not isinstance(label, dict):
                continue
            for run in label.get("commandRuns") or []:
                tap = run.get("onTap") if isinstance(run, dict) else None
                command = tap.get("innertubeCommand") if isinstance(tap, dict) else None
                if isinstance(command, dict) and isinstance(command.get("browseEndpoint"), dict):
                    channel = str(label.get("content") or "")
                    break
        if isinstance(parts, list) and parts and isinstance(parts[-1], dict) and "accessibilityLabel" in parts[-1]:
            label = parts[-1].get("text") or {}
            published = str(label.get("content") or "") if isinstance(label, dict) else ""
    duration = ""
    image = model.get("contentImage") or {}
    thumbnail = image.get("thumbnailViewModel") if isinstance(image, dict) else None
    for overlay in (thumbnail.get("overlays") or []) if isinstance(thumbnail, dict) else []:
        if not isinstance(overlay, dict):
            continue
        for name, field in (("thumbnailBottomOverlayViewModel", "badges"),
                            ("thumbnailOverlayBadgeViewModel", "thumbnailBadges")):
            value = overlay.get(name) or {}
            for badge in (value.get(field) or []) if isinstance(value, dict) else []:
                value_badge = badge.get("thumbnailBadgeViewModel") if isinstance(badge, dict) else None
                if isinstance(value_badge, dict):
                    duration = str(value_badge.get("text") or "")
    return {"videoId": model.get("contentId"),
            "title": {"simpleText": str(title.get("content") or "") if isinstance(title, dict) else ""},
            "ownerText": {"simpleText": channel}, "publishedTimeText": {"simpleText": published},
            "lengthText": {"simpleText": duration}}


def _video_renderers(data):
    pending = [data]
    visited = 0
    while pending and visited < 12000:
        node = pending.pop()
        visited += 1
        if isinstance(node, list):
            pending.extend(reversed(node))
        elif isinstance(node, dict):
            video = node.get("videoRenderer")
            if isinstance(video, dict):
                yield video
                continue
            lockup = node.get("lockupViewModel")
            if isinstance(lockup, dict):
                video = _lockup_video(lockup)
                if video:
                    yield video
                continue
            pending.extend(reversed(list(node.values())))


def _results(data, seen):
    entries = []
    for entry in _video_renderers(_search_items(data)):
        video_id = entry.get("videoId", "")
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id) or video_id in seen:
            continue
        seen.add(video_id)
        entries.append({"id": video_id,
                        "title": _label(entry, "title"),
                        "channel": _label(entry, "ownerText", 100) or _label(entry, "longBylineText", 100),
                        "published": _label(entry, "publishedTimeText", 40),
                        "thumbnail": "https://i.ytimg.com/vi/{}/hqdefault.jpg".format(video_id),
                        "duration": _label(entry, "lengthText", 20),
                        "live": any(isinstance(badge.get("metadataBadgeRenderer"), dict)
                                    and badge["metadataBadgeRenderer"].get("style")
                                    == "BADGE_STYLE_TYPE_LIVE_NOW"
                                    for badge in (entry.get("badges") or []) if isinstance(badge, dict))})
        if len(entries) >= 120:
            break
    return entries


class WebYouTube(object):
    def __init__(self, history=None, playback_history=None):
        self._lock = threading.RLock()
        self._jobs = {}
        self._playing = None
        self._player = None
        self._latest_play_token = ""
        self._cursors = {}
        self._lists = {}
        self._search_jobs = {}
        self._history = history if history is not None else YouTubeHistory()
        self._playback_history = playback_history if playback_history is not None else YouTubePlaybackHistory()
        self._sequence = None
        self._autoplay_pending = False

    def _remember_cursor(self, state):
        if not state["buffer"] and not state["continuation"]:
            return ""
        token = secrets.token_urlsafe(24)
        with self._lock:
            now = time.monotonic()
            self._cursors = {key: value for key, value in self._cursors.items()
                             if now - value["created"] < CURSOR_LIFETIME}
            if len(self._cursors) >= 240:
                self._cursors.pop(next(iter(self._cursors)))
            state["created"] = now
            state["busy"] = False
            state["finished"] = threading.Event()
            state.pop("search_token", None)
            self._cursors[token] = state
        return token

    def settings(self):
        player = load_player_settings()
        return {"resolution": player.youtube_resolution,
                "autoplay": bool(player.youtube_autoplay)}

    def save(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"resolution", "autoplay"}:
            raise WebServiceError("invalid_settings", "Invalid YouTube settings", 400)
        if "resolution" in payload and str(payload["resolution"]) not in YOUTUBE_QUALITIES:
            raise WebServiceError("invalid_settings", "Invalid resolution", 400)
        if "autoplay" in payload and not isinstance(payload["autoplay"], bool):
            raise WebServiceError("invalid_settings", "Invalid autoplay option", 400)

        def update():
            player = load_player_settings()
            if "resolution" in payload:
                player.youtube_resolution = str(payload["resolution"])
            if "autoplay" in payload:
                player.youtube_autoplay = payload["autoplay"]
            if not save_player_settings(player):
                raise WebServiceError("settings_not_saved", "Settings could not be saved", 500)
            return self.settings()
        return WEB_REMOTE._run_on_gui(update)

    def history(self):
        return {"queries": self._history.list()}

    def delete_history(self, payload):
        if not isinstance(payload, dict) or set(payload) - {"query"}:
            raise WebServiceError("invalid_query", "Invalid search query", 400)
        try:
            values = self._history.delete(payload.get("query") if "query" in payload else None)
        except ValueError:
            raise WebServiceError("invalid_query", "Invalid search query", 400) from None
        except OSError:
            raise WebServiceError("settings_not_saved", "Settings could not be saved", 500) from None
        return {"queries": values}

    def playback_history(self):
        videos = self._playback_history.list()
        quality = str(load_player_settings().youtube_resolution)
        context = secrets.token_urlsafe(24) if videos else ""
        response = {"results": videos, "quality": quality, "cursor": "", "context": context}
        if context:
            with self._lock:
                now = time.monotonic()
                self._lists = {key: value for key, value in self._lists.items()
                               if now - value["created"] < CURSOR_LIFETIME}
                if len(self._lists) >= 240:
                    self._lists.pop(next(iter(self._lists)))
                self._lists[context] = {"created": now, "response": response,
                                       "query": "", "language": metadata_language(), "history": True}
        return {"videos": videos, "quality": quality, "context": context}

    def delete_playback_history(self, payload):
        if (not isinstance(payload, dict) or set(payload) - {"id"}
                or ("id" in payload and (not isinstance(payload["id"], str)
                    or not VIDEO_ID.fullmatch(payload["id"])))):
            raise WebServiceError("invalid_video", "Invalid video selection", 400)
        try:
            self._playback_history.delete(payload.get("id"))
        except OSError:
            raise WebServiceError("settings_not_saved", "Settings could not be saved", 500) from None
        return self.playback_history()

    def _result_page(self, state, on_update=None, cancel_event=None):
        """Page ordinary video cards without fetching per-video metadata."""
        results, requests = [], 0
        deadline = time.monotonic() + SEARCH_SECONDS
        context = secrets.token_urlsafe(24)

        def check_current():
            if cancel_event is not None and cancel_event.is_set():
                raise WebServiceError("cancelled", "Search cancelled", 409)

        def publish(pending, cursor=""):
            check_current()
            response = {"results": list(results), "page": state["page"],
                        "has_more": bool(cursor), "cursor": cursor, "context": context,
                        "quality": str(load_player_settings().youtube_resolution), "pending": pending,
                        "pagination_warning": state.get("pagination_warning", "")}
            with self._lock:
                now = time.monotonic()
                self._lists = {key: value for key, value in self._lists.items()
                               if now - value["created"] < CURSOR_LIFETIME}
                if context not in self._lists and len(self._lists) >= 240:
                    self._lists.pop(next(iter(self._lists)))
                self._lists[context] = {"created": now, "response": response,
                                       "query": state["query"], "language": state["language"]}
            if on_update is not None:
                on_update(response)
            return response

        while len(results) < PAGE_SIZE and time.monotonic() < deadline:
            check_current()
            count = min(PAGE_SIZE - len(results), len(state["buffer"]))
            if count:
                results.extend(state["buffer"][:count])
                del state["buffer"][:count]
                if on_update is not None:
                    publish(True)
            continuation = state["continuation"]
            if (len(results) >= PAGE_SIZE or not continuation or not state["config"]
                    or requests >= SEARCH_CONTINUATIONS):
                break
            token = continuation["continuation"]
            data = _continuation_page(state["config"], dict(continuation), state["language"])
            check_current()
            requests += 1
            state["continuations_seen"].add(token)
            state["continuation"] = _continuation(data)
            if (state["continuation"] and state["continuation"]["continuation"]
                    in state["continuations_seen"]):
                state["continuation"] = None
            state["buffer"].extend(_results(data, state["seen"]))

        check_current()
        next_state = {key: value for key, value in state.items()
                      if key not in ("response", "created", "busy", "finished", "search_token")}
        next_state["page"] += 1
        return publish(False, self._remember_cursor(next_state))

    def search(self, query, cursor="", language=None, on_update=None, cancel_event=None):
        query = normalize_query(query)
        if not query:
            raise WebServiceError("invalid_query", "Enter at least two characters", 400)
        if cancel_event is not None and cancel_event.is_set():
            raise WebServiceError("cancelled", "Search cancelled", 409)
        if cursor:
            # Navigation and autoplay can reach a page while its background
            # preparation is running. Join that work outside the service lock.
            waiting_until = time.monotonic() + SEARCH_SECONDS + 30
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    raise WebServiceError("cancelled", "Search cancelled", 409)
                with self._lock:
                    state = self._cursors.get(str(cursor))
                    if state is None or time.monotonic() - state["created"] >= CURSOR_LIFETIME:
                        raise WebServiceError("youtube_page_expired", "Search again to see more results", 410)
                    if state["query"] != query:
                        raise WebServiceError("invalid_query", "Search changed; search again", 400)
                    if state.get("response") is not None:
                        return dict(state["response"], quality=str(load_player_settings().youtube_resolution))
                    finished = state.setdefault("finished", threading.Event())
                    if not state["busy"]:
                        state["busy"] = True
                        finished.clear()
                        break
                if time.monotonic() >= waiting_until:
                    raise WebServiceError("youtube_page_busy", "Results are loading", 409)
                finished.wait(.25)
            try:
                working = dict(state, buffer=list(state["buffer"]), seen=set(state["seen"]),
                               continuations_seen=set(state["continuations_seen"]))
                response = self._result_page(working, on_update, cancel_event)
                with self._lock:
                    state["response"] = response
                return response
            finally:
                with self._lock:
                    state["busy"] = False
                    finished.set()

        try:
            self._history.add(query)
        except OSError:
            raise WebServiceError("settings_not_saved", "Settings could not be saved", 500) from None
        language = language if language else metadata_language()
        data, config = _search_page(query, language)
        if cancel_event is not None and cancel_event.is_set():
            raise WebServiceError("cancelled", "Search cancelled", 409)
        seen = set()
        entries = _results(data, seen)
        if not entries and "itemSectionRenderer" not in json.dumps(data, ensure_ascii=False):
            raise WebServiceError("youtube_unavailable", "YouTube search is unavailable", 502)
        continuation = _continuation(data)
        state = {"query": query, "language": language, "config": config,
                 "buffer": entries, "continuation": continuation if config else None,
                 "seen": seen, "continuations_seen": set(), "page": 1,
                 "pagination_warning": "youtube_page_unavailable" if continuation and not config else ""}
        return self._result_page(state, on_update, cancel_event)

    def search_async(self, query, cursor="", language=None):
        query = normalize_query(query)
        if not query:
            raise WebServiceError("invalid_query", "Invalid search query", 400)
        if not isinstance(cursor, str) or len(cursor) > 100:
            raise WebServiceError("youtube_page_expired", "Search again to see more results", 410)
        quality = str(load_player_settings().youtube_resolution)
        with self._lock:
            now = time.monotonic()
            self._search_jobs = {key: job for key, job in self._search_jobs.items()
                                 if not job["done"].is_set() or now - job["created"] < 300}
            page = 1
            if cursor:
                stored = self._cursors.get(cursor)
                if not stored or now - stored["created"] >= CURSOR_LIFETIME:
                    raise WebServiceError("youtube_page_expired", "Search again to see more results", 410)
                if stored["query"] != query:
                    raise WebServiceError("invalid_query", "Invalid search query", 400)
                page = stored["page"]
                existing_token = stored.get("search_token", "")
                existing = self._search_jobs.get(existing_token)
                if (existing and not existing["cancel"].is_set()
                        and not existing["response"].get("error")):
                    return self.search_status(existing_token)
            if sum(not job["done"].is_set() for job in self._search_jobs.values()) >= 4:
                raise WebServiceError("youtube_page_busy", "Results are loading", 409)
            if len(self._search_jobs) >= 32:
                completed = next((key for key, job in self._search_jobs.items() if job["done"].is_set()), None)
                if completed is not None:
                    self._search_jobs.pop(completed)
            token = secrets.token_urlsafe(24)
            initial = {"results": [], "page": page, "quality": quality,
                       "context": "", "cursor": "", "has_more": False, "pending": True,
                       "pagination_warning": "", "search_token": token}
            job = {"created": now, "cancel": threading.Event(), "done": threading.Event(),
                   "response": initial, "quality": quality}
            self._search_jobs[token] = job
            if cursor:
                stored["search_token"] = token

        def publish(response):
            with self._lock:
                if not job["cancel"].is_set():
                    job["response"] = dict(response, search_token=token)

        def work():
            try:
                response = self.search(query, cursor, language, publish, job["cancel"])
                publish(response)
            except Exception as exc:
                code = getattr(exc, "code", "youtube_unavailable")
                with self._lock:
                    if not job["cancel"].is_set():
                        job["response"] = dict(job["response"], pending=False, error=code,
                                               pagination_warning=code)
                log_event("youtube", "search-failed error_type={}".format(type(exc).__name__))
            finally:
                job["done"].set()

        worker = threading.Thread(target=work, name="GT-YouTube-Progress")
        worker.daemon = True
        worker.start()
        return self.search_status(token)

    def search_status(self, token):
        with self._lock:
            job = self._search_jobs.get(token) if isinstance(token, str) and len(token) <= 100 else None
            if job is None or (job["done"].is_set() and time.monotonic() - job["created"] >= 300):
                if job is not None:
                    self._search_jobs.pop(token, None)
                raise WebServiceError("youtube_page_expired", "Search again to see more results", 410)
            return dict(job["response"], quality=str(load_player_settings().youtube_resolution),
                        results=[dict(entry) for entry in job["response"]["results"]])

    def cancel_search(self, token):
        with self._lock:
            job = self._search_jobs.get(token) if isinstance(token, str) and len(token) <= 100 else None
            if job is not None:
                job["cancel"].set()
                job["response"] = dict(job["response"], pending=False, error="cancelled")
        return {"cancelled": job is not None}

    def _play_sequence(self, context, video_id, quality):
        if not context:
            return None
        with self._lock:
            listing = self._lists.get(context)
            if listing is None or time.monotonic() - listing["created"] >= CURSOR_LIFETIME:
                raise WebServiceError("youtube_page_expired", "Search again to see more results", 410)
            response = listing["response"]
            entries = response["results"]
            index = next((i for i, entry in enumerate(entries) if entry["id"] == video_id), -1)
            if index < 0:
                raise WebServiceError("invalid_video", "Invalid video selection", 400)
            return {"context": context, "entries": entries, "index": index,
                    "quality": quality, "cursor": response["cursor"],
                    "query": listing["query"], "language": listing["language"],
                    "next_state": self._cursors.get(response["cursor"]),
                    "history": bool(listing.get("history"))}

    def _same_player(self, expected):
        """A mini/fullscreen handoff keeps one logical playback owner."""
        current = self._player
        if current is expected:
            return True
        owner = (current if getattr(current, "_youtube_preview", False)
                 else getattr(current, "_youtube_preview_owner", None))
        previous_owner = (expected if getattr(expected, "_youtube_preview", False)
                          else getattr(expected, "_youtube_preview_owner", None))
        if owner is None or owner is not previous_owner or getattr(owner, "_closed", True):
            return False
        from .youtube_preview import same_reference
        return same_reference(getattr(current, "reference", None),
                              getattr(expected, "reference", None))

    def adopt_preview_fullscreen(self, owner, dialog):
        with self._lock:
            if self._player is not owner or getattr(owner, "_closed", True):
                return False
            self._player = dialog
        dialog.set_youtube_end_callback(self._video_ended)
        dialog.onClose.append(lambda: self._player_closed(dialog))
        return True

    def play(self, video_id, title, context="", _automatic=False, _guard=None,
             _sequence_override=None, _preview_owner=None):
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
            raise WebServiceError("invalid_video", "Invalid video selection", 400)
        if not isinstance(context, str) or len(context) > 100:
            raise WebServiceError("invalid_video", "Invalid video selection", 400)
        player = load_player_settings()
        quality = str(player.youtube_resolution)
        sequence = (_sequence_override if _automatic and _guard is not None
                    else self._play_sequence(context, video_id, quality))
        if _automatic and (not sequence or sequence["quality"] != quality):
            return {"token": "", "state": "cancelled"}
        title = str(title or "YouTube")[:160]
        import secrets
        token = secrets.token_urlsafe(18)
        with self._lock:
            if _guard is not None and (not self._same_player(_guard[0])
                    or self._latest_play_token != _guard[1] or not player.youtube_autoplay):
                return {"token": "", "state": "cancelled"}
            self._autoplay_pending = False
            self._jobs = {key: val for key, val in self._jobs.items() if time.monotonic() - val["created"] < 300}
            if len(self._jobs) > 30:
                self._jobs.pop(next(iter(self._jobs)))
            self._jobs[token] = {"state": "resolving", "created": time.monotonic(), "title": title,
                                 "requested_quality": quality}
            self._latest_play_token = token

        def worker():
            state = "failed"
            try:
                from .youtube_vendor.video_url import YouTubeVideoUrl
                if str(load_player_settings().youtube_resolution) != quality:
                    with self._lock:
                        self._jobs[token]["state"] = "cancelled"
                    return
                with EXTRACT_LOCK:
                    set_preferences(player.youtube_resolution, _search_locale(metadata_language())[0],
                                    player.youtube_dash, player.youtube_stream_mode,
                                    player.youtube_audio_preference)
                    resolver = YouTubeVideoUrl()
                    resolver.preferred_quality = int(quality)
                    url = resolver.extract(video_id)
                if not url:
                    raise ValueError("no video stream")
                unused_video, audio = youtube_stream_parts(url)
                video_codec = getattr(resolver, "selected_video_codec", "")
                service_type = youtube_service_type(url, player.movie_service_type, video_codec)
                selected_quality = int(getattr(resolver, "selected_quality", 0) or 0)
                if not 0 < selected_quality <= 2160:
                    raise ValueError("invalid video stream quality")
                duration_seconds = int(getattr(resolver, "duration_seconds", 0) or 0)
                print("[GTYouTube] video_id={} requested={} selected={} "
                      "video_itag={} audio_itag={} engine={} native_audio={} duration={}".format(
                          video_id, player.youtube_resolution, selected_quality,
                          getattr(resolver, "selected_video_itag", ""),
                          getattr(resolver, "selected_audio_itag", ""),
                          service_type, bool(audio), duration_seconds))
                log_event(
                    "youtube",
                    "playback-resolved video_id={} requested={} selected={} "
                    "engine={} codec={} separate_audio={} duration={}".format(
                        video_id, player.youtube_resolution, selected_quality,
                        service_type, video_codec, bool(audio), duration_seconds,
                    ),
                )

                with self._lock:
                    if token != self._latest_play_token:
                        self._jobs[token]["state"] = "cancelled"
                        return

                def launch():
                    current = load_player_settings()
                    with self._lock:
                        if (token != self._latest_play_token
                                or str(current.youtube_resolution) != quality
                                or (_automatic and (not current.youtube_autoplay
                                    or not self._same_player(_guard[0])))):
                            return False
                    from enigma import eServiceReference
                    from .browser import GTExternalPlayerScreen
                    from .content import ContentItem
                    item = ContentItem("movie", video_id, title)
                    item.youtube_requested_quality = str(player.youtube_resolution)
                    item.youtube_selected_quality = selected_quality
                    item.youtube_duration_seconds = duration_seconds
                    item.youtube_native = True
                    item.youtube_separate_audio = bool(audio)
                    item.youtube_video_codec = video_codec
                    reference = eServiceReference(service_type, 0, url)
                    reference.setName(title)
                    session = WEB_REMOTE.session
                    with self._lock:
                        active = self._player
                    preview = (_preview_owner if _preview_owner is not None else
                               active if _automatic and getattr(active, "_youtube_preview", False) else None)
                    if preview is not None and (
                            getattr(preview, "_closed", True)
                            or not getattr(preview, "_preview_available", False)
                            or getattr(session, "current_dialog", None) is not preview
                            or active is not None and active is not preview):
                        return False
                    if active is not None:
                        if (getattr(active, "_closed", False)
                                or getattr(session, "current_dialog", None) is not active):
                            return False
                        active._save_resume_position()
                    # Both TV and web history cards carry this server-owned
                    # context. Read current seconds, rather than a stale card.
                    bookmark = (self._playback_history.get(video_id)
                                if sequence and sequence.get("history") and not _automatic else None)
                    start_position = youtube_resume_position(
                        (bookmark or {}).get("position", 0), duration_seconds
                    )
                    if preview is not None:
                        if not preview.replace_web_video(
                                reference, item, resume_store=self._playback_history,
                                start_position=start_position):
                            return False
                        with self._lock:
                            self._player = preview
                        if getattr(preview, "_youtube_service_owner", None) is not self:
                            preview._youtube_service_owner = self
                            preview.onClose.append(lambda: self._player_closed(preview))
                    elif active is not None:
                        # Enigma2 pushes every session.open() onto the modal
                        # stack; replace the playing service in the same screen.
                        # Wait for onClose before opening again if EXIT won a
                        # race with this web request.
                        if (getattr(active, "_closed", False)
                                or getattr(session, "current_dialog", None) is not active):
                            return False
                        if not active.replace_web_video(
                                reference, item, resume_store=self._playback_history,
                                start_position=start_position):
                            return False
                        if getattr(active, "_youtube_preview", False) and not _automatic:
                            active._open_youtube_fullscreen()
                    else:
                        interrupted = getattr(session, "current_dialog", None)
                        suspend = getattr(interrupted, "suspend_for_web_video", None)
                        return_state = suspend() if callable(suspend) else None
                        try:
                            if return_state is not None:
                                dialog = session.open(
                                    GTExternalPlayerScreen, reference, item,
                                    web_return_player=(interrupted, return_state),
                                    resume_store=self._playback_history,
                                    resume_key_value=video_id, start_position=start_position,
                                )
                            else:
                                dialog = session.open(
                                    GTExternalPlayerScreen, reference, item,
                                    resume_store=self._playback_history,
                                    resume_key_value=video_id, start_position=start_position,
                                )
                        except Exception:
                            if return_state is not None:
                                interrupted.resume_from_web_video(return_state)
                            raise
                        if dialog is None:
                            if return_state is not None:
                                interrupted.resume_from_web_video(return_state)
                            return False
                        with self._lock:
                            self._player = dialog
                        callbacks = getattr(dialog, "onClose", None)
                        if callbacks is not None:
                            callbacks.append(lambda: self._player_closed(dialog))
                    dialog = self._player
                    dialog.set_youtube_end_callback(self._video_ended)
                    with self._lock:
                        self._sequence = sequence
                        self._playing = {"id": video_id, "title": title,
                                         "requested_quality": str(player.youtube_resolution),
                                         "quality": selected_quality,
                                         "duration": duration_seconds}
                        self._jobs[token]["quality"] = selected_quality
                    return True

                state = "playing" if WEB_REMOTE._run_on_gui(launch) else "cancelled"
                if state == "playing":
                    try:
                        self._playback_history.add(video_id, title, selected_quality, duration_seconds)
                    except OSError as exc:
                        # A storage failure must not stop an already launched video.
                        log_event("youtube", "playback-history-write-failed error_type={}".format(type(exc).__name__))
            except Exception as exc:
                # Signed media URLs must never be written to device logs.
                print("[GTYouTube] playback failed video_id={} error={}".format(
                    video_id, type(exc).__name__))
                log_event("youtube", "playback-failed video_id={} error_type={}".format(
                    video_id, type(exc).__name__))
                with self._lock:
                    if token in self._jobs:
                        self._jobs[token]["error"] = (
                            "youtube_native_player_required" if isinstance(exc, YouTubeNativePlayerUnavailable)
                            else getattr(exc, "code", "youtube_play_failed"))
                state = "failed"
            finally:
                with self._lock:
                    if token in self._jobs and self._jobs[token]["state"] != "cancelled":
                        self._jobs[token]["state"] = state

        threading.Thread(target=worker, name="GT-YouTube-Play", daemon=True).start()
        return {"token": token, "state": "resolving"}

    def cancel_play(self, token):
        """Prevent a pending native-screen request from opening after EXIT."""
        with self._lock:
            job = self._jobs.get(str(token))
            if (token == self._latest_play_token and job is not None
                    and job["state"] == "resolving"):
                self._latest_play_token = ""
                job["state"] = "cancelled"
                return True
        return False

    def _video_ended(self, dialog):
        """Called only for the current native video's end, on the GUI thread."""
        player = load_player_settings()
        with self._lock:
            if self._player is not dialog or getattr(dialog, "_closed", False):
                return
            if self._playing:
                self._playing["ended"] = True
                try:
                    self._playback_history.finish(self._playing["id"])
                except OSError as exc:
                    log_event("youtube", "playback-history-write-failed error_type={}".format(type(exc).__name__))
            sequence = self._sequence
            if sequence:
                listing = self._lists.get(sequence["context"])
                response = listing.get("response") if listing else None
                if response:
                    current_id = (self._playing or {}).get("id")
                    index = next((position for position, entry in enumerate(response["results"])
                                  if entry["id"] == current_id), -1)
                    if index >= 0:
                        sequence = dict(sequence, entries=response["results"], index=index,
                                        cursor=response["cursor"],
                                        next_state=(self._cursors.get(response["cursor"])
                                                    or (sequence.get("next_state")
                                                        if response["cursor"] == sequence["cursor"] else None)))
                        self._sequence = sequence
            if sequence:
                sequence = dict(sequence, quality=str(player.youtube_resolution))
            latest_job = self._jobs.get(self._latest_play_token) or {}
            if (not player.youtube_autoplay or not sequence or self._autoplay_pending
                    or latest_job.get("state") != "playing"):
                return
            guard = (dialog, self._latest_play_token)
            self._autoplay_pending = True

        def advance():
            try:
                context = sequence["context"]
                index = sequence["index"] + 1
                entries = sequence["entries"]
                cursor = sequence["cursor"]
                next_state = sequence.get("next_state")
                # A short video can end while its search is still filling.
                # Wait off the GUI thread for the next search result.
                waiting_until = time.monotonic() + SEARCH_SECONDS + 15
                while True:
                    preferences = load_player_settings()
                    with self._lock:
                        if (not self._same_player(dialog) or self._latest_play_token != guard[1]
                                or not preferences.youtube_autoplay
                                or str(preferences.youtube_resolution) != sequence["quality"]):
                            return
                        listing = self._lists.get(context)
                        latest = listing.get("response") if listing else None
                        search_job = next((job for job in self._search_jobs.values()
                                           if job["response"].get("context") == context), None)
                        if latest:
                            current_id = (self._playing or {}).get("id")
                            current_index = next((position for position, entry in enumerate(latest["results"])
                                                  if entry["id"] == current_id), -1)
                            if current_index >= 0:
                                next_state = (self._cursors.get(latest["cursor"])
                                              or (next_state if latest["cursor"] == cursor else None))
                                entries, index, cursor = latest["results"], current_index + 1, latest["cursor"]
                        pending = bool(latest and latest.get("pending")
                                       and (not search_job or not search_job["done"].is_set()
                                            and not search_job["cancel"].is_set()))
                    if index < len(entries) or not pending:
                        break
                    if time.monotonic() >= waiting_until:
                        return
                    if search_job:
                        search_job["done"].wait(.25)
                    else:
                        time.sleep(.25)
                # Continue the same ordinary search, including an empty page.
                for unused in range(4):
                    if index < len(entries):
                        break
                    if not cursor:
                        return
                    with self._lock:
                        stored = self._cursors.get(cursor)
                    if not stored or time.monotonic() - stored["created"] >= CURSOR_LIFETIME:
                        if not next_state:
                            return
                        # An active queue outlives the web paging cache. Keep
                        # its next page state without retaining signed URLs.
                        restored = {key: value for key, value in next_state.items()
                                    if key not in ("response", "busy", "created")}
                        cursor = self._remember_cursor(restored)
                    response = self.search(sequence["query"], cursor, sequence["language"])
                    context, entries, index = response["context"], response["results"], 0
                    cursor = response["cursor"]
                    with self._lock:
                        next_state = self._cursors.get(cursor)
                if index >= len(entries):
                    return
                with self._lock:
                    if not self._same_player(dialog) or self._latest_play_token != guard[1]:
                        return
                entry = entries[index]
                next_sequence = dict(sequence, context=context, entries=entries,
                                     index=index, cursor=cursor, next_state=next_state)
                self.play(entry["id"], entry["title"], context, _automatic=True,
                          _guard=guard, _sequence_override=next_sequence)
            except Exception as exc:
                log_event("youtube", "autoplay-stopped error_type={}".format(type(exc).__name__))
            finally:
                with self._lock:
                    if self._latest_play_token == guard[1]:
                        self._autoplay_pending = False
        threading.Thread(target=advance, name="GT-YouTube-Next", daemon=True).start()

    def _player_closed(self, dialog):
        with self._lock:
            if self._player is dialog:
                owner = getattr(dialog, "_youtube_preview_owner", None)
                if (getattr(dialog, "_youtube_preview_kept", False)
                        and owner is not None and not getattr(owner, "_closed", True)
                        and owner._preview_owns_service()):
                    self._player = owner
                    owner.set_youtube_end_callback(self._video_ended)
                    return
                self._player = None
                self._playing = None
                self._sequence = None
                self._autoplay_pending = False
                self._latest_play_token = ""
                for job in self._jobs.values():
                    if job["state"] == "resolving":
                        job["state"] = "cancelled"

    def status(self, token=""):
        with self._lock:
            job = self._jobs.get(str(token)) if token else None
            playing = dict(self._playing) if self._playing else None
        if job and time.monotonic() - job["created"] >= 300:
            job = None
        result = {"job": {"state": job["state"], "title": job["title"],
                          "requested_quality": job.get("requested_quality", ""),
                          "quality": job.get("quality", 0),
                          "error": job.get("error", "")} if job else None,
                  "playing": None, "settings": self.settings(),
                  "autoplay_pending": self._autoplay_pending}
        if playing:
            def active():
                from .browser import GTExternalPlayerScreen
                dialog = getattr(WEB_REMOTE.session, "current_dialog", None)
                item = getattr(dialog, "current_item", None)
                if ((not isinstance(dialog, GTExternalPlayerScreen)
                        and not (dialog is self._player and getattr(dialog, "_youtube_preview", False)))
                        or getattr(item, "stream_id", None) != playing["id"]):
                    return None
                position = length = 0
                try:
                    position, length = dialog._seek_position()
                    position = max(0, int(position))
                    length = max(0, int(length))
                except Exception:
                    pass
                return {"id": playing["id"], "title": playing["title"],
                        "requested_quality": playing.get("requested_quality", ""),
                        "quality": playing.get("quality", 0),
                        "position": position, "length": length,
                        "ended": bool(playing.get("ended"))}
            try:
                result["playing"] = WEB_REMOTE._run_on_gui(active)
            except WebServiceError:
                pass
        return result


WEB_YOUTUBE = WebYouTube()

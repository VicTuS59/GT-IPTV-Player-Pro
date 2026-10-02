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
from .web_api import WebServiceError
from .web_remote import WEB_REMOTE
from .youtube_vendor.config import EXTRACT_LOCK, set_preferences


VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
PAGE_SIZE = 12
CURSOR_LIFETIME = 1800
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
    url = "https://www.youtube.com/results?{}".format(urlencode({
        "search_query": query, "hl": youtube_language, "gl": region,
    }))
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
                        "duration": _label(entry, "lengthText", 20)})
        if len(entries) >= 120:
            break
    return entries


class WebYouTube(object):
    def __init__(self):
        self._lock = threading.RLock()
        self._jobs = {}
        self._playing = None
        self._player = None
        self._remux = None
        self._latest_play_token = ""
        self._cursors = {}

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
            self._cursors[token] = state
        return token

    def settings(self):
        player = load_player_settings()
        return {"resolution": player.youtube_resolution,
                "dash": player.youtube_dash,
                "mode": player.youtube_stream_mode,
                "audio_preference": player.youtube_audio_preference}

    def save(self, payload):
        allowed = ("resolution", "dash", "language", "mode", "audio_preference")
        if set(payload) - set(allowed):
            raise WebServiceError("invalid_settings", "Invalid YouTube settings", 400)
        if "resolution" in payload and str(payload["resolution"]) not in ("360", "480", "720", "1080", "2160"):
            raise WebServiceError("invalid_settings", "Invalid resolution", 400)
        # Old browser tabs may still submit their former language selection.
        # It is ignored: search language belongs to each requesting device.
        if "language" in payload and payload["language"] not in ("tr", "en", "de", "fr", "es", "ar"):
            raise WebServiceError("invalid_settings", "Invalid language", 400)
        if "dash" in payload and not isinstance(payload["dash"], bool):
            raise WebServiceError("invalid_settings", "Invalid format", 400)
        if "mode" in payload and payload["mode"] not in ("auto", "compatible", "dash"):
            raise WebServiceError("invalid_settings", "Invalid format", 400)
        if "audio_preference" in payload and payload["audio_preference"] not in ("default", "original"):
            raise WebServiceError("invalid_settings", "Invalid audio preference", 400)
        if "dash" in payload and "mode" in payload:
            raise WebServiceError("invalid_settings", "Conflicting format options", 400)

        def update():
            player = load_player_settings()
            if "resolution" in payload:
                player.youtube_resolution = str(payload["resolution"])
            if "dash" in payload:
                player.youtube_dash = payload["dash"]
                player.youtube_stream_mode = "auto" if payload["dash"] else "compatible"
            if "mode" in payload:
                player.youtube_stream_mode = payload["mode"]
                player.youtube_dash = payload["mode"] != "compatible"
            if "audio_preference" in payload:
                player.youtube_audio_preference = payload["audio_preference"]
            if not save_player_settings(player):
                raise WebServiceError("settings_not_saved", "Settings could not be saved", 500)
            return self.settings()
        return WEB_REMOTE._run_on_gui(update)

    def search(self, query, cursor="", language=None):
        query = str(query or "").strip()
        if len(query) < 2 or len(query) > 120:
            raise WebServiceError("invalid_query", "Enter at least two characters", 400)
        if cursor:
            with self._lock:
                state = self._cursors.get(str(cursor))
                if state is None or time.monotonic() - state["created"] >= CURSOR_LIFETIME:
                    raise WebServiceError("youtube_page_expired", "Search again to see more results", 410)
                if state["query"] != query:
                    raise WebServiceError("invalid_query", "Search changed; search again", 400)
                if state.get("response") is not None:
                    return state["response"]
                if state["busy"]:
                    raise WebServiceError("youtube_page_busy", "Results are loading", 409)
                state["busy"] = True
            try:
                buffer = list(state["buffer"])
                continuation = state["continuation"]
                seen = set(state["seen"])
                continuations_seen = set(state.get("continuations_seen", ()))
                results = []
                # A bounded number of requests fills a page even when a
                # continuation only contains repeated or non-video entries.
                for unused in range(4):
                    take = min(PAGE_SIZE - len(results), len(buffer))
                    results.extend(buffer[:take])
                    del buffer[:take]
                    if len(results) == PAGE_SIZE or not continuation:
                        break
                    previous = continuation["continuation"]
                    data = _continuation_page(state["config"], continuation, state["language"])
                    continuations_seen.add(previous)
                    continuation = _continuation(data)
                    if continuation and continuation["continuation"] in continuations_seen:
                        continuation = None
                    buffer.extend(_results(data, seen))
                if not results:
                    raise WebServiceError("youtube_page_unavailable", "No more results were returned", 502)
                next_cursor = self._remember_cursor({
                    "query": query, "language": state["language"], "config": state["config"],
                    "buffer": buffer, "continuation": continuation, "seen": seen,
                    "continuations_seen": continuations_seen, "page": state["page"] + 1,
                    "pagination_warning": state.get("pagination_warning", ""),
                })
                response = {"results": results, "page": state["page"],
                            "has_more": bool(next_cursor), "cursor": next_cursor,
                            "pagination_warning": state.get("pagination_warning", "")}
                with self._lock:
                    state["response"] = response
                return response
            finally:
                with self._lock:
                    state["busy"] = False

        # Native callers have no browser locale; follow the receiver language.
        # Web callers supply their own browser locale per search.
        language = language if language else metadata_language()
        data, config = _search_page(query, language)
        seen = set()
        entries = _results(data, seen)
        if not entries and "itemSectionRenderer" not in json.dumps(data, ensure_ascii=False):
            raise WebServiceError("youtube_unavailable", "YouTube search is unavailable", 502)
        continuation = _continuation(data)
        warning = "youtube_page_unavailable" if continuation and not config else ""
        cursor = self._remember_cursor({
            "query": query, "language": language, "config": config,
            "buffer": entries[PAGE_SIZE:], "continuation": continuation if config else None,
            "seen": seen, "continuations_seen": set(), "page": 2,
            "pagination_warning": warning,
        })
        return {"results": entries[:PAGE_SIZE], "page": 1,
                "has_more": bool(cursor), "cursor": cursor,
                "pagination_warning": warning}

    def play(self, video_id, title):
        if not isinstance(video_id, str) or not VIDEO_ID.fullmatch(video_id):
            raise WebServiceError("invalid_video", "Invalid video selection", 400)
        title = str(title or "YouTube")[:160]
        import secrets
        token = secrets.token_urlsafe(18)
        with self._lock:
            self._jobs = {key: val for key, val in self._jobs.items() if time.monotonic() - val["created"] < 300}
            if len(self._jobs) > 30:
                self._jobs.pop(next(iter(self._jobs)))
            self._jobs[token] = {"state": "resolving", "created": time.monotonic(), "title": title}
            self._latest_play_token = token

        def worker():
            remux = None
            state = "failed"
            try:
                from .youtube_vendor.video_url import YouTubeVideoUrl
                player = load_player_settings()
                with EXTRACT_LOCK:
                    set_preferences(player.youtube_resolution, _search_locale(metadata_language())[0],
                                    player.youtube_dash, player.youtube_stream_mode,
                                    player.youtube_audio_preference)
                    resolver = YouTubeVideoUrl()
                    url = resolver.extract(video_id)
                if not url:
                    raise ValueError("no video stream")
                video, separator, audio = url.partition("&suburi=")
                for candidate in (video, audio if separator else video):
                    parsed = urlsplit(candidate)
                    host = (parsed.hostname or "").lower()
                    if parsed.scheme != "https" or not any(host == domain or host.endswith("." + domain) for domain in ("googlevideo.com", "youtube.com")):
                        raise ValueError("invalid stream host")

                playback_url = url
                if separator:
                    try:
                        from .youtube_remux import YouTubeRemux
                        remux = YouTubeRemux(video, audio)
                        playback_url = remux.url
                    except (OSError, ValueError):
                        # Enigma2's original separate-stream service remains
                        # usable on images without FFmpeg or a loopback socket.
                        remux = None
                selected_quality = int(getattr(resolver, "selected_quality", 0) or 0)
                duration_seconds = int(getattr(resolver, "duration_seconds", 0) or 0)
                print("[GTYouTube] video_id={} requested={} selected={} "
                      "video_itag={} audio_itag={} engine={} remux={} duration={}".format(
                          video_id, player.youtube_resolution, selected_quality,
                          getattr(resolver, "selected_video_itag", ""),
                          getattr(resolver, "selected_audio_itag", ""),
                          player.movie_service_type, bool(remux), duration_seconds))

                with self._lock:
                    if token != self._latest_play_token:
                        self._jobs[token]["state"] = "cancelled"
                        return

                def launch():
                    with self._lock:
                        if token != self._latest_play_token:
                            return False
                    from enigma import eServiceReference
                    from .browser import GTExternalPlayerScreen
                    from .content import ContentItem
                    item = ContentItem("movie", video_id, title)
                    item.youtube_requested_quality = str(player.youtube_resolution)
                    item.youtube_selected_quality = selected_quality
                    item.youtube_duration_seconds = duration_seconds
                    item.youtube_remux = remux
                    item.youtube_start_seconds = 0
                    service_type = int(player.movie_service_type)
                    if service_type not in (4097, 5001, 5002):
                        service_type = 4097
                    reference = eServiceReference(service_type, 0, playback_url)
                    session = WEB_REMOTE.session
                    with self._lock:
                        active = self._player
                        old_remux = self._remux
                    if active is not None:
                        # Enigma2 pushes every session.open() onto the modal
                        # stack; replace the playing service in the same screen.
                        # Wait for onClose before opening again if EXIT won a
                        # race with this web request.
                        if (getattr(active, "_closed", False)
                                or getattr(session, "current_dialog", None) is not active):
                            return False
                        if not active.replace_web_video(reference, item):
                            return False
                    else:
                        dialog = session.open(GTExternalPlayerScreen, reference, item)
                        if dialog is None:
                            return False
                        with self._lock:
                            self._player = dialog
                        callbacks = getattr(dialog, "onClose", None)
                        if callbacks is not None:
                            callbacks.append(lambda: self._player_closed(dialog))
                    with self._lock:
                        self._remux = remux
                        self._playing = {"id": video_id, "title": title,
                                         "requested_quality": str(player.youtube_resolution),
                                         "quality": selected_quality,
                                         "duration": duration_seconds}
                        self._jobs[token]["quality"] = selected_quality
                    if old_remux is not None and old_remux is not remux:
                        old_remux.close()
                    return True

                state = "playing" if WEB_REMOTE._run_on_gui(launch) else "cancelled"
            except Exception as exc:
                # Signed media URLs must never be written to device logs.
                print("[GTYouTube] playback failed video_id={} error={}".format(
                    video_id, type(exc).__name__))
                state = "failed"
            finally:
                if state != "playing" and remux is not None:
                    remux.close()
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

    def _player_closed(self, dialog):
        with self._lock:
            if self._player is dialog:
                self._player = None
                self._playing = None
                remux, self._remux = self._remux, None
            else:
                remux = None
        if remux is not None:
            remux.close()

    def status(self, token=""):
        with self._lock:
            job = self._jobs.get(str(token)) if token else None
            playing = dict(self._playing) if self._playing else None
        if job and time.monotonic() - job["created"] >= 300:
            job = None
        result = {"job": {"state": job["state"], "title": job["title"],
                          "quality": job.get("quality", 0)} if job else None,
                  "playing": None}
        if playing:
            def active():
                from .browser import GTExternalPlayerScreen
                dialog = getattr(WEB_REMOTE.session, "current_dialog", None)
                item = getattr(dialog, "current_item", None)
                if not isinstance(dialog, GTExternalPlayerScreen) or getattr(item, "stream_id", None) != playing["id"]:
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
                        "position": position, "length": length}
            try:
                result["playing"] = WEB_REMOTE._run_on_gui(active)
            except WebServiceError:
                pass
        return result


WEB_YOUTUBE = WebYouTube()


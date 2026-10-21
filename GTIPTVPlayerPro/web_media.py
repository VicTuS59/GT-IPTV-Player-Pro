# -*- coding: utf-8 -*-
"""Short-lived, credential-free web selection of saved IPTV VOD items."""
import secrets
import threading
import time
from contextlib import nullcontext

from .downloads import (
    DownloadError, MESSAGES as DOWNLOAD_MESSAGES, STATUS as DOWNLOAD_STATUSES,
    enqueue as enqueue_download, snapshot as download_snapshot,
)
from .web_api import GTWebService, WebServiceError, _source_key, _source_name
from .web_remote import WEB_REMOTE


SELECTION_LIFETIME = 600
EPISODE_PAGE_SIZE = 120
MAX_EPISODE_CONTEXTS = 4
MAX_CACHED_EPISODES = 4000
MAX_ORDINARY_SELECTIONS = 200
MAX_ITEM_SELECTIONS = MAX_ORDINARY_SELECTIONS + MAX_EPISODE_CONTEXTS * EPISODE_PAGE_SIZE


class WebMedia(object):
    def __init__(self):
        self._lock = threading.RLock()
        self._items = {}
        # Parent selections have their own bounded lifetime: visiting later
        # pages must not evict the series token along with old episode cards.
        self._episode_contexts = {}
        self._episode_request_serial = 0
        self._service = GTWebService()

    @staticmethod
    def _playable(source, item):
        return item.content_type == "movie" or (
            item.content_type == "series" and (
                bool(getattr(item, "episode", "") or getattr(item, "parent_id", ""))
                or str(getattr(source, "source_type", "")) == "m3u"
            )
        )

    @classmethod
    def _downloadable(cls, source, item):
        return (str(getattr(source, "source_type", "xtream") or "xtream")
                in ("xtream", "stalker") and cls._playable(source, item))

    def _put(self, source, item, episode_context=None):
        token = secrets.token_urlsafe(24)
        with self._lock:
            now = time.monotonic()
            self._prune_episode_contexts_locked(now)
            self._items = {key: value for key, value in self._items.items()
                           if now - value[0] < SELECTION_LIFETIME}
            self._items[token] = (now, _source_key(source), item)
            if episode_context is not None:
                episode_context["tokens"].add(token)
            protected = set().union(*(value["tokens"] for value in self._episode_contexts.values()))
            ordinary = sum(key not in protected for key in self._items)
            while ordinary > MAX_ORDINARY_SELECTIONS or len(self._items) > MAX_ITEM_SELECTIONS:
                victim = next(key for key in self._items if key not in protected)
                self._items.pop(victim)
                ordinary -= 1
        return {"token": token, "name": str(item.name)[:160],
                "kind": str(item.content_type), "source": _source_name(source),
                "playable": self._playable(source, item),
                "downloadable": self._downloadable(source, item),
                "season": str(getattr(item, "season", "") or ""),
                "episode": str(getattr(item, "episode", "") or "")}

    def _get(self, token):
        with self._lock:
            entry = self._items.get(str(token or ""))
        if entry is None or time.monotonic() - entry[0] >= SELECTION_LIFETIME:
            raise WebServiceError("selection_expired", "Search again to select this title", 410)
        return self._service._find_source(entry[1]), entry[2]

    @staticmethod
    def _scope(client):
        scope = getattr(client, "request_scope", None)
        return scope(deadline=time.monotonic() + 15, timeout=9) if callable(scope) else nullcontext()

    def search(self, source_id, query, kind):
        from .browser import content_client_for
        query = str(query or "").strip()
        if len(query) < 2 or len(query) > 120:
            raise WebServiceError("invalid_query", "Enter at least two characters", 400)
        if kind not in ("movie", "series"):
            raise WebServiceError("invalid_type", "Choose movie or series", 400)
        source = self._service._find_source(source_id)
        client = content_client_for(source)
        try:
            with self._scope(client):
                if str(getattr(source, "source_type", "")) == "m3u":
                    items = [item for item in client.search_items(query, limit=100)
                             if item.content_type == kind][:18]
                elif kind == "movie":
                    page = client.search_movies(query, page=1, page_size=18)
                    items = page.items[:18]
                else:
                    # Series catalogues are provider-specific and may be large;
                    # this operation is bounded by the client's request scope.
                    items = [item for item in client.load_items("series")
                             if query.casefold() in item.name.casefold()][:18]
        except Exception:
            raise WebServiceError("media_search_failed", "Source search failed or timed out", 502)
        return {"results": [self._put(source, item) for item in items]}

    def _prune_episode_contexts_locked(self, now):
        self._episode_contexts = {
            key: value for key, value in self._episode_contexts.items()
            if now - value["created"] < SELECTION_LIFETIME
        }

    def _episode_context(self, token):
        token = str(token or "")
        with self._lock:
            self._prune_episode_contexts_locked(time.monotonic())
            context = self._episode_contexts.get(token)
        if context is not None:
            return context, self._service._find_source(context["source"])
        source, item = self._get(token)
        if item.content_type != "series" or getattr(item, "episode", ""):
            raise WebServiceError("invalid_type", "Choose a series", 400)
        with self._lock:
            # Another request may have installed the same context while the
            # source was being read. Never replace its already loaded list.
            context = self._episode_contexts.get(token)
            if context is None:
                while len(self._episode_contexts) >= MAX_EPISODE_CONTEXTS:
                    self._episode_contexts.pop(next(iter(self._episode_contexts)))
                context = {"created": time.monotonic(), "source": _source_key(source),
                           "item": item, "episodes": None, "loading": False,
                           "tokens": set(), "request_serial": 0}
                self._episode_contexts[token] = context
        return context, source

    def _cache_episodes_locked(self, context, episodes):
        if (len(episodes) > MAX_CACHED_EPISODES
                or not any(value is context for value in self._episode_contexts.values())):
            return
        cached = sum(len(value["episodes"] or ())
                     for value in self._episode_contexts.values() if value is not context)
        for value in self._episode_contexts.values():
            if cached + len(episodes) <= MAX_CACHED_EPISODES:
                break
            if value is not context and value["episodes"] is not None:
                cached -= len(value["episodes"])
                value["episodes"] = None
        context["episodes"] = episodes

    def episodes(self, token, page=1):
        from .browser import content_client_for
        if (isinstance(page, bool) or not isinstance(page, (str, int))
                or not str(page).isascii() or not str(page).isdigit()
                or len(str(page)) > 8 or int(page) < 1):
            raise WebServiceError("episodes_failed", "Episodes could not be loaded", 400)
        page = int(page)
        with self._lock:
            # Assign before source lookup: a slow older lookup must not
            # supersede a newer completed request for the same series.
            self._episode_request_serial += 1
            request_serial = self._episode_request_serial
        context, source = self._episode_context(token)
        item = context["item"]
        with self._lock:
            episodes = context["episodes"]
            if request_serial < context["request_serial"]:
                raise WebServiceError("episodes_failed", "Episodes could not be loaded", 409)
            if episodes is None:
                if context["loading"]:
                    raise WebServiceError("episodes_failed", "Episodes could not be loaded", 409)
                context["loading"] = True
            context["request_serial"] = request_serial
        if episodes is None:
            try:
                client = content_client_for(source)
                with self._scope(client):
                    episodes = tuple(client.load_episodes(item.stream_id))
                with self._lock:
                    # Large series remain fully pageable by bounded explicit
                    # refetches. They are not silently cut to the cache budget.
                    if context["request_serial"] == request_serial:
                        self._cache_episodes_locked(context, episodes)
            except Exception:
                raise WebServiceError("episodes_failed", "Episodes could not be loaded", 502)
            finally:
                with self._lock:
                    context["loading"] = False
        total = len(episodes)
        pages = max(1, (total + EPISODE_PAGE_SIZE - 1) // EPISODE_PAGE_SIZE)
        if page > pages:
            raise WebServiceError("episodes_failed", "Episodes could not be loaded", 400)
        start = (page - 1) * EPISODE_PAGE_SIZE
        selected = episodes[start:start + EPISODE_PAGE_SIZE]
        # Commit a page's selection tokens together, so the returned page
        # never evicts its own first cards while replacing the previous page.
        with self._lock:
            if (self._episode_contexts.get(str(token or "")) is not context
                    or time.monotonic() - context["created"] >= SELECTION_LIFETIME):
                raise WebServiceError("selection_expired", "Search again to select this title", 410)
            if context["request_serial"] != request_serial:
                raise WebServiceError("episodes_failed", "Episodes could not be loaded", 409)
            # Only the current page remains pinned for this context. Other
            # contexts retain their current page even if an older browser
            # response finishes later or one series is paged repeatedly.
            for previous_token in context["tokens"]:
                self._items.pop(previous_token, None)
            context["tokens"] = set()
            for episode in selected:
                episode.favorite_parent = item
            results = [self._put(source, episode, context) for episode in selected]
        return {"results": results, "page": page, "page_size": EPISODE_PAGE_SIZE,
                "total": total, "total_pages": pages,
                "has_previous": page > 1, "has_more": page < pages}

    def download(self, token):
        source, item = self._get(token)
        if not self._downloadable(source, item):
            raise WebServiceError("download_unsupported", DOWNLOAD_MESSAGES["unsupported"], 400)
        try:
            from .browser import content_client_for
            # Link resolution and transfer belong to the existing detached
            # worker; the web request only submits the saved selection.
            identity = enqueue_download(content_client_for(source), item)
        except DownloadError as error:
            code = error.code if error.code in DOWNLOAD_MESSAGES else "failed"
            status = 409 if code in ("playing", "busy", "folder", "space") else (
                400 if code == "unsupported" else 500
            )
            raise WebServiceError("download_" + code, DOWNLOAD_MESSAGES[code], status)
        except Exception:
            raise WebServiceError("download_failed", DOWNLOAD_MESSAGES["failed"], 500)
        status = "queued"
        try:
            job = next((job for job in download_snapshot().get("jobs", [])
                        if job.get("id") == identity), {})
            if job.get("status") in DOWNLOAD_STATUSES:
                status = job["status"]
        except Exception:
            pass  # A committed queue request remains successful.
        return {"id": identity, "name": str(item.name)[:160], "status": status}

    def play(self, token):
        source, item = self._get(token)
        if item.content_type not in ("movie", "series"):
            raise WebServiceError("invalid_type", "Select a movie or episode", 400)

        def launch():
            from .browser import content_client_for, open_extplayer
            client = content_client_for(source)
            open_extplayer(WEB_REMOTE.session, client, item)
            return {"requested": True, "name": str(item.name)[:160]}

        return WEB_REMOTE._run_on_gui(launch)


WEB_MEDIA = WebMedia()

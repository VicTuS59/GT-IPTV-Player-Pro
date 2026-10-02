# -*- coding: utf-8 -*-
"""Short-lived, credential-free web selection of saved IPTV VOD items."""
import secrets
import threading
import time
from contextlib import nullcontext

from .web_api import GTWebService, WebServiceError, _source_key, _source_name
from .web_remote import WEB_REMOTE


class WebMedia(object):
    def __init__(self):
        self._lock = threading.RLock()
        self._items = {}
        self._service = GTWebService()

    def _put(self, source, item):
        token = secrets.token_urlsafe(24)
        with self._lock:
            now = time.monotonic()
            self._items = {key: value for key, value in self._items.items()
                           if now - value[0] < 600}
            self._items[token] = (now, _source_key(source), item)
            if len(self._items) > 200:
                self._items.pop(next(iter(self._items)))
        return {"token": token, "name": str(item.name)[:160],
                "kind": str(item.content_type), "source": _source_name(source),
                "playable": item.content_type == "movie" or bool(getattr(item, "episode", "") or getattr(item, "parent_id", "")) or str(getattr(source, "source_type", "")) == "m3u",
                "season": str(getattr(item, "season", "") or ""),
                "episode": str(getattr(item, "episode", "") or "")}

    def _get(self, token):
        with self._lock:
            entry = self._items.get(str(token or ""))
        if entry is None or time.monotonic() - entry[0] >= 600:
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

    def episodes(self, token):
        from .browser import content_client_for
        source, item = self._get(token)
        if item.content_type != "series" or getattr(item, "episode", ""):
            raise WebServiceError("invalid_type", "Choose a series", 400)
        client = content_client_for(source)
        try:
            with self._scope(client):
                episodes = client.load_episodes(item.stream_id)
        except Exception:
            raise WebServiceError("episodes_failed", "Episodes could not be loaded", 502)
        return {"results": [self._put(source, episode) for episode in episodes[:120]]}

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


# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Prepare nearby wide artwork first, then one adjacent VOD page while idle.

Network/metadata work uses one reusable, cancellation-aware reader. Native
image decoders are scheduled individually on the GUI loop and feed the same
bounded cache as visible images. There is no second pixmap cache or hidden UI.
"""
from collections import OrderedDict, deque
from copy import copy

from . import background
from .source_loader import SourceLoader


class VODPreparation(object):
    def __init__(self, screen, api=None):
        if api is None:
            from . import browser as api
        self.screen, self.api = screen, api
        self.timer = api.eTimer()
        api._connect_timer(self.timer, self.poll)
        self.reader = SourceLoader(api.eTimer(), api._connect_timer, self._read_done)
        self.closed = self.paused = False
        self.epoch = object()
        self.position = self.plan = None
        self.last_index = None
        self.direction = 1
        self.jobs, self.images = deque(), deque()
        self.heroes = OrderedDict()
        self.active = self.decoder = self.token = None
        self.more_requested = None
        self.touched_page = None
        self.decode_bytes = 0
        self.card_bytes_reserved = 0
        self.decoder_deadline = 0.0
        self.kind = screen.content_type

    def _field(self, suffix, default=None):
        return getattr(self.screen, "_" + self.kind + "_" + suffix, default)

    def _position(self):
        return (self.kind, self._field("loaded_category_id", ""),
                self._field("window_base", 0) + self._field("selected_index", 0))

    def _stop_work(self):
        self.epoch = object()
        self.timer.stop()
        if self.token is not None:
            self.token.cancel()
        self.token = self.active = None
        self.reader.cancel()
        decoder, self.decoder = self.decoder, None
        if decoder is not None:
            decoder.close()
        self.jobs.clear()
        self.images.clear()
        self.decode_bytes = 0
        self.card_bytes_reserved = 0

    def invalidate(self):
        self._stop_work()
        self.plan = self.position = None
        self.more_requested = None
        self.touched_page = None
        self.heroes.clear()

    def selection(self, kind):
        self.kind = kind
        position = self._position()
        if position != self.position:
            if self.last_index is not None and self.position is not None and position[:2] == self.position[:2]:
                self.direction = 1 if position[2] >= self.last_index else -1
            self.last_index = position[2]
            self._stop_work()
            self.position, self.plan = position, None

    def pause(self):
        self.paused = True
        self.invalidate()

    def resume(self):
        if not self.closed:
            self.paused = False
            self.update(self.kind)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.invalidate()
        self.reader.close()
        self.screen = None

    def _size(self, name):
        try:
            size = self.screen[name].instance.size()
            return int(size.width()), int(size.height())
        except (KeyError, AttributeError, TypeError, RuntimeError):
            return None

    def card_ready(self, kind, slot, path):
        size = self._size("{}_tile_poster_{}".format(kind, slot))
        return bool(size and background.pixmap_prepared(path, size))

    def show_ready(self, kind):
        """Display completed provider/TMDb data without a navigation debounce."""
        self.kind = kind
        entries = self._field("entries", ())
        if not entries:
            return False
        item = entries[self._field("selected_index", 0)]
        info = self.api._detail_cache_get(self._field("info_cache"),
            self._field("info_cache_lock"), item.stream_id)
        if info is None or not getattr(info, "_vod_metadata_complete", False):
            return False
        for suffix in ("pending_detail_generation", "detail_preview", "detail_result"):
            setattr(self.screen, "_" + kind + "_" + suffix, None)
        setattr(self.screen, "_" + kind + "_detail_pending", False)
        url = self.api._safe_picon_url(getattr(info, "cover", "") or getattr(item, "icon", ""))
        path = self.api._cached_picon_path(url) if url else ""
        getattr(self.screen, "_apply_" + kind + "_detail")(
            info, path, "", url, getattr(item, "icon", ""))
        return True

    def _wide_urls(self, item, info):
        verified = getattr(info, "fallback_backdrop", "")
        ordered = ((verified, getattr(info, "backdrop", ""), getattr(item, "backdrop", ""))
                   if getattr(item, "portal_request_type", "") and verified else
                   (getattr(info, "backdrop", ""), verified, getattr(item, "backdrop", "")))
        urls = []
        for value in ordered:
            url = self.api._safe_picon_url(value)
            if url and url not in urls:
                urls.append(url)
        return tuple(urls)

    def _hero_key(self, kind, item, info):
        return (kind, self._field("loaded_category_id", ""), str(item.stream_id), self._wide_urls(item, info))

    def hero_ready(self, kind, item, info):
        key = self._hero_key(kind, item, info)
        if key not in self.heroes and key[-1]:
            # Completed detail can introduce the same verified URL that an
            # earlier artwork-only lookup prepared before provider data.
            key = key[:-1] + ((key[-1][0],),)
        path = self.heroes.get(key, "")
        if path and self.api._valid_cinematic_backdrop_file(path):
            self.heroes.move_to_end(key)
            return path
        self.heroes.pop(key, None)
        return ""

    def remember_hero(self, kind, item, candidates, path, resolved=""):
        if self.closed or self.paused or not path:
            return
        prefix = (kind, self._field("loaded_category_id", ""), str(item.stream_id))
        keys = [prefix + (tuple(candidates),)]
        if resolved:
            keys.append(prefix + ((resolved,),))
        for key in keys:
            self.heroes[key] = path
            self.heroes.move_to_end(key)
        while len(self.heroes) > 8:
            self.heroes.popitem(last=False)

    def show_hero(self, kind, item):
        """Attach prepared art immediately while text keeps its debounce."""
        self.kind = kind
        info = self.api._detail_cache_get(self._field("info_cache"),
            self._field("info_cache_lock"), item.stream_id)
        if not self.hero_ready(kind, item, info):
            return False
        return self.screen._start_cinematic_backdrop(kind, item, info)

    def _signature(self):
        entries = self._field("entries", ())
        page_size = self.screen._poster_page_size
        start = self._field("selected_index", 0) // page_size * page_size
        nearby = entries[max(0, start - page_size):start + page_size * 2]
        return (self._position(), self._field("window_base", 0), start,
                len(entries), tuple((str(item.stream_id), str(getattr(item, "icon", ""))) for item in nearby))

    def update(self, kind):
        self.kind = kind
        if self.closed or self.paused or getattr(self.screen, "_closed", False):
            return
        entries = self._field("entries", ())
        if not entries:
            self.invalidate()
            return
        self.selection(kind)
        signature = self._signature()
        if signature == self.plan:
            return
        self._stop_work()
        self.plan = signature
        size = self.screen._poster_page_size
        selected = self._field("selected_index", 0)
        start = selected // size * size
        # Recently displayed current/previous cards keep their LRU position;
        # prepared data borrows the existing pool only within its byte budget.
        page_key = (self._position()[:2], self._field("window_base", 0) + start)
        if page_key != self.touched_page:
            self.touched_page = page_key
            for index in range(max(0, start - size), min(start + size, len(entries))):
                item = entries[index]
                url = self.screen._vod_card_url(getattr(item, "icon", ""))
                path = self.api._cached_picon_path(url) if url else ""
                if path:
                    self.card_ready(kind, index % size, path)
        following = entries[start + size:start + size * 2]
        # Hero work runs first, but it cannot consume the small-card share of
        # this plan's existing decode allowance. Disk preparation still runs
        # when a further large native surface would exceed that allowance.
        for slot, item in enumerate(following):
            card_size = self._size("{}_tile_poster_{}".format(kind, slot))
            url = self.screen._vod_card_url(getattr(item, "icon", ""))
            path = self.api._cached_picon_path(url) if url else ""
            if card_size and not (path and self.card_ready(kind, slot, path)):
                self.card_bytes_reserved += card_size[0] * card_size[1] * 4
        info_items = []
        if getattr(self.screen, "_cinematic_enabled", False):
            for distance in (1, 2):
                target = selected + self.direction * distance
                if 0 <= target < len(entries):
                    info_items.append(entries[target])
        if following:
            info_items.append(following[0])
        seen, targets = set(), []
        for item in info_items:
            identity = str(item.stream_id)
            if identity not in seen:
                seen.add(identity)
                targets.append(item)
        if getattr(self.screen, "_cinematic_enabled", False):
            for item in targets:
                self.jobs.append(("hero", 0, item))
        for slot, item in enumerate(following):
            self.jobs.append(("poster", slot, item))
        for item in targets:
            self.jobs.append(("info", 0, item))
        self.timer.start(180, True)

    def _busy(self, artwork=False):
        if (getattr(self.screen, "_closed", False) or getattr(self.screen, "_vod_work_suspended", False)
                or self._field("search_child_open", False) or getattr(self.screen, "_category_manager_child_open", False)):
            return True
        fields = ("loading", "more_loading") if artwork else ("loading", "detail_pending", "poster_pending", "more_loading")
        if any(self._field(name, False) for name in fields):
            return True
        if self._field("pending_category_id") is not None:
            return True
        if getattr(self.screen, "_cinematic_pending", False):
            return True
        for key, loader in getattr(self.screen, "_gt_pixmap_loaders", {}).items():
            if (str(key).startswith(self.kind + "-tile-") or key == "cinematic-backdrop") and getattr(loader, "picload", None) is not None:
                return True
        return False

    def poll(self):
        if self.closed or self.paused:
            return
        if self._signature() != self.plan:
            self.update(self.kind)
            return
        if self.decoder is not None and self.api.time.monotonic() >= self.decoder_deadline:
            decoder, self.decoder = self.decoder, None
            decoder.close()
        hero_work = bool((self.active is not None and self.active[0] == "hero")
                         or (self.active is None and self.jobs and self.jobs[0][0] == "hero")
                         or (self.images and self.images[0][2] is not None))
        if self._busy(artwork=hero_work):
            if self.active is not None:
                self.jobs.appendleft(self.active)
                self.active = None
                if self.token is not None:
                    self.token.cancel()
                self.token = None
                self.reader.cancel()
            if self.decoder is not None:
                decoder, self.decoder = self.decoder, None
                decoder.close()
            self.timer.start(150, True)
            return
        if self.images and self.decoder is None:
            self._start_image()
        if self.active is None and self.jobs and not self._busy(artwork=self.jobs[0][0] == "hero"):
            job = self.active = self.jobs.popleft()
            token = self.token = self.api._WorkerToken(8.0)
            epoch, kind = self.epoch, self.kind
            self.reader.request(lambda: (epoch, kind, job, token, self._read(kind, job, token)))
        size = self.screen._poster_page_size
        start = self._field("selected_index", 0) // size * size
        more_key = (self._position()[:2], self._field("window_base", 0) + start,
                    len(self._field("entries", ())))
        if (not self._busy() and not self.jobs and self.active is None and self._field("has_more", False)
                and len(self._field("entries", ())) < start + size * 2
                and self.more_requested != more_key):
            self.more_requested = more_key
            getattr(self.screen, "_start_" + self.kind + "_more_load")()
            self.timer.start(150, True)
        elif self.jobs or self.active is not None or self.images or self.decoder is not None:
            self.timer.start(60, True)

    def _read(self, kind, job, token):
        stage, slot, item = job
        token.check()
        if stage in ("poster", "recover"):
            url = self.screen._vod_card_url(getattr(item, "icon", ""))
            path = self.api._cached_picon_path(url) if url else ""
            if not path and url and stage == "poster":
                path = self.screen._fetch_vod_card(url, token)
            resolved = url
            if not path and stage == "recover":
                path, resolved = self.screen._recover_vod_card(kind, item, url, token)
            token.check()
            return (path, resolved, url, None, ())
        info = self.api._detail_cache_get(getattr(self.screen, "_" + kind + "_info_cache"),
            getattr(self.screen, "_" + kind + "_info_cache_lock"), item.stream_id)
        if stage == "hero":
            # Catalogue/cached data is enough to find art. In particular, do
            # not occupy a Stalker provider lane with speculative summaries.
            candidates = self._wide_urls(item, info)
            urls, resolved = list(candidates), ""
            metadata = self.screen._metadata_client
            resolver = getattr(metadata, "cinematic_backdrop", None)
            if callable(resolver) and (not urls or (getattr(item, "portal_request_type", "")
                                                  and not getattr(info, "fallback_backdrop", ""))):
                try:
                    resolved = self.api._safe_picon_url(self.api._bounded_client_call(
                        metadata, token, lambda: resolver(kind, item, info)))
                except Exception:
                    token.check()
                if resolved:
                    urls = [resolved] + [url for url in urls if url != resolved]
            path, selected_url = "", ""
            for url in urls:
                token.check()
                candidate = self.api._cached_picon_path(url) or self.screen._fetch_vod_card(url, token)
                if candidate and self.api._valid_cinematic_backdrop_file(candidate):
                    path, selected_url = candidate, url
                    break
            token.check()
            return ("", selected_url, candidates, None, (path,) if path else ())
        if info is None or not getattr(info, "_vod_metadata_complete", False):
            info = copy(self.screen._vod_poster_info(kind, item, token))
            metadata = self.screen._metadata_client
            method = getattr(metadata, "complete_" + kind + "_cinematic", None) if getattr(self.screen, "_cinematic_enabled", False) else None
            if not callable(method):
                method = getattr(metadata, "complete_" + kind)
            info = self.api._bounded_client_call(metadata, token, lambda: method(item, info))
            if info is not None:
                info._vod_metadata_complete = True
                if getattr(self.screen, "_poster_grid_enabled", False):
                    info._grid_metadata_complete = True
        token.check()
        paths = []
        if getattr(self.screen, "_cinematic_enabled", False):
            for url in self._wide_urls(item, info):
                path = self.api._cached_picon_path(url)
                if not path:
                    path = self.screen._fetch_vod_card(url, token)
                if path and self.api._valid_cinematic_backdrop_file(path):
                    paths.append(path)
                    break
        token.check()
        return ("", "", "", info, tuple(paths))

    def _read_done(self, value, error):
        if self.closed or self.paused:
            return
        if error is None and value is not None:
            epoch, kind, original_job, token, result = value
            if epoch is not self.epoch or kind != self.kind or self.active is not original_job:
                return
            self.active = None
            if not token.active():
                self.token = None
                self.timer.start(40, True)
                return
            path, resolved, requested, info, heroes = result
            stage, slot, item = original_job
            if info is not None:
                self.api._detail_cache_put(self._field("info_cache"), self._field("info_cache_lock"), item.stream_id, info)
            if path:
                current = self.screen._vod_card_url(getattr(item, "icon", ""))
                if current in ("", requested, resolved):
                    if resolved:
                        item.icon = resolved
                        self.screen._remember_vod_card_size(resolved)
                        self.plan = self._signature()
                    self.images.append((path, self._size("{}_tile_poster_{}".format(kind, slot)), None))
            elif stage == "poster":
                self.jobs.append(("recover", slot, item))
            for path in heroes:
                candidates = requested if stage == "hero" else self._wide_urls(item, info)
                self.remember_hero(kind, item, candidates, path, resolved)
                self.images.appendleft((path, self._size(kind + "_backdrop"), self.api._cinematic_cover_ratio(path)))
        else:
            self.active = None
        self.token = None
        self.timer.start(40, True)

    def _start_image(self):
        path, size, ratio = self.images.popleft()
        if size is None:
            return
        if ratio is None:
            self.card_bytes_reserved = max(0, self.card_bytes_reserved - size[0] * size[1] * 4)
        try:
            key = background.prepared_pixmap_key(path, size, ratio)
            if key is None or background.pixmap_prepared(path, size, ratio):
                return
            cost = key[1] * key[2] * 4
            budget = min(48 * 1024 * 1024, background._BACKGROUND_CACHE_BUDGET_BYTES[0] // 2)
            reserved = self.card_bytes_reserved if ratio is not None else 0
            if self.decode_bytes + cost + reserved > budget:
                return
            epoch = self.epoch
            def done(success):
                if epoch is self.epoch and not self.closed and not self.paused:
                    self.decoder = None
                    self.timer.start(40, True)
            self.decode_bytes += cost
            decoder = self.decoder = background.PreparedPixmapLoader(path, size, cover_ratio=ratio, on_loaded=done)
            self.decoder_deadline = self.api.time.monotonic() + 2.5
            decoder.start()
        except (OSError, AttributeError, TypeError, ValueError, RuntimeError):
            self.decoder = None

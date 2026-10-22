# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""One cancellable catalogue producer for a movie or series browser."""
import threading
from collections import OrderedDict
import time

from .content import ContentError
from .movie_catalog import MovieCatalogIndex


class _CatalogToken(object):
    def __init__(self):
        self.cancel_event = threading.Event()
        self.deadline = time.monotonic() + 30 * 60

    def check(self):
        if self.cancel_event.is_set():
            raise ContentError(reason=ContentError.CANCELLED)
        if time.monotonic() >= self.deadline:
            raise ContentError(reason=ContentError.DEADLINE)


class MovieBrowseCatalog(object):
    def __init__(self, client, index=None):
        self.index = index or MovieCatalogIndex(client)
        self.condition = threading.Condition()
        self._state = dict(preparing=False, complete=False, movies=0, reason="")
        self._token = None
        self._started = False
        self._generation = 0
        self.idle = threading.Event()
        self.idle.set()
        self._positions = OrderedDict()
        self._workers = 0
        self._closed = False
        self._position_epoch = None

    @property
    def state(self):
        with self.condition:
            return dict(self._state)

    def start(self, force=False, preferred_category=None):
        with self.condition:
            if self._closed:
                return
            if preferred_category is not None:
                self.index.preferred_category = str(preferred_category or "")
            if self._started and not force:
                return
            if self._token is not None:
                self._token.cancel_event.set()
            self._generation += 1
            generation = self._generation
            token = self._token = _CatalogToken()
            self._started = True
            self._state = dict(self._state, preparing=True, complete=False, reason="")
            if force:
                self._positions.clear()
            self._workers += 1
            self.idle.clear()
            worker = threading.Thread(target=self._run, args=(generation, token, force, self.index))
            worker.daemon = True
            try:
                worker.start()
            except (RuntimeError, OSError):
                self._workers -= 1
                self._state.update(preparing=False, reason="storage")
                if not self._workers:
                    self.idle.set()
            self.condition.notify_all()

    def _run(self, generation, token, force, index):
        def progress(state):
            with self.condition:
                if generation == self._generation:
                    self._state = dict(state)
                    self.condition.notify_all()
        try:
            state = index.prepare(token, callback=progress, force=force)
            progress(dict(state, preparing=False))
        except Exception:
            progress(dict(self.state, preparing=False, complete=False, reason="storage"))
        finally:
            with self.condition:
                self._workers = max(0, self._workers - 1)
                if not self._workers:
                    self.idle.set()
                self.condition.notify_all()

    def stop(self):
        with self.condition:
            if self._token is not None:
                self._token.cancel_event.set()
            self._generation += 1
            self._started = False
            self._state.update(preparing=False)
            self.condition.notify_all()

    def close(self):
        """Final screen exit; playback/search pauses continue to use stop()."""
        with self.condition:
            self._closed = True
            self.stop()
            self._positions.clear()
        self.index.close()

    def read_window(self, category_id="", offset=0, size=240, hidden_category_ids=(), token=None,
                    _generation_retry=0):
        """Hydrate a bounded GUI/player window using small cursor checkpoints.

        This also works while the producer is suspended for video playback.
        Only integer cursors are retained for previously visited catalogue pages.
        """
        scope = (str(category_id or ""), frozenset(hidden_category_ids),
                 hasattr(hidden_category_ids, "allowed"),
                 frozenset(getattr(hidden_category_ids, "allowed", ())))
        offset, size = max(0, int(offset)), max(1, min(280, int(size)))
        index = self.index
        epoch = index.state().get("generation")
        with self.condition:
            if epoch != self._position_epoch:
                self._positions.clear()
                self._position_epoch = epoch
            checkpoints = self._positions.setdefault(scope, {0: 0})
            self._positions.move_to_end(scope)
            while len(self._positions) > 8:
                self._positions.popitem(last=False)
            position = max((value for value in checkpoints if value <= offset), default=0)
            cursor = checkpoints[position]
        selected, more = [], True
        while more and len(selected) < size:
            if token is not None:
                token.check()
            page = index.browse(category_id, after=cursor, page_size=min(80, offset + size - position),
                hidden_category_ids=hidden_category_ids, token=token, include_total=False)
            page_epoch = getattr(page, "catalog_state", {}).get("generation")
            if epoch is not None and page_epoch != epoch:
                with self.condition:
                    self._positions.clear()
                    self._position_epoch = page_epoch
                if _generation_retry >= 2:
                    raise ContentError(reason=ContentError.CANCELLED)
                return self.read_window(category_id, offset, size, hidden_category_ids, token,
                                        _generation_retry + 1)
            rows = list(page.items)
            take = max(0, offset - position)
            selected.extend(rows[take:])
            position += len(rows)
            old_cursor, cursor = cursor, page.next_cursor
            more = bool(page.has_more)
            if rows:
                with self.condition:
                    checkpoints[position] = cursor
            if not rows or cursor == old_cursor:
                break
            # Windows end on a natural backend boundary; never advance a
            # cursor past rows that were not handed to the UI.
            if len(selected) >= size:
                break
        if index is not self.index:
            raise ContentError(reason=ContentError.CANCELLED)
        page.items = selected
        page.next_cursor = cursor
        page.has_more = more
        page.catalog_state = self.state
        return page

    def read(self, category_id="", after=0, hidden_category_ids=(), token=None):
        # This method runs in the existing UI worker. It waits for useful rows
        # rather than returning empty pages that would make the GUI busy-poll.
        with self.condition:
            index, generation = self.index, self._generation
        while True:
            if token is not None:
                token.check()
            with self.condition:
                # A paused/closed screen must not reopen a cache or wait for
                # a producer that was cancelled by EXIT or BLUE.
                if not self._started or generation != self._generation:
                    raise ContentError(reason=ContentError.CANCELLED)
            result = index.browse(category_id, after=after, page_size=80,
                hidden_category_ids=hidden_category_ids, token=token,
                include_total=not self.state.get("preparing"))
            with self.condition:
                if generation != self._generation:
                    raise ContentError(reason=ContentError.CANCELLED)
                preparing = bool(self._state.get("preparing"))
                if result.items or not preparing:
                    result.has_more = result.has_more or preparing
                    result.catalog_state = dict(self._state)
                    scope = (str(category_id or ""), frozenset(hidden_category_ids),
                             hasattr(hidden_category_ids, "allowed"),
                             frozenset(getattr(hidden_category_ids, "allowed", ())))
                    checkpoints = self._positions.setdefault(scope, {0: 0})
                    position = next((pos for pos, cursor in checkpoints.items() if cursor == after), None)
                    if position is not None:
                        checkpoints[position + len(result.items)] = result.next_cursor
                    self._positions.move_to_end(scope)
                    while len(self._positions) > 8:
                        self._positions.popitem(last=False)
                    return result
                self.condition.wait(0.25)

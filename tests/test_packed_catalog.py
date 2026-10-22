"""Search correctness and shared streaming lifecycle for large RAM catalogues."""
import ast
import builtins
from collections import deque
from contextlib import contextmanager
import os
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_memory_catalog as support
from gt_memory_test.content import ContentError, ContentItem, MovieSearchPage
from gt_memory_test import packed_catalog as packed
from gt_memory_test import stalker
from gt_memory_test.movie_browse import MovieBrowseCatalog

memory, catalog = support.memory, support.catalog


def forbidden(*args, **kwargs):
    raise AssertionError("catalogue filesystem access")


class PackedTests(unittest.TestCase):
    setUp = support.MemoryTests.setUp
    close_all = support.MemoryTests.close_all
    index = support.MemoryTests.index
    def test_search_inflates_only_result_blocks_and_reuses_match_positions(self):
        index = self.index(support.Client(count=20000))
        index.prepare()
        with patch.object(packed.zlib, "decompress", wraps=packed.zlib.decompress) as decoded:
            self.assertEqual(index.search("019999").items[0].stream_id, "19999")
            self.assertLessEqual(decoded.call_count, 1)
            self.assertEqual(index.search("no-such-name").total_items, 0)
            self.assertLessEqual(decoded.call_count, 1)
        session = index._memory.session
        index.search("Film", page=1)
        cached = next(value[0] for key, value in session.queries.items() if key[0] == (b"film",))
        index.search("Film", page=2)
        self.assertTrue(any(value[0] is cached for value in session.queries.values()))

    def test_every_compression_boundary_and_partial_tail_is_readable(self):
        for count in (127, 128, 129, 255, 256, 257):
            index = self.index(support.Client(str(count), count=count))
            index.prepare()
            for number in (1, min(128, count), count):
                page = index.search("%06d" % number)
                self.assertEqual(page.items[0].stream_id, str(number))
            index.close()

    def test_turkish_substrings_and_multiple_terms_do_not_cross_rows(self):
        client = support.Client(count=0)
        names = ("Cem İlk Çığlık", "CEM ORTA", "Cemal Son", "Yalnız Bir", "iki Sözcük")
        client.iter_movie_catalog_items = lambda category="": (
            ContentItem("movie", str(i), name, category_id="1") for i, name in enumerate(names, 1))
        index = self.index(client)
        index.prepare()
        self.assertEqual(index.search("cEm").total_items, 3)
        self.assertEqual(index.search("CEM çığlık").items[0].stream_id, "1")
        self.assertEqual(index.search("bir iki").total_items, 0)
        self.assertEqual(index.search("ilK").items[0].stream_id, "1")

    def test_duplicate_membership_updates_already_compressed_rows(self):
        client = support.Client(count=300)
        client.source_type = "stalker"
        client.load_categories = lambda kind: [SimpleNamespace(category_id=str(i), name=str(i)) for i in (1, 2)]
        original = client.iter_movie_catalog_items
        def rows(category=""):
            for item in original(category):
                item.category_id = category
                yield item
        client.iter_movie_catalog_items = rows
        index = self.index(client)
        index.prepare()
        self.assertEqual(index.search("000001", hidden_category_ids=("2",)).total_items, 0)
        self.assertEqual(index.browse("2").total_items, 300)
        self.assertEqual(set(index.browse("2").items[0].portal_category_ids), {"1", "2"})

    def test_read_cache_and_query_cache_share_the_budget_and_are_bounded(self):
        index = self.index(support.Client(count=2000))
        index.prepare()
        for query in ("Film", "000", "001", "002", "003", "004", "005"):
            index.search(query)
        self.assertLessEqual(len(index._memory.session.queries), 4)
        self.assertLessEqual(len(index._memory.session.active.rows.cache), 2)
        self.assertEqual(index.state()["memory_bytes"], memory.budget.used)

    def test_shared_reader_follows_existing_producer_without_another_download(self):
        client = support.Client(count=300)
        waiting, finish = threading.Event(), threading.Event()
        original = client.iter_movie_catalog_items
        def rows(category=""):
            for item in original(category):
                if item.stream_id == "150":
                    waiting.set()
                    if not finish.wait(5):
                        raise AssertionError("producer timeout")
                yield item
        client.iter_movie_catalog_items = rows
        first, second = self.index(client), self.index(client)
        received, follower_done = threading.Event(), threading.Event()
        producer = threading.Thread(target=first.prepare)
        follower = threading.Thread(target=lambda: (second.prepare(callback=lambda state: received.set()), follower_done.set()))
        producer.start()
        try:
            self.assertTrue(waiting.wait(3))
            follower.start()
            self.assertTrue(received.wait(1))
            self.assertEqual(second.search("000100").items[0].stream_id, "100")
            self.assertFalse(follower_done.is_set())
        finally:
            finish.set()
            producer.join(3)
            follower.join(3)
        self.assertTrue(follower_done.is_set())
        self.assertEqual(client.calls, 1)
        self.assertEqual(second.browse().total_items, 300)

    def test_stalker_selected_category_loads_before_other_categories(self):
        client = support.Client(count=0)
        client.source_type = "stalker"
        client.load_categories = lambda kind: [SimpleNamespace(category_id=str(i), name=str(i)) for i in (1, 2)]
        seen = []
        def rows(category=""):
            seen.append(category)
            yield ContentItem("movie", category, "Film " + category, category_id=category)
        client.iter_movie_catalog_items = rows
        index = self.index(client)
        index.preferred_category = "2"
        self.assertTrue(index.prepare()["complete"])
        self.assertEqual(seen, ["2", "1"])

    def test_shared_reader_category_change_preempts_at_a_page_boundary(self):
        client = support.Client(count=0)
        client.source_type = "stalker"
        client.load_categories = lambda kind: [SimpleNamespace(category_id=str(i), name=str(i)) for i in (1, 2)]
        ready, resume = threading.Event(), threading.Event()
        finished = []
        def rows(category=""):
            for number in range(1, 601):
                if category == "1" and number == 250:
                    ready.set()
                    if not resume.wait(3):
                        raise AssertionError("priority test timed out")
                yield ContentItem("movie", str(int(category) * 1000 + number),
                                  "Film %d" % number, category_id=category)
            finished.append(category)
        client.iter_movie_catalog_items = rows
        first, second = self.index(client), self.index(client)
        first.preferred_category = "1"
        worker = threading.Thread(target=first.prepare)
        worker.start()
        try:
            self.assertTrue(ready.wait(2))
            second.preferred_category = "2"
            second.state()
        finally:
            resume.set()
            worker.join(5)
        self.assertFalse(worker.is_alive())
        self.assertEqual(finished, ["2", "1"])
        self.assertEqual(first.browse().total_items, 1200)

    def test_browser_constructor_selects_shared_index_for_both_portal_and_xtream(self):
        source = ast.parse((support.PLUGIN / "browser.py").read_text(encoding="utf-8"))
        selections = []
        for node in ast.walk(source):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.IfExp):
                if any(isinstance(target, ast.Attribute) and target.attr in (
                        "_movie_browser_catalog", "_series_browser_catalog") for target in node.targets):
                    selections.append(node.value.test)
        self.assertEqual(len(selections), 2)
        for test in selections:
            code = compile(ast.Expression(body=test), "catalogue-selection", "eval")
            self.assertTrue(eval(code, {"source_type": "stalker"}))
            self.assertTrue(eval(code, {"source_type": "xtream"}))
            self.assertFalse(eval(code, {"source_type": "m3u"}))

    def test_cold_stalker_search_publishes_ram_then_supported_provider_result(self):
        source = ast.parse((support.PLUGIN / "browser.py").read_text(encoding="utf-8"))
        screen = next(node for node in source.body if isinstance(node, ast.ClassDef) and node.name == "GTAllMoviesSearchScreen")
        worker = next(node for node in screen.body if isinstance(node, ast.FunctionDef) and node.name == "_search_page_worker")
        poll = next(node for node in screen.body if isinstance(node, ast.FunctionDef) and node.name == "_poll_search")
        class Cancelled(Exception):
            pass
        namespace = dict(time=time, _WorkerCancelled=Cancelled, ContentError=ContentError,
            CatalogStorageError=catalog.CatalogStorageError, CatalogLimitError=catalog.CatalogLimitError)
        exec(compile(ast.Module(body=[worker, poll], type_ignores=[]), "cold-search", "exec"), namespace)
        ram = MovieSearchPage([], source="catalog")
        ram.catalog_state = {"complete": False}
        events = []
        @contextmanager
        def scope(**kwargs):
            yield
        client = SimpleNamespace(source_type="stalker", request_scope=scope,
            search_movies=lambda *args, **kwargs: MovieSearchPage([ContentItem("movie", "5", "Cem")], source="portal"))
        owner = SimpleNamespace(_catalog_mode=True, content_type="movie", provider_page_size=14,
            _hidden_category_ids=(), _catalog_index=SimpleNamespace(search=lambda *args, **kwargs: ram),
            client=client, _queue_search_event=events.append, _provider_worker_finished=lambda: None)
        token = SimpleNamespace(check=lambda: None, deadline=time.monotonic() + 10, cancel_event=threading.Event())
        namespace["_search_page_worker"](owner, 1, "cem", 1, token)
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0][3].source, "catalog")
        self.assertEqual(events[1][3].items[0].name, "Cem")
        applied = []
        owner._closed, owner._event_lock, owner._events = False, threading.Lock(), deque(events)
        owner._search_generation, owner._requested_page, owner._current_page = 1, 1, 1
        owner._loading, owner._search_token, owner._pending_page_index = True, token, 0
        owner._apply_search_page = lambda page, *args: applied.append(page.source)
        owner._catalog_mailbox, owner._catalog_generation = None, 0
        owner._search_reason, owner._query_key = "", ""
        owner._catalog_refresh_pending, owner._catalog_preparing = False, False
        owner._render = lambda: None
        namespace["_poll_search"](owner)
        self.assertEqual(applied, ["catalog", "portal"])

    def test_evicted_stalker_details_restore_one_page_beyond_old_page_limits(self):
        account = stalker.PortalAccount("http://details.invalid/c/", "00:1A:79:00:00:07")
        client = stalker.StalkerPortalClient(account, opener=SimpleNamespace(open=forbidden))
        for kind in ("movie", "series"):
            identity = "200000" if kind == "movie" else "123:200000"
            calls = []
            def request(request_type, action, parameters=()):
                calls.append(dict(parameters)["p"])
                return {"data": [dict(id=identity, series_id=identity, name="Cem Son", description="Details")]}
            client._request = request
            item = ContentItem(kind, identity, "Cem Son", category_id="1")
            item.portal_catalog_page = 22000
            item.portal_request_type = "vod" if kind == "movie" else "series"
            client.remember_catalog_item(item)
            entry = client._cached_media_entry(kind, identity)
            self.assertEqual(entry["description"], "Details")
            self.assertEqual(calls, ["22000"])
            self.assertFalse(client._vod_catalog_states)

    def test_signed_movie_playback_recovers_its_indexed_page_after_lru_eviction(self):
        account = stalker.PortalAccount("http://playback.invalid/c/", "00:1A:79:00:00:08")
        client = stalker.StalkerPortalClient(account, opener=SimpleNamespace(open=forbidden))
        calls = []
        def request(request_type, action, parameters=()):
            calls.append(dict(parameters)["p"])
            return {"data": [dict(id="200000", name="Cem Son", cmd="http://playback.invalid/video?token=fresh")]}
        client._request = request
        item = ContentItem("movie", "200000", "Cem Son", category_id="1")
        item.portal_catalog_page = 22000
        item.portal_request_type = "vod"
        restored = client._movie_playback_item(item)
        self.assertEqual(restored.stream_id, "200000")
        self.assertEqual(calls, ["22000"])
        self.assertFalse(client._vod_catalog_states)


if __name__ == "__main__":
    unittest.main()

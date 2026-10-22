# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Run the actual RAM-only catalogue without filesystem or flock adapters."""
import ast
import builtins
import gc
import importlib.util
import os
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import mock_open, patch


PLUGIN = Path(__file__).resolve().parents[1] / "GTIPTVPlayerPro"
SPEC = importlib.util.spec_from_file_location("gt_memory_test", PLUGIN / "__init__.py",
                                            submodule_search_locations=[str(PLUGIN)])
PACKAGE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = PACKAGE
SPEC.loader.exec_module(PACKAGE)
if os.name == "nt":
    # Existing category-settings helpers import fcntl at module load. The RAM
    # catalogue must never call that receiver-only settings lock in these tests.
    def forbidden_flock(*args):
        raise AssertionError("RAM catalogue used a file lock")
    sys.modules.setdefault("fcntl", SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8,
                                                   flock=forbidden_flock))
from gt_memory_test import memory_catalog as memory
from gt_memory_test import movie_catalog as catalog
from gt_memory_test.category_visibility import CategoryVisibility
from gt_memory_test.content import ContentError, ContentItem
from gt_memory_test.movie_browse import MovieBrowseCatalog


class Client:
    source_type = "xtream"

    def __init__(self, name="one", count=1200):
        self.account = SimpleNamespace(identity=("https", "example.invalid", name))
        self.count, self.calls = count, 0

    def iter_movie_catalog_items(self, category=""):
        self.calls += 1
        for number in range(1, self.count + 1):
            yield ContentItem("movie", str(number), "Film %06d" % number,
                              category_id=str(number % 3 + 1), year="2026")

    def iter_series_catalog_items(self, category=""):
        for item in self.iter_movie_catalog_items(category):
            item.content_type = "series"
            yield item


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.indices = []
        self.guard = patch.object(memory, "available_memory", return_value=512 * 1024 * 1024)
        self.guard.start()
        self.addCleanup(self.guard.stop)
        memory.budget.checked = 0
        self.addCleanup(self.close_all)

    def close_all(self):
        for index in self.indices:
            index.close()
        self.indices.clear()
        self.assertEqual(memory.budget.used, 0)

    def index(self, client=None, series=False, **kwargs):
        index = (catalog.SeriesCatalogIndex if series else catalog.MovieCatalogIndex)(
            client or Client(), directory="unused-test-path", **kwargs)
        self.indices.append(index)
        return index

    def test_browse_search_category_and_continuation(self):
        index = self.index()
        state = index.prepare()
        self.assertTrue(state["complete"])
        self.assertEqual(state["movies"], 1200)
        self.assertEqual(state["backend"], "memory")
        page = index.browse(page_size=80)
        self.assertEqual(len(page.items), 80)
        self.assertEqual(index.browse(after=page.next_cursor).items[0].stream_id, "81")
        category = index.browse("2")
        self.assertEqual(category.total_items, 400)
        self.assertTrue(all(item.category_id == "2" for item in category.items))
        self.assertEqual(index.search("000999").items[0].stream_id, "999")

    def test_catalogue_has_no_file_writer_or_sqlite_dependency(self):
        index = self.index(Client(count=100))
        real_import = builtins.__import__
        def imported(name, *args, **kwargs):
            if name in ("sqlite3", "tempfile", "fcntl"):
                raise AssertionError("catalogue imported " + name)
            return real_import(name, *args, **kwargs)
        with (patch("builtins.open", side_effect=AssertionError("catalogue opened a file")),
              patch.object(os, "open", side_effect=AssertionError("catalogue opened a descriptor")),
              patch("builtins.__import__", imported)):
            self.assertTrue(index.prepare()["complete"])
            self.assertTrue(index.search("Film").items)
            self.assertTrue(index.browse("2").items)
            self.assertTrue(index.prepare(force=True)["complete"])
            index.close()

    def test_last_reader_releases_memory_immediately(self):
        index = self.index(Client(count=500))
        index.prepare()
        session = index._memory.session
        self.assertGreater(memory.budget.used, 0)
        index.close()
        self.assertEqual(memory.budget.used, 0)
        self.assertTrue(session.closed)
        self.assertIsNone(session.active)

    def test_shared_readers_retain_only_one_catalogue(self):
        client = Client(count=300)
        one, two = self.index(client), self.index(client)
        one.prepare()
        two.prepare()
        self.assertIs(one._memory.session, two._memory.session)
        self.assertEqual(client.calls, 1)
        weight = memory.budget.used
        one.close()
        self.assertEqual(memory.budget.used, weight)
        self.assertTrue(two.browse().items)
        two.close()
        self.assertEqual(memory.budget.used, 0)

    def test_gc_is_a_fallback_for_unclosed_index(self):
        index = catalog.MovieCatalogIndex(Client(count=10), directory="unused")
        index.prepare()
        self.assertGreater(memory.budget.used, 0)
        del index
        gc.collect()
        self.assertEqual(memory.budget.used, 0)

    def test_close_is_idempotent_and_does_not_reopen(self):
        index = self.index()
        index.prepare()
        index.close()
        index.close()
        with self.assertRaises(ContentError):
            index.browse()
        self.assertEqual(memory.budget.used, 0)

    def test_account_and_content_type_are_isolated(self):
        movie = self.index(Client("same", count=100))
        series = self.index(Client("same", count=40), series=True)
        other = self.index(Client("other", count=20))
        for index in (movie, series, other):
            self.assertTrue(index.prepare()["complete"])
        self.assertEqual(movie.browse().total_items, 100)
        self.assertEqual(series.browse().total_items, 40)
        self.assertEqual(other.browse().total_items, 20)
        self.assertEqual(series.browse().items[0].content_type, "series")

    def test_shared_ram_budget_covers_fifty_accounts_and_series(self):
        with patch.object(memory.budget, "maximum", 300000):
            reasons = []
            for number in range(50):
                index = self.index(Client(str(number), count=50), series=bool(number % 2))
                reasons.append(index.prepare()["reason"])
                self.assertLessEqual(memory.budget.used, 300000)
            self.assertIn("limit", reasons)

    def test_partial_result_survives_per_catalogue_memory_limit(self):
        index = self.index(Client(count=2000), max_bytes=65536)
        state = index.prepare()
        self.assertEqual(state["reason"], "limit")
        self.assertEqual(state["limit_kind"], "catalog-memory")
        self.assertGreater(state["movies"], 0)
        self.assertLessEqual(state["memory_bytes"], 65536)
        self.assertEqual(index.browse().total_items, state["movies"])

    def test_retry_reclaims_old_partial_generation_before_replacement(self):
        index = self.index(Client(count=2000), max_bytes=65536)
        old = index.prepare()
        fresh = index.prepare(force=True)
        self.assertEqual(fresh["reason"], "limit")
        self.assertEqual(fresh["movies"], old["movies"])
        self.assertGreater(fresh["generation"], old["generation"])
        self.assertLessEqual(fresh["memory_bytes"], 65536)

    def test_available_ram_guard_preserves_previous_complete_rows(self):
        index = self.index(Client(count=100))
        index.prepare()
        with patch.object(memory, "available_memory", return_value=memory.RAM_RESERVE_BYTES - 1):
            state = index.prepare(force=True)
            self.assertEqual(state["reason"], "limit")
            self.assertEqual(state["limit_kind"], "available-memory")
            with self.assertRaises(catalog.CatalogLimitError):
                index.search("Film")
        self.assertEqual(index.browse().total_items, 100)

    def test_item_limit_is_reported_separately(self):
        index = self.index(Client(count=100), max_items=25)
        state = index.prepare()
        self.assertEqual(state["movies"], 25)
        self.assertEqual(state["limit_kind"], "item-count")

    def test_hidden_and_positive_category_filters(self):
        index = self.index(Client(count=120))
        index.prepare()
        self.assertEqual(index.search("Film", hidden_category_ids=("2",)).total_items, 80)
        visibility = CategoryVisibility((), ("2",), ("1", "2", "3"), ())
        result = index.browse(hidden_category_ids=visibility)
        self.assertEqual(result.total_items, 40)
        self.assertTrue(all("2" in item.portal_category_ids for item in result.items))

    def test_duplicate_portal_memberships_are_merged(self):
        client = Client(count=0)
        client.source_type = "stalker"
        client.load_categories = lambda kind: [SimpleNamespace(category_id=str(n), name=str(n)) for n in (1, 2)]
        client.iter_movie_catalog_items = lambda category="": iter([ContentItem("movie", "1", "Same", category_id=category)])
        index = self.index(client)
        self.assertTrue(index.prepare()["complete"])
        self.assertEqual(index.browse().total_items, 1)
        self.assertEqual(set(index.browse().items[0].portal_category_ids), {"1", "2"})
        self.assertEqual(index.browse("2").total_items, 1)
        self.assertEqual(index.search("Same", hidden_category_ids=("2",)).total_items, 0)

    def test_stalker_composite_series_identity_is_preserved(self):
        client = Client(count=0)
        client.source_type = "stalker"
        client.load_categories = lambda kind: [SimpleNamespace(category_id="3", name="Shows")]
        client.iter_series_catalog_items = lambda category="": iter([ContentItem("series", "123:456", "Dizi", category_id="3")])
        index = self.index(client, series=True)
        self.assertTrue(index.prepare()["complete"])
        item = index.browse().items[0]
        self.assertEqual(item.stream_id, "123:456")
        self.assertEqual(item.portal_command, "")

    def test_refreshes_do_not_accumulate_generations(self):
        index = self.index(Client(count=250))
        index.prepare()
        used = memory.budget.used
        for _ in range(4):
            self.assertTrue(index.prepare(force=True)["complete"])
            self.assertEqual(memory.budget.used, used)
            self.assertIsNone(index._memory.session.work)

    def test_failed_refresh_keeps_previous_complete_catalogue(self):
        client = Client(count=50)
        index = self.index(client, max_bytes=65536)
        index.prepare()
        client.count = 2000
        self.assertEqual(index.prepare(force=True)["reason"], "limit")
        self.assertEqual(index.browse().total_items, 50)

    def test_cancellation_retains_complete_partial_rows(self):
        event = threading.Event()
        def checked():
            if event.is_set():
                raise ContentError(reason=ContentError.CANCELLED)
        token = SimpleNamespace(check=checked, cancel_event=event, deadline=None)
        client = Client(count=500)
        original = client.iter_movie_catalog_items
        def items(category=""):
            for item in original(category):
                if item.stream_id == "205":
                    event.set()
                yield item
        client.iter_movie_catalog_items = items
        index = self.index(client)
        self.assertEqual(index.prepare(token)["reason"], "cancelled")
        self.assertEqual(index.browse().total_items, 204)

    def test_rows_are_readable_while_network_producer_is_waiting(self):
        loaded, finish = threading.Event(), threading.Event()
        client = Client(count=100)
        original = client.iter_movie_catalog_items
        def items(category=""):
            for item in original(category):
                if item.stream_id == "6":
                    loaded.set()
                    if not finish.wait(5):
                        raise AssertionError("test producer timed out")
                yield item
        client.iter_movie_catalog_items = items
        index = self.index(client)
        worker = threading.Thread(target=index.prepare)
        worker.start()
        try:
            self.assertTrue(loaded.wait(3))
            self.assertEqual(index.browse().total_items, 5)
            index.close()
            self.assertEqual(memory.budget.used, 0)
        finally:
            finish.set()
            worker.join(3)
        self.assertFalse(worker.is_alive())
        self.assertEqual(memory.budget.used, 0)

    def test_browse_pause_preserves_catalogue_and_close_releases(self):
        index = self.index(Client(count=1000))
        index.prepare()
        browser = MovieBrowseCatalog(index.client, index=index)
        browser.stop()
        self.assertEqual(browser.read_window(offset=555, size=240).items[0].stream_id, "556")
        self.assertGreater(memory.budget.used, 0)
        browser.close()
        self.assertEqual(memory.budget.used, 0)
        browser.start()
        self.assertTrue(browser.idle.is_set())

    def test_player_window_cursors_reset_after_search_refresh(self):
        index = self.index(Client(count=1200))
        index.prepare()
        browser = MovieBrowseCatalog(index.client, index=index)
        browser.read_window(offset=0, size=240)
        self.assertTrue(index.prepare(force=True)["complete"])
        page = browser.read_window(offset=240, size=240)
        self.assertEqual(page.items[0].stream_id, "241")

    def test_window_does_not_mix_generations_published_between_pages(self):
        index = self.index(Client(count=1200))
        index.prepare()
        browser = MovieBrowseCatalog(index.client, index=index)
        original = index.browse
        calls = [0]
        def browse(*args, **kwargs):
            calls[0] += 1
            if calls[0] == 2:
                index.prepare(force=True)
            return original(*args, **kwargs)
        with patch.object(index, "browse", browse):
            page = browser.read_window(offset=160, size=240)
        self.assertEqual([item.stream_id for item in page.items],
                         [str(number) for number in range(161, 401)])

    def test_search_screen_exit_explicitly_releases_ram(self):
        source = ast.parse((PLUGIN / "browser.py").read_text(encoding="utf-8"))
        screen = next(node for node in source.body if isinstance(node, ast.ClassDef)
                      and node.name == "GTAllMoviesSearchScreen")
        stop = next(node for node in screen.body if isinstance(node, ast.FunctionDef) and node.name == "_stop")
        namespace = {"_cancel_worker_token": lambda token: None}
        exec(compile(ast.Module(body=[stop], type_ignores=[]), "<search screen exit>", "exec"), namespace)
        index = self.index(Client(count=100))
        index.prepare()
        fake = SimpleNamespace(_closed=False, _catalog_generation=0, _catalog_token=None,
            _event_lock=threading.Lock(), _catalog_index=index,
            _cancel_search_request=lambda **kwargs: None, _page_cache={}, _results=[object()],
            _keyboard_timer=SimpleNamespace(stop=lambda: None), _release_xtream_search_cache=lambda: None)
        namespace["_stop"](fake)
        self.assertEqual(memory.budget.used, 0)
        self.assertEqual(fake._results, [])


class MemoryReadingTests(unittest.TestCase):
    def test_memavailable_and_old_kernel_fallback(self):
        with patch("builtins.open", mock_open(read_data="MemAvailable: 40000 kB\n")):
            self.assertEqual(memory.available_memory(), 40000 * 1024)
        with patch("builtins.open", mock_open(read_data="MemFree: 200 kB\nCached: 200 kB\nBuffers: 100 kB\nShmem: 250 kB\n")):
            self.assertEqual(memory.available_memory(), 250 * 1024)


if __name__ == "__main__":
    unittest.main()

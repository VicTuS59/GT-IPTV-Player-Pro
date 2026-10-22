"""On-demand browsing regressions, using real clients and browser workers."""
import ast
import builtins
from contextlib import contextmanager
import io
import json
import os
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

import test_memory_catalog as support
import test_native_browse_memory as native_support
from gt_memory_test import catalog_cleanup, content, stalker
from gt_memory_test.category_browse import CategoryBrowseCatalog
from gt_memory_test.playlist import PlaylistAccount


class CategoryClient:
    source_type = "xtream"

    def __init__(self):
        self.account = SimpleNamespace(identity=("https", "category.invalid", "one"))
        self.calls = []
        self.local = threading.local()
        self.blocked = threading.Event()
        self.resume = threading.Event()
        self.cancelled = threading.Event()
        self.block_category = None
        self.empty_category = None
        self.error_category = None

    @contextmanager
    def request_scope(self, cancel_event=None, deadline=None):
        previous = getattr(self.local, "token", None)
        self.local.token = cancel_event
        try:
            yield
        finally:
            self.local.token = previous

    def load_categories(self, kind):
        raise AssertionError("category browsing started an account scan")

    def iter_movie_catalog_items(self, category=""):
        self.calls.append(("movie", category))
        if category == self.error_category:
            raise content.ContentError("Server failure", content.ContentError.CONNECTION)
        if category == self.empty_category:
            return
        try:
            for number in range(1, 601):
                event = getattr(self.local, "token", None)
                if event is not None and event.is_set():
                    self.cancelled.set()
                    raise content.ContentError(reason=content.ContentError.CANCELLED)
                if category == self.block_category and number == 251:
                    self.blocked.set()
                    while not self.resume.wait(0.01):
                        if event is not None and event.is_set():
                            self.cancelled.set()
                            raise content.ContentError(reason=content.ContentError.CANCELLED)
                yield content.ContentItem("movie", str(number), "Film %06d" % number,
                                          category_id=category or "7")
        finally:
            pass

    def iter_series_catalog_items(self, category=""):
        for item in self.iter_movie_catalog_items(category):
            item.content_type = "series"
            yield item


class CategoryBrowseTests(unittest.TestCase):
    def setUp(self):
        self.browsers = []
        self.clients = []
        for guard in (patch.object(support.memory, "available_memory", return_value=512 * 1024 * 1024),
                      patch.object(catalog_cleanup, "_done", True)):
            guard.start()
            self.addCleanup(guard.stop)
        support.memory.budget.checked = 0
        self.addCleanup(self.close_all)

    def close_all(self):
        for client in self.clients:
            if hasattr(client, "resume"):
                client.resume.set()
        for browser in self.browsers:
            browser.close()
        for browser in self.browsers:
            self.assertTrue(browser.idle.wait(5), "producer did not drain")
        self.assertEqual(support.memory.budget.used, 0)

    def browser(self, client=None, kind="movie"):
        client = client or CategoryClient()
        self.clients.append(client)
        browser = CategoryBrowseCatalog(client, content_type=kind)
        self.browsers.append(browser)
        return browser

    def complete(self, browser):
        self.assertTrue(browser.idle.wait(30))
        self.assertTrue(browser.state["complete"], browser.state)

    def test_no_selection_starts_no_request_and_allocates_no_rows(self):
        browser = self.browser()
        browser.start()
        browser.start(force=True)
        self.assertEqual(browser.client.calls, [])
        self.assertEqual(support.memory.budget.used, 0)

    def test_selected_category_only_no_file_io_and_no_global_search_session(self):
        for kind in ("movie", "series"):
            with self.subTest(kind=kind):
                browser = self.browser(kind=kind)
                with (patch.object(builtins, "open", native_support.forbidden),
                      patch.object(os, "open", native_support.forbidden)):
                    browser.start(preferred_category="42")
                    self.complete(browser)
                    page = browser.read("42")
                    self.assertEqual(len(page.items), 80)
                    self.assertEqual(page.items[0].content_type, kind)
                    self.assertTrue(all(item.category_id == "42" for item in page.items))
                    self.assertEqual(browser.client.calls, [("movie", "42")])
                    self.assertTrue(page.has_more)
                    browser.start()
                    self.complete(browser)
                    self.assertEqual(browser.client.calls, [("movie", "42")])
                    global_index = (support.catalog.SeriesCatalogIndex if kind == "series"
                                    else support.catalog.MovieCatalogIndex)(browser.client, directory="unused")
                    try:
                        self.assertEqual(global_index.state()["movies"], 0)
                        self.assertFalse(global_index.state()["complete"])
                    finally:
                        global_index.close()
                    browser.close()

    def test_first_page_is_available_while_the_category_is_still_loading(self):
        client = CategoryClient()
        client.block_category = "slow"
        browser = self.browser(client)
        browser.start(preferred_category="slow")
        self.assertTrue(client.blocked.wait(5))
        started = time.monotonic()
        page = browser.read("slow")
        self.assertLess(time.monotonic() - started, 1)
        self.assertEqual(page.items[0].stream_id, "1")
        self.assertEqual(len(page.items), 80)
        self.assertTrue(page.has_more)
        self.assertFalse(browser.idle.is_set())

    def test_switch_cancels_old_category_and_publishes_only_new_rows(self):
        client = CategoryClient()
        client.block_category = "old"
        browser = self.browser(client)
        browser.start(preferred_category="old")
        self.assertTrue(client.blocked.wait(5))
        old_index = browser.index
        browser.start(preferred_category="new")
        self.complete(browser)
        self.assertTrue(client.cancelled.wait(2))
        self.assertTrue(old_index._closed)
        self.assertEqual(browser.state["movies"], 600)
        self.assertTrue(all(row.category_id == "new" for row in browser.read("new").items))
        self.assertEqual(client.calls, [("movie", "old"), ("movie", "new")])
        with self.assertRaises(content.ContentError) as caught:
            browser.read("old")
        self.assertEqual(caught.exception.reason, content.ContentError.CANCELLED)

    def test_a_running_global_search_does_not_block_a_selected_category(self):
        client = CategoryClient()
        client.block_category = ""
        global_index = support.catalog.MovieCatalogIndex(client, directory="unused")
        worker = threading.Thread(target=global_index.prepare)
        worker.start()
        try:
            self.assertTrue(client.blocked.wait(5))
            browser = self.browser(client)
            browser.start(preferred_category="42")
            self.complete(browser)
            self.assertTrue(worker.is_alive(), "global search was not held open")
            self.assertEqual(browser.read("42").items[0].category_id, "42")
            self.assertEqual(browser.state["movies"], 600)
        finally:
            client.resume.set()
            worker.join(5)
            global_index.close()
        self.assertFalse(worker.is_alive())

    def test_first_page_timeout_stops_loading_and_cancels_the_category_producer(self):
        tree = ast.parse((support.PLUGIN / "browser.py").read_text(encoding="utf-8"))
        for kind in ("movie", "series"):
            with self.subTest(kind=kind):
                method = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
                              and node.name == "_poll_" + kind + "_result")
                namespace = dict(N_=lambda value: value,
                                 _cancel_worker_token=lambda token: token.cancel())
                exec(compile(ast.Module(body=[method], type_ignores=[]), "category-timeout", "exec"), namespace)
                client = CategoryClient()
                client.block_category = "42"
                browser = self.browser(client, kind)
                browser.start(preferred_category="42")
                self.assertTrue(client.blocked.wait(5))
                rendered = []
                prefix = "_" + kind + "_"
                owner = SimpleNamespace(_closed=False)
                token = SimpleNamespace(expired=lambda: True, cancel=lambda: None)
                for suffix, value in dict(browser_catalog=browser, index_reload_when_done=False,
                                          loading=True, ready=False, load_token=token,
                                          search_child_open=False).items():
                    setattr(owner, prefix + suffix, value)
                setattr(owner, "_update_" + kind + "_catalog_message", lambda: None)
                setattr(owner, "_render_" + kind + "_results", lambda: rendered.append(True))
                namespace["_poll_" + kind + "_result"](owner)
                self.assertFalse(getattr(owner, prefix + "loading"))
                self.assertIn("timed out", getattr(owner, prefix + "error"))
                self.assertEqual(rendered, [True])
                self.assertTrue(client.cancelled.wait(3))
                browser.close()

    def test_empty_category_finishes_without_global_fallback(self):
        client = CategoryClient()
        client.empty_category = "empty"
        browser = self.browser(client)
        browser.start(preferred_category="empty")
        self.complete(browser)
        page = browser.read("empty")
        self.assertEqual(page.items, [])
        self.assertFalse(page.has_more)
        self.assertEqual(client.calls, [("movie", "empty")])

    def test_error_finishes_and_does_not_scan_other_categories(self):
        client = CategoryClient()
        client.error_category = "broken"
        browser = self.browser(client)
        browser.start(preferred_category="broken")
        self.assertTrue(browser.idle.wait(5))
        self.assertFalse(browser.state["preparing"])
        with self.assertRaises(content.ContentError) as caught:
            browser.read("broken")
        self.assertEqual(caught.exception.reason, content.ContentError.CONNECTION)
        self.assertEqual(client.calls, [("movie", "broken")])

    def test_refresh_defers_work_until_selection_and_reloads_only_selected_category(self):
        browser = self.browser()
        browser.start(preferred_category="42")
        self.complete(browser)
        self.assertGreater(support.memory.budget.used, 0)
        browser.invalidate()
        self.assertEqual(support.memory.budget.used, 0)
        browser.start()
        self.assertEqual(len(browser.client.calls), 1)
        browser.start(preferred_category="42")
        self.complete(browser)
        self.assertEqual(browser.client.calls, [("movie", "42"), ("movie", "42")])

    def test_backward_windows_and_playback_pause_keep_selected_rows_readable(self):
        browser = self.browser()
        browser.start(preferred_category="42")
        self.complete(browser)
        page = browser.read_window("42", offset=320, size=240)
        self.assertEqual(page.items[0].stream_id, "321")
        browser.stop()
        self.assertEqual(browser.read_window("42", size=80).items[0].stream_id, "1")
        browser.start()
        self.complete(browser)
        self.assertEqual(len(browser.client.calls), 1)

    def test_real_xtream_uses_category_parameter_and_preserves_more_than_20k_items(self):
        for kind, action in (("movie", "get_vod_streams"), ("series", "get_series")):
            with self.subTest(kind=kind):
                requests = []
                key = "stream_id" if kind == "movie" else "series_id"
                payload = json.dumps([{key: n, "name": "Title %06d" % n, "category_id": "42"}
                                      for n in range(1, 21001)]).encode()
                def opened(request, timeout=None):
                    query = parse_qs(urlsplit(request.full_url).query)
                    self.assertEqual(query["action"], [action])
                    self.assertEqual(query["category_id"], ["42"])
                    requests.append(query)
                    return io.BytesIO(payload)
                account = PlaylistAccount("Audit", "http", "xtream.invalid", "", "user", "password", "ts")
                browser = self.browser(content.XtreamContentClient(account, opener=opened), kind)
                browser.start(preferred_category="42")
                self.complete(browser)
                self.assertEqual(browser.state["movies"], 21000)
                self.assertEqual(browser.read_window("42", offset=20990, size=80).items[-1].stream_id, "21000")
                self.assertEqual(len(requests), 1)
                browser.close()

    def test_real_stalker_only_requests_the_selected_category_for_movies_and_series(self):
        for kind in ("movie", "series"):
            with self.subTest(kind=kind):
                account = stalker.PortalAccount("http://portal-category.invalid/c/", "00:1A:79:00:00:12")
                client = stalker.StalkerPortalClient(account, opener=SimpleNamespace(open=native_support.forbidden))
                client._paced_catalog_wait = lambda: None
                requests = []
                def requested(request_type, action, parameters=()):
                    self.assertEqual(action, "get_ordered_list")
                    params = dict(parameters)
                    self.assertEqual(params.get("category", params.get("genre")), "42")
                    page = int(params.get("p", "1"))
                    requests.append((request_type, page))
                    rows = [dict(id=str(n), name="Film %06d" % n, category_id="42", cmd="/media/%d.mpg" % n)
                            for n in range((page - 1) * 100 + 1, min(page * 100, 250) + 1)] if page > 0 else []
                    if kind == "series":
                        for row in rows:
                            row.update(id="123:" + row["id"], series_id="123:" + row["id"], is_series="1", cmd="")
                    return dict(data=rows, total_items=250)
                client._request = requested
                browser = self.browser(client, kind)
                browser.start(preferred_category="42")
                self.complete(browser)
                self.assertEqual(browser.state["movies"], 250)
                expected = "123:1" if kind == "series" else "1"
                self.assertEqual(browser.read("42").items[0].stream_id, expected)
                self.assertGreaterEqual(len(requests), 3)
                browser.close()

    def test_browser_workers_publish_empty_and_error_results_and_ignore_stale_generations(self):
        methods = native_support.browser_methods()
        for kind in ("movie", "series"):
            for outcome in ("empty", "error", "stale"):
                with self.subTest(kind=kind, outcome=outcome):
                    client = CategoryClient()
                    if outcome == "empty":
                        client.empty_category = "42"
                    elif outcome == "error":
                        client.error_category = "42"
                    browser = self.browser(client, kind)
                    browser.start(preferred_category="42")
                    self.assertTrue(browser.idle.wait(5))
                    prefix = "_" + kind + "_"
                    owner = SimpleNamespace(client=client, _hidden_category_ids=(), _closed=False,
                        _client_has_more_items=lambda *args: False)
                    for suffix, value in dict(browser_catalog=browser, native_window=None,
                                              generation=2 if outcome == "stale" else 1,
                                              catalog_generation=1, window_base=0, ready=False).items():
                        setattr(owner, prefix + suffix, value)
                    methods["_load_" + kind + "_worker"](owner, 1, "42")
                    self.assertEqual(getattr(owner, prefix + "ready"), outcome != "stale")
                    if outcome != "stale":
                        self.assertEqual(getattr(owner, prefix + "entries"), [])
                        self.assertEqual(bool(getattr(owner, prefix + "error")), outcome == "error")
                    browser.close()


if __name__ == "__main__":
    unittest.main()

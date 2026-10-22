"""Exercise native portal paging through the browser and process RAM window."""
import ast
import builtins
import gc
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import test_memory_catalog as support
from gt_memory_test import stalker
from gt_memory_test.content import ContentError, ContentItem
from gt_memory_test.native_browse import NativeBrowseWindow

memory = support.memory


def forbidden(*args, **kwargs):
    raise AssertionError("native catalogue attempted filesystem I/O")


def items(start=1, count=100):
    for number in range(start, start + count):
        item = ContentItem("movie", str(number), "Film %06d" % number, category_id="1")
        item.portal_command = "/media/%d.mpg" % number
        item.portal_request_type = "vod"
        item.portal_category_ids = ("1", "2")
        yield item


def browser_methods():
    source = (support.PLUGIN / "browser.py").read_text(encoding="utf-8-sig")
    tree = ast.parse(source)
    wanted = {"_load_movie_worker", "_load_movie_more_worker", "_close_native_vod_windows",
              "_load_series_worker", "_load_series_more_worker"}
    methods = [node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name in wanted]
    assert len(methods) == len(wanted)
    class WorkerCancelled(Exception):
        pass
    namespace = dict(
        _bounded_client_call=lambda client, token, call: call(),
        category_filter_active=lambda hidden: bool(hidden),
        filter_hidden_category_items=lambda entries, *unused: entries,
        _WorkerCancelled=WorkerCancelled,
        ContentError=ContentError,
        CatalogStorageError=support.catalog.CatalogStorageError,
        CatalogLimitError=support.catalog.CatalogLimitError,
        N_=lambda value: value,
    )
    exec(compile(ast.Module(body=methods, type_ignores=[]), "browser-native-workers", "exec"), namespace)
    return namespace


class NativeMemoryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.methods = browser_methods()

    def setUp(self):
        self.windows = []
        guard = patch.object(memory, "available_memory", return_value=512 * 1024 * 1024)
        guard.start()
        self.addCleanup(guard.stop)
        memory.budget.checked = 0
        self.addCleanup(self.close_all)

    def close_all(self):
        for window in self.windows:
            window.close()
        self.windows.clear()
        self.assertEqual(memory.budget.used, 0)

    def window(self, **kwargs):
        window = NativeBrowseWindow("movie", **kwargs)
        self.windows.append(window)
        return window

    def test_native_page_storage_and_backward_reads_never_open_files(self):
        window = self.window()
        with patch.object(builtins, "open", forbidden), patch.object(os, "open", forbidden):
            self.assertEqual(len(window.append(items(count=900), True)), 900)
            page = window.read_window(offset=600, size=80)
            self.assertEqual(page.items[0].stream_id, "601")
            self.assertEqual(page.items[-1].stream_id, "680")
            self.assertEqual(page.page_size, 80)
            self.assertTrue(page.has_more)
            self.assertEqual(window.read_window(offset=0).items[0].portal_command, "/media/1.mpg")
            self.assertEqual(window.read_window(offset=0).items[0].portal_category_ids, ["1", "2"])
            window.close()
            self.assertEqual(memory.budget.used, 0)

    def test_real_stalker_normal_movie_browser_first_more_and_backward_pages(self):
        self.check_normal_stalker_browser("movie")

    def test_real_stalker_normal_series_browser_first_more_and_backward_pages(self):
        self.check_normal_stalker_browser("series")

    def check_normal_stalker_browser(self, kind):
        account = stalker.PortalAccount("http://audit.invalid/c/", "00:1A:79:00:00:02")
        client = stalker.StalkerPortalClient(account, opener=SimpleNamespace(open=forbidden))
        client._paced_catalog_wait = lambda: None
        requests = []
        def request(request_type, action, parameters=()):
            self.assertEqual(action, "get_ordered_list")
            page = int(dict(parameters).get("p", 1))
            requests.append(page)
            data = [dict(id=str(n), name="Film %06d" % n, category_id="1", cmd="/media/%d.mpg" % n)
                    for n in range((page - 1) * 1000 + 1, min(page * 1000, 2500) + 1)]
            if kind == "series":
                for entry in data:
                    entry.update(id="123:" + entry["id"], series_id="123:" + entry["id"],
                                 is_series="1", cmd="")
            return dict(data=data, total_items=2500)
        client._request = request
        window = self.window()
        window.content_type = kind
        prefix = "_" + kind + "_"
        owner = SimpleNamespace(client=client, _hidden_category_ids=(), _closed=False,
            _client_has_more_items=client.has_more_items)
        for suffix, value in dict(browser_catalog=None, native_window=window,
                                  generation=1, catalog_generation=1, window_base=0).items():
            setattr(owner, prefix + suffix, value)
        owner._vod_window_base = lambda unused: getattr(owner, prefix + "window_base")
        def first_id(number):
            return ("123:" if kind == "series" else "") + str(number)
        with (patch.object(builtins, "open", forbidden), patch.object(os, "open", forbidden),
              patch.object(stalker, "log_event") as diagnostics):
            self.methods["_load_" + kind + "_worker"](owner, 1, "1")
            self.assertEqual(getattr(owner, prefix + "error"), "")
            self.assertEqual(len(getattr(owner, prefix + "entries")), 240)
            self.assertEqual(window.count, 1000)
            self.methods["_load_" + kind + "_more_worker"](owner, 1, "1", client.load_more_items)
            result = getattr(owner, prefix + "more_result")
            self.assertEqual(result[4], "")
            self.assertEqual(result[2][0].stream_id, first_id(241))
            self.assertEqual(requests, [1])
            setattr(owner, prefix + "window_base", 720)
            setattr(owner, prefix + "entries", window.read_window(offset=720, size=280).items)
            self.methods["_load_" + kind + "_more_worker"](owner, 1, "1", client.load_more_items)
            result = getattr(owner, prefix + "more_result")
            self.assertEqual(result[4], "")
            self.assertEqual(result[2][0].stream_id, first_id(1001))
            self.assertEqual(window.count, 2000)
            self.assertEqual(requests, [1, 2])
            self.assertEqual(window.read_window(offset=0, size=80).items[0].stream_id, first_id(1))
            self.methods["_close_native_vod_windows"](owner)
            self.assertEqual(memory.budget.used, 0)

    def test_window_memory_limit_keeps_preceding_rows_readable(self):
        window = self.window(max_bytes=5000)
        with self.assertRaises(ContentError) as caught:
            window.append(items(count=100))
        self.assertEqual(caught.exception.reason, ContentError.CATALOG_ITEM_LIMIT)
        self.assertGreater(window.count, 0)
        self.assertLessEqual(memory.budget.used, 5000)
        self.assertEqual(window.read_window(size=1).items[0].stream_id, "1")

    def test_native_and_streaming_catalogues_share_one_memory_budget(self):
        index = support.catalog.MovieCatalogIndex(support.Client(count=10), directory="test-no-cleanup")
        try:
            index.prepare()
            used = memory.budget.used
            with patch.object(memory.budget, "maximum", used + 1000):
                window = self.window()
                with self.assertRaises(ContentError):
                    window.append(items(count=100))
                self.assertLessEqual(memory.budget.used, used + 1000)
                window.close()
                self.assertEqual(memory.budget.used, used)
        finally:
            index.close()

    def test_low_available_memory_stops_without_disk_fallback(self):
        window = self.window()
        with patch.object(memory, "available_memory", return_value=1), patch.object(os, "open", forbidden):
            with self.assertRaises(ContentError):
                window.append(items())
        self.assertEqual(window.count, 0)
        self.assertEqual(memory.budget.used, 0)

    def test_close_during_worker_releases_rows_without_waiting(self):
        window = self.window()
        def stream():
            yield next(items())
            window.close()
            yield next(items(start=2))
        with self.assertRaises(ContentError) as caught:
            window.append(stream())
        self.assertEqual(caught.exception.reason, ContentError.CANCELLED)
        self.assertEqual(window.count, 0)
        self.assertEqual(memory.budget.used, 0)

    def test_cancelled_append_preserves_prior_rows_until_close(self):
        window = self.window()
        window.append(items(count=20))
        def cancel():
            raise ContentError(reason=ContentError.CANCELLED)
        with self.assertRaises(ContentError):
            window.append(items(start=21), token=SimpleNamespace(check=cancel))
        self.assertEqual(window.count, 20)
        self.assertEqual(window.read_window(size=1).items[0].stream_id, "1")

    def test_repeated_open_close_has_no_accounting_growth(self):
        weights = []
        for unused in range(10):
            window = self.window()
            window.append(items(count=200))
            weights.append(memory.budget.used)
            window.close()
            window.close()
            self.assertEqual(memory.budget.used, 0)
            with self.assertRaises(ContentError):
                window.read_window()
        self.assertEqual(len(set(weights)), 1)

    def test_garbage_collection_releases_unclosed_window(self):
        window = NativeBrowseWindow("movie")
        window.append(items())
        self.assertGreater(memory.budget.used, 0)
        del window
        gc.collect()
        self.assertEqual(memory.budget.used, 0)

    def test_recent_duplicate_filter_stays_bounded(self):
        window = self.window()
        window.append(items(count=1000))
        self.assertEqual(len(window._rows.recent), 560)
        self.assertEqual(window.append(items(start=900, count=20)), [])
        self.assertEqual(window.count, 1000)

    def test_large_record_rejected_before_retention(self):
        window = self.window()
        item = next(items())
        item.name = "x" * 32769
        with self.assertRaises(ContentError):
            window.append([item])
        self.assertEqual(window.count, 0)
        self.assertEqual(memory.budget.used, 0)


if __name__ == "__main__":
    unittest.main()

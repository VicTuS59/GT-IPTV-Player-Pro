"""History/episode/download compatibility with SQLite imports unavailable."""
import ast
import builtins
import importlib.util
import json
import os
import random
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

P = Path(__file__).resolve().parents[1] / 'GTIPTVPlayerPro'
NAME = '_json_history_checks'
spec = importlib.util.spec_from_file_location(NAME, P / '__init__.py', submodule_search_locations=[str(P)])
pkg = importlib.util.module_from_spec(spec)
sys.modules[NAME] = pkg
spec.loader.exec_module(pkg)
history = __import__(NAME + '.history_index', fromlist=['*'])
episodes = __import__(NAME + '.episode_history', fromlist=['*'])


class JSONHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='gt-json-history-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        history.clear_history_cache()
        original = builtins.__import__
        def no_sqlite(name, *args, **kwargs):
            if name.split('.')[0] in ('sqlite3', '_sqlite3'):
                raise AssertionError('SQLite import attempted')
            return original(name, *args, **kwargs)
        guard = patch.object(builtins, '__import__', side_effect=no_sqlite)
        guard.start(); self.addCleanup(guard.stop)

    def write(self, name, value):
        path = self.root / name
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
        return str(path)

    def jobs(self, rows):
        return history.HistoryIndex(self.write('downloads.json', {'jobs': rows}), 'jobs')

    def test_lookup_reads_existing_json_and_last_duplicate_wins(self):
        index = self.jobs([{'id':'a','title':'ilk'},{'id':'b'}, {'id':'a','title':'son'}])
        self.assertEqual(index.get_many(iter(('b','a','missing'))), {'a':{'id':'a','title':'son'},'b':{'id':'b'}})
        self.assertEqual([key for key,value in index.items()], ['a','b'])
        self.assertEqual([p.name for p in self.root.iterdir()], ['downloads.json'])

    def test_download_pages_preserve_timestamp_order_ties_folder_and_terminal_filter(self):
        rows = [{'id':'b','created':20,'status':'completed','path':'/media/hdd/A/b.ts'},
                {'id':'a','created':20,'status':'cancelled','path':'/media/hdd/A/a.ts'},
                {'id':'new','created':100,'status':'error','path':'/media/hdd/A/new.ts'},
                {'id':'other','created':99,'status':'completed','path':'/media/hdd/AB/c.ts'},
                {'id':'old','created':2,'status':'completed','path':'/media/hdd/A/old.ts'}]
        index = self.jobs(rows)
        page,total = index.page(0,2,'/media/hdd/A/./',True)
        self.assertEqual([row['id'] for row in page], ['a','b']); self.assertEqual(total,3)
        page,total = index.page(2,2,'/media/hdd/A',True)
        self.assertEqual([row['id'] for row in page], ['old']); self.assertEqual(total,3)
        self.assertEqual(index.page(8,2,'/media/hdd/A',True), ([],3))

    def test_partitioned_history_preserves_all_records_and_global_order(self):
        rows = [{'id':str(i).zfill(4),'created':i%7,'status':'completed','path':'/media/hdd/a.ts'} for i in range(135)]
        rows += [dict(rows[6],created=999),dict(rows[120],status='error')]
        random.Random(42).shuffle(rows)
        unique = {row['id']:row for row in rows}
        expected = sorted(unique.values(),key=lambda row:(-row['created'],row['id']))
        index = self.jobs(rows)
        with patch.object(history,'PARTITION_BYTES',2048), patch.object(history,'METADATA_BYTES',2048):
            self.assertEqual(list(index.items()), sorted(unique.items()))
            actual,total = index.page(37,15)
            self.assertEqual(actual,expected[37:52]); self.assertEqual(total,len(unique))

    def test_cached_results_are_independent_and_new_file_generation_invalidates_them(self):
        index = self.jobs([{'id':'a','created':1}])
        result = index.get_many(('a',)); result['a']['created']=90
        self.assertEqual(index.get_many(('a',))['a']['created'],1)
        page,total = index.page(); page[0]['created']=99
        self.assertEqual(index.page()[0][0]['created'],1)
        temp = Path(index.path+'.replacement'); temp.write_text('{"jobs":[{"id":"a","created":7}]}',encoding='utf-8'); os.replace(temp,index.path)
        self.assertEqual(index.get_many(('a',))['a']['created'],7)
        self.assertEqual(index.page()[0][0]['created'],7)

    def test_repeated_queries_reuse_bounded_memory_cache(self):
        index = self.jobs([{'id':str(i),'created':i} for i in range(80)])
        index.page()
        with patch.object(index,'_records',side_effect=AssertionError('Unnecessary history scan')):
            self.assertEqual(index.page()[1],80)
        for i in range(80): index.get_many((str(i),))
        self.assertLessEqual(history._RESULT_BYTES,history.RESULT_CACHE_BYTES)
        self.assertLessEqual(len(history._RESULTS),history.RESULT_CACHE_ENTRIES)

    def test_removed_paths_stay_removed_after_new_instance_and_reload(self):
        path = self.write('removed.json',{'version':1,'paths':['/media/hdd/İzlenen.ts','/media/usb/a.ts']})
        removed = history.RemovedPaths(path)
        self.assertIn('/media/hdd/İzlenen.ts',removed)
        self.assertEqual(removed.matching(iter(('/media/hdd/İzlenen.ts','/media/hdd/new.ts'))), {'/media/hdd/İzlenen.ts'})
        history.clear_history_cache()
        self.assertIn('/media/hdd/İzlenen.ts',history.RemovedPaths(path))

    def test_legacy_and_current_watched_records_keep_dates_and_atomic_changes(self):
        a,b,c = ('a'*64,'b'*64,'c'*64)
        path = self.write('watched.json',{'version':1,'items':{a:True,b:{'watched':True,'finished_at':123}}})
        store = episodes.WatchedEpisodeStore(path)
        self.assertEqual(store.get_many((a,b)),{a:{'finished_at':0},b:{'finished_at':123}})
        self.assertTrue(store.mark(c,finished_at=456))
        self.assertTrue(store.mark(b,finished_at=999))
        self.assertEqual(store.get_many((b,))[b]['finished_at'],123)
        self.assertTrue(store.unmark(a))
        current = json.loads(Path(path).read_text(encoding='utf-8'))
        self.assertEqual(current['version'],2); self.assertNotIn(a,current['items'])
        self.assertEqual(current['items'][b]['finished_at'],123)
        self.assertEqual(episodes.WatchedEpisodeStore(path).get_many((c,))[c]['finished_at'],456)

    def test_async_watched_unwatched_confirmation_preserves_current_contract(self):
        path = str(self.root/'async.json'); key='e'*64
        store = episodes.WatchedEpisodeStore(path)
        self.assertTrue(store.set_watched_async(key,True,finished_at=700))
        self.assertTrue(store.confirm_watched(key,True,timeout=3))
        self.assertTrue(store.set_watched_async(key,False))
        self.assertTrue(store.confirm_watched(key,False,timeout=3))
        self.assertEqual(store.get_many((key,)),{})

    def test_malformed_or_unsupported_document_is_never_partially_accepted(self):
        path = self.root/'bad.json'
        for body in ('{"version":2,"items":{"'+('a'*64)+'":true}} trailing',
                     '{"version":9,"items":{"'+('a'*64)+'":true}}',
                     '{"version":2,"items":{"'+('a'*64)+'":true},"broken":'):
            path.write_text(body,encoding='utf-8')
            original=path.read_bytes()
            with self.assertRaises(ValueError): history.HistoryIndex(str(path),'items',True).get_many(('a'*64,))
            self.assertFalse(episodes.WatchedEpisodeStore(str(path)).mark('b'*64,finished_at=100))
            self.assertEqual(path.read_bytes(),original)

    def test_failed_atomic_write_leaves_original_history_and_no_partial_file(self):
        path = self.write('watched.json',{'version':2,'items':{'a'*64:True}}); original=Path(path).read_bytes()
        with patch.object(history.os,'replace',side_effect=OSError('disk full')):
            self.assertFalse(episodes.WatchedEpisodeStore(path).mark('b'*64,finished_at=100))
        self.assertEqual(Path(path).read_bytes(),original)
        self.assertEqual([p.name for p in self.root.iterdir()],['watched.json'])

    def test_symlink_or_directory_source_is_rejected(self):
        with self.assertRaises(OSError): history.HistoryIndex(str(self.root),'jobs').page()
        source=Path(self.write('source.json',{'jobs':[]})); link=self.root/'link.json'
        try: link.symlink_to(source)
        except OSError: return  # Windows may not grant symlink creation.
        with self.assertRaises(OSError): history.HistoryIndex(str(link),'jobs').page()

    def test_concurrent_queries_share_no_mutable_results(self):
        index=self.jobs([{'id':str(i),'created':1} for i in range(120)])
        errors=[]
        def reader():
            try:
                for i in range(8):
                    rows,total=index.page(0,100)
                    self.assertEqual(total,120)
                    self.assertEqual(len({row['created'] for row in rows}),1)
            except BaseException as error: errors.append(error)
        threads=[threading.Thread(target=reader) for unused in range(3)]
        for thread in threads: thread.start()
        for thread in threads: thread.join(5); self.assertFalse(thread.is_alive())
        self.assertFalse(errors,errors)

    def test_replacement_during_query_retries_the_whole_generation(self):
        index=self.jobs([{'id':str(i),'created':1} for i in range(120)])
        original=index._records; replaced=[]
        def records():
            yield from original()
            # Swap between the query's metadata and row lookups. Closing the
            # old descriptor first also supports Windows' file sharing rules.
            if not replaced:
                replaced.append(True)
                history.rewrite(index.path,'jobs',({'id':str(i),'created':9} for i in range(20)),{})
        with patch.object(index,'_records',side_effect=records):
            rows,total=index.page()
        self.assertEqual(total,20); self.assertEqual(len(rows),20)
        self.assertEqual({row['created'] for row in rows},{9})

    def test_legacy_disposable_database_files_are_never_read_or_written(self):
        index=self.jobs([{'id':'old','created':5}])
        legacy=Path(index.path+'.history-index.sqlite3'); legacy.write_bytes(b'obsolete database')
        self.assertEqual(index.page()[1],1)
        self.assertEqual(index.get_many(('old',))['old']['created'],5)
        self.assertEqual(legacy.read_bytes(),b'obsolete database')
        self.assertEqual(len(list(self.root.iterdir())),2)

    def test_read_only_history_queries_require_no_cache_or_database_writes(self):
        index=self.jobs([{'id':'a','created':1}]); original=history.os.open
        def read_only(path,flags,*args,**kwargs):
            if flags & (os.O_WRONLY|os.O_RDWR|os.O_CREAT): raise OSError('Read-only storage')
            return original(path,flags,*args,**kwargs)
        with patch.object(history.os,'open',side_effect=read_only):
            self.assertEqual(index.page()[1],1)
            self.assertEqual(index.get_many(('a',))['a']['created'],1)
        self.assertEqual([p.name for p in self.root.iterdir()],['downloads.json'])

    def test_jump_near_end_and_following_cursor_preserve_page_boundaries(self):
        rows=[{'id':str(i).zfill(4),'created':i} for i in range(251)]
        random.Random(10).shuffle(rows);index=self.jobs(rows)
        with patch.object(history,'METADATA_BYTES',4096):
            page,total=index.page(225,10)
            self.assertEqual(total,251);self.assertEqual([row['created'] for row in page],list(range(25,15,-1)))
            page,total=index.page(235,10)
            self.assertEqual([row['created'] for row in page],list(range(15,5,-1)))

    def test_source_and_package_do_not_depend_on_sqlite(self):
        for path in P.rglob('*.py'):
            tree=ast.parse(path.read_bytes())
            for node in ast.walk(tree):
                if isinstance(node,ast.Import): self.assertFalse(any(a.name.split('.')[0] in ('sqlite3','_sqlite3') for a in node.names),path.name)
                if isinstance(node,ast.ImportFrom): self.assertNotIn((node.module or '').split('.')[0],('sqlite3','_sqlite3'),path.name)
        self.assertNotIn('python3-sqlite3',(P.parent/'CONTROL/control').read_text(encoding='utf-8').split('Installed-Size:')[0])


if __name__ == '__main__':
    unittest.main(verbosity=2)

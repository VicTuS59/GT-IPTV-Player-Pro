"""Scoped R0-to-R2 cache migration; authoritative JSON and other data survive."""
import ast,builtins,importlib.util,json,os,stat,subprocess,sys,tempfile,threading,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch

P=Path(__file__).resolve().parents[1]/'GTIPTVPlayerPro';NAME='_r2_migration_checks'
spec=importlib.util.spec_from_file_location(NAME,P/'__init__.py',submodule_search_locations=[str(P)])
pkg=importlib.util.module_from_spec(spec);sys.modules[NAME]=pkg;spec.loader.exec_module(pkg)
cleanup=__import__(NAME+'.legacy_storage_cleanup',fromlist=['*'])
backup=__import__(NAME+'.backup',fromlist=['*'])
history=__import__(NAME+'.history_index',fromlist=['*'])


class MigrationTests(unittest.TestCase):
    def test_live_tmp_alias_is_replaced_instead_of_counted_as_four_deferrals(self):
        visited=[]
        proxy=types.SimpleNamespace(sep='/',path=types.SimpleNamespace(sep='/',abspath=lambda p:p,realpath=lambda p:'/var/volatile/tmp'))
        def empty(root,relative):
            visited.append(relative)
            if relative.startswith('tmp/'):raise OSError('No-follow rejects the directory symlink')
            raise FileNotFoundError('No obsolete directory or file exists')
        with patch.object(cleanup,'os',proxy),patch.object(cleanup,'_Directory',side_effect=empty):
            result=cleanup.clean_legacy_storage('/')
        self.assertEqual(result,{'removed':0,'skipped':0})
        self.assertFalse(any(p.startswith('tmp/') for p in visited))
        self.assertEqual(sum(p.startswith('var/volatile/tmp/') for p in visited),4)

    def test_offline_image_and_unknown_tmp_targets_are_not_redirected(self):
        for root,alias in (('/offline/image','/var/volatile/tmp'),('/','/another/location'),('/','/tmp')):
            visited=[]
            proxy=types.SimpleNamespace(sep='/',path=types.SimpleNamespace(sep='/',abspath=lambda p:p,realpath=lambda p:alias))
            with patch.object(cleanup,'os',proxy),patch.object(cleanup,'_clean_directory',side_effect=lambda r,p,pattern,result:visited.append(p)):
                cleanup.clean_legacy_storage(root)
            self.assertEqual(sum(p.startswith('tmp/') for p in visited),4)
            self.assertFalse(any(p.startswith('var/volatile/tmp/') for p in visited))

    def setUp(self):
        test_parent=os.environ.get('GT_MIGRATION_TEST_TMP')
        self.temp=tempfile.TemporaryDirectory(prefix='gt-r2-migration-',dir=test_parent);self.root=Path(self.temp.name);self.addCleanup(self.temp.cleanup)
        self.base=self.root/'etc/enigma2/gtiptvplayer';self.base.mkdir(parents=True)
        self.episode=self.root/'etc/enigma2/gtiptvplayerpro-episode-history.json'
        self.episode.write_text(json.dumps({'version':2,'items':{'a'*64:{'watched':True,'finished_at':123}}}),encoding='utf-8')
        (self.base/'downloads.json').write_text(json.dumps({'jobs':[{'id':'old','created':10,'status':'completed','path':'/media/hdd/keep.ts'}]}),encoding='utf-8')
        (self.base/'downloads-removed.json').write_text(json.dumps({'version':1,'paths':['/media/hdd/removed.ts']}),encoding='utf-8')
        self.json_before={p:p.read_bytes() for p in (self.episode,self.base/'downloads.json',self.base/'downloads-removed.json')}
        original=builtins.__import__
        def no_driver(name,*args,**kwargs):
            if name.split('.')[0] in ('sqlite3','_sqlite3'):raise AssertionError('Database driver requested')
            return original(name,*args,**kwargs)
        driver_guard=patch.object(builtins,'__import__',side_effect=no_driver);driver_guard.start();self.addCleanup(driver_guard.stop)

    def file(self,relative,body=b'old disposable cache'):
        p=self.root/relative;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(body);return p

    def assert_json_intact(self):
        for p,body in self.json_before.items():self.assertEqual(p.read_bytes(),body)

    def test_all_three_history_databases_and_known_sidecars_are_removed(self):
        paths=[]
        variants=('','-journal','-wal','-shm','.0123456789abcdef0123456789abcdef.tmp','.0123456789abcdef0123456789abcdef.tmp-journal')
        for json_file in self.json_before:
            for suffix in variants:
                p=Path(str(json_file)+'.history-index.sqlite3'+suffix);p.write_bytes(b'SQLite format 3\x00old cache');paths.append(p)
        result=cleanup.clean_legacy_storage(str(self.root))
        self.assertEqual(result,{'removed':18,'skipped':0});self.assertTrue(all(not p.exists() for p in paths));self.assert_json_intact()

    def test_catalogue_databases_removed_in_all_known_cache_directories(self):
        paths=[]
        for directory in cleanup._CATALOGUES:
            for suffix in ('','-wal','-shm','-journal'):
                paths.append(self.file(directory+'/'+('a'*64)+'.sqlite3'+suffix))
        result=cleanup.clean_legacy_storage(str(self.root))
        self.assertEqual(result,{'removed':len(paths),'skipped':0});self.assertTrue(all(not p.exists() for p in paths));self.assert_json_intact()

    def test_unrelated_files_and_wrong_locations_are_never_deleted(self):
        keep=[self.file('etc/enigma2/other-plugin.sqlite3'),
              self.file('etc/enigma2/downloads.json.history-index.sqlite3'),
              self.file('etc/enigma2/gtiptvplayer/gtiptvplayerpro-episode-history.json.history-index.sqlite3'),
              self.file('etc/enigma2/gtiptvplayer/user.sqlite3'),
              self.file('etc/enigma2/gtiptvplayer/downloads.json.history-index.sqlite3.notes'),
              self.file('etc/enigma2/gtiptvplayer/downloads.json.history-index.sqlite3.not-a-uuid.tmp'),
              self.file('media/hdd/keep.ts',b'video'),
              self.file('usr/lib/python3/sqlite3/__init__.py',b'shared system module'),
              self.file('etc/enigma2/gtiptvplayer/vod-catalog/index/'+('a'*64)+'.jsonl')]
        before={p:p.read_bytes() for p in keep};cleanup.clean_legacy_storage(str(self.root))
        self.assertEqual({p:p.read_bytes() for p in keep},before);self.assert_json_intact()

    def test_cleanup_is_idempotent_and_creates_no_directories_or_markers(self):
        before={p.relative_to(self.root).as_posix() for p in self.root.rglob('*')}
        self.assertEqual(cleanup.clean_legacy_storage(str(self.root)),{'removed':0,'skipped':0})
        self.assertEqual(cleanup.clean_legacy_storage(str(self.root)),{'removed':0,'skipped':0})
        self.assertEqual({p.relative_to(self.root).as_posix() for p in self.root.rglob('*')},before)

    def test_failed_unlink_preserves_json_and_defers_optional_cleanup(self):
        p=Path(str(self.episode)+'.history-index.sqlite3');p.write_bytes(b'old')
        with patch.object(cleanup._Directory,'unlink',side_effect=PermissionError('Read-only storage')):
            result=cleanup.clean_legacy_storage(str(self.root))
        self.assertGreater(result['skipped'],0);self.assertTrue(p.exists());self.assert_json_intact()

    def test_directory_with_database_name_is_not_recursively_deleted(self):
        p=Path(str(self.episode)+'.history-index.sqlite3');p.mkdir();(p/'user.txt').write_text('keep')
        result=cleanup.clean_legacy_storage(str(self.root))
        self.assertTrue((p/'user.txt').exists());self.assertGreater(result['skipped'],0);self.assert_json_intact()

    def test_symlinked_cache_directory_is_not_followed(self):
        outside=self.root/'outside';outside.mkdir();keep=outside/(('a'*64)+'.sqlite3');keep.write_bytes(b'keep')
        directory=self.base/'vod-catalog';
        try:directory.symlink_to(outside,target_is_directory=True)
        except OSError:self.skipTest('Host does not permit directory symlinks')
        result=cleanup.clean_legacy_storage(str(self.root))
        self.assertEqual(keep.read_bytes(),b'keep');self.assertTrue(directory.is_symlink());self.assertGreater(result['skipped'],0)

    def test_database_symlink_removes_only_the_link(self):
        keep=self.file('outside/keep.db',b'keep');link=Path(str(self.episode)+'.history-index.sqlite3')
        try:link.symlink_to(keep)
        except OSError:self.skipTest('Host does not permit file symlinks')
        cleanup.clean_legacy_storage(str(self.root));self.assertFalse(link.is_symlink());self.assertEqual(keep.read_bytes(),b'keep')

    def test_hardlinked_cache_does_not_remove_other_links_content(self):
        keep=self.file('outside/keep.db',b'keep');link=Path(str(self.episode)+'.history-index.sqlite3')
        try:os.link(keep,link)
        except OSError:self.skipTest('Host does not permit hard links')
        cleanup.clean_legacy_storage(str(self.root));self.assertFalse(link.exists());self.assertEqual(keep.read_bytes(),b'keep')

    def test_held_or_unsafe_catalogue_lock_defers_its_entire_group(self):
        p=self.file('etc/enigma2/gtiptvplayer/vod-catalog/index/'+('a'*64)+'.sqlite3');lock=self.file(p.relative_to(self.root).as_posix()+'.lock')
        with patch.object(cleanup._Directory,'lock',side_effect=BlockingIOError('Busy')):
            result=cleanup.clean_legacy_storage(str(self.root))
        self.assertTrue(p.exists());self.assertTrue(lock.exists());self.assertGreater(result['skipped'],0)

    def test_unlocked_catalogue_lock_is_closed_and_removed_after_its_data(self):
        p=self.file('etc/enigma2/gtiptvplayer/vod-catalog/index/'+('a'*64)+'.sqlite3');lock=self.file(p.relative_to(self.root).as_posix()+'.lock',b' ')
        result=cleanup.clean_legacy_storage(str(self.root));self.assertFalse(p.exists());self.assertFalse(lock.exists());self.assertEqual(result['skipped'],0)

    def test_backup_and_restore_policy_excludes_all_history_sidecar_variants(self):
        manager=backup.BackupManager(root=str(self.root))
        with patch.object(backup,'_path',side_effect=lambda p:p):
            for source in ('downloads.json','downloads-removed.json'):
                logical='/etc/enigma2/gtiptvplayer/'+source
                self.assertTrue(manager._settings_file(logical,'data'))
                for suffix in ('','-journal','-wal','-shm','.lock','.0123456789abcdef0123456789abcdef.tmp','.0123456789abcdef0123456789abcdef.tmp-journal'):
                    old=logical+'.history-index.sqlite3'+suffix
                    self.assertFalse(manager._settings_file(old,'data'),old)
                    entries=manager.restore_entries({'files':[dict(path=old,kind='data')]})
                    self.assertEqual(entries,[])

    def test_r0_history_survives_cleanup_and_r2_queries_create_no_database(self):
        old=Path(str(self.episode)+'.history-index.sqlite3');old.write_bytes(b'old')
        cleanup.clean_legacy_storage(str(self.root))
        self.assertEqual(history.HistoryIndex(str(self.episode),'items',True).get_many(('a'*64,))['a'*64]['finished_at'],123)
        self.assertEqual(history.HistoryIndex(str(self.base/'downloads.json'),'jobs').page()[1],1)
        self.assertIn('/media/hdd/removed.ts',history.RemovedPaths(str(self.base/'downloads-removed.json')))
        self.assertFalse(any('.sqlite3' in p.name for p in self.root.rglob('*')));self.assert_json_intact()

    def test_standalone_installer_entry_point_honours_offline_image_root(self):
        p=Path(str(self.episode)+'.history-index.sqlite3');p.write_bytes(b'old')
        result=subprocess.run([sys.executable,'-B',str(P/'legacy_storage_cleanup.py'),'--root',str(self.root)],capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr);self.assertFalse(p.exists());self.assert_json_intact()
        source=(P.parent/'CONTROL/postinst').read_text(encoding='utf-8')
        self.assertIn('legacy_storage_cleanup.py" --root "${TARGET_ROOT:-/}"',source)

    def test_startup_is_scheduled_once_without_waiting_or_gui_calls(self):
        with patch.object(cleanup,'_LIVE_PLATFORM',True),patch.object(cleanup,'_STARTED',False),patch.object(cleanup.threading,'Thread') as thread:
            cleanup.schedule_legacy_cleanup();cleanup.schedule_legacy_cleanup()
            self.assertEqual(thread.call_count,1);thread.return_value.start.assert_called_once()
            self.assertTrue(thread.call_args.kwargs['daemon']);self.assertNotIn('join',thread.return_value.method_calls)
        tree=ast.parse((P/'main.py').read_bytes())
        self.assertTrue(any(isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='schedule_legacy_cleanup' for n in ast.walk(tree)))

    def test_no_database_dependency_or_driver_import_is_added(self):
        tree=ast.parse((P/'legacy_storage_cleanup.py').read_bytes())
        for node in ast.walk(tree):
            if isinstance(node,ast.Import):self.assertFalse(any('sqlite' in a.name.lower() for a in node.names))
            if isinstance(node,ast.ImportFrom):self.assertNotIn('sqlite',(node.module or '').lower())
        control=(P.parent/'CONTROL/control').read_text(encoding='utf-8')
        self.assertNotIn('sqlite',next(line for line in control.splitlines() if line.startswith('Depends:')).lower())

    def test_reparse_parent_policy_is_rejected_without_native_symlink_privilege(self):
        if os.name!='nt':self.skipTest('Windows offline-root policy')
        directory=self.base/'vod-catalog';directory.mkdir();keep=directory/(('b'*64)+'.sqlite3');keep.write_bytes(b'keep')
        original=cleanup.os.lstat
        def reparse(path,*args,**kwargs):
            info=original(path,*args,**kwargs)
            if os.path.normcase(str(path))==os.path.normcase(str(directory)):
                return types.SimpleNamespace(st_mode=info.st_mode,st_file_attributes=0x400)
            return info
        with patch.object(cleanup.os,'lstat',side_effect=reparse):result=cleanup.clean_legacy_storage(str(self.root))
        self.assertTrue(keep.exists());self.assertGreater(result['skipped'],0);self.assert_json_intact()

    def test_posix_descriptor_path_uses_no_follow_and_relative_unlink(self):
        # Exercise the actual POSIX branch through an inert descriptor adapter
        # on Windows, where native directory descriptors are unavailable.
        real=os;handles={};unlinked=[];opened=[];next_handle=[1000000]
        proxy=types.SimpleNamespace(name='posix',path=real.path,sep=real.sep,O_RDONLY=real.O_RDONLY,
            O_DIRECTORY=0x1000000,O_NOFOLLOW=0x2000000,O_NONBLOCK=0x4000000,geteuid=lambda:real.stat(self.root).st_uid)
        def target(name,dir_fd=None):return real.path.join(handles[dir_fd],name) if dir_fd is not None else real.path.abspath(name)
        def open_dir(name,flags,dir_fd=None):
            path=target(name,dir_fd);self.assertTrue(flags&proxy.O_NOFOLLOW)
            info=real.lstat(path)
            if not stat.S_ISDIR(info.st_mode):raise OSError('Not a plain directory')
            next_handle[0]+=1;fd=next_handle[0];handles[fd]=path;opened.append((name,dir_fd,flags));return fd
        def unlink(name,dir_fd=None):
            self.assertIsNotNone(dir_fd);unlinked.append((name,dir_fd));real.unlink(target(name,dir_fd))
        proxy.open=open_dir;proxy.close=lambda fd:handles.pop(fd)
        proxy.stat=lambda name,dir_fd=None,follow_symlinks=False:real.lstat(target(name,dir_fd))
        proxy.scandir=lambda fd:real.scandir(handles[fd]);proxy.unlink=unlink
        p=Path(str(self.episode)+'.history-index.sqlite3');p.write_bytes(b'old')
        with patch.object(cleanup,'os',proxy):result=cleanup.clean_legacy_storage(str(self.root))
        self.assertFalse(p.exists());self.assertEqual(result['removed'],1);self.assertTrue(opened);self.assertTrue(unlinked);self.assertFalse(handles);self.assert_json_intact()

    def test_startup_cleanup_failure_does_not_escape_into_gui(self):
        class ImmediateThread:
            def __init__(self,target,**kwargs):self.target=target
            def start(self):self.target()
        with patch.object(cleanup,'_LIVE_PLATFORM',True),patch.object(cleanup,'_STARTED',False),patch.object(cleanup,'clean_legacy_storage',side_effect=OSError('Unavailable')),patch.object(cleanup.threading,'Thread',ImmediateThread):
            cleanup.schedule_legacy_cleanup()


if __name__=='__main__':unittest.main(verbosity=2)

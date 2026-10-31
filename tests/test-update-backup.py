"""Linux integration tests with real private backup archives and isolated data.

No real receiver settings, network endpoint or package manager are touched.
"""
import contextlib,hashlib,importlib.util,json,os,stat,sys,tempfile,types,unittest,zipfile
from pathlib import Path
from unittest.mock import Mock,patch

if 'PLUGIN_ROOT' not in globals():PLUGIN_ROOT=Path(__file__).resolve().parents[1]/'GTIPTVPlayerPro'
if 'PACKAGE_NAME' not in globals():
 PACKAGE_NAME='_update_backup_integration'
 spec=importlib.util.spec_from_file_location(PACKAGE_NAME,PLUGIN_ROOT/'__init__.py',submodule_search_locations=[str(PLUGIN_ROOT)])
 pkg=importlib.util.module_from_spec(spec);sys.modules[PACKAGE_NAME]=pkg;spec.loader.exec_module(pkg)
service=__import__(PACKAGE_NAME+'.update_service',fromlist=['*'])
backup=__import__(PACKAGE_NAME+'.backup',fromlist=['*'])
settings=__import__(PACKAGE_NAME+'.settings',fromlist=['*'])

@unittest.skipIf(os.name!='posix','Backup mount/owner tests require the receiver filesystem API')
class SafetyBackupTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='gtiptv-backup-test-');self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name).resolve();self.flash=self.root/'etc/enigma2';self.flash.mkdir(parents=True)
  self.drive=self.root/'media/disk/İNDİRMELER';self.drive.mkdir(parents=True)
  self.data=self.flash/'gtiptvplayer';self.data.mkdir()
  (self.data/'accounts.json').write_text('[]\n')
  (self.flash/'settings').write_text('config.plugins.gtiptvplayerpro.backup_folder=/media/disk/İNDİRMELER\nconfig.plugins.gtiptvplayerpro.cinematic_view=true\nconfig.unrelated.keep=yes\n')
  self.manager=backup.BackupManager(root=str(self.root))
  self.section=types.SimpleNamespace(backup_folder=types.SimpleNamespace(value=str(self.drive)))
  for replacement in (patch.object(backup,'BackupManager',return_value=self.manager),
      patch.object(settings,'plugin_config_section',return_value=self.section),
      patch.object(service,'BACKUP_FALLBACK',str(self.flash))):
   replacement.start();self.addCleanup(replacement.stop)
  self.originals={p:hashlib.sha256(p.read_bytes()).hexdigest() for p in (self.data/'accounts.json',self.flash/'settings')}

 def tearDown(self):
  self.assertEqual({p:hashlib.sha256(p.read_bytes()).hexdigest() for p in self.originals},self.originals)

 def create(self):
  path=Path(service.create_safety_backup());self.assertTrue(path.is_file())
  self.assertEqual(stat.S_IMODE(path.stat().st_mode),0o600)
  self.assertEqual(stat.S_IMODE(path.parent.stat().st_mode),0o700)
  manifest=self.manager.inspect(str(path));self.assertEqual(manifest['purpose'],'safety')
  with zipfile.ZipFile(path) as archive:
   native=next(f for f in manifest['files'] if f['kind']=='native')
   fragment=archive.read(native['member'])
   self.assertIn(b'config.plugins.gtiptvplayerpro.',fragment);self.assertNotIn(b'config.unrelated.',fragment)
  return path

 def test_configured_folder_is_used_with_verified_private_archive(self):
  path=self.create();self.assertEqual(path.parent,self.drive/'gtiptv-update-backups')
  self.assertFalse((self.flash/'gtiptv-update-backups').exists())

 def test_missing_drive_falls_back_without_recreating_missing_parent(self):
  missing=self.root/'missing-disk';self.section.backup_folder.value=str(missing)
  self.assertEqual(self.create().parent,self.flash/'gtiptv-update-backups');self.assertFalse(missing.exists())

 def test_writable_unmounted_media_placeholder_is_rejected_before_mkdir(self):
  real_destination=self.manager.destination
  native_manager=type(self.manager).__new__(type(self.manager));native_manager.root='/';native_manager.plugin_host=str(self.root/'code')
  def destination(folder):
   if folder=='/media/hdd':
    with patch.object(backup.os.path,'isdir',return_value=True),patch.object(backup.os,'access',return_value=True),patch.object(backup.os,'statvfs',return_value=types.SimpleNamespace(f_flag=0)),patch.object(backup.os.path,'ismount',return_value=False):
     return native_manager.destination(folder)
   return real_destination(folder)
  self.section.backup_folder.value='/media/hdd'
  with patch.object(self.manager,'destination',side_effect=destination),patch.object(service,'private_directory',wraps=service.private_directory) as mkdir:
   self.assertEqual(self.create().parent,self.flash/'gtiptv-update-backups')
   self.assertEqual(mkdir.call_args_list[0].args[0],str(self.flash/'gtiptv-update-backups'))

 def test_disk_full_prefers_flash_and_preserves_required_archive_margin(self):
  real_stat=backup.os.statvfs
  def space(path):
   if str(path).startswith(str(self.drive)):
    return types.SimpleNamespace(f_flag=0,f_bavail=16,f_frsize=4096,f_bsize=4096)
   return real_stat(path)
  with patch.object(backup.os,'statvfs',side_effect=space):
   self.assertEqual(self.create().parent,self.flash/'gtiptv-update-backups')

 def test_no_space_anywhere_returns_space_error_and_no_archive(self):
  with patch.object(backup.os,'statvfs',return_value=types.SimpleNamespace(f_flag=0,f_bavail=16,f_frsize=4096,f_bsize=4096)),self.assertRaises(service.UpdateError) as error:
   service.create_safety_backup()
  self.assertEqual(error.exception.code,'space');self.assertFalse(list(self.root.rglob('*.gtbackup')))

 def test_readonly_drive_falls_back_before_creating_backup_directory(self):
  real_stat=backup.os.statvfs
  def readonly(path):
   return types.SimpleNamespace(f_flag=getattr(os,'ST_RDONLY',1)) if str(path).startswith(str(self.drive)) else real_stat(path)
  with patch.object(backup.os,'statvfs',side_effect=readonly):self.assertEqual(self.create().parent,self.flash/'gtiptv-update-backups')
  self.assertFalse((self.drive/'gtiptv-update-backups').exists())

 def test_owned_data_directory_cannot_be_a_backup_target(self):
  self.section.backup_folder.value=str(self.data)
  self.assertEqual(self.create().parent,self.flash/'gtiptv-update-backups')
  self.assertFalse((self.data/'gtiptv-update-backups').exists())

 def test_backup_directory_symlink_is_not_followed_or_chmodded(self):
  target=self.root/'unrelated';target.mkdir(mode=0o755)
  (self.drive/'gtiptv-update-backups').symlink_to(target,target_is_directory=True)
  self.assertEqual(self.create().parent,self.flash/'gtiptv-update-backups')
  self.assertEqual(stat.S_IMODE(target.stat().st_mode),0o755);self.assertFalse(list(target.iterdir()))

 def test_all_targets_unavailable_is_explicit_storage_error(self):
  self.section.backup_folder.value=str(self.root/'gone')
  with patch.object(service,'BACKUP_FALLBACK',str(self.root/'also-gone')),self.assertRaises(service.UpdateError) as error:service.create_safety_backup()
  self.assertEqual(error.exception.code,'storage')

 def test_busy_backup_does_not_retry_or_return_permission_to_install(self):
  with patch.object(self.manager,'create',side_effect=backup.BackupError('busy')) as create,self.assertRaises(service.UpdateError) as error:service.create_safety_backup()
  self.assertEqual(error.exception.code,'busy');self.assertEqual(create.call_count,1)

 def test_changed_user_data_aborts_without_retrying_on_flash(self):
  with patch.object(self.manager,'create',side_effect=backup.BackupError('changed')) as create,self.assertRaises(service.UpdateError) as error:service.create_safety_backup()
  self.assertEqual(error.exception.code,'backup');self.assertEqual(create.call_count,1)

 def test_retention_keeps_three_backups_and_unrelated_files(self):
  paths=[self.create() for unused in range(5)];folder=paths[-1].parent
  unrelated=folder/'my-settings.gtbackup';unrelated.write_bytes(b'user')
  link=folder/'GTIPTVPlayerPro-0-safety-symlink.gtbackup';link.symlink_to(unrelated)
  current=self.create()
  owned=[p for p in folder.iterdir() if not p.is_symlink() and '-safety-' in p.name and p.suffix=='.gtbackup']
  self.assertEqual(len(owned),3);self.assertIn(current,owned);self.assertFalse(paths[0].exists())
  self.assertEqual(unrelated.read_bytes(),b'user');self.assertTrue(link.is_symlink())

 def test_retention_failure_does_not_invalidate_completed_backup(self):
  with patch.object(service,'_trim_safety_backups',wraps=service._trim_safety_backups),patch.object(service.os,'listdir',side_effect=PermissionError):self.create()

if __name__=='__main__':unittest.main(verbosity=2)

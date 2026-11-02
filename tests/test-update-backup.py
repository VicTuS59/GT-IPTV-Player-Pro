"""Upgrades never read backup sources; manual backups remain available."""
import base64,hashlib,importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
ROOT=Path(__file__).resolve().parents[1];PLUGIN_ROOT=ROOT/'GTIPTVPlayerPro';NAME='_no_update_backup_tests'
spec=importlib.util.spec_from_file_location(NAME,PLUGIN_ROOT/'__init__.py',submodule_search_locations=[str(PLUGIN_ROOT)])
pkg=importlib.util.module_from_spec(spec);sys.modules[NAME]=pkg;spec.loader.exec_module(pkg)
protocol=__import__(NAME+'.update_protocol',fromlist=['*'])
service=__import__(NAME+'.update_service',fromlist=['*'])
backup=__import__(NAME+'.backup',fromlist=['*'])
FIXTURE=json.loads((ROOT/'tests/update-fixtures.json').read_text());TRUST=FIXTURE['test_trust']
original_verify=protocol.verify_signature;protocol.KEY_ID=TRUST['key_id']
protocol.verify_signature=lambda body,signature:original_verify(body,signature,n=TRUST['n'],e=TRUST['e'])

class BackupRegressionTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory(prefix='gt-no-update-backup-');self.addCleanup(self.tmp.cleanup)
  self.root=Path(self.tmp.name);self.data=self.root/'etc/enigma2/gtiptvplayer';self.data.mkdir(parents=True)
  (self.data/'accounts.json').write_text('[]')
  (self.root/'etc/enigma2/settings').write_text('config.plugins.gtiptvplayerpro.cinematic_view=true\n')
  self.manager=backup.BackupManager(root=str(self.root));self.cache=self.root/'tmp/updates';self.cache.mkdir(parents=True)
  self.state=service.UpdateState(str(self.data/'update-settings.json'))
  self.updater=service.UpdateService(state=self.state,cache=str(self.cache),current='1.7.0-r106')
  body=FIXTURE['vectors']['stable'].encode();self.offer=dict(body=body,manifest=protocol.verify_envelope(body))
  self.path=self.updater.path_for(self.offer['manifest']);Path(self.path).write_bytes(base64.b64decode(FIXTURE['packages']['valid']))
 def install(self):
  runner=Mock(return_value=0)
  with patch.object(backup.BackupManager,'create',side_effect=AssertionError('Automatic backup is forbidden')) as create:
   result=self.updater.install(self.offer,self.path,busy=lambda:False,runner=runner,uid=0,root=str(self.root))
  create.assert_not_called();runner.assert_called_once();self.assertEqual(result['version'],self.offer['manifest']['version'])
  self.assertFalse(list(self.root.rglob('*.gtbackup')));self.assertFalse(list(self.root.rglob('gtiptv-update-backups')))
 def test_missing_external_account_file_does_not_block_upgrade(self):
  (self.data/'playlist-files.json').write_text(json.dumps({'types':{'xtream':{'slots':['/media/removed/account.txt']}}}))
  originals={p:hashlib.sha256(p.read_bytes()).digest() for p in self.root.rglob('*') if p.is_file()}
  self.install()
  for path,digest in originals.items():self.assertEqual(hashlib.sha256(path.read_bytes()).digest(),digest)
 def test_damaged_user_settings_are_not_read_during_update(self):
  settings=self.root/'etc/enigma2/gtiptvplayerpro-settings.json';settings.write_bytes(b'{invalid JSON')
  self.install();self.assertEqual(settings.read_bytes(),b'{invalid JSON')
 def test_unavailable_backup_folder_does_not_block_upgrade(self):
  (self.root/'etc/enigma2/settings').write_text('config.plugins.gtiptvplayerpro.backup_folder=/media/removed\n');self.install()
 def test_manual_backup_still_creates_a_verified_settings_archive(self):
  folder=self.root/'manual';folder.mkdir();result=self.manager.create(str(folder));manifest=self.manager.inspect(result['path'])
  self.assertEqual(manifest['purpose'],'settings');self.assertTrue(any(item['path'].endswith('accounts.json') for item in manifest['files']))
if __name__=='__main__':unittest.main(verbosity=2)

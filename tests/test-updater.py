"""Public fixture tests; never call a real installer or a network endpoint."""
import base64,contextlib,copy,hashlib,importlib.util,io,json,os,shutil,ssl,tempfile,threading,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
W=Path(__file__).resolve().parent;P=W.parent/'GTIPTVPlayerPro'
if 'CORE_FIXTURE' not in globals():CORE_FIXTURE=json.loads((W/'update-fixtures.json').read_text(encoding='utf-8'))
if 'CORE_PACKAGE' not in globals():
 CORE_PACKAGE='_r106_core_tests'
 spec=importlib.util.spec_from_file_location(CORE_PACKAGE,P/'__init__.py',submodule_search_locations=[str(P)])
 pkg=importlib.util.module_from_spec(spec);__import__('sys').modules[CORE_PACKAGE]=pkg;spec.loader.exec_module(pkg)
protocol=__import__(CORE_PACKAGE+'.update_protocol',fromlist=['*'])

# These public vectors use a deliberately separate test trust root.
# First prove that the production verifier rejects them, then inject test trust
# only into this isolated test module. The installed plugin is never changed.
_production_verify=protocol.verify_signature
for _body in CORE_FIXTURE['vectors'].values():
 _doc=json.loads(_body)
 assert _doc['key_id']!=protocol.KEY_ID
 assert not _production_verify(protocol.canonical(_doc['signed']),base64.b64decode(_doc['signature']))
_trust=CORE_FIXTURE['test_trust'];protocol.KEY_ID=_trust['key_id']
protocol.verify_signature=lambda body,signature:_production_verify(body,signature,n=_trust['n'],e=_trust['e'])

packages=__import__(CORE_PACKAGE+'.update_package',fromlist=['*'])
service=__import__(CORE_PACKAGE+'.update_service',fromlist=['*'])
UpdateError=protocol.UpdateError
def vector(name='stable'):return CORE_FIXTURE['vectors'][name].encode()
def ipk(name='valid'):return base64.b64decode(CORE_FIXTURE['packages'][name])
def manifest(data=None):
 m=copy.deepcopy(CORE_FIXTURE['manifest'])
 if data is not None:m.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest())
 return m
class MemoryState:
 def __init__(self):self.data=dict(auto=True,channel='stable',last_check=0,skipped='',offer='',installed='',backup='',result='')
 def read(self):return self.data.copy()
 def write(self,**changes):self.data.update(changes);return self.read()
class FixtureTransport:
 def __init__(self):self.calls=[];self.body=vector();self.data=ipk();self.cancel_after=False;self.fail=False
 def transfer(self,url,maximum,cancel=None,sink=None,**kw):
  self.calls.append(url)
  if self.fail:raise UpdateError('network')
  if cancel is not None and cancel.is_set():raise UpdateError('cancelled')
  if sink:
   middle=len(self.data)//2;sink(self.data[:middle])
   if self.cancel_after:
    cancel.set();raise UpdateError('cancelled')
   sink(self.data[middle:]);return len(self.data)
  return self.body
class UpdateProtocolTests(unittest.TestCase):
 def test_reference_stable_signature_and_retired_source_rejected(self):
  m=protocol.verify_envelope(vector());self.assertEqual(m['version'],'1.7.0-r107')
  for requested in ('stable','test'):
   with self.assertRaises(UpdateError):protocol.verify_envelope(vector('test'),requested)
  with self.assertRaises(UpdateError):protocol.source_url('test')
 def test_every_signed_field_is_validated(self):
  for name in ('architecture','bytes_bool','big','installed_big','schema','repository','package','notes','url','sha','python','field'):
   with self.subTest(name=name),self.assertRaises(UpdateError):protocol.verify_envelope(vector(name),'stable')
 def test_wrong_channel_and_changed_notes_fail(self):
  with self.assertRaises(UpdateError):protocol.verify_envelope(vector('test'),'stable')
  value=json.loads(vector());value['signed']['notes']='tampered'
  with self.assertRaises(UpdateError):protocol.verify_envelope(protocol.canonical(value),'stable')
 def test_unknown_key_and_malformed_signatures_fail(self):
  for field,value in (('key_id','0'*64),('signature','!'*512),('signature','A'*512),('signature','a'*511)):
   body=json.loads(vector());body[field]=value
   with self.subTest(field=field,value=value[:8]),self.assertRaises(UpdateError):protocol.verify_envelope(protocol.canonical(body),'stable')
 def test_json_duplicate_keys_nan_utf8_depth_and_limit(self):
  for data in (b'{"x":1,"x":2}',b'{"x":NaN}',b'\xff',b'['*2000,b'x'*65537):
   with self.assertRaises(UpdateError):protocol.json_document(data)
 def test_numeric_versions_never_lexicographic(self):
  self.assertLess(protocol.version_tuple('1.7.0-r99'),protocol.version_tuple('1.7.0-r106'))
  self.assertLess(protocol.version_tuple('1.7.0-r999'),protocol.version_tuple('1.8.0-r0'))
  for value in ('v1.7.0-r107','1.7.0-r0106','1.7.0','1.7.0-r-1',True,'1.7.0-r1000000'):
   with self.assertRaises(UpdateError):protocol.version_tuple(value)
 def test_https_host_credentials_ports_and_redirects_restricted(self):
  for url in ('http://github.com/a','https://evil.example/a','https://github.com:444/a','https://u:p@github.com/a','https://github.com/a#x','https://github.com/a\n'):
   with self.assertRaises(UpdateError):protocol.safe_url(url)
  with self.assertRaises(UpdateError):protocol._Redirect().redirect_request(Mock(),None,302,'',{},'http://github.com/a')
 def test_transport_verifies_certificates_and_has_no_proxy_or_credentials(self):
  t=protocol.UpdateTransport();self.assertEqual(t.context.verify_mode,ssl.CERT_REQUIRED);self.assertTrue(t.context.check_hostname)
  response=Mock();response.headers={'Content-Length':'3'};response.geturl.return_value=protocol.source_url('stable');response.getcode.return_value=200
  response.read.side_effect=[b'abc',b''];response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
  t.opener=Mock();t.opener.open.return_value=response
  self.assertEqual(t.transfer(protocol.source_url('stable'),10),b'abc')
  req=t.opener.open.call_args.args[0];self.assertFalse(any(k.lower() in ('authorization','cookie') for k in req.headers))
  self.assertEqual(t.opener.open.call_args.kwargs['timeout'],10)
 def test_transport_length_limit_encoding_truncation_and_cancel(self):
  for headers,data in (({'Content-Length':'11'},[b'']),({'Content-Length':'3'},[b'ab',b'']),({'Content-Encoding':'gzip'},[b'']),({},[b'x'*11])):
   t=protocol.UpdateTransport();r=Mock();r.headers=headers;r.geturl.return_value=protocol.source_url('stable');r.getcode.return_value=200;r.read.side_effect=data
   r.__enter__=Mock(return_value=r);r.__exit__=Mock(return_value=False);t.opener=Mock();t.opener.open.return_value=r
   with self.assertRaises(UpdateError):t.transfer(protocol.source_url('stable'),10)
  cancel=threading.Event();cancel.set()
  with self.assertRaises(UpdateError):protocol.UpdateTransport().transfer(protocol.source_url('stable'),10,cancel)
 def test_transport_404_is_a_quiet_unconfigured_source(self):
  t=protocol.UpdateTransport();t.opener=Mock();t.opener.open.side_effect=protocol.HTTPError(protocol.source_url('stable'),404,'not found',{},None)
  with self.assertRaises(UpdateError) as error:t.transfer(protocol.source_url('stable'),10)
  self.assertEqual(error.exception.code,'source')
class UpdatePackageTests(unittest.TestCase):
 def test_real_fixture_identity_paths_and_signed_size(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'valid.ipk';p.write_bytes(ipk());result=packages.inspect_ipk(str(p),manifest());self.assertEqual(result['version'],'1.7.0-r107')
 def test_bad_container_paths_links_duplicate_or_identity(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.ipk'
   for name in CORE_FIXTURE['packages']:
    if name=='valid':continue
    data=ipk(name);p.write_bytes(data)
    with self.subTest(name=name),self.assertRaises(UpdateError):packages.inspect_ipk(str(p),manifest(data))
 def test_byte_hash_size_and_cancellation(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'test.ipk';p.write_bytes(ipk())
   for changes in ({'bytes':len(ipk())+1},{'sha256':'0'*64},{'installed_bytes':1}):
    with self.assertRaises(UpdateError):packages.inspect_ipk(str(p),dict(manifest(),**changes))
   event=threading.Event();event.set()
   with self.assertRaises(UpdateError):packages.inspect_ipk(str(p),manifest(),event)
class UpdateServiceTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.folder=self.tmp.name
  self.state=MemoryState();self.network=FixtureTransport();self.s=service.UpdateService(self.state,self.network,self.folder,current='1.7.0-r106')
  self.owner=patch.object(service.os,'geteuid',return_value=os.stat(self.folder).st_uid,create=True);self.owner.start();self.addCleanup(self.owner.stop)
 def offer(self):return self.s.check(clock=lambda:100000)
 def test_manual_check_and_cached_offer_never_install_or_fetch_ipk(self):
  with patch.object(service,'run_opkg') as run:
   offer=self.offer();self.assertEqual(offer,self.s.cached());self.assertEqual(len(self.network.calls),1);run.assert_not_called()
 def test_auto_throttle_opt_out_and_clock_rollback(self):
  self.offer();self.s.check(automatic=True,clock=lambda:100001);self.assertEqual(len(self.network.calls),1)
  self.state.write(auto=False);self.s.check(automatic=True,clock=lambda:200000);self.assertEqual(len(self.network.calls),1)
  self.state.write(auto=True);self.s.check(automatic=True,clock=lambda:200000);self.assertEqual(len(self.network.calls),2)
  self.s.check(automatic=True,clock=lambda:1);self.assertEqual(len(self.network.calls),3)
 def test_plugin_entry_refreshes_metadata_before_six_hours(self):
  self.offer();self.s.check(automatic=True,on_entry=True,clock=lambda:100001)
  self.assertEqual(len(self.network.calls),2)
  self.s.check(automatic=True,clock=lambda:100002);self.assertEqual(len(self.network.calls),2)
 def test_plugin_entry_still_respects_automatic_check_opt_out(self):
  self.offer();self.state.write(auto=False)
  self.s.check(automatic=True,on_entry=True,clock=lambda:100001)
  self.assertEqual(len(self.network.calls),1)
 def test_older_current_and_pending_restart_block_repeat_install(self):
  self.s.current='1.7.0-r107';self.assertIsNone(self.offer());self.assertIsNone(self.s.cached())
  self.s.current='1.7.0-r106';offer=self.offer();self.state.write(installed='1.7.0-r107')
  with self.assertRaises(UpdateError):self.s.verified_offer(offer)
 def test_legacy_channel_state_never_changes_the_network_source(self):
  original=self.network.transfer
  def transfer(*a,**k):self.state.write(channel='test',offer='');return original(*a,**k)
  self.network.transfer=transfer
  self.assertIsNotNone(self.offer())
  self.assertTrue(self.network.calls[-1].endswith('/updates/stable.json'))
 def test_download_verify_reuse_without_installation(self):
  offer=self.offer();progress=[]
  with patch.object(service,'run_opkg') as run:
   p=self.s.download(offer,progress=lambda a,b:progress.append((a,b)));self.assertEqual(Path(p).read_bytes(),ipk());run.assert_not_called()
   self.assertEqual(self.s.download(offer),p);self.assertEqual(len(self.network.calls),2);self.assertEqual(progress[-1],(len(ipk()),len(ipk())))
 def test_cancel_and_network_failure_remove_only_partial_files(self):
  offer=self.offer();self.network.cancel_after=True
  with self.assertRaises(UpdateError):self.s.download(offer,cancel=threading.Event())
  self.assertFalse(list(Path(self.folder).glob('*.part')));self.assertFalse(list(Path(self.folder).glob('*.ipk')))
 def test_corrupted_download_and_cached_bytes_are_rechecked(self):
  offer=self.offer();self.network.data=b'bad'
  with self.assertRaises(UpdateError):self.s.download(offer)
  self.network.data=ipk();p=self.s.download(offer);Path(p).write_bytes(b'tamper');self.s.download(offer);self.assertEqual(Path(p).read_bytes(),ipk())
 def test_disk_space_and_channel_mismatch_block_download(self):
  offer=self.offer()
  with patch.object(service.shutil,'disk_usage',return_value=types.SimpleNamespace(free=1)),self.assertRaises(UpdateError):self.s.download(offer)
  invalid=dict(offer,body=vector('test'))
  with self.assertRaises(UpdateError):self.s.download(invalid)
 def install_call(self,**kw):
  offer=self.offer();p=self.s.download(offer)
  with patch.object(self.s,'installation_lock',side_effect=lambda:contextlib.nullcontext()):
   return self.s.install(offer,p,busy=kw.pop('busy',lambda:False),runner=kw.pop('runner',Mock(return_value=0)),uid=kw.pop('uid',0),root=self.folder,**kw)
 def test_confirmed_installer_only_specific_plugin_and_persists_restart(self):
  runner=Mock(return_value=0);result=self.install_call(runner=runner)
  path,log=runner.call_args.args;self.assertEqual(path,self.s.path_for(manifest()));self.assertTrue(log.endswith('install.log'))
  self.assertNotIn('backup',result);self.assertFalse(list(Path(self.folder).glob('*.gtbackup')));self.assertEqual(self.state.read()['installed'],'1.7.0-r107')
 def test_busy_or_not_root_or_unknown_activity_never_runs_installer(self):
  for params in ({'busy':lambda:True},{'uid':1},{'busy':lambda:'activity'}):
   runner=Mock(return_value=0)
   with self.assertRaises(UpdateError):self.install_call(runner=runner,**params)
   runner.assert_not_called()
 def test_second_activity_guard_before_package_manager(self):
  runner=Mock();busy=Mock(side_effect=[False,True])
  with self.assertRaises(UpdateError):self.install_call(runner=runner,busy=busy)
  self.assertEqual(busy.call_count,2);runner.assert_not_called()
 def test_flash_space_is_rechecked_before_opkg_without_creating_a_backup(self):
  runner=Mock(return_value=0)
  with patch.object(service.shutil,'disk_usage',side_effect=[types.SimpleNamespace(free=10**9),types.SimpleNamespace(free=10**9),types.SimpleNamespace(free=1)]),self.assertRaises(UpdateError) as error:
   self.install_call(runner=runner)
  self.assertEqual(error.exception.code,'space');runner.assert_not_called()
 def test_opkg_failure_is_visible_and_keeps_failed_state(self):
  for effect in (1,OSError('failed')):
   run=Mock(return_value=effect) if isinstance(effect,int) else Mock(side_effect=effect)
   with self.assertRaises(UpdateError):self.install_call(runner=run)
   self.assertEqual(self.state.read()['result'],'install-failed');self.assertEqual(self.state.read()['backup'],'')
 def test_cached_download_reports_verified_completion_without_another_transfer(self):
  offer=self.offer();self.s.download(offer);events=[];progress=[];calls=len(self.network.calls)
  self.s.download(offer,progress=lambda a,b:progress.append((a,b)),feedback=lambda *e:events.append(e))
  self.assertEqual(len(self.network.calls),calls);self.assertEqual(progress[-1],(len(ipk()),len(ipk())))
  self.assertEqual([e[0] for e in events],['verify','downloaded'])
 def test_installer_completion_follows_persisted_success_and_failures_never_complete(self):
  events=[]
  def progress(phase,done,total):events.append((phase,self.state.read()['result']))
  self.install_call(progress=progress)
  self.assertEqual([e[0] for e in events],['verify','verify','install','complete'])
  self.assertEqual(events[-1],('complete','installed'))
  self.state=MemoryState();self.s.state=self.state;events.clear()
  with self.assertRaises(UpdateError):self.install_call(progress=progress,runner=Mock(return_value=1))
  self.assertNotIn('complete',[e[0] for e in events]);self.assertEqual(self.state.read()['result'],'install-failed')
 def test_atomic_state_preserves_options_and_private_mode(self):
  state=service.UpdateState(str(Path(self.folder)/'state/settings.json'));self.assertTrue(state.read()['auto'])
  state.write(auto=False);state.write(skipped='1.7.0-r107');self.assertEqual(state.read()['channel'],'stable');self.assertFalse(state.read()['auto'])
  self.assertFalse(list(Path(self.folder).rglob('.update-state-*')))
 def test_legacy_test_settings_migrate_and_preserve_other_preferences(self):
  path=Path(self.folder)/'state.json';path.write_text(json.dumps(dict(auto=False,channel='test',last_check=999999,offer=vector('test').decode(),skipped='1.7.0-r107',installed='1.7.0-r105',backup='/saved/user.gtbackup',result='installed')))
  state=service.UpdateState(str(path));data=state.read();self.assertEqual(data['channel'],'stable');self.assertFalse(data['auto']);self.assertEqual(data['last_check'],0);self.assertEqual(data['offer'],'');self.assertEqual(data['skipped'],'');self.assertEqual(data['backup'],'/saved/user.gtbackup');self.assertEqual(data['installed'],'1.7.0-r105')
  state.write(last_check=1);self.assertEqual(json.loads(path.read_text())['channel'],'stable')
 def test_state_cannot_persist_a_retired_channel(self):
  state=service.UpdateState(str(Path(self.folder)/'state.json'))
  with self.assertRaises(UpdateError):state.write(channel='test')
  self.assertFalse(Path(state.path).exists())
 def test_old_test_offer_is_rejected_even_with_matching_legacy_channel(self):
  self.state.write(channel='test',offer=vector('test').decode());self.assertIsNone(self.s.cached())
  with self.assertRaises(UpdateError):self.s.verified_offer(dict(body=vector('test')))
 def test_cache_retention_only_removes_owned_package_names(self):
  paths=[]
  for i in range(4):
   p=Path(self.folder)/(str(i)*64+'.ipk');p.write_bytes(b'old');os.utime(p,ns=(i+1,i+1));paths.append(p)
  unrelated=Path(self.folder)/'user.ipk';unrelated.write_bytes(b'user');self.s._trim_cache(str(paths[-1]))
  self.assertFalse(paths[0].exists());self.assertFalse(paths[1].exists());self.assertTrue(paths[2].exists());self.assertTrue(paths[3].exists());self.assertTrue(unrelated.exists())
 def test_package_is_revalidated_after_activity_check_before_opkg(self):
  runner=Mock(return_value=0)
  def busy():
   Path(self.s.path_for(manifest())).write_bytes(b'tampered during activity check');return False
  with self.assertRaises(UpdateError):self.install_call(busy=busy,runner=runner)
  runner.assert_not_called()
 def test_state_rejects_untrusted_json(self):
  path=Path(self.folder)/'settings.json';path.write_bytes(b'{"auto":true,"auto":false}')
  with self.assertRaises(UpdateError):service.UpdateState(str(path)).read()
 def test_real_opkg_runner_is_fixed_argv_and_waits_without_kill(self):
  process=Mock();process.wait.return_value=0;log=str(Path(self.folder)/'log')
  with patch.object(service.os.path,'isfile',return_value=True),patch.object(service.os,'access',return_value=True),patch.object(service.subprocess,'Popen',return_value=process) as popen:
   self.assertEqual(service.run_opkg('/cache/verified.ipk',log),0)
  self.assertEqual(popen.call_args.args[0],['/usr/bin/opkg','install','/cache/verified.ipk']);self.assertNotIn('shell',popen.call_args.kwargs)
  process.wait.assert_called_once();process.kill.assert_not_called();process.terminate.assert_not_called()
 def test_successful_install_is_not_reported_failed_when_final_state_write_fails(self):
  write=self.state.write
  def save(**changes):
   if 'installed' in changes:raise OSError('flash status write failed')
   return write(**changes)
  self.state.write=save;runner=Mock(return_value=0);events=[]
  result=self.install_call(runner=runner,progress=lambda *e:events.append(e))
  self.assertEqual(result['warning'],'state');self.assertEqual(result['version'],'1.7.0-r107')
  self.assertEqual(events[-1],('complete',1,1));runner.assert_called_once()
 def test_activity_reason_is_retained_for_the_translated_ui(self):
  for reason in ('recording','downloading','activity'):
   runner=Mock(return_value=0)
   with self.assertRaises(UpdateError) as caught:self.install_call(busy=lambda:reason,runner=runner)
   self.assertEqual(caught.exception.reason,reason);runner.assert_not_called()
 def test_noncritical_cache_cleanup_does_not_fail_verified_download(self):
  offer=self.offer()
  with patch.object(service.os,'listdir',side_effect=PermissionError):path=self.s.download(offer)
  self.assertEqual(Path(path).read_bytes(),ipk())
if __name__=='__main__':unittest.main(verbosity=2)

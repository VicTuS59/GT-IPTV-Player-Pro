"""Actual updater error routing, all 43 catalogues and both image keymaps."""
import ast,errno,gettext,importlib.util,json,socket,ssl,sys,tempfile,textwrap,types,unittest
from pathlib import Path
from string import Formatter
from unittest.mock import Mock,patch
from urllib.error import HTTPError,URLError

ROOT=Path(__file__).resolve().parents[1];P=ROOT/'GTIPTVPlayerPro';NAME='_update_error_regressions'
spec=importlib.util.spec_from_file_location(NAME,P/'__init__.py',submodule_search_locations=[str(P)])
pkg=importlib.util.module_from_spec(spec);sys.modules[NAME]=pkg;spec.loader.exec_module(pkg)
errors=__import__(NAME+'.update_errors',fromlist=['*'])
protocol=__import__(NAME+'.update_protocol',fromlist=['*'])
service=__import__(NAME+'.update_service',fromlist=['*'])
i18n=__import__(NAME+'.i18n',fromlist=['*'])
TREE=ast.parse((P/'update_ui.py').read_text())
CONTROLLER=next(n for n in TREE.body if isinstance(n,ast.ClassDef) and n.name=='GTSoftwareUpdateScreen')


class FailureTests(unittest.TestCase):
 def test_network_causes_are_classified_without_showing_raw_exception_text(self):
  for cause,reason in ((socket.gaierror(-2,'private-host'),'dns'),(TimeoutError('private-url'),'timeout'),(ssl.SSLCertVerificationError(1,'private-url'),'certificate'),(ConnectionError('private-url'),'network')):
   error=errors.network_error(URLError(cause));self.assertEqual(error.reason,reason)
   self.assertNotIn('private',errors.format_error(error,lambda s:s))
 def test_http_error_contains_status_and_interrupted_download_is_distinct(self):
  transport=protocol.UpdateTransport();transport.opener=Mock()
  transport.opener.open.side_effect=HTTPError(protocol.source_url(),503,'private',{},None)
  with self.assertRaises(errors.UpdateError) as caught:transport.transfer(protocol.source_url(),32)
  self.assertEqual(caught.exception.reason,'http');self.assertIn('503',errors.format_error(caught.exception,lambda s:s))
  response=Mock();response.headers={'Content-Length':'3'};response.geturl.return_value=protocol.source_url();response.getcode.return_value=200
  response.read.side_effect=[b'ab',b''];response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
  transport.opener=Mock();transport.opener.open.return_value=response
  with self.assertRaises(errors.UpdateError) as caught:transport.transfer(protocol.source_url(),32)
  self.assertEqual(caught.exception.reason,'interrupted')
 def test_storage_reasons_keep_space_permissions_and_readonly_distinct(self):
  for number,code,reason in ((errno.ENOSPC,'space','space'),(errno.EROFS,'storage','readonly'),(errno.EACCES,'storage','permission')):
   error=errors.storage_error(OSError(number,'secret'),'/tmp/updates');self.assertEqual((error.code,error.reason),(code,reason))
   self.assertNotIn('secret',errors.format_error(error,lambda s:s))
 def test_free_space_message_contains_required_available_and_target(self):
  with patch.object(errors.shutil,'disk_usage',return_value=types.SimpleNamespace(free=1024)),self.assertRaises(errors.UpdateError) as caught:
   errors.require_space('/tmp',16*1024*1024)
  self.assertEqual(caught.exception.context,dict(path='/tmp',required=16*1024*1024,available=1024))
  text=errors.format_error(caught.exception,lambda s:s);self.assertIn('Required space: 16.0 MiB',text);self.assertIn('Storage: /tmp',text)
 def test_opkg_log_categories_are_specific_and_do_not_echo_credentials(self):
  cases=[(' * pkg_hash_check_unresolved: cannot find dependency python3-example for gtiptv','dependency'),
         ('cannot satisfy the following dependencies:','dependency'),('check_data_file_clashes: conflict','conflict'),
         ('Could not lock /var/lib/opkg/lock: Resource temporarily unavailable','opkg_lock'),
         ('postinst script returned status 1','configure'),('No space left on device','space'),
         ('Read-only file system','readonly'),('Permission denied','permission'),('unexpected output','install')]
  with tempfile.TemporaryDirectory() as directory:
   log=Path(directory)/'install.log'
   for text,reason in cases:
    log.write_text(text+'\nhttps://user:password@private.invalid/path?token=secret')
    error=errors.installation_error(str(log),1);self.assertEqual(error.reason,reason)
    message=errors.format_error(error,lambda s:s)
    self.assertNotIn('password',message);self.assertNotIn('secret',message)
    if reason=='dependency' and 'python3-example' in text:self.assertIn('python3-example',message)
 def test_opkg_reports_the_filesystem_and_space_it_actually_checked(self):
  with tempfile.TemporaryDirectory() as directory:
   log = Path(directory) / 'install.log'
   log.write_text('error: verify_pkg_installable: Only have 2048kb available on filesystem /usr, pkg enigma2-plugin-extensions-gtiptvplayerpro needs 6144\n')
   error = errors.installation_error(str(log), 1)
   self.assertEqual(error.code, 'space')
   self.assertEqual(error.context, dict(log=str(log), exit_code=1, path='/usr', available=2 * 1024 ** 2, required=6 * 1024 ** 2))
   log.write_text('No space left on device\n')
   self.assertNotIn('path', errors.installation_error(str(log)).context)
 def test_bad_update_state_identifies_settings_instead_of_bad_server_metadata(self):
  with tempfile.TemporaryDirectory() as directory:
   path=Path(directory)/'update-settings.json';path.write_text('{bad json')
   with self.assertRaises(errors.UpdateError) as caught:service.UpdateState(str(path)).read()
   self.assertEqual(caught.exception.reason,'state');self.assertEqual(caught.exception.context['path'],str(path))
 def test_unknown_exception_gets_a_localized_reason_and_stage(self):
  @errors.reported('download')
  def fail():raise ValueError('user=password')
  with self.assertRaises(errors.UpdateError) as caught:fail()
  self.assertEqual(caught.exception.stage,'download');self.assertEqual(caught.exception.code,'failed')
  self.assertNotIn('password',errors.format_error(caught.exception,lambda s:s))


class LanguageTests(unittest.TestCase):
 def test_every_error_reason_exists_and_formats_in_all_43_languages(self):
  paths=list((P/'locale').glob('*/LC_MESSAGES/GTIPTVPlayerPro.mo'));self.assertEqual(len(paths),43)
  keys=set(errors.ERROR_TEXTS.values())|{errors.POST_INSTALL_WARNING,'Required space','Free space','Storage','Error'}
  for path in paths:
   with path.open('rb') as handle:catalog=gettext.GNUTranslations(handle)
   for source in keys:
    with self.subTest(language=path.parent.parent.name,message=source):
     translated=catalog._catalog.get(source);self.assertTrue(translated)
     self.assertEqual(sorted(n for _,n,_,_ in Formatter().parse(source) if n is not None),sorted(n for _,n,_,_ in Formatter().parse(translated) if n is not None))
   for reason in errors.ERROR_TEXTS:
    text=errors.format_error(errors.UpdateError('install',reason,status=503,package='python3-example',required=32*1024*1024,available=1024,path='/'),catalog.gettext)
    self.assertTrue(text);self.assertNotIn('{package}',text);self.assertNotIn('{status}',text)
 def test_runtime_device_language_and_regional_aliases_use_compiled_messages(self):
  i18n._CATALOGS.clear();key=errors.ERROR_TEXTS['network']
  for code in i18n.SUPPORTED_LANGUAGE_CODES:
   text=i18n.translate_for_language(key,code)
   if code not in ('en','en_AU','en_GB'):self.assertNotEqual(text,key,code)
   else:self.assertEqual(text,key)


def controller(method_names):
 methods=[n for n in CONTROLLER.body if isinstance(n,ast.FunctionDef) and n.name in method_names]
 namespace=dict(textwrap=textwrap,format_error=errors.format_error,_=lambda s:s,STAGE_TEXTS=errors.STAGE_TEXTS,
                LABELS={'title':'Software update','failed':'Update failed','busy':'Stop recordings'},
                MessageBox=types.SimpleNamespace(TYPE_ERROR=0))
 exec(compile(ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[])),'actual-update-ui-methods','exec'),namespace)
 return type('ActualUpdateController',(),{n.name:namespace[n.name] for n in methods})()


class RemoteAndNoticeTests(unittest.TestCase):
 def test_channel_keys_page_notes_on_both_actual_image_mapping_variants(self):
  screen=controller({'_note_pages','notes_up','notes_down'})
  screen._offer=dict(manifest=dict(notes='\n'.join('Release note {}'.format(i) for i in range(40))))
  screen._notes_offset=0;screen._render=Mock()
  init=next(n for n in CONTROLLER.body if isinstance(n,ast.FunctionDef) and n.name=='__init__')
  call=next(n.value for n in ast.walk(init) if isinstance(n,ast.Assign) and isinstance(n.value,ast.Call) and isinstance(n.value.func,ast.Name) and n.value.func.id=='ActionMap')
  for node in ast.walk(call):
   if isinstance(node,ast.Attribute) and isinstance(node.value,ast.Name) and node.value.id=='self' and not hasattr(screen,node.attr):setattr(screen,node.attr,Mock())
  contexts,actions=eval(compile(ast.Expression(call),'<actual-updater-actions>','eval'),dict(self=screen,ActionMap=lambda contexts,actions,priority:(contexts,actions)))
  maps=json.loads((ROOT/'tests/update-keymaps.json').read_text())
  # These mapto names are extracted from OpenPLi/develop and OpenATV/master
  # data/keymap.xml; no receiver keymap is changed by the plugin.
  for image,image_maps in maps.items():
   screen._notes_offset=0
   for key,expected in (('KEY_CHANNELUP',1),('KEY_CHANNELDOWN',0)):
    for context in contexts:
     action=image_maps.get(context,{}).get(key)
     if action in actions:actions[action]();break
    else:self.fail('Unmapped key: '+image+' / '+key)
    self.assertEqual(screen._notes_offset,expected,image+' / '+key)
  for alias in ('channelUp','nextBouquet','chplus'):
   screen._notes_offset=0;actions[alias]();self.assertEqual(screen._notes_offset,1)
  for alias in ('channelDown','prevBouquet','chminus'):
   screen._notes_offset=1;actions[alias]();self.assertEqual(screen._notes_offset,0)
  screen._notes_offset=0;screen.notes_up();self.assertEqual(screen._notes_offset,0)
  screen._notes_offset=len(screen._note_pages())-1;screen.notes_down();self.assertEqual(screen._notes_offset,len(screen._note_pages())-1)
 def test_error_popup_waits_for_native_modal_handoff_and_retains_reason(self):
  screen=controller({'_error','_show_error'})
  screen._error_stage='install';screen._error_timer=Mock();screen._closed=False;screen._busy='';screen.execing=False
  screen.session=types.SimpleNamespace(in_exec=False,current_dialog=screen,open=Mock())
  screen._error(errors.UpdateError('install','dependency',package='python3-example'))
  self.assertIn('python3-example',screen._detail);screen._show_error();screen.session.open.assert_not_called()
  screen.execing=True;screen.session.in_exec=True;screen._show_error();screen.session.open.assert_called_once()
  self.assertIn('python3-example',screen.session.open.call_args.args[1]);self.assertEqual(screen._error_pending,'')
 def test_error_popup_retries_a_native_race_and_never_opens_after_close(self):
  screen=controller({'_show_error'});screen._error_timer=Mock();screen._closed=False;screen._busy='';screen.execing=True
  screen._error_pending='translated reason';screen._error_attempts=0
  screen.session=types.SimpleNamespace(in_exec=True,current_dialog=screen,open=Mock(side_effect=[RuntimeError('handoff'),None]))
  screen._show_error();self.assertEqual(screen._error_pending,'translated reason');screen._show_error();self.assertEqual(screen._error_pending,'')
  screen._closed=True;screen._error_pending='late';screen._show_error();self.assertEqual(screen.session.open.call_count,2)


if __name__=='__main__':unittest.main(verbosity=2)

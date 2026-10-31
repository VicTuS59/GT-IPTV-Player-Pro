"""Bounded update phase feedback, real harmless child logs, and catalogue checks."""
import ast,gettext,importlib.util,io,json,os,subprocess,sys,tempfile,types,unittest
from pathlib import Path
from unittest.mock import Mock,patch
if 'PLUGIN_ROOT' not in globals():PLUGIN_ROOT=Path(__file__).resolve().parents[1]/'GTIPTVPlayerPro'
if 'PACKAGE_NAME' not in globals():
 PACKAGE_NAME='_feedback_tests';spec=importlib.util.spec_from_file_location(PACKAGE_NAME,PLUGIN_ROOT/'__init__.py',submodule_search_locations=[str(PLUGIN_ROOT)])
 pkg=importlib.util.module_from_spec(spec);sys.modules[PACKAGE_NAME]=pkg;spec.loader.exec_module(pkg)
feedback=__import__(PACKAGE_NAME+'.update_feedback',fromlist=['*']);service=__import__(PACKAGE_NAME+'.update_service',fromlist=['*'])

class FeedbackTests(unittest.TestCase):
 def test_real_opkg_log_phases_reject_other_packages_and_never_claim_complete(self):
  name=feedback.PACKAGE
  for line,phase in (('Upgrading '+name+' (1.7.0-r109) to '+name+' (1.7.0) on root','install'),('Configuring '+name+'.','configure'),('Configuring unrelated. ',None),('Collected errors:',None),('Successfully installed',None)):
   self.assertEqual(feedback.installer_phase(line),phase)
 def test_indeterminate_progress_moves_and_never_uses_a_completion_percentage(self):
  seen=set()
  for tick in range(90):
   x,width,percent=feedback.progress_geometry(0,0,tick);self.assertIsNone(percent);self.assertGreaterEqual(x,0);self.assertLessEqual(x+width,100);seen.add(x)
  self.assertGreater(len(seen),10)
 def test_byte_progress_is_bounded_and_completion_requires_real_total(self):
  self.assertEqual(feedback.progress_geometry(25,100),(0,25,25))
  self.assertEqual(feedback.progress_geometry(100,100),(0,100,100))
  self.assertEqual(feedback.progress_geometry(-1,100),(0,0,0))
  self.assertEqual(feedback.progress_geometry(101,100),(0,100,100))
 def test_feedback_failure_does_not_interrupt_package_manager(self):
  def fail(*args):raise RuntimeError('closed consumer')
  feedback.emit(fail,'install');feedback.emit(None,'install')
 def child(self,fail_consumer=False,exit_code=0):
  # Execute a harmless Python child, while asserting that the production
  # launcher requested exactly opkg install with the verified filename.
  real_popen=subprocess.Popen;events=[];name=feedback.PACKAGE
  program='import sys,time;print('+repr('Upgrading '+name+' (old) to '+name+' (new) on root')+',flush=True);time.sleep(.25);print('+repr('Configuring '+name+'.')+',flush=True);sys.exit('+str(exit_code)+')'
  def launch(argv,**kwargs):
   self.assertEqual(argv,['/usr/bin/opkg','install','/fixture/verified.ipk'])
   self.assertNotIn('shell',kwargs)
   return real_popen([sys.executable,'-u','-c',program],**kwargs)
  def progress(*event):
   events.append(event)
   if fail_consumer:raise RuntimeError('closed UI')
  with tempfile.TemporaryDirectory() as d,patch.object(service.os.path,'isfile',return_value=True),patch.object(service.os,'access',return_value=True),patch.object(service.subprocess,'Popen',side_effect=launch):
   code=service.run_opkg('/fixture/verified.ipk',str(Path(d)/'install.log'),progress=progress)
  return code,events
 def test_harmless_child_emits_real_log_stages_without_kill_or_pipe_backpressure(self):
  code,events=self.child();self.assertEqual(code,0);self.assertEqual([e[0] for e in events],['install','configure'])
 def test_failed_child_never_reports_complete(self):
  code,events=self.child(exit_code=2);self.assertEqual(code,2);self.assertNotIn('complete',[e[0] for e in events])
 def test_closed_feedback_consumer_does_not_abort_running_child(self):
  code,events=self.child(fail_consumer=True);self.assertEqual(code,0);self.assertEqual([e[0] for e in events],['install','configure'])
 def test_three_stage_captions_exist_in_all_43_languages(self):
  if 'CATALOGUES' in globals():bodies=CATALOGUES
  else:bodies=[p.read_bytes() for p in (PLUGIN_ROOT/'locale').glob('*/LC_MESSAGES/GTIPTVPlayerPro.mo')]
  self.assertEqual(len(bodies),43)
  for raw in bodies:
   catalog=gettext.GNUTranslations(io.BytesIO(raw))
   for key in ('Verifying update...','Installing update...','Configuring update...'):self.assertTrue(catalog._catalog.get(key))

class RestartHandoffTests(unittest.TestCase):
 def setUp(self):
  # Compile the actual controller methods without importing a GUI binding.
  # The native session model refuses modal opens during confirmation handoff.
  source=UI_SOURCE if 'UI_SOURCE' in globals() else (PLUGIN_ROOT/'update_ui.py').read_text(encoding='utf-8')
  tree=ast.parse(source);controller=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='GTSoftwareUpdateScreen')
  names={'_ask_restart','_resumed','_restart_ready','_restart'}
  methods=[n for n in controller.body if isinstance(n,ast.FunctionDef) and n.name in names]
  namespace=dict(__package__=PACKAGE_NAME,PLUGIN_VERSION='1.0.0-r0',effective_version=lambda state:state['installed'],receiver_busy=lambda session:False,message=lambda key:key,MessageBox=types.SimpleNamespace(TYPE_YESNO=1))
  exec(compile(ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[])),'actual-update-restart-methods','exec'),namespace)
  cls=type('ActualRestartController',(),{n.name:namespace[n.name] for n in methods});self.s=s=cls()
  s._closed=False;s._busy='';s._restart_open=False;s._restart_pending=False;s._restart_confirmed=False;s._restart_attempts=0;s.execing=False;s._restart_timer=Mock();s._render=Mock()
  s.service=types.SimpleNamespace(state=types.SimpleNamespace(read=lambda:dict(installed='2.0.0-r0')))
  s.session=types.SimpleNamespace(in_exec=False,current_dialog=s,open=Mock(),openWithCallback=Mock())
  self.diag=__import__(PACKAGE_NAME+'.diagnostics',fromlist=['*']);self.logging=patch.object(self.diag,'log_event');self.logging.start();self.addCleanup(self.logging.stop)
 def test_automatic_question_survives_native_handoff_without_remote_key(self):
  s=self.s;s._ask_restart();s._restart_ready();s.session.openWithCallback.assert_not_called()
  s.execing=True;s.session.in_exec=True;s._resumed();s._restart_ready();s.session.openWithCallback.assert_called_once()
  self.assertFalse(s.session.openWithCallback.call_args.kwargs['default']);s.session.open.assert_not_called()
 def test_modal_open_race_retries_once_and_decline_never_restarts(self):
  s=self.s;s.execing=True;s.session.in_exec=True;s.session.openWithCallback.side_effect=[RuntimeError('modal open are allowed only from a screen which is modal!'),None]
  s._ask_restart();s._restart_ready();s._restart_ready();s._restart_ready();self.assertEqual(s.session.openWithCallback.call_count,2)
  s._restart(False);s._restart_ready();s.session.open.assert_not_called()
 def test_other_dialog_and_close_prevent_automatic_restart(self):
  s=self.s;s.execing=True;s.session.in_exec=True;s.session.current_dialog=object();s._ask_restart();s._restart_ready();s.session.openWithCallback.assert_not_called()
  s.session.current_dialog=s;s._closed=True;s._restart_ready();s.session.openWithCallback.assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)

"""Immediate, nonblocking entry checks and modal-safe shared notifications."""
import ast,gettext,importlib.util,io,sys,threading,types,unittest
from pathlib import Path
from unittest.mock import Mock
P=Path(__file__).resolve().parents[1]/'GTIPTVPlayerPro';NAME='_entry_update_checks'
spec=importlib.util.spec_from_file_location(NAME,P/'__init__.py',submodule_search_locations=[str(P)])
pkg=importlib.util.module_from_spec(spec);sys.modules[NAME]=pkg;spec.loader.exec_module(pkg)
service=__import__(NAME+'.update_service',fromlist=['*'])
loader=__import__(NAME+'.source_loader',fromlist=['*'])
tree=ast.parse((P/'update_ui.py').read_bytes())
nodes=[node for node in tree.body if isinstance(node,(ast.ClassDef,ast.FunctionDef)) and node.name in
       ('UpdateMonitor','UpdateNotice','attach_update_notice')]
OFFER=dict(manifest=dict(version='100.0.0-r0'))

class Timer:
    def __init__(self):self.callback=[];self.starts=[];self.active=False
    def start(self,*args):self.starts.append(args);self.active=True
    def stop(self):self.active=False

class State:
    def __init__(self):self.value=dict(auto=True,skipped='',installed='')
    def read(self):return dict(self.value)
    def write(self,**changes):self.value.update(changes)

class Service:
    def __init__(self):self.state=State();self.calls=[]
    def check(self,**kw):self.calls.append(kw);return OFFER

class Reader:
    def __init__(self,timer,connect,apply):self.apply=apply;self.jobs=[];self.closed=False
    def request(self,job):self.jobs.append(job)
    def close(self):self.closed=True
    def finish(self,value=OFFER,error=None):self.apply(value,error)
    def run(self):self.apply(self.jobs[-1](),None)

def connect(timer,callback):timer.callback.append(callback)

def namespace(reader=Reader,factory=Service):
    ns=dict(threading=threading,eTimer=Timer,connect=connect,SourceLoader=reader,UpdateService=factory,
            version_tuple=service.version_tuple,effective_version=service.effective_version,
            MessageBox=types.SimpleNamespace(TYPE_YESNO=1),message=lambda key:'Update available. Open updater?',
            GTSoftwareUpdateScreen=object())
    exec(compile(ast.fix_missing_locations(ast.Module(body=nodes,type_ignores=[])),'actual-entry-update-controller','exec'),ns)
    return ns

class StartupTests(unittest.TestCase):
    def setUp(self):
        self.ns=namespace()
        self.session=types.SimpleNamespace(current_dialog=None,in_exec=True,open=Mock(),openWithCallback=Mock())
    def screen(self,entry=False):
        screen=types.SimpleNamespace(session=self.session,onShown=[],onClose=[],execing=True,
                                     _child_screen_open=False,_dashboard_child_open=False)
        self.ns['attach_update_notice'](screen,entry=entry)
        self.session.current_dialog=screen
        self.addCleanup(screen._update_notice._stop)
        return screen
    def show(self,screen):
        self.session.current_dialog=screen
        for callback in screen.onShown:callback()
        return screen._update_notice.monitor
    def test_constructor_has_no_request_and_shown_starts_without_five_second_timer(self):
        root=self.screen(entry=True);monitor=root._update_notice.monitor
        self.assertEqual(monitor.reader.jobs,[])
        self.show(root)
        self.assertEqual(len(monitor.reader.jobs),1)
        self.assertEqual(monitor.timer.starts,[(21600000,True)])
        monitor.reader.run()
        self.assertTrue(monitor.service.calls[0]['on_entry'])
        self.assertTrue(monitor.service.calls[0]['automatic'])
        self.assertIs(monitor.service.calls[0]['cancel'],monitor.cancel)
    def test_repeated_shown_and_welcome_dashboard_share_one_request(self):
        root=self.screen(entry=True);monitor=self.show(root)
        dashboard=self.screen();self.assertIs(dashboard._update_notice.monitor,monitor)
        self.show(dashboard);self.show(root);self.show(dashboard)
        self.assertEqual(len(monitor.reader.jobs),1)
        monitor.reader.run()
        callback=self.session.openWithCallback.call_args.args[0]
        self.assertIs(callback.__self__,dashboard._update_notice)
        self.session.open.assert_not_called()
    def test_automatic_login_and_welcome_close_do_not_discard_result(self):
        root=self.screen(entry=True);monitor=self.show(root)
        dashboard=self.screen();self.show(dashboard);root._update_notice._stop()
        self.assertFalse(monitor.closed);self.assertFalse(monitor.cancel.is_set())
        monitor.reader.run();self.session.openWithCallback.assert_called_once()
        self.assertIs(self.session.openWithCallback.call_args.args[0].__self__,dashboard._update_notice)
    def test_child_screen_defers_notice_until_return_without_another_query(self):
        root=self.screen(entry=True);monitor=self.show(root)
        root._child_screen_open=True;self.session.current_dialog=object()
        monitor.reader.run();self.session.openWithCallback.assert_not_called()
        root._child_screen_open=False;self.show(root)
        self.session.openWithCallback.assert_called_once();self.assertEqual(len(monitor.reader.jobs),1)
    def test_busy_metadata_check_retries_entry_without_another_five_second_delay(self):
        monitor=self.show(self.screen(entry=True))
        monitor.reader.finish(None,service.UpdateError('busy'))
        self.assertEqual(monitor.timer.starts[-1],(250,True))
        monitor._check();monitor.reader.run()
        self.assertTrue(monitor.service.calls[-1]['on_entry'])
    def test_periodic_check_uses_the_normal_six_hour_policy(self):
        monitor=self.show(self.screen(entry=True));monitor.reader.finish(None)
        monitor._check();monitor.reader.run()
        self.assertFalse(monitor.service.calls[-1]['on_entry'])
        self.assertEqual(monitor.timer.starts[-1],(21600000,True))
    def test_opt_out_and_previously_declined_version_do_not_prompt(self):
        monitor=self.show(self.screen(entry=True));monitor.service.state.write(auto=False)
        monitor.reader.run();self.session.openWithCallback.assert_not_called()
        monitor.service.state.write(auto=True,skipped=OFFER['manifest']['version'])
        monitor._notify();self.session.openWithCallback.assert_not_called()
    def test_pending_installed_version_is_not_offered_again(self):
        monitor=self.show(self.screen(entry=True));monitor.service.state.write(installed=OFFER['manifest']['version'])
        monitor.reader.run();self.session.openWithCallback.assert_not_called()
    def test_modal_open_failure_retries_without_marking_version_as_skipped(self):
        monitor=self.show(self.screen(entry=True))
        self.session.openWithCallback.side_effect=RuntimeError('native modal handoff')
        monitor.reader.run();self.assertEqual(monitor.service.state.read()['skipped'],'')
        self.assertFalse(monitor.prompt_open);self.assertEqual(monitor.notice_timer.starts[-1],(100,True))
        self.session.openWithCallback.side_effect=None;monitor._notify()
        self.assertEqual(monitor.service.state.read()['skipped'],OFFER['manifest']['version'])
    def test_confirmation_opens_updater_after_native_handoff_and_never_installs(self):
        root=self.screen(entry=True);monitor=self.show(root);monitor.reader.run()
        root.execing=False;self.session.in_exec=False
        root._update_notice._reply(True);monitor._notify()
        self.session.open.assert_not_called()
        root.execing=True;self.session.in_exec=True;monitor._notify();monitor._notify()
        self.session.open.assert_called_once_with(self.ns['GTSoftwareUpdateScreen'])
    def test_decline_keeps_menu_and_does_not_reopen_notice(self):
        root=self.screen(entry=True);monitor=self.show(root);monitor.reader.run()
        root._update_notice._reply(False);self.show(root)
        self.session.open.assert_not_called();self.session.openWithCallback.assert_called_once()
    def test_last_screen_close_cancels_worker_and_ignores_late_results(self):
        root=self.screen(entry=True);monitor=self.show(root);root._update_notice._stop()
        self.assertTrue(monitor.closed);self.assertTrue(monitor.cancel.is_set());self.assertTrue(monitor.reader.closed)
        self.assertFalse(monitor.timer.active);self.assertFalse(monitor.notice_timer.active)
        monitor.reader.finish();self.session.openWithCallback.assert_not_called()
    def test_next_real_launch_starts_a_fresh_request(self):
        root=self.screen(entry=True);first=self.show(root);root._update_notice._stop()
        second=self.show(self.screen(entry=True))
        self.assertIsNot(first,second);self.assertEqual(len(second.reader.jobs),1)
    def test_real_source_loader_does_network_work_off_gui_thread(self):
        entered=threading.Event();allow=threading.Event();threads=[];gui=threading.get_ident()
        class BlockingService(Service):
            def check(self,**kw):
                threads.append(threading.get_ident());entered.set()
                if not allow.wait(2):raise RuntimeError('test worker was not released')
                return OFFER
        ns=namespace(reader=loader.SourceLoader,factory=BlockingService)
        root=types.SimpleNamespace(session=self.session,onShown=[],onClose=[],execing=True)
        ns['attach_update_notice'](root,entry=True);monitor=root._update_notice.monitor
        self.addCleanup(root._update_notice._stop);self.addCleanup(allow.set)
        self.session.current_dialog=root
        root.onShown[0]()
        self.assertTrue(entered.wait(1));self.assertFalse(allow.is_set())
        self.session.openWithCallback.assert_not_called()
        self.assertNotEqual(threads,[gui]);allow.set()
        with monitor.reader.condition:
            self.assertTrue(monitor.reader.condition.wait_for(lambda:monitor.reader.result is not None,timeout=2))
        monitor.reader.poll();self.session.openWithCallback.assert_called_once()
    def test_only_the_entry_screen_starts_a_new_monitor(self):
        main=ast.parse((P/'main.py').read_bytes());calls=[]
        for klass in (node for node in main.body if isinstance(node,ast.ClassDef)):
            for call in ast.walk(klass):
                if isinstance(call,ast.Call) and isinstance(call.func,ast.Name) and call.func.id=='attach_update_notice':
                    calls.append((klass.name,[(kw.arg,ast.literal_eval(kw.value)) for kw in call.keywords]))
        self.assertEqual(sum(bool(keywords) for name,keywords in calls),1)
        self.assertIn(('GTIPTVPlayerProScreen',[('entry',True)]),calls)
    def test_the_new_interval_description_is_translated_in_all_43_catalogues(self):
        key='Updates are checked when the plugin opens and every 6 hours.'
        paths=list((P/'locale').glob('*/LC_MESSAGES/GTIPTVPlayerPro.mo'));self.assertEqual(len(paths),43)
        for path in paths:
            catalog=gettext.GNUTranslations(io.BytesIO(path.read_bytes()))
            self.assertTrue(key in catalog._catalog,path.parents[1].name)
            self.assertTrue(catalog.gettext(key).strip())

if __name__=='__main__':unittest.main()

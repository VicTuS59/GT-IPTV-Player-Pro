"""Actual fullscreen-logo flow: retries, stale replies, cooldowns and decode."""
import ast,importlib.util,json,sys,threading,types,unittest
from pathlib import Path
from unittest.mock import Mock
P=Path(__file__).resolve().parents[1]/'GTIPTVPlayerPro'
spec=importlib.util.spec_from_file_location('_live_picon_retry_policy',P/'live_picon_retry.py')
policy=importlib.util.module_from_spec(spec);spec.loader.exec_module(policy)
tree=ast.parse((P/'browser_player.py').read_bytes())
klass=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='GTExternalPlayerScreen')
NAMES={'_is_live_item','_live_network_silent','_live_picon_status','_defer_live_picon','_live_picon_tick',
       '_live_snapshot_for_current_item','_remember_live_snapshot_component',
       '_apply_live_snapshot_picon','_live_info_missing_components','_arm_live_info_fallback',
       '_schedule_live_info_completion','_start_live_info_completion','_publish_live_info_result',
       '_load_live_info_picon_worker','_apply_live_info_result','_live_info_tick','_apply_picon',
       '_sync_picon_visibility','show_info','_reset_picon_display','_cancel_live_info_state','_picon_tick'}
METHODS=[n for n in klass.body if isinstance(n,ast.FunctionDef) and n.name in NAMES]
assert len(METHODS)==len(NAMES)

class Clock:
    def __init__(self):self.now=1.0
    def __call__(self):return self.now
    def advance(self,seconds):self.now+=seconds

class Widget:
    def __init__(self):self.visible=False;self.calls=[]
    def show(self):self.visible=True;self.calls.append(threading.get_ident())
    def hide(self):self.visible=False;self.calls.append(threading.get_ident())

class Dialog(dict):
    def __init__(self):
        super().__init__({name:Widget() for name in ('picon','youtube_logo','picon_placeholder')})
        self._gt_pixmap_loaders={}
    def show(self):pass

class Timer:
    def __init__(self):self.starts=[];self.active=False
    def start(self,*args):self.starts.append(args);self.active=True
    def stop(self):self.active=False

class Cancelled(Exception):pass

class Token:
    def __init__(self,seconds):self.seconds=seconds;self.cancelled=False
    def active(self):return not self.cancelled
    def expired(self):return not self.active()
    def cancel(self):self.cancelled=True
    def check(self):
        if not self.active():raise Cancelled()

class Thread:
    def __init__(self,target,args):self.target=target;self.args=args;self.daemon=False
    def start(self):self.target(*self.args)

class Handle:
    def __init__(self,callback):self.callback=callback;self.closed=False
    def close(self):self.closed=True;self.callback(False)

class Model:
    def __init__(self,clock):
        self.clock=clock;self.cache={};self.files=set();self.responses=[];self.decodes=[]
        self.fetches=[];self.paint_calls=[];self.handles=[];self.purges=[];self.cooldown=0.0
    def url(self,sid='B'):return 'https://example.invalid/'+sid+'.png'
    def cached(self,url):return self.cache.get(url,'')
    def warm(self,path='/fixture/B.png',sid='B'):
        self.cache[self.url(sid)]=path;self.files.add(path);return path
    def fetch(self,client,item,worker_token=None,timeout=None):
        self.fetches.append((item.stream_id,timeout,worker_token.seconds,threading.get_ident()))
        result=self.responses.pop(0) if self.responses else dict(path='/fixture/'+item.stream_id+'.png')
        if isinstance(result,BaseException):raise result
        if result.get('expire'):worker_token.cancel()
        self.cooldown=self.clock()+result.get('cooldown',0)
        path=result.get('path','')
        if path:self.warm(path,item.stream_id)
        return path
    def paint(self,kind,dialog,name,path,**kw):
        old=dialog._gt_pixmap_loaders.pop(kw['key'],None)
        if old:old.close()
        self.paint_calls.append((kind,path,threading.get_ident()))
        handle=Handle(kw['on_loaded']);self.handles.append(handle)
        dialog._gt_pixmap_loaders[kw['key']]=handle
        result=self.decodes.pop(0) if self.decodes else True
        if isinstance(result,BaseException):raise result
        if result is not None:handle.callback(result)
    def purge(self,url,path):
        self.purges.append((url,path))
        if self.cache.get(url)!=path:return False
        self.cache.pop(url);self.files.discard(path);return True

def player(cached=False,download=None,decode=None,source='xtream',thread_class=Thread):
    clock=Clock();model=Model(clock)
    if cached:model.warm()
    model.responses=list(download or []);model.decodes=list(decode or [])
    ns=dict(time=types.SimpleNamespace(monotonic=clock,time=clock),LivePiconRetry=policy.LivePiconRetry,
            LIVE_INFO_PICON_TIMEOUT=policy.DOWNLOAD_TIMEOUT,LIVE_INFO_PICON_BUDGET=policy.DOWNLOAD_BUDGET,
            LIVE_INFO_FALLBACK_MS=900,LIVE_INFO_READY_MIN_SAMPLES=2,LIVE_INFO_SETTLE_MS=700,
            STALKER_LIVE_EPG_QUIET_MS=750,LIVE_INFO_POLL_MS=100,
            artwork_path=lambda value:value,_safe_picon_url=lambda value:value if str(value).startswith('https://') else '',
            _cached_picon_path=model.cached,_valid_live_picon_file=lambda value:value in model.files,
            _picon_failure_active=lambda url:clock()<model.cooldown,
            _picon_failure_remaining=lambda url:max(0,model.cooldown-clock()),
            _discard_invalid_live_picon_cache=model.purge,_load_live_picon_path=model.fetch,
            _WorkerToken=Token,_WorkerCancelled=Cancelled,_cancel_worker_token=lambda token:token.cancel() if token else None,
            _debug=lambda *args:None,threading=types.SimpleNamespace(Thread=thread_class,Event=threading.Event),
            attach_native_alpha_pixmap=lambda *a,**kw:model.paint('alpha',*a,**kw),
            attach_pixmap=lambda *a,**kw:model.paint('compatibility',*a,**kw))
    exec(compile(ast.fix_missing_locations(ast.Module(body=METHODS,type_ignores=[])),'actual-live-picon-methods','exec'),ns)
    cls=type('ActualPiconFlow',(),{name:ns[name] for name in NAMES});s=cls()
    s._closed=False;s._started=True;s._generation=2
    s.current_item=types.SimpleNamespace(content_type='live',stream_id='B',icon=model.url())
    s.client=types.SimpleNamespace(source_type=source);s._live_info_epg_retry_after={};s._live_info_epg_refresh_generation=-1
    s._picon_request_generation=s._cached_picon_generation=-1;s._picon_request_token=2;s._picon_visible=False
    s._live_picon_retry=None;s._live_info_state=None;s._live_info_results={};s._live_info_results_lock=threading.Lock()
    s._live_info_timer=Timer();s._picon_timer=Timer();s._live_info_queued_generation=-1
    s._live_info_due_at=0.;s._live_play_started_at=0.;s._live_info_ready_samples=2
    s._picon_worker_token=None;s._picon_results={};s._info_visible=True;s._info_dialog=Dialog()
    s._live_snapshot={}
    s._snapshot_epg_window=lambda value:[];s._live_info_loader=lambda:None;s._live_transition_in_progress=lambda:False
    s._handoff_first_show_pending=False;s._seeking=s._paused=False;s._subtitle_message_tokens=[]
    for name in ('_hide_seek_overlay','_clock_tick','_render_live_snapshot_epg','_refresh_live_metadata_once',
                 '_refresh_infobar_telemetry','_set_subtitle_infobar_visible','_start_player_weather','_start_info_hide_timer'):
        setattr(s,name,Mock())
    return s,model,clock

def start(s):
    s._schedule_live_info_completion()
    if s._live_info_queued_generation==s._generation:s._start_live_info_completion(force=True)
    if s._live_info_state is not None:s._live_info_tick()

def tick(s,clock,seconds=0):
    clock.advance(seconds);s._picon_tick()
    if s._live_info_state is not None:s._live_info_tick()

class FlowTests(unittest.TestCase):
    def test_warm_logo_is_applied_without_a_download(self):
        s,m,c=player(cached=True);start(s)
        self.assertTrue(s._picon_visible);self.assertEqual(m.fetches,[])
        self.assertEqual(s._cached_picon_generation,s._generation)
    def test_cold_logo_downloads_and_decodes_with_a_bounded_longer_budget(self):
        for source in ('xtream','stalker','m3u'):
            s,m,c=player(source=source);start(s)
            self.assertTrue(s._picon_visible)
            self.assertEqual(m.fetches[0][1:3],(5,11))
            self.assertEqual(s._live_picon_status().attempts,1)
            self.assertEqual(s._live_picon_status().decode_attempts,1)
    def test_download_failure_recovers_on_the_same_channel(self):
        s,m,c=player(download=[dict(path='')]);start(s)
        self.assertFalse(s._picon_visible);self.assertEqual(s._cached_picon_generation,-1)
        tick(s,c,1);self.assertTrue(s._picon_visible);self.assertEqual(len(m.fetches),2)
    def test_transient_cooldown_is_respected_and_does_not_become_five_minutes(self):
        s,m,c=player(download=[dict(path='',cooldown=20)]);start(s)
        for value in range(19):tick(s,c,1)
        self.assertEqual(len(m.fetches),1);self.assertFalse(s._picon_visible)
        tick(s,c,1);self.assertEqual(len(m.fetches),2);self.assertTrue(s._picon_visible)
    def test_existing_cooldown_is_scheduled_even_without_an_initial_worker(self):
        s,m,c=player();m.cooldown=c()+20;start(s)
        self.assertEqual(m.fetches,[]);self.assertTrue(s._picon_timer.active)
        tick(s,c,20);self.assertTrue(s._picon_visible)
    def test_permanent_failure_and_rate_limit_are_not_bypassed(self):
        for cooldown in (60,300):
            s,m,c=player(download=[dict(path='',cooldown=cooldown)]);start(s)
            for value in range(100):s.show_info();tick(s,c,.1)
            self.assertEqual(len(m.fetches),1);self.assertFalse(s._picon_visible)
            self.assertFalse(s._picon_timer.active)
    def test_decode_failure_uses_a_compatibility_decoder_without_fetching_again(self):
        s,m,c=player(cached=True,decode=[False,True]);start(s)
        self.assertFalse(s._picon_visible);self.assertEqual(s._cached_picon_generation,-1)
        tick(s,c,1)
        self.assertTrue(s._picon_visible);self.assertEqual(m.fetches,[])
        self.assertEqual([kind for kind,path,tid in m.paint_calls],['alpha','compatibility'])
    def test_pending_decode_does_not_trigger_duplicate_requests(self):
        s,m,c=player(cached=True,decode=[None]);start(s)
        for value in range(20):s._live_info_missing_components();s.show_info()
        self.assertEqual(len(m.paint_calls),1);self.assertEqual(m.fetches,[])
        self.assertEqual(s._cached_picon_generation,-1)
        m.handles[0].callback(True);self.assertTrue(s._picon_visible)
    def test_decoder_exception_uses_the_same_bounded_retry(self):
        s,m,c=player(cached=True,decode=[RuntimeError('decode failed'),True]);start(s)
        tick(s,c,1);self.assertTrue(s._picon_visible)
    def test_two_decoder_failures_discard_only_the_known_cache_then_redownload(self):
        s,m,c=player(cached=True,decode=[False,False,True]);start(s)
        tick(s,c,1);self.assertEqual(m.purges,[(m.url(),'/fixture/B.png')])
        tick(s,c,1);self.assertTrue(s._picon_visible);self.assertEqual(len(m.fetches),1)
        self.assertEqual(s._live_picon_status().attempts,3)
    def test_repeated_failures_stop_after_three_attempts(self):
        s,m,c=player(download=[dict(path='')]*20);start(s)
        for value in range(100):s.show_info();tick(s,c,1)
        self.assertEqual(len(m.fetches),3);self.assertFalse(s._picon_visible)
        self.assertEqual(s._live_picon_status().attempts,3)
    def test_never_returning_decoder_is_timed_out_and_late_callback_is_ignored(self):
        s,m,c=player(cached=True,decode=[None,True]);start(s);old=m.handles[0]
        tick(s,c,5);self.assertTrue(old.closed);self.assertFalse(s._picon_visible)
        tick(s,c,1);self.assertTrue(s._picon_visible)
        old.callback(False);self.assertTrue(s._picon_visible)
        self.assertEqual(s._live_picon_status().phase,'ready')
    def test_expired_download_without_a_result_recovers(self):
        s,m,c=player(download=[dict(path='',expire=True)]);start(s)
        self.assertEqual(s._live_picon_status().phase,'')
        tick(s,c,1);self.assertTrue(s._picon_visible)
    def test_failed_worker_start_does_not_crash_or_latch_pending(self):
        class Broken(Thread):
            def start(self):raise RuntimeError('thread unavailable')
        s,m,c=player(thread_class=Broken);start(s)
        self.assertEqual(s._live_picon_status().phase,'')
        self.assertTrue(s._picon_timer.active)
    def test_channel_change_rejects_old_decode_and_resets_the_budget(self):
        s,m,c=player(cached=True,decode=[None,True]);start(s);old=m.handles[0]
        s._cancel_live_info_state();s._reset_picon_display();s._generation+=1
        s.current_item=types.SimpleNamespace(content_type='live',stream_id='C',icon=m.url('C'))
        m.warm('/fixture/C.png','C');start(s)
        self.assertTrue(s._picon_visible);self.assertEqual(s._live_picon_status().attempts,1)
        old.callback(False);self.assertTrue(s._picon_visible)
        self.assertEqual(s._live_picon_status().key,(3,'C'))
    def test_channel_change_rejects_old_download_results(self):
        pending=[]
        class Delayed(Thread):
            def start(self):pending.append(self)
        s,m,c=player(thread_class=Delayed);start(s);old=pending.pop()
        s._cancel_live_info_state();s._reset_picon_display();s._generation+=1
        s.current_item=types.SimpleNamespace(content_type='live',stream_id='C',icon=m.url('C'))
        m.warm('/fixture/C.png','C');start(s)
        old.target(*old.args)
        self.assertTrue(s._picon_visible);self.assertEqual(s._live_info_results,{})
        self.assertEqual([path for kind,path,tid in m.paint_calls],['/fixture/C.png'])
    def test_info_reopening_resumes_cancelled_work_without_resetting_attempts(self):
        pending=[]
        class Delayed(Thread):
            def start(self):pending.append(self)
        s,m,c=player(thread_class=Delayed);start(s);s._cancel_live_info_state();s._picon_timer.stop()
        self.assertEqual(s._live_picon_status().attempts,1)
        s.show_info();self.assertTrue(s._picon_timer.active)
        tick(s,c,1);self.assertEqual(s._live_picon_status().attempts,2)
    def test_no_retries_after_close_or_during_a_channel_transition(self):
        for closed in (False,True):
            s,m,c=player(download=[dict(path='')]);start(s)
            s._closed=closed;s._live_transition_in_progress=lambda:not closed
            for value in range(10):tick(s,c,1)
            self.assertEqual(len(m.fetches),1)
    def test_picon_only_retry_does_not_refetch_epg(self):
        s,m,c=player();s._live_info_missing_components=lambda:(True,True)
        s._live_info_queued_generation=s._generation
        self.assertTrue(s._start_live_info_completion(force=True,picon_only=True))
        self.assertEqual(set(s._live_info_state['events']),{'picon_path'})
    def test_old_and_duplicate_callbacks_cannot_clear_a_successful_logo(self):
        s,m,c=player(cached=True);start(s);handle=m.handles[0]
        handle.callback(False);handle.callback(True)
        self.assertTrue(s._picon_visible);self.assertEqual(s._live_picon_status().attempts,1)
    def test_hidden_infobar_stays_hidden_when_logo_finishes(self):
        s,m,c=player(cached=True,decode=[None]);s._info_visible=False;start(s)
        m.handles[0].callback(True);self.assertTrue(s._picon_visible)
        self.assertFalse(s._info_dialog['picon'].visible)
        s._start_info_hide_timer.assert_not_called()
        s.show_info();self.assertTrue(s._info_dialog['picon'].visible)
    def test_a_visible_banner_gets_time_to_display_the_loaded_logo_once(self):
        s,m,c=player(cached=True);start(s)
        s._start_info_hide_timer.assert_called_once()
        m.handles[0].callback(True);s._start_info_hide_timer.assert_called_once()
    def test_no_client_does_not_start_a_timer_poll_loop(self):
        s,m,c=player();s.client=None;s._picon_tick()
        self.assertEqual(m.fetches,[]);self.assertEqual(s._picon_timer.starts,[])
    def test_invalid_url_causes_no_download(self):
        s,m,c=player();s.current_item.icon='file:///etc/passwd';start(s)
        for value in range(5):tick(s,c,1)
        self.assertEqual(m.fetches,[]);self.assertEqual(s._live_picon_status().attempts,0)
    def test_vod_keeps_the_original_decoder_without_a_live_retry_timer(self):
        s,m,c=player();s.current_item.content_type='movie'
        s._apply_picon(s._generation,'/fixture/movie.jpg')
        self.assertTrue(s._picon_visible);self.assertIsNone(s._live_picon_retry)
        self.assertEqual(m.paint_calls[0][0],'compatibility');self.assertEqual(s._picon_timer.starts,[])
    def test_real_worker_only_publishes_data_and_gui_tick_applies_pixmap(self):
        s,m,c=player(thread_class=threading.Thread);gui=threading.get_ident()
        s._schedule_live_info_completion();s._start_live_info_completion(force=True)
        state=s._live_info_state
        self.assertTrue(state['events']['picon_path'].wait(2))
        self.assertNotEqual(m.fetches[0][3],gui);self.assertEqual(m.paint_calls,[])
        s._live_info_tick();self.assertTrue(s._picon_visible)
        self.assertEqual(m.paint_calls[0][2],gui)
    def test_decoder_settle_gate_is_preserved_for_retries(self):
        s,m,c=player();s._live_play_started_at=c();s._live_info_ready_samples=0
        s._picon_tick();self.assertEqual(m.fetches,[])
        c.advance(.9);s._picon_tick();s._live_info_tick()
        self.assertTrue(s._picon_visible)

class SnapshotIsolationTests(unittest.TestCase):
    def initial(self,**entry):
        s,m,c=player();a=m.warm('/fixture/A.png','A')
        s._live_snapshot=dict(stream_id='A',picon_path=a,events=['A-event'],
            weather_forecast='common-weather',channels={'A':dict(stream_id='A',picon_path=a,events=['A-event'])})
        if entry:s._live_snapshot['channels']['B']=dict(stream_id='B',**entry)
        return s,m,c
    def test_new_channel_with_only_epg_never_decodes_the_first_channel_logo(self):
        s,m,c=self.initial();s._remember_live_snapshot_component('B','events',['B-event']);start(s)
        self.assertEqual([path for kind,path,tid in m.paint_calls],['/fixture/B.png'])
        self.assertEqual([entry[0] for entry in m.fetches],['B'])
        self.assertEqual(s._live_snapshot_for_current_item()['picon_path'],'/fixture/B.png')
    def test_download_failure_after_early_epg_still_retries_the_current_channel(self):
        pending=[]
        class Delayed(Thread):
            def start(self):pending.append(self)
        s,m,c=player(thread_class=Delayed,download=[dict(path='',cooldown=20)])
        a=m.warm('/fixture/A.png','A')
        s._live_snapshot=dict(stream_id='A',picon_path=a,events=['A-event'],channels={'A':dict(stream_id='A',picon_path=a)})
        start(s);self.assertEqual(m.paint_calls,[])
        state=s._live_info_state
        self.assertTrue(s._apply_live_info_result(state,'events',['B-event']))
        job=pending.pop();job.target(*job.args);s._live_info_tick()
        self.assertFalse(s._picon_visible);self.assertEqual(m.paint_calls,[])
        tick(s,c,20);job=pending.pop();job.target(*job.args);s._live_info_tick()
        self.assertTrue(s._picon_visible)
        self.assertEqual([path for kind,path,tid in m.paint_calls],['/fixture/B.png'])
        self.assertEqual([entry[0] for entry in m.fetches],['B','B'])
    def test_missing_current_logo_never_inherits_the_first_logo(self):
        s,m,c=self.initial(events=['B-event'])
        self.assertNotIn('picon_path',s._live_snapshot_for_current_item())
        self.assertEqual(s._live_snapshot_for_current_item()['events'],['B-event'])
    def test_missing_current_epg_never_inherits_the_first_epg(self):
        s,m,c=self.initial(picon_path='/fixture/B.png')
        selected=s._live_snapshot_for_current_item()
        self.assertNotIn('events',selected);self.assertEqual(selected['picon_path'],'/fixture/B.png')
    def test_absent_channel_retains_only_common_weather(self):
        s,m,c=self.initial();selected=s._live_snapshot_for_current_item()
        self.assertEqual(selected,{'weather_forecast':'common-weather'})
    def test_keyed_entry_without_a_repeated_id_binds_to_its_own_key(self):
        s,m,c=self.initial();s._live_snapshot['channels']['B']={'events':['B-event']}
        selected=s._live_snapshot_for_current_item()
        self.assertEqual(selected['stream_id'],'B');self.assertNotIn('picon_path',selected)
    def test_mismatched_nested_id_is_rejected(self):
        s,m,c=self.initial();s._live_snapshot['channels']['B']={'stream_id':'A','picon_path':'/fixture/A.png'}
        self.assertEqual(s._live_snapshot_for_current_item(),{'weather_forecast':'common-weather'})
    def test_flat_snapshot_requires_an_explicit_matching_id(self):
        for stream_id in ('','A','B'):
            s,m,c=player();s._live_snapshot=dict(picon_path='/fixture/B.png')
            if stream_id:s._live_snapshot['stream_id']=stream_id
            selected=s._live_snapshot_for_current_item()
            self.assertEqual(bool(selected.get('picon_path')),stream_id=='B')
    def test_snapshot_reads_do_not_mutate_the_original_screen_data(self):
        s,m,c=self.initial(events=['B-event']);before=json.dumps(s._live_snapshot,sort_keys=True)
        for repeat in range(5):s._live_snapshot_for_current_item()
        self.assertEqual(json.dumps(s._live_snapshot,sort_keys=True),before)
    def test_repeated_zaps_select_each_channels_own_cached_logo(self):
        s,m,c=self.initial()
        for sid in ('A','B','C','A','C','B'):
            s._cancel_live_info_state();s._reset_picon_display();s._generation+=1
            s.current_item=types.SimpleNamespace(content_type='live',stream_id=sid,icon=m.url(sid))
            m.warm('/fixture/'+sid+'.png',sid)
            s._remember_live_snapshot_component(sid,'events',[sid+'-event']);start(s)
            self.assertTrue(s._picon_visible)
            self.assertEqual(m.paint_calls[-1][1],'/fixture/'+sid+'.png')
        self.assertEqual(m.fetches,[])
    def test_no_current_logo_url_stays_blank_instead_of_showing_the_first_channel(self):
        s,m,c=self.initial(events=['B-event']);s.current_item.icon='';start(s)
        self.assertEqual(m.paint_calls,[]);self.assertEqual(m.fetches,[]);self.assertFalse(s._picon_visible)
    def test_explicit_empty_logo_does_not_restore_a_parent_logo(self):
        s,m,c=self.initial(events=['B-event'],picon_path='');start(s)
        self.assertEqual([path for kind,path,tid in m.paint_calls],['/fixture/B.png'])
    def test_valid_complete_channel_snapshot_avoids_a_network_request(self):
        s,m,c=self.initial(events=['B-event'],picon_path='/fixture/B.png');m.files.add('/fixture/B.png');start(s)
        self.assertEqual(m.fetches,[]);self.assertEqual(m.paint_calls[-1][1],'/fixture/B.png')
    def test_failed_current_logo_decode_never_falls_back_to_the_first_channel(self):
        s,m,c=self.initial(events=['B-event']);m.decodes=[False,True];start(s);tick(s,c,1)
        self.assertTrue(s._picon_visible)
        self.assertEqual([path for kind,path,tid in m.paint_calls],['/fixture/B.png','/fixture/B.png'])
    def test_unknown_active_channel_id_cannot_claim_a_flat_snapshot(self):
        s,m,c=player();s.current_item.stream_id='';s._live_snapshot=dict(stream_id='',picon_path='/fixture/A.png')
        self.assertEqual(s._live_snapshot_for_current_item(),{})

class PolicyTests(unittest.TestCase):
    def test_per_channel_attempt_and_time_limits(self):
        state=policy.LivePiconRetry((1,'channel'),0)
        for now in (0,1,2):
            self.assertTrue(state.begin('download',now));state.defer(now)
        self.assertFalse(state.ready(3));self.assertFalse(state.can_wait(3))
        other=policy.LivePiconRetry((2,'other'),0)
        self.assertFalse(other.ready(60));self.assertFalse(other.can_wait(60))
    def test_download_and_decode_share_an_attempt(self):
        state=policy.LivePiconRetry((1,'channel'),0)
        state.begin('download',0);state.begin('decode',1)
        self.assertEqual((state.attempts,state.decode_attempts),(1,1))
        self.assertEqual(state.decode_deadline,6)
        state.succeeded();self.assertFalse(state.ready(2))

class HelperTests(unittest.TestCase):
    def namespace(self,active=False):
        tree=ast.parse((P/'browser_common.py').read_bytes())
        node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_load_live_picon_path')
        download=Mock(return_value='');mark=Mock();clear=Mock()
        ns=dict(_safe_picon_url=lambda v:v,_cached_picon_path=lambda url:'',_valid_live_picon_file=lambda p:bool(p),
                _download_client_image=download,_picon_cache_path=lambda url:'/fixture/logo.png',
                LIVE_PICON_DOWNLOAD_TIMEOUT=2,_WorkerCancelled=Cancelled,
                _picon_failure_active=lambda url:active,_mark_picon_failure=mark,_clear_picon_failure=clear,
                _discard_invalid_live_picon_cache=Mock(return_value=True))
        exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'actual-live-picon-cache-helper','exec'),ns)
        return ns,download,mark
    def test_list_timeout_default_is_unchanged_and_infobar_can_override(self):
        ns,download,mark=self.namespace();item=types.SimpleNamespace(icon='https://example.invalid/logo.png')
        ns['_load_live_picon_path'](None,item);self.assertEqual(download.call_args.kwargs['timeout'],2)
        ns['_load_live_picon_path'](None,item,timeout=5);self.assertEqual(download.call_args.kwargs['timeout'],5)
    def test_existing_transient_cooldown_is_not_overwritten(self):
        ns,download,mark=self.namespace(active=True);download.side_effect=TimeoutError('temporary')
        ns['_load_live_picon_path'](None,types.SimpleNamespace(icon='https://example.invalid/logo.png'))
        mark.assert_not_called()
    def test_new_error_keeps_its_type_for_the_cooldown_policy(self):
        ns,download,mark=self.namespace();error=TimeoutError('temporary');download.side_effect=error
        ns['_load_live_picon_path'](None,types.SimpleNamespace(icon='https://example.invalid/logo.png'))
        self.assertIs(mark.call_args.args[1],error)

if __name__=='__main__':unittest.main()

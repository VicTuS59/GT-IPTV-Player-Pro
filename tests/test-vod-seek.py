"""Exercise the shipped VOD seek methods with inert native service adapters."""
import ast,importlib,sys,types,unittest
from pathlib import Path
from unittest.mock import Mock
P=Path(__file__).resolve().parents[1]/'GTIPTVPlayerPro'
PACKAGE='_gt_vod_seek_test'
package=types.ModuleType(PACKAGE);package.__path__=[str(P)];sys.modules[PACKAGE]=package
language=types.ModuleType(PACKAGE+'.i18n');language._=language.N_=lambda text:text
sys.modules[language.__name__]=language
recovery=types.ModuleType(PACKAGE+'.live_recovery')
recovery.media_snapshot=lambda service,info:dict(video_pts=service.pts if service else None)
recovery.reference_identity=lambda ref:ref.toString() if ref else ''
sys.modules[recovery.__name__]=recovery
diagnostics=types.ModuleType(PACKAGE+'.diagnostics');diagnostics.log_event=lambda *args:None
sys.modules[diagnostics.__name__]=diagnostics
enigma=types.ModuleType('enigma');enigma.iServiceInformation=object();sys.modules.setdefault('enigma',enigma)
support=importlib.import_module(PACKAGE+'.vod_seek')
progress=importlib.import_module(PACKAGE+'.vod_seek_progress')
root=ast.parse((P/'browser_player.py').read_bytes())
original=next(n for n in root.body if isinstance(n,ast.ClassDef) and n.name=='GTExternalPlayerScreen')
NAMES={'_raw_seek_interface','_seek_interface','_seek_seconds','_seek_seconds_precise','_seek_position',
       '_seek_call_failed','_seek_reached','_prepare_subtitle_seek','_complete_subtitle_seek',
       '_cancel_seek_verification','_schedule_seek_verification','_request_seek','_verify_seek_result',
       '_seek_immediately','_begin_or_adjust_seek','confirm_seek','_seek_unavailable','_seek_not_ready',
       '_pause_interface','pause_playback','resume_playback','_resume_bookmark_position_ready'}
methods=[node for node in original.body if isinstance(node,ast.FunctionDef) and node.name in NAMES]
assert len(methods)==len(NAMES)
namespace=dict(VodSeekSupport=support.VodSeekSupport,N_=lambda value:value,
    youtube_duration_seconds=lambda item:0,serviceapp_uses_async_seek=lambda kind:kind in (5001,5002),
    reference_service_type=lambda ref,*args:ref.type,_debug=lambda *args:None,log_event=lambda *args:None,
    SEEK_VERIFY_DELAY_MS=650,SEEK_VERIFY_MAX_CHECKS=2,SEEK_ASYNC_VERIFY_TIMEOUT_SECONDS=12)
node=ast.ClassDef(name='ActualSeekPlayer',bases=[ast.Name(id='VodSeekSupport',ctx=ast.Load())],keywords=[],body=methods,decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'shipped-seek-methods','exec'),namespace)
ActualPlayer=namespace['ActualSeekPlayer']

class Clock:
    def __init__(self):self.now=100.0
    def monotonic(self):return self.now

class Timer:
    def __init__(self):self.starts=[];self.active=False
    def start(self,*args):self.starts.append(args);self.active=True
    def stop(self):self.active=False

class Reference:
    def __init__(self,kind=5002,name='fixture'):self.type=kind;self.name=name
    def toString(self):return str(self.type)+':'+self.name

class Decoder:
    def __init__(self,clock):
        self.clock=clock;self.position=100;self.length=600;self.pts=None;self.commands=[];self.result=0
        self.paused=False;self.pauses=0;self.resumes=0
    def seek(self):return self
    def pause(self):
        # Native service.pause() returns its pause interface; that interface
        # is separate below so recording a pause never reads decoder state.
        return types.SimpleNamespace(pause=self.set_pause,unpause=self.unpause)
    def set_pause(self):self.paused=True;self.pauses+=1;return 0
    def unpause(self):self.paused=False;self.resumes+=1;return 0
    def isCurrentlySeekable(self):return 3
    def getPlayPosition(self):return (0,self.position*90000) if self.position>=0 else (1,0)
    def getLength(self):return (0,self.length*90000) if self.length>0 else (1,0)
    def seekTo(self,ticks):self.commands.append(('absolute',ticks//90000));return self.result
    def seekRelative(self,direction,ticks):self.commands.append(('relative',direction*ticks//90000));return self.result

class Navigation:
    def __init__(self,decoder,reference):self.decoder=decoder;self.current=reference;self.stops=0;self.plays=[]
    def getCurrentlyPlayingServiceReference(self):return self.current
    def stopService(self):self.stops+=1;self.current=None
    def playService(self,reference):self.plays.append(reference);self.current=reference;self.decoder.position=0;return 0

def fixture(kind='movie',engine=5002):
    clock=Clock();namespace['time']=support.time=clock
    s=ActualPlayer();d=Decoder(clock);ref=Reference(engine);nav=Navigation(d,ref)
    s.session=types.SimpleNamespace(nav=nav,current_dialog=s,in_exec=True)
    s.reference=ref;s.current_item=types.SimpleNamespace(content_type=kind,stream_id='fixture',youtube_native=False)
    s._current_service=lambda:d if nav.current is ref else None
    s._generation=1;s._closed=False;s._started=True;s._paused=False;s._seeking=False
    s._web_suspended=None;s._pending_engine_reference=None;s._pending_seek_verification=None
    s._resume_start_position=0;s._resume_bookmark_guard=None;s._resume_key='fixture'
    s._seek_ready=True;s._seek_startup_deadline=0;s._seek_target=0;s._seek_length=0
    s._seek_verify_timer=Timer();s._resume_timer=Timer();s._playback_timer=Timer();s._subtitle_auto_timer=Timer();s._hide_timer=Timer()
    s._subtitle_controller=types.SimpleNamespace(prepare_seek=Mock(),after_seek=Mock(),cancel_seek=Mock(),
        resume=Mock(),pause=Mock(),reset_for_service_change=Mock(),external_loaded=False)
    s._youtube_end_watcher=None
    for name in ('show_info','hide_info','_hide_seek_overlay','_show_seek_overlay','_start_info_hide_timer',
                 '_playback_tick','_remember_resume_subtitle','_set_static_info_epg','_arm_resume_bookmark_guard'):
        setattr(s,name,Mock())
    s._subtitle_position=lambda:d.position
    s._play_subtitle_service=lambda navigation,reference:navigation.playService(reference)
    return s,d,nav,clock

def tick(s,clock,seconds=0.65):clock.now+=seconds;s._verify_seek_result()
def wait(s,clock,seconds):
    end=clock.now+seconds
    while clock.now<end and s._pending_seek_verification is not None:tick(s,clock,min(.65,end-clock.now))
def until(s,clock,predicate,seconds=30):
    end=clock.now+seconds
    while not predicate() and clock.now<end and s._pending_seek_verification is not None:tick(s,clock)
    assert predicate(), 'Expected seek phase not reached within its budget'
def complete(s,d,clock,target=None,pts=False):
    d.position=s._pending_seek_verification['target'] if target is None else target
    if pts:d.pts=90000*d.position
    tick(s,clock)
    for step in (1,2):
        d.position+=1
        if pts:d.pts+=60000
        tick(s,clock)

class FlowTests(unittest.TestCase):
    def test_rapid_forward_coalesces_without_overlapping_commands(self):
        s,d,n,c=fixture()
        for press in range(4):s._seek_immediately(60)
        self.assertEqual(d.commands,[('absolute',160)])
        self.assertEqual(s._pending_seek_verification['queued_target'],340)
        complete(s,d,c)
        self.assertEqual(d.commands,[('absolute',160),('absolute',340)])
    def test_backward_and_opposite_direction_keep_latest_intent(self):
        s,d,n,c=fixture();s._seek_immediately(-60);s._seek_immediately(60)
        self.assertEqual(d.commands,[('absolute',40)])
        complete(s,d,c)
        self.assertEqual(d.commands,[('absolute',40),('absolute',100)])
    def test_new_presses_never_extend_the_inflight_deadline(self):
        s,d,n,c=fixture();s._seek_immediately(60);deadline=s._pending_seek_verification['guard'].deadline
        for press in range(30):c.now+=.1;s._seek_immediately(60)
        self.assertEqual(s._pending_seek_verification['guard'].deadline,deadline)
        self.assertEqual(len(d.commands),1)
    def test_single_position_jump_does_not_confirm_playback(self):
        s,d,n,c=fixture();s._seek_immediately(60);d.position=160;tick(s,c)
        self.assertIsNotNone(s._pending_seek_verification)
        s._subtitle_controller.after_seek.assert_not_called()
        d.position=161;tick(s,c);self.assertIsNotNone(s._pending_seek_verification)
        d.position=162;tick(s,c);self.assertIsNone(s._pending_seek_verification)
    def test_unknown_video_clock_uses_advancing_position_for_all_engines(self):
        for kind in ('movie','series'):
            for engine in (1,4097,5001,5002):
                s,d,n,c=fixture(kind,engine);s._seek_immediately(-60);complete(s,d,c)
                self.assertIsNone(s._pending_seek_verification);self.assertEqual(n.stops,0)
    def test_known_video_clock_must_advance_even_when_time_label_changes(self):
        s,d,n,c=fixture();s._seek_immediately(60);d.pts=90000*160;d.position=160;tick(s,c)
        for step in range(20):d.position+=1;tick(s,c)
        self.assertIsNotNone(s._pending_seek_verification);self.assertEqual(d.resumes,1)
    def test_advancing_pts_confirms_frames_with_a_cached_position(self):
        s,d,n,c=fixture();s._seek_immediately(60);d.position=160;d.pts=100;tick(s,c)
        for step in range(2):d.pts+=60000;tick(s,c)
        self.assertIsNone(s._pending_seek_verification)
    def test_transient_stall_recovers_with_one_unpause(self):
        s,d,n,c=fixture();s._seek_immediately(60);wait(s,c,12.1)
        self.assertEqual(d.resumes,1);self.assertEqual(n.stops,0)
        complete(s,d,c);self.assertIsNone(s._pending_seek_verification);self.assertEqual(n.plays,[])
    def test_stalled_seek_replays_same_reference_once_and_keeps_latest_target(self):
        s,d,n,c=fixture('series');original=s.reference
        s._seek_immediately(60);s._seek_immediately(60)
        until(s,c,lambda:bool(n.plays))
        self.assertEqual(n.stops,1);self.assertEqual(n.plays,[original]);self.assertIs(s.reference,original)
        until(s,c,lambda:len(d.commands)==2)
        self.assertEqual(d.commands,[('absolute',160),('absolute',220)])
        complete(s,d,c);self.assertIsNone(s._pending_seek_verification)
        self.assertEqual(s._subtitle_auto_timer.starts[-1],(650,True))
    def test_persistent_stall_stops_after_one_replay_and_shows_translated_error(self):
        s,d,n,c=fixture();s._seek_immediately(-60);wait(s,c,44)
        self.assertEqual(n.stops,1);self.assertEqual(n.plays,[s.reference]);self.assertEqual(d.resumes,1)
        self.assertIsNone(s._pending_seek_verification)
        self.assertEqual(s._show_seek_overlay.call_args.args[0],'THIS STREAM CANNOT BE SEEKED')
        self.assertTrue(s._resume_bookmark_guard['vod_seek_unconfirmed'])
        self.assertFalse(s._resume_bookmark_position_ready(0))
    def test_failed_replay_cannot_replace_an_existing_resume_bookmark(self):
        s,d,n,c=fixture();old=dict(key='fixture',target=100,reference=s.reference,generation=1)
        s._resume_bookmark_guard=old;s._seek_immediately(60);wait(s,c,44)
        self.assertIs(s._resume_bookmark_guard,old)
        self.assertFalse(s._resume_bookmark_position_ready(160))
    def test_confirmed_replay_releases_the_history_guard(self):
        s,d,n,c=fixture();s._seek_immediately(60);until(s,c,lambda:len(d.commands)==2);complete(s,d,c)
        self.assertIsNone(s._resume_bookmark_guard)
    def test_ignored_seek_does_not_restart_a_playing_stream(self):
        s,d,n,c=fixture();s._seek_immediately(300)
        for sample in range(20):d.position+=1;tick(s,c)
        self.assertEqual(d.resumes,0);self.assertEqual(n.stops,0);self.assertIsNone(s._pending_seek_verification)
    def test_unknown_timing_does_not_restart_a_stream(self):
        s,d,n,c=fixture();s._seek_immediately(60);d.position=-1;wait(s,c,14)
        self.assertEqual(n.stops,0);self.assertEqual(d.resumes,0)
    def test_pause_prevents_automatic_recovery(self):
        s,d,n,c=fixture();s._seek_immediately(60);s.pause_playback();wait(s,c,60)
        self.assertTrue(s._paused);self.assertEqual(d.resumes,0);self.assertEqual(n.stops,0)
        s.resume_playback();complete(s,d,c);self.assertIsNone(s._pending_seek_verification)
    def test_seek_while_paused_can_complete_without_forcing_playback(self):
        s,d,n,c=fixture();s.pause_playback();s._seek_immediately(60);d.position=160;tick(s,c)
        self.assertIsNone(s._pending_seek_verification);self.assertTrue(s._paused);self.assertEqual(d.resumes,0)
    def test_explicit_play_can_nudge_a_stalled_seek_only_once(self):
        s,d,n,c=fixture();s._seek_immediately(60);c.now+=2
        for press in range(10):s.resume_playback()
        self.assertEqual(d.resumes,1)
    def test_pause_and_play_during_decoder_release_preserve_user_intent(self):
        s,d,n,c=fixture();s._seek_immediately(60)
        until(s,c,lambda:s._pending_seek_verification['phase']=='release')
        self.assertEqual(s._pending_seek_verification['phase'],'release')
        s.pause_playback();self.assertTrue(s._paused)
        wait(s,c,3);self.assertGreaterEqual(d.pauses,1)
        d.position=160;tick(s,c);self.assertIsNone(s._pending_seek_verification)
        self.assertTrue(s._paused);s.resume_playback();self.assertFalse(s._paused)
    def test_play_during_reacquire_resumes_an_intentional_pause(self):
        s,d,n,c=fixture();s._seek_immediately(60)
        until(s,c,lambda:s._pending_seek_verification['phase']=='release')
        s.pause_playback();until(s,c,lambda:bool(n.plays))
        self.assertTrue(d.paused);s.resume_playback();self.assertFalse(d.paused)
        until(s,c,lambda:len(d.commands)==2);complete(s,d,c)
        self.assertIsNone(s._pending_seek_verification)
    def test_foreground_modal_defers_recovery(self):
        s,d,n,c=fixture();s._seek_immediately(60);s.session.current_dialog=object();wait(s,c,60)
        self.assertEqual(n.stops,0);self.assertEqual(d.resumes,0)
        s.session.current_dialog=s;complete(s,d,c);self.assertIsNone(s._pending_seek_verification)
    def test_context_changes_cancel_without_touching_the_decoder(self):
        for change in ('closed','stopped','generation','item','reference','web','engine','other-service'):
            s,d,n,c=fixture();s._seek_immediately(60)
            if change=='closed':s._closed=True
            elif change=='stopped':s._started=False
            elif change=='generation':s._generation+=1
            elif change=='item':s.current_item=types.SimpleNamespace(content_type='movie',stream_id='fixture')
            elif change=='reference':s.reference=Reference()
            elif change=='web':s._web_suspended={}
            elif change=='engine':s._pending_engine_reference=Reference(4097)
            else:n.current=Reference(5002,'other')
            wait(s,c,20);self.assertEqual(n.stops,0);self.assertEqual(n.plays,[])
    def test_leaving_during_release_cannot_reopen_the_video(self):
        for change in ('close','stop','other-service'):
            s,d,n,c=fixture();s._seek_immediately(60)
            until(s,c,lambda:s._pending_seek_verification['phase']=='release')
            if change=='close':s._closed=True
            elif change=='stop':s._started=False
            else:n.current=Reference(4097,'DVB')
            tick(s,c,1);self.assertEqual(n.plays,[])
    def test_missing_ownership_api_never_stops_a_service(self):
        s,d,n,c=fixture();s._seek_immediately(60);n.getCurrentlyPlayingServiceReference=None
        wait(s,c,20);self.assertEqual(n.stops,0);self.assertIsNone(s._pending_seek_verification)
    def test_end_seek_does_not_trigger_automatic_replay(self):
        s,d,n,c=fixture();s._seek_immediately(9999);wait(s,c,14)
        self.assertEqual(n.stops,0);self.assertEqual(d.resumes,0)
    def test_zero_video_clock_does_not_restart_healthy_playback(self):
        s,d,n,c=fixture();s._seek_immediately(60);d.pts=0;complete(s,d,c)
        self.assertIsNone(s._pending_seek_verification);self.assertEqual(n.stops,0)
    def test_startup_resume_waits_for_progress_and_keeps_its_bookmark(self):
        s,d,n,c=fixture();s._resume_bookmark_guard=dict(key='fixture',target=160,reference=s.reference,generation=1)
        s._request_seek(d,100,160,startup_resume=True);d.position=160;tick(s,c)
        self.assertIsNotNone(s._resume_bookmark_guard)
        d.position+=1;tick(s,c);d.position+=1;tick(s,c)
        self.assertIsNone(s._resume_bookmark_guard)
    def test_seekbar_updates_the_target_without_cancelling_pending_seek(self):
        s,d,n,c=fixture();s._seek_immediately(60);state=s._pending_seek_verification
        s._begin_or_adjust_seek(30);self.assertIs(s._pending_seek_verification,state)
        self.assertEqual(s._seek_target,190);s.confirm_seek()
        self.assertEqual(state['queued_target'],190);self.assertEqual(len(d.commands),1)
    def test_seek_rejection_uses_existing_error_path(self):
        s,d,n,c=fixture();d.result=-1;s._seek_immediately(60)
        self.assertEqual(d.commands,[('absolute',160),('relative',60)])
        self.assertIsNone(s._pending_seek_verification);self.assertEqual(n.stops,0)
    def test_local_zero_target_can_be_restored_after_replay(self):
        s,d,n,c=fixture();s._seek_immediately(-9999);until(s,c,lambda:len(d.commands)==2)
        self.assertEqual(d.commands[-1],('absolute',0));complete(s,d,c)
        self.assertIsNone(s._pending_seek_verification)
    def test_live_tv_remains_unseekable_without_native_calls(self):
        s,d,n,c=fixture('live');s._seek_immediately(60)
        self.assertEqual(d.commands,[]);self.assertEqual(n.stops,0)
    def test_youtube_keeps_the_original_seek_verifier(self):
        s,d,n,c=fixture();s.current_item.youtube_native=True;s._seek_immediately(60)
        self.assertNotIn('guard',s._pending_seek_verification)
        d.position=160;tick(s,c);self.assertIsNone(s._pending_seek_verification)
    def test_catchup_keeps_the_original_seek_verifier(self):
        s,d,n,c=fixture('catchup');s._seek_immediately(60)
        self.assertNotIn('guard',s._pending_seek_verification)
        d.position=160;tick(s,c);self.assertIsNone(s._pending_seek_verification)

class ObservationTests(unittest.TestCase):
    def test_target_jump_is_not_an_advancing_sample(self):
        state=progress.SeekProgress(100,160,0)
        self.assertFalse(state.observe(160,9000000,1));self.assertEqual(state.pts_steps,0)
    def test_mpeg_clock_wrap_is_valid(self):
        state=progress.SeekProgress(100,160,0)
        self.assertFalse(state.observe(160,progress.PTS_MASK-20000,1))
        self.assertFalse(state.observe(160,30000,1.65))
        self.assertTrue(state.observe(160,90000,2.3))
    def test_one_large_discontinuity_does_not_confirm_frames(self):
        state=progress.SeekProgress(100,160,0);state.observe(160,100,1)
        self.assertFalse(state.observe(160,90000000,2));self.assertEqual(state.pts_steps,0)
    def test_unknown_clocks_do_not_establish_a_stall(self):
        state=progress.SeekProgress(100,160,0)
        for now in range(20):state.observe(-1,None,now)
        self.assertFalse(state.stalled(20))
    def test_natural_playback_halfway_to_a_target_is_not_a_seek(self):
        state=progress.SeekProgress(100,160,0)
        for step in range(1,35):self.assertFalse(state.observe(100+step,None,step))
    def test_intentional_pause_extends_the_deadline(self):
        state=progress.SeekProgress(100,160,0);state.hold(60)
        self.assertEqual(state.deadline,72)

if __name__=='__main__':unittest.main()

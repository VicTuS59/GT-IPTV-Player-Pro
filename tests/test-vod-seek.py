"""Exercise the shipped VOD seek methods with inert native service adapters."""
import ast,importlib,sys,types,unittest
from pathlib import Path
from unittest.mock import Mock, patch
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
       '_seek_immediately','_seek_step_state','_begin_or_adjust_seek','confirm_seek','_seek_unavailable','_seek_not_ready',
       'seek_forward','seek_backward','_seek_keys_active','_take_seek_key','_finish_seek_key',
       'seek_right_or_info','seek_left_or_info','_clear_direction_hold','_direction_press',
       '_direction_release','_direction_hold_timeout','_direction_long',
       '_preview_seek_key','_handle_seek_key','_is_live_item','_playing_reference_identity','_reference_identity',
       '_handle_player_key','_bind_player_key_handler','_seek_alias_directions',
       '_unbind_player_key_handler','_prime_player_key',
       '_pause_interface','pause_playback','resume_playback','_resume_bookmark_position_ready'}
methods=[node for node in original.body if isinstance(node,ast.FunctionDef) and node.name in NAMES]
assert len(methods)==len(NAMES)
namespace=dict(VodSeekSupport=support.VodSeekSupport,SeekKeyHold=support.SeekKeyHold,N_=lambda value:value,
    youtube_duration_seconds=lambda item:getattr(item, 'youtube_duration', 0),
    serviceapp_uses_async_seek=lambda kind:kind in (5001,5002),
    reference_service_type=lambda ref,*args:ref.type,_debug=lambda *args:None,log_event=lambda *args:None,
    DIRECTION_HOLD_MS=650,SEEK_VERIFY_DELAY_MS=650,SEEK_VERIFY_MAX_CHECKS=2,SEEK_ASYNC_VERIFY_TIMEOUT_SECONDS=12)
node=ast.ClassDef(name='ActualSeekPlayer',bases=[ast.Name(id=base,ctx=ast.Load()) for base in ('VodSeekSupport','dict')],keywords=[],body=methods,decorator_list=[])
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


REMOTE_KEYS = dict(KEY_LEFT=105, KEY_RIGHT=106, KEY_REWIND=168, KEY_FASTFORWARD=208,
                   KEY_PREVIOUSSONG=165, KEY_NEXTSONG=163, KEY_EXIT=174)


class Remote:
    """Model OpenPLi's suppressed break and OpenATV's long-release flag."""
    def __init__(self):
        self.callback = None
        self.last_key = None
        self.long = False
        self.primes = []

    def bindAction(self, context, priority, callback):
        self.callback = callback

    def unbindAction(self, context, callback):
        assert self.callback == callback
        self.callback = None

    def keyPressed(self, device, key, flag):
        self.primes.append(key)
        self.send(key, flag)

    def send(self, key, flag):
        if self.callback is None:
            return 0
        if flag == 0:
            self.last_key, self.long = key, False
        elif key != self.last_key or (flag == 1 and self.long):
            return 0
        elif flag == 3:
            self.long = True
        return self.callback(key, flag)


def bind_remote(screen, bindings=None, keymap_api='components', key_ids=REMOTE_KEYS):
    remote = Remote()
    screen._seek_key_hold = None
    screen._seek_key_priming = False
    screen._seek_key_directions = {}
    screen._seek_key_timer = Timer()
    screen._direction_hold_timer = Timer()
    screen._held_direction = None
    screen._held_direction_long = False
    screen._minute_seek_open = False
    screen._aspect_menu_open = False
    screen._subtitle_message_tokens = set()
    screen._player_key_ids = {}
    screen._player_keys_bound = False
    screen._player_key_callback = screen._handle_player_key
    screen._player_key_map = None
    screen._subtitle_sync_keys = Mock()
    screen._live_zero_key = Mock()
    screen._handle_live_patch_key = Mock(return_value=False)
    screen._render_seek_timeline = Mock()
    screen._reset_live_patch_code = Mock()
    screen._dismiss_subtitle_sync_offset = Mock()
    screen['actions'] = types.SimpleNamespace(
        contexts=('ColorActions', 'InfobarSeekActions', 'InfobarSeekActionsPTS', 'MediaPlayerActions'),
        actions=dict.fromkeys(('seekBack', 'seekFwd', 'seekBackManual', 'seekFwdManual',
                               'SeekbarBack', 'SeekbarFwd', 'rewind', 'fastForward', 'fastforward',
                               'yellow', 'stop', 'left', 'right')),
    )
    bindings = bindings or {}
    keymap = types.SimpleNamespace(queryKeyBinding=lambda context, action: bindings.get((context, action), ()))
    modules = {'keyids': types.SimpleNamespace(KEYIDS=key_ids),
               'Components': types.ModuleType('Components'),
               'Components.ActionMap': keymap if keymap_api == 'components' else types.SimpleNamespace(),
               'Tools': types.ModuleType('Tools'),
               'Tools.KeyBindings': keymap if keymap_api == 'tools' else types.SimpleNamespace(),
               'enigma': types.SimpleNamespace(eActionMap=types.SimpleNamespace(getInstance=lambda: remote))}
    with patch.dict(sys.modules, modules):
        screen._bind_player_key_handler()
    assert remote.callback is not None
    return remote


class RemoteSeekTests(unittest.TestCase):
    def test_short_seek_is_one_second_each_way(self):
        sources = (('movie', False), ('series', False), ('movie', True), ('catchup', False))
        keys = (('KEY_FASTFORWARD', 1), ('KEY_REWIND', -1))
        for content, is_youtube in sources:
            for engine in (1, 4097, 5001, 5002):
                for name, seconds in keys:
                    with self.subTest(content=content, youtube=is_youtube, engine=engine, button=name):
                        screen, decoder, navigation, clock = fixture(content, engine)
                        screen.current_item.youtube_native = is_youtube
                        remote = bind_remote(screen)
                        key = REMOTE_KEYS[name]
                        self.assertEqual(remote.send(key, 0), 1)
                        self.assertEqual(decoder.commands, [])
                        clock.now += 0.08
                        self.assertEqual(remote.send(key, 1), 1)
                        self.assertEqual(decoder.commands, [('absolute', 100 + seconds)])
                        self.assertEqual(navigation.plays, [])
                        self.assertFalse(screen._seek_key_timer.active)

    def test_hold_accelerates_in_one_second_steps_and_seeks_once_on_release(self):
        for name, direction, delay, interval, repeats in (
                ('KEY_FASTFORWARD', 1, 0.5, 0.1, 45), ('KEY_REWIND', -1, 4.9, 0.4, 14)):
            with self.subTest(key=name):
                screen, decoder, navigation, clock = fixture()
                remote = bind_remote(screen)
                key = REMOTE_KEYS[name]
                frames = []
                screen._show_seek_overlay.side_effect = lambda title, hint, timeline=None: frames.append((timeline[0], clock.now))
                screen._render_seek_timeline.side_effect = lambda position, length: frames.append((position, clock.now))
                remote.send(key, 0)
                self.assertGreater(screen._seek_key_timer.starts[-1][0], delay * 1000)
                for index in range(repeats):
                    clock.now = 100.0 + delay + index * interval
                    remote.send(key, 2)
                self.assertEqual(decoder.commands, [])
                self.assertEqual([frame[0] for frame in frames],
                                 list(range(100 + direction, 100 + direction * (len(frames) + 1), direction)))
                self.assertGreater(frames[1][1] - frames[0][1], frames[-1][1] - frames[-2][1])
                self.assertGreater(len(frames), 6)
                self.assertEqual(remote.send(key, 5), 1)
                self.assertEqual(decoder.commands, [('absolute', frames[-1][0])])
                self.assertIsNone(screen._seek_key_hold)
                screen._finish_seek_key()
                self.assertEqual(len(decoder.commands), 1)
                self.assertEqual(navigation.stops, 0)

    def test_native_long_rearms_break_without_an_extra_step(self):
        screen, decoder, _, clock = fixture('series')
        remote = bind_remote(screen)
        key = REMOTE_KEYS['KEY_FASTFORWARD']
        remote.send(key, 0)
        clock.now += 0.6
        remote.send(key, 2)
        remote.send(key, 3)
        remote.send(key, 2)
        self.assertEqual(remote.primes, [key])
        self.assertEqual(screen._seek_key_hold.target, 101)
        self.assertEqual(remote.send(key, 1), 1)
        self.assertEqual(decoder.commands, [('absolute', 101)])

    def test_missing_release_commits_the_last_displayed_second_once(self):
        screen, decoder, _, clock = fixture()
        remote = bind_remote(screen)
        key = REMOTE_KEYS['KEY_REWIND']
        remote.send(key, 0)
        clock.now += 0.6
        remote.send(key, 2)
        self.assertEqual(decoder.commands, [])
        self.assertEqual(screen._seek_key_timer.starts[-1], (support.SeekKeyHold.REPEAT_TIMEOUT_MS, True))
        screen._finish_seek_key()
        remote.send(key, 1)
        self.assertEqual(decoder.commands, [('absolute', 99)])

    def test_second_tap_is_queued_even_when_only_one_second_from_the_first(self):
        screen, decoder, _, clock = fixture()
        remote = bind_remote(screen)
        key = REMOTE_KEYS['KEY_FASTFORWARD']
        for _ in range(2):
            remote.send(key, 0)
            remote.send(key, 1)
        self.assertEqual(decoder.commands, [('absolute', 101)])
        self.assertEqual(screen._pending_seek_verification['queued_target'], 102)
        complete(screen, decoder, clock)
        self.assertEqual(decoder.commands, [('absolute', 101), ('absolute', 102)])

    def test_seek_respects_boundaries_and_keeps_an_intentional_pause(self):
        for position, name, edge in ((1, 'KEY_REWIND', 0), (599, 'KEY_FASTFORWARD', 600)):
            with self.subTest(position=position):
                screen, decoder, _, clock = fixture()
                screen._paused = decoder.paused = True
                decoder.position = position
                remote = bind_remote(screen)
                key = REMOTE_KEYS[name]
                remote.send(key, 0)
                for index in range(6):
                    clock.now = 100.5 + index / 4
                    remote.send(key, 2)
                remote.send(key, 1)
                self.assertEqual(decoder.commands, [('absolute', edge)])
                self.assertTrue(screen._paused)
                self.assertEqual(decoder.resumes, 0)

    def test_seek_is_discarded_when_playback_or_focus_changes(self):
        for change in ('unbind', 'new generation', 'another service', 'modal', 'exit'):
            with self.subTest(change=change):
                screen, decoder, navigation, clock = fixture()
                remote = bind_remote(screen)
                key = REMOTE_KEYS['KEY_FASTFORWARD']
                remote.send(key, 0)
                clock.now += 0.6
                remote.send(key, 2)
                if change == 'unbind':
                    screen._unbind_player_key_handler()
                elif change == 'new generation':
                    screen._generation += 1
                elif change == 'another service':
                    navigation.current = Reference(4097, 'another')
                elif change == 'modal':
                    screen.session.current_dialog = object()
                else:
                    self.assertEqual(remote.send(REMOTE_KEYS['KEY_EXIT'], 0), 0)
                screen._finish_seek_key()
                self.assertEqual(decoder.commands, [])
                self.assertFalse(screen._seek_key_timer.active)

    def test_live_and_unseekable_startup_do_not_send_seek_requests(self):
        for content in ('live', 'movie'):
            with self.subTest(content=content):
                screen, decoder, _, clock = fixture(content)
                decoder.length = 0
                screen._seek_ready = False
                screen._seek_startup_deadline = clock.now + 8
                remote = bind_remote(screen)
                key = REMOTE_KEYS['KEY_FASTFORWARD']
                expected = 0 if content == 'live' else 1
                self.assertEqual(remote.send(key, 0), expected)
                self.assertEqual(remote.send(key, 1), expected)
                self.assertEqual(decoder.commands, [])
                if content == 'movie':
                    screen._show_seek_overlay.assert_called_once_with('Loading...', 'Please wait')

    def test_youtube_can_use_metadata_when_the_first_video_has_no_native_length(self):
        screen, decoder, navigation, _ = fixture(engine=5002)
        decoder.length = 0
        screen.current_item.youtube_native = True
        screen.current_item.youtube_duration = 600
        remote = bind_remote(screen)
        key = REMOTE_KEYS['KEY_FASTFORWARD']
        remote.send(key, 0)
        remote.send(key, 1)
        self.assertEqual(decoder.commands, [('absolute', 101)])
        self.assertNotIn('guard', screen._pending_seek_verification)
        self.assertEqual(navigation.stops, 0)


class DirectionKeyTests(unittest.TestCase):
    def test_short_arrows_keep_the_original_movie_series_and_youtube_steps(self):
        for content, youtube in (('movie', False), ('series', False), ('movie', True)):
            for name, target in (('KEY_LEFT', 40), ('KEY_RIGHT', 160)):
                with self.subTest(content=content, youtube=youtube, key=name):
                    screen, decoder, _, _ = fixture(content)
                    screen.current_item.youtube_native = youtube
                    remote = bind_remote(screen)
                    key = REMOTE_KEYS[name]
                    self.assertEqual(remote.send(key, 0), 1)
                    self.assertEqual(remote.send(key, 1), 1)
                    self.assertEqual(decoder.commands, [('absolute', target)])
                    self.assertIsNone(screen._seek_key_hold)

    def test_held_arrows_open_the_original_minute_input(self):
        for name, direction in (('KEY_LEFT', -1), ('KEY_RIGHT', 1)):
            with self.subTest(key=name):
                screen, decoder, _, _ = fixture('series')
                remote = bind_remote(screen)
                screen.open_minute_seek = Mock()
                key = REMOTE_KEYS[name]
                self.assertEqual(remote.send(key, 0), 1)
                self.assertEqual(screen._direction_hold_timer.starts[-1], (namespace['DIRECTION_HOLD_MS'], True))
                if direction < 0:
                    remote.send(key, 2)
                else:
                    screen._direction_hold_timeout()
                remote.send(key, 1)
                screen.open_minute_seek.assert_called_once_with(direction)
                self.assertEqual(decoder.commands, [])
                self.assertFalse(screen._seek_key_timer.active)

    def test_arrows_and_seek_buttons_keep_separate_steps_in_manual_seekbar(self):
        screen, decoder, _, _ = fixture()
        remote = bind_remote(screen)
        screen._seeking = True
        screen._seek_target = 100
        screen._seek_length = 600
        for name, target in (('KEY_RIGHT', 130), ('KEY_FASTFORWARD', 131), ('KEY_LEFT', 101), ('KEY_REWIND', 100)):
            key = REMOTE_KEYS[name]
            remote.send(key, 0)
            remote.send(key, 1)
            self.assertEqual(screen._seek_target, target)
        self.assertEqual(decoder.commands, [])

    def test_live_and_catchup_arrows_leave_shortcuts_to_the_original_action_map(self):
        for content in ('live', 'catchup'):
            with self.subTest(content=content):
                screen, decoder, _, _ = fixture(content)
                remote = bind_remote(screen)
                for name, action in (('KEY_LEFT', screen.seek_left_or_info), ('KEY_RIGHT', screen.seek_right_or_info)):
                    key = REMOTE_KEYS[name]
                    self.assertEqual(remote.send(key, 0), 0)
                    self.assertEqual(remote.send(key, 1), 0)
                    action()
                self.assertEqual(screen.show_info.call_count, 2)
                self.assertEqual(decoder.commands, [])

    def test_previous_and_next_media_keys_keep_the_original_action_map(self):
        for name in ('KEY_PREVIOUSSONG', 'KEY_NEXTSONG'):
            with self.subTest(key=name):
                screen, decoder, _, _ = fixture()
                remote = bind_remote(screen)
                key = REMOTE_KEYS[name]
                self.assertEqual(remote.send(key, 0), 0)
                self.assertEqual(remote.send(key, 1), 0)
                self.assertEqual(decoder.commands, [])
                self.assertIsNone(screen._seek_key_hold)


class MediaSeekAliasTests(unittest.TestCase):
    def test_seek_bindings_use_the_same_tap_and_hold_as_transport_keys(self):
        for api in ('components', 'tools'):
            for name, action, direction in (('KEY_PREVIOUSSONG', 'seekBack', -1),
                                            ('KEY_NEXTSONG', 'seekFwd', 1)):
                key = REMOTE_KEYS[name]
                bindings = {('InfobarSeekActions', action): [(key, 2)]}
                with self.subTest(api=api, key=name, gesture='tap'):
                    screen, decoder, _, _ = fixture('series')
                    remote = bind_remote(screen, bindings, api)
                    self.assertEqual(remote.send(key, 0), 1)
                    self.assertEqual(remote.send(key, 1), 1)
                    self.assertEqual(decoder.commands, [('absolute', 100 + direction)])
                with self.subTest(api=api, key=name, gesture='hold'):
                    screen, decoder, _, clock = fixture('series')
                    remote = bind_remote(screen, bindings, api)
                    remote.send(key, 0)
                    for repeat in range(32):
                        clock.now = 100.5 + repeat / 10
                        if repeat == 3:
                            remote.send(key, 3)
                        remote.send(key, 2)
                    self.assertEqual(decoder.commands, [])
                    frames = [screen._show_seek_overlay.call_args.kwargs['timeline'][0]]
                    frames.extend(call.args[0] for call in screen._render_seek_timeline.call_args_list)
                    self.assertGreater(len(frames), 8)
                    self.assertEqual(frames, [100 + n * direction for n in range(1, len(frames) + 1)])
                    self.assertEqual(remote.send(key, 1), 1)
                    self.assertEqual(decoder.commands, [('absolute', frames[-1])])

    def test_alias_direction_comes_from_the_active_seek_binding(self):
        screen, decoder, _, _ = fixture('movie')
        key = REMOTE_KEYS['KEY_NEXTSONG']
        remote = bind_remote(screen, {('InfobarSeekActionsPTS', 'SeekbarBack'): [(key, 8)]})
        remote.send(key, 0)
        remote.send(key, 1)
        self.assertEqual(decoder.commands, [('absolute', 99)])

    def test_nonseek_inactive_and_conflicting_mappings_are_left_to_enigma(self):
        key = REMOTE_KEYS['KEY_NEXTSONG']
        for bindings in ({('ColorActions', 'yellow'): [(key, 2)]},
                         {('OtherPlayerActions', 'seekFwd'): [(key, 2)]},
                         {('InfobarSeekActions', 'seekFwd'): [(key, 0)]},
                         {('InfobarSeekActions', 'seekFwd'): [(key, 2)],
                          ('MediaPlayerActions', 'stop'): [(key, 8)]}):
            with self.subTest(bindings=bindings):
                screen, decoder, _, _ = fixture()
                remote = bind_remote(screen, bindings)
                self.assertEqual(remote.send(key, 0), 0)
                self.assertEqual(remote.send(key, 1), 0)
                self.assertEqual(decoder.commands, [])

    def test_unavailable_keymap_api_keeps_dedicated_seek_and_direction_keys_working(self):
        screen, decoder, _, _ = fixture('series')
        remote = bind_remote(screen, keymap_api=None)
        for name in ('KEY_FASTFORWARD', 'KEY_RIGHT'):
            key = REMOTE_KEYS[name]
            self.assertEqual(remote.send(key, 0), 1)
            self.assertEqual(remote.send(key, 1), 1)
        self.assertEqual(decoder.commands, [('absolute', 101)])
        self.assertEqual(screen._pending_seek_verification['queued_target'], 161)

    def test_direction_key_cannot_become_an_alias(self):
        screen, decoder, _, _ = fixture('series')
        key = REMOTE_KEYS['KEY_LEFT']
        key_ids = dict(REMOTE_KEYS, KEY_PREVIOUSSONG=key)
        remote = bind_remote(screen, {('InfobarSeekActions', 'seekBack'): [(key, 2)]}, key_ids=key_ids)
        remote.send(key, 0)
        remote.send(key, 1)
        self.assertEqual(decoder.commands, [('absolute', 40)])

    def test_live_tv_does_not_bind_the_seek_aliases(self):
        screen, decoder, _, _ = fixture('live')
        key = REMOTE_KEYS['KEY_NEXTSONG']
        remote = bind_remote(screen, {('InfobarSeekActions', 'seekFwd'): [(key, 2)]})
        self.assertEqual(remote.send(key, 0), 0)
        self.assertEqual(remote.send(key, 1), 0)
        self.assertEqual(decoder.commands, [])


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

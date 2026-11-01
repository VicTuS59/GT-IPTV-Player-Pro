"""Playback quality must never occupy YouTube result titles or mini-TV."""
import ast,importlib.util,sys,threading,types,unittest
from pathlib import Path
from unittest.mock import Mock
P=Path(__file__).resolve().parents[1]/'GTIPTVPlayerPro';NAME='_youtube_status_checks'
spec=importlib.util.spec_from_file_location(NAME,P/'__init__.py',submodule_search_locations=[str(P)])
pkg=importlib.util.module_from_spec(spec);sys.modules[NAME]=pkg;spec.loader.exec_module(pkg)
typography=__import__(NAME+'.typography',fromlist=['*'])
source=ast.parse((P/'youtube_ui.py').read_bytes())
klass=next(n for n in source.body if isinstance(n,ast.ClassDef) and n.name=='GTYouTubeSearchScreen')
methods=[n for n in klass.body if isinstance(n,ast.FunctionDef) and n.name in ('_poll','_set_status')]
ns=dict(_=lambda text:text,ellipsize_dynamic_text=typography.ellipsize_dynamic_text,
        load_player_settings=lambda:types.SimpleNamespace(youtube_resolution='1080'))
exec(compile(ast.fix_missing_locations(ast.Module(body=methods,type_ignores=[])),'actual-youtube-poll-status','exec'),ns)

class Label:
    def __init__(self):
        self.instance=None;self.value='';self.skinAttributes=[('font','Regular;24'),('size','764,36'),('noWrap','1')]
    def setText(self,value):self.value=value
    def getText(self):return self.value

class PollController(dict):
    _poll=ns['_poll'];_set_status=ns['_set_status']
    def __init__(self,state='playing',**job):
        super().__init__();self['status']=Label();self['status'].setText('Please wait')
        self._closed=False;self.execing=True;self._sync_quality=Mock();self._search_token=''
        self._poll_prefetch=Mock();self._event_lock=threading.Lock();self._events=[]
        self._play_token='offline-fixture';self._pending_play_id='fixture';self._service=Mock()
        self._service.status.return_value={'job':dict(state=state,**job)}
        self._history_fullscreen_id='';self._busy=False;self._thumb_active=False;self._prefetch=None;self._poll_timer=Mock()

def intersect(a,b):
    return (min(a[0]+a[2],b[0]+b[2])>max(a[0],b[0]) and
            min(a[1]+a[3],b[1]+b[3])>max(a[1],b[1]))

class Skin:
    def __init__(self,width,height):self.width=width;self.height=height;self.boxes={};self.parts=[];self.names=set()
    def rect(self,box):
        return tuple(max(1 if i>1 else 0,round(value*(self.width/1920 if i%2==0 else self.height/1080))) for i,value in enumerate(box))
    def pic(self,name,box,*args,**kw):self.boxes[name]=self.rect(box)
    def label(self,name,box,*args,**kw):self.boxes[name]=self.rect(box)

layout_tree=ast.parse((P/'auxiliary_layout.py').read_bytes())
youtube_layout=next(n for n in layout_tree.body if isinstance(n,ast.FunctionDef) and n.name=='youtube')
layout_ns=dict(plugin_path=lambda *a:'/fixture.png',shared=lambda *a:'/fixture.png',older=lambda *a:'/fixture.png',MUTED='#AFC1D8',CYAN='#20D5ED',
               panel=lambda s,name,box,*a:s.pic(name,box),divider=lambda s,name,box:s.label(name,box))
exec(compile(ast.fix_missing_locations(ast.Module(body=[youtube_layout],type_ignores=[])),'actual-youtube-native-layout','exec'),layout_ns)

class YouTubeStatusTests(unittest.TestCase):
    def test_playback_success_clears_the_status_instead_of_overlaying_quality(self):
        for quality in (0,720,1080,2160):
            screen=PollController(quality=quality,requested_quality='1080')
            screen._poll();self.assertEqual(screen['status'].getText(),'')
            self.assertEqual(screen._play_token,'')
    def test_resolving_keeps_the_wait_message(self):
        screen=PollController(state='resolving');screen._poll()
        self.assertEqual(screen['status'].getText(),'Please wait')
        self.assertTrue(screen._play_token)
    def test_failure_messages_remain_available(self):
        screen=PollController(state='failed');screen._poll()
        self.assertEqual(screen['status'].getText(),'Could not start the player.')
        screen=PollController(state='failed',error='youtube_quality_unavailable');screen._poll()
        self.assertIn('No videos found',screen['status'].getText())
    def test_status_normalizes_and_fits_text_to_one_row(self):
        screen=PollController();screen._set_status('Long error\n'+'İ العربية 日本語 '*50)
        self.assertNotIn('\n',screen['status'].getText())
        self.assertIn('…',screen['status'].getText())
    def test_status_has_a_separate_nonoverlapping_box_at_all_six_osds(self):
        for width,height in ((640,480),(720,480),(720,576),(1280,720),(1920,1080),(3840,2160)):
            with self.subTest(osd=(width,height)):
                skin=Skin(width,height);layout_ns['youtube'](skin);status=skin.boxes['status']
                for name in ('section','search_text','preview_mask','preview_art','preview_title','preview_detail','preview_action','preview_resolution','pages'):
                    self.assertFalse(intersect(status,skin.boxes[name]),name)
                for index in range(5):
                    for prefix in ('row_','title_','detail_','duration_','thumb_'):
                        self.assertFalse(intersect(status,skin.boxes[prefix+str(index)]),prefix+str(index))
                self.assertLessEqual(status[0]+status[2],width)
                self.assertLessEqual(status[1]+status[3],height)
                self.assertEqual(skin.boxes['preview_mask'],skin.rect((1236,220,616,347)))
    def test_search_controller_routes_every_status_write_through_the_fitter(self):
        raw=[]
        for method in (n for n in klass.body if isinstance(n,ast.FunctionDef)):
            if method.name=='_set_status':continue
            for call in ast.walk(method):
                if (isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=='setText'
                        and isinstance(call.func.value,ast.Subscript)
                        and isinstance(call.func.value.slice,ast.Constant) and call.func.value.slice.value=='status'):
                    raw.append(method.name)
        self.assertEqual(raw,[])

if __name__=='__main__':unittest.main()

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native update screen and quiet, throttled foreground notifications."""
import threading
import textwrap
from xml.etree import ElementTree as ET
from Components.ActionMap import ActionMap
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from enigma import eTimer,getDesktop
from . import PLUGIN_VERSION
from .i18n import _,localized_upper
from .source_loader import SourceLoader
from .utility_design import NativeSkin,bind_missing
from .auxiliary_layout import shell,panel,shared
from .auxiliary_presentation import _footer_skin,_footer,text
from .remote_footer import decorate_remote_footer,footer_item,install_remote_footer
from .update_service import UpdateState,UpdateService,effective_version,receiver_busy,create_safety_backup
from .update_protocol import UpdateError

LABELS={
 'title':'Software update','auto':'Automatic update check',
 'check':'Check for updates','download':'Download and verify','install':'Install update','available':'Update available',
 'current':'Up to date','verified':'Update verified','restart':'Restart interface','source':'Update source is not ready',
 'signature':'Update signature is invalid','metadata':'Invalid update information','package':'Update package is invalid',
 'compatibility':'Update is not compatible with this receiver','busy':'Stop recordings and downloads before updating',
 'failed':'Update failed','installed':'Update installed. Restart the interface.','confirm':'Install the verified update?',
 'notice':'Update available. Open updater?','interval':'Automatic checks run every 6 hours.','new':'New version',
}
FOOTER=tuple(footer_item(key,action) for key,action in (
 ('arrows','navigate'),('ok','change'),('green',LABELS['check']),('yellow',LABELS['download']),
 ('blue',LABELS['install']),('menu','Menu'),('exit','back')))

def message(key):return _(LABELS[key])

def connect(timer,callback):
    try:timer.callback.append(callback)
    except AttributeError:timer.timeout.connect(callback)

def update_skin():
    desktop=getDesktop(0).size();w,h=desktop.width(),desktop.height();px=lambda n:max(1,round(n*min(w/1920.,h/1080.)))
    s=NativeSkin('GTSoftwareUpdateScreen',w,h,px);shell(s)
    panel(s,'update_left',(42,114,1080,812));panel(s,'update_right',(1140,114,738,812),True)
    for i in range(2):
        y=176+i*110
        s.pic('update_row_'+str(i),(64,y,1036,94),shared('path-bar'))
        s.pic('update_focus_'+str(i),(64,y,1036,94),shared('search-focus'),z=4)
        s.label('update_label_'+str(i),(92,y+10,630,72),32,lines=2)
        s.label('update_value_'+str(i),(744,y+10,330,72),30,'#20D5ED',align='right',lines=2)
    s.label('update_status',(84,536,994,110),38,'#20D5ED',lines=3)
    s.label('update_detail',(84,662,994,84),27,'#AFC1D8',lines=3)
    s.label('update_track',(84,770,984,14),background='#253D54')
    s.label('update_fill',(84,770,1,14),background='#20D5ED',z=6)
    s.label('update_progress',(84,794,984,44),28,'#20D5ED',align='right')
    s.label('update_hint',(84,850,994,56),25,'#AFC1D8',lines=2)
    s.label('update_caption',(1180,138,656,60),36,'#20D5ED')
    s.label('update_new_version',(1180,218,656,88),55)
    s.label('update_package_size',(1180,312,656,48),27,'#AFC1D8')
    s.label('update_notes',(1180,382,656,458),28,lines=14)
    s.label('update_notes_hint',(1180,864,656,38),25,'#AFC1D8')
    return decorate_remote_footer(s.finish(),FOOTER,show_web_hint=False)

class GTSoftwareUpdateScreen(Screen):
    def __init__(self,session,service=None):
        self.skin=update_skin();Screen.__init__(self,session)
        self.service=service or UpdateService();self._closed=False;self._busy='';self._offer=None;self._verified=None
        self._selected=0;self._notes_offset=0;self._initial=False;self._cancel_event=threading.Event();self._progress=(0,0)
        self._progress_lock=threading.Lock();self._status='';self._detail='';self._installed=False
        self._activity_lock=threading.Lock();self._activity_request=None
        self._aux_kind='updater';self._aux_text={};self._aux_force_fit=False;self._aux_footer_keys=tuple(i[0] for i in FOOTER)
        w,h=map(int,ET.fromstring(self.skin).get('size').split(','));self._seven_size=(w,h)
        self._seven_px=lambda n:max(1,round(n*min(w/1920.,h/1080.)));self._seven_rects={};self._seven_items=FOOTER
        install_remote_footer(self,FOOTER);bind_missing(self);self.skin=_footer_skin(self,FOOTER)
        self._reader=SourceLoader(eTimer(),connect,self._applied)
        self._progress_timer=eTimer();connect(self._progress_timer,self._poll_progress)
        self['actions']=ActionMap(['OkCancelActions','DirectionActions','ColorActions','MenuActions','ChannelSelectBaseActions'],{
            'ok':self.change,'cancel':self.back,'back':self.back,'up':self.up,'down':self.down,
            'upRepeated':self.up,'downRepeated':self.down,'left':self.change,'right':self.change,
            'green':self.check,'yellow':self.download,'blue':self.install,'menu':self.menu,
            'channelUp':self.notes_up,'channelDown':self.notes_down},-1)
        self.onShown.append(self._shown);self.onLayoutFinish.append(self._layout_ready);self.onClose.append(self._stop)
        self.setTitle(message('title'));self._render()

    def _layout_ready(self):
        self._aux_force_fit=True
        try:self._render()
        finally:self._aux_force_fit=False

    def _shown(self):
        if not self._initial:
            self._initial=True
            try:
                self._offer=self.service.cached();self._status='available' if self._offer else ''
            except UpdateError as error:self._error(error)
            self.check()

    def _start(self,kind,job):
        if self._busy or self._closed:return
        self._busy=kind;self._cancel_event=threading.Event();self._progress=(0,0);self._detail=''
        self._reader.request(job);self._progress_timer.start(200,True);self._render()

    def check(self):
        if not self._busy:self._start('check',lambda:self.service.check(cancel=self._cancel_event))

    def download(self):
        if self._busy:return
        if self._offer is None:self.check();return
        self._start('download',lambda:self.service.download(self._offer,cancel=self._cancel_event,progress=self._set_progress))

    def install(self):
        if self._busy:return
        if self._verified is None or self._offer is None:self._status='download';self._render();return
        self.session.openWithCallback(self._confirmed,MessageBox,message('confirm')+'\n'+self._offer['manifest']['version'],type=MessageBox.TYPE_YESNO,default=False)

    def _confirmed(self,confirmed):
        if confirmed is not True or self._closed or self._busy:return
        if receiver_busy(self.session):self._status='busy';self._render();return
        self._start('install',lambda:self.service.install(self._offer,self._verified,
                    busy=self._activity_busy,backup=create_safety_backup))

    def _activity_busy(self):
        # Installation workers ask the GUI timer to read native navigation.
        # No Enigma C++/GUI object is accessed from the package-manager thread.
        request=dict(event=threading.Event(),busy=True)
        with self._activity_lock:
            if self._closed:return True
            self._activity_request=request
        if not request['event'].wait(5):return True
        return request['busy']

    def _set_progress(self,done,total):
        with self._progress_lock:self._progress=(done,total)

    def _poll_progress(self):
        if self._closed:return
        with self._activity_lock:
            request,self._activity_request=self._activity_request,None
        if request is not None:
            try:request['busy']=receiver_busy(self.session)
            finally:request['event'].set()
        self._render()
        if self._busy:self._progress_timer.start(200,True)

    def _applied(self,value,error):
        if self._closed:return
        kind,self._busy=self._busy,'';self._progress_timer.stop()
        if error is not None:
            self._error(error)
            if kind=='download':self._verified=None
        elif kind=='check':
            self._offer=value;self._verified=None;self._notes_offset=0;self._status='available' if value else 'current'
        elif kind=='download':self._verified=value;self._status='verified'
        elif kind=='install':
            self._status='installed';self._installed=True;self._verified=None;self._detail=value['backup'];self._offer=None
        self._render()
        if kind=='install' and error is None:self._ask_restart()

    def _error(self,error):
        code=getattr(error,'code','failed')
        self._status=code if code in LABELS else 'current' if code=='version' else 'installed' if code=='restart' else 'failed'
        self._detail=_('Cancelled') if code=='cancelled' else _('Not enough free space.') if code=='space' else ''

    def up(self):
        if not self._busy:self._selected=(self._selected-1)%2;self._render()
    def down(self):
        if not self._busy:self._selected=(self._selected+1)%2;self._render()
    def change(self):
        if self._busy:return
        try:
            state=self.service.state.read()
            if self._selected==0:self.service.state.write(auto=not state['auto'])
            else:self.check();return
        except UpdateError as error:self._error(error)
        self._render()
    def _note_pages(self):
        notes=self._offer['manifest']['notes'] if self._offer else ''
        rows=[]
        for line in notes.splitlines():rows.extend(textwrap.wrap(line,width=42) or [''])
        return ['\n'.join(rows[i:i+12]) for i in range(0,len(rows),12)] or ['']
    def notes_up(self):self._notes_offset=max(0,self._notes_offset-1);self._render()
    def notes_down(self):
        self._notes_offset=min(len(self._note_pages())-1,self._notes_offset+1)
        self._render()
    def menu(self):
        if not self._busy:self._ask_restart()
    def _ask_restart(self):
        try:pending=effective_version(self.service.state.read())!=PLUGIN_VERSION
        except UpdateError:pending=False
        if not pending:return
        if receiver_busy(self.session):self._status='busy';self._render();return
        self.session.openWithCallback(self._restart,MessageBox,message('restart')+'?',type=MessageBox.TYPE_YESNO,default=False)
    def _restart(self,answer):
        if answer is True and not self._closed and not receiver_busy(self.session):
            from Screens.Standby import TryQuitMainloop
            self.session.open(TryQuitMainloop,3)
    def back(self):
        if self._busy=='install':return
        self.close()
    def _stop(self):
        self._closed=True;self._cancel_event.set();self._reader.close();self._progress_timer.stop()
        with self._activity_lock:
            request,self._activity_request=self._activity_request,None
        if request is not None:request['busy']=True;request['event'].set()

    def _render(self):
        if self._closed:return
        text(self,'aux_gt','GT',60);text(self,'aux_brand','IPTV PLAYER PRO',40);text(self,'aux_title',message('title'),46)
        self['aux_web_hint'].hide()
        try:state=self.service.state.read()
        except UpdateError:state=dict(auto=False,installed='');self._status='failed'
        values=(_('On') if state['auto'] else _('Off'),effective_version(state))
        for i,(label,value) in enumerate(zip((message('auto'),_('Version')),values)):
            text(self,'update_label_'+str(i),label,32,2);text(self,'update_value_'+str(i),value,30,2)
            (self['update_focus_'+str(i)].show if i==self._selected else self['update_focus_'+str(i)].hide)()
        status=message(self._busy) if self._busy in LABELS else message(self._status) if self._status in LABELS else message('interval')
        text(self,'update_status',status,36,3);text(self,'update_detail',self._detail,27,3)
        text(self,'update_hint',message('interval'),25,2)
        text(self,'update_caption',message('new'),36)
        manifest=self._offer['manifest'] if self._offer else None
        text(self,'update_new_version',manifest['version'] if manifest else '—',55)
        text(self,'update_package_size','{:.2f} MB · GitHub'.format(manifest['bytes']/1e6) if manifest else 'GitHub',27)
        pages=self._note_pages();self._notes_offset=min(self._notes_offset,len(pages)-1)
        # Preserve signed release-note paragraphs; generic title fitting flattens them.
        self['update_notes'].setText(pages[self._notes_offset])
        text(self,'update_notes_hint','CH± · '+_('Page')+' {}/{}'.format(self._notes_offset+1,len(pages)),25)
        with self._progress_lock:done,total=self._progress
        percent=min(100,int(done*100/total)) if total else 0
        self['update_fill'].show() if total else self['update_fill'].hide()
        from .seven_presentation import rect
        rect(self,'update_fill',(84,770,max(1,984*percent/100.),14))
        text(self,'update_progress','{}%'.format(percent) if total else '',28)
        _footer(self)

class UpdateNotice:
    def __init__(self,screen):
        self.screen=screen;self.service=UpdateService();self.cancel=threading.Event();self.closed=False
        self.timer=eTimer();connect(self.timer,self._check)
        self.reader=SourceLoader(eTimer(),connect,self._applied)
        screen.onShown.append(self._shown);screen.onClose.append(self._stop)
    def _shown(self):
        if not self.closed:self.timer.start(5000,True)
    def _check(self):
        if self.closed:return
        self.reader.request(lambda:self.service.check(automatic=True,cancel=self.cancel))
        self.timer.start(6*60*60*1000,True)
    def _applied(self,offer,error):
        if self.closed or error is not None or not offer:return
        try:
            if (getattr(self.screen.session,'current_dialog',None) is not self.screen
                    or getattr(self.screen,'_child_screen_open',False) is True
                    or getattr(self.screen,'_dashboard_child_open',False) is True):return
            state=self.service.state.read();version=offer['manifest']['version']
            if state['skipped']==version or not state['auto']:return
            self.service.state.write(skipped=version)
            self.screen.session.openWithCallback(self._reply,MessageBox,message('notice')+'\n'+version,type=MessageBox.TYPE_YESNO,default=False)
        except Exception:pass
    def _reply(self,answer):
        if answer is True and not self.closed:self.screen.session.open(GTSoftwareUpdateScreen)
    def _stop(self):
        self.closed=True;self.cancel.set();self.timer.stop();self.reader.close()

def attach_update_notice(screen):
    # Constructors perform no network request or SSL-context initialization.
    screen._update_notice=UpdateNotice(screen)

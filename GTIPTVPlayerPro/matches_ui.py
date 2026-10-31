# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native Enigma2 match list, independent of portals and playback."""
import datetime as dt
import os
import time
from collections import OrderedDict

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.ChoiceBox import ChoiceBox
from Screens.Screen import Screen
from Screens.MessageBox import MessageBox
from enigma import eTimer

from .background import attach_background, attach_native_alpha_pixmap
from .i18n import _, localized_short_weekday, localized_upper
from .main import _connect_timer, _scale
from .matches import LEAGUE_NAMES, MatchesLoader, REFRESH_SECONDS, leagues_for_matches
from .matches_text import tr
from .liveonsat import COUNTRIES, filter_broadcasts, load_country, save_country
from .match_highlights import available as highlights_available, search_query as highlights_query
from .matches_skin import rows as theme_rows, skin as theme_skin, artwork, scroll_geometry
from .welcome_text import fit_action_caption
from .scrollbar import update_scrollbar
from .typography import ellipsize_dynamic_text, fit_dynamic_text, font_px, visible_count

TABS = (("live", "Live"), ("finished", "Finished"), ("upcoming", "Upcoming"), ("broadcasts", "LiveOnSat"))


def _rows():
    return theme_rows()


def matches_skin():
    return theme_skin(*_scale())


class GTMatchesScreen(Screen):
    def __init__(self,session,loader=None,today=None):
        self.skin=matches_skin()
        Screen.__init__(self,session)
        self.loader=loader or MatchesLoader()
        self.day=today or dt.date.today()
        self.tab=0;self.league='';self.selected=0;self.window=0
        self.snapshot=None;self.matches=[];self._closed=False;self._started=False
        self._loading=False;self._dialog_open=False;self._next_refresh=0
        self._request=None;self._logo_for=None;self._logo_due=0;self._logo_requested=None
        self._error=False
        self.country=load_country();self._broadcast_snapshot=None;self._broadcast_rows=()
        self._channel_window=0;self._channel_match=None;self._tv_restore_id=None;self._visible_mode=None
        self._logo_paths=OrderedDict();self._logo_attempts=OrderedDict()
        self._logo_job=None;self._image_paths={};self._image_valid={}
        # Build only native labels/pixmaps; never import a portal or player.
        import xml.etree.ElementTree as ET
        self._font_sizes={}
        for widget in ET.fromstring(self.skin).findall('widget'):
            name=widget.attrib['name']
            self[name]=Label('') if 'font' in widget.attrib else Pixmap()
            if 'font' in widget.attrib:self._font_sizes[name]=int(widget.attrib['font'].split(';')[1])
        width,height,unused=_scale()
        self['title'].setText(localized_upper(tr('Matches')))
        self['r96_brand_gt'].setText('GT');self['brand'].setText('IPTV PLAYER PRO')
        self['r96_key_lr'].setText('◀▶');self['r96_action_lr'].setText(_('Change'))
        self['r96_key_ud'].setText('▲▼');self['r96_action_ud'].setText(_('Select'))
        for i,unused in enumerate(TABS):self['tab_'+str(i)].setText(tr(TABS[i][1]))
        for name,message in (('league','League'),('match','Match'),('score','Score'),('status','Status')):
            self['column_'+name].setText(tr(message))
        for name,message in (('detail_header','Match information'),('detail_half_label','First half'),('detail_state_label','Status')):
            self[name].setText(localized_upper(tr(message)) if name=='detail_header' else tr(message))
        self['action_red'].setText(_('Back'));self['action_green'].setText(_('Refresh'))
        self['action_yellow'].setText(tr('Date'));self['action_blue'].setText(tr('League filter'))
        self['source'].setText(tr('Source: ESPN'))
        self['navigation'].setText('')
        self['tv_action_red'].setText(_('Back'));self['tv_action_green'].setText(_('Refresh'))
        self['tv_action_yellow'].setText(tr('Select country'))
        self['tv_source'].setText(tr('Source: LiveOnSat'))
        self['tv_column_channels'].setText(_('Channels'))
        self._static_values={name:self[name].getText() for name in self._font_sizes}
        self['actions']=ActionMap(['OkCancelActions','DirectionActions','ColorActions','MenuActions','ChannelSelectBaseActions'],
            {'ok':self.open_highlights,'cancel':self.close,'back':self.close,'red':self.close,
             'up':lambda:self.move(-1),'down':lambda:self.move(1),
             'left':lambda:self.change_tab(-1),'right':lambda:self.change_tab(1),
             'green':self.refresh,'yellow':self.choose_date,'blue':self.choose_league,
             'menu':self.choose_date,'nextBouquet':lambda:self.page(1),
             'prevBouquet':lambda:self.page(-1)},-1)
        self._timer=eTimer();_connect_timer(self._timer,self._tick)
        self.onShown.append(self._start)
        self.onLayoutFinish.append(self._fit_static)
        self.onClose.append(self._shutdown)
        self.setTitle(tr('Matches'))
        self._render()

    def _fit_static(self):
        for name in ('title','brand','filter','count','detail_header','detail_half_label','detail_state_label',
                     'column_league','column_match','column_score','column_status','source','navigation',
                     'action_red','action_green','action_yellow','action_blue','tab_0','tab_1','tab_2','tab_3',
                     'tv_action_red','tv_action_green','tv_action_yellow','tv_source','tv_column_channels'):
            self._set(name,self._static_values.get(name,self[name].getText()),36)

    def _start(self):
        if self._closed:return
        if not self._started:self._started=True;self.refresh()
        self._timer.start(250,True)

    def refresh(self):
        if self._closed or self._loading or self._dialog_open:return
        if self._is_broadcast():
            self._tv_restore_id=self.matches[self.selected].id if self.matches else None
            self._broadcast_snapshot=None;self._broadcast_rows=();self.matches=[]
            self._request=self.loader.request('broadcasts',None)
        else:self._request=self.loader.request('scores',self.day)
        self._loading=True;self._error=False;self._logo_requested=None;self._logo_job=None
        self._render()

    def _tick(self):
        if self._closed:return
        result=self.loader.poll()
        if result:
            generation,kind,payload,error=result
            if generation==self._request:
                if kind=='scores':
                    self._loading=False;self._error=error
                    if not error and payload is not None:
                        selected_id=self.matches[self.selected].id if self.matches else None
                        self.snapshot=payload
                        self._filter(selected_id)
                        self._logo_due=time.monotonic()+0.6 if self.matches else 0
                    self._next_refresh=time.monotonic()+REFRESH_SECONDS
                elif kind=='broadcasts':
                    self._loading=False;self._error=error
                    self._broadcast_snapshot=payload if not error else None
                    self._filter(self._tv_restore_id)
                    self._logo_due=time.monotonic()+0.6 if self.matches else 0
                    self._next_refresh=time.monotonic()+REFRESH_SECONDS
                elif kind=='logos':
                    identifiers=self._logo_job or ()
                    self._logo_job=None
                    if not error and isinstance(payload,(tuple,list)):
                        for identifier,path in zip(identifiers,payload):
                            if path and os.path.isfile(path):
                                self._logo_paths[identifier]=path
                                self._logo_paths.move_to_end(identifier)
                                while len(self._logo_paths)>32:self._logo_paths.popitem(last=False)
                    self._logo_due=time.monotonic()+0.15
                self._render()
        now=time.monotonic()
        if not self._dialog_open:
            # Score refresh always takes priority over decorative logo work.
            if not self._loading and now>=self._next_refresh:self.refresh()
            elif not self._loading and self.matches and self._logo_due and now>=self._logo_due and self._logo_job is None:
                self._logo_due=0
                self._request_visible_logos(now)
        self['clock'].setText(time.strftime('%H:%M'))
        self._timer.start(250,True)

    def _league_options(self):
        return leagues_for_matches(self.snapshot.matches) if self.snapshot else ()

    @staticmethod
    def _league_label(key):
        if key == 'espn:unknown':return tr('League')
        return LEAGUE_NAMES.get(key) or (tr('League') + ' ' + key.split(':')[-1])

    def _is_broadcast(self):
        return self.tab==3

    def _filter(self,selected_id=None):
        if self._is_broadcast():
            self._broadcast_rows=filter_broadcasts(self._broadcast_snapshot,self.country)
            self.matches=[row.match for row in self._broadcast_rows]
            self.selected=next((i for i,m in enumerate(self.matches) if m.id==selected_id),0)
            self.window=0;self._channel_window=0
            return
        if self.league and self.league not in dict(self._league_options()):self.league=''
        category=TABS[self.tab][0]
        self.matches=[m for m in (self.snapshot.matches if self.snapshot else ())
                      if m.category==category and (not self.league or m.league==self.league)]
        self.selected=next((i for i,m in enumerate(self.matches) if m.id==selected_id),0)
        self.window=0

    def move(self,step):
        if not self.matches:return
        self.selected=(self.selected+step)%len(self.matches)
        self._render()

    def change_tab(self,step):
        before=self._is_broadcast()
        self.tab=(self.tab+step)%len(TABS)
        if before!=self._is_broadcast():
            self.snapshot=None;self._broadcast_snapshot=None;self._broadcast_rows=();self.matches=[]
            self.selected=self.window=0;self._loading=False
            self.refresh()
        else:self._filter();self._render()

    def page(self,step):
        if not self._is_broadcast():return self.move(step*_rows())
        if not self.matches:return
        count=len(self._broadcast_rows[self.selected].channels)
        pages=max(1,(count+9)//10)
        self._channel_window=((self._channel_window//10+step)%pages)*10
        self._render()


    def _set(self,name,value,capacity=40):
        preferred=self._font_sizes.get(name)
        unused_w,unused_h,px=_scale()
        minimum=min(preferred or px(20),px(20))
        two_lines=(name.startswith('tab_') or name.startswith('action_') or
                   name.startswith('column_') or name.startswith('tv_row_channels_') or name in ('detail_home','detail_away','detail_phase',
                   'detail_header','detail_league','detail_state','detail_half_label','detail_state_label','empty_message') or
                   name.startswith('row_') and name.endswith(('_home','_away','_league','_status')))
        if two_lines:
            rendered=fit_action_caption(self[name],value,preferred_size=preferred,min_size=minimum)
        else:
            rendered=fit_dynamic_text(self[name],value,max_lines=1,preferred_size=preferred,min_size=minimum)
        self[name].setText(rendered)

    def _render(self):
        self._mode_visibility()
        for i in range(4):
            self['tab_focus_'+str(i)].show() if i==self.tab else self['tab_focus_'+str(i)].hide()
        if self._is_broadcast():return self._render_broadcasts()
        relative={-1:'Yesterday',0:'Today',1:'Tomorrow'}.get((self.day-dt.date.today()).days)
        date_label=tr(relative) if relative else self.day.strftime('%d/%m/%Y')
        self._set('filter',date_label+' • '+(self._league_label(self.league) if self.league else tr('All leagues')),70)
        self._set('count',tr('{count} matches').format(count=len(self.matches)),40)
        if self.selected<self.window:self.window=self.selected
        if self.selected>=self.window+_rows():self.window=self.selected-_rows()+1
        for i in range(_rows()):
            index=self.window+i;match=self.matches[index] if index<len(self.matches) else None
            self['row_focus_'+str(i)].show() if match and index==self.selected else self['row_focus_'+str(i)].hide()
            live=match and match.category=='live' and match.phase not in ('Suspended','Half-time')
            self['row_{}_live'.format(i)].show() if live else self['row_{}_live'.format(i)].hide()
            status=('' if match is None else match.clock if live and match.clock else
                    dt.datetime.fromtimestamp(match.kickoff).strftime('%H:%M') if match.category=='upcoming' and match.phase=='Scheduled' else tr(match.phase))
            values=('', '', '', '', '', '') if match is None else (self._league_label(match.league),match.home,'–',match.away,match.score,status)
            for name,value in zip(('league','home','separator','away','score','status'),values):self._set('row_{}_{}'.format(i,name),value)
        self['position'].setText('{} / {}'.format(self.selected+1 if self.matches else 0,len(self.matches)))
        width,height,unused=_scale()
        update_scrollbar(self,'scroll',len(self.matches),self.selected,_rows(),scroll_geometry(width,height))
        match=self.matches[self.selected] if self.matches else None
        current_id=match.id if match else None
        if current_id!=self._logo_for:
            self._logo_for=current_id
            self._logo_due=time.monotonic()+0.6 if match else 0
        self._render_logos()
        self._set('detail_league',self._league_label(match.league) if match else '',40)
        self._set('detail_home',match.home if match else '',22)
        self._set('detail_away',match.away if match else '',22)
        self._set('detail_score',match.score if match else '—',8)
        phase=tr(match.phase) if match else ''
        if match and match.category=='live' and match.clock and match.phase not in ('Half-time','Suspended'):phase+=' • '+match.clock
        if match and match.category=='upcoming':phase+=' • '+dt.datetime.fromtimestamp(match.kickoff).strftime('%d/%m %H:%M')
        self._set('detail_phase',phase,35)
        self['detail_half_score'].setText(match.half_score if match else '—')
        self._set('detail_state',tr(match.phase) if match else '—',22)
        updated=self.snapshot.updated_at if self.snapshot else None
        self._set('updated',tr('Last update: {time}').format(time=time.strftime('%d/%m %H:%M:%S',time.localtime(updated))) if updated else tr('No update yet'),60)
        if self._loading:message=tr('Loading matches...')
        elif self._error or (self.snapshot and self.snapshot.failed_leagues and self.snapshot.updated_at is None):
            message=tr('Scores could not be updated.')
            if self.snapshot and self.snapshot.updated_at:message+=' '+tr('Showing saved scores.')
        elif self.snapshot and self.snapshot.failed_leagues:message=tr('Some leagues could not be updated.')
        elif not self.matches:message=tr('No matches found.')
        elif highlights_available(match):message=tr('OK: Match highlights')
        else:message=''
        self._set('message',message if self.matches else '',85)
        self._set('empty_message',message if not self.matches else '')
        for name in ('empty_icon','empty_message'):
            self[name].hide() if self.matches else self[name].show()
        self._render_status_colors()

    def _mode_visibility(self):
        television=self._is_broadcast()
        if television==self._visible_mode:return
        self._visible_mode=television
        core=('detail_league','detail_score','detail_home','detail_away','detail_phase',
              'detail_half_label','detail_half_score','detail_state_label','detail_state','source',
              'column_score','column_status','action_red','action_green','action_yellow','action_blue',
              'key_red','key_green','key_yellow','key_blue')
        for name in core:self[name].hide() if television else self[name].show()
        for name in self._font_sizes:
            if name.startswith('tv_'):self[name].show() if television else self[name].hide()
        for key in ('red','green','yellow'):
            self['tv_key_'+key].show() if television else self['tv_key_'+key].hide()
        for i in range(_rows()):
            for suffix in ('score','status'):
                self['row_{}_{}'.format(i,suffix)].hide() if television else self['row_{}_{}'.format(i,suffix)].show()
        self._set('detail_header',localized_upper(tr('Broadcaster channels') if television else tr('Match information')))
        self['navigation'].setText(('◀ ▶ '+_('Change')+'    ▲ ▼ '+_('Select')+
                                    ('    OK / CH± '+_('Channels') if television else '')))

    def _render_broadcasts(self):
        country=tr(dict(COUNTRIES).get(self.country,'All'))
        self._set('filter',country+' • '+tr('Live broadcasts'),70)
        self._set('count',tr('{count} matches').format(count=len(self.matches)))
        if self.selected<self.window:self.window=self.selected
        if self.selected>=self.window+_rows():self.window=self.selected-_rows()+1
        for i in range(_rows()):
            index=self.window+i;row=self._broadcast_rows[index] if index<len(self._broadcast_rows) else None
            match=row.match if row else None
            self['row_focus_'+str(i)].show() if match and index==self.selected else self['row_focus_'+str(i)].hide()
            self['row_{}_live'.format(i)].hide()
            values=('', '', '', '') if match is None else (self._league_label(match.league),match.home,'–',match.away)
            for name,value in zip(('league','home','separator','away'),values):self._set('row_{}_{}'.format(i,name),value)
            self._set('tv_row_channels_'+str(i),row.channels[0].name if row else '')
        self['position'].setText('{} / {}'.format(self.selected+1 if self.matches else 0,len(self.matches)))
        width,height,unused=_scale()
        update_scrollbar(self,'scroll',len(self.matches),self.selected,_rows(),scroll_geometry(width,height))
        self._render_logos()
        row=self._broadcast_rows[self.selected] if self.matches else None
        current_id=row.match.id if row else None
        if current_id!=self._logo_for:
            self._logo_for=current_id;self._logo_due=time.monotonic()+0.6 if row else 0
        if (row.match.id if row else None)!=self._channel_match:
            self._channel_match=row.match.id if row else None;self._channel_window=0
        channels=row.channels if row else ()
        self._set('tv_country',country)
        self._set('tv_count',_('Channels')+': '+str(len(channels)))
        for i in range(10):
            index=self._channel_window+i;channel=channels[index] if index<len(channels) else None
            self._set('tv_number_'+str(i),'{:02d}'.format(index+1) if channel else '')
            self._set('tv_channel_'+str(i),channel.name if channel else '')
        self._set('tv_page',('{}–{} / {}'.format(self._channel_window+1,min(self._channel_window+10,len(channels)),len(channels))) if channels else '')
        updated=self._broadcast_snapshot.updated_at if self._broadcast_snapshot else None
        self._set('updated',tr('Last update: {time}').format(time=time.strftime('%H:%M:%S',time.localtime(updated))) if updated else tr('No update yet'))
        if self._loading:message=tr('Loading matches...')
        elif self._error or (self._broadcast_snapshot and self._broadcast_snapshot.failed):message=tr('Broadcasts could not be updated.')
        elif self._broadcast_snapshot and self._broadcast_snapshot.partial:message=tr('Broadcast information may be incomplete.')
        elif not self.matches:message=tr('No live broadcasts found.')
        else:message=''
        self._set('message',message if self.matches else '')
        self._set('empty_message',message if not self.matches else '')
        for name in ('empty_icon','empty_message'):
            self[name].hide() if self.matches else self[name].show()
        self._render_status_colors()

    def _render_status_colors(self):
        self._set('navigation','OK / CH± '+_('Channels') if self._is_broadcast() else '')
        self['r96_detail_rule'].hide() if self._is_broadcast() else self['r96_detail_rule'].show()
        for i in range(_rows()):
            index=self.window+i
            match=self.matches[index] if index<len(self.matches) else None
            warning=bool(match and not self._is_broadcast() and match.phase=='Suspended')
            name='row_{}_warning_status'.format(i)
            self._set(name,tr(match.phase) if warning else '')
            self[name].show() if warning else self[name].hide()
            self['row_{}_status'.format(i)].hide() if warning or self._is_broadcast() else self['row_{}_status'.format(i)].show()

    def choose_country(self):
        if self._closed or self._dialog_open:return
        self._dialog_open=True
        self.session.openWithCallback(self._country_chosen,ChoiceBox,title=tr('Select country'),
                                      list=[(tr(name),key) for key,name in COUNTRIES])

    def _country_chosen(self,choice):
        self._dialog_open=False
        if self._closed or not choice or choice[1] not in dict(COUNTRIES):return
        try:save_country(choice[1])
        except (OSError,ValueError):
            self._dialog_open=True
            self.session.openWithCallback(self._highlights_closed,MessageBox,tr('Country preference could not be saved.'),type=MessageBox.TYPE_ERROR)
            return
        self.country=choice[1];self._filter();self._render()

    def _request_visible_logos(self,now):
        if not self.matches:return
        chosen=self.matches[self.selected]
        visible=self.matches[self.window:self.window+_rows()]
        wanted=[]
        for match in [chosen]+visible:
            for identifier in (match.home_id,match.away_id):
                if (identifier and identifier not in wanted and identifier not in self._logo_paths and
                        now-self._logo_attempts.get(identifier,-1000)>=60):wanted.append(identifier)
        identifiers=tuple(wanted[:2])
        if not identifiers:return
        for identifier in identifiers:
            self._logo_attempts[identifier]=now
            self._logo_attempts.move_to_end(identifier)
        while len(self._logo_attempts)>128:self._logo_attempts.popitem(last=False)
        self._logo_job=identifiers
        self._request=self.loader.request('logos',identifiers)

    def _logo_loaded(self,name,path,loaded):
        if self._closed or self._image_paths.get(name)!=path:return
        self._image_valid[name]=bool(loaded)
        self[name].show() if loaded else self[name].hide()
        placeholder=name.replace('_logo','_placeholder')
        self[placeholder].hide() if loaded else self[placeholder].show()

    def _team_logo(self,name,identifier,present=False):
        path=self._logo_paths.get(identifier) if identifier else None
        if path and not os.path.isfile(path):
            self._logo_paths.pop(identifier,None);path=None
        if path:
            if self._image_paths.get(name)!=path:
                self._image_paths[name]=path
                self._image_valid[name]=None
                attach_native_alpha_pixmap(self,name,path,key=name,
                    on_loaded=lambda loaded:self._logo_loaded(name,path,loaded))
            loaded=self._image_valid.get(name)
            self[name].show() if loaded else self[name].hide()
            self[name.replace('_logo','_placeholder')].hide() if loaded else self[name.replace('_logo','_placeholder')].show()
        else:
            self._image_paths.pop(name,None)
            self._image_valid.pop(name,None)
            loader=getattr(self,'_gt_pixmap_loaders',{}).pop(name,None)
            if loader:loader.close()
            self[name].hide()
            self[name.replace('_logo','_placeholder')].show() if present else self[name.replace('_logo','_placeholder')].hide()

    def _render_logos(self):
        match=self.matches[self.selected] if self.matches and not self._is_broadcast() else None
        for name,identifier in (('home_logo',match.home_id if match else ''),('away_logo',match.away_id if match else '')):
            self._team_logo(name,identifier,match is not None)
        for i in range(_rows()):
            index=self.window+i;match=self.matches[index] if index<len(self.matches) else None
            self._team_logo('row_{}_home_logo'.format(i),match.home_id if match else '',match is not None)
            self._team_logo('row_{}_away_logo'.format(i),match.away_id if match else '',match is not None)

    def open_highlights(self):
        if self._is_broadcast():return self.page(1)
        if self._closed or self._dialog_open or not self.matches:return
        match=self.matches[self.selected]
        query=highlights_query(match)
        if not query:return
        self._dialog_open=True
        try:
            from .match_highlights_ui import GTMatchHighlightsScreen
            self.session.openWithCallback(self._highlights_closed,GTMatchHighlightsScreen,query=query,match=match)
        except Exception:
            self._dialog_open=False
            self._set('message',_('Could not load content'))

    def _highlights_closed(self,*args):
        self._dialog_open=False
        if not self._closed:self._render()

    def choose_date(self):
        if self._is_broadcast():return self.choose_country()
        if self._dialog_open:return
        today=dt.date.today()
        options=[(tr('Previous day'),self.day-dt.timedelta(days=1)),(tr('Next day'),self.day+dt.timedelta(days=1)),
                 (tr('Today'),today),(tr('Yesterday'),today-dt.timedelta(days=1)),(tr('Tomorrow'),today+dt.timedelta(days=1))]
        self._dialog_open=True
        self.session.openWithCallback(self._date_chosen,ChoiceBox,title=tr('Select date'),list=options)

    def _date_chosen(self,choice):
        self._dialog_open=False
        if self._closed or not choice:return
        day=choice[1]
        today=dt.date.today()
        day=max(today-dt.timedelta(days=30),min(today+dt.timedelta(days=30),day))
        if day==self.day:return
        self.day=day;self.snapshot=None;self.matches=[];self.selected=0;self.window=0
        # Supersede a pending fetch rather than allowing its day to replace this one.
        self._loading=False;self.refresh()

    def choose_league(self):
        if self._is_broadcast():return
        if self._dialog_open:return
        self._dialog_open=True
        options=[(tr('All leagues'),'')]+[(self._league_label(key),key) for key,name in self._league_options()]
        self.session.openWithCallback(self._league_chosen,ChoiceBox,title=tr('League filter'),list=options)

    def _league_chosen(self,choice):
        self._dialog_open=False
        if self._closed or not choice:return
        if choice[1] not in dict(self._league_options()) and choice[1]!='':return
        self.league=choice[1];self._filter();self._render()

    def _shutdown(self):
        self._closed=True;self._timer.stop();self.loader.close()

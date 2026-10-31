# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Presentation-only adapters for source banks, live TV and TV archive."""
import os,re
from functools import wraps
from types import MethodType
from xml.etree import ElementTree as ET
from .i18n import _,localized_upper
from .background import attach_native_alpha_pixmap
from .utility_design import bind_missing
from .seven_presentation import text as fit_text,show,rect,progress,_footer_style,_paint_footer
from .remote_footer import web_shortcut_hint_text
from .scrollbar import update_scrollbar
from .typography import font_px,ellipsize_dynamic_text
from .broadcast_layout import restyle,art

TITLES={'playlists':'Playlists','categories':'LIVE TV CATEGORIES','live':'Live TV','archive':'TV Archive'}

def skin_after(kind):
    def decorate(factory):
        @wraps(factory)
        def built(*args,**kwargs):return restyle(factory(*args,**kwargs),kind)
        return built
    return decorate

def refresh_after(method):
    @wraps(method)
    def refreshed(s,*args,**kwargs):
        depth=getattr(s,'_r101_refresh_depth',0);s._r101_refresh_depth=depth+1
        try:result=method(s,*args,**kwargs)
        finally:s._r101_refresh_depth=depth
        if depth==0 and hasattr(s,'_broadcast_kind'):refresh(s)
        return result
    return refreshed

def text(s,name,value,size=30,lines=1):
    if name not in s:return
    value=str(value or '');key=(value,size,lines)
    cached=s._r101_text.get(name)
    if s._r101_force_fit or cached is None or cached[0]!=key or s[name].getText()!=cached[1]:
        fit_text(s,name,value,size,lines);s._r101_text[name]=(key,s[name].getText())

def footer_style(markup,items):
    root=ET.fromstring(_footer_style(markup,items));w,h=map(int,root.get('size').split(','));sx,sy=w/1920.,h/1080.
    if any(item[1]=='rename' for item in items):
        weights=(.9,.9,.9,.95,1.45,.65,.9);x=64.
        for i,(item,weight) in enumerate(zip(items,weights)):
            col=1792.*weight/sum(weights);key=item[0];kw=42 if key in ('red','green','yellow','blue') else 110 if key in ('left_right','exit') else 92 if key=='ok' else 100
            for prefix in ('remote_key_','remote_key_bg_','remote_action_','r100_footer_sep_'):
                n=root.find("widget[@name='"+prefix+str(i)+"']")
                if n is None:continue
                if prefix=='remote_action_':box=(x+kw+18,1005,col-kw-38,58)
                elif prefix=='r100_footer_sep_':box=(x+col-12,1007,1,46) if i<len(items)-1 else (0,0,1,1)
                else:box=(x,1010 if kw==42 else 1006,kw,42 if kw==42 else 54)
                n.set('position','{},{}'.format(round(box[0]*sx),round(box[1]*sy)));n.set('size','{},{}'.format(max(1,round(box[2]*sx)),max(1,round(box[3]*sy))))
            x+=col
    for i,item in enumerate(items):
        if item[0]=='channel_page':
            for prefix in ('remote_key_','remote_key_bg_'):
                n=root.find("widget[@name='"+prefix+str(i)+"']")
                if n is not None:n.set('size','{},{}'.format(round(180*sx),round(54*sy)))
            n=root.find("widget[@name='remote_action_"+str(i)+"']")
            if n is not None:
                x,y=map(int,n.get('position').split(','));a,b=map(int,n.get('size').split(','));n.set('position','{},{}'.format(x+round(80*sx),y));n.set('size','{},{}'.format(max(1,a-round(80*sx)),b))
        if item[1]=='rename':
            n=root.find("widget[@name='remote_action_"+str(i)+"']")
            if n is not None:n.set('noWrap','1')
    return ET.tostring(root,encoding='unicode')

def setup(s,kind,items):
    s._broadcast_kind=kind;s._seven_items=tuple(items)
    w,h=map(int,ET.fromstring(s.skin).get('size').split(','));s._seven_size=(w,h);s._seven_px=lambda n:max(1,round(n*min(w/1920.,h/1080.)));s._seven_rects={}
    s._r101_text={};s._r101_force_fit=False;s._r101_logo_signature=None;s._r101_category_icons={}
    bind_missing(s);s.skin=footer_style(s.skin,items)
    if kind=='categories':s._list_scroll_geometry=(round(959*w/1920.),round(190*h/1080.),max(2,round(5*w/1920.)),round(665*h/1080.))
    if kind=='live':
        px=s._seven_px;s._progress_width=round(398*w/1920.);s._progress_height=max(1,round(12*h/1080.));s._row_progress_width=round(80*w/1920.);s._row_progress_height=max(1,round(14*h/1080.))
        s._live_channel_name_font=font_px(px,30);s._live_channel_name_min_font=font_px(px,22)
        s._ribbon_now_preferred_font=font_px(px,36);s._ribbon_now_min_font=font_px(px,25)
        # Reuse the current-EPG poster pipeline and its saved metadata setting.
        # Mini-TV geometry and all navigation/playback actions remain intact.
    s._broadcast_layout_callback=MethodType(layout_ready,s)
    s.onLayoutFinish.append(s._broadcast_layout_callback)
    if hasattr(s,'onShown'):s.onShown.append(s._broadcast_layout_callback)
    refresh(s)

def layout_ready(s):
    s._r101_force_fit=True
    try:refresh(s)
    finally:s._r101_force_fit=False

def refresh(s):
    if getattr(s,'_closed',False):return
    kind=s._broadcast_kind
    if kind=='live' and getattr(s,'_fullscreen_active',False):return
    text(s,'r101_gt','GT',60);text(s,'r101_brand','IPTV PLAYER PRO',40)
    title=_(TITLES[kind]);title=_('Live TV categories') if kind=='categories' else title
    text(s,'r101_title',title,48);text(s,'r101_web_hint',_('Hold MENU for 3 seconds: Web interface'),20)
    {'playlists':_playlists,'categories':_categories,'live':_live,'archive':_archive}[kind](s)
    _paint_footer(s)
    for i,item in enumerate(getattr(s,'_remote_footer_items',s._seven_items)):
        if item[0]=='channel_page':text(s,'remote_key_'+str(i),'CH+/CH−',28)
        if item[1]=='rename':text(s,'remote_action_'+str(i),_('Rename'),27)

def _playlists(s):
    text(s,'subtitle','IPTV / XTREAM' if s.source_type=='xtream' else 'STALKER / MAC',28)
    start=s.page_index*s.page_size;end=min(start+s.page_size,len(s._slots))
    text(s,'r101_count_0',_('Page {} / {}').format(s.page_index+1,s.page_count),26)
    text(s,'r101_count_1','{}–{} / {}'.format(start+1,end,len(s._slots)),26)
    for i in range(s.page_size):
        index=start+i;exists=index<len(s._slots);path=s._slots[index] if exists else None;active=exists and s._active_index==index
        for prefix in ('r101_row_','row_number_','row_name_','row_path_'):show(s,prefix+str(i),exists)
        show(s,'r101_focus_'+str(i),exists and index==s.selected_index)
        for kind in ('file','plus'):show(s,'r101_file_{}_{}'.format(kind,i),exists and (bool(path)==(kind=='file')))
        error=bool(active and path and not os.path.isfile(path))
        show(s,'r101_status_'+str(i),active and not error);show(s,'row_status_'+str(i),active and not error)
        show(s,'r101_status_red_'+str(i),error);show(s,'r101_status_error_'+str(i),error)
        if error:text(s,'r101_status_error_'+str(i),_('Error'),23)
        if not exists:continue
        text(s,'row_number_'+str(i),'{:02d}'.format(index+1),30)
        text(s,'row_name_'+str(i),os.path.basename(path) if path else _('Add playlist'),28)
        text(s,'row_path_'+str(i),os.path.dirname(path) if path else 'TXT / .txt',23)
        # Keep status and basename in separate boxes even for long file names.
        x=46+(i//6)*681;y=146+(i%6)*129;cw=660 if i<6 else 620
        rect(s,'row_name_'+str(i),(x+178,y+14,cw-308 if active else cw-204,48))
    path=s._slots[s.selected_index] if 0<=s.selected_index<len(s._slots) else None
    active=bool(path and s._active_index==s.selected_index);error=active and not os.path.isfile(path)
    state='red' if error else 'green' if active else 'neutral'
    for k in ('green','neutral','red'):show(s,'r101_side_badge_'+k,path is not None and k==state)
    text(s,'r101_side_caption',localized_upper(_('Selected playlist')),32,2)
    text(s,'r101_side_title',os.path.basename(path) if path else _('Add playlist'),39,2)
    for k in ('green','neutral','red'):
        text(s,'r101_side_status_'+k,_('Error') if error else _('Active') if active else _('Ready to connect') if path else '',27,2)
        show(s,'r101_side_status_'+k,bool(path) and k==state)
    text(s,'r101_source_caption',_('Source type'),28);text(s,'r101_source_value',s['subtitle'].getText(),34)
    text(s,'r101_path_caption',_('Save location'),28);text(s,'r101_path_value',os.path.dirname(path) if path else _('Not configured'),31,3)
    show(s,'r101_file_icon',bool(path))

def _categories(s):
    text(s,'categories_caption',localized_upper(_('Categories')),38)
    count=len(s._entries);start=s.selected_index//s.page_size*s.page_size if count else 0
    for i in range(s.page_size):
        item=s._entries[start+i] if start+i<count else None
        show(s,'r101_category_no_'+str(i),item is not None);show(s,'r101_category_icon_'+str(i),item is not None)
        show(s,'category_focus_art_'+str(i),item is not None and start+i==s.selected_index)
        text(s,'r101_category_no_'+str(i),'{:02d}'.format(start+i+1) if item else '',30)
        text(s,'category_row_'+str(i),item.name if item else '',33)
        if item:
            name=localized_upper(item.name);icon='tiles' if str(item.category_id) in ('','all','0') or start+i==0 else 'sport' if 'SPOR' in name or 'SPORT' in name else 'kids' if 'COCUK' in name or 'ÇOCUK' in name or 'KIDS' in name else 'news' if 'HABER' in name or 'NEWS' in name or 'BELGESEL' in name else 'tv'
            if s._r101_category_icons.get(i)!=icon:
                s._r101_category_icons[i]=icon
                attach_native_alpha_pixmap(s,'r101_category_icon_'+str(i),art(icon),key='r101-category-'+str(i))
        # The previous selected-item art is replaced by the persistent icon.
        show(s,'category_icon_'+str(i),False)
    text(s,'message',_('{} categories · {} / {}').format(count,s.selected_index+1 if count else 0,count) if count and not s._error else s['message'].getText(),25)
    show(s,'items',not count or bool(s._error))
    text(s,'weather_caption',s['weather_caption'].getText(),36)
    city=getattr(s,'_dynamic_weather_city_text',s['weather_city'].getText()).lstrip('● ').strip()
    text(s,'weather_city','●  '+city,38)
    text(s,'weather_temp',s['weather_temp'].getText(),96)
    for n,size in (('weather_desc',34),('weather_metrics',24),('weather_updated',22)):
        text(s,n,s[n].getText(),size,2 if n=='weather_desc' else 1)
    for i in range(5):
        for prefix,size in (('weather_day_',29),('weather_day_high_',36),('weather_day_low_',29)):text(s,prefix+str(i),s[prefix+str(i)].getText(),size)

def _live(s):
    count=len(s._entries);selected=s._entries[s.selected_index] if count and 0<=s.selected_index<count else None
    start=s.selected_index//s.page_size*s.page_size if count else 0
    for i in range(s.page_size):
        present=start+i<count
        show(s,'r101_live_row_'+str(i),present);show(s,'r101_live_focus_'+str(i),present and start+i==s.selected_index)
        if present:
            text(s,'stream_name_'+str(i),s._entries[start+i].name,30,2)
            text(s,'stream_program_'+str(i),s['stream_program_'+str(i)].getText(),25)
            event=s._current_live_row_event(s._detail_store_snapshot(s._selection_key(s._entries[start+i]),item=s._entries[start+i]).get('events'))
            if event is not None:
                ratio,_value=s._progress_details(event);progress(s,'stream_progress_fill_'+str(i),round(100*ratio),(1300,215+i*74,80,14))
    text(s,'r101_epg_caption',localized_upper(_('Broadcast schedule')),36)
    text(s,'summary_caption',localized_upper(_('Summary')),27)
    for n,size,lines in (('ribbon_now_title',36,2),('ribbon_now_time',26,1),('epg_progress',26,1),('ribbon_now_caption',29,1),('epg_title',30,1)):
        value=s[n].getText()
        if n=='epg_progress' and re.match(r'^%\d+$',value):value=value[1:]+'%'
        text(s,n,value,size,lines)
    for n in ('next','third','fourth','fifth'):
        text(s,'epg_'+n+'_time',s['epg_'+n+'_time'].getText(),24)
        text(s,'epg_'+n+'_title',s['epg_'+n+'_title'].getText(),24,2)
    if selected and getattr(s,'_detail_selection_key',None)==s._selection_key(selected):
        events=s._detail_window();event=events[0] if events else None
        if event is not None:
            ratio,value=s._progress_details(event);progress(s,'epg_progress_fill',round(ratio*100),(56,380,398,12))
    text(s,'clock_time',s['clock_time'].getText()[:5],35)
    text(s,'clock_date',s['clock_date'].getText().replace('\n',' · '),24)
    if count and not s._error:
        pages=(count+s.page_size-1)//s.page_size;text(s,'message','{} / {}'.format(s.selected_index//s.page_size+1,pages),27)
    elif s._error or not count:
        text(s,'message','',27)
        text(s,'ribbon_now_title',_(s._error) if s._error else s['message'].getText() or _('No channels found; press GREEN to try again.'),28,2)

def _logos(s,item):
    from .artwork_file import artwork_path
    from .browser_common import _valid_live_picon_file
    key=s._selection_key(item) if item is not None else None
    cached=s._detail_store_snapshot(key,item=item) if item is not None else {}
    path=artwork_path(cached.get('picon_path','') or '')
    if not path or not _valid_live_picon_file(path):path=''
    signature=(key,path)
    if signature==s._r101_logo_signature:return
    s._r101_logo_signature=signature
    for side in ('left','right'):
        name='r101_'+side+'_logo';placeholder=name+'_placeholder'
        show(s,name,False);show(s,placeholder,True);text(s,placeholder,'GT' if item is not None else '—',38)
        if not path:continue
        def loaded(success,widget=name,fallback=placeholder,owner=signature):
            if s._closed or s._fullscreen_active or owner!=s._r101_logo_signature:return
            show(s,widget,success);show(s,fallback,not success)
        attach_native_alpha_pixmap(s,name,path,key='r101-'+side+'-logo',on_loaded=loaded,show_on_load=False)

def _archive(s):
    text(s,'channels_heading',localized_upper(_('Channels')),35)
    text(s,'r101_detail_caption',localized_upper(_('Programme information')),34)
    text(s,'r101_time_caption',_('Time'),29);text(s,'r101_program_caption',_('Programme'),29);text(s,'r101_duration_caption',_('Duration'),29)
    start=s.channel_index//12*12
    for i in range(12):
        present=start+i<len(s.channels);selected=present and start+i==s.channel_index
        for prefix in ('r101_channel_row_','r101_channel_icon_'):show(s,prefix+str(i),present)
        show(s,'r101_channel_selected_'+str(i),selected);show(s,'r101_channel_focus_'+str(i),selected and s.focus=='channels')
        if present:text(s,'channel_'+str(i),s.channels[start+i].name,27)
    first=s.day_index//4*4;amount=min(4,len(s.days)-first)
    for i in range(4):
        present=i<amount;selected=present and first+i==s.day_index
        x=554+i*(302 if amount<=2 else 204);width=290 if amount<=2 else 194
        for prefix in ('r101_day_idle_','r101_day_selected_','r101_day_focus_'):
            rect(s,prefix+str(i),(x,124,width,58))
        rect(s,'day_'+str(i),(x+8,128,width-16,50))
        show(s,'r101_day_idle_'+str(i),present);show(s,'r101_day_selected_'+str(i),selected);show(s,'r101_day_focus_'+str(i),selected and s.focus=='days')
        text(s,'day_'+str(i),s['day_'+str(i)].getText(),29)
    start=s.programme_index//5*5
    for i in range(5):
        present=start+i<len(s.programmes);selected=present and start+i==s.programme_index
        show(s,'r101_programme_row_'+str(i),present);show(s,'r101_programme_selected_'+str(i),selected);show(s,'r101_programme_focus_'+str(i),selected and s.focus=='programmes')
        if present:
            text(s,'programme_'+str(i),s.programmes[start+i].title,30)
            for prefix,size in (('programme_time_',30),('programme_duration_',27)):text(s,prefix+str(i),s[prefix+str(i)].getText(),size)
    programme=s.programmes[s.programme_index] if s.programmes and 0<=s.programme_index<len(s.programmes) else None
    raw=s['detail_time'].getText();parts=raw.split('·',1)
    text(s,'r101_detail_time',parts[0].strip() if programme else '—',34)
    text(s,'r101_detail_duration',_('{} min').format(max(1,int((programme.end-programme.start+59)//60))) if programme else '',30)
    text(s,'r101_archive_watch',_('Watch from archive') if programme else '',31)
    text(s,'detail_title',programme.title if programme else '—',48,2)
    text(s,'detail_description',getattr(programme,'description','') if programme else '',28,6)
    show(s,'r101_archive_icon',programme is not None)
    text(s,'day_caption',s['day_caption'].getText(),31,2)
    for name in ('channel_count','programme_count','day_count'):text(s,name,s[name].getText(),26)

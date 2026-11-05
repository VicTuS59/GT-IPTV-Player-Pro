# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Presentation adapters; providers, jobs, persistence and actions stay in controllers."""
from functools import wraps
from types import MethodType
from collections import OrderedDict
from xml.etree import ElementTree as ET
import re
from .background import attach_pixmap
from .i18n import _,localized_upper
from .paths import plugin_path
from .remote_footer import remote_action_text
from .remote_footer import prepared_skin, skin_metadata, _cached_skin
from .scrollbar import update_scrollbar,hide_scrollbar
from .typography import current_text_size,fit_dynamic_text,font_px
from .utility_design import bind_missing
from .welcome_design import sentence_caption
from .ui_update import set_visible, set_geometry
from .seven_layout import restyle

TITLES={'batch':'Account test','health':'Server Health','xmltv':'External XMLTV EPG','mapping':'Manual channel mapping','automation':'DVB EPG','continue':'Continue Watching','favorites':'Favorites'}

def skin_after(kind,screen_name=None):
    def decorate(factory):
        @wraps(factory)
        def built(*args,**kwargs):
            markup=factory(*args,**kwargs)
            if screen_name and ET.fromstring(markup).get('name')!=screen_name:return markup
            return restyle(markup,kind)
        return _cached_skin(built)
    return decorate

def refresh_after(method):
    @wraps(method)
    def refreshed(screen,*args,**kwargs):
        result=method(screen,*args,**kwargs)
        if hasattr(screen,'_seven_kind'):refresh(screen)
        return result
    return refreshed

def _footer_style(markup,items):
    root=ET.fromstring(markup);w,h=map(int,root.get('size').split(','));sx,sy=w/1920.,h/1080.
    px=lambda n:max(1,round(n*min(sx,sy)));count=len(items);col=1792./count
    for node in root:
        name=node.get('name','')
        if name.startswith(('remote_footer_stage','remote_footer_bg','remote_divider_','remote_web_hint')):
            node.set('position','0,0');node.set('size','1,1');node.set('transparent','1');node.attrib.pop('pixmap',None);node.attrib.pop('backgroundColor',None)
        if not name.startswith(('remote_key_','remote_action_','r100_footer_sep_')):continue
        i=int(name.rsplit('_',1)[1])
        if i>=count:continue
        key=items[i][0];colored=key in ('red','green','yellow','blue');x=64+i*col
        kw=42 if colored else {'arrows':100,'up_down':100,'left_right':110,'channel':110,'ok':92,'exit':110}.get(key,100)
        if name.startswith('remote_action_'):
            box=(x+kw+18,1005,max(30,col-kw-38),58);node.set('font','Regular;{}'.format(font_px(px,32 if count<=7 else 28)))
        elif name.startswith('r100_footer_sep_'):box=(x+col-12,1007,1,46) if i<count-1 else (0,0,1,1)
        else:
            box=(x,1010 if colored else 1006,kw,42 if colored else 54)
            if name.startswith('remote_key_bg_'):
                node.set('pixmap',plugin_path('skin','images','utility-r96','dot-'+key+'.png') if colored else plugin_path('skin','images','discovery-r48','key-outline-r48.png'))
            else:node.set('font','Regular;{}'.format(font_px(px,30)));node.set('halign','center')
        node.set('position','{},{}'.format(round(box[0]*sx),round(box[1]*sy)));node.set('size','{},{}'.format(max(1,round(box[2]*sx)),max(1,round(box[3]*sy))));node.set('noWrap','1')
    return ET.tostring(root,encoding='unicode')

def setup(screen,kind,items):
    screen._seven_kind=kind;screen._seven_items=tuple(items)
    w,h=skin_metadata(screen.skin).size;screen._seven_size=(w,h)
    screen._seven_px=lambda n:max(1,round(n*min(w/1920.,h/1080.)))
    screen._seven_rects={};screen._seven_poster_paths=OrderedDict();screen._seven_logo_key=None
    bind_missing(screen);screen.skin=prepared_skin(_footer_style,screen.skin,tuple(items))
    if kind=='batch':screen._progress_width=round(476*w/1920.);screen._progress_height=max(1,round(24*h/1080.))
    if kind in ('continue','favorites'):
        screen._continue_progress_width=round(384*w/1920.);screen._continue_progress_height=max(1,round(18*h/1080.))
        screen._list_scroll_geometry=(0,0,1,1)
    screen._seven_refresh_callback=MethodType(refresh,screen)
    screen.onLayoutFinish.append(screen._seven_refresh_callback)
    if hasattr(screen,'onShown'):screen.onShown.append(screen._seven_refresh_callback)

def text(s,name,value,size=30,lines=1,preferred_size=None,*,reuse_current=True,cached_only=False):
    if name in s:
        card_title=name.startswith('continue_title_')
        preferred=font_px(s._seven_px,size) if preferred_size is None else preferred_size
        fit_dynamic_text(s[name],str(value or ''),max_lines=lines,preferred_size=preferred,
            min_size=preferred if card_title else max(9,s._seven_px(20)),fallback_chars=23 if card_title else 90,
            reuse_current=reuse_current,cached_only=cached_only)
def show(s,name,visible):
    if name in s:set_visible(s[name],visible)
def rect(s,name,box):
    if name not in s:return
    w,h=s._seven_size;x,y,a,b=box;box=(round(x*w/1920.),round(y*h/1080.),max(1,round(a*w/1920.)),max(1,round(b*h/1080.)))
    s._seven_rects[name]=box
    instance=getattr(s[name],'instance',None)
    if instance is not None:
        try:
            set_geometry(s[name],box)
        except Exception:pass
def progress(s,name,percent,box):
    percent=max(0,min(100,int(percent)));show(s,name,percent>0)
    x,y,w,h=box;rect(s,name,(x,y,max(1,w*percent/100.),h))

def _paint_footer(s,keys=None,sizes=None):
    items=tuple(getattr(s,'_remote_footer_items',s._seven_items));slots=len(s._seven_items)
    profile=current_text_size()
    px=s._seven_px
    caption_font=font_px(px,32 if slots<=7 else 28,value=profile)
    key_font=font_px(px,30,value=profile)
    key_labels={'arrows':'▲▼','up_down':'▲▼','left_right':'◀▶','channel':'CH±','ok':'OK','exit':'EXIT','menu':'MENU'}
    for i in range(slots):
        item=items[i] if i<len(items) else ('','');key=item[0];visible=bool(key);colored=key in ('red','green','yellow','blue')
        text(s,'remote_action_'+str(i),remote_action_text(item[1]) if visible else '',preferred_size=caption_font)
        if not colored:
            font=font_px(px,sizes[key],value=profile) if sizes and key in sizes else key_font
            text(s,'remote_key_'+str(i),(keys or {}).get(key,key_labels.get(key,key)),preferred_size=font)
        show(s,'remote_key_'+str(i),visible and not colored);show(s,'remote_key_bg_'+str(i),visible);show(s,'remote_action_'+str(i),visible)
    for i in range(7):show(s,'r100_footer_sep_'+str(i),i<len(items)-1 and bool(items[i][0]))
    for name in s:
        if name.startswith(('remote_footer_stage','remote_footer_bg','remote_divider_','remote_web_hint')):show(s,name,False)

def refresh(s):
    kind=s._seven_kind
    text(s,'r100_gt','GT',60);text(s,'r100_brand','IPTV PLAYER PRO',40)
    title=s['header'].getText() if kind=='health' else _(TITLES[kind])
    text(s,'r100_title',title,50)
    text(s,'r100_web_hint',_('Hold MENU for 3 seconds: Web interface'),20)
    if kind=='batch':_batch(s)
    elif kind=='health':_health(s)
    elif kind in ('xmltv','automation'):_settings(s)
    elif kind=='mapping':refresh_mapping(s)
    else:refresh_media(s)
    _paint_footer(s)

def _batch(s):
    from . import main
    total=len(s.accounts);testing=s._state=='testing';percent=int(s._completed*100/total) if total else 100
    text(s,'r100_batch_col1',_('Server'),34);text(s,'r100_batch_col2',_('Status'),34)
    text(s,'r100_side_caption',localized_upper(_('Test status')),38)
    text(s,'r100_batch_percent','{}%'.format(percent),84)
    text(s,'r100_batch_done',_('{} / {} completed').format(s._completed,total),34)
    text(s,'r100_batch_account_caption',_('Account being tested') if testing else _('Information'),29)
    text(s,'r100_batch_account',main._account_test_display_name(s._current_account) if testing and s._current_account is not None else s['subtitle'].getText(),34,3)
    text(s,'r100_batch_state',sentence_caption(_('TESTING...')) if testing else s['subtitle'].getText(),30,2)
    text(s,'r100_batch_state_caption',_('Status'),25)
    text(s,'r100_batch_hint',_('Accounts are checked in order.') if testing else s['review_page'].getText(),25)
    progress(s,'progress_fill',percent,(1364,614,476,24))
    for i in range(s.page_size):
        name=s['result_name_'+str(i)].getText();pending=bool(testing and s._current_pending and name and not any(s['result_status_'+str(i)+suffix].visible for suffix in ('_green','_yellow','_red')))
        show(s,'r100_focus_'+str(i),pending)
        for state in ('green','yellow','red'):show(s,'r100_batch_state_{}_{}'.format(i,state),bool(name and s['result_status_'+str(i)+'_'+state].visible))

def _health(s):
    from . import main
    result=s._result;signal=s._health_signal;kind=main._source_type(s.account)
    for state in ('neutral','green','yellow','red'):
        show(s,'r100_health_icon_'+state,state==signal);show(s,'r100_health_badge_'+state,state==signal)
    for suffix in ('','_green','_yellow','_red'):text(s,'status'+suffix,sentence_caption(s['status'+suffix].getText()),50,2)
    text(s,'status_badge',sentence_caption(s['status_badge'].getText()),32)
    text(s,'r100_latency_caption',_('Latency'),29);text(s,'r100_latency','{} ms'.format(result.latency_ms) if result is not None else '—',52)
    text(s,'r100_health_heading_left',localized_upper(_('Account information') if kind!='m3u' else _('Source information')),36)
    text(s,'r100_health_heading_right',localized_upper(_('Server information')),36)
    structured=result is not None and kind=='xtream'
    captions=(('Account status','Active / Maximum','Expiry','Server time'),('Timezone','Protocol','Output format','Server version'))
    values=((result.account_status,result.connection_text(),result.expiry,result.server_time),(result.timezone,result.protocol,result.output_format,result.server_version)) if structured else (('—',)*4,('—',)*4)
    for side,labels,vals in zip(('left','right'),captions,values):
        for i,(caption,value) in enumerate(zip(labels,vals)):
            text(s,'r100_hc_{}_{}'.format(side,i),_(caption)+':',30,2)
            text(s,'r100_hv_{}_{}'.format(side,i),main._localized_runtime_text(value),30,2)
            show(s,'r100_hc_{}_{}'.format(side,i),structured or result is None)
            show(s,'r100_hv_{}_{}'.format(side,i),structured or result is None)
        show(s,'details_'+side,result is not None and not structured)
    show(s,'r100_api_icon',signal=='green' and result is not None)
    rect(s,'details',(1040 if signal=='green' and result is not None else 532,713,782 if signal=='green' and result is not None else 1284,64))
    text(s,'details',s['details'].getText(),29,2)
    for state in ('green','yellow','red'):text(s,'legend_'+state,sentence_caption(s['legend_'+state].getText()),30)

def _settings(s):
    values=s._values();auto=s._seven_kind=='automation'
    for i,value in enumerate(values):
        text(s,'label_'+str(i),s['label_'+str(i)].getText(),32,2);text(s,'value_'+str(i),value,30,2)
        show(s,'r100_focus_'+str(i),i==s.selected_index)
        flag=(s._automatic() and i in (2,3)) or (auto and i==6) if auto else i==0
        on=(bool(getattr(s.settings,'update_on_startup',True)) if i==2 else bool(getattr(s.settings,'update_in_standby',True))) if auto and i in (2,3) else bool(s.binding.enabled) if not auto else False
        for state in ('green','neutral'):show(s,'r100_pill_{}_{}'.format(i,state),flag and ((state=='green')==on))
        show(s,'r100_pill_text_'+str(i),flag);show(s,'value_'+str(i),not flag)
        if flag:text(s,'r100_pill_text_'+str(i),value,28)
    if auto:
        text(s,'subtitle',_('Automatic updates'),28)
        text(s,'r100_side_caption',localized_upper(_('Update plan')),34)
        text(s,'r100_side_title',values[0],50)
        text(s,'r100_side_info',_('Every {}').format(values[1]) if s._automatic() else _('Manual'),32,3)
        text(s,'r100_side_label',_('Next update'),28)
        text(s,'r100_side_value',values[5],32,3)
        rect(s,'r100_side_label',(1372,696,480,44));rect(s,'r100_side_value',(1372,742,480,50))
        text(s,'r100_lock_note',_('Waking from deep standby is disabled'),27,2)
    else:
        text(s,'r100_side_caption',localized_upper(_('EPG information')),34)
        text(s,'r100_side_title',_('On') if s.binding.enabled else _('Disabled'),50,2)
        text(s,'r100_side_info',_('Enable or disable the external XMLTV guide.'),32,3)
        text(s,'r100_side_label',_('XMLTV source'),28)
        text(s,'r100_side_value',values[2],30,3)

def refresh_mapping(s, previous_index=None):
    count = len(s._services)
    start = s.selected_index // s.page_size * s.page_size
    if previous_index is None:
        mapped = len(s._match.mappings) if s._match is not None else 0
        counts = (_('{} DVB services').format(count), _('{} XMLTV channels').format(len(s._channels)), _('{} mapped').format(mapped))
        for i, value in enumerate(counts):
            text(s, 'r100_count_'+str(i), value, 28)
        text(s, 'r100_map_col1', _('DVB channel'), 30)
        text(s, 'r100_map_col2', _('Mapping'), 30)
        text(s, 'r100_side_caption', localized_upper(_('Selected channel')), 35)
        text(s, 'r100_side_label', _('XMLTV channel'), 30)
        text(s, 'message', s['message'].getText(), 26, 4)
        for i in range(s.page_size):
            service = s._services[start+i] if start+i < count else None
            text(s, 'r100_map_no_'+str(i), '{:02d}'.format(start+i+1) if service else '', 36)
            text(s, 'r100_map_name_'+str(i), service.name if service else '', 36)
            text(s, 'r100_map_status_'+str(i), s._row_status(service) if service else '', 27, 2)
            show(s, 'r100_map_status_bg_'+str(i), service is not None)
            show(s, 'r100_focus_'+str(i), service is not None and start+i == s.selected_index)
    else:
        show(s, 'r100_focus_'+str(previous_index-start), False)
        show(s, 'r100_focus_'+str(s.selected_index-start), True)
    selected = s._services[s.selected_index] if count else None
    text(s,'r100_side_title',selected.name if selected else '—',52,2)
    text(s,'r100_mapping_status',s._row_status(selected) if selected else '—',30,2)
    channel_name='—'
    if selected:
        manual=s._manual_lookup.get(selected.service_ref);xmltv_id=getattr(manual,'xmltv_id','') if manual is not None and not manual.blocked else s._mapped_lookup.get(selected.service_ref,'') if manual is None else ''
        channel=s._channel_lookup.get(xmltv_id)
        channel_name=channel.display_name if channel is not None else xmltv_id or '—'
    text(s,'r100_side_value',channel_name,32,2)
    w,h=s._seven_size
    update_scrollbar(s,'list_scroll',count,s.selected_index,s.page_size,(round(1320*w/1920.),round(252*h/1080.),max(2,round(5*w/1920.)),round(687*h/1080.)))

def _poster_key(item):return tuple(str(getattr(item,k,'') or '') for k in ('content_type','stream_id','parent_id','season','episode','icon'))
def _resume(item):
    position=int(getattr(item,'resume_position',0) or 0);length=int(getattr(item,'resume_length',0) or 0)
    return max(0,min(100,round(position*100./length))) if length>0 else 0,max(1,round(max(0,length-position)/60.)) if length>0 else None
def _media_name(item):return re.sub(r'^\s*S\d+\s*(?:B|E)\d+\s*[-·: ]*','',item.name,flags=re.I) if item.content_type=='series' else item.name

def refresh_media(s, previous_index=None):
    items = s._entries
    count = len(items)
    selected = items[s.selected_index] if 0 <= s.selected_index < count else None
    favorites = s._seven_kind == 'favorites'
    start = s.selected_index // s.page_size * s.page_size if count else 0
    if previous_index is not None:
        show(s, 'r100_media_focus_'+str(previous_index-start), False)
        show(s, 'r100_media_focus_'+str(s.selected_index-start), True)
        _media_details(s, selected, favorites)
        return
    from . import main
    source=main._safe_source_name(s.account)
    text(s,'subtitle',source if favorites else source+' · '+_('Unfinished movies and series episodes'),28)
    pages=(count+s.page_size-1)//s.page_size if count else 0;page=s.selected_index//s.page_size+1 if count else 0
    total=int(s._favorite_counts.get('',0)) if favorites else count
    text(s,'r100_counter',_('{} favorites · Page {} / {}').format(total,page,pages) if favorites else _('{} items · {} / {} pages').format(count,page,pages),28)
    if favorites:
        for i,(key,label) in enumerate(s.FILTERS):
            text(s,'favorite_filter_'+str(i),'{} {}'.format(_(label),s._favorite_counts.get(key,0)),32)
            show(s,'r100_tab_'+str(i),i==s._favorite_filter_index)
    for i in range(s.page_size):
        item=items[start+i] if start+i<count else None
        show(s,'r100_media_focus_'+str(i),item is not None and start+i==s.selected_index and not getattr(s,'_move_active',False))
        show(s,'r100_type_bg_'+str(i),item is not None)
        if item:
            text(s,'continue_title_'+str(i),item.name if favorites else _media_name(item),31 if favorites else 38)
            label=_({'live':'Live TV','movie':'Movies','series':'Series'}.get(item.content_type,'Favorite'))
            text(s,'continue_type_'+str(i),label if favorites else localized_upper(label),25)
        if not favorites:
            p,remaining=_resume(item) if item else (0,None)
            text(s,'r100_percent_'+str(i),'{}%'.format(p) if item else '',29)
            text(s,'r100_remaining_'+str(i),_('{} min remaining').format(remaining) if remaining is not None else '—' if item else '',29)
            show(s,'r100_percent_'+str(i),item is not None);show(s,'r100_remaining_'+str(i),item is not None)
            if item:progress(s,'continue_progress_fill_'+str(i),p,(68+i*444,824,384,18))
    text(s,'r100_side_caption',localized_upper(_('Selected favorite') if favorites else _('Playback information')),34)
    if favorites:
        text(s,'r100_side_label',_('Source'),29);text(s,'r100_side_value',source,34,2)
        text(s,'r100_open_hint','OK   '+_('Open'),32)
    else:
        text(s,'r100_resume_hint',_('Continue from where you left off.'),27,2)
    _media_details(s, selected, favorites)
    if not items or s._error:text(s,'message',s['favorite_info'].getText() if favorites else s['message'].getText(),26,2)
    else:text(s,'message','',23)
    hide_scrollbar(s,'list_scroll')


def _media_details(s, selected, favorites):
    text(s,'r100_side_title',_media_name(selected) if selected else '—',44 if favorites else 42,2)
    if favorites:
        text(s,'r100_side_info',_({'live':'Live TV','movie':'Movies','series':'Series'}.get(selected.content_type,'Favorite')) if selected else '',32)
        key=_poster_key(selected) if selected else None
        if key!=s._seven_logo_key:
            s._seven_logo_key=key;show(s,'r100_side_logo',False);show(s,'r100_logo_placeholder',True)
            text(s,'r100_logo_placeholder','GT' if selected else '—',45)
        if selected:
            path=s._seven_poster_paths.get(key)
            if path:_selected_logo(s,key,path)
    else:
        p,remaining=_resume(selected) if selected else (0,None)
        meta=_('Season {} · Episode {}').format(getattr(selected,'season','—') or '—',getattr(selected,'episode','—') or '—') if selected and selected.content_type=='series' else str(getattr(selected,'year','') or '')
        text(s,'r100_side_info',meta,32,2)
        text(s,'r100_side_value',_('{} min remaining').format(remaining) if remaining is not None else '—',50,2)
        text(s,'r100_resume_percent','{}%'.format(p) if selected else '',29)
        progress(s,'r100_resume_fill',p,(1412,776,432,18));show(s,'r100_resume_bg',selected is not None)


def _selected_logo(s,key,path):
    signature=(key,path,id(getattr(s['r100_side_logo'],'instance',None)))
    if getattr(s,'_r27_selected_logo',None)==signature and getattr(s['r100_side_logo'],'visible',False):return
    def loaded(success):
        if s._closed or key!=s._seven_logo_key:return
        if success:
            s._r27_selected_logo=signature
            show(s,'r100_side_logo',True);show(s,'r100_logo_placeholder',False)
    attach_pixmap(s,'r100_side_logo',path,key='seven-selected-logo',on_loaded=loaded,show_on_load=False)

def poster_after(method):
    @wraps(method)
    def applied(s,slot,path,stream_id,generation,page_start):
        if not hasattr(s,'_seven_kind'):return method(s,slot,path,stream_id,generation,page_start)
        def valid():
            return not s._closed and generation==s._continue_generation and page_start==s.selected_index//s.page_size*s.page_size and 0<=slot<s.page_size and page_start+slot<len(s._entries) and str(s._entries[page_start+slot].stream_id)==str(stream_id)
        if not valid():return
        signature=(generation,page_start,str(stream_id),path,id(getattr(s['continue_poster_'+str(slot)],'instance',None)))
        applied=getattr(s,'_r27_applied_posters',None)
        if applied is None:applied=s._r27_applied_posters={}
        if applied.get(slot)==signature and getattr(s['continue_poster_'+str(slot)],'visible',False):return
        def loaded(success):
            if not valid():return
            if success:applied[slot]=signature
            show(s,'continue_poster_'+str(slot),success)
            show(s,'continue_poster_placeholder_'+str(slot),not success)
        attach_pixmap(s,'continue_poster_'+str(slot),path,key='continue-poster-'+str(slot),on_loaded=loaded,show_on_load=False)
        if getattr(s,'_seven_kind',None)=='favorites' and generation==s._continue_generation and page_start==s.selected_index//s.page_size*s.page_size:
            item=s._entries[page_start+slot] if page_start+slot<len(s._entries) else None
            if item is not None and str(item.stream_id)==str(stream_id):
                key=_poster_key(item);s._seven_poster_paths[key]=path;s._seven_poster_paths.move_to_end(key)
                while len(s._seven_poster_paths)>24:s._seven_poster_paths.popitem(last=False)
                if page_start+slot==s.selected_index:_selected_logo(s,key,path)
    return applied

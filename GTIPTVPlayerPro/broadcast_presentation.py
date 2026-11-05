# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Presentation-only adapters for source banks, live TV and TV archive."""
import datetime
import os,re
from concurrent.futures import Future
from functools import lru_cache, wraps
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import MethodType
from xml.etree import ElementTree as ET
from .i18n import _,localized_short_weekday,localized_upper
from .utility_design import bind_missing
from .seven_presentation import text as fit_text,show,rect,progress,_footer_style,_paint_footer
from .remote_footer import prepared_skin, skin_metadata, layout_needs_fit, _cached_skin
from .scrollbar import update_scrollbar
from .typography import current_text_size, font_px
from .broadcast_layout import restyle

TITLES={'playlists':'Playlists','categories':'LIVE TV CATEGORIES','live':'Live TV','archive':'TV Archive'}

def skin_after(kind):
    def decorate(factory):
        @wraps(factory)
        def built(*args,**kwargs):return restyle(factory(*args,**kwargs),kind)
        return _cached_skin(built)
    return decorate

def refresh_after(method):
    @wraps(method)
    def refreshed(s,*args,**kwargs):
        depth=getattr(s,'_r101_refresh_depth',0);s._r101_refresh_depth=depth+1
        try:result=method(s,*args,**kwargs)
        finally:s._r101_refresh_depth=depth
        if depth==0 and hasattr(s,'_broadcast_kind') and not kwargs.get('selection_only', False):
            refresh(s)
        return result
    return refreshed

def text(s,name,value,size=30,lines=1):
    if name not in s:return
    value=str(value or '');key=(value,size,lines)
    cached=s._r101_text.get(name)
    if s._r101_force_fit or cached is None or cached[0]!=key or s[name].getText()!=cached[1]:
        fonts=getattr(s,'_r101_category_fonts',None)
        preferred_size=fonts.get(size) if fonts is not None else None
        fit_text(s,name,value,size,lines,preferred_size=preferred_size,reuse_current=False)
        s._r101_text[name]=(key,s[name].getText())

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

_LIVE_PANELS = ('r101_left_panel', 'r101_center_panel', 'r101_right_panel', 'r101_footer_panel')
_LIVE_ROW_PREFIXES = ('r101_live_row_', 'r101_live_focus_')


@lru_cache(maxsize=4)
def _live_art_markup(skin, page_size):
    names = set(_LIVE_PANELS)
    names.update(prefix + str(slot) for prefix in _LIVE_ROW_PREFIXES for slot in range(page_size))
    root = ET.Element('screen')
    for node in ET.fromstring(skin).findall('widget'):
        if node.get('name') in names:
            ET.SubElement(root, 'widget', {key: node.attrib[key] for key in ('name', 'position', 'size', 'pixmap')
                                         if key in node.attrib})
    return ET.tostring(root, encoding='unicode')


def _encode_live_art(image, _path):
    stream = BytesIO()
    image.convert('RGB').save(stream, format='PNG', compress_level=1)
    return stream.getvalue()


def _build_live_art(job, markup, page_size):
    try:
        from .opaque_row_art import prepare_opaque_panels, prepare_opaque_rows
        panels = prepare_opaque_panels(markup, _LIVE_PANELS, loader=_encode_live_art)
        rows = prepare_opaque_rows(markup, 'r101_center_panel', 'r101_live_row_', page_size,
                                   ((), ('r101_live_focus_',)), loader=_encode_live_art)
    except Exception as error:
        job.set_exception(error)
    else:
        job.set_result((panels, rows))


@lru_cache(maxsize=2)
def _live_art_job(markup, page_size):
    job = Future()
    Thread(target=_build_live_art, args=(job, markup, page_size), daemon=True).start()
    return job


@lru_cache(maxsize=2)
def _decode_live_art(job):
    from enigma import loadPNG
    from .opaque_row_art import OpaqueRowArt
    try:
        panels, rows = job.result()
        with TemporaryDirectory(prefix='gt-live-pixmaps-') as folder:
            def decode(data, name):
                path = Path(folder, name + '.png')
                path.write_bytes(data)
                return loadPNG(str(path))

            decoded = {name: pixmap for name, data in panels.items()
                       if (pixmap := decode(data, name)) is not None}
            if rows is not None:
                tiles = [[decode(data, f'row-{slot}-{state}') for state, data in enumerate(variants)]
                         for slot, variants in enumerate(rows.pixmaps)]
                rows = (OpaqueRowArt(rows.prefix, rows.overlay_prefixes, tiles)
                        if all(pixmap is not None for variants in tiles for pixmap in variants) else None)
        return decoded, rows
    except Exception:
        return {}, None


@lru_cache(maxsize=3)
def _live_skin_with_prepared_art(skin, page_size, panel_names, has_rows):
    prepared = set(panel_names)
    if has_rows:
        prepared.update(prefix + str(slot)
                        for prefix in _LIVE_ROW_PREFIXES
                        for slot in range(page_size))
    root = ET.fromstring(skin)
    for widget in root.findall('widget'):
        if widget.get('name') in prepared:
            # Install the prepared pixmap on layout instead of decoding a
            # second copy from the original skin asset.
            widget.attrib.pop('pixmap', None)
    return ET.tostring(root, encoding='unicode')


def install_ready_live_art(screen):
    job = screen._live_art_job
    if job is None or not job.done():
        return
    from .opaque_row_art import install_opaque_panels
    screen._opaque_live_panels, screen._opaque_stream_rows = _decode_live_art(job)
    screen._live_art_job = None
    install_opaque_panels(screen, screen._opaque_live_panels)
    _live_rows(screen)

def setup(s,kind,items):
    s._broadcast_kind=kind;s._seven_items=tuple(items)
    w,h=skin_metadata(s.skin).size;s._seven_size=(w,h);s._seven_px=lambda n:max(1,round(n*min(w/1920.,h/1080.)));s._seven_rects={}
    s._r101_text={};s._r101_force_fit=False;s._r101_logo_signature=None
    if kind=='categories':
        from .native_live_category_list import NativeLiveCategoryList
        s['r101_category_list'] = NativeLiveCategoryList(w,h)
    bind_missing(s);s.skin=prepared_skin(footer_style,s.skin,tuple(items))
    if kind=='categories':
        from .opaque_row_art import DeferredOpaqueArt, install_opaque_panels
        s._opaque_category_panels = {}

        def category_art_ready(panels, unused_rows):
            s._opaque_category_panels = panels
            install_opaque_panels(s, panels)
            from .browser_common import LIVE_STREAM_PAGE_SIZE
            from .browser_live import _live_stream_skin
            _live_art_job(_live_art_markup(_live_stream_skin(), LIVE_STREAM_PAGE_SIZE), LIVE_STREAM_PAGE_SIZE)

        DeferredOpaqueArt(s, ('r101_categories_panel', 'r101_weather_panel', 'r101_footer_panel'),
                          (), category_art_ready)
        s._list_scroll_geometry=(round(959*w/1920.),round(190*h/1080.),max(2,round(5*w/1920.)),round(665*h/1080.))
        show(s,'r101_category_measure_number',False)
        show(s,'r101_category_measure_name',False)
    if kind=='live':
        job = _live_art_job(_live_art_markup(s.skin, s.page_size), s.page_size)
        s._live_art_job = job
        s._opaque_live_panels, s._opaque_stream_rows = {}, None
        if job.done():
            s._opaque_live_panels, s._opaque_stream_rows = _decode_live_art(job)
            s._live_art_job = None
            s.skin = _live_skin_with_prepared_art(s.skin, s.page_size, tuple(s._opaque_live_panels),
                                                  s._opaque_stream_rows is not None)
        px=s._seven_px;s._progress_width=round(398*w/1920.);s._progress_height=max(1,round(12*h/1080.))
        s._live_channel_name_font=font_px(px,30);s._live_channel_name_min_font=font_px(px,22)
        s._ribbon_now_preferred_font=font_px(px,36);s._ribbon_now_min_font=font_px(px,25)
        # Reuse the current-EPG poster pipeline and its saved metadata setting.
        # Mini-TV geometry and all navigation/playback actions remain intact.
    if kind=='archive':
        from .opaque_row_art import DeferredOpaqueArt

        def archive_art_ready(unused_panels, rows):
            s._opaque_archive_channels, s._opaque_archive_programmes = rows
            _archive(s)

        DeferredOpaqueArt(s, (), (
            ('r101_channels_panel', 'r101_channel_row_', 12,
             ((), ('r101_channel_selected_',), ('r101_channel_selected_', 'r101_channel_focus_'))),
            ('r101_programmes_panel', 'r101_programme_row_', 5,
             ((), ('r101_programme_selected_',), ('r101_programme_selected_', 'r101_programme_focus_'))),
        ), archive_art_ready)
    s._broadcast_layout_callback=MethodType(layout_ready,s)
    s.onLayoutFinish.append(s._broadcast_layout_callback)
    if hasattr(s,'onShown'):s.onShown.append(s._broadcast_layout_callback)
    if kind != 'live':
        refresh(s)

def layout_ready(s):
    s._r101_force_fit=layout_needs_fit(s,'_r28_broadcast_layout')
    if s._broadcast_kind == 'live':
        install_ready_live_art(s)
    if s._broadcast_kind in ('categories', 'live'):
        if not s._r101_force_fit:
            return
        from .opaque_row_art import install_opaque_panels
        panels = s._opaque_category_panels if s._broadcast_kind == 'categories' else s._opaque_live_panels
        install_opaque_panels(s, panels)
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
    _paint_footer(s,keys={'channel_page':'CH+/CH−'},sizes={'channel_page':28})

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

def refresh_category_list(screen, full=False):
    """Update the visible list independently of the forecast and footer."""
    if screen._closed:
        return
    entries = () if screen._loading or screen._error else screen._entries
    count = len(entries)
    selected = screen.selected_index % count if count else -1
    if count:
        screen.selected_index = selected
    start = selected // screen.page_size * screen.page_size if count else 0
    row = selected - start
    category_list = screen['r101_category_list']
    visible = max(0, min(screen.page_size, count - start))
    if full or visible != getattr(screen, '_r101_category_visible', -1):
        for slot in range(screen.page_size):
            show(screen, 'category_row_art_' + str(slot), slot < visible and not category_list.has_opaque_rows)
            show(screen, 'category_focus_art_' + str(slot), slot == row and not category_list.has_opaque_rows)
        screen._r101_category_visible = visible
    elif not category_list.has_opaque_rows:
        previous = screen._r101_category_selected_row
        if row != previous:
            if previous >= 0:
                show(screen, 'category_focus_art_' + str(previous), False)
            if row >= 0:
                show(screen, 'category_focus_art_' + str(row), True)
    screen._r101_category_selected_row = row
    category_list.sync(screen, entries, selected, start)
    show(screen, 'r101_category_list', bool(count))
    show(screen, 'items', not count)
    message = (screen._r101_category_counter.format(count, selected + 1, count)
               if count else screen['message'].getText())
    text(screen, 'message', message, 25)
    update_scrollbar(screen, 'list_scroll', count, selected, screen.page_size, screen._list_scroll_geometry)


def _categories(s):
    profile = current_text_size()
    s._r101_category_fonts = {size: font_px(s._seven_px, size, value=profile) for size in (25, 30, 33)}
    s._r101_category_counter = _('{} categories · {} / {}')
    text(s,'categories_caption',localized_upper(_('Categories')),38)
    refresh_category_list(s, full=True)
    refresh_category_weather(s)


def refresh_category_weather(s):
    if getattr(s, '_broadcast_kind', None) != 'categories' or s._closed:
        return
    text(s,'weather_caption',s['weather_caption'].getText(),36)
    city=getattr(s,'_dynamic_weather_city_text',s['weather_city'].getText()).lstrip('● ').strip()
    text(s,'weather_city','●  '+city,38)
    text(s,'weather_temp',s['weather_temp'].getText(),96)
    for n,size in (('weather_desc',34),('weather_metrics',24),('weather_updated',22)):
        text(s,n,s[n].getText(),size,2 if n=='weather_desc' else 1)
    for i in range(5):
        for prefix,size in (('weather_day_',29),('weather_day_high_',36),('weather_day_low_',29)):text(s,prefix+str(i),s[prefix+str(i)].getText(),size)

def _live_row_art(s, slot, present, selected):
    artwork = getattr(s, '_opaque_stream_rows', None)
    if artwork is not None and artwork.paint(s, slot, present, int(selected)):
        return
    show(s,'r101_live_row_'+str(slot),present)
    show(s,'r101_live_focus_'+str(slot),selected)


def _live_rows(s):
    count=0 if s._loading else len(s._entries)
    start=s.selected_index//s.page_size*s.page_size if count else 0
    for i in range(s.page_size):
        present=start+i<count
        _live_row_art(s, i, present, present and start+i==s.selected_index)
        if present:
            text(s,'stream_name_'+str(i),s._entries[start+i].name,30,2)


def _live_detail(s):
    selected=s._entries[s.selected_index] if 0<=s.selected_index<len(s._entries) else None
    for n,size,lines in (('ribbon_now_title',36,2),('ribbon_now_time',26,1),('epg_progress',26,1),('ribbon_now_caption',29,1),('epg_title',30,1)):
        value=getattr(s,'_r101_now_title',s[n].getText()) if n=='ribbon_now_title' else s[n].getText()
        if n=='epg_progress' and re.match(r'^%\d+$',value):value=value[1:]+'%'
        text(s,n,value,size,lines)
    for n in ('next','third','fourth','fifth'):
        text(s,'epg_'+n+'_time',s['epg_'+n+'_time'].getText(),24)
        text(s,'epg_'+n+'_title',s['epg_'+n+'_title'].getText(),24,2)
    if selected and getattr(s,'_detail_selection_key',None)==s._selection_key(selected):
        events=s._detail_window();event=events[0] if events else None
        if event is not None:
            ratio,value=s._progress_details(event);progress(s,'epg_progress_fill',round(ratio*100),(56,380,398,12))


def _live_page(s):
    if s._loading:
        text(s, 'message', '', 27)
        return
    count=len(s._entries)
    if count and not s._error:
        pages=(count+s.page_size-1)//s.page_size;text(s,'message','{} / {}'.format(s.selected_index//s.page_size+1,pages),27)
    elif s._error or not count:
        text(s,'message','',27)
        text(s,'ribbon_now_title',_(s._error) if s._error else s['message'].getText() or _('No channels found; press GREEN to try again.'),28,2)


def refresh_live_selection(s, rows_changed, previous_row):
    """Paint the live list selection without refreshing static screen chrome."""
    if rows_changed:
        _live_rows(s)
        _live_page(s)
    else:
        selected_row = s._live_render_selected_row
        if previous_row != selected_row:
            if previous_row >= 0:
                _live_row_art(s, previous_row, True, False)
            _live_row_art(s, selected_row, True, True)
    _live_detail(s)


def _live(s):
    _live_rows(s)
    text(s,'r101_epg_caption',localized_upper(_('Broadcast schedule')),36)
    text(s,'summary_caption',localized_upper(_('Summary')),27)
    _live_detail(s)
    text(s,'clock_time',s['clock_time'].getText()[:5],35)
    text(s,'clock_date',s['clock_date'].getText().replace('\n',' · '),24)
    _live_page(s)


def _archive_row_art(s, kind, slot, present, selected):
    focused = selected and s.focus == kind + 's'
    artwork = getattr(s, '_opaque_archive_' + kind + 's', None)
    state = 2 if focused else int(selected)
    if artwork is not None and artwork.paint(s, slot, present, state):
        return
    prefix = 'r101_' + kind
    show(s, prefix + '_row_' + str(slot), present)
    show(s, prefix + '_selected_' + str(slot), selected)
    show(s, prefix + '_focus_' + str(slot), focused)


def _archive(s):
    text(s,'channels_heading',localized_upper(_('Channels')),35)
    text(s,'r101_detail_caption',localized_upper(_('Programme information')),34)
    text(s,'r101_time_caption',_('Time'),29);text(s,'r101_program_caption',_('Programme'),29);text(s,'r101_duration_caption',_('Duration'),29)
    start=s.channel_index//12*12
    for i in range(12):
        present=start+i<len(s.channels);selected=present and start+i==s.channel_index
        _archive_row_art(s, 'channel', i, present, selected)
        show(s,'r101_channel_icon_'+str(i),present)
        text(s,'channel_'+str(i),s.channels[start+i].name if present else '',27)
    first=s.day_index//4*4;amount=min(4,len(s.days)-first)
    for i in range(4):
        present=i<amount;selected=present and first+i==s.day_index
        x=554+i*(302 if amount<=2 else 204);width=290 if amount<=2 else 194
        for prefix in ('r101_day_idle_','r101_day_selected_','r101_day_focus_'):
            rect(s,prefix+str(i),(x,124,width,58))
        rect(s,'day_'+str(i),(x+8,128,width-16,50))
        show(s,'r101_day_idle_'+str(i),present);show(s,'r101_day_selected_'+str(i),selected);show(s,'r101_day_focus_'+str(i),selected and s.focus=='days')
        day = s.days[first+i] if present else None
        value = '{}  {}'.format(localized_short_weekday(day.isoformat()), day.strftime('%d.%m')) if day else ''
        text(s,'day_'+str(i),value,29)
    refresh_archive_programmes(s)
    text(s,'day_caption',s['day_caption'].getText(),31,2)
    for name in ('channel_count','day_count'):text(s,name,s[name].getText(),26)


def refresh_archive_programmes(s, previous_index=None):
    start=s.programme_index//5*5
    rows = range(5) if previous_index is None else (previous_index - start, s.programme_index - start)
    for i in rows:
        present=start+i<len(s.programmes);selected=present and start+i==s.programme_index
        _archive_row_art(s, 'programme', i, present, selected)
        if previous_index is None:
            programme = s.programmes[start+i] if present else None
            time_text = datetime.datetime.fromtimestamp(programme.start).strftime('%H:%M') if programme else ''
            duration = _('{} min').format(max(1, int((programme.end-programme.start+59)//60))) if programme else ''
            text(s, 'programme_'+str(i), programme.title if programme else '', 30)
            text(s, 'programme_time_'+str(i), time_text, 30)
            text(s, 'programme_duration_'+str(i), duration, 27)
    programme=s.programmes[s.programme_index] if s.programmes and 0<=s.programme_index<len(s.programmes) else None
    time_text = '—'
    if programme:
        time_text = '{} – {}'.format(
            datetime.datetime.fromtimestamp(programme.start).strftime('%H:%M'),
            datetime.datetime.fromtimestamp(programme.end).strftime('%H:%M'),
        )
    text(s, 'r101_detail_time', time_text, 34)
    text(s,'r101_detail_duration',_('{} min').format(max(1,int((programme.end-programme.start+59)//60))) if programme else '',30)
    text(s,'r101_archive_watch',_('Watch from archive') if programme else '',31)
    text(s,'detail_title',programme.title if programme else '—',48,2)
    text(s,'detail_description',getattr(programme,'description','') if programme else '',28,6)
    show(s,'r101_archive_icon',programme is not None)
    count = '{}/{}'.format(s.programme_index + 1, len(s.programmes)) if s.programmes else ''
    text(s, 'programme_count', count, 26)

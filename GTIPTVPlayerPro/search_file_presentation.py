# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native presentation; existing search, navigation and selection stay intact."""
import os,re
from functools import wraps
from types import MethodType
from xml.etree import ElementTree as ET
from .i18n import _,localized_upper
from .utility_design import bind_missing
from .seven_presentation import text as fit_text,show,_footer_style,_paint_footer
from .scrollbar import update_scrollbar,hide_scrollbar
from .typography import font_px
from .search_file_layout import restyle,search_scroll_geometry,file_list_geometry

def skin_after(kind):
    def decorate(factory):
        @wraps(factory)
        def built(*a,**kw):return restyle(factory(*a,**kw),kind)
        return built
    return decorate

def refresh_after(method):
    @wraps(method)
    def refreshed(s,*a,**kw):
        result=method(s,*a,**kw)
        if hasattr(s,'_r102_kind'):refresh(s)
        return result
    return refreshed

def text(s,name,value,size=30,lines=1):
    if name not in s:return
    value=str(value or '');key=(value,size,lines)
    cached=s._r102_text.get(name)
    if s._r102_force_fit or cached is None or cached[0]!=key or s[name].getText()!=cached[1]:
        fit_text(s,name,value,size,lines)
        s._r102_text[name]=(key,s[name].getText())

def setup(s,kind,items):
    s._r102_kind=kind;s._r102_text={};s._r102_force_fit=False
    s._seven_items=tuple(items)
    w,h=map(int,ET.fromstring(s.skin).get('size').split(','))
    s._seven_size=(w,h);s._seven_px=lambda n:max(1,round(n*min(w/1920.,h/1080.)));s._seven_rects={}
    bind_missing(s);s.skin=_footer_style(s.skin,items)
    if kind=='files':
        root=ET.fromstring(s.skin)
        for prefix in ('remote_key_','remote_key_bg_'):
            n=root.find("widget[@name='"+prefix+"0']")
            n.set('size','{},{}'.format(round(150*w/1920.),round(54*h/1080.)))
        n=root.find("widget[@name='remote_action_0']")
        n.set('position','{},{}'.format(round(232*w/1920.),round(1005*h/1080.)))
        n.set('size','{},{}'.format(round(164*w/1920.),round(58*h/1080.)))
        s.skin=ET.tostring(root,encoding='unicode')
        s._r102_file_callback=MethodType(refresh,s)
        callbacks=getattr(s['filelist'],'onSelectionChanged',None)
        if isinstance(callbacks,list):callbacks.append(s._r102_file_callback)
        s.onClose.append(MethodType(close_files,s))
    else:
        from .search_poster import SearchPoster
        s._scroll_geometry=search_scroll_geometry(w,h)
        s._search_title_preferred_font=font_px(s._seven_px,36)
        s._search_title_min_font=font_px(s._seven_px,26,value='standard')
        s._r102_poster=SearchPoster(s)
        s['r102_search_poster'].hide()
        s.onClose.append(s._r102_poster.close)
    s._r102_layout_callback=MethodType(layout_ready,s)
    s.onLayoutFinish.append(s._r102_layout_callback)
    if hasattr(s,'onShown'):s.onShown.append(s._r102_layout_callback)
    refresh(s)

def layout_ready(s):
    if s._r102_kind=='files':
        unused_box,row=file_list_geometry(*s._seven_size)
        native=getattr(s['filelist'],'l',None)
        if native is not None and callable(getattr(native,'setItemHeight',None)):native.setItemHeight(row)
    s._r102_force_fit=True
    try:refresh(s)
    finally:s._r102_force_fit=False

def refresh(s):
    if getattr(s,'_closed',False) or getattr(s,'_r102_closed',False):return
    text(s,'r102_gt','GT',60);text(s,'r102_brand','IPTV PLAYER PRO',40)
    text(s,'r102_web_hint',_('Hold MENU for 3 seconds: Web interface'),20)
    if s._r102_kind=='files':_files(s)
    else:_search(s)
    _paint_footer(s)
    if s._r102_kind=='files':text(s,'remote_key_0','▲▼◀▶',28)

def close_files(s):
    s._r102_closed=True
    callbacks=getattr(s['filelist'],'onSelectionChanged',None)
    if isinstance(callbacks,list) and s._r102_file_callback in callbacks:callbacks.remove(s._r102_file_callback)

def _search(s):
    from .browser_common import _rating_label
    series=s.content_type=='series'
    text(s,'r102_title',_(s._search_title_message),48)
    text(s,'query_caption',_('Series name') if series else _('Movie name'),28)
    text(s,'query_text',s._query or _(s._query_hint_message),36)
    text(s,'r102_results_caption',localized_upper(_('Search results')),42)
    text(s,'result_caption',_(s._result_title_message),27)
    text(s,'r102_detail_caption',localized_upper(_('Series information') if series else _('Movie information')),40)
    count=len(s._results)
    start=s.selected_index//s.page_size*s.page_size if count else 0
    global_start=(s._current_page-1)*s._result_page_size
    for row in range(s.page_size):
        item=s._results[start+row] if start+row<count else None
        present=item is not None
        show(s,'r102_result_focus_'+str(row),present and start+row==s.selected_index)
        show(s,'r102_result_line_'+str(row),present)
        show(s,'r102_result_favorite_'+str(row),present and s._is_favorite(item))
        name=str(getattr(item,'name','') or '') if present else ''
        year=str(getattr(item,'year','') or '') if present else ''
        # The year has its own column. Retain all other provider identity tags.
        if year and re.fullmatch(r'\d{4}',year):name=re.sub(r'\s*\('+re.escape(year)+r'\)\s*$','',name).rstrip()
        text(s,'catalog_result_'+str(row),name,32)
        text(s,'r102_result_number_'+str(row),'{:05d}'.format(global_start+start+row+1) if present else '',28)
        text(s,'r102_result_year_'+str(row),year,29)
    show(s,'message',not count)
    if count:
        total=s._total_items or global_start+count
        text(s,'r102_position','{} / {}'.format(global_start+s.selected_index+1,total),28)
        item=s._results[s.selected_index]
        rating=_rating_label(getattr(item,'rating',''))
        valid_rating=rating.startswith('★ ')
        show(s,'r102_rating_star',valid_rating)
        text(s,'detail_rating',rating[2:] if valid_rating else '',38)
        text(s,'detail_title',item.name,36,2)
        meta=[str(getattr(item,key,'') or '') for key in ('year','duration')]
        text(s,'detail_meta','\n'.join(value for value in meta if value),30,3)
        plot=str(getattr(item,'plot','') or '') or _(s._empty_summary_message)
        text(s,'detail_plot',plot,27,2)
    else:
        show(s,'r102_rating_star',False)
        text(s,'r102_position','',28)
        text(s,'message',s['message'].getText(),31,4)
        text(s,'detail_title',s['detail_title'].getText(),36,2)
        text(s,'detail_plot',s['detail_plot'].getText(),27,2)
    s._r102_poster.schedule()

def _file_entry(raw):
    try:path,is_dir=raw[0][:2]
    except (IndexError,TypeError,ValueError):return ('','storage','')
    name=''
    for part in raw[1:]:
        if isinstance(part,(list,tuple)) and len(part)>=8 and isinstance(part[7],str):
            name=part[7];break
    if not name and path:name=os.path.basename(str(path).rstrip('/')) or str(path)
    if path is None and is_dir:name=name.strip('<> ')
    icon='storage' if path is None else 'folder' if is_dir else 'document'
    return (name,icon,str(path or ''))

def _files(s):
    text(s,'r102_title',_('Choose file'),48)
    text(s,'instruction',_('Playlist · Device, USB or mounted network storage · TXT / .txt'),26)
    text(s,'r102_path_caption',_('Current location'),25)
    text(s,'r102_files_caption',localized_upper(_('Folders and files')),29)
    text(s,'r102_selected_caption',localized_upper(_('Selected item')),40)
    text(s,'r102_filter_caption',_('File filter'),28)
    text(s,'r102_filter_value','TXT / .txt',33)
    text(s,'r102_location_caption',_('Current location'),28)
    text(s,'r102_file_help',_('Open the folder containing your playlist.'),25)
    files=s['filelist']
    rows=files.getFileList() if callable(getattr(files,'getFileList',None)) else getattr(files,'list',[])
    rows=rows or []
    try:index=max(0,int(files.getSelectionIndex()))
    except (TypeError,ValueError,AttributeError):index=0
    index=min(index,max(0,len(rows)-1))
    start=index//11*11
    directory=files.getCurrentDirectory()
    location=directory or _('Storage')
    text(s,'path',location,30)
    text(s,'r102_location_value',location,32,2)
    for row in range(11):
        present=start+row<len(rows)
        name,icon,unused=_file_entry(rows[start+row]) if present else ('','','')
        text(s,'r102_file_name_'+str(row),name,31)
        show(s,'r102_file_focus_'+str(row),present and start+row==index)
        show(s,'r102_file_line_'+str(row),present)
        for kind in ('folder','storage','document'):show(s,'r102_file_{}_{}'.format(kind,row),present and kind==icon)
    name,icon,path=_file_entry(rows[index]) if rows else ('','','')
    text(s,'r102_selected_name',name or _('Select playlist'),38,3)
    for kind in ('folder','storage','document'):show(s,'r102_selected_'+kind,kind==icon)
    help_message=_('View connected storage locations.') if icon=='storage' else _('Open this folder to browse its contents.') if icon=='folder' else _('Press GREEN or OK to select this playlist.') if icon=='document' else _('No files found.')
    text(s,'r102_selected_help',help_message,29,3)
    show(s,'r102_file_empty',not rows)
    text(s,'r102_file_empty',_('No files found.') if not rows else '',31,3)
    w,h=s._seven_size
    geometry=(round(1274*w/1920.),round(282*h/1080.),max(2,round(5*w/1920.)),round(616*h/1080.))
    if rows:update_scrollbar(s,'r102_file_scroll',len(rows),index,11,geometry)
    else:hide_scrollbar(s,'r102_file_scroll')

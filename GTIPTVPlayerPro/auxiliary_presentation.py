# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native presentation only; existing controllers own data, actions and jobs."""
from functools import wraps
from types import MethodType
from xml.etree import ElementTree as ET
from .i18n import _, localized_upper, localized_language_name
from .remote_footer import remote_action_text, set_remote_footer
from .seven_presentation import text as fit_text, show, rect, _footer_style
from .scrollbar import update_scrollbar, hide_scrollbar
from .utility_design import bind_missing
from .typography import font_px
from .auxiliary_layout import restyle


def skin_after(kind, screen_name=None):
    def decorate(factory):
        @wraps(factory)
        def built(*a, **kw):
            markup = factory(*a, **kw)
            if screen_name and ET.fromstring(markup).get('name') != screen_name:
                return markup
            return restyle(markup, kind)
        return built
    return decorate


def refresh_after(method):
    @wraps(method)
    def refreshed(s, *a, **kw):
        depth = getattr(s, '_aux_refresh_depth', 0)
        s._aux_refresh_depth = depth + 1
        try:
            result = method(s, *a, **kw)
        finally:
            s._aux_refresh_depth = depth
        if not depth and hasattr(s, '_aux_kind'):
            refresh(s)
        return result
    return refreshed


def text(s, name, value, size=30, lines=1):
    if name not in s:
        return
    value = str(value or '')
    key = (value, size, lines)
    cached = s._aux_text.get(name)
    if s._aux_force_fit or cached is None or cached[0] != key or s[name].getText() != cached[1]:
        fit_text(s, name, value, size, lines)
        s._aux_text[name] = (key, s[name].getText())


def box(s, values):
    w, h = s._seven_size
    return tuple(max(1 if i > 1 else 0, round(v * (w / 1920. if i % 2 == 0 else h / 1080.))) for i, v in enumerate(values))


def _ordered_items(items, kind):
    order = ('arrows', 'ok', 'green', 'yellow', 'blue', 'red', 'exit') if kind in ('online', 'local') else ()
    return tuple(sorted(items, key=lambda item: order.index(item[0]) if item[0] in order else len(order))) if order else tuple(items)


def _footer_skin(s, items):
    root = ET.fromstring(_footer_style(s.skin, items))
    # Allocate space to the actual translated captions, including long history actions.
    weights = []
    for key, action, *unused in items:
        kw = 42 if key in ('red', 'green', 'yellow', 'blue') else {'arrows':100, 'up_down':100, 'left_right':110, 'ok':92, 'exit':110}.get(key,100)
        weights.append((kw, kw + 46 + len(remote_action_text(action)) * 15))
    total = sum(n[1] for n in weights) or 1
    x = 62.
    for i, (kw, weight) in enumerate(weights):
        col = 1796. * weight / total
        for prefix in ('remote_key_', 'remote_key_bg_', 'remote_action_'):
            node = root.find("widget[@name='" + prefix + str(i) + "']")
            if node is None:
                continue
            colored = items[i][0] in ('red', 'green', 'yellow', 'blue')
            logical = (x + kw + 18, 996, max(32, col-kw-32), 60) if prefix == 'remote_action_' else (x, 1004 if colored else 998, kw, 42 if colored else 54)
            a,b,c,d = box(s, logical)
            node.set('position', '{},{}'.format(a,b)); node.set('size','{},{}'.format(c,d))
        node = root.find("widget[@name='aux_footer_sep_" + str(i) + "']")
        if node is not None:
            a,b,c,d = box(s, (x+col-7,1006,1,42))
            node.set('position','{},{}'.format(a,b));node.set('size','{},{}'.format(c,d))
        x += col
    return ET.tostring(root, encoding='unicode')


def setup(s, kind, items):
    s._aux_kind = kind; s._aux_text = {}; s._aux_force_fit = False
    s._seven_items = _ordered_items(items, kind)
    w,h = map(int, ET.fromstring(s.skin).get('size').split(','))
    s._seven_size = (w,h); s._seven_px = lambda n:max(1,round(n*min(w/1920.,h/1080.)))
    s._seven_rects = {}; s._aux_flag_key = None
    bind_missing(s)
    set_remote_footer(s, s._seven_items)
    s.skin = _footer_skin(s, s._seven_items)
    s._aux_footer_keys = tuple(item[0] for item in s._seven_items)
    if kind in ('channels','categories'):
        s._list_scroll_geometry = box(s,(1856,254,6,640) if kind=='channels' else (1194,214,6,620))
    s._aux_layout_callback = MethodType(layout_ready, s)
    s.onLayoutFinish.append(s._aux_layout_callback)
    if hasattr(s,'onShown'): s.onShown.append(s._aux_layout_callback)
    refresh(s)


def layout_ready(s):
    s._aux_force_fit = True
    try:
        if s._aux_kind == 'about':
            # Use the full original legal/privacy message and the actual font profile.
            width = box(s,(0,0,1160,770))[2]
            preferred = font_px(s._seven_px,26)
            s._message_visible_lines = max(6, int(box(s,(0,0,1160,770))[3]/max(1,preferred*1.28)))
            columns = max(24, int(width/max(1,preferred*.58)))
            s._message_lines = s._wrapped_message_lines(s._aux_message_source, columns)
            s._message_scroll_geometry = box(s,(1274,148,5,744))
            s._refresh_message()
        refresh(s)
    finally:
        s._aux_force_fit = False


def _footer(s):
    items = _ordered_items(getattr(s,'_remote_footer_items',s._seven_items),s._aux_kind)
    if items != getattr(s,'_remote_footer_items',()):set_remote_footer(s,items)
    keys=tuple(item[0] for item in items)
    if keys!=s._aux_footer_keys:
        # The category move mode changes both meanings and key shapes.
        from .background import attach_native_alpha_pixmap
        old=s._aux_footer_keys;s._aux_footer_keys=keys;s.skin=_footer_skin(s,items)
        w,h=s._seven_size
        for node in ET.fromstring(s.skin):
            name=node.get('name','')
            if not name.startswith(('remote_key_','remote_action_','aux_footer_sep_')):continue
            x,y=map(int,node.get('position').split(','));a,b=map(int,node.get('size').split(','))
            rect(s,name,(x*1920./w,y*1080./h,a*1920./w,b*1080./h))
            if name.startswith('remote_key_bg_'):
                i=int(name.rsplit('_',1)[1])
                if i<len(keys) and keys[i] and (i>=len(old) or keys[i]!=old[i]):
                    attach_native_alpha_pixmap(s,name,node.get('pixmap'),key='aux-footer-'+str(i),show_on_load=False)
    for i,item in enumerate(items):
        key,action=item[:2];colored=key in ('red','green','yellow','blue')
        text(s,'remote_action_'+str(i),remote_action_text(action),29 if len(items)>=7 else 31)
        text(s,'remote_key_'+str(i),{'arrows':'▲▼','up_down':'▲▼','left_right':'◀▶','channel':'CH±','ok':'OK','exit':'EXIT','menu':'MENU'}.get(key,key),29)
        show(s,'remote_key_'+str(i),bool(key) and not colored)
        show(s,'remote_key_bg_'+str(i),bool(key));show(s,'remote_action_'+str(i),bool(key))
    for i in range(8):show(s,'aux_footer_sep_'+str(i),i<len(items)-1 and bool(items[i][0]))
    for name in s:
        if name.startswith(('remote_footer_stage','remote_footer_bg','remote_divider_','remote_web_hint')):show(s,name,False)


def refresh(s):
    if getattr(s,'_closed',False):return
    text(s,'aux_gt','GT',60);text(s,'aux_brand','IPTV PLAYER PRO',40)
    text(s,'aux_web_hint',_('Hold MENU for 3 seconds: Web interface'),20)
    titles={'source':'Add M3U source','history':getattr(s,'HISTORY_TITLE','Search history'),'youtube':'YouTube','online':'Subtitles','local':'Choose a subtitle file','categories':'Category Management','channels':'Search All Channels','weather':'Weather','about':'About'}
    text(s,'aux_title',_(titles[s._aux_kind]),46)
    {'source':_source,'history':_history,'youtube':_youtube,'online':_online,'local':_local,'categories':_categories,'channels':_channels,'weather':_weather,'about':_about}[s._aux_kind](s)
    _footer(s)


def _source(s):
    remote=s._field_values[1]=='remote'
    text(s,'instruction',_('Choose the source name and location.'),29)
    text(s,'aux_source_location_caption',localized_upper(_('Source location')),28)
    text(s,'name_value',s._field_values[0] or _('Enter a source name'),35)
    text(s,'location_value',s._field_values[2],29)
    for field in (0,2):show(s,'aux_field_focus_'+str(field),s.selected_field==field)
    for i in range(2):
        show(s,'aux_source_selected_'+str(i),bool(i)==remote)
        show(s,'aux_source_focus_'+str(i),s.selected_field==1 and bool(i)==remote)
        for name,size,lines in (('kind_title_',31,1),('kind_description_',25,2),('kind_meta_',26,1)):
            text(s,name+str(i),s[name+str(i)].getText(),size,lines)
    text(s,'aux_side_caption',localized_upper(_('Source information')),38)
    show(s,'aux_side_icon_folder-outline',not remote);show(s,'aux_side_icon_link',remote)
    text(s,'aux_side_title',_('Web address') if remote else _('Local / NAS'),39,2)
    text(s,'aux_side_help',_('Direct HTTP(S) M3U playlist address.') if remote else _('Select a playlist from the receiver, USB or mounted network storage.'),30,4)
    text(s,'aux_type_caption',_('Supported format'),28);text(s,'aux_type_value','M3U / M3U8',36)
    text(s,'aux_save_hint',_('Press GREEN to save the source.'),26,2)


def _history(s):
    total=len(s._entries)
    for i in range(7):
        pos=s._top+i;entry=s._entries[pos] if pos<total else None
        value=entry.get('title','') if isinstance(entry,dict) else entry or ''
        text(s,'title_'+str(i),value,34)
        show(s,'aux_history_row_'+str(i),entry is not None);show(s,'aux_history_focus_'+str(i),entry is not None and pos==s._selected)
    selected=s._entries[s._selected] if total else ''
    video=isinstance(selected,dict)
    text(s,'aux_side_caption',localized_upper(_('Selected video') if video else _('Selected search')),38)
    text(s,'aux_history_value',selected.get('title','') if video else selected or '—',45,3)
    text(s,'aux_history_help',_('Press OK to play the selected video.') if video else _('Press OK to search again.'),30,3)
    if total:update_scrollbar(s,'aux_history_scroll',total,s._selected,7,box(s,(1200,210,7,596)))
    else:hide_scrollbar(s,'aux_history_scroll')


def _youtube(s):
    rows=s._visible()
    text(s,'aux_title',_('Match highlights') if type(s).__name__=='GTMatchHighlightsScreen' else 'YouTube',46)
    for i,entry in enumerate(rows[:5]):
        text(s,'title_'+str(i),entry.get('title',''),31,2)
        text(s,'detail_'+str(i),s['detail_'+str(i)].getText(),24)
        text(s,'duration_'+str(i),s['duration_'+str(i)].getText(),25,2)
    response=s._pages.get(s._page) or {}
    entries=response.get('results') or []
    selected=entries[s._selected] if entries and 0<=s._selected<len(entries) else None
    playing=s._preview_owns_service()
    video=s._preview_records.get(s.current_item.stream_id,{'title':s.current_item.name}) if playing else selected
    if video:
        text(s,'preview_title',video.get('title',''),36,3)
        text(s,'preview_detail',s['preview_detail'].getText(),27,2)
    text(s,'preview_action',s['preview_action'].getText(),27)


def _online(s):
    from . import subtitle_browser as b
    entries=s._filtered();selected=entries[s.selected] if entries else None
    for field,caption in (('language','Language'),('provider','Provider'),('release','Release'),('compatibility','Compatibility'),('meta','FPS')):
        text(s,'aux_column_'+field,_(caption),24)
    for i in range(7):
        pos=s.top+i;entry=entries[pos] if pos<len(entries) else None
        show(s,'aux_subtitle_focus_'+str(i),entry is not None and pos==s.selected)
        if entry:
            text(s,'release_'+str(i),b.subtitle_result_release(entry,_('Subtitle')),26)
            text(s,'compatibility_'+str(i),s['compatibility_'+str(i)].getText(),23)
            text(s,'provider_'+str(i),b.provider_display_name(str(entry.get('provider') or 'subdl')),25)
            text(s,'meta_'+str(i),s['meta_'+str(i)].getText(),22,2)
    text(s,'aux_side_caption',localized_upper(_('Selected subtitle')),35)
    text(s,'aux_release_caption',_('Release'),26);text(s,'aux_fps_caption',_('Frame rate'),26)
    text(s,'aux_compat_caption',_('Compatibility'),26)
    text(s,'aux_subtitle_empty',s['message'].getText() or _('No content found.'),31,4)
    show(s,'aux_subtitle_empty',selected is None)
    status=str((selected or {}).get('compatibility_status') or 'unknown').lower()
    if status not in b.COMPATIBILITY_STATUSES:status='unknown'
    language=str((selected or {}).get('language') or '')
    text(s,'aux_selected_language',localized_language_name(language) if language else '—',34)
    text(s,'aux_selected_provider',b.provider_display_name(str(selected.get('provider') or 'subdl')) if selected else '',27)
    text(s,'aux_release_value',b.subtitle_result_release(selected,_('Subtitle')) if selected else '—',27,3)
    text(s,'aux_fps_value',s['meta_'+str(s.selected-s.top)].getText() if selected else '—',29)
    for state in b.COMPATIBILITY_STATUSES:
        visible=selected is not None and state==status
        show(s,'aux_status_art_'+state,visible);show(s,'aux_status_'+state,visible)
        text(s,'aux_status_'+state,_(b.COMPATIBILITY_LABELS[state]),27)
    text(s,'aux_subtitle_help',_('Press OK to download the selected subtitle.') if selected else _('Search for subtitles or change the filter.'),25,2)
    from .background import attach_native_alpha_pixmap
    path=b.subtitle_flag_path(language) if language else ''
    if path and path!=s._aux_flag_key:
        s._aux_flag_key=path
        attach_native_alpha_pixmap(s,'aux_selected_flag',path,key='aux_selected_flag',show_on_load=False)
    show(s,'aux_selected_flag',bool(path and selected))


def _local(s):
    entries=s.entries;selected=entries[s.selected] if entries else None
    picker=s._folder_picker
    text(s,'aux_title',_('Choose download folder') if picker else _('Choose a subtitle file'),46)
    for i in range(7):
        pos=s.top+i;entry=entries[pos] if pos<len(entries) else None
        folder=bool(entry and entry.get('kind')=='directory')
        show(s,'aux_local_focus_'+str(i),entry is not None and pos==s.selected)
        show(s,'aux_local_folder-outline_'+str(i),entry is not None and folder)
        show(s,'aux_local_document_'+str(i),entry is not None and not folder)
        if entry:text(s,'name_'+str(i),_(entry['label']) if entry.get('label') else entry.get('name',''),32)
    folder=bool(selected and selected.get('kind')=='directory')
    show(s,'aux_local_selected_folder-outline',folder);show(s,'aux_local_selected_document',selected is not None and not folder)
    text(s,'aux_side_caption',localized_upper(_('Selected folder') if picker else _('Selected subtitle')),36)
    text(s,'aux_local_name',(_(selected['label']) if selected.get('label') else selected.get('name','')) if selected else '—',36,3)
    text(s,'aux_local_help',_('Open this folder to browse its contents.') if folder else _('Press OK to load this subtitle.') if selected else _('Select a folder or subtitle file.'),28,3)
    text(s,'aux_folder_caption',_('Download folder'),27);text(s,'aux_folder_value',s._download_folder,30,2)
    text(s,'aux_language_caption',_('Subtitle language'),27)
    text(s,'aux_language_value',localized_language_name(s._language) if s._language else _('Automatic detection'),30)
    text(s,'aux_local_action',_('Press GREEN to use this folder.') if picker else _('Subtitle files: SRT / ASS / SSA / SUB / VTT'),24)
    show(s,'aux_local_empty',selected is None);text(s,'aux_local_empty',s['message'].getText() or _('No files found.'),31,4)


def _categories(s):
    entries=s._entries if not s._loading else []
    start=s.selected_index//s.page_size*s.page_size
    selected=entries[s.selected_index] if entries else None
    for i in range(8):
        entry=entries[start+i] if start+i<len(entries) else None
        show(s,'aux_category_focus_'+str(i),entry is not None and start+i==s.selected_index)
        show(s,'aux_category_line_'+str(i),entry is not None)
        if entry:
            text(s,'category_row_'+str(i),'{:02d}.  {}'.format(start+i+1,entry.name),33)
            text(s,'category_state_'+str(i),_('Hidden') if s._entry_id(entry) in s._hidden else '',24)
    show(s,'items',selected is None)
    text(s,'aux_category_position','{} / {}'.format(s.selected_index+1,len(entries)) if selected else '',29)
    text(s,'aux_side_caption',localized_upper(_('Selected category')),37)
    text(s,'aux_category_name',selected.name if selected else '—',36,3)
    text(s,'aux_category_type',_({'live':'Live TV','movie':'Movies','series':'Series'}.get(s.content_type,'Live TV')),29)
    hidden=selected is not None and s._entry_id(selected) in s._hidden
    text(s,'aux_category_visibility',_('Hidden') if hidden else _('Visible') if selected else '—',31)
    text(s,'aux_category_help',_('Press OK to show or hide the category. Mark categories with RED to move them together.'),28,3)
    text(s,'manager_move_help',s._move_guidance() if s._move_mode else _('Changes are applied when you press GREEN.'),26,5)
    if selected:update_scrollbar(s,'list_scroll',len(entries),s.selected_index,s.page_size,s._list_scroll_geometry)
    else:hide_scrollbar(s,'list_scroll')


def _channels(s):
    entries=s._entries if not s._loading and not s._error else []
    total=len(entries);start=s.selected_index//s.page_size*s.page_size
    text(s,'aux_query',s._query or _('Enter a channel name.'),33)
    text(s,'aux_source_type',_('M3U CHANNEL SEARCH'),26)
    text(s,'aux_channel_count',_('{} channels found').format(total) if total else '',27)
    for i in range(10):
        entry=entries[start+i] if i<s.page_size and start+i<total else None
        text(s,'aux_channel_number_'+str(i),'{:04d}'.format(start+i+1) if entry else '',28)
        text(s,'aux_channel_name_'+str(i),entry.name+('  ·  '+entry.group_title if getattr(entry,'group_title','') else '') if entry else '',32)
        show(s,'aux_channel_focus_'+str(i),entry is not None and start+i==s.selected_index)
        show(s,'aux_channel_line_'+str(i),entry is not None)
        show(s,'aux_channel_favorite_'+str(i),entry is not None and s._is_favorite(entry))
    empty=not total
    for name in ('aux_empty_icon','aux_empty_title','aux_empty_help'):show(s,name,empty)
    title=_('Loading content...') if s._loading else s._error or _(s.empty_message())
    text(s,'aux_empty_title',title,46,2)
    text(s,'aux_empty_help',_('Please wait') if s._loading else _('Press BLUE to enter another channel name.'),31,2)
    show(s,'items',False)
    if empty:
        text(s,'message','');hide_scrollbar(s,'list_scroll')
    else:update_scrollbar(s,'list_scroll',total,s.selected_index,s.page_size,s._list_scroll_geometry)


def _weather(s):
    entries=s.entries if not s._loading else [];total=len(entries)
    start=s.selected_index//7*7;selected=entries[s.selected_index] if total else None
    text(s,'aux_results_caption',localized_upper(_('Search results')),38)
    text(s,'aux_weather_position','{} / {}'.format(s.selected_index+1,total) if total else '',28)
    show(s,'message',not total)
    for i in range(7):
        entry=entries[start+i] if start+i<total else None
        text(s,'result_'+str(i),entry.name if entry else '',31)
        detail=' · '.join(dict.fromkeys(v for v in (entry.admin2,entry.admin1,entry.country) if v)) if entry else ''
        text(s,'aux_weather_detail_'+str(i),detail,24)
        show(s,'aux_weather_focus_'+str(i),entry is not None and start+i==s.selected_index)
        show(s,'aux_weather_line_'+str(i),entry is not None)
    text(s,'aux_side_caption',localized_upper(_('Selected location')),37)
    text(s,'aux_city_name',selected.name if selected else '—',50)
    for i,(caption,value) in enumerate((('District',selected.admin2 if selected else ''),('Province',selected.admin1 if selected else ''),('Country',selected.country if selected else ''))):
        text(s,'aux_city_caption_'+str(i),_(caption),26)
        text(s,'aux_city_value_'+str(i),value or '—',30)
    text(s,'aux_city_help',_('Press OK to select this location.'),24)
    if total:update_scrollbar(s,'location_scroll',total,s.selected_index,7,box(s,(1236,324,5,556)))
    else:hide_scrollbar(s,'location_scroll')


def _about(s):
    from . import PLUGIN_VERSION
    text(s,'aux_title',s['header'].getText(),46)
    text(s,'aux_side_caption',localized_upper(_('Application information')),36)
    for i,(caption,value) in enumerate((('Application','GT IPTV Player Pro'),('Version',PLUGIN_VERSION),('Developer','VicTuS59'),('Platform','Enigma2'),('Source code','GPL-2.0-or-later'))):
        text(s,'aux_about_caption_'+str(i),_(caption),26)
        text(s,'aux_about_value_'+str(i),value,32)

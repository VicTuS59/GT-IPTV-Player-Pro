# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Discovery-style provider VOD layout and GUI-only category navigation."""
from xml.etree import ElementTree as ET
import sys
from .ui_update import set_visible
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from .i18n import _, device_language, localized_upper
from .paths import plugin_path
from .remote_footer import footer_item, remote_action_text
from .remote_footer import _layout_get, _layout_keep, _skin_context
from .scrollbar import hide_scrollbar, update_scrollbar
from .typography import current_text_size, ellipsize_dynamic_text, fit_dynamic_text, font_px, set_text_if_changed

POSTERS = 6
CATEGORY_ROWS = 8
CANVAS = '#030910'


def footer(kind):
    return (footer_item('ok', 'play' if kind == 'movie' else 'open'),
            footer_item('red', 'Favorites'), footer_item('green', 'search'),
            footer_item('yellow', 'Categories'), footer_item('blue', 'Sorting'),
            footer_item('menu', 'Menu'), footer_item('exit', 'back'))


def footer_captions(kind):
    captions=[remote_action_text(item[1]) for item in footer(kind)]
    sort=_('Sort')
    captions[4]=_('Sorting') if sort=='Sort' and not device_language().lower().startswith('en') else sort
    return tuple(captions)


def footer_geometry(width,height,px,captions,keys=None):
    from .discovery_layout import _units
    keys=tuple(keys or (item[0] for item in footer('movie')))
    if len(keys)!=len(captions):raise ValueError('Footer keys and captions must match')
    widths={'ok':72,'exit':84,'menu':94,'ok_long':150,'up_down':80,'left_right':80}
    key_widths=[px(widths.get(key,28)) for key in keys]
    count=len(keys)
    gap,inner=px(38 if count<=7 else 24),px(18 if count<=7 else 14)
    available=width-px(128)-sum(key_widths)-inner*count-gap*(count-1)
    weights=[_units(text) for text in captions]
    total=sum(weights);used=0;cursor=px(64);result=[]
    for index,key_width in enumerate(key_widths):
        allocation=round(available*sum(weights[:index+1])/total)-used
        used+=allocation
        result.append({'key':(cursor,height-px(62),key_width,px(42)),
                       'caption':(cursor+key_width+inner,height-px(64),max(1,allocation),px(46))})
        cursor+=key_width+inner+allocation+gap
    return result


def style_footer(markup,kind,px,items=None,captions=None):
    """Restyle only this screen's standard components, leaving shared guides intact."""
    items=tuple(items or footer(kind));captions=tuple(captions or footer_captions(kind))
    # Key every design-unit value used below, rather than a new px lambda's
    # identity. Thus custom OSD scaling is preserved without per-open misses.
    scale=tuple(px(n) for n in (1,14,18,22,24,26,28,32,38,42,46,62,64,72,80,84,94,128,150))
    cache_key=(style_footer,_skin_context(),markup,kind,items,captions,scale)
    try:
        hash(cache_key);cacheable=True
    except TypeError:
        cacheable=False
    cached=_layout_get(cache_key) if cacheable else None
    if cached is not None:return cached
    root=ET.fromstring(markup)
    width,height=map(int,root.get('size').split(','))
    keys=tuple(item[0] for item in items)
    groups=footer_geometry(width,height,px,captions,keys)
    outline=plugin_path('skin','images','discovery-r48','key-outline-r48.png')
    colors={'red':'#FF334B','green':'#00CC78','yellow':'#FFD239','blue':'#008EFF'}
    for widget in root:
        name=widget.get('name','')
        if name.startswith(('remote_key_bg_','remote_key_','remote_action_')):
            index=int(name.rsplit('_',1)[1])
            if index>=len(groups):continue
            key=keys[index]
            if name.startswith('remote_action_'):
                rect=groups[index]['caption'];widget.set('font','Regular;{}'.format(font_px(px,26 if len(items)<=7 else 24)))
                widget.set('foregroundColor','#D8E1EF');widget.set('noWrap','1')
            else:
                rect=groups[index]['key']
                if name.startswith('remote_key_bg_'):
                    if key not in colors:widget.set('pixmap',outline)
                    else:
                        rect=(0,0,1,1);widget.attrib.pop('pixmap',None)
                else:
                    widget.set('font','Regular;{}'.format(font_px(px,32 if key in colors else 22 if key=='ok_long' else 24)))
                    widget.set('foregroundColor',colors.get(key,'#EDF2F8'))
            widget.set('position','{},{}'.format(*rect[:2]));widget.set('size','{},{}'.format(*rect[2:]))
        elif name in ('remote_footer_stage','remote_footer_bg') or name.startswith('remote_divider_'):
            widget.set('position','0,0');widget.set('size','1,1')
            widget.attrib.pop('pixmap',None);widget.attrib.pop('backgroundColor',None)
            widget.set('transparent','1')
    result=ET.tostring(root,encoding='unicode')
    cost=(sys.getsizeof(markup)+sys.getsizeof(result)+sys.getsizeof(cache_key)
          +sum(sys.getsizeof(value) for value in captions)+sys.getsizeof(scale))
    return _layout_keep(cache_key,result,cost) if cacheable else result


def refresh_plain_footer(screen,items,px,captions,keys=None,sizes=None,minimums=None):
    key_text={'ok':'OK','exit':'EXIT','menu':'MENU','up_down':'↑↓','left_right':'◀▶','ok_long':_('Hold OK')}
    if keys:key_text.update(keys)
    profile=current_text_size()
    caption_font=font_px(px,26 if len(items)<=7 else 24,value=profile)
    key_font=font_px(px,24,value=profile)
    caption_min=px(16)
    key_min=px(14)
    for index,(item,text) in enumerate(zip(items,captions)):
        fit_dynamic_text(screen['remote_action_'+str(index)],text,max_lines=1,
                         preferred_size=caption_font,min_size=caption_min)
        key=item[0];colored=key in ('red','green','yellow','blue')
        widget=screen['remote_key_'+str(index)]
        if colored:
            set_text_if_changed(widget,'●');set_visible(screen['remote_key_bg_'+str(index)], False)
        else:
            size=(sizes or {}).get(key,22 if key=='ok_long' else 24)
            preferred=key_font if size==24 else font_px(px,size,value=profile)
            fit_dynamic_text(widget,key_text.get(key,key),max_lines=1,
                             preferred_size=preferred,min_size=(minimums or {}).get(key,key_min))
    for name in ('remote_footer_stage','remote_footer_bg'):set_visible(screen[name], False)
    for index in range(len(items)-1):set_visible(screen['remote_divider_'+str(index)], False)



def poster_geometry(width, height, px):
    left, right, gap = px(64), width-px(64), px(28)
    available = right-left-gap*(POSTERS-1)
    tiles = []
    for i in range(POSTERS):
        x = left+available*i//POSTERS+gap*i
        end = left+available*(i+1)//POSTERS+gap*i
        w = end-x
        h = min(px(366), (w*3)//2)
        tiles.append({'card':(x,px(582),w,h+px(48)),
                      'image':(x,px(582),w,h),
                      'title':(x-px(5),px(582)+h+px(8),w+px(10),px(36))})
    return {'tiles':tiles}


def scroll_geometry(width, height, px):
    return ((px(618),px(300),px(6),px(597)),
            (px(64),height-px(89),width-px(128),px(3)))


def skin(kind, width, height, px, favorite_star):
    parts=['<screen name="GTContentBrowserScreen" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="{}">'.format(width,height,CANVAS)]
    names=set()
    def label(name,rect,font=26,color='#EDF2F8',bg=None,z=5,align='left',lines=False):
        names.add(name)
        x,y,w,h=rect
        parts.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" {} zPosition="{}" halign="{}" valign="center" {}/>'.format(
            name,x,y,w,h,px(font),color,
            'backgroundColor="{}" transparent="0"'.format(bg) if bg else 'transparent="1"',z,align,'' if lines else 'noWrap="1"'))
    def box(name,x,y,w,h,**kw):label(name,tuple(px(v) if v else 0 for v in (x,y,w,h)),**kw)
    def pix(name,x,y,w,h,path='',z=1):
        names.add(name)
        parts.append('<widget name="{}" position="{},{}" size="{},{}" {} alphatest="blend" scale="1" zPosition="{}"/>'.format(
            name,px(x) if x else 0,px(y) if y else 0,px(w),px(h),
            'pixmap="{}"'.format(path.replace('&','&amp;').replace('"','&quot;')) if path else '',z))
    label(kind+'_video_guard',(0,0,width,height),font=1,bg=CANVAS,z=-30)
    pix(kind+'_backdrop',500,0,1420,650,z=-20)
    mask=lambda name:plugin_path('skin','images','discovery-r48',name)
    pix(kind+'_backdrop_scrim',0,0,1920,650,mask('hero-left-r48.png'),-19)
    pix(kind+'_detail_shadow',0,0,1920,210,mask('hero-top-r48.png'),-18)
    pix(kind+'_people_shadow',0,430,1920,220,mask('hero-bottom-r48.png'),-17)
    box('header',64,24,430,56,font=32)
    box('vod_header_divider',507,32,1,36,font=1,bg='#566A7A')
    box('vod_page_title',540,24,900,56,font=30)
    for i in range(5):
        box('vod_tab_'+str(i),64+i*345,100,320,42,font=27,color='#C2CCD8')
        box('vod_tab_focus_'+str(i),64+i*345,149,100,3,font=1,bg='#31CFE5')
    box('vod_category_name',68,185,1000,34,font=23,color='#9DAEBF')
    box(kind+'_title',64,231,1000,100,font=68,lines=True)
    box(kind+'_rating',68,350,175,42,font=31,color='#F4CA77')
    box(kind+'_meta',263,350,725,42,font=27,color='#C2CCD8')
    if kind=='movie':box(kind+'_genre',68,402,940,34,font=25,color='#D7DFE8')
    box(kind+'_people',68,444,940,32,font=23,color='#94A5B9')
    box(kind+'_plot',68,490,940,66,font=26,lines=True,color='#BAC7D6')
    box(kind+'_rail_caption',68,557,1400,24,font=18,color='#91A5BC')
    box('vod_counter',1540,545,316,34,font=23,color='#9EADBF',align='right')
    for i,tile in enumerate(poster_geometry(width,height,px)['tiles']):
        x,y,w,h=tile['image']
        label(kind+'_tile_focus_'+str(i),(x-px(3),y-px(3),w+px(6),h+px(6)),font=1,bg='#31CFE5',z=12)
        label(kind+'_tile_inner_'+str(i),(x,y,w,h),font=1,bg=CANVAS,z=13)
        label(kind+'_tile_bg_'+str(i),(x,y,w,h),font=1,bg=CANVAS,z=11)
        label(kind+'_tile_placeholder_'+str(i),(x,y,w,h),font=25,color='#52667C',bg=CANVAS,z=14,align='center')
        parts.append('<widget name="{}_tile_poster_{}" position="{},{}" size="{},{}" alphatest="blend" scale="1" zPosition="15"/>'.format(kind,i,x,y,w,h));names.add(kind+'_tile_poster_'+str(i))
        label(kind+'_tile_title_'+str(i),tile['title'],font=23,z=16,align='center')
        parts.append('<widget name="{}_tile_favorite_{}" position="{},{}" size="{},{}" pixmap="{}" alphatest="blend" scale="1" zPosition="17"/>'.format(kind,i,x+w-px(40),y+px(8),px(30),px(30),favorite_star));names.add(kind+'_tile_favorite_'+str(i))
    label('footer',(0,height-px(75),width,px(75)),font=18,z=30)
    label('vod_footer_line',(px(32),height-px(77),width-px(64),1),font=1,bg='#2A3B4C',z=30)
    drawer_art=lambda name:plugin_path('skin','images','drawer-r104',name+'.png')
    box(kind+'_category_panel',20,162,620,824,font=1,z=80)
    box(kind+'_category_accent',0,0,1,1,font=1,z=-40)
    pix('vod_drawer_surface',20,162,620,824,drawer_art('panel'),80)
    box(kind+'_category_caption',52,178,546,62,font=48,color='#20D5ED',z=84)
    box('vod_drawer_subtitle',54,244,546,34,font=26,color='#AFC1D8',z=84)
    box('vod_drawer_divider',50,280,548,1,font=1,bg='#315774',z=84)
    for i in range(CATEGORY_ROWS):
        y=300+i*75
        box(kind+'_category_focus_'+str(i),0,0,1,1,font=1,z=-40)
        pix('vod_drawer_focus_'+str(i),38,y,570,72,drawer_art('focus'),82)
        box(kind+'_category_marker_'+str(i),38,y+3,5,66,font=1,bg='#20D5ED',z=84)
        box('vod_drawer_number_'+str(i),60,y+10,62,50,font=28,color='#AFC1D8',z=85)
        box('vod_drawer_rule_'+str(i),136,y+13,1,46,font=1,bg='#4D6A86',z=84)
        box(kind+'_category_'+str(i),154,y+2,438,68,font=32,z=85,lines=True)
        box('vod_drawer_row_line_'+str(i),52,y+73,548,1,font=1,bg='#1D3B53',z=81)
    box('vod_drawer_status',54,420,542,144,font=30,color='#AFC1D8',z=85,lines=True)
    box('vod_drawer_count',464,906,136,32,font=24,color='#AFC1D8',z=85,align='right')
    box('vod_drawer_help_line',50,943,548,1,font=1,bg='#315774',z=84)
    for i,x in enumerate((52,228,404)):
        box('vod_drawer_help_'+str(i),x,949,178,30,font=23,color='#C2CCD8',z=85)
    box('vod_drawer_hint',0,0,1,1,font=1,z=-40)
    for name,geometry,z,color in (('list_scroll_track',scroll_geometry(width,height,px)[0],83,'#26344E'),
                                  ('list_scroll_thumb',scroll_geometry(width,height,px)[0],84,'#31CFE5'),
                                  ('poster_scroll_track',scroll_geometry(width,height,px)[1],18,'#26344E'),
                                  ('poster_scroll_thumb',scroll_geometry(width,height,px)[1],19,'#31CFE5')):
        label(name,geometry,font=1,bg=color,z=z)
    # Existing controller-only components remain bound but have no visible box.
    for name in ('app_bg','top_accent','screen_label','content_panel','list_accent','breadcrumb','items','message',
                 kind+'_server',kind+'_hero_panel',kind+'_rail_shadow',kind+'_people_panel',kind+'_meta_divider',
                 kind+'_poster_frame',kind+'_poster_placeholder',kind+'_poster',kind+'_detail_panel',
                 kind+'_detail_accent',kind+'_plot_caption') + ((kind+'_grid_panel',kind+'_grid_accent',kind+'_grid_position') if kind=='series' else ()):
        if name not in names:label(name,(0,0,1,1),font=1,z=-40)
    parts.append('</screen>')
    return '\n'.join(parts)


def place_trailer_hint(markup, px):
    root=ET.fromstring(markup)
    width=int(root.get('size').split(',')[0])
    x,y,w,h=width-px(324),px(26),px(260),px(52)
    for widget in root:
        if widget.get('name','').endswith('_trailer_hint_bg'):
            widget.set('position','{},{}'.format(x,y))
            widget.set('size','{},{}'.format(w,h))
        elif widget.get('name','').endswith('_trailer_hint_text'):
            widget.set('position','{},{}'.format(x+px(58),y+px(4)))
            widget.set('size','{},{}'.format(w-px(70),h-px(8)))
            widget.set('font','Regular;{}'.format(px(22)))
    return ET.tostring(root,encoding='unicode')


class DiscoveryVODMethods:
    def _setup_discovery_widgets(self):
        from .browser_common import _scale
        self.skin=style_footer(self.skin,self.content_type,_scale()[2])
        self.onLayoutFinish.append(self._refresh_discovery_footer)
        self.onLayoutFinish.append(self._render_discovery_navigation)
        self.onShown.append(self._refresh_discovery_footer)
        self._refresh_discovery_footer()
        self._discovery_drawer_open=False
        self._discovery_tabs_focused=False
        self._discovery_tab_cursor=0
        self._discovery_category_cursor=0
        self['header'].setText('GT IPTV PLAYER')
        for name in ('vod_header_divider','vod_page_title','vod_category_name','vod_counter','vod_footer_line',
                     'vod_drawer_divider','vod_drawer_hint','vod_drawer_count','vod_drawer_subtitle','vod_drawer_status','vod_drawer_help_line'):
            self[name]=Label('')
        self['vod_drawer_surface']=Pixmap()
        for i in range(CATEGORY_ROWS):
            self['vod_drawer_focus_'+str(i)]=Pixmap()
            for prefix in ('vod_drawer_number_','vod_drawer_rule_','vod_drawer_row_line_'):self[prefix+str(i)]=Label('')
        for i in range(3):self['vod_drawer_help_'+str(i)]=Label('')
        self['vod_page_title'].setText(localized_upper(_('Movies' if self.content_type=='movie' else 'Series')))
        self['vod_drawer_hint'].setText('↑↓  {}    OK  {}    EXIT  {}'.format(_('Navigate'),_('Select'),_('Back')))
        for i in range(5):
            self['vod_tab_'+str(i)]=Label('')
            self['vod_tab_focus_'+str(i)]=Label('')
        kind=self.content_type
        setattr(self,'_'+kind+'_focus','posters' if kind=='movie' else 'series')
        setattr(self,'_'+kind+'_title_preferred_font',font_px(_scale()[2],68,role='title'))
        self['actions']=ActionMap(['OkCancelActions','DirectionActions','ColorActions','MoviePlayerActions','MenuActions'],{
            'ok':self._discovery_ok,'cancel':self._discovery_cancel,
            'up':lambda:self._discovery_move('up'),'down':lambda:self._discovery_move('down'),
            'left':lambda:self._discovery_move('left'),'right':lambda:self._discovery_move('right'),
            'upRepeated':lambda:self._discovery_move('up'),'downRepeated':lambda:self._discovery_move('down'),
            'leftRepeated':lambda:self._discovery_move('left'),'rightRepeated':lambda:self._discovery_move('right'),
            'channelUp':lambda:self._discovery_page(-1),'channelDown':lambda:self._discovery_page(1),
            'yellow':self._discovery_toggle_categories,'green':self._discovery_search,
            'blue':self._discovery_sort,'red':self._discovery_favorite,'menu':self._discovery_menu},-1)
        self._render_discovery_navigation()

    def _refresh_discovery_footer(self):
        from .browser_common import _scale
        refresh_plain_footer(self,footer(self.content_type),_scale()[2],footer_captions(self.content_type))

    def _discovery_set_focus(self, categories=False):
        setattr(self,'_'+self.content_type+'_focus','categories' if categories else 'posters' if self.content_type=='movie' else 'series')

    def _discovery_render_posters(self):
        getattr(self,'_render_movie_posters' if self.content_type=='movie' else '_render_series_tiles')()

    def _render_discovery_navigation(self):
        if not getattr(self,'_vod_discovery_layout',False):return
        kind=self.content_type
        entries=getattr(self,'_entries',())
        opened=getattr(self,'_discovery_drawer_open',False)
        cursor=max(0,min(getattr(self,'_discovery_category_cursor',0),len(entries)-1)) if entries else 0
        self._discovery_category_cursor=cursor
        active=max(0,min(getattr(self,'selected_index',0),len(entries)-1)) if entries else 0
        content=getattr(self,'_'+kind+'_entries',())
        position=getattr(self,'_'+kind+'_selected_index',0)
        base=self._vod_window_base(kind)
        set_text_if_changed(self['vod_counter'],'{} / {}{}'.format(base+position+1 if content else 0,base+len(content),'+' if getattr(self,'_'+kind+'_has_more',False) else ''))
        start=active//4*4
        tabs=tuple((entry.category_id, entry.name) for entry in entries[start:start+4])
        signature=(id(entries),len(entries),active,tabs,opened,
            cursor if opened else None,getattr(self,'_discovery_tab_cursor',0),
            getattr(self,'_discovery_tabs_focused',False),
            getattr(self,'_error',''),getattr(self,'_loading',False),
            tuple(entry.name for entry in entries[cursor//self.page_size*self.page_size:cursor//self.page_size*self.page_size+self.page_size]) if opened else ())
        if signature==getattr(self,'_discovery_navigation_signature',None):return
        self._discovery_navigation_signature=signature
        self._discovery_tab_indices=tuple(range(start,min(start+4,len(entries))))
        for i in range(5):
            index=self._discovery_tab_indices[i] if i<len(self._discovery_tab_indices) else None
            text=entries[index].name if i<4 and index is not None else _('Categories') if i==4 else ''
            ellipsize_dynamic_text(self['vod_tab_'+str(i)],text,fallback_chars=24)
            selected=(self._discovery_tab_cursor==i) if getattr(self,'_discovery_tabs_focused',False) else (index==active if index is not None and i<4 else opened and i==4)
            self['vod_tab_focus_'+str(i)].show() if selected else self['vod_tab_focus_'+str(i)].hide()
        set_text_if_changed(self['vod_category_name'],localized_upper(entries[active].name) if entries else '')
        if getattr(self,'_error',''):
            self[kind+'_rail_caption'].setText(_(self._error))
        elif getattr(self,'_loading',False):
            self[kind+'_rail_caption'].setText(_('Loading...'))
        elif not entries:
            self[kind+'_rail_caption'].setText(_('No category found.'))
        if not opened:
            for row in range(self.page_size):
                for name in (kind+'_category_'+str(row),'vod_drawer_number_'+str(row),
                    'vod_drawer_rule_'+str(row),'vod_drawer_row_line_'+str(row),
                    'vod_drawer_focus_'+str(row),kind+'_category_focus_'+str(row),
                    kind+'_category_marker_'+str(row)):
                    self[name].hide()
            for name in (kind+'_category_panel',kind+'_category_accent',kind+'_category_caption',
                'vod_drawer_divider','vod_drawer_hint','vod_drawer_count','vod_drawer_surface',
                'vod_drawer_subtitle','vod_drawer_help_line','vod_drawer_status')+tuple('vod_drawer_help_'+str(i) for i in range(3)):
                self[name].hide()
            hide_scrollbar(self,'list_scroll')
            return
        page_start=cursor//self.page_size*self.page_size
        from .browser_common import _scale
        px=_scale()[2]
        for row in range(self.page_size):
            index=page_start+row
            name=kind+'_category_'+str(row)
            fit_dynamic_text(self[name],entries[index].name if index<len(entries) else '',max_lines=2,
                             preferred_size=font_px(px,32),min_size=font_px(px,25,value='standard'),fallback_chars=62)
            visible=opened and index<len(entries)
            self[name].show() if visible else self[name].hide()
            self['vod_drawer_number_'+str(row)].setText('{:02d}'.format(index+1) if index<len(entries) else '')
            for prefix in ('vod_drawer_number_','vod_drawer_rule_','vod_drawer_row_line_'):
                self[prefix+str(row)].show() if visible else self[prefix+str(row)].hide()
            self['vod_drawer_focus_'+str(row)].show() if visible and index==cursor else self['vod_drawer_focus_'+str(row)].hide()
            for prefix in ('_category_focus_','_category_marker_'):
                widget=self[kind+prefix+str(row)]
                widget.show() if opened and index==cursor and index<len(entries) else widget.hide()
        for name in (kind+'_category_panel',kind+'_category_accent',kind+'_category_caption',
                     'vod_drawer_divider','vod_drawer_hint','vod_drawer_count','vod_drawer_surface','vod_drawer_subtitle','vod_drawer_help_line')+tuple('vod_drawer_help_'+str(i) for i in range(3)):
            self[name].show() if opened else self[name].hide()
        self[kind+'_category_caption'].setText(localized_upper(_('Categories')))
        self['vod_drawer_subtitle'].setText('{} · {}: {}'.format(_('Movies' if kind=='movie' else 'Series'),_('Categories'),len(entries)))
        for i,value in enumerate(('▲▼ '+_('Select'),'OK '+_('Select'),'EXIT '+_('Close'))):
            fit_dynamic_text(self['vod_drawer_help_'+str(i)],value,max_lines=1,preferred_size=font_px(px,23),min_size=px(17))
        self['vod_drawer_count'].setText('{} / {}'.format(cursor+1 if entries else 0,len(entries)))
        state=_(self._error) if getattr(self,'_error','') else _('Loading...') if getattr(self,'_loading',False) else _('No category found.') if not entries else ''
        self['vod_drawer_status'].setText(state)
        self['vod_drawer_status'].show() if opened and state else self['vod_drawer_status'].hide()
        if opened and entries:update_scrollbar(self,'list_scroll',len(entries),cursor,self.page_size,self._list_scroll_geometry)
        else:hide_scrollbar(self,'list_scroll')

    def _refresh_discovery_categories(self):
        self._render_discovery_navigation()
        if self._loading or self._error or not self._entries:return
        loaded=getattr(self,'_'+self.content_type+'_loaded_category_id',None)
        if not any(str(entry.category_id)==str(loaded) for entry in self._entries):
            self._discovery_choose_category(max(0,min(self.selected_index,len(self._entries)-1)))

    def _discovery_choose_category(self,index):
        if self._closed or self._loading or not 0<=index<len(self._entries):return
        kind=self.content_type
        self.selected_index=index
        self._discovery_drawer_open=self._discovery_tabs_focused=False
        self._discovery_set_focus()
        self._render_discovery_navigation()
        category=self._entries[index]
        if str(getattr(self,'_'+kind+'_loaded_category_id',None))!=str(category.category_id):
            getattr(self,'_start_'+kind+'_load')(category)
        else:
            self._discovery_render_posters()

    def _discovery_toggle_categories(self):
        if self._closed:return
        self._discovery_drawer_open=not self._discovery_drawer_open
        self._discovery_tabs_focused=False
        if self._discovery_drawer_open:self._discovery_category_cursor=self.selected_index
        self._discovery_set_focus(self._discovery_drawer_open)
        self._discovery_render_posters()
        self._render_discovery_navigation()

    def _discovery_move(self,direction):
        if self._closed:return
        if self._discovery_drawer_open:
            amount={'up':-1,'down':1,'left':-self.page_size,'right':self.page_size}[direction]
            self._discovery_category_cursor=max(0,min(self._discovery_category_cursor+amount,len(self._entries)-1))
            self._render_discovery_navigation();return
        if self._discovery_tabs_focused:
            if direction=='down':
                self._discovery_tabs_focused=False;self._discovery_set_focus();self._discovery_render_posters()
            elif direction in ('left','right'):
                available=list(range(len(self._discovery_tab_indices)))+[4]
                current=available.index(self._discovery_tab_cursor) if self._discovery_tab_cursor in available else 0
                self._discovery_tab_cursor=available[(current+(1 if direction=='right' else -1))%len(available)]
            self._render_discovery_navigation();return
        if direction=='up':
            self._discovery_tabs_focused=True
            self._discovery_tab_cursor=self.selected_index%4 if self._discovery_tab_indices else 4
            self._discovery_set_focus(True);self._discovery_render_posters();self._render_discovery_navigation();return
        if direction in ('left','right'):
            kind=self.content_type
            if not getattr(self,'_'+kind+'_entries',()):return
            index=getattr(self,'_'+kind+'_selected_index')+(1 if direction=='right' else -1)
            if index<0 and not self._vod_window_base(kind):return
            getattr(self,'_select_'+kind)(index)
            self._render_discovery_navigation()

    def _discovery_page(self,direction):
        if self._discovery_drawer_open:
            self._discovery_move('left' if direction<0 else 'right');return
        kind=self.content_type
        if not getattr(self,'_'+kind+'_entries',()):return
        self._discovery_tabs_focused=False;self._discovery_set_focus()
        index=getattr(self,'_'+kind+'_selected_index')+direction*self._poster_page_size
        if direction<0:index=max(-self._vod_window_base(kind),index)
        elif not getattr(self,'_'+kind+'_has_more',False):index=min(index,len(getattr(self,'_'+kind+'_entries'))-1)
        getattr(self,'_select_'+kind)(index)

    def _discovery_ok(self):
        if self._discovery_drawer_open:self._discovery_choose_category(self._discovery_category_cursor)
        elif self._discovery_tabs_focused:
            if self._discovery_tab_cursor==4:self._discovery_toggle_categories()
            elif self._discovery_tab_cursor<len(self._discovery_tab_indices):self._discovery_choose_category(self._discovery_tab_indices[self._discovery_tab_cursor])
        else:self.open_selected()

    def _discovery_cancel(self):
        if self._discovery_drawer_open:self._discovery_toggle_categories()
        elif self._discovery_tabs_focused:
            self._discovery_tabs_focused=False;self._discovery_set_focus();self._discovery_render_posters();self._render_discovery_navigation()
        else:self.close()

    def _discovery_search(self):
        if self._discovery_drawer_open:return
        getattr(self,'open_movie_search' if self.content_type=='movie' else 'open_series_search')()

    def _discovery_sort(self):
        if self._discovery_drawer_open:return
        from .sorting_ui import open_sort
        open_sort(self)

    def _discovery_favorite(self):
        if not self._discovery_drawer_open:self.toggle_selected_favorite()

    def _discovery_menu(self):
        if self._discovery_drawer_open:
            self._discovery_toggle_categories();self.open_content_edit();return
        from Screens.ChoiceBox import ChoiceBox
        options=[(_('Download'),'download'),(_('Refresh'),'refresh'),(_('Edit categories'),'edit')]
        if self.content_type=='series':options.append((_('Continue watching'),'continue'))
        def chosen(choice):
            if not choice:return
            action=choice[1]
            if action=='download':self.download_selected()
            elif action=='edit':self.open_content_edit()
            elif action=='continue':self.open_series_continue()
            else:getattr(self,'refresh_movies' if self.content_type=='movie' else 'refresh_series')()
        self.session.openWithCallback(chosen,ChoiceBox,title=_('Menu'),list=options)

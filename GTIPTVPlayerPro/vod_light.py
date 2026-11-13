# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native VOD lists with artwork for the settled selection."""
import time
from types import SimpleNamespace

from .i18n import _, localized_upper
from .content import ContentError
from .typography import (clean_dynamic_text, ellipsize_dynamic_text,
    fit_dynamic_text, font_px, set_text_if_changed)
from .provider_preview import catalogue_info
from .metadata import media_metadata_ready
from .scrollbar import update_scrollbar, hide_scrollbar
from .vod_artwork import (backdrop_urls, cached_backdrop, cached_poster, cached_preview_backdrop,
                          download_backdrop, load_poster, load_preview_backdrop, poster_urls,
                          provider_backdrop_loader)

CATEGORY_ROWS = 11
CONTENT_ROWS = 10
CANVAS = '#0B0E13'
PANEL = '#10151D'
HERO_BG = '#0D131C'
INK = '#DDE4EE'
MUTED = '#9BA9BE'
LINE = '#222834'
FRAME = '#292F3B'
THEMES = {
    'movie': ('#FFC482', '#2C2118', '#BE8A54'),
    'series': ('#81E4F2', '#112A32', '#4C9EAB'),
}
HERO_BOX = (927, 139, 932, 503)
KEY_RELEASE_TIMEOUT_MS = 650
KEY_CONTEXT = 'GTIPTVLightListActions'
_keys_loaded = False


def ensure_keys():
    global _keys_loaded
    if not _keys_loaded:
        from keymapparser import readKeymap
        from .paths import plugin_path
        readKeymap(plugin_path('light_vod_keys.xml'))
        _keys_loaded=True


def geometry(width, height):
    x = lambda v: int(round(v * width / 1920.0))
    y = lambda v: int(round(v * height / 1080.0))
    rect = lambda a,b,c,d: (x(a),y(b),max(1,x(c)),max(1,y(d)))
    poster=rect(926,672,179,269)
    category_row_height,content_row_height=y(64),y(67)
    category_scroll=list(rect(409,213,3,704));category_scroll[3]=category_row_height*CATEGORY_ROWS
    content_scroll=list(rect(894,229,3,670));content_scroll[3]=content_row_height*CONTENT_ROWS
    return dict(rect=rect, category_scroll=tuple(category_scroll),
        content_scroll=tuple(content_scroll), poster=poster, hero=rect(*HERO_BOX),
        category_row_height=category_row_height,content_row_height=content_row_height,
        font=lambda v:max(8,int(round(v*min(width/1920.0,height/1080.0)))))


def footer(kind):
    return (('OK', _('Play') if kind=='movie' else _('Open'), INK),
        ('●',_('Favorites'),'#FF454F'),('●',_('Search'),'#00C990'),
        ('●',_('Edit categories'),'#FFD53A'),('●',_('Sorting'),'#19ADFF'),
        ('MENU',_('Menu'),INK),('EXIT',_('Back'),INK),
        ('↑ ↓',_('Navigate'),INK),
        ('← →',_('Page'),INK))


def footer_layout():
    """FHD key/caption boxes; skin and font fitting share these bounds."""
    return {
        7:((60,1016,58,32),(130,1008,120,48)),
        8:((256,1016,68,32),(336,1008,110,48)),
        0:((458,1016,50,32),(520,1008,130,48)),
        1:((654,1016,24,32),(685,1008,153,48)),
        2:((841,1016,24,32),(872,1008,130,48)),
        3:((1005,1016,24,32),(1036,1008,227,48)),
        4:((1266,1016,24,32),(1297,1008,128,48)),
        5:((1427,1016,77,32),(1516,1008,130,48)),
        6:((1664,1016,66,32),(1742,1008,118,48)),
    }


def skin(kind, width, height, fill=None, border=None):
    g=geometry(width,height);rect=g['rect'];font=g['font']
    accent, default_fill, default_border = THEMES[kind]
    fill, border = fill or default_fill, border or default_border
    parts=['<screen name="GTContentBrowserScreen" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="{}">'.format(width,height,CANVAS)]
    def label(name,box,size=30,color=INK,bg=CANVAS,z=3,align='left',lines=False,inset=False,valign='center',transparent=False):
        a,b,c,d=rect(*box)
        if inset:
            padding=max(1,min(width,height)//540)
            a+=padding;b+=padding;c-=2*padding;d-=2*padding
        parts.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" backgroundColor="{}" transparent="{}" zPosition="{}" valign="{}" halign="{}" noWrap="{}" />'.format(
            name,a,b,c,d,font_px(font,size,role='title' if name.endswith('_title') else 'body'),color,bg,int(transparent),z,valign,align,0 if lines else 1))
    def frame(name,box,bg=PANEL,edge=FRAME,z=0):
        a,b,c,d=rect(*box)
        thickness=max(1,min(width,height)//1080)
        for suffix,x,y,w,h,color in (('_frame',a,b,c,d,edge),
                ('',a+thickness,b+thickness,c-2*thickness,d-2*thickness,bg)):
            parts.append('<widget name="{}{}" position="{},{}" size="{},{}" font="Regular;1" backgroundColor="{}" transparent="0" zPosition="{}" />'.format(name,suffix,x,y,max(1,w),max(1,h),color,z if suffix else z+1))
    # Permanent opaque native surfaces remain behind letterboxed/alpha posters.
    label('light_video_guard',(0,0,1920,1080),1,bg=CANVAS,z=-5)
    frame('light_category_panel',(60,138,360,830))
    frame('light_hero_panel',(926,138,934,505),bg=HERO_BG)
    label('light_logo',(60,28,52,55),43,'#69E0E5')
    label('light_brand',(116,28,211,55),23)
    label('light_header_separator',(345,28,1,55),1,bg=FRAME)
    for tab,x in (('movie',377),('series',553)):
        label('light_tab_'+tab,(x,25,160,56),28,INK if tab==kind else MUTED,
            bg='#1C222C' if tab==kind else CANVAS,align='center')
    frame('light_search_frame',(1640,26,220,54),bg=CANVAS)
    label('light_search',(1656,30,188,46),23,MUTED,align='center')
    label('light_top_line',(0,106,1920,1),1,bg=FRAME)
    label('light_category_heading',(91,153,245,39),20,MUTED,PANEL)
    label('light_category_total',(338,153,57,39),20,MUTED,PANEL,align='right')
    label('light_category_count',(91,926,300,32),20,MUTED,PANEL)
    label('light_subtitle',(448,150,360,27),19,MUTED)
    for name,box,bg in (('light_category_list',(75,213,329,704),PANEL),
                        ('light_content_list',(448,229,439,670),CANVAS)):
        a,b,c,d=rect(*box)
        d=(g['category_row_height']*CATEGORY_ROWS if name=='light_category_list'
           else g['content_row_height']*CONTENT_ROWS)
        parts.append('<widget name="{}" position="{},{}" size="{},{}" backgroundColor="{}" foregroundColor="{}" selectionBackgroundColor="{}" selectionForegroundColor="{}" scrollbarMode="showNever" transparent="0" zPosition="3" />'.format(name,a,b,c,d,bg,INK,fill,INK))
    label(kind+'_rail_caption',(448,178,325,42),37)
    label('light_total',(781,181,116,39),22,MUTED,align='right')
    label('light_position',(448,922,439,36),21,MUTED)
    for prefix,box in (('list_scroll',g['category_scroll']),('poster_scroll',g['content_scroll'])):
        a,b,c,d=box
        for suffix,color in (('track',LINE),('thumb',MUTED)):
            parts.append('<widget name="{}_{}" position="{},{}" size="{},{}" font="Regular;1" backgroundColor="{}" transparent="0" zPosition="5" />'.format(prefix,suffix,a,b,c,d,color))
    a,b,c,d=g['hero']
    parts.append('<widget name="light_hero" position="{},{}" size="{},{}" scale="1" alphatest="off" transparent="0" zPosition="3" />'.format(a,b,c,d))
    label('light_hero_caption_back',(927,451,932,191),1,bg=HERO_BG,z=4)
    label('light_selected_heading',(967,168,500,34),22,accent,HERO_BG,z=5,transparent=True)
    label(kind+'_title',(966,433,854,137),74,'#FFFFFF',HERO_BG,z=5,lines=True,valign='bottom',transparent=True)
    label(kind+'_meta',(967,580,850,43),25,INK,HERO_BG,z=5,transparent=True)
    a,b,c,d=g['poster']
    parts.append('<widget name="{}_poster_backing" position="{},{}" size="{},{}" font="Regular;1" backgroundColor="{}" transparent="0" zPosition="2" />'.format(kind,a,b,c,d,PANEL))
    parts.append('<widget name="{}_poster_placeholder" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" backgroundColor="{}" transparent="0" zPosition="3" valign="center" halign="center" />'.format(kind,a,b,c,d,font(24),MUTED,PANEL))
    parts.append('<widget name="{}_poster" position="{},{}" size="{},{}" scale="1" alphatest="blend" transparent="1" zPosition="4" />'.format(kind,a,b,c,d))
    label(kind+'_rating',(1139,675,186,34),24,accent)
    label(kind+'_plot',(1139,721,721,132),26,lines=True,valign='top')
    label('light_primary',(1139,872,225,51),25,CANVAS,accent,align='center')
    frame('light_info_key_frame',(1659,675,65,34),bg=CANVAS,edge=FRAME,z=3)
    label('light_info_key',(1660,676,63,32),18,align='center',z=5)
    label('light_info_hint',(1735,671,125,42),19,MUTED,lines=True)
    frame(kind+'_trailer_hint_bg',(1393,883,75,31),bg=CANVAS,edge=FRAME,z=2)
    label('light_play_key',(1394,884,73,29),17,align='center',z=4)
    label(kind+'_trailer_hint_text',(1480,876,380,45),22,MUTED)
    label('message',(448,963,1412,31),21,MUTED)
    label('light_footer_divider',(60,1000,1800,1),1,bg=FRAME)
    # Fixed native geometry; no footer PNG decode or per-key layout work.
    for i,(key,caption,color) in enumerate(footer(kind)):
        key_box,caption_box=footer_layout()[i]
        if key!='●':frame('light_footer_key_frame_'+str(i),key_box,bg=CANVAS,edge=FRAME,z=3)
        label('light_footer_key_'+str(i),key_box,20 if key!='●' else 23,color,z=5,align='center',inset=key!='●')
        label('light_footer_caption_'+str(i),caption_box,21,lines=True)
    # Shared async infrastructure owns these invisible components.
    for name in ('top_accent','header','screen_label','content_panel','list_accent',
                 'breadcrumb','items','footer',kind+'_server',kind+'_genre',kind+'_people'):
        label(name,(0,0,1,1),1,bg=CANVAS,z=-20)
    parts.append('<widget name="app_bg" position="0,0" size="1,1" zPosition="-30" />')
    return '\n'.join(parts)+'</screen>'


def configure(screen):
    from .browser_common import _scale
    from .channel_highlight import channel_highlight_colors, selection_border_colors
    width,height,unused=_scale();g=geometry(width,height)
    fill,border=getattr(screen,'_light_palette',('navy','navy'))
    accent, default_fill, default_border = THEMES[screen.content_type]
    screen._light_accent=accent
    screen._light_selection_fill=default_fill if fill=='navy' else channel_highlight_colors(fill)[0]
    screen._light_selection_border=default_border if border=='navy' else selection_border_colors(border)[1]
    screen.skin=skin(screen.content_type,width,height,screen._light_selection_fill,screen._light_selection_border)
    screen.page_size=CATEGORY_ROWS
    screen._poster_page_size=CONTENT_ROWS
    screen._poster_slot_count=0
    screen._list_scroll_geometry=g['category_scroll']
    screen._poster_scroll_geometry=g['content_scroll']
    screen._screen_background_path=''
    screen._opaque_background=True
    screen._footer_layout_stacked=False
    screen._footer_show_web_hint=False
    screen._light_geometry=g


class LightVODMethods:
    def _setup_light_widgets(self):
        from Components.Label import Label
        from Components.Pixmap import Pixmap
        from Components.ActionMap import ActionMap
        from .native_vod_list import NativeVODList
        from xml.etree import ElementTree as ET
        kind=self.content_type
        root=ET.fromstring(self.skin)
        for node in root.findall('widget'):
            name=node.get('name')
            if name not in self:
                if name in ('light_category_list','light_content_list'):
                    category=name=='light_category_list'
                    width=int(node.get('size').split(',')[0])
                    row_height=self._light_geometry['category_row_height' if category else 'content_row_height']
                    self[name]=NativeVODList(width,row_height,font_px(self._light_geometry['font'],28),
                        INK,PANEL if category else CANVAS,self._light_selection_fill,
                        None if category else self._light_native_title,
                        border=self._light_selection_border,separator=LINE,accent=self._light_accent)
                else:self[name]=Pixmap() if name in (kind+'_poster','light_hero','app_bg') else Label('')
        for name,text in (('light_logo','GT'),('light_brand','IPTV PLAYER PRO'),
            ('light_tab_movie',_('Movies')),('light_tab_series',_('Series')),('light_search',_('Search')),
            ('light_subtitle',localized_upper(_('Movie library') if kind=='movie' else _('Series library'))),
            ('light_selected_heading',localized_upper(_('Selected movie') if kind=='movie' else _('Selected series'))),
            ('light_category_heading',localized_upper(_('Categories'))),
            ('light_primary','OK  '+(_('Play') if kind=='movie' else _('Open'))),
            ('light_info_key','INFO'),('light_info_hint',_('Full summary')),('light_play_key','PLAY'),
            (kind+'_trailer_hint_text',_('Trailer')),
            (kind+'_poster_placeholder',_('Movies') if kind=='movie' else _('Series'))):
            self[name].setText(text)
        self._light_fit_static()
        self[kind+'_poster'].hide()
        self['light_hero'].hide()
        self['light_primary'].hide()
        self._light_rows_signature=self._light_categories_signature=None
        self._light_list_sync=False
        self._light_favorite_revision=0
        self._light_favorite_signature=None
        self['light_category_list'].onSelectionChanged.append(self._light_native_category_changed)
        self['light_content_list'].onSelectionChanged.append(self._light_native_content_changed)
        self._setup_vod_runtime()
        self['actions']=ActionMap(['OkCancelActions','DirectionActions','ColorActions',
            'MoviePlayerActions','MenuActions','InfobarEPGActions'],{
            'ok':self._light_ok,'cancel':self._light_back,
            'up':lambda:self._light_key_move(-1),'down':lambda:self._light_key_move(1),
            'upRepeated':lambda:self._light_key_move(-1,True),'downRepeated':lambda:self._light_key_move(1,True),
            'left':self._light_page_previous,'right':self._light_page_next,
            'leftRepeated':lambda:self._light_page(-1,True),'rightRepeated':lambda:self._light_page(1,True),
            'channelUp':self._light_ignore_page_key,
            'channelDown':self._light_ignore_page_key,
            'red':self._light_toggle_favorite,'green':self._light_search,
            'yellow':self.open_category_manager,'blue':self._light_sort,
            'menu':self._light_menu,'showEventInfo':self._light_info},-1)
        ensure_keys()
        self['light_keys']=ActionMap([KEY_CONTEXT],{
            'pagePrevious':self._light_page_previous,
            'pageNext':self._light_page_next,
            'pagePreviousRepeat':lambda:self._light_page(-1,True),
            'pageNextRepeat':lambda:self._light_page(1,True),
            'ignorePageKey':self._light_ignore_page_key,
            'showSynopsis':self._light_info,
            'listUp':lambda:self._light_key_move(-1),
            'listDown':lambda:self._light_key_move(1),
            'listUpRepeat':lambda:self._light_key_move(-1,True),
            'listDownRepeat':lambda:self._light_key_move(1,True),
            'navigationReleased':self._light_release},-2)
        self._light_render_rows()
        self._light_render_categories(schedule_load=False)
        self._light_waiting()

    def _setup_vod_runtime(self):
        from enigma import eTimer
        from .browser_common import _connect_timer
        from .source_loader import SourceLoader

        self._light_cover_identity=None
        self._light_poster_url=''
        self._light_cover_token=None
        self._light_cover_request=None
        self._light_cover_job=None
        self._light_visual_identity=None
        self._light_hero_request=None
        self._light_hero_after_cache=None
        self._light_hero_token=None
        self._light_hero_ready=False
        self._light_hero_decoding=False
        self._light_hero_url=''
        self._light_navigation_held=False
        self._light_navigation_repeated=False
        self._light_visible=False
        self._light_plot_text=''
        self._light_repeat_direction=0
        self._light_repeat_count=0
        self._light_repeat_time=0
        self._light_detail_identity=None
        self._light_cover_loader=SourceLoader(eTimer(),_connect_timer,self._light_cover_done)
        self._light_hero_loader=SourceLoader(eTimer(),_connect_timer,self._light_hero_done)
        self._light_list_delivery=SourceLoader(eTimer(),_connect_timer,self._light_list_ready)
        self._light_list_delivery_job=None
        self._light_cover_start_timer=eTimer()
        _connect_timer(self._light_cover_start_timer,self._light_start_cover)
        self.onShown.append(self._light_shown)
        self.onLayoutFinish.append(self._light_layout_ready)
        self.onHide.append(self._light_hidden)
        self.onClose.append(self._light_close)
        self.onClose.append(self._stop_movie if self.content_type=='movie' else self._stop_series)

    def _setup_movie_widgets(self):self._setup_light_widgets()
    def _setup_series_widgets(self):self._setup_light_widgets()

    def _light_fit_static(self):
        font=self._light_geometry['font']
        for i,(key,caption,color) in enumerate(footer(self.content_type)):
            self['light_footer_key_'+str(i)].setText(key)
            fit_dynamic_text(self['light_footer_caption_'+str(i)],caption,max_lines=2,
                preferred_size=font_px(font,21),min_size=font(16),fallback_chars=24)
        for name,size,minimum,lines in (('light_subtitle',19,16,1),
                ('light_tab_movie',28,20,1),('light_tab_series',28,20,1),
                ('light_primary',25,18,1),('light_search',23,17,1),
                ('light_selected_heading',22,18,1),('light_category_heading',20,16,1),
                ('light_info_hint',20,15,2)):
            widget=self[name]
            fit_dynamic_text(widget,widget.getText(),max_lines=lines,
                preferred_size=font_px(font,size),min_size=font(minimum),fallback_chars=24)

    def _light_shown(self):
        self._light_visible=True
        self['light_video_guard'].show()
        backing=self.content_type+'_poster_backing'
        if backing in self:self[backing].show()
        entries=self._light_field('entries',())
        if entries and not self._light_field('loading',False):
            self._light_detail_identity=None
            self._light_schedule_detail(immediate=True)
            if getattr(entries,'progressive',False) and not entries.finalized:
                self._light_field('timer').start(120,True)

    def _light_cancel_cover(self):
        from .browser_common import _cancel_worker_token
        if self._light_cover_request is None:return
        _cancel_worker_token(getattr(self,'_light_cover_token',None))
        self._light_cover_token=None
        self._light_cover_request=None
        self._light_cover_job=None
        timer=getattr(self,'_light_cover_start_timer',None)
        if timer is not None:timer.stop()
        loader=getattr(self,'_light_cover_loader',None)
        if loader is not None:loader.cancel()

    def _light_cancel_hero(self):
        from .browser_common import _cancel_worker_token
        self._light_hero_request=None
        self._light_hero_after_cache=None
        self._light_hero_decoding=False
        if self._light_hero_token is not None:
            _cancel_worker_token(self._light_hero_token)
            self._light_hero_token=None
            self._light_hero_loader.cancel()

    def _light_cancel_art(self):
        self._light_cancel_cover()
        self._light_cancel_hero()
        loaders=getattr(self,'_gt_pixmap_loaders',{})
        for key in ('light-hero',self.content_type+'-detail'):
            decoder=loaders.pop(key,None)
            if decoder is not None:decoder.close()

    def _light_reset_art(self,identity=None,*,keep_hero_job=False):
        if keep_hero_job:
            self._light_cancel_cover()
        else:
            self._light_cancel_art()
        self._light_visual_identity=identity
        self._light_cover_identity=None
        self._light_poster_url=''
        self._light_hero_ready=False
        self._light_hero_url=''
        self._light_show('light_hero',False)
        self._light_show('light_hero_caption_back',True)
        self._light_show(self.content_type+'_poster',False)
        self._light_show(self.content_type+'_poster_placeholder',True)

    def _light_hidden(self):
        self._light_visible=False
        self._light_cancel_art()
        self._light_field('detail_timer').stop()
        self._light_release()

    def _light_close(self):
        self._light_hidden()
        self._light_cover_loader.close()
        self._light_hero_loader.close()
        self._light_list_delivery.close()
        self._light_list_delivery_job=None

    def _light_begin_list(self,kind,generation):
        self._light_list_delivery_job=(kind,generation,self._light_list_delivery.expect())

    def _light_publish_list(self,kind,generation):
        # Worker-side publication only; native widgets stay on the GUI loop.
        job=self._light_list_delivery_job
        if job is not None and job[:2]==(kind,generation):
            self._light_list_delivery.publish(job[2],(kind,generation))

    def _light_list_ready(self,value,error):
        if self._closed or error is not None or not value:return
        kind,generation=value
        if generation!=getattr(self,'_'+kind+'_generation',None):return
        if not getattr(self,'_'+kind+'_loading',False):return
        getattr(self,'_'+kind+'_timer').stop()
        getattr(self,'_poll_'+kind+'_result')()

    def _light_layout_ready(self):
        from .remote_footer import bind_layout_text
        bind_layout_text(self)
        self._light_rows_signature=self._light_categories_signature=None
        self._light_caption_text=None
        self._light_render_rows()
        self._light_render_categories(schedule_load=False)
        plot=self._light_plot_text
        self._light_plot_text=''
        self._light_set_plot(plot)
        self._light_fit_static()

    def _light_field(self,name,default=None):
        return getattr(self,'_'+self.content_type+'_'+name,default)

    def _light_content_focus(self):
        return self._light_field('focus')==('posters' if self.content_type=='movie' else 'series')

    def _light_discard_deferred_step(self):
        prefix='_'+self.content_type+'_'
        setattr(self,prefix+'pending_advance',False)
        setattr(self,prefix+'pending_selection_index',None)
        if self.content_type=='series':self._series_pending_clamp_to_end=False

    def _light_release(self, *, arm=True):
        from .browser_common import NAVIGATION_DEBOUNCE_MS
        held=self._light_navigation_held
        repeated=self._light_navigation_repeated
        if repeated:self._light_discard_deferred_step()
        self._light_navigation_held=False
        self._light_navigation_repeated=False
        self._light_repeat_direction=0
        self._light_repeat_count=0
        if (arm and held and self._light_visible
                and self._light_field('pending_detail_generation')==self._light_field('generation')):
            self._light_field('detail_timer').start(NAVIGATION_DEBOUNCE_MS,True)
            if repeated:self._light_preview_cached_hero()

    def _light_note_navigation(self,repeated):
        self._light_navigation_held=True
        if repeated and not self._light_navigation_repeated:
            self._light_navigation_repeated=True
            self._light_discard_deferred_step()
        if self._light_field('pending_detail_generation')==self._light_field('generation'):
            self._light_field('detail_timer').start(KEY_RELEASE_TIMEOUT_MS,True)

    def _light_key_move(self,step,repeated=False):
        self._light_note_navigation(repeated)
        now=time.monotonic()
        if not repeated or step!=self._light_repeat_direction or now-self._light_repeat_time>.8:
            self._light_repeat_count=0
        elif self._light_content_focus():
            self._light_repeat_count+=1
        self._light_repeat_direction=step
        self._light_repeat_time=now
        speed=10 if self._light_repeat_count>=15 else 3 if self._light_repeat_count>=6 else 1
        self._light_move(step*speed,repeated=repeated)

    def _light_labels(self,start,stop):
        entries=self._light_field('entries',())
        reader=getattr(entries,'labels',None)
        if callable(reader):
            source_type=str(getattr(self.client,'source_type',''))
            return [SimpleNamespace(stream_id=identity,name=name,content_type=self.content_type,
                source_type=source_type) for identity,name in reader(start,stop)]
        return entries[start:stop]

    def _light_select(self,index):
        kind=self.content_type;entries=self._light_field('entries',())
        if self._closed or self._light_field('loading',False) or not entries:return
        prefix='_'+kind+'_'
        focused=self._light_content_focus()
        setattr(self,prefix+'focus','posters' if kind=='movie' else 'series')
        index=int(index)
        full_category=getattr(entries,'full_category',False)
        if not full_category:
            if index<0 and self._vod_window_base(kind):
                self._request_vod_window(kind,index)
                return
            if index>=len(entries) and self._light_field('has_more',False):
                setattr(self,prefix+'pending_advance',True)
                setattr(self,prefix+'pending_selection_index',index)
                getattr(self,'_start_'+kind+'_more_load')()
                return
            self._light_discard_deferred_step()
        if getattr(entries,'progressive',False):
            from .progressive_vod import defer_selection
            if defer_selection(self,kind,index):return
            self._light_discard_deferred_step()
            catalog=self._light_field('browser_catalog')
            if entries.pending or (catalog is not None and catalog.state.get('preparing')):
                index=max(0,min(index,len(entries)-1))
            else:index%=len(entries)
        else:index%=len(entries)
        if index!=self._light_field('selected_index',0):
            setattr(self,prefix+'selected_index',index)
            if not full_category:self._trim_vod_window(kind)
            setattr(self,prefix+'generation',self._light_field('generation')+1)
            if kind=='series':
                self._series_info=None;self._series_info_stream_id=''
                self._remember_series_selection()
            self._light_schedule_detail()
        self._light_render_rows()
        if not focused:self._light_render_categories(schedule_load=False)
        if not full_category:
            getattr(self,'_maybe_load_more_movies' if kind=='movie' else '_maybe_load_more_series')()

    def _select_movie(self,index):self._light_select(index)
    def _select_series(self,index):self._light_select(index)

    def _remember_series_selection(self):
        if not self._series_entries:return
        index=max(0,min(self._series_selected_index,len(self._series_entries)-1))
        item=self._light_labels(index,index+1)[0]
        self._series_selection_by_category[str(self._series_loaded_category_id or '')]=(index+self._vod_window_base('series'),str(item.stream_id))

    def _light_move(self,step,page=False,repeated=False):
        if self._closed or self._loading:return
        if self._light_content_focus():
            entries=self._light_field('entries',())
            if not entries:return
            if getattr(entries,'progressive',False):entries.restore_identity=None
            delta=CONTENT_ROWS if page else 1
            target=self._light_field('selected_index',0)+step*delta
            catalog=self._light_field('browser_catalog')
            complete=not self._light_field('has_more',False) and not getattr(entries,'pending',False) and not (catalog is not None and catalog.state.get('preparing'))
            if repeated and not complete:target=min(target,len(entries)-1)
            if complete and not self._vod_window_base(self.content_type):
                target=max(0,min(target,len(entries)-1)) if page else target%len(entries)
            getattr(self,'_select_'+self.content_type)(target)
        elif self._entries:
            delta=CATEGORY_ROWS if page else 1
            getattr(self,'_select_'+self.content_type+'_category')(self.selected_index+step*delta)

    def _light_page_previous(self):
        self._light_page(-1)

    def _light_page_next(self):
        self._light_page(1)

    def _light_page(self,step,repeated=False):
        self._light_note_navigation(repeated)
        self._light_move(step,page=True,repeated=repeated)

    def _light_ignore_page_key(self):
        # Consume image-specific channel/page aliases only on these screens.
        return 1

    def _light_left(self):
        if not self._light_content_focus():return
        setattr(self,'_'+self.content_type+'_pending_advance',False)
        setattr(self,'_'+self.content_type+'_pending_selection_index',None)
        setattr(self,'_'+self.content_type+'_focus','categories')
        self._light_render_categories(schedule_load=False)
        self._light_render_rows()

    def _light_right(self):
        if self._light_content_focus():return
        if not self._entries or self._loading:return
        category=self._entries[self.selected_index]
        if (self._light_field('loaded_category_id') is None
                or str(self._light_field('loaded_category_id') or '')!=str(category.category_id or '')):return
        setattr(self,'_'+self.content_type+'_focus','posters' if self.content_type=='movie' else 'series')
        self._light_render_categories(schedule_load=False)
        self._light_render_rows()
        if self._light_field('entries',()) and not self._light_field('loading',False):
            getattr(self,'_schedule_'+self.content_type+'_detail')()

    def _light_ok(self):
        if self._light_content_focus():return self.open_selected()
        if self._closed or self._loading or not self._entries:return
        category=self._entries[self.selected_index]
        if (self._light_field('loaded_category_id') is None
                or str(self._light_field('loaded_category_id') or '')!=str(category.category_id or '')):
            getattr(self,'_start_'+self.content_type+'_load')(category)
        setattr(self,'_'+self.content_type+'_focus','posters' if self.content_type=='movie' else 'series')
        self._light_render_categories(schedule_load=False)
        self._light_render_rows()

    def _light_back(self):
        if self._light_content_focus():self._light_left()
        else:self.close()

    def _light_toggle_favorite(self):
        self._light_favorite_revision+=1
        self.toggle_selected_favorite()

    def _light_native_title(self,title,identity):
        if not self._favorite_keys:return title
        item=SimpleNamespace(stream_id=identity,content_type=self.content_type,
            source_type=str(getattr(self.client,'source_type','')))
        return ('★ ' if self._is_favorite(item) else '')+title

    def _light_native_category_changed(self):
        if self._light_list_sync or self._closed:return
        self.selected_index=self['light_category_list'].getSelectedIndex()
        self._light_render_categories(schedule_load=False)

    def _light_native_content_changed(self):
        if self._light_list_sync or self._closed:return
        self._light_select(self['light_content_list'].getSelectedIndex())

    def _light_assign_list(self,name,rows,index,focused,replace=False):
        widget=self[name]
        self._light_list_sync=True
        try:
            if replace:widget.publish_list(rows)
            if widget.getSelectedIndex()!=index:widget.moveToIndex(index)
            focus_key=(id(getattr(widget,'instance',None)),focused)
            if getattr(widget,'_light_focus_key',None)!=focus_key:
                widget.selectionEnabled(focused)
                widget._light_focus_key=focus_key
        finally:self._light_list_sync=False

    def _light_search(self):
        return getattr(self,'open_movie_search' if self.content_type=='movie' else 'open_series_search')()

    def _light_sort(self):
        from .sorting_ui import open_sort
        return open_sort(self)

    def _light_menu(self):
        from Screens.ChoiceBox import ChoiceBox
        options=[(_('Change layout'),self._choose_vod_layout),
            (_('Refresh'),self.refresh_movies if self.content_type=='movie' else self.refresh_series),
            (_('Download'),self.download_selected),(_('Edit categories'),self.open_category_manager),
            (_('Sorting'),self._light_sort),(_('Information'),self._light_info)]
        if self.content_type=='series':options.append((_('Continue Watching'),self.open_series_continue))
        def selected(value):
            if value and not self._closed:value[1]()
        self.session.openWithCallback(selected,ChoiceBox,title=_('Menu'),list=options)

    def _choose_vod_layout(self):
        from Screens.ChoiceBox import ChoiceBox

        options = ((_("List view"), "list"), (_("Cinematic view"), "cinematic"))
        choices = [("✓ " + label if value == self._vod_layout else label, value)
                   for label, value in options]
        self.session.openWithCallback(self._vod_layout_chosen, ChoiceBox,
                                      title=_("Change layout"), list=choices)

    def _vod_layout_chosen(self, choice):
        if self._closed or not choice or choice[1] == self._vod_layout:
            return
        from Screens.MessageBox import MessageBox
        from .settings import VOD_LAYOUTS, _NATIVE_CONFIG_ERRORS, load_player_settings, save_player_settings

        layout = choice[1]
        if layout not in VOD_LAYOUTS:
            return
        try:
            settings = load_player_settings()
            settings.vod_layout = layout
            saved = save_player_settings(settings)
        except _NATIVE_CONFIG_ERRORS:
            saved = False
        if not saved:
            self.session.open(MessageBox, _("Settings could not be saved. Check the receiver storage."),
                              type=MessageBox.TYPE_ERROR)
            return
        category_id = self._light_field("loaded_category_id")
        if category_id is None and self._entries:
            category_id = self._entries[self.selected_index].category_id
        self.close(("vod-layout", self.content_type, category_id))

    def _restore_vod_layout_category(self):
        category_id = getattr(self, "_vod_layout_category", None)
        if category_id is not None and self._entries and not self._loading:
            self._vod_layout_category = None
            for index, entry in enumerate(self._entries):
                if str(entry.category_id) == str(category_id):
                    self.selected_index = index
                    break

    def _light_info(self):
        entries = self._light_field('entries', ())
        if self._closed or not entries:
            return
        item = entries[self._light_field('selected_index', 0)]
        from .vod_light_info import LightSynopsisScreen
        identity = self._light_item_identity(item)
        hero_url = self._light_hero_url if identity == self._light_visual_identity else ''
        poster_url = self._light_poster_url if identity == self._light_cover_identity else ''
        self.session.open(
            LightSynopsisScreen, self.client, self._metadata_client, item,
            self._cached_grid_detail(self.content_type), hero_url=hero_url, poster_url=poster_url,
        )

    def _light_render_categories(self,schedule_load=True):
        self._restore_vod_layout_category()
        kind=self.content_type
        if self._entries:self.selected_index%=len(self._entries)
        cursor=self.selected_index
        focused=not self._light_content_focus()
        signature=id(self._entries)
        replace=signature!=self._light_categories_signature
        rows=[(item.name,str(item.category_id)) for item in self._entries] if replace else None
        self._light_categories_signature=signature
        self._light_assign_list('light_category_list',rows,cursor,focused,replace)
        self['light_category_list'].set_active_identity(self._light_field('loaded_category_id'))
        set_text_if_changed(self['light_footer_caption_0'],_('Open') if focused or kind=='series' else _('Play'))
        set_text_if_changed(self['light_category_total'],str(len(self._entries)))
        set_text_if_changed(self['light_category_count'],'{} / {}'.format(cursor+1 if self._entries else 0,len(self._entries)))
        update_scrollbar(self,'list_scroll',len(self._entries),cursor,CATEGORY_ROWS,self._list_scroll_geometry)
        if self._error:
            set_text_if_changed(self['message'],_(self._error))
        elif self._loading or not self._entries:
            set_text_if_changed(self['message'],_('Loading...') if self._loading else _('No category found.'))
        elif not self._light_field('category_load_started',False):
            set_text_if_changed(self['message'],_('Open')+' : OK')

    def _refresh_movie_categories(self,schedule_load=True):self._light_render_categories(schedule_load)
    def _refresh_series_categories(self):self._light_render_categories()
    def _clear_movie_categories(self):self._light_render_categories(schedule_load=False)
    def _clear_series_categories(self):self._light_render_categories(schedule_load=False)

    def _select_movie_category(self,index):self._light_select_category(index)
    def _select_series_category(self,index):self._light_select_category(index)

    def _light_select_category(self,index):
        if not self._entries or self._loading:return
        setattr(self,'_'+self.content_type+'_pending_advance',False)
        setattr(self,'_'+self.content_type+'_pending_selection_index',None)
        if self.content_type=='series':self._remember_series_selection()
        setattr(self,'_'+self.content_type+'_focus','categories')
        self.selected_index=index%len(self._entries)
        self._light_render_categories()
        self._light_render_rows()

    def _light_show(self,name,value):
        widget=self[name]
        if bool(getattr(widget,'visible',False))!=bool(value):
            widget.show() if value else widget.hide()

    def _light_row_color(self,widget,selected):
        instance=getattr(widget,'instance',None)
        if instance is not None:
            key=(id(instance),selected)
            if getattr(widget,'_light_selected',None)==key:return
            widget._light_selected=key
            try:
                from skin import parseColor
                instance.setBackgroundColor(parseColor(self._light_selection_fill if selected else PANEL))
            except (ImportError,AttributeError,RuntimeError,TypeError):pass

    def _light_render_rows(self):
        kind=self.content_type;entries=self._light_field('entries',())
        selected=max(0,min(self._light_field('selected_index',0),len(entries)-1)) if entries else 0
        focused=self._light_content_focus()
        signature=(id(entries),getattr(entries,'native_revision',0))
        replace=signature!=self._light_rows_signature
        rows=None
        if replace:
            rows=getattr(entries,'native_menu_rows',None)
            if rows is None:rows=[(item.name,str(item.stream_id)) for item in entries]
            self._light_rows_signature=signature
        self._light_assign_list('light_content_list',rows,selected,focused,replace)
        self._light_show('light_primary',focused and bool(entries) and not self._light_field('loading',False))
        favorite_signature=(self._favorite_keys,self._light_favorite_revision)
        if favorite_signature!=self._light_favorite_signature:
            self._light_favorite_signature=favorite_signature
            if not replace:self['light_content_list'].l.invalidate()
        setattr(self,'_'+kind+'_poster_page_key',(0,id(entries)))
        self._light_refresh_count()
        caption_key=(id(self._entries),self._light_field('loaded_category_id'))
        if (caption_key!=getattr(self,'_light_caption_key',None)
                and not self._light_field('loading',False) and not self._light_field('error','')):
            self._light_caption_key=caption_key
            self._light_update_caption()
        hint=getattr(self,'_'+kind+'_trailer_hint',None)
        if hint is not None:
            hint.refresh_hint()
            self._light_show(kind+'_trailer_hint_bg_frame',bool(getattr(self[kind+'_trailer_hint_bg'],'visible',False)))

    def _light_refresh_count(self):
        entries=self._light_field('entries',());selected=self._light_field('selected_index',0)
        if self._light_field('loading',False) and not entries:
            set_text_if_changed(self['light_total'],'…')
            set_text_if_changed(self['light_position'],'')
            hide_scrollbar(self,'poster_scroll')
            return
        base=self._vod_window_base(self.content_type)
        catalog=self._light_field('browser_catalog')
        growing=bool(self._light_field('has_more',False) or (catalog is not None and catalog.state.get('preparing')) or getattr(entries,'pending',False)
                     or (getattr(entries,'progressive',False) and not entries.finalized))
        set_text_if_changed(self['light_total'],str(base+len(entries))+(' +' if growing else ''))
        set_text_if_changed(self['light_position'],'{} / {}{}'.format(base+selected+1 if entries else 0,base+len(entries),' +' if growing else ''))
        update_scrollbar(self,'poster_scroll',base+len(entries),base+selected,CONTENT_ROWS,self._poster_scroll_geometry)

    def _render_movie_posters(self):self._light_render_rows()
    def _render_series_tiles(self):self._light_render_rows()

    def _light_clear(self):
        self._light_rows_signature=None
        self._light_render_rows()
        self._light_reset_art()
        for field in ('title','rating','meta'):set_text_if_changed(self[self.content_type+'_'+field],'')

    def _clear_movie_posters(self):self._light_clear()
    def _clear_series_tiles(self):self._light_clear()

    def _light_waiting(self,item=None):
        kind=self.content_type
        if item is None:
            self._light_reset_art()
            for field in ('title','rating','meta'):set_text_if_changed(self[kind+'_'+field],'')
            self._light_set_plot(_('Loading...') if self._light_field('loading',False) else '')
            return
        info=self._cached_grid_detail(kind) or catalogue_info(self.client,kind,item)
        self._light_apply_detail(info)

    def _render_movie_waiting(self,item=None):self._light_waiting(item)
    def _render_series_waiting(self,item=None):self._light_waiting(item)

    def _light_set_plot(self,text):
        cleaned=clean_dynamic_text(text)
        if cleaned==self._light_plot_text:return
        self._light_plot_text=cleaned
        fit_dynamic_text(self[self.content_type+'_plot'],self._light_plot_text,max_lines=4,
            preferred_size=font_px(self._light_geometry['font'],26),
            min_size=self._light_geometry['font'](23),fallback_chars=280)

    def _set_movie_plot(self,value):self._light_set_plot(value)
    def _set_series_plot(self,value):self._light_set_plot(value)

    def _light_item_identity(self, item):
        return (self._light_field('loaded_category_id'), str(item.stream_id), self._light_field('generation'))

    def _light_apply_detail(self,info,poster_path='',metadata_status='',poster_url='',catalogue_poster_url='',
                            *, cache_only=False):
        from .browser_common import _rating_label,_attach_subtitle_detail_metadata
        kind=self.content_type;entries=self._light_field('entries',())
        if not entries:return
        item=entries[self._light_field('selected_index',0)]
        identity=self._light_item_identity(item)
        if identity!=self._light_visual_identity:
            request=self._light_hero_request
            self._light_reset_art(identity,keep_hero_job=request is not None and request[0]==identity)
        if not cache_only:
            self._light_detail_identity=identity
            _attach_subtitle_detail_metadata(item,info)
            if kind=='series':self._series_info,self._series_info_stream_id=info,str(item.stream_id)
        self._light_hero(item,info,cache_only=cache_only)
        fit_dynamic_text(self[kind+'_title'],getattr(info,'title','') or item.name,max_lines=2,
            preferred_size=font_px(self._light_geometry['font'],74,role='title'),min_size=self._light_geometry['font'](42),fallback_chars=46)
        duration=getattr(info,'duration','') if kind=='movie' else ''
        meta=' · '.join(str(value) for value in (getattr(info,'year','') or getattr(item,'year',''),
            duration,getattr(info,'genre','')) if value)
        fit_dynamic_text(self[kind+'_meta'],meta,max_lines=1,fallback_chars=64)
        set_text_if_changed(self[kind+'_rating'],_rating_label(getattr(info,'tmdb_rating','') or getattr(info,'rating','') or getattr(item,'rating','')))
        self._light_set_plot(getattr(info,'plot','') or (_('Loading...') if info is None else _('Movie summary unavailable.') if kind=='movie' else _('Series summary unavailable.')))
        if not cache_only:
            self._light_cover(item,poster_path,poster_url,info)

    def _apply_movie_detail(self,*args):self._light_apply_detail(*args)
    def _apply_series_detail(self,*args):self._light_apply_detail(*args)

    def _light_preview_cached_hero(self):
        entries = self._light_field('entries', ())
        if self._closed or not self._light_visible or not entries:
            return
        try:
            item = entries[self._light_field('selected_index', 0)]
            info = self._cached_grid_detail(self.content_type) or catalogue_info(self.client, self.content_type, item)
        except (ContentError, IndexError):
            return
        self._light_hero(item, info, cache_only=True)

    def _light_schedule_detail(self,immediate=False):
        from .browser_common import _cancel_worker_token,NAVIGATION_DEBOUNCE_MS
        kind=self.content_type;prefix='_'+kind+'_';entries=self._light_field('entries',())
        if self._closed or not entries:return
        index=self._light_field('selected_index',0)
        item=self._light_labels(index,index+1)[0]
        identity=(self._light_field('loaded_category_id'),str(item.stream_id),self._light_field('generation'))
        if identity==self._light_detail_identity:return
        _cancel_worker_token(self._light_field('detail_token'))
        setattr(self,prefix+'detail_token',None)
        setattr(self,prefix+'detail_result',None);setattr(self,prefix+'detail_preview',None)
        setattr(self,prefix+'detail_pending',False)
        timer=self._light_field('detail_timer');timer.stop()
        setattr(self,prefix+'pending_detail_generation',self._light_field('generation'))
        self._light_cancel_art()
        if immediate:getattr(self,'_commit_'+kind+'_detail')()
        else:
            timer.start(KEY_RELEASE_TIMEOUT_MS if self._light_navigation_held else NAVIGATION_DEBOUNCE_MS,True)
            if not self._light_navigation_repeated:self._light_preview_cached_hero()

    def _schedule_movie_detail(self,immediate=False):self._light_schedule_detail(immediate)
    def _schedule_series_detail(self,immediate=False):self._light_schedule_detail(immediate)

    def _light_commit_detail(self):
        kind=self.content_type;prefix='_'+kind+'_'
        if (self._closed or not self._light_visible
                or self._light_field('pending_detail_generation')!=self._light_field('generation')):return
        if self._light_navigation_held:self._light_release(arm=False)
        setattr(self,prefix+'pending_detail_generation',None)
        entries=self._light_field('entries',())
        if not entries:return
        item=entries[self._light_field('selected_index',0)]
        info=self._cached_grid_detail(kind) or catalogue_info(self.client,kind,item)
        self._light_apply_detail(info)
        if media_metadata_ready(info) and provider_backdrop_loader(self.client, item, info) is None:return
        getattr(self,'_start_'+kind+'_detail')()

    def _commit_movie_detail(self):self._light_commit_detail()
    def _commit_series_detail(self):self._light_commit_detail()

    def _light_cover(self,item,path,url,info=None):
        if not self._light_visible:return
        kind=self.content_type
        identity=self._light_item_identity(item)
        if identity!=self._light_cover_identity:
            self._light_cancel_cover();self._light_cover_identity=identity
            self._light_poster_url=''
            self[kind+'_poster'].hide();self[kind+'_poster_placeholder'].show()
        elif getattr(self[kind+'_poster'],'visible',False):
            return
        covers=poster_urls(item,info,url)
        hit=cached_poster(covers,width=getattr(self,'_poster_image_width',342)) if not path else None
        path=path or (hit[1] if hit else '')
        if path:
            self._light_cancel_cover()
            self._light_poster_url=hit[0] if hit else next(iter(covers),'')
            self._apply_detail_poster(kind,item,path)
            return
        if not covers and not getattr(self._metadata_client,'available',False):return
        hints=tuple(getattr(info,field,'') or getattr(item,source,'') for field,source in
                    (('title','name'),('year','year'),('tmdb_id','tmdb_id'),('imdb_id','imdb_id')))
        request=(identity,covers,hints)
        if self._light_cover_request==request:return
        self._light_cancel_cover()
        self._light_cover_request=request
        self._light_cover_job=(request,item,info)
        # Selection has already settled before detail/artwork is requested.
        self._light_cover_start_timer.start(0,True)

    def _light_start_cover(self):
        from .browser_common import _WorkerToken
        job,self._light_cover_job=self._light_cover_job,None
        if job is None or self._closed or not self._light_visible:return
        request,item,info=job
        if request!=self._light_cover_request:return
        kind=self.content_type
        token=self._light_cover_token=_WorkerToken(12)
        width=getattr(self,'_poster_image_width',342)
        def load():
            found=load_poster(self.client,self._metadata_client,kind,item,info,request[1],token,width=width)
            token.check()
            return request,found
        self._light_cover_loader.request(load)

    def _light_cover_done(self,result,error):
        if self._closed or not self._light_visible or error is not None or not result:return
        request,found=result
        if request!=self._light_cover_request:return
        entries=self._light_field('entries',())
        if not entries:return
        item=entries[self._light_field('selected_index',0)]
        if self._light_item_identity(item)!=request[0]:return
        if found:
            self._light_poster_url,path=found
            self._apply_detail_poster(self.content_type,item,path)

    def _light_hero(self,item,info,*,cache_only=False):
        identity=self._light_item_identity(item)
        if not self._light_visible or (self._light_visual_identity==identity and self._light_hero_ready):return
        from .browser_common import _WorkerToken,_cinematic_cover_ratio
        from .hero_artwork import is_composite,preparation_allowance,prepare_hero,vod_hero_plan
        cinema=getattr(self,'_vod_discovery_layout',False)
        urls=backdrop_urls(item,info)
        if not urls and (not cinema or cache_only or not getattr(self._metadata_client,'available',False)):
            return
        request=(identity,urls,cache_only)
        previous=self._light_hero_request
        if request==previous:return
        if (previous is not None and previous[:2]==request[:2]
                and (self._light_hero_token is not None or self._light_hero_decoding)):
            if not cache_only:self._light_hero_after_cache=(item,info)
            return
        self._light_cancel_hero()
        self._light_hero_request=request
        token=self._light_hero_token=_WorkerToken(8)
        plan=None if cinema else vod_hero_plan(*self._light_geometry['hero'][2:])

        def load():
            if cinema:
                found=(cached_preview_backdrop(urls,token) if cache_only else
                       load_preview_backdrop(self.client,self._metadata_client,self.content_type,item,info,urls,token))
            else:
                found=cached_backdrop(urls,token)
                if found is None and not cache_only:
                    found=download_backdrop(self.client,urls,token)
            if found is None:return None
            url,path=found
            if cinema:
                # The restored skin already provides native gradient layers.
                # Limit the decoded texture on UHD receivers.
                width,height=self._light_geometry['hero'][2:]
                size=(min(width,1420),min(height,650))
                return request,url,path,size,False,_cinematic_cover_ratio(path),info
            path=prepare_hero(path,plan,token,preparation_allowance())
            token.check()
            composited=is_composite(path,plan)
            return request,url,path,plan.size,composited,None if composited else _cinematic_cover_ratio(path),info

        self._light_hero_loader.request(load)

    def _light_hero_done(self,result,error):
        from .background import attach_pixmap
        from .hero_artwork import release_prepared
        self._light_hero_token=None
        if error is not None or result is None:
            self._light_resume_hero_after_cache(self._light_hero_request)
            return
        request,url,path,size,composited,ratio,info=result

        def loaded(success):
            if not self._light_hero_current(request):return
            self._light_hero_decoding=False
            self._light_hero_ready=success
            if success:
                self._light_hero_url=url
                self._light_hero_after_cache=None
            self._light_show('light_hero_caption_back',not (success and composited))
            self._light_show('light_hero',success)
            if not success:self._light_resume_hero_after_cache(request)

        try:
            if not self._light_hero_current(request):return
            if request[0]!=self._light_visual_identity:
                self._light_apply_detail(info,cache_only=True)
            self._light_hero_decoding=True
            attach_pixmap(self,'light_hero',path,key='light-hero',on_loaded=loaded,
                cover_ratio=ratio if size==self._light_geometry['hero'][2:] else None,
                cover_alignment='top_right',decode_size=size,show_on_load=False)
        except (AttributeError, ContentError, IndexError, OSError, RuntimeError, TypeError, ValueError):
            loaded(False)
        finally:
            release_prepared(path)

    def _light_hero_current(self,request):
        if (request is None or self._closed or not self._light_visible or request!=self._light_hero_request
                or request[0][0]!=self._light_field('loaded_category_id')
                or request[0][2]!=self._light_field('generation')):
            return False
        index=self._light_field('selected_index',0)
        try:
            selected=self._light_labels(index,index+1)
        except (ContentError, IndexError):
            return False
        return bool(selected and str(selected[0].stream_id)==request[0][1])

    def _light_resume_hero_after_cache(self,request):
        pending,self._light_hero_after_cache=self._light_hero_after_cache,None
        if pending is not None and self._light_hero_current(request):
            self._light_hero(*pending)

    # Only the settled selection requests artwork.
    def _start_deferred_movie_posters(self):pass
    def _start_deferred_series_posters(self):pass

    def _light_update_caption(self):
        identity=self._light_field('loaded_category_id')
        category=next((item for item in self._entries if str(item.category_id or '')==str(identity or '')),None) if identity is not None else None
        title=category.name if category is not None else _('Movies') if self.content_type=='movie' else _('Series')
        if title!=getattr(self,'_light_caption_text',None):
            self._light_caption_text=title
            ellipsize_dynamic_text(self[self.content_type+'_rail_caption'],title,fallback_chars=32)
        self._light_refresh_count()

    def _update_movie_rail_caption(self):self._light_update_caption()
    def _update_series_rail_caption(self):self._light_update_caption()

    def _light_catalog_message(self):
        from .movie_catalog import RAM_UNAVAILABLE
        catalog=self._light_field('browser_catalog')
        if catalog is None:return
        state=catalog.state
        sort_error=getattr(self._light_field('entries',()),'sort_error','')
        job=getattr(self,'_sorting_job',None)
        if job is not None:
            text=_('Loading...')+' · '+_('Sorting')
            if state.get('preparing'):text+=' · '+str(state.get('movies',0))
        elif sort_error:text=_(sort_error)
        elif state.get('preparing'):
            text='{} {}'.format(_('Loading...'),state.get('movies',0))
            if catalog.sort_mode!='provider':text+=' · '+_('Sorting')
        elif state.get('limit_kind')=='available-memory':text=_(RAM_UNAVAILABLE)
        elif state.get('reason')=='limit':text=_('Catalogue limit reached. Results may be incomplete.')
        elif not state.get('complete'):
            text=_('Search incomplete.') if self._light_field('entries',()) else _('Could not load movies') if self.content_type=='movie' else _('Could not load series')
        else:text=''
        if text!=getattr(self,'_light_catalog_text',None):
            self._light_catalog_text=text
            ellipsize_dynamic_text(self['message'],text,fallback_chars=90)

    def _update_movie_catalog_message(self):self._light_catalog_message()
    def _update_series_catalog_message(self):self._light_catalog_message()

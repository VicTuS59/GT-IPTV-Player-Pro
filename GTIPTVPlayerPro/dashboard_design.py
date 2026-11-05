# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native dashboard presentation; navigation and provider routing stay in main."""
from xml.sax.saxutils import escape
from .ui_update import set_visible
from .typography import set_text_if_changed
from Components.Label import Label
from Components.Pixmap import Pixmap
from .background import attach_native_alpha_pixmap, attach_pixmap
from .channel_highlight import saved_selection_palette
from .episode_design import episode_focus_assets
from .i18n import _, device_language, localized_upper
from .paths import plugin_path
from .remote_footer import remote_action_text
from .typography import current_text_size, fit_dynamic_text, font_px
from .vod_discovery import style_footer, refresh_plain_footer

DEFAULT_ACTIONS=('live','catchup','movie','series','discover','continue','favorites','settings')
LABELS=('brand_gt','header','account_bar','status_label','server_url','status','clock',
        'dashboard_header_line','dashboard_footer_line','dashboard_selected_caption',
        'preview_title','preview_description','dashboard_open_key','dashboard_open_text','footer')
PIXMAPS=('dashboard_hero','dashboard_bottom_shade','dashboard_open_frame')

def art(name):
    return plugin_path('skin','images','dashboard-r94',name+'.png')

def hero_path(action):
    group='live' if action in ('live','catchup') else 'cinema' if action in ('movie','series','discover') else 'lounge'
    return art(group)

def dashboard_geometry(width,height,px):
    x=lambda v:int(round(v*width/1920.))
    y=lambda v:int(round(v*height/1080.))
    rect=lambda a,b,c,d:(x(a),y(b),max(1,x(c)),max(1,y(d)))
    compact=width<=800
    footer_h=max(44,int(px(94)))
    footer_y=height-footer_h
    row_start=y(122);row_end=min(y(952),footer_y-8);step=(row_end-row_start)//8
    rows=[]
    for i in range(8):
        ry=row_start+i*step;rh=max(1,step-max(3,y(10)))
        rows.append(dict(box=(x(32),ry,x(450),rh),icon=(x(74),ry+max(0,(rh-y(52))//2),x(52),y(52)),
                         text=(x(154),ry,x(300),rh)))
    return dict(width=width,height=height,compact=compact,rows=rows,
        hero=rect(510,98,1410,884),shade=rect(510,755,1410,227),
        brand_gt=rect(40,17,80,66),header=rect(120,17,900,66),
        status=rect(1430,17,260,66),clock=rect(1730,17,150,66),
        caption=rect(550,337,1000,44),title=rect(550,397,1190,160),
        description=rect(550,579,930,115),open_key=rect(550,729,98,61),
        open_text=rect(675,727,700,66),footer_y=footer_y,footer_h=footer_h,
        menu_font=max(14,int(px(36))),title_font=max(24,int(px(82))),
        description_font=max(13,int(px(35))),caption_font=max(12,int(px(28))),
        brand_font=max(18,int(px(46))),status_font=max(12,int(px(34))))

def dashboard_skin(width,height,px):
    g=dashboard_geometry(width,height,px)
    lines=['<screen name="GTDashboardScreen" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="#030910">'.format(width,height)]
    def widget(name,box,font=1,color='#EDF2F8',background=None,pixmap=None,z=5,align='left',wrap=False):
        a,b,c,d=box
        attrs='name="{}" position="{},{}" size="{},{}" zPosition="{}"'.format(name,a,b,c,d,z)
        if pixmap or name in PIXMAPS or name.startswith(('focus_','menu_icon_','menu_row_','menu_fill_')):
            attrs+=' alphatest="blend" scale="1"'
            if pixmap:attrs+=' pixmap="{}"'.format(escape(pixmap,{'"':'&quot;'}))
        else:
            attrs+=' font="Regular;{}" foregroundColor="{}" transparent="{}" valign="center" halign="{}" noWrap="{}"'.format(max(1,int(font)),color,0 if background else 1,align,0 if wrap else 1)
            if background:attrs+=' backgroundColor="{}"'.format(background)
        lines.append('<widget {}/>'.format(attrs))
    widget('dashboard_hero',g['hero'],z=0)
    widget('dashboard_bottom_shade',g['shade'],pixmap=plugin_path('skin','images','discovery-r48','hero-bottom-r48.png'),z=1)
    widget('brand_gt',g['brand_gt'],g['brand_font'],color='#20D5ED')
    widget('header',g['header'],g['brand_font'])
    widget('status',g['status'],g['status_font'],color='#00DA7B',align='right')
    widget('clock',g['clock'],g['brand_font'],align='right')
    widget('dashboard_header_line',(max(1,int(px(24))),int(round(height*98/1080.)),width-2*max(1,int(px(24))),1),background='#2A3C50')
    for name in ('account_bar','status_label','server_url'):widget(name,(0,0,1,1))
    fill,border=episode_focus_assets(*saved_selection_palette())
    for i,row in enumerate(g['rows']):
        widget('menu_row_'+str(i),row['box'],pixmap=art('row-outline'),z=2)
        widget('menu_fill_'+str(i),row['box'],pixmap=fill,z=3)
        widget('focus_'+str(i),row['box'],pixmap=border,z=4)
        widget('menu_icon_'+str(i),row['icon'],pixmap=art('icon-'+DEFAULT_ACTIONS[i]),z=5)
        widget('card_'+str(i),row['text'],g['menu_font'],z=6)
    widget('dashboard_selected_caption',g['caption'],g['caption_font'],color='#20D5ED')
    widget('preview_title',g['title'],g['title_font'],color='#FFFFFF',wrap=True)
    widget('preview_description',g['description'],g['description_font'],color='#C7D4E5',wrap=True)
    widget('dashboard_open_frame',g['open_key'],pixmap=plugin_path('skin','images','discovery-r48','key-outline-r48.png'))
    widget('dashboard_open_key',g['open_key'],max(14,int(px(30))),align='center')
    widget('dashboard_open_text',g['open_text'],max(14,int(px(31))))
    widget('dashboard_footer_line',(max(1,int(px(24))),g['footer_y']-2,width-2*max(1,int(px(24))),1),background='#2A3C50',z=30)
    widget('footer',(0,g['footer_y'],width,g['footer_h']),max(1,int(px(20))),z=30)
    lines.append('</screen>')
    return '\n'.join(lines)

class DashboardPresentation(object):
    def _setup_dashboard(self,scale,normal_items,move_items):
        width,height,px=scale
        self._dashboard_px=px
        self._dashboard_geometry=dashboard_geometry(width,height,px)
        self._dashboard_normal_footer=normal_items
        self._dashboard_move_footer=move_items
        self._dashboard_icon_actions={}
        self._dashboard_focus_palette=None
        self._dashboard_selection_key=None
        self._dashboard_hero_path=''
        self._dashboard_hero_generation=0
        self._dashboard_hero_loaded=False
        for name in LABELS:self[name]=Label('')
        for name in PIXMAPS:self[name]=Pixmap()
        for i in range(len(self._menu_items)):
            self['card_'+str(i)]=Label('')
            for prefix in ('menu_row_','menu_fill_','focus_','menu_icon_'):self[prefix+str(i)]=Pixmap()
        set_text_if_changed(self['brand_gt'], 'GT')
        set_text_if_changed(self['header'], 'IPTV PLAYER PRO')
        set_text_if_changed(self['dashboard_selected_caption'], '')
        set_text_if_changed(self['dashboard_open_key'], 'OK')
        set_text_if_changed(self['dashboard_open_text'], remote_action_text('open'))
        for name in ('account_bar','status_label','server_url','dashboard_hero'):set_visible(self[name], False)
        self.skin=style_footer(self.skin,'movie',px,normal_items,
                               tuple(remote_action_text(item[1]) for item in normal_items))

    def _refresh_dashboard_footer(self):
        items=self._dashboard_move_footer if self._menu_move_backup is not None else self._dashboard_normal_footer
        refresh_plain_footer(self, items, self._dashboard_px,
                             tuple(remote_action_text(item[1]) for item in items),
                             keys={'up_down': '▲▼'})
        for i,item in enumerate(items):
            if not item[0]:
                set_visible(self['remote_key_'+str(i)], False)
                set_visible(self['remote_key_bg_'+str(i)], False)

    def _refresh_selection(self):
        if self._dashboard_closed:return
        px=self._dashboard_px;g=self._dashboard_geometry
        palette=saved_selection_palette()
        key = (id(getattr(self['preview_title'], 'instance', None)), palette,
               device_language(), current_text_size(), self.selected_index,
               tuple(item[2] for item in self._menu_items),
               self._menu_move_backup is not None, self._menu_move_error)
        if key == self._dashboard_selection_key:
            return
        self._dashboard_selection_key = key
        palette_changed=palette!=self._dashboard_focus_palette
        if palette_changed:self._dashboard_focus_palette=palette
        fill,border=episode_focus_assets(*palette)
        for i,item in enumerate(self._menu_items):
            action=item[2];label=localized_upper(_(item[0]))
            if not self._action_available(action):label+='  —'
            fit_dynamic_text(self['card_'+str(i)],label,max_lines=1,fallback_chars=28,
                             preferred_size=font_px(px,36 if action!='continue' else 31),min_size=max(11,int(px(22))))
            selected=i==self.selected_index
            for prefix,path in (('menu_fill_',fill),('focus_',border)):
                name=prefix+str(i)
                if palette_changed:attach_native_alpha_pixmap(self,name,path,key=name,show_on_load=False)
                set_visible(self[name], selected)
            if self._dashboard_icon_actions.get(i)!=action:
                self._dashboard_icon_actions[i]=action
                name='menu_icon_'+str(i)
                attach_native_alpha_pixmap(self,name,art('icon-'+action),key=name)
        item=self._menu_items[self.selected_index]
        fit_dynamic_text(self['preview_title'],localized_upper(_(item[0])),max_lines=2,fallback_chars=40,
                         preferred_size=font_px(px,82,role='title'),min_size=max(22,int(px(42))))
        description=_(item[1])
        moving=self._menu_move_backup is not None
        if moving:
            description=_('Settings could not be saved. Check the receiver storage.') if self._menu_move_error else _('Move')
        fit_dynamic_text(self['preview_description'],description,max_lines=2,fallback_chars=90,
                         preferred_size=font_px(px,35),min_size=max(12,int(px(25))))
        for name in ('dashboard_open_frame','dashboard_open_key','dashboard_open_text'):
            set_visible(self[name], not (moving))
        self._refresh_dashboard_footer()
        path=hero_path(item[2])
        if path==self._dashboard_hero_path:return
        self._dashboard_hero_path=path
        self._dashboard_hero_generation+=1
        generation=self._dashboard_hero_generation
        self._dashboard_hero_loaded=False
        set_visible(self['dashboard_hero'], False)
        def loaded(success):
            if self._dashboard_closed or generation!=self._dashboard_hero_generation or path!=self._dashboard_hero_path:return
            self._dashboard_hero_loaded=bool(success)
            if success:set_visible(self['dashboard_hero'], True)
            else:
                set_visible(self['dashboard_hero'], False)
                self._dashboard_hero_path=''
                self._dashboard_selection_key=None
        attach_pixmap(self,'dashboard_hero',path,key='dashboard-selected-hero',on_loaded=loaded,
                      cover_ratio=(16,9),cover_alignment='right',
                      decode_size=(min(1920,g['hero'][2]),min(1080,g['hero'][3])),show_on_load=False)

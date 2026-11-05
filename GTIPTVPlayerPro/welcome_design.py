# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Welcome-screen presentation only; source and connection state stay in main."""
from xml.sax.saxutils import escape
from .ui_update import set_visible
from .typography import set_text_if_changed
from Components.Label import Label
from Components.Pixmap import Pixmap
from .background import attach_native_alpha_pixmap
from .channel_highlight import saved_selection_palette, welcome_selection_art
from .episode_design import episode_focus_assets
from .auto_login_ui import auto_login_overlay_skin
from .i18n import _,device_language,localized_upper
from .paths import plugin_path
from .remote_footer import remote_action_text
from .typography import fit_dynamic_text,font_px
from .vod_discovery import style_footer,refresh_plain_footer
from .welcome_text import fit_action_caption,fit_welcome_title

BUTTONS=('connect_button','accounts_button','add_button','youtube_button','matches_button')
ICONS=('connect','manage','add','youtube','matches')
EXTRA_LABELS=('welcome_brand_gt','welcome_version','welcome_header_line','welcome_column_line',
              'welcome_device_line','welcome_footer_line','welcome_status_dot')
EXTRA_PIXMAPS=('welcome_source_panel','welcome_source_icon','welcome_shield')

def art(name):return plugin_path('skin','images','welcome-r95',name+'.png')

def welcome_geometry(width,height,px):
    x=lambda v:int(round(v*width/1920.));y=lambda v:int(round(v*height/1080.))
    rect=lambda a,b,c,d:(x(a),y(b),max(1,x(c)),max(1,y(d)))
    actions=[];available=1214-4*18
    for i in range(5):
        left=635+i*available//5+i*18;right=635+(i+1)*available//5+i*18
        actions.append(dict(box=rect(left,590,right-left,275),icon=rect(left+(right-left-112)//2,635,112,112),
                            label=rect(left+14,772,right-left-28,77)))
    footer_h=max(50,int(px(94)));footer_y=height-footer_h
    return dict(width=width,height=height,actions=actions,footer_y=footer_y,footer_h=footer_h,
        brand=rect(64,20,72,66),header=rect(140,20,1160,66),version=rect(1495,20,355,66),
        eyebrow=rect(72,165,470,55),title=rect(72,237,470,185),subtitle=rect(72,440,470,150),
        shield=rect(205,620,180,190),device=rect(72,903,470,57),
        caption=rect(635,150,770,55),meta=rect(1445,150,400,55),source=rect(635,220,1214,325),
        source_icon=rect(712,285,215,210),account=rect(1025,261,778,100),
        status=rect(1067,369,736,45),status_dot=rect(1025,369,32,45),
        expiry=rect(1025,435,778,42),connection=rect(1025,485,778,42),
        hint=rect(647,895,1190,58),
        brand_font=max(18,int(px(46))),title_font=max(24,int(px(60))),
        subtitle_font=max(13,int(px(33))),account_font=max(16,int(px(44))),
        status_font=max(12,int(px(29))),detail_font=max(12,int(px(27))),
        caption_font=max(12,int(px(28))),button_font=max(12,int(px(32))))

def welcome_skin(width,height,px):
    g=welcome_geometry(width,height,px)
    lines=['<screen name="GTIPTVPlayerProScreen" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="#04101F">'.format(width,height)]
    def widget(name,box,font=1,color='#EDF2F8',background=None,pixmap=None,z=5,align='left',wrap=False):
        a,b,c,d=box;attrs='name="{}" position="{},{}" size="{},{}" zPosition="{}"'.format(name,a,b,c,d,z)
        if pixmap or name in EXTRA_PIXMAPS or name.startswith(('focus_','welcome_action_','welcome_fill_')):
            attrs+=' scale="1" alphatest="blend"'
            if pixmap:attrs+=' pixmap="{}"'.format(escape(pixmap,{'"':'&quot;'}))
        else:
            attrs+=' font="Regular;{}" foregroundColor="{}" transparent="{}" valign="center" halign="{}" noWrap="{}"'.format(max(1,int(font)),color,0 if background else 1,align,0 if wrap else 1)
            if background:attrs+=' backgroundColor="{}"'.format(background)
        lines.append('<widget {}/>'.format(attrs))
    widget('video_guard',(0,0,width,height),background='#04101F',z=-20)
    widget('welcome_brand_gt',g['brand'],g['brand_font'],color='#20D5ED')
    widget('header',g['header'],g['brand_font'])
    widget('welcome_version',g['version'],max(13,int(px(30))),color='#A9BBD2',align='right')
    line_left=int(round(width*36/1920.));line_width=width-2*line_left
    widget('welcome_header_line',(line_left,int(round(height*104/1080.)),line_width,1),background='#34536F')
    widget('welcome_column_line',(int(round(width*590/1920.)),int(round(height*156/1080.)),1,max(1,int(round(height*770/1080.)))),background='#34536F')
    widget('eyebrow',g['eyebrow'],g['caption_font'],color='#20D5ED')
    widget('welcome_title',g['title'],g['title_font'],color='#FFFFFF')
    widget('subtitle',g['subtitle'],g['subtitle_font'],color='#B7C9E0',wrap=True)
    widget('welcome_shield',g['shield'],pixmap=art('shield'))
    widget('welcome_device_line',(g['device'][0],g['device'][1]-max(3,int(px(16))),g['device'][2],1),background='#34536F')
    widget('device_info',g['device'],max(10,int(px(19))),color='#A9BBD2',wrap=True)
    widget('account_caption',g['caption'],g['caption_font'],color='#20D5ED')
    widget('account_meta',g['meta'],max(11,int(px(25))),color='#A9BBD2',align='right')
    widget('welcome_source_panel',g['source'],pixmap=art('source-card'),z=1)
    widget('welcome_source_icon',g['source_icon'],pixmap=art('server'),z=2)
    widget('account_card',g['account'],g['account_font'],color='#FFFFFF',wrap=True)
    widget('account_status',g['status'],g['status_font'],color='#BBD5E9',wrap=True)
    widget('welcome_status_dot',g['status_dot'],g['status_font'],color='#00D89A')
    for prefix,key in (('account_expiry','expiry'),('account_connection','connection')):
        for suffix,color in (('','#BED0E5'),('_green','#DAE7F4'),('_yellow','#FFD239'),('_red','#FF5269')):
            widget(prefix+suffix,g[key],g['detail_font'],color=color)
    fill,border=episode_focus_assets(*saved_selection_palette())
    for i,row in enumerate(g['actions']):
        widget('welcome_action_bg_'+str(i),row['box'],pixmap=art('action-card'),z=2)
        widget('welcome_fill_'+str(i),row['box'],pixmap=fill,z=3)
        widget('focus_'+str(i),row['box'],pixmap=welcome_selection_art(saved_selection_palette()[1]),z=4)
        icon=plugin_path('skin','images','dashboard-r94','icon-continue.png') if i==0 else art(ICONS[i])
        widget('welcome_action_icon_'+str(i),row['icon'],pixmap=icon,z=5)
        widget(BUTTONS[i],row['label'],g['button_font'],color='#FFFFFF',z=5,align='center',wrap=True)
    widget('hint',g['hint'],max(11,int(px(21))),color='#A9BBD2',wrap=True)
    widget('welcome_footer_line',(line_left,g['footer_y']-2,line_width,1),background='#34536F',z=30)
    widget('footer',(0,g['footer_y'],width,g['footer_h']),max(1,int(px(20))),z=30)
    for name in ('top_accent','brand_panel','brand_mark','right_panel','account_accent'):
        widget(name,(0,0,1,1),z=-30)
    lines.append(auto_login_overlay_skin(width,height))
    lines.append('</screen>');return '\n'.join(lines)

def sentence_caption(text):
    if device_language().startswith('tr'):
        text=text.replace('I','ı').replace('İ','i')
    text=text.lower()
    return localized_upper(text[:1])+text[1:]

class WelcomePresentation(object):
    def _refresh_welcome_action_focus(self):
        for index in range(5):
            set_visible(self['focus_'+str(index)], self.selected_action == index)
        self._refresh_welcome_chrome()

    def _setup_welcome(self,scale,items,version):
        width,height,px=scale;self._welcome_px=px;self._welcome_footer_items=items
        self._welcome_geometry=welcome_geometry(width,height,px);self._welcome_palette=None
        for name in EXTRA_LABELS:self[name]=Label('')
        for name in EXTRA_PIXMAPS:self[name]=Pixmap()
        for i in range(5):
            for prefix in ('welcome_action_bg_','welcome_action_icon_','welcome_fill_'):self[prefix+str(i)]=Pixmap()
        set_text_if_changed(self['welcome_brand_gt'], 'GT');set_text_if_changed(self['header'], 'IPTV PLAYER PRO')
        set_text_if_changed(self['welcome_version'], 'v'+str(version))
        set_text_if_changed(self['eyebrow'], localized_upper(_('Welcome')))
        fit_welcome_title(self['welcome_title'],_('Ready to watch?'),preferred_size=font_px(px,60,role='title'),min_size=max(12,int(px(36))))
        set_text_if_changed(self['welcome_status_dot'], '●')
        self.skin=style_footer(self.skin,'movie',px,items,tuple(remote_action_text(item[1]) for item in items))
        self.onShown.append(self._refresh_welcome_chrome)

    def _refresh_welcome_chrome(self):
        px=self._welcome_px
        fit_welcome_title(self['welcome_title'],_('Ready to watch?'),preferred_size=font_px(px,60,role='title'),min_size=max(12,int(px(36))))
        fit_dynamic_text(self['subtitle'],self['subtitle'].getText(),max_lines=3,preferred_size=font_px(px,33),min_size=max(12,int(px(25))))
        palette=saved_selection_palette();changed=palette!=self._welcome_palette
        if changed:self._welcome_palette=palette
        fill,border=episode_focus_assets(*palette)
        from .matches_text import tr as match_tr
        captions=(_('CONNECT SOURCE'),_('MANAGE SOURCES'),_('ADD SOURCE'),'YouTube',match_tr('Matches'))
        for i,name in enumerate(BUTTONS):
            text=captions[i]
            if name!='youtube_button':text=sentence_caption(text.replace('\n',' '))
            fit_action_caption(self[name],text,preferred_size=font_px(px,32),min_size=max(11,int(px(24))))
            component='welcome_fill_'+str(i)
            if changed:attach_native_alpha_pixmap(self,component,fill,key=component,show_on_load=False)
            set_visible(self[component], self.selected_action==i)
        set_visible(self['welcome_status_dot'], self.accounts)
        refresh_plain_footer(self, self._welcome_footer_items, px,
                             tuple(remote_action_text(item[1]) for item in self._welcome_footer_items),
                             keys={'up_down': '▲▼'})

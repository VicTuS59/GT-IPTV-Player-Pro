# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native, PNG-backed presentation for source choosers and player settings."""
from .ui_update import set_visible
from .typography import set_text_if_changed
from xml.sax.saxutils import escape
from Components.Label import Label
from Components.Pixmap import Pixmap
from .background import attach_native_alpha_pixmap
from .channel_highlight import saved_selection_palette
from .episode_design import episode_focus_assets
from .i18n import _,localized_upper
from .paths import plugin_path
from .remote_footer import remote_action_text, skin_metadata, bind_layout_text
from .typography import current_text_size,fit_dynamic_text,font_px
from .vod_discovery import style_footer,refresh_plain_footer
from .welcome_design import sentence_caption

CANVAS='#04101F'
def art(name):return plugin_path('skin','images','utility-r96',name+'.png')

class NativeSkin(object):
    def __init__(self,name,width,height,px):
        self.width,self.height,self.px=width,height,px;self.names=set()
        self.parts=['<screen name="{}" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="{}">'.format(name,width,height,CANVAS)]
    def rect(self,box):
        a,b,c,d=box
        return (round(a*self.width/1920.),round(b*self.height/1080.),max(1,round(c*self.width/1920.)),max(1,round(d*self.height/1080.)))
    def label(self,name,box,font=1,color='#EDF2F8',background=None,z=5,align='left',lines=1):
        self.names.add(name);a,b,c,d=self.rect(box);size=1 if font==1 else max(9,font_px(self.px,font))
        attrs='name="{}" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" transparent="{}" zPosition="{}" valign="center" halign="{}" noWrap="{}"'.format(name,a,b,c,d,size,color,0 if background else 1,z,align,0 if lines>1 else 1)
        if background:attrs+=' backgroundColor="{}"'.format(background)
        self.parts.append('<widget {}/>'.format(attrs))
    def pic(self,name,box,path=None,z=2):
        self.names.add(name);a,b,c,d=self.rect(box)
        attrs='name="{}" position="{},{}" size="{},{}" scale="1" alphatest="blend" zPosition="{}"'.format(name,a,b,c,d,z)
        if path:attrs+=' pixmap="{}"'.format(escape(path,{'"':'&quot;'}))
        self.parts.append('<widget {}/>'.format(attrs))
    def finish(self):return '\n'.join(self.parts+['</screen>'])

def bind_missing(screen):
    for name,is_label in skin_metadata(screen.skin).widgets:
        if name not in screen:screen[name]=Label('') if is_label else Pixmap()
    bind_layout_text(screen)

def plain_footer(screen,items,px):
    keys={'up_down':'▲▼','left_right':'◀▶'}
    sizes={}
    if hasattr(screen,'_r97_kind'):
        keys.update(arrows='▲▼◀▶',channel='CH±');sizes.update(arrows=22,channel=22)
    minimums={key:max(9,px(18)) for key in sizes}
    refresh_plain_footer(screen,items,px,tuple(remote_action_text(item[1]) for item in items),keys=keys,sizes=sizes,minimums=minimums)
    for i,item in enumerate(items):
        if not item[0]:
            set_visible(screen['remote_key_'+str(i)], False);set_visible(screen['remote_key_bg_'+str(i)], False)

def source_skin(width,height,px,manage=False,palette=None):
    s=NativeSkin('GTManageSourcesScreen' if manage else 'GTAddSourceTypeScreen',width,height,px)
    s.label('r96_brand_gt',(64,21,70,66),46,'#20D5ED');s.label('r96_brand',(140,21,1040,66),44)
    s.label('r96_crumb',(1450,21,400,66),28,'#A9BBD2',align='right')
    s.label('r96_header_line',(48,103,1824,2),background='#29435E')
    s.label('header',(266,137,1150,76),54,lines=1)
    s.label('instruction',(266,216,1300,53),30,'#B7C9E0')
    s.label('step',(1400,147,285,60),25,'#A9BBD2',align='right')
    fill,border=episode_focus_assets(*(saved_selection_palette() if palette is None else palette))
    icons=(plugin_path('skin','images','welcome-r95','server.png'),art('folder'),art('portal'))
    for i in range(3):
        y=278+i*204;box=(235,y,1450,184)
        s.pic('r96_source_card_'+str(i),box,art('source-row'))
        s.pic('r96_source_fill_'+str(i),box,fill,z=3);s.pic('r96_source_focus_'+str(i),box,border,z=4)
        s.pic('r96_source_icon_'+str(i),(300,y+24,125,125),icons[i],z=5)
        s.label('icon_'+str(i),(398,y+126,92,36),23,'#20D5ED',align='center')
        s.label('title_'+str(i),(555,y+20,815 if manage else 1030,55),39)
        s.label('description_'+str(i),(555,y+79,1085,43),27,'#B7C9E0')
        s.label('capabilities_'+str(i),(555,y+128,1030,38),26,'#20D5ED')
        s.pic('r96_count_bg_'+str(i),(1390,y+38,250,62),art('count-badge'),z=4)
        s.label('count_'+str(i),(1400,y+41,230,56),26,'#20D5ED',z=5,align='center')
        s.label('card_'+str(i),(0,0,1,1),z=-40)
        for edge in ('top','bottom','left','right'):s.label('focus_{}_{}'.format(i,edge),(0,0,1,1),z=-40)
    s.label('badge_1',(1400,278+204+130,240,36),24,'#20D5ED',align='right')
    s.pic('r96_info_panel',(235,896,1450,78),art('info-panel'))
    s.pic('r96_info_icon',(267,913,44,44),art('info'),z=5)
    s.label('message',(340,905,1310,60),25,'#B7C9E0',lines=2)
    s.label('info_icon',(0,0,1,1),z=-40)
    for name in ('panel','info_panel'):s.label(name,(0,0,1,1),z=-40)
    for name in ('app_bg','manage_panel_art','manage_info_art','manage_footer_art')+tuple('manage_card_art_'+str(i) for i in range(3)):
        s.pic(name,(0,0,1,1),z=-40)
    s.label('r96_footer_line',(48,984,1824,2),background='#29435E',z=30)
    s.label('footer',(0,986,1920,94),20,z=30)
    return s.finish()

class SourcePresentation(object):
    def _setup_source_presentation(self,scale,items,manage=False):
        width,height,px=scale;self._r96_source_px=px;self._r96_source_items=items
        self._r96_source_manage=manage;self._r96_source_palette=None
        self._r96_source_scale=scale
        self._r96_source_rows=None
        bind_missing(self)
        self.skin=style_footer(self.skin,'movie',px,items,tuple(remote_action_text(item[1]) for item in items))
        self.onShown.append(self._refresh_source_presentation)
        self.onLayoutFinish.append(self._refresh_source_presentation)

    def _load_source_selection_art(self,palette):
        fill,border=episode_focus_assets(*palette)
        if self._r96_source_manage:
            from .opaque_row_art import prepare_opaque_rows
            skin=source_skin(*self._r96_source_scale,manage=True,palette=palette)
            previous=self._r96_source_rows
            self._r96_source_rows=prepare_opaque_rows(skin,None,'r96_source_card_',3,
                ((),('r96_source_fill_','r96_source_focus_')))
            for i in range(3):
                name='r96_source_card_'+str(i)
                widget=self[name]
                widget._gt_opaque_row=None
                if previous is not None and self._r96_source_rows is None:
                    instance=widget.instance
                    if instance is not None:
                        instance.setAlphatest(2)
                        instance.setScale(1)
                        instance.setTransparent(True)
                    attach_native_alpha_pixmap(self,name,art('source-row'),key=name,show_on_load=False)
            if self._r96_source_rows is not None:
                return
        for i in range(3):
            for prefix,path in (('r96_source_fill_',fill),('r96_source_focus_',border)):
                name=prefix+str(i)
                attach_native_alpha_pixmap(self,name,path,key=name,show_on_load=False)

    def _refresh_source_selection(self,previous=None):
        selected=self.selected_index
        slots=range(3) if previous is None else (previous,selected)
        for slot in slots:
            active=slot==selected
            rows=self._r96_source_rows
            if rows is not None and rows.paint(self,slot,True,int(active)):
                continue
            for prefix in ('r96_source_fill_','r96_source_focus_'):
                set_visible(self[prefix+str(slot)],active and rows is None)
        set_text_if_changed(self['step'], '{} / 3 · {}'.format(selected+1,_('Source type')))

    def _refresh_source_presentation(self):
        px=self._r96_source_px;manage=self._r96_source_manage
        set_text_if_changed(self['r96_brand_gt'], 'GT');set_text_if_changed(self['r96_brand'], 'IPTV PLAYER PRO');set_text_if_changed(self['r96_crumb'], _('Sources'))
        set_text_if_changed(self['header'], _('Manage Sources') if manage else _('Add Source'))
        titles=(_('Xtream Codes Accounts'),_('M3U Sources'),_('Stalker / MAC Portal Accounts')) if manage else ('Xtream Codes','M3U '+_('Live TV'),'Stalker / MAC '+_('Portal'))
        palette=saved_selection_palette()
        if palette!=self._r96_source_palette:
            self._load_source_selection_art(palette)
            self._r96_source_palette=palette
        for i,title in enumerate(titles):
            title=sentence_caption(title).replace('Xtream codes','Xtream Codes').replace('M3u','M3U').replace(' / mac',' / MAC').replace(' tv',' TV')
            fit_dynamic_text(self['title_'+str(i)],title,max_lines=1,preferred_size=font_px(px,39),min_size=max(11,int(px(23))))
            for name in ('count_'+str(i),'r96_count_bg_'+str(i)):set_visible(self[name], manage)
            if manage:set_text_if_changed(self['count_'+str(i)], sentence_caption(self['count_'+str(i)].getText()))
            caps=(_('Live TV'),_('Movies'),_('Series')) if i!=1 else ((_('Live TV'),) if manage else (_('Local file'),'NAS','HTTP(S)'))
            set_text_if_changed(self['capabilities_'+str(i)], ' · '.join(caps))
        set_visible(self['badge_1'], not (manage))
        if not manage:set_text_if_changed(self['badge_1'], _('Live TV'))
        self._refresh_source_selection()
        plain_footer(self,self._r96_source_items,px)

def settings_geometry(width,height,px):
    x=lambda v:round(v*width/1920.);y=lambda v:round(v*height/1080.)
    return dict(scroll=(x(1224),y(270),max(3,x(8)),y(672)))

def settings_skin(width,height,px):
    s=NativeSkin('GTPlayerSettingsScreen',width,height,px)
    s.label('r96_brand_gt',(64,21,70,66),46,'#20D5ED');s.label('brand',(140,21,850,66),44)
    s.label('header',(1045,21,790,66),35,'#A9BBD2')
    s.label('r96_header_line',(48,103,1824,2),background='#29435E')
    s.label('r96_header_divider',(1002,37,2,38),background='#29435E')
    s.label('player_tab_bg',(64,126,870,94),background='#12395E',z=2)
    s.pic('r96_player_frame',(64,126,870,94),art('tab-active'),z=3)
    s.pic('r96_language_frame',(950,126,906,94),art('tab-idle'),z=3)
    s.label('player_tab',(64,126,870,94),36,align='center')
    s.label('language_tab',(950,132,906,52),33,align='center')
    s.label('language_soon',(950,181,906,32),23,'#A9BBD2',align='center')
    for i in range(9):
        y=270+76*i;box=(64,y,1150,66)
        s.pic('r96_settings_row_'+str(i),box,art('row-outline'),z=2)
        s.label('row_focus_'+str(i),box,background='#14375E',z=2)
        s.label('row_marker_'+str(i),(64,y,4,66),background='#20D5ED',z=3)
        s.pic('r96_settings_focus_'+str(i),box,art('row-selected'),z=4)
        s.label('row_label_'+str(i),(100,y,710,66),30,z=5)
        s.label('row_value_'+str(i),(852,y+4,350,58),28,z=5,align='center')
        s.label('row_bg_'+str(i),(0,0,1,1),z=-40);s.label('value_bg_'+str(i),(0,0,1,1),z=-40)
        for state in ('on','off','error'):s.pic('r96_state_{}_{}'.format(state,i),(937,y+7,180,52),art('state-'+state),z=4)
        s.label('r96_state_text_'+str(i),(941,y+7,172,52),26,z=5,align='center')
        s.pic('r96_chevron_'+str(i),(1172,y+24,20,20),art('chevron'),z=6)
    s.label('settings_scroll_track',(1224,270,8,672),background='#18334D',z=4)
    s.label('settings_scroll_thumb',(1224,270,8,90),background='#20D5ED',z=5)
    for name in ('settings_up','settings_down','panel','top_accent','language_tab_bg'):s.label(name,(0,0,1,1),z=-40)
    s.pic('app_bg',(0,0,1,1),z=-40)
    s.pic('r96_settings_panel',(1260,264,596,700),art('detail-panel'),z=1)
    s.label('r96_setting_header',(1300,285,516,55),30,'#20D5ED')
    s.pic('r96_setting_icon',(1340,370,112,112),art('settings-tv'))
    s.label('r96_setting_title',(1300,500,516,95),34,lines=2)
    s.label('r96_setting_help',(1300,610,516,110),26,'#B7C9E0',lines=3)
    s.label('r96_value_caption',(1300,739,516,38),23,'#A9BBD2')
    s.pic('r96_value_panel',(1300,780,516,72),art('value-panel'))
    s.label('r96_setting_value',(1316,787,484,58),29,lines=2)
    s.label('message',(1300,875,516,78),23,'#A9BBD2',lines=3)
    s.label('r96_footer_line',(48,984,1824,2),background='#29435E',z=30)
    s.label('footer',(0,986,1920,94),20,z=30)
    return s.finish()

SETTING_HELP={0:'Select the player used for live TV.',1:'Select the player used for movies.',2:'Select the player used for series.',
    3:'Enable or disable TMDb metadata enrichment.',4:'Press OK to enter or change the TMDb key.',
    5:'Press OK to open the DVB EPG settings.',6:'Press OK to turn the category edit lock on or off.',8:'Open independent subtitle settings.'}

class SettingsPresentation(object):
    def _setup_settings_presentation(self,scale,items,move_items):
        width,height,px=scale;self._r96_settings_px=px
        self._r96_settings_items=items;self._r96_settings_move_items=move_items
        bind_missing(self)
        self.skin=style_footer(self.skin,'movie',px,items,tuple(remote_action_text(item[1]) for item in items))
        from .opaque_row_art import DeferredOpaqueArt
        self._r96_settings_row_art = None
        self._r96_settings_panel_art = {}
        DeferredOpaqueArt(self, ('r96_settings_panel',), (
            (None, 'r96_settings_row_', 9, ((), ('row_focus_', 'row_marker_', 'r96_settings_focus_'))),
        ), self._install_settings_art)

    def _install_settings_art(self, panels, rows):
        from .opaque_row_art import install_opaque_panels
        self._r96_settings_row_art, = rows
        self._r96_settings_panel_art = panels
        install_opaque_panels(self, panels)
        selected = self._settings_order.index(self.selected_index) - self._settings_window
        for slot in range(9):
            self._paint_settings_row(slot, slot == selected)

    def _paint_settings_row(self, slot, selected):
        artwork = self._r96_settings_row_art
        if artwork is not None and artwork.paint(self, slot, True, int(selected)):
            return
        for prefix in ('row_focus_', 'row_marker_', 'r96_settings_focus_'):
            set_visible(self[prefix + str(slot)], selected)

    def _refresh_settings_presentation(self, previous_slot=None):
        from .opaque_row_art import install_opaque_panels
        install_opaque_panels(self, self._r96_settings_panel_art)
        px = self._r96_settings_px
        profile = current_text_size()
        index = self.selected_index
        value = self._value_text(index)
        fit_dynamic_text(self['r96_setting_title'], _(self.LABELS[index]), max_lines=2, preferred_size=font_px(px,34,value=profile), min_size=max(12,int(px(23))))
        fit_dynamic_text(self['r96_setting_help'], _(SETTING_HELP.get(index,self.LABELS[index])), max_lines=3, preferred_size=font_px(px,26,value=profile), min_size=max(11,int(px(20))))
        fit_dynamic_text(self['r96_setting_value'], value, max_lines=2, preferred_size=font_px(px,29,value=profile), min_size=max(11,int(px(20))))
        if previous_slot is not None:
            selected_slot = self._settings_order.index(index) - self._settings_window
            if previous_slot != selected_slot:
                self._paint_settings_row(previous_slot, False)
            self._paint_settings_row(selected_slot, True)
            return
        set_text_if_changed(self['r96_brand_gt'], 'GT')
        set_text_if_changed(self['brand'], 'IPTV PLAYER PRO')
        set_text_if_changed(self['r96_setting_header'], localized_upper(_('Setting information')))
        set_text_if_changed(self['r96_value_caption'], _('Selected value'))
        label_font = font_px(px, 30, value=profile)
        value_font = font_px(px, 28, value=profile)
        state_font = font_px(px, 26, value=profile)
        for slot in range(9):
            item=self._settings_order[self._settings_window+slot]
            self._paint_settings_row(slot, item==index)
            state=('on' if self.settings.metadata_enabled else 'off') if item==3 else (
                'on' if self._category_lock_status=='enabled' else 'off' if self._category_lock_status=='disabled' else 'error') if item==6 else None
            for key in ('on','off','error'):
                node=self['r96_state_{}_{}'.format(key,slot)];set_visible(node, key==state)
            state_text=self['r96_state_text_'+str(slot)]
            row_value = value if item == index else self._value_text(item)
            if state:
                fit_dynamic_text(state_text, row_value, max_lines=1, preferred_size=state_font, min_size=max(10,int(px(18))))
                set_visible(state_text, True);set_visible(self['row_value_'+str(slot)], False)
            else:set_visible(state_text, False);set_visible(self['row_value_'+str(slot)], True)
            set_visible(self['r96_chevron_'+str(slot)], item in (5,7,8,9,10,11,12,13,14,15,16,17))
            label = _(self.LABELS[item])
            if self._settings_move_backup is not None and item == index:
                label = "↕ " + label
            fit_dynamic_text(self['row_label_'+str(slot)], label, max_lines=1, preferred_size=label_font, min_size=max(11,int(px(20))))
            fit_dynamic_text(self['row_value_'+str(slot)], row_value, max_lines=1, preferred_size=value_font, min_size=max(11,int(px(20))))
        items=self._r96_settings_move_items if self._settings_move_backup is not None else self._r96_settings_items
        plain_footer(self,items,px)

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native component geometry measured from the seven approved PNG previews."""
from xml.etree import ElementTree as ET
from .paths import plugin_path
from .utility_design import NativeSkin

INK='#EDF2F8';MUTED='#AFC1D8';CYAN='#20D5ED'
def art(name):return plugin_path('skin','images','seven-r100',name+'.png')
def state_art(state):return plugin_path('skin','images','accounts-r99','status-'+state+'.png')
def panel(s,name,box,right=False):s.pic(name,box,art('right-panel' if right else 'left-panel'),z=1)
def line(s,name,x,y,w):s.label(name,(x,y,w,2),background='#244E76',z=3)
def badge(s,name,box):s.pic(name,box,art('badge'),z=5)

def settings(s,automation=False):
    panel(s,'r100_list_panel',(42,144,1276,730));panel(s,'r100_side_panel',(1338,144,548,790 if automation else 730),True)
    for i in range(7):
        y=156+i*100
        s.pic('r100_focus_'+str(i),(51,y,1258,98),art('row-focus'),z=3)
        s.label('label_'+str(i),(84,y+8,740,82),32,lines=2)
        s.label('value_'+str(i),(844,y+8,435,82),30,align='right',lines=2)
        line(s,'r100_row_line_'+str(i),47,y+98,1260)
        for state in ('green','neutral'):s.pic('r100_pill_{}_{}'.format(i,state),(1048,y+20,232,58),state_art(state),z=4)
        s.label('r100_pill_text_'+str(i),(1058,y+23,212,52),30,align='center')
    s.label('r100_side_caption',(1372,166,480,62),34,CYAN)
    line(s,'r100_side_divider',1372,230,480)
    s.pic('r100_side_icon',(1530,284,160,160),art('clock' if automation else 'guide'))
    s.label('r100_side_title',(1372,480,480,84),50,align='center',lines=2)
    s.label('r100_side_info',(1372,574,480,108),32,MUTED,align='center',lines=3)
    line(s,'r100_side_line2',1372,698,480)
    s.label('r100_side_label',(1372,718,480,50),28,MUTED,align='center')
    s.label('r100_side_value',(1372,770,480,94),32,align='center',lines=3)
    if automation:
        line(s,'r100_lock_divider',1372,790,480)
        s.pic('r100_lock_icon',(1392,828,58,58),art('lock'),z=5)
        s.label('r100_lock_note',(1480,820,350,84),27,MUTED,lines=2)
    s.label('message',(64,894,1780,58),25,MUTED,lines=2)

def mapping(s):
    for i in range(3):
        badge(s,'r100_count_bg_'+str(i),(60+i*280,120,254,52))
        s.label('r100_count_'+str(i),(68+i*280,122,238,48),28,align='center')
    panel(s,'r100_list_panel',(42,178,1276,774));panel(s,'r100_side_panel',(1338,174,548,778),True)
    s.label('r100_map_col1',(78,191,800,58),30,MUTED);s.label('r100_map_col2',(1005,191,280,58),30,MUTED)
    for i in range(8):
        y=252+i*86
        s.pic('r100_focus_'+str(i),(48,y,1260,84),art('row-focus'),z=3)
        s.label('r100_map_no_'+str(i),(75,y+8,80,68),36,MUTED,align='center')
        s.label('r100_map_name_'+str(i),(182,y+8,800,68),36)
        badge(s,'r100_map_status_bg_'+str(i),(1055,y+15,230,54))
        s.label('r100_map_status_'+str(i),(1065,y+17,210,50),27,align='center',lines=2)
        line(s,'r100_map_line_'+str(i),47,y+84,1260)
    s.label('list_scroll_track',(1320,252,5,687),background='#244E76');s.label('list_scroll_thumb',(1320,252,5,100),background=CYAN,z=6)
    s.label('r100_side_caption',(1372,190,480,64),35,CYAN)
    line(s,'r100_side_divider',1372,254,480)
    s.pic('r100_side_icon',(1530,310,160,160),art('mapping'))
    s.label('r100_side_title',(1372,488,480,86),52,align='center',lines=2)
    badge(s,'r100_mapping_status_bg',(1440,586,346,60));s.label('r100_mapping_status',(1450,590,326,52),32,align='center',lines=2)
    line(s,'r100_side_line2',1372,670,480)
    s.label('r100_side_label',(1372,696,480,52),30,MUTED)
    s.label('r100_side_value',(1372,750,480,88),32,lines=2)
    s.label('message',(1372,850,480,90),26,MUTED,lines=4)

def batch(s):
    panel(s,'r100_list_panel',(40,102,1264,856));panel(s,'r100_side_panel',(1324,102,556,856),True)
    s.label('r100_batch_col1',(88,132,850,60),34,MUTED);s.label('r100_batch_col2',(1048,132,220,60),34,MUTED)
    line(s,'r100_batch_head_line',64,190,1216)
    for i in range(8):
        y=202+i*87
        s.pic('r100_focus_'+str(i),(50,y,1244,85),art('row-focus'),z=3)
        s.label('result_name_'+str(i),(88,y+8,906,68),36)
        for suffix,color in (('',CYAN),('_green','#62EF91'),('_yellow','#FFD239'),('_red','#FF8497')):
            s.label('result_status_'+str(i)+suffix,(1048,y+10,222,64),30,color,align='center',lines=2)
        for state in ('green','yellow','red'):s.pic('r100_batch_state_{}_{}'.format(i,state),(1040,y+11,238,60),state_art(state),z=4)
        line(s,'r100_batch_line_'+str(i),64,y+86,1216)
    s.label('r100_side_caption',(1364,134,476,64),38,CYAN)
    line(s,'r100_side_divider',1364,202,476)
    s.pic('r100_side_icon',(1518,260,160,160),art('server-check'))
    s.label('r100_batch_percent',(1364,438,476,100),84,align='center')
    s.label('r100_batch_done',(1364,540,476,62),34,MUTED,align='center')
    s.label('progress_track',(1364,614,476,24),background='#203B5D',z=4);s.label('progress_fill',(1364,614,476,24),background=CYAN,z=5)
    line(s,'r100_side_line2',1364,674,476)
    s.label('r100_batch_account_caption',(1364,700,476,46),29,MUTED)
    s.label('r100_batch_account',(1364,748,476,96),34,lines=3)
    s.label('r100_batch_state_caption',(1364,824,476,34),25,MUTED)
    s.label('r100_batch_state',(1364,848,476,60),32,CYAN,lines=2)
    s.label('r100_batch_hint',(1364,912,476,36),25,MUTED)

def health(s):
    s.label('account',(64,92,1776,44),30,MUTED)
    panel(s,'r100_health_outer',(38,138,1842,796))
    s.pic('r100_health_status_panel',(64,162,408,620),art('health-status'))
    for state,icon in (('neutral','neutral'),('green','ok'),('yellow','busy'),('red','error')):
        s.pic('r100_health_icon_'+state,(178,210,180,180),art(icon))
        s.pic('r100_health_badge_'+state,(110,478,314,60),state_art(state),z=3)
    for suffix,color in (('',MUTED),('_green','#2AE67C'),('_yellow','#FFD239'),('_red','#FF8497')):
        s.label('status'+suffix,(88,400,360,68),50,color,align='center',lines=2)
    s.label('status_badge',(124,483,286,50),32,align='center')
    badge(s,'r100_latency_panel',(104,592,328,152))
    s.label('r100_latency_caption',(122,610,292,44),29,MUTED,align='center')
    s.label('r100_latency',(122,660,292,64),52,align='center')
    for side,x,w in (('left',492,720),('right',1234,622)):
        s.pic('r100_health_panel_'+side,(x,162,w,520),art('health-info'))
        s.label('r100_health_heading_'+side,(x+40,184,w-80,64),36,CYAN)
        line(s,'r100_health_header_'+side,x+40,236,w-80)
        for i in range(4):
            y=274+i*90;cw=315 if side=='left' else 298;vw=w-cw-80
            s.label('r100_hc_{}_{}'.format(side,i),(x+40,y,cw,70),30,MUTED,lines=2)
            s.label('r100_hv_{}_{}'.format(side,i),(x+cw+40,y,vw,70),30,lines=2)
            if i<3:line(s,'r100_hl_{}_{}'.format(side,i),x+40,y+78,w-80)
    s.label('details_left',(532,262,640,400),29,lines=13);s.label('details_right',(1274,262,542,400),29,lines=13)
    s.pic('r100_api_panel',(492,704,1364,80),art('strip'))
    s.pic('r100_api_icon',(980,723,42,42),art('ok'))
    s.label('details',(1040,713,782,64),29,MUTED,lines=2)
    s.pic('r100_legend_panel',(64,812,1792,100),art('strip'))
    for i,color in enumerate(('green','yellow','red')):
        s.pic('r100_legend_dot_'+color,(210+i*560,840,42,42),plugin_path('skin','images','utility-r96','dot-'+color+'.png'))
        s.label('legend_'+color,(284+i*560,829,450,64),30)

def media(s,favorites=False):
    if favorites:
        panel(s,'r100_media_panel',(42,234,1284,700));panel(s,'r100_side_panel',(1338,140,548,794),True)
        for i in range(4):
            x=42+i*322
            s.pic('r100_tab_idle_'+str(i),(x,140,310,76),art('badge'),z=2)
            s.pic('r100_tab_'+str(i),(x,140,310,76),art('tab-focus'),z=3)
            s.label('favorite_filter_'+str(i),(x+8,143,294,68),32,align='center')
        count=6
    else:panel(s,'r100_side_panel',(1376,174,504,734),True);count=3
    for i in range(count):
        x=54+(i%3)*430 if favorites else 44+i*444;y=250+(i//3)*320 if favorites else 174
        cw,ch=(414,300) if favorites else (432,734)
        s.pic('continue_card_'+str(i),(x,y,cw,ch),art('favorite-card' if favorites else 'media-card'))
        s.pic('r100_media_focus_'+str(i),(x,y,cw,ch),art('favorite-focus' if favorites else 'media-focus'),z=7)
        poster=(x+12,y+10,cw-24,220) if favorites else (x+16,y+16,cw-32,494)
        s.label('continue_poster_placeholder_'+str(i),poster,28,MUTED,background='#020A14',z=3,align='center',lines=3)
        s.pic('continue_poster_'+str(i),poster,z=4)
        badge(s,'r100_type_bg_'+str(i),(x+16,y+18,152,52))
        s.label('continue_type_'+str(i),(x+20,y+20,144,48),25,CYAN,z=6,align='center')
        s.label('continue_title_'+str(i),(x+18,y+238 if favorites else y+518,cw-36,56),31 if favorites else 38,lines=1)
        if not favorites:
            s.label('continue_meta_'+str(i),(x+22,y+590,cw-44,44),30,MUTED)
            s.label('continue_progress_bg_'+str(i),(x+24,y+650,cw-48,18),background='#203B5D',z=4)
            s.label('continue_progress_fill_'+str(i),(x+24,y+650,cw-48,18),background=CYAN,z=5)
            s.label('r100_percent_'+str(i),(x+24,y+679,110,42),29)
            s.label('r100_remaining_'+str(i),(x+140,y+679,cw-164,42),29,align='right')
        else:
            for edge,box in (('top',(x,y,cw,4)),('bottom',(x,y+ch-4,cw,4)),('left',(x,y,4,ch)),('right',(x+cw-4,y,4,ch))):
                s.label('continue_move_{}_{}'.format(i,edge),box,background='#FFD239',z=8)
    rx=1372 if favorites else 1412;rw=480 if favorites else 432
    s.label('r100_side_caption',(rx,158 if favorites else 194,rw,64),34,CYAN)
    line(s,'r100_side_divider',rx,222 if favorites else 262,rw)
    if favorites:
        s.label('r100_logo_placeholder',(1380,254,464,248),45,CYAN,background='#020A14',align='center')
        s.pic('r100_side_logo',(1380,254,464,248),z=4)
        s.label('r100_side_title',(1372,516,480,80),44,lines=2)
        s.label('r100_side_info',(1372,610,480,60),32,CYAN)
        line(s,'r100_side_line2',1372,672,480)
        s.label('r100_side_label',(1372,700,480,44),29,MUTED)
        s.label('r100_side_value',(1372,754,480,64),34,lines=2)
        badge(s,'r100_open_hint_bg',(1372,842,480,68));s.label('r100_open_hint',(1372,848,480,56),32,align='center')
    else:
        s.pic('r100_side_icon',(1540,326,160,160),art('play'))
        s.label('r100_side_title',(1412,500,432,94),42,lines=2)
        s.label('r100_side_info',(1412,606,432,60),32,MUTED,lines=2)
        s.label('r100_side_value',(1412,700,432,80),50,lines=2)
        s.label('r100_resume_bg',(1412,776,432,18),background='#203B5D',z=4)
        s.label('r100_resume_fill',(1412,776,432,18),background=CYAN,z=5)
        s.label('r100_resume_percent',(1412,808,432,38),29)
        s.label('r100_resume_hint',(1412,854,432,44),27,MUTED,lines=2)
    s.label('message',(64,932,1230,34),23,MUTED)

def restyle(markup,kind):
    old=ET.fromstring(markup);w,h=map(int,old.get('size').split(','));px=lambda n:max(1,round(n*min(w/1920.,h/1080.)))
    s=NativeSkin(old.get('name'),w,h,px)
    s.label('r100_gt',(60,14,78,72),60,CYAN);s.label('r100_brand',(150,18,310,68),40)
    s.label('r100_header_divider',(470,26,2,52),background='#3F9CBD')
    s.label('r100_title',(506,16,1364,76),50)
    if kind!='batch':s.label('subtitle',(506 if kind in ('mapping','favorites') else 64,86,1300,48),28,MUTED)
    s.label('r100_counter',(1450,84 if kind!='favorites' else 18,430,48),28,MUTED,align='right')
    s.label('r100_web_hint',(1430,1010,410,44) if kind=='batch' else (1430,936,450,30),20,MUTED,align='right',z=50)
    s.pic('r100_footer_panel',(36,978,1848,88),art('strip'),z=29)
    s.label('r100_footer_line',(36,972,1848,2),background='#244E76',z=30)
    s.label('footer',(0,976,1920,104),20,z=30)
    for i in range(7):s.label('r100_footer_sep_'+str(i),(0,0,1,1),background='#315774',z=30)
    if kind in ('xmltv','automation'):settings(s,kind=='automation')
    elif kind=='mapping':mapping(s)
    elif kind=='batch':batch(s)
    elif kind=='health':health(s)
    else:media(s,kind=='favorites')
    root=ET.fromstring(s.finish());nodes={n.get('name'):n for n in root}
    for n in old:
        name=n.get('name','')
        if name in nodes:continue
        if not name.startswith('remote_'):
            n.set('position','0,0');n.set('size','1,1');n.set('zPosition','-40');n.set('transparent','1')
            for attr in ('pixmap','pixmaps','backgroundColor'):n.attrib.pop(attr,None)
        root.append(n)
    for n in root:
        name=n.get('name','')
        if name in ('r100_gt','r100_brand','r100_title','r100_side_caption','r100_side_title','r100_batch_percent','r100_latency','r100_side_value','status_green','status_yellow','status_red') or name.startswith(('r100_map_name_','result_name_','continue_title_','r100_health_heading_')):
            n.set('borderWidth',str(px(1)));n.set('borderColor',n.get('foregroundColor',INK))
    return ET.tostring(root,encoding='unicode')

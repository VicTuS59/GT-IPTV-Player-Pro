# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native component geometry for the five approved broadcast/source previews."""
from xml.etree import ElementTree as ET
from .paths import plugin_path
from .utility_design import NativeSkin

INK='#EDF2F8';MUTED='#AFC1D8';CYAN='#20D5ED'
CATEGORY_PAGE_SIZE = 8
CATEGORY_ROW_HEIGHT = 84
CATEGORY_LIST_BOX = (54, 186, 900, CATEGORY_PAGE_SIZE * CATEGORY_ROW_HEIGHT)
CATEGORY_ROW_ART_BOX = (54, 186, 900, 78)
CATEGORY_PANEL_BOX = (38, 108, 933, 816)
CATEGORY_ICON_BOX = (161, 199, 52, 52)
def art(n):return plugin_path('skin','images','broadcast-r101',n+'.png')
def panel(s,n,box,narrow=False):s.pic(n,box,art('narrow-panel' if narrow else 'wide-panel'),z=1)
def line(s,n,box):s.label(n,box,background='#225477',z=3)
def badge(s,n,box):s.pic(n,box,plugin_path('skin','images','seven-r100','badge.png'),z=3)

def playlists(s):
    for i in range(12):
        x=46+(i//6)*681;y=146+(i%6)*129;cw=660 if i<6 else 620
        s.pic('r101_row_'+str(i),(x,y,cw,116),art('file-row'))
        s.pic('r101_focus_'+str(i),(x,y,cw,116),art('file-focus'),z=3)
        s.label('row_number_'+str(i),(x+24,y+28,58,58),30,CYAN)
        for kind in ('file','plus'):s.pic('r101_file_{}_{}'.format(kind,i),(x+92,y+29,62,62),art(kind),z=5)
        s.label('row_name_'+str(i),(x+178,y+14,cw-204,48),28,lines=1)
        s.label('row_path_'+str(i),(x+178,y+64,cw-204,38),23,MUTED)
        s.pic('r101_status_'+str(i),(x+cw-110,y+39,90,42),plugin_path('skin','images','accounts-r99','status-green.png'),z=5)
        s.pic('r101_status_red_'+str(i),(x+cw-110,y+39,90,42),plugin_path('skin','images','accounts-r99','status-red.png'),z=5)
        s.label('row_status_'+str(i),(x+cw-108,y+39,86,42),23,'#55EF91',align='center',z=6)
        s.label('r101_status_error_'+str(i),(x+cw-108,y+39,86,42),23,'#FF8497',align='center',z=6)
    for i,box in enumerate(((1454,50,216,54),(1680,50,202,54))):
        badge(s,'r101_count_bg_'+str(i),box);s.label('r101_count_'+str(i),(box[0]+6,box[1],box[2]-12,box[3]),27,align='center')
    panel(s,'r101_side_panel',(1362,146,520,761),True)
    s.label('r101_side_caption',(1394,164,458,76),32,CYAN,lines=2)
    line(s,'r101_side_line',(1394,244,458,2))
    s.pic('r101_file_icon',(1404,282,126,126),art('file'),z=5)
    s.label('r101_side_title',(1554,284,290,62),39,lines=2)
    for state in ('green','neutral','red'):s.pic('r101_side_badge_'+state,(1554,350,190,54),plugin_path('skin','images','accounts-r99','status-'+state+'.png'),z=4)
    for state,color in (('green','#55EF91'),('neutral',CYAN),('red','#FF8497')):
        s.label('r101_side_status_'+state,(1560,352,178,50),30,color,align='center',lines=2)
    line(s,'r101_side_line2',(1394,438,458,2))
    s.label('r101_source_caption',(1394,466,458,44),28,MUTED)
    s.label('r101_source_value',(1394,518,458,56),34)
    line(s,'r101_side_line3',(1394,590,458,2))
    s.label('r101_path_caption',(1394,616,458,46),28,MUTED)
    s.label('r101_path_value',(1394,670,458,142),31,lines=3)
    s.label('message',(64,917,1290,44),24,MUTED)

def categories(s):
    panel(s,'r101_categories_panel',CATEGORY_PANEL_BOX);panel(s,'r101_weather_panel',(989,108,895,816))
    s.label('categories_caption',(68,124,862,58),38,CYAN)
    row_x, row_y, row_width, art_height = CATEGORY_ROW_ART_BOX
    for i in range(CATEGORY_PAGE_SIZE):
        box = (row_x, row_y + i*CATEGORY_ROW_HEIGHT, row_width, art_height)
        s.pic('category_row_art_'+str(i),box,art('category-row'))
        s.pic('category_focus_art_'+str(i),box,art('category-focus'),z=3)
    # Keep the existing text boxes available for fitting names once per catalogue.
    s.label('r101_category_measure_number',(78,196,68,58),30,MUTED)
    s.label('r101_category_measure_name',(252,191,666,68),33)
    x, y, width, _height = s.rect(CATEGORY_LIST_BOX)
    row_height = s.rect((0,0,1,CATEGORY_ROW_HEIGHT))[3]
    s.parts.append('<widget name="r101_category_list" position="{},{}" size="{},{}" '
                   'zPosition="5" transparent="1" selectionDisabled="1" '
                   'scrollbarMode="showNever" scrollbarScroll="byPage" />'.format(
                       x, y, width, CATEGORY_PAGE_SIZE * row_height))
    s.label('message',(70,872,864,42),25,MUTED,align='right')
    s.label('items',(72,202,830,100),28,MUTED,lines=3)
    s.label('list_scroll_track',(959,190,5,665),background='#214663');s.label('list_scroll_thumb',(959,190,5,100),background=CYAN,z=6)
    s.label('weather_caption',(1020,124,834,58),36,CYAN)
    line(s,'weather_header_divider',(1014,187,846,2))
    s.label('weather_city',(1030,204,810,68),38,CYAN)
    s.pic('weather_current_icon',(1064,318,270,198),z=5)
    s.label('weather_today_label',(1395,290,448,62),30,MUTED)
    s.label('weather_temp',(1390,352,458,128),96)
    s.label('weather_desc',(1390,482,458,66),34,CYAN,lines=2)
    line(s,'weather_forecast_divider',(1014,566,846,2))
    for i in range(5):
        x=1017+i*168
        if i:line(s,'weather_day_separator_'+str(i),(x-4,596,1,228))
        s.label('weather_day_'+str(i),(x,584,160,56),29,align='center')
        s.pic('weather_day_icon_'+str(i),(x+24,652,112,90),z=5)
        s.label('weather_day_high_'+str(i),(x,746,160,56),36,align='center')
        s.label('weather_day_low_'+str(i),(x,800,160,42),29,CYAN,align='center')
    s.label('weather_metrics',(1030,850,810,36),24,MUTED,align='center')
    s.label('weather_updated',(1030,889,810,30),22,MUTED,align='center')

def live_geometry(width,height,px):
    def box(b):return (round(b[0]*width/1920.),round(b[1]*height/1080.),max(1,round(b[2]*width/1920.)),max(1,round(b[3]*height/1080.)))
    values={'left_panel':(24,108,462,840),'list_panel':(498,108,910,840),'right_panel':(1420,108,476,840),'list_scrollbar':(1398,182,5,742),'preview':(1436,128,444,250),'preview_frame':(1430,122,456,262),'poster':(1456,398,404,536),'summary':(56,438,398,140),'summary_pagination':(400,578,40,24),'summary_scrollbar':(467,438,4,140)}
    return {n:box(b) for n,b in values.items()}

def live(s):
    for n,box in (('left',(24,108,462,840)),('center',(498,108,910,840)),('right',(1420,108,476,840))):panel(s,'r101_'+n+'_panel',box,True)
    s.label('r101_epg_caption',(54,124,400,55),36,CYAN)
    line(s,'r101_epg_line',(50,182,408,2))
    s.label('ribbon_now_caption',(56,202,398,38),29,CYAN)
    s.label('ribbon_now_title',(56,252,398,68),36,lines=2)
    s.label('ribbon_now_time',(56,328,290,36),26,MUTED)
    s.label('epg_progress',(352,328,98,36),26,MUTED,align='right')
    s.label('epg_progress_bg',(56,380,398,12),background='#243D59',z=4);s.label('epg_progress_fill',(56,380,398,12),background=CYAN,z=5)
    s.label('summary_caption',(56,404,398,32),27,CYAN)
    s.label('summary',(56,438,398,140),23,MUTED,lines=5)
    s.label('summary_scroll',(400,578,40,24),17,MUTED,align='right')
    s.label('summary_scrollbar_track',(467,438,4,140),background='#243D59');s.label('summary_scrollbar_thumb',(467,438,4,32),background=CYAN,z=6)
    line(s,'summary_separator',(52,608,408,2))
    s.label('epg_title',(56,624,398,42),30,CYAN)
    for i,n in enumerate(('next','third','fourth','fifth')):
        y=688+i*60
        s.label('epg_'+n+'_time',(56,y,96,62),24,MUTED)
        s.label('epg_'+n+'_title',(161,y,288,62),24,lines=2)
    s.label('list_caption',(522,124,600,54),36,CYAN)
    s.label('message',(1270,128,116,46),27,MUTED,align='right')
    line(s,'list_caption_accent',(520,182,864,2))
    row_x, row_width = 510, 882
    picon_x, picon_width = 612, 106
    favorite_size, favorite_inset = 25, 3
    favorite_x = picon_x + picon_width - favorite_size - favorite_inset
    name_x = 740
    name_width = row_x + row_width - name_x - 14
    favorite_star = art('favorite-picon-star')
    move_mark = plugin_path('skin', 'images', 'category-check-tick-r83.png')
    for i in range(10):
        y=184+i*74
        picon_y = y + 5
        s.pic('r101_live_row_'+str(i),(row_x,y,row_width,72),art('stream-row'))
        s.pic('r101_live_focus_'+str(i),(row_x,y,row_width,72),art('stream-focus'),z=3)
        s.label('stream_number_'+str(i),(526,y+6,78,60),26,MUTED)
        s.pic('stream_move_mark_'+str(i),(526,y+18,36,36),move_mark,z=7)
        s.pic('stream_picon_'+str(i),(picon_x,picon_y,picon_width,62),z=5)
        s.label('stream_name_'+str(i),(name_x,y+5,name_width,62),30,lines=2)
        s.pic('stream_favorite_'+str(i),
              (favorite_x,picon_y+favorite_inset,favorite_size,favorite_size),favorite_star,z=7)
    s.label('list_scroll_track',(1398,184,5,740),background='#243D59');s.label('list_scroll_thumb',(1398,184,5,100),background=CYAN,z=6)
    s.pic('preview_frame',(1430,122,456,262),art('file-row'),z=3)
    s.parts.append('<widget name="preview_video" position="{},{}" size="{},{}" backgroundColor="transparent" zPosition="5"/>'.format(*s.rect((1436,128,444,250))));s.names.add('preview_video')
    s.label('preview_mask',(1436,128,444,250),background='#020508',z=7)
    s.label('preview_hint',(1436,215,444,62),27,MUTED,align='center',z=9)
    s.pic('detail_programme_poster',(1456,398,404,536),z=7)
    s.label('clock_time',(1695,12,180,46),35,align='right')
    s.label('clock_date',(1330,62,546,34),24,MUTED,align='right')

def archive(s):
    panel(s,'r101_channels_panel',(24,104,490,844),True);panel(s,'r101_programmes_panel',(528,104,864,844));panel(s,'r101_detail_panel',(1408,104,488,844),True)
    s.label('account',(1255,14,438,66),29,MUTED,align='right');s.label('clock',(1765,14,108,66),42,align='right')
    s.label('channels_heading',(56,121,400,58),35,CYAN)
    for i in range(12):
        y=183+i*59
        s.pic('r101_channel_row_'+str(i),(41,y,453,55),art('category-row'))
        s.pic('r101_channel_selected_'+str(i),(41,y,453,55),art('selection'),z=3)
        s.pic('r101_channel_focus_'+str(i),(41,y,453,55),art('file-focus'),z=4)
        s.pic('r101_channel_icon_'+str(i),(57,y+10,37,37),art('tv'),z=5)
        s.label('channel_'+str(i),(128,y+1,346,52),27)
    s.label('channel_count',(329,902,118,42),26,MUTED,align='right')
    for p,box in (('channel_scroll_up',(458,126,32,32)),('channel_scroll_down',(470,909,28,28)),('programme_scroll_up',(1356,277,28,28)),('programme_scroll_down',(1356,876,28,28))):
        s.label(p,box,24,CYAN,align='center');s.label(p+'_disabled',box,24,'#284A63',align='center')
    for i in range(4):
        box=(554+i*204,124,194,58)
        s.pic('r101_day_idle_'+str(i),box,art('tab-idle'))
        s.pic('r101_day_selected_'+str(i),box,art('tab-active'),z=3)
        s.pic('r101_day_focus_'+str(i),box,art('file-focus'),z=4)
        s.label('day_'+str(i),(box[0]+8,128,box[2]-16,50),29,align='center')
    s.label('day_caption',(556,202,692,72),31,lines=2)
    s.label('day_count',(1290,212,78,42),26,MUTED,align='right')
    s.label('r101_time_caption',(578,277,130,50),29,MUTED);s.label('r101_program_caption',(722,277,450,50),29,MUTED);s.label('r101_duration_caption',(1242,277,108,50),29,MUTED,align='right')
    for i in range(5):
        y=332+i*78
        s.pic('r101_programme_row_'+str(i),(554,y,810,76),art('archive-row'))
        s.pic('r101_programme_selected_'+str(i),(554,y,810,76),art('selection'),z=3)
        s.pic('r101_programme_focus_'+str(i),(554,y,810,76),art('file-focus'),z=4)
        s.label('programme_time_'+str(i),(578,y+7,126,62),30)
        s.label('programme_'+str(i),(722,y+7,470,62),30)
        s.label('programme_duration_'+str(i),(1220,y+7,130,62),27,align='right')
    s.label('programme_count',(1242,874,108,44),26,MUTED,align='right')
    s.label('r101_detail_caption',(1440,121,426,58),34,CYAN)
    line(s,'r101_archive_detail_line',(1438,181,434,2))
    s.pic('r101_archive_icon',(1560,240,176,176),art('archive'),z=5)
    s.label('detail_title',(1448,448,410,92),48,lines=2)
    s.label('r101_detail_time',(1448,538,410,58),34)
    s.label('r101_detail_duration',(1448,597,410,46),30)
    s.label('r101_archive_watch',(1448,649,410,52),31,CYAN)
    line(s,'r101_archive_detail_line2',(1438,710,434,2))
    s.label('detail_description',(1448,735,410,180),28,MUTED,lines=6)
    s.label('message',(560,761,800,100),27,MUTED,lines=4)

def restyle(markup,kind):
    old=ET.fromstring(markup);w,h=map(int,old.get('size').split(','));px=lambda v:max(1,round(v*min(w/1920.,h/1080.)))
    s=NativeSkin(old.get('name'),w,h,px)
    s.label('r101_gt',(60,14,82,72),60,CYAN);s.label('r101_brand',(150,18,350,68),40)
    line(s,'r101_header_divider',(510,26,2,52));s.label('r101_title',(542,16,650 if kind=='archive' else 840 if kind=='playlists' else 630 if kind=='live' else 1120,76),48)
    if kind=='playlists':s.label('subtitle',(506,88,840,45),28,MUTED)
    s.pic('r101_footer_panel',(24,978,1872,88),art('footer'),z=29)
    s.label('footer',(0,976,1920,104),20,z=30)
    s.label('r101_web_hint',(1410,945,470,28),20,MUTED,align='right',z=20)
    for i in range(7):s.label('r100_footer_sep_'+str(i),(0,0,1,1),background='#315774',z=30)
    {'playlists':playlists,'categories':categories,'live':live,'archive':archive}[kind](s)
    root=ET.fromstring(s.finish());names={n.get('name') for n in root}
    for n in old:
        name=n.get('name','')
        if name in names:continue
        if kind == 'live' and name and not name.startswith('remote_'):
            # These legacy components remain available to the controller.
            # The live skin only needs native instances for its visible widgets.
            continue
        if not name.startswith('remote_'):
            n.set('position','0,0');n.set('size','1,1');n.set('zPosition','-40');n.set('transparent','1')
            for attr in ('pixmap','pixmaps','backgroundColor'):n.attrib.pop(attr,None)
        root.append(n)
    for n in root:
        name=n.get('name','')
        if kind=='live' and name=='summary':
            # The controller supplies measured lines and a scrolling window.
            n.set('valign','top');n.set('noWrap','1')
        if name.startswith(('r101_gt','r101_brand','r101_title','r101_side_title','r101_side_caption','r101_right_channel','r101_left_channel','row_name_')) or name in ('categories_caption','weather_caption','weather_temp','weather_city','list_caption','detail_title','channels_heading','r101_epg_caption','r101_selected_caption','ribbon_now_title','ribbon_now_caption','epg_title','r101_detail_caption'):
            n.set('borderWidth',str(px(1)));n.set('borderColor',n.get('foregroundColor',INK))
    return ET.tostring(root,encoding='unicode')

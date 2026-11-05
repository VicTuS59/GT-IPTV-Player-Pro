# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Responsive native matches theme. All coordinates follow the OSD desktop."""
from .paths import plugin_path
from .typography import visible_count
from .utility_design import NativeSkin, art

LIST_RECT = (48,264,1240,608)
ROWS_RECT = (60,322,1216,552)
SCROLL_RECT = (1291,322,5,552)


def rows():
    return max(4, visible_count(4))






def scroll_geometry(width,height):
    x,y,w,h=SCROLL_RECT
    return (round(x*width/1920.),round(y*height/1080.),max(1,round(w*width/1920.)),round(h*height/1080.))


def skin(width,height,px):
    s=NativeSkin('GTMatchesScreen',width,height,px)
    empty=art('ball');key_frame=plugin_path('skin','images','discovery-r48','key-outline-r48.png')
    s.pic('matches_bg',(0,0,1,1),z=-40)
    s.label('r96_brand_gt',(60,24,70,66),46,'#20D5ED')
    s.label('brand',(137,24,825,66),44)
    s.label('title',(990,24,560,66),37,'#A9BBD2')
    s.label('clock',(1600,24,250,66),44,align='right')
    s.label('r96_header_line',(40,103,1840,2),background='#29435E')
    s.label('r96_header_divider',(952,38,2,38),background='#29435E')
    for i in range(4):
        left=60+i*452
        s.pic('r96_tab_bg_'+str(i),(left,125,438,72),art('tab-idle'))
        s.pic('tab_focus_'+str(i),(left,125,438,72),art('tab-active'),z=3)
        s.label('tab_'+str(i),(left+10,130,418,60),36,align='center')
    s.label('filter',(64,210,1210,46),29)
    s.label('count',(1310,210,540,46),28,'#A9BBD2',align='right')
    s.pic('r96_list_panel',(48,264,1240,610),art('detail-panel'),z=1)
    s.pic('r96_detail_panel',(1304,264,552,690),art('detail-panel'),z=1)
    for name,left,w in (('league',84,220),('match',316,612),('score',945,160),('status',1124,138)):
        s.label('column_'+name,(left,276,w,44),28,'#A9BBD2',align='center' if name in ('score','status') else 'left')
    row_h=ROWS_RECT[3]/float(rows())
    for i in range(rows()):
        top=ROWS_RECT[1]+i*row_h
        s.pic('r96_match_row_'+str(i),(60,top,1216,row_h-3),z=2)
        s.pic('row_focus_'+str(i),(60,top,1216,row_h-3),art('tab-active'),z=2)
        s.label('r96_row_line_'+str(i),(62,top+row_h-1,1212,1),background='#20364A',z=2)
        s.label('row_{}_league'.format(i),(84,top+6,216,row_h-13),27,'#B7C9E0',lines=2)
        for side,offset in (('home',0),('away',row_h/2)):
            s.pic('row_{}_{}_logo'.format(i,side),(316,top+offset+8,46,46),z=4)
            s.pic('row_{}_{}_placeholder'.format(i,side),(316,top+offset+8,46,46),empty,z=3)
            s.label('row_{}_{}'.format(i,side),(380,top+offset+2,520,row_h/2-5),29,lines=2)
        s.label('row_{}_separator'.format(i),(0,0,1,1),z=-40)
        s.label('row_{}_score'.format(i),(942,top+7,160,row_h-14),41,align='center')
        s.pic('row_{}_live'.format(i),(1123,top+(row_h-13)/2,13,13),art('dot-green'),z=4)
        s.label('row_{}_status'.format(i),(1147,top+7,112,row_h-14),28,'#D6F5EB',align='center',lines=2)
        s.label('row_{}_warning_status'.format(i),(1137,top+7,130,row_h-14),28,'#FFD239',align='center',lines=2)
    s.label('scroll_track',(1291,322,5,552),background='#18334D',z=3)
    s.label('scroll_thumb',(1291,322,5,80),background='#20D5ED',z=4)
    s.pic('empty_icon',(580,460,100,100),empty,z=3)
    s.label('empty_message',(225,575,810,110),31,'#A9BBD2',align='center',lines=2)
    s.label('position',(64,879,240,34),23,'#A9BBD2')
    s.label('detail_header',(1336,278,488,62),33,'#20D5ED',lines=2)
    s.label('detail_league',(1336,344,488,68),28,'#B7C9E0',lines=2)
    for side,left in (('home',1350),('away',1720)):
        s.pic(side+'_logo',(left,440,110,110),z=4);s.pic(side+'_placeholder',(left,440,110,110),empty,z=3)
    s.label('detail_score',(1467,444,242,124),92,align='center')
    s.label('detail_home',(1328,579,245,92),28,align='center',lines=2)
    s.label('detail_away',(1597,579,245,92),28,align='center',lines=2)
    s.label('detail_phase',(1336,699,488,72),30,'#63F3B7',align='center',lines=2)
    s.label('r96_detail_rule',(1336,788,488,1),background='#29435E')
    s.label('detail_half_label',(1336,798,300,48),27,'#B7C9E0',lines=2)
    s.label('detail_half_score',(1669,798,155,48),30,align='right')
    s.label('detail_state_label',(1336,856,220,48),27,'#B7C9E0',lines=2)
    s.label('detail_state',(1570,856,254,48),27,align='right',lines=2)
    s.label('source',(1336,916,488,30),22,'#A9BBD2',align='right')
    s.label('tv_country',(1336,343,315,44),27,'#B7C9E0')
    s.label('tv_count',(1660,343,164,44),25,'#B7C9E0',align='right')
    s.label('tv_column_channels',(950,276,312,44),27,'#B7C9E0')
    for i in range(rows()):
        top=ROWS_RECT[1]+i*row_h
        s.label('tv_row_channels_'+str(i),(950,top+8,312,row_h-16),27,lines=2)
    for i in range(10):
        top=395+i*48
        s.label('tv_number_'+str(i),(1336,top,48,44),23,'#A9BBD2')
        s.label('tv_channel_'+str(i),(1395,top,429,44),27)
    s.label('tv_page',(1610,916,214,30),22,'#A9BBD2',align='right')
    s.label('tv_source',(1336,916,275,30),22,'#A9BBD2')
    s.label('message',(64,915,1220,32),23,'#20D5ED')
    s.label('updated',(64,952,1060,30),22,'#A9BBD2')
    s.label('navigation',(1140,952,708,30),20,'#A9BBD2',align='right')
    s.label('r96_footer_line',(40,988,1840,2),background='#29435E',z=30)
    for key,left in (('lr',64),('ud',335)):
        s.pic('r96_key_frame_'+key,(left,1010,80,43),key_frame,z=31)
        s.label('r96_key_'+key,(left,1010,80,43),24,align='center',z=32)
        s.label('r96_action_'+key,(left+97,1008,160,47),25,z=32)
    for i,key in enumerate(('red','green','yellow','blue')):
        left=620+i*298
        s.pic('key_'+key,(left,1018,24,24),art('dot-'+key),z=32)
        s.label('action_'+key,(left+40,1008,235,47),25,z=32,lines=2)
    for key,left in (('red',620),('green',1015),('yellow',1410)):
        s.pic('tv_key_'+key,(left,1018,24,24),art('dot-'+key),z=32)
        s.label('tv_action_'+key,(left+40,1008,355,47),25,z=32,lines=2)
    return s.finish()

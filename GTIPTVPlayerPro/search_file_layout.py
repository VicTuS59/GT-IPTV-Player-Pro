# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared native movie/series search and TXT picker geometry."""
from xml.etree import ElementTree as ET
from .paths import plugin_path
from .utility_design import NativeSkin

CYAN='#20D5ED'
MUTED='#AFC1D8'

def art(name):
    return plugin_path('skin','images','screens-r102',name+'.png')

def search_scroll_geometry(width,height):
    return (round(1138*width/1920.),round(275*height/1080.),max(2,round(8*width/1920.)),round(640*height/1080.))

def file_list_geometry(width,height):
    # The original FileList remains the navigation model underneath PNG rows.
    top=round(282*height/1080.)
    row=max(1,round(56*height/1080.))
    return (round(58*width/1920.),top,round(1216*width/1920.),row*11),row

def header(s,title_y=10):
    s.label('r102_gt',(42,title_y,82,66),60,CYAN)
    s.label('r102_brand',(134,title_y+4,356,62),40)
    s.label('r102_header_divider',(500,title_y+7,2,58),background='#227091')
    s.label('r102_title',(526,title_y,1010,72),48)
    s.pic('r102_footer_panel',(24,978,1872,88),art('footer'),z=29)
    s.label('footer',(0,976,1920,104),20,z=30)
    s.label('r102_web_hint',(1390,946,490,28),20,MUTED,align='right',z=20)
    for i in range(7):s.label('r100_footer_sep_'+str(i),(0,0,1,1),background='#315774',z=30)

def search(s):
    header(s)
    s.label('server',(1550,18,330,52),26,MUTED,align='right')
    s.pic('r102_query_panel',(42,80,1836,72),art('search-bar'))
    s.pic('r102_search_icon',(66,96,40,40),art('search'),z=5)
    s.label('query_caption',(148,90,148,52),28,MUTED)
    s.label('r102_query_divider',(306,94,2,44),background=CYAN)
    s.label('query_text',(346,88,1486,56),36)
    s.pic('r102_result_panel',(42,165,1116,780),art('search-list'))
    s.pic('r102_detail_panel',(1170,165,710,780),art('search-detail'))
    s.label('r102_results_caption',(66,180,920,58),42,CYAN)
    s.label('result_caption',(66,238,900,36),27,MUTED)
    s.label('r102_position',(996,189,130,48),28,MUTED,align='right')
    for i in range(10):
        y=275+i*64
        s.pic('r102_result_focus_'+str(i),(54,y,1072,64),art('search-focus'),z=3)
        s.label('r102_result_number_'+str(i),(78,y+5,106,54),28,MUTED)
        s.label('catalog_result_'+str(i),(200,y+4,784,56),32)
        s.pic('r102_result_favorite_'+str(i),(988,y+21,23,23),plugin_path('skin','images','favorite-star-v15.png'),z=6)
        s.label('r102_result_year_'+str(i),(1024,y+5,82,54),29,MUTED,align='right')
        s.label('r102_result_line_'+str(i),(64,y+63,1052,1),background='#225477',z=2)
    s.label('results_scroll_track',(1138,275,8,640),background='#243D59')
    s.label('results_scroll_thumb',(1138,275,8,120),background=CYAN,z=6)
    s.label('message',(76,374,1032,190),31,MUTED,lines=4)
    s.label('r102_detail_caption',(1196,180,650,58),40,CYAN)
    s.label('r102_poster_frame',(1196,236,362,482),background='#1A7189',z=3)
    s.label('r102_poster_back',(1198,238,358,478),background='#031321',z=4)
    s.pic('r102_poster_empty',(1308,401,138,138),art('no-poster'),z=5)
    s.pic('r102_search_poster',(1198,238,358,478),z=6)
    s.pic('r102_rating_star',(1586,256,42,42),plugin_path('skin','images','favorite-star-v15.png'),z=6)
    s.label('detail_rating',(1640,244,94,66),38,'#FFD239')
    s.label('detail_meta',(1744,249,108,112),30,MUTED,align='right',lines=3)
    s.label('detail_category',(1584,374,258,150),26,MUTED,lines=4)
    s.label('detail_title',(1196,728,652,68),36,lines=2)
    s.label('detail_plot_caption',(1196,802,652,38),29,CYAN)
    s.label('detail_plot',(1196,848,652,58),27,lines=2)
    s.label('status',(1196,911,652,30),22,MUTED)

def files(s):
    header(s,18)
    s.label('instruction',(526,94,1330,42),26,MUTED)
    s.pic('r102_files_panel',(42,144,1248,762),art('files-list'))
    s.pic('r102_file_detail_panel',(1304,144,576,762),art('files-detail'))
    s.pic('r102_path_panel',(58,158,1216,72),art('path-bar'),z=3)
    s.pic('r102_path_icon',(82,170,48,48),art('folder-outline'),z=5)
    s.label('r102_path_caption',(166,158,1070,32),25,MUTED)
    s.label('path',(166,190,1070,35),30)
    s.label('r102_files_caption',(68,244,1160,36),29,MUTED)
    for i in range(11):
        y=282+i*56
        s.pic('r102_file_focus_'+str(i),(58,y,1208,56),art('file-focus'),z=4)
        for icon in ('folder','storage','document'):
            s.pic('r102_file_{}_{}'.format(icon,i),(88,y+9,40,40),art(icon),z=5)
        s.label('r102_file_name_'+str(i),(182,y+3,1058,50),31)
        s.label('r102_file_line_'+str(i),(60,y+55,1200,1),background='#225477',z=2)
    s.label('r102_file_scroll_track',(1274,282,5,616),background='#243D59')
    s.label('r102_file_scroll_thumb',(1274,282,5,100),background=CYAN,z=6)
    s.label('r102_file_empty',(84,360,1140,150),31,MUTED,lines=3)
    s.label('r102_selected_caption',(1334,162,514,68),40,CYAN)
    s.label('r102_file_detail_line',(1334,232,514,2),background='#225477')
    for icon in ('folder','storage','document'):
        s.pic('r102_selected_'+icon,(1336,276,122,122),art(icon),z=5)
    s.label('r102_selected_name',(1492,268,350,150),38,lines=3)
    s.label('r102_selected_help',(1334,438,512,98),29,MUTED,lines=3)
    s.label('r102_file_detail_line2',(1334,550,514,2),background='#225477')
    s.label('r102_filter_caption',(1334,570,512,46),28,MUTED)
    s.label('r102_filter_value',(1334,622,512,50),33)
    s.label('r102_file_detail_line3',(1334,690,514,2),background='#225477')
    s.label('r102_location_caption',(1334,710,512,46),28,MUTED)
    s.label('r102_location_value',(1334,760,512,58),32,lines=2)
    s.label('r102_file_detail_line4',(1334,832,514,2),background='#225477')
    s.label('r102_file_help',(1334,850,512,42),25,MUTED)
    s.label('message',(52,922,1228,38),23,MUTED)

def restyle(markup,kind):
    old=ET.fromstring(markup)
    w,h=map(int,old.get('size').split(','))
    px=lambda n:max(1,round(n*min(w/1920.,h/1080.)))
    s=NativeSkin(old.get('name'),w,h,px)
    (files if kind=='files' else search)(s)
    root=ET.fromstring(s.finish())
    for n in old:
        name=n.get('name','')
        if name in s.names:continue
        if name=='filelist':
            box,row=file_list_geometry(w,h)
            n.set('position','{},{}'.format(*box[:2]));n.set('size','{},{}'.format(*box[2:]))
            n.set('itemHeight',str(row));n.set('scrollbarMode','showNever');n.set('zPosition','-35')
        elif not name.startswith('remote_'):
            n.set('position','0,0');n.set('size','1,1');n.set('zPosition','-40')
        n.set('transparent','1')
        for attr in ('pixmap','pixmaps','backgroundColor'):n.attrib.pop(attr,None)
        root.append(n)
    for n in root:
        if n.get('name','') in ('r102_gt','r102_brand','r102_title','r102_results_caption','r102_detail_caption','r102_selected_caption','detail_title','r102_selected_name','query_text'):
            n.set('borderWidth',str(px(1)));n.set('borderColor',n.get('foregroundColor','#EDF2F8'))
    return ET.tostring(root,encoding='unicode')

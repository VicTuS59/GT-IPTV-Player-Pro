# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native, text-free-PNG-backed versions of the nine approved auxiliary pages."""
from xml.etree import ElementTree as ET
from .paths import plugin_path
from .utility_design import NativeSkin

CYAN='#20D5ED';MUTED='#AFC1D8'
def art(name):return plugin_path('skin','images','auxiliary-r103',name+'.png')
def shared(name):return plugin_path('skin','images','screens-r102',name+'.png')
def older(name):return plugin_path('skin','images','broadcast-r101',name+'.png')
def panel(s,name,box,side=False):s.pic(name,box,shared('search-detail' if side else 'files-list'))
def divider(s,name,box):s.label(name,box,background='#225477',z=3)
def hidden(s,name):s.label(name,(0,0,1,1),z=-40)
def icon(s,name,box,kind):s.pic(name,box,shared(kind) if kind in ('folder-outline','document','storage') else art(kind),z=5)

def shell(s):
 s.label('aux_gt',(42,10,82,68),60,CYAN);s.label('aux_brand',(134,14,356,62),40)
 divider(s,'aux_header_divider',(508,22,2,56));s.label('aux_title',(540,10,1070,76),48)
 s.pic('aux_footer_panel',(24,978,1872,88),shared('footer'),z=29)
 s.label('footer',(0,976,1920,104),20,z=30)
 s.label('aux_web_hint',(1350,946,530,28),20,MUTED,align='right',z=20)
 for i in range(8):s.label('aux_footer_sep_'+str(i),(0,0,1,1),background='#315774',z=30)

def source(s):
 s.label('step',(1450,20,426,58),27,MUTED,align='right')
 panel(s,'aux_source_panel',(42,102,1206,808));panel(s,'aux_source_side',(1264,102,614,808),True)
 s.label('instruction',(80,126,1120,58),29,MUTED)
 s.label('name_caption',(80,202,1120,44),28,CYAN)
 s.pic('aux_name_panel',(80,252,1128,84),shared('path-bar'))
 s.pic('aux_field_focus_0',(80,252,1128,84),shared('search-focus'),z=4)
 s.label('name_value',(106,264,1070,60),35)
 s.label('aux_source_location_caption',(80,374,1120,44),28,CYAN)
 for i,x in enumerate((80,658)):
  s.pic('aux_source_card_'+str(i),(x,430,550,220),shared('search-detail'))
  s.pic('aux_source_selected_'+str(i),(x,430,550,220),art('source-focus'),z=3)
  s.pic('aux_source_focus_'+str(i),(x,430,550,220),art('source-focus'),z=4)
  icon(s,'aux_source_icon_'+str(i),(x+28,474,100,100),'folder-outline' if not i else 'link')
  s.label('kind_title_'+str(i),(x+160,454,366,50),31)
  s.label('kind_description_'+str(i),(x+160,514,366,68),25,MUTED,lines=2)
  divider(s,'aux_source_line_'+str(i),(x+158,587,366,2))
  s.label('kind_meta_'+str(i),(x+160,600,366,40),26,CYAN)
 s.label('location_caption',(80,688,1120,44),28,CYAN)
 s.pic('aux_path_panel',(80,738,1128,84),shared('path-bar'))
 s.pic('aux_field_focus_2',(80,738,1128,84),shared('search-focus'),z=4)
 s.label('location_value',(106,750,884,60),29)
 s.pic('aux_location_key',(1016,750,170,60),shared('path-bar'),z=4)
 s.label('location_action',(1024,752,154,56),26,CYAN,align='center')
 s.label('message',(82,838,1124,58),24,MUTED,lines=2)
 s.label('aux_side_caption',(1300,128,542,64),38,CYAN)
 for kind in ('folder-outline','link'):icon(s,'aux_side_icon_'+kind,(1300,244,126,126),kind)
 s.label('aux_side_title',(1448,244,390,126),39,lines=2)
 s.label('aux_side_help',(1300,404,540,158),30,MUTED,lines=4)
 divider(s,'aux_source_side_line',(1300,582,540,2))
 s.label('aux_type_caption',(1300,610,540,44),28,MUTED)
 s.label('aux_type_value',(1300,662,540,58),36)
 divider(s,'aux_source_side_line2',(1300,738,540,2))
 s.label('aux_save_hint',(1300,802,540,78),26,MUTED,lines=2)

def history(s):
 panel(s,'aux_history_panel',(42,108,1180,816));panel(s,'aux_history_side',(1240,108,638,816),True)
 s.label('quality',(1700,20,178,54),28,CYAN,align='right')
 s.label('section',(76,128,1100,68),40)
 for i in range(7):
  y=210+i*86
  s.pic('aux_history_row_'+str(i),(58,y,1128,78),older('stream-row'))
  s.pic('aux_history_focus_'+str(i),(58,y,1128,78),shared('file-focus'),z=4)
  s.label('title_'+str(i),(94,y+8,1062,60),36)
  s.label('detail_'+str(i),(96,y+54,900,25),22,MUTED)
  s.label('duration_'+str(i),(1040,y+23,132,42),25,MUTED,align='right')
 s.label('pages',(76,862,1100,46),28,CYAN,align='center')
 s.label('status',(100,460,1020,140),30,MUTED,lines=4)
 s.label('aux_history_scroll_track',(1200,210,7,596),background='#243D59')
 s.label('aux_history_scroll_thumb',(1200,210,7,100),background=CYAN,z=6)
 s.label('aux_side_caption',(1278,132,560,68),38,CYAN)
 icon(s,'aux_history_icon',(1450,310,196,196),'search')
 s.label('aux_history_value',(1280,548,552,144),48,align='center',lines=3)
 s.label('aux_history_help',(1280,720,552,132),30,MUTED,align='center',lines=3)

def youtube(s):
 s.pic('youtube_logo',(548,25,62,44),plugin_path('skin','images','yt-logo-r44-1080.png'),z=5)
 s.pic('quality_frame',(1606,22,270,60),shared('path-bar'))
 s.label('quality',(1614,24,254,56),29,CYAN,align='center')
 s.pic('search_frame',(48,104,1830,78),shared('search-bar'))
 s.pic('search_icon',(72,123,40,40),shared('search'),z=5)
 s.label('search_text',(146,114,1688,56),37)
 s.label('section',(48,192,1100,36),27,MUTED)
 for i in range(5):
  y=236+i*136
  s.pic('row_'+str(i),(48,y,1144,126),older('stream-row'))
  s.pic('focus_'+str(i),(48,y,1144,126),shared('file-focus'),z=4)
  s.pic('thumb_'+str(i),(68,y+14,168,98),z=5)
  s.label('title_'+str(i),(266,y+6,762,76),31,lines=2)
  s.label('detail_'+str(i),(266,y+88,762,30),24,MUTED)
  s.pic('live_bg_'+str(i),(1046,y+44,124,44),plugin_path('skin','images','accounts-r99','status-red.png'),z=5)
  s.label('duration_'+str(i),(1040,y+36,136,60),25,align='center',lines=2)
 panel(s,'preview_panel',(1210,196,668,726),True)
 box=s.rect((1236,220,616,347))
 s.parts.append('<widget name="preview_video" position="{},{}" size="{},{}" backgroundColor="transparent" zPosition="7"/>'.format(*box));s.names.add('preview_video')
 s.label('preview_mask',(1236,220,616,347),background='#020A14',z=8)
 s.pic('preview_art',(1236,220,616,347),z=9)
 s.label('preview_badge',(1236,586,280,44),25,CYAN)
 s.label('preview_resolution',(1560,586,292,44),25,CYAN,align='right')
 s.pic('preview_live_bg',(1236,586,144,44),plugin_path('skin','images','accounts-r99','status-red.png'),z=10)
 s.label('preview_live',(1236,586,144,44),26,align='center',z=11)
 s.label('preview_title',(1236,644,616,142),36,lines=3)
 s.label('preview_detail',(1236,792,616,64),27,MUTED,lines=2)
 divider(s,'aux_preview_divider',(1236,859,616,2))
 s.label('preview_action',(1236,870,616,38),27)
 s.label('pages',(48,922,1144,38),26,CYAN,align='center')
 s.label('status',(70,450,1100,180),29,MUTED,align='center',lines=5)

def online_subtitles(s):
 s.label('programme',(42,104,1400,58),38)
 s.label('provider_status',(42,170,1400,42),25,MUTED)
 s.pic('aux_filter_panel',(1500,116,378,62),shared('path-bar'))
 s.label('filter',(1510,118,358,58),26,align='center')
 panel(s,'aux_subtitles_panel',(30,224,1316,700));panel(s,'aux_subtitles_side',(1360,224,530,700),True)
 s.label('summary',(54,246,850,44),28)
 s.label('page',(1070,246,250,44),26,MUTED,align='right')
 for name,x,w in (('language',54,135),('provider',204,216),('release',436,580),('compatibility',1036,184),('meta',1230,94)):
  s.label('aux_column_'+name,(x,302,w,40),25,MUTED)
 for i in range(7):
  y=352+i*78
  s.pic('row_art_'+str(i),(40,y,1296,74),older('stream-row'))
  s.pic('aux_subtitle_focus_'+str(i),(40,y,1296,74),shared('file-focus'),z=4)
  s.pic('flag_'+str(i),(54,y+18,58,38),z=6)
  s.label('language_'+str(i),(128,y+10,70,54),26)
  s.label('provider_'+str(i),(204,y+10,216,54),25,CYAN)
  s.label('release_'+str(i),(436,y+8,580,58),26)
  for status in ('high','possible','fps_convert','unknown','different'):
   s.pic('status_art_{}_{}'.format(status,i),(1036,y+14,184,46),art('status-'+status),z=5)
  s.label('compatibility_'+str(i),(1040,y+14,176,46),23,z=6)
  s.label('meta_'+str(i),(1230,y+8,94,58),22,lines=2)
 s.label('message',(64,932,1250,36),23,MUTED)
 s.label('aux_subtitle_empty',(100,450,1180,160),31,MUTED,align='center',lines=4)
 s.label('aux_side_caption',(1388,246,472,66),35,CYAN)
 s.pic('aux_selected_flag',(1390,326,66,44),z=6)
 s.label('aux_selected_language',(1478,314,380,64),34)
 s.label('aux_selected_provider',(1390,380,468,46),27,CYAN)
 divider(s,'aux_subtitle_line',(1390,438,468,2))
 s.label('aux_release_caption',(1390,456,468,40),26,MUTED)
 s.label('aux_release_value',(1390,502,468,114),27,lines=3)
 divider(s,'aux_subtitle_line2',(1390,626,468,2))
 s.label('aux_fps_caption',(1390,640,468,36),26,MUTED)
 s.label('aux_fps_value',(1390,680,468,44),29)
 s.label('aux_compat_caption',(1390,738,468,36),26,MUTED)
 for status,color in (('high','#55EF91'),('possible','#FFD239'),('fps_convert','#5FA9FF'),('unknown',MUTED),('different','#FF8497')):
  s.pic('aux_status_art_'+status,(1390,782,468,50),art('status-'+status),z=4)
  s.label('aux_status_'+status,(1400,782,448,50),27,color,align='center')
 s.label('aux_subtitle_help',(1390,848,468,60),25,MUTED,lines=2)

def local_subtitles(s):
 panel(s,'aux_local_heading',(32,108,1858,106))
 s.label('programme',(58,116,1500,50),36)
 s.label('summary',(58,168,1510,36),26,MUTED)
 s.label('page',(1610,164,252,40),26,MUTED,align='right')
 panel(s,'aux_local_panel',(32,230,1200,694));panel(s,'aux_local_side',(1250,230,640,694),True)
 icon(s,'aux_location_icon',(58,249,44,44),'folder-outline')
 s.label('path',(122,240,1080,64),33,CYAN)
 for i in range(7):
  y=316+i*82
  s.pic('row_art_'+str(i),(48,y,1168,78),older('stream-row'))
  s.pic('aux_local_focus_'+str(i),(48,y,1168,78),shared('file-focus'),z=4)
  for kind in ('folder-outline','document'):icon(s,'aux_local_{}_{}'.format(kind,i),(72,y+16,46,46),kind)
  s.label('name_'+str(i),(150,y+8,780,60),32)
  s.label('detail_'+str(i),(950,y+8,248,60),24,MUTED,align='right')
 s.label('message',(58,932,1170,36),22,MUTED)
 s.label('aux_local_empty',(80,450,1110,170),31,MUTED,align='center',lines=4)
 s.label('aux_side_caption',(1284,246,572,68),36,CYAN)
 for kind in ('folder-outline','document'):icon(s,'aux_local_selected_'+kind,(1284,336,126,126),kind)
 s.label('aux_local_name',(1432,326,420,142),36,lines=3)
 s.label('aux_local_help',(1284,490,570,96),28,MUTED,lines=3)
 divider(s,'aux_local_line',(1284,598,570,2))
 s.label('aux_folder_caption',(1284,614,570,40),27,MUTED)
 s.label('aux_folder_value',(1284,658,570,78),30,lines=2)
 divider(s,'aux_local_line2',(1284,750,570,2))
 s.label('aux_language_caption',(1284,766,570,38),27,MUTED)
 s.label('aux_language_value',(1284,808,570,50),30)
 s.label('aux_local_action',(1284,870,570,38),24,MUTED)

def categories(s):
 s.label('manager_caption',(48,92,1800,42),29,MUTED)
 panel(s,'aux_categories_panel',(42,146,1170,778));panel(s,'aux_category_side',(1230,146,648,778),True)
 s.label('aux_category_position',(1040,164,140,44),29,MUTED,align='right')
 for i in range(8):
  y=212+i*78
  s.pic('aux_category_focus_'+str(i),(58,y,1128,74),shared('file-focus'),z=4)
  for state in ('empty','tick'):
   s.pic('category_check_{}_{}'.format(i,state),(84,y+18,38,38),plugin_path('skin','images','utility-r97','unchecked.png' if state=='empty' else 'checked.png'),z=6)
  s.label('category_row_'+str(i),(148,y+8,840,56),33)
  s.label('category_state_'+str(i),(1010,y+8,160,56),24,MUTED,align='right')
  s.label('category_insert_'+str(i),(70,y+72,1100,3),background=CYAN,z=7)
  divider(s,'aux_category_line_'+str(i),(68,y+74,1110,1))
 s.label('list_scroll_track',(1194,214,6,620),background='#243D59')
 s.label('list_scroll_thumb',(1194,214,6,100),background=CYAN,z=6)
 s.label('message',(68,854,1110,58),24,MUTED,align='right',lines=2)
 s.label('items',(90,440,1080,160),31,MUTED,align='center',lines=4)
 s.label('aux_side_caption',(1268,166,570,70),37,CYAN)
 icon(s,'aux_category_icon',(1268,270,126,126),'folder-outline')
 s.label('aux_category_name',(1420,256,418,130),36,lines=3)
 s.label('aux_category_type',(1420,390,418,46),29,MUTED)
 s.label('aux_category_visibility',(1420,448,418,46),31,CYAN)
 divider(s,'aux_category_side_line',(1268,518,570,2))
 s.label('aux_category_help',(1268,546,570,98),28,MUTED,lines=3)
 s.label('manager_selection_count',(1268,662,570,42),28,CYAN)
 s.label('manager_move_help',(1268,718,570,154),26,MUTED,lines=5)

def channels(s):
 s.pic('aux_query_panel',(42,92,1836,68),shared('search-bar'))
 s.pic('aux_query_icon',(66,108,38,38),shared('search'),z=5)
 s.label('aux_query',(146,100,1670,52),33,MUTED)
 s.label('aux_source_type',(1700,20,178,48),28,MUTED,align='right')
 panel(s,'aux_channel_panel',(42,172,1836,760))
 s.label('breadcrumb',(66,190,1460,50),29,CYAN)
 s.label('aux_channel_count',(1580,192,266,46),27,MUTED,align='right')
 for i in range(10):
  y=254+i*64
  s.pic('aux_channel_focus_'+str(i),(56,y,1786,62),shared('file-focus'),z=4)
  s.label('aux_channel_number_'+str(i),(80,y+4,102,54),28,MUTED)
  s.label('aux_channel_name_'+str(i),(208,y+4,1550,54),32)
  s.pic('aux_channel_favorite_'+str(i),(1796,y+18,26,26),plugin_path('skin','images','favorite-star-v15.png'),z=6)
  divider(s,'aux_channel_line_'+str(i),(70,y+61,1760,1))
 s.label('list_scroll_track',(1856,254,6,640),background='#243D59')
 s.label('list_scroll_thumb',(1856,254,6,100),background=CYAN,z=6)
 icon(s,'aux_empty_icon',(864,366,192,192),'search')
 s.label('aux_empty_title',(180,610,1560,108),46,align='center',lines=2)
 s.label('aux_empty_help',(180,732,1560,84),31,MUTED,align='center',lines=2)
 s.label('message',(70,932,1750,36),23,MUTED)

def about(s):
 s.pic('tmdb_logo',(1732,8,144,76),z=5)
 panel(s,'aux_about_panel',(42,104,1260,832));panel(s,'aux_about_side',(1320,104,558,832),True)
 s.label('message',(82,132,1160,770),26,align='center',lines=26)
 s.label('message_scroll_track',(1274,148,5,744),background='#243D59')
 s.label('message_scroll_thumb',(1274,148,5,90),background=CYAN,z=6)
 s.label('aux_side_caption',(1350,124,498,72),36,CYAN)
 icon(s,'aux_about_icon',(1510,226,160,160),'code')
 for i in range(5):
  y=416+i*98
  divider(s,'aux_about_line_'+str(i),(1350,y-10,498,2))
  s.label('aux_about_caption_'+str(i),(1350,y,498,34),26,MUTED)
  s.label('aux_about_value_'+str(i),(1350,y+36,498,48),32)

def restyle(markup,kind):
 old=ET.fromstring(markup);w,h=map(int,old.get('size').split(','))
 px=lambda n:max(1,round(n*min(w/1920.,h/1080.)))
 s=NativeSkin(old.get('name'),w,h,px);shell(s)
 {'source':source,'history':history,'youtube':youtube,'online':online_subtitles,'local':local_subtitles,'categories':categories,'channels':channels,'about':about,'weather':weather}[kind](s)
 root=ET.fromstring(s.finish())
 if kind=='youtube':
  n=root.find("widget[@name='aux_title']");n.set('position','{},{}'.format(round(632*w/1920.),round(10*h/1080.)));n.set('size','{},{}'.format(round(930*w/1920.),round(76*h/1080.)))
 for n in old:
  name=n.get('name','')
  if name in s.names:continue
  if not name.startswith('remote_'):
   n.set('position','0,0');n.set('size','1,1');n.set('zPosition','-40');n.set('transparent','1')
   for attr in ('pixmap','pixmaps','backgroundColor'):n.attrib.pop(attr,None)
  root.append(n)
 for n in root:
  name=n.get('name','')
  if name in ('message',) and kind=='about':n.set('valign','top');n.set('noWrap','1')
  if name.startswith(('aux_gt','aux_brand','aux_title')) or name in ('aux_side_caption','aux_history_value','aux_category_name','aux_city_name'):
   n.set('borderWidth',str(px(1)));n.set('borderColor',n.get('foregroundColor','#EDF2F8'))
 return ET.tostring(root,encoding='unicode')

def weather(s):
 s.label('subtitle',(48,90,1500,40),29,MUTED)
 s.pic('aux_query_panel',(48,142,1828,70),shared('search-bar'))
 s.pic('aux_query_icon',(72,158,38,38),shared('search'),z=5)
 s.label('query',(146,150,1686,54),34)
 panel(s,'aux_weather_panel',(48,230,1198,694));panel(s,'aux_weather_side',(1262,230,614,694),True)
 s.label('aux_results_caption',(76,246,930,62),38,CYAN)
 s.label('aux_weather_position',(1076,252,138,50),28,MUTED,align='right')
 for i in range(7):
  y=324+i*80
  s.pic('aux_weather_focus_'+str(i),(62,y,1168,76),shared('file-focus'),z=4)
  s.label('result_'+str(i),(86,y+4,1100,36),31)
  s.label('aux_weather_detail_'+str(i),(86,y+40,1100,30),24,MUTED)
  divider(s,'aux_weather_line_'+str(i),(76,y+75,1140,1))
 s.label('location_scroll_track',(1236,324,5,556),background='#243D59')
 s.label('location_scroll_thumb',(1236,324,5,100),background=CYAN,z=6)
 s.label('message',(80,442,1130,154),31,MUTED,align='center',lines=4)
 s.label('aux_side_caption',(1292,246,554,66),37,CYAN)
 icon(s,'aux_city_icon',(1496,340,140,140),'pin')
 s.label('aux_city_name',(1292,492,554,74),50,align='center')
 for i,y in enumerate((588,682,776)):
  s.label('aux_city_caption_'+str(i),(1292,y,554,32),26,CYAN,align='center')
  s.label('aux_city_value_'+str(i),(1292,y+34,554,50),30,align='center')
 s.label('aux_city_help',(1292,872,554,40),24,MUTED,align='center')

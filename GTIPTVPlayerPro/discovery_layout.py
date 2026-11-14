# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native discovery geometry for movie and series browsing."""
import unicodedata

CANVAS = '#030910'
CARD_X, CARD_STEP, CARD_Y, CARD_W, CARD_H = 68, 308, 582, 244, 366
TAB_GEOMETRY = ((64, 172), (253, 172), (473, 188), (678, 230), (927, 290), (1235, 176))

def _units(text):
    total = 0.0
    for char in text:
        if unicodedata.combining(char): continue
        total += 1.0 if unicodedata.east_asian_width(char) in ('W','F') else .34 if char.isspace() else .56
    return max(2.0, total)

def guide_geometry(width, height, px, captions, keys):
    left, right = px(64), width-px(64)
    hint_w, gap, inner = px(276), px(18), px(12)
    end = right-hint_w-px(32)
    sizes = [px(24 if key in ('green','yellow','blue') else 56 if key=='ok' else 76 if key=='menu' else 68) for key in keys]
    remaining = end-left-sum(sizes)-inner*len(keys)-gap*(len(keys)-1)
    weights = [_units(text) for text in captions];weight=sum(weights);x=left;used=0;result=[]
    for i,(key,size,unit) in enumerate(zip(keys,sizes,weights)):
        allocation = round(remaining*sum(weights[:i+1])/weight)-used
        used += allocation
        result.append(dict(key=(x,height-px(54),size,px(34)),
            caption=(x+size+inner,height-px(55),max(1,allocation),px(36))))
        x += size+inner+allocation+gap
    return result,(end+px(17),height-px(56),1,px(37)),(end+px(34),height-px(55),hint_w,px(36))

def skin(width, height, px, captions, keys, font_size, asset):
    parts=['<screen name="GTMovieDiscoveryScreen" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="{}">'.format(width,height,CANVAS)]
    def label(name,x,y,w,h,size=26,color='#EDF2F8',background=None,z=4,align='left',lines=False,native=False):
        rect=(x,y,w,h) if native else tuple(px(v) if v else 0 for v in (x,y,w,h))
        parts.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" zPosition="{}" halign="{}" valign="center" {} {}/>'.format(name,*rect,font_size(px,size),color,z,align,'backgroundColor="{}" transparent="0"'.format(background) if background else 'transparent="1"','' if lines else 'noWrap="1"'))
    def pixmap(name,x,y,w,h,path='',z=1):
        source='pixmap="{}"'.format(path.replace('&','&amp;').replace('"','&quot;')) if path else ''
        parts.append('<widget name="{}" position="{},{}" size="{},{}" {} alphatest="blend" scale="1" zPosition="{}" />'.format(name,px(x) if x else 0,px(y) if y else 0,px(w),px(h),source,z))
    for name,z in (('video_guard',-30),('app_bg',-20)):
        label(name,0,0,width,height,1,background=CANVAS,z=z,native=True)
    pixmap('backdrop',500,0,1420,650,z=-10)
    pixmap('hero_fallback',1308,0,612,650,z=-10)
    pixmap('scrim',0,0,1920,650,asset('hero-left-r48.png'),-9)
    pixmap('hero_bottom',0,430,1920,220,asset('hero-bottom-r48.png'),-8)
    pixmap('hero_top',0,0,1920,210,asset('hero-top-r48.png'),-7)
    label('header',64,27,410,48,30)
    label('header_divider',478,34,1,32,1,background='#566A7A')
    label('page_title',501,27,1000,48,30,'#9DAEBF')
    label('tmdb_wordmark',1715,26,95,42,25,'#36D6D8')
    pixmap('tmdb_logo',1817,39,42,12,asset('tmdb-pill-r48.png'),5)
    label('tabs_divider',443,100,1,34,1,background='#566A7A')
    for i,(x,w) in enumerate(TAB_GEOMETRY):
        label('tab_'+str(i),x,94,w,46,28,'#A5B4C5')
        label('tab_selected_'+str(i),x,94,w,46,28,'#37D9EA' if i<2 else '#FFFFFF')
        label('tab_focus_'+str(i),x,145,w,3,1,background='#37D9EA',z=5)
    label('counter',1440,94,416,46,25,'#9EADBF',align='right')
    label('section',68,190,870,32,23,'#7C99B2')
    label('title',64,231,870,94,72,lines=True)
    label('original',68,327,790,28,23,'#8596A8')
    label('rating_star',65,365,38,38,34,'#F4CA77')
    label('rating',110,366,102,38,32)
    label('meta',238,366,630,38,29,'#C2CCD8')
    label('genres',68,411,800,34,27,'#D7DFE8')
    label('director',68,452,800,32,25,'#94A5B9')
    label('plot',68,493,800,64,26,'#BAC7D6',lines=True)
    label('status',68,554,1710,26,20,'#91A5BC')
    label('busy',1806,554,50,26,22,'#37D9EA',align='right')
    for i in range(6):
        x=CARD_X+i*CARD_STEP
        label('focus_'+str(i),x-3,CARD_Y-3,CARD_W+6,CARD_H+6,1,background='#31CFE5',z=4)
        label('empty_'+str(i),x,CARD_Y,CARD_W,CARD_H,25,'#52667C',background=CANVAS,z=5,align='center')
        pixmap('poster_'+str(i),x,CARD_Y,CARD_W,CARD_H,z=6)
        label('name_'+str(i),x-17,955,CARD_W+34,34,24,'#D7E0EB',z=7,align='center')
        label('marker_'+str(i),x+102,996,40,3,1,background='#31CFE5',z=7)
    label('footer',0,0,1,1,1)
    label('footer_line',px(44),height-px(69),width-px(88),1,1,background='#2A3B4C',native=True)
    groups,divider,hint=guide_geometry(width,height,px,captions,keys)
    for i,(key,group) in enumerate(zip(keys,groups)):
        x,y,w,h=group['key']
        if key in ('green','yellow','blue'):
            label('guide_dot_'+str(i),x,y,w,h,31,{'green':'#14D96C','yellow':'#F6C929','blue':'#3C98F5'}[key],align='center',native=True)
        else:
            parts.append('<widget name="guide_bg_{}" position="{},{}" size="{},{}" pixmap="{}" alphatest="blend" scale="1" zPosition="4" />'.format(i,x,y,w,h,asset('key-outline-r48.png')))
            label('guide_key_'+str(i),x,y,w,h,21,align='center',native=True,z=5)
        label('guide_action_'+str(i),*group['caption'],size=23,native=True)
    label('guide_divider',*divider,size=1,background='#566A7A',native=True)
    label('web_hint',*hint,size=17,color='#8D9EB3',native=True)
    parts.append('</screen>')
    return '\n'.join(parts)

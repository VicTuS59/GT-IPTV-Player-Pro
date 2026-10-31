# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Receiver-independent geometry and artwork for the season/episode browser.

The photographic layer is supplied by the selected series at runtime.  All
bundled artwork is theme/shape-only and needs no Pillow on the receiver.
"""

from xml.sax.saxutils import escape

from .paths import plugin_path
from .channel_highlight import normalize_channel_highlight, normalize_selection_border


EPISODE_ROWS = 8
EPISODE_PIXMAP_NAMES = (
    "episode_backdrop", "episode_scrim", "episode_resume_button",
    "episode_favorite_button", "episode_download_button", "episode_favorite_key",
    "season_tabs_focus",
) + tuple("episode_action_focus_{}".format(i) for i in range(3)) + tuple(
    "episode_{}_{}".format(kind, i)
    for i in range(EPISODE_ROWS) for kind in ("watched", "focus_fill", "focus")
)
EPISODE_LABEL_NAMES = (
    "series_title", "series_meta", "series_genre", "series_plot", "series_cast",
    "series_cast_label", "episode_resume_text", "episode_resume_detail",
    "episode_favorite_text", "episode_download_text", "episode_download_key",
    "season_progress_label", "season_progress_text", "season_progress_track",
    "season_progress_fill", "season_progress_percent", "season_tabs_rule",
    "season_count", "episode_position", "episode_clock",
) + tuple("season_tab_{}".format(i) for i in range(3)) + tuple(
    "season_tab_line_{}".format(i) for i in range(3)
) + tuple(
    "episode_{}_{}".format(kind, i)
    for i in range(EPISODE_ROWS)
    for kind in ("row_bg", "number", "name", "status", "progress_track", "progress_fill")
)


def episode_art(name):
    return plugin_path("skin", "images", "episode-r46-{}.png".format(name))


_ACTION_OUTLINE_SIZES = frozenset((
    (610, 128), (540, 128), (305, 64), (270, 64),
    (203, 43), (180, 43), (193, 35), (160, 35),
    (193, 29), (160, 29), (170, 29), (141, 29),
))


def episode_action_outline_asset(width, height):
    """Keep thin rounded action borders at their native physical pixels.

    The receiver blitter can omit a two-pixel source edge when shrinking the
    shared 600-pixel frame. Known desktop geometries use matching PNG sizes;
    unusual desktops retain the existing scalable artwork.
    """
    width, height = int(width), int(height)
    if (width, height) in _ACTION_OUTLINE_SIZES:
        return (plugin_path("skin", "images",
                            "episode-r48-action-outline-{}x{}.png".format(width, height)), 0)
    return episode_art("action-outline"), 1


def episode_focus_assets(fill="navy", border=None):
    """Use independent saved fill/border colors without 289 combined images."""
    fill = normalize_channel_highlight(fill)
    border = normalize_selection_border(border, fill)
    return (episode_art("focus-fill-" + fill), episode_art("focus-border-" + border))



EPISODE_LABEL_NAMES += ("episode_guard", "episode_header_divider", "episode_selected_caption", "episode_list_caption", "episode_page_hint", "episode_footer_line")
EPISODE_PIXMAP_NAMES += ("episode_bottom_scrim",)

def episode_geometry(width,height,px):
    width,height=max(1,int(width)),max(1,int(height))
    compact=width<=800
    x=lambda n:int(round(n*width/1920.))
    y=lambda n:int(round(n*height/1080.))
    rect=lambda a,b,c,d:(x(a),y(b),max(1,x(c)),max(1,y(d)))
    footer_h=max(int(px(72)),56 if compact else 1)
    footer_y=height-footer_h
    margin=max(17,x(64));right_x=x(1200);right_w=width-right_x-margin
    left_w=right_x-margin-x(64)
    hidden=(0,0,1,1)
    fonts={key:max(1,int(px(value))) for key,value in {
        'title':64,'meta':27,'genre':23,'plot':27,'cast':22,'cast_label':22,
        'resume':34,'resume_detail':23,'favorite':22,'download':21,
        'progress_label':23,'progress_text':22,'progress_percent':22,
        'name':30,'status':23,'number':35,'season':27,'count':22,
        'position':22,'clock':30,'breadcrumb':28,'brand':32,'brand_sub':32,
        'selected_caption':22,'list_caption':24,'page_hint':22}.items()}
    left={
        'title':rect(64,480,1080,112),'meta':rect(64,610,1080,38),
        'genre':hidden,'plot':rect(64,665,1040,95),'cast':hidden,'cast_label':hidden,
        'selected_caption':rect(64,782,1040,26),
        'resume_text':rect(64,816,1040,43),'resume_detail':rect(64,866,1040,30),
        'resume':hidden,'favorite':hidden,'download':hidden,'favorite_key':hidden,
        'favorite_text':hidden,'download_key':hidden,'download_text':hidden,
        'progress_label':rect(64,936,1040,25),
        'season_progress':rect(64,967,1080,9),
        'progress_text':rect(64,982,940,23),'progress_percent':rect(1044,982,100,23),
    }
    brand=rect(64,24,166,56);brand_sub=rect(230,24,220,56)
    breadcrumb=rect(540,24,1000,56);clock=rect(1760,24,96,56)
    header_divider=rect(505,33,1,37)
    tab_y,tab_h,list_y=y(100),y(44),y(230)
    list_bottom=min(y(950),footer_y-max(24,int(px(40))))
    message=rect(64,902,1080,25)
    if compact:
        sy=lambda n:int(round(n*height/480.))
        cr=lambda a,b,c,d:(a,sy(b),max(1,c),max(1,sy(d)))
        left.update({
            'title':cr(margin,200,left_w,46),'meta':cr(margin,251,left_w,19),
            'plot':cr(margin,278,left_w,46),'selected_caption':cr(margin,330,left_w,15),
            'resume_text':cr(margin,349,left_w,22),'resume_detail':cr(margin,373,left_w,14),
            'progress_label':(margin,footer_y-34,left_w,13),
            'season_progress':(margin,footer_y-19,left_w,4),
            'progress_text':(margin,footer_y-13,max(1,left_w-35),11),
            'progress_percent':(margin+left_w-33,footer_y-13,33,11),
        })
        fonts.update(title=max(22,int(width*.033)),meta=12,plot=13 if width<700 else 14,
            resume=15,resume_detail=11,progress_label=11,progress_text=11,progress_percent=11,
            name=14,status=11,number=18,season=13,count=11,position=11,clock=17,
            breadcrumb=12,brand=18,brand_sub=18,selected_caption=11,list_caption=12,page_hint=11)
        brand=(margin,sy(16),90,sy(26));brand_sub=(margin+91,sy(16),95,sy(26))
        breadcrumb=(margin+196,sy(16),max(1,width-margin*2-278),sy(26))
        clock=(width-margin-55,sy(16),55,sy(26));header_divider=hidden
        message=cr(margin,174,left_w,20)
    tab_w=max(1,min(x(180),right_w//3))
    tabs=[(right_x+i*(tab_w+max(2,x(16))),tab_y,tab_w,tab_h) for i in range(3)]
    rows=[];step=max(1,(list_bottom-list_y)//EPISODE_ROWS)
    for i in range(EPISODE_ROWS):
        row_y=list_y+i*step;row_h=max(1,step-max(1,int(px(3))))
        pad=max(5,x(16));number_w=max(25,x(65));text_x=right_x+pad+number_w+max(6,x(16))
        text_w=max(1,right_x+right_w-text_x-max(10,x(20)))
        name_y=row_y+(2 if compact else y(7));name_h=max(17,y(39))
        status_y=row_y+(max(19,name_h) if compact else y(47));status_h=max(14,y(30))
        progress_w=max(32,x(110));progress_h=max(3,y(5))
        progress_x=right_x+right_w-progress_w-max(9,x(20))
        tick=max(16,int(px(30)))
        progress=(progress_x,status_y+max(0,(status_h-progress_h)//2),progress_w,progress_h)
        rows.append({'row':(right_x,row_y,right_w,row_h),
            'rule':(right_x+pad,row_y+row_h,max(1,right_w-2*pad),1),
            'number':(right_x+pad,row_y+max(0,(row_h-fonts['number']-4)//2),number_w,fonts['number']+4),
            'name':(text_x,name_y,text_w,name_h),
            'status':(text_x,status_y,max(1,progress_x-text_x-max(5,x(12))),status_h),
            'watched':(right_x+right_w-tick-pad,row_y+max(0,(row_h-tick)//2),tick,tick),
            'progress':progress,'progress_track':progress,'progress_fill':progress,'progress_width':progress_w})
    result=dict(left)
    result.update(width=width,height=height,compact=compact,fonts=fonts,footer_y=footer_y,footer_h=footer_h,
        rows=rows,list_scroll=(width-max(10,x(35)),list_y,max(3,x(6)),max(1,list_bottom-list_y)),
        list_rect=(right_x,list_y,right_w,max(1,list_bottom-list_y)),
        season_tabs=tabs,season_tab=tabs,season_tabs_focus=hidden,
        season_tabs_rule=(right_x,tab_y+tab_h+max(2,y(8)),right_w,max(1,y(2))),
        season_tab_lines=[(a,b+h+max(2,y(7)),min(w,max(35,x(130))),max(2,y(3))) for a,b,w,h in tabs],
        season_count=(right_x+right_w//2,list_y-max(24,y(50)),right_w//2,max(18,y(32))),
        list_caption=(right_x,list_y-max(24,y(50)),right_w//2,max(18,y(32))),
        episode_position=(right_x,footer_y-max(22,y(40)),right_w//2,max(17,y(28))),
        page_hint=(right_x+right_w//2,footer_y-max(22,y(40)),right_w//2,max(17,y(28))),
        message=message,backdrop=rect(0,72,1180,790),scrim=rect(0,340,1180,522),
        bottom_scrim=rect(0,620,1180,250),brand=brand,brand_sub=brand_sub,
        breadcrumb=breadcrumb,clock=clock,header_divider=header_divider,
        actions=(hidden,hidden,hidden),resume_font=fonts['resume'],name_font=fonts['name'],
        status_font=fonts['status'],title_font=fonts['title'],plot_font=fonts['plot'],cast_font=fonts['cast'])
    return result


def episode_footer(download=False):
    from .remote_footer import footer_item
    return (footer_item('up_down','Episode'),footer_item('left_right','Season'),
            footer_item('ok','download' if download else 'play'),footer_item('ok_long','Watched'),
            footer_item('red','Favorites'),footer_item('green','refresh'),
            footer_item('menu','download'),footer_item('exit','back'))


def episode_skin(width,height,px,watched_icon):
    g=episode_geometry(width,height,px);f=g['fonts'];names=set()
    lines=['<screen name="GTEpisodeListScreen" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="#030910">'.format(width,height)]
    def tag(name,box,font=1,color='#EDF2F8',background=None,pixmap=None,z=5,align='left',wrap=False):
        names.add(name);a,b,c,d=box
        attrs='name="{}" position="{},{}" size="{},{}" zPosition="{}"'.format(name,a,b,c,d,z)
        if name in EPISODE_PIXMAP_NAMES or name=='app_bg':
            attrs+=' alphatest="blend" scale="1"'
            if pixmap:attrs+=' pixmap="{}"'.format(escape(pixmap,{'"':'&quot;'}))
        else:
            attrs+=' font="Regular;{}" foregroundColor="{}" transparent="{}" valign="center" halign="{}" noWrap="{}"'.format(max(1,int(font)),color,0 if background else 1,align,0 if wrap else 1)
            if background:attrs+=' backgroundColor="{}"'.format(background)
        lines.append('<widget {}/>'.format(attrs))
    tag('app_bg',(0,0,1,1),z=-40)
    tag('episode_guard',(0,0,width,height),background='#030910',z=-30)
    tag('episode_backdrop',g['backdrop'],z=-20)
    tag('episode_scrim',g['scrim'],pixmap=plugin_path('skin','images','discovery-r48','hero-bottom-r48.png'),z=-19)
    tag('episode_bottom_scrim',g['bottom_scrim'],pixmap=plugin_path('skin','images','discovery-r48','hero-bottom-r48.png'),z=-18)
    for text,key,c in (('GT IPTV','brand','#FFFFFF'),('PLAYER','brand_sub','#31CFE5')):
        a,b,w,h=g[key]
        lines.append('<eLabel position="{},{}" size="{},{}" text="{}" font="Regular;{}" foregroundColor="{}" transparent="1" valign="center" zPosition="6"/>'.format(a,b,w,h,text,f[key],c))
    tag('episode_header_divider',g['header_divider'],background='#566A7A')
    tag('breadcrumb',g['breadcrumb'],f['breadcrumb'],color='#A9B8C9')
    tag('episode_clock',g['clock'],f['clock'],align='right')
    for name,key,font_key in (('series_title','title','title'),('series_meta','meta','meta'),
                            ('series_genre','genre','genre'),('series_plot','plot','plot'),
                            ('series_cast_label','cast_label','cast_label'),('series_cast','cast','cast'),
                            ('episode_selected_caption','selected_caption','selected_caption'),
                            ('episode_resume_text','resume_text','resume'),('episode_resume_detail','resume_detail','resume_detail'),
                            ('episode_list_caption','list_caption','list_caption'),('episode_page_hint','page_hint','page_hint')):
        tag(name,g[key],f[font_key],color='#FFFFFF' if name in ('series_title','episode_resume_text') else '#B9C8D8',wrap=name in ('series_title','series_plot'),align='right' if key=='page_hint' else 'left')
    tag('season_progress_label',g['progress_label'],f['progress_label'],color='#A9B8C9')
    tag('season_progress_text',g['progress_text'],f['progress_text'],color='#B9C8D8')
    tag('season_progress_track',g['season_progress'],background='#24384E')
    tag('season_progress_fill',g['season_progress'],background='#31CFE5',z=6)
    tag('season_progress_percent',g['progress_percent'],f['progress_percent'],align='right')
    tag('season_tabs_rule',g['season_tabs_rule'],background='#24415A')
    for i,tab in enumerate(g['season_tabs']):
        tag('season_tab_'+str(i),tab,f['season'],align='left')
        tag('season_tab_line_'+str(i),g['season_tab_lines'][i],background='#31CFE5',z=6)
    tag('season_count',g['season_count'],f['count'],align='right',color='#A9B8C9')
    fill_asset,border_asset=episode_focus_assets()
    for i,row in enumerate(g['rows']):
        tag('episode_row_bg_'+str(i),row['rule'],background='#173549',z=1)
        tag('episode_focus_fill_'+str(i),row['row'],pixmap=fill_asset,z=2)
        tag('episode_focus_'+str(i),row['row'],pixmap=border_asset,z=6)
        for part in ('number','name','status'):tag('episode_'+part+'_'+str(i),row[part],f[part],color='#FFFFFF' if part=='name' else '#B9C8D8')
        tag('episode_watched_'+str(i),row['watched'],pixmap=watched_icon,z=7)
        tag('episode_progress_track_'+str(i),row['progress'],background='#24384E')
        tag('episode_progress_fill_'+str(i),row['progress'],background='#31CFE5',z=6)
    tag('list_scroll_track',g['list_scroll'],background='#24384E')
    tag('list_scroll_thumb',g['list_scroll'],background='#31CFE5',z=6)
    tag('episode_position',g['episode_position'],f['position'],color='#A9B8C9')
    tag('message',g['message'],max(11,f['status']),color='#F4CA77')
    line_x=max(1,int(px(32)))
    tag('episode_footer_line',(line_x,g['footer_y']-2,width-2*line_x,1),background='#24384E',z=30)
    tag('footer',(0,g['footer_y'],width,g['footer_h']),int(px(20)),z=30)
    for name in EPISODE_LABEL_NAMES+EPISODE_PIXMAP_NAMES+('top_accent','header','screen_label','content_panel','list_accent','items'):
        if name not in names:tag(name,(0,0,1,1),z=-40)
    lines.append('</screen>')
    return '\n'.join(lines)

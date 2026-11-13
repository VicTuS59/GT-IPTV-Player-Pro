# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Resolution-driven receiver-display geometry, independent of TV skins."""

FIELDS = ('kind','percent','title','episode','elapsed_label','elapsed',
          'total_label','total','subtitle','detail','clock','remaining_label','remaining')


def panel_layout(width, height, kind='movie'):
    width, height = max(1, int(width)), max(1, int(height))
    width, height = min(width,4096), min(height,4096)
    minimal = height < (40 if kind == 'live' else 56) or width < 96
    compact = not minimal and (height < 160 or width < 240)
    full_playback = kind in ('movie', 'series', 'youtube') and not minimal and not compact
    margin = 0 if minimal else max(1,min(width,height)//40)
    content = max(1,width-2*margin)
    bar = 0 if minimal else min(max(2,height//38),max(1,height//10))
    if full_playback:
        bar = height // 12
    bar_y = max(margin,height-margin-bar)
    usable = max(1,bar_y-margin-(max(1,height//90) if bar else 0))
    fields = {name:dict(box=(0,0,1,1),font=1,visible=False,scroll=True) for name in FIELDS}
    def put(name,x,y,w,h,color='#FFFFFF',align='left',scroll=True):
        w,h=max(1,int(w)),max(1,int(h))
        x,y=max(0,int(x)),max(0,int(y))
        w,h=min(w,max(1,width-x)),min(h,max(1,height-y))
        fields[name]=dict(box=(x,y,w,h),font=max(1,int(h*.9)),visible=True,
                          color=color,align=align,scroll=scroll)
    def rows(weights):
        result={};used=0;total=sum(weight for name,weight in weights)
        for index,(name,weight) in enumerate(weights):
            end=usable if index==len(weights)-1 else int(round(usable*sum(w for n,w in weights[:index+1])/total))
            result[name]=(margin+used,max(1,end-used));used=end
        return result
    if minimal:
        put('title',0,0,width,height)
    elif compact:
        # Keep Live TV exactly as approved. VOD uses two large rows which
        # rotate through title, elapsed, total and remaining information.
        weights=([('title',35),('subtitle',32),('clock',33)] if kind=='live'
                 else [('kind',42),('title',58)])
        cells=rows(weights)
        for name,(y,h) in cells.items():
            put(name,margin,y,content,h,'#22D3EE' if name=='remaining' else '#FFFFFF',
                scroll=name not in ('clock',))
    elif kind=='live':
        cells=rows([('kind',10),('title',29),('subtitle',23),('detail',12),('clock',26)])
        for name,(y,h) in cells.items():
            put(name,margin,y,content,h,'#A855F7' if name=='kind' else '#22D3EE' if name=='detail' else '#FFFFFF',
                'center' if name=='clock' else 'left',name!='clock')
    else:
        portrait = height > width
        if full_playback and portrait:
            weights = [('kind', 8), ('title', 13), ('remaining_label', 8), ('remaining', 29),
                       ('elapsed_label', 7), ('elapsed', 14), ('total_label', 7), ('total', 14)]
        elif full_playback:
            weights = [('kind', 11), ('title', 16), ('remaining_label', 10), ('remaining', 37),
                       ('time_labels', 10), ('times', 16)]
        elif portrait:
            weights = [('kind', 8), ('title', 17), ('elapsed_label', 8), ('elapsed', 16),
                       ('total_label', 8), ('total', 16), ('remaining_label', 8), ('remaining', 19)]
        else:
            weights = [('kind', 12), ('title', 20), ('time_labels', 13), ('times', 20),
                       ('remaining_label', 13), ('remaining', 22)]
        cells=rows(weights)
        y,h=cells['kind'];half=max(1,content//2)
        put('kind',margin,y,content*3//4,h,'#A855F7')
        put('percent',margin+content*3//4,y,content-content*3//4,h,'#22D3EE','right',False)
        y,h=cells['title'];put('title',margin,y,content,h)
        if portrait:
            for name in ('elapsed_label','elapsed','total_label','total'):
                y,h=cells[name]
                put(name,margin,y,content,h,align='center',scroll=name.endswith('_label'))
        else:
            y,h=cells['time_labels']
            put('elapsed_label',margin,y,half,h)
            put('total_label',margin+half,y,content-half,h,align='right')
            y,h=cells['times']
            put('elapsed',margin,y,half,h,scroll=False)
            put('total',margin+half,y,content-half,h,align='right',scroll=False)
        y, h = cells['remaining_label']
        put('remaining_label', margin, y, content, h, '#22D3EE' if full_playback else '#FFFFFF', 'center')
        y,h=cells['remaining'];put('remaining',margin,y,content,h,'#22D3EE','center',False)
    return dict(width=width,height=height,mode='minimal' if minimal else 'compact' if compact else 'full',
                kind=kind,margin=margin,content_w=content,progress_y=bar_y,progress_h=bar,
                fields=fields,live_detail=kind=='live',vod_detail=kind!='live')


def panel_skin(layout):
    lines=['<screen name="GTPlayerFrontPanelSummary" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="#000000">'.format(layout['width'],layout['height'])]
    for name in FIELDS:
        field=layout['fields'][name];x,y,w,h=field['box']
        lines.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" backgroundColor="#000000" halign="{}" valign="center" noWrap="1" />'.format(
            name,x,y,w,h,field['font'],field.get('color','#FFFFFF'),field.get('align','left')))
    for name,color,width in (('progress_bg','#26344E',layout['content_w']),('progress_fill','#22D3EE',1)):
        lines.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;1" backgroundColor="{}" transparent="0" />'.format(
            name,layout['margin'],layout['progress_y'] if layout['progress_h'] else 0,width,max(1,layout['progress_h']),color))
    lines.append('</screen>');return '\n'.join(lines)

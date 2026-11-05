# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Full synopsis on demand; native paging, no automatic scrolling timer."""
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.ScrollLabel import ScrollLabel
from Screens.Screen import Screen
from .i18n import _
from .vod_light import geometry, CANVAS, INK


class LightSynopsisScreen(Screen):
    def __init__(self, session, text):
        from .browser_common import _scale
        width,height,unused=_scale();g=geometry(width,height);rect=g['rect'];font=g['font']
        parts=['<screen name="GTLightSynopsis" position="0,0" size="{},{}" flags="wfNoBorder" backgroundColor="{}">'.format(width,height,CANVAS)]
        for name,box,size in (('title',(80,45,1760,80),38),('text',(80,155,1760,730),32),('footer',(80,935,1760,70),25)):
            x,y,w,h=rect(*box)
            parts.append('<widget name="{}" position="{},{}" size="{},{}" font="Regular;{}" foregroundColor="{}" backgroundColor="{}" transparent="0" />'.format(name,x,y,w,h,font(size),INK,CANVAS))
        self.skin='\n'.join(parts)+'</screen>'
        Screen.__init__(self,session)
        self['title']=Label(_('Information'))
        self['text']=ScrollLabel(text)
        self['footer']=Label('▲▼  '+_('Page')+'     OK / EXIT  '+_('Close'))
        self['actions']=ActionMap(['OkCancelActions','DirectionActions'],{
            'ok':self.close,'cancel':self.close,'up':self['text'].pageUp,
            'down':self['text'].pageDown,'left':self['text'].pageUp,
            'right':self['text'].pageDown},-1)

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Clipped native text with one shared summary timer; no skin renderer addon."""
import time
import unicodedata
from Components.GUIComponent import GUIComponent
from enigma import eLabel,eWidget,ePoint,eSize,gFont
from skin import parseColor
from .typography import clean_dynamic_text,_text_units


def right_to_left(text):
    for character in text:
        direction=unicodedata.bidirectional(character)
        if direction in ('R','AL'):return True
        if direction=='L':return False
    return False


def scroll_offset(elapsed,overflow,speed,rtl=False):
    if overflow<=0:return 0
    travel=float(overflow)/max(1.0,speed)
    phase=max(0.0,float(elapsed))%(1.5+travel+1.2)
    moved=0.0 if phase<1.5 else min(float(overflow),(phase-1.5)*speed)
    return int(round(-overflow+moved if rtl else -moved))


class PanelText(GUIComponent):
    GUI_WIDGET=eWidget
    def __init__(self):
        GUIComponent.__init__(self)
        self._text='';self._label=None;self._width=1;self._height=1
        self._font_size=1;self._family='Regular';self._color='#FFFFFF'
        self._align='left';self._allow_scroll=True;self._overflow=0
        self._since=time.monotonic();self._rtl=False;self._last_offset=None

    def configure(self,field):
        self._width,self._height=field['box'][2:]
        self._font_size=field['font'];self._color=field.get('color','#FFFFFF')
        self._align=field.get('align','left');self._allow_scroll=field.get('scroll',True)

    def getText(self):return self._text
    def setText(self,value):return self.set_panel_text(value)

    def set_panel_text(self,value,preserve_phase=False):
        text=clean_dynamic_text(value)
        if text==self._text:return False
        self._text=text;self._rtl=right_to_left(text)
        if not preserve_phase:self._since=time.monotonic()
        self._prepare();return True

    def postWidgetCreate(self,instance):
        self._label=eLabel(instance)
        self._prepare()

    def preWidgetRemove(self,instance):
        self._label=None;self._overflow=0

    def applySkin(self,desktop,parent):
        # The parent is only a clipping rectangle. Text attributes belong to
        # its child eLabel, preserving Arabic/Indic shaping during movement.
        attributes=[]
        for key,value in self.skinAttributes or ():
            if key=='font':
                self._family,size=value.rsplit(';',1);self._font_size=max(1,int(size))
            elif key=='foregroundColor':self._color=value
            elif key=='halign':self._align=value
            elif key not in ('valign','noWrap'):attributes.append((key,value))
        self.skinAttributes=attributes
        result=GUIComponent.applySkin(self,desktop,parent)
        try:
            size=self.instance.size();self._width=max(1,size.width());self._height=max(1,size.height())
        except Exception:pass
        self._prepare();return result

    def _prepare(self):
        self._last_offset=None
        label=self._label
        if label is None:
            estimate=int(_text_units(self._text)*self._font_size*.58)
            self._overflow=max(0,estimate-self._width) if self._allow_scroll else 0
            return
        label.setNoWrap(1);label.setForegroundColor(parseColor(self._color))
        label.setBackgroundColor(parseColor('#000000'))
        label.setVAlign(eLabel.alignCenter)
        label.setText(self._text)
        size = self._font_size
        while True:
            label.setFont(gFont(self._family,size))
            label.resize(eSize(max(self._width,size*max(1,len(self._text))*2),self._height))
            try:
                measured=label.calculateSize();measured_width=max(0,int(measured.width()));measured_height=max(0,int(measured.height()))
            except Exception:
                measured_width=int(_text_units(self._text)*size*.58);measured_height=size
            if size == 1:
                break
            # Scale to the measured bounds before rechecking the native font.
            # Clocks can change each second, so avoid measuring every size.
            fitted = size
            if measured_height > self._height:
                fitted = self._height * size // measured_height
            if not self._allow_scroll and measured_width > self._width:
                fitted = min(fitted, self._width * size // measured_width)
            if fitted == size:
                break
            size = max(1, fitted)
        self._display_font=size
        self._overflow=max(0,measured_width-self._width) if self._allow_scroll else 0
        self._speed=max(10.0,min(90.0,size*.8))
        if self._overflow:
            label.setHAlign(eLabel.alignRight if self._rtl else eLabel.alignLeft)
            label.resize(eSize(self._width+self._overflow,self._height))
        else:
            label.setHAlign({'left':eLabel.alignLeft,'center':eLabel.alignCenter,'right':eLabel.alignRight}.get(self._align,eLabel.alignLeft))
            label.resize(eSize(self._width,self._height))
        self.tick(time.monotonic())

    def moving(self):return bool(self._overflow and self._label is not None and self._text)

    def cycle_seconds(self):
        return 2.7+self._overflow/max(10.0,getattr(self,'_speed',10.0))

    def tick(self,now):
        if self._label is None:return
        offset=scroll_offset(now-self._since,self._overflow,getattr(self,'_speed',10.0),self._rtl)
        if offset!=self._last_offset:
            self._label.move(ePoint(offset,0));self._last_offset=offset

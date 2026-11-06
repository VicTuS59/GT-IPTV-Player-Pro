# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Enigma2's native listbox owns row painting and selection movement."""
import enigma
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryText, MultiContentEntryProgress
from enigma import (eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT,
                    RT_HALIGN_RIGHT, RT_VALIGN_CENTER)
from skin import parseColor

RT_ELLIPSIS = getattr(enigma, "RT_ELLIPSIS", 0)


class NativeVODList(MenuList):
    def __init__(self,width,row_height,font,foreground,background,selected,title_for=None,
                 border='#06D7EB',separator='#174456',accent='#06D7EB'):
        self._row_width,self._row_height=width,row_height
        self._title_for=title_for
        self._ink=parseColor(foreground).argb()
        self._background=parseColor(background).argb()
        self._selected=parseColor(selected).argb()
        self._border=parseColor(border).argb()
        self._separator=parseColor(separator).argb()
        self._accent=parseColor(accent).argb()
        self._active_identity=None
        self._padding=max(4,font//2)
        MenuList.__init__(self,[],enableWrapAround=True,content=eListboxPythonMultiContent)
        self.l.setFont(0,gFont('Regular',font))
        self.l.setItemHeight(row_height)
        self.l.setBuildFunc(self._build_row)

    def _build_row(self,title,identity):
        if self._title_for is not None:title=self._title_for(title,identity)
        gap=max(2,self._row_height//14);pad=self._padding
        height=max(1,self._row_height-2*gap)
        active=identity==self._active_identity
        background=self._selected if active else self._background
        content=self._title_for is not None
        # Enigma2 builds only visible rows. The stripe and outline are native
        # fills, shared across the category and content panes.
        row=[identity,
            MultiContentEntryProgress(pos=(0,gap),size=(self._row_width,height),
                percent=0,borderWidth=1,foreColor=background,
                foreColorSelected=self._border if content else self._selected,
                backColor=background,backColorSelected=self._selected),
            MultiContentEntryProgress(pos=(0,gap),size=(max(2,self._row_height//16),height),
                percent=100,borderWidth=0,foreColor=self._accent if active else background,
                foreColorSelected=self._accent,backColor=background,backColorSelected=self._selected),
            MultiContentEntryText(pos=(pad,gap+1),size=(max(1,self._row_width-pad*(3 if content else 2)),max(1,height-2)),
                font=0,flags=RT_HALIGN_LEFT|RT_VALIGN_CENTER|RT_ELLIPSIS,text=title,
                color=self._accent if active else self._ink,
                color_sel=self._ink if content else self._accent,
                backcolor=background,backcolor_sel=self._selected)]
        if content:
            row.extend((
                MultiContentEntryProgress(pos=(0,self._row_height-1),size=(self._row_width,1),
                    percent=100,borderWidth=0,foreColor=self._separator,foreColorSelected=self._separator,
                    backColor=self._background,backColorSelected=self._background),
                MultiContentEntryText(pos=(self._row_width-2*pad,gap+1),size=(pad,max(1,height-2)),
                    font=0,flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,text='›',
                    color=self._background,color_sel=self._accent,
                    backcolor=self._background,backcolor_sel=self._selected)))
        return row

    def set_active_identity(self,identity):
        identity=str(identity) if identity is not None else None
        if identity!=self._active_identity:
            self._active_identity=identity
            self.l.invalidate()

    def publish_list(self,rows):
        # GrowingCategoryList appends to the same Python list. Enigma2 reads
        # its size directly; a non-home reset updates geometry without moving
        # the native cursor or viewport back to the beginning.
        if rows is getattr(self,'list',None):
            reset=getattr(getattr(self,'instance',None),'entryReset',None)
            if callable(reset):
                try:
                    reset(False)
                    return
                except (TypeError,RuntimeError):pass
        self.setList(rows)

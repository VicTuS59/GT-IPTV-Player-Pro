# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Enigma2's native listbox owns row painting and selection movement."""
import enigma
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryText, MultiContentEntryProgress
from enigma import (eListboxPythonMultiContent, gFont, RT_HALIGN_LEFT,
                    RT_HALIGN_RIGHT, RT_VALIGN_CENTER)
from skin import parseColor

from .selected_row_scroll import clear_selection, native_list_row, redraw_native_row, select_native

RT_ELLIPSIS = getattr(enigma, "RT_ELLIPSIS", 0)


def _supports_progress_color(name):
    try:
        MultiContentEntryProgress(pos=(0, 0), size=(1, 1), percent=0, **{name: 0})
    except TypeError:
        return False
    return True


_PROGRESS_FORE_SELECTED = _supports_progress_color('foreColorSelected')
_PROGRESS_BACK_SELECTED = _supports_progress_color('backColorSelected')


def _progress(foreColorSelected, backColorSelected, **kwargs):
    if _PROGRESS_FORE_SELECTED:
        kwargs['foreColorSelected'] = foreColorSelected
    if _PROGRESS_BACK_SELECTED:
        kwargs['backColorSelected'] = backColorSelected
    return MultiContentEntryProgress(**kwargs)


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
        gap = max(2, row_height // 14)
        self._title_box = (self._padding, gap + 1,
                           max(1, width - self._padding * (3 if title_for is not None else 2)),
                           max(1, row_height - 2 * gap - 2))
        self._title_font = ('Regular', font)
        self._scroll_hidden_index = None
        self._scroll_full_index = None
        MenuList.__init__(self,[],enableWrapAround=True,content=eListboxPythonMultiContent)
        self.l.setFont(0, gFont(*self._title_font))
        self.l.setItemHeight(row_height)
        self.l.setBuildFunc(self._build_row)

    def _build_row(self,title,identity):
        scroll_index = None
        if self._scroll_hidden_index is not None or self._scroll_full_index is not None:
            scroll_index = self.l.getCurrentSelectionIndex()
        if self._scroll_hidden_index is not None and scroll_index == self._scroll_hidden_index:
            title = ''
        elif self._title_for is not None:
            title = self._title_for(title, identity)
        title_flags = RT_HALIGN_LEFT | RT_VALIGN_CENTER
        if self._scroll_full_index is None or scroll_index != self._scroll_full_index:
            title_flags |= RT_ELLIPSIS
        gap=max(2,self._row_height//14);pad=self._padding
        height=max(1,self._row_height-2*gap)
        active=identity==self._active_identity
        background=self._selected if active else self._background
        content=self._title_for is not None
        # Enigma2 builds only visible rows. The stripe and outline are native
        # fills, shared across the category and content panes.
        row=[identity,
            _progress(pos=(0,gap),size=(self._row_width,height),
                percent=0,borderWidth=1,foreColor=background,
                foreColorSelected=self._border if content else self._selected,
                backColor=background,backColorSelected=self._selected),
            _progress(pos=(0,gap),size=(max(2,self._row_height//16),height),
                percent=100,borderWidth=0,foreColor=self._accent if active else background,
                foreColorSelected=self._accent,backColor=background,backColorSelected=self._selected),
            MultiContentEntryText(pos=self._title_box[:2],size=self._title_box[2:],
                font=0,flags=title_flags,text=title,
                color=self._accent if active else self._ink,
                color_sel=self._ink if content else self._accent,
                backcolor=background,backcolor_sel=self._selected)]
        if content:
            row.extend((
                _progress(pos=(0,self._row_height-1),size=(self._row_width,1),
                    percent=100,borderWidth=0,foreColor=self._separator,foreColorSelected=self._separator,
                    backColor=self._background,backColorSelected=self._background),
                MultiContentEntryText(pos=(self._row_width-2*pad,gap+1),size=(pad,max(1,height-2)),
                    font=0,flags=RT_HALIGN_RIGHT|RT_VALIGN_CENTER,text='›',
                    color=self._background,color_sel=self._accent,
                    backcolor=self._background,backcolor_sel=self._selected)))
        return row

    def scroll_selected_title(self, screen, index):
        rows = self.list
        if (not isinstance(index, int) or not 0 <= index < len(rows)
                or not callable(getattr(self.l, 'getCurrentSelectionIndex', None))):
            clear_selection(screen)
            return
        rectangle = native_list_row(self, index, self._title_box, self._row_height)
        if rectangle is None:
            clear_selection(screen)
            return
        title, identity = rows[index]
        if self._title_for is not None:
            title = self._title_for(title, identity)
        instance = self.instance

        def paint(hidden):
            self._scroll_hidden_index = index if hidden and self.list is rows else None
            if self.instance is instance and self.list is rows:
                redraw_native_row(self, index)

        def show_full(shown):
            self._scroll_full_index = index if shown and self.list is rows else None
            if self.instance is instance and self.list is rows:
                redraw_native_row(self, index)

        select_native(screen, self, rectangle, title, self._title_font,
                      self._ink if self._title_for is not None else self._accent,
                      key=(id(self), id(rows), index, identity), paint=paint,
                      show_full=show_full if RT_ELLIPSIS else None)

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

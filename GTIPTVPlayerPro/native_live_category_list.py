# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native list content for the existing Live TV category artwork."""
from pathlib import Path
from tempfile import TemporaryDirectory

import enigma
from Components.MenuList import MenuList
from Components.MultiContent import MultiContentEntryPixmap, MultiContentEntryPixmapAlphaBlend, MultiContentEntryText
from skin import parseColor

from .broadcast_layout import (CATEGORY_ICON_BOX, CATEGORY_LIST_BOX, CATEGORY_PAGE_SIZE,
                               CATEGORY_PANEL_BOX, CATEGORY_ROW_ART_BOX, CATEGORY_ROW_HEIGHT, INK, MUTED, art)
from .i18n import localized_upper
from .typography import fit_dynamic_text
from .utility_design import CANVAS, NativeSkin


_ICON_NAMES = ('tiles', 'sport', 'kids', 'news', 'tv')


def _category_icon(item, index):
    if index == 0 or str(item.category_id) in ('', 'all', '0'):
        return 'tiles'
    name = localized_upper(item.name)
    if 'SPOR' in name or 'SPORT' in name:
        return 'sport'
    if 'COCUK' in name or 'ÇOCUK' in name or 'KIDS' in name:
        return 'kids'
    if 'HABER' in name or 'NEWS' in name or 'BELGESEL' in name:
        return 'news'
    return 'tv'


def _prepare_artwork(rect, row_height, icon_size):
    # Compose the existing panel and row images once. The GUI can copy opaque
    # row tiles when paging instead of repeatedly blending the large overlays.
    from PIL import Image

    resampling = getattr(Image, 'Resampling', Image)

    def resized(name, size, filter_type):
        with Image.open(art(name)) as image:
            return image.convert('RGBA').resize(size, filter_type)

    panel_x, panel_y, panel_width, panel_height = rect(CATEGORY_PANEL_BOX)
    list_x, list_y, list_width, _height = rect(CATEGORY_LIST_BOX)
    x, y = list_x - panel_x, list_y - panel_y
    art_size = rect(CATEGORY_ROW_ART_BOX)[2:]
    normal = resized('category-row', art_size, resampling.NEAREST)
    focus = resized('category-focus', art_size, resampling.NEAREST)
    panel = Image.new('RGBA', (panel_width, panel_height), CANVAS)
    panel.alpha_composite(resized('wide-panel', panel.size, resampling.NEAREST))

    with TemporaryDirectory(prefix='gt-category-art-') as tmp:
        def load(image, name):
            path = Path(tmp) / (name + '.png')
            image.save(path, format='PNG', compress_level=0)
            pixmap = enigma.loadPNG(str(path))
            if pixmap is None:
                raise OSError('Unable to load prepared category artwork')
            return pixmap

        tiles = []
        for slot in range(CATEGORY_PAGE_SIZE):
            top = y + slot * row_height
            empty = panel.crop((x, top, x + list_width, top + row_height))
            row = empty.copy()
            row.alpha_composite(normal)
            selected = row.copy()
            selected.alpha_composite(focus)
            tiles.append(tuple(MultiContentEntryPixmap(size=image.size,
                               png=load(image.convert('RGB'), f'row-{slot}-{state}'))
                               for state, image in enumerate((empty, row, selected))))
        icons = {name: load(resized(name, icon_size, resampling.LANCZOS), name) for name in _ICON_NAMES}
    return tiles, icons


class NativeLiveCategoryList(MenuList):
    def __init__(self, width, height):
        super().__init__([], enableWrapAround=True, content=enigma.eListboxPythonMultiContent)
        rect = NativeSkin('GTContentBrowserScreen', width, height, None).rect
        row_height = rect((0, 0, 1, CATEGORY_ROW_HEIGHT))[3]
        self.l.setItemHeight(row_height)
        self._origin = rect(CATEGORY_LIST_BOX)[:2]
        x, y, w, h = rect(CATEGORY_ICON_BOX)
        icon_position = (x - self._origin[0], y - self._origin[1])
        icon_size = (w, h)
        try:
            self._tiles, pixmaps = _prepare_artwork(rect, row_height, icon_size)
            icon_flags = 0
        except (ImportError, OSError, ValueError):
            self._tiles = None
            pixmaps = {name: enigma.loadPNG(art(name)) for name in _ICON_NAMES}
            icon_flags = enigma.BT_SCALE
        self._icons = {
            name: MultiContentEntryPixmapAlphaBlend(pos=icon_position, size=icon_size,
                                                    png=pixmap, flags=icon_flags)
            for name, pixmap in pixmaps.items()
        }
        self._number_color = parseColor(MUTED).argb()
        self._name_color = parseColor(INK).argb()
        self._font_indices = {}
        self._entries = None
        self._style = None
        self._painted_selection = -1

    @property
    def has_opaque_rows(self):
        return self._tiles is not None

    def postWidgetCreate(self, instance):
        super().postWidgetCreate(instance)
        if hasattr(instance, 'allowNativeKeys'):
            instance.allowNativeKeys(False)

    def _text_entry(self, label, value, preferred, minimum, color):
        rendered = fit_dynamic_text(label, value, max_lines=1, preferred_size=preferred,
                                    min_size=minimum, fallback_chars=90, reuse_current=False)
        _instance, family, size = getattr(label, '_gt_typography_font_key', (None, 'Regular', preferred))
        font = (family, size)
        if font not in self._font_indices:
            index = len(self._font_indices)
            self.l.setFont(index, enigma.gFont(family, size))
            self._font_indices[font] = index
        position = label.instance.position()
        dimensions = label.instance.size()
        flags = enigma.RT_HALIGN_LEFT | enigma.RT_VALIGN_CENTER | getattr(enigma, 'RT_BLEND', 0)
        return MultiContentEntryText(pos=(position.x() - self._origin[0], position.y() - self._origin[1]),
                                     size=(dimensions.width(), dimensions.height()),
                                     font=self._font_indices[font], flags=flags, text=rendered, color=color)

    def _category_rows(self, entries, selected, number, name, fonts, minimum):
        rows = []
        for index, item in enumerate(entries):
            row = [index]
            if self._tiles is not None:
                row.append(self._tiles[index % CATEGORY_PAGE_SIZE][2 if index == selected else 1])
            row.extend((self._text_entry(number, '{:02d}'.format(index + 1), fonts[30], minimum, self._number_color),
                        self._icons[_category_icon(item, index)],
                        self._text_entry(name, item.name, fonts[33], minimum, self._name_color)))
            rows.append(row)
        remainder = len(rows) % CATEGORY_PAGE_SIZE
        if self._tiles is not None and remainder:
            rows.extend([None, tile[0]] for tile in self._tiles[remainder:])
        return rows

    def sync(self, screen, entries, selected, page_start):
        if self.instance is None:
            return
        number = screen['r101_category_measure_number']
        name = screen['r101_category_measure_name']
        if number.instance is None or name.instance is None:
            return
        fonts = screen._r101_category_fonts
        style = (id(self.instance), id(number.instance), id(name.instance), fonts[30], fonts[33])
        if entries is not self._entries or style != self._style:
            minimum = max(9, screen._seven_px(20))
            if self._tiles is not None:
                self.instance.setTransparent(False)
            self.setList(self._category_rows(entries, selected, number, name, fonts, minimum))
            self._entries, self._style = entries, style
        elif self._tiles is not None and selected >= 0 and selected != self._painted_selection:
            previous = self._painted_selection
            if previous >= 0:
                self.list[previous][1] = self._tiles[previous % CATEGORY_PAGE_SIZE][1]
            self.list[selected][1] = self._tiles[selected % CATEGORY_PAGE_SIZE][2]
        self._painted_selection = selected
        if selected >= 0:
            if self.getSelectedIndex() != selected:
                self.moveToIndex(selected)
            if hasattr(self.instance, 'getTopIndex') and self.instance.getTopIndex() != page_start:
                self.instance.setTopIndex(page_start)

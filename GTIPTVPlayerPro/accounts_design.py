# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""The approved account list, selected-account panel and readable TV guide."""
from xml.etree import ElementTree as ET
from .background import attach_native_alpha_pixmap
from .i18n import _, localized_upper
from .paths import plugin_path
from .remote_footer import remote_action_text
from .scrollbar import update_scrollbar, hide_scrollbar
from .typography import fit_dynamic_text, font_px
from .utility_design import art as utility_art
from .welcome_design import sentence_caption
from .ui_update import set_visible

STATES = ('neutral', 'green', 'yellow', 'red')
COLORS = {'neutral': '#D0DCEE', 'green': '#62EF91', 'yellow': '#F6D15B', 'red': '#FF8497'}


def art(name):
    return plugin_path('skin', 'images', 'accounts-r99', name + '.png')


def build(s):
    s.label('r99_gt', (60, 15, 78, 72), 60, '#20D5ED')
    s.label('r99_brand', (138, 19, 310, 68), 40)
    s.label('r99_header_divider', (470, 26, 2, 52), background='#3F9CBD')
    s.pic('r99_count_panel', (60, 98, 282, 52), art('badge'))
    s.label('count_badge', (69, 100, 264, 48), 31, align='center')
    s.pic('r99_page_panel', (366, 98, 236, 52), art('badge'))
    s.label('source_badge', (375, 100, 218, 48), 31, align='center')
    s.label('message', (637, 98, 650, 52), 29, '#AFC1D8')
    s.pic('r99_list_panel', (42, 166, 1276, 802), art('list-panel'), z=1)
    s.pic('r99_detail_panel', (1338, 166, 548, 802), art('detail-panel'), z=1)
    s.label('accounts', (88, 310, 1174, 320), 36, '#AFC1D8', z=4, lines=6)
    for i in range(7):
        y = 182 + i * 112
        s.pic('account_row_art_' + str(i), (51, y, 1256, 110), art('row'), z=2)
        s.pic('account_focus_art_' + str(i), (51, y, 1256, 110), art('focus'), z=3)
        s.label('row_number_' + str(i), (78, y + 19, 80, 65), 40, '#B7CAEB', align='center')
        s.label('row_' + str(i), (182, y + 9, 824, 50), 38)
        s.label('r99_separator_' + str(i), (47, y + 110, 1260, 1), background='#193D60', z=4)
        for suffix, color in (('', '#B7CAEB'), ('_green', '#B7CAEB'), ('_yellow', '#B7CAEB'), ('_red', '#B7CAEB')):
            s.label('row_detail_' + str(i) + suffix, (182, y + 60, 824, 40), 28, color)
            s.label('row_status_' + str(i) + suffix, (1038, y + 25, 240, 56), 28, COLORS[suffix[1:] or 'neutral'], align='center')
        for state in STATES:
            s.pic('row_status_art_{}_{}'.format(i, state), (1028, y + 24, 260, 60), art('status-' + state), z=4)
    s.label('account_scroll_track', (1322, 184, 5, 780), background='#1C4161')
    s.label('account_scroll_thumb', (1322, 184, 5, 100), background='#20D5ED', z=6)
    s.label('selected_caption', (1376, 189, 464, 64), 36, '#20D5ED')
    s.label('r99_detail_line_top', (1376, 248, 464, 2), background='#295179')
    s.pic('r99_source_icon', (1540, 310, 148, 148), art('server'), z=4)
    s.label('r99_selected_name', (1376, 460, 464, 72), 39, align='center', lines=2)
    for state in STATES:
        s.pic('r99_selected_badge_' + state, (1442, 537, 340, 60), art('status-' + state), z=3)
        s.label('r99_selected_status_' + state, (1454, 541, 316, 52), 32, COLORS[state], align='center')
    for i in range(3):
        y = 674 + i * 94
        s.label('r99_meta_line_' + str(i), (1376, y - 40, 464, 2), background='#20466B')
        s.label('r99_meta_caption_' + str(i), (1376, y, 195, 52), 28, '#AFC1D8')
        s.label('r99_meta_value_' + str(i), (1570, y, 270, 52), 28, align='right', lines=2)
    for i in range(7):
        s.label('r99_footer_sep_' + str(i), (0, 0, 1, 1), background='#315774', z=30)


def finish(root, rect, px):
    nodes = {n.get('name'): n for n in root}
    title = nodes['r97_title']
    x, y, w, h = rect((506, 15, 1340, 80))
    title.set('position', '{},{}'.format(x, y)); title.set('size', '{},{}'.format(w, h))
    title.set('font', 'Regular;{}'.format(font_px(px, 52)))
    nodes['r97_brand'].set('position', '0,0'); nodes['r97_brand'].set('size', '1,1')
    x, y, w, h = rect((1440, 104, 440, 40))
    nodes['r97_web_hint'].set('position', '{},{}'.format(x, y)); nodes['r97_web_hint'].set('size', '{},{}'.format(w, h))
    # A same-color native text outline gives headings/account names the
    # approved heavier weight without depending on a skin-specific bold font.
    for name,node in nodes.items():
        if name in ('r99_gt','r99_brand','r97_title','selected_caption','r99_selected_name') or (name.startswith('row_') and name[4:].isdigit()):
            node.set('borderWidth',str(px(1)))
            node.set('borderColor',node.get('foregroundColor','#EDF2F8'))


def footer_geometry(width, height, items):
    sx, sy = width / 1920.0, height / 1080.0
    def box(x, y, w, h):
        return round(x*sx), round(y*sy), max(1, round(w*sx)), max(1, round(h*sy))
    count = len(items); column = 1792.0 / count
    groups = []
    for i, item in enumerate(items):
        key = item[0]; x = 64 + i * column
        kw = {'up_down': 90, 'arrows': 100, 'channel': 100, 'ok': 88, 'exit': 96}.get(key, 42)
        colored = key in ('red', 'green', 'yellow', 'blue')
        groups.append(dict(key=box(x, 1010 if colored else 1006, kw, 42 if colored else 54),
                           caption=box(x+kw+18, 1005, max(30, column-kw-38), 58),
                           separator=box(x+column-12, 1007, 1, 46)))
    return groups


def style_footer(markup, items):
    root = ET.fromstring(markup);width, height = map(int, root.get('size').split(','))
    groups = footer_geometry(width, height, items)
    px = lambda n: max(1, round(n*min(width/1920.0, height/1080.0)))
    for node in root:
        name = node.get('name', '')
        if name.startswith(('remote_key_bg_', 'remote_key_', 'remote_action_', 'r99_footer_sep_')):
            i = int(name.rsplit('_', 1)[1])
            if i >= len(items):
                node.set('size', '1,1');node.set('position', '0,0');continue
            key = items[i][0]; colored = key in ('red', 'green', 'yellow', 'blue')
            if name.startswith('remote_action_'):
                box = groups[i]['caption'];node.set('font', 'Regular;{}'.format(font_px(px, 32 if len(items)<=7 else 28)))
            elif name.startswith('r99_footer_sep_'):
                box = groups[i]['separator'] if i < len(items)-1 else (0,0,1,1)
            else:
                box = groups[i]['key']
                if name.startswith('remote_key_bg_'):
                    node.set('pixmap', utility_art('dot-'+key) if colored else plugin_path('skin','images','discovery-r48','key-outline-r48.png'))
                else:
                    node.set('font','Regular;{}'.format(font_px(px, 30)))
            node.set('position','{},{}'.format(*box[:2]));node.set('size','{},{}'.format(*box[2:]));node.set('noWrap','1')
    return ET.tostring(root,encoding='unicode')


def _fit(screen, name, text, size=30, lines=1):
    return fit_dynamic_text(screen[name], str(text or ''), max_lines=lines,
                            preferred_size=font_px(screen._r97_px, size), min_size=max(9, screen._r97_px(20)), fallback_chars=80)


def _show(screen, name, value):
    set_visible(screen[name], value)


def refresh(screen, *, selection_only=False):
    from . import main
    count = len(screen.accounts);page_count = (count+screen.page_size-1)//screen.page_size if count else 0
    current = screen.selected_index//screen.page_size+1 if count else 0
    if getattr(screen, '_opaque_account_rows', None) is not None:
        start = screen.selected_index // screen.page_size * screen.page_size
        for slot in range(screen.page_size):
            index = start + slot
            screen._opaque_account_rows.paint(screen, slot, index < count, int(index == screen.selected_index))
    source = screen.accounts[screen.selected_index] if count else None
    result = screen._health_results.get(main._source_result_key(source)) if source else None
    status, signal = main._account_test_status_text(result) if result is not None else (_('Ready to connect'), 'neutral')
    signal = signal if signal in STATES else 'neutral'
    if not selection_only:
        _fit(screen, 'r99_gt', 'GT', 60)
        _fit(screen, 'r99_brand', 'IPTV PLAYER PRO', 40)
        noun = _('Source') if screen.source_type == 'm3u' else _('Account')
        _fit(screen, 'count_badge', '{} / {} {}'.format(count, main.SOURCE_LIMITS.get(screen.source_type, main.MAX_ACCOUNTS), noun.lower()), 31)
        _fit(screen, 'source_badge', '{} / {} {}'.format(current, page_count, _('Page').lower()), 31)
        _fit(screen, 'selected_caption', localized_upper(_('Selected source') if screen.source_type=='m3u' else _('Selected account')), 36)
    name = main._source_list_labels(source)[0] if source else _('No account selected')
    _fit(screen, 'r99_selected_name', name, 39, 2)
    kind = main._source_type(source) if source else screen.source_type
    protocol = 'Stalker / MAC' if kind=='stalker' else main._source_badge(source) if source else '—'
    expiry = main._account_test_expiry_text(result) if result is not None and kind!='m3u' else _('Unknown') if source and kind!='m3u' else '—'
    connections = main._account_test_connection_text(result) if result is not None and kind!='m3u' else _('Unknown') if source and kind!='m3u' else '—'
    if not selection_only:
        captions = (_('Protocol'), _('Expiry: {}').format('').rstrip(': '), _('Active / Maximum'))
        for i, caption in enumerate(captions):
            _fit(screen, 'r99_meta_caption_'+str(i), caption+':', 28)
    for i, value in enumerate((protocol, expiry, connections)):
        _fit(screen, 'r99_meta_value_'+str(i), value, 28, 2)
    for state in STATES:
        _show(screen, 'r99_selected_badge_'+state, state==signal)
        _show(screen, 'r99_selected_status_'+state, state==signal)
    _fit(screen, 'r99_selected_status_'+signal, sentence_caption(status) if source else '—', 32)
    icon = plugin_path('skin','images','utility-r96','folder.png') if kind=='m3u' else plugin_path('skin','images','utility-r96','portal.png') if kind=='stalker' else art('server')
    if getattr(screen, '_r99_source_icon_path', None)!=icon:
        screen._r99_source_icon_path=icon
        attach_native_alpha_pixmap(screen,'r99_source_icon',icon,key='r99_source_icon')
    if not selection_only:
        start = screen.selected_index//screen.page_size*screen.page_size
        for slot in range(screen.page_size):
            _show(screen, 'r99_separator_'+str(slot), start+slot<count)
        _show(screen,'accounts',not count)
    if count:
        cached=getattr(screen,'_r27_account_dimensions',None)
        if cached is None or cached[0] is not screen.skin:
            root=ET.fromstring(screen.skin);dimensions=tuple(map(int,root.get('size').split(',')))
            cached=screen._r27_account_dimensions=(screen.skin,dimensions)
        w,h=cached[1]
        update_scrollbar(screen,'account_scroll',count,screen.selected_index,screen.page_size,
                         (round(1322*w/1920.),round(184*h/1080.),max(2,round(5*w/1920.)),round(780*h/1080.)))
    else:hide_scrollbar(screen,'account_scroll')


def paint_footer(screen, items):
    for i,item in enumerate(items):
        key=item[0];colored=key in ('red','green','yellow','blue')
        _fit(screen,'remote_action_'+str(i),remote_action_text(item[1]),32 if len(items)<=7 else 28)
        if not colored:
            _fit(screen,'remote_key_'+str(i),{'up_down':'▲▼','arrows':'▲▼','channel':'CH±','ok':'OK','exit':'EXIT'}.get(key,key),30)
        _show(screen,'remote_key_'+str(i),not colored);_show(screen,'remote_key_bg_'+str(i),True)
    for i in range(7):_show(screen,'r99_footer_sep_'+str(i),i<len(items)-1)

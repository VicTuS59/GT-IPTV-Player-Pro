# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Shared native presentation for the twelve approved utility screens.

Controllers own all account, network, download and archive operations. This
module only lays out their existing components and reflects their current data.
"""
from functools import wraps
from types import MethodType
import re
from xml.etree import ElementTree as ET

from .background import attach_native_alpha_pixmap
from .channel_highlight import saved_selection_palette
from .episode_design import episode_focus_assets
from .i18n import _
from .paths import plugin_path
from .remote_footer import remote_action_text
from .typography import fit_dynamic_text, font_px
from .utility_design import NativeSkin, bind_missing, plain_footer
from .vod_discovery import style_footer
from .welcome_design import sentence_caption

TITLES = {'accounts': 'Accounts', 'epg': 'DVB SATELLITE EPG',
          'appearance': 'Appearance', 'subtitles': 'Subtitles',
          'playlist': 'Select playlist', 'downloads': 'Downloads',
          'weather': 'Weather', 'web': 'Web Interface',
          'youtube': 'YouTube settings', 'backup': 'Backup dashboard',
          'restore': 'Restore from backup', 'account': 'Add New Account'}
INK = '#EDF2F8'
MUTED = '#AFC1D8'
CYAN = '#20D5ED'


def asset(name):
    return plugin_path('skin', 'images', 'utility-r97', name + '.png')


def _card(s, name, box, focus=True):
    s.pic('r97_card_' + name, box, asset('card'), z=1)
    if focus:
        s.pic('r97_focus_' + name, box, asset('focus'), z=3)


def _panel(s, icon='settings', title='r97_help_title'):
    s.pic('r97_help_panel', (1276, 192, 580, 664), asset('panel'), z=1)
    s.label('r97_help_caption', (1312, 215, 500, 56), 29, CYAN)
    s.label('r97_help_divider', (1312, 285, 500, 2), background='#29435E')
    s.pic('r97_help_icon', (1320, 325, 128, 128), asset(icon))
    s.label(title, (1312, 480, 500, 106), 36, lines=2)
    s.label('r97_help_text', (1312, 597, 500, 128), 28, MUTED, lines=4)
    s.label('r97_value_caption', (1312, 738, 500, 36), 23, MUTED)
    s.pic('r97_value_panel', (1312, 784, 500, 64), asset('field'))
    s.label('r97_help_value', (1330, 788, 464, 56), 29, lines=2)


def _settings(s, kind):
    count = {'epg': 6, 'weather': 4, 'appearance': 6, 'youtube': 3, 'account': 4}[kind]
    top = 212
    step = 108 if kind in ('epg', 'appearance') else 132
    h = step - 16
    for i in range(count):
        y = top + i * step
        _card(s, str(i), (64, y, 1168, h))
        lp, vp = ('row_label_', 'row_value_') if kind == 'appearance' else ('label_', 'value_')
        if kind == 'account':
            s.label('r97_field_label_' + str(i), (100, y + 12, 330, h - 24), 33)
            s.label('r97_field_value_' + str(i), (452, y + 12, 744, h - 24), 32, MUTED)
        else:
            split = 685 if kind in ('appearance', 'epg') else 530
            s.label(lp + str(i), (100, y + 9, split - 126, h - 18), 30, lines=2)
            s.label(vp + str(i), (split, y + 9, 1196 - split, h - 18), 28, CYAN, align='right', lines=2)
            from .utility_design import art
            badge = (1004, y + (h - 52) // 2, 180, 52)
            for state in ('on', 'off'):
                s.pic('r97_state_{}_{}'.format(state, i), badge, art('state-' + state), z=2)
            s.label('r97_state_text_' + str(i), (1010, badge[1], 168, 52), 27, INK, align='center')
    s.label('message', (66, 892, 1780, 76), 25, MUTED, lines=2)
    _panel(s, {'weather': 'location', 'youtube': 'youtube', 'account': 'globe', 'epg': 'browser'}.get(kind, 'settings'))
    if kind == 'appearance':
        for name, row in (('channel_color_swatch', 3), ('selection_border_swatch', 4)):
            s.pic(name, (728, top + row * step + 25, 88, 40), z=6)
        s.pic('r97_preview_fill', (1316, 593, 496, 64), asset('card'), z=2)
        s.pic('r97_preview_border', (1316, 593, 496, 64), asset('focus'), z=3)
        s.label('r97_preview_selected', (1340, 597, 440, 56), 29)
        s.label('r97_preview_normal', (1340, 665, 440, 56), 27, MUTED)
    if kind == 'epg':
        s.label('r97_epg_count', (1312, 548, 500, 98), 44, CYAN, lines=2)
        s.label('r97_epg_update', (1312, 650, 500, 100), 26, MUTED, lines=3)


def _accounts(s):
    from .accounts_design import build
    build(s)


def _playlist(s):
    s.label('instruction', (250, 185, 1430, 62), 32, MUTED)
    s.label('step', (1400, 112, 440, 64), 27, MUTED, align='right')
    for i in range(2):
        y = 315 + i * 240
        _card(s, str(i), (235, y, 1450, 202))
        s.pic('r97_playlist_icon_' + str(i), (285, y + 36, 128, 128), asset('browser' if i == 0 else 'globe'))
        s.label('title_' + str(i), (475, y + 26, 1100, 66), 41)
        s.label('description_' + str(i), (475, y + 103, 1100, 70), 29, MUTED, lines=2)
    s.label('message', (250, 867, 1430, 75), 27, MUTED, lines=2)


def _subtitles(s):
    s.label('status_badge', (1415, 120, 440, 54), 23, CYAN, align='right')
    s.label('settings_title', (76, 167, 990, 48), 28, CYAN)
    s.label('appearance_title', (76, 690, 990, 48), 28, CYAN)
    for i in range(16):
        y = 222 + i * 41 if i < 11 else 745 + (i - 11) * 41
        _card(s, str(i), (64, y, 1006, 38))
        s.label('label_' + str(i), (83, y, 597, 38), 24)
        s.label('value_' + str(i), (688, y, 355, 38), 23, MUTED, align='right')
    s.pic('r97_preview_panel', (1100, 178, 756, 790), asset('panel'))
    s.label('preview_title', (1130, 198, 680, 58), 30, CYAN)
    s.label('preview_caption', (1130, 259, 680, 38), 24, MUTED)
    s.label('screen_frame', (1116, 319, 724, 514), background='#29435E', z=2)
    s.label('screen_body', (1120, 323, 716, 506), background='#020A14', z=3)
    s.label('preview_text', (1120, 690, 716, 129), 40, '#FDE047', z=5, align='center', lines=2)
    s.label('preview_detail', (1130, 842, 680, 45), 23, MUTED, align='center')
    s.label('message', (1130, 898, 680, 60), 23, CYAN, lines=2)


def _downloads(s):
    s.label('summary', (64, 130, 1250, 56), 28, MUTED)
    s.label('free', (1390, 130, 466, 56), 28, MUTED, align='right')
    s.label('page', (1020, 188, 212, 40), 24, MUTED, align='right')
    s.pic('r97_download_panel', (1276, 212, 580, 658), asset('panel'))
    s.label('detail_heading', (1312, 230, 500, 54), 30)
    s.label('poster_placeholder', (1420, 311, 276, 235), 52, CYAN, background='#102844', align='center')
    s.pic('poster', (1420, 311, 276, 235), z=4)
    s.label('detail_title', (1312, 570, 500, 112), 33, lines=3)
    s.label('detail', (1312, 701, 500, 154), 25, MUTED, lines=5)
    for i in range(5):
        y = 238 + i * 124
        _card(s, str(i), (64, y, 1168, 114))
        s.label('placeholder_' + str(i), (84, y + 15, 144, 84), 32, CYAN, background='#102844', align='center')
        s.pic('thumb_' + str(i), (84, y + 15, 144, 84), z=4)
        s.label('name_' + str(i), (254, y + 7, 580, 58), 30, lines=2)
        s.label('meta_' + str(i), (254, y + 75, 580, 32), 22, MUTED)
        s.label('status_' + str(i), (860, y + 8, 350, 56), 27, CYAN, align='right', lines=2)
        s.pic('progress_' + str(i), (860, y + 84, 260, 10), z=5)
        s.label('percent_' + str(i), (1127, y + 70, 85, 37), 23, MUTED, align='right')
    s.pic('r97_folder_panel', (64, 882, 1792, 56), asset('field'))
    s.label('folder', (88, 887, 1744, 44), 25, MUTED)
    s.label('message', (76, 942, 1756, 34), 22, CYAN)
    s.label('arrow_up', (1234, 227, 30, 40), 24, MUTED, align='center')
    s.label('arrow_down', (1234, 804, 30, 40), 24, MUTED, align='center')


def _web(s):
    s.pic('r97_connection_panel', (64, 170, 1184, 770), asset('panel'))
    s.pic('r97_globe', (590, 211, 135, 135), asset('globe'))
    s.pic('r97_status_panel', (400, 383, 528, 70), asset('field'))
    s.label('status', (418, 389, 492, 58), 33, align='center')
    s.label('address_title', (150, 488, 1012, 48), 30, align='center')
    s.pic('r97_address_panel', (190, 548, 932, 88), asset('field'))
    s.label('address', (212, 554, 888, 76), 34, align='center', lines=2)
    s.label('code_title', (150, 667, 1012, 48), 30, align='center')
    s.pic('r97_code_panel', (350, 735, 620, 112), asset('field'))
    s.pic('r97_code_frame', (350, 735, 620, 112), asset('focus'), z=3)
    s.label('code', (370, 741, 580, 100), 64, align='center')
    s.label('security', (100, 874, 1110, 46), 24, MUTED, align='center')
    s.pic('r97_help_panel', (1276, 170, 580, 770), asset('panel'))
    s.label('r97_help_caption', (1312, 212, 500, 68), 29, CYAN)
    s.pic('r97_help_icon', (1445, 335, 210, 210), asset('browser'))
    s.label('detail', (1312, 623, 500, 226), 31, MUTED, lines=6)


def _backup(s, restore=False):
    s.label('intro', (64, 115, 1792, 56), 30, MUTED)
    s.pic('r97_path_panel', (64, 192, 1792, 100), asset('field'))
    s.pic('r97_path_icon', (90, 210, 66, 66), asset('file'))
    s.label('path_caption', (184, 198, 1370, 37), 25, MUTED)
    s.label('path', (184, 239, 1370, 43), 30)
    s.label('path_action', (1600, 213, 220, 57), 27, CYAN, align='right')
    top, step, h = (365, 190, 176) if restore else (326, 206, 190)
    icons = ('account', 'settings', 'favorite', 'history', 'subtitles', 'file')
    for i in range(6):
        x, y = 64 + (i % 2) * 606, top + (i // 2) * step
        s.pic('r97_card_' + str(i), (x, y, 586, h), asset('card'))
        s.pic('card_focus_' + str(i), (x, y, 586, h), asset('focus'), z=3)
        s.pic('card_check_' + str(i), (x + 24, y + 24, 34, 34), z=5)
        s.pic('r97_group_icon_' + str(i), (x + 78, y + 46, 98, 98), asset(icons[i]), z=4)
        s.label('card_title_' + str(i), (x + 198, y + 14, 368, 65), 28, lines=2)
        s.label('card_description_' + str(i), (x + 198, y + 85, 254, 79), 23, MUTED, lines=3)
        s.label('card_size_' + str(i), (x + 454, y + 108, 110, 48), 26, CYAN, align='right')
        if restore:
            s.pic('card_disabled_' + str(i), (x, y, 586, h), asset('shade'), z=2)
    s.pic('r97_summary_panel', (1296, top, 560, (980 if restore else 940) - top), asset('panel'))
    s.label('summary_title', (1330, top + 18, 490, 66), 35, lines=2)
    s.label('selection_count', (1330, top + 98, 490, 45), 29, CYAN)
    s.label('r97_summary_line', (1330, top + 157, 490, 2), background='#29435E')
    s.label('selected_size', (1330, top + 172, 490, 92), 64, CYAN)
    s.label('size_caption', (1330, top + 271, 490, 54), 27, MUTED, lines=2)
    s.pic('r97_disk_icon', (1330, top + 350, 80, 80), asset('shield' if restore else 'disk'))
    s.label('free_caption', (1432, top + 342, 386, 47), 27)
    s.label('free_space', (1432 if not restore else 1330, top + 391, 386 if not restore else 490, 58), 41 if not restore else 25, lines=2)
    if restore:
        s.label('archive_date', (70, 304, 480, 45), 25, MUTED)
        s.label('archive_version', (560, 304, 400, 45), 25, MUTED)
        s.label('archive_verified', (1000, 304, 840, 45), 25, CYAN, align='right')
        s.pic('r97_safety_panel', (1330, 822, 490, 47), asset('field'))
        s.label('safety_action', (1350, 823, 450, 43), 25, '#FFD239')
        s.label('backup_hint', (1330, 873, 490, 42), 21, MUTED, lines=2)
        button_y = 919
    else:
        s.label('backup_hint', (1330, 798, 490, 60), 24, '#57D896', lines=2)
        button_y = 865
    s.pic('r97_backup_button_art', (1330, button_y, 490, 52 if restore else 64), asset('button'))
    s.pic('r97_backup_button_disabled', (1330, button_y, 490, 52 if restore else 64), asset('button-disabled'), z=3)
    s.label('backup_button', (1340, button_y, 470, 52 if restore else 64), 31, z=4, align='center')
    s.pic('operation_panel', (160, 313, 1600, 610), asset('panel'), z=8)
    s.label('detail', (230, 351, 1460, 438), 28, z=10, lines=14)
    s.pic('progress', (230, 835, 1460, 18), z=10)
    s.label('message', (68, 946, 1160, 32), 22, MUTED, z=10)


def restyle(markup, kind, width=None, height=None, px=None):
    old = ET.fromstring(markup)
    if width is None:
        width, height = map(int, old.get('size').split(','))
    factor = min(width / 1920.0, height / 1080.0)
    px = px or (lambda n: max(1, round(n * factor)))
    s = NativeSkin(old.get('name'), width, height, px)
    s.label('r97_title', (64, 34, 1300, 80), 48)
    s.label('r97_brand', (1400, 36, 456, 70), 28, CYAN, align='right')
    s.label('r97_footer_line', (48, 990, 1824, 2), background='#29435E', z=30)
    s.label('footer', (0, 994, 1920, 86), 20, z=30)
    if kind != 'web':
        s.label('r97_web_hint', (1410, 163 if kind in ('backup', 'restore') else 957, 446, 28), 19, MUTED, align='right')
    if kind in ('epg', 'weather', 'appearance', 'youtube', 'account'):
        _settings(s, kind)
    elif kind == 'accounts':
        _accounts(s)
    elif kind == 'playlist':
        _playlist(s)
    elif kind == 'subtitles':
        _subtitles(s)
    elif kind == 'downloads':
        _downloads(s)
    elif kind == 'web':
        _web(s)
    else:
        _backup(s, restore=kind == 'restore')
    root = ET.fromstring(s.finish())
    nodes = {n.get('name'): n for n in root}
    if kind == 'accounts':
        from .accounts_design import finish
        finish(root, s.rect, px)
    if kind == 'epg':
        nodes['r97_help_title'].set('size', '{},{}'.format(*s.rect((0, 0, 500, 55))[2:]))
    for node in old:
        name = node.get('name', '')
        if name in nodes:
            target = nodes[name]
            if name.startswith('card_check_'):
                target.set('pixmaps', ','.join(asset(n) for n in ('unchecked', 'checked', 'disabled')))
            if name == 'progress' or name.startswith('progress_'):
                target.set('foregroundColor', CYAN)
                target.set('backgroundColor', '#203C58')
                target.set('borderWidth', '0')
                target.attrib.pop('alphatest', None)
                target.attrib.pop('scale', None)
            continue
        if not name.startswith('remote_'):
            node.set('position', '0,0'); node.set('size', '1,1'); node.set('zPosition', '-40')
            for key in ('pixmap', 'pixmaps', 'backgroundColor'):
                node.attrib.pop(key, None)
            node.set('transparent', '1')
        root.append(node)
    return ET.tostring(root, encoding='unicode')


def skin_after(kind):
    def decorated(factory):
        @wraps(factory)
        def skin(*args, **kwargs):
            markup = factory(*args, **kwargs)
            # DVB automation shares this factory with the approved EPG page.
            # Retain its own labels, row count and controller-specific layout.
            if kind == 'epg' and ET.fromstring(markup).get('name') != 'GTDVBEPGSettingsScreen':
                return markup
            return restyle(markup, kind)
        return skin
    return decorated


def setup(screen, kind, items):
    screen._r97_kind = kind
    screen._r97_items = tuple(items)
    width, height = map(int, ET.fromstring(screen.skin).get('size').split(','))
    screen._r97_px = lambda n: max(1, round(n * min(width / 1920.0, height / 1080.0)))
    screen._r97_palette = None
    bind_missing(screen)
    screen.skin = style_footer(screen.skin, 'movie', screen._r97_px, items,
                              tuple(remote_action_text(item[1]) for item in items))
    if kind == 'accounts':
        from .accounts_design import style_footer as style_account_footer
        screen.skin = style_account_footer(screen.skin, items)
    root = ET.fromstring(screen.skin)
    for node in root:
        if node.get('name', '').startswith('remote_key_') and node.get('font'):
            node.set('noWrap', '1')
    screen.skin = ET.tostring(root, encoding='unicode')
    # OpenPLi dispatches only bound methods as Python callbacks here; plain
    # functions/lambdas are passed to exec() as legacy skin code strings.
    screen._utility_refresh_callback = MethodType(refresh, screen)
    screen.onLayoutFinish.append(screen._utility_refresh_callback)
    if hasattr(screen, 'onShown'):
        screen.onShown.append(screen._utility_refresh_callback)


def refresh_after(method):
    @wraps(method)
    def painted(screen, *args, **kwargs):
        result = method(screen, *args, **kwargs)
        if hasattr(screen, '_r97_kind'):
            refresh(screen)
        return result
    return painted


def _text(screen, name, value, lines=1, size=None):
    if name not in screen:
        return
    kw = dict(max_lines=lines)
    if size:
        kw.update(preferred_size=font_px(screen._r97_px, size), min_size=max(9, screen._r97_px(18)))
    fit_dynamic_text(screen[name], str(value or ''), **kw)


def _show(screen, name, visible):
    if name in screen:
        (screen[name].show if visible else screen[name].hide)()


def refresh(screen):
    if not hasattr(screen, '_r97_kind'):
        return
    kind = screen._r97_kind
    px = screen._r97_px
    title = screen._heading() if kind == 'accounts' else _(TITLES[kind])
    if kind == 'downloads' and screen._history_mode:
        title = _('Download history')
    acronyms = dict(dvb='DVB', epg='EPG', iptv='IPTV', xtream='Xtream', codes='Codes', youtube='YouTube', api='API', m3u='M3U', mac='MAC')
    title = re.sub(r'\b(?:dvb|epg|iptv|xtream|codes|youtube|api|m3u|mac)\b', lambda m: acronyms[m[0].lower()], sentence_caption(title), flags=re.I)
    _text(screen, 'r97_title', title, size=48)
    _text(screen, 'r97_brand', 'GT IPTV PLAYER PRO', size=28)
    _text(screen, 'r97_web_hint', _('Hold MENU for 3 seconds: Web interface'), size=19)
    index = getattr(screen, 'selected_field', 0) if kind == 'account' else getattr(screen, 'selected_index', 0)
    palette = (screen.channel_highlight, screen.selection_border) if kind == 'appearance' else saved_selection_palette()
    if palette != screen._r97_palette:
        screen._r97_palette = palette
        fill, border = episode_focus_assets(*palette)
        names = [name for name in screen if name.startswith('r97_focus_')]
        if kind == 'appearance':
            names.append('r97_preview_border')
            attach_native_alpha_pixmap(screen, 'r97_preview_fill', fill, key='r97_preview_fill')
        for name in names:
            attach_native_alpha_pixmap(screen, name, border, key=name, show_on_load=False)
    for name in screen:
        if name.startswith('r97_focus_'):
            slot = int(name.rsplit('_', 1)[1])
            chosen = (screen._start + slot == screen.index and screen._start + slot < len(screen._entries)) if kind == 'downloads' else slot == index
            _show(screen, name, chosen)
        if name.startswith('remote_web_hint'):
            screen[name].hide()
    if kind in ('epg', 'weather', 'appearance', 'youtube', 'account'):
        _refresh_settings(screen, kind, index)
    elif kind == 'playlist':
        for i in range(2):
            _text(screen, 'title_' + str(i), 'IPTV / XTREAM' if i == 0 else 'STALKER / MAC', size=41)
    elif kind == 'subtitles':
        _text(screen, 'header', '', size=1)
    elif kind == 'accounts':
        from .accounts_design import refresh as refresh_accounts
        refresh_accounts(screen)
    elif kind == 'web':
        _text(screen, 'r97_help_caption', _('Temporary local web session'), lines=2, size=29)
    if kind == 'downloads':
        for i in range(5):
            _show(screen, 'r97_card_' + str(i), screen._start + i < len(screen._entries))
    elif kind in ('backup', 'restore'):
        disabled = kind == 'restore' and (not screen.manifest or not screen.selected_groups)
        _show(screen, 'r97_backup_button_disabled', disabled)
        _show(screen, 'r97_backup_button_art', not disabled)
        if kind == 'restore' and not screen.manifest:
            for i in range(6):
                _text(screen, 'card_description_' + str(i), _('No backup file selected'), lines=3, size=23)
    items = tuple(getattr(screen, '_remote_footer_items', screen._r97_items))
    if len(items) != len(screen._r97_items):
        items = screen._r97_items
    plain_footer(screen, items, px)
    for i, item in enumerate(items):
        key = item[0]
        if key in ('arrows', 'channel'):
            _text(screen, 'remote_key_' + str(i), '▲▼◀▶' if key == 'arrows' else 'CH±', size=22)
    if kind == 'accounts':
        from .accounts_design import paint_footer
        paint_footer(screen, items)
    # State changes use the standard footer controller; the native renderer is
    # restyled after those updates so MENU and Details/Play stay in sync.


def _refresh_settings(screen, kind, index):
    if kind == 'account':
        labels = (_('Server URL'), _('Username'), _('Password'), _('Output format'))
        values = [screen._field_values[0] or 'http://', screen._field_values[1] or _('Enter the username'),
                  '*' * min(len(screen._field_values[2]), 24) if screen._field_values[2] else _('Enter the password'),
                  '‹  ' + screen._field_values[3].upper() + '  ›']
        for i in range(4):
            _text(screen, 'r97_field_label_' + str(i), labels[i], size=33)
            _text(screen, 'r97_field_value_' + str(i), values[i], size=32)
        title = labels[index]
        help_text = (_('Enter the server URL supplied by your provider.'), _('Enter the username'),
                     _('Enter the password'), _('Select a TS or M3U8 output supported by the server.'))[index]
        value = _('Press OK to edit') if index < 3 else screen._field_values[3].upper()
    else:
        lp, vp = ('row_label_', 'row_value_') if kind == 'appearance' else ('label_', 'value_')
        title = screen[lp + str(index)].getText()
        value = screen[vp + str(index)].getText()
        help_text = _('Use LEFT / RIGHT to change the value.')
        for i in range(len(screen.LABELS)):
            _text(screen, lp + str(i), screen[lp + str(i)].getText(), lines=2, size=30)
            _text(screen, vp + str(i), screen[vp + str(i)].getText(), lines=2, size=28)
            enabled = (screen.settings.enabled if i == 0 else None) if kind in ('weather', 'epg') else (
                screen.cinematic_view if i == 1 else screen.show_in_main_menu if i == 2 else None) if kind == 'appearance' else (
                screen.settings.youtube_autoplay if i == 1 else None)
            for state in ('on', 'off'):
                _show(screen, 'r97_state_{}_{}'.format(state, i), enabled is not None and (state == 'on') == bool(enabled))
            _show(screen, 'r97_state_text_' + str(i), enabled is not None)
            _show(screen, vp + str(i), enabled is None)
            if enabled is not None:
                _text(screen, 'r97_state_text_' + str(i), screen[vp + str(i)].getText(), size=27)
        if kind == 'weather':
            _text(screen, 'value_1', screen.settings.location_display_name, lines=2, size=28)
            help_text = _('Press OK on the City row to open the keyboard.') if index == 1 else _('Weather display settings')
            title = screen.settings.location_display_name
            value = '°' + screen.settings.unit
        elif kind == 'youtube':
            help_text = (_('Select the video playback quality.'), _('Automatically play the next video.'),
                         _('Search language follows the interface language.'))[index]
        elif kind == 'appearance':
            _text(screen, 'r97_preview_selected', _('Selected item'), size=29)
            _text(screen, 'r97_preview_normal', _('Normal text'), size=27)
            _show(screen, 'r97_help_text', False)
        elif kind == 'epg':
            title = _('EPG information')
            _text(screen, 'r97_epg_count', _('{} channels · {} events').format(screen.settings.last_mapping_count, screen.settings.last_event_count), lines=2, size=36)
            _text(screen, 'r97_epg_update', screen._summary_text(), lines=3, size=26)
            _show(screen, 'r97_help_text', False)
            _show(screen, 'r97_help_value', False)
            _show(screen, 'r97_value_panel', False)
            _show(screen, 'r97_value_caption', False)
    _text(screen, 'r97_help_caption', _('Field information') if kind == 'account' else _('Location information') if kind == 'weather' else _('Setting information'), size=29)
    _text(screen, 'r97_help_title', title, lines=2, size=36)
    _text(screen, 'r97_help_text', help_text, lines=4, size=28)
    _text(screen, 'r97_value_caption', _('Selected value'), size=23)
    _text(screen, 'r97_help_value', value, lines=2, size=29)

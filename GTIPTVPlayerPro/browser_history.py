# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""history; loaded when its screen is needed."""

import threading
import time
from collections import deque
from .ui_update import set_visible
from .typography import set_text_if_changed
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import ePoint, eSize, eTimer, getDesktop, iServiceInformation
from .seven_presentation import skin_after as seven_skin_after, refresh_after as seven_refresh_after, setup as setup_seven, poster_after, refresh_media
from .content import ContentItem, ContentError
from .diagnostics import log_event
from .i18n import N_, _, localized_upper, metadata_language
from .background import attach_pixmap
from .metadata import MetadataError, TMDbMetadataClient
from .vod_grid import LatestPosterWorkers
from .favorites import FavoriteStore, favorite_key
from .playback import PlaybackResumeStore, resume_account_scope, resume_key
from .persistence import queue_playback_clear
from .remote_footer import prepared_skin, set_remote_footer
from .scrollbar import hide_scrollbar, update_scrollbar
from .settings import load_player_settings
from .typography import ellipsize_dynamic_text, set_text_if_changed
from .browser_common import APP_BACKGROUND_PATH, CONTINUE_FOOTER_ITEMS, CONTINUE_POSTER_WORKERS, FAVORITES_FOOTER_ITEMS, FAVORITES_MOVE_FOOTER_ITEMS, FAVORITE_STAR_PATH, GTAsyncListScreen, PICON_DOWNLOAD_TIMEOUT, WORKER_POSTER_TIMEOUT_SECONDS, _DownloadSupport, _WorkerToken, _attach_subtitle_detail_metadata, _bounded_client_call, _cached_picon_path, _cancel_worker_token, _connect_timer, _debug, _debug_identifier, _download_client_image, _is_m3u_client, _is_stalker_client, _picon_cache_path, _safe_picon_url, _scale, _series_provider_id, content_client_for

@seven_skin_after('continue')
def _continue_watching_skin(screen_name='GTContinueWatchingScreen'):
    """Poster-card grid for every unfinished movie and series episode."""
    width, height, px = _scale()
    variant = {(720, 576): 'sd576', (720, 480): 'sd480', (640, 480): 'sd640'}.get((width, height))
    if variant is None:
        variant = '720' if width <= 1280 and height <= 720 else '1080' if width <= 1920 and height <= 1080 else '2160'
    art_dir = APP_BACKGROUND_PATH.rsplit('/', 1)[0]
    favorite_star = FAVORITE_STAR_PATH.replace('&', '&amp;').replace('"', '&quot;')
    margin = px(64)
    footer_y = height - px(92)
    panel_y = px(112)
    panel_h = px(828)
    grid_x = px(86)
    grid_y = (px(215), px(545))
    card_w = px(420)
    card_h = px(305)
    card_gap = px(22)
    poster_h = px(188)
    progress_x_pad = px(18)
    progress_w = card_w - progress_x_pad * 2
    progress_h = px(9)
    focus_h = px(4)
    cards = []
    for index in range(8):
        row, column = divmod(index, 4)
        card_x = grid_x + column * (card_w + card_gap)
        card_y = grid_y[row]
        cards.append('\n    <widget name="continue_card_{index}" position="{card_x},{card_y}"\n            size="{card_w},{card_h}" pixmap="{card_pixmap}"\n            scale="1" zPosition="2" />\n    <widget name="continue_poster_placeholder_{index}"\n            position="{poster_x},{poster_y}" size="{poster_w},{poster_h}"\n            font="Regular;{placeholder_font}" foregroundColor="#4B6485"\n            transparent="1" zPosition="3"\n            valign="center" halign="center" />\n    <widget name="continue_poster_{index}"\n            position="{poster_x},{poster_y}" size="{poster_w},{poster_h}"\n            alphatest="blend" scale="1" zPosition="4" />\n    <widget name="continue_favorite_{index}"\n            position="{favorite_x},{favorite_y}"\n            size="{favorite_size},{favorite_size}"\n            pixmap="{favorite_star}" alphatest="blend" scale="1"\n            zPosition="7" />\n    <widget name="continue_type_{index}" position="{type_x},{type_y}"\n            size="{type_w},{type_h}" font="Regular;{type_font}"\n            foregroundColor="#22D3EE" backgroundColor="#152746"\n            transparent="0" zPosition="6" valign="center" halign="center" />\n    <widget name="continue_title_{index}" position="{text_x},{title_y}"\n            size="{text_w},{title_h}" font="Regular;{title_font}"\n            foregroundColor="#FFFFFF" transparent="1" zPosition="5"\n            valign="center" halign="left" noWrap="1" />\n    <widget name="continue_meta_{index}" position="{text_x},{meta_y}"\n            size="{text_w},{meta_h}" font="Regular;{meta_font}"\n            foregroundColor="#AEBBCD" transparent="1" zPosition="5"\n            valign="center" halign="left" />\n    <widget name="continue_progress_bg_{index}"\n            position="{progress_x},{progress_y}" size="{progress_w},{progress_h}"\n            font="Regular;1" backgroundColor="#334155"\n            transparent="0" zPosition="5" />\n    <widget name="continue_progress_fill_{index}"\n            position="{progress_x},{progress_y}" size="{progress_w},{progress_h}"\n            font="Regular;1" backgroundColor="#22D3EE"\n            transparent="0" zPosition="6" />\n    <widget name="continue_progress_text_{index}"\n            position="{text_x},{progress_text_y}" size="{text_w},{progress_text_h}"\n            font="Regular;{progress_font}" foregroundColor="#E2E8F0"\n            transparent="1" zPosition="5" valign="center" halign="center" />\n    <widget name="continue_focus_{index}_top" position="{focus_x},{focus_y}"\n            size="{focus_w},{focus_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="8" />\n    <widget name="continue_focus_{index}_bottom"\n            position="{focus_x},{focus_bottom_y}" size="{focus_w},{focus_h}"\n            font="Regular;1" backgroundColor="#D946EF"\n            transparent="0" zPosition="8" />\n    <widget name="continue_focus_{index}_left" position="{focus_x},{focus_y}"\n            size="{focus_h},{card_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="8" />\n    <widget name="continue_focus_{index}_right"\n            position="{focus_right_x},{focus_y}" size="{focus_h},{card_h}"\n            font="Regular;1" backgroundColor="#D946EF"\n            transparent="0" zPosition="8" />'.format(index=index, card_x=card_x, card_y=card_y, card_w=card_w, card_h=card_h, card_pixmap='{}/screen-continue-glass-r91-tile-{}-{:02d}.png'.format(art_dir, variant, index).replace('&', '&amp;'), poster_x=card_x + px(6), poster_y=card_y + px(6), poster_w=card_w - px(12), poster_h=poster_h, favorite_x=card_x + card_w - px(58), favorite_y=card_y + px(14), favorite_size=px(42), favorite_star=favorite_star, placeholder_font=px(28), type_x=card_x + px(16), type_y=card_y + px(16), type_w=px(100), type_h=px(38), type_font=px(20), text_x=card_x + px(18), text_w=card_w - px(36), title_y=card_y + px(194), title_h=px(48), title_font=px(28), meta_y=card_y + px(236), meta_h=px(30), meta_font=px(19), progress_x=card_x + progress_x_pad, progress_y=card_y + px(267), progress_w=progress_w, progress_h=progress_h, progress_text_y=card_y + px(278), progress_text_h=px(25), progress_font=px(19), focus_x=card_x, focus_y=card_y, focus_w=card_w, focus_h=focus_h, focus_bottom_y=card_y + card_h - focus_h, focus_right_x=card_x + card_w - focus_h))
    return '\n<screen name="{screen_name}" position="0,0" size="{width},{height}"\n        flags="wfNoBorder" backgroundColor="#050914">\n    <widget name="app_bg" position="0,0" size="{width},{height}"\n            zPosition="0" />\n    <widget name="top_accent" position="0,0" size="{width},{accent_h}"\n            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />\n    <widget name="header" position="{margin},{header_y}"\n            size="{header_w},{header_h}" font="Regular;{header_font}"\n            foregroundColor="#FFFFFF" transparent="1"\n            valign="center" halign="left" />\n    <widget name="screen_label" position="{screen_x},{header_y}"\n            size="{screen_w},{header_h}" font="Regular;{screen_font}"\n            foregroundColor="#AEBBCD" transparent="1"\n            valign="center" halign="right" />\n    <widget name="content_panel" position="{margin},{panel_y}"\n            size="{panel_w},{panel_h}" font="Regular;1"\n            transparent="1" zPosition="1" />\n    <widget name="list_accent" position="{margin},{panel_y}"\n            size="{accent_w},{panel_h}" font="Regular;1"\n            backgroundColor="#8B5CF6" transparent="0" zPosition="2" />\n    <widget name="breadcrumb" position="{crumb_x},{crumb_y}"\n            size="{crumb_w},{crumb_h}" font="Regular;{crumb_font}"\n            foregroundColor="#22D3EE" transparent="1" zPosition="3"\n            valign="center" halign="left" />\n    <widget name="subtitle" position="{crumb_x},{subtitle_y}"\n            size="{crumb_w},{subtitle_h}" font="Regular;{subtitle_font}"\n            foregroundColor="#CBD5E1" transparent="1" zPosition="3"\n            valign="center" halign="left" />\n    <widget name="items" position="1,1" size="1,1" font="Regular;1"\n            transparent="1" />\n    {cards}\n    <widget name="list_scroll_track" position="{scroll_x},{scroll_y}"\n            size="{scroll_w},{scroll_h}" font="Regular;1"\n            backgroundColor="#26344E" transparent="0" zPosition="5" />\n    <widget name="list_scroll_thumb" position="{scroll_x},{scroll_y}"\n            size="{scroll_w},{scroll_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="6" />\n    <widget name="message" position="{message_x},{message_y}"\n            size="{message_w},{message_h}" font="Regular;{message_font}"\n            foregroundColor="#9AA8BE" transparent="1" zPosition="4"\n            valign="center" halign="right" />\n    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"\n            font="Regular;{footer_font}" foregroundColor="#C8D2E3"\n            backgroundColor="#080E1A" transparent="0"\n            valign="center" halign="center" />\n</screen>\n'.format(screen_name=screen_name, width=width, height=height, accent_h=px(5), margin=margin, header_y=px(28), header_w=px(1040), header_h=px(64), header_font=px(34), screen_x=width - margin - px(500), screen_w=px(500), screen_font=px(18), panel_y=panel_y, panel_w=width - margin * 2, panel_h=panel_h, accent_w=px(7), crumb_x=px(98), crumb_y=px(125), crumb_w=px(1450), crumb_h=px(42), crumb_font=px(20), subtitle_y=px(164), subtitle_h=px(38), subtitle_font=px(21), cards=''.join(cards), scroll_x=width - px(82), scroll_y=px(215), scroll_w=px(8), scroll_h=px(635), message_x=width - px(520), message_y=px(858), message_w=px(430), message_h=px(52), message_font=px(19), footer_y=footer_y, footer_h=height - footer_y, footer_font=px(18))

@seven_skin_after('favorites')
def _favorites_skin():
    """Favorites-only 5×3 poster grid; Continue Watching stays 4×2."""
    width, height, px = _scale()
    variant = {(720, 576): 'sd576', (720, 480): 'sd480', (640, 480): 'sd640'}.get((width, height))
    if variant is None:
        variant = '720' if width <= 1280 and height <= 720 else '1080' if width <= 1920 and height <= 1080 else '2160'
    art_dir = APP_BACKGROUND_PATH.rsplit('/', 1)[0]
    margin = px(54)
    footer_y = height - px(92)
    panel_y = px(104)
    panel_h = px(836)
    tab_y = px(118)
    tab_w = px(300)
    tab_h = px(58)
    tab_gap = px(12)
    grid_x = px(66)
    grid_y = (px(190), px(405), px(620))
    card_w = px(342)
    card_h = px(200)
    card_gap = px(14)
    poster_h = px(154)
    focus_h = max(2, px(4))
    cards = []
    for index in range(15):
        row, column = divmod(index, 5)
        card_x = grid_x + column * (card_w + card_gap)
        card_y = grid_y[row]
        hidden = px(1)
        cards.append('\n    <widget name="continue_card_{index}" position="{card_x},{card_y}"\n            size="{card_w},{card_h}" pixmap="{card_pixmap}"\n            scale="1" zPosition="2" />\n    <widget name="continue_poster_placeholder_{index}"\n            position="{poster_x},{poster_y}" size="{poster_w},{poster_h}"\n            font="Regular;{placeholder_font}" foregroundColor="#4B6485"\n            transparent="1" zPosition="3"\n            valign="center" halign="center" />\n    <widget name="continue_poster_{index}"\n            position="{poster_x},{poster_y}" size="{poster_w},{poster_h}"\n            alphatest="blend" scale="1" zPosition="4" />\n    <widget name="continue_type_{index}" position="{type_x},{type_y}"\n            size="{type_w},{type_h}" font="Regular;{type_font}"\n            foregroundColor="#22D3EE" backgroundColor="#10243C"\n            transparent="0" zPosition="6" valign="center" halign="center" />\n    <widget name="continue_title_{index}" position="{title_x},{title_y}"\n            size="{title_w},{title_h}" font="Regular;{title_font}"\n            foregroundColor="#FFFFFF" transparent="1" zPosition="5"\n            valign="center" halign="left"\n            noWrap="1" />\n    <widget name="continue_favorite_{index}" position="0,0"\n            size="{hidden},{hidden}" zPosition="1" />\n    <widget name="continue_meta_{index}" position="0,0"\n            size="{hidden},{hidden}" font="Regular;1" transparent="1" />\n    <widget name="continue_progress_bg_{index}" position="0,0"\n            size="{hidden},{hidden}" font="Regular;1" transparent="1" />\n    <widget name="continue_progress_fill_{index}" position="0,0"\n            size="{hidden},{hidden}" font="Regular;1" transparent="1" />\n    <widget name="continue_progress_text_{index}" position="0,0"\n            size="{hidden},{hidden}" font="Regular;1" transparent="1" />\n    <widget name="continue_focus_{index}_top" position="{card_x},{card_y}"\n            size="{card_w},{focus_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="8" />\n    <widget name="continue_focus_{index}_bottom" position="{card_x},{bottom_y}"\n            size="{card_w},{focus_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="8" />\n    <widget name="continue_focus_{index}_left" position="{card_x},{card_y}"\n            size="{focus_h},{card_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="8" />\n    <widget name="continue_focus_{index}_right" position="{right_x},{card_y}"\n            size="{focus_h},{card_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="8" />\n    <widget name="continue_move_{index}_top" position="{card_x},{card_y}"\n            size="{card_w},{focus_h}" font="Regular;1"\n            backgroundColor="#FACC15" transparent="0" zPosition="9" />\n    <widget name="continue_move_{index}_bottom" position="{card_x},{bottom_y}"\n            size="{card_w},{focus_h}" font="Regular;1"\n            backgroundColor="#FACC15" transparent="0" zPosition="9" />\n    <widget name="continue_move_{index}_left" position="{card_x},{card_y}"\n            size="{focus_h},{card_h}" font="Regular;1"\n            backgroundColor="#FACC15" transparent="0" zPosition="9" />\n    <widget name="continue_move_{index}_right" position="{right_x},{card_y}"\n            size="{focus_h},{card_h}" font="Regular;1"\n            backgroundColor="#FACC15" transparent="0" zPosition="9" />'.format(index=index, card_x=card_x, card_y=card_y, card_w=card_w, card_h=card_h, card_pixmap='{}/screen-favorites-glass-r91-tile-{}-{:02d}.png'.format(art_dir, variant, index).replace('&', '&amp;'), poster_x=card_x + px(5), poster_y=card_y + px(5), poster_w=card_w - px(10), poster_h=poster_h, placeholder_font=max(10, px(24)), type_x=card_x + px(12), type_y=card_y + px(10), type_w=px(92), type_h=px(30), type_font=max(9, px(16)), title_x=card_x + px(12), title_y=card_y + px(158), title_w=card_w - px(24), title_h=px(37), title_font=max(10, px(21)), hidden=hidden, focus_h=focus_h, bottom_y=card_y + card_h - focus_h, right_x=card_x + card_w - focus_h))
    filters = []
    for index in range(4):
        tab_x = px(66) + index * (tab_w + tab_gap)
        filters.append('\n    <widget name="favorite_filter_focus_{index}" position="{x},{y}"\n            size="{w},{h}" font="Regular;1" backgroundColor="#22D3EE"\n            transparent="0" zPosition="3" />\n    <widget name="favorite_filter_{index}" position="{inner_x},{inner_y}"\n            size="{inner_w},{inner_h}" font="Regular;{font}"\n            foregroundColor="#FFFFFF" transparent="1" zPosition="4"\n            valign="center" halign="center" />'.format(index=index, x=tab_x, y=tab_y, w=tab_w, h=tab_h, inner_x=tab_x + px(3), inner_y=tab_y + px(3), inner_w=tab_w - px(6), inner_h=tab_h - px(6), font=max(10, px(21))))
    return '\n<screen name="GTFavoritesScreen" position="0,0" size="{width},{height}"\n        flags="wfNoBorder" backgroundColor="#050914">\n    <widget name="app_bg" position="0,0" size="{width},{height}" zPosition="0" />\n    <widget name="top_accent" position="0,0" size="{width},{accent_h}"\n            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />\n    <widget name="header" position="{margin},{header_y}" size="{header_w},{header_h}"\n            font="Regular;{header_font}" foregroundColor="#FFFFFF"\n            transparent="1" valign="center" halign="left" />\n    <widget name="screen_label" position="{screen_x},{header_y}"\n            size="{screen_w},{header_h}" font="Regular;{screen_font}"\n            foregroundColor="#FFFFFF" transparent="1" valign="center" halign="right" />\n    <widget name="content_panel" position="{margin},{panel_y}"\n            size="{panel_w},{panel_h}" font="Regular;1"\n            transparent="1" zPosition="1" />\n    <widget name="list_accent" position="{margin},{panel_y}" size="{accent_w},{panel_h}"\n            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" zPosition="2" />\n    <widget name="breadcrumb" position="{margin},{crumb_y}" size="{crumb_w},{crumb_h}"\n            font="Regular;{crumb_font}" foregroundColor="#22D3EE"\n            transparent="1" valign="center" halign="left" />\n    <widget name="subtitle" position="0,0" size="1,1" font="Regular;1" transparent="1" />\n    <widget name="items" position="1,1" size="1,1" font="Regular;1" transparent="1" />\n    {filters}\n    {cards}\n    <widget name="favorite_info" position="{info_x},{info_y}"\n            size="{info_w},{info_h}" font="Regular;{info_font}"\n            foregroundColor="#60A5FA" transparent="1" zPosition="4"\n            valign="center" halign="left"\n            noWrap="1" />\n    <widget name="list_scroll_track" position="{scroll_x},{scroll_y}"\n            size="{scroll_w},{scroll_h}" font="Regular;1"\n            backgroundColor="#26344E" transparent="0" zPosition="5" />\n    <widget name="list_scroll_thumb" position="{scroll_x},{scroll_y}"\n            size="{scroll_w},{scroll_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="6" />\n    <widget name="message" position="0,0" size="1,1" font="Regular;1" transparent="1" />\n    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"\n            font="Regular;{footer_font}" foregroundColor="#C8D2E3"\n            backgroundColor="#080E1A" transparent="0" valign="center" halign="center" />\n</screen>\n'.format(width=width, height=height, accent_h=px(5), margin=margin, header_y=px(20), header_w=px(760), header_h=px(50), header_font=max(12, px(31)), screen_x=width - margin - px(620), screen_w=px(620), screen_font=max(9, px(18)), panel_y=panel_y, panel_w=width - margin * 2, panel_h=panel_h, accent_w=px(5), crumb_y=px(68), crumb_w=px(1200), crumb_h=px(34), crumb_font=max(9, px(18)), filters=''.join(filters), cards=''.join(cards), info_x=px(66), info_y=px(835), info_w=width - px(132), info_h=px(68), info_font=max(10, px(21)), scroll_x=width - px(73), scroll_y=px(190), scroll_w=px(7), scroll_h=px(630), footer_y=footer_y, footer_h=height - footer_y, footer_font=max(9, px(17)))

class GTContinueWatchingScreen(_DownloadSupport, GTAsyncListScreen):
    """Poster grid for every unfinished movie and series episode."""
    page_size = 3

    def __init__(self, session, account, client=None, resume_store=None, metadata_client=None, grid_screen_name='GTContinueWatchingScreen', grid_title=None, grid_subtitle=None, grid_footer_items=None, grid_footer_alternate_items=None, grid_skin=None, grid_scroll_geometry=None, content_type_filter=''):
        self.account = account
        self.client = client or content_client_for(account)
        self.resume_store = resume_store or PlaybackResumeStore()
        self.account_scope = resume_account_scope(self.client)
        self._content_type_filter = str(content_type_filter or '').strip().lower()
        if metadata_client is not None:
            self._metadata_client = metadata_client
        else:
            try:
                settings = load_player_settings()
            except Exception:
                settings = None
            self._metadata_client = TMDbMetadataClient(api_key=getattr(settings, 'tmdb_api_key', ''), enabled=getattr(settings, 'metadata_enabled', True), language=metadata_language())
        self._initial_show = True
        self._continue_generation = 0
        self._continue_poster_page_key = None
        self._continue_poster_state = None
        self._footer_layout_items = grid_footer_items or CONTINUE_FOOTER_ITEMS
        self._footer_layout_alternate_items = tuple(grid_footer_alternate_items or ())
        self._screen_background_path = ''
        self._opaque_background = True
        self._seven_pending_kind = 'favorites' if grid_screen_name == 'GTFavoritesScreen' else 'continue'
        self._footer_show_web_hint = False
        self._footer_show_dividers = False
        self._footer_transparent_panel = True
        self.skin = grid_skin or prepared_skin(_continue_watching_skin, grid_screen_name)
        width, height, px = _scale()
        self._continue_progress_width = px(384)
        self._continue_progress_height = px(9)
        self._list_scroll_geometry = grid_scroll_geometry or (width - px(82), px(215), px(8), px(635))
        title = grid_title or N_('Continue Watching')
        GTAsyncListScreen.__init__(self, session, grid_screen_name, title, '{}  >  {}'.format(account.display_name, _(title)))
        set_text_if_changed(self['screen_label'], _(title))
        self['subtitle'] = Label(_(grid_subtitle or N_('Unfinished movies and series episodes')))
        for slot in range(self.page_size):
            self['continue_card_{}'.format(slot)] = Pixmap()
            self['continue_poster_placeholder_{}'.format(slot)] = Label('')
            self['continue_poster_{}'.format(slot)] = Pixmap()
            self['continue_favorite_{}'.format(slot)] = Pixmap()
            self['continue_type_{}'.format(slot)] = Label('')
            self['continue_title_{}'.format(slot)] = Label('')
            self['continue_meta_{}'.format(slot)] = Label('')
            self['continue_progress_bg_{}'.format(slot)] = Label('')
            self['continue_progress_fill_{}'.format(slot)] = Label('')
            self['continue_progress_text_{}'.format(slot)] = Label('')
            for edge in ('top', 'bottom', 'left', 'right'):
                self['continue_focus_{}_{}'.format(slot, edge)] = Label('')
        self._poster_timer = eTimer()
        _connect_timer(self._poster_timer, self._poll_continue_posters)
        self['download_actions'] = ActionMap(['MenuActions'], {'menu': self.request_selected_download}, -1)
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions', 'ColorActions'], {'ok': self.open_selected, 'cancel': self.close, 'left': self.move_left, 'right': self.move_right, 'up': self.move_grid_up, 'down': self.move_grid_down, 'red': self.remove_selected, 'green': self.start_load}, -1)
        if hasattr(self, 'onShown'):
            self.onShown.append(self._reload_after_return)
        if hasattr(self, 'onClose'):
            self.onClose.append(self._stop_continue_posters)
        self._clear_continue_cards()
        setup_seven(self, self._seven_pending_kind, self._footer_layout_items)

    def _reload_after_return(self):
        if self._initial_show:
            self._initial_show = False
            return
        if not self._loading:
            self._continue_poster_page_key = None
            self.start_load()

    def load_items(self):
        entries = []
        for bookmark in self.resume_store.list_entries(self.account_scope):
            metadata = bookmark.get('item', {})
            content_type = str(metadata.get('content_type', '') or '').strip().lower()
            if self._content_type_filter and content_type != self._content_type_filter:
                continue
            item = ContentItem(content_type, metadata.get('stream_id'), metadata.get('name'), category_id=metadata.get('category_id'), extension=metadata.get('extension'), icon=metadata.get('icon'), season=metadata.get('season'), episode=metadata.get('episode'), year=metadata.get('year'), parent_id=metadata.get('parent_id'))
            if str(metadata.get('source_type', '') or '').lower() == 'stalker':
                item.source_type = 'stalker'
                request_type = str(metadata.get('portal_request_type', '') or '').strip().lower()
                allowed_request_types = {'movie': frozenset(('vod', 'movie')), 'series': frozenset(('vod', 'series'))}
                if request_type in allowed_request_types.get(item.content_type, ()):
                    item.portal_request_type = request_type
            item.resume_position = int(bookmark.get('position', 0) or 0)
            item.resume_length = int(bookmark.get('length', 0) or 0)
            item.resume_updated_at = int(bookmark.get('updated_at', 0) or 0)
            item.resume_key = str(bookmark.get('key', '') or '')
            if item.content_type == 'series' and item.parent_id:
                parent = ContentItem('series', item.parent_id, metadata.get('parent_name') or item.name, category_id=item.category_id, icon=item.icon, year=item.year)
                if getattr(item, 'source_type', '') == 'stalker':
                    parent.source_type = 'stalker'
                parent.catalog_category_id = str(item.category_id or '')
                item.favorite_parent = parent
            entries.append(item)
        return entries

    def empty_message(self):
        return N_('No content found.')

    def _clear_continue_cards(self, first=0):
        for slot in range(first, self.page_size):
            getattr(self, '_r27_card_content', {}).pop(slot, None)
            for name in ('continue_card_{}', 'continue_poster_placeholder_{}', 'continue_poster_{}', 'continue_favorite_{}', 'continue_type_{}', 'continue_title_{}', 'continue_meta_{}', 'continue_progress_bg_{}', 'continue_progress_fill_{}', 'continue_progress_text_{}'):
                set_visible(self[name.format(slot)], False)
            for edge in ('top', 'bottom', 'left', 'right'):
                set_visible(self['continue_focus_{}_{}'.format(slot, edge)], False)

    @seven_refresh_after
    def _refresh(self):
        if self._error:
            self._clear_continue_cards()
            ellipsize_dynamic_text(self['message'], _(self._error), fallback_chars=80)
            hide_scrollbar(self, 'list_scroll')
            return
        if not self._entries:
            self._clear_continue_cards()
            set_text_if_changed(self['message'], _(self.empty_message()))
            hide_scrollbar(self, 'list_scroll')
            return
        self.selected_index %= len(self._entries)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start:page_start + self.page_size]
        self._clear_continue_cards(len(page_items))
        for slot, item in enumerate(page_items):
            self._render_continue_card(slot, item, page_start + slot == self.selected_index)
        if getattr(self, '_seven_kind', '') not in ('continue', 'favorites'):
            set_text_if_changed(self['message'], '{} | {}/{}'.format(len(self._entries), self.selected_index + 1, len(self._entries)))
            update_scrollbar(self, 'list_scroll', len(self._entries), self.selected_index, self.page_size, self._list_scroll_geometry)
        self._render_continue_posters(page_items, page_start)

    def _render_continue_card(self, slot, item, selected):
        from .ui_update import card_content_changed
        if card_content_changed(self, slot, item):
            position = int(getattr(item, 'resume_position', 0) or 0)
            length = int(getattr(item, 'resume_length', 0) or 0)
            progress = 0 if length <= 0 else int(min(100, max(0, round(float(position) * 100.0 / float(length)))))
            remaining_minutes = max(1, int(round(max(0, length - position) / 60.0)))
            content_key = N_('Movies') if item.content_type == 'movie' else N_('Series')
            content_label = localized_upper(_(content_key))
            meta = ''
            if item.content_type == 'series':
                season = str(getattr(item, 'season', '') or '').strip()
                episode = str(getattr(item, 'episode', '') or '').strip()
                if season or episode:
                    meta = _('S{}  •  E{}').format(season or '-', episode or '-')
            elif str(getattr(item, 'year', '') or '').strip():
                meta = str(item.year)
            placeholder = self['continue_poster_placeholder_{}'.format(slot)]
            placeholder.setText('GT\n{}'.format(content_label))
            set_visible(placeholder, True)
            set_visible(self['continue_poster_{}'.format(slot)], False)
            set_visible(self['continue_favorite_{}'.format(slot)], False)
            set_text_if_changed(self['continue_type_{}'.format(slot)], content_label)
            ellipsize_dynamic_text(self['continue_title_{}'.format(slot)], item.name, fallback_chars=22)
            set_text_if_changed(self['continue_meta_{}'.format(slot)], meta)
            set_text_if_changed(self['continue_progress_text_{}'.format(slot)], _('%{} • {} min remaining').format(progress, remaining_minutes))
            try:
                self['continue_progress_fill_{}'.format(slot)].instance.resize(eSize(max(1, int(round(self._continue_progress_width * progress / 100.0))), self._continue_progress_height))
            except Exception:
                pass
            for name in ('continue_card_{}', 'continue_type_{}', 'continue_title_{}', 'continue_meta_{}', 'continue_progress_bg_{}', 'continue_progress_fill_{}', 'continue_progress_text_{}'):
                set_visible(self[name.format(slot)], True)
        for edge in ('top', 'bottom', 'left', 'right'):
            widget = self['continue_focus_{}_{}'.format(slot, edge)]
            if selected:
                set_visible(widget, True)
            else:
                set_visible(widget, False)

    @staticmethod
    def _continue_candidate(candidates, value):
        url = _safe_picon_url(value)
        if url and url not in candidates:
            candidates.append(url)

    def _continue_provider_candidates(self, item):
        candidates = []
        info = None
        content_type = str(getattr(item, 'content_type', '') or '')
        stream_id = str(getattr(item, 'stream_id', '') or '')
        if content_type == 'movie':
            loader = getattr(self.client, 'load_vod_info', None)
            if loader is not None:
                try:
                    info = loader(stream_id)
                    self._continue_candidate(candidates, getattr(info, 'cover', ''))
                except Exception as error:
                    _debug('continue movie poster lookup failed id={} error={}'.format(stream_id, error.__class__.__name__))
        elif content_type == 'series':
            parent_id = str(getattr(item, 'parent_id', '') or '')
            series_id = _series_provider_id(self.client, parent_id) or _series_provider_id(self.client, stream_id)
            loader = getattr(self.client, 'load_series_summary', None)
            if loader is None:
                loader = getattr(self.client, 'load_series_info', None)
            if loader is not None and series_id:
                try:
                    info = loader(series_id)
                    if parent_id:
                        for episode in list(getattr(info, 'episodes', []) or []):
                            if str(getattr(episode, 'stream_id', '')) == stream_id:
                                self._continue_candidate(candidates, getattr(episode, 'icon', ''))
                                break
                    self._continue_candidate(candidates, getattr(info, 'cover', ''))
                except Exception as error:
                    _debug('continue series poster lookup failed id={} error={}'.format(_debug_identifier(stream_id), error.__class__.__name__))
        _attach_subtitle_detail_metadata(item, info)
        return (candidates, info)

    def _continue_metadata_candidates(self, item, info, token=None):
        candidates = []
        client = self._metadata_client
        if client is None or not bool(getattr(client, 'available', False)):
            return candidates
        try:
            content_type = getattr(item, 'content_type', '')
            if content_type not in ('movie', 'series'):
                return candidates
            cover = _bounded_client_call(client, token, lambda: client.poster_cover('movie' if content_type == 'movie' else 'tv', item, info))
            self._continue_candidate(candidates, cover)
        except MetadataError as error:
            _debug('continue TMDb poster fallback failed id={} error={}'.format(_debug_identifier(getattr(item, 'stream_id', '')), error.__class__.__name__))
        except Exception as error:
            _debug('continue poster fallback failed id={} error={}'.format(_debug_identifier(getattr(item, 'stream_id', '')), error.__class__.__name__))
        return candidates

    def _download_continue_candidate(self, url, token=None):
        if not url:
            return ''
        if token is not None:
            token.check()
            remaining = max(0.25, token.deadline - time.monotonic())
        else:
            remaining = PICON_DOWNLOAD_TIMEOUT
        result = _download_client_image(self.client, url, _picon_cache_path(url), timeout=min(PICON_DOWNLOAD_TIMEOUT, remaining), token=token)
        if token is not None:
            token.check()
        return result

    def _render_continue_posters(self, page_items, page_start):
        page_key = tuple(((str(item.stream_id), str(getattr(item, 'icon', '') or '')) for item in page_items))
        if page_key == self._continue_poster_page_key:
            return
        self._cancel_continue_poster_work()
        self._continue_generation += 1
        self._continue_poster_page_key = page_key
        generation = self._continue_generation
        jobs = []
        for slot, item in enumerate(page_items):
            url = _safe_picon_url(getattr(item, 'icon', ''))
            if url or self._poster_job_allowed(item, url):
                jobs.append((slot, item, url, page_start))
        if not jobs:
            self._continue_poster_state = None
            return
        token = _WorkerToken(WORKER_POSTER_TIMEOUT_SECONDS)
        state = {'generation': generation, 'pending': len(jobs), 'jobs': deque(jobs), 'results': [], 'remember': [], 'lock': threading.Lock(), 'token': token}
        self._continue_poster_state = state
        pool = getattr(self, '_continue_poster_workers', None)
        if pool is None:
            pool = self._continue_poster_workers = LatestPosterWorkers(worker_count=CONTINUE_POSTER_WORKERS)
            self.onClose.append(pool.close)
        pool.replace([lambda: self._load_continue_poster_worker(state) for unused in range(min(CONTINUE_POSTER_WORKERS, len(jobs)))])
        self._poster_timer.start(55, True)

    def _poster_job_allowed(self, item, stored_url):
        del stored_url
        return getattr(item, 'content_type', '') != 'live'

    def _load_continue_poster_worker(self, state):
        while True:
            with state['lock']:
                if not state['jobs']:
                    return
                job = state['jobs'].popleft()
            self._load_continue_poster_job(job, state)

    def _load_continue_poster_job(self, job, state):
        slot, item, stored_url, page_start = job
        token = state.get('token')
        stream_id = str(getattr(item, 'stream_id', '') or '')
        path = ''
        selected_url = ''
        info = None
        candidates = []
        self._continue_candidate(candidates, stored_url)
        try:
            token.check()
            path = _cached_picon_path(stored_url) if stored_url else ''
            allow_lookup = not path and self._poster_job_allowed(item, stored_url)
            if allow_lookup and stored_url:
                path = self._download_continue_candidate(stored_url, token)
                if path:
                    selected_url = stored_url
            if not path and allow_lookup:
                provider_candidates, info = _bounded_client_call(self.client, token, lambda: self._continue_provider_candidates(item))
                for candidate in provider_candidates:
                    if candidate in candidates:
                        continue
                    candidates.append(candidate)
                    path = self._download_continue_candidate(candidate, token)
                    if path:
                        selected_url = candidate
                        break
            if not path and allow_lookup:
                token.check()
                for candidate in self._continue_metadata_candidates(item, info, token):
                    if candidate in candidates:
                        continue
                    path = self._download_continue_candidate(candidate, token)
                    if path:
                        selected_url = candidate
                        break
        except Exception as error:
            _debug('continue poster failed id={} error={}'.format(_debug_identifier(stream_id), error.__class__.__name__))
            path = ''
            selected_url = ''
        with state['lock']:
            state['results'].append((slot, stream_id, path, page_start, selected_url))
            state['pending'] -= 1

    def _poll_continue_posters(self):
        if self._closed:
            return
        state = self._continue_poster_state
        if state is None:
            return
        token = state.get('token')
        if token is not None and token.expired():
            _cancel_worker_token(token)
            if self._continue_poster_state is state:
                self._continue_poster_state = None
            return
        with state['lock']:
            results = list(state['results'])
            state['results'][:] = []
            pending = int(state['pending'])
        for slot, stream_id, path, page_start, selected_url in results:
            if path:
                try:
                    current = self._entries[page_start + slot]
                    valid = str(current.stream_id) == str(stream_id)
                except Exception:
                    current = None
                    valid = False
                if valid and selected_url:
                    current.icon = selected_url
                    state['remember'].append((str(getattr(current, 'resume_key', '') or ''), current))
                self._apply_continue_poster(slot, path, stream_id, state['generation'], page_start)
        if pending > 0:
            self._poster_timer.start(120, True)
        elif self._continue_poster_state is state:
            remember_many = getattr(self.resume_store, 'remember_many', None)
            try:
                if callable(remember_many):
                    remember_many(state['remember'], self.account_scope)
                else:
                    remember = getattr(self.resume_store, 'remember', None)
                    if callable(remember):
                        for key, current in state['remember']:
                            remember(key, current, self.account_scope)
            except Exception:
                pass
            self._continue_poster_state = None

    @poster_after
    def _apply_continue_poster(self, slot, path, stream_id, generation, page_start):

        def loaded(success):
            try:
                current_page = int(self.selected_index / self.page_size) * self.page_size
                current = self._entries[page_start + slot]
                valid = generation == self._continue_generation and current_page == page_start and (str(current.stream_id) == str(stream_id))
                if success and valid:
                    set_visible(self['continue_poster_{}'.format(slot)], True)
                    set_visible(self['continue_poster_placeholder_{}'.format(slot)], False)
                else:
                    set_visible(self['continue_poster_{}'.format(slot)], False)
            except Exception:
                pass
        attach_pixmap(self, 'continue_poster_{}'.format(slot), path, key='continue-poster-{}'.format(slot), on_loaded=loaded)

    def _cancel_continue_poster_work(self):
        state = self._continue_poster_state
        if state is not None:
            _cancel_worker_token(state.get('token'))
        self._continue_poster_state = None
        try:
            self._poster_timer.stop()
        except Exception:
            pass

    def _stop_continue_posters(self):
        self._continue_generation += 1
        self._cancel_continue_poster_work()

    def _suspend_for_playback(self):
        self._stop_continue_posters()

    def _select_continue(self, index):
        if not self._entries or self._loading:
            return
        previous = self.selected_index
        self.selected_index = min(max(0, int(index)), len(self._entries) - 1)
        if (getattr(self, '_seven_kind', None) in ('continue', 'favorites') and not self._error
                and previous // self.page_size == self.selected_index // self.page_size):
            if previous != self.selected_index:
                refresh_media(self, previous)
            return
        self._refresh()

    def move_left(self):
        self._select_continue(self.selected_index - 1)

    def move_right(self):
        self._select_continue(self.selected_index + 1)

    def move_grid_up(self):
        if self.selected_index >= 3:
            self._select_continue(self.selected_index - 3)

    def move_grid_down(self):
        self._select_continue(self.selected_index + 3)

    def remove_selected(self):
        if not self._entries or self._loading:
            return
        item = self._entries[self.selected_index]
        key = str(getattr(item, 'resume_key', '') or '')
        if not key:
            return
        if not queue_playback_clear(self.resume_store, key):
            return
        self._entries.pop(self.selected_index)
        self.selected_index = min(self.selected_index, max(0, len(self._entries) - 1))
        self._continue_poster_page_key = None
        self._refresh()

    @staticmethod
    def _time_text(seconds):
        try:
            seconds = max(0, int(seconds or 0))
        except (TypeError, ValueError, OverflowError):
            seconds = 0
        hours, remainder = divmod(seconds, 3600)
        minutes, remaining_seconds = divmod(remainder, 60)
        return '{:02d}:{:02d}:{:02d}'.format(hours, minutes, remaining_seconds)

    def item_text(self, item):
        position = int(getattr(item, 'resume_position', 0) or 0)
        length = int(getattr(item, 'resume_length', 0) or 0)
        progress = 0 if length <= 0 else int(min(100, max(0, round(float(position) * 100.0 / float(length)))))
        content_label = _('Movies') if item.content_type == 'movie' else _('Series')
        return '{}  •  {}  •  %{}  •  {} / {}'.format(content_label, item.name, progress, self._time_text(position), self._time_text(length))

    def open_selected(self):
        if not self._entries or self._loading:
            return
        from .browser_player import open_extplayer
        open_extplayer(self.session, self.client, self._entries[self.selected_index], self._entries, self.selected_index, owner=self)

class GTFavoritesScreen(GTContinueWatchingScreen):
    """Local mixed-media favorites with one filterable poster grid."""
    page_size = 6
    FILTERS = (('', N_('All')), ('live', N_('Live TV')), ('movie', N_('Movies')), ('series', N_('Series')))

    def __init__(self, session, account, client=None, favorite_store=None, metadata_client=None):
        self._favorite_filter_index = 0
        self._favorite_counts = {'': 0, 'live': 0, 'movie': 0, 'series': 0}
        self._move_active = False
        self._move_snapshot = None
        self._move_snapshot_index = 0
        store = favorite_store if favorite_store is not None else FavoriteStore()
        width, unused_height, px = _scale()
        del unused_height
        GTContinueWatchingScreen.__init__(self, session, account, client=client, resume_store=store, metadata_client=metadata_client, grid_screen_name='GTFavoritesScreen', grid_title=N_('Favorites'), grid_subtitle=N_('Favorite channels, movies and series'), grid_footer_items=FAVORITES_FOOTER_ITEMS, grid_footer_alternate_items=FAVORITES_MOVE_FOOTER_ITEMS, grid_skin=prepared_skin(_favorites_skin), grid_scroll_geometry=(width - px(73), px(190), px(7), px(630)))
        self.favorite_store = store
        self.favorite_scope = self.account_scope
        for index in range(len(self.FILTERS)):
            self['favorite_filter_{}'.format(index)] = Label('')
            self['favorite_filter_focus_{}'.format(index)] = Label('')
        self['favorite_info'] = Label('')
        for slot in range(self.page_size):
            for edge in ('top', 'bottom', 'left', 'right'):
                self['continue_move_{}_{}'.format(slot, edge)] = Label('')
        self._hide_move_frames()
        self._set_dynamic_breadcrumb('{}  •  {}'.format(self._safe_source_name(), localized_upper(_('Favorites'))))
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions', 'ColorActions'], {'ok': self.ok_action, 'cancel': self.exit_action, 'left': self.move_left, 'right': self.move_right, 'up': self.move_grid_up, 'down': self.move_grid_down, 'red': self.remove_selected, 'yellow': self.start_move, 'green': self.refresh_favorites, 'blue': self.cycle_filter}, -1)
        self['channel_actions'] = ActionMap(['MoviePlayerActions'], {'channelUp': self.previous_page, 'channelDown': self.next_page}, -1)

    def _safe_source_name(self):
        source_type = str(getattr(self.account, 'source_type', 'xtream') or 'xtream').lower()
        if source_type == 'stalker':
            return str(getattr(self.account, 'host', '') or 'STALKER')
        name = str(getattr(self.account, 'name', '') or '').strip()
        if name:
            return name
        host = str(getattr(self.account, 'host', '') or '').strip()
        return host or localized_upper(_('Source'))

    def _active_filter(self):
        return self.FILTERS[self._favorite_filter_index % len(self.FILTERS)]

    @staticmethod
    def _favorite_item(entry):
        metadata = entry.get('item', {})
        item = ContentItem(metadata.get('content_type'), metadata.get('stream_id'), metadata.get('name'), category_id=metadata.get('category_id'), extension=metadata.get('extension'), icon=metadata.get('icon'), rating=metadata.get('rating'), year=metadata.get('year'), parent_id=metadata.get('parent_id'))
        if str(metadata.get('source_type', '') or '').lower() == 'stalker':
            item.source_type = 'stalker'
            request_type = str(metadata.get('portal_request_type', '') or '').lower()
            if request_type in ('itv', 'vod', 'movie', 'series'):
                item.portal_request_type = request_type
        item.catalog_category_id = str(metadata.get('catalog_category_id', '') or '')
        item.favorite_key = str(entry.get('key', '') or '')
        item.resume_key = item.favorite_key
        item.favorite_updated_at = int(entry.get('updated_at', 0) or 0)
        return item

    def _entries_for_type(self, content_type=''):
        entries = self.favorite_store.list_entries(self.favorite_scope, content_type)
        return [self._favorite_item(entry) for entry in entries]

    def load_items(self):
        content_type = self._active_filter()[0]
        stored_entries = self.favorite_store.list_entries(self.favorite_scope)
        counts = {'': len(stored_entries), 'live': 0, 'movie': 0, 'series': 0}
        for entry in stored_entries:
            item_type = str(entry.get('item', {}).get('content_type', '') or '')
            if item_type in counts:
                counts[item_type] += 1
        self._favorite_counts = counts
        entries = [self._favorite_item(entry) for entry in stored_entries if not content_type or str(entry.get('item', {}).get('content_type', '') or '') == content_type]
        if not _is_m3u_client(self.client):
            return entries
        if not entries:
            return []
        resolver = getattr(self.client, 'resolve_items', None)
        if not callable(resolver):
            return []
        resolved_by_id = {str(getattr(item, 'stream_id', '') or ''): item for item in resolver(entries)}
        output = []
        for saved in entries:
            stream_id = str(getattr(saved, 'stream_id', '') or '')
            item = resolved_by_id.get(stream_id)
            if item is None:
                item = saved
                item.m3u_stale = True
            item.favorite_key = str(getattr(saved, 'favorite_key', '') or '')
            item.resume_key = item.favorite_key
            item.favorite_updated_at = int(getattr(saved, 'favorite_updated_at', 0) or 0)
            output.append(item)
        return output

    def _continue_provider_candidates(self, item):
        """Keep favorite artwork work independent from catalog requests.

        Favorite snapshots already contain a credential-free public artwork
        URL when one is available.  A missing image is allowed to use the
        optional metadata fallback, but must not occupy the shared provider
        request slots needed to open a selected series.
        """
        return ([], None)

    def _render_continue_posters(self, page_items, page_start):
        if self._move_active:
            self._cancel_continue_poster_work()
            return
        return GTContinueWatchingScreen._render_continue_posters(self, page_items, page_start)

    def _poster_job_allowed(self, item, stored_url):
        return bool(stored_url) or getattr(item, 'content_type', '') != 'live'

    def empty_message(self):
        return N_('No favorites have been added.')

    @staticmethod
    def _favorite_content_label(item):
        labels = {'live': N_('Live TV'), 'movie': N_('Movies'), 'series': N_('Series')}
        return localized_upper(_(labels.get(item.content_type, N_('Favorite'))))

    def _render_continue_card(self, slot, item, selected):
        from .ui_update import card_content_changed
        if card_content_changed(self, slot, item):
            content_label = self._favorite_content_label(item)
            placeholder = self['continue_poster_placeholder_{}'.format(slot)]
            placeholder.setText('GT\n{}'.format(content_label))
            set_visible(placeholder, True)
            set_visible(self['continue_poster_{}'.format(slot)], False)
            set_text_if_changed(self['continue_type_{}'.format(slot)], content_label)
            ellipsize_dynamic_text(self['continue_title_{}'.format(slot)], item.name, fallback_chars=25)
            for name in ('continue_card_{}', 'continue_type_{}', 'continue_title_{}'):
                set_visible(self[name.format(slot)], True)
            set_visible(self['continue_meta_{}'.format(slot)], False)
            set_visible(self['continue_progress_bg_{}'.format(slot)], False)
            set_visible(self['continue_progress_fill_{}'.format(slot)], False)
            set_visible(self['continue_progress_text_{}'.format(slot)], False)
            set_visible(self['continue_favorite_{}'.format(slot)], False)
        for edge in ('top', 'bottom', 'left', 'right'):
            widget = self['continue_focus_{}_{}'.format(slot, edge)]
            move_widget = self['continue_move_{}_{}'.format(slot, edge)]
            if selected and (not self._move_active):
                set_visible(widget, True)
            else:
                set_visible(widget, False)
            if selected and self._move_active:
                set_visible(move_widget, True)
            else:
                set_visible(move_widget, False)

    def start_load(self):
        return GTContinueWatchingScreen.start_load(self)

    def _hide_move_frames(self, first=0):
        for slot in range(first, self.page_size):
            for edge in ('top', 'bottom', 'left', 'right'):
                set_visible(self['continue_move_{}_{}'.format(slot, edge)], False)

    def _update_filter_tabs(self):
        for index, (content_type, label) in enumerate(self.FILTERS):
            count = int(self._favorite_counts.get(content_type, 0) or 0)
            if getattr(self, '_seven_kind', '') != 'favorites':
                set_text_if_changed(self['favorite_filter_{}'.format(index)], '{}  {}'.format(localized_upper(_(label)), count))
            focus = self['favorite_filter_focus_{}'.format(index)]
            if index == self._favorite_filter_index:
                set_visible(focus, True)
            else:
                set_visible(focus, False)

    @seven_refresh_after
    def _refresh(self):
        self._update_filter_tabs()
        total = int(self._favorite_counts.get('', 0) or 0)
        if self._error:
            self._clear_continue_cards()
            self._hide_move_frames()
            set_text_if_changed(self['screen_label'], '{} {}  •  {} 0 / 0'.format(total, localized_upper(_('Favorites')), localized_upper(_('Page'))))
            ellipsize_dynamic_text(self['favorite_info'], _(self._error), fallback_chars=110)
            hide_scrollbar(self, 'list_scroll')
            return
        if not self._entries:
            self._clear_continue_cards()
            self._hide_move_frames()
            self.selected_index = 0
            set_text_if_changed(self['screen_label'], '{} {}  •  {} 0 / 0'.format(total, localized_upper(_('Favorites')), localized_upper(_('Page'))))
            set_text_if_changed(self['favorite_info'], _(self.empty_message()))
            hide_scrollbar(self, 'list_scroll')
            return
        self.selected_index = min(max(0, int(self.selected_index)), len(self._entries) - 1)
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start:page_start + self.page_size]
        self._clear_continue_cards(len(page_items))
        self._hide_move_frames(len(page_items))
        for slot, item in enumerate(page_items):
            self._render_continue_card(slot, item, page_start + slot == self.selected_index)
        page_count = int((len(self._entries) + self.page_size - 1) / self.page_size)
        page = int(page_start / self.page_size) + 1
        set_text_if_changed(self['screen_label'], '{} {}  •  {} {} / {}'.format(total, localized_upper(_('Favorites')), localized_upper(_('Page')), page, page_count))
        selected = self._entries[self.selected_index]
        ellipsize_dynamic_text(self['favorite_info'], '{}  •  {}  •  {}'.format(selected.name, self._favorite_content_label(selected), self._safe_source_name()), fallback_chars=110)
        if getattr(self, '_seven_kind', '') not in ('continue', 'favorites'):
            set_text_if_changed(self['message'], '{} | {}/{}'.format(len(self._entries), self.selected_index + 1, len(self._entries)))
            update_scrollbar(self, 'list_scroll', len(self._entries), self.selected_index, self.page_size, self._list_scroll_geometry)
        self._render_continue_posters(page_items, page_start)

    def cycle_filter(self):
        if self._loading or self._move_active:
            return
        self._stop_continue_posters()
        self._favorite_filter_index = (self._favorite_filter_index + 1) % len(self.FILTERS)
        self._continue_poster_page_key = None
        self.selected_index = 0
        self.start_load()

    def _select_favorite(self, index):
        self._select_continue(index)

    def _move_reordered_item(self, delta):
        if not self._entries or not self._move_active:
            return
        target = min(max(0, self.selected_index + int(delta)), len(self._entries) - 1)
        if target == self.selected_index:
            return
        item = self._entries.pop(self.selected_index)
        self._entries.insert(target, item)
        self.selected_index = target
        self._continue_poster_page_key = None
        self._refresh()

    def move_left(self):
        if self._move_active:
            self._move_reordered_item(-1)
        else:
            self._select_favorite(self.selected_index - 1)

    def move_right(self):
        if self._move_active:
            self._move_reordered_item(1)
        else:
            self._select_favorite(self.selected_index + 1)

    def move_grid_up(self):
        if self._move_active:
            self._move_reordered_item(-3)
        elif self.selected_index >= 3:
            self._select_favorite(self.selected_index - 3)

    def move_grid_down(self):
        if self._move_active:
            self._move_reordered_item(3)
        else:
            self._select_favorite(self.selected_index + 3)

    def _change_page(self, direction):
        if not self._entries or self._loading:
            return
        if self._move_active:
            self._move_reordered_item(int(direction) * self.page_size)
            return
        current_start = int(self.selected_index / self.page_size) * self.page_size
        last_start = int((len(self._entries) - 1) / self.page_size) * self.page_size
        target_start = max(0, min(current_start + int(direction) * self.page_size, last_start))
        if target_start == current_start:
            return
        slot = self.selected_index - current_start
        target_count = min(self.page_size, len(self._entries) - target_start)
        self._select_favorite(target_start + min(slot, target_count - 1))

    def previous_page(self):
        self._change_page(-1)

    def next_page(self):
        self._change_page(1)

    def start_move(self):
        if self._loading or self._move_active or (not self._entries):
            return
        self._stop_continue_posters()
        self._continue_poster_page_key = None
        self._move_snapshot = list(self._entries)
        self._move_snapshot_index = self.selected_index
        self._move_active = True
        set_remote_footer(self, FAVORITES_MOVE_FOOTER_ITEMS)
        self._refresh()

    def _leave_move_mode(self):
        self._move_active = False
        self._move_snapshot = None
        self._continue_poster_page_key = None
        set_remote_footer(self, FAVORITES_FOOTER_ITEMS)

    def _watch_favorite_operation(self, result, failure, success=''):
        operations = getattr(self, '_favorite_operations', None)
        if operations is None:
            operations = self._favorite_operations = []
            self._favorite_operation_timer = eTimer()
            _connect_timer(self._favorite_operation_timer, self._poll_favorite_operations)
            self.onClose.append(self._favorite_operation_timer.stop)
        operations[:] = [entry for entry in operations if not entry[0]['done']][-31:]
        operations.append((result, failure, success))
        self._favorite_operation_timer.start(80, True)

    def _poll_favorite_operations(self):
        if self._closed:
            return
        pending = []
        for result, failure, success in self._favorite_operations:
            if not result['done']:
                pending.append((result, failure, success))
            elif not result['ok']:
                self.start_load()
                set_text_if_changed(self['favorite_info'], failure)
            elif success:
                set_text_if_changed(self['favorite_info'], success)
        self._favorite_operations = pending
        if pending:
            self._favorite_operation_timer.start(80, True)

    def save_move(self):
        if not self._move_active:
            return
        keys = [str(getattr(item, 'favorite_key', '') or '') for item in self._entries]
        content_type = self._active_filter()[0]
        result = self.favorite_store.reorder_async(self.favorite_scope, keys, content_type=content_type)
        if result is None:
            self.cancel_move()
            set_text_if_changed(self['favorite_info'], _('Could not save the favorite order.'))
            return
        self._leave_move_mode()
        self._refresh()
        set_text_if_changed(self['favorite_info'], _('Please wait'))
        self._watch_favorite_operation(result, _('Could not save the favorite order.'), _('Favorite order saved.'))

    def cancel_move(self):
        if not self._move_active:
            return
        self._entries = list(self._move_snapshot or ())
        self.selected_index = min(self._move_snapshot_index, max(0, len(self._entries) - 1))
        self._leave_move_mode()
        self._refresh()
        set_text_if_changed(self['favorite_info'], _('Move cancelled.'))

    def ok_action(self):
        if self._move_active:
            self.save_move()
        else:
            self.open_selected()

    def exit_action(self):
        if self._move_active:
            self.cancel_move()
        else:
            self.close()

    def refresh_favorites(self):
        if not self._move_active:
            self.start_load()

    def remove_selected(self):
        if not self._entries or self._loading or self._move_active:
            return
        item = self._entries[self.selected_index]
        key = str(getattr(item, 'favorite_key', '') or '')
        desired, result = self.favorite_store.toggle_async(self.favorite_scope, item, desired=False) if key else (None, None)
        if desired is not False:
            set_text_if_changed(self['message'], _('Could not update favorites.'))
            return
        self._watch_favorite_operation(result, _('Could not update favorites.'))
        self._entries.pop(self.selected_index)
        item_type = str(getattr(item, 'content_type', '') or '')
        self._favorite_counts[''] = max(0, self._favorite_counts.get('', 0) - 1)
        if item_type in self._favorite_counts:
            self._favorite_counts[item_type] = max(0, self._favorite_counts.get(item_type, 0) - 1)
        self.selected_index = min(self.selected_index, max(0, len(self._entries) - 1))
        self._continue_poster_page_key = None
        self._refresh()
        ellipsize_dynamic_text(self['message'], _('{} removed from favorites.').format(item.name), fallback_chars=80)

    def open_selected(self):
        if not self._entries or self._loading or self._move_active:
            return
        item = self._entries[self.selected_index]
        if _is_m3u_client(self.client):
            if bool(getattr(item, 'm3u_stale', False)):
                set_text_if_changed(self['message'], _('This channel is no longer present in the M3U source.'))
                return
            playable = [entry for entry in self._entries if not bool(getattr(entry, 'm3u_stale', False))]
            try:
                selected_index = playable.index(item)
                from .browser_player import open_extplayer
                open_extplayer(self.session, self.client, item, playable, selected_index, owner=self)
            except (ContentError, TypeError, ValueError):
                set_text_if_changed(self['message'], _('This channel is no longer present in the M3U source.'))
            return
        if item.content_type == 'series':
            parent_id = str(getattr(item, 'parent_id', '') or '').strip()
            series = item
            if parent_id and parent_id != str(item.stream_id):
                series = ContentItem('series', parent_id, item.name, category_id=getattr(item, 'category_id', ''), icon=getattr(item, 'icon', ''), rating=getattr(item, 'rating', ''), year=getattr(item, 'year', ''))
                if getattr(item, 'source_type', '') == 'stalker':
                    series.source_type = 'stalker'
                    series.portal_request_type = str(getattr(item, 'portal_request_type', '') or '')
                series.catalog_category_id = str(getattr(item, 'catalog_category_id', '') or '')
            self._suspend_for_playback()
            episode_client = self.client if _is_stalker_client(self.client) else content_client_for(self.account)
            try:
                from .browser_episodes import GTEpisodeListScreen
                self.session.open(GTEpisodeListScreen, self.account, series, episode_client, favorite_store=self.favorite_store)
            except Exception as error:
                log_event('favorites', 'Could not open favorite series id={}'.format(_debug_identifier(getattr(series, 'stream_id', ''))), error=error)
                set_text_if_changed(self['message'], _('Could not open the content list.'))
            return
        playable = self._entries_for_type(item.content_type)
        selected_index = 0
        item_key = str(getattr(item, 'favorite_key', '') or '')
        for index, candidate in enumerate(playable):
            if str(getattr(candidate, 'favorite_key', '') or '') == item_key:
                selected_index = index
                break
        if playable:
            from .browser_player import open_extplayer
            open_extplayer(self.session, self.client, playable[selected_index], playable, selected_index, owner=self)

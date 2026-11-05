# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""vod; loaded when its screen is needed."""

from .ui_update import set_visible
from .typography import set_text_if_changed
from .search_file_presentation import skin_after as search_file_skin_after, refresh_after as search_file_refresh_after, setup as setup_search_file
import math
import os
import threading
import time
from collections import OrderedDict, deque
from contextlib import nullcontext
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.Screen import Screen
from enigma import ePoint, eSize, eTimer, getDesktop, iServiceInformation
from . import sorting_ui as content_sort_ui
from .provider_preview import catalogue_info
from .artwork import is_tmdb_artwork_url, poster_batch_timeout, tmdb_poster_url
from .content import ContentError, XtreamContentClient
from .diagnostics import log_event
from .i18n import N_, _, localized_upper
from .background import attach_native_alpha_pixmap, attach_pixmap
from .source_loader import SourceLoader
from .vod_discovery import DiscoveryVODMethods, skin as discovery_vod_skin
from .hero_artwork import prepare_hero, preparation_allowance, is_composite, release_prepared
from .category_visibility import category_filter_active, filter_hidden_category_items, hidden_category_match_ids
from .metadata import MetadataError, complete_movie_for_device_language, media_metadata_ready, complete_media_for_device_language
from .movie_catalog import MovieCatalogIndex, SeriesCatalogIndex, CatalogStorageError, CatalogLimitError, catalog_failure_message, RAM_UNAVAILABLE
from .vod_grid import CINEMATIC_POSTER_COLUMNS, GRID_COLUMNS, GRID_PAGE_SIZE, LatestPosterWorkers, grid_move, page_position, poster_grid_skin
from .native_browse import NativeBrowseWindow
from .remote_footer import decorate_remote_footer, prepared_skin, install_remote_footer
from .scrollbar import hide_scrollbar, update_scrollbar
from .typography import ellipsize_dynamic_text, fit_dynamic_text, font_px, set_static_text, set_text_if_changed
from .browser_common import ALL_MOVIES_SEARCH_FOOTER_ITEMS, ALL_MOVIES_SEARCH_PAGE_SIZE, ALL_SERIES_SEARCH_FOOTER_ITEMS, BACKGROUND_POSTER_WORKERS, CINEMATIC_BACKDROP_TIMEOUT_SECONDS, CINEMATIC_CANDIDATE_TIMEOUT_SECONDS, CINEMATIC_CATEGORY_PAGE_SIZE, CINEMATIC_CATEGORY_ROW_HEIGHT, CINEMATIC_FALLBACK_SHADOW_PATH, FAVORITE_STAR_PATH, HERO_DEBOUNCE_MS, MOVIE_CATEGORY_TIMEOUT_SECONDS, MOVIE_SEARCH_TIMEOUT_SECONDS, NAVIGATION_DEBOUNCE_MS, PICON_DOWNLOAD_TIMEOUT, SERIES_GRID_COLUMNS, SERIES_GRID_PAGE_SIZE, SERIES_PROGRESSIVE_SCAN_DELAY_MS, SERIES_RAIL_PAGE_SIZE, SERIES_SEARCH_RESULT_LIMIT, WORKER_DETAIL_TIMEOUT_SECONDS, WORKER_LIST_TIMEOUT_SECONDS, WORKER_POSTER_TIMEOUT_SECONDS, _FavoriteSupport, _WorkerCancelled, _WorkerToken, _attach_subtitle_detail_metadata, _bounded_client_call, _cached_picon_path, _cancel_worker_token, _cinematic_cover_ratio, _clear_picon_failure, _connect_timer, _debug, _debug_identifier, _detail_cache_get, _detail_cache_put, _discard_invalid_live_picon_cache, _download_client_image, _is_stalker_client, _mark_picon_failure, _movie_search_text, _pending_vod_detail, _picon_cache_path, _picon_failure_remaining, _poster_jobs, _poster_metadata_token, _provider_picon_timeout, _rating_label, _safe_picon_url, _scale, _set_component_visible, _valid_cinematic_backdrop_file, _year_not_in_title, content_client_for

def _cinematic_scroll_geometries(width, height, px):
    """Keep runtime cursors pixel-identical to the cinematic skin."""
    list_geometry = (px(8 + 348 - 14), px(4 + 54), px(8), CINEMATIC_CATEGORY_PAGE_SIZE * px(CINEMATIC_CATEGORY_ROW_HEIGHT) - px(8))
    rail_x = px(365)
    poster_geometry = (rail_x, height - px(90) - px(28), width - rail_x, px(6))
    return (list_geometry, poster_geometry)

def _movie_browser_skin(cinematic=False):
    """Remote-first VOD library: categories, poster rail and film details."""
    width, height, px = _scale()
    if cinematic:
        return discovery_vod_skin('movie', width, height, px, FAVORITE_STAR_PATH)
    if not cinematic:
        return poster_grid_skin('movie', width, height, px, FAVORITE_STAR_PATH, _movie_browser_skin(True))

@search_file_skin_after('search')
def _all_movies_search_skin():
    """Lightweight full-catalog movie search without artwork workers."""
    width, height, px = _scale()
    footer_y = height - px(82)
    query_y = px(108)
    query_h = px(76)
    list_x = px(42)
    list_y = px(205)
    list_w = px(1120)
    list_h = footer_y - list_y - px(28)
    detail_x = px(1200)
    detail_y = list_y
    detail_w = width - detail_x - px(42)
    detail_h = list_h
    row_y = list_y + px(62)
    row_h = px(64)
    rows = []
    for index in range(ALL_MOVIES_SEARCH_PAGE_SIZE):
        y = row_y + index * row_h
        rows.append('\n    <widget name="catalog_result_focus_{index}" position="{x},{y}"\n            size="{w},{h}" font="Regular;1" backgroundColor="#172A45"\n            transparent="0" zPosition="2" />\n    <widget name="catalog_result_marker_{index}" position="{x},{y}"\n            size="{marker_w},{h}" font="Regular;1" backgroundColor="#22D3EE"\n            transparent="0" zPosition="3" />\n    <widget name="catalog_result_{index}" position="{text_x},{y}"\n            size="{text_w},{h}" font="Regular;{font}"\n            foregroundColor="#F8FAFC" transparent="1" zPosition="4"\n            noWrap="1" valign="center" halign="left" />'.format(index=index, x=list_x + px(18), y=y, w=list_w - px(44), h=row_h - px(7), marker_w=px(5), text_x=list_x + px(40), text_w=list_w - px(84), font=px(21)))
    return '\n<screen name="GTAllMoviesSearchScreen" position="0,0" size="{width},{height}"\n        flags="wfNoBorder" backgroundColor="#020617">\n    <widget name="app_bg" position="0,0" size="{width},{height}" zPosition="0" />\n    <widget name="top_accent" position="0,0" size="{width},{accent_h}"\n            font="Regular;1" backgroundColor="#8B5CF6" transparent="0" />\n    <widget name="header" position="{header_x},{header_y}"\n            size="{header_w},{header_h}" font="Regular;{header_font}"\n            foregroundColor="#FFFFFF" transparent="1"\n            valign="center" halign="left" />\n    <widget name="server" position="{server_x},{header_y}"\n            size="{server_w},{header_h}" font="Regular;{server_font}"\n            foregroundColor="#94A3B8" transparent="1"\n            valign="center" halign="right" />\n    <widget name="query_panel" position="{list_x},{query_y}"\n            size="{query_w},{query_h}" font="Regular;1"\n            backgroundColor="#0D1729" transparent="0" />\n    <widget name="query_accent" position="{list_x},{query_y}"\n            size="{line_w},{query_h}" font="Regular;1"\n            backgroundColor="#D946EF" transparent="0" zPosition="2" />\n    <widget name="query_caption" position="{query_caption_x},{query_y}"\n            size="{query_caption_w},{query_h}" font="Regular;{caption_font}"\n            foregroundColor="#D8B4FE" transparent="1"\n            valign="center" halign="left" />\n    <widget name="query_text" position="{query_text_x},{query_y}"\n            size="{query_text_w},{query_h}" font="Regular;{query_font}"\n            foregroundColor="#FFFFFF" transparent="1"\n            valign="center" halign="left" />\n    <widget name="result_panel" position="{list_x},{list_y}"\n            size="{list_w},{list_h}" font="Regular;1"\n            backgroundColor="#0D1729" transparent="0" />\n    <widget name="result_accent" position="{list_x},{list_y}"\n            size="{list_w},{line_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="2" />\n    <widget name="result_caption" position="{caption_x},{caption_y}"\n            size="{caption_w},{caption_h}" font="Regular;{caption_font}"\n            foregroundColor="#22D3EE" transparent="1"\n            valign="center" halign="left" />\n    {rows}\n    <widget name="results_scroll_track" position="{scroll_x},{scroll_y}"\n            size="{scroll_w},{scroll_h}" font="Regular;1"\n            backgroundColor="#26344E" transparent="0" zPosition="5" />\n    <widget name="results_scroll_thumb" position="{scroll_x},{scroll_y}"\n            size="{scroll_w},{scroll_h}" font="Regular;1"\n            backgroundColor="#22D3EE" transparent="0" zPosition="6" />\n    <widget name="detail_panel" position="{detail_x},{detail_y}"\n            size="{detail_w},{detail_h}" font="Regular;1"\n            backgroundColor="#0D1729" transparent="0" />\n    <widget name="detail_accent" position="{detail_x},{detail_y}"\n            size="{detail_w},{line_h}" font="Regular;1"\n            backgroundColor="#D946EF" transparent="0" zPosition="2" />\n    <widget name="detail_title" position="{detail_text_x},{detail_title_y}"\n            size="{detail_text_w},{detail_title_h}"\n            font="Regular;{detail_title_font}" foregroundColor="#FFFFFF"\n            transparent="1" noWrap="0" valign="center" halign="left" />\n    <widget name="detail_rating" position="{detail_text_x},{rating_y}"\n            size="{rating_w},{rating_h}" font="Regular;{rating_font}"\n            foregroundColor="#FACC15" transparent="1"\n            valign="center" halign="left" />\n    <widget name="detail_meta" position="{meta_x},{rating_y}"\n            size="{meta_w},{rating_h}" font="Regular;{meta_font}"\n            foregroundColor="#CBD5E1" transparent="1"\n            valign="center" halign="left" />\n    <widget name="detail_category" position="{detail_text_x},{category_y}"\n            size="{detail_text_w},{category_h}" font="Regular;{category_font}"\n            foregroundColor="#22D3EE" transparent="1"\n            valign="center" halign="left" />\n    <widget name="detail_plot_caption" position="{detail_text_x},{plot_caption_y}"\n            size="{detail_text_w},{plot_caption_h}"\n            font="Regular;{caption_font}" foregroundColor="#A855F7"\n            transparent="1" valign="center" halign="left" />\n    <widget name="detail_plot" position="{detail_text_x},{plot_y}"\n            size="{detail_text_w},{plot_h}" font="Regular;{plot_font}"\n            foregroundColor="#E2E8F0" transparent="1" noWrap="0"\n            valign="top" halign="left" />\n    <widget name="status" position="{detail_text_x},{status_y}"\n            size="{detail_text_w},{status_h}" font="Regular;{status_font}"\n            foregroundColor="#94A3B8" transparent="1" noWrap="0"\n            valign="bottom" halign="left" />\n    <widget name="message" position="{list_x},{message_y}"\n            size="{list_w},{message_h}" font="Regular;{message_font}"\n            foregroundColor="#94A3B8" transparent="1"\n            valign="center" halign="center" />\n    <widget name="footer" position="0,{footer_y}" size="{width},{footer_h}"\n            font="Regular;{footer_font}" foregroundColor="#C8D2E3"\n            backgroundColor="#080E1A" transparent="0"\n            valign="center" halign="center" />\n</screen>\n'.format(width=width, height=height, accent_h=px(5), header_x=px(50), header_y=px(22), header_w=px(760), header_h=px(70), header_font=px(38), server_x=px(850), server_w=width - px(900), server_font=px(19), query_y=query_y, query_h=query_h, query_w=width - px(84), line_w=px(6), query_caption_x=list_x + px(28), query_caption_w=px(180), query_text_x=list_x + px(218), query_text_w=width - list_x - px(260), query_font=px(25), list_x=list_x, list_y=list_y, list_w=list_w, list_h=list_h, line_h=px(4), caption_x=list_x + px(28), caption_y=list_y + px(10), caption_w=list_w - px(56), caption_h=px(42), caption_font=px(21), rows=''.join(rows), scroll_x=list_x + list_w - px(16), scroll_y=row_y, scroll_w=px(8), scroll_h=row_h * ALL_MOVIES_SEARCH_PAGE_SIZE - px(7), detail_x=detail_x, detail_y=detail_y, detail_w=detail_w, detail_h=detail_h, detail_text_x=detail_x + px(30), detail_text_w=detail_w - px(60), detail_title_y=detail_y + px(30), detail_title_h=px(122), detail_title_font=px(35), rating_y=detail_y + px(164), rating_w=px(190), rating_h=px(40), rating_font=px(24), meta_x=detail_x + px(220), meta_w=detail_w - px(250), meta_font=px(20), category_y=detail_y + px(214), category_h=px(44), category_font=px(20), plot_caption_y=detail_y + px(278), plot_caption_h=px(36), plot_y=detail_y + px(324), plot_h=px(220), plot_font=px(28), status_y=detail_y + detail_h - px(170), status_h=px(140), status_font=px(19), message_y=list_y + list_h - px(55), message_h=px(45), message_font=px(18), footer_y=footer_y, footer_h=height - footer_y, footer_font=px(18))

def _series_browser_skin(cinematic=False):
    """Series categories, eight-card grid and selected-series preview."""
    width, height, px = _scale()
    if cinematic:
        return discovery_vod_skin('series', width, height, px, FAVORITE_STAR_PATH)
    if not cinematic:
        return poster_grid_skin('series', width, height, px, FAVORITE_STAR_PATH, _series_browser_skin(True))

class GTAllMoviesSearchScreen(_FavoriteSupport, Screen):
    """Search complete shared RAM rows, or a separate account-wide catalogue."""
    page_size = ALL_MOVIES_SEARCH_PAGE_SIZE
    provider_page_size = 14
    page_cache_limit = 5
    content_type = 'movie'
    _search_title_message = N_('Search All Movies')
    _query_hint_message = N_('Enter a movie name.')
    _result_title_message = N_('All Movies')
    _selecting_title_message = N_('Selecting movie...')
    _summary_title_message = N_('MOVIE SUMMARY')
    _empty_summary_message = N_('Movie summary unavailable.')
    _load_error_message = N_('Could not load movies')
    _no_match_message = N_('No movies matched this search.')
    _item_noun_message = N_('Movie')

    def __init__(self, session, account, client=None, favorite_store=None, hidden_category_ids=None, initial_query=None, shared_catalog=None):
        search_footer = ALL_SERIES_SEARCH_FOOTER_ITEMS if self.content_type == 'series' else ALL_MOVIES_SEARCH_FOOTER_ITEMS
        self.skin = decorate_remote_footer(prepared_skin(_all_movies_search_skin), search_footer, transparent_panel=True, show_dividers=False, stacked=True)
        Screen.__init__(self, session)
        self.account = account
        self.client = client or content_client_for(account)
        self._init_favorite_support(favorite_store)
        self._hidden_category_ids = hidden_category_match_ids((), hidden_category_ids)
        self._closed = False
        self._query = ''
        self._query_key = ''
        self._results = []
        self.selected_index = 0
        self._current_page = 1
        self._result_page_size = self.provider_page_size
        self._total_items = 0
        self._has_more = False
        self._known_last_page = None
        self._supported = True
        self._loading = False
        self._search_reason = ''
        self._search_source = ''
        self._search_generation = 0
        self._search_token = None
        self._requested_page = 0
        self._pending_page_index = 0
        self._page_cache = OrderedDict()
        self._event_lock = threading.Lock()
        self._events = deque()
        self._catalog_mode = self.content_type == 'series' or str(getattr(self.client, 'source_type', '')).lower() in ('xtream', 'stalker') or isinstance(self.client, XtreamContentClient)
        self._catalog_index = shared_catalog
        self._shared_catalog = shared_catalog is not None
        self._owns_search_client = not self._shared_catalog
        self._catalog_started = self._shared_catalog
        self._catalog_preparing = False
        self._catalog_state = shared_catalog.state() if self._shared_catalog else {'complete': False, 'movies': 0}
        self._catalog_page_complete = False
        self._catalog_token = None
        self._catalog_generation = 0
        self._catalog_mailbox = None
        self._catalog_refresh_pending = False
        self._catalog_next_refresh = 0.0
        self._provider_worker_lock = threading.Lock()
        self._provider_worker_count = 0
        self._provider_workers_idle = threading.Event()
        self._provider_workers_idle.set()
        self._keyboard_requested = False
        self._initial_query = initial_query
        self._poll_timer = eTimer()
        _connect_timer(self._poll_timer, self._poll_search)
        self._keyboard_timer = eTimer()
        _connect_timer(self._keyboard_timer, self._open_initial_search)
        self['top_accent'] = Label('')
        self['header'] = Label(_(self._search_title_message))
        self['server'] = Label('')
        self._dynamic_search_server_text = account.host or account.display_name
        self._refit_search_server()
        self['query_panel'] = Label('')
        self['query_accent'] = Label('')
        self['query_caption'] = Label(_('Search'))
        self['query_text'] = Label(_(self._query_hint_message))
        self['result_panel'] = Label('')
        self['result_accent'] = Label('')
        self['result_caption'] = Label(_(self._result_title_message))
        for index in range(self.page_size):
            self['catalog_result_{}'.format(index)] = Label('')
            self['catalog_result_focus_{}'.format(index)] = Label('')
            self['catalog_result_marker_{}'.format(index)] = Label('')
        self['results_scroll_track'] = Label('')
        self['results_scroll_thumb'] = Label('')
        hide_scrollbar(self, 'results_scroll')
        self['detail_panel'] = Label('')
        self['detail_accent'] = Label('')
        self['detail_title'] = Label(_(self._selecting_title_message))
        self['detail_rating'] = Label('')
        self['detail_meta'] = Label('')
        self['detail_category'] = Label('')
        self['detail_plot_caption'] = Label(_(self._summary_title_message))
        self['detail_plot'] = Label(_(self._query_hint_message))
        self['status'] = Label(_(self._query_hint_message))
        self['message'] = Label('')
        self['footer'] = Label('')
        install_remote_footer(self, search_footer, stacked=True)
        width, height, px = _scale()
        self._search_title_preferred_font = font_px(px, 35, role='title')
        self._search_title_min_font = font_px(px, 35, role='title', value='standard')
        list_x = px(42)
        list_y = px(205)
        row_y = list_y + px(62)
        row_h = px(64)
        self._scroll_geometry = (list_x + px(1120) - px(16), row_y, px(8), row_h * self.page_size - px(7))
        self['download_actions'] = ActionMap(['MenuActions'], {'menu': self.request_selected_download}, -1)
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions', 'ColorActions'], {'ok': self.open_selected, 'cancel': self.close, 'up': self.move_up, 'down': self.move_down, 'upRepeated': self.move_up, 'downRepeated': self.move_down, 'left': self.page_up, 'right': self.page_down, 'leftRepeated': self.page_up, 'rightRepeated': self.page_down, 'red': self.toggle_favorite, 'green': self.refresh_catalog, 'blue': self.open_keyboard}, -1)
        if hasattr(self, 'onShown'):
            self.onShown.append(self._screen_shown)
        if hasattr(self, 'onLayoutFinish'):
            self.onLayoutFinish.append(self._refit_search_server)
        if hasattr(self, 'onClose'):
            self.onClose.append(self._stop)
        self.setTitle(_(self._search_title_message))
        self._render()
        setup_search_file(self, 'search', search_footer)

    def _refit_search_server(self):
        ellipsize_dynamic_text(self['server'], self._dynamic_search_server_text, fallback_chars=80)

    def _screen_shown(self):
        if self._closed:
            return
        if self._favorite_screen_shown():
            self._render()
        if not self._keyboard_requested:
            self._keyboard_requested = True
            self._keyboard_timer.start(40, True)

    def _open_initial_search(self):
        if self._closed:
            return
        if self._initial_query is not None:
            query, self._initial_query = (self._initial_query, None)
            self._query_entered(query)
        else:
            self.open_keyboard()

    def open_keyboard(self):
        if self._closed:
            return
        try:
            self._keyboard_timer.stop()
        except Exception:
            pass
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self['status'].setText(_('The virtual keyboard is unavailable on this image.'))
            return
        opener = getattr(self.session, 'openWithCallback', None)
        if not callable(opener):
            self['status'].setText(_('Could not open the virtual keyboard.'))
            return
        opener(self._query_entered, VirtualKeyBoard, title=_(self._search_title_message), text=self._query)

    def _cancel_search_request(self, invalidate_generation=False):
        if invalidate_generation:
            self._search_generation += 1
        _cancel_worker_token(self._search_token)
        self._search_token = None
        self._loading = False
        self._requested_page = 0
        with self._event_lock:
            discarded = tuple(self._events)
            self._events.clear()
        for event in discarded:
            if event[0] in ('page', 'preview'):
                self._release_search_page(event[3])
        try:
            self._poll_timer.stop()
        except Exception:
            pass

    @staticmethod
    def _release_search_page(result):
        items = getattr(result, 'items', None)
        if getattr(items, 'full_search', False):
            items.close()

    def _release_search_results(self):
        if getattr(self._results, 'full_search', False):
            self._results.close()
        self._results = []

    def _reset_search_results(self):
        self._release_search_results()
        self.selected_index = 0
        self._current_page = 1
        self._result_page_size = self.provider_page_size
        self._total_items = 0
        self._has_more = False
        self._known_last_page = None
        self._supported = True
        self._search_reason = ''
        self._search_source = ''

    def _query_entered(self, value):
        if self._closed or value is None:
            return
        query = ' '.join(str(value or '').split())[:120]
        query_key = _movie_search_text(query)
        changed = query_key != self._query_key
        self._query = query
        ellipsize_dynamic_text(self['query_text'], query or _(self._query_hint_message), fallback_chars=90)
        if changed:
            self._cancel_search_request(invalidate_generation=True)
            self._page_cache.clear()
            self._reset_search_results()
            self._query_key = query_key
        if not query_key:
            self._catalog_refresh_pending = False
            self._render()
            return
        if self._catalog_mode and (not self._catalog_started):
            self._start_catalog()
        self._load_page(1, pending_index=0)

    def _start_catalog(self, force=False):
        if self._closed or self._catalog_preparing:
            return
        if self._shared_catalog:
            if not force:
                return
            self._catalog_index.close()
            self._catalog_index = None
            self._shared_catalog = False
            self._catalog_page_complete = False
        if self._catalog_index is None:
            self._catalog_index = SeriesCatalogIndex(self.client) if self.content_type == 'series' else MovieCatalogIndex(self.client)
            self._catalog_index.category_list = True
            self._catalog_index.scope += ':plain-search'
        self._catalog_started = True
        self._catalog_preparing = True
        self._catalog_state = dict(self._catalog_state, complete=False, reason='')
        self._catalog_generation += 1
        generation = self._catalog_generation
        token = self._catalog_token = _WorkerToken(30 * 60)
        self._provider_worker_started()
        worker = threading.Thread(target=self._catalog_worker, args=(generation, token, force))
        worker.daemon = True
        try:
            worker.start()
        except (RuntimeError, OSError):
            self._provider_worker_finished()
            token.cancel()
            self._catalog_token = None
            self._catalog_preparing = False
            self._catalog_state['reason'] = 'storage'
        self._poll_timer.start(60, True)

    def _catalog_worker(self, generation, token, force):

        def progress(state):
            with self._event_lock:
                if not self._closed and generation == self._catalog_generation:
                    self._catalog_mailbox = (generation, dict(state))
        try:
            result = self._catalog_index.prepare(token, callback=progress, force=force)
            progress(dict(result, preparing=False))
        except Exception:
            progress(dict(self._catalog_state, complete=False, preparing=False, reason='storage'))
        finally:
            self._provider_worker_finished()

    def _cache_key(self, page):
        return (self._query_key, int(page))

    def _cached_page(self, page):
        key = self._cache_key(page)
        result = self._page_cache.get(key)
        if result is not None:
            self._page_cache.move_to_end(key)
        return result

    def _cache_page(self, result, page):
        if getattr(result, 'progress_preview', False):
            return
        if getattr(getattr(result, 'items', None), 'full_search', False):
            return
        key = self._cache_key(page)
        self._page_cache[key] = result
        self._page_cache.move_to_end(key)
        while len(self._page_cache) > self.page_cache_limit:
            self._page_cache.popitem(last=False)

    @staticmethod
    def _page_value(result, name, default=None):
        if isinstance(result, dict):
            return result.get(name, default)
        return getattr(result, name, default)

    def _load_page(self, page, pending_index=0):
        if self._closed or not self._query_key:
            return False
        try:
            page = max(1, int(page))
        except (TypeError, ValueError, OverflowError):
            page = 1
        if self._catalog_mode:
            page = 1
        if self._known_last_page is not None and page > self._known_last_page:
            self._has_more = False
            self._render()
            return False
        cached = self._cached_page(page)
        if cached is not None:
            self._apply_search_page(cached, page, pending_index)
            return True
        if self._loading:
            self['status'].setText(_('Please wait'))
            return False
        searcher = getattr(self.client, 'search_movies', None)
        if not self._catalog_mode and (not callable(searcher)):
            self._supported = False
            self._search_reason = 'unsupported'
            self._results = []
            self._render()
            return False
        token = _WorkerToken(MOVIE_SEARCH_TIMEOUT_SECONDS)
        self._search_token = token
        self._loading = True
        self._requested_page = page
        self._pending_page_index = int(pending_index)
        self._search_reason = ''
        self._search_generation += 1
        generation = self._search_generation
        worker = threading.Thread(target=self._search_page_worker, args=(generation, self._query, page, token))
        worker.daemon = True
        self._provider_worker_started()
        try:
            worker.start()
        except (RuntimeError, OSError):
            self._provider_worker_finished()
            _cancel_worker_token(token)
            if self._search_token is token:
                self._search_token = None
                self._loading = False
                self._requested_page = 0
                self._search_reason = 'search_error'
            self._render()
            return False
        self._render()
        self._poll_timer.start(60, True)
        return True

    def _queue_search_event(self, event):
        with self._event_lock:
            if not self._closed and event[1] == self._search_generation:
                self._events.append(event)
                return
        if event[0] in ('page', 'preview'):
            self._release_search_page(event[3])

    def _provider_worker_started(self):
        with self._provider_worker_lock:
            self._provider_worker_count += 1
            self._provider_workers_idle.clear()

    def _provider_worker_finished(self):
        with self._provider_worker_lock:
            self._provider_worker_count = max(0, self._provider_worker_count - 1)
            if self._provider_worker_count == 0:
                self._provider_workers_idle.set()

    def _search_page_worker(self, generation, query, page, token):
        pending_result = None
        try:
            try:
                token.check()
                if getattr(self, '_catalog_mode', False):
                    if not self._catalog_preparing:
                        pending_result = self._catalog_index.search_all(query,
                            hidden_category_ids=self._hidden_category_ids, token=token)
                    else:
                        pending_result = self._catalog_index.search(query, page=page, page_size=self.provider_page_size, hidden_category_ids=self._hidden_category_ids, token=token)
                        pending_result.has_more = False
                        pending_result.progress_preview = True
                    token.check()
                    result, pending_result = pending_result, None
                    self._queue_search_event(('page', generation, page, result))
                    if not result.items and (not result.catalog_state.get('complete')) and (self.content_type == 'movie') and (page == 1) and (str(getattr(self.client, 'source_type', '')) == 'stalker') and (not self._hidden_category_ids):
                        try:
                            deadline = min(token.deadline, time.monotonic() + 5.0)
                            with self.client.request_scope(cancel_event=token.cancel_event, deadline=deadline):
                                portal = self.client.search_movies(query, page=page, page_size=self.provider_page_size)
                            token.check()
                            if portal.supported and portal.items:
                                self._queue_search_event(('preview', generation, page, portal))
                        except (ContentError, _WorkerCancelled):
                            pass
                    return
                searcher = getattr(self.client, 'search_movies', None)
                if not callable(searcher):
                    self._queue_search_event(('error', generation, page, 'unsupported'))
                    return

                def search_page():
                    source_type = str(getattr(self.client, 'source_type', '') or '').lower()
                    if self._hidden_category_ids and (source_type == 'xtream' or isinstance(self.client, XtreamContentClient)):
                        try:
                            return searcher(query, page=page, page_size=self.provider_page_size, hidden_category_ids=self._hidden_category_ids)
                        except TypeError:
                            pass
                    return searcher(query, page=page, page_size=self.provider_page_size)
                request_scope = getattr(self.client, 'request_scope', None)
                if callable(request_scope):
                    with request_scope(cancel_event=token.cancel_event, deadline=token.deadline):
                        result = search_page()
                else:
                    result = search_page()
                token.check()
                fallback = not self._page_value(result, 'supported', True) and callable(getattr(self.client, 'load_vod_catalog_step', None))
                self._queue_search_event(('fallback' if fallback else 'page', generation, page, result))
            except _WorkerCancelled:
                return
            except (CatalogStorageError, CatalogLimitError, MemoryError) as error:
                reason = ('available-memory' if isinstance(error, MemoryError)
                          or getattr(error, 'limit_kind', '') == 'available-memory'
                          else 'limit' if isinstance(error, CatalogLimitError) else 'storage')
                self._queue_search_event(('error', generation, page, reason))
            except ContentError as error:
                if error.reason == ContentError.UNSUPPORTED_TYPE and callable(getattr(self.client, 'load_vod_catalog_step', None)):
                    self._queue_search_event(('fallback', generation, page, None))
                    return
                self._queue_search_event(('error', generation, page, str(getattr(error, 'reason', '') or 'search_error')))
            except Exception:
                self._queue_search_event(('error', generation, page, 'search_error'))
        finally:
            self._release_search_page(pending_result)
            self._provider_worker_finished()

    def _apply_search_page(self, result, requested_page, pending_index=0):
        previous_page = self._current_page
        previous_page_size = self._result_page_size
        supported = bool(self._page_value(result, 'supported', True))
        page = self._page_value(result, 'page', requested_page)
        try:
            page = max(1, int(page))
        except (TypeError, ValueError, OverflowError):
            page = max(1, int(requested_page))
        self._search_source = str(self._page_value(result, 'source', '') or '')
        if self._search_source == 'catalog':
            self._catalog_page_complete = bool(self._page_value(result, 'catalog_state', {}).get('complete', False))
        try:
            result_page_size = int(self._page_value(result, 'page_size', self.provider_page_size) or self.provider_page_size)
        except (TypeError, ValueError, OverflowError):
            result_page_size = self.provider_page_size
        result_page_size = max(1, result_page_size)
        if not supported:
            if page > 1 and self._results:
                self._known_last_page = max(1, page - 1)
                self._total_items = (previous_page - 1) * previous_page_size + len(self._results)
                self._has_more = False
                self._search_reason = 'unsupported'
                self._render()
                return
            self._cache_page(result, page)
            self._supported = False
            self._search_reason = 'unsupported'
            self._current_page = page
            self._result_page_size = result_page_size
            self._results = []
            self.selected_index = 0
            self._total_items = 0
            self._has_more = False
            self._render()
            return
        self._cache_page(result, page)
        self._supported = True
        self._search_reason = ''
        raw_result_items = self._page_value(result, 'items', ())
        if getattr(raw_result_items, 'full_search', False):
            # Hidden categories were filtered in the worker. Preserve the RAM
            # view rather than hydrating/copying all matching items here.
            result_items = raw_result_items
        else:
            raw_result_items = list(raw_result_items or ())
            result_items = filter_hidden_category_items(raw_result_items, self._hidden_category_ids)
        result_has_more = bool(self._page_value(result, 'has_more', False))
        try:
            result_total = max(0, int(self._page_value(result, 'total_items', 0) or 0))
        except (TypeError, ValueError, OverflowError):
            result_total = 0
        if self._search_source == 'catalog' and page > 1 and (not result_items):
            self._results = []
            self.selected_index = 0
            self._total_items = result_total
            self._current_page = max(1, (result_total + result_page_size - 1) // result_page_size)
            self._known_last_page = None
            self._catalog_refresh_pending = True
            self._catalog_next_refresh = 0.0
            self._render()
            return
        if page > 1 and (not result_items) and self._results and (not raw_result_items or not result_has_more):
            self._known_last_page = max(1, page - 1)
            self._total_items = (previous_page - 1) * previous_page_size + len(self._results)
            self._has_more = False
            self._render()
            return
        self._current_page = page
        self._result_page_size = result_page_size
        self._release_search_results()
        self._results = result_items
        self._total_items = result_total
        if not result_has_more and (not (getattr(self, '_catalog_mode', False) and (not self._catalog_state.get('complete', False)))):
            self._known_last_page = page
        self._has_more = bool(result_has_more and (self._known_last_page is None or page < self._known_last_page))
        try:
            pending_index = int(pending_index)
        except (TypeError, ValueError, OverflowError):
            pending_index = 0
        if self._results:
            if pending_index < 0:
                pending_index = len(self._results) + pending_index
            self.selected_index = min(len(self._results) - 1, max(0, pending_index))
        else:
            self.selected_index = 0
        self._render()

    def _poll_search(self):
        if self._closed:
            return
        events = []
        with self._event_lock:
            while self._events:
                events.append(self._events.popleft())
        changed = False
        for event in events:
            if event[1] != self._search_generation:
                if event[0] in ('page', 'preview'):
                    self._release_search_page(event[3])
                continue
            if event[2] != self._requested_page and (not (event[0] == 'preview' and (not self._loading) and (event[2] == self._current_page))):
                if event[0] in ('page', 'preview'):
                    self._release_search_page(event[3])
                continue
            self._loading = False
            self._search_token = None
            self._requested_page = 0
            if event[0] == 'fallback':
                self._catalog_mode = True
                self._page_cache.clear()
                self._reset_search_results()
                self._start_catalog()
                self._catalog_refresh_pending = True
            elif event[0] in ('page', 'preview'):
                pending_index = self._pending_page_index
                self._pending_page_index = 0
                self._apply_search_page(event[3], event[2], pending_index)
            else:
                self._pending_page_index = 0
                self._search_reason = str(event[3] or 'search_error')
            changed = True
        with self._event_lock:
            catalogue, self._catalog_mailbox = (self._catalog_mailbox, None)
        if catalogue is not None and catalogue[0] == self._catalog_generation:
            self._catalog_state = catalogue[1]
            self._catalog_preparing = bool(self._catalog_state.get('preparing', False))
            if not self._catalog_preparing:
                self._catalog_token = None
            self._page_cache.clear()
            self._known_last_page = None
            self._catalog_refresh_pending = True
            changed = True
        token = self._search_token
        if self._loading and token is not None and token.expired():
            _cancel_worker_token(token)
            if self._search_token is token:
                self._search_token = None
                self._loading = False
                self._requested_page = 0
                self._search_reason = ContentError.DEADLINE
                changed = True
        if changed or (not self._loading and self._search_reason):
            self._render()
        if self._catalog_refresh_pending and (not self._loading) and self._query_key and (not self._catalog_preparing or time.monotonic() >= self._catalog_next_refresh):
            self._catalog_refresh_pending = False
            self._catalog_next_refresh = time.monotonic() + 2.0
            self._load_page(self._current_page, pending_index=self.selected_index)
        with self._event_lock:
            has_events = bool(self._events)
        if self._loading or has_events or self._catalog_preparing or (self._catalog_refresh_pending and self._query_key):
            self._poll_timer.start(60, True)

    def _reason_text(self, reason):
        reason = str(reason or '')
        if reason in (ContentError.DEADLINE, 'deadline'):
            return _('Request timed out')
        if reason in (ContentError.CANCELLED, 'cancelled'):
            return _('Request cancelled')
        if reason == ContentError.ACCESS_DENIED:
            return _('Server denied access')
        if reason == ContentError.RATE_LIMITED:
            return _('Server request limit reached')
        if reason == 'unsupported':
            return _('This source does not support the selected content type.')
        return _(self._load_error_message)

    def _status_text(self):
        if not self._query_key:
            return _(self._query_hint_message)
        if not self._supported:
            return _('This source does not support the selected content type.')
        if self._catalog_mode:
            state = self._catalog_state
            if self._search_reason == 'available-memory' or state.get('limit_kind') == 'available-memory':
                return _(RAM_UNAVAILABLE)
            if self._search_reason == 'storage' or state.get('reason') == 'storage':
                return _('Search cache is unavailable. Check receiver storage.')
            if self._catalog_preparing:
                if self.content_type == 'series':
                    return _('Preparing series catalogue: {count} series').format(count=state.get('movies', 0))
                return _('Preparing search catalogue: {movies} movies').format(movies=state.get('movies', 0))
            if self._search_reason == 'limit' or state.get('reason') == 'limit':
                return _('Catalogue limit reached. Results may be incomplete.')
            if not state.get('complete'):
                if self.content_type == 'series':
                    return _('Series catalogue incomplete: {count} series. Press GREEN to continue.').format(count=state.get('movies', 0))
                return _('Catalogue incomplete: {movies} movies. Press GREEN to continue.').format(movies=state.get('movies', 0))
            if not self._search_reason:
                if self.content_type == 'series':
                    return _('{} • {} SERIES').format(_('Search'), self._total_items)
                return _('Catalogue ready: {movies} movies • {results} matches').format(movies=state.get('movies', 0), results=self._total_items)
        if self._loading:
            return _('Please wait')
        if self._search_reason:
            return self._reason_text(self._search_reason)
        return _('{movies} movies • {results} results').format(movies=self._total_items or len(self._results), results=len(self._results))

    @search_file_refresh_after
    def _render(self):
        if self._results:
            self.selected_index = min(len(self._results) - 1, max(0, self.selected_index))
        else:
            self.selected_index = 0
        if hasattr(self, '_r102_kind') and self._results:
            set_text_if_changed(self['status'], self._status_text())
            update_scrollbar(self, 'results_scroll', len(self._results), self.selected_index, self.page_size, self._scroll_geometry)
            return
        visual_start = int(self.selected_index / self.page_size) * self.page_size
        visible = self._results[visual_start:visual_start + self.page_size]
        global_start = (self._current_page - 1) * self._result_page_size
        for row in range(self.page_size):
            selected = False
            if row < len(visible):
                local_index = visual_start + row
                item = visible[row]
                favorite = '  ★' if self._is_favorite(item) else ''
                year = str(getattr(item, 'year', '') or '')
                suffix = '  •  {}'.format(year) if year else ''
                ellipsize_dynamic_text(self['catalog_result_{}'.format(row)], '{:05d}.  {}{}{}'.format(global_start + local_index + 1, str(getattr(item, 'name', '') or _(self._item_noun_message)), suffix, favorite), fallback_chars=64)
                selected = local_index == self.selected_index
            else:
                set_text_if_changed(self['catalog_result_{}'.format(row)], '')
            for name in ('catalog_result_focus_{}'.format(row), 'catalog_result_marker_{}'.format(row)):
                if selected:
                    set_visible(self[name], True)
                else:
                    set_visible(self[name], False)
        if self._results:
            update_scrollbar(self, 'results_scroll', len(self._results), self.selected_index, self.page_size, self._scroll_geometry)
            global_position = global_start + self.selected_index + 1
            total = self._total_items or global_start + len(self._results)
            set_text_if_changed(self['message'], '{}/{}'.format(global_position, total))
        else:
            hide_scrollbar(self, 'results_scroll')
            if not self._supported:
                set_text_if_changed(self['message'], _('This source does not support the selected content type.'))
            elif self._search_reason:
                set_text_if_changed(self['message'], _(self._load_error_message))
            elif self._query_key and (not self._loading):
                set_text_if_changed(self['message'], _('Search incomplete.') if self._catalog_mode and (not (self._catalog_state.get('complete') and self._catalog_page_complete)) else _(self._no_match_message))
            else:
                set_text_if_changed(self['message'], '')
        set_text_if_changed(self['status'], self._status_text())
        self._render_detail()

    def _render_detail(self):
        if not self._results:
            set_static_text(self['detail_title'], _(self._selecting_title_message), preferred_size=self._search_title_preferred_font)
            set_text_if_changed(self['detail_rating'], '')
            set_text_if_changed(self['detail_meta'], '')
            set_text_if_changed(self['detail_category'], '')
            if not self._supported:
                text = _('This source does not support the selected content type.')
            elif self._search_reason:
                text = _(self._load_error_message)
            elif self._query_key and (not self._loading):
                text = _('Search incomplete.') if self._catalog_mode and (not (self._catalog_state.get('complete') and self._catalog_page_complete)) else _(self._no_match_message)
            else:
                text = _(self._query_hint_message)
            set_text_if_changed(self['detail_plot'], text)
            return
        item = self._results[self.selected_index]
        item_name = str(getattr(item, 'name', '') or '')
        if item_name:
            fit_dynamic_text(self['detail_title'], item_name, max_lines=2, preferred_size=self._search_title_preferred_font, min_size=self._search_title_min_font, fallback_chars=30)
        else:
            set_static_text(self['detail_title'], _(self._item_noun_message), preferred_size=self._search_title_preferred_font)
        set_text_if_changed(self['detail_rating'], _rating_label(getattr(item, 'rating', '')))
        meta = [value for value in (_year_not_in_title(item_name, getattr(item, 'year', '')), str(getattr(item, 'duration', '') or '')) if value]
        ellipsize_dynamic_text(self['detail_meta'], '  •  '.join(meta) or _('Information unavailable.'), fallback_chars=34)
        ellipsize_dynamic_text(self['detail_category'], str(getattr(item, 'catalog_category_name', '') or getattr(item, 'group_title', '') or ''), fallback_chars=45)
        fit_dynamic_text(self['detail_plot'], str(getattr(item, 'plot', '') or '') or _(self._empty_summary_message), max_lines=7, fallback_chars=48)

    def _select(self, index):
        if self._loading:
            return
        try:
            index = int(index)
        except (TypeError, ValueError, OverflowError):
            return
        if not self._results:
            if index >= 0 and self._has_more:
                self._load_page(self._current_page + 1, pending_index=0)
            elif index < 0 and self._current_page > 1:
                self._load_page(self._current_page - 1, pending_index=-1)
            return
        if 0 <= index < len(self._results):
            self.selected_index = index
            self._render()
            return
        if index >= len(self._results):
            if self._has_more:
                self._load_page(self._current_page + 1, pending_index=index - len(self._results))
            else:
                self.selected_index = len(self._results) - 1
                self._render()
            return
        if self._current_page > 1:
            self._load_page(self._current_page - 1, pending_index=index)
        else:
            self.selected_index = 0
            self._render()

    def move_up(self):
        self._select(self.selected_index - 1)

    def move_down(self):
        self._select(self.selected_index + 1)

    def page_up(self):
        self._select(self.selected_index - self.page_size)

    def page_down(self):
        self._select(self.selected_index + self.page_size)

    def refresh_catalog(self):
        if self._loading or not self._provider_workers_idle.is_set():
            self['status'].setText(_('Please wait'))
            return
        if not self._query_key:
            self.open_keyboard()
            return
        self._cancel_search_request(invalidate_generation=True)
        if self._catalog_mode:
            self._start_catalog(force=True)
        self._release_xtream_search_cache()
        query_key = self._query_key
        for key in tuple(self._page_cache):
            if key[0] == query_key:
                self._page_cache.pop(key, None)
        self._reset_search_results()
        self._query_key = query_key
        self._render()
        self._load_page(1, pending_index=0)

    def toggle_favorite(self):
        if not self._results:
            self['status'].setText(_('Select content before using favorites.'))
            return
        item = self._results[self.selected_index]
        added = self._toggle_favorite_item(item)
        self._render()
        fit_dynamic_text(self['status'], self._favorite_message(item, added), max_lines=4, fallback_chars=48)

    def _release_xtream_search_cache(self):
        if not getattr(self, '_owns_search_client', True):
            return
        source_type = str(getattr(self.client, 'source_type', '') or '').lower()
        if not (source_type == 'xtream' or isinstance(self.client, XtreamContentClient)):
            return
        releaser = getattr(self.client, 'release_movie_search', None)
        if not callable(releaser):
            return
        try:
            releaser()
        except Exception as error:
            log_event('movies', 'Could not release movie search cache', error=error)

    def open_selected(self):
        if not self._results:
            return
        if self._loading:
            self['status'].setText(_('Please wait'))
            return
        if self.content_type == 'series':
            self._open_series_episodes()
            return
        from .browser_player import open_extplayer
        entries = self._results if getattr(self._results, 'full_category', False) else list(self._results)
        open_extplayer(self.session, self.client, self._results[self.selected_index], entries, self.selected_index, owner=self)

    def _stop(self):
        if self._closed:
            return
        self._closed = True
        self._catalog_generation += 1
        _cancel_worker_token(self._catalog_token)
        self._catalog_token = None
        self._catalog_preparing = False
        with self._event_lock:
            self._catalog_mailbox = None
        self._cancel_search_request(invalidate_generation=True)
        self._release_search_results()
        if self._catalog_index is not None:
            self._catalog_index.close()
        self._results = []
        self._page_cache.clear()
        try:
            self._keyboard_timer.stop()
        except Exception:
            pass
        self._release_xtream_search_cache()

class GTAllSeriesSearchScreen(GTAllMoviesSearchScreen):
    """Paged account-wide series search using the session RAM catalogue."""
    content_type = 'series'
    _search_title_message = N_('Search All Series')
    _query_hint_message = N_('Enter a series name.')
    _result_title_message = N_('All Series')
    _selecting_title_message = N_('Selecting series...')
    _summary_title_message = N_('SERIES SUMMARY')
    _empty_summary_message = N_('Series summary unavailable.')
    _load_error_message = N_('Could not load series')
    _no_match_message = N_('No series matched this search.')
    _item_noun_message = N_('Series')

    def _release_xtream_search_cache(self):
        pass

    def request_selected_download(self):
        self._open_series_episodes(download_mode=True)

    def _open_series_episodes(self, download_mode=False):
        if self._closed or not self._results or self._loading:
            return
        if getattr(self, '_series_episode_child_open', False):
            return
        self._series_episode_child_open = True
        self._catalog_generation += 1
        _cancel_worker_token(self._catalog_token)
        self._catalog_token = None
        self._catalog_preparing = False
        self._catalog_started = False
        with self._event_lock:
            self._catalog_mailbox = None
        self._cancel_search_request(invalidate_generation=True)
        try:
            episode_client = self.client if _is_stalker_client(self.client) else content_client_for(self.account)
            from .browser_episodes import GTEpisodeListScreen
            self.session.openWithCallback(self._series_episode_closed, GTEpisodeListScreen, self.account, self._results[self.selected_index], episode_client, favorite_store=self.favorite_store, download_mode=download_mode)
        except Exception as error:
            log_event('series', 'Could not open series search episodes', error=error)
            self._series_episode_closed()
            self['status'].setText(_('Could not open the content list.'))

    def _series_episode_closed(self, *args):
        self._series_episode_child_open = False
        if self._closed:
            return
        self._reload_favorite_keys()
        self._page_cache.clear()
        self._known_last_page = None
        if self._query_key:
            self._start_catalog()
            self._catalog_refresh_pending = True
            self._poll_timer.start(60, True)
        self._render()

class VODBrowserMethods(DiscoveryVODMethods):

    def _prepare_cinematic_shadows(self):
        if getattr(self, '_closed', False) or not getattr(self, '_cinematic_enabled', False):
            return
        if getattr(self, '_vod_discovery_layout', False):
            self._set_cinematic_panels(self.content_type, bool(getattr(self, '_cinematic_applied_path', '')))
            return
        plan = getattr(self, '_cinematic_composite_plan', None)
        if plan is not None:
            unused_backdrop, name = self._cinematic_widget_names(self.content_type)
            def loaded(success):
                if self._closed:
                    return
                if not success:
                    self._cinematic_composite_plan = None
                    preparation = getattr(self, '_vod_preparation', None)
                    if preparation is not None:
                        preparation.invalidate()
                    self._reset_cinematic_backdrop()
                    self._prepare_cinematic_shadows()
                    return
                self._set_cinematic_panels(self.content_type, bool(getattr(self, '_cinematic_applied_path', '')))
            attach_pixmap(self, name, plan.mask, key='cinematic-fallback-mask',
                          decode_size=plan.size, show_on_load=False, on_loaded=loaded)
            return
        for name in self._cinematic_fallback_shadow_names(self.content_type):
            attach_native_alpha_pixmap(self, name, CINEMATIC_FALLBACK_SHADOW_PATH, key='prepared-shadow-' + name, show_on_load=False)

    def _cached_grid_detail(self, kind):
        prefix = '_' + kind + '_'
        entries = getattr(self, prefix + 'entries')
        if not entries:
            return None
        item = entries[getattr(self, prefix + 'selected_index')]
        cache = getattr(self, prefix + 'info_cache')
        lock = getattr(self, prefix + 'info_cache_lock', None)
        return _detail_cache_get(cache, lock, item.stream_id) if lock is not None else cache.get(item.stream_id)

    def _schedule_grid_detail(self, kind, immediate=False):
        entries = getattr(self, '_' + kind + '_entries')
        if not entries or self._closed:
            return
        prefix = '_' + kind + '_'
        generation = getattr(self, prefix + 'generation')
        setattr(self, prefix + 'pending_detail_generation', generation)
        _cancel_worker_token(getattr(self, prefix + 'detail_token'))
        setattr(self, prefix + 'detail_token', None)
        setattr(self, prefix + 'detail_result', None)
        setattr(self, prefix + 'detail_preview', None)
        timer = getattr(self, prefix + 'detail_timer')
        timer.stop()
        item = entries[getattr(self, prefix + 'selected_index')]
        info = self._cached_grid_detail(kind)
        ready = media_metadata_ready(info)
        self._render_grid_detail(kind, info, loading=not ready)
        self._apply_grid_cover(kind, '', getattr(info, 'cover', ''), getattr(item, 'icon', ''))
        if ready:
            setattr(self, prefix + 'pending_detail_generation', None)
            setattr(self, prefix + 'detail_pending', False)
            return
        if immediate:
            getattr(self, '_commit_' + kind + '_detail')()
        else:
            timer.start(NAVIGATION_DEBOUNCE_MS, True)

    def _start_grid_detail(self, kind):
        prefix = '_' + kind + '_'
        entries = getattr(self, prefix + 'entries')
        if not entries or self._closed:
            return
        generation = getattr(self, prefix + 'generation')
        item = entries[getattr(self, prefix + 'selected_index')]
        token = _WorkerToken(WORKER_DETAIL_TIMEOUT_SECONDS)
        _cancel_worker_token(getattr(self, prefix + 'detail_token'))
        setattr(self, prefix + 'detail_token', token)
        setattr(self, prefix + 'detail_pending', True)
        pool = getattr(self, '_grid_detail_workers', None)
        if pool is None:
            pool = self._grid_detail_workers = LatestPosterWorkers(worker_count=2)
            self.onClose.append(pool.close)
        worker = getattr(self, '_load_' + kind + '_detail_worker')
        pool.replace([lambda: worker(generation, item, token)])
        getattr(self, '_start_deferred_' + kind + '_posters')()
        getattr(self, prefix + 'timer').start(80, True)

    def _render_grid_detail(self, kind, info, loading=False):
        if loading:
            info = _pending_vod_detail(info)
        prefix = '_' + kind + '_'
        entries = getattr(self, prefix + 'entries')
        if not entries:
            getattr(self, '_set_static_' + kind + '_title')(_('Selecting movie...') if kind == 'movie' else _('Selecting series...'))
            for field in ('rating', 'meta', 'people'):
                set_text_if_changed(self[kind + '_' + field], '')
            message = _('Loading...') if loading else ''
            getattr(self, '_set_' + kind + '_plot')(message)
            return
        item = entries[getattr(self, prefix + 'selected_index')]
        if info is not None:
            _attach_subtitle_detail_metadata(item, info)
        if kind == 'series':
            self._series_info = info
            self._series_info_stream_id = str(item.stream_id)
        title = getattr(info, 'title', '') or item.name
        getattr(self, '_set_' + kind + '_title')(title)
        tmdb_rating = getattr(info, 'tmdb_rating', '')
        rating = tmdb_rating or getattr(info, 'rating', '') or getattr(item, 'rating', '')
        label = _rating_label(rating)
        if tmdb_rating:
            label = 'TMDb {} / 10'.format(label)
        fit_dynamic_text(self[kind + '_rating'], label, max_lines=1, preferred_size=getattr(self, '_grid_rating_font', None), min_size=getattr(self, '_grid_rating_min_font', None), fallback_chars=24)
        meta = [str(value) for value in (getattr(info, 'year', '') or getattr(item, 'year', ''), getattr(info, 'genre', ''), getattr(info, 'duration', '')) if value]
        if kind == 'series':
            seasons = list(getattr(info, 'seasons', []) or [])
            if seasons:
                meta.append('{}: {}'.format(_('Seasons'), len(seasons)))
        ellipsize_dynamic_text(self[kind + '_meta'], '  •  '.join(meta), fallback_chars=110)
        plot = getattr(info, 'plot', '')
        if not plot:
            plot = _('Loading...') if loading else _('Movie summary unavailable.') if kind == 'movie' else _('Series summary unavailable.')
        getattr(self, '_set_' + kind + '_plot')(plot)
        people = []
        if getattr(info, 'director', ''):
            people.append(_('Director: {}').format(info.director))
        if getattr(info, 'cast', ''):
            people.append(_('Cast: {}').format(info.cast))
        fit_dynamic_text(self[kind + '_people'], '\n'.join(people), max_lines=2, preferred_size=getattr(self, '_grid_people_font', None), min_size=getattr(self, '_grid_people_min_font', None), fallback_chars=140)

    def _apply_grid_cover(self, kind, path, url, catalogue_url):
        entries = getattr(self, '_' + kind + '_entries')
        if not entries:
            return
        selected = getattr(self, '_' + kind + '_selected_index')
        item = entries[selected]
        url, catalogue_url = (_safe_picon_url(url), _safe_picon_url(catalogue_url))
        current = _safe_picon_url(getattr(item, 'icon', ''))
        if url and current in ('', catalogue_url) and (current != url):
            item.icon = url
            getattr(self, '_render_movie_posters' if kind == 'movie' else '_render_series_tiles')()

    def _release_grid_pixmaps(self, kind):
        setattr(self, '_' + kind + '_paint_signature', None)
        setattr(self, '_' + kind + '_poster_page_key', None)
        loaders = getattr(self, '_gt_pixmap_loaders', {})
        for slot in range(self._poster_slot_count):
            getattr(self, '_vod_tile_requests', {}).pop((kind, slot), None)
            getattr(self, '_vod_tile_displayed', {}).pop((kind, slot), None)
            loader = loaders.pop('{}-tile-{}'.format(kind, slot), None)
            if loader is not None:
                loader.close()
            widget = self['{}_tile_poster_{}'.format(kind, slot)]
            widget.hide()
            try:
                widget.instance.setPixmap(None)
            except Exception:
                pass

    def _update_grid_position(self, kind):
        entries = getattr(self, '_' + kind + '_entries')
        selected = getattr(self, '_' + kind + '_selected_index')
        more = getattr(self, '_' + kind + '_has_more')
        base = self._vod_window_base(kind)
        selected += base
        count = len(entries) + base
        self[kind + '_grid_position'].setText(page_position(selected, count, more))
        pages = max(1, (count + GRID_PAGE_SIZE - 1) // GRID_PAGE_SIZE)
        update_scrollbar(self, 'poster_scroll', pages + int(more), selected // GRID_PAGE_SIZE, 1, self._poster_scroll_geometry)
        _set_component_visible(self['poster_scroll_more'], selected // GRID_PAGE_SIZE < pages - 1 or more)

    def _move_vod_grid(self, kind, direction):
        entries = getattr(self, '_' + kind + '_entries')
        selected = getattr(self, '_' + kind + '_selected_index')
        more = getattr(self, '_' + kind + '_has_more')
        if not entries or getattr(self, '_' + kind + '_loading'):
            return
        target = grid_move(selected, len(entries), more, direction)
        if direction == 'up' and selected < GRID_COLUMNS and self._vod_window_base(kind):
            target = selected - GRID_COLUMNS
        if target is None:
            setattr(self, '_' + kind + '_focus', 'categories')
            getattr(self, '_refresh_' + kind + '_categories')()
            getattr(self, '_render_movie_posters' if kind == 'movie' else '_render_series_tiles')()
        elif target != selected:
            if kind == 'series':
                self._series_pending_clamp_to_end = direction == 'down'
            getattr(self, '_select_' + kind)(target)


    def _set_movie_title(self, value):
        return fit_dynamic_text(self['movie_title'], value, max_lines=1 if getattr(self, '_poster_grid_enabled', False) else 2, preferred_size=self._movie_title_preferred_font, min_size=self._movie_title_min_font, fallback_chars=42)

    def _set_static_movie_title(self, value):
        return set_static_text(self['movie_title'], value, preferred_size=self._movie_title_preferred_font)




    def _open_movie_trailer(self, item, info):
        if self._closed or self._movie_search_child_open or self._movie_search_keyboard_open:
            return None
        from .movie_trailer_ui import MovieTrailerScreen
        self._pause_movie_work_for_search()
        self._movie_search_resume_category_id = None
        self._movie_search_resume_detail = True
        self._movie_search_resume_posters = True
        self._movie_search_child_open = True
        self._movie_search_child = None
        try:
            opener = getattr(self.session, 'openWithCallback', None)
            if callable(opener):
                dialog = opener(self._movie_trailer_closed, MovieTrailerScreen, item, info, self._metadata_client)
            else:
                dialog = self.session.open(MovieTrailerScreen, item, info, self._metadata_client)
                dialog.onClose.append(self._movie_trailer_closed)
            self._movie_search_child = dialog
            return dialog
        except Exception as error:
            log_event('movies', 'Could not open the movie trailer', error=error)
            self._movie_trailer_closed()
            self['message'].setText(_('Could not start the player.'))
            return None

    def _movie_trailer_closed(self, *args):
        if self._closed or not self._movie_search_child_open:
            return
        try:
            catalog = getattr(self, '_movie_browser_catalog', None)
            if catalog is not None:
                catalog.start()
        except Exception as error:
            log_event('movies', 'Could not resume the movie catalog', error=error)
        finally:
            self._movie_search_closed(*args)

    def open_movie_search(self):
        if self.content_type != 'movie' or self._closed:
            return
        if self._loading or self._category_preferences_generation != self._load_generation:
            self['message'].setText(_('Please wait'))
            return
        opener = getattr(self.session, 'openWithCallback', None)
        if not callable(opener):
            self['message'].setText(_('Could not open the content list.'))
            return
        if self._movie_search_child_open or self._movie_search_keyboard_open:
            self['message'].setText(_('Please wait'))
            return
        if self._movie_search_drain_event is not None:
            self['message'].setText(_('Please wait'))
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self['message'].setText(_('The virtual keyboard is unavailable on this image.'))
            return
        self._movie_search_keyboard_open = True
        try:
            opener(self._movie_search_entered, VirtualKeyBoard, title=_('Search All Movies'), text=self._movie_search_query)
        except Exception:
            self._movie_search_keyboard_open = False
            self['message'].setText(_('Could not open the virtual keyboard.'))

    def _movie_search_entered(self, value):
        self._movie_search_keyboard_open = False
        if self._closed or value is None:
            return
        query = ' '.join(str(value or '').split())[:120]
        if not query:
            return
        self._movie_search_query = query
        opener = getattr(self.session, 'openWithCallback', None)
        if not callable(opener):
            self['message'].setText(_('Could not open the content list.'))
            return
        shared_catalog = None
        try:
            catalog = getattr(self, '_movie_browser_catalog', None)
            shared_catalog = catalog.borrow_search() if catalog is not None else None
            search_client = shared_catalog.client if shared_catalog is not None else content_client_for(self.account)
        except Exception as error:
            if shared_catalog is not None:
                shared_catalog.close()
            log_event('movies', 'Could not create movie search client', error=error)
            self['message'].setText(_('Could not open the content list.'))
            return
        self._movie_search_child_open = True
        self._movie_search_child = None
        try:
            self._pause_movie_work_for_search()
            self._movie_search_child = opener(self._movie_search_closed, GTAllMoviesSearchScreen, self.account, search_client, self.favorite_store, self._hidden_category_ids, initial_query=query, shared_catalog=shared_catalog)
        except Exception:
            if shared_catalog is not None:
                shared_catalog.close()
            self._movie_search_closed()
            source_type = str(getattr(search_client, 'source_type', '') or '').lower()
            if shared_catalog is None and (source_type == 'xtream' or isinstance(search_client, XtreamContentClient)):
                releaser = getattr(search_client, 'release_movie_search', None)
                if callable(releaser):
                    try:
                        releaser()
                    except Exception:
                        pass
            if not self._closed:
                self['message'].setText(_('Could not open the content list.'))

    def _pause_movie_work_for_search(self):
        content_sort_ui.cancel_loading(self, 'movie')
        reset_cinematic = getattr(self, '_reset_cinematic_backdrop', None)
        if callable(reset_cinematic):
            reset_cinematic()
        browse_catalog = getattr(self, '_movie_browser_catalog', None)
        if browse_catalog is not None:
            browse_catalog.stop()
        category_active = bool(self._loading or self._load_token is not None)
        movie_active = bool(self._movie_loading or self._movie_load_token is not None)
        more_active = bool(self._movie_more_loading or self._movie_more_token is not None)
        detail_active = bool(self._movie_detail_pending or self._movie_pending_detail_generation is not None or self._movie_detail_token is not None)
        poster_active = bool(self._movie_poster_pending or self._movie_poster_state is not None or self._movie_poster_token is not None or (self._movie_deferred_poster_jobs is not None) or self._vod_poster_retries.pending)
        self._reset_vod_poster_retries()
        pending_category_id = self._movie_pending_category_id
        loaded_category_id = self._movie_loaded_category_id
        self._movie_search_resume_categories = category_active
        self._movie_search_resume_category_id = pending_category_id if pending_category_id is not None else loaded_category_id if movie_active else None
        if browse_catalog is not None and loaded_category_id is not None:
            self._movie_search_resume_category_id = loaded_category_id
        self._movie_search_resume_more = more_active
        self._movie_search_resume_detail = detail_active
        self._movie_search_resume_posters = poster_active
        if category_active:
            self._load_generation += 1
            _cancel_worker_token(self._load_token)
            self._load_token = None
            self._loading = False
        if movie_active or more_active or detail_active or poster_active:
            self._movie_generation += 1
        if poster_active:
            self._movie_poster_generation = getattr(self, '_movie_poster_generation', self._movie_generation) + 1
        if movie_active or more_active:
            self._movie_catalog_generation += 1
        for token in (self._movie_load_token, self._movie_more_token, self._movie_detail_token, self._movie_poster_token):
            _cancel_worker_token(token)
        poster_state = self._movie_poster_state
        if isinstance(poster_state, dict):
            _cancel_worker_token(poster_state.get('token'))
        self._movie_load_token = None
        self._movie_more_token = None
        self._movie_detail_token = None
        self._movie_poster_token = None
        self._movie_loading = False
        self._movie_more_loading = False
        self._movie_more_result = None
        self._movie_detail_pending = False
        self._movie_detail_preview = None
        self._movie_detail_result = None
        self._movie_poster_pending = False
        self._movie_poster_state = None
        self._movie_deferred_poster_jobs = None
        self._movie_pending_category_id = None
        self._movie_pending_detail_generation = None
        for timer in (self._timer, self._movie_timer, self._movie_category_timer, self._movie_detail_timer):
            try:
                timer.stop()
            except Exception:
                pass

    def _resume_category_list_after_search(self):
        if self._closed or self._loading:
            return
        self._load_generation += 1
        generation = self._load_generation
        token = _WorkerToken(WORKER_LIST_TIMEOUT_SECONDS)
        self._load_token = token
        self._loading = True
        self._ready = False
        self._error = ''
        worker = threading.Thread(target=self._run_load, args=(generation, token))
        worker.daemon = True
        worker.start()
        self._timer.start(150, True)

    def _resume_movie_category_after_search(self, category_id):
        if self._closed or category_id is None or self._movie_loading:
            return
        category_id = str(category_id)
        self._movie_generation += 1
        self._movie_catalog_generation += 1
        generation = self._movie_generation
        browse_catalog = getattr(self, '_movie_browser_catalog', None)
        if browse_catalog is not None:
            browse_catalog.start()
            self._movie_browse_cursor = 0
        token = _WorkerToken(MOVIE_CATEGORY_TIMEOUT_SECONDS)
        self._movie_load_token = token
        self._movie_loading = True
        self._movie_ready = False
        self._movie_error = ''
        self._movie_loaded_category_id = category_id
        worker = threading.Thread(target=self._load_movie_worker, args=(generation, category_id, token))
        worker.daemon = True
        worker.start()
        self._movie_timer.start(120, True)

    def _resume_movie_work_after_search(self):
        resume_categories = self._movie_search_resume_categories
        category_id = self._movie_search_resume_category_id
        resume_more = self._movie_search_resume_more
        resume_detail = self._movie_search_resume_detail
        resume_posters = self._movie_search_resume_posters
        self._movie_search_resume_categories = False
        self._movie_search_resume_category_id = None
        self._movie_search_resume_more = False
        self._movie_search_resume_detail = False
        self._movie_search_resume_posters = False
        if resume_categories:
            self._resume_category_list_after_search()
        if category_id is not None:
            self._resume_movie_category_after_search(category_id)
        elif resume_more and self._movie_has_more:
            self._start_movie_more_load()
        if category_id is None and resume_detail and self._movie_entries:
            self._schedule_movie_detail()
        if category_id is None and resume_posters and self._movie_entries:
            self._movie_poster_page_key = None
            self._render_movie_posters()

    def _poll_movie_search_resume(self):
        if self._closed:
            self._movie_search_drain_event = None
            return
        if self._movie_search_child_open:
            self._movie_search_resume_timer.start(100, True)
            return
        drain_event = self._movie_search_drain_event
        if drain_event is not None and (not drain_event.is_set()):
            self._movie_search_resume_timer.start(100, True)
            return
        self._movie_search_drain_event = None
        self._resume_movie_work_after_search()

    def _movie_search_closed(self, *args):
        if self._closed or not self._movie_search_child_open:
            return
        child = self._movie_search_child
        self._movie_search_child = None
        self._movie_search_child_open = False
        self._reload_favorite_keys()
        self._refresh_movie_categories(schedule_load=False)
        self._render_movie_posters()
        drain_event = getattr(child, '_provider_workers_idle', None)
        if drain_event is not None and (not drain_event.is_set()):
            self._movie_search_drain_event = drain_event
            self._movie_search_resume_timer.start(100, True)
            return
        self._movie_search_drain_event = None
        self._resume_movie_work_after_search()

    def _schedule_movie_load(self, category=None, immediate=False):
        if not self._entries or self._closed:
            return
        category = category or self._entries[self.selected_index]
        self._movie_pending_category_id = category.category_id
        try:
            self._movie_category_timer.stop()
        except Exception:
            pass
        if immediate:
            self._commit_movie_category_load()
        else:
            self._movie_category_timer.start(NAVIGATION_DEBOUNCE_MS, True)

    def _commit_movie_category_load(self):
        if not self._entries or self._closed:
            return
        category = self._entries[self.selected_index]
        if category.category_id != self._movie_pending_category_id:
            return
        self._movie_pending_category_id = None
        self._start_movie_load(category)

    def _start_movie_load(self, category=None):
        if category is None and self._entries:
            category = self._entries[self.selected_index]
        if category is not None:
            content_sort_ui.category_started(self, 'movie', category.category_id)
        preparation = getattr(self, '_vod_preparation', None)
        if preparation is not None:
            preparation.invalidate()
        if not self._entries or self._closed:
            return
        category = category or self._entries[self.selected_index]
        for timer in (self._movie_category_timer, self._movie_detail_timer):
            try:
                timer.stop()
            except Exception:
                pass
        self._movie_pending_category_id = None
        self._movie_pending_detail_generation = None
        for token in (self._movie_load_token, self._movie_more_token, self._movie_detail_token, self._movie_poster_token):
            _cancel_worker_token(token)
        self._movie_category_load_started = True
        self._movie_generation += 1
        self._movie_catalog_generation += 1
        self._movie_poster_generation = getattr(self, '_movie_poster_generation', self._movie_generation) + 1
        generation = self._movie_generation
        self._movie_loading = True
        self._movie_ready = False
        self._movie_error = ''
        self._movie_catalog_entries = []
        old_window = getattr(self, '_movie_native_window', None)
        if old_window is not None:
            old_window.close()
        if _is_stalker_client(self.client) and getattr(self, '_movie_browser_catalog', None) is None:
            self._movie_native_window = NativeBrowseWindow('movie')
            if not getattr(self, '_native_window_close_hook', False):
                self._native_window_close_hook = True
                self.onClose.append(self._close_native_vod_windows)
        self._movie_entries = []
        self._movie_browse_cursor = 0
        self._movie_selected_index = 0
        self._movie_loaded_category_id = category.category_id
        self._movie_detail_preview = None
        self._movie_detail_result = None
        self._movie_detail_pending = False
        self._movie_poster_state = None
        self._movie_poster_pending = False
        self._movie_poster_page_key = None
        self._movie_has_more = False
        self._movie_more_loading = False
        self._movie_more_result = None
        self._movie_pending_advance = False
        self._movie_pending_selection_index = None
        self._movie_more_token = None
        self._movie_detail_token = None
        self._movie_poster_token = None
        self._movie_deferred_poster_jobs = None
        ellipsize_dynamic_text(self['movie_rail_caption'], _('{} • LOADING MOVIES').format(localized_upper(category.name)), fallback_chars=90)
        self._clear_movie_posters()
        self._render_movie_waiting()
        browse_catalog = getattr(self, '_movie_browser_catalog', None)
        if browse_catalog is not None:
            browse_catalog.start(preferred_category=category.category_id)
        token = _WorkerToken(browse_catalog.load_timeout if browse_catalog is not None else MOVIE_CATEGORY_TIMEOUT_SECONDS)
        self._movie_load_token = token
        begin_delivery = getattr(self, '_light_begin_list', None)
        if callable(begin_delivery): begin_delivery('movie', generation)
        worker = threading.Thread(target=self._load_movie_worker, args=(generation, category.category_id, token))
        worker.daemon = True
        worker.start()
        self._movie_timer.start(120, True)

    def _load_movie_worker(self, generation, category_id, token=None):
        browse_catalog = getattr(self, '_movie_browser_catalog', None)
        native_window = getattr(self, '_movie_native_window', None)
        try:
            if browse_catalog is not None:
                page = content_sort_ui.initial_page(browse_catalog, category_id, self._hidden_category_ids, token)
                page = content_sort_ui.restore_page(self, browse_catalog, page, 'movie', category_id, generation, token)
                entries = page.items
                error = '' if entries or page.catalog_state.get('complete') else N_('Search incomplete.')
            else:
                entries = list(_bounded_client_call(self.client, token, lambda: self.client.load_items('movie', category_id)) or [])
                error = ''
            if browse_catalog is None and (not str(category_id or '').strip() or category_filter_active(getattr(self, '_hidden_category_ids', ()))):
                entries = filter_hidden_category_items(entries, self._hidden_category_ids, category_id)
        except _WorkerCancelled:
            return
        except ContentError as caught:
            entries = []
            error = str(caught)
        except (CatalogStorageError, CatalogLimitError, MemoryError) as caught:
            entries = []
            error = catalog_failure_message(caught)
        except Exception:
            entries = []
            error = N_('Could not load movies')
        if self._closed or generation != self._movie_generation or (token is not None and (not token.active())):
            return
        if native_window is not None and (not error):
            try:
                entries = native_window.append(entries, self._client_has_more_items('movie', category_id), token)[:240]
            except Exception:
                entries = []
                error = N_('Search incomplete.')
        self._movie_window_base = getattr(page, 'sort_base', 0) if browse_catalog is not None and (not error) else 0
        self._movie_selected_index = getattr(page, 'sort_selected', 0) if browse_catalog is not None and (not error) else 0
        self._movie_known_ids = set() if getattr(entries, 'full_category', False) else {str(item.stream_id) for item in entries}
        self._movie_entries = self._movie_catalog_entries = entries
        self._movie_error = error
        if browse_catalog is not None and (not error):
            self._movie_browse_cursor = page.next_cursor
            self._movie_has_more = page.has_more
        else:
            self._movie_has_more = self._client_has_more_items('movie', category_id) if browse_catalog is None else False
        if native_window is not None and (not error):
            self._movie_has_more = native_window.has_more or native_window.count > len(entries)
        self._movie_ready = True
        publish = getattr(self, '_light_publish_list', None)
        if callable(publish): publish('movie', generation)

    def _start_movie_more_load(self):
        loader = getattr(self.client, 'load_more_items', None)
        if not callable(loader) and getattr(self, '_movie_browser_catalog', None) is None or not self._movie_has_more or self._movie_more_loading or self._closed:
            return False
        self._movie_more_loading = True
        generation = self._movie_catalog_generation
        category_id = str(self._movie_loaded_category_id or '')
        token = _WorkerToken(MOVIE_CATEGORY_TIMEOUT_SECONDS)
        _cancel_worker_token(self._movie_more_token)
        self._movie_more_token = token
        worker = threading.Thread(target=self._load_movie_more_worker, args=(generation, category_id, loader, token))
        worker.daemon = True
        worker.start()
        self._movie_timer.start(80, True)
        return True

    def _load_movie_more_worker(self, generation, category_id, loader, token=None):
        browse_catalog = getattr(self, '_movie_browser_catalog', None)
        native_window = getattr(self, '_movie_native_window', None)
        page = None
        try:
            local_end = self._vod_window_base('movie') + len(self._movie_entries)
            if native_window is not None and local_end < native_window.count:
                page = native_window.read_window(category_id, offset=local_end, size=80, token=token)
                entries = list(page.items)
            elif browse_catalog is not None:
                page = browse_catalog.read(category_id, after=self._movie_browse_cursor, hidden_category_ids=self._hidden_category_ids, token=token)
                entries = list(page.items)
            else:
                entries = list(_bounded_client_call(self.client, token, lambda: loader('movie', category_id)) or [])
            if not str(category_id or '').strip() or category_filter_active(getattr(self, '_hidden_category_ids', ())):
                entries = filter_hidden_category_items(entries, self._hidden_category_ids, category_id)
            error = ''
        except _WorkerCancelled:
            return
        except ContentError as caught:
            entries = []
            error = str(caught)
        except (CatalogStorageError, CatalogLimitError, MemoryError) as caught:
            entries = []
            error = catalog_failure_message(caught)
        except Exception:
            entries = []
            error = N_('Could not load movies')
        has_more = page.has_more if page is not None else self._client_has_more_items('movie', category_id) if browse_catalog is None else False
        if native_window is not None and (not error) and (page is None):
            try:
                entries = native_window.append(entries, has_more, token)[:80]
                has_more = native_window.has_more or local_end + len(entries) < native_window.count
            except Exception:
                entries, has_more = ([], False)
                error = N_('Search incomplete.')
        if self._closed or generation != self._movie_catalog_generation or (token is not None and (not token.active())):
            return
        if page is not None:
            self._movie_browse_cursor = page.next_cursor
        self._movie_more_result = (generation, category_id, entries, has_more, error)

    def _close_native_vod_windows(self):
        for kind in ('movie', 'series'):
            window = getattr(self, '_' + kind + '_native_window', None)
            if window is not None:
                window.close()

    def _vod_window_base(self, kind):
        return int(getattr(self, '_' + kind + '_window_base', 0))

    def _trim_vod_window(self, kind):
        prefix = '_' + kind + '_'
        if getattr(getattr(self, prefix + 'entries', None), 'full_category', False):
            return
        if getattr(self, prefix + 'browser_catalog', None) is None and getattr(self, prefix + 'native_window', None) is None:
            return
        entries = getattr(self, prefix + 'entries')
        selected = getattr(self, prefix + 'selected_index')
        excess = len(entries) - 280
        remove = (excess + 69) // 70 * 70 if excess > 0 else 0
        remove = min(remove, max(0, (selected - 14) // 70 * 70))
        if remove:
            del entries[:remove]
            setattr(self, prefix + 'window_base', self._vod_window_base(kind) + remove)
            setattr(self, prefix + 'selected_index', selected - remove)
            pending = getattr(self, prefix + 'pending_selection_index', None)
            if pending is not None:
                setattr(self, prefix + 'pending_selection_index', pending - remove)
            setattr(self, prefix + 'poster_page_key', None)
        setattr(self, prefix + 'known_ids', {str(item.stream_id) for item in entries})
        if kind == 'movie':
            self._movie_catalog_entries = entries

    def _request_vod_window(self, kind, index):
        prefix = '_' + kind + '_'
        catalog = getattr(self, prefix + 'browser_catalog', None) or getattr(self, prefix + 'native_window', None)
        if catalog is None or getattr(self, prefix + 'more_loading'):
            return False
        absolute = max(0, self._vod_window_base(kind) + int(index))
        base = max(0, (absolute - 70) // 70 * 70)
        generation = getattr(self, prefix + 'catalog_generation')
        category = str(getattr(self, prefix + 'loaded_category_id') or '')
        token = _WorkerToken(WORKER_LIST_TIMEOUT_SECONDS)
        setattr(self, prefix + 'more_token', token)
        setattr(self, prefix + 'more_loading', True)
        hidden = self._hidden_category_ids

        def load():
            try:
                read_base, target = (base, absolute)
                if getattr(catalog, 'content_type', None) == kind:
                    while absolute >= catalog.count and catalog.has_more:
                        token.check()
                        rows = _bounded_client_call(self.client, token, lambda: self.client.load_more_items(kind, category))
                        rows = filter_hidden_category_items(rows, hidden, category)
                        catalog.append(rows, self._client_has_more_items(kind, category), token)
                    if absolute >= catalog.count and (not catalog.has_more):
                        target = max(0, catalog.count - 1)
                        read_base = max(0, (target - 70) // 70 * 70)
                page = catalog.read_window(category, offset=read_base, size=240, hidden_category_ids=hidden, token=token)
                result = (generation, category, page.items, page.has_more, '', (read_base, target))
                cursor = page.next_cursor
            except Exception:
                result = (generation, category, [], False, N_('Search incomplete.'), None)
                cursor = None
            if not self._closed and token.active() and (generation == getattr(self, prefix + 'catalog_generation')):
                if cursor is not None:
                    setattr(self, prefix + 'browse_cursor', cursor)
                setattr(self, prefix + 'more_result', result)
        worker = threading.Thread(target=load, name='GT-CatalogWindow', daemon=True)
        try:
            worker.start()
        except (RuntimeError, OSError):
            setattr(self, prefix + 'more_loading', False)
            return False
        getattr(self, prefix + 'timer').start(80, True)
        return True

    def _apply_vod_window(self, kind, entries, replacement):
        if replacement is None:
            return False
        prefix = '_' + kind + '_'
        base, absolute = replacement
        setattr(self, prefix + 'entries', list(entries))
        setattr(self, prefix + 'window_base', base)
        setattr(self, prefix + 'known_ids', {str(item.stream_id) for item in entries})
        if kind == 'movie':
            self._movie_catalog_entries = self._movie_entries
        if entries:
            getattr(self, '_select_' + kind)(max(0, min(absolute - base, len(entries) - 1)))
        return True

    def _maybe_load_more_movies(self):
        if not self._movie_entries or not self._movie_has_more:
            return False
        remaining = len(self._movie_entries) - self._movie_selected_index - 1
        if remaining > getattr(self, '_poster_page_size', CINEMATIC_POSTER_COLUMNS) and (not self._movie_pending_advance):
            return False
        return self._start_movie_more_load()


    def _apply_movie_more_result(self):
        result = self._movie_more_result
        if result is None:
            return
        self._movie_more_result = None
        generation, category_id, entries, has_more, error = result[:5]
        replacement = result[5] if len(result) > 5 else None
        if generation != self._movie_catalog_generation or category_id != str(self._movie_loaded_category_id or ''):
            return
        self._movie_more_loading = False
        self._movie_more_token = None
        self._movie_has_more = bool(has_more)
        if error:
            self._movie_pending_advance = False
            self._movie_pending_selection_index = None
            ellipsize_dynamic_text(self['message'], _(error), fallback_chars=90)
            if not self._movie_entries:
                self._movie_error = error
                self._render_movie_results()
            return
        if self._apply_vod_window('movie', entries, replacement):
            return
        old_length = len(self._movie_entries)
        known = getattr(self, '_movie_known_ids', None)
        if known is None:
            known = self._movie_known_ids = {str(item.stream_id) for item in self._movie_catalog_entries}
        for item in entries:
            stream_id = str(getattr(item, 'stream_id', ''))
            if not stream_id or stream_id in known:
                continue
            known.add(stream_id)
            self._movie_catalog_entries.append(item)
        self._movie_entries = self._movie_catalog_entries
        if not old_length:
            self._render_movie_results()
            return
        self._update_movie_rail_caption()
        if self._movie_pending_advance:
            pending_index = getattr(self, '_movie_pending_selection_index', None)
            if pending_index is None and len(self._movie_entries) > old_length:
                pending_index = old_length
            if pending_index is not None and pending_index < len(self._movie_entries):
                self._movie_pending_advance = False
                self._movie_pending_selection_index = None
                self._select_movie(pending_index)
                return
            if self._movie_has_more:
                self._start_movie_more_load()
                return
            self._movie_pending_advance = False
            self._movie_pending_selection_index = None
            if self._movie_entries:
                last_index = len(self._movie_entries) - 1
                if last_index != self._movie_selected_index:
                    self._select_movie(last_index)
                    return
        self._movie_pending_advance = False
        self._movie_pending_selection_index = None
        self._trim_vod_window('movie')
        self._render_movie_posters()


    def _poll_movie_result(self):
        if self._closed:
            return
        browse_catalog = getattr(self, '_movie_browser_catalog', None)
        if browse_catalog is not None:
            self._update_movie_catalog_message()
            if getattr(self, '_light_vod_enabled', False):
                self._light_refresh_count()
            if self._movie_index_reload_when_done and (not browse_catalog.state.get('preparing')):
                self._movie_index_reload_when_done = False
                self._start_movie_load()
                return
        if self._movie_loading:
            if not self._movie_ready:
                if self._movie_load_token is not None and self._movie_load_token.expired():
                    _cancel_worker_token(self._movie_load_token)
                    if browse_catalog is not None:
                        browse_catalog.stop()
                    self._movie_loading = False
                    self._movie_error = N_('Movie request timed out')
                    self._render_movie_results()
                    return
                self._movie_timer.start(120, True)
                return
            self._movie_loading = False
            self._movie_load_token = None
            content_sort_ui.refresh_initial(self, 'movie')
            self._render_movie_results()
            if browse_catalog is not None and (browse_catalog.state.get('preparing') or getattr(self._movie_entries, 'progressive', False)):
                self._movie_timer.start(120, True)
            return
        growing_pending = content_sort_ui.poll_loading(self, 'movie')
        if growing_pending:
            self._movie_timer.start(120, True)
        if self._movie_more_loading and self._movie_more_token is not None and self._movie_more_token.expired():
            _cancel_worker_token(self._movie_more_token)
            self._movie_more_loading = False
            self._movie_more_token = None
            self._movie_pending_advance = False
            self._movie_pending_selection_index = None
        self._apply_movie_more_result()
        preview = getattr(self, '_movie_detail_preview', None)
        if preview is not None:
            self._movie_detail_preview = None
            if self._movie_detail_result is None and preview[0] == self._movie_generation:
                self._apply_movie_detail(_pending_vod_detail(preview[1]), *preview[2:])
        detail = self._movie_detail_result
        if detail is not None:
            self._movie_detail_preview = None
            self._movie_detail_result = None
            if not getattr(self, '_poster_grid_enabled', False) or detail[0] == self._movie_generation:
                self._movie_detail_pending = False
                self._movie_detail_token = None
            generation, info, poster_path, metadata_status, poster_url, catalogue_poster_url = detail
            if generation == self._movie_generation:
                self._apply_movie_detail(info, poster_path, metadata_status, poster_url, catalogue_poster_url)
            self._start_deferred_movie_posters()
        elif self._movie_detail_pending and self._movie_detail_token is not None and self._movie_detail_token.expired():
            _cancel_worker_token(self._movie_detail_token)
            self._movie_detail_token = None
            self._movie_detail_pending = False
            if getattr(self, '_poster_grid_enabled', False):
                self._render_grid_detail('movie', self._cached_grid_detail('movie'))
            self._start_deferred_movie_posters()
        state = self._movie_poster_state
        if state is not None:
            state_token = state.get('token')
            state_expired = state_token is not None and state_token.expired()
            if state_expired:
                _cancel_worker_token(state_token)
            with state['lock']:
                selected_slot = self._movie_selected_index - int(state['page_key'][0])
                state['results'].sort(key=lambda result: int(result[0]) != selected_slot)
                limit = 2 if getattr(self, '_poster_grid_enabled', False) else len(state['results'])
                results = state['results'][:limit]
                del state['results'][:limit]
                complete = (state_expired or state['pending_workers'] <= 0) and (not state['results'])
                if complete and 'queued_jobs' in state:
                    state['queued_jobs'].clear()
                    state.get('recovery_jobs', deque()).clear()
            generation = state['generation']
            page_is_current = state.get('page_key') == self._movie_poster_page_key
            if page_is_current:
                for slot, stream_id, path, requested_url, resolved_url, fallback_deferred in results:
                    try:
                        page_start = int(state['page_key'][0])
                        page_item = self._movie_entries[page_start + slot]
                        result_is_current = generation == getattr(self, '_movie_poster_generation', self._movie_generation) and str(page_item.stream_id) == str(stream_id)
                    except Exception:
                        page_item = None
                        result_is_current = False
                    if not result_is_current:
                        continue
                    if path and resolved_url:
                        self._remember_vod_card_size(resolved_url)
                    current_url = self._vod_card_url(getattr(page_item, 'icon', ''))
                    requested_url = self._vod_card_url(requested_url)
                    resolved_url = self._vod_card_url(resolved_url)
                    if path and current_url not in ('', requested_url, resolved_url):
                        continue
                    if path and resolved_url and (current_url in ('', requested_url)):
                        page_item.icon = resolved_url
                    if path:
                        poster_urls = getattr(self, '_movie_poster_urls', None)
                        if poster_urls is not None:
                            poster_urls[slot] = resolved_url or current_url
                    if fallback_deferred and (not path):
                        retry_url = self._vod_card_url(getattr(page_item, 'icon', ''))
                        self._defer_vod_poster_jobs('movie', self._movie_poster_page_key, [(slot, page_item, retry_url)], True)
                    self._apply_movie_tile_poster(slot, path, stream_id=stream_id, generation=generation)
                    try:
                        selected_item = self._movie_entries[self._movie_selected_index]
                    except Exception:
                        selected_item = None
                    if selected_item is not None and str(selected_item.stream_id) == str(stream_id):
                        self._apply_detail_poster('movie', selected_item, path)
            if complete or not page_is_current:
                if self._movie_poster_state is state:
                    self._movie_poster_state = None
                    self._movie_poster_pending = False
                    self._movie_poster_token = None
                if page_is_current:
                    self._start_deferred_movie_posters()
                    if complete:
                        self._schedule_missing_vod_posters('movie')
        poll_cinematic = getattr(self, '_poll_cinematic_backdrop', None)
        cinematic_pending = bool(poll_cinematic() if callable(poll_cinematic) else False)
        if self._movie_detail_pending or self._movie_poster_pending or self._movie_more_loading or cinematic_pending or (browse_catalog is not None and browse_catalog.state.get('preparing')):
            self._movie_timer.start(self._vod_poster_poll_delay('movie'), True)

    def _apply_detail_poster(self, kind, item, path=''):
        if getattr(self, '_vod_discovery_layout', False):
            return False
        if getattr(self, '_poster_grid_enabled', False):
            return False
        if kind == 'movie':
            entries = self._movie_entries
            generation = self._movie_generation
            poster_name = 'movie_poster'
            placeholder_name = 'movie_poster_placeholder'
            attach_key = 'movie-detail'
        elif kind == 'series':
            entries = self._series_entries
            generation = self._series_generation
            poster_name = 'series_poster'
            placeholder_name = 'series_poster_placeholder'
            attach_key = 'series-detail'
        else:
            return False
        if item is None or not entries:
            return False
        if not path:
            url = _safe_picon_url(getattr(item, 'icon', ''))
            path = _cached_picon_path(url) if url else ''
        if not path:
            return False
        stream_id = str(getattr(item, 'stream_id', '') or '')
        signatures = getattr(self, '_detail_poster_signatures', None)
        if signatures is None:
            signatures = {}
            self._detail_poster_signatures = signatures
        try:
            size = self[poster_name].instance.size()
            target_size = (int(size.width()), int(size.height()))
        except (AttributeError, TypeError, RuntimeError):
            target_size = None
        signature = (generation, stream_id, path, getattr(path, 'file_identity', None), target_size)
        previous = signatures.get(kind)
        if previous is not None and previous[0] == signature:
            return True
        request = (signature, object())
        signatures[kind] = request

        def loaded(success):
            try:
                if kind == 'movie':
                    current_generation = self._movie_generation
                    current_entries = self._movie_entries
                    current_index = self._movie_selected_index
                else:
                    current_generation = self._series_generation
                    current_entries = self._series_entries
                    current_index = self._series_selected_index
                current = current_entries[current_index]
                valid = generation == current_generation and str(getattr(current, 'stream_id', '')) == stream_id and (signatures.get(kind) is request)
                if not valid:
                    return
                if success:
                    _set_component_visible(self[poster_name], True)
                    _set_component_visible(self[placeholder_name], False)
                else:
                    if signatures.get(kind) is request:
                        signatures.pop(kind, None)
                    _set_component_visible(self[poster_name], False)
                    _set_component_visible(self[placeholder_name], True)
            except Exception:
                pass
        attach_pixmap(self, poster_name, path, key=attach_key, on_loaded=loaded)
        return True

    @staticmethod
    def _cinematic_widget_names(kind):
        if kind == 'movie':
            return ('movie_backdrop', 'movie_backdrop_scrim')
        if kind == 'series':
            return ('series_backdrop', 'series_backdrop_scrim')
        return ('', '')

    @staticmethod
    def _cinematic_video_guard_name(kind):
        if kind == 'movie':
            return 'movie_video_guard'
        if kind == 'series':
            return 'series_video_guard'
        return ''

    @staticmethod
    def _cinematic_panel_names(kind):
        if kind == 'movie':
            return ('movie_hero_panel',)
        if kind == 'series':
            return ('series_hero_panel',)
        return ()

    @staticmethod
    def _cinematic_fallback_shadow_names(kind):
        if kind == 'movie':
            return ('movie_detail_shadow', 'movie_people_shadow', 'movie_rail_shadow')
        if kind == 'series':
            return ('series_detail_shadow', 'series_people_shadow', 'series_rail_shadow')
        return ()

    def _set_cinematic_panels(self, kind, artwork_visible):
        """Keep only bounded text shadows over hero or transparent fallback."""
        if getattr(self, '_vod_discovery_layout', False):
            _set_component_visible(self[kind + '_video_guard'], True)
            for suffix in ('backdrop_scrim', 'detail_shadow', 'people_shadow'):
                _set_component_visible(self[kind + '_' + suffix], bool(artwork_visible))
            for suffix in ('hero_panel', 'rail_shadow', 'people_panel'):
                _set_component_visible(self[kind + '_' + suffix], False)
            return
        try:
            set_visible = _set_component_visible
        except NameError:

            def set_visible(component, visible):
                if visible:
                    component.show()
                else:
                    component.hide()
        guard_name = self._cinematic_video_guard_name(kind)
        if guard_name:
            try:
                set_visible(self[guard_name], True)
            except Exception:
                pass
        for name in self._cinematic_panel_names(kind):
            try:
                set_visible(self[name], False)
            except Exception:
                pass
        unused_backdrop_name, hero_scrim_name = self._cinematic_widget_names(kind)
        composite_plan = getattr(self, '_cinematic_composite_plan', None)
        if hero_scrim_name:
            try:
                set_visible(self[hero_scrim_name], bool(composite_plan is not None and not getattr(self, '_cinematic_composite_visible', False)))
            except Exception:
                pass
        for name in self._cinematic_fallback_shadow_names(kind):
            try:
                set_visible(self[name], composite_plan is None)
            except Exception:
                pass

    def _cancel_hero_start(self):
        self._hero_start_request = None
        timer = getattr(self, '_hero_start_timer', None)
        if timer is not None:
            timer.stop()

    def _schedule_hero_start(self, kind, item, immediate=False):
        self._cancel_hero_start()
        if not getattr(self, '_cinematic_enabled', False):
            return
        preparation = getattr(self, '_vod_preparation', None)
        if preparation is not None and preparation.show_hero(kind, item):
            return
        self._hero_start_request = (kind, getattr(self, '_' + kind + '_loaded_category_id', ''), str(item.stream_id), getattr(self, '_' + kind + '_generation'))
        if self._hero_start_timer is None:
            self._hero_start_timer = eTimer()
            _connect_timer(self._hero_start_timer, self._commit_hero_start)
            self.onClose.append(self._cancel_hero_start)
        self._hero_start_timer.start(0 if immediate else HERO_DEBOUNCE_MS, True)

    def _commit_hero_start(self):
        request, self._hero_start_request = (self._hero_start_request, None)
        if not request or self._closed or getattr(self, '_vod_work_suspended', False):
            return
        kind, category, identity, generation = request
        if kind != self.content_type or category != getattr(self, '_' + kind + '_loaded_category_id', '') or generation != getattr(self, '_' + kind + '_generation'):
            return
        entries = getattr(self, '_' + kind + '_entries', ())
        index = getattr(self, '_' + kind + '_selected_index', 0)
        if not 0 <= index < len(entries) or str(entries[index].stream_id) != identity:
            return
        info = _detail_cache_get(getattr(self, '_' + kind + '_info_cache'), getattr(self, '_' + kind + '_info_cache_lock'), identity)
        self._start_cinematic_backdrop(kind, entries[index], info)

    def _begin_cinematic_delivery(self):
        delivery = getattr(self, '_cinematic_delivery', None)
        if delivery is None:
            delivery = self._cinematic_delivery = SourceLoader(eTimer(), _connect_timer, self._cinematic_delivery_ready)
            self.onClose.append(delivery.close)
        self._cinematic_delivery_epoch = delivery.expect()
        return self._cinematic_delivery_epoch

    def _publish_cinematic_result(self, result, epoch=None):
        if (getattr(self, '_closed', False) or getattr(self, '_vod_work_suspended', False)
                or result[3] != getattr(self, '_cinematic_generation', -1)):
            release_prepared(result[2])
            return
        delivery = getattr(self, '_cinematic_delivery', None)
        if epoch is not None and delivery is not None:
            if not delivery.publish(epoch, result):
                release_prepared(result[2])
        else:
            self._cinematic_result = result

    def _cinematic_delivery_ready(self, result, error):
        # Both native message delivery and preparation callbacks run on GUI.
        if result is None:
            return
        if (getattr(self, '_closed', False) or getattr(self, '_vod_work_suspended', False)
                or result[3] != getattr(self, '_cinematic_generation', -1)
                or not self._cinematic_selection_matches(result[0], result[1])):
            release_prepared(result[2])
            return
        delivery = getattr(self, '_cinematic_delivery', None)
        if delivery is not None:
            delivery.cancel()
        self._cinematic_result = result
        self._poll_cinematic_backdrop()

    def _cinematic_selection_matches(self, kind, stream_id):
        if kind != getattr(self, 'content_type', None):
            return False
        entries = getattr(self, '_' + kind + '_entries', ())
        index = getattr(self, '_' + kind + '_selected_index', 0)
        return (0 <= index < len(entries)
                and str(getattr(entries[index], 'stream_id', '')) == str(stream_id))

    def _clear_cinematic_display(self, kind):
        self._cinematic_applied_path = ''
        self._cinematic_applied_identity = None
        self._cinematic_composite_visible = False
        backdrop_name, unused_scrim_name = self._cinematic_widget_names(kind)
        if backdrop_name:
            try:
                _set_component_visible(self[backdrop_name], False)
            except Exception:
                pass
        self._set_cinematic_panels(kind, False)

    def _clear_previous_cinematic_display(self, kind, stream_id):
        if getattr(self, '_cinematic_applied_identity', None) != (kind, stream_id):
            self._clear_cinematic_display(kind)

    def _reset_cinematic_backdrop(self, keep_visible=False):
        """Cancel stale work; keep a surface only for the selected item."""
        self._cancel_hero_start()
        if not getattr(self, '_cinematic_enabled', False):
            return
        self._cinematic_generation = getattr(self, '_cinematic_generation', 0) + 1
        _cancel_worker_token(getattr(self, '_cinematic_token', None))
        self._cinematic_token = None
        delivery = getattr(self, '_cinematic_delivery', None)
        if delivery is not None:
            delivery.cancel()
        self._cinematic_delivery_epoch = None
        self._cinematic_pending = False
        self._cinematic_lookup_pending = False
        self._cinematic_result = None
        self._cinematic_surface_prepared = None
        self._cinematic_request_signature = None
        self._cinematic_decode_request = None
        applied_identity = getattr(self, '_cinematic_applied_identity', None)
        keep_visible = (keep_visible and bool(getattr(self, '_cinematic_applied_path', ''))
                        and applied_identity is not None
                        and self._cinematic_selection_matches(*applied_identity))
        if not keep_visible:
            self._cinematic_applied_identity = None
            self._cinematic_applied_path = ''
            self._cinematic_composite_visible = False
        loaders = getattr(self, '_gt_pixmap_loaders', {})
        loader = loaders.pop('cinematic-backdrop', None)
        if loader is not None:
            try:
                loader.close()
            except Exception:
                pass
        if keep_visible:
            return
        backdrop_name, unused_scrim_name = self._cinematic_widget_names(getattr(self, 'content_type', ''))
        if backdrop_name:
            try:
                try:
                    set_visible = _set_component_visible
                except NameError:
                    set_visible = lambda component, visible: component.show() if visible else component.hide()
                set_visible(self[backdrop_name], False)
            except Exception:
                pass
        set_panels = getattr(self, '_set_cinematic_panels', None)
        if callable(set_panels):
            set_panels(getattr(self, 'content_type', ''), False)

    def _start_cinematic_backdrop(self, kind, item, info=None, catalogue_poster_url=''):
        """Load a settled selection's best verified cinematic hero image.

        Every candidate must be an explicit wide-art field and must decode as
        genuine landscape artwork. Posters remain available only in the card
        rail and are never enlarged into a cinematic hero.
        """
        if (not getattr(self, '_cinematic_enabled', False)
                or getattr(self, '_closed', False) or getattr(self, '_vod_work_suspended', False)
                or not self._cinematic_selection_matches(kind, getattr(item, 'stream_id', ''))):
            return False
        self._cancel_hero_start()
        stream_id = str(getattr(item, 'stream_id', '') or '')
        detail_generation = getattr(self, '_movie_generation' if kind == 'movie' else '_series_generation', None)
        prepared = (kind, stream_id, detail_generation)
        candidates = []
        seen = set()
        provider_item = bool(str(getattr(item, 'portal_request_type', '') or '').strip())
        verified_backdrop = getattr(info, 'fallback_backdrop', '')
        if provider_item and verified_backdrop:
            ordered_values = (verified_backdrop, getattr(info, 'backdrop', ''), getattr(item, 'backdrop', ''))
        else:
            ordered_values = (getattr(info, 'backdrop', ''), verified_backdrop, getattr(item, 'backdrop', ''))
        for value in ordered_values:
            url = _safe_picon_url(value)
            if not url or url in seen:
                continue
            seen.add(url)
            candidates.append(url)
        signature = (prepared, tuple(candidates))
        previous = getattr(self, '_cinematic_request_signature', None)
        preparation = getattr(self, '_vod_preparation', None)
        prepared_path = preparation.hero_ready(kind, item, info) if preparation is not None else ''
        token = getattr(self, '_cinematic_token', None)
        same_selection = bool(previous and previous[0] == prepared)
        applied_matches = getattr(self, '_cinematic_applied_identity', None) == (kind, stream_id)
        if same_selection and applied_matches and prepared_path and (prepared_path == getattr(self, '_cinematic_applied_path', '')):
            self._cinematic_request_signature = signature
            return True
        if same_selection and getattr(self, '_cinematic_lookup_pending', False) and getattr(self, '_cinematic_pending', False) and (not verified_backdrop) and (token is not None) and token.active():
            self._cinematic_request_signature = signature
            return True
        same_primary = bool(previous and previous[0] == prepared and previous[1] and candidates and (previous[1][0] == candidates[0]))
        if signature == previous or same_primary:
            token = getattr(self, '_cinematic_token', None)
            applied_path = getattr(self, '_cinematic_applied_path', '')
            decode = getattr(self, '_cinematic_decode_request', None)
            decoding = bool(decode and decode[:2] == (kind, stream_id) and (decode[3] == getattr(self, '_cinematic_generation', -1)))
            displayed = (applied_matches and applied_path
                         and (signature == previous or (candidates and _cached_picon_path(candidates[0]) == applied_path)))
            if displayed or (getattr(self, '_cinematic_pending', False) and getattr(self, '_cinematic_result', None) is None and (token is not None) and token.active()) or decoding:
                self._cinematic_request_signature = signature
                return True
        if getattr(self, '_cinematic_surface_prepared', None) == prepared:
            self._cinematic_surface_prepared = None
        else:
            self._reset_cinematic_backdrop(keep_visible=same_selection)
        self._cinematic_request_signature = signature
        generation = self._cinematic_generation
        if prepared_path:
            return self._apply_cinematic_backdrop(kind, stream_id, prepared_path, generation)
        resolver = getattr(getattr(self, '_metadata_client', None), 'cinematic_backdrop', None)
        resolve = callable(resolver) and getattr(self._metadata_client, 'available', True) and (not candidates or (provider_item and (not verified_backdrop)))
        if not candidates and (not resolve):
            self._clear_previous_cinematic_display(kind, stream_id)
            return False
        cached = _cached_picon_path(candidates[0]) if candidates else ''
        cached_ready = False
        if cached:
            if _valid_cinematic_backdrop_file(cached):
                cached_ready = True
                if getattr(self, '_cinematic_composite_plan', None) is None:
                    self._apply_cinematic_backdrop(kind, stream_id, cached, generation)
                    return True
        delivery_epoch = self._begin_cinematic_delivery()
        if preparation is not None and preparation.adopt_hero(kind, item, generation):
            self._cinematic_token = preparation.token
            self._cinematic_pending = True
            self._cinematic_lookup_pending = True
            timer = getattr(self, '_' + kind + '_timer', None)
            if timer is not None:
                timer.start(40, True)
            return True
        token = _WorkerToken(CINEMATIC_BACKDROP_TIMEOUT_SECONDS)
        self._cinematic_token = token
        self._cinematic_pending = True
        self._cinematic_lookup_pending = resolve and not cached_ready
        pool = getattr(self, '_cinematic_workers', None)
        if pool is None:
            pool = self._cinematic_workers = LatestPosterWorkers(worker_count=1)
            self.onClose.append(pool.close)
        pool.replace([lambda: self._load_cinematic_backdrop_worker(kind, stream_id, tuple(candidates), generation, token, item, info, delivery_epoch)])
        timer = getattr(self, '_movie_timer' if kind == 'movie' else '_series_timer', None)
        if timer is not None:
            try:
                timer.start(80, True)
            except Exception:
                pass
        return True

    def _load_cinematic_backdrop_worker(self, kind, stream_id, candidates, generation, token, item=None, info=None, delivery_epoch=None):
        path, resolved, selected_url = ('', '', '')
        resolver = getattr(getattr(self, '_metadata_client', None), 'cinematic_backdrop', None)
        attempted = set()
        lookup_done = not (item is not None and callable(resolver) and getattr(self, '_cinematic_lookup_pending', False))
        first_attempt = True
        while True:
            if not token.active() or generation != getattr(self, '_cinematic_generation', -1):
                return
            request = getattr(self, '_cinematic_request_signature', None)
            current_candidates = request[1] if request is not None else candidates
            urls = list(current_candidates)
            if resolved:
                if getattr(item, 'portal_request_type', '') or not urls:
                    urls = [resolved] + [url for url in urls if url != resolved]
                elif resolved not in urls:
                    urls.append(resolved)
            url = next((value for value in urls if value not in attempted), '')
            # Try one explicit URL first. A failing provider link must not
            # consume all fallback time before the independent TMDb lookup.
            if not lookup_done and (not first_attempt or not url):
                lookup_done = True
                try:
                    token.check()
                    resolved = _safe_picon_url(_bounded_client_call(self._metadata_client, token, lambda: resolver(kind, item, info)))
                except _WorkerCancelled:
                    return
                except Exception as error:
                    if not token.active():
                        return
                    _debug('cinematic wide-art lookup failed kind={} error={}'.format(kind, error.__class__.__name__))
                continue
            if not url:
                break
            attempted.add(url)
            try:
                token.check()
                timeout = min(1.5, CINEMATIC_CANDIDATE_TIMEOUT_SECONDS) if first_attempt and not lookup_done else CINEMATIC_CANDIDATE_TIMEOUT_SECONDS
                first_attempt = False
                candidate = _download_client_image(self.client, url, _picon_cache_path(url), timeout=timeout, token=token, priority=True)
                token.check()
                if candidate and _valid_cinematic_backdrop_file(candidate):
                    path, selected_url = (candidate, url)
                    break
            except _WorkerCancelled:
                return
            except Exception as error:
                _debug('cinematic hero candidate failed kind={} id={} error={}'.format(kind, _debug_identifier(stream_id), error.__class__.__name__))
        if token.active() and generation == getattr(self, '_cinematic_generation', -1):
            path = prepare_hero(path, getattr(self, '_cinematic_composite_plan', None), token, preparation_allowance())
            token.check()
            if generation != getattr(self, '_cinematic_generation', -1):
                release_prepared(path)
                return
            self._publish_cinematic_result((kind, stream_id, path, generation, tuple(current_candidates), selected_url), delivery_epoch)

    def _apply_cinematic_backdrop(self, kind, stream_id, path, generation):
        if (not path or generation != self._cinematic_generation
                or getattr(self, '_closed', False) or getattr(self, '_vod_work_suspended', False)
                or not self._cinematic_selection_matches(kind, stream_id)):
            release_prepared(path)
            return False
        backdrop_name, unused_scrim_name = self._cinematic_widget_names(kind)
        if not backdrop_name:
            release_prepared(path)
            return False
        plan = getattr(self, '_cinematic_composite_plan', None)
        composited = is_composite(path, plan)
        request = (kind, stream_id, path, generation, object())
        self._cinematic_decode_request = request
        try:
            set_visible = _set_component_visible
        except NameError:

            def set_visible(component, visible):
                if visible:
                    component.show()
                else:
                    component.hide()

        def loaded(success):
            try:
                if kind == 'movie':
                    current = self._movie_entries[self._movie_selected_index]
                else:
                    current = self._series_entries[self._series_selected_index]
                valid = getattr(self, '_cinematic_enabled', False) and generation == self._cinematic_generation and (str(getattr(current, 'stream_id', '')) == stream_id) and (getattr(self, '_cinematic_decode_request', None) is request)
                if not valid:
                    return
                self._cinematic_decode_request = None
                if success:
                    self._cinematic_applied_path = path
                    self._cinematic_applied_identity = (kind, stream_id)
                    self._cinematic_composite_visible = composited
                    set_visible(self[backdrop_name], True)
                    set_panels = getattr(self, '_set_cinematic_panels', None)
                    if callable(set_panels):
                        set_panels(kind, True)
                else:
                    if composited:
                        source = getattr(path, 'hero_source_path', '')
                        if source:
                            preparation = getattr(self, '_vod_preparation', None)
                            if preparation is not None:
                                for key in list(preparation.heroes):
                                    if key[0] == kind and key[2] == stream_id:
                                        preparation.heroes.pop(key, None)
                            self._apply_cinematic_backdrop(kind, stream_id, source, generation)
                            return
                    if (getattr(self, '_cinematic_applied_path', '')
                            and getattr(self, '_cinematic_applied_identity', None) == (kind, stream_id)):
                        return
                    self._clear_cinematic_display(kind)
            except Exception:
                pass
        try:
            options = {'decode_size': plan.size} if composited else {
                'cover_ratio': _cinematic_cover_ratio(path), 'cover_alignment': 'top_right'}
            attach_pixmap(self, backdrop_name, path, key='cinematic-backdrop', on_loaded=loaded, **options)
        finally:
            release_prepared(path)
        return True

    def _poll_cinematic_backdrop(self):
        if not getattr(self, '_cinematic_enabled', False):
            return False
        result = getattr(self, '_cinematic_result', None)
        if result is not None:
            self._cinematic_result = None
            if (result[3] != self._cinematic_generation
                    or not self._cinematic_selection_matches(result[0], result[1])):
                release_prepared(result[2])
                return bool(getattr(self, '_cinematic_pending', False))
            self._cinematic_pending = False
            self._cinematic_token = None
            self._cinematic_lookup_pending = False
            kind, stream_id, path, generation = result[:4]
            if generation == self._cinematic_generation and path:
                preparation = getattr(self, '_vod_preparation', None)
                if preparation is not None and len(result) > 4:
                    entries = getattr(self, '_' + kind + '_entries', ())
                    index = getattr(self, '_' + kind + '_selected_index', 0)
                    if index < len(entries) and str(entries[index].stream_id) == stream_id:
                        preparation.remember_hero(kind, entries[index], result[4], path, result[5])
                self._apply_cinematic_backdrop(kind, stream_id, path, generation)
            else:
                self._clear_previous_cinematic_display(kind, stream_id)
            return False
        token = getattr(self, '_cinematic_token', None)
        if token is not None and token.expired():
            _cancel_worker_token(token)
            self._cinematic_token = None
            self._cinematic_pending = False
            self._cinematic_lookup_pending = False
            delivery = getattr(self, '_cinematic_delivery', None)
            if delivery is not None:
                delivery.cancel()
            request = getattr(self, '_cinematic_request_signature', None)
            if request:
                self._clear_previous_cinematic_display(*request[0][:2])
            return False
        return bool(getattr(self, '_cinematic_pending', False))


    def _render_movie_results(self):
        if getattr(self, '_poster_grid_enabled', False):
            self['hint'].setText('')
        if self._movie_error:
            self['movie_rail_caption'].setText(_('COULD NOT LOAD MOVIES'))
            self._set_movie_plot(_(self._movie_error))
            self._clear_movie_posters()
            return
        if not self._movie_entries:
            if self._movie_has_more and (self._movie_more_loading or self._start_movie_more_load()):
                category = self._entries[self.selected_index]
                self['movie_rail_caption'].setText(_('{} • LOADING MOVIES').format(localized_upper(category.name)))
                self._set_movie_plot(_('Please wait'))
                self._clear_movie_posters()
                return
            self['movie_rail_caption'].setText(_('NO MOVIES FOUND IN THIS CATEGORY'))
            self._set_movie_plot(_('Try another category.'))
            self._clear_movie_posters()
            return
        self._movie_selected_index %= len(self._movie_entries)
        self._update_movie_rail_caption()
        self._schedule_movie_detail()
        self._render_movie_posters()
        self._maybe_load_more_movies()


    def _prioritize_vod_posters(self, kind, page_key):
        """Promote the current selection without restarting in-flight work."""
        state = getattr(self, '_' + kind + '_poster_state', None)
        if state is None or state.get('page_key') != page_key:
            return
        selected = getattr(self, '_' + kind + '_selected_index') - int(page_key[0])
        with state['lock']:
            if state.get('selected_slot') == selected:
                return
            state['selected_slot'] = selected
            queue = state.get('queued_jobs')
            if queue:
                jobs = sorted(queue, key=lambda job: int(job[0]) != selected)
                queue.clear()
                queue.extend(jobs)
            state['results'].sort(key=lambda result: int(result[0]) != selected)

    def _vod_poster_poll_delay(self, kind):
        """Drain ready grid cards quickly, retaining two decodes per turn."""
        state = getattr(self, '_' + kind + '_poster_state', None)
        if getattr(self, '_poster_grid_enabled', False) and state is not None:
            with state['lock']:
                if state['results']:
                    return 20
        return 80

    def _publish_cached_vod_cards(self, kind, page_key, cards):
        """Feed verified disk hits to the existing bounded GUI result drain.

        A missing native bitmap is not a missing file: it needs no download
        or metadata worker. Keep the existing two-cards-per-tick grid policy.
        """
        prefix = '_' + kind + '_'
        if self._closed or page_key != getattr(self, prefix + 'poster_page_key'):
            return
        generation = getattr(self, prefix + 'poster_generation', getattr(self, prefix + 'generation'))
        state = getattr(self, prefix + 'poster_state', None)
        if state is None:
            state = {'generation': generation, 'page_key': page_key, 'results': [],
                     'queued_jobs': deque(), 'pending_workers': 0,
                     'lock': threading.Lock(), 'token': None}
            setattr(self, prefix + 'poster_state', state)
        if state['generation'] != generation or state.get('page_key') != page_key:
            return
        selected = getattr(self, prefix + 'selected_index') - int(page_key[0])
        cards = sorted(cards, key=lambda card: int(card[0]) != selected)
        identities = {(int(card[0]), str(card[1])) for card in cards}
        with state['lock']:
            state['results'][:] = cards + [card for card in state['results']
                if (int(card[0]), str(card[1])) not in identities]
        setattr(self, prefix + 'poster_pending', True)
        getattr(self, prefix + 'timer').start(0, True)

    def _queue_vod_poster_jobs(self, kind, page_key, jobs):
        state = getattr(self, '_{}_poster_state'.format(kind), None)
        if state is not None and state.get('page_key') == page_key:
            token = state.get('token')
            with state['lock']:
                if state.get('pending_workers', 0) > 0 and 'queued_jobs' in state and (token is None or token.active()):
                    identities = {(int(slot), str(item.stream_id)) for slot, item, url in jobs}
                    queue = state['queued_jobs']
                    retained = [job for job in queue if (int(job[0]), str(job[1].stream_id)) not in identities]
                    queue.clear()
                    selected = getattr(self, '_{}_selected_index'.format(kind)) - int(page_key[0])
                    priority = sorted(jobs, key=lambda job: int(job[0]) != selected)
                    queue.extend(priority + retained)
                    recovery = state.get('recovery_jobs')
                    if recovery:
                        retained = [job for job in recovery if (int(job[0]), str(job[1].stream_id)) not in identities]
                        recovery.clear()
                        recovery.extend(retained)
                    state['selected_slot'] = selected
                    if token is not None:
                        token.deadline = max(token.deadline, time.monotonic() + poster_batch_timeout(len(queue) + BACKGROUND_POSTER_WORKERS, BACKGROUND_POSTER_WORKERS, PICON_DOWNLOAD_TIMEOUT, WORKER_DETAIL_TIMEOUT_SECONDS))
                    return
            self._defer_vod_poster_jobs(kind, page_key, jobs)
            getattr(self, '_{}_timer'.format(kind)).start(50, True)
            return
        getattr(self, '_start_{}_poster_jobs'.format(kind))(page_key, jobs)

    def _defer_vod_poster_jobs(self, kind, page_key, jobs, wait_for_detail=False):
        attribute = '_{}_deferred_poster_jobs'.format(kind)
        existing = getattr(self, attribute, None)
        merged = {}
        if existing is not None and existing[0] == page_key:
            merged.update(((str(job[1].stream_id), job) for job in existing[1]))
            wait_for_detail = wait_for_detail or (len(existing) > 2 and existing[2])
        merged.update(((str(job[1].stream_id), job) for job in jobs))
        setattr(self, attribute, (page_key, list(merged.values()), bool(wait_for_detail)))

    def _reset_vod_poster_retries(self, kind=None):
        timer = self._vod_poster_retry_timer
        if timer is not None:
            timer.stop()
        context = None
        if kind is not None:
            context = (kind, getattr(self, '_{}_poster_generation'.format(kind)), getattr(self, '_{}_poster_page_key'.format(kind)))
        self._vod_poster_retries.reset(context)
        with self._vod_decode_failure_lock:
            self._vod_decode_failures.clear()

    def _arm_vod_poster_retries(self):
        delay = self._vod_poster_retries.next_delay(time.monotonic())
        timer = self._vod_poster_retry_timer
        if delay is None or self._closed:
            if timer is not None:
                timer.stop()
            return
        if timer is None:
            timer = self._vod_poster_retry_timer = eTimer()
            _connect_timer(timer, self._retry_visible_vod_posters)
            self.onClose.append(self._reset_vod_poster_retries)
        timer.start(max(100, int(math.ceil(delay * 1000.0))), True)

    def _schedule_vod_poster_retry(self, kind, slot, item, delay=None):
        if self._closed or kind != self.content_type:
            return False
        generation = getattr(self, '_{}_poster_generation'.format(kind))
        page_key = getattr(self, '_{}_poster_page_key'.format(kind))
        context = (kind, generation, page_key)
        retries = self._vod_poster_retries
        if page_key is None or retries.context != context:
            return False
        entries = getattr(self, '_{}_entries'.format(kind))
        index = int(page_key[0]) + slot
        if index >= len(entries) or str(entries[index].stream_id) != str(item.stream_id):
            return False
        displayed = getattr(self, '_vod_tile_displayed', {}).get((kind, slot))
        if displayed is not None and displayed[:2] == (generation, str(item.stream_id)) and getattr(self['{}_tile_poster_{}'.format(kind, slot)], 'visible', False):
            return False
        if delay is None:
            url = self._vod_card_url(getattr(item, 'icon', ''))
            delay = min(60.0, _picon_failure_remaining(url)) if url else 2.0
        scheduled = retries.schedule(item.stream_id, (slot, str(item.stream_id)), time.monotonic(), delay)
        self._arm_vod_poster_retries()
        return scheduled

    def _retry_visible_vod_posters(self):
        if self._closed:
            self._reset_vod_poster_retries()
            return
        kind = self.content_type
        context = (kind, getattr(self, '_{}_poster_generation'.format(kind)), getattr(self, '_{}_poster_page_key'.format(kind)))
        if self._vod_poster_retries.context != context or context[2] is None:
            self._reset_vod_poster_retries()
            return
        if getattr(self, '_{}_loading'.format(kind), False) or getattr(self, '_{}_search_child_open'.format(kind), False) or getattr(self, '_{}_child_open'.format(kind), False) or (getattr(self, '_{}_poster_state'.format(kind), None) is not None):
            self._vod_poster_retry_timer.start(500, True)
            return
        entries = getattr(self, '_{}_entries'.format(kind))
        jobs = []
        for slot, stream_id in self._vod_poster_retries.ready(time.monotonic()):
            index = int(context[2][0]) + slot
            if index < len(entries) and str(entries[index].stream_id) == stream_id:
                item = entries[index]
                jobs.append((slot, item, self._vod_card_url(getattr(item, 'icon', ''))))
        if jobs:
            self._queue_vod_poster_jobs(kind, context[2], jobs)
        self._arm_vod_poster_retries()

    def _schedule_missing_vod_posters(self, kind):
        page_key = getattr(self, '_{}_poster_page_key'.format(kind))
        if page_key is None or getattr(self, '_{}_poster_state'.format(kind)) is not None or getattr(self, '_{}_deferred_poster_jobs'.format(kind)) is not None:
            return
        generation = getattr(self, '_{}_poster_generation'.format(kind))
        entries = getattr(self, '_{}_entries'.format(kind))
        for slot, item in enumerate(entries[int(page_key[0]):int(page_key[0]) + self._poster_page_size]):
            request = getattr(self, '_vod_tile_requests', {}).get((kind, slot))
            if request is not None and request[0][:2] == (generation, str(item.stream_id)):
                continue
            self._schedule_vod_poster_retry(kind, slot, item)

    def _rejected_vod_card_urls(self, kind, item):
        with self._vod_decode_failure_lock:
            return set(self._vod_decode_failures.get((kind, str(item.stream_id)), ()))

    def _retry_vod_poster_after_decode(self, kind, slot, item, path):
        if self._closed:
            return
        url = self._vod_card_url(getattr(item, 'icon', ''))
        if not url or os.path.splitext(os.path.abspath(path))[0] != os.path.splitext(_picon_cache_path(url))[0]:
            return
        with self._vod_decode_failure_lock:
            self._vod_decode_failures.setdefault((kind, str(item.stream_id)), set()).add(url)
        _discard_invalid_live_picon_cache(url, path)
        _mark_picon_failure(url)
        log_event('artwork', 'stage=decode outcome=failed kind={} id={}'.format(kind, _debug_identifier(item.stream_id)))
        self._schedule_vod_poster_retry(kind, slot, item, delay=2.0)

    def _start_movie_poster_jobs(self, page_key, jobs):
        if self._closed or page_key != self._movie_poster_page_key:
            return
        selected_slot = self._movie_selected_index - int(page_key[0])
        jobs = sorted(jobs, key=lambda job: 0 if int(job[0]) == selected_slot else 1)
        if not jobs:
            self._movie_deferred_poster_jobs = None
            return
        generation = getattr(self, '_movie_poster_generation', self._movie_generation)
        timeout = max(WORKER_POSTER_TIMEOUT_SECONDS, poster_batch_timeout(len(jobs), BACKGROUND_POSTER_WORKERS, PICON_DOWNLOAD_TIMEOUT, WORKER_DETAIL_TIMEOUT_SECONDS))
        token = _WorkerToken(timeout)
        _cancel_worker_token(self._movie_poster_token)
        self._movie_poster_token = token
        self._movie_poster_pending = True
        worker_count = min(BACKGROUND_POSTER_WORKERS, len(jobs))
        state = {'generation': generation, 'page_key': page_key, 'selected_slot': selected_slot, 'results': [], 'queued_jobs': deque(jobs), 'pending_workers': worker_count, 'lock': threading.Lock(), 'token': token}
        self._movie_poster_state = state
        self._movie_deferred_poster_jobs = None
        pool = getattr(self, '_grid_poster_workers', None)
        if pool is None:
            pool = self._grid_poster_workers = LatestPosterWorkers()
            self.onClose.append(pool.close)
        target = self._load_movie_posters_worker
        tasks = [lambda batch=jobs[i::worker_count]: target(batch, state, token) for i in range(worker_count)]
        pool.replace(tasks)
        self._movie_timer.start(50, True)


    def _vod_card_url(self, url):
        url = tmdb_poster_url(_safe_picon_url(url), getattr(self, '_poster_image_width', 342))
        return self._vod_poster_url_overrides.get(url, url)

    def _remember_vod_card_size(self, url):
        if not is_tmdb_artwork_url(url):
            return
        canonical = tmdb_poster_url(url, getattr(self, '_poster_image_width', 342))
        if canonical != url:
            self._vod_poster_url_overrides[canonical] = url
            self._vod_poster_url_overrides.move_to_end(canonical)
            while len(self._vod_poster_url_overrides) > 128:
                self._vod_poster_url_overrides.popitem(last=False)

    def _vod_poster_info(self, kind, item, token):
        cache = getattr(self, '_{}_info_cache'.format(kind), {})
        lock = getattr(self, '_{}_info_cache_lock'.format(kind), None)
        info = _detail_cache_get(cache, lock, item.stream_id) if lock is not None else cache.get(item.stream_id)
        if info is not None:
            return info
        poster_cache = getattr(self, '_vod_poster_info_cache', None)
        poster_lock = getattr(self, '_vod_poster_info_lock', None)
        key = (kind, str(item.stream_id))
        if poster_cache is not None and poster_lock is not None:
            with poster_lock:
                info = poster_cache.get(key)
                if info is not None:
                    poster_cache.move_to_end(key)
                    return info
        info = catalogue_info(self.client, kind, item)
        if info is not None:
            return info
        name = 'load_vod_info' if kind == 'movie' else 'load_series_summary'
        loader = getattr(self.client, name, None)
        if not callable(loader):
            return None
        try:
            provider_token = _poster_metadata_token(token)
            info = _bounded_client_call(self.client, provider_token, lambda: loader(item.stream_id))
        except _WorkerCancelled:
            if token is not None:
                token.check()
            return None
        except Exception as error:
            log_event('artwork', 'stage=provider-summary outcome=failed kind={} id={}'.format(kind, _debug_identifier(item.stream_id)), error)
            return None
        if info is not None and poster_cache is not None and (poster_lock is not None):
            with poster_lock:
                poster_cache[key] = info
                poster_cache.move_to_end(key)
                while len(poster_cache) > 128:
                    poster_cache.popitem(last=False)
        return info

    def _fetch_vod_card(self, url, token, priority=False, timeout=None):
        if not url:
            return ''
        if token is not None:
            token.check()
        try:
            timeout = _provider_picon_timeout(self._metadata_client, url) if timeout is None else timeout
            return _download_client_image(self.client, url, _picon_cache_path(url), timeout=timeout, token=token, **{'priority': priority} if priority else {})
        except _WorkerCancelled:
            raise
        except Exception as error:
            _debug('VOD poster failed source={} error={}'.format('tmdb' if is_tmdb_artwork_url(url) else 'provider', error.__class__.__name__))
            return ''

    def _recover_vod_card(self, kind, item, requested_url, token):
        if token is not None:
            token.check()
        rejected = self._rejected_vod_card_urls(kind, item)
        tried = {requested_url} | rejected
        if is_tmdb_artwork_url(requested_url) and requested_url in rejected:
            for width in (185, 342, 500):
                url = tmdb_poster_url(requested_url, width)
                if url in tried:
                    continue
                tried.add(url)
                path = self._fetch_vod_card(url, token)
                if path:
                    return (path, url)
        info = self._vod_poster_info(kind, item, token)
        candidates = [getattr(info, 'cover', '')] + list(getattr(info, 'cover_urls', ()) or ())
        alternatives = 0
        for candidate in candidates:
            url = self._vod_card_url(candidate)
            if not url or url in tried:
                continue
            tried.add(url)
            alternatives += 1
            path = self._fetch_vod_card(url, token)
            if path:
                return (path, url)
            if alternatives >= 2:
                break
        try:
            metadata_token = _poster_metadata_token(token)
            url = _bounded_client_call(self._metadata_client, metadata_token, lambda: self._metadata_client.poster_cover(kind, item, info))
        except _WorkerCancelled:
            if token is not None:
                token.check()
            url = ''
        except Exception as error:
            log_event('artwork', 'stage=poster-lookup outcome=failed kind={} id={}'.format(kind, _debug_identifier(item.stream_id)), error)
            url = ''
        url = self._vod_card_url(url)
        if url and url not in tried:
            path = self._fetch_vod_card(url, token)
            if path:
                return (path, url)
        return ('', '')

    def _load_vod_posters_worker(self, kind, jobs, state, token=None):
        metadata_client = getattr(self, '_metadata_client', None)
        begun = False
        try:
            begin = getattr(metadata_client, 'begin_cache_batch', None)
            if callable(begin):
                begin()
                begun = True
            with state['lock']:
                state.setdefault('queued_jobs', deque(jobs))
                state.setdefault('recovery_jobs', deque())
            while True:
                if token is not None:
                    token.check()
                with state['lock']:
                    recovery = state['recovery_jobs']
                    selected = state.get('selected_slot')
                    promoted = next((job for job in recovery if int(job[0]) == selected), None)
                    if promoted is not None:
                        recovery.remove(promoted)
                        job, recover = promoted, True
                    elif state['queued_jobs']:
                        job, recover = state['queued_jobs'].popleft(), False
                    elif recovery:
                        job, recover = recovery.popleft(), True
                    else:
                        break
                slot, item, requested = job
                url = self._vod_card_url(requested)
                if recover:
                    path, resolved = self._recover_vod_card(kind, item, url, token)
                else:
                    path = '' if url in self._rejected_vod_card_urls(kind, item) else self._fetch_vod_card(url, token)
                    resolved = url
                    if not path:
                        with state['lock']:
                            state['recovery_jobs'].append((slot, item, url))
                        continue
                with state['lock']:
                    state['results'].append((slot, item.stream_id, path, url, resolved, False))
        finally:
            with state['lock']:
                state['pending_workers'] -= 1
            if begun:
                try:
                    metadata_client.end_cache_batch()
                except Exception as error:
                    _debug('poster cache flush failed: {}'.format(error.__class__.__name__))

    def _load_movie_posters_worker(self, jobs, state, token=None):
        self._load_vod_posters_worker('movie', jobs, state, token)

    def _apply_movie_tile_poster(self, slot, path, stream_id='', generation=None):
        if not path:
            return
        generation = getattr(self, '_movie_poster_generation', self._movie_generation) if generation is None else generation
        requests = getattr(self, '_vod_tile_requests', None)
        if requests is None:
            requests = self._vod_tile_requests = {}
        key = ('movie', slot)
        try:
            size = self['movie_tile_poster_{}'.format(slot)].instance.size()
            target_size = (int(size.width()), int(size.height()))
        except (AttributeError, TypeError, RuntimeError):
            target_size = None
        signature = (generation, str(stream_id), path, getattr(path, 'file_identity', None), target_size)
        previous = requests.get(key)
        if previous is not None and previous[0] == signature:
            return
        request = (signature, object())
        requests[key] = request
        displayed = getattr(self, '_vod_tile_displayed', None)
        if displayed is None:
            displayed = self._vod_tile_displayed = {}
        previous_display = displayed.get(key)
        was_visible = bool(previous_display is not None and previous_display[:2] == signature[:2] and getattr(self['movie_tile_poster_{}'.format(slot)], 'visible', False))

        def loaded(success):
            if self._closed or requests.get(key) is not request:
                return
            try:
                page_size = getattr(self, '_poster_page_size', CINEMATIC_POSTER_COLUMNS)
                page_start = self._movie_selected_index // page_size * page_size
                current = self._movie_entries[page_start + slot]
                valid = generation == getattr(self, '_movie_poster_generation', self._movie_generation) and str(current.stream_id) == str(stream_id)
                if not valid:
                    return
                if success:
                    displayed[key] = signature
                    self._vod_poster_retries.succeeded(stream_id)
                    self._arm_vod_poster_retries()
                    _set_component_visible(self['movie_tile_poster_{}'.format(slot)], True)
                    _set_component_visible(self['movie_tile_placeholder_{}'.format(slot)], False)
                else:
                    requests.pop(key, None)
                    if not was_visible:
                        _set_component_visible(self['movie_tile_poster_{}'.format(slot)], False)
                    self._retry_vod_poster_after_decode('movie', slot, current, path)
            except Exception:
                pass
        try:
            attach_pixmap(self, 'movie_tile_poster_{}'.format(slot), path, key='movie-tile-{}'.format(slot), on_loaded=loaded)
        except Exception as error:
            log_event('artwork', 'stage=decode outcome=start-failed', error)
            loaded(False)

    def _start_movie_detail(self):
        if getattr(self, '_poster_grid_enabled', False):
            self._start_grid_detail('movie')
            return
        if not self._movie_entries:
            return
        item = self._movie_entries[self._movie_selected_index]
        generation = self._movie_generation
        _cancel_worker_token(self._movie_detail_token)
        token = _WorkerToken(WORKER_DETAIL_TIMEOUT_SECONDS)
        self._movie_detail_token = token
        if getattr(self, '_cinematic_enabled', False):
            info = _detail_cache_get(self._movie_info_cache, self._movie_info_cache_lock, item.stream_id)
            self._start_cinematic_backdrop('movie', item, info)
        pool = getattr(self, '_grid_detail_workers', None)
        if pool is None:
            pool = self._grid_detail_workers = LatestPosterWorkers(worker_count=2)
            self.onClose.append(pool.close)
        self._movie_detail_pending = True
        pool.replace([lambda: self._load_movie_detail_worker(generation, item, token)])
        self._start_deferred_movie_posters()
        self._movie_timer.start(80, True)



    def _catalogue_detail_preview(self, kind, generation, item, token):
        previewer = getattr(self._metadata_client, 'catalogue_preview', None)
        if not callable(previewer):
            return None
        batch = getattr(self._metadata_client, 'cache_batch', None)
        with batch() if callable(batch) else nullcontext():
            info = _bounded_client_call(self._metadata_client, token, lambda: previewer(kind, item))
            if info is not None and generation == getattr(self, '_' + kind + '_generation') and (token is None or token.active()):
                url = _safe_picon_url(getattr(info, 'cover', '') or getattr(item, 'icon', ''))
                setattr(self, '_' + kind + '_detail_preview', (generation, info, _cached_picon_path(url) if url else '', '', url, _safe_picon_url(getattr(item, 'icon', ''))))
        return info

    def _load_movie_detail_worker(self, generation, item, token=None):
        if token is not None:
            token.check()
        catalogue_poster_url = _safe_picon_url(getattr(item, 'icon', ''))
        info = _detail_cache_get(self._movie_info_cache, self._movie_info_cache_lock, item.stream_id) if hasattr(self, '_movie_info_cache_lock') else self._movie_info_cache.get(item.stream_id)
        metadata_status = ''
        early_info = catalogue_info(self.client, 'movie', item) if info is None else None
        if info is None and early_info is not None and early_info.plot:
            info = early_info
        if info is None:
            localized = self._catalogue_detail_preview('movie', generation, item, token)
            if localized is not None:
                info = localized
                early_info = localized
        if early_info is not None and generation == self._movie_generation and (token is None or token.active()):
            preview_url = _safe_picon_url(getattr(early_info, 'cover', '') or getattr(item, 'icon', ''))
            self._movie_detail_preview = (generation, early_info, _cached_picon_path(preview_url) if preview_url else '', '', preview_url, catalogue_poster_url)
        loader = getattr(self.client, 'load_vod_info', None)
        if info is None and loader is not None:
            try:
                info = _bounded_client_call(self.client, token, lambda: loader(item.stream_id))
                if hasattr(self, '_movie_info_cache_lock'):
                    _detail_cache_put(self._movie_info_cache, self._movie_info_cache_lock, item.stream_id, info)
                else:
                    self._movie_info_cache[item.stream_id] = info
            except Exception as error:
                _debug('movie detail failed id={} error={}'.format(item.stream_id, error.__class__.__name__))
                info = None
        if token is not None:
            token.check()
        if early_info is None and info is not None and (generation == self._movie_generation) and (token is None or token.active()):
            preview_url = _safe_picon_url(getattr(info, 'cover', '') or getattr(item, 'icon', ''))
            self._movie_detail_preview = (generation, info, _cached_picon_path(preview_url) if preview_url else '', '', preview_url, catalogue_poster_url)
        try:
            completer = None
            if self._cinematic_enabled:
                completer = getattr(self._metadata_client, 'complete_movie_cinematic', None)
            if not callable(completer):
                completer = self._metadata_client.complete_movie
            info = _bounded_client_call(self._metadata_client, token, lambda: complete_movie_for_device_language(self._metadata_client, completer, item, info))
            if hasattr(self, '_movie_info_cache_lock'):
                _detail_cache_put(self._movie_info_cache, self._movie_info_cache_lock, item.stream_id, info)
            else:
                self._movie_info_cache[item.stream_id] = info
            metadata_status = str(getattr(self._metadata_client, 'last_error', '') or '')
        except MetadataError as error:
            metadata_status = str(error)
            _debug('TMDb movie detail fallback failed id={} error={}'.format(item.stream_id, error.__class__.__name__))
        poster_url = _safe_picon_url(getattr(info, 'cover', '') or getattr(item, 'icon', ''))
        poster_path = _cached_picon_path(poster_url) if poster_url else ''
        if generation == self._movie_generation and (token is None or token.active()):
            self._movie_detail_result = (generation, info, poster_path, metadata_status, poster_url, catalogue_poster_url)



    def refresh_movies(self):
        if self.content_type != 'movie':
            self.start_load()
            return
        if self._movie_search_child_open:
            self['message'].setText(_('Please wait'))
            return
        for name in ('_load_token', '_movie_load_token', '_movie_more_token', '_movie_detail_token', '_movie_poster_token'):
            _cancel_worker_token(getattr(self, name, None))
        self._loading = self._movie_loading = self._movie_more_loading = False
        self._movie_generation += 1
        self._movie_catalog_generation += 1
        self._movie_poster_generation = getattr(self, '_movie_poster_generation', 0) + 1
        self._movie_more_result = self._movie_detail_preview = self._movie_detail_result = None
        self._movie_poster_state = self._movie_deferred_poster_jobs = None
        self._movie_ready = self._movie_detail_pending = self._movie_poster_pending = False
        retry_entries = self._movie_entries.cached_items() if getattr(self._movie_entries, 'full_category', False) else self._movie_entries
        browse_catalog = getattr(self, '_movie_browser_catalog', None)
        if browse_catalog is not None:
            browse_catalog.invalidate()
            self._movie_index_reload_when_done = False
        for current in retry_entries:
            url = _safe_picon_url(getattr(current, 'icon', ''))
            if url:
                _clear_picon_failure(url)
        invalidator = getattr(self.client, 'invalidate_cache', None)
        if invalidator is not None:
            try:
                category_id = self._entries[self.selected_index].category_id if self._entries else None
                invalidator('movie', category_id)
            except Exception:
                pass
        self._movie_poster_page_key = None
        if self._movie_focus == 'categories':
            self.start_load()
        else:
            self._start_movie_load()



    def _stop_movie(self):
        content_sort_ui.cancel_loading(self, 'movie')
        preparation = getattr(self, '_vod_preparation', None)
        if preparation is not None:
            preparation.invalidate()
        self._reset_vod_poster_retries()
        pool = getattr(self, '_grid_detail_workers', None)
        if pool is not None:
            pool.close()
        browse_catalog = getattr(self, '_movie_browser_catalog', None)
        if browse_catalog is not None:
            browse_catalog.close()
        reset_cinematic = getattr(self, '_reset_cinematic_backdrop', None)
        if callable(reset_cinematic):
            reset_cinematic()
        try:
            plot_widget = self['movie_plot']
        except (KeyError, TypeError):
            plot_widget = None
        stop_scroll = getattr(plot_widget, 'stop_scroll', None)
        if callable(stop_scroll):
            stop_scroll()
        self._movie_generation += 1
        self._movie_catalog_generation += 1
        self._movie_poster_generation = getattr(self, '_movie_poster_generation', self._movie_generation) + 1
        self._movie_search_child_open = False
        self._movie_search_child = None
        self._movie_search_keyboard_open = False
        self._movie_search_drain_event = None
        self._movie_search_resume_categories = False
        self._movie_search_resume_category_id = None
        self._movie_search_resume_more = False
        self._movie_search_resume_detail = False
        self._movie_search_resume_posters = False
        self._movie_more_loading = False
        self._movie_more_result = None
        self._movie_pending_advance = False
        self._movie_pending_selection_index = None
        for token in (self._movie_load_token, self._movie_more_token, self._movie_detail_token, self._movie_poster_token):
            _cancel_worker_token(token)
        self._movie_load_token = None
        self._movie_more_token = None
        self._movie_detail_token = None
        self._movie_poster_token = None
        self._movie_poster_state = None
        self._movie_deferred_poster_jobs = None
        movie_cache_lock = getattr(self, '_movie_info_cache_lock', None)
        if movie_cache_lock is None:
            self._movie_info_cache.clear()
        else:
            with movie_cache_lock:
                self._movie_info_cache.clear()
        for timer in (self._movie_timer, self._movie_category_timer, self._movie_detail_timer, self._movie_search_resume_timer):
            try:
                timer.stop()
            except Exception:
                pass


    def _set_series_title(self, value):
        return fit_dynamic_text(self['series_title'], value, max_lines=1 if getattr(self, '_poster_grid_enabled', False) else 2, preferred_size=self._series_title_preferred_font, min_size=self._series_title_min_font, fallback_chars=18)

    def _set_static_series_title(self, value):
        return set_static_text(self['series_title'], value, preferred_size=self._series_title_preferred_font)


    def _series_rail_page_size(self):
        return getattr(self, '_poster_page_size', CINEMATIC_POSTER_COLUMNS if getattr(self, '_cinematic_enabled', False) else SERIES_GRID_PAGE_SIZE)



    def _loaded_series_category(self):
        category_id = str(self._series_loaded_category_id or '')
        for category in self._entries:
            if str(getattr(category, 'category_id', '') or '') == category_id:
                return category
        return None

    def _selected_series_category_is_loaded(self):
        if not self._entries:
            return False
        category = self._entries[self.selected_index]
        return str(self._series_loaded_category_id or '') == str(getattr(category, 'category_id', '') or '')


    def _restore_series_selection(self):
        """Restore a category's grid position without wrapping partial pages."""
        if not self._series_entries:
            self._series_selected_index = 0
            return
        category_id = str(self._series_loaded_category_id or '')
        saved = self._series_selection_by_category.get(category_id)
        if not saved:
            self._series_selected_index = 0
            return
        saved_index, saved_stream_id = saved
        saved_stream_id = str(saved_stream_id or '')
        if saved_stream_id:
            if getattr(self._series_entries, 'full_category', False):
                index = self._series_entries.position(saved_stream_id)
                if index is not None:
                    self._series_selected_index = index
                    return
                if getattr(self._series_entries, 'progressive', False):
                    self._series_entries.restore_identity = (saved_stream_id, self._series_generation)
                    self._series_selected_index = 0
                    return
            else:
                for index, item in enumerate(self._series_entries):
                    if str(getattr(item, 'stream_id', '') or '') == saved_stream_id:
                        self._series_selected_index = index
                        return
        try:
            saved_index = max(0, int(saved_index))
        except (TypeError, ValueError, OverflowError):
            saved_index = 0
        base = self._vod_window_base('series')
        if not base <= saved_index < base + len(self._series_entries) and (getattr(self, '_series_browser_catalog', None) is not None or getattr(self, '_series_native_window', None) is not None):
            self._request_vod_window('series', saved_index - base)
            return
        saved_index -= base
        self._series_selected_index = min(saved_index, len(self._series_entries) - 1)
        if saved_index >= len(self._series_entries) and self._series_has_more:
            self._series_pending_advance = True
            self._series_pending_selection_index = saved_index
            self._series_pending_clamp_to_end = True

    def _schedule_series_load(self, category=None, immediate=False):
        if not self._entries or self._closed or getattr(self, '_series_search_child_open', False):
            return
        category = category or self._entries[self.selected_index]
        self._series_pending_category_id = category.category_id
        try:
            self._series_category_timer.stop()
        except Exception:
            pass
        if immediate:
            self._commit_series_category_load()
        else:
            self._series_category_timer.start(NAVIGATION_DEBOUNCE_MS, True)

    def _commit_series_category_load(self):
        if not self._entries or self._closed:
            return
        category = self._entries[self.selected_index]
        if category.category_id != self._series_pending_category_id:
            return
        self._series_pending_category_id = None
        self._start_series_load(category)

    def _start_series_load(self, category=None):
        self._remember_series_selection()
        if category is None and self._entries:
            category = self._entries[self.selected_index]
        if category is not None:
            content_sort_ui.category_started(self, 'series', category.category_id)
        preparation = getattr(self, '_vod_preparation', None)
        if preparation is not None:
            preparation.invalidate()
        if not self._entries or self._closed or getattr(self, '_series_search_child_open', False):
            return
        category = category or self._entries[self.selected_index]
        for timer in (self._series_category_timer, self._series_detail_timer, self._series_scan_timer):
            try:
                timer.stop()
            except Exception:
                pass
        self._series_pending_category_id = None
        self._series_pending_detail_generation = None
        for token in (self._series_load_token, self._series_more_token, self._series_detail_token, self._series_poster_token):
            _cancel_worker_token(token)
        self._series_category_load_started = True
        self._series_generation += 1
        self._series_catalog_generation += 1
        self._series_poster_generation = getattr(self, '_series_poster_generation', self._series_generation) + 1
        generation = self._series_generation
        self._series_loading = True
        self._series_ready = False
        self._series_error = ''
        old_window = getattr(self, '_series_native_window', None)
        if old_window is not None:
            old_window.close()
        if _is_stalker_client(self.client) and getattr(self, '_series_browser_catalog', None) is None:
            self._series_native_window = NativeBrowseWindow('series')
            if not getattr(self, '_native_window_close_hook', False):
                self._native_window_close_hook = True
                self.onClose.append(self._close_native_vod_windows)
        self._series_entries = []
        self._series_browse_cursor = 0
        self._series_selected_index = 0
        self._series_loaded_category_id = category.category_id
        self._series_detail_preview = None
        self._series_detail_result = None
        self._series_detail_pending = False
        self._series_poster_state = None
        self._series_poster_pending = False
        self._series_poster_page_key = None
        self._series_has_more = False
        self._series_more_loading = False
        self._series_more_result = None
        self._series_pending_advance = False
        self._series_pending_selection_index = None
        self._series_pending_clamp_to_end = False
        self._series_load_token = None
        self._series_more_token = None
        self._series_detail_token = None
        self._series_poster_token = None
        self._series_deferred_poster_jobs = None
        self._series_info = None
        self._series_info_stream_id = ''
        ellipsize_dynamic_text(self['series_rail_caption'], _('{} • LOADING SERIES').format(localized_upper(category.name)), fallback_chars=65)
        self._clear_series_tiles()
        self._render_series_waiting()
        browse_catalog = getattr(self, '_series_browser_catalog', None)
        if browse_catalog is not None:
            browse_catalog.start(preferred_category=category.category_id)
        token = _WorkerToken(browse_catalog.load_timeout if browse_catalog is not None else WORKER_LIST_TIMEOUT_SECONDS)
        self._series_load_token = token
        begin_delivery = getattr(self, '_light_begin_list', None)
        if callable(begin_delivery): begin_delivery('series', generation)
        worker = threading.Thread(target=self._load_series_worker, args=(generation, category.category_id, token))
        worker.daemon = True
        worker.start()
        self._series_timer.start(120, True)

    def _load_series_worker(self, generation, category_id, token=None):
        browse_catalog = getattr(self, '_series_browser_catalog', None)
        native_window = getattr(self, '_series_native_window', None)
        try:
            if browse_catalog is not None:
                page = content_sort_ui.initial_page(browse_catalog, category_id, self._hidden_category_ids, token)
                page = content_sort_ui.restore_page(self, browse_catalog, page, 'series', category_id, generation, token)
                entries = page.items
                error = '' if entries or page.catalog_state.get('complete') else N_('Could not load series')
            else:
                entries = list(_bounded_client_call(self.client, token, lambda: self.client.load_items('series', category_id)) or [])
                error = ''
            if browse_catalog is None and (not str(category_id or '').strip() or category_filter_active(getattr(self, '_hidden_category_ids', ()))):
                entries = filter_hidden_category_items(entries, self._hidden_category_ids, category_id)
        except _WorkerCancelled:
            return
        except ContentError as caught:
            entries = []
            error = str(caught)
        except (CatalogStorageError, CatalogLimitError, MemoryError) as caught:
            entries = []
            error = catalog_failure_message(caught)
        except Exception:
            entries = []
            error = N_('Could not load series')
        if self._closed or generation != self._series_generation or (token is not None and (not token.active())):
            return
        if native_window is not None and (not error):
            try:
                entries = native_window.append(entries, self._client_has_more_items('series', category_id), token)[:240]
            except Exception:
                entries = []
                error = N_('Search incomplete.')
        self._series_window_base = getattr(page, 'sort_base', 0) if browse_catalog is not None and (not error) else 0
        self._series_selected_index = getattr(page, 'sort_selected', 0) if browse_catalog is not None and (not error) else 0
        self._series_known_ids = set() if getattr(entries, 'full_category', False) else {str(item.stream_id) for item in entries}
        self._series_entries = entries
        self._series_error = error
        if browse_catalog is not None:
            self._series_has_more = page.has_more if not error else False
            if not error:
                self._series_browse_cursor = page.next_cursor
        else:
            self._series_has_more = self._client_has_more_items('series', category_id)
        if native_window is not None and (not error):
            self._series_has_more = native_window.has_more or native_window.count > len(entries)
        self._series_ready = True
        publish = getattr(self, '_light_publish_list', None)
        if callable(publish): publish('series', generation)

    def _start_series_more_load(self):
        loader = getattr(self.client, 'load_more_items', None)
        if not callable(loader) and getattr(self, '_series_browser_catalog', None) is None or not self._series_has_more or self._series_more_loading or self._closed:
            return False
        try:
            self._series_scan_timer.stop()
        except Exception:
            pass
        self._series_more_loading = True
        generation = self._series_catalog_generation
        category_id = str(self._series_loaded_category_id or '')
        token = _WorkerToken(WORKER_LIST_TIMEOUT_SECONDS)
        _cancel_worker_token(self._series_more_token)
        self._series_more_token = token
        worker = threading.Thread(target=self._load_series_more_worker, args=(generation, category_id, loader, token))
        worker.daemon = True
        worker.start()
        self._series_timer.start(80, True)
        return True

    def _load_series_more_worker(self, generation, category_id, loader, token=None):
        browse_catalog = getattr(self, '_series_browser_catalog', None)
        native_window = getattr(self, '_series_native_window', None)
        page = None
        try:
            local_end = self._vod_window_base('series') + len(self._series_entries)
            if native_window is not None and local_end < native_window.count:
                page = native_window.read_window(category_id, offset=local_end, size=80, token=token)
                entries = list(page.items)
            elif browse_catalog is not None:
                page = browse_catalog.read(category_id, after=self._series_browse_cursor, hidden_category_ids=self._hidden_category_ids, token=token)
                entries = list(page.items)
            else:
                entries = list(_bounded_client_call(self.client, token, lambda: loader('series', category_id)) or [])
            if not str(category_id or '').strip() or category_filter_active(getattr(self, '_hidden_category_ids', ())):
                entries = filter_hidden_category_items(entries, self._hidden_category_ids, category_id)
            error = ''
        except _WorkerCancelled:
            return
        except ContentError as caught:
            entries = []
            error = str(caught)
        except (CatalogStorageError, CatalogLimitError, MemoryError) as caught:
            entries = []
            error = catalog_failure_message(caught)
        except Exception:
            entries = []
            error = N_('Could not load series')
        has_more = page.has_more if page is not None else self._client_has_more_items('series', category_id) if browse_catalog is None else False
        if native_window is not None and (not error) and (page is None):
            try:
                entries = native_window.append(entries, has_more, token)[:80]
                has_more = native_window.has_more or local_end + len(entries) < native_window.count
            except Exception:
                entries, has_more = ([], False)
                error = N_('Search incomplete.')
        if self._closed or generation != self._series_catalog_generation or (token is not None and (not token.active())):
            return
        if page is not None:
            self._series_browse_cursor = page.next_cursor
        self._series_more_result = (generation, category_id, entries, has_more, error)

    def _maybe_load_more_series(self):
        if not self._series_entries or not self._series_has_more:
            return False
        page_size = getattr(self, '_poster_page_size', CINEMATIC_POSTER_COLUMNS if getattr(self, '_cinematic_enabled', False) else SERIES_GRID_PAGE_SIZE)
        remaining = len(self._series_entries) - self._series_selected_index - 1
        if remaining > page_size and (not self._series_pending_advance):
            return False
        return self._start_series_more_load()

    def _series_catalog_diagnostic(self):
        diagnostic = getattr(self.client, 'catalog_diagnostic', None)
        if not callable(diagnostic):
            return {}
        try:
            result = diagnostic('series', str(self._series_loaded_category_id or ''))
        except Exception:
            return {}
        return result if isinstance(result, dict) else {}

    def _series_scan_caption(self):
        diagnostic = self._series_catalog_diagnostic()
        pages = int(diagnostic.get('pages', 0) or 0)
        return _('Series scan • {} pages').format(pages)

    def _schedule_series_scan(self, delay_ms=SERIES_PROGRESSIVE_SCAN_DELAY_MS):
        if self._closed or self._series_loading or self._series_more_loading or (not self._series_has_more) or (self._series_entries and (not self._series_pending_advance) and (len(self._series_entries) - self._series_selected_index - 1 > getattr(self, '_poster_page_size', 14))):
            return False
        try:
            self._series_scan_timer.stop()
            self._series_scan_timer.start(max(1, int(delay_ms)), True)
        except Exception:
            return False
        return True

    def _resume_series_scan(self):
        if self._closed or self._series_loading or self._series_more_loading or (not self._series_has_more) or (self._series_entries and (not self._series_pending_advance) and (len(self._series_entries) - self._series_selected_index - 1 > getattr(self, '_poster_page_size', 14))):
            return
        if self._series_detail_pending or self._series_pending_detail_generation is not None:
            self._schedule_series_scan()
            return
        self._start_series_more_load()


    def _apply_series_more_result(self):
        result = self._series_more_result
        if result is None:
            return
        self._series_more_result = None
        generation, category_id, entries, has_more, error = result[:5]
        replacement = result[5] if len(result) > 5 else None
        if generation != self._series_catalog_generation or category_id != str(self._series_loaded_category_id or ''):
            return
        self._series_more_loading = False
        self._series_more_token = None
        self._series_has_more = bool(has_more)
        if error:
            self._series_pending_advance = False
            self._series_pending_selection_index = None
            self._series_pending_clamp_to_end = False
            ellipsize_dynamic_text(self['message'], _(error), fallback_chars=90)
            if not self._series_entries:
                self._series_error = error
                self._render_series_results()
            return
        if self._apply_vod_window('series', entries, replacement):
            return
        old_length = len(self._series_entries)
        known = getattr(self, '_series_known_ids', None)
        if known is None:
            known = self._series_known_ids = {str(item.stream_id) for item in self._series_entries}
        for item in entries:
            stream_id = str(getattr(item, 'stream_id', ''))
            if not stream_id or stream_id in known:
                continue
            known.add(stream_id)
            self._series_entries.append(item)
        if old_length == 0 and self._series_entries and (not self._series_pending_advance):
            self._series_pending_advance = False
            self._series_pending_selection_index = None
            self._series_pending_clamp_to_end = False
            self._render_series_results()
            return
        if not self._series_entries:
            if self._series_has_more and (not error):
                self['series_rail_caption'].setText(self._series_scan_caption())
                self._set_series_plot(_('Please wait'))
                self._schedule_series_scan()
                return
            self._series_pending_advance = False
            self._series_pending_selection_index = None
            self._series_pending_clamp_to_end = False
            self._render_series_results()
            return
        self._update_series_rail_caption()
        if self._series_pending_advance:
            pending_index = getattr(self, '_series_pending_selection_index', None)
            if pending_index is None and len(self._series_entries) > old_length:
                pending_index = old_length
            if pending_index is not None and pending_index < len(self._series_entries):
                self._series_pending_advance = False
                self._series_pending_selection_index = None
                self._series_pending_clamp_to_end = False
                self._select_series(pending_index)
                return
            if self._series_has_more:
                self._start_series_more_load()
                return
            clamp_to_end = self._series_pending_clamp_to_end
            self._series_pending_advance = False
            self._series_pending_selection_index = None
            self._series_pending_clamp_to_end = False
            if clamp_to_end and self._series_entries:
                last_index = len(self._series_entries) - 1
                if last_index != self._series_selected_index:
                    self._select_series(last_index)
                    return
        self._series_pending_advance = False
        self._series_pending_selection_index = None
        self._series_pending_clamp_to_end = False
        self._trim_vod_window('series')
        self._render_series_tiles()
        self._schedule_series_scan()


    def _poll_series_result(self):
        if self._closed or getattr(self, '_series_search_child_open', False):
            return
        browse_catalog = getattr(self, '_series_browser_catalog', None)
        if browse_catalog is not None:
            self._update_series_catalog_message()
            if getattr(self, '_light_vod_enabled', False):
                self._light_refresh_count()
            if self._series_index_reload_when_done and (not browse_catalog.state.get('preparing')):
                self._series_index_reload_when_done = False
                self._start_series_load()
                return
        if self._series_loading:
            if not self._series_ready:
                if self._series_load_token is not None and self._series_load_token.expired():
                    _cancel_worker_token(self._series_load_token)
                    if browse_catalog is not None:
                        browse_catalog.stop()
                    self._series_loading = False
                    self._series_error = N_('Series request timed out')
                    self._render_series_results()
                    return
                self._series_timer.start(120, True)
                return
            self._series_loading = False
            self._series_load_token = None
            content_sort_ui.refresh_initial(self, 'series')
            self._render_series_results()
            if browse_catalog is not None and (browse_catalog.state.get('preparing') or getattr(self._series_entries, 'progressive', False)):
                self._series_timer.start(120, True)
            return
        growing_pending = content_sort_ui.poll_loading(self, 'series')
        if growing_pending:
            self._series_timer.start(120, True)
        if self._series_more_loading and self._series_more_token is not None and self._series_more_token.expired():
            _cancel_worker_token(self._series_more_token)
            self._series_more_loading = False
            self._series_more_token = None
            self._series_pending_advance = False
            self._series_pending_selection_index = None
        self._apply_series_more_result()
        preview = getattr(self, '_series_detail_preview', None)
        if preview is not None:
            self._series_detail_preview = None
            if self._series_detail_result is None and preview[0] == self._series_generation:
                self._apply_series_detail(_pending_vod_detail(preview[1]), *preview[2:])
        detail = self._series_detail_result
        if detail is not None:
            self._series_detail_preview = None
            self._series_detail_result = None
            if not getattr(self, '_poster_grid_enabled', False) or detail[0] == self._series_generation:
                self._series_detail_pending = False
                self._series_detail_token = None
            generation, info, poster_path, metadata_status, poster_url, catalogue_poster_url = detail
            if generation == self._series_generation:
                self._apply_series_detail(info, poster_path, metadata_status, poster_url, catalogue_poster_url)
            self._start_deferred_series_posters()
        elif self._series_detail_pending and self._series_detail_token is not None and self._series_detail_token.expired():
            _cancel_worker_token(self._series_detail_token)
            self._series_detail_token = None
            self._series_detail_pending = False
            if getattr(self, '_poster_grid_enabled', False):
                self._render_grid_detail('series', self._cached_grid_detail('series'))
            self._start_deferred_series_posters()
        state = self._series_poster_state
        if state is not None:
            state_token = state.get('token')
            state_expired = state_token is not None and state_token.expired()
            if state_expired:
                _cancel_worker_token(state_token)
            with state['lock']:
                selected_slot = self._series_selected_index - int(state['page_key'][0])
                state['results'].sort(key=lambda result: int(result[0]) != selected_slot)
                limit = 2 if getattr(self, '_poster_grid_enabled', False) else len(state['results'])
                results = state['results'][:limit]
                del state['results'][:limit]
                complete = (state_expired or state['pending_workers'] <= 0) and (not state['results'])
                if complete and 'queued_jobs' in state:
                    state['queued_jobs'].clear()
                    state.get('recovery_jobs', deque()).clear()
            generation = state['generation']
            page_is_current = state.get('page_key') == self._series_poster_page_key
            if page_is_current:
                for slot, stream_id, path, requested_url, resolved_url, fallback_deferred in results:
                    try:
                        page_start = int(state['page_key'][0])
                        page_item = self._series_entries[page_start + slot]
                        result_is_current = generation == getattr(self, '_series_poster_generation', self._series_generation) and str(page_item.stream_id) == str(stream_id)
                    except Exception:
                        page_item = None
                        result_is_current = False
                    if not result_is_current:
                        continue
                    if path and resolved_url:
                        self._remember_vod_card_size(resolved_url)
                    current_url = self._vod_card_url(getattr(page_item, 'icon', ''))
                    requested_url = self._vod_card_url(requested_url)
                    resolved_url = self._vod_card_url(resolved_url)
                    if path and current_url not in ('', requested_url, resolved_url):
                        continue
                    if path and resolved_url and (current_url in ('', requested_url)):
                        page_item.icon = resolved_url
                    if path:
                        poster_urls = getattr(self, '_series_poster_urls', None)
                        if poster_urls is not None:
                            poster_urls[slot] = resolved_url or current_url
                    if fallback_deferred and (not path):
                        retry_url = self._vod_card_url(getattr(page_item, 'icon', ''))
                        self._defer_vod_poster_jobs('series', self._series_poster_page_key, [(slot, page_item, retry_url)], True)
                    self._apply_series_tile_poster(slot, path, stream_id=stream_id, generation=generation)
                    try:
                        selected_item = self._series_entries[self._series_selected_index]
                    except Exception:
                        selected_item = None
                    if selected_item is not None and str(selected_item.stream_id) == str(stream_id):
                        self._apply_detail_poster('series', selected_item, path)
            if complete or not page_is_current:
                if self._series_poster_state is state:
                    self._series_poster_state = None
                    self._series_poster_pending = False
                    self._series_poster_token = None
                if page_is_current:
                    self._start_deferred_series_posters()
                    if complete:
                        self._schedule_missing_vod_posters('series')
        poll_cinematic = getattr(self, '_poll_cinematic_backdrop', None)
        cinematic_pending = bool(poll_cinematic() if callable(poll_cinematic) else False)
        if self._series_detail_pending or self._series_poster_pending or self._series_more_loading or cinematic_pending or (browse_catalog is not None and browse_catalog.state.get('preparing')):
            self._series_timer.start(self._vod_poster_poll_delay('series'), True)


    def _series_catalog_outcome(self):
        result = self._series_catalog_diagnostic()
        if not result.get('complete'):
            return ''
        return str(result.get('outcome', '') or '')

    def _render_series_results(self):
        if getattr(self, '_poster_grid_enabled', False):
            self['hint'].setText('')
        if self._series_error:
            self['series_rail_caption'].setText(_('COULD NOT LOAD SERIES'))
            self._set_series_plot(_(self._series_error))
            self._clear_series_tiles()
            return
        if not self._series_entries:
            if self._series_has_more:
                self['series_rail_caption'].setText(self._series_scan_caption())
                self._set_series_plot(_('Please wait'))
                self._clear_series_tiles()
                self._schedule_series_scan()
                return
            outcome = self._series_catalog_outcome()
            if outcome == 'unsupported':
                self['series_rail_caption'].setText(_('SERIES DATA COULD NOT BE READ'))
                self._set_series_plot(_('The series response uses an unsupported format. Categories remain available.'))
            elif outcome == 'error':
                self['series_rail_caption'].setText(_('SERIES DATA UNAVAILABLE'))
                self._set_series_plot(_('The server did not answer the series request. Categories remain available.'))
            else:
                self['series_rail_caption'].setText(_('NO SERIES FOUND IN THIS CATEGORY'))
                self._set_series_plot(_('No series were found on this server or in this category.'))
            self._clear_series_tiles()
            return
        self._restore_series_selection()
        self._update_series_rail_caption()
        self._schedule_series_detail()
        self._render_series_tiles()
        self._maybe_load_more_series()
        self._schedule_series_scan()


    def _series_grid_position_text(self):
        if getattr(self, '_poster_grid_enabled', False):
            return page_position(self._vod_window_base('series') + self._series_selected_index, self._vod_window_base('series') + len(self._series_entries), self._series_has_more)
        if not self._series_entries:
            return ''
        page_size = getattr(self, '_poster_page_size', CINEMATIC_POSTER_COLUMNS if getattr(self, '_cinematic_enabled', False) else SERIES_GRID_PAGE_SIZE)
        base = self._vod_window_base('series')
        count = base + len(self._series_entries)
        page_start = int((base + self._series_selected_index) / page_size) * page_size
        page_end = min(page_start + page_size, count)
        page_number = int(page_start / page_size) + 1
        page_label = localized_upper(_('Page'))
        if self._series_has_more:
            return '{}–{} / {}+  •  {} {}'.format(page_start + 1, page_end, count, page_label, page_number)
        page_count = max(1, int((count + page_size - 1) / page_size))
        return '{}–{} / {}  •  {} {}/{}'.format(page_start + 1, page_end, count, page_label, page_number, page_count)

    def _start_series_poster_jobs(self, page_key, jobs):
        if self._closed or page_key != self._series_poster_page_key:
            return
        selected_slot = self._series_selected_index - int(page_key[0])
        jobs = sorted(jobs, key=lambda job: 0 if int(job[0]) == selected_slot else 1)
        if not jobs:
            self._series_deferred_poster_jobs = None
            return
        generation = getattr(self, '_series_poster_generation', self._series_generation)
        timeout = max(WORKER_POSTER_TIMEOUT_SECONDS, poster_batch_timeout(len(jobs), BACKGROUND_POSTER_WORKERS, PICON_DOWNLOAD_TIMEOUT, WORKER_DETAIL_TIMEOUT_SECONDS))
        token = _WorkerToken(timeout)
        _cancel_worker_token(self._series_poster_token)
        self._series_poster_token = token
        self._series_poster_pending = True
        worker_count = min(BACKGROUND_POSTER_WORKERS, len(jobs))
        state = {'generation': generation, 'page_key': page_key, 'selected_slot': selected_slot, 'results': [], 'queued_jobs': deque(jobs), 'pending_workers': worker_count, 'lock': threading.Lock(), 'token': token}
        self._series_poster_state = state
        self._series_deferred_poster_jobs = None
        pool = getattr(self, '_grid_poster_workers', None)
        if pool is None:
            pool = self._grid_poster_workers = LatestPosterWorkers()
            self.onClose.append(pool.close)
        target = self._load_series_posters_worker
        tasks = [lambda batch=jobs[i::worker_count]: target(batch, state, token) for i in range(worker_count)]
        pool.replace(tasks)
        self._series_timer.start(50, True)


    def _load_series_posters_worker(self, jobs, state, token=None):
        self._load_vod_posters_worker('series', jobs, state, token)

    def _apply_series_tile_poster(self, slot, path, stream_id='', generation=None):
        if not path:
            return
        generation = getattr(self, '_series_poster_generation', self._series_generation) if generation is None else generation
        requests = getattr(self, '_vod_tile_requests', None)
        if requests is None:
            requests = self._vod_tile_requests = {}
        key = ('series', slot)
        try:
            size = self['series_tile_poster_{}'.format(slot)].instance.size()
            target_size = (int(size.width()), int(size.height()))
        except (AttributeError, TypeError, RuntimeError):
            target_size = None
        signature = (generation, str(stream_id), path, getattr(path, 'file_identity', None), target_size)
        previous = requests.get(key)
        if previous is not None and previous[0] == signature:
            return
        request = (signature, object())
        requests[key] = request
        displayed = getattr(self, '_vod_tile_displayed', None)
        if displayed is None:
            displayed = self._vod_tile_displayed = {}
        previous_display = displayed.get(key)
        was_visible = bool(previous_display is not None and previous_display[:2] == signature[:2] and getattr(self['series_tile_poster_{}'.format(slot)], 'visible', False))

        def loaded(success):
            if self._closed or requests.get(key) is not request:
                return
            try:
                page_size = getattr(self, '_poster_page_size', CINEMATIC_POSTER_COLUMNS if getattr(self, '_cinematic_enabled', False) else SERIES_GRID_PAGE_SIZE)
                page_start = int(self._series_selected_index / page_size) * page_size
                current = self._series_entries[page_start + slot]
                valid = generation == getattr(self, '_series_poster_generation', self._series_generation) and str(current.stream_id) == str(stream_id)
                if not valid:
                    return
                if success:
                    displayed[key] = signature
                    self._vod_poster_retries.succeeded(stream_id)
                    self._arm_vod_poster_retries()
                    _set_component_visible(self['series_tile_poster_{}'.format(slot)], True)
                    _set_component_visible(self['series_tile_placeholder_{}'.format(slot)], False)
                else:
                    requests.pop(key, None)
                    if not was_visible:
                        _set_component_visible(self['series_tile_poster_{}'.format(slot)], False)
                    self._retry_vod_poster_after_decode('series', slot, current, path)
            except Exception:
                pass
        try:
            attach_pixmap(self, 'series_tile_poster_{}'.format(slot), path, key='series-tile-{}'.format(slot), on_loaded=loaded)
        except Exception as error:
            log_event('artwork', 'stage=decode outcome=start-failed', error)
            loaded(False)

    def _start_series_detail(self):
        if getattr(self, '_poster_grid_enabled', False):
            self._start_grid_detail('series')
            return
        if not self._series_entries:
            return
        item = self._series_entries[self._series_selected_index]
        generation = self._series_generation
        _cancel_worker_token(self._series_detail_token)
        token = _WorkerToken(WORKER_DETAIL_TIMEOUT_SECONDS)
        self._series_detail_token = token
        if getattr(self, '_cinematic_enabled', False):
            info = _detail_cache_get(self._series_info_cache, self._series_info_cache_lock, item.stream_id)
            self._start_cinematic_backdrop('series', item, info)
        pool = getattr(self, '_grid_detail_workers', None)
        if pool is None:
            pool = self._grid_detail_workers = LatestPosterWorkers(worker_count=2)
            self.onClose.append(pool.close)
        self._series_detail_pending = True
        pool.replace([lambda: self._load_series_detail_worker(generation, item, token)])
        self._start_deferred_series_posters()
        self._series_timer.start(80, True)



    def _load_series_detail_worker(self, generation, item, token=None):
        if token is not None:
            token.check()
        catalogue_poster_url = _safe_picon_url(getattr(item, 'icon', ''))
        info = _detail_cache_get(self._series_info_cache, self._series_info_cache_lock, item.stream_id) if hasattr(self, '_series_info_cache_lock') else self._series_info_cache.get(item.stream_id)
        metadata_status = ''
        early_info = catalogue_info(self.client, 'series', item) if info is None else None
        if info is None and early_info is not None:
            # Episodes are fetched only when entering the selected series.
            info = early_info
        if info is None:
            localized = self._catalogue_detail_preview('series', generation, item, token)
            if localized is not None:
                info = localized
                early_info = localized
        if early_info is not None and generation == self._series_generation and (token is None or token.active()):
            preview_url = _safe_picon_url(getattr(early_info, 'cover', '') or getattr(item, 'icon', ''))
            self._series_detail_preview = (generation, early_info, _cached_picon_path(preview_url) if preview_url else '', '', preview_url, catalogue_poster_url)
        loader = getattr(self.client, 'load_series_summary', None)
        if loader is None:
            loader = getattr(self.client, 'load_series_info', None)
        if info is None and loader is not None:
            try:
                info = _bounded_client_call(self.client, token, lambda: loader(item.stream_id))
                if hasattr(self, '_series_info_cache_lock'):
                    _detail_cache_put(self._series_info_cache, self._series_info_cache_lock, item.stream_id, info)
                else:
                    self._series_info_cache[item.stream_id] = info
            except Exception as error:
                _debug('series detail failed id={} error={}'.format(_debug_identifier(item.stream_id), error.__class__.__name__))
                info = None
        if token is not None:
            token.check()
        if early_info is None and info is not None and (generation == self._series_generation) and (token is None or token.active()):
            preview_url = _safe_picon_url(getattr(info, 'cover', '') or getattr(item, 'icon', ''))
            self._series_detail_preview = (generation, info, _cached_picon_path(preview_url) if preview_url else '', '', preview_url, catalogue_poster_url)
        try:
            completer = None
            if self._cinematic_enabled:
                completer = getattr(self._metadata_client, 'complete_series_cinematic', None)
            if not callable(completer):
                completer = self._metadata_client.complete_series
            info = _bounded_client_call(self._metadata_client, token, lambda: complete_media_for_device_language(self._metadata_client, completer, item, info))
            if hasattr(self, '_series_info_cache_lock'):
                _detail_cache_put(self._series_info_cache, self._series_info_cache_lock, item.stream_id, info)
            else:
                self._series_info_cache[item.stream_id] = info
            metadata_status = str(getattr(self._metadata_client, 'last_error', '') or '')
        except MetadataError as error:
            metadata_status = str(error)
            _debug('TMDb series detail fallback failed id={} error={}'.format(_debug_identifier(item.stream_id), error.__class__.__name__))
        poster_url = _safe_picon_url(getattr(info, 'cover', '') or getattr(item, 'icon', ''))
        poster_path = _cached_picon_path(poster_url) if poster_url else ''
        if generation == self._series_generation and (token is None or token.active()):
            self._series_detail_result = (generation, info, poster_path, metadata_status, poster_url, catalogue_poster_url)



    def refresh_series(self):
        if self.content_type != 'series':
            self.start_load()
            return
        self._remember_series_selection()
        for name in ('_load_token', '_series_load_token', '_series_more_token', '_series_detail_token', '_series_poster_token'):
            _cancel_worker_token(getattr(self, name, None))
        self._loading = self._series_loading = self._series_more_loading = False
        self._series_generation += 1
        self._series_catalog_generation += 1
        self._series_poster_generation = getattr(self, '_series_poster_generation', 0) + 1
        self._series_more_result = self._series_detail_preview = self._series_detail_result = None
        self._series_poster_state = self._series_deferred_poster_jobs = None
        self._series_ready = self._series_detail_pending = self._series_poster_pending = False
        retry_entries = self._series_entries.cached_items() if getattr(self._series_entries, 'full_category', False) else self._series_entries
        browse_catalog = getattr(self, '_series_browser_catalog', None)
        if browse_catalog is not None:
            browse_catalog.invalidate()
            self._series_index_reload_when_done = False
        for current in retry_entries:
            url = _safe_picon_url(getattr(current, 'icon', ''))
            if url:
                _clear_picon_failure(url)
        invalidator = getattr(self.client, 'invalidate_cache', None)
        if invalidator is not None:
            try:
                category_id = self._entries[self.selected_index].category_id if self._entries else None
                invalidator('series', category_id)
            except Exception:
                pass
        self._series_poster_page_key = None
        if self._series_focus == 'categories':
            self.start_load()
        else:
            self._start_series_load()



    def _series_move_up(self):
        if getattr(self, '_poster_grid_enabled', False) and self.content_type == 'series' and (self._series_focus == 'series'):
            self._move_vod_grid('series', 'up')
            return
        if self._series_focus == 'categories':
            self._select_series_category(self.selected_index - 1)
            return
        if getattr(self, '_cinematic_enabled', False):
            self._series_focus = 'categories'
            self._refresh_series_categories()
            self._render_series_tiles()
            return
        target = self._series_selected_index - SERIES_GRID_COLUMNS
        if target >= 0:
            self._select_series(target)

    def _series_move_down(self):
        if getattr(self, '_poster_grid_enabled', False) and self.content_type == 'series' and (self._series_focus == 'series'):
            self._move_vod_grid('series', 'down')
            return
        if self._series_focus == 'categories':
            self._select_series_category(self.selected_index + 1)
            return
        if getattr(self, '_cinematic_enabled', False):
            return
        target = self._series_selected_index + SERIES_GRID_COLUMNS
        if target < len(self._series_entries) or self._series_has_more:
            self._series_pending_clamp_to_end = False
            self._select_series(target)

    def _series_move_left(self):
        if getattr(self, '_poster_grid_enabled', False) and self.content_type == 'series' and (self._series_focus == 'series'):
            self._move_vod_grid('series', 'left')
            return
        if self._series_focus == 'categories':
            self._select_series_category(self.selected_index - self.page_size)
            return
        if getattr(self, '_cinematic_enabled', False):
            if self._series_selected_index == 0 and (not self._vod_window_base('series')):
                self._series_focus = 'categories'
                self._refresh_series_categories()
                self._render_series_tiles()
            else:
                self._select_series(self._series_selected_index - 1)
            return
        if self._series_selected_index % SERIES_GRID_COLUMNS == 0:
            self._series_focus = 'categories'
            self._refresh_series_categories()
            self._render_series_tiles()
            return
        self._select_series(self._series_selected_index - 1)

    def _series_move_right(self):
        if getattr(self, '_poster_grid_enabled', False) and self.content_type == 'series' and (self._series_focus == 'series'):
            self._move_vod_grid('series', 'right')
            return
        if self._series_focus == 'categories':
            category = self._entries[self.selected_index] if self._entries else None
            if category is not None and str(self._series_loaded_category_id or '') == str(getattr(category, 'category_id', '') or '') and self._series_entries:
                self._series_focus = 'series'
                self._schedule_series_detail()
                self._refresh_series_categories()
                self._render_series_tiles()
            elif category is not None:
                self._start_series_load(category)
            return
        if getattr(self, '_cinematic_enabled', False):
            target = self._series_selected_index + 1
            if target < len(self._series_entries) or self._series_has_more:
                self._series_pending_clamp_to_end = False
                self._select_series(target)
            return
        if self._series_selected_index % SERIES_GRID_COLUMNS == SERIES_GRID_COLUMNS - 1:
            return
        target = self._series_selected_index + 1
        if target < len(self._series_entries) or self._series_has_more:
            self._series_pending_clamp_to_end = False
            self._select_series(target)

    def _series_page(self, direction):
        """Page the currently focused series pane and keep its focus."""
        step = -1 if int(direction) < 0 else 1
        if self._series_focus == 'categories':
            if not self._entries:
                return
            target = self.selected_index + step * self.page_size
            target = max(0, min(target, len(self._entries) - 1))
            if target != self.selected_index:
                self._select_series_category(target)
            return
        if not self._series_entries:
            return
        page_size = getattr(self, '_poster_page_size', CINEMATIC_POSTER_COLUMNS if getattr(self, '_cinematic_enabled', False) else SERIES_RAIL_PAGE_SIZE)
        target = self._series_selected_index + step * page_size
        if step < 0:
            target = max(-self._vod_window_base('series'), target)
        elif not self._series_has_more:
            target = min(target, len(self._series_entries) - 1)
        if target != self._series_selected_index:
            self._series_pending_clamp_to_end = step > 0
            self._select_series(target)

    def _open_series_child(self, screen, *args, **kwargs):
        if self._series_child_open:
            self['message'].setText(_('Please wait'))
            return None
        self._remember_series_selection()
        self._series_child_open = True
        self._suspend_for_playback(keep_catalog=screen is GTAllSeriesSearchScreen)
        try:
            opener = getattr(self.session, 'openWithCallback', None)
            if callable(opener):
                return opener(self._series_child_closed, screen, *args, **kwargs)
            dialog = self.session.open(screen, *args, **kwargs)
            on_close = getattr(dialog, 'onClose', None)
            if isinstance(on_close, list):
                on_close.append(self._series_child_closed)
            return dialog
        except Exception as error:
            log_event('series', 'Could not open the series child screen', error=error)
            self._series_child_closed()
            self['message'].setText(_('Could not open the content list.'))
            return None

    def _series_child_closed(self, *args):
        if self._closed:
            return
        self._series_child_open = False
        self._reload_favorite_keys()
        self._series_poster_page_key = None
        self._refresh_series_categories()
        category_is_current = self._selected_series_category_is_loaded()
        if not category_is_current:
            self._clear_series_tiles()
            return
        browse_catalog = getattr(self, '_series_browser_catalog', None)
        if browse_catalog is not None:
            browse_catalog.start()
            self._series_timer.start(120, True)
        if self._series_loading:
            self._series_timer.start(120, True)
        if self._series_entries:
            self._schedule_series_detail()
        self._render_series_tiles()
        if self._series_entries:
            self._schedule_series_scan()

    def download_selected(self):
        if self.content_type == 'series':
            self.open_series_episode_list(download_mode=True)
            return
        if self.content_type != 'movie' or self._movie_loading or (not self._movie_entries):
            return
        from .downloads_ui import request_download
        request_download(self.session, self.client, self._movie_entries[self._movie_selected_index])

    def open_series_episode_list(self, download_mode=False):
        if self.content_type != 'series' or self._series_loading or (not self._series_entries):
            return
        series = self._series_entries[self._series_selected_index]
        episode_client = self.client if _is_stalker_client(self.client) else content_client_for(self.account)
        share = getattr(self.client, 'share_series_responses', None)
        if callable(share):
            share(episode_client)
        from .browser_episodes import GTEpisodeListScreen
        self._open_series_child(GTEpisodeListScreen, self.account, series, episode_client, favorite_store=self.favorite_store, download_mode=download_mode, series_info=self._series_info if self._series_info_stream_id == str(series.stream_id) else None)

    def open_series_continue(self):
        if self.content_type != 'series':
            return
        if self._loading or self._series_loading or self._series_pending_category_id is not None or (self._entries and (not self._selected_series_category_is_loaded())):
            self['message'].setText(_('Please wait'))
            return
        continue_client = self.client if _is_stalker_client(self.client) else content_client_for(self.account)
        from .browser_history import GTContinueWatchingScreen
        self._open_series_child(GTContinueWatchingScreen, self.account, continue_client, grid_subtitle=N_('Series'), content_type_filter='series')

    def open_series_search(self):
        if self.content_type != 'series' or self._closed:
            return
        if getattr(self, '_series_search_keyboard_open', False) or getattr(self, '_series_search_child_open', False) or self._series_child_open:
            return
        try:
            from Screens.VirtualKeyBoard import VirtualKeyBoard
        except ImportError:
            self['message'].setText(_('The virtual keyboard is unavailable on this image.'))
            return
        opener = getattr(self.session, 'openWithCallback', None)
        if not callable(opener):
            self['message'].setText(_('Could not open the virtual keyboard.'))
            return
        self._series_search_keyboard_open = True
        try:
            opener(self._series_search_entered, VirtualKeyBoard, title=_('Search All Series'), text=self._series_search_query)
        except Exception:
            self._series_search_keyboard_open = False
            self['message'].setText(_('Could not open the virtual keyboard.'))

    def _series_search_entered(self, value):
        self._series_search_keyboard_open = False
        if self._closed or value is None:
            return
        self._series_search_query = ' '.join(str(value or '').split())[:120]
        query = _movie_search_text(self._series_search_query)
        if not query:
            return
        if callable(getattr(self.client, 'iter_series_catalog_items', None)):
            if getattr(self, '_series_search_child_open', False):
                return
            shared_catalog = None
            try:
                catalog = getattr(self, '_series_browser_catalog', None)
                shared_catalog = catalog.borrow_search() if catalog is not None else None
                search_client = shared_catalog.client if shared_catalog is not None else self.client if _is_stalker_client(self.client) else content_client_for(self.account)
            except Exception:
                if shared_catalog is not None:
                    shared_catalog.close()
                self['message'].setText(_('Could not open the content list.'))
                return
            self._series_search_child_open = True
            try:
                self._pause_series_for_search()
                self._series_search_child = self.session.openWithCallback(self._series_search_closed, GTAllSeriesSearchScreen, self.account, search_client, self.favorite_store, self._hidden_category_ids, initial_query=self._series_search_query, shared_catalog=shared_catalog)
            except Exception:
                if shared_catalog is not None:
                    shared_catalog.close()
                self._series_search_closed()
                self['message'].setText(_('Could not open the content list.'))
            return
        terms = query.split()
        matches = []
        for index, item in enumerate(self._series_entries):
            if all((term in _movie_search_text(getattr(item, 'name', '')) for term in terms)):
                matches.append((item.name, index))
                if len(matches) >= SERIES_SEARCH_RESULT_LIMIT:
                    break
        if not matches:
            self['message'].setText(_('No content found.'))
            return
        if len(matches) == 1:
            self._select_series(matches[0][1])
            return
        try:
            from Screens.ChoiceBox import ChoiceBox
        except ImportError:
            self._select_series(matches[0][1])
            return
        opener = getattr(self.session, 'openWithCallback', None)
        if not callable(opener):
            self._select_series(matches[0][1])
            return
        opener(self._series_search_choice, ChoiceBox, title=_('Search'), list=matches)

    def _series_search_choice(self, choice):
        if not choice:
            return
        try:
            index = choice[1]
        except (IndexError, KeyError, TypeError):
            return
        try:
            index = int(index)
        except (TypeError, ValueError, OverflowError):
            return
        if 0 <= index < len(self._series_entries):
            self._select_series(index)

    def _pause_series_for_search(self):
        content_sort_ui.cancel_loading(self, 'series')
        self._reset_vod_poster_retries()
        self._remember_series_selection()
        catalog = getattr(self, '_series_browser_catalog', None)
        if catalog is not None:
            catalog.stop()
        self._series_search_resume_categories = bool(self._loading)
        if self._loading:
            self._load_generation += 1
            _cancel_worker_token(self._load_token)
            self._load_token = None
            self._loading = False
        self._series_generation += 1
        self._series_catalog_generation += 1
        self._series_poster_generation += 1
        for name in ('_series_load_token', '_series_more_token', '_series_detail_token', '_series_poster_token'):
            _cancel_worker_token(getattr(self, name, None))
            setattr(self, name, None)
        self._series_loading = self._series_more_loading = False
        self._series_detail_pending = self._series_poster_pending = False
        self._series_more_result = self._series_detail_preview = self._series_detail_result = None
        self._series_poster_state = self._series_deferred_poster_jobs = None
        self._series_pending_category_id = self._series_pending_detail_generation = None
        for name in ('_timer', '_series_timer', '_series_scan_timer', '_series_category_timer', '_series_detail_timer'):
            timer = getattr(self, name, None)
            if timer is not None:
                try:
                    timer.stop()
                except Exception:
                    pass

    def _series_search_closed(self, *args):
        if self._closed or not self._series_search_child_open:
            return
        child = self._series_search_child
        self._series_search_child = None
        self._series_search_child_open = False
        self._series_search_drain_event = getattr(child, '_provider_workers_idle', None)
        self._resume_series_after_search()

    def _resume_series_after_search(self):
        if self._closed:
            self._series_search_drain_event = None
            return
        event = self._series_search_drain_event
        if self._series_search_child_open or (event is not None and (not event.is_set())):
            self._series_search_resume_timer.start(100, True)
            return
        self._series_search_drain_event = None
        self._reload_favorite_keys()
        self._series_poster_page_key = None
        if self._series_search_resume_categories:
            self._series_search_resume_categories = False
            self._resume_category_list_after_search()
        elif self._entries:
            self._start_series_load()

    def _stop_series(self):
        content_sort_ui.cancel_loading(self, 'series')
        preparation = getattr(self, '_vod_preparation', None)
        if preparation is not None:
            preparation.invalidate()
        self._reset_vod_poster_retries()
        pool = getattr(self, '_grid_detail_workers', None)
        if pool is not None:
            pool.close()
        timer = getattr(self, '_series_search_resume_timer', None)
        if timer is not None:
            timer.stop()
        self._series_search_drain_event = None
        self._series_search_keyboard_open = self._series_search_child_open = False
        browse_catalog = getattr(self, '_series_browser_catalog', None)
        if browse_catalog is not None:
            browse_catalog.close()
        reset_cinematic = getattr(self, '_reset_cinematic_backdrop', None)
        if callable(reset_cinematic):
            reset_cinematic()
        try:
            plot_widget = self['series_plot']
        except (KeyError, TypeError):
            plot_widget = None
        stop_scroll = getattr(plot_widget, 'stop_scroll', None)
        if callable(stop_scroll):
            stop_scroll()
        self._series_generation += 1
        self._series_catalog_generation += 1
        self._series_poster_generation = getattr(self, '_series_poster_generation', self._series_generation) + 1
        self._series_child_open = False
        self._series_more_loading = False
        self._series_more_result = None
        self._series_pending_advance = False
        self._series_pending_selection_index = None
        self._series_pending_clamp_to_end = False
        for token in (self._series_load_token, self._series_more_token, self._series_detail_token, self._series_poster_token):
            _cancel_worker_token(token)
        self._series_load_token = None
        self._series_more_token = None
        self._series_detail_token = None
        self._series_poster_token = None
        self._series_poster_state = None
        self._series_deferred_poster_jobs = None
        series_cache_lock = getattr(self, '_series_info_cache_lock', None)
        if series_cache_lock is None:
            self._series_info_cache.clear()
        else:
            with series_cache_lock:
                self._series_info_cache.clear()
        for timer in (self._series_timer, self._series_scan_timer, self._series_category_timer, self._series_detail_timer):
            try:
                timer.stop()
            except Exception:
                pass

def install_vod_methods(target):
    if target.__dict__.get('_vod_methods_installed', False):
        return
    from .vod_light import LightVODMethods
    for cls in (DiscoveryVODMethods, VODBrowserMethods, LightVODMethods):
        for name, method in vars(cls).items():
            if not name.startswith('__'):
                setattr(target, name, method)
    target._vod_methods_installed = True

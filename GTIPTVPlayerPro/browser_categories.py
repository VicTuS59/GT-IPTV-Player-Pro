# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""categories; loaded when its screen is needed."""

import datetime
import os
import threading
from .typography import set_text_if_changed
from .ui_update import set_visible
from collections import OrderedDict
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from Screens.MessageBox import MessageBox
from enigma import ePoint, eSize, eTimer, getDesktop, iServiceInformation
from .broadcast_presentation import refresh_after as broadcast_refresh_after, setup as setup_broadcast
from .broadcast_presentation import refresh_category_list, refresh_category_weather
from . import sorting_ui as content_sort_ui
from .content import CONTENT_LABELS
from .i18n import N_, _, localized_upper, metadata_language
from .live_history import clear_live_channel_history
from .category_preferences import CategoryPreferences, LiveCategoryPreferenceStore, arrange_categories, category_preferences_path
from .category_visibility import category_filter_active, category_visibility_ids
from .category_lock import CategoryEditLockStore
from .category_lock_ui import GTCategoryCodeInputScreen, RECOVERY_REQUEST
from .metadata import TMDbMetadataClient
from .category_browse import CategoryBrowseCatalog
from .category_titles import load_category_titles
from .vod_grid import CINEMATIC_POSTER_COLUMNS, GRID_PAGE_SIZE, PosterRetryQueue
from .playback import resume_account_scope
from .remote_footer import prepared_skin
from .scrollbar import hide_scrollbar, update_scrollbar
from .settings import load_player_settings
from .typography import current_text_size, ellipsize_dynamic_text, set_text_if_changed
from .weather import WeatherError, day_name, load_weather_settings, shared_weather_service, weather_description, weather_icon_name
from .browser_common import GTAsyncListScreen, LIVE_CATEGORY_FOOTER_ITEMS, M3U_LIVE_CATEGORY_FOOTER_ITEMS, MOVIE_RAIL_PAGE_SIZE, WEATHER_ICON_DIR, WORKER_WEATHER_TIMEOUT_SECONDS, _FavoriteSupport, _WorkerCancelled, _WorkerToken, _cancel_worker_token, _client_supports, _connect_timer, _is_m3u_client, _scale, _validated_content_type, content_client_for

LIVE_PREVIEW_RETURN_RESTORE_MS = 160

class GTContentBrowserScreen(_FavoriteSupport, GTAsyncListScreen):

    def __init__(self, session, account, content_type, client=None, weather_settings_loader=None, weather_service=None, metadata_client=None, player_settings_loader=None, favorite_store=None, category_store=None, category_lock_store=None):
        self.account = account
        self.client = client or content_client_for(account)
        self.content_type = _validated_content_type(self.client, content_type)
        if self.content_type in ('movie', 'series'):
            from .browser_vod import install_vod_methods
            install_vod_methods(type(self))
        if self.content_type == 'live':
            self._clear_live_recall_history()
        self._footer_layout_stacked = self.content_type in ('movie', 'series')
        self._footer_content_y_offset = 14 if self.content_type in ('movie', 'series') else 0
        self._init_favorite_support(favorite_store)
        source_type = str(getattr(self.client, 'source_type', 'xtream') or 'xtream').strip().lower()
        self._category_preferences_enabled = bool(self.content_type in ('live', 'movie', 'series') and source_type in ('xtream', 'stalker', 'm3u'))
        self._live_category_preferences_enabled = self._category_preferences_enabled
        self.category_store = category_store if category_store is not None else LiveCategoryPreferenceStore(category_preferences_path(self.content_type))
        self._hidden_category_ids = frozenset()
        self.category_lock_store = category_lock_store if category_lock_store is not None else CategoryEditLockStore()
        self._all_categories = []
        self._category_preferences_generation = -1
        self._live_all_categories = self._all_categories
        self._live_preferences_generation = self._category_preferences_generation
        self._category_manager_return_id = ''
        self._category_lock_prompt_open = False
        self._category_manager_child_open = False
        try:
            browser_settings = (player_settings_loader or load_player_settings)()
        except Exception:
            browser_settings = None
        if isinstance(browser_settings, dict):
            metadata_api_key = browser_settings.get('tmdb_api_key', '')
            metadata_enabled = browser_settings.get('metadata_enabled', True)
        else:
            metadata_api_key = getattr(browser_settings, 'tmdb_api_key', '')
            metadata_enabled = getattr(browser_settings, 'metadata_enabled', True)
        if isinstance(browser_settings, dict):
            self._light_palette=(browser_settings.get('channel_highlight','navy'),browser_settings.get('selection_border','navy'))
        else:
            self._light_palette=(getattr(browser_settings,'channel_highlight','navy'),getattr(browser_settings,'selection_border','navy'))
        self._light_vod_enabled = self.content_type in ('movie', 'series')
        self._cinematic_enabled = False
        self._vod_discovery_layout = False
        self._cinematic_composite_plan = None
        self._cinematic_composite_visible = False
        self._poster_grid_enabled = False
        self._poster_page_size = GRID_PAGE_SIZE if self._poster_grid_enabled else 6 if self._vod_discovery_layout else CINEMATIC_POSTER_COLUMNS
        self._poster_slot_count = self._poster_page_size
        self._vod_poster_retries = PosterRetryQueue()
        self._vod_poster_retry_timer = None
        self._vod_decode_failures = {}
        self._vod_decode_failure_lock = threading.Lock()
        self._vod_poster_url_overrides = OrderedDict()
        self._vod_poster_info_cache = OrderedDict()
        self._vod_poster_info_lock = threading.Lock()
        self._cinematic_generation = 0
        self._cinematic_pending = False
        self._cinematic_result = None
        self._cinematic_token = None
        self._cinematic_lookup_pending = False
        self._cinematic_surface_prepared = None
        self._cinematic_request_signature = None
        self._cinematic_applied_path = ''
        self._cinematic_decode_request = None
        self._hero_start_timer = None
        self._hero_start_request = None
        if metadata_client is not None:
            self._metadata_client = metadata_client
        else:
            self._metadata_client = TMDbMetadataClient(api_key=metadata_api_key, enabled=metadata_enabled, language=metadata_language())
        self._weather_settings_loader = weather_settings_loader or load_weather_settings
        self._weather_service = weather_service or shared_weather_service()
        self._weather_forecast = None
        self._weather_error = ''
        self._weather_ready = False
        self._weather_loading = False
        self._weather_generation = 0
        self._weather_token = None
        self._weather_started = False
        self._weather_suspended = False
        self._weather_timer = eTimer()
        _connect_timer(self._weather_timer, self._poll_weather)
        if self.content_type == 'live':
            self._returned_preview = None
            self._returned_preview_timer = eTimer()
            _connect_timer(self._returned_preview_timer, self._restore_returned_preview)
            self._footer_layout_items = M3U_LIVE_CATEGORY_FOOTER_ITEMS if _client_supports(self.client, 'search') and _is_m3u_client(self.client) else LIVE_CATEGORY_FOOTER_ITEMS
            self._screen_background_path = ''
            self._opaque_background = True
            self._footer_show_web_hint = False
            self._footer_show_dividers = False
            self._footer_transparent_panel = True
            from .browser_live import _live_category_skin
            self.skin = prepared_skin(_live_category_skin)
            self.page_size = 8
            width, height, px = _scale()
            self._list_scroll_geometry = (px(50 + 945 - 15), px(145), px(10), px(716))
        elif self.content_type == 'movie':
            from .vod_light import configure
            configure(self)
            self._movie_catalog_entries = []
            self._movie_entries = []
            self._movie_browser_catalog = CategoryBrowseCatalog(self.client, native=True) if source_type in ('xtream', 'stalker') else None
            self._movie_browse_cursor = 0
            self._movie_index_reload_when_done = False
            self._movie_selected_index = 0
            self._movie_focus = 'categories'
            self._movie_loading = False
            self._movie_ready = False
            self._movie_error = ''
            self._movie_generation = 0
            self._movie_catalog_generation = 0
            self._movie_loaded_category_id = None
            self._movie_category_load_started = False
            self._movie_category_list_generation = -1
            self._movie_detail_preview = None
            self._movie_detail_result = None
            self._movie_poster_state = None
            self._movie_detail_pending = False
            self._movie_poster_pending = False
            self._movie_pending_category_id = None
            self._movie_pending_detail_generation = None
            self._movie_poster_page_key = None
            self._movie_has_more = False
            self._movie_more_loading = False
            self._movie_more_result = None
            self._movie_pending_advance = False
            self._movie_pending_selection_index = None
            self._movie_load_token = None
            self._movie_more_token = None
            self._movie_detail_token = None
            self._movie_poster_token = None
            self._movie_deferred_poster_jobs = None
            self._movie_info_cache = OrderedDict()
            self._movie_info_cache_lock = threading.Lock()
            self._movie_poster_generation = 0
            self._movie_search_child_open = False
            self._movie_search_child = None
            self._movie_search_keyboard_open = False
            self._movie_search_query = ''
            self._movie_search_drain_event = None
            self._movie_search_resume_categories = False
            self._movie_search_resume_category_id = None
            self._movie_search_resume_more = False
            self._movie_search_resume_detail = False
            self._movie_search_resume_posters = False
            self._movie_timer = eTimer()
            _connect_timer(self._movie_timer, self._poll_movie_result)
            self._movie_category_timer = eTimer()
            _connect_timer(self._movie_category_timer, self._commit_movie_category_load)
            self._movie_detail_timer = eTimer()
            _connect_timer(self._movie_detail_timer, self._commit_movie_detail)
            self._movie_search_resume_timer = eTimer()
            _connect_timer(self._movie_search_resume_timer, self._poll_movie_search_resume)
        elif self.content_type == 'series':
            from .vod_light import configure
            configure(self)
            self._series_entries = []
            self._series_browser_catalog = CategoryBrowseCatalog(self.client, content_type='series', native=True) if source_type in ('xtream', 'stalker') else None
            self._series_browse_cursor = 0
            self._series_index_reload_when_done = False
            self._series_selected_index = 0
            self._series_focus = 'categories'
            self._series_selection_by_category = {}
            self._series_loading = False
            self._series_ready = False
            self._series_error = ''
            self._series_generation = 0
            self._series_catalog_generation = 0
            self._series_loaded_category_id = None
            self._series_category_load_started = False
            self._series_detail_preview = None
            self._series_detail_result = None
            self._series_poster_state = None
            self._series_detail_pending = False
            self._series_poster_pending = False
            self._series_pending_category_id = None
            self._series_pending_detail_generation = None
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
            self._series_info_cache = OrderedDict()
            self._series_info_cache_lock = threading.Lock()
            self._series_poster_generation = 0
            self._series_info = None
            self._series_info_stream_id = ''
            self._series_child_open = False
            self._series_search_query = ''
            self._series_search_keyboard_open = False
            self._series_search_child_open = False
            self._series_search_child = None
            self._series_search_drain_event = None
            self._series_search_resume_categories = False
            self._series_search_resume_timer = eTimer()
            _connect_timer(self._series_search_resume_timer, self._resume_series_after_search)
            self._series_timer = eTimer()
            _connect_timer(self._series_timer, self._poll_series_result)
            self._series_scan_timer = eTimer()
            _connect_timer(self._series_scan_timer, self._resume_series_scan)
            self._series_category_timer = eTimer()
            _connect_timer(self._series_category_timer, self._commit_series_category_load)
            self._series_detail_timer = eTimer()
            _connect_timer(self._series_detail_timer, self._commit_series_detail)
        if self._vod_discovery_layout:
            from .vod_discovery import CATEGORY_ROWS, footer, place_trailer_hint, scroll_geometry
            self.page_size = CATEGORY_ROWS
            width, height, px = _scale()
            self._list_scroll_geometry, self._poster_scroll_geometry = scroll_geometry(width, height, px)
            self.skin = place_trailer_hint(self.skin, px)
            self._screen_background_path = ''
            self._opaque_background = True
            self._footer_layout_items = footer(self.content_type)
            self._footer_layout_stacked = False
            self._footer_content_y_offset = 0
            self._footer_show_web_hint = False
        title = CONTENT_LABELS[self.content_type][0]
        GTAsyncListScreen.__init__(self, session, 'GTContentBrowserScreen', title, _('{}  |  Select a category').format(account.display_name), frameless_footer=self.content_type in ('movie', 'series'))
        if self.content_type == 'live':
            self['category_footer_art'] = Pixmap()
            self['server'] = Label('')
            self._dynamic_server_widget = 'server'
            self._dynamic_server_text = account.host or account.display_name
            ellipsize_dynamic_text(self['server'], self._dynamic_server_text, fallback_chars=55)
            self['clock'] = Label(datetime.datetime.now().strftime('%H:%M'))
            self['left_panel'] = Label('')
            self['right_panel'] = Label('')
            self['left_panel_art'] = Pixmap()
            self['right_panel_art'] = Pixmap()
            for panel in ('left', 'right'):
                for color in ('cyan', 'magenta'):
                    self['{}_panel_accent_{}'.format(panel, color)] = Label('')
            self['categories_caption'] = Label(_('LIVE TV CATEGORIES'))
            for index in range(8):
                self['category_row_art_{}'.format(index)] = Pixmap()
                self['category_row_art_{}'.format(index)].hide()
                self['category_focus_art_{}'.format(index)] = Pixmap()
                self['category_focus_art_{}'.format(index)].hide()
                self['category_icon_{}'.format(index)] = Pixmap()
                self['category_icon_{}'.format(index)].hide()
                self['category_row_{}'.format(index)] = Label('')
                for edge in ('fill', 'top', 'bottom', 'left', 'right'):
                    self['category_focus_{}_{}'.format(index, edge)] = Label('')
            self['weather_caption'] = Label(_('5-DAY WEATHER FORECAST'))
            self['weather_header_divider'] = Label('')
            self['weather_forecast_divider'] = Label('')
            self['weather_city'] = Label('')
            self['weather_today_card'] = Label('')
            self['weather_current_icon'] = Pixmap()
            self['weather_today_label'] = Label(_('TODAY'))
            self['weather_temp'] = Label('--°')
            self['weather_desc'] = Label(_('Loading weather data'))
            self['weather_metrics'] = Label('')
            self['weather_updated'] = Label('')
            for index in range(5):
                if index:
                    self['weather_day_separator_{}'.format(index)] = Label('')
                self['weather_day_card_{}'.format(index)] = Label('')
                self['weather_day_{}'.format(index)] = Label('--')
                self['weather_day_icon_{}'.format(index)] = Pixmap()
                self['weather_day_high_{}'.format(index)] = Label('--°')
                self['weather_day_low_{}'.format(index)] = Label('--°')
            self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions', 'ColorActions', 'MenuActions'], {'ok': self.open_selected, 'cancel': self.close, 'up': self.move_up, 'down': self.move_down, 'upRepeated': self.move_up, 'downRepeated': self.move_down, 'left': self.page_up, 'right': self.page_down, 'leftRepeated': self.page_up, 'rightRepeated': self.page_down, 'green': self.refresh_all, 'yellow': self.open_content_edit, 'blue': self.open_live_category_action, 'menu': self.open_weather_settings, 'red': self.close}, -1)
            self._render_weather_waiting()
            if hasattr(self, 'onShown'):
                self.onShown.append(self.start_weather)
            if hasattr(self, 'onClose'):
                self.onClose.append(self._restore_returned_preview)
                self.onClose.append(self._stop_weather)
                self.onClose.append(self._clear_live_recall_history)
        elif self.content_type == 'movie':
            self._setup_movie_widgets()
            from .movie_trailer_ui import MovieTrailerHint
            self._movie_trailer_hint = MovieTrailerHint(self)
        elif self.content_type == 'series':
            self._setup_series_widgets()
            from .series_trailer_ui import SeriesTrailerHint
            self._series_trailer_hint = SeriesTrailerHint(self)
        if hasattr(self, 'onShown'):
            self.onShown.append(self._favorite_browser_shown)
        if hasattr(self, 'onHide'):
            self.onHide.append(self._pause_vod_scroll)
        if hasattr(self, 'onLayoutFinish'):
            self.onLayoutFinish.append(self._refit_content_dynamic_layout)
            if self.content_type in ('movie', 'series'):
                self.onLayoutFinish.append(self._prepare_cinematic_shadows)
        if self.content_type == 'live': setup_broadcast(self, 'categories', self._footer_layout_items)

    def _refit_content_dynamic_layout(self):
        widget_name = getattr(self, '_dynamic_server_widget', '')
        if widget_name:
            ellipsize_dynamic_text(self[widget_name], getattr(self, '_dynamic_server_text', ''), fallback_chars=55 if self.content_type == 'live' else 80)
        if self.content_type == 'live' and hasattr(self, '_dynamic_weather_city_text'):
            ellipsize_dynamic_text(self['weather_city'], self._dynamic_weather_city_text, fallback_chars=20)

    def _pause_vod_scroll(self):
        if self.content_type in ('movie', 'series'):
            try:
                self[self.content_type + '_plot'].suspend_scroll()
            except (KeyError, AttributeError):
                pass

    def _favorite_browser_shown(self):
        if getattr(self, '_vod_work_suspended', False):
            self._vod_work_suspended = False
            kind = self.content_type
            catalog = getattr(self, '_{}_browser_catalog'.format(kind), None)
            if catalog is not None:
                catalog.start()
            if kind in ('movie', 'series'):
                if getattr(self, '_{}_has_more'.format(kind), False):
                    getattr(self, '_start_{}_more_load'.format(kind))()
                getattr(self, '_schedule_{}_detail'.format(kind))()
                getattr(self, '_render_movie_posters' if kind == 'movie' else '_render_series_tiles')()
        for name in ('movie_plot', 'series_plot'):
            try:
                self[name].resume_scroll()
            except (KeyError, AttributeError):
                pass
        if not self._favorite_screen_shown():
            return
        if self.content_type == 'movie' and self._movie_entries:
            self._render_movie_posters()
        elif self.content_type == 'series' and self._series_entries:
            self._render_series_tiles()

    def _selected_favorite_item(self):
        if self.content_type == 'movie':
            if self._movie_entries and (not self._movie_loading):
                return self._movie_entries[self._movie_selected_index]
            return None
        if self.content_type == 'series':
            if self._series_entries and (not self._series_loading):
                return self._series_entries[self._series_selected_index]
            return None
        return None

    def toggle_selected_favorite(self):
        item = self._selected_favorite_item()
        if item is None:
            self['message'].setText(_('Select content before using favorites.'))
            return
        added = self._toggle_favorite_item(item)
        if self.content_type == 'movie':
            self._render_movie_posters()
        elif self.content_type == 'series':
            self._render_series_tiles()
        ellipsize_dynamic_text(self['message'], self._favorite_message(item, added), fallback_chars=24)

    def load_items(self):
        return load_category_titles(self.client, self.content_type,
            force=getattr(self, '_category_titles_refresh', False))

    def _prepare_loaded_items(self, values):
        """Load and arrange category preferences away from the GUI thread."""
        raw_categories = list(values or [])
        if not self._category_preferences_enabled:
            return {'raw': raw_categories, 'entries': raw_categories, 'hidden': frozenset()}
        try:
            preferences = self.category_store.load(self.favorite_scope)
            entries = arrange_categories(raw_categories, preferences)
            hidden = category_visibility_ids(raw_categories, preferences)
        except Exception:
            entries = raw_categories
            hidden = frozenset()
        return {'raw': raw_categories, 'entries': entries, 'hidden': hidden}

    def _commit_loaded_items(self, prepared, generation):
        if not isinstance(prepared, dict):
            prepared = {}
        raw_categories = list(prepared.get('raw', ()))
        self._all_categories = raw_categories
        self._live_all_categories = raw_categories
        self._entries = list(prepared.get('entries', ()))
        self._hidden_category_ids = prepared.get('hidden', frozenset())
        self._category_preferences_generation = generation
        self._live_preferences_generation = generation

    def _poll_result(self):
        if self._category_preferences_enabled and self._ready and (not self._error) and (self._category_preferences_generation != self._load_generation):
            raw_categories = list(self._entries)
            self._all_categories = raw_categories
            self._live_all_categories = raw_categories
            try:
                preferences = self.category_store.load(self.favorite_scope)
                self._entries = arrange_categories(raw_categories, preferences)
                self._hidden_category_ids = category_visibility_ids(raw_categories, preferences)
            except Exception:
                self._entries = raw_categories
                self._hidden_category_ids = frozenset()
            self._category_preferences_generation = self._load_generation
            self._live_preferences_generation = self._load_generation
        GTAsyncListScreen._poll_result(self)

    def start_load(self):
        if getattr(self, '_light_vod_enabled', False) and not self._loading and not self._closed:
            prefix = '_' + self.content_type + '_'
            setattr(self, prefix + 'loaded_category_id', None)
            setattr(self, prefix + 'category_load_started', False)
            setattr(self, prefix + 'entries', [])
            setattr(self, prefix + 'focus', 'categories')
            self._light_render_rows()
            self._light_waiting()
        if self._category_preferences_enabled and (not self._loading) and (not self._closed):
            self._all_categories = []
            self._live_all_categories = self._all_categories
        GTAsyncListScreen.start_load(self)
        if getattr(self, '_broadcast_kind', None) == 'categories' and self._loading:
            refresh_category_list(self, full=True)

    def _client_has_more_items(self, content_type, category_id):
        checker = getattr(self.client, 'has_more_items', None)
        if not callable(checker):
            return False
        try:
            return bool(checker(content_type, category_id))
        except Exception:
            return False

    def empty_message(self):
        return N_('No category found.')

    def _refresh(self):
        if self.content_type == 'movie':
            self._refresh_movie_categories()
            return
        if self.content_type == 'series':
            self._refresh_series_categories()
            return
        if self.content_type != 'live':
            GTAsyncListScreen._refresh(self)
            return
        self._refresh_live_categories()

    @broadcast_refresh_after
    def _refresh_live_categories(self):
        if self._loading:
            return
        if self._error:
            set_text_if_changed(self['items'], _('Could not open the content list.'))
            ellipsize_dynamic_text(self['message'], _(self._error), fallback_chars=70)
            self._clear_live_rows()
            hide_scrollbar(self, 'list_scroll')
            return
        if not self._entries:
            set_text_if_changed(self['items'], _(self.empty_message()))
            set_text_if_changed(self['message'], _('No categories found; press GREEN to try again.'))
            self._clear_live_rows()
            hide_scrollbar(self, 'list_scroll')
            return
        self.selected_index %= len(self._entries)
        if getattr(self, '_broadcast_kind', None) == 'categories':
            return
        page_start = int(self.selected_index / self.page_size) * self.page_size
        page_items = self._entries[page_start:page_start + self.page_size]
        page_key = (page_start, current_text_size(), tuple(((str(getattr(item, 'category_id', '') or ''), item.name) for item in page_items)))
        page_changed = page_key != getattr(self, '_live_category_render_page_key', None)
        selected_row = self.selected_index - page_start
        previous_row = getattr(self, '_live_category_render_selected_row', -1)
        if page_changed:
            plain_lines = []
            for row_index in range(self.page_size):
                if row_index < len(page_items):
                    item = page_items[row_index]
                    absolute_index = page_start + row_index
                    rendered_row = ellipsize_dynamic_text(self['category_row_{}'.format(row_index)], '{:02d}   {}'.format(absolute_index + 1, item.name), fallback_chars=35)
                    plain_lines.append(rendered_row)
                    set_visible(self['category_row_art_{}'.format(row_index)], True)
                else:
                    set_text_if_changed(self['category_row_{}'.format(row_index)], '')
                    set_visible(self['category_row_art_{}'.format(row_index)], False)
            set_text_if_changed(self['items'], '\n'.join(plain_lines))
        affected_rows = range(self.page_size) if page_changed else (previous_row, selected_row) if selected_row != previous_row else ()
        for row_index in affected_rows:
            if not 0 <= row_index < self.page_size:
                continue
            selected = row_index == selected_row and row_index < len(page_items)
            for suffix in ('focus_art', 'icon'):
                component = self['category_{}_{}'.format(suffix, row_index)]
                if selected:
                    set_visible(component, True)
                else:
                    set_visible(component, False)
            for edge in ('fill', 'top', 'bottom', 'left', 'right'):
                focus = self['category_focus_{}_{}'.format(row_index, edge)]
                if selected:
                    set_visible(focus, True)
                else:
                    set_visible(focus, False)
        self._live_category_render_page_key = page_key
        self._live_category_render_selected_row = selected_row
        set_text_if_changed(self['message'], '{}: {} | {}/{}'.format(_('Categories'), len(self._entries), self.selected_index + 1, len(self._entries)))
        update_scrollbar(self, 'list_scroll', len(self._entries), self.selected_index, self.page_size, self._list_scroll_geometry)

    def _clear_live_rows(self):
        self._live_category_render_page_key = None
        self._live_category_render_selected_row = -1
        for row_index in range(8):
            set_text_if_changed(self['category_row_{}'.format(row_index)], '')
            for suffix in ('row_art', 'focus_art', 'icon'):
                set_visible(self['category_{}_{}'.format(suffix, row_index)], False)
            for edge in ('fill', 'top', 'bottom', 'left', 'right'):
                set_visible(self['category_focus_{}_{}'.format(row_index, edge)], False)

    def refresh_all(self):
        for name in ('_load_token', '_live_more_token'):
            _cancel_worker_token(getattr(self, name, None))
        self._loading = False
        self._category_titles_refresh = True
        invalidator = getattr(self.client, 'invalidate_cache', None)
        if invalidator is not None:
            try:
                invalidator(self.content_type)
            except Exception:
                pass
        self.start_load()
        self.start_weather(force=True)

    def open_content_edit(self):
        return content_sort_ui.open_edit(self)

    def open_live_category_action(self):
        if _is_m3u_client(self.client):
            self.open_channel_search()
            return
        return self.open_content_edit()

    def open_category_manager(self, action='edit'):
        if not self._category_preferences_enabled or self._category_manager_child_open or self._category_lock_prompt_open or self._loading or self._error or (not self._all_categories) or (not self.favorite_scope) or (self.content_type == 'movie' and self._movie_search_child_open) or (self.content_type == 'series' and self._series_child_open) or (not any((str(getattr(entry, 'category_id', '') or '').strip() for entry in self._all_categories))):
            return
        self._category_manager_action = 'sort' if action == 'sort' else 'edit'
        if self._entries:
            self._category_manager_return_id = str(getattr(self._entries[self.selected_index], 'category_id', '') or '').strip()
        else:
            self._category_manager_return_id = ''
        try:
            lock_status = self.category_lock_store.status()
        except Exception:
            lock_status = 'corrupt'
        if lock_status not in ('disabled', 'enabled'):
            message = _('Category edit lock data is damaged. Remove and reinstall the plugin to reset only this lock.')
            self['message'].setText(message)
            self.session.open(MessageBox, message, type=MessageBox.TYPE_INFO)
            return
        if lock_status == 'enabled':
            self._open_category_lock_code(self._category_manager_pin_entered, _('Category editor locked'), _('Enter the 4-digit category edit PIN.'), allow_recovery=True)
            return
        self._open_category_manager_unlocked()

    def _open_category_lock_code(self, callback, title, prompt, digits=4, allow_recovery=False):
        if self._category_lock_prompt_open:
            return False
        opener = getattr(self.session, 'openWithCallback', None)
        if not callable(opener):
            self['message'].setText(_('The PIN entry screen is unavailable on this image.'))
            return False
        self._category_lock_prompt_open = True

        def completed(value):
            self._category_lock_prompt_open = False
            callback(value)
        try:
            opener(completed, GTCategoryCodeInputScreen, title=title, prompt=prompt, digits=digits, allow_recovery=allow_recovery)
        except Exception:
            self._category_lock_prompt_open = False
            self['message'].setText(_('The PIN entry screen is unavailable on this image.'))
            return False
        return True

    def _category_manager_pin_entered(self, value):
        if value is None:
            return
        if value == RECOVERY_REQUEST:
            self._open_category_lock_code(self._category_manager_recovery_entered, _('Category lock recovery'), _('Enter the 12-digit recovery code.'), digits=12)
            return
        try:
            valid = self.category_lock_store.verify_pin(value)
        except Exception:
            valid = False
        if not valid:
            message = _('Incorrect PIN. Category editor remains locked.')
            self['message'].setText(message)
            self.session.open(MessageBox, message, type=MessageBox.TYPE_ERROR)
            return
        self._open_category_manager_unlocked()

    def _category_manager_recovery_entered(self, value):
        if value is None:
            return
        try:
            valid = self.category_lock_store.verify_recovery(value)
        except Exception:
            valid = False
        if not valid:
            message = _('Incorrect recovery code. Category editor remains locked.')
            self['message'].setText(message)
            self.session.open(MessageBox, message, type=MessageBox.TYPE_ERROR)
            return
        self._open_category_lock_code(self._category_recovery_pin_created, _('Create a new category edit PIN'), _('Enter a new 4-digit PIN.'))

    def _category_recovery_pin_created(self, pin):
        if pin is None:
            return
        self._pending_recovery_pin = str(pin)
        opened = self._open_category_lock_code(self._category_recovery_pin_confirmed, _('Confirm the new category edit PIN'), _('Enter the same 4-digit PIN again.'))
        if not opened:
            self._pending_recovery_pin = ''

    def _category_recovery_pin_confirmed(self, confirmation):
        pin = getattr(self, '_pending_recovery_pin', '')
        self._pending_recovery_pin = ''
        if confirmation is None:
            return
        if str(confirmation) != pin:
            message = _('The PIN entries do not match. Nothing was changed.')
            self['message'].setText(message)
            self.session.open(MessageBox, message, type=MessageBox.TYPE_ERROR)
            return
        try:
            recovery_code = self.category_lock_store.configure(pin, confirmation)
        except Exception:
            recovery_code = ''
        if not recovery_code:
            message = _('The new PIN could not be saved. Category editor remains locked.')
            self['message'].setText(message)
            self.session.open(MessageBox, message, type=MessageBox.TYPE_ERROR)
            return
        message = _('Recovery succeeded. Write down the new recovery code; it is shown only once:\n\n{}').format(recovery_code)
        self['message'].setText(_('Category edit PIN renewed.'))
        opener = getattr(self.session, 'openWithCallback', None)
        if callable(opener):
            opener(self._category_recovery_notice_closed, MessageBox, message, type=MessageBox.TYPE_INFO)
            return
        self.session.open(MessageBox, message, type=MessageBox.TYPE_INFO)

    def _category_recovery_notice_closed(self, *unused):
        self._open_category_manager_unlocked()

    def _open_category_manager_unlocked(self):
        if getattr(self, '_category_manager_action', 'edit') == 'sort':
            return content_sort_ui.open_category_sort(self)
        if self._category_manager_child_open:
            return
        if self.content_type == 'live':
            self._stop_weather(suspend=True)
        from .browser_live import GTLiveCategoryManagerScreen
        arguments = (GTLiveCategoryManagerScreen, self.account.display_name, self.favorite_scope, list(self._all_categories), self.category_store, self._category_manager_return_id)
        self._category_manager_child_open = True
        opener = getattr(self.session, 'openWithCallback', None)
        if callable(opener):
            try:
                opener(self._category_manager_closed, *arguments, content_type=self.content_type)
            except Exception:
                self._category_manager_child_open = False
                if self.content_type == 'live':
                    self._weather_child_closed()
                self['message'].setText(_('Could not open the content list.'))
            return
        try:
            dialog = self.session.open(*arguments, content_type=self.content_type)
        except Exception:
            self._category_manager_child_open = False
            if self.content_type == 'live':
                self._weather_child_closed()
            self['message'].setText(_('Could not open the content list.'))
            return
        callbacks = getattr(dialog, 'onClose', None)
        if isinstance(callbacks, list):
            callbacks.append(self._category_manager_closed)

    def _category_manager_closed(self, changed=False, ordered_ids=None, hidden_ids=None, include_terms=None, exclude_terms=None, sort_mode=None):
        self._category_manager_child_open = False
        if changed and self._all_categories and (not self._error):
            preferences = CategoryPreferences(ordered_ids or (), hidden_ids or (), include_terms or (), exclude_terms or (), sort_mode, self.category_store.load(self.favorite_scope).recency)
            previous_visibility = self._hidden_category_ids
            previous_rules = getattr(previous_visibility, 'rule_key', ((), ()))
            self._hidden_category_ids = category_visibility_ids(self._all_categories, preferences)
            filters_changed = previous_rules != getattr(self._hidden_category_ids, 'rule_key', ((), ()))
            if category_filter_active(self._hidden_category_ids):
                filters_changed = filters_changed or (previous_visibility != self._hidden_category_ids or getattr(previous_visibility, 'allowed', ()) != self._hidden_category_ids.allowed)
            self._entries = arrange_categories(self._all_categories, preferences)
            self.selected_index = 0
            return_id_found = False
            for index, entry in enumerate(self._entries):
                if str(getattr(entry, 'category_id', '') or '').strip() == self._category_manager_return_id:
                    self.selected_index = index
                    return_id_found = True
                    break
            if not return_id_found and self.content_type == 'movie' and self._category_manager_return_id and (len(self._entries) > 1):
                self.selected_index = 1
            self._ready = True
            self._loading = False
            if self.content_type == 'live':
                self._refresh_live_categories()
            elif self.content_type == 'movie':
                self._movie_focus = 'categories'
                category = self._entries[self.selected_index]
                category_id = str(getattr(category, 'category_id', '') or '').strip()
                if getattr(self, '_light_vod_enabled', False) and not self._movie_category_load_started:
                    self._refresh_movie_categories(schedule_load=False)
                    self._render_movie_posters()
                elif filters_changed or category_id != self._movie_loaded_category_id:
                    self._start_movie_load(category)
                else:
                    self._refresh_movie_categories(schedule_load=False)
                    self._render_movie_posters()
            elif self.content_type == 'series':
                self._series_focus = 'categories'
                category = self._entries[self.selected_index]
                category_id = str(getattr(category, 'category_id', '') or '').strip()
                if getattr(self, '_light_vod_enabled', False) and not self._series_category_load_started:
                    self._refresh_series_categories()
                    self._render_series_tiles()
                elif filters_changed or not category_id or category_id != str(self._series_loaded_category_id or '').strip():
                    self._start_series_load(category)
                else:
                    self._refresh_series_categories()
                    self._render_series_tiles()
        if self.content_type == 'live':
            self._weather_child_closed()

    def open_channel_search(self):
        if self.content_type != 'live' or not _is_m3u_client(self.client) or (not _client_supports(self.client, 'search')):
            return
        if self._loading or self._category_preferences_generation != self._load_generation:
            self['message'].setText(_('Please wait'))
            return
        self._stop_weather(suspend=True)
        opener = getattr(self.session, 'openWithCallback', None)
        if callable(opener):
            from .browser_live import GTM3UChannelSearchScreen
            opener(self._m3u_search_closed, GTM3UChannelSearchScreen, self.account, self.client, self.favorite_store, self._hidden_category_ids)
            return
        from .browser_live import GTM3UChannelSearchScreen
        dialog = self.session.open(GTM3UChannelSearchScreen, self.account, self.client, self.favorite_store, self._hidden_category_ids)
        callbacks = getattr(dialog, 'onClose', None)
        if isinstance(callbacks, list):
            callbacks.append(self._m3u_search_closed)

    def _m3u_search_closed(self, *args):
        self._reload_favorite_keys()
        self._weather_child_closed(*args)

    def _select(self, index):
        if getattr(self, '_broadcast_kind', None) != 'categories':
            return GTAsyncListScreen._select(self, index)
        if self._closed or self._loading or self._error or not self._entries:
            return
        index %= len(self._entries)
        if index != self.selected_index:
            self.selected_index = index
            refresh_category_list(self)

    def move_up(self):
        if getattr(self, '_poster_grid_enabled', False) and self.content_type == 'movie' and (self._movie_focus == 'posters'):
            self._move_vod_grid('movie', 'up')
            return
        if self.content_type == 'series':
            self._series_move_up()
            return
        if self.content_type != 'movie':
            GTAsyncListScreen.move_up(self)
            return
        if self._movie_focus == 'posters':
            self._movie_focus = 'categories'
            self._render_movie_posters()
            self._refresh_movie_categories()
        else:
            self._select_movie_category(self.selected_index - 1)

    def move_down(self):
        if getattr(self, '_poster_grid_enabled', False) and self.content_type == 'movie' and (self._movie_focus == 'posters'):
            self._move_vod_grid('movie', 'down')
            return
        if self.content_type == 'series':
            self._series_move_down()
            return
        if self.content_type != 'movie':
            GTAsyncListScreen.move_down(self)
            return
        if self._movie_focus == 'posters':
            return
        self._select_movie_category(self.selected_index + 1)

    def page_up(self):
        if getattr(self, '_poster_grid_enabled', False) and self.content_type == 'movie' and (self._movie_focus == 'posters'):
            self._move_vod_grid('movie', 'left')
            return
        if self.content_type == 'series':
            self._series_move_left()
            return
        if self.content_type != 'movie':
            GTAsyncListScreen.page_up(self)
            return
        if self._movie_focus == 'posters':
            if self._movie_selected_index == 0 and (not self._vod_window_base('movie')):
                self._movie_focus = 'categories'
                self._render_movie_posters()
                self._refresh_movie_categories()
            else:
                self._select_movie(self._movie_selected_index - 1)
        else:
            self._select_movie_category(self.selected_index - self.page_size)

    def page_down(self):
        if getattr(self, '_poster_grid_enabled', False) and self.content_type == 'movie' and (self._movie_focus == 'posters'):
            self._move_vod_grid('movie', 'right')
            return
        if self.content_type == 'series':
            self._series_move_right()
            return
        if self.content_type != 'movie':
            GTAsyncListScreen.page_down(self)
            return
        if self._movie_focus == 'categories':
            self.open_selected()
        elif self._movie_entries and (not self._movie_loading):
            self._select_movie(self._movie_selected_index + 1)

    def channel_page_up(self):
        """Move one visible page back without changing the active pane."""
        if self.content_type == 'series':
            self._series_page(-1)
            return
        if self.content_type != 'movie':
            return
        if self._movie_focus == 'posters':
            if not self._movie_entries:
                return
            target = max(-self._vod_window_base('movie'), self._movie_selected_index - getattr(self, '_poster_page_size', MOVIE_RAIL_PAGE_SIZE))
            if target != self._movie_selected_index:
                self._select_movie(target)
            return
        if not self._entries:
            return
        target = max(0, self.selected_index - self.page_size)
        if target != self.selected_index:
            self._select_movie_category(target)

    def channel_page_down(self):
        """Move one visible page forward without changing the active pane."""
        if self.content_type == 'series':
            self._series_page(1)
            return
        if self.content_type != 'movie':
            return
        if self._movie_focus == 'posters':
            if not self._movie_entries:
                return
            target = self._movie_selected_index + getattr(self, '_poster_page_size', MOVIE_RAIL_PAGE_SIZE)
            if not self._movie_has_more:
                target = min(target, len(self._movie_entries) - 1)
            if target != self._movie_selected_index:
                self._select_movie(target)
            return
        if not self._entries:
            return
        target = min(self.selected_index + self.page_size, len(self._entries) - 1)
        if target != self.selected_index:
            self._select_movie_category(target)

    @broadcast_refresh_after
    def _render_weather_waiting(self, settings=None):
        settings = settings or self._weather_settings_loader()
        self._dynamic_weather_city_text = '●  {}'.format(localized_upper(settings.city))
        ellipsize_dynamic_text(self['weather_city'], self._dynamic_weather_city_text, fallback_chars=20)
        self['weather_current_icon'].hide()
        self['weather_metrics'].setText('')
        self._clear_weather_days()
        if not settings.enabled:
            self['weather_temp'].setText('--')
            self['weather_desc'].setText(_('Weather is disabled'))
            self['weather_updated'].setText(_('You can enable it in Settings.'))
        else:
            self['weather_temp'].setText('--°')
            self['weather_desc'].setText(_('Loading forecast'))
            self['weather_updated'].setText('Open-Meteo')
        refresh_category_weather(self)

    def _clear_weather_days(self):
        for index in range(5):
            self['weather_day_{}'.format(index)].setText('--')
            self['weather_day_high_{}'.format(index)].setText('--°')
            self['weather_day_low_{}'.format(index)].setText('--°')
            self['weather_day_icon_{}'.format(index)].hide()

    @staticmethod
    def _weather_icon_path(code):
        return os.path.join(WEATHER_ICON_DIR, '{}.png'.format(weather_icon_name(code)))

    def _apply_weather_icon(self, widget_name, code):
        widget = self[widget_name]
        path = self._weather_icon_path(code)
        if not os.path.isfile(path):
            widget.hide()
            return
        try:
            widget.instance.setPixmapFromFile(path)
            widget.show()
        except Exception:
            widget.hide()

    def start_weather(self, force=False):
        if self.content_type != 'live' or self._weather_loading or self._closed or self._weather_suspended or (self._weather_started and (not force)):
            return
        settings = self._weather_settings_loader()
        self._render_weather_waiting(settings)
        if not settings.enabled:
            self._weather_started = True
            return
        _cancel_worker_token(self._weather_token)
        self._weather_generation += 1
        generation = self._weather_generation
        token = _WorkerToken(WORKER_WEATHER_TIMEOUT_SECONDS)
        self._weather_token = token
        self._weather_settings = settings
        self._weather_forecast = None
        self._weather_error = ''
        self._weather_ready = False
        self._weather_loading = True
        self._weather_started = True
        self._weather_force = bool(force)
        worker = threading.Thread(target=self._run_weather, args=(generation, token))
        worker.daemon = True
        worker.start()
        self._weather_timer.start(150, True)

    def _run_weather(self, generation=None, token=None):
        if generation is None:
            generation = self._weather_generation
        forecast = None
        error_text = ''
        try:
            if token is not None:
                token.check()
            forecast = self._weather_service.get(self._weather_settings, force=self._weather_force, cancel_event=token.cancel_event if token is not None else None, deadline=token.deadline if token is not None else None)
            if token is not None:
                token.check()
        except _WorkerCancelled:
            return
        except WeatherError as caught:
            error_text = str(caught)
        except Exception:
            error_text = 'Could not load the weather forecast'
        if self._closed or self._weather_suspended or generation != self._weather_generation or (token is not None and (not token.active())):
            return
        self._weather_forecast = forecast
        self._weather_error = error_text
        self._weather_ready = True

    def _poll_weather(self):
        if self._closed:
            return
        if not self._weather_ready:
            if self._weather_token is not None and self._weather_token.expired():
                self._weather_token.cancel()
                self._weather_loading = False
                self._weather_error = N_('Weather request timed out')
                self['weather_desc'].setText(_('Weather data unavailable.'))
                ellipsize_dynamic_text(self['weather_updated'], _(self._weather_error), fallback_chars=50)
                refresh_category_weather(self)
                return
            self._weather_timer.start(150, True)
            return
        self._weather_loading = False
        self._weather_token = None
        if self._weather_forecast is None:
            self['weather_desc'].setText(_('Weather data unavailable.'))
            ellipsize_dynamic_text(self['weather_updated'], _(self._weather_error), fallback_chars=50)
            refresh_category_weather(self)
            return
        forecast = self._weather_forecast
        degree = forecast.degree
        self._dynamic_weather_city_text = '●  {}'.format(localized_upper(forecast.city))
        ellipsize_dynamic_text(self['weather_city'], self._dynamic_weather_city_text, fallback_chars=20)
        self._apply_weather_icon('weather_current_icon', forecast.current_code)
        self['weather_temp'].setText('{:.0f}{}'.format(forecast.current_temperature, degree))
        self['weather_desc'].setText(localized_upper(weather_description(forecast.current_code)))
        self['weather_metrics'].setText(_('Humidity {}% • Wind {:.0f} km/h').format(forecast.current_humidity, forecast.current_wind_speed))
        suffix = _('  •  Cached') if forecast.stale else ''
        self['weather_updated'].setText(_('Updated: {}{}').format(forecast.updated_at, suffix))
        for index in range(5):
            if index >= len(forecast.days):
                self['weather_day_{}'.format(index)].setText('--')
                self['weather_day_high_{}'.format(index)].setText('--°')
                self['weather_day_low_{}'.format(index)].setText('--°')
                self['weather_day_icon_{}'.format(index)].hide()
                continue
            day = forecast.days[index]
            label = _('Today') if index == 0 else day_name(day.date)
            self['weather_day_{}'.format(index)].setText(localized_upper(label))
            self['weather_day_high_{}'.format(index)].setText('{:.0f}°'.format(day.maximum))
            self['weather_day_low_{}'.format(index)].setText('{:.0f}°'.format(day.minimum))
            self._apply_weather_icon('weather_day_icon_{}'.format(index), day.code)
        refresh_category_weather(self)

    def open_weather_settings(self):
        from .main import GTWeatherSettingsScreen
        if hasattr(self.session, 'openWithCallback'):
            self.session.openWithCallback(self._weather_settings_closed, GTWeatherSettingsScreen)
        else:
            self.session.open(GTWeatherSettingsScreen)

    def _suspend_for_playback(self, keep_catalog=False):
        """Silence optional parent work; search keeps the shared producer."""
        if self.content_type in ('movie', 'series'):
            content_sort_ui.cancel_loading(self, self.content_type)
        preparation = getattr(self, '_vod_preparation', None)
        if preparation is not None:
            preparation.pause()
        self._reset_vod_poster_retries()
        self._vod_work_suspended = True
        self._pause_vod_scroll()
        if self.content_type == 'movie':
            browse_catalog = getattr(self, '_movie_browser_catalog', None)
            if browse_catalog is not None and (not keep_catalog):
                browse_catalog.stop()
            reset_cinematic = getattr(self, '_reset_cinematic_backdrop', None)
            if callable(reset_cinematic):
                reset_cinematic()
            for token in (self._movie_more_token, self._movie_detail_token, self._movie_poster_token):
                _cancel_worker_token(token)
            self._movie_more_token = None
            self._movie_detail_token = None
            self._movie_poster_token = None
            self._movie_more_loading = False
            self._movie_detail_pending = False
            self._movie_poster_pending = False
            self._movie_poster_state = None
            self._movie_deferred_poster_jobs = None
            for timer in (self._movie_timer, self._movie_detail_timer):
                try:
                    timer.stop()
                except Exception:
                    pass
        elif self.content_type == 'series':
            browse_catalog = getattr(self, '_series_browser_catalog', None)
            if browse_catalog is not None and (not keep_catalog):
                browse_catalog.stop()
            reset_cinematic = getattr(self, '_reset_cinematic_backdrop', None)
            if callable(reset_cinematic):
                reset_cinematic()
            for token in (self._series_more_token, self._series_detail_token, self._series_poster_token):
                _cancel_worker_token(token)
            self._series_more_token = None
            self._series_detail_token = None
            self._series_poster_token = None
            self._series_more_loading = False
            self._series_detail_pending = False
            self._series_poster_pending = False
            self._series_poster_state = None
            self._series_deferred_poster_jobs = None
            for timer in (self._series_timer, self._series_scan_timer, self._series_category_timer, self._series_detail_timer):
                try:
                    timer.stop()
                except Exception:
                    pass

    def _weather_settings_closed(self, *args):
        self.start_weather(force=True)

    def _stop_weather(self, suspend=False):
        self._weather_generation += 1
        _cancel_worker_token(self._weather_token)
        self._weather_token = None
        self._weather_loading = False
        self._weather_ready = bool(self._weather_forecast is not None)
        self._weather_suspended = bool(suspend)
        if self._weather_forecast is None:
            self._weather_started = False
        try:
            self._weather_timer.stop()
        except Exception:
            pass

    def _weather_child_closed(self, *args):
        if self.content_type == 'live' and args and isinstance(args[0], dict):
            result = args[0]
            if result.get('kind') == 'live-list-category':
                self._returned_preview = result.get('preview_restore')
                if self._returned_preview is not None:
                    # The category frame is already queued for the renderer.
                    self._returned_preview_timer.start(LIVE_PREVIEW_RETURN_RESTORE_MS, True)
                category_id = str(result.get('category_id', '') or '')
                for index, category in enumerate(self._entries):
                    if str(getattr(category, 'category_id', '') or '') == category_id:
                        if index != self.selected_index:
                            self.selected_index = index
                            self._refresh()
                        break
        self._weather_suspended = False
        if self._weather_forecast is None:
            self.start_weather()

    def _restore_returned_preview(self):
        preview = self._returned_preview
        if preview is None:
            return
        self._returned_preview = None
        self._returned_preview_timer.stop()
        from .browser_live import GTStreamListScreen
        GTStreamListScreen.restore_returned_preview(getattr(self.session, 'nav', None), *preview)

    def _clear_live_recall_history(self):
        if self.content_type == 'live':
            clear_live_channel_history(self.client, resume_account_scope(self.client))

    def open_selected(self):
        if not self._entries or self._loading:
            return
        if self.content_type == 'movie':
            if self._movie_search_child_open:
                self['message'].setText(_('Please wait'))
                return
            if self._movie_focus == 'categories':
                category = self._entries[self.selected_index]
                loaded = self._movie_loaded_category_id == category.category_id
                if loaded and self._movie_entries and (not self._movie_loading):
                    self._movie_focus = 'posters'
                    self._schedule_movie_detail()
                    self._render_movie_posters()
                    self._refresh_movie_categories()
                elif not loaded or not (self._movie_loading or self._movie_more_loading):
                    self._start_movie_load(category)
                return
            if self._movie_entries and (not self._movie_loading):
                from .browser_player import open_extplayer
                open_extplayer(self.session, self.client, self._movie_entries[self._movie_selected_index], self._movie_entries, self._movie_selected_index, owner=self)
            return
        if self.content_type == 'series':
            if self._series_focus == 'categories':
                category = self._entries[self.selected_index]
                loaded = str(self._series_loaded_category_id or '') == str(getattr(category, 'category_id', '') or '')
                if loaded and self._series_entries:
                    self._series_focus = 'series'
                    self._schedule_series_detail()
                    self._refresh_series_categories()
                    self._render_series_tiles()
                elif not loaded or not (self._series_loading or self._series_more_loading):
                    self._start_series_load(category)
                return
            self.open_series_episode_list()
            return
        from .browser_live import GTStreamListScreen
        if self.content_type == 'live':
            self._restore_returned_preview()
        arguments = (GTStreamListScreen, self.account, self.content_type, self._entries[self.selected_index], self.client)
        snapshot = self._weather_forecast
        self._stop_weather(suspend=True)
        opener = getattr(self.session, 'openWithCallback', None)
        if callable(opener):
            opener(self._weather_child_closed, *arguments, weather_forecast=snapshot, favorite_store=self.favorite_store, hidden_category_ids=self._hidden_category_ids, live_source_categories=self._all_categories, defer_preview_exit=self.content_type == 'live')
            return
        self.session.open(*arguments, weather_forecast=snapshot, favorite_store=self.favorite_store, hidden_category_ids=self._hidden_category_ids, live_source_categories=self._all_categories)

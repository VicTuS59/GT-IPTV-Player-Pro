# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""episodes; loaded when its screen is needed."""

import datetime
import re
import time
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import ePoint, eSize, eTimer, getDesktop, iServiceInformation
from .source_loader import SourceLoader
from .diagnostics import log_event, redact
from .i18n import N_, _, ngettext, localized_date_text, localized_short_weekday, localized_language_name, localized_upper, metadata_language
from .live_zero import LiveZeroKey
from .background import attach_background, attach_background_window, attach_native_alpha_pixmap, attach_pixmap
from .metadata import MetadataError, TMDbMetadataClient, complete_movie_for_device_language, movie_metadata_ready, media_metadata_ready, complete_media_for_device_language
from .playback import PlaybackResumeStore, resume_account_scope, resume_key, should_save_resume
from .episode_history import EpisodeCompletionWatcher, WatchedEpisodeStore, episode_watch_key
from .episode_design import EPISODE_ROWS, EPISODE_LABEL_NAMES, EPISODE_PIXMAP_NAMES, episode_geometry, episode_skin, episode_focus_assets, episode_footer
from .episode_model import group_episode_seasons, episode_number, episode_title, episode_code, episode_display_title, format_episode_time, progress_percent, preferred_resume_index
from .vod_discovery import style_footer, refresh_plain_footer
from .paths import plugin_path
from .remote_footer import web_shortcut_hint_text, decorate_remote_footer, prepared_skin, footer_item, install_remote_footer, set_remote_footer, remote_action_text
from .scrollbar import hide_scrollbar, update_horizontal_scrollbar, update_scrollbar
from .settings import DEFAULT_INFOBAR_TIMEOUT, available_service_types, load_player_settings, normalize_infobar_timeout, serviceapp_uses_async_seek, service_engine_label
from .typography import _grapheme_boundaries, _line_fits, _text_units, clean_dynamic_text, current_text_size, ellipsize_dynamic_text, fit_dynamic_text, font_px, scale_skin_fonts, set_static_text, set_text_if_changed, visible_count
from .browser_common import APP_BACKGROUND_PATH, CINEMATIC_CANDIDATE_TIMEOUT_SECONDS, EPISODE_DOWNLOAD_FOOTER_ITEMS, EPISODE_FOOTER_ITEMS, GTAsyncListScreen, _FavoriteSupport, _WorkerCancelled, _WorkerToken, _bounded_client_call, _cached_picon_path, _cancel_worker_token, _cinematic_cover_ratio, _connect_timer, _download_client_image, _picon_cache_path, _rating_label, _safe_picon_url, _scale, _valid_cinematic_fallback_file, content_client_for

def _episode_list_skin():
    width, height, px = _scale()
    return episode_skin(width, height, px, plugin_path('skin', 'images', 'category-check-tick-r83.png'))

class GTEpisodeListScreen(_FavoriteSupport, GTAsyncListScreen):
    """Account-scoped cinematic season browser with durable completion dates."""

    def __init__(self, session, account, series, client=None, favorite_store=None, download_mode=False, series_info=None):
        self.download_mode = bool(download_mode)
        self.series = series
        self.client = client or content_client_for(account)
        self._watched_store = WatchedEpisodeStore()
        self._resume_store = PlaybackResumeStore()
        self._watched_records = {}
        self._resume_records = {}
        self._all_episode_entries = []
        self._season_groups = []
        self._season_index = 0
        self._season_selection = {}
        self._episode_pane = 'episodes'
        self._episode_action = 0
        self._provider_info = series_info
        self._series_detail_info = series_info
        self._episode_keys_bound = False
        self._episode_key_map = None
        self._episode_key_ids = set()
        self._episode_ok_key_code = None
        self._episode_metadata_started = False
        self._episode_art_token = None
        self._episode_art_path = ''
        self._episode_state_generation = 0
        self._episode_reload_selection = None
        self._episode_resume_request = ''
        self._episode_pending_watched = None
        self._init_favorite_support(favorite_store)
        self._footer_layout_items = episode_footer(self.download_mode)
        self._footer_layout_stacked = False
        self._footer_transparent_panel = True
        self._footer_show_dividers = False
        self.skin = prepared_skin(_episode_list_skin)
        self.page_size = EPISODE_ROWS
        width, height, px = _scale()
        self._episode_geometry = episode_geometry(width, height, px)
        self._footer_panel_height = int(round(self._episode_geometry['footer_h'] / min(width / 1920.0, height / 1080.0)))
        self._footer_bottom_padding = 0
        self._footer_show_web_hint = False
        self._list_scroll_geometry = self._episode_geometry['list_scroll']
        self._screen_background_path = ''
        self._opaque_background = True
        GTAsyncListScreen.__init__(self, session, 'GTEpisodeListScreen', series.name, _('Series') + '  /  ' + series.name, frameless_footer=True)
        captions=tuple(remote_action_text(item[1]) for item in self._footer_layout_items)
        self.skin=style_footer(self.skin,'series',px,self._footer_layout_items,captions)
        for name in EPISODE_LABEL_NAMES:
            self[name] = Label('')
        for name in EPISODE_PIXMAP_NAMES:
            self[name] = Pixmap()
        self.onLayoutFinish.append(self._refresh_episode_footer)
        self.onShown.append(self._refresh_episode_footer)
        self._refresh_episode_footer()
        self._episode_state_reader = SourceLoader(eTimer(), _connect_timer, self._episode_state_loaded)
        self._episode_art_reader = SourceLoader(eTimer(), _connect_timer, self._episode_art_loaded)
        self._episode_ok_key = LiveZeroKey(eTimer, self._episode_key_active, self.open_selected, self.toggle_selected_watched, prime_native=self._prime_episode_ok)
        self['actions'] = ActionMap(['OkCancelActions', 'DirectionActions', 'ColorActions', 'MenuActions'], {'ok': self.open_selected, 'oklong': self.toggle_selected_watched, 'ok_long': self.toggle_selected_watched, 'menu': self.download_selected, 'cancel': self.close, 'up': self.move_up, 'down': self.move_down, 'upRepeated': self.move_up, 'downRepeated': self.move_down, 'left': self.episode_left, 'right': self.episode_right, 'leftRepeated': self.episode_left, 'rightRepeated': self.episode_right, 'red': self.toggle_series_favorite, 'green': self.start_load}, -1)
        self['channel_actions'] = ActionMap(['MoviePlayerActions'], {'channelUp': self.previous_page, 'channelDown': self.next_page}, -1)
        self._episode_clock_timer = eTimer()
        _connect_timer(self._episode_clock_timer, self._episode_clock_tick)
        self.onExecBegin.append(self._bind_episode_keys)
        self.onExecEnd.append(self._unbind_episode_keys)
        self.onShown.append(self._episode_screen_shown)
        self.onLayoutFinish.append(self._episode_layout_ready)
        self.onClose.append(self._stop_episode_screen)
        self['episode_backdrop'].hide()
        self['episode_scrim'].hide()
        self['season_tabs_focus'].hide()
        self._clear_episode_rows()
        self._render_episode_details()

    def _episode_layout_ready(self):
        from .channel_highlight import saved_selection_palette
        fill, border = saved_selection_palette()
        fill_path, border_path = episode_focus_assets(fill, border)
        for i in range(self.page_size):
            for suffix, path in (('', border_path), ('_fill', fill_path)):
                name = ('episode_focus_fill_{}' if suffix else 'episode_focus_{}').format(i)
                attach_native_alpha_pixmap(self, name, path, key=name, show_on_load=False)
        self._render_episode_details()
        self._refresh()

    def _refresh_episode_footer(self):
        refresh_plain_footer(self,self._footer_layout_items,_scale()[2],
            tuple(remote_action_text(item[1]) for item in self._footer_layout_items))

    def _episode_screen_shown(self):
        self._favorite_screen_shown()
        self._episode_clock_tick()

    def _episode_clock_tick(self):
        if self._closed:
            return
        self['episode_clock'].setText(time.strftime('%H:%M'))
        if bool(getattr(self, 'execing', True)):
            self._episode_clock_timer.start(30000, True)

    def _episode_key_active(self):
        return bool(not self._closed and (not self._loading) and getattr(self, 'execing', True) and self._entries)

    def _bind_episode_keys(self):
        if self._closed or self._episode_keys_bound:
            return
        try:
            from enigma import eActionMap
            from keyids import KEYIDS
            self._episode_key_ids = set((KEYIDS[name] for name in ('KEY_OK', 'KEY_ENTER') if name in KEYIDS))
            self._episode_key_map = eActionMap.getInstance()
            self._episode_key_map.bindAction('', -110, self._episode_key_event)
            self._episode_keys_bound = True
        except (ImportError, AttributeError, RuntimeError, TypeError) as error:
            log_event('episodes', 'remote-bind-failed', error)
        self._episode_clock_tick()

    def _prime_episode_ok(self):
        if self._episode_key_map is not None and self._episode_ok_key_code in self._episode_key_ids:
            self._episode_key_map.keyPressed('', self._episode_ok_key_code, 0)

    def _episode_key_event(self, key, flag):
        if key not in self._episode_key_ids:
            return 0
        self._episode_ok_key_code = key
        return self._episode_ok_key.handle(flag)

    def _unbind_episode_keys(self):
        self._episode_ok_key.cancel()
        self._episode_clock_timer.stop()
        if self._episode_keys_bound and self._episode_key_map is not None:
            try:
                self._episode_key_map.unbindAction('', self._episode_key_event)
            except (AttributeError, RuntimeError, TypeError):
                pass
        self._episode_keys_bound = False
        self._episode_ok_key_code = None

    def _stop_episode_screen(self):
        self._unbind_episode_keys()
        self._episode_ok_key.close()
        _cancel_worker_token(self._episode_art_token)
        self._episode_art_token = None
        self._episode_state_generation += 1
        self._episode_resume_request = ''
        self._episode_pending_watched = None
        self._episode_state_reader.close()
        self._episode_art_reader.close()

    def start_load(self):
        if self._loading or self._closed:
            return
        if self._entries:
            self._episode_reload_selection = (self._season_groups[self._season_index][0], episode_watch_key(self.client, self._entries[self.selected_index]))
        self._clear_episode_rows()
        GTAsyncListScreen.start_load(self)
        self['items'].setText('')

    def load_items(self):
        restore = getattr(self.client, 'restore_series_catalog_item', None)
        if callable(restore) and getattr(self.series, 'portal_catalog_page', None) is not None:
            restore(self.series)
        info = self._provider_info
        loader = getattr(self.client, 'load_series_info', None)
        if callable(loader):
            loaded = loader(self.series.stream_id)
            if loaded is not None:
                info = loaded
        entries = getattr(info, 'episodes', None) if info is not None else None
        if entries is None or not entries:
            entries = self.client.load_episodes(self.series.stream_id)
        entries = list(entries or [])
        for episode in entries:
            episode.parent_id = str(self.series.stream_id)
            episode.favorite_parent = self.series
            if not getattr(episode, 'icon', ''):
                episode.icon = getattr(self.series, 'icon', '')
        return (entries, info)

    def _prepare_loaded_items(self, value):
        entries, info = value
        watched = self._watched_store.get_many((episode_watch_key(self.client, item) for item in entries), refresh=True)
        bookmarks = self._resume_store.get_many((resume_key(self.client, item) for item in entries))
        return (entries, info, watched, bookmarks)

    def _commit_loaded_items(self, value, generation):
        if self._closed or generation != self._load_generation:
            return
        if not isinstance(value, tuple) or len(value) != 4:
            value = ([], self._provider_info, {}, {})
        entries, info, watched, bookmarks = value
        self._all_episode_entries = entries
        self._provider_info = info
        self._series_detail_info = info or self._series_detail_info
        self._watched_records, self._resume_records = (watched, bookmarks)
        self._season_groups = group_episode_seasons(entries)
        self._season_index = 0
        target = preferred_resume_index(entries, bookmarks, set(watched), self.client)
        selection = self._episode_reload_selection
        if selection:
            target = next((i for i, item in enumerate(entries) if episode_watch_key(self.client, item) == selection[1]), target)
        target_item = entries[target] if target is not None and target < len(entries) else None
        self._entries = []
        for i, (season, items) in enumerate(self._season_groups):
            if target_item is not None and target_item in items:
                self._season_index = i
                self._entries = items
                self.selected_index = items.index(target_item)
                break
        if not self._entries and self._season_groups:
            self._entries = self._season_groups[0][1]
            self.selected_index = 0
        self._episode_reload_selection = None

    def _poll_result(self):
        GTAsyncListScreen._poll_result(self)
        if not self._closed and (not self._loading) and self._ready:
            self._render_episode_details()
            if self._entries and (not self._episode_metadata_started):
                self._episode_metadata_started = True
                self._start_episode_artwork()

    def empty_message(self):
        return N_('No episodes were found for this series.')

    def _change_season(self, step):
        if self._loading or not self._season_groups:
            return
        old = self._season_groups[self._season_index][0]
        self._season_selection[old] = self.selected_index
        self._season_index = (self._season_index + step) % len(self._season_groups)
        season, self._entries = self._season_groups[self._season_index]
        self.selected_index = min(self._season_selection.get(season, 0), len(self._entries) - 1)
        self._refresh()

    def move_up(self):
        self._episode_pane = 'episodes'
        if self._entries and not self._loading:self._select(max(0,self.selected_index-1))

    def move_down(self):
        self._episode_pane = 'episodes'
        if self._entries and not self._loading:self._select(min(len(self._entries)-1,self.selected_index+1))

    def episode_left(self):
        self._episode_pane = 'episodes'
        self._change_season(-1)

    def episode_right(self):
        self._episode_pane = 'episodes'
        self._change_season(1)

    def _change_episode_page(self, direction):
        if not self._entries or self._loading:
            return
        self._episode_pane = 'episodes'
        start = self.selected_index // self.page_size * self.page_size
        last = (len(self._entries) - 1) // self.page_size * self.page_size
        target = max(0, min(start + direction * self.page_size, last))
        self.selected_index = target + min(self.selected_index - start, len(self._entries) - target - 1)
        self._refresh()

    def previous_page(self):
        self._change_episode_page(-1)

    def next_page(self):
        self._change_episode_page(1)

    def _episode_fit(self, name, value, lines=1):
        fonts = self._episode_geometry.get('fonts', {})
        font_key = {'series_title': 'title', 'series_meta': 'meta', 'series_genre': 'genre', 'series_plot': 'plot', 'series_cast': 'cast', 'episode_resume_text': 'resume', 'episode_resume_detail': 'resume_detail', 'episode_favorite_text': 'favorite', 'episode_download_text': 'download', 'season_progress_text': 'progress_text'}.get(name, name)
        if name.startswith('season_tab_'):
            font_key = 'season'
        preferred = fonts.get(font_key)
        kwargs = {'max_lines': lines, 'fallback_chars': 90 if lines > 1 else 55}
        if isinstance(preferred, (int, float)):
            kwargs.update(preferred_size=int(preferred), min_size=max(9, int(preferred * 0.7)))
        fit_dynamic_text(self[name], str(value or ''), **kwargs)

    @staticmethod
    def _episode_show(widget, shown):
        if shown:
            widget.show()
        else:
            widget.hide()

    def _episode_progress(self, name, rect, percent):
        width = max(1, int(rect[2] * max(0, min(100, percent)) / 100.0))
        instance = getattr(self[name], 'instance', None)
        if instance is not None:
            instance.resize(eSize(width, max(1, rect[3])))
        self._episode_show(self[name], percent > 0)

    def _resume_target(self):
        index = preferred_resume_index(self._entries, self._resume_records, set(self._watched_records), self.client)
        return index if index is not None else self.selected_index if self._entries else None

    def _render_episode_details(self):
        info = self._series_detail_info
        title = getattr(info, 'title', '') or self.series.name
        year = getattr(info, 'year', '') or getattr(self.series, 'year', '')
        if year:title=re.sub(r'\s*\('+re.escape(str(year))+r'\)\s*$', '',title)
        self._episode_fit('series_title', title, 2)
        meta = [str(year)] if year else []
        if self._season_groups:
            season_count = len(self._season_groups)
            meta.append(ngettext('{} season', '{} seasons', season_count).format(season_count))
        rating = getattr(info, 'rating', '') or getattr(self.series, 'rating', '')
        if rating:
            meta.append(_rating_label(rating))
        self._episode_fit('series_meta', '  •  '.join(meta), 1)
        self._episode_fit('series_genre', getattr(info, 'genre', ''), 1)
        self._episode_fit('series_plot', getattr(info, 'plot', '') or _('No information available.'), 3)
        cast = getattr(info, 'cast', '') or ''
        self['series_cast_label'].setText(_('CAST') if cast else '')
        self._episode_fit('series_cast', str(cast), 2)
        self._episode_fit('episode_favorite_text', _('Remove from favorites') if self._is_series_favorite() else _('Add to favorites'))
        self._episode_fit('episode_download_text', _('Download episode'))
        self['episode_download_key'].setText('MENU')
        self['season_progress_label'].setText(_('SEASON PROGRESS'))
        self['episode_selected_caption'].setText(localized_upper(_('Selected episode')))
        self['episode_list_caption'].setText(localized_upper(_('Episodes')))
        self['episode_page_hint'].setText('CH±  '+_('Page'))
        self._set_dynamic_breadcrumb(_('Series') + '  /  ' + _('Episodes'))
        self._render_episode_resume()

    def _is_series_favorite(self):
        return self._is_favorite(self.series)

    def _render_episode_resume(self):
        target = self.selected_index if self._entries else None
        label = ''
        detail = ''
        if target is not None and self._entries:
            item = self._entries[target]
            bookmark = self._resume_records.get(resume_key(self.client, item))
            number = episode_number(item, target + 1)
            season=getattr(item,'season','')
            label=(_('Season {}').format(season) if season else _('Unknown season'))+' · '+_('Episode {}').format(number)
            if episode_watch_key(self.client,item) in self._watched_records:
                detail=_('Watched')
            elif bookmark:
                detail=_('Resume at {}').format(format_episode_time(bookmark['position']))
            else:
                detail=_('Not watched')
        self._episode_fit('episode_resume_text', label)
        self._episode_fit('episode_resume_detail', detail)

    def _refresh(self):
        if not hasattr(self, '_episode_geometry'):
            return
        self['items'].setText('')
        self._episode_fit('episode_favorite_text', _('Remove from favorites') if self._is_series_favorite() else _('Add to favorites'))
        self._clear_episode_rows()
        if self._error or not self._entries:
            self['message'].setText(_(self._error or self.empty_message()))
            hide_scrollbar(self, 'list_scroll')
            self._render_episode_resume()
            return
        self.selected_index %= len(self._entries)
        start = self.selected_index // self.page_size * self.page_size
        current_watched = self._watched_store.get_many((episode_watch_key(self.client, item) for item in self._all_episode_entries), refresh=False)
        self._watched_records = current_watched
        for row, item in enumerate(self._entries[start:start + self.page_size]):
            number = episode_number(item, start + row + 1)
            self['episode_number_{}'.format(row)].setText(str(number).zfill(2))
            title = episode_display_title(item,self.series.name,_('Episode {}').format(number),start+row+1)
            ellipsize_dynamic_text(self['episode_name_{}'.format(row)], title, fallback_chars=55)
            key = episode_watch_key(self.client, item)
            watched = current_watched.get(key)
            bookmark = self._resume_records.get(resume_key(self.client, item))
            percent = 0
            if watched is not None:
                status = _('Watched')
                timestamp = watched.get('finished_at', 0)
                if timestamp:
                    try:
                        status += ' · ' + datetime.datetime.fromtimestamp(timestamp).strftime('%d.%m.%Y')
                    except (OSError, ValueError, OverflowError):
                        pass
            elif bookmark:
                status = _('Resume at {}').format(format_episode_time(bookmark['position']))
                percent = progress_percent(bookmark['position'], bookmark['length'])
            else:
                status = _('Not watched')
            code=episode_code(item,start+row+1)
            if code:status=code+' · '+status
            ellipsize_dynamic_text(self['episode_status_{}'.format(row)], status, fallback_chars=65)
            for part in ('row_bg', 'number', 'name', 'status'):
                self['episode_{}_{}'.format(part, row)].show()
            self._episode_show(self['episode_watched_{}'.format(row)], watched is not None)
            self._episode_show(self['episode_progress_track_{}'.format(row)], percent > 0)
            self._episode_progress('episode_progress_fill_{}'.format(row), self._episode_geometry['rows'][row]['progress'], percent)
            focused = start + row == self.selected_index and self._episode_pane == 'episodes'
            self._episode_show(self['episode_focus_{}'.format(row)], focused)
            self._episode_show(self['episode_focus_fill_{}'.format(row)], focused)
        first_tab = min(max(0, self._season_index - 1), max(0, len(self._season_groups) - 3))
        for slot in range(3):
            index = first_tab + slot
            exists = index < len(self._season_groups)
            label = ''
            if exists:
                season = self._season_groups[index][0]
                label = _('Season {}').format(season) if season else _('Unknown season')
                if self._episode_pane == 'seasons' and index == self._season_index:
                    label = '▸ ' + label
            self._episode_fit('season_tab_{}'.format(slot), label)
            self._episode_show(self['season_tab_line_{}'.format(slot)], exists and index == self._season_index)
        self._episode_show(self['season_tabs_focus'], self._episode_pane == 'seasons')
        episode_count = len(self._entries)
        self['season_count'].setText(ngettext('{} episode', '{} episodes', episode_count).format(episode_count))
        completed = sum((episode_watch_key(self.client, item) in current_watched for item in self._entries))
        percent = int(completed * 100 / episode_count)
        self._episode_fit('season_progress_text', ngettext('{} / {} episode completed', '{} / {} episodes completed', episode_count).format(completed, episode_count))
        self['season_progress_percent'].setText('{}%'.format(percent))
        self._episode_progress('season_progress_fill', self._episode_geometry['season_progress'], percent)
        self['episode_position'].setText('{}–{} / {}'.format(start + 1, min(start + self.page_size, len(self._entries)), len(self._entries)))
        for i in range(3):
            self._episode_show(self['episode_action_focus_{}'.format(i)], self._episode_pane == 'actions' and self._episode_action == i)
        self['message'].setText('')
        self._render_episode_resume()
        update_scrollbar(self, 'list_scroll', len(self._entries), self.selected_index, self.page_size, self._list_scroll_geometry)

    def _clear_episode_rows(self):
        for i in range(self.page_size):
            for part in ('row_bg', 'number', 'name', 'status', 'watched', 'progress_track', 'progress_fill'):
                self['episode_{}_{}'.format(part, i)].hide()
            self['episode_focus_{}'.format(i)].hide()
            self['episode_focus_fill_{}'.format(i)].hide()
        for i in range(3):
            self['episode_action_focus_{}'.format(i)].hide()

    def toggle_selected_watched(self):
        if self._loading or not self._entries:
            return
        self._episode_pane = 'episodes'
        item = self._entries[self.selected_index]
        key = episode_watch_key(self.client, item)
        watched = key in self._watched_store.get_many((episode_watch_key(self.client, item) for item in self._all_episode_entries), refresh=False)
        if not key or not self._watched_store.set_watched_async(key, not watched):
            self['message'].setText(_('Could not save watched status.'))
            return
        self._episode_pending_watched = (key, not watched)
        self._refresh()
        self['message'].setText(_('Please wait'))
        self._request_episode_state()

    def _request_episode_state(self):
        if self._closed:
            return
        self._episode_state_generation += 1
        generation = self._episode_state_generation
        entries = list(self._all_episode_entries)
        mutation = self._episode_pending_watched

        def read():
            confirmed = self._watched_store.confirm_watched(*mutation) if mutation is not None else None
            return (generation, self._watched_store.get_many((episode_watch_key(self.client, item) for item in entries), refresh=True), self._resume_store.get_many((resume_key(self.client, item) for item in entries)), mutation, confirmed)
        self._episode_state_reader.request(read)

    def _episode_state_loaded(self, value, error):
        if self._closed:
            return
        if error is not None or not value:
            if self._episode_pending_watched is not None:
                self._episode_pending_watched = None
                self['message'].setText(_('Could not save watched status.'))
            return
        generation, watched, bookmarks, mutation, confirmed = value
        if generation != self._episode_state_generation:
            return
        self._watched_records, self._resume_records = (watched, bookmarks)
        self._refresh()
        if mutation is not None and mutation == self._episode_pending_watched:
            if confirmed is None:
                self['message'].setText(_('Please wait'))
                self._request_episode_state()
                return
            self._episode_pending_watched = None
            self['message'].setText((_('Marked as watched') if mutation[1] else _('Marked as not watched')) if confirmed else _('Could not save watched status.'))

    def _episode_playback_closed(self, *args):
        if not self._closed:
            self._request_episode_state()

    def open_selected(self):
        self._episode_resume_request = ''
        if self._loading or not self._entries:
            return
        if self._episode_pane == 'seasons':
            self._episode_pane = 'episodes'
            self._refresh()
            return
        if self._episode_pane == 'actions':
            if self._episode_action == 1:
                self.toggle_series_favorite()
                return
            if self._episode_action == 2:
                self.download_selected()
                return
            target = self._resume_target()
            if target is not None:
                self.selected_index = target
                if not self.download_mode:
                    self._episode_resume_request = resume_key(self.client, self._entries[target])
        if self.download_mode:
            self.download_selected()
            return
        from .browser_player import open_extplayer
        open_extplayer(self.session, self.client, self._entries[self.selected_index], self._entries, self.selected_index, owner=self)

    def _consume_episode_resume_request(self, key):
        requested, self._episode_resume_request = (self._episode_resume_request, '')
        return bool(requested and requested == key)

    def _resume_after_playback_resolution_failure(self):
        self._episode_resume_request = ''

    def download_selected(self):
        if not self._entries or self._loading:
            return
        from .downloads_ui import request_download
        request_download(self.session, self.client, self._entries[self.selected_index])

    def toggle_series_favorite(self):
        added = self._toggle_favorite_item(self.series)
        self._render_episode_details()
        self['message'].setText(self._favorite_message(self.series, added))

    def _start_episode_artwork(self):
        token = _WorkerToken(40)
        self._episode_art_token = token
        seed = self._provider_info

        def load():
            settings = load_player_settings()
            metadata = TMDbMetadataClient(api_key=getattr(settings, 'tmdb_api_key', ''), enabled=getattr(settings, 'metadata_enabled', True), language=metadata_language())
            info = seed
            try:
                info = _bounded_client_call(metadata, token, lambda: metadata.complete_series_cinematic(self.series, seed)) or seed
            except _WorkerCancelled:
                raise
            except Exception as error:
                log_event('episodes', 'metadata-fallback', error)
            candidates = []
            for value in (getattr(info, 'fallback_backdrop', ''), getattr(info, 'backdrop', ''), getattr(self.series, 'backdrop', ''), getattr(info, 'cover', ''), getattr(self.series, 'icon', '')):
                url = _safe_picon_url(value)
                if url and url not in candidates:
                    candidates.append(url)
            path = ''
            for url in candidates[:4]:
                token.check()
                try:
                    candidate = _cached_picon_path(url) or _download_client_image(self.client, url, _picon_cache_path(url), timeout=CINEMATIC_CANDIDATE_TIMEOUT_SECONDS, token=token)
                    if candidate and _valid_cinematic_fallback_file(candidate):
                        path = candidate
                        break
                except _WorkerCancelled:
                    raise
                except Exception as error:
                    log_event('episodes', 'artwork-fallback', error)
            token.check()
            return (info, path)
        self._episode_art_reader.request(load)

    def _episode_art_loaded(self, value, error):
        if self._closed or error is not None or (not value):
            return
        self._series_detail_info, path = value
        self._render_episode_details()
        if not path or path == self._episode_art_path:
            return
        self._episode_art_path = path

        def loaded(success):
            if not self._closed and path == self._episode_art_path:
                self._episode_show(self['episode_backdrop'], success)
                self._episode_show(self['episode_scrim'], success)
        attach_pixmap(self, 'episode_backdrop', path, key='episode-series-art', on_loaded=loaded, cover_ratio=_cinematic_cover_ratio(path), cover_alignment='center')

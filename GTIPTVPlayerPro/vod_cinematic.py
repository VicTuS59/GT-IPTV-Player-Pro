# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""The discovery skin driven by the lightweight VOD browser."""

import xml.etree.ElementTree as ET

from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from enigma import eTimer

from .browser_categories import GTContentBrowserScreen
from .browser_common import _WorkerToken, _cancel_worker_token, _connect_timer, _scale
from .i18n import _, localized_upper
from .remote_footer import bind_layout_text, prepared_skin
from .scrollbar import update_horizontal_scrollbar
from .series_trailer_ui import add_hint_skin
from .source_loader import SourceLoader
from .typography import clean_dynamic_text, ellipsize_dynamic_text, fit_dynamic_text, set_text_if_changed
from .vod_artwork import cached_poster, load_poster, poster_urls
from .vod_discovery import POSTERS, poster_geometry
from .vod_light import LightVODMethods, ensure_keys
from .vod_render import page_changed


class GTCinematicContentBrowserScreen(GTContentBrowserScreen):
    _vod_layout = "cinematic"

    def _configure_vod(self):
        from .browser_vod import _movie_browser_skin, _series_browser_skin

        kind = self.content_type
        width, height, px = _scale()
        markup = prepared_skin(_movie_browser_skin if kind == 'movie' else _series_browser_skin, True)
        root = ET.fromstring(add_hint_skin(markup, width, height, px, prefix=kind))
        root.set('name', 'GTCinematicContentBrowserScreen')
        hero = root.find("./widget[@name='{}_backdrop']".format(kind))
        hero_rect = tuple(map(int, (hero.get('position') + ',' + hero.get('size')).split(',')))
        hero.set('name', 'light_hero')
        root.find("./widget[@name='{}_backdrop_scrim']".format(kind)).set('name', 'light_hero_caption_back')
        root.find("./widget[@name='{}_video_guard']".format(kind)).set('name', 'light_video_guard')
        self.skin = ET.tostring(root, encoding='unicode')
        self._light_geometry = {'hero': hero_rect, 'font': px}
        self._poster_image_width = min(342, poster_geometry(width, height, px)['tiles'][0]['image'][2])

    def _setup_cinematic_widgets(self):
        kind = self.content_type
        for node in ET.fromstring(self.skin).findall('widget'):
            name = node.get('name')
            if not name or name in self:
                continue
            if 'alphatest' in node.attrib or name == kind + '_poster':
                self[name] = Pixmap()
                if not node.get('pixmap'):
                    self[name].hide()
            else:
                self[name] = Label('')
        self._setup_discovery_widgets()

        self._rail_key = None
        self._rail_page = ()
        self._rail_images = {}
        self._rail_attempted = set()
        self._rail_cached = {}
        self._rail_cache_checked = False
        self._rail_request = None
        self._rail_loader = SourceLoader(eTimer(), _connect_timer, self._rail_ready)
        self._rail_timer = eTimer()
        _connect_timer(self._rail_timer, self._rail_start)
        self._setup_vod_runtime()
        ensure_keys()
        self['cinematic_actions'] = ActionMap(['GTIPTVCinematicActions'], {
            'posterPrevious': lambda: self._rail_move('left'),
            'posterNext': lambda: self._rail_move('right'),
            'posterPreviousRepeat': lambda: self._rail_move('left', repeated=True),
            'posterNextRepeat': lambda: self._rail_move('right', repeated=True),
            'navigationReleased': self._light_release,
            'previousPage': lambda: self._discovery_page(-1),
            'nextPage': lambda: self._discovery_page(1),
            'showSynopsis': self._light_info,
        }, -2)
        self._light_render_rows()
        self._light_waiting()

    _setup_movie_widgets = _setup_cinematic_widgets
    _setup_series_widgets = _setup_cinematic_widgets

    def _light_layout_ready(self):
        bind_layout_text(self)
        self._discovery_navigation_signature = None
        self._light_render_rows()

    def _light_shown(self):
        LightVODMethods._light_shown(self)
        self._light_render_categories()
        self._rail_schedule()

    def _light_hidden(self):
        self._rail_cancel()
        LightVODMethods._light_hidden(self)

    def _light_close(self):
        LightVODMethods._light_close(self)
        self._rail_loader.close()

    def _rail_move(self, direction, repeated=False):
        if self._light_content_focus() and self._rail_page:
            self._light_note_navigation(repeated)
            if repeated:
                self._rail_cancel()
        self._discovery_move(direction)

    def _light_release(self, *, arm=True):
        LightVODMethods._light_release(self, arm=arm)
        self._rail_schedule()

    def _light_render_categories(self, schedule_load=True):
        self._restore_vod_layout_category()
        if schedule_load and self._light_visible:
            self._refresh_discovery_categories()
        else:
            self._render_discovery_navigation()

    def _light_render_rows(self):
        kind = self.content_type
        entries = self._light_field('entries', ())
        start = self._light_field('selected_index', 0) // POSTERS * POSTERS
        page = entries[start:start + POSTERS]
        key = (start, tuple(str(item.stream_id) for item in page))
        rail_key = (self._light_field('loaded_category_id'), self._vod_window_base(kind), key,
                    tuple(getattr(item, 'icon', '') for item in page))
        changed_page = rail_key != self._rail_key
        if changed_page:
            self._rail_cancel()
            self._release_grid_pixmaps(kind)
            self._rail_key, self._rail_page = rail_key, page
            self._rail_images.clear()
            self._rail_attempted.clear()
            self._rail_cached.clear()
            self._rail_cache_checked = False
            setattr(self, '_' + kind + '_poster_page_key', key)
            setattr(self, '_' + kind + '_poster_generation', self._light_field('poster_generation', 0) + 1)
        focused = self._light_content_focus()
        if page_changed(self, kind, key, page, focused):
            selected = self._light_field('selected_index', 0) - start
            for slot in range(POSTERS):
                item = page[slot] if slot < len(page) else None
                tile = kind + '_tile_'
                suffix = '_' + str(slot)
                for element in ('bg', 'inner', 'title'):
                    self._light_show(tile + element + suffix, item is not None)
                self._light_show(tile + 'focus' + suffix, focused and item is not None and selected == slot)
                self._light_show(tile + 'favorite' + suffix, item is not None and self._is_favorite(item))
                if changed_page:
                    self._light_show(tile + 'placeholder' + suffix, item is not None)
                ellipsize_dynamic_text(self[tile + 'title' + suffix], item.name if item is not None else '',
                                       fallback_chars=24)
        if self._rail_request is not None and self._rail_request[0] == self._rail_selected_slot():
            self._rail_cancel()
        self._light_refresh_count()
        self._light_update_caption()
        hint = getattr(self, '_' + kind + '_trailer_hint', None)
        if hint is not None:
            hint.refresh_hint()
        self._rail_schedule()

    def _light_refresh_count(self):
        self._render_discovery_navigation()
        base = self._vod_window_base(self.content_type)
        update_horizontal_scrollbar(self, 'poster_scroll', base + len(self._light_field('entries', ())),
                                    base + self._light_field('selected_index', 0), POSTERS,
                                    self._poster_scroll_geometry)

    def _light_update_caption(self):
        if self._error or self._loading or not self._entries or not self._light_field('entries', ()):
            self._light_caption_text = None
            return
        text = localized_upper(self._entries[self.selected_index].name)
        widget = self[self.content_type + '_rail_caption']
        if text != getattr(self, '_light_caption_text', None) or not widget.getText():
            self._light_caption_text = text
            ellipsize_dynamic_text(widget, text, fallback_chars=60)

    def _light_waiting(self, item=None):
        LightVODMethods._light_waiting(self, item)
        if item is None:
            set_text_if_changed(self[self.content_type + '_people'], '')
            if self.content_type == 'movie':
                set_text_if_changed(self['movie_genre'], '')

    def _light_schedule_detail(self, immediate=False):
        first_title = (self._light_visible and self._light_visual_identity is None
                       and not self._light_navigation_held)
        LightVODMethods._light_schedule_detail(self, immediate=immediate or first_title)

    def _light_set_plot(self, text):
        text = clean_dynamic_text(text)
        if text != self._light_plot_text:
            self._light_plot_text = text
            fit_dynamic_text(self[self.content_type + '_plot'], text, max_lines=2,
                             min_size=self._light_geometry['font'](21), fallback_chars=125)

    def _light_apply_detail(self, info, *args, **kwargs):
        LightVODMethods._light_apply_detail(self, info, *args, **kwargs)
        if not self._light_field('entries', ()):
            return
        director, cast = (getattr(info, field, '') for field in ('director', 'cast'))
        credits = ' · '.join(label.format(value) for label, value in
                             ((_("Director: {}"), director), (_("Cast: {}"), cast)) if value)
        ellipsize_dynamic_text(self[self.content_type + '_people'], credits, fallback_chars=70)
        if self.content_type == 'movie':
            ellipsize_dynamic_text(self['movie_genre'], getattr(info, 'genre', '') or '', fallback_chars=55)
        self._rail_schedule()

    def _rail_selected_slot(self):
        return self._light_field('selected_index', 0) % POSTERS

    def _rail_cancel(self):
        self._rail_timer.stop()
        if self._rail_request is not None:
            slot, token = self._rail_request
            self._rail_request = None
            self._rail_attempted.discard(slot)
            _cancel_worker_token(token)
            self._rail_loader.cancel()

    def _rail_next_slot(self):
        if (self._closed or not self._light_visible or self._light_field('loading', False)
                or self._light_navigation_held or self._rail_request is not None
                or self._light_detail_identity is None
                or self._light_detail_identity[-1] != self._light_field('generation')):
            return None
        selected = self._rail_selected_slot()
        for slot in self._rail_cached:
            if slot != selected:
                return slot
        return next((slot for slot in range(len(self._rail_page))
                     if slot != selected and slot not in self._rail_attempted and slot not in self._rail_images), None)

    def _rail_schedule(self):
        if self._rail_next_slot() is not None:
            self._rail_timer.start(45, True)

    def _rail_start(self):
        slot = self._rail_next_slot()
        if slot is None:
            return
        if not self._rail_cache_checked:
            self._rail_check_cache()
            return
        found = self._rail_cached.pop(slot, None)
        if found is not None:
            self._rail_paint(slot, *found)
            self._rail_schedule()
            return
        item, kind, key = self._rail_page[slot], self.content_type, self._rail_key
        token = _WorkerToken(5)
        self._rail_attempted.add(slot)
        self._rail_request = slot, token

        def load():
            found = load_poster(self.client, self._metadata_client, kind, item, None,
                                poster_urls(item, None), token, width=self._poster_image_width, priority=False)
            return key, slot, found

        self._rail_loader.request(load)

    def _rail_check_cache(self):
        # A missing first poster must never delay cached posters further right.
        token = _WorkerToken(2)
        key, page, selected = self._rail_key, self._rail_page, self._rail_selected_slot()
        width = self._poster_image_width
        self._rail_request = None, token

        def load():
            hits = {}
            for slot, item in enumerate(page):
                if slot != selected:
                    found = cached_poster(poster_urls(item, None), token, width)
                    if found is not None:
                        hits[slot] = found
            return key, None, hits

        self._rail_loader.request(load)

    def _rail_ready(self, result, error):
        checking_cache = self._rail_request is not None and self._rail_request[0] is None
        self._rail_request = None
        if self._closed or not self._light_visible:
            return
        if checking_cache:
            self._rail_cache_checked = True
        if error is None and result is not None:
            key, slot, found = result
            if key == self._rail_key and found:
                if slot is None:
                    self._rail_cached.update(found)
                    self._rail_cached.pop(self._rail_selected_slot(), None)
                else:
                    self._rail_paint(slot, *found)
        self._rail_schedule()

    def _rail_paint(self, slot, url, path):
        if not 0 <= slot < len(self._rail_page):
            return
        kind = self.content_type
        self._rail_images[slot] = url, path
        getattr(self, '_apply_' + kind + '_tile_poster')(
            slot, path, str(self._rail_page[slot].stream_id), self._light_field('poster_generation'))

    def _light_cover(self, item, path, url, info=None):
        slot = self._rail_selected_slot()
        if slot < len(self._rail_page) and str(self._rail_page[slot].stream_id) == str(item.stream_id):
            cached = self._rail_cached.pop(slot, None)
            if cached is not None:
                self._rail_paint(slot, *cached)
            found = self._rail_images.get(slot)
            if found:
                self._light_cancel_cover()
                self._light_cover_identity = self._light_item_identity(item)
                self._light_poster_url = found[0]
                return
        LightVODMethods._light_cover(self, item, path, url, info)

    def _apply_detail_poster(self, kind, item, path):
        slot = self._rail_selected_slot()
        if slot >= len(self._rail_page) or str(self._rail_page[slot].stream_id) != str(item.stream_id):
            self._light_render_rows()
        self._rail_paint(slot, self._light_poster_url, path)

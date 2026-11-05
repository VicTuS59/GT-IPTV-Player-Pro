# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Movie PLAY hint with the same artwork, geometry and dialog as series."""
from Components.ActionMap import ActionMap
from Components.Label import Label
from Components.Pixmap import Pixmap
from .i18n import _
from .typography import fit_dynamic_text
from .series_trailer_ui import (SeriesTrailerHint, SeriesTrailerScreen,
    PLAY_KEY_CONTEXT, _ensure_play_keys)


class MovieTrailerHint(SeriesTrailerHint):
    def __init__(self, screen):
        self.screen = screen
        screen['movie_trailer_hint_bg'] = Label('') if getattr(screen, '_light_vod_enabled', False) else Pixmap()
        screen['movie_trailer_hint_text'] = Label(_('Trailer'))
        _ensure_play_keys()
        screen['movie_trailer_actions'] = ActionMap([PLAY_KEY_CONTEXT],
            {'playTrailer': self.start}, -2)
        screen.onLayoutFinish.append(self.refresh_hint)
        screen.onShown.append(self.refresh_hint)
        screen.onClose.append(self.close)

    def refresh_hint(self):
        screen = self.screen
        if screen is None:
            return
        visible = bool(not getattr(screen, '_closed', False)
            and getattr(screen, '_movie_entries', None)
            and not getattr(screen, '_movie_loading', False)
            and not getattr(screen, '_movie_search_child_open', False)
            and getattr(screen, '_movie_focus', 'categories') == 'posters')
        if getattr(self, '_hint_visible', None) == visible:
            return
        self._hint_visible = visible
        names = ('movie_trailer_hint_bg', 'movie_trailer_hint_text')
        if getattr(screen, '_light_vod_enabled', False):
            names += ('light_play_key',)
        for name in names:
            screen[name].show() if visible else screen[name].hide()
        if visible:
            from .main import _scale
            px = _scale()[2]
            fit_dynamic_text(screen['movie_trailer_hint_text'], _('Trailer'), max_lines=2,
                preferred_size=px(25), min_size=max(8, px(14)))

    def start(self):
        screen = self.screen
        if (screen is None or getattr(screen, '_closed', False)
                or getattr(screen, '_movie_loading', False)
                or getattr(screen, '_movie_search_child_open', False)
                or getattr(screen, '_movie_search_keyboard_open', False)
                or getattr(screen, '_movie_focus', '') != 'posters'):
            return
        entries = getattr(screen, '_movie_entries', ())
        index = getattr(screen, '_movie_selected_index', 0)
        if not 0 <= index < len(entries):
            return
        screen._open_movie_trailer(entries[index], screen._cached_grid_detail('movie'))


class MovieTrailerScreen(SeriesTrailerScreen):
    def _service(self):
        from .movie_trailer import service_for
        return service_for(self._metadata)

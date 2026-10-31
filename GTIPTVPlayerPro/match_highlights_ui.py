# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Match-scoped verified results, reusing the unchanged native YouTube player."""
from .auxiliary_presentation import skin_after as aux_skin_after, refresh_after as aux_refresh_after, setup as setup_aux
from .matches_text import tr
from .youtube_ui import GTYouTubeSearchScreen
from .web_youtube import WEB_YOUTUBE
from .match_highlights_search import MatchHighlightsService
from .remote_footer import footer_item,set_remote_footer

FOOTER=tuple(footer_item(key,action) for key,action in (
    ('up_down','select'),('left_right','page'),('ok','play'),('blue','retry'),
    ('yellow','quality'),('red','refresh'),('exit','back')))


class GTMatchHighlightsScreen(GTYouTubeSearchScreen):
    def __init__(self,session,query,match=None,search_service=None):
        self._match_query=query;self._seed_query=query
        self._highlights_service=MatchHighlightsService(match,search_service if search_service is not None else WEB_YOUTUBE)
        GTYouTubeSearchScreen.__init__(self,session,search_service=self._highlights_service)
        self.setTitle(tr('Match highlights'))
        self.onLayoutFinish.append(self._set_highlights_footer)

    @aux_refresh_after
    def _set_highlights_footer(self):
        set_remote_footer(self,FOOTER)

    @aux_refresh_after
    def _render_preview(self):
        GTYouTubeSearchScreen._render_preview(self)
        items=list(FOOTER)
        current=getattr(self,'_remote_footer_items',())
        if len(current)>2:items[2]=current[2]
        set_remote_footer(self,items)

    def _open_initial_search(self):
        if self._closed:return
        query,self._match_query=self._match_query,''
        if query:self._query_entered(query)

    def _verified_status(self):
        response=self._pages.get(self._page) or {}
        if response and not self._busy and not response.get('results') and not response.get('error'):
            self._fit_youtube_caption('status',tr('No date-verified highlights found for this match.'),22,minimum=11)

    @aux_refresh_after
    def _render(self):
        GTYouTubeSearchScreen._render(self)
        self._fit_youtube_caption('section',tr('Match highlights'),27,minimum=13)
        self._verified_status()

    def _search_update(self,response):
        GTYouTubeSearchScreen._search_update(self,response)
        self._verified_status()

    def retry(self):
        if not self._closed and not self._busy:self._query_entered(self._seed_query)

    def open_keyboard(self):self.retry()

    def open_history(self):self.retry()

    def _stop(self):
        GTYouTubeSearchScreen._stop(self)
        self._highlights_service.close()

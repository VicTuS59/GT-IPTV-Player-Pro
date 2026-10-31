# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""On-demand movie trailers; share the existing 32-result trailer cache."""
import time
from .metadata import TMDbMetadataClient, MetadataError
from . import series_trailer as core

MAX_SECONDS = 30


def movie_names(detail):
    values = [detail.get('original_title'), detail.get('title')]
    values.extend(v.get('title') for v in (detail.get('alternative_titles') or {}).get('titles', [])[:40] if isinstance(v, dict))
    values.extend((v.get('data') or {}).get('title') for v in
        (detail.get('translations') or {}).get('translations', [])[:80] if isinstance(v, dict))
    result, seen = [], set()
    for value in values:
        text = ' '.join(str(value or '').split())[:160]
        key = ' '.join(core.words(text))
        if len(key) >= 2 and key not in seen:
            seen.add(key); result.append(text)
    return result[:64]


class MovieTrailerService:
    def __init__(self, metadata, search=None, clock=None):
        self.metadata, self.search = metadata, search
        self.clock = clock or time.monotonic

    def _identity(self, item, info):
        title = core.clean_trailer_title(getattr(item, 'name', '') or getattr(info, 'title', ''))
        year = getattr(item, 'year', '') or getattr(info, 'year', '')
        seen = set()
        def fetch(ident):
            seen.add(str(ident))
            try:
                detail = self.metadata._request_json('/movie/' + str(ident),
                    dict(core.video_parameters(), append_to_response='alternative_titles,translations,videos'), language='en-US')
            except MetadataError as error:
                if error.http_status != 404: raise
                return None
            if str(detail.get('id') or '') != str(ident): return None
            identity = self.metadata._poster_identity(detail, 'movie')
            for alias in movie_names(detail):
                if self.metadata._provider_identity_matches('movie', title, year,
                        dict(identity, title=alias, original_title=alias)):
                    return detail
            return None
        for ident in (getattr(info, 'tmdb_id', ''), getattr(item, 'tmdb_id', '')):
            if self.metadata._valid_tmdb_id(ident) and str(ident) not in seen:
                detail = fetch(ident)
                if detail: return detail
        if len(core.words(title)) == 0: return {}
        params = {'query':title, 'page':1, 'include_adult':'false'}
        if str(year).isdigit() and len(str(year)) == 4: params['primary_release_year'] = str(year)
        payload = self.metadata._request_json('/search/movie', params, language='en-US')
        values = [v for v in (payload.get('results') or [])[:20] if isinstance(v, dict)]
        values.sort(key=lambda v:self.metadata._candidate_score(title, str(year), v, 'movie'), reverse=True)
        for value in values[:3]:
            ident = value.get('id')
            if self.metadata._valid_tmdb_id(ident) and str(ident) not in seen:
                detail = fetch(ident)
                if detail: return detail
        return {}

    def _official(self, videos, target):
        from .series_trailer_sources import general_title_allowed
        values = [v for v in videos[:150] if isinstance(v, dict) and v.get('official') is True
            and v.get('site') == 'YouTube' and v.get('type') in ('Trailer', 'Teaser')
            and core.VIDEO_ID.fullmatch(str(v.get('key') or ''))
            and general_title_allowed(v.get('name') or '', target)]
        return max(values, key=lambda v:(v.get('type') == 'Trailer',
            v.get('iso_639_1') == target['original_language'], str(v.get('published_at') or ''))) if values else None

    def _find(self, detail, target, cancel, deadline):
        def found(video):
            return {'state':'found', 'kind':'movie', 'id':video['key'],
                'title':video.get('name') or target['names'][0], 'source':'tmdb_movie'}
        video = self._official((detail.get('videos') or {}).get('results') or [], target)
        if video: return found(video)
        core.check(cancel, deadline)
        try:
            payload = self.metadata._request_json('/movie/{}/videos'.format(detail['id']),
                core.video_parameters(target['original_language']), language='en-US')
        except MetadataError as error:
            if error.http_status != 404: raise
            payload = {}
        video = self._official(payload.get('results') or [], target)
        if video: return found(video)
        from .series_trailer_sources import choose_general
        if self.search is None:
            from .web_youtube import trailer_search
            search = trailer_search
        else: search = self.search
        names = target['names']
        queries = ['{} {} official trailer'.format(names[0], target['first_air_date'][:4])]
        alias = next((n for n in names[1:] if core.words(n) != core.words(names[0])), '')
        if alias: queries.append('{} {} official trailer'.format(alias, target['first_air_date'][:4]))
        for query in queries:
            core.check(cancel, deadline)
            if deadline - self.clock() < 7: break
            video = choose_general(search(query[:160], cancel), target)
            core.check(cancel, deadline)
            if video:
                return {'state':'found', 'kind':'movie', 'id':video['id'],
                    'title':video['title'], 'source':'youtube_movie'}
        return {'state':'not_found'}

    def find(self, item, info=None, cancel=None):
        deadline = self.clock() + MAX_SECONDS
        try:
            core.check(cancel, deadline)
            if not self.metadata.available: return {'state':'error'}
            with self.metadata.language_scope('en-US'), self.metadata.request_scope(cancel, deadline):
                detail = self._identity(item, info)
                core.check(cancel, deadline)
                if not detail or not movie_names(detail): return {'state':'not_found'}
                key = ('movie', str(detail['id']))
                with core._LOCK:
                    cached = core._CACHE.get(key)
                    if cached and self.clock() < cached[0]:
                        core._CACHE.move_to_end(key); return dict(cached[1])
                target = {'names':movie_names(detail), 'channel_names':movie_names(detail)[:2],
                    'issuers':[str(v.get('name') or '') for v in detail.get('production_companies', []) if isinstance(v, dict)],
                    'season':1, 'first_air_date':str(detail.get('release_date') or ''),
                    'air_date':'', 'original_language':detail.get('original_language') or 'en'}
                result = self._find(detail, target, cancel, deadline)
                core.check(cancel, deadline)
                if result['state'] in ('found', 'not_found'):
                    with core._LOCK:
                        ttl = core.SUCCESS_SECONDS if result['state'] == 'found' else core.EMPTY_SECONDS
                        core._CACHE[key] = (self.clock()+ttl, dict(result)); core._CACHE.move_to_end(key)
                        while len(core._CACHE) > core.MAX_CACHE: core._CACHE.popitem(last=False)
                return result
        except core.TrailerCancelled:
            return {'state':'cancelled'}
        except Exception:
            return {'state':'cancelled' if cancel is not None and cancel.is_set() else 'error'}


def service_for(metadata):
    client = TMDbMetadataClient(api_key=getattr(metadata, 'api_key', ''), enabled=True,
        language='en-US', timeout=5, cache_path=getattr(metadata, 'cache_path', ''))
    return MovieTrailerService(client)

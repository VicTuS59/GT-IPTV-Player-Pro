# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded match-only search; the existing YouTube player is delegated unchanged."""
import json
import secrets
import threading
import time
from collections import Counter, OrderedDict
from concurrent.futures import ThreadPoolExecutor,as_completed,TimeoutError
from itertools import zip_longest
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler,Request,build_opener

from .flashscore_video import fetch_flashscore_videos
from .i18n import metadata_language
from .settings import load_player_settings
from .web_errors import WebServiceError
from .web_youtube import _search_page,_results,SEARCH_USER_AGENT
from .match_highlights import alternate_search, fold, international_search, league_key, match_date
from .match_highlights_filter import candidate_ok,metadata_from_page,verify,VIDEO_ID
from .matches import Cancelled as ScoresCancelled, ScoresError, _shared_scores_client, parse_match, text
from .matches_sources import fetch_flashscore_kickoff
from .uefa_highlights import eligible as uefa_eligible, find_highlight as find_uefa_highlight

MAX_CANDIDATES=12
SEARCH_BUDGET=45
METADATA_LIMIT=3*1024*1024
FLASHSCORE_VIDEO_BUDGET=10
LOCAL_SEARCH_BUDGET = 12
LOCAL_MAX_CANDIDATES = 6


class _YouTubeRedirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        parts=urlsplit(newurl)
        if parts.scheme!='https' or parts.hostname not in ('www.youtube.com','youtube.com') or parts.username or parts.password:
            raise ValueError('Unexpected metadata redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


class Cancelled(Exception):pass


class MetadataReader:
    def __init__(self,opener=None,clock=time.monotonic):
        self._open=opener or build_opener(_YouTubeRedirect()).open
        self._clock=clock;self._lock=threading.Lock();self._cache=OrderedDict()
    def read(self,video_id,cancel,deadline):
        if not VIDEO_ID.fullmatch(video_id):raise ValueError('Invalid video ID')
        if cancel.is_set() or self._clock()>=deadline:raise Cancelled()
        with self._lock:
            entry=self._cache.get(video_id)
            if entry and 0<=self._clock()-entry[0]<300:return dict(entry[1])
        request=Request('https://www.youtube.com/watch?v='+video_id+'&hl=en',headers={
            'User-Agent':SEARCH_USER_AGENT,'Accept':'text/html','Accept-Language':'en-US,en;q=0.8','Accept-Encoding':'identity'})
        with self._open(request,timeout=max(.1,min(4.,deadline-self._clock()))) as response:
            body=bytearray()
            while True:
                if cancel.is_set() or self._clock()>=deadline:raise Cancelled()
                chunk=response.read(min(65536,METADATA_LIMIT+1-len(body)))
                if not chunk:break
                body.extend(chunk)
                if len(body)>METADATA_LIMIT:raise ValueError('Metadata response too large')
        if cancel.is_set() or self._clock()>=deadline:raise Cancelled()
        result=metadata_from_page(bytes(body).decode('utf-8','replace'),video_id)
        with self._lock:
            self._cache[video_id]=(self._clock(),dict(result));self._cache.move_to_end(video_id)
            while len(self._cache)>32:self._cache.popitem(last=False)
        return result


def public_candidates(query,language,cancel,deadline):
    remaining = deadline - time.monotonic()
    if cancel.is_set() or remaining <= 0:
        raise Cancelled()
    data, unused_config = _search_page(query, language, timeout=max(0.1, min(9, remaining)))
    if cancel.is_set() or time.monotonic()>=deadline:raise Cancelled()
    return _results(data,set())


def _match_for_search(match, cancel):
    if not match.league.startswith('flashscore:'):
        return match, False
    try:
        kickoff = fetch_flashscore_kickoff(match, cancel)
        return match._replace(kickoff=kickoff), True
    except ScoresCancelled as error:
        raise Cancelled() from error
    except HTTPError as error:
        error.close()
    except (ScoresError, OSError, ValueError):
        pass
    return match, False


def _espn_season(match, cancel):
    """Require independent season evidence for a precisely matched Série B fixture."""
    day = match_date(match)
    try:
        events = _shared_scores_client().competition_events('bra.2', day, cancel)
    except ScoresCancelled as error:
        raise Cancelled() from error
    except HTTPError as error:
        error.close()
        return None
    except (ScoresError, OSError, ValueError):
        return None

    expected = (match.kickoff, fold(match.home), fold(match.away), match.home_score, match.away_score)
    for event in events:
        game = parse_match(event)
        if game is None or game.league != 'bra.2' or game.category != 'finished':
            continue
        identity = (game.kickoff, fold(game.home), fold(game.away), game.home_score, game.away_score)
        season = event.get('season')
        if identity == expected and isinstance(season, dict) and season.get('year') == day.year:
            return day.year
    return None


class MatchHighlightsService:
    # The parent screen can render a cached search before verification starts.
    recent_search = None

    def __init__(self,match,backend,candidate_source=None,reader=None,registry=None,clock=time.monotonic):
        self.match=match;self.backend=backend;self._source=candidate_source or public_candidates
        self._reader=reader or MetadataReader();self._clock=clock
        self._registry=registry if registry is not None else json.loads(Path(__file__).with_name('match_highlights_publishers.json').read_text(encoding='utf-8'))
        self._lock=threading.RLock();self._jobs=OrderedDict();self._contexts=OrderedDict();self._closed=False
    def __getattr__(self,name):return getattr(self.backend,name)
    def _quality(self):return str(load_player_settings().youtube_resolution)
    def _queries(self,query,language,match):
        # Search candidates only. Every video still has to pass metadata checks.
        original_day, verified_day = match_date(self.match), match_date(match)
        if original_day != verified_day:
            query = query.replace(' {} '.format(original_day), ' {} '.format(verified_day), 1)
        return tuple(dict.fromkeys(((query[:120], language), alternate_search(match, language))))

    def _flashscore_result(self, cancel, deadline):
        try:
            match, season, ids = fetch_flashscore_videos(self.match, cancel)
        except ScoresCancelled as error:
            raise Cancelled() from error
        except HTTPError as error:
            error.close()
            return None, None, None
        except (ScoresError, OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
            return None, None, None

        stats = {
            'candidates': len(ids), 'inspected': 0, 'accepted': 0, 'rejected': {},
            'source_errors': 0, 'metadata_errors': 0,
        }

        for video_id in ids:
            if cancel.is_set():
                raise Cancelled()
            if self._clock() >= deadline:
                break
            stats['inspected'] += 1
            try:
                metadata = self._reader.read(video_id, cancel, deadline)
            except Cancelled:
                if cancel.is_set():
                    raise
                break
            except HTTPError as error:
                error.close()
                stats['metadata_errors'] += 1
                continue
            except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
                stats['metadata_errors'] += 1
                continue
            seconds = metadata.get('duration_seconds', '')
            duration = ''
            if isinstance(seconds, str) and seconds.isascii() and seconds.isdecimal() and len(seconds) <= 5:
                duration = '{}:{:02d}'.format(*divmod(int(seconds), 60))
            entry = {
                'id': video_id,
                'title': metadata.get('title', ''),
                'thumbnail': 'https://i.ytimg.com/vi/{}/hqdefault.jpg'.format(video_id),
                'duration': duration,
                'published': 'Flashscore',
            }
            item, reason = verify(entry, metadata, match, self._registry, flashscore_season=season)
            if item:
                item['title'] = text(item['title'], 180)
                stats['accepted'] = 1
                return item, match, stats
            stats['rejected'][reason] = stats['rejected'].get(reason, 0) + 1
        return None, match, None

    def _search_youtube(self, queries, match, kickoff_verified, cancel, deadline, *, local_names=()):
        candidate_lists = []
        source_errors = 0
        for term, search_language in queries:
            if cancel.is_set() or self._clock() >= deadline:
                raise Cancelled()
            try:
                values = self._source(term, search_language, cancel, deadline)
            except Cancelled:
                raise
            except Exception:
                source_errors += 1
                continue
            candidate_lists.append([
                item for item in values[:120] if isinstance(item, dict)
                and candidate_ok(item, match, local_names=local_names)
            ])
        # Give both searches inspection slots before applying the global cap.
        if match.league.startswith('flashscore:'):
            ranked = (item for row in zip_longest(*candidate_lists) for item in row if item is not None)
        else:
            ranked = (item for rows in candidate_lists for item in rows)
        entries = {}
        for item in ranked:
            entries.setdefault(item['id'], item)
        # Channel names only influence the bounded inspection order. They never
        # establish official status; final priority uses the real channel ID/URL.
        publisher_names = {
            fold(name)
            for publisher in self._registry
            for name in (publisher.get('name', ''), *publisher.get('search_names', ()))
            if name
        }
        limit = LOCAL_MAX_CANDIDATES if local_names else MAX_CANDIDATES
        candidates = sorted(
            entries.values(),
            key=lambda item: fold(item.get('channel', '')) not in publisher_names,
        )[:limit]
        espn_season = None
        if candidates and kickoff_verified and league_key(match.league) == 'bra.2':
            espn_season = _espn_season(match, cancel)
        verified=[];reasons={};metadata_errors=0
        pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='GT-Match-Date')
        futures={pool.submit(self._reader.read,item['id'],cancel,deadline):item for item in candidates}
        try:
            for future in as_completed(futures,timeout=max(.01,deadline-self._clock())):
                if cancel.is_set() or self._clock()>=deadline:raise Cancelled()
                try:metadata=future.result()
                except Cancelled:raise
                except Exception:metadata_errors+=1;continue
                item, reason = verify(futures[future], metadata, match, self._registry,
                                      espn_season=espn_season, local_names=local_names)
                reasons[reason]=reasons.get(reason,0)+1
                if item:verified.append(item)
        except TimeoutError:
            metadata_errors+=sum(not f.done() for f in futures)
        finally:
            for future in futures:future.cancel()
            pool.shutdown(wait=False)
        if cancel.is_set():raise Cancelled()
        order={value['id']:index for index,value in enumerate(candidates)}
        verified.sort(key=lambda value:(not value['official'],order[value['id']]))
        return verified,{'candidates':len(entries),'inspected':len(candidates),'accepted':len(verified),
            'rejected':reasons,'source_errors':source_errors,'metadata_errors':metadata_errors}

    def _collect(self, query, language, cancel):
        deadline = self._clock() + SEARCH_BUDGET
        match = None
        if self.match.league.startswith('flashscore:'):
            result, match, stats = self._flashscore_result(
                cancel, min(deadline, self._clock() + FLASHSCORE_VIDEO_BUDGET),
            )
            if result is not None:
                if cancel.is_set():
                    raise Cancelled()
                return [result], stats, ''
        kickoff_verified = match is not None
        if match is None:
            match, kickoff_verified = _match_for_search(self.match, cancel)

        queries = self._queries(query, language, match)
        try:
            results, stats = self._search_youtube(queries, match, kickoff_verified, cancel, deadline)
        except Cancelled:
            if cancel.is_set() or not uefa_eligible(match):
                raise
            results, stats = [], {'candidates': 0, 'inspected': 0, 'accepted': 0, 'rejected': {},
                                  'source_errors': len(queries), 'metadata_errors': 0}
        if not results and stats['source_errors'] < len(queries) and self._clock() < deadline:
            extra = international_search(match, language)
            if extra and extra[0] not in queries:
                local_query, local_names = extra
                try:
                    results, local_stats = self._search_youtube(
                        (local_query,), match, kickoff_verified, cancel,
                        min(deadline, self._clock() + LOCAL_SEARCH_BUDGET), local_names=local_names,
                    )
                except Cancelled:
                    if cancel.is_set():
                        raise
                else:
                    queries += (local_query,)
                    for field in ('candidates', 'inspected', 'source_errors', 'metadata_errors'):
                        stats[field] += local_stats[field]
                    stats['accepted'] = len(results)
                    stats['rejected'] = dict(Counter(stats['rejected']) + Counter(local_stats['rejected']))

        if cancel.is_set():
            raise Cancelled()
        if not results and uefa_eligible(match):
            try:
                uefa = find_uefa_highlight(match, cancel)
            except ScoresCancelled as error:
                raise Cancelled() from error
            if cancel.is_set():
                raise Cancelled()
            if uefa:
                stats['accepted'] = 1
                return [uefa], stats, ''

        unavailable = stats['source_errors'] == len(queries) or (
            stats['inspected'] and stats['metadata_errors'] == stats['inspected']
        )
        return results, stats, 'youtube_unavailable' if not results and unavailable else ''

    def search_async(self,query,cursor='',language=None):
        if cursor:raise WebServiceError('youtube_page_expired','Search again to see more results',410)
        if not isinstance(query,str) or not 2<=len(query)<=120 or self.match is None:
            raise WebServiceError('invalid_query','Invalid match search',400)
        with self._lock:
            if self._closed:raise WebServiceError('cancelled','Search cancelled',409)
            for old in self._jobs.values():old['cancel'].set()
            if sum(not old['done'].is_set() for old in self._jobs.values())>=2:
                raise WebServiceError('youtube_page_busy','Results are loading',409)
            while len(self._jobs)>=4:self._jobs.popitem(last=False)
            token=secrets.token_urlsafe(24)
            response={'results':[],'page':1,'quality':self._quality(),'context':'','cursor':'','has_more':False,
                'pending':True,'pagination_warning':'','search_token':token}
            job={'cancel':threading.Event(),'done':threading.Event(),'response':response};self._jobs[token]=job
        def work():
            try:
                results,stats,error=self._collect(query,language or metadata_language(),job['cancel'])
                if job['cancel'].is_set():return
                context=secrets.token_urlsafe(24) if results and not results[0].get('playback_url') else ''
                final=dict(response,results=results,pending=False,context=context,error=error,verification=stats)
                with self._lock:
                    if self._closed or job['cancel'].is_set():return
                    if context:
                        # Existing playback/autoplay sees ONLY this verified list.
                        # No unfiltered continuation may reach the sequence.
                        with self.backend._lock:
                            self.backend._lists[context]={'created':time.monotonic(),'response':dict(final),
                                'query':query,'language':language or metadata_language()}
                        self._contexts[context]={v['id'] for v in results}
                        while len(self._contexts)>8:
                            oldest,unused=self._contexts.popitem(last=False)
                            with self.backend._lock:self.backend._lists.pop(oldest,None)
                    job['response']=final
            except Cancelled:
                with self._lock:job['response']=dict(response,pending=False,error='cancelled')
            except Exception:
                with self._lock:job['response']=dict(response,pending=False,error='youtube_unavailable')
            finally:job['done'].set()
        worker=threading.Thread(target=work,name='GT-Verified-Match-Highlights');worker.daemon=True;worker.start()
        return self.search_status(token)
    def search_status(self,token):
        with self._lock:
            job=self._jobs.get(token) if isinstance(token,str) else None
            if job is None:raise WebServiceError('youtube_page_expired','Search again to see more results',410)
            return dict(job['response'],quality=self._quality(),results=[dict(v) for v in job['response']['results']])
    def cancel_search(self,token):
        with self._lock:
            job=self._jobs.get(token)
            if job is not None:
                job['cancel'].set();job['response']=dict(job['response'],pending=False,error='cancelled')
        return {'cancelled':job is not None}
    def play(self,video_id,title,context='',**kwargs):
        with self._lock:
            if self._closed or video_id not in self._contexts.get(context,set()):
                raise WebServiceError('invalid_video','Invalid match highlight selection',400)
        return self.backend.play(video_id,title,context,**kwargs)
    def close(self):
        with self._lock:
            self._closed=True
            for job in self._jobs.values():job['cancel'].set()
            with self.backend._lock:
                for context in self._contexts:self.backend._lists.pop(context,None)
            self._contexts.clear()

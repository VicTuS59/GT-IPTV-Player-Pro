# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded match-only search; the existing YouTube player is delegated unchanged."""
import datetime as dt
import json
import secrets
import threading
import time
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor,as_completed,TimeoutError
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler,Request,build_opener

from .i18n import metadata_language
from .settings import load_player_settings
from .web_api import WebServiceError
from .web_youtube import _search_page,_results,SEARCH_USER_AGENT
from .match_highlights_filter import candidate_ok,metadata_from_page,verify,VIDEO_ID,fold

MAX_CANDIDATES=12
SEARCH_BUDGET=45
METADATA_LIMIT=3*1024*1024


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
    if cancel.is_set() or time.monotonic()>=deadline:raise Cancelled()
    data,unused_config=_search_page(query,language)
    if cancel.is_set() or time.monotonic()>=deadline:raise Cancelled()
    return _results(data,set())


class MatchHighlightsService:
    def __init__(self,match,backend,candidate_source=None,reader=None,registry=None,clock=time.monotonic):
        self.match=match;self.backend=backend;self._source=candidate_source or public_candidates
        self._reader=reader or MetadataReader();self._clock=clock
        self._registry=registry if registry is not None else json.loads(Path(__file__).with_name('match_highlights_publishers.json').read_text(encoding='utf-8'))
        self._lock=threading.RLock();self._jobs=OrderedDict();self._contexts=OrderedDict();self._closed=False
    def __getattr__(self,name):return getattr(self.backend,name)
    def _quality(self):return str(load_player_settings().youtube_resolution)
    def _queries(self,query):
        # Search candidates only. Every video still has to pass metadata checks.
        first=query[:120]
        keyword='highlights -efootball -fifa -gameplay -simulation'
        suffix=dt.datetime.fromtimestamp(self.match.kickoff).strftime('%Y-%m-%d')+' '+keyword
        room=120-len(suffix)-2
        home=self.match.home[:room//2];away=self.match.away[:room-len(home)]
        second=' '.join((home,away,suffix))
        return tuple(dict.fromkeys((first,second)))
    def _collect(self,query,language,cancel):
        deadline=self._clock()+SEARCH_BUDGET;entries={};source_errors=0
        for term in self._queries(query):
            if cancel.is_set() or self._clock()>=deadline:raise Cancelled()
            try:values=self._source(term,language,cancel,deadline)
            except Cancelled:raise
            except Exception:source_errors+=1;continue
            for item in values[:120]:
                if isinstance(item,dict) and candidate_ok(item,self.match):entries.setdefault(item['id'],item)
        # Channel names only influence the bounded inspection order. They never
        # establish official status; final priority uses the real channel ID/URL.
        def likely_official(item):
            name=fold(item.get('channel',''))
            return not any(name==fold(v['name']) for v in self._registry)
        candidates=sorted(entries.values(),key=likely_official)[:MAX_CANDIDATES]
        verified=[];reasons={};metadata_errors=0
        pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='GT-Match-Date')
        futures={pool.submit(self._reader.read,item['id'],cancel,deadline):item for item in candidates}
        try:
            for future in as_completed(futures,timeout=max(.01,deadline-self._clock())):
                if cancel.is_set() or self._clock()>=deadline:raise Cancelled()
                try:metadata=future.result()
                except Cancelled:raise
                except Exception:metadata_errors+=1;continue
                item,reason=verify(futures[future],metadata,self.match,self._registry)
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
        error='youtube_unavailable' if not verified and (source_errors==len(self._queries(query)) or candidates and metadata_errors==len(candidates)) else ''
        return verified,{'candidates':len(entries),'inspected':len(candidates),'accepted':len(verified),
            'rejected':reasons,'source_errors':source_errors,'metadata_errors':metadata_errors},error
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
                context=secrets.token_urlsafe(24) if results else ''
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

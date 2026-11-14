# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded selected-result artwork; network and disk work stays off the GUI."""
import threading
from enigma import eTimer
from .background import attach_pixmap
from .content import ContentError
from .browser_common import (_WorkerToken,_connect_timer,
    _safe_picon_url,_cached_picon_path,_picon_cache_path,_download_client_image)
from .vod_grid import LatestPosterWorkers

DWELL_MS=250
TIMEOUT_SECONDS=8

class SearchPoster(object):
    def __init__(self,screen):
        self.screen=screen
        self.closed=False
        self.key=None
        self.generation=0
        self.token=None
        self.workers=None
        self.mailbox=None
        self.lock=threading.Lock()
        self.dwell=eTimer();self.poll=eTimer()
        _connect_timer(self.dwell,self.start)
        _connect_timer(self.poll,self.tick)

    def target(self):
        s=self.screen
        if self.closed or getattr(s, '_closed', True) or not s._results:return None
        try:
            item=s._results[s.selected_index]
        except ContentError as error:
            if error.reason == ContentError.CANCELLED:
                return None
            raise
        url=_safe_picon_url(getattr(item,'icon',''))
        return (s.content_type,str(item.stream_id),url) if url else None

    def _clear_decode(self):
        loader=getattr(self.screen,'_gt_pixmap_loaders',{}).pop('r102-search-poster',None)
        if loader is not None:loader.close()
        self.screen['r102_search_poster'].hide()
        self.screen['r102_poster_empty'].show()

    def schedule(self):
        key=self.target()
        if key==self.key:return
        self.cancel()
        self.key=key
        if key is None:return
        path=_cached_picon_path(key[2])
        if path:self.apply(path,self.generation,key)
        else:self.dwell.start(DWELL_MS,True)

    def cancel(self):
        self.generation+=1
        self.dwell.stop();self.poll.stop()
        if self.token is not None:self.token.cancel()
        self.token=None;self.key=None
        if self.workers is not None:self.workers.replace([])
        with self.lock:self.mailbox=None
        self._clear_decode()

    def start(self):
        key=self.target()
        if key is None or key!=self.key:return
        generation=self.generation
        token=self.token=_WorkerToken(TIMEOUT_SECONDS)
        client=self.screen.client
        url=key[2]
        if self.workers is None:self.workers=LatestPosterWorkers(worker_count=2)
        def work():
            path=''
            try:
                token.check()
                path=_cached_picon_path(url)
                if not path:
                    path=_download_client_image(client,url,_picon_cache_path(url),timeout=TIMEOUT_SECONDS,token=token,priority=True)
                token.check()
            except Exception:
                path=''
            with self.lock:
                if not self.closed and generation==self.generation and token.active():
                    self.mailbox=(generation,key,path)
        self.workers.replace([work])
        self.poll.start(80,True)

    def tick(self):
        if self.closed:return
        if self.target()!=self.key:
            self.schedule();return
        with self.lock:result,self.mailbox=self.mailbox,None
        if result is not None:
            self.token=None
            generation,key,path=result
            if path:self.apply(path,generation,key)
            return
        if self.token is not None and self.token.active():self.poll.start(80,True)
        elif self.token is not None:
            self.token.cancel();self.token=None

    def apply(self,path,generation,key):
        if self.closed or generation!=self.generation or key!=self.target():return
        def loaded(success):
            if self.closed or generation!=self.generation or key!=self.target():return
            (self.screen['r102_search_poster'].show if success else self.screen['r102_search_poster'].hide)()
            (self.screen['r102_poster_empty'].hide if success else self.screen['r102_poster_empty'].show)()
        attach_pixmap(self.screen,'r102_search_poster',path,key='r102-search-poster',on_loaded=loaded,show_on_load=False)

    def close(self):
        if self.closed:return
        self.closed=True
        self.cancel()
        if self.workers is not None:self.workers.close()

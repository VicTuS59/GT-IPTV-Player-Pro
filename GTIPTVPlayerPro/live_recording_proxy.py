"""One local player fetch path shared by TS/HLS playback and recording."""
import collections
import hashlib
import http.server
import json
import re
import secrets
import socket
import threading
import time
from http.client import HTTPException
from urllib.parse import urlsplit
from urllib.request import build_opener, ProxyHandler, HTTPRedirectHandler, Request
from urllib.error import HTTPError

from . import live_recording as recording
from .live_recording_hls import rewrite
from .stream_media import media_request, TransportPackets

CHUNK=65536
MAX_PLAYLIST=2*1024*1024
_entries={}
_originals=collections.OrderedDict()
_lock=threading.RLock()
_server=None
_server_stop=None


class Redirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        result=super().redirect_request(req,fp,code,msg,headers,newurl)
        if result is not None and urlsplit(req.full_url).netloc!=urlsplit(newurl).netloc:
            for key in ('Authorization','Cookie','Host'): result.remove_header(key)
        return result


class Entry:
    def __init__(self,url):
        self.original=url
        request=media_request(url)
        self.source=request.full_url;self.headers=dict(request.header_items())
        self.token=secrets.token_hex(20)
        self.port=_server.server_address[1] if _server is not None else 0
        path=urlsplit(self.source).path.lower()
        suffix='index.m3u8' if path.endswith('.m3u8') else ('live.ts' if path.endswith(('.ts','.mpegts')) else 'live')
        self.root='/'+self.token+'/'+suffix
        self.kind='';self.unsupported=False;self.last=time.monotonic()
        self.fetch_lock=threading.Lock();self.lock=threading.RLock()
        self.resources=collections.OrderedDict();self.small=collections.OrderedDict();self.small_bytes=0
        self.job=None;self.stream_claimed=False;self.closed=False;self.response=None
        self.active_requests=0
        self.packets=None
        self.register(self.source,kind='root',identity='root')

    @property
    def url(self):return 'http://127.0.0.1:%d%s'%(self.port,self.root)

    def register(self,url,kind='segment',role='video',byte_range=None,sequence=None,playlist='',identity=None):
        if len(url)>8192 or urlsplit(url).scheme not in ('http','https'): raise ValueError('Unsupported HLS resource')
        identity=identity or hashlib.sha256(json.dumps([url,byte_range,sequence,playlist]).encode()).hexdigest()[:32]
        with self.lock:
            value=self.resources.get(identity)
            if value is None:
                extension=urlsplit(url).path.lower().rsplit('.',1)[-1]
                ext='m3u8' if kind=='playlist' else ('key' if kind=='key' else ('mp4' if kind=='map' else extension if extension in ('ts','aac','m4s','mp4','vtt') else 'ts'))
                name=identity+'.'+ext
                value=dict(url=url,id=identity,name=name,kind=kind,role=role,range=byte_range,
                    local='/'+self.token+'/'+name,meta=dict(name=name,segment=False))
                self.resources[identity]=value
            self.resources.move_to_end(identity)
            while len(self.resources)>1024:
                oldest=next((k for k,v in self.resources.items() if v['kind'] not in ('root','playlist')),None)
                if oldest is None: raise ValueError('Too many HLS playlists')
                self.resources.pop(oldest)
            return value

    def attach(self,job):
        with self.lock:
            self.job=job;self.packets=TransportPackets()
            for name,body in self.small.values(): job.put('asset',(name,body))

    def close(self):
        with self.lock:
            self.closed=True
            response=self.response
        if response is not None:
            try:
                from .stream_media import response_socket
                sock=response_socket(response)
                if sock is not None: sock.shutdown(socket.SHUT_RDWR)
            except OSError:pass
        recording.source_closed(self.url)
        _retire(self)

    def clear_closed(self):
        with self.lock:
            if self.closed and not self.active_requests:
                self.resources.clear();self.small.clear();self.small_bytes=0
                self.job=self.packets=self.response=None

    def _remember(self,resource,body):
        if len(body)>1024*1024:return
        with self.lock:
            previous=self.small.pop(resource['id'],None)
            if previous:self.small_bytes-=len(previous[1])
            self.small[resource['id']]=(resource['name'],body);self.small_bytes+=len(body)
            while self.small_bytes>1024*1024:
                unused,value=self.small.popitem(last=False);self.small_bytes-=len(value[1])

    def serve(self,handler,resource):
        with self.fetch_lock:
            if self.closed:handler.send_error(410);return
            if resource['kind']=='root' and self.stream_claimed:
                handler.send_error(409);return
            self.last=time.monotonic()
            headers=dict(self.headers)
            explicit={key.lower() for key in headers}
            for key in ('User-Agent','Referer','Origin','Accept','Accept-Language'):
                if key.lower() not in explicit and handler.headers.get(key):
                    headers[key]=handler.headers[key]
            if urlsplit(resource['url']).netloc!=urlsplit(self.source).netloc:
                headers={k:v for k,v in headers.items() if k.lower() not in ('authorization','cookie','host')}
            requested_range=handler.headers.get('Range','')
            original_range=resource['range']
            full_resource=not requested_range or requested_range=='bytes=0-'
            if original_range:
                length,start=original_range
                if requested_range and not full_resource:
                    match=re.fullmatch(r'bytes=(\d+)-(\d*)',requested_range)
                    if not match:handler.send_error(416);return
                    offset=int(match[1]);end=int(match[2]) if match[2] else length-1
                    if offset>=length or end<offset:handler.send_error(416);return
                    headers['Range']='bytes=%d-%d'%(start+offset,start+min(end,length-1))
                else:headers['Range']='bytes=%d-%d'%(start,start+length-1)
            elif requested_range:headers['Range']=requested_range
            opener=build_opener(ProxyHandler({}),Redirect())
            response=None;capture=None;capture_id=secrets.token_hex(8);complete=False;small=[];small_size=0
            with self.lock:self.active_requests+=1
            try:
                if self.closed:return
                response=opener.open(Request(resource['url'],headers=headers),timeout=8)
                self.response=response
                status=response.getcode();mime=response.headers.get('Content-Type','').split(';',1)[0].lower()
                read=getattr(response,'read1',response.read)
                first=read(CHUNK)
                hls=(resource['kind']=='playlist' or urlsplit(resource['url']).path.lower().endswith('.m3u8') or 'mpegurl' in mime or first.lstrip().startswith(b'#EXTM3U'))
                if hls:
                    body=bytearray(first)
                    while len(body)<=MAX_PLAYLIST:
                        block=read(min(CHUNK,MAX_PLAYLIST+1-len(body)))
                        if not block:break
                        body.extend(block)
                    if len(body)>MAX_PLAYLIST:raise ValueError('Playlist exceeds bound')
                    if getattr(response,'length',0) not in (0,None):raise ValueError('Incomplete playlist')
                    if response.headers.get('Content-Encoding','').lower()=='gzip':
                        import zlib
                        decoder=zlib.decompressobj(16+zlib.MAX_WBITS)
                        body=decoder.decompress(bytes(body),MAX_PLAYLIST+1)
                        if len(body)>MAX_PLAYLIST or not decoder.eof:raise ValueError('Invalid compressed playlist')
                    body,bad=rewrite(bytes(body),response.geturl(),self.register,resource['id'],resource['role'])
                    self.kind='hls';self.unsupported=self.unsupported or bad
                    if bad and self.job is not None:
                        self.job.error='unsupported';self.job.stop()
                    handler.send_response(200);handler.send_header('Content-Type','application/vnd.apple.mpegurl')
                    handler.send_header('Content-Length',str(len(body)));handler.send_header('Cache-Control','no-store');handler.end_headers()
                    handler.wfile.write(body)
                    return
                if resource['kind']=='root':
                    if self.stream_claimed:handler.send_error(409);return
                    self.stream_claimed=True
                    self.kind='ts' if mime in ('video/mp2t','video/mpeg','application/octet-stream') or (len(first)>376 and first[0]==first[188]==first[376]==0x47) else ''
                    if response.headers.get('Content-Encoding','').lower() not in ('','identity'):self.unsupported=True
                if original_range and status!=206:raise ValueError('Server did not honor HLS byte range')
                handler.send_response(200 if original_range and full_resource else status)
                handler.send_header('Content-Type',mime or 'application/octet-stream')
                for key in ('Content-Length','Content-Encoding'):
                    if response.headers.get(key):handler.send_header(key,response.headers[key])
                if response.headers.get('Content-Range') and not original_range:
                    handler.send_header('Content-Range',response.headers['Content-Range'])
                elif original_range and not full_resource:
                    handler.send_header('Content-Range','bytes %d-%d/%d'%(offset,min(end,length-1),length))
                handler.send_header('Connection','close');handler.end_headers()
                with self.lock:
                    candidate=self.job
                    if candidate is not None and candidate.kind=='hls' and full_resource and candidate.state=='recording':
                        capture=candidate;capture.put('begin',(capture_id,dict(resource['meta'])))
                block=first
                while block and not self.closed:
                    if resource['kind']=='root':
                        with self.lock:job=self.job;packets=self.packets
                        if job is not None and packets is not None and job.kind=='ts' and job.state=='recording':
                            try:clean=packets.feed(block)
                            except ValueError:job.error='unsupported';job.stop();clean=b''
                            if clean:job.put('ts',clean)
                    elif capture is not None:capture.put('data',(capture_id,block))
                    handler.wfile.write(block)
                    if resource['kind'] in ('key','map') and small_size<=1024*1024:
                        small.append(block);small_size+=len(block)
                    self.last=time.monotonic()
                    block=read(CHUNK)
                complete=not block and not self.closed and getattr(response,'length',0) in (0,None)
                if complete and resource['kind'] in ('key','map') and small_size<=1024*1024:
                    self._remember(resource,b''.join(small))
            except HTTPError as error:
                handler.send_error(error.code);error.close()
            except (OSError,ValueError,UnicodeError,HTTPException):
                handler.close_connection=True
            finally:
                if capture is not None:capture.put('end' if complete else 'abort',capture_id)
                if response is not None:response.close()
                self.response=None
                with self.lock:self.active_requests-=1
                if resource['kind']=='root' and self.kind!='hls':self.close()
                self.clear_closed()


def _retire(entry):
    global _server,_server_stop
    with _lock:
        _originals[entry.url]=entry.original
        _originals.move_to_end(entry.url)
        while len(_originals)>8:_originals.popitem(last=False)
        if _entries.get(entry.token) is entry:_entries.pop(entry.token,None)
        if not _entries and _server is not None:
            stop,_server_stop=_server_stop,None
            _server=None
            if stop is not None:stop.set()
    entry.clear_closed()


def _serve(server,stop):
    # GUI shutdown only signals this existing server thread. It never waits
    # for serve_forever.shutdown() or for provider requests to finish.
    server.timeout=.2
    try:
        while not stop.is_set():server.handle_request()
    finally:server.server_close()


def _start():
    global _server,_server_stop
    if _server is not None:return
    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version='HTTP/1.0'
        def log_message(self,*args):pass
        def _resource(self):
            if self.client_address[0]!='127.0.0.1':return None,None
            pieces=self.path.split('/')
            if len(pieces)!=3:return None,None
            with _lock:entry=_entries.get(pieces[1])
            if entry is None:return None,None
            with entry.lock:
                resource=entry.resources.get('root') if self.path==entry.root else next((v for v in entry.resources.values() if v['local']==self.path),None)
            return entry,resource
        def do_HEAD(self):
            entry,resource=self._resource()
            if resource is None:self.send_error(404);return
            if resource['kind']=='playlist' or (resource['kind']=='root' and (entry.kind=='hls' or self.path.endswith('.m3u8'))):
                mime='application/vnd.apple.mpegurl'
            elif self.path.endswith(('.mp4','.m4s')):mime='video/mp4'
            elif self.path.endswith('.aac'):mime='audio/aac'
            elif self.path.endswith('.vtt'):mime='text/vtt'
            elif resource['kind']=='segment' or self.path.endswith('.ts'):mime='video/mp2t'
            else:mime='application/octet-stream'
            self.send_response(200);self.send_header('Content-Type',mime);self.end_headers()
        def do_GET(self):
            entry,resource=self._resource()
            if resource is None:self.send_error(404);return
            self.connection.settimeout(10)
            entry.serve(self,resource)
    _server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Handler);_server.daemon_threads=True
    _server_stop=threading.Event()
    try:threading.Thread(target=_serve,args=(_server,_server_stop),name='GT-RecordingRelay',daemon=True).start()
    except BaseException:
        _server.server_close();_server=_server_stop=None;raise


def prepare(url):
    if urlsplit(str(url).split('#',1)[0]).scheme not in ('http','https'):return url
    with _lock:
        url=_originals.get(url,url)
        existing=next((e for e in _entries.values() if e.url==url),None)
        # Reopening/cycling the engine needs a fresh single-consumer endpoint,
        # never a proxy that fetches another local proxy as its upstream.
        if existing:url=existing.original
        for key,entry in list(_entries.items()):
            if entry.closed or time.monotonic()-entry.last>120:
                entry.close();_entries.pop(key,None)
        if len(_entries)>=8:
            entry=next(iter(_entries.values()));entry.close();_entries.pop(entry.token,None)
        _start()
        entry=Entry(url);_entries[entry.token]=entry
        return entry.url


def find(url):
    with _lock:return next((e for e in _entries.values() if e.url==url and not e.closed),None)


def release_except(url):
    with _lock:
        entries=[entry for entry in _entries.values() if entry.url!=url and not entry.closed]
    for entry in entries: entry.close()


def release_session():
    release_except('')
    with _lock:_originals.clear()

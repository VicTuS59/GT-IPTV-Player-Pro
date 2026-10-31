# -*- coding: utf-8 -*-
"""Bounded asynchronous live recordings; disk faults never stop the player."""
import collections
import json
import os
import re
import stat
import threading
import time
import uuid

from .persistence import guarded_mutation

SETTINGS = '/etc/enigma2/gtiptvplayer/live-recording.json'
QUEUE_BYTES = 2 * 1024 * 1024
MIN_FREE = 16 * 1024 * 1024
_lock = threading.RLock()
_job = None
_source = ''
_source_epoch = 0
_reference_url = ''
_preparing = None
_begin_lock = threading.Lock()


class RecordingError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def recording_folder():
    try:
        with open(SETTINGS, 'rb') as handle:
            body = handle.read(16385)
        value = json.loads(body) if len(body) <= 16384 else {}
        path = value.get('folder', '') if isinstance(value, dict) else ''
        return path if isinstance(path, str) and os.path.isabs(path) else ''
    except (OSError, ValueError, TypeError):
        return ''


def checked_folder(path):
    if not path or not os.path.isabs(path):
        raise RecordingError('folder')
    real = os.path.realpath(path)
    if not os.path.isdir(real) or not os.access(real, os.W_OK | os.X_OK):
        raise RecordingError('storage')
    mount = real
    while mount != '/' and not os.path.ismount(mount):
        mount = os.path.dirname(mount)
    # Never fall through to internal flash after an unplugged HDD/USB.
    if mount == '/' or os.stat(mount).st_dev != os.stat(real).st_dev:
        raise RecordingError('storage')
    try:
        with open('/proc/mounts', encoding='utf-8') as handle:
            for line in handle:
                fields = line.split()
                if len(fields) >= 3:
                    point = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1],8)), fields[1])
                    if os.path.realpath(point) == mount and fields[2] in (
                            'tmpfs','ramfs','rootfs','overlay','squashfs','jffs2','ubifs','proc','sysfs','devtmpfs'):
                        raise RecordingError('storage')
    except OSError:
        raise RecordingError('storage')
    return real


@guarded_mutation
def save_recording_folder(path):
    real = checked_folder(path)
    parent = os.path.dirname(SETTINGS)
    os.makedirs(parent, mode=0o700, exist_ok=True)
    temporary = SETTINGS + '.' + uuid.uuid4().hex + '.tmp'
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            json.dump({'version': 1, 'folder': real}, handle, ensure_ascii=False)
            handle.flush(); os.fsync(handle.fileno())
        os.replace(temporary, SETTINGS)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass
    return real


def _destination(path):
    real = checked_folder(path)
    fd = os.open(real, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        # Validate again after pinning the directory: an unplug between the
        # first check and open must not select the fallback flash directory.
        checked_folder(real)
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_dev != os.stat(real).st_dev:
            raise RecordingError('storage')
        free = os.fstatvfs(fd)
        if free.f_bavail * free.f_frsize < MIN_FREE:
            raise RecordingError('space')
        return fd, real
    except Exception:
        os.close(fd); raise


def _name(title):
    text = re.sub(r'[\x00-\x1f<>:"/\\|?*]', '_', str(title or 'Live TV')).strip(' .')
    text = text.encode('utf-8')[:100].decode('utf-8', 'ignore') or 'Live TV'
    return time.strftime('%Y%m%d_%H%M%S_') + text + '_' + uuid.uuid4().hex[:6]


class Recording:
    def __init__(self, folder, title, kind='ts'):
        self.parent, real = _destination(folder)
        self.directory = self.parent
        self.kind = kind
        base = _name(title)
        self.path = os.path.join(real, base + ('.ts' if kind == 'ts' else '/index.m3u8'))
        self.file = None
        try:
            if kind == 'hls':
                os.mkdir(base, mode=0o700, dir_fd=self.parent)
                self.directory = os.open(base, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=self.parent)
            else:
                fd = os.open(base+'.ts', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.parent)
                self.file = os.fdopen(fd, 'wb', buffering=65536)
        except Exception:
            if self.directory != self.parent: os.close(self.directory)
            os.close(self.parent)
            raise
        self.condition = threading.Condition()
        self.queue = collections.deque()
        self.inflight = set()
        self.queued = self.high_water = self.bytes_written = 0
        self.closing = False
        self.state = 'recording'
        self.error = ''
        self.parts = {}
        self.playlists = {}
        self.last_space_check = 0
        self.worker = threading.Thread(target=self._run, name='GT-LiveRecording', daemon=True)
        try: self.worker.start()
        except Exception:
            self._close_files(); raise

    def put(self, event, value=None):
        size = len(value) if isinstance(value, bytes) else 0
        if event in ('data', 'asset'): size = len(value[1])
        with self.condition:
            continuing = event in ('data','end','abort') and (value[0] if event=='data' else value) in self.inflight
            if (self.closing and not continuing) or self.state not in ('recording','stopping'): return False
            if self.queued + size > QUEUE_BYTES or len(self.queue) >= 256:
                self.error = 'storage'; self.closing = True
                self.condition.notify_all()
                return False
            self.queue.append((event, value, size)); self.queued += size
            if event == 'begin': self.inflight.add(value[0])
            elif event in ('end','abort'): self.inflight.discard(value)
            self.high_water = max(self.high_water, self.queued)
            self.condition.notify_all()
            return True

    def stop(self):
        with self.condition:
            self.closing = True
            if self.state == 'recording': self.state = 'stopping'
            self.condition.notify_all()

    def snapshot(self):
        with self.condition:
            return dict(state=self.state, error=self.error, bytes=self.bytes_written,
                        path=self.path, queued_bytes=self.queued, high_water=self.high_water, kind=self.kind)

    def _atomic(self, name, body):
        temporary = name+'.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
        try:
            with os.fdopen(fd, 'wb') as handle: handle.write(body)
            os.replace(temporary, name, src_dir_fd=self.directory, dst_dir_fd=self.directory)
        finally:
            try: os.unlink(temporary, dir_fd=self.directory)
            except FileNotFoundError: pass

    def _asset(self, name, body):
        fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
        with os.fdopen(fd, 'wb') as handle: handle.write(body)

    def _playlist(self, meta):
        # The player selects the representation. Record its fetched video and
        # audio segments without a second provider reader or transcoding.
        if meta.get('role') not in ('video', 'audio'): return
        track = meta['role']
        seq = meta['seq']
        state = self.playlists.get(track)
        if state is not None and state['source'] == meta['playlist'] and seq <= state['last']:
            return
        if state is None:
            fd = os.open(track+'.m3u8', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
            handle = os.fdopen(fd, 'w', encoding='utf-8')
            handle.write('#EXTM3U\n#EXT-X-VERSION:7\n#EXT-X-TARGETDURATION:%d\n#EXT-X-MEDIA-SEQUENCE:%d\n#EXT-X-PLAYLIST-TYPE:EVENT\n' % (meta['target'], seq))
            state = self.playlists[track] = dict(file=handle, last=seq-1, source=meta['playlist'], encrypted=False)
        handle = state['file']
        if meta.get('discontinuity') or state['source'] != meta['playlist'] or seq != state['last']+1:
            handle.write('#EXT-X-DISCONTINUITY\n')
        for tag in ('key', 'map'):
            text = meta.get(tag, '')
            if tag == 'key' and not text and state['encrypted']:
                text = '#EXT-X-KEY:METHOD=NONE'
            if text:
                # Key/map must already have arrived through the shared player
                # path. A partial/unsupported bundle is never called success.
                names = re.findall(r'URI="([^"]+)"', text)
                if any(not self._exists(name) for name in names):
                    raise RecordingError('unsupported')
                handle.write(text+'\n')
            if tag == 'key': state['encrypted'] = bool(text and 'METHOD=NONE' not in text)
        if meta.get('date'): handle.write('#EXT-X-PROGRAM-DATE-TIME:'+meta['date']+'\n')
        handle.write('#EXTINF:%.6f,\n%s\n' % (meta['duration'], meta['name']))
        handle.flush()
        state.update(last=seq, source=meta['playlist'])
        audio = 'audio' in self.playlists
        video = 'video' in self.playlists
        index = '#EXTM3U\n#EXT-X-VERSION:7\n'
        if audio and video:
            index += '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="audio",NAME="Audio",DEFAULT=YES,AUTOSELECT=YES,URI="audio.m3u8"\n'
        index += '#EXT-X-STREAM-INF:BANDWIDTH=10000000'+(',AUDIO="audio"' if audio and video else '')+'\n'+('video.m3u8' if video else 'audio.m3u8')+'\n'
        self._atomic('index.m3u8', index.encode())

    def _exists(self, name):
        try: return stat.S_ISREG(os.stat(name, dir_fd=self.directory, follow_symlinks=False).st_mode)
        except OSError: return False

    def _event(self, event, value):
        if event == 'ts':
            self.file.write(value); self.bytes_written += len(value)
        elif event == 'asset':
            name, body = value
            if not self._exists(name): self._asset(name, body)
        elif event == 'begin':
            identity, meta = value
            if not self._exists(meta['name']):
                fd = os.open(meta['name']+'.part', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=self.directory)
                self.parts[identity] = (os.fdopen(fd,'wb'), meta)
        elif event == 'data':
            identity, body = value
            if identity in self.parts:
                self.parts[identity][0].write(body); self.bytes_written += len(body)
        elif event in ('end', 'abort'):
            entry = self.parts.pop(value, None)
            if entry:
                handle, meta = entry; handle.close()
                if event == 'end':
                    os.rename(meta['name']+'.part', meta['name'], src_dir_fd=self.directory, dst_dir_fd=self.directory)
                    if meta.get('segment'): self._playlist(meta)
                else:
                    os.unlink(meta['name']+'.part', dir_fd=self.directory)

    def _run(self):
        try:
            while True:
                with self.condition:
                    while not self.queue and (not self.closing or self.inflight) and not self.error: self.condition.wait(.2)
                    if self.error: break
                    if not self.queue and self.closing and not self.inflight: break
                    event, value, size = self.queue.popleft(); self.queued -= size
                if time.monotonic()-self.last_space_check > 1:
                    info = os.fstatvfs(self.directory)
                    if info.f_bavail*info.f_frsize < 1024*1024: raise RecordingError('space')
                    self.last_space_check = time.monotonic()
                self._event(event, value)
            if self.kind == 'hls':
                for state in self.playlists.values():
                    state['file'].write('#EXT-X-ENDLIST\n'); state['file'].flush()
                if not self.playlists and not self.error: self.error = 'unsupported'
            if self.file: self.file.flush(); os.fsync(self.file.fileno())
            os.fsync(self.directory)
        except Exception as error:
            self.error = getattr(error, 'code', 'storage')
        finally:
            self._close_files()
            with self.condition:
                self.state = 'error' if self.error else 'completed'
                self.queue.clear(); self.queued = 0; self.closing = True

    def _close_files(self):
        for handle in ([self.file] if self.file else []) + [v['file'] for v in getattr(self,'playlists',{}).values()] + [v[0] for v in getattr(self,'parts',{}).values()]:
            try: handle.close()
            except OSError: pass
        for handle, meta in getattr(self, 'parts', {}).values():
            try: os.unlink(meta['name']+'.part', dir_fd=self.directory)
            except OSError: pass
        for descriptor in {self.parent, self.directory}:
            try: os.close(descriptor)
            except OSError: pass


def current():
    with _lock: return _job


def publish(url, block):
    with _lock:
        job = _job if url == _source else None
    if job is not None and job.kind == 'ts': job.put('ts', block)


def stop():
    global _source
    with _lock:
        job = _job; _source = ''
    if job is not None: job.stop()
    return job


def reference_started(url):
    with _lock:
        changed = bool(_source and _source != url)
    if changed: stop()


def reference_starting(url):
    global _reference_url, _source_epoch
    with _lock:
        if url != _reference_url:
            _reference_url = url; _source_epoch += 1
            request = _preparing
            if request is not None: request.cancel()
    # Shut the old proxy socket before the decoder opens a new channel. This
    # also aborts pending HLS pieces when their player consumer goes away.
    import sys
    proxy = sys.modules.get(__package__+'.live_recording_proxy')
    if proxy is not None: proxy.release_except(url)


def _native_relay(url):
    import sys
    for module_name in ('stalker_live_trial','xtream_live','m3u_live'):
        module = sys.modules.get(__package__+'.'+module_name)
        manager = getattr(module, '_MANAGER', None)
        if manager is not None:
            with manager.lock:
                for entry in manager.entries.values():
                    if entry.relay.url == url: return entry.relay
    return None


def wrap_url(url):
    if not recording_folder() or _native_relay(url) is not None: return url
    from .live_recording_proxy import prepare
    return prepare(url)


def begin(url, title, cancel_event=None):
    global _job, _source
    if not _begin_lock.acquire(False): raise RecordingError('busy')
    job = None
    try:
        with _lock:
            epoch = _source_epoch
            if _job is not None and _job.state in ('recording','stopping'):
                raise RecordingError('busy')
        def check():
            if cancel_event is not None and cancel_event.is_set(): raise InterruptedError('Recording preparation cancelled')
            with _lock:
                if epoch != _source_epoch: raise InterruptedError('Recording source changed')
        check()
        folder = recording_folder()
        if not folder: raise RecordingError('folder')
        native = _native_relay(url)
        if native is not None and native.stop_event.is_set(): raise RecordingError('reopen')
        entry = None
        if native is None:
            from .live_recording_proxy import find
            entry = find(url)
            if entry is None:
                from urllib.parse import urlsplit
                raise RecordingError('reopen' if urlsplit(url).scheme in ('http','https') else 'unsupported')
            if entry.kind not in ('ts','hls') or entry.unsupported: raise RecordingError('unsupported')
        check()
        # Mount discovery, statvfs, and file creation must never hold the
        # memory lock used by the GUI's current()/recording-badge callbacks.
        job = Recording(folder, title, 'ts' if native is not None else entry.kind)
        check()
        with _lock:
            check()
            if entry is not None and entry.closed: raise RecordingError('reopen')
            if native is not None and native.stop_event.is_set(): raise RecordingError('reopen')
            _job, _source = job, url
            if entry is not None: entry.attach(job)
            if native is not None: native.recording_sink = job
            return job
    except BaseException:
        if job is not None: job.stop()
        raise
    finally:
        _begin_lock.release()


class BeginRequest:
    def __init__(self, url, title):
        self.url, self.title = url, title
        self.cancelled, self.done = threading.Event(), threading.Event()
        self.job = self.error = None

    def cancel(self):
        self.cancelled.set()
        job = self.job
        if job is not None: job.stop()

    def run(self):
        global _preparing
        try:
            self.job = begin(self.url, self.title, self.cancelled)
            if self.cancelled.is_set(): self.job.stop()
        except InterruptedError:
            pass
        except Exception as error:
            self.error = getattr(error, 'code', 'storage')
        finally:
            with _lock:
                if _preparing is self: _preparing = None
            self.done.set()


def request_begin(url, title):
    global _preparing
    with _lock:
        if _preparing is not None: raise RecordingError('busy')
        request = _preparing = BeginRequest(url, title)
    try:
        threading.Thread(target=request.run, name='GT-RecordingPrepare', daemon=True).start()
    except BaseException:
        with _lock:
            if _preparing is request: _preparing = None
        raise
    return request


def source_closed(url):
    with _lock:
        matches = url == _source
    if matches: stop()


def release_session():
    import sys
    with _lock:
        request = _preparing
    if request is not None: request.cancel()
    proxy = sys.modules.get(__package__+'.live_recording_proxy')
    if proxy is not None: proxy.release_session()

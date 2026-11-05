# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Updater state, bounded verified downloads and explicitly requested opkg installation."""
import hashlib
import os
import re
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from . import PLUGIN_VERSION
from .update_protocol import UpdateError,UpdateTransport,MAX_METADATA,canonical,json_document,source_url,verify_envelope,version_tuple
from .update_package import inspect_ipk
from .update_feedback import emit,installer_phase
from .update_errors import reported,storage_error,require_space,installation_error

SETTINGS='/etc/enigma2/gtiptvplayer/update-settings.json'
CACHE='/tmp/gtiptv-updates'
INTERVAL=6*60*60
_STATE_LOCK=threading.RLock()
_CHECK_LOCK=threading.Lock()
_INSTALL_LOCK=threading.Lock()

def private_directory(path):
    os.makedirs(path,mode=0o700,exist_ok=True)
    info=os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid():raise UpdateError('storage',path=path)
    if stat.S_IMODE(info.st_mode)!=0o700:os.chmod(path,0o700)
    return path

class UpdateState:
    def __init__(self,path=SETTINGS):self.path=path
    def read(self):
        defaults=dict(auto=True,channel='stable',last_check=0,skipped='',offer='',installed='',backup='',result='')
        try:
            fd=os.open(self.path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
            with os.fdopen(fd,'rb') as handle:
                info=os.fstat(handle.fileno())
                if not stat.S_ISREG(info.st_mode) or info.st_uid!=os.geteuid():raise UpdateError('storage')
                raw=handle.read(192*1024+1)
            data=json_document(raw,192*1024)
            if not isinstance(data,dict):raise UpdateError('storage')
            if type(data.get('auto')) is bool:defaults['auto']=data['auto']
            if type(data.get('last_check')) in (int,float) and 0<=data['last_check']<10**12:defaults['last_check']=data['last_check']
            for key,maximum in (('skipped',48),('offer',MAX_METADATA),('installed',48),('backup',2048),('result',80)):
                if isinstance(data.get(key),str) and len(data[key])<=maximum:defaults[key]=data[key]
            # Migrate former selector settings without accepting cached offers
            # from another source or postponing the first stable check.
            if data.get('channel','stable')!='stable':defaults.update(last_check=0,skipped='',offer='')
        except FileNotFoundError:pass
        except OSError as error:raise storage_error(error,self.path) from error
        except Exception as error:raise UpdateError('storage','state',path=self.path) from error
        return defaults
    def write(self,**changes):
        if changes.get('channel','stable')!='stable':raise UpdateError('metadata')
        with _STATE_LOCK:
            value=self.read();value.update(changes);body=canonical(value)
            if len(body)>192*1024:raise UpdateError('storage')
            directory=private_directory(os.path.dirname(self.path));temporary=None
            try:
                fd,temporary=tempfile.mkstemp(prefix='.update-state-',dir=directory)
                with os.fdopen(fd,'wb') as handle:handle.write(body);handle.flush();os.fsync(handle.fileno())
                os.chmod(temporary,0o600);os.replace(temporary,self.path);temporary=None
            except OSError as error:raise storage_error(error,self.path) from error
            except Exception as error:raise UpdateError('storage','state',path=self.path) from error
            finally:
                if temporary is not None:
                    try:os.unlink(temporary)
                    except OSError:pass
            return value

def effective_version(state,current=PLUGIN_VERSION):
    pending=state.get('installed','')
    try:return pending if version_tuple(pending)>version_tuple(current) else current
    except UpdateError:return current

class UpdateService:
    def __init__(self,state=None,transport=None,cache=CACHE,current=PLUGIN_VERSION):
        self.state=state or UpdateState();self.transport=transport;self.cache=cache;self.current=current

    def transfer(self,*args,**kwargs):
        if self.transport is None:self.transport=UpdateTransport()
        return self.transport.transfer(*args,**kwargs)

    def cached(self):
        state=self.state.read();body=state.get('offer','').encode('utf-8')
        if not body:return None
        try:manifest=verify_envelope(body)
        except UpdateError:return None
        if version_tuple(manifest['version'])<=version_tuple(effective_version(state,self.current)):return None
        return dict(body=body,manifest=manifest)

    @reported('check')
    def check(self,automatic=False,cancel=None,clock=time.time,on_entry=False):
        if not _CHECK_LOCK.acquire(False):raise UpdateError('busy')
        try:
            state=self.state.read();now=clock();elapsed=now-state['last_check']
            if automatic and (not state['auto'] or (not on_entry and 0<=elapsed<INTERVAL)):return self.cached()
            self.state.write(last_check=now)
            body=self.transfer(source_url(),MAX_METADATA,cancel=cancel,seconds=25)
            manifest=verify_envelope(body)
            self.state.write(offer=body.decode('utf-8'),result='checked')
            if version_tuple(manifest['version'])<=version_tuple(effective_version(state,self.current)):return None
            return dict(body=body,manifest=manifest)
        finally:_CHECK_LOCK.release()

    def verified_offer(self,offer):
        if not isinstance(offer,dict) or not isinstance(offer.get('body'),bytes):raise UpdateError('metadata')
        state=self.state.read();manifest=verify_envelope(offer['body'])
        if state.get('installed') and effective_version(state,self.current)!=self.current:raise UpdateError('restart')
        if version_tuple(manifest['version'])<=version_tuple(effective_version(state,self.current)):raise UpdateError('version')
        return manifest

    def path_for(self,manifest):return os.path.join(self.cache,manifest['sha256']+'.ipk')

    @reported('download')
    def download(self,offer,cancel=None,progress=None,feedback=None):
        manifest=self.verified_offer(offer);private_directory(self.cache)
        path=self.path_for(manifest)
        if os.path.lexists(path):
            try:
                emit(feedback,'verify')
                inspect_ipk(path,manifest,cancel)
                if progress is not None:progress(manifest['bytes'],manifest['bytes'])
                emit(feedback,'downloaded',1,1)
                return path
            except UpdateError as error:
                if error.code=='cancelled':raise
                # Only this updater's content-addressed cache entry is removed.
                os.unlink(path)
        require_space(self.cache,manifest['bytes']+8*1024*1024)
        temporary=None;written=0;digest=hashlib.sha256()
        try:
            fd,temporary=tempfile.mkstemp(prefix='.update-',suffix='.part',dir=self.cache)
            with os.fdopen(fd,'wb') as output:
                def sink(chunk):
                    nonlocal written
                    try:output.write(chunk)
                    except OSError as error:raise storage_error(error,self.cache) from error
                    written+=len(chunk);digest.update(chunk)
                    if progress is not None:progress(written,manifest['bytes'])
                count=self.transfer(manifest['url'],manifest['bytes'],cancel=cancel,sink=sink)
                output.flush();os.fsync(output.fileno())
            if count!=manifest['bytes'] or written!=manifest['bytes'] or digest.hexdigest()!=manifest['sha256']:raise UpdateError('package')
            emit(feedback,'verify')
            inspect_ipk(temporary,manifest,cancel)
            os.chmod(temporary,0o600);os.replace(temporary,path);temporary=None
            self._trim_cache(path)
            emit(feedback,'downloaded',1,1)
            return path
        except UpdateError:raise
        except OSError as error:raise storage_error(error,self.cache) from error
        finally:
            if temporary is not None:
                try:os.unlink(temporary)
                except OSError:pass

    def _trim_cache(self,current):
        # Keep at most two verified downloads in the updater-owned /tmp folder.
        if _INSTALL_LOCK.locked():return
        owned=[]
        try:names=os.listdir(self.cache)
        except OSError:return
        for name in names:
            if not re.fullmatch(r'[0-9a-f]{64}\.ipk',name):continue
            path=os.path.join(self.cache,name)
            try:info=os.lstat(path)
            except OSError:continue
            if stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and path!=current:owned.append((info.st_mtime_ns,path))
        for unused,path in sorted(owned,reverse=True)[1:]:
            try:os.unlink(path)
            except OSError:pass

    @contextmanager
    def installation_lock(self):
        if not _INSTALL_LOCK.acquire(False):raise UpdateError('busy')
        fd=None
        try:
            private_directory(self.cache)
            fd=os.open(os.path.join(self.cache,'install.lock'),os.O_WRONLY|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
            try:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as error:raise UpdateError('busy') from error
            except ImportError as error:raise UpdateError('install','opkg_missing') from error
            yield
        finally:
            if fd is not None:os.close(fd)
            _INSTALL_LOCK.release()

    @reported('install')
    def install(self,offer,path,busy,runner=None,root='/',uid=None,progress=None):
        """Called only after GUI confirmation; never called by a check/download."""
        with self.installation_lock():
            manifest=self.verified_offer(offer)
            if (os.geteuid() if uid is None else uid)!=0:raise UpdateError('install','root')
            if os.path.abspath(path)!=os.path.abspath(self.path_for(manifest)):raise UpdateError('package')
            emit(progress,'verify')
            inspect_ipk(path,manifest)
            self._check_activity(busy)
            # Reserve one complete unpacked payload plus working space. There
            # is no update backup and no second payload to budget on flash.
            required=manifest['installed_bytes']+8*1024*1024
            require_space(root,required)
            # Revalidate after the native activity check, which may yield to
            # the UI, and immediately before the package-manager boundary.
            emit(progress,'verify')
            inspect_ipk(path,manifest)
            self._check_activity(busy)
            require_space(root,required)
            log=os.path.join(self.cache,'install.log')
            self.state.write(result='installing')
            emit(progress,'install')
            try:code=runner(path,log) if runner is not None else run_opkg(path,log,progress=progress)
            except UpdateError:
                self._mark_install_failed()
                raise
            except OSError as error:
                self._mark_install_failed()
                raise storage_error(error,self.cache) from error
            except Exception as error:
                self._mark_install_failed()
                raise UpdateError('install') from error
            if code!=0:
                self._mark_install_failed()
                raise installation_error(log,code)
            warning=''
            try:self.state.write(installed=manifest['version'],result='installed',offer='')
            except Exception:
                # opkg has already succeeded. Do not report the installed
                # package as failed just because a bookkeeping write failed.
                warning='state'
            emit(progress,'complete',1,1)
            return dict(version=manifest['version'],log=log,warning=warning)

    @staticmethod
    def _check_activity(callback):
        reason=callback()
        if reason:
            raise UpdateError('busy',reason if reason in ('recording','downloading','activity') else 'busy')

    def _mark_install_failed(self):
        try:self.state.write(result='install-failed')
        except Exception:pass

def run_opkg(path,log,progress=None):
    binary=next((p for p in ('/usr/bin/opkg','/bin/opkg') if os.path.isfile(p) and os.access(p,os.X_OK)),None)
    if binary is None:raise UpdateError('install','opkg_missing')
    fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as output:
        process=subprocess.Popen([binary,'install',path],stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                                 close_fds=True,start_new_session=True,env=dict(os.environ,LANG='C',LC_ALL='C'))
        # The child writes directly to the private log, never a bounded pipe.
        # Read at most 8 KiB per tick and retain at most a partial 1 KiB line.
        # Only known phase names cross into the GUI; log text stays on device.
        offset=0;partial=b'';phase=None
        while True:
            try:code=process.wait(timeout=0.2);finished=True
            except subprocess.TimeoutExpired:finished=False
            if progress is not None:
                try:
                    read_fd=os.open(log,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
                    with os.fdopen(read_fd,'rb') as source:
                        original=os.fstat(output.fileno());current=os.fstat(source.fileno())
                        if (original.st_dev,original.st_ino)!=(current.st_dev,current.st_ino):raise OSError('log identity')
                        source.seek(offset);chunk=source.read(8192);offset+=len(chunk)
                    rows=(partial+chunk).split(b'\n');partial=rows.pop()[-1024:]
                    if finished and partial:rows.append(partial);partial=b''
                    for line in rows:
                        found=installer_phase(line.decode('utf-8',errors='replace'))
                        if found is not None and found!=phase:phase=found;emit(progress,phase)
                except OSError:pass
            # Never kill opkg halfway through unpack/configure, including when
            # progress reporting fails or the UI closes unexpectedly.
            if finished:return code

def receiver_activity(session):
    try:
        nav=session.nav
        if callable(getattr(nav,'getRecordings',None)) and nav.getRecordings():return 'recording'
        timer=getattr(nav,'RecordTimer',None)
        if callable(getattr(timer,'isRecording',None)) and timer.isRecording():return 'recording'
        from . import downloads
        with downloads.transaction(read_only=True) as state:
            if any(job.get('status') in downloads.ACTIVE for job in state.get('jobs',())):return 'downloading'
    except Exception:
        # An unreadable activity state is not permission to replace running code.
        return 'activity'
    return ''


def receiver_busy(session):
    return bool(receiver_activity(session))

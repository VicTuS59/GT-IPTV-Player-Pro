# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Updater state, bounded verified downloads and explicitly requested opkg installation."""
import base64
import errno
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import threading
import time
from contextlib import contextmanager
from . import PLUGIN_VERSION
from .update_protocol import UpdateError,UpdateTransport,MAX_METADATA,canonical,json_document,source_url,verify_envelope,version_tuple
from .update_package import inspect_ipk

SETTINGS='/etc/enigma2/gtiptvplayer/update-settings.json'
CACHE='/tmp/gtiptv-updates'
BACKUP_FALLBACK='/etc/enigma2'
INTERVAL=6*60*60
_STATE_LOCK=threading.RLock()
_CHECK_LOCK=threading.Lock()
_INSTALL_LOCK=threading.Lock()

def private_directory(path):
    os.makedirs(path,mode=0o700,exist_ok=True)
    info=os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.geteuid():raise UpdateError('storage')
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
        except UpdateError:raise
        except Exception:raise UpdateError('storage')
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
            except Exception:raise UpdateError('storage')
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

    def check(self,automatic=False,cancel=None,clock=time.time):
        if not _CHECK_LOCK.acquire(False):raise UpdateError('busy')
        try:
            state=self.state.read();now=clock();elapsed=now-state['last_check']
            if automatic and (not state['auto'] or 0<=elapsed<INTERVAL):return self.cached()
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

    def download(self,offer,cancel=None,progress=None):
        manifest=self.verified_offer(offer);private_directory(self.cache)
        path=self.path_for(manifest)
        if os.path.lexists(path):
            try:
                inspect_ipk(path,manifest,cancel);return path
            except UpdateError as error:
                if error.code=='cancelled':raise
                # Only this updater's content-addressed cache entry is removed.
                os.unlink(path)
        if shutil.disk_usage(self.cache).free<manifest['bytes']+8*1024*1024:raise UpdateError('space')
        temporary=None;written=0;digest=hashlib.sha256()
        try:
            fd,temporary=tempfile.mkstemp(prefix='.update-',suffix='.part',dir=self.cache)
            with os.fdopen(fd,'wb') as output:
                def sink(chunk):
                    nonlocal written
                    output.write(chunk);written+=len(chunk);digest.update(chunk)
                    if progress is not None:progress(written,manifest['bytes'])
                count=self.transfer(manifest['url'],manifest['bytes'],cancel=cancel,sink=sink)
                output.flush();os.fsync(output.fileno())
            if count!=manifest['bytes'] or written!=manifest['bytes'] or digest.hexdigest()!=manifest['sha256']:raise UpdateError('package')
            inspect_ipk(temporary,manifest,cancel)
            os.chmod(temporary,0o600);os.replace(temporary,path);temporary=None
            self._trim_cache(path)
            return path
        except UpdateError:raise
        except Exception:raise UpdateError('storage')
        finally:
            if temporary is not None:
                try:os.unlink(temporary)
                except OSError:pass

    def _trim_cache(self,current):
        # Keep at most two verified downloads in the updater-owned /tmp folder.
        if _INSTALL_LOCK.locked():return
        owned=[]
        for name in os.listdir(self.cache):
            if not re.fullmatch(r'[0-9a-f]{64}\.ipk',name):continue
            path=os.path.join(self.cache,name);info=os.lstat(path)
            if stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and path!=current:owned.append((info.st_mtime_ns,path))
        for unused,path in sorted(owned,reverse=True)[1:]:
            try:os.unlink(path)
            except OSError:pass

    @contextmanager
    def installation_lock(self):
        if not _INSTALL_LOCK.acquire(False):raise UpdateError('busy')
        fd=None
        try:
            import fcntl
            private_directory(self.cache)
            fd=os.open(os.path.join(self.cache,'install.lock'),os.O_WRONLY|os.O_CREAT|getattr(os,'O_NOFOLLOW',0),0o600)
            fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
            yield
        except (BlockingIOError,ImportError):raise UpdateError('busy')
        finally:
            if fd is not None:os.close(fd)
            _INSTALL_LOCK.release()

    def install(self,offer,path,busy,backup,runner=None,root='/',uid=None):
        """Called only after GUI confirmation; never called by a check/download."""
        with self.installation_lock():
            manifest=self.verified_offer(offer)
            if (os.geteuid() if uid is None else uid)!=0:raise UpdateError('install')
            if os.path.abspath(path)!=os.path.abspath(self.path_for(manifest)):raise UpdateError('package')
            inspect_ipk(path,manifest)
            if busy():raise UpdateError('busy')
            required=manifest['installed_bytes']*2+16*1024*1024
            if shutil.disk_usage(root).free<required:raise UpdateError('space')
            safety=backup()
            if not isinstance(safety,str) or not os.path.isfile(safety):raise UpdateError('backup')
            if busy():raise UpdateError('busy')
            # A fallback backup can consume the same flash that opkg needs.
            if shutil.disk_usage(root).free<required:raise UpdateError('space')
            # Revalidate bytes and identity after the backup and immediately
            # before crossing the package-manager execution boundary.
            inspect_ipk(path,manifest)
            log=os.path.join(self.cache,'install.log')
            self.state.write(backup=safety,result='installing')
            try:code=(runner or run_opkg)(path,log)
            except Exception:
                self.state.write(backup=safety,result='install-failed')
                raise UpdateError('install')
            if code!=0:
                self.state.write(backup=safety,result='install-failed')
                raise UpdateError('install')
            self.state.write(installed=manifest['version'],backup=safety,result='installed',offer='')
            return dict(version=manifest['version'],backup=safety,log=log)

def run_opkg(path,log):
    binary=next((p for p in ('/usr/bin/opkg','/bin/opkg') if os.path.isfile(p) and os.access(p,os.X_OK)),None)
    if binary is None:raise UpdateError('install')
    fd=os.open(log,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|getattr(os,'O_NOFOLLOW',0),0o600)
    with os.fdopen(fd,'wb') as output:
        process=subprocess.Popen([binary,'install',path],stdin=subprocess.DEVNULL,stdout=output,stderr=subprocess.STDOUT,
                                 close_fds=True,start_new_session=True,env=dict(os.environ,LANG='C',LC_ALL='C'))
        # Never kill opkg halfway through unpack/configure. The GUI remains
        # responsive and disallows closing this installation screen meanwhile.
        return process.wait()

def receiver_busy(session):
    try:
        nav=session.nav
        if callable(getattr(nav,'getRecordings',None)) and nav.getRecordings():return True
        timer=getattr(nav,'RecordTimer',None)
        if callable(getattr(timer,'isRecording',None)) and timer.isRecording():return True
        from . import downloads,live_recording
        with downloads.transaction(read_only=True) as state:
            if any(job.get('status') in downloads.ACTIVE for job in state.get('jobs',())):return True
        job=live_recording.current()
        if job is not None and job.snapshot().get('state') in ('recording','stopping'):return True
        if getattr(live_recording,'_preparing',None) is not None:return True
    except Exception:
        # An unreadable activity state is not permission to replace running code.
        return True
    return False

def create_safety_backup():
    from .backup import BackupError,BackupManager
    from .settings import plugin_config_section
    section=plugin_config_section()
    preferred=getattr(getattr(section,'backup_folder',None),'value','') or '/media/hdd'
    if not isinstance(preferred,str) or not os.path.isabs(preferred):preferred='/media/hdd'
    manager=BackupManager();attempted=set();failure='storage'
    for parent in (preferred,BACKUP_FALLBACK):
        try:
            # Use the backup manager's mount, writable-storage and source-path
            # checks before creating a directory. /media/hdd may be a writable
            # placeholder on a tiny tmpfs even when no disk is connected.
            parent=manager.destination(parent)
            if parent in attempted:continue
            attempted.add(parent)
            folder=private_directory(os.path.join(parent,'gtiptv-update-backups'))
            # create() rechecks the destination and the actual archive size
            # under its persistence lock; never bypass backup to allow opkg.
            result=manager.create(folder,label='safety')
        except BackupError as error:
            if error.code not in ('storage','space'):
                raise UpdateError(error.code if error.code in ('busy','cancelled') else 'backup') from error
            failure=error.code
            continue
        except OSError as error:
            failure='space' if error.errno in (errno.ENOSPC,getattr(errno,'EDQUOT',errno.ENOSPC)) else 'storage'
            continue
        except UpdateError as error:
            if error.code!='storage':raise
            failure=error.code
            continue
        if not isinstance(result,dict) or not isinstance(result.get('path'),str) or not os.path.isfile(result['path']):
            raise UpdateError('backup')
        _trim_safety_backups(folder,result['path'])
        return result['path']
    raise UpdateError(failure)


def _trim_safety_backups(folder,current):
    # This dedicated directory owns only automatic pre-update backups.
    from .backup import EXTENSION
    try:
        owned=[]
        for name in os.listdir(folder):
            if name.startswith('GTIPTVPlayerPro-') and '-safety-' in name and name.endswith(EXTENSION):
                path=os.path.join(folder,name);info=os.lstat(path)
                if stat.S_ISREG(info.st_mode) and info.st_uid==os.geteuid() and path!=current:
                    owned.append((info.st_mtime_ns,path))
        for unused,path in sorted(owned,reverse=True)[2:]:os.unlink(path)
    except OSError:
        # Retention is housekeeping; a completed safety backup remains usable.
        pass

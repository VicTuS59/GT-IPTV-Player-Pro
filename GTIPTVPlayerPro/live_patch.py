"""Optional live-patch API. Importing the main plugin never loads its engine."""
import importlib
from pathlib import Path
import threading
import time

BRIDGE_API=1
_runtime=None
_lock=threading.RLock()
_addon_path=Path(__file__).resolve().parent.parent/'GTIPTVLivePatch'

def available():
    return ((_addon_path/'__init__.py').is_file() and
            (_addon_path/'engine/live_patch_runtime.py').is_file())

def _failure(action,error):
    try:
        from .diagnostics import log_event
        log_event('live_patch',action+' error_type='+type(error).__name__)
    except Exception:pass

def _load():
    global _runtime
    with _lock:
        if not available():return None
        if _runtime is None:
            try:
                parent=__package__.rpartition('.')[0]
                module=importlib.import_module((parent+'.' if parent else '')+'GTIPTVLivePatch')
                if getattr(module,'API_VERSION',None)!=BRIDGE_API:return None
                _runtime=module.bind(__package__)
            except Exception as error:
                _failure('load-failed',error)
                return None
        return _runtime

def enabled():
    return bool(_runtime is not None and _runtime.enabled())

def activate():
    runtime=_load()
    if runtime is None:return False
    try:return runtime.activate()
    except Exception as error:
        _failure('activation-failed',error)
        return False

def deactivate():
    if _runtime is None:return False
    try:return _runtime.deactivate()
    except Exception as error:
        _failure('cleanup-failed',error)
        return False

def _source(client):
    return str(getattr(client,'source_type','') or
        getattr(getattr(client,'account',None),'source_type','')).lower()

def supports(client):
    return _source(client) in ('stalker','xtream','m3u') and available()

def playback_result(client,item,value):
    if not enabled() or str(getattr(item,'content_type','')).lower()!='live':return value
    return _runtime.playback_result(client,item,value)

def stalker_result(client,name,args,kwargs,operation):
    return (_runtime.stalker_result(client,name,args,kwargs,operation)
        if enabled() else operation())

def arm_auto_recovery(player):
    if enabled():return _runtime.arm_auto_recovery(player)

def toggle_osd(player):
    source=_source(getattr(player,'client',None))
    if not enabled():return source in ('stalker','xtream')
    try:return _runtime.toggle_osd(player)
    except Exception as error:
        _failure('panel-failed',error)
        return source in ('stalker','xtream','m3u')

class UnlockCode:
    """Replay ordinary digits on mismatch; no engine or network ownership."""
    code='4668'
    timeout=2.5
    def __init__(self,clock=time.monotonic):
        self.clock=clock;self.pending='';self.last=0.0
    def reset(self):
        pending,self.pending=self.pending,'';self.last=0.0
        return pending
    def feed(self,digit):
        now=self.clock();released=''
        if self.pending and now-self.last>=self.timeout:released=self.reset()
        candidate=self.pending+str(digit);self.last=now
        if candidate==self.code:self.reset();return True,released
        while candidate and not self.code.startswith(candidate):
            released+=candidate[0];candidate=candidate[1:]
        self.pending=candidate
        return False,released

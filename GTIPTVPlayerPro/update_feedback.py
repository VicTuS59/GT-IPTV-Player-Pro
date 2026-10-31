# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded stage feedback; percentages describe measured bytes, never opkg time."""
from .update_protocol import PACKAGE

PHASE_LABELS={'verify':'Verifying update...','backup':'Creating backup...',
              'install':'Installing update...','configure':'Configuring update...'}

def emit(callback,phase,done=0,total=0):
    if callback is not None:
        try:callback(phase,done,total)
        except Exception:
            # A feedback consumer must never interrupt an active package manager.
            pass

def installer_phase(line):
    line=line.strip()
    if line.startswith('Configuring '+PACKAGE+'.'):return 'configure'
    if line.startswith(('Upgrading '+PACKAGE+' ','Installing '+PACKAGE+' ','Unpacking '+PACKAGE+' ')):return 'install'
    return None

def progress_geometry(done,total,tick=0):
    """Return offset/width in percent, with None for indeterminate progress."""
    if total>0:
        percent=max(0,min(100,int(done*100/total)))
        return 0,percent,percent
    # A moving segment conveys work without fabricating a completion percentage.
    step=int(tick)%30;offset=(step if step<=15 else 30-step)*5
    return offset,25,None

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Inspect a downloaded IPK without extracting or executing any member."""
import ast
import hashlib
import os
import stat
import tarfile
from pathlib import PurePosixPath
from .update_protocol import UpdateError,PACKAGE,PREFIX,MAX_INSTALLED

def _name(value):
    if value in ('.','./'):return '.'
    while value.startswith('./'):value=value[2:]
    if not value or value.startswith('/') or '\\' in value or '\x00' in value or '..' in value.split('/'):raise UpdateError('package')
    return str(PurePosixPath(value)).rstrip('/')

def _control(body):
    result={};continuation=False
    try:text=body.decode('utf-8')
    except UnicodeError:raise UpdateError('package')
    for line in text.splitlines():
        if line.startswith((' ','\t')):
            if not continuation:raise UpdateError('package')
            continue
        if not line.strip():continuation=False;continue
        key,colon,value=line.partition(':')
        if not colon or key in result:raise UpdateError('package')
        result[key]=value.strip();continuation=True
    return result

class _Slice:
    def __init__(self,file,size):self.file=file;self.left=size
    def read(self,size=-1):
        size=self.left if size<0 else min(size,self.left)
        data=self.file.read(size);self.left-=len(data);return data

def inspect_ipk(path,manifest,cancel=None):
    """Hash, ar/control identity and every tar path are checked before opkg."""
    descriptor=os.open(path,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
    with os.fdopen(descriptor,'rb') as handle:
        info=os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size!=manifest['bytes']:raise UpdateError('package')
        digest=hashlib.sha256()
        while True:
            if cancel is not None and cancel.is_set():raise UpdateError('cancelled')
            chunk=handle.read(65536)
            if not chunk:break
            digest.update(chunk)
        if digest.hexdigest()!=manifest['sha256']:raise UpdateError('package')
        handle.seek(0)
        if handle.read(8)!=b'!<arch>\n':raise UpdateError('package')
        controls=None;plugin_version=None;file_count=0;unpacked=0
        try:
            for expected in ('debian-binary','control.tar.gz','data.tar.gz'):
                header=handle.read(60)
                if len(header)!=60 or header[58:]!=b'`\n':raise UpdateError('package')
                name=header[:16].decode('ascii').strip().rstrip('/')
                length=int(header[48:58].decode('ascii').strip())
                start=handle.tell()
                if name!=expected or length<0 or start+length>info.st_size:raise UpdateError('package')
                if name=='debian-binary':
                    if length!=4 or handle.read(4)!=b'2.0\n':raise UpdateError('package')
                else:
                    seen=set();section_size=0
                    with tarfile.open(fileobj=_Slice(handle,length),mode='r|gz') as archive:
                        for member in archive:
                            if cancel is not None and cancel.is_set():raise UpdateError('cancelled')
                            key=_name(member.name)
                            if key in seen or not (member.isfile() or member.isdir()):raise UpdateError('package')
                            seen.add(key)
                            if len(seen)>6000 or member.size<0:raise UpdateError('package')
                            if name=='control.tar.gz':
                                if member.isdir():continue
                                if key not in ('control','postinst','prerm','postrm','conffiles'):raise UpdateError('package')
                                section_size+=member.size
                                if section_size>256*1024:raise UpdateError('package')
                                if key=='control':controls=_control(archive.extractfile(member).read(65537))
                            else:
                                if member.isdir():
                                    if not (key=='.' or key==PREFIX.rstrip('/') or key.startswith(PREFIX) or PREFIX.startswith(key+'/')):raise UpdateError('package')
                                    continue
                                if not key.startswith(PREFIX) or key==PREFIX.rstrip('/'):raise UpdateError('package')
                                file_count+=1;unpacked+=member.size
                                if unpacked>manifest['installed_bytes'] or unpacked>MAX_INSTALLED:raise UpdateError('package')
                                if key==PREFIX+'__init__.py':
                                    if member.size>65536:raise UpdateError('package')
                                    tree=ast.parse(archive.extractfile(member).read())
                                    values=[n.value.value for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.value,ast.Constant)
                                            and any(isinstance(t,ast.Name) and t.id=='PLUGIN_VERSION' for t in n.targets)]
                                    if len(values)!=1:raise UpdateError('package')
                                    plugin_version=values[0]
                handle.seek(start+length)
                if length%2 and handle.read(1)!=b'\n':raise UpdateError('package')
            if handle.read(1):raise UpdateError('package')
            if not controls or controls.get('Package')!=PACKAGE or controls.get('Version')!=manifest['version'] or controls.get('Architecture')!='all':raise UpdateError('package')
            declared=int(controls.get('Installed-Size','-1'))*1024
            if not unpacked<=declared<=manifest['installed_bytes'] or plugin_version!=manifest['version'] or not file_count:raise UpdateError('package')
        except UpdateError:raise
        except Exception:raise UpdateError('package')
    return dict(files=file_count,installed_bytes=declared,version=plugin_version)

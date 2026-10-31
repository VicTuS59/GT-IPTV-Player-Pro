#!/usr/bin/env python3
"""Advance local release version and notes before committing/publishing; no network."""
import argparse,ast,os,re,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
def version(value):
 if not re.fullmatch(r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})-r(0|[1-9][0-9]{0,5})',value):raise ValueError('Expected X.Y.Z-rN')
 return tuple(map(int,re.split(r'\.|-r',value)))
def prepare(target,notes_file,dry_run=False):
 init=ROOT/'GTIPTVPlayerPro/__init__.py';control=ROOT/'CONTROL/control';builder=ROOT/'scripts/build-ipk.sh';notes=ROOT/'RELEASE-NOTES.txt'
 raw=init.read_text(encoding='utf-8');old=re.search(r'PLUGIN_VERSION = "([^"]+)"',raw)[1]
 if version(target)<=version(old):raise ValueError('The new version must be higher than '+old)
 changes={init:raw.replace('PLUGIN_VERSION = "'+old+'"','PLUGIN_VERSION = "'+target+'"',1),control:re.sub(r'^Version: .+$','Version: '+target,control.read_text(encoding='utf-8'),count=1,flags=re.M),builder:builder.read_text(encoding='utf-8').replace('_'+old+'_all.ipk','_'+target+'_all.ipk'),notes:Path(notes_file).read_text(encoding='utf-8')}
 if not changes[notes].strip():raise ValueError('Release notes must not be empty')
 ast.parse(changes[init],feature_version=(3,9))
 if not dry_run:
  for path,text in changes.items():
   fd,tmp=tempfile.mkstemp(prefix='.release-',dir=path.parent)
   try:
    with os.fdopen(fd,'w',encoding='utf-8',newline='\n') as handle:handle.write(text)
    os.replace(tmp,path)
   finally:
    if os.path.exists(tmp):os.unlink(tmp)
 print(('Would prepare ' if dry_run else 'Prepared ')+target+' from '+old+'; no GitHub publication performed')
if __name__=='__main__':
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--version',required=True);p.add_argument('--notes-file',type=Path,required=True);p.add_argument('--dry-run',action='store_true');args=p.parse_args()
 prepare(args.version,args.notes_file,args.dry_run)

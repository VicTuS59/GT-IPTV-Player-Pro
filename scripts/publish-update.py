#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-or-later
"""Build/sign a stable release or refresh its signed pointer. No third-party Python dependency."""
import argparse,base64,hashlib,importlib.util,json,os,re,shutil,subprocess,sys,tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('_gt_update_publisher',ROOT/'GTIPTVPlayerPro/__init__.py',submodule_search_locations=[str(ROOT/'GTIPTVPlayerPro')])
pkg=importlib.util.module_from_spec(spec);sys.modules[spec.name]=pkg;spec.loader.exec_module(pkg)
from _gt_update_publisher.update_protocol import canonical,json_document,verify_envelope,verify_signature,version_tuple,REPOSITORY,PACKAGE,MAX_METADATA,MAX_PACKAGE,MAX_INSTALLED
from _gt_update_publisher.update_package import inspect_ipk
from _gt_update_publisher.update_trust import KEY_ID

class PublishError(Exception):pass
def command(argv,body=None):
 result=subprocess.run(argv,input=body,stdout=subprocess.PIPE,stderr=subprocess.PIPE,check=False)
 if result.returncode:raise PublishError('Command failed: '+Path(argv[0]).name+' '+(argv[1] if len(argv)>1 else ''))
 return result.stdout
def gh_api(endpoint,method='GET',data=None,optional=False):
 argv=['gh','api','--method',method,endpoint,'-H','Accept: application/vnd.github+json','-H','X-GitHub-Api-Version: 2026-03-10']
 if data is not None:argv+=['--input','-']
 result=subprocess.run(argv,input=canonical(data) if data is not None else None,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
 if result.returncode:
  if optional and b'HTTP 404' in result.stderr:return None
  raise PublishError('GitHub API failed: '+method+' '+endpoint.split('?')[0])
 return json.loads(result.stdout) if result.stdout else None
def validate_tag(tag):
 if not isinstance(tag,str) or not tag.startswith('v'):raise PublishError('Expected tag vX.Y.Z-rN')
 version_tuple(tag[1:]);return tag[1:]
def release_filename(version):return PACKAGE+'_'+version+'_all.ipk'
def sign(body,key_file):
 """OpenSSL on CI; cryptography fallback for an ordinary Windows workstation."""
 if shutil.which('openssl'):
  signature=command(['openssl','dgst','-sha256','-sign',str(key_file)],body)
 else:
  from cryptography.hazmat.primitives import hashes,serialization
  from cryptography.hazmat.primitives.asymmetric import padding
  key=serialization.load_pem_private_key(Path(key_file).read_bytes(),None)
  signature=key.sign(body,padding.PKCS1v15(),hashes.SHA256())
 if not verify_signature(body,signature):raise PublishError('Signing key does not match the public key pinned in R105')
 return signature
def make_metadata(ipk,key_file,notes):
 ipk=Path(ipk);control=(ROOT/'CONTROL/control').read_text(encoding='utf-8');version=re.search(r'^Version: (.+)$',control,re.M)[1]
 version_tuple(version)
 if version!=pkg.PLUGIN_VERSION or ipk.name!=release_filename(version):raise PublishError('Source/control/IPK version mismatch')
 size=ipk.stat().st_size
 if size>MAX_PACKAGE:raise PublishError('Package exceeds the receiver limit')
 manifest=dict(schema=1,repository=REPOSITORY,channel='stable',package=PACKAGE,version=version,architecture='all',python_min='3.9',python_max='3.14',url=f'https://github.com/{REPOSITORY}/releases/download/v{version}/{ipk.name}',sha256=hashlib.sha256(ipk.read_bytes()).hexdigest(),bytes=size,installed_bytes=MAX_INSTALLED,notes=notes[:4000])
 manifest['installed_bytes']=inspect_ipk(str(ipk),manifest)['installed_bytes']
 envelope=canonical(dict(key_id=KEY_ID,signed=manifest,signature=base64.b64encode(sign(canonical(manifest),key_file)).decode()))+b'\n'
 verify_envelope(envelope);return envelope
def advance_pointer(body,api=gh_api):
 manifest=verify_envelope(body)
 endpoint=f'repos/{REPOSITORY}/contents/updates/stable.json'
 old=api(endpoint+'?ref=main',optional=True)
 request=dict(message=f'Publish stable update {manifest["version"]}',branch='main',content=base64.b64encode(body).decode())
 if old:
  previous=base64.b64decode(old['content']);p=verify_envelope(previous)
  if version_tuple(manifest['version'])<version_tuple(p['version']):raise PublishError('Channel downgrade is forbidden')
  if version_tuple(manifest['version'])==version_tuple(p['version']):
   if json_document(body)!=json_document(previous):raise PublishError('A published version is immutable; increase its revision')
   return False
  request['sha']=old['sha']
 api(endpoint,method='PUT',data=request);return True
def released_assets(tag,api=gh_api):
 version=validate_tag(tag);release=api(f'repos/{REPOSITORY}/releases/tags/{tag}')
 if release.get('draft') or release.get('prerelease'):raise PublishError('Only published stable releases are supported')
 assets={a['name']:a for a in release.get('assets',())}
 expected=[release_filename(version),release_filename(version)+'.sha256','update-stable.json']
 if len(assets)!=len(release.get('assets',())) or not all(n in assets for n in expected):raise PublishError('Release assets are incomplete')
 for name in expected:
  if assets[name].get('state')!='uploaded':raise PublishError('Release asset is not ready')
 with tempfile.TemporaryDirectory(prefix='gt-update-release-') as d:
  args=['gh','release','download',tag,'--repo',REPOSITORY,'--dir',d]
  for name in expected:args+=['--pattern',name]
  command(args)
  ipk=Path(d)/expected[0];body=(Path(d)/'update-stable.json').read_bytes()
  m=verify_envelope(body)
  if m['version']!=version or assets[ipk.name]['size']!=m['bytes']:raise PublishError('Release version/size mismatch')
  inspect_ipk(str(ipk),m)
  expected_sha=f'{m["sha256"]}  {ipk.name}\n'.encode()
  if (Path(d)/(ipk.name+'.sha256')).read_bytes()!=expected_sha:raise PublishError('Release checksum mismatch')
  return body
def publish():
 if os.environ.get('GITHUB_REPOSITORY')!=REPOSITORY or os.environ.get('GITHUB_REF')!='refs/heads/main':raise PublishError('Publishing is restricted to trusted main')
 version=pkg.PLUGIN_VERSION;version_tuple(version);tag='v'+version
 secret=os.environ.pop('GTIPTV_UPDATE_SIGNING_KEY','')
 if not secret:raise PublishError('GTIPTV_UPDATE_SIGNING_KEY repository secret is missing')
 out=ROOT/'dist';out.mkdir(exist_ok=True);ipk=out/release_filename(version)
 command(['sh',str(ROOT/'scripts/build-ipk.sh'),str(ipk)])
 with tempfile.TemporaryDirectory(prefix='gt-update-sign-') as d:
  key=Path(d)/'key.pem';key.write_bytes(secret.encode());os.chmod(key,0o600);secret=None
  notes=(ROOT/'RELEASE-NOTES.txt').read_text(encoding='utf-8')[:4000]
  (out/'update-stable.json').write_bytes(make_metadata(ipk,key,notes))
 digest=hashlib.sha256(ipk.read_bytes()).hexdigest();(out/(ipk.name+'.sha256')).write_text(digest+'  '+ipk.name+'\n',encoding='ascii',newline='\n')
 existing=gh_api(f'repos/{REPOSITORY}/releases/tags/{tag}',optional=True)
 if existing and not existing.get('draft'):
  # A rerun can refresh a pointer, but never changes published release assets.
  if existing.get('prerelease'):raise PublishError('Only stable releases are supported')
  body=released_assets(tag)
  local=(out/'update-stable.json').read_bytes()
  if json_document(body)!=json_document(local):raise PublishError('Published build differs; increase revision')
 else:
  if not existing:
   command(['gh','release','create',tag,'--repo',REPOSITORY,'--draft','--target',os.environ['GITHUB_SHA'],'--title','GT IPTV Player Pro '+version,'--notes-file',str(ROOT/'RELEASE-NOTES.txt')])
  paths=[ipk,out/(ipk.name+'.sha256'),out/'update-stable.json']
  command(['gh','release','upload',tag,'--repo',REPOSITORY,'--clobber',*[str(p) for p in paths]])
  command(['gh','release','edit',tag,'--repo',REPOSITORY,'--draft=false','--prerelease=false','--latest=true'])
  body=released_assets(tag)
 advance_pointer(body)
 print('Published signed stable update '+version)
def main():
 parser=argparse.ArgumentParser(description=__doc__);sub=parser.add_subparsers(dest='mode',required=True)
 sub.add_parser('publish')
 a=sub.add_parser('refresh');a.add_argument('--tag',required=True)
 a=sub.add_parser('sign');a.add_argument('--ipk',type=Path,required=True);a.add_argument('--key',type=Path,required=True);a.add_argument('--output',type=Path,required=True)
 args=parser.parse_args()
 if args.mode=='publish':publish()
 elif args.mode=='refresh':
  if os.environ.get('GITHUB_REPOSITORY')!=REPOSITORY:raise PublishError('Wrong repository')
  advance_pointer(released_assets(args.tag));print('Signed stable release pointer verified and published')
 else:args.output.write_bytes(make_metadata(args.ipk,args.key,(ROOT/'RELEASE-NOTES.txt').read_text(encoding='utf-8')))
if __name__=='__main__':
 try:main()
 except Exception as error:
  # Do not print environment variables, PEM data or authenticated subprocess arguments.
  print('Update publishing failed: '+(str(error) if isinstance(error,PublishError) else type(error).__name__),file=sys.stderr);sys.exit(1)

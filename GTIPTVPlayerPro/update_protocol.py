# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Strict signed update metadata and bounded HTTPS transport; no GUI or installation."""
import base64
import hashlib
import hmac
import json
import os
import re
import ssl
import sys
import time
from urllib.parse import urlsplit
from urllib.request import Request,build_opener,HTTPSHandler,HTTPRedirectHandler,ProxyHandler
from urllib.error import HTTPError
from .update_trust import KEY_ID,RSA_N,RSA_E
from .update_errors import UpdateError,network_error

REPOSITORY='VicTuS59/GT-IPTV-Player-Pro'
PACKAGE='enigma2-plugin-extensions-gtiptvplayerpro'
PREFIX='usr/lib/enigma2/python/Plugins/Extensions/GTIPTVPlayerPro/'
CHANNELS=('stable',)
MAX_METADATA=64*1024
MAX_PACKAGE=64*1024*1024
MAX_INSTALLED=256*1024*1024
VERSION=re.compile(r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})-r(0|[1-9][0-9]{0,5})\Z')
SHA256=re.compile(r'[0-9a-f]{64}\Z')
ALLOWED_HOSTS=frozenset(('raw.githubusercontent.com','github.com','release-assets.githubusercontent.com','objects.githubusercontent.com'))

def version_tuple(value):
    if not isinstance(value,str):raise UpdateError('metadata')
    match=VERSION.fullmatch(value)
    if not match:raise UpdateError('metadata')
    return tuple(int(n) for n in match.groups())

def canonical(document):
    try:return json.dumps(document,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,UnicodeError,RecursionError):raise UpdateError('metadata')

def _pairs(rows):
    result={}
    for key,value in rows:
        if key in result:raise UpdateError('metadata')
        result[key]=value
    return result

def json_document(body,limit=MAX_METADATA):
    if not isinstance(body,bytes) or not 0<len(body)<=limit:raise UpdateError('metadata')
    try:
        return json.loads(body.decode('utf-8'),object_pairs_hook=_pairs,
                          parse_constant=lambda value:(_ for _ in ()).throw(UpdateError('metadata')))
    except (ValueError,UnicodeError,RecursionError):raise UpdateError('metadata')

def verify_signature(body,signature,n=RSA_N,e=RSA_E):
    """RFC 8017 RSASSA-PKCS1-v1_5/SHA-256 verification, fixed complete encoding.

    Public-key integer exponentiation has no secret operand. The publisher
    signs through OpenSSL; interoperability and negative vectors are tested
    independently with cryptography. No private operation is implemented here.
    """
    width=(n.bit_length()+7)//8
    if n.bit_length()!=3072 or e!=65537 or not isinstance(signature,bytes) or len(signature)!=width:return False
    number=int.from_bytes(signature,'big')
    if number>=n:return False
    digest_info=bytes.fromhex('3031300d060960864801650304020105000420')+hashlib.sha256(body).digest()
    encoded=b'\x00\x01'+b'\xff'*(width-len(digest_info)-3)+b'\x00'+digest_info
    return hmac.compare_digest(pow(number,e,n).to_bytes(width,'big'),encoded)

def safe_url(value):
    if not isinstance(value,str) or len(value)>4096 or any(ord(c)<33 for c in value):raise UpdateError('metadata')
    try:
        parts=urlsplit(value)
        if (parts.scheme!='https' or parts.hostname not in ALLOWED_HOSTS or parts.username is not None
                or parts.password is not None or parts.port not in (None,443) or parts.fragment):raise UpdateError('metadata')
    except ValueError:raise UpdateError('metadata')
    return value

def source_url(channel='stable'):
    if channel not in CHANNELS:raise UpdateError('metadata')
    return 'https://raw.githubusercontent.com/'+REPOSITORY+'/main/updates/'+channel+'.json'

def verify_envelope(body,channel='stable',python_version=None):
    document=json_document(body)
    if not isinstance(document,dict) or set(document)!= {'signed','signature','key_id'}:raise UpdateError('metadata')
    if document['key_id']!=KEY_ID:raise UpdateError('signature')
    signed=document['signed'];signature=document['signature']
    if not isinstance(signed,dict) or not isinstance(signature,str) or len(signature)!=512:raise UpdateError('signature')
    try:signature=base64.b64decode(signature,validate=True)
    except (ValueError,UnicodeError):raise UpdateError('signature')
    if not verify_signature(canonical(signed),signature):raise UpdateError('signature')
    fields={'schema','repository','channel','package','version','architecture','python_min','python_max','url','sha256','bytes','installed_bytes','notes'}
    if set(signed)!=fields or type(signed['schema']) is not int or signed['schema']!=1:raise UpdateError('metadata')
    if signed['repository']!=REPOSITORY or signed['package']!=PACKAGE or signed['channel']!=channel or channel not in CHANNELS:raise UpdateError('metadata')
    if signed['architecture']!='all':raise UpdateError('compatibility')
    version_tuple(signed['version'])
    for field,maximum in (('bytes',MAX_PACKAGE),('installed_bytes',MAX_INSTALLED)):
        if type(signed[field]) is not int or not 1<=signed[field]<=maximum:raise UpdateError('metadata')
    if not isinstance(signed['sha256'],str) or not SHA256.fullmatch(signed['sha256']):raise UpdateError('metadata')
    if not isinstance(signed['notes'],str) or len(signed['notes'])>4000:raise UpdateError('metadata')
    versions=[]
    for field in ('python_min','python_max'):
        value=signed[field]
        if not isinstance(value,str) or not re.fullmatch(r'[0-9]{1,2}\.[0-9]{1,2}',value):raise UpdateError('metadata')
        versions.append(tuple(int(n) for n in value.split('.')))
    current=tuple(python_version or sys.version_info[:2])
    if not versions[0]<=current<=versions[1]:raise UpdateError('compatibility')
    filename=PACKAGE+'_'+signed['version']+'_all.ipk'
    expected='https://github.com/'+REPOSITORY+'/releases/download/v'+signed['version']+'/'+filename
    if safe_url(signed['url'])!=expected:raise UpdateError('metadata')
    return dict(signed)

def check_cancel(cancel,deadline):
    if cancel is not None and cancel.is_set():raise UpdateError('cancelled')
    if time.monotonic()>deadline:raise UpdateError('network','timeout')

class _Redirect(HTTPRedirectHandler):
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        safe_url(newurl)
        return super().redirect_request(req,fp,code,msg,headers,newurl)

class UpdateTransport:
    def __init__(self):
        # Independent of IPTV provider transports and their credentials/options.
        try:self.context=ssl.create_default_context()
        except Exception as error:raise UpdateError('network','certificate') from error
        # Some receiver images compile OpenSSL against /usr/lib/ssl-1.1 while
        # ca-certificates installs the real trust bundle under /etc/ssl/certs.
        # Load that system bundle explicitly; certificate/hostname checks stay on.
        for path in ('/etc/ssl/certs/ca-certificates.crt','/etc/pki/tls/certs/ca-bundle.crt'):
            if os.path.isfile(path):
                try:self.context.load_verify_locations(cafile=path)
                except Exception as error:raise UpdateError('network','certificate') from error
                break
        self.opener=build_opener(ProxyHandler({}),HTTPSHandler(context=self.context),_Redirect())

    def transfer(self,url,maximum,cancel=None,sink=None,seconds=180):
        safe_url(url);deadline=time.monotonic()+seconds;check_cancel(cancel,deadline)
        req=Request(url,headers={'User-Agent':'GTIPTVPlayerPro-Updater/1','Accept-Encoding':'identity'})
        try:
            with self.opener.open(req,timeout=10) as response:
                safe_url(response.geturl())
                if response.getcode()!=200:raise UpdateError('network','http',status=response.getcode())
                length=response.headers.get('Content-Length')
                if length is not None and (not length.isdigit() or int(length)>maximum):raise UpdateError('metadata')
                if response.headers.get('Content-Encoding','identity').lower() not in ('','identity'):raise UpdateError('metadata')
                parts=[];total=0
                while True:
                    check_cancel(cancel,deadline)
                    chunk=response.read(min(65536,maximum-total+1))
                    if not chunk:break
                    total+=len(chunk)
                    if total>maximum:raise UpdateError('metadata')
                    if sink is None:parts.append(chunk)
                    else:sink(chunk)
                if length is not None and total!=int(length):raise UpdateError('network','interrupted')
                return b''.join(parts) if sink is None else total
        except UpdateError:raise
        except HTTPError as error:
            code=error.code
            try:error.close()
            except Exception:pass
            raise UpdateError('source' if code==404 else 'network','source' if code==404 else 'http',status=code) from error
        except Exception as error:raise network_error(error) from error

"""Rewrite HLS resources locally and describe complete, playable recording units."""
import math
import re
from urllib.parse import urljoin, urlsplit


def attributes(line):
    return dict(re.findall(r'([A-Z0-9-]+)=("[^"]*"|[^,]*)', line.split(':',1)[-1]))


def unquote(value): return value.strip('"')


def rewrite(body, base, register, playlist_id, role='video'):
    text = body.decode('utf-8-sig')
    if not text.lstrip().startswith('#EXTM3U'): raise ValueError('Not an HLS playlist')
    lines = text.splitlines()
    if len(lines) > 20000: raise ValueError('HLS playlist too large')
    if any(len(line)>16384 for line in lines): raise ValueError('HLS line too large')
    seq = next((int(x.split(':',1)[1]) for x in lines if x.startswith('#EXT-X-MEDIA-SEQUENCE:')),0)
    target = next((int(x.split(':',1)[1]) for x in lines if x.startswith('#EXT-X-TARGETDURATION:')),10)
    if seq < 0 or seq >= 2**128 or target <= 0: raise ValueError('Invalid HLS sequence')
    output=[]; duration=None; key=''; init=''; discontinuity=False; date=''; byte_range=None
    ends={}; unsupported=False
    for line in lines:
        line=line.strip()
        if line.startswith('#EXTINF:'):
            duration=float(line.split(':',1)[1].split(',',1)[0])
            if not math.isfinite(duration) or not 0 < duration <= 3600: raise ValueError('Invalid HLS duration')
        elif line.startswith('#EXT-X-BYTERANGE:'):
            value=line.split(':',1)[1].split('@');byte_range=(int(value[0]),int(value[1]) if len(value)>1 else None)
            continue
        elif line.startswith('#EXT-X-DISCONTINUITY') and ':' not in line:
            discontinuity=True
        elif line.startswith('#EXT-X-PROGRAM-DATE-TIME:'):
            date=line.split(':',1)[1]
        elif line.startswith(('#EXT-X-KEY:','#EXT-X-MAP:','#EXT-X-MEDIA:','#EXT-X-I-FRAME-STREAM-INF:', '#EXT-X-SESSION-KEY:')):
            values=attributes(line); uri=unquote(values.get('URI',''))
            kind='key' if line.startswith(('#EXT-X-KEY:','#EXT-X-SESSION-KEY:')) else ('map' if line.startswith('#EXT-X-MAP:') else 'playlist')
            media_type=unquote(values.get('TYPE',''))
            child_role={'AUDIO':'audio','SUBTITLES':'subtitle'}.get(media_type,role)
            if line.startswith('#EXT-X-I-FRAME-STREAM-INF:'): child_role='aux'
            selected_range=None
            if kind=='map' and values.get('BYTERANGE'):
                spec=unquote(values['BYTERANGE']).split('@');selected_range=(int(spec[0]),int(spec[1]) if len(spec)>1 else 0)
            if uri:
                if urlsplit(urljoin(base,uri)).scheme not in ('http','https'):
                    unsupported=True
                    output.append(line)
                    if kind=='key': key=''
                    continue
                resource=register(urljoin(base,uri),kind=kind,role=child_role,byte_range=selected_range)
                line=line.replace('URI="'+uri+'"','URI="'+resource['local']+'"')
                if selected_range: line=re.sub(r',?BYTERANGE="[^"]+"','',line)
                saved=line.replace(resource['local'],resource['name'])
            else: saved=line
            if kind=='key':
                method=unquote(values.get('METHOD','NONE'))
                if method not in ('NONE','AES-128') or line.startswith('#EXT-X-SESSION-KEY:'): unsupported=True
                key=saved
            elif kind=='map': init=saved
        elif line and not line.startswith('#'):
            if duration is None:
                resource=register(urljoin(base,line),kind='playlist',role=role)
            else:
                selected_range=None
                if byte_range:
                    length,start=byte_range;url=urljoin(base,line)
                    start=ends.get(url,0) if start is None else start
                    if length <= 0 or start < 0: raise ValueError('Invalid HLS range')
                    selected_range=(length,start);ends[url]=start+length
                resource=register(urljoin(base,line),kind='segment',role=role,byte_range=selected_range,sequence=seq,playlist=playlist_id)
                record_key=key
                if record_key.startswith('#EXT-X-KEY:') and 'METHOD=AES-128' in record_key and 'IV=' not in record_key:
                    record_key+=',IV=0x%032x'%seq
                resource['meta']=dict(segment=True,name=resource['name'],seq=seq,playlist=playlist_id,
                    role=role,duration=duration,target=max(target,int(math.ceil(duration))),key=record_key,
                    map=init,discontinuity=discontinuity,date=date)
                seq+=1;duration=None;discontinuity=False;date='';byte_range=None
            line=resource['local']
        output.append(line)
    return ('\n'.join(output)+'\n').encode('utf-8'),unsupported

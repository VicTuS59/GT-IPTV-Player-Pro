# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conservative match identity checks for public YouTube metadata, no playback."""
import datetime as dt
import html
import json
import re
import unicodedata
from urllib.parse import unquote,urlsplit

from .match_highlights import available

VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
CHANNEL_ID = re.compile(r'^UC[A-Za-z0-9_-]{22}$')
HIGHLIGHTS = re.compile(r'\b(?:highlights?|o?zet(?:i|ler[i]?)?|zusammenfassung|spielzusammenfassung|resumen|resum|resume|resumo|sintesi|samenvatting|sammandrag|kooste|obzor|обзор|огляд|ملخص|خلاصه)\b|集锦|精華', re.I)
GAMES = re.compile(r'\b(?:e\s*football|eafc|ea\s*(?:sports\s*)?fc\s*\d{2}|fifa\s*\d{2,4}|pes\s*\d{2,4}|pro\s*evolution\s*soccer|football\s*manager|gameplay|gaming|simulation|simulasyon|simulacao|simulacion|simulazione|simulated|simulator|esports?|e\s*sports|ps[345]|playstation|xbox|video\s*game)\b', re.I)
OTHER = re.compile(r'\b(?:reaction|reacts?|prediction|preview|watchalong|press\s*conference|interview|tahmin|roportaj|basin\s*toplantisi|mac\s*sonu\s*yorum|macinin\s*ardindan|nach\s*dem\s*spiel|pressekonferenz)\b', re.I)
YOUTH = re.compile(r'\b(?:u\s*(?:17|18|19|20|21|23)|women|womens|femenin[oa]|feminin|frauen|kadinlar|reserves?|legends)\b')
ALIASES = {
    'bayern munich':('bayern munchen','fc bayern munchen','bayern'),
    'bayern munchen':('bayern munich','fc bayern munchen','bayern'),
    'manchester united':('man united','man utd','manchester utd'),
    'manchester city':('man city',), 'tottenham hotspur':('tottenham','spurs'),
    'wolverhampton wanderers':('wolves','wolverhampton'),
    'west ham united':('west ham',), 'newcastle united':('newcastle',),
    'brighton hove albion':('brighton','brighton and hove albion'),
    'athletic club':('athletic bilbao',), 'atletico madrid':('atletico de madrid',),
    'paris saint germain':('psg',), 'internazionale':('inter milan','inter'),
    'inter milan':('internazionale','inter'), 'ac milan':('milan',),
    'borussia monchengladbach':('monchengladbach','gladbach'),
    'borussia dortmund':('dortmund','bvb'), 'bayer leverkusen':('bayer 04 leverkusen','leverkusen'),
    'istanbul basaksehir':('basaksehir','basaksehir fk'),
    'amed sfk':('amedspor','amed sportif faaliyetler'),
    'erzurum bb':('erzurumspor','erzurumspor fk','erzurum bb'),
}
LEAGUE_ZONES={'tur.1':'Europe/Istanbul','eng.1':'Europe/London','ger.1':'Europe/Berlin',
    'ita.1':'Europe/Rome','esp.1':'Europe/Madrid','fra.1':'Europe/Paris','ned.1':'Europe/Amsterdam','por.1':'Europe/Lisbon'}


def fold(value):
    value=unicodedata.normalize('NFKD',html.unescape(str(value or '')).casefold().replace('ı','i'))
    value=''.join(c for c in value if not unicodedata.combining(c))
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in value).split())


def aliases(name):
    value=fold(name);result={value,*ALIASES.get(value,())}
    tokens=value.split()
    if len(tokens)>1:
        compact=' '.join(t for t in tokens if t not in ('fc','afc','cf','sc','fk'))
        if len(compact)>=4:result.add(compact)
    return {v for v in result if v and v not in ('united','city','real','sporting','athletic')}


def team_positions(title,match):
    value=fold(title)
    positions=[]
    for name in (match.home,match.away):
        found=[]
        for alias in aliases(name):
            for item in re.finditer(r'(?<!\w)'+re.escape(alias)+r'(?!\w)',value):found.append(item.span())
        positions.append(found)
    return next(((h[0],a[0]) for h in positions[0] for a in positions[1]
                 if h[1]<=a[0] or a[1]<=h[0]),None)


def candidate_ok(entry,match):
    if not available(match) or not VIDEO_ID.fullmatch(str(entry.get('id',''))):return False
    title=str(entry.get('title') or '')[:512];normal=fold(title)
    if entry.get('live') or GAMES.search(normal) or OTHER.search(normal):return False
    if YOUTH.search(normal) and not YOUTH.search(fold(match.home+' '+match.away)):return False
    return team_positions(title,match) is not None


def _label(value):
    if not isinstance(value,dict):return ''
    return str(value.get('simpleText') or ''.join(str(x.get('text','')) for x in value.get('runs',[]) if isinstance(x,dict)))


def metadata_from_page(page,video_id):
    if not VIDEO_ID.fullmatch(video_id):raise ValueError('Invalid video ID')
    found=re.search(r'(?:var\s+)?ytInitialPlayerResponse\s*=\s*|window\["ytInitialPlayerResponse"\]\s*=\s*',page)
    if not found:raise ValueError('Video date metadata unavailable')
    response=json.JSONDecoder().raw_decode(page[found.end():].lstrip())[0]
    if not isinstance(response,dict):raise ValueError('Invalid metadata')
    details=response.get('videoDetails') or {};micro=(response.get('microformat') or {}).get('playerMicroformatRenderer') or {}
    if not isinstance(details,dict) or not isinstance(micro,dict) or details.get('videoId')!=video_id:raise ValueError('Video identity mismatch')
    status=response.get('playabilityStatus') or {}
    if not isinstance(status,dict) or status.get('status')!='OK':raise ValueError('Video unavailable')
    channel=str(details.get('channelId') or '')
    if not CHANNEL_ID.fullmatch(channel):raise ValueError('Channel identity unavailable')
    # Retain only public identity fields, never formats, signed media URLs or cookies.
    return {'id':video_id,'title':str(details.get('title') or _label(micro.get('title')))[:512],
        'description':str(details.get('shortDescription') or _label(micro.get('description')))[:10000],
        'channel_id':channel,'channel':str(details.get('author') or '')[:120],
        'owner_url':str(micro.get('ownerProfileUrl') or '')[:200],
        'publish_date':str(micro.get('publishDate') or '')[:64],'upload_date':str(micro.get('uploadDate') or '')[:64],
        'category':str(micro.get('category') or '')[:64],
        'tags':[str(v)[:100] for v in (details.get('keywords') or [])[:60] if isinstance(v,str)],
        'live':bool(details.get('isLive') or micro.get('liveBroadcastDetails'))}


def official(metadata,registry):
    channel=metadata.get('channel_id','')
    try:
        owner=urlsplit(metadata.get('owner_url',''))
        handle=unquote(owner.path).strip('/').casefold() if owner.scheme in ('https','http') and owner.hostname in ('youtube.com','www.youtube.com') and not owner.username and not owner.password else ''
    except ValueError:handle=''
    for item in registry:
        if channel in item.get('channel_ids',()) or handle and handle in item.get('handles',()):return True
    return False


def event_days(match):
    days={dt.datetime.fromtimestamp(match.kickoff).date(),dt.datetime.fromtimestamp(match.kickoff,dt.timezone.utc).date()}
    try:
        from .timezone_compat import ZoneInfo
        zone=LEAGUE_ZONES.get(match.league,'Europe/Berlin')
        days.add(dt.datetime.fromtimestamp(match.kickoff,ZoneInfo(zone)).date())
    except (ImportError,ValueError,KeyError):pass
    return days


def _date(value):
    try:return dt.date.fromisoformat(str(value)[:10])
    except (TypeError,ValueError):return None


def _timestamp(value):
    try:
        parsed=dt.datetime.fromisoformat(str(value).replace('Z','+00:00'))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (TypeError,ValueError,OverflowError,OSError):return None


def written_dates(value):
    dates=set()
    for year,month,day in re.findall(r'(?<!\d)(20\d{2})[-/.](\d{1,2})[-/.](\d{1,2})(?!\d)',value):
        try:dates.add(dt.date(int(year),int(month),int(day)))
        except ValueError:pass
    for day,month,year in re.findall(r'(?<!\d)(\d{1,2})[./-](\d{1,2})[./-](20\d{2})(?!\d)',value):
        try:dates.add(dt.date(int(year),int(month),int(day)))
        except ValueError:pass
    months={'january':1,'jan':1,'ocak':1,'januar':1,'february':2,'feb':2,'subat':2,'februar':2,
        'march':3,'mar':3,'mart':3,'marz':3,'april':4,'apr':4,'nisan':4,'may':5,'mayis':5,'mai':5,
        'june':6,'jun':6,'haziran':6,'juni':6,'july':7,'jul':7,'temmuz':7,'juli':7,
        'august':8,'aug':8,'agustos':8,'september':9,'sep':9,'sept':9,'eylul':9,
        'october':10,'oct':10,'ekim':10,'oktober':10,'okt':10,'november':11,'nov':11,'kasim':11,
        'december':12,'dec':12,'aralik':12,'dezember':12,'dez':12}
    normal=fold(value)
    for day,month,year in re.findall(r'\b(\d{1,2}) (\w+) (20\d{2})\b',normal):
        try:dates.add(dt.date(int(year),months[month],int(day)))
        except (ValueError,KeyError):pass
    for month,day,year in re.findall(r'\b(\w+) (\d{1,2}) (20\d{2})\b',normal):
        try:dates.add(dt.date(int(year),months[month],int(day)))
        except (ValueError,KeyError):pass
    return dates


def seasons(value):
    found=set()
    for start,end in re.findall(r'\b(20\d{2})\s*[/–-]\s*(20\d{2}|\d{2})\b',value):
        start=int(start);end=int(end) if len(end)==4 else 2000+int(end)
        if end==start+1:found.add((start,end))
    return found


def verify(entry,metadata,match,registry):
    """Fail closed unless date and fixture identity can be tied to this match."""
    if not candidate_ok(entry,match) or metadata.get('id')!=entry.get('id'):return None,'candidate'
    authoritative=dict(entry,title=metadata.get('title',''),live=metadata.get('live'))
    if not candidate_ok(authoritative,match):return None,'title'
    description=str(metadata.get('description') or '')[:10000]
    content=metadata.get('title','')+'\n'+description
    normal=fold(content+' '+' '.join(metadata.get('tags') or []))
    if fold(metadata.get('category'))=='gaming' or GAMES.search(normal):return None,'game'
    if OTHER.search(fold(metadata.get('title',''))):return None,'commentary'
    if not HIGHLIGHTS.search(fold(metadata.get('title','')+' '+description[:300])):return None,'not_highlights'
    days=event_days(match);utc_day=dt.datetime.fromtimestamp(match.kickoff,dt.timezone.utc).date()
    values=[metadata.get('publish_date'),metadata.get('upload_date')]
    published=[_date(v) for v in values if v];published=[v for v in published if v]
    if not published:return None,'missing_date'
    # Both original upload and public release must be close to the fixture.
    if any(not utc_day-dt.timedelta(days=1)<=v<=utc_day+dt.timedelta(days=4) for v in published):return None,'publication_date'
    stamps=[_timestamp(v) for v in values if v];stamps=[v for v in stamps if v is not None]
    if any(v<match.kickoff or v>match.kickoff+4*86400 for v in stamps):return None,'publication_time'
    written=written_dates(content)
    if written-days:return None,'match_date'
    day=dt.datetime.fromtimestamp(match.kickoff,dt.timezone.utc)
    start=day.year if day.month>=7 else day.year-1
    expected_season=(start,start+1);found_seasons=seasons(content)
    if any(v!=expected_season for v in found_seasons):return None,'season'
    trusted=official(metadata,registry)
    exact_date=bool(written.intersection(days))
    if not exact_date:
        # One home league fixture per season: require its published season AND
        # exact home-away score from a known official channel. UEFA repeats
        # opponents across rounds, so it always needs an explicit match date.
        position=team_positions(metadata.get('title',''),match)
        score=bool(match.home_score is not None and match.away_score is not None and re.search(
            r'(?<!\d)'+str(match.home_score)+r'\s*[-–:]\s*'+str(match.away_score)+r'(?!\d)',metadata.get('title','')+' '+description[:1200]))
        if not (trusted and match.league in LEAGUE_ZONES and expected_season in found_seasons
                and position and position[0]<position[1] and score):return None,'unverified_fixture'
    result=dict(entry,title=metadata['title'],channel=metadata.get('channel') or entry.get('channel',''),
        official=trusted,match_date_verified=True,channel_id=metadata.get('channel_id',''),
        verified_publish_date=metadata.get('publish_date') or metadata.get('upload_date'))
    return result,'verified'

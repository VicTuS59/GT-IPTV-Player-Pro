# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded public TV listings, matched only to ESPN-confirmed live fixtures."""
import codecs
import json
import os
import re
import sys
import tempfile
import unicodedata
from collections import namedtuple
from html.parser import HTMLParser
from urllib.request import Request

from .matches import Budget, Cancelled, REFRESH_SECONDS, ScoresError, text

URL = 'https://www.liveonsat.com/2day.php'
COUNTRY_PATH = '/etc/enigma2/gtiptvplayer/matches-country.json'
COUNTRIES = (('ALL','All'),('TR','Turkey'),('DE','Germany'),('FR','France'),
    ('GB','United Kingdom'),('IE','Ireland'),('IT','Italy'),('ES','Spain'),
    ('PT','Portugal'),('NL','Netherlands'),('BE','Belgium'),('PL','Poland'),
    ('RO','Romania'),('BG','Bulgaria'),('GR','Greece'),('HU','Hungary'),
    ('CZ','Czechia'),('SK','Slovakia'),('DK','Denmark'),('SE','Sweden'),
    ('NO','Norway'),('FI','Finland'),('HR','Croatia'),('RS','Serbia'),
    ('BA','Bosnia and Herzegovina'),('SI','Slovenia'),('AL','Albania'),
    ('US','United States'),('CA','Canada'),('BR','Brazil'),('AU','Australia'),
    ('JP','Japan'),('MENA','MENA'))
COUNTRY_CODES = frozenset(key for key, unused in COUNTRIES)
# Busy match days exceed 2 MiB; this caps transfer, not retained memory.
# Keep 32 KiB reads and the existing parser/guide/cache memory limits.
WIRE_LIMIT = 8 * 1024 * 1024
BUFFER_LIMIT = 256 * 1024
ROWS_LIMIT = 128
CHANNEL_LIMIT = 96
GUIDE_BYTES_LIMIT = 512 * 1024  # Shared with, never added to, the 4 MiB scores pool.
Channel = namedtuple('Channel', 'name countries')
Broadcast = namedtuple('Broadcast', 'match channels')
BroadcastSnapshot = namedtuple('BroadcastSnapshot', 'matches updated_at failed partial')

def load_country(path=COUNTRY_PATH):
    try:
        if os.path.islink(path):return 'ALL'
        with open(path, 'rb') as source:raw = source.read(2049)
        if len(raw) > 2048:return 'ALL'
        data = json.loads(raw.decode('utf-8'))
        value = data.get('country') if isinstance(data, dict) and data.get('version') == 1 else None
        return value if isinstance(value, str) and value in COUNTRY_CODES else 'ALL'
    except (OSError, ValueError, TypeError):return 'ALL'

def save_country(country, path=COUNTRY_PATH):
    if country not in COUNTRY_CODES:raise ValueError('Invalid broadcast country')
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode='wb', dir=folder, prefix='.matches-country-', delete=False) as target:
            temporary = target.name
            target.write(json.dumps({'version':1, 'country':country}, separators=(',',':')).encode('utf-8'))
            target.flush();os.fsync(target.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path);temporary = None
    finally:
        if temporary is not None:
            try:os.unlink(temporary)
            except OSError:pass

_REGIONS = {
    'TR':r'\b(turkiye|turkey|turkish|s sport|spor smart|tivibu|tabii|trt|a spor)\b',
    'DE':r'\b(deutsch|germany|german)\b', 'FR':r'\b(france|french)\b',
    'GB':r'\b(uk|gb|britain|sky sports|tnt sports)\b', 'IE':r'\b(ireland|irish)\b',
    'IT':r'\b(italia|italy|italian)\b', 'ES':r'\b(espana|spain|spanish|movistar|m\+ laliga|laliga tv bar|laliga hypermotion)\b',
    'PT':r'\b(portugal|portuguese)\b', 'NL':r'\b(nederland|netherlands|dutch|ziggo)\b',
    'BE':r'\b(belgium|belgique|belgie|belgian|play sports)\b', 'PL':r'\b(polska|poland|polish|tvp)\b',
    'RO':r'\b(romania|romanian|ro)\b', 'BG':r'\b(bulgaria|bulgarian|diema|b tv|btv)\b',
    'GR':r'\b(hellas|greece|greek|cosmote)\b', 'HU':r'\b(hungary|hungarian|magyar)\b',
    'CZ':r'\b(czech|czechia)\b', 'SK':r'\b(slovakia|slovak)\b',
    'DK':r'\b(danmark|denmark|dansk|danish)\b', 'SE':r'\b(sverige|sweden|swedish)\b',
    'NO':r'\b(norge|norway|norwegian)\b', 'FI':r'\b(suomi|finland|finnish|urheilu)\b',
    'HR':r'\b(hrvatska|croatia|croatian)\b', 'RS':r'\b(srbija|serbia|serbian)\b',
    'BA':r'\b(bih|bosnia)\b', 'SI':r'\b(slovenija|slovenia|slovenian)\b',
    'AL':r'\b(albania|albanian|shqip)\b', 'US':r'\b(usa|us|united states|espn\+|paramount\+|peacock)\b',
    'CA':r'\b(canada|canadian)\b', 'BR':r'\b(brazil|brasil|brazilian)\b',
    'AU':r'\b(australia|australian)\b', 'JP':r'\b(japan|japanese)\b',
    'MENA':r'\b(mena|al kass|alkass)\b',
}
_REGION_PATTERNS = tuple((key, re.compile(value)) for key,value in _REGIONS.items())
def normal(value):
    value = str(value).casefold().replace('ı','i').replace('ø','o').replace('ł','l').replace('đ','d')
    value = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^\w+]+', ' ', value, flags=re.UNICODE).split())

def channel_countries(name):
    # An explicit geographic suffix wins over a network's default country.
    value = normal(name)
    if re.search(r'\b(mena|al kass|alkass)\b',value):return ('MENA',)
    explicit = [(key,pattern) for key,pattern in _REGION_PATTERNS if key not in ('TR','GB','ES','NL','BE','PL','BG','GR','US')]
    found = tuple(key for key,pattern in explicit if pattern.search(value))
    if found:return found
    return tuple(key for key,pattern in _REGION_PATTERNS if pattern.search(value))

_ALIASES = {
    'man utd':'manchester united', 'man united':'manchester united', 'man city':'manchester city',
    'nottm forest':'nottingham forest', 'nottm forest fc':'nottingham forest',
    'spurs':'tottenham hotspur', 'tottenham':'tottenham hotspur',
    'bayern munchen':'bayern munich', 'bayern':'bayern munich',
    'inter':'internazionale', 'inter milan':'internazionale', 'milan':'ac milan',
    'psg':'paris saint germain', 'paris sg':'paris saint germain',
    'atletico madrid':'atletico madrid', 'athletic club':'athletic bilbao',
    'rizespor':'caykur rizespor', 'istanbul basaksehir':'istanbul basaksehir', 'basaksehir':'istanbul basaksehir',
    'sparta':'sparta rotterdam', 'fortuna':'fortuna sittard',
    'rb salzburg':'red bull salzburg', 'salzburg':'red bull salzburg',
    'sporting cp':'sporting lisbon', 'sporting':'sporting lisbon',
    'viktoria plzen':'viktoria plzen', 'bayer leverkusen':'bayer leverkusen', 'leverkusen':'bayer leverkusen',
}
def team_key(name):
    words = normal(name).split()
    words = [word for word in words if word not in ('fc','cf','afc','sc','fk','sk')]
    key = ' '.join(words)
    return _ALIASES.get(key, key)

def demographic(name):
    value = normal(name)
    women = bool(re.search(r'\b(women|womens|feminine|femenina|frauen|femminile|we league)\b',value))
    ages = tuple(sorted(set(re.findall(r'\b(?:u\s*|under\s*)(1[5-9]|2[0-3])\b',value))))
    return women, ages

class ListingParser(HTMLParser):
    """Consume markup incrementally; never load JS, images, links or a DOM."""
    def __init__(self, live_matches, budget):
        super().__init__(convert_charrefs=True)
        self.budget=budget;self.live=tuple(live_matches);self.records=[];self.weight=0
        self.depth=0;self.block_depth=None;self.fix_depth=None;self.capture=None
        self.competition='';self.fixture=None;self.parsed=0;self.matched=0;self.partial=False
        self.peak_buffer=0;self.seen_channels=0;self.timed_fixtures=0

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs);classes=attrs.get('class','').split()
        if tag=='div':
            self.depth+=1
            if self.depth>64:raise ScoresError('Listing nesting limit')
            if 'blockfix' in classes:
                if self.fixture is not None:raise ScoresError('Invalid listing boundary')
                self.block_depth=self.depth;self.fixture={'name':'','competition':self.competition,'kickoff':None,'channels':[],'notes':''}
            if self.fixture is not None and 'fix_text' in classes:self.fix_depth=self.depth
            if self.fix_depth and 'fLeft' in classes:self.capture=['teams','div',self.depth,[]]
            if self.fixture is not None and 'notes' in classes:self.capture=['notes','div',self.depth,[]]
        if tag=='span' and 'comp_head' in classes:self.capture=['competition','span',None,[]]
        if self.fixture is not None:
            if 'fLeft_time_live' in classes and 'data-timestamp' in attrs and self.fixture['kickoff'] is None:
                value=attrs['data-timestamp']
                if re.fullmatch(r'[0-9]{9,11}',value):self.fixture['kickoff']=int(value)
            if tag=='a' and any(c.startswith('chan_live_') for c in classes):
                self.capture=['channel','a',None,[]]

    def handle_data(self, data):
        if self.capture is not None and sum(len(v) for v in self.capture[3])<512:
            self.capture[3].append(data[:512])

    def handle_endtag(self, tag):
        capture=self.capture
        if capture and tag==capture[1] and (tag!='div' or capture[2]==self.depth):
            value=text(''.join(capture[3]),120)
            kind=capture[0];self.capture=None
            if kind=='competition':self.competition=value
            elif self.fixture is not None:
                if kind=='teams':self.fixture['name']=value
                elif kind=='notes':self.fixture['notes']=value
                elif kind=='channel' and value:
                    self.seen_channels+=1
                    channels=self.fixture['channels']
                    value=text(re.sub(r'\s*(?:📺|\(\$/geo/R\)|\(geo/R\)|\[online\])', '',value, flags=re.I),96)
                    if value not in channels:
                        if len(channels)<CHANNEL_LIMIT:channels.append(value)
                        else:self.partial=True
        if tag=='div':
            if self.fix_depth==self.depth:self.fix_depth=None
            if self.block_depth==self.depth:
                self._finish();self.fixture=None;self.block_depth=None
            self.depth=max(0,self.depth-1)

    def feed(self, data):
        self.budget.check();super().feed(data)
        self.peak_buffer=max(self.peak_buffer,len(self.rawdata))
        if len(self.rawdata)>BUFFER_LIMIT:raise ScoresError('Listing token limit')

    def _finish(self):
        self.budget.check();self.parsed+=1
        if self.parsed>2048:raise ScoresError('Listing count limit')
        entry=self.fixture
        if entry['kickoff']:self.timed_fixtures+=1
        if not entry['kickoff'] or not entry['channels'] or re.search(r'\b(postponed|cancelled|canceled|abandoned)\b',entry['name']+' '+entry['notes'],re.I):return
        pair=re.split(r'\s+(?:v|vs\.?|–|—)\s+',entry['name'],maxsplit=1,flags=re.I)
        if len(pair)!=2:return
        home,away=map(team_key,pair)
        kind=demographic(entry['competition']+' '+entry['name'])
        candidates=[]
        from .matches import LEAGUE_NAMES
        for match in self.live:
            if (match.category=='live' and abs(match.kickoff-entry['kickoff'])<=600
                    and team_key(match.home)==home and team_key(match.away)==away
                    and demographic(LEAGUE_NAMES.get(match.league,'')+' '+match.home+' '+match.away)==kind):candidates.append(match)
        if len(candidates)!=1:return
        match=candidates[0]
        prior=next((i for i,row in enumerate(self.records) if row.match.id==match.id),None)
        channels=tuple(Channel(name,channel_countries(name)) for name in entry['channels'])
        old_size=0
        if prior is not None:
            old=self.records[prior]
            old_size=self._row_bytes(old)
            names={c.name for c in old.channels}
            channels=old.channels+tuple(c for c in channels if c.name not in names)
            if len(channels)>CHANNEL_LIMIT:self.partial=True;channels=channels[:CHANNEL_LIMIT]
        row=Broadcast(match,channels)
        size=self._row_bytes(row)
        if (prior is None and len(self.records)>=ROWS_LIMIT) or self.weight+size-old_size>GUIDE_BYTES_LIMIT:self.partial=True;return
        if prior is None:self.records.append(row);self.matched+=1
        else:self.records[prior]=row
        self.weight+=size-old_size

    @staticmethod
    def _row_bytes(row):
        return (256+sys.getsizeof(row)+sys.getsizeof(row.match)+sum(sys.getsizeof(v) for v in row.match)
                +sys.getsizeof(row.channels)+sum(sys.getsizeof(c)+sys.getsizeof(c.name)+sys.getsizeof(c.countries)
                +sum(sys.getsizeof(v) for v in c.countries) for c in row.channels))

def read_listings(opener, live_matches, budget):
    request=Request(URL,headers={'Accept':'text/html','Accept-Encoding':'identity','User-Agent':'GTIPTVPlayerPro/1.7.0 (TV guide)'})
    parser=ListingParser(live_matches,budget);decoder=codecs.getincrementaldecoder('utf-8-sig')()
    count=0
    with opener(request,timeout=budget.timeout()) as response:
        while True:
            budget.check();chunk=response.read(min(32768,WIRE_LIMIT+1-count));count+=len(chunk)
            if count>WIRE_LIMIT:raise ScoresError('Listing wire limit')
            parser.feed(decoder.decode(chunk,final=not chunk))
            if not chunk:break
        parser.close()
    budget.check()
    if not parser.parsed or not parser.seen_channels or not parser.timed_fixtures or parser.fixture is not None:raise ScoresError('Unrecognised listings')
    return tuple(parser.records),parser.weight,parser.partial

def filter_broadcasts(snapshot,country):
    if snapshot is None:return ()
    result=[]
    for row in snapshot.matches:
        channels=tuple(c for c in row.channels if country=='ALL' or country in c.countries)
        if channels:result.append(Broadcast(row.match,channels))
    return tuple(result)

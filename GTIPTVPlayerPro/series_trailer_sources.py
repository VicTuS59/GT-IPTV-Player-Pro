# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Official series/season trailer fallbacks and bounded publisher-page embeds."""
import datetime
import re
import time
from html.parser import HTMLParser
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

from . import series_trailer as core

# Domains are tied to the series' metadata publisher; arbitrary portal URLs
# never become playback sources. Embedded YouTube videos use the native player.
PUBLISHERS = (
    (('apple','appletv'),('apple.com',)),
    (('zdf',),('zdf.de',)), (('ard','daserste'),('ardmediathek.de','daserste.de')),
    (('bbc','bbcone','bbctwo','bbcthree'),('bbc.co.uk','bbc.com')),
    (('itv','itv1'),('itv.com',)), (('channel4',),('channel4.com',)),
    (('netflix',),('netflix.com','netflix.com.br')),
    (('amazon','primevideo','amazonprimevideo'),('amazon.com','primevideo.com','amazon.co.uk','amazon.de')),
    (('hbo','max','hbomax'),('hbo.com','max.com')),
    (('disney','disneyplus','disneychannel'),('disney.com','disneyplus.com')),
    (('paramount','paramountplus','cbs'),('paramountplus.com','cbs.com')),
    (('nbc','peacock'),('nbc.com','peacocktv.com')),
    (('fox',),('fox.com',)), (('abc',),('abc.com',)),
    (('tf1',),('tf1.fr','tf1info.fr')), (('france2','france3','france5','francetelevisions'),('france.tv','francetelevisions.fr')),
    (('arte',),('arte.tv',)), (('rtl','rtlplus'),('rtl.de','rtlplus.com')),
    (('sat1','prosieben','joyn'),('sat1.de','prosieben.de','joyn.de')),
    (('orf','orf1','orf2'),('orf.at',)), (('rai','rai1','rai2','rai3'),('rai.it','raiplay.it')),
    (('atresplayer','antena3','lasexta'),('atresplayer.com','antena3.com','lasexta.com')),
    (('mediaset','telecinco','cuatro'),('mediaset.es','telecinco.es','mitele.es')),
    (('npo','npo1','npo2','npo3'),('npo.nl','npostart.nl')),
    (('vrt','een'),('vrt.be',)), (('svt','svt1','svt2'),('svt.se','svtplay.se')),
    (('dr','dr1'),('dr.dk',)), (('nrk','nrk1'),('nrk.no',)),
    (('tvp','tvp1','tvp2'),('tvp.pl',)),
    (('kbs','kbs1','kbs2'),('kbs.co.kr',)), (('sbs',),('sbs.co.kr',)),
    (('mbc',),('imbc.com',)), (('tvn',),('tvn.cjenm.com','cjenm.com')),
    (('jtbc',),('jtbc.co.kr',)), (('nhk',),('nhk.jp','nhk.or.jp')),
    (('tbs',),('tbs.co.jp',)), (('tv tokyo','tvtokyo'),('tv-tokyo.co.jp',)),
    (('globoplay','tvglobo','globo'),('globo.com',)), (('telemundo',),('telemundo.com',)),
)


def general_target(detail, upcoming=None):
    last = detail.get('last_episode_to_air') or {}
    return {'series_id':str(detail.get('id') or ''),'names':core.series_names(detail),
        'channel_names':list(dict.fromkeys(str(v).strip() for v in
            (detail.get('original_name'),detail.get('name')) if str(v or '').strip())),
        'issuers':[str(v.get('name') or '') for key in ('networks','production_companies')
            for v in detail.get(key,[]) if isinstance(v,dict)],
        'season':(upcoming or {}).get('season') or core.positive(last.get('season_number')) or 1,
        'first_air_date':str(detail.get('first_air_date') or ''),
        'air_date':(upcoming or {}).get('air_date',''),
        'original_language':detail.get('original_language') or 'en'}


def general_queries(target):
    name = target['names'][0]
    season_term = core.QUERY_TERMS.get(target['original_language'],core.QUERY_TERMS['en'])[0]
    result = ['{} {} official trailer'.format(name,target['first_air_date'][:4])]
    if target['season']>1:
        result.insert(0,'{} {} {} trailer'.format(name,season_term,target['season']))
    else:
        # Use a declared alternate title when the original-language query fails.
        alias = next((v for v in target['names'][1:] if core.words(v)!=core.words(name)),name)
        result.append('{} official trailer'.format(alias))
    return list(dict.fromkeys(q for q in result if len(q)<=120))[:2]


def general_title_allowed(title, target):
    text = core.normal(title)
    if re.search(r'\bs\s*\d+\s*[- ._]*e\s*\d+|(?<!\w)\d{1,2}\s*[x×]\s*\d{1,3}',text):
        return False
    if core.labeled_numbers(text,core.EPISODE_WORDS) or re.search(r'第\s*\d+\s*[話话集]|\d+\s*[회화]',text):
        return False
    if any(v in text for v in ('recap','zusammenfassung','highlights','compilation','explained',
            'woche','semaine','hafta','weekly','behind the scenes','backstage','interview','reaction','fanmade','fan made')):
        return False
    seasons = core.labeled_numbers(text,core.SEASON_WORDS)
    for pair in re.findall(r'第\s*(\d+)\s*[季期]|\bs\s*(\d{1,3})(?!\w)',text):
        seasons.update(int(v) for v in pair if v)
    if seasons and seasons!={target['season']}:
        return False
    years = set(re.findall(r'(?<!\d)(?:19|20)\d{2}(?!\d)',text))
    if years and not years<={target['first_air_date'][:4],target.get('air_date','')[:4]}:
        return False
    return True


def choose_general(videos, target, today=None):
    today = today or datetime.date.today()
    try:
        age_limit = max(366,(today-datetime.date.fromisoformat(target['first_air_date'])).days+397)
    except (ValueError,TypeError):
        age_limit = 366
    found = []
    for index,video in enumerate(videos[:120]):
        title = str(video.get('title') or '')
        text = core.normal(title)
        generic_terms = {core.normal(v[2]) for v in core.QUERY_TERMS.values()}-{'vorschau','avance'}
        generic_terms.update(('trailer','teaser','bande annonce','bande-annonce'))
        if not any(term in text for term in generic_terms):
            continue
        age = core.published_age(video.get('published'))
        if (core.VIDEO_ID.fullmatch(str(video.get('id') or '')) and not video.get('live')
                and core.title_matches(title,target) and core.promo(title) and general_title_allowed(title,target)
                and core.official_channel(video,target) and age is not None and age<=age_limit):
            found.append((age,index,video))
    return min(found,key=lambda v:v[:2])[2] if found else None


def official_tmdb(videos, target):
    values = [v for v in videos if isinstance(v,dict) and v.get('official') is True
        and v.get('site')=='YouTube' and v.get('type') in ('Trailer','Teaser')
        and core.VIDEO_ID.fullmatch(str(v.get('key') or ''))
        and general_title_allowed(v.get('name') or '',target)]
    return max(values,key=lambda v:str(v.get('published_at') or '')) if values else None


def publisher_domains(target):
    issuers = {''.join(core.words(v)) for v in target['issuers']}
    return tuple(dict.fromkeys(domain for brands,domains in PUBLISHERS
        if issuers.intersection(''.join(core.words(v)) for v in brands) for domain in domains))


def safe_page(url, domains):
    try:
        part = urlsplit(url)
        host = (part.hostname or '').lower()
        return bool(len(url)<=2048 and part.scheme=='https' and not part.username and not part.password
            and part.port in (None,443) and any(host==d or host.endswith('.'+d) for d in domains))
    except ValueError:
        return False


def publisher_pages(detail, target):
    domains = publisher_domains(target)
    result = []
    if 'apple.com' in domains:
        slug = '-'.join(core.words(target['names'][0]))
        if re.fullmatch(r'[a-z0-9-]{3,100}',slug):
            result.append('https://www.apple.com/tv-pr/originals/'+slug+'/')
    homepage = str(detail.get('homepage') or '')
    if homepage.startswith('http://'):
        homepage = 'https://'+homepage[7:]
    if safe_page(homepage,domains):
        result.append(homepage)
    return list(dict.fromkeys(result))[:2],domains


def youtube_id(url):
    try:
        part = urlsplit(url)
        host = (part.hostname or '').lower()
        if host in ('youtube.com','www.youtube.com','youtube-nocookie.com','www.youtube-nocookie.com'):
            ident = part.path.split('/embed/',1)[1].split('/')[0] if '/embed/' in part.path else parse_qs(part.query).get('v',[''])[0]
        elif host=='youtu.be':
            ident = part.path.strip('/')
        else:
            return ''
        return ident if core.VIDEO_ID.fullmatch(ident) else ''
    except (ValueError,IndexError):
        return ''


class PublisherHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_depth=0;self.titles=[];self.text=[];self.embeds=[];self.links=[];self.script=False

    def handle_starttag(self,tag,attrs):
        attrs=dict(attrs)
        if tag in ('title','h1'):
            self.title_depth+=1
        if tag=='script':self.script=True
        if tag=='meta' and attrs.get('property')=='og:title':self.titles.append(attrs.get('content',''))
        if tag=='iframe':
            ident=youtube_id(attrs.get('src') or attrs.get('data-src') or '')
            if ident:self.embeds.append((ident,' '.join(self.text[-30:])))
        if tag=='a' and attrs.get('href'):
            self.links.append(attrs['href'])

    def handle_endtag(self,tag):
        if tag in ('title','h1'):self.title_depth=max(0,self.title_depth-1)
        if tag=='script':self.script=False

    def handle_data(self,data):
        if self.title_depth:self.titles.append(data)
        if not self.script and data.strip():self.text.append(data.strip())


def publisher_embeds(body, target):
    page=PublisherHTML();page.feed(body)
    if not core.title_matches(' '.join(page.titles),target):
        return [],page
    result=[]
    for ident,context in page.embeds:
        if core.promo(context) and general_title_allowed(context[-500:],target) and ident not in result:
            result.append(ident)
    return result,page


class PublisherRedirect(HTTPRedirectHandler):
    def __init__(self,domains):self.domains=domains
    def redirect_request(self,req,fp,code,msg,headers,newurl):
        if not safe_page(newurl,self.domains):raise ValueError('Untrusted publisher redirect')
        return super().redirect_request(req,fp,code,msg,headers,newurl)


def read_page(url,domains,cancel,deadline):
    core.check(cancel,deadline)
    if not safe_page(url,domains):raise ValueError('Untrusted publisher URL')
    timeout=min(4,max(.1,deadline-time.monotonic()))
    request=Request(url,headers={'User-Agent':'GT-IPTV-Player-Pro/1.7.0-r32','Accept':'text/html'})
    with build_opener(PublisherRedirect(domains)).open(request,timeout=timeout) as response:
        if not safe_page(response.geturl(),domains):raise ValueError('Untrusted publisher response')
        if 'html' not in response.headers.get('Content-Type','').lower():return ''
        chunks=[];size=0
        while size<1048576:
            core.check(cancel,deadline)
            chunk=response.read(min(32768,1048576-size))
            if not chunk:break
            chunks.append(chunk);size+=len(chunk)
        if size>=1048576:return ''
    core.check(cancel,deadline)
    return b''.join(chunks).decode('utf-8',errors='replace')


def publisher_trailer(detail,target,cancel,deadline,reader=None):
    reader=reader or read_page
    pages,domains=publisher_pages(detail,target)
    for url in pages:
        core.check(cancel,deadline)
        if deadline-time.monotonic()<4:break
        try:
            body=reader(url,domains,cancel,deadline)
            core.check(cancel,deadline)
            identifiers,page=publisher_embeds(body,target)
            if identifiers:
                return {'state':'found','kind':'general','id':identifiers[0],
                    'title':target['names'][0],'target':target,'source':'publisher','publisher_url':url}
            # One same-publisher trailer page may be linked from the show page.
            link=next((urljoin(url,v) for v in page.links if core.promo(v)
                and safe_page(urljoin(url,v),domains)),None)
            if link and deadline-time.monotonic()>=4:
                identifiers,_=publisher_embeds(reader(link,domains,cancel,deadline),target)
                core.check(cancel,deadline)
                if identifiers:
                    return {'state':'found','kind':'general','id':identifiers[0],
                        'title':target['names'][0],'target':target,'source':'publisher','publisher_url':link}
        except core.TrailerCancelled:
            raise
        except Exception:
            core.check(cancel,deadline)
    return None

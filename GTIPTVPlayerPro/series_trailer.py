# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded, on-demand next-episode trailer discovery. No GUI or provider I/O."""
import datetime
import re
import threading
import time
import unicodedata
from collections import OrderedDict

from .metadata import TMDbMetadataClient, MetadataError, clean_media_title

VIDEO_ID = re.compile(r"^[A-Za-z0-9_-]{11}$")
CHANNEL_ID = re.compile(r"^UC[A-Za-z0-9_-]{22}$")
MAX_SECONDS = 40
MAX_CACHE = 32
SUCCESS_SECONDS = 15 * 60
EMPTY_SECONDS = 60
_CACHE = OrderedDict()
_LOCK = threading.RLock()
# TMDb silently returned no videos for the former 200-code parameter. Query
# small batches covering every UI language family, independently of device locale.
VIDEO_LANGUAGES = ('en ar bg ca cs da de el es et fa fi fr fy gl he hr hu id is it ja ko ku lt lv mk '
    'nb nl nn pl pt ro ru sk sl sq sr sv ta th tr uk vi zh cn xx null').split()


def video_parameters(language='en', codes=None):
    codes = codes or [str(language or 'en').split('-')[0], 'en', 'xx', 'null']
    return {'include_video_language':','.join(dict.fromkeys(codes))}


def normal(value):
    text = unicodedata.normalize("NFKD", str(value or "").casefold().replace("ı", "i"))
    return " ".join("".join(c for c in text if not unicodedata.combining(c)).replace("&", " ").split())


def words(value):
    return re.findall(r"[^\W_]+", normal(value), re.UNICODE)


def clean_trailer_title(value):
    # Portal audio/country tags can follow the name, including before a year.
    # Strip only delimited known codes; words such as IT remain real titles.
    codes = ('ar bg ca cs da de el en es et fa fi fr fy gl he hr hu id is it ja ko ku lt lv mk '
        'nb nl nn no pl pt ru sk sl sq sr sv ta th tr uk vi zh zh_HK '
        'eng ger deu fra fre dut nld tur ara spa por ita rus pol ukr jp cn ro be au us gb').split()
    tag = '(?:'+'|'.join(re.escape(v) for v in codes)+')(?:[_-][a-z]{2})?'
    decorated = re.sub(r'\|\s*'+tag+r'\s*\||\[\s*'+tag+r'\s*\]|\(\s*'+tag+r'\s*\)',
        ' ',str(value or ''),flags=re.I)
    return clean_media_title(decorated)


def positive(value):
    if isinstance(value, bool):
        return 0
    try:
        n = int(str(value))
        return n if 0 < n < 10000 else 0
    except (ValueError, TypeError, OverflowError):
        return 0


class TrailerCancelled(Exception):
    pass


def check(cancel, deadline):
    if cancel is not None and cancel.is_set():
        raise TrailerCancelled()
    if time.monotonic() >= deadline:
        raise MetadataError("Trailer lookup timed out")


def next_target(detail, today=None):
    """Use scheduled broadcast metadata, never the provider's last stored item."""
    today = today or datetime.date.today()
    if detail.get("status") in ("Ended", "Canceled"):
        return None
    last = detail.get("last_episode_to_air") or {}
    target = detail.get("next_episode_to_air")
    # Returning Series does not announce an episode. In particular, never
    # manufacture a finale+1 or a new-season episode from a missing schedule.
    if not isinstance(target, dict) or not target:
        return None
    season, episode = positive(target.get("season_number")), positive(target.get("episode_number"))
    if not season or not episode:
        return None
    if (positive(last.get("season_number")), positive(last.get("episode_number"))) >= (season, episode):
        return None
    air_date = str(target.get("air_date") or "")
    if air_date:
        try:
            if datetime.date.fromisoformat(air_date) < today:
                return None
        except ValueError:
            return None
    seasons = {positive(s.get("season_number")): positive(s.get("episode_count"))
        for s in detail.get("seasons", []) if isinstance(s, dict)}
    absolute = episode
    if season > 1:
        if any(not seasons.get(n) for n in range(1, season)):
            absolute = 0
        else:
            absolute += sum(seasons[n] for n in range(1, season))
    channel_names = list(dict.fromkeys(str(v).strip() for v in
        (detail.get("original_name"), detail.get("name")) if str(v or "").strip()))
    aliases = series_names(detail)
    issuers = [str(v.get("name") or "") for key in ("networks", "production_companies")
        for v in detail.get(key, []) if isinstance(v, dict)]
    return {"series_id": str(detail.get("id") or ""), "season": season, "episode": episode,
        "absolute": absolute, "scheduled": True, "air_date": air_date,
        "absolute_unambiguous": bool(absolute and absolute > max(seasons.values(), default=0)),
        "episode_names": [str(target.get("name") or "")],
        "names": aliases, "channel_names": channel_names, "issuers": issuers,
        "original_language": detail.get("original_language") or "en",
        "turkish": "TR" in (detail.get("origin_country") or [])}


def series_names(detail):
    values = [detail.get('original_name'), detail.get('name')]
    extra = detail.get('alternative_titles') or {}
    values.extend(v.get('title') for v in (extra.get('results') or [])[:40] if isinstance(v,dict))
    translations = detail.get('translations') or {}
    values.extend((v.get('data') or {}).get('name') for v in
        (translations.get('translations') or [])[:80] if isinstance(v,dict))
    result, seen = [], set()
    for value in values:
        text = ' '.join(str(value or '').split())[:160]
        key = ' '.join(words(text))
        if len(key)>=3 and key not in seen:
            result.append(text);seen.add(key)
    return result[:64]


def name_forms(name):
    tokens = words(name)
    result = [tokens]
    articles = {'the','a','an','der','die','das','le','la','les','el','los','las','il','lo','gli','de','het','o','os','as'}
    if len(tokens)>1 and tokens[0] in articles:
        rest = tokens[1:]
        if len(rest)>1 or len(rest[0])>=8:
            result.append(rest)
    return result


def title_matches(title, target):
    title_words = words(title)
    joined = " " + " ".join(title_words) + " "
    for name in target["names"]:
        for name_words in name_forms(name):
            if name_words and " " + " ".join(name_words) + " " in joined:
                return True
    return False


# Local broadcast terms; these are search data, not device-language UI text.
QUERY_TERMS = {
    'en':('season','episode','trailer'),'de':('Staffel','Folge','Vorschau'),
    'fr':('saison','épisode','bande-annonce'),'es':('temporada','episodio','avance'),
    'it':('stagione','episodio','trailer'),'pt':('temporada','episódio','trailer'),
    'nl':('seizoen','aflevering','trailer'),'pl':('sezon','odcinek','zwiastun'),
    'ru':('сезон','серия','трейлер'),'uk':('сезон','серія','трейлер'),
    'tr':('sezon','bölüm','fragman'),'ar':('الموسم','الحلقة','إعلان'),
    'fa':('فصل','قسمت','تیزر'),'he':('עונה','פרק','טריילר'),
    'ja':('シーズン','話','予告'),'zh':('季','集','预告'),'cn':('季','集','預告'),
    'ko':('시즌','회','예고'),'th':('ซีซัน','ตอน','ตัวอย่าง'),'vi':('mùa','tập','trailer'),
    'id':('musim','episode','trailer'),'hu':('évad','rész','előzetes'),
    'cs':('řada','díl','upoutávka'),'sk':('séria','epizóda','upútavka'),
    'sl':('sezona','del','napovednik'),'hr':('sezona','epizoda','najava'),
    'sr':('сезона','епизода','трејлер'),'mk':('сезона','епизода','трејлер'),
    'bg':('сезон','епизод','трейлър'),'el':('σεζόν','επεισόδιο','τρέιλερ'),
    'sv':('säsong','avsnitt','trailer'),'da':('sæson','afsnit','trailer'),
    'nb':('sesong','episode','trailer'),'nn':('sesong','episode','trailer'),
    'no':('sesong','episode','trailer'),'fi':('kausi','jakso','traileri'),
    'is':('sería','þáttur','stikla'),'et':('hooaeg','osa','treiler'),
    'lv':('sezona','sērija','reklāmkadri'),'lt':('sezonas','serija','anonsas'),
    'ca':('temporada','episodi','tràiler'),'gl':('tempada','episodio','avance'),
    'fy':('seizoen','ôflevering','trailer'),'ku':('demsal','beş','fragman'),
    'sq':('sezoni','episodi','trailer'),'ta':('பருவம்','அத்தியாயம்','முன்னோட்டம்'),
}
SEASON_WORDS = {normal(v[0]) for v in QUERY_TERMS.values()} | {'sezonul','season','сезон','κυκλος'}
EPISODE_WORDS = {normal(v[1]) for v in QUERY_TERMS.values()} | {'ep','episode','episodul','capitulo','эпизод','епізод','화','話','话'}


def labeled_numbers(text, labels):
    suffix = {normal(v) for v in ('bölüm','evad','rész','話','话','集','회','화')}
    result = set()
    for label in labels:
        pattern = re.escape(label)
        after = re.findall(r'(?<!\w)'+pattern+r'\s*[.:#-]?\s*(\d{1,4})(?!\d)',text)
        before = re.findall(r'(?<!\d)(\d{1,4})\s*[.:#-]?\s*'+pattern+r'(?!\w)',text)
        values = before if label in suffix and before else (after or before)
        result.update(int(v) for v in values)
    return result


def specific_episode_name(value):
    text = ' '.join(str(value or '').split())
    tokens = words(text)
    meaningful = [v for v in tokens if not v.isdecimal() and v not in EPISODE_WORDS|SEASON_WORDS|{'part','number','no'}]
    return text if len(meaningful)>=2 and sum(map(len,meaningful))>=8 else ''


def episode_name_matches(title, target):
    for name in target.get('episode_names',[]):
        if specific_episode_name(name) and title_matches(title,{'names':[name]}):
            return True
    return False


def episode_matches(title, target):
    """Validate local season/episode notation without relaxing identity checks."""
    text = normal(title)
    if re.search(r'\bs\s*\d+\s*e\s*\d+\s*[-–—]\s*(?:e\s*)?\d+',text):
        return False
    coded = re.findall(r"\bs\s*(\d{1,3})\s*[- ._]*e\s*(\d{1,4})(?!\d)", text)
    coded += re.findall(r'(?<!\w)(\d{1,2})\s*[x×]\s*(\d{1,3})(?!\d)',text)
    if coded:
        return all((int(s), int(e)) == (target["season"], target["episode"]) for s, e in coded)
    # A range is a weekly/multi-episode preview, not a proven single episode.
    ep = '(?:'+'|'.join(re.escape(v) for v in sorted(EPISODE_WORDS,key=len,reverse=True))+')'
    if re.search(ep+r'\s*\d+\s*[-–—]\s*\d+|\d+\s*[-–—]\s*\d+\s*'+ep,text):
        return False
    sp = '(?:'+'|'.join(re.escape(v) for v in sorted(SEASON_WORDS,key=len,reverse=True))+')'
    gap = r'\s*[.:#|/·•,–—-]?\s*'
    pairs = re.findall(r'(?<!\w)'+sp+gap+r'(\d{1,3})'+gap+ep+gap+r'(\d{1,4})(?!\d)',text)
    pairs += re.findall(r'(?<!\d)(\d{1,3})'+gap+sp+gap+r'(\d{1,4})'+gap+ep+r'(?!\w)',text)
    if pairs:
        return all((int(s),int(e))==(target['season'],target['episode']) for s,e in pairs)
    seasons = labeled_numbers(text,SEASON_WORDS)
    episodes = labeled_numbers(text,EPISODE_WORDS)
    seasons.update(int(v) for v in re.findall(r'第\s*(\d{1,3})\s*[季期]',text))
    episodes.update(int(v) for v in re.findall(r'第\s*(\d{1,4})\s*[話话集]',text))
    episodes.update(int(v) for v in re.findall(r'(?<!\d)(\d{1,4})\s*[회화]',text))
    if seasons and seasons!={target['season']}:
        return False
    if seasons and episodes:
        return episodes=={target['episode']}
    if episodes:
        if episodes=={target['episode']} and (target['season']==1 or episode_name_matches(title,target)):
            return True
        if (target.get('turkish') or target.get('absolute_unambiguous')) and target.get('absolute'):
            return episodes=={target['absolute']}
        return False
    return episode_name_matches(title,target)


def promo(title):
    text = normal(title)
    terms = tuple(normal(v[2]) for v in QUERY_TERMS.values()) + (
             "fragman", "tanitim", "trailer", "teaser", "promo", "preview", "bande annonce",
             "bande-annonce", "vorschau", "avance", "anticipazioni", "予告", "预告", "預告", "예고")
    bad = ("reaction", "reaksiyon", "fanmade", "fan made", "concept trailer", "tahmin", "analiz",
           "full episode", "tek parca", "tek parça", "izle full", "review", "inceleme")
    return any(term in text for term in terms) and not any(term in text for term in bad)


PUBLISHER_FAMILIES = {
    'daserste':('ard','ardmediathek'), 'bbcone':('bbc',), 'bbctwo':('bbc',),
    'bbcthree':('bbc',), 'bbcamerica':('bbcamerica',), 'rtlplus':('rtl','rtlplus'),
    'appletv':('apple','appletv'),'disneyplus':('disneyplus','disney'),
    'amazonprimevideo':('primevideo','amazonprimevideo'),'primevideo':('primevideo','amazonprimevideo'),
    'kbs1':('kbs',),'kbs2':('kbs',),'hbomax':('hbo','max'),'max':('max','hbo'),
    'france2':('francetelevisions','francetv'),'france3':('francetelevisions','francetv'),
}
OFFICIAL_CHANNEL_IDS = {'appletv':{'UC1Myj674wRVXB9I4c6Hm5zA'},'zdf':{'UC5r45__P2B0cHUQy3kV9NwQ'}}
CHANNEL_DESCRIPTORS = ('official','official channel','officiel','oficial','offiziell','resmi','dizi',
    'serien','series','drama','dramas','tv','television','network','entertainment',
    'filme und serien','serien und filme','filme serien','films and series','series and films',
    'series films','series y peliculas','series e filmes','series et films','reality',
    'deutschland','germany','france','espana','espanol','italia','brasil','latinoamerica',
    'uk and ireland','uk ireland','india','japan','asia','korea','australia','canada',
    'films','filme','fiction','serien und filme','drama kbs','deutsch','german','deutschland')


def official_channel(video, target):
    ident = str(video.get('channel_id') or '')
    if not CHANNEL_ID.fullmatch(ident):
        return False
    issuers = {''.join(words(v)) for v in target['issuers']}
    if any(ident in OFFICIAL_CHANNEL_IDS.get(issuer,set()) for issuer in issuers):
        return True
    if not video.get('channel_verified'):
        return False
    name = "".join(words(video.get("channel")))
    if not name:
        return False
    descriptors = {''.join(words(v)) for v in CHANNEL_DESCRIPTORS}
    for issuer in target.get('channel_names',target['names']) + target["issuers"]:
        expected = "".join(words(issuer))
        if len(expected) < 3:
            continue
        for brand in (expected,)+PUBLISHER_FAMILIES.get(expected,()):
            if name==brand or any(name in (brand+d,d+brand) for d in descriptors):
                return True
    return False


def published_age(text):
    """Public search labels use English, independent of the device locale."""
    text = normal(text).replace("streamed ", "").replace("premiered ", "")
    match = re.search(r"\b(\d+)\s*(second|sec\b|s\b|minute|min\b|m\b|hour|hr\b|h\b|day|d\b|week|wk\b|w\b|month|mo\b|year|yr\b|y\b)", text)
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    factor = {"second":1/86400,"sec":1/86400,"s":1/86400,"minute":1/1440,"min":1/1440,"m":1/1440,
              "hour":1/24,"hr":1/24,"h":1/24,"day":1,"d":1,"week":7,"wk":7,"w":7,
              "month":31,"mo":31,"year":366,"yr":366,"y":366}.get(unit,366)
    return amount * factor


def choose_youtube(videos, target):
    candidates = []
    for index, video in enumerate(videos[:120]):
        title = str(video.get("title") or "")
        age = published_age(video.get("published"))
        if (VIDEO_ID.fullmatch(str(video.get("id") or "")) and not video.get("live")
                and title_matches(title, target) and episode_matches(title, target)
                and promo(title) and official_channel(video, target)
                and age is not None and age <= 45):
            candidates.append((age, index, video))
    return min(candidates, key=lambda value: (value[0], value[1]))[2] if candidates else None


def queries(target):
    name = target["names"][0]
    result = []
    if target["turkish"] and target["absolute"]:
        result.append('{} {}. Bölüm Fragmanı'.format(name, target["absolute"]))
    language = str(target.get('original_language') or 'en').split('-')[0]
    season_term,episode_term,promo_term = QUERY_TERMS.get(language,QUERY_TERMS['en'])
    if language!='en' and not target.get('turkish'):
        result.append('{} {} {} {} {} {}'.format(name,season_term,target['season'],episode_term,target['episode'],promo_term))
    result.append('{} S{:02d}E{:02d} trailer'.format(name,target['season'],target['episode']))
    specific = next((v for v in target.get('episode_names',[]) if specific_episode_name(v)),None)
    if specific:
        result.append('{} {} {}'.format(name,specific,promo_term))
    elif target.get('absolute_unambiguous') and not target.get('turkish'):
        result.append('{} {} {} {}'.format(name,episode_term,target['absolute'],promo_term))
    elif not target.get('turkish'):
        result.append('{} season {} episode {} promo'.format(name,target['season'],target['episode']))
    return list(dict.fromkeys(query for query in result if len(query)<=120))[:3]


class SeriesTrailerService:
    def __init__(self, metadata, search=None, clock=None, today=None):
        self.metadata = metadata
        self.search = search
        self.clock = clock or time.monotonic
        self.today = today

    def _identity_matches(self, title, year, detail):
        identity = self.metadata._poster_identity(detail, 'series')
        if self.metadata._provider_identity_matches('series', title, year, identity):
            return True
        query = ' '.join(words(clean_trailer_title(title)))
        for alias in series_names(detail):
            if query and query == ' '.join(words(clean_trailer_title(alias))):
                return self.metadata._provider_identity_matches('series', title, year,
                    dict(identity, title=alias, original_title=alias))
        return False

    def _identity(self, item, info):
        title = clean_trailer_title(getattr(info, "title", "") or getattr(item, "name", ""))
        year = getattr(info, "year", "") or getattr(item, "year", "")
        ident = getattr(info, "tmdb_id", "") or getattr(item, "tmdb_id", "")
        seen = set()
        def fetch(value):
            seen.add(str(value))
            try:
                detail = self.metadata._request_json('/tv/' + str(value),
                    dict(video_parameters(),append_to_response='alternative_titles,translations,videos'), language='en-US')
            except MetadataError as error:
                if error.http_status != 404:
                    raise
                return None
            return detail if self._identity_matches(title, year, detail) else None
        if self.metadata._valid_tmdb_id(ident):
            result = fetch(ident)
            if result:
                return result
        # Search may return an English name for a translated provider title.
        # Validate the bounded candidates against their declared native aliases.
        payload = self.metadata._request_json('/search/tv',
            {'query':clean_media_title(title),'page':1,'include_adult':'false'}, language='en-US')
        for selected in (payload.get('results') or [])[:3]:
            value = selected.get('id') if isinstance(selected,dict) else None
            if self.metadata._valid_tmdb_id(value) and str(value) not in seen:
                result = fetch(value)
                if result:
                    return result
        return {}

    def find(self, item, info=None, cancel=None):
        if not self.metadata.available:
            return {"state": "error"}
        deadline = self.clock() + MAX_SECONDS
        try:
            with self.metadata.language_scope('en-US'), self.metadata.request_scope(cancel, deadline):
                check(cancel, deadline)
                detail = self._identity(item, info)
                check(cancel, deadline)
                if not detail or not series_names(detail):
                    return {'state':'not_found'}
                from .series_trailer_sources import general_target
                target = next_target(detail, self.today() if self.today else None)
                general = general_target(detail,target)
                key = (general['series_id'],(target or {}).get('season',0),
                    (target or {}).get('episode',0),(target or {}).get('air_date',''),general['season'])
                with _LOCK:
                    cached = _CACHE.get(key)
                    if cached and self.clock() < cached[0]:
                        _CACHE.move_to_end(key)
                        return dict(cached[1])
                result = {'state':'not_found'}
                episode_deadline = deadline-18
                if target and self.clock()<episode_deadline:
                    try:
                        with self.metadata.request_scope(cancel,episode_deadline):
                            result = self._find_target(target,cancel,episode_deadline)
                    except MetadataError:
                        check(cancel,deadline)
                if result['state']!='found':
                    check(cancel,deadline)
                    result = self._find_general(detail,general,cancel,deadline)
                check(cancel, deadline)
                if result['state'] in ('found', 'not_found'):
                    with _LOCK:
                        ttl = SUCCESS_SECONDS if result['state']=='found' and result.get('kind')!='general' else EMPTY_SECONDS
                        _CACHE[key] = (self.clock()+ttl,dict(result))
                        _CACHE.move_to_end(key)
                        while len(_CACHE)>MAX_CACHE:
                            _CACHE.popitem(last=False)
                return result
        except TrailerCancelled:
            return {"state": "cancelled"}
        except Exception:
            if cancel is not None and cancel.is_set():
                return {"state": "cancelled"}
            return {"state": "error"}

    def _find_target(self, target, cancel, deadline):
        path = '/tv/{}/season/{}/episode/{}'.format(target['series_id'], target['season'], target['episode'])
        # Native episode text helps identify a promo without a numeric title.
        # include_video_language keeps appended videos independent of UI locale.
        try:
            payload = self.metadata._request_json(path, dict(video_parameters(target['original_language']),
                append_to_response='videos'), language=target['original_language'])
        except MetadataError as error:
            if error.http_status != 404:
                raise
            payload = {}
        if payload and (positive(payload.get('season_number')),positive(payload.get('episode_number'))) != (target['season'],target['episode']):
            payload = {}
        if payload:
            name = str(payload.get('name') or '')
            if name and name not in target['episode_names']:
                target['episode_names'].insert(0,name)
        direct = [v for v in (payload.get('videos') or {}).get('results', []) if isinstance(v, dict) and v.get('official') is True
            and v.get('site') == 'YouTube' and v.get('type') in ('Trailer','Teaser')
            and VIDEO_ID.fullmatch(str(v.get('key') or ''))]
        if direct:
            video = max(direct, key=lambda v: str(v.get('published_at') or ''))
            return {'state':'found','id':video['key'],'title':video.get('name') or target['names'][0],
                    'target':target,'source':'tmdb','kind':'episode'}
        if self.search is None:
            from .web_youtube import trailer_search
            search = trailer_search
        else:
            search = self.search
        for query in queries(target):
            check(cancel, deadline)
            if deadline-self.clock() < 10:
                break
            videos = search(query, cancel)
            check(cancel, deadline)
            video = choose_youtube(videos, target)
            if video:
                return {'state':'found','id':video['id'],'title':video['title'], 'target':target,
                        'source':'youtube','channel':video.get('channel',''),'kind':'episode'}
        return {'state':'not_found','target':target}


    def _find_general(self, detail, target, cancel, deadline):
        from .series_trailer_sources import official_tmdb, general_queries, choose_general, publisher_trailer
        check(cancel,deadline)
        def result(video,source):
            return {'state':'found','kind':'general','id':video['key'],
                'title':video.get('name') or target['names'][0],'target':target,'source':source}
        video = official_tmdb((detail.get('videos') or {}).get('results') or [],target)
        if video:
            return result(video,'tmdb_series')
        paths = ['/tv/{}/videos'.format(target['series_id'])]
        if target['season']>1 and deadline-self.clock()>14:
            path = '/tv/{}/season/{}/videos'.format(target['series_id'],target['season'])
            paths.insert(0,path)
            try:
                payload = self.metadata._request_json(path,video_parameters(target['original_language']),language='en-US')
                video = official_tmdb(payload.get('results') or [],target)
                if video:return result(video,'tmdb_season')
            except MetadataError:
                check(cancel,deadline)
        if deadline-self.clock()>13:
            try:
                found = publisher_trailer(detail,target,cancel,min(deadline-10,self.clock()+5))
                if found:return found
            except MetadataError:
                check(cancel,deadline)
        if self.search is None:
            from .web_youtube import trailer_search
            search = trailer_search
        else:
            search = self.search
        for query in general_queries(target):
            check(cancel,deadline)
            if deadline-self.clock()<10:break
            video = choose_general(search(query,cancel),target,self.today() if self.today else None)
            check(cancel,deadline)
            if video:
                return {'state':'found','kind':'general','id':video['id'],'title':video['title'],
                    'target':target,'source':'youtube_general','channel':video.get('channel','')}
        # Retry official metadata in short language batches when no preferred
        # or public-page source matched. No device-language filter is consulted.
        for path in paths:
            for start in range(0,len(VIDEO_LANGUAGES),3):
                check(cancel,deadline)
                if deadline-self.clock()<2:return {'state':'not_found','target':target}
                codes = list(dict.fromkeys(['en','null']+VIDEO_LANGUAGES[start:start+3]))
                try:
                    payload = self.metadata._request_json(path,video_parameters(codes=codes),language='en-US')
                    video = official_tmdb(payload.get('results') or [],target)
                    if video:return result(video,'tmdb_season' if '/season/' in path else 'tmdb_series')
                except MetadataError:
                    check(cancel,deadline)
        return {'state':'not_found','target':target}


def service_for(metadata):
    """An explicit PLAY request has its own small TMDb request budget."""
    client = TMDbMetadataClient(api_key=getattr(metadata,'api_key',''), enabled=True,
        language='en-US', timeout=5, cache_path=getattr(metadata,'cache_path',''))
    return SeriesTrailerService(client)

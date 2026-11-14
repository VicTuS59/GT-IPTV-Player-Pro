# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Conservative match identity checks for public YouTube metadata, no playback."""
import datetime as dt
import json
import re
from urllib.parse import unquote,urlsplit

from .match_highlights import available, flashscore_match_zone, fold, league_key, local_team_name, match_date, search_team_name
from .match_highlights_languages import has_local_highlight_keyword

VIDEO_ID = re.compile(r'^[A-Za-z0-9_-]{11}$')
CHANNEL_ID = re.compile(r'^UC[A-Za-z0-9_-]{22}$')
SCORE_PAIR = re.compile(r'(?<![\d./:–-])(\d{1,3})\s*(?:[-–:x×]|a)\s*(\d{1,3})(?![\d./:–-])', re.I)
HIGHLIGHTS = re.compile(r'\b(?:highlights?|o?zet(?:i|ler[i]?)?|zusammenfassung|spielzusammenfassung|resumen|resum|resume|resumo|melhores momentos|gols d[ao] (?:jogo|partida)|goles del partido|sintesi|samenvatting|sammandrag|kooste|obzor|обзор|огляд|ملخص|خلاصه)\b|集锦|精華', re.I)
GAMES = re.compile(r'\b(?:e\s*football|eafc|ea\s*(?:sports\s*)?fc\s*\d{2}|fifa\s*\d{2,4}|pes\s*\d{2,4}|pro\s*evolution\s*soccer|football\s*manager|gameplay|gaming|simulation|simulasyon|simulacao|simulacion|simulazione|simulated|simulator|esports?|e\s*sports|ps[345]|playstation|xbox|video\s*game)\b', re.I)
OTHER = re.compile(r'\b(?:reaction|reacts?|prediction|preview|watchalong|press\s*conference|interview|tahmin|roportaj|basin\s*toplantisi|mac\s*sonu\s*yorum|macinin\s*ardindan|nach\s*dem\s*spiel|pressekonferenz)\b', re.I)
SEASON_YEAR = re.compile(r'\b(?:season|temporada|stagione|saison|sezon(?:u)?) (20\d{2})\b')
YOUTH = re.compile(r'\b(?:u\s*(?:17|18|19|20|21|23)|reserves?|legends)\b')
WOMEN = re.compile(r'\b(?:women|womens|ladies|femenin[oa]|feminin(?:e)?|femminile|frauen|kadinlar|vrouwen)\b|\bserieawomen\w*')
WOMEN_SUFFIX = re.compile(r'\s+(?:w|women|womens|ladies|femenin[oa]|feminin(?:e)?|femminile|frauen|kadinlar|vrouwen)$')
WOMEN_ABBREVIATION = re.compile(r'\bw\b(?=\s+\d|\s*\.|\s*[-–×|])', re.I)
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


def aliases(name):
    value=fold(name);result={value,*ALIASES.get(value,())}
    tokens=value.split()
    if len(tokens)>1:
        compact=' '.join(t for t in tokens if t not in ('fc','afc','cf','sc','fk'))
        if len(compact)>=4:result.add(compact)
    return {v for v in result if v and v not in ('united','city','real','sporting','athletic')}


def _women_match(match):
    names = (fold(match.home), fold(match.away), fold(match.league))
    return any(WOMEN.search(name) or WOMEN_SUFFIX.search(name) for name in names)


def _team_aliases(name, match, women):
    local = local_team_name(name, match.league)
    result = aliases(name) | aliases(search_team_name(name, match.league)) | aliases(local)
    if women:
        short = WOMEN_SUFFIX.sub('', fold(name))
        result.update(aliases(short) | aliases(search_team_name(short, match.league)))
    return result


def team_positions(title,match,local_names=()):
    women = _women_match(match)
    home, away = (_team_aliases(name, match, women) for name in (match.home, match.away))
    # Only the extra search supplies team aliases derived from the fixture.
    if local_names:
        for names, local in zip((home, away), local_names):
            name = fold(local)
            names.add(name)
            if women:
                names.add(WOMEN_SUFFIX.sub('', name))
    return alias_positions(title, home, away)


def alias_positions(title, home, away):
    if not home or not away:
        return None
    # A short alias can occur inside the other team's full name.
    names = '|'.join(map(re.escape, sorted(home | away, key=lambda value: (-len(value), value))))
    home_names, away_names = home - away, away - home
    home_at = away_at = None
    for item in re.finditer(r'(?<!\w)(?:' + names + r')(?!\w)', fold(title)):
        name = item.group()
        if home_at is None and name in home_names:
            home_at = item.start()
        if away_at is None and name in away_names:
            away_at = item.start()
        if home_at is not None and away_at is not None:
            return home_at, away_at
    return None


def candidate_ok(entry,match,*,linked_video=False,local_names=()):
    if not available(match) or not VIDEO_ID.fullmatch(str(entry.get('id',''))):return False
    title=str(entry.get('title') or '')[:512];normal=fold(title)
    if entry.get('live') or GAMES.search(normal) or OTHER.search(normal):return False
    teams = fold(match.home+' '+match.away)
    if YOUTH.search(normal) and not YOUTH.search(teams):return False
    if local_names:
        # International names can be a substring of another country or a different squad.
        ages = set(re.findall(r'\bu\s*(\d{2})\b', teams))
        if ages and set(re.findall(r'\bu\s*(\d{2})\b', normal)) != ages:
            return False
    women = _women_match(match)
    marks_women = WOMEN.search(normal) or WOMEN_ABBREVIATION.search(title)
    if marks_women and not women:return False
    if match.league.startswith('flashscore:') and women and not (marks_women or linked_video):return False
    return team_positions(title,match,local_names) is not None


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
        'duration_seconds':str(details.get('lengthSeconds') or '')[:6],
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
    match_zone = flashscore_match_zone(match.league)
    if match_zone and league_key(match.league) == 'bra.2':
        return {match_date(match)}
    days={dt.datetime.fromtimestamp(match.kickoff).date(),dt.datetime.fromtimestamp(match.kickoff,dt.timezone.utc).date()}
    if match_zone:
        days.add(match_date(match))
        return days
    try:
        from .timezone_compat import ZoneInfo
        zone=LEAGUE_ZONES.get(league_key(match.league),'Europe/Berlin')
        days.add(dt.datetime.fromtimestamp(match.kickoff,ZoneInfo(zone)).date())
    except (ImportError,OSError,ValueError,KeyError):pass
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
    for year,month,day in re.findall(r'(?<!\d)(20\d{2})(?:[-/.]|[年년]\s*)(\d{1,2})(?:[-/.]|[月월]\s*)(\d{1,2})(?!\d)',value):
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
        'december':12,'dec':12,'aralik':12,'dezember':12,'dez':12,
        'janeiro':1,'enero':1,'fevereiro':2,'febrero':2,'marco':3,'marzo':3,
        'abril':4,'maio':5,'mayo':5,'junho':6,'junio':6,'julho':7,'julio':7,
        'agosto':8,'setembro':9,'septiembre':9,'outubro':10,'octubre':10,
        'novembro':11,'noviembre':11,'dezembro':12,'diciembre':12,
        'janvier':1,'fevrier':2,'mars':3,'avril':4,'juin':6,'juillet':7,'aout':8,
        'septembre':9,'octobre':10,'novembre':11,'decembre':12}
    normal=fold(value)
    for day,month,year in re.findall(r'\b(\d{1,2}) (?:de )?(\w+) (?:de )?(20\d{2})\b',normal):
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


def _title_scores(title):
    return {tuple(map(int, pair)) for pair in SCORE_PAIR.findall(title)}


def _compatible_title_score(title, match, local_names=()):
    reported = _title_scores(title)
    if not reported or None in (match.home_score, match.away_score):
        return True
    home, away = team_positions(title, match, local_names)
    score = (match.home_score, match.away_score)
    return reported == {score if home < away else score[::-1]}


def _egypt_cup_highlight(metadata, match, registry, stamps, found_seasons, expected_season):
    # The Egypt Cup broadcaster includes the cup and final score in the title.
    # Match the season and require an upload on the local match day or next day.
    publishers = (
        item for item in registry
        if match.league in item.get('highlight_leagues', ())
    )
    title = metadata['title']
    if not (
        official(metadata, publishers) and found_seasons == {expected_season} and stamps
        and None not in (match.home_score, match.away_score)
        and any(label in fold(title) for label in ('كاس مصر', 'egypt cup'))
        and _title_scores(title) and _compatible_title_score(title, match)
    ):
        return False
    day = match_date(match)
    return all(
        match.kickoff <= stamp <= match.kickoff + 36 * 3600
        and day <= match_date(match, stamp) <= day + dt.timedelta(days=1)
        for stamp in stamps
    )


def _serie_b_score(title, description, match):
    position = team_positions(title, match)
    score = match.home_score, match.away_score
    if position is None or None in score:
        return False
    first, second = score if position[0] < position[1] else score[::-1]
    reported = _title_scores(title)
    if reported:
        return reported == {(first, second)}
    # A headline may name the loser first while the score in the description
    # gives the winner's goals first. Require a result verb between the teams.
    start, end = sorted(position)
    phrase = fold(title)[start:end].rstrip()
    result = (first > second and phrase.endswith((' vence', ' venceu'))
              or first < second and phrase.endswith((' perde para', ' perdeu para')))
    winner, loser = max(score), min(score)
    summary = description.split('\n', 1)[0][:300]
    return bool(result and team_positions(summary, match) is not None
                and re.search(rf'\bpor {winner} a {loser}\b', fold(summary)))


def _serie_b_seasons(content):
    return {
        int(year)
        for pair in re.findall(
            r'\b(?:serie b|temporada|brasileirao) (?:de )?(20\d{2})\b|\b(20\d{2}) (?:brasileirao )?serie b\b',
            fold(content),
        )
        for year in pair if year
    }


def verify(entry,metadata,match,registry,*,espn_season=None,flashscore_season=None,local_names=()):
    """Fail closed unless date and fixture identity can be tied to this match."""
    linked_video = flashscore_season is not None
    if not candidate_ok(entry,match,linked_video=linked_video,local_names=local_names) or metadata.get('id')!=entry.get('id'):return None,'candidate'
    authoritative=dict(entry,title=metadata.get('title',''),live=metadata.get('live'))
    if not candidate_ok(authoritative,match,linked_video=linked_video,local_names=local_names):return None,'title'
    description=str(metadata.get('description') or '')[:10000]
    content=metadata.get('title','')+'\n'+description
    normal=fold(content+' '+' '.join(metadata.get('tags') or []))
    if fold(metadata.get('category'))=='gaming' or GAMES.search(normal):return None,'game'
    if OTHER.search(fold(metadata.get('title',''))):return None,'commentary'
    sample=metadata.get('title','')+' '+description[:300]
    if flashscore_season is None and not HIGHLIGHTS.search(fold(sample)) and not (
            match.league.startswith('flashscore:') and has_local_highlight_keyword(sample)):
        return None,'not_highlights'
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
    key=league_key(match.league)
    is_flashscore_serie_b=key=='bra.2' and match.league.startswith('flashscore:')
    found_seasons=seasons(content)
    day=utc_day
    start=day.year if day.month>=7 else day.year-1
    expected_season=(start,start+1)
    if flashscore_season is not None:
        if found_seasons - {flashscore_season}:return None,'season'
        # Standalone written years also cover calendar-year competitions.
        years = {int(year) for year in SEASON_YEAR.findall(fold(content))}
        if is_flashscore_serie_b:
            years.update(_serie_b_seasons(content))
        if years - {flashscore_season[0]}:return None,'season'
        if not _compatible_title_score(metadata['title'], match):return None,'unverified_fixture'
    elif is_flashscore_serie_b:
        day=match_date(match)
        if found_seasons or _serie_b_seasons(content)-{day.year}:return None,'season'
        if not _serie_b_score(metadata['title'],description,match):return None,'unverified_fixture'
    elif any(v!=expected_season for v in found_seasons):return None,'season'
    trusted=official(metadata,registry)
    exact_date=bool(written.intersection(days))
    if local_names:
        # The added aliases establish names only. The extra search still needs
        # the fixture date, the season and the final score in the video metadata.
        if not exact_date:
            return None,'match_date'
        if not found_seasons:
            return None,'season'
        if not _title_scores(metadata['title']):
            return None,'unverified_fixture'
    if (flashscore_season is None and match.league.startswith('flashscore:') and not is_flashscore_serie_b
            and not _compatible_title_score(metadata['title'], match, local_names)):
        return None,'unverified_fixture'
    if flashscore_season is not None:
        # The exact Video tab provides date, season and score evidence independently of
        # free-text YouTube metadata. It is still subject to the time window,
        # team, live and game checks above. Never use this for a search hit.
        if not stamps or len(stamps)!=sum(bool(value) for value in values):
            return None,'missing_date'
    elif is_flashscore_serie_b and not exact_date:
        broadcasters=(item for item in registry if key in item.get('calendar_season_leagues',()))
        publication_days={day,day+dt.timedelta(days=1)}
        if not (espn_season==day.year and official(metadata,broadcasters)
                and re.search(r'\bserie b\b',fold(metadata['title']+' '+description[:300]))
                and stamps and len(stamps)==sum(bool(value) for value in values)
                and all(s<=match.kickoff+36*3600 and match_date(match,s) in publication_days for s in stamps)):
            return None,'unverified_fixture'
    elif not exact_date and match.league == 'flashscore:egypt: egypt cup':
        if (len(stamps)!=sum(bool(value) for value in values)
                or not _egypt_cup_highlight(metadata, match, registry, stamps, found_seasons, expected_season)):
            return None,'unverified_fixture'
    elif not exact_date:
        # One home league fixture per season: require its published season AND
        # exact home-away score from a known official channel. UEFA repeats
        # opponents across rounds, so it always needs an explicit match date.
        position=team_positions(metadata.get('title',''),match)
        score=bool(match.home_score is not None and match.away_score is not None and re.search(
            r'(?<!\d)'+str(match.home_score)+r'\s*[-–:]\s*'+str(match.away_score)+r'(?!\d)',metadata.get('title','')+' '+description[:1200]))
        if not (trusted and key in LEAGUE_ZONES and expected_season in found_seasons
                and position and position[0]<position[1] and score):return None,'unverified_fixture'
    result=dict(entry,title=metadata['title'],channel=metadata.get('channel') or entry.get('channel',''),
        official=trusted,match_date_verified=True,channel_id=metadata.get('channel_id',''),
        verified_publish_date=metadata.get('publish_date') or metadata.get('upload_date'))
    return result,'verified'

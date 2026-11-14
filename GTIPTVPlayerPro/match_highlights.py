# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Completed-match search context, without network or playback operations."""
import datetime as dt
import html
import unicodedata

from .match_highlights_languages import (
    international_team, international_territory, local_country_name, search_locale,
)
from .matches import LEAGUE_NAMES, text
from .matches_text import tr


_FLASHSCORE_LEAGUES = {
    ("turkey", "super lig"): "tur.1",
    ("turkiye", "super lig"): "tur.1",
    ("england", "premier league"): "eng.1",
    ("germany", "bundesliga"): "ger.1",
    ("italy", "serie a"): "ita.1",
    ("spain", "laliga"): "esp.1",
    ("spain", "la liga"): "esp.1",
    ("france", "ligue 1"): "fra.1",
    ("netherlands", "eredivisie"): "ned.1",
    ("portugal", "liga portugal"): "por.1",
    ("portugal", "primeira liga"): "por.1",
    ("brazil", "serie b"): "bra.2",
}

_FLASHSCORE_MATCH_ZONES = {
    "argentina": ("America/Argentina/Buenos_Aires", -3),
    "brazil": ("America/Sao_Paulo", -3),
    "egypt": ("Africa/Cairo", 2),
    "mexico": ("America/Mexico_City", -6),
}

_LOCAL_COMPETITION_NAMES = {
    "bra.2": "Série B",
    "flashscore:argentina: reserve league - clausura": "Torneo Proyección Clausura",
    "flashscore:egypt: egypt cup": "كأس مصر",
}

_FLASHSCORE_TEAMS = {
    "atl madrid": "Atlético Madrid",
    "b dortmund": "Borussia Dortmund",
    "b monchengladbach": "Borussia Mönchengladbach",
    "manchester utd": "Manchester United",
    "man utd": "Manchester United",
    "man city": "Manchester City",
    "paris sg": "Paris Saint-Germain",
    "psg": "Paris Saint-Germain",
}

# Short names such as Inter and Milan also occur in other competitions.
_FLASHSCORE_LEAGUE_TEAMS = {
    ("ger.1", "bayern munich"): "Bayern München",
    ("ger.1", "fc koln"): "1. FC Köln",
    ("ita.1", "inter"): "Inter Milan",
    ("ita.1", "milan"): "AC Milan",
    ("esp.1", "betis"): "Real Betis",
    ("fra.1", "rennes"): "Stade Rennais",
    ("fra.1", "lille"): "LOSC Lille",
    ("bra.2", "operario pr"): "Operário",
    ("flashscore:belgium: jupiler pro league", "club brugge kv"): "Club Brugge",
    ("flashscore:argentina: super cup", "estudiantes l p"): "Estudiantes de La Plata",
    ("flashscore:argentina: super cup", "ind rivadavia"): "Independiente Rivadavia",
    ("flashscore:south korea: k league 1 - championship group", "ulsan hd"): "Ulsan",
    ("flashscore:south korea: k league 1 - relegation group", "ulsan hd"): "Ulsan",
    ("flashscore:south korea: k league 1", "ulsan hd"): "Ulsan",
    ("flashscore:mexico: liga premier serie a", "club calor"): "Calor FC",
    ("flashscore:mexico: liga premier serie a", "apodaca"): "Real Apodaca",
    ("flashscore:argentina: reserve league - clausura", "boca juniors 2"): "Boca Juniors Reserva",
    ("flashscore:argentina: reserve league - clausura", "newells old boys 2"): "Newell's Old Boys Reserva",
}

# The score feed can use an English translation or a sponsor's short name.
_LOCAL_TEAM_NAMES = {
    "egypt": {
        "arab contractors": "المقاولون العرب",
        "we sc": "المصرية للاتصالات",
    },
}


def available(match):
    return bool(match is not None and match.category == 'finished'
                and match.phase not in ('Cancelled', 'Abandoned'))


def fold(value):
    value = html.unescape(str(value or "")).casefold().replace("ı", "i")
    value = unicodedata.normalize("NFKD", value)
    plain = "".join(c for c in value if not unicodedata.combining(c))
    return " ".join("".join(c if c.isalnum() else " " for c in plain).split())


def league_key(league):
    if not league.startswith("flashscore:"):
        return league
    country, _, competition = league[len("flashscore:"):].partition(":")
    # Keep qualifiers such as Women, 2 and Play Offs in the comparison.
    return _FLASHSCORE_LEAGUES.get((fold(country), fold(competition)), league)


def flashscore_country(league):
    if league.startswith("flashscore:"):
        return league.split(":", 2)[1].strip()
    return ""


def flashscore_match_zone(league):
    return _FLASHSCORE_MATCH_ZONES.get(flashscore_country(league))


def match_date(match, timestamp=None):
    stamp = match.kickoff if timestamp is None else timestamp
    match_zone = flashscore_match_zone(match.league)
    if match_zone is None:
        return dt.datetime.fromtimestamp(stamp).date()
    zone_name, offset = match_zone
    try:
        from .timezone_compat import ZoneInfo
        zone = ZoneInfo(zone_name)
    except (ImportError, OSError, ValueError, KeyError):
        # These competitions only search recent fixtures. Use the current
        # standard offset on images with incomplete timezone data.
        zone = dt.timezone(dt.timedelta(hours=offset))
    return dt.datetime.fromtimestamp(stamp, zone).date()


def search_team_name(name, league):
    if not league.startswith("flashscore:"):
        return name
    key = fold(name)
    return (_FLASHSCORE_TEAMS.get(key)
            or _FLASHSCORE_LEAGUE_TEAMS.get((league_key(league), key), name))


def local_team_name(name, league):
    return _LOCAL_TEAM_NAMES.get(flashscore_country(league), {}).get(fold(name), name)


def _shorten(value, limit):
    if len(value) <= limit:
        return value
    part = value[:limit]
    boundary = part.rfind(' ')
    return part[:boundary] if boundary >= limit // 2 else part


def _search_teams(match, local=False):
    teams = (search_team_name(name, match.league) for name in (match.home, match.away))
    return (local_team_name(name, match.league) for name in teams) if local else teams


def _fixture_query(teams, suffix):
    home, away = (text(name, 100) for name in teams)
    budget = 120 - len(suffix) - 2
    home_limit = min(len(home), budget // 2)
    away_limit = min(len(away), budget - home_limit)
    home_limit = min(len(home), budget - away_limit)
    return ' '.join((_shorten(home, home_limit), _shorten(away, away_limit), suffix))


def search_query(match):
    """Keep both opponents and the match date within 120 characters."""
    if not available(match):
        return ''
    day = match_date(match).isoformat()
    key = league_key(match.league)
    league = "Brasileirão Série B" if key == "bra.2" else LEAGUE_NAMES.get(key, '')
    if not league and match.league.startswith("flashscore:"):
        league = match.league.split(":", 2)[-1]
    keyword = text(tr('Match highlights'), 40)
    suffix = ' '.join(part for part in (day, text(league, 28), keyword) if part)
    return _fixture_query(_search_teams(match), suffix)


def alternate_search(match, device_locale):
    country = flashscore_country(match.league)
    if country:
        locale, keyword = search_locale(country, device_locale)
        league = _LOCAL_COMPETITION_NAMES.get(league_key(match.league), match.league.split(":", 2)[-1])
        score = ""
        if match.home_score is not None and match.away_score is not None:
            score = "{}-{}".format(match.home_score, match.away_score)
        day = "" if flashscore_match_zone(match.league) else match_date(match).isoformat()
        suffix = " ".join(part for part in (score, text(league, 28), keyword, day) if part)
    else:
        locale = device_locale
        suffix = match_date(match).isoformat() + ' highlights -efootball -fifa -gameplay -simulation'
    return _fixture_query(_search_teams(match, local=True), suffix), locale


def international_search(match, device_locale):
    """Use the participants' territory for one extra search of an international fixture."""
    if (not available(match) or not international_territory(flashscore_country(match.league))
            or None in (match.home_score, match.away_score)):
        return None
    home, away = (international_team(name) for name in (match.home, match.away))
    country = home[0] or away[0]
    if not country:
        return None

    locale, keyword = search_locale(country, device_locale)
    # When the first side would repeat the search language, use the other side's language.
    device_language = device_locale.replace("_", "-").split("-", 1)[0].lower()
    if home[0] and away[0] and locale.split("-", 1)[0] == device_language:
        locale, keyword = search_locale(away[0], device_locale)

    teams = tuple(
        local_country_name(country, locale) + qualifier if country and qualifier is not None else name
        for country, name, qualifier in (home, away)
    )
    score = "{}-{}".format(match.home_score, match.away_score)
    suffix = "{} {} {}".format(score, match_date(match).isoformat(), keyword)
    return (_fixture_query(teams, suffix), locale), teams

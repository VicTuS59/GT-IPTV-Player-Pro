# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read YouTube highlights linked by Flashscore's public match Video tab."""

import json
import re
from html import unescape
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request, build_opener

from .match_highlights import available, fold, match_date
from .match_highlights_filter import VIDEO_ID
from .matches import Budget, _SameHostRedirect, read_score_body


_SITE = "https://www.flashscore.com"
_FEED = "https://global.flashscore.ninja/2/x/feed/"
_USER_AGENT = "GTIPTVPlayerPro/1.9.3 (scores)"
_MATCH_ID = re.compile(r"[A-Za-z0-9]{8}")
_VIDEO_PAGE = re.compile(r"/match/football/[^/]+/[^/]+/(?:video/)?")
_ENVIRONMENT = re.compile(r"<script>\s*window\.environment\s*=\s*(?=\{)")
_SEASON = re.compile(r'<div class="heading__info">\s*(20\d{2})(?:\s*[/–-]\s*(20\d{2}|\d{2}))?\s*</div>')
_PAGE_LIMIT = 1536 * 1024
_FEED_LIMIT = 32 * 1024
_RESULT_LIMIT = 3


def _youtube_id(value):
    try:
        url = urlsplit(unescape(value))
    except ValueError:
        return ""
    if url.scheme not in ("http", "https"):
        return ""
    is_youtube = url.netloc in ("youtube.com", "www.youtube.com")
    if is_youtube and url.path == "/watch":
        ids = parse_qs(url.query).get("v", ())
        identifier = ids[0] if len(ids) == 1 else ""
    elif (is_youtube or url.netloc in ("www.youtube-nocookie.com", "youtube-nocookie.com")) and url.path.startswith("/embed/"):
        identifier = url.path[len("/embed/"):]
    elif url.netloc == "youtu.be":
        identifier = url.path[1:]
    else:
        return ""
    return identifier if VIDEO_ID.fullmatch(identifier) else ""


def _video_ids(body):
    result = []
    if not body.endswith("~"):
        return ()
    for row in body.split("~"):
        pairs = [field.split("÷", 1) for field in row.split("¬") if "÷" in field]
        fields = dict(pairs)
        # HIH is the Video tab's full match highlight flag. Goals and previews
        # have their own rows and must never be promoted to match summaries.
        if (len(fields) != len(pairs) or fields.get("HIH") != "1" or fields.get("HHV", "").casefold() != "youtube"
                or not fields.get("HII", "").isdigit()):
            continue
        ids = {_youtube_id(fields[key]) for key in ("HUO", "HUR") if fields.get(key)}
        if "" in ids or len(ids) != 1:
            continue
        video_id = ids.pop()
        if video_id not in result:
            result.append(video_id)
        if len(result) >= _RESULT_LIMIT:
            break
    return tuple(result)


def _check_fixture(env, match):
    if (env["event_id_c"] != match.id or env["sport"] != "soccer"
            or env["eventStageTypeId"] != 3):
        raise ValueError("Unexpected Flashscore fixture")

    home, away = (env["participantsData"][side] for side in ("home", "away"))
    if (len(home) != 1 or len(away) != 1
            or (fold(home[0]["name"]), fold(away[0]["name"])) != (fold(match.home), fold(match.away))):
        raise ValueError("Unexpected Flashscore teams")
    if fold(env["header"]["country_name"]) != fold(match.league.split(":", 2)[1]):
        raise ValueError("Unexpected Flashscore country")

    # DC is the UTC kickoff. DE and DF are the home and away final scores.
    scores = {key: value for block in env["common_feed"] for key, value in block.items()}
    kickoff = scores["DC"]
    if (type(kickoff) is not int or abs(kickoff - match.kickoff) > 36 * 3600
            or (scores["DE"], scores["DF"]) != (str(match.home_score), str(match.away_score))):
        raise ValueError("Flashscore result differs from the selected match")
    return match._replace(kickoff=kickoff)


def _league_season(env, opener, headers, budget):
    tournament_path = env["header"]["tournament"]["link"]
    if not re.fullmatch(r"/football/[a-z0-9-]+/[a-z0-9-]+/", tournament_path):
        return None
    stage = env["stats2_config"]["tournamentStage"]
    if not re.fullmatch(r"[A-Za-z0-9]{8}", stage):
        return None
    page = Request(_SITE + tournament_path, headers=dict(headers, Accept="text/html"))
    with opener(page, timeout=budget.timeout()) as response:
        if response.geturl() != page.full_url:
            return None
        html = read_score_body(response, _PAGE_LIMIT, budget).decode("utf-8-sig")
    # Match the tournament stage as well as the heading so that the current
    # season of a league cannot validate a video from a prior season.
    stage_pattern = r'(?:tournamentStageId\s*:\s*"{0}"|(?:^|¬)ZC÷{0}¬)'.format(stage)
    if not re.search(stage_pattern, html):
        return None
    season = _SEASON.search(html)
    if season is None:
        return None
    start, end = season.groups()
    first = int(start)
    last = (2000 + int(end) if len(end) == 2 else int(end)) if end else first
    return (first, last) if last in (first, first + 1) else None


def fetch_flashscore_videos(match, cancel, opener=None):
    """Return the match, its checked season, and candidate YouTube IDs."""
    if (not available(match) or not match.league.startswith("flashscore:")
            or None in (match.home_score, match.away_score) or not _MATCH_ID.fullmatch(match.id)):
        return None, None, ()

    budget = Budget(cancel, seconds=6)
    opener = opener or build_opener(_SameHostRedirect()).open
    headers = {"User-Agent": _USER_AGENT, "Accept-Encoding": "identity"}
    page = Request("{}/match/{}/".format(_SITE, match.id), headers=dict(headers, Accept="text/html"))
    with opener(page, timeout=budget.timeout()) as response:
        location = urlsplit(response.geturl())
        if (location.scheme != "https" or location.netloc != "www.flashscore.com"
                or not _VIDEO_PAGE.fullmatch(location.path)
                or parse_qs(location.query).get("mid") != [match.id]):
            return None, None, ()
        body = read_score_body(response, _PAGE_LIMIT, budget).decode("utf-8-sig")
    marker = _ENVIRONMENT.search(body)
    if marker is None:
        return None, None, ()
    end = body.find("</script>", marker.end())
    if end < 0:
        return None, None, ()
    env, _ = json.JSONDecoder().raw_decode(body[marker.end():end])
    # A daily score row can be listed on an adjacent date. The exact match ID
    # and matching teams and final score identify the authoritative kickoff.
    match = _check_fixture(env, match)

    # This header is provided by the public match page for browser requests.
    # Use the current value in memory and send it only to Flashscore's feed.
    signature = env["config"]["app"]["feed_sign"]
    if not isinstance(signature, str) or not re.fullmatch(r"[A-Za-z0-9_+/=.-]{1,80}", signature):
        return match, None, ()
    feed = Request(_FEED + "df_hi_1_" + match.id, headers={
        **headers, "Accept": "*/*", "Referer": _SITE + "/", "Origin": _SITE, "X-Fsign": signature,
    })
    with opener(feed, timeout=budget.timeout()) as response:
        if response.geturl() != feed.full_url:
            return match, None, ()
        ids = _video_ids(read_score_body(response, _FEED_LIMIT, budget).decode("utf-8-sig"))
    season = _league_season(env, opener, headers, budget) if ids else None
    if season is None or not season[0] <= match_date(match).year <= season[1]:
        return match, None, ()
    return match, season, ids

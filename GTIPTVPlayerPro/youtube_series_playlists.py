# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read episodes from the public pages of curated YouTube playlists."""

import re
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from typing import NamedTuple
from unicodedata import category, normalize
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, build_opener
from xml.etree import ElementTree

from .youtube_live import (LiveLookupError, _ORIGIN, _PublicRedirects, _VIDEO_ID,
                           _objects, _page_object, _public_page, _time_left)
from .youtube_series_catalog import YouTubeEpisode


_PLAYLIST_ID = re.compile(r"PL[A-Za-z0-9_-]{10,48}")
_CHANNEL_ID = re.compile(r"UC[A-Za-z0-9_-]{22}")
_TIMEOUT = 8.0
_FEED_LIMIT = 384 * 1024
_MAX_EPISODE_SECONDS = 4 * 60 * 60
_WORKERS = ThreadPoolExecutor(max_workers=3, thread_name_prefix="GT-YouTube-Series")
_PLAYLIST_RENDERERS = ("playlistVideoListRenderer", "playlistPanelRenderer")
_VIDEO_RENDERERS = ("playlistVideoRenderer", "playlistPanelVideoRenderer")
_NS = {"atom": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}
_DURATION = re.compile(r"\d{1,3}:[0-5]\d(?::[0-5]\d)?")
_EXCERPT = re.compile(
    r"\b(?:shorts?|clips?|highlights?|trailers?|previews?|teasers?|recaps?|compilations?|marathons?|"
    r"best of|behind the scenes|coming up next|sneak peek|and more|fragman\w*|tanitim\w*|"
    r"ozet\w*|sahne(?:ler\w*|si)?|klip\w*|kamera arkasi|on izleme|canli yayin|"
    r"kisa bolum\w*|(?:iki|uc|[2-9]) bolum bir arada|uzun versiyon|"
    r"bande annonce|extraits?|resumes?|trailer ufficiale|anteprima|"
    r"resumen(?:es)?|avances?|escenas?|fragmentos?|mejores momentos|trechos?|melhores momentos|zusammenschnitt|vorschau|"
    r"soundtracks?|songs?|canzoni|musicas?|ost|part\s*\d+|parte\s*\d+|parca\s*\d+)\b"
    r"|&\s*more\b|[\[(]\s*[1-9]\s*/\s*[2-9]\s*[\])]|"
    r"\b24\s*/\s*7\b"
    r"|予告|切り抜き|예고|하이라이트|ملخص|برومو|مقاطع|ตวอยาง|анонс|уривок",
)
_EPISODE_TITLE = re.compile(
    r"\b(?:ep(?:isodes?|isodios?)?|bolum|capitulo|folge|aflevering|episodi[oa]|tap|odcinek|dil|сер[іи]я|الحلق[ةه])"
    r"\.?\s*[:#.-]?\s*\d{1,4}\b"
    r"|\b\d{1,4}\s*\.?\s*(?:bolum|folge|aflevering|ep(?:isode)?|сер[іи]я)\b"
    r"|\b[st]\d{1,2}\s*e\d{1,4}\b"
    r"|\b(?:full\s*episode|tam bolum|ganze folge|episode complet|episodio complet[oa])\b"
    r"|第\s*\d{1,4}\s*[話回集]|\d{1,4}\s*[화회集]|ตอน(?:ท)?\s*\d{1,4}",
)
_SHOW_PREFIX = re.compile(
    r"^(.{2,100}?)\s+[|·:–-]?\s*(?:\d{1,4}\s*\.\s*bölüm|"
    r"(?:full\s+)?(?:episodes?|episod(?:io)?|cap[ií]tulo)\s*[:#.]?\s*\d{1,4})\b",
    re.IGNORECASE,
)
_SEASON_EPISODE = re.compile(r"\b[st]\s*(\d{1,2})\s*e\s*(\d{1,4})\b")
_SEASON_NUMBER = re.compile(
    r"\b(?:season|sezon|temporada|staffel|saison|stagione)\s*(\d{1,2})\b"
    r"|\b(\d{1,2})\s*\.?\s*(?:sezon|sezonu)\b"
)
_EPISODE_NUMBER = re.compile(
    r"\b(?:ep(?:isodes?|isodios?)?|bolum|capitulo|folge|aflevering|episodi[oa]|tap|odcinek|сер[іи]я|الحلق[ةه])"
    r"\.?\s*[:#.-]?\s*(\d{1,4})\b"
    r"|\b(\d{1,4})\s*\.?\s*(?:bolum|folge|aflevering|ep(?:isode)?|сер[іи]я)\b"
    r"|第\s*(\d{1,4})\s*[話回集]|(\d{1,4})\s*[화회集]|ตอน(?:ท)?\s*(\d{1,4})"
)
_BARE_EPISODE = re.compile(r"^(?:第\s*\d+\s*[話回集]|\d+\s*[화회集]|"
                           r"\d+\s+(?:bolum|folge)|(?:ep(?:isode)?|capitulo|bolum|folge)\s+\d+)$")
_PREMIERE_YEAR = re.compile(r"(?:19|20)\d{2}")


class _PlaylistEntry(NamedTuple):
    episode: YouTubeEpisode
    seconds: int
    uploader: str = ""


class _PlaylistPage(NamedTuple):
    entries: tuple
    more: bool
    owner: str


def normalized_title(value):
    text = normalize("NFD", normalize("NFKC", value).casefold().replace("ı", "i"))
    return normalize("NFC", "".join(c for c in text if category(c) != "Mn"))


def title_key(value):
    return " ".join(re.findall(r"\w+", normalized_title(value)))


def is_excerpt(value):
    return bool(_EXCERPT.search(normalized_title(value)))


def episode_series_name(value):
    match = _SHOW_PREFIX.match(value)
    return match.group(1).strip(" |·:–-") if match else ""


def episode_position(value, *, at_start=False):
    title = normalized_title(value)
    pair = _SEASON_EPISODE.match(title) if at_start else _SEASON_EPISODE.search(title)
    if pair:
        return int(pair[1]), int(pair[2])
    season = _SEASON_NUMBER.match(title) if at_start else _SEASON_NUMBER.search(title)
    if season:
        title = (title[:season.start()] + " " + title[season.end():]).lstrip()
    episode = _EPISODE_NUMBER.match(title) if at_start else _EPISODE_NUMBER.search(title)
    if not episode:
        return 0, 0
    number = int(next(value for value in episode.groups() if value))
    return (int(season[1] or season[2]) if season else 0), number


def named_episode_position(label, aliases, year=""):
    """Read an episode number immediately after an exact series title."""
    if is_excerpt(label):
        return None
    key = title_key(label)
    for name in aliases:
        alias = title_key(name)
        if not alias or not key.startswith(alias + " "):
            continue
        suffix = key[len(alias) + 1:]
        qualifier, _, rest = suffix.partition(" ")
        if _PREMIERE_YEAR.fullmatch(qualifier):
            position = episode_position(rest, at_start=True)
            if position[1]:
                if not year or qualifier == year:
                    return position
                continue
        position = episode_position(suffix, at_start=True)
        if position[1]:
            return position
    return None


def same_episode_position(left, right):
    return left[1] == right[1] and (not left[0] or not right[0] or left[0] == right[0])


def full_episodes(page, minimum_seconds, *, numbered=False, show="", aliases=(), bare_title=False):
    episodes = {}
    show_key = title_key(show)
    alias_keys = tuple(" " + key + " " for name in aliases if (key := title_key(name)))
    for entry in page.entries:
        title = entry.episode.title
        if not minimum_seconds <= entry.seconds <= _MAX_EPISODE_SECONDS or is_excerpt(title):
            continue
        if entry.uploader and page.owner and entry.uploader != page.owner:
            continue
        if numbered and not (_EPISODE_TITLE.search(normalized_title(title)) or episode_position(title)[1]):
            continue
        if show_key and title_key(episode_series_name(title)) != show_key:
            continue
        if alias_keys:
            episode_key = " " + title_key(title) + " "
            if not any(key in episode_key for key in alias_keys) and not (
                    bare_title and (_BARE_EPISODE.fullmatch(episode_key.strip()) or
                                    _SEASON_EPISODE.fullmatch(episode_key.strip()))):
                continue
        episodes.setdefault(entry.episode.video_id, entry.episode)
    return tuple(episodes.values())


def _playlist_ids(series):
    configured = getattr(series, "playlist_ids", ())
    if not isinstance(configured, (tuple, list)):
        return ()
    return tuple(dict.fromkeys(value for value in configured
                               if isinstance(value, str) and _PLAYLIST_ID.fullmatch(value)))


def _episode(video_id, title):
    if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id) or not isinstance(title, str):
        return None
    title = " ".join(title.split())[:300]
    if not title or title.casefold().strip("[]") in ("private video", "deleted video"):
        return None
    return YouTubeEpisode(title, video_id)


def _title(value):
    if not isinstance(value, dict):
        return value
    text = value.get("simpleText")
    if isinstance(text, str):
        return text
    runs = value.get("runs")
    if isinstance(runs, list):
        return "".join(run["text"] for run in runs
                       if isinstance(run, dict) and isinstance(run.get("text"), str))
    return ""


def _field(value, *path):
    for key in path:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def channel_tab_contents(data, channel_id, title):
    if _field(data, "metadata", "channelMetadataRenderer", "externalId") != channel_id:
        raise LiveLookupError()
    tabs = _field(data, "contents", "twoColumnBrowseResultsRenderer", "tabs") or []
    for node in tabs:
        renderer = _field(node, "tabRenderer")
        if title == "Search" and not isinstance(renderer, dict):
            renderer = _field(node, "expandableTabRenderer")
        if not isinstance(renderer, dict) or renderer.get("selected") is not True:
            continue
        path = _field(renderer, "endpoint", "commandMetadata", "webCommandMetadata", "url")
        if (renderer.get("title") == title or isinstance(path, str)
                and path.split("?", 1)[0].rstrip("/").endswith("/" + title.lower())):
            return renderer.get("content")
    return ()


def _seconds(value):
    if not isinstance(value, str) or not _DURATION.fullmatch(value):
        return 0
    result = 0
    for part in value.split(":"):
        result = 60 * result + int(part)
    return result


def _lockup_seconds(model):
    for node in _objects(model.get("contentImage")):
        badge = node.get("thumbnailOverlayBadgeViewModel")
        if isinstance(badge, dict) and (seconds := _seconds(badge.get("text"))):
            return seconds
        badge = node.get("thumbnailBadgeViewModel")
        if isinstance(badge, dict) and (seconds := _seconds(badge.get("text"))):
            return seconds
    return 0


def _lockup_episode(model, playlist_id):
    if not isinstance(model, dict) or model.get("contentType") != "LOCKUP_CONTENT_TYPE_VIDEO":
        return None
    watch = _field(model, "rendererContext", "commandContext", "onTap", "innertubeCommand", "watchEndpoint")
    video_id = model.get("contentId")
    if not isinstance(watch, dict) or watch.get("playlistId") != playlist_id or watch.get("videoId") != video_id:
        return None
    if _non_public_card(model.get("contentImage")):
        return None
    title = _field(model, "metadata", "lockupMetadataViewModel", "title", "content")
    episode = _episode(video_id, title)
    owner = _uploader(_field(model, "metadata", "lockupMetadataViewModel", "metadata", "contentMetadataViewModel"))
    return _PlaylistEntry(episode, _lockup_seconds(model), owner) if episode is not None else None


def _channel_ids(data):
    publishers = set()
    for node in _objects(data):
        code = _field(node, "browseEndpoint", "browseId")
        if isinstance(code, str) and _CHANNEL_ID.fullmatch(code):
            publishers.add(code)
    return publishers


def _uploader(data):
    ids = _channel_ids(data)
    return next(iter(ids), "") if len(ids) <= 1 else "-"


def _non_public_card(data):
    for node in _objects(data):
        style = node.get("style") or node.get("badgeStyle")
        if isinstance(style, str) and any(token in style.upper() for token in (
                "MEMBERS_ONLY", "LIVE_NOW", "UPCOMING", "PRIVATE", "PAID", "PREMIUM")):
            return True
        label = node.get("label") or node.get("text")
        if isinstance(label, str) and label.strip().casefold() in (
                "members only", "members-only", "private video", "buy or rent", "upcoming", "live"):
            return True
    return False


def search_episode_rows(data, channel_id=""):
    """Yield video cards with an explicit uploader and duration."""
    for node in _objects(data.get("contents")):
        video = node.get("videoRenderer") or node.get("gridVideoRenderer")
        model = node.get("lockupViewModel")
        if isinstance(video, dict) and video.get("isPlayable") is not False:
            if _non_public_card([video.get("badges"), video.get("thumbnailOverlays")]):
                continue
            video_id = video.get("videoId")
            title = _title(video.get("title"))
            seconds = _seconds(_title(video.get("lengthText")))
            byline = [video.get(k) for k in ("ownerText", "longBylineText", "shortBylineText")]
        elif _field(model, "contentType") == "LOCKUP_CONTENT_TYPE_VIDEO":
            metadata = _field(model, "metadata", "lockupMetadataViewModel")
            byline = _field(metadata, "metadata", "contentMetadataViewModel", "metadataRows")
            if _non_public_card([model.get("contentImage"), byline]):
                continue
            video_id = model.get("contentId")
            title = _field(metadata, "title", "content")
            seconds = _lockup_seconds(model)
        else:
            continue
        episode = _episode(video_id, title)
        owners = _channel_ids(byline)
        owner = next(iter(owners), channel_id)
        if len(owners) <= 1 and owner and seconds and episode is not None:
            yield owner, _PlaylistEntry(episode, seconds)


def _playlist_page(playlist_id, deadline, cancel):
    path = "/playlist?" + urlencode({"list": playlist_id, "hl": "en"})
    page = _public_page(path, deadline, cancel, "en-US,en;q=0.9")
    _time_left(deadline, cancel)
    data = _page_object(page, "ytInitialData")
    rows = []
    more = False
    # Only traverse playlist contents. The sidebar and other page sections can
    # include unrelated videos, even when they share a publisher.
    for node in _objects(data.get("contents")):
        section = _field(node, "itemSectionRenderer", "contents")
        if isinstance(section, list):
            matches = [_lockup_episode(item.get("lockupViewModel"), playlist_id)
                       for item in section if isinstance(item, dict)]
            matched_rows = [episode for episode in matches if episode is not None]
            if matched_rows:
                rows.extend(matched_rows)
                more |= any(isinstance(item, dict) and "continuationItemViewModel" in item for item in section)
        for key in _PLAYLIST_RENDERERS:
            renderer = node.get(key)
            if not isinstance(renderer, dict) or renderer.get("playlistId", playlist_id) != playlist_id:
                continue
            contents = renderer.get("contents")
            if not isinstance(contents, list):
                continue
            for item in contents:
                if not isinstance(item, dict):
                    continue
                more |= "continuationItemRenderer" in item
                for video_key in _VIDEO_RENDERERS:
                    video = item.get(video_key)
                    if isinstance(video, dict) and video.get("isPlayable") is not False:
                        if _non_public_card([video.get("badges"), video.get("thumbnailOverlays")]):
                            continue
                        watch = _field(video, "navigationEndpoint", "watchEndpoint")
                        if isinstance(watch, dict) and watch.get("playlistId", playlist_id) != playlist_id:
                            continue
                        episode = _episode(video.get("videoId"), _title(video.get("title")))
                        if episode is not None:
                            seconds = _seconds(_title(video.get("lengthText")))
                            raw = video.get("lengthSeconds")
                            if not seconds and isinstance(raw, str) and re.fullmatch(r"\d{1,6}", raw):
                                seconds = int(raw)
                            uploader = _uploader([video.get(k) for k in ("ownerText", "shortBylineText", "longBylineText")])
                            rows.append(_PlaylistEntry(episode, seconds, uploader))
    owners = _channel_ids([data.get("header"), data.get("sidebar")])
    owner = next(iter(owners)) if len(owners) == 1 else ""
    return _PlaylistPage(tuple(rows), more, owner)


def _watch_playlist_window(playlist_id, anchor, deadline, cancel):
    query = urlencode({"v": anchor, "list": playlist_id, "hl": "en"})
    body = _public_page("/watch?" + query, deadline, cancel, "en-US,en;q=0.9")
    data = _page_object(body, "ytInitialData")
    _time_left(deadline, cancel)
    position = None
    indexed = {}
    for node in _objects(data):
        video = node.get("playlistPanelVideoRenderer")
        watch = _field(video, "navigationEndpoint", "watchEndpoint")
        index = _field(watch, "index")
        if type(index) is not int or watch.get("playlistId") != playlist_id:
            continue
        ident = video.get("videoId")
        if ident != watch.get("videoId"):
            continue
        if ident == anchor and video.get("selected") is True:
            position = index
        if video.get("isPlayable") is False or _non_public_card([video.get("badges"), video.get("thumbnailOverlays")]):
            continue
        episode = _episode(ident, _title(video.get("title")))
        seconds = _seconds(_title(video.get("lengthText")))
        if episode is not None:
            indexed[index] = _PlaylistEntry(episode, seconds, _uploader(video.get("shortBylineText")))
    if position is None:
        return ()
    return tuple(indexed[index] for index in sorted(indexed) if index > position)


def _feed_episodes(playlist_id, deadline, cancel):
    path = "/feeds/videos.xml?" + urlencode({"playlist_id": playlist_id})
    request = Request(_ORIGIN + path, headers={"Accept": "application/atom+xml, application/xml",
                                              "Accept-Encoding": "identity", "User-Agent": "Mozilla/5.0"})
    timeout = min(2.0, _time_left(deadline, cancel))
    payload = bytearray()
    try:
        with build_opener(_PublicRedirects()).open(request, timeout=timeout) as response:
            read = getattr(response, "read1", response.read)
            while len(payload) <= _FEED_LIMIT:
                _time_left(deadline, cancel)
                chunk = read(min(32 * 1024, _FEED_LIMIT + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
    except HTTPError as error:
        error.close()
        raise LiveLookupError() from None
    if len(payload) > _FEED_LIMIT or re.search(rb"<!\s*(?:doctype|entity)", payload, re.IGNORECASE):
        return ()
    root = ElementTree.fromstring(payload)
    if root.tag != "{" + _NS["atom"] + "}feed":
        return ()
    if root.findtext("yt:playlistId", default=playlist_id, namespaces=_NS) != playlist_id:
        return ()
    episodes = (_episode(entry.findtext("yt:videoId", namespaces=_NS),
                         entry.findtext("atom:title", namespaces=_NS))
                for entry in root.findall("atom:entry", _NS))
    return tuple(episode for episode in episodes if episode is not None and not is_excerpt(episode.title)
                 and _EPISODE_TITLE.search(normalized_title(episode.title)))


def _load_channel_uploads(series, deadline, cancel):
    channel_id = series.channel_id
    if not _CHANNEL_ID.fullmatch(channel_id):
        raise LiveLookupError()
    data = _page_object(_public_page(f"/channel/{channel_id}/videos?hl=en", deadline, cancel,
                                    "en-US,en;q=0.9"), "ytInitialData")
    content = channel_tab_contents(data, channel_id, "Videos")
    rows = tuple(row for owner, row in search_episode_rows({"contents": content}, channel_id)
                 if owner == channel_id)
    _time_left(deadline, cancel)
    episodes = full_episodes(_PlaylistPage(rows, False, channel_id), series.minimum_episode_seconds,
                             numbered=True, aliases=series.title_aliases or (series.title,))
    combined = {item.video_id: item for item in (*episodes, *series.episodes)}
    if not combined:
        raise LiveLookupError()
    return tuple(combined.values())


def load_series_episodes(series, cancel=None):
    """Return successful playlist rows in publisher order within one time budget."""
    playlist_ids = _playlist_ids(series)
    deadline = time.monotonic() + _TIMEOUT
    if not playlist_ids and series.channel_uploads:
        return _load_channel_uploads(series, deadline, cancel)
    if not playlist_ids:
        raise LiveLookupError()
    owner = series.channel_id
    _time_left(deadline, cancel)
    pending = {_WORKERS.submit(_playlist_page, playlist_id, deadline, cancel): (i, False)
               for i, playlist_id in enumerate(playlist_ids)}
    results = {}
    last_error = None
    try:
        while pending and (cancel is None or not cancel.is_set()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            done, _pending = wait(pending, min(remaining, 0.1), return_when=FIRST_COMPLETED)
            for job in done:
                index, feed = pending.pop(job)
                try:
                    if feed:
                        rows, need_feed = job.result(), False
                    else:
                        page = job.result()
                        if owner and page.owner != owner:
                            raise LiveLookupError()
                        rows = full_episodes(page, series.minimum_episode_seconds,
                                             numbered=bool(owner), show=series.episode_series,
                                             aliases=series.title_aliases)
                        need_feed = not owner and (page.more or not rows)
                except (LiveLookupError, OSError, ValueError, RecursionError, ElementTree.ParseError) as error:
                    last_error = error
                    rows = ()
                    need_feed = not owner and not feed and getattr(error, "code", None) not in (
                        "rate_limited", "cancelled", "timeout")
                results[index, feed] = rows
                # The short public Atom feed can supplement a long playlist or
                # cover a missing HTML renderer. It is not a continuation API.
                if need_feed:
                    job = _WORKERS.submit(_feed_episodes, playlist_ids[index], deadline, cancel)
                    pending[job] = index, True
    finally:
        for job in pending:
            job.cancel()
    if cancel is not None and cancel.is_set():
        raise LiveLookupError("cancelled")
    episodes = {}
    for index in range(len(playlist_ids)):
        for row in results.get((index, False), ()) + results.get((index, True), ()):
            episodes.setdefault(row.video_id, row)
    if not episodes:
        if isinstance(last_error, LiveLookupError):
            raise last_error
        raise LiveLookupError("timeout" if time.monotonic() >= deadline else "unavailable")
    for episode in series.episodes:
        episodes.setdefault(episode.video_id, episode)
    return tuple(episodes.values())

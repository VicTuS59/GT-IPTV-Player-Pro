# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Find active broadcasts on public YouTube search pages."""

import json
import re
import socket
import time
from contextvars import ContextVar
from threading import Event, Lock
from typing import NamedTuple
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .i18n import N_, locale_for_language, normalize_language, translate_for_language


class LiveBroadcast(NamedTuple):
    video_id: str
    title: str
    channel: str


LIVE_CATEGORIES = {
    "all": N_("All"),
    "gaming": N_("Gaming"),
    "music": N_("Music"),
    "news": N_("News"),
    "sports": N_("Sports"),
}

# Natural queries for terms that can otherwise match unrelated broadcasts.
_CATEGORY_QUERIES = {
    ("tr", "gaming"): "Türkçe canlı yayın video oyunları",
    ("de", "music"): "Deutsche Musik im Livestream",
}

# Combine public Live searches while keeping the relevance results first.
_LIVE_FILTERS = (
    "EgJAAQ==",  # Live
    "EgQIAkAB",  # Live, today
    "EgQIA0AB",  # Live, this week
    "EgQIBEAB",  # Live, this month
    "CAMSAkAB",  # Live, popularity
)
_LIVE_STYLES = ("LIVE", "BADGE_STYLE_TYPE_LIVE_NOW", "THUMBNAIL_OVERLAY_BADGE_STYLE_LIVE")

# Search terms are sent to YouTube. The screen's own labels use gettext.
_LIVE_QUERIES = {
    "ar": "بث مباشر بالعربية",
    "bg": "на живо на български",
    "ca": "directe en català",
    "cs": "živý přenos česky",
    "da": "live på dansk",
    "de": "Livestream Deutsch",
    "el": "ζωντανή μετάδοση στα ελληνικά",
    "en": "live stream in English",
    "es": "en vivo en español",
    "et": "otseülekanne eesti keeles",
    "fa": "پخش زنده فارسی",
    "fi": "suora lähetys suomeksi",
    "fr": "direct en français",
    "fy": "live útstjoering yn it Frysk",
    "gl": "directo en galego",
    "he": "שידור חי בעברית",
    "hr": "prijenos uživo na hrvatskom",
    "hu": "élő adás magyarul",
    "id": "siaran langsung bahasa Indonesia",
    "is": "bein útsending á íslensku",
    "it": "diretta in italiano",
    "ku": "weşana zindî bi kurdî",
    "lt": "tiesioginė transliacija lietuviškai",
    "lv": "tiešraide latviešu valodā",
    "mk": "пренос во живо на македонски",
    "nb": "direktesending på norsk",
    "nl": "livestream Nederlands",
    "nn": "direktesending på nynorsk",
    "pl": "transmisja na żywo po polsku",
    "pt": "transmissão ao vivo em português",
    "ro": "transmisiune în direct în română",
    "ru": "прямой эфир на русском",
    "sk": "živé vysielanie po slovensky",
    "sl": "prenos v živo v slovenščini",
    "sq": "transmetim drejtpërdrejt në shqip",
    "sr": "пренос уживо на српском",
    "sv": "direktsändning på svenska",
    "ta": "தமிழ் நேரலை",
    "th": "ถ่ายทอดสดภาษาไทย",
    "tr": "Türkçe canlı yayın",
    "uk": "прямий ефір українською",
    "vi": "phát trực tiếp tiếng Việt",
    "zh_CN": "中文直播",
    "zh_HK": "粵語直播",
}

_ORIGIN = "https://www.youtube.com"
_HOSTS = frozenset(("www.youtube.com", "youtube.com", "m.youtube.com"))
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_PAGE_LIMIT = 4 * 1024 * 1024
_LOOKUP_SECONDS = 6.0
_LIVE_EXECUTOR = None
_LIVE_EXECUTOR_LOCK = Lock()
_PUBLIC_LOOKUP = ContextVar("youtube_public_lookup", default=None)
_TIMEOUT_MESSAGES = frozenset(("TMDb request timed out", "Image download timed out.",
                               "Image host name resolution timed out."))


class _PublicLookupTiming(NamedTuple):
    request_timeout: float
    timed_out: Event


class LiveLookupError(RuntimeError):
    def __init__(self, code="unavailable"):
        self.code = code
        super().__init__(code)


def _record_lookup_timeout(error=None):
    timing = _PUBLIC_LOOKUP.get()
    if timing is None:
        return False
    if error is None:
        timing.timed_out.set()
        return True
    for _ in range(5):
        if error is None:
            break
        if (isinstance(error, (TimeoutError, socket.timeout))
                or getattr(error, "code", None) in ("timeout", 408, 504)
                or getattr(error, "http_status", None) in (408, 504)
                or str(error) in _TIMEOUT_MESSAGES):
            timing.timed_out.set()
            return True
        reason = error.reason if isinstance(error, URLError) else None
        error = reason if isinstance(reason, BaseException) else error.__cause__ or error.__context__
    return False


class _PublicRedirects(HTTPRedirectHandler):
    max_redirections = 3

    def __init__(self, deadline=None, cancel=None):
        super().__init__()
        self.deadline = deadline
        self.cancel = cancel

    def redirect_request(self, request, fp, code, message, headers, new_url):
        target = urlsplit(new_url)
        if (target.scheme != "https" or target.hostname not in _HOSTS
                or target.port not in (None, 443) or target.username is not None
                or target.password is not None):
            raise LiveLookupError()
        if self.deadline is not None:
            request.timeout = _time_left(self.deadline, self.cancel)
        return super().redirect_request(request, fp, code, message, headers, new_url)


def _time_left(deadline, cancel):
    if cancel is not None and cancel.is_set():
        raise LiveLookupError("cancelled")
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        _record_lookup_timeout()
        raise LiveLookupError("timeout")
    return remaining


def _public_page(path, deadline, cancel, accept_language):
    timing = _PUBLIC_LOOKUP.get()
    timeout = min(timing.request_timeout if timing is not None else 4.0, _time_left(deadline, cancel))
    request_deadline = min(deadline, time.monotonic() + timeout) if timing is not None else deadline
    redirects = _PublicRedirects(request_deadline, cancel) if timing is not None else _PublicRedirects()
    request = Request(_ORIGIN + path, headers={
        "Accept": "text/html,application/xhtml+xml",
        "Accept-Encoding": "identity",
        "Accept-Language": accept_language,
        "User-Agent": "Mozilla/5.0",
    })
    payload = bytearray()
    try:
        with build_opener(redirects).open(request, timeout=timeout) as response:
            read = getattr(response, "read1", response.read)
            while len(payload) <= _PAGE_LIMIT:
                remaining = _time_left(request_deadline, cancel)
                if timing is not None:
                    raw = getattr(getattr(response, "fp", None), "raw", None)
                    set_timeout = getattr(getattr(raw, "_sock", None), "settimeout", None)
                    if callable(set_timeout):
                        set_timeout(remaining)
                chunk = read(min(65536, _PAGE_LIMIT + 1 - len(payload)))
                if not chunk:
                    if timing is not None:
                        _time_left(request_deadline, cancel)
                    return payload.decode("utf-8", "replace")
                payload.extend(chunk)
    except HTTPError as error:
        error.close()
        if _record_lookup_timeout(error):
            raise LiveLookupError("timeout") from None
        raise LiveLookupError("rate_limited" if error.code == 429 else "unavailable") from None
    except (URLError, OSError, ValueError) as error:
        if timing is not None:
            _time_left(request_deadline, cancel)
            if _record_lookup_timeout(error):
                raise LiveLookupError("timeout") from None
        raise LiveLookupError() from None
    raise LiveLookupError()


def _page_object(page, variable):
    name = re.escape(variable)
    pattern = r'''(?:\b''' + name + r'''\b|window\[\s*["']''' + name + r'''["']\s*\])\s*=\s*'''
    decoder = json.JSONDecoder()
    for match in re.finditer(pattern, page):
        try:
            value, _end = decoder.raw_decode(page, match.end())
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise LiveLookupError()


def _mapping(value):
    return value if isinstance(value, dict) else {}


def player_is_live(response):
    details = _mapping(response.get("videoDetails"))
    microformat = _mapping(_mapping(response.get("microformat")).get("playerMicroformatRenderer"))
    broadcast = _mapping(microformat.get("liveBroadcastDetails"))
    return bool(not broadcast.get("endTimestamp") and (
        details.get("isLive") is True or broadcast.get("isLiveNow") is True))


def _objects(value):
    pending = [value]
    for _node in range(12000):
        if not pending:
            break
        value = pending.pop()
        if isinstance(value, dict):
            yield value
            pending.extend(reversed(tuple(value.values())))
        elif isinstance(value, list):
            pending.extend(reversed(value))


def _is_live_badge(value):
    return any(badge.get("style") in _LIVE_STYLES or badge.get("badgeStyle") in _LIVE_STYLES
               for badge in _objects(value))


def _live_results(data):
    # Lazy import keeps the playback resolver independent of the web service.
    from .web_youtube import _label, _lockup_video, _search_items

    entries = []
    seen = set()
    for item in _objects(_search_items(data)):
        renderer = item.get("videoRenderer")
        if isinstance(renderer, dict):
            if not _is_live_badge([renderer.get("badges"), renderer.get("thumbnailOverlays")]):
                continue
        else:
            lockup = item.get("lockupViewModel")
            if not isinstance(lockup, dict) or not _is_live_badge(lockup.get("contentImage")):
                continue
            try:
                renderer = _lockup_video(lockup)
            except (AttributeError, TypeError):
                continue
            if renderer is None:
                continue
        video = renderer.get("videoId")
        if not isinstance(video, str) or not _VIDEO_ID.fullmatch(video) or video in seen:
            continue
        title = _label(renderer, "title")
        if not title:
            continue
        channel = _label(renderer, "ownerText", 100) or _label(renderer, "longBylineText", 100)
        entries.append(LiveBroadcast(video, title, channel))
        seen.add(video)
        if len(entries) == 40:
            break
    return entries


def _read_live_results(path, deadline, cancel, accept_language, page_reader):
    _time_left(deadline, cancel)
    page = page_reader(path, deadline, cancel, accept_language)
    _time_left(deadline, cancel)
    data = _page_object(page, "ytInitialData")
    if not isinstance(data.get("contents"), dict):
        raise LiveLookupError()
    return _live_results(data)


def search_live_broadcasts(category, language, cancel=None, *, page_reader=_public_page):
    """Fetch public searches concurrently and deliver a single ordered list."""
    from concurrent.futures import ThreadPoolExecutor, wait
    from .web_youtube import _search_locale

    global _LIVE_EXECUTOR
    with _LIVE_EXECUTOR_LOCK:
        if _LIVE_EXECUTOR is None:
            # The worker limit includes cancelled searches with closing sockets.
            _LIVE_EXECUTOR = ThreadPoolExecutor(max_workers=len(_LIVE_FILTERS),
                                                thread_name_prefix="GT-YouTube-Live")

    language = normalize_language(language)
    base = language.split("_", 1)[0]
    seed = _LIVE_QUERIES.get(language) or _LIVE_QUERIES.get(base, _LIVE_QUERIES["en"])
    query = seed
    if category in LIVE_CATEGORIES and category != "all":
        query = _CATEGORY_QUERIES.get((language, category))
        if query is None:
            query = seed + " " + translate_for_language(LIVE_CATEGORIES[category], language)
    language_code, region, accept_language = _search_locale(locale_for_language(language))
    deadline = time.monotonic() + _LOOKUP_SECONDS
    futures = []
    try:
        for search_filter in _LIVE_FILTERS:
            _time_left(deadline, cancel)
            params = urlencode({"search_query": query, "sp": search_filter,
                                "hl": language_code, "gl": region})
            futures.append(_LIVE_EXECUTOR.submit(_read_live_results, "/results?" + params,
                                                deadline, cancel, accept_language, page_reader))

        pending = set(futures)
        while pending and (cancel is None or not cancel.is_set()):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            _, pending = wait(pending, timeout=min(remaining, 0.10))
        if cancel is not None and cancel.is_set():
            raise LiveLookupError("cancelled")

        entries, errors = {}, set()
        for future in futures:
            if future in pending:
                errors.add("timeout")
                continue
            try:
                for row in future.result():
                    entries.setdefault(row.video_id, row)
            except Exception as error:
                errors.add(getattr(error, "code", "unavailable"))
        if not entries and errors:
            raise LiveLookupError("rate_limited" if "rate_limited" in errors else "unavailable")
        return list(entries.values())
    finally:
        for future in futures:
            future.cancel()

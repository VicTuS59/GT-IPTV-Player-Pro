# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Verify YouTube sources through the sites of a show's producer or network."""

import json
import re
import time
from collections import OrderedDict
from html.parser import HTMLParser
from threading import Lock
from typing import NamedTuple
from urllib.parse import parse_qs, quote, unquote, urlencode, urljoin, urlsplit, urlunsplit

from .network_security import NetworkSecurityError, fetch_public_url
from .youtube_live import (LiveLookupError, _VIDEO_ID, _objects, _page_object, _public_page,
                           _record_lookup_timeout, _time_left)
from .youtube_series_playlists import (_CHANNEL_ID, _PLAYLIST_ID, _PlaylistEntry, _PlaylistPage, _episode,
                                       _field, _title, episode_position, full_episodes, is_excerpt,
                                       named_episode_position, same_episode_position, title_key)


class PublisherEpisodePage(NamedTuple):
    url: str
    position: tuple[int, int]


class PublisherYouTube(NamedTuple):
    channel_ids: tuple[str, ...]
    playlist_ids: tuple[str, ...]
    episodes: tuple[tuple[str, _PlaylistEntry], ...]
    episode_pages: tuple[PublisherEpisodePage, ...] = ()
    episode_archives: tuple[str, ...] = ()
    series_channel_ids: tuple[str, ...] = ()


_YOUTUBE = frozenset(("youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be",
                     "youtube-nocookie.com", "www.youtube-nocookie.com"))
_CHANNEL_PATH = re.compile(r"/(?:channel/UC[A-Za-z0-9_-]{22}|@[^/?#%\s]{1,80}|(?:user|c)/[A-Za-z0-9_.-]{1,80})")
_LEGACY_CHANNEL_PATH = re.compile(r"/[A-Za-z0-9_.-]{3,80}")
_RESERVED_PATHS = frozenset(("/watch", "/playlist", "/results", "/embed", "/shorts", "/live", "/feed",
                             "/redirect", "/account", "/premium", "/signin", "/gaming"))
_IGNORED_HOSTS = _YOUTUBE | frozenset((
    "facebook.com", "instagram.com", "twitter.com", "x.com", "tiktok.com",
    "wikipedia.org", "themoviedb.org", "imdb.com", "linktr.ee",
))
_PAGE_CACHE = OrderedDict()
_PAGE_LOCK = Lock()
_EPISODE_ARCHIVES = frozenset((
    "bolum", "bolumler", "bolumleri", "tum bolumler", "episodes", "all episodes", "full episodes",
    "episodios", "capitulos", "folgen", "episodi", "afleveringen", "odcinki", "серии", "серіі",
    "الحلقات", "エピソード", "모든 에피소드",
))
_NON_EPISODE_PATHS = frozenset(("fotogaleri", "foto galeri", "gallery", "galeria", "news", "haber", "haberler"))
_LOOKUP_ERRORS = (NetworkSecurityError, LiveLookupError, OSError, ValueError, RecursionError)
_LANGUAGE_TAG = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}")
_DESCRIPTION_LINK = re.compile(r"https?://[^\s<>\x22']{1,1024}")


class _PublisherDocument(NamedTuple):
    url: str
    titles: tuple[str, ...]
    links: tuple[tuple[str, str], ...]
    video_links: tuple[str, ...]


class _PublisherPage(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.title_parts = None
        self.structured_data = None
        self.anchor = None
        self.titles = []
        self.links = []
        self.video_links = []

    def _link(self, value, label=""):
        if isinstance(value, str) and value and len(self.links) < 1600:
            self.links.append((value, label[:180]))

    def _video_link(self, value):
        self._link(value)
        if isinstance(value, str) and value and len(self.video_links) < 24:
            self.video_links.append(value)

    def _close_anchor(self):
        if self.anchor is not None:
            href, labels = self.anchor
            self.anchor = None
            self._link(href, " ".join(labels))

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        if tag in ("title", "h1"):
            self.title_parts = []
        elif tag == "meta":
            key = (attrs.get("property") or "").lower()
            if key == "og:title":
                self.titles.append(attrs.get("content") or "")
            elif key in ("og:video", "og:video:url", "og:video:secure_url"):
                self._video_link(attrs.get("content"))
        elif tag == "a":
            self._close_anchor()
            self.anchor = (attrs.get("href"), [attrs.get("aria-label") or attrs.get("title") or ""])
        elif tag == "img" and self.anchor is not None and attrs.get("alt"):
            self.anchor[1].append(attrs["alt"])
        elif tag == "iframe":
            self._video_link(attrs.get("src") or attrs.get("data-src"))
        elif tag == "amp-youtube":
            video_id = attrs.get("data-videoid") or ""
            if _VIDEO_ID.fullmatch(video_id):
                self._video_link("https://www.youtube.com/watch?v=" + video_id)
        elif tag == "script" and (attrs.get("type") or "").lower() == "application/ld+json":
            self.structured_data = ""

    def handle_endtag(self, tag):
        if tag in ("title", "h1") and self.title_parts is not None:
            self.titles.append(" ".join(self.title_parts))
            self.title_parts = None
        elif tag == "a":
            self._close_anchor()
        elif tag == "script" and self.structured_data is not None:
            raw, self.structured_data = self.structured_data, None
            if len(raw) > 64 * 1024:
                return
            try:
                structure = json.loads(raw)
            except (ValueError, RecursionError):
                return
            for node in _objects(structure):
                is_video = node.get("@type") == "VideoObject"
                for name in ("sameAs", "embedUrl", "contentUrl"):
                    links = node.get(name)
                    for link in links[:16] if isinstance(links, list) else (links,):
                        if is_video and name != "sameAs":
                            self._video_link(link)
                        else:
                            self._link(link)

    def handle_data(self, value):
        if self.title_parts is not None and value.strip():
            self.title_parts.append(value.strip())
        if self.structured_data is not None:
            self.structured_data = value
        elif self.anchor is not None and value.strip() and len(self.anchor[1]) < 12:
            self.anchor[1].append(value.strip()[:180])


def _https_url(value, base=""):
    if (not isinstance(value, str) or not value.strip() or len(value) > 2048
            or "\\" in value or any(ord(char) < 32 for char in value)):
        return ""
    value = value.strip()
    if not base and re.match(r"^(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,63}(?:/|$)", value):
        value = "https://" + value
    try:
        url = urlsplit(urljoin(base, value))
        if (url.scheme not in ("https", "http") or not url.hostname or url.username is not None
                or url.password is not None or url.port not in (None, 80 if url.scheme == "http" else 443)):
            return ""
        host = url.hostname.rstrip(".").encode("idna").decode("ascii").lower()
        if not re.fullmatch(r"[a-z0-9-]+(?:\.[a-z0-9-]+)+", host):
            return ""
        path = quote(url.path or "/", safe="/%:@!$&'()*+,;=-._~")
        query = quote(url.query, safe="/%:@!$&'()*+,;=?-._~")
        return urlunsplit(("https", host, path, query, ""))
    except (UnicodeError, ValueError):
        return ""


def _host(url):
    return (urlsplit(url).hostname or "").removeprefix("www.")


def _same_site(url, root):
    target, site = _host(url), _host(root)
    return bool(target and site and (target == site or target.endswith("." + site)))


def _website(url):
    link = _https_url(url)
    host = _host(link)
    return link if host and not any(host == item or host.endswith("." + item) for item in _IGNORED_HOSTS) else ""


def _matches(value, names):
    key = title_key(value)
    separated = " " + key + " "
    return any(
        key == name or (len(name) >= 3 and (" " + name + " " in separated or
                                           key.replace(" ", "") == name.replace(" ", "")))
        for name in names
    )


def _read_page(url, deadline, cancel):
    now = time.monotonic()
    with _PAGE_LOCK:
        cached = _PAGE_CACHE.get(url)
        if cached is not None and now - cached[0] < 600:
            _PAGE_CACHE.move_to_end(url)
            return cached[1]
    timeout = min(2.4, _time_left(deadline, cancel))
    try:
        body, headers, final_url = fetch_public_url(
            url, 1024 * 1024, timeout, "text/html,application/xhtml+xml", "Mozilla/5.0",
            max_redirects=2, cancel_event=cancel,
        )
    except (NetworkSecurityError, OSError) as error:
        _record_lookup_timeout(error)
        raise
    final_url = _website(final_url)
    if not final_url or not _same_site(final_url, url):
        return None
    content_type = headers.get("content-type", "")
    if content_type and "html" not in content_type.lower():
        return None
    charset = re.search(r"\bcharset\s*=\s*[\x22']?([\w.-]+)", content_type, re.IGNORECASE)
    try:
        document = body.decode(charset[1] if charset else "utf-8", "replace")
    except LookupError:
        document = body.decode("utf-8", "replace")
    page = _PublisherPage()
    page.feed(document)
    page.close()
    result = _PublisherDocument(final_url, tuple(page.titles[:40]), tuple(page.links), tuple(page.video_links))
    with _PAGE_LOCK:
        _PAGE_CACHE[url] = (time.monotonic(), result)
        _PAGE_CACHE.move_to_end(url)
        while len(_PAGE_CACHE) > 24:
            _PAGE_CACHE.popitem(last=False)
    return result


def _youtube_targets(link, base):
    url = urlsplit(_https_url(link, base))
    if url.hostname not in _YOUTUBE:
        return
    values = parse_qs(url.query, max_num_fields=40)
    playlist_id = values.get("list", ("",))[0]
    if _PLAYLIST_ID.fullmatch(playlist_id):
        yield "playlist", playlist_id
    path = unquote(url.path).rstrip("/")
    channel_path = re.sub(r"/(?:featured|videos|playlists|about)$", "", path)
    if (_CHANNEL_PATH.fullmatch(channel_path) or
            url.hostname not in ("youtu.be", "youtube-nocookie.com", "www.youtube-nocookie.com")
            and channel_path.casefold() not in _RESERVED_PATHS
            and _LEGACY_CHANNEL_PATH.fullmatch(channel_path)):
        yield "channel", quote(channel_path, safe="/@_-.")
    if url.hostname == "youtu.be":
        video_id = path.strip("/")
    elif path.startswith("/embed/"):
        video_id = path.rsplit("/", 1)[1]
    else:
        video_id = values.get("v", ("",))[0]
    if _VIDEO_ID.fullmatch(video_id):
        yield "video", video_id


def _channel_identity(path, deadline, cancel, *, require_badge=False):
    body = _public_page(path + "?hl=en", deadline, cancel, "en-US,en;q=0.9")
    data = _page_object(body, "ytInitialData")
    metadata = _field(data, "metadata", "channelMetadataRenderer")
    identifier, title = _field(metadata, "externalId"), _field(metadata, "title")
    if (isinstance(identifier, str) and _CHANNEL_ID.fullmatch(identifier) and
            isinstance(title, str)):
        if not require_badge or _has_channel_badge(data.get("header"), title):
            return identifier, title
    return "", ""


def verified_youtube_channel(path, names, deadline, cancel):
    identifier, title = _channel_identity(path, deadline, cancel)
    return identifier if _matches(title, names) else ""


def _has_channel_badge(badges, title):
    expected = (title + ", Verified").casefold()
    for node in _objects(badges):
        style = node.get("style") or node.get("badgeStyle")
        label = node.get("label")
        if style == "BADGE_STYLE_TYPE_VERIFIED" or isinstance(label, str) and label.casefold() == expected:
            return True
    return False


def verified_publisher_channel(name, deadline, cancel):
    """Recover an issuer listed in TMDb when its website has no usable channel link."""
    key = title_key(name).replace(" ", "")
    query = urlencode({"search_query": name, "sp": "EgIQAg==", "hl": "en"})
    data = _page_object(_public_page("/results?" + query, deadline, cancel, "en-US,en;q=0.9"), "ytInitialData")
    matched = set()
    for node in _objects(data.get("contents")):
        channel = node.get("channelRenderer")
        title = _title(_field(channel, "title"))
        ident = _field(channel, "channelId")
        if (isinstance(ident, str) and _CHANNEL_ID.fullmatch(ident) and isinstance(title, str)
                and key and title_key(title).replace(" ", "") == key
                and _has_channel_badge(channel.get("ownerBadges"), title)):
            matched.add(ident)
    return next(iter(matched)) if len(matched) == 1 else ""


def _catalog_series_channel(homepage, title_names, deadline, cancel):
    """Verify the channel explicitly linked from the catalog's official series homepage."""
    try:
        links = {path: None for kind, path in _youtube_targets(homepage, "") if kind == "channel"}
        if not links and (site := _website(homepage)):
            document = _read_page(site, deadline, cancel)
            if document is None or not any(_matches(title, title_names) for title in document.titles):
                return ""
            for href, _ in document.links:
                try:
                    links.update((path, None) for kind, path in _youtube_targets(href, document.url) if kind == "channel")
                except ValueError:
                    continue
        names = {name.replace(" ", "") for name in title_names}
        for path in sorted(links, key=lambda value: not value.startswith("/channel/"))[:4]:
            try:
                owner, title = _channel_identity(path, deadline, cancel, require_badge=True)
            except _LOOKUP_ERRORS:
                continue
            primary = re.split(r"\s+[–—-]\s+|\s*[|·]\s*", title, maxsplit=1)[0]
            if (owner and any(title_key(value).replace(" ", "") in names for value in (title, primary))
                    and (not path.startswith("/channel/") or path == "/channel/" + owner)):
                return owner
    except _LOOKUP_ERRORS:
        pass
    return ""


def _watch_details(video_id, deadline, cancel, language="en"):
    if not _VIDEO_ID.fullmatch(video_id):
        return "", {}
    lang = language if _LANGUAGE_TAG.fullmatch(language) else "en"
    accept = "en-US,en;q=0.9" if lang == "en" else lang
    body = _public_page("/watch?" + urlencode({"v": video_id, "hl": lang}), deadline, cancel, accept)
    payload = _page_object(body, "ytInitialPlayerResponse")
    detail = payload.get("videoDetails")
    if (_field(payload, "playabilityStatus", "status") != "OK" or _field(detail, "videoId") != video_id
            or _field(detail, "isLiveContent") is True):
        return "", {}
    return body, detail


def _linked_channels(body, description):
    try:
        data = _page_object(body, "ytInitialData")
    except LiveLookupError:
        data = {}
    primary = _field(data, "contents", "twoColumnWatchNextResults", "results", "results", "contents") or ()
    # The publisher controls the video's title and description. Other panels
    # can contain unrelated channels, suggested videos and viewer comments.
    fields = []
    for item in primary:
        fields.extend((_field(item, "videoPrimaryInfoRenderer", "title"),
                       _field(item, "videoSecondaryInfoRenderer", "description"),
                       _field(item, "videoSecondaryInfoRenderer", "attributedDescription")))
    for node in _objects(data.get("engagementPanels")):
        items = _field(node, "structuredDescriptionContentRenderer", "items") or ()
        for item in items:
            fields.extend((_field(item, "videoTitleHeaderViewModel", "videoTitle"),
                           _field(item, "expandableVideoDescriptionBodyRenderer", "attributedDescriptionBodyText")))
    for node in _objects(fields):
        ident = _field(node, "browseEndpoint", "browseId")
        if isinstance(ident, str) and _CHANNEL_ID.fullmatch(ident):
            yield "/channel/" + ident
    if isinstance(description, str):
        for url in _DESCRIPTION_LINK.findall(description[:8192])[:16]:
            try:
                for kind, path in _youtube_targets(url.rstrip(".,!?;)]}"), ""):
                    if kind == "channel":
                        yield path
            except ValueError:
                continue


def publisher_series_channel(video_id, publisher, aliases, deadline, cancel):
    """Verify a channel that an identified publisher links in its own video."""
    body, video = _watch_details(video_id, deadline, cancel)
    title = video.get("title")
    names = tuple(title_key(name) for name in aliases)
    if video.get("channelId") != publisher or not isinstance(title, str) or not _matches(title, names):
        return ""
    wanted = {name.replace(" ", "") for name in names if name}
    links = dict.fromkeys(_linked_channels(body, video.get("shortDescription")))
    links.pop("/channel/" + publisher, None)
    for path in tuple(links)[:4]:
        try:
            owner, channel_title = _channel_identity(path, deadline, cancel)
        except _LOOKUP_ERRORS:
            continue
        if (owner and owner != publisher and title_key(channel_title).replace(" ", "") in wanted
                and (not path.startswith("/channel/") or path == "/channel/" + owner)):
            return owner
    return ""


def canonical_playlist_episode(owner, row, candidate, details, deadline, cancel):
    """Recheck a localized playlist card against the same video on its owner's channel."""
    video_id = row.episode.video_id
    _, video = _watch_details(video_id, deadline, cancel, details.original_language)
    title, length = video.get("title"), video.get("lengthSeconds")
    if (video.get("channelId") != owner or not isinstance(title, str) or
            not isinstance(length, (str, int)) or not re.fullmatch(r"[0-9]{1,6}", str(length))):
        return None
    position = episode_position(title)
    displayed = episode_position(row.episode.title)
    if not position[1] or (displayed[1] and not same_episode_position(displayed, position)):
        return None
    episode = _episode(video_id, title)
    if episode is None:
        return None
    verified = _PlaylistEntry(episode, int(length), owner)
    accepted = full_episodes(_PlaylistPage((verified,), False, owner), details.minimum_seconds,
                             numbered=True, aliases=candidate.names)
    return accepted[0] if accepted else None


def _official_video(video_id, title_names, source_names, deadline, cancel, *, publisher_title=""):
    _, detail = _watch_details(video_id, deadline, cancel)
    author, channel = _field(detail, "author"), _field(detail, "channelId")
    title, length = _field(detail, "title"), _field(detail, "lengthSeconds")
    if (not isinstance(channel, str) or not _CHANNEL_ID.fullmatch(channel)
            or not isinstance(author, str) or not _matches(author, source_names + title_names)
            or not isinstance(title, str) or not title.strip()):
        return None
    if not isinstance(length, (str, int)) or not re.fullmatch(r"[0-9]{1,6}", str(length)):
        return None
    if publisher_title:
        expected = named_episode_position(publisher_title, title_names)
        actual = episode_position(title)
        if not expected or is_excerpt(title) or (actual[1] and not same_episode_position(actual, expected)):
            return None
        if not actual[1] or not _matches(title, title_names):
            title = publisher_title
    elif not _matches(title, title_names):
        return None
    episode = _episode(video_id, title)
    return (channel, _PlaylistEntry(episode, int(length))) if episode is not None else None


def _episode_navigation(document, title_names):
    pages = {}
    archive = ""
    for href, label in document.links:
        link = _website(_https_url(href, document.url))
        if not link or not _same_site(link, document.url):
            continue
        path = unquote(urlsplit(link).path).rstrip("/")
        if not path or is_excerpt(path):
            continue
        parts = tuple(title_key(part) for part in path.split("/") if part)
        if _NON_EPISODE_PATHS.intersection(parts):
            continue
        named = named_episode_position(label, title_names)
        if not named and not _matches(path, title_names):
            continue
        leaf = parts[-1] if parts else ""
        position = named or episode_position(leaf)
        if not position[1] and not is_excerpt(label):
            position = episode_position(title_key(label), at_start=True)
        if position[1]:
            pages.setdefault(link, PublisherEpisodePage(link, position))
        elif not archive and (leaf in _EPISODE_ARCHIVES or title_key(label) in _EPISODE_ARCHIVES):
            archive = link
    return tuple(pages.values()), archive


def _archive_pages(document):
    root = urlsplit(document.url).path.rstrip("/")
    pages = {}
    for href, _ in document.links:
        link = _website(_https_url(href, document.url))
        if not link or link == document.url or not _same_site(link, document.url):
            continue
        parts = urlsplit(link)
        path = parts.path.rstrip("/")
        if path.startswith(root + "/"):
            number = path[len(root) + 1:]
        elif path == root and parts.query:
            try:
                number = parse_qs(parts.query, max_num_fields=24).get("page", ("",))[0]
            except ValueError:
                continue
        else:
            continue
        if re.fullmatch(r"[1-9][0-9]{0,3}", number):
            pages.setdefault(int(number), link)
    return tuple(pages[number] for number in sorted(pages, reverse=True)[:3])


def publisher_episode_pages(sources, aliases, deadline, cancel, missing=()):
    """Read episode references from a short, explicitly linked publisher archive."""
    pages = {page.url: page for page in sources.episode_pages}
    title_names = tuple(title_key(title) for title in aliases)

    def add(url):
        try:
            document = _read_page(url, deadline, cancel)
            if document is not None:
                references, _ = _episode_navigation(document, title_names)
                pages.update((page.url, page) for page in references)
            return document
        except _LOOKUP_ERRORS:
            return None

    def covers(position, page):
        return position[1] == page.position[1] and (not position[0] or position[0] == page.position[0])

    def enough():
        if not missing:
            return bool(pages)
        return all(any(covers(position, page) for page in pages.values()) for position in missing)

    for archive in sources.episode_archives:
        if enough() or deadline - time.monotonic() < .4 or (cancel is not None and cancel.is_set()):
            break
        document = add(archive)
        if document is None:
            continue
        for url in _archive_pages(document):
            if enough() or deadline - time.monotonic() < .8 or (cancel is not None and cancel.is_set()):
                break
            add(url)
    return tuple(page for page in pages.values() if not missing or any(covers(position, page) for position in missing))


def publisher_episode_video(page, aliases, info, year, deadline, cancel):
    """Match an episode's publisher page and its linked YouTube video by exact ID."""
    try:
        _time_left(deadline, cancel)
        document = _read_page(page.url, deadline, cancel)
    except _LOOKUP_ERRORS:
        return None
    if document is None:
        return None
    title = next((label for label in document.titles
                  if (position := named_episode_position(label, aliases, year))
                  and same_episode_position(position, page.position)), "")
    if not title:
        return None
    links = list(document.video_links)
    links.extend(href for href, label in document.links
                 if (position := named_episode_position(label, aliases, year))
                 and same_episode_position(position, page.position))
    ids = {}
    for link in links:
        try:
            ids.update((identifier, None) for kind, identifier in _youtube_targets(link, document.url) if kind == "video")
        except ValueError:
            continue
    title_names = tuple(title_key(name) for name in aliases)
    source_names = tuple(title_key(name) for name in info.names)
    for video_id in tuple(ids)[:3]:
        try:
            verified = _official_video(video_id, title_names, source_names, deadline, cancel, publisher_title=title)
        except _LOOKUP_ERRORS:
            continue
        if verified is not None:
            owner, entry = verified
            if full_episodes(_PlaylistPage((entry,), False, owner), info.minimum_seconds, numbered=True, aliases=aliases):
                return verified
    return None


def _linked_series_site(target, homepage, label, title_names, show_page):
    if homepage and _same_site(target, homepage):
        return show_page or _matches(label, title_names)
    if title_key(label) in title_names:
        return True
    brand = title_key(_host(target).split(".", 1)[0]).replace(" ", "")
    return show_page and any(len(key) >= 4 and brand.startswith(key.replace(" ", "")) for key in title_names)


def publisher_youtube_sources(info, aliases, deadline, cancel, series_year=None):
    sites = tuple(dict.fromkeys(link for site in info.sites if (link := _website(site.homepage))))
    title_names = tuple(dict.fromkeys(key for name in aliases if (key := title_key(name))))
    source_names = tuple(dict.fromkeys(key for name in info.names if (key := title_key(name))))
    homepage = _website(info.homepage)
    home = homepage if homepage and any(_same_site(homepage, site) for site in sites) else ""
    catalog_owner = ""
    if series_year is not None and info.homepage and not home:
        end = min(deadline, time.monotonic() + 13.0)
        catalog_owner = _catalog_series_channel(info.homepage, title_names, end, cancel)
    if not sites:
        return PublisherYouTube((), (), (), series_channel_ids=(catalog_owner,) if catalog_owner else ())
    pages = list(dict.fromkeys(([home] if home else []) + list(sites)))
    links = {}
    episode_pages, episode_archives = {}, {}
    visited = set()
    linked_sites = set()
    page_limit = max(4, len(sites) + 3) if series_year is not None else 4
    while pages and len(visited) < page_limit and deadline - time.monotonic() >= .4:
        url = pages.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            parsed = _read_page(url, deadline, cancel)
        except _LOOKUP_ERRORS:
            continue
        if parsed is None:
            continue
        base, titles, hrefs, _ = parsed
        show_page = any(_matches(title, title_names) for title in titles)
        if url in linked_sites and not show_page:
            continue
        if series_year is not None:
            references, archive = _episode_navigation(parsed, title_names)
            episode_pages.update((page.url, page) for page in references)
            if archive:
                episode_archives[archive] = None
        show_link = directory_link = series_link = ""
        for href, label in hrefs:
            named_show = bool(label) and _matches(label, title_names)
            priority = 2 if named_show else int(show_page)
            try:
                for kind, ident in _youtube_targets(href, base):
                    if priority or kind == "channel":
                        links[kind, ident] = max(priority, links.get((kind, ident), 0))
            except ValueError:
                continue
            scan_linked = series_year is not None and not linked_sites and not series_link
            if not show_link or not directory_link or scan_linked:
                then = _website(_https_url(href, base))
                if not then or then in visited:
                    continue
                if _same_site(then, base):
                    path = unquote(urlsplit(then).path)
                    if not show_link and not show_page and _matches(path, title_names):
                        show_link = then
                    if not directory_link and "youtube" in title_key(path).split():
                        directory_link = then
                elif scan_linked and _linked_series_site(then, homepage, label, title_names, show_page):
                    series_link = then
        if series_link:
            linked_sites.add(series_link)
        pages[:0] = [link for link in (series_link, directory_link, show_link) if link]
        if (any(kind == "playlist" or (kind == "channel" and priority == 2)
                for (kind, _), priority in links.items())
                or episode_pages and deadline - time.monotonic() < 5.0):
            break

    ranked = sorted(links, key=links.get, reverse=True)
    playlists = tuple(ident for kind, ident in ranked if kind == "playlist")[:2]
    channels, episodes = [], []
    sources = PublisherYouTube((), (), (), tuple(episode_pages.values()), tuple(episode_archives)[:2],
                               (catalog_owner,) if catalog_owner else ())
    if series_year is not None:
        references = publisher_episode_pages(sources, aliases, deadline, cancel)
        for page in sorted(references, key=lambda item: item.position, reverse=True)[:3]:
            if deadline - time.monotonic() < .4 or (cancel is not None and cancel.is_set()):
                break
            row = publisher_episode_video(page, aliases, info, series_year, deadline, cancel)
            if row is not None:
                episodes.append(row)
                break
        sources = sources._replace(episode_pages=references)
    channel_attempts = video_attempts = 0
    for kind, ident in ranked:
        if deadline - time.monotonic() < .4 or (cancel is not None and cancel.is_set()):
            break
        try:
            if kind == "video" and not playlists and video_attempts < 2:
                video_attempts += 1
                row = _official_video(ident, title_names, source_names, deadline, cancel)
                if row is not None:
                    episodes.append(row)
            elif kind == "channel" and channel_attempts < 3:
                channel_attempts += 1
                names = source_names + title_names if links[kind, ident] else source_names
                inspected = verified_youtube_channel(ident, names, deadline, cancel)
                if inspected and inspected not in channels:
                    channels.append(inspected)
        except _LOOKUP_ERRORS:
            continue
    return sources._replace(channel_ids=tuple(channels), playlist_ids=playlists, episodes=tuple(episodes))

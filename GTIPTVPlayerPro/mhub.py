# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native live playlists from user-configured mHub Addon Protocol v2 sources."""

import hashlib
import json
import math
import threading
import time
from collections import OrderedDict, deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from fnmatch import fnmatchcase
from urllib.parse import quote, urlencode, urljoin, urlsplit, urlunsplit

from . import PLUGIN_VERSION
from .content import (
    ContentCategory,
    ContentError,
    ContentItem,
    EPGEvent,
    _bounded_provider_integer,
    _validate_provider_json_depth,
)
from .i18n import N_, _, device_language
from .network_security import (
    NetworkSecurityError,
    fetch_public_url,
    resolve_public_addresses,
    validate_public_url_syntax,
)


MANIFEST_FILE = "mhub-addon.json"
DEFAULT_TIMEOUT = 12
MAX_JSON_BYTES = 4 * 1024 * 1024
MAX_PROVIDER_ENTRIES = 8
MAX_CATEGORIES = 128
MAX_ITEMS_PER_PAGE = 8192
MAX_CATALOG_ITEMS = 20000
MAX_CATALOG_PAGES = 128
MAX_CATALOG_BYTES = 12 * 1024 * 1024
MAX_OPEN_CATEGORIES = 4
INITIAL_ITEMS = 36
MORE_ITEMS = 60


def _text(value, maximum=180):
    if not isinstance(value, str):
        return ""
    return "".join(
        " " if ord(character) < 32 or 127 <= ord(character) <= 159 else
        "\ufffd" if 0xD800 <= ord(character) <= 0xDFFF else character
        for character in value[:maximum]
    ).strip()


def _identifier(value, maximum=256):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if len(value) > maximum or any(ord(character) < 32 or 0xD800 <= ord(character) <= 0xDFFF
                                   for character in value):
        return ""
    return "" if value in (".", "..") else value


def _list(value):
    return value if isinstance(value, list) else []


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _public_url(value):
    if not isinstance(value, str) or len(value) > 2048 or any(
        ord(character) <= 32 or ord(character) >= 127 or character in "\\|"
        for character in value
    ):
        raise NetworkSecurityError()
    result = validate_public_url_syntax(value)
    if "%" in urlsplit(result["url"]).netloc:
        raise NetworkSecurityError()
    return result


def _asset_url(base, value):
    if not isinstance(value, str) or not value or len(value) > 2048:
        return ""
    try:
        return _public_url(urljoin(base, value))["url"]
    except (NetworkSecurityError, ValueError):
        return ""


def _stable_id(*parts):
    data = json.dumps(parts, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(data.encode("ascii")).hexdigest()[:32]


def _playback_error():
    return ContentError(N_("Could not create the stream URL"), ContentError.PLAYBACK_URL)


def _access_error(status=None):
    return ContentError(N_("Server denied access to content"), ContentError.ACCESS_DENIED, status)


def _response_error():
    return ContentError(N_("Server returned an invalid content response"), ContentError.INVALID_RESPONSE)


def _unsupported_stream(value):
    return (
        value.get("headers") not in (None, {}) or bool(value.get("drm"))
        or bool(value.get("licenseUrl") or value.get("licenseServer"))
    )


@dataclass(frozen=True)
class _Provider:
    manifest_url: str
    catalogues: tuple
    resources: frozenset
    prefixes: tuple
    patterns: tuple

    def url(self, path, parameters):
        manifest = urlsplit(self.manifest_url)
        base = manifest.path.rsplit("/", 1)[0]
        return urlunsplit((manifest.scheme, manifest.netloc, base + "/" + path, urlencode(parameters), ""))


@dataclass(frozen=True)
class _Category:
    provider: _Provider
    catalogue_id: str
    name: str
    filters: tuple = ()

    @property
    def key(self):
        return _stable_id(self.provider.manifest_url, self.catalogue_id, self.filters)


@dataclass
class _Page:
    values: list = field(default_factory=list)
    pending: deque = field(default_factory=deque)
    seen_ids: set = field(default_factory=set)
    seen_cursors: set = field(default_factory=set)
    epg: dict = field(default_factory=dict)
    next_cursor: str = ""
    page_count: int = 0
    bytes_loaded: int = 0
    end: bool = False


class MHubContentClient:
    source_type = "mhub"
    source_label = "mHub"
    capabilities = frozenset(("live", "epg"))
    async_playback_resolution = True

    def __init__(self, account, timeout=DEFAULT_TIMEOUT, fetcher=None, resolver=None):
        self.account = account
        try:
            self._manifest_url = _public_url(account.location)["url"]
        except (AttributeError, NetworkSecurityError) as error:
            raise _response_error() from error
        self.timeout = float(timeout)
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError(N_("Request timed out"))
        self._fetcher = fetcher or fetch_public_url
        self._resolver = resolver
        self._local = threading.local()
        self._catalogue_lock = threading.Lock()
        self._page_lock = threading.Lock()
        self._state_lock = threading.Lock()
        self._providers = ()
        self._categories = None
        self._first_pages = {}
        self._pages = OrderedDict()

    def supports(self, capability):
        return str(capability or "").strip().lower() in self.capabilities

    @contextmanager
    def request_scope(self, cancel_event=None, deadline=None, timeout=None):
        previous = getattr(self._local, "scope", None)
        if cancel_event is None:
            cancel_event = previous[0] if previous else threading.Event()
        try:
            scope_timeout = self.timeout if timeout is None else min(float(timeout), self.timeout)
            scope_deadline = time.monotonic() + scope_timeout
            if deadline is not None:
                scope_deadline = min(float(deadline), scope_deadline)
            if not math.isfinite(scope_deadline):
                raise ValueError()
        except (TypeError, ValueError, OverflowError) as error:
            raise ContentError(N_("Request timed out"), ContentError.DEADLINE) from error
        if previous:
            scope_deadline = min(scope_deadline, previous[1])
        self._local.scope = cancel_event, scope_deadline
        try:
            self._check_scope()
            yield self
        finally:
            self._local.scope = previous

    def _check_scope(self):
        event, deadline = self._local.scope
        if event.is_set():
            raise ContentError(N_("Request cancelled"), ContentError.CANCELLED)
        if deadline <= time.monotonic():
            raise ContentError(N_("Request timed out"), ContentError.DEADLINE)
        return event, deadline

    @contextmanager
    def _locked(self, lock):
        while True:
            unused_event, deadline = self._check_scope()
            if lock.acquire(timeout=max(0, min(0.05, deadline - time.monotonic()))):
                break
        try:
            self._check_scope()
            yield
        finally:
            lock.release()

    @staticmethod
    def _verify_type(content_type):
        if content_type != "live":
            raise ContentError(N_("Unsupported content type"), ContentError.UNSUPPORTED_TYPE)

    def _request_json(self, url):
        event, deadline = self._check_scope()
        try:
            url = _public_url(url)["url"]
            body, unused_headers, final_url = self._fetcher(
                url,
                maximum=MAX_JSON_BYTES,
                timeout=max(0.001, deadline - time.monotonic()),
                accept="application/json",
                user_agent="GT-IPTV-Player-Pro/{}".format(PLUGIN_VERSION),
                resolver=self._resolver,
                cancel_event=event,
            )
            final_url = _public_url(final_url)["url"]
        except (NetworkSecurityError, OSError) as error:
            self._check_scope()
            status = getattr(error, "status", getattr(error, "code", None))
            if status in (401, 403, 428):
                raise _access_error(status) from error
            if status == 429:
                raise ContentError(
                    N_("Server request limit reached"), ContentError.RATE_LIMITED, status
                ) from error
            if status is not None:
                raise ContentError(
                    _(N_("Server returned HTTP {}")).format(status), ContentError.HTTP_ERROR, status
                ) from error
            raise ContentError(N_("Could not connect to the content server"), ContentError.CONNECTION) from error
        if not isinstance(body, (bytes, str)) or len(body) > MAX_JSON_BYTES:
            raise ContentError(N_("Server content list is too large"), ContentError.RESPONSE_TOO_LARGE)
        try:
            payload = json.loads(body, parse_int=_bounded_provider_integer)
            _validate_provider_json_depth(payload)
        except (TypeError, ValueError, RecursionError) as error:
            raise _response_error() from error
        self._check_scope()
        if not isinstance(payload, dict):
            raise _response_error()
        if "clientFetch" in payload or payload.get("error") in (
            "identity_required", "entitlement_required", "config_required"
        ):
            raise _access_error()
        return payload, final_url, len(body)

    @staticmethod
    def _provider(payload, manifest_url):
        if payload.get("specVersion") != 2 or not _identifier(payload.get("id")) or not _text(payload.get("name")):
            raise _response_error()
        if payload.get("identity") == "required":
            raise _access_error()
        resources = frozenset(
            value for value in _list(payload.get("resources"))
            if value in ("catalog", "item", "source", "resolve")
        )
        catalogues = ()
        if "catalog" in resources and "live" in _list(payload.get("types")):
            catalogues = tuple(
                entry for entry in _list(payload.get("catalogs"))[:MAX_CATEGORIES]
                if isinstance(entry, dict) and entry.get("type") == "live"
                and _identifier(entry.get("id")) and _text(entry.get("name"))
            )
        prefixes = tuple(
            value for value in (_identifier(entry, 64) for entry in _list(payload.get("idPrefixes"))[:32])
            if value and ":" not in value
        )
        patterns = tuple(
            value for value in (_identifier(entry, 512) for entry in _list(payload.get("urlPatterns"))[:32])
            if value
        )
        return _Provider(manifest_url, catalogues, resources, prefixes, patterns)

    def _language_parameters(self):
        return {"language": device_language().replace("_", "-")}

    def _manifest(self, url):
        split = urlsplit(url)
        parts = [split.query, urlencode(self._language_parameters())]
        request_url = urlunsplit((split.scheme, split.netloc, split.path, "&".join(filter(None, parts)), ""))
        payload, final_url, unused_size = self._request_json(request_url)
        final = urlsplit(final_url)
        # Redirects may serve the manifest under another file name. All
        # resource requests are relative to the final manifest's directory.
        return payload, urlunsplit((final.scheme, final.netloc, final.path, "", ""))

    def _load_providers(self):
        payload, manifest_url = self._manifest(self._manifest_url)
        if "addons" not in payload:
            return (self._provider(payload, manifest_url),)
        if payload.get("specVersion") != 2 or not isinstance(payload["addons"], list):
            raise _response_error()
        providers = []
        visited = {manifest_url}
        first_error = None
        enabled = [entry for entry in payload["addons"][:MAX_CATEGORIES]
                   if isinstance(entry, dict) and entry.get("enabled") is True]
        for entry in enabled[:MAX_PROVIDER_ENTRIES]:
            base = _text(entry.get("url"), 2048)
            if not base:
                continue
            try:
                child = urlsplit(urljoin(manifest_url, base))
            except ValueError:
                continue
            path = child.path.rstrip("/")
            if not path.endswith("/" + MANIFEST_FILE):
                path += "/" + MANIFEST_FILE
            url = urlunsplit((child.scheme, child.netloc, path, child.query, child.fragment))
            if url in visited:
                continue
            visited.add(url)
            try:
                result, final_url = self._manifest(url)
                if "addons" not in result:
                    providers.append(self._provider(result, final_url))
            except ContentError as error:
                if error.reason in (ContentError.CANCELLED, ContentError.DEADLINE):
                    raise
                if first_error is None:
                    first_error = error
        if not any(provider.catalogues for provider in providers) and first_error is not None:
            raise first_error
        return tuple(providers)

    @staticmethod
    def _filter_categories(category, features):
        for option in _list(_mapping(features).get("filter"))[:16]:
            option = _mapping(option)
            key = _identifier(option.get("id"), 64)
            if key.lower() not in ("country", "countries", "group"):
                continue
            for value in _list(option.get("values"))[:MAX_CATEGORIES]:
                value = _mapping(value)
                name = _text(value.get("name"))
                code = _identifier(value.get("id"), 128)
                if code and name:
                    yield _Category(category.provider, category.catalogue_id, name, ((key, code),))

    def load_categories(self, content_type):
        self._verify_type(content_type)
        with self.request_scope(), self._locked(self._catalogue_lock):
            if self._categories is None:
                providers = self._load_providers()
                catalogues = [(provider, entry) for provider in providers for entry in provider.catalogues][:MAX_CATEGORIES]
                categories = OrderedDict()
                first_pages = {}
                filters = []
                for provider, entry in catalogues:
                    category = _Category(provider, _identifier(entry["id"]), _text(entry["name"]))
                    categories[category.key] = category
                    features = entry.get("features")
                    declared_filters = list(self._filter_categories(category, features))
                    if len(catalogues) == 1 and not declared_filters:
                        first_page = self._fetch_page(category, "")
                        first_pages[category.key] = first_page
                        declared_filters = self._filter_categories(category, first_page[0].get("features"))
                    filters.extend(declared_filters)
                for filtered in filters:
                    if len(categories) >= MAX_CATEGORIES:
                        break
                    categories[filtered.key] = filtered
                self._check_scope()
                self._providers = providers
                self._first_pages = first_pages
                self._categories = categories
            return [ContentCategory(category.key, category.name) for category in self._categories.values()]

    def _fetch_page(self, category, cursor):
        parameters = self._language_parameters()
        if cursor:
            parameters["cursor"] = cursor
        for name, value in category.filters:
            parameters["filter[{}]".format(name)] = value
        url = category.provider.url("catalog/live/{}.json".format(quote(category.catalogue_id, safe="")), parameters)
        payload, unused_url, size = self._request_json(url)
        if not isinstance(payload.get("items"), list):
            raise _response_error()
        if len(payload["items"]) > MAX_ITEMS_PER_PAGE:
            raise ContentError(N_("Server content list is too large"), ContentError.RESPONSE_TOO_LARGE)
        return payload, size

    def _get_category(self, category_id):
        if self._categories is None:
            self.load_categories("live")
        if not category_id:
            return next(iter(self._categories.values()), None)
        return self._categories.get(str(category_id))

    def _fetch_next_page(self, category, page):
        first_page = self._first_pages.get(category.key) if page.page_count == 0 else None
        payload, size = first_page or self._fetch_page(category, page.next_cursor)
        if page.bytes_loaded + size > MAX_CATALOG_BYTES:
            with self._state_lock:
                page.end = True
            return
        incoming = []
        incoming_ids = set()
        remaining = MAX_CATALOG_ITEMS - len(page.seen_ids)
        for index, item in enumerate(payload["items"]):
            if index % 64 == 0:
                self._check_scope()
            item = _mapping(item)
            item_id = _identifier(item.get("id"))
            if not item_id or item.get("type") != "live" or item.get("directory"):
                continue
            if item_id in page.seen_ids or item_id in incoming_ids:
                continue
            incoming_ids.add(item_id)
            incoming.append(item)
            if len(incoming) >= remaining:
                break
        cursor = _identifier(payload.get("nextCursor"), 512)
        self._check_scope()
        # This method runs under the pagination lock. Only the small cursor
        # and completion snapshot are also read by the player UI thread.
        with self._state_lock:
            self._first_pages.pop(category.key, None)
            page.seen_ids.update(incoming_ids)
            page.pending.extend(incoming)
            page.page_count += 1
            page.bytes_loaded += size
            page.end = (
                not cursor or cursor in page.seen_cursors
                or page.page_count >= MAX_CATALOG_PAGES or len(page.seen_ids) >= MAX_CATALOG_ITEMS
            )
            page.next_cursor = cursor
            if not page.end:
                page.seen_cursors.add(cursor)

    def _item(self, category, raw):
        stream_id = _stable_id(category.provider.manifest_url, raw["id"].strip())
        images = _mapping(raw.get("images"))
        base = category.provider.manifest_url
        logo = _asset_url(base, images.get("logo")) or _asset_url(base, images.get("poster"))
        item = ContentItem("live", stream_id, _text(raw.get("name")), category.key, icon=logo)
        item._mhub_owner = self
        item._mhub_provider = category.provider
        item._mhub_item = raw
        return item

    def _fill_page(self, category, page, limit):
        attempts = 0
        while not page.pending and not page.end and attempts < 4:
            self._fetch_next_page(category, page)
            attempts += 1
        self._check_scope()
        with self._state_lock:
            added = [self._item(category, page.pending.popleft()) for _ in range(min(limit, len(page.pending)))]
            page.values.extend(added)
            page.epg.update((item.stream_id, item._mhub_item["epg"]) for item in added
                            if isinstance(item._mhub_item.get("epg"), list))
        return added

    def load_items(self, content_type, category_id=""):
        self._verify_type(content_type)
        with self.request_scope(), self._locked(self._page_lock):
            category = self._get_category(category_id)
            if category is None:
                return []
            key = category.key
            page = self._pages.get(key)
            if page is None:
                page = _Page()
                self._fill_page(category, page, INITIAL_ITEMS)
                with self._state_lock:
                    self._pages[key] = page
                    while len(self._pages) > MAX_OPEN_CATEGORIES:
                        self._pages.popitem(last=False)
            else:
                with self._state_lock:
                    self._pages.move_to_end(key)
            return list(page.values)

    def load_more_items(self, content_type, category_id=""):
        self._verify_type(content_type)
        with self.request_scope():
            self.load_items(content_type, category_id)
            with self._locked(self._page_lock):
                category = self._get_category(category_id)
                page = self._pages.get(category.key) if category else None
                return self._fill_page(category, page, MORE_ITEMS) if page is not None else []

    def has_more_items(self, content_type, category_id=""):
        self._verify_type(content_type)
        categories = self._categories
        if not categories:
            return False
        key = str(category_id or "") or next(iter(categories))
        with self._state_lock:
            page = self._pages.get(key)
            return bool(page and (page.pending or not page.end))

    def _playable_url(self, value):
        event, deadline = self._check_scope()
        try:
            info = _public_url(value)
            resolve_public_addresses(
                info["hostname"], info["port"], resolver=self._resolver, deadline=deadline, cancel_event=event
            )
        except (NetworkSecurityError, OSError) as error:
            self._check_scope()
            raise _playback_error() from error
        return info["url"]

    def _get_optional(self, provider, path, extra=None):
        parameters = self._language_parameters()
        parameters.update(extra or {})
        try:
            payload, unused_url, unused_size = self._request_json(provider.url(path, parameters))
        except ContentError as error:
            if error.http_status == 404:
                return {}
            raise
        return payload

    @staticmethod
    def _item_sources(payload):
        sources = _list(payload.get("sources"))[:16]
        if payload.get("url"):
            sources = sources + [payload]
        return sources

    def _play_sources(self, providers, sources, seen):
        for candidate in sources:
            candidate = _mapping(candidate)
            value = candidate.get("url")
            if not isinstance(value, str) or candidate.get("kind", "stream") != "stream":
                continue
            if _unsupported_stream(candidate):
                # The native Enigma2 engine owns its HTTP requests and may
                # redirect them. It cannot promise origin-scoped auth headers.
                continue
            if value in seen:
                continue
            seen.add(value)
            try:
                source_url = self._playable_url(value)
                resolvers = [provider for provider in providers if "resolve" in provider.resources
                             and any(fnmatchcase(source_url, pattern) for pattern in provider.patterns)]
                if not resolvers:
                    return source_url
                for provider in resolvers:
                    resolved = self._get_optional(provider, "resolve", {"url": source_url})
                    if resolved.get("url") and not _unsupported_stream(resolved):
                        return self._playable_url(resolved["url"])
            except ContentError as error:
                if error.reason != ContentError.PLAYBACK_URL:
                    raise
        return ""

    def playback_url(self, item):
        if not isinstance(item, ContentItem) or item.content_type != "live" or getattr(item, "_mhub_owner", None) is not self:
            raise ContentError(N_("Invalid playback item"), ContentError.INVALID_PLAYBACK_ITEM)
        with self.request_scope():
            raw = item._mhub_item
            provider = item._mhub_provider
            providers = self._providers
            seen = set()
            url = self._play_sources(providers, self._item_sources(raw), seen)
            if url:
                return url
            identifiers = dict(_mapping(raw.get("ids")))
            if "item" in provider.resources:
                path = "item/live/{}.json".format(quote(raw["id"].strip(), safe=""))
                details = self._get_optional(provider, path)
                url = self._play_sources(providers, self._item_sources(details), seen)
                if url:
                    return url
                identifiers.update(_mapping(details.get("ids")))
            for candidate in providers:
                if "source" not in candidate.resources:
                    continue
                for prefix in candidate.prefixes:
                    identifier = _identifier(identifiers.get(prefix))
                    if not identifier:
                        continue
                    path = "source/live/{}.json".format(quote("{}:{}".format(prefix, identifier), safe=":"))
                    result = self._get_optional(candidate, path)
                    url = self._play_sources(providers, self._item_sources(result), seen)
                    if url:
                        return url
            raise _playback_error()

    def refresh_playback_url(self, item, cancel_event=None, deadline=None):
        with self.request_scope(cancel_event, deadline):
            return self.playback_url(item)

    def load_epg(self, stream_id, limit=2, cache_only=False):
        with self._state_lock:
            raw = next((page.epg[stream_id] for page in self._pages.values() if stream_id in page.epg), [])
        try:
            limit = max(0, min(64, int(limit)))
        except (TypeError, ValueError, OverflowError):
            return []
        events = []
        now = time.time()
        for row in raw[:64]:
            row = _mapping(row)
            timestamps = []
            for key in ("start", "end"):
                try:
                    stamp = datetime.fromisoformat(_text(row.get(key), 64).replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        break
                    timestamps.append(int(stamp.timestamp()))
                except (ValueError, OverflowError, OSError):
                    break
            if len(timestamps) != 2:
                continue
            start, end = timestamps
            if 0 < start < end and end > now:
                events.append(EPGEvent(_text(row.get("title")), start, end, start <= now,
                                       _text(row.get("description"), 1000)))
        return sorted(events, key=lambda event: event.start_timestamp)[:limit]

    load_short_epg = load_epg

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cache-first artwork selection for one film or series."""


def artwork_urls(values):
    from .browser_common import _safe_picon_url

    return tuple(dict.fromkeys(filter(None, map(_safe_picon_url, values))))


def backdrop_urls(item, info, preferred=""):
    return artwork_urls((preferred, getattr(info, "fallback_backdrop", ""),
                         getattr(info, "backdrop", ""), getattr(item, "backdrop", "")))


def poster_urls(item, info, preferred=""):
    return artwork_urls((preferred, getattr(info, "cover", ""), getattr(item, "icon", ""),
                         *(getattr(info, "cover_urls", ()) or ())))[:4]


def provider_backdrop_loader(provider, item, info):
    if getattr(info, "_provider_backdrop_checked", False) or backdrop_urls(item, info):
        return None
    method = "load_series_summary" if item.content_type == "series" else "load_vod_info"
    loader = getattr(provider, method, None)
    return loader if callable(loader) else None


def cached_artwork(urls, validate, token=None):
    from .browser_common import _cached_picon_path

    for url in urls:
        if token is not None:
            token.check()
        path = _cached_picon_path(url)
        if path and validate(path):
            return url, path
    return None


def cached_backdrop(urls, token=None):
    from .browser_common import _valid_cinematic_backdrop_file

    return cached_artwork(urls, _valid_cinematic_backdrop_file, token)


def cached_preview_backdrop(urls, token=None):
    from .artwork import tmdb_poster_url

    return cached_backdrop(artwork_urls(tuple(urls) + tuple(tmdb_poster_url(url, 780) for url in urls)), token)


def cached_poster(urls, token=None, width=342):
    from .artwork import tmdb_poster_url
    from .browser_common import _cinematic_cover_ratio

    sizes = (width, 342) if width != 342 else (342,)
    variants = artwork_urls(tuple(urls) + tuple(tmdb_poster_url(url, size) for size in sizes for url in urls))
    return cached_artwork(variants, _cinematic_cover_ratio, token)


def download_artwork(provider, url, token, timeout=3, priority=True):
    from .browser_common import _WorkerCancelled, _download_client_image, _picon_cache_path

    try:
        return _download_client_image(provider, url, _picon_cache_path(url),
                                      timeout=timeout, token=token, priority=priority)
    except _WorkerCancelled:
        raise
    except Exception:
        token.check()
        return ""


def download_backdrop(provider, urls, token):
    from .browser_common import _valid_cinematic_backdrop_file

    for url in urls:
        token.check()
        path = download_artwork(provider, url, token)
        if path and _valid_cinematic_backdrop_file(path):
            return url, path
    return None


def load_preview_backdrop(provider, metadata, kind, item, info, urls, token):
    """Find a selected title's wide image without fetching its summary."""
    from .artwork import tmdb_poster_url
    from .browser_common import _WorkerCancelled, _WorkerToken, _bounded_client_call

    hit = cached_preview_backdrop(urls, token)
    if hit is not None:
        return hit
    sized = artwork_urls(tmdb_poster_url(url, 780) for url in urls)
    lookup = getattr(metadata, "cinematic_backdrop", None)
    if not getattr(metadata, "available", False) or not callable(lookup):
        return download_backdrop(provider, sized, token)
    if sized:
        first = _WorkerToken(2)
        first.cancel_event = token.cancel_event
        first.deadline = min(first.deadline, token.deadline)
        try:
            hit = download_backdrop(provider, sized, first)
            if hit is not None:
                return hit
        except _WorkerCancelled:
            token.check()
    try:
        resolved = _bounded_client_call(metadata, token,
            lambda: lookup(kind, item, info, artwork_only=True))
    except _WorkerCancelled:
        raise
    except Exception:
        token.check()
        resolved = ""
    fallback = artwork_urls((tmdb_poster_url(resolved, 780),))
    return cached_backdrop(fallback, token) or download_backdrop(provider, fallback or sized, token)


def lookup_poster_urls(metadata, kind, item, info, token):
    from .browser_common import _WorkerCancelled, _bounded_client_call
    from .diagnostics import log_event

    lookup = getattr(metadata, "poster_covers", None) or getattr(metadata, "poster_cover", None)
    if not callable(lookup):
        return ()
    try:
        urls = _bounded_client_call(metadata, token, lambda: lookup(kind, item, info))
    except _WorkerCancelled:
        raise
    except Exception as error:
        token.check()
        log_event("artwork", "stage=poster-lookup outcome=failed kind={}".format(kind), error)
        return ()
    return artwork_urls((urls,) if isinstance(urls, str) else (urls or ()))[:3]


def load_poster(provider, metadata, kind, item, info, urls, token, *, width=342, priority=True):
    """Try known or cached covers, then use an artwork-only TMDb lookup."""
    from .artwork import tmdb_poster_url
    from .browser_common import _WorkerCancelled, _WorkerToken, _cinematic_cover_ratio

    def fetch(candidates, budget, timeout):
        for url in candidates:
            budget.check()
            path = download_artwork(provider, url, budget, timeout=timeout, priority=priority)
            if path and _cinematic_cover_ratio(path):
                return url, path
        return None

    found = cached_poster(urls, token, width)
    if found is not None:
        return found
    known = artwork_urls(tmdb_poster_url(url, width) for url in urls)
    can_lookup = getattr(metadata, "available", False)
    budget = token
    if known and can_lookup:
        # A stalled provider must leave time to resolve and fetch TMDb art.
        budget = _WorkerToken(3)
        budget.cancel_event = token.cancel_event
        budget.deadline = min(budget.deadline, token.deadline)
    try:
        found = fetch(known, budget, 1.5 if can_lookup else 4)
        if found is not None:
            return found
    except _WorkerCancelled:
        token.check()
    if can_lookup:
        alternatives = lookup_poster_urls(metadata, kind, item, info, token)
        hit = cached_poster(alternatives, token, width)
        if hit is not None:
            return hit
        alternatives = artwork_urls(tmdb_poster_url(url, width) for url in alternatives)
        found = fetch(tuple(url for url in alternatives if url not in known), token, 2)
        if found is not None:
            return found
        if budget is not token:
            return fetch(known, token, 4)
    return None

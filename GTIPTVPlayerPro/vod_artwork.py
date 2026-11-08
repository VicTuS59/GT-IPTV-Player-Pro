# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Cache-first artwork selection for one film or series."""


def artwork_urls(values):
    from .browser_common import _safe_picon_url

    return tuple(dict.fromkeys(filter(None, map(_safe_picon_url, values))))


def backdrop_urls(item, info, preferred=""):
    return artwork_urls((preferred, getattr(info, "fallback_backdrop", ""),
                         getattr(info, "backdrop", ""), getattr(item, "backdrop", "")))


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


def download_artwork(provider, url, token):
    from .browser_common import _WorkerCancelled, _download_client_image, _picon_cache_path

    try:
        return _download_client_image(provider, url, _picon_cache_path(url),
                                      timeout=3, token=token, priority=True)
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

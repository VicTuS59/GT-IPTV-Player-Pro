# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

"""Small urllib helpers for API requests which carry account credentials."""

from urllib.error import HTTPError
from urllib.parse import parse_qsl, urlsplit
from urllib.request import HTTPRedirectHandler, build_opener


DEFAULT_MAX_REDIRECTS = 3
_CREDENTIAL_CHAIN = "_gtiptv_credential_redirect_chain"
_REDIRECT_COUNT = "_gtiptv_credential_redirect_count"
_PRIVATE_HEADERS = frozenset(
    ("authorization", "cookie", "proxy-authorization")
)


def _url_origin(value):
    try:
        parsed = urlsplit(str(value or ""))
        scheme = str(parsed.scheme or "").lower()
        hostname = str(parsed.hostname or "").lower()
        port = parsed.port or (443 if scheme == "https" else 80)
    except (TypeError, ValueError):
        return ()
    if scheme not in ("http", "https") or not hostname:
        return ()
    return scheme, hostname, int(port)


def _query_has_private_value(url, private_names):
    try:
        query = urlsplit(str(url or "")).query
        pairs = parse_qsl(query, keep_blank_values=True)
    except (TypeError, ValueError):
        return False
    for name, unused_value in pairs:
        if str(name or "").lower() in private_names:
            return True
    return False


def _request_has_credentials(request, private_names):
    if _query_has_private_value(request.full_url, private_names):
        return True
    for name, unused_value in request.header_items():
        if str(name or "").lower() in _PRIVATE_HEADERS:
            return True
    return False


class CredentialRedirectHandler(HTTPRedirectHandler):
    """Limit redirects without sending API credentials to another origin."""

    max_repeats = 3
    max_redirections = DEFAULT_MAX_REDIRECTS

    def __init__(self, private_query_names, max_redirects=DEFAULT_MAX_REDIRECTS):
        HTTPRedirectHandler.__init__(self)
        names = set()
        for name in private_query_names or ():
            name = str(name or "").strip().lower()
            if name:
                names.add(name)
        try:
            max_redirects = int(max_redirects)
        except (TypeError, ValueError, OverflowError):
            max_redirects = DEFAULT_MAX_REDIRECTS
        self.private_query_names = frozenset(names)
        self.max_redirects = max(1, min(DEFAULT_MAX_REDIRECTS, max_redirects))
        self.max_redirections = self.max_redirects

    def redirect_request(self, request, fp, code, msg, headers, new_url):
        source_origin = _url_origin(request.full_url)
        target_origin = _url_origin(new_url)
        credential_chain = bool(
            getattr(request, _CREDENTIAL_CHAIN, False)
            or _request_has_credentials(request, self.private_query_names)
        )
        try:
            target = urlsplit(str(new_url or ""))
            target_has_userinfo = bool(target.username or target.password)
        except (TypeError, ValueError):
            target_has_userinfo = True

        safe_upgrade = bool(
            source_origin
            and target_origin
            and source_origin[0] == "http"
            and target_origin[0] == "https"
            and source_origin[1] == target_origin[1]
            and (
                source_origin[2] == target_origin[2]
                or (source_origin[2] == 80 and target_origin[2] == 443)
            )
        )
        same_origin = bool(
            source_origin
            and target_origin
            and source_origin == target_origin
        )
        if credential_chain and source_origin and target_origin:
            if source_origin[0] == "https" and target_origin[0] == "http":
                raise HTTPError(
                    new_url,
                    code,
                    "HTTPS credential request cannot be redirected to HTTP",
                    headers,
                    fp,
                )
            if not same_origin and not safe_upgrade:
                raise HTTPError(
                    new_url,
                    code,
                    "Credential request cannot be redirected to another origin",
                    headers,
                    fp,
                )
        if credential_chain and (
            not source_origin or not target_origin or target_has_userinfo
        ):
            raise HTTPError(
                new_url,
                code,
                "Credential request redirect URL is not safe",
                headers,
                fp,
            )

        redirect_count = int(getattr(request, _REDIRECT_COUNT, 0) or 0)
        if redirect_count >= self.max_redirects:
            raise HTTPError(
                request.full_url,
                code,
                "Credential request was redirected too many times",
                headers,
                fp,
            )
        redirected = HTTPRedirectHandler.redirect_request(
            self,
            request,
            fp,
            code,
            msg,
            headers,
            new_url,
        )
        if redirected is not None:
            setattr(redirected, _CREDENTIAL_CHAIN, credential_chain)
            setattr(redirected, _REDIRECT_COUNT, redirect_count + 1)
        return redirected


def build_credential_urlopen(
    private_query_names,
    max_redirects=DEFAULT_MAX_REDIRECTS,
):
    """Return an ``urlopen``-compatible callable with bounded redirects."""

    return build_opener(
        CredentialRedirectHandler(
            private_query_names,
            max_redirects=max_redirects,
        )
    ).open

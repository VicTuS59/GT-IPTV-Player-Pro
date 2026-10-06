# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Opaque content and search-preference keys for in-session subtitle lists."""

from hashlib import sha256
import json


def subtitle_search_context(metadata, settings, identity=(), providers=None):
    metadata = metadata if isinstance(metadata, dict) else {}
    content = {
        name: " ".join(str(metadata.get(name) or "").split()).casefold()
        for name in ("title", "year", "content_type", "season", "episode",
                     "tmdb_id", "imdb_id", "sd_id")
    }
    preferences = {
        name: getattr(settings, name, None)
        for name in ("enabled", "provider", "primary_language", "secondary_language",
                     "hearing_impaired", "subdl_api_key", "subsource_api_key",
                     "opensubtitles_api_key", "opensubtitles_username",
                     "opensubtitles_password")
    }
    encoded = json.dumps((tuple(identity), content, preferences, providers),
                         sort_keys=True, separators=(",", ":"), default=str)
    # Neither API credentials nor receiver object identities reach the browser.
    return sha256(encoded.encode("utf-8")).hexdigest()

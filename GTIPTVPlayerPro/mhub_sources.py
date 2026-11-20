# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""User-configured mHub v2 sources, stored independently of M3U sources."""

import json
import os
import re
import stat
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

from .network_security import NetworkSecurityError, validate_public_url_syntax
from .persistence import guarded_mutation


MHUB_SOURCES_PATH = "/etc/enigma2/gtiptvplayer/mhub-sources.json"
_REGISTRY_VERSION = 1
_MAX_SOURCES = 500
_MAX_BYTES = 4 * 1024 * 1024
_LOCK = threading.RLock()


class MHubSourceConflict(ValueError):
    """The same manifest is already registered under another source ID."""


def _manifest_url(value, encode_components):
    try:
        address = validate_public_url_syntax(value)
        parsed = urlsplit(address["url"])
        host = address["hostname"]
        netloc = "[{}]".format(host) if ":" in host else host
        if address["port"] != (443 if parsed.scheme == "https" else 80):
            netloc += ":{}".format(address["port"])
        path = parsed.path.rstrip("/")
        if not path.endswith("/mhub-addon.json"):
            path += "/mhub-addon.json"
        query = parsed.query
        if encode_components:
            if any(ord(character) <= 32 or ord(character) >= 127 or character in "\\|%"
                   for character in netloc):
                raise ValueError()
            # Retain escaped bytes and reserved URL delimiters while accepting
            # paths and queries entered directly on the receiver keyboard.
            path = quote(re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", path), safe="/%:@!$&'()*+,;=")
            query = quote(re.sub(r"%(?![0-9A-Fa-f]{2})", "%25", query), safe="/?%:@!$&'()*+,;=")
        normalized = urlunsplit(parsed._replace(netloc=netloc, path=path, query=query))
        return validate_public_url_syntax(normalized)["url"]
    except (NetworkSecurityError, ValueError, UnicodeError):
        raise ValueError("Invalid mHub manifest URL") from None


def normalize_manifest_url(value):
    return _manifest_url(value, encode_components=True)


class MHubSource:
    source_type = "mhub"
    source_label = "mHub v2"
    capabilities = frozenset(("live",))

    def __init__(self, source_id, name, location, *, _stored=False):
        if not isinstance(source_id, str) or source_id in (".", "..") or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", source_id):
            raise ValueError("Invalid mHub source ID")
        if not isinstance(name, str) or not isinstance(location, str):
            raise ValueError("Invalid mHub source")
        self.source_id = source_id
        self.name = " ".join(name.split())[:80]
        stored_location = _manifest_url(location, encode_components=False) if _stored else None
        try:
            self.location = normalize_manifest_url(location)
        except ValueError:
            if stored_location is None:
                raise
            self.location = stored_location
        # Preserve existing entries, including distinct v1 spellings which
        # now normalize to an identical encoded URL.
        self._registry_location = stored_location or self.location
        self.host = urlsplit(self.location).hostname

    @property
    def display_name(self):
        return self.name or self.host

    @property
    def identity(self):
        return (self.source_type, self.source_id)

    def supports(self, capability):
        return capability in self.capabilities


def load_mhub_sources(path=MHUB_SOURCES_PATH):
    path = Path(path)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if path.is_symlink():
        raise ValueError("Invalid mHub source registry")
    try:
        fd = os.open(path, flags)
    except FileNotFoundError:
        return []
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > _MAX_BYTES:
            raise ValueError("Invalid mHub source registry")
        raw = handle.read(_MAX_BYTES + 1)
    if len(raw) > _MAX_BYTES:
        raise ValueError("Invalid mHub source registry")
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get("version") != _REGISTRY_VERSION:
        raise ValueError("Invalid mHub source registry")
    records = payload.get("sources")
    if not isinstance(records, list) or len(records) > _MAX_SOURCES:
        raise ValueError("Invalid mHub source registry")
    sources = []
    ids, locations = set(), set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Invalid mHub source registry")
        source = MHubSource(record.get("id"), record.get("name"), record.get("location"), _stored=True)
        if source.source_id in ids or source._registry_location in locations:
            raise ValueError("Duplicate mHub source registry entry")
        ids.add(source.source_id)
        locations.add(source._registry_location)
        sources.append(source)
    return sources


def _write_sources(path, sources):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    entries = [{"id": item.source_id, "name": item.name, "location": item._registry_location} for item in sources]
    fd, temporary = tempfile.mkstemp(prefix=".mhub-sources-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"version": _REGISTRY_VERSION, "sources": entries}, handle, ensure_ascii=False, separators=(",", ":"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@guarded_mutation(busy_error=True)
def save_mhub_source(source, path=MHUB_SOURCES_PATH):
    if not isinstance(source, MHubSource):
        raise ValueError("Invalid mHub source")
    with _LOCK:
        sources = load_mhub_sources(path)
        if any(item.location == source.location and item.source_id != source.source_id for item in sources):
            raise MHubSourceConflict("mHub source already exists")
        for index, item in enumerate(sources):
            if item.source_id == source.source_id:
                sources[index] = source
                break
        else:
            if len(sources) >= _MAX_SOURCES:
                raise ValueError("Too many mHub sources")
            sources.append(source)
        _write_sources(path, sources)
    return source


@guarded_mutation(busy_error=True)
def delete_mhub_source(source_id, path=MHUB_SOURCES_PATH):
    with _LOCK:
        sources = load_mhub_sources(path)
        remaining = [source for source in sources if source.source_id != source_id]
        if len(remaining) == len(sources):
            return False
        _write_sources(path, remaining)
        return True

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

import hashlib
import json
import os
import threading
import time
from urllib.parse import urlsplit

from .portal_identity import safe_composite_id


DEFAULT_RESUME_PATH = "/etc/enigma2/gtiptvplayerpro-resume.json"
MAX_RESUME_ITEMS = 250
MIN_RESUME_SECONDS = 5
MIN_REMAINING_SECONDS = 60
FINISHED_RATIO = 0.95
_RESUME_STORE_LOCK = threading.RLock()


def _safe_resume_portal_request_type(value, content_type):
    """Return one credential-free Stalker create-link dialect enum."""
    value = str(value or "").strip().lower()
    allowed = {
        "movie": frozenset(("vod", "movie")),
        "series": frozenset(("vod", "series")),
    }
    return (
        value
        if value in allowed.get(str(content_type or "").lower(), ())
        else ""
    )


def _client_is_stalker(client):
    return str(
        getattr(client, "source_type", "")
        or getattr(getattr(client, "account", None), "source_type", "")
        or ""
    ).strip().lower() == "stalker"


def _item_is_stalker(item):
    content_type = str(getattr(item, "content_type", "") or "").lower()
    return bool(
        str(
            getattr(item, "source_type", "")
            or getattr(item, "provider", "")
            or ""
        ).strip().lower() == "stalker"
        or _safe_resume_portal_request_type(
            getattr(item, "portal_request_type", ""),
            content_type,
        )
    )


def _resume_stream_id(value, content_type, allow_portal_composite=False):
    raw_value = str(value or "")
    numeric_value = raw_value.strip()
    if numeric_value.isdigit():
        return numeric_value
    if content_type == "series" and allow_portal_composite:
        return safe_composite_id(raw_value)
    return ""


def _account_identity(client):
    account = getattr(client, "account", None)
    identity = getattr(account, "identity", ())
    if not identity and account is not None:
        identity = (
            getattr(account, "scheme", ""),
            getattr(account, "netloc", ""),
            getattr(account, "base_path", ""),
            getattr(account, "username", ""),
        )
    return tuple(str(value or "") for value in (identity or ()))


def resume_account_scope(client):
    """Return a one-way account identifier without storing credentials."""
    identity = _account_identity(client)
    if not any(identity):
        return ""
    raw = "\x1f".join(identity)
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def resume_key(client, item):
    """Build an anonymous account/content key without persisting credentials."""
    content_type = str(getattr(item, "content_type", "") or "").lower()
    stream_id = _resume_stream_id(
        getattr(item, "stream_id", ""),
        content_type,
        allow_portal_composite=_client_is_stalker(client),
    )
    if content_type not in ("movie", "series") or not stream_id:
        return ""
    raw = "\x1f".join(
        list(_account_identity(client))
        + [content_type, stream_id]
    )
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def _safe_resume_icon(value):
    """Keep a poster URL without persisting embedded account credentials."""
    value = str(value or "").replace("\r", "").replace("\n", "").strip()
    if not value or len(value) > 1024:
        return ""
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        return ""
    if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
        return ""
    # Query strings, fragments and URL user-info frequently carry Xtream
    # usernames, passwords or signed tokens. The card can safely fall back to
    # its built-in placeholder when a provider only offers such a URL.
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    lowered = value.lower()
    if "username=" in lowered or "password=" in lowered or "token=" in lowered:
        return ""
    return value


def resume_item_metadata(item):
    """Return the minimum safe data needed to reopen a VOD item."""
    content_type = str(getattr(item, "content_type", "") or "").lower()
    is_stalker = _item_is_stalker(item)
    stream_id = _resume_stream_id(
        getattr(item, "stream_id", ""),
        content_type,
        allow_portal_composite=is_stalker,
    )
    if content_type not in ("movie", "series") or not stream_id:
        return {}
    name = str(getattr(item, "name", "") or "").replace("\r", " ")
    name = name.replace("\n", " ").strip()[:180]
    extension = str(getattr(item, "extension", "") or "").lower().lstrip(".")
    if not extension.replace("_", "").isalnum():
        extension = ""
    raw_parent_id = str(getattr(item, "parent_id", "") or "")
    parent_id = raw_parent_id.strip()
    if not parent_id.isdigit():
        parent_id = (
            safe_composite_id(raw_parent_id)
            if content_type == "series" and is_stalker
            else ""
        )
    parent = getattr(item, "favorite_parent", None)
    parent_name = str(getattr(parent, "name", "") or "")
    parent_name = parent_name.replace("\r", " ").replace("\n", " ")
    parent_name = " ".join(parent_name.split())[:180]
    metadata = {
        "content_type": content_type,
        "stream_id": stream_id[:40] if stream_id.isdigit() else stream_id,
        "name": name or "GT IPTV",
        "category_id": str(
            getattr(item, "category_id", "") or ""
        ).strip()[:40],
        "extension": (extension[:12] or "mp4"),
        "season": str(getattr(item, "season", "") or "").strip()[:12],
        "episode": str(getattr(item, "episode", "") or "").strip()[:12],
        "year": str(getattr(item, "year", "") or "").strip()[:12],
        "icon": _safe_resume_icon(getattr(item, "icon", "")),
        "parent_id": parent_id[:40] if parent_id.isdigit() else parent_id,
        "parent_name": parent_name if parent_id else "",
    }
    if is_stalker:
        metadata["source_type"] = "stalker"
        request_type = _safe_resume_portal_request_type(
            getattr(item, "portal_request_type", ""),
            content_type,
        )
        if request_type:
            metadata["portal_request_type"] = request_type
    return metadata


def should_save_resume(position, length):
    try:
        position = int(position)
        length = int(length)
    except (TypeError, ValueError, OverflowError):
        return False
    if position < MIN_RESUME_SECONDS or length <= 0 or position >= length:
        return False
    if length - position < MIN_REMAINING_SECONDS:
        return False
    return float(position) / float(length) < FINISHED_RATIO


class PlaybackResumeStore(object):
    def __init__(self, path=DEFAULT_RESUME_PATH, clock=None):
        self.path = path
        self.clock = clock or time.time

    def _load_state(self):
        """Return stored items and whether a mutator may safely replace them."""
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            # A dangling symlink is still an existing, unreadable path.  Do not
            # replace it as though the resume file had never existed.
            return {}, not os.path.lexists(self.path)
        except (IOError, OSError, TypeError, ValueError):
            return {}, False
        if not isinstance(payload, dict):
            return {}, False
        items = payload.get("items")
        if not isinstance(items, dict):
            return {}, False
        return items, True

    def _load(self):
        with _RESUME_STORE_LOCK:
            items, unused_mutable = self._load_state()
            del unused_mutable
            return items

    def _write_locked(self, items):
        directory = os.path.dirname(self.path)
        temporary = "{}.tmp-{}-{}".format(
            self.path,
            os.getpid(),
            "{}-{}".format(threading.get_ident(), time.monotonic_ns()),
        )
        descriptor = None
        try:
            if directory and not os.path.isdir(directory):
                os.makedirs(directory, exist_ok=True)
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(temporary, flags, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                descriptor = None
                json.dump(
                    {"version": 3, "items": items},
                    handle,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                )
                handle.flush()
                try:
                    os.fsync(handle.fileno())
                except (AttributeError, OSError):
                    pass
            replacer = getattr(os, "replace", os.rename)
            replacer(temporary, self.path)
            return True
        except (IOError, OSError, TypeError, ValueError):
            return False
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            try:
                if os.path.lexists(temporary):
                    os.unlink(temporary)
            except OSError:
                pass

    def _write(self, items):
        with _RESUME_STORE_LOCK:
            return self._write_locked(items)

    def get(self, key):
        if not key:
            return None
        entry = self._load().get(key)
        if not isinstance(entry, dict):
            return None
        try:
            position = int(entry.get("position", 0))
            length = int(entry.get("length", 0))
            updated_at = int(entry.get("updated_at", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return None
        if not should_save_resume(position, length):
            return None
        return {
            "position": position,
            "length": length,
            "updated_at": updated_at,
        }

    def list_entries(self, account_scope=""):
        """List resumable entries newest-first for one anonymous account."""
        account_scope = str(account_scope or "")
        if not account_scope:
            return []
        results = []
        for key, entry in self._load().items():
            if not isinstance(entry, dict):
                continue
            if str(entry.get("account_scope", "")) != account_scope:
                continue
            item = entry.get("item")
            if not isinstance(item, dict):
                continue
            try:
                position = int(entry.get("position", 0))
                length = int(entry.get("length", 0))
                updated_at = int(entry.get("updated_at", 0) or 0)
            except (TypeError, ValueError, OverflowError):
                continue
            if not should_save_resume(position, length):
                continue
            content_type = str(item.get("content_type", "") or "").lower()
            stream_id = _resume_stream_id(
                item.get("stream_id", ""),
                content_type,
                allow_portal_composite=(
                    str(item.get("source_type", "") or "").lower()
                    == "stalker"
                ),
            )
            if content_type not in ("movie", "series") or not stream_id:
                continue
            results.append(
                {
                    "key": str(key),
                    "position": position,
                    "length": length,
                    "updated_at": updated_at,
                    "item": dict(item),
                }
            )
        results.sort(
            key=lambda entry: (entry["updated_at"], entry["key"]),
            reverse=True,
        )
        return results[:MAX_RESUME_ITEMS]

    def remember(self, key, item, account_scope=""):
        """Enrich a valid legacy bookmark without changing its watch time."""
        if not key:
            return False
        metadata = resume_item_metadata(item)
        if not metadata:
            return False
        with _RESUME_STORE_LOCK:
            items, mutable = self._load_state()
            if not mutable:
                return False
            entry = items.get(key)
            if not isinstance(entry, dict):
                return False
            try:
                position = int(entry.get("position", 0))
                length = int(entry.get("length", 0))
            except (TypeError, ValueError, OverflowError):
                return False
            if not should_save_resume(position, length):
                return False
            entry = dict(entry)
            entry["item"] = metadata
            if account_scope:
                entry["account_scope"] = str(account_scope)
            if items.get(key) == entry:
                return True
            items[key] = entry
            return self._write_locked(items)

    def remember_many(self, entries, account_scope=""):
        """Enrich several bookmarks with one read and at most one disk write."""
        with _RESUME_STORE_LOCK:
            items, mutable = self._load_state()
            if not mutable:
                return False
            changed = False
            eligible = False
            for key, item in list(entries or ()):
                key = str(key or "")
                metadata = resume_item_metadata(item)
                entry = items.get(key)
                if not key or not metadata or not isinstance(entry, dict):
                    continue
                try:
                    position = int(entry.get("position", 0))
                    length = int(entry.get("length", 0))
                except (TypeError, ValueError, OverflowError):
                    continue
                if not should_save_resume(position, length):
                    continue
                eligible = True
                updated = dict(entry)
                updated["item"] = metadata
                if account_scope:
                    updated["account_scope"] = str(account_scope)
                if updated != entry:
                    items[key] = updated
                    changed = True
            if not eligible:
                return False
            if not changed:
                return True
            return self._write_locked(items)

    def save(self, key, position, length, item=None, account_scope=""):
        if not key:
            return False
        if not should_save_resume(position, length):
            return self.clear(key)
        with _RESUME_STORE_LOCK:
            items, mutable = self._load_state()
            if not mutable:
                return False
            previous = items.get(key)
            entry = dict(previous) if isinstance(previous, dict) else {}
            entry.update({
                "position": int(position),
                "length": int(length),
                "updated_at": int(self.clock()),
            })
            metadata = resume_item_metadata(item)
            if metadata:
                entry["item"] = metadata
            if account_scope:
                entry["account_scope"] = str(account_scope)
            items[key] = entry
            if len(items) > MAX_RESUME_ITEMS:
                def updated_at(item_key):
                    value = items.get(item_key)
                    if not isinstance(value, dict):
                        return 0
                    try:
                        return int(value.get("updated_at", 0) or 0)
                    except (TypeError, ValueError, OverflowError):
                        return 0

                ordered = sorted(
                    items,
                    key=updated_at,
                )
                for old_key in ordered[: len(items) - MAX_RESUME_ITEMS]:
                    items.pop(old_key, None)
            return self._write_locked(items)

    def clear(self, key):
        if not key:
            return False
        with _RESUME_STORE_LOCK:
            items, mutable = self._load_state()
            if not mutable:
                return False
            if key not in items:
                return True
            items.pop(key, None)
            return self._write_locked(items)

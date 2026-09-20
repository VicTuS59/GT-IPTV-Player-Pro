# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account-scoped favorites stored without provider credentials."""

import hashlib
import json
import os
import threading
import time
from urllib.parse import urlsplit

from .portal_identity import safe_composite_id


DEFAULT_FAVORITES_PATH = "/etc/enigma2/gtiptvplayerpro-favorites.json"
MAX_FAVORITES = 2000
# R41 stores an explicit 64-character key order in addition to the unchanged
# legacy items dictionary.  Eight MiB safely accommodates MAX_FAVORITES with
# maximum-length safe poster URLs and multibyte titles.
MAX_FAVORITES_BYTES = 8 * 1024 * 1024
_STORE_LOCK = threading.RLock()
_CONTENT_TYPES = frozenset(("live", "movie", "series"))
_M3U_STREAM_PREFIX = "m3u:"
_M3U_STREAM_HEX_LENGTH = 64
_LOWER_HEX = frozenset("0123456789abcdef")


def _clean_text(value, limit):
    value = str(value or "").replace("\r", " ").replace("\n", " ")
    return " ".join(value.split())[: int(limit)]


def _numeric_id(value):
    value = str(value or "").strip()
    return value[:40] if value.isdigit() else ""


def _favorite_stream_id(value, content_type, allow_portal_composite=False):
    """Return a stored stream identifier accepted by the favorites format."""
    numeric = _numeric_id(value)
    if numeric:
        return numeric

    if content_type == "series" and allow_portal_composite:
        composite = safe_composite_id(value)
        if composite:
            return composite

    if content_type != "live":
        return ""
    value = str(value or "")
    expected_length = len(_M3U_STREAM_PREFIX) + _M3U_STREAM_HEX_LENGTH
    if len(value) != expected_length or not value.startswith(
        _M3U_STREAM_PREFIX
    ):
        return ""
    digest = value[len(_M3U_STREAM_PREFIX):]
    if any(character not in _LOWER_HEX for character in digest):
        return ""
    return value


def _item_value(item, name):
    if isinstance(item, dict):
        return item.get(name, "")
    return getattr(item, name, "")


def _is_stalker_item(item):
    source_type = str(
        _item_value(item, "source_type")
        or _item_value(item, "provider")
        or ""
    ).strip().lower()
    if source_type == "stalker":
        return True
    return bool(str(_item_value(item, "portal_request_type") or "").strip())


def _safe_extension(value, content_type):
    value = str(value or "").lower().lstrip(".")[:12]
    if not value.replace("_", "").isalnum():
        value = ""
    if value:
        return value
    return "ts" if content_type == "live" else "mp4"


def _safe_portal_request_type(value, content_type):
    """Keep only a credential-free Stalker request dialect enum."""

    value = str(value or "").strip().lower()
    allowed = {
        "live": frozenset(("itv",)),
        "movie": frozenset(("vod", "movie")),
        "series": frozenset(("vod", "series")),
    }
    return value if value in allowed.get(content_type, ()) else ""


def _safe_icon(value):
    """Keep only public-looking artwork URLs with no embedded credentials."""
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
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        return ""
    lowered = value.lower()
    for marker in (
        "username=",
        "password=",
        "token=",
        "auth=",
        "apikey=",
        "api_key=",
    ):
        if marker in lowered:
            return ""
    return value


def favorite_key(account_scope, item):
    """Return an opaque key for one account/content pair."""
    account_scope = str(account_scope or "").strip()
    content_type = str(_item_value(item, "content_type") or "").lower()
    is_stalker = _is_stalker_item(item)
    stream_id = _favorite_stream_id(
        _item_value(item, "stream_id"),
        content_type,
        allow_portal_composite=is_stalker,
    )
    if not account_scope or content_type not in _CONTENT_TYPES or not stream_id:
        return ""
    raw = "\x1f".join((account_scope, content_type, stream_id))
    return hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()


def favorite_item_metadata(item):
    """Return the small credential-free snapshot needed to reopen an item."""
    content_type = str(_item_value(item, "content_type") or "").lower()
    is_stalker = _is_stalker_item(item)
    stream_id = _favorite_stream_id(
        _item_value(item, "stream_id"),
        content_type,
        allow_portal_composite=is_stalker,
    )
    if content_type not in _CONTENT_TYPES or not stream_id:
        return {}
    name = _clean_text(_item_value(item, "name"), 180)
    parent_id = _numeric_id(_item_value(item, "parent_id"))
    if not parent_id and content_type == "series" and is_stalker:
        parent_id = safe_composite_id(_item_value(item, "parent_id"))
    metadata = {
        "content_type": content_type,
        "stream_id": stream_id,
        "name": name or "GT IPTV",
        "category_id": _numeric_id(_item_value(item, "category_id")),
        "catalog_category_id": _numeric_id(
            _item_value(item, "catalog_category_id")
        ),
        "extension": _safe_extension(
            _item_value(item, "extension"),
            content_type,
        ),
        "icon": _safe_icon(_item_value(item, "icon")),
        "rating": _clean_text(_item_value(item, "rating"), 24),
        "year": _clean_text(_item_value(item, "year"), 12),
        "parent_id": parent_id,
    }
    if is_stalker:
        metadata["source_type"] = "stalker"
        request_type = _safe_portal_request_type(
            _item_value(item, "portal_request_type"),
            content_type,
        )
        if request_type:
            metadata["portal_request_type"] = request_type
    return metadata


class FavoriteStore(object):
    """Small atomic JSON store; disk access occurs only on explicit actions."""

    def __init__(self, path=DEFAULT_FAVORITES_PATH, clock=None):
        self.path = path
        self.clock = clock or time.time

    def _load_state(self):
        """Return stored state and whether a mutator may replace it."""
        try:
            if os.path.getsize(self.path) > MAX_FAVORITES_BYTES:
                return {}, [], False
        except FileNotFoundError:
            # A dangling symlink is still an existing, unreadable path.  Do not
            # replace it as though the favorites file had never existed.
            return {}, [], not os.path.lexists(self.path)
        except (IOError, OSError, TypeError, ValueError):
            return {}, [], False
        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except FileNotFoundError:
            return {}, [], not os.path.lexists(self.path)
        except (IOError, OSError, TypeError, ValueError):
            return {}, [], False
        if not isinstance(payload, dict):
            return {}, [], False
        items = payload.get("items")
        if not isinstance(items, dict):
            return {}, [], False

        # R40 stores only ``items``.  R41 adds an explicit order while keeping
        # the original dictionary untouched so old favorites are recognised
        # without a migration pass.  Missing keys are appended using R40's
        # updated_at ordering.
        raw_order = payload.get("order", []) if isinstance(payload, dict) else []
        order = []
        seen = set()
        if isinstance(raw_order, list):
            for key in raw_order:
                key = str(key or "")
                if key in items and key not in seen:
                    seen.add(key)
                    order.append(key)
        missing = []
        for key, entry in items.items():
            key = str(key)
            if key in seen:
                continue
            try:
                updated_at = int(entry.get("updated_at", 0) or 0)
            except (AttributeError, TypeError, ValueError, OverflowError):
                updated_at = 0
            missing.append((updated_at, key))
        missing.sort(reverse=True)
        order.extend(key for unused_updated_at, key in missing)
        return items, order, True

    def _load(self):
        """Compatibility helper retained for callers from older releases."""
        return self._load_state()[0]

    def _write_locked(self, items, order=None):
        order = list(order or ())
        canonical_order = []
        seen = set()
        for key in order:
            key = str(key or "")
            if key in items and key not in seen:
                seen.add(key)
                canonical_order.append(key)
        for key in items:
            key = str(key)
            if key not in seen:
                seen.add(key)
                canonical_order.append(key)
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
                    {"version": 1, "items": items, "order": canonical_order},
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

    def _write(self, items, order=None):
        with _STORE_LOCK:
            return self._write_locked(items, order)

    @staticmethod
    def _valid_entry(key, entry, account_scope="", content_type=""):
        if not isinstance(entry, dict):
            return None
        scope = str(entry.get("account_scope", "") or "")
        if account_scope and scope != str(account_scope):
            return None
        metadata = entry.get("item")
        if not isinstance(metadata, dict):
            return None
        metadata = favorite_item_metadata(metadata)
        if not metadata:
            return None
        item_type = metadata["content_type"]
        if content_type and item_type != str(content_type):
            return None
        if str(key) != favorite_key(scope, metadata):
            return None
        try:
            updated_at = int(entry.get("updated_at", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return None
        return {
            "key": str(key),
            "account_scope": scope,
            "updated_at": max(0, updated_at),
            "item": metadata,
        }

    def list_entries(self, account_scope="", content_type=""):
        account_scope = str(account_scope or "")
        content_type = str(content_type or "").lower()
        if not account_scope or (content_type and content_type not in _CONTENT_TYPES):
            return []
        with _STORE_LOCK:
            items, order, unused_mutable = self._load_state()
            del unused_mutable
        results = []
        for key in order:
            entry = items.get(key)
            valid = self._valid_entry(
                key,
                entry,
                account_scope=account_scope,
                content_type=content_type,
            )
            if valid is not None:
                results.append(valid)
        return results[:MAX_FAVORITES]

    def keys(self, account_scope=""):
        return {
            entry["key"]
            for entry in self.list_entries(account_scope)
        }

    @staticmethod
    def _prune(items, order):
        if len(items) <= MAX_FAVORITES:
            return items, list(order)
        keep_order = [key for key in order if key in items][:MAX_FAVORITES]
        keep = set(keep_order)
        return (
            {key: value for key, value in items.items() if key in keep},
            keep_order,
        )

    def add(self, account_scope, item):
        account_scope = str(account_scope or "")
        key = favorite_key(account_scope, item)
        metadata = favorite_item_metadata(item)
        if not key or not metadata:
            return False
        with _STORE_LOCK:
            items, order, mutable = self._load_state()
            if not mutable:
                return False
            items[key] = {
                "account_scope": account_scope,
                "updated_at": int(self.clock()),
                "item": metadata,
            }
            order = [entry_key for entry_key in order if entry_key != key]
            order.insert(0, key)
            items, order = self._prune(items, order)
            return self._write_locked(items, order)

    def remove(self, key, account_scope=""):
        key = str(key or "")
        if not key:
            return False
        with _STORE_LOCK:
            items, order, mutable = self._load_state()
            if not mutable:
                return False
            entry = items.get(key)
            if self._valid_entry(
                key,
                entry,
                account_scope=str(account_scope or ""),
            ) is None:
                return False
            items.pop(key, None)
            order = [entry_key for entry_key in order if entry_key != key]
            return self._write_locked(items, order)

    def clear(self, key):
        """Compatibility helper for the shared poster-grid controller."""
        return self.remove(key)

    def toggle(self, account_scope, item):
        account_scope = str(account_scope or "")
        key = favorite_key(account_scope, item)
        metadata = favorite_item_metadata(item)
        if not key or not metadata:
            return None
        with _STORE_LOCK:
            items, order, mutable = self._load_state()
            if not mutable:
                return None
            if self._valid_entry(
                key,
                items.get(key),
                account_scope=account_scope,
            ) is not None:
                items.pop(key, None)
                order = [entry_key for entry_key in order if entry_key != key]
                return False if self._write_locked(items, order) else None
            items[key] = {
                "account_scope": account_scope,
                "updated_at": int(self.clock()),
                "item": metadata,
            }
            order = [entry_key for entry_key in order if entry_key != key]
            order.insert(0, key)
            items, order = self._prune(items, order)
            return True if self._write_locked(items, order) else None

    def remember(self, key, item, account_scope=""):
        """Refresh safe metadata without moving an existing favorite."""
        key = str(key or "")
        metadata = favorite_item_metadata(item)
        if not key or not metadata:
            return False
        with _STORE_LOCK:
            items, order, mutable = self._load_state()
            if not mutable:
                return False
            entry = items.get(key)
            valid = self._valid_entry(
                key,
                entry,
                account_scope=str(account_scope or ""),
            )
            if valid is None:
                return False
            updated = dict(entry)
            updated["item"] = metadata
            if account_scope:
                updated["account_scope"] = str(account_scope)
            if updated == entry:
                return True
            items[key] = updated
            return self._write_locked(items, order)

    def remember_many(self, entries, account_scope=""):
        """Refresh several poster URLs with one flash write at most."""
        account_scope = str(account_scope or "")
        with _STORE_LOCK:
            items, order, mutable = self._load_state()
            if not mutable:
                return False
            changed = False
            eligible = False
            for key, item in list(entries or ()):
                key = str(key or "")
                metadata = favorite_item_metadata(item)
                entry = items.get(key)
                valid = self._valid_entry(
                    key,
                    entry,
                    account_scope=account_scope,
                )
                if valid is None or not metadata:
                    continue
                eligible = True
                updated = dict(entry)
                updated["item"] = metadata
                if account_scope:
                    updated["account_scope"] = account_scope
                if updated != entry:
                    items[key] = updated
                    changed = True
            if not eligible or not changed:
                return eligible
            return self._write_locked(items, order)

    def reorder(self, account_scope, ordered_keys, content_type=""):
        """Persist one account/filter order without touching timestamps.

        Only slots occupied by the selected account and optional content type
        are replaced.  Favorites outside the active filter retain both their
        relative order and their exact stored metadata.
        """
        account_scope = str(account_scope or "")
        content_type = str(content_type or "").strip().lower()
        if not account_scope or (content_type and content_type not in _CONTENT_TYPES):
            return False
        requested = [str(key or "") for key in list(ordered_keys or ())]
        if not requested or len(requested) != len(set(requested)):
            return False
        with _STORE_LOCK:
            items, order, mutable = self._load_state()
            if not mutable:
                return False
            current = []
            for key in order:
                if self._valid_entry(
                    key,
                    items.get(key),
                    account_scope=account_scope,
                    content_type=content_type,
                ) is not None:
                    current.append(key)
            if len(current) != len(requested) or set(current) != set(requested):
                return False
            requested_iter = iter(requested)
            selected = set(current)
            replacement = [
                next(requested_iter) if key in selected else key
                for key in order
            ]
            return self._write_locked(items, replacement)

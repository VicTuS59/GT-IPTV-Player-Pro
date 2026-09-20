# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Credential-free, account-scoped content category preferences."""

import errno
import fcntl
import json
import os
import re
import stat
import threading
import time

from .category_filters import (
    category_filter_ids,
    clean_filter_terms,
    valid_filter_terms,
)


DEFAULT_CATEGORY_PREFERENCES_PATH = (
    "/etc/enigma2/gtiptvplayer/live-categories.json"
)
DEFAULT_MOVIE_CATEGORY_PREFERENCES_PATH = (
    "/etc/enigma2/gtiptvplayer/movie-categories.json"
)
DEFAULT_SERIES_CATEGORY_PREFERENCES_PATH = (
    "/etc/enigma2/gtiptvplayer/series-categories.json"
)
CATEGORY_PREFERENCE_PATHS = {
    "live": DEFAULT_CATEGORY_PREFERENCES_PATH,
    "movie": DEFAULT_MOVIE_CATEGORY_PREFERENCES_PATH,
    "series": DEFAULT_SERIES_CATEGORY_PREFERENCES_PATH,
}
MAX_CATEGORY_PREFERENCES_BYTES = 8 * 1024 * 1024
MAX_CATEGORY_SOURCES = 500
MAX_CATEGORIES_PER_SOURCE = 20000
_FORMAT_VERSION = 1
_STORE_LOCK = threading.RLock()
_WRITE_LOCK_TIMEOUT_SECONDS = 2.0
_SCOPE_PATTERN = re.compile(r"^[0-9a-f]{64}$")


def _clean_scope(value):
    value = str(value or "").strip().lower()
    return value if _SCOPE_PATTERN.fullmatch(value) else ""


def _clean_category_id(value):
    if not isinstance(value, str):
        return ""
    value = value.strip()
    if not value or value == "*":
        return ""
    try:
        encoded = value.encode("utf-8", "strict")
    except UnicodeError:
        return ""
    if not encoded or len(encoded) > 128:
        return ""
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        return ""
    return value


def _category_id(category):
    if isinstance(category, dict):
        return str(category.get("category_id") or "").strip()
    return str(getattr(category, "category_id", "") or "").strip()


def category_preferences_path(content_type):
    """Return the fixed preference path for one supported content type."""

    content_type = str(content_type or "").strip().lower()
    try:
        return CATEGORY_PREFERENCE_PATHS[content_type]
    except KeyError:
        raise ValueError("Unsupported category preference content type")


def _clean_id_list(values):
    cleaned = []
    seen = set()
    if not isinstance(values, (list, tuple, set, frozenset)):
        return cleaned
    for value in values:
        category_id = _clean_category_id(value)
        if not category_id or category_id in seen:
            continue
        seen.add(category_id)
        cleaned.append(category_id)
        if len(cleaned) >= MAX_CATEGORIES_PER_SOURCE:
            break
    return cleaned


def content_item_category_ids(item):
    """Return every trustworthy category ID carried by one content item.

    Xtream items normally expose ``category_id`` while Stalker/Ministra
    records can belong to more than one portal category.  The catalogue ID is
    also retained because a few portal dialects omit category metadata from
    the row itself after a category-scoped request.

    Missing metadata deliberately produces an empty set.  Callers can then
    keep the item visible instead of guessing and hiding unrelated content.
    """

    if isinstance(item, dict):
        getter = item.get
    else:
        getter = lambda name, default=None: getattr(item, name, default)

    values = []
    portal_values = getter("portal_category_ids", ())
    if isinstance(portal_values, dict):
        values.extend(portal_values.keys())
    elif isinstance(portal_values, (list, tuple, set, frozenset)):
        values.extend(portal_values)
    elif portal_values not in (None, ""):
        values.append(portal_values)
    values.extend(
        (
            getter("category_id", ""),
            getter("catalog_category_id", ""),
        )
    )

    category_ids = set()
    for value in values:
        if value is None:
            continue
        category_id = _clean_category_id(str(value))
        if category_id:
            category_ids.add(category_id)
    return frozenset(category_ids)


def hidden_category_match_ids(categories, hidden_ids):
    """Add unique provider category names as safe aliases for hidden IDs.

    A few Stalker/Ministra dialects return a numeric ID from the category
    endpoint but only its human-readable name on content rows.  The category
    list is already in memory, so a unique ID-to-name mapping closes that gap
    without another portal request.  Duplicate names remain unmapped to avoid
    hiding a visible category by guesswork.
    """

    hidden = []
    seen_hidden = set()
    for value in hidden_ids or ():
        if value is None or isinstance(
            value,
            (dict, list, tuple, set, frozenset),
        ):
            continue
        category_id = _clean_category_id(str(value))
        if not category_id or category_id in seen_hidden:
            continue
        seen_hidden.add(category_id)
        hidden.append(category_id)
        if len(hidden) >= MAX_CATEGORIES_PER_SOURCE * 2:
            break
    if not hidden:
        return frozenset()

    alias_owners = {}
    category_ids = set()
    for category in list(categories or ())[:MAX_CATEGORIES_PER_SOURCE]:
        category_id = _clean_category_id(_category_id(category))
        if not category_id:
            continue
        category_ids.add(category_id)
        if isinstance(category, dict):
            raw_name = (
                category.get("name")
                or category.get("title")
                or category.get("category_name")
                or ""
            )
            raw_aliases = (
                category.get("portal_aliases")
                or category.get("category_alias")
                or category.get("alias")
                or ()
            )
        else:
            raw_name = getattr(category, "name", "")
            raw_aliases = getattr(category, "portal_aliases", ())
        if not isinstance(raw_aliases, (list, tuple, set, frozenset)):
            raw_aliases = (raw_aliases,)
        for raw_alias in (raw_name,) + tuple(raw_aliases):
            alias = _clean_category_id(str(raw_alias or ""))
            if alias:
                alias_owners.setdefault(alias, set()).add(category_id)

    matches = list(hidden)
    seen_matches = set(hidden)
    for alias, owners in alias_owners.items():
        if (
            len(owners) == 1
            and next(iter(owners)) in seen_hidden
            and (alias not in category_ids or alias in seen_hidden)
            and alias not in seen_matches
        ):
            seen_matches.add(alias)
            matches.append(alias)
            if len(matches) >= MAX_CATEGORIES_PER_SOURCE * 2:
                break
    return frozenset(matches)


def filter_hidden_category_items(items, hidden_ids):
    """Hide mapped items from synthetic All lists without provider I/O."""

    values = list(items or ())
    hidden = hidden_category_match_ids((), hidden_ids)
    if not hidden:
        return values
    return [
        item
        for item in values
        if not content_item_category_ids(item).intersection(hidden)
    ]


class CategoryPreferences(object):
    """Validated immutable snapshot for one IPTV source."""

    def __init__(
        self,
        order=None,
        hidden=None,
        include_terms=None,
        exclude_terms=None,
    ):
        self.order = tuple(_clean_id_list(order or ()))
        self.hidden = frozenset(_clean_id_list(hidden or ()))
        self.include_terms = clean_filter_terms(include_terms or ())
        self.exclude_terms = clean_filter_terms(exclude_terms or ())


def arrange_live_categories(
    categories,
    preferences=None,
    include_hidden=False,
):
    """Apply stored order while keeping the synthetic All Channels row fixed."""

    preferences = preferences or CategoryPreferences()
    all_channels = None
    category_by_id = {}
    provider_order = []
    for category in list(categories or ()):
        raw_id = _category_id(category)
        if not raw_id:
            if all_channels is None:
                all_channels = category
            continue
        category_id = _clean_category_id(raw_id)
        if not category_id or category_id in category_by_id:
            continue
        category_by_id[category_id] = category
        provider_order.append(category_id)
        if len(provider_order) >= MAX_CATEGORIES_PER_SOURCE:
            break

    ordered_ids = []
    seen = set()
    for category_id in preferences.order:
        if category_id in category_by_id and category_id not in seen:
            seen.add(category_id)
            ordered_ids.append(category_id)
    for category_id in provider_order:
        if category_id not in seen:
            seen.add(category_id)
            ordered_ids.append(category_id)

    arranged = [all_channels] if all_channels is not None else []
    hidden = preferences.hidden
    allowed = frozenset(category_by_id)
    if not include_hidden and (preferences.include_terms or preferences.exclude_terms):
        allowed, unused_excluded = category_filter_ids(
            list(category_by_id.values()),
            preferences.include_terms,
            preferences.exclude_terms,
        )
    arranged.extend(
        category_by_id[category_id]
        for category_id in ordered_ids
        if include_hidden or (
            category_id not in hidden and category_id in allowed
        )
    )
    return arranged


# The implementation has always been content-neutral.  Keep the historical
# public name for R68/R72 callers while exposing the accurate R73 name.
arrange_categories = arrange_live_categories


class LiveCategoryPreferenceStore(object):
    """Small atomic category store; I/O occurs only on load or explicit save."""

    def __init__(self, path=DEFAULT_CATEGORY_PREFERENCES_PATH):
        self.path = path

    @staticmethod
    def _validated_sources(payload):
        if not isinstance(payload, dict):
            return {}, False
        try:
            version = int(payload.get("version", 0) or 0)
        except (TypeError, ValueError, OverflowError):
            return {}, False
        if version != _FORMAT_VERSION:
            return {}, False
        raw_sources = payload.get("sources")
        if not isinstance(raw_sources, dict):
            return {}, False
        sources = {}
        for raw_scope, raw_preferences in raw_sources.items():
            scope = _clean_scope(raw_scope)
            if not scope or not isinstance(raw_preferences, dict):
                continue
            raw_order = raw_preferences.get("order", [])
            raw_hidden = raw_preferences.get("hidden", [])
            raw_include = raw_preferences.get("include_terms", [])
            raw_exclude = raw_preferences.get("exclude_terms", [])
            if (
                not isinstance(raw_order, list)
                or not isinstance(raw_hidden, list)
                or any(not isinstance(value, str) for value in raw_order)
                or any(not isinstance(value, str) for value in raw_hidden)
                or not isinstance(raw_include, list)
                or not isinstance(raw_exclude, list)
                or not valid_filter_terms(raw_include)
                or not valid_filter_terms(raw_exclude)
            ):
                return {}, False
            preferences = CategoryPreferences(
                raw_order,
                raw_hidden,
                raw_include,
                raw_exclude,
            )
            sources[scope] = {
                "order": list(preferences.order),
                "hidden": sorted(preferences.hidden),
                "include_terms": list(preferences.include_terms),
                "exclude_terms": list(preferences.exclude_terms),
            }
            if len(sources) >= MAX_CATEGORY_SOURCES:
                break
        return sources, True

    def _open_parent_directory(self, create=False):
        directory = os.path.dirname(self.path) or "."
        descriptor = None
        try:
            if create and not os.path.isdir(directory):
                os.makedirs(directory, mode=0o700, exist_ok=True)
            listed = os.lstat(directory)
            if stat.S_ISLNK(listed.st_mode) or not stat.S_ISDIR(
                listed.st_mode
            ):
                return None
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_DIRECTORY", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(directory, flags)
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(opened.st_mode)
                or opened.st_dev != listed.st_dev
                or opened.st_ino != listed.st_ino
            ):
                os.close(descriptor)
                descriptor = None
                return None
            return descriptor
        except (IOError, OSError, TypeError, ValueError):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            return None

    def _acquire_store_lock(self, exclusive):
        directory_descriptor = self._open_parent_directory(create=True)
        if directory_descriptor is None:
            return None, None
        lock_descriptor = None
        lock_name = "{}.lock".format(os.path.basename(self.path))
        try:
            flags = os.O_RDWR | os.O_CREAT
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            lock_descriptor = os.open(
                lock_name,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
            opened = os.fstat(lock_descriptor)
            listed = os.stat(
                lock_name,
                dir_fd=directory_descriptor,
                follow_symlinks=False,
            )
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or not stat.S_ISREG(listed.st_mode)
                or opened.st_dev != listed.st_dev
                or opened.st_ino != listed.st_ino
            ):
                raise OSError("Unsafe category preference lock")
            os.fchmod(lock_descriptor, 0o600)
            deadline = time.monotonic() + _WRITE_LOCK_TIMEOUT_SECONDS
            operation = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
            while True:
                try:
                    fcntl.flock(
                        lock_descriptor,
                        operation | fcntl.LOCK_NB,
                    )
                    break
                except OSError as caught:
                    if caught.errno not in (errno.EACCES, errno.EAGAIN):
                        raise
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.05)
            return directory_descriptor, lock_descriptor
        except (IOError, OSError, TypeError, ValueError):
            if lock_descriptor is not None:
                try:
                    os.close(lock_descriptor)
                except OSError:
                    pass
            try:
                os.close(directory_descriptor)
            except OSError:
                pass
            return None, None

    @staticmethod
    def _release_store_lock(directory_descriptor, lock_descriptor):
        if lock_descriptor is not None:
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_UN)
            except (IOError, OSError):
                pass
            try:
                os.close(lock_descriptor)
            except OSError:
                pass
        if directory_descriptor is not None:
            try:
                os.close(directory_descriptor)
            except OSError:
                pass

    def _load_state(self, directory_descriptor=None):
        filename = os.path.basename(self.path)
        try:
            if directory_descriptor is None:
                listed = os.lstat(self.path)
            else:
                listed = os.stat(
                    filename,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
        except FileNotFoundError:
            return {}, True
        except (IOError, OSError, TypeError, ValueError):
            return {}, False
        if (
            stat.S_ISLNK(listed.st_mode)
            or not stat.S_ISREG(listed.st_mode)
            or listed.st_size > MAX_CATEGORY_PREFERENCES_BYTES
        ):
            return {}, False

        descriptor = None
        try:
            flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            if directory_descriptor is None:
                descriptor = os.open(self.path, flags)
            else:
                descriptor = os.open(
                    filename,
                    flags,
                    dir_fd=directory_descriptor,
                )
            opened = os.fstat(descriptor)
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_dev != listed.st_dev
                or opened.st_ino != listed.st_ino
                or opened.st_size > MAX_CATEGORY_PREFERENCES_BYTES
            ):
                return {}, False
            with os.fdopen(descriptor, "r", encoding="utf-8") as handle:
                descriptor = None
                payload = json.load(handle)
        except (
            IOError,
            OSError,
            TypeError,
            ValueError,
            MemoryError,
            RecursionError,
        ):
            return {}, False
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
        try:
            return self._validated_sources(payload)
        except (TypeError, ValueError, MemoryError, RecursionError):
            return {}, False

    def load(self, account_scope):
        scope = _clean_scope(account_scope)
        if not scope:
            return CategoryPreferences()
        with _STORE_LOCK:
            directory_descriptor, lock_descriptor = (
                self._acquire_store_lock(exclusive=False)
            )
            if lock_descriptor is None:
                return CategoryPreferences()
            try:
                sources, unused_mutable = self._load_state(
                    directory_descriptor
                )
            finally:
                self._release_store_lock(
                    directory_descriptor,
                    lock_descriptor,
                )
        del unused_mutable
        entry = sources.get(scope, {})
        return CategoryPreferences(
            entry.get("order", ()),
            entry.get("hidden", ()),
            entry.get("include_terms", ()),
            entry.get("exclude_terms", ()),
        )

    def apply(self, account_scope, categories, include_hidden=False):
        return arrange_live_categories(
            categories,
            self.load(account_scope),
            include_hidden=include_hidden,
        )

    def _write_locked(self, sources, directory_descriptor=None):
        payload = {
            "version": _FORMAT_VERSION,
            "sources": sources,
        }
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        except (TypeError, ValueError, OverflowError, MemoryError, RecursionError):
            return False
        if len(encoded) > MAX_CATEGORY_PREFERENCES_BYTES:
            return False

        filename = os.path.basename(self.path)
        temporary = "{}.tmp-{}-{}".format(
            filename,
            os.getpid(),
            "{}-{}".format(threading.get_ident(), time.monotonic_ns()),
        )
        descriptor = None
        owned_directory_descriptor = None
        try:
            if directory_descriptor is None:
                owned_directory_descriptor = self._open_parent_directory(
                    create=True
                )
                directory_descriptor = owned_directory_descriptor
            if directory_descriptor is None:
                return False
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = os.open(
                temporary,
                flags,
                0o600,
                dir_fd=directory_descriptor,
            )
            with os.fdopen(descriptor, "wb") as handle:
                descriptor = None
                handle.write(encoded)
                handle.flush()
                os.fsync(handle.fileno())
            replacer = getattr(os, "replace", os.rename)
            replacer(
                temporary,
                filename,
                src_dir_fd=directory_descriptor,
                dst_dir_fd=directory_descriptor,
            )
            try:
                os.fsync(directory_descriptor)
            except (AttributeError, OSError):
                pass
            return True
        except (IOError, OSError, TypeError, ValueError):
            return False
        finally:
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if directory_descriptor is not None:
                try:
                    os.unlink(temporary, dir_fd=directory_descriptor)
                except OSError:
                    pass
            if owned_directory_descriptor is not None:
                try:
                    os.close(owned_directory_descriptor)
                except OSError:
                    pass

    def save(
        self,
        account_scope,
        ordered_ids,
        hidden_ids,
        include_terms=None,
        exclude_terms=None,
    ):
        scope = _clean_scope(account_scope)
        if not scope:
            return False
        if (
            (include_terms is not None and not valid_filter_terms(include_terms))
            or (exclude_terms is not None and not valid_filter_terms(exclude_terms))
        ):
            return False
        order = _clean_id_list(ordered_ids)
        hidden = set(_clean_id_list(hidden_ids))
        hidden.intersection_update(order)
        with _STORE_LOCK:
            directory_descriptor, lock_descriptor = (
                self._acquire_store_lock(exclusive=True)
            )
            if lock_descriptor is None:
                return False
            try:
                sources, mutable = self._load_state(directory_descriptor)
                if not mutable:
                    return False
                if (
                    scope not in sources
                    and len(sources) >= MAX_CATEGORY_SOURCES
                ):
                    return False
                previous = sources.get(scope, {})
                sources[scope] = {
                    "order": order,
                    "hidden": sorted(hidden),
                    "include_terms": (
                        list(clean_filter_terms(include_terms))
                        if include_terms is not None
                        else previous.get("include_terms", [])
                    ),
                    "exclude_terms": (
                        list(clean_filter_terms(exclude_terms))
                        if exclude_terms is not None
                        else previous.get("exclude_terms", [])
                    ),
                }
                return self._write_locked(
                    sources,
                    directory_descriptor=directory_descriptor,
                )
            finally:
                self._release_store_lock(
                    directory_descriptor,
                    lock_descriptor,
                )

    def reset(self, account_scope):
        scope = _clean_scope(account_scope)
        if not scope:
            return False
        with _STORE_LOCK:
            directory_descriptor, lock_descriptor = (
                self._acquire_store_lock(exclusive=True)
            )
            if lock_descriptor is None:
                return False
            try:
                sources, mutable = self._load_state(directory_descriptor)
                if not mutable:
                    return False
                if scope not in sources:
                    return True
                sources.pop(scope, None)
                return self._write_locked(
                    sources,
                    directory_descriptor=directory_descriptor,
                )
            finally:
                self._release_store_lock(
                    directory_descriptor,
                    lock_descriptor,
                )

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small account-scoped JSON category titles; content lists stay in RAM."""
from collections import OrderedDict
import hashlib
import json
import math
import os
import stat
import tempfile
import threading
import time
from types import SimpleNamespace

from .content import ContentCategory, CONTENT_LABELS
from .diagnostics import log_event
from .i18n import _
from .vod_catalog import vod_catalog_account_scope

DIRECTORY = '/etc/enigma2/gtiptvplayer/category-titles'
TTL_SECONDS = 24 * 60 * 60
MAX_BYTES = 8 * 1024 * 1024
MAX_CATEGORIES = 20000
_lock = threading.RLock()
_revisions = OrderedDict()


def _key(client, kind):
    source = str(getattr(client, 'source_type', 'xtream') or '').lower()
    if kind not in ('movie', 'series') or source not in ('xtream', 'stalker'):
        return ''
    scope = vod_catalog_account_scope(client)
    if not scope:
        return ''
    return hashlib.sha256((source + '\0' + scope + '\0' + kind).encode('utf-8')).hexdigest()


def purge_account_titles(account, directory=None):
    """Discard just the deleted account's titles and pending write tickets.

    Called after the account-file transaction succeeds. Cleanup is optional:
    an unwritable cache must not turn a committed deletion into an error.
    """
    if isinstance(account, tuple):
        account = SimpleNamespace(identity=account)
    client = SimpleNamespace(account=account,
        source_type=getattr(account, 'source_type', 'xtream'))
    keys = tuple(key for key in (_key(client, 'movie'), _key(client, 'series')) if key)
    if not keys:
        return 0
    removed = 0
    descriptor = None
    with _lock:
        for key in keys:
            # A request already fetching provider data cannot recreate the
            # deleted file when it eventually reaches its write step.
            _revisions.pop(key, None)
        try:
            descriptor = os.open(DIRECTORY if directory is None else directory,
                os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0) | getattr(os, 'O_NOFOLLOW', 0))
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                return 0
            for key in keys:
                name = key + '.json'
                try:
                    info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
                    if stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                        os.unlink(name, dir_fd=descriptor)
                        removed += 1
                except FileNotFoundError:
                    pass
                except OSError as error:
                    log_event('categories', 'title-cache-cleanup-skipped', error)
        except FileNotFoundError:
            pass
        except OSError as error:
            log_event('categories', 'title-cache-cleanup-skipped', error)
        finally:
            if descriptor is not None:
                os.close(descriptor)
    return removed


def _read(path, key, kind, now):
    descriptor = None
    try:
        if not stat.S_ISDIR(os.lstat(os.path.dirname(path)).st_mode):
            return None
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
                             | getattr(os, 'O_NONBLOCK', 0))
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or not 0 < info.st_size <= MAX_BYTES:
            return None
        with os.fdopen(descriptor, 'rb') as handle:
            descriptor = None
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None
        data = json.loads(raw.decode('utf-8'))
        if (not isinstance(data, dict) or data.get('schema') != 1
                or data.get('scope') != key or data.get('kind') != kind):
            return None
        created = data.get('created')
        if (isinstance(created, bool) or not isinstance(created, (int, float))
                or not math.isfinite(created) or not -300 <= now - created < TTL_SECONDS):
            return None
        rows = data.get('categories')
        if not isinstance(rows, list) or len(rows) > MAX_CATEGORIES:
            return None
        result = [ContentCategory('', _(CONTENT_LABELS[kind][1]))]
        seen = set()
        for row in rows:
            if not isinstance(row, list) or len(row) not in (2, 3):
                return None
            identity, name = row[:2]
            if (not isinstance(identity, str) or not identity or identity in seen
                    or len(identity.encode('utf-8')) > 128
                    or any(ord(c) < 32 or ord(c) == 127 for c in identity)
                    or not isinstance(name, str) or len(name.encode('utf-8')) > 4096):
                return None
            seen.add(identity)
            category = ContentCategory(identity, name)
            if len(row) == 3:
                aliases = row[2]
                if (not isinstance(aliases, list) or len(aliases) > 32
                        or any(not isinstance(value, str) or len(value.encode('utf-8')) > 4096
                               for value in aliases)):
                    return None
                category.portal_aliases = tuple(aliases)
            result.append(category)
        return result, data.get('provider_state', {})
    except (OSError, ValueError, TypeError, UnicodeError, RecursionError, MemoryError):
        return None
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _write(path, key, kind, categories, now, provider_state):
    temporary = None
    try:
        rows, seen = [], set()
        for category in categories:
            identity = str(getattr(category, 'category_id', '') or '')
            if not identity or identity in seen:
                continue
            name = str(getattr(category, 'name', '') or '')
            if (len(identity.encode('utf-8')) > 128
                    or any(ord(c) < 32 or ord(c) == 127 for c in identity)
                    or len(name.encode('utf-8')) > 4096):
                return False
            seen.add(identity)
            row = [identity, name]
            aliases = getattr(category, 'portal_aliases', ())
            if aliases:
                if (not isinstance(aliases, (list, tuple)) or len(aliases) > 32
                        or any(not isinstance(value, str) or len(value.encode('utf-8')) > 4096
                               for value in aliases)):
                    return False
                row.append(list(aliases))
            rows.append(row)
            if len(rows) > MAX_CATEGORIES:
                return False
        raw = (json.dumps(dict(schema=1, scope=key, kind=kind, created=now,
                              categories=rows, provider_state=provider_state), ensure_ascii=False,
                          separators=(',', ':')) + '\n').encode('utf-8')
        if len(raw) > MAX_BYTES:
            return False
        directory = os.path.dirname(path)
        os.makedirs(directory, mode=0o700, exist_ok=True)
        if not stat.S_ISDIR(os.lstat(directory).st_mode) or os.path.islink(path):
            return False
        descriptor, temporary = tempfile.mkstemp(prefix='.categories-', dir=directory)
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
        return True
    except (OSError, ValueError, TypeError, UnicodeError, MemoryError):
        return False
    finally:
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def load_category_titles(client, kind, force=False, directory=None, clock=None):
    """A cache failure never prevents fresh provider categories from opening."""
    key = _key(client, kind)
    if not key:
        return client.load_categories(kind)
    now = (clock or time.time)()
    path = os.path.join(DIRECTORY if directory is None else directory, key + '.json')
    with _lock:
        if force or key not in _revisions:
            _revisions[key] = object()
        ticket = _revisions[key]
        _revisions.move_to_end(key)
        while len(_revisions) > 512:
            _revisions.popitem(last=False)
    if not force:
        cached = _read(path, key, kind, now)
        if cached is not None:
            categories, state = cached
            if str(getattr(client, 'source_type', '')) != 'stalker':
                return categories
            # A portal category request can select a MAG profile and VOD
            # dialect. Reuse titles only when that client state can be restored.
            restore = getattr(client, 'restore_category_title_cache', None)
            with _lock:
                # A superseded reader must not prime a newly refreshed portal
                # client with old aliases/dialect state.
                if _revisions.get(key) is not ticket:
                    return categories
                try:
                    if callable(restore) and restore(kind, categories, state):
                        return categories
                except Exception:
                    pass
    elif callable(getattr(client, 'invalidate_cache', None)):
        try:
            client.invalidate_cache(kind)
        except Exception:
            pass
    categories = list(client.load_categories(kind) or ())
    provider_state = {}
    export = getattr(client, 'category_title_cache_state', None)
    if callable(export):
        try:
            provider_state = export(kind)
        except Exception:
            pass
    with _lock:
        # An older request cannot overwrite a newer GREEN refresh.
        if _revisions.get(key) is ticket and not _write(path, key, kind, categories, now, provider_state):
            log_event('categories', 'title-cache-write-skipped kind=' + kind)
    return categories

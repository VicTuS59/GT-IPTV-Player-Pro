# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Persistent, bounded movie/series catalogues with category checkpoints.

Only compact metadata is stored. Provider passwords and playable signed links
never belong in this cache. Each worker owns its SQLite connection; GUI code
only consumes small pages and coalesced progress messages.
"""

from contextlib import contextmanager
import fcntl
import json
from collections import OrderedDict
import os
import stat
import threading
import time

from .content import ContentError, ContentItem, MovieSearchPage, _movie_search_text
from .category_preferences import content_item_category_ids
from .diagnostics import log_event
from .portal_identity import safe_series_id
from .vod_catalog import (
    DEFAULT_VOD_CATALOG_DIR, VodCatalogCache, _category_values,
    vod_catalog_account_scope,
)


CATALOG_TTL = 24 * 60 * 60
CATALOG_MAX_ITEMS = 250000
CATALOG_MAX_BYTES = 96 * 1024 * 1024
CATALOG_FREE_BYTES = 16 * 1024 * 1024
CATALOG_BATCH = 500
CATALOG_MAX_ACCOUNTS = 3
_locks_guard = threading.Lock()
_locks = {}
_default_backends = {}


def _cache_directories(scope, namespace=""):
    """Resolve receiver system links, but never follow the account cache file."""
    if DEFAULT_VOD_CATALOG_DIR.startswith("/etc/enigma2/"):
        legacy = os.path.join(os.path.realpath("/etc/enigma2"), "gtiptvplayer/vod-catalog/index")
    else:
        legacy = os.path.join(os.path.realpath(os.path.dirname(DEFAULT_VOD_CATALOG_DIR)),
                              os.path.basename(DEFAULT_VOD_CATALOG_DIR), "index")
    if namespace:
        legacy = os.path.join(legacy, namespace)
    candidates = []
    if os.path.isfile(os.path.join(legacy, scope + ".sqlite3")):
        candidates.append(legacy)
    for mount in ("/media/hdd", "/media/usb"):
        if os.path.ismount(os.path.realpath(mount)):
            path = os.path.join(os.path.realpath(mount), "gtiptvplayer/cache/vod-catalog/index")
            candidates.append(os.path.join(path, namespace) if namespace else path)
    temporary = os.path.join(os.path.realpath("/tmp"), "gtiptvplayer/vod-catalog/index")
    candidates.extend((legacy, os.path.join(temporary, namespace) if namespace else temporary))
    result = []
    for path in candidates:
        # Only trusted, fixed system parents are canonicalized. The final
        # private directory and every account file still reject symlinks.
        if path not in result:
            result.append(path)
    return result


def _check(token):
    if token is not None:
        token.check()


class CatalogStorageError(Exception):
    pass


class CatalogLimitError(Exception):
    pass


class _SqliteMovieCatalogIndex(object):
    content_type = "movie"
    stream_method = "iter_movie_catalog_items"
    storage_namespace = ""

    @staticmethod
    def _valid_id(value):
        return str(value or "").isdigit()

    def _record(self, item):
        return self.codec._record(item)

    def __init__(self, client, directory=None, clock=None, max_items=CATALOG_MAX_ITEMS,
                 max_bytes=CATALOG_MAX_BYTES, free_bytes=CATALOG_FREE_BYTES):
        self.client = client
        self.clock = clock or time.time
        self.directory = directory or os.path.join(DEFAULT_VOD_CATALOG_DIR, "index")
        if self.storage_namespace:
            self.directory = os.path.join(self.directory, self.storage_namespace)
        self.scope = vod_catalog_account_scope(client)
        self.backend_key = (self.scope, self.storage_namespace) if self.storage_namespace else self.scope
        self.path = os.path.join(self.directory, self.scope + ".sqlite3")
        self.codec = VodCatalogCache(client, directory=self.directory)
        self.max_items = max(1, int(max_items))
        self.max_bytes = max(65536, int(max_bytes))
        self.free_bytes = max(0, int(free_bytes))
        with _locks_guard:
            self.lock = _locks.setdefault(self.path, threading.RLock())

    def _safe_files(self):
        if not self.scope:
            raise CatalogStorageError()
        directory_fd = self.codec._directory_fd(create=True)
        if directory_fd is None:
            raise CatalogStorageError()
        try:
            for suffix in ("", "-wal", "-shm", ".lock"):
                filename = self.scope + ".sqlite3" + suffix
                try:
                    info = os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise CatalogStorageError()
            filename = self.scope + ".sqlite3"
            fd = os.open(filename, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600,
                         dir_fd=directory_fd)
            os.fchmod(fd, 0o600)
            os.close(fd)
        finally:
            os.close(directory_fd)

    @contextmanager
    def _connection(self, token=None):
        # sqlite3 is optional on receiver images; the facade can select the
        # portable backend when the module or a usable database is unavailable.
        import sqlite3
        connection = None
        try:
            with self.lock:
                self._safe_files()
                connection = sqlite3.connect(self.path, timeout=2.0)
                if connection.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
                    connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=NORMAL")
                connection.execute("PRAGMA cache_size=-2048")
                connection.execute("PRAGMA temp_store=FILE")
                connection.execute("PRAGMA wal_autocheckpoint=256")
                page_size = connection.execute("PRAGMA page_size").fetchone()[0]
                connection.execute("PRAGMA max_page_count={}".format(self.max_bytes // page_size))
                self._initialize_schema(connection)
            if token is not None:
                def progress():
                    try:
                        _check(token)
                        return 0
                    except Exception:
                        return 1
                connection.set_progress_handler(progress, 2000)
            _check(token)
            yield connection
        except sqlite3.Error as error:
            _check(token)
            log_event("movie-cache", "SQLite operation failed: {}".format(str(error)), error=error)
            if "full" in str(error).lower():
                raise CatalogLimitError() from error
            raise CatalogStorageError() from error
        except (OSError, ImportError) as error:
            log_event("movie-cache", "Cache access failed: {}".format(str(error)), error=error)
            raise CatalogStorageError() from error
        finally:
            if connection is not None:
                connection.close()

    def _initialize_schema(self, connection):
        connection.executescript("""
            CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY,v TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS movies(
                gen INTEGER NOT NULL,id TEXT NOT NULL,name TEXT NOT NULL,
                terms TEXT NOT NULL,record TEXT NOT NULL,
                PRIMARY KEY(gen,id)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS memberships(
                gen INTEGER NOT NULL,id TEXT NOT NULL,source TEXT NOT NULL,cid TEXT NOT NULL,
                PRIMARY KEY(gen,id,source,cid)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS memberships_category ON memberships(gen,cid,id);
            CREATE TABLE IF NOT EXISTS visits(
                gen INTEGER NOT NULL,id TEXT NOT NULL,source TEXT NOT NULL,attempt INTEGER NOT NULL,
                PRIMARY KEY(gen,id,source)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS categories(
                gen INTEGER NOT NULL,id TEXT NOT NULL,name TEXT NOT NULL,
                done INTEGER NOT NULL DEFAULT 0,
                PRIMARY KEY(gen,id)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS browse_order(
                seq INTEGER PRIMARY KEY AUTOINCREMENT,id TEXT NOT NULL UNIQUE);
        """)
        if self._get(connection, "browse_order_ready") != "1":
            self._free_space()
            connection.execute("INSERT OR IGNORE INTO browse_order(id) SELECT id FROM movies ORDER BY gen,id")
            self._set(connection, "browse_order_ready", 1)
            connection.commit()

    @staticmethod
    def _get(connection, key, default="0"):
        row = connection.execute("SELECT v FROM meta WHERE k=?", (key,)).fetchone()
        return row[0] if row else default

    @staticmethod
    def _set(connection, key, value):
        connection.execute("INSERT OR REPLACE INTO meta VALUES(?,?)", (key, str(value)))

    def _generations(self, connection):
        return (int(self._get(connection, "active")), int(self._get(connection, "working")))

    @staticmethod
    def _visible_sql(active, working):
        if working:
            return ("SELECT * FROM movies WHERE gen={w} UNION ALL "
                    "SELECT * FROM movies a WHERE gen={a} AND NOT EXISTS "
                    "(SELECT 1 FROM movies w WHERE w.gen={w} AND w.id=a.id)").format(w=working, a=active)
        return "SELECT * FROM movies WHERE gen={}".format(active)

    def _state(self, connection):
        active, working = self._generations(connection)
        revision = self._get(connection, "revision")
        count_key = (active, working, revision)
        cached = getattr(self, "_count_cache", None)
        if cached is not None and cached[0] == count_key:
            total = cached[1]
        else:
            total = connection.execute("SELECT COUNT(*) FROM ({})".format(
                self._visible_sql(active, working))).fetchone()[0]
            self._count_cache = (count_key, total)
        done, categories = connection.execute(
            "SELECT COALESCE(SUM(done),0),COUNT(*) FROM categories WHERE gen=?",
            (working or active,)).fetchone()
        return dict(movies=total, complete=bool(active and not working),
                    updated=float(self._get(connection, "updated")),
                    done=done, total=categories, reason=self._get(connection, "reason", ""),
                    working=bool(working))

    def state(self):
        with self._connection() as connection:
            return self._state(connection)

    def search(self, query, page=1, page_size=14, hidden_category_ids=(), token=None):
        page, page_size = max(1, int(page)), max(1, min(100, int(page_size)))
        terms = tuple(dict.fromkeys(_movie_search_text(query)[:240].split()))[:30]
        with self._connection(token) as connection:
            # A read transaction gives count, rows and completion the same view
            # while the other worker commits the next small batch.
            connection.execute("BEGIN")
            state = self._state(connection)
            active, working = self._generations(connection)
            connection.execute("CREATE TEMP TABLE scope(cid TEXT PRIMARY KEY,hidden INTEGER,allowed INTEGER)")
            hidden = {str(value) for value in hidden_category_ids}
            allowed = {str(value) for value in getattr(hidden_category_ids, "allowed", ())}
            connection.executemany("INSERT INTO scope VALUES(?,?,?)",
                                   ((value, int(value in hidden), int(value in allowed))
                                    for value in hidden.union(allowed)))
            conditions = ["m.terms LIKE ?" for term in terms] or ["0"]
            # A duplicate film in ANY hidden category remains hidden, including
            # when another copy is in the generation currently being built.
            conditions.append("NOT EXISTS (SELECT 1 FROM memberships c JOIN scope s ON s.cid=c.cid "
                              "WHERE c.id=m.id AND c.gen IN ({},{}) AND s.hidden=1)".format(active, working))
            if hasattr(hidden_category_ids, "allowed"):
                conditions.append("EXISTS (SELECT 1 FROM memberships c JOIN scope s ON s.cid=c.cid "
                                  "WHERE c.id=m.id AND c.gen IN ({},{}) AND s.allowed=1)".format(active, working))
            sql = "WITH visible AS ({}) SELECT {{}} FROM visible m WHERE {}".format(
                self._visible_sql(active, working), " AND ".join(conditions))
            parameters = ["%" + term + "%" for term in terms]
            total = connection.execute(sql.format("COUNT(*)"), parameters).fetchone()[0]
            rows = connection.execute(sql.format("m.gen,m.id,m.record") +
                " ORDER BY CASE WHEN m.name=? THEN 0 WHEN m.name LIKE ? THEN 1 ELSE 2 END,m.name,m.id LIMIT ? OFFSET ?",
                parameters + [_movie_search_text(query), _movie_search_text(query) + "%",
                              page_size, (page - 1) * page_size]).fetchall()
            memberships = self._page_memberships(connection, [row[1] for row in rows], active, working)
            items = []
            for gen, movie_id, record in rows:
                _check(token)
                item = self._hydrate(json.loads(record))
                if item is not None:
                    item.portal_category_ids = tuple(memberships.get(movie_id, ()))
                    if item.portal_category_ids and item.category_id not in item.portal_category_ids:
                        item.category_id = item.catalog_category_id = item.portal_category_ids[0]
                    items.append(item)
            result = MovieSearchPage(items, page, page_size, total,
                                     total > page * page_size, source="catalog")
            result.catalog_state = state
            return result

    def _hydrate(self, record):
        item = self.codec._item(record)
        if item is None and record.get("i") and str(getattr(self.client, "source_type", "")) == "stalker":
            # Recover non-persistable portal commands through authenticated
            # create_link/category lookup at playback, never store signed URLs.
            item = ContentItem("movie", record["i"], record["n"],
                               category_id=record.get("c", ""), extension=record.get("x", "mp4"),
                               year=record.get("y", ""), rating=record.get("r", ""), icon=record.get("p", ""))
            item.catalog_category_id = record.get("c", "")
            item.catalog_category_name = record.get("g", "")
            item.portal_command = ""
            item.portal_command_is_fallback = True
            item.portal_request_type = record.get("q", "vod")
            item.portal_series = "0"
        if item is not None:
            item.content_type = self.content_type
            item.tmdb_id, item.imdb_id = record.get("t", ""), record.get("b", "")
            if "j" in record:
                item.portal_catalog_page = int(record["j"])
        return item

    def _page_memberships(self, connection, ids, active, working):
        if not ids:
            return {}
        sql = "SELECT DISTINCT id,cid FROM memberships WHERE gen IN (?,?) AND id IN ({})".format(
            ",".join("?" for unused in ids))
        result = {}
        for identity, category in connection.execute(sql, [active, working] + list(ids)):
            result.setdefault(identity, []).append(category)
        return result

    def browse(self, category_id="", after=0, page_size=80, hidden_category_ids=(), token=None, include_total=True):
        """Seek by stable sequence; a normal page never materializes the catalogue."""
        after, page_size = max(0, int(after)), max(1, min(250, int(page_size)))
        category_id = str(category_id or "")
        with self._connection(token) as connection:
            connection.execute("BEGIN")
            active, working = self._generations(connection)
            revision = self._get(connection, "revision")
            hidden = {str(value) for value in hidden_category_ids}
            positive = hasattr(hidden_category_ids, "allowed")
            allowed = {str(value) for value in getattr(hidden_category_ids, "allowed", ())}
            connection.execute("CREATE TEMP TABLE scope(cid TEXT PRIMARY KEY,hidden INTEGER,allowed INTEGER)")
            connection.executemany("INSERT INTO scope VALUES(?,?,?)",
                ((value, int(value in hidden), int(value in allowed)) for value in hidden.union(allowed)))
            conditions = ["(w.id IS NOT NULL OR a.id IS NOT NULL)"]
            if hidden:
                conditions.append("NOT EXISTS (SELECT 1 FROM memberships c JOIN scope s ON s.cid=c.cid "
                    "WHERE c.id=o.id AND c.gen IN ({},{}) AND s.hidden=1)".format(active, working))
            if positive:
                conditions.append("EXISTS (SELECT 1 FROM memberships c JOIN scope s ON s.cid=c.cid "
                    "WHERE c.id=o.id AND c.gen IN ({},{}) AND s.allowed=1)".format(active, working))
            parameters = []
            category_seek = (bool(category_id) and self.content_type == "movie"
                             and str(getattr(self.client, "source_type", "")) == "xtream")
            if category_id and not category_seek:
                conditions.append("EXISTS (SELECT 1 FROM memberships c WHERE c.id=o.id "
                    "AND c.gen IN ({},{}) AND c.cid=?)".format(active, working))
                parameters.append(category_id)
            # LEFT JOIN fixes the loop order: seq range first, indexed point
            # reads for at most the candidates needed to fill one page.
            sql = (" FROM browse_order o LEFT JOIN movies w ON w.gen={w} AND w.id=o.id "
                   "LEFT JOIN movies a ON a.gen={a} AND a.id=o.id WHERE ").format(w=working, a=active)
            if category_seek:
                # Begin at indexed category members. Scanning the global
                # sequence until a late/small category fills its first page
                # makes category navigation slower than All Movies.
                sql = (" FROM (SELECT DISTINCT id FROM memberships "
                       "WHERE gen IN ({a},{w}) AND cid=?) matched "
                       "CROSS JOIN browse_order o "
                       "LEFT JOIN movies w ON w.gen={w} AND w.id=o.id "
                       "LEFT JOIN movies a ON a.gen={a} AND a.id=o.id "
                       "WHERE o.id=matched.id AND ").format(w=working, a=active)
                parameters.append(category_id)
            sql += " AND ".join(conditions)
            totals = getattr(self, "_browse_totals", None)
            if totals is None:
                totals = self._browse_totals = OrderedDict()
            total_key = (active, working, revision, category_id, frozenset(hidden), positive, frozenset(allowed))
            total = totals.get(total_key)
            if total is None and include_total:
                total = connection.execute("SELECT COUNT(*)" + sql, parameters).fetchone()[0]
                totals[total_key] = total
                while len(totals) > 32:
                    totals.popitem(last=False)
            rows = connection.execute("SELECT o.seq,o.id,COALESCE(w.record,a.record)" + sql +
                " AND o.seq>? ORDER BY o.seq LIMIT ?", parameters + [after, page_size + 1]).fetchall()
            memberships = self._page_memberships(connection, [row[1] for row in rows[:page_size]], active, working)
            items, cursor = [], after
            for sequence, movie_id, record in rows[:page_size]:
                _check(token)
                cursor = sequence
                item = self._hydrate(json.loads(record))
                if item is not None:
                    item.portal_category_ids = tuple(memberships.get(movie_id, ()))
                    items.append(item)
            # Producer callbacks own catalogue-sized counts. The UI uses its
            # producer snapshot; this inexpensive view is sufficient otherwise.
            state = dict(movies=total or 0, complete=bool(active and not working),
                         working=bool(working), reason=self._get(connection, "reason", ""))
            result = MovieSearchPage(items, page_size=page_size, total_items=total or 0,
                                     has_more=len(rows) > page_size, source="catalog")
            result.next_cursor, result.catalog_state = cursor, state
            return result

    def _free_space(self):
        info = os.statvfs(self.directory)
        block_size = info.f_frsize or info.f_bsize
        if info.f_bavail * block_size < self.free_bytes:
            raise CatalogLimitError()

    def _write(self, connection, generation, batch, category_id, category_name, source, attempt):
        self._free_space()
        for item in batch:
            record = self._record(item)
            if not record["i"]:
                continue
            record["t"] = str(getattr(item, "tmdb_id", "") or "")[:32]
            record["b"] = str(getattr(item, "imdb_id", "") or "")[:32]
            indexed_page = getattr(item, "portal_catalog_page", None)
            if isinstance(indexed_page, int) and 0 <= indexed_page <= 20000:
                record["j"] = indexed_page
            if category_id:
                record["c"], record["g"] = category_id, category_name
            name = _movie_search_text(record["n"])
            connection.execute("INSERT OR REPLACE INTO movies VALUES(?,?,?,?,?)",
                (generation, record["i"], name, name + " " + _movie_search_text(record["y"]),
                 json.dumps(record, ensure_ascii=False, separators=(",", ":"))))
            connection.execute("INSERT OR IGNORE INTO browse_order(id) VALUES(?)", (record["i"],))
            connection.execute("DELETE FROM memberships WHERE gen=? AND id=? AND source=? AND NOT EXISTS "
                               "(SELECT 1 FROM visits WHERE gen=? AND id=? AND source=? AND attempt=?)",
                               (generation, record["i"], source, generation, record["i"], source, attempt))
            connection.execute("INSERT OR REPLACE INTO visits VALUES(?,?,?,?)",
                               (generation, record["i"], source, attempt))
            ids = set(content_item_category_ids(item))
            if category_id:
                ids.add(category_id)
            connection.executemany("INSERT OR IGNORE INTO memberships VALUES(?,?,?,?)",
                ((generation, record["i"], source, str(cid)[:40]) for cid in ids if str(cid)))
        count = connection.execute("SELECT COUNT(*) FROM movies WHERE gen=?", (generation,)).fetchone()[0]
        if count > self.max_items:
            connection.rollback()
            raise CatalogLimitError()
        self._set(connection, "revision", int(self._get(connection, "revision")) + 1)
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)")

    @contextmanager
    def _request(self, token):
        scope = getattr(self.client, "request_scope", None)
        _check(token)
        if callable(scope):
            with scope(cancel_event=getattr(token, "cancel_event", None),
                       deadline=getattr(token, "deadline", None)):
                yield
        else:
            yield
        _check(token)

    def _collect(self, connection, generation, category_id, category_name, token, notify):
        batch = []
        source = "primary" if not category_id and self._get(connection, "phase", "primary") == "primary" else "category:" + category_id
        attempt = time.monotonic_ns()
        try:
            stream = getattr(self.client, self.stream_method, None)
            if callable(stream):
                batch_limit = 14 if str(getattr(self.client, "source_type", "")) == "stalker" else CATALOG_BATCH
                with self._request(token):
                    for item in stream(category_id):
                        _check(token)
                        batch.append(item)
                        if len(batch) >= batch_limit:
                            self._write(connection, generation, batch, category_id, category_name, source, attempt)
                            batch = []
                            notify()
            else:
                stepper = getattr(self.client, "load_vod_catalog_step", None) if self.content_type == "movie" else None
                if not callable(stepper):
                    raise ContentError(reason=ContentError.UNSUPPORTED_TYPE)
                cursor = 0
                while True:
                    with self._request(token):
                        step = stepper(category_id, cursor, limit=250)
                    batch.extend(step.items)
                    # Portal pages become durable immediately, rather than
                    # waiting for 500 rows or the whole category to finish.
                    self._write(connection, generation, batch, category_id, category_name, source, attempt)
                    batch = []
                    notify()
                    if step.complete:
                        if not step.trusted_complete:
                            raise ContentError(reason="incomplete")
                        break
                    if not step.progressed or step.cursor < cursor:
                        raise ContentError(reason="incomplete")
                    cursor = step.cursor
                    event = getattr(token, "cancel_event", None)
                    if event is not None:
                        event.wait(0.12)
                    _check(token)
        finally:
            # Keep the last short batch even when transport is interrupted.
            if batch:
                self._write(connection, generation, batch, category_id, category_name, source, attempt)
        # Only a validated EOF permits removing rows missing from this retry.
        connection.execute("DELETE FROM visits WHERE gen=? AND source=? AND attempt<>?",
                           (generation, source, attempt))
        self._remove_orphans(connection, generation)
        connection.commit()

    @staticmethod
    def _remove_orphans(connection, generation):
        connection.execute("DELETE FROM memberships WHERE gen=? AND NOT EXISTS "
                           "(SELECT 1 FROM visits v WHERE v.gen=memberships.gen AND v.id=memberships.id AND v.source=memberships.source)", (generation,))
        connection.execute("DELETE FROM movies WHERE gen=? AND NOT EXISTS "
                           "(SELECT 1 FROM visits v WHERE v.gen=movies.gen AND v.id=movies.id)", (generation,))

    def _prune_accounts(self):
        """Bound old account caches, without deleting one in active use."""
        candidates = []
        for filename in os.listdir(self.directory):
            stem = filename[:-8] if filename.endswith(".sqlite3") else ""
            if len(stem) != 64 or any(c not in "0123456789abcdef" for c in stem):
                continue
            path = os.path.join(self.directory, filename)
            try:
                info = os.lstat(path)
            except FileNotFoundError:
                continue
            if stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                if path != self.path:
                    candidates.append((info.st_mtime, path))
        candidates.sort(reverse=True)
        for _, path in candidates[CATALOG_MAX_ACCOUNTS - 1:]:
            descriptor = -1
            try:
                descriptor = os.open(path + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                for suffix in ("", "-wal", "-shm"):
                    try:
                        os.unlink(path + suffix)
                    except FileNotFoundError:
                        pass
            except OSError:
                pass
            finally:
                if descriptor >= 0:
                    os.close(descriptor)

    def prepare(self, token=None, callback=None, force=False):
        """Build/resume in a background worker; failed refresh retains active data."""
        descriptor = -1
        last_notification = [0.0]
        connection = None
        final_state = dict(movies=0, complete=False, reason="storage", done=0, total=0)
        def notify(final=False):
            now = time.monotonic()
            if final or now - last_notification[0] >= 0.4:
                last_notification[0] = now
                state = self._state(connection)
                state["preparing"] = not final
                if callable(callback):
                    callback(state)
        try:
            with self._connection() as connection:
                descriptor = os.open(self.path + ".lock", os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                lock_deadline = time.monotonic() + 15.0
                while True:
                    _check(token)
                    try:
                        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except BlockingIOError:
                        if time.monotonic() >= lock_deadline:
                            final_state = self._state(connection)
                            final_state.update(reason="busy", complete=False)
                            return final_state
                        event = getattr(token, "cancel_event", None)
                        if event is not None:
                            event.wait(0.05)
                        else:
                            time.sleep(0.05)
                _check(token)
                self._prune_accounts()
                state = self._state(connection)
                if state["complete"] and not force and 0 <= self.clock() - state["updated"] < CATALOG_TTL:
                    notify(final=True)
                    return state
                active, working = self._generations(connection)
                if not working:
                    working = max(active + 1, 1)
                    self._set(connection, "working", working)
                    self._set(connection, "phase", "primary")
                self._set(connection, "reason", "")
                connection.commit()
                notify()
                try:
                    primary = self._get(connection, "phase", "primary") == "primary"
                    if (primary and str(getattr(self.client, "source_type", "")) != "stalker"
                            and callable(getattr(self.client, self.stream_method, None))):
                        try:
                            self._collect(connection, working, "", "", token, notify)
                            self._publish(connection, working, token)
                            notify(final=True)
                            return self._state(connection)
                        except ContentError as error:
                            _check(token)
                            if error.reason in (ContentError.ACCESS_DENIED, ContentError.RATE_LIMITED):
                                raise
                            self._set(connection, "phase", "categories")
                            connection.commit()
                    if not connection.execute("SELECT 1 FROM categories WHERE gen=? LIMIT 1", (working,)).fetchone():
                        with self._request(token):
                            categories = self.client.load_categories(self.content_type)
                        values = dict(_category_values(category) for category in categories)
                        values.pop("", None)
                        if not values:
                            values = {"": ""}
                        connection.executemany("INSERT OR IGNORE INTO categories(gen,id,name) VALUES(?,?,?)",
                                               ((working, key, value) for key, value in values.items()))
                        self._set(connection, "phase", "categories")
                        connection.commit()
                    failures, reason = 0, ""
                    categories = connection.execute("SELECT id,name FROM categories WHERE gen=? AND done=0 ORDER BY id", (working,)).fetchall()
                    for category_id, category_name in categories:
                        _check(token)
                        try:
                            self._collect(connection, working, category_id, category_name, token, notify)
                            connection.execute("UPDATE categories SET done=1 WHERE gen=? AND id=?", (working, category_id))
                            connection.commit()
                            failures = 0
                        except ContentError as error:
                            _check(token)
                            reason = error.reason
                            failures += 1
                            if reason in (ContentError.ACCESS_DENIED, ContentError.RATE_LIMITED) or failures >= 2:
                                raise
                        notify()
                        event = getattr(token, "cancel_event", None)
                        if event is not None:
                            event.wait(0.12)
                    remaining = connection.execute("SELECT COUNT(*) FROM categories WHERE gen=? AND done=0", (working,)).fetchone()[0]
                    if remaining:
                        self._set(connection, "reason", reason or "incomplete")
                        connection.commit()
                    else:
                        self._publish(connection, working, token)
                except Exception as error:
                    connection.rollback()
                    reason = getattr(error, "reason", "") or (
                        "limit" if isinstance(error, CatalogLimitError) else
                        "storage" if isinstance(error, (CatalogStorageError, OSError)) else "incomplete")
                    if reason in (ContentError.CATALOG_ITEM_LIMIT, ContentError.RESPONSE_TOO_LARGE):
                        reason = "limit"
                    if getattr(getattr(token, "cancel_event", None), "is_set", lambda: False)():
                        reason = "cancelled"
                    self._set(connection, "reason", reason)
                    connection.commit()
                notify(final=True)
                final_state = self._state(connection)
        except (CatalogStorageError, CatalogLimitError, OSError, ImportError):
            pass
        finally:
            if descriptor >= 0:
                os.close(descriptor)
        final_state["preparing"] = False
        if callable(callback):
            callback(final_state)
        return final_state

    def _publish(self, connection, generation, token):
        _check(token)
        # A single transaction replaces the last complete generation only
        # after every category (or the complete array EOF) has been validated.
        if self._get(connection, "phase", "primary") == "categories":
            connection.execute("DELETE FROM visits WHERE gen=? AND source='primary'", (generation,))
            self._remove_orphans(connection, generation)
        for table in ("movies", "memberships", "categories", "visits"):
            connection.execute("DELETE FROM {} WHERE gen<>?".format(table), (generation,))
        connection.execute("DELETE FROM browse_order WHERE NOT EXISTS "
                           "(SELECT 1 FROM movies m WHERE m.gen=? AND m.id=browse_order.id)", (generation,))
        self._set(connection, "active", generation)
        self._set(connection, "working", 0)
        self._set(connection, "updated", self.clock())
        self._set(connection, "reason", "")
        _check(token)
        self._set(connection, "revision", int(self._get(connection, "revision")) + 1)
        connection.commit()
        connection.execute("PRAGMA wal_checkpoint(PASSIVE)")

class MovieCatalogIndex(_SqliteMovieCatalogIndex):
    """Select a usable backend in a worker, without doing filesystem I/O in UI."""

    def __init__(self, client, directory=None, clock=None, max_items=CATALOG_MAX_ITEMS,
                 max_bytes=CATALOG_MAX_BYTES, free_bytes=CATALOG_FREE_BYTES,
                 allow_fallback=None):
        super().__init__(client, directory, clock, max_items, max_bytes, free_bytes)
        self._automatic_storage = directory is None
        self._allow_fallback = self._automatic_storage if allow_fallback is None else bool(allow_fallback)
        self._backend = None
        self._portable = None

    @property
    def backend_name(self):
        return self._backend or ""

    def _select_backend(self, token=None):
        if self._backend is not None:
            return
        with self.lock:
            if self._backend is not None:
                return
            if self._automatic_storage:
                paths = (_cache_directories(self.scope, self.storage_namespace)
                         if self.storage_namespace else _cache_directories(self.scope))
            else:
                paths = [self.directory]
            remembered = _default_backends.get(self.backend_key) if self._automatic_storage else None
            if remembered is not None and remembered[1] in paths:
                mode, path = remembered
                paths = [path] + [value for value in paths if value != path]
            errors = []
            for path in paths:
                _check(token)
                self.directory = path
                self.path = os.path.join(path, self.scope + ".sqlite3")
                self.codec = VodCatalogCache(self.client, directory=path)
                try:
                    if self._automatic_storage:
                        self._free_space_if_existing()
                    with super()._connection(token):
                        pass
                    self._backend = "sqlite"
                    break
                except (CatalogStorageError, CatalogLimitError, ImportError, OSError) as error:
                    errors.append(error)
                    log_event("movie-cache", "Cannot initialize SQLite cache at {}: {}".format(path, str(error)), error=error)
                    if not self._allow_fallback:
                        raise
            if self._backend is None and self._allow_fallback:
                from .movie_cache_compat import PortableMovieCatalog
                for path in paths:
                    _check(token)
                    self.directory = path
                    self.path = os.path.join(path, self.scope + ".sqlite3")
                    self.codec = VodCatalogCache(self.client, directory=path)
                    try:
                        portable = PortableMovieCatalog(self)
                        portable.probe()
                        self._portable = portable
                        self._backend = "portable"
                        break
                    except (CatalogStorageError, CatalogLimitError, OSError) as error:
                        errors.append(error)
                        log_event("movie-cache", "Cannot initialize portable cache at {}: {}".format(path, str(error)), error=error)
            if self._backend is None:
                if errors:
                    raise errors[-1]
                raise CatalogStorageError()
            if self._automatic_storage:
                _default_backends[self.backend_key] = (self._backend, self.directory)
            log_event("movie-cache", "Using {} catalogue at {}".format(self._backend, self.directory))

    def _free_space_if_existing(self):
        if os.path.isdir(self.directory):
            self._free_space()

    def state(self):
        self._select_backend()
        return self._portable.state() if self._portable else super().state()

    def search(self, query, page=1, page_size=14, hidden_category_ids=(), token=None):
        self._select_backend(token)
        if self._portable:
            return self._portable.search(query, page, page_size, hidden_category_ids, token)
        return super().search(query, page, page_size, hidden_category_ids, token)

    def browse(self, category_id="", after=0, page_size=80, hidden_category_ids=(), token=None, include_total=True):
        self._select_backend(token)
        if self._portable:
            return self._portable.browse(category_id, after, page_size, hidden_category_ids, token)
        return super().browse(category_id, after, page_size, hidden_category_ids, token, include_total)

    def prepare(self, token=None, callback=None, force=False):
        try:
            self._select_backend(token)
        except (CatalogStorageError, CatalogLimitError, OSError, ImportError):
            state = dict(movies=0, complete=False, preparing=False, reason="storage", done=0, total=0)
            if callable(callback):
                callback(state)
            return state
        if self._portable:
            return self._portable.prepare(token, callback, force)
        return super().prepare(token, callback, force)


class SeriesCatalogIndex(MovieCatalogIndex):
    """Reuse the bounded engine without mixing series and movie identities."""
    content_type = "series"
    stream_method = "iter_series_catalog_items"
    storage_namespace = "series"

    def _valid_id(self, value):
        if str(getattr(self.client, "source_type", "")) == "stalker":
            return bool(safe_series_id(value))
        return super()._valid_id(value)

    def _record(self, item):
        record = super()._record(item)
        if str(getattr(self.client, "source_type", "")) == "stalker":
            record["i"] = safe_series_id(getattr(item, "stream_id", ""))
            request_type = getattr(item, "portal_request_type", "")
            record["q"] = request_type if request_type in ("series", "vod") else "series"
            parameter = getattr(item, "portal_catalog_category_parameter", "")
            record["h"] = parameter if parameter in ("category", "genre") else "category"
            flag = getattr(item, "portal_catalog_series_flag", "")
            record["f"] = flag if flag in ("series", "is_series") else ""
            # Series headers are API parents, never reusable playback links.
            record.pop("m", None)
        return record

    def _hydrate(self, record):
        if str(getattr(self.client, "source_type", "")) != "stalker":
            return super()._hydrate(record)
        identity = safe_series_id(record.get("i", ""))
        if not identity:
            return None
        item = ContentItem("series", identity, record.get("n", ""),
            category_id=record.get("c", ""), extension=record.get("x", "mp4"),
            icon=record.get("p", ""), year=record.get("y", ""), rating=record.get("r", ""),
            tmdb_id=record.get("t", ""), imdb_id=record.get("b", ""))
        item.catalog_category_id = record.get("c", "")
        item.catalog_category_name = record.get("g", "")
        item.portal_command = ""
        item.portal_series = "0"
        item.portal_request_type = record.get("q") if record.get("q") in ("series", "vod") else "series"
        item.portal_catalog_category_parameter = record.get("h") if record.get("h") in ("category", "genre") else "category"
        item.portal_catalog_series_flag = record.get("f") if record.get("f") in ("series", "is_series") else ""
        if isinstance(record.get("j"), int) and 0 <= record["j"] <= 20000:
            item.portal_catalog_page = record["j"]
        return item


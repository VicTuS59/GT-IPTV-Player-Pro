"""Paged, rebuildable history indexes. The original JSON remains authoritative.

Indexes never contain credentials and are not backups. Writes stream the JSON
to an atomic replacement; no history is truncated to fit an in-memory cache.
"""
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
import json
import os
import stat
import threading
import uuid

SUFFIX = '.history-index.sqlite3'
CHUNK = 65536
MAX_VALUE = 2 * 1024 * 1024
PAGE_SIZE = 100
MAX_INDEX_BYTES = 8 * 1024 * 1024
_LOCK = threading.RLock()
_DECLINED = OrderedDict()


def _sqlite():
    import sqlite3
    return sqlite3


class Reader:
    def __init__(self, source):
        self.source, self.text, self.pos, self.eof = source, '', 0, False
        self.decoder = json.JSONDecoder()

    def fill(self):
        self.text = self.text[self.pos:]; self.pos = 0
        part = self.source.read(CHUNK)
        self.eof = not part
        self.text += part
        if len(self.text) > MAX_VALUE:
            raise ValueError('History record too large')

    def peek(self):
        while True:
            while self.pos < len(self.text) and self.text[self.pos].isspace(): self.pos += 1
            if self.pos < len(self.text): return self.text[self.pos]
            if self.eof: return ''
            self.fill()

    def take(self, char):
        if self.peek() != char: raise ValueError('Invalid history document')
        self.pos += 1

    def value(self):
        if not self.peek(): raise ValueError('Truncated history document')
        while True:
            try:
                value, end = self.decoder.raw_decode(self.text, self.pos)
                if isinstance(value, (int, float)) and not isinstance(value, bool) and not self.eof and end == len(self.text):
                    self.fill(); continue
                self.pos = end
                return value
            except json.JSONDecodeError:
                if self.eof: raise ValueError('Invalid history record')
                self.fill()


def document(path, field, mapping=False):
    """Yield (top-level key, value); the large field is yielded per record.

    Field records are marked by a None top-level key. Parsing finishes before
    an index is published, including the final envelope and trailing bytes.
    """
    try:
        if os.path.islink(path): raise OSError('Unsafe history document')
        descriptor = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0) | getattr(os, 'O_NONBLOCK', 0))
    except FileNotFoundError:
        return
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        os.close(descriptor); raise OSError('Unsafe history document')
    source = os.fdopen(descriptor, 'r', encoding='utf-8-sig')
    with source:
        reader = Reader(source); reader.take('{'); seen = set()
        if reader.peek() != '}':
            while True:
                key = reader.value()
                if not isinstance(key, str) or key in seen: raise ValueError('Invalid history key')
                seen.add(key); reader.take(':')
                if key == field:
                    reader.take('{' if mapping else '[')
                    closing = '}' if mapping else ']'
                    if reader.peek() != closing:
                        while True:
                            item_key = reader.value() if mapping else None
                            if mapping:
                                if not isinstance(item_key, str): raise ValueError('Invalid history item')
                                reader.take(':')
                            value = reader.value()
                            yield None, (item_key, value) if mapping else value
                            if reader.peek() == closing: break
                            reader.take(',')
                    reader.take(closing)
                else:
                    yield key, reader.value()
                if reader.peek() == '}': break
                reader.take(',')
        reader.take('}')
        if reader.peek(): raise ValueError('Trailing history data')
        if field not in seen: raise ValueError('Missing history collection')


def signature(path):
    try:
        value = os.stat(path)
        return (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    except FileNotFoundError:
        return None


def _sync_parent(path):
    if os.name != 'posix': return
    descriptor = os.open(os.path.dirname(path), os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def rewrite(path, field, records, metadata):
    """Publish an entire JSON document using only one record's working set."""
    parent = os.path.dirname(os.path.abspath(path)); os.makedirs(parent, mode=0o700, exist_ok=True)
    temporary = path + '.' + uuid.uuid4().hex + '.tmp'
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    mapping = field == 'items'
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as target:
            target.write('{')
            for key, value in metadata.items():
                if key == field: continue
                target.write(json.dumps(key)+':'+json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+',')
            target.write(json.dumps(field)+(':{' if mapping else ':['))
            first = True
            for record in records:
                if not first: target.write(',')
                first = False
                if mapping:
                    key, value = record
                    target.write(json.dumps(key)+':')
                else: value = record
                json.dump(value, target, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            target.write('}}' if mapping else ']}')
            target.flush(); os.fsync(target.fileno())
        os.replace(temporary, path); _sync_parent(path)
    finally:
        try: os.unlink(temporary)
        except FileNotFoundError: pass


class HistoryIndex:
    def __init__(self, path, field, mapping=False):
        self.path, self.field, self.mapping = os.path.abspath(path), field, mapping
        self.index = self.path + SUFFIX

    def _records(self):
        version = None
        for key, value in document(self.path, self.field, self.mapping):
            if key is not None:
                if key == 'version': version = value
                continue
            if self.mapping:
                identity, item = value
            elif self.field == 'paths':
                identity, item = value, value
                if not isinstance(value, str) or not os.path.isabs(value) or '\0' in value:
                    raise ValueError('Invalid removed-download entry')
            else:
                item = value; identity = item.get('id', '') if isinstance(item, dict) else ''
            if not isinstance(identity, str) or not identity: continue
            yield identity, item
        if os.path.exists(self.path) and (self.mapping and version not in (1, 2) or self.field == 'paths' and version != 1):
            raise ValueError('Unsupported history version')

    @staticmethod
    def _connect(path):
        connection = _sqlite().connect(path, timeout=2)
        try:
            connection.execute('PRAGMA cache_size=-64')
            connection.execute('PRAGMA mmap_size=0')
            connection.execute('PRAGMA temp_store=FILE')
        except BaseException:
            connection.close(); raise
        return connection

    def _build(self, expected):
        parent = os.path.dirname(self.index); os.makedirs(parent, mode=0o700, exist_ok=True)
        temporary = self.index + '.' + uuid.uuid4().hex + '.tmp'
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600); os.close(fd)
        connection = None
        try:
            connection = self._connect(temporary)
            connection.executescript('CREATE TABLE meta(signature TEXT); CREATE TABLE rows(id TEXT PRIMARY KEY, path TEXT, directory TEXT, status TEXT, created REAL, body TEXT); CREATE INDEX row_path ON rows(path); CREATE INDEX row_time ON rows(created DESC,id); CREATE INDEX row_directory ON rows(directory,created DESC,id);')
            for identity, item in self._records():
                value = item if isinstance(item, dict) else {}
                created = value.get('created') or 0
                try: created = float(created)
                except (TypeError, ValueError, OverflowError): created = 0
                path = str(value.get('path') or '')
                connection.execute('INSERT OR REPLACE INTO rows VALUES(?,?,?,?,?,?)',
                    (identity, path, os.path.normcase(os.path.normpath(os.path.dirname(path))), str(value.get('status') or ''), created,
                     json.dumps(item, ensure_ascii=False, separators=(',', ':'), allow_nan=False)))
                if os.path.getsize(temporary) > MAX_INDEX_BYTES:
                    _DECLINED[self.path] = expected
                    while len(_DECLINED) > 16: _DECLINED.popitem(last=False)
                    raise OSError('History index exceeds disk budget')
            if signature(self.path) != expected: raise InterruptedError('History changed during indexing')
            connection.execute('INSERT INTO meta VALUES(?)', (json.dumps({'source': expected, 'version': 1}),))
            connection.commit(); connection.close(); connection = None
            if os.path.getsize(temporary) > MAX_INDEX_BYTES:
                _DECLINED[self.path] = expected
                while len(_DECLINED) > 16: _DECLINED.popitem(last=False)
                raise OSError('History index exceeds disk budget')
            with open(temporary, 'rb') as handle: os.fsync(handle.fileno())
            os.replace(temporary, self.index)
        finally:
            if connection is not None: connection.close()
            for candidate in (temporary, temporary+'-journal'):
                try: os.unlink(candidate)
                except FileNotFoundError: pass

    @contextmanager
    def connection(self):
        # The cache is disposable; a corrupt/missing/stale index is rebuilt
        # without replacing or deleting any authoritative history document.
        with _LOCK:
            sqlite3 = _sqlite()
            for attempt in range(3):
                expected = signature(self.path)
                if self.path in _DECLINED and _DECLINED[self.path] == expected:
                    raise OSError('History index exceeds disk budget')
                connection = None
                try:
                    info = os.lstat(self.index)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or info.st_uid != getattr(os, 'geteuid', lambda: info.st_uid)()
                            or os.name == 'posix' and info.st_mode & 0o022): raise OSError('Unsafe history index')
                    connection = self._connect(self.index)
                    row = connection.execute('SELECT signature FROM meta').fetchone()
                    if row and json.loads(row[0]) == {'source': list(expected) if expected is not None else None, 'version': 1}: break
                except (sqlite3.DatabaseError, OSError, ValueError): pass
                if connection is not None: connection.close(); connection = None
                try: self._build(expected)
                except InterruptedError:
                    if attempt == 2: raise
                else:
                    connection = self._connect(self.index)
                    break
            try: yield connection
            finally:
                if connection is not None: connection.close()

    def get_many(self, identities):
        result = {}
        # Chunk before opening the cache so read-only/full storage can always
        # fall back to authoritative JSON without consuming a one-shot iterator.
        keys = iter(identities)
        while True:
            batch = []
            for unused in range(100):
                try: batch.append(str(next(keys)))
                except StopIteration: break
            if not batch: return result
            try:
                with self.connection() as connection:
                    for key, body in connection.execute('SELECT id,body FROM rows WHERE id IN ('+','.join('?' for key in batch)+')', batch):
                        result[key] = json.loads(body)
            except (OSError, _sqlite().Error):
                wanted = set(batch)
                wanted.update(str(key) for key in keys)
                for key, value in self._records():
                    if key in wanted: result[key] = value
                return result

    def items(self):
        try:
            with self.connection() as connection:
                for key, body in connection.execute('SELECT id,body FROM rows ORDER BY id'):
                    yield key, json.loads(body)
        except (OSError, _sqlite().Error):
            yield from self._records()

    def page(self, offset=0, size=PAGE_SIZE, folder=None, terminal=False):
        offset, size = max(0, int(offset)), max(1, min(PAGE_SIZE, int(size)))
        where, args = [], []
        if terminal: where.append("status IN ('completed','cancelled')")
        if folder is not None:
            where.append('directory=?')
            args.append(os.path.normcase(os.path.normpath(folder)))
        clause = (' WHERE '+' AND '.join(where)) if where else ''
        try:
            with self.connection() as connection:
                total = connection.execute('SELECT count(*) FROM rows'+clause, args).fetchone()[0]
                rows = connection.execute('SELECT body FROM rows'+clause+' ORDER BY created DESC,id LIMIT ? OFFSET ?', args+[size,offset])
                return [json.loads(row[0]) for row in rows], total
        except (OSError, _sqlite().Error):
            values, total = [], 0
            for key, value in self._records():
                if terminal and value.get('status') not in ('completed', 'cancelled'): continue
                if folder is not None and os.path.dirname(value.get('path', '')) != folder: continue
                if offset <= total < offset+size: values.append(value)
                total += 1
            return values, total


class RemovedPaths:
    """Membership checks use a small per-operation cache, never a full set."""
    def __init__(self, path):
        self.path, self.cache = path, OrderedDict()
        self.index = HistoryIndex(path, 'paths')

    def __contains__(self, key):
        if key not in self.cache:
            self.cache[key] = key in self.index.get_many((key,))
            if len(self.cache) > 256: self.cache.popitem(last=False)
        return self.cache[key]

    def __iter__(self):
        for key, value in self.index._records(): yield key

    def matching(self, keys):
        if not os.path.exists(self.path): return set()
        return set(self.index.get_many(keys))

    def __bool__(self): return os.path.exists(self.path)

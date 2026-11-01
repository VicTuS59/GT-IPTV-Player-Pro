"""Bounded JSON history queries and atomic, lossless history writes."""
from collections import OrderedDict
import math
import json
import os
import stat
import sys
import threading
import uuid

CHUNK = 65536
MAX_VALUE = 2 * 1024 * 1024
PAGE_SIZE = 100
PARTITION_BYTES = 512 * 1024
METADATA_BYTES = 3 * 1024 * 1024
RESULT_CACHE_BYTES = 256 * 1024
RESULT_CACHE_ENTRIES = 24
CURSOR_ENTRIES = 128
_LOCK = threading.RLock()
_READ_LOCK = threading.RLock()
_RESULTS = OrderedDict()
_CURSORS = OrderedDict()
_RESULT_BYTES = 0


def _json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')


def clear_history_cache():
    global _RESULT_BYTES
    with _LOCK:
        _RESULTS.clear(); _CURSORS.clear(); _RESULT_BYTES = 0


def _cached_result(key):
    with _LOCK:
        body = _RESULTS.get(key)
        if body is not None: _RESULTS.move_to_end(key)
    # Return an independent value: GUI/download consumers may mutate their row.
    return json.loads(body) if body is not None else None


def _remember_result(key, result):
    global _RESULT_BYTES
    if len(repr(key)) > 8192: return
    body = _json_bytes(result)
    if len(body) > RESULT_CACHE_BYTES: return
    with _LOCK:
        for old in tuple(_RESULTS):
            if old[:3] == key[:3] and old[3] != key[3]:
                _RESULT_BYTES -= len(_RESULTS.pop(old))
        previous = _RESULTS.pop(key, b'')
        _RESULT_BYTES += len(body) - len(previous)
        _RESULTS[key] = body
        while _RESULT_BYTES > RESULT_CACHE_BYTES or len(_RESULTS) > RESULT_CACHE_ENTRIES:
            _RESULT_BYTES -= len(_RESULTS.popitem(last=False)[1])


def _nearest_cursor(scope, offset):
    position, order = 0, None
    with _LOCK:
        for (owner, found), boundary in _CURSORS.items():
            if owner == scope and position < found <= offset:
                position, order = found, boundary
    return position, order


def _remember_cursor(scope, position, order):
    if len(repr(scope)) + len(repr(order)) > 8192: return
    with _LOCK:
        key = (scope, position)
        _CURSORS[key] = order; _CURSORS.move_to_end(key)
        while len(_CURSORS) > CURSOR_ENTRIES: _CURSORS.popitem(last=False)




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
    a query result is returned, including the final envelope and trailing bytes.
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
        value = os.lstat(path)
        if not stat.S_ISREG(value.st_mode): raise OSError('Unsafe history document')
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
    """Compatibility API backed by streamed JSON and bounded process memory.

    The original documents remain the only persistent history. No database,
    sidecar index or temporary read cache is opened or created.
    """
    def __init__(self, path, field, mapping=False):
        self.path, self.field, self.mapping = os.path.abspath(path), field, mapping

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
            if isinstance(identity, str) and identity:
                yield identity, item
        if os.path.exists(self.path) and (self.mapping and version not in (1, 2) or self.field == 'paths' and version != 1):
            raise ValueError('Unsupported history version')

    def _query(self, query, read):
        # Like the previous shared history backend, allow only one bounded
        # working block at a time; nested selected-row lookups are reentrant.
        with _READ_LOCK:
            return self._read_query(query, read)

    def _read_query(self, query, read):
        # A cache entry belongs to one complete filesystem generation. Reading
        # to EOF validates the JSON envelope/version even after finding a key.
        for unused in range(3):
            expected = signature(self.path)
            key = (self.path, self.field, self.mapping, expected, query)
            cached = _cached_result(key)
            if cached is not None:
                if signature(self.path) == expected:
                    return cached
                continue
            result = read(expected)
            if signature(self.path) == expected:
                _remember_result(key, result)
                return result
        raise InterruptedError('History changed during reading')

    def get_many(self, identities):
        keys = tuple(dict.fromkeys(str(key) for key in identities))
        if not keys: return {}
        wanted = set(keys)
        def read(unused_signature):
            result = {}
            for key, value in self._records():
                if key in wanted: result[key] = value
            return result
        return self._query(('keys', keys), read)

    def _ordered_records(self, prefix='', project=None):
        """Yield unique IDs in the former index's binary order, last value wins.

        Small histories need one pass. Large histories are partitioned by ID
        using bounded working blocks, never by loading the entire document.
        Shared prefixes are skipped together to avoid one pass per character.
        """
        rows, costs, working, common, split = {}, {}, 0, None, False
        for key, value in self._records():
            encoded = key.encode('utf-8').hex()
            if not encoded.startswith(prefix): continue
            if project is not None: value = project(value)
            if common is None:
                common = encoded
            else:
                length = min(len(common), len(encoded))
                i = len(prefix)
                while i < length and common[i] == encoded[i]: i += 1
                common = common[:i]
            if split: continue
            cost = (sys.getsizeof(key) + sys.getsizeof(value) + 128 if project is not None
                    else 256 + len(key.encode('utf-8')) * 3 + len(_json_bytes(value)) * 3)
            working += cost - costs.get(key, 0)
            rows[key], costs[key] = value, cost
            # One record may exceed the small-block budget; the parser's
            # existing MAX_VALUE cap still bounds that individual record.
            budget = METADATA_BYTES if project is not None else PARTITION_BYTES
            if working > budget and len(rows) > 1:
                split = True
                rows.clear(); costs.clear()
        if not split:
            for key in sorted(rows): yield key, rows[key]
            return
        branches, exact = set(), None
        for key, value in self._records():
            encoded = key.encode('utf-8').hex()
            if not encoded.startswith(common): continue
            if len(encoded) == len(common): exact = (key, project(value) if project is not None else value)
            else: branches.add(encoded[len(common)])
        if exact is not None: yield exact
        for character in sorted(branches):
            yield from self._ordered_records(common + character, project)

    def items(self):
        with _READ_LOCK:
            expected = signature(self.path)
            yield from self._ordered_records()
            if signature(self.path) != expected:
                raise InterruptedError('History changed during reading')

    def _window(self, after, size, folder, terminal, reverse=False):
        total = [0]
        def project(item):
            if not isinstance(item, dict): return None
            if terminal and item.get('status') not in ('completed', 'cancelled'): return None
            path = str(item.get('path') or '')
            directory = os.path.normcase(os.path.normpath(os.path.dirname(path)))
            if folder is not None and directory != folder: return None
            try: created = float(item.get('created') or 0)
            except (TypeError, ValueError, OverflowError): created = 0
            return created if math.isfinite(created) else 0
        def ranked():
            # Retain only ID/time scalars while choosing a page. Full row
            # bodies are loaded for the selected 100 records at most.
            for identity, created in self._ordered_records(project=project):
                if created is None: continue
                order = (-created, identity)
                total[0] += 1
                if after is None or (order < after if reverse else order > after):
                    yield order, identity
        # Keep a sorted window with built-in operations only. This avoids
        # adding another optional Python module to minimal receiver images.
        rows = []
        for order, identity in ranked():
            if len(rows) == size and (order <= rows[-1][0] if reverse else order >= rows[-1][0]):
                continue
            low, high = 0, len(rows)
            while low < high:
                middle = (low + high) // 2
                earlier = order > rows[middle][0] if reverse else order < rows[middle][0]
                if earlier: high = middle
                else: low = middle + 1
            rows.insert(low, (order, identity))
            if len(rows) > size: rows.pop()
        values = self.get_many(identity for order, identity in rows)
        return [(order, values[identity]) for order, identity in rows if identity in values], total[0]

    def page(self, offset=0, size=PAGE_SIZE, folder=None, terminal=False):
        offset, size = max(0, int(offset)), max(1, min(PAGE_SIZE, int(size)))
        folder = os.path.normcase(os.path.normpath(folder)) if folder is not None else None
        def read(expected):
            scope = (self.path, self.field, self.mapping, expected, folder, bool(terminal))
            position, after = _nearest_cursor(scope, offset)
            while position < offset:
                rows, total = self._window(after, min(PAGE_SIZE, offset-position), folder, terminal)
                if not rows: return [], total
                position += len(rows); after = rows[-1][0]
                _remember_cursor(scope, position, after)
                if offset >= total: return [], total
                # A direct jump near the end must not walk every earlier page.
                # Work backwards from the end when it needs fewer bounded
                # windows; normal next-page navigation uses its RAM cursor.
                tail = max(0, total-offset-size)
                if tail + size < offset-position:
                    boundary = None
                    while tail:
                        backward, unused_total = self._window(boundary, min(PAGE_SIZE, tail), folder, terminal, True)
                        if not backward: return [], total
                        tail -= len(backward); boundary = backward[-1][0]
                    backward, total = self._window(boundary, min(size, total-offset), folder, terminal, True)
                    rows = list(reversed(backward))
                    if rows: _remember_cursor(scope, offset+len(rows), rows[-1][0])
                    return [value for order, value in rows], total
            rows, total = self._window(after, size, folder, terminal)
            if rows: _remember_cursor(scope, position+len(rows), rows[-1][0])
            return [value for order, value in rows], total
        result = self._query(('page', offset, size, folder, bool(terminal)), read)
        return result[0], result[1]


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

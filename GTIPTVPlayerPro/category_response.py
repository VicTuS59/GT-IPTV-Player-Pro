# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bulk JSON for ordinary category replies; bounded streaming for large bodies."""
import io
import json

from .content import ContentError, _bounded_provider_integer, _validate_provider_json_depth
from .streaming_json import iter_json_array, STREAM_CHUNK_BYTES


class CategoryBatch:
    """One decoded response, or a bounded chunk of an oversized response."""
    def __init__(self, values, atomic, byte_size=None):
        self.values, self.atomic = values, atomic
        self.byte_size = byte_size


def iter_category_batches(read, check, native=False):
    """Use the native JSON decoder once for ordinary Xtream lists.

    The temporary buffer adapts to available RAM; exceeding it continues the
    same HTTP response through the established bounded streaming parser.
    This threshold never truncates a category or starts a second request.
    """
    from .memory_catalog import available_memory, budget
    from .content_sort import Lease
    try: free = available_memory()
    except (OSError, ValueError): free = 8*1024*1024
    cap = (max(0,min(16*1024*1024,free//4)) if native else
           max(0,min(8*1024*1024,free//8,(budget.maximum-budget.used)//8)))
    chunks, length = [], 0
    lease = Lease(check_available=False)
    try:
        while cap >= 65536 and length <= cap:
            check()
            chunk = read(min(STREAM_CHUNK_BYTES, cap-length+1))
            if isinstance(chunk, str): chunk = chunk.encode('utf-8')
            if not chunk:
                check()
                raw = b''.join(chunks)
                try:
                    values = json.loads(raw.decode('utf-8-sig'), parse_int=_bounded_provider_integer)
                    if not isinstance(values, list): raise ValueError('array required')
                except (ValueError, UnicodeError, RecursionError):
                    raise ContentError('Server returned an invalid content response', ContentError.INVALID_RESPONSE)
                chunks.clear(); raw = None
                # These exact dictionaries become the retained list. The
                # collector accounts their size before publishing them.
                lease.close()
                check()
                yield CategoryBatch(values, True, length if native else None)
                return
            if not native: lease.reserve(len(chunk)*4)
            chunks.append(chunk); length += len(chunk)
        prefix = io.BytesIO(b''.join(chunks)); chunks.clear()
        lease.close()
        received=[length];published=[0]
        def replay(size):
            chunk = prefix.read(size)
            if chunk:return chunk
            chunk=read(size);received[0]+=len(chunk)
            return chunk
        batch = []
        for value in iter_json_array(replay, check):
            if isinstance(value, dict): batch.append(value)
            if len(batch) == 512:
                size=received[0]-published[0];published[0]=received[0]
                yield CategoryBatch(batch,False,size if native else None)
                batch = []
        if batch: yield CategoryBatch(batch,False,received[0]-published[0] if native else None)
    finally:
        lease.close()


def iter_category_array(read, check):
    from .memory_catalog import available_memory, budget
    from .content_sort import Lease
    try: free = available_memory()
    except (OSError, ValueError): free = 8*1024*1024
    # Keep the fast json.loads path for small replies, but expose large
    # categories after at most 128 KiB, not megabytes of network buffering.
    cap = max(65536, min(128*1024, free//8,
                        max(65536, (budget.maximum-budget.used)//8)))
    lease = Lease(check_available=False)
    chunks, length = [], 0
    try:
        while length <= cap:
            check()
            chunk = read(min(STREAM_CHUNK_BYTES, cap-length+1))
            if isinstance(chunk, str): chunk = chunk.encode('utf-8')
            if not chunk:
                check()
                raw = b''.join(chunks)
                try:
                    values = json.loads(raw.decode('utf-8-sig'), parse_int=_bounded_provider_integer)
                    if not isinstance(values, list): raise ValueError('array required')
                except (ValueError, UnicodeError, RecursionError):
                    raise ContentError('Server returned an invalid content response', ContentError.INVALID_RESPONSE)
                chunks.clear(); raw = None
                for position, value in enumerate(values):
                    check()
                    try: _validate_provider_json_depth(value)
                    except ValueError:
                        raise ContentError('Server returned an invalid content response', ContentError.INVALID_RESPONSE)
                    yield value
                check()
                return
            lease.reserve(len(chunk)*4)
            chunks.append(chunk); length += len(chunk)
        # Continue the same response without a second HTTP request.
        prefix = io.BytesIO(b''.join(chunks)); chunks.clear()
        def replay(size):
            chunk = prefix.read(size)
            return chunk if chunk else read(size)
        yield from iter_json_array(replay, check)
    finally:
        lease.close()

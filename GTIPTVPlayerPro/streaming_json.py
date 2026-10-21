# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Read a JSON array with a bounded buffer instead of retaining its body."""

import codecs
import json

from .content import ContentError, _bounded_provider_integer, _validate_provider_json_depth
from .i18n import N_


STREAM_RESPONSE_BYTES = 256 * 1024 * 1024
STREAM_OBJECT_BYTES = 2 * 1024 * 1024
STREAM_CHUNK_BYTES = 64 * 1024


def iter_json_array(read, check, max_bytes=STREAM_RESPONSE_BYTES,
                    object_limit=STREAM_OBJECT_BYTES):
    decoder = json.JSONDecoder(parse_int=_bounded_provider_integer)
    utf8 = codecs.getincrementaldecoder("utf-8-sig")("strict")
    buffer, position, received, eof = "", 0, 0, False

    def invalid():
        return ContentError(N_("Server returned an invalid content response"), ContentError.INVALID_RESPONSE)

    def refill():
        nonlocal buffer, position, received, eof
        check()
        buffer = buffer[position:]
        position = 0
        chunk = read(STREAM_CHUNK_BYTES)
        check()
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        received += len(chunk)
        if received > max_bytes:
            raise ContentError(N_("Server content list is too large"), ContentError.RESPONSE_TOO_LARGE)
        eof = not chunk
        try:
            buffer += utf8.decode(chunk, final=eof)
        except UnicodeError:
            raise invalid()

    def next_character():
        nonlocal position
        while True:
            while position < len(buffer) and buffer[position].isspace():
                position += 1
            if position < len(buffer):
                return buffer[position]
            if eof:
                return ""
            refill()

    if next_character() != "[":
        raise invalid()
    position += 1
    after_comma = False
    while True:
        check()
        character = next_character()
        if character == "]" and not after_comma:
            position += 1
            break
        if not character or character == "]":
            raise invalid()
        while True:
            try:
                value, end = decoder.raw_decode(buffer, position)
                # A scalar at a chunk boundary may not be complete yet.
                if end == len(buffer) and not eof:
                    refill()
                    continue
                break
            except (ValueError, RecursionError):
                if eof:
                    raise invalid()
                if len(buffer) - position > object_limit:
                    raise ContentError(N_("Server content list is too large"), ContentError.RESPONSE_TOO_LARGE)
                refill()
        if end - position > object_limit:
            raise ContentError(N_("Server content list is too large"), ContentError.RESPONSE_TOO_LARGE)
        position = end
        try:
            _validate_provider_json_depth(value)
        except ValueError:
            raise invalid()
        check()
        yield value
        character = next_character()
        if character == "]":
            position += 1
            break
        if character != ",":
            raise invalid()
        position += 1
        after_comma = True
    if next_character():
        raise invalid()
    check()

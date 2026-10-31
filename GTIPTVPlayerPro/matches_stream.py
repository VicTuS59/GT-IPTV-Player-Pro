# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Bounded event-at-a-time reader for the public ESPN scoreboard object."""
import codecs
import json

CHUNK_BYTES = 65536
BUFFER_CHAR_LIMIT = 256 * 1024
WIRE_BYTES_LIMIT = 22 * 1024 * 1024


class StreamError(ValueError):
    pass


class ScoreboardStream:
    def __init__(self, response, budget, event_limit=1000):
        self.response = response
        self.budget = budget
        self.event_limit = event_limit
        self.events = 0
        self.complete = False
        self.buffer = ""
        self.position = 0
        self.eof = False
        self.wire_bytes = 0
        self.peak_characters = 0
        self.utf8 = codecs.getincrementaldecoder("utf-8-sig")()
        self.decoder = json.JSONDecoder(parse_constant=self._invalid_constant)

    @staticmethod
    def _invalid_constant(value):
        raise StreamError("Non-JSON numeric constant")

    def _fill(self):
        self.budget.check()
        if self.eof:
            raise StreamError("Truncated scoreboard")
        if self.position:
            self.buffer = self.buffer[self.position:]
            self.position = 0
        room = BUFFER_CHAR_LIMIT - len(self.buffer)
        if room <= 0:
            raise StreamError("Scoreboard value exceeds buffer budget")
        chunk = self.response.read(min(CHUNK_BYTES, room, WIRE_BYTES_LIMIT + 1 - self.wire_bytes))
        self.wire_bytes += len(chunk)
        if self.wire_bytes > WIRE_BYTES_LIMIT:
            raise StreamError("Scoreboard exceeds wire budget")
        self.eof = not chunk
        self.buffer += self.utf8.decode(chunk, final=self.eof)
        self.peak_characters = max(self.peak_characters, len(self.buffer))
        self.budget.check()

    def _peek(self):
        while True:
            while self.position < len(self.buffer) and self.buffer[self.position] in " \t\r\n":
                self.position += 1
            if self.position < len(self.buffer):
                return self.buffer[self.position]
            if self.eof:
                return ""
            self._fill()

    def _take(self, expected):
        if self._peek() != expected:
            raise StreamError("Unexpected scoreboard structure")
        self.position += 1

    def _value(self):
        if not self._peek():
            raise StreamError("Missing scoreboard value")
        while True:
            self.budget.check()
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.position)
                # A number can continue across a network chunk boundary.
                if (isinstance(value, (int, float)) and not isinstance(value, bool)
                        and not self.eof and (end == len(self.buffer)
                        or self.buffer[end] not in " \t\r\n,]}:")):
                    self._fill()
                    continue
                self.position = end
                return value
            except json.JSONDecodeError:
                if self.eof:
                    raise StreamError("Invalid scoreboard JSON")
                self._fill()

    def __iter__(self):
        self._take("{")
        keys = set()
        while self._peek() != "}":
            key = self._value()
            if not isinstance(key, str) or len(key) > 80 or key in keys or len(keys) >= 128:
                raise StreamError("Invalid scoreboard key")
            keys.add(key)
            self._take(":")
            if key == "events":
                self._take("[")
                if self._peek() != "]":
                    while True:
                        event = self._value()
                        if not isinstance(event, dict):
                            raise StreamError("Invalid scoreboard event")
                        self.events += 1
                        if self.events > self.event_limit:
                            raise StreamError("Too many scoreboard events")
                        yield event
                        if self._peek() == "]":
                            break
                        self._take(",")
                self._take("]")
            else:
                self._value()
            if self._peek() == "}":
                break
            self._take(",")
            if self._peek() == "}":
                raise StreamError("Trailing scoreboard comma")
        self._take("}")
        if "events" not in keys or self._peek():
            raise StreamError("Invalid scoreboard envelope")
        self.complete = True

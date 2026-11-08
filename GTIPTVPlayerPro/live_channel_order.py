# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local live-channel order and an explicit OK/EXIT move transaction."""

import hashlib
import json
import re
from itertools import chain

from .category_preferences import LiveCategoryPreferenceStore, MAX_CATEGORIES_PER_SOURCE
from .live_history import live_channel_key


DEFAULT_CHANNEL_ORDER_PATH = "/etc/enigma2/gtiptvplayer/live-channel-order.json"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def channel_order_scope(account_scope, category_id):
    if not isinstance(account_scope, str) or not _DIGEST.fullmatch(account_scope):
        return ""
    raw = json.dumps([account_scope, str(category_id or "")], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def channel_order_key_for_id(stream_id):
    return hashlib.sha256(stream_id.encode("utf-8")).hexdigest() if stream_id else ""


def channel_order_key(item):
    return channel_order_key_for_id(live_channel_key(item))


def arrange_channels(entries, order):
    """Keep provider objects intact and append new channels in provider order."""
    values = list(entries or ())
    ranks = {key: index for index, key in enumerate(order)}
    if not ranks:
        return values
    return sorted(values, key=lambda item: ranks.get(channel_order_key(item), len(ranks)))


class LiveChannelOrderStore:
    def __init__(self, path=DEFAULT_CHANNEL_ORDER_PATH):
        # Reuse the existing locked, bounded, atomic preference writer in a
        # separate file. Its category IDs are opaque 64-character hashes here.
        self._store = LiveCategoryPreferenceStore(path)

    def load(self, scope):
        return tuple(key for key in self._store.load(scope).order if _DIGEST.fullmatch(key))

    def save(self, scope, entries, previous_order=()):
        return self.save_keys(scope, (channel_order_key(item) for item in entries), previous_order)

    def save_keys(self, scope, keys, previous_order=()):
        order = []
        seen = set()
        for key in chain(keys, previous_order):
            if key and _DIGEST.fullmatch(key) and key not in seen:
                order.append(key)
                seen.add(key)
        # Fail visibly rather than silently truncating a large catalogue.
        if not order or len(order) > MAX_CATEGORIES_PER_SOURCE:
            return None
        return tuple(order) if self._store.save(scope, order, ()) else None


class ChannelMove:
    """Only the caller's explicit save persists; cancellation retains new pages."""
    def __init__(self, entries, index):
        self.original = list(entries)
        self.original_index = int(index)
        self.item = entries[index]

    def move(self, entries, target):
        index = next(i for i, item in enumerate(entries) if item is self.item)
        target = min(len(entries) - 1, max(0, int(target)))
        entries.insert(target, entries.pop(index))
        return target

    def cancel(self, entries):
        original_keys = {channel_order_key(item) for item in self.original}
        arrivals = [item for item in entries if channel_order_key(item) not in original_keys]
        entries[:] = self.original + arrivals
        return self.original_index


class BulkChannelMove:
    """Keep the original paged order separate from the editable draft."""

    def __init__(self, entries, index):
        self.original = list(entries)
        self.original_index = index
        self.marked = set()

    def include_page(self, entries):
        self.original.extend(entries)

    def toggle(self, item):
        key = live_channel_key(item)
        if key in self.marked:
            self.marked.remove(key)
        elif key:
            self.marked.add(key)

    def place_after(self, entries, target_index):
        return self._place(entries, target_index, after=True)

    def place_before(self, entries, target_index):
        return self._place(entries, target_index, after=False)

    def _place(self, entries, target_index, *, after):
        if not self.marked or not 0 <= target_index < len(entries):
            return None
        target = entries[target_index]
        if live_channel_key(target) in self.marked:
            return None
        block, remaining = [], []
        insert_at = None
        for item in entries:
            if live_channel_key(item) in self.marked:
                block.append(item)
            else:
                if item is target:
                    insert_at = len(remaining) + (1 if after else 0)
                remaining.append(item)
        if not block or insert_at is None:
            return None
        entries[:] = remaining[:insert_at] + block + remaining[insert_at:]
        self.marked.clear()
        return insert_at

    def cancel(self, entries):
        entries[:] = self.original
        return self.original_index

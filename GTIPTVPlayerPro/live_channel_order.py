# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Local live-channel order and an explicit OK/EXIT move transaction."""

import hashlib
import json
import re

from .category_preferences import LiveCategoryPreferenceStore, MAX_CATEGORIES_PER_SOURCE
from .live_history import live_channel_key


DEFAULT_CHANNEL_ORDER_PATH = "/etc/enigma2/gtiptvplayer/live-channel-order.json"
_DIGEST = re.compile(r"^[0-9a-f]{64}$")


def channel_order_scope(account_scope, category_id):
    if not isinstance(account_scope, str) or not _DIGEST.fullmatch(account_scope):
        return ""
    raw = json.dumps([account_scope, str(category_id or "")], separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def channel_order_key(item):
    value = live_channel_key(item)
    return hashlib.sha256(value.encode("utf-8")).hexdigest() if value else ""


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
        order = []
        seen = set()
        for key in [channel_order_key(item) for item in entries] + list(previous_order):
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

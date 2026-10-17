# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Remember accepted live channels across list/fullscreen decoder handoffs."""

from collections import OrderedDict
from copy import copy


MAX_ACCOUNT_HISTORIES = 16
_histories = OrderedDict()
_CLIENT_HISTORY_ATTRIBUTE = "_gt_live_channel_history"


def live_channel_key(item):
    if getattr(item, "content_type", "") != "live":
        return ""
    return str(getattr(item, "stream_id", "") or "").strip()


class LiveChannelListContext(object):
    """A visited catalogue, without holding its screen or decoder alive."""

    def __init__(self, category, entries, has_more=False):
        self.category = copy(category)
        self.category_id = str(getattr(category, "category_id", "") or "").strip()
        # List replacement creates a new context. Incremental pages can keep
        # extending this list without copying the catalogue on every zap.
        self.entries = entries
        self.has_more = bool(has_more)


class LiveChannelSelection(object):
    def __init__(self, item, category_id="", context=None):
        self.item = copy(item)
        self.key = live_channel_key(item)
        self.category_id = str(category_id or "").strip()
        self.context = context


class LiveChannelHistory(object):
    def __init__(self):
        self.current = None
        self.previous = None

    def remember(self, item, category_id=None, context=None):
        key = live_channel_key(item)
        if not key:
            return False
        existing = next((
            entry for entry in (self.current, self.previous)
            if entry is not None and entry.key == key
        ), None)
        if category_id is None:
            category_id = (
                existing.category_id if existing is not None
                else getattr(item, "category_id", "")
            )
        if context is None and existing is not None:
            context = existing.context
        selection = LiveChannelSelection(item, category_id, context)
        if self.current is not None and self.current.key == key:
            self.current = selection
            return False
        self.previous, self.current = self.current, selection
        return True

    def previous_for(self, key):
        if self.current is not None and key and key == self.current.key:
            return self.previous
        return None


def _history_for(client, account_scope, create=False):
    if client is None:
        return None
    if account_scope:
        # The caller supplies the existing one-way account scope. Keep only
        # the last two selections in RAM; never persist playback credentials.
        key = (str(getattr(client, "source_type", "") or ""), account_scope)
        history = _histories.get(key)
        if history is None:
            if not create:
                return None
            history = LiveChannelHistory()
            _histories[key] = history
        _histories.move_to_end(key)
        while len(_histories) > MAX_ACCOUNT_HISTORIES:
            _histories.popitem(last=False)
        return history
    # Clients without an account identity must never share an empty scope.
    history = getattr(client, _CLIENT_HISTORY_ATTRIBUTE, None)
    if history is None and create:
        history = LiveChannelHistory()
        try:
            setattr(client, _CLIENT_HISTORY_ATTRIBUTE, history)
        except (AttributeError, TypeError):
            return None
    return history


def remember_live_channel(client, item, account_scope, category_id=None, context=None):
    key = live_channel_key(item)
    if not key:
        return False
    history = _history_for(client, account_scope, create=True)
    return history.remember(item, category_id, context) if history is not None else False


def current_live_channel_context(client, item, account_scope):
    key = live_channel_key(item)
    history = _history_for(client, account_scope) if key else None
    if history is not None and history.current is not None and history.current.key == key:
        return history.current.context
    return None


def previous_live_channel_key(client, item, account_scope):
    selection = previous_live_channel_selection(client, item, account_scope)
    return selection.key if selection is not None else ""


def previous_live_channel_selection(client, item, account_scope):
    key = live_channel_key(item)
    if not key:
        return None
    history = _history_for(client, account_scope)
    return history.previous_for(key) if history is not None else None


def clear_live_channel_history(client, account_scope):
    if client is None:
        return
    if account_scope:
        key = (str(getattr(client, "source_type", "") or ""), account_scope)
        _histories.pop(key, None)
    else:
        try:
            delattr(client, _CLIENT_HISTORY_ATTRIBUTE)
        except (AttributeError, TypeError):
            pass

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""One persistent automatic-login selection, identified without credentials."""

import hashlib
import json
import threading

from .settings import load_player_settings, normalize_auto_login_source, save_auto_login_source


_SELECTION_LOCK = threading.RLock()


def source_identity_key(account):
    """Keep a selection across renames, reordering and credential updates."""
    kind = str(getattr(account, "source_type", "") or "").lower()
    if kind not in ("xtream", "stalker", "m3u"):
        return ""
    identity = getattr(account, "identity", ())
    if not isinstance(identity, (list, tuple)) or not identity:
        return ""
    try:
        encoded = json.dumps([kind, list(identity)], ensure_ascii=True,
                             separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError):
        return ""
    if len(encoded) > 16384:
        return ""
    return hashlib.sha256(encoded).hexdigest()


def automatic_account(accounts, key):
    key = normalize_auto_login_source(key)
    if not key:
        return None
    return next((account for account in accounts if source_identity_key(account) == key), None)


def toggle_auto_login(account, accounts, loader=None, saver=None):
    """Return (status, stored key, owner); never replace another saved account."""
    loader = loader or load_player_settings
    saver = saver or save_auto_login_source
    with _SELECTION_LOCK:
        requested = source_identity_key(account)
        if not requested:
            return "error", "", None
        try:
            settings = loader()
            current = normalize_auto_login_source(getattr(settings, "auto_login_source", ""))
            owner = automatic_account(accounts, current)
            if current == requested:
                selected = ""
            elif owner is not None:
                return "conflict", current, owner
            else:
                # A removed TXT/account cannot leave an unchangeable selection.
                selected = requested
            if not saver(selected):
                return "error", current, owner
        except (AttributeError, IOError, OSError, TypeError, ValueError):
            return "error", "", None
        return ("enabled" if selected else "disabled"), selected, account

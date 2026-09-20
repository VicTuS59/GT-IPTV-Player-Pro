# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Process-local ownership guard for DVB EPG update jobs."""

import threading


_LOCK = threading.Lock()
_OWNER = None


def acquire_runtime(owner):
    """Claim the DVB EPG pipeline, or return ``False`` when it is occupied."""
    if owner is None:
        raise ValueError("a DVB EPG runtime owner is required")
    global _OWNER
    with _LOCK:
        if _OWNER is None:
            _OWNER = owner
            return True
        return False


def release_runtime(owner):
    """Release a claim only when it belongs to ``owner``."""
    global _OWNER
    with _LOCK:
        if _OWNER is owner:
            _OWNER = None
            return True
    return False


def runtime_is_owned_by(owner):
    """Return whether ``owner`` currently holds the DVB EPG pipeline."""
    with _LOCK:
        return owner is not None and _OWNER is owner


def runtime_is_busy(excluding=None):
    """Return whether another DVB EPG update currently owns the pipeline."""
    with _LOCK:
        return _OWNER is not None and _OWNER is not excluding

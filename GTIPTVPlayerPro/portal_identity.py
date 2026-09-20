# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Small validators for provider-owned Stalker series identifiers."""

import re


MAX_PORTAL_COMPOSITE_ID_BYTES = 128
_COMPOSITE_ID_PATTERN = re.compile(r"^[0-9]+[:_/-][0-9]+$")


def safe_numeric_id(value, allow_zero=False):
    """Return the existing bounded Stalker numeric identifier form."""
    value = str(value or "").strip()
    if (
        not value
        or len(value) > 40
        or any(character < "0" or character > "9" for character in value)
        or (not allow_zero and not value.strip("0"))
    ):
        return ""
    return value


def safe_composite_id(value):
    """Return a bounded ``digits/separator/digits`` ID without rewriting it."""
    value = str(value or "")
    if not value or value != value.strip():
        return ""
    try:
        encoded = value.encode("ascii", "strict")
    except UnicodeError:
        return ""
    if not encoded or len(encoded) > MAX_PORTAL_COMPOSITE_ID_BYTES:
        return ""
    return value if _COMPOSITE_ID_PATTERN.fullmatch(value) else ""


def safe_series_id(value):
    """Return a numeric or explicitly supported composite series ID."""
    return safe_numeric_id(value) or safe_composite_id(value)


def is_composite_id(value):
    return bool(safe_composite_id(value))

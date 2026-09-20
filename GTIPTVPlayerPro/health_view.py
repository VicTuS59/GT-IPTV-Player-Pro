# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Pure presentation helpers for the shared source-health screen."""

from __future__ import absolute_import

from datetime import datetime, timedelta


HEALTH_SIGNAL_NEUTRAL = "neutral"
HEALTH_SIGNAL_GREEN = "green"
HEALTH_SIGNAL_YELLOW = "yellow"
HEALTH_SIGNAL_RED = "red"

_NEUTRAL_STATUSES = frozenset(
    (
        "",
        "waiting",
        "waiting for test",
        "testing",
        "testing...",
        "cancelled",
        "unknown",
    )
)
_GREEN_STATUSES = frozenset(("online",))
_YELLOW_STATUSES = frozenset(("busy", "disabled", "stale"))
_RED_STATUSES = frozenset(("error", "invalid"))
_YELLOW_ACCOUNT_STATUSES = frozenset(
    ("blocked", "banned", "disabled", "inactive")
)
_RED_ACCOUNT_STATUSES = frozenset(("expired",))

_ACCOUNT_PREVIEW_INACTIVE_STATUSES = frozenset(
    ("blocked", "banned", "disabled", "inactive", "expired", "invalid")
)
_ACCOUNT_PREVIEW_UNLIMITED_EXPIRY = frozenset(
    ("unlimited", "never", "lifetime", "no expiry")
)


def health_signal(status, account_status=""):
    """Return a colour key without inspecting translated or free-form text."""
    status_key = str(status or "").strip().casefold()
    account_key = str(account_status or "").strip().casefold()
    if status_key in _RED_STATUSES:
        return HEALTH_SIGNAL_RED
    if account_key in _RED_ACCOUNT_STATUSES:
        return HEALTH_SIGNAL_RED
    if account_key in _YELLOW_ACCOUNT_STATUSES:
        return HEALTH_SIGNAL_YELLOW
    if status_key in _GREEN_STATUSES:
        return HEALTH_SIGNAL_GREEN
    if status_key in _YELLOW_STATUSES:
        return HEALTH_SIGNAL_YELLOW
    if status_key in _NEUTRAL_STATUSES:
        return HEALTH_SIGNAL_NEUTRAL
    return HEALTH_SIGNAL_NEUTRAL


def account_expiry_signal(expiry, account_status="", now=None):
    """Classify one normalized account expiry without guessing malformed data.

    Health providers already normalize supported dates to ``DD.MM.YYYY`` with
    an optional ``HH:MM`` suffix.  A date-only subscription remains valid for
    the complete displayed calendar day on the receiver.
    """
    account_key = str(account_status or "").strip().casefold()
    if account_key == "expired":
        return HEALTH_SIGNAL_RED

    expiry_text = str(expiry or "").strip()
    expiry_key = expiry_text.casefold()
    if expiry_key in _ACCOUNT_PREVIEW_UNLIMITED_EXPIRY:
        return HEALTH_SIGNAL_GREEN
    if not expiry_text:
        return HEALTH_SIGNAL_NEUTRAL

    parsed = None
    date_only = False
    for template in ("%d.%m.%Y %H:%M", "%d.%m.%Y"):
        try:
            parsed = datetime.strptime(expiry_text, template)
            date_only = template == "%d.%m.%Y"
            break
        except (TypeError, ValueError):
            continue
    if parsed is None:
        return HEALTH_SIGNAL_NEUTRAL

    if now is None:
        current = datetime.now()
    elif isinstance(now, datetime):
        current = now
    else:
        try:
            current = datetime.fromtimestamp(float(now))
        except (OverflowError, OSError, TypeError, ValueError):
            return HEALTH_SIGNAL_NEUTRAL
    expiry_boundary = parsed + timedelta(days=1) if date_only else parsed
    return (
        HEALTH_SIGNAL_RED
        if current >= expiry_boundary
        else HEALTH_SIGNAL_GREEN
    )


def account_connection_signal(
    status,
    account_status="",
    active_connections=None,
    max_connections=None,
):
    """Classify sanitized connection counters for the welcome-screen card."""
    status_key = str(status or "").strip().casefold()
    account_key = str(account_status or "").strip().casefold()
    if account_key in _ACCOUNT_PREVIEW_INACTIVE_STATUSES or status_key in (
        "disabled",
        "invalid",
    ):
        return HEALTH_SIGNAL_RED

    active = active_connections
    maximum = max_connections
    if (
        isinstance(active, bool)
        or isinstance(maximum, bool)
        or not isinstance(active, int)
        or not isinstance(maximum, int)
        or active < 0
        or maximum <= 0
    ):
        return (
            HEALTH_SIGNAL_YELLOW
            if status_key == "busy"
            else HEALTH_SIGNAL_NEUTRAL
        )
    return (
        HEALTH_SIGNAL_YELLOW
        if active >= maximum
        else HEALTH_SIGNAL_GREEN
    )


def account_test_signal(
    status,
    account_status="",
    active_connections=None,
    max_connections=None,
    expiry="",
    now=None,
):
    """Classify an automatic account test without deleting transient errors.

    The source manager uses a stricter contract than the general traffic-light
    screen: only provider-confirmed disabled/invalid/expired accounts are red.
    Network, timeout and malformed-response failures remain neutral so a
    temporary outage can never place an account in the bulk-deletion set.
    """
    status_key = str(status or "").strip().casefold()
    account_key = str(account_status or "").strip().casefold()
    expiry_signal = account_expiry_signal(expiry, account_status, now=now)

    if status_key == "error":
        return HEALTH_SIGNAL_NEUTRAL
    if expiry_signal == HEALTH_SIGNAL_RED:
        return HEALTH_SIGNAL_RED
    if (
        status_key in ("disabled", "invalid")
        or account_key in _ACCOUNT_PREVIEW_INACTIVE_STATUSES
    ):
        return HEALTH_SIGNAL_RED
    if status_key == "busy":
        return HEALTH_SIGNAL_YELLOW

    active = active_connections
    maximum = max_connections
    if (
        not isinstance(active, bool)
        and not isinstance(maximum, bool)
        and isinstance(active, int)
        and isinstance(maximum, int)
        and active >= 0
        and maximum > 0
        and active >= maximum
    ):
        return HEALTH_SIGNAL_YELLOW
    if status_key == "online":
        return HEALTH_SIGNAL_GREEN
    return HEALTH_SIGNAL_NEUTRAL


def account_test_is_inactive(result, now=None):
    """Return true only for a provider-confirmed inactive test result."""
    return account_test_signal(
        getattr(result, "status", ""),
        getattr(result, "account_status", ""),
        getattr(result, "active_connections", None),
        getattr(result, "max_connections", None),
        getattr(result, "expiry", ""),
        now=now,
    ) == HEALTH_SIGNAL_RED


def split_health_text(rendered, left_line_count):
    """Split one already-localised health template into two cards and a note."""
    text = str(rendered or "")
    fields, separator, message = text.partition("\n\n")
    lines = fields.splitlines()
    try:
        left_line_count = max(0, int(left_line_count))
    except (TypeError, ValueError, OverflowError):
        left_line_count = 0
    left = "\n".join(lines[:left_line_count]).strip()
    right = "\n".join(lines[left_line_count:]).strip()
    if not separator:
        message = ""
    return left, right, message.strip()


def dashboard_account_text(source_type, display_name, safe_endpoint):
    """Keep the dashboard MAC compact without changing shared display names."""
    source_type = str(source_type or "").strip().casefold()
    display_name = str(display_name or "").strip()
    safe_endpoint = str(safe_endpoint or "").strip()
    if source_type == "stalker" and safe_endpoint:
        return safe_endpoint
    return display_name or safe_endpoint


def health_account_text(
    source_type,
    display_name,
    safe_name,
    safe_endpoint,
):
    """Build a credential-free, de-duplicated identity for the health header."""
    source_type = str(source_type or "").strip().casefold()
    display_name = str(display_name or "").strip()
    safe_name = str(safe_name or "").strip()
    safe_endpoint = str(safe_endpoint or "").strip()
    primary = safe_endpoint if source_type == "stalker" else display_name
    candidates = (primary, safe_name)
    values = []
    seen = set()
    for value in candidates:
        key = value.casefold()
        if not value or key in seen:
            continue
        seen.add(key)
        values.append(value)
    return "  |  ".join(values)

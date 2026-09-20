# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later

"""Read subscription dates without keeping a portal's private account data."""

import re
from datetime import datetime

from .i18n import N_


EXPIRY_KEYS = (
    "exp_date", "expirydate", "expire_billing_date", "expiry_date",
    "expiration_date", "expire_date", "subscription_expiry",
    "subscription_end", "end_date",
)
EXPIRY_WRAPPERS = (
    "data", "profile", "result", "account_info", "user_info", "account",
)
UNLIMITED_VALUES = frozenset(("unlimited", "never", "lifetime", "no expiry"))
_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
)
_MONTH_NUMBERS = dict((name, index + 1) for index, name in enumerate(_MONTHS))
_MONTH_NUMBERS.update(
    (name[:3], index + 1) for index, name in enumerate(_MONTHS)
)
_TIME_PATTERN = (
    r"(?:[T ,]+(?P<hour>\d{1,2}):(?P<minute>\d{2})"
    r"(?::(?P<second>\d{2})(?:\.\d{1,6})?)?"
    r"(?:\s*(?P<ampm>am|pm))?"
    r"(?P<zone>Z|[+-]\d{2}:?\d{2})?)?"
)
_DATE_PATTERNS = (
    re.compile(
        r"(?P<year>\d{4})(?P<separator>[-/])(?P<month>\d{1,2})"
        r"(?P=separator)(?P<day>\d{1,2})" + _TIME_PATTERN, re.I,
    ),
    re.compile(
        r"(?P<day>\d{1,2})(?P<separator>[./-])(?P<month>\d{1,2})"
        r"(?P=separator)(?P<year>\d{4})" + _TIME_PATTERN, re.I,
    ),
    re.compile(
        r"(?P<month_name>[A-Za-z]+) (?P<day>\d{1,2}),? "
        r"(?P<year>\d{4})" + _TIME_PATTERN, re.I,
    ),
    re.compile(
        r"(?P<day>\d{1,2}) (?P<month_name>[A-Za-z]+),? "
        r"(?P<year>\d{4})" + _TIME_PATTERN, re.I,
    ),
)


def _expiry_value(value, allow_timestamp=True):
    """Accept a whole date value; never extract dates from unrelated prose."""
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return ""
    value = str(value).strip()
    if not value or len(value) > 80:
        return ""
    if value.lower() in UNLIMITED_VALUES:
        return N_("Unlimited")
    if allow_timestamp and re.fullmatch(r"\d{9,10}|\d{13}", value):
        timestamp = int(value)
        if len(value) == 13:
            timestamp /= 1000.0
        # Small counters, zero sentinels and out-of-range values are not dates.
        if not 946684800 <= timestamp < 4102444800:
            return ""
        try:
            return datetime.fromtimestamp(timestamp).strftime("%d.%m.%Y %H:%M")
        except (OverflowError, OSError, ValueError):
            return ""
    for pattern_index, pattern in enumerate(_DATE_PATTERNS):
        match = pattern.fullmatch(value)
        if match is None:
            continue
        fields = match.groupdict()
        try:
            month_name = fields.get("month_name")
            month = (
                _MONTH_NUMBERS[month_name.lower()]
                if month_name else int(fields["month"])
            )
            day = int(fields["day"])
            if pattern_index == 1 and fields["separator"] != ".":
                # Portals use both D/M/Y and M/D/Y. Only an unambiguous
                # numerical date can identify the provider's convention.
                if day <= 12 and month <= 12 and day != month:
                    return ""
                if day <= 12 and month > 12:
                    day, month = month, day
            hour = int(fields.get("hour") or 0)
            ampm = fields.get("ampm")
            zone = fields.get("zone")
            if ampm:
                if not 1 <= hour <= 12 or zone:
                    return ""
                hour = hour % 12 + (12 if ampm.lower() == "pm" else 0)
            if zone and zone.upper() != "Z":
                offset = zone[1:].replace(":", "")
                if int(offset[:2]) > 23 or int(offset[2:]) > 59:
                    return ""
            parsed = datetime(
                int(fields["year"]), month, day, hour,
                int(fields.get("minute") or 0), int(fields.get("second") or 0),
            )
            if parsed.year < 1970:
                return ""
            # Textual dates retain the server's calendar/time representation.
            # In particular, a date-only subscription must not move a day when
            # the receiver and server use different time zones.
            template = "%d.%m.%Y %H:%M" if fields.get("hour") else "%d.%m.%Y"
            return parsed.strftime(template)
        except (KeyError, OverflowError, ValueError):
            return ""
    return ""


def portal_expiry(value, account_info=False):
    """Return a safe date/explicit unlimited value, or an empty unknown value.

    Older MAG account-info implementations put a formatted expiry in ``phone``.
    That compatibility field is accepted only in account-info responses and
    never as a numeric timestamp; real telephone numbers are private data.
    """
    pending = [(value, 0)]
    dates = set()
    phone_dates = set()
    visited = 0
    while pending and visited < 32:
        entry, depth = pending.pop()
        visited += 1
        if not isinstance(entry, dict) or depth > 4:
            continue
        for key in EXPIRY_KEYS:
            parsed = _expiry_value(entry.get(key))
            if parsed:
                dates.add(parsed)
        for key in ("unlimited", "is_unlimited"):
            flag = entry.get(key)
            if flag is True or (
                isinstance(flag, (str, int)) and not isinstance(flag, bool)
                and str(flag).strip().lower() in ("1", "true", "yes")
            ):
                dates.add(N_("Unlimited"))
        if account_info:
            parsed = _expiry_value(entry.get("phone"), allow_timestamp=False)
            if parsed:
                phone_dates.add(parsed)
        for key in EXPIRY_WRAPPERS:
            nested = entry.get(key)
            if isinstance(nested, dict):
                pending.append((nested, depth + 1))
    # Conflicting explicit claims cannot identify one subscription end date.
    if len(dates) == 1:
        return dates.pop()
    if dates:
        return ""
    if len(phone_dates) == 1:
        return phone_dates.pop()
    return ""

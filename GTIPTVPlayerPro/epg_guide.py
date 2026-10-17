# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""On-demand full schedules; ordinary now/next reads remain unchanged."""

import datetime
import time

from .content import ContentError, EPGEvent


MAX_GUIDE_EVENTS = 10000
MAX_DAY_PAGES = 128
MAX_GUIDE_DAYS = 366
_FATAL_REASONS = frozenset((ContentError.CANCELLED, ContentError.DEADLINE,
    ContentError.ACCESS_DENIED, ContentError.RATE_LIMITED, ContentError.CONNECTION,
    ContentError.RESPONSE_TOO_LARGE))


def event_day(event):
    try:
        return datetime.datetime.fromtimestamp(event.start_timestamp).date()
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def event_days(event):
    first = event_day(event)
    try:
        last = datetime.datetime.fromtimestamp(event.end_timestamp - 1).date()
    except (ValueError, TypeError, OverflowError, OSError):
        return (first,) if first is not None else ()
    return (first, last) if first is not None and last != first else (first,) if first is not None else ()


def clean_events(events):
    values = {}
    for event in events or ():
        start = getattr(event, "start_timestamp", 0)
        end = getattr(event, "end_timestamp", 0)
        if not isinstance(start, (int, float)) or not 0 < start < 4102444800:
            continue
        if not isinstance(end, (int, float)) or not start < end <= start + 86400:
            continue
        key = (start, end, str(getattr(event, "title", "") or ""))
        old = values.get(key)
        if old is None or len(getattr(event, "description", "")) > len(getattr(old, "description", "")):
            values[key] = event
        if len(values) >= MAX_GUIDE_EVENTS:
            break
    return sorted(values.values(), key=lambda event: (event.start_timestamp, event.end_timestamp, event.title))


class GuideBatch(object):
    def __init__(self, events=(), days=(), requested_day=None, lazy=False):
        self.events = clean_events(events)
        actual = {day for event in self.events for day in event_days(event)}
        self.days = sorted(actual.union(day for day in days if isinstance(day, datetime.date)))[:MAX_GUIDE_DAYS]
        self.requested_day = requested_day
        self.lazy = bool(lazy)


def _unwrap(value):
    for unused in range(5):
        if not isinstance(value, dict):
            break
        nested = next((value[key] for key in ("js", "data", "days", "dates", "week")
                       if isinstance(value.get(key), (dict, list))), None)
        if nested is None:
            break
        value = nested
    return value


def portal_days(value):
    value = _unwrap(value)
    rows = list(value.values()) if isinstance(value, dict) else value if isinstance(value, list) else ()
    days = set()
    for row in rows[:MAX_GUIDE_DAYS]:
        if isinstance(row, dict):
            if str(row.get("disabled", "")).lower() in ("1", "true", "yes"):
                continue
            raw = next((row[key] for key in ("f_mysql", "date", "day", "value") if row.get(key)), "")
        else:
            raw = row
        try:
            day = datetime.date.fromisoformat(str(raw))
        except (TypeError, ValueError):
            continue
        days.add(day)
    return sorted(days)


def _portal_events(client, payload, identities):
    channel_ids = tuple(value for kind, value in identities
                        if kind in ("id", "ch_id", "channel_id", "stream_id"))
    target = channel_ids[0] if channel_ids else ""
    rows = client._epg_rows_for_channel(payload, target, channel_ids=channel_ids)
    events = []
    for row in rows[:MAX_GUIDE_EVENTS]:
        start, end = client._epg_times(row)
        events.append(EPGEvent(row.get("name") or row.get("title") or row.get("epg_title"),
                               start, end, description=row.get("descr") or row.get("description") or row.get("plot") or ""))
    return clean_events(events)


def _stalker_guide(client, item, day=None):
    stream_id = str(item.stream_id)
    meta = dict(getattr(client, "_live_channel_meta", {}).get(stream_id, {}))
    identities = tuple(meta.get("identities") or (("stream_id", stream_id),))
    channel_id = next((value for kind, value in identities if kind in ("id", "ch_id", "channel_id", "stream_id")), stream_id)
    days = []
    if day is None:
        try:
            days = portal_days(client._request("epg", "get_week", (("ch_id", channel_id),)))
        except ContentError as error:
            if error.reason in _FATAL_REASONS:
                raise
    today = datetime.date.today()
    selected = day or (today if today in days or not days else min(days, key=lambda date: abs((date - today).days)))
    events, fingerprints = [], set()
    try:
        for page in range(1, MAX_DAY_PAGES + 1):
            client._check_request_limits()
            payload = client._request("epg", "get_simple_data_table", (
                ("ch_id", channel_id), ("date", selected.isoformat()), ("p", str(page)),
            ))
            batch = _portal_events(client, payload, identities)
            signature = tuple((event.start_timestamp, event.end_timestamp, event.title) for event in batch)
            if not signature or signature in fingerprints:
                break
            fingerprints.add(signature)
            events.extend(batch)
            if len(events) >= MAX_GUIDE_EVENTS:
                break
            # Do not assume a page length: MAG portals use different sizes.
            value = payload
            while isinstance(value, dict) and isinstance(value.get("js"), dict):
                value = value["js"]
            if isinstance(value, dict):
                try:
                    if int(value.get("total_items", 0)) > 0 and len(events) >= int(value["total_items"]):
                        break
                except (TypeError, ValueError, OverflowError):
                    pass
    except ContentError as error:
        if error.reason in _FATAL_REASONS or day is not None:
            raise
    if events:
        return GuideBatch(events, days, selected, lazy=True)
    # Compatibility with portals exposing only the ITV guide route. This is
    # one bounded response; only returned event dates become selectable.
    if day is None:
        try:
            payload = client._request("itv", "get_epg_info", (("ch_id", channel_id), ("channel_id", channel_id), ("period", "8784")))
            events = _portal_events(client, payload, identities)
            if events:
                return GuideBatch(events)
        except ContentError as error:
            if error.reason in _FATAL_REASONS:
                raise
    return GuideBatch((), days, selected, lazy=True)


def load_guide(client, item, day=None):
    kind = str(getattr(client, "source_type", "xtream") or "xtream").lower()
    if getattr(item, "content_type", "") != "live":
        return GuideBatch()
    if kind == "stalker":
        return _stalker_guide(client, item, day)
    if kind == "m3u":
        from .m3u_epg_settings import get_m3u_epg_binding
        from .m3u_epg import M3UEPGError, read_guide_events
        with client._state_lock:
            record = dict(client._record_by_id.get(str(item.stream_id), {}))
        tvg_id = str(record.get("tvg_id", "") or "").strip()
        binding = get_m3u_epg_binding(client.account.source_id)
        if not tvg_id or binding is None or not binding.enabled:
            return GuideBatch()
        deadline = client._operation_deadline()
        cancel_event, unused_deadline, unused_timeout = client._request_limits()
        try:
            rows = read_guide_events(client.account.source_id, binding, tvg_id,
                now=client.wall_clock(), deadline=deadline, timeout=client._remaining_timeout(deadline),
                cancel_event=cancel_event, resolver=client.resolver, local_roots=client.local_roots)
        except M3UEPGError as error:
            client._check_limits(deadline)
            raise ContentError("EPG guide unavailable", ContentError.CONTENT_RESPONSE) from error
        return GuideBatch(EPGEvent(title, start, end, description=description) for start, end, title, description in rows)
    # Xtream's simple table is the complete available schedule. Deliberately
    # avoid load_epg/load_simple_epg, which clip the response to now/next.
    from .content import XtreamContentClient, _numeric_id
    stream_id = _numeric_id(item.stream_id)
    if not stream_id:
        return GuideBatch()
    try:
        payload = client._request_json("get_simple_data_table", {"stream_id": stream_id})
    except ContentError as error:
        if error.reason in _FATAL_REASONS:
            raise
        payload = client._request_json("get_short_epg", {"stream_id": stream_id, "limit": MAX_GUIDE_EVENTS})
    return GuideBatch(XtreamContentClient._epg_events(payload))

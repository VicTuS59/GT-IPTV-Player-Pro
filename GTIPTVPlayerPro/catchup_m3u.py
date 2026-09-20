# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Explicit M3U catchup declarations and bounded, on-demand XMLTV history.

Supported wire conventions are documented by Kodi PVR IPTV Simple:
https://github.com/kodi-pvr/pvr.iptvsimple/blob/Piers/README.md
No archive endpoint or retention window is inferred from ordinary live URLs.
"""

import datetime
import math
import re
import time
from urllib.parse import unquote, urlsplit, urlunsplit

from .catchup import ArchiveChannel, ArchiveProgramme
from .content import ContentError
from .i18n import N_
from .m3u_epg import (
    M3UEPGCancelled, M3UEPGError, binding_fingerprint, read_archive_events,
)
from .m3u_epg_settings import get_m3u_epg_binding


MAX_ARCHIVE_DAYS = 31
MAX_TEMPLATE_LENGTH = 4096
HISTORY_CACHE_SECONDS = 300
_TOKEN = re.compile(r"(\$?)\{([^{}]+)\}")
_DIGITS = re.compile(r"^[0-9]{1,8}$")
_ALIASES = {"start": "utc", "end": "utcend", "now": "lutc",
            "timestamp": "lutc", "duration": "duration"}


def _unavailable():
    return ContentError(N_("Archive playback is unavailable for this channel."),
                        ContentError.PLAYBACK_URL)


def _safe_url(value):
    if not isinstance(value, str) or not value or len(value) > MAX_TEMPLATE_LENGTH:
        raise ValueError("invalid archive URL")
    decoded = unquote(value)
    if any(ord(char) <= 32 or ord(char) == 127 or char in "|\\{}" for char in decoded):
        raise ValueError("invalid archive URL characters")
    parsed = urlsplit(value)
    if (parsed.scheme.lower() not in ("http", "https") or not parsed.hostname
            or parsed.fragment or (parsed.port is not None and not 1 <= parsed.port <= 65535)):
        raise ValueError("invalid archive URL origin")
    return parsed


def _render(template, start, end, now):
    """Substitute numeric timestamp fields only, never arbitrary format code."""
    if (not isinstance(template, str) or not template
            or len(template) > MAX_TEMPLATE_LENGTH):
        raise ValueError("invalid archive template")
    target_seen = [False]
    times = {"utc": start, "utcend": end, "lutc": now}

    def replace(match):
        dollar, token = match.groups()
        name, separator, argument = token.partition(":")
        if dollar:
            name = _ALIASES.get(name)
            if name is None:
                raise ValueError("unsupported archive token")
        if name in ("utc", "utcend", "offset") or name in (
            "Y", "m", "d", "H", "M", "S"
        ):
            target_seen[0] = True
        if name in times:
            value = int(times[name])
            if not separator:
                return str(value)
            if not argument or len(argument) > 64 or any(char not in "YmdHMS-_:/.T" for char in argument):
                raise ValueError("invalid timestamp format")
            stamp = time.gmtime(value)
            fields = dict(zip("YmdHMS", ("{:04d}".format(stamp.tm_year),
                          "{:02d}".format(stamp.tm_mon), "{:02d}".format(stamp.tm_mday),
                          "{:02d}".format(stamp.tm_hour), "{:02d}".format(stamp.tm_min),
                          "{:02d}".format(stamp.tm_sec))))
            return "".join(fields.get(char, char) for char in argument)
        if name in ("Y", "m", "d", "H", "M", "S") and not separator and not dollar:
            return time.strftime("%" + name, time.gmtime(start))
        if name in ("duration", "offset"):
            divider = 1
            if separator:
                if not _DIGITS.fullmatch(argument) or int(argument) < 1:
                    raise ValueError("invalid archive divider")
                divider = int(argument)
            value = end - start if name == "duration" else now - start
            return str(max(0, int(value)) // divider)
        raise ValueError("unsupported archive token")

    rendered = _TOKEN.sub(replace, template)
    if not target_seen[0] or any(char in rendered for char in "{}"):
        raise ValueError("missing or unsupported archive time field")
    return rendered


def _configuration(record):
    attributes = record.get("catchup") or {}
    mode = str(attributes.get("catchup", "")).lower()
    if not mode and attributes.get("timeshift"):
        mode = "shift"
    if mode not in ("default", "append", "shift"):
        raise ValueError("unsupported M3U catchup mode")
    days_value = attributes.get("catchup-days", attributes.get("timeshift", ""))
    if not isinstance(days_value, str) or not _DIGITS.fullmatch(days_value) or int(days_value) <= 0:
        raise ValueError("missing archive retention")
    days = min(MAX_ARCHIVE_DAYS, int(days_value))
    correction = float(attributes.get("catchup-correction", "0"))
    if not math.isfinite(correction) or not -12 <= correction <= 14:
        raise ValueError("invalid archive correction")
    correction = int(correction * 3600)
    live = _safe_url(record.get("url", ""))
    base = urlunsplit(live)
    source = attributes.get("catchup-source", "")
    if mode == "shift":
        template = base + ("&" if live.query else "?") + "utc={utc}&lutc={lutc}"
    elif mode == "append":
        if not source or source[0] not in "?&":
            raise ValueError("append catchup requires an explicit query")
        template = base + ("&" if live.query else "?") + source[1:]
    else:
        # Full replacement: live credentials and headers are never inherited
        # by the explicitly configured archive server, including cross-host URLs.
        template = source
    sample = _render(template, 1700000000, 1700003600, 1700086400)
    parsed = _safe_url(sample)
    # Timestamps must not be able to change the authority being contacted.
    if _TOKEN.search(urlsplit(template).netloc):
        raise ValueError("archive origin contains a placeholder")
    return days, correction, template, (parsed.scheme, parsed.netloc)


def _record(client, channel):
    with client._state_lock:
        record = client._record_by_id.get(str(channel.stream_id))
        if record is None:
            raise _unavailable()
        return dict(record)


def load_channels(client):
    deadline = client._operation_deadline()
    client._check_limits(deadline)
    client.prepare()
    result = []
    with client._state_lock:
        for index, record in enumerate(client._records):
            if index % 64 == 0:
                client._check_limits(deadline)
            if not record.get("tvg_id"):
                continue
            try:
                days, unused_correction, unused_template, unused_origin = _configuration(record)
            except (TypeError, ValueError, OverflowError):
                continue
            result.append(ArchiveChannel(client._content_item(record), days,
                                         metadata={"source_id": client.account.source_id}))
    client._check_limits(deadline)
    return result


def load_programmes(client, channel, day=None):
    deadline = client._operation_deadline()
    client._check_limits(deadline)
    record = _record(client, channel)
    try:
        days, unused_correction, unused_template, unused_origin = _configuration(record)
    except (TypeError, ValueError, OverflowError) as error:
        raise _unavailable() from error
    binding = get_m3u_epg_binding(client.account.source_id)
    if binding is None or not binding.enabled:
        raise ContentError(N_("Configure an XMLTV guide for this M3U source to use TV archive."),
                           ContentError.UNSUPPORTED_TYPE)
    client._check_limits(deadline)
    now = int(client.wall_clock())
    key = (client.account.source_id, record["tvg_id"], days,
           binding_fingerprint(binding, client.account.source_id))
    with client._state_lock:
        cached = getattr(client, "_archive_history", None)
    if cached is not None and cached[0] == key and client.clock() < cached[1]:
        rows = cached[2]
    else:
        cancel_event, unused_deadline, unused_timeout = client._request_limits()
        try:
            rows = read_archive_events(
                client.account.source_id, binding, record["tvg_id"], days,
                now=now, deadline=deadline, timeout=client._remaining_timeout(deadline),
                cancel_event=cancel_event, resolver=client.resolver,
                local_roots=client.local_roots,
            )
        except M3UEPGCancelled as error:
            client._check_limits(deadline)
            raise ContentError(N_("Request cancelled"), ContentError.CANCELLED) from error
        except (M3UEPGError, OSError, ValueError) as error:
            raise ContentError(N_("Could not load the TV archive."),
                               ContentError.CONTENT_RESPONSE) from error
        client._check_limits(deadline)
        with client._state_lock:
            # One channel only; changing channels cannot grow archive RAM use.
            client._archive_history = (key, client.clock() + HISTORY_CACHE_SECONDS, rows)
    programmes = []
    for index, (start, end, title, description) in enumerate(rows):
        if index % 64 == 0:
            client._check_limits(deadline)
        if start < now - days * 86400 or end > now:
            continue
        if day is not None and datetime.date.fromtimestamp(start) != day:
            continue
        programmes.append(ArchiveProgramme(
            "{}:{}".format(record["tvg_id"], start), title, start, end, description,
            metadata={"source_id": client.account.source_id, "stream_id": channel.stream_id},
        ))
    client._check_limits(deadline)
    return programmes


def playback_url(client, channel, programme):
    deadline = client._operation_deadline()
    client._check_limits(deadline)
    record = _record(client, channel)
    try:
        days, correction, template, origin = _configuration(record)
        start, end, now = int(programme.start), int(programme.end), int(client.wall_clock())
        if not (0 < start < end <= now) or start < now - days * 86400:
            raise ContentError(N_("This programme is no longer available in the TV archive."),
                               ContentError.PLAYBACK_URL)
        metadata = programme.metadata or {}
        if (metadata.get("source_id") != client.account.source_id
                or str(metadata.get("stream_id")) != str(channel.stream_id)):
            raise ValueError("programme belongs to another channel")
        # Kodi's correction is the provider time shift to subtract from the
        # programme timestamps.  Current UTC time remains unchanged, so an
        # offset query also reflects the corrected programme start.
        url = _render(template, start - correction, end - correction, now)
        parsed = _safe_url(url)
        if (parsed.scheme, parsed.netloc) != origin:
            raise ValueError("archive URL origin changed")
    except (TypeError, ValueError, OverflowError) as error:
        raise _unavailable() from error
    client._check_limits(deadline)
    return url


def _template_uses_current_time(template):
    """Return whether an explicit template can safely produce a fresh URL."""
    for match in _TOKEN.finditer(str(template or "")):
        dollar, token = match.groups()
        name = token.partition(":")[0]
        if dollar:
            name = _ALIASES.get(name, "")
        if name in ("lutc", "offset"):
            return True
    return False


def decoder_retry_playback_url(
    client,
    channel,
    programme,
    previous_url,
):
    """Re-render only a declared, current-time-dependent catch-up target."""
    deadline = client._operation_deadline()
    client._check_limits(deadline)
    record = _record(client, channel)
    try:
        unused_days, unused_correction, template, unused_origin = (
            _configuration(record)
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise _unavailable() from error
    if not _template_uses_current_time(template):
        return ""
    # playback_url re-applies the programme/channel/retention/origin checks and
    # renders the exact provider declaration.  No alternate route or token is
    # inferred here.
    candidate = playback_url(client, channel, programme)
    client._check_limits(deadline)
    return candidate if candidate != str(previous_url or "").strip() else ""

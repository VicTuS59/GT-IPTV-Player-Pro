# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Native Stalker TV archive, using the authenticated receiver session.

Protocol reference (Infomir's published Stalker source):
https://github.com/azhurb/stalker_portal/tree/
1b059002a017a488d7fa912fcc09044f8f1c09e8

``server/lib/epg.class.php::getSimpleDataTable`` takes ch_id/date/p and
returns the EPG id, Unix timestamps and mark_archive. ``c/tv_archive.js``
uses that EPG id in ``auto /media/<id>.mpg``; TvArchive::createLink resolves
it through the archive storage. A live channel command is never an archive
command. Native tv_archive_duration is in HOURS, not Xtream's days.

OpenXC-compatible portals return the otherwise equivalent programme identity
as ``<epg_id>_<channel_id>``.  Both components stay numeric and the suffix is
bound to the selected channel.  Composite identities first retain R90's
``.mpg`` media command and make one ``.ts`` compatibility attempt only when
that response is not playable.  Native numeric EPG identities use ``.mpg``
without an extension fallback.

Stb reads the timezone cookie and sets PHP/MySQL timezones before querying
the date (server/lib/stb.class.php). The existing session already sends the
receiver timezone, so the selected receiver date needs no guessed offset.
"""

from __future__ import absolute_import

import datetime
import hashlib
import math
import re
import time

from .catchup import ArchiveChannel, ArchiveProgramme
from .content import ContentError, ContentItem
from .diagnostics import log_event
from .i18n import N_


MAX_ARCHIVE_DAYS = 31
MAX_CHANNELS = 2000
MAX_CHANNEL_ROWS = 20000
MAX_CHANNEL_PAGES = 100
MAX_PROGRAMME_PAGES = 16
MAX_PROGRAMME_ROWS = 2000
_CHANNEL_ID = re.compile(r"^[0-9]{1,20}$")
_PROGRAMME_ID = re.compile(r"^[0-9]{1,20}(?:_[0-9]{1,20})?$")
_MAC_PLACEHOLDER = re.compile(r"%mac%", re.IGNORECASE)
_PROGRAMME_PAGE_FINGERPRINT_FIELDS = (
    ("id", 41), ("real_id", 41),
    ("ch_id", 20), ("mark_archive", 16),
    ("start_timestamp", 32), ("stop_timestamp", 32),
)


def _positive_flag(value):
    return value is True or str(value or "").strip().lower() in (
        "1", "true", "yes", "on",
    )


def _number(value):
    try:
        if isinstance(value, bool) or len(str(value)) > 32:
            return None
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError, OverflowError):
        return None


def _identity(value):
    text = str(value or "").strip()
    return text if _CHANNEL_ID.match(text) and int(text) > 0 else ""


def _programme_identity(value, channel_id=""):
    """Accept native EPG IDs and OpenXC's bounded ``epg_id_channel_id``."""
    text = str(value or "").strip()
    if not _PROGRAMME_ID.match(text):
        return ""
    parts = text.split("_")
    if any(int(part) <= 0 for part in parts):
        return ""
    if len(parts) == 2 and channel_id and parts[1] != channel_id:
        return ""
    return text


def _programme_page_fingerprint(rows):
    """Bound repeated-page detection without trusting programme ID alone."""
    digest = hashlib.sha256()
    for row in rows[:MAX_PROGRAMME_ROWS]:
        if not isinstance(row, dict):
            digest.update(b"\x00")
            continue
        digest.update(b"\x01")
        for field, limit in _PROGRAMME_PAGE_FINGERPRINT_FIELDS:
            text = str(row.get(field, "") or "")
            value = text[:limit].encode("utf-8", "surrogatepass")
            # Retain the original character length so an invalid overlong
            # identity cannot alias the valid bounded prefix on a later page.
            digest.update(str(len(text)).encode("ascii"))
            digest.update(b":")
            digest.update(str(len(value)).encode("ascii"))
            digest.update(b":")
            digest.update(value)
            digest.update(b"\x00")
    return digest.digest()


def _unsupported():
    return ContentError(
        N_("TV archive is not supported by this source."),
        ContentError.UNSUPPORTED_TYPE,
    )


def _unavailable():
    return ContentError(
        N_("Archive playback is unavailable for this channel."),
        ContentError.PLAYBACK_URL,
    )


def _expired():
    return ContentError(
        N_("This programme is no longer available in the TV archive."),
        ContentError.PLAYBACK_URL,
    )


def _is_unsupported(error):
    return error.reason in (
        ContentError.UNSUPPORTED_TYPE, ContentError.CONTENT_RESPONSE,
    ) or (
        error.reason == ContentError.HTTP_ERROR
        and error.http_status in (400, 404, 405, 422, 501)
    )


def _is_archive_media_mismatch(error):
    """Return whether one alternate OpenXC archive extension is safe.

    Composite OpenXC programme IDs are deployed with both ``.mpg`` and
    ``.ts`` archive commands.  Only response-shape, route and playability
    failures may cross that compatibility boundary.  Authentication,
    throttling, cancellation, timeout and server failures must remain single
    attempts so the explicit archive open cannot turn into a hidden retry
    loop.
    """
    return error.reason in (
        ContentError.PLAYBACK_URL,
        ContentError.UNSUPPORTED_TYPE,
        ContentError.CONTENT_RESPONSE,
        ContentError.INVALID_RESPONSE,
    ) or (
        error.reason == ContentError.HTTP_ERROR
        and error.http_status in (400, 404, 405, 422, 501)
    )


def _table(value):
    """Accept bounded native list wrappers; a missing API is not a list."""
    for unused in range(5):
        if isinstance(value, list):
            return value
        if not isinstance(value, dict):
            break
        for name in ("data", "items", "channels", "results", "js", "result"):
            if name in value:
                value = value[name]
                break
        else:
            break
    raise _unsupported()


def _channel(client, entry):
    if not isinstance(entry, dict):
        return None
    # An explicit native disabled flag wins over legacy aliases.
    for key in ("enable_tv_archive", "tv_archive", "archive"):
        if key in entry:
            enabled = _positive_flag(entry[key])
            break
    else:
        enabled = False
    hours = _number(entry.get("tv_archive_duration"))
    channel_id = _identity(entry.get("id") or entry.get("ch_id"))
    if not enabled or not hours or hours <= 0 or not channel_id:
        return None
    seconds = min(hours * 3600, MAX_ARCHIVE_DAYS * 86400)
    categories = client._category_ids(entry)
    item = ContentItem(
        "live", channel_id, entry.get("name") or entry.get("title"),
        category_id=categories[0] if categories else "",
        icon=entry.get("logo") or entry.get("stream_icon") or "",
    )
    item.portal_category_ids = categories
    item.portal_channel_id = channel_id
    # Retain no live URL/token/command in archive metadata.
    return ArchiveChannel(item, int(math.ceil(seconds / 86400)), metadata={
        "provider": "stalker", "channel_id": channel_id,
        "retention_seconds": seconds, "archive_seconds": seconds,
    })


def load_channels(client):
    """Discover only channels explicitly advertising native archive hours."""
    client._check_request_limits()
    try:
        value = client._request("itv", "get_all_channels")
        rows = _table(value)
    except ContentError as error:
        if not _is_unsupported(error):
            raise
        rows = []
    results = []
    seen = set()
    scanned = 0

    def consume(batch):
        nonlocal scanned
        added = 0
        for entry in batch:
            client._check_request_limits()
            scanned += 1
            if scanned > MAX_CHANNEL_ROWS or len(results) >= MAX_CHANNELS:
                break
            if not isinstance(entry, dict):
                continue
            channel_id = _identity(entry.get("id") or entry.get("ch_id"))
            if not channel_id or channel_id in seen:
                continue
            seen.add(channel_id)
            added += 1
            channel = _channel(client, entry)
            if channel is not None:
                results.append(channel)
        return added

    if rows:
        consume(rows)
    if results:
        return results
    # Some compatible portals expose a non-empty all-channels catalogue
    # without archive flags, then provide the complete archive metadata only
    # through the ordered ITV catalogue.  Let those same channel IDs be
    # evaluated again rather than suppressing the documented fallback.
    seen.clear()
    scanned = 0
    # The documented native catalogue alternative remains an ITV catalogue;
    # it never becomes a guessed tv_archive/get_ordered_list call.
    for page in range(1, MAX_CHANNEL_PAGES + 1):
        client._check_request_limits()
        try:
            value = client._request(
                "itv", "get_ordered_list",
                client._live_order_parameters("*", page),
            )
            rows = _table(value)
        except ContentError as error:
            if _is_unsupported(error):
                if results:
                    return results
                raise _unsupported()
            raise
        added = consume(rows)
        total = client._total_items(value)
        if (
            not rows or not added or scanned >= MAX_CHANNEL_ROWS
            or len(results) >= MAX_CHANNELS
            or (total is not None and len(seen) >= total)
        ):
            break
        client._paced_catalog_wait()
    return results


def _channel_context(channel):
    metadata = getattr(channel, "metadata", {})
    if not isinstance(metadata, dict):
        raise _unavailable()
    channel_id = _identity(metadata.get("channel_id"))
    seconds = _number(metadata.get("retention_seconds"))
    if (
        metadata.get("provider") != "stalker" or not channel_id
        or channel_id != str(getattr(channel, "stream_id", ""))
        or not seconds or seconds <= 0
    ):
        raise _unavailable()
    return channel_id, min(seconds, MAX_ARCHIVE_DAYS * 86400)


def load_programmes(client, channel, day=None):
    """Fetch only the selected day, with native one-based pagination."""
    client._check_request_limits()
    channel_id, retention = _channel_context(channel)
    now = time.time()
    today = datetime.datetime.fromtimestamp(now).date()
    if day is None:
        day = today - datetime.timedelta(days=1)
    if isinstance(day, datetime.datetime):
        day = day.date()
    if not isinstance(day, datetime.date):
        raise _unavailable()
    if day > today or day < datetime.datetime.fromtimestamp(now - retention).date():
        return []
    results = []
    seen = set()
    page_fingerprints = set()
    scanned = 0
    pages = 0
    rejected = {
        "shape": 0, "id": 0, "duplicate": 0, "channel": 0,
        "archive": 0, "time": 0, "day": 0,
    }
    observed = {"invalid_id": 0, "archive_disabled": 0}
    accepted_numeric = 0
    accepted_composite = 0
    for page in range(1, MAX_PROGRAMME_PAGES + 1):
        client._check_request_limits()
        try:
            pages += 1
            value = client._request("epg", "get_simple_data_table", (
                ("ch_id", channel_id), ("date", day.isoformat()),
                ("p", str(page)),
            ))
            rows = _table(value)
        except ContentError as error:
            if _is_unsupported(error):
                raise _unsupported()
            raise
        fingerprint = _programme_page_fingerprint(rows)
        if not rows or fingerprint in page_fingerprints:
            break
        page_fingerprints.add(fingerprint)
        for row in rows:
            client._check_request_limits()
            scanned += 1
            if scanned > MAX_PROGRAMME_ROWS:
                break
            if not isinstance(row, dict):
                rejected["shape"] += 1
                continue
            programme_id = _programme_identity(row.get("id"), channel_id)
            archive_enabled = _positive_flag(row.get("mark_archive"))
            # These observations deliberately overlap.  Keep the accepted-row
            # contract strict while making a row that has both an invalid ID
            # and a disabled archive flag visible in privacy-safe diagnostics.
            if not programme_id:
                observed["invalid_id"] += 1
            if not archive_enabled:
                observed["archive_disabled"] += 1
            if not programme_id:
                rejected["id"] += 1
                continue
            if programme_id in seen:
                rejected["duplicate"] += 1
                continue
            if _identity(row.get("ch_id")) != channel_id:
                rejected["channel"] += 1
                continue
            if not archive_enabled:
                rejected["archive"] += 1
                continue
            start = _number(row.get("start_timestamp"))
            end = _number(row.get("stop_timestamp"))
            if (
                start is None or end is None or start <= 0
                or start < now - retention or end <= start or end > now
            ):
                rejected["time"] += 1
                continue
            try:
                if datetime.datetime.fromtimestamp(start).date() != day:
                    rejected["day"] += 1
                    continue
            except (OverflowError, OSError, ValueError):
                rejected["day"] += 1
                continue
            seen.add(programme_id)
            if "_" in programme_id:
                accepted_composite += 1
            else:
                accepted_numeric += 1
            results.append(ArchiveProgramme(
                programme_id, row.get("name") or row.get("title"),
                int(start), int(end), row.get("descr") or row.get("description") or "",
                metadata={"provider": "stalker", "channel_id": channel_id,
                          "mark_archive": True},
            ))
        total = client._total_items(value)
        if scanned >= MAX_PROGRAMME_ROWS or (total is not None and scanned >= total):
            break
        client._paced_catalog_wait()
    log_event(
        "tv_archive",
        (
            "Stalker programme scan pages={} rows={} accepted={} "
            "numeric={} composite={} rejected_shape={} rejected_id={} "
            "rejected_duplicate={} rejected_channel={} rejected_archive={} "
            "rejected_time={} rejected_day={} observed_invalid_id={} "
            "observed_archive_disabled={}"
        ).format(
            pages, scanned, len(results), accepted_numeric, accepted_composite,
            rejected["shape"], rejected["id"], rejected["duplicate"],
            rejected["channel"], rejected["archive"], rejected["time"],
            rejected["day"], observed["invalid_id"],
            observed["archive_disabled"],
        ),
    )
    return sorted(results, key=lambda programme: (programme.start, programme.end))


def _check_link_error(value, depth=0):
    if not isinstance(value, dict) or depth > 4:
        return
    error = str(value.get("error") or "").strip().lower()
    if error not in ("", "0", "false", "none", "null"):
        if error == "limit":
            raise ContentError(
                N_("Portal request limit reached"), ContentError.RATE_LIMITED,
            )
        if any(word in error for word in ("auth", "token", "access denied")):
            raise ContentError(
                N_("Portal access denied"), ContentError.ACCESS_DENIED,
            )
        raise _unavailable()
    for name in ("data", "js", "result"):
        _check_link_error(value.get(name), depth + 1)


def _playback_url_with_extensions(
    client,
    channel,
    programme,
    media_extensions,
):
    """Mint one bounded archive link and report its pseudo-command type."""
    client._check_request_limits()
    channel_id, retention = _channel_context(channel)
    metadata = getattr(programme, "metadata", {})
    programme_id = _programme_identity(
        getattr(programme, "programme_id", ""), channel_id,
    )
    if (
        not isinstance(metadata, dict) or metadata.get("provider") != "stalker"
        or metadata.get("channel_id") != channel_id
        or not _positive_flag(metadata.get("mark_archive")) or not programme_id
    ):
        raise _unavailable()
    start = _number(getattr(programme, "start", None))
    end = _number(getattr(programme, "end", None))
    now = time.time()
    if (
        start is None or end is None or start <= 0
        or start < now - retention or end <= start or end > now
    ):
        raise _expired()
    media_extensions = tuple(media_extensions or ())
    if not media_extensions or any(
        extension not in ("mpg", "ts") for extension in media_extensions
    ):
        raise _unavailable()
    with client._playback_request_priority():
        for attempt, media_extension in enumerate(media_extensions):
            try:
                command = "auto /media/{}.{}".format(
                    programme_id, media_extension,
                )
                value = client._request("tv_archive", "create_link", (
                    ("cmd", command),
                    # These fields are part of the MAG/Ministra create-link
                    # contract used by compatible portals.  Empty/zero values
                    # preserve native archive selection without borrowing VOD
                    # or live-TV state.
                    ("series", ""),
                    ("forced_storage", ""),
                    ("disable_ad", "0"),
                    ("download", "0"),
                    ("force_ch_link_check", "0"),
                ))
                _check_link_error(value)
                selected, choice = (
                    client._create_link_playable_choice(value)
                )
                selected = str(selected or "").strip()
                placeholder = bool(_MAC_PLACEHOLDER.search(selected))
                if placeholder:
                    mac = str(getattr(client.account, "mac", "") or "")
                    if not mac:
                        raise _unavailable()
                    selected = _MAC_PLACEHOLDER.sub(
                        lambda unused_match: mac,
                        selected,
                    )
                # Re-validate after substituting only the provider-declared
                # MAC placeholder.  No host, path, query or command is logged.
                parser = getattr(client, "_playable_url", None)
                if callable(parser):
                    selected = parser(selected)
                client._check_request_limits()
                safe_choice = choice if isinstance(choice, dict) else {}
                log_event(
                    "tv_archive",
                    (
                        "Stalker link outcome=accepted id={} variant={} "
                        "selected={} valid={} distinct={} extension={} "
                        "query={} mac_placeholder={} media_headers=provider"
                    ).format(
                        "composite" if "_" in programme_id else "numeric",
                        media_extension,
                        str(safe_choice.get("selected", "unknown"))[:16],
                        str(safe_choice.get("valid", 0) or 0)[:12],
                        str(safe_choice.get("distinct", 0) or 0)[:12],
                        str(safe_choice.get("extension", "none"))[:12],
                        str(safe_choice.get("query", "no"))[:3],
                        "yes" if placeholder else "no",
                    ),
                )
                # The portal's create_link result is already the media
                # contract.  Feed it to Enigma2 unchanged, like a MAG client;
                # retain provider-supplied suffix headers but do not invent
                # additional media headers here.
                return selected, media_extension
            except ContentError as error:
                if (
                    attempt == 0 and len(media_extensions) == 2
                    and _is_archive_media_mismatch(error)
                ):
                    continue
                if _is_archive_media_mismatch(error):
                    raise _unavailable()
                raise


def playback_url_with_variant(client, channel, programme):
    """Return a playable URL plus the archive pseudo-command extension used."""
    programme_id = _programme_identity(
        getattr(programme, "programme_id", ""),
        _channel_context(channel)[0],
    )
    extensions = ("mpg", "ts") if "_" in programme_id else ("mpg",)
    return _playback_url_with_extensions(
        client, channel, programme, extensions,
    )


def playback_url(client, channel, programme):
    """Mint a fresh TV-archive link; never fall back to live or VOD."""
    return playback_url_with_variant(client, channel, programme)[0]


def decoder_retry_playback_url(
    client,
    channel,
    programme,
    previous_variant,
):
    """Mint one fresh same-program ticket for an alternate local engine."""
    channel_id, unused_retention = _channel_context(channel)
    programme_id = _programme_identity(
        getattr(programme, "programme_id", ""), channel_id,
    )
    if not programme_id or previous_variant not in ("mpg", "ts"):
        return None
    # Keep the pseudo-extension that the portal accepted.  A decoder failure
    # is not evidence that the server supports another archive route.  Mint a
    # fresh same-programme ticket and let only the local engine change.
    extension = previous_variant
    # Authentication, rate limiting, cancellation, deadlines and server errors
    # propagate.  The facade permits this hook only once, so there is no hidden
    # retry loop and no live/VOD command can be substituted.
    return _playback_url_with_extensions(
        client, channel, programme, (extension,),
    )


def playback_keepalive(client, initial=False):
    """Send the catchup-only MAG portal activity signal.

    This is session activity for an already explicit archive open, not the
    Live-TV automatic refresh removed in R93.  It never creates or replaces a
    stream link.
    """
    client._check_request_limits()
    client._request("watchdog", "get_events", (
        ("cur_play_type", "11"),
        ("event_active_id", "0"),
        ("init", "1" if initial else "0"),
    ))
    client._check_request_limits()
    return True

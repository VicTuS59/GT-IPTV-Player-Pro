# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account-scoped TV archives, loaded only from the archive browser."""

import calendar
import datetime
import math
import re
import threading
import time
from collections import OrderedDict
from contextlib import contextmanager
from urllib.parse import quote, urlunsplit

from .category_preferences import LiveCategoryPreferenceStore, content_item_category_ids
from .category_visibility import category_ids_visible, category_visibility_ids
from .content import ContentError, ContentItem, XtreamContentClient, _decode_epg_description, _decode_epg_text
from .i18n import N_, _
from .playback import resume_account_scope


MAX_ARCHIVE_DAYS = 31
MAX_ARCHIVE_CHANNELS = 20000
MAX_ARCHIVE_PROGRAMMES = 4096
ARCHIVE_CACHE_SECONDS = 180
ARCHIVE_CACHE_ENTRIES = 6
MAX_PROGRAMME_SECONDS = 24 * 3600
_NUMBER = re.compile(r"^[0-9]{1,12}(?:\.[0-9]{1,3})?$")


def _positive_number(value, maximum):
    raw = str(value or "").strip()
    if not _NUMBER.fullmatch(raw):
        return 0.0
    try:
        number = float(raw)
        return min(number, maximum) if math.isfinite(number) and number > 0 else 0.0
    except (ValueError, OverflowError):
        return 0.0


def _true(value):
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _datetime_value(value):
    raw = str(value or "").strip()
    if not raw or len(raw) > 48 or not re.match(r"^\d{4}-\d{2}-\d{2}[ T]", raw):
        return None
    try:
        return datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return None


def _timestamp(value, timezone=None):
    raw = str(value or "").strip()
    if raw.isascii() and raw.isdigit() and len(raw) <= 13:
        stamp = int(raw)
        if stamp > 100000000000:
            stamp //= 1000
        return stamp if 0 < stamp < 4102444800 else 0
    value = _datetime_value(raw)
    if value is None or (value.tzinfo is None and timezone is None):
        return 0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone)
    try:
        stamp = int(value.timestamp())
        return stamp if 0 < stamp < 4102444800 else 0
    except (ValueError, OverflowError, OSError):
        return 0


class ArchiveChannel(object):
    def __init__(self, item, days, metadata=None):
        self.item = item
        retention = _positive_number(days, MAX_ARCHIVE_DAYS)
        self.days = int(math.ceil(retention))
        self.metadata = dict(metadata or {})
        self.metadata.setdefault("archive_seconds", int(retention * 86400))
        self.name = str(getattr(item, "name", "") or "")
        self.stream_id = str(getattr(item, "stream_id", "") or "")
        self.category_id = str(getattr(item, "category_id", "") or "")


class ArchiveProgramme(object):
    def __init__(self, programme_id, title, start, end, description="", metadata=None):
        self.programme_id = str(programme_id or "")[:160]
        self.title = _decode_epg_text(title) or _("No programme information")
        self.start = _timestamp(start)
        self.end = _timestamp(end)
        self.description = _decode_epg_description(description)
        self.metadata = dict(metadata or {})


def programme_available(channel, programme, now=None):
    now = int(time.time() if now is None else now)
    retention = _positive_number(
        channel.metadata.get("archive_seconds", channel.days * 86400),
        MAX_ARCHIVE_DAYS * 86400,
    )
    return bool(
        retention
        and programme.start > 0
        and now - retention <= programme.start < programme.end <= now
        and programme.end - programme.start <= MAX_PROGRAMME_SECONDS
    )


def _day_value(day):
    if isinstance(day, datetime.datetime):
        return day.date()
    if isinstance(day, datetime.date):
        return day
    if day is None:
        return None
    try:
        return datetime.date.fromisoformat(str(day))
    except (ValueError, TypeError):
        raise ContentError(N_("Could not load the TV archive."), ContentError.INVALID_RESPONSE)


class ArchiveClient(object):
    """Small transient facade; no archive requests occur at construction."""

    async_playback_resolution = True

    def __init__(self, account, content_client=None, clock=None, category_store=None):
        self.account = account
        self.source_type = str(getattr(account, "source_type", "xtream") or "xtream").lower()
        if content_client is None:
            if self.source_type == "stalker":
                from .stalker import StalkerPortalClient
                content_client = StalkerPortalClient(account)
            elif self.source_type == "m3u":
                from .m3u import M3UContentClient
                content_client = M3UContentClient(account)
            else:
                content_client = XtreamContentClient(account)
        self.client = content_client
        self._clock = clock or time.time
        self._category_store = category_store or LiveCategoryPreferenceStore()
        self._context = threading.local()
        self._lock = threading.RLock()
        self._channels = {}
        self._channel_generation = 0
        self._programmes = OrderedDict()
        # Invalidating a programme key must also fence an already-running
        # request.  The worker may have passed its cancellation check just
        # before the UI requested a refresh, so cache publication is guarded
        # by this facade-wide generation as well as the request event.
        self._programme_cache_generation = 0
        self._timezone = None
        self._timezone_checked = False
        self._account_scope = resume_account_scope(self)
        self._account_password = str(getattr(account, "password", "") or "")
        self._check()

    @contextmanager
    def request_scope(self, cancel_event=None, deadline=None, timeout=None):
        previous = getattr(self._context, "limits", (None, None))
        if cancel_event is None:
            cancel_event = previous[0]
        if previous[1] is not None:
            deadline = previous[1] if deadline is None else min(deadline, previous[1])
        self._context.limits = (cancel_event, deadline)
        try:
            self._check()
            factory = getattr(self.client, "request_scope", None)
            if callable(factory):
                with factory(cancel_event=cancel_event, deadline=deadline, timeout=timeout):
                    yield self
            else:
                yield self
            self._check()
        finally:
            self._context.limits = previous

    def _check(self):
        if (resume_account_scope(self) != self._account_scope
                or resume_account_scope(self.client) != self._account_scope
                or str(getattr(self.account, "password", "") or "") != self._account_password
                or str(getattr(getattr(self.client, "account", None), "password", "") or "") != self._account_password
                or str(getattr(self.account, "source_type", "xtream") or "xtream").lower() != self.source_type):
            raise ContentError(N_("Invalid playback item"), ContentError.INVALID_PLAYBACK_ITEM)
        event, deadline = getattr(self._context, "limits", (None, None))
        if event is not None and event.is_set():
            raise ContentError(N_("Request cancelled"), ContentError.CANCELLED)
        if deadline is not None and time.monotonic() >= float(deadline):
            raise ContentError(N_("Request timed out"), ContentError.DEADLINE)

    def _adapter(self):
        if self.source_type == "stalker":
            from . import catchup_stalker
            return catchup_stalker
        if self.source_type == "m3u":
            from . import catchup_m3u
            return catchup_m3u
        if self.source_type != "xtream":
            raise ContentError(N_("TV archive is not supported by this source."), ContentError.UNSUPPORTED_TYPE)
        return None

    def _visible_channels(self, channels):
        preferences = self._category_store.load(resume_account_scope(self))
        if not (preferences.hidden or preferences.include_terms or preferences.exclude_terms):
            return channels
        categories = self.client.load_categories("live")
        self._check()
        visibility = category_visibility_ids(categories, preferences)
        return [channel for channel in channels if category_ids_visible(
            content_item_category_ids(channel.item), visibility
        )]

    def load_channels(self, cancel_event=None, deadline=None):
        with self.request_scope(cancel_event, deadline):
            adapter = self._adapter()
            channels = adapter.load_channels(self.client) if adapter else self._xtream_channels()
            self._check()
            valid = []
            seen = set()
            for channel in channels[:MAX_ARCHIVE_CHANNELS]:
                self._check()
                if channel.days <= 0 or not channel.stream_id or channel.stream_id in seen:
                    continue
                seen.add(channel.stream_id)
                valid.append(channel)
            valid = self._visible_channels(valid)
            self._check()
            with self._lock:
                self._channels = {channel.stream_id: channel for channel in valid}
                self._channel_generation += 1
                self._programme_cache_generation += 1
                self._programmes.clear()
            return valid

    def _known_channel(self, channel):
        with self._lock:
            known = self._channels.get(str(getattr(channel, "stream_id", "")))
        if known is not channel:
            raise ContentError(N_("Archive playback is unavailable for this channel."), ContentError.INVALID_CHANNEL_ID)
        return known

    def should_cache_programmes(self, programmes):
        """Return whether one normalised programme result is reusable.

        A Stalker portal can answer an authenticated EPG request with an
        HTTP-200 empty wrapper while its session is being renewed.  Treating
        that transient value as authoritative made both the facade and the UI
        replay it for their full TTLs.  Positive Stalker results remain
        cacheable; other providers retain their existing empty-result cache.
        """
        return self.source_type != "stalker" or bool(programmes)

    def invalidate_programme_cache(self, channel=None, day=None):
        """Forget reusable programme data and fence in-flight publication.

        ``day`` is deliberately mapped through the same provider-aware key as
        :meth:`load_programmes`: Xtream fetches one all-days table, while M3U
        and Stalker fetch the selected day.  No provider request is made.
        """
        day = _day_value(day)
        adapter = self._adapter()
        with self._lock:
            self._programme_cache_generation += 1
            if channel is None:
                self._programmes.clear()
                return
            stream_id = str(getattr(channel, "stream_id", "") or "")
            if not stream_id:
                return
            cache_day = day.isoformat() if day is not None and adapter else "*"
            self._programmes.pop((stream_id, cache_day), None)

    def load_programmes(self, channel, day=None, cancel_event=None, deadline=None):
        day = _day_value(day)
        with self.request_scope(cancel_event, deadline):
            self._known_channel(channel)
            adapter = self._adapter()
            cache_day = day.isoformat() if day is not None and adapter else "*"
            key = (channel.stream_id, cache_day)
            with self._lock:
                channel_generation = self._channel_generation
                cache_generation = self._programme_cache_generation
                cached = self._programmes.get(key)
                if (cached and self._clock() - cached[0] < ARCHIVE_CACHE_SECONDS
                        and self.should_cache_programmes(cached[1])):
                    self._programmes.move_to_end(key)
                    rows = cached[1]
                else:
                    # Also discard an empty Stalker value left by an older
                    # build instead of serving it until the old TTL expires.
                    if cached:
                        self._programmes.pop(key, None)
                    rows = None
            if rows is None:
                rows = (adapter.load_programmes(self.client, channel, day=day)
                        if adapter else self._xtream_programmes(channel))
                self._check()
                valid = []
                seen = set()
                now = self._clock()
                for programme in rows[:MAX_ARCHIVE_PROGRAMMES]:
                    self._check()
                    identity = (programme.programme_id, programme.start, programme.end)
                    if identity in seen or not programme_available(channel, programme, now):
                        continue
                    seen.add(identity)
                    programme._archive_owner = self
                    programme._archive_channel = channel
                    programme._archive_generation = channel_generation
                    valid.append(programme)
                rows = sorted(valid, key=lambda row: (row.start, row.end, row.programme_id))
                with self._lock:
                    if (
                        channel_generation == self._channel_generation
                        and cache_generation == self._programme_cache_generation
                        and self._channels.get(channel.stream_id) is channel
                        and self.should_cache_programmes(rows)
                    ):
                        self._programmes[key] = (self._clock(), rows)
                        self._programmes.move_to_end(key)
                        while len(self._programmes) > ARCHIVE_CACHE_ENTRIES:
                            self._programmes.popitem(last=False)
                    # On an empty or invalidated result, leave the key alone.
                    # A newer-generation request may already have published a
                    # positive value while this older network call unwound.
                self._known_channel(channel)
            now = self._clock()
            return [row for row in rows if programme_available(channel, row, now)
                    and (day is None or datetime.datetime.fromtimestamp(row.start).date() == day)]

    def playback_item(self, channel, programme):
        self._validate_programme(channel, programme)
        item = ContentItem("catchup", channel.stream_id, programme.title,
                           category_id=channel.category_id, extension="ts",
                           icon=getattr(channel.item, "icon", ""),
                           duration=str(programme.end - programme.start), plot=programme.description)
        item.archive_channel = channel
        item.archive_programme = programme
        item.archive_owner = self
        item.source_type = self.source_type
        # Decoder compatibility retries belong to this one explicit open.  Do
        # not keep provider URLs or retry state on the account-wide facade.
        item._archive_decoder_retry_used = False
        item._archive_provider_variant = ""
        return item

    def _validate_programme(self, channel, programme):
        self._check()
        self._known_channel(channel)
        if (getattr(programme, "_archive_owner", None) is not self
                or getattr(programme, "_archive_channel", None) is not channel
                or getattr(programme, "_archive_generation", None) != self._channel_generation):
            raise ContentError(N_("Invalid playback item"), ContentError.INVALID_PLAYBACK_ITEM)
        if not programme_available(channel, programme, self._clock()):
            raise ContentError(N_("This programme is no longer available in the TV archive."), ContentError.PLAYBACK_URL)

    def playback_url(self, item, cancel_event=None, deadline=None):
        with self.request_scope(cancel_event, deadline):
            if getattr(item, "archive_owner", None) is not self or item.content_type != "catchup":
                raise ContentError(N_("Invalid playback item"), ContentError.INVALID_PLAYBACK_ITEM)
            channel, programme = item.archive_channel, item.archive_programme
            self._validate_programme(channel, programme)
            adapter = self._adapter()
            if self.source_type == "stalker":
                url, variant = adapter.playback_url_with_variant(
                    self.client, channel, programme,
                )
                item._archive_provider_variant = variant
            elif adapter:
                url = adapter.playback_url(self.client, channel, programme)
                item._archive_provider_variant = "declared"
            else:
                variant = self._xtream_extension()
                url = self._xtream_playback_url(
                    channel, programme, extension=variant,
                )
                item._archive_provider_variant = variant
            self._check()
            return url

    def refresh_playback_url(self, item, cancel_event=None, deadline=None):
        return self.playback_url(item, cancel_event, deadline)

    def decoder_retry_playback_url(
        self,
        item,
        previous_url,
        cancel_event=None,
        deadline=None,
    ):
        """Return one provider-safe archive fallback after decoder failure.

        This API is deliberately narrower than ``refresh_playback_url``.  It
        cannot invent an archive route and each playback item can consume it
        at most once.  The caller remains responsible for detecting a real
        decoder startup failure before invoking it.
        """
        previous_url = str(previous_url or "").strip()
        if not previous_url:
            return ""
        with self.request_scope(cancel_event, deadline):
            if (
                getattr(item, "archive_owner", None) is not self
                or getattr(item, "content_type", "") != "catchup"
            ):
                raise ContentError(
                    N_("Invalid playback item"),
                    ContentError.INVALID_PLAYBACK_ITEM,
                )
            channel, programme = item.archive_channel, item.archive_programme
            self._validate_programme(channel, programme)
            with self._lock:
                if getattr(item, "_archive_decoder_retry_used", False):
                    return ""
                # Claim the only retry before provider work so concurrent or
                # repeated decoder callbacks cannot create a hidden loop.
                item._archive_decoder_retry_used = True
                previous_variant = str(
                    getattr(item, "_archive_provider_variant", "") or ""
                ).lower()

            adapter = self._adapter()
            if adapter is None:
                # Xtream catchup URLs are deterministic account routes, not
                # disposable tickets.  Changing ``ts`` to ``m3u8`` (or the
                # reverse) can invent a route the provider never advertised.
                # Returning no provider candidate lets the watchdog retry the
                # exact URL through the alternate local engine.
                return ""
            else:
                resolver = getattr(
                    adapter, "decoder_retry_playback_url", None,
                )
                if not callable(resolver):
                    return ""
                if self.source_type == "stalker":
                    result = resolver(
                        self.client,
                        channel,
                        programme,
                        previous_variant,
                    )
                    if not result:
                        return ""
                    url, variant = result
                    item._archive_provider_variant = variant
                else:
                    url = resolver(
                        self.client,
                        channel,
                        programme,
                        previous_url,
                    )
            self._check()
            url = str(url or "").strip()
            return url if url and url != previous_url else ""

    def archive_playback_keepalive(
        self,
        item,
        initial=False,
        cancel_event=None,
        deadline=None,
    ):
        """Signal one explicit Stalker catchup session without touching live."""
        if self.source_type != "stalker":
            return False
        with self.request_scope(cancel_event, deadline):
            if (
                getattr(item, "archive_owner", None) is not self
                or getattr(item, "content_type", "") != "catchup"
            ):
                raise ContentError(
                    N_("Invalid playback item"),
                    ContentError.INVALID_PLAYBACK_ITEM,
                )
            self._validate_programme(
                item.archive_channel,
                item.archive_programme,
            )
            adapter = self._adapter()
            sender = getattr(adapter, "playback_keepalive", None)
            if not callable(sender):
                return False
            result = bool(sender(self.client, initial=bool(initial)))
            self._check()
            return result

    def _xtream_channels(self):
        payload = self.client._request_json("get_live_streams")
        if not isinstance(payload, list):
            raise ContentError(N_("Could not load the TV archive."), ContentError.CONTENT_RESPONSE)
        channels = []
        for raw in payload[:MAX_ARCHIVE_CHANNELS]:
            self._check()
            if not isinstance(raw, dict) or not _true(raw.get("tv_archive")):
                continue
            days = _positive_number(raw.get("tv_archive_duration"), MAX_ARCHIVE_DAYS)
            if not days:
                continue
            item = self.client._content_item("live", raw)
            if item is not None:
                memberships = raw.get("category_ids")
                if isinstance(memberships, list):
                    item.portal_category_ids = tuple(str(value)[:128] for value in memberships[:128])
                channels.append(ArchiveChannel(item, days))
        return channels

    def _xtream_timezone(self):
        if self._timezone_checked:
            return self._timezone
        payload = self.client._request_json("")
        info = payload.get("server_info", {}) if isinstance(payload, dict) else {}
        if not isinstance(info, dict):
            info = {}
        name = str(info.get("timezone") or "")[:128]
        zone = None
        if name:
            try:
                from zoneinfo import ZoneInfo
                zone = ZoneInfo(name)
            except (ImportError, ValueError, KeyError):
                pass
        if zone is None:
            wall = _datetime_value(info.get("time_now"))
            epoch = _timestamp(info.get("timestamp_now"))
            if wall is not None and epoch:
                offset = calendar.timegm(wall.timetuple()) - epoch
                if abs(offset) <= 14 * 3600 and offset % 60 == 0:
                    zone = datetime.timezone(datetime.timedelta(seconds=offset))
        self._check()
        self._timezone = zone
        self._timezone_checked = True
        return zone

    def _xtream_programmes(self, channel):
        payload = self.client._request_json("get_simple_data_table", {"stream_id": channel.stream_id})
        rows = payload.get("epg_listings", []) if isinstance(payload, dict) else payload
        if not isinstance(rows, list):
            raise ContentError(N_("Could not load the TV archive."), ContentError.INVALID_RESPONSE)
        programmes = []
        for row in rows[:MAX_ARCHIVE_PROGRAMMES]:
            self._check()
            if not isinstance(row, dict):
                continue
            if "has_archive" in row and not _true(row["has_archive"]):
                continue
            if row.get("stream_id") is not None and str(row["stream_id"]) != channel.stream_id:
                continue
            start = _timestamp(row.get("start_timestamp"))
            end = _timestamp(row.get("stop_timestamp") or row.get("end_timestamp"))
            raw_start = _datetime_value(row.get("start"))
            if not start or not end or raw_start is None:
                zone = self._xtream_timezone()
                start = start or _timestamp(row.get("start"), zone)
                end = end or _timestamp(row.get("end") or row.get("stop"), zone)
                if raw_start is None and start and zone is not None:
                    raw_start = datetime.datetime.fromtimestamp(start, zone)
            if not start or not end or raw_start is None:
                continue
            programme = ArchiveProgramme(
                row.get("id") or "{}-{}".format(channel.stream_id, start),
                row.get("title") or row.get("name"), start, end,
                row.get("description") or row.get("descr", ""),
                {"url_start": raw_start.strftime("%Y-%m-%d:%H-%M")},
            )
            programmes.append(programme)
        return programmes

    def _xtream_extension(self):
        extension = str(
            getattr(self.account, "output_format", "ts") or "ts"
        ).lower()
        return extension if extension in ("ts", "m3u8") else "ts"

    def _xtream_playback_url(self, channel, programme, extension=None):
        date = str(programme.metadata.get("url_start") or "")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}:\d{2}-\d{2}", date):
            raise ContentError(N_("Archive playback is unavailable for this channel."), ContentError.PLAYBACK_URL)
        duration = max(1, int(math.ceil((programme.end - programme.start) / 60.0)))
        extension = str(extension or self._xtream_extension()).lower()
        if extension not in ("ts", "m3u8"):
            extension = "ts"
        path = "{}/timeshift/{}/{}/{}/{}/{}.{}".format(
            self.account.base_path.rstrip("/"),
            quote(self.account.username, safe=""),
            quote(self.account.password, safe=""),
            duration, date, channel.stream_id, extension,
        )
        return urlunsplit((self.account.scheme, self.account.netloc, path, "", ""))

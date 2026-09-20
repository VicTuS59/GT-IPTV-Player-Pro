# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""DVB EPG support built around an Xtream XMLTV guide.

This module deliberately contains no Enigma2 or GUI imports.  Network and
XML work is synchronous and is therefore intended to run on a worker thread.
The caller remains responsible for invoking ``eEPGCache.importEvents`` on the
appropriate Enigma2 thread.

Only real DVB references found in the receiver's bouquets and ``lamedb`` are
eligible for automatic matching or EPGImport output.  IPTV service references
are never generated or accepted.
"""

import calendar
import datetime
import gzip
import hashlib
import json
import math
import os
import re
import stat
import time
import unicodedata
import uuid
from collections import OrderedDict, defaultdict, deque
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from xml.etree import ElementTree
from xml.sax.saxutils import escape, quoteattr


CONFIG_DIRECTORY = "/etc/enigma2/gtiptvplayer"
DVB_EPG_DIRECTORY = os.path.join(CONFIG_DIRECTORY, "dvb-epg")
MANUAL_MAPPING_PATH = os.path.join(DVB_EPG_DIRECTORY, "mappings.json")
XMLTV_CACHE_DIRECTORY = "/tmp/gtiptvplayerpro-dvb-epg"

EPGIMPORT_DIRECTORY = "/etc/epgimport"
EPGIMPORT_CHANNELS_PATH = os.path.join(
    EPGIMPORT_DIRECTORY,
    "gtiptvplayerpro.channels.xml",
)
EPGIMPORT_SOURCES_PATH = os.path.join(
    EPGIMPORT_DIRECTORY,
    "gtiptvplayerpro.sources.xml",
)

MAPPING_VERSION = 1
MAX_MAPPING_BYTES = 2 * 1024 * 1024
MAX_MANUAL_MAPPINGS = 5000

MAX_LAMEDB_BYTES = 32 * 1024 * 1024
MAX_BOUQUET_BYTES = 4 * 1024 * 1024
MAX_BOUQUET_FILES = 512
MAX_DVB_SERVICES = 100000
MAX_LINE_BYTES = 32 * 1024

MAX_XMLTV_DOWNLOAD_BYTES = 64 * 1024 * 1024
MAX_XMLTV_BYTES = 128 * 1024 * 1024
MIN_XMLTV_FREE_BYTES = 16 * 1024 * 1024
MAX_XMLTV_CHANNELS = 50000
MAX_XMLTV_PROGRAMMES = 500000
MAX_XMLTV_DEPTH = 32
MAX_XMLTV_NODES = 4 * 1000 * 1000
MAX_XMLTV_TOP_LEVEL_NODES = 4096
MAX_XMLTV_TEXT_CHARACTERS = 64 * 1024 * 1024
MAX_XMLTV_TOP_LEVEL_TEXT_CHARACTERS = 128 * 1024
MAX_XMLTV_BYTES_BETWEEN_EVENTS = 1024 * 1024
MAX_XMLTV_ID_LENGTH = 256
MAX_CHANNEL_NAME_LENGTH = 256
MAX_TITLE_LENGTH = 512
MAX_SUBTITLE_LENGTH = 512
MAX_DESCRIPTION_LENGTH = 8192
MAX_CATEGORY_LENGTH = 160
MAX_CATEGORIES_PER_PROGRAMME = 16
MAX_EVENTS_PER_XMLTV_CHANNEL = 10000
MAX_IMPORT_EVENTS = 150000
MAX_IMPORT_TEXT_CHARACTERS = 24 * 1024 * 1024
DEFAULT_IMPORT_PAST_SECONDS = 12 * 60 * 60
DEFAULT_IMPORT_FUTURE_SECONDS = 8 * 24 * 60 * 60

DEFAULT_DOWNLOAD_TIMEOUT = 45.0
MAX_DOWNLOAD_TIMEOUT = 180.0
MAX_REDIRECTS = 3
READ_CHUNK_BYTES = 64 * 1024

_TIMESTAMP_RE = re.compile(
    r"^\s*(\d{8}|\d{10}|\d{12}|\d{14})"
    r"(?:\s*(Z|[+-]\d{2}:?\d{2}))?\s*$",
    re.IGNORECASE,
)
_BOUQUET_REFERENCE_RE = re.compile(
    r'FROM\s+BOUQUET\s+"([^"/\\]+)"',
    re.IGNORECASE,
)
_SAFE_BOUQUET_FILENAME_RE = re.compile(r"^[A-Za-z0-9_.+-]+\.tv$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f]")
_QUALITY_SUFFIXES = frozenset(("sd", "hd", "fhd", "uhd", "4k", "8k"))


class DvbEpgError(RuntimeError):
    """Base error raised by the DVB EPG core."""


class MappingStoreError(DvbEpgError):
    """The persistent manual-mapping store is invalid or unavailable."""


class XmltvError(DvbEpgError):
    """An XMLTV document is invalid or exceeds a safety limit."""


class XmltvDownloadError(DvbEpgError):
    """The Xtream XMLTV guide could not be downloaded safely."""


class OperationCancelled(DvbEpgError):
    """A worker operation was cancelled or exceeded its deadline."""


class DvbService(object):
    """A real DVB service present in both a bouquet and ``lamedb``."""

    __slots__ = (
        "service_ref",
        "name",
        "provider",
        "bouquets",
        "bouquet_files",
        "bouquet_name",
        "bouquet_file",
        "identity",
    )

    def __init__(
        self,
        service_ref,
        name,
        provider="",
        bouquets=(),
        bouquet_files=(),
        identity=None,
    ):
        parsed = parse_dvb_service_reference(service_ref)
        if parsed is None:
            raise ValueError("invalid DVB service reference")
        self.service_ref = parsed["service_ref"]
        self.name = _clean_single_line(name, MAX_CHANNEL_NAME_LENGTH)
        self.provider = _clean_single_line(provider, MAX_CHANNEL_NAME_LENGTH)
        self.bouquets = tuple(
            _clean_single_line(item, MAX_CHANNEL_NAME_LENGTH)
            for item in bouquets
            if _clean_single_line(item, MAX_CHANNEL_NAME_LENGTH)
        )
        self.bouquet_files = tuple(
            str(item or "").strip()
            for item in bouquet_files
            if _SAFE_BOUQUET_FILENAME_RE.match(str(item or "").strip()) is not None
        )
        self.bouquet_name = self.bouquets[0] if self.bouquets else ""
        self.bouquet_file = self.bouquet_files[0] if self.bouquet_files else ""
        self.identity = identity or parsed["identity"]

    def __repr__(self):
        return "DvbService(ref={!r}, name={!r})".format(
            self.service_ref,
            self.name,
        )


class DvbBouquet(object):
    """A television bouquet containing at least one real lamedb service."""

    __slots__ = ("filename", "name", "service_count")

    def __init__(self, filename, name, service_count):
        filename = str(filename or "").strip()
        if _SAFE_BOUQUET_FILENAME_RE.match(filename) is None:
            raise ValueError("invalid DVB bouquet filename")
        self.filename = filename
        self.name = _clean_single_line(name, MAX_CHANNEL_NAME_LENGTH) or filename
        self.service_count = int(service_count)
        if self.service_count <= 0:
            raise ValueError("empty DVB bouquet")

    def __repr__(self):
        return "DvbBouquet(filename={!r}, name={!r}, services={!r})".format(
            self.filename,
            self.name,
            self.service_count,
        )


class XmltvChannel(object):
    """Minimal XMLTV channel metadata; artwork is intentionally ignored."""

    __slots__ = ("xmltv_id", "display_names")

    def __init__(self, xmltv_id, display_names=()):
        self.xmltv_id = _validated_xmltv_id(xmltv_id)
        names = []
        seen = set()
        for value in display_names:
            name = _clean_xml_text(value, MAX_CHANNEL_NAME_LENGTH)
            if name and name not in seen:
                seen.add(name)
                names.append(name)
        self.display_names = tuple(names)

    @property
    def display_name(self):
        return self.display_names[0] if self.display_names else self.xmltv_id

    def __repr__(self):
        return "XmltvChannel(id={!r}, name={!r})".format(
            self.xmltv_id,
            self.display_name,
        )


class XmltvScanResult(object):
    """Bounded metadata collected while scanning an XMLTV document."""

    __slots__ = (
        "channels",
        "channel_elements",
        "programme_elements",
        "invalid_programmes",
    )

    def __init__(
        self,
        channels,
        channel_elements=0,
        programme_elements=0,
        invalid_programmes=0,
    ):
        self.channels = tuple(channels)
        self.channel_elements = int(channel_elements)
        self.programme_elements = int(programme_elements)
        self.invalid_programmes = int(invalid_programmes)


class ManualMapping(object):
    """A source-scoped user decision for one DVB service.

    A blocked entry has an empty ``xmltv_id`` and prevents a later automatic
    match from silently re-enabling EPG for that DVB service.
    """

    __slots__ = (
        "source_key",
        "source_name",
        "service_ref",
        "service_name",
        "xmltv_id",
        "xmltv_name",
        "blocked",
        "updated_at",
    )

    def __init__(
        self,
        source_key,
        service_ref,
        xmltv_id="",
        blocked=False,
        source_name="",
        service_name="",
        xmltv_name="",
        updated_at=None,
    ):
        self.source_key = _validated_source_key(source_key)
        parsed = parse_dvb_service_reference(service_ref)
        if parsed is None:
            raise ValueError("manual mapping contains a non-DVB service reference")
        self.service_ref = parsed["service_ref"]
        self.blocked = bool(blocked)
        xmltv_id = str(xmltv_id or "").strip()
        if self.blocked:
            if xmltv_id:
                raise ValueError("a blocked manual mapping cannot contain an XMLTV ID")
            self.xmltv_id = ""
        else:
            self.xmltv_id = _validated_xmltv_id(xmltv_id)
        self.source_name = _clean_single_line(source_name, 160)
        self.service_name = _clean_single_line(service_name, MAX_CHANNEL_NAME_LENGTH)
        self.xmltv_name = _clean_single_line(xmltv_name, MAX_CHANNEL_NAME_LENGTH)
        if updated_at is None:
            updated_at = int(time.time())
        try:
            updated_at = int(updated_at)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("manual mapping timestamp is invalid") from error
        if updated_at < 0 or updated_at > 0x7FFFFFFFFFFFFFFF:
            raise ValueError("manual mapping timestamp is invalid")
        self.updated_at = updated_at

    def to_dict(self):
        return {
            "source_key": self.source_key,
            "source_name": self.source_name,
            "service_ref": self.service_ref,
            "service_name": self.service_name,
            "xmltv_id": self.xmltv_id,
            "xmltv_name": self.xmltv_name,
            "blocked": self.blocked,
            "updated_at": self.updated_at,
        }

    def __repr__(self):
        state = "blocked" if self.blocked else self.xmltv_id
        return "ManualMapping(source={!r}, ref={!r}, state={!r})".format(
            self.source_key,
            self.service_ref,
            state,
        )


class MappingLoadResult(object):
    """Result of redundant primary/backup mapping-store recovery."""

    __slots__ = ("entries", "generation", "recovered", "source_path")

    def __init__(self, entries=(), generation=0, recovered=False, source_path=""):
        self.entries = tuple(entries)
        self.generation = int(generation)
        self.recovered = bool(recovered)
        self.source_path = str(source_path or "")


class ChannelMatchResult(object):
    """Conservative manual and automatic XMLTV-to-DVB matching result."""

    __slots__ = (
        "mappings",
        "manual_ids",
        "automatic_ids",
        "blocked_services",
        "ambiguous_ids",
        "unmatched_ids",
        "stale_manual_services",
    )

    def __init__(
        self,
        mappings,
        manual_ids=(),
        automatic_ids=(),
        blocked_services=(),
        ambiguous_ids=None,
        unmatched_ids=(),
        stale_manual_services=(),
    ):
        self.mappings = OrderedDict(mappings)
        self.manual_ids = tuple(manual_ids)
        self.automatic_ids = tuple(automatic_ids)
        self.blocked_services = tuple(blocked_services)
        self.ambiguous_ids = OrderedDict(ambiguous_ids or ())
        self.unmatched_ids = tuple(unmatched_ids)
        self.stale_manual_services = tuple(stale_manual_services)


class ImportEventResult(object):
    """XMLTV event tuples grouped for ``eEPGCache.importEvents``.

    ``groups`` contains ``(services, events)`` pairs.  ``services`` is a tuple
    of DVB service-reference strings and every event is exactly the supported
    six-field tuple ``(start, duration, title, subtitle, description,
    category)``.  No event ID is added.
    """

    __slots__ = (
        "groups",
        "programme_count",
        "imported_count",
        "invalid_count",
        "unmapped_count",
    )

    def __init__(
        self,
        groups=(),
        programme_count=0,
        imported_count=0,
        invalid_count=0,
        unmapped_count=0,
    ):
        self.groups = tuple(groups)
        self.programme_count = int(programme_count)
        self.imported_count = int(imported_count)
        self.invalid_count = int(invalid_count)
        self.unmapped_count = int(unmapped_count)

    @property
    def event_map(self):
        """Return ``service_ref -> events`` for status/UI consumers."""
        output = OrderedDict()
        for services, events in self.groups:
            for service_ref in services:
                output[service_ref] = events
        return output


class XmltvDownloadResult(object):
    """Metadata about an atomically installed XMLTV cache file."""

    __slots__ = (
        "path",
        "size_bytes",
        "channel_count",
        "programme_count",
        "redirect_count",
    )

    def __init__(
        self,
        path,
        size_bytes,
        channel_count,
        programme_count,
        redirect_count=0,
    ):
        self.path = str(path)
        self.size_bytes = int(size_bytes)
        self.channel_count = int(channel_count)
        self.programme_count = int(programme_count)
        self.redirect_count = int(redirect_count)


def parse_xmltv_timestamp(value, default_offset_minutes=0):
    """Convert an XMLTV timestamp to a UTC epoch without using local time.

    Numeric ``+HHMM``/``-HHMM`` and ``+HH:MM``/``-HH:MM`` offsets are
    honoured.  A timestamp without an offset uses the explicit
    ``default_offset_minutes`` value; the default is UTC.  Device timezone and
    DST settings are never consulted.
    """
    text = str(value or "")
    match = _TIMESTAMP_RE.match(text)
    if match is None:
        raise ValueError("invalid XMLTV timestamp")
    digits, offset_text = match.groups()
    padded = digits + ("0" * (14 - len(digits)))
    try:
        moment = datetime.datetime(
            int(padded[0:4]),
            int(padded[4:6]),
            int(padded[6:8]),
            int(padded[8:10]),
            int(padded[10:12]),
            int(padded[12:14]),
        )
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("invalid XMLTV timestamp") from error

    if offset_text and offset_text.upper() == "Z":
        offset_minutes = 0
    elif offset_text:
        sign = -1 if offset_text[0] == "-" else 1
        compact = offset_text[1:].replace(":", "")
        hours = int(compact[0:2])
        minutes = int(compact[2:4])
        if hours > 14 or minutes > 59 or (hours == 14 and minutes):
            raise ValueError("invalid XMLTV UTC offset")
        offset_minutes = sign * ((hours * 60) + minutes)
    else:
        offset_minutes = _validated_utc_offset(default_offset_minutes)

    utc_moment = moment - datetime.timedelta(minutes=offset_minutes)
    try:
        return int(calendar.timegm(utc_moment.utctimetuple()))
    except (OverflowError, ValueError) as error:
        raise ValueError("XMLTV timestamp is outside the supported range") from error


def parse_dvb_service_reference(value):
    """Return parsed fields for a plain DVB service reference, or ``None``.

    The accepted form has the ten numeric Enigma2 fields and no stream URL or
    path.  Reference type ``1`` and flag field ``0`` are required, which
    rejects markers, directories, alternatives and all common IPTV forms.
    """
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 192
        or _CONTROL_RE.search(text)
        or re.match(r"^[0-9A-Fa-f:]+$", text) is None
    ):
        return None
    parts = text.split(":")
    if parts and parts[-1] == "":
        parts.pop()
    if len(parts) != 10 or any(not part for part in parts):
        return None
    try:
        fields = tuple(int(part, 16) for part in parts)
    except (TypeError, ValueError, OverflowError):
        return None
    if fields[0] != 1 or fields[1] != 0:
        return None
    if fields[2] <= 0 or fields[2] > 0xFFFF:
        return None
    if any(field < 0 or field > 0xFFFFFFFF for field in fields):
        return None
    service_id = fields[3]
    transport_stream_id = fields[4]
    original_network_id = fields[5]
    dvb_namespace = fields[6]
    if service_id <= 0 or service_id > 0xFFFF:
        return None
    if transport_stream_id > 0xFFFF or original_network_id > 0xFFFF:
        return None
    if fields[7:] != (0, 0, 0):
        return None
    canonical = ":".join(part.upper() for part in parts) + ":"
    return {
        "service_ref": canonical,
        "service_type": fields[2],
        "service_id": service_id,
        "transport_stream_id": transport_stream_id,
        "original_network_id": original_network_id,
        "dvb_namespace": dvb_namespace,
        "identity": (
            service_id,
            dvb_namespace,
            transport_stream_id,
            original_network_id,
        ),
    }


def is_dvb_service_reference(value):
    """Return whether ``value`` is a URL-free Enigma2 DVB service ref."""
    return parse_dvb_service_reference(value) is not None


def load_dvb_services(
    enigma2_directory="/etc/enigma2",
    include_unbouqueted=False,
    bouquet_files=None,
    cancel_event=None,
    deadline=None,
):
    """Load real television services from bouquets and ``lamedb``.

    Bouquet references are kept exactly (apart from harmless hexadecimal case
    normalisation) and must resolve to a service identity in ``lamedb``.  If
    ``include_unbouqueted`` is true, remaining lamedb services are appended
    using canonical DVB references generated solely from lamedb fields.
    """
    base = os.path.realpath(str(enigma2_directory or ""))
    if not base or not os.path.isdir(base):
        return []
    lamedb_entries = _load_lamedb_entries(base, cancel_event, deadline)
    if not lamedb_entries:
        return []

    selected_files = _validated_bouquet_filter(bouquet_files)
    bouquet_rows = _load_bouquet_rows(base, cancel_event, deadline)
    services = []
    by_identity = OrderedDict()
    for service_ref, description, bouquet_name, bouquet_file in bouquet_rows:
        if selected_files is not None and bouquet_file not in selected_files:
            continue
        parsed = parse_dvb_service_reference(service_ref)
        if parsed is None:
            continue
        entry = lamedb_entries.get(parsed["identity"])
        if entry is None:
            continue
        current = by_identity.get(parsed["identity"])
        name = description or entry["name"]
        if current is None:
            current = {
                "service_ref": parsed["service_ref"],
                "name": name,
                "provider": entry["provider"],
                "bouquets": [],
                "bouquet_files": [],
                "identity": parsed["identity"],
            }
            by_identity[parsed["identity"]] = current
        elif not current["name"] and name:
            current["name"] = name
        bouquet_label = bouquet_name or bouquet_file
        if bouquet_label and bouquet_label not in current["bouquets"]:
            current["bouquets"].append(bouquet_label)
        if bouquet_file and bouquet_file not in current["bouquet_files"]:
            current["bouquet_files"].append(bouquet_file)

    if include_unbouqueted and selected_files is None:
        for identity, entry in lamedb_entries.items():
            if identity in by_identity:
                continue
            by_identity[identity] = {
                "service_ref": entry["service_ref"],
                "name": entry["name"],
                "provider": entry["provider"],
                "bouquets": [],
                "bouquet_files": [],
                "identity": identity,
            }

    for row in by_identity.values():
        if len(services) >= MAX_DVB_SERVICES:
            raise DvbEpgError("too many DVB services")
        services.append(
            DvbService(
                row["service_ref"],
                row["name"],
                provider=row["provider"],
                bouquets=row["bouquets"],
                bouquet_files=row["bouquet_files"],
                identity=row["identity"],
            )
        )
    return services


def list_dvb_bouquets(
    enigma2_directory="/etc/enigma2",
    cancel_event=None,
    deadline=None,
):
    """List TV bouquets in configured order, excluding non-DVB/empty files."""
    base = os.path.realpath(str(enigma2_directory or ""))
    if not base or not os.path.isdir(base):
        return tuple()
    lamedb_entries = _load_lamedb_entries(base, cancel_event, deadline)
    if not lamedb_entries:
        return tuple()
    rows, bouquet_order = _load_bouquet_data(base, cancel_event, deadline)
    counts = defaultdict(set)
    for service_ref, _description, _bouquet_name, bouquet_file in rows:
        parsed = parse_dvb_service_reference(service_ref)
        if parsed is not None and parsed["identity"] in lamedb_entries:
            counts[bouquet_file].add(parsed["identity"])
    output = []
    for filename, name in bouquet_order:
        count = len(counts.get(filename, ()))
        if count:
            output.append(DvbBouquet(filename, name, count))
    return tuple(output)


def _validated_bouquet_filter(bouquet_files):
    if bouquet_files is None:
        return None
    if isinstance(bouquet_files, str):
        bouquet_files = (bouquet_files,)
    try:
        values = list(bouquet_files)
    except TypeError as error:
        raise ValueError("invalid DVB bouquet selection") from error
    if len(values) > MAX_BOUQUET_FILES:
        raise ValueError("too many DVB bouquets selected")
    output = set()
    for value in values:
        filename = str(value or "").strip()
        if _SAFE_BOUQUET_FILENAME_RE.match(filename) is None:
            raise ValueError("invalid DVB bouquet filename")
        output.add(filename)
    return output


def _load_lamedb_entries(base, cancel_event, deadline):
    entries = OrderedDict()
    candidates = (
        os.path.join(base, "lamedb"),
        os.path.join(base, "lamedb5"),
    )
    for path in candidates:
        if not os.path.isfile(path):
            continue
        _check_cancelled(cancel_event, deadline)
        try:
            parsed = _parse_lamedb(path, cancel_event, deadline)
        except (OSError, ValueError, DvbEpgError):
            continue
        for identity, row in parsed.items():
            entries[identity] = row
            if len(entries) > MAX_DVB_SERVICES:
                raise DvbEpgError("too many lamedb services")
    return entries


def _parse_lamedb(path, cancel_event, deadline):
    lines = _read_bounded_lines(path, MAX_LAMEDB_BYTES, MAX_LINE_BYTES)
    try:
        first = next(lines)
    except StopIteration:
        return OrderedDict()
    header = first.decode("ascii", "ignore").strip()
    match = re.match(r"^eDVB services /(\d+)/$", header)
    if match is None:
        raise ValueError("invalid lamedb header")
    version = int(match.group(1))
    if version == 5:
        return _parse_lamedb_v5(lines, cancel_event, deadline)
    if version not in (3, 4):
        raise ValueError("unsupported lamedb version")
    return _parse_lamedb_v4(lines, cancel_event, deadline)


def _parse_lamedb_v4(lines, cancel_event, deadline):
    entries = OrderedDict()
    in_services = False
    iterator = iter(lines)
    for index, raw_line in enumerate(iterator):
        if index % 256 == 0:
            _check_cancelled(cancel_event, deadline)
        line = raw_line.decode("utf-8", "replace").rstrip("\r\n")
        if not in_services:
            if line == "services":
                in_services = True
            continue
        if line == "end":
            break
        fields = line.split(":")
        if len(fields) < 6:
            continue
        try:
            service_id = int(fields[0], 16)
            dvb_namespace = int(fields[1], 16)
            transport_stream_id = int(fields[2], 16)
            original_network_id = int(fields[3], 16)
            service_type = int(fields[4], 10)
        except (TypeError, ValueError, OverflowError):
            continue
        try:
            name_line = next(iterator)
            data_line = next(iterator)
        except StopIteration:
            break
        name = _clean_single_line(
            name_line.decode("utf-8", "replace"),
            MAX_CHANNEL_NAME_LENGTH,
        )
        data = data_line.decode("utf-8", "replace").strip()
        provider = _provider_from_lamedb_data(data)
        row = _lamedb_row(
            service_id,
            dvb_namespace,
            transport_stream_id,
            original_network_id,
            service_type,
            name,
            provider,
        )
        if row is not None:
            entries[row[0]] = row[1]
            if len(entries) > MAX_DVB_SERVICES:
                raise DvbEpgError("too many lamedb services")
    return entries


def _parse_lamedb_v5(lines, cancel_event, deadline):
    entries = OrderedDict()
    for index, raw_line in enumerate(lines):
        if index % 256 == 0:
            _check_cancelled(cancel_event, deadline)
        line = raw_line.decode("utf-8", "replace").strip()
        if not line.startswith("s:"):
            continue
        separator = line.find(',"')
        if separator < 0:
            continue
        fields = line[2:separator].split(":")
        name_start = separator + 2
        name_end = line.find('"', name_start)
        if name_end < 0 or len(fields) < 6:
            continue
        try:
            service_id = int(fields[0], 16)
            dvb_namespace = int(fields[1], 16)
            transport_stream_id = int(fields[2], 16)
            original_network_id = int(fields[3], 16)
            service_type = int(fields[4], 10)
        except (TypeError, ValueError, OverflowError):
            continue
        name = _clean_single_line(line[name_start:name_end], MAX_CHANNEL_NAME_LENGTH)
        provider = _provider_from_lamedb_data(line[name_end + 1 :].lstrip(","))
        row = _lamedb_row(
            service_id,
            dvb_namespace,
            transport_stream_id,
            original_network_id,
            service_type,
            name,
            provider,
        )
        if row is not None:
            entries[row[0]] = row[1]
            if len(entries) > MAX_DVB_SERVICES:
                raise DvbEpgError("too many lamedb services")
    return entries


def _lamedb_row(
    service_id,
    dvb_namespace,
    transport_stream_id,
    original_network_id,
    service_type,
    name,
    provider,
):
    values = (
        service_id,
        dvb_namespace,
        transport_stream_id,
        original_network_id,
        service_type,
    )
    if (
        service_id <= 0
        or service_id > 0xFFFF
        or dvb_namespace < 0
        or dvb_namespace > 0xFFFFFFFF
        or transport_stream_id < 0
        or transport_stream_id > 0xFFFF
        or original_network_id < 0
        or original_network_id > 0xFFFF
        or service_type <= 0
        or service_type > 0xFFFF
        or any(not isinstance(item, int) for item in values)
    ):
        return None
    identity = (
        service_id,
        dvb_namespace,
        transport_stream_id,
        original_network_id,
    )
    service_ref = "1:0:{:X}:{:X}:{:X}:{:X}:{:X}:0:0:0:".format(
        service_type,
        service_id,
        transport_stream_id,
        original_network_id,
        dvb_namespace,
    )
    return identity, {
        "service_ref": service_ref,
        "name": name,
        "provider": provider,
    }


def _provider_from_lamedb_data(value):
    text = str(value or "")
    for fragment in text.split(","):
        if fragment.startswith("p:"):
            return _clean_single_line(fragment[2:], MAX_CHANNEL_NAME_LENGTH)
    return ""


def _load_bouquet_rows(base, cancel_event, deadline):
    rows, _bouquet_order = _load_bouquet_data(base, cancel_event, deadline)
    return rows


def _load_bouquet_data(base, cancel_event, deadline):
    root_filename = "bouquets.tv"
    root_path = os.path.join(base, root_filename)
    queue = deque((root_filename,)) if os.path.isfile(root_path) else deque()
    fallback = []
    try:
        filenames = os.listdir(base)
    except OSError:
        filenames = []
    for filename in sorted(filenames):
        if (
            filename != root_filename
            and _SAFE_BOUQUET_FILENAME_RE.match(filename) is not None
            and filename.startswith("userbouquet.")
        ):
            fallback.append(filename)
    if not queue:
        queue.extend(fallback)
    queued_files = set(queue)
    seen_files = set()
    rows = []
    bouquet_order = []
    while queue:
        _check_cancelled(cancel_event, deadline)
        filename = queue.popleft()
        queued_files.discard(filename)
        if filename in seen_files:
            continue
        if (
            filename != root_filename
            and _SAFE_BOUQUET_FILENAME_RE.match(filename) is None
        ):
            continue
        seen_files.add(filename)
        if len(seen_files) > MAX_BOUQUET_FILES:
            raise DvbEpgError("too many bouquet files")
        path = os.path.join(base, filename)
        if os.path.realpath(path).rsplit(os.sep, 1)[0] != base:
            continue
        try:
            lines = list(_read_bounded_lines(path, MAX_BOUQUET_BYTES, MAX_LINE_BYTES))
        except OSError:
            continue
        bouquet_name = ""
        pending_index = None
        for index, raw_line in enumerate(lines):
            if index % 256 == 0:
                _check_cancelled(cancel_event, deadline)
            line = raw_line.decode("utf-8", "replace").strip()
            if line.startswith("#NAME "):
                bouquet_name = _clean_single_line(
                    line[6:],
                    MAX_CHANNEL_NAME_LENGTH,
                )
                continue
            if line.startswith("#SERVICE "):
                pending_index = None
                body = line[9:].strip()
                nested = _BOUQUET_REFERENCE_RE.search(body)
                if nested is not None:
                    child = nested.group(1)
                    if (
                        _SAFE_BOUQUET_FILENAME_RE.match(child) is not None
                        and child not in seen_files
                        and child not in queued_files
                    ):
                        if len(seen_files) + len(queued_files) >= MAX_BOUQUET_FILES:
                            raise DvbEpgError("too many bouquet files")
                        queue.append(child)
                        queued_files.add(child)
                parsed = parse_dvb_service_reference(body)
                if parsed is not None:
                    rows.append(
                        [parsed["service_ref"], "", bouquet_name, filename]
                    )
                    pending_index = len(rows) - 1
                continue
            if line.startswith("#DESCRIPTION ") and pending_index is not None:
                rows[pending_index][1] = _clean_single_line(
                    line[13:],
                    MAX_CHANNEL_NAME_LENGTH,
                )
                pending_index = None
        if filename != root_filename:
            bouquet_order.append((filename, bouquet_name or filename))
    ordered_names = set(filename for filename, _name in bouquet_order)
    for filename in fallback:
        if filename not in seen_files and filename not in queued_files:
            if len(seen_files) + len(queued_files) >= MAX_BOUQUET_FILES:
                raise DvbEpgError("too many bouquet files")
            queue.append(filename)
            queued_files.add(filename)
    # The fallback queue is processed in a second bounded pass so files not
    # referenced by bouquets.tv remain selectable after image migrations.
    while queue:
        _check_cancelled(cancel_event, deadline)
        filename = queue.popleft()
        queued_files.discard(filename)
        if filename in seen_files:
            continue
        if _SAFE_BOUQUET_FILENAME_RE.match(filename) is None:
            continue
        seen_files.add(filename)
        if len(seen_files) > MAX_BOUQUET_FILES:
            raise DvbEpgError("too many bouquet files")
        path = os.path.join(base, filename)
        try:
            lines = list(_read_bounded_lines(path, MAX_BOUQUET_BYTES, MAX_LINE_BYTES))
        except OSError:
            continue
        bouquet_name = ""
        pending_index = None
        for index, raw_line in enumerate(lines):
            if index % 256 == 0:
                _check_cancelled(cancel_event, deadline)
            line = raw_line.decode("utf-8", "replace").strip()
            if line.startswith("#NAME "):
                bouquet_name = _clean_single_line(line[6:], MAX_CHANNEL_NAME_LENGTH)
            elif line.startswith("#SERVICE "):
                pending_index = None
                parsed = parse_dvb_service_reference(line[9:].strip())
                if parsed is not None:
                    rows.append(
                        [parsed["service_ref"], "", bouquet_name, filename]
                    )
                    pending_index = len(rows) - 1
            elif line.startswith("#DESCRIPTION ") and pending_index is not None:
                rows[pending_index][1] = _clean_single_line(
                    line[13:],
                    MAX_CHANNEL_NAME_LENGTH,
                )
                pending_index = None
        if filename not in ordered_names:
            bouquet_order.append((filename, bouquet_name or filename))
            ordered_names.add(filename)
    return [tuple(row) for row in rows], bouquet_order


def xtream_source_key(account):
    """Return a stable, credential-safe identity for an Xtream XMLTV source.

    The password is intentionally excluded, so a routine password rotation
    does not discard a user's mappings.  Server, base path and username are
    included, preventing mappings from another provider/account being applied
    automatically.
    """
    details = _xtream_account_details(account)
    identity = "{}://{}{}\n{}".format(
        details["scheme"],
        details["netloc"].lower(),
        details["base_path"],
        details["username"],
    ).encode("utf-8")
    return "xtream-{}".format(hashlib.sha256(identity).hexdigest()[:32])


def build_xtream_xmltv_url(account):
    """Build the account's ``xmltv.php`` URL without exposing it in errors."""
    details = _xtream_account_details(account)
    path = (details["base_path"].rstrip("/") + "/xmltv.php").replace("//", "/")
    query = urlencode(
        {
            "username": details["username"],
            "password": details["password"],
        }
    )
    return urlunsplit(
        (
            details["scheme"],
            details["netloc"],
            path,
            query,
            "",
        )
    )


def default_xmltv_cache_path(account, directory=XMLTV_CACHE_DIRECTORY):
    """Return the source-scoped local XMLTV cache path."""
    return os.path.join(str(directory), xtream_source_key(account) + ".xml")


def _xtream_account_details(account):
    if account is None:
        raise ValueError("Xtream account is required")
    source_type = str(getattr(account, "source_type", "xtream") or "xtream").lower()
    if source_type != "xtream":
        raise ValueError("DVB EPG requires an Xtream account")
    scheme = str(getattr(account, "scheme", "") or "").lower().strip()
    netloc = str(getattr(account, "netloc", "") or "").strip()
    base_path = str(getattr(account, "base_path", "") or "").strip()
    username = str(getattr(account, "username", "") or "").strip()
    password = str(getattr(account, "password", "") or "")
    if scheme not in ("http", "https") or not netloc:
        raise ValueError("Xtream account endpoint is invalid")
    if any(
        _CONTROL_RE.search(value) is not None
        for value in (netloc, base_path, username, password)
    ):
        raise ValueError("Xtream account contains invalid characters")
    if len(netloc) > 512 or len(base_path) > 1024:
        raise ValueError("Xtream account endpoint is invalid")
    if not username or not password or len(username) > 512 or len(password) > 1024:
        raise ValueError("Xtream account credentials are invalid")
    try:
        parsed = urlsplit("{}://{}".format(scheme, netloc))
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise ValueError("Xtream account endpoint is invalid") from error
    if (
        not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in ("", "/")
        or parsed.query
        or parsed.fragment
        or (port is not None and not 1 <= port <= 65535)
    ):
        raise ValueError("Xtream account endpoint is invalid")
    if not base_path:
        base_path = ""
    elif not base_path.startswith("/"):
        base_path = "/" + base_path
    if "?" in base_path or "#" in base_path or "\\" in base_path:
        raise ValueError("Xtream account base path is invalid")
    return {
        "scheme": scheme,
        "netloc": netloc,
        "base_path": base_path.rstrip("/"),
        "username": username,
        "password": password,
    }


def read_manual_mappings(path=MANUAL_MAPPING_PATH):
    """Read the newest valid generation from primary and ``.bak`` files."""
    candidates = []
    errors = []
    for candidate, is_backup in ((path, False), (path + ".bak", True)):
        if not os.path.lexists(candidate):
            continue
        try:
            payload = _read_mapping_payload(candidate)
        except (OSError, ValueError, MappingStoreError) as error:
            errors.append(error)
            continue
        candidates.append((payload["generation"], is_backup, candidate, payload))
    if not candidates:
        if errors:
            raise MappingStoreError("manual DVB EPG mappings are invalid") from errors[0]
        return MappingLoadResult()
    candidates.sort(key=lambda item: (item[0], not item[1]), reverse=True)
    generation, is_backup, source_path, payload = candidates[0]
    return MappingLoadResult(
        payload["entries"],
        generation=generation,
        recovered=is_backup,
        source_path=source_path,
    )


def load_manual_mappings(path=MANUAL_MAPPING_PATH):
    """Return all validated manual decisions from redundant storage."""
    return read_manual_mappings(path).entries


def save_manual_mappings(entries, path=MANUAL_MAPPING_PATH):
    """Validate and atomically save mappings to primary and backup files.

    Both files carry a monotonically increasing generation.  The backup is
    written first; readers choose the newest valid generation, so an
    interruption between the two atomic replacements cannot lose the update.
    """
    validated = _validated_manual_entries(entries)
    current_generation = 0
    for candidate in (path, path + ".bak"):
        if not os.path.lexists(candidate):
            continue
        try:
            current = _read_mapping_payload(candidate)
        except (OSError, ValueError, MappingStoreError):
            continue
        current_generation = max(current_generation, current["generation"])
    generation = current_generation + 1
    payload = {
        "version": MAPPING_VERSION,
        "generation": generation,
        "saved_at": int(time.time()),
        "mappings": [entry.to_dict() for entry in validated],
    }
    body = (
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    if len(body) > MAX_MAPPING_BYTES:
        raise MappingStoreError("manual DVB EPG mapping file is too large")
    _atomic_write(path + ".bak", body, 0o600)
    _atomic_write(path, body, 0o600)
    return tuple(validated)


def set_manual_mapping(
    source_key,
    service_ref,
    xmltv_id,
    path=MANUAL_MAPPING_PATH,
    source_name="",
    service_name="",
    xmltv_name="",
    updated_at=None,
):
    """Persist one active mapping, rejecting duplicate source XMLTV IDs."""
    source_key = _validated_source_key(source_key)
    new_entry = ManualMapping(
        source_key,
        service_ref,
        xmltv_id=xmltv_id,
        blocked=False,
        source_name=source_name,
        service_name=service_name,
        xmltv_name=xmltv_name,
        updated_at=updated_at,
    )
    entries = list(load_manual_mappings(path))
    output = []
    for entry in entries:
        if entry.source_key == source_key and entry.service_ref == new_entry.service_ref:
            continue
        if (
            entry.source_key == source_key
            and not entry.blocked
            and entry.xmltv_id == new_entry.xmltv_id
        ):
            raise MappingStoreError(
                "the XMLTV channel is already assigned to another DVB service"
            )
        output.append(entry)
    output.append(new_entry)
    save_manual_mappings(output, path)
    return new_entry


def block_automatic_mapping(
    source_key,
    service_ref,
    path=MANUAL_MAPPING_PATH,
    source_name="",
    service_name="",
    updated_at=None,
):
    """Persist the user's decision not to auto-map one DVB service."""
    source_key = _validated_source_key(source_key)
    new_entry = ManualMapping(
        source_key,
        service_ref,
        xmltv_id="",
        blocked=True,
        source_name=source_name,
        service_name=service_name,
        updated_at=updated_at,
    )
    entries = list(load_manual_mappings(path))
    output = [
        entry
        for entry in entries
        if not (
            entry.source_key == source_key
            and entry.service_ref == new_entry.service_ref
        )
    ]
    output.append(new_entry)
    save_manual_mappings(output, path)
    return new_entry


def remove_manual_mapping(source_key, service_ref, path=MANUAL_MAPPING_PATH):
    """Remove one active or blocked manual decision."""
    source_key = _validated_source_key(source_key)
    parsed = parse_dvb_service_reference(service_ref)
    if parsed is None:
        raise ValueError("invalid DVB service reference")
    entries = list(load_manual_mappings(path))
    output = [
        entry
        for entry in entries
        if not (
            entry.source_key == source_key
            and entry.service_ref == parsed["service_ref"]
        )
    ]
    if len(output) == len(entries):
        return False
    save_manual_mappings(output, path)
    return True


def active_channel_mappings(entries, source_key):
    """Return ``xmltv_id -> service_ref`` for one validated source."""
    source_key = _validated_source_key(source_key)
    output = OrderedDict()
    for entry in _validated_manual_entries(entries):
        if entry.source_key != source_key or entry.blocked:
            continue
        if entry.xmltv_id in output:
            raise MappingStoreError("duplicate XMLTV mapping")
        output[entry.xmltv_id] = entry.service_ref
    return output


def _read_mapping_payload(path):
    body = _read_regular_file(path, MAX_MAPPING_BYTES)
    try:
        payload = json.loads(body.decode("utf-8-sig"))
    except (TypeError, ValueError, UnicodeError) as error:
        raise MappingStoreError("manual mapping JSON is invalid") from error
    if not isinstance(payload, dict) or payload.get("version") != MAPPING_VERSION:
        raise MappingStoreError("manual mapping version is not supported")
    try:
        generation = int(payload.get("generation", 0))
    except (TypeError, ValueError, OverflowError) as error:
        raise MappingStoreError("manual mapping generation is invalid") from error
    if generation <= 0 or generation > 0x7FFFFFFFFFFFFFFF:
        raise MappingStoreError("manual mapping generation is invalid")
    rows = payload.get("mappings")
    if not isinstance(rows, list) or len(rows) > MAX_MANUAL_MAPPINGS:
        raise MappingStoreError("manual mapping list is invalid")
    entries = []
    for row in rows:
        if not isinstance(row, dict):
            raise MappingStoreError("manual mapping entry is invalid")
        required = (
            "source_key",
            "service_ref",
            "xmltv_id",
            "blocked",
            "updated_at",
        )
        if any(field not in row for field in required):
            raise MappingStoreError("manual mapping entry is incomplete")
        if not isinstance(row.get("blocked"), bool):
            raise MappingStoreError("manual mapping blocked state is invalid")
        try:
            entry = ManualMapping(
                row.get("source_key"),
                row.get("service_ref"),
                xmltv_id=row.get("xmltv_id"),
                blocked=row.get("blocked"),
                source_name=row.get("source_name", ""),
                service_name=row.get("service_name", ""),
                xmltv_name=row.get("xmltv_name", ""),
                updated_at=row.get("updated_at"),
            )
        except (TypeError, ValueError) as error:
            raise MappingStoreError("manual mapping entry is invalid") from error
        entries.append(entry)
    validated = _validated_manual_entries(entries)
    return {
        "generation": generation,
        "entries": tuple(validated),
    }


def _validated_manual_entries(entries):
    try:
        values = list(entries or ())
    except TypeError as error:
        raise MappingStoreError("manual mappings are invalid") from error
    if len(values) > MAX_MANUAL_MAPPINGS:
        raise MappingStoreError("too many manual DVB EPG mappings")
    output = []
    service_keys = set()
    xmltv_keys = set()
    for value in values:
        if isinstance(value, ManualMapping):
            entry = ManualMapping(**value.to_dict())
        elif isinstance(value, dict):
            try:
                entry = ManualMapping(**value)
            except (TypeError, ValueError) as error:
                raise MappingStoreError("manual mapping entry is invalid") from error
        else:
            raise MappingStoreError("manual mapping entry is invalid")
        service_key = (entry.source_key, entry.service_ref)
        if service_key in service_keys:
            raise MappingStoreError("duplicate DVB service mapping")
        service_keys.add(service_key)
        if not entry.blocked:
            xmltv_key = (entry.source_key, entry.xmltv_id)
            if xmltv_key in xmltv_keys:
                raise MappingStoreError(
                    "one XMLTV channel cannot be assigned to multiple DVB services"
                )
            xmltv_keys.add(xmltv_key)
        output.append(entry)
    output.sort(key=lambda item: (item.source_key, item.service_name, item.service_ref))
    return output


def normalise_channel_name(value, drop_quality=False):
    """Return a conservative accent-insensitive channel-name key."""
    text = _clean_single_line(value, MAX_CHANNEL_NAME_LENGTH)
    text = text.replace("ı", "i").replace("İ", "I")
    decomposed = unicodedata.normalize("NFKD", text)
    folded = "".join(
        character
        for character in decomposed
        if not unicodedata.combining(character)
    ).casefold()
    tokens = re.findall(r"[a-z0-9]+", folded)
    if drop_quality:
        while len(tokens) > 1 and tokens[-1] in _QUALITY_SUFFIXES:
            tokens.pop()
    return " ".join(tokens)


def match_xmltv_channels(
    channels,
    dvb_services,
    manual_entries=(),
    source_key="",
):
    """Merge source-scoped manual decisions with cautious name matches.

    Exact normalised names are attempted first.  A second pass may ignore one
    or more terminal quality markers such as ``HD``/``UHD``.  A proposal is
    accepted only when both the XMLTV channel and DVB service are unique in
    that pass.  There is no fuzzy, substring or edit-distance matching.
    """
    source_key = _validated_source_key(source_key)
    channel_rows = _validated_xmltv_channels(channels)
    service_rows = _validated_dvb_services(dvb_services)
    channel_by_id = OrderedDict((item.xmltv_id, item) for item in channel_rows)
    service_by_ref = OrderedDict((item.service_ref, item) for item in service_rows)

    mappings = OrderedDict()
    manual_ids = []
    automatic_ids = []
    blocked_services = []
    stale_manual_services = []
    used_services = set()
    reserved_xmltv_ids = set()

    for entry in _validated_manual_entries(manual_entries):
        if entry.source_key != source_key:
            continue
        service = service_by_ref.get(entry.service_ref)
        if service is None:
            stale_manual_services.append(entry.service_ref)
            continue
        used_services.add(entry.service_ref)
        if entry.blocked:
            blocked_services.append(entry.service_ref)
            continue
        reserved_xmltv_ids.add(entry.xmltv_id)
        if entry.xmltv_id not in channel_by_id:
            stale_manual_services.append(entry.service_ref)
            continue
        mappings[entry.xmltv_id] = entry.service_ref
        manual_ids.append(entry.xmltv_id)

    remaining_channels = [
        item
        for item in channel_rows
        if item.xmltv_id not in mappings and item.xmltv_id not in reserved_xmltv_ids
    ]
    remaining_services = [
        item for item in service_rows if item.service_ref not in used_services
    ]
    ambiguous = OrderedDict()

    for drop_quality in (False, True):
        proposals = _channel_match_proposals(
            remaining_channels,
            remaining_services,
            drop_quality,
        )
        reverse = defaultdict(list)
        for xmltv_id, candidates in proposals.items():
            if len(candidates) == 1:
                reverse[next(iter(candidates))].append(xmltv_id)
        accepted_ids = set()
        accepted_refs = set()
        for channel in remaining_channels:
            candidates = proposals.get(channel.xmltv_id, set())
            if len(candidates) != 1:
                if candidates:
                    ambiguous[channel.xmltv_id] = tuple(sorted(candidates))
                continue
            service_ref = next(iter(candidates))
            if len(reverse[service_ref]) != 1:
                ambiguous[channel.xmltv_id] = tuple(sorted(candidates))
                continue
            mappings[channel.xmltv_id] = service_ref
            automatic_ids.append(channel.xmltv_id)
            accepted_ids.add(channel.xmltv_id)
            accepted_refs.add(service_ref)
            ambiguous.pop(channel.xmltv_id, None)
        if accepted_ids:
            remaining_channels = [
                item for item in remaining_channels if item.xmltv_id not in accepted_ids
            ]
            remaining_services = [
                item for item in remaining_services if item.service_ref not in accepted_refs
            ]

    unmatched_ids = [item.xmltv_id for item in remaining_channels]
    return ChannelMatchResult(
        mappings,
        manual_ids=manual_ids,
        automatic_ids=automatic_ids,
        blocked_services=blocked_services,
        ambiguous_ids=ambiguous,
        unmatched_ids=unmatched_ids,
        stale_manual_services=stale_manual_services,
    )


def _channel_match_proposals(channels, services, drop_quality):
    service_index = defaultdict(set)
    for service in services:
        key = normalise_channel_name(service.name, drop_quality=drop_quality)
        if key:
            service_index[key].add(service.service_ref)
    proposals = OrderedDict()
    for channel in channels:
        candidates = set()
        names = channel.display_names or (channel.xmltv_id,)
        for name in names:
            key = normalise_channel_name(name, drop_quality=drop_quality)
            if key:
                candidates.update(service_index.get(key, ()))
        proposals[channel.xmltv_id] = candidates
    return proposals


def _validated_xmltv_channels(channels):
    output = []
    seen = set()
    for value in channels or ():
        if isinstance(value, XmltvChannel):
            item = value
        elif isinstance(value, dict):
            item = XmltvChannel(
                value.get("xmltv_id", value.get("id", "")),
                value.get("display_names", ()),
            )
        else:
            raise ValueError("invalid XMLTV channel")
        if item.xmltv_id in seen:
            raise ValueError("duplicate XMLTV channel ID")
        seen.add(item.xmltv_id)
        output.append(item)
        if len(output) > MAX_XMLTV_CHANNELS:
            raise ValueError("too many XMLTV channels")
    return output


def _validated_dvb_services(services):
    output = []
    seen = set()
    for value in services or ():
        if not isinstance(value, DvbService):
            raise ValueError("invalid DVB service")
        if value.service_ref in seen:
            continue
        seen.add(value.service_ref)
        output.append(value)
        if len(output) > MAX_DVB_SERVICES:
            raise ValueError("too many DVB services")
    return output


def scan_xmltv(
    path,
    default_offset_minutes=0,
    validate_programmes=False,
    cancel_event=None,
    deadline=None,
    maximum_bytes=MAX_XMLTV_BYTES,
):
    """Stream-scan an XMLTV file with fixed byte, node and depth limits."""
    default_offset_minutes = _validated_utc_offset(default_offset_minutes)
    elements = _iter_xmltv_top_level(
        path,
        cancel_event=cancel_event,
        deadline=deadline,
        maximum_bytes=maximum_bytes,
    )
    return _scan_xmltv_elements(
        elements,
        default_offset_minutes,
        validate_programmes,
    )


def _scan_xmltv_elements(
    elements,
    default_offset_minutes,
    validate_programmes,
):
    """Build scan metadata from an already bounded top-level iterator."""
    channel_names = OrderedDict()
    channel_elements = 0
    programme_elements = 0
    invalid_programmes = 0
    for tag, element in elements:
        if tag == "channel":
            channel_elements += 1
            if channel_elements > MAX_XMLTV_CHANNELS:
                raise XmltvError("XMLTV channel limit exceeded")
            try:
                channel = _parse_xmltv_channel(element)
            except ValueError:
                continue
            names = channel_names.setdefault(channel.xmltv_id, [])
            for name in channel.display_names:
                if name not in names:
                    names.append(name)
        elif tag == "programme":
            programme_elements += 1
            if programme_elements > MAX_XMLTV_PROGRAMMES:
                raise XmltvError("XMLTV programme limit exceeded")
            if validate_programmes:
                try:
                    _programme_time_window(element, default_offset_minutes)
                except ValueError:
                    invalid_programmes += 1
    channels = [
        XmltvChannel(xmltv_id, names)
        for xmltv_id, names in channel_names.items()
    ]
    return XmltvScanResult(
        channels,
        channel_elements=channel_elements,
        programme_elements=programme_elements,
        invalid_programmes=invalid_programmes,
    )


def load_xmltv_channels(
    path,
    cancel_event=None,
    deadline=None,
    maximum_bytes=MAX_XMLTV_BYTES,
):
    """Return the bounded channel catalogue from an XMLTV cache file."""
    return scan_xmltv(
        path,
        cancel_event=cancel_event,
        deadline=deadline,
        maximum_bytes=maximum_bytes,
    ).channels


def build_import_event_groups(
    path,
    channel_mappings,
    default_offset_minutes=0,
    now=None,
    past_seconds=DEFAULT_IMPORT_PAST_SECONDS,
    future_seconds=DEFAULT_IMPORT_FUTURE_SECONDS,
    allowed_services=None,
    cancel_event=None,
    deadline=None,
    maximum_bytes=MAX_XMLTV_BYTES,
):
    """Build bounded six-field event tuples grouped by DVB service.

    The default time horizon is applied immediately after parsing the two
    timestamp attributes and before title/description text is collected.  It
    keeps low-memory receivers from retaining stale or far-future provider
    data.  All boundaries are UTC epochs.
    """
    mappings = _normalise_channel_mappings(channel_mappings)
    _validate_allowed_mappings(mappings, allowed_services)
    default_offset_minutes = _validated_utc_offset(default_offset_minutes)
    try:
        now = float(time.time() if now is None else now)
        past_seconds = int(past_seconds)
        future_seconds = int(future_seconds)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("invalid EPG import time horizon") from error
    if (
        not math.isfinite(now)
        or past_seconds < 0
        or future_seconds < 0
        or past_seconds > 31 * 24 * 60 * 60
        or future_seconds > 31 * 24 * 60 * 60
    ):
        raise ValueError("invalid EPG import time horizon")
    earliest = int(now) - past_seconds
    latest = int(now) + future_seconds

    by_xmltv_id = OrderedDict((xmltv_id, []) for xmltv_id in mappings)
    programme_count = 0
    invalid_count = 0
    unmapped_count = 0
    imported_count = 0
    imported_text_characters = 0
    for tag, element in _iter_xmltv_top_level(
        path,
        cancel_event=cancel_event,
        deadline=deadline,
        maximum_bytes=maximum_bytes,
    ):
        if tag != "programme":
            continue
        programme_count += 1
        if programme_count > MAX_XMLTV_PROGRAMMES:
            raise XmltvError("XMLTV programme limit exceeded")
        xmltv_id = str(element.attrib.get("channel", "") or "").strip()
        if xmltv_id not in by_xmltv_id:
            unmapped_count += 1
            continue
        try:
            start_epoch, stop_epoch = _programme_time_window(
                element,
                default_offset_minutes,
            )
        except ValueError:
            invalid_count += 1
            continue
        if stop_epoch <= earliest or start_epoch >= latest:
            continue
        try:
            event = _programme_event_tuple(element, start_epoch, stop_epoch)
        except ValueError:
            invalid_count += 1
            continue
        imported_text_characters += len(event[2]) + len(event[3]) + len(event[4])
        if imported_text_characters > MAX_IMPORT_TEXT_CHARACTERS:
            raise XmltvError("EPG import text-memory limit exceeded")
        events = by_xmltv_id[xmltv_id]
        if len(events) >= MAX_EVENTS_PER_XMLTV_CHANNEL:
            raise XmltvError("XMLTV per-channel event limit exceeded")
        events.append(event)
        imported_count += 1
        if imported_count > MAX_IMPORT_EVENTS:
            raise XmltvError("EPG import event limit exceeded")

    groups = []
    for xmltv_id, events in by_xmltv_id.items():
        if not events:
            continue
        events.sort(key=lambda item: (item[0], item[1], item[2]))
        deduplicated = []
        previous_key = None
        for event in events:
            key = (event[0], event[1], event[2])
            if key == previous_key:
                continue
            deduplicated.append(event)
            previous_key = key
        groups.append(((mappings[xmltv_id],), tuple(deduplicated)))
    imported_count = sum(len(events) for _services, events in groups)
    return ImportEventResult(
        groups,
        programme_count=programme_count,
        imported_count=imported_count,
        invalid_count=invalid_count,
        unmapped_count=unmapped_count,
    )


def build_import_events(*args, **kwargs):
    """Compatibility alias for :func:`build_import_event_groups`."""
    return build_import_event_groups(*args, **kwargs)


def _parse_xmltv_channel(element):
    xmltv_id = _validated_xmltv_id(element.attrib.get("id", ""))
    names = []
    for child in list(element):
        if _local_name(child.tag) != "display-name":
            continue
        name = _element_text(child, MAX_CHANNEL_NAME_LENGTH)
        if name and name not in names:
            names.append(name)
        if len(names) >= 16:
            break
    return XmltvChannel(xmltv_id, names)


def _programme_time_window(element, default_offset_minutes):
    start_epoch = parse_xmltv_timestamp(
        element.attrib.get("start", ""),
        default_offset_minutes,
    )
    stop_epoch = parse_xmltv_timestamp(
        element.attrib.get("stop", ""),
        default_offset_minutes,
    )
    duration = stop_epoch - start_epoch
    if duration <= 0 or duration > 14 * 24 * 60 * 60:
        raise ValueError("invalid XMLTV programme duration")
    return start_epoch, stop_epoch


def _programme_event_tuple(element, start_epoch, stop_epoch):
    title = ""
    subtitle = ""
    description = ""
    categories = []
    for child in list(element):
        tag = _local_name(child.tag)
        if tag == "title" and not title:
            title = _element_text(child, MAX_TITLE_LENGTH)
        elif tag == "sub-title" and not subtitle:
            subtitle = _element_text(child, MAX_SUBTITLE_LENGTH)
        elif tag == "desc" and not description:
            description = _element_text(child, MAX_DESCRIPTION_LENGTH)
        elif tag == "category" and len(categories) < MAX_CATEGORIES_PER_PROGRAMME:
            category = _element_text(child, MAX_CATEGORY_LENGTH)
            if category:
                categories.append(category)
    if not title:
        title = subtitle
        subtitle = ""
    if not title:
        raise ValueError("XMLTV programme title is empty")
    duration = int(stop_epoch - start_epoch)
    return (
        int(start_epoch),
        duration,
        title,
        subtitle,
        description,
        xmltv_category_code(categories),
    )


def xmltv_category_code(categories):
    """Map broad XMLTV categories to the DVB content-nibble major code."""
    if isinstance(categories, str):
        categories = (categories,)
    keys = [normalise_channel_name(value) for value in categories or ()]
    vocabulary = (
        (0x10, ("movie", "film", "cinema", "drama", "dizi")),
        (0x20, ("news", "current affairs", "haber", "gundem")),
        (0x30, ("show", "game show", "entertainment", "eglence")),
        (0x40, ("sport", "sports", "spor")),
        (0x50, ("children", "youth", "kids", "cocuk")),
        (0x60, ("music", "ballet", "dance", "muzik")),
        (0x70, ("arts", "culture", "sanat", "kultur")),
        (0x80, ("social", "politics", "economics", "siyaset", "ekonomi")),
        (0x90, ("education", "science", "factual", "documentary", "belgesel")),
        (0xA0, ("leisure", "hobbies", "travel", "cooking", "gezi", "yemek")),
    )
    for key in keys:
        padded = " {} ".format(key)
        for code, terms in vocabulary:
            if any(" {} ".format(term) in padded for term in terms):
                return code
    return 0


def _normalise_channel_mappings(channel_mappings):
    if isinstance(channel_mappings, ChannelMatchResult):
        channel_mappings = channel_mappings.mappings
    if not hasattr(channel_mappings, "items"):
        raise ValueError("channel mappings are invalid")
    output = OrderedDict()
    used_refs = set()
    for xmltv_id, service_ref in channel_mappings.items():
        xmltv_id = _validated_xmltv_id(xmltv_id)
        parsed = parse_dvb_service_reference(service_ref)
        if parsed is None:
            raise ValueError("channel mapping contains a non-DVB service reference")
        if xmltv_id in output:
            raise ValueError("duplicate XMLTV channel mapping")
        if parsed["service_ref"] in used_refs:
            raise ValueError("one DVB service cannot use multiple XMLTV channels")
        output[xmltv_id] = parsed["service_ref"]
        used_refs.add(parsed["service_ref"])
        if len(output) > MAX_MANUAL_MAPPINGS:
            raise ValueError("too many channel mappings")
    return output


def _validate_allowed_mappings(mappings, allowed_services):
    if allowed_services is None:
        return
    allowed = set()
    for value in allowed_services:
        service_ref = value.service_ref if isinstance(value, DvbService) else value
        parsed = parse_dvb_service_reference(service_ref)
        if parsed is not None:
            allowed.add((parsed["identity"], parsed["service_type"]))
    for service_ref in mappings.values():
        parsed = parse_dvb_service_reference(service_ref)
        if (
            parsed is None
            or (parsed["identity"], parsed["service_type"]) not in allowed
        ):
            raise ValueError(
                "channel mapping is not present in the selected DVB bouquets"
            )


def download_xtream_xmltv(
    account,
    destination=None,
    timeout=DEFAULT_DOWNLOAD_TIMEOUT,
    cancel_event=None,
    deadline=None,
    opener=None,
    maximum_download_bytes=MAX_XMLTV_DOWNLOAD_BYTES,
    maximum_xmltv_bytes=MAX_XMLTV_BYTES,
):
    """Synchronously download, validate and atomically cache Xtream XMLTV.

    Run this function outside the Enigma2 GUI thread.  Redirects are limited
    to the same origin so username/password query data cannot be forwarded to
    an unrelated host.  Exceptions never include the credential-bearing URL.
    """
    try:
        timeout = float(timeout)
        maximum_download_bytes = int(maximum_download_bytes)
        maximum_xmltv_bytes = int(maximum_xmltv_bytes)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("invalid XMLTV download limits") from error
    if (
        not math.isfinite(timeout)
        or timeout <= 0
        or timeout > MAX_DOWNLOAD_TIMEOUT
        or maximum_download_bytes <= 0
        or maximum_download_bytes > MAX_XMLTV_DOWNLOAD_BYTES
        or maximum_xmltv_bytes <= 0
        or maximum_xmltv_bytes > MAX_XMLTV_BYTES
    ):
        raise ValueError("invalid XMLTV download limits")
    deadline = _absolute_download_deadline(timeout, deadline)
    _check_cancelled(cancel_event, deadline)
    destination = destination or default_xmltv_cache_path(account)
    destination = str(destination)
    if not os.path.isabs(destination):
        raise ValueError("XMLTV cache path must be absolute")
    cache_directory = os.path.dirname(destination) or "."
    cache_name = _validated_cache_entry_name(os.path.basename(destination))
    url = build_xtream_xmltv_url(account)
    directory_descriptor = None
    raw_name = ""
    xml_name = ""
    response = None
    redirect_count = 0
    try:
        try:
            directory_descriptor = _open_private_cache_directory(cache_directory)
        except OSError as error:
            raise XmltvDownloadError("XMLTV cache directory is unsafe") from error
        _require_free_space_descriptor(directory_descriptor)
        _validate_cache_target(directory_descriptor, cache_name)
        raw_name = _temporary_cache_name(cache_name, "download")
        xml_name = _temporary_cache_name(cache_name, "xml")
        response, redirect_count = _open_xmltv_response(
            url,
            timeout,
            cancel_event,
            deadline,
            opener,
        )
        headers = getattr(response, "headers", {})
        length_text = headers.get("Content-Length", "") if headers else ""
        if length_text:
            try:
                announced = int(str(length_text).strip())
            except (TypeError, ValueError, OverflowError) as error:
                raise XmltvDownloadError("XMLTV response size is invalid") from error
            if announced < 0 or announced > maximum_download_bytes:
                raise XmltvDownloadError("XMLTV download is too large")
        encoding = str(headers.get("Content-Encoding", "") if headers else "").lower()
        if encoding not in ("", "identity", "gzip", "x-gzip"):
            raise XmltvDownloadError("XMLTV response encoding is not supported")
        downloaded = _copy_response_to_file(
            response,
            raw_name,
            maximum_download_bytes,
            cancel_event,
            deadline,
            directory_descriptor=directory_descriptor,
            timeout=timeout,
        )
        try:
            response.close()
        except Exception:
            pass
        response = None
        magic = _read_cache_prefix(directory_descriptor, raw_name, 2)
        compressed = encoding in ("gzip", "x-gzip") or magic == b"\x1f\x8b"
        if compressed:
            _decompress_gzip_file(
                raw_name,
                xml_name,
                maximum_xmltv_bytes,
                cancel_event,
                deadline,
                directory_descriptor=directory_descriptor,
            )
            staged_name = xml_name
        else:
            if downloaded > maximum_xmltv_bytes:
                raise XmltvDownloadError("XMLTV guide is too large")
            staged_name = raw_name
        staged_file = _open_regular_cache_file(
            directory_descriptor,
            staged_name,
            maximum_xmltv_bytes,
        )
        try:
            size_bytes = os.fstat(staged_file.fileno()).st_size
            elements = _iter_xmltv_file(
                staged_file,
                cancel_event=cancel_event,
                deadline=deadline,
                maximum_bytes=maximum_xmltv_bytes,
            )
            scan = _scan_xmltv_elements(elements, 0, False)
        finally:
            staged_file.close()
        if not scan.channels:
            raise XmltvDownloadError("XMLTV guide contains no channels")
        _install_cache_entry(
            directory_descriptor,
            staged_name,
            cache_name,
            0o600,
            maximum_xmltv_bytes,
        )
        if staged_name == raw_name:
            raw_name = ""
        else:
            xml_name = ""
        _verify_private_cache_directory(
            cache_directory,
            directory_descriptor,
        )
        return XmltvDownloadResult(
            destination,
            size_bytes,
            len(scan.channels),
            scan.programme_elements,
            redirect_count=redirect_count,
        )
    except OperationCancelled:
        raise
    except XmltvDownloadError:
        raise
    except (ElementTree.ParseError, XmltvError, OSError, EOFError) as error:
        raise XmltvDownloadError("XMLTV guide is invalid") from error
    finally:
        if response is not None:
            try:
                response.close()
            except Exception:
                pass
        if directory_descriptor is not None:
            for temporary in (raw_name, xml_name):
                if not temporary:
                    continue
                try:
                    os.unlink(temporary, dir_fd=directory_descriptor)
                except OSError:
                    pass
            try:
                os.close(directory_descriptor)
            except OSError:
                pass


class _NoRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _open_xmltv_response(url, timeout, cancel_event, deadline, opener):
    client = opener or build_opener(_NoRedirectHandler())
    origin = _url_origin(url)
    current = url
    for redirect_count in range(MAX_REDIRECTS + 1):
        _check_cancelled(cancel_event, deadline)
        request = Request(
            current,
            headers={
                "Accept": "application/xml,text/xml,application/gzip,*/*;q=0.1",
                "Accept-Encoding": "gzip,identity",
                "Connection": "close",
                "User-Agent": "GTIPTVPlayerPro DVB-EPG",
            },
        )
        try:
            if callable(client) and not hasattr(client, "open"):
                response = client(request, _remaining_timeout(deadline, timeout))
            else:
                response = client.open(
                    request,
                    timeout=_remaining_timeout(deadline, timeout),
                )
        except HTTPError as error:
            if error.code not in (301, 302, 303, 307, 308):
                try:
                    error.close()
                except Exception:
                    pass
                raise XmltvDownloadError(
                    "XMLTV server returned HTTP status {}".format(error.code)
                ) from None
            location = error.headers.get("Location", "") if error.headers else ""
            try:
                error.close()
            except Exception:
                pass
            if redirect_count >= MAX_REDIRECTS or not location:
                raise XmltvDownloadError("XMLTV redirect limit exceeded")
            candidate = urljoin(current, location)
            if _url_origin(candidate) != origin:
                raise XmltvDownloadError("XMLTV redirect changed server origin")
            current = candidate
            continue
        except (URLError, OSError) as error:
            del error
            raise XmltvDownloadError("XMLTV server connection failed") from None
        status = getattr(response, "status", None)
        if status is None:
            try:
                status = response.getcode()
            except Exception:
                status = 200
        if int(status or 200) != 200:
            try:
                response.close()
            except Exception:
                pass
            raise XmltvDownloadError(
                "XMLTV server returned HTTP status {}".format(status)
            )
        try:
            final_url = response.geturl()
        except Exception:
            final_url = current
        if _url_origin(final_url) != origin:
            try:
                response.close()
            except Exception:
                pass
            raise XmltvDownloadError("XMLTV redirect changed server origin")
        return response, redirect_count
    raise XmltvDownloadError("XMLTV redirect limit exceeded")


def _url_origin(url):
    try:
        parsed = urlsplit(str(url or ""))
        port = parsed.port
    except (TypeError, ValueError) as error:
        raise XmltvDownloadError("XMLTV endpoint is invalid") from error
    scheme = str(parsed.scheme or "").lower()
    if (
        scheme not in ("http", "https")
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise XmltvDownloadError("XMLTV endpoint is invalid")
    return (
        scheme,
        parsed.hostname.lower(),
        port or (443 if scheme == "https" else 80),
    )


def _copy_response_to_file(
    response,
    path,
    maximum,
    cancel_event,
    deadline,
    directory_descriptor=None,
    timeout=DEFAULT_DOWNLOAD_TIMEOUT,
):
    if directory_descriptor is None:
        descriptor = _create_private_file(path)
    else:
        descriptor = _create_private_cache_file(directory_descriptor, path)
    total = 0
    reader = getattr(response, "read1", None)
    if not callable(reader):
        reader = response.read
    try:
        while True:
            _check_cancelled(cancel_event, deadline)
            try:
                _set_response_socket_timeout(
                    response,
                    _remaining_timeout(deadline, timeout),
                )
            except OSError:
                # ``http.client.HTTPResponse`` clears ``fp`` as soon as a
                # Content-Length body is consumed.  In that successful EOF
                # state there is no live socket left to configure for the
                # otherwise harmless final read iteration.
                is_closed = getattr(response, "isclosed", None)
                try:
                    response_finished = bool(
                        callable(is_closed) and is_closed()
                    )
                except Exception:
                    response_finished = False
                if response_finished:
                    remaining = getattr(response, "length", None)
                    try:
                        body_complete = (
                            remaining is None or int(remaining) <= 0
                        )
                    except (TypeError, ValueError, OverflowError):
                        body_complete = False
                    if body_complete:
                        break
                raise
            try:
                chunk = reader(min(READ_CHUNK_BYTES, maximum + 1 - total))
            except OSError:
                _check_cancelled(cancel_event, deadline)
                raise
            _check_cancelled(cancel_event, deadline)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise XmltvDownloadError("XMLTV download is too large")
            _write_all(descriptor, chunk)
            if total % (4 * 1024 * 1024) < len(chunk):
                if directory_descriptor is None:
                    _require_free_space(os.path.dirname(path) or ".")
                else:
                    _require_free_space_descriptor(directory_descriptor)
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    except Exception:
        os.close(descriptor)
        raise
    else:
        os.close(descriptor)
    if total <= 0:
        raise XmltvDownloadError("XMLTV server returned an empty guide")
    return total


def _decompress_gzip_file(
    source_path,
    destination_path,
    maximum,
    cancel_event,
    deadline,
    directory_descriptor=None,
):
    if directory_descriptor is None:
        descriptor = _create_private_file(destination_path)
    else:
        descriptor = _create_private_cache_file(
            directory_descriptor,
            destination_path,
        )
    total = 0
    source_file = None
    try:
        if directory_descriptor is None:
            source = gzip.open(source_path, "rb")
        else:
            source_file = _open_regular_cache_file(
                directory_descriptor,
                source_path,
                MAX_XMLTV_DOWNLOAD_BYTES,
            )
            source = gzip.GzipFile(fileobj=source_file, mode="rb")
        with source:
            while True:
                _check_cancelled(cancel_event, deadline)
                chunk = source.read(min(READ_CHUNK_BYTES, maximum + 1 - total))
                _check_cancelled(cancel_event, deadline)
                if not chunk:
                    break
                total += len(chunk)
                if total > maximum:
                    raise XmltvDownloadError("expanded XMLTV guide is too large")
                _write_all(descriptor, chunk)
                if total % (4 * 1024 * 1024) < len(chunk):
                    if directory_descriptor is None:
                        _require_free_space(
                            os.path.dirname(destination_path) or "."
                        )
                    else:
                        _require_free_space_descriptor(directory_descriptor)
        os.fsync(descriptor)
        os.fchmod(descriptor, 0o600)
    except Exception:
        os.close(descriptor)
        raise
    else:
        os.close(descriptor)
    finally:
        if source_file is not None:
            source_file.close()
    if total <= 0:
        raise XmltvDownloadError("expanded XMLTV guide is empty")


def write_epgimport_configuration(
    account,
    channel_mappings,
    channels_path=EPGIMPORT_CHANNELS_PATH,
    sources_path=EPGIMPORT_SOURCES_PATH,
    source_name="GT IPTV Player Pro DVB EPG",
    allowed_services=None,
    enigma2_directory="/etc/enigma2",
):
    """Atomically export EPGImport channels/sources XML for one Xtream account.

    This optional export does not import or depend on the EPGImport plugin.
    When ``allowed_services`` is omitted the receiver bouquet/lamedb catalogue
    is loaded and every output reference is verified against it.
    """
    mappings = _normalise_channel_mappings(channel_mappings)
    if allowed_services is None:
        allowed_services = load_dvb_services(enigma2_directory)
    _validate_allowed_mappings(mappings, allowed_services)
    source_name = _clean_single_line(source_name, 160)
    if not source_name:
        source_name = "GT IPTV Player Pro DVB EPG"
    source_url = build_xtream_xmltv_url(account)

    channel_lines = ['<?xml version="1.0" encoding="utf-8"?>', "<channels>"]
    for xmltv_id, service_ref in sorted(mappings.items()):
        channel_lines.append(
            "  <channel id={}>{}</channel>".format(
                quoteattr(xmltv_id),
                escape(service_ref),
            )
        )
    channel_lines.append("</channels>")
    channel_body = ("\n".join(channel_lines) + "\n").encode("utf-8")

    source_lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<sources>",
        "  <sourcecat sourcecatname={}>".format(quoteattr(source_name)),
        "    <source type=\"gen_xmltv\" nocheck=\"1\" channels={} >".format(
            quoteattr(str(channels_path))
        ),
        "      <description>{}</description>".format(escape(source_name)),
        "      <url>{}</url>".format(escape(source_url)),
        "    </source>",
        "  </sourcecat>",
        "</sources>",
    ]
    source_body = ("\n".join(source_lines) + "\n").encode("utf-8")
    _atomic_write(str(channels_path), channel_body, 0o644)
    _atomic_write(str(sources_path), source_body, 0o600)
    return {
        "channels_path": str(channels_path),
        "sources_path": str(sources_path),
        "channel_count": len(mappings),
        "source_key": xtream_source_key(account),
    }


def write_epgimport_files(*args, **kwargs):
    """Compatibility alias for :func:`write_epgimport_configuration`."""
    return write_epgimport_configuration(*args, **kwargs)


class _BoundedXmlReader(object):
    """File wrapper enforcing byte and XML-declaration safety limits."""

    def __init__(self, raw_file, maximum, cancel_event=None, deadline=None):
        self.raw_file = raw_file
        self.maximum = int(maximum)
        self.cancel_event = cancel_event
        self.deadline = deadline
        self.total = 0
        self._bytes_since_event = 0
        self._scan_tail = b""
        self._first_chunk = True

    def read(self, size=-1):
        _check_cancelled(self.cancel_event, self.deadline)
        remaining = self.maximum + 1 - self.total
        if remaining <= 0:
            raise XmltvError("XMLTV file is too large")
        if size is None or size < 0:
            size = min(READ_CHUNK_BYTES, remaining)
        else:
            size = min(int(size), remaining)
        data = self.raw_file.read(size)
        _check_cancelled(self.cancel_event, self.deadline)
        if not data:
            return data
        self.total += len(data)
        self._bytes_since_event += len(data)
        if self.total > self.maximum:
            raise XmltvError("XMLTV file is too large")
        if self._bytes_since_event > MAX_XMLTV_BYTES_BETWEEN_EVENTS:
            raise XmltvError("XMLTV parser event interval limit exceeded")
        if self._first_chunk:
            self._first_chunk = False
            unsupported_boms = (
                b"\xff\xfe",
                b"\xfe\xff",
                b"\x00\x00\xfe\xff",
                b"\xff\xfe\x00\x00",
            )
            if data.startswith(unsupported_boms):
                raise XmltvError("XMLTV UTF-16/32 input is not supported")
        scan = (self._scan_tail + data).lower()
        if b"encoding=\"utf-16" in scan or b"encoding='utf-16" in scan:
            raise XmltvError("XMLTV UTF-16 input is not supported")
        if b"encoding=\"utf-32" in scan or b"encoding='utf-32" in scan:
            raise XmltvError("XMLTV UTF-32 input is not supported")
        if b"<!entity" in scan:
            raise XmltvError("XMLTV entity declarations are prohibited")
        if re.search(br"<!doctype[^>]{0,8192}\[", scan, re.DOTALL):
            raise XmltvError("XMLTV internal DTD subsets are prohibited")
        self._scan_tail = scan[-8192:]
        return data

    def mark_parser_event(self):
        """Reset read-ahead accounting after a parser start/end event."""
        self._bytes_since_event = 0


def _iter_xmltv_top_level(
    path,
    cancel_event=None,
    deadline=None,
    maximum_bytes=MAX_XMLTV_BYTES,
):
    raw_file = _open_regular_file(path, maximum_bytes)
    try:
        for item in _iter_xmltv_file(
            raw_file,
            cancel_event=cancel_event,
            deadline=deadline,
            maximum_bytes=maximum_bytes,
        ):
            yield item
    finally:
        raw_file.close()


def _iter_xmltv_file(
    raw_file,
    cancel_event=None,
    deadline=None,
    maximum_bytes=MAX_XMLTV_BYTES,
):
    """Yield bounded XMLTV children from an open binary regular file."""
    reader = _BoundedXmlReader(
        raw_file,
        maximum_bytes,
        cancel_event=cancel_event,
        deadline=deadline,
    )
    depth = 0
    root_seen = False
    count = 0
    total_nodes = 0
    total_text_characters = 0
    top_level_nodes = 0
    top_level_text_characters = 0
    open_elements = []
    try:
        try:
            parser = ElementTree.iterparse(reader, events=("start", "end"))
            for event, element in parser:
                reader.mark_parser_event()
                if event == "start":
                    depth += 1
                    open_elements.append(element)
                    total_nodes += 1
                    if total_nodes > MAX_XMLTV_NODES:
                        _clear_xml_elements(open_elements)
                        raise XmltvError("XMLTV total node limit exceeded")
                    if depth == 2:
                        top_level_nodes = 1
                        top_level_text_characters = 0
                    elif depth > 2:
                        top_level_nodes += 1
                    if top_level_nodes > MAX_XMLTV_TOP_LEVEL_NODES:
                        _clear_xml_elements(open_elements)
                        raise XmltvError("XMLTV top-level subtree node limit exceeded")
                    if depth > MAX_XMLTV_DEPTH:
                        _clear_xml_elements(open_elements)
                        raise XmltvError("XMLTV nesting depth limit exceeded")
                    if not root_seen:
                        root_seen = True
                        if _local_name(element.tag) != "tv":
                            _clear_xml_elements(open_elements)
                            raise XmltvError("XMLTV root element is invalid")
                    if total_nodes % 1024 == 0:
                        _check_cancelled(cancel_event, deadline)
                    continue
                if not open_elements or open_elements[-1] is not element:
                    _clear_xml_elements(open_elements)
                    raise XmltvError("XMLTV document structure is invalid")
                text_characters = len(element.text or "") + len(element.tail or "")
                total_text_characters += text_characters
                if depth >= 2:
                    top_level_text_characters += text_characters
                if total_text_characters > MAX_XMLTV_TEXT_CHARACTERS:
                    _clear_xml_elements(open_elements)
                    raise XmltvError("XMLTV cumulative text limit exceeded")
                if (
                    top_level_text_characters
                    > MAX_XMLTV_TOP_LEVEL_TEXT_CHARACTERS
                ):
                    _clear_xml_elements(open_elements)
                    raise XmltvError("XMLTV top-level subtree text limit exceeded")
                if depth == 2:
                    tag = _local_name(element.tag)
                    try:
                        if tag in ("channel", "programme"):
                            count += 1
                            if count % 128 == 0:
                                _check_cancelled(cancel_event, deadline)
                            yield tag, element
                    finally:
                        # ``Element.clear()`` releases the subtree contents,
                        # but ElementTree's root would otherwise keep one
                        # empty child object for every programme in a large
                        # guide.  Detach the completed top-level node as well
                        # so memory stays bounded across the whole stream.
                        if open_elements and open_elements[0] is not element:
                            try:
                                open_elements[0].remove(element)
                            except (ValueError, AttributeError):
                                pass
                        element.clear()
                    top_level_nodes = 0
                    top_level_text_characters = 0
                open_elements.pop()
                depth -= 1
                if depth < 0:
                    _clear_xml_elements(open_elements)
                    raise XmltvError("XMLTV document structure is invalid")
        except ElementTree.ParseError as error:
            raise XmltvError("XMLTV document is not well formed") from error
        if not root_seen or depth != 0:
            raise XmltvError("XMLTV document is incomplete")
    finally:
        _clear_xml_elements(open_elements)


def _clear_xml_elements(elements):
    """Release parser-held subtrees promptly on cancellation or limit errors."""
    for element in reversed(elements):
        try:
            element.clear()
        except (AttributeError, TypeError, ValueError):
            pass


def _element_text(element, maximum):
    fragments = []
    collected = 0
    collection_limit = max(int(maximum) * 2, int(maximum) + 256)
    try:
        for fragment in element.itertext():
            fragment = str(fragment or "")
            remaining = collection_limit - collected
            if remaining <= 0:
                break
            fragments.append(fragment[:remaining])
            collected += min(len(fragment), remaining)
    except (TypeError, ValueError) as error:
        raise ValueError("invalid XMLTV text") from error
    text = "".join(fragments)
    return _clean_xml_text(text, maximum)


def _local_name(tag):
    text = str(tag or "")
    return text.rsplit("}", 1)[-1] if "}" in text else text


def _validated_xmltv_id(value):
    text = str(value or "").strip()
    if (
        not text
        or len(text) > MAX_XMLTV_ID_LENGTH
        or _CONTROL_RE.search(text) is not None
    ):
        raise ValueError("invalid XMLTV channel ID")
    return text


def _validated_source_key(value):
    text = str(value or "").strip()
    if (
        not text
        or len(text) > 128
        or _CONTROL_RE.search(text) is not None
        or re.match(r"^[A-Za-z0-9._:-]+$", text) is None
    ):
        raise ValueError("invalid DVB EPG source key")
    return text


def _validated_utc_offset(value):
    try:
        minutes = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("invalid default XMLTV UTC offset") from error
    if minutes < -(14 * 60) or minutes > 14 * 60:
        raise ValueError("invalid default XMLTV UTC offset")
    return minutes


def _clean_single_line(value, maximum):
    text = str(value or "").replace("\r", " ").replace("\n", " ")
    text = _CONTROL_RE.sub(" ", text)
    return " ".join(text.split())[: int(maximum)]


def _clean_xml_text(value, maximum):
    text = unicodedata.normalize("NFC", str(value or ""))
    text = _CONTROL_RE.sub(" ", text)
    return " ".join(text.split())[: int(maximum)]


def _read_regular_file(path, maximum):
    with _open_regular_file(path, maximum) as source:
        body = source.read(maximum + 1)
    if len(body) > maximum:
        raise OSError("file is too large")
    return body


def _open_regular_file(path, maximum):
    path = str(path)
    listed = os.lstat(path)
    if stat.S_ISLNK(listed.st_mode) or not stat.S_ISREG(listed.st_mode):
        raise OSError("not a regular file")
    if listed.st_size > maximum:
        raise OSError("file is too large")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_dev != listed.st_dev
            or details.st_ino != listed.st_ino
            or details.st_size > maximum
        ):
            raise OSError("unsafe file")
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def _read_bounded_lines(path, maximum, maximum_line):
    source = _open_regular_file(path, maximum)
    buffer = b""
    total = 0
    try:
        while True:
            chunk = source.read(READ_CHUNK_BYTES)
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise OSError("file is too large")
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                if len(line) > maximum_line:
                    raise OSError("line is too long")
                yield line.rstrip(b"\r")
            if len(buffer) > maximum_line:
                raise OSError("line is too long")
        if buffer:
            if len(buffer) > maximum_line:
                raise OSError("line is too long")
            yield buffer.rstrip(b"\r")
    finally:
        source.close()


def _validated_cache_entry_name(value):
    """Return a single safe filename component for a private cache entry."""
    name = str(value or "")
    if (
        not name
        or name in (".", "..")
        or os.path.basename(name) != name
        or "\x00" in name
        or len(name.encode("utf-8")) > 255
    ):
        raise ValueError("invalid XMLTV cache filename")
    return name


def _open_private_cache_directory(path):
    """Open an owned, non-symlink cache directory with exact mode ``0700``.

    An existing directory that has ever been exposed through group/other
    permission bits is rejected instead of silently repaired.  Owner-only
    modes may be tightened through the already-open directory descriptor.
    """
    path = str(path)
    created = False
    try:
        os.mkdir(path, 0o700)
        created = True
    except FileExistsError:
        pass
    listed = os.lstat(path)
    if stat.S_ISLNK(listed.st_mode) or not stat.S_ISDIR(listed.st_mode):
        raise OSError("unsafe XMLTV cache directory")
    if listed.st_uid != os.geteuid():
        raise OSError("XMLTV cache directory owner is invalid")
    permissions = stat.S_IMODE(listed.st_mode)
    if permissions & 0o077:
        raise OSError("XMLTV cache directory is not private")
    flags = (
        os.O_RDONLY
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_DIRECTORY", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    descriptor = os.open(path, flags)
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISDIR(details.st_mode)
            or details.st_dev != listed.st_dev
            or details.st_ino != listed.st_ino
            or details.st_uid != os.geteuid()
        ):
            raise OSError("unsafe XMLTV cache directory")
        permissions = stat.S_IMODE(details.st_mode)
        if permissions & 0o077:
            raise OSError("XMLTV cache directory is not private")
        if permissions != 0o700:
            os.fchmod(descriptor, 0o700)
        details = os.fstat(descriptor)
        if (
            details.st_uid != os.geteuid()
            or stat.S_IMODE(details.st_mode) != 0o700
        ):
            raise OSError("XMLTV cache directory permissions are invalid")
        if created:
            try:
                os.fsync(descriptor)
            except OSError:
                pass
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _verify_private_cache_directory(path, descriptor):
    """Verify that a held cache dirfd still names the requested private path."""
    listed = os.lstat(path)
    details = os.fstat(descriptor)
    if (
        stat.S_ISLNK(listed.st_mode)
        or not stat.S_ISDIR(listed.st_mode)
        or listed.st_dev != details.st_dev
        or listed.st_ino != details.st_ino
        or details.st_uid != os.geteuid()
        or stat.S_IMODE(details.st_mode) != 0o700
    ):
        raise OSError("XMLTV cache directory changed during download")


def _require_free_space_descriptor(
    directory_descriptor,
    reserve=MIN_XMLTV_FREE_BYTES,
):
    try:
        details = os.fstatvfs(directory_descriptor)
        available = int(details.f_bavail) * int(details.f_frsize or details.f_bsize)
    except (AttributeError, OSError, TypeError, ValueError, OverflowError):
        return
    if available < int(reserve):
        raise XmltvDownloadError("not enough temporary space for XMLTV guide")


def _validate_cache_target(directory_descriptor, name):
    name = _validated_cache_entry_name(name)
    try:
        details = os.stat(
            name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
    except FileNotFoundError:
        return
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise OSError("unsafe XMLTV cache target")


def _temporary_cache_name(target_name, purpose):
    target_name = _validated_cache_entry_name(target_name)
    prefix = target_name[:96]
    return _validated_cache_entry_name(
        ".{}.{}.{}.{}.tmp".format(
            prefix,
            str(purpose),
            os.getpid(),
            uuid.uuid4().hex,
        )
    )


def _create_private_cache_file(directory_descriptor, name):
    name = _validated_cache_entry_name(name)
    flags = (
        os.O_WRONLY
        | os.O_CREAT
        | os.O_EXCL
        | getattr(os, "O_CLOEXEC", 0)
        | getattr(os, "O_NOFOLLOW", 0)
    )
    return os.open(name, flags, 0o600, dir_fd=directory_descriptor)


def _open_regular_cache_descriptor(directory_descriptor, name, maximum):
    name = _validated_cache_entry_name(name)
    listed = os.stat(
        name,
        dir_fd=directory_descriptor,
        follow_symlinks=False,
    )
    if (
        stat.S_ISLNK(listed.st_mode)
        or not stat.S_ISREG(listed.st_mode)
        or listed.st_size > int(maximum)
    ):
        raise OSError("unsafe XMLTV cache file")
    flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(
        os,
        "O_NOFOLLOW",
        0,
    )
    descriptor = os.open(name, flags, dir_fd=directory_descriptor)
    try:
        details = os.fstat(descriptor)
        if (
            not stat.S_ISREG(details.st_mode)
            or details.st_dev != listed.st_dev
            or details.st_ino != listed.st_ino
            or details.st_size > int(maximum)
        ):
            raise OSError("unsafe XMLTV cache file")
        return descriptor
    except Exception:
        os.close(descriptor)
        raise


def _open_regular_cache_file(directory_descriptor, name, maximum):
    descriptor = _open_regular_cache_descriptor(
        directory_descriptor,
        name,
        maximum,
    )
    try:
        return os.fdopen(descriptor, "rb")
    except Exception:
        os.close(descriptor)
        raise


def _read_cache_prefix(directory_descriptor, name, length):
    descriptor = _open_regular_cache_descriptor(
        directory_descriptor,
        name,
        MAX_XMLTV_DOWNLOAD_BYTES,
    )
    try:
        return os.read(descriptor, int(length))
    finally:
        os.close(descriptor)


def _install_cache_entry(
    directory_descriptor,
    staged_name,
    destination_name,
    mode,
    maximum,
):
    """Durably replace a cache entry without resolving directory symlinks."""
    staged_name = _validated_cache_entry_name(staged_name)
    destination_name = _validated_cache_entry_name(destination_name)
    descriptor = _open_regular_cache_descriptor(
        directory_descriptor,
        staged_name,
        maximum,
    )
    installed = False
    try:
        os.fchmod(descriptor, mode)
        os.fsync(descriptor)
        source_details = os.fstat(descriptor)
        _validate_cache_target(directory_descriptor, destination_name)
        os.replace(
            staged_name,
            destination_name,
            src_dir_fd=directory_descriptor,
            dst_dir_fd=directory_descriptor,
        )
        installed = True
        destination_details = os.stat(
            destination_name,
            dir_fd=directory_descriptor,
            follow_symlinks=False,
        )
        if (
            not stat.S_ISREG(destination_details.st_mode)
            or destination_details.st_dev != source_details.st_dev
            or destination_details.st_ino != source_details.st_ino
            or stat.S_IMODE(destination_details.st_mode) != mode
        ):
            raise OSError("XMLTV cache installation changed unexpectedly")
        try:
            os.fsync(directory_descriptor)
        except OSError:
            pass
    except Exception:
        if installed:
            try:
                current = os.stat(
                    destination_name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if (
                    current.st_dev == source_details.st_dev
                    and current.st_ino == source_details.st_ino
                ):
                    os.unlink(destination_name, dir_fd=directory_descriptor)
            except OSError:
                pass
        raise
    finally:
        os.close(descriptor)


def _ensure_directory(path, mode):
    path = str(path or ".")
    created = False
    try:
        os.makedirs(path, mode)
        created = True
    except OSError:
        if not os.path.isdir(path):
            raise
    details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISDIR(details.st_mode):
        raise OSError("unsafe directory")
    if created:
        try:
            os.chmod(path, mode)
        except OSError:
            pass


def _require_free_space(directory, reserve=MIN_XMLTV_FREE_BYTES):
    try:
        details = os.statvfs(directory)
        available = int(details.f_bavail) * int(details.f_frsize or details.f_bsize)
    except (AttributeError, OSError, TypeError, ValueError, OverflowError):
        return
    if available < int(reserve):
        raise XmltvDownloadError("not enough temporary space for XMLTV guide")


def _validate_atomic_target(path):
    if not os.path.lexists(path):
        return
    details = os.lstat(path)
    if stat.S_ISLNK(details.st_mode) or not stat.S_ISREG(details.st_mode):
        raise OSError("unsafe target")


def _temporary_path(target, purpose):
    directory = os.path.dirname(target) or "."
    return os.path.join(
        directory,
        ".{}.{}.{}.{}.tmp".format(
            os.path.basename(target),
            purpose,
            os.getpid(),
            uuid.uuid4().hex,
        ),
    )


def _create_private_file(path):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0)
    return os.open(path, flags, 0o600)


def _write_all(descriptor, body):
    offset = 0
    while offset < len(body):
        written = os.write(descriptor, body[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def _atomic_write(path, body, mode):
    if not isinstance(body, bytes):
        raise TypeError("atomic body must be bytes")
    directory = os.path.dirname(path) or "."
    directory_mode = 0o700 if mode & 0o077 == 0 else 0o755
    _ensure_directory(directory, directory_mode)
    _validate_atomic_target(path)
    temporary = _temporary_path(path, "write")
    descriptor = _create_private_file(temporary)
    try:
        _write_all(descriptor, body)
        os.fsync(descriptor)
        os.fchmod(descriptor, mode)
    except Exception:
        os.close(descriptor)
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    else:
        os.close(descriptor)
    try:
        os.replace(temporary, path)
        os.chmod(path, mode)
        _fsync_directory(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def _install_staged_file(staged_path, destination, mode):
    _validate_atomic_target(destination)
    os.chmod(staged_path, mode)
    os.replace(staged_path, destination)
    os.chmod(destination, mode)
    _fsync_directory(os.path.dirname(destination) or ".")


def _fsync_directory(directory):
    try:
        descriptor = os.open(directory, os.O_RDONLY | getattr(os, "O_CLOEXEC", 0))
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    except OSError:
        pass


def _check_cancelled(cancel_event, deadline):
    if cancel_event is not None:
        try:
            if cancel_event.is_set():
                raise OperationCancelled("DVB EPG operation was cancelled")
        except AttributeError as error:
            raise ValueError("invalid cancellation event") from error
    if deadline is not None:
        try:
            expired = time.monotonic() >= float(deadline)
        except (TypeError, ValueError, OverflowError) as error:
            raise ValueError("invalid DVB EPG deadline") from error
        if expired:
            raise OperationCancelled("DVB EPG operation timed out")


def _remaining_timeout(deadline, fallback):
    if deadline is None:
        return float(fallback)
    remaining = float(deadline) - time.monotonic()
    if remaining <= 0:
        raise OperationCancelled("DVB EPG operation timed out")
    return min(float(fallback), remaining)


def _absolute_download_deadline(timeout, deadline):
    """Return one finite deadline for connection, transfer and validation."""
    started = time.monotonic()
    automatic = started + float(timeout)
    if deadline is None:
        return automatic
    try:
        supplied = float(deadline)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError("invalid XMLTV download deadline") from error
    if not math.isfinite(supplied):
        raise ValueError("invalid XMLTV download deadline")
    return min(automatic, supplied)


def _set_response_socket_timeout(response, timeout):
    """Apply the remaining absolute deadline to urllib's live socket."""
    candidates = []
    response_file = getattr(response, "fp", None)
    raw_file = getattr(response_file, "raw", None)
    for candidate in (
        getattr(raw_file, "_sock", None),
        getattr(raw_file, "sock", None),
        getattr(response_file, "_sock", None),
        getattr(response_file, "sock", None),
        getattr(response, "_sock", None),
        getattr(response, "sock", None),
    ):
        if (
            candidate is not None
            and callable(getattr(candidate, "settimeout", None))
            and all(candidate is not item for item in candidates)
        ):
            candidates.append(candidate)
    if not candidates:
        raise OSError("XMLTV response socket is unavailable")
    last_error = None
    for candidate in candidates:
        try:
            candidate.settimeout(float(timeout))
            return
        except (OSError, TypeError, ValueError) as error:
            last_error = error
    raise OSError("XMLTV response socket timeout could not be set") from last_error

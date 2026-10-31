# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account-free, bounded XMLTV sources for native DVB services only.

This module never imports playlist, a player, a relay or a playback API.
Network/XML work belongs on a worker. Native EPG import belongs to the GUI.
"""
import collections
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import time
import unicodedata
from xml.etree import ElementTree as ET

from . import dvb_epg as core

CACHE_DIRECTORY = '/tmp/gtiptvplayerpro-public-dvb-epg-v1'
DEFAULT_PROFILE = 'public-all-satellites-v1'
MAPPING_KEY = 'public-dvb-v1'
PROFILE_KEYS = (DEFAULT_PROFILE, 'public-tr-v1', 'public-hotbird-v1')
LEGACY_COMBINED_PROFILE = 'public-tr-hotbird-v1'
MAX_COMPRESSED_BYTES = 16 * 1024 * 1024
MAX_EXPANDED_BYTES = 128 * 1024 * 1024
MAX_XZ_MEMORY = 32 * 1024 * 1024
MAX_CACHE_AGE_SECONDS = 36 * 60 * 60
MAX_METADATA_CHANNELS = 12000
TV_TYPES = frozenset((1, 17, 22, 25, 27, 31, 32))


class GuideSource(object):
    def __init__(self, key):
        if key == LEGACY_COMBINED_PROFILE:
            key = DEFAULT_PROFILE
        if key not in PROFILE_KEYS:
            raise ValueError('invalid public DVB EPG profile')
        self.key = key


def load_guide_sources():
    return tuple(GuideSource(key) for key in PROFILE_KEYS)


def find_guide_source(key):
    return GuideSource(key) if key in PROFILE_KEYS or key == LEGACY_COMBINED_PROFILE else None


def guide_mapping_key(source):
    if not isinstance(source, GuideSource):
        raise ValueError('a public DVB EPG source is required')
    return MAPPING_KEY


class Feed(object):
    def __init__(self, key, label, satellite, filename, family, mapping=False, urls=None, regions=()):
        self.key, self.label, self.satellite = key, label, satellite
        self.family, self.mapping = family, mapping
        self.regions = frozenset(regions)
        if urls is not None:
            self.urls = tuple(urls)
        elif family == 'ripper':
            self.urls = ('https://epgshare01.online/epgshare01/' + filename,)
        elif family == 'openepg':
            self.urls = ('https://www.open-epg.com/files/' + filename,)
        else:
            # These are the working URLs published in OE-Alliance's catalogue.
            self.urls = ('http://www.xmltvepg.nl/' + filename,
                         'http://epg.vuplus-community.net/' + filename)


TURKEY_FEEDS = (
    Feed('tr-ripper3', 'EPG-Ripper TR3', 420, 'epg_ripper_TR3.xml.gz', 'ripper'),
    Feed('tr-ripper1', 'EPG-Ripper TR1', 420, 'epg_ripper_TR1.xml.gz', 'ripper'),
    Feed('tr-open4', 'Open-EPG Turkey4', 420, 'turkey4.xml.gz', 'openepg'),
    Feed('tr-open3', 'Open-EPG Turkey3', 420, 'turkey3.xml.gz', 'openepg'),
    Feed('tr-open1', 'Open-EPG Turkey1', 420, 'turkey1.xml.gz', 'openepg'),
    Feed('tr-open2', 'Open-EPG Turkey2', 420, 'turkey2.xml.gz', 'openepg'),
)
HOTBIRD_FEEDS = tuple(
    Feed(key, label, 130, filename, family)
    for key, label, filename, family in (
        ('pl-basic', 'Azman Polska', 'azman_basic.xml.xz', 'azman'),
        ('pl-misc', 'Azman Polska extra', 'azman_misc.xml.xz', 'azman'),
        ('it-basic', 'Rytec Italia', 'rytecIT_Basic.xz', 'rytec'),
        ('it-sky', 'Rytec Italia Sky', 'rytecIT_Sky.xz', 'rytec'),
        ('it-extra', 'Rytec Italia extra', 'rytecIT_SportMovies.xz', 'rytec'),
        ('gr-basic', 'Rytec Ellada', 'rytecGR_Basic.xz', 'rytec'),
        ('gr-extra', 'Rytec Ellada extra', 'rytecGR_SportMovies.xz', 'rytec'),
        ('ch-basic', 'Rytec Switzerland', 'rytecCH_Basic.xz', 'rytec'),
        ('de-basic', 'Rytec Deutschland', 'rytecDE_Basic.xz', 'rytec'),
        ('de-common', 'Rytec D/A/CH', 'rytecDE_Common.xz', 'rytec'),
        ('de-extra', 'Rytec D/A/CH extra', 'rytecDE_SportMovies.xz', 'rytec'),
        ('at-basic', 'Rytec Osterreich', 'rytecAT_Basic.xz', 'rytec'),
        ('fr-tnt', 'Rytec France TNT', 'rytecTNT_Basic.xz', 'rytec'),
        ('fr-misc', 'Rytec France', 'rytecFR_Mixte.xz', 'rytec'),
        ('fr-extra', 'Rytec France extra', 'rytecFR_SportMovies.xz', 'rytec'),
        ('news', 'Rytec News', 'rytecNWS.xz', 'rytec'),
        ('misc', 'Rytec Misc', 'rytecMisc.xz', 'rytec'),
    )
)
MAPPING_FEEDS = {
    'rytec': Feed('rytec-map', 'Rytec DVB', 130, 'rytec.channels.xml.xz', 'rytec', True),
    'azman': Feed('azman-map', 'Azman DVB', 130, 'azman.channels.xml.xz', 'azman', True),
}

with Path(__file__).with_name('dvb_epg_catalog.json').open(encoding='utf-8') as _catalog_file:
    _CATALOG = json.load(_catalog_file)
GLOBAL_FEEDS = tuple(Feed(row['key'], row['label'], None, '', row['family'],
                         urls=row['urls'], regions=row['regions']) for row in _CATALOG['feeds'])
GLOBAL_MAPPING_FEEDS = {
    family: tuple((Feed(row['key'], family + ' DVB', None, '', family, True, urls=row['urls']),
                   None if row['satellites'] is None else frozenset(row['satellites']))
                  for row in rows)
    for family, rows in _CATALOG['mappings'].items()
}


def source_accepts_service(source, service):
    parsed = core.parse_dvb_service_reference(service.service_ref)
    if not parsed or parsed['service_type'] not in TV_TYPES:
        return False
    satellite = parsed['dvb_namespace'] >> 16
    if source.key == 'public-tr-v1':
        return satellite == 420
    if source.key == 'public-hotbird-v1':
        return satellite == 130
    return 0 <= satellite < 3600  # Exclude terrestrial/cable namespaces.


def selected_feeds(source, services):
    guide_mapping_key(source)
    services = tuple(services)
    satellites = {service.identity[1] >> 16 for service in services}
    feeds = []
    if source.key == DEFAULT_PROFILE:
        if 420 in satellites:
            feeds.extend(TURKEY_FEEDS)
        if any(source_accepts_service(source, s) for s in services):
            feeds.extend(GLOBAL_FEEDS)
        return tuple(feeds)
    if source.key != 'public-hotbird-v1' and 420 in satellites:
        feeds.extend(TURKEY_FEEDS)
    if source.key != 'public-tr-v1' and 130 in satellites:
        feeds.extend(HOTBIRD_FEEDS)
    return tuple(feeds)


class _XZReader(object):
    """Incremental xz reader with a fixed decoder-memory ceiling."""
    def __init__(self, raw):
        try:
            import lzma
        except ImportError as error:
            raise core.XmltvDownloadError('Python lzma support is unavailable') from error
        self.raw = raw
        self.decoder = lzma.LZMADecompressor(memlimit=MAX_XZ_MEMORY)
        self.error_type = lzma.LZMAError

    def read(self, size):
        try:
            while not self.decoder.eof:
                data = self.raw.read(65536) if self.decoder.needs_input else b''
                if not data and self.decoder.needs_input:
                    raise core.XmltvError('incomplete xz guide')
                result = self.decoder.decompress(data, max_length=size)
                if result:
                    return result
            return b''
        except self.error_type as error:
            raise core.XmltvError('invalid xz guide') from error


def _open_decoded(path):
    raw = core._open_regular_file(path, MAX_COMPRESSED_BYTES)
    try:
        magic = raw.read(6)
        raw.seek(0)
        if magic.startswith(b'\x1f\x8b'):
            return raw, gzip.GzipFile(fileobj=raw, mode='rb')
        if magic.startswith(b'\xfd7zXZ'):
            return raw, _XZReader(raw)
        return raw, raw
    except Exception:
        raw.close()
        raise


def _elements(path, mapping=False, cancel_event=None, deadline=None):
    raw, decoded = _open_decoded(path)
    try:
        if not mapping:
            yield from core._iter_xmltv_file(decoded, cancel_event=cancel_event,
                                             deadline=deadline, maximum_bytes=MAX_EXPANDED_BYTES)
        else:
            reader = core._BoundedXmlReader(decoded, 4 * 1024 * 1024,
                                            cancel_event=cancel_event, deadline=deadline)
            depth = count = 0
            root = None
            for event, element in ET.iterparse(reader, events=('start', 'end')):
                reader.mark_parser_event()
                if event == 'start':
                    depth += 1
                    if root is None:
                        root = element
                        if element.tag != 'channels':
                            raise core.XmltvError('invalid DVB mapping document')
                    if depth > 4:
                        raise core.XmltvError('DVB mapping nesting limit')
                else:
                    if depth == 2:
                        count += 1
                        if count > 50000:
                            raise core.XmltvError('DVB mapping count limit')
                        core._check_cancelled(cancel_event, deadline)
                        if element.tag == 'channel':
                            yield 'channel', element
                        root.remove(element)
                        element.clear()
                    depth -= 1
            if root is None or depth:
                raise core.XmltvError('incomplete DVB mapping document')
    except (ET.ParseError, EOFError) as error:
        raise core.XmltvError('invalid compressed EPG document') from error
    finally:
        if decoded is not raw and hasattr(decoded, 'close'):
            decoded.close()
        raw.close()


def _validate_feed(path, feed, cancel_event, deadline):
    channels = programmes = usable = 0
    for tag, element in _elements(path, feed.mapping, cancel_event, deadline):
        if tag == 'channel':
            channels += 1
            if not feed.mapping:
                core._parse_xmltv_channel(element)
            if channels > (50000 if feed.mapping else MAX_METADATA_CHANNELS):
                raise core.XmltvError('too many guide channels')
        else:
            programmes += 1
            if programmes > core.MAX_XMLTV_PROGRAMMES:
                raise core.XmltvError('too many guide programmes')
            try:
                start, stop = core._programme_time_window(element, 0)
                event = core._programme_event_tuple(element, start, stop)
                if stop > time.time() and not _placeholder(event[2]):
                    usable += 1
            except ValueError:
                pass
    if not channels or (not feed.mapping and not usable):
        raise core.XmltvError('empty EPG source')


def fetch_feed(feed, directory=CACHE_DIRECTORY, cancel_event=None, deadline=None, opener=None):
    """Install a fully validated compressed cache atomically; preserve old on failure."""
    fd = core._open_private_cache_directory(directory)
    name = feed.key + '.feed'
    staged = ''
    failure = None
    try:
        core._require_free_space_descriptor(fd)
        core._validate_cache_target(fd, name)
        for url in feed.urls:
            core._check_cancelled(cancel_event, deadline)
            staged = core._temporary_cache_name(name, 'download')
            response = None
            try:
                response, _redirects = core._open_xmltv_response(url, 10, cancel_event, deadline, opener)
                length = response.headers.get('Content-Length', '')
                if length and (int(length) < 0 or int(length) > MAX_COMPRESSED_BYTES):
                    raise core.XmltvDownloadError('public EPG download size limit')
                core._copy_response_to_file(response, staged, MAX_COMPRESSED_BYTES,
                                            cancel_event, deadline, directory_descriptor=fd, timeout=10)
                response.close()
                response = None
                core._verify_private_cache_directory(directory, fd)
                _validate_feed(os.path.join(directory, staged), feed, cancel_event, deadline)
                core._install_cache_entry(fd, staged, name, 0o600, MAX_COMPRESSED_BYTES)
                staged = ''
                return os.path.join(directory, name)
            except core.OperationCancelled:
                raise
            except (core.DvbEpgError, OSError, ValueError) as error:
                failure = error
            finally:
                if response is not None:
                    response.close()
                if staged:
                    try:
                        os.unlink(staged, dir_fd=fd)
                    except OSError:
                        pass
                    staged = ''
        raise core.XmltvDownloadError('public EPG source is unavailable') from failure
    finally:
        os.close(fd)


def cached_feed(feed, directory=CACHE_DIRECTORY, now=None):
    path = os.path.join(directory, feed.key + '.feed')
    try:
        age = (time.time() if now is None else now) - os.lstat(path).st_mtime
        if age < -300 or age > MAX_CACHE_AGE_SECONDS:
            return None
        with core._open_regular_file(path, MAX_COMPRESSED_BYTES):
            pass
        return path
    except OSError:
        return None


def _path_for_feed(feed, download, directory, cancel_event, deadline, fetcher):
    if download:
        try:
            return fetcher(feed, directory=directory, cancel_event=cancel_event, deadline=deadline)
        except core.OperationCancelled:
            raise
        except (core.DvbEpgError, OSError, ValueError):
            pass
    return cached_feed(feed, directory)


def _name_key(value, drop_quality=False):
    text = re.sub(r'\.tr\s*$', '', str(value or ''), flags=re.I).strip()
    text = text.replace('ı', 'i').replace('İ', 'I')
    text = ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).casefold()
    if drop_quality:
        text = re.sub(r'(?:\s*[-_.( ]?\s*(?:fhd|hd|sd)\s*\)?\s*)+$', '', text)
    text = text.replace('+', ' plus ')
    return ''.join(c for c in text if c.isalnum())


def _placeholder(title):
    key = _name_key(title)
    return key in ('nodata', 'nodataavailable', 'noinformation', 'noinformationavailable',
                   'noepg', 'noepgavailable', 'noepgdata', 'noepgdataavailable',
                   'programbilgisiyok', 'epgbilgisibulunamadi', 'yayinakisibulunamadi')


def _channel_key(feed, native_id):
    return feed.key + ':' + hashlib.sha256(native_id.encode('utf-8')).hexdigest()[:32]


class GuideSnapshot(object):
    def __init__(self, source, services, records, paths, identities, now):
        self.source, self.services, self.records = source, tuple(services), records
        self.paths, self.identities, self.now = paths, identities, now
        self.channels = tuple(core.XmltvChannel(key, (r['names'][0] + ' · ' + r['feed'].label,))
                              for key, r in records.items())


class GuideMatch(object):
    """Service reference -> source-scoped channel ID, allowing DVB simulcasts."""
    def __init__(self, mappings, blocked=(), ambiguous=()):
        self.mappings = collections.OrderedDict(mappings)
        self.blocked_services = tuple(blocked)
        self.ambiguous_services = tuple(ambiguous)


def load_guide(source, services, download=False, directory=CACHE_DIRECTORY,
               cancel_event=None, deadline=None, fetcher=fetch_feed, now=None):
    now = int(time.time() if now is None else now)
    guide_mapping_key(source)
    services = tuple(s for s in services if source_accepts_service(source, s))
    feeds = selected_feeds(source, services)
    paths = {}
    identities = collections.defaultdict(set)
    target_identities = {s.identity for s in services}
    satellites = {s.identity[1] >> 16 for s in services}
    families = {feed.family for feed in feeds if feed.family in GLOBAL_MAPPING_FEEDS}
    for family in sorted(families):
        for feed, scope in GLOBAL_MAPPING_FEEDS[family]:
            if scope is not None and not satellites.intersection(scope):
                continue
            path = _path_for_feed(feed, download, directory, cancel_event, deadline, fetcher)
            if not path:
                continue
            try:
                pending = collections.defaultdict(set)
                for _tag, element in _elements(path, True, cancel_event, deadline):
                    parsed = core.parse_dvb_service_reference((element.text or '').strip())
                    ident = str(element.get('id', '')).strip()
                    if parsed and parsed['identity'] in target_identities and ident:
                        pending[(family, ident)].add(parsed['identity'])
                for key, values in pending.items():
                    identities[key].update(values)
            except core.OperationCancelled:
                raise
            except (core.DvbEpgError, OSError, ValueError):
                continue
    wanted_ids = collections.defaultdict(set)
    for family, ident in identities:
        wanted_ids[family].add(ident)
    if source.key == DEFAULT_PROFILE:
        selected = []
        for feed in feeds:
            if feed.family not in GLOBAL_MAPPING_FEEDS:
                selected.append(feed)
                continue
            native_ids = wanted_ids[feed.family]
            if not native_ids:
                continue
            regional_feeds = [f for f in feeds if f.family == feed.family and f.regions]
            known = set().union(*(f.regions for f in regional_feeds)) if regional_feeds else set()
            suffixes = {ident.rsplit('.', 1)[-1].casefold() for ident in native_ids}
            # Unknown ID formats retain all sources; never guess a country.
            if feed.regions and suffixes <= known and not feed.regions.intersection(suffixes):
                continue
            selected.append(feed)
        feeds = tuple(selected)
    records = collections.OrderedDict()
    for priority, feed in enumerate(feeds):
        core._check_cancelled(cancel_event, deadline)
        path = _path_for_feed(feed, download, directory, cancel_event, deadline, fetcher)
        if not path:
            continue
        pending = collections.OrderedDict()
        try:
            stats = collections.defaultdict(lambda: [False, 0, 0])
            for tag, element in _elements(path, False, cancel_event, deadline):
                if tag == 'channel':
                    channel = core._parse_xmltv_channel(element)
                    if (source.key == DEFAULT_PROFILE and feed.family in GLOBAL_MAPPING_FEEDS
                            and channel.xmltv_id not in wanted_ids[feed.family]):
                        continue
                    if len(pending) >= MAX_METADATA_CHANNELS:
                        raise core.XmltvError('public guide channel limit')
                    pending[channel.xmltv_id] = channel.display_names or (channel.xmltv_id,)
                else:
                    try:
                        if element.get('channel', '') not in pending:
                            continue
                        start, stop = core._programme_time_window(element, 0)
                        title = core._element_text(next((e for e in element if core._local_name(e.tag) == 'title'), ET.Element('title')), core.MAX_TITLE_LENGTH)
                        if not title or _placeholder(title) or stop <= now:
                            continue
                        stat = stats[element.get('channel', '')]
                        stat[0] = stat[0] or start <= now < stop
                        stat[1] = max(stat[1], stop)
                        stat[2] += 1
                    except ValueError:
                        continue
            if len(records) + sum(ident in stats for ident in pending) > MAX_METADATA_CHANNELS:
                raise core.XmltvError('combined public guide channel limit')
            for native_id, names in pending.items():
                if native_id not in stats:
                    continue
                key = _channel_key(feed, native_id)
                records[key] = {'feed': feed, 'native_id': native_id, 'names': names,
                                'current': stats[native_id][0], 'last_stop': stats[native_id][1],
                                'priority': priority}
            paths[feed.key] = path
        except core.OperationCancelled:
            raise
        except (core.DvbEpgError, OSError, ValueError):
            continue
    return GuideSnapshot(source, services, records, paths, identities, now)


def match_guide(snapshot, manual_entries=()):
    refs = {s.service_ref for s in snapshot.services}
    manual = {m.service_ref: m for m in manual_entries if m.source_key == MAPPING_KEY and m.service_ref in refs}
    by_identity = collections.defaultdict(list)
    names = [collections.defaultdict(list), collections.defaultdict(list)]
    for key, record in snapshot.records.items():
        feed = record['feed']
        if feed.family in GLOBAL_MAPPING_FEEDS:
            for identity in snapshot.identities.get((feed.family, record['native_id']), ()):
                by_identity[identity].append(key)
        else:
            for tier in (0, 1):
                for value in set(record['names']) | {record['native_id']}:
                    nk = _name_key(value, bool(tier))
                    if nk and key not in names[tier][nk]:
                        names[tier][nk].append(key)
    result = collections.OrderedDict()
    blocked, ambiguous = [], []
    for service in snapshot.services:
        core._validated_dvb_services((service,))
        entry = manual.get(service.service_ref)
        if entry is not None:
            if entry.blocked:
                blocked.append(service.service_ref)
            elif entry.xmltv_id in snapshot.records:
                result[service.service_ref] = entry.xmltv_id
            # A stale manual choice is retained for the user, never guessed over.
            continue
        satellite = service.identity[1] >> 16
        candidates = by_identity.get(service.identity, ())
        if candidates:
            counts = collections.Counter(snapshot.records[k]['feed'].key for k in candidates)
            candidates = [k for k in candidates if counts[snapshot.records[k]['feed'].key] == 1]
        if satellite == 420 and not candidates:
            for tier in (0, 1):
                found = names[tier].get(_name_key(service.name, bool(tier)), ())
                if not found:
                    continue
                # Multiple sources may describe one station; an ambiguity inside
                # one source is not silently resolved by choosing a random ID.
                counts = collections.Counter(snapshot.records[k]['feed'].key for k in found)
                candidates = [k for k in found if counts[snapshot.records[k]['feed'].key] == 1]
                if candidates:
                    break
        if candidates:
            selected = min(candidates, key=lambda k: (not snapshot.records[k]['current'], snapshot.records[k]['priority']))
            result[service.service_ref] = selected
        elif source_accepts_service(snapshot.source, service):
            ambiguous.append(service.service_ref)
    return GuideMatch(result, blocked, ambiguous)


def build_guide_events(snapshot, matched, epg_days=3, cancel_event=None, deadline=None):
    earliest = snapshot.now - core.DEFAULT_IMPORT_PAST_SECONDS
    latest = snapshot.now + int(epg_days) * 86400
    wanted = collections.defaultdict(lambda: collections.defaultdict(list))
    for ref, key in matched.mappings.items():
        record = snapshot.records[key]
        wanted[record['feed'].key][record['native_id']].append(ref)
    groups = []
    programme_count = invalid_count = count = text_count = 0
    for feed_key, native in wanted.items():
        events = collections.defaultdict(list)
        for tag, element in _elements(snapshot.paths[feed_key], False, cancel_event, deadline):
            if tag != 'programme':
                continue
            programme_count += 1
            ident = element.get('channel', '')
            if ident not in native:
                continue
            try:
                start, stop = core._programme_time_window(element, 0)
                if stop <= earliest or start >= latest:
                    continue
                event = core._programme_event_tuple(element, start, stop)
                if _placeholder(event[2]):
                    continue
            except ValueError:
                invalid_count += 1
                continue
            count += 1
            text_count += sum(len(value) for value in event[2:5])
            if count > core.MAX_IMPORT_EVENTS or text_count > core.MAX_IMPORT_TEXT_CHARACTERS:
                raise core.XmltvError('public DVB EPG memory limit')
            if len(events[ident]) >= core.MAX_EVENTS_PER_XMLTV_CHANNEL:
                raise core.XmltvError('public DVB EPG channel event limit')
            events[ident].append(event)
        for ident, rows in events.items():
            deduplicated = {(row[0], row[1], row[2]): row for row in rows}
            unique = tuple(deduplicated[key] for key in sorted(deduplicated))
            if unique:
                groups.append((tuple(native[ident]), unique))
    if not groups:
        raise core.DvbEpgError('public guides contain no usable mapped programmes')
    return core.ImportEventResult(groups, programme_count=programme_count,
                                   imported_count=sum(len(events) for _refs, events in groups), invalid_count=invalid_count)


def update_guide(source, services, epg_days=3, cancel_event=None, deadline=None,
                 directory=CACHE_DIRECTORY, fetcher=fetch_feed, now=None):
    snapshot = load_guide(source, services, True, directory, cancel_event, deadline, fetcher, now)
    matched = match_guide(snapshot, core.load_manual_mappings())
    imported = build_guide_events(snapshot, matched, epg_days, cancel_event, deadline)
    active = {ref for refs, _events in imported.groups for ref in refs}
    matched.mappings = collections.OrderedDict((ref, key) for ref, key in matched.mappings.items() if ref in active)
    return matched, imported

# -*- coding: utf-8 -*-
# SPDX-FileCopyrightText: 2026 VicTuS59
# SPDX-License-Identifier: GPL-2.0-or-later
"""Account-free Polish catalogue adapters; never inspect the media stream."""
import base64
import io
import os
import re
import unicodedata
import zipfile
from html import unescape
from html.parser import HTMLParser
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree as ET
from .subtitle_language import (
    filename_languages, normalize_subtitle_language, subtitle_episode_matches,
    subtitle_episode_numbers,
)
from .subtitle_decode import decode_subtitle_content

MAX_RESPONSE = 2 * 1024 * 1024

POLISH_PROVIDERS = ('napiprojekt', 'napisy24')


def polish_search_providers(languages):
    if isinstance(languages, str):
        languages = (languages,)
    first = next(iter(languages or ()), '')
    return POLISH_PROVIDERS if normalize_subtitle_language(first) == 'pl' else ()


def _error(code, status):
    from .web_subtitles import SubtitleError
    return SubtitleError(code, status)


def _text(value, limit=240):
    return ' '.join(str(value or '').split())[:limit]


def _decode(raw):
    return decode_subtitle_content(raw, 'pl')


def _xml(raw, fragments=False):
    text = _decode(raw)
    if re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I):
        raise _error('provider_invalid_reply', 502)
    text = re.sub(r'<\?xml[^?]*\?>', '', text).strip()
    try:
        return ET.fromstring('<results>' + text + '</results>' if fragments else text)
    except ET.ParseError:
        raise _error('provider_invalid_reply', 502)


def _raw(url, domain, fields=None):
    from .web_subtitles import SubtitleError, _request
    try:
        return _request(url, (domain,), headers={
            'Referer': 'https://' + domain + '/',
            'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
        }, payload=urlencode(fields).encode('utf-8') if fields is not None else None,
            json_response=False, timeout=6)
    except SubtitleError as error:
        # These sources have no user credentials; an access block is an outage.
        if error.code == 'provider_key_rejected':
            raise _error('provider_unavailable', 502)
        raise


def _title_key(title):
    text = unicodedata.normalize('NFKD', _text(title).casefold()).replace('ł', 'l')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    words = re.findall(r'[^\W_]+', text, re.UNICODE)
    if words and words[0] in ('the', 'a', 'an'):
        words.pop(0)
    return ' '.join(words)


def _number(value):
    try:
        number = int(value or 0)
        return number if 0 <= number <= 9999 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _fps(value):
    try:
        number = float(str(value or '').replace(',', '.'))
        return number if 10 <= number <= 120 else 0
    except (TypeError, ValueError, OverflowError):
        return 0


def _episode_match(meta, season=0, episode=0, release=''):
    return subtitle_episode_matches({'season': season, 'episode': episode, 'release': release},
                                    meta.get('season'), meta.get('episode'))


def _time(milliseconds):
    milliseconds = max(0, int(round(milliseconds)))
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return '%02d:%02d:%02d,%03d' % (hours, minutes, seconds, ms)


def subtitle_utf8(raw, fps=0):
    """Normalize Polish text and common catalogue formats to the shared parser."""
    text = _decode(raw).replace('\r\n', '\n').replace('\r', '\n').strip()
    if '-->' in text:
        return text.encode('utf-8')
    cues, timed = [], []
    rate = _fps(fps)
    for line in text.splitlines()[:20001]:
        match = re.match(r'^\{(\d+)\}\{(\d+)\}(.*)$', line)
        if match:
            start, end, words = match.groups()
            if start == end and start in ('0', '1') and _fps(words):
                rate = _fps(words)
                continue
            if not rate:
                raise _error('subtitle_format_unsupported', 422)
            cues.append((int(start) * 1000 / rate, int(end) * 1000 / rate, words))
            continue
        match = re.match(r'^\[(\d+)\]\[(\d+)\](.*)$', line)
        if match:
            start, end, words = match.groups()
            cues.append((int(start) * 100, int(end) * 100, words))
            continue
        match = re.match(r'^(\d{1,3}):(\d{2}):(\d{2})[:=](.*)$', line)
        if match:
            hours, minutes, seconds, words = match.groups()
            if int(minutes) < 60 and int(seconds) < 60:
                timed.append(((int(hours) * 3600 + int(minutes) * 60 + int(seconds)) * 1000, words))
    grouped = {}
    for start, words in timed:
        if words.strip():
            grouped.setdefault(start, []).append(words)
    starts = sorted(grouped)
    for index, start in enumerate(starts):
        end = min(start + 6000, starts[index + 1]) if index + 1 < len(starts) else start + 4000
        cues.append((start, end, '|'.join(grouped[start])))
    output = []
    for start, end, words in cues[:20000]:
        words = re.sub(r'\{[^}]{0,80}\}', '', words).replace('|', '\n').strip()
        if end > start and words:
            output.append('%d\n%s --> %s\n%s\n' % (len(output) + 1, _time(start), _time(end), words))
    if not output:
        raise _error('subtitle_format_unsupported', 422)
    return '\n'.join(output).encode('utf-8')


class CatalogueHTML(HTMLParser):
    def __init__(self, text):
        super().__init__(convert_charrefs=True)
        self.links, self.rows = [], []
        self.link, self.row, self.cell = None, None, None
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'tr':
            self.row = {'title': attrs.get('title', ''), 'links': [], 'cells': []}
        if tag == 'td' and self.row is not None:
            self.cell = []
        if tag == 'a':
            self.link = [attrs.get('href', ''), []]

    def handle_data(self, data):
        if self.link is not None:
            self.link[1].append(data)
        if self.cell is not None:
            self.cell.append(data)

    def handle_endtag(self, tag):
        if tag == 'a' and self.link is not None:
            link = (self.link[0], _text(''.join(self.link[1])))
            self.links.append(link)
            if self.row is not None:
                self.row['links'].append(link)
            self.link = None
        if tag == 'td' and self.row is not None and self.cell is not None:
            self.row['cells'].append(_text(''.join(self.cell)))
            self.cell = None
        if tag == 'tr' and self.row is not None:
            self.rows.append(self.row)
            self.row = self.cell = None


def _napi_path(href, pattern):
    parsed = urlsplit(href)
    if parsed.scheme not in ('', 'http', 'https') or parsed.hostname not in (None, 'www.napiprojekt.pl', 'napiprojekt.pl'):
        return ''
    path = parsed.path.lstrip('/')
    return path if re.fullmatch(pattern, path) and not parsed.query else ''


class NapiProjektClient:
    BASE = 'https://www.napiprojekt.pl'

    def search(self, metadata, languages):
        if not polish_search_providers(languages):
            return []
        from .online_subtitles import normalized_metadata
        meta = normalized_metadata(metadata, 'pl')
        query = meta['search_title']
        queries = [query]
        shorter = re.sub(r'(?i)^(?:the|a|an)\s+', '', query)
        if shorter != query:
            queries.append(shorter)
        catalog = []
        for query in queries:
            raw = _raw(self.BASE + '/ajax/search_catalog.php', 'napiprojekt.pl', {
                'queryString': query, 'queryKind': '1' if meta['season'] else '2',
                'queryYear': '' if meta['season'] else meta['year'], 'associate': '',
            })
            html = CatalogueHTML(_decode(raw))
            for href, title in html.links:
                path = _napi_path(href, r'napisy-[1-9]\d{0,9}-[^/]{1,300}')
                name = re.sub(r'\s*(?:\((?:19|20)\d{2}\)|-\s*(?:19|20)\d{2}r\.?)\s*$', '', title)
                names = [name, re.sub(r'\s*\([^)]*\)\s*$', '', name)]
                names.extend(re.findall(r'\(([^)]*)\)', name))
                year = re.search(r'((?:19|20)\d{2})(?:\)|r\.?)?\s*$', title)
                year_matches = (not meta['year'] or meta['season'] or not year
                                or int(year.group(1)) == meta['year'])
                if path and year_matches and any(_title_key(n) == _title_key(meta['search_title']) for n in names):
                    catalog.append(path)
            if catalog:
                break
        results, seen = [], set()
        for path in dict.fromkeys(catalog):
            if len(results) >= 30:
                break
            page = CatalogueHTML(_decode(_raw(self.BASE + '/' + path, 'napiprojekt.pl')))
            subtitle_path = next((_napi_path(href, r'napisy1,1,[01]-dla-[1-9]\d{0,9}-[^/]{1,300}')
                                  for href, title in page.links if _napi_path(href, r'napisy1,1,[01]-dla-[1-9]\d{0,9}-[^/]{1,300}')), '')
            if not subtitle_path:
                continue
            subtitle_url = self.BASE + '/' + subtitle_path
            if meta['season'] and meta['episode']:
                # The public season/episode form uses this redirect endpoint.
                # Select the requested episode instead of scanning page one.
                identifier = re.match(r'napisy-(\d+)-', path).group(1)
                year = re.search(r'\((\d{4})\)$', path)
                subtitle_url = self.BASE + '/movie-subtitles-redirect.php?' + urlencode({
                    'sezon': meta['season'], 'odcinek': meta['episode'],
                    'rok': year.group(1) if year else meta['year'],
                    'id': identifier, 'tytul': meta['title'],
                })
            page = CatalogueHTML(_decode(_raw(subtitle_url, 'napiprojekt.pl')))
            for row in page.rows:
                for href, release in row['links']:
                    match = re.fullmatch(r'napiprojekt:([a-fA-F0-9]{32})', href)
                    if not match or match.group(1).lower() in seen or not _episode_match(meta, release=release):
                        continue
                    seen.add(match.group(1).lower())
                    fps_match = re.search(r'Video FPS:\s*(?:</?[^>]+>\s*)*([\d.,]+)', unescape(row['title']), re.I)
                    rate = _fps(fps_match.group(1)) if fps_match else _fps(row['cells'][2] if len(row['cells']) > 2 else 0)
                    results.append({'provider':'napiprojekt','title':meta['title'],'release':release,
                                    'language':'pl','subtitle_id':match.group(1).lower(),
                                    'season':meta['season'],'episode':meta['episode'],'fps':rate,
                                    'identity_match':True,'hearing_impaired':False})
                    if len(results) >= 30:
                        break
            if len(catalog) > 1:
                # One exact title suffices; do not enumerate the whole catalogue.
                break
        return results[:30]

    def download(self, result):
        if not isinstance(result, dict) or result.get('provider') != 'napiprojekt' or normalize_subtitle_language(result.get('language')) != 'pl' or not re.fullmatch(r'[a-f0-9]{32}', str(result.get('subtitle_id') or '')):
            raise _error('subtitle_not_found', 404)
        raw = _raw(self.BASE + '/api/api-napiprojekt3.php', 'napiprojekt.pl', {
            'mode':'1','client':'NapiProjektPython','downloaded_subtitles_txt':'1',
            'downloaded_subtitles_id':result['subtitle_id'],'downloaded_subtitles_lang':'PL',
        })
        document = _xml(raw)
        if document.findtext('.//status') != 'success':
            raise _error('subtitle_not_found', 404)
        for marker in ('language', 'lang'):
            declared = document.findtext('.//' + marker)
            if declared and normalize_subtitle_language(declared) != 'pl':
                raise _error('subtitle_no_results', 404)
        returned_id = document.findtext('.//subtitles/id')
        if returned_id and returned_id.lower() != result['subtitle_id']:
            raise _error('subtitle_not_found', 404)
        try:
            encoded = re.sub(r'\s+', '', document.findtext('.//content') or '')
            content = base64.b64decode(encoded, validate=True)
        except (ValueError, TypeError):
            raise _error('provider_invalid_reply', 502)
        return subtitle_utf8(content, result.get('fps'))


class Napisy24Client:
    BASE = 'https://napisy24.pl'

    def search(self, metadata, languages):
        if not polish_search_providers(languages):
            return []
        from .online_subtitles import normalized_metadata
        meta = normalized_metadata(metadata, 'pl')
        imdb = str(meta.get('imdb_id') or '')
        query = {'imdb':imdb} if re.fullmatch(r'tt\d{5,12}', imdb) else {'title':meta['search_title']}
        document = _xml(_raw(self.BASE + '/libs/webapi.php?' + urlencode(query), 'napisy24.pl'), fragments=True)
        results, seen = [], set()
        for item in document.iter('subtitle'):
            values = {child.tag:_text(child.text) for child in item}
            identifier = values.get('id', '')
            if not re.fullmatch(r'[1-9]\d{0,14}', identifier) or identifier in seen:
                continue
            if values.get('language', '').lower() not in ('pl','pol','polish'):
                continue
            titles = [values.get('title', '')] + values.get('altTitle', '').split(';')
            identity = bool(imdb and values.get('imdb') == imdb)
            if not identity and not any(_title_key(t) == _title_key(meta['search_title']) for t in titles):
                continue
            if meta['year'] and values.get('year') and str(meta['year']) != values['year'] and not meta['season']:
                continue
            release = values.get('release') or values.get('title')
            if not _episode_match(meta,values.get('season'),values.get('episode'),release):
                continue
            seen.add(identifier)
            results.append({'provider':'napisy24','title':meta['title'],'release':release,
                            'language':'pl','subtitle_id':identifier,'season':meta['season'],
                            'episode':meta['episode'],'fps':_fps(values.get('fps')),'identity_match':True,
                            'hearing_impaired':False})
            if len(results) >= 30:
                break
        return results

    def download(self, result):
        if not isinstance(result, dict) or result.get('provider') != 'napisy24' or normalize_subtitle_language(result.get('language')) != 'pl' or not re.fullmatch(r'[1-9]\d{0,14}', str(result.get('subtitle_id') or '')):
            raise _error('subtitle_not_found', 404)
        raw = _raw(self.BASE + '/run/pages/download.php?' + urlencode({'napisId':result['subtitle_id']}), 'napisy24.pl')
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries = archive.infolist()
                if len(entries) > 128:
                    raise _error('subtitle_too_large', 422)
                candidates = [entry for entry in entries if not entry.is_dir()
                              and entry.filename.lower().endswith(('.srt','.vtt','.txt','.sub'))
                              and not entry.flag_bits & 1 and entry.file_size <= MAX_RESPONSE]
                if result.get('season') and result.get('episode'):
                    meta = {'season':_number(result['season']),'episode':_number(result['episode'])}
                    matching = [entry for entry in candidates if _episode_match(meta,release=os.path.basename(entry.filename))]
                    if matching:
                        candidates = matching
                    elif len(candidates) > 1 or any(subtitle_episode_numbers(entry.filename) for entry in candidates):
                        raise _error('subtitle_not_found', 404)
                if not candidates:
                    raise _error('subtitle_format_unsupported', 422)
                labelled = [(entry, filename_languages(entry.filename)) for entry in candidates]
                matching = [entry for entry, codes in labelled if codes == {'pl'}]
                if matching:
                    candidates = matching
                else:
                    candidates = [entry for entry, codes in labelled if not codes]
                stems = {os.path.splitext(entry.filename.lower())[0] for entry in candidates}
                if not candidates or len(stems) != 1:
                    # Multiple editions/CDs are not interchangeable. A
                    # matching extension variant of one file is safe.
                    raise _error('subtitle_no_results', 404)
                candidates.sort(key=lambda entry:(not entry.filename.lower().endswith('.srt'),entry.filename.lower()))
                with archive.open(candidates[0]) as stream:
                    content = stream.read(MAX_RESPONSE + 1)
        except (OSError, ValueError, zipfile.BadZipFile, RuntimeError):
            raise _error('subtitle_format_unsupported', 422)
        return subtitle_utf8(content, result.get('fps'))

# -*- coding: utf-8 -*-
# SPDX-License-Identifier: GPL-2.0-or-later
"""Validated provider rows directly into the selected-category RAM schema."""
from .content import (_clean_text, _safe_extension, _normalise_media_url,
                      _imdb_id, _numeric_id, _movie_search_text)
from .category_preferences import content_item_category_ids
from .content_sort import assign_sort_metadata
from .i18n import _
from .vod_catalog import (_compact_text, _category_id, _safe_poster_url,
                          _safe_portal_command, _safe_portal_request_type)


class CatalogRecord(dict):
    """A final RAM record with the few attributes needed by portal paging."""
    __slots__ = ('content_type',)

    def __init__(self, kind, **fields):
        super().__init__(fields)
        self.content_type = kind

    stream_id = property(lambda self: self['i'])
    icon = property(lambda self: self['p'], lambda self, value: self.__setitem__('p', value))
    sort_added = property(lambda self: self.get('sa', 0), lambda self, value: self.__setitem__('sa', value))
    sort_release = property(lambda self: self.get('sr', 0), lambda self, value: self.__setitem__('sr', value))
    portal_catalog_page = property(lambda self: self.get('j'), lambda self, value: self.__setitem__('j', value))
    portal_catalog_category_parameter = property(lambda self: self.get('h', 'category'), lambda self, value: self.__setitem__('h', value if value in ('category', 'genre') else 'category'))
    portal_catalog_series_flag = property(lambda self: self.get('f', ''), lambda self, value: self.__setitem__('f', value if value in ('series', 'is_series') else ''))


def _base(kind, identity, name, category, extension, icon, rating, year,
          tmdb, imdb, entry, account, selected, portal_ids=()):
    # Match the established display/codec bounds, including title truncation
    # before whitespace compaction. Only translate the fallback when needed.
    name = _compact_text(_clean_text(name) or _('Untitled content'), 180)
    category = str(category or '').strip()
    year = _compact_text(_clean_text(year), 20)
    members = set(content_item_category_ids(dict(category_id=category,
                  catalog_category_id=selected, portal_category_ids=portal_ids)))
    if selected:
        members.add(selected)
    record = CatalogRecord(kind, i=identity, n=name, c=selected or _category_id(category),
        g='', x=_safe_extension(extension, kind), y=year,
        r=_compact_text(_clean_text(rating), 20), p=_safe_poster_url(icon, account),
        t=str(tmdb or '').strip()[:32], b=_imdb_id(imdb),
        a=sorted(str(value)[:40] for value in members)[:128],
        u=_movie_search_text(name+' '+year))
    return assign_sort_metadata(record, entry)


def xtream_record(entry, kind, account, selected=''):
    if not isinstance(entry, dict):
        return None
    identity = _numeric_id(entry.get('series_id' if kind == 'series' else 'stream_id'))[:40]
    if not identity:
        return None
    icon = _normalise_media_url(entry.get('stream_icon') or entry.get('cover')
        or entry.get('movie_image') or entry.get('cover_big') or entry.get('poster')
        or entry.get('poster_path') or entry.get('icon'), account)
    return _base(kind, identity, entry.get('name'), entry.get('category_id'),
        entry.get('container_extension'), icon, entry.get('rating') or entry.get('rating_5based'),
        entry.get('year') or entry.get('releaseDate'), entry.get('tmdb_id') or entry.get('tmdb'),
        entry.get('imdb_id') or entry.get('imdbId') or entry.get('imdb'), entry, account, selected)


def stalker_record(client, entry, kind, request_type, allow_composite, selected):
    identity = client._media_identifier(entry, kind, allow_series_composite=allow_composite)
    if not identity:
        return None
    command = client._channel_command(entry)
    if kind == 'movie' and not command:
        command = '/media/{}.mpg'.format(identity)
    members = client._category_ids(entry)
    icon = client._first_media_url(entry, 'screenshot_uri', 'movie_image', 'cover_big',
                                   'cover', 'poster', 'stream_icon', 'logo')
    record = _base(kind, identity if kind == 'series' else identity[:40], entry.get('name') or entry.get('title') or entry.get('o_name'),
        members[0] if members else '', entry.get('container_extension') or client._command_extension(command),
        icon, entry.get('rating') or entry.get('rating_imdb') or entry.get('rating_kinopoisk'),
        entry.get('year') or entry.get('release_year') or entry.get('releasedate') or entry.get('releaseDate'),
        entry.get('tmdb_id') or entry.get('tmdbId') or entry.get('themoviedb_id'),
        entry.get('imdb_id') or entry.get('imdbId') or entry.get('imdb'), entry, client.account, selected, members)
    if kind == 'series':
        record.update(q=request_type if request_type in ('series', 'vod') else 'series', h='category', f='')
    else:
        record['q'] = _safe_portal_request_type(request_type)
        record['m'] = _safe_portal_command(command, client.account)
    return record

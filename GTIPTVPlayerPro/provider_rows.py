"""Keep list rows cheap; expand verbose provider text only for nearby items.

Identifiers, commands, dates and category membership stay untouched. Only
long descriptive strings use lossless compression, never whole row codecs.
"""
import sys
import zlib

_DETAIL_FIELDS = frozenset(('plot', 'description', 'overview', 'cast', 'actors',
                            'director', 'genre', 'genres_str'))
# json.loads shares object keys within one bulk reply. A streaming decoder
# has a fresh key memo per row, so retain only one copy of known schema keys.
# Unknown provider-controlled keys are deliberately not globally interned.
_FIELD_KEYS = {key: key for key in '''stream_id series_id id movie_id video_id
    file_id episode_id name title o_name category_id category_ids category
    genres genre genres_str stream_icon movie_image cover cover_big poster
    poster_path icon logo screenshot_uri container_extension rating rating_imdb
    rating_kinopoisk rating_5based year release_year releasedate releaseDate
    release_date first_air_date tmdb_id tmdbId themoviedb_id tmdb imdb_id imdbId
    imdb cmd command url added date_added added_at created_at created
    last_modified lastModified modified_at updated_at last_updated is_series
    series plot description overview cast actors director duration duration_secs
    time backdrop_path backdrop backdrop_url backdrop_image background
    background_url fanart fanart_url cover_landscape landscape hero hero_image
    banner season season_number episode episode_num censored hd
    '''.split()}


class _Text:
    __slots__ = ('data',)

    def __init__(self, data):
        self.data = data

    def __sizeof__(self):
        return object.__sizeof__(self) + sys.getsizeof(self.data)

    def text(self):
        return zlib.decompress(self.data).decode('utf-8', 'surrogatepass')


class ProviderRow(dict):
    def __getitem__(self, key):
        value = super().__getitem__(key)
        return value.text() if isinstance(value, _Text) else value

    def get(self, key, default=None):
        value = super().get(key, default)
        return value.text() if isinstance(value, _Text) else value


def retain(entry, shared_keys=False):
    if isinstance(entry, ProviderRow):
        return entry
    updates = {}
    for key in _DETAIL_FIELDS.intersection(entry):
        value = entry[key]
        if isinstance(value, str) and len(value) > 512:
            encoded = value.encode('utf-8', 'surrogatepass')
            if len(encoded) > 2 * 1024 * 1024:
                from .content import ContentError
                raise ContentError('Server content list is too large', ContentError.RESPONSE_TOO_LARGE)
            # Don't spend space on an incompressible description.
            compressed = zlib.compress(encoded, 1)
            if len(compressed) + 96 < sys.getsizeof(value):
                updates[key] = _Text(compressed)
    if shared_keys:
        # json.loads already shares keys throughout a response. Adopt its
        # dictionaries directly unless verbose text needs compression.
        if not updates: return entry
        result = ProviderRow(entry)
    elif _FIELD_KEYS.keys() >= entry.keys():
        result = dict(zip(map(sys.intern, entry), entry.values()))
    else:
        result = {_FIELD_KEYS.get(key, key): value for key, value in entry.items()}
    if updates:
        result = ProviderRow(result)
    result.update(updates)
    return result


def expanded(entry):
    return {key: entry[key] for key in entry}

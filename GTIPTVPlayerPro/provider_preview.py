"""Build a selected item's synopsis from its already loaded category row."""
from .content import MovieInfo, SeriesInfo, _normalise_media_url


def catalogue_info(client, kind, item):
    entry = getattr(item, '_catalogue_entry', None)
    if not isinstance(entry, dict):
        return None
    def first(*keys):
        for key in keys:
            value = entry.get(key)
            if value not in (None, '', [], {}):
                return value
        return ''
    if kind == 'series' and callable(getattr(client, '_series_info_from_entry', None)):
        return client._series_info_from_entry(entry, [])
    cls = SeriesInfo if kind == 'series' else MovieInfo
    result = cls(title=first('name', 'title', 'o_name') or item.name,
        plot=first('plot', 'description', 'overview'),
        rating=first('rating', 'rating_imdb', 'rating_kinopoisk', 'rating_5based') or getattr(item, 'rating', ''),
        year=first('year', 'release_year', 'releasedate', 'releaseDate') or getattr(item, 'year', ''),
        genre=first('genres_str', 'genre'), director=first('director'),
        cast=first('actors', 'cast'), cover=getattr(item, 'icon', ''),
        backdrop=_normalise_media_url(first('backdrop_path', 'backdrop', 'backdrop_url',
            'backdrop_image', 'background', 'background_url', 'fanart', 'fanart_url',
            'cover_landscape', 'landscape', 'hero', 'hero_image', 'banner'), client.account),
        tmdb_id=getattr(item, 'tmdb_id', ''), imdb_id=getattr(item, 'imdb_id', ''))

    if kind == 'movie':
        result.duration = str(first('duration', 'time', 'duration_secs') or '')
    return result

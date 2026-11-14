"""Exercise discovery against small, offline TMDb and provider fixtures."""
import ast
from contextlib import nullcontext
from gettext import GNUTranslations
from io import BytesIO
import importlib
import inspect
import json
from pathlib import Path
import sys
import threading
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlsplit
from xml.etree import ElementTree

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
if sys.platform == 'win32':
    sys.modules.setdefault('fcntl', SimpleNamespace(LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=lambda *args: None))

from GTIPTVPlayerPro import discovery
from GTIPTVPlayerPro.category_visibility import CategoryVisibility
from GTIPTVPlayerPro.content import ContentItem, XtreamContentClient
from GTIPTVPlayerPro.memory_catalog import budget
from GTIPTVPlayerPro.movie_catalog import SeriesCatalogIndex
from GTIPTVPlayerPro.playlist import parse_playlist_line


SHOW = dict(id=541, title='Karanlık', original_title='Dark', year='2017', media_type='tv')
TMDB_SHOW = dict(id=541, name='Karanlık', original_name='Dark', first_air_date='2017-12-01',
                 original_language='de', poster_path='/poster.jpg', backdrop_path='/hero.jpg', vote_average=8.4)
TMDB_MOVIE = dict(id=541, title='Orman', original_title='Forest', release_date='2020-01-01',
                  poster_path='/film.jpg', backdrop_path='/forest.jpg')


def sample_series(identity, name, category='1', **fields):
    return dict(series_id=identity, name=name, category_id=category, **fields)


class DiscoveryClientTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.responses = {}

        def opener(request, timeout):
            url = urlsplit(request.full_url)
            params = parse_qs(url.query)
            path = url.path.removeprefix('/3')
            language = params['language'][0]
            self.calls.append((path, language, params))
            result = self.responses.get((path, language), self.responses.get(path))
            self.assertIsNotNone(result, (path, language))
            if callable(result):
                result = result(params)
            return BytesIO(json.dumps(result).encode())

        discovery._CACHE.clear()
        self.client = discovery.TMDbDiscoveryClient(api_key='fixture-key', language='tr-TR', opener=opener,
                                                    backdrop_size='w780')

    def test_both_media_types_have_separate_paths_and_cached_records(self):
        for path in ('/tv/popular', '/tv/top_rated', '/tv/on_the_air', '/discover/tv', '/tv/541/recommendations'):
            self.responses[path] = dict(results=[TMDB_SHOW, TMDB_SHOW, dict(TMDB_SHOW, id=42, adult=True)], total_pages=2)
        for section in ('popular', 'top', 'new', 'genres', 'similar'):
            page = self.client.titles('tv', section, 1, 18, 541)
            self.assertEqual(len(page['entries']), 1)
            self.assertEqual(page['entries'][0]['year'], '2017')
            self.assertIn('/w780/', page['entries'][0]['backdrop'])
        self.assertEqual(len(self.calls), 5)
        self.assertEqual(self.calls[3][2]['with_genres'], ['18'])
        self.responses['/movie/popular'] = dict(results=[TMDB_MOVIE])
        self.assertEqual(self.client.movies()['entries'][0]['title'], 'Orman')
        self.assertEqual(self.client.titles('tv')['entries'][0]['title'], 'Karanlık')
        self.assertEqual(len(self.calls), 6)
        self.responses['/genre/tv/list'] = dict(genres=[dict(id=18, name='Dram')])
        self.assertEqual(self.client.genres(media_type='tv'), [('Dram', 18)])

    def test_detail_reuses_list_art_and_trailer_falls_back_to_original_language(self):
        self.responses['/tv/541'] = dict(TMDB_SHOW, backdrop_path=None, overview='Özet', number_of_seasons=3,
                                         created_by=[dict(name='Yapımcı')])
        detail = self.client.detail(541, media_type='tv', listing={'backdrop': 'https://example.test/hero.jpg'})
        self.assertEqual((detail['seasons'], detail['creators'], detail['backdrop']),
                         (3, 'Yapımcı', 'https://example.test/hero.jpg'))
        self.assertEqual(len(self.calls), 1)
        self.responses['/tv/541/videos', 'tr-TR'] = dict(results=[dict(type='Trailer', site='Vimeo', key='abcdefghijk')])
        self.responses['/tv/541/videos', 'de'] = dict(results=[
            dict(type='Teaser', site='YouTube', key='abcdefghijk', official=True),
            dict(type='Trailer', site='YouTube', key='12345678901', official=True),
            dict(type='Trailer', site='YouTube', key='invalid', official=True),
        ])
        self.assertEqual(self.client.trailer(541, media_type='tv', original_language='de'), '12345678901')
        self.assertEqual([call[:2] for call in self.calls[-2:]], [('/tv/541/videos', 'tr-TR'), ('/tv/541/videos', 'de')])
        self.responses['/movie/541/videos'] = dict(results=[dict(type='Trailer', site='YouTube', key='ABCDEFGHIJK')])
        self.assertEqual(self.client.trailer(541), 'ABCDEFGHIJK')


class DiscoveryProviderTests(unittest.TestCase):
    def setUp(self):
        self.account = parse_playlist_line('http://example.invalid/get.php?username=demo&password=fixture&type=m3u_plus')
        free = patch('GTIPTVPlayerPro.memory_catalog.available_memory', return_value=1024**3)
        free.start()
        self.addCleanup(free.stop)
        self.visibility = CategoryVisibility({'2'}, {'1'}, {'1', '2'}, ((), ()))

    def verify_matches(self, client, expected, plain):
        index = SeriesCatalogIndex(client, directory='memory-fixture')
        index.category_list = plain
        try:
            state = index.prepare()
            self.assertTrue(state['complete'], state)
            lookup = discovery.series_lookup(SHOW)
            filtered = dict(hidden_category_ids=self.visibility, discovery=lookup)
            first = index.search('Karanlık', page_size=2, **filtered)
            self.assertEqual([item.stream_id for item in first.items], expected[:2])
            self.assertEqual(first.total_items, len(expected))
            complete = index.search_all('Karanlık', **filtered)
            try:
                self.assertTrue(complete.items.full_search)
                self.assertEqual([item.stream_id for item in complete.items], expected)
                for item in complete.items:
                    self.assertEqual(item.content_type, 'series')
            finally:
                complete.items.close()
            unfiltered = index.search('Karanlık', discovery=lookup)
            self.assertEqual({item.stream_id for item in unfiltered.items}, set(expected) | {'14'})
            complete = index.search_all('Karanlık', discovery=lookup)
            try:
                self.assertEqual([item.stream_id for item in complete.items],
                                 [item.stream_id for item in unfiltered.items])
            finally:
                complete.items.close()
            edited = index.search('Private')
            self.assertEqual(len(edited.items), 1)
        finally:
            index.close()
        self.assertEqual(budget.used, 0)

    def test_uses_native_xtream_rows_without_requesting_details_for_candidates(self):
        rows = [sample_series('10', 'Darkness'), sample_series('11', 'DE | Dark (2021)', year=2021),
                sample_series('12', 'TR: Karanlık (2017)', year=2017), sample_series('13', 'Different title', tmdb=541),
                sample_series('14', 'Private show', '2', tmdb_id='541'), sample_series('15', 'Dark FHD', year=2017)]
        calls = []

        def opener(request, timeout):
            action = parse_qs(urlsplit(request.full_url).query)['action'][0]
            calls.append(action)
            if action == 'get_series':
                return BytesIO(json.dumps(rows).encode())
            self.assertEqual(action, 'get_series_info')
            self.assertEqual(parse_qs(urlsplit(request.full_url).query)['series_id'], ['13'])
            return BytesIO(json.dumps(dict(info=dict(name='Different title'), episodes={'2': [
                dict(id='100', title='Episode 1', container_extension='mp4', season=2, episode_num=1)
            ]})).encode())

        client = XtreamContentClient(self.account, opener=opener)
        self.verify_matches(client, ['13', '12', '15', '11'], True)
        self.assertEqual(calls, ['get_series'])
        episodes = client.load_episodes('13')
        self.assertEqual([(episode.stream_id, episode.season) for episode in episodes], [('100', '2')])

    def test_full_search_runs_after_a_provider_catalog_without_membership_filters(self):
        client = XtreamContentClient(self.account, opener=lambda *args, **kwargs:
                                     BytesIO(json.dumps([sample_series(13, 'Karanlık', tmdb_id=541)]).encode()))
        index = SeriesCatalogIndex(client, directory='search-worker-fixture')
        index.category_list = True
        self.addCleanup(index.close)
        self.assertTrue(index.prepare()['complete'])
        source = Path(discovery.__file__).with_name('browser_vod.py')
        tree = ast.parse(source.read_bytes())
        base = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'GTAllMoviesSearchScreen')
        names = ('_search_page_worker', '_release_search_page', '_queue_search_event', '_provider_worker_finished')
        namespace = dict(_movie_search_text=discovery._movie_search_text)
        methods = [node for node in base.body if isinstance(node, ast.FunctionDef) and node.name in names]
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
        attrs = {name: namespace[name] for name in names}
        attrs['_release_search_page'] = staticmethod(attrs['_release_search_page'])
        Screen = type('GTAllMoviesSearchScreen', (), attrs)
        namespace['GTAllMoviesSearchScreen'] = Screen
        screen = Screen()
        screen._closed = False
        screen._catalog_mode = True
        screen._catalog_preparing = False
        screen._catalog_index = index
        screen._hidden_category_ids = frozenset()
        screen._event_lock = threading.Lock()
        screen._events = []
        screen._search_generation = 1
        screen._provider_worker_lock = threading.Lock()
        screen._provider_worker_count = 1
        screen._provider_workers_idle = threading.Event()
        screen._discovery_series_lookup = discovery.series_lookup(SHOW)
        screen._search_page_worker(1, 'Karanlık', 1, SimpleNamespace(check=lambda: None))
        self.assertTrue(screen._provider_workers_idle.is_set())
        self.assertEqual(len(screen._events), 1)
        result = screen._events[0]
        self.assertEqual(result[:3], ('page', 1, 1))
        self.addCleanup(result[3].items.close)
        self.assertEqual(result[3].items[0].stream_id, '13')
        screen.__dict__.clear()
        screen._queue_search_event(result)
        screen._provider_worker_finished()
        self.assertTrue(result[3].items.closed)

    def test_compact_catalog_also_matches_and_keeps_stalker_provider_ids(self):
        class PortalFixture:
            source_type = 'stalker'

            def __init__(self):
                self.account = SimpleNamespace(identity=('http', 'portal.invalid', 'demo'), source_type='stalker')

            def load_categories(self, content_type):
                return [SimpleNamespace(category_id='1', name='Visible'), SimpleNamespace(category_id='2', name='Hidden')]

            def request_scope(self, **kwargs):
                return nullcontext()

            def remember_catalog_item(self, item):
                pass

            def iter_series_catalog_items(self, category=''):
                for identity, name, tmdb, year, member in (
                    ('10', 'Darkness', '', '', '1'), ('11', 'Dark (2021)', '', '2021', '1'),
                    ('12', 'Karanlık (2017)', '', '2017', '1'), ('13', 'Alternate title', '541', '', '1'),
                    ('14', 'Private show', '541', '', '2'), ('15', 'Dark FHD', '', '2017', '1'),
                ):
                    if not category or category == member:
                        item = ContentItem('series', identity, name, category_id=member, year=year, tmdb_id=tmdb)
                        item.portal_request_type = 'vod'
                        yield item

        self.verify_matches(PortalFixture(), ['13', '12', '15', '11'], False)

    def test_return_from_episodes_never_renders_a_replaced_catalog(self):
        records = [sample_series(19, 'Karanlık', tmdb_id='541')]
        client = XtreamContentClient(self.account, opener=lambda *args, **kwargs:
                                     BytesIO(json.dumps(records).encode()))
        index = SeriesCatalogIndex(client, directory='episode-return-fixture')
        index.category_list = True
        self.addCleanup(index.close)
        source = Path(discovery.__file__).with_name('browser_vod.py')
        tree = ast.parse(source.read_bytes())
        base, series = (next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == name)
                        for name in ('GTAllMoviesSearchScreen', 'GTAllSeriesSearchScreen'))
        names = ('_release_search_results', '_discard_stale_catalog_results', '_series_episode_closed')
        methods = [node for cls in (base, series) for node in cls.body
                   if isinstance(node, ast.FunctionDef) and node.name in names]
        namespace = {}
        exec(compile(ast.Module(body=methods, type_ignores=[]), str(source), 'exec'), namespace)
        screen = type('SearchFixture', (), {name: namespace[name] for name in names})()
        screen._closed = False
        screen._query_key = 'karanlik'
        screen._page_cache = {}
        screen._reload_favorite_keys = Mock()
        screen._poll_timer = Mock()
        screen._render = Mock()

        self.assertTrue(index.prepare()['complete'])
        screen._results = index.search_all('Karanlık', discovery=discovery.series_lookup(SHOW)).items
        self.addCleanup(screen._results.close)
        screen._catalog_page_complete = True
        screen._start_catalog = Mock()
        screen._series_episode_closed()
        screen._start_catalog.assert_not_called()
        self.assertEqual(screen._results[0].stream_id, '19')

        screen._catalog_page_complete = False

        def rebuild():
            self.assertEqual(screen._results, [])
            self.assertTrue(index.prepare(force=True)['complete'])

        screen._start_catalog = Mock(side_effect=rebuild)
        screen._series_episode_closed()
        screen._start_catalog.assert_called_once()
        self.assertTrue(screen._catalog_refresh_pending)
        self.assertEqual(screen._results, [])

        screen._results = index.search_all('Karanlık', discovery=discovery.series_lookup(SHOW)).items
        self.addCleanup(screen._results.close)
        self.assertTrue(index.prepare(force=True)['complete'])
        self.assertTrue(screen._discard_stale_catalog_results())
        self.assertEqual(screen._results, [])


class TrailerPlayerTests(unittest.TestCase):
    def test_falls_back_to_native_http_service_when_serviceapp_is_absent(self):
        from GTIPTVPlayerPro import web_youtube
        from GTIPTVPlayerPro.youtube_vendor import video_url

        for preferred, native_available, registered, expected in (
            (5002, False, (4097,), 4097), (5002, True, (4097, 5002), 5002),
            (5001, False, (4097,), 4097), (4097, False, (4097,), 4097), (1, False, (4097,), 4097),
        ):
            with self.subTest(preferred=preferred, native_available=native_available):
                player = SimpleNamespace(movie_service_type=preferred, youtube_resolution='1080', youtube_dash=True,
                                         youtube_stream_mode='auto', youtube_audio_preference='original')
                receiver = SimpleNamespace(current_dialog=None, nav=Mock())
                owner = SimpleNamespace(_closed=False)
                receiver.current_dialog = owner
                history = Mock()
                service = web_youtube.WebYouTube(history=Mock(), playback_history=history)
                monitor = SimpleNamespace(done=threading.Event(), outcome='playing', confirmation='fixture', close=Mock())
                monitor.done.set()
                stream = 'https://r1.googlevideo.com/fixture'
                native_url = 'http://127.0.0.1:12345/youtube/video'
                resolver = SimpleNamespace(extract=lambda video_id: stream, selected_quality=720,
                    selected_video_codec='h264', duration_seconds=90, selected_video_itag='22',
                    selected_audio_itag='', selected_audio_bitrate=0)
                reference = Mock()
                playback = object()
                closed = Mock()

                def open_player(callback, screen, ref, item, **options):
                    self.assertIs(callback, closed)
                    self.assertIs(screen, playback)
                    self.assertIs(ref, reference)
                    self.assertEqual(options, {})
                    self.assertTrue(item.youtube_native)
                    return SimpleNamespace(onClose=[])

                receiver.openWithCallback = Mock(side_effect=open_player)
                reference_factory = Mock(return_value=reference)
                # Run only this fixture's worker synchronously. Its GUI adapter
                # uses inline calls and its startup monitor is already settled.
                inline_thread = SimpleNamespace(Thread=lambda target, **kwargs: SimpleNamespace(start=target))
                with (patch.object(web_youtube, 'threading', inline_thread),
                      patch.object(web_youtube, 'load_player_settings', return_value=player),
                      patch.object(web_youtube, 'registered_youtube_services', return_value=registered),
                      patch.object(web_youtube, 'probe_native_player', return_value=dict(available=native_available,
                                   version=1 if native_available else 0, reason='fixture', separate_audio=native_available)),
                      patch.object(web_youtube, 'receiver_youtube_codecs', return_value=frozenset({'h264'})),
                      patch.object(web_youtube, 'native_youtube_service_url', return_value=native_url),
                      patch.object(web_youtube, 'prepare_youtube_decoder_position', return_value=None),
                      patch.object(web_youtube, 'WEB_REMOTE', SimpleNamespace(session=receiver, _run_on_gui=lambda fn, **kw: fn())),
                      patch.object(service, '_monitor_youtube_start', return_value=monitor),
                      patch.object(service, '_finish_youtube_start', return_value=True),
                      patch.object(video_url, 'YouTubeVideoUrl', return_value=resolver),
                      patch.dict(sys.modules, {
                          'enigma': module('enigma', eServiceReference=reference_factory),
                          'GTIPTVPlayerPro.browser': module('GTIPTVPlayerPro.browser', GTExternalPlayerScreen=playback),
                      })):
                    request = service.play('abcdefghijk', 'Trailer fixture', _request_owner=owner, _on_closed=closed,
                                           _original_audio=True, _trailer=True)
                self.assertEqual(service._jobs[request['token']]['state'], 'playing')
                reference_factory.assert_called_once_with(expected, 0, native_url if expected == 5002 else stream)
                receiver.openWithCallback.assert_called_once()
                history.add.assert_not_called()


class Widget:
    def __init__(self, text=''):
        self.text, self.visible, self.instance = text, True, None

    def show(self): self.visible = True
    def hide(self): self.visible = False
    def getText(self): return self.text
    def setText(self, value): self.text = value


class Timer:
    def __init__(self): self.callback, self.pending = None, False
    def start(self, unused_delay, unused_single=True): self.pending = True
    def stop(self): self.pending = False

    def fire(self):
        if self.pending:
            self.stop()
            self.callback()


class Screen(dict):
    def __init__(self, session):
        super().__init__()
        self.session = session
        self.onShown, self.onLayoutFinish, self.onClose = [], [], []

    def setTitle(self, title): self.title = title

    def close(self):
        for callback in self.onClose:
            callback()


class Session:
    def __init__(self): self.current_dialog, self.opened = None, []

    def openWithCallback(self, callback, screen, *args, **kwargs):
        self.opened.append((screen, args, kwargs, callback, self.current_dialog))
        self.current_dialog = object()
        return self.current_dialog

    def return_from_child(self, *values):
        *unused, callback, parent = self.opened[-1]
        self.current_dialog = parent
        callback(*values)


def module(name, **values):
    result = ModuleType(name)
    result.__dict__.update(values)
    return result


class DiscoveryScreenTests(unittest.TestCase):
    def setUp(self):
        package = 'GTIPTVPlayerPro'
        self.article = type('MovieInfo', (), {})
        self.movie_search = type('MovieSearch', (), {})
        self.series_search = type('SeriesSearch', (), {})
        self.choices = type('ChoiceBox', (), {})
        self.message = type('MessageBox', (), {'TYPE_INFO': 0})
        self.loaded = []
        self.decode = []
        self.cache = {}
        self.youtube_calls = []
        self.session = Session()
        self.youtube = SimpleNamespace(play=self.play, status=lambda token: {'job': {'state': 'resolving'}},
                                       cancel_play=lambda token: self.youtube_calls.append(('cancel', token)))
        self.web_remote = SimpleNamespace(session=None)
        self.web_remote.bind_session = lambda session: setattr(self.web_remote, 'session', session)
        small = dict(
            ActionMap=lambda contexts, actions, priority: SimpleNamespace(contexts=contexts, actions=actions),
            Label=Widget, Pixmap=Widget, ChoiceBox=self.choices, MessageBox=self.message, Screen=Screen,
        )
        native = {group+'.'+name: module(group+'.'+name, **{name: small[name]}) for group, names in (
            ('Components', ('ActionMap', 'Label', 'Pixmap')), ('Screens', ('ChoiceBox', 'MessageBox', 'Screen'))
        ) for name in names}
        native['enigma'] = module('enigma', eTimer=Timer)
        native[package+'.main'] = module(package+'.main', _scale=lambda: self.scale(1920, 1080),
            _connect_timer=lambda timer, callback: setattr(timer, 'callback', callback), GTFeatureScreen=self.article)
        # The real worker token gives the same deadline/cancel behavior to the UI fixture.
        imports = {
            'browser': dict(_WorkerToken=self.token_type(), _WorkerCancelled=Cancelled, PICON_DOWNLOAD_TIMEOUT=1,
                _cached_picon_path=self.cache.get, _cinematic_cover_ratio=lambda path: (1280, 720),
                _valid_cinematic_backdrop_file=bool, _client_supports=lambda client, kind: client.supports(kind),
                _download_picon=lambda *args, **kwargs: args[1], _picon_cache_path=lambda url: url, content_client_for=None),
            'background': dict(attach_pixmap=self.attach),
            'browser_vod': dict(GTAllMoviesSearchScreen=self.movie_search, GTAllSeriesSearchScreen=self.series_search),
            'series_trailer_ui': dict(PLAY_KEY_CONTEXT='GTIPTVPlayerProSeriesTrailerActions', _ensure_play_keys=lambda: None),
            'web_remote': dict(WEB_REMOTE=self.web_remote), 'web_youtube': dict(WEB_YOUTUBE=self.youtube),
        }
        native.update({package+'.'+name: module(package+'.'+name, **values) for name, values in imports.items()})
        mock = patch.dict(sys.modules, native)
        mock.start()
        self.addCleanup(mock.stop)
        self.ui = importlib.import_module(package+'.discovery_ui')
        self.screen = self.ui.GTMovieDiscoveryScreen(self.session, object(), SimpleNamespace(supports=lambda kind: True))
        self.session.current_dialog = self.screen
        self.screen._client = SimpleNamespace(available=True, request_scope=lambda **kwargs: nullcontext())
        for callback in self.screen.onLayoutFinish:
            self.assertTrue(inspect.ismethod(callback))
            callback()
        self.real_submit, self.screen._submit = self.screen._submit, self.record_job
        self.addCleanup(lambda: self.screen.close() if getattr(self.screen, 'onClose', ()) else None)

    @staticmethod
    def scale(width, height):
        factor = min(width / 1920, height / 1080)
        return width, height, lambda value: max(1, round(value * factor))

    @staticmethod
    def token_type():
        class Token:
            def __init__(self, seconds): self.deadline, self.cancel_event = time.monotonic()+seconds, threading.Event()
            def active(self): return not self.expired()
            def expired(self): return self.cancel_event.is_set() or time.monotonic() >= self.deadline
            def cancel(self): self.cancel_event.set()
            def check(self):
                if not self.active(): raise Cancelled()
        return Token

    def attach(self, screen, name, path, **kwargs):
        self.decode.append((name, path, kwargs['on_loaded']))

    def record_job(self, channel, work, on_done, timeout=25):
        self.screen._cancel(channel)
        token = self.token_type()(timeout)
        self.screen._jobs[channel] = (token, on_done)
        self.loaded.append((channel, work))

    def finish_job(self, channel, result):
        unused, callback = self.screen._jobs.pop(channel)
        callback(result)

    def play(self, video_id, title, **kwargs):
        self.youtube_calls.append((video_id, title, kwargs))
        return {'token': 'fixture', 'state': 'resolving'}

    def initial(self, kind):
        data = dict(SHOW if kind == 'tv' else {'id': 541, 'media_type': 'movie', 'title': 'Forest'})
        data.update(poster=kind+' poster', backdrop=kind+' hero', rating=8.4, year='2017')
        self.screen._loaded(dict(entries=[data], page=1, pages=1))
        return data

    def test_cached_art_then_details_and_stale_film_decode_after_switch_to_series(self):
        self.cache.update({'movie hero': 'cached-movie-hero', 'movie poster': 'cached-movie-poster',
                           'tv hero': 'cached-tv-hero', 'tv poster': 'cached-tv-poster'})
        self.initial('movie')
        self.assertEqual([data[1] for data in self.decode], ['cached-movie-hero', 'cached-movie-poster'])
        self.assertEqual(self.loaded, [])
        movie_hero, movie_poster = self.decode
        self.screen.focus_tabs(True)
        self.screen.move(1)
        self.screen.open_selected()
        self.assertEqual(self.screen._media_type, 'tv')
        self.finish_job('list', dict(entries=[], page=1, pages=1))
        self.initial('tv')
        movie_hero[2](True)
        movie_poster[2](True)
        self.assertFalse(self.screen['backdrop'].visible)
        self.assertFalse(self.screen['poster_0'].visible)
        self.decode[-2][2](True)
        self.decode[-1][2](True)
        self.assertTrue(self.screen['backdrop'].visible)
        self.assertTrue(self.screen['poster_0'].visible)
        self.screen._detail_timer.fire()
        self.assertEqual(self.loaded[-1][0], 'detail')

    def test_series_iptv_handoff_and_trailer_use_receiver_screen(self):
        self.screen._change_media('tv')
        self.finish_job('list', dict(entries=[], page=1, pages=1))
        title = self.initial('tv')
        self.screen.search_iptv()
        self.finish_job('search', {'2'})
        screen, args, options, *unused = self.session.opened[-1]
        self.assertIs(screen, self.series_search)
        self.assertIs(args[1], self.screen._provider)
        self.assertEqual(options, dict(hidden_category_ids={'2'}, initial_query='Karanlık', discovery_title=title))
        self.session.return_from_child()
        self.screen.open_selected()
        self.assertIs(self.session.opened[-1][0], self.article)
        self.session.return_from_child()
        self.screen['trailer_actions'].actions['playTrailer']()
        self.finish_job('trailer', 'abcdefghijk')
        self.assertEqual(self.youtube_calls[0][:2], ('abcdefghijk', 'Karanlık — Trailer'))
        video_options = self.youtube_calls[0][2]
        self.assertTrue(video_options['_trailer'])
        self.assertIs(video_options['_request_owner'], self.screen)
        video_options['_on_closed']()
        self.assertFalse(self.screen._busy)
        self.assertEqual(self.screen._selected(), title)

    def test_latest_list_request_wins_with_timer_delivery(self):
        self.screen._submit = self.real_submit
        entered, release, complete = (threading.Event() for _ in range(3))
        results = []

        def old_work(token):
            entered.set()
            release.wait(2)
            return 'stale'

        def new_work(token):
            complete.set()
            return 'fresh'

        try:
            self.screen._submit('list', old_work, results.append)
            self.assertTrue(entered.wait(2))
            self.screen._submit('list', new_work, results.append)
        finally:
            release.set()
        self.assertTrue(complete.wait(2))
        deadline = time.monotonic() + 2
        while not results and time.monotonic() < deadline:
            self.screen._poll()
            time.sleep(0.005)
        self.assertEqual(results, ['fresh'])

    def test_native_screen_destruction_tolerates_late_callbacks(self):
        self.cache.update({'movie hero': 'cached-hero', 'movie poster': 'cached-poster'})
        self.initial('movie')
        self.screen.open_menu()
        unused, unused_args, unused_kwargs, closed, parent = self.session.opened[-1]
        self.assertIs(parent, self.screen)
        self.assertTrue(self.screen._child_open)
        self.screen.close()
        self.screen.__dict__.clear()
        self.screen.clear()
        closed(('Cancel', 'back'))
        self.screen._trailer_closed()
        self.screen._poll()
        self.screen._begin()
        self.screen._start_selected_hero()
        self.screen._load_selected_detail()
        self.screen._queue(('done', 'list', self.token_type()(1), None, ''))
        for _, _, decoded in self.decode:
            decoded(True)

    def test_rejected_native_child_open_does_not_lock_discovery(self):
        self.session.openWithCallback = Mock(return_value='config.crash.bsodpython.value=True')
        self.screen.open_menu()
        self.assertFalse(self.screen._child_open)
        self.assertEqual(self.screen['status'].getText(), 'Could not open the content list.')

    def test_skin_accepts_every_osd_and_catalog(self):
        catalogs = list(Path(discovery.__file__).parent.glob('locale/*/LC_MESSAGES/*.mo'))
        self.assertEqual(len(catalogs), 43)
        for path in catalogs:
            translator = GNUTranslations(BytesIO(path.read_bytes()))
            from GTIPTVPlayerPro import remote_footer
            for width, height in ((640, 480), (720, 480), (720, 576), (1280, 720), (1920, 1080), (3840, 2160)):
                with (patch.object(self.ui, '_scale', return_value=self.scale(width, height)),
                      patch.object(self.ui, '_', translator.gettext),
                      patch.object(remote_footer, '_', translator.gettext)):
                    skin = ElementTree.fromstring(self.ui._skin())
                names = [item.get('name') for item in skin]
                self.assertEqual(len(set(names)), len(names))
                self.assertTrue(set(names).issubset(self.screen))
                for item in skin:
                    x, y = map(int, item.get('position').split(','))
                    w, h = map(int, item.get('size').split(','))
                    self.assertTrue(0 <= x < x+w <= width and 0 <= y < y+h <= height,
                                    (path.parent.parent.name, width, height, item.attrib))


class Cancelled(Exception):
    pass


if __name__ == '__main__':
    unittest.main()

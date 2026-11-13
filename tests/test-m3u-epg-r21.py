"""M3U timing, XMLTV transport, name matching and receiver menu regressions."""

import ast
from contextlib import nullcontext
from gettext import GNUTranslations
import gzip
import http.client
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import Mock, patch


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'GTIPTVPlayerPro'
sys.path.insert(0, str(ROOT))
if os.name == 'nt':
    sys.modules.setdefault('fcntl', types.ModuleType('fcntl'))

from GTIPTVPlayerPro import i18n, m3u, m3u_epg, m3u_epg_scheduler, m3u_epg_settings


SOURCE = m3u.M3USource('test-r21', 'Test', 'url', 'https://example.org/list.m3u')
M3U = b'#EXTM3U\n#EXTINF:-1,Das Erste HD\nhttps://example.org/live\n'
XML = b'''<?xml version="1.0"?><tv>
<channel id="one.de"><display-name>Das Erste</display-name></channel>
<channel id="duplicate.a"><display-name>Duplicate</display-name></channel>
<channel id="duplicate.b"><display-name>Duplicate</display-name></channel>
<channel id="explicit"><display-name>Another name</display-name></channel>
<channel id="cyr"><display-name>&#1050;&#1072;&#1085;&#1072;&#1083; 1</display-name></channel>
<programme channel="one.de" start="20261112100000 +0000" stop="20261112110000 +0000"><title>News</title></programme>
</tv>'''
NOW = 1794479400  # 2026-11-12 10:30 UTC


def make_client(**kwargs):
    return m3u.M3UContentClient(
        SOURCE, cache_dir='', registry_path='', epg_store=object(),
        name_store=types.SimpleNamespace(load=lambda scope: types.SimpleNamespace(names={})), **kwargs,
    )


def http_response(body, headers):
    buffer = io.BytesIO(b'HTTP/1.1 200 OK\r\nConnection: close\r\n' + headers + b'\r\n\r\n' + body)
    buffer._sock = types.SimpleNamespace(settimeout=lambda seconds: None)
    response = http.client.HTTPResponse(types.SimpleNamespace(makefile=lambda *args: buffer))
    response.begin()
    return response


class TransportTests(unittest.TestCase):
    def test_progressing_download_can_cross_the_previous_deadline(self):
        clock = types.SimpleNamespace(seconds=0)

        def fetch(url, **kwargs):
            self.assertEqual(kwargs['timeout'], m3u.M3U_NETWORK_IDLE_TIMEOUT)
            clock.seconds = 37
            return M3U

        client = make_client(fetcher=fetch, clock=lambda: clock.seconds)
        with client.request_scope(deadline=m3u.M3U_REMOTE_PREPARE_TIMEOUT):
            self.assertEqual(client.prepare()['channels'], 1)

    def test_remote_m3u_connection_uses_the_longer_budget_before_browsing(self):
        tree = ast.parse((PLUGIN / 'main.py').read_bytes())
        connection = next(node for node in tree.body if isinstance(node, ast.ClassDef)
                          and node.name == '_DirectAccountConnection')

        class Job:
            def __init__(self, timeout):
                self.timeout = timeout
                self.cancel_event = object()

            def check(self):
                pass

            def active(self):
                return True

        checker = Mock(return_value=types.SimpleNamespace(status='ONLINE'))
        worker = lambda target: types.SimpleNamespace(start=target)
        namespace = {'__package__': 'GTIPTVPlayerPro', '_source_type': lambda account: account.source_type,
                     'eTimer': Mock(), '_connect_timer': Mock(), '_UIJobToken': Job, '_cancel_ui_job': Mock(),
                     'threading': types.SimpleNamespace(Thread=worker), 'UI_NETWORK_JOB_TIMEOUT_SECONDS': 30,
                     'check_source_health': checker}
        exec(compile(ast.Module(body=[connection], type_ignores=[]), 'main.py', 'exec'), namespace)
        result = namespace['_DirectAccountConnection'](None, SOURCE)
        result.start()
        self.assertGreater(result._job.timeout, m3u.M3U_REMOTE_PREPARE_TIMEOUT)
        checker.assert_called_once_with(SOURCE, cancel_event=result._job.cancel_event,
                                        timeout=m3u.M3U_REMOTE_PREPARE_TIMEOUT)

    def test_download_and_parse_have_independent_deadlines(self):
        clock = types.SimpleNamespace(seconds=0)

        def stalled(url, **kwargs):
            clock.seconds = 91
            return M3U

        client = make_client(fetcher=stalled, clock=lambda: clock.seconds)
        with self.assertRaises(m3u.ContentError) as raised:
            client.prepare()
        self.assertEqual(raised.exception.reason, m3u.ContentError.DEADLINE)

        clock.seconds = 0
        parser = m3u._parse_m3u

        def slow_catalog(data, source, deadline, check):
            clock.seconds += 31
            return parser(data, source, deadline, check)

        client = make_client(fetcher=lambda *args, **kwargs: M3U, clock=lambda: clock.seconds)
        with patch.object(m3u, '_parse_m3u', slow_catalog), self.assertRaises(m3u.ContentError) as raised:
            client.prepare()
        self.assertEqual(raised.exception.reason, m3u.ContentError.DEADLINE)

    def test_complete_http_body_is_accepted_after_socket_closes(self):
        payload = gzip.compress(XML)
        chunked = b'%x\r\n' % len(payload) + payload + b'\r\n0\r\n\r\n'
        cases = ((payload, b'Content-Length: %d' % len(payload)),
                 (chunked, b'Transfer-Encoding: chunked'))
        with tempfile.TemporaryDirectory(prefix='r21-epg-', dir=ROOT) as directory:
            self.assertEqual(Path(directory).resolve().parent, ROOT.resolve())
            for index, (body, headers) in enumerate(cases):
                path = Path(directory) / str(index)
                response = http_response(body, headers)
                shim = nullcontext() if hasattr(os, 'fchmod') else patch.object(os, 'fchmod', lambda *args: None, create=True)
                with response, shim:
                    result = m3u_epg._copy_http_response(response, types.SimpleNamespace(sock=None),
                                                         path, True, 5, None, time.monotonic() + 5)
                self.assertEqual(result, (True, len(payload)))
                self.assertEqual(path.read_bytes(), payload)

    def test_truncated_http_body_is_rejected(self):
        with tempfile.TemporaryDirectory(prefix='r21-epg-', dir=ROOT) as directory:
            self.assertEqual(Path(directory).resolve().parent, ROOT.resolve())
            path = Path(directory) / 'partial'
            response = http_response(b'123', b'Content-Length: 12')
            with response, self.assertRaises(http.client.IncompleteRead):
                m3u_epg._copy_http_response(response, types.SimpleNamespace(sock=None),
                                             path, False, 5, None, time.monotonic() + 5)
            self.assertFalse(path.exists())

    def test_dns_and_deadline_keep_distinct_reasons(self):
        def dns_failure(*args):
            raise socket.gaierror('fixture host unavailable')

        with self.assertRaises(m3u_epg.M3UEPGSourceError) as raised:
            m3u_epg._download_remote_source('https://example.org/epg.xml', None, 5, None,
                                            time.monotonic() + 5, dns_failure)
        self.assertEqual(raised.exception.reason, 'dns')
        with self.assertRaises(m3u_epg.M3UEPGCancelled) as raised:
            m3u_epg._check_limits(None, time.monotonic() - 1)
        self.assertEqual(raised.exception.reason, 'deadline')


class MatchingTests(unittest.TestCase):
    def test_scheduler_refreshes_a_playlist_with_only_channel_names(self):
        binding = m3u_epg_settings.Binding(SOURCE.source_id, enabled=True, kind='url',
                                            location='https://example.org/epg.xml')
        client = make_client(fetcher=lambda *args, **kwargs: M3U)
        scheduler = m3u_epg_scheduler.M3UEPGScheduler.__new__(m3u_epg_scheduler.M3UEPGScheduler)
        scheduler._owner = object()
        scheduler._store = object()
        scheduler._find_source = Mock(return_value=SOURCE)
        scheduler._persist_status = Mock(return_value=True)
        event = types.SimpleNamespace(is_set=lambda: False)
        with patch.multiple(m3u_epg_scheduler, M3UContentClient=Mock(return_value=client),
                            refresh_source=Mock(return_value={'matched_ids': 1, 'event_count': 1}),
                            acquire_runtime=Mock(return_value=True), release_runtime=Mock(), log_event=Mock()):
            self.assertEqual(scheduler._refresh_job(binding, event, time.monotonic() + 5), 'success')
            refresh = m3u_epg_scheduler.refresh_source
            self.assertEqual(refresh.call_args.args[2], frozenset())
            self.assertEqual(refresh.call_args.kwargs['wanted_names'], {m3u.epg_name_key('Das Erste HD'): 'Das Erste HD'})

    def test_explicit_ids_unique_names_and_manual_choices_are_cached(self):
        name = m3u.epg_name_key('Das Erste HD')
        duplicate = m3u.epg_name_key('Duplicate')
        cyrillic = m3u.epg_name_key('Канал 1 HD')
        names = {name: 'Das Erste HD', duplicate: 'Duplicate', cyrillic: 'Канал 1 HD'}
        fingerprint = 'a' * 64
        with tempfile.TemporaryDirectory(prefix='r21-epg-', dir=ROOT) as directory:
            self.assertEqual(Path(directory).resolve().parent, ROOT.resolve())
            source = Path(directory) / 'guide.xml'
            source.write_bytes(XML)

            def build(overrides=None):
                return m3u_epg._build_snapshot(SOURCE.source_id, fingerprint, source, ('explicit',),
                                               NOW, None, time.monotonic() + 5,
                                               wanted_names=names, name_mappings=overrides)

            automatic = build()
            self.assertEqual(dict(automatic['aliases']), {'explicit': 'explicit', name: 'one.de', cyrillic: 'cyr'})
            self.assertEqual(automatic['name_choices'], [[duplicate, 'Duplicate', ['duplicate.a', 'duplicate.b']]])
            store = m3u_epg.M3UEPGStore(cache_dir=directory)
            try:
                store.publish(build({duplicate: 'duplicate.b'}), persist=False)
                self.assertEqual(store.resolve_name(SOURCE.source_id, duplicate, fingerprint), 'duplicate.b')
                self.assertEqual(store.resolve_name(SOURCE.source_id, duplicate, 'b' * 64), '')
                self.assertEqual(store.lookup(SOURCE.source_id, name, now=NOW)[0].title, 'News')
                self.assertEqual(store.lookup(SOURCE.source_id, name, now=NOW, cache_only=True)[0].title, 'News')
                self.assertEqual(store.name_choices(SOURCE.source_id, fingerprint)[0][0], duplicate)
            finally:
                store.forget(SOURCE.source_id)

    def test_old_settings_and_manual_mapping_round_trip(self):
        binding = m3u_epg_settings.Binding(SOURCE.source_id, kind='url', location='https://example.org/epg.xml')
        old_data = binding.as_dict()
        del old_data['name_mappings']
        self.assertEqual(m3u_epg_settings.Binding.from_dict(old_data).name_mappings, {})
        key = m3u.epg_name_key('Duplicate')
        new = m3u_epg_settings.Binding.from_dict({**old_data, 'name_mappings': {key: 'duplicate.b'}})
        self.assertEqual(new.copy().name_mappings, {key: 'duplicate.b'})
        self.assertEqual(new.fingerprint, binding.fingerprint)

    def test_provider_names_resolve_when_an_id_is_missing_or_unmatched(self):
        playlist = b"""#EXTM3U
#EXTINF:-1 tvg-id="0",TR: TRT 1 FHD
https://example.org/trt1
#EXTINF:-1 tvg-id="0",TR: TV8.5 FHD
https://example.org/tv85
#EXTINF:-1 tvg-id="KanalD.tr",TR: TRT 1 HD
https://example.org/kanald
#EXTINF:-1,TR: Kanal D FHD
https://example.org/kanald2
#EXTINF:-1,DE: TV8.5 HD
https://example.org/regional
#EXTINF:-1,TR: Show TV +1 HD
https://example.org/timeshift
#EXTINF:-1,TR: STAR FHD
https://example.org/star
"""
        xmltv = b"""<tv>
<channel id="TRT1.tr"><display-name>TR - TRT1</display-name></channel>
<channel id="TV8,5.tr"><display-name>TR - TV 85</display-name></channel>
<channel id="KanalD.tr"><display-name>TR - Kanal D</display-name></channel>
<channel id="KanalDHD.tr"><display-name>TR - Kanal-D</display-name></channel>
<channel id="ShowTV.tr"><display-name>TR - Show TV</display-name></channel>
<channel id="StarTV.tr"><display-name>TR - Star TV</display-name></channel>
<programme channel="TRT1.tr" start="20261112100000 +0000" stop="20261112110000 +0000"><title>TRT</title></programme>
<programme channel="TV8,5.tr" start="20261112100000 +0000" stop="20261112110000 +0000"><title>TV8.5</title></programme>
</tv>"""
        fingerprint = 'a' * 64
        with tempfile.TemporaryDirectory(prefix='epg-names-', dir=ROOT) as directory:
            source = Path(directory) / 'guide.xml'
            source.write_bytes(xmltv)
            store = m3u_epg.M3UEPGStore(cache_dir=directory)
            client = m3u.M3UContentClient(
                SOURCE, cache_dir='', registry_path='', epg_store=store,
                name_store=types.SimpleNamespace(load=lambda scope: types.SimpleNamespace(names={})),
                fetcher=lambda *args, **kwargs: playlist, wall_clock=lambda: NOW,
            )
            client.prepare()
            try:
                result = m3u_epg._build_snapshot(
                    SOURCE.source_id, fingerprint, source, client.epg_channel_ids(),
                    NOW, None, time.monotonic() + 5, wanted_names=client.epg_channel_names(),
                )
                store.publish(result, persist=False)
                stream_ids = {row['name']: row['stream_id'] for row in client._records}
                trt = stream_ids['TR: TRT 1 FHD']
                tv85 = stream_ids['TR: TV8.5 FHD']
                self.assertEqual(client.resolved_epg_id(trt, fingerprint), 'TRT1.tr')
                self.assertEqual(client.resolved_epg_id(tv85, fingerprint), 'TV8,5.tr')
                self.assertEqual(client.load_epg(trt)[0].title, 'TRT')
                self.assertEqual(client.load_epg(tv85)[0].title, 'TV8.5')
                self.assertEqual(client.peek_epg(trt)[0].title, 'TRT')
                explicit = stream_ids['TR: TRT 1 HD']
                self.assertEqual(client.resolved_epg_id(explicit, fingerprint), 'KanalD.tr')
                self.assertEqual(client.load_epg(explicit), [])
                self.assertEqual(client.resolved_epg_id(stream_ids['DE: TV8.5 HD'], fingerprint), '')
                self.assertEqual(client.resolved_epg_id(stream_ids['TR: Show TV +1 HD'], fingerprint), '')
                self.assertEqual(client.resolved_epg_id(stream_ids['TR: STAR FHD'], fingerprint), 'StarTV.tr')
                self.assertEqual(result['name_choices'], [
                    [m3u.epg_name_key('TR: Kanal D FHD'), 'TR: Kanal D FHD', ['KanalD.tr', 'KanalDHD.tr']],
                ])
            finally:
                store.forget(SOURCE.source_id)


def ui_methods(*method_names, **extra):
    tree = ast.parse((PLUGIN / 'm3u_epg_ui.py').read_bytes())
    screen = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'GTM3UXMLTVSettingsScreen')
    screen.bases = [ast.Name(id='dict', ctx=ast.Load())]
    screen.body = [node for node in screen.body if isinstance(node, ast.FunctionDef) and node.name in method_names]
    helpers = [node for node in tree.body if isinstance(node, ast.FunctionDef)
               and node.name in ('_safe_int', '_status_text', '_binding_snapshot', '_normalise_kind',
                                 '_normalise_update_mode', '_normalise_interval')]
    namespace = {'__package__': 'GTIPTVPlayerPro', '_': i18n._, 'REFRESH_ACTIVE_STATES': ('queued', 'running'),
                 'Binding': m3u_epg_settings.Binding, 'SUPPORTED_INTERVALS': (4, 6, 12, 24),
                 'SUPPORTED_UPDATE_MODES': ('manual', 'automatic'), **extra}
    module = ast.fix_missing_locations(ast.Module(body=[*helpers, screen], type_ignores=[]))
    exec(compile(module, 'm3u_epg_ui.py', 'exec'), namespace)
    return namespace


class ReceiverMenuTests(unittest.TestCase):
    def test_dns_timeout_and_no_match_have_separate_device_language_messages(self):
        status = ui_methods('_mapping_page')['_status_text']
        binding = m3u_epg_settings.Binding(SOURCE.source_id)
        with patch.object(i18n, 'device_language', return_value='tr'):
            self.assertEqual(status(binding, {'state': 'failed', 'error_code': 'dns'}),
                             'EPG sunucusunun adı çözümlenemedi.')
            self.assertEqual(status(binding, {'state': 'failed', 'error_code': 'deadline'}),
                             i18n._('Request timed out'))
            binding.last_success_utc = NOW
            self.assertEqual(status(binding), 'EPG’de eşleşen kanal bulunamadı.')

    def test_two_stage_choice_persists_the_user_selected_channel(self):
        binding = m3u_epg_settings.Binding(SOURCE.source_id, kind='url', location='https://example.org/epg.xml')
        key = m3u.epg_name_key('Duplicate')
        store = types.SimpleNamespace(name_choices=lambda *args: ((key, 'Duplicate', ('a', 'b')),))
        saved = []

        def update(source_id, mutate):
            candidate = m3u_epg_settings.Binding.from_dict({**binding.as_dict(), **mutate(binding)})
            saved.append(candidate)
            return candidate

        screen_type = ui_methods('_mapping_page', 'open_name_mapping', '_choose_xmltv_name', '_save_name_mapping',
                                 set_text_if_changed=Mock(), update_m3u_epg_binding=update,
                                 _notify_settings_changed=Mock())['GTM3UXMLTVSettingsScreen']
        screen = screen_type(message=object())
        screen._closed = False
        screen._uses_default_persistence = True
        screen.source_id = SOURCE.source_id
        screen.binding = binding
        screen._refresh = Mock()
        screen.session = types.SimpleNamespace(openWithCallback=Mock())
        module = types.ModuleType('Screens.ChoiceBox')
        module.ChoiceBox = object
        with patch.dict(sys.modules, {'Screens.ChoiceBox': module}), patch.object(m3u_epg, 'get_default_store', return_value=store):
            screen.open_name_mapping()
            callback, box, title, rows = screen.session.openWithCallback.call_args.args
            self.assertEqual(rows[0][0], 'Duplicate')
            callback(rows[0])
            callback, box, title, rows = screen.session.openWithCallback.call_args.args
            self.assertEqual([value for _, value in rows], ['', 'a', 'b'])
            callback(rows[2])
        self.assertEqual(saved[0].name_mappings, {key: 'b'})

    def test_all_43_language_catalogs_have_the_new_epg_text(self):
        catalogs = list((PLUGIN / 'locale').glob('*/LC_MESSAGES/GTIPTVPlayerPro.mo'))
        self.assertEqual(len(catalogs), 43)
        for path in catalogs:
            catalog = GNUTranslations(io.BytesIO(path.read_bytes()))._catalog
            for message in ('EPG server name could not be resolved.', 'No matching EPG channels found.',
                            'No channels need manual mapping.', 'Channel mapping saved. Refresh EPG to apply it.'):
                self.assertTrue(catalog.get(message), (path.parents[1].name, message))


if __name__ == '__main__':
    unittest.main()

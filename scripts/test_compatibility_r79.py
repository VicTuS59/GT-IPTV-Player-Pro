"""Worker lifecycle and real time-zone checks; no receiver or provider access."""
import ast
import copy
import datetime as dt
import importlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import types
import unittest
from contextlib import contextmanager
from unittest.mock import Mock, patch

W = Path(__file__).resolve().parents[2]
P = W / 'release-1.7.0-r79/GTIPTVPlayerPro'
if os.name != 'posix':
    fcntl = types.ModuleType('fcntl')
    fcntl.LOCK_EX, fcntl.LOCK_UN, fcntl.LOCK_NB = 2, 8, 4
    fcntl.flock = lambda *args: None
    sys.modules['fcntl'] = fcntl
PKG = 'r79_actual'
package = types.ModuleType(PKG)
package.__path__ = [str(P)]
package.__file__ = str(P / '__init__.py')
sys.modules[PKG] = package
exec(compile((P / '__init__.py').read_bytes(), package.__file__, 'exec'), package.__dict__)
store = importlib.import_module(PKG + '.downloads')
launcher = importlib.import_module(PKG + '.download_launcher')
archive = importlib.import_module(PKG + '.catchup')
highlights = importlib.import_module(PKG + '.match_highlights_filter')
diagnostics = importlib.import_module(PKG + '.diagnostics')


class WorkerStartTests(unittest.TestCase):
    def setUp(self):
        self.state = {'worker': {}, 'worker_start': 0,
                      'jobs': [{'id': 'fixture', 'status': 'queued'}],
                      'private': {'fixture': {'url': 'fixture-private-url'}}}
        self.lock = threading.RLock()

        @contextmanager
        def transaction(*args, **kwargs):
            with self.lock:
                yield self.state

        for target, name, value in (
            (store, 'transaction', transaction), (store, 'alive', lambda record: bool(record)),
            (store.time, 'time', lambda: 100.0),
            (store.os.path, 'isfile', lambda path: path == '/usr/bin/python3'),
            (store.os, 'access', lambda *args: True),
        ):
            scope = patch.object(target, name, value)
            scope.start(); self.addCleanup(scope.stop)
        self.run = self.enter_patch(subprocess, 'run', Mock())
        self.log = self.enter_patch(diagnostics, 'log_event', Mock())

    def enter_patch(self, target, name, value):
        scope = patch.object(target, name, value)
        scope.start(); self.addCleanup(scope.stop)
        return value

    def test_existing_worker_is_not_started_again(self):
        self.state['worker'] = {'pid': 42, 'stamp': 'live'}
        store.ensure_worker(); self.run.assert_not_called()

    def test_pending_worker_reservation_prevents_duplicate(self):
        self.state['worker_start'] = 110
        store.ensure_worker(); self.run.assert_not_called()

    def test_new_worker_uses_standalone_python_and_short_launcher(self):
        store.ensure_worker()
        args, options = self.run.call_args
        self.assertEqual(args[0], ['/usr/bin/python3', str(P / 'download_launcher.py')])
        self.assertEqual(self.state['worker_start'], 120)
        self.assertTrue(options['check'])
        self.assertEqual(options['timeout'], 8)

    def test_spawn_uses_c_child_setup_and_private_detached_stdio(self):
        store.ensure_worker(); options = self.run.call_args.kwargs
        self.assertNotIn('preexec_fn', options)
        self.assertFalse(options.get('shell', False))
        self.assertTrue(options['start_new_session'])
        self.assertTrue(options['close_fds'])
        self.assertEqual(options['cwd'], '/')
        self.assertEqual(options['umask'], 0o077)
        for name in ('stdin', 'stdout', 'stderr'):
            self.assertEqual(options[name], subprocess.DEVNULL)

    def test_worker_start_does_not_change_jobs_or_credentials(self):
        before = copy.deepcopy(self.state)
        store.ensure_worker()
        for name in ('jobs', 'private', 'worker'):
            self.assertEqual(self.state[name], before[name])

    def test_launch_error_releases_reservation_for_immediate_retry(self):
        self.run.side_effect = OSError('fixture error')
        with self.assertRaises(store.DownloadError): store.ensure_worker()
        self.assertEqual(self.state['worker_start'], 0)
        self.run.side_effect = None
        store.ensure_worker(); self.assertEqual(self.run.call_count, 2)

    def test_timeout_is_bounded_and_releases_reservation(self):
        self.run.side_effect = subprocess.TimeoutExpired('fixture', 8)
        with self.assertRaises(store.DownloadError) as caught: store.ensure_worker()
        self.assertEqual(caught.exception.code, 'failed')
        self.assertEqual(self.state['worker_start'], 0)

    def test_failed_launcher_exit_uses_existing_translated_error(self):
        self.run.side_effect = subprocess.CalledProcessError(1, 'fixture')
        with self.assertRaises(store.DownloadError) as caught: store.ensure_worker()
        self.assertEqual(caught.exception.code, 'failed')
        self.log.assert_called_once()

    def test_failure_preserves_newer_reservation(self):
        def fail(*args, **kwargs):
            self.state['worker_start'] = 130
            raise OSError('fixture')
        self.run.side_effect = fail
        with self.assertRaises(store.DownloadError): store.ensure_worker()
        self.assertEqual(self.state['worker_start'], 130)

    def test_early_worker_registration_is_preserved(self):
        def register(*args, **kwargs):
            self.state['worker'] = {'pid': 42, 'stamp': 'live'}
            self.state['worker_start'] = 0
        self.run.side_effect = register
        store.ensure_worker(); store.ensure_worker()
        self.run.assert_called_once()
        self.assertEqual(self.state['worker']['pid'], 42)

    def test_embedded_enigma_binary_is_never_used_as_python(self):
        with patch.object(store.os.path, 'isfile', return_value=False), patch.object(store.sys, 'executable', '/usr/bin/enigma2'):
            with self.assertRaises(store.DownloadError): store.ensure_worker()
        self.run.assert_not_called()
        self.assertEqual(self.state['worker_start'], 0)

    def test_standalone_interpreter_fallback_is_retained(self):
        with patch.object(store.os.path, 'isfile', return_value=False), patch.object(store.sys, 'executable', '/custom/python3.12'):
            store.ensure_worker()
        self.assertEqual(self.run.call_args.args[0][0], '/custom/python3.12')

    def test_local_interpreter_candidate_is_supported(self):
        with patch.object(store.os.path, 'isfile', side_effect=lambda path: path == '/usr/local/bin/python3'):
            store.ensure_worker()
        self.assertEqual(self.run.call_args.args[0][0], '/usr/local/bin/python3')

    def test_concurrent_starts_launch_exactly_one_process(self):
        entered, release = threading.Event(), threading.Event()
        self.run.side_effect = lambda *args, **kwargs: (entered.set(), release.wait(2))
        worker = threading.Thread(target=store.ensure_worker)
        worker.start()
        try:
            self.assertTrue(entered.wait(2)); store.ensure_worker()
            self.run.assert_called_once()
        finally:
            release.set(); worker.join(3)
        self.assertFalse(worker.is_alive())

    def test_failed_spawn_preserves_pending_download(self):
        before = copy.deepcopy(self.state)
        self.run.side_effect = OSError('fixture')
        with self.assertRaises(store.DownloadError): store.ensure_worker()
        for name in ('jobs', 'private'): self.assertEqual(self.state[name], before[name])


class LauncherTests(unittest.TestCase):
    def test_worker_is_started_as_a_separate_session(self):
        class Exit(BaseException): pass
        with patch.object(launcher.subprocess, 'Popen') as popen, patch.object(launcher.os, '_exit', side_effect=Exit) as leave:
            with self.assertRaises(Exit): launcher.main()
        args, options = popen.call_args
        self.assertEqual(args[0], [sys.executable, str(P / 'download_worker.py'), 'run'])
        self.assertTrue(options['close_fds']); self.assertTrue(options['start_new_session'])
        self.assertEqual(options['cwd'], '/'); self.assertEqual(options['umask'], 0o077)
        self.assertNotIn('preexec_fn', options); self.assertFalse(options.get('shell', False))
        for name in ('stdin', 'stdout', 'stderr'): self.assertEqual(options[name], subprocess.DEVNULL)
        popen.return_value.wait.assert_not_called()
        leave.assert_called_once_with(0)

    def test_worker_spawn_failure_returns_nonzero(self):
        with patch.object(launcher.subprocess, 'Popen', side_effect=OSError('fixture')), patch.object(launcher.os, '_exit') as leave:
            self.assertEqual(launcher.main(), 1)
        leave.assert_not_called()

    def test_subprocess_error_returns_nonzero(self):
        with patch.object(launcher.subprocess, 'Popen', side_effect=subprocess.SubprocessError('fixture')):
            self.assertEqual(launcher.main(), 1)

    def test_no_python_fork_global_child_handler_or_new_thread(self):
        for name in ('downloads.py', 'download_launcher.py'):
            tree = ast.parse((P / name).read_bytes())
            names = [ast.unparse(node.func) for node in ast.walk(tree) if isinstance(node, ast.Call)]
            self.assertFalse(set(names) & {'os.fork', 'os.register_at_fork', 'signal.signal', 'threading.Thread'})

    def test_launcher_import_is_side_effect_free(self):
        with patch.object(subprocess, 'Popen', side_effect=AssertionError('spawn during import')):
            importlib.reload(launcher)


class TimeZoneTests(unittest.TestCase):
    def client(self, info):
        value = object.__new__(archive.ArchiveClient)
        value._timezone_checked, value._timezone = False, None
        value.client = types.SimpleNamespace(_request_json=Mock(return_value={'server_info': info}))
        value._check = Mock()
        return value

    def test_istanbul_can_be_resolved_without_server_clock_fields(self):
        value = self.client({'timezone': 'Europe/Istanbul'})
        zone = value._xtream_timezone()
        self.assertEqual(dt.datetime(2026, 10, 30, tzinfo=zone).utcoffset(), dt.timedelta(hours=3))

    def test_archive_uses_historical_daylight_saving_offset(self):
        zone = self.client({'timezone': 'Europe/London'})._xtream_timezone()
        winter = archive._timestamp('2026-01-15 12:00:00', zone)
        summer = archive._timestamp('2026-07-15 12:00:00', zone)
        self.assertEqual(dt.datetime.fromtimestamp(winter, dt.timezone.utc).hour, 12)
        self.assertEqual(dt.datetime.fromtimestamp(summer, dt.timezone.utc).hour, 11)

    def test_archive_handles_dst_transition_day(self):
        zone = self.client({'timezone': 'Europe/London'})._xtream_timezone()
        before = archive._timestamp('2026-03-29 00:30:00', zone)
        after = archive._timestamp('2026-03-29 02:30:00', zone)
        self.assertEqual(after - before, 3600)

    def test_provider_timezone_is_looked_up_only_once_per_client(self):
        value = self.client({'timezone': 'Europe/Istanbul'})
        self.assertIs(value._xtream_timezone(), value._xtream_timezone())
        value.client._request_json.assert_called_once()

    def test_unknown_zone_keeps_existing_provider_offset_fallback(self):
        stamp = int(dt.datetime(2026, 10, 30, 9, tzinfo=dt.timezone.utc).timestamp())
        zone = self.client({'timezone': 'Invalid/Fixture', 'time_now': '2026-10-30 12:00:00', 'timestamp_now': stamp})._xtream_timezone()
        self.assertEqual(zone.utcoffset(None), dt.timedelta(hours=3))

    def test_unresolvable_time_without_zone_is_not_silently_shifted(self):
        value = self.client({'timezone': 'Invalid/Fixture'})
        self.assertIsNone(value._xtream_timezone())
        self.assertEqual(archive._timestamp('2026-10-30 12:00:00', None), 0)

    def test_match_date_includes_turkish_day_after_utc_midnight_boundary(self):
        stamp = dt.datetime(2026, 10, 29, 22, 30, tzinfo=dt.timezone.utc).timestamp()
        value = types.SimpleNamespace(kickoff=stamp, league='tur.1')
        with patch('zoneinfo.ZoneInfo', wraps=importlib.import_module('zoneinfo').ZoneInfo) as lookup:
            days = highlights.event_days(value)
        lookup.assert_called_once_with('Europe/Istanbul')
        self.assertIn(dt.date(2026, 10, 30), days)
        self.assertIn(dt.date(2026, 10, 29), days)

    def test_required_zones_for_highlights_and_worldwide_portals_exist(self):
        from zoneinfo import ZoneInfo
        for name in set(highlights.LEAGUE_ZONES.values()) | {'America/New_York', 'Africa/Cairo', 'Asia/Riyadh', 'Australia/Sydney', 'UTC'}:
            self.assertIsNotNone(ZoneInfo(name))


class ReleaseTests(unittest.TestCase):
    def test_r78_buffers_graphics_languages_and_worker_are_identical(self):
        base = W / 'release-1.7.0-r78/GTIPTVPlayerPro'
        for name in ('memory_catalog.py', 'graphics_policy.py', 'artwork_cache.py', 'live_patch.py', 'stream_media.py', 'live_recording.py', 'live_recording_proxy.py', 'download_worker.py', 'catchup.py', 'match_highlights_filter.py'):
            self.assertEqual((P / name).read_bytes(), (base / name).read_bytes(), name)
        count = 0
        for source in (base / 'locale').rglob('*'):
            if source.is_file(): self.assertEqual(source.read_bytes(), (P / source.relative_to(base)).read_bytes())
            if source.name == 'GTIPTVPlayerPro.mo': count += 1
        self.assertEqual(count, 43)

    def test_ipk_declares_zoneinfo_and_full_iana_data(self):
        control = (P.parent / 'CONTROL/control').read_text(encoding='utf-8')
        depends = next(line for line in control.splitlines() if line.startswith('Depends:'))
        self.assertIn('python3-zoneinfo | python3-misc (<< 3.10)', depends)
        self.assertIn('tzdata', depends.split(':', 1)[1].replace(' ', '').split(','))

    def test_every_runtime_source_compiles_with_python39_syntax(self):
        for source in P.rglob('*.py'):
            tree = ast.parse(source.read_bytes(), filename=str(source), feature_version=(3, 9))
            compile(tree, str(source), 'exec')


if __name__ == '__main__':
    unittest.main(verbosity=2)

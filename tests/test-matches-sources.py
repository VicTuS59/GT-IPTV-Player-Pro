"""Provider parsing, cache isolation and Enigma2 score navigation."""

import ast
import datetime as dt
import gettext
import importlib.util
import io
import sys
import threading
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / 'GTIPTVPlayerPro'
sys.path.insert(0, str(ROOT))

from GTIPTVPlayerPro import i18n, matches, matches_sources as sources, matches_text


DAY = dt.date(2026, 11, 12)
NOW = dt.datetime.combine(DAY, dt.time(18)).timestamp()


def flash_html(rows):
    return ('<html><div id="score-data"><h4>EUROPE: League <a href="/standings/a/">Standings</a></h4>'
            + rows + '</div></html>').encode('utf-8')


class ProviderTests(unittest.TestCase):
    def test_flashscore_reads_scored_live_finished_and_nested_status_rows(self):
        raw = flash_html(
            '<span class="live">45\'+2</span>Türkiye <img src="card.png" /> - Belgium '
            '<a href="/match/aB123456/" class="live">2-1</a><br />'
            '<span>20:45</span>England - Croatia <a class="sched" href="/match/cD123456/">&nbsp;-&nbsp;</a><br />'
            '<span>18:00</span>Italy - France <a class="fin" href="/match/eF123456/?d=0">1-0</a><br />'
            '<span>19:00<span class="status canceled">Postponed</span></span>Team &amp; Co - Away '
            '<a href="/match/gH123456/" class="fin">-</a><br />'
        )
        opener = Mock(side_effect=lambda request, timeout: io.BytesIO(raw))
        rows = sources._flash_day(opener, DAY, NOW, matches.Budget())
        self.assertEqual([league for _, league in rows], ['EUROPE: League'] * 4)
        live, upcoming, finished, postponed = (match for match, _ in rows)
        self.assertEqual((live.home, live.away, live.score, live.clock), ('Türkiye', 'Belgium', '2 – 1', '45′+2′'))
        self.assertEqual((upcoming.category, upcoming.score), ('upcoming', '—'))
        self.assertEqual(dt.datetime.fromtimestamp(upcoming.kickoff, dt.timezone.utc).strftime('%H:%M'), '19:45')
        self.assertEqual((finished.category, finished.score), ('finished', '1 – 0'))
        self.assertEqual((postponed.home, postponed.category, postponed.phase), ('Team & Co', 'upcoming', 'Postponed'))
        self.assertFalse(finished.home_id)
        self.assertTrue(opener.call_args.args[0].full_url.startswith(sources.FLASH_URL))

    def test_flashscore_without_tzdata_uses_winter_and_summer_offsets(self):
        with patch.object(sources, '_CENTRAL_EUROPE', None):
            self.assertEqual(sources._flash_zone(dt.date(2026, 1, 8)).utcoffset(None), dt.timedelta(hours=1))
            self.assertEqual(sources._flash_zone(dt.date(2026, 7, 8)).utcoffset(None), dt.timedelta(hours=2))

    def test_missing_or_truncated_flashscore_table_is_a_fetch_error(self):
        for body in (b'<html>Service unavailable</html>', b'<div id="score-data"><h4>League</h4>'):
            opener = Mock(side_effect=lambda request, timeout: io.BytesIO(body))
            snap = sources.fetch_flashscore_day(DAY, opener=opener, clock=lambda: NOW)
            self.assertIsNone(snap.updated_at)
            self.assertTrue(snap.failed_leagues)

    def test_flashscore_snapshot_keeps_competitions_and_current_scores(self):
        payload = flash_html('<span class="live">HT</span>Arsenal - Chelsea '
                             '<a href="/match/aB123456/" class="live">2-0</a><br />')
        opener = Mock(side_effect=lambda request, timeout: io.BytesIO(payload))
        snap = sources.fetch_flashscore_day(DAY, opener=opener, clock=lambda: NOW)
        self.assertEqual(snap.updated_at, NOW)
        self.assertFalse(snap.failed_leagues)
        self.assertEqual(snap.leagues, (('flashscore:europe: league', 'EUROPE: League'),))
        live, = snap.matches
        self.assertEqual((live.phase, live.score), ('Half-time', '2 – 0'))
        self.assertTrue(all(call.args[0].full_url.startswith(sources.FLASH_URL) for call in opener.call_args_list))

    def test_flashscore_http_denial_stays_a_source_error(self):
        def denied(request, timeout):
            raise HTTPError(request.full_url, 403, 'Forbidden', None, io.BytesIO(b''))
        opener = Mock(side_effect=denied)
        snap = sources.fetch_flashscore_day(DAY, opener=opener, clock=lambda: NOW)
        self.assertFalse(snap.matches)
        self.assertIsNone(snap.updated_at)
        self.assertTrue(snap.failed_leagues)
        self.assertEqual(opener.call_count, 1)

    def test_cancellation_prevents_network_requests(self):
        cancel = threading.Event()
        cancel.set()
        opener = Mock()
        with self.assertRaises(matches.Cancelled):
            sources.fetch_flashscore_day(DAY, cancel, opener=opener, clock=lambda: NOW)
        opener.assert_not_called()

    def test_cache_keeps_scores_separate_and_reuses_broadcasts(self):
        with patch.object(matches.tempfile, 'mkdtemp', side_effect=OSError):
            flash = matches.MatchesLoader(source='flashscore')
            espn = matches.MatchesLoader(source='espn')
        with patch.object(matches, '_RECENT_MATCHES', matches.OrderedDict()):
            flash._remember_snapshot(flash._snapshot_key('scores', DAY), 'flash scores')
            flash._remember_snapshot(flash._snapshot_key('broadcasts', None), 'TV listings')
            self.assertEqual(flash.cached('scores', DAY), 'flash scores')
            self.assertIsNone(espn.cached('scores', DAY))
            self.assertEqual(espn.cached('broadcasts', None), 'TV listings')
        flash.close()
        espn.close()

    def test_worker_start_failure_is_reported_without_raising(self):
        with patch.object(matches.tempfile, 'mkdtemp', side_effect=OSError):
            loader = matches.MatchesLoader(source='flashscore')
        with patch.object(matches.threading.Thread, 'start', side_effect=RuntimeError):
            generation = loader.request('scores', DAY)
        self.assertEqual(loader.poll(), (generation, 'scores', None, True))
        loader.close()


def load_ui(choice_box):
    def module(name, **attributes):
        value = types.ModuleType(name)
        value.__dict__.update(attributes)
        return value

    stub = Mock()
    replacements = dict((m.__name__, m) for m in (
        module('Components.ActionMap', ActionMap=stub), module('Components.Label', Label=stub),
        module('Components.Pixmap', Pixmap=stub), module('Screens.ChoiceBox', ChoiceBox=choice_box),
        module('Screens.Screen', Screen=dict), module('Screens.MessageBox', MessageBox=stub),
        module('enigma', eTimer=stub),
        module('GTIPTVPlayerPro.background', attach_native_alpha_pixmap=stub),
        module('GTIPTVPlayerPro.main', _connect_timer=stub, _scale=stub),
        module('GTIPTVPlayerPro.matches_skin', rows=stub, skin=stub, scroll_geometry=stub),
        module('GTIPTVPlayerPro.remote_footer', prepared_skin=stub, skin_metadata=stub, bind_layout_text=stub),
        module('GTIPTVPlayerPro.scrollbar', update_scrollbar=stub),
        module('GTIPTVPlayerPro.typography', set_text_if_changed=stub, fit_dynamic_text=stub),
        module('GTIPTVPlayerPro.welcome_text', fit_action_caption=stub),
        module('GTIPTVPlayerPro.ui_update', set_visible=stub),
    ))
    spec = importlib.util.spec_from_file_location('GTIPTVPlayerPro.matches_ui', PLUGIN / 'matches_ui.py')
    ui = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, replacements):
        spec.loader.exec_module(ui)
    return ui


class OldChoiceBox:
    def __init__(self, session, title, list):
        self.session, self.prompt, self.options = session, title, list


class NewChoiceBox:
    def __init__(self, session, text, choiceList):
        self.session, self.prompt, self.options = session, text, choiceList


class NavigationTests(unittest.TestCase):
    def test_source_picker_works_with_legacy_and_current_choicebox_arguments(self):
        for box, selection_argument in ((OldChoiceBox, 'entry'), (NewChoiceBox, 'none')):
            ui = load_ui(box)
            session = types.SimpleNamespace(open=Mock())
            with patch.object(i18n, 'device_language', return_value='tr'):
                picker = ui.GTMatchesSourceScreen(session)
            self.assertEqual(picker.prompt, 'Kaynak seç')
            self.assertEqual([row[0] for row in picker.options], ['ESPN', 'Flashscore'])
            for entry, source in zip(picker.options, matches.SCORE_SOURCES):
                self.assertEqual(entry[1], 'CALLFUNC')
                entry[2](entry if selection_argument == 'entry' else None)
                session.open.assert_called_with(ui.GTMatchesScreen, source=source)

    def test_dashboard_cancel_does_not_pass_choicebox_result_to_account_preview(self):
        ui = load_ui(NewChoiceBox)
        tree = ast.parse((PLUGIN / 'main.py').read_bytes())
        function = next(node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef) and node.name == 'open_matches')
        namespace = {'__package__': 'GTIPTVPlayerPro'}
        exec(compile(ast.Module(body=[function], type_ignores=[]), 'main.py', 'exec'), namespace)
        owner = types.SimpleNamespace(_refresh=Mock(), _cancel_account_preview=Mock(), _open_child_screen=Mock(),
                                      _schedule_account_preview_check=Mock())
        with patch.dict(sys.modules, {'GTIPTVPlayerPro.matches_ui': ui}):
            namespace['open_matches'](owner)
        screen, on_close = owner._open_child_screen.call_args.args
        self.assertIs(screen, ui.GTMatchesSourceScreen)
        on_close(None)
        owner._schedule_account_preview_check.assert_called_once_with()

    def test_failed_source_displays_localized_error_and_allows_cancel(self):
        ui = load_ui(NewChoiceBox)
        screen = ui.GTMatchesScreen.__new__(ui.GTMatchesScreen)
        screen._closed = False
        screen._dialog_open = True
        screen._date_chosen()
        self.assertFalse(screen._dialog_open)
        screen.matches = []
        screen.tab = 0
        screen._loading = False
        screen._error = True
        screen.snapshot = matches.Snapshot(DAY, (), None, ('date',), False)
        screen._set = Mock()
        screen['empty_icon'] = screen['empty_message'] = object()
        with patch.object(i18n, 'device_language', return_value='tr'):
            screen._render_message()
            screen._set.assert_any_call('empty_message', matches_text.tr('Scores could not be updated.'))

    def test_source_labels_follow_every_installed_device_language(self):
        catalogs = sorted((PLUGIN / 'locale').glob('*/LC_MESSAGES/GTIPTVPlayerPro.mo'))
        self.assertEqual(len(catalogs), 43)
        for path in catalogs:
            code = i18n.normalize_language(path.parents[1].name)
            catalog = gettext.GNUTranslations(io.BytesIO(path.read_bytes()))
            with self.subTest(language=code), patch.object(i18n, 'device_language', return_value=code):
                expected = catalog._catalog['Football scores\x04Select source']
                self.assertEqual(matches_text.tr('Select source'), expected)
                self.assertIn('Flashscore', i18n._('Source: {}').format('Flashscore'))
        with patch.object(i18n, 'device_language', return_value='en'):
            self.assertEqual(matches_text.tr('Select source'), 'Select source')


if __name__ == '__main__':
    unittest.main()

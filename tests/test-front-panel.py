"""Resolution, gettext, native text and actual summary lifecycle checks.

Native SDK adapters model clipping and glyph metrics; a physical panel is still
needed to verify the receiver driver's refresh rate and font glyph coverage.
"""
import gettext
import hashlib
import importlib.util
import io
import json
import math
import sys
import types
import unicodedata
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1] / 'GTIPTVPlayerPro'


class Size:
    def __init__(self, width, height):
        self.w, self.h = width, height
    def width(self): return self.w
    def height(self): return self.h
    def x(self): return self.w
    def y(self): return self.h


class Widget:
    def __init__(self, parent=None):
        self.parent = parent
        self.dimensions = Size(1, 1)
        self.position = Size(0, 0)
        self.visible = True
    def size(self): return self.dimensions
    def resize(self, value): self.dimensions = value
    def move(self, value): self.position = value
    def show(self): self.visible = True
    def hide(self): self.visible = False


class NativeLabel(Widget):
    alignLeft, alignCenter, alignRight = 0, 1, 2
    def __init__(self, parent=None):
        super().__init__(parent)
        self.value, self.font = '', ('Regular', 1)
        self.moves = 0
    def setText(self, value): self.value = value
    def setFont(self, value): self.font = value
    def setNoWrap(self, value): self.nowrap = value
    def setForegroundColor(self, value): self.color = value
    def setBackgroundColor(self, value): self.background = value
    def setVAlign(self, value): self.valign = value
    def setHAlign(self, value): self.halign = value
    def move(self, value):
        super().move(value)
        self.moves += 1
    def calculateSize(self):
        units = sum(0 if unicodedata.category(c).startswith('M') else
                    1 if unicodedata.east_asian_width(c) in ('W', 'F') else
                    .3 if c.isspace() else .6 for c in self.value)
        tall = 1.35 if any('\u0b80' <= c <= '\u0bff' for c in self.value) else 1.18
        return Size(math.ceil(units * self.font[1]), math.ceil(tall * self.font[1]))


class GUI:
    def __init__(self):
        self.instance = None
        self.skinAttributes = []
        self.visible = True
    def applySkin(self, desktop, parent):
        for key, value in self.skinAttributes:
            if key == 'size': self.instance.resize(Size(*map(int, value.split(','))))
        return True
    def show(self): self.visible = True
    def hide(self): self.visible = False


class Label(GUI):
    def __init__(self, text=''):
        super().__init__()
        self.value = text
    def setText(self, value): self.value = value
    def getText(self): return self.value


class Screen(dict):
    def __init__(self, session, parent=None):
        super().__init__()
        self.session, self.parent = session, parent
        self.onShown, self.onLayoutFinish, self.onHide, self.onClose = [], [], [], []
        self.onShow = []


class Timer:
    def __init__(self):
        self.callback, self.starts = [], []
        self.active = False
    def start(self, milliseconds, once=False):
        self.starts.append((milliseconds, once))
        self.active = True
    def stop(self): self.active = False


def sdk_module(name, **members):
    mod = types.ModuleType(name)
    mod.__path__ = []
    mod.__dict__.update(members)
    sys.modules[name] = mod


sdk_module('Components')
sdk_module('Components.GUIComponent', GUIComponent=GUI)
sdk_module('Components.Label', Label=Label)
sdk_module('Screens')
sdk_module('Screens.Screen', Screen=Screen)
sdk_module('skin', parseColor=lambda color: color)
language = types.SimpleNamespace(getLanguage=lambda: 'en_EN', addCallback=lambda callback: None)
sdk_module('Components.Language', language=language)
sdk_module('enigma', eWidget=Widget, eLabel=NativeLabel, ePoint=Size, eSize=Size,
           gFont=lambda family, size: (family, size), eTimer=Timer,
           iServiceInformation=types.SimpleNamespace(),
           getDesktop=lambda index: types.SimpleNamespace(size=lambda: Size(400, 240)))
NAME = '_front_panel_tests'
spec = importlib.util.spec_from_file_location(NAME, ROOT / '__init__.py', submodule_search_locations=[str(ROOT)])
pkg = importlib.util.module_from_spec(spec)
sys.modules[NAME] = pkg
spec.loader.exec_module(pkg)
front = __import__(NAME + '.front_panel', fromlist=['*'])
layout = __import__(NAME + '.front_panel_layout', fromlist=['*'])
text = __import__(NAME + '.front_panel_text', fromlist=['*'])
i18n = __import__(NAME + '.i18n', fromlist=['*'])

PANELS = ((16, 8), (64, 16), (128, 32), (64, 64), (96, 40), (96, 48),
          (128, 64), (132, 64), (220, 176), (256, 64), (320, 240),
          (400, 240), (480, 272), (480, 320), (640, 480), (800, 480),
          (240, 320), (480, 800), (1280, 720))


def bind(component, field):
    component.instance = Widget()
    component.instance.resize(Size(*field['box'][2:]))
    component.postWidgetCreate(component.instance)


class FrontPanelTests(unittest.TestCase):
    def summary(self, kind='movie', dimensions=(400, 240), title='Harry Potter and the Philosopher’s Stone', seek=(13, 9653), **metadata):
        if kind == 'youtube':
            kind = 'movie'
            metadata['youtube_native'] = True
        item = types.SimpleNamespace(id='fixture', content_type=kind, name=title, **metadata)
        player = types.SimpleNamespace(current_item=item, _seek_position=lambda: seek, _current_epg_events=[])
        return self.bind_summary(player, dimensions), player

    def bind_summary(self, player, dimensions, summary_class=front.GTPlayerFrontPanelSummary):
        with patch.object(front, 'getDesktop', return_value=types.SimpleNamespace(size=lambda: Size(*dimensions))):
            panel = summary_class(None, player)
        for name in layout.FIELDS:
            bind(panel[name], panel._layout['fields'][name])
        panel['progress_fill'].instance = Widget()
        for callback in panel.onLayoutFinish: callback()
        self.addCleanup(panel._stop)
        return panel

    def test_geometry_matrix_and_skin_stay_inside_the_real_viewport(self):
        for width, height in PANELS:
            for kind in ('movie', 'series', 'youtube', 'live', 'catchup'):
                with self.subTest(panel=(width, height), kind=kind):
                    result = layout.panel_layout(width, height, kind)
                    skin = ET.fromstring(layout.panel_skin(result))
                    self.assertEqual(skin.attrib['size'], '%s,%s' % (width, height))
                    boxes = []
                    for field in result['fields'].values():
                        x, y, w, h = field['box']
                        self.assertTrue(0 <= x < width and 0 <= y < height)
                        self.assertTrue(1 <= w <= width - x and 1 <= h <= height - y)
                        self.assertLessEqual(field['font'], h)
                        if field['visible']: boxes.append((x, y, w, h))
                    for index, (x, y, w, h) in enumerate(boxes):
                        for xx, yy, ww, hh in boxes[index + 1:]:
                            self.assertTrue(x + w <= xx or xx + ww <= x or y + h <= yy or yy + hh <= y)
                    if result['progress_h']:
                        self.assertLessEqual(result['progress_y'] + result['progress_h'], height)
                        self.assertTrue(all(y + h <= result['progress_y'] for x, y, w, h in boxes))

    def test_unusual_and_invalid_dimensions_are_bounded(self):
        for width, height in ((1, 1), (12, 90), (97, 41), (239, 159), (241, 161), (4096, 4096), (-5, 0), (10000, 9000)):
            result = layout.panel_layout(width, height, 'series')
            for field in result['fields'].values():
                x, y, w, h = field['box']
                self.assertLessEqual(x + w, result['width'])
                self.assertLessEqual(y + h, result['height'])

    def test_display_resolution_uses_only_the_front_desktop(self):
        calls = []
        def desktop(index):
            calls.append(index)
            self.assertEqual(index, 1)
            return types.SimpleNamespace(size=lambda: Size(480, 320))
        with patch.object(front, 'getDesktop', side_effect=desktop):
            self.assertEqual(front.front_panel_size(), (480, 320))
        self.assertEqual(calls, [1])
        with patch.object(front, 'getDesktop', side_effect=RuntimeError('no display')):
            self.assertEqual(front.front_panel_size(), (132, 64))

    def test_color_lcd_does_not_limit_models_by_name(self):
        for dimensions in ((320, 240), (400, 240), (480, 320), (800, 480)):
            panel, player = self.summary(dimensions=dimensions)
            self.assertEqual((panel._layout['width'], panel._layout['height']), dimensions)
            self.assertEqual(panel._layout['mode'], 'full')
            self.assertEqual(panel['elapsed'].getText(), '00:13')
            self.assertEqual(panel['total'].getText(), '02:40:53')
            self.assertEqual(panel['remaining'].getText(), '02:40:40')

    def test_movies_series_and_youtube_share_the_layout_at_each_panel_size(self):
        for dimensions in PANELS:
            movie = layout.panel_skin(layout.panel_layout(*dimensions, kind='movie'))
            for kind in ('series', 'youtube'):
                with self.subTest(dimensions=dimensions, kind=kind):
                    self.assertEqual(movie, layout.panel_skin(layout.panel_layout(*dimensions, kind=kind)))

    def test_archive_and_unknown_seek_remain_readable(self):
        panel, player = self.summary('catchup', seek=(-1, 0))
        self.assertEqual(panel['kind'].getText(), 'TV ARCHIVE')
        self.assertEqual(panel['elapsed'].getText(), '--:--')
        self.assertEqual(panel._layout['progress_h'], 6)
        self.assertFalse(panel['progress_fill'].visible)
        player._seek_position = lambda: (10000, 9999)
        panel.refresh_now()
        self.assertEqual(panel['remaining'].getText(), '00:00')
        self.assertEqual(panel._progress_value, 100)

    def test_compact_oled_rotates_large_caption_and_time_pairs(self):
        with patch.object(front.time, 'monotonic', return_value=100.0) as clock:
            panel, player = self.summary('series', dimensions=(132, 64), title='Film', season='3', episode='12')
            self.assertEqual(panel._layout['mode'], 'compact')
            self.assertEqual(panel['title'].getText(), 'Film')
            self.assertFalse(panel['episode'].visible)
            seen = set()
            for tick in range(20):
                clock.return_value = 100 + tick * 60
                panel.refresh_now()
                seen.add((panel['kind'].getText(), panel['title'].getText()))
                self.assertGreaterEqual(panel['kind']._display_font, 19)
                self.assertGreaterEqual(panel['title']._display_font, 25)
            self.assertTrue({('SERIES · 0%', 'Film'), ('ELAPSED', '00:13'),
                             ('TOTAL', '02:40:53'), ('REMAINING · 0%', '02:40:40')} <= seen)

    def test_minimal_single_line_cycles_after_a_complete_scroll(self):
        with patch.object(front.time, 'monotonic', return_value=100.0) as clock:
            panel, player = self.summary('series', dimensions=(128, 32), season='2', episode='7')
            self.assertEqual(panel._layout['mode'], 'minimal')
            self.assertEqual(panel['title'].getText(), player.current_item.name)
            cycle = max(5.0, panel['title'].cycle_seconds())
            clock.return_value = 100 + cycle - .1
            panel.refresh_now()
            self.assertEqual(panel['title'].getText(), player.current_item.name)
            clock.return_value = 101 + cycle
            panel.refresh_now()
            self.assertEqual(panel['title'].getText(), 'ELAPSED 00:13')

    def test_minimal_rotation_includes_localized_time_captions(self):
        with patch.object(front.time, 'monotonic', return_value=100.0) as clock:
            panel, player = self.summary(dimensions=(64, 16), title='Film')
            seen = set()
            for tick in range(30):
                clock.return_value = 100 + tick * 60
                panel.refresh_now()
                seen.add(panel['title'].getText())
            self.assertTrue({'Film', 'MOVIES', 'ELAPSED 00:13', 'TOTAL 02:40:53', 'REMAINING 02:40:40'} <= seen)

    def test_live_programme_and_clock_never_reuse_old_episode_text(self):
        panel, player = self.summary('live')
        self.assertEqual(panel['subtitle'].getText(), 'EPG information unavailable')
        with patch.object(front.time, 'time', return_value=1000):
            player._current_epg_events = [types.SimpleNamespace(title='Evening news', start_timestamp=900, end_timestamp=1500)]
            panel.refresh_now()
            self.assertEqual(panel['subtitle'].getText(), 'Evening news')
            self.assertIn(' - ', panel['detail'].getText())
            player._current_epg_events[0].start_timestamp = 1200
            panel.refresh_now()
            self.assertEqual(panel['subtitle'].getText(), 'NEXT: Evening news')
        self.assertFalse(panel['episode'].visible)
        self.assertTrue(panel['clock'].visible)
        self.assertFalse(panel['progress_fill'].visible)

    def test_title_is_full_text_in_native_label_and_clipped_by_parent(self):
        value = 'A very long programme — Ελληνικά 日本語 Türkçe ' * 6
        panel, player = self.summary(title=value.strip())
        widget = panel['title']
        self.assertEqual(widget._label.value, value.strip())
        self.assertEqual(widget._label.parent, widget.instance)
        self.assertTrue(widget._label.nowrap)
        self.assertTrue(widget.moving())
        self.assertGreater(widget._label.size().width(), widget.instance.size().width())
        self.assertNotIn('…', widget._label.value)

    def test_fit_text_has_no_scroll_and_motion_timer_is_idle(self):
        panel, player = self.summary(title='Film')
        self.assertFalse(panel['title'].moving())
        self.assertFalse(panel._motion_pending)
        moves = panel['title']._label.moves
        panel['title'].tick(1000)
        self.assertEqual(panel['title']._label.moves, moves)

    def test_refresh_does_not_restart_scrolling_or_create_extra_motion_timers(self):
        panel, player = self.summary(title='Long programme title ' * 12)
        since = panel['title']._since
        count = len(panel._motion_timer.starts)
        for value in range(10):
            player._seek_position = lambda value=value: (value + 13, 9653)
            panel.refresh_now()
        self.assertEqual(panel['title']._since, since)
        self.assertEqual(len(panel._motion_timer.starts), count)
        panel['title'].tick(since + 2.5)
        self.assertLess(panel['title']._label.position.x(), 0)

    def test_changed_title_resets_scroll_start(self):
        panel, player = self.summary()
        with patch.object(front.time, 'monotonic', return_value=500):
            player.current_item.name = 'A different long film title'
            panel.refresh_now()
        self.assertEqual(panel['title']._since, 500)
        self.assertEqual(panel['title']._label.position.x(), 0)

    def test_rtl_scroll_starts_at_the_reading_edge_and_reaches_both_ends(self):
        self.assertTrue(text.right_to_left('123 عنوان عربي طويل'))
        self.assertFalse(text.right_to_left('123 English'))
        self.assertEqual(text.scroll_offset(0, 100, 20), 0)
        self.assertEqual(text.scroll_offset(0, 100, 20, True), -100)
        self.assertEqual(text.scroll_offset(1, 100, 20, True), -100)
        self.assertEqual(text.scroll_offset(4, 100, 20, True), -50)
        self.assertEqual(text.scroll_offset(7, 100, 20, True), 0)
        self.assertEqual(text.scroll_offset(8, 100, 20, True), -100)
        self.assertEqual(text.scroll_offset(999, 0, 20, True), 0)
        panel, player = self.summary(title='عنوان برنامج عربي طويل جداً ' * 7)
        self.assertEqual(panel['title']._label.halign, NativeLabel.alignRight)
        self.assertEqual(panel['title']._label.position.x(), -panel['title']._overflow)

    def test_native_height_metrics_fit_tall_translated_glyphs(self):
        panel, player = self.summary(title='தமிழ் ' * 20)
        widget = panel['title']
        self.assertLessEqual(widget._label.calculateSize().height(), widget.instance.size().height())
        self.assertEqual(widget.getText(), ('தமிழ் ' * 20).strip())

    def test_numeric_clocks_fit_before_scrolling_and_keep_their_full_value(self):
        for dimensions in ((320, 240), (400, 240), (240, 320), (480, 800)):
            panel, _ = self.summary(dimensions=dimensions, seek=(3599999, 7199999))
            for name in ('elapsed', 'total', 'remaining', 'percent'):
                with self.subTest(dimensions=dimensions, field=name):
                    field = panel[name]
                    measured = field._label.calculateSize()
                    self.assertLessEqual(measured.width(), field._width)
                    self.assertLessEqual(measured.height(), field._height)
                    self.assertFalse(field.moving())
                    self.assertEqual(field._label.value, field.getText())

    def test_hide_close_and_reshow_manage_both_timers(self):
        panel, player = self.summary(title='Overflow text ' * 20)
        self.assertTrue(panel._motion_timer.active)
        self.assertTrue(panel._refresh_timer.active)
        for callback in panel.onHide:
            callback()
        self.assertFalse(panel._motion_timer.active)
        self.assertFalse(panel._refresh_timer.active)
        panel._animate()
        self.assertFalse(panel._motion_timer.active)
        for callback in panel.onShow:
            callback()
        self.assertTrue(panel._motion_timer.active)
        self.assertTrue(panel._refresh_timer.active)
        panel._stop()
        self.assertIsNone(player._front_panel_summary)
        self.assertFalse(panel._motion_timer.active)
        self.assertFalse(panel._refresh_timer.active)

    def test_layout_callbacks_are_bound_methods_for_native_screen(self):
        panel, player = self.summary()
        self.assertTrue(all(getattr(callback, '__self__', None) is panel for callback in panel.onLayoutFinish))

    def test_skin_attributes_are_applied_to_native_child_not_viewport(self):
        field = layout.panel_layout(400, 240)['fields']['title']
        widget = text.PanelText()
        widget.configure(field)
        widget.instance = Widget()
        widget.postWidgetCreate(widget.instance)
        widget.skinAttributes = [('size', '120,30'), ('font', 'Regular;24'), ('foregroundColor', '#A855F7'),
                                 ('halign', 'right'), ('valign', 'center'), ('noWrap', '1')]
        widget.setText('Hello')
        self.assertTrue(widget.applySkin(None, None))
        self.assertEqual((widget._width, widget._height), (120, 30))
        self.assertEqual(widget._label.color, '#A855F7')
        self.assertEqual(widget._label.halign, NativeLabel.alignRight)
        self.assertNotIn('font', dict(widget.skinAttributes))
        widget.preWidgetRemove(widget.instance)
        self.assertFalse(widget.moving())

    def test_new_and_existing_ui_captions_are_in_all_43_catalogues(self):
        catalogues = sorted((ROOT / 'locale').glob('*/LC_MESSAGES/GTIPTVPlayerPro.mo'))
        self.assertEqual(len(catalogues), 43)
        messages = ('Elapsed', 'Total', 'Season %(season)s · Episode %(episode)s', 'REMAINING',
                    'MOVIES', 'SERIES', 'LIVE TV', 'TV Archive', 'Please wait', 'Season', 'Episode',
                    'EPG information unavailable', 'NEXT')
        for path in catalogues:
            with self.subTest(language=path.parents[1].name):
                translator = gettext.GNUTranslations(io.BytesIO(path.read_bytes()))
                for message in messages:
                    self.assertIn(message, translator._catalog)
                    self.assertTrue(translator.gettext(message).strip())
                result = translator.gettext(messages[2]) % dict(season='2', episode='7')
                self.assertIn('2', result)
                self.assertIn('7', result)
                with patch.object(front, '_', translator.gettext):
                    for kind in ('movie', 'series', 'youtube'):
                        panel, player = self.summary(kind, season='2', episode='7')
                        self.assertFalse(panel['episode'].visible)
                        for name, message in (('elapsed_label', 'Elapsed'), ('total_label', 'Total'),
                                              ('remaining_label', 'REMAINING')):
                            self.assertEqual(panel[name].getText(), translator.gettext(message).upper())
                        for name in ('kind', 'title', 'elapsed_label', 'total_label', 'remaining_label'):
                            self.assertLessEqual(panel[name]._label.calculateSize().height(), panel[name]._height)

    def test_all_playback_types_emphasize_remaining_time_and_scroll_long_titles(self):
        title = 'A long programme title with more text than the LCD can fit'
        for kind, caption in (('movie', 'MOVIES'), ('series', 'SERIES'), ('youtube', 'YouTube')):
            with self.subTest(kind=kind):
                panel, player = self.summary(kind, title=title, seek=(738, 6006), season='2', episode='7')
                fields = panel._layout['fields']
                self.assertEqual(panel['kind'].getText(), caption)
                self.assertFalse(panel['episode'].visible)
                self.assertEqual(panel['title'].getText(), title)
                self.assertTrue(panel['title'].moving())
                self.assertEqual(panel['elapsed'].getText(), '12:18')
                self.assertEqual(panel['total'].getText(), '01:40:06')
                self.assertEqual(panel['remaining'].getText(), '01:27:48')
                self.assertEqual(panel['percent'].getText(), '12%')
                self.assertEqual(panel['remaining']._label.halign, NativeLabel.alignCenter)
                self.assertEqual(panel['remaining']._label.color, '#22D3EE')
                self.assertEqual(panel['remaining_label']._label.color, '#22D3EE')
                self.assertLess(fields['title']['box'][1], fields['remaining_label']['box'][1])
                self.assertLess(fields['remaining']['box'][1], fields['elapsed_label']['box'][1])
                self.assertEqual(fields['elapsed']['box'][1], fields['total']['box'][1])
                self.assertGreaterEqual(panel['remaining']._display_font, 2 * panel['total']._display_font)

                self.assertEqual(panel._layout['progress_h'], 20)
                self.assertGreaterEqual(panel._layout['content_w'], 380)
                progress = panel['progress_fill']
                self.assertEqual(progress.instance.size().width(), 47)
                self.assertEqual(progress.instance.size().height(), 20)
                player._seek_position = lambda: (0, 6006)
                panel.refresh_now()
                self.assertFalse(progress.visible)
                player._seek_position = lambda: (6006, 6006)
                panel.refresh_now()
                self.assertTrue(progress.visible)
                self.assertEqual(progress.instance.size().width(), panel._layout['content_w'])

    def test_youtube_preview_uses_its_existing_sample_and_clears_after_playback(self):
        main = types.ModuleType(NAME + '.main')
        main._connect_timer = front._connect
        with patch.dict(sys.modules, {main.__name__: main}):
            preview = __import__(NAME + '.youtube_preview', fromlist=['YouTubePreviewMixin'])

        for dimensions in ((400, 240), (132, 64), (64, 32)):
            with self.subTest(dimensions=dimensions):
                player = preview.YouTubePreviewMixin()
                navigation = types.SimpleNamespace(reference=None)
                navigation.getCurrentlyPlayingServiceReference = lambda: navigation.reference
                player.session = types.SimpleNamespace(current_dialog=player, nav=navigation)
                player._closed = False
                player._init_youtube_preview(True)
                player._resume_youtube_window = Mock()
                player._suspend_youtube_window = Mock()
                player._seek_position = Mock(return_value=(738, 6006))
                panel = self.bind_summary(player, dimensions, player.createSummary())
                self.assertEqual(panel['title'].getText(), 'YouTube')
                self.assertFalse(panel['progress_bg'].visible)
                self.assertFalse(panel._refresh_timer.active)
                player._seek_position.assert_not_called()

                item = types.SimpleNamespace(id='video', name='A YouTube video title',
                                             content_type='movie', youtube_native=True)
                player.current_item = item
                player.reference = navigation.reference = object()
                player._started = True
                player._preview_tick()
                player._seek_position.assert_called_once_with()
                self.assertEqual(panel['title'].getText(), item.name)
                if panel._layout['mode'] == 'full':
                    self.assertEqual(panel['kind'].getText(), 'YouTube')
                    self.assertEqual(panel['remaining'].getText(), '01:27:48')
                    self.assertEqual(panel['progress_fill'].instance.size().height(), 20)

                for callback in panel.onHide:
                    callback()
                player._fullscreen_active = True
                player._preview_tick()
                player._seek_position.assert_called_once_with()
                player._fullscreen_active = False
                player._seek_position.return_value = (900, 6006)
                for callback in panel.onShow:
                    callback()
                self.assertTrue(panel._active)
                self.assertFalse(panel._refresh_timer.active)
                if panel._layout['mode'] == 'full':
                    self.assertEqual(panel['remaining'].getText(), '01:25:06')

                player._seek_position.reset_mock()
                navigation.reference = None
                player._preview_tick()
                self.assertEqual(panel['title'].getText(), 'YouTube')
                self.assertFalse(panel['progress_bg'].visible)
                self.assertFalse(panel['progress_fill'].visible)
                player._seek_position.assert_not_called()

    def test_portrait_clocks_use_the_full_width_to_remain_large(self):
        for dimensions in ((240, 320), (480, 800)):
            panel, player = self.summary(dimensions=dimensions, seek=(3361, 9653))
            for name in ('elapsed', 'total', 'remaining'):
                self.assertEqual(panel._layout['fields'][name]['box'][2], panel._layout['content_w'])
                self.assertFalse(panel[name].moving())
                self.assertGreaterEqual(panel[name]._display_font, 32)

    def test_small_panel_does_not_rotate_until_both_large_fields_finish(self):
        with patch.object(front.time, 'monotonic', return_value=100.0) as clock:
            panel, player = self.summary(dimensions=(132, 64), title='Long programme name ' * 10)
            duration = max(5, panel['title'].cycle_seconds(), panel['kind'].cycle_seconds())
            clock.return_value = 100 + duration - .1
            panel.refresh_now()
            self.assertEqual(panel['title'].getText(), player.current_item.name.strip())
            clock.return_value = 101 + duration
            panel.refresh_now()
            self.assertEqual(panel['kind'].getText(), 'ELAPSED')
            self.assertEqual(panel['title'].getText(), '00:13')
            since = panel['title']._since
            player._seek_position = lambda: (14, 9653)
            panel.refresh_now()
            self.assertEqual(panel['title']._since, since)
            self.assertEqual(panel['title'].getText(), '00:14')

    def test_live_tv_geometry_and_skin_match_the_approved_r3_snapshots(self):
        fixture = Path(__file__).parent / 'fixtures/front-panel-live-r3.json'
        snapshots = json.loads(fixture.read_text(encoding='utf-8'))
        for snapshot in snapshots:
            with self.subTest(dimensions=snapshot['size']):
                current = layout.panel_layout(*snapshot['size'], kind='live')
                body = json.dumps(current, sort_keys=True, separators=(',', ':')).encode()
                self.assertEqual(hashlib.sha256(body).hexdigest(), snapshot['layout_sha256'])
                self.assertEqual(hashlib.sha256(layout.panel_skin(current).encode()).hexdigest(), snapshot['skin_sha256'])

    def test_regional_aliases_and_turkish_case_follow_the_device_language(self):
        for device, folder in (('fr_FR', 'fr_FR'), ('pt_BR', 'pt'), ('sq_AL', 'sq_AL'), ('zh_CN', 'zh'), ('zh_HK', 'zh_HK'), ('tr_TR', 'tr')):
            translator = gettext.GNUTranslations(io.BytesIO((ROOT / 'locale' / folder / 'LC_MESSAGES/GTIPTVPlayerPro.mo').read_bytes()))
            with patch.object(language, 'getLanguage', return_value=device):
                i18n._CATALOGS.clear()
                self.assertEqual(i18n._('Elapsed'), translator.gettext('Elapsed'))
        with patch.object(language, 'getLanguage', return_value='tr_TR'):
            self.assertEqual(i18n.localized_upper('diziler'), 'DİZİLER')

    def test_unicode_provider_text_is_bounded_without_cutting_normal_titles(self):
        panel, player = self.summary(title='Café – Türkçe العربية 日本語')
        self.assertEqual(panel['title'].getText(), 'Café – Türkçe العربية 日本語')
        player.current_item.name = 'Long ' * 100000
        panel.refresh_now()
        self.assertLessEqual(len(panel['title'].getText()), 2048)

    def test_old_and_new_timer_signal_apis_are_supported(self):
        target = types.SimpleNamespace(callback=[])
        callback = lambda: None
        front._connect(target, callback)
        self.assertEqual(target.callback, [callback])
        connection = object()
        target = types.SimpleNamespace(timeout=types.SimpleNamespace(connect=lambda value: connection))
        self.assertIs(front._connect(target, callback), connection)


if __name__ == '__main__':
    unittest.main()

"""Welcome headings keep complete words and fit without native auto-wrapping."""
import gettext
import importlib.util
import io
import math
import sys
import unicodedata
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

P = Path(__file__).resolve().parents[1] / 'GTIPTVPlayerPro'
NAME = '_welcome_heading_checks'
spec = importlib.util.spec_from_file_location(
    NAME, P / '__init__.py', submodule_search_locations=[str(P)])
pkg = importlib.util.module_from_spec(spec)
sys.modules[NAME] = pkg
spec.loader.exec_module(pkg)
text = __import__(NAME + '.welcome_text', fromlist=['*'])
typography = __import__(NAME + '.typography', fromlist=['*'])


class NativeLabel:
    """An inert eLabel adapter with widths, font changes and explicit newlines."""
    def __init__(self, width, height, size):
        self.width, self.height, self.font = width, height, ('Regular', size)
        self.value = ''
        self.nowrap = True

    def size(self):
        return SimpleNamespace(width=lambda: self.width, height=lambda: self.height)

    def setFont(self, value):
        self.font = value

    def setNoWrap(self, value):
        self.nowrap = value

    def line_width(self, line):
        units = 0
        for c in line:
            if unicodedata.category(c).startswith('M'):
                continue
            units += 1 if unicodedata.east_asian_width(c) in ('W', 'F') else (
                0.3 if c.isspace() else 0.6)
        return math.ceil(units * self.font[1])

    def calculateSize(self):
        lines = self.value.split('\n')
        return SimpleNamespace(
            width=lambda: max(self.line_width(line) for line in lines),
            height=lambda: math.ceil(len(lines) * self.font[1] * 1.2))


class Label:
    def __init__(self, width=470, height=185, size=60, native=True):
        self.instance = NativeLabel(width, height, size) if native else None
        self.skinAttributes = [('size', '%s,%s' % (width, height)),
                               ('font', 'Regular;%s' % size), ('noWrap', '1')]
        self.value = ''

    def setText(self, value):
        self.value = value
        if self.instance:
            self.instance.value = value

    def getText(self):
        return self.value


class WelcomeHeadingTests(unittest.TestCase):
    def setUp(self):
        typography.clear_dynamic_text_cache()
        fake_font = patch.object(typography, 'gFont', side_effect=lambda family, size: (family, size))
        fake_font.start()
        self.addCleanup(fake_font.stop)

    def test_german_matches_approved_two_line_layout(self):
        label = Label()
        result = text.fit_welcome_title(label, 'Bereit zum Fernsehen?', 60, 44)
        self.assertEqual(result, 'Bereit zum\nFernsehen?')
        self.assertNotIn('Ferns\nehen', result)
        self.assertTrue(label.instance.nowrap)

    def test_all_43_catalogues_at_six_osds_and_three_text_profiles(self):
        paths = sorted((P / 'locale').glob('*/LC_MESSAGES/GTIPTVPlayerPro.mo'))
        self.assertEqual(len(paths), 43)
        for path in paths:
            original = gettext.GNUTranslations(io.BytesIO(path.read_bytes())).gettext('Ready to watch?')
            for width, height in ((640, 480), (720, 480), (720, 576),
                                  (1280, 720), (1920, 1080), (3840, 2160)):
                for profile, percent in (('standard', 100), ('large', 110), ('very_large', 110)):
                    factor = min(width / 1920, height / 1080)
                    size = max(1, round(round(60 * factor) * percent / 100))
                    label = Label(round(470 * width / 1920), round(185 * height / 1080), size)
                    minimum = max(12, round(36 * factor))
                    with self.subTest(language=path.parent.parent.name, osd=(width, height), profile=profile):
                        result = text.fit_welcome_title(label, original, size, minimum)
                        self.assertEqual(len(result.split('\n')), 2)
                        self.assertEqual(result.replace('\n', '').replace(' ', ''), original.replace(' ', ''))
                        if ' ' in original:
                            self.assertEqual(result.split(), original.split())
                        self.assertTrue(label.instance.nowrap)
                        self.assertLessEqual(label.instance.calculateSize().width(), label.instance.width)
                        self.assertLessEqual(label.instance.calculateSize().height(), label.instance.height)

    def test_no_native_measurements_still_preserve_whole_words(self):
        label = Label(width=200, size=30, native=False)
        self.assertEqual(text.fit_welcome_title(label, 'Bereit zum Fernsehen?', 30, 22),
                         'Bereit zum\nFernsehen?')

    def test_missing_calculate_size_uses_word_safe_fallback(self):
        label = Label()
        with patch.object(label.instance, 'calculateSize', side_effect=RuntimeError('Unavailable')):
            result = text.fit_welcome_title(label, 'Bereit zum Fernsehen?', 60, 44)
        self.assertEqual(result, 'Bereit zum\nFernsehen?')

    def test_long_indivisible_word_shrinks_without_truncation(self):
        label = Label(width=250, size=60)
        result = text.fit_welcome_title(label, 'Bereit zum Fernsehen?', 60, 30)
        self.assertEqual(result, 'Bereit zum\nFernsehen?')
        self.assertLess(label.instance.font[1], 60)
        self.assertLessEqual(label.instance.calculateSize().width(), 250)

    def test_non_space_languages_break_only_at_phrase_boundary(self):
        expected = {'准备好观看了吗？': '准备好\n观看了吗？',
                    '準備好觀看了嗎？': '準備好\n觀看了嗎？',
                    'พร้อมรับชมหรือยัง?': 'พร้อมรับชม\nหรือยัง?'}
        for original, rendered in expected.items():
            self.assertEqual(text.fit_welcome_title(Label(), original, 60, 44), rendered)

    def test_english_heading_and_repeated_refresh_keep_complete_text(self):
        label = Label()
        for unused in range(3):
            self.assertEqual(text.fit_welcome_title(label, 'Ready to watch?', 60, 44), 'Ready to\nwatch?')
            self.assertEqual(label.getText(), 'Ready to\nwatch?')


if __name__ == '__main__':
    unittest.main(verbosity=2)

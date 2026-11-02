#!/usr/bin/env python3
"""Merge the reviewed updater translations into all 43 existing catalogues."""
import gettext
import json
import subprocess
import tempfile
from pathlib import Path
from string import Formatter

ROOT = Path(__file__).resolve().parents[1]


def fields(text):
    return sorted(name for _, name, _, _ in Formatter().parse(text) if name is not None)


def main():
    data = json.loads((ROOT / 'scripts/updater-r1-translations.json').read_text(encoding='utf-8'))
    messages = data.pop('_messages')
    paths = sorted((ROOT / 'GTIPTVPlayerPro/locale').glob('*/LC_MESSAGES/GTIPTVPlayerPro.mo'))
    if len(paths) != 43 or set(data) != {p.parent.parent.name for p in paths}:
        raise ValueError('Translation languages must exactly match all 43 packaged catalogues')
    for mo in paths:
        language = mo.parent.parent.name
        rows = data[language]
        if len(rows) != len(messages):
            raise ValueError('Incomplete translations: ' + language)
        for source, translation in zip(messages, rows):
            if not translation or fields(source) != fields(translation):
                raise ValueError('Invalid translation/placeholder: ' + language + ' / ' + source)
        with mo.open('rb') as handle:
            original = gettext.GNUTranslations(handle)
        quote = lambda value: json.dumps(value, ensure_ascii=False)
        body = '# Updater 1.8.5-r1: translated failure reasons.\n'
        body += 'msgid ""\nmsgstr ' + quote(original._catalog['']) + '\n\n'
        for source, translation in zip(messages, rows):
            body += 'msgid ' + quote(source) + '\nmsgstr ' + quote(translation) + '\n\n'
        addition = mo.parent / 'update-r1.po'
        addition.write_text(body.rstrip() + '\n', encoding='utf-8')
        with tempfile.TemporaryDirectory(prefix='gt-updater-locales-') as directory:
            old = Path(directory) / 'existing.po'
            merged = Path(directory) / 'merged.po'
            output = Path(directory) / 'updated.mo'
            subprocess.run(['msgunfmt', str(mo), '-o', str(old)], check=True)
            subprocess.run(['msgcat', '--use-first', '--no-wrap', str(addition), str(old), '-o', str(merged)], check=True)
            subprocess.run(['msgfmt', '--check-format', str(merged), '-o', str(output)], check=True)
            with output.open('rb') as handle:
                updated = gettext.GNUTranslations(handle)
            for key, value in original._catalog.items():
                if key not in messages and updated._catalog.get(key) != value:
                    raise ValueError('Existing translation changed: ' + language + ' / ' + str(key))
            for source, translation in zip(messages, rows):
                if updated.gettext(source) != translation:
                    raise ValueError('Compiled translation mismatch: ' + language)
            mo.write_bytes(output.read_bytes())
    print('Updated and verified {} messages in all {} catalogues'.format(len(messages), len(paths)))


if __name__ == '__main__':
    main()

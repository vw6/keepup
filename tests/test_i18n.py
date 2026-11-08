import json
import os
import re
import unittest

from keepup import i18n

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLACE = re.compile(r'\{(\w+)\}')


def flat(table, prefix=''):
    out = {}
    for k, v in table.items():
        if isinstance(v, dict):
            out.update(flat(v, f'{prefix}{k}.'))
        else:
            out[prefix + k] = v
    return out


class Locales(unittest.TestCase):
    def test_every_key_in_code_is_in_base(self):
        base = flat(i18n._load(i18n.BASE))
        src = ''
        for name in os.listdir(os.path.join(ROOT, 'keepup')):
            if name.endswith('.py'):
                with open(os.path.join(ROOT, 'keepup', name), encoding='utf-8') as f:
                    src += f.read()
        keys = set(re.findall(r"tr\(['\"]([a-z_0-9.]+)['\"]", src))
        self.assertTrue(keys)
        self.assertEqual(sorted(k for k in keys if k not in base), [])
        for stage in ('waiting', 'prepare', 'deps', 'sources', 'build', 'package', 'sync', 'download', 'check', 'install', 'hooks'):
            self.assertIn(f'progress.stage.{stage}', base)

    def test_translations_keep_placeholders(self):
        base = flat(i18n._load(i18n.BASE))
        for lang in i18n.available() - {i18n.BASE}:
            for key, text in flat(i18n._load(lang)).items():
                self.assertIn(key, base, f'{lang}: лишний ключ {key}')
                self.assertEqual(set(PLACE.findall(text)), set(PLACE.findall(base[key])), f'{lang}: {key}')

    def test_pick_and_fallback(self):
        self.assertEqual(i18n.pick('de_AT', {'ru_RU', 'de_DE'}), 'de_DE')
        self.assertEqual(i18n.pick('ru_RU', {'ru_RU'}), 'ru_RU')
        self.assertIsNone(i18n.pick('fr_FR', {'ru_RU'}))
        self.assertEqual(i18n._lookup({'a': {'b': 'x'}}, 'a.b'), 'x')
        self.assertIsNone(i18n._lookup({'a': {'b': 'x'}}, 'a.c'))
        self.assertEqual(i18n.tr('нет.такого.ключа'), 'нет.такого.ключа')
        self.assertEqual(i18n.tr('progress.step', done=1, total=2), 'Шаг 1 из 2')

    def test_news_translation_only_in_russian(self):
        from unittest import mock
        from keepup import translate
        with mock.patch('shutil.which', return_value='/usr/bin/trans'):
            with mock.patch.dict(os.environ, {'KEEPUP_LANG': 'en_US'}):
                self.assertEqual(i18n.language(), 'en_US')
                self.assertFalse(translate.available())
            with mock.patch.dict(os.environ, {'KEEPUP_LANG': 'ru_RU'}):
                self.assertTrue(translate.available())
            with mock.patch.dict(os.environ, {'KEEPUP_LANG': 'fr_FR'}):
                self.assertEqual(i18n.language(), 'ru_RU')

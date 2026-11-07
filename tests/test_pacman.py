import os
import tempfile
import unittest

from keepup import news, pacman


class Versions(unittest.TestCase):
    def test_order(self):
        cases = [('1.0', '1.0-1', 0), ('2:1.0', '1:9.9', 1), ('7.2.10-1', '7.3.0-5', -1), ('1.0a', '1.0', -1),
                 ('1.0.a', '1.0', 1), ('1.2.3.r4.gabc-1', '1.2.3-1', 1), ('3:26.2.4-1', '3:26.2.5-1', -1),
                 ('1.0-1.1', '1.0-1', 1), ('001.2', '1.2', 0)]
        for a, b, want in cases:
            got = pacman.vercmp(a, b)
            self.assertEqual((got > 0) - (got < 0), want, (a, b))


class Parsing(unittest.TestCase):
    def test_checkupdates(self):
        ups = pacman.parse_checkupdates('mesa 3:26.2.5-1 -> 3:26.2.6-1\nnano 9.2-1.1 -> 9.3-1.1\nмусор\n')
        self.assertEqual([(u.name, u.needs_reboot) for u in ups], [('mesa', True), ('nano', False)])

    def test_cache_name(self):
        self.assertEqual(pacman.parse_cache_name('linux-cachyos-headers-7.3.0-5-x86_64_v4.pkg.tar.zst'),
                         ('linux-cachyos-headers', '7.3.0-5', 'x86_64_v4'))
        self.assertEqual(pacman.parse_cache_name('mesa-3:26.2.4-1-x86_64.pkg.tar.zst'), ('mesa', '3:26.2.4-1', 'x86_64'))
        self.assertIsNone(pacman.parse_cache_name('mesa-3:26.2.4-1-x86_64.pkg.tar.zst.sig'))
        self.assertIsNone(pacman.parse_cache_name('download-abc'))

    def test_desc(self):
        f = pacman.parse_desc('%NAME%\nlinux-cachyos\n\n%VERSION%\n7.2.10-1\n\n%BASE%\nlinux-cachyos\n')
        self.assertEqual(f['VERSION'], ['7.2.10-1'])

    def test_log(self):
        log = pacman.parse_log('[2026-10-29T23:13:21+0300] [ALPM] downgraded linux-cachyos (7.3.0-5 -> 7.2.10-1)\n'
                               '[2026-10-29T23:13:28+0300] [ALPM-SCRIPTLET] Building initramfs\n')
        self.assertEqual(len(log), 1)
        self.assertEqual((log[0].action, log[0].name), ('downgraded', 'linux-cachyos'))


class Related(unittest.TestCase):
    def setUp(self):
        mk = pacman.Installed
        self.pkgs = {n: mk(n, '1', b) for n, b in [('linux-cachyos', 'linux-cachyos'), ('linux-cachyos-headers', 'linux-cachyos'),
                                                   ('mesa', 'mesa'), ('vulkan-radeon', 'mesa'), ('lib32-mesa', 'lib32-mesa'),
                                                   ('lib32-vulkan-radeon', 'lib32-mesa'), ('nano', 'nano')]}

    def test_kernel_group(self):
        self.assertEqual(pacman.related('linux-cachyos', self.pkgs), ['linux-cachyos', 'linux-cachyos-headers'])

    def test_lib32_pair(self):
        self.assertEqual(set(pacman.related('mesa', self.pkgs)), {'mesa', 'vulkan-radeon', 'lib32-mesa', 'lib32-vulkan-radeon'})

    def test_rollback_files(self):
        with tempfile.TemporaryDirectory() as d:
            for f in ('mesa-3:26.2.4-1-x86_64.pkg.tar.zst', 'vulkan-radeon-3:26.2.4-1-x86_64.pkg.tar.zst'):
                open(os.path.join(d, f), 'w').close()
            files = pacman.rollback_files(['mesa', 'vulkan-radeon', 'nano'], '3:26.2.4-1', self.pkgs, pacman.cache(d))
            self.assertIsNone(files['nano'])
            self.assertTrue(files['mesa'].endswith('mesa-3:26.2.4-1-x86_64.pkg.tar.zst'))


class News(unittest.TestCase):
    FEED = ('<rss><channel><item><title>NVIDIA 590 driver drops Pascal support</title><link>https://a/1</link>'
            '<pubDate>Sat, 20 Dec 2025 00:00:00 +0000</pubDate><description>&lt;p&gt;The nvidia-open package&lt;/p&gt;'
            '&lt;p&gt;Use mesa instead.&lt;/p&gt;</description></item></channel></rss>')

    def test_parse_and_mark(self):
        items = news.mark(news.parse(self.FEED), ['mesa', 'nvidia-open', 'nvidia', 'xz'])
        self.assertEqual(items[0].text, 'The nvidia-open package\n\nUse mesa instead.')
        self.assertEqual(items[0].touches, ['mesa', 'nvidia-open', 'nvidia'])


if __name__ == '__main__':
    unittest.main()

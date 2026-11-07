import unittest

from updater import flatpak, pacman


class Flatpak(unittest.TestCase):
    def test_list(self):
        apps = flatpak.parse_list('dev.vencord.Vesktop\tVesktop\t1.6.7\tstable\tflathub\tsystem\nbroken line\n')
        self.assertEqual([(a.app_id, a.installation) for a in apps], [('dev.vencord.Vesktop', 'system')])

    def test_updates(self):
        self.assertEqual(flatpak.parse_updates('dev.vencord.Vesktop\t1.6.9\norg.x.App\t\n'),
                         {'dev.vencord.Vesktop': '1.6.9', 'org.x.App': ''})

    def test_log(self):
        text = ('        Commit: aaa\n        Parent: bbb\n       Subject: Vesktop 1.6.9 (#85)\n          Date: 2026-11-06 17:34:46 +0000\n'
                '       History: \n\n        Commit: bbb\n       Subject: fix\n          Date: 2026-10-23 19:31:17 +0000\n')
        self.assertEqual([(c.commit, c.subject) for c in flatpak.parse_log(text)], [('aaa', 'Vesktop 1.6.9 (#85)'), ('bbb', 'fix')])


class Checkout(unittest.TestCase):
    def test_system_needs_root_and_only_app(self):
        app = flatpak.App('dev.vencord.Vesktop', 'Vesktop', '1.6.7', 'stable', 'flathub', 'system')
        cmd = flatpak.checkout_command(app, 'abc')
        self.assertEqual(cmd[:3], ['pkexec', 'flatpak', 'update'])
        self.assertIn('--no-deps', cmd)
        self.assertIn('--no-related', cmd)
        user = flatpak.App('x', 'X', '1', 'stable', 'flathub', 'user')
        self.assertEqual(flatpak.checkout_command(user, 'abc')[0], 'flatpak')


class Aur(unittest.TestCase):
    def test_devel_version(self):
        ups = pacman.parse_checkupdates('wine-discord-ipc-bridge-git 0.0.3.0.g86fcbdd-1 -> latest-commit\n')
        self.assertEqual((ups[0].name, ups[0].new), ('wine-discord-ipc-bridge-git', 'latest-commit'))


if __name__ == '__main__':
    unittest.main()


class Kernels(unittest.TestCase):
    SL = ('cachyos-znver4 linux-cachyos 7.3.0-5 [installed: 7.2.10-1]\ncachyos-znver4 linux-cachyos-headers 7.3.0-5\n'
          'cachyos-znver4 linux-cachyos-r8125 7.3.0-5\ncachyos-znver4 linux-cachyos-lts 6.18.55-2 [installed]\n'
          'cachyos-znver4 linux-cachyos-lts-headers 6.18.55-2\ncachyos-znver4 linux-cachyos-lts-r8125 6.18.55-2\n'
          'cachyos-znver4 linux-cachyos-bore 7.2.10-1\ncachyos-znver4 linux-cachyos-bore-headers 7.2.10-1\n'
          'core linux-firmware 20261101-1\ncore linux-api-headers 7.0-1\n')

    def test_repo_kernels(self):
        from updater import kernels
        sync = kernels.parse_sync_list(self.SL)
        self.assertEqual(kernels.repo_kernels(sync), ['linux-cachyos', 'linux-cachyos-bore', 'linux-cachyos-lts'])
        self.assertTrue(sync['linux-cachyos'][2])

    def test_modules(self):
        from updater import kernels
        installed = {'linux-cachyos', 'linux-cachyos-headers', 'linux-cachyos-r8125', 'linux-cachyos-lts', 'linux-cachyos-lts-headers'}
        suffixes = kernels.module_suffixes(installed, ['linux-cachyos', 'linux-cachyos-lts'])
        self.assertEqual(suffixes, ['r8125'])
        sync = kernels.parse_sync_list(self.SL)
        self.assertEqual(kernels.modules_for('linux-cachyos-lts', suffixes, sync), ['linux-cachyos-lts-r8125'])
        self.assertEqual(kernels.modules_for('linux-cachyos-bore', suffixes, sync), [])

    def test_boot_order(self):
        from updater import kernels
        order = kernels.parse_boot_order('ESP_PATH="/boot"\nBOOT_ORDER="*, *lts, *fallback, Snapshots"\n')
        self.assertEqual(order, ['*', '*lts', '*fallback', 'Snapshots'])
        self.assertIsNone(kernels.default_kernel(order, ['linux-cachyos', 'linux-cachyos-lts']))
        self.assertEqual(kernels.default_kernel(order, ['linux-cachyos']), 'linux-cachyos')
        new = kernels.new_order(order, 'linux-cachyos-lts')
        self.assertEqual(new, ['linux-cachyos-lts', '*', '*lts', '*fallback', 'Snapshots'])
        self.assertEqual(kernels.default_kernel(new, ['linux-cachyos', 'linux-cachyos-lts']), 'linux-cachyos-lts')
        self.assertEqual(kernels.new_order(new, 'linux-cachyos'), ['linux-cachyos', '*', '*lts', '*fallback', 'Snapshots'])


class AppStream(unittest.TestCase):
    XML = ('<component><id>org.x.App</id><name>App</name><name xml:lang="ru">Прил</name><summary>Does</summary>'
           '<description><p>One  two</p><p xml:lang="ru">Раз</p><ul><li>a</li><li xml:lang="ru">б</li></ul></description>'
           '<icon type="cached" width="128" height="128">org.x.App.png</icon>'
           '<screenshots><screenshot><caption>Main</caption><image type="thumbnail" width="624">https://a/624.png</image>'
           '<image type="thumbnail" width="752">https://a/752.png</image></screenshot></screenshots>'
           '<releases><release version="2.0" date="2026-11-06"><description><p>New</p></description></release></releases></component>')

    def test_component(self):
        import xml.etree.ElementTree as ET
        from updater import appstream
        info = appstream.parse_component(ET.fromstring(self.XML), 'ru')
        self.assertEqual((info.name, info.summary), ('Прил', 'Does'))
        self.assertEqual(info.description, 'Раз\n\n• б')
        self.assertEqual(info.screenshots, [('Main', 'https://a/752.png')])
        self.assertEqual((info.releases[0].version, info.releases[0].text), ('2.0', 'New'))
        en = appstream.parse_component(ET.fromstring(self.XML), 'de')
        self.assertEqual(en.description, 'One two\n\n• a')


class Uninstall(unittest.TestCase):
    def test_commands(self):
        app = flatpak.App('ai.storyteller.artcraft-launcher', 'ArtCraft Launcher', '0.2.1', 'stable', 'artcraft-launcher-origin', 'system')
        self.assertEqual(flatpak.uninstall_commands(app), [['flatpak', 'uninstall', '-y', '--noninteractive', '--system',
                                                            'ai.storyteller.artcraft-launcher//stable']])
        both = flatpak.uninstall_commands(app, delete_data=True, unused=True)
        self.assertIn('--delete-data', both[0])
        self.assertEqual(both[1][-1], '--unused')

import unittest

from keepup import flatpak, pacman, search


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
        from keepup import kernels
        sync = kernels.parse_sync_list(self.SL)
        self.assertEqual(kernels.repo_kernels(sync), ['linux-cachyos', 'linux-cachyos-bore', 'linux-cachyos-lts'])
        self.assertTrue(sync['linux-cachyos'][2])

    def test_modules(self):
        from keepup import kernels
        installed = {'linux-cachyos', 'linux-cachyos-headers', 'linux-cachyos-r8125', 'linux-cachyos-lts', 'linux-cachyos-lts-headers'}
        suffixes = kernels.module_suffixes(installed, ['linux-cachyos', 'linux-cachyos-lts'])
        self.assertEqual(suffixes, ['r8125'])
        sync = kernels.parse_sync_list(self.SL)
        self.assertEqual(kernels.modules_for('linux-cachyos-lts', suffixes, sync), ['linux-cachyos-lts-r8125'])
        self.assertEqual(kernels.modules_for('linux-cachyos-bore', suffixes, sync), [])

    def test_boot_order(self):
        from keepup import kernels
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
        from keepup import appstream
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


class AurCard(unittest.TestCase):
    def test_qi(self):
        from keepup import aur
        f = aur.parse_qi('Name            : google-chrome\nOptional Deps   : pipewire: WebRTC\n                  kdialog: dialogs\nInstalled Size  : 441.65 MiB\n')
        self.assertEqual(f['Installed Size'], '441.65 MiB')
        self.assertEqual(f['Optional Deps'], 'pipewire: WebRTC\nkdialog: dialogs')

    def test_feed(self):
        from keepup import aur
        feed = ("<feed xmlns='http://www.w3.org/2005/Atom'><entry><title>upgpkg: x 2-1</title><updated>2026-11-03T23:07:52Z</updated>"
                "<link rel='alternate' href='http://aur/commit/?id=1'/><content type='text'>\nupstream release\n</content></entry></feed>")
        self.assertEqual(aur.parse_feed(feed), [('upgpkg: x 2-1', '2026-11-03', 'upstream release', 'http://aur/commit/?id=1')])

    def test_commands(self):
        from keepup import aur
        self.assertEqual(aur.remove_command('x'), ['pkexec', 'pacman', '-Rns', '--noconfirm', 'x'])
        self.assertEqual(aur.remove_command('x', deps=False)[2], '-Rn')


class AurBuild(unittest.TestCase):
    def test_commit_and_commands(self):
        from keepup import aur
        self.assertEqual(aur.commit_of('http://aur.archlinux.org/cgit/aur.git/commit/?id=a3cd9ff99d3a&h=google-chrome'), 'a3cd9ff99d3a')
        self.assertIsNone(aur.commit_of('https://aur.archlinux.org/packages/x'))
        cmds = aur.build_commands('x-bin', 'abc123', {'x-bin'})
        self.assertEqual(cmds[2][-1], 'abc123')
        self.assertIn('makepkg', cmds[4])
        self.assertTrue(callable(cmds[-1]))


class ReleaseNotes(unittest.TestCase):
    def test_srcinfo_and_changes(self):
        from keepup import notes
        old = notes.parse_srcinfo('pkgbase = x\n\tpkgver = 8.0.4\n\tpkgrel = 1\n\tdepends = gtk3\n\tdepends = libxss\n')
        new = notes.parse_srcinfo('pkgbase = x\n\tpkgver = 8.1.0\n\tpkgrel = 1\n\tdepends = gtk3\n\tdepends = libxtst\n\tepoch = 1\n')
        self.assertEqual(notes.version_of(new), '1:8.1.0-1')
        self.assertEqual(notes.compare_builds(old, new), ['новая зависимость: libxtst', 'убрана зависимость: libxss'])
        self.assertEqual(notes._plain_version('1:8.1.0-2'), '8.1.0')


class Markdown(unittest.TestCase):
    def test_markup_parses(self):
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Adw', '1')
        from gi.repository import Pango
        from keepup.window import markdown_markup
        for text in ('- ***Android 7 and 8 unavailable***', '**a *b* c**', '## H & <x>', '`code` and *i*'):
            Pango.parse_markup(markdown_markup(text), -1, '\0')


class KindIcons(unittest.TestCase):
    def test_kinds(self):
        cases = {'linux-cachyos': 'computer-symbolic', 'linux-cachyos-r8125': 'computer-symbolic', 'lib32-mesa': 'video-display-symbolic',
                 'vulkan-radeon': 'video-display-symbolic', 'noto-fonts-emoji': 'font-x-generic-symbolic', 'python-pydantic': 'text-x-script-symbolic',
                 'chaotic-keyring': 'security-high-symbolic', 'harfbuzz': 'package-x-generic-symbolic', 'libarchive': 'application-x-addon-symbolic'}
        for name, icon in cases.items():
            self.assertEqual(pacman.kind_icon(name), icon, name)


class SingleUpdate(unittest.TestCase):
    def test_command(self):
        self.assertEqual(pacman.install_urls_command(['https://m/x.pkg.tar.zst']),
                         ['pkexec', 'pacman', '-U', '--noconfirm', 'https://m/x.pkg.tar.zst'])


class Search(unittest.TestCase):
    def test_filter_rows_groups_and_expanders(self):
        import os
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Adw', '1')
        from gi.repository import Adw, Gtk
        if not Gtk.init_check():
            self.skipTest('нет дисплея')
        from keepup.window import WidePage, _filter_widget, expander, row
        page = WidePage()
        updates = Adw.PreferencesGroup(title='Обновления')
        tg, mesa = row('telegram-desktop', '7.3.3 → 7.4.1'), row('mesa', '26.2.5 → 26.2.6')
        updates.add(tg)
        updates.add(mesa)
        history = Adw.PreferencesGroup(title='История')
        day = expander('08.11.2026', 'обновлён: 2')
        inner_tg, inner_x = row('telegram-desktop', 'обновлён'), row('nano', 'обновлён')
        day.add_row(inner_tg)
        day.add_row(inner_x)
        history.add(day)
        other = Adw.PreferencesGroup(title='Прочее')
        other.add(row('linux-cachyos', ''))
        for g in (updates, history, other):
            page.add(g)
        _filter_widget(page, 'telegram')
        self.assertTrue(tg.get_visible())
        self.assertFalse(mesa.get_visible())
        self.assertTrue(day.get_visible() and day.get_expanded())
        self.assertTrue(inner_tg.get_visible())
        self.assertFalse(inner_x.get_visible())
        self.assertFalse(other.get_visible())
        _filter_widget(page, '')
        self.assertTrue(mesa.get_visible() and other.get_visible() and inner_x.get_visible())


class Translate(unittest.TestCase):
    def test_protect(self):
        from keepup import translate
        names = {'php-legacy', 'ca-certificates-mozilla'}
        text = 'Update to php-legacy ≥ 8.3.36 and/or re-enroll the man-in-the-middle `pacman -Syu` https://a.b/c\n# mkinitcpio -P'
        masked, kept = translate.protect(text, names)
        self.assertEqual(kept, ['php-legacy', '8.3.36', '`pacman -Syu`', 'https://a.b/c', '# mkinitcpio -P'])
        self.assertIn('and/or', masked)
        self.assertIn('re-enroll', masked)
        self.assertIn('man-in-the-middle', masked)
        self.assertEqual(translate.restore(masked, kept), text)


class NewsArchive(unittest.TestCase):
    def test_roundtrip_keeps_translation(self):
        import tempfile
        from datetime import datetime, timezone
        from keepup import news
        with tempfile.TemporaryDirectory() as d:
            old = news.ARCHIVE
            news.ARCHIVE = d
            try:
                item = news.Item('Title', 'https://a/1', datetime(2026, 11, 1, tzinfo=timezone.utc), 'Text')
                news.save(item)
                item.title_ru, item.text_ru = 'Заголовок', 'Текст'
                news.save(item)
                # Та же статья из свежей ленты без перевода не стирает сохранённый перевод.
                news.save(news.Item('Title', 'https://a/1', item.date, 'Text'), only_new=True)
                news.save(news.Item('Other', 'https://a/2', datetime(2026, 11, 5, tzinfo=timezone.utc), 'B'), only_new=True)
                items = news.archived()
                self.assertEqual([i.link for i in items], ['https://a/2', 'https://a/1'])
                self.assertEqual((items[1].title_ru, items[1].text_ru), ('Заголовок', 'Текст'))
            finally:
                news.ARCHIVE = old


class Progress(unittest.TestCase):
    def test_stages_and_overall(self):
        from keepup import progress
        s, overall, values = progress.State(), progress.Overall(), []
        for line in [':: Синхронизация баз данных пакетов...', 'загрузка файлов пакетов...', 'проверка целостности пакета...',
                     'обновление amneziavpn-bin...', ':: Запуск post-transaction hooks...', '(3/6) Arming ConditionNeedsUpdate...',
                     '(6/6) Updating the desktop file MIME type cache...']:
            progress.feed(s, line)
            values.append(overall.update(s))
        self.assertEqual(s.stage, 'Настройка системы')
        self.assertEqual(s.detail, 'Шаг 6 из 6')
        self.assertEqual(values, sorted(values))
        self.assertAlmostEqual(values[-1], 1.0)
        a = progress.State()
        progress.feed(a, 'обновление amneziavpn-bin...')
        self.assertEqual((a.stage, a.detail), ('Установка', 'Обновление: amneziavpn-bin'))


class FindNew(unittest.TestCase):
    def test_pacman_ss(self):
        text = ('cachyos-extra-znver4/obs-studio 33.0.1-1.1 [installed]\n    Free, open source software for live streaming\n'
                'extra/obs-studio 33.0.1-1\n    Free, open source software for live streaming\n')
        found = search.parse_ss(text)
        self.assertEqual([(f.repo, f.name, f.version) for f in found],
                         [('cachyos-extra-znver4', 'obs-studio', '33.0.1-1.1'), ('extra', 'obs-studio', '33.0.1-1')])
        self.assertEqual(found[0].summary, 'Free, open source software for live streaming')

    def test_rank(self):
        names = ['obsidian', 'libobs', 'obs-studio', 'obs', 'something']
        items = [search.Found('repo', n, title=n, summary='obs' if n == 'something' else '') for n in names]
        self.assertEqual([f.name for f in sorted(items, key=lambda f: search.rank(f, 'obs'))],
                         ['obs', 'obs-studio', 'obsidian', 'libobs', 'something'])

    def test_flathub_all_words(self):
        apps = [search.Found('flatpak', 'org.kde.kdenlive', 'Video editor', title='Kdenlive', data={'text': 'org.kde.kdenlive'}),
                search.Found('flatpak', 'org.gimp.GIMP', 'Image editor', title='GIMP', data={'text': 'org.gimp.gimp'})]
        self.assertEqual([a.name for a in search.flathub('video editor', apps)], ['org.kde.kdenlive'])

    def test_install_commands(self):
        cmd = search.repo_install_command(['https://m/x-1-1-x86_64.pkg.tar.zst'], ['libx'])
        self.assertEqual(cmd[:2], ['pkexec', '/usr/bin/python3'])
        self.assertTrue(cmd[2].endswith('helpers/install_packages.py'))
        self.assertEqual(cmd[3:], ['--dep=libx', 'https://m/x-1-1-x86_64.pkg.tar.zst'])
        self.assertIn('--needed', search.aur_install_command('x'))
        item = search.Found('flatpak', 'org.gimp.GIMP', repo='flathub', data={'installation': 'system'})
        self.assertEqual(search.flatpak_install_command(item)[-3:], ['--system', 'flathub', 'org.gimp.GIMP'])


class AurPolite(unittest.TestCase):
    def test_one_request_per_word(self):
        from unittest import mock
        import io, json
        calls = []

        def fake(req, timeout=0):
            calls.append(req.full_url)
            return io.BytesIO(json.dumps({'results': [{'Name': 'telegram-x', 'Description': 'desktop client'}]}).encode())
        search._aur_answers.clear()
        with mock.patch('urllib.request.urlopen', fake):
            self.assertEqual(search.aur('te'), [])
            search.aur('telegram')
            search.aur('telegram desktop')
            search.aur('telegram')
        self.assertEqual(len(calls), 1)
        self.assertIsNone(search.aur_key('vs'))

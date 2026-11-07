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

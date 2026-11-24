import os
import tempfile
import unittest
from unittest import mock

from keepup import appimages

DESKTOP = """[Desktop Entry]
Name=HUE Club
Name[ru]=Клуб HUE
Exec=AppRun --no-sandbox %U
Terminal=false
Type=Application
Icon=hue-club-launcher
StartupWMClass=HUE Club
X-AppImage-Version=1.0.8
DBusActivatable=true

[Desktop Action New]
Name=Second
Exec=AppRun --new
"""


class Desktop(unittest.TestCase):
    def test_parse_only_main_group(self):
        e = appimages.parse_desktop(DESKTOP)
        self.assertEqual(appimages.get(e, 'Name'), 'HUE Club')
        self.assertEqual(appimages.get(e, 'Name', 'ru_RU'), 'Клуб HUE')
        self.assertEqual(appimages.get(e, 'Exec'), 'AppRun --no-sandbox %U')

    def test_exec_args(self):
        self.assertEqual(appimages.exec_args('AppRun --no-sandbox %U'), '--no-sandbox %U')
        self.assertEqual(appimages.exec_args('"my app" %F'), '%F')
        self.assertEqual(appimages.exec_args('AppRun'), '')

    def test_make_desktop(self):
        text = appimages.make_desktop(appimages.parse_desktop(DESKTOP), '/home/u/a $b.AppImage', '/home/u/icon.png')
        lines = text.splitlines()
        self.assertIn('Exec="/home/u/a \\$b.AppImage" --no-sandbox %U', lines)
        self.assertIn('Icon=/home/u/icon.png', lines)
        self.assertIn('StartupWMClass=HUE Club', lines)
        self.assertIn(f'{appimages.MARK}=/home/u/a $b.AppImage', lines)
        self.assertNotIn('DBusActivatable=true', lines)
        self.assertNotIn('Exec=AppRun --new', lines)


class Files(unittest.TestCase):
    def test_is_appimage(self):
        with tempfile.TemporaryDirectory() as d:
            good, bad = os.path.join(d, 'a'), os.path.join(d, 'b')
            with open(good, 'wb') as f:
                f.write(b'\x7fELF\x02\x01\x01\x00AI\x02rest')
            with open(bad, 'wb') as f:
                f.write(b'\x7fELF\x02\x01\x01\x00\x00\x00\x00')
            self.assertTrue(appimages.is_appimage(good))
            self.assertFalse(appimages.is_appimage(bad))
            self.assertFalse(appimages.is_appimage(os.path.join(d, 'нет')))

    def test_desktop_name_keeps_image_name_unless_taken(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(appimages.desktop_name('hue-club-launcher.desktop', d), 'hue-club-launcher.desktop')
            with open(os.path.join(d, 'hue-club-launcher.desktop'), 'w') as f:
                f.write('[Desktop Entry]\nName=Чужой\n')
            self.assertEqual(appimages.desktop_name('hue-club-launcher.desktop', d), 'keepup-hue-club-launcher.desktop')
            with open(os.path.join(d, 'hue-club-launcher.desktop'), 'w') as f:
                f.write(f'[Desktop Entry]\nName=Свой\n{appimages.MARK}=/x\n')
            self.assertEqual(appimages.desktop_name('hue-club-launcher.desktop', d), 'hue-club-launcher.desktop')

    def test_find_icon_prefers_root_then_biggest(self):
        with tempfile.TemporaryDirectory() as root:
            for rel in ('usr/share/icons/hicolor/64x64/apps/x.png', 'usr/share/icons/hicolor/256x256/apps/x.png'):
                os.makedirs(os.path.dirname(os.path.join(root, rel)), exist_ok=True)
                with open(os.path.join(root, rel), 'wb') as f:
                    f.write(b'\x89PNG')
            with open(os.path.join(root, 'x'), 'wb') as f:
                f.write(b'\x7fELF same name as the icon')
            self.assertTrue(appimages.find_icon(root, 'x').endswith('256x256/apps/x.png'))
            with open(os.path.join(root, 'x.png'), 'wb') as f:
                f.write(b'\x89PNG')
            self.assertEqual(appimages.find_icon(root, 'x'), os.path.join(root, 'x.png'))

    def test_add_and_remove(self):
        with tempfile.TemporaryDirectory() as home:
            src = os.path.join(home, 'App-1.AppImage')
            with open(src, 'wb') as f:
                f.write(b'\x7fELF\x02\x01\x01\x00AI\x02body')
            icon = os.path.join(home, 'i.png')
            with open(icon, 'wb') as f:
                f.write(b'\x89PNG....')
            tmp = tempfile.mkdtemp(dir=home)
            fake = (appimages.parse_desktop(DESKTOP), 'hue-club-launcher.desktop', icon, '1.0.8', tmp)
            with mock.patch.dict(os.environ, {'XDG_DATA_HOME': home}), mock.patch.object(appimages, 'read_image', return_value=fake), \
                    mock.patch.object(appimages, '_refresh_menu'):
                app, replaced = appimages.add(src)
                self.assertFalse(replaced)
                self.assertFalse(os.path.exists(src))
                self.assertTrue(os.access(app.path, os.X_OK))
                self.assertTrue(app.icon.endswith('icon.png'))
                self.assertEqual(app.desktop, 'hue-club-launcher.desktop')
                self.assertEqual([a.key for a in appimages.installed()], ['hue-club-launcher'])
                appimages.remove(app)
                self.assertEqual(appimages.installed(), [])
                self.assertFalse(os.path.exists(appimages.desktop_path(app)))

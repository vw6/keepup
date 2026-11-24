import json
import os
import struct
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
                    mock.patch.object(appimages, '_refresh_menu'), mock.patch.object(appimages, 'window_class', return_value='hue-club'):
                app, replaced = appimages.add(src)
                self.assertFalse(replaced)
                self.assertFalse(os.path.exists(src))
                self.assertTrue(os.access(app.path, os.X_OK))
                self.assertTrue(app.icon.endswith('icon.png'))
                self.assertEqual(app.desktop, 'hue-club-launcher.desktop')
                with open(appimages.desktop_path(app), encoding='utf-8') as f:
                    self.assertIn('StartupWMClass=hue-club', f.read().splitlines())
                self.assertEqual([a.key for a in appimages.installed()], ['hue-club-launcher'])
                appimages.remove(app)
                self.assertEqual(appimages.installed(), [])
                self.assertFalse(os.path.exists(appimages.desktop_path(app)))


class Electron(unittest.TestCase):
    def test_app_id(self):
        self.assertEqual(appimages.electron_app_id({'name': 'hue-club-launcher', 'productName': 'HUE Club'}), 'hue-club')
        self.assertEqual(appimages.electron_app_id({'name': 'x', 'desktopName': 'hue-club-launcher.desktop'}), 'hue-club-launcher')
        self.assertEqual(appimages.electron_app_id({'name': 'Some App'}), 'some-app')

    def test_asar(self):
        pkg = json.dumps({'name': 'n', 'productName': 'P Q'}).encode()
        header = json.dumps({'files': {'main.js': {'size': 3, 'offset': '0'}, 'package.json': {'size': len(pkg), 'offset': '3'}}}).encode()
        pickle = struct.pack('<II', len(header) + 4, len(header)) + header
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, 'resources'))
            with open(os.path.join(root, 'resources', 'app.asar'), 'wb') as f:
                f.write(struct.pack('<II', 4, len(pickle)) + pickle + b'abc' + pkg)
            self.assertEqual(appimages.electron_package(root), {'name': 'n', 'productName': 'P Q'})
            self.assertIsNone(appimages.electron_package(os.path.join(root, 'нет')))

    def test_desktop_takes_window_class(self):
        text = appimages.make_desktop(appimages.parse_desktop(DESKTOP), '/a.AppImage', '', 'hue-club')
        self.assertIn('StartupWMClass=hue-club', text.splitlines())
        self.assertNotIn('StartupWMClass=HUE Club', text.splitlines())

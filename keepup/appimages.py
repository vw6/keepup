"""AppImage: файл переносится в свою скрытую папку, ярлык в меню и значок берутся из самого образа."""
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

from . import data_dir

MARK = 'X-KeepUp-AppImage'
EXTRACT_TIMEOUT = 60
ICON_EXTS = ('.png', '.svg', '.xpm')


def store_dir(*parts):
    return data_dir('appimages', *parts)


def apps_dir():
    return os.path.join(os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share')), 'applications')


@dataclass
class AppImage:
    key: str
    name: str
    comment: str
    version: str
    path: str
    icon: str
    desktop: str
    size: int
    added: float = 0.0


class AppImageError(Exception):
    pass


# Ярлык .desktop

def parse_desktop(text):
    """Строки группы [Desktop Entry] по порядку: [(ключ, значение)], ключи с переводом вроде Name[ru] как есть."""
    entries, inside = [], False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith('['):
            inside = s == '[Desktop Entry]'
            continue
        if inside and '=' in s and not s.startswith('#'):
            k, v = s.split('=', 1)
            entries.append((k.strip(), v.strip()))
    return entries


def get(entries, key, lang=''):
    """Значение ключа, сначала на языке lang (ru_RU → Name[ru_RU], Name[ru]), потом без перевода."""
    d = dict(entries)
    names = []
    if lang:
        names += [f'{key}[{lang}]', f'{key}[{lang.split("_")[0]}]']
    names.append(key)
    return next((d[n] for n in names if d.get(n)), '')


def _quote(path):
    """Путь для Exec= по спецификации ярлыков: в двойных кавычках, \\ " ` $ экранируются."""
    return '"' + re.sub(r'([\\"`$])', r'\\\1', path) + '"'


def exec_args(value):
    """Всё в Exec= после самой программы (AppRun или имя файла): ключи и %U, %F остаются как были."""
    value = value.strip()
    if value.startswith('"'):
        end = value.find('"', 1)
        return value[end + 1:].strip() if end > 0 else ''
    return value.split(None, 1)[1] if ' ' in value else ''


def make_desktop(entries, path, icon):
    """Свой ярлык из ярлыка образа: Exec и TryExec на файл в нашей папке, значок файлом, пометка KeepUp."""
    skip = {'Exec', 'TryExec', 'Icon', 'DBusActivatable', MARK}
    out = ['[Desktop Entry]']
    seen = set()
    for k, v in entries:
        if k in skip or k.startswith('X-AppImage-Integrate'):
            continue
        out.append(f'{k}={v}')
        seen.add(k)
    if 'Type' not in seen:
        out.insert(1, 'Type=Application')
    args = exec_args(get(entries, 'Exec'))
    out.append(f'Exec={_quote(path)}' + (f' {args}' if args else ''))
    out.append(f'TryExec={path}')
    if icon:
        out.append(f'Icon={icon}')
    out.append(f'{MARK}={path}')
    return '\n'.join(out) + '\n'


# Извлечение из образа

def extract(path, dest, patterns):
    """Вытаскивает из образа только нужные файлы. Свою разметку образа (squashfs внутри ELF) разбирает его же
    программа запуска: ключ --appimage-extract есть у всех образов второго типа и FUSE ему не нужен."""
    root = os.path.join(dest, 'squashfs-root')
    for pattern in patterns:
        try:
            subprocess.run([path, '--appimage-extract', pattern], cwd=dest, stdin=subprocess.DEVNULL,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=EXTRACT_TIMEOUT,
                           env={**os.environ, 'APPIMAGE_EXTRACT_AND_RUN': ''})
        except (OSError, subprocess.TimeoutExpired) as e:
            raise AppImageError(str(e)) from e
    return root


def is_appimage(path):
    """Образ второго типа: ELF и метка AI\\x02 по смещению 8."""
    try:
        with open(path, 'rb') as f:
            head = f.read(11)
    except OSError:
        return False
    return head[:4] == b'\x7fELF' and head[8:11] == b'AI\x02'


def find_icon(root, name):
    """Значок по имени из Icon=: в корне образа, потом в теме hicolor внутри (самый крупный), потом .DirIcon."""
    found = []
    if name:
        base = os.path.basename(name)
        # Имя без расширения в корне образа обычно сама программа (Icon=hue-club-launcher и такой же исполняемый файл),
        # поэтому только файлы картинок.
        for ext in ICON_EXTS:
            found += glob.glob(os.path.join(root, glob.escape(base) + ext))
        if not found:
            sizes = glob.glob(os.path.join(root, 'usr/share/icons/hicolor/*/apps', glob.escape(base) + '.*'))
            found = sorted(sizes, key=_icon_size, reverse=True)
    dir_icon = os.path.join(root, '.DirIcon')
    if os.path.exists(dir_icon):
        found.append(dir_icon)
    return next((f for f in found if os.path.isfile(f) and is_image(f)), '')


def is_image(path):
    try:
        with open(path, 'rb') as f:
            head = f.read(512)
    except OSError:
        return False
    return head.startswith(b'\x89PNG') or b'<svg' in head or head.startswith(b'/* XPM */')


def _icon_size(path):
    m = re.search(r'/(\d+)x\d+/', path)
    return int(m.group(1)) if m else (10000 if '/scalable/' in path else 0)


def icon_ext(path):
    with open(path, 'rb') as f:
        head = f.read(512)
    if head.startswith(b'\x89PNG'):
        return '.png'
    if b'<svg' in head or head.lstrip().startswith(b'<?xml'):
        return '.svg'
    ext = os.path.splitext(path)[1].lower()
    return ext if ext in ICON_EXTS else '.png'


def metainfo_version(root):
    for f in glob.glob(os.path.join(root, 'usr/share/metainfo/*.xml')) + glob.glob(os.path.join(root, 'usr/share/appdata/*.xml')):
        try:
            with open(f, encoding='utf-8', errors='replace') as fh:
                m = re.search(r'<release[^>]*\sversion="([^"]+)"', fh.read())
        except OSError:
            continue
        if m:
            return m.group(1)
    return ''


def read_image(path, lang=''):
    """Ярлык, значок и версия образа: (строки ярлыка, имя ярлыка, путь к значку во временной папке, версия, папка)."""
    if not is_appimage(path):
        raise AppImageError('not-appimage')
    os.chmod(path, os.stat(path).st_mode | 0o111)
    tmp = tempfile.mkdtemp(prefix='keepup-appimage-')
    root = extract(path, tmp, ['*.desktop'])
    desktops = sorted(glob.glob(os.path.join(root, '*.desktop'))) or sorted(glob.glob(os.path.join(root, '**/*.desktop'), recursive=True))
    if not desktops:
        shutil.rmtree(tmp, ignore_errors=True)
        raise AppImageError('no-desktop')
    with open(desktops[0], encoding='utf-8', errors='replace') as f:
        entries = parse_desktop(f.read())
    icon_name = get(entries, 'Icon')
    patterns = ['.DirIcon', 'usr/share/metainfo/*', 'usr/share/appdata/*']
    if icon_name:
        base = os.path.basename(icon_name)
        patterns = [base, f'{base}.*', f'usr/share/icons/hicolor/*/apps/{base}.*'] + patterns
    extract(path, tmp, patterns)
    version = get(entries, 'X-AppImage-Version') or metainfo_version(root)
    return entries, os.path.basename(desktops[0]), find_icon(root, icon_name), version, tmp


# Своя папка

def slug(text):
    s = re.sub(r'[^A-Za-z0-9._-]+', '-', text).strip('-.')
    return s or 'appimage'


def desktop_name(image_desktop, apps=None):
    """Имя ярлыка в меню. Берём имя ярлыка из образа: по нему GNOME узнаёт окно программы и ставит её значок в панель.
    Если такое имя уже занято чужим ярлыком, берём своё, и тогда окно узнаётся по StartupWMClass."""
    apps = apps or apps_dir()
    name = image_desktop if image_desktop.endswith('.desktop') else image_desktop + '.desktop'
    target = os.path.join(apps, name)
    if not os.path.exists(target) or _ours(target):
        return name
    return f'keepup-{name}'


def _ours(desktop):
    try:
        with open(desktop, encoding='utf-8', errors='replace') as f:
            return any(k == MARK for k, _ in parse_desktop(f.read()))
    except OSError:
        return False


def _manifest(key):
    return store_dir(key, 'app.json')


def installed():
    out = []
    for f in sorted(glob.glob(store_dir('*', 'app.json'))):
        try:
            with open(f, encoding='utf-8') as fh:
                d = json.load(fh)
            a = AppImage(**{k: d.get(k, '') for k in ('key', 'name', 'comment', 'version', 'path', 'icon', 'desktop')},
                         size=0, added=d.get('added', 0.0))
        except (OSError, ValueError, TypeError):
            continue
        a.size = os.path.getsize(a.path) if os.path.exists(a.path) else 0
        out.append(a)
    return sorted(out, key=lambda a: a.name.lower())


def add(source, lang='', move=True):
    """Ставит образ: файл в ~/.local/share/keepup/appimages/<ключ>/, значок рядом, ярлык в меню.
    Тот же образ (по имени ярлыка) заменяет прежний: так ставится новая версия. Возвращает AppImage и признак замены."""
    source = os.path.abspath(source)
    entries, image_desktop, icon_src, version, tmp = read_image(source, lang)
    try:
        key = slug(image_desktop[:-8] if image_desktop.endswith('.desktop') else image_desktop)
        folder = store_dir(key)
        old = None
        if os.path.exists(_manifest(key)):
            old = next((a for a in installed() if a.key == key), None)
        os.makedirs(folder, exist_ok=True)
        target = os.path.join(folder, os.path.basename(source))
        if old and old.path != target and os.path.exists(old.path) and os.path.abspath(old.path) != source:
            os.remove(old.path)
        if os.path.abspath(target) != source:
            if move:
                try:
                    shutil.move(source, target)
                except PermissionError:
                    shutil.copy2(source, target)
            else:
                shutil.copy2(source, target)
        os.chmod(target, 0o755)
        icon = ''
        if icon_src:
            for f in glob.glob(os.path.join(folder, 'icon.*')):
                os.remove(f)
            icon = os.path.join(folder, 'icon' + icon_ext(icon_src))
            shutil.copyfile(icon_src, icon)
        name = desktop_name(image_desktop) if not old else old.desktop
        desktop = os.path.join(apps_dir(), name)
        os.makedirs(apps_dir(), exist_ok=True)
        with open(desktop + '.tmp', 'w', encoding='utf-8') as f:
            f.write(make_desktop(entries, target, icon))
        os.chmod(desktop + '.tmp', 0o755)
        os.replace(desktop + '.tmp', desktop)
        app = AppImage(key, get(entries, 'Name', lang) or key, get(entries, 'Comment', lang), version, target, icon,
                       name, os.path.getsize(target), time.time())
        with open(_manifest(key), 'w', encoding='utf-8') as f:
            json.dump({k: getattr(app, k) for k in ('key', 'name', 'comment', 'version', 'path', 'icon', 'desktop', 'added')},
                      f, ensure_ascii=False, indent=1)
        _refresh_menu()
        return app, old is not None
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def remove(app):
    """Убирает ярлык из меню и папку образа целиком. Настройки самой программы в ~/.config не трогаются."""
    desktop = os.path.join(apps_dir(), app.desktop)
    if app.desktop and os.path.exists(desktop) and _ours(desktop):
        os.remove(desktop)
    folder = store_dir(app.key)
    if app.key and os.path.isdir(folder):
        shutil.rmtree(folder)
    _refresh_menu()


def desktop_path(app):
    return os.path.join(apps_dir(), app.desktop)


def _refresh_menu():
    if shutil.which('update-desktop-database'):
        subprocess.run(['update-desktop-database', '-q', apps_dir()], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

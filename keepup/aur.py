"""Пакеты не из репозиториев (AUR и прочие): список, обновления через paru и собранные paru версии для отката."""
import glob
import json
import os
import re
import shutil
import subprocess
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from .pacman import Update, parse_checkupdates
from . import USER_AGENT, cache_dir

PARU_CLONE = os.path.expanduser('~/.cache/paru/clone')


def available():
    return shutil.which('paru') is not None


def foreign():
    """Установленные пакеты, которых нет ни в одном подключённом репозитории: имя -> версия."""
    out = subprocess.run(['pacman', '-Qm'], capture_output=True, stdin=subprocess.DEVNULL, text=True)
    return dict(line.split(maxsplit=1) for line in out.stdout.splitlines() if ' ' in line)


def list_updates():
    """Обновления из AUR по данным paru; у пакетов -git вместо версии бывает «latest-commit»."""
    out = subprocess.run(['paru', '-Qua'], capture_output=True, stdin=subprocess.DEVNULL, text=True, timeout=120)
    # paru отвечает кодом 1 и пустым выводом, когда обновлений нет.
    if out.returncode not in (0, 1) or (out.returncode == 1 and out.stderr.strip() and not out.stdout.strip()):
        raise RuntimeError(out.stderr.strip() or 'paru завершился с ошибкой')
    return [Update(u.name, u.old, u.new) for u in parse_checkupdates(out.stdout)]


def build_dirs():
    """Каталоги, где paru оставляет собранные пакеты; их версии годятся для отката."""
    return sorted(d for d in glob.glob(os.path.join(PARU_CLONE, '*')) if os.path.isdir(d))


def upgrade_command():
    # Сборка идёт от пользователя, а установку paru просит через pkexec, то есть через окно пароля GNOME.
    return ['paru', '-Sua', '--noconfirm', '--skipreview', '--batchinstall', '--sudo', 'pkexec']


# Карточка пакета: сведения AUR, локальная база, иконка и описание из установленных файлов, изменения PKGBUILD.

AUR_RPC = 'https://aur.archlinux.org/rpc/v5/info'
AUR_FEED = 'https://aur.archlinux.org/cgit/aur.git/atom/?h={}'
ATOM = '{http://www.w3.org/2005/Atom}'


def rpc_info(names, timeout=20):
    """Сведения AUR о пакетах одним запросом: имя -> словарь ответа; пакетов, которых в AUR нет, в ответе нет."""
    if not names:
        return {}
    query = urllib.parse.urlencode([('arg[]', n) for n in names])
    req = urllib.request.Request(f'{AUR_RPC}?{query}', headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.load(resp)
    return {r['Name']: r for r in data.get('results', [])}


def parse_qi(text):
    """Поля pacman -Qi (вывод на C-локали); продолжения строк с отступом приклеиваются к полю."""
    fields, key = {}, None
    for line in text.splitlines():
        if line[:1].strip() and ':' in line:
            key, _, value = line.partition(':')
            key = key.strip()
            fields[key] = value.strip()
        elif key and line.strip():
            fields[key] += '\n' + line.strip()
    return fields


def local_info(name):
    out = subprocess.run(['pacman', '-Qi', name], capture_output=True, stdin=subprocess.DEVNULL, text=True, env={**os.environ, 'LC_ALL': 'C'})
    return parse_qi(out.stdout)


def files_of(names):
    """Файлы пакетов одним вызовом pacman -Ql: имя -> список путей."""
    out = subprocess.run(['pacman', '-Ql', *names], capture_output=True, stdin=subprocess.DEVNULL, text=True)
    files = {}
    for line in out.stdout.splitlines():
        name, _, path = line.partition(' ')
        files.setdefault(name, []).append(path)
    return files


def desktop_icon(paths):
    """Значок из первого .desktop пакета: имя из темы значков или путь к файлу."""
    for path in paths:
        if path.startswith('/usr/share/applications/') and path.endswith('.desktop'):
            try:
                with open(path, encoding='utf-8', errors='replace') as f:
                    for line in f:
                        if line.startswith('Icon='):
                            return line[5:].strip()
            except OSError:
                continue
    return ''


def metainfo_file(paths):
    for path in paths:
        if (path.startswith('/usr/share/metainfo/') or path.startswith('/usr/share/appdata/')) and path.endswith('.xml'):
            return path
    return ''


def parse_feed(text):
    """Изменения PKGBUILD из ленты Atom: (заголовок, дата, пояснение, ссылка), сверху новые."""
    items = []
    for entry in ET.fromstring(text).iter(ATOM + 'entry'):
        link = entry.find(ATOM + 'link')
        items.append((entry.findtext(ATOM + 'title', '').strip(), entry.findtext(ATOM + 'updated', '')[:10],
                      (entry.findtext(ATOM + 'content') or '').strip(), link.get('href', '') if link is not None else ''))
    return items


def pkgbuild_log(name, timeout=20):
    req = urllib.request.Request(AUR_FEED.format(urllib.parse.quote(name)), headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return parse_feed(resp.read().decode('utf-8', errors='replace'))


def update_one_command(name):
    return ['paru', '-S', '--noconfirm', '--skipreview', '--sudo', 'pkexec', name]


def remove_command(name, deps=True):
    # -s убирает зависимости, которые ставились ради этого пакета и больше никому не нужны.
    return ['pkexec', 'pacman', '-R' + ('ns' if deps else 'n'), '--noconfirm', name]


# Сборка любой прошлой версии: PKGBUILD берётся из git-хранилища пакета в AUR на нужной правке.

AUR_GIT = 'https://aur.archlinux.org/{}.git'
WORK = cache_dir('aur')
COMMIT_ID = re.compile(r'[?&]id=([0-9a-f]{7,40})')


def commit_of(link):
    m = COMMIT_ID.search(link or '')
    return m.group(1) if m else None


def built_packages(directory, installed):
    """Собранные makepkg пакеты, из них только те, что сейчас установлены (у составных пакетов частей несколько)."""
    out = subprocess.run(['makepkg', '--packagelist', '-D', directory], capture_output=True, stdin=subprocess.DEVNULL, text=True)
    paths = [p for p in out.stdout.split() if os.path.exists(p)]
    chosen = []
    for path in paths:
        name = os.path.basename(path).rsplit('-', 3)[0]
        if name in installed:
            chosen.append(path)
    return chosen or paths


def build_commands(base, commit, installed):
    """Команды по очереди: получить хранилище, перейти на правку, собрать, поставить через pkexec.
    Последняя команда строится после сборки, когда известны имена файлов."""
    directory = os.path.join(WORK, base)
    get = (['git', '-C', directory, 'fetch', '--quiet', 'origin'] if os.path.isdir(os.path.join(directory, '.git'))
           else ['git', 'clone', '--quiet', AUR_GIT.format(base), directory])
    return [
        ['mkdir', '-p', WORK],
        get,
        ['git', '-C', directory, 'checkout', '--quiet', '--force', commit],
        ['git', '-C', directory, 'clean', '-fdxq'],
        # Без -s: недостающие для сборки пакеты makepkg перечислит, ставить их через sudo из окна он не умеет.
        ['makepkg', '-D', directory, '-f', '--cleanbuild', '--noconfirm'],
        lambda: ['pkexec', 'pacman', '-U', '--noconfirm', *built_packages(directory, installed)],
    ]

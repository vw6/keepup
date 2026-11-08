"""Поиск того, что можно поставить: пакеты репозиториев, AUR и приложения Flathub, и команды установки."""
import json
import os
import re
import subprocess
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import USER_AGENT, appstream
from .pacman import CHECKUPDATES_DB

AUR_SEARCH = 'https://aur.archlinux.org/rpc/v5/search/{}?by=name-desc'
SS_HEAD = re.compile(r'^(\S+)/(\S+) (\S+)(.*)$')
AUR_MIN = 3          # короче AUR не спрашиваем: ответ огромный и бесполезный
AUR_KEEP = 600       # сколько секунд помнить ответ AUR на слово
APP_TYPES = {'desktop', 'desktop-application', 'console-application'}


@dataclass
class Found:
    source: str        # repo, aur или flatpak
    name: str          # имя пакета или идентификатор приложения
    summary: str = ''
    version: str = ''
    repo: str = ''     # репозиторий pacman или хранилище Flatpak
    title: str = ''    # название приложения; у пакетов совпадает с именем
    icon: str = ''
    popularity: float = 0.0
    data: dict = field(default_factory=dict)


def words(query):
    return [w for w in query.lower().split() if w]


def rank(item, query):
    """Порядок выдачи: точное имя, часть имени целиком (obs в obs-studio), имя с начала, слово в имени, остальное;
    внутри по популярности."""
    q = ' '.join(words(query))
    names = [item.name.lower(), item.title.lower()]
    if q in names or any(n.rsplit('.', 1)[-1] == q for n in names):
        level = 0
    elif any(q in re.split(r'[-._ ]', n) for n in names):
        level = 1
    elif any(n.startswith(q) for n in names):
        level = 2
    elif all(any(w in n for n in names) for w in words(query)):
        level = 3
    else:
        level = 4
    return level, -item.popularity, len(item.name), item.name


def parse_ss(text):
    found = []
    for line in text.splitlines():
        m = SS_HEAD.match(line)
        if m:
            found.append(Found('repo', m.group(2), version=m.group(3), repo=m.group(1), title=m.group(2)))
        elif line.startswith('    ') and found:
            found[-1].summary = line.strip()
    return found


def repo_db():
    """Свежие списки, которые скачал checkupdates при последней проверке, иначе системные."""
    return CHECKUPDATES_DB if os.path.isdir(os.path.join(CHECKUPDATES_DB, 'sync')) else '/var/lib/pacman'


_last_repo = [None, None]


def repo(query):
    """Пакеты репозиториев; последний ответ запоминается: AUR тут же спрашивает его же, чтобы убрать повторы."""
    db = repo_db()
    stamp = (query, db, _mtime(os.path.join(db, 'sync')))
    if _last_repo[0] == stamp:
        return _last_repo[1]
    terms = [re.escape(w) for w in words(query)]
    if not terms:
        return []
    out = subprocess.run(['pacman', '-Ss', '--dbpath', db, '--', *terms], capture_output=True,
                         stdin=subprocess.DEVNULL, text=True, env={**os.environ, 'LC_ALL': 'C'})
    # Один пакет бывает в нескольких репозиториях: pacman ставит из первого по порядку, его и оставляем.
    seen, found = set(), []
    for f in parse_ss(out.stdout):
        if f.name not in seen:
            seen.add(f.name)
            found.append(f)
    found.sort(key=lambda f: rank(f, query))
    _last_repo[:] = [stamp, found]
    return found


def _mtime(path):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return 0


def aur_key(query):
    """Слово, по которому спрашиваем AUR (он ищет по одному): самое длинное; None, если оно короче AUR_MIN."""
    terms = words(query)
    key = max(terms, key=len) if terms else ''
    return key if len(key) >= AUR_MIN else None


_aur_answers = {}


def aur(query, timeout=15, skip=()):
    """Пакеты AUR; то, что есть в репозиториях (skip), не показываем: оттуда оно ставится готовым."""
    key = aur_key(query)
    if not key:
        return []
    terms = words(query)
    # Ответ на слово помним AUR_KEEP секунд: стёртый и набранный заново запрос, как и «telegram desktop» после
    # «telegram», в сеть второй раз не идёт.
    kept = _aur_answers.get(key)
    if kept and time.monotonic() - kept[0] < AUR_KEEP:
        results = kept[1]
    else:
        req = urllib.request.Request(AUR_SEARCH.format(urllib.parse.quote(key, safe='')), headers={'User-Agent': USER_AGENT})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            results = json.load(resp).get('results', [])
        _aur_answers[key] = (time.monotonic(), results)
    found = []
    for r in results:
        text = f'{r["Name"]} {r.get("Description") or ""}'.lower()
        if all(w in text for w in terms) and r['Name'] not in skip:
            found.append(Found('aur', r['Name'], r.get('Description') or '', r.get('Version', ''), 'aur', r['Name'],
                               popularity=r.get('Popularity') or 0.0, data=r))
    return sorted(found, key=lambda f: rank(f, query))


_catalogs = {}
_catalog_lock = threading.Lock()


def catalog(remote='flathub', installation='system', lang='ru'):
    """Лёгкий список приложений хранилища (имя, кратко, значок) один раз на изменение каталога."""
    folder = appstream.catalog_dir(remote, installation)
    path = os.path.join(folder, 'appstream.xml')
    try:
        stamp = os.stat(path).st_mtime_ns
    except OSError:
        return []
    with _catalog_lock:
        cached = _catalogs.get((remote, installation))
        if cached and cached[0] == stamp:
            return cached[1]
        apps = []
        for _event, elem in ET.iterparse(path, events=('end',)):
            if elem.tag != 'component':
                continue
            if elem.get('type') in APP_TYPES:
                app_id = (elem.findtext('id') or '').strip()
                name = appstream._pick(elem, 'name', lang)
                summary = appstream._pick(elem, 'summary', lang)
                icon = ''
                for i in elem.findall('icon'):
                    if i.get('type') == 'cached' and i.get('width') == '64' and not i.get('scale'):
                        icon = os.path.join(folder, 'icons', '64x64', (i.text or '').strip())
                keywords = ' '.join((k.text or '') for k in elem.iter('keyword'))
                apps.append(Found('flatpak', app_id, (summary.text or '').strip() if summary is not None else '',
                                  repo=remote, title=(name.text or '').strip() if name is not None else app_id, icon=icon,
                                  data={'installation': installation, 'text': f'{app_id} {keywords}'.lower()}))
            elem.clear()
        _catalogs[(remote, installation)] = (stamp, apps)
        return apps


def flathub(query, apps):
    terms = words(query)
    if not terms:
        return []
    hits = [a for a in apps if all(w in f'{a.title} {a.summary} {a.data["text"]}'.lower() for w in terms)]
    return sorted(hits, key=lambda f: rank(f, query))


# Установка

def repo_install_command(urls, deps):
    """Ровно эти файлы из свежих списков одним действием; зависимости помечаются как зависимости."""
    helper = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'helpers', 'install_packages.py')
    return ['pkexec', '/usr/bin/python3', helper, *[f'--dep={d}' for d in deps], *urls]


def aur_install_command(name):
    return ['paru', '-S', '--needed', '--noconfirm', '--skipreview', '--sudo', 'pkexec', name]


def flatpak_install_command(item):
    # Для системной установки flatpak сам спрашивает пароль через polkit.
    return ['flatpak', 'install', '-y', '--noninteractive', f'--{item.data.get("installation", "system")}', item.repo, item.name]

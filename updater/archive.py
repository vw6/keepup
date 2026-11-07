"""Прошлые версии пакетов из архива Arch (archive.archlinux.org) и история их сборки на GitLab Arch."""
import json
import re
import urllib.parse
import urllib.request
from dataclasses import dataclass

from .pacman import PKG_EXT, parse_cache_name, vercmp

ARCHIVE = 'https://archive.archlinux.org/packages/{}/{}/'
GITLAB = 'https://gitlab.archlinux.org/api/v4/projects/{}/repository/commits?per_page={}'
HREF = re.compile(r'href="([^"]+)"')
_CACHE = {}


@dataclass
class ArchivedVersion:
    name: str
    version: str
    arch: str
    url: str


def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={'User-Agent': 'vw6-updater/0.1'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


def parse_index(name, html, base_url):
    """Файлы пакета из списка каталога архива, от новой версии к старой."""
    found = []
    for href in HREF.findall(html):
        filename = urllib.parse.unquote(href)
        parsed = parse_cache_name(filename) if PKG_EXT.search(filename) else None
        if parsed and parsed[0] == name:
            found.append(ArchivedVersion(name, parsed[1], parsed[2], base_url + href))
    found.sort(key=_Key, reverse=True)
    return found


class _Key:
    def __init__(self, item):
        self.version = item.version

    def __lt__(self, other):
        return vercmp(self.version, other.version) < 0


def versions(name):
    """Версии из архива; пакета нет в архиве (сборки CachyOS, AUR) -> пустой список. Ответ запоминается."""
    if name not in _CACHE:
        url = ARCHIVE.format(name[0], urllib.parse.quote(name))
        try:
            _CACHE[name] = parse_index(name, _get(url), url)
        except OSError:
            _CACHE[name] = []
    return _CACHE[name]


def plain(version):
    """Версия без эпохи и номера сборки: у CachyOS и Arch эпохи разные, а сама программа одна."""
    return version.split(':')[-1].rsplit('-', 1)[0]


def packaging_log(base, count=15):
    """Правки сборки пакета Arch на GitLab: (дата, заголовок, ссылка), сверху новые."""
    project = urllib.parse.quote(f'archlinux/packaging/packages/{base}', safe='')
    data = json.loads(_get(GITLAB.format(project, count)))
    return [(c.get('created_at', '')[:10], c.get('title', ''), c.get('web_url', '')) for c in data]

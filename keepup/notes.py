"""Примечания к выпуску версии пакета AUR: что поменялось в программе и в сборке."""
import json
import re
from concurrent.futures import ThreadPoolExecutor
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import USER_AGENT, appstream
from .i18n import tr

SRCINFO = 'https://aur.archlinux.org/cgit/aur.git/plain/.SRCINFO?h={}&id={}'
GITHUB = re.compile(r'^https?://github\.com/([^/]+)/([^/#?]+)')
FLATHUB = '/var/lib/flatpak/appstream/flathub/x86_64/active/appstream.xml'
SUFFIX = re.compile(r'-(bin|git|appimage|beta|nightly|stable|electron)$')


@dataclass
class Notes:
    version: str = ''
    text: str = ''
    source: str = ''
    link: str = ''
    build_changes: list[str] = field(default_factory=list)


def _get(url, timeout=20, accept=None):
    headers = {'User-Agent': USER_AGENT}
    if accept:
        headers['Accept'] = accept
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
        return resp.read().decode('utf-8', errors='replace')


def parse_srcinfo(text):
    """Поля .SRCINFO: ключ -> список значений (depends и подобные повторяются)."""
    fields = {}
    for line in text.splitlines():
        key, sep, value = line.strip().partition(' = ')
        if sep:
            fields.setdefault(key, []).append(value.strip())
    return fields


def srcinfo(base, commit):
    text = _get(SRCINFO.format(urllib.parse.quote(base), commit))
    if text.lstrip().startswith('<'):
        raise RuntimeError(tr('notes.srcinfo.u_etoy_pravki_net'))
    return parse_srcinfo(text)


def version_of(fields):
    ver = (fields.get('pkgver') or [''])[0]
    rel = (fields.get('pkgrel') or [''])[0]
    epoch = (fields.get('epoch') or [''])[0]
    return (f'{epoch}:' if epoch else '') + ver + (f'-{rel}' if rel else '')


def compare_builds(old, new):
    """Что поменялось в сборке между двумя .SRCINFO: зависимости и источники, по-человечески."""
    # Шаблоны «появилось» и «пропало» для каждого поля .SRCINFO.
    texts = {'depends': (tr('notes.compare_builds.novaya_zavisimost'), tr('notes.compare_builds.ubrana_zavisimost')),
             'makedepends': (tr('notes.compare_builds.novaya_zavisimost_dlya_sborki'), tr('notes.compare_builds.ubrana_zavisimost_dlya_sborki')),
             'optdepends': (tr('notes.compare_builds.novaya_neobyazatelnaya_zavisimost'), tr('notes.compare_builds.ubrana_neobyazatelnaya_zavisimost')),
             'provides': (tr('notes.compare_builds.predostavlyaet'), tr('notes.compare_builds.bolshe_ne_predostavlyaet')),
             'conflicts': (tr('notes.compare_builds.konfliktuet_s'), tr('notes.compare_builds.bolshe_ne_konfliktuet_s'))}
    changes = []
    for key, (added, removed) in texts.items():
        a, b = set(old.get(key, [])), set(new.get(key, []))
        changes += [added.replace('{x}', x) for x in sorted(b - a)]
        changes += [removed.replace('{x}', x) for x in sorted(a - b)]
    return changes


def _plain_version(version):
    """«1:8.1.0-2» -> «8.1.0»: так версию пишут авторы программ."""
    return version.split(':')[-1].rsplit('-', 1)[0]


def _norm(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def from_metainfo(path, version):
    info = appstream.from_file(path) if path else None
    return _pick_release(info, version, tr('notes.from_metainfo.opisanie_versiy_iz_samoy'))


def _pick_release(info, version, source):
    if not info:
        return None
    want = _plain_version(version)
    for rel in info.releases:
        if rel.version == want and rel.text:
            return Notes(text=rel.text, source=source)
    return None


def from_flathub(name, version, url=''):
    """Та же программа в каталоге Flathub: совпадение по имени пакета, названию или последней части идентификатора."""
    try:
        open(FLATHUB).close()
    except OSError:
        return None
    key = SUFFIX.sub('', name.lower())
    for _event, elem in ET.iterparse(FLATHUB, events=('end',)):
        if elem.tag != 'component':
            continue
        app_id = (elem.findtext('id') or '').removesuffix('.desktop')
        title = elem.findtext('name') or ''
        if key in (_norm(app_id.rsplit('.', 1)[-1]), _norm(title)):
            notes = _pick_release(appstream.parse_component(elem), version, tr('notes.from_flathub.zhurnal_izmeneniy_programmy_na', app_id=app_id))
            elem.clear()
            if notes:
                notes.link = f'https://flathub.org/apps/{app_id}'
            return notes
        elem.clear()
    return None


_RELEASES = {}


def github_releases(owner, repo):
    """Последние выпуски одним запросом; запоминаются на время работы программы. Ответы «не найдено» у GitHub
    приходят по нескольку секунд, поэтому метки по одной не перебираются."""
    key = (owner.lower(), repo.lower())
    if key not in _RELEASES:
        _RELEASES[key] = json.loads(_get(f'https://api.github.com/repos/{owner}/{repo}/releases?per_page=100',
                                         accept='application/vnd.github+json'))
    return _RELEASES[key]


def from_github(url, version):
    m = GITHUB.match(url or '')
    if not m:
        return None
    owner, repo = m.group(1), m.group(2).removesuffix('.git')
    want = _plain_version(version)
    try:
        releases = github_releases(owner, repo)
    except (OSError, ValueError):
        return None
    # Метка может быть v2.5.2, 2.5.2 или с приставкой (ttl-v2.5.2, release-2.5.2): ищем номер версии целиком.
    token = re.compile(r'(?<![\d.])' + re.escape(want) + r'(?![\d])')
    for data in releases:
        if data.get('body') and (token.search(data.get('tag_name', '')) or token.search(data.get('name') or '')):
            return _github_notes(data, owner, repo)
    return None


def _github_notes(data, owner, repo):
    return Notes(text=data['body'].strip(), source=tr('notes._github_notes.vypusk_na_github', owner=owner, repo=repo), link=data.get('html_url', ''))


def for_version(name, version, url, metainfo_path):
    """Примечания к версии пакета из репозитория: сама программа, выпуски GitHub, Flathub."""
    notes = from_metainfo(metainfo_path, version) or from_github(url, version) or from_flathub(name, version, url) or Notes()
    notes.version = version
    return notes


def collect(name, base, commit, prev_commit, url, metainfo_path):
    """Версия правки, примечания к ней из первого найденного места и что поменялось в сборке против прошлой правки."""
    with ThreadPoolExecutor(2) as pool:
        previous = pool.submit(srcinfo, base, prev_commit) if prev_commit else None
        new = srcinfo(base, commit)
        version = version_of(new)
        notes = (from_metainfo(metainfo_path, version) or from_github((new.get('url') or [url])[0], version)
                 or from_flathub(name, version, url) or Notes())
        notes.version = version
        try:
            old = previous.result() if previous else None
        except (OSError, RuntimeError):
            old = None
    if old:
        if version_of(old) != version:
            notes.build_changes.append(tr('notes.collect.versiya', old=version_of(old), version=version))
        notes.build_changes += compare_builds(old, new)
    return notes

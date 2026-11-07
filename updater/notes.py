"""Примечания к выпуску версии пакета AUR: что поменялось в программе и в сборке."""
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field

from . import appstream

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
    headers = {'User-Agent': 'vw6-updater/0.1'}
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
        raise RuntimeError('у этой правки нет .SRCINFO')
    return parse_srcinfo(text)


def version_of(fields):
    ver = (fields.get('pkgver') or [''])[0]
    rel = (fields.get('pkgrel') or [''])[0]
    epoch = (fields.get('epoch') or [''])[0]
    return (f'{epoch}:' if epoch else '') + ver + (f'-{rel}' if rel else '')


def compare_builds(old, new):
    """Что поменялось в сборке между двумя .SRCINFO: зависимости и источники, по-человечески."""
    names = {'depends': 'зависимость', 'makedepends': 'зависимость для сборки', 'optdepends': 'необязательная зависимость',
             'provides': 'предоставляет', 'conflicts': 'конфликтует с'}
    changes = []
    for key, word in names.items():
        a, b = set(old.get(key, [])), set(new.get(key, []))
        changes += [f'новая {word}: {x}' if word.startswith(('зав', 'необ')) else f'{word}: {x}' for x in sorted(b - a)]
        changes += [f'убрана {word}: {x}' if word.startswith(('зав', 'необ')) else f'больше не {word}: {x}' for x in sorted(a - b)]
    return changes


def _plain_version(version):
    """«1:8.1.0-2» -> «8.1.0»: так версию пишут авторы программ."""
    return version.split(':')[-1].rsplit('-', 1)[0]


def _norm(text):
    return re.sub(r'[^a-z0-9]+', '-', text.lower()).strip('-')


def from_metainfo(path, version):
    info = appstream.from_file(path) if path else None
    return _pick_release(info, version, 'описание версий из самой программы')


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
            notes = _pick_release(appstream.parse_component(elem), version, f'журнал изменений программы на Flathub ({app_id})')
            elem.clear()
            if notes:
                notes.link = f'https://flathub.org/apps/{app_id}'
            return notes
        elem.clear()
    return None


def from_github(url, version):
    m = GITHUB.match(url or '')
    if not m:
        return None
    owner, repo = m.group(1), m.group(2).removesuffix('.git')
    want = _plain_version(version)
    for tag in (f'v{want}', want):
        try:
            data = json.loads(_get(f'https://api.github.com/repos/{owner}/{repo}/releases/tags/{urllib.parse.quote(tag)}',
                                   accept='application/vnd.github+json'))
        except OSError:
            continue
        if data.get('body'):
            return _github_notes(data, owner, repo)
    # Метки бывают и с приставкой (ttl-v2.5.1, release-2.5.1): ищем среди последних выпусков по номеру версии.
    try:
        releases = json.loads(_get(f'https://api.github.com/repos/{owner}/{repo}/releases?per_page=50', accept='application/vnd.github+json'))
    except OSError:
        return None
    token = re.compile(r'(?<![\d.])' + re.escape(want) + r'(?![\d])')
    for data in releases:
        if data.get('body') and (token.search(data.get('tag_name', '')) or token.search(data.get('name') or '')):
            return _github_notes(data, owner, repo)
    return None


def _github_notes(data, owner, repo):
    return Notes(text=data['body'].strip(), source=f'выпуск на GitHub ({owner}/{repo})', link=data.get('html_url', ''))


def collect(name, base, commit, prev_commit, url, metainfo_path):
    """Версия правки, примечания к ней из первого найденного места и что поменялось в сборке против прошлой правки."""
    new = srcinfo(base, commit)
    version = version_of(new)
    notes = (from_metainfo(metainfo_path, version) or from_flathub(name, version, url)
             or from_github((new.get('url') or [url])[0], version) or Notes())
    notes.version = version
    if prev_commit:
        try:
            old = srcinfo(base, prev_commit)
        except (OSError, RuntimeError):
            old = None
        if old:
            if version_of(old) != version:
                notes.build_changes.append(f'версия {version_of(old)} → {version}')
            notes.build_changes += compare_builds(old, new)
    return notes

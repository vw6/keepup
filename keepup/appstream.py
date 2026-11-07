"""Сведения о приложениях Flatpak из каталога AppStream хранилища: название, описание, иконка, снимки, изменения по версиям."""
import hashlib
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from . import USER_AGENT, cache_dir

LANG_ATTR = '{http://www.w3.org/XML/1998/namespace}lang'
SHOTS = cache_dir('shots')


@dataclass
class Release:
    version: str
    date: str
    text: str


@dataclass
class Info:
    app_id: str
    name: str = ''
    summary: str = ''
    developer: str = ''
    license: str = ''
    homepage: str = ''
    description: str = ''
    icon: str = ''
    screenshots: list[tuple[str, str]] = field(default_factory=list)
    releases: list[Release] = field(default_factory=list)


def catalog_dir(remote, installation, arch='x86_64'):
    base = '/var/lib/flatpak' if installation == 'system' else os.path.expanduser('~/.local/share/flatpak')
    return os.path.join(base, 'appstream', remote, arch, 'active')


def _pick(elem, tag, lang):
    """Текст тега на нужном языке, иначе без языка."""
    plain = None
    for child in elem.findall(tag):
        child_lang = child.get(LANG_ATTR)
        if child_lang == lang:
            return child
        if child_lang is None and plain is None:
            plain = child
    return plain


def _flat(elem):
    return re.sub(r'\s+', ' ', ''.join(elem.itertext())).strip()


def _text(node, lang):
    """Абзацы и списки описания в обычный текст; у переведённых описаний язык стоит на каждом абзаце и пункте."""
    if node is None:
        return ''
    blocks = []
    for child in node:
        if child.tag == 'p':
            blocks.append((child.get(LANG_ATTR), _flat(child)))
        elif child.tag in ('ul', 'ol'):
            for li in child:
                if li.tag == 'li':
                    blocks.append((li.get(LANG_ATTR) or child.get(LANG_ATTR), '• ' + _flat(li)))
    use = lang if any(item_lang == lang for item_lang, _ in blocks) else None
    out = []
    for item_lang, text in blocks:
        if item_lang != use:
            continue
        # Пункты одного списка идут одним куском, без пустых строк между ними.
        if text.startswith('• ') and out and out[-1].startswith('• '):
            out[-1] += '\n' + text
        else:
            out.append(text)
    return '\n\n'.join(out)


def parse_component(elem, lang='ru'):
    info = Info(elem.findtext('id', '').strip())
    name = _pick(elem, 'name', lang)
    summary = _pick(elem, 'summary', lang)
    info.name = (name.text or '').strip() if name is not None else info.app_id
    info.summary = (summary.text or '').strip() if summary is not None else ''
    dev = elem.find('developer')
    info.developer = (dev.findtext('name') or '').strip() if dev is not None else (elem.findtext('developer_name') or '').strip()
    info.license = (elem.findtext('project_license') or '').strip()
    for url in elem.findall('url'):
        if url.get('type') == 'homepage':
            info.homepage = (url.text or '').strip()
    info.description = _text(_pick(elem, 'description', lang), lang)
    for icon in elem.findall('icon'):
        if icon.get('type') == 'cached' and icon.get('width') == '128' and not icon.get('scale'):
            info.icon = (icon.text or '').strip()
    for shot in elem.iter('screenshot'):
        caption = _pick(shot, 'caption', lang)
        # В каталоге хранилища есть уменьшенные копии; в metainfo самих программ обычно только исходный снимок.
        thumbs = sorted((img for img in shot.findall('image') if img.get('type') == 'thumbnail'),
                        key=lambda img: abs(int(img.get('width') or 0) - 752)) or shot.findall('image')
        if thumbs:
            info.screenshots.append(((caption.text or '').strip() if caption is not None else '', (thumbs[0].text or '').strip()))
    for rel in elem.iter('release'):
        info.releases.append(Release(rel.get('version', ''), rel.get('date') or rel.get('timestamp', ''), _text(_pick(rel, 'description', lang), lang)))
    return info


def from_file(path, lang='ru'):
    """Сведения из metainfo, который поставила сама программа (/usr/share/metainfo)."""
    try:
        root = ET.parse(path).getroot()
    except (OSError, ET.ParseError):
        return None
    return parse_component(root, lang) if root.tag == 'component' else None


def lookup(remote, installation, app_ids, lang='ru'):
    """Сведения о нужных приложениях одним проходом по каталогу (он большой, поэтому без загрузки целиком)."""
    folder = catalog_dir(remote, installation)
    path = os.path.join(folder, 'appstream.xml')
    found = {}
    if not os.path.exists(path):
        return found
    wanted = set(app_ids)
    for _event, elem in ET.iterparse(path, events=('end',)):
        if elem.tag != 'component':
            continue
        app_id = (elem.findtext('id') or '').strip()
        if app_id in wanted:
            info = parse_component(elem, lang)
            if info.icon:
                icon_path = os.path.join(folder, 'icons', '128x128', info.icon)
                info.icon = icon_path if os.path.exists(icon_path) else ''
            found[app_id] = info
            if len(found) == len(wanted):
                break
        elem.clear()
    return found


def screenshot(url, timeout=20):
    """Байты снимка экрана; скачанные хранятся в кэше пользователя."""
    os.makedirs(SHOTS, exist_ok=True)
    path = os.path.join(SHOTS, hashlib.sha1(url.encode()).hexdigest() + os.path.splitext(url)[1])
    if os.path.exists(path):
        with open(path, 'rb') as f:
            return f.read()
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    with open(path, 'wb') as f:
        f.write(data)
    return data

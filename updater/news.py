"""Новости Arch Linux из ленты RSS и пометка тех, что касаются ожидающих обновлений."""
import html
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime

FEED = 'https://archlinux.org/feeds/news/'
CACHE = os.path.join(os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache')), 'vw6-updater', 'news.xml')
TAG = re.compile(r'<[^>]+>')


@dataclass
class Item:
    title: str
    link: str
    date: datetime | None
    text: str
    touches: list[str] = field(default_factory=list)


def _plain(fragment):
    # Абзацы и переносы в тексте новости сохраняются пустой строкой, остальная разметка снимается.
    fragment = re.sub(r'</p>', '\n\n', fragment, flags=re.I)
    fragment = re.sub(r'<br\s*/?>|</li>', '\n', fragment, flags=re.I)
    fragment = re.sub(r'<li[^>]*>', '• ', fragment, flags=re.I)
    text = html.unescape(TAG.sub('', fragment))
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(line.strip() for line in text.splitlines())).strip()


def parse(xml_text):
    items = []
    for node in ET.fromstring(xml_text).iter('item'):
        when = node.findtext('pubDate')
        try:
            date = parsedate_to_datetime(when) if when else None
        except (TypeError, ValueError):
            date = None
        items.append(Item(node.findtext('title', '').strip(), node.findtext('link', '').strip(), date,
                          _plain(node.findtext('description', ''))))
    return items


def fetch(timeout=15):
    """Свежая лента; при ошибке сети отдаётся последняя сохранённая, и это видно по второму значению."""
    try:
        req = urllib.request.Request(FEED, headers={'User-Agent': 'vw6-updater/0.1'})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read().decode('utf-8')
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, 'w', encoding='utf-8') as f:
            f.write(data)
        return parse(data), True
    except OSError:
        try:
            with open(CACHE, encoding='utf-8') as f:
                return parse(f.read()), False
        except OSError:
            return [], False


def mark(items, names):
    """Отмечает новости, где по имени упомянут пакет из списка обновлений."""
    words = [n for n in names if len(n) >= 3]
    for item in items:
        hay = (item.title + '\n' + item.text).lower()
        item.touches = [n for n in words if re.search(r'(?<![\w.-])' + re.escape(n.lower()) + r'(?![\w-])', hay)]
    return items

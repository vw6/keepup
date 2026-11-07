"""Новости Arch Linux из ленты RSS и пометка тех, что касаются ожидающих обновлений."""
import hashlib
import html
import json
import os
import re
import time
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime
from email.utils import parsedate_to_datetime
from . import USER_AGENT, cache_dir, data_dir

FEED = 'https://archlinux.org/feeds/news/'
CACHE = cache_dir('news.xml')
ARCHIVE = data_dir('news')
FRESH = 30 * 60   # чаще раза в полчаса за лентой не ходим: новости Arch выходят раз в несколько недель
TAG = re.compile(r'<[^>]+>')


@dataclass
class Item:
    title: str
    link: str
    date: datetime | None
    text: str
    touches: list[str] = field(default_factory=list)
    title_ru: str = ''
    text_ru: str = ''


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


def fetch(timeout=15, max_age=FRESH):
    """Новости: архив всех увиденных статей плюс свежая лента. Лента скачивается, только если сохранённая старше max_age;
    второе значение говорит, удалось ли её обновить (или она и так свежая)."""
    fresh = False
    try:
        if time.time() - os.path.getmtime(CACHE) < max_age:
            fresh = True
    except OSError:
        pass
    if not fresh:
        try:
            req = urllib.request.Request(FEED, headers={'User-Agent': USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = resp.read().decode('utf-8')
            os.makedirs(os.path.dirname(CACHE), exist_ok=True)
            with open(CACHE, 'w', encoding='utf-8') as f:
                f.write(data)
            fresh = True
        except OSError:
            pass
    try:
        with open(CACHE, encoding='utf-8') as f:
            feed = parse(f.read())
    except (OSError, ET.ParseError):
        feed = []
    for item in feed:
        save(item, only_new=True)
    return archived(), fresh


def _path(item):
    return os.path.join(ARCHIVE, hashlib.sha1((item.link or item.title).encode()).hexdigest()[:16] + '.json')


def save(item, only_new=False):
    """Статья в архив: заголовок, дата, текст и, если есть, перевод. Перевод из архива не затирается пустым."""
    path = _path(item)
    old = {}
    if os.path.exists(path):
        if only_new:
            try:
                with open(path, encoding='utf-8') as f:
                    old = json.load(f)
            except (OSError, ValueError):
                old = {}
            if old.get('text') == item.text and old.get('title') == item.title:
                return
    os.makedirs(ARCHIVE, exist_ok=True)
    record = {'title': item.title, 'link': item.link, 'date': item.date.isoformat() if item.date else '',
              'text': item.text, 'title_ru': item.title_ru or old.get('title_ru', ''), 'text_ru': item.text_ru or old.get('text_ru', '')}
    tmp = path + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(record, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def archived():
    """Все статьи архива, сверху новые."""
    items = []
    try:
        names = os.listdir(ARCHIVE)
    except OSError:
        return items
    for name in names:
        if not name.endswith('.json'):
            continue
        try:
            with open(os.path.join(ARCHIVE, name), encoding='utf-8') as f:
                r = json.load(f)
        except (OSError, ValueError):
            continue
        date = datetime.fromisoformat(r['date']) if r.get('date') else None
        items.append(Item(r.get('title', ''), r.get('link', ''), date, r.get('text', ''),
                          title_ru=r.get('title_ru', ''), text_ru=r.get('text_ru', '')))
    items.sort(key=lambda i: i.date.timestamp() if i.date else 0, reverse=True)
    return items


def mark(items, names):
    """Отмечает новости, где по имени упомянут пакет из списка обновлений."""
    words = [n for n in names if len(n) >= 3]
    for item in items:
        hay = (item.title + '\n' + item.text).lower()
        item.touches = [n for n in words if re.search(r'(?<![\w.-])' + re.escape(n.lower()) + r'(?![\w-])', hay)]
    return items

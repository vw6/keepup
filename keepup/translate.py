"""Машинный перевод новостей через translate-shell (trans); команды, имена пакетов и версии не переводятся."""
import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
from . import cache_dir, config_dir
from .i18n import tr

CACHE = cache_dir('translations.json')
SETTINGS = config_dir('settings.json')
# Что переводчик испортил бы: код в обратных кавычках, строки-команды, адреса и «технические» слова.
# Метка вида ZQX0Q проходит перевод нетронутой.
PROTECT = re.compile(
    r'`[^`\n]+`'
    r'|^[ \t]*(?:[$#][ \t]|(?:sudo|pacman|paru|systemctl|mkinitcpio|pacman-key|ln|rm|mv|cp|echo|chmod)\b)[^\n]*'
    r'|https?://\S+'
    r'|[^\s,;:!?()«»"]*[A-Za-z0-9][^\s,;:!?()«»"]*[-_./=<>@+~][^\s,;:!?()«»"]*[A-Za-z0-9>=]',
    re.M)
CODEISH = re.compile(r'[\d_./=<>@+~]')
PLAIN_WORDS = {'and/or', 'or/and', 'e.g', 'i.e'}
_packages = None


def package_names():
    """Имена всех пакетов из подключённых репозиториев: «php-legacy» не переводится, а «man-in-the-middle» переводится."""
    global _packages
    if _packages is None:
        out = subprocess.run(['pacman', '-Slq'], capture_output=True, stdin=subprocess.DEVNULL, text=True)
        _packages = set(out.stdout.split())
    return _packages


_cache = None
_lock = threading.Lock()


def available():
    return shutil.which('trans') is not None


def protect(text, names=None):
    kept = []
    names = package_names() if names is None else names

    def hold(m):
        token = m.group(0)
        word = token.rstrip('.')
        # Обычное слово через дефис (re-enroll, man-in-the-middle) переводится; код, цифры и имена пакетов нет.
        plain = not token.startswith('`') and not re.search(r'\s', token) and not token.startswith('http') and (
            word.lower() in PLAIN_WORDS or re.fullmatch(r'[A-Za-z]+(?:/[A-Za-z]+)+', word)
            or (not CODEISH.search(word) and word.lower() not in names and word not in names))
        if plain:
            return token
        kept.append(token)
        return f'ZQX{len(kept) - 1}Q'
    return PROTECT.sub(hold, text), kept


def restore(text, kept):
    return re.sub(r'ZQX(\d+)Q', lambda m: kept[int(m.group(1))] if int(m.group(1)) < len(kept) else m.group(0), text)


def _load():
    global _cache
    if _cache is None:
        try:
            with open(CACHE, encoding='utf-8') as f:
                _cache = json.load(f)
        except (OSError, ValueError):
            _cache = {}
    return _cache


def cached(text, lang='ru'):
    return _load().get(hashlib.sha1(f'{lang}\0{text}'.encode()).hexdigest())


def translate(text, lang='ru', timeout=40):
    """Перевод с запоминанием; при ошибке исключение, текст остаётся как был."""
    if not text.strip():
        return text
    hit = cached(text, lang)
    if hit is not None:
        return hit
    masked, kept = protect(text)
    out = subprocess.run(['trans', '-no-autocorrect', '-no-ansi', '-b', f':{lang}'], input=masked, capture_output=True,
                         text=True, timeout=timeout)
    if out.returncode != 0 or not out.stdout.strip():
        raise RuntimeError(out.stderr.strip() or tr('translate.translate.perevodchik_ne_otvetil'))
    result = restore(out.stdout.strip(), kept)
    # Переводы идут из нескольких потоков (список и открытая новость): запись файла по очереди.
    with _lock:
        _load()[hashlib.sha1(f'{lang}\0{text}'.encode()).hexdigest()] = result
        os.makedirs(os.path.dirname(CACHE), exist_ok=True)
        with open(CACHE, 'w', encoding='utf-8') as f:
            json.dump(_cache, f, ensure_ascii=False)
    return result


def load_settings():
    try:
        with open(SETTINGS, encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def save_setting(key, value):
    settings = load_settings()
    settings[key] = value
    os.makedirs(os.path.dirname(SETTINGS), exist_ok=True)
    with open(SETTINGS, 'w', encoding='utf-8') as f:
        json.dump(settings, f, ensure_ascii=False, indent=1)

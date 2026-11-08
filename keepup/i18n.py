"""Строки интерфейса из locales/<язык>.json: tr('раздел.ключ', имя=значение); основной язык ru_RU."""
import json
import locale
import os

BASE = 'ru_RU'
LOCALES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'locales')
_strings = None


def system_language():
    """Язык интерфейса из окружения: LANGUAGE, LC_ALL, LC_MESSAGES, LANG; KEEPUP_LANG его перекрывает."""
    for var in ('KEEPUP_LANG', 'LANGUAGE', 'LC_ALL', 'LC_MESSAGES', 'LANG'):
        value = os.environ.get(var, '').split(':')[0]
        if value and value not in ('C', 'POSIX') and not value.startswith('C.'):
            return value.split('.')[0].split('@')[0]
    return (locale.getlocale()[0] or BASE)


def _load(name):
    try:
        with open(os.path.join(LOCALES, f'{name}.json'), encoding='utf-8') as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    data.pop('metadata', None)
    return data


def pick(language, available):
    """Файл для языка: точное совпадение (de_DE), иначе тот же язык другой страны (de_AT → de_DE)."""
    if language in available:
        return language
    prefix = language.split('_')[0]
    return next((a for a in sorted(available) if a.split('_')[0] == prefix), None)


def available():
    try:
        return {f[:-5] for f in os.listdir(LOCALES) if f.endswith('.json')}
    except OSError:
        return set()


def _lookup(table, key):
    node = table
    for part in key.split('.'):
        if not isinstance(node, dict) or part not in node:
            return None
        node = node[part]
    return node if isinstance(node, str) else None


def language():
    """Язык, на котором показан интерфейс: файл, выбранный по системе, иначе основной."""
    return pick(system_language(), available()) or BASE


def tr(key, **values):
    """Строка по ключу на языке системы; чего нет в переводе, берётся из основного языка, а если нет и там, сам ключ."""
    global _strings
    if _strings is None:
        chosen = language()
        _strings = [_load(chosen)] if chosen != BASE else []
        _strings.append(_load(BASE))
    text = next((t for t in (_lookup(s, key) for s in _strings) if t is not None), key)
    for name, value in values.items():
        text = text.replace('{' + name + '}', str(value))
    return text

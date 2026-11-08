"""Понятный ход работы по выводу pacman, paru, makepkg и flatpak: этап, что сейчас делается и доля готовности."""
import re
from dataclasses import dataclass

from .i18n import tr

COUNTER = re.compile(r'^\(\s*(\d+)/(\d+)\)\s*(.*)$')
# Этапы: (шаблон строки, этап). Вывод бывает и по-русски, и по-английски, в зависимости от языка системы.
# Название этапа для человека берётся из перевода: progress.stage.<этап>.
STAGES = [
    (re.compile(r'Синхронизация баз|Synchronizing package databases', re.I), 'sync'),
    (re.compile(r'Получение исходных файлов|Retrieving sources', re.I), 'sources'),
    (re.compile(r'Запускается build\(\)|Starting build\(\)|Сборка пакета|Making package', re.I), 'build'),
    (re.compile(r'Создание пакета|Creating package|Сжатие пакета|Compressing package', re.I), 'package'),
    (re.compile(r'загрузка файлов пакетов|загрузка|Retrieving packages|downloading', re.I), 'download'),
    (re.compile(r'проверка связки ключей|проверка целостности|checking keyring|checking package integrity|'
                r'проверка возможных конфликтов|checking for file conflicts|проверка доступного|checking available', re.I), 'check'),
    (re.compile(r'Обработка изменений пакета|Processing package changes', re.I), 'install'),
    (re.compile(r'post-transaction hooks', re.I), 'hooks'),
    (re.compile(r'Resolving dependencies|Разрешение зависимостей', re.I), 'deps'),
    (re.compile(r'^(Updating|Обновление|Installing|Установка|Uninstalling|Удаление) (app|runtime)/', re.I), 'flatpak'),
]
ACTION = re.compile(r'^(?:\(\s*\d+/\d+\)\s*)?(обновление|установка|удаление|переустановка|понижение|upgrading|installing|removing|'
                    r'reinstalling|downgrading)\s+(\S+?)(?:\.\.\.)?$', re.I)
ACTIONS = {'обновление': 'upgrade', 'upgrading': 'upgrade', 'установка': 'install', 'installing': 'install',
           'удаление': 'remove', 'removing': 'remove', 'переустановка': 'reinstall', 'reinstalling': 'reinstall',
           'понижение': 'downgrade', 'downgrading': 'downgrade'}


@dataclass
class State:
    stage: str = 'waiting'
    detail: str = ''
    fraction: float | None = None   # None: доля неизвестна, полоска «бежит»


def label(state):
    return tr(f'progress.stage.{state.stage}')


def feed(state, line):
    """Обновляет ход работы по очередной строке вывода; возвращает True, если что-то поменялось."""
    text = line.strip()
    if not text or text.startswith('$ '):
        return False
    before = (state.stage, state.detail, state.fraction)
    if state.stage == 'waiting':
        state.stage = 'prepare'
    m = ACTION.match(text)
    if m:
        state.stage = 'install' if state.stage != 'hooks' else state.stage
        state.detail = tr(f'progress.action.{ACTIONS[m.group(1).lower()]}', name=m.group(2))
    for rule, name in STAGES:
        if rule.search(text):
            if name == 'flatpak':
                state.stage = 'install'
                state.detail = text
            else:
                state.stage = name
                if name != 'install':
                    state.detail = ''
                state.fraction = None
            break
    c = COUNTER.match(text)
    if c:
        done, total = int(c.group(1)), int(c.group(2))
        if total:
            state.fraction = min(1.0, done / total)
        if state.stage == 'hooks':
            # Названия шагов pacman пишет по-английски; номер шага понятнее.
            state.detail = tr('progress.step', done=done, total=total)
    return before != (state.stage, state.detail, state.fraction)


# Место этапа на общей полоске: этапы идут в этом порядке, а полоска начинается с первого встреченного,
# поэтому и обновление из репозитория, и сборка из AUR проходят её от начала до конца.
RANGES = {
    'waiting': (0.0, 0.0), 'prepare': (0.0, 0.03), 'deps': (0.03, 0.06),
    'sources': (0.06, 0.12), 'build': (0.12, 0.45), 'package': (0.45, 0.5),
    'sync': (0.5, 0.55), 'download': (0.55, 0.7), 'check': (0.7, 0.78),
    'install': (0.78, 0.9), 'hooks': (0.9, 1.0),
}


class Overall:
    """Общая доля готовности: только растёт, от первого встреченного этапа до конца."""
    def __init__(self):
        self.base = None
        self.value = 0.0

    def update(self, state):
        start, end = RANGES.get(state.stage, (self.value, self.value))
        if state.stage == 'waiting':
            return self.value
        if self.base is None:
            self.base = start
        inner = state.fraction if state.fraction is not None else 0.0
        raw = start + (end - start) * inner
        span = 1.0 - self.base
        value = (raw - self.base) / span if span > 0 else 1.0
        self.value = max(self.value, min(1.0, value))
        return self.value

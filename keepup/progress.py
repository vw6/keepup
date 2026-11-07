"""Понятный ход работы по выводу pacman, paru, makepkg и flatpak: этап, что сейчас делается и доля готовности."""
import re
from dataclasses import dataclass

COUNTER = re.compile(r'^\(\s*(\d+)/(\d+)\)\s*(.*)$')
# Этапы: (шаблон строки, название этапа). Вывод бывает и по-русски, и по-английски, в зависимости от языка системы.
STAGES = [
    (re.compile(r'Синхронизация баз|Synchronizing package databases', re.I), 'Обновление списков пакетов'),
    (re.compile(r'Получение исходных файлов|Retrieving sources', re.I), 'Скачивание исходников'),
    (re.compile(r'Запускается build\(\)|Starting build\(\)|Сборка пакета|Making package', re.I), 'Сборка'),
    (re.compile(r'Создание пакета|Creating package|Сжатие пакета|Compressing package', re.I), 'Упаковка'),
    (re.compile(r'загрузка файлов пакетов|загрузка|Retrieving packages|downloading', re.I), 'Скачивание'),
    (re.compile(r'проверка связки ключей|проверка целостности|checking keyring|checking package integrity|'
                r'проверка возможных конфликтов|checking for file conflicts|проверка доступного|checking available', re.I), 'Проверка'),
    (re.compile(r'Обработка изменений пакета|Processing package changes', re.I), 'Установка'),
    (re.compile(r'post-transaction hooks', re.I), 'Настройка системы'),
    (re.compile(r'Resolving dependencies|Разрешение зависимостей', re.I), 'Подбор зависимостей'),
    (re.compile(r'^(Updating|Обновление|Installing|Установка|Uninstalling|Удаление) (app|runtime)/', re.I), 'Flatpak'),
]
ACTION = re.compile(r'^(?:\(\s*\d+/\d+\)\s*)?(обновление|установка|удаление|переустановка|понижение|upgrading|installing|removing|'
                    r'reinstalling|downgrading)\s+(\S+?)(?:\.\.\.)?$', re.I)
WORDS = {'обновление': 'Обновление', 'upgrading': 'Обновление', 'установка': 'Установка', 'installing': 'Установка',
         'удаление': 'Удаление', 'removing': 'Удаление', 'переустановка': 'Переустановка', 'reinstalling': 'Переустановка',
         'понижение': 'Откат', 'downgrading': 'Откат'}


@dataclass
class State:
    stage: str = 'Ждём подтверждения паролем'
    detail: str = ''
    fraction: float | None = None   # None: доля неизвестна, полоска «бежит»


def feed(state, line):
    """Обновляет ход работы по очередной строке вывода; возвращает True, если что-то поменялось."""
    text = line.strip()
    if not text or text.startswith('$ '):
        return False
    before = (state.stage, state.detail, state.fraction)
    if state.stage == 'Ждём подтверждения паролем':
        state.stage = 'Подготовка'
    m = ACTION.match(text)
    if m:
        state.stage = 'Установка' if state.stage != 'Настройка системы' else state.stage
        state.detail = f'{WORDS.get(m.group(1).lower(), m.group(1))}: {m.group(2)}'
    for rule, name in STAGES:
        if rule.search(text):
            if name == 'Flatpak':
                state.stage = 'Установка'
                state.detail = text
            else:
                state.stage = name
                if name not in ('Установка',):
                    state.detail = ''
                state.fraction = None
            break
    c = COUNTER.match(text)
    if c:
        done, total = int(c.group(1)), int(c.group(2))
        if total:
            state.fraction = min(1.0, done / total)
        if state.stage == 'Настройка системы':
            # Названия шагов pacman пишет по-английски; номер шага понятнее.
            state.detail = f'Шаг {done} из {total}'
    return before != (state.stage, state.detail, state.fraction)


# Место этапа на общей полоске: этапы идут в этом порядке, а полоска начинается с первого встреченного,
# поэтому и обновление из репозитория, и сборка из AUR проходят её от начала до конца.
RANGES = {
    'Ждём подтверждения паролем': (0.0, 0.0), 'Подготовка': (0.0, 0.03), 'Подбор зависимостей': (0.03, 0.06),
    'Скачивание исходников': (0.06, 0.12), 'Сборка': (0.12, 0.45), 'Упаковка': (0.45, 0.5),
    'Обновление списков пакетов': (0.5, 0.55), 'Скачивание': (0.55, 0.7), 'Проверка': (0.7, 0.78),
    'Установка': (0.78, 0.9), 'Настройка системы': (0.9, 1.0),
}


class Overall:
    """Общая доля готовности: только растёт, от первого встреченного этапа до конца."""
    def __init__(self):
        self.base = None
        self.value = 0.0

    def update(self, state):
        start, end = RANGES.get(state.stage, (self.value, self.value))
        if state.stage in ('Ждём подтверждения паролем',):
            return self.value
        if self.base is None:
            self.base = start
        inner = state.fraction if state.fraction is not None else 0.0
        raw = start + (end - start) * inner
        span = 1.0 - self.base
        value = (raw - self.base) / span if span > 0 else 1.0
        self.value = max(self.value, min(1.0, value))
        return self.value

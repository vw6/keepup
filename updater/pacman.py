"""Сведения о пакетах pacman: обновления, кэш прошлых версий, связанные пакеты, история и нужна ли перезагрузка."""
import os
import re
import subprocess
from dataclasses import dataclass, field
from datetime import datetime

LOCAL_DB = '/var/lib/pacman/local'
CACHE_DIR = '/var/cache/pacman/pkg'
LOG_FILE = '/var/log/pacman.log'
PKG_EXT = re.compile(r'\.pkg\.tar(\.(zst|xz|gz|bz2))?$')
LOG_LINE = re.compile(r'^\[([^\]]+)\] \[ALPM\] (upgraded|downgraded|installed|removed|reinstalled) (\S+) \((.*)\)$')

# После обновления этих пакетов изменения вступают в силу только с перезагрузкой.
REBOOT_PACKAGES = re.compile(r'^(linux(-[a-z0-9]+)*|nvidia.*|mesa|lib32-mesa|vulkan-.*|systemd|glibc|lib32-glibc|'
                             r'amd-ucode|intel-ucode|linux-firmware.*|dbus.*|gnome-shell|mutter)$')
KERNEL_NAME = re.compile(r'^linux(-[a-z0-9]+)*$')
NOT_KERNEL = re.compile(r'-(headers|docs|api-headers|firmware.*|tools.*|atm|zfs|nvidia.*|r8125|r8168)$')


@dataclass
class Update:
    name: str
    old: str
    new: str
    needs_reboot: bool = False


@dataclass
class CachedVersion:
    name: str
    version: str
    arch: str
    path: str


@dataclass
class Installed:
    name: str
    version: str
    base: str
    install_date: datetime | None = None
    cached: list[CachedVersion] = field(default_factory=list)


@dataclass
class LogEntry:
    when: datetime
    action: str
    name: str
    versions: str


def _split_evr(v):
    epoch, _, rest = v.partition(':') if ':' in v else ('0', '', v)
    ver, _, rel = rest.rpartition('-') if '-' in rest else (rest, '', '')
    return int(epoch or 0), ver, rel


def _alnum(c):
    return c.isascii() and c.isalnum()


def _rpmvercmp(a, b):
    # Тот же обход, что rpmvercmp в alpm: куски цифр и букв по очереди, разделители значат только «был ли он».
    if a == b:
        return 0
    i = j = 0
    while i < len(a) and j < len(b):
        si = i
        while i < len(a) and not _alnum(a[i]):
            i += 1
        sj = j
        while j < len(b) and not _alnum(b[j]):
            j += 1
        if i >= len(a) or j >= len(b):
            break
        # Разной длины разделители: версия с более длинным новее.
        if i - si != j - sj:
            return -1 if i - si < j - sj else 1
        ei, ej = i, j
        if a[i].isdigit():
            while ei < len(a) and a[ei].isdigit():
                ei += 1
            while ej < len(b) and b[ej].isdigit():
                ej += 1
            numeric = True
        else:
            while ei < len(a) and a[ei].isascii() and a[ei].isalpha():
                ei += 1
            while ej < len(b) and b[ej].isascii() and b[ej].isalpha():
                ej += 1
            numeric = False
        x, y = a[i:ei], b[j:ej]
        if not y:
            # Кусок разного вида: цифры новее букв.
            return 1 if numeric else -1
        if numeric:
            x, y = x.lstrip('0'), y.lstrip('0')
            if len(x) != len(y):
                return 1 if len(x) > len(y) else -1
        if x != y:
            return 1 if x > y else -1
        i, j = ei, ej
    rest_a, rest_b = a[i:], b[j:]
    if not rest_a and not rest_b:
        return 0
    # Хвост из букв сразу за последним куском (1.0a) делает версию старее, хвост через разделитель новее.
    if (not rest_a and not (rest_b[:1].isascii() and rest_b[:1].isalpha())) or (rest_a[:1].isascii() and rest_a[:1].isalpha()):
        return -1
    return 1


def vercmp(a, b):
    """Сравнение версий как у pacman (эпоха, версия, сборка): отрицательное, если a старше b."""
    ea, va, ra = _split_evr(a)
    eb, vb, rb = _split_evr(b)
    if ea != eb:
        return 1 if ea > eb else -1
    c = _rpmvercmp(va, vb)
    if c or not ra or not rb:
        return c
    return _rpmvercmp(ra, rb)


def parse_checkupdates(text):
    """Строки вида «имя старая -> новая» из checkupdates."""
    updates = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] == '->':
            updates.append(Update(parts[0], parts[1], parts[3], bool(REBOOT_PACKAGES.match(parts[0]))))
    return updates


def list_updates():
    """Доступные обновления из репозиториев. checkupdates берёт свежие списки во временную базу и систему не меняет."""
    out = subprocess.run(['checkupdates', '--nocolor'], capture_output=True, stdin=subprocess.DEVNULL, text=True)
    # Код 2 значит «обновлений нет», 1 это ошибка сети или базы.
    if out.returncode == 1:
        raise RuntimeError(out.stderr.strip() or 'checkupdates завершился с ошибкой')
    return parse_checkupdates(out.stdout)


def parse_desc(text):
    """Поля файла desc локальной базы: %ИМЯ% и значения ниже до пустой строки."""
    fields, key = {}, None
    for line in text.splitlines():
        if line.startswith('%') and line.endswith('%'):
            key = line.strip('%')
            fields[key] = []
        elif line and key:
            fields[key].append(line)
        else:
            key = None
    return fields


def installed(db=LOCAL_DB):
    """Установленные пакеты из локальной базы pacman, без запуска pacman."""
    result = {}
    for entry in os.listdir(db):
        try:
            with open(os.path.join(db, entry, 'desc'), encoding='utf-8') as f:
                fields = parse_desc(f.read())
        except OSError:
            continue
        name = fields.get('NAME', [''])[0]
        if not name:
            continue
        stamp = fields.get('INSTALLDATE', [''])[0]
        result[name] = Installed(name, fields.get('VERSION', [''])[0], fields.get('BASE', [name])[0],
                                 datetime.fromtimestamp(int(stamp)) if stamp.isdigit() else None)
    return result


def parse_cache_name(filename):
    """«имя-версия-сборка-арх.pkg.tar.zst» в (имя, версия-сборка, арх); имя само может содержать дефисы."""
    if filename.endswith('.sig') or not PKG_EXT.search(filename):
        return None
    stem = PKG_EXT.sub('', filename)
    parts = stem.rsplit('-', 3)
    if len(parts) != 4:
        return None
    name, ver, rel, arch = parts
    return name, f'{ver}-{rel}', arch


def cache(directories=(CACHE_DIR,)):
    """Все версии пакетов в кэше pacman и в других каталогах сборок: имя -> список версий от новой к старой."""
    if isinstance(directories, str):
        directories = [directories]
    by_name = {}
    for directory in directories:
        try:
            files = os.listdir(directory)
        except OSError:
            continue
        for filename in files:
            parsed = parse_cache_name(filename)
            if parsed:
                name, version, arch = parsed
                by_name.setdefault(name, []).append(CachedVersion(name, version, arch, os.path.join(directory, filename)))
    for name, versions in by_name.items():
        unique = {}
        for v in versions:
            unique.setdefault((v.version, v.arch), v)
        by_name[name] = sorted(unique.values(), key=_VersionKey, reverse=True)
    return by_name


class _VersionKey:
    """Ключ сортировки через vercmp; дубли одной версии разных сборок архитектуры остаются рядом."""
    def __init__(self, item):
        self.version = item.version

    def __lt__(self, other):
        return vercmp(self.version, other.version) < 0


def with_cache(packages, cached):
    """Прикладывает к установленным пакетам версии из кэша, кроме уже установленной."""
    for pkg in packages.values():
        pkg.cached = [c for c in cached.get(pkg.name, []) if c.version != pkg.version]
    return packages


def related(name, packages):
    """Пакеты, которые откатываются вместе: та же сборка (pkgbase) и 32-разрядная пара."""
    pkg = packages.get(name)
    if not pkg:
        return [name]
    group = {p.name for p in packages.values() if p.base == pkg.base}
    pair = name[6:] if name.startswith('lib32-') else 'lib32-' + name
    if pair in packages:
        pair_base = packages[pair].base
        group |= {p.name for p in packages.values() if p.base == pair_base}
    return sorted(group, key=lambda n: (n != name, n))


def rollback_files(names, version, packages, cached):
    """Файлы кэша для отката группы на одну версию; для пакета без такой версии в кэше возвращается None."""
    files = {}
    for n in names:
        match = [c for c in cached.get(n, []) if c.version == version]
        files[n] = match[0].path if match else None
    return files


def kernels(packages):
    """Установленные ядра: сами пакеты ядра, без заголовков, документации и модулей."""
    return sorted((p for p in packages.values() if KERNEL_NAME.match(p.name) and not NOT_KERNEL.search(p.name)
                   and os.path.isdir('/usr/lib/modules') and _has_modules(p)), key=lambda p: p.name)


def _has_modules(pkg):
    # Пакет ядра кладёт свой vmlinuz в каталог модулей; так отсекаются linux-firmware и подобные.
    for d in os.listdir('/usr/lib/modules'):
        if os.path.exists(os.path.join('/usr/lib/modules', d, 'pkgbase')):
            with open(os.path.join('/usr/lib/modules', d, 'pkgbase')) as f:
                if f.read().strip() == pkg.name:
                    return True
    return False


def running_kernel():
    """Версия и пакет запущенного ядра."""
    release = os.uname().release
    base_file = os.path.join('/usr/lib/modules', release, 'pkgbase')
    base = None
    if os.path.exists(base_file):
        with open(base_file) as f:
            base = f.read().strip()
    return release, base


def boot_time():
    with open('/proc/stat') as f:
        for line in f:
            if line.startswith('btime '):
                return datetime.fromtimestamp(int(line.split()[1]))
    return None


def parse_log(text):
    entries = []
    for line in text.splitlines():
        m = LOG_LINE.match(line)
        if m:
            stamp = m.group(1)
            try:
                when = datetime.fromisoformat(stamp)
            except ValueError:
                continue
            entries.append(LogEntry(when.replace(tzinfo=None), m.group(2), m.group(3), m.group(4)))
    return entries


def history(path=LOG_FILE):
    try:
        with open(path, encoding='utf-8', errors='replace') as f:
            return parse_log(f.read())
    except OSError:
        return []


def reboot_reasons(log, boot):
    """Пакеты из REBOOT_PACKAGES, изменённые после последней загрузки."""
    if boot is None:
        return []
    seen = {}
    for e in log:
        if e.when > boot and e.action in ('upgraded', 'downgraded', 'reinstalled') and REBOOT_PACKAGES.match(e.name):
            seen[e.name] = e
    return list(seen.values())

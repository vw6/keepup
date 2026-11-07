"""Ядра: что есть в репозиториях, какие модули ставить вместе с ядром и какое ядро загружается первым в limine."""
import os
import re
import subprocess
from dataclasses import dataclass, field

LIMINE_DEFAULTS = '/etc/default/limine'
LIMINE_CONF = '/etc/limine-entry-tool.conf'
HELPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'helpers', 'set_default_kernel.py')
NAME = re.compile(r'^[a-z0-9][a-z0-9.+_-]*$')


@dataclass
class RepoKernel:
    name: str
    repo: str
    version: str
    installed: bool
    description: str = ''
    modules: list[str] = field(default_factory=list)


def parse_sync_list(text):
    """Строки «репозиторий имя версия [установлен]» из pacman -Sl: имя -> (репозиторий, версия, установлен)."""
    found = {}
    for line in text.splitlines():
        f = line.split()
        if len(f) >= 3 and f[1] not in found:
            found[f[1]] = (f[0], f[2], len(f) > 3)
    return found


def repo_kernels(sync):
    """Ядро это пакет, у которого в репозитории есть пара «-headers»."""
    return sorted(n for n in sync if not n.endswith('-headers') and n + '-headers' in sync)


def module_suffixes(installed_names, kernel_names):
    """Внешние модули, которые стоят хотя бы у одного ядра (r8125, nvidia и подобные): их суффиксы."""
    suffixes = set()
    for k in kernel_names:
        for n in installed_names:
            if n.startswith(k + '-') and n != k + '-headers':
                rest = n[len(k) + 1:]
                # linux-cachyos-lts начинается с linux-cachyos-, но это другое ядро, а не модуль.
                if rest and not any(n == other or n.startswith(other + '-') for other in kernel_names if other != k and len(other) > len(k)):
                    suffixes.add(rest)
    return sorted(suffixes)


def modules_for(kernel, suffixes, sync):
    return [f'{kernel}-{s}' for s in suffixes if f'{kernel}-{s}' in sync]


def descriptions(names):
    """Описания пакетов одним вызовом pacman -Si."""
    if not names:
        return {}
    out = subprocess.run(['pacman', '-Si', *names], capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'})
    result, name = {}, None
    for line in out.stdout.splitlines():
        if line.startswith('Name'):
            name = line.split(':', 1)[1].strip()
        elif line.startswith('Description') and name and name not in result:
            result[name] = line.split(':', 1)[1].strip()
    return result


def available(installed_names):
    out = subprocess.run(['pacman', '-Sl'], capture_output=True, text=True, env={**os.environ, 'LC_ALL': 'C'})
    sync = parse_sync_list(out.stdout)
    names = repo_kernels(sync)
    suffixes = module_suffixes(installed_names, [n for n in names if n in installed_names])
    desc = descriptions(names)
    return [RepoKernel(n, sync[n][0], sync[n][1], n in installed_names, desc.get(n, ''), modules_for(n, suffixes, sync))
            for n in names]


def parse_boot_order(text):
    """Значение BOOT_ORDER из файла настроек limine (последнее присваивание побеждает)."""
    order = None
    for line in text.splitlines():
        m = re.match(r'^\s*BOOT_ORDER\s*=\s*"?([^"#]*)"?', line)
        if m:
            order = [x.strip() for x in m.group(1).split(',') if x.strip()]
    return order


def boot_order():
    for path in (LIMINE_DEFAULTS, LIMINE_CONF):
        try:
            with open(path) as f:
                order = parse_boot_order(f.read())
        except OSError:
            continue
        if order:
            return order
    return []


def default_kernel(order, kernel_names):
    """Ядро, которое limine загрузит первым: первое имя или шаблон в BOOT_ORDER, под который подходит ядро."""
    for pattern in order:
        rx = re.compile('^' + re.escape(pattern).replace(r'\*', '.*') + '$')
        hits = [k for k in kernel_names if rx.match(k)]
        if len(hits) == 1:
            return hits[0]
        if hits:
            # Шаблон «*» подходит многим, а порядок между ними решает сам limine: точно назвать первое нельзя.
            return None
    return None


def new_order(order, kernel):
    """BOOT_ORDER, где выбранное ядро стоит первым, а прежние точные имена ядер убраны."""
    rest = [p for p in order if '*' in p or not p.startswith('linux')]
    return [kernel] + (rest or ['*', '*fallback', 'Snapshots'])


def install_command(names):
    # Новое ядро ставится вместе с полным обновлением: в Arch установка на устаревших списках пакетов не поддерживается.
    return ['pkexec', 'pacman', '-Syu', '--noconfirm', '--needed', *names]


def set_default_command(kernel):
    if not NAME.match(kernel):
        raise ValueError(kernel)
    return ['pkexec', '/usr/bin/python3', HELPER, kernel]

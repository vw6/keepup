"""Приложения Flatpak: список, обновления и прошлые сборки из журнала удалённого хранилища для отката."""
import os
import re
import shutil
import subprocess
from dataclasses import dataclass

ENV = {**os.environ, 'LC_ALL': 'C'}


@dataclass
class App:
    app_id: str
    name: str
    version: str
    branch: str
    origin: str
    installation: str
    new_version: str = ''


@dataclass
class Commit:
    commit: str
    subject: str
    date: str


def available():
    return shutil.which('flatpak') is not None


def _run(args, timeout=60):
    return subprocess.run(['flatpak', *args], capture_output=True, stdin=subprocess.DEVNULL, text=True, env=ENV, timeout=timeout)


def parse_list(text):
    apps = []
    for line in text.splitlines():
        f = line.split('\t')
        if len(f) >= 6:
            apps.append(App(*f[:6]))
    return apps


def installed():
    return parse_list(_run(['list', '--app', '--columns=application,name,version,branch,origin,installation']).stdout)


def parse_updates(text):
    """Из remote-ls --updates: приложение -> новая версия (у некоторых приложений версия пустая)."""
    found = {}
    for line in text.splitlines():
        f = line.split('\t')
        if f and f[0]:
            found[f[0]] = f[1] if len(f) > 1 else ''
    return found


def with_updates(apps):
    out = _run(['remote-ls', '--updates', '--app', '--columns=application,version'], timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or 'flatpak remote-ls завершился с ошибкой')
    found = parse_updates(out.stdout)
    for app in apps:
        if app.app_id in found:
            app.new_version = found[app.app_id] or 'новая сборка'
    return apps


def parse_log(text):
    """Сборки из remote-info --log, от новой к старой."""
    commits = []
    for block in re.split(r'\n\s*\n', text):
        c = re.search(r'^\s*Commit:\s*(\S+)', block, re.M)
        if c:
            subject = re.search(r'^\s*Subject:\s*(.*)$', block, re.M)
            date = re.search(r'^\s*Date:\s*(.*)$', block, re.M)
            commits.append(Commit(c.group(1), subject.group(1).strip() if subject else '', date.group(1).strip() if date else ''))
    return commits


def history(app):
    out = _run(['remote-info', '--log', f'--{app.installation}', app.origin, f'{app.app_id}//{app.branch}'], timeout=120)
    if out.returncode != 0:
        raise RuntimeError(out.stderr.strip() or 'flatpak remote-info завершился с ошибкой')
    return parse_log(out.stdout)


def current_commit(app):
    out = _run(['info', f'--{app.installation}', '--show-commit', f'{app.app_id}//{app.branch}'])
    return out.stdout.strip()


def upgrade_command():
    # Для системной установки flatpak сам спрашивает пароль через polkit.
    return ['flatpak', 'update', '-y', '--noninteractive']


def update_one_command(app):
    return ['flatpak', 'update', '-y', '--noninteractive', f'--{app.installation}', f'{app.app_id}//{app.branch}']


def uninstall_commands(app, delete_data=False, unused=False):
    """Удаление приложения; по желанию с его данными в ~/.var/app и со средами, которые больше никому не нужны."""
    cmds = [['flatpak', 'uninstall', '-y', '--noninteractive', f'--{app.installation}', *(['--delete-data'] if delete_data else []),
             f'{app.app_id}//{app.branch}']]
    if unused:
        cmds.append(['flatpak', 'uninstall', '-y', '--noninteractive', f'--{app.installation}', '--unused'])
    return cmds


def checkout_command(app, commit):
    # Конкретную сборку в системную установку flatpak ставит только от root. Среды и переводы не трогаются:
    # меняется одно приложение.
    cmd = ['flatpak', 'update', '-y', '--noninteractive', '--no-deps', '--no-related', f'--{app.installation}',
           f'--commit={commit}', f'{app.app_id}//{app.branch}']
    return (['pkexec'] + cmd) if app.installation == 'system' else cmd

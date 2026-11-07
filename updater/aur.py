"""Пакеты не из репозиториев (AUR и прочие): список, обновления через paru и собранные paru версии для отката."""
import glob
import os
import subprocess

from .pacman import Update, parse_checkupdates

PARU_CLONE = os.path.expanduser('~/.cache/paru/clone')


def available():
    return subprocess.run(['which', 'paru'], capture_output=True).returncode == 0


def foreign():
    """Установленные пакеты, которых нет ни в одном подключённом репозитории: имя -> версия."""
    out = subprocess.run(['pacman', '-Qm'], capture_output=True, text=True)
    return dict(line.split(maxsplit=1) for line in out.stdout.splitlines() if ' ' in line)


def list_updates():
    """Обновления из AUR по данным paru; у пакетов -git вместо версии бывает «latest-commit»."""
    out = subprocess.run(['paru', '-Qua'], capture_output=True, text=True, timeout=120)
    # paru отвечает кодом 1 и пустым выводом, когда обновлений нет.
    if out.returncode not in (0, 1) or (out.returncode == 1 and out.stderr.strip() and not out.stdout.strip()):
        raise RuntimeError(out.stderr.strip() or 'paru завершился с ошибкой')
    return [Update(u.name, u.old, u.new) for u in parse_checkupdates(out.stdout)]


def build_dirs():
    """Каталоги, где paru оставляет собранные пакеты; их версии годятся для отката."""
    return sorted(d for d in glob.glob(os.path.join(PARU_CLONE, '*')) if os.path.isdir(d))


def upgrade_command():
    # Сборка идёт от пользователя, а установку paru просит через pkexec, то есть через окно пароля GNOME.
    return ['paru', '-Sua', '--noconfirm', '--skipreview', '--batchinstall', '--sudo', 'pkexec']

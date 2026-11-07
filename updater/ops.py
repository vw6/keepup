"""Действия с правами root через pkexec: полное обновление и установка версий из кэша; вывод идёт построчно."""
import os
import subprocess
import threading

from gi.repository import GLib

LOCK = '/var/lib/pacman/db.lck'


def busy():
    """pacman уже занят другой программой."""
    return os.path.exists(LOCK)


def upgrade_command():
    # Только полное обновление: выборочное при устаревших списках пакетов ставит Arch в частичное обновление.
    return ['pkexec', 'pacman', '-Syu', '--noconfirm']


def install_files_command(paths):
    return ['pkexec', 'pacman', '-U', '--noconfirm', *paths]


def run(cmd, on_line, on_done):
    """Запуск в отдельном потоке; on_line и on_done вызываются в главном потоке GTK.
    cmd это одна команда или список команд: они идут по очереди, пока какая-то не завершится с ошибкой."""
    commands = cmd if cmd and isinstance(cmd[0], list) else [cmd]

    def work():
        code = 0
        for one in commands:
            try:
                proc = subprocess.Popen(one, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                                        env={**os.environ, 'LC_ALL': os.environ.get('LANG', 'C.UTF-8')})
            except OSError as e:
                GLib.idle_add(on_line, str(e))
                code = -1
                break
            for line in proc.stdout:
                GLib.idle_add(on_line, line.rstrip('\n'))
            code = proc.wait()
            if code != 0:
                break
        GLib.idle_add(on_done, code)
    threading.Thread(target=work, daemon=True).start()

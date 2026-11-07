"""Запускается от root через pkexec: ставит файлы пакетов из свежих списков и помечает зависимости."""
import re
import subprocess
import sys

URL = re.compile(r'^(https?://[^\s]+|/var/cache/pacman/pkg/[^/\s]+)\.pkg\.tar(\.(zst|xz|gz|bz2))?$')
NAME = re.compile(r'^[a-z0-9@._+-]+$')


def main():
    deps = [a[6:] for a in sys.argv[1:] if a.startswith('--dep=')]
    # Пакет, который уже лежит в кэше, pacman -Sp называет адресом file://.
    urls = [a.removeprefix('file://') for a in sys.argv[1:] if not a.startswith('--dep=')]
    if not urls or not all(URL.match(u) for u in urls) or not all(NAME.match(d) for d in deps):
        sys.exit('нужны адреса файлов пакетов')
    code = subprocess.run(['pacman', '-U', '--noconfirm', *urls]).returncode
    if code == 0 and deps:
        code = subprocess.run(['pacman', '-D', '--asdeps', *deps]).returncode
    sys.exit(code)


if __name__ == '__main__':
    main()

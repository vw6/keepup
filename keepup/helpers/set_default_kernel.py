"""Запускается от root через pkexec: ставит ядро первым в BOOT_ORDER limine и пересобирает меню загрузки."""
import os
import re
import shutil
import subprocess
import sys
import time

DEFAULTS = '/etc/default/limine'
NAME = re.compile(r'^[a-z0-9][a-z0-9.+_-]*$')


def main():
    if len(sys.argv) != 2 or not NAME.match(sys.argv[1]):
        sys.exit('нужно одно имя ядра')
    kernel = sys.argv[1]
    if not os.path.isdir('/usr/lib/modules') or not any(
            open(os.path.join('/usr/lib/modules', d, 'pkgbase')).read().strip() == kernel
            for d in os.listdir('/usr/lib/modules') if os.path.exists(os.path.join('/usr/lib/modules', d, 'pkgbase'))):
        sys.exit(f'ядро {kernel} не установлено')

    text = open(DEFAULTS).read() if os.path.exists(DEFAULTS) else ''
    order = None
    for line in text.splitlines():
        m = re.match(r'^\s*BOOT_ORDER\s*=\s*"?([^"#]*)"?', line)
        if m:
            order = [x.strip() for x in m.group(1).split(',') if x.strip()]
    rest = [p for p in (order or []) if '*' in p or not p.startswith('linux')] or ['*', '*fallback', 'Snapshots']
    line = 'BOOT_ORDER="' + ', '.join([kernel] + rest) + '"'

    if os.path.exists(DEFAULTS):
        backup = f'{DEFAULTS}.before-updater-{time.strftime("%Y%m%d-%H%M%S")}'
        shutil.copy2(DEFAULTS, backup)
        print(f'копия прежних настроек: {backup}', flush=True)
    if order is None:
        text = text.rstrip('\n') + ('\n' if text else '') + line + '\n'
    else:
        text = re.sub(r'(?m)^\s*BOOT_ORDER\s*=.*$', line, text)
    with open(DEFAULTS, 'w') as f:
        f.write(text)
    print(f'{DEFAULTS}: {line}', flush=True)
    sys.exit(subprocess.run(['limine-update']).returncode)


if __name__ == '__main__':
    main()

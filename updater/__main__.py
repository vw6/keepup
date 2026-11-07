"""Запуск: python3 -m updater."""
import os
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio  # noqa: E402

from .window import Window  # noqa: E402


def main():
    # --page ВКЛАДКА открывает окно сразу на ней: overview, news, packages, aur, flatpak, kernels, history.
    args = sys.argv[1:]
    page = args[args.index('--page') + 1] if '--page' in args[:-1] else 'overview'
    # UPDATER_SEPARATE=1 запускает отдельный экземпляр, не трогая уже открытое окно (для проверок).
    flags = Gio.ApplicationFlags.NON_UNIQUE if os.environ.get('UPDATER_SEPARATE') == '1' else Gio.ApplicationFlags.DEFAULT_FLAGS
    app = Adw.Application(application_id='ru.vw6.Updater', flags=flags)
    app.connect('activate', lambda a: (a.get_active_window() or Window(a, page)).present())
    return app.run(sys.argv[:1])


if __name__ == '__main__':
    sys.exit(main())

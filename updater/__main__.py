"""Запуск: python3 -m updater."""
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw  # noqa: E402

from .window import Window  # noqa: E402


def main():
    app = Adw.Application(application_id='ru.vw6.Updater')
    app.connect('activate', lambda a: (a.get_active_window() or Window(a)).present())
    return app.run(sys.argv)


if __name__ == '__main__':
    sys.exit(main())

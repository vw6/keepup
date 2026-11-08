"""KeepUp: обновления, версии и откаты для Arch и CachyOS в стиле GNOME."""
import os

NAME = 'KeepUp'
APP_ID = 'ru.vw6.KeepUp'
VERSION = '0.3.5'
USER_AGENT = f'keepup/{VERSION}'


def cache_dir(*parts):
    return os.path.join(os.environ.get('XDG_CACHE_HOME', os.path.expanduser('~/.cache')), 'keepup', *parts)


def data_dir(*parts):
    return os.path.join(os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share')), 'keepup', *parts)


def config_dir(*parts):
    return os.path.join(os.environ.get('XDG_CONFIG_HOME', os.path.expanduser('~/.config')), 'keepup', *parts)

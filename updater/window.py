"""Главное окно: боковые вкладки «Обзор», «Новости», «Пакеты», «AUR», «Flatpak», «Ядра», «История»."""
import os
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gdk, GLib, Gtk, Pango  # noqa: E402

from . import appstream, archive, aur, flatpak, kernels, news, notes, ops, pacman  # noqa: E402

PAGES = [
    ('overview', 'Обзор', 'view-grid-symbolic'),
    ('news', 'Новости Arch', 'x-office-document-symbolic'),
    ('packages', 'Пакеты', 'system-software-install-symbolic'),
    ('aur', 'AUR', 'application-x-addon-symbolic'),
    ('flatpak', 'Flatpak', 'applications-system-symbolic'),
    ('kernels', 'Ядра', 'computer-symbolic'),
    ('history', 'История', 'document-open-recent-symbolic'),
]
ROLLBACK_ROWS = 80
HISTORY_DAYS = 30

CSS = b"""
.pill { border-radius: 999px; padding: 1px 9px; font-size: 0.85em; }
.pill.warn { background: alpha(@warning_bg_color, 0.25); color: @warning_fg_color; }
.pill.info { background: alpha(@accent_bg_color, 0.2); color: @accent_color; }
.pill.ok { background: alpha(@success_bg_color, 0.2); color: @success_color; }
.log { font-family: monospace; font-size: 0.9em; }
"""


def pill(text, kind='info'):
    label = Gtk.Label(label=text, valign=Gtk.Align.CENTER)
    label.add_css_class('pill')
    label.add_css_class(kind)
    return label


def row(title, subtitle='', activatable=False):
    # Разметку выключаем до текста: иначе GTK пробует разобрать «<admin@cachyos.org>» как тег и ругается.
    r = Adw.ActionRow(activatable=activatable, use_markup=False)
    r.set_title(title)
    r.set_subtitle(subtitle)
    r.set_subtitle_lines(3)
    return r


def expander(title, subtitle='', expanded=False):
    r = Adw.ExpanderRow(use_markup=False, expanded=expanded)
    r.set_title(title)
    r.set_subtitle(subtitle)
    return r


def background(work, done):
    """work() в отдельном потоке, done(результат или исключение) в главном."""
    def run():
        try:
            result = work()
        except Exception as e:  # ошибка показывается в окне, а не роняет его
            result = e
        GLib.idle_add(done, result)
    threading.Thread(target=run, daemon=True).start()


class Snapshot:
    """Всё, что окно знает о системе на момент последней проверки."""
    def __init__(self):
        self.updates = []
        self.updates_error = None
        self.packages = {}
        self.cache = {}
        self.log = []
        self.reboot = []
        self.kernel = ('', None)
        self.news = []
        self.news_fresh = False
        self.foreign = {}
        self.aur_updates = []
        self.aur_error = None
        self.flatpaks = []
        self.flatpak_error = None
        self.repo_kernels = []
        self.boot_order = []
        self.app_info = {}
        self.foreign_files = {}
        self.icons = {}

    @classmethod
    def collect(cls):
        s = cls()
        # Сетевые запросы идут одновременно: репозитории, AUR, Flatpak и новости друг от друга не зависят.
        with ThreadPoolExecutor(4) as pool:
            repo = pool.submit(pacman.list_updates)
            feed = pool.submit(news.fetch)
            aur_upd = pool.submit(aur.list_updates) if aur.available() else None
            flat = pool.submit(lambda: flatpak.with_updates(flatpak.installed())) if flatpak.available() else None
            s.packages = pacman.installed()
            s.cache = pacman.cache([pacman.CACHE_DIR, *aur.build_dirs()])
            pacman.with_cache(s.packages, s.cache)
            s.log = pacman.history()
            s.kernel = pacman.running_kernel()
            s.reboot = pacman.reboot_reasons(s.log, pacman.boot_time())
            s.foreign = aur.foreign()
            s.foreign_files = aur.files_of(list(s.foreign)) if s.foreign else {}
            s.icons = pacman.desktop_icons()
            s.repo_kernels = kernels.available(set(s.packages))
            s.boot_order = kernels.boot_order()
            s.updates, s.updates_error = _result(repo)
            s.news, s.news_fresh = feed.result()
            if aur_upd:
                s.aur_updates, s.aur_error = _result(aur_upd)
            if flat:
                s.flatpaks, s.flatpak_error = _result(flat)
        news.mark(s.news, [u.name for u in s.updates + s.aur_updates])
        by_remote = defaultdict(list)
        for app in s.flatpaks:
            by_remote[(app.origin, app.installation)].append(app.app_id)
        for (remote, installation), ids in by_remote.items():
            s.app_info.update(appstream.lookup(remote, installation, ids))
        return s


def _result(future):
    try:
        return future.result(), None
    except Exception as e:  # ошибка одного источника не мешает остальным
        return [], str(e)


class OperationDialog(Adw.Dialog):
    """Окно с выводом команды, пока идёт действие; закрыть можно, только когда оно кончилось."""
    def __init__(self, title, cmd, on_finished):
        super().__init__(title=title, content_width=760, content_height=520, can_close=False)
        self.on_finished = on_finished
        view = Adw.ToolbarView()
        header = Adw.HeaderBar()
        self.spinner = Adw.Spinner()
        header.pack_start(self.spinner)
        view.add_top_bar(header)
        self.status = Adw.Banner(title='Ждём подтверждения паролем…', revealed=True)
        view.add_top_bar(self.status)
        self.buffer = Gtk.TextBuffer()
        text = Gtk.TextView(buffer=self.buffer, editable=False, cursor_visible=False, monospace=True,
                            wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=12, bottom_margin=12, left_margin=12, right_margin=12)
        self.scroll = Gtk.ScrolledWindow(child=text, vexpand=True)
        view.set_content(self.scroll)
        self.set_child(view)
        ops.run(cmd, self.add_line, self.finish)

    def add_line(self, line):
        if self.buffer.get_char_count() == 0:
            self.status.set_title('Идёт работа')
        self.buffer.insert(self.buffer.get_end_iter(), line + '\n')
        adj = self.scroll.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()))

    def finish(self, code):
        self.spinner.set_visible(False)
        messages = {0: 'Готово', 126: 'Отменено: пароль не введён', 127: 'Нет прав на это действие'}
        self.status.set_title(messages.get(code, f'Завершилось с ошибкой (код {code}), подробности выше'))
        self.set_can_close(True)
        self.on_finished(code)


class Window(Adw.ApplicationWindow):
    def __init__(self, app, page='overview'):
        super().__init__(application=app, title='Обновления', default_width=1040, default_height=720)
        provider = Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_display(self.get_display(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        self.snap = Snapshot()
        self.rollback_query = ''
        self.toasts = Adw.ToastOverlay()

        split = Adw.NavigationSplitView()
        split.set_sidebar(Adw.NavigationPage(title='Обновления', child=self._sidebar()))
        self.content_title = Adw.WindowTitle(title=PAGES[0][1])
        content = Adw.ToolbarView()
        header = Adw.HeaderBar(title_widget=self.content_title)
        self.refresh_button = Gtk.Button(icon_name='view-refresh-symbolic', tooltip_text='Проверить заново')
        self.refresh_button.connect('clicked', lambda *_: self.refresh())
        header.pack_end(self.refresh_button)
        content.add_top_bar(header)
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.holders = {}
        for key, _title, _icon in PAGES:
            holder = Adw.Bin()
            self.holders[key] = holder
            self.stack.add_named(holder, key)
        content.set_content(self.stack)
        # Карточка приложения или пакета открывается как страница поверх вкладки, со стрелкой «назад» в заголовке.
        self.nav = Adw.NavigationView()
        self.nav.add(Adw.NavigationPage(title=PAGES[0][1], tag='root', child=content))
        self.content_page = Adw.NavigationPage(title=PAGES[0][1], child=self.nav)
        split.set_content(self.content_page)
        self.split = split

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 640sp'))
        bp.add_setter(split, 'collapsed', True)
        self.add_breakpoint(bp)

        self.toasts.set_child(split)
        self.set_content(self.toasts)
        self.open_page(page if page in dict((k, t) for k, t, _ in PAGES) else 'overview')
        self.show_loading()
        self.refresh()

    # Боковая панель

    def _sidebar(self):
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        listbox.add_css_class('navigation-sidebar')
        for key, title, icon in PAGES:
            box = Gtk.Box(spacing=12, margin_top=6, margin_bottom=6, margin_start=6)
            box.append(Gtk.Image(icon_name=icon))
            box.append(Gtk.Label(label=title, xalign=0))
            r = Gtk.ListBoxRow(child=box)
            r.page_key = key
            listbox.append(r)
        listbox.connect('row-selected', self.on_page_selected)
        self.sidebar_list = listbox
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(Gtk.ScrolledWindow(child=listbox, vexpand=True))
        return view

    def on_page_selected(self, _box, r):
        if r is None:
            return
        if hasattr(self, 'nav'):
            self.nav.pop_to_tag('root')
        title = dict((k, t) for k, t, _ in PAGES)[r.page_key]
        self.stack.set_visible_child_name(r.page_key)
        self.content_title.set_title(title)
        self.content_page.set_title(title)
        self.split.set_show_content(True)

    # Данные

    def show_loading(self):
        for holder in self.holders.values():
            holder.set_child(Adw.StatusPage(title='Проверяем систему…', child=Adw.Spinner()))

    def refresh(self):
        self.refresh_button.set_sensitive(False)
        background(Snapshot.collect, self.on_snapshot)

    def on_snapshot(self, result):
        self.refresh_button.set_sensitive(True)
        if isinstance(result, Exception):
            self.toast(f'Проверка не удалась: {result}')
            return
        self.snap = result
        for key, build in (('overview', self.build_overview), ('news', self.build_news), ('packages', self.build_packages),
                           ('aur', self.build_aur), ('flatpak', self.build_flatpak),
                           ('kernels', self.build_kernels), ('history', self.build_history)):
            self.holders[key].set_child(build())

    def toast(self, text):
        toast = Adw.Toast(timeout=5, use_markup=False)
        toast.set_title(text)
        self.toasts.add_toast(toast)

    # Обзор

    def build_overview(self):
        s = self.snap
        page = Adw.PreferencesPage()
        state = Adw.PreferencesGroup(title='Состояние')
        page.add(state)

        if s.updates_error:
            r = row('Не удалось проверить обновления', s.updates_error)
            r.add_prefix(Gtk.Image(icon_name='dialog-warning-symbolic'))
        elif s.updates:
            reboot_count = sum(u.needs_reboot for u in s.updates)
            sub = f'из них после {reboot_count} понадобится перезагрузка' if reboot_count else 'перезагрузка после них не понадобится'
            r = row(f'Доступно обновлений: {len(s.updates)}', sub)
            button = Gtk.Button(label='Обновить всё', valign=Gtk.Align.CENTER)
            button.add_css_class('suggested-action')
            button.connect('clicked', lambda *_: self.confirm_upgrade())
            r.add_suffix(button)
        else:
            r = row('Система обновлена', 'новых версий в репозиториях нет')
            r.add_prefix(Gtk.Image(icon_name='object-select-symbolic'))
        state.add(r)

        if s.reboot:
            names = ', '.join(sorted({e.name for e in s.reboot})[:6])
            r = row('Нужна перезагрузка', f'после загрузки обновились: {names}')
            r.add_prefix(Gtk.Image(icon_name='system-reboot-symbolic'))
            r.add_suffix(pill('перезагрузка', 'warn'))
        else:
            r = row('Перезагрузка не нужна', 'важные пакеты с последней загрузки не менялись')
            r.add_prefix(Gtk.Image(icon_name='object-select-symbolic'))
        state.add(r)

        if s.aur_updates or s.aur_error:
            r = row(f'AUR: обновлений {len(s.aur_updates)}' if not s.aur_error else 'AUR: проверка не удалась',
                    s.aur_error or ', '.join(u.name for u in s.aur_updates[:5]), activatable=True)
            r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
            r.connect('activated', lambda *_: self.open_page('aur'))
            state.add(r)
        flat_new = [a for a in s.flatpaks if a.new_version]
        if flat_new or s.flatpak_error:
            r = row(f'Flatpak: обновлений {len(flat_new)}' if not s.flatpak_error else 'Flatpak: проверка не удалась',
                    s.flatpak_error or ', '.join(a.name for a in flat_new[:5]), activatable=True)
            r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
            r.connect('activated', lambda *_: self.open_page('flatpak'))
            state.add(r)

        release, base = s.kernel
        state.add(row('Запущено ядро', f'{release}' + (f' (пакет {base})' if base else '')))

        touching = [n for n in s.news if n.touches]
        news_group = Adw.PreferencesGroup(title='Прочитать до обновления',
                                          description='Новости Arch, где упомянуты пакеты из ваших обновлений.')
        if touching:
            for item in touching[:5]:
                r = row(item.title, f'{_date(item.date)} · касается: {", ".join(item.touches)}', activatable=True)
                r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
                r.connect('activated', lambda *_: self.open_page('news'))
                news_group.add(r)
        else:
            news_group.add(row('Таких новостей нет', 'свежие новости Arch не упоминают пакеты из списка обновлений'))
        page.add(news_group)
        return page

    def open_page(self, key):
        for i, (k, _t, _i) in enumerate(PAGES):
            if k == key:
                self.sidebar_list.select_row(self.sidebar_list.get_row_at_index(i))

    # Новости

    def build_news(self):
        page = Adw.PreferencesPage()
        desc = None if self.snap.news_fresh else 'Сеть недоступна: показана последняя сохранённая лента.'
        group = Adw.PreferencesGroup(title='Новости Arch Linux', description=desc)
        page.add(group)
        if not self.snap.news:
            group.add(row('Новостей нет', 'лента не загрузилась и сохранённой копии нет'))
            return page
        for item in self.snap.news[:30]:
            exp = expander(item.title, _date(item.date))
            if item.touches:
                exp.add_suffix(pill('касается: ' + ', '.join(item.touches[:3]), 'warn'))
            body = Gtk.Label(label=item.text, wrap=True, xalign=0, selectable=True,
                             margin_top=12, margin_bottom=12, margin_start=12, margin_end=12)
            body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            exp.add_row(body)
            if item.link:
                link = Adw.ActionRow(title='Открыть на сайте', activatable=True)
                link.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic'))
                link.connect('activated', lambda _r, url=item.link: Gtk.UriLauncher(uri=url).launch(self, None, None))
                exp.add_row(link)
            group.add(exp)
        return page

    # Пакеты

    def build_packages(self):
        s = self.snap
        page = Adw.PreferencesPage()
        upd = Adw.PreferencesGroup(title='Обновления из репозиториев',
                                   description='Ставятся только все вместе: так Arch не попадает в частичное обновление.')
        if s.updates:
            button = Gtk.Button(label='Обновить всё', valign=Gtk.Align.CENTER)
            button.add_css_class('suggested-action')
            button.connect('clicked', lambda *_: self.confirm_upgrade())
            upd.set_header_suffix(button)
            for u in sorted(s.updates, key=lambda u: (not u.needs_reboot, u.name)):
                r = row(u.name, f'{u.old}  →  {u.new}', activatable=True)
                r.add_prefix(_pkg_icon(self.icon_for(u.name), 32))
                if u.needs_reboot:
                    r.add_suffix(pill('перезагрузка', 'warn'))
                r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
                r.connect('activated', lambda _r, n=u.name: self.show_pkg(n))
                upd.add(r)
        else:
            upd.add(row('Обновлений нет', s.updates_error or 'все пакеты свежие'))
        page.add(upd)

        self.rollback_group = Adw.PreferencesGroup(
            title='Установленные пакеты', description='Найдите пакет: описание, история версий и сборки, другая версия из кэша '
                                                      'или архива Arch, удаление.')
        search = Gtk.SearchEntry(placeholder_text='Найти пакет', valign=Gtk.Align.CENTER)
        search.set_text(self.rollback_query)
        search.connect('search-changed', self.on_rollback_search)
        self.rollback_group.set_header_suffix(search)
        self.rollback_rows = []
        self.fill_rollback()
        page.add(self.rollback_group)
        return page

    def on_rollback_search(self, entry):
        self.rollback_query = entry.get_text().strip().lower()
        self.fill_rollback()

    def fill_rollback(self):
        for r in self.rollback_rows:
            self.rollback_group.remove(r)
        self.rollback_rows = []
        q = self.rollback_query
        foreign = self.snap.foreign
        candidates = sorted((p for p in self.snap.packages.values() if p.name not in foreign and (p.cached if not q else q in p.name.lower())),
                            key=lambda p: (not p.name.lower().startswith(q), p.name))
        for pkg in candidates[:ROLLBACK_ROWS]:
            r = row(pkg.name, f'установлена {pkg.version} · в кэше версий: {len(pkg.cached)}', activatable=True)
            r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
            r.add_prefix(_pkg_icon(self.icon_for(pkg.name), 32))
            r.connect('activated', lambda _r, name=pkg.name: self.show_pkg(name))
            self.rollback_group.add(r)
            self.rollback_rows.append(r)
        if len(candidates) > ROLLBACK_ROWS:
            r = row(f'Ещё {len(candidates) - ROLLBACK_ROWS}', 'уточните поиск')
            self.rollback_group.add(r)
            self.rollback_rows.append(r)
        if not candidates:
            r = row('Ничего не найдено', 'без поиска показаны пакеты с прошлыми версиями в кэше; введите имя, чтобы найти любой')
            self.rollback_group.add(r)
            self.rollback_rows.append(r)

    def ask_rollback(self, name):
        s = self.snap
        pkg = s.packages[name]
        versions = []
        for c in pkg.cached:
            if c.version not in versions:
                versions.append(c.version)
        group = pacman.related(name, s.packages)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        choose = Adw.PreferencesGroup()
        labels = [v + ('  (новее установленной)' if pacman.vercmp(v, pkg.version) > 0 else '') for v in versions]
        versions_row = Adw.ComboRow(title='Версия', model=Gtk.StringList.new(labels))
        choose.add(versions_row)
        box.append(choose)
        together = Adw.PreferencesGroup(title='Откатятся вместе' if len(group) > 1 else '')
        box.append(together)

        dialog = Adw.AlertDialog(heading=f'Другая версия {name}', body=f'Сейчас установлена {pkg.version}.')
        dialog.set_extra_child(box)
        dialog.add_response('cancel', 'Отмена')
        dialog.add_response('rollback', 'Поставить')
        dialog.set_response_appearance('rollback', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')

        shown = []

        def update_group(*_):
            for r in shown:
                together.remove(r)
            shown.clear()
            version = versions[versions_row.get_selected()]
            files = pacman.rollback_files(group, version, s.packages, s.cache)
            missing = [n for n, f in files.items() if f is None]
            if len(group) > 1:
                for n in group:
                    r = row(n, f'{s.packages[n].version}  →  {version}' if files[n] else 'этой версии в кэше нет')
                    if not files[n]:
                        r.add_suffix(pill('нет в кэше', 'warn'))
                    together.add(r)
                    shown.append(r)
            dialog.set_response_enabled('rollback', not missing)
            dialog.set_body(f'Сейчас установлена {pkg.version}.' + (
                ' Не у всех связанных пакетов эта версия есть в кэше: откат по одному сломал бы систему.' if missing else ''))
            dialog.files = [f for f in files.values() if f]
            dialog.version = version

        versions_row.connect('notify::selected', update_group)
        update_group()
        dialog.connect('response', lambda d, resp: resp == 'rollback' and self.run_op(
            f'{name}: установка {d.version}', ops.install_files_command(d.files)))
        dialog.present(self)

    # AUR

    def build_aur(self):
        s = self.snap
        page = Adw.PreferencesPage()
        if not aur.available():
            page.add(_note_group('paru не установлен', 'вкладка AUR работает через paru'))
            return page
        upd = Adw.PreferencesGroup(title='Обновления AUR',
                                   description='Пакеты собираются на этом компьютере. Перед обновлением загляните в изменения PKGBUILD на сайте AUR: '
                                               'отсюда сборка идёт без просмотра.')
        if s.aur_updates:
            button = Gtk.Button(label='Обновить AUR', valign=Gtk.Align.CENTER)
            button.add_css_class('suggested-action')
            button.connect('clicked', lambda *_: self.confirm_aur())
            upd.set_header_suffix(button)
            for u in s.aur_updates:
                upd.add(self.aur_row(u.name, f'{u.old}  →  {u.new}'))
        else:
            upd.add(row('Обновлений нет', s.aur_error or 'все пакеты из AUR свежие'))
        page.add(upd)

        group = Adw.PreferencesGroup(title='Пакеты не из репозиториев',
                                     description='AUR и пакеты из отключённых репозиториев. Выберите пакет: описание, изменения PKGBUILD, обновление, '
                                                 'другая версия и удаление.')
        for name, version in sorted(s.foreign.items()):
            pkg = s.packages.get(name)
            older = pkg.cached if pkg else []
            group.add(self.aur_row(name, version + (f' · других версий: {len(older)}' if older else '')))
        page.add(group)
        return page

    def icon_for(self, name, files=None):
        """Значок программы из её .desktop, иначе служебный по виду пакета."""
        return (self.snap.icons.get(name) or aur.desktop_icon(files if files is not None else self.snap.foreign_files.get(name, []))
                or pacman.kind_icon(name))

    def aur_row(self, name, subtitle):
        r = row(name, subtitle, activatable=True)
        r.add_prefix(_pkg_icon(self.icon_for(name), 32))
        if any(u.name == name for u in self.snap.aur_updates):
            r.add_suffix(pill('есть обновление', 'info'))
        r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
        r.connect('activated', lambda _r: self.show_aur(name))
        return r

    def show_aur(self, name):
        s = self.snap
        files = s.foreign_files.get(name, [])
        local = aur.local_info(name)
        meta = appstream.from_file(aur.metainfo_file(files)) if aur.metainfo_file(files) else None
        update = next((u for u in s.aur_updates if u.name == name), None)
        pkg = s.packages.get(name)

        page = WidePage()
        dialog = self.detail_page(name, page)

        head = Gtk.Box(spacing=18, margin_bottom=6)
        head.append(_pkg_icon(self.icon_for(name, files), 96))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        title = Gtk.Label(label=(meta.name if meta and meta.name else name), xalign=0, wrap=True)
        title.add_css_class('title-1')
        text.append(title)
        summary = Gtk.Label(label=(meta.summary if meta and meta.summary else local.get('Description', '')), xalign=0, wrap=True)
        text.append(summary)
        byline = Gtk.Label(label=name, xalign=0, wrap=True)
        byline.add_css_class('dim-label')
        text.append(byline)
        head.append(text)
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        if update:
            b = Gtk.Button(label=f'Обновить до {update.new}')
            b.add_css_class('suggested-action')
            b.add_css_class('pill')
            b.connect('clicked', lambda *_: self.confirm_aur_one(dialog, update))
            buttons.append(b)
        if pkg and pkg.cached:
            b = Gtk.Button(label='Другая версия…')
            b.add_css_class('pill')
            b.connect('clicked', lambda *_: self.ask_rollback(name))
            buttons.append(b)
        b = Gtk.Button(label='Удалить')
        b.add_css_class('destructive-action')
        b.add_css_class('pill')
        b.connect('clicked', lambda *_: self.confirm_pkg_remove(dialog, name))
        buttons.append(b)
        head.append(buttons)
        top = Adw.PreferencesGroup()
        top.add(head)
        page.add(top)

        if meta and meta.screenshots:
            page.add(_screenshots(meta.screenshots))

        aur_group = Adw.PreferencesGroup(title='В AUR')
        aur_wait = row('Загружаем сведения AUR…')
        aur_group.add(aur_wait)
        page.add(aur_group)
        log_group = Adw.PreferencesGroup(title='Изменения PKGBUILD', description='Последние правки сборочного сценария в AUR, сверху новые.')
        log_wait = row('Загружаем историю…')
        log_group.add(log_wait)
        page.add(log_group)

        description = meta.description if meta and meta.description else ''
        if description:
            about = Adw.PreferencesGroup(title='Описание')
            label = Gtk.Label(label=description, wrap=True, xalign=0, selectable=True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            about.add(label)
            page.add(about)

        details = Adw.PreferencesGroup(title='Подробно')
        details.add(row('Установлена версия', local.get('Version', s.foreign.get(name, ''))))
        for key, title_ in (('Installed Size', 'Размер'), ('Install Date', 'Установлен'), ('Install Reason', 'Причина установки'),
                            ('Licenses', 'Лицензия'), ('Depends On', 'Зависимости')):
            if local.get(key) and local[key] != 'None':
                value = {'Explicitly installed': 'установлен вами', 'Installed as a dependency for another package': 'как зависимость'}.get(local[key], local[key])
                details.add(row(title_, value))
        if local.get('URL') and local['URL'] != 'None':
            site = row('Сайт программы', local['URL'], activatable=True)
            site.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic'))
            site.connect('activated', lambda *_: Gtk.UriLauncher(uri=local['URL']).launch(self, None, None))
            details.add(site)
        page.add(details)
        self.nav.push(dialog)

        def show_rpc(result):
            aur_group.remove(aur_wait)
            if isinstance(result, Exception):
                aur_group.add(row('Сведения AUR не загрузились', str(result)))
                return
            info = result.get(name)
            if not info:
                aur_group.add(row('Этого пакета нет в AUR', 'он из отключённого репозитория или собран вручную'))
                log_group.set_visible(False)
                return
            if info.get('OutOfDate'):
                r = row('Помечен как устаревший', 'сопровождающему сообщили, что вышла новая версия программы')
                r.add_suffix(pill('устарел', 'warn'))
                aur_group.add(r)
            aur_group.add(row('Версия в AUR', info.get('Version', '')))
            aur_group.add(row('Сопровождающий', info.get('Maintainer') or 'нет: пакет брошен, его может подхватить кто угодно'))
            aur_group.add(row('Голоса и популярность', f'{info.get("NumVotes", 0)} голосов · популярность {info.get("Popularity", 0):.2f}'))
            if info.get('LastModified'):
                from datetime import datetime
                aur_group.add(row('Последнее изменение', datetime.fromtimestamp(info['LastModified']).strftime('%d.%m.%Y')))
            page_url = f'https://aur.archlinux.org/packages/{info.get("Name", name)}'
            link = row('Страница в AUR', page_url, activatable=True)
            link.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic'))
            link.connect('activated', lambda *_: Gtk.UriLauncher(uri=page_url).launch(self, None, None))
            aur_group.add(link)
            base = info.get('PackageBase') or name
            background(lambda: aur.pkgbuild_log(base), lambda result: show_log(result, base))

        def show_log(result, base):
            log_group.remove(log_wait)
            if isinstance(result, Exception) or not result:
                log_group.add(row('История не загрузилась', str(result) if isinstance(result, Exception) else 'записей нет'))
                return
            log_group.set_description('Правки сборочного сценария в AUR, сверху новые. Выберите правку, чтобы собрать и поставить '
                                      'ту версию; кнопка справа открывает правку на сайте.')
            entries = result[:16]
            for i, (title_, date, note, href) in enumerate(entries[:15]):
                commit = aur.commit_of(href)
                prev = aur.commit_of(entries[i + 1][3]) if i + 1 < len(entries) else None
                r = row(title_, f'{date}' + (f' · {note}' if note else ''), activatable=bool(commit))
                if i == 0:
                    r.add_suffix(pill('новейшая', 'info'))
                if href:
                    site = Gtk.Button(icon_name='adw-external-link-symbolic', valign=Gtk.Align.CENTER, tooltip_text='Открыть правку на сайте AUR')
                    site.add_css_class('flat')
                    site.connect('clicked', lambda _b, u=href.replace('http://', 'https://'): Gtk.UriLauncher(uri=u).launch(self, None, None))
                    r.add_suffix(site)
                if commit:
                    r.connect('activated', lambda _r, c=commit, p=prev, t=title_, d=date: self.confirm_aur_build(name, base, c, p, t, d))
                log_group.add(r)

        background(lambda: aur.rpc_info([name]), show_rpc)

    def detail_page(self, title, page):
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        view.set_content(page)
        return Adw.NavigationPage(title=title, child=view)

    def close_detail(self, parent):
        # Окно сборок Flatpak это настоящий диалог: его закрываем, а страницу карточки снимаем со стека.
        if isinstance(parent, Adw.Dialog):
            parent.close()
        self.nav.pop_to_tag('root')

    def confirm_aur_build(self, name, base, commit, prev, title, date):
        """Страница версии из правки AUR: примечания к выпуску, изменения сборки и кнопка сборки."""
        files = self.snap.foreign_files.get(name, [])
        local_url = aur.local_info(name).get('URL', '')
        page = WidePage()
        nav_page = self.detail_page(f'{name}: правка от {date}', page)

        head = Gtk.Box(spacing=18, margin_bottom=6)
        head.append(_pkg_icon(self.icon_for(name, files), 72))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        heading = Gtk.Label(label=name, xalign=0, wrap=True)
        heading.add_css_class('title-1')
        text.append(heading)
        sub = Gtk.Label(label=f'Правка от {date}: «{title}»', xalign=0, wrap=True)
        sub.add_css_class('dim-label')
        text.append(sub)
        head.append(text)
        go = Gtk.Button(label='Собрать и поставить', valign=Gtk.Align.CENTER, sensitive=False)
        go.add_css_class('suggested-action')
        go.add_css_class('pill')
        installed = set(self.snap.packages)
        go.connect('clicked', lambda *_: (self.nav.pop_to_tag('root'), self.run_op(
            f'Сборка {name} ({date})', aur.build_commands(base, commit, installed))))
        head.append(go)
        top = Adw.PreferencesGroup()
        top.add(head)
        page.add(top)

        program = Adw.PreferencesGroup(title='Что изменилось в программе')
        changes = Gtk.Label(label='Ищем примечания к выпуску…', xalign=0, wrap=True, selectable=True,
                            margin_top=14, margin_bottom=14, margin_start=16, margin_end=16)
        changes.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        # Текст лежит в своей карточке: строки-ссылки группа ставит списком выше прочего, поэтому они в отдельной группе ниже.
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class('card')
        card.append(changes)
        program.add(card)
        page.add(program)
        links = Adw.PreferencesGroup(visible=False)
        page.add(links)
        build = Adw.PreferencesGroup(title='Что изменилось в сборке', description='Против прошлой правки PKGBUILD.')
        page.add(build)
        build.set_visible(False)
        about = Adw.PreferencesGroup(title='Перед сборкой')
        about.add(row('Как это пройдёт', 'PKGBUILD этой правки скачается из AUR, пакет соберётся на этом компьютере и встанет вместо '
                                         'нынешнего; установку подтвердите паролем. Ход сборки будет виден в окне.'))
        about.add(row('Если не соберётся', 'старые версии иногда не собираются: файлы программы могли пропасть с сайта автора. '
                                           'Тогда система не изменится.'))
        about.add(row('Потом', 'кнопка «Обновить AUR» снова поставит новейшую версию.'))
        page.add(about)
        self.nav.push(nav_page)

        def show(result):
            go.set_sensitive(True)
            if isinstance(result, Exception):
                changes.set_label(f'Примечания не загрузились: {result}')
                return
            heading.set_label(f'{name} {result.version}')
            if result.text:
                set_markup_or_text(changes, markdown_markup(result.text), result.text)
                program.set_description('Источник: ' + result.source)
                if result.link:
                    link = row('Открыть источник', result.link, activatable=True)
                    link.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic'))
                    link.connect('activated', lambda *_: Gtk.UriLauncher(uri=result.link).launch(self, None, None))
                    links.add(link)
                    links.set_visible(True)
            else:
                changes.set_label('Примечаний к этой версии не нашлось: ни у самой программы, ни на Flathub, ни в выпусках GitHub.')
                if local_url and local_url != 'None':
                    link = row('Сайт программы', local_url, activatable=True)
                    link.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic'))
                    link.connect('activated', lambda *_: Gtk.UriLauncher(uri=local_url).launch(self, None, None))
                    links.add(link)
                    links.set_visible(True)
            if result.build_changes:
                build.set_visible(True)
                for change in result.build_changes:
                    build.add(row(change))

        background(lambda: notes.collect(name, base, commit, prev, local_url, aur.metainfo_file(files)), show)

    def confirm_aur_one(self, parent, update):
        ask = Adw.AlertDialog(heading=f'Обновить {update.name}?',
                              body=f'{update.old} → {update.new}. Пакет соберётся на этом компьютере без просмотра PKGBUILD; '
                                   'установку подтвердите паролем.')
        ask.add_response('cancel', 'Отмена')
        ask.add_response('go', 'Собрать и установить')
        ask.set_response_appearance('go', Adw.ResponseAppearance.SUGGESTED)
        ask.set_default_response('cancel')
        ask.connect('response', lambda _d, resp: resp == 'go' and (self.close_detail(parent), self.run_op(
            f'Обновление {update.name}', aur.update_one_command(update.name))))
        ask.present(self)

    def confirm_pkg_remove(self, parent, name):
        group = Adw.PreferencesGroup()
        deps = Adw.SwitchRow(title='Убрать ненужные зависимости', subtitle='пакеты, поставленные только ради этого и больше никому не нужные',
                             active=True)
        group.add(deps)
        ask = Adw.AlertDialog(heading=f'Удалить {name}?', body='Пакет удалится из системы; настройки в домашней папке останутся.')
        ask.set_extra_child(group)
        ask.add_response('cancel', 'Отмена')
        ask.add_response('go', 'Удалить')
        ask.set_response_appearance('go', Adw.ResponseAppearance.DESTRUCTIVE)
        ask.set_default_response('cancel')
        ask.connect('response', lambda _d, resp: resp == 'go' and (self.close_detail(parent), self.run_op(
            f'Удаление {name}', aur.remove_command(name, deps.get_active()))))
        ask.present(self)

    def confirm_aur(self):
        names = ', '.join(u.name for u in self.snap.aur_updates[:6])
        dialog = Adw.AlertDialog(heading='Обновить пакеты AUR?',
                                 body=f'Будут собраны и установлены: {names}. Сборка идёт без просмотра PKGBUILD; '
                                      'установку подтвердите паролем, окно может появиться несколько раз.')
        dialog.add_response('cancel', 'Отмена')
        dialog.add_response('go', 'Собрать и установить')
        dialog.set_response_appearance('go', Adw.ResponseAppearance.SUGGESTED)
        dialog.connect('response', lambda _d, resp: resp == 'go' and self.run_op('Обновление AUR', aur.upgrade_command()))
        dialog.present(self)

    # Flatpak

    def build_flatpak(self):
        s = self.snap
        page = Adw.PreferencesPage()
        if not flatpak.available():
            page.add(_note_group('Flatpak не установлен', ''))
            return page
        new = [a for a in s.flatpaks if a.new_version]
        upd = Adw.PreferencesGroup(title='Обновления Flatpak')
        if new:
            button = Gtk.Button(label='Обновить всё', valign=Gtk.Align.CENTER)
            button.add_css_class('suggested-action')
            button.connect('clicked', lambda *_: self.run_op('Обновление Flatpak', flatpak.upgrade_command(), pacman_lock=False))
            upd.set_header_suffix(button)
            for a in new:
                upd.add(self.app_row(a, f'{a.version or "?"}  →  {a.new_version}'))
        else:
            upd.add(row('Обновлений нет', s.flatpak_error or 'все приложения свежие'))
        page.add(upd)

        group = Adw.PreferencesGroup(title='Приложения', description='Выберите приложение: описание, снимки, что нового, обновление и прошлые сборки.')
        for a in sorted(s.flatpaks, key=lambda a: self.app_name(a).lower()):
            group.add(self.app_row(a, f'{a.version or "без номера версии"} · {a.origin} · {a.installation}'))
        if not s.flatpaks:
            group.add(row('Приложений нет', s.flatpak_error or ''))
        page.add(group)
        return page

    def app_name(self, app):
        info = self.snap.app_info.get(app.app_id)
        return info.name if info and info.name else app.name

    def app_row(self, app, subtitle):
        info = self.snap.app_info.get(app.app_id)
        r = row(self.app_name(app), (info.summary + '\n' if info and info.summary else '') + subtitle, activatable=True)
        r.add_prefix(_icon(info, 40))
        if app.new_version:
            r.add_suffix(pill('есть обновление', 'info'))
        r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
        r.connect('activated', lambda _r: self.show_app(app))
        return r

    def show_app(self, app):
        info = self.snap.app_info.get(app.app_id) or appstream.Info(app.app_id, name=app.name)
        page = WidePage()
        dialog = self.detail_page(info.name or app.name, page)

        head = Gtk.Box(spacing=18, margin_bottom=6)
        head.append(_icon(info, 96))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        title = Gtk.Label(label=info.name or app.name, xalign=0, wrap=True)
        title.add_css_class('title-1')
        text.append(title)
        if info.summary:
            text.append(Gtk.Label(label=info.summary, xalign=0, wrap=True))
        if info.developer:
            dev = Gtk.Label(label=info.developer, xalign=0)
            dev.add_css_class('dim-label')
            text.append(dev)
        head.append(text)
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        if app.new_version:
            update = Gtk.Button(label=f'Обновить до {app.new_version}')
            update.add_css_class('suggested-action')
            update.add_css_class('pill')
            update.connect('clicked', lambda *_: self.confirm_app_update(dialog, app))
            buttons.append(update)
        builds = Gtk.Button(label='Другая сборка…')
        builds.add_css_class('pill')
        builds.connect('clicked', lambda *_: self.ask_flatpak(app))
        buttons.append(builds)
        remove = Gtk.Button(label='Удалить')
        remove.add_css_class('destructive-action')
        remove.add_css_class('pill')
        remove.connect('clicked', lambda *_: self.confirm_app_remove(dialog, app))
        buttons.append(remove)
        head.append(buttons)
        top = Adw.PreferencesGroup()
        top.add(head)
        page.add(top)

        if info.screenshots:
            page.add(_screenshots(info.screenshots))

        newer = [r for r in info.releases if app.version and flatpak_newer(r.version, app.version)]
        notes = newer or info.releases[:1]
        if notes:
            whats = Adw.PreferencesGroup(title='Что нового' if newer else f'Версия {notes[0].version}')
            for rel in notes[:5]:
                exp = expander(f'Версия {rel.version}', _release_date(rel.date), expanded=rel is notes[0])
                body = Gtk.Label(label=rel.text or 'Описания изменений нет.', wrap=True, xalign=0, selectable=True,
                                 margin_top=10, margin_bottom=10, margin_start=12, margin_end=12)
                body.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
                exp.add_row(body)
                whats.add(exp)
            page.add(whats)

        if info.description:
            about = Adw.PreferencesGroup(title='Описание')
            label = Gtk.Label(label=info.description, wrap=True, xalign=0, selectable=True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            about.add(label)
            page.add(about)

        details = Adw.PreferencesGroup(title='Подробно')
        details.add(row('Установлена версия', app.version or 'без номера версии'))
        details.add(row('Хранилище', f'{app.origin} · установка {app.installation} · ветка {app.branch}'))
        if info.license:
            details.add(row('Лицензия', info.license))
        details.add(row('Идентификатор', app.app_id))
        if info.homepage:
            site = row('Сайт', info.homepage, activatable=True)
            site.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic'))
            site.connect('activated', lambda *_: Gtk.UriLauncher(uri=info.homepage).launch(self, None, None))
            details.add(site)
        page.add(details)
        self.nav.push(dialog)

    def confirm_app_update(self, parent, app):
        ask = Adw.AlertDialog(heading=f'Обновить {self.app_name(app)}?',
                              body=f'{app.version or "?"} → {app.new_version}. Нужные приложению среды обновятся вместе с ним.')
        ask.add_response('cancel', 'Отмена')
        ask.add_response('go', 'Обновить')
        ask.set_response_appearance('go', Adw.ResponseAppearance.SUGGESTED)
        ask.set_default_response('cancel')
        ask.connect('response', lambda _d, resp: resp == 'go' and (self.close_detail(parent), self.run_op(
            f'Обновление {self.app_name(app)}', flatpak.update_one_command(app), pacman_lock=False)))
        ask.present(self)

    def confirm_app_remove(self, parent, app):
        name = self.app_name(app)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        group = Adw.PreferencesGroup()
        data = Adw.SwitchRow(title='Удалить и данные приложения', subtitle=f'настройки и файлы в ~/.var/app/{app.app_id}')
        unused = Adw.SwitchRow(title='Убрать ненужные среды', subtitle='среды выполнения, которыми больше не пользуется ни одно приложение',
                               active=True)
        group.add(data)
        group.add(unused)
        box.append(group)
        ask = Adw.AlertDialog(heading=f'Удалить {name}?', body='Приложение пропадёт из системы. Без данных его можно поставить заново как было.')
        ask.set_extra_child(box)
        ask.add_response('cancel', 'Отмена')
        ask.add_response('go', 'Удалить')
        ask.set_response_appearance('go', Adw.ResponseAppearance.DESTRUCTIVE)
        ask.set_default_response('cancel')
        ask.connect('response', lambda _d, resp: resp == 'go' and (self.close_detail(parent), self.run_op(
            f'Удаление {name}', flatpak.uninstall_commands(app, data.get_active(), unused.get_active()), pacman_lock=False)))
        ask.present(self)

    def ask_flatpak(self, app):
        dialog = Adw.Dialog(title=app.name, content_width=560, content_height=520)
        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        holder = Adw.Bin()
        holder.set_child(Adw.StatusPage(title='Загружаем журнал сборок…', child=Adw.Spinner()))
        view.set_content(holder)
        dialog.set_child(view)
        dialog.present(self)

        def show(result):
            if isinstance(result, Exception):
                holder.set_child(Adw.StatusPage(title='Журнал не загрузился', description=str(result)))
                return
            commits, current = result
            page = Adw.PreferencesPage()
            group = Adw.PreferencesGroup(title='Сборки в хранилище', description='Сверху самая новая.')
            for c in commits:
                r = row(c.subject or c.commit[:12], f'{c.date} · {c.commit[:12]}', activatable=c.commit != current)
                if c.commit == current:
                    r.add_suffix(pill('установлена', 'ok'))
                else:
                    r.connect('activated', lambda _r, commit=c: self.confirm_flatpak(dialog, app, commit))
                group.add(r)
            page.add(group)
            holder.set_child(page)

        background(lambda: (flatpak.history(app), flatpak.current_commit(app)), show)

    def confirm_flatpak(self, parent, app, commit):
        ask = Adw.AlertDialog(heading=f'Поставить эту сборку {app.name}?',
                              body=f'{commit.subject or commit.commit[:12]}\n{commit.date}\n\nМеняется только приложение, среды остаются как есть.')
        ask.add_response('cancel', 'Отмена')
        ask.add_response('go', 'Поставить')
        ask.set_response_appearance('go', Adw.ResponseAppearance.SUGGESTED)
        ask.set_default_response('cancel')
        ask.connect('response', lambda _d, resp: resp == 'go' and (self.close_detail(parent), self.run_op(
            f'{app.name}: сборка {commit.commit[:12]}', flatpak.checkout_command(app, commit.commit), pacman_lock=False)))
        ask.present(self)

    # Ядра

    def build_kernels(self):
        s = self.snap
        page = Adw.PreferencesPage()
        release, running_base = s.kernel
        found = pacman.kernels(s.packages)
        names = [k.name for k in found]
        default = kernels.default_kernel(s.boot_order, names)
        repo = {k.name: k for k in s.repo_kernels}

        group = Adw.PreferencesGroup(title='Установленные ядра',
                                     description='Основное загружается первым; остальные выбираются в меню загрузчика. '
                                                 'Держите хотя бы одно запасное.')
        page.add(group)
        if not found:
            group.add(row('Ядра не найдены', 'не удалось сопоставить пакеты ядер с /usr/lib/modules'))
        for k in found:
            exp = expander(k.name, f'установлено {k.version}' + (f' · запущено {release}' if k.name == running_base else ''))
            exp.add_prefix(_pkg_icon('computer-symbolic', 32))
            if k.name == default:
                exp.add_suffix(pill('основное', 'info'))
            if k.name == running_base:
                exp.add_suffix(pill('запущено', 'ok'))
            pending = [u for u in s.updates if u.name == k.name]
            if pending:
                exp.add_suffix(pill(f'есть {pending[0].new}', 'info'))
            missing = [m for m in (repo[k.name].modules if k.name in repo else []) if m not in s.packages]
            if missing:
                exp.add_suffix(pill('нет модулей', 'warn'))
                r = row('Поставить недостающие модули', f'{", ".join(missing)}: стоят у другого ядра, а у этого нет; без r8125 может не быть сети',
                        activatable=True)
                r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
                r.connect('activated', lambda _r, ms=missing, n=k.name: self.ask_install(n, ms, modules_only=True))
                exp.add_row(r)
            if k.name != default:
                r = row('Сделать основным', 'это ядро будет загружаться первым; меню загрузчика пересоберётся', activatable=True)
                r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
                r.connect('activated', lambda _r, n=k.name: self.ask_default(n))
                exp.add_row(r)
            for c in _unique_versions(k.cached):
                newer = pacman.vercmp(c, k.version) > 0
                r = row(f'{"Поставить" if newer else "Откатить на"} {c}',
                        ('новее установленной; ' if newer else '') + 'вместе с заголовками и модулями этого ядра', activatable=True)
                r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
                r.connect('activated', lambda _r, name=k.name: self.ask_rollback(name))
                exp.add_row(r)
            if not k.cached:
                exp.add_row(row('Прошлых версий в кэше нет'))
            group.add(exp)
        if default is None and found:
            group.add(row('Основное ядро не задано явно',
                          f'BOOT_ORDER: {", ".join(s.boot_order) or "по умолчанию"}; первым идёт ядро, которое limine поставит первым по шаблону'))

        more = Adw.PreferencesGroup(title='Доступные ядра',
                                    description='Из подключённых репозиториев. Ставятся вместе с заголовками, модулями ваших устройств и полным обновлением системы.')
        page.add(more)
        for k in s.repo_kernels:
            if k.installed:
                continue
            r = row(k.name, f'{k.version} · {k.repo}' + (f' · {k.description}' if k.description else ''), activatable=True)
            r.add_prefix(_pkg_icon('computer-symbolic', 32))
            r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
            r.connect('activated', lambda _r, kk=k: self.ask_install(kk.name, [kk.name, kk.name + '-headers', *kk.modules]))
            more.add(r)
        if not any(not k.installed for k in s.repo_kernels):
            more.add(row('Других ядер в репозиториях нет'))
        return page

    def ask_install(self, kernel, names, modules_only=False):
        heading = f'Поставить модули для {kernel}?' if modules_only else f'Поставить ядро {kernel}?'
        body = (f'Будут установлены: {", ".join(names)}.\n\nВместе с ними обновится вся система: '
                f'так Arch требует при установке новых пакетов. Пункт в меню загрузчика появится сам.')
        dialog = Adw.AlertDialog(heading=heading, body=body)
        dialog.add_response('cancel', 'Отмена')
        dialog.add_response('go', 'Установить')
        dialog.set_response_appearance('go', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.connect('response', lambda _d, resp: resp == 'go' and self.run_op(f'Установка {kernel}', kernels.install_command(names)))
        dialog.present(self)

    def ask_default(self, kernel):
        order = kernels.new_order(self.snap.boot_order, kernel)
        dialog = Adw.AlertDialog(heading=f'Сделать {kernel} основным?',
                                 body=f'В /etc/default/limine станет BOOT_ORDER="{", ".join(order)}", прежние настройки сохранятся копией. '
                                      'Потом limine-update пересоберёт меню загрузки, это займёт около минуты.')
        dialog.add_response('cancel', 'Отмена')
        dialog.add_response('go', 'Сделать основным')
        dialog.set_response_appearance('go', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('cancel')
        dialog.connect('response', lambda _d, resp: resp == 'go' and self.run_op(f'Основное ядро: {kernel}', kernels.set_default_command(kernel)))
        dialog.present(self)

    # История

    def build_history(self):
        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup(title='Что менялось', description=f'Журнал pacman за последние {HISTORY_DAYS} дней изменений.')
        page.add(group)
        days = defaultdict(list)
        for e in self.snap.log:
            days[e.when.date()].append(e)
        verbs = {'upgraded': 'обновлён', 'downgraded': 'откачен', 'installed': 'установлен', 'removed': 'удалён', 'reinstalled': 'переустановлен'}
        for day in sorted(days, reverse=True)[:HISTORY_DAYS]:
            entries = days[day]
            counts = defaultdict(int)
            for e in entries:
                counts[e.action] += 1
            summary = ', '.join(f'{verbs[a]}: {n}' for a, n in counts.items())
            exp = expander(day.strftime('%d.%m.%Y'), summary)
            for e in entries[:200]:
                exp.add_row(row(e.name, f'{e.when.strftime("%H:%M")} · {verbs[e.action]} · {e.versions}'))
            if len(entries) > 200:
                exp.add_row(row(f'и ещё {len(entries) - 200}'))
            group.add(exp)
        return page

    # Действия

    # Карточка пакета из репозитория

    def show_pkg(self, name):
        s = self.snap
        pkg = s.packages.get(name)
        if not pkg:
            self.toast(f'{name} не установлен')
            return
        files = pacman_files(name)
        local = aur.local_info(name)
        meta = appstream.from_file(aur.metainfo_file(files)) if aur.metainfo_file(files) else None
        update = next((u for u in s.updates if u.name == name), None)

        page = WidePage()
        nav_page = self.detail_page(name, page)
        head = Gtk.Box(spacing=18, margin_bottom=6)
        head.append(_pkg_icon(self.icon_for(name, files), 96))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        title = Gtk.Label(label=(meta.name if meta and meta.name else name), xalign=0, wrap=True)
        title.add_css_class('title-1')
        text.append(title)
        text.append(Gtk.Label(label=(meta.summary if meta and meta.summary else local.get('Description', '')), xalign=0, wrap=True))
        byline = Gtk.Label(label=f'{name} {pkg.version}', xalign=0, wrap=True)
        byline.add_css_class('dim-label')
        text.append(byline)
        head.append(text)
        buttons = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, valign=Gtk.Align.CENTER)
        if update:
            b = Gtk.Button(label=f'Обновить до {update.new}')
            b.add_css_class('suggested-action')
            b.add_css_class('pill')
            b.set_tooltip_text('Только этот пакет и его сборка, если это не потянет обновление общих пакетов')
            b.connect('clicked', lambda *_: self.ask_single_update(name))
            buttons.append(b)
            b = Gtk.Button(label='Обновить всю систему')
            b.add_css_class('pill')
            b.connect('clicked', lambda *_: self.confirm_upgrade())
            buttons.append(b)
        b = Gtk.Button(label='Удалить')
        b.add_css_class('destructive-action')
        b.add_css_class('pill')
        b.connect('clicked', lambda *_: self.confirm_repo_remove(name))
        buttons.append(b)
        head.append(buttons)
        top = Adw.PreferencesGroup()
        top.add(head)
        page.add(top)
        if meta and meta.screenshots:
            page.add(_screenshots(meta.screenshots))

        repo = pacman.repo_info(name)
        repo_group = Adw.PreferencesGroup(title='В репозитории')
        if repo:
            # Локальные списки пакетов могут отставать; свежую версию знает проверка обновлений.
            repo_group.add(row('Версия', f'{update.new if update else repo.get("Version", "")} · {repo.get("Repository", "")}'))
            for key, title_ in (('Packager', 'Собрал'), ('Build Date', 'Дата сборки'), ('Download Size', 'Размер загрузки')):
                if repo.get(key):
                    repo_group.add(row(title_, repo[key]))
        else:
            repo_group.add(row('Нет в подключённых репозиториях', 'пакет поставлен вручную или его репозиторий отключён'))
        page.add(repo_group)

        versions_group = Adw.PreferencesGroup(title='Версии', description='Выберите версию: что в ней изменилось и поставить её. '
                                                                          'Сверху новые; прошлые берутся из кэша или архива Arch.')
        wait = row('Загружаем архив версий…')
        versions_group.add(wait)
        page.add(versions_group)
        aur_group = Adw.PreferencesGroup(title='История в AUR', visible=False,
                                         description='Репозиторий собрал этот пакет из AUR. Выберите правку: что изменилось в той версии '
                                                     'и собрать её; собранная встанет вместо пакета из репозитория.')
        page.add(aur_group)
        history_group = Adw.PreferencesGroup(title='История сборки в Arch', description='Правки сборочного сценария пакета на GitLab Arch.')
        history_wait = row('Загружаем историю…')
        history_group.add(history_wait)
        page.add(history_group)

        description = meta.description if meta and meta.description else ''
        if description:
            about = Adw.PreferencesGroup(title='Описание')
            label = Gtk.Label(label=description, wrap=True, xalign=0, selectable=True)
            label.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
            about.add(label)
            page.add(about)
        details = Adw.PreferencesGroup(title='Подробно')
        for key, title_ in (('Installed Size', 'Размер'), ('Install Date', 'Установлен'), ('Install Reason', 'Причина установки'),
                            ('Licenses', 'Лицензия'), ('Depends On', 'Зависимости'), ('Required By', 'Нужен пакетам')):
            if local.get(key) and local[key] != 'None':
                value = {'Explicitly installed': 'установлен вами', 'Installed as a dependency for another package': 'как зависимость'}.get(local[key], local[key])
                details.add(row(title_, value))
        if local.get('URL') and local['URL'] != 'None':
            site = row('Сайт программы', local['URL'], activatable=True)
            site.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic'))
            site.connect('activated', lambda *_: Gtk.UriLauncher(uri=local['URL']).launch(self, None, None))
            details.add(site)
        page.add(details)
        self.nav.push(nav_page)

        def show_versions(archived):
            versions_group.remove(wait)
            archived = [] if isinstance(archived, Exception) else archived
            merged = {}
            for c in pkg.cached:
                merged.setdefault(c.version, {'cache': c.path})
            for a in archived[:25]:
                merged.setdefault(a.version, {})['archive'] = a.url
            merged.setdefault(pkg.version, {})['installed'] = True
            if update:
                merged.setdefault(update.new, {})['repo'] = True
            order = sorted(merged, key=lambda v: _VerKey(v), reverse=True)[:30]
            for v in order:
                where = merged[v]
                tags = []
                if where.get('installed'):
                    tags.append('установлена')
                if where.get('repo'):
                    tags.append('в репозитории')
                if where.get('cache'):
                    tags.append('в кэше')
                if where.get('archive'):
                    tags.append('архив Arch')
                only_repo = not (where.get('cache') or where.get('archive') or where.get('installed'))
                if only_repo:
                    tags.append('ставится обновлением системы')
                r = row(f'Версия {v}', ' · '.join(tags), activatable=not where.get('installed'))
                if where.get('installed'):
                    r.add_suffix(pill('установлена', 'ok'))
                elif where.get('repo'):
                    r.add_suffix(pill('новейшая', 'info'))
                if not where.get('installed'):
                    r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
                    r.connect('activated', lambda _r, ver=v, upd=only_repo: self.show_pkg_version(name, ver, local.get('URL', ''), files, from_repo=upd))
                versions_group.add(r)
            if not archived:
                versions_group.set_description(versions_group.get_description() + ' В архиве Arch этого пакета нет: '
                                               'это сборка CachyOS или пакет не из Arch, доступны только версии из кэша.')

        def show_history(result):
            history_group.remove(history_wait)
            if isinstance(result, Exception) or not result:
                history_group.set_visible(False)
                return
            for date, title_, href in result:
                r = row(title_, date, activatable=bool(href))
                if href:
                    r.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic'))
                    r.connect('activated', lambda _r, u=href: Gtk.UriLauncher(uri=u).launch(self, None, None))
                history_group.add(r)

        def show_aur(result):
            if isinstance(result, Exception) or not result:
                return
            base, log = result
            entries = log[:16]
            for i, (title_, date, note_, href) in enumerate(entries[:15]):
                commit = aur.commit_of(href)
                prev = aur.commit_of(entries[i + 1][3]) if i + 1 < len(entries) else None
                r = row(title_, date + (f' · {note_}' if note_ else ''), activatable=bool(commit))
                if commit:
                    r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
                    r.connect('activated', lambda _r, c=commit, p=prev, t=title_, d=date: self.confirm_aur_build(name, base, c, p, t, d))
                aur_group.add(r)
            aur_group.set_visible(True)

        def aur_history():
            # Только для пакетов без архива Arch: сборки CachyOS из AUR (claude-desktop и подобные).
            if archive.versions(name):
                return None
            info = aur.rpc_info([name]).get(name)
            if not info:
                return None
            base = info.get('PackageBase') or name
            return base, aur.pkgbuild_log(base)

        background(lambda: archive.versions(name), show_versions)
        background(lambda: archive.packaging_log(pkg.base), show_history)
        background(aur_history, show_aur)

    def show_pkg_version(self, name, version, url, files, from_repo=False):
        """Страница версии пакета из репозитория: примечания к выпуску и что поставится вместе с ним."""
        s = self.snap
        pkg = s.packages[name]
        group_names = pacman.related(name, s.packages)
        page = WidePage()
        nav_page = self.detail_page(f'{name} {version}', page)
        head = Gtk.Box(spacing=18, margin_bottom=6)
        head.append(_pkg_icon(self.icon_for(name, files), 72))
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        heading = Gtk.Label(label=f'{name} {version}', xalign=0, wrap=True)
        heading.add_css_class('title-1')
        text.append(heading)
        newer = _VerKey(pkg.version) < _VerKey(version)
        sub = Gtk.Label(label=f'Сейчас установлена {pkg.version}; эта версия ' + ('новее' if newer else 'старше'), xalign=0, wrap=True)
        sub.add_css_class('dim-label')
        text.append(sub)
        head.append(text)
        go = Gtk.Button(label='Обновить до этой' if from_repo else 'Поставить эту версию', valign=Gtk.Align.CENTER, sensitive=from_repo)
        if from_repo:
            go.connect('clicked', lambda *_: self.ask_single_update(name))
        go.add_css_class('suggested-action')
        go.add_css_class('pill')
        head.append(go)
        top = Adw.PreferencesGroup()
        top.add(head)
        page.add(top)

        program = Adw.PreferencesGroup(title='Что изменилось в программе')
        changes = Gtk.Label(label='Ищем примечания к выпуску…', xalign=0, wrap=True, selectable=True,
                            margin_top=14, margin_bottom=14, margin_start=16, margin_end=16)
        changes.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        card.add_css_class('card')
        card.append(changes)
        program.add(card)
        page.add(program)
        links = Adw.PreferencesGroup(visible=False)
        page.add(links)
        together = Adw.PreferencesGroup(title='Что поставится', description='Пакеты одной сборки ставятся вместе одной версии, иначе система сломается.')
        together_wait = row('Ищем файлы в кэше и архиве Arch…')
        together.add(together_wait)
        page.add(together)
        warn = Adw.PreferencesGroup()
        warn.add(row('Обновление вернёт новейшую', 'следующее обновление системы снова поставит версию из репозитория'))
        page.add(warn)
        self.nav.push(nav_page)

        def show_notes(result):
            if isinstance(result, Exception) or not result.text:
                changes.set_label('Примечаний к этой версии не нашлось: ни у самой программы, ни в выпусках GitHub, ни на Flathub.')
                site = url if url and url != 'None' else ''
                if site:
                    link = row('Сайт программы', site, activatable=True)
                    link.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic'))
                    link.connect('activated', lambda *_: Gtk.UriLauncher(uri=site).launch(self, None, None))
                    links.add(link)
                    links.set_visible(True)
                return
            set_markup_or_text(changes, markdown_markup(result.text), result.text)
            program.set_description('Источник: ' + result.source)
            if result.link:
                link = row('Открыть источник', result.link, activatable=True)
                link.add_suffix(Gtk.Image(icon_name='adw-external-link-symbolic'))
                link.connect('activated', lambda *_: Gtk.UriLauncher(uri=result.link).launch(self, None, None))
                links.add(link)
                links.set_visible(True)

        def find_sources():
            sources = {}
            for n in group_names:
                cached = next((c.path for c in s.cache.get(n, []) if c.version == version), None)
                if cached:
                    sources[n] = (cached, 'кэш')
                    continue
                arch = next((a.url for a in archive.versions(n) if a.version == version), None)
                sources[n] = (arch, 'архив Arch') if arch else (None, '')
            return sources

        def show_sources(sources):
            together.remove(together_wait)
            if isinstance(sources, Exception):
                together.add(row('Не удалось найти файлы', str(sources)))
                return
            missing = [n for n, (src, _) in sources.items() if not src]
            for n, (src, where) in sources.items():
                r = row(n, f'{s.packages[n].version}  →  {version} · {where}' if src else 'этой версии нет ни в кэше, ни в архиве Arch')
                if not src:
                    r.add_suffix(pill('нет', 'warn'))
                together.add(r)
            if missing:
                together.set_description('Не у всех пакетов этой сборки есть такая версия, поэтому поставить её нельзя: '
                                         'пакеты разных версий сломали бы систему.')
                return
            go.set_sensitive(True)
            paths = [src for src, _ in sources.values()]
            go.connect('clicked', lambda *_: (self.nav.pop_to_tag('root'), self.run_op(
                f'{name}: установка {version}', ops.install_files_command(paths))))

        background(lambda: notes.for_version(name, version, url, aur.metainfo_file(files)), show_notes)
        if from_repo:
            together.set_description('Эта версия есть в репозитории: обновление поставит её вместе с пакетами её сборки, '
                                     'если это не потянет обновление общих пакетов.')
            together.remove(together_wait)
            warn.set_visible(False)
        else:
            background(find_sources, show_sources)

    def ask_single_update(self, name):
        s = self.snap
        group = [n for n in pacman.related(name, s.packages) if any(u.name == n for u in s.updates)] or [name]

        def show(result):
            if isinstance(result, Exception):
                self.toast(f'Не удалось рассчитать обновление: {result}')
                return
            targets, others, new = result
            if not targets:
                self.toast('Новой версии в свежих списках нет: нажмите «Проверить заново»')
                return
            if others:
                ask = Adw.AlertDialog(heading=f'{name} отдельно не обновить',
                                      body='Новая версия потянет обновление общих пакетов, которыми пользуются и другие программы: '
                                           + ', '.join(f'{t.name} {t.version}' for t in others[:8])
                                           + '. Обновлённые отдельно, они могут сломать программы, собранные под старые. Обновите всю систему.')
                ask.add_response('cancel', 'Отмена')
                ask.add_response('all', 'Обновить всю систему')
                ask.set_response_appearance('all', Adw.ResponseAppearance.SUGGESTED)
                ask.connect('response', lambda _d, resp: resp == 'all' and self.confirm_upgrade())
                ask.present(self)
                return
            lines = [f'{t.name}  {s.packages[t.name].version} → {t.version}' for t in targets if t.name in s.packages]
            lines += [f'{t.name} {t.version} (новая зависимость)' for t in new]
            required = aur.local_info(name).get('Required By', 'None')
            body = ('Поставится только это:\n' + '\n'.join(lines) + '\n\nОбщие пакеты не меняются, поэтому другие программы это не заденет.'
                    + ('' if required in ('', 'None') else f' Пакет нужен и другим ({required.replace(chr(10), " ")[:120]}), '
                       'но они получат ту же совместимую версию.'))
            ask = Adw.AlertDialog(heading=f'Обновить только {name}?', body=body)
            ask.add_response('cancel', 'Отмена')
            ask.add_response('go', 'Обновить')
            ask.set_response_appearance('go', Adw.ResponseAppearance.SUGGESTED)
            ask.set_default_response('cancel')
            urls = [t.url for t in targets]
            ask.connect('response', lambda _d, resp: resp == 'go' and (self.nav.pop_to_tag('root'), self.run_op(
                f'Обновление {name}', pacman.install_urls_command(urls))))
            ask.present(self)

        background(lambda: pacman.single_update_plan(group, s.packages), show)

    def confirm_repo_remove(self, name):
        group = Adw.PreferencesGroup()
        deps = Adw.SwitchRow(title='Убрать ненужные зависимости', subtitle='пакеты, поставленные только ради этого и больше никому не нужные',
                             active=True)
        group.add(deps)
        ask = Adw.AlertDialog(heading=f'Удалить {name}?', body='Пакет удалится из системы; настройки в домашней папке останутся. '
                                                               'Если он нужен другим пакетам, pacman откажется и объяснит почему.')
        ask.set_extra_child(group)
        ask.add_response('cancel', 'Отмена')
        ask.add_response('go', 'Удалить')
        ask.set_response_appearance('go', Adw.ResponseAppearance.DESTRUCTIVE)
        ask.set_default_response('cancel')
        ask.connect('response', lambda _d, resp: resp == 'go' and (self.nav.pop_to_tag('root'), self.run_op(
            f'Удаление {name}', pacman.remove_command(name, deps.get_active()))))
        ask.present(self)

    def confirm_upgrade(self):
        s = self.snap
        reboot = [u.name for u in s.updates if u.needs_reboot]
        touching = [n for n in s.news if n.touches]
        body = f'Будет обновлено пакетов: {len(s.updates)}.'
        if reboot:
            body += f' После этого понадобится перезагрузка ({", ".join(reboot[:5])}).'
        if touching:
            body += f' Сначала прочитайте новости: «{touching[0].title}».'
        dialog = Adw.AlertDialog(heading='Обновить систему?', body=body)
        dialog.add_response('cancel', 'Отмена')
        dialog.add_response('go', 'Обновить')
        dialog.set_response_appearance('go', Adw.ResponseAppearance.SUGGESTED)
        dialog.connect('response', lambda _d, resp: resp == 'go' and self.run_op('Обновление системы', ops.upgrade_command()))
        dialog.present(self)

    def run_op(self, title, cmd, pacman_lock=True):
        if pacman_lock and ops.busy():
            self.toast('pacman сейчас занят другой программой, попробуйте позже')
            return
        OperationDialog(title, cmd, lambda code: code == 0 and self.refresh()).present(self)


class _VerKey:
    """Порядок по самой версии программы: у сборок CachyOS и Arch разные эпохи (3:26.2.5 и 1:26.2.6), а программа одна."""
    def __init__(self, version):
        self.version = version

    def __lt__(self, other):
        c = pacman.vercmp(archive.plain(self.version), archive.plain(other.version))
        return c < 0 if c else pacman.vercmp(self.version, other.version) < 0


def pacman_files(name):
    return aur.files_of([name]).get(name, [])


class WidePage(Gtk.ScrolledWindow):
    """Страница карточки: прокрутка на всю ширину окна, а содержимое по центру шириной до 980 пикселей.
    Adw.PreferencesPage держит колонку узкой, а обёрнутая в Adw.Clamp целиком уносила с собой полосу прокрутки и тень."""
    def __init__(self):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=24, margin_top=24, margin_bottom=24,
                           margin_start=12, margin_end=12)
        self.set_child(Adw.Clamp(maximum_size=980, tightening_threshold=720, child=self.box))

    def add(self, group):
        self.box.append(group)


def set_markup_or_text(label, markup, text):
    """Разметка, а если Pango её не принял, тот же текст без оформления: лучше просто, чем пусто."""
    try:
        Pango.parse_markup(markup, -1, '\0')
    except GLib.Error:
        label.set_label(text)
        return
    label.set_markup(markup)


def markdown_markup(text):
    """Примечания с GitHub пишут в Markdown: заголовки жирным, пункты точками, остальное как есть, всё экранировано."""
    import re
    lines = []
    for line in text.replace('\r', '').splitlines():
        heading = re.match(r'^#{1,6}\s+(.*)', line)
        item = re.match(r'^\s*[-*+]\s+(.*)', line)
        if heading:
            body = '<b>' + GLib.markup_escape_text(heading.group(1)) + '</b>'
        elif item:
            body = '• ' + GLib.markup_escape_text(item.group(1))
        else:
            body = GLib.markup_escape_text(line)
        body = re.sub(r'\*\*\*(.+?)\*\*\*', r'<b><i>\1</i></b>', body)
        body = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', body)
        body = re.sub(r'`([^`]+)`', r'<tt>\1</tt>', body)
        body = re.sub(r'(?<![*\w])\*(?!\s)([^*]+?)(?<!\s)\*(?![*\w])', r'<i>\1</i>', body)
        lines.append(body)
    return re.sub(r'\n{3,}', '\n\n', '\n'.join(lines)).strip()


def _screenshots(shots):
    group = Adw.PreferencesGroup()
    carousel = Adw.Carousel(spacing=12, allow_scroll_wheel=False)
    carousel.set_size_request(-1, 440)
    for caption, url in shots[:8]:
        frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        picture = Gtk.Picture(can_shrink=True, content_fit=Gtk.ContentFit.CONTAIN, vexpand=True)
        frame.append(picture)
        if caption:
            cap = Gtk.Label(label=caption, wrap=True)
            cap.add_css_class('dim-label')
            frame.append(cap)
        carousel.append(frame)
        background(lambda u=url: appstream.screenshot(u),
                   lambda data, p=picture: not isinstance(data, Exception) and p.set_paintable(
                       Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))))
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    box.append(carousel)
    box.append(Adw.CarouselIndicatorDots(carousel=carousel))
    group.add(box)
    return group


def _pkg_icon(icon, size):
    """Значок из .desktop: путь к файлу или имя из темы; без него общий значок пакета."""
    if icon and icon.startswith('/') and os.path.exists(icon):
        image = Gtk.Image.new_from_file(icon)
    else:
        theme = Gtk.IconTheme.get_for_display(Gdk.Display.get_default())
        image = Gtk.Image(icon_name=icon if icon and theme.has_icon(icon) else 'package-x-generic-symbolic')
    image.set_pixel_size(size)
    image.set_valign(Gtk.Align.CENTER)
    return image


def _icon(info, size):
    if info and info.icon:
        image = Gtk.Image.new_from_file(info.icon)
    else:
        image = Gtk.Image(icon_name='application-x-executable-symbolic')
    image.set_pixel_size(size)
    image.set_valign(Gtk.Align.CENTER)
    return image


def flatpak_newer(a, b):
    return pacman.vercmp(a, b) > 0


def _release_date(value):
    if value.isdigit():
        from datetime import datetime
        return datetime.fromtimestamp(int(value)).strftime('%d.%m.%Y')
    return value


def _note_group(title, subtitle):
    group = Adw.PreferencesGroup()
    group.add(row(title, subtitle))
    return group


def _date(d):
    return d.astimezone().strftime('%d.%m.%Y') if d else ''


def _unique_versions(cached):
    seen = []
    for c in cached:
        if c.version not in seen:
            seen.append(c.version)
    return seen

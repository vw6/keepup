"""Главное окно: боковые вкладки «Обзор», «Новости», «Пакеты», «Ядра», «История»."""
import threading
from collections import defaultdict

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, GLib, Gtk, Pango  # noqa: E402

from . import news, ops, pacman  # noqa: E402

PAGES = [
    ('overview', 'Обзор', 'view-grid-symbolic'),
    ('news', 'Новости Arch', 'x-office-document-symbolic'),
    ('packages', 'Пакеты', 'system-software-install-symbolic'),
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
    r = Adw.ActionRow(title=title, subtitle=subtitle, activatable=activatable)
    r.set_use_markup(False)
    r.set_subtitle_lines(3)
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

    @classmethod
    def collect(cls):
        s = cls()
        s.packages = pacman.installed()
        s.cache = pacman.cache()
        pacman.with_cache(s.packages, s.cache)
        s.log = pacman.history()
        s.kernel = pacman.running_kernel()
        s.reboot = pacman.reboot_reasons(s.log, pacman.boot_time())
        try:
            s.updates = pacman.list_updates()
        except (RuntimeError, OSError) as e:
            s.updates_error = str(e)
        s.news, s.news_fresh = news.fetch()
        news.mark(s.news, [u.name for u in s.updates])
        return s


class OperationDialog(Adw.Dialog):
    """Окно с выводом pacman, пока идёт действие; закрыть можно, только когда оно кончилось."""
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
            self.status.set_title('Идёт работа pacman')
        self.buffer.insert(self.buffer.get_end_iter(), line + '\n')
        adj = self.scroll.get_vadjustment()
        GLib.idle_add(lambda: adj.set_value(adj.get_upper()))

    def finish(self, code):
        self.spinner.set_visible(False)
        messages = {0: 'Готово', 126: 'Отменено: пароль не введён', 127: 'Нет прав на это действие'}
        self.status.set_title(messages.get(code, f'pacman завершился с ошибкой (код {code}), подробности выше'))
        self.set_can_close(True)
        self.on_finished(code)


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
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
        self.content_page = Adw.NavigationPage(title=PAGES[0][1], child=content)
        split.set_content(self.content_page)
        self.split = split

        bp = Adw.Breakpoint.new(Adw.BreakpointCondition.parse('max-width: 640sp'))
        bp.add_setter(split, 'collapsed', True)
        self.add_breakpoint(bp)

        self.toasts.set_child(split)
        self.set_content(self.toasts)
        self.sidebar_list.select_row(self.sidebar_list.get_row_at_index(0))
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
                           ('kernels', self.build_kernels), ('history', self.build_history)):
            self.holders[key].set_child(build())

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=text, timeout=5))

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
            exp = Adw.ExpanderRow(title=item.title, subtitle=_date(item.date))
            exp.set_use_markup(False)
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
                r = row(u.name, f'{u.old}  →  {u.new}')
                if u.needs_reboot:
                    r.add_suffix(pill('перезагрузка', 'warn'))
                upd.add(r)
        else:
            upd.add(row('Обновлений нет', s.updates_error or 'все пакеты свежие'))
        page.add(upd)

        self.rollback_group = Adw.PreferencesGroup(
            title='Откат версии', description='Пакеты, прошлые версии которых лежат в кэше pacman. Найдите пакет и выберите версию.')
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
        candidates = sorted((p for p in self.snap.packages.values() if p.cached and (not q or q in p.name.lower())),
                            key=lambda p: (not p.name.lower().startswith(q), p.name))
        for pkg in candidates[:ROLLBACK_ROWS]:
            r = row(pkg.name, f'установлена {pkg.version} · в кэше версий: {len(pkg.cached)}', activatable=True)
            r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
            r.connect('activated', lambda _r, name=pkg.name: self.ask_rollback(name))
            self.rollback_group.add(r)
            self.rollback_rows.append(r)
        if len(candidates) > ROLLBACK_ROWS:
            r = row(f'Ещё {len(candidates) - ROLLBACK_ROWS}', 'уточните поиск')
            self.rollback_group.add(r)
            self.rollback_rows.append(r)
        if not candidates:
            r = row('Ничего не найдено', 'у этого пакета нет прошлых версий в кэше')
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

    # Ядра

    def build_kernels(self):
        s = self.snap
        page = Adw.PreferencesPage()
        release, running_base = s.kernel
        group = Adw.PreferencesGroup(title='Установленные ядра',
                                     description='Держите хотя бы одно запасное ядро: если новое не загрузится, его выбирают в загрузчике.')
        page.add(group)
        found = pacman.kernels(s.packages)
        if not found:
            group.add(row('Ядра не найдены', 'не удалось сопоставить пакеты ядер с /usr/lib/modules'))
        for k in found:
            exp = Adw.ExpanderRow(title=k.name, subtitle=f'установлено {k.version}' + (f' · запущено {release}' if k.name == running_base else ''))
            exp.set_use_markup(False)
            if k.name == running_base:
                exp.add_suffix(pill('запущено', 'ok'))
            pending = [u for u in s.updates if u.name == k.name]
            if pending:
                exp.add_suffix(pill(f'есть {pending[0].new}', 'info'))
            if k.cached:
                for c in _unique_versions(k.cached):
                    newer = pacman.vercmp(c, k.version) > 0
                    r = row(f'{"Поставить" if newer else "Откатить на"} {c}',
                            ('новее установленной; ' if newer else '') + 'вместе с заголовками и модулями этого ядра', activatable=True)
                    r.add_suffix(Gtk.Image(icon_name='go-next-symbolic'))
                    r.connect('activated', lambda _r, name=k.name: self.ask_rollback(name))
                    exp.add_row(r)
            else:
                exp.add_row(row('Прошлых версий в кэше нет'))
            group.add(exp)
        return page

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
            exp = Adw.ExpanderRow(title=day.strftime('%d.%m.%Y'), subtitle=summary)
            for e in entries[:200]:
                exp.add_row(row(e.name, f'{e.when.strftime("%H:%M")} · {verbs[e.action]} · {e.versions}'))
            if len(entries) > 200:
                exp.add_row(row(f'и ещё {len(entries) - 200}'))
            group.add(exp)
        return page

    # Действия

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

    def run_op(self, title, cmd):
        if ops.busy():
            self.toast('pacman сейчас занят другой программой, попробуйте позже')
            return
        OperationDialog(title, cmd, lambda code: code == 0 and self.refresh()).present(self)


def _date(d):
    return d.astimezone().strftime('%d.%m.%Y') if d else ''


def _unique_versions(cached):
    seen = []
    for c in cached:
        if c.version not in seen:
            seen.append(c.version)
    return seen

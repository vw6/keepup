<p align="center"><img src="data/icons/ru.vw6.KeepUp.svg" width="128" alt="KeepUp"></p>

# KeepUp

Окно обновлений для Arch Linux и CachyOS в стиле GNOME. *English below.*

## О проекте

Это в первую очередь личный проект: я сделал его для себя, чтобы обновлять свою систему не из консоли и спокойно откатывать то, что не понравилось. Выкладываю как есть. Развитие, исправления и ответы на вопросы не гарантируются, хотя замечания и правки приветствуются.

Программа написана примерно за час вместе с Claude Opus 5.5 (Anthropic): я ставил задачи и проверял результат, модель писала код. Это прототип на Python; если он приживётся, его планируется переписать на Rust.

## Что умеет

На вкладке «Обзор» видно, сколько обновлений ждёт в репозиториях, AUR и Flatpak, нужна ли перезагрузка и какие новости Arch стоит прочитать до обновления. Новости Arch показываются с пометкой, если касаются ваших пакетов, переводятся на русский через translate-shell (команды, версии и имена пакетов не переводятся) и сохраняются локально вместе с переводом.

У каждого пакета, из репозиториев или из AUR, и у каждого приложения Flatpak есть своя страница: описание, снимки экрана, история версий и примечания к выпуску из самой программы, выпусков GitHub или каталога Flathub. Отсюда можно обновить один пакет, если это не тянет обновление общих библиотек, поставить любую прошлую версию из кэша pacman, архива Arch или правки PKGBUILD в AUR, удалить пакет. Пакеты одной сборки (например, все части mesa) ставятся и откатываются только вместе.

Вкладка «Ядра» показывает установленные и доступные ядра, ставит ядро вместе с заголовками и модулями ваших устройств, подсказывает, если у запасного ядра не хватает модуля, и выбирает основное ядро в загрузчике Limine. Вкладка «История» раскладывает журнал pacman по дням.

Все действия с правами root идут через стандартное окно пароля (pkexec). Пароль программа не видит.

## Установка

Из AUR:

```
paru -S keepup
```

Из исходников:

```
make install DESTDIR=/ PREFIX=/usr
```

Нужны python 3.10 или новее, python-gobject, gtk4 4.10 или новее, libadwaita 1.7 или новее, pacman-contrib и polkit. Необязательно: paru для AUR, flatpak, translate-shell для перевода новостей, git и base-devel для сборки прошлых версий из AUR, limine-mkinitcpio-hook для выбора основного ядра.

Для разработки: `./run.sh` запускает программу из каталога проекта, `./run.sh --page aur` открывает сразу нужную вкладку, `KEEPUP_SEPARATE=1` запускает второй экземпляр рядом с уже открытым окном, проверки запускаются командой `make test`. Данные программы лежат в `~/.cache/keepup`, `~/.local/share/keepup` (архив новостей с переводами) и `~/.config/keepup`.

## Лицензия

[Mozilla Public License 2.0](LICENSE). Код можно свободно использовать, менять и включать в свои проекты, в том числе закрытые. Если вы распространяете изменённые файлы KeepUp, их исходный код нужно открыть под той же лицензией.

---

## English

KeepUp is a GNOME-style update manager for Arch Linux and CachyOS: pending updates from the repositories, AUR and Flatpak, Arch news with optional machine translation and an offline archive, a page for every package with release notes, rollback to any previous version (pacman cache, Arch Linux Archive or an AUR commit), kernel management with Limine, and pacman history by day.

This is primarily a personal project, published as is. Further development, fixes and support are not guaranteed. It was written in about an hour together with Claude Opus 5.5 (Anthropic) as a Python prototype that may later be rewritten in Rust.

Licensed under the [Mozilla Public License 2.0](LICENSE): free to use, modify and include in other projects, including proprietary ones; modified KeepUp files must be published under the same license when distributed.

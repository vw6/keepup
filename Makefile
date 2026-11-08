# Установка в систему: make install DESTDIR=… PREFIX=/usr (так её зовёт PKGBUILD).
PREFIX ?= /usr
DESTDIR ?=
LIB = $(DESTDIR)$(PREFIX)/lib/keepup
SHARE = $(DESTDIR)$(PREFIX)/share

install:
	install -d $(LIB)/keepup/helpers
	install -m644 keepup/*.py $(LIB)/keepup/
	install -m644 keepup/helpers/*.py $(LIB)/keepup/helpers/
	install -d $(LIB)/keepup/locales
	install -m644 keepup/locales/*.json $(LIB)/keepup/locales/
	install -Dm755 bin/keepup $(DESTDIR)$(PREFIX)/bin/keepup
	install -Dm644 data/ru.vw6.KeepUp.desktop $(SHARE)/applications/ru.vw6.KeepUp.desktop
	install -Dm644 data/ru.vw6.KeepUp.metainfo.xml $(SHARE)/metainfo/ru.vw6.KeepUp.metainfo.xml
	install -Dm644 data/icons/ru.vw6.KeepUp.svg $(SHARE)/icons/hicolor/scalable/apps/ru.vw6.KeepUp.svg
	install -Dm644 data/icons/ru.vw6.KeepUp-symbolic.svg $(SHARE)/icons/hicolor/symbolic/apps/ru.vw6.KeepUp-symbolic.svg

test:
	python3 -m unittest discover -s tests -t .

.PHONY: install test

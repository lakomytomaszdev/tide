# Nakładka ikon „Tide” na motywie bazowym, lokalne kopie .desktop i polecenie tide-icons.
INSTALL_TARGETS += install-icons
UNINSTALL_TARGETS += uninstall-icons
TEST_DIRS += theme/icons/tests

# Motyw bazowy nakładki, np. make install ICON_BASE=Papirus-Dark
ICON_BASE ?=

.PHONY: install-icons uninstall-icons icons-audit icons-sync icons-build

install-icons:
	@/usr/bin/python3 scripts/icons.py install $(if $(ICON_BASE),--base $(ICON_BASE))

uninstall-icons:
	@/usr/bin/python3 scripts/icons.py uninstall

icons-audit:
	@/usr/bin/python3 theme/icons/tide_icons.py audit

icons-sync:
	@/usr/bin/python3 theme/icons/tide_icons.py sync $(if $(ICON_BASE),--base $(ICON_BASE))

icons-build:
	@/usr/bin/python3 theme/icons/tide_icons.py build $(if $(ICON_BASE),--base $(ICON_BASE))

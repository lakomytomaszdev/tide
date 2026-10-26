# Dock Tide: dolny panel z aplikacjami pośrodku i widżetami Tide z prawej.
# Idzie na koniec instalacji (LATE_INSTALL_TARGETS), bo dokłada tylko widżety już zainstalowane.
LATE_INSTALL_TARGETS += install-dock
UNINSTALL_TARGETS += uninstall-dock

.PHONY: install-dock uninstall-dock show-dock

install-dock:
	@/usr/bin/python3 scripts/dock.py install

uninstall-dock:
	@/usr/bin/python3 scripts/dock.py uninstall

show-dock:
	@/usr/bin/python3 scripts/dock.py show

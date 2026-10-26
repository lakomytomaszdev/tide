# Pasek boczny Tide: chowany panel przy lewej krawędzi lewego monitora z widokami szczegółowymi
# agentów, systemu, worktrees i Limitów AI, wysuwany myszą albo skrótem Meta+S.
# Idzie na koniec instalacji (LATE_INSTALL_TARGETS), bo dokłada tylko widżety już zainstalowane.
# Odinstalowanie (mk/*.mk w kolejności alfabetycznej) usuwa panel przed pakietami widżetów.
LATE_INSTALL_TARGETS += install-sidebar
UNINSTALL_TARGETS += uninstall-sidebar

.PHONY: install-sidebar uninstall-sidebar show-sidebar sidebar-preview

install-sidebar:
	@/usr/bin/python3 scripts/sidebar.py install

uninstall-sidebar:
	@/usr/bin/python3 scripts/sidebar.py uninstall

# Skrypt plasmashell, który uruchomiłaby instalacja — bez uruchamiania.
show-sidebar:
	@/usr/bin/python3 scripts/sidebar.py show

# Zrzuty paska (schowany, wysunięty skrótem, znów schowany) z zagnieżdżonej sesji w build/sidebar.
sidebar-preview:
	@sh scripts/sidebar_preview.sh build/sidebar

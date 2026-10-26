# Widżet „Nowe zadanie” (org.tide.newtask): kafelek „＋ Zadanie · nowe dla agenta” w docku, który
# otwiera okno „Tide: nowe zadanie” przez tide-daemon (/Tasks ShowDialog, install-daemon i install-new-task).
INSTALL_TARGETS += install-widget-newtask
UNINSTALL_TARGETS += uninstall-widget-newtask

.PHONY: install-widget-newtask uninstall-widget-newtask

install-widget-newtask:
	@bash widgets/install.sh install widgets/newtask

uninstall-widget-newtask:
	@bash widgets/install.sh uninstall widgets/newtask

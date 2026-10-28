# Widżet Praca (org.tide.work, PT-40): jeden kafelek zamiast Tablicy i Agentów — karty tablicy i sesje
# agentów bez karty z jednej listy usługi (/Board Work()), w docku po prawej (przed Limitami AI) i w pasku
# bocznym. Dane daje tide-daemon (install-daemon); okno Tide (install-tide-window) otwiera karty, Tablicę
# i Sesje. Pakiety org.tide.board i org.tide.agents zostają zainstalowane do wycofania. Testy: widgets/tests
# (mk/widget-agents.mk), podgląd: make preview-widgets albo widgets/preview_work.py.
INSTALL_TARGETS += install-widget-work
UNINSTALL_TARGETS += uninstall-widget-work

.PHONY: install-widget-work uninstall-widget-work

install-widget-work:
	@bash widgets/install.sh install widgets/work

uninstall-widget-work:
	@bash widgets/install.sh uninstall widgets/work

# Widżet tablicy pracy (org.tide.board): kafelek w docku (po prawej, przed Agentami), popup z tym,
# co czeka, jest w toku i do sprawdzenia, i szybkim dopisaniem do backlogu. Dane daje tide-daemon
# (obszar /Board), który instaluje się przez install-daemon; okno Tide (install-tide-window) otwiera
# kliknięcie w wiersz. Testy: widgets/tests (mk/widget-agents.mk), podgląd: make preview-widgets.
INSTALL_TARGETS += install-widget-board
UNINSTALL_TARGETS += uninstall-widget-board

.PHONY: install-widget-board uninstall-widget-board

install-widget-board:
	@bash widgets/install.sh install widgets/board

uninstall-widget-board:
	@bash widgets/install.sh uninstall widgets/board

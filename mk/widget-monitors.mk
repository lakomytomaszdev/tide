# Widżet „Monitory” (org.tide.stream.monitors, PT-38): kafelek „Monitory · PC → Xbox” w docku Stream z popupem
# potwierdzenia. Przełączenie (wejście DP-2 przez DDC/CI, układ w KWin bez zmian) robi tide-daemon (/Monitors).
# Cel tylko instaluje pakiet — miejsce w docku Stream należy do układu paneli Stream (PT-36). Testy w widgets/tests
# (katalog dopisuje mk/widget-agents.mk). Podgląd: make preview-monitors (build/preview/stream-monitors-*.png).
INSTALL_TARGETS += install-widget-monitors
UNINSTALL_TARGETS += uninstall-widget-monitors

.PHONY: install-widget-monitors uninstall-widget-monitors preview-monitors monitors-status

install-widget-monitors:
	@bash widgets/install.sh install widgets/monitors

uninstall-widget-monitors:
	@bash widgets/install.sh uninstall widgets/monitors

# Kafelek i potwierdzenie na fałszywym stanie usługi; paleta Streamer (kafelek jest tylko w Stream),
# MONITORS_PALETTE=mist dla porównania.
MONITORS_PALETTE ?= streamer
preview-monitors:
	@/usr/bin/python3 widgets/preview_monitors.py build/preview --palette theme/palette/$(MONITORS_PALETTE).json

# Wejście prawego monitora według stanu usługi (tylko odczyt pliku stanu, bez DDC).
monitors-status:
	@/usr/bin/python3 daemon/tide_daemon/monitors.py status

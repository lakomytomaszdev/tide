# Okno „Tide Sesje” (QML + PyQt6) z plikiem .desktop: koordynatorzy, tablica ticketów, podagenci.
# Dane i akcje daje tide-daemon (/Coordination, install-daemon); otwierają je widżet agentów i KRunner.
INSTALL_TARGETS += install-sessions-window
UNINSTALL_TARGETS += uninstall-sessions-window
TEST_DIRS += apps/sessions/tests

.PHONY: install-sessions-window uninstall-sessions-window preview-sessions-window

install-sessions-window:
	@bash apps/sessions/install.sh install

uninstall-sessions-window:
	@bash apps/sessions/install.sh uninstall

# Podgląd okna na danych z tego komputera (tylko odczyt, YouTrack tylko GET), bez instalowania:
# build/sessions-window.png; SANITIZE=1 — bez treści pracy (tytuły, opisy, pytania, wyniki).
SESSIONS_SELECT ?=
preview-sessions-window:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/sessions build/sessions-window
	@/usr/bin/python3 daemon/tide-daemon.py --print-coordination > build/coordination.json
	@/usr/bin/python3 tools/sessions_preview.py build/coordination.json $(if $(SANITIZE),--sanitize) \
		> build/sessions-preview.json
	@QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/sessions-window/tide-sessions.py \
		--data build/sessions-preview.json $(if $(SESSIONS_SELECT),--select $(SESSIONS_SELECT)) \
		--grab build/sessions-window.png
	@echo "Podgląd: build/sessions-window.png"

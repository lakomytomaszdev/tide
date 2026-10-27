# Widok „Sesje” okna Tide (apps/sessions: backend.py i QML; dawniej osobne okno „Tide Sesje”):
# koordynatorzy, tablica ticketów, podagenci. Instaluje go okno Tide (mk/tide-window.mk) razem
# z przekierowaniem dawnego launchera; tu zostają testy i podgląd samego widoku.
TEST_DIRS += apps/sessions/tests

.PHONY: install-sessions-window uninstall-sessions-window preview-sessions-window

# Dawne cele — dziś to samo co okno Tide.
install-sessions-window: install-tide-window
uninstall-sessions-window: uninstall-tide-window

# Podgląd widoku na danych z tego komputera (tylko odczyt, YouTrack tylko GET), bez instalowania:
# build/sessions-window.png; SANITIZE=1 — bez treści pracy (tytuły, opisy, pytania, wyniki).
SESSIONS_SELECT ?=
preview-sessions-window:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/tide build/tide-window
	@/usr/bin/python3 daemon/tide-daemon.py --print-coordination > build/coordination.json
	@/usr/bin/python3 tools/sessions_preview.py build/coordination.json $(if $(SANITIZE),--sanitize) \
		> build/sessions-preview.json
	@QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/tide-window/tide.py --view sessions \
		--data build/sessions-preview.json $(if $(SESSIONS_SELECT),--select $(SESSIONS_SELECT)) \
		--grab build/sessions-window.png
	@echo "Podgląd: build/sessions-window.png"

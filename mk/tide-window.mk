# Okno „Tide” (QML + PyQt6) z paskiem widoków: Tablica, Sesje, Worktrees, Docker, Raporty,
# Modele (views.json).
# Dane widoków daje tide-daemon (install-daemon); otwierają je widżety, KRunner i menu aplikacji.
# Instaluje też przekierowania dawnych okien Sesje i Worktrees (apps/tide/install.sh).
INSTALL_TARGETS += install-tide-window
UNINSTALL_TARGETS += uninstall-tide-window
TEST_DIRS += apps/tide/tests
TEST_DIRS += apps/issues/tests

.PHONY: install-tide-window uninstall-tide-window preview-tide-window

install-tide-window:
	@bash apps/tide/install.sh install

uninstall-tide-window:
	@bash apps/tide/install.sh uninstall

# Podgląd każdego widoku na danych z tego komputera (tylko odczyt, YouTrack tylko GET), bez
# instalowania: build/tide-window-<widok>.png; SANITIZE=1 — Sesje bez treści pracy.
TIDE_VIEWS := board issues sessions worktrees docker reports models
preview-tide-window:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/tide build/tide-window
	@/usr/bin/python3 daemon/tide-daemon.py --print-coordination > build/coordination.json
	@/usr/bin/python3 tools/sessions_preview.py build/coordination.json $(if $(SANITIZE),--sanitize) \
		> build/sessions-preview.json
	@/usr/bin/python3 daemon/tide-daemon.py --print-worktrees > build/worktrees.json
	@for view in $(TIDE_VIEWS); do \
		QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/tide-window/tide.py --view $$view \
			--data sessions=build/sessions-preview.json --data worktrees=build/worktrees.json \
			--grab build/tide-window-$$view.png || exit 1; \
		echo "Podgląd: build/tide-window-$$view.png"; \
	done

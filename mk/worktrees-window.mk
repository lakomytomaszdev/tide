# Widok „Worktrees” okna Tide (apps/worktrees: backend.py i QML; dawniej osobne okno „Tide Worktrees”).
# Dane idą z tide-daemon (/Worktrees). Instaluje go okno Tide (mk/tide-window.mk) razem
# z przekierowaniem dawnego launchera; tu zostają testy i podgląd samego widoku.
TEST_DIRS += apps/worktrees/tests

.PHONY: install-worktrees-window uninstall-worktrees-window preview-worktrees-window

# Dawne cele — dziś to samo co okno Tide.
install-worktrees-window: install-tide-window
uninstall-worktrees-window: uninstall-tide-window

# Podgląd widoku na danych z tego komputera (tylko odczyt), bez instalowania: build/worktrees-window.png
preview-worktrees-window:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/tide build/tide-window
	@/usr/bin/python3 daemon/tide-daemon.py --print-worktrees > build/worktrees.json
	@QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/tide-window/tide.py --view worktrees \
		--data build/worktrees.json --grab build/worktrees-window.png
	@echo "Podgląd: build/worktrees-window.png"

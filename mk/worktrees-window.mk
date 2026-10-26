# Okno „Tide Worktrees” (QML + PyQt6) z plikiem .desktop. Otwiera je kafelek org.tide.worktrees
# przez tide-daemon (install-daemon), dane też idą z tide-daemon (/Worktrees).
INSTALL_TARGETS += install-worktrees-window
UNINSTALL_TARGETS += uninstall-worktrees-window
TEST_DIRS += apps/worktrees/tests

.PHONY: install-worktrees-window uninstall-worktrees-window preview-worktrees-window

install-worktrees-window:
	@bash apps/worktrees/install.sh install

uninstall-worktrees-window:
	@bash apps/worktrees/install.sh uninstall

# Podgląd okna na danych z tego komputera (tylko odczyt), bez instalowania: build/worktrees-window.png
preview-worktrees-window:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/worktrees build/worktrees-window
	@/usr/bin/python3 daemon/tide-daemon.py --print-worktrees > build/worktrees.json
	@QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/worktrees-window/tide-worktrees.py \
		--data build/worktrees.json --grab build/worktrees-window.png
	@echo "Podgląd: build/worktrees-window.png"

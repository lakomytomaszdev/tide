# tide-tray: lustra elementów zasobnika z kafelkami Tide (decyzja 95). Kafelki są w nakładce ikon,
# więc instalacja idzie po install-icons (kolejność plików mk/ — icons.mk < tray.mk).
INSTALL_TARGETS += install-tray
UNINSTALL_TARGETS += uninstall-tray
TEST_DIRS += tray/tests

.PHONY: install-tray uninstall-tray tray-status

install-tray:
	@bash tray/install.sh

uninstall-tray:
	@bash tray/uninstall.sh

# Co robi usługa: ostatnie wpisy dziennika i wyłączone przez nią oryginały.
tray-status:
	@systemctl --user --no-pager status tide-tray.service | head -5 || true
	@journalctl --user -u tide-tray.service -n 20 --no-pager -o cat || true
	@cat "$${XDG_STATE_HOME:-$$HOME/.local/state}/tide/tray-mirror.json" 2>/dev/null || echo "brak wyłączonych oryginałów"

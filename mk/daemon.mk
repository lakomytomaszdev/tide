# tide-daemon: usługa użytkownika systemd z danymi dla widżetów.
INSTALL_TARGETS += install-daemon
UNINSTALL_TARGETS += uninstall-daemon
TEST_DIRS += daemon/tests

.PHONY: install-daemon uninstall-daemon

install-daemon:
	@bash daemon/install.sh

uninstall-daemon:
	@bash daemon/uninstall.sh

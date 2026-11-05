INSTALL_TARGETS += install-cli
UNINSTALL_TARGETS += uninstall-cli

.PHONY: install-cli uninstall-cli
install-cli:
	@bash cli/install.sh

uninstall-cli:
	@bash cli/uninstall.sh

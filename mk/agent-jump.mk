# Skok do czekającego agenta: skrypt KWin ze skrótem Meta+A wołający tide-daemon.
# Powiadomienia „Agent czeka” są w samej usłudze (install-daemon).
INSTALL_TARGETS += install-agent-jump
UNINSTALL_TARGETS += uninstall-agent-jump
TEST_DIRS += kwin/tests

.PHONY: install-agent-jump uninstall-agent-jump

install-agent-jump:
	@bash kwin/install.sh install kwin/agent-jump

uninstall-agent-jump:
	@bash kwin/install.sh uninstall kwin/agent-jump

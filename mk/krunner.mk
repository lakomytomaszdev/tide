# Wtyczka KRunnera: sesje agentów z 30 dni, projekty i worktrees. Wyniki daje tide-daemon
# (org.tide.Daemon.KRunner, instalowany przez install-daemon); tu jest tylko plik .desktop wtyczki.
INSTALL_TARGETS += install-krunner
UNINSTALL_TARGETS += uninstall-krunner

.PHONY: install-krunner uninstall-krunner

install-krunner:
	@bash krunner/install.sh install

uninstall-krunner:
	@bash krunner/install.sh uninstall

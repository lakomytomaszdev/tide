# Widżet worktrees (org.tide.worktrees). Dane daje tide-daemon (obszar /Worktrees), który
# instaluje się przez install-daemon.
INSTALL_TARGETS += install-widget-worktrees
UNINSTALL_TARGETS += uninstall-widget-worktrees

.PHONY: install-widget-worktrees uninstall-widget-worktrees

install-widget-worktrees:
	@bash widgets/install.sh install widgets/worktrees

uninstall-widget-worktrees:
	@bash widgets/install.sh uninstall widgets/worktrees

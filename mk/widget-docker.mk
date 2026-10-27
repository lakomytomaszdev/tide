# Widżet Docker (org.tide.docker): kafelek w docku z lewej, zaraz po System, popup z projektami
# i padniętymi kontenerami, link do widoku Docker w oknie Tide. Dane daje tide-daemon (obszar /Docker),
# który instaluje się przez install-daemon. Testy w widgets/tests (katalog dopisuje mk/widget-agents.mk).
INSTALL_TARGETS += install-widget-docker
UNINSTALL_TARGETS += uninstall-widget-docker

.PHONY: install-widget-docker uninstall-widget-docker

install-widget-docker:
	@bash widgets/install.sh install widgets/docker

uninstall-widget-docker:
	@bash widgets/install.sh uninstall widgets/docker

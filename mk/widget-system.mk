# Widżet System (org.tide.system) na czujnikach ksystemstats. Testy w widgets/tests
# (katalog dopisuje już mk/widget-agents.mk). Bez pakietu ksystemstats widżet działa,
# ale procesor, pamięć i karta graficzna czekają na dane — pakiet tylko po apt-get -s install.
INSTALL_TARGETS += install-widget-system
UNINSTALL_TARGETS += uninstall-widget-system

.PHONY: install-widget-system uninstall-widget-system

install-widget-system:
	@bash widgets/install.sh install widgets/system

uninstall-widget-system:
	@bash widgets/install.sh uninstall widgets/system

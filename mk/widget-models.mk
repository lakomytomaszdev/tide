# Widżet Modele (org.tide.models): stan serwera modeli lokalnych, model w pamięci, kolejka; popup z serwerami
# i rolami, link do widoku Modele w oknie Tide. Od PT-41 poza dockiem (model lokalny jest w kafelku „AI”,
# org.tide.limits) — pakiet instaluje się dalej, żeby dało się wrócić jedną linią w scripts/dock.py.
# Dane daje tide-daemon (obszar /Models), który instaluje się przez install-daemon. Testy w widgets/tests
# (katalog dopisuje mk/widget-agents.mk), podgląd: make preview-widgets.
INSTALL_TARGETS += install-widget-models
UNINSTALL_TARGETS += uninstall-widget-models

.PHONY: install-widget-models uninstall-widget-models

install-widget-models:
	@bash widgets/install.sh install widgets/models

uninstall-widget-models:
	@bash widgets/install.sh uninstall widgets/models

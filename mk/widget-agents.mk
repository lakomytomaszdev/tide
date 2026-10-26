# Widżet agentów (org.tide.agents) z wspólną biblioteką QML i paletą.
INSTALL_TARGETS += install-widget-agents
UNINSTALL_TARGETS += uninstall-widget-agents
TEST_DIRS += widgets/tests

.PHONY: install-widget-agents uninstall-widget-agents preview-widgets

install-widget-agents:
	@bash widgets/install.sh install widgets/agents

uninstall-widget-agents:
	@bash widgets/install.sh uninstall widgets/agents

# Kafelki czterech widżetów na tle docka (qml6 offscreen, przykładowe dane), bez instalowania.
preview-widgets:
	@/usr/bin/python3 widgets/preview.py build/preview

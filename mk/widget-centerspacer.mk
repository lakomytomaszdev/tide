# Widżet „Środek” (org.tide.centerspacer): lewy odstęp górnych pasków, który trzyma zegar na środku.
# Dokłada go install-panels (mk/panels.mk), więc instaluje się przed nim. Testy w widgets/tests.
INSTALL_TARGETS += install-widget-centerspacer
UNINSTALL_TARGETS += uninstall-widget-centerspacer

.PHONY: install-widget-centerspacer uninstall-widget-centerspacer

install-widget-centerspacer:
	@bash widgets/install.sh install widgets/centerspacer

uninstall-widget-centerspacer:
	@bash widgets/install.sh uninstall widgets/centerspacer

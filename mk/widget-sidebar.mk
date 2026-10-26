# Nagłówek paska bocznego Tide (org.tide.sidebar): nazwa i skrót Meta+S, który wysuwa pasek.
# Panel z widżetami składa scripts/sidebar.py (mk/sidebar.mk), po instalacji widżetów.
INSTALL_TARGETS += install-widget-sidebar
UNINSTALL_TARGETS += uninstall-widget-sidebar

.PHONY: install-widget-sidebar uninstall-widget-sidebar

install-widget-sidebar:
	@bash widgets/install.sh install widgets/sidebar

uninstall-widget-sidebar:
	@bash widgets/install.sh uninstall widgets/sidebar

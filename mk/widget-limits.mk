# Widżet Limity AI (org.tide.limits) na danych z usługi QuotaApp. Testy w widgets/tests
# (katalog dopisuje już mk/widget-agents.mk). Port i token usługi trafiają do pakietu przy instalacji.
INSTALL_TARGETS += install-widget-limits
UNINSTALL_TARGETS += uninstall-widget-limits

.PHONY: install-widget-limits uninstall-widget-limits swap-limits-widget

install-widget-limits:
	@bash widgets/install.sh install widgets/limits

# Usuwa tylko org.tide.limits — stary widżet QuotaApp (org.limityai.widget) zostaje.
uninstall-widget-limits:
	@bash widgets/install.sh uninstall widgets/limits

# Podmiana w dolnym panelu: org.limityai.widget → org.tide.limits (najpierw kopia zapasowa).
# Nie jest częścią make install. Sam skrypt bez zmian: bash scripts/swap-limits-widget.sh --print
swap-limits-widget:
	@bash scripts/swap-limits-widget.sh

# Pudełko karty: Incus z pulą btrfs, projekt ograniczony i statyczne nftables (PT-166). Instalacja wymaga
# sudo i zmienia system, dlatego NIE jest częścią make install ani make uninstall — tylko jawne cele box-*.
# Testy: scripts/tests/test_box_install.py (scripts/tests jest już w TEST_DIRS). Opis: docs/tide-daemon.md,
# sekcja „Pudełko → Instalacja”. Cele box-base (#16) i box-check (#28) dopisują kolejne tickety.
SUDO ?= sudo
# Port proxy Tide na adresie mostka — ta sama wartość co box.proxy-port usługi; zmiana = ponowne box-install.
BOX_PROXY_PORT ?= 18080
# restricted (domyślnie) albo admin — admin tylko po P-5 z PT-164 z wpisem tryb-admin w docs/box-decisions.md.
BOX_PROJECT_MODE ?= restricted
OUT ?=

.PHONY: box-install box-uninstall box-state

box-install:
	$(SUDO) bash scripts/box/install.sh --yes --project-mode $(BOX_PROJECT_MODE) --proxy-port $(BOX_PROXY_PORT)

box-uninstall:
	$(SUDO) bash scripts/box/uninstall.sh

box-state:
ifeq ($(OUT),)
	@bash scripts/box/host-state.sh
else
	@mkdir -p "$(dir $(OUT))"
	@bash scripts/box/host-state.sh > "$(OUT)"
	@echo "Zapisano $(OUT)"
endif

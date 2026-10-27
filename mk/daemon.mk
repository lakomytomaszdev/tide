# tide-daemon: usługa użytkownika systemd z danymi dla widżetów.
INSTALL_TARGETS += install-daemon
UNINSTALL_TARGETS += uninstall-daemon
TEST_DIRS += daemon/tests

.PHONY: install-daemon uninstall-daemon

install-daemon:
	@bash daemon/install.sh

uninstall-daemon:
	@bash daemon/uninstall.sh

# Kontrola numerów ticketów w planach agentów wszystkich repozytoriów workspace (tylko odczyt,
# YouTrack tylko GET); kod wyjścia 1 przy kolizji twardej. Reguły: docs/agent-plan-standard.md.
PLANS_WORKSPACE ?= $(HOME)/DevWorkspace
.PHONY: check-plans
check-plans:
	@/usr/bin/python3 tools/check_plans.py "$(PLANS_WORKSPACE)"

# Następny lokalny numer ticketu z rezerwacją (flock na $XDG_STATE_HOME/tide/ticket-ids.json):
#   make next-id REPO=~/DevWorkspace/plasma-tide [PREFIX=PT] [COUNT=3] [TITLE="…"] [DRY_RUN=1]
REPO ?=
PREFIX ?=
COUNT ?= 1
TITLE ?=
DRY_RUN ?=
.PHONY: next-id
next-id:
	@test -n "$(REPO)" || { echo "Podaj REPO=<katalog repozytorium>, np. make next-id REPO=~/DevWorkspace/plasma-tide"; exit 1; }
	@/usr/bin/python3 tools/next_id.py "$(REPO)" --count "$(COUNT)" $(if $(PREFIX),--prefix "$(PREFIX)") \
		$(if $(TITLE),--title "$(TITLE)") $(if $(DRY_RUN),--dry-run)

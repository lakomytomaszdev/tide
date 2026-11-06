.PHONY: help backup backups restore install uninstall test pre-install-backup

NAME ?=
# Fragmenty w mk/ definiują własne cele — bez tego pierwszym celem byłaby instalacja komponentu.
.DEFAULT_GOAL := help

# Każdy komponent dopisuje swoje cele w mk/<komponent>.mk:
#   INSTALL_TARGETS += install-<komponent>
#   UNINSTALL_TARGETS += uninstall-<komponent>
#   TEST_DIRS += <katalog z testami>
INSTALL_TARGETS :=
# Cele, które muszą iść po wszystkich innych (np. dock dokłada zainstalowane już widżety).
LATE_INSTALL_TARGETS :=
UNINSTALL_TARGETS :=
TEST_DIRS :=

-include mk/*.mk

help:
	@echo "make install               instalacja Tide (najpierw kopia zapasowa)"
	@echo "make uninstall             usunięcie wszystkiego, co zainstalował Tide"
	@echo "make test                  testy wszystkich komponentów"
	@echo "make test JOBS=N           pełny przebieg w N procesach (domyślnie rdzenie minus 2)"
	@echo "make test-<zestaw>         zestawy: $(TEST_SUITES)"
	@echo "make test-changed BASE=ref testy zmian względem bazy (domyślnie master)"
	@echo "make test-benchmark        pomiar dawnych katalogów discover i równoległego make test"
	@echo "make activity-look-check   czy wygląd pulpitu zgadza się z opisem bieżącej aktywności (tylko odczyt)"
	@echo "make activity-look-status  przypisania aktywności, wyglądy, dziennik przełączenia, stan kopii silnika"
	@echo "make check-plans           kontrola numerów ticketów w planach agentów (~/DevWorkspace)"
	@echo "make next-id REPO=katalog  następny lokalny numer ticketu z rezerwacją [PREFIX= COUNT= TITLE= DRY_RUN=1]"
	@echo "make backup [NAME=nazwa]   kopia obecnego wyglądu Plasmy"
	@echo "make backups               lista kopii"
	@echo "make restore NAME=nazwa    przywrócenie wyglądu z kopii"
	@echo "make sddm-preview          zrzuty ekranu logowania w build/sddm/preview (bez sudo)"
	@echo "make install-sddm          ekran logowania SDDM (sudo, osobno od make install)"
	@echo "make uninstall-sddm        przywrócenie poprzedniego ekranu logowania (sudo)"
	@echo "make plymouth-preview      klatki ekranu startowego w build/plymouth/preview (bez sudo)"
	@echo "make install-plymouth      ekran startowy Plymouth (sudo, update-initramfs, osobno od make install)"
	@echo "make uninstall-plymouth    przywrócenie poprzedniego ekranu startowego (sudo)"
	@echo "make box-install           Incus, pula btrfs, projekt ograniczony i nftables pudełka (sudo, osobno od make install)"
	@echo "make box-uninstall         cofnięcie make box-install; usuwa wszystkie pudełka (sudo)"
	@echo "make box-state [OUT=plik]  stan hosta do porównania przed instalacją i po odinstalowaniu"
	@echo "make box-base              obraz bazowy pudełka: agenci, proxy, czcionki (tide box base, bez sudo)"

install: pre-install-backup $(INSTALL_TARGETS) $(LATE_INSTALL_TARGETS)
	@echo "Zainstalowano Tide."

uninstall: $(UNINSTALL_TARGETS)
	@echo "Odinstalowano Tide."

pre-install-backup:
	@bash scripts/backup.sh "install-$$(date +%Y-%m-%d_%H%M%S)"

test:
	@/usr/bin/python3 tools/test_runner.py --dirs $(TEST_DIRS) $(TEST_JOB_ARGS) $(TEST_OUTPUT_ARGS)

backup:
	@bash scripts/backup.sh $(NAME)

backups:
	@bash scripts/restore.sh --list

restore:
	@test -n "$(NAME)" || { echo "Podaj NAME=<kopia>, np. make restore NAME=przed-tide"; exit 1; }
	@bash scripts/restore.sh $(NAME)

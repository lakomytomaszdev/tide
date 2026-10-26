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
	@echo "make backup [NAME=nazwa]   kopia obecnego wyglądu Plasmy"
	@echo "make backups               lista kopii"
	@echo "make restore NAME=nazwa    przywrócenie wyglądu z kopii"
	@echo "make sddm-preview          zrzuty ekranu logowania w build/sddm/preview (bez sudo)"
	@echo "make install-sddm          ekran logowania SDDM (sudo, osobno od make install)"
	@echo "make uninstall-sddm        przywrócenie poprzedniego ekranu logowania (sudo)"
	@echo "make plymouth-preview      klatki ekranu startowego w build/plymouth/preview (bez sudo)"
	@echo "make install-plymouth      ekran startowy Plymouth (sudo, update-initramfs, osobno od make install)"
	@echo "make uninstall-plymouth    przywrócenie poprzedniego ekranu startowego (sudo)"

install: pre-install-backup $(INSTALL_TARGETS) $(LATE_INSTALL_TARGETS)
	@echo "Zainstalowano Tide."

uninstall: $(UNINSTALL_TARGETS)
	@echo "Odinstalowano Tide."

pre-install-backup:
	@bash scripts/backup.sh "install-$$(date +%Y-%m-%d_%H%M%S)"

test:
	@for dir in $(TEST_DIRS); do /usr/bin/python3 -m unittest discover -s "$$dir" -t . || exit 1; done

backup:
	@bash scripts/backup.sh $(NAME)

backups:
	@bash scripts/restore.sh --list

restore:
	@test -n "$(NAME)" || { echo "Podaj NAME=<kopia>, np. make restore NAME=przed-tide"; exit 1; }
	@bash scripts/restore.sh $(NAME)

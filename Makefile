.PHONY: help backup backups restore

NAME ?=

help:
	@echo "make backup [NAME=nazwa]   kopia obecnego wyglądu Plasmy"
	@echo "make backups               lista kopii"
	@echo "make restore NAME=nazwa    przywrócenie wyglądu z kopii"

backup:
	@bash scripts/backup.sh $(NAME)

backups:
	@bash scripts/restore.sh --list

restore:
	@test -n "$(NAME)" || { echo "Podaj NAME=<kopia>, np. make restore NAME=przed-tide"; exit 1; }
	@bash scripts/restore.sh $(NAME)

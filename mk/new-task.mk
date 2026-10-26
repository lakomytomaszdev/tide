# Nowe zadanie dla agenta: okno „Tide: nowe zadanie” (QML + PyQt6, plik .desktop, profil Konsoli
# „Tide: zadanie agenta”) i skrypt KWin ze skrótem Meta+Alt+A. Worktree i kartę Konsoli zakłada
# tide-daemon (/Tasks, install-daemon); przycisk „+ Nowe zadanie” jest w widżecie agentów,
# osobny kafelek — widżet org.tide.newtask (install-widget-newtask).
INSTALL_TARGETS += install-new-task install-new-task-shortcut
UNINSTALL_TARGETS += uninstall-new-task uninstall-new-task-shortcut
TEST_DIRS += apps/newtask/tests

.PHONY: install-new-task uninstall-new-task install-new-task-shortcut uninstall-new-task-shortcut preview-new-task

install-new-task:
	@bash apps/newtask/install.sh install

uninstall-new-task:
	@bash apps/newtask/install.sh uninstall

install-new-task-shortcut:
	@bash kwin/install.sh install kwin/new-task

uninstall-new-task-shortcut:
	@bash kwin/install.sh uninstall kwin/new-task

# Podgląd okna na repozytoriach z tego komputera (tylko odczyt), bez instalowania: build/new-task.png
PREVIEW_PROMPT ?= Popraw nagłówek listy sesji w popupie agentów: liczby w Geist Mono, „gotowe” w kolorze semantic.done
preview-new-task:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/newtask build/new-task
	@/usr/bin/python3 daemon/tide-daemon.py --print-worktrees --no-processes > build/worktrees.json
	@/usr/bin/python3 tools/new_task_preview.py build/worktrees.json "$(PREVIEW_PROMPT)" > build/new-task.json
	@QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/new-task/tide-new-task.py --data build/new-task.json \
		--prompt "$(PREVIEW_PROMPT)" --grab build/new-task.png
	@echo "Podgląd: build/new-task.png"

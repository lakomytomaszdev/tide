# Widok „Tablica” okna Tide (apps/board: backend.py i QML): karty ze wszystkich projektów z tablicy
# pracy w tide-daemon (/Board). Instaluje go okno Tide (mk/tide-window.mk); tu zostają testy i podgląd.
TEST_DIRS += apps/board/tests

.PHONY: preview-board-view preview-board-view-sample

# Podgląd widoku na tablicy z tego komputera (tylko odczyt — --print-board niczego nie zakłada ani nie
# commituje), bez instalowania: build/board-view.png.
preview-board-view:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/tide build/tide-window
	@/usr/bin/python3 daemon/tide-daemon.py --print-board > build/board.json
	@QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/tide-window/tide.py --view board --data build/board.json \
		--grab build/board-view.png
	@echo "Podgląd: build/board-view.png"

# To samo na przykładowej tablicy jak na mocku (tymczasowe repozytorium, stan z koordynatora symulowany):
# build/board-view-<stan>.png — tablica, karta, nowa karta, Projekty i tagi, wiersz na projekt, pusta.
preview-board-view-sample:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/tide build/tide-window
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
		/usr/bin/python3 -m apps.board.tests.boardfix "$$tmp/fx" --derived > build/board-sample.json && \
		for state in board card=K-13 new=todo manage=logviewer layout=lanes; do \
			name="$${state%%=*}"; preview=""; [ "$$state" != board ] && preview="--preview $$state"; \
			QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/tide-window/tide.py --view board \
				--data build/board-sample.json $$preview --width 1440 --height 900 \
				--grab build/board-view-$$name.png || exit 1; \
			echo "Podgląd: build/board-view-$$name.png"; \
		done

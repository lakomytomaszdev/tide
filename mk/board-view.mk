# Widok „Tablica” okna Tide (apps/board: backend.py i QML): karty ze wszystkich projektów z tablicy
# pracy w tide-daemon (/Board). Instaluje go okno Tide (mk/tide-window.mk); tu zostają testy i podgląd.
TEST_DIRS += apps/board/tests

.PHONY: preview-board-view preview-board-view-sample preview-board-launch

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

# Okno „Co ma zrobić agent?” z dwoma zrzutami ekranu (PT-43; zrzutami są podglądy tablicy i karty) w chmurze
# i z modelem lokalnym (przykładowa rola `agent`): build/board-launch-shots.png, build/board-launch-local.png.
preview-board-launch:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/tide build/tide-window
	@/usr/bin/python3 daemon/tide-daemon.py --print-models > build/models-status.json
	@tmp="$$(mktemp -d)"; trap 'rm -rf "$$tmp"' EXIT; \
		/usr/bin/python3 -m apps.board.tests.boardfix "$$tmp/fx" --derived > build/board-sample.json && \
		/usr/bin/python3 tools/board_launch_preview.py build/board-sample.json build/models-status.json \
			> build/board-launch.json && \
		/usr/bin/python3 tools/board_launch_preview.py build/board-sample.json build/models-status.json --local \
			> build/board-launch-local.json && \
		run() { QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/tide-window/tide.py --view board \
			--width 1440 --height 900 "$$@"; } && \
		run --data build/board-sample.json --grab build/board-launch-shot-1.png && \
		run --data build/board-sample.json --preview card=K-13 --grab build/board-launch-shot-2.png && \
		shots="$(CURDIR)/build/board-launch-shot-1.png,$(CURDIR)/build/board-launch-shot-2.png" && \
		run --data build/board-launch.json --preview launch=K-9,move --preview "shots=$$shots" \
			--grab build/board-launch-shots.png && \
		run --data build/board-launch-local.json --preview launch=K-9,move --preview "shots=$$shots" \
			--grab build/board-launch-local.png
	@echo "Podgląd: build/board-launch-shots.png, build/board-launch-local.png (model lokalny)"

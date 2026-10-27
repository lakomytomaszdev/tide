# Widok „Raporty” okna Tide (apps/reports: backend.py i QML) — raporty poranne bez przeglądarki.
# Dane idą z tide-daemon (/Reports Days, Day, OpenDay, MarkRead). Instaluje go okno Tide
# (mk/tide-window.mk); tu zostają testy i podgląd widoku na syntetycznych raportach.
TEST_DIRS += apps/reports/tests

.PHONY: preview-reports-view

# Podgląd widoku na danych z apps/reports/tests/fixture.py (bez prawdziwych raportów): build/reports-view.png
preview-reports-view:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/tide build/tide-window
	@/usr/bin/python3 -c "import json, sys; sys.path.insert(0, '.'); from apps.reports.tests import fixture; \
		json.dump(fixture.sample(), open('build/reports-view.json', 'w'), ensure_ascii=False)"
	@TZ=Europe/Warsaw QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/tide-window/tide.py --view reports \
		--data reports=build/reports-view.json --grab build/reports-view.png --width 1440 --height 900
	@echo "Podgląd: build/reports-view.png"

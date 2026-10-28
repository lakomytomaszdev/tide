# Widok „Modele” okna Tide (apps/models: backend.py i QML) — serwery modeli lokalnych, przełącznik
# „ten komputer / Mac”, tryb raportu, role i pobieranie modeli bez edycji plików. Dane i zapis daje
# tide-daemon (/Models); instaluje go okno Tide (mk/tide-window.mk), tu zostają testy i podgląd widoku.
TEST_DIRS += apps/models/tests

.PHONY: preview-models-view

# Podgląd bez instalowania na przykładowym Status() (apps/models/tests/fixture.py), bez usługi i bez
# pytania serwerów: build/models-view-<stan>.png (lista stanów w apps/models/tests/preview.py).
preview-models-view:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/tide build/tide-window
	@/usr/bin/python3 apps/models/tests/preview.py build/tide-window build

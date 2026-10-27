# Widok „Docker” okna Tide (apps/docker: backend.py i QML) — własny podgląd kontenerów zamiast
# Docker Desktop. Dane i akcje daje tide-daemon (/Docker); instaluje go okno Tide (mk/tide-window.mk),
# tu zostają testy i podgląd samego widoku.
TEST_DIRS += apps/docker/tests

.PHONY: preview-docker-view

# Podgląd bez instalowania: build/docker-view.png na danych z tego komputera (wyłącznie GET do Engine API)
# i build/docker-view-sample.png na przykładowych danych (z logami i potwierdzeniem porządków).
preview-docker-view:
	@mkdir -p build
	@/usr/bin/python3 widgets/stage.py --app apps/tide build/tide-window
	@/usr/bin/python3 daemon/tide-daemon.py --print-docker > build/docker.json
	@/usr/bin/python3 apps/docker/tests/fixture.py > build/docker-sample.json
	@QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/tide-window/tide.py --view docker \
		--data build/docker.json --grab build/docker-view.png
	@QT_QPA_PLATFORM=offscreen /usr/bin/python3 build/tide-window/tide.py --view docker \
		--data build/docker-sample.json --preview logs=cookapp-api --grab build/docker-view-sample.png
	@echo "Podgląd: build/docker-view.png, build/docker-view-sample.png"

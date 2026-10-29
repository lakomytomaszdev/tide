# Paleta wariantu — testy spójności wartości, z których korzystają generatory.
TEST_DIRS += theme/palette/tests

# Wariant palety w celach podglądu (wallpaper-preview, preview-plasma-style, lockscreen-build,
# lockscreen-test, lockscreen-screenshot): PALETTE=streamer — aktywność Stream. Mist zapisuje
# podglądy tam, gdzie dotąd; inne warianty w katalogach i plikach z przyrostkiem -<wariant>.
PALETTE ?= mist
PALETTE_FILE = theme/palette/$(PALETTE).json
PALETTE_SUFFIX = $(if $(filter mist,$(PALETTE)),,-$(PALETTE))

.PHONY: mist-golden palette-compare

# Sumy plików Mist dla testu regresji (theme/palette/tests/mist-golden.json) — tylko po zamierzonej
# zmianie wyglądu Mist, razem z nią w jednym commicie.
mist-golden:
	@/usr/bin/python3 theme/palette/outputs.py golden

# Mist obok Streamer: tapeta, ekran blokady (prawdziwy greeter) i styl Plasmy, oba monitory
# w natywnej rozdzielczości → build/compare/ (overview.png i pełne kadry).
palette-compare:
	@/usr/bin/python3 theme/palette/compare.py build/compare

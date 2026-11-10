#!/bin/bash
# Pomiar „przed” karty K-92 (PT-323): sonda offscreen i prawdziwe okno Tide (wszystkie warianty i eksperymenty).
# Użycie z katalogu worktree: bash apps/board/tests/measure_all.sh
# Wyniki: build/pt-323-measure/board.json i window.json (oraz na stdout). Trwa ok. 40 minut (3 × 70 s na kombinację);
# na czas pomiaru nie uruchamiaj innych ciężkich zadań.
set -u
cd "$(dirname "$0")/../../.." || exit 1
mkdir -p build/pt-323-measure
status=0
/usr/bin/python3 apps/board/tests/measure_board.py --out build/pt-323-measure/board.json || status=1
/usr/bin/python3 apps/board/tests/measure_window.py --matrix --out build/pt-323-measure/window.json || status=1
exit $status

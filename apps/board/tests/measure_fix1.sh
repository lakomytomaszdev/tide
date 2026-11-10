#!/bin/bash
# PT-323, poprawki rundy 1 (M1, M2): pomiar okna w niewidocznym kompozytorze kwin (--nested kwin) i powtórzenie sondy.
# Użycie z katalogu worktree: bash apps/board/tests/measure_fix1.sh
# Wyniki: build/pt-323-measure/{board-rerun.json,window-nested.json,nested-error.txt}; trwa ok. 25 minut.
# Na czas pomiaru nie uruchamiaj innych ciężkich zadań (loadavg zapisują same skrypty, a skrypt dopisuje go poniżej).
set -u
cd "$(dirname "$0")/../../.." || exit 1
out=build/pt-323-measure
mkdir -p "$out"
echo "loadavg przed: $(cat /proc/loadavg)" | tee "$out/fix1-loadavg.txt"
status=0
/usr/bin/python3 apps/board/tests/measure_board.py --out "$out/board-rerun.json" || status=1
echo "loadavg po sondzie: $(cat /proc/loadavg)" | tee -a "$out/fix1-loadavg.txt"
if command -v kwin_wayland >/dev/null 2>&1; then
    /usr/bin/python3 apps/board/tests/measure_window.py --nested kwin --matrix \
        --only a-idle,a-active5,b-idle,b-active5,a-active5-no-animations \
        --out "$out/window-nested.json" 2> "$out/nested-error.txt" || status=1
else
    echo "brak kwin_wayland w PATH" | tee "$out/nested-error.txt"
    status=1
fi
echo "loadavg po oknie: $(cat /proc/loadavg)" | tee -a "$out/fix1-loadavg.txt"
exit $status

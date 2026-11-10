#!/bin/bash
# PT-323, poprawki rundy 2: powtórzenie bazy (a) 5 aktywnych kart (rozrzut > 20%), powtórzenie eksperymentu
# --no-animations (pierwotnie przy loadavg 16->7) oraz rozdzielenie migawki od wiązań (static-no-animations);
# QSG_INFO=1 zapisuje renderer (llvmpipe czy GPU) w stderr dziecka.
# Użycie z katalogu worktree: bash apps/board/tests/measure_fix2.sh
# Wyniki: build/pt-323-measure/window-fix2.json, fix2-loadavg.txt, fix2-stderr.txt; trwa ok. 12-15 minut.
set -u
cd "$(dirname "$0")/../../.." || exit 1
out=build/pt-323-measure
mkdir -p "$out"
echo "loadavg przed: $(cat /proc/loadavg)" | tee "$out/fix2-loadavg.txt"
QSG_INFO=1 /usr/bin/python3 apps/board/tests/measure_window.py --nested kwin --matrix \
    --only a-active5,a-active5-no-animations,a-active5-static-no-animations \
    --out "$out/window-fix2.json" 2> "$out/fix2-stderr.txt"
status=$?
echo "loadavg po: $(cat /proc/loadavg)" | tee -a "$out/fix2-loadavg.txt"
grep -m3 -i "renderer\|llvmpipe\|GL_RENDERER" "$out/fix2-stderr.txt" | tee -a "$out/fix2-loadavg.txt"
exit $status

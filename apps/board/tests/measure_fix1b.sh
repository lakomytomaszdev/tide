#!/bin/bash
# PT-323, poprawki rundy 1, część 2: powtórzenie pomiarów okna w niewidocznym kompozytorze kwin, które nie wyszły
# w measure_fix1.sh (katalog pakietu w /tmp został usunięty w trakcie), plus scenariusz zgodności skali (stress).
# Użycie z katalogu worktree: bash apps/board/tests/measure_fix1b.sh
# Wyniki: build/pt-323-measure/window-nested2.json, fix1b-loadavg.txt; trwa ok. 25 minut.
set -u
cd "$(dirname "$0")/../../.." || exit 1
out=build/pt-323-measure
mkdir -p "$out"
echo "loadavg przed: $(cat /proc/loadavg)" | tee "$out/fix1b-loadavg.txt"
/usr/bin/python3 apps/board/tests/measure_window.py --nested kwin --matrix \
    --only a-idle,a-active5,b-idle,b-active5,a-stress,b-stress \
    --out "$out/window-nested2.json" 2> "$out/nested2-stderr.txt"
status=$?
echo "loadavg po: $(cat /proc/loadavg)" | tee -a "$out/fix1b-loadavg.txt"
exit $status

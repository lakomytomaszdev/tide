#!/bin/bash
# Pomiar skali produkcji K-98 (PT-362) przez kolejkę koordynatora: jeden przebieg naraz, izolowane dane
# (reproduktor sam tworzy HOME/XDG_*/Tablicę, sesje i worktree w katalogu tymczasowym, usługa na prywatnej magistrali),
# bez instalacji, produkcyjnej magistrali i dzienników.
#
# Ważność (decyzja właściciela 11.11): /proc/loadavg przed i po próbie; próba ważna tylko przy 1-min obciążeniu
# < liczba rdzeni / 2 w obu odczytach. Najwyżej 3 próby; rozstrzyga pierwsza ważna (bez wybierania najlepszej).
#
#   before — kod HEAD (przed zmianą), box.enabled=true; ocena `perf_scale_k98_eval.py reproduce`
#   after  — kod HEAD (kandydat), box.enabled true i false oraz reproduktor PT-342 (`perf_board_refresh.py`, bez zmian)
#            idle i busy (--busy-ms 2000); ocena `perf_scale_k98_eval.py gate` względem wyniku „przed”
#
# Użycie: bash daemon/tests/perf_scale_k98.sh <katalog wyniku> before
#         bash daemon/tests/perf_scale_k98.sh <katalog wyniku> after <wynik przed: before-box-on.json>
set -uo pipefail
out=${1:?katalog wyniku}
mode=${2:?before albo after}
before_json=${3:-}
repo=$(git rev-parse --show-toplevel)
head_sha=$(git -C "$repo" rev-parse HEAD)
tests="$repo/daemon/tests"
cores=$(nproc)
mkdir -p "$out"

load1() { cut -d' ' -f1 /proc/loadavg; }
valid() { awk -v l="$1" -v c="$cores" 'BEGIN { exit !(l < c / 2) }'; }

{
  echo "mode $mode"
  echo "head $head_sha"
  echo "status-clean $([ -z "$(git -C "$repo" status --porcelain)" ] && echo yes || echo no)"
  echo "nproc $cores (próg 1-min loadavg < $(awk -v c="$cores" 'BEGIN { print c / 2 }'))"
  echo "reproducer $tests/perf_scale_k98.py sha256 $(sha256sum "$tests/perf_scale_k98.py" | cut -d' ' -f1)"
  echo "reproducer-last-commit $(git -C "$repo" log -1 --format=%H -- daemon/tests/perf_scale_k98.py)"
  [ -n "$before_json" ] && echo "before $before_json"
} > "$out/run.txt"

decisive=""
for attempt in 1 2 3; do
  dir="$out/attempt-$attempt"
  mkdir -p "$dir"
  before=$(cat /proc/loadavg)
  echo "attempt-$attempt loadavg-before $before" >> "$out/run.txt"
  if [ "$mode" = before ]; then
    /usr/bin/python3 "$tests/perf_scale_k98.py" --box on --sha "$head_sha" --out "$dir/before-box-on.json" \
      > "$dir/before-box-on.log" 2>&1
    echo "attempt-$attempt before-box-on-exit $?" >> "$out/run.txt"
  else
    for box in on off; do
      /usr/bin/python3 "$tests/perf_scale_k98.py" --box "$box" --sha "$head_sha" --out "$dir/after-box-$box.json" \
        > "$dir/after-box-$box.log" 2>&1
      echo "attempt-$attempt after-box-$box-exit $?" >> "$out/run.txt"
    done
    /usr/bin/python3 "$tests/perf_board_refresh.py" --sha "$head_sha" --out "$dir/refresh-idle.json" \
      > "$dir/refresh-idle.log" 2>&1
    echo "attempt-$attempt refresh-idle-exit $?" >> "$out/run.txt"
    /usr/bin/python3 "$tests/perf_board_refresh.py" --sha "$head_sha" --busy-ms 2000 --out "$dir/refresh-busy.json" \
      > "$dir/refresh-busy.log" 2>&1
    echo "attempt-$attempt refresh-busy-exit $?" >> "$out/run.txt"
  fi
  after=$(cat /proc/loadavg)
  echo "attempt-$attempt loadavg-after $after" >> "$out/run.txt"
  if valid "${before%% *}" && valid "${after%% *}"; then
    echo "attempt-$attempt valid yes" >> "$out/run.txt"
    decisive=$attempt
    break
  fi
  echo "attempt-$attempt valid no" >> "$out/run.txt"
done
echo "decisive ${decisive:-none}" >> "$out/run.txt"

if [ -z "$decisive" ]; then
  echo "brak ważnej próby (loadavg) — pomiar do powtórzenia przez kolejkę koordynatora" | tee -a "$out/run.txt"
  exit 2
fi
dir="$out/attempt-$decisive"
if [ "$mode" = before ]; then
  /usr/bin/python3 "$tests/perf_scale_k98_eval.py" reproduce "$dir/before-box-on.json" --out "$out"
else
  /usr/bin/python3 "$tests/perf_scale_k98_eval.py" gate --before "${before_json:?wynik przed}" \
    --on "$dir/after-box-on.json" --off "$dir/after-box-off.json" \
    --refresh-idle "$dir/refresh-idle.json" --refresh-busy "$dir/refresh-busy.json" --out "$out"
fi
status=$?
echo "eval-exit $status" >> "$out/run.txt"
cat "$out/run.txt"
for log in "$dir"/*box-*.log; do
  echo "== $log"
  tail -n 12 "$log"
done
exit $status

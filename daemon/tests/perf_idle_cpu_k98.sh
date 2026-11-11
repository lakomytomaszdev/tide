#!/bin/bash
# CPU usługi na postoju (K-98 #5, PT-346): jeden przebieg kolejki koordynatora na kodzie wskazanego SHA, oba warianty
# `box.enabled` (on, off), reproduktorem `perf_idle_cpu.py` z HEAD (ten sam scenariusz przed i po zmianie). Kod SHA
# jest wypakowany `git archive` do katalogu tymczasowego (bez rejestrowania worktree) i podany przez `--src`.
# Wszystko na izolowanych danych (HOME/XDG_*/Tablica w katalogu tymczasowym, prywatna magistrala), bez instalacji.
#
# Ważność (decyzja właściciela 11.11): /proc/loadavg przed i po próbie; próba ważna tylko przy 1-min obciążeniu
# < liczba rdzeni / 2 w obu odczytach. Najwyżej 3 próby; rozstrzyga pierwsza ważna (bez wybierania najlepszej).
#
# Opcjonalnie (`refresh-gate` jako trzeci argument): po próbach regresja bramki PT-345 tym samym reproduktorem co
# w PT-342…PT-345 (`perf_board_refresh_compare.sh`, bez zmian): początek ticketu vs HEAD, bez obciążenia i przy
# zajętym workerze (`--busy-ms 2000`).
#
# Użycie: bash daemon/tests/perf_idle_cpu_k98.sh <katalog wyniku> <SHA kodu> [refresh-gate] [SHA początku ticketu]
set -uo pipefail
out=${1:?katalog wyniku}
target=${2:?SHA kodu}
gate=${3:-}
ticket_base=${4:-9c8247a80199d0641399abd3086a6acd3a26f9fc}
repo=$(git rev-parse --show-toplevel)
head_sha=$(git -C "$repo" rev-parse HEAD)
target_sha=$(git -C "$repo" rev-parse "$target^{commit}")
tests="$repo/daemon/tests"
cores=$(nproc)
mkdir -p "$out"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git -C "$repo" archive "$target_sha" daemon | tar -x -C "$tmp"

load1() { cut -d' ' -f1 /proc/loadavg; }
valid() { awk -v l="$1" -v c="$cores" 'BEGIN { exit !(l < c / 2) }'; }

{
  echo "target $target_sha"
  echo "reproducer-head $head_sha"
  echo "reproducer $tests/perf_idle_cpu.py sha256 $(sha256sum "$tests/perf_idle_cpu.py" | cut -d' ' -f1)"
  echo "status-clean $([ -z "$(git -C "$repo" status --porcelain)" ] && echo yes || echo no)"
  echo "nproc $cores (próg 1-min loadavg < $(awk -v c="$cores" 'BEGIN { print c / 2 }'))"
  echo "proc-count $(ls -d /proc/[0-9]* | wc -l)"
} > "$out/run.txt"

decisive=""
for attempt in 1 2 3; do
  dir="$out/attempt-$attempt"
  mkdir -p "$dir"
  before=$(cat /proc/loadavg)
  echo "attempt-$attempt loadavg-before $before" >> "$out/run.txt"
  for box in on off; do
    /usr/bin/python3 "$tests/perf_idle_cpu.py" --src "$tmp" --sha "$target_sha" --box "$box" \
      --out "$dir/box-$box.json" > "$dir/box-$box.log" 2>&1
    echo "attempt-$attempt box-$box-exit $?" >> "$out/run.txt"
    tail -n 1 "$dir/box-$box.log" | sed "s/^/attempt-$attempt box-$box /" >> "$out/run.txt"
  done
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

if [ "$gate" = "refresh-gate" ]; then
  bash "$tests/perf_board_refresh_compare.sh" "$out/refresh-idle" "$ticket_base" > "$out/refresh-idle.log" 2>&1
  echo "refresh-idle-exit $?" >> "$out/run.txt"
  bash "$tests/perf_board_refresh_compare.sh" "$out/refresh-busy" "$ticket_base" --busy-ms 2000 \
    > "$out/refresh-busy.log" 2>&1
  echo "refresh-busy-exit $?" >> "$out/run.txt"
fi
cat "$out/run.txt"
[ -n "$decisive" ]

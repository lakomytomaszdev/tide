#!/bin/bash
# CPU usługi na postoju w skali produkcji dzienników (K-105, PT-410): jeden przebieg kolejki koordynatora na kodzie
# wskazanego SHA reproduktorem `perf_idle_cpu.py --scale prod` z HEAD. Kod SHA jest wypakowany `git archive` do
# katalogu tymczasowego (bez rejestrowania worktree) i podany przez `--src`. Wszystko na izolowanych danych
# (HOME/XDG_*/Tablica w katalogu tymczasowym, prywatna magistrala), bez instalacji i bez danych właściciela.
#
# Ważność (reguła PT-362/PT-346): /proc/loadavg przed i po próbie; próba ważna tylko przy 1-min obciążeniu
# < liczba rdzeni / 2 w obu odczytach. Najwyżej 3 próby, jedna naraz; rozstrzyga pierwsza ważna (bez wybierania
# najlepszej). Po rozstrzygającej próbie ocena `perf_idle_scale.py evaluate` (od PT-458 jednookienkowa, bez progów
# i bez werdyktu: tylko ważność; z `--before` także porównanie z pomiarem „przed” i wzrost VmRSS). Rozstrzyga tryb
# 10 okien: `perf_k105_queue.py` (docs/tide-daemon.md, „Wydajność postoju”).
#
# Użycie: bash daemon/tests/perf_idle_scale_k105.sh <katalog wyniku> <SHA kodu> [on|off] [przed.json]
#   on|off — `box.enabled` (domyślnie on); przed.json — wynik „przed” do porównania (pomiar „po”).
set -uo pipefail
out=${1:?katalog wyniku}
target=${2:?SHA kodu}
box=${3:-on}
before_json=${4:-}
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
  echo "scale-module $tests/perf_idle_scale.py sha256 $(sha256sum "$tests/perf_idle_scale.py" | cut -d' ' -f1)"
  echo "status-clean $([ -z "$(git -C "$repo" status --porcelain)" ] && echo yes || echo no)"
  echo "box $box"
  echo "nproc $cores (próg 1-min loadavg < $(awk -v c="$cores" 'BEGIN { print c / 2 }'))"
  echo "proc-count $(ls -d /proc/[0-9]* | wc -l)"
} > "$out/run.txt"

decisive=""
for attempt in 1 2 3; do
  dir="$out/attempt-$attempt"
  mkdir -p "$dir"
  before=$(cat /proc/loadavg)
  echo "attempt-$attempt loadavg-before $before" >> "$out/run.txt"
  /usr/bin/python3 "$tests/perf_idle_cpu.py" --src "$tmp" --sha "$target_sha" --box "$box" --scale prod \
    --out "$dir/box-$box.json" > "$dir/box-$box.log" 2>&1
  echo "attempt-$attempt box-$box-exit $?" >> "$out/run.txt"
  tail -n 1 "$dir/box-$box.log" | sed "s/^/attempt-$attempt box-$box /" >> "$out/run.txt"
  after=$(cat /proc/loadavg)
  echo "attempt-$attempt loadavg-after $after" >> "$out/run.txt"
  # Ocena na wyniku próby: kod 2 = nieważna (loadavg w oknie pomiaru), 0 = ważna (bez progów, PT-458).
  evaluated=2
  if [ -f "$dir/box-$box.json" ]; then
    args=(evaluate --after "$dir/box-$box.json" --out "$dir/evaluation.json")
    [ -n "$before_json" ] && args+=(--before "$before_json")
    /usr/bin/python3 "$tests/perf_idle_scale.py" "${args[@]}" > "$dir/evaluation.txt" 2>&1
    evaluated=$?
    echo "attempt-$attempt evaluate-exit $evaluated" >> "$out/run.txt"
  fi
  if valid "${before%% *}" && valid "${after%% *}" && [ "$evaluated" -ne 2 ]; then
    echo "attempt-$attempt valid yes" >> "$out/run.txt"
    decisive=$attempt
    break
  fi
  echo "attempt-$attempt valid no" >> "$out/run.txt"
done
echo "decisive ${decisive:-none}" >> "$out/run.txt"
cat "$out/run.txt"
[ -n "$decisive" ]

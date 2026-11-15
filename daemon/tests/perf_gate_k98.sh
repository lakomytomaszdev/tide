#!/bin/bash
# Bramka czasów odpowiedzi K-98 (PT-345) na jednym scalonym kandydacie (HEAD = PT-342+PT-343+PT-344+PT-345).
# Jeden przebieg kolejki koordynatora; wszystko na izolowanych danych (reproduktory same tworzą HOME/XDG_*/Tablicę
# w katalogu tymczasowym, usługa D-Bus na prywatnej magistrali), bez instalacji i produkcyjnych dzienników.
#
# Ważność (decyzja właściciela 11.11): /proc/loadavg przed i po próbie; próba ważna tylko przy 1-min obciążeniu
# < liczba rdzeni / 2 w obu odczytach. Najwyżej 3 próby; rozstrzyga pierwsza ważna (bez wybierania najlepszej).
# Próba:
#   refresh-idle  — reproduktor PT-342 BEZ ZMIAN (perf_board_refresh_compare.sh): baza karty vs HEAD, 30 kart,
#                   30 odświeżeń, 100 żądań na metodę, bez obciążenia,
#   refresh-busy  — to samo przy zajętym workerze przepływu (scenariusz PT-343, --busy-ms 2000),
#   cold-1..5     — zimny start: 5 osobnych startów świeżej usługi (każdy compare = osobny proces bazy i HEAD),
#   first-call    — p95 pierwszego wywołania serii D-Bus (perf_dbus_first_call.py, 20 serii): początek ticketu vs HEAD.
# Po pomiarze (już poza oknem obciążenia): regresje PT-342, PT-343, PT-344, `box.enabled=false` i PT-345 na tym samym
# HEAD przez uruchamiacz projektu. Na końcu ocena progów (perf_gate_k98_eval.py) → gate.json i gate.txt.
#
# Użycie: bash daemon/tests/perf_gate_k98.sh <katalog wyniku> [SHA bazy karty] [SHA początku ticketu]
set -uo pipefail
out=${1:?katalog wyniku}
card_base=${2:-a95d7a31}
ticket_base=${3:-e2ab2fc7653eb930d80684a00eccb2994b3298f0}
repo=$(git rev-parse --show-toplevel)
head_sha=$(git -C "$repo" rev-parse HEAD)
card_base_sha=$(git -C "$repo" rev-parse "$card_base^{commit}")
ticket_base_sha=$(git -C "$repo" rev-parse "$ticket_base^{commit}")
tests="$repo/daemon/tests"
cores=$(nproc)
mkdir -p "$out"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# Kod początku ticketu dla pomiaru pierwszego wywołania (usługa jako osobny proces z --src).
git -C "$repo" archive "$ticket_base_sha" daemon tools docs theme | tar -x -C "$tmp"

load1() { cut -d' ' -f1 /proc/loadavg; }
valid() { awk -v l="$1" -v c="$cores" 'BEGIN { exit !(l < c / 2) }'; }

{
  echo "head $head_sha"
  echo "card-base $card_base_sha"
  echo "ticket-base $ticket_base_sha"
  echo "status-clean $([ -z "$(git -C "$repo" status --porcelain)" ] && echo yes || echo no)"
  echo "nproc $cores (próg 1-min loadavg < $(awk -v c="$cores" 'BEGIN { print c / 2 }'))"
  echo "reproducer $tests/perf_board_refresh.py sha256 $(sha256sum "$tests/perf_board_refresh.py" | cut -d' ' -f1)"
  echo "reproducer-last-commit $(git -C "$repo" log -1 --format=%H -- daemon/tests/perf_board_refresh.py daemon/tests/perf_board_refresh_compare.sh)"
} > "$out/run.txt"

decisive=""
for attempt in 1 2 3; do
  dir="$out/attempt-$attempt"
  mkdir -p "$dir"
  before=$(cat /proc/loadavg)
  echo "attempt-$attempt loadavg-before $before" >> "$out/run.txt"
  bash "$tests/perf_board_refresh_compare.sh" "$dir/refresh-idle" "$card_base_sha" > "$dir/refresh-idle.log" 2>&1
  echo "attempt-$attempt refresh-idle-exit $?" >> "$out/run.txt"
  bash "$tests/perf_board_refresh_compare.sh" "$dir/refresh-busy" "$card_base_sha" --busy-ms 2000 \
    > "$dir/refresh-busy.log" 2>&1
  echo "attempt-$attempt refresh-busy-exit $?" >> "$out/run.txt"
  for i in 1 2 3 4 5; do
    bash "$tests/perf_board_refresh_compare.sh" "$dir/cold-$i" "$card_base_sha" --refreshes 1 --requests 1 \
      > "$dir/cold-$i.log" 2>&1
    echo "attempt-$attempt cold-$i-exit $?" >> "$out/run.txt"
  done
  /usr/bin/python3 "$tests/perf_dbus_first_call.py" --src "$tmp" --sha "$ticket_base_sha" \
    --out "$dir/first-call-base.json" > "$dir/first-call-base.log" 2>&1
  echo "attempt-$attempt first-call-base-exit $?" >> "$out/run.txt"
  /usr/bin/python3 "$tests/perf_dbus_first_call.py" --src "$repo" --sha "$head_sha" \
    --out "$dir/first-call-head.json" > "$dir/first-call-head.log" 2>&1
  echo "attempt-$attempt first-call-head-exit $?" >> "$out/run.txt"
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

# Regresje na tym samym kandydacie (po pomiarze).
modules=(daemon/tests/test_cardflow_refresh_context.py daemon/tests/test_cardflow_refresh_cost.py
         daemon/tests/test_perf_board_refresh.py daemon/tests/test_cardflow_busy_reads.py
         daemon/tests/test_cardflow_tick_reads.py daemon/tests/test_perf_tick_recovery.py
         daemon/tests/test_cardflow_integration_k38.py daemon/tests/test_monitor_probe.py
         daemon/tests/test_dbus_contract.py daemon/tests/test_attention.py)
(cd "$repo" && /usr/bin/python3 tools/test_runner.py --output "$out/regressions" --modules "${modules[@]}") \
  > "$out/regressions.log" 2>&1
echo "regressions-exit $?" >> "$out/run.txt"
echo "regressions-modules ${modules[*]}" >> "$out/run.txt"

/usr/bin/python3 "$tests/perf_gate_k98_eval.py" "$out" ${decisive:+--attempt "$decisive"}
status=$?
echo "gate-exit $status" >> "$out/run.txt"
cat "$out/run.txt"
exit $status

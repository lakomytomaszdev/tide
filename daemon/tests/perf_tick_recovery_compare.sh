#!/bin/bash
# Porównanie odzyskiwania i kosztu odczytów ticku (K-98 #3, PT-344): ten sam reproduktor (z HEAD, bez zmian) na kodzie
# bazy i na HEAD, każdy w osobnym procesie, na izolowanych danych. Kod bazy jest wypakowany `git archive` do katalogu
# tymczasowego (bez rejestrowania worktree) i podany reproduktorowi przez `--src`; reproduktor i atrapy pochodzą
# z HEAD, więc obie strony mierzą ten sam scenariusz.
#
# Użycie: bash daemon/tests/perf_tick_recovery_compare.sh <katalog wyniku> <SHA bazy> [argumenty reproduktora…]
# Wynik: base.json, head.json (surowe liczniki i czasy), loadavg przed/między/po, SHA i polecenia w run.txt.
set -euo pipefail
out=${1:?katalog wyniku}
base=${2:?SHA bazy}
shift 2
repo=$(git rev-parse --show-toplevel)
base_sha=$(git -C "$repo" rev-parse "$base^{commit}")
head_sha=$(git -C "$repo" rev-parse HEAD)
script="$repo/daemon/tests/perf_tick_recovery.py"
mkdir -p "$out"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
# `tools/` jest potrzebne pakietowi testów (`daemon/tests/__init__.py` → `tools.test_isolation`), a `docs/` —
# zasobom usługi (standard planu przy starcie karty), szukanym obok kodu z `--src`.
git -C "$repo" archive "$base_sha" daemon tools docs | tar -x -C "$tmp"
# Reproduktor importuje fixture z pakietu testów `--src`; w bazie musi to być ten sam plik co w HEAD.
cp "$script" "$tmp/daemon/tests/perf_tick_recovery.py"
{
  echo "base $base_sha"
  echo "head $head_sha"
  echo "reproducer $script @ $head_sha (sha256 $(sha256sum "$script" | cut -d' ' -f1))"
  echo "status-clean $([ -z "$(git -C "$repo" status --porcelain)" ] && echo yes || echo no)"
  echo "nproc $(nproc)"
  echo "loadavg-start $(cat /proc/loadavg)"
  echo "args $*"
} > "$out/run.txt"
set +e
/usr/bin/python3 "$tmp/daemon/tests/perf_tick_recovery.py" --src "$tmp" --sha "$base_sha" --out "$out/base.json" "$@" \
  > "$out/base.txt" 2> "$out/base.err"
base_exit=$?
echo "base-exit $base_exit" >> "$out/run.txt"
echo "loadavg-between $(cat /proc/loadavg)" >> "$out/run.txt"
/usr/bin/python3 "$script" --src "$repo" --sha "$head_sha" --out "$out/head.json" "$@" \
  > "$out/head.txt" 2> "$out/head.err"
head_exit=$?
echo "head-exit $head_exit" >> "$out/run.txt"
echo "loadavg-end $(cat /proc/loadavg)" >> "$out/run.txt"
cat "$out/base.txt" "$out/head.txt"
# Porażka reproduktora (wyjątek albo nieudane sprawdzenia odzyskiwania) jest widoczna w kodzie wyjścia.
if [ "$base_exit" -ne 0 ] || [ "$head_exit" -ne 0 ]; then
  tail -n 20 "$out/base.err" "$out/head.err" >&2
  exit 1
fi

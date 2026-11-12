#!/bin/bash
# Porównanie kosztu odświeżenia Tablicy (K-98): ten sam reproduktor (z HEAD, bez zmian) na kodzie bazy i na HEAD,
# każdy w osobnym procesie (zimny start osobno), na izolowanych danych. Kod bazy jest wypakowany `git archive`
# do katalogu tymczasowego (bez rejestrowania worktree) i podany reproduktorowi przez `--src`; sam reproduktor
# i atrapy (`fake_incus.py`) pochodzą z HEAD, więc obie strony mierzą ten sam scenariusz i ten sam zestaw metod.
#
# Użycie: bash daemon/tests/perf_board_refresh_compare.sh <katalog wyniku> <SHA bazy> [argumenty reproduktora…]
# Wynik: base.json, head.json (surowe próbki), loadavg przed/po, SHA i polecenia w run.txt.
set -euo pipefail
out=${1:?katalog wyniku}
base=${2:?SHA bazy}
shift 2
repo=$(git rev-parse --show-toplevel)
base_sha=$(git -C "$repo" rev-parse "$base^{commit}")
head_sha=$(git -C "$repo" rev-parse HEAD)
script="$repo/daemon/tests/perf_board_refresh.py"
mkdir -p "$out"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
git -C "$repo" archive "$base_sha" daemon | tar -x -C "$tmp"
{
  echo "base $base_sha"
  echo "head $head_sha"
  echo "reproducer $script @ $head_sha (sha256 $(sha256sum "$script" | cut -d' ' -f1))"
  echo "status-clean $([ -z "$(git -C "$repo" status --porcelain)" ] && echo yes || echo no)"
  echo "nproc $(nproc)"
  echo "loadavg-start $(cat /proc/loadavg)"
  echo "args $*"
} > "$out/run.txt"
/usr/bin/python3 "$script" --src "$tmp" --sha "$base_sha" --out "$out/base.json" "$@" | tee "$out/base.txt"
echo "loadavg-between $(cat /proc/loadavg)" >> "$out/run.txt"
/usr/bin/python3 "$script" --src "$repo" --sha "$head_sha" --out "$out/head.json" "$@" | tee "$out/head.txt"
echo "loadavg-end $(cat /proc/loadavg)" >> "$out/run.txt"

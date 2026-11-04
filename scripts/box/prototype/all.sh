#!/usr/bin/env bash
# Wszystkie próby PT-164 po kolei: p5 → p1 → p4 → p9 → measure. Każda pisze własny log w docs/box-prototype/;
# niepowodzenie jednej próby nie zatrzymuje następnych. Użycie: bash scripts/box/prototype/all.sh
# (sudo zapyta o hasło tylko w chwilowych wariantach projektu Incusa: intercept w p5/p1, migawki w measure).
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
codes=()
for name in p5 p1 p4 p9 measure; do
    rc=0
    bash "$here/$name.sh" </dev/null || rc=$?
    codes+=("$name=$rc")
done
echo
echo "== Próby PT-164: ${codes[*]} (0 — wszystko działa, 1 — coś nie działa, 2 — warunki; szczegóły w logach)"
for c in "${codes[@]}"; do
    [[ ${c#*=} == 0 ]] || exit 1
done

#!/usr/bin/env bash
# Wszystkie próby PT-164 po kolei: p5 → p1 → p4 → p9 → measure. Każda pisze własny log w docs/box-prototype/;
# niepowodzenie jednej próby nie zatrzymuje następnych.
# Użycie: bash scripts/box/prototype/all.sh [--install]
#   --install — najpierw sudo bash scripts/box/install.sh --yes (runda 3: root:<uid>:1 w /etc/subuid i /etc/subgid
#               dla raw.idmap, restart incus; wyjście w docs/box-prototype/install.log). Przerwanie, gdy instalacja
#               padnie. sudo zapyta o hasło (terminal albo askpass).
# Poza tym sudo pyta tylko w chwilowych wariantach projektu Incusa (intercept w p5, migawki w measure).
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
install=0
case "${1:-}" in
    --install) install=1 ;;
    "") ;;
    *) echo "Nieznany argument: $1 (dozwolone: --install)" >&2; exit 2 ;;
esac

if [[ $install -eq 1 ]]; then
    mkdir -p "$repo/docs/box-prototype"
    log="$repo/docs/box-prototype/install.log"
    sudo_cmd=(sudo)
    if ! (: </dev/tty) 2>/dev/null && [[ -x /usr/bin/ksshaskpass ]]; then
        export SUDO_ASKPASS=${SUDO_ASKPASS:-/usr/bin/ksshaskpass}
        sudo_cmd=(sudo -A)
    fi
    echo "== sudo bash scripts/box/install.sh --yes  (log: docs/box-prototype/install.log)"
    rc=0
    { echo "# make box-install przed rundą 3 PT-164 — $(date -Iseconds)"; echo; } >"$log"
    "${sudo_cmd[@]}" bash "$repo/scripts/box/install.sh" --yes </dev/null >>"$log" 2>&1 || rc=$?
    echo "kod wyjścia: $rc" >>"$log"
    tail -n 20 "$log"
    if [[ $rc -ne 0 ]]; then
        echo "Instalacja nie przeszła (kod $rc) — próby nie ruszają. Log: docs/box-prototype/install.log" >&2
        exit 2
    fi
fi

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

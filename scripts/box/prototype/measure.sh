#!/usr/bin/env bash
# Pomiary pudełka (PT-164, progi US-4): narzut kroku (incus exec + systemd-run --user --scope dla true) ≤ 3 s,
# zimny start zatrzymanego kontenera ≤ 30 s, incus snapshot restore (reset) ≤ 60 s. Każdy pomiar trzy razy,
# mediana w wyniku. Przekroczenie progu jest opisane, nie blokuje (blokuje dopiero #24).
# Użycie: bash scripts/box/prototype/measure.sh   (po p9.sh — narzut wymaga lingering agenta; sudo tylko, gdy
# projekt blokuje migawki — wtedy chwilowe restricted.snapshots=allow, cofane na końcu)
#
# Migawka proto-measure powstaje z bieżącego stanu kontenera i jest usuwana na końcu, więc reset niczego
# nie gubi. Start i reset liczone do chwili, gdy systemd w kontenerze skończył start.
set -euo pipefail
# shellcheck source=scripts/box/prototype/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SNAP=proto-measure
log_start measure "Pomiary — narzut kroku, zimny start, reset z migawki"
require_incus
box_exists || die "brak kontenera $BOX — najpierw p1.sh/p4.sh/p5.sh/p9.sh"
[[ $(box_status) == RUNNING ]] || run "start kontenera" -- incus start "$BOX"
run "czekanie na systemd" -- systemd_ready

median() { printf '%s\n' "$@" | sort -n | sed -n "$((($# + 1) / 2))p"; }

verdict() {  # verdict <klucz> <opis> <próg ms> <czasy ms…>
    local key=$1 desc=$2 limit=$3 med all=""
    shift 3
    for t in "$@"; do all+="$(fmt_ms "$t") "; done
    if [[ $# -lt 3 ]]; then
        result "$key" fail "$desc: tylko $# udanych pomiarów ($all)"
        return 0
    fi
    med=$(median "$@")
    if ((med <= limit)); then
        result "$key" ok "$desc: mediana $(fmt_ms "$med") s (próg $(fmt_ms "$limit") s; pomiary: $all)"
    else
        result "$key" fail "$desc: mediana $(fmt_ms "$med") s PRZEKRACZA próg $(fmt_ms "$limit") s (pomiary: $all)"
    fi
}

ready() {  # incus start/restore zwraca wcześniej niż koniec startu — liczymy do systemctl is-system-running
    systemd_ready
}

# 1. Narzut kroku.
times=()
for i in 1 2 3; do
    run "narzut kroku #$i: incus exec + systemd-run --user --scope true" -- as_agent systemd-run --user --scope --quiet true
    ok && times+=("$LAST_MS")
done
verdict narzut-kroku "incus exec + systemd-run --scope dla true" 3000 "${times[@]}"
times=()
for i in 1 2 3; do  # wariant z P-9: usługa --user (proces przeżywa śmierć klienta incus exec)
    run "narzut kroku (usługa) #$i: incus exec + systemd-run --user --wait true" -- as_agent systemd-run --user \
        --quiet --collect --wait true
    ok && times+=("$LAST_MS")
done
verdict narzut-kroku-usluga "incus exec + systemd-run --user --wait (usługa) dla true" 3000 "${times[@]}"

# 2. Zimny start zatrzymanego kontenera.
start_ready() { incus start "$BOX" && ready; }
times=()
for i in 1 2 3; do
    run "zatrzymanie #$i" -- incus stop "$BOX"
    run "zimny start #$i (incus start + koniec startu systemd)" -- start_ready
    ok && times+=("$LAST_MS")
done
verdict zimny-start "start zatrzymanego kontenera" 30000 "${times[@]}"

# 3. Reset z migawki.
incus snapshot delete "$BOX" "$SNAP" </dev/null >/dev/null 2>&1 || true
run "migawka $SNAP z bieżącego stanu" -- incus snapshot create "$BOX" "$SNAP"
if ! ok; then
    # Projekt ograniczony z PT-166 ma domyślne restricted.snapshots=block (runda 1: „doesn't allow for snapshot
    # creation”). Wariant: chwilowo allow (sudo, cofane po usunięciu migawki) — pomiar resetu i tak potrzebny.
    result migawki-w-projekcie fail "projekt $PROJECT nie pozwala na migawki: $(tail -n 1 <<<"$LAST_OUT")"
    project_allow restricted.snapshots allow || die "nie da się chwilowo zezwolić na migawki"
    run "migawka $SNAP (restricted.snapshots=allow)" -- incus snapshot create "$BOX" "$SNAP"
    ok || die "nie da się utworzyć migawki także po restricted.snapshots=allow"
fi
drop_snapshot() { run "usunięcie migawki $SNAP" -- incus snapshot delete "$BOX" "$SNAP"; }
at_exit drop_snapshot
restore_ready() {
    incus snapshot restore "$BOX" "$SNAP" || return
    [[ $(box_status) == RUNNING ]] || incus start "$BOX" || return
    ready
}
times=()
for i in 1 2 3; do
    run "reset #$i (incus snapshot restore + koniec startu)" -- restore_ready
    ok && times+=("$LAST_MS")
done
verdict reset "incus snapshot restore" 60000 "${times[@]}"
finish

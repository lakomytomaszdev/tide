#!/usr/bin/env bash
# Sprzątanie po próbach prototypu pudełka (PT-164 i PT-178). Dzienniki w docs/box-prototype/ zostają.
#   - proxy prototypowe: proxy.py z PT-178 (pid z $XDG_RUNTIME_DIR/tide-box-prototype/proxy.pid i pozostałe procesy)
#     oraz pozostałe chwilowe proxy-plain.py z PT-164;
#   - kontenery prób: tide-proto (PT-164), tide-proto-net i tide-proto-p7 (PT-178) — chyba że --keep-box;
#   - worktree i gałąź proto/k-38 z P-4 (PT-164) w projekcie node;
#   - reguły tide-proto w inet tide-box (PT-164, tryb PROTO_NET=rules) — tylko z --rules: odczyt tabeli wymaga sudo,
#     a w domyślnym trybie proxy reguł nie ma (p1.sh usuwa je w pułapce EXIT także po błędzie).
# Przy wyniku „done” PT-164 kontener tide-proto zostaje dla kolejnych ticketów — wtedy --keep-box.
# Użycie: bash scripts/box/prototype/cleanup.sh [--keep-box] [--rules]   (sudo — tylko z --rules)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf '%s\n' "Użycie: bash scripts/box/prototype/cleanup.sh [--keep-box] [--rules] — usuwa proxy prototypowe," \
        "kontenery prób (tide-proto, tide-proto-net, tide-proto-p7), worktree proto-k-38; --rules: reguły tide-proto (sudo)"
    exit 0
fi
set -euo pipefail
keep=0
rules=0
for arg in "$@"; do
    case $arg in
        --keep-box) keep=1 ;;
        --rules) rules=1 ;;
        *) echo "Nieznany argument: $arg (dozwolone: --keep-box, --rules)" >&2; exit 2 ;;
    esac
done
# shellcheck source=scripts/box/prototype/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

proj=${PROTO_NODE_PROJECT:-$HOME/DevWorkspace/toolssuite}
state=${XDG_RUNTIME_DIR:-/tmp}/tide-box-prototype
log_start cleanup "Sprzątanie po próbach PT-164 i PT-178"

# SIGTERM, do 5 s czekania, potem SIGKILL — zawieszone proxy nie może trzymać portu 18080 (review PT-178 r2, M-1).
stop_pid() {
    local pid=$1 i
    kill "$pid" 2>/dev/null || return 0
    for (( i = 0; i < 50; i++ )); do
        kill -0 "$pid" 2>/dev/null || return 0
        sleep 0.1
    done
    note "proxy $pid nie skończyło się w 5 s po SIGTERM — SIGKILL"
    kill -9 "$pid" 2>/dev/null || true
}

stopped=0
if [[ -f $state/proxy.pid ]]; then
    pid=$(cat "$state/proxy.pid")
    if kill -0 "$pid" 2>/dev/null && grep -q 'prototype/proxy.py' "/proc/$pid/cmdline" 2>/dev/null; then
        stop_pid "$pid"
        note "zatrzymano proxy prototypowe (pid $pid)"
        stopped=$((stopped + 1))
    fi
    rm -f "$state/proxy.pid"
fi
for pid in $(pgrep -f 'scripts/box/prototype/proxy(-plain)?\.py' || true); do
    note "zatrzymuję pozostałe proxy prototypowe: $pid"
    stop_pid "$pid"
    stopped=$((stopped + 1))
done
rmdir "$state" 2>/dev/null || true
result proxy ok "zatrzymanych proxy prototypowych: $stopped"

if [[ $keep -eq 1 ]]; then
    result kontenery skip "--keep-box: kontenery prób zostają"
else
    require_incus
    for box in "$BOX" "${PROTO_BOX:-tide-proto-net}" tide-proto-p7; do
        if incus info "$box" >/dev/null 2>&1 </dev/null; then
            run "usunięcie kontenera $box" -- incus delete --force "$box"
            ok && result "kontener-$box" ok "kontener $box usunięty" || result "kontener-$box" fail "incus delete: kod $LAST_RC"
        else
            result "kontener-$box" ok "kontenera $box nie ma"
        fi
    done
    run "kontenery po sprzątaniu" -- incus list --format csv -c n
fi

if [[ -d $proj/.git ]]; then
    if [[ -e $proj-worktrees/proto-k-38 ]]; then
        run "usunięcie worktree proto-k-38" -- git -C "$proj" worktree remove --force "$proj-worktrees/proto-k-38"
    fi
    if git -C "$proj" rev-parse --verify --quiet refs/heads/proto/k-38 >/dev/null; then
        run "usunięcie gałęzi proto/k-38" -- git -C "$proj" branch -D proto/k-38
    fi
    run "worktree projektu po sprzątaniu" -- git -C "$proj" worktree list
fi

if [[ $rules -eq 1 ]]; then
    sudo_prime
    run "tabela inet tide-box" -- as_root nft -a list table inet tide-box
    listing=$LAST_OUT
    left=0
    while read -r chain handle; do
        [[ -n $chain ]] || continue
        left=$((left + 1))
        run "usunięcie pozostałej reguły tide-proto ($chain, uchwyt $handle)" -- as_root nft delete rule inet tide-box "$chain" handle "$handle"
    done < <(proto_rules "$listing")
    result reguly-tide-proto ok "pozostałych reguł tide-proto: $left (usunięte)"
else
    result reguly-tide-proto skip "bez --rules: tryb proxy nie dodaje reguł, p1.sh w trybie rules usuwa je w pułapce EXIT"
fi
finish

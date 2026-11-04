#!/usr/bin/env bash
# Sprzątanie po próbach PT-164: usuwa kontener tide-proto i pozostałości (reguły tide-proto w inet tide-box,
# worktree i gałąź proto/k-38 z P-4). Uruchamiane, gdy wynik ticketu nie jest „done” — przy „done” kontener
# zostaje dla kolejnych ticketów. Użycie: bash scripts/box/prototype/cleanup.sh   (sudo — tylko gdy zostały reguły)
set -euo pipefail
# shellcheck source=scripts/box/prototype/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

proj=${PROTO_NODE_PROJECT:-$HOME/DevWorkspace/toolssuite}
log_start cleanup "Sprzątanie po próbach PT-164"
require_incus
if box_exists; then
    run "usunięcie kontenera $BOX" -- incus delete --force "$BOX"
    ok && result kontener ok "kontener $BOX usunięty" || result kontener fail "incus delete: kod $LAST_RC"
else
    result kontener ok "kontenera $BOX nie ma"
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
finish

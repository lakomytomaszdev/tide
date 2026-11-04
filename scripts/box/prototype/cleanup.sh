#!/usr/bin/env bash
# Sprzątanie po próbach PT-178: zatrzymuje prototypowe proxy (pid z $XDG_RUNTIME_DIR/tide-box-prototype/proxy.pid)
# i usuwa kontenery prób (tide-proto-net, tide-proto-p7). Dzienniki w docs/box-prototype/ zostają.
# Użycie: bash scripts/box/prototype/cleanup.sh [--keep-box]   (--keep-box: tylko proxy, pudełko zostaje)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf '%s\n' "Użycie: bash scripts/box/prototype/cleanup.sh [--keep-box] — usuwa proxy prototypowe i kontenery prób"
    exit 0
fi
set -Eeuo pipefail
state=${XDG_RUNTIME_DIR:-/tmp}/tide-box-prototype
keep=0
[[ ${1:-} == --keep-box ]] && keep=1

if [[ -f $state/proxy.pid ]]; then
    pid=$(cat "$state/proxy.pid")
    if kill -0 "$pid" 2>/dev/null && grep -q 'prototype/proxy.py' "/proc/$pid/cmdline" 2>/dev/null; then
        kill "$pid"
        echo "zatrzymano proxy prototypowe (pid $pid)"
    fi
    rm -f "$state/proxy.pid"
fi
leftover=$(pgrep -f 'scripts/box/prototype/proxy.py' || true)
[[ -n $leftover ]] && { echo "zatrzymuję pozostałe proxy prototypowe: $leftover"; kill $leftover || true; }

if [[ $keep -eq 0 ]]; then
    for box in ${PROTO_BOX:-tide-proto-net} tide-proto-p7; do
        if incus info "$box" >/dev/null 2>&1; then
            incus delete --force "$box"
            echo "usunięto kontener $box"
        fi
    done
fi
rmdir "$state" 2>/dev/null || true
echo "gotowe"

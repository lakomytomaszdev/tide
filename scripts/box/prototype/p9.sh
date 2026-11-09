#!/usr/bin/env bash
# P-9 (PT-164): systemd --user użytkownika agent w kontenerze (lingering) i systemd-run przez incus exec; proces
# kroku ma przeżyć śmierć klienta incus exec na hoście i restart tide-daemon.
# Użycie: bash scripts/box/prototype/p9.sh   (bez sudo)
#
# Jak w BoxRunner (#6): usługa na hoście uruchamia incus exec, a w kontenerze systemd-run. Śmierć usługi emulujemy
# jednostką hosta tide-proto-client (systemd-run --user), w której biegnie klient incus exec — systemctl --user
# stop zabija ją tak, jak restart zabija grupę tide-daemon. Trzy warianty w kontenerze (każdy sprawdzany):
#   scope        — systemd-run --user --scope sleep 30 (projekt #6; runda 1: proces ginie razem z klientem,
#                  bo scope nie zmienia rodzica — sleep to proces wykonany przez incus exec),
#   usługa-user  — systemd-run --user --unit=… --collect --wait sleep 30 (proces dzieckiem systemd --user agenta;
#                  klient tylko czeka; kod wyjścia przez --wait albo ExecMainStatus jednostki),
#   usługa-root  — systemd-run --uid=agent … --wait jako root pudełka (wariant awaryjny z ticketu).
# Potem systemctl --user restart tide-daemon (gdy jednostka jest), jak w kryterium ticketu — klient incus exec
# biegnie wtedy z tego skryptu, nie z tide-daemon, więc ten test pokazuje tylko, że restart niczego nie zabija
# w kontenerze; właściwą emulacją śmierci usługi jest zatrzymanie tide-proto-client.
set -euo pipefail
# shellcheck source=scripts/box/prototype/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log_start p9 "P-9 — systemd --user agenta i systemd-run przez incus exec"
require_incus
ensure_box

run "lingering użytkownika agent" -- in_box loginctl enable-linger agent
run "czekanie na systemd --user agenta (user@$AGENT_UID)" -- in_box bash -c "
    for i in \$(seq 60); do [ -S /run/user/$AGENT_UID/bus ] && systemctl is-active user@$AGENT_UID.service && exit 0; sleep 1; done
    systemctl status user@$AGENT_UID.service --no-pager; exit 1"
ok && result lingering ok "systemd --user agenta działa (lingering)" || result lingering fail "user@$AGENT_UID nie wstał"

run "systemd-run --user --scope true przez incus exec" -- as_agent systemd-run --user --scope --quiet true
ok && result scope ok "systemd-run --user --scope przez incus exec --user $AGENT_UID działa" \
    || result scope fail "systemd-run --user --scope: kod $LAST_RC"
run "systemd-run --user --wait true przez incus exec (usługa)" -- as_agent systemd-run --user --quiet --collect --wait true
ok && result usluga-user ok "systemd-run --user --wait (usługa) przez incus exec działa" \
    || result usluga-user fail "systemd-run --user --wait: kod $LAST_RC"

alive() {  # alive <opis> — czy sleep agenta żyje w kontenerze
    run "$1" -- in_box pgrep -u "$AGENT_UID" -a -x sleep
    ok
}
kill_sleeps() { run "sprzątanie: sleep agenta w kontenerze" -- in_box pkill -u "$AGENT_UID" -x sleep; }
stop_client() { run "sprzątanie: jednostka hosta tide-proto-client" -- systemctl --user stop tide-proto-client; }
at_exit kill_sleeps
at_exit stop_client

client_cmd() {  # client_cmd <wariant> <jednostka> — klient incus exec uruchamiający sleep 30 w kontenerze
    case "$1" in
        scope) CLIENT=(incus exec "$BOX" -T "${AGENT_OPTS[@]}" -- systemd-run --user --scope "--unit=$2" sleep 30) ;;
        usługa-user) CLIENT=(incus exec "$BOX" -T "${AGENT_OPTS[@]}" -- systemd-run --user "--unit=$2" --collect
                             --wait sleep 30) ;;
        usługa-root) CLIENT=(incus exec "$BOX" -T -- systemd-run "--uid=$AGENT_UID" "--gid=$AGENT_UID" "--unit=$2"
                             --collect --wait sleep 30) ;;
    esac
}

# 1. Klient incus exec w jednostce hosta, potem jej zatrzymanie (jak śmierć tide-daemon) — każdy wariant.
survivors=()
n=0
for variant in scope usługa-user usługa-root; do
    n=$((n + 1))
    client_cmd "$variant" "tide-proto-sleep-$n"
    run "[$variant] klient incus exec w jednostce hosta tide-proto-client" -- systemd-run --user \
        --unit=tide-proto-client --collect --quiet -- "${CLIENT[@]}"
    sleep "$SETTLE"
    alive "[$variant] sleep w kontenerze przed zatrzymaniem klienta" && before=1 || before=0
    run "[$variant] zatrzymanie klienta na hoście" -- systemctl --user stop tide-proto-client
    sleep "$SETTLE"
    alive "[$variant] sleep w kontenerze po zatrzymaniu klienta" && after=1 || after=0
    if [[ $before -eq 1 && $after -eq 1 ]]; then
        result "przezywa-klienta-$variant" ok "proces ($variant) przeżył śmierć klienta incus exec na hoście"
        survivors+=("$variant")
    else
        result "przezywa-klienta-$variant" fail "przed: $before, po zatrzymaniu klienta: $after ($variant)"
    fi
    kill_sleeps
done
if [[ ${#survivors[@]} -gt 0 ]]; then
    result przezywa-klienta ok "śmierć klienta incus exec przeżywa: ${survivors[*]}"
else
    result przezywa-klienta fail "żaden wariant nie przeżył śmierci klienta incus exec"
fi

# 2. Restart tide-daemon na hoście (kryterium ticketu) — wariant scope z ticketu.
if systemctl --user cat tide-daemon.service >/dev/null 2>&1 </dev/null; then
    client_cmd scope tide-proto-sleep-restart
    "${CLIENT[@]}" </dev/null >/dev/null 2>&1 &
    client=$!
    at_exit "kill $client 2>/dev/null"
    sleep "$SETTLE"
    alive "sleep w kontenerze przed restartem tide-daemon" && before=1 || before=0
    run "restart tide-daemon na hoście" -- systemctl --user restart tide-daemon
    sleep "$SETTLE"
    alive "sleep w kontenerze po restarcie tide-daemon" && after=1 || after=0
    if [[ $before -eq 1 && $after -eq 1 ]]; then
        result przezywa-restart-daemona ok "proces (scope) żyje po systemctl --user restart tide-daemon"
    else
        result przezywa-restart-daemona fail "przed: $before, po restarcie: $after (scope)"
    fi
else
    result przezywa-restart-daemona skip "brak jednostki tide-daemon na hoście"
fi
finish

#!/usr/bin/env bash
# P-9 (PT-164): systemd --user użytkownika agent w kontenerze (lingering) i systemd-run --user --scope przez
# incus exec; proces w scope przeżywa śmierć klienta incus exec na hoście i restart tide-daemon.
# Użycie: bash scripts/box/prototype/p9.sh   (bez sudo)
#
# Jak w BoxRunner (#6): usługa na hoście uruchamia incus exec, a w kontenerze systemd-run --user --scope.
# Śmierć usługi emulujemy jednostką hosta tide-proto-client (systemd-run --user), w której biegnie klient
# incus exec — systemctl --user stop zabija ją tak, jak restart zabija grupę tide-daemon. Potem osobno
# systemctl --user restart tide-daemon (gdy jednostka jest), jak w kryterium ticketu.
# Wariant awaryjny, gdy scope nie działa: systemd-run jako root pudełka z --uid=agent (usługa zamiast scope).
set -euo pipefail
# shellcheck source=scripts/box/prototype/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log_start p9 "P-9 — systemd --user agenta i systemd-run --user --scope przez incus exec"
require_incus
ensure_box

run "lingering użytkownika agent" -- in_box loginctl enable-linger agent
run "czekanie na systemd --user agenta (user@$AGENT_UID)" -- in_box bash -c "
    for i in \$(seq 60); do [ -S /run/user/$AGENT_UID/bus ] && systemctl is-active user@$AGENT_UID.service && exit 0; sleep 1; done
    systemctl status user@$AGENT_UID.service --no-pager; exit 1"
ok && result lingering ok "systemd --user agenta działa (lingering)" || result lingering fail "user@$AGENT_UID nie wstał"

run "systemd-run --user --scope true przez incus exec" -- as_agent systemd-run --user --scope --quiet true
scope_rc=$LAST_RC
ok && result scope ok "systemd-run --user --scope przez incus exec --user $AGENT_UID działa" \
    || result scope fail "systemd-run --user --scope: kod $LAST_RC"

alive() {  # alive <opis> — ile procesów sleep agenta żyje w kontenerze
    run "$1" -- in_box pgrep -u "$AGENT_UID" -a -x sleep
    ok
}
kill_sleeps() { run "sprzątanie: sleep agenta w kontenerze" -- in_box pkill -u "$AGENT_UID" -x sleep; }
at_exit kill_sleeps

mode=scope
[[ $scope_rc -eq 0 ]] || mode=usługa
[[ $mode == scope ]] || note "Wariant awaryjny: systemd-run jako root pudełka z --uid (usługa zamiast scope)."
client_cmd() {  # client_cmd <jednostka> — klient incus exec, który z hosta uruchamia sleep 30 w kontenerze
    if [[ $mode == scope ]]; then
        CLIENT=(incus exec "$BOX" -T "${AGENT_OPTS[@]}" -- systemd-run --user --scope "--unit=$1" sleep 30)
    else
        CLIENT=(incus exec "$BOX" -T -- systemd-run "--uid=$AGENT_UID" "--gid=$AGENT_UID" "--unit=$1"
                --collect --wait sleep 30)
    fi
}

# 1. Klient incus exec w jednostce hosta, potem jej zatrzymanie (jak śmierć tide-daemon).
client_cmd tide-proto-sleep-1
run "klient incus exec w jednostce hosta tide-proto-client" -- systemd-run --user --unit=tide-proto-client --collect \
    --quiet -- "${CLIENT[@]}"
stop_client() { run "sprzątanie: jednostka hosta tide-proto-client" -- systemctl --user stop tide-proto-client; }
at_exit stop_client
sleep "$SETTLE"
alive "sleep w kontenerze przed zatrzymaniem klienta" && before=1 || before=0
run "zatrzymanie klienta na hoście (systemctl --user stop tide-proto-client)" -- systemctl --user stop tide-proto-client
sleep "$SETTLE"
alive "sleep w kontenerze po zatrzymaniu klienta" && after=1 || after=0
if [[ $before -eq 1 && $after -eq 1 ]]; then
    result przezywa-klienta ok "proces w $mode przeżył śmierć klienta incus exec na hoście"
else
    result przezywa-klienta fail "przed: $before, po zatrzymaniu klienta: $after ($mode)"
fi
kill_sleeps

# 2. Restart tide-daemon na hoście (kryterium ticketu).
if systemctl --user cat tide-daemon.service >/dev/null 2>&1 </dev/null; then
    client_cmd tide-proto-sleep-2
    "${CLIENT[@]}" </dev/null >/dev/null 2>&1 &
    client=$!
    at_exit "kill $client 2>/dev/null"
    sleep "$SETTLE"
    alive "sleep w kontenerze przed restartem tide-daemon" && before=1 || before=0
    run "restart tide-daemon na hoście" -- systemctl --user restart tide-daemon
    sleep "$SETTLE"
    alive "sleep w kontenerze po restarcie tide-daemon" && after=1 || after=0
    if [[ $before -eq 1 && $after -eq 1 ]]; then
        result przezywa-restart-daemona ok "proces w $mode żyje po systemctl --user restart tide-daemon"
    else
        result przezywa-restart-daemona fail "przed: $before, po restarcie: $after ($mode)"
    fi
else
    result przezywa-restart-daemona skip "brak jednostki tide-daemon na hoście"
fi
finish

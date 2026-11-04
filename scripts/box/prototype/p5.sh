#!/usr/bin/env bash
# P-5 (PT-164): projekt ograniczony Incusa (incus-user, grupa incus) — nesting, intercept mknod/setxattr
# i filtrowanie NIC (security.mac_filtering, security.ipv4_filtering, security.ipv6_filtering na eth0 pudełka).
# Użycie: bash scripts/box/prototype/p5.sh   (sudo zapyta o hasło — nftables dla apt, wariant intercept)
#
# Podszycie: na hoście słucha mały serwer TCP na adresie mostka i porcie proxy Tide (jedyny port, który tabela
# inet tide-box przepuszcza z mostka) i zapisuje adres źródłowy każdego połączenia. Z kontenera:
#   kontrola A — nc z własnego adresu (musi dojść),
#   kontrola B — przed filtrowaniem: ip addr add <obcy adres>/24 dev eth0; nc -s <obcy adres> (musi dojść —
#                dowód, że test w ogóle widzi podszycie),
#   próba     — po włączeniu filtrowania: to samo podszycie nie może dojść (brak wpisu w dzienniku serwera).
# Wariant intercept, gdy projekt go blokuje: chwilowe restricted.containers.interception=allow (sudo incus),
# cofane na końcu — tylko pomiar; decyzja trafia do docs/box-decisions.md. Filtrowanie NIC nie ma wariantu.
set -euo pipefail
# shellcheck source=scripts/box/prototype/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

project="user-$(id -u)"
log_start p5 "P-5 — projekt ograniczony: nesting, intercept i filtrowanie NIC"
require_incus
sudo_prime
require_table
run "projekt $project (incus-user)" -- incus project show "$project"
run "projekt $project widziany przez administratora" -- as_root incus project show "$project"
ensure_box
ensure_pkgs netcat-openbsd python3 iproute2 util-linux
net_close

# --- nesting i intercept -------------------------------------------------------------------------------

intercept_variant=projekt
run "nesting i intercept w projekcie ograniczonym" -- incus config set "$BOX" security.nesting=true \
    security.syscalls.intercept.mknod=true security.syscalls.intercept.setxattr=true
if ok; then
    result config-nesting-intercept ok "projekt ograniczony przyjął security.nesting i intercept mknod/setxattr"
else
    result config-nesting-intercept fail "projekt ograniczony odrzucił ustawienia: $(tail -n 1 <<<"$LAST_OUT")"
    run "restricted.containers.interception (przed)" -- as_root incus project get "$project" restricted.containers.interception
    old_interception=$LAST_OUT
    restore_interception() {
        run "cofnięcie restricted.containers.interception" -- as_root incus project set "$project" \
            "restricted.containers.interception=${old_interception:-block}"
    }
    unset_intercept() {
        run "zdjęcie intercept mknod przed cofnięciem projektu" -- incus config unset "$BOX" security.syscalls.intercept.mknod
        run "zdjęcie intercept setxattr przed cofnięciem projektu" -- incus config unset "$BOX" security.syscalls.intercept.setxattr
    }
    at_exit restore_interception
    run "wariant: restricted.containers.interception=allow (chwilowo)" -- as_root incus project set "$project" \
        restricted.containers.interception=allow
    run "nesting i intercept po zmianie projektu" -- incus config set "$BOX" security.nesting=true \
        security.syscalls.intercept.mknod=true security.syscalls.intercept.setxattr=true
    if ok; then
        intercept_variant="restricted.containers.interception=allow"
        at_exit unset_intercept
        result config-wariant-interception info "intercept działa po restricted.containers.interception=allow — decyzja w box-decisions"
    else
        intercept_variant=brak
        result config-wariant-interception fail "intercept odrzucony także po zmianie projektu: $(tail -n 1 <<<"$LAST_OUT")"
    fi
fi
run "ustawienia bezpieczeństwa kontenera" -- incus config show "$BOX"
grep -q 'security.privileged: "true"' <<<"$LAST_OUT" && die "kontener jest uprzywilejowany (security.privileged) — niedozwolone"
run "restart kontenera (ustawienia działają od startu)" -- incus restart "$BOX"
wait_box

run "nesting: przestrzeń użytkownika i /proc w kontenerze" -- in_box unshare --user --map-root-user --pid --fork --mount-proc true
ok && result nesting ok "unshare z --mount-proc w kontenerze działa" \
    || result nesting fail "unshare --mount-proc w kontenerze: kod $LAST_RC"
run "intercept mknod: urządzenie znakowe 1:3 w kontenerze" -- in_box bash -c 'rm -f /var/tmp/proto-null; mknod /var/tmp/proto-null c 1 3 && ls -l /var/tmp/proto-null && rm -f /var/tmp/proto-null'
mknod_rc=$LAST_RC
run "intercept setxattr: trusted.overlay.opaque w kontenerze" -- in_box python3 -c '
import os, shutil
d = "/var/tmp/proto-xattr"
shutil.rmtree(d, ignore_errors=True)
os.mkdir(d)
os.setxattr(d, "trusted.overlay.opaque", b"y")
print(os.getxattr(d, "trusted.overlay.opaque"))
shutil.rmtree(d)'
setxattr_rc=$LAST_RC
if [[ $mknod_rc -eq 0 && $setxattr_rc -eq 0 ]]; then
    result intercept ok "mknod i setxattr (trusted.overlay.*) działają w kontenerze ($intercept_variant)"
else
    result intercept fail "mknod kod $mknod_rc, setxattr kod $setxattr_rc ($intercept_variant)"
fi

# --- filtrowanie NIC i podszycie -----------------------------------------------------------------------

cidr=$(bridge_cidr)
[[ -n $cidr ]] || die "brak adresu IPv4 mostka $BRIDGE"
bridge=${cidr%/*}
spoof="${bridge%.*}.250"
port=$(as_root nft list table inet tide-box </dev/null | sed -n 's/.*tcp dport \([0-9]*\) accept comment "proxy Tide".*/\1/p' | head -1)
[[ -n $port ]] || die "nie znalazłem portu proxy w tabeli inet tide-box"
note "Mostek $cidr, port proxy $port, obcy adres $spoof"

seen=$(mktemp "${TMPDIR:-/tmp}/tide-proto-seen-XXXXXX")
python3 -c '
import socket, sys
s = socket.socket()
s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
s.bind((sys.argv[1], int(sys.argv[2])))
s.listen(16)
open(sys.argv[3], "a").write("ready\n")
while True:
    c, a = s.accept()
    open(sys.argv[3], "a").write(a[0] + "\n")
    c.close()
' "$bridge" "$port" "$seen" </dev/null >/dev/null 2>&1 &
listener=$!
stop_listener() { kill "$listener" 2>/dev/null; rm -f "$seen"; }
at_exit stop_listener
for _ in 1 2 3 4 5 6 7 8 9 10; do grep -q ready "$seen" && break; sleep 0.3; done
grep -q ready "$seen" || die "serwer testowy nie słucha na $bridge:$port (port zajęty — zatrzymaj proxy z #15)"
note "Serwer testowy na hoście: $bridge:$port (zapisuje adres źródłowy połączenia)"

box_addr=$(box_ip) || die "brak adresu kontenera"
seen_since() { tail -n +"$(($1 + 1))" "$seen"; }
mark() { wc -l <"$seen"; }
connect() {  # connect <opis> [obcy adres]
    if [[ -n ${2:-} ]]; then
        run "$1" -- in_box bash -c "ip addr add $2/24 dev eth0 && timeout 10 nc -s $2 -z -w 3 $bridge $port; rc=\$?; ip addr del $2/24 dev eth0; exit \$rc"
    else
        run "$1" -- in_box timeout 10 nc -z -w 3 "$bridge" "$port"
    fi
    sleep "$SETTLE"
}

run "eth0 kontenera przed próbą" -- incus config device show "$BOX"
if grep -q '^eth0:' <<<"$LAST_OUT"; then
    run "zatrzymanie kontenera (zdjęcie filtrowania z poprzedniego uruchomienia)" -- incus stop "$BOX"
    run "usunięcie nadpisania eth0" -- incus config device remove "$BOX" eth0
    run "start kontenera" -- incus start "$BOX"
    wait_box
    box_addr=$(box_ip)
fi

m=$(mark)
connect "kontrola A: połączenie z własnego adresu $box_addr (bez filtrowania)"
grep -qx "$box_addr" <(seen_since "$m") && control_a=1 || control_a=0
m=$(mark)
connect "kontrola B: podszycie pod $spoof bez filtrowania" "$spoof"
grep -qx "$spoof" <(seen_since "$m") && control_b=1 || control_b=0
note "Kontrola A: $([[ $control_a -eq 1 ]] && echo doszło || echo nie doszło); kontrola B (podszycie bez filtrowania): $([[ $control_b -eq 1 ]] && echo doszło || echo nie doszło)"

run "zatrzymanie kontenera przed włączeniem filtrowania" -- incus stop "$BOX"
filters="mac, ipv4, ipv6"
run "filtrowanie NIC na eth0 (mac, ipv4, ipv6)" -- incus config device override "$BOX" eth0 \
    security.mac_filtering=true security.ipv4_filtering=true security.ipv6_filtering=true
if ! ok; then
    note "override z ipv6_filtering odrzucone — próba bez ipv6 (mostek bez IPv6, PT-166)"
    filters="mac, ipv4"
    run "filtrowanie NIC na eth0 (mac, ipv4)" -- incus config device override "$BOX" eth0 \
        security.mac_filtering=true security.ipv4_filtering=true
fi
filter_rc=$LAST_RC
run "start kontenera z filtrowaniem" -- incus start "$BOX"
wait_box
box_addr=$(box_ip)
run "eth0 kontenera po włączeniu filtrowania" -- incus config device show "$BOX"

m=$(mark)
connect "z filtrowaniem: połączenie z własnego adresu $box_addr"
grep -qx "$box_addr" <(seen_since "$m") && own_ok=1 || own_ok=0
m=$(mark)
connect "z filtrowaniem: podszycie pod $spoof" "$spoof"
grep -qx "$spoof" <(seen_since "$m") && spoof_seen=1 || spoof_seen=0
block "adresy źródłowe zapisane przez serwer testowy" "$(grep -v ready "$seen" || true)"

if [[ $filter_rc -ne 0 ]]; then
    result filtrowanie-nic fail "Incus odrzucił filtrowanie NIC w projekcie ograniczonym — warunek konieczny (question)"
elif [[ $control_b -eq 0 ]]; then
    result filtrowanie-nic fail "kontrola B nie zobaczyła podszycia bez filtrowania — test nieważny, wynik nieznany"
elif [[ $own_ok -eq 0 ]]; then
    result filtrowanie-nic fail "z filtrowaniem nie dochodzi nawet ruch z własnego adresu — filtr psuje sieć pudełka"
elif [[ $spoof_seen -eq 1 ]]; then
    result filtrowanie-nic fail "podszycie pod $spoof doszło do $bridge:$port mimo filtrowania ($filters)"
else
    result filtrowanie-nic ok "podszycie pod $spoof nie doszło do $bridge:$port; własny adres dochodzi ($filters)"
fi
[[ $filters == "mac, ipv4" ]] && result filtrowanie-ipv6 info "security.ipv6_filtering odrzucone (mostek bez IPv6) — ruch IPv6 z mostka i tak DROP w inet tide-box"
finish

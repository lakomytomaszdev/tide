#!/usr/bin/env bash
# P-7 (PT-178): Incus z Ubuntu universe — czy remote images: działa i czy images:ubuntu/24.04 startuje.
# Użycie: bash scripts/box/prototype/p7.sh      (jako zwykły użytkownik w grupie incus, bez sudo)
#
# Sprawdza pochodzenie pakietu incus (apt-cache policy), wersję, listę obrazów images: i uruchamia chwilowy
# kontener tide-proto-p7, który na końcu usuwa. Niczego nie instaluje — gdy images: nie działa, wariant Zabbly
# (nowe źródło apt) jest osobną decyzją użytkownika, nie częścią próby.
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf '%s\n' "Użycie: bash scripts/box/prototype/p7.sh — P-7: images: i images:ubuntu/24.04 z Incusa z Ubuntu"
    exit 0
fi
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
proto_log p7
need incus apt-cache dpkg-query

NAME=tide-proto-p7
cleanup() { incus delete --force "$NAME" >/dev/null 2>&1 || true; }
trap cleanup EXIT

step "pakiet incus i jego źródło"
run dpkg-query -W -f '${Package} ${Version}\n' incus incus-client || true
LC_ALL=C apt-cache policy incus | sed -n '1,12p'
origin=$(LC_ALL=C apt-cache policy incus | awk '/\*\*\*/{getline; print; exit}')
result pakiet "$(dpkg-query -W -f '${Version}' incus 2>/dev/null || echo brak) z: ${origin:-?}"
run incus version || true
run incus project list || true
run incus remote list || true

step "incus image list images: ubuntu/24.04"
list_rc=0
run incus image list images: ubuntu/24.04 -c lfpdasut || list_rc=$?
result image-list "kod $list_rc"

step "incus launch $PROTO_IMAGE $NAME"
cleanup
launch_rc=0
start=$(date +%s)
run incus launch "$PROTO_IMAGE" "$NAME" || launch_rc=$?
if [[ $launch_rc -eq 0 ]]; then
    for i in $(seq 60); do
        [[ $(incus list "$NAME" -c s -f csv) == RUNNING ]] && incus exec "$NAME" -- true 2>/dev/null && break
        sleep 1
    done
    run incus exec "$NAME" -- sh -c '. /etc/os-release; echo "$PRETTY_NAME"' || launch_rc=$?
    run incus exec "$NAME" -- systemctl is-system-running --wait || true
    run incus list "$NAME" -c ns4tS || true
fi
result launch "kod $launch_rc, czas $(( $(date +%s) - start )) s"

if [[ $list_rc -eq 0 && $launch_rc -eq 0 ]]; then
    result P-7 "działa z pakietem Ubuntu — images: i ubuntu/24.04 bez Zabbly"
else
    result P-7 "NIE działa (images: kod $list_rc, launch kod $launch_rc) — wariant Zabbly do decyzji użytkownika"
fi

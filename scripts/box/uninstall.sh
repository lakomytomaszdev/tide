#!/usr/bin/env bash
# Cofa scripts/box/install.sh (PT-166) według pliku stanu /var/lib/tide-box/install.state.
# Użycie: sudo bash scripts/box/uninstall.sh [--yes]
#         bash scripts/box/uninstall.sh --dry-run     (bez sudo: plan, bez zmian)
#         make box-uninstall                          (pyta o potwierdzenie — usuwa wszystkie pudełka)
#
# Kroki w odwrotnej kolejności niż instalacja:
#  1. trwałość: wyłącza i usuwa tide-box-nft.service i drop-in incus.service (zatrzymanie jednostki
#     zatrzymuje też Incusa — Requires=; tabela zostaje w jądrze do kroku 2, jednostka nie ma ExecStop).
#  2. nftables: usuwa tabelę inet tide-box i /etc/tide-box.
#  3. Incus: incus admin shutdown (zatrzymuje pudełka), zatrzymuje jednostki incus*, odmontowuje
#     /var/lib/incus, odłącza pętlę puli, usuwa mostek incusbr0 i tabelę inet incus.
#  4. grupa: wypisuje użytkownika z grup, które istniały przed instalacją (pozostałe znikają w kroku 6).
#  5. pakiety: apt-get purge pakietów z pliku stanu — najpierw symulacja; odmowa, gdy purge usunąłby coś
#     spoza tej listy (np. pakiet doinstalowany później, który zależy od incusa).
#  6. pozostałości: /var/lib/incus, /var/cache/incus, /var/log/incus, plik puli /home/tide-incus-pool.img,
#     grupy założone przez pakiety, linie dopisane do /etc/subuid i /etc/subgid, plik stanu.
#
# Wszystkie pudełka i ich dane w puli znikają (katalogi domowe pudełek na hoście, ~/.local/share/tide/box,
# zostają). Dowód odwracalności: scripts/box/host-state.sh przed instalacją i po odinstalowaniu, diff pusty.
#
# TIDE_BOX_ROOT (tylko z --dry-run, dla testów) — korzeń udawanego systemu plików, jak w install.sh.
set -Eeuo pipefail

MARKER=scripts/box/install.sh
BRIDGE=incusbr0
POOL=tide
POOL_IMG=/home/tide-incus-pool.img
UNIT=tide-box-nft.service

yes=0
dry=0
for arg in "$@"; do
    case "$arg" in
        --yes) yes=1 ;;
        --dry-run) dry=1 ;;
        *) echo "Nieznany argument: $arg (dozwolone: --yes, --dry-run)" >&2; exit 2 ;;
    esac
done
if [[ $dry -eq 1 ]]; then
    root="${TIDE_BOX_ROOT:-}"
else
    unset TIDE_BOX_ROOT
    root=""
    if [[ $EUID -ne 0 ]]; then
        echo "Uruchom przez sudo: sudo bash $0  (podgląd bez zmian: bash $0 --dry-run)" >&2
        exit 1
    fi
fi

STATE_DIR="$root/var/lib/tide-box"
STATE="$STATE_DIR/install.state"
NFT_DIR="$root/etc/tide-box"
UNIT_FILE="$root/etc/systemd/system/$UNIT"
DROPIN="$root/etc/systemd/system/incus.service.d/tide-box.conf"
POOL_FILE="$root$POOL_IMG"
LOOP_LINK="$root/var/lib/incus/disks/$POOL.img"

ours() { [[ -f $1 ]] && grep -qF "$MARKER" "$1"; }
state_get() { [[ -f $STATE ]] && sed -n "s/^$1=//p" "$STATE" | tail -1 || true; }

for f in "$UNIT_FILE" "$DROPIN"; do
    if [[ -e $f ]] && ! ours "$f"; then
        echo "${f#$root} nie ma znacznika Tide — to nie nasz plik, przerywam. Nic nie zmieniono." >&2
        exit 1
    fi
done
if [[ ! -f $STATE && ! -e $UNIT_FILE && ! -e $DROPIN && ! -e $NFT_DIR ]]; then
    echo "Brak ${STATE#$root} i plików Tide — nic do usunięcia."
    exit 0
fi
if [[ -f $STATE ]] && ! head -1 "$STATE" | grep -qF "$MARKER"; then
    echo "${STATE#$root} nie ma znacznika Tide — przerywam. Nic nie zmieniono." >&2
    exit 1
fi

user=$(state_get user)
read -r -a packages <<<"$(state_get packages)"
read -r -a member_of <<<"$(state_get member_of)"
read -r -a groups_before <<<"$(state_get groups_before)"
incus_ours=0
[[ " ${packages[*]} " == *" incus "* ]] && incus_ours=1

# --- plan -----------------------------------------------------------------------------------------

changes=()
plan() {  # plan <nr> <nazwa> <zmiana:0|1> <opis>
    if [[ $3 -eq 1 ]]; then
        echo "[$1/6] $2: zmiana — $4"
        changes+=("$2")
    else
        echo "[$1/6] $2: bez zmian — $4"
    fi
}

if [[ -e $UNIT_FILE || -e $DROPIN ]]; then
    plan 1 trwałość 1 "systemctl disable --now $UNIT, usunięcie jednostki i drop-inu incus.service"
else
    plan 1 trwałość 0 "brak jednostki $UNIT"
fi

table=0
out=$(nft list table inet tide-box 2>&1) && table=1
if [[ $table -eq 1 || -e $NFT_DIR ]]; then
    plan 2 nftables 1 "nft delete table inet tide-box, usunięcie ${NFT_DIR#$root}"
elif [[ $out == *"Operation not permitted"* ]]; then
    plan 2 nftables 1 "nft delete table inet tide-box, jeśli jest (stanu nie da się odczytać bez roota)"
else
    plan 2 nftables 0 "brak tabeli inet tide-box"
fi

if [[ $incus_ours -eq 1 ]]; then
    plan 3 incus 1 "incus admin shutdown (wszystkie pudełka), stop jednostek incus*, odmontowanie /var/lib/incus, pętla puli, $BRIDGE, tabela inet incus"
else
    plan 3 incus 0 "Incusa nie instalował ten skrypt"
fi

leave=()
for g in "${member_of[@]}"; do
    [[ " ${groups_before[*]} " == *" $g "* ]] && leave+=("$g")
done
if [[ ${#leave[@]} -gt 0 && -n $user ]]; then
    plan 4 grupa 1 "gpasswd -d $user ${leave[*]}"
else
    plan 4 grupa 0 "grupy dodane do $user znikną razem z pakietem"
fi

if [[ ${#packages[@]} -gt 0 ]]; then
    if ! sim=$(LC_ALL=C apt-get -s purge "${packages[@]}" 2>&1); then
        echo "apt-get -s purge ${packages[*]} nie przeszło — przerywam, nic nie zmieniono:" >&2
        echo "$sim" | sed 's/^/  /' >&2
        exit 1
    fi
    extra=$(awk '$1 == "Purg" || $1 == "Remv" { print $2 }' <<<"$sim" | grep -vxF -f <(printf '%s\n' "${packages[@]}") || true)
    if [[ -n "$extra" ]]; then
        echo "apt-get purge usunąłby też pakiety, których nie instalował ten skrypt — przerywam, nic nie zmieniono:" >&2
        echo "$extra" | sed 's/^/  /' >&2
        exit 1
    fi
    plan 5 pakiety 1 "apt-get purge -y ${packages[*]}"
else
    plan 5 pakiety 0 "brak pakietów w pliku stanu"
fi

leftovers="plik stanu ${STATE#$root}"
if [[ $incus_ours -eq 1 ]]; then
    leftovers="/var/lib/incus, /var/cache/incus, /var/log/incus, $POOL_IMG, grupy z pakietów, linie dopisane do /etc/subuid i /etc/subgid, $leftovers"
fi
plan 6 pozostałości 1 "$leftovers"

echo
echo "Uwaga: wszystkie pudełka Incusa i dane w puli zostaną usunięte."
if [[ $dry -eq 1 ]]; then
    echo "Podgląd (--dry-run): nic nie zmieniono. Kroki do wykonania: ${changes[*]}."
    exit 0
fi
if [[ $yes -eq 0 ]]; then
    if [[ ! -t 0 ]]; then
        echo "Brak terminala do pytania — uruchom w terminalu albo z --yes. Nic nie zmieniono." >&2
        exit 1
    fi
    read -r -p "Odinstalować? [t/N] " answer || answer=n
    if [[ "$answer" != [tTyY] ]]; then
        echo "Przerwano, nic nie zmieniono."
        exit 1
    fi
fi

# --- wykonanie ------------------------------------------------------------------------------------

trap 'echo "Odinstalowanie przerwane w połowie — ponów: make box-uninstall (plik stanu zostaje do końca)." >&2' ERR
planned() { [[ " ${changes[*]} " == *" $1 "* ]]; }
unit_known() { systemctl cat "$1" >/dev/null 2>&1; }

if planned trwałość; then
    if unit_known "$UNIT"; then
        systemctl disable --now "$UNIT"
    fi
    rm -f "$UNIT_FILE" "$DROPIN"
    [[ -d $(dirname "$DROPIN") ]] && rmdir --ignore-fail-on-non-empty "$(dirname "$DROPIN")"
    systemctl daemon-reload
fi

if planned nftables; then
    if nft list table inet tide-box >/dev/null 2>&1; then
        nft delete table inet tide-box
    fi
    rm -rf "$NFT_DIR"
fi

if planned incus; then
    # Po kroku 1 incusd zwykle już nie działa (Requires=); wywołanie incus obudziłoby go przez
    # incus.socket tylko po to, żeby go zaraz wyłączyć.
    if command -v incus >/dev/null && systemctl is-active --quiet incus.service; then
        incus admin shutdown || echo "incus admin shutdown nie przeszło — zatrzymuję jednostki." >&2
    fi
    for u in incus-startup.service incus-user.socket incus-user.service incus.socket incus.service; do
        if unit_known "$u"; then
            systemctl stop "$u"
        fi
    done
    # Od najgłębszych: pula, shmounts, guestapi… Bez -l, żeby zajęty punkt był widoczny jako błąd.
    findmnt -rn -o TARGET | { grep -E '^/var/lib/incus(/|$)' || true; } | sort -r | while read -r target; do
        umount "$target"
    done
    losetup -j "$POOL_IMG" | cut -d: -f1 | while read -r loop; do
        losetup -d "$loop"
    done
    if ip link show "$BRIDGE" >/dev/null 2>&1; then
        ip link delete "$BRIDGE"
    fi
    if nft list table inet incus >/dev/null 2>&1; then
        nft delete table inet incus
    fi
fi

if planned grupa; then
    for g in "${leave[@]}"; do
        gpasswd -d "$user" "$g"
    done
fi

if planned pakiety; then
    DEBIAN_FRONTEND=noninteractive apt-get purge -y "${packages[@]}"
fi

if [[ $incus_ours -eq 1 ]]; then
    # --one-file-system: gdyby coś zostało zamontowane, rm nie wejdzie do środka (np. do puli).
    rm -rf --one-file-system /var/lib/incus /var/cache/incus /var/log/incus
    rm -f "$POOL_IMG" "$LOOP_LINK"
    for g in incus incus-admin; do
        if [[ " ${groups_before[*]} " != *" $g "* ]] && getent group "$g" >/dev/null; then
            groupdel "$g"
        fi
    done
    for f in subuid subgid; do
        [[ -f $STATE_DIR/$f.before ]] || continue
        added=$(grep -vxF -f "$STATE_DIR/$f.before" "/etc/$f" || true)
        if [[ -n "$added" ]]; then
            # Nadpisanie w miejscu zachowuje właściciela i uprawnienia pliku.
            kept=$(grep -vxF -f <(printf '%s\n' "$added") "/etc/$f" || true)
            printf '%s' "$kept${kept:+$'\n'}" >"/etc/$f"
        fi
    done
fi
rm -rf "$STATE_DIR"
trap - ERR

echo
echo "Odinstalowano pudełko Tide. Dowód: bash scripts/box/host-state.sh > host-after.txt; diff host-before.txt host-after.txt"

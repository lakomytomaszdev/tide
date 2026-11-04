#!/usr/bin/env bash
# Migawka stanu hosta, którego dotyka instalacja pudełka (PT-166) — dowód odwracalności make box-uninstall.
# Użycie: bash scripts/box/host-state.sh > docs/box-prototype/host-before.txt   (przed make box-install)
#         bash scripts/box/host-state.sh > docs/box-prototype/host-after.txt    (po make box-uninstall)
#         diff docs/box-prototype/host-before.txt docs/box-prototype/host-after.txt   → pusty
#         make box-state OUT=plik                                                 (to samo, zakłada katalog)
#
# Tylko odczyt. nft list ruleset wymaga roota: bez niego skrypt woła sudo nft list ruleset (hasło raz).
# Zmienne części stanu są pomijane, żeby diff pokazywał tylko zmiany instalacji: liczniki reguł
# (counter packets/bytes), czasy wygaśnięcia elementów zbiorów, indeksy i flagi łączy.
#
# TIDE_BOX_ROOT (dla testów) — korzeń udawanego systemu plików dla sprawdzanych plików i katalogów.
set -euo pipefail

root="${TIDE_BOX_ROOT:-}"
section() { printf '\n## %s\n' "$1"; }
exists() { if [[ -e $root$1 || -L $root$1 ]]; then echo "$1: jest"; else echo "$1: brak"; fi; }

echo "# Stan hosta dla pudełka Tide (scripts/box/host-state.sh)"

section "pakiety (dpkg-query -W)"
dpkg-query -W | LC_ALL=C sort

section "łącza (ip -o link: nazwa i typ)"
ip -o link | awk -F': ' '{ split($3, rest, "link/"); split(rest[2], t, " "); print $2, "link/" t[1] }' | LC_ALL=C sort

section "nftables (nft list ruleset, bez liczników)"
if ! ruleset=$(nft list ruleset 2>/dev/null); then
    ruleset=$(sudo nft list ruleset)
fi
sed -E 's/counter packets [0-9]+ bytes [0-9]+/counter/g; s/ expires [0-9hms.]+//g' <<<"$ruleset"

section "grupy (getent group)"
getent group | LC_ALL=C sort

section "/etc/subuid, /etc/subgid"
for f in /etc/subuid /etc/subgid; do
    echo "$f:"
    if [[ -f $root$f ]]; then sed 's/^/  /' "$root$f"; fi
done

section "pliki i katalogi"
for p in /home/tide-incus-pool.img /var/lib/incus /var/cache/incus /var/log/incus /var/lib/tide-box \
    /etc/tide-box /etc/systemd/system/tide-box-nft.service /etc/systemd/system/incus.service.d; do
    exists "$p"
done

section "jednostki systemd (incus*, lxcfs*, tide-box*)"
# Bez żadnej pasującej jednostki (stan przed instalacją) systemctl kończy się kodem 1 — to nie błąd.
units=$(systemctl list-unit-files --no-legend --no-pager 'incus*' 'lxcfs*' 'tide-box*') || units=""
[[ -z $units ]] || awk '{ print $1, $2 }' <<<"$units" | LC_ALL=C sort

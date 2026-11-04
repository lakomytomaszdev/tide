#!/usr/bin/env bash
# Instaluje Incusa dla pudełka karty Tide (PT-166, wzór scripts/sandbox/install.sh z PT-75).
# Użycie: sudo bash scripts/box/install.sh [--yes] [--project-mode restricted|admin] [--proxy-port 18080]
#         bash scripts/box/install.sh --dry-run [...]   (bez sudo: sprawdzenia i plan, bez zmian)
#         make box-install                              (sudo pyta raz o hasło, --yes)
#
# Kroki (każdy sprawdza stan i nic nie robi, gdy jest już zrobiony — drugie uruchomienie nic nie zmienia):
#  1. symulacja apt: apt-get -s install incus btrfs-progs [nftables]; odmowa, gdy lista zawiera pakiet
#     z qt6/kf6/libkf/plasma w nazwie, podnosi albo usuwa zainstalowany pakiet (uninstall tego nie cofnie).
#  2. instalacja: apt-get install tych samych pakietów; lista nowych pakietów idzie do pliku stanu.
#  3. preseed: incus admin init --preseed — pula btrfs „tide” w rzadkim pliku /home/tide-incus-pool.img
#     (200 GiB; dowiązanie z domyślnego miejsca pliku pętli Incusa, bo / ma mało miejsca), mostek incusbr0
#     z NAT i DHCP (tylko IPv4), profil default z dyskiem na tej puli i kartą w incusbr0.
#  4. grupa: użytkownik z SUDO_USER do grupy incus (restricted) albo incus-admin (admin).
#  5. projekt: user-<uid> (ten, którego używa incus-user), ograniczony, z restricted.containers.nesting=allow,
#     dyskami tylko z katalogu domowego, siecią tylko incusbr0; gniazdo incus-user.socket włączone.
#  6. nftables: tabela inet tide-box (scripts/box/files.sh rules) w /etc/tide-box/tide-box.nft, wczytana.
#  7. trwałość: jednostka tide-box-nft.service (wczytuje plik przy starcie) i drop-in incus.service
#     (Requires=) — Incus nie wstaje bez reguł.
#
# Tryb admin (--project-mode admin: grupa incus-admin = root hosta) tylko po próbie P-5 z PT-164 i z wpisem
# „tryb-admin” w docs/box-decisions.md — bez wpisu skrypt odmawia.
# Plik stanu /var/lib/tide-box/install.state zapisuje, co zmieniono (pakiety, grupy, kopie /etc/subuid
# i /etc/subgid) — z niego korzysta scripts/box/uninstall.sh. Incusa zainstalowanego nie przez ten skrypt
# nie przejmujemy (odmowa). Dowód odwracalności: scripts/box/host-state.sh przed instalacją i po odinstalowaniu.
#
# Zmienna dla testów (działa tylko z --dry-run; sudo i tak czyści środowisko): TIDE_BOX_ROOT — korzeń
# udawanego systemu plików (pliki reguł, jednostki, stanu, puli).
set -Eeuo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../.." && pwd)
files="$here/files.sh"

BRIDGE=incusbr0
POOL=tide
POOL_IMG=/home/tide-incus-pool.img
POOL_SIZE=200GiB
UNIT=tide-box-nft.service
MARKER=scripts/box/install.sh

yes=0
dry=0
mode=restricted
port=18080
while [[ $# -gt 0 ]]; do
    case "$1" in
        --yes) yes=1 ;;
        --dry-run) dry=1 ;;
        --project-mode) mode="${2:-}"; shift ;;
        --project-mode=*) mode="${1#*=}" ;;
        --proxy-port) port="${2:-}"; shift ;;
        --proxy-port=*) port="${1#*=}" ;;
        *) echo "Nieznany argument: $1 (dozwolone: --yes, --dry-run, --project-mode, --proxy-port)" >&2; exit 2 ;;
    esac
    shift
done
if [[ $mode != restricted && $mode != admin ]]; then
    echo "--project-mode: restricted albo admin (podano: '$mode')" >&2
    exit 2
fi
if ! [[ $port =~ ^[0-9]{1,5}$ ]] || (( 10#$port < 1 || 10#$port > 65535 )); then
    echo "--proxy-port: liczba 1–65535 (podano: '$port')" >&2
    exit 2
fi

if [[ $dry -eq 1 ]]; then
    # sudo … --dry-run: plan dla użytkownika, który wywołał sudo, z pełnym wglądem w stan Incusa.
    user="${SUDO_USER:-$(id -un)}"
    [[ $user == root ]] && user="$(id -un)"
    root="${TIDE_BOX_ROOT:-}"
else
    # Zmienne testów nie mogą sterować prawdziwą instalacją.
    unset TIDE_BOX_ROOT
    root=""
    if [[ $EUID -ne 0 ]]; then
        echo "Uruchom przez sudo: sudo bash $0  (podgląd bez zmian: bash $0 --dry-run)" >&2
        exit 1
    fi
    user="${SUDO_USER:-}"
    if [[ -z "$user" || "$user" == root ]]; then
        echo "Brak SUDO_USER — uruchom jako zwykły użytkownik przez sudo, nie z konta roota." >&2
        exit 1
    fi
fi
uid=$(id -u "$user")
gid=$(id -g "$user")
home=$(getent passwd "$user" | cut -d: -f6)
if [[ -z "$home" || ! -d "$home" ]]; then
    echo "Nie znam katalogu domowego użytkownika $user." >&2
    exit 1
fi
project="user-$uid"
group=incus
[[ $mode == admin ]] && group=incus-admin

STATE_DIR="$root/var/lib/tide-box"
STATE="$STATE_DIR/install.state"
NFT_FILE="$root/etc/tide-box/tide-box.nft"
UNIT_FILE="$root/etc/systemd/system/$UNIT"
DROPIN="$root/etc/systemd/system/incus.service.d/tide-box.conf"
POOL_FILE="$root$POOL_IMG"
LOOP_LINK="$root/var/lib/incus/disks/$POOL.img"

if [[ $mode == admin ]] && ! grep -q '^| tryb-admin |' "$repo/docs/box-decisions.md" 2>/dev/null; then
    echo "Tryb admin (grupa incus-admin = root hosta) wymaga wpisu „tryb-admin” w docs/box-decisions.md" >&2
    echo "po próbie P-5 z PT-164. Bez wpisu: --project-mode restricted (domyślnie)." >&2
    exit 1
fi
for tool in apt-get dpkg-query systemctl getent; do
    if ! command -v "$tool" >/dev/null; then
        echo "Brak programu: $tool" >&2
        exit 1
    fi
done

state_get() { [[ -f $STATE ]] && sed -n "s/^$1=//p" "$STATE" | tail -1 || true; }

# --- 1. symulacja apt -----------------------------------------------------------------------------

packages=(incus btrfs-progs)
command -v nft >/dev/null || packages+=(nftables)
if ! sim=$(LC_ALL=C apt-get -s install "${packages[@]}" 2>&1); then
    echo "apt-get -s install ${packages[*]} nie przeszło — przerywam, nic nie zmieniono:" >&2
    echo "$sim" | sed 's/^/  /' >&2
    exit 1
fi
new=$(awk '$1 == "Inst" { print $2 }' <<<"$sim")
upgraded=$(awk '$1 == "Inst" && $3 ~ /^\[/ { print $2 } $1 == "Remv" { print $2 }' <<<"$sim")
forbidden=$(grep -Ei 'qt6|kf6|libkf|plasma' <<<"$new" || true)

echo "[1/7] symulacja apt: apt-get -s install ${packages[*]}"
if [[ -n "$new" ]]; then
    echo "$new" | sed 's/^/        nowy pakiet: /'
else
    echo "        brak nowych pakietów"
fi
if [[ -n "$forbidden" ]]; then
    echo "Symulacja apt zawiera pakiety Qt/KDE — odmowa (zasada projektu: apt nie może ruszać Qt ani KDE):" >&2
    echo "$forbidden" | sed 's/^/  /' >&2
    echo "Nic nie zmieniono." >&2
    exit 1
fi
if [[ -n "$upgraded" ]]; then
    echo "Symulacja apt podnosi albo usuwa zainstalowane pakiety — make box-uninstall by tego nie cofnął:" >&2
    echo "$upgraded" | sed 's/^/  /' >&2
    echo "Nic nie zmieniono." >&2
    exit 1
fi
incus_new=0
grep -qx incus <<<"$new" && incus_new=1
if [[ $incus_new -eq 0 && ! -f $STATE ]]; then
    echo "Incus jest już zainstalowany, ale nie przez ten skrypt (brak ${STATE#$root}) — nie przejmuję cudzej" >&2
    echo "instalacji. Nic nie zmieniono." >&2
    exit 1
fi

# --- plan kroków 2–7 ------------------------------------------------------------------------------

changes=()
plan() {  # plan <nr> <nazwa> <zmiana:0|1> <opis>
    if [[ $3 -eq 1 ]]; then
        echo "[$1/7] $2: zmiana — $4"
        changes+=("$2")
    else
        echo "[$1/7] $2: bez zmian — $4"
    fi
}

# 2. instalacja
if [[ -n "$new" ]]; then
    plan 2 instalacja 1 "apt-get install -y ${packages[*]}"
else
    plan 2 instalacja 0 "pakiety są zainstalowane"
fi

# 3. preseed
address=""
need_preseed=1
if [[ $incus_new -eq 0 ]]; then
    address=$(incus network get "$BRIDGE" ipv4.address 2>/dev/null || true)
    address="${address%%/*}"
    if incus storage show "$POOL" >/dev/null 2>&1 && [[ -n "$address" ]] \
        && [[ "$(incus profile device get default root pool 2>/dev/null || true)" == "$POOL" ]]; then
        need_preseed=0
    fi
fi
if [[ $need_preseed -eq 1 ]]; then
    if [[ -e $POOL_FILE ]] && ! { [[ $incus_new -eq 0 ]] && incus storage show "$POOL" >/dev/null 2>&1; }; then
        if [[ $dry -eq 1 && $incus_new -eq 0 && $EUID -ne 0 ]]; then
            # Bez roota (i przed ponownym zalogowaniem do grupy) incus może nie pokazać puli, która jest.
            echo "        (podgląd bez roota nie widzi stanu Incusa — pełny plan: sudo bash $0 --dry-run)"
        else
            echo "$POOL_IMG już istnieje, a puli $POOL nie ma — nie formatuję cudzego pliku. Nic nie zmieniono." >&2
            exit 1
        fi
    fi
    plan 3 preseed 1 "incus admin init --preseed: pula btrfs $POOL ($POOL_IMG, $POOL_SIZE, rzadki), $BRIDGE z NAT i DHCP"
else
    plan 3 preseed 0 "pula $POOL i mostek $BRIDGE ($address) są"
fi

# 4. grupa
members=$(getent group "$group" | cut -d: -f4 || true)
if [[ ",$members," == *",$user,"* ]]; then
    plan 4 grupa 0 "$user jest w grupie $group"
else
    plan 4 grupa 1 "usermod -aG $group $user (nowa grupa działa po ponownym zalogowaniu)"
fi

# 5. projekt
project_config=(
    features.images=true
    features.profiles=true
    features.storage.volumes=true
    features.networks=false
)
if [[ $mode == restricted ]]; then
    project_config+=(
        restricted=true
        restricted.containers.nesting=allow
        restricted.devices.disk=allow
        "restricted.devices.disk.paths=$home"
        "restricted.idmap.uid=$uid"
        "restricted.idmap.gid=$gid"
        "restricted.networks.access=$BRIDGE"
    )
else
    project_config+=(restricted=false)
fi
project_missing=()
profile_ok=0
if [[ $incus_new -eq 0 ]] && incus project show "$project" >/dev/null 2>&1; then
    for kv in "${project_config[@]}"; do
        current=$(incus project get "$project" "${kv%%=*}" 2>/dev/null || true)
        # Niepodane restricted=false i features.networks=false są wartościami domyślnymi.
        [[ -z "$current" && "${kv#*=}" == false ]] && current=false
        [[ "$current" == "${kv#*=}" ]] || project_missing+=("$kv")
    done
    if [[ "$(incus profile device get default root pool --project "$project" 2>/dev/null || true)" == "$POOL" \
        && "$(incus profile device get default eth0 network --project "$project" 2>/dev/null || true)" == "$BRIDGE" ]]; then
        profile_ok=1
    fi
    project_exists=1
else
    project_missing=("${project_config[@]}")
    project_exists=0
fi
user_socket=1
if [[ $mode == restricted ]]; then
    [[ "$(systemctl is-enabled incus-user.socket 2>/dev/null || true)" == enabled ]] || user_socket=0
fi
if [[ ${#project_missing[@]} -eq 0 && $profile_ok -eq 1 && $user_socket -eq 1 ]]; then
    plan 5 projekt 0 "projekt $project ($mode) ma ustawienia i profil default"
else
    desc="projekt $project ($mode)"
    [[ $project_exists -eq 0 ]] && desc="incus project create $project ($mode)"
    [[ ${#project_missing[@]} -gt 0 ]] && desc+=", ustawienia: ${project_missing[*]}"
    [[ $profile_ok -eq 0 ]] && desc+=", profil default: root na $POOL, eth0 w $BRIDGE"
    [[ $user_socket -eq 0 ]] && desc+=", systemctl enable --now incus-user.socket"
    plan 5 projekt 1 "$desc"
fi

# 6. nftables
work=$(mktemp -d "${TMPDIR:-/tmp}/tide-box-XXXXXX")
trap 'rm -rf "$work"' EXIT
table_loaded() {  # 0 — jest, 1 — brak, 2 — nie da się sprawdzić (bez roota)
    local out
    if out=$(nft list table inet tide-box 2>&1); then
        return 0
    fi
    [[ $out == *"Operation not permitted"* ]] && return 2
    return 1
}
check_rules() {  # nft -c przed jakąkolwiek zmianą; bez roota w podglądzie — pominięte z uwagą
    local check
    command -v nft >/dev/null || return 0
    if ! check=$(nft -c -f "$1" 2>&1); then
        if [[ $dry -eq 1 && $check == *"Operation not permitted"* ]]; then
            echo "        (nft -c wymaga roota — składnię sprawdzi instalacja z sudo)"
        else
            echo "nft -c odrzucił reguły — przerywam, nic nie zmieniono:" >&2
            echo "$check" | sed 's/^/  /' >&2
            exit 1
        fi
    fi
}
if [[ -z "$address" ]]; then
    # Adresu mostka jeszcze nie ma (świeży host): składnię sprawdzamy z adresem zastępczym, żeby błąd
    # wyszedł przed apt i preseedem, nie dopiero w kroku 6. Właściwy plik powstaje po preseedzie.
    bash "$files" rules 10.0.0.1 "$port" >"$work/tide-box.check.nft"
    check_rules "$work/tide-box.check.nft"
    plan 6 nftables 1 "tabela inet tide-box w ${NFT_FILE#$root}: proxy <adres $BRIDGE po preseed>:$port, DHCP, reszta DROP"
else
    bash "$files" rules "$address" "$port" >"$work/tide-box.nft"
    check_rules "$work/tide-box.nft"
    loaded=0
    table_loaded || loaded=$?
    if [[ $loaded -eq 0 ]] && cmp -s "$work/tide-box.nft" "$NFT_FILE"; then
        plan 6 nftables 0 "tabela inet tide-box wczytana, ${NFT_FILE#$root} aktualny (proxy $address:$port)"
    else
        why="tabela inet tide-box w ${NFT_FILE#$root} (proxy $address:$port, DHCP, reszta DROP)"
        [[ $loaded -eq 2 ]] && why+="; stanu tabeli nie da się odczytać bez roota"
        if [[ -f $NFT_FILE ]] && ! cmp -s "$work/tide-box.nft" "$NFT_FILE"; then
            diff -u "$NFT_FILE" "$work/tide-box.nft" | sed 's/^/        /' || true
        fi
        plan 6 nftables 1 "$why"
    fi
fi

# 7. trwałość
bash "$files" unit >"$work/unit"
bash "$files" dropin >"$work/dropin"
if cmp -s "$work/unit" "$UNIT_FILE" && cmp -s "$work/dropin" "$DROPIN" \
    && [[ "$(systemctl is-enabled "$UNIT" 2>/dev/null || true)" == enabled ]]; then
    plan 7 trwałość 0 "$UNIT włączona, drop-in incus.service jest"
else
    plan 7 trwałość 1 "$UNIT (nft -f przy starcie, przed siecią i Incusem) i drop-in incus.service (Requires=)"
fi

echo
if [[ ${#changes[@]} -eq 0 ]]; then
    echo "Nic do zmiany: Incus, pula, projekt $project i reguły inet tide-box są na miejscu."
    exit 0
fi
if [[ $dry -eq 1 ]]; then
    echo "Podgląd (--dry-run): nic nie zmieniono. Kroki do wykonania: ${changes[*]}."
    echo "Instalacja: make box-install  (albo sudo bash $0 --yes)"
    exit 0
fi
if [[ $yes -eq 0 ]]; then
    if [[ ! -t 0 ]]; then
        echo "Brak terminala do pytania — uruchom w terminalu albo z --yes. Nic nie zmieniono." >&2
        exit 1
    fi
    read -r -p "Wykonać kroki: ${changes[*]}? [t/N] " answer || answer=n
    if [[ "$answer" != [tTyY] ]]; then
        echo "Przerwano, nic nie zmieniono."
        exit 1
    fi
fi

# --- wykonanie ------------------------------------------------------------------------------------

trap 'echo "Instalacja przerwana w połowie. Ponów: make box-install; cofnięcie: make box-uninstall" >&2; rm -rf "$work"' ERR
planned() { [[ " ${changes[*]} " == *" $1 "* ]]; }
state_add() {  # state_add <klucz> <wartość…> — dopisuje wartości bez powtórzeń
    local key=$1 v cur
    shift
    cur=$(state_get "$key")
    for v in "$@"; do
        [[ " $cur " == *" $v "* ]] || cur="${cur:+$cur }$v"
    done
    if grep -q "^$key=" "$STATE"; then
        sed -i "s|^$key=.*|$key=$cur|" "$STATE"
    else
        echo "$key=$cur" >>"$STATE"
    fi
}

install -d -m 755 "$STATE_DIR"
if [[ ! -f $STATE ]]; then
    printf '# %s — stan instalacji pudełka; czyta scripts/box/uninstall.sh\nuser=%s\n' "$MARKER" "$user" >"$STATE"
    chmod 644 "$STATE"
fi

if planned instalacja; then
    # Przed instalacją: które grupy już były i kopie /etc/subuid, /etc/subgid (postinst incusa dopisuje root).
    before=()
    for g in incus incus-admin; do getent group "$g" >/dev/null && before+=("$g"); done
    grep -q '^groups_before=' "$STATE" || echo "groups_before=${before[*]}" >>"$STATE"
    for f in subuid subgid; do
        [[ -f $STATE_DIR/$f.before ]] || cp -p "/etc/$f" "$STATE_DIR/$f.before"
    done
    # Lista z symulacji trafia do stanu przed instalacją — przerwana instalacja też jest do cofnięcia.
    # shellcheck disable=SC2086
    state_add packages $new
    DEBIAN_FRONTEND=noninteractive apt-get install -y "${packages[@]}"
fi

incus admin waitready --timeout 120

if planned preseed; then
    # Incus tworzy plik pętli puli tylko w /var/lib/incus/disks/<pula>.img (na /, gdzie jest mało miejsca);
    # dowiązanie sprawia, że rzadki plik powstaje na /home. Sprawdzone niżej: plik musi istnieć na /home.
    if ! incus storage show "$POOL" >/dev/null 2>&1; then
        install -d -m 700 "$(dirname "$LOOP_LINK")"
        [[ -L $LOOP_LINK ]] || ln -s "$POOL_IMG" "$LOOP_LINK"
    fi
    incus admin init --preseed <<EOF
networks:
- name: $BRIDGE
  type: bridge
  config:
    ipv4.address: auto
    ipv4.nat: "true"
    ipv4.dhcp: "true"
    ipv6.address: none
storage_pools:
- name: $POOL
  driver: btrfs
  config:
    size: $POOL_SIZE
profiles:
- name: default
  devices:
    root:
      path: /
      pool: $POOL
      type: disk
    eth0:
      name: eth0
      network: $BRIDGE
      type: nic
EOF
    if [[ ! -f $POOL_IMG || -L $POOL_IMG ]]; then
        echo "Incus nie założył pliku puli $POOL_IMG — sprawdź: incus storage show $POOL" >&2
        exit 1
    fi
fi
address=$(incus network get "$BRIDGE" ipv4.address)
address="${address%%/*}"

if planned grupa; then
    usermod -aG "$group" "$user"
    state_add member_of "$group"
fi

if planned projekt; then
    if ! incus project show "$project" >/dev/null 2>&1; then
        # Projekt zakładamy sami, zanim zrobi to incus-user (ten dałby projektowi własny mostek bez reguł).
        incus project create "$project"
    fi
    to_set=()
    for kv in "${project_config[@]}"; do
        current=$(incus project get "$project" "${kv%%=*}" 2>/dev/null || true)
        [[ -z "$current" && "${kv#*=}" == false ]] && current=false
        [[ "$current" == "${kv#*=}" ]] || to_set+=("$kv")
    done
    [[ ${#to_set[@]} -eq 0 ]] || incus project set "$project" "${to_set[@]}"
    if incus profile device get default root pool --project "$project" >/dev/null 2>&1; then
        incus profile device set default root pool="$POOL" --project "$project"
    else
        incus profile device add default root disk path=/ pool="$POOL" --project "$project"
    fi
    if incus profile device get default eth0 network --project "$project" >/dev/null 2>&1; then
        incus profile device set default eth0 network="$BRIDGE" --project "$project"
    else
        incus profile device add default eth0 nic name=eth0 network="$BRIDGE" --project "$project"
    fi
    [[ $mode == restricted ]] && systemctl enable --now incus-user.socket
fi

if planned nftables; then
    bash "$files" rules "$address" "$port" >"$work/tide-box.nft"
    nft -c -f "$work/tide-box.nft"
    install -D -m 644 -o root -g root "$work/tide-box.nft" "$NFT_FILE"
    nft -f "$NFT_FILE"
fi

if planned trwałość; then
    install -D -m 644 -o root -g root "$work/unit" "$UNIT_FILE"
    install -D -m 644 -o root -g root "$work/dropin" "$DROPIN"
    systemctl daemon-reload
    systemctl enable "$UNIT"
    systemctl start "$UNIT"
fi
trap - ERR

# --- raport ---------------------------------------------------------------------------------------

echo
echo "== apt-get -s install ${packages[*]} (nowe pakiety)"
echo "${new:-(brak)}"
echo
echo "== incus list --project $project"
incus list --project "$project"
echo
echo "== incus project show $project"
incus project show "$project"
echo
echo "== nft list table inet tide-box"
nft list table inet tide-box
echo
echo "Gotowe. Grupa $group działa po ponownym zalogowaniu $user. Cofnięcie: make box-uninstall"

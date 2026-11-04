# Wspólne funkcje prób pudełka (PT-178: P-2, P-3, P-6, P-7). Dołączane przez `source`, nie uruchamiane.
#
# Dziennik: każda próba woła `proto_log p<N>` — całe wyjście (stdout i stderr) idzie na ekran i do
# docs/box-prototype/p<N>-<czas>.log; obok katalog p<N>-<czas>/ na dzienniki proxy (odmowy, dostęp) i listy domen.
# Wynik próby to wiersze „WYNIK <klucz>: …” w dzienniku — z nich powstaje notka i docs/box-prototype.md.
#
# Pudełko prób: kontener tide-proto-net (własny, bo #1/PT-164 jeszcze trwa) z ubuntu/24.04 z images:, w projekcie
# użytkownika (incus-user). Sieć pudełka przechodzi przez nftables z PT-166: wolno tylko do adresu mostka na port
# proxy (18080) i DHCP. Prototypowe proxy (proxy.py) biegnie na hoście na adresie mostka, uruchamiane przez próbę
# i zatrzymywane w trap. Żadna próba nie zmienia nftables ani nie włącza sieci pudełka inaczej niż przez proxy.

set -Eeuo pipefail

PROTO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
PROTO_REPO=$(cd "$PROTO_DIR/../../.." && pwd)
PROTO_BOX=${PROTO_BOX:-tide-proto-net}
PROTO_IMAGE=${PROTO_IMAGE:-images:ubuntu/24.04}
PROTO_PORT=${PROTO_PORT:-18080}
PROTO_BRIDGE=${PROTO_BRIDGE:-incusbr0}
PROTO_STATE=${XDG_RUNTIME_DIR:-/tmp}/tide-box-prototype
# Użytkownik pudełka: obraz ubuntu/24.04 ma „ubuntu” z uid 1000.
BOX_USER=ubuntu
BOX_HOME=/home/ubuntu

proto_log() {
    local trial=$1 stamp
    stamp=$(date +%Y-%m-%dT%H%M%S)
    mkdir -p "$PROTO_REPO/docs/box-prototype"
    PROTO_RUN="$PROTO_REPO/docs/box-prototype/$trial-$stamp"
    PROTO_LOG="$PROTO_RUN.log"
    mkdir -p "$PROTO_RUN"
    exec > >(tee -a "$PROTO_LOG") 2>&1
    echo "# próba $trial — $(date -Iseconds) — $(uname -r) — $(id -un)@$(hostname)"
    echo "# dziennik: ${PROTO_LOG#$PROTO_REPO/}"
}

step() { echo; echo "== $*"; }
result() { echo "WYNIK $1: ${*:2}"; }

# Polecenie z pełnym wyjściem i kodem — „nie działa” zawsze z pełnym błędem, bez obejścia.
run() {
    echo "\$ $*"
    local rc=0
    "$@" || rc=$?
    echo "-> kod $rc"
    return $rc
}

need() {
    local tool
    for tool in "$@"; do
        command -v "$tool" >/dev/null || { echo "Brak programu: $tool" >&2; exit 1; }
    done
}

bridge_address() {
    local cidr
    cidr=$(incus network get "$PROTO_BRIDGE" ipv4.address 2>/dev/null || true)
    if [[ -z $cidr ]]; then
        cidr=$(ip -4 -o addr show dev "$PROTO_BRIDGE" 2>/dev/null | awk '{print $4}' | head -1)
    fi
    [[ -n $cidr ]] || { echo "Nie znam adresu mostka $PROTO_BRIDGE — czy make box-install (PT-166) przeszło?" >&2; exit 1; }
    echo "${cidr%/*}"
}

# NO_PROXY z karty (P-6): localhost, pętla zwrotna, sieci Dockera, host.docker.internal, adres mostka.
no_proxy_value() { echo "localhost,127.0.0.1,::1,172.16.0.0/12,host.docker.internal,$1"; }

# --- proxy prototypowe ------------------------------------------------------------------------------

# proxy_start <plik listy> [argumenty proxy.py…] — na adresie mostka, dzienniki w $PROTO_RUN.
proxy_start() {
    local list=$1; shift
    PROXY_DENY="$PROTO_RUN/deny.log"
    PROXY_ACCESS="$PROTO_RUN/access.log"
    touch "$list" "$PROXY_DENY" "$PROXY_ACCESS"
    if ss -Hltn "sport = :$PROTO_PORT" | grep -q .; then
        echo "Port $PROTO_PORT jest zajęty (inne proxy?) — zatrzymaj je albo uruchom cleanup.sh:" >&2
        ss -Hltnp "sport = :$PROTO_PORT" >&2 || true
        exit 1
    fi
    mkdir -p "$PROTO_STATE"
    python3 "$PROTO_DIR/proxy.py" --listen "$BRIDGE" --port "$PROTO_PORT" --domains "$list" \
        --deny-log "$PROXY_DENY" --access-log "$PROXY_ACCESS" "$@" >>"$PROTO_RUN/proxy.out" 2>&1 &
    PROXY_PID=$!
    echo "$PROXY_PID" >"$PROTO_STATE/proxy.pid"
    trap proxy_stop EXIT
    local i
    for i in $(seq 50); do
        grep -q "słucha na" "$PROTO_RUN/proxy.out" 2>/dev/null && break
        kill -0 "$PROXY_PID" 2>/dev/null || { cat "$PROTO_RUN/proxy.out" >&2; exit 1; }
        sleep 0.1
    done
    sed 's/^/   proxy: /' "$PROTO_RUN/proxy.out"
}

proxy_stop() {
    if [[ -n ${PROXY_PID:-} ]] && kill -0 "$PROXY_PID" 2>/dev/null; then
        kill "$PROXY_PID" 2>/dev/null || true
        wait "$PROXY_PID" 2>/dev/null || true
    fi
    rm -f "$PROTO_STATE/proxy.pid"
}

# Nowe wiersze dziennika odmów od wiersza $1: unikalne domeny (bez portu, pierwszego wystąpienia kolejno).
denied_since() { tail -n +"$(( $1 + 1 ))" "$PROXY_DENY" | awk -F'\t' '!seen[$2]++ { print $2 }'; }
lines() { wc -l <"$1" | tr -d ' '; }

# --- pudełko prób -------------------------------------------------------------------------------------

box_exists() { incus info "$PROTO_BOX" >/dev/null 2>&1; }

# Środowisko proxy przekazywane jawnie do incus exec (exec nie czyta /etc/environment — robi to tylko PAM).
box_env() {
    local np; np=$(no_proxy_value "$BRIDGE")
    BOX_ENV=(--env "HTTP_PROXY=http://$BRIDGE:$PROTO_PORT" --env "HTTPS_PROXY=http://$BRIDGE:$PROTO_PORT"
             --env "http_proxy=http://$BRIDGE:$PROTO_PORT" --env "https_proxy=http://$BRIDGE:$PROTO_PORT"
             --env "NO_PROXY=$np" --env "no_proxy=$np")
}

in_box() { box_env; incus exec "$PROTO_BOX" "${BOX_ENV[@]}" -- "$@"; }
in_box_user() {
    box_env
    incus exec "$PROTO_BOX" --user 1000 --group 1000 --cwd "$BOX_HOME" --env "HOME=$BOX_HOME" \
        --env "USER=$BOX_USER" --env "PATH=$BOX_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" "${BOX_ENV[@]}" -- "$@"
}

# Zapis pliku w pudełku ze stdin: box_write <ścieżka> [właściciel] (domyślnie root).
box_write() {
    incus exec "$PROTO_BOX" -- sh -c 'mkdir -p "$(dirname "$1")" && cat >"$1" && chown "$2" "$1"' sh "$1" "${2:-root:root}"
}

# Pudełko prób z konfiguracją proxy z karty: /etc/environment, apt, drop-in dockerd.
box_ensure() {
    if box_exists; then
        echo "   pudełko $PROTO_BOX już jest — używam"
    else
        run incus launch "$PROTO_IMAGE" "$PROTO_BOX" -c security.nesting=true
    fi
    if [[ $(incus list "$PROTO_BOX" -c s -f csv) != RUNNING ]]; then
        run incus start "$PROTO_BOX"
    fi
    local i
    for i in $(seq 60); do
        incus exec "$PROTO_BOX" -- hostname -I 2>/dev/null | grep -q '[0-9]' && break
        sleep 1
    done
    incus exec "$PROTO_BOX" -- systemctl is-system-running --wait 2>/dev/null || true
    if [[ $(incus exec "$PROTO_BOX" -- id -u "$BOX_USER" 2>/dev/null) != 1000 ]]; then
        echo "W pudełku $PROTO_BOX nie ma użytkownika $BOX_USER z uid 1000 — obraz inny niż oczekiwany." >&2
        exit 1
    fi
    local np; np=$(no_proxy_value "$BRIDGE")
    local url="http://$BRIDGE:$PROTO_PORT"
    box_write /etc/environment <<EOF
PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/games:/usr/local/games:/snap/bin"
HTTP_PROXY=$url
HTTPS_PROXY=$url
http_proxy=$url
https_proxy=$url
NO_PROXY=$np
no_proxy=$np
EOF
    box_write /etc/apt/apt.conf.d/90tide-proxy <<EOF
Acquire::http::Proxy "$url/";
Acquire::https::Proxy "$url/";
EOF
    box_write /etc/systemd/system/docker.service.d/tide-proxy.conf <<EOF
[Service]
Environment="HTTP_PROXY=$url" "HTTPS_PROXY=$url" "NO_PROXY=$np"
EOF
    echo "   pudełko $PROTO_BOX: adres $(incus exec "$PROTO_BOX" -- hostname -I | awk '{print $1}'), proxy $url"
}

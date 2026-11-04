#!/usr/bin/env bash
# Wspólne funkcje prób pudełka karty (PT-164, docs/card-box-design.md sekcja 10). Źródłowany przez p1.sh, p4.sh,
# p5.sh, p9.sh, measure.sh i cleanup.sh — sam niczego nie uruchamia.
#
# Każda próba pisze log docs/box-prototype/<nazwa>.log: nagłówek, a dla każdego polecenia: polecenie, kod wyjścia,
# czas i skrócone wyjście; na końcu wiersze „WYNIK <klucz>: ok|fail|skip|info — opis” i podsumowanie.
# Polecenia dostają stdin z /dev/null — skrypty o nic nie pytają (poza hasłem sudo dla chwilowych wariantów
# projektu Incusa i trybu PROTO_NET=rules).
#
# Kontener: tide-proto z images:ubuntu/24.04 w projekcie ograniczonym z PT-166 (polecenie incus użytkownika
# w grupie incus trafia przez incus-user do projektu user-<uid>), security.nesting=true,
# security.syscalls.intercept.mknod/setxattr=true, nigdy security.privileged.
#
# Sieć kontenera: tabela inet tide-box z PT-166 zostawia z mostka tylko proxy i DHCP. Gdy próba potrzebuje sieci
# (apt, docker pull, npm ci), net_open ją otwiera na czas próby, net_close zamyka (także w pułapce EXIT):
#   PROTO_NET=proxy (domyślnie) — chwilowe proxy proxy.py na adresie mostka i porcie proxy Tide (ten port tabela
#     już przepuszcza); kontener dostaje HTTP(S)_PROXY, apt i dockerd też. Bez sudo i bez zmian w nftables.
#     Powód (runda 1, 04.11): reguły chwilowe w inet tide-box nie wystarczyły — forward z incusbr0 dalej ginął
#     (najpewniej łańcuch FORWARD Dockera hosta z polityką DROP; iptables-nft, tabela ip filter).
#   PROTO_NET=rules — dokładnie trzy chwilowe reguły z komentarzem „tide-proto” w tabeli inet tide-box (forward
#     z kontenera, forward odpowiedzi, input DNS do dnsmasq na mostku), usuwane po uchwytach; log zawiera tabelę
#     przed i po (muszą być identyczne) i łańcuch FORWARD tabeli ip filter (diagnoza).
#
# Zmienne dla testów (scripts/tests/test_box_prototype.py): PROTO_SETTLE (sekundy czekania, domyślnie 3),
# PROTO_WAIT (limit czekania na start kontenera, domyślnie 180), PROTO_NET, PROTO_PORT (port proxy, domyślnie
# z /etc/tide-box/tide-box.nft albo 18080).
set -euo pipefail

PROTO_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
REPO=$(cd "$PROTO_DIR/../../.." && pwd)
LOG_DIR="$REPO/docs/box-prototype"
BOX=tide-proto
IMAGE=images:ubuntu/24.04
BRIDGE=incusbr0
AGENT_UID=1000
SETTLE=${PROTO_SETTLE:-3}
WAIT=${PROTO_WAIT:-180}
BOX_CONFIG=(-c security.nesting=true -c security.syscalls.intercept.mknod=true
            -c security.syscalls.intercept.setxattr=true)

LOG=""
LAST_RC=0
LAST_OUT=""
LAST_MS=0
FAILS=0
SUMMARY_DONE=0
RESULTS=()
CLEANUPS=()
SUDO=(sudo)
KEEPALIVE_PID=""
NET_OPEN=0
NFT_BEFORE=""
NET_MODE=${PROTO_NET:-proxy}
NET_ENV=()
PROXY_PID=""
PROXY_LOG=""
SUDO_PRIMED=0

# --- log ------------------------------------------------------------------------------------------

now_us() { local t=$EPOCHREALTIME; echo "${t//[!0-9]/}"; }
fmt_ms() { printf '%d.%03d' $(($1 / 1000)) $(($1 % 1000)); }

log_start() {  # log_start <nazwa> <tytuł>
    mkdir -p "$LOG_DIR"
    LOG="$LOG_DIR/$1.log"
    : >"$LOG"
    {
        echo "# $2"
        echo
        echo "- data: $(date -Iseconds)"
        echo "- skrypt: scripts/box/prototype/$1.sh"
        echo "- jądro hosta: $(uname -r)"
        echo "- kontener: $BOX ($IMAGE)"
        echo
    } >>"$LOG"
    echo "== $2"
    echo "   log: ${LOG#"$REPO"/}"
    trap '_on_exit' EXIT
    trap 'exit 130' INT TERM
}

note() {
    echo "$*" >>"$LOG"
    echo "   $*"
}

block() {  # block <tytuł> <treść> — pełny blok (np. tabela nftables) do logu
    {
        echo "--- $1"
        printf '%s\n' "$2"
        echo "--- koniec: $1"
    } >>"$LOG"
}

shorten() {  # ostatnie 30 wierszy wyjścia z wcięciem
    local text n
    text=$(cat)
    [[ -z $text ]] && return 0
    n=$(wc -l <<<"$text")
    if ((n > 30)); then
        echo "    | … (pominięto $((n - 30)) wierszy)"
        tail -n 30 <<<"$text" | sed 's/^/    | /'
    else
        sed 's/^/    | /' <<<"$text"
    fi
}

show_cmd() {  # polecenie w postaci, którą można przepisać do terminala
    local head=$1
    shift
    case "$head" in
        in_box) printf 'incus exec %s -T %s-- %s' "$BOX" "${NET_ENV[*]:+${NET_ENV[*]} }" "$(printf '%q ' "$@")" ;;
        as_agent) printf 'incus exec %s -T %s %s-- %s' "$BOX" "${AGENT_OPTS[*]}" "${NET_ENV[*]:+${NET_ENV[*]} }" \
            "$(printf '%q ' "$@")" ;;
        as_root) printf 'sudo %s' "$(printf '%q ' "$@")" ;;
        *) printf '%s' "$(printf '%q ' "$head" "$@")" ;;
    esac
}

# run <opis> -- <polecenie…>: wykonuje bez stdin, zapisuje polecenie, kod wyjścia, czas i skrócone wyjście.
# Zawsze zwraca 0 — wynik w LAST_RC, LAST_OUT, LAST_MS (ok sprawdza LAST_RC).
run() {
    local desc=$1 start
    shift
    [[ ${1:-} == -- ]] && shift
    echo "   … $desc"
    start=$(now_us)
    set +e
    LAST_OUT=$("$@" </dev/null 2>&1)
    LAST_RC=$?
    set -e
    LAST_MS=$((($(now_us) - start) / 1000))
    {
        echo "\$ $(show_cmd "$@")"
        echo "  [$desc] kod wyjścia: $LAST_RC, czas: $(fmt_ms "$LAST_MS") s"
        shorten <<<"$LAST_OUT"
    } >>"$LOG"
    printf '     kod %s, %s s\n' "$LAST_RC" "$(fmt_ms "$LAST_MS")"
    return 0
}

ok() { [[ $LAST_RC -eq 0 ]]; }

result() {  # result <klucz> <ok|fail|skip|info> <opis>
    RESULTS+=("$1|$2|$3")
    [[ $2 == fail ]] && FAILS=$((FAILS + 1))
    {
        echo
        echo "WYNIK $1: $2 — $3"
        echo
    } >>"$LOG"
    echo "   WYNIK $1: $2 — $3"
}

at_exit() { CLEANUPS+=("$1"); }

cleanup_all() {
    local i
    set +e
    for ((i = ${#CLEANUPS[@]} - 1; i >= 0; i--)); do
        local c=${CLEANUPS[$i]}
        unset 'CLEANUPS[$i]'
        eval "$c"
    done
    CLEANUPS=()
    if [[ -n $KEEPALIVE_PID ]]; then
        kill "$KEEPALIVE_PID" 2>/dev/null
        KEEPALIVE_PID=""
    fi
    set -e
}

summary() {
    local r
    SUMMARY_DONE=1
    {
        echo
        echo "## Podsumowanie"
        echo
        for r in "${RESULTS[@]}"; do
            IFS='|' read -r k s d <<<"$r"
            echo "- $k: $s — $d"
        done
        echo
        echo "Niepowodzeń: $FAILS"
    } >>"$LOG"
}

finish() {  # finish [kod] — sprzątanie, podsumowanie, wyjście (1, gdy któraś próba nie działa)
    local code=${1:-}
    cleanup_all
    summary
    [[ -z $code ]] && { [[ $FAILS -eq 0 ]] && code=0 || code=1; }
    echo "   koniec: $FAILS niepowodzeń, log ${LOG#"$REPO"/}"
    exit "$code"
}

_on_exit() {
    local code=$?
    cleanup_all
    if [[ $SUMMARY_DONE -eq 0 && -n $LOG ]]; then
        result przerwanie fail "skrypt przerwany (kod $code) — sprzątanie wykonane w pułapce EXIT"
        summary
    fi
}

die() {
    result warunki fail "$*"
    finish 2
}

# --- host i kontener ------------------------------------------------------------------------------

AGENT_OPTS=(--user "$AGENT_UID" --group "$AGENT_UID" --cwd /home/agent --env HOME=/home/agent
            --env "XDG_RUNTIME_DIR=/run/user/$AGENT_UID"
            --env "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$AGENT_UID/bus")

# NET_ENV: zmienne proxy, gdy sieć próby jest otwarta (net_open w trybie proxy).
in_box() { incus exec "$BOX" -T "${NET_ENV[@]}" -- "$@"; }
as_agent() { incus exec "$BOX" -T "${AGENT_OPTS[@]}" "${NET_ENV[@]}" -- "$@"; }
as_root() { "${SUDO[@]}" "$@"; }

require_incus() {
    run "dostęp do Incusa (incus-user, projekt user-$(id -u))" -- incus info
    ok || die "incus nie odpowiada — użytkownik w grupie incus i ponowne zalogowanie (PT-166, make box-install)"
    run "wersja Incusa" -- incus version
}

sudo_prime() {  # raz pyta o hasło (terminal albo askpass), potem odświeża znacznik w tle
    [[ $SUDO_PRIMED -eq 1 ]] && return 0
    SUDO_PRIMED=1
    if [[ $EUID -eq 0 ]]; then
        SUDO=()
        return 0
    fi
    if sudo -n true 2>/dev/null; then
        SUDO=(sudo -n)
    else
        if ! (: </dev/tty) 2>/dev/null; then
            if [[ -z ${SUDO_ASKPASS:-} ]]; then
                local a
                for a in /usr/bin/ksshaskpass /usr/bin/ssh-askpass /usr/lib/ssh/x11-ssh-askpass; do
                    [[ -x $a ]] && { export SUDO_ASKPASS=$a; break; }
                done
            fi
            [[ -n ${SUDO_ASKPASS:-} ]] || die "sudo wymaga hasła, a nie ma terminala ani askpass"
            SUDO=(sudo -A)
        fi
        echo "   sudo: hasło jest potrzebne do chwilowych zmian (nftables albo ustawienia projektu Incusa)"
        "${SUDO[@]}" -v </dev/null || die "sudo -v nie przeszło"
    fi
    ( while sleep 60; do sudo -n -v 2>/dev/null || exit 0; done ) </dev/null >/dev/null 2>&1 &
    KEEPALIVE_PID=$!
}

require_table() {  # tryb proxy bez sudo: wystarczy, że jednostka z PT-166 wczytała reguły; rules — pełna tabela
    if [[ $NET_MODE == rules ]]; then
        sudo_prime
        run "tabela inet tide-box z PT-166" -- as_root nft list table inet tide-box
    else
        run "reguły inet tide-box wczytane (jednostka z PT-166)" -- systemctl is-active tide-box-nft.service
    fi
    ok || die "brak tabeli inet tide-box — najpierw make box-install (PT-166)"
}

box_exists() { incus info "$BOX" >/dev/null 2>&1 </dev/null; }

box_status() { incus info "$BOX" </dev/null 2>/dev/null | sed -n 's/^Status: *//p' | head -1; }

box_ip() {  # adres IPv4 eth0 kontenera (czeka na DHCP)
    local i ip
    for ((i = 0; i < WAIT; i++)); do
        ip=$(incus list "$BOX" -c 4 -f csv </dev/null 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+){3} \(eth0\)' | head -1 | cut -d' ' -f1)
        [[ -n $ip ]] && { echo "$ip"; return 0; }
        sleep 1
    done
    return 1
}

bridge_cidr() { ip -4 -o addr show dev "$BRIDGE" </dev/null | awk '{print $4}' | head -1; }

systemd_ready() {  # czeka, aż systemd kontenera skończy start (running/degraded); tuż po starcie nie ma jeszcze
    local i out=""   # szyny D-Bus („Failed to connect to bus”) — wtedy ponawia co 0,5 s
    for ((i = 0; i < WAIT * 2; i++)); do
        out=$(timeout "$WAIT" incus exec "$BOX" -T -- systemctl is-system-running --wait 2>&1 </dev/null) || true
        case "$out" in  # dokładnie: „Error: Instance is not running” też zawiera „running”
            running | degraded) echo "$out (próba $((i + 1)))"; return 0 ;;
        esac
        sleep 0.5
    done
    echo "$out"
    return 1
}

wait_box() {  # systemd w kontenerze skończył start i jest adres
    run "czekanie na start systemd w kontenerze" -- systemd_ready
    ok || note "uwaga: systemd nie skończył startu: $LAST_OUT"
    box_ip >/dev/null || die "kontener $BOX nie dostał adresu IPv4 w $WAIT s"
}

BOX_VARIANT=""
ensure_box() {  # kontener istnieje, działa, ma użytkownika agent (uid 1000)
    if box_exists; then
        note "Kontener $BOX już jest — używam go."
        run "konfiguracja kontenera" -- incus config show "$BOX"
    else
        run "utworzenie kontenera (nesting, intercept mknod/setxattr)" -- incus launch "$IMAGE" "$BOX" "${BOX_CONFIG[@]}"
        if ok; then
            BOX_VARIANT="nesting+intercept"
        else
            note "Utworzenie z intercept odrzucone — ponawiam z samym nesting (intercept ocenia P-5)."
            box_exists && run "usunięcie nieudanego kontenera" -- incus delete --force "$BOX"
            run "utworzenie kontenera (sam nesting)" -- incus launch "$IMAGE" "$BOX" -c security.nesting=true
            ok || die "nie da się utworzyć kontenera $BOX"
            BOX_VARIANT="nesting"
        fi
        result kontener info "utworzony: $BOX_VARIANT"
    fi
    if [[ $(box_status) != RUNNING ]]; then
        run "start kontenera" -- incus start "$BOX"
        if ! ok && [[ -n $(incus config get "$BOX" raw.idmap </dev/null 2>/dev/null) ]]; then
            # Pozostałość wariantu raw.idmap z p4 (runda 2): bez root:1000:1 w /etc/subuid kontener nie wstaje.
            note "Kontener nie startuje z raw.idmap pozostawionym przez p4 — zdejmuję raw.idmap."
            run "zdjęcie pozostawionego raw.idmap" -- incus config unset "$BOX" raw.idmap
            run "start kontenera bez raw.idmap" -- incus start "$BOX"
        fi
        ok || die "kontener $BOX nie startuje"
    fi
    wait_box
    ensure_agent
}

ensure_agent() {  # użytkownik agent z uid 1000 (obraz może mieć ubuntu z uid 1000)
    run "użytkownik agent (uid $AGENT_UID)" -- in_box bash -c "
        set -e
        name=\$(id -un $AGENT_UID 2>/dev/null || true)
        if [ -z \"\$name\" ]; then
            useradd -m -u $AGENT_UID -U -s /bin/bash agent
        elif [ \"\$name\" != agent ]; then
            usermod -l agent -d /home/agent -m \"\$name\"
            groupmod -n agent \"\$(id -gn $AGENT_UID)\" 2>/dev/null || true
        fi
        id agent"
    ok || die "nie da się przygotować użytkownika agent w kontenerze"
}

ensure_pkgs() {  # ensure_pkgs <pakiet…> — apt w kontenerze, z siecią chwilową tylko gdy czegoś brakuje
    run "pakiety w kontenerze: $*" -- in_box dpkg-query -W -f '${Package} ${Status}\n' "$@"
    local p missing=()
    for p in "$@"; do
        grep -q "^$p install ok installed" <<<"$LAST_OUT" || missing+=("$p")
    done
    [[ ${#missing[@]} -eq 0 ]] && return 0
    net_open
    run "apt-get update w kontenerze" -- in_box apt-get update
    ok || die "apt-get update w kontenerze nie przeszło"
    run "apt-get install ${missing[*]}" -- in_box env DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${missing[@]}"
    ok || die "apt-get install ${missing[*]} w kontenerze nie przeszło"
}

# --- chwilowe reguły nftables (tylko tabela inet tide-box, komentarz tide-proto) ---------------------

proto_rules() {  # wypisuje „<łańcuch> <uchwyt>” reguł z komentarzem tide-proto
    awk '/^[[:space:]]*chain /{c=$2}
         /comment "tide-proto"/{for(i=1;i<=NF;i++) if($i=="handle") print c, $(i+1)}' <<<"$1"
}

net_open() {
    [[ $NET_OPEN -eq 1 ]] && return 0
    if [[ $NET_MODE == proxy ]]; then
        proxy_open
    else
        rules_open
    fi
}

net_close() {
    [[ $NET_OPEN -eq 1 ]] || return 0
    NET_OPEN=0
    if [[ $NET_MODE == proxy ]]; then
        proxy_close
    else
        rules_close
    fi
}

# --- sieć przez chwilowe proxy (domyślnie) -------------------------------------------------------------

proxy_port() {
    local p=${PROTO_PORT:-}
    [[ -n $p ]] || p=$(sed -n 's/.*tcp dport \([0-9]*\) accept comment "proxy Tide".*/\1/p' \
        /etc/tide-box/tide-box.nft 2>/dev/null | head -1)
    echo "${p:-18080}"
}

proxy_open() {
    local cidr bridge port url i
    cidr=$(bridge_cidr)
    [[ -n $cidr ]] || die "brak adresu IPv4 mostka $BRIDGE"
    bridge=${cidr%/*}
    port=$(proxy_port)
    PROXY_LOG=$(mktemp "${TMPDIR:-/tmp}/tide-proto-proxy-XXXXXX")
    python3 "$PROTO_DIR/proxy.py" "$bridge" "$port" "$PROXY_LOG" </dev/null >/dev/null 2>&1 &
    PROXY_PID=$!
    NET_OPEN=1
    at_exit net_close
    for ((i = 0; i < 20; i++)); do grep -q '^ready$' "$PROXY_LOG" && break; sleep 0.25; done
    grep -q '^ready$' "$PROXY_LOG" || die "chwilowe proxy nie słucha na $bridge:$port (port zajęty?)"
    url="http://$bridge:$port"
    NET_ENV=(--env "http_proxy=$url" --env "https_proxy=$url" --env "HTTP_PROXY=$url" --env "HTTPS_PROXY=$url"
             --env "no_proxy=localhost,127.0.0.1,::1" --env "NO_PROXY=localhost,127.0.0.1,::1")
    note "Sieć: chwilowe proxy scripts/box/prototype/proxy.py na $bridge:$port (port proxy Tide, przepuszczany przez inet tide-box); bez zmian w nftables."
    run "apt w kontenerze przez proxy" -- in_box bash -c "printf '%s\n' 'Acquire::http::Proxy \"$url\";' \
        'Acquire::https::Proxy \"$url\";' 'Acquire::http::Pipeline-Depth \"0\";' >/etc/apt/apt.conf.d/95tide-proto"
}

box_docker_proxy() {  # dockerd w kontenerze ciągnie obrazy przez chwilowe proxy (drop-in jak w #16)
    [[ $NET_MODE == proxy && $NET_OPEN -eq 1 ]] || return 0
    local url=${NET_ENV[1]#http_proxy=}
    run "dockerd w kontenerze przez proxy (drop-in)" -- in_box bash -c "
        mkdir -p /etc/systemd/system/docker.service.d
        printf '%s\n' '[Service]' 'Environment=HTTP_PROXY=$url' 'Environment=HTTPS_PROXY=$url' \
            'Environment=NO_PROXY=localhost,127.0.0.1,::1' >/etc/systemd/system/docker.service.d/tide-proto.conf
        systemctl daemon-reload && systemctl restart docker"
}

proxy_close() {
    kill "$PROXY_PID" 2>/dev/null || true
    NET_ENV=()
    local hosts
    hosts=$(grep -v '^ready$' "$PROXY_LOG" 2>/dev/null | sort | uniq -c | sort -rn || true)
    block "połączenia przez chwilowe proxy (liczba, metoda, cel)" "$hosts"
    rm -f "$PROXY_LOG"
    result siec-proxy info "chwilowe proxy zamknięte; celów: $(grep -c . <<<"$hosts" || true); nftables bez zmian"
}

# --- sieć przez chwilowe reguły nftables (PROTO_NET=rules) ---------------------------------------------

rules_open() {
    local ip
    sudo_prime
    ip=$(box_ip) || die "brak adresu kontenera — nie otwieram sieci"
    run "diagnoza: łańcuch FORWARD tabeli ip filter (Docker hosta)" -- as_root nft list chain ip filter FORWARD
    NFT_BEFORE=$(as_root nft list table inet tide-box </dev/null) || die "nie da się odczytać tabeli inet tide-box"
    block "nft list table inet tide-box (przed regułami chwilowymi)" "$NFT_BEFORE"
    NET_OPEN=1
    at_exit net_close
    note "Sieć: chwilowe reguły tide-proto w tabeli inet tide-box dla $ip (PROTO_NET=rules)."
    run "reguła chwilowa: ruch wychodzący kontenera" -- as_root nft insert rule inet tide-box forward \
        "iifname \"$BRIDGE\" ip saddr $ip accept comment \"tide-proto\""
    ok || die "nie da się wstawić reguły chwilowej"
    run "reguła chwilowa: odpowiedzi do kontenera" -- as_root nft insert rule inet tide-box forward \
        "oifname \"$BRIDGE\" ip daddr $ip ct state established,related accept comment \"tide-proto\""
    ok || die "nie da się wstawić reguły chwilowej"
    run "reguła chwilowa: DNS przez dnsmasq na mostku" -- as_root nft insert rule inet tide-box input \
        "iifname \"$BRIDGE\" ip saddr $ip meta l4proto { tcp, udp } th dport 53 accept comment \"tide-proto\""
    ok || die "nie da się wstawić reguły chwilowej"
}

rules_close() {
    local listing chain handle after
    listing=$(as_root nft -a list table inet tide-box </dev/null)
    while read -r chain handle; do
        [[ -n $chain ]] || continue
        run "usunięcie reguły chwilowej ($chain, uchwyt $handle)" -- as_root nft delete rule inet tide-box "$chain" handle "$handle"
    done < <(proto_rules "$listing")
    after=$(as_root nft list table inet tide-box </dev/null)
    block "nft list table inet tide-box (po usunięciu reguł chwilowych)" "$after"
    if [[ $after == "$NFT_BEFORE" ]] && ! grep -q tide-proto <<<"$after"; then
        result nft-przed-po ok "tabela inet tide-box przed i po identyczna, brak tide-proto"
    else
        result nft-przed-po fail "tabela inet tide-box po próbie różni się od stanu przed (diff w logu)"
        block "diff przed/po" "$(diff <(echo "$NFT_BEFORE") <(echo "$after") || true)"
    fi
}

# --- chwilowe ustawienia projektu ograniczonego (wariant prób, cofane na końcu) ---------------------------

PROJECT="user-$(id -u)"
declare -A PROJECT_OLD=()

project_allow() {  # project_allow <klucz> <wartość> — sudo incus project set, cofane w pułapce EXIT
    local key=$1 value=$2
    sudo_prime
    run "$key (przed zmianą)" -- as_root incus project get "$PROJECT" "$key"
    PROJECT_OLD[$key]=$LAST_OUT
    at_exit "project_restore $key"
    run "wariant: $key=$value w projekcie $PROJECT (chwilowo)" -- as_root incus project set "$PROJECT" "$key=$value"
    ok
}

project_restore() {
    local key=$1 old=${PROJECT_OLD[$1]:-}
    if [[ -n $old ]]; then
        run "cofnięcie $key=$old" -- as_root incus project set "$PROJECT" "$key=$old"
    else
        run "cofnięcie $key (wartość domyślna)" -- as_root incus project unset "$PROJECT" "$key"
    fi
}

intercept_on() {  # czy kontener ma intercept mknod i setxattr
    [[ $(incus config get "$BOX" security.syscalls.intercept.mknod </dev/null 2>/dev/null) == true &&
       $(incus config get "$BOX" security.syscalls.intercept.setxattr </dev/null 2>/dev/null) == true ]]
}

enable_intercept() {  # wariant: restricted.containers.interception=allow i intercept w kontenerze, cofane na końcu
    project_allow restricted.containers.interception allow || return 1
    run "intercept mknod/setxattr w kontenerze" -- incus config set "$BOX" \
        security.syscalls.intercept.mknod=true security.syscalls.intercept.setxattr=true
    ok || return 1
    at_exit disable_intercept
    run "restart kontenera z intercept" -- incus restart "$BOX"
    wait_box
}

disable_intercept() {  # przed cofnięciem projektu — inaczej kontener nie wystartuje w projekcie bez interception
    run "zdjęcie intercept mknod" -- incus config unset "$BOX" security.syscalls.intercept.mknod
    run "zdjęcie intercept setxattr" -- incus config unset "$BOX" security.syscalls.intercept.setxattr
}

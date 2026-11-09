#!/bin/bash
# Czerwona lista PT-191. Uruchamiać wyłącznie przez tide box run, jako agent w prawdziwym pudełku.
# Brak narzędzia nie jest dowodem odmowy. Wyjście 1 oznacza co najmniej jedno „przeszło”.
set -uo pipefail

if [[ $(id -un) != agent || -z ${TIDE_BOX_NAME:-} || -z ${TIDE_HOST_PID_NS:-} ]]; then
    echo 'check.sh: wymagany użytkownik agent i środowisko tide box run' >&2
    exit 2
fi
host_ip=${TIDE_BOX_BRIDGE:-}
spoof_ip=
spoof_image=
prefix=24
iface=eth0
while (($#)); do
    case $1 in
        --host-ip|--spoof-ip|--spoof-image|--prefix|--interface)
            (($# >= 2)) || exit 2
            case $1 in
                --host-ip) host_ip=$2 ;;
                --spoof-ip) spoof_ip=$2 ;;
                --spoof-image) spoof_image=$2 ;;
                --prefix) prefix=$2 ;;
                --interface) iface=$2 ;;
            esac
            shift 2 ;;
        *) echo "check.sh: nieznana opcja $1" >&2; exit 2 ;;
    esac
done
[[ $host_ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
[[ -z $spoof_ip || $spoof_ip =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] || exit 2
[[ $prefix =~ ^[0-9]+$ && $prefix -ge 1 && $prefix -le 32 ]] || exit 2
[[ $iface =~ ^[a-zA-Z0-9_.-]+$ ]] || exit 2

failures=0
skipped=0
row() {
    printf '| %s | %s | %s |\n' "$1" "$2" "$3"
    [[ $2 != przeszło ]] || failures=$((failures + 1))
    [[ $2 != 'nie dotyczy' ]] || skipped=$((skipped + 1))
}
probe() {
    local label=$1 tool=$2 code
    shift 2
    if ! command -v "$tool" >/dev/null 2>&1; then
        row "$label" 'nie dotyczy' "brak narzędzia $tool"
        return
    fi
    timeout 12 "$@" >/dev/null 2>&1
    code=$?
    case $code in
        0) row "$label" przeszło 'polecenie powiodło się' ;;
        125|126|127) row "$label" 'nie dotyczy' "nie uruchomiono próby (kod $code)" ;;
        *) row "$label" odmowa "kod $code" ;;
    esac
}
command -v timeout >/dev/null || { echo 'check.sh: brak timeout' >&2; exit 2; }
printf '| Punkt | Wynik | Dowód / ograniczenie |\n| --- | --- | --- |\n'
probe 'SSH hosta' ls ls "${TIDE_HOST_HOME}/.ssh"
probe 'git-credentials hosta' cat cat "${TIDE_HOST_HOME}/.git-credentials"
probe 'główny worktree (/apps, jeśli istnieje na hoście)' ls ls "${TIDE_HOST_MAIN_PROBE:-${TIDE_HOST_REPO}/apps}"
for domain in example.com github.com; do
    probe "curl https://$domain (proxy kroku)" curl curl --fail --silent --output /dev/null \
        --connect-timeout 3 --max-time 8 "https://$domain"
    probe "curl https://$domain (bez proxy)" curl curl --noproxy '*' --fail --silent --output /dev/null \
        --connect-timeout 3 --max-time 8 "https://$domain"
done
probe 'DNS github.com' nslookup nslookup -timeout=3 -retry=1 github.com
probe 'Docker hosta' docker docker -H unix:///host/docker.sock ps
probe 'D-Bus sesji hosta' dbus-send dbus-send --session --print-reply --reply-timeout=3000 \
    --dest=org.freedesktop.DBus /org/freedesktop/DBus org.freedesktop.DBus.ListNames
if git remote get-url origin >/dev/null 2>&1; then
    probe 'git push origin HEAD' git git push origin HEAD
else
    row 'git push origin HEAD' 'nie dotyczy' 'brak origin — nie sprawdzono granicy push'
fi
if command -v gh >/dev/null 2>&1; then
    gh auth status >/dev/null 2>&1
    row 'gh auth status (brak programu)' przeszło 'gh jest zainstalowany (niezależnie od stanu logowania)'
else
    row 'gh auth status (brak programu)' odmowa 'gh nie jest zainstalowany'
fi
# /proc/1 wewnątrz kontenera oznacza jego init, nie init hosta. Nie nazywamy odczytu init kontenera wyciekiem.
cat /proc/1/environ >/dev/null 2>&1
proc_code=$?
pid_ns=$(readlink /proc/self/ns/pid)
if [[ -n $pid_ns && $pid_ns != "$TIDE_HOST_PID_NS" ]]; then
    row '/proc/1/environ hosta' odmowa "inna przestrzeń PID niż host; odczyt init pudełka: kod $proc_code"
elif [[ -z $pid_ns ]]; then
    row '/proc/1/environ hosta' 'nie dotyczy' 'nie odczytano przestrzeni PID'
else
    row '/proc/1/environ hosta' przeszło 'przestrzeń PID hosta widoczna'
fi
# Istniejący, chroniony katalog systemowy, a nie błędna ścieżka z brakującym rodzicem.
probe 'zapis poza domem, worktree i .git (/etc)' sh sh -c \
    'umask 077; set -C; file=/etc/tide-box-check-$$; printf test > "$file" || exit 1; rm -f -- "$file"' tide-write
probe "Postgres hosta $host_ip:5432" nc nc -z -w 3 "$host_ip" 5432

# Kod 10: curl rzeczywiście wykonał próbę po nadaniu adresu i skończył odmową połączenia/timeoutem.
# Kod 77: brak możliwości próby; HTTP 403 także jest porażką granicy NIC (pakiet doszedł do proxy).
spoof_body='
command -v ip >/dev/null && command -v curl >/dev/null || exit 77
ip addr add "$1/$2" dev "$3" || exit 77
cleanup() { ip addr del "$1/$2" dev "$3" || exit 78; }
trap '\''cleanup "$1" "$2" "$3"'\'' EXIT
trap '\''exit 77'\'' HUP INT TERM
curl --interface "$1" --noproxy "" -x "$4" --connect-timeout 3 --max-time 8 --fail --silent --output /dev/null https://registry.npmjs.org/
code=$?
case $code in
    7|28) exit 10 ;;
    3|4|5|6|45|48|49) exit 77 ;;  # niepoprawne wywołanie, proxy/DNS albo bind — bez dowodu wysłania
    *) exit 0 ;;                  # także błąd TLS/odbioru: połączenie opuściło filtrowaną kartę
esac
'
if [[ -z $spoof_ip ]]; then
    row 'podszycie IP + curl --interface' 'nie dotyczy' 'brak wskazanego, sprawdzonego wolnego IP (--spoof-ip)'
else
    proxy_port=${HTTPS_PROXY##*:}
    proxy_base="http://${TIDE_BOX_BRIDGE}:${proxy_port%/}"
    if command -v sudo >/dev/null && sudo -n true >/dev/null 2>&1; then
        timeout 20 sudo -n sh -c "$spoof_body" tide-spoof "$spoof_ip" "$prefix" "$iface" "$proxy_base" >/dev/null 2>&1
        spoof_code=$?
    elif command -v docker >/dev/null && [[ -n $spoof_image ]]; then
        timeout 20 docker run --rm --pull=never --privileged --net=host "$spoof_image" \
            sh -c "$spoof_body" tide-spoof "$spoof_ip" "$prefix" "$iface" "$proxy_base" >/dev/null 2>&1
        spoof_code=$?
    else
        spoof_code=77
    fi
    case $spoof_code in
        10) row 'podszycie IP + curl --interface' odmowa "$spoof_ip: próba po nadaniu adresu; wymagane potwierdzenie braku wpisu proxy na hoście" ;;
        0) row 'podszycie IP + curl --interface' przeszło "$spoof_ip: pakiet dotarł do proxy" ;;
        78) row 'podszycie IP + curl --interface' przeszło 'błąd usuwania próbnego adresu — wymagana kontrola' ;;
        *) row 'podszycie IP + curl --interface' 'nie dotyczy' "brak roota/narzędzi/obrazu lub nie wykonano pełnej próby (kod $spoof_code)" ;;
    esac
fi
if [[ ${TIDE_BOX_DOCKER:-0} == 1 && ${TIDE_BOX_K3_CLAUDE:-0} != 1 ]]; then
    row 'cat ~/.claude/.credentials.json z Bash agenta' 'nie dotyczy' 'ryzyko resztkowe K1: Docker bez K3'
else
    row 'cat ~/.claude/.credentials.json z Bash agenta' 'nie dotyczy' \
        'wymaga osobnej próby narzędziem Bash rzeczywistego agenta (P-2/K3); zwykły shell jej nie zastępuje'
fi
printf '\nPrzeszło: %s; nie dotyczy: %s. Pominięte próby wymagają osobnego dowodu.\n' "$failures" "$skipped"
((failures == 0))

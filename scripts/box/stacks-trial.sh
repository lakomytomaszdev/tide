#!/bin/bash
# Pierwsza prawdziwa budowa pudełek projektu z szablonów stosów (PT-192, #29) — próby ręczne z ticketu:
#   A. `tide box build plasma-tide` z tymczasowym profilem python-qt: czas (próg US-4 ≤ 15 min), status (hash,
#      „filtrowanie NIC: tak”), claude/codex --version jako agent, verify, dziennik odmów budowy;
#      `tide box reset plasma-tide` (próg ≤ 60 s) z plikiem w /home/agent sprzed resetu,
#   B. `tide box build tide-stacks-test` z profilem dotnet + docker: `systemctl show docker -p Environment`,
#      `docker info` jako agent, dziennik odmów; pudełko usuwane po próbie.
# Uruchomienie (z katalogu repozytorium, poza piaskownicą agenta): `bash scripts/box/stacks-trial.sh`.
# Log: docs/box-prototype/pt192-<czas>.log (+ katalog z dziennikami odmów). Profile próbne są tymczasowe: na końcu
# (także po błędzie) pudełka i profile próbne są usuwane, chyba że KEEP_BOXES=1.
#
# Zmiana hosta tylko chwilowa i cofana na końcu: `restricted.snapshots=allow` w projekcie Incusa (sudo) — projekt
# z PT-166 blokuje migawki, a budowa kończy się migawką clean (decyzja migawki-w-projekcie; na stałe zrobi to PT-182).
# Bez terminala sudo używa SUDO_ASKPASS albo dostępnego ksshaskpass/ssh-askpass (okno hasła także przy cofaniu,
# jeśli zgoda wygasła). Hasło nie przechodzi przez stdin próby ani jej dziennik. Bez pomocnika: tylko sudo -n.
# Budowa idzie przez własne proxy Tide polecenia `tide box build` (port box.proxy-port) — zatrzymaj usługę Tide
# z box.enabled na czas próby, jeśli trzyma ten port. Obraz bazowy `tide-base` musi istnieć (`make box-base`).
set -uo pipefail

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PROJECT=${TIDE_BOX_PROJECT:-user-$(id -u)}
STAMP=$(date +%Y-%m-%dT%H%M%S)
OUT="$ROOT/docs/box-prototype/pt192-$STAMP"
LOG="$OUT.log"
STATE=${XDG_STATE_HOME:-$HOME/.local/state}/tide/box
BOARD=${TIDE_BOARD_DIR:-$HOME/DevWorkspace/tide-board}
BUILD_LIMIT=900
RESET_LIMIT=60
SNAP_OLD=""
SNAP_CHANGED=0
FAILURES=0
SUDO=(sudo)
CREATED=()       # sluga z profilem założonym przez próbę (do usunięcia na końcu)

mkdir -p "$OUT"
exec > >(tee -a "$LOG") 2>&1

tide() { python3 "$ROOT/daemon/tide.py" box "$@" </dev/null; }
in_box() { incus exec --project "$PROJECT" "$@" </dev/null; }
section() { printf '\n===== %s =====\n' "$*"; }
result() {
    printf 'WYNIK %s: %s — %s\n' "$1" "$2" "$3"
    if [[ $2 == fail || $2 == over ]]; then
        FAILURES=$((FAILURES + 1))
    fi
}
now() { date +%s.%N; }
since() { LC_ALL=C awk -v a="$1" -v b="$(now)" 'BEGIN { printf "%.1f", b - a }'; }

select_sudo() {
    # Ten sam sposób uwierzytelnienia przy zmianie i sprzątaniu; bez uruchamiania budowy jako root hosta.
    if (: </dev/tty) 2>/dev/null; then
        return 0
    fi
    if [[ -z ${SUDO_ASKPASS:-} ]]; then
        local helper
        for helper in /usr/bin/ksshaskpass /usr/bin/ssh-askpass /usr/lib/ssh/x11-ssh-askpass; do
            if [[ -x $helper ]]; then
                export SUDO_ASKPASS=$helper
                break
            fi
        done
    fi
    if [[ -n ${SUDO_ASKPASS:-} ]]; then
        SUDO=(sudo -A)
        echo "sudo: bez terminala używam okna hasła (askpass) do zmiany i cofnięcia ustawienia migawek"
    else
        SUDO=(sudo -n)
        echo "sudo: brak terminala i askpass — możliwa tylko wcześniej przyznana zgoda"
    fi
}

cleanup() {
    local code=$?
    section "sprzątanie"
    local slug
    for slug in "${CREATED[@]}"; do
        if [[ ${KEEP_BOXES:-0} == 1 ]]; then
            echo "KEEP_BOXES=1: pudełko i profil $slug zostają"
            continue
        fi
        if ! tide destroy "$slug" --yes; then
            result "cleanup-$slug" fail "nie usunięto pudełka — profil próbny zostaje do ponowienia sprzątania"
            continue
        fi
        if ! rm -f "$BOARD/projects/$slug/box.yaml"; then
            result "cleanup-profile-$slug" fail "nie usunięto profilu próbnego"
            continue
        fi
        echo "profil próbny $slug usunięty ($BOARD/projects/$slug/box.yaml); dziennik pudełka zostaje w Tablicy"
    done
    if (( SNAP_CHANGED )); then
        local restored=0
        if [[ -n $SNAP_OLD ]]; then
            "${SUDO[@]}" incus project set "$PROJECT" "restricted.snapshots=$SNAP_OLD" && restored=1
        else
            "${SUDO[@]}" incus project unset "$PROJECT" restricted.snapshots && restored=1
        fi
        if (( restored )); then
            echo "restricted.snapshots cofnięte: $(incus project get "$PROJECT" restricted.snapshots </dev/null)"
        else
            result cleanup-snapshots fail "nie cofnięto restricted.snapshots"
        fi
    fi
    echo "log: $LOG"
    if (( FAILURES > 0 )); then
        echo "nieudane sprawdzenia: $FAILURES"
        code=1
    fi
    exit "$code"
}
trap cleanup EXIT

profile_file() {  # profile_file <slug> <treść> — plik tymczasowy z profilem
    local file="$OUT/$1.box.yaml"
    printf '%s\n' "$2" > "$file"
    echo "$file"
}

approve() {  # approve <slug> <plik>
    if [[ -e $BOARD/projects/$1/box.yaml ]]; then
        result "approve-$1" fail "$1 ma już profil w Tablicy ($BOARD/projects/$1/box.yaml) — próba go nie nadpisze"
        return 1
    fi
    if ! tide approve "$1" --file "$2"; then
        result "approve-$1" fail "nie zatwierdzono profilu próbnego"
        return 1
    fi
    CREATED+=("$1")
}

build() {  # build <slug> — czas budowy, dziennik odmów do katalogu próby
    local start code took
    start=$(now)
    tide build "$1"
    code=$?
    took=$(since "$start")
    cp -f "$STATE/$1/build.log" "$OUT/$1-build.log" 2>/dev/null || true
    cp -f "$STATE/$1/build-denied.log" "$OUT/$1-build-denied.log" 2>/dev/null || true
    echo "budowa $1: kod $code, $took s"
    if (( code != 0 )); then
        result "build-$1" fail "kod $code po $took s (dziennik: $OUT/$1-build.log)"
        return 1
    fi
    if awk -v t="$took" -v l="$BUILD_LIMIT" 'BEGIN { exit !(t <= l) }'; then
        result "build-$1" ok "$took s (próg $BUILD_LIMIT s)"
    else
        result "build-$1" over "$took s > $BUILD_LIMIT s — przyczyna w krokach build.log"
    fi
    denied "$1"
}

denied() {
    local file="$STATE/$1/build-denied.log"
    if [[ -s $file ]]; then
        result "denied-$1" fail "odmowy proxy w budowie:"
        cat "$file"
    else
        result "denied-$1" ok "dziennik odmów budowy pusty"
    fi
}

check() {  # check <nazwa> <polecenie…> — jedno sprawdzenie z wynikiem
    local name=$1
    shift
    if "$@"; then result "$name" ok "$*"; else result "$name" fail "$* (kod $?)"; fi
}

section "środowisko"
date -Is
git -C "$ROOT" log -1 --format='%h %s'
incus version </dev/null
python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from tide_daemon.box import builder; print("STACKS_VERSION", builder.STACKS_VERSION, "suma katalogu", builder.templates_version())' "$ROOT/daemon"
if ! incus image list --project "$PROJECT" --format csv -c l </dev/null | grep -qx tide-base; then
    echo "BŁĄD: brak obrazu tide-base w projekcie $PROJECT — najpierw make box-base" >&2
    exit 1
fi

section "migawki w projekcie (chwilowo)"
if ! SNAP_OLD=$(incus project get "$PROJECT" restricted.snapshots </dev/null); then
    result snapshot-read fail "nie odczytano restricted.snapshots — próba nie zmieni nieznanego ustawienia"
    exit 1
fi
echo "restricted.snapshots przed: '${SNAP_OLD:-<domyślne>}'"
if [[ $SNAP_OLD != allow ]]; then
    select_sudo
    if ! "${SUDO[@]}" incus project set "$PROJECT" restricted.snapshots=allow; then
        result snapshot-auth fail "nie ustawiono restricted.snapshots=allow — próba przerwana przed profilami"
        exit 1
    fi
    SNAP_CHANGED=1
fi

# ---------------------------------------------------------------- A: plasma-tide z python-qt
section "A: plasma-tide (python-qt)"
A=$(profile_file plasma-tide "tide-box: 1
project: plasma-tide
stacks: [python-qt]
verify:
  - python3 -c 'import PyQt6.QtCore, PyQt6.QtGui, PyQt6.QtQml, PyQt6.QtQuick'
  - QT_QPA_PLATFORM=offscreen python3 -c 'from PyQt6.QtGui import QGuiApplication; QGuiApplication([])'")
cat "$A"
if approve plasma-tide "$A" && build plasma-tide; then
    tide status plasma-tide
    status=$(tide status plasma-tide)
    [[ $status == *"stan: clean"* ]] && result status-plasma-tide ok "stan clean" \
        || result status-plasma-tide fail "stan inny niż clean (verify?)"
    [[ $status == *"filtrowanie NIC: tak"* && $status == *"hash: "[0-9a-f]* ]] \
        && result nic-hash-plasma-tide ok "hash i filtrowanie NIC: tak" || result nic-hash-plasma-tide fail "brak hasha albo filtrowania"
    check claude-as-agent in_box plasma-tide --user 1000 --group 1000 --env HOME=/home/agent -- claude --version
    check codex-as-agent in_box plasma-tide --user 1000 --group 1000 --env HOME=/home/agent -- codex --version
    check pyqt-offscreen in_box plasma-tide --user 1000 --group 1000 --env HOME=/home/agent --env QT_QPA_PLATFORM=offscreen \
        -- python3 -c 'from PyQt6.QtGui import QGuiApplication; a = QGuiApplication([]); print(a.platformName())'
    check qt-env in_box plasma-tide -- grep -x 'QT_QPA_PLATFORM="offscreen"' /etc/environment

    section "A: reset z plikiem w /home/agent"
    check reset-marker-home in_box plasma-tide --user 1000 --group 1000 -- sh -c "echo pt192-$STAMP > /home/agent/pt192-marker"
    check reset-marker-rootfs in_box plasma-tide -- touch /root/pt192-poza-home
    start=$(now)
    tide reset plasma-tide || result reset-command fail "tide box reset zakończył się błędem"
    took=$(since "$start")
    awk -v t="$took" -v l="$RESET_LIMIT" 'BEGIN { exit !(t <= l) }' \
        && result reset-plasma-tide ok "$took s (próg $RESET_LIMIT s)" || result reset-plasma-tide over "$took s > $RESET_LIMIT s"
    # Migawka clean jest z działającego pudełka; gdyby po przywróceniu stało, uruchamiamy je (tide box start to #19).
    incus start --project "$PROJECT" plasma-tide </dev/null 2>/dev/null || true
    sleep 3
    marker=$(in_box plasma-tide -- cat /home/agent/pt192-marker 2>&1)
    [[ $marker == "pt192-$STAMP" ]] && result reset-keeps-home ok "plik z /home/agent przetrwał reset" \
        || result reset-keeps-home fail "brak pliku po resecie: $marker"
    in_box plasma-tide -- test -e /root/pt192-poza-home && result reset-restores-rootfs fail "plik poza /home/agent przetrwał" \
        || result reset-restores-rootfs ok "plik poza /home/agent cofnięty"
    status=$(tide status plasma-tide)
    echo "$status"
    [[ $status == *"stan: clean"* ]] && result status-after-reset ok clean || result status-after-reset fail "stan po resecie"
fi

# ---------------------------------------------------------------- B: tide-stacks-test z dotnet + docker
section "B: tide-stacks-test (dotnet + docker)"
B=$(profile_file tide-stacks-test "tide-box: 1
project: tide-stacks-test
stacks: [dotnet, docker]
dotnet:
  sdk: ['8.0.415']
docker:
  enabled: true
verify:
  - dotnet --list-sdks
  - docker info")
cat "$B"
if approve tide-stacks-test "$B" && build tide-stacks-test; then
    status=$(tide status tide-stacks-test)
    echo "$status"
    [[ $status == *"stan: clean"* ]] && result status-tide-stacks-test ok "stan clean" \
        || result status-tide-stacks-test fail "stan inny niż clean (verify?)"
    env_line=$(in_box tide-stacks-test -- systemctl show docker -p Environment)
    echo "$env_line"
    no_proxy=$(python3 -c 'import sys; sys.path.insert(0, sys.argv[1]); from tide_daemon.box import netenv; print(netenv.BOX_NO_PROXY)' "$ROOT/daemon")
    [[ $env_line == *"HTTPS_PROXY=http://"* && $env_line == *"NO_PROXY=$no_proxy"* && $env_line != *DOCKER_HOST* ]] \
        && result dockerd-env ok "proxy i pełne NO_PROXY" || result dockerd-env fail "$env_line"
    check docker-as-agent in_box tide-stacks-test --user 1000 --group 1000 --env HOME=/home/agent -- \
        sg docker -c "docker info --format 'docker {{.ServerVersion}} runtime {{.DefaultRuntime}}'"
    # Proxy polecenia build już nie działa. Sprawdzamy lokalny obraz pobrany przez builder podczas budowy,
    # bez domyślnego pull hello-world:latest po zdjęciu polityki proxy (próba z 18:38, kod 125).
    check docker-image-as-agent in_box tide-stacks-test --user 1000 --group 1000 --env HOME=/home/agent -- \
        sg docker -c "docker image inspect testcontainers/ryuk:0.11.0 --format '{{.Id}}'"
    check dotnet-sdks in_box tide-stacks-test --user 1000 --group 1000 --env HOME=/home/agent -- \
        bash -lc 'dotnet --list-sdks'
    check nuget-config in_box tide-stacks-test -- grep -c 'add key="nuget.org"' /home/agent/.nuget/NuGet/NuGet.Config
fi

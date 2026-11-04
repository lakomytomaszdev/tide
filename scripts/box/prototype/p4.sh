#!/usr/bin/env bash
# P-4 (PT-164): montowanie <proj>/.git i <proj>-worktrees z ext4 /home przez shift=true pod identycznymi
# ścieżkami, nakładki tylko do odczytu na .git/hooks, .git/config, .git/info; git status, git commit na gałęzi
# proto/k-38, npm ci w kontenerze i na hoście; widoczność hosta z kontenera (~/.ssh, ~/.git-credentials,
# główny worktree, gniazdo Dockera hosta, Postgres hosta).
# Użycie: bash scripts/box/prototype/p4.sh   (sieć dla apt i npm przez chwilowe proxy z lib.sh, bez sudo)
#
# Projekt: PROTO_NODE_PROJECT (domyślnie ~/DevWorkspace/toolssuite — node z package-lock.json). Skrypt zakłada
# worktree <proj>-worktrees/proto-k-38 na nowej gałęzi proto/k-38 i usuwa je (z gałęzią) na końcu; główny
# worktree projektu nie jest ruszany ani montowany. npm ci z --ignore-scripts (bez skryptów projektu na hoście).
# Wariant awaryjny, gdy shift=true zawiedzie: raw.idmap "both 1000 1000" bez shift (skrypt próbuje sam).
set -euo pipefail
# shellcheck source=scripts/box/prototype/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

proj=${PROTO_NODE_PROJECT:-$HOME/DevWorkspace/toolssuite}
gitdir="$proj/.git"
wtroot="$proj-worktrees"
wt="$wtroot/proto-k-38"
branch=proto/k-38

log_start p4 "P-4 — worktree i .git z ext4 przez shift=true, nakładki ro, git i npm ci"
[[ -d $gitdir ]] || die "brak $gitdir (PROTO_NODE_PROJECT=<repozytorium>)"
[[ -d $wtroot ]] || die "brak katalogu worktree projektu $wtroot"
[[ ! -e $wt ]] || die "$wt już istnieje — usuń pozostałość poprzedniej próby (cleanup.sh)"
! git -C "$proj" rev-parse --verify --quiet "refs/heads/$branch" >/dev/null || die "gałąź $branch już istnieje"
command -v node >/dev/null || die "brak node na hoście (potrzebny do porównania npm ci)"
node_version=$(node --version)
node_version=${node_version#v}
note "Projekt: $proj, worktree próby: $wt, node hosta: $node_version"
note "System plików: $(df -T "$proj" | awk 'NR==2 {print $2}') ($proj)"

require_incus
require_table
ensure_box
net_open
ensure_pkgs git ca-certificates curl xz-utils netcat-openbsd
run "node $node_version w kontenerze (jak na hoście)" -- in_box bash -c "
    set -e
    if [ \"\$(node --version 2>/dev/null)\" != v$node_version ]; then
        curl -fsSL https://nodejs.org/dist/v$node_version/node-v$node_version-linux-x64.tar.xz \
            | tar -xJ -C /usr/local --strip-components=1
    fi
    node --version; npm --version"
ok || die "node $node_version nie zainstalował się w kontenerze"

# --- worktree próby i montowania ---------------------------------------------------------------------

run "worktree próby na hoście" -- git -C "$proj" worktree add -b "$branch" "$wt" HEAD
ok || die "git worktree add nie przeszło"
remove_worktree() {
    run "usunięcie worktree próby" -- git -C "$proj" worktree remove --force "$wt"
    run "usunięcie gałęzi próby" -- git -C "$proj" branch -D "$branch"
}
at_exit remove_worktree

DEVICES=()
remove_devices() {
    local d
    for d in "${DEVICES[@]}"; do
        run "odmontowanie $d" -- incus config device remove "$BOX" "$d"
    done
    DEVICES=()
}
at_exit remove_devices

mount_all() {  # mount_all <true|false> — shift=true albo bez shift (wariant raw.idmap)
    local shift=$1 extra=()
    [[ $shift == true ]] && extra=(shift=true)
    add() {  # add <nazwa> <ścieżka> [readonly]
        run "montowanie $2 (shift=$shift${3:+, ro})" -- incus config device add "$BOX" "$1" disk \
            "source=$2" "path=$2" "${extra[@]}" ${3:+readonly=true}
        ok || return 1
        DEVICES+=("$1")
    }
    add proto-git "$gitdir" || return 1
    add proto-wt "$wtroot" || return 1
    add proto-hooks "$gitdir/hooks" ro || return 1
    add proto-config "$gitdir/config" ro || return 1
    if [[ -d $gitdir/info ]]; then
        add proto-info "$gitdir/info" ro || return 1
    else
        note "brak $gitdir/info — nakładka pominięta"
    fi
}

idmap=shift
if ! mount_all true; then
    remove_devices
    idmap=raw.idmap
fi
if [[ $idmap == shift ]]; then
    run "właściciel worktree w kontenerze (oczekiwany uid $AGENT_UID)" -- in_box stat -c '%u:%g %n' "$wt"
    if [[ $LAST_OUT != "$AGENT_UID:"* ]]; then
        note "shift=true nie mapuje uid $AGENT_UID — przechodzę na wariant raw.idmap"
        remove_devices
        idmap=raw.idmap
    fi
fi
if [[ $idmap == raw.idmap ]]; then
    run "wariant awaryjny: raw.idmap both $AGENT_UID $AGENT_UID" -- incus config set "$BOX" raw.idmap "both $AGENT_UID $AGENT_UID"
    ok || die "raw.idmap odrzucone — ani shift=true, ani raw.idmap"
    run "restart kontenera po raw.idmap" -- incus restart "$BOX"
    wait_box
    mount_all false || die "montowanie bez shift (raw.idmap) nie przeszło"
    run "właściciel worktree w kontenerze (raw.idmap)" -- in_box stat -c '%u:%g %n' "$wt"
fi
if [[ $LAST_OUT == "$AGENT_UID:"* ]]; then
    result montowanie ok "worktree i .git zamontowane pod identycznymi ścieżkami ($idmap), uid $AGENT_UID ↔ $AGENT_UID"
else
    result montowanie fail "uid w kontenerze: $LAST_OUT ($idmap)"
fi

# --- git ---------------------------------------------------------------------------------------------

# Polecenia agenta w katalogu worktree próby (ten sam uid co krok w pudełku).
AGENT_OPTS=(--user "$AGENT_UID" --group "$AGENT_UID" --cwd "$wt" --env HOME=/home/agent
            --env "XDG_RUNTIME_DIR=/run/user/$AGENT_UID")
run "git status w kontenerze" -- as_agent git status --short --branch
ok && result git-status ok "git status w worktree z kontenera" || result git-status fail "git status: kod $LAST_RC"

run "git add + commit na $branch w kontenerze" -- as_agent bash -c "
    echo 'PT-164: zapis z kontenera' > proto-k-38.txt &&
    git add proto-k-38.txt &&
    git -c user.name='Tide proto' -c user.email=proto@tide.invalid commit -q -m 'proto: commit from the box (PT-164)'"
commit_rc=$LAST_RC
run "commit widoczny na hoście" -- git -C "$wt" log -1 --format='%H %an %s'
host_log=$LAST_OUT
run "właściciel pliku z kontenera na hoście" -- stat -c '%U:%G %n' "$wt/proto-k-38.txt"
owner=$LAST_OUT
run "git status na hoście po commicie z kontenera" -- git -C "$wt" status --short --branch
if [[ $commit_rc -eq 0 && $host_log == *"Tide proto"* && $owner == "$(id -un):"* ]]; then
    result git-commit ok "commit na $branch z kontenera widoczny na hoście, plik należy do $(id -un)"
else
    result git-commit fail "commit z kontenera: kod $commit_rc, host: '$host_log', właściciel: '$owner'"
fi

denied=0
check_ro() {  # check_ro <opis> <polecenie powłoki>
    run "zapis do $1 z kontenera (oczekiwana odmowa)" -- as_agent sh -c "$2"
    if ok; then
        result "ro-$1" fail "zapis do $1 z kontenera przeszedł — nakładka ro nie działa"
    else
        result "ro-$1" ok "zapis do $1 odrzucony: $(tail -n 1 <<<"$LAST_OUT")"
        denied=$((denied + 1))
    fi
}
check_ro hooks "mkdir '$gitdir/hooks/.proto-k-38'"
check_ro config ": >> '$gitdir/config'"
[[ -d $gitdir/info ]] && check_ro info "mkdir '$gitdir/info/.proto-k-38'"
rmdir "$gitdir/hooks/.proto-k-38" "$gitdir/info/.proto-k-38" 2>/dev/null || true

# --- npm ci ------------------------------------------------------------------------------------------

lockdir=$(find "$wt" -maxdepth 3 -name package-lock.json -not -path '*/node_modules/*' -printf '%h\n' | sort | head -1)
if [[ -z $lockdir ]]; then
    result npm-ci skip "w $proj nie ma package-lock.json"
else
    note "npm ci w $lockdir (z --ignore-scripts)"
    run "npm ci na hoście" -- bash -c "cd '$lockdir' && npm ci --ignore-scripts --no-audit --no-fund"
    host_rc=$LAST_RC host_ms=$LAST_MS
    run "npm ci w kontenerze (zimny cache)" -- as_agent bash -c "cd '$lockdir' && npm ci --ignore-scripts --no-audit --no-fund"
    cold_rc=$LAST_RC cold_ms=$LAST_MS
    run "npm ci w kontenerze (ciepły cache)" -- as_agent bash -c "cd '$lockdir' && npm ci --ignore-scripts --no-audit --no-fund"
    warm_rc=$LAST_RC warm_ms=$LAST_MS
    times="host $(fmt_ms "$host_ms") s (kod $host_rc), kontener zimny $(fmt_ms "$cold_ms") s (kod $cold_rc), ciepły $(fmt_ms "$warm_ms") s (kod $warm_rc)"
    if [[ $cold_rc -eq 0 && $warm_rc -eq 0 ]]; then
        result npm-ci ok "npm ci na montowaniu: $times"
    else
        result npm-ci fail "npm ci na montowaniu: $times"
    fi
fi
net_close

# --- co widać z kontenera ------------------------------------------------------------------------------

hide() {  # hide <klucz> <opis> <polecenie w kontenerze…> — polecenie musi zawieść
    local key=$1 desc=$2
    shift 2
    run "$desc (oczekiwany błąd)" -- in_box "$@"
    ok && result "$key" fail "$desc: widoczne z kontenera" || result "$key" ok "$desc: niewidoczne (kod $LAST_RC)"
}
run "katalog domowy hosta widziany z kontenera" -- in_box ls -la "$HOME" "$HOME/DevWorkspace"
hide ukryte-ssh "~/.ssh" test -e "$HOME/.ssh"
hide ukryte-git-credentials "~/.git-credentials" test -e "$HOME/.git-credentials"
run "główny worktree projektu w kontenerze (oczekiwane tylko .git)" -- in_box ls -A "$proj"
[[ $LAST_OUT == ".git" ]] && result ukryty-glowny-worktree ok "w $proj widać tylko .git" \
    || result ukryty-glowny-worktree fail "w $proj widać: $(tr '\n' ' ' <<<"$LAST_OUT")"
run "urządzenia kontenera (gniazdo Dockera hosta?)" -- incus config show "$BOX" --expanded
grep -q 'docker.sock' <<<"$LAST_OUT" && result ukryty-docker-hosta fail "kontener ma urządzenie z docker.sock" \
    || result ukryty-docker-hosta ok "żadne urządzenie kontenera nie wskazuje gniazda Dockera hosta"
host_ips=$(ip -4 -o addr show scope global | awk '$2 !~ /^(docker|br-|veth|virbr)/ {print $4}' | cut -d/ -f1)
for hip in $host_ips; do
    hide "ukryty-postgres-$hip" "Postgres hosta $hip:5432" timeout 10 nc -z -w 3 "$hip" 5432
done
remove_devices
finish

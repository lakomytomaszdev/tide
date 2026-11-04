#!/usr/bin/env bash
# P-1 (PT-164): Docker w nieuprzywilejowanym kontenerze Incusa — docker run hello-world i dotnet test LegalApp
# z Testcontainers (pgvector/pgvector:pg16). Użycie: bash scripts/box/prototype/p1.sh
#
# Sieć: lib.sh net_open/net_close — domyślnie chwilowe proxy na porcie proxy Tide (bez sudo i bez zmian
# w nftables), PROTO_NET=rules — trzy chwilowe reguły tide-proto w inet tide-box (sudo); zamykane w pułapce EXIT.
# Docker w projekcie takim, jaki jest (bez intercept, który projekt ograniczony blokuje — runda 2 pokazała, że
# intercept nie zmienia błędu). Runtime'y po kolei, do pierwszego działającego (runda 3):
#   runc       — runc z docker.io (runda 2: „open sysctl net.ipv4.ip_unprivileged_port_start … reopen fd 8:
#                permission denied” — nowy runc kontra profil AppArmor kontenera),
#   crun       — crun z apt jako domyślny runtime dockerd (/etc/docker/daemon.json),
#   runc-stary — najstarszy runc z repozytoriów Ubuntu 24.04 (sprzed poprawek procfs z 2025; ryzyko tylko
#                w pudełku: root pudełka to nieuprzywilejowany uid hosta).
# Docker CE z repozytorium Dockera pominięty: containerd.io ma ten sam nowy runc.
# Źródła LegalApp: git archive HEAD z repozytorium hosta wypakowane w /root/legalapp kontenera — kontener
# nie montuje głównego worktree projektu. Repozytorium: PROTO_LEGALAPP albo pierwsze z listy niżej.
# Wariant awaryjny (gdy nie działa): Podman w pudełku z gniazdem Docker API albo type: vm (docs/box-prototype.md).
set -euo pipefail
# shellcheck source=scripts/box/prototype/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log_start p1 "P-1 — Docker w nieuprzywilejowanym kontenerze (nesting, intercept mknod/setxattr)"
require_incus
require_table
ensure_box
run "konfiguracja bezpieczeństwa kontenera" -- incus config get "$BOX" security.privileged
[[ -z $LAST_OUT || $LAST_OUT == false ]] || die "kontener jest uprzywilejowany (security.privileged: $LAST_OUT) — próba niedozwolona"

net_open
ensure_pkgs docker.io ca-certificates curl libicu74
box_docker_proxy
hello() {
    run "docker info ($1: runtime, sterownik, opcje bezpieczeństwa)" -- in_box docker info \
        --format '{{.DefaultRuntime}} | {{.Driver}} | {{.SecurityOptions}} | {{.Name}}'
    run "docker run --rm hello-world ($1)" -- in_box docker run --rm hello-world
    ok && grep -q "Hello from Docker" <<<"$LAST_OUT"
}
use_runtime() {  # use_runtime <wariant> — przygotowuje runtime dockerd w kontenerze
    case "$1" in
        runc) ;;
        crun)
            run "apt-get install crun" -- in_box env DEBIAN_FRONTEND=noninteractive apt-get install -y \
                --no-install-recommends crun
            ok || return 1
            run "dockerd: domyślny runtime crun" -- in_box bash -c '
                printf "%s\n" "{\"default-runtime\": \"crun\", \"runtimes\": {\"crun\": {\"path\": \"/usr/bin/crun\"}}}" \
                    >/etc/docker/daemon.json
                systemctl restart docker && crun --version | head -n 1'
            ;;
        runc-stary)
            run "wersje runc w repozytoriach" -- in_box apt-cache madison runc
            old=$(awk '{print $3}' <<<"$LAST_OUT" | sort -V | head -n 1)
            [[ -n $old ]] || return 1
            run "runc $old (najstarszy w Ubuntu 24.04) i domyślny runtime" -- in_box bash -c "
                rm -f /etc/docker/daemon.json
                DEBIAN_FRONTEND=noninteractive apt-get install -y --allow-downgrades runc=$old &&
                systemctl restart docker && runc --version | head -n 1"
            ;;
    esac
    ok
}
variant=""
for candidate in runc crun runc-stary; do
    if use_runtime "$candidate" && hello "$candidate"; then
        result "docker-$candidate" ok "docker run --rm hello-world działa z runtime $candidate"
        variant=$candidate
        break
    fi
    result "docker-$candidate" fail "runtime $candidate: kod $LAST_RC — $(grep -m1 -E 'Error|error|E:' <<<"$LAST_OUT" || tail -n 1 <<<"$LAST_OUT")"
done
if [[ -n $variant ]]; then
    result hello-world ok "docker run --rm hello-world w kontenerze działa (runtime $variant, bez intercept)"
else
    result hello-world fail "docker run --rm hello-world nie działa z żadnym runtime (runc, crun, runc-stary) — błędy wyżej"
    result dotnet-test skip "pominięte — Docker w kontenerze nie działa"
    finish
fi

# --- LegalApp ------------------------------------------------------------------------------------

la=${PROTO_LEGALAPP:-}
if [[ -z $la ]]; then
    for d in "$HOME/DevWorkspace/agent-team" "$HOME/DevWorkspace/LegalApp" "$HOME/DevWorkspace/legalapp"; do
        [[ -d $d/.git ]] && { la=$d; break; }
    done
fi
[[ -n $la && -d $la/.git ]] || die "nie znalazłem repozytorium LegalApp (PROTO_LEGALAPP=<ścieżka>)"
note "LegalApp: $la ($(git -C "$la" rev-parse --short HEAD))"

channel=$(git -C "$la" show HEAD:global.json 2>/dev/null | sed -n 's/.*"version" *: *"\([0-9]*\.[0-9]*\).*/\1/p' | head -1)
if [[ -z $channel ]]; then
    channel=$(git -C "$la" grep -hoE 'net[0-9]+\.[0-9]+' HEAD -- '*.csproj' '*.props' 2>/dev/null \
        | sed 's/^.*:net//; s/^net//' | sort -V | tail -1)
fi
[[ -n $channel ]] || die "nie znam wersji .NET LegalApp (global.json ani TargetFramework)"
note "Kanał .NET SDK: $channel"

run "instalacja .NET SDK $channel w kontenerze" -- in_box bash -c "
    set -e
    if ! /usr/share/dotnet/dotnet --list-sdks 2>/dev/null | grep -q '^$channel\.'; then
        curl -fsSL https://dot.net/v1/dotnet-install.sh -o /tmp/dotnet-install.sh
        bash /tmp/dotnet-install.sh --channel $channel --install-dir /usr/share/dotnet
    fi
    ln -sf /usr/share/dotnet/dotnet /usr/local/bin/dotnet
    dotnet --list-sdks"
ok || die ".NET SDK $channel nie zainstalował się w kontenerze"

copy_sources() {
    git -C "$la" archive --format=tar HEAD \
        | incus exec "$BOX" -T -- bash -c 'rm -rf /root/legalapp && mkdir -p /root/legalapp && tar -x -C /root/legalapp'
}
run "kopia źródeł (git archive HEAD → /root/legalapp)" -- copy_sources
ok || die "kopia źródeł LegalApp do kontenera nie przeszła"

run "dotnet test LegalApp (Testcontainers, pgvector/pgvector:pg16)" -- in_box bash -c "
    cd /root/legalapp
    export DOTNET_CLI_TELEMETRY_OPTOUT=1 DOTNET_NOLOGO=1
    timeout ${PROTO_DOTNET_TIMEOUT:-3600} dotnet test >/root/dotnet-test.log 2>&1
    rc=\$?
    grep -E 'Passed!|Failed!|Skipped!|error [A-Z]+[0-9]+|Exception:' /root/dotnet-test.log | sort | uniq -c | tail -n 40
    exit \$rc"
dotnet_rc=$LAST_RC
dotnet_ms=$LAST_MS
run "pełne wyjście dotnet test → docs/box-prototype/p1-dotnet-test.log" -- \
    incus file pull "$BOX/root/dotnet-test.log" "$LOG_DIR/p1-dotnet-test.log"
run "obrazy Dockera w kontenerze" -- in_box docker images --format '{{.Repository}}:{{.Tag}} {{.Size}}'
if [[ $dotnet_rc -eq 0 ]]; then
    result dotnet-test ok "dotnet test LegalApp z Testcontainers w kontenerze przeszedł ($(fmt_ms "$dotnet_ms") s, $variant)"
else
    result dotnet-test fail "dotnet test LegalApp: kod $dotnet_rc ($(fmt_ms "$dotnet_ms") s, $variant) — błędy wyżej i w p1-dotnet-test.log"
fi
run "pozostałe kontenery Dockera" -- in_box docker ps -a --format '{{.Image}} {{.Status}}'

finish

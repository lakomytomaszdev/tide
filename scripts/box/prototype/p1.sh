#!/usr/bin/env bash
# P-1 (PT-164): Docker w nieuprzywilejowanym kontenerze Incusa — docker run hello-world i dotnet test LegalApp
# z Testcontainers (pgvector/pgvector:pg16). Użycie: bash scripts/box/prototype/p1.sh   (sudo zapyta o hasło)
#
# Sieć: chwilowe reguły tide-proto w tabeli inet tide-box (lib.sh: net_open/net_close), usuwane w pułapce EXIT.
# Źródła LegalApp: git archive HEAD z repozytorium hosta wypakowane w /root/legalapp kontenera — kontener
# nie montuje głównego worktree projektu. Repozytorium: PROTO_LEGALAPP albo pierwsze z listy niżej.
# Wariant awaryjny (gdy nie działa): Podman w pudełku z gniazdem Docker API albo type: vm (docs/box-prototype.md).
set -euo pipefail
# shellcheck source=scripts/box/prototype/lib.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

log_start p1 "P-1 — Docker w nieuprzywilejowanym kontenerze (nesting, intercept mknod/setxattr)"
require_incus
sudo_prime
require_table
ensure_box
run "konfiguracja bezpieczeństwa kontenera" -- incus config get "$BOX" security.privileged
[[ -z $LAST_OUT || $LAST_OUT == false ]] || die "kontener jest uprzywilejowany (security.privileged: $LAST_OUT) — próba niedozwolona"

net_open
ensure_pkgs docker.io ca-certificates curl libicu74
run "docker info (sterownik, opcje bezpieczeństwa)" -- in_box docker info --format '{{.Driver}} | {{.SecurityOptions}} | {{.Name}}'
run "docker run --rm hello-world" -- in_box docker run --rm hello-world
if ok && grep -q "Hello from Docker" <<<"$LAST_OUT"; then
    result hello-world ok "docker run --rm hello-world w kontenerze działa"
else
    result hello-world fail "docker run --rm hello-world: kod $LAST_RC (pełny błąd wyżej)"
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
    result dotnet-test ok "dotnet test LegalApp z Testcontainers w kontenerze przeszedł ($(fmt_ms "$dotnet_ms") s)"
else
    result dotnet-test fail "dotnet test LegalApp: kod $dotnet_rc ($(fmt_ms "$dotnet_ms") s) — błędy wyżej"
fi
run "pozostałe kontenery Dockera" -- in_box docker ps -a --format '{{.Image}} {{.Status}}'

finish

#!/usr/bin/env bash
# P-6 (PT-178): klienci sieci w pudełku przez proxy CONNECT bez DNS i NO_PROXY dla localhost.
# Użycie: bash scripts/box/prototype/p6.sh [--only klient,…]   (jako zwykły użytkownik w grupie incus, bez sudo)
#
# Pudełko tide-proto-net (common.sh) dostaje HTTP(S)_PROXY/NO_PROXY w /etc/environment, proxy apt i drop-in dockerd.
# Prototypowe proxy startuje na adresie mostka z PUSTĄ listą domen. Każdy klient biegnie w rundach: runda 1 z pustą
# listą (dziennik odmów = domeny, których potrzebuje), w kolejnych rundach lista = domeny z odmów poprzednich rund,
# aż klient przejdzie bez nowych odmów albo skończy się 5 rund. Przed każdą rundą cache klienta jest czyszczony,
# żeby kolejna runda naprawdę szła do sieci. Domeny każdego klienta: $PROTO_RUN/domains-<klient>.txt.
#
# apt: najpierw ubuntu.sources przepisane na https:// (CONNECT); gdy to nie działa — wariant awaryjny: zwykłe HTTP
# przez proxy dla domen z listy (forma bezwzględna), wynik do decyzji w docs/box-decisions.md.
# NO_PROXY: serwer HTTP w pudełku na pętli zwrotnej, żądania z curl, Node fetch, dotnet HttpClient i Playwright do
# http://localhost:<port> i http://127.0.0.1:<port>; dowód = serwer dostał ścieżkę, a dziennik proxy nie ma wpisu.
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf '%s\n' "Użycie: bash scripts/box/prototype/p6.sh [--only apt,npm,…] — P-6: klienci przez CONNECT i NO_PROXY" \
        "Klienci: apt apt-install npm dotnet pip docker playwright claude-installer codex node-fetch; noproxy"
    exit 0
fi
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
proto_log p6
need incus python3 ss

only=""
[[ ${1:-} == --only ]] && only=",${2:-},"
want() { [[ -z $only || $only == *",$1,"* ]]; }

BRIDGE=$(bridge_address)
LIST="$PROTO_RUN/domains.txt"
: >"$LIST"
step "proxy na $BRIDGE:$PROTO_PORT z pustą listą"
proxy_start "$LIST"
step "pudełko $PROTO_BOX"
box_ensure
run in_box sh -c 'getent hosts registry.npmjs.org || echo "DNS w pudełku: brak (oczekiwane)"' || true

ROUNDS=5
# Ograniczenie ponowień, żeby runda z odmową nie czekała minut — nie zmienia drogi przez proxy.
QUIET=(--env npm_config_fetch_retries=1 --env npm_config_audit=false --env npm_config_fund=false
       --env DOTNET_CLI_TELEMETRY_OPTOUT=1 --env DOTNET_NOLOGO=1 --env PIP_RETRIES=1)
declare -A FOUND RC

# trial <klient> root|user <skrypt bash> — rundy z listą uzupełnianą z dziennika odmów.
trial() {
    local name=$1 who=$2 script=$3 round mark rc new found=""
    step "klient $name"
    for round in $(seq "$ROUNDS"); do
        printf '%s\n' $found >"$LIST"
        mark=$(lines "$PROXY_DENY")
        echo "-- runda $round, lista: ${found:-(pusta)}"
        rc=0
        if [[ $who == root ]]; then
            box_env; timeout 900 incus exec "$PROTO_BOX" "${BOX_ENV[@]}" "${QUIET[@]}" -- bash -c "$script" || rc=$?
        else
            box_env
            timeout 900 incus exec "$PROTO_BOX" --user 1000 --group 1000 --cwd "$BOX_HOME" --env "HOME=$BOX_HOME" \
                --env "PATH=$BOX_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" "${BOX_ENV[@]}" "${QUIET[@]}" \
                -- bash -c "$script" || rc=$?
        fi
        new=$(denied_since "$mark" | tr '\n' ' ')
        echo "-- runda $round: kod $rc, odmowy: ${new:-brak}"
        [[ $round -eq 1 ]] && result "P-6 $name pusta-lista" "kod $rc, odmowy: ${new:-brak}"
        if [[ -z $new ]]; then
            break
        fi
        found="$found $new"
    done
    found=$(printf '%s\n' $found | awk 'NF && !seen[$0]++')
    printf '%s\n' $found >"$PROTO_RUN/domains-$name.txt"
    FOUND[$name]=$found
    RC[$name]=$rc
    result "P-6 $name" "kod końcowy $rc po $round rundach; domeny: $(echo $found)"
}

all_found() { printf '%s\n' "${FOUND[@]}" | tr ' ' '\n' | awk 'NF && !seen[$0]++'; }

# --- apt ---------------------------------------------------------------------------------------------
SOURCES=/etc/apt/sources.list.d/ubuntu.sources
# apt-get update kończy się kodem 0 także przy części nieudanych pobrań (W:) — to też liczymy jako błąd.
APT_UPDATE='rm -rf /var/lib/apt/lists/* && apt-get -o Acquire::Retries=0 update 2>&1 | tee /tmp/apt.out;
  rc=${PIPESTATUS[0]}; if [ "$rc" = 0 ] && grep -Eq "^(E|W): " /tmp/apt.out; then rc=1; fi; exit $rc'
if want apt; then
    run in_box sh -c "test -f $SOURCES.tide-orig || cp $SOURCES $SOURCES.tide-orig"
    run in_box sed -i 's#http://#https://#g' "$SOURCES"
    run in_box grep -E '^URIs' "$SOURCES" || true
    trial apt-https root "$APT_UPDATE"
    if [[ ${RC[apt-https]} -ne 0 ]]; then
        step "apt przez https nie działa — wariant awaryjny: zwykłe HTTP przez proxy"
        run in_box cp "$SOURCES.tide-orig" "$SOURCES"
        trial apt-http root "$APT_UPDATE"
        APT=apt-http
    else
        APT=apt-https
    fi
    result "P-6 apt" "wariant $APT"
fi

if want apt-install; then
    trial apt-install root 'DEBIAN_FRONTEND=noninteractive apt-get install -y -o Acquire::Retries=0 \
        curl ca-certificates nodejs npm python3-pip dotnet-sdk-8.0 docker.io bubblewrap socat jq'
fi

# --- npm, NuGet, pip ---------------------------------------------------------------------------------
if want npm; then
    in_box_user sh -c 'mkdir -p p6-npm && cd p6-npm && printf "%s\n" "{\"name\":\"p6\",\"version\":\"1.0.0\",\"private\":true,\"dependencies\":{\"left-pad\":\"1.3.0\"}}" > package.json'
    trial npm user 'cd p6-npm && npm cache clean --force && rm -rf node_modules package-lock.json &&
        npm install --package-lock-only && npm ci'
fi

if want dotnet; then
    in_box_user sh -c 'mkdir -p p6-dotnet && cat > p6-dotnet/p6.csproj' <<'EOF'
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework></PropertyGroup>
  <ItemGroup><PackageReference Include="Newtonsoft.Json" Version="13.0.3" /></ItemGroup>
</Project>
EOF
    in_box_user sh -c 'cat > p6-dotnet/Program.cs' <<'EOF'
// P-6: dotnet HttpClient do adresu z argumentu — czy honoruje NO_PROXY dla localhost.
using var http = new System.Net.Http.HttpClient();
var url = args.Length > 0 ? args[0] : "https://api.nuget.org/v3/index.json";
var res = await http.GetAsync(url);
System.Console.WriteLine($"{(int)res.StatusCode} {url}");
EOF
    trial dotnet user 'cd p6-dotnet && dotnet nuget locals all --clear && rm -rf obj bin && dotnet restore'
fi

if want pip; then
    trial pip user 'rm -rf /tmp/p6-pip && pip download --no-cache-dir -d /tmp/p6-pip requests==2.32.3'
fi

# --- docker pull -------------------------------------------------------------------------------------
if want docker; then
    run in_box sh -c 'systemctl daemon-reload && systemctl restart docker && systemctl show docker -p Environment' || true
    run in_box docker info --format '{{.ServerVersion}} {{.Driver}} {{.HTTPSProxy}}' || true
    trial docker root 'docker rmi -f hello-world >/dev/null 2>&1; docker pull hello-world'
    run in_box docker run --rm hello-world || true
fi

# --- Playwright --------------------------------------------------------------------------------------
if want playwright; then
    in_box_user mkdir -p p6-pw
    trial playwright user 'cd p6-pw && rm -rf node_modules package*.json ~/.cache/ms-playwright && npm cache clean --force &&
        npm init -y >/dev/null && npm i playwright && npx playwright install chromium'
fi

# --- instalatory agentów -----------------------------------------------------------------------------
if want claude-installer; then
    trial claude-installer user 'set -o pipefail; rm -rf ~/.local/bin/claude ~/.local/share/claude ~/.cache/claude &&
        curl -fsSL https://claude.ai/install.sh | bash -s 2.1.317 && ~/.local/bin/claude --version'
fi

if want codex; then
    trial codex root 'npm cache clean --force && npm i -g @openai/codex && codex --version'
fi

# --- Node fetch (na zewnątrz) -------------------------------------------------------------------------
if want node-fetch; then
    printf '%s\n' $(all_found) registry.npmjs.org | awk '!s[$0]++' >"$LIST"
    step "Node fetch na zewnątrz (lista: wszystkie domeny)"
    run in_box_user node --version || true
    for extra in "" "--env NODE_USE_ENV_PROXY=1"; do
        mark=$(lines "$PROXY_ACCESS")
        rc=0
        box_env
        run timeout 60 incus exec "$PROTO_BOX" --user 1000 --group 1000 "${BOX_ENV[@]}" $extra -- node -e \
            'fetch("https://registry.npmjs.org/left-pad").then(r => console.log("status", r.status)).catch(e => { console.log(String(e.cause || e)); process.exit(1) })' || rc=$?
        hits=$(tail -n +"$(( mark + 1 ))" "$PROXY_ACCESS" | grep -c registry.npmjs.org || true)
        result "P-6 node-fetch${extra:+ NODE_USE_ENV_PROXY=1}" "kod $rc, wpisy proxy: $hits"
    done
fi

# --- NO_PROXY dla localhost ---------------------------------------------------------------------------
if want noproxy; then
    printf '%s\n' $(all_found) | awk 'NF && !s[$0]++' >"$LIST"
    step "NO_PROXY: serwer na pętli zwrotnej w pudełku (port 8765, :: = także 127.0.0.1)"
    in_box_user sh -c 'pkill -f "[h]ttp.server 8765"; rm -f /tmp/p6-http.log;
        setsid -f python3 -m http.server 8765 --bind :: </dev/null >/tmp/p6-http.log 2>&1'
    sleep 1
    in_box_user sh -c 'cat > p6-pw/noproxy.js' <<'EOF'
// P-6: Playwright (Chromium) do adresu z argumentu — czy przeglądarka honoruje NO_PROXY.
const { chromium } = require("playwright");
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage();
  const res = await page.goto(process.argv[2]);
  console.log(res.status(), process.argv[2]);
  await browser.close();
})().catch(e => { console.error(String(e)); process.exit(1); });
EOF
    run in_box_user sh -c 'cd p6-dotnet && dotnet build -nologo -v q -o /tmp/p6-dotnet-bin' || true
    declare -A CMD=(
        [curl]='curl -sS -o /dev/null -w "%{http_code}\n" URL'
        [node-fetch]='node -e "fetch(process.argv[1]).then(r => console.log(r.status))" URL'
        [dotnet]='dotnet /tmp/p6-dotnet-bin/p6.dll URL'
        [playwright]='cd p6-pw && node noproxy.js URL'
    )
    for client in curl node-fetch dotnet playwright; do
        for host in localhost 127.0.0.1; do
            path="/p6-$client-$host"
            url="http://$host:8765$path"
            mark=$(lines "$PROXY_ACCESS")
            rc=0
            run in_box_user bash -c "${CMD[$client]//URL/$url}" || rc=$?
            sleep 0.5
            proxy_hits=$(tail -n +"$(( mark + 1 ))" "$PROXY_ACCESS" | grep -Ec 'localhost|127\.0\.0\.1|::1' || true)
            server_hit=$(in_box grep -c -- "$path" /tmp/p6-http.log || true)
            verdict="nie"
            [[ $rc -eq 0 && $proxy_hits -eq 0 && ${server_hit:-0} -gt 0 ]] && verdict="tak"
            result "P-6 noproxy $client $host" "$verdict (kod $rc, serwer: ${server_hit:-0}, wpisy proxy: $proxy_hits)"
        done
    done
    in_box_user pkill -f "http.server 8765" || true
fi

step "podsumowanie"
all_found >"$PROTO_RUN/domains-all.txt"
for name in "${!FOUND[@]}"; do
    echo "$name: kod ${RC[$name]}; $(echo ${FOUND[$name]})"
done | sort
echo "odmowy łącznie: $(lines "$PROXY_DENY"), żądania łącznie: $(lines "$PROXY_ACCESS")"
echo "katalog wyników: ${PROTO_RUN#$PROTO_REPO/}"

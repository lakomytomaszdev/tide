#!/usr/bin/env bash
# P-3 (PT-178): K3 — logowanie subskrypcyjne agentów przez proxy wstrzykujące token hosta.
# Użycie: bash scripts/box/prototype/p3.sh      (po p6.sh: w pudełku muszą być claude i codex)
#
# Proxy prototypowe na adresie mostka z trasami /anthropic i /chatgpt czyta token z plików hosta POZA repozytorium
# (domyślnie ~/.claude/.credentials.json i ~/.codex/auth.json; inne: P3_CLAUDE_CREDS, P3_CODEX_AUTH) przy każdym
# żądaniu i nigdy ich nie loguje. W pudełku nie ma prawdziwych poświadczeń — tylko atrapy:
# - Claude Code: CLAUDE_CODE_OAUTH_TOKEN=atrapa, ANTHROPIC_BASE_URL=http://<mostek>:18080/anthropic,
# - Codex: ~/.codex/auth.json z atrapą (JWT bez podpisu, konto „tide-box-dummy”), chatgpt_base_url na proxy;
#   drugi wariant dokłada OPENAI_BASE_URL (adres modelu w trybie ChatGPT).
# Lista domen CONNECT jest pusta: dziennik odmów pokazuje, dokąd agent idzie poza base URL (telemetria,
# odświeżanie OAuth). Pomiar: `claude -p 'hi'` ×3 w pudełku przez proxy i ×3 na hoście, mediana.
# Skrypt nie wypisuje treści plików tokenów; dziennik proxy nie zawiera nagłówków.
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf '%s\n' "Użycie: bash scripts/box/prototype/p3.sh — P-3: Claude Code i Codex przez proxy wstrzykujące (K3)"
    exit 0
fi
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
proto_log p3
need incus python3 ss claude

CLAUDE_CREDS=${P3_CLAUDE_CREDS:-$HOME/.claude/.credentials.json}
CODEX_AUTH=${P3_CODEX_AUTH:-$HOME/.codex/auth.json}
inject=()
[[ -f $CLAUDE_CREDS ]] && inject+=(--inject "anthropic=$CLAUDE_CREDS") || echo "Brak $CLAUDE_CREDS — Claude Code pominięty"
[[ -f $CODEX_AUTH ]] && inject+=(--inject "chatgpt=$CODEX_AUTH") || echo "Brak $CODEX_AUTH — Codex pominięty"

BRIDGE=$(bridge_address)
BASE="http://$BRIDGE:$PROTO_PORT"
LIST="$PROTO_RUN/domains.txt"
: >"$LIST"
step "proxy wstrzykujące na $BASE (lista CONNECT pusta)"
proxy_start "$LIST" "${inject[@]}"
step "pudełko $PROTO_BOX"
box_ensure
run in_box_user sh -c 'rm -f ~/.claude/.credentials.json ~/.codex/auth.json; ls -la ~/.claude ~/.codex 2>&1 | head -20' || true
run in_box_user claude --version || { echo "Brak claude w pudełku — najpierw p6.sh"; exit 1; }

elapsed() { awk -v a="$1" -v b="$(date +%s.%N)" 'BEGIN { printf "%.2f", b - a }'; }
median() { printf '%s\n' "$@" | sort -n | awk '{a[NR]=$1} END {print a[int((NR+1)/2)]}'; }
since() { tail -n +"$(( $2 + 1 ))" "$1"; }

# --- Claude Code ----------------------------------------------------------------------------------
step "Claude Code: claude -p 'hi' ×3 przez $BASE/anthropic"
box_times=()
claude_ok=0
mark_a=$(lines "$PROXY_ACCESS"); mark_d=$(lines "$PROXY_DENY")
for i in 1 2 3; do
    t0=$(date +%s.%N)
    rc=0
    box_env
    run timeout 180 incus exec "$PROTO_BOX" --user 1000 --group 1000 --cwd "$BOX_HOME" --env "HOME=$BOX_HOME" \
        --env "PATH=$BOX_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" "${BOX_ENV[@]}" \
        --env "ANTHROPIC_BASE_URL=$BASE/anthropic" --env "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-tide-box-dummy" \
        -- claude -p 'hi' || rc=$?
    t=$(elapsed "$t0")
    box_times+=("$t")
    [[ $rc -eq 0 ]] && claude_ok=$(( claude_ok + 1 ))
    echo "-- powtórzenie $i: kod $rc, czas $t s"
done
echo "-- żądania przez proxy:"; since "$PROXY_ACCESS" "$mark_a" | cut -f3- | sort | uniq -c
echo "-- odmowy CONNECT:"; since "$PROXY_DENY" "$mark_d" | cut -f2,3 | sort | uniq -c
codes=$(since "$PROXY_ACCESS" "$mark_a" | awk -F'\t' '$3 ~ /^INJECT/ && $4 ~ /^\/anthropic/ {print $5}' | sort | uniq -c | tr -s ' ' | tr '\n' ';')
result "P-3 claude" "$claude_ok/3 udanych; kody /anthropic: ${codes:-brak żądań}; polecenie: ANTHROPIC_BASE_URL=$BASE/anthropic CLAUDE_CODE_OAUTH_TOKEN=<atrapa> claude -p 'hi'"
result "P-3 claude czas-pudełko" "$(median "${box_times[@]}") s (${box_times[*]})"

step "host: claude -p 'hi' ×3 (pomiar porównawczy)"
host_times=()
host_dir=$(mktemp -d)
for i in 1 2 3; do
    t0=$(date +%s.%N)
    rc=0
    (cd "$host_dir" && run timeout 180 claude -p 'hi') || rc=$?
    t=$(elapsed "$t0")
    host_times+=("$t")
    echo "-- powtórzenie $i: kod $rc, czas $t s"
done
rmdir "$host_dir" 2>/dev/null || true
result "P-3 claude czas-host" "$(median "${host_times[@]}") s (${host_times[*]})"

# --- Codex ----------------------------------------------------------------------------------------
if in_box_user sh -c 'command -v codex' >/dev/null 2>&1 && [[ -f $CODEX_AUTH ]]; then
    step "Codex: atrapa ~/.codex/auth.json w pudełku"
    python3 - <<'EOF' | box_write "$BOX_HOME/.codex/auth.json" ubuntu:ubuntu
import base64, datetime, json
def b64(obj):
    return base64.urlsafe_b64encode(json.dumps(obj).encode()).rstrip(b"=").decode()
claims = {"email": "box@tide.invalid", "exp": 4102444800,
          "https://api.openai.com/auth": {"chatgpt_plan_type": "plus", "chatgpt_account_id": "tide-box-dummy"}}
jwt = f'{b64({"alg": "none", "typ": "JWT"})}.{b64(claims)}.atrapa'
now = datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")
print(json.dumps({"OPENAI_API_KEY": None, "tokens": {"id_token": jwt, "access_token": jwt,
      "refresh_token": "tide-box-dummy", "account_id": "tide-box-dummy"}, "last_refresh": now}))
EOF
    in_box chmod 600 "$BOX_HOME/.codex/auth.json"
    run in_box_user codex --version || true
    codex_ok=""
    for variant in chatgpt_base_url chatgpt_base_url+OPENAI_BASE_URL; do
        step "Codex wariant $variant"
        extra=()
        [[ $variant == *OPENAI_BASE_URL ]] && extra=(--env "OPENAI_BASE_URL=$BASE/chatgpt/backend-api/codex")
        mark_a=$(lines "$PROXY_ACCESS"); mark_d=$(lines "$PROXY_DENY")
        rc=0
        box_env
        run timeout 180 incus exec "$PROTO_BOX" --user 1000 --group 1000 --cwd "$BOX_HOME" --env "HOME=$BOX_HOME" \
            "${BOX_ENV[@]}" "${extra[@]}" -- codex exec --skip-git-repo-check \
            -c "chatgpt_base_url=\"$BASE/chatgpt/backend-api/\"" 'Reply with the single word: hi' </dev/null || rc=$?
        echo "-- żądania przez proxy:"; since "$PROXY_ACCESS" "$mark_a" | cut -f3- | sort | uniq -c
        echo "-- odmowy CONNECT:"; since "$PROXY_DENY" "$mark_d" | cut -f2,3 | sort | uniq -c
        codes=$(since "$PROXY_ACCESS" "$mark_a" | awk -F'\t' '$4 ~ /^\/chatgpt/ {print $4" "$5}' | sort | uniq -c | tr -s ' ' | tr '\n' ';')
        result "P-3 codex $variant" "kod $rc; /chatgpt: ${codes:-brak żądań}"
        [[ $rc -eq 0 && -z $codex_ok ]] && codex_ok=$variant
    done
    if [[ -n $codex_ok ]]; then
        result "P-3 codex" "działa w wariancie $codex_ok"
    else
        result "P-3 codex" "żaden wariant nie przeszedł"
    fi
else
    result "P-3 codex" "pominięty (brak codex w pudełku albo $CODEX_AUTH na hoście)"
fi

step "odświeżanie OAuth"
refresh=$(grep -E 'console\.anthropic\.com|platform\.claude\.com|claude\.ai|auth\.openai\.com|/oauth' "$PROXY_ACCESS" || true)
echo "${refresh:-brak żądań odświeżenia (ani przez base URL, ani przez CONNECT)}"
result "P-3 odświeżanie" "$(echo "$refresh" | grep -c . || true) żądań do punktów OAuth"

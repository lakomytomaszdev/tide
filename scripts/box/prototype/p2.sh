#!/usr/bin/env bash
# P-2 (PT-178): wewnętrzne piaskownice agentów w pudełku — bwrap (Claude Code) i landlock (Codex).
# Użycie: bash scripts/box/prototype/p2.sh      (po p6.sh i p3.sh: claude, codex, bubblewrap, socat w pudełku)
#
# Host ma kernel.apparmor_restrict_unprivileged_userns=1; pytanie, czy w kontenerze (profil AppArmor Incusa,
# security.nesting=true) proces użytkownika utworzy przestrzeń nazw użytkownika, jak robi to bwrap.
# Kolejno: sysctl i etykieta AppArmor, goły bwrap i unshare, `codex sandbox linux` (bez modelu), potem z modelem
# przez proxy wstrzykujące z P-3: `claude -p` z sandbox.enabled=true (failIfUnavailable, denyRead ~/.claude)
# i `codex exec --sandbox workspace-write`. Dowód działania piaskownicy to pliki, nie odpowiedź modelu:
# odczyt znacznika z ~/.claude ma się nie udać, zapis do ~/p2-outside-* (poza katalogiem roboczym) też.
# Wynik „nie działa” zostaje z pełnym błędem — skrypt nie wyłącza piaskownicy, żeby „przeszło”.
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf '%s\n' "Użycie: bash scripts/box/prototype/p2.sh — P-2: bwrap (Claude Code) i landlock (Codex) w pudełku"
    exit 0
fi
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
proto_log p2
need incus python3 ss

CLAUDE_CREDS=${P3_CLAUDE_CREDS:-$HOME/.claude/.credentials.json}
CODEX_AUTH=${P3_CODEX_AUTH:-$HOME/.codex/auth.json}
inject=()
[[ -f $CLAUDE_CREDS ]] && inject+=(--inject "anthropic=$CLAUDE_CREDS")
[[ -f $CODEX_AUTH ]] && inject+=(--inject "chatgpt=$CODEX_AUTH")

BRIDGE=$(bridge_address)
BASE="http://$BRIDGE:$PROTO_PORT"
LIST="$PROTO_RUN/domains.txt"
: >"$LIST"
step "proxy (wstrzykujące, lista CONNECT pusta) na $BASE"
proxy_start "$LIST" "${inject[@]}"
step "pudełko $PROTO_BOX"
box_ensure

step "host i pudełko: przestrzenie nazw i AppArmor"
for key in kernel.apparmor_restrict_unprivileged_userns kernel.unprivileged_userns_clone user.max_user_namespaces; do
    echo "host $key = $(cat "/proc/sys/${key//.//}" 2>/dev/null || echo brak)"
done
run incus config get "$PROTO_BOX" security.nesting || true
run in_box_user sh -c 'echo "etykieta AppArmor: $(cat /proc/self/attr/current)"; cat /proc/sys/user/max_user_namespaces' || true
run in_box_user sh -c 'command -v bwrap socat; bwrap --version' || true

step "goły bwrap i unshare jako $BOX_USER"
bw_rc=0
run in_box_user bwrap --ro-bind / / --dev /dev --proc /proc --unshare-all --die-with-parent -- sh -c 'echo bwrap-ok' || bw_rc=$?
result "P-2 bwrap-goły" "kod $bw_rc"
run in_box_user unshare --user --map-root-user --net -- sh -c 'echo unshare-ok' || true

in_box_user sh -c 'rm -rf ~/p2-work ~/p2-outside-*; mkdir -p ~/p2-work ~/.claude && echo p2-marker-not-a-secret > ~/.claude/p2-marker'

# --- Codex bez modelu --------------------------------------------------------------------------------
if in_box_user sh -c 'command -v codex' >/dev/null 2>&1; then
    step "codex sandbox linux --full-auto (bez modelu)"
    cs_rc=0
    run in_box_user sh -c 'cd ~/p2-work && codex sandbox linux --full-auto -- sh -c \
        "echo ok > ./codex-inside; echo zle > ~/p2-outside-codex; cat ~/.claude/p2-marker"' || cs_rc=$?
    inside=$(in_box_user sh -c 'test -f ~/p2-work/codex-inside && echo tak || echo nie')
    outside=$(in_box_user sh -c 'test -f ~/p2-outside-codex && echo tak || echo nie')
    result "P-2 codex-sandbox" "kod $cs_rc; zapis w katalogu roboczym: $inside; zapis poza nim: $outside"
    if [[ $inside == tak && $outside == nie ]]; then
        result "P-2 landlock" "działa (codex sandbox linux ogranicza zapis do katalogu roboczego)"
    else
        result "P-2 landlock" "NIE działa (zob. błąd wyżej)"
    fi
fi

# --- Claude Code z modelem ---------------------------------------------------------------------------
if [[ -f $CLAUDE_CREDS ]] && in_box_user sh -c 'command -v claude' >/dev/null 2>&1; then
    step "claude -p z sandbox.enabled=true (model przez $BASE/anthropic)"
    in_box_user sh -c 'cat > ~/p2-work/settings.json' <<EOF
{"sandbox": {"enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false,
  "autoAllowBashIfSandboxed": true,
  "filesystem": {"denyRead": ["~/.claude"], "allowWrite": ["$BOX_HOME/p2-work"]},
  "network": {"allowedDomains": []}}}
EOF
    prompt='Run each of these three shell commands with the Bash tool, one per call, and report their exit codes:
1) cat ~/.claude/p2-marker > ./claude-read.txt 2>&1
2) echo ok > ./claude-inside
3) echo bad > ~/p2-outside-claude'
    cl_rc=0
    box_env
    run timeout 300 incus exec "$PROTO_BOX" --user 1000 --group 1000 --cwd "$BOX_HOME/p2-work" --env "HOME=$BOX_HOME" \
        --env "PATH=$BOX_HOME/.local/bin:/usr/local/bin:/usr/bin:/bin" "${BOX_ENV[@]}" \
        --env "ANTHROPIC_BASE_URL=$BASE/anthropic" --env "CLAUDE_CODE_OAUTH_TOKEN=sk-ant-oat01-tide-box-dummy" \
        -- claude -p --setting-sources= --settings "$BOX_HOME/p2-work/settings.json" --allowedTools Bash \
        --output-format text "$prompt" || cl_rc=$?
    read_leak=$(in_box_user sh -c 'grep -c p2-marker-not-a-secret ~/p2-work/claude-read.txt 2>/dev/null || true')
    inside=$(in_box_user sh -c 'test -f ~/p2-work/claude-inside && echo tak || echo nie')
    outside=$(in_box_user sh -c 'test -f ~/p2-outside-claude && echo tak || echo nie')
    run in_box_user sh -c 'cat ~/p2-work/claude-read.txt 2>/dev/null' || true
    result "P-2 claude-sandbox" "kod $cl_rc; znacznik z ~/.claude przeczytany: ${read_leak:-0}; zapis w katalogu roboczym: $inside; poza nim: $outside"
    if [[ $cl_rc -eq 0 && $inside == tak && $outside == nie && ${read_leak:-0} == 0 ]]; then
        result "P-2 bwrap" "działa (sandbox.enabled=true, denyRead i allowWrite egzekwowane)"
    else
        result "P-2 bwrap" "NIE działa albo niepełne (zob. wyjście wyżej)"
    fi
else
    result "P-2 bwrap" "pominięty z modelem (brak claude w pudełku albo $CLAUDE_CREDS na hoście); zob. goły bwrap"
fi

# --- Codex z modelem ---------------------------------------------------------------------------------
if [[ -f $CODEX_AUTH ]] && in_box_user sh -c 'test -f ~/.codex/auth.json' 2>/dev/null; then
    step "codex exec --sandbox workspace-write (model przez $BASE/chatgpt)"
    cx_rc=0
    box_env
    run timeout 300 incus exec "$PROTO_BOX" --user 1000 --group 1000 --cwd "$BOX_HOME/p2-work" --env "HOME=$BOX_HOME" \
        "${BOX_ENV[@]}" --env "OPENAI_BASE_URL=$BASE/chatgpt/backend-api/codex" -- codex exec --skip-git-repo-check \
        --sandbox workspace-write -c "chatgpt_base_url=\"$BASE/chatgpt/backend-api/\"" \
        'Run these two shell commands and report their exit codes: `echo ok > ./codex-model-inside` and `echo bad > ~/p2-outside-codex-model`' \
        || cx_rc=$?
    inside=$(in_box_user sh -c 'test -f ~/p2-work/codex-model-inside && echo tak || echo nie')
    outside=$(in_box_user sh -c 'test -f ~/p2-outside-codex-model && echo tak || echo nie')
    result "P-2 codex-exec" "kod $cx_rc; zapis w katalogu roboczym: $inside; poza nim: $outside"
fi
echo "-- żądania przez proxy:"; cut -f3- "$PROXY_ACCESS" | sort | uniq -c

#!/usr/bin/env bash
# P-2 (PT-178): wewnętrzne piaskownice agentów w pudełku — bwrap (Claude Code) i landlock (Codex).
# Użycie: bash scripts/box/prototype/p2.sh      (po p6.sh i p3.sh: claude, codex, bubblewrap, socat w pudełku)
#
# Host ma kernel.apparmor_restrict_unprivileged_userns=1; pytanie, czy w kontenerze (profil AppArmor Incusa,
# security.nesting=true) proces użytkownika utworzy przestrzeń nazw użytkownika, jak robi to bwrap.
# Zestaw prób (funkcja suite): goły bwrap i unshare, `codex sandbox linux` (bez modelu), potem z modelem przez proxy
# wstrzykujące z P-3: `claude -p` z sandbox.enabled=true (failIfUnavailable, denyRead ~/.claude) i
# `codex exec --sandbox workspace-write`. Dowód działania piaskownicy to pliki, nie odpowiedź modelu: odczyt znacznika
# z ~/.claude ma się nie udać, zapis do ~/p2-outside-* (poza katalogiem roboczym) też.
#
# Wariant A: pudełko jak z obrazu. Wariant B (gdy w A goły bwrap pada): ten sam profil AppArmor dla /usr/bin/bwrap,
# którego używa host (scripts/sandbox/check.py profile — `userns,` tylko dla bwrap), wczytany WEWNĄTRZ pudełka do jego
# przestrzeni AppArmor (apparmor_parser jako root pudełka). Host, sysctl i nftables bez zmian.
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
# Z wnętrza etykieta to „unconfined” (przestrzeń AppArmor kontenera); profil Incusa widać tylko od strony hosta.
box_pid=$(incus info "$PROTO_BOX" 2>/dev/null | awk '/^PID:/ {print $2}')
echo "host: profil AppArmor procesu init pudełka (pid ${box_pid:-?}): $(cat "/proc/${box_pid:-0}/attr/current" 2>/dev/null || echo nieczytelny)"
run in_box_user sh -c 'command -v bwrap socat; bwrap --version' || true
# Wariant A ma być pudełkiem z obrazu: profil bwrap z poprzedniego wariantu B zdejmujemy z jądra i z /etc.
run in_box sh -c 'f=/etc/apparmor.d/bwrap-userns-restrict; if [ -f "$f" ]; then apparmor_parser -R "$f"; rm -f "$f"; echo "zdjęto profil bwrap z wariantu B"; fi' || true

# Codex 0.166: `codex sandbox linux …` potraktował „linux” jako polecenie (przebieg 3: „Failed to execvp linux”),
# więc próba bierze formę z pomocy: `codex sandbox linux` tylko, gdy taka podkomenda istnieje.
if in_box_user sh -c 'codex sandbox --help 2>&1 | grep -Eq "^[[:space:]]+linux"' 2>/dev/null; then
    CODEX_SANDBOX=(codex sandbox linux --full-auto)
else
    # 0.166: `codex sandbox [OPTIONS] [COMMAND]` bez --full-auto (przebieg 4: „unexpected argument '--full-auto'”);
    # ta forma nie była jeszcze uruchomiona — dowodem dla P-2 jest `codex exec --sandbox workspace-write` niżej.
    CODEX_SANDBOX=(codex sandbox)
fi
echo "forma piaskownicy Codexa: ${CODEX_SANDBOX[*]}"
run in_box_user codex sandbox --help || true
CODEX_VARIANT=$(cat "$PROTO_STATE/codex-variant" 2>/dev/null || echo provider)
echo "wariant Codexa z P-3: $CODEX_VARIANT"

# suite <wariant> — cały zestaw prób; wyniki jako „WYNIK P-2 <wariant> …”.
suite() {
    local v=$1 bw_rc=0 us_rc=0 cs_rc=0 cl_rc=0 cx_rc=0 inside outside read_leak
    step "[$v] goły bwrap i unshare jako $BOX_USER"
    run in_box_user bwrap --ro-bind / / --dev /dev --proc /proc --unshare-all --die-with-parent -- sh -c 'echo bwrap-ok' || bw_rc=$?
    result "P-2 $v bwrap-goły" "kod $bw_rc"
    run in_box_user unshare --user --map-root-user --net -- sh -c 'echo unshare-ok' || us_rc=$?
    result "P-2 $v unshare-userns" "kod $us_rc"
    run journalctl -k --since "-1min" --no-pager -g 'apparmor="DENIED"' || true
    BARE_RC=$bw_rc

    in_box_user sh -c 'rm -rf ~/p2-work ~/p2-outside-*; mkdir -p ~/p2-work ~/.claude && echo p2-marker-not-a-secret > ~/.claude/p2-marker'

    if in_box_user sh -c 'command -v codex' >/dev/null 2>&1; then
        step "[$v] ${CODEX_SANDBOX[*]} (bez modelu)"
        box_env
        run incus exec "$PROTO_BOX" --user 1000 --group 1000 --cwd "$BOX_HOME/p2-work" --env "HOME=$BOX_HOME" \
            "${BOX_ENV[@]}" -- "${CODEX_SANDBOX[@]}" -- sh -c \
            "echo ok > ./codex-inside; echo zle > $BOX_HOME/p2-outside-codex; cat $BOX_HOME/.claude/p2-marker" || cs_rc=$?
        inside=$(in_box_user sh -c 'test -f ~/p2-work/codex-inside && echo tak || echo nie')
        outside=$(in_box_user sh -c 'test -f ~/p2-outside-codex && echo tak || echo nie')
        result "P-2 $v codex-sandbox" "kod $cs_rc; zapis w katalogu roboczym: $inside; zapis poza nim: $outside"
        if [[ $inside == tak && $outside == nie ]]; then
            result "P-2 $v landlock" "działa (codex sandbox linux ogranicza zapis do katalogu roboczego)"
        else
            result "P-2 $v landlock" "NIE działa (zob. błąd wyżej)"
        fi
    fi

    if [[ -f $CLAUDE_CREDS ]] && in_box_user sh -c 'command -v claude' >/dev/null 2>&1; then
        step "[$v] claude -p z sandbox.enabled=true (model przez $BASE/anthropic)"
        in_box_user sh -c 'cat > ~/p2-work/settings.json' <<EOF
{"sandbox": {"enabled": true, "failIfUnavailable": true, "allowUnsandboxedCommands": false,
  "autoAllowBashIfSandboxed": true,
  "filesystem": {"denyRead": ["~/.claude"], "allowWrite": ["$BOX_HOME/p2-work"]},
  "network": {"allowedDomains": []}}}
EOF
        local prompt='Run each of these three shell commands with the Bash tool, one per call, and report their exit codes:
1) cat ~/.claude/p2-marker > ./claude-read.txt 2>&1
2) echo ok > ./claude-inside
3) echo bad > ~/p2-outside-claude'
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
        result "P-2 $v claude-sandbox" "kod $cl_rc; znacznik z ~/.claude przeczytany: ${read_leak:-0}; zapis w katalogu roboczym: $inside; poza nim: $outside"
        if [[ $cl_rc -eq 0 && $inside == tak && $outside == nie && ${read_leak:-0} == 0 ]]; then
            result "P-2 $v bwrap" "działa (sandbox.enabled=true, denyRead i allowWrite egzekwowane)"
        else
            result "P-2 $v bwrap" "NIE działa albo niepełne (zob. wyjście wyżej)"
        fi
    else
        result "P-2 $v bwrap" "pominięty z modelem (brak claude w pudełku albo $CLAUDE_CREDS na hoście)"
    fi

    if [[ -f $CODEX_AUTH ]] && in_box_user sh -c 'test -f ~/.codex/auth.json' 2>/dev/null; then
        step "[$v] codex exec --sandbox workspace-write (model przez $BASE/chatgpt, wariant $CODEX_VARIANT)"
        codex_args "$BASE" "$CODEX_VARIANT"
        box_env
        run timeout 300 incus exec "$PROTO_BOX" --user 1000 --group 1000 --cwd "$BOX_HOME/p2-work" --env "HOME=$BOX_HOME" \
            "${BOX_ENV[@]}" "${CODEX_ENV[@]}" -- codex exec --skip-git-repo-check \
            --sandbox workspace-write "${CODEX_CFG[@]}" \
            'Run these two shell commands and report their exit codes: `echo ok > ./codex-model-inside` and `echo bad > ~/p2-outside-codex-model`' \
            </dev/null || cx_rc=$?
        inside=$(in_box_user sh -c 'test -f ~/p2-work/codex-model-inside && echo tak || echo nie')
        outside=$(in_box_user sh -c 'test -f ~/p2-outside-codex-model && echo tak || echo nie')
        result "P-2 $v codex-exec" "kod $cx_rc; zapis w katalogu roboczym: $inside; poza nim: $outside"
    fi
}

suite A

if [[ $BARE_RC -ne 0 ]]; then
    step "wariant B: profil AppArmor bwrap (jak na hoście) wczytany w przestrzeni AppArmor pudełka"
    # apparmor_parser w pudełku z apt — na czas instalacji lista CONNECT = domeny apt z P-6.
    printf '%s\n' archive.ubuntu.com security.ubuntu.com >"$LIST"
    run in_box sh -c 'command -v apparmor_parser || DEBIAN_FRONTEND=noninteractive apt-get install -y apparmor' || true
    : >"$LIST"
    python3 "$PROTO_REPO/scripts/sandbox/check.py" profile | box_write /etc/apparmor.d/bwrap-userns-restrict
    run in_box cat /etc/apparmor.d/bwrap-userns-restrict || true
    pb_rc=0
    run in_box apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict || pb_rc=$?
    result "P-2 B profil-wczytany" "kod $pb_rc"
    run in_box sh -c 'cat /sys/kernel/security/apparmor/profiles 2>&1 | grep -i bwrap' || true
    suite B
fi
echo "-- żądania przez proxy:"; cut -f3- "$PROXY_ACCESS" | sort | uniq -c

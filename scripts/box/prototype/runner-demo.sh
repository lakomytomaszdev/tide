#!/bin/bash
# Próba ręczna PT-169 (#6): krok przez BoxRunner (`python3 -m tide_daemon.box.runner --demo`) na kontenerze
# prototypowym z #1 (domyślnie `tide-proto`). Uruchamiać poza piaskownicą kroku, z worktree w <repo>-worktrees:
#
#   bash scripts/box/prototype/runner-demo.sh
#
# Sprawdza: `git status` w worktree i odczyt `in/env` z pudełka, brak znacznika z `in/env` w `ps -o args` hosta
# (procesy pudełka też są widoczne na hoście), odmowę zapisu do `.git/hooks` i `.git/config`, `curl` do serwera
# w pudełku na 127.0.0.1 bez proxy. Pudełko dostaje urządzenia BoxRunnera (zatrzymanie/start, gdy brakuje
# nakładek-plików) i plik /opt/tide/tide-step. Log: docs/box-prototype/runner-demo.log (bez wartości z in/env).
set -u
root=$(cd "$(dirname "$0")/../../.." && pwd)
log="$root/docs/box-prototype/runner-demo.log"
slug=${PROTO_BOX:-tide-proto}
port=${PROTO_PORT:-8765}
marker="tide-demo-$(od -An -N8 -tx1 /dev/urandom | tr -d ' \n')"
mkdir -p "$(dirname "$log")"
exec > >(tee "$log") 2>&1

echo "# PT-169 runner-demo $(date -Is), pudełko $slug, worktree $root"
inner='
echo "== git status"; git status --short --branch | head -5; echo "kod $?"
echo "== in/env (same klucze)"; sed "s/=.*//" "$TIDE_STEP/in/env" | tr "\n" " "; echo
echo "== znacznik w in/env: $(grep -c "^TIDE_DEMO_SECRET=" "$TIDE_STEP/in/env") wiersz, w środowisku: ${TIDE_DEMO_SECRET:+tak}"
echo "== tryb in/env: $(stat -c "%a %u" "$TIDE_STEP/in/env"), id: $(id -u)"
echo "NO_PROXY=$NO_PROXY"; echo "HTTPS_PROXY=$HTTPS_PROXY"
common=$(git rev-parse --path-format=absolute --git-common-dir)
for probe in "$common/hooks/tide-pt169-probe" "$common/config"; do
  if ( : >> "$probe" ) 2>/tmp/pt169-err; then echo "WYNIK zapis-$(basename "$probe"): fail (zapis przeszedł)";
  else echo "WYNIK zapis-$(basename "$probe"): ok ($(cat /tmp/pt169-err))"; fi
done
python3 -m http.server '"$port"' --bind 127.0.0.1 >/dev/null 2>&1 & srv=$!
sleep 1
out=$(curl -sv -o /dev/null --max-time 5 http://127.0.0.1:'"$port"'/ 2>&1); code=$?
echo "$out" | grep -E "Uses proxy|Connected to|< HTTP/" | sed "s/^/  /"
if [ $code = 0 ] && ! echo "$out" | grep -q "Uses proxy"; then echo "WYNIK curl-bez-proxy: ok"; else echo "WYNIK curl-bez-proxy: fail (kod $code)"; fi
kill $srv 2>/dev/null
sleep 6
exit 0
'
export TIDE_DEMO_MARKER="$marker"
PYTHONPATH="$root/daemon" python3 -m tide_daemon.box.runner --demo "$slug" --worktree "$root" --push-tide-step \
    -- /bin/sh -c "$inner" &
demo=$!
sleep 4
snap=$(mktemp)
ps -eo args > "$snap"
leaks=$(grep -cF "$marker" "$snap")
rm -f "$snap"
if [ "$leaks" = 0 ]; then echo "WYNIK ps-bez-in-env: ok (0 procesów ze znacznikiem w argv)";
else echo "WYNIK ps-bez-in-env: fail ($leaks procesów ze znacznikiem w argv)"; fi
wait "$demo"
echo "WYNIK demo: kod $?"

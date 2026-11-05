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
# „Uses proxy env variable no_proxy == …” curl pisze zawsze, gdy czyta NO_PROXY — o ominięciu proxy świadczy
# połączenie wprost z 127.0.0.1, nie z adresem proxy (runda 3: fałszywe „fail” przy dopasowaniu „Uses proxy”).
proxy_host=${HTTPS_PROXY#http://}; proxy_host=${proxy_host%%:*}
if [ $code = 0 ] && echo "$out" | grep -q "Connected to 127.0.0.1 " && ! echo "$out" | grep -q "Connected to $proxy_host "; then
  echo "WYNIK curl-bez-proxy: ok"; else echo "WYNIK curl-bez-proxy: fail (kod $code)"; fi
kill $srv 2>/dev/null
sleep 6
exit 0
'
export TIDE_DEMO_MARKER="$marker"
PYTHONPATH="$root/daemon" python3 -m tide_daemon.box.runner --demo "$slug" --worktree "$root" --push-tide-step \
    -- /bin/sh -c "$inner" &
demo=$!
# Migawka `ps` dopiero, gdy krok naprawdę biegnie (klient `incus exec` z jednostką kroku w argv) — dodanie
# montowań może zatrzymać i wystartować pudełko, co trwa dłużej niż stałe `sleep` (runda 1).
snap=$(mktemp)
seen=0
for _ in $(seq 1 120); do
    ps -eo args > "$snap"
    if grep -q -- "--unit=tide-step-demo-" "$snap"; then seen=1; sleep 2; ps -eo args > "$snap"; break; fi
    kill -0 "$demo" 2>/dev/null || break
    sleep 0.5
done
leaks=$(grep -cF "$marker" "$snap")
rm -f "$snap"
if [ "$seen" != 1 ]; then echo "WYNIK ps-bez-in-env: skip (krok nie wystartował — migawka bez procesów kroku)";
elif [ "$leaks" = 0 ]; then echo "WYNIK ps-bez-in-env: ok (0 procesów ze znacznikiem w argv, krok w trakcie)";
else echo "WYNIK ps-bez-in-env: fail ($leaks procesów ze znacznikiem w argv)"; fi
wait "$demo"
code=$?
echo "WYNIK demo: kod $code"
if [ "$code" != 0 ]; then
    echo "== diagnostyka"
    incus config device show "$slug" </dev/null 2>&1 | sed 's/^/  /'
    incus config get "$slug" raw.idmap </dev/null 2>&1 | sed 's/^/  raw.idmap: /'
    incus exec "$slug" -T -- loginctl show-user 1000 -p Linger -p State </dev/null 2>&1 | sed 's/^/  /'
    incus exec "$slug" -T --user 1000 --group 1000 -- /usr/bin/env -i XDG_RUNTIME_DIR=/run/user/1000 \
        /usr/bin/systemctl --user is-system-running </dev/null 2>&1 | sed 's/^/  systemctl --user: /'
    incus exec "$slug" -T --user 1000 --group 1000 -- ls -ld "$root" "$root".demo-*.step /opt/tide/tide-step \
        </dev/null 2>&1 | sed 's/^/  /'
    incus exec "$slug" -T --user 1000 --group 1000 -- /usr/bin/env -i XDG_RUNTIME_DIR=/run/user/1000 \
        /usr/bin/journalctl --user -n 30 --no-pager </dev/null 2>&1 | sed 's/^/  /'
fi
exit "$code"

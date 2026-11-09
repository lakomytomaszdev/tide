#!/usr/bin/env bash
# Wszystkie próby prototypu pudełka po kolei. Każda pisze własny log w docs/box-prototype/; błąd jednej próby nie
# zatrzymuje kolejnych.
#   PT-164: p5 → p1 → p4 → p9 → measure (kontener tide-proto; sudo pyta tylko w chwilowych wariantach projektu
#           Incusa — intercept w p5, migawki w measure — i w trybie PROTO_NET=rules);
#   PT-178: p7 → p6 → p3 → p2 (pudełko tide-proto-net; P-6 instaluje claude i codex w pudełku, P-2 korzysta
#           z atrapy Codexa z P-3; bez sudo).
# Kontenery prób zostają (powtórki); usuwa je cleanup.sh.
# Użycie: bash scripts/box/prototype/all.sh [--install]
#   --install — najpierw sudo bash scripts/box/install.sh --yes (root:<uid>:1 w /etc/subuid i /etc/subgid dla
#               raw.idmap, restart incus; wyjście w docs/box-prototype/install.log). Przerwanie, gdy instalacja padnie.
# TRIALS="p3 p2" — tylko wybrane próby (kolejność jak podana); P6_ONLY=klient,… — powtórka tylko części P-6.
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf '%s\n' "Użycie: bash scripts/box/prototype/all.sh [--install] — próby P-5, P-1, P-4, P-9, pomiary (PT-164)" \
        "i P-7, P-6, P-3, P-2 (PT-178) po kolei; TRIALS=\"p1 p9\" — tylko wybrane"
    exit 0
fi
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
repo=$(cd "$here/../../.." && pwd)
install=0
case "${1:-}" in
    --install) install=1 ;;
    "") ;;
    *) echo "Nieznany argument: $1 (dozwolone: --install)" >&2; exit 2 ;;
esac

trials=${TRIALS:-p5 p1 p4 p9 measure p7 p6 p3 p2}
for trial in $trials; do
    [[ $trial =~ ^(p[1-79]|measure)$ ]] \
        || { echo "TRIALS: nieznana próba $trial (p1–p7, p9, measure)" >&2; exit 2; }
done

if [[ $install -eq 1 ]]; then
    mkdir -p "$repo/docs/box-prototype"
    log="$repo/docs/box-prototype/install.log"
    sudo_cmd=(sudo)
    if ! (: </dev/tty) 2>/dev/null && [[ -x /usr/bin/ksshaskpass ]]; then
        export SUDO_ASKPASS=${SUDO_ASKPASS:-/usr/bin/ksshaskpass}
        sudo_cmd=(sudo -A)
    fi
    echo "== sudo bash scripts/box/install.sh --yes  (log: docs/box-prototype/install.log)"
    rc=0
    { echo "# make box-install przed próbami prototypu — $(date -Iseconds)"; echo; } >"$log"
    "${sudo_cmd[@]}" bash "$repo/scripts/box/install.sh" --yes </dev/null >>"$log" 2>&1 || rc=$?
    echo "kod wyjścia: $rc" >>"$log"
    tail -n 20 "$log"
    if [[ $rc -ne 0 ]]; then
        echo "Instalacja nie przeszła (kod $rc) — próby nie ruszają. Log: docs/box-prototype/install.log" >&2
        exit 2
    fi
fi

codes=()
for trial in $trials; do
    echo "######## $trial"
    args=()
    [[ $trial == p6 && -n ${P6_ONLY:-} ]] && args=(--only "$P6_ONLY")
    rc=0
    bash "$here/$trial.sh" "${args[@]}" </dev/null || rc=$?
    echo "######## $trial: kod $rc"
    codes+=("$trial=$rc")
done
echo
echo "== Próby: ${codes[*]} (0 — działa, 1 — coś nie działa, 2 — warunki; szczegóły w logach)"
echo "dzienniki: $(cd "$repo" && ls -1t docs/box-prototype/*.log 2>/dev/null | head -4 | tr '\n' ' ')"
for c in "${codes[@]}"; do
    [[ ${c#*=} == 0 ]] || exit 1
done

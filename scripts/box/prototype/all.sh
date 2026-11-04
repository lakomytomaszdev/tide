#!/usr/bin/env bash
# Wszystkie próby PT-178 po kolei: P-7 (images:), P-6 (klienci, instaluje claude i codex w pudełku), P-3 (K3,
# pomiar), P-2 (piaskownice agentów; korzysta z atrapy Codexa z P-3). Błąd jednej próby nie zatrzymuje kolejnych —
# każda ma własny dziennik w docs/box-prototype/. Pudełko tide-proto-net zostaje (powtórki); usuwa je cleanup.sh.
# Użycie: bash scripts/box/prototype/all.sh      (jako zwykły użytkownik w grupie incus, bez sudo)
if [[ ${1:-} == --help || ${1:-} == -h ]]; then
    printf '%s\n' "Użycie: bash scripts/box/prototype/all.sh — próby P-7, P-6, P-3, P-2 po kolei"
    exit 0
fi
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
status=0
# P6_ONLY=klient,… — powtórka tylko części P-6 (np. playwright,noproxy), reszta pudełka z poprzedniego przebiegu.
# TRIALS="p3 p2" — tylko wybrane próby (kolejność jak podana; p2 korzysta z wariantu Codexa z p3).
for trial in ${TRIALS:-p7 p6 p3 p2}; do
    [[ $trial =~ ^p[2367]$ ]] || { echo "TRIALS: nieznana próba $trial (p2, p3, p6, p7)" >&2; exit 2; }
    echo "######## $trial"
    args=()
    [[ $trial == p6 && -n ${P6_ONLY:-} ]] && args=(--only "$P6_ONLY")
    bash "$here/$trial.sh" "${args[@]}"
    rc=$?
    echo "######## $trial: kod $rc"
    [[ $rc -ne 0 ]] && status=1
done
echo "dzienniki: $(cd "$here/../../.." && ls -1t docs/box-prototype/*.log 2>/dev/null | head -4 | tr '\n' ' ')"
exit $status

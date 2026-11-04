#!/bin/bash
# tide-step — opakowanie kroku w pudełku (/usr/local/bin/tide-step, instaluje obraz bazowy PT-179; kontrakt należy
# do BoxRunnera #6/PT-169, który może go rozszerzyć — zmiana tego pliku zmienia hash przepisu i przebudowuje obraz).
#
#   tide-step <plik env> <plik kodu> <program> [argumenty…]
#
# Uruchamiane przez `incus exec <pudełko> --user 1000 … -- systemd-run --user --unit=tide-step-<id> --collect
# --wait tide-step …` (decyzja krok-jako-usluga-user). Usługa systemd nie dziedziczy środowiska wywołującego, więc
# zmienne kroku (także atrapy K3) przychodzą w pliku `<plik env>` (0600, wiersze KLUCZ=wartość — wczytywane jako
# dane, nigdy wykonywane), a nie w argv ani we właściwościach jednostki. Proxy pudełka jest w /etc/environment
# (obraz bazowy) i jest wczytywane pierwsze; plik kroku może je nadpisać. Kod wyjścia programu trafia atomowo do
# `<plik kodu>` (odczyt po restarcie usługi Tide), tak jak EXIT_WRAPPER kroku na hoście (tasks/agentcmd.py).
set -uo pipefail
umask 077

if [[ $# -lt 3 ]]; then
    echo "użycie: tide-step <plik env> <plik kodu> <program> [argumenty…]" >&2
    exit 2
fi
env_file=$1
status=$2
shift 2

load_env() {  # load_env <plik> — KLUCZ=wartość (wartość w cudzysłowie "…" albo bez), komentarze i puste pomijane
    local line key value
    while IFS= read -r line || [[ -n $line ]]; do
        [[ -z ${line//[[:space:]]/} || $line == \#* ]] && continue
        key=${line%%=*}
        value=${line#*=}
        if [[ $line != *=* || ! $key =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
            echo "tide-step: $1: niepoprawny wiersz (oczekiwano KLUCZ=wartość)" >&2
            return 2
        fi
        if [[ $value == \"*\" && ${#value} -ge 2 ]]; then
            value=${value:1:${#value}-2}
        fi
        export "$key=$value"
    done < "$1"
}

if [[ -r /etc/environment ]]; then
    load_env /etc/environment || exit 2
fi
load_env "$env_file" || exit 2

trap : TERM
"$@"
code=$?
printf '%s\n' "$code" > "$status.tmp" && mv -f -- "$status.tmp" "$status"
exit "$code"

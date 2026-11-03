#!/usr/bin/env bash
# Instaluje profil AppArmor tylko dla /usr/bin/bwrap, żeby działała piaskownica Claude Code (PT-75).
# Użycie: sudo bash scripts/sandbox/install.sh [--yes]
#         bash scripts/sandbox/install.sh --dry-run     (bez sudo: sprawdzenia i podgląd, bez zmian)
#
# 1. Sprawdza warunki: /usr/bin/bwrap, apparmor_parser, brak obcego pliku w miejscu docelowym i brak
#    innego profilu dla /usr/bin/bwrap w /etc/apparmor.d (np. /etc/apparmor.d/bwrap z dokumentacji
#    Claude Code) — dwa profile dla jednej ścieżki to niejasne, który wygra, więc wtedy przerywa.
# 2. Składa profil (python3 scripts/sandbox/check.py profile — profil z dokumentacji Claude Code:
#    `profile bwrap /usr/bin/bwrap flags=(unconfined) { userns, }`, uzasadnienie w check.py) i sprawdza go
#    apparmor_parser bez wczytywania do jądra.
# 3. Pokazuje, co zmieni, i czeka na potwierdzenie (--yes pomija pytanie).
# 4. Zapisuje /etc/apparmor.d/bwrap-userns-restrict (root, 644; nazwa pliku z pierwszej wersji profilu)
#    i wczytuje tylko ten plik (apparmor_parser -r). Przy podmianie starszego profilu Tide najpierw usuwa
#    z jądra jego profile (pierwsza wersja miała też unpriv_bwrap). Przy starcie systemu plik wczyta
#    zwykła usługa apparmor.
# 5. Sprawdza jako użytkownik, który wywołał sudo: bwrap działa, a zwykły `unshare -U` nadal trafia
#    do profilu unprivileged_userns (ograniczenie dla reszty systemu zostaje).
#
# Nie zmienia sysctl kernel.apparmor_restrict_unprivileged_userns ani profilu unprivileged_userns.
# Cofnięcie: sudo bash scripts/sandbox/uninstall.sh
#
# Zmienne dla testów (działają tylko z --dry-run; sudo i tak czyści środowisko): TIDE_SANDBOX_ROOT — korzeń
# udawanego systemu (jak w check.py), TIDE_SANDBOX_CHECK — program zamiast check.py.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
BWRAP=/usr/bin/bwrap

yes=0
dry=0
for arg in "$@"; do
    case "$arg" in
        --yes) yes=1 ;;
        --dry-run) dry=1 ;;
        *) echo "Nieznany argument: $arg (dozwolone: --yes, --dry-run)" >&2; exit 2 ;;
    esac
done

check="$here/check.py"
if [[ $dry -eq 1 ]]; then
    user="$(id -un)"
    as_user() { "$@"; }
    check="${TIDE_SANDBOX_CHECK:-$check}"
else
    # Zmienne testów nie mogą sterować prawdziwą instalacją (atrapy prób, inny korzeń, inny check.py).
    unset TIDE_SANDBOX_ROOT TIDE_SANDBOX_BWRAP TIDE_SANDBOX_UNSHARE TIDE_SANDBOX_CHECK
    if [[ $EUID -ne 0 ]]; then
        echo "Uruchom przez sudo: sudo bash $0  (podgląd bez zmian: bash $0 --dry-run)" >&2
        exit 1
    fi
    user="${SUDO_USER:-}"
    if [[ -z "$user" || "$user" == root ]]; then
        echo "Brak SUDO_USER — uruchom jako zwykły użytkownik przez sudo, nie z konta roota." >&2
        exit 1
    fi
    # Próby bwrap i unshare mają sens tylko bez uprawnień roota.
    as_user() { sudo -u "$user" -- "$@"; }
fi

# --- 1. warunki -----------------------------------------------------------------------------------

for tool in apparmor_parser python3; do
    if ! command -v "$tool" >/dev/null; then
        echo "Brak programu: $tool" >&2
        exit 1
    fi
done
if [[ ! -x $BWRAP ]]; then
    echo "Brak $BWRAP (pakiet bubblewrap) — piaskownica Claude Code go wymaga." >&2
    exit 1
fi

TARGET="${TIDE_SANDBOX_ROOT:-}/etc/apparmor.d/bwrap-userns-restrict"
state=$(as_user /usr/bin/python3 "$check" --json 2>/dev/null) || true
if ! /usr/bin/python3 -c 'import json, sys; json.loads(sys.argv[1])' "$state" 2>/dev/null; then
    echo "Nie udało się odczytać stanu ($check --json) — przerywam, nic nie zmieniono." >&2
    exit 1
fi
field() { /usr/bin/python3 -c 'import json,sys; v=json.loads(sys.argv[1]); [v:=v[k] for k in sys.argv[2:]]; print(v if not isinstance(v, list) else "\n".join(v))' "$state" "$@"; }

if [[ "$(field profile)" == obcy ]]; then
    echo "$TARGET już istnieje i nie ma znacznika Tide — nie nadpisuję cudzego profilu." >&2
    echo "Jeśli to kopia z /usr/share/apparmor/extra-profiles, piaskownica może już działać: python3 $here/check.py" >&2
    exit 1
fi
conflicts=$(field conflicts)
if [[ -n "$conflicts" ]]; then
    echo "W /etc/apparmor.d jest już inny profil dla $BWRAP:" >&2
    echo "$conflicts" | sed 's/^/  /' >&2
    echo "Dwa profile dla jednej ścieżki to niejasne, który wygra — przerywam, nic nie zmieniono." >&2
    exit 1
fi
if [[ "$(field restrict_userns)" != 1 ]]; then
    echo "kernel.apparmor_restrict_unprivileged_userns ≠ 1 — ograniczenia nie ma, profil niczego nie zmieni." >&2
    as_user /usr/bin/python3 "$check" >&2 || true
    exit 1
fi

# --- 2. profil ------------------------------------------------------------------------------------

work=$(mktemp -d "${TMPDIR:-/tmp}/tide-sandbox-XXXXXX")
trap 'rm -rf "$work"' EXIT
/usr/bin/python3 "$here/check.py" profile >"$work/bwrap-userns-restrict"
if ! apparmor_parser -Q -K "$work/bwrap-userns-restrict"; then
    echo "apparmor_parser odrzucił profil — przerywam, nic nie zmieniono." >&2
    exit 1
fi

# --- 3. pytanie -----------------------------------------------------------------------------------

echo "Stan teraz:"
as_user /usr/bin/python3 "$check" | sed 's/^/  /' || true
echo
if [[ "$(field profile)" == tide ]] && cmp -s "$work/bwrap-userns-restrict" "$TARGET"; then
    echo "Na dysku jest już ten sam profil Tide — zostanie tylko ponownie wczytany do jądra."
elif [[ "$(field profile)" == tide ]]; then
    echo "Profil Tide na dysku różni się od bieżącego:"
    diff -u "$TARGET" "$work/bwrap-userns-restrict" | sed 's/^/  /' || true
fi
echo "Zostanie zapisany $TARGET (root, 644) i wczytany: apparmor_parser -r $TARGET"
echo "Profil: bwrap, tylko dla $BWRAP — może zakładać przestrzenie użytkownika (także zagnieżdżone, których"
echo "potrzebuje piaskownica Claude Code). Dzieci bwrap mają uprawnienia w swojej przestrzeni."
echo "Bez zmian: sysctl kernel.apparmor_restrict_unprivileged_userns=1 i profil unprivileged_userns."
if [[ $dry -eq 1 ]]; then
    echo
    echo "Podgląd (--dry-run): profil przeszedł apparmor_parser, nic nie zmieniono."
    echo "Instalacja: sudo bash $0"
    exit 0
fi
if [[ $yes -eq 0 ]]; then
    if [[ ! -t 0 ]]; then
        echo "Brak terminala do pytania — uruchom w terminalu albo z --yes. Nic nie zmieniono." >&2
        exit 1
    fi
    read -r -p "Zainstalować? [t/N] " answer || answer=n
    if [[ "$answer" != [tTyY] ]]; then
        echo "Przerwano, nic nie zmieniono."
        exit 1
    fi
fi

# --- 4. instalacja --------------------------------------------------------------------------------

# Profile ze starszej wersji pliku, których nowa nie ma (pierwsza wersja miała też unpriv_bwrap): -r ich nie
# usuwa. Najpierw wczytujemy nowy plik (bwrap podmienia się bez przerwy), potem usuwamy tylko zbędne.
old_names=""
[[ -e "$TARGET" ]] && old_names=$(apparmor_parser -N "$TARGET" 2>/dev/null || true)
new_names=$(apparmor_parser -N "$work/bwrap-userns-restrict")
trap 'echo "Instalacja przerwana w połowie. Stan: python3 $here/check.py; naprawa: sudo bash $0 albo cofnięcie: sudo bash $here/uninstall.sh" >&2; rm -rf "$work"' ERR
install -m 644 -o root -g root "$work/bwrap-userns-restrict" "$TARGET"
apparmor_parser -r "$TARGET"
for name in $old_names; do
    if ! grep -qxF "$name" <<<"$new_names"; then
        printf 'profile %s {}\n' "$name" >"$work/stale"
        apparmor_parser -R "$work/stale" || echo "Nie udało się usunąć z jądra starego profilu $name (zniknie po restarcie)." >&2
    fi
done
trap - ERR

# --- 5. sprawdzenie -------------------------------------------------------------------------------

echo
if as_user /usr/bin/python3 "$check"; then
    echo
    echo "Gotowe. Cofnięcie: sudo bash $here/uninstall.sh"
else
    echo
    echo "Profil zapisany i wczytany, ale sprawdzenie nie przeszło (wyżej). Dziennik: journalctl -k -g apparmor" >&2
    echo "Cofnięcie: sudo bash $here/uninstall.sh" >&2
    exit 1
fi

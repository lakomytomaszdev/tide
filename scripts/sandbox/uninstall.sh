#!/usr/bin/env bash
# Usuwa profil AppArmor dla bwrap zainstalowany przez scripts/sandbox/install.sh (PT-75).
# Użycie: sudo bash scripts/sandbox/uninstall.sh
#         bash scripts/sandbox/uninstall.sh --dry-run   (bez sudo: co zostałoby usunięte, bez zmian)
#
# Usuwa z jądra profile z tego pliku (apparmor_parser -R) i plik
# /etc/apparmor.d/bwrap-userns-restrict — tylko jeśli ma znacznik Tide. Po usunięciu bwrap znów
# kończy się „setting up uid map: Permission denied”, a piaskownica Claude Code się wyłącza.
#
# TIDE_SANDBOX_ROOT (tylko z --dry-run, dla testów) — korzeń udawanego systemu, jak w check.py.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
MARKER=scripts/sandbox/install.sh

dry=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) dry=1 ;;
        *) echo "Nieznany argument: $arg (dozwolone: --dry-run)" >&2; exit 2 ;;
    esac
done
if [[ $dry -eq 0 ]]; then
    if [[ $EUID -ne 0 ]]; then
        echo "Uruchom przez sudo: sudo bash $0  (podgląd bez zmian: bash $0 --dry-run)" >&2
        exit 1
    fi
    # Zmienne testów nie mogą sterować prawdziwą instalacją (atrapy prób, inny korzeń, inny check.py).
    unset TIDE_SANDBOX_ROOT TIDE_SANDBOX_BWRAP TIDE_SANDBOX_UNSHARE TIDE_SANDBOX_CHECK
fi
TARGET="${TIDE_SANDBOX_ROOT:-}/etc/apparmor.d/bwrap-userns-restrict"

if [[ ! -e $TARGET ]]; then
    echo "Brak $TARGET — nic do usunięcia."
    exit 0
fi
if ! head -1 "$TARGET" | grep -qF "$MARKER"; then
    echo "$TARGET nie ma znacznika Tide — to nie nasz profil, nie usuwam go." >&2
    exit 1
fi

echo "Zostanie usunięty z jądra i z dysku: $TARGET (profile: $(apparmor_parser -N "$TARGET" 2>/dev/null | tr '\n' ' '))."
echo "Piaskownica Claude Code przestanie działać (bwrap znów zablokowany przez AppArmor)."
if [[ $dry -eq 1 ]]; then
    echo "Podgląd (--dry-run): nic nie zmieniono."
    exit 0
fi
if [[ ! -t 0 ]]; then
    echo "Brak terminala do pytania — uruchom w terminalu. Nic nie zmieniono." >&2
    exit 1
fi
read -r -p "Kontynuować? [t/N] " answer || answer=n
if [[ "$answer" != [tTyY] ]]; then
    echo "Przerwano, nic nie zmieniono."
    exit 1
fi

apparmor_parser -R "$TARGET" || echo "Profile nie były wczytane — usuwam sam plik."
rm -f "$TARGET"
echo "Profil usunięty."
if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
    sudo -u "$SUDO_USER" -- /usr/bin/python3 "$here/check.py" || true
fi

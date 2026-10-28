#!/usr/bin/env bash
# Usuwa profil AppArmor dla bwrap zainstalowany przez scripts/sandbox/install.sh (PT-75).
# Użycie: sudo bash scripts/sandbox/uninstall.sh
#
# Usuwa z jądra profile z tego pliku (apparmor_parser -R) i plik
# /etc/apparmor.d/bwrap-userns-restrict — tylko jeśli ma znacznik Tide. Po usunięciu bwrap znów
# kończy się „setting up uid map: Permission denied”, a piaskownica Claude Code się wyłącza.
set -euo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
TARGET=/etc/apparmor.d/bwrap-userns-restrict
MARKER=scripts/sandbox/install.sh

if [[ $EUID -ne 0 ]]; then
    echo "Uruchom przez sudo: sudo bash $0" >&2
    exit 1
fi
if [[ ! -e $TARGET ]]; then
    echo "Brak $TARGET — nic do usunięcia."
    exit 0
fi
if ! head -1 "$TARGET" | grep -qF "$MARKER"; then
    echo "$TARGET nie ma znacznika Tide — to nie nasz profil, nie usuwam go." >&2
    exit 1
fi

echo "Zostanie usunięty z jądra i z dysku: $TARGET (profil bwrap)."
echo "Piaskownica Claude Code przestanie działać (bwrap znów zablokowany przez AppArmor)."
read -r -p "Kontynuować? [t/N] " answer
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

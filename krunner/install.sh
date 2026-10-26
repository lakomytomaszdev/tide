#!/usr/bin/env bash
# Instaluje albo usuwa wtyczkę KRunnera Tide: plik .desktop w $XDG_DATA_HOME/krunner/dbusplugins.
# Użycie: bash krunner/install.sh install|uninstall
#
# Samą wtyczkę obsługuje tide-daemon (nazwa org.tide.Daemon.KRunner, obiekt /KRunner), który
# instaluje się przez `make install-daemon`. KRunner czyta wtyczki D-Bus przy starcie, więc po
# zmianie prosimy działający KRunner o zamknięcie — uruchomi się sam przy następnym Alt+Spacja
# (aktywacja D-Bus). Wyszukiwanie w menu Plasmy ma własną listę wtyczek i zobaczy zmianę
# dopiero po ponownym zalogowaniu (albo `systemctl --user restart plasma-plasmashell`).
set -euo pipefail

action="${1:-}"
src="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
dir="$data/krunner/dbusplugins"
file="$dir/tide.desktop"

restart_krunner() {
    # Najlepszy możliwy wysiłek: KRunner może nie działać (wtedy i tak przeczyta wtyczki przy starcie).
    qdbus6 org.kde.krunner /MainApplication quit >/dev/null 2>&1 || true
}

case "$action" in
    install)
        mkdir -p "$dir"
        install -m 0644 "$src/tide.desktop" "$file"
        restart_krunner
        echo "Zainstalowano wtyczkę KRunnera Tide: $file"
        ;;
    uninstall)
        rm -f "$file"
        rmdir "$dir" "$data/krunner" 2>/dev/null || true
        restart_krunner
        echo "Odinstalowano wtyczkę KRunnera Tide."
        ;;
    *)
        echo "Nieznana akcja: $action (install|uninstall)" >&2
        exit 2
        ;;
esac

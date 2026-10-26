#!/usr/bin/env bash
# Instaluje albo usuwa okno „Tide Sesje” (kopią, nie dowiązaniem).
# Użycie: bash apps/sessions/install.sh install|uninstall
# Pliki: $XDG_DATA_HOME/tide/sessions-window/ (launcher, QML z biblioteką i paletą)
#        $XDG_DATA_HOME/applications/tide-sessions.desktop (ikona i nazwa w docku i menu),
#        $XDG_DATA_HOME/icons/hicolor/256x256/apps/tide-sessions.png (stonowany glif, także bez nakładki ikon).
# Okno otwierają: widżet agentów („Sesje”, postęp koordynatora), KRunner („sesje”) — przez tide-daemon
# (/Coordination ShowWindow) — albo menu aplikacji.
set -euo pipefail

action="${1:-}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
dest="$data/tide/sessions-window"
desktop="$data/applications/tide-sessions.desktop"
icon_dir="$data/icons/hicolor/256x256/apps"
icon="$icon_dir/tide-sessions.png"

refresh_menu() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$data/applications" >/dev/null 2>&1 || true
    fi
    if command -v kbuildsycoca6 >/dev/null 2>&1; then
        kbuildsycoca6 >/dev/null 2>&1 || true
    fi
}

case "$action" in
    install)
        mkdir -p "$(dirname "$dest")" "$(dirname "$desktop")"
        /usr/bin/python3 "$root/widgets/stage.py" --app "$root/apps/sessions" "$dest"
        chmod 755 "$dest/tide-sessions.py"
        if ! /usr/bin/python3 "$root/theme/icons/tide_icons.py" glyph tide-sessions --out "$icon"; then
            echo "Uwaga: nie udało się narysować ikony tide-sessions (bez nakładki Tide okno nie będzie miało ikony)." >&2
        fi
        cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tide Sesje
GenericName=Koordynacja agentów
Comment=Koordynatorzy, tickety i podagenci: co w toku, co czeka na ciebie, co po review
Exec=/usr/bin/python3 -s $dest/tide-sessions.py
Icon=tide-sessions
Terminal=false
Categories=Development;
Keywords=agent;claude;codex;ticket;youtrack;plan;
StartupNotify=true
StartupWMClass=tide-sessions
X-Tide-Component=sessions-window
EOF
        refresh_menu
        echo "Zainstalowano okno Tide Sesje: $dest"
        ;;
    uninstall)
        rm -rf "$dest"
        rm -f "$icon"
        rmdir "$icon_dir" "$data/icons/hicolor/256x256" "$data/icons/hicolor" "$data/icons" 2>/dev/null || true
        if [[ -f "$desktop" ]] && grep -q '^X-Tide-Component=sessions-window$' "$desktop"; then
            rm -f "$desktop"
        fi
        rmdir "$data/tide" 2>/dev/null || true
        refresh_menu
        echo "Odinstalowano okno Tide Sesje."
        ;;
    *)
        echo "Nieznana akcja: $action (install|uninstall)" >&2
        exit 2
        ;;
esac

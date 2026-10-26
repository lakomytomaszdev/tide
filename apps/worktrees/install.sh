#!/usr/bin/env bash
# Instaluje albo usuwa okno „Tide Worktrees” (kopią, nie dowiązaniem).
# Użycie: bash apps/worktrees/install.sh install|uninstall
# Pliki: $XDG_DATA_HOME/tide/worktrees-window/ (launcher, QML z biblioteką i paletą)
#        $XDG_DATA_HOME/applications/tide-worktrees.desktop (ikona i nazwa w docku i menu),
#        $XDG_DATA_HOME/icons/hicolor/256x256/apps/tide-worktrees.png (zapasowa ikona: stonowany glif,
#        ten sam, który daje nakładka ikon Tide — okno ma ikonę także bez niej).
# Okno otwiera kafelek org.tide.worktrees przez tide-daemon (ShowWindow) albo menu aplikacji.
set -euo pipefail

action="${1:-}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
dest="$data/tide/worktrees-window"
desktop="$data/applications/tide-worktrees.desktop"
icon_dir="$data/icons/hicolor/256x256/apps"
icon="$icon_dir/tide-worktrees.png"

refresh_menu() {
    # Pamięć podręczna menu KDE i baza plików .desktop — żeby dock od razu znał ikonę i nazwę.
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
        /usr/bin/python3 "$root/widgets/stage.py" --app "$root/apps/worktrees" "$dest"
        chmod 755 "$dest/tide-worktrees.py"
        if ! /usr/bin/python3 "$root/theme/icons/tide_icons.py" glyph tide-worktrees --out "$icon"; then
            echo "Uwaga: nie udało się narysować ikony tide-worktrees (bez nakładki Tide okno nie będzie miało ikony)." >&2
        fi
        cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tide Worktrees
GenericName=Worktrees
Comment=Worktrees repozytoriów: co w pracy, co do sprzątnięcia, co do decyzji
Exec=/usr/bin/python3 -s $dest/tide-worktrees.py
Icon=tide-worktrees
Terminal=false
Categories=Development;
Keywords=git;worktree;branch;
StartupNotify=true
StartupWMClass=tide-worktrees
X-Tide-Component=worktrees-window
EOF
        refresh_menu
        echo "Zainstalowano okno Tide Worktrees: $dest"
        ;;
    uninstall)
        rm -rf "$dest"
        rm -f "$icon"
        # Puste katalogi hicolor po naszej ikonie; cudzych plików rmdir nie ruszy.
        rmdir "$icon_dir" "$data/icons/hicolor/256x256" "$data/icons/hicolor" "$data/icons" 2>/dev/null || true
        # Tylko nasz plik .desktop (ze znacznikiem) — cudzego o tej nazwie nie ruszamy.
        if [[ -f "$desktop" ]] && grep -q '^X-Tide-Component=worktrees-window$' "$desktop"; then
            rm -f "$desktop"
        fi
        rmdir "$data/tide" 2>/dev/null || true
        refresh_menu
        echo "Odinstalowano okno Tide Worktrees."
        ;;
    *)
        echo "Nieznana akcja: $action (install|uninstall)" >&2
        exit 2
        ;;
esac

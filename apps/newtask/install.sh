#!/usr/bin/env bash
# Instaluje albo usuwa okno „Tide: nowe zadanie” (kopią, nie dowiązaniem).
# Użycie: bash apps/newtask/install.sh install|uninstall
# Pliki: $XDG_DATA_HOME/tide/new-task/ (launcher, QML z biblioteką i paletą),
#        $XDG_DATA_HOME/applications/tide-new-task.desktop (nazwa i ikona w menu i pasku zadań),
#        $XDG_DATA_HOME/icons/hicolor/256x256/apps/tide-new-task.png (glif, także bez nakładki ikon),
#        $XDG_DATA_HOME/konsole/TideTask.profile — profil „Tide: zadanie agenta”, którym tide-daemon
#        otwiera kartę z agentem w istniejącym oknie Konsoli (polecenie: launch.py usługi; wygląd
#        dziedziczy po domyślnym profilu z konsolerc). Bez profilu zadanie otwiera nowe okno.
# Okno otwierają: skrót KWin (make install-new-task-shortcut), przycisk w widżecie agentów
# i widżet „Tide · Zadanie” — wszystkie przez tide-daemon (/Tasks ShowDialog).
set -euo pipefail

action="${1:-}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
dest="$data/tide/new-task"
desktop="$data/applications/tide-new-task.desktop"
icon_dir="$data/icons/hicolor/256x256/apps"
icon="$icon_dir/tide-new-task.png"
profile="$data/konsole/TideTask.profile"
launcher="$data/tide/daemon/tide_daemon/tasks/launch.py"

refresh_menu() {
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$data/applications" >/dev/null 2>&1 || true
    fi
    if command -v kbuildsycoca6 >/dev/null 2>&1; then
        kbuildsycoca6 >/dev/null 2>&1 || true
    fi
}

default_profile() {
    # DefaultProfile z konsolerc (np. Tide.profile z make install-konsole); bez niego — wbudowany.
    local value=""
    if [[ -f "$config/konsolerc" ]]; then
        value="$(awk -F= '/^\[/{g=$0} g=="[Desktop Entry]" && $1=="DefaultProfile"{print $2}' "$config/konsolerc" \
                 | tail -n1)"
    fi
    if [[ -z "$value" || "$value" == "TideTask.profile" ]]; then
        value="FALLBACK/"
    fi
    printf '%s' "$value"
}

case "$action" in
    install)
        mkdir -p "$(dirname "$dest")" "$(dirname "$desktop")" "$(dirname "$profile")"
        /usr/bin/python3 "$root/widgets/stage.py" --app "$root/apps/newtask" "$dest"
        chmod 755 "$dest/tide-new-task.py"
        if ! /usr/bin/python3 "$root/theme/icons/tide_icons.py" glyph tide-new-task --out "$icon"; then
            echo "Uwaga: nie udało się narysować ikony tide-new-task." >&2
        fi
        cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tide: nowe zadanie
GenericName=Nowe zadanie dla agenta
Comment=Repozytorium, Claude Code albo Codex, polecenie i nowy worktree — agent startuje w Konsoli
Exec=/usr/bin/python3 -s $dest/tide-new-task.py
Icon=tide-new-task
Terminal=false
Categories=Development;
Keywords=agent;claude;codex;worktree;zadanie;
StartupNotify=true
StartupWMClass=tide-new-task
X-Tide-Component=new-task
EOF
        command="/usr/bin/python3 -s $launcher"
        if [[ "$launcher" == *" "* ]]; then
            command="/usr/bin/python3 -s '$launcher'"
        fi
        cat > "$profile" <<EOF
[General]
Name=Tide: zadanie agenta
Parent=$(default_profile)
Command=$command
X-Tide-Component=new-task
EOF
        refresh_menu
        if [[ ! -f "$launcher" ]]; then
            echo "Uwaga: brak $launcher — zainstaluj usługę (make install-daemon), zanim uruchomisz zadanie."
        fi
        echo "Zainstalowano okno Tide: nowe zadanie: $dest"
        ;;
    uninstall)
        rm -rf "$dest"
        rm -f "$icon"
        rmdir "$icon_dir" "$data/icons/hicolor/256x256" "$data/icons/hicolor" "$data/icons" 2>/dev/null || true
        if [[ -f "$desktop" ]] && grep -q '^X-Tide-Component=new-task$' "$desktop"; then
            rm -f "$desktop"
        fi
        if [[ -f "$profile" ]] && grep -q '^X-Tide-Component=new-task$' "$profile"; then
            rm -f "$profile"
        fi
        rmdir "$data/konsole" "$data/tide" 2>/dev/null || true
        refresh_menu
        echo "Odinstalowano okno Tide: nowe zadanie."
        ;;
    *)
        echo "Nieznana akcja: $action (install|uninstall)" >&2
        exit 2
        ;;
esac

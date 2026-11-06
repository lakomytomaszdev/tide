#!/usr/bin/env bash
# Instaluje albo usuwa okno „Tide” z paskiem widoków (kopią, nie dowiązaniem).
# Użycie: bash apps/tide/install.sh install|uninstall
# Pliki: $XDG_DATA_HOME/tide/tide-window/ (launcher tide.py, QML z biblioteką i paletą, moduły widoków
#        w views/<id>/ z views.json),
#        $XDG_DATA_HOME/applications/tide.desktop (nazwa i ikona w docku i menu, akcje „Tablica”,
#        „Sesje”, „Worktrees”, „Docker”, „Raporty”, „Modele” w menu kontekstowym),
#        $XDG_DATA_HOME/icons/hicolor/scalable/apps/tide.svg (logo „trzy fale” z palety).
# Zgodność z dawnymi oknami Sesje i Worktrees (stały się widokami):
#        $XDG_DATA_HOME/tide/sessions-window/tide-sessions.py i tide/worktrees-window/tide-worktrees.py —
#        cienkie przekierowania do okna Tide na widoku (tide-daemon sprzed restartu, przypięte skróty);
#        tide-sessions.desktop i tide-worktrees.desktop — wejścia do menu „Tide Sesje” i „Tide Worktrees”,
#        które otwierają okno Tide od razu na widoku (przypięte w docku dalej działają), z glifami
#        w hicolor/256x256 jak dotąd. Wszystkie trzy mają StartupWMClass=tide: okno jest jedno, więc
#        pasek zadań grupuje je pod tide.desktop (przypięty „Tide Sesje” pokazuje okno Tide, nie osobne).
# Cudzego pliku .desktop o tej samej nazwie (bez znacznika Tide) instalacja nie nadpisuje.
# Okno otwierają: widżety (przez tide-daemon: /Coordination i /Worktrees ShowWindow), KRunner
# („sesje”), menu aplikacji; kolejne uruchomienie prosi działające okno o Show(widok, element).
set -euo pipefail

action="${1:-}"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
dest="$data/tide/tide-window"
apps="$data/applications"
desktop="$apps/tide.desktop"
logo="$data/icons/hicolor/scalable/apps/tide.svg"
glyph_dir="$data/icons/hicolor/256x256/apps"
palette="$root/theme/palette/mist.json"
marker="X-Tide-Component=tide-window"

# Dawne okna: katalog z przekierowaniem, plik .desktop, glif i znacznik sprzed zmiany.
legacy_views=(sessions worktrees)
declare -A legacy_dir=([sessions]="$data/tide/sessions-window" [worktrees]="$data/tide/worktrees-window")
declare -A legacy_script=([sessions]="tide-sessions.py" [worktrees]="tide-worktrees.py")
declare -A legacy_name=([sessions]="tide-sessions" [worktrees]="tide-worktrees")
declare -A legacy_marker=([sessions]="sessions-window" [worktrees]="worktrees-window")

refresh_menu() {
    # Pamięć podręczna menu KDE i baza plików .desktop — żeby dock od razu znał ikonę i nazwę.
    if command -v update-desktop-database >/dev/null 2>&1; then
        update-desktop-database "$apps" >/dev/null 2>&1 || true
    fi
    if command -v kbuildsycoca6 >/dev/null 2>&1; then
        kbuildsycoca6 >/dev/null 2>&1 || true
    fi
}

# Nasz plik .desktop (ze znacznikiem okna Tide albo dawnego okna) — cudzego o tej nazwie nie ruszamy.
ours() {
    local file="$1" old="${2:-}"
    [[ -f "$file" ]] || return 1
    grep -q "^$marker\$" "$file" && return 0
    [[ -n "$old" ]] && grep -q "^X-Tide-Component=$old\$" "$file"
}

# Instalacja jak deinstalacja: cudzego pliku .desktop o tej samej nazwie nie nadpisujemy, tylko o nim mówimy.
may_write() {
    local file="$1" old="${2:-}"
    if [[ -e "$file" ]] && ! ours "$file" "$old"; then
        echo "Uwaga: $file nie jest plikiem Tide — zostawiam go bez zmian." >&2
        return 1
    fi
}

exec_line() {
    echo "/usr/bin/python3 -s $dest/tide.py$1"
}

write_main_desktop() {
    may_write "$desktop" || return 0
    cat > "$desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Tide
GenericName=Praca z agentami
Comment=Tablica, sesje agentów, worktrees, Docker, raporty i modele lokalne w jednym oknie
Exec=$(exec_line "")
Icon=tide
Terminal=false
Categories=Development;
Keywords=agent;claude;codex;tablica;sesje;worktree;docker;raport;model;ollama;
StartupNotify=true
StartupWMClass=tide
Actions=board;issues;sessions;worktrees;docker;reports;models;
$marker

[Desktop Action board]
Name=Tablica
Exec=$(exec_line " --view board")

[Desktop Action sessions]
Name=Sesje
Exec=$(exec_line " --view sessions")

[Desktop Action issues]
Name=Sprawy
Exec=$(exec_line " --view issues")

[Desktop Action worktrees]
Name=Worktrees
Exec=$(exec_line " --view worktrees")

[Desktop Action docker]
Name=Docker
Exec=$(exec_line " --view docker")

[Desktop Action reports]
Name=Raporty
Exec=$(exec_line " --view reports")

[Desktop Action models]
Name=Modele
Exec=$(exec_line " --view models")
EOF
}

write_view_desktop() {
    local view="$1" name="$2" generic="$3" comment="$4" keywords="$5"
    may_write "$apps/${legacy_name[$view]}.desktop" "${legacy_marker[$view]}" || return 0
    cat > "$apps/${legacy_name[$view]}.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=$name
GenericName=$generic
Comment=$comment
Exec=$(exec_line " --view $view")
Icon=${legacy_name[$view]}
Terminal=false
Categories=Development;
Keywords=$keywords
StartupNotify=true
StartupWMClass=tide
$marker
EOF
}

case "$action" in
    install)
        mkdir -p "$(dirname "$dest")" "$apps" "$(dirname "$logo")" "$glyph_dir"
        /usr/bin/python3 "$root/widgets/stage.py" --app "$root/apps/tide" "$dest"
        chmod 755 "$dest/tide.py"

        tmp="$(mktemp -d)"
        trap 'rm -rf "$tmp"' EXIT
        /usr/bin/python3 "$root/theme/logo/generate.py" "$palette" "$tmp"
        cp "$tmp/tide-logo.svg" "$logo"

        # Dawne okna: pełne pakiety zastępuje samo przekierowanie pod tą samą ścieżką.
        for view in "${legacy_views[@]}"; do
            rm -rf "${legacy_dir[$view]}"
            mkdir -p "${legacy_dir[$view]}"
            cp "$root/apps/tide/compat/${legacy_script[$view]}" "${legacy_dir[$view]}/${legacy_script[$view]}"
            chmod 755 "${legacy_dir[$view]}/${legacy_script[$view]}"
            if ! /usr/bin/python3 "$root/theme/icons/tide_icons.py" glyph "${legacy_name[$view]}" \
                    --out "$glyph_dir/${legacy_name[$view]}.png"; then
                echo "Uwaga: nie udało się narysować ikony ${legacy_name[$view]}." >&2
            fi
        done

        write_main_desktop
        write_view_desktop sessions "Tide Sesje" "Koordynacja agentów" \
            "Okno Tide na widoku Sesje: koordynatorzy, tickety i podagenci" "agent;claude;codex;ticket;youtrack;plan;"
        write_view_desktop worktrees "Tide Worktrees" "Worktrees" \
            "Okno Tide na widoku Worktrees: co w pracy, co do sprzątnięcia, co do decyzji" "git;worktree;branch;"
        refresh_menu
        echo "Zainstalowano okno Tide: $dest"
        ;;
    uninstall)
        rm -rf "$dest"
        rm -f "$logo"
        if ours "$desktop"; then rm -f "$desktop"; fi
        for view in "${legacy_views[@]}"; do
            rm -rf "${legacy_dir[$view]}"
            rm -f "$glyph_dir/${legacy_name[$view]}.png"
            if ours "$apps/${legacy_name[$view]}.desktop" "${legacy_marker[$view]}"; then
                rm -f "$apps/${legacy_name[$view]}.desktop"
            fi
        done
        # Puste katalogi po naszych plikach; cudzych plików rmdir nie ruszy.
        rmdir "$glyph_dir" "$data/icons/hicolor/256x256" "$(dirname "$logo")" "$data/icons/hicolor/scalable" \
              "$data/icons/hicolor" "$data/icons" "$data/tide" 2>/dev/null || true
        refresh_menu
        echo "Odinstalowano okno Tide."
        ;;
    *)
        echo "Nieznana akcja: $action (install|uninstall)" >&2
        exit 2
        ;;
esac

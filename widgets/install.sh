#!/usr/bin/env bash
# Instaluje albo usuwa widżet Plasmy przez kpackagetool6.
# Użycie: bash widgets/install.sh install|uninstall <katalog-widżetu>   (np. widgets/agents)
# Pakiet jest najpierw składany w $BUILD_DIR (domyślnie build/) razem z biblioteką i paletą.
set -euo pipefail

action="$1"
widget="$(cd "$2" && pwd)"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build="${BUILD_DIR:-$root/build}"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
id="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["KPlugin"]["Id"])' "$widget/metadata.json")"
installed="$data/plasma/plasmoids/$id"

case "$action" in
    install)
        staged="$build/widgets/$id"
        /usr/bin/python3 "$root/widgets/stage.py" "$widget" "$staged"
        if [[ -d "$installed" ]]; then
            kpackagetool6 -t Plasma/Applet -u "$staged"
            echo "Zaktualizowano widżet $id. Nowy kod Plasma wczyta po: systemctl --user restart plasma-plasmashell"
        else
            kpackagetool6 -t Plasma/Applet -i "$staged"
            echo "Zainstalowano widżet $id — dodaj go do panelu z listy widżetów."
        fi
        ;;
    uninstall)
        if [[ -d "$installed" ]]; then
            kpackagetool6 -t Plasma/Applet -r "$id"
        fi
        echo "Odinstalowano widżet $id."
        ;;
    *)
        echo "Nieznana akcja: $action (install|uninstall)" >&2
        exit 2
        ;;
esac

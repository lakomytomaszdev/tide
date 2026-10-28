#!/usr/bin/env bash
# Instaluje albo usuwa widżet Plasmy przez kpackagetool6.
# Użycie: bash widgets/install.sh install|uninstall <katalog-widżetu>   (np. widgets/agents)
# Pakiet jest najpierw składany w $BUILD_DIR (domyślnie build/) razem z biblioteką i paletą.
# Plik z tokenem (klucz "X-Tide-LimityConfig" w metadata.json) zostaje tylko w zainstalowanym
# pakiecie z trybem 0600 — ze złożonej kopii w $BUILD_DIR jest usuwany zaraz po instalacji.
# Po instalacji widgets/stale.py wypisuje widżety, których nowego kodu Plasma jeszcze nie wczytała.
set -euo pipefail

action="$1"
widget="$(cd "$2" && pwd)"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
build="${BUILD_DIR:-$root/build}"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
read -r id secret < <(/usr/bin/python3 -c '
import json, sys
meta = json.load(open(sys.argv[1]))
print(meta["KPlugin"]["Id"], meta.get("X-Tide-LimityConfig", ""))' "$widget/metadata.json")
installed="$data/plasma/plasmoids/$id"

case "$action" in
    install)
        staged="$build/widgets/$id"
        if [[ -n "$secret" ]]; then
            trap 'rm -f "$staged/$secret"' EXIT
        fi
        /usr/bin/python3 "$root/widgets/stage.py" "$widget" "$staged"
        if [[ -d "$installed" ]]; then
            kpackagetool6 -t Plasma/Applet -u "$staged"
            echo "Zaktualizowano widżet $id."
        else
            kpackagetool6 -t Plasma/Applet -i "$staged"
            echo "Zainstalowano widżet $id — dodaj go do panelu z listy widżetów."
        fi
        # kpackagetool6 nie musi zachować trybu pliku — token ma być czytelny tylko dla właściciela.
        if [[ -n "$secret" && -f "$installed/$secret" ]]; then
            chmod 600 "$installed/$secret"
        fi
        # Wszystkie widżety Tide nowsze od działającej powłoki, nie tylko ten (PT-32).
        /usr/bin/python3 "$root/widgets/stale.py"
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

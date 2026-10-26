#!/usr/bin/env bash
# Instaluje albo usuwa skrypt KWin Tide (kopią, przez kpackagetool6) i włącza go w kwinrc.
# Użycie: bash kwin/install.sh install|uninstall <katalog-skryptu>   (np. kwin/agent-jump)
#
# Po instalacji KWin dostaje prośbę o ponowne wczytanie konfiguracji i skryptów
# (org.kde.KWin /KWin reconfigure, /Scripting start). Gdy KWin nie działa (np. w testach),
# skrypt i tak wczyta się przy następnym logowaniu.
set -euo pipefail

action="$1"
script="$(cd "$2" && pwd)"
root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
data="${XDG_DATA_HOME:-$HOME/.local/share}"
config="${XDG_CONFIG_HOME:-$HOME/.config}"
id="$(/usr/bin/python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["KPlugin"]["Id"])' "$script/metadata.json")"
installed="$data/kwin/scripts/$id"
shortcut_key="Meta+A"
shortcut_action="TideJumpToWaitingAgent"

kwin_call() {
    # Najlepszy możliwy wysiłek: KWin może nie działać albo nie znać metody.
    dbus-send --session --type=method_call --print-reply --dest=org.kde.KWin "$@" >/dev/null 2>&1 || true
}

case "$action" in
    install)
        if [[ -d "$installed" ]]; then
            kpackagetool6 -t KWin/Script -u "$script"
        else
            kpackagetool6 -t KWin/Script -i "$script"
        fi
        kwriteconfig6 --file kwinrc --group Plugins --key "${id}Enabled" true
        kwin_call /KWin org.kde.KWin.reconfigure
        kwin_call /Scripting org.kde.kwin.Scripting.unloadScript "string:$id"   # nowa wersja po aktualizacji
        kwin_call /Scripting org.kde.kwin.Scripting.start
        echo "Zainstalowano skrypt KWin $id (skrót $shortcut_key)."
        taken="$(/usr/bin/python3 "$root/kwin/shortcuts.py" "$config/kglobalshortcutsrc" "$shortcut_key" \
                 "kwin/$shortcut_action")"
        if [[ -n "$taken" ]]; then
            echo "Uwaga: $shortcut_key ma już przypisane:"
            echo "$taken" | sed 's/^/  - /'
            echo "KDE nie nada wtedy skrótu nowej akcji. Zwolnij go w Ustawieniach systemowych → Skróty,"
            echo "a potem przypisz $shortcut_key akcji „Tide: przejdź do agenta, który czeka najdłużej” (KWin)."
        fi
        ;;
    uninstall)
        kwin_call /Scripting org.kde.kwin.Scripting.unloadScript "string:$id"
        kwriteconfig6 --file kwinrc --group Plugins --key "${id}Enabled" --delete
        dbus-send --session --type=method_call --print-reply --dest=org.kde.kglobalaccel /kglobalaccel \
            org.kde.KGlobalAccel.unregister string:kwin "string:$shortcut_action" >/dev/null 2>&1 || true
        if [[ -d "$installed" ]]; then
            kpackagetool6 -t KWin/Script -r "$id"
        fi
        echo "Odinstalowano skrypt KWin $id."
        ;;
    *)
        echo "Nieznana akcja: $action (install|uninstall)" >&2
        exit 2
        ;;
esac

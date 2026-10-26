#!/bin/sh
# Podgląd paska bocznego Tide w zagnieżdżonej sesji, bez dotykania bieżącej.
#
# Użycie: sh scripts/sidebar_preview.sh <katalog na wyniki>
#
# Wirtualny KWin (kwin_wayland --virtual, dwa wyjścia 1920×1080, bez ekranu) z obsługą
# skrótów globalnych (KGlobalAccel działa w KWinie), na prywatnej magistrali D-Bus
# (dbus-run-session) i z tymczasowymi XDG_CONFIG_HOME/XDG_DATA_HOME/XDG_STATE_HOME.
# Do „domu” sesji trafiają: kopie kdeglobals i plasmarc (kolory, czcionki, styl), czcionki,
# styl Plasmy Tide wygenerowany z bieżącego drzewa (z ramką „west”) i widżety Tide złożone
# z bieżącego drzewa. Potem prawdziwe scripts/panels.py i scripts/sidebar.py instalują paski
# w zagnieżdżonym plasmashell. Prawdziwy ~/.local/share jest w XDG_DATA_DIRS tylko do odczytu
# (ikony, usługi D-Bus na żądanie, np. tide-daemon — uruchomione na prywatnej magistrali
# kończą się razem z nią).
#
# Wyniki: hidden.png (pasek schowany), revealed.png (po skrócie — ta sama akcja, którą KWin
# wywołuje na Meta+S), rehidden.png (po czasie sam się chowa), install*.log, state.txt
# (panele i skróty), uninstall.log, plasmashell.log, kwin.log.
set -eu

OUT=$(realpath -m "$1")
ROOT=$(cd "$(dirname "$0")/.." && pwd)
REAL_CONFIG=${XDG_CONFIG_HOME:-$HOME/.config}
REAL_DATA=${XDG_DATA_HOME:-$HOME/.local/share}

WORK=$(mktemp -d "${TMPDIR:-/tmp}/tide-sidebar.XXXXXX")
trap '[ -n "${KEEP:-}" ] || rm -rf "$WORK"' EXIT
mkdir -p "$OUT" "$WORK/config" "$WORK/data" "$WORK/state" "$WORK/cache" "$WORK/runtime" "$WORK/build"
chmod 700 "$WORK/runtime"

for name in kdeglobals plasmarc; do
    [ -f "$REAL_CONFIG/$name" ] && cp "$REAL_CONFIG/$name" "$WORK/config/"
done
# Styl Tide zawsze z bieżącego drzewa — zainstalowana wersja może nie mieć jeszcze ramki „west”.
kwriteconfig6 --file "$WORK/config/plasmarc" --group Theme --key name Tide
# Czcionki fontconfig bierze tylko z XDG_DATA_HOME, więc Geist trzeba skopiować.
[ -d "$REAL_DATA/fonts" ] && cp -r "$REAL_DATA/fonts" "$WORK/data/"
/usr/bin/python3 "$ROOT/theme/plasma/generate.py" "$ROOT/theme/palette/mist.json" \
    "$WORK/data/plasma/desktoptheme/Tide"
/usr/bin/python3 "$ROOT/theme/wallpaper/generate.py" "$ROOT/theme/palette/mist.json" "$WORK/wallpaper" > /dev/null
for widget in sidebar agents system worktrees limits; do
    XDG_DATA_HOME="$WORK/data" BUILD_DIR="$WORK/build" \
        LIMITY_CONFIG="${LIMITY_CONFIG:-$REAL_CONFIG/limity-ai-widget/config.json}" \
        bash "$ROOT/widgets/install.sh" install "$ROOT/widgets/$widget" > /dev/null
done

SOCKET="tide-sidebar-$$"
env -u WAYLAND_DISPLAY -u DISPLAY -u XDG_SESSION_ID \
    XDG_CONFIG_HOME="$WORK/config" XDG_CONFIG_DIRS=/etc/xdg XDG_DATA_HOME="$WORK/data" \
    XDG_STATE_HOME="$WORK/state" XDG_CACHE_HOME="$WORK/cache" XDG_RUNTIME_DIR="$WORK/runtime" \
    XDG_DATA_DIRS="$REAL_DATA:${XDG_DATA_DIRS:-/usr/local/share:/usr/share}" \
    DBUS_SYSTEM_BUS_ADDRESS="unix:path=$WORK/no-system-bus" \
    TIDE_MAIN_OUTPUT=Virtual-1 TIDE_SIDE_OUTPUT=Virtual-0 \
dbus-run-session -- sh -c '
    S=$1; OUT=$2; WORK=$3; ROOT=$4
    kwin_wayland --virtual --socket "$S" --width 1920 --height 1080 --output-count 2 \
        --no-lockscreen > "$WORK/kwin.log" 2>&1 &
    kwin=$!
    for _ in $(seq 30); do
        [ -S "$XDG_RUNTIME_DIR/$S" ] && break
        sleep 0.5
    done
    dbus-update-activation-environment WAYLAND_DISPLAY="$S" XDG_RUNTIME_DIR="$XDG_RUNTIME_DIR"
    export WAYLAND_DISPLAY="$S"
    plasmashell --no-respawn > "$WORK/plasmashell.log" 2>&1 &
    for _ in $(seq 60); do
        qdbus6 org.kde.plasmashell >/dev/null 2>&1 && break
        sleep 1
    done
    sleep 15
    shot() { spectacle --background --nonotify --fullscreen --output "$OUT/$1" >> "$WORK/spectacle.log" 2>&1 || true; }

    # Tylko w zagnieżdżonej sesji: domyślny układ nowego plasmashell stawia dolny panel na
    # pierwszym wyjściu, czyli tu na „lewym” ekranie — w prawdziwym układzie dock jest na
    # głównym. Tapeta Tide (wycinek lewego monitora), żeby przezroczystość wyglądała jak na pulpicie.
    qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript "
        var all = panels();
        for (var i = 0; i < all.length; ++i) { if (all[i].location === \"bottom\") all[i].remove(); }
        var ds = desktops();
        for (var j = 0; j < ds.length; ++j) {
            ds[j].wallpaperPlugin = \"org.kde.image\";
            ds[j].currentConfigGroup = [\"Wallpaper\", \"org.kde.image\", \"General\"];
            ds[j].writeConfig(\"Image\", \"file://$WORK/wallpaper/HDMI-A-1.png\");
        }" > /dev/null

    /usr/bin/python3 "$ROOT/scripts/panels.py" install > "$OUT/install-panels.log" 2>&1 || true
    /usr/bin/python3 "$ROOT/scripts/sidebar.py" install > "$OUT/install.log" 2>&1
    sleep 8
    shot hidden.png
    header=$(/usr/bin/python3 -c "import json,sys; print(json.load(open(sys.argv[1]))[\"header\"])" \
             "$XDG_STATE_HOME/tide/sidebar-installed.json")
    # To samo, co robi KWin po naciśnięciu Meta+S: wyzwala akcję skrótu nagłówka.
    qdbus6 org.kde.kglobalaccel /component/plasmashell org.kde.kglobalaccel.Component.invokeShortcut \
        "activate widget $header" >> "$OUT/install.log" 2>&1
    sleep 3
    shot revealed.png
    sleep 10
    shot rehidden.png

    /usr/bin/python3 "$ROOT/scripts/sidebar.py" install > "$OUT/install-again.log" 2>&1
    {
        echo "# Skróty komponentu plasmashell w KGlobalAccel (zagnieżdżony KWin):"
        qdbus6 org.kde.kglobalaccel /component/plasmashell org.kde.kglobalaccel.Component.shortcutNames
        echo "# kglobalshortcutsrc (tymczasowy):"
        grep -A2 "activate widget" "$XDG_CONFIG_HOME/kglobalshortcutsrc" || true
        echo "# Panele po drugiej instalacji:"
        /usr/bin/python3 "$ROOT/scripts/panels.py" dump | /usr/bin/python3 -c "
import json, sys
for p in json.load(sys.stdin)[\"panels\"]:
    print(p[\"id\"], p[\"screen\"], p[\"location\"], p.get(\"hiding\"), p[\"height\"],
          [w[\"type\"].rsplit(\".\", 1)[-1] for w in p[\"widgets\"]])"
    } > "$OUT/state.txt" 2>&1
    /usr/bin/python3 "$ROOT/scripts/sidebar.py" uninstall > "$OUT/uninstall.log" 2>&1
    {
        echo "# Panele po odinstalowaniu:"
        /usr/bin/python3 "$ROOT/scripts/panels.py" dump | /usr/bin/python3 -c "
import json, sys
for p in json.load(sys.stdin)[\"panels\"]:
    print(p[\"id\"], p[\"screen\"], p[\"location\"])"
        echo "# Skróty plasmashell po odinstalowaniu:"
        qdbus6 org.kde.kglobalaccel /component/plasmashell org.kde.kglobalaccel.Component.shortcutNames
    } >> "$OUT/state.txt" 2>&1

    kill "$kwin" 2>/dev/null || true
    wait "$kwin" 2>/dev/null || true
    # Wszystko, co prywatna magistrala uruchomiła na żądanie, kończy się razem z nią.
    for pid in $(pgrep -u "$(id -u)"); do
        [ "$pid" = "$$" ] && continue
        if { tr "\0" "\n" < "/proc/$pid/environ"; } 2>/dev/null | grep -qx "DBUS_SESSION_BUS_ADDRESS=$DBUS_SESSION_BUS_ADDRESS"; then
            kill "$pid" 2>/dev/null || true
        fi
    done
' sh "$SOCKET" "$OUT" "$WORK" "$ROOT" 2> "$OUT/session.log"

cp "$WORK/kwin.log" "$WORK/plasmashell.log" "$OUT/" 2>/dev/null || true
cat "$OUT/install.log"
ls -l "$OUT"/*.png 2>/dev/null || true

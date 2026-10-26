#!/usr/bin/env bash
# Podmienia w dolnym panelu stary widżet Limity AI (org.limityai.widget z QuotaApp)
# na org.tide.limits, w tym samym miejscu panelu. Działa przez skrypt Plasmy
# (org.kde.PlasmaShell.evaluateScript), więc bez restartu plasmashell.
#
# Użycie: bash scripts/swap-limits-widget.sh [--print]
#   --print              tylko wypisuje skrypt JS, niczego nie zmienia
#   TIDE_PANEL_SCREEN=n  ogranicza podmianę do dolnego panelu na ekranie n (domyślnie każdy dolny)
#
# Kolejność: Plasma 6 pozwala ustawić pozycję przez widget.index (zapisuje AppletOrder panelu
# i woła reorderApplets). Nowy widżet trafia więc na pozycję starego; gdy stary był ostatni,
# zostaje dopisany na końcu. Panel zablokowany (Zablokuj widżety) trzeba najpierw odblokować.
set -euo pipefail

OLD="org.limityai.widget"
NEW="org.tide.limits"
screen="${TIDE_PANEL_SCREEN:--1}"
[[ "$screen" =~ ^-?[0-9]+$ ]] || { echo "TIDE_PANEL_SCREEN musi być liczbą" >&2; exit 2; }

script() {
    printf 'var OLD = "%s";\nvar NEW = "%s";\nvar SCREEN = %s;\n' "$OLD" "$NEW" "$screen"
    cat <<'JS'
// Podmiana widżetu Limity AI w dolnym panelu (wygenerowane przez scripts/swap-limits-widget.sh).
var report = [];
panels().forEach(function (panel) {
    if (panel.location !== "bottom") return;
    if (SCREEN >= 0 && panel.screen !== SCREEN) return;
    var olds = panel.widgets(OLD);
    if (olds.length === 0) return;
    panel.currentConfigGroup = ["General"];
    var order = String(panel.readConfig("AppletOrder", "")).split(";")
        .filter(function (s) { return s !== ""; })
        .map(function (s) { return parseInt(s, 10); });
    panel.currentConfigGroup = [];
    var hasNew = panel.widgets(NEW).length > 0;
    olds.forEach(function (old) {
        var index = order.indexOf(old.id);
        var last = index === order.length - 1;
        var oldId = old.id;
        old.remove();
        if (hasNew) {
            report.push("panel " + panel.id + ": usunięto " + OLD + " #" + oldId + " (" + NEW + " już jest)");
            return;
        }
        var added = panel.addWidget(NEW);
        hasNew = true;
        if (index >= 0 && !last) {
            added.index = index;
        }
        report.push("panel " + panel.id + " (ekran " + panel.screen + "): " + OLD + " #" + oldId
            + " -> " + NEW + " #" + added.id + (index >= 0 && !last ? " na pozycji " + index : " na końcu"));
    });
});
print(report.length ? report.join("\n") : "Nie znaleziono " + OLD + " w dolnym panelu, nic nie zmieniono.");
JS
}

if [[ "${1:-}" == "--print" ]]; then
    script
    exit 0
fi

data="${XDG_DATA_HOME:-$HOME/.local/share}"
if [[ ! -f "$data/plasma/plasmoids/$NEW/metadata.json" ]]; then
    echo "Widżet $NEW nie jest zainstalowany — najpierw: make install-widget-limits" >&2
    exit 1
fi

# Zmiana układu panelu = zmiana wyglądu, więc najpierw kopia zapasowa.
bash "$(dirname "${BASH_SOURCE[0]}")/backup.sh" "swap-limits-$(date +%Y-%m-%d_%H%M%S)"
qdbus6 org.kde.plasmashell /PlasmaShell org.kde.PlasmaShell.evaluateScript "$(script)"

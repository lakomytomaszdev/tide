// Cofnięcie górnych pasków Tide:
// - usuwa pasek lewego monitora (rozpoznany po znaczniku Tide),
// - przywraca widżety i ustawienia głównego paska z zrzutu sprzed instalacji (P.previous).
// Widżety, które przetrwały instalację, zostają (z przywróconymi kluczami, które zmieniliśmy),
// brakujące są tworzone na nowo z zapisaną konfiguracją.

// Panel bez widoku (tidePanelHasView) trafia do `orphans`: jego właściwości widoku nie są czytane ani
// przywracane, ale panele Tide do usunięcia znikają także wtedy.
// Główny pasek bez widoku nie jest przywracany w całości: `skipped` niesie jego id, a instalator zachowuje
// zrzut i ponowi odinstalowanie po włączeniu monitora.
var tideOut = {removed: [], main: null, mainOrder: [], skipped: null, orphans: tideOrphanIds()};

var tideAll = panels();
for (var tideI = 0; tideI < tideAll.length; ++tideI) {
    if (tideIsSideBar(tideAll[tideI])) {
        tideOut.removed.push(tideAll[tideI].id);
        tideAll[tideI].remove();
    }
}

// Klucze w korzeniu konfiguracji zasobnika (to też kontener), którymi zarządza Plasma.
var tideManaged = ["plugin", "immutability", "formfactor", "location", "lastScreen", "activityId",
                   "wallpaperplugin"];

// Zapisuje zrzuconą konfigurację; `only` ogranicza zapis do listy "ścieżka|klucz".
function tideRestore(widget, config, only, defaults) {
    var done = {};
    for (var path in config) {
        if (path.indexOf("/Applets") === 0) {
            continue;
        }
        var group = path === "/" ? [] : path.substring(1).split("/");
        for (var key in config[path]) {
            var id = path + "|" + key;
            if ((path === "/" && tideManaged.indexOf(key) >= 0) || (only && only.indexOf(id) < 0)) {
                continue;
            }
            widget.currentConfigGroup = group;
            widget.writeConfig(key, config[path][key]);
            done[id] = true;
        }
    }
    // Klucz, którego przed instalacją nie było, wraca do wartości domyślnej widżetu.
    for (var missing in (defaults || {})) {
        if (!done[missing] && (!only || only.indexOf(missing) >= 0)) {
            var parts = missing.split("|");
            widget.currentConfigGroup = parts[0] === "/" ? [] : parts[0].substring(1).split("/");
            widget.writeConfig(parts[1], defaults[missing]);
        }
    }
    widget.currentConfigGroup = [];
}

var tidePrevious = null;
if (P.previous) {
    for (var tideJ = 0; tideJ < P.previous.panels.length; ++tideJ) {
        if (P.previous.panels[tideJ].id === P.mainPanel) {
            tidePrevious = P.previous.panels[tideJ];
        }
    }
}
var tideBar = P.mainPanel ? panelById(P.mainPanel) : null;

if (tideBar && P.mainCreated) {
    // Panel powstał przy instalacji (wcześniej go nie było) — usuwamy go w całości.
    tideOut.removed.push(tideBar.id);
    tideBar.remove();
} else if (tideBar && tidePrevious && !tidePanelHasView(tideBar)) {
    tideOut.skipped = tideBar.id;
} else if (tideBar && tidePrevious) {
    var tideWanted = {};
    for (var tideK = 0; tideK < tidePrevious.widgets.length; ++tideK) {
        tideWanted[tidePrevious.widgets[tideK].id] = tidePrevious.widgets[tideK].type;
    }
    var tideNow = tideSorted(tideBar);
    for (var tideL = 0; tideL < tideNow.length; ++tideL) {
        if (tideWanted[tideNow[tideL].id] !== tideNow[tideL].type) {
            tideNow[tideL].remove();
        }
    }
    for (var tideM = 0; tideM < tidePrevious.widgets.length; ++tideM) {
        var tideOld = tidePrevious.widgets[tideM];
        var tideWidget = tideBar.widgetById(tideOld.id);
        if (tideWidget && tideWidget.type === tideOld.type) {
            tideRestore(tideWidget, tideOld.config, P.changedKeys[tideOld.type] || [],
                        P.defaults[tideOld.type]);
        } else {
            tideWidget = tideBar.addWidget(tideOld.type);
            tideRestore(tideWidget, tideOld.config, null, null);
        }
        tideOut.mainOrder.push(tideWidget.id);
    }
    // Wpis zrzutu `orphan` (zrobiony bez widoku) nie ma kluczy widoku — nie zapisujemy `undefined`.
    if (tidePrevious.height !== undefined) {
        tideBar.height = tidePrevious.height;
    }
    if (tidePrevious.floating !== undefined) {
        tideBar.floating = tidePrevious.floating;
    }
    tideOut.main = tideBar.id;
}

tideResult(tideOut);

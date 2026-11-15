// Zrzut paneli przed zmianą: właściwości, widżety w kolejności i ich konfiguracja.
// Tylko odczyt — niczego nie zapisuje.

// Konfiguracja obiektu jako {"/Grupa/Podgrupa": {klucz: wartość}}, jak w dumpCurrentLayoutJS.
// Pomijamy grupy „Applets” — to konfiguracja widżetów potomnych, zrzucana osobno.
// configGroups w korzeniu nie zawsze zwraca grupy obsługiwane przez KConfigXT widżetu
// (np. General w menu, Appearance w zegarze), więc te sprawdzamy zawsze.
var tideKnownGroups = ["General", "Appearance"];

function tideConfig(object) {
    var out = {};
    function walk(path) {
        object.currentConfigGroup = path;
        var keys = object.configKeys;
        var groups = [];
        var listed = object.configGroups;
        for (var g = 0; g < listed.length; ++g) {
            groups.push(listed[g]);
        }
        for (var k = 0; path.length === 0 && k < tideKnownGroups.length; ++k) {
            if (groups.indexOf(tideKnownGroups[k]) < 0) {
                groups.push(tideKnownGroups[k]);
            }
        }
        var values = {};
        for (var i = 0; i < keys.length; ++i) {
            values[keys[i]] = String(object.readConfig(keys[i], ""));
        }
        if (keys.length) {
            out["/" + path.join("/")] = values;
        }
        for (var j = 0; j < groups.length; ++j) {
            if (groups[j] !== "Applets") {
                walk(path.concat([groups[j]]));
            }
        }
    }
    walk([]);
    object.currentConfigGroup = [];
    return out;
}

var tideDump = {screens: tideScreens(), panels: []};
var tideAll = panels();
for (var tideI = 0; tideI < tideAll.length; ++tideI) {
    var tidePanel = tideAll[tideI];
    var tideEntry = {id: tidePanel.id, screen: tidePanel.screen, location: tidePanel.location};
    if (tidePanelHasView(tidePanel)) {
        tideEntry.height = tidePanel.height;
        tideEntry.floating = tidePanel.floating;
        tideEntry.alignment = tidePanel.alignment;
        tideEntry.lengthMode = tidePanel.lengthMode;
        tideEntry.hiding = tidePanel.hiding;
        tideEntry.opacity = tidePanel.opacity;
        tideEntry.offset = tidePanel.offset;
        tideEntry.minimumLength = tidePanel.minimumLength;
        tideEntry.maximumLength = tidePanel.maximumLength;
    } else {
        tideEntry.orphan = true;
    }
    tideEntry.sideBar = tideIsSideBar(tidePanel);
    tideEntry.config = tideConfig(tidePanel);
    tideEntry.widgets = [];
    var tideWidgets = tideSorted(tidePanel);
    for (var tideJ = 0; tideJ < tideWidgets.length; ++tideJ) {
        var tideWidget = tideWidgets[tideJ];
        tideEntry.widgets.push({id: tideWidget.id, type: tideWidget.type, index: tideWidget.index,
                                config: tideConfig(tideWidget)});
    }
    tideDump.panels.push(tideEntry);
}
tideResult(tideDump);

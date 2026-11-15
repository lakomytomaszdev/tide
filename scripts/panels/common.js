// Wspólne funkcje skryptów paneli Tide (API skryptowe plasmashell, wywoływane przez
// org.kde.PlasmaShell.evaluateScript). Parametry przychodzą w obiekcie P z panels.py.

// Numer ekranu Plasmy dla złącza (np. DP-2); gdy złącza nie ma — wartość zapasowa.
function tideScreen(connector, fallback) {
    var screen = connector ? screenForConnector(connector) : -1;
    return (typeof screen === "number" && screen >= 0 && screen < screenCount) ? screen : fallback;
}

// Panel ma widok tylko na istniejącym ekranie. Panel z zapamiętanym ekranem spoza screenCount
// (np. lastScreen=1 przy wyłączonym DP-2) jest „bez widoku”: jego właściwości widoku wolno czytać
// i zapisywać dopiero po sprawdzeniu tej funkcji — inaczej plasmashell się wywraca.
// Kopia pod tą samą nazwą jest w stream-read.js, który nie dołącza common.js.
function tidePanelHasView(panel) {
    return panel.screen >= 0 && panel.screen < screenCount;
}

// Identyfikatory paneli bez widoku (lista `orphans` w wyniku skryptów instalacji). Skrypty takich paneli
// nie czytają ani nie zapisują ich właściwości widoku i nie zakładają w zamian nowego panelu tej samej roli.
function tideOrphanIds() {
    var ids = [];
    var all = panels();
    for (var i = 0; i < all.length; ++i) {
        if (!tidePanelHasView(all[i])) {
            ids.push(all[i].id);
        }
    }
    return ids;
}

// Główny ekran i ekran boczny. Bez drugiego monitora side = -1.
function tideScreens() {
    var main = tideScreen(P.mainOutput, 0);
    var side = -1;
    if (screenCount > 1) {
        side = tideScreen(P.sideOutput, -1);
        if (side === main) {
            side = -1;
        }
        for (var s = 0; side < 0 && s < screenCount; ++s) {
            if (s !== main) {
                side = s;
            }
        }
    }
    return {count: screenCount, main: main, side: side};
}

// Pasek lewego monitora rozpoznajemy po znaczniku w konfiguracji panelu, nie po numerze.
function tideIsSideBar(panel) {
    panel.currentConfigGroup = ["Tide"];
    var role = String(panel.readConfig("role", ""));
    panel.currentConfigGroup = [];
    return role === "side-bar";
}

function tideMarkSideBar(panel) {
    panel.currentConfigGroup = ["Tide"];
    panel.writeConfig("role", "side-bar");
    panel.currentConfigGroup = [];
}

// Rola panelu Tide ze znacznika [Tide] role (np. "side-bar", "sidebar"); "" dla obcych paneli.
function tideRole(panel) {
    panel.currentConfigGroup = ["Tide"];
    var role = String(panel.readConfig("role", ""));
    panel.currentConfigGroup = [];
    return role;
}

function tideMarkRole(panel, role) {
    panel.currentConfigGroup = ["Tide"];
    panel.writeConfig("role", role);
    panel.currentConfigGroup = [];
}

// Widżety w kolejności na panelu (index pochodzi z AppletOrder; nieznane na końcu wg położenia).
function tideSorted(panel) {
    var widgets = panel.widgets();
    var list = [];
    for (var i = 0; i < widgets.length; ++i) {
        list.push(widgets[i]);
    }
    function key(w) {
        return w.index >= 0 ? w.index : 100000 + (w.geometry ? w.geometry.x : 0);
    }
    list.sort(function (a, b) { return key(a) - key(b); });
    return list;
}

// Zapis kilku kluczy do jednej grupy konfiguracji widżetu.
function tideWrite(widget, group, values) {
    widget.currentConfigGroup = group;
    for (var key in values) {
        widget.writeConfig(key, values[key]);
    }
    widget.currentConfigGroup = [];
}

function tideAdd(panel, type, group, values) {
    var widget = panel.addWidget(type);
    if (group) {
        tideWrite(widget, group, values);
    }
    return widget;
}

// Górny panel na ekranie (bez paska bocznego Tide); hint to identyfikator preferowany.
function tideTopPanel(screen, hint) {
    var found = null;
    var all = panels();
    for (var i = 0; i < all.length; ++i) {
        var panel = all[i];
        if (tidePanelHasView(panel) && panel.screen === screen && panel.location === "top" && !tideIsSideBar(panel)) {
            if (!found || panel.id === hint) {
                found = panel;
            }
        }
    }
    return found;
}

function tideResult(value) {
    print("TIDE_JSON:" + JSON.stringify(value));
}

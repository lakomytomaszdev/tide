// Dock Tide na głównym monitorze: dolny panel na pełną szerokość, aplikacje pośrodku
// (menedżer zadań między dwoma rozciągliwymi odstępami), kafelki widżetów Tide po obu stronach:
// z lewej system (P.left), z prawej sprawy AI (P.widgets).
// Pełna szerokość, bo okna zwykle są zmaksymalizowane, a wąski dock zostawia paski tapety.

var tideMainScreen = tideScreens().main;
var tideDock = null;
var tideAll = panels();
for (var tideI = 0; tideI < tideAll.length; ++tideI) {
    if (tideAll[tideI].screen === tideMainScreen && tideAll[tideI].location === "bottom") {
        tideDock = tideAll[tideI];
        break;
    }
}
var tideCreated = false;
if (!tideDock) {
    tideDock = new Panel("org.kde.panel");
    tideDock.screen = tideMainScreen;
    tideDock.location = "bottom";
    tideCreated = true;
}

// Stan sprzed zmian — tylko przy pierwszej instalacji, żeby odinstalowanie mogło go przywrócić.
var tidePrevious = {lengthMode: tideDock.lengthMode, alignment: tideDock.alignment,
                    height: tideDock.height, floating: tideDock.floating};

tideDock.lengthMode = "fill";
tideDock.floating = true;
tideDock.height = P.dockHeight;

// Stary widżet Limity AI z QuotaApp zastępuje org.tide.limits.
var tideWidgets = tideSorted(tideDock);
var tideByType = {};
var tideSpacers = [];
for (var tideJ = 0; tideJ < tideWidgets.length; ++tideJ) {
    var tideW = tideWidgets[tideJ];
    if (tideW.type === "org.limityai.widget" && P.widgets.indexOf("org.tide.limits") >= 0) {
        tideW.remove();
        continue;
    }
    // Osobny kafelek „＋ Zadanie” zastąpił przycisk „＋” na kafelku agentów.
    if (tideW.type === "org.tide.newtask" && P.widgets.indexOf("org.tide.newtask") < 0) {
        tideW.remove();
        continue;
    }
    // Kafelek Modele wszedł do kafelka „AI” (org.tide.limits, PT-41) — pakiet zostaje, ale z docka znika.
    if (tideW.type === "org.tide.models" && P.widgets.indexOf("org.tide.models") < 0) {
        tideW.remove();
        continue;
    }
    if (tideW.type === "org.kde.plasma.panelspacer") {
        tideSpacers.push(tideW);
        continue;
    }
    if (!tideByType[tideW.type]) {
        tideByType[tideW.type] = tideW;
    }
}

var tideTasks = tideByType["org.kde.plasma.icontasks"] || tideByType["org.kde.plasma.taskmanager"]
                || tideDock.addWidget("org.kde.plasma.icontasks");
// Bez „wypełniania wolnego miejsca” menedżer zadań ma szerokość ikon i daje się wyśrodkować.
tideWrite(tideTasks, ["General"], {fill: false});

while (tideSpacers.length < 2) {
    tideSpacers.push(tideAdd(tideDock, "org.kde.plasma.panelspacer", ["General"], {expanding: true}));
}
for (var tideK = 2; tideK < tideSpacers.length; ++tideK) {
    tideSpacers[tideK].remove();
}
tideWrite(tideSpacers[0], ["Tide"], {role: "dock-spacer"});
tideWrite(tideSpacers[1], ["Tide"], {role: "dock-spacer"});

function tideWidgetIds(types) {
    var ids = [];
    for (var i = 0; i < types.length; ++i) {
        ids.push((tideByType[types[i]] || tideDock.addWidget(types[i])).id);
    }
    return ids;
}
// Kafelki z lewej przed pierwszym odstępem, z prawej za drugim; aplikacje zostają pośrodku.
var tideOrder = tideWidgetIds(P.left || []).concat([tideSpacers[0].id, tideTasks.id, tideSpacers[1].id],
                                                   tideWidgetIds(P.widgets));
// Pozostałe widżety użytkownika zostają na końcu, w dotychczasowej kolejności.
var tideRest = tideSorted(tideDock);
for (var tideM = 0; tideM < tideRest.length; ++tideM) {
    if (tideOrder.indexOf(tideRest[tideM].id) < 0) {
        tideOrder.push(tideRest[tideM].id);
    }
}

tideResult({dock: tideDock.id, created: tideCreated, previous: tidePrevious, order: tideOrder,
            tasks: tideTasks.id});

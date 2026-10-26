// Dock Tide na głównym monitorze: dolny panel na pełną szerokość, aplikacje pośrodku
// (menedżer zadań między dwoma rozciągliwymi odstępami), kafelki widżetów Tide z prawej.
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

var tideOrder = [tideSpacers[0].id, tideTasks.id, tideSpacers[1].id];
for (var tideL = 0; tideL < P.widgets.length; ++tideL) {
    var tideType = P.widgets[tideL];
    var tideWidget = tideByType[tideType] || tideDock.addWidget(tideType);
    tideOrder.push(tideWidget.id);
}
// Pozostałe widżety użytkownika zostają na końcu, w dotychczasowej kolejności.
var tideRest = tideSorted(tideDock);
for (var tideM = 0; tideM < tideRest.length; ++tideM) {
    if (tideOrder.indexOf(tideRest[tideM].id) < 0) {
        tideOrder.push(tideRest[tideM].id);
    }
}

tideResult({dock: tideDock.id, created: tideCreated, previous: tidePrevious, order: tideOrder,
            tasks: tideTasks.id});

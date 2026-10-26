// Górne paski Tide.
// Główny monitor: istniejący górny panel jako pływająca pigułka z menu (logo Tide),
// globalnym menu, zegarem z datą pośrodku i zasobnikiem systemowym.
// Lewy monitor: smukły pasek z globalnym menu i zegarem z datą.
// Kolejność widżetów ustawia osobny skrypt (order.js), gdy Plasma doda już nowe widżety.

var tideScreensNow = tideScreens();
var tideOut = {screens: tideScreensNow, main: null, mainOrder: [], mainCreated: false,
               side: null, sideOrder: [], sideCreated: false};

// --- główny pasek -----------------------------------------------------------
var tideBar = tideTopPanel(tideScreensNow.main, P.mainHint);
if (!tideBar) {
    tideBar = new Panel("org.kde.panel");
    tideBar.screen = tideScreensNow.main;
    tideBar.location = "top";
    tideOut.mainCreated = true;
}
tideBar.floating = true;
tideBar.height = P.barHeight;

// Zostawiamy po jednym menu, menu globalnym, zegarze i zasobniku (z ich konfiguracją),
// resztę usuwamy — poprzedni układ jest w panels-previous.json i w kopii zapasowej.
// Wyjątek: widżety „Ikona” to skróty użytkownika, więc zostają wszystkie, przed zasobnikiem.
var tideKeep = {"org.kde.plasma.kickoff": null, "org.kde.plasma.appmenu": null,
                "org.kde.plasma.digitalclock": null, "org.kde.plasma.systemtray": null};
var tideShortcuts = [];
var tideCurrent = tideSorted(tideBar);
for (var tideI = 0; tideI < tideCurrent.length; ++tideI) {
    var tideW = tideCurrent[tideI];
    if (tideW.type === "org.kde.plasma.icon") {
        tideShortcuts.push(tideW);
    } else if (tideKeep.hasOwnProperty(tideW.type) && tideKeep[tideW.type] === null) {
        tideKeep[tideW.type] = tideW;
    } else {
        tideW.remove();
    }
}
function tideEnsure(type) {
    return tideKeep[type] || tideBar.addWidget(type);
}
var tideKickoff = tideEnsure("org.kde.plasma.kickoff");
var tideMenu = tideEnsure("org.kde.plasma.appmenu");
var tideClock = tideEnsure("org.kde.plasma.digitalclock");
var tideTray = tideEnsure("org.kde.plasma.systemtray");
var tideSpacerLeft = tideAdd(tideBar, "org.kde.plasma.panelspacer", ["General"], {expanding: true});
var tideSpacerRight = tideAdd(tideBar, "org.kde.plasma.panelspacer", ["General"], {expanding: true});

tideWrite(tideKickoff, ["General"], {icon: P.icon});
// Menu tylko dla okien z tego monitora — na lewym monitorze jest osobny pasek.
tideWrite(tideMenu, ["Appearance"], {compactView: false, allScreens: false});
tideWrite(tideClock, ["Appearance"], {showDate: true});

tideOut.main = tideBar.id;
tideOut.mainOrder = [tideKickoff.id, tideMenu.id, tideSpacerLeft.id, tideClock.id, tideSpacerRight.id];
for (var tideS = 0; tideS < tideShortcuts.length; ++tideS) {
    tideOut.mainOrder.push(tideShortcuts[tideS].id);
}
tideOut.mainOrder.push(tideTray.id);

// --- pasek lewego monitora ----------------------------------------------------
if (tideScreensNow.side >= 0) {
    var tideSide = null;
    if (P.sidePanel) {
        var tideKnown = panelById(P.sidePanel);
        if (tideKnown && tideIsSideBar(tideKnown)) {
            tideSide = tideKnown;
        }
    }
    var tideAllPanels = panels();
    for (var tideJ = 0; !tideSide && tideJ < tideAllPanels.length; ++tideJ) {
        if (tideIsSideBar(tideAllPanels[tideJ])) {
            tideSide = tideAllPanels[tideJ];
        }
    }
    if (!tideSide) {
        // Nowy panel Plasma stawia na aktywnym monitorze — przenosimy go na boczny.
        tideSide = new Panel("org.kde.panel");
        tideMarkSideBar(tideSide);
        tideOut.sideCreated = true;
    }
    if (tideSide.screen !== tideScreensNow.side) {
        tideSide.screen = tideScreensNow.side;
    }
    tideSide.location = "top";
    tideSide.floating = true;
    tideSide.height = P.slimHeight;

    var tideOld = tideSide.widgets();
    for (var tideK = 0; tideK < tideOld.length; ++tideK) {
        tideOld[tideK].remove();
    }
    var tideSideMenu = tideAdd(tideSide, "org.kde.plasma.appmenu", ["Appearance"],
                               {compactView: false, allScreens: false});
    var tideSideLeft = tideAdd(tideSide, "org.kde.plasma.panelspacer", ["General"], {expanding: true});
    var tideSideClock = tideAdd(tideSide, "org.kde.plasma.digitalclock", ["Appearance"], {showDate: true});
    var tideSideRight = tideAdd(tideSide, "org.kde.plasma.panelspacer", ["General"], {expanding: true});

    tideOut.side = tideSide.id;
    tideOut.sideOrder = [tideSideMenu.id, tideSideLeft.id, tideSideClock.id, tideSideRight.id];
}

tideResult(tideOut);

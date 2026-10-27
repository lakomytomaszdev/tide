// Pasek boczny Tide: pionowy panel przy lewej, zewnętrznej krawędzi lewego monitora
// (nie przy krawędzi między monitorami — tam mysz przechodzi na drugi ekran), chowany
// automatycznie i pływający. Od góry: nagłówek ze skrótem (org.tide.sidebar), agenci,
// system, Docker, worktrees i Limity AI — widżety w widoku szczegółowym (sidebarMode).
//
// Panel rozpoznajemy po znaczniku [Tide] role=sidebar. Ponowna instalacja używa tego samego
// panelu i tych samych widżetów (ten sam identyfikator nagłówka = ta sama akcja skrótu).
// Kolejność widżetów ustawia potem order.js, gdy Plasma doda już nowe widżety.
//
// Parametry: P.sidebarPanel (podpowiedź), P.width, P.widgets (typy w kolejności, nagłówek
// pierwszy), P.header (typ nagłówka), P.shortcut ("" = nie ustawiać), P.floatingGap (odstęp
// pływającego panelu od krawędzi ekranu w Plasmie), P.spacer (typ rozciąganego odstępu na końcu).

var tideScreensNow = tideScreens();
var tideOut = {screens: tideScreensNow, sidebar: null, created: false, header: null, order: [],
               shortcut: "", removed: []};

// Ile miejsca u góry ekranu zajmują stałe górne panele (smukły pasek): pasek boczny
// zaczyna się pod nimi, żeby nie zasłaniał globalnego menu, tak jak na mocku.
function tideTopReserved(screen, self) {
    var reserved = 0;
    var all = panels();
    for (var i = 0; i < all.length; ++i) {
        var panel = all[i];
        if (panel.id !== self && panel.screen === screen && panel.location === "top" && panel.hiding === "none") {
            reserved = Math.max(reserved, panel.height + (panel.floating ? P.floatingGap : 0));
        }
    }
    return reserved;
}

function tideFindSidebar() {
    if (P.sidebarPanel) {
        var known = panelById(P.sidebarPanel);
        if (known && tideRole(known) === "sidebar") {
            return known;
        }
    }
    var all = panels();
    for (var i = 0; i < all.length; ++i) {
        if (tideRole(all[i]) === "sidebar") {
            return all[i];
        }
    }
    return null;
}

if (tideScreensNow.side >= 0) {
    var tideSidebar = tideFindSidebar();
    if (!tideSidebar) {
        // Nowy panel Plasma stawia na aktywnym monitorze — przenosimy go na lewy.
        tideSidebar = new Panel("org.kde.panel");
        tideMarkRole(tideSidebar, "sidebar");
        tideOut.created = true;
    }
    if (tideSidebar.screen !== tideScreensNow.side) {
        tideSidebar.screen = tideScreensNow.side;
    }
    tideSidebar.location = "left";
    // Widoczny, gdy na lewym monitorze nic go nie zasłania; chowa się, gdy najedzie okno.
    tideSidebar.hiding = "dodgewindows";
    tideSidebar.floating = true;
    // Długość „custom” od dołu górnego paska do dołu ekranu („fill” zawsze bierze cały ekran
    // i zasłaniałby smukły pasek). Najpierw maksimum, bo minimum większe od starego maksimum
    // Plasma przycina. Dla panelu pionowego „left” to wyrównanie do góry.
    var tideTop = tideTopReserved(tideScreensNow.side, tideSidebar.id);
    var tideLength = Math.round(screenGeometry(tideScreensNow.side).height) - tideTop;
    tideSidebar.lengthMode = "custom";
    tideSidebar.alignment = "left";
    tideSidebar.offset = tideTop;
    tideSidebar.maximumLength = tideLength;
    tideSidebar.minimumLength = tideLength;
    // Dla panelu pionowego „height” to grubość, czyli szerokość paska.
    tideSidebar.height = P.width;

    // Zostają widżety z listy (po jednym z typu, z ich konfiguracją) i jeden odstęp, reszta
    // znika — to nasz panel.
    var tideByType = {};
    var tideSpacer = null;
    var tideCurrent = tideSorted(tideSidebar);
    for (var tideI = 0; tideI < tideCurrent.length; ++tideI) {
        var tideW = tideCurrent[tideI];
        if (P.widgets.indexOf(tideW.type) >= 0 && !tideByType[tideW.type]) {
            tideByType[tideW.type] = tideW;
        } else if (tideW.type === P.spacer && !tideSpacer) {
            tideSpacer = tideW;
        } else {
            tideOut.removed.push(tideW.type);
            tideW.remove();
        }
    }
    for (var tideJ = 0; tideJ < P.widgets.length; ++tideJ) {
        var tideType = P.widgets[tideJ];
        var tideWidget = tideByType[tideType] || tideSidebar.addWidget(tideType);
        if (tideType !== P.header) {
            // [Configuration][General] to grupa, z której KConfigXT widżetu czyta przy starcie;
            // Plasma zapisuje też od razu do schematu, więc widok zmienia się bez restartu.
            tideWrite(tideWidget, ["Configuration", "General"], {sidebarMode: true});
        }
        tideOut.order.push(tideWidget.id);
        if (tideType === P.header) {
            tideOut.header = tideWidget.id;
            if (P.shortcut && String(tideWidget.globalShortcut) !== P.shortcut) {
                tideWidget.globalShortcut = P.shortcut;
            }
            // Odczyt z KGlobalAccel — pusty, gdy klawisz zajęła inna akcja.
            tideOut.shortcut = String(tideWidget.globalShortcut || "");
        }
    }
    // Rozciągany odstęp na końcu bierze wolne miejsce, więc kafelki leżą jeden pod drugim od
    // góry. Bez niego panel rozkłada nadmiar wysokości między aplety i między kafelkami
    // powstają nierówne dziury.
    if (!tideSpacer) {
        tideSpacer = tideAdd(tideSidebar, P.spacer, ["General"], {expanding: true});
    }
    tideWrite(tideSpacer, ["Tide"], {role: "sidebar-spacer"});
    tideOut.order.push(tideSpacer.id);
    tideOut.sidebar = tideSidebar.id;
}

tideResult(tideOut);

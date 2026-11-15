// Odinstalowanie docka Tide: usuwa widżety Tide i dodane odstępy, przywraca szerokość,
// wysokość i pływanie panelu oraz wypełnianie menedżera zadań. Panel utworzony przez Tide znika.

var tideDock = P.dock ? panelById(P.dock) : null;
// Dock bez widoku (tidePanelHasView) trafia do `orphans`: widżety Tide znikają, ale szerokość, wysokość
// i pływanie panelu nie są czytane ani przywracane.
var tideReport = {dock: P.dock, removed: [], skipped: false, orphans: tideOrphanIds()};
if (tideDock) {
    if (P.created) {
        tideDock.remove();
    } else {
        var tideWidgets = tideDock.widgets();
        for (var tideI = 0; tideI < tideWidgets.length; ++tideI) {
            var tideW = tideWidgets[tideI];
            tideW.currentConfigGroup = ["Tide"];
            var tideRole = String(tideW.readConfig("role", ""));
            tideW.currentConfigGroup = [];
            if (P.widgets.indexOf(tideW.type) >= 0 || tideRole === "dock-spacer") {
                tideReport.removed.push(tideW.type);
                tideW.remove();
            } else if (tideW.type === "org.kde.plasma.icontasks" || tideW.type === "org.kde.plasma.taskmanager") {
                tideWrite(tideW, ["General"], {fill: true});
            }
        }
        if (P.previous && !tidePanelHasView(tideDock)) {
            // Instalator zachowa dock-installed.json (jedyna kopia `previous`) do ponowienia z widokiem.
            tideReport.skipped = true;
        } else if (P.previous) {
            tideDock.lengthMode = P.previous.lengthMode;
            tideDock.alignment = P.previous.alignment;
            tideDock.height = P.previous.height;
            tideDock.floating = P.previous.floating;
        }
    }
}
tideResult(tideReport);

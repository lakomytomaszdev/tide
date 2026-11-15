// Ustawia kolejność widżetów na panelach: P.panels = [{id, order: [id widżetu, ...]}].
// Zapis AppletOrder trafia do konfiguracji panelu, a ustawienie indeksu pierwszego widżetu
// wywołuje reorderApplets() panelu z tą samą listą, więc zmiana jest widoczna od razu.

// Panel bez widoku (tidePanelHasView) jest pomijany w całości i trafia do `orphans`: nie ustawiamy mu
// grubości ani kolejności.
var tideReport = [];
var tideOrphans = tideOrphanIds();
for (var tideI = 0; tideI < P.panels.length; ++tideI) {
    var tideItem = P.panels[tideI];
    var tidePanel = panelById(tideItem.id);
    if (!tidePanel || !tideItem.order.length || !tidePanelHasView(tidePanel)) {
        continue;
    }
    if (tideItem.height) {
        tidePanel.height = tideItem.height;
    }
    tidePanel.currentConfigGroup = ["General"];
    tidePanel.writeConfig("AppletOrder", tideItem.order.join(";"));
    tidePanel.currentConfigGroup = [];
    var tideFirst = tidePanel.widgetById(tideItem.order[0]);
    if (tideFirst) {
        tideFirst.index = 0;
    }
    var tideTypes = [];
    var tideSortedNow = tideSorted(tidePanel);
    for (var tideJ = 0; tideJ < tideSortedNow.length; ++tideJ) {
        tideTypes.push(tideSortedNow[tideJ].type);
    }
    tideReport.push({id: tideItem.id, order: tideTypes});
}
tideResult({panels: tideReport, orphans: tideOrphans});

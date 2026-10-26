// Odinstalowanie paska bocznego Tide: usuwa panele ze znacznikiem [Tide] role=sidebar.
// Najpierw zdejmuje globalny skrót nagłówka, żeby klawisz (Meta+S) nie został zajęty
// przez akcję usuniętego widżetu. P.header to typ nagłówka.

var tideOut = {removed: [], shortcutsCleared: []};
var tideAll = panels();
for (var tideI = 0; tideI < tideAll.length; ++tideI) {
    var tidePanel = tideAll[tideI];
    if (tideRole(tidePanel) !== "sidebar") {
        continue;
    }
    var tideWidgets = tidePanel.widgets();
    for (var tideJ = 0; tideJ < tideWidgets.length; ++tideJ) {
        if (tideWidgets[tideJ].type === P.header && String(tideWidgets[tideJ].globalShortcut || "") !== "") {
            tideWidgets[tideJ].globalShortcut = "";
            tideOut.shortcutsCleared.push(tideWidgets[tideJ].id);
        }
    }
    tideOut.removed.push(tidePanel.id);
    tidePanel.remove();
}
tideResult(tideOut);

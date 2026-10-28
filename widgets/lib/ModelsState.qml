// Stan modeli lokalnych bez wyglądu i bez PlasmoidItem (da się go załadować w teście z atrapą źródła) — dla
// kafelka Modele i kafelka „AI” (biblioteka „tide”, PT-41): migawka Status(), czy usługa odpowiada, „teraz”
// do czasów w podpowiedzi i popupie, „Odśwież”.
// nowMs idzie do przodu przy każdym odczycie (refreshed źródła) i przy najechaniu (touch()), więc
// podpowiedź kafelka mówi „zwolni pamięć za 4 min” od chwili odczytu, a nie od ostatniego otwarcia popupu.
import QtQuick
import "models.js" as Logic

Item {
    id: models

    // DaemonSource (/Models Status) albo atrapa z result, available, refreshed(), call() i refresh().
    property var source: null
    property var clock: function () { return Date.now() }
    // Refresh() odpowiada od razu, a odczyt idzie w tle usługi (do 3 s na serwer): potem czytamy stan sami,
    // bo Changed przychodzi tylko przy prawdziwej zmianie.
    property int refreshDelayMs: 3500

    readonly property var snapshot: source && source.result && typeof source.result === "object"
                                    && !Array.isArray(source.result) ? source.result : null
    readonly property bool serviceUp: !!source && source.available === true
    property real nowMs: clock()
    property bool refreshing: false
    readonly property string toolTip: Logic.toolTip(snapshot, serviceUp, nowMs)

    function touch() {
        nowMs = clock()
    }

    function refresh() {
        if (refreshing || !source) return
        refreshing = true
        source.call("Refresh", [], function () { refreshDone.restart() })
    }

    Connections {
        target: models.source
        ignoreUnknownSignals: true
        function onRefreshed() { models.touch() }
    }

    Timer {
        id: refreshDone
        interval: models.refreshDelayMs
        onTriggered: {
            models.source.refresh()
            models.refreshing = false
        }
    }
}

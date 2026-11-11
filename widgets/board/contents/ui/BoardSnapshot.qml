// Migawka Tablicy dla widżetu (PT-385): odpowiedź Snapshot() z `readState: "preparing"` (usługa po restarcie
// jeszcze nie opublikowała Tablicy, `cards: []`) nie zastępuje poprzedniej migawki pustą Tablicą. Bez poprzedniej
// — `loading` (kafelek pokazuje „wczytywanie”). Odczyt jest ponawiany po sygnale Changed (DaemonSource) albo
// najpóźniej po `retryMs`, zamiast czekać na kolejne odpytanie co 60 s.
// Bez PlasmoidItem, więc da się go załadować w teście (widgets/tests/test_widget_board.py).
import QtQuick

QtObject {
    id: board

    property var daemon: null            // DaemonSource: result, refresh(), sygnał refreshed()
    property int retryMs: 2000
    property var snapshot: null          // ostatnia gotowa migawka (readState inne niż "preparing")
    property bool preparing: false       // ostatnia odpowiedź: usługa się przygotowuje
    readonly property bool loading: preparing && !snapshot

    function take(value) {
        if (!value || typeof value !== "object") return
        if (value.readState === "preparing") {
            preparing = true
            retry.restart()
            return
        }
        preparing = false
        retry.stop()
        snapshot = value
    }

    property Timer retry: Timer {
        interval: board.retryMs
        onTriggered: if (board.daemon) board.daemon.refresh()
    }

    property Connections watch: Connections {
        target: board.daemon
        function onRefreshed() { board.take(board.daemon.result) }
    }

    Component.onCompleted: if (daemon) take(daemon.result)
}

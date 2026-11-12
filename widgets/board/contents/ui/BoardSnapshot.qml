// Migawka Tablicy dla widżetu (PT-385): odpowiedź Snapshot() z `readState: "preparing"` (usługa po restarcie
// jeszcze nie opublikowała Tablicy, `cards: []`) nie zastępuje poprzedniej migawki pustą Tablicą. Bez poprzedniej
// — `loading` (kafelek pokazuje „wczytywanie”) do najbliższej gotowej odpowiedzi. Usługa wysyła /Board Changed przy
// pierwszej publikacji, a DaemonSource odczytuje wtedy Snapshot() ponownie — bez dodatkowego zegara.
// Bez PlasmoidItem, więc da się go załadować w teście (widgets/tests/test_widget_board.py).
import QtQuick

QtObject {
    id: board

    property var daemon: null            // DaemonSource: result i sygnał refreshed()
    property var snapshot: null          // ostatnia gotowa migawka (readState inne niż "preparing")
    property bool preparing: false       // ostatnia odpowiedź: usługa się przygotowuje
    readonly property bool loading: preparing && !snapshot

    function take(value) {
        if (!value || typeof value !== "object") return
        if (value.readState === "preparing") {
            preparing = true
            return
        }
        preparing = false
        snapshot = value
    }

    property Connections watch: Connections {
        target: board.daemon
        function onRefreshed() { board.take(board.daemon.result) }
    }

    Component.onCompleted: if (daemon) take(daemon.result)
}

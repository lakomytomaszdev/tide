// Widżet Modele: kafelek w docku po prawej, zaraz za Limitami AI („Modele”, „● qwen3:8b · na karcie 100%”;
// CompactModels), popup z serwerami, modelami w pamięci, kolejką, rolami i trybem raportu (ModelsPopup).
// Dane: tide-daemon przez D-Bus (org.tide.Daemon /Models, docs/tide-daemon.md#modele-lokalne): Status()
// po sygnale Changed i co minutę na wszelki wypadek; „Odśwież” woła Refresh() — wynik przychodzi sygnałem
// Changed, a gdy nic się nie zmieniło, po chwili czytamy Status() sami.
// „Otwórz w Tide” otwiera okno Tide na widoku Modele przez org.tide.Window Show("models", "") (TideWindow.qml).
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "../code/models.js" as Logic
import "tide" as Tide

PlasmoidItem {
    id: root

    readonly property var snapshot: source.result && typeof source.result === "object" && !Array.isArray(source.result)
                                    ? source.result : null
    readonly property bool serviceUp: source.available
    property real nowMs: Date.now()
    property bool refreshing: false
    property string windowNote: ""

    function refresh() {
        if (refreshing) return
        refreshing = true
        source.call("Refresh", [], function () { refreshDone.restart() })
    }

    Plasmoid.title: "Modele"
    Plasmoid.icon: "cpu"
    toolTipMainText: "Modele lokalne"
    toolTipSubText: Logic.toolTip(snapshot, serviceUp, nowMs)
    preferredRepresentation: compactRepresentation

    onExpandedChanged: if (expanded) nowMs = Date.now()

    Tide.DaemonSource {
        id: source
        path: "/Models"
        iface: "org.tide.Daemon.Models"
        method: "Status"
        pollInterval: 60000
    }

    // Refresh() odpowiada od razu, a odczyt idzie w tle usługi (do 3 s na serwer): potem czytamy stan sami,
    // bo Changed przychodzi tylko przy prawdziwej zmianie.
    Timer {
        id: refreshDone
        interval: 3500
        onTriggered: {
            source.refresh()
            root.refreshing = false
        }
    }

    // „zwolni pamięć za 4 min” i „od 2 min” w kolejce liczone na bieżąco przy otwartym popupie.
    Timer {
        interval: 30000
        repeat: true
        running: root.expanded
        onTriggered: root.nowMs = Date.now()
    }

    Timer {
        id: noteTimer
        interval: 30000
        onTriggered: root.windowNote = ""
    }

    TideWindow {
        id: tideWindow
        onOpened: {
            root.windowNote = ""
            root.expanded = false
        }
        onFailed: kind => {
            root.windowNote = Logic.windowNote(kind)
            noteTimer.restart()
        }
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? -1 : tile.implicitWidth + tile.dockGap
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? dot.implicitHeight + Kirigami.Units.smallSpacing * 4 : -1
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded
        Accessible.role: Accessible.Button
        Accessible.name: tile.Accessible.name

        CompactModels {
            id: tile
            visible: !compact.vertical
            anchors.centerIn: parent
            height: Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            snapshot: root.snapshot
            serviceUp: root.serviceUp
            active: compact.containsMouse || root.expanded
        }

        // Pionowy panel: sama kropka stanu.
        Tide.StateDot {
            id: dot
            visible: compact.vertical
            anchors.centerIn: parent
            size: 10
            tint: tile.dotColor
            sessionState: tile.info.pulse ? "working" : ""
        }
    }

    fullRepresentation: Item {
        Layout.preferredWidth: Kirigami.Units.gridUnit * 22
        Layout.minimumWidth: Kirigami.Units.gridUnit * 18
        Layout.preferredHeight: popup.implicitHeight + Kirigami.Units.largeSpacing * 2

        ModelsPopup {
            id: popup
            anchors.fill: parent
            anchors.margins: Kirigami.Units.largeSpacing
            snapshot: root.snapshot
            serviceUp: root.serviceUp
            nowMs: root.nowMs
            refreshing: root.refreshing
            windowNote: root.windowNote
            onRefreshRequested: root.refresh()
            onOpenRequested: tideWindow.open()
        }
    }
}

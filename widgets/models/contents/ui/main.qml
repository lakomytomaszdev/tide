// Widżet Modele: kafelek w docku po prawej, zaraz za Limitami AI („Modele”, „● qwen3:8b · na karcie 100%”;
// CompactModels), popup z serwerami, modelami w pamięci, kolejką, rolami i trybem raportu (ModelsPopup).
// Dane: tide-daemon przez D-Bus (org.tide.Daemon /Models, docs/tide-daemon.md#modele-lokalne): Status()
// po sygnale Changed i co minutę na wszelki wypadek; „Odśwież” woła Refresh() — wynik przychodzi sygnałem
// Changed, a gdy nic się nie zmieniło, po chwili czytamy Status() sami (ModelsState.qml, tam też „teraz”
// do czasów w podpowiedzi — przesuwane przy każdym odczycie i najechaniu).
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

    readonly property var snapshot: models.snapshot
    readonly property bool serviceUp: models.serviceUp
    property string windowNote: ""

    Plasmoid.title: "Modele"
    Plasmoid.icon: "cpu"
    toolTipMainText: "Modele lokalne"
    toolTipSubText: models.toolTip
    preferredRepresentation: compactRepresentation

    onExpandedChanged: if (expanded) models.touch()

    Tide.DaemonSource {
        id: daemon
        path: "/Models"
        iface: "org.tide.Daemon.Models"
        method: "Status"
        pollInterval: 60000
    }

    ModelsState {
        id: models
        source: daemon
    }

    // „zwolni pamięć za 4 min” i „od 2 min” w kolejce liczone na bieżąco przy otwartym popupie.
    Timer {
        interval: 30000
        repeat: true
        running: root.expanded
        onTriggered: models.touch()
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
        onContainsMouseChanged: if (containsMouse) models.touch()
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
            nowMs: models.nowMs
            refreshing: models.refreshing
            windowNote: root.windowNote
            onRefreshRequested: models.refresh()
            onOpenRequested: tideWindow.open()
        }
    }
}

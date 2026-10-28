// Widżet „Monitory” (org.tide.stream.monitors, PT-38): kafelek w docku Stream z bieżącym wejściem prawego monitora
// i popup z potwierdzeniem. Przełączenie robi tide-daemon (/Monitors SetLayout: wejście DP-2 przez DDC/CI, układ
// w KWin bez zmian, decyzja 81) — monitory to stan pulpitu Tide, nie streamu, więc źródłem jest usługa Tide,
// a nie rdzeń Streamer (decyzja 15; wyjątek z decyzji 79 dotyczy tylko stanu streamu).
// Bez skrótu klawiszowego (plan PT-38).
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import "tide" as Tide
import "../code/monitors.js" as Monitors

PlasmoidItem {
    id: root

    readonly property var status: source.result
    readonly property bool up: source.available
    readonly property var model: Monitors.tile(status, up)
    property string refusal: ""

    Plasmoid.title: "Monitory"
    Plasmoid.icon: "video-display"
    toolTipMainText: "Monitory PC ⇄ Xbox"
    toolTipSubText: model.toolTip
    onExpandedChanged: if (expanded) refusal = ""

    Tide.DaemonSource {
        id: source
        path: "/Monitors"
        iface: "org.tide.Daemon.Monitors"
        method: "Status"
        pollInterval: 30000
    }

    function switchTo(layout) {
        source.call("SetLayout", [layout], function (reply) {
            let answer = null
            try { answer = JSON.parse(reply) } catch (e) { answer = null }
            if (answer && answer.accepted) {
                root.expanded = false
                source.refresh()
            } else {
                root.refusal = answer && answer.error ? answer.error : "tide-daemon nie odpowiedział"
            }
        })
    }

    compactRepresentation: MouseArea {
        id: compact
        Layout.minimumWidth: tile.implicitWidth + tile.dockGap
        Layout.preferredWidth: Layout.minimumWidth
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded
        Accessible.role: Accessible.Button
        Accessible.name: "Monitory: " + (root.model.from ? root.model.from + " " + root.model.to : root.model.busy)

        CompactMonitors {
            id: tile
            anchors.centerIn: parent
            height: Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            status: root.status
            up: root.up
            active: compact.containsMouse || root.expanded
        }
    }

    fullRepresentation: Item {
        Layout.preferredWidth: Kirigami.Units.gridUnit * 19
        Layout.minimumWidth: Kirigami.Units.gridUnit * 15
        Layout.preferredHeight: column.implicitHeight + Kirigami.Units.smallSpacing * 2
        Layout.minimumHeight: Layout.preferredHeight

        ColumnLayout {
            id: column
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.smallSpacing

            MonitorsPopup {
                Layout.fillWidth: true
                status: root.status
                up: root.up
                onConfirmed: layout => root.switchTo(layout)
                onCancelled: root.expanded = false
            }

            Text {
                Layout.fillWidth: true
                visible: root.refusal !== ""
                text: root.refusal
                textFormat: Text.PlainText
                wrapMode: Text.Wrap
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt - 1
            }
        }
    }
}

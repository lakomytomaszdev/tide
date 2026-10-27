// Karta „Zasoby” w prawej kolumnie: CPU (udział w całym procesorze — hostCpuPercent) i RAM działających
// kontenerów z paskami (teal, lawenda), pod spodem największe zużycie pamięci i miejsce na dysku.
// Dane ze Stats() (tylko gdy widok jest na wierzchu) i Disk().
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "docker.js" as Logic

Rectangle {
    id: card

    property var stats: null
    property var disk: null
    property var snapshot: null

    readonly property bool ready: !!(stats && stats.ok && stats.total)
    readonly property real cpu: ready ? (stats.total.hostCpuPercent || 0) : 0
    readonly property real mem: ready ? (stats.total.memBytes || 0) : 0
    readonly property real hostMem: Logic.hostMemory(stats)
    readonly property string note: {
        const parts = []
        const top = ready ? Logic.topMemory(stats, snapshot, 2) : ""
        if (top) parts.push("Najwięcej: " + top + ".")
        const disk = Logic.diskLine(card.disk)
        if (disk) parts.push(disk + ".")
        if (!ready && stats && stats.error) parts.unshift("Statystyki: " + stats.error + ".")
        return parts.join(" ")
    }

    implicitHeight: body.implicitHeight + 28
    radius: Tide.Tokens.radiusCard
    color: Tide.Tokens.backgroundCard
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)

    component Meter: RowLayout {
        id: meter
        property string label: ""
        property real fraction: 0
        property color tint: Tide.Tokens.accent
        property string value: ""
        Layout.fillWidth: true
        spacing: 10
        Text {
            Layout.preferredWidth: 40
            text: meter.label
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
        }
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 5
            radius: 3
            color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.10)
            Rectangle {
                width: card.ready ? Math.max(4, parent.width * Math.min(1, Math.max(0, meter.fraction))) : 0
                height: parent.height
                radius: 3
                color: meter.tint
            }
        }
        Text {
            Layout.preferredWidth: 62
            horizontalAlignment: Text.AlignRight
            text: meter.value
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.monoFamily
            font.pixelSize: 12
        }
    }

    ColumnLayout {
        id: body
        anchors.fill: parent
        anchors.margins: 14
        spacing: 10

        Text {
            text: "Zasoby"
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            font.weight: Font.DemiBold
            Accessible.role: Accessible.Heading
        }
        Meter {
            objectName: "cpuMeter"
            label: "CPU"
            fraction: card.cpu / 100
            tint: Tide.Tokens.teal
            value: card.ready ? Logic.percent(card.cpu) : "—"
        }
        Meter {
            objectName: "ramMeter"
            label: "RAM"
            fraction: card.hostMem > 0 ? card.mem / card.hostMem : 0
            tint: Tide.Tokens.lavender
            value: card.ready ? Logic.bytes(card.mem) : "—"
        }
        Text {
            objectName: "resourcesNote"
            Layout.fillWidth: true
            visible: text !== ""
            text: card.note
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            lineHeight: 1.15
            wrapMode: Text.WordWrap
        }
    }
}

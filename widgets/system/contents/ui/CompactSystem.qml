// Kafelek w docku: trzy pierścienie — procesor (teal), pamięć (lawenda) i pełniejsza z partycji
// (brzoskwinia) — z procentem w środku i małym podpisem pod spodem. Bez danych pierścień
// pokazuje sam tor i „—”.
import QtQuick
import "../code/system.js" as Logic
import "tide" as Tide

Grid {
    id: compact

    required property var source            // SystemSource
    property bool vertical: false
    property real ringSize: 36
    property color textColor: Tide.Tokens.textPrimary
    property real labelPointSize: Math.max(6, Tide.Tokens.fontSizePt - 3)

    readonly property var meters: [
        { label: "CPU", percent: source.cpuPercent, color: Tide.Tokens.teal },
        { label: "RAM", percent: source.memoryPercent, color: Tide.Tokens.lavender },
        { label: "Dysk", percent: source.fullerDisk ? source.fullerDisk.usedPercent : null, color: Tide.Tokens.peach }
    ]

    columns: vertical ? 1 : meters.length
    columnSpacing: Math.round(ringSize * 0.3)
    rowSpacing: Math.round(ringSize * 0.2)

    Repeater {
        model: compact.meters

        delegate: Column {
            required property var modelData
            spacing: 1

            Tide.Ring {
                anchors.horizontalCenter: parent.horizontalCenter
                width: compact.ringSize
                height: compact.ringSize
                thickness: Math.max(3, compact.ringSize * 0.11)
                value: Logic.fraction(modelData.percent)
                color: modelData.color

                Text {
                    // Sam numer bez „%” — w 36 px mieści się tylko tyle.
                    text: modelData.percent === null ? "—" : Math.round(modelData.percent)
                    color: compact.textColor
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: Math.round(compact.ringSize * 0.3)
                    font.weight: Font.DemiBold
                }
            }
            Text {
                anchors.horizontalCenter: parent.horizontalCenter
                text: modelData.label
                color: compact.textColor
                opacity: 0.7
                font.family: Tide.Theme.uiFamily
                font.pointSize: compact.labelPointSize
            }
        }
    }
}

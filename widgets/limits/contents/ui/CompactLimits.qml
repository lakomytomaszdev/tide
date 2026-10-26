// Zawartość kafelka: dwa wiersze „GPT” i „Claude”, w każdym cienki miernik i procent.
// Wartość to niższy z limitów (sesja, tydzień); kolor miernika według poziomu, brak danych = „—”.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

GridLayout {
    id: grid

    required property var source            // LimitsSource
    property color textColor: Tide.Tokens.textPrimary
    property real meterWidth: 40
    property real meterHeight: 4
    property real pointSize: Tide.Tokens.fontSizePt - 1

    readonly property var services: [
        { key: "chatgpt", label: "GPT" },
        { key: "claude", label: "Claude" }
    ]

    function percentText(value) {
        return value === null ? "—" : value + "%"
    }

    columns: 3
    rowSpacing: 2
    columnSpacing: 6

    Repeater {
        model: grid.services
        delegate: Text {
            required property var modelData
            required property int index
            Layout.row: index
            Layout.column: 0
            text: modelData.label
            color: grid.textColor
            opacity: 0.75
            font.family: Tide.Theme.uiFamily
            font.pointSize: grid.pointSize
        }
    }
    Repeater {
        model: grid.services
        delegate: Tide.MeterBar {
            required property var modelData
            required property int index
            readonly property var percent: grid.source.panelValue(modelData.key)
            Layout.row: index
            Layout.column: 1
            Layout.preferredWidth: grid.meterWidth
            Layout.preferredHeight: grid.meterHeight
            Layout.alignment: Qt.AlignVCenter
            value: percent === null ? null : percent / 100
            color: Tide.Theme.levelColor(percent)
        }
    }
    Repeater {
        model: grid.services
        delegate: Text {
            required property var modelData
            required property int index
            readonly property var percent: grid.source.panelValue(modelData.key)
            Layout.row: index
            Layout.column: 2
            Layout.minimumWidth: widest.width
            horizontalAlignment: Text.AlignRight
            text: grid.percentText(percent)
            color: percent === null ? Tide.Tokens.textMuted : grid.textColor
            font.family: Tide.Theme.monoFamily
            font.pointSize: grid.pointSize
            font.weight: Font.DemiBold
        }
    }

    // Stała szerokość kolumny procentów, żeby kafelek nie skakał przy zmianie wartości.
    TextMetrics {
        id: widest
        text: "100%"
        font.family: Tide.Theme.monoFamily
        font.pointSize: grid.pointSize
        font.weight: Font.DemiBold
    }
}

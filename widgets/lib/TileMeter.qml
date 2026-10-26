// Jeden miernik w kafelku docka: „CPU 34%” — podpis w text.secondary, procent w Geist Mono —
// a pod spodem cienki pasek w kolorze miernika. percent od 0 do 100, null = brak danych („—”).
// Szerokość co najmniej na „88%”, żeby kafelek nie skakał przy 9% → 10%.
import QtQuick

Column {
    id: meter

    property string label: ""
    property var percent: null
    property color color: Tokens.accent
    property real labelPointSize: Tokens.fontSizePt - 1
    property real valuePointSize: Tokens.fontSizePt
    property color textColor: Tokens.textPrimary
    readonly property bool known: typeof percent === "number" && isFinite(percent)
    readonly property string valueText: known ? Math.round(percent) + "%" : "—"
    readonly property string text: label + " " + valueText

    spacing: 2

    Row {
        id: line
        spacing: 4

        Text {
            id: name
            text: meter.label
            color: Tokens.textSecondary
            font.family: Theme.uiFamily
            font.pointSize: meter.labelPointSize
            font.weight: Font.Medium
        }
        Text {
            anchors.baseline: name.baseline
            width: Math.max(implicitWidth, twoDigits.advanceWidth)
            text: meter.valueText
            color: meter.known ? meter.textColor : Tokens.textMuted
            font.family: Theme.monoFamily
            font.pointSize: meter.valuePointSize
            font.weight: Font.DemiBold
        }
    }
    MeterBar {
        width: line.width
        height: 3
        value: meter.known ? Math.max(0, Math.min(1, meter.percent / 100)) : null
        color: meter.color
    }

    TextMetrics {
        id: twoDigits
        text: "88%"
        font.family: Theme.monoFamily
        font.pointSize: meter.valuePointSize
        font.weight: Font.DemiBold
    }
}

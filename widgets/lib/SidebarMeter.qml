// Wiersz miernika w kafelku paska bocznego: podpis | cienki pasek | wartość, opcjonalnie
// wyciszony dopisek pod paskiem („sesja · reset 18:30”). Podpisy mają wspólną szerokość
// (labelWidth), więc paski kolejnych wierszy zaczynają się w jednym miejscu, jak w TileMeter.
import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: meter

    property string label: ""
    property var value: null              // 0–1 albo null (brak danych)
    property string valueText: ""
    property color color: Tokens.accent
    property color valueColor: Tokens.textPrimary
    property bool monoValue: true         // procent w Geist Mono, „9,5 GB wolne” zwykłym krojem
    property string note: ""
    property real labelWidth: 44
    property real valueWidth: 40
    readonly property real barX: labelWidth + row.spacing

    spacing: 3

    RowLayout {
        id: row
        Layout.fillWidth: true
        spacing: 10

        Text {
            Layout.preferredWidth: meter.labelWidth
            text: meter.label
            color: Tokens.textSecondary
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt - 1
            font.weight: Font.Medium
            elide: Text.ElideRight
        }
        MeterBar {
            Layout.fillWidth: true
            Layout.preferredHeight: 4
            Layout.alignment: Qt.AlignVCenter
            value: meter.value
            color: meter.color
        }
        Text {
            Layout.preferredWidth: Math.max(meter.valueWidth, implicitWidth)
            horizontalAlignment: Text.AlignRight
            text: meter.valueText
            color: meter.value === null ? Tokens.textMuted : meter.valueColor
            font.family: meter.monoValue ? Theme.monoFamily : Theme.uiFamily
            font.pointSize: Tokens.fontSizePt - (meter.monoValue ? 0 : 1)
            font.weight: meter.monoValue ? Font.DemiBold : Font.Normal
        }
    }
    Text {
        Layout.fillWidth: true
        Layout.leftMargin: meter.barX
        visible: meter.note !== ""
        text: meter.note
        color: Tokens.textMuted
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt - 2
        elide: Text.ElideRight
    }
}

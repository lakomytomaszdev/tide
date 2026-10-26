// Jeden limit w karcie usługi: etykieta i procent, miernik, tekst resetu.
// W gęstym układzie (pasek boczny) reset stoi obok miernika — jedna linia mniej.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

ColumnLayout {
    id: row

    property string label: ""
    property var value: null              // pozostały procent albo null
    property var reset: null              // np. „poniedziałek 20:00”
    property bool dense: false
    readonly property string resetText: reset ? "reset " + reset : "reset: brak danych"

    spacing: dense ? 2 : 4

    RowLayout {
        Layout.fillWidth: true
        Text {
            Layout.fillWidth: true
            text: row.label
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            elide: Text.ElideRight
        }
        Text {
            text: row.value === null ? "brak danych" : row.value + "%"
            color: Tide.Theme.levelColor(row.value)
            font.family: row.value === null ? Tide.Theme.uiFamily : Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: row.value === null ? Font.Normal : Font.DemiBold
        }
    }
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        Tide.MeterBar {
            Layout.fillWidth: true
            value: row.value === null ? null : row.value / 100
            color: Tide.Theme.levelColor(row.value)
        }
        Text {
            visible: row.dense
            Layout.maximumWidth: row.width * 0.6
            text: row.reset ? row.reset : "—"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            elide: Text.ElideRight
        }
    }
    Text {
        Layout.fillWidth: true
        visible: !row.dense
        text: row.resetText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }
}

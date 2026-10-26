// Jeden limit w karcie usługi: etykieta i procent, miernik, tekst resetu.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

ColumnLayout {
    id: row

    property string label: ""
    property var value: null              // pozostały procent albo null
    property var reset: null              // np. „poniedziałek 20:00”

    spacing: 4

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
    Tide.MeterBar {
        Layout.fillWidth: true
        value: row.value === null ? null : row.value / 100
        color: Tide.Theme.levelColor(row.value)
    }
    Text {
        Layout.fillWidth: true
        text: row.reset ? "reset " + row.reset : "reset: brak danych"
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }
}

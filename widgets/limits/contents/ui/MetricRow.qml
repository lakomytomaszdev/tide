// Jeden limit w karcie usługi: etykieta i procent, miernik, tekst resetu, a po prawej
// prognoza z tide-daemon („wyczerpie się ok. 17:40” w brzoskwini, „starczy do resetu” wyciszone).
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

ColumnLayout {
    id: row

    property string label: ""
    property var value: null              // pozostały procent albo null
    property var reset: null              // np. „poniedziałek 20:00”
    property var forecast: null           // prognoza z LimitsSource.forecastFor() albo null
    readonly property string forecastText: Tide.Texts.forecastText(forecast)

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
    RowLayout {
        Layout.fillWidth: true
        spacing: 8

        Text {
            Layout.fillWidth: true
            text: row.reset ? "reset " + row.reset : "reset: brak danych"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
            elide: Text.ElideRight
        }
        Text {
            visible: row.forecastText !== ""
            text: row.forecastText
            color: Tide.Theme.forecastColor(row.forecast)
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }
}

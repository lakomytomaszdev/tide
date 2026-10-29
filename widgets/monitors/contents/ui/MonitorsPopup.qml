// Potwierdzenie przełączenia wejścia prawego monitora (PT-38): co się stanie, przycisk przełączenia i „Anuluj”.
// Zmiana jest odczuwalna (prawy ekran pokazuje Xboxa, a KWin dalej uważa go za pulpit), więc kafelek nigdy
// nie przełącza od razu. Uwaga po automatycznym powrocie i błąd („przełącz wejście przyciskiem na monitorze”) —
// zwykłym textSecondary, bez koloru ostrzegawczego (ten jest zarezerwowany dla streamu, decyzja 79).
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "../code/monitors.js" as Monitors

Tide.Card {
    id: card

    property var status: null
    property bool up: false
    readonly property var model: Monitors.confirm(status, up)

    signal confirmed(string layout)
    signal cancelled()

    padding: 16
    spacing: 8

    Text {
        Layout.fillWidth: true
        text: card.model.title
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt + 3
        font.weight: Font.DemiBold
    }

    Repeater {
        model: card.model.lines
        Text {
            required property string modelData
            Layout.fillWidth: true
            text: modelData
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
        }
    }

    Repeater {
        model: card.model.note !== "" ? [card.model.note] : []
        Text {
            required property string modelData
            Layout.fillWidth: true
            text: modelData
            textFormat: Text.PlainText
            wrapMode: Text.Wrap
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }

    Text {
        Layout.fillWidth: true
        visible: card.model.error !== ""
        text: card.model.error
        textFormat: Text.PlainText
        wrapMode: Text.Wrap
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.topMargin: 6
        spacing: 8
        visible: card.model.button !== ""
        Item { Layout.fillWidth: true }
        Tide.TextButton {
            id: cancel
            variant: "outline"
            text: "Anuluj"
            pointSize: Tide.Tokens.fontSizePt
            onClicked: card.cancelled()
        }
        Tide.TextButton {
            id: go
            variant: "filled"
            text: card.model.button
            enabled: card.model.enabled
            opacity: enabled ? 1 : 0.55
            pointSize: Tide.Tokens.fontSizePt
            onClicked: if (card.model.enabled) card.confirmed(card.model.target)
        }
    }
}

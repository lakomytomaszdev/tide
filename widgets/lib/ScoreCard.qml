// Karta „Wynik serii” lewego monitora Stream (PT-91, decyzja 29.10): w prawej kolumnie, kiedy sytuacja z rdzenia
// ma kartę „score” (tryb Observer albo wynik widoczny dla widzów). +1, cofnij, zamiana stron i ukrycie wyniku;
// zerowanie i nowa seria zostają w Panelu streamera (przytrzymanie, formularz).
import QtQuick
import QtQuick.Layouts

Card {
    id: card

    property var view: null
    property bool busy: false
    signal commandRequested(string name, var args)

    readonly property var observer: view && view.core && view.core.observer ? view.core.observer : ({})
    readonly property var series: observer.series || null
    readonly property bool shown: !!(view && view.situation && view.situation.score_shown)

    padding: 16
    spacing: 10

    RowLayout {
        Layout.fillWidth: true
        Text {
            text: "Wynik serii"
            textFormat: Text.PlainText
            color: Tokens.textPrimary
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt + 3
            font.weight: Font.DemiBold
        }
        Item { Layout.fillWidth: true }
        Text {
            text: card.series ? "BO" + card.series.format + (card.series.finished ? " · koniec" : " · gra " + card.series.game) : ""
            textFormat: Text.PlainText
            color: Tokens.textMuted
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt - 1
        }
    }

    Text {
        visible: !card.series
        Layout.fillWidth: true
        wrapMode: Text.Wrap
        text: "Brak serii. Nową serię zakładasz w Panelu streamera."
        textFormat: Text.PlainText
        color: Tokens.textSecondary
        font.family: Theme.uiFamily
        font.pointSize: Tokens.fontSizePt
    }

    GridLayout {
        visible: !!card.series
        Layout.fillWidth: true
        columns: 2
        columnSpacing: 8
        rowSpacing: 8
        Repeater {
            model: card.series ? [0, 1] : []
            ColumnLayout {
                required property int modelData
                Layout.fillWidth: true
                spacing: 6
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: modelData === 0 ? card.series.left : card.series.right
                    textFormat: Text.PlainText
                    color: Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt + 1
                    font.weight: Font.DemiBold
                }
                Text {
                    Layout.alignment: Qt.AlignHCenter
                    text: String(card.series.score[modelData])
                    textFormat: Text.PlainText
                    color: Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt * 2.4
                    font.weight: Font.Bold
                }
                TextButton {
                    objectName: modelData === 0 ? "scoreLeft" : "scoreRight"
                    Layout.fillWidth: true
                    variant: "outline"
                    topPadding: 12
                    bottomPadding: 12
                    pointSize: Tokens.fontSizePt + 1
                    enabled: !card.busy && !card.series.finished
                    text: "+1"
                    textColor: Tokens.textPrimary
                    onClicked: card.commandRequested("observer.point", { side: modelData === 0 ? "left" : "right" })
                }
            }
        }
    }

    RowLayout {
        visible: !!card.series
        Layout.fillWidth: true
        spacing: 8
        Repeater {
            model: [
                { text: "Cofnij", cmd: "observer.undo", args: {} },
                { text: "Zamień strony", cmd: "observer.swap", args: {} },
                { text: card.shown ? "Ukryj wynik" : "Pokaż wynik", cmd: "observer.overlay.set", args: { visible: !card.shown } }
            ]
            TextButton {
                required property var modelData
                Layout.fillWidth: true
                variant: "outline"
                topPadding: 8
                bottomPadding: 8
                pointSize: Tokens.fontSizePt - 1
                enabled: !card.busy
                text: modelData.text
                textColor: Tokens.textPrimary
                onClicked: card.commandRequested(modelData.cmd, modelData.args)
            }
        }
    }
}

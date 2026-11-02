// Ramka karty „Teraz” (PT-105, decyzja 5 K-29): tytuł, znacznik stanu i akcje karty. Stany z rdzenia:
// auto (pokazał ją rdzeń według sytuacji, przerywany znacznik), przypięta (złota krawędź, zostaje po zmianie
// etapu) i schowana (× chowa ją do startu następnego streamu; wraca z nagłówka „Teraz”). Pinezka przypina albo
// odpina, × chowa. Rdzeń bez przypinania (item.state === "") daje samą kartę bez znacznika i przycisków.
// Treść karty to dzieci (ColumnLayout), komendy wysyła kolumna „Teraz” (NowColumn.qml).
import QtQuick
import QtQuick.Layouts
import QtQuick.Shapes

Card {
    id: card

    // {id, title, state: auto|pinned|"", pinnable, hideable} z Stream.now().
    property var item: ({ id: "", title: "", state: "", pinnable: false, hideable: false })
    property string chip: ""
    property string linkText: ""     // odesłanie w nagłówku (np. „Panel streamera”)
    property bool busy: false
    signal pinRequested()
    signal hideRequested()
    signal linkClicked()

    readonly property bool pinned: item.state === "pinned"

    objectName: "now-" + item.id
    padding: 10
    spacing: 6
    border.color: pinned ? Theme.alpha(Tokens.accent, 0.45) : Theme.alpha(Tokens.textPrimary, 0.06)

    // Złota krawędź przypiętej karty (przeniesiona z układu treści na samą kartę).
    Rectangle {
        visible: card.pinned
        parent: card
        x: 1
        y: card.radius
        width: 3
        height: card.height - 2 * card.radius
        color: Tokens.accent
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 6
        Text {
            text: card.item.title
            textFormat: Text.PlainText
            color: Tokens.textPrimary
            font.family: Theme.uiFamily
            font.pointSize: Tokens.fontSizePt + 2
            font.weight: Font.DemiBold
            elide: Text.ElideRight
            Layout.maximumWidth: card.width * 0.5
        }
        Item {
            id: tagBox
            objectName: "now-tag-" + card.item.id
            visible: card.item.state !== ""
            readonly property string text: card.pinned ? "przypięta" : "auto"
            implicitWidth: tag.implicitWidth + 14
            implicitHeight: tag.implicitHeight + 4
            Shape {
                anchors.fill: parent
                preferredRendererType: Shape.CurveRenderer
                ShapePath {
                    strokeColor: card.pinned ? Theme.alpha(Tokens.accent, 0.70) : Theme.alpha(Tokens.textPrimary, 0.30)
                    strokeWidth: 1
                    strokeStyle: card.pinned ? ShapePath.SolidLine : ShapePath.DashLine
                    dashPattern: [3, 2]
                    fillColor: "transparent"
                    PathRectangle {
                        x: 0.5; y: 0.5
                        width: tagBox.width - 1
                        height: tagBox.height - 1
                        radius: (tagBox.height - 1) / 2
                    }
                }
            }
            Text {
                id: tag
                anchors.centerIn: parent
                text: parent.text
                textFormat: Text.PlainText
                color: card.pinned ? Tokens.accent : Tokens.textMuted
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 2
            }
        }
        Rectangle {
            visible: card.chip !== ""
            radius: height / 2
            color: "transparent"
            border.width: 1
            border.color: Theme.alpha(Tokens.textPrimary, 0.20)
            implicitWidth: chipText.implicitWidth + 14
            implicitHeight: chipText.implicitHeight + 4
            Text {
                id: chipText
                anchors.centerIn: parent
                text: card.chip
                textFormat: Text.PlainText
                color: Tokens.textSecondary
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 2
            }
        }
        Item { Layout.fillWidth: true }
        TextButton {
            visible: card.linkText !== ""
            variant: "link"
            text: card.linkText
            onClicked: card.linkClicked()
        }
        IconButton {
            objectName: "now-pin-" + card.item.id
            visible: card.item.pinnable
            enabled: !card.busy
            iconName: "pin"
            on: card.pinned
            tip: card.pinned ? "Odepnij kartę „" + card.item.title + "”" : "Przypnij kartę „" + card.item.title + "”"
            onClicked: card.pinRequested()
        }
        IconButton {
            objectName: "now-hide-" + card.item.id
            visible: card.item.hideable
            enabled: !card.busy
            iconName: "x"
            tip: "Schowaj kartę „" + card.item.title + "” do końca streamu"
            onClicked: card.hideRequested()
        }
    }
}

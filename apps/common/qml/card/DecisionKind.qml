// Plakietka rodzaju decyzji („Pytanie”, „Prośba o zgodę”, „Błąd Tide”…) nad decyzją w oknie decyzji i w bloku
// „Twój ruch” pełnej karty. Podpis z decision.js (`kindLabel`); pierwszym dzieckiem jest tekst plakietki.
import QtQuick
import "../tide" as Tide
import "decision.js" as Decision

Rectangle {
    id: chip

    property var decision: null

    implicitWidth: kindText.implicitWidth + 16
    implicitHeight: kindText.implicitHeight + 8
    radius: Tide.Tokens.radiusSmall
    color: Tide.Theme.alpha(Tide.Tokens.waiting, 0.14)
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.waiting, 0.4)
    Text {
        id: kindText
        anchors.centerIn: parent
        text: Decision.kindLabel(chip.decision)
        textFormat: Text.PlainText
        color: Tide.Tokens.waiting
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 0.9
        font.weight: Font.DemiBold
    }
}

// Podpowiedź Tide z tekstem zawsze jako zwykły tekst (PlainText) — także gdy pochodzi z usługi albo z pliku
// (nazwa modelu, powód niedostępności serwera); kolory z palety, bez stylu Plasmy.
import QtQuick
import QtQuick.Controls as QQC2

QQC2.ToolTip {
    id: tip
    delay: 300
    padding: 8
    contentItem: Text {
        text: tip.text
        textFormat: Text.PlainText
        color: Tokens.textPrimary
        font.family: Theme.uiFamily
        font.pixelSize: 12
        wrapMode: Text.Wrap
    }
    background: Rectangle {
        radius: Tokens.radiusSmall
        color: Tokens.backgroundCard
        border.width: 1
        border.color: Theme.alpha(Tokens.textPrimary, 0.12)
    }
}

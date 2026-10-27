// Podpis pola formularza (12 px, text.muted).
import QtQuick
import "tide" as Tide

Text {
    textFormat: Text.PlainText
    color: Tide.Tokens.textMuted
    font.family: Tide.Theme.uiFamily
    font.pixelSize: 12
    font.weight: Font.Medium
}

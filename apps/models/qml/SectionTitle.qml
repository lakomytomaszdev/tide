// Nagłówek sekcji widoku: 11 px wersalikami w text.muted (jak „RAPORTY” i „NA DAILY”).
import QtQuick
import "tide" as Tide

Text {
    textFormat: Text.PlainText
    color: Tide.Tokens.textMuted
    font.family: Tide.Theme.uiFamily
    font.pixelSize: 11
    font.weight: Font.DemiBold
    font.letterSpacing: 0.6
    font.capitalization: Font.AllUppercase
    Accessible.role: Accessible.Heading
}

// Wynik akcji obok kontrolki: błąd z usługi w różu (nigdy po cichu), powodzenie w mięcie, uwaga w maśle,
// zwykła informacja wyciszona. Tekst zawsze jako zwykły tekst (komunikaty usługi).
import QtQuick
import "tide" as Tide

Text {
    property string tone: "muted"   // error, ok, warn, muted

    visible: text !== ""
    wrapMode: Text.WordWrap
    textFormat: Text.PlainText
    color: tone === "error" ? Tide.Tokens.negative
         : tone === "ok" ? Tide.Tokens.positive
         : tone === "warn" ? Tide.Tokens.neutral : Tide.Tokens.textMuted
    font.family: Tide.Theme.uiFamily
    font.pixelSize: 12
}

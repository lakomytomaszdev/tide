// Kropka stanu modelu lokalnego w kafelku „AI” (ton z tide/models.js, indicator() i tile()): mięta — działa,
// teal — pracuje albo pobiera, masło — część modelu na procesorze albo brak modelu roli, róż — serwer nie
// odpowiada. Kolory jak w kafelku Modele (decyzja 68). Bez pulsowania: kafelek AI ma być spokojny, gdy
// wszystko działa — pracę widać w kolorze, a szczegóły w popupie.
import QtQuick
import "tide" as Tide

Tide.StateDot {
    property string tone: "muted"
    readonly property color toneColor: tone === "ok" ? Tide.Tokens.positive
        : tone === "working" ? Tide.Tokens.working
        : tone === "partial" || tone === "warning" ? Tide.Tokens.neutral
        : tone === "offline" ? Tide.Tokens.negative
        : Tide.Tokens.textMuted

    size: 7
    tint: toneColor
}

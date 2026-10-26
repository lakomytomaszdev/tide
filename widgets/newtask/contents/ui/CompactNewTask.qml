// Kafelek „Nowe zadanie” w docku, w tym samym układzie co pozostałe: mała etykieta „＋ Zadanie”
// (plus w tealu), pod nią „nowe dla agenta”. Teal jak kafelek agentów — to wejście do ich pracy,
// stoją obok siebie. Bez PlasmoidItem, więc da się go załadować w teście i w podglądzie.
import QtQuick
import "tide" as Tide

Tide.DockTile {
    id: tile

    property bool available: true
    readonly property string summary: available ? "nowe dla agenta" : "usługa nie działa"

    // Etykieta rysowana tutaj (z plusem), w stylu etykiety DockTile.
    label: "Zadanie"
    showLabel: false
    tint: available ? Tide.Tokens.accent : Tide.Tokens.textMuted

    Row {
        spacing: 4
        Text {
            text: "＋"
            color: tile.available ? Tide.Tokens.accent : Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.labelPointSize
            font.weight: Font.DemiBold
        }
        Text {
            text: tile.label
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.labelPointSize
            font.weight: Font.Medium
        }
    }
    Text {
        text: tile.summary
        color: tile.available ? Tide.Tokens.textSecondary : Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: tile.valuePointSize
    }
}

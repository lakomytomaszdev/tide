// Kafelek worktrees w docku: „Worktrees · 5 w pracy”, pod spodem „15 do sprzątnięcia · 16 GB”.
// Liczba do sprzątnięcia w mięcie (Geist Mono), rozmiar wyciszony; gdy nic nie zostało —
// wyciszone „nic do sprzątnięcia”. Kafelek w lawendzie, jak na mocku Mist.
// Bez PlasmoidItem, więc da się go załadować w teście (widgets/tests/test_dock_tiles.py).
import QtQuick
import "tide" as Tide

Tide.DockTile {
    id: tile

    property int cleanCount: 0
    property real cleanBytes: 0
    property int activeCount: 0
    property bool available: true

    readonly property string summary: !available ? "usługa nie działa"
        : cleanCount > 0 ? Tide.Texts.cleanable(cleanCount) + " · " + Tide.Theme.formatBytes(cleanBytes)
        : Tide.Texts.cleanable(0)

    label: "Worktrees"
    detail: available && activeCount > 0 ? activeCount + " w pracy" : ""
    tint: available ? Tide.Tokens.lavender : Tide.Tokens.textMuted

    Row {
        spacing: 4

        Text {
            id: count
            visible: tile.available && tile.cleanCount > 0
            text: tile.cleanCount
            color: Tide.Tokens.positive
            font.family: Tide.Theme.monoFamily
            font.pointSize: tile.valuePointSize
            font.weight: Font.DemiBold
        }
        Text {
            visible: count.visible
            anchors.baseline: count.baseline
            text: "do sprzątnięcia"
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
        Text {
            visible: count.visible
            anchors.baseline: count.baseline
            text: "·"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
        Text {
            visible: count.visible
            anchors.baseline: count.baseline
            text: Tide.Theme.formatBytes(tile.cleanBytes)
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.monoFamily
            font.pointSize: tile.valuePointSize - 1
        }
        Text {
            visible: !count.visible
            text: tile.summary
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
    }
}

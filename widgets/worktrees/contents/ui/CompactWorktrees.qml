// Kafelek worktrees w docku, w tym samym układzie co kafelek agentów: „Worktrees”, pod spodem
// „● 2 w pracy · ● 15 do sprzątnięcia”. Kropki w kolorach stanów (teal — w pracy, mięta — do
// sprzątnięcia), liczby w Geist Mono, słowa w text.secondary; pokazujemy tylko niezerowe części.
// Rozmiar do odzyskania jest w podpowiedzi. Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import "tide" as Tide

Tide.DockTile {
    id: tile

    property int cleanCount: 0
    property real cleanBytes: 0
    property int activeCount: 0
    property bool available: true

    readonly property bool quiet: !available || (activeCount === 0 && cleanCount === 0)
    readonly property string summary: available ? Tide.Texts.worktreesSummary(activeCount, cleanCount)
                                                : "usługa nie działa"

    label: "Worktrees"
    tint: available ? Tide.Tokens.lavender : Tide.Tokens.textMuted

    Row {
        spacing: 4

        // W pracy: agent albo zmiany z ostatniej doby.
        Tide.StateDot {
            visible: activeNumber.visible
            anchors.verticalCenter: parent.verticalCenter
            sessionState: "working"
            size: 7
        }
        Text {
            id: activeNumber
            visible: !tile.quiet && tile.activeCount > 0
            text: tile.activeCount
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.monoFamily
            font.pointSize: tile.valuePointSize
            font.weight: Font.DemiBold
        }
        Text {
            visible: activeNumber.visible
            anchors.baseline: activeNumber.baseline
            text: "w pracy"
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
        Text {
            visible: activeNumber.visible && cleanNumber.visible
            anchors.baseline: activeNumber.baseline
            text: "·"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }

        // Do sprzątnięcia: mięta, bo to bezpieczne do usunięcia.
        Rectangle {
            visible: cleanNumber.visible
            anchors.verticalCenter: parent.verticalCenter
            width: 7
            height: 7
            radius: 3.5
            color: Tide.Tokens.positive
        }
        Text {
            id: cleanNumber
            visible: !tile.quiet && tile.cleanCount > 0
            text: tile.cleanCount
            color: Tide.Tokens.positive
            font.family: Tide.Theme.monoFamily
            font.pointSize: tile.valuePointSize
            font.weight: Font.DemiBold
        }
        Text {
            visible: cleanNumber.visible
            anchors.baseline: cleanNumber.baseline
            text: "do sprzątnięcia"
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }

        // Nic w pracy i nic do sprzątnięcia albo usługa nie działa.
        Text {
            visible: tile.quiet
            text: tile.summary
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
    }
}

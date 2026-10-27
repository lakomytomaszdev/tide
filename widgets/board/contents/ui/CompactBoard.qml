// Kafelek tablicy w docku (mock zatwierdzony 27.10.2026): mięta, „Tablica · 11 kart”, pod spodem
// „● 2 w toku · ● 1 czeka · ● 1 do sprawdzenia” — tylko niezerowe części, kropki w kolorach stanów
// (teal, brzoskwinia, lawenda), liczby w Geist Mono, słowa w text.secondary. Bez pracy agentów —
// „○ 3 do zrobienia”, bez kart — wyciszone „pusto”.
// Bez PlasmoidItem, więc da się go załadować w teście (widgets/tests/test_widget_board.py).
import QtQuick
import "tide" as Tide
import "boardtexts.js" as Texts

Tide.DockTile {
    id: tile

    property var counts: Texts.counts(null)
    property bool available: true

    readonly property var parts: available ? Texts.tileParts(counts) : []
    readonly property string summary: available ? Texts.tileSummary(counts) : "usługa nie działa"

    label: "Tablica"
    detail: available ? Texts.tileDetail(counts) : ""
    detailColor: Tide.Tokens.textSecondary
    tint: available ? Tide.Tokens.mint : Tide.Tokens.textMuted

    Row {
        spacing: 4

        Repeater {
            model: tile.parts
            Row {
                id: part
                required property var modelData
                required property int index
                spacing: 4
                Text {
                    visible: part.index > 0
                    anchors.baseline: number.baseline
                    text: "·"
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: tile.valuePointSize
                }
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 7
                    height: 7
                    radius: 3.5
                    color: part.modelData.hollow ? "transparent" : Tide.Tokens[part.modelData.tint]
                    border.width: part.modelData.hollow ? 1.5 : 0
                    border.color: Tide.Tokens[part.modelData.tint]
                }
                Text {
                    id: number
                    text: part.modelData.count
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: tile.valuePointSize
                    font.weight: Font.DemiBold
                }
                Text {
                    anchors.baseline: number.baseline
                    text: part.modelData.word
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: tile.valuePointSize
                }
            }
        }

        // Pusto albo usługa nie działa.
        Text {
            visible: tile.parts.length === 0
            text: tile.summary
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
    }
}

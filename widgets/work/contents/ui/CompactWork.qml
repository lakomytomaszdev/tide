// Kafelek Praca w docku (PT-40, zastępuje Tablicę i Agentów): „Praca · 10”, pod spodem
// „● 3 w toku · ● 2 czeka · ● 2 do spr.” — tylko niezerowe części, kropki w kolorach stanów (teal,
// brzoskwinia, lawenda), liczby w Geist Mono, słowa w text.secondary. Bez pracy w toku — „○ 3 do
// zrobienia”, bez niczego — wyciszone „nic w toku”. Kafelek jest w tealu, a gdy coś czeka na ciebie —
// w brzoskwini (jak dawny kafelek Agentów). „＋” po prawej otwiera okno „Nowe zadanie”.
// Bez PlasmoidItem, więc da się go załadować w teście (widgets/tests/test_widget_work.py).
import QtQuick
import "tide" as Tide
import "worktexts.js" as Texts

Tide.DockTile {
    id: tile

    property var counts: Texts.counts(null)
    property bool available: true
    property bool showNewTask: true
    signal newTaskRequested()

    readonly property var parts: available ? Texts.tileParts(counts) : []
    readonly property string summary: available ? Texts.tileSummary(counts) : "usługa nie działa"

    label: "Praca"
    detail: available ? Texts.tileDetail(counts) : ""
    detailColor: Tide.Tokens.textSecondary
    tint: !available ? Tide.Tokens.textMuted : counts.waiting > 0 ? Tide.Tokens.waiting : Tide.Tokens.working
    trailingWidth: showNewTask ? newTask.width + 6 : 0

    Rectangle {
        id: newTask
        objectName: "newTaskButton"
        parent: tile
        visible: tile.showNewTask
        width: 26
        height: 26
        radius: Tide.Tokens.radiusSmall
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        color: Tide.Theme.alpha(Tide.Tokens.working, newTaskArea.containsMouse ? 0.28 : 0.14)
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.working, newTaskArea.containsMouse ? 0.55 : 0.30)
        z: 2
        Accessible.role: Accessible.Button
        Accessible.name: "Nowe zadanie dla agenta"
        Accessible.onPressAction: tile.newTaskRequested()

        Text {
            anchors.centerIn: parent
            text: "+"
            color: Tide.Tokens.working
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 2
            font.weight: Font.DemiBold
        }
        MouseArea {
            id: newTaskArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: tile.newTaskRequested()
        }
    }

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
                    // Czekające na ciebie: brzoskwiniowa liczba, bo to one wymagają reakcji.
                    color: part.modelData.tint === "waiting" ? Tide.Tokens.waiting : Tide.Tokens.textPrimary
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

        // Nic w toku albo usługa nie działa.
        Text {
            visible: tile.parts.length === 0
            text: tile.summary
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
    }
}

// Kafelek agentów w docku: „Agenci”, pod spodem „● 2 pracują · ● 1 czeka · ○ 2 gotowe” — tylko
// niezerowe części. Kropki w kolorach stanów (teal, brzoskwinia, pusty błękit dla skończonych),
// liczby w Geist Mono, słowa w text.secondary. Bez żywych sesji — wyciszone „brak sesji”.
// Kafelek jest w tealu, a gdy ktoś naprawdę czeka na ciebie — w brzoskwini. Skończone sesje
// niczego nie blokują, więc nie zmieniają koloru kafelka.
// Bez PlasmoidItem, więc da się go załadować w teście (widgets/tests/test_dock_tiles.py).
import QtQuick
import "tide" as Tide

Tide.DockTile {
    id: tile

    property int workingCount: 0
    property int waitingCount: 0
    property int doneCount: 0
    property bool available: true
    // „＋” po prawej otwiera okno „Nowe zadanie” (osobny kafelek byłby zbędny).
    property bool showNewTask: true
    signal newTaskRequested()

    readonly property string summary: available ? Tide.Texts.agentsSummary(workingCount, waitingCount, doneCount)
                                                : "usługa nie działa"
    readonly property bool quiet: !available || (workingCount === 0 && waitingCount === 0 && doneCount === 0)

    label: "Agenci"
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
    tint: !available ? Tide.Tokens.textMuted : waitingCount > 0 ? Tide.Tokens.waiting : Tide.Tokens.working

    Row {
        spacing: 4

        // Pracujące: kropka, liczba, słowo.
        Tide.StateDot {
            visible: workingNumber.visible
            anchors.verticalCenter: parent.verticalCenter
            sessionState: "working"
            size: 7
        }
        Text {
            id: workingNumber
            visible: !tile.quiet && tile.workingCount > 0
            text: tile.workingCount
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.monoFamily
            font.pointSize: tile.valuePointSize
            font.weight: Font.DemiBold
        }
        Text {
            visible: workingNumber.visible
            anchors.baseline: workingNumber.baseline
            text: Tide.Texts.workingWord(tile.workingCount)
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
        Text {
            visible: workingNumber.visible && (waitingNumber.visible || doneNumber.visible)
            anchors.baseline: workingNumber.baseline
            text: "·"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }

        // Czekające na ciebie: brzoskwiniowa liczba, bo to one wymagają reakcji.
        Tide.StateDot {
            visible: waitingNumber.visible
            anchors.verticalCenter: parent.verticalCenter
            sessionState: "waiting"
            size: 7
        }
        Text {
            id: waitingNumber
            visible: !tile.quiet && tile.waitingCount > 0
            text: tile.waitingCount
            color: Tide.Tokens.waiting
            font.family: Tide.Theme.monoFamily
            font.pointSize: tile.valuePointSize
            font.weight: Font.DemiBold
        }
        Text {
            visible: waitingNumber.visible
            anchors.baseline: waitingNumber.baseline
            text: Tide.Texts.waitingWord(tile.waitingCount)
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
        Text {
            visible: waitingNumber.visible && doneNumber.visible
            anchors.baseline: waitingNumber.baseline
            text: "·"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }

        // Skończone: puste kółko i spokojna liczba — informacja, nie wezwanie.
        Tide.StateDot {
            visible: doneNumber.visible
            anchors.verticalCenter: parent.verticalCenter
            sessionState: "done"
            size: 7
        }
        Text {
            id: doneNumber
            visible: !tile.quiet && tile.doneCount > 0
            text: tile.doneCount
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.monoFamily
            font.pointSize: tile.valuePointSize
            font.weight: Font.DemiBold
        }
        Text {
            visible: doneNumber.visible
            anchors.baseline: doneNumber.baseline
            text: Tide.Texts.doneWord(tile.doneCount)
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }

        // Brak żywych sesji albo usługa nie działa.
        Text {
            visible: tile.quiet
            text: tile.summary
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: tile.valuePointSize
        }
    }
}

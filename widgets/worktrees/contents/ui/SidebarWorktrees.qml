// Kafelek worktrees w pasku bocznym: „● 2 w pracy · ● 15 do sprzątnięcia (16 GB)” — tym samym
// językiem co kafelek w docku (teal — w pracy, mięta — do sprzątnięcia), ale zawsze z obiema
// liczbami, także zerami. W nagłówku mały przycisk „Otwórz okno” (okno „Tide Worktrees” z listą
// i sprzątaniem). Opis problemu pojawia się dopiero, gdy okna nie udało się otworzyć albo usługa
// nie działa. Bez PlasmoidItem, więc da się go załadować w teście.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Tide.SidebarTile {
    id: tile

    property bool available: true
    property string problem: ""          // "not-installed", "no-service" albo ""
    property int cleanCount: 0
    property real cleanBytes: 0
    property int activeCount: 0
    signal openRequested()

    readonly property bool showProblem: !available || problem !== ""
    readonly property string problemText: !available || problem === "no-service"
        ? "Usługa tide-daemon nie odpowiada" : "Okno Worktrees nie jest zainstalowane"
    // To samo słowami (dostępność i testy).
    readonly property string summary: activeCount + " w pracy · " + cleanCount + " do sprzątnięcia"
        + (cleanCount > 0 ? " (" + Tide.Theme.formatBytes(cleanBytes) + ")" : "")

    title: "Worktrees"
    spacing: 8
    Accessible.name: "Worktrees: " + (available ? summary : problemText)

    headerItems: Tide.TextButton {
        text: tile.showProblem ? "Spróbuj ponownie" : "Otwórz okno"
        Accessible.name: tile.showProblem ? "Spróbuj ponownie otworzyć okno Worktrees" : "Otwórz okno Worktrees"
        onClicked: tile.openRequested()
    }

    component Count: Row {
        id: count
        property int value: 0
        property string word: ""
        property color dot: Tide.Tokens.working
        spacing: 6

        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            width: 7
            height: 7
            radius: 3.5
            color: count.value > 0 ? count.dot : Tide.Theme.alpha(count.dot, 0.35)
        }
        Text {
            id: number
            text: count.value
            color: count.value > 0 ? Tide.Tokens.textPrimary : Tide.Tokens.textMuted
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: Font.DemiBold
        }
        Text {
            anchors.baseline: number.baseline
            text: count.word
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }

    Row {
        visible: tile.available
        spacing: 8

        Count {
            id: active
            value: tile.activeCount
            word: "w pracy"
            dot: Tide.Tokens.working
        }
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: "·"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
        Count {
            value: tile.cleanCount
            word: "do sprzątnięcia"
            dot: Tide.Tokens.positive
        }
        Text {
            visible: tile.cleanCount > 0
            anchors.verticalCenter: parent.verticalCenter
            text: "(" + Tide.Theme.formatBytes(tile.cleanBytes) + ")"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 1
        }
    }

    Text {
        Layout.fillWidth: true
        visible: tile.showProblem
        text: tile.problemText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }
}

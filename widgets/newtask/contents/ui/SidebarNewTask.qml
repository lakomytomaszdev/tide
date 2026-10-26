// Kafelek „Nowe zadanie” w pasku bocznym (sidebarMode): jedna linia podpowiedzi i link
// „+ Nowe zadanie” w nagłówku. Domyślny pasek Tide go nie zawiera — kafelek agentów ma ten sam
// link — ale widżet dodany do paska ręcznie wygląda jak reszta. Bez PlasmoidItem (testy, podgląd).
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Tide.SidebarTile {
    id: tile

    property bool available: true
    property string problem: ""          // "not-installed", "no-service" albo ""
    property string shortcut: "Meta+Alt+A"
    signal openRequested()

    readonly property string hint: !available || problem === "no-service" ? "Usługa tide-daemon nie odpowiada"
        : problem === "not-installed" ? "Okno nie jest zainstalowane (make install-new-task)"
        : "Repozytorium, Claude Code albo Codex, polecenie i zrzuty" + (shortcut ? " · " + shortcut : "")

    title: "Nowe zadanie"
    Accessible.name: "Nowe zadanie dla agenta: " + hint

    headerItems: Tide.TextButton {
        text: "＋ Nowe zadanie"
        Accessible.name: "Otwórz okno nowego zadania dla agenta"
        onClicked: tile.openRequested()
    }

    Text {
        Layout.fillWidth: true
        text: tile.hint
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }
}

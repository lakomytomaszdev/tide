// Kafelek agentów w pasku bocznym: jedna spokojna linia na żywą sesję (SessionRows — te same
// wiersze co w popupie kafelka w docku): kropka stanu, projekt, krótkie słowo stanu i czas.
// Tylko czekające dostają mały przycisk „Przejdź” („Otwórz w Codex” dla Codex Desktop);
// skończone i bezczynne są przygaszone.
// Najwyżej maxRows wierszy (czekające pierwsze), resztę otwiera „+N więcej” — pełna lista
// w okienku obok paska. Zakończone sesje są ukryte, dopóki nie kliknie się „Pokaż zakończone”.
// W nagłówku link „＋ Nowe zadanie” (okno nowego zadania dla agenta).
// Bez PlasmoidItem, więc da się go załadować w teście i w podglądzie (widgets/preview.py).
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Tide.SidebarTile {
    id: tile

    property var sessions: []
    property bool available: true
    property bool showEnded: false
    property real nowTs: Date.now() / 1000
    property int maxRows: 6
    signal activateRequested(string sessionId)
    signal moreRequested()
    signal showEndedToggled()
    signal newTaskRequested()
    signal sessionsRequested(string coordinatorId)   // okno Tide Sesje (pusty id — bez wyboru)

    // Wiersze i liczniki — wspólne z popupem kafelka w docku (AgentRows, SessionRows).
    readonly property AgentRows agentRows: AgentRows {
        sessions: tile.sessions
        showEnded: tile.showEnded
        nowTs: tile.nowTs
    }
    readonly property int endedCount: agentRows.endedCount
    readonly property int workingCount: agentRows.workingCount
    readonly property int waitingCount: agentRows.waitingCount
    readonly property int doneCount: agentRows.doneCount
    readonly property int hiddenCount: Math.max(0, agentRows.rows.length - maxRows)
    // Wiersze z gotowymi tekstami — z nich rysujemy i to samo sprawdzają testy.
    readonly property var rows: agentRows.rows.slice(0, maxRows)
    readonly property bool anyButton: rows.some(r => r.button)
    readonly property string summary: available ? Tide.Texts.agentsSummary(workingCount, waitingCount, doneCount)
                                                : "usługa nie działa"

    title: "Agenci"
    spacing: 8
    Accessible.name: "Agenci: " + summary

    // Liczniki w nagłówku tylko obok wierszy — bez sesji powtarzałyby linię pod spodem.
    headerItems: [
        Text {
            visible: tile.rows.length > 0
            text: tile.summary
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt - 2
        },
        Tide.TextButton {
            objectName: "newTaskButton"
            text: "＋ Nowe zadanie"
            Accessible.name: "Nowe zadanie dla agenta"
            onClicked: tile.newTaskRequested()
        }
    ]

    SessionRows {
        Layout.fillWidth: true
        visible: tile.rows.length > 0
        rows: tile.rows
        onActivateRequested: id => tile.activateRequested(id)
        onProgressRequested: id => tile.sessionsRequested(id)
    }

    Text {
        Layout.fillWidth: true
        visible: tile.rows.length === 0
        text: !tile.available ? "Usługa tide-daemon nie działa" : "Brak aktywnych sesji"
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        elide: Text.ElideRight
    }

    RowLayout {
        Layout.fillWidth: true
        visible: tile.hiddenCount > 0 || tile.endedCount > 0 || tile.agentRows.coordinatorCount > 0
        spacing: 8

        Tide.TextButton {
            id: moreButton
            visible: tile.hiddenCount > 0
            text: "+" + tile.hiddenCount + " więcej"
            Accessible.name: "Pokaż wszystkie sesje (" + tile.hiddenCount + " więcej)"
            onClicked: tile.moreRequested()
        }
        // Okno Tide Sesje — tylko gdy któraś sesja zleca pracę podagentom (w nagłówku brak miejsca).
        Tide.TextButton {
            objectName: "sessionsButton"
            visible: tile.agentRows.coordinatorCount > 0
            text: "Sesje"
            Accessible.name: "Tide Sesje: koordynatorzy, tickety i podagenci"
            onClicked: tile.sessionsRequested("")
        }
        Item { Layout.fillWidth: true }
        Tide.TextButton {
            visible: tile.endedCount > 0
            accent: Tide.Tokens.textMuted
            pointSize: Tide.Tokens.fontSizePt - 2
            text: (tile.showEnded ? "Ukryj zakończone" : "Pokaż zakończone") + " (" + tile.endedCount + ")"
            onClicked: tile.showEndedToggled()
        }
    }

    readonly property alias moreButton: moreButton
}

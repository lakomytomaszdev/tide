// Kafelek agentów w pasku bocznym: jedna spokojna linia na żywą sesję — kropka stanu (teal
// pracuje, brzoskwinia czeka, puste błękitne kółko skończył), projekt, krótkie słowo stanu
// i czas. Tylko czekające dostają mały przycisk „Przejdź”; skończone i bezczynne są przygaszone.
// Najwyżej maxRows wierszy (czekające pierwsze), resztę otwiera „+N więcej” — pełna lista
// w okienku obok paska. Zakończone sesje są ukryte, dopóki nie kliknie się „Pokaż zakończone”.
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

    // Kolejność wierszy: najpierw ci, którzy czekają na ciebie, potem pracujący, skończeni,
    // bezczynni i (na życzenie) zakończeni; w każdej grupie najświeżsi u góry.
    readonly property var rank: ({ waiting: 0, working: 1, done: 2, idle: 3, ended: 4 })
    readonly property var live: sessions.filter(s => Tide.Theme.sessionState(s) !== "ended")
    readonly property int endedCount: sessions.length - live.length
    readonly property int workingCount: countOf("working")
    readonly property int waitingCount: countOf("waiting")
    readonly property int doneCount: countOf("done")
    readonly property var ordered: (showEnded ? sessions : live).slice().sort((a, b) => {
        const ra = rankOf(a), rb = rankOf(b)
        return ra !== rb ? ra - rb : sinceOf(b) - sinceOf(a)
    })
    readonly property int hiddenCount: Math.max(0, ordered.length - maxRows)
    // Wiersze z gotowymi tekstami — z nich rysujemy i to samo sprawdzają testy.
    readonly property var rows: ordered.slice(0, maxRows).map(s => {
        const state = Tide.Theme.sessionState(s)
        return {
            id: s.id,
            state: state,
            project: s.project || "—",
            word: Tide.Theme.shortStateLabel(state),
            ago: Tide.Theme.shortAgo(sinceOf(s), nowTs),
            button: state === "waiting" && s.canActivate === true,
            clickable: s.canActivate === true
        }
    })
    readonly property bool anyButton: rows.some(r => r.button)
    readonly property string summary: available ? Tide.Texts.agentsSummary(workingCount, waitingCount, doneCount)
                                                : "usługa nie działa"

    function countOf(state) {
        return live.filter(s => Tide.Theme.sessionState(s) === state).length
    }
    function rankOf(session) {
        const value = rank[Tide.Theme.sessionState(session)]
        return value === undefined ? 5 : value
    }
    // Czekający i skończeni liczą czas od zmiany stanu, pozostali od ostatniej aktywności.
    function sinceOf(session) {
        const state = Tide.Theme.sessionState(session)
        return (state === "waiting" || state === "done" ? session.sinceTs : session.lastActivityTs) || 0
    }
    function rowOpacity(state) {
        return state === "done" ? 0.72 : state === "idle" ? 0.55 : state === "ended" ? 0.45 : 1
    }

    title: "Agenci"
    spacing: 8
    Accessible.name: "Agenci: " + summary

    // Liczniki w nagłówku tylko obok wierszy — bez sesji powtarzałyby linię pod spodem.
    headerItems: Text {
        visible: tile.rows.length > 0
        text: tile.summary
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 2
    }

    ColumnLayout {
        Layout.fillWidth: true
        visible: tile.rows.length > 0
        spacing: 2

        Repeater {
            model: tile.rows

            delegate: Item {
                id: row
                required property var modelData
                readonly property bool waiting: modelData.state === "waiting"

                Layout.fillWidth: true
                implicitHeight: Math.max(30, line.implicitHeight + 8)
                opacity: tile.rowOpacity(modelData.state)

                // Czekający wiersz lekko w brzoskwini; najechany — delikatne podświetlenie.
                Rectangle {
                    anchors.fill: parent
                    anchors.leftMargin: -6
                    anchors.rightMargin: -6
                    radius: Tide.Tokens.radiusSmall
                    color: row.waiting ? Tide.Theme.alpha(Tide.Tokens.waiting, area.containsMouse ? 0.16 : 0.10)
                                       : Tide.Theme.alpha(Tide.Tokens.textPrimary, area.containsMouse ? 0.05 : 0)
                }
                // Kliknięcie w wiersz przenosi do okna sesji (jak karta w popupie); przycisk
                // „Przejdź” robi to samo i jest dostępny z klawiatury.
                MouseArea {
                    id: area
                    anchors.fill: parent
                    enabled: row.modelData.clickable
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: tile.activateRequested(row.modelData.id)
                }

                RowLayout {
                    id: line
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 8

                    Tide.StateDot {
                        Layout.alignment: Qt.AlignVCenter
                        sessionState: row.modelData.state
                        size: 8
                    }
                    Text {
                        Layout.fillWidth: true
                        text: row.modelData.project
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        font.weight: Font.Medium
                        elide: Text.ElideRight
                    }
                    Text {
                        Layout.preferredWidth: Math.ceil(Math.max(implicitWidth, wordWidth.advanceWidth))
                        text: row.modelData.word
                        color: row.modelData.state === "idle" || row.modelData.state === "ended"
                               ? Tide.Tokens.textMuted : Tide.Theme.stateColor(row.modelData.state)
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 1
                    }
                    Text {
                        Layout.preferredWidth: Math.ceil(Math.max(implicitWidth, agoWidth.advanceWidth))
                        horizontalAlignment: Text.AlignRight
                        text: row.modelData.ago
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pointSize: Tide.Tokens.fontSizePt - 2
                    }
                    // Kolumna przycisku jest zarezerwowana we wszystkich wierszach, gdy choć jeden
                    // czeka — wtedy słowa stanu i czasy stoją w równych kolumnach.
                    Item {
                        visible: tile.anyButton
                        Layout.preferredWidth: buttonProbe.implicitWidth
                        Layout.preferredHeight: buttonProbe.implicitHeight

                        Tide.TextButton {
                            anchors.fill: parent
                            visible: row.modelData.button
                            variant: "filled"
                            accent: Tide.Tokens.waiting
                            text: "Przejdź"
                            Accessible.name: "Przejdź do sesji " + row.modelData.project
                            onClicked: tile.activateRequested(row.modelData.id)
                        }
                    }
                }
            }
        }
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
        visible: tile.hiddenCount > 0 || tile.endedCount > 0
        spacing: 8

        Tide.TextButton {
            id: moreButton
            visible: tile.hiddenCount > 0
            text: "+" + tile.hiddenCount + " więcej"
            Accessible.name: "Pokaż wszystkie sesje (" + tile.hiddenCount + " więcej)"
            onClicked: tile.moreRequested()
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

    // Kolumny słowa stanu („bezczynna” — najdłuższe) i czasu („59 min”) mają stałą szerokość,
    // żeby wiersze układały się w równe kolumny.
    TextMetrics {
        id: wordWidth
        text: "bezczynna"
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
    }
    TextMetrics {
        id: agoWidth
        text: "59 min"
        font.family: Tide.Theme.monoFamily
        font.pointSize: Tide.Tokens.fontSizePt - 2
    }
    // Wzorzec przycisku do zmierzenia kolumny „Przejdź” (niewidoczny, więc bez fokusu).
    Tide.TextButton {
        id: buttonProbe
        visible: false
        variant: "filled"
        text: "Przejdź"
    }

    readonly property alias moreButton: moreButton
}

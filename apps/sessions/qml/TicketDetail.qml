// Szczegóły ticketu po prawej: ID, gałąź, liczba podagentów i stan, a pod nimi (przewijane) sekcje
// „Co to jest” (tytuł po ludzku i skąd), „Co zrobiono”, „Teraz / review” (opis z describe.py, pełne
// teksty), skąd stan, uwagi z planu i oś czasu po polsku (zlecił implementację → implementacja
// skończona → review: uwagi słowami → poprawki…; angielski opis zlecenia jako drobny dopisek)
// i przyciski: Pokaż wynik, Przejdź do koordynatora, Diff gałęzi, Otwórz w YouTracku, Worktree.
// Teksty z danych zawsze jako Text.PlainText.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "sessions.js" as Logic

Rectangle {
    id: detail

    property var board: null
    property var ticket: null
    property real nowTs: Date.now() / 1000
    property bool canDiff: true
    signal resultRequested(string runId)
    signal activateRequested()
    signal diffRequested(string key)
    signal worktreeRequested(string key)
    signal urlRequested(string url)

    readonly property var events: ticket ? (ticket.events || []) : []
    readonly property string resultRun: Logic.resultRun(board, ticket)

    color: "transparent"

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 20
        spacing: 12

        Text {
            visible: detail.ticket !== null && (detail.ticket.id || "") !== ""
            text: detail.ticket ? (detail.ticket.id || "") : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.sky
            font.family: Tide.Theme.monoFamily
            font.pixelSize: 13
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            visible: detail.ticket === null
            text: "Wybierz ticket na tablicy"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 17
            font.weight: Font.DemiBold
            wrapMode: Text.Wrap
        }
        Flow {
            Layout.fillWidth: true
            spacing: 8
            visible: detail.ticket !== null
            Chip {
                visible: detail.ticket !== null && (detail.ticket.branch || "") !== ""
                text: detail.ticket ? (detail.ticket.branch || "") : ""
                branch: true
                maximumWidth: detail.width - 40
            }
            Chip {
                visible: text !== ""
                text: Logic.runMeta(detail.board, detail.ticket, detail.nowTs)
            }
            Chip {
                visible: detail.ticket !== null
                text: detail.ticket ? detail.ticket.word : ""
                tint: detail.ticket ? ({ "czeka": "peach", "w-toku": "teal", "review": "lavender",
                                         "gotowe": "mint" })[detail.ticket.state] || "" : ""
            }
        }
        // --- opis, źródła i oś czasu (przewijane razem) -----------------------------------------
        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.topMargin: 2
            clip: true
            contentWidth: width
            contentHeight: scroll.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

            ColumnLayout {
                id: scroll
                width: flick.width - 8
                spacing: 12
                visible: detail.ticket !== null

                DetailSection {
                    Layout.fillWidth: true
                    label: "Co to jest"
                    text: detail.ticket ? (detail.ticket.title || "") : ""
                    hint: Logic.titleSources(detail.ticket)
                    heading: true
                }
                DetailSection {
                    Layout.fillWidth: true
                    visible: detail.ticket !== null && (detail.ticket.done || "") !== ""
                    label: "Co zrobiono"
                    text: detail.ticket ? (detail.ticket.done || "") : ""
                }
                DetailSection {
                    Layout.fillWidth: true
                    visible: detail.ticket !== null && (detail.ticket.now || "") !== ""
                    label: "Teraz / review"
                    text: detail.ticket ? (detail.ticket.now || "") : ""
                    tint: detail.ticket && detail.ticket.state === "czeka" ? "peach" : ""
                }
                Text {
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: detail.ticket ? Logic.sourceText(detail.ticket) : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
                Text {
                    Layout.fillWidth: true
                    visible: detail.ticket !== null && (detail.ticket.planNote || "") !== ""
                    text: detail.ticket ? "Uwagi z planu: " + (detail.ticket.planNote || "") : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
                Text {
                    Layout.topMargin: 6
                    text: "PRZEBIEG"
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 11
                    font.weight: Font.DemiBold
                    font.letterSpacing: 0.8
                    Accessible.role: Accessible.Heading
                }

                ColumnLayout {
                    id: timeline
                    Layout.fillWidth: true
                    spacing: 0

                    Text {
                        visible: detail.ticket !== null && detail.events.length === 0
                        Layout.fillWidth: true
                        text: detail.ticket && detail.ticket.inPlan ? "W planie, żaden podagent jeszcze nad nim nie pracował."
                                                                   : "Brak zdarzeń."
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        wrapMode: Text.Wrap
                    }

                    Repeater {
                        model: detail.events
                        RowLayout {
                            id: event
                            required property var modelData
                            required property int index
                            readonly property bool last: index === detail.events.length - 1
                            Layout.fillWidth: true
                            spacing: 10

                            // Szyna: kropka i linia do następnego zdarzenia.
                            Item {
                                Layout.preferredWidth: 22
                                Layout.fillHeight: true
                                Dot {
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    y: 5
                                    tint: Logic.eventTint(event.modelData)
                                    hollow: event.modelData.state === "done" || event.modelData.state === "attention"
                                    pulse: event.modelData.kind === "running"
                                }
                                Rectangle {
                                    visible: !event.last
                                    anchors.horizontalCenter: parent.horizontalCenter
                                    y: 17
                                    width: 1.5
                                    height: parent.height - 21
                                    color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.10)
                                }
                            }
                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.bottomMargin: 14
                                spacing: 3
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    Text {
                                        Layout.fillWidth: true
                                        text: event.modelData.title
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textPrimary
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                        wrapMode: Text.Wrap
                                        maximumLineCount: 2
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        text: Logic.eventTime(event.modelData, detail.nowTs)
                                        color: Tide.Tokens.textMuted
                                        font.family: Tide.Theme.monoFamily
                                        font.pixelSize: 12
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    visible: (event.modelData.meta || "") !== ""
                                    text: event.modelData.meta || ""
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 11
                                    elide: Text.ElideRight
                                }
                                Text {
                                    Layout.fillWidth: true
                                    visible: (event.modelData.text || "") !== ""
                                    text: event.modelData.text || ""
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textSecondary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 13
                                    lineHeight: 1.12
                                    wrapMode: Text.Wrap
                                    maximumLineCount: 4
                                    elide: Text.ElideRight
                                }
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.topMargin: 4
                                    visible: (event.modelData.quote || "") !== ""
                                    implicitHeight: quote.implicitHeight + 16
                                    radius: 8
                                    color: Tide.Theme.alpha(Tide.Tokens.peach, 0.06)
                                    Rectangle {
                                        width: 2
                                        height: parent.height
                                        color: Tide.Tokens.peach
                                    }
                                    Text {
                                        id: quote
                                        x: 12
                                        y: 8
                                        width: parent.width - 20
                                        text: event.modelData.quote || ""
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textPrimary
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 13
                                        wrapMode: Text.Wrap
                                        maximumLineCount: 4
                                        elide: Text.ElideRight
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // --- przyciski --------------------------------------------------------------------
        Flow {
            Layout.fillWidth: true
            spacing: 8
            visible: detail.ticket !== null

            PillButton {
                visible: detail.resultRun !== ""
                text: "Pokaż wynik"
                toolTip: "Raport podagenta i wycinek jego pracy"
                onClicked: detail.resultRequested(detail.resultRun)
            }
            PillButton {
                text: "Przejdź do koordynatora"
                toolTip: detail.board && detail.board.live ? "Okno sesji koordynatora" : "Wznów sesję koordynatora w Konsoli"
                onClicked: detail.activateRequested()
            }
            PillButton {
                visible: detail.ticket !== null && (detail.ticket.branch || "") !== "" && detail.canDiff
                text: "Diff gałęzi"
                toolTip: "git diff gałęzi ticketu względem gałęzi głównej (Konsola)"
                onClicked: detail.diffRequested(detail.ticket.key)
            }
            PillButton {
                visible: detail.ticket !== null && (detail.ticket.url || "") !== ""
                text: "Otwórz w YouTracku"
                toolTip: detail.ticket ? (detail.ticket.url || "") : ""
                onClicked: detail.urlRequested(detail.ticket.url)
            }
            PillButton {
                visible: detail.ticket !== null && (detail.ticket.worktree || "") !== ""
                text: "Worktree"
                toolTip: detail.ticket ? (detail.ticket.worktree || "") : ""
                onClicked: detail.worktreeRequested(detail.ticket.key)
            }
        }
    }
}

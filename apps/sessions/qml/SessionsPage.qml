// Zawartość okna „Tide Sesje” (mock zatwierdzony 26.10.2026): koordynatorzy po lewej (z licznikami
// ticketów) i pojedyncze sesje, tablica wybranego koordynatora pośrodku (Do zrobienia, W toku,
// Czeka, Review, Gotowe; nagłówek z gałęzią, modelem i paskiem postępu), szczegóły ticketu z osią
// czasu po prawej. „Pokaż wynik” otwiera raport podagenta w miejscu tablicy i szczegółów.
// Sam nie rozmawia z usługą — dostaje dane i zgłasza prośby sygnałami (testy ładują go z przykładami).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "sessions.js" as Logic

Rectangle {
    id: page

    property var coordinators: []
    property var board: null
    property var sessions: []
    property var detail: null
    property bool available: true
    property real nowTs: Date.now() / 1000
    property string selectedId: ""
    property string selectedKey: ""
    property bool showingResult: false
    property bool canDiff: true

    signal selectRequested(string id)
    signal activateRequested(string id)
    signal refreshRequested(bool force)
    signal diffRequested(string id, string key)
    signal worktreeRequested(string id, string key)
    signal resultRequested(string id, string run)
    signal urlRequested(string url)
    signal sessionRequested(string id)

    readonly property var singles: Logic.singleSessions(sessions, coordinators)
    readonly property var ticket: Logic.ticketById(board, selectedKey)
    readonly property bool youtrack: board !== null && board.tracker && board.tracker.kind === "youtrack"

    function showResult(runId) {
        showingResult = true
        resultRequested(selectedId, runId)
    }

    onBoardChanged: {
        if (!board) return
        if (!Logic.ticketById(board, selectedKey)) selectedKey = Logic.defaultTicket(board)
    }

    color: Tide.Tokens.backgroundWindow
    focus: true

    RowLayout {
        anchors.fill: parent
        spacing: 0

        // --- lewa kolumna: koordynatorzy ------------------------------------------------------
        Rectangle {
            Layout.preferredWidth: 290
            Layout.fillHeight: true
            color: "transparent"

            Flickable {
                id: sideFlick
                anchors.fill: parent
                anchors.margins: 14
                anchors.topMargin: 18
                clip: true
                contentWidth: width
                contentHeight: side.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

                ColumnLayout {
                    id: side
                    width: sideFlick.width
                    spacing: 8

                    Text {
                        Layout.leftMargin: 6
                        Layout.topMargin: 4
                        text: "KOORDYNATORZY"
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        font.letterSpacing: 0.9
                        font.weight: Font.DemiBold
                        Accessible.role: Accessible.Heading
                    }
                    Text {
                        Layout.fillWidth: true
                        Layout.leftMargin: 6
                        visible: page.coordinators.length === 0
                        text: !page.available ? "Usługa tide-daemon nie działa." : "Żadna sesja z ostatnich dni nie zlecała pracy podagentom."
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        wrapMode: Text.Wrap
                    }
                    Repeater {
                        model: page.coordinators
                        CoordinatorItem {
                            required property var modelData
                            Layout.fillWidth: true
                            coordinator: modelData
                            selected: modelData.id === page.selectedId
                            nowTs: page.nowTs
                            onClicked: page.selectRequested(modelData.id)
                        }
                    }

                    Text {
                        Layout.leftMargin: 6
                        Layout.topMargin: 14
                        visible: page.singles.length > 0
                        text: "POJEDYNCZE SESJE"
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        font.letterSpacing: 0.9
                        font.weight: Font.DemiBold
                        Accessible.role: Accessible.Heading
                    }
                    Repeater {
                        model: page.singles
                        Rectangle {
                            id: single
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: 34
                            radius: 10
                            color: singleArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.04) : "transparent"
                            Accessible.role: Accessible.Button
                            Accessible.name: (modelData.project || "") + " · " + (modelData.tool === "codex" ? "Codex" : "Claude")
                            Accessible.onPressAction: page.sessionRequested(modelData.id)
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 12
                                anchors.rightMargin: 12
                                spacing: 8
                                Dot {
                                    tint: modelData.state === "waiting" ? "peach" : modelData.state === "working" ? "teal" : "sky"
                                    hollow: modelData.state === "done"
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: (modelData.project || "—") + "  <font color='" + Tide.Tokens.textMuted + "'>· "
                                          + (modelData.tool === "codex" ? "Codex" : "Claude") + "</font>"
                                    textFormat: Text.StyledText
                                    color: Tide.Tokens.textPrimary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 13
                                    font.weight: Font.Medium
                                    elide: Text.ElideRight
                                }
                            }
                            MouseArea {
                                id: singleArea
                                anchors.fill: parent
                                hoverEnabled: true
                                enabled: modelData.canActivate === true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: page.sessionRequested(modelData.id)
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            Layout.fillHeight: true
            implicitWidth: 1
            color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07)
        }

        // --- środek i prawa kolumna (albo wynik podagenta) ------------------------------------
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            RowLayout {
                anchors.fill: parent
                spacing: 0
                visible: !page.showingResult

                // Tablica.
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.margins: 22
                    Layout.topMargin: 20
                    spacing: 16

                    Text {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: page.board === null
                        horizontalAlignment: Text.AlignHCenter
                        verticalAlignment: Text.AlignVCenter
                        text: page.coordinators.length > 0 ? "Wybierz koordynatora po lewej." : ""
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 14
                    }

                    // Nagłówek.
                    RowLayout {
                        Layout.fillWidth: true
                        visible: page.board !== null
                        spacing: 14
                        ColumnLayout {
                            Layout.fillWidth: true
                            spacing: 6
                            Text {
                                Layout.fillWidth: true
                                text: page.board ? page.board.title : ""
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 21
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                                Accessible.role: Accessible.Heading
                            }
                            Flow {
                                Layout.fillWidth: true
                                spacing: 10
                                Text {
                                    text: page.board ? page.board.project : ""
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 13
                                    height: 22
                                    verticalAlignment: Text.AlignVCenter
                                }
                                Chip {
                                    visible: page.board !== null && (page.board.branch || "") !== "" && page.board.branch !== "HEAD"
                                    text: page.board ? page.board.branch : ""
                                    branch: true
                                    maximumWidth: 360
                                }
                                Chip {
                                    text: page.board ? ("koordynator · " + (page.board.tool === "codex" ? "Codex" : "Claude")
                                                        + (page.board.model ? " " + page.board.model : "")) : ""
                                }
                                Text {
                                    text: page.board ? Logic.since(page.board.startedTs, page.nowTs) : ""
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 13
                                    height: 22
                                    verticalAlignment: Text.AlignVCenter
                                }
                                Text {
                                    visible: page.board !== null && page.board.plan !== null
                                    text: page.board && page.board.plan ? "plan: " + page.board.plan.path.split("/").pop() : ""
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.monoFamily
                                    font.pixelSize: 12
                                    height: 22
                                    verticalAlignment: Text.AlignVCenter
                                }
                            }
                        }
                        PillButton {
                            Layout.alignment: Qt.AlignTop
                            text: page.youtrack ? "Plan z YouTracka ↻" : "Odśwież ↻"
                            toolTip: page.youtrack
                                     ? (page.board.tracker.error ? "YouTrack: " + page.board.tracker.error : "Odśwież tablicę i dane z YouTracka")
                                     : "Odśwież tablicę"
                            textColor: page.youtrack && page.board.tracker.error ? Tide.Tokens.peach : Tide.Tokens.textSecondary
                            onClicked: page.refreshRequested(true)
                        }
                        PillButton {
                            Layout.alignment: Qt.AlignTop
                            text: "Przejdź do koordynatora"
                            fill: Tide.Tokens.accent
                            textColor: Tide.Tokens.backgroundDesktop
                            strong: true
                            toolTip: page.board && page.board.live ? "Okno sesji koordynatora" : "Wznów sesję w Konsoli"
                            onClicked: page.activateRequested(page.selectedId)
                        }
                    }

                    // Pytanie koordynatora, które nie dotyczy jednego ticketu.
                    Rectangle {
                        Layout.fillWidth: true
                        visible: page.board !== null && page.board.question !== null && page.board.state === "waiting"
                        implicitHeight: questionText.implicitHeight + 18
                        radius: 10
                        color: Tide.Theme.alpha(Tide.Tokens.peach, 0.07)
                        border.width: 1
                        border.color: Tide.Theme.alpha(Tide.Tokens.peach, 0.30)
                        Text {
                            id: questionText
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.margins: 12
                            text: page.board && page.board.question ? "Koordynator czeka na ciebie: „" + page.board.question.text + "”" : ""
                            color: Tide.Tokens.peach
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                            wrapMode: Text.Wrap
                            maximumLineCount: 3
                            elide: Text.ElideRight
                        }
                    }

                    // Postęp.
                    RowLayout {
                        Layout.fillWidth: true
                        visible: page.board !== null
                        spacing: 12
                        Text {
                            text: Logic.progressText(page.board)
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                        }
                        Rectangle {
                            id: bar
                            Layout.fillWidth: true
                            implicitHeight: 6
                            radius: 3
                            color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)
                            clip: true
                            Row {
                                anchors.fill: parent
                                Repeater {
                                    model: Logic.progressParts(page.board)
                                    Rectangle {
                                        required property var modelData
                                        width: bar.width * modelData.part
                                        height: bar.height
                                        color: Tide.Tokens[modelData.tint]
                                    }
                                }
                            }
                        }
                        Text {
                            text: Logic.percent(page.board)
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.monoFamily
                            font.pixelSize: 12
                        }
                    }

                    // Ostrzeżenia o numerach ticketów w planach repozytorium (dyskretnie, nad kolumnami).
                    Rectangle {
                        id: warningBar
                        objectName: "numberingWarnings"
                        readonly property var lines: Logic.warningLines(page.board)
                        Layout.fillWidth: true
                        visible: page.board !== null && lines.length > 0
                        implicitHeight: warningColumn.implicitHeight + 16
                        radius: 10
                        color: Tide.Theme.alpha(Tide.Tokens.butter, 0.06)
                        border.width: 1
                        border.color: Tide.Theme.alpha(Tide.Tokens.butter, 0.26)
                        Accessible.role: Accessible.AlertMessage
                        Accessible.name: lines.join(" ")
                        Column {
                            id: warningColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12
                            spacing: 4
                            Repeater {
                                model: warningBar.lines
                                Text {
                                    required property var modelData
                                    objectName: "numberingWarningLine"
                                    width: warningColumn.width
                                    text: modelData
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.butter
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 12
                                    wrapMode: Text.Wrap
                                    maximumLineCount: 2
                                    elide: Text.ElideRight
                                }
                            }
                        }
                    }

                    // Kolumny.
                    RowLayout {
                        objectName: "boardColumns"
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        visible: page.board !== null
                        spacing: 12
                        Repeater {
                            model: Logic.COLUMNS
                            BoardColumn {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                Layout.preferredWidth: 1
                                spec: modelData
                                tickets: Logic.column(page.board ? page.board.tickets : [], modelData.key)
                                selectedKey: page.selectedKey
                                nowTs: page.nowTs
                                onTicketClicked: key => page.selectedKey = key
                            }
                        }
                    }
                }

                Rectangle {
                    Layout.fillHeight: true
                    implicitWidth: 1
                    visible: page.board !== null
                    color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07)
                }

                TicketDetail {
                    Layout.preferredWidth: 380
                    Layout.fillHeight: true
                    visible: page.board !== null
                    board: page.board
                    ticket: page.ticket
                    nowTs: page.nowTs
                    canDiff: page.canDiff
                    onResultRequested: runId => page.showResult(runId)
                    onActivateRequested: page.activateRequested(page.selectedId)
                    onDiffRequested: key => page.diffRequested(page.selectedId, key)
                    onWorktreeRequested: key => page.worktreeRequested(page.selectedId, key)
                    onUrlRequested: url => page.urlRequested(url)
                }
            }

            ResultPanel {
                anchors.fill: parent
                visible: page.showingResult
                detail: page.detail
                nowTs: page.nowTs
                onCloseRequested: page.showingResult = false
            }
        }
    }
}

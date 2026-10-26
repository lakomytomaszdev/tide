// Pełna lista sesji agentów: nagłówek z liczbą sesji i licznikami, zwarte wiersze (SessionRows —
// te same co w kafelku paska bocznego, tu z drugą, wyciszoną linią i bez limitu wierszy;
// przy bardzo długiej liście przewijane) i „Pokaż zakończone”. To zawartość popupu kafelka
// w docku i okienka „+N więcej” z paska bocznego. W nagłówku „＋ Nowe zadanie” otwiera okno nowego
// zadania dla agenta (tide-daemon /Tasks ShowDialog). Bez PlasmoidItem, więc da się ją załadować w teście.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import "tide" as Tide

ColumnLayout {
    id: view

    property var sessions: []
    property bool available: true
    property bool showEnded: false
    property real nowTs: Date.now() / 1000
    signal activateRequested(string sessionId)
    signal showEndedToggled()
    signal newTaskRequested()

    // Wiersze i liczniki — wspólne z kafelkiem paska bocznego (AgentRows).
    readonly property AgentRows agentRows: AgentRows {
        sessions: view.sessions
        showEnded: view.showEnded
        nowTs: view.nowTs
    }
    readonly property int workingCount: agentRows.workingCount
    readonly property int waitingCount: agentRows.waitingCount
    readonly property int doneCount: agentRows.doneCount
    readonly property int endedCount: agentRows.endedCount
    // Wiersze z gotowymi tekstami — z nich rysujemy i to samo sprawdzają testy.
    readonly property var rows: agentRows.rows
    readonly property var shownSessions: agentRows.ordered

    spacing: Kirigami.Units.largeSpacing

    RowLayout {
        Layout.fillWidth: true
        spacing: Kirigami.Units.smallSpacing

        Text {
            text: "Sesje agentów"
            color: Kirigami.Theme.textColor
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt + 3
            font.weight: Font.DemiBold
        }
        Rectangle {
            radius: height / 2
            color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.10)
            implicitHeight: countLabel.implicitHeight + 4
            implicitWidth: countLabel.implicitWidth + 14
            visible: view.sessions.length > 0
            Text {
                id: countLabel
                anchors.centerIn: parent
                text: view.sessions.length - view.endedCount
                color: Kirigami.Theme.textColor
                font.family: Tide.Theme.monoFamily
                font.pointSize: Tide.Tokens.fontSizePt
            }
        }
        Item { Layout.fillWidth: true }
        Tide.CountPill {
            sessionState: "working"
            count: view.workingCount
            textColor: Kirigami.Theme.textColor
        }
        Tide.CountPill {
            sessionState: "waiting"
            count: view.waitingCount
            emphasized: view.waitingCount > 0
            textColor: Kirigami.Theme.textColor
        }
        Tide.CountPill {
            visible: view.doneCount > 0
            sessionState: "done"
            count: view.doneCount
            textColor: Kirigami.Theme.textColor
        }
        Tide.TextButton {
            objectName: "newTaskButton"
            Layout.leftMargin: Kirigami.Units.smallSpacing
            variant: "outline"
            text: "＋ Nowe zadanie"
            Accessible.name: "Nowe zadanie dla agenta"
            onClicked: view.newTaskRequested()
        }
    }

    QQC2.ScrollView {
        id: scroll
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: view.rows.length > 0
        contentWidth: availableWidth
        QQC2.ScrollBar.horizontal.policy: QQC2.ScrollBar.AlwaysOff

        // Wiersze mają tło wysunięte o 6 px w bok — wcięcie, żeby nie przycinał go brzeg widoku.
        Item {
            width: scroll.availableWidth
            implicitHeight: rowsList.implicitHeight

            SessionRows {
                id: rowsList
                x: 6
                width: parent.width - 12
                rows: view.rows
                showDetail: true
                onActivateRequested: id => view.activateRequested(id)
            }
        }
    }

    Text {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: view.shownSessions.length === 0
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
        text: !view.available ? "Usługa tide-daemon nie działa.\nUruchom: systemctl --user start tide-daemon"
            : "Brak aktywnych sesji z ostatniej doby."
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
    }

    Text {
        visible: view.endedCount > 0
        text: (view.showEnded ? "Ukryj zakończone" : "Pokaż zakończone") + " (" + view.endedCount + ")"
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
        MouseArea {
            anchors.fill: parent
            cursorShape: Qt.PointingHandCursor
            onClicked: view.showEndedToggled()
        }
    }
}

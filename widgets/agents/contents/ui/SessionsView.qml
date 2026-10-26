// Pełna lista sesji agentów: nagłówek z liczbą sesji i licznikami, karty sesji (SessionCard)
// i „Pokaż zakończone”. To zawartość popupu kafelka w docku i okienka „+N więcej” z paska
// bocznego. Bez PlasmoidItem, więc da się ją załadować w teście.
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

    readonly property int workingCount: countOf("working")
    readonly property int waitingCount: countOf("waiting")
    readonly property int doneCount: countOf("done")
    readonly property int endedCount: countOf("ended")
    readonly property var shownSessions: sessions.filter(s => showEnded || s.state !== "ended")

    // Stan liczony przez Theme.sessionState: starsza usługa zgłaszała koniec tury jako „czeka”.
    function countOf(state) {
        return sessions.filter(s => Tide.Theme.sessionState(s) === state).length
    }

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
    }

    QQC2.ScrollView {
        Layout.fillWidth: true
        Layout.fillHeight: true
        visible: view.shownSessions.length > 0

        ListView {
            id: list
            model: view.shownSessions
            spacing: Kirigami.Units.smallSpacing * 1.5
            clip: true
            delegate: SessionCard {
                required property var modelData
                session: modelData
                nowTs: view.nowTs
                width: ListView.view.width
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

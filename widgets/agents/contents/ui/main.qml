// Widżet agentów: kafelek z liczbą sesji pracujących i czekających, popup z listą sesji.
// Dane: tide-daemon przez D-Bus (org.tide.Daemon /Agents, opis w docs/tide-daemon.md).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "tide" as Tide

PlasmoidItem {
    id: root

    readonly property var sessions: Array.isArray(source.result) ? source.result : []
    readonly property int workingCount: countOf("working")
    readonly property int waitingCount: countOf("waiting")
    readonly property int endedCount: countOf("ended")
    readonly property var shownSessions: sessions.filter(s => showEnded || s.state !== "ended")
    property bool showEnded: false
    property real nowTs: Date.now() / 1000

    function countOf(state) {
        return sessions.filter(s => s.state === state).length
    }

    function activate(sessionId) {
        source.call("ActivateSession", [sessionId], function (ok) {
            if (ok) root.expanded = false
        })
    }

    Plasmoid.title: "Sesje agentów"
    Plasmoid.icon: "utilities-terminal"
    Plasmoid.status: waitingCount > 0 ? PlasmaCore.Types.NeedsAttentionStatus : PlasmaCore.Types.ActiveStatus
    toolTipMainText: "Sesje agentów"
    toolTipSubText: source.available
        ? "Pracuje: " + workingCount + " · Czeka na ciebie: " + waitingCount
        : "Usługa tide-daemon nie działa"
    preferredRepresentation: compactRepresentation

    Tide.DaemonSource {
        id: source
        path: "/Agents"
        iface: "org.tide.Daemon.Agents"
        method: "Sessions"
    }

    // Czas „od ostatniej aktywności” liczony na bieżąco, bez pytania usługi.
    Timer {
        interval: 15000
        running: true
        repeat: true
        onTriggered: root.nowTs = Date.now() / 1000
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? -1 : counters.implicitWidth + Kirigami.Units.smallSpacing * 2
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? counters.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded

        Rectangle {
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing / 2
            radius: Tide.Tokens.radiusSmall
            color: Tide.Tokens.textPrimary
            opacity: compact.containsMouse || root.expanded ? 0.08 : 0
            Behavior on opacity { NumberAnimation { duration: Kirigami.Units.shortDuration } }
        }

        Grid {
            id: counters
            anchors.centerIn: parent
            columns: compact.vertical ? 1 : 2
            spacing: Kirigami.Units.smallSpacing
            opacity: source.available ? 1 : 0.45

            Tide.CountPill {
                sessionState: "working"
                count: root.workingCount
                textColor: Kirigami.Theme.textColor
            }
            Tide.CountPill {
                sessionState: "waiting"
                count: root.waitingCount
                emphasized: root.waitingCount > 0
                textColor: Kirigami.Theme.textColor
            }
        }
    }

    fullRepresentation: Item {
        Layout.preferredWidth: Kirigami.Units.gridUnit * 26
        Layout.preferredHeight: Kirigami.Units.gridUnit * 28
        Layout.minimumWidth: Kirigami.Units.gridUnit * 18
        Layout.minimumHeight: Kirigami.Units.gridUnit * 12

        ColumnLayout {
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing
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
                    visible: root.sessions.length > 0
                    Text {
                        id: countLabel
                        anchors.centerIn: parent
                        text: root.sessions.length - root.endedCount
                        color: Kirigami.Theme.textColor
                        font.family: Tide.Theme.monoFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                    }
                }
                Item { Layout.fillWidth: true }
                Tide.CountPill {
                    sessionState: "working"
                    count: root.workingCount
                    textColor: Kirigami.Theme.textColor
                }
                Tide.CountPill {
                    sessionState: "waiting"
                    count: root.waitingCount
                    emphasized: root.waitingCount > 0
                    textColor: Kirigami.Theme.textColor
                }
            }

            QQC2.ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: root.shownSessions.length > 0

                ListView {
                    id: list
                    model: root.shownSessions
                    spacing: Kirigami.Units.smallSpacing * 1.5
                    clip: true
                    delegate: SessionCard {
                        required property var modelData
                        session: modelData
                        nowTs: root.nowTs
                        width: ListView.view.width
                        onActivateRequested: id => root.activate(id)
                    }
                }
            }

            Text {
                Layout.fillWidth: true
                Layout.fillHeight: true
                visible: root.shownSessions.length === 0
                horizontalAlignment: Text.AlignHCenter
                verticalAlignment: Text.AlignVCenter
                wrapMode: Text.WordWrap
                text: !source.available ? "Usługa tide-daemon nie działa.\nUruchom: systemctl --user start tide-daemon"
                    : "Brak aktywnych sesji z ostatniej doby."
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
            }

            Text {
                visible: root.endedCount > 0
                text: (root.showEnded ? "Ukryj zakończone" : "Pokaż zakończone") + " (" + root.endedCount + ")"
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt - 1
                MouseArea {
                    anchors.fill: parent
                    cursorShape: Qt.PointingHandCursor
                    onClicked: root.showEnded = !root.showEnded
                }
            }
        }
    }
}

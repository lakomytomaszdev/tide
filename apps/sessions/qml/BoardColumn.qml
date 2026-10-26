// Kolumna tablicy: nagłówek z kropką i liczbą, karty ticketów (przewijane), w „Gotowe” tylko kilka
// ostatnich i „+N więcej”.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "sessions.js" as Logic

Rectangle {
    id: column

    property var spec: ({})
    property var tickets: []
    property string selectedKey: ""
    property real nowTs: Date.now() / 1000
    property bool expanded: false
    signal ticketClicked(string key)

    readonly property int limit: spec.key === "gotowe" && !expanded ? Logic.DONE_PREVIEW : 100000
    readonly property var shown: tickets.slice(0, limit)

    radius: 16
    color: Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.55)
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07)

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 10
        anchors.topMargin: 12
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 4
            Layout.rightMargin: 4
            spacing: 8
            Dot {
                visible: column.spec.dot === true
                tint: column.spec.tint || "textMuted"
                hollow: column.spec.hollow === true
            }
            Text {
                Layout.fillWidth: true
                text: column.spec.title || ""
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                Accessible.role: Accessible.Heading
            }
            Text {
                text: column.tickets.length
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 12
            }
        }

        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: cards.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: flick.contentHeight > flick.height ? QQC2.ScrollBar.AsNeeded : QQC2.ScrollBar.AlwaysOff }

            ColumnLayout {
                id: cards
                width: flick.width
                spacing: 10

                Repeater {
                    model: column.shown
                    TicketCard {
                        required property var modelData
                        Layout.fillWidth: true
                        ticket: modelData
                        selected: modelData.key === column.selectedKey
                        nowTs: column.nowTs
                        onClicked: column.ticketClicked(modelData.key)
                    }
                }
                Text {
                    Layout.leftMargin: 6
                    visible: column.tickets.length > column.shown.length || (column.expanded && column.spec.key === "gotowe"
                                                                               && column.tickets.length > Logic.DONE_PREVIEW)
                    text: column.expanded ? "pokaż mniej" : "+" + (column.tickets.length - column.shown.length) + " więcej"
                    color: moreArea.containsMouse ? Tide.Tokens.accent : Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    Accessible.role: Accessible.Button
                    Accessible.name: text
                    Accessible.onPressAction: column.expanded = !column.expanded
                    MouseArea {
                        id: moreArea
                        anchors.fill: parent
                        anchors.margins: -4
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: column.expanded = !column.expanded
                    }
                }
                Text {
                    Layout.leftMargin: 6
                    visible: column.tickets.length === 0
                    text: "—"
                    color: Tide.Theme.alpha(Tide.Tokens.textMuted, 0.6)
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }
            }
        }
    }
}

// Koordynator na liście po lewej: kropka stanu, projekt, jedna linia opisu i liczniki ticketów
// (● w toku, ● czeka, ○ gotowe). Zaznaczony — tealowe tło z obwódką, zakończony — przygaszony.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "sessions.js" as Logic

Rectangle {
    id: item

    property var coordinator: ({})
    property bool selected: false
    property real nowTs: Date.now() / 1000
    signal clicked()

    readonly property var counts: coordinator.counts || {}
    readonly property int working: (counts["w-toku"] || 0) + (counts["review"] || 0)

    implicitHeight: body.implicitHeight + 24
    radius: 14
    color: selected ? Tide.Theme.alpha(Tide.Tokens.accent, 0.08)
         : area.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.04) : "transparent"
    border.width: 1
    border.color: selected ? Tide.Theme.alpha(Tide.Tokens.accent, 0.30) : "transparent"
    opacity: coordinator.live || selected ? 1 : 0.62
    activeFocusOnTab: true

    Accessible.role: Accessible.Button
    Accessible.name: (coordinator.project || "") + ": " + Logic.coordinatorLine(coordinator, nowTs)
    Accessible.onPressAction: item.clicked()
    Keys.onReturnPressed: item.clicked()
    Keys.onSpacePressed: item.clicked()

    ColumnLayout {
        id: body
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 12
        spacing: 4

        RowLayout {
            Layout.fillWidth: true
            spacing: 8
            Dot {
                tint: Logic.coordinatorTint(item.coordinator)
                hollow: Logic.coordinatorHollow(item.coordinator)
                pulse: item.coordinator.state === "working"
            }
            Text {
                Layout.fillWidth: true
                text: item.coordinator.project || "—"
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 14
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
            Text {
                visible: (item.coordinator.label || "") !== ""
                text: item.coordinator.label || ""
                color: Tide.Tokens.sky
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 11
            }
        }
        Text {
            Layout.fillWidth: true
            text: Logic.coordinatorLine(item.coordinator, item.nowTs)
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            wrapMode: Text.Wrap
            maximumLineCount: 2
            elide: Text.ElideRight
        }
        RowLayout {
            Layout.topMargin: 4
            spacing: 12
            visible: (item.counts.total || 0) > 0
            Repeater {
                model: [
                    { tint: "teal", n: item.working, hollow: false },
                    { tint: "peach", n: item.counts["czeka"] || 0, hollow: false },
                    { tint: "sky", n: item.counts["gotowe"] || 0, hollow: true }
                ].filter(p => p.n > 0)
                RowLayout {
                    required property var modelData
                    spacing: 5
                    Dot { tint: modelData.tint; hollow: modelData.hollow; size: 7 }
                    Text {
                        text: modelData.n
                        color: modelData.hollow ? Tide.Tokens.textMuted : Tide.Tokens.textSecondary
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 12
                    }
                }
            }
            Item { Layout.fillWidth: true }
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: item.clicked()
    }
}

// Doba na liście raportów: „Dziś · wt 27.10” z brzoskwiniową kropką nieotwartego raportu
// i „07:00 · 5 projektów” pod spodem; wybrana — tło w kolorze akcentu.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Rectangle {
    id: item

    property string label: ""
    property string sub: ""
    property bool fresh: false
    property bool selected: false
    signal clicked()

    implicitHeight: column.implicitHeight + 18
    radius: Tide.Tokens.radiusFrame
    color: selected ? Tide.Theme.alpha(Tide.Tokens.accent, 0.10)
         : area.containsMouse || activeFocus ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.04) : "transparent"
    border.width: activeFocus ? 1 : 0
    border.color: Tide.Tokens.accent
    activeFocusOnTab: true

    Accessible.role: Accessible.Button
    Accessible.name: label + (fresh ? ", nowy" : "") + (sub ? ", " + sub : "")
    Accessible.onPressAction: clicked()
    Keys.onReturnPressed: clicked()
    Keys.onSpacePressed: clicked()

    ColumnLayout {
        id: column
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: 10
        anchors.rightMargin: 10
        spacing: 3

        RowLayout {
            spacing: 6
            Text {
                objectName: "dayLabel"
                text: item.label
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
            }
            Dot {
                objectName: "freshDot"
                visible: item.fresh
                tint: "waiting"
                size: 7
            }
        }
        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: item.sub
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
            elide: Text.ElideRight
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

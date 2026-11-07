import QtQuick
import "tide" as Tide

Rectangle {
    id: button
    property string text: ""
    property bool primary: false
    signal clicked()
    implicitWidth: label.implicitWidth + 28
    implicitHeight: 36
    radius: Tide.Tokens.radiusFrame
    color: primary ? Tide.Tokens.accent : Tide.Tokens.backgroundCard
    border.width: activeFocus ? 2 : 1
    border.color: activeFocus ? Tide.Tokens.sky : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.15)
    opacity: enabled ? 1 : 0.5
    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.onPressAction: if (enabled) clicked()
    Keys.onReturnPressed: if (enabled) clicked()
    Keys.onSpacePressed: if (enabled) clicked()
    Text {
        id: label
        anchors.centerIn: parent
        text: button.text
        textFormat: Text.PlainText
        color: button.primary ? Tide.Tokens.backgroundDesktop : Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 13
    }
    MouseArea {
        anchors.fill: parent
        cursorShape: button.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (button.enabled) button.clicked()
    }
}

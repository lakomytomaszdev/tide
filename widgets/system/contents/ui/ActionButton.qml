// Płaski przycisk w stopce popupu („Monitor systemu”, „Analiza dysku”). Działa z klawiatury.
import QtQuick
import "tide" as Tide

Rectangle {
    id: button

    property string text: ""
    signal clicked()

    implicitWidth: label.implicitWidth + 28
    implicitHeight: Math.max(32, label.implicitHeight + 16)
    radius: Tide.Tokens.radiusSmall
    color: area.containsMouse || activeFocus ? Qt.lighter(Tide.Tokens.backgroundCard, 1.25) : Tide.Tokens.backgroundCard
    border.width: activeFocus ? 1 : 0
    border.color: Tide.Tokens.accent
    activeFocusOnTab: true

    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.onPressAction: button.clicked()
    Keys.onReturnPressed: button.clicked()
    Keys.onEnterPressed: button.clicked()
    Keys.onSpacePressed: button.clicked()

    Behavior on color { ColorAnimation { duration: 150 } }

    Text {
        id: label
        anchors.centerIn: parent
        text: button.text
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt - 1
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: button.clicked()
    }
}

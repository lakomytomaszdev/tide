// Przycisk widoku Docker: zaokrąglony prostokąt z palety, bez zależności od stylu QtQuick Controls
// (jak w widoku Worktrees). Tekst zwykły — bywa w nim nazwa z usługi.
import QtQuick
import QtQuick.Controls as QQC2
import "tide" as Tide

Rectangle {
    id: button

    property string text: ""
    property string toolTip: ""
    property color fill: Tide.Tokens.backgroundCard
    property color textColor: Tide.Tokens.textSecondary
    property bool strong: false
    property real horizontalPadding: 14
    property int pixelSize: 13
    signal clicked()

    implicitHeight: 36
    implicitWidth: label.implicitWidth + horizontalPadding * 2
    radius: Tide.Tokens.radiusFrame
    color: !enabled ? Tide.Tokens.backgroundWindow
         : area.pressed ? Qt.darker(fill, 1.08)
         : area.containsMouse || activeFocus ? Qt.lighter(fill, 1.1) : fill
    border.width: activeFocus ? 1 : 0
    border.color: Tide.Tokens.accent
    activeFocusOnTab: enabled

    Accessible.role: Accessible.Button
    Accessible.name: toolTip || text
    Accessible.onPressAction: if (enabled) clicked()
    Keys.onReturnPressed: if (enabled) clicked()
    Keys.onEnterPressed: if (enabled) clicked()
    Keys.onSpacePressed: if (enabled) clicked()

    Text {
        id: label
        anchors.centerIn: parent
        width: Math.min(implicitWidth, button.width - button.horizontalPadding)
        text: button.text
        textFormat: Text.PlainText
        color: button.enabled ? button.textColor : Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: button.pixelSize
        font.weight: button.strong ? Font.DemiBold : Font.Medium
        elide: Text.ElideRight
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        enabled: button.enabled
        cursorShape: Qt.PointingHandCursor
        onClicked: button.clicked()
    }

    QQC2.ToolTip.visible: area.containsMouse && toolTip !== ""
    QQC2.ToolTip.text: toolTip
    QQC2.ToolTip.delay: 500
}

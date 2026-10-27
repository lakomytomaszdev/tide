// Przycisk widoku Tablica: zaokrąglony prostokąt z palety (bez stylu QtQuick Controls). Warianty:
// wypełniony (fill), obrysowany (outline) i tekstowy (flat — bez tła, tylko kolor tekstu).
import QtQuick
import QtQuick.Controls as QQC2
import "tide" as Tide

Rectangle {
    id: button

    property string text: ""
    property string toolTip: ""
    property color fill: Tide.Tokens.backgroundCard
    property color textColor: Tide.Tokens.textSecondary
    property bool outline: false
    property bool flat: false
    property bool strong: false
    property real horizontalPadding: 14
    property int pixelSize: 13
    property real pillRadius: Tide.Tokens.radiusFrame
    property bool leftAligned: false
    signal clicked()

    implicitHeight: 36
    implicitWidth: label.implicitWidth + horizontalPadding * 2
    radius: pillRadius
    color: flat || outline ? (area.containsMouse || activeFocus ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05) : "transparent")
         : !enabled ? Tide.Tokens.backgroundCard
         : area.pressed ? Qt.darker(fill, 1.08)
         : area.containsMouse || activeFocus ? Qt.lighter(fill, 1.1) : fill
    border.width: outline || activeFocus ? 1 : 0
    border.color: activeFocus ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.16)
    activeFocusOnTab: true
    opacity: enabled ? 1 : 0.55

    Accessible.role: Accessible.Button
    Accessible.name: toolTip || text
    Accessible.onPressAction: if (enabled) clicked()
    Keys.onReturnPressed: if (enabled) clicked()
    Keys.onEnterPressed: if (enabled) clicked()
    Keys.onSpacePressed: if (enabled) clicked()

    Text {
        id: label
        x: button.leftAligned ? button.horizontalPadding : (button.width - width) / 2
        anchors.verticalCenter: parent.verticalCenter
        text: button.text
        textFormat: Text.PlainText
        color: button.enabled || button.flat || button.outline ? button.textColor : Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: button.pixelSize
        font.weight: button.strong ? Font.DemiBold : Font.Medium
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: button.enabled ? Qt.PointingHandCursor : Qt.ArrowCursor
        onClicked: if (button.enabled) button.clicked()
    }

    QQC2.ToolTip.visible: area.containsMouse && toolTip !== ""
    QQC2.ToolTip.text: toolTip
    QQC2.ToolTip.delay: 500
}

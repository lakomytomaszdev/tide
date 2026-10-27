// Kwadratowy przycisk akcji wiersza kontenera (28 px, promień 8 — mock „Tide Docker”): znak z Glyph,
// opcjonalnie zabarwiony (Uruchom w mięcie, Usuń w różu, otwarte logi w sky). Rola Button, Tab,
// spacja i Enter; nazwa dla czytnika ekranu i podpowiedź z „name”.
import QtQuick
import QtQuick.Controls as QQC2
import "tide" as Tide

Rectangle {
    id: button

    property string icon: ""
    property string name: ""                 // co robi — podpowiedź i nazwa dla czytnika ekranu
    property color tint: Tide.Tokens.textSecondary
    property bool tinted: false              // tło w kolorze tint (Uruchom, otwarte logi)
    property bool highlighted: false
    signal clicked()

    implicitWidth: 28
    implicitHeight: 28
    radius: 8
    color: tinted || highlighted ? Tide.Theme.alpha(tint, area.containsMouse || activeFocus ? 0.24 : (highlighted ? 0.18 : 0.14))
         : area.containsMouse || activeFocus ? Qt.lighter(Tide.Tokens.backgroundCard, 1.18) : Tide.Tokens.backgroundCard
    opacity: enabled ? 1 : 0.4
    border.width: activeFocus ? 1 : 0
    border.color: Tide.Tokens.accent
    activeFocusOnTab: enabled

    Accessible.role: Accessible.Button
    Accessible.name: name
    Accessible.onPressAction: if (enabled) clicked()
    Keys.onReturnPressed: if (enabled) clicked()
    Keys.onEnterPressed: if (enabled) clicked()
    Keys.onSpacePressed: if (enabled) clicked()

    Glyph {
        anchors.centerIn: parent
        width: button.icon === "stop" || button.icon === "play" ? 13 : 15
        height: width
        name: button.icon
        color: button.tint
        stroke: button.icon === "stop" ? 2.2 : 2
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        enabled: button.enabled
        cursorShape: Qt.PointingHandCursor
        onClicked: button.clicked()
    }

    QQC2.ToolTip.visible: area.containsMouse && name !== ""
    QQC2.ToolTip.text: name
    QQC2.ToolTip.delay: 500
}

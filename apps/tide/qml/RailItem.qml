// Pozycja paska widoków: znak, nazwa i plakietka (liczba w Geist Mono, „● 1” albo słowo w kolorze
// tonu). Wybrana — tło w tealu ~0,10, tekst główny 600, znak w tealu. Tab, strzałki, Enter i spacja.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Rectangle {
    id: item

    property string title: ""
    property string icon: ""
    property string badgeText: ""
    property string badgeTone: "muted"
    property bool current: false

    signal clicked()

    readonly property bool numeric: /^[0-9]+$/.test(badgeText)

    implicitHeight: 38
    radius: Tide.Tokens.radiusFrame
    color: current ? Tide.Theme.alpha(Tide.Tokens.accent, 0.10)
                   : mouse.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.04) : "transparent"
    border.width: activeFocus ? 1 : 0
    border.color: Tide.Theme.alpha(Tide.Tokens.accent, 0.6)

    activeFocusOnTab: true
    Keys.onReturnPressed: item.clicked()
    Keys.onEnterPressed: item.clicked()
    Keys.onSpacePressed: item.clicked()

    Accessible.role: Accessible.PageTab
    Accessible.name: badgeText !== "" ? title + ", " + badgeText : title
    Accessible.selected: current
    Accessible.onPressAction: item.clicked()

    RowLayout {
        anchors.fill: parent
        anchors.leftMargin: 10
        anchors.rightMargin: 10
        spacing: 10

        RailIcon {
            Layout.preferredWidth: 18
            Layout.preferredHeight: 18
            name: item.icon
            color: item.current ? Tide.Tokens.accent : Tide.Tokens.textMuted
        }
        Text {
            Layout.fillWidth: true
            text: item.title
            color: item.current ? Tide.Tokens.textPrimary : Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 14
            font.weight: item.current ? Font.DemiBold : Font.Medium
            elide: Text.ElideRight
        }
        Text {
            visible: item.badgeText !== ""
            text: item.badgeText
            color: item.badgeTone === "waiting" ? Tide.Tokens.waiting
                 : item.badgeTone === "accent" ? Tide.Tokens.accent : Tide.Tokens.textMuted
            font.family: item.numeric ? Tide.Theme.monoFamily : Tide.Theme.uiFamily
            font.pixelSize: 11
        }
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: item.clicked()
    }
}

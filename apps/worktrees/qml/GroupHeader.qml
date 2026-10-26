// Nagłówek grupy: kropka w kolorze grupy, nazwa, liczba, opis i opcjonalny link po prawej.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

RowLayout {
    id: header

    property color tint: Tide.Tokens.accent
    property string title: ""
    property int count: 0
    property string description: ""
    property string actionText: ""
    signal actionTriggered()

    spacing: 10

    Rectangle {
        Layout.alignment: Qt.AlignVCenter
        implicitWidth: 8
        implicitHeight: 8
        radius: 4
        color: header.tint
    }
    Text {
        text: header.title
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 15
        font.weight: Font.DemiBold
        Accessible.role: Accessible.Heading
    }
    Text {
        text: header.count
        color: header.tint
        font.family: Tide.Theme.monoFamily
        font.pixelSize: 13
        font.weight: Font.DemiBold
    }
    Text {
        Layout.fillWidth: true
        text: header.description
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
        elide: Text.ElideRight
    }
    Text {
        id: action
        visible: header.actionText !== ""
        text: header.actionText
        color: actionArea.containsMouse ? Qt.lighter(Tide.Tokens.accent, 1.2) : Tide.Tokens.accent
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
        font.weight: Font.Medium
        Accessible.role: Accessible.Button
        Accessible.name: text
        Accessible.onPressAction: header.actionTriggered()
        MouseArea {
            id: actionArea
            anchors.fill: parent
            anchors.margins: -4
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: header.actionTriggered()
        }
    }
}

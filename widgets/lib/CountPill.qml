// Licznik z kropką stanu, np. „● 3” w kafelku panelu. Wyróżniony dostaje zabarwione tło.
// Kolor wynika ze stanu sesji (sessionState) albo jest podany wprost (tint), np. dla worktrees.
import QtQuick

Rectangle {
    id: pill

    property string sessionState: ""
    property color tint: Theme.stateColor(sessionState)
    property int count: 0
    property bool emphasized: false
    property real pointSize: Tokens.fontSizePt
    property color textColor: Tokens.textPrimary

    implicitWidth: row.implicitWidth + height * 0.7
    implicitHeight: label.implicitHeight + 4
    radius: height / 2
    color: emphasized ? Theme.alpha(tint, 0.22) : "transparent"

    Row {
        id: row
        anchors.centerIn: parent
        spacing: Math.round(label.implicitHeight * 0.3)

        StateDot {
            anchors.verticalCenter: parent.verticalCenter
            sessionState: pill.sessionState
            tint: pill.tint
            size: Math.round(label.implicitHeight * 0.5)
        }
        Text {
            id: label
            text: pill.count
            color: pill.emphasized ? pill.tint : pill.textColor
            font.family: Theme.monoFamily
            font.pointSize: pill.pointSize
            font.weight: pill.emphasized ? Font.DemiBold : Font.Medium
        }
    }
}

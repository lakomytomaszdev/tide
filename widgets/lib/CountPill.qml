// Licznik z kropką stanu, np. „● 3” w kafelku panelu. Wyróżniony dostaje zabarwione tło.
import QtQuick

Rectangle {
    id: pill

    property string sessionState: ""
    property int count: 0
    property bool emphasized: false
    property real pointSize: Tokens.fontSizePt
    property color textColor: Tokens.textPrimary

    implicitWidth: row.implicitWidth + height * 0.7
    implicitHeight: label.implicitHeight + 4
    radius: height / 2
    color: emphasized ? Theme.alpha(Theme.stateColor(sessionState), 0.22) : "transparent"

    Row {
        id: row
        anchors.centerIn: parent
        spacing: Math.round(label.implicitHeight * 0.3)

        StateDot {
            anchors.verticalCenter: parent.verticalCenter
            sessionState: pill.sessionState
            size: Math.round(label.implicitHeight * 0.5)
        }
        Text {
            id: label
            text: pill.count
            color: pill.emphasized ? Theme.stateColor(pill.sessionState) : pill.textColor
            font.family: Theme.monoFamily
            font.pointSize: pill.pointSize
            font.weight: pill.emphasized ? Font.DemiBold : Font.Medium
        }
    }
}

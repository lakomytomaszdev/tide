// Cienki pasek miernika: value od 0 do 1, null = brak danych.
import QtQuick

Item {
    id: meter

    property var value: null
    property color color: Tokens.accent
    property color trackColor: Theme.alpha(Tokens.textPrimary, 0.12)
    readonly property real clamped: value === null || value === undefined ? 0 : Math.max(0, Math.min(1, value))

    implicitWidth: 120
    implicitHeight: 6

    Rectangle {
        anchors.fill: parent
        radius: height / 2
        color: meter.trackColor
    }
    Rectangle {
        height: parent.height
        width: parent.width * meter.clamped
        radius: height / 2
        color: meter.value === null ? Tokens.textMuted : meter.color
        Behavior on width { NumberAnimation { duration: 250; easing.type: Easing.OutCubic } }
        Behavior on color { ColorAnimation { duration: 250 } }
    }
}

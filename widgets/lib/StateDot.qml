// Kropka stanu sesji. Przy „pracuje” delikatnie pulsuje.
import QtQuick

Rectangle {
    id: dot

    property string sessionState: ""
    property real size: 8

    implicitWidth: size
    implicitHeight: size
    radius: size / 2
    color: Theme.stateColor(sessionState)

    SequentialAnimation on opacity {
        running: dot.sessionState === "working" && dot.visible
        loops: Animation.Infinite
        alwaysRunToEnd: true
        NumberAnimation { from: 1.0; to: 0.45; duration: 900; easing.type: Easing.InOutSine }
        NumberAnimation { from: 0.45; to: 1.0; duration: 900; easing.type: Easing.InOutSine }
    }
}

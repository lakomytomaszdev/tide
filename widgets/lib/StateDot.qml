// Kropka stanu sesji. Przy „pracuje” delikatnie pulsuje, „skończył” (done) jest pustym kółkiem —
// spokojny znak, że agent oddał głos, ale niczego nie blokuje.
import QtQuick

Rectangle {
    id: dot

    property string sessionState: ""
    property real size: 8
    property color tint: Theme.stateColor(sessionState)
    readonly property bool hollow: sessionState === "done"

    implicitWidth: size
    implicitHeight: size
    radius: size / 2
    color: hollow ? "transparent" : tint
    border.width: hollow ? Math.max(1.5, size * 0.2) : 0
    border.color: tint

    SequentialAnimation on opacity {
        running: dot.sessionState === "working" && dot.visible
        loops: Animation.Infinite
        alwaysRunToEnd: true
        NumberAnimation { from: 1.0; to: 0.45; duration: 900; easing.type: Easing.InOutSine }
        NumberAnimation { from: 0.45; to: 1.0; duration: 900; easing.type: Easing.InOutSine }
    }
}

// Kropka stanu w kolorze tokenu palety (nazwa, np. "teal"); puste kółko dla „skończył”.
// Przy pulse delikatna obwódka jak w mocku (koordynator albo agent, który teraz pracuje).
import QtQuick
import "tide" as Tide

Rectangle {
    id: dot

    property string tint: "textMuted"
    property bool hollow: false
    property bool pulse: false
    property real size: 8
    readonly property color tone: Tide.Tokens[tint] !== undefined ? Tide.Tokens[tint] : Tide.Tokens.textMuted

    implicitWidth: size
    implicitHeight: size
    radius: size / 2
    color: hollow ? "transparent" : tone
    border.width: hollow ? 1.6 : 0
    border.color: tone

    Rectangle {
        visible: dot.pulse
        anchors.centerIn: parent
        width: dot.size + 6
        height: width
        radius: width / 2
        color: "transparent"
        border.width: 3
        border.color: Tide.Theme.alpha(dot.tone, 0.18)
    }
}

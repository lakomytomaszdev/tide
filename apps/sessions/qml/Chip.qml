// Mały znacznik: gałąź (lawenda, Geist Mono) albo rola/opis (wyciszony).
import QtQuick
import "tide" as Tide

Rectangle {
    id: chip

    property string text: ""
    property bool branch: false
    property string tint: branch ? "lavender" : ""

    readonly property color tone: tint !== "" ? Tide.Tokens[tint] : Tide.Tokens.textSecondary
    implicitHeight: label.implicitHeight + 6
    implicitWidth: Math.min(label.implicitWidth + 18, maximumWidth)
    property real maximumWidth: 420
    radius: 8
    color: tint !== "" ? Tide.Theme.alpha(tone, 0.14) : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07)

    Text {
        id: label
        anchors.centerIn: parent
        width: Math.min(implicitWidth, chip.maximumWidth - 18)
        text: chip.text
        color: chip.tone
        elide: Text.ElideMiddle
        font.family: chip.branch ? Tide.Theme.monoFamily : Tide.Theme.uiFamily
        font.pixelSize: 12
    }
}

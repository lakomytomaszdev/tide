// Kropka w kolorze tokenu palety (nazwa, np. "teal") albo podanym kolorze; puste kółko (hollow) dla
// kolumn Backlog i Gotowe.
import QtQuick
import "tide" as Tide

Rectangle {
    id: dot

    property string tint: "textMuted"
    property color colorOverride: "transparent"
    property bool hollow: false
    property real size: 9
    readonly property color tone: colorOverride.a > 0 ? colorOverride
        : Tide.Tokens[tint] !== undefined ? Tide.Tokens[tint] : Tide.Tokens.textMuted

    implicitWidth: size
    implicitHeight: size
    radius: size / 2
    color: hollow ? "transparent" : tone
    border.width: hollow ? 1.5 : 0
    border.color: tone
}

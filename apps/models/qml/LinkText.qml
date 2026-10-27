// Link-przycisk (Zaawansowane, Usuń token, Pobierz teraz): sam tekst 12 px, podkreślenie po najechaniu,
// rola Button, Tab, spacja i Enter. Wyłączony — wyciszony.
import QtQuick
import "tide" as Tide

Text {
    id: link

    property color tint: Tide.Tokens.accent
    signal clicked()

    textFormat: Text.PlainText
    color: !enabled ? Tide.Tokens.textMuted : area.containsMouse || activeFocus ? Qt.lighter(tint, 1.15) : tint
    font.family: Tide.Theme.uiFamily
    font.pixelSize: 12
    font.weight: Font.Medium
    font.underline: enabled && (area.containsMouse || activeFocus)
    activeFocusOnTab: enabled

    Accessible.role: Accessible.Button
    Accessible.name: text
    Accessible.onPressAction: if (enabled) clicked()
    Keys.onReturnPressed: if (enabled) clicked()
    Keys.onEnterPressed: if (enabled) clicked()
    Keys.onSpacePressed: if (enabled) clicked()

    MouseArea {
        id: area
        anchors.fill: parent
        anchors.margins: -4
        hoverEnabled: true
        enabled: link.enabled
        cursorShape: Qt.PointingHandCursor
        onClicked: link.clicked()
    }
}

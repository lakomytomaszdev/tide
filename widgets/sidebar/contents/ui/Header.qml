// Wygląd nagłówka paska bocznego: „Tide” i plakietka ze skrótem (Geist Mono na tle karty),
// z marginesem jak wnętrze kafelków (SidebarTile), żeby tekst stał w jednej linii z ich treścią.
// Bez PlasmoidItem, więc podgląd paska (widgets/preview.py) rysuje ten sam nagłówek.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

RowLayout {
    id: header

    property string shortcutText: ""
    property real inset: 14

    implicitHeight: Math.max(title.implicitHeight, badge.implicitHeight) + 8
    spacing: 8

    Text {
        id: title
        Layout.fillWidth: true
        Layout.leftMargin: header.inset
        text: "Tide"
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 15
        font.weight: Font.DemiBold
        elide: Text.ElideRight
    }

    Rectangle {
        id: badge
        Layout.rightMargin: header.inset
        visible: header.shortcutText !== ""
        implicitWidth: shortcut.implicitWidth + 14
        implicitHeight: shortcut.implicitHeight + 6
        radius: 6
        color: Tide.Tokens.backgroundCard

        Text {
            id: shortcut
            anchors.centerIn: parent
            text: header.shortcutText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.monoFamily
            font.pixelSize: 11
        }
    }
}

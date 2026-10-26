// Wygląd nagłówka paska bocznego: „Tide”, obok link do raportu porannego („Raport · dziś 07:00”
// z brzoskwiniową kropką, dopóki raport jest nieotwarty; bez raportu link znika) i plakietka
// ze skrótem (Geist Mono na tle karty), z marginesem jak wnętrze kafelków (SidebarTile), żeby
// tekst stał w jednej linii z ich treścią.
// Bez PlasmoidItem, więc podgląd paska (widgets/preview.py) rysuje ten sam nagłówek.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

RowLayout {
    id: header

    property string shortcutText: ""
    property real inset: 14
    // Najnowszy raport z tide-daemon (/Reports Status()) albo null.
    property var report: null
    property real nowTs: Date.now() / 1000
    readonly property string reportText: Tide.Texts.reportLink(report, nowTs)
    readonly property bool reportUnread: reportText !== "" && report.unread === true
    signal reportRequested()

    implicitHeight: Math.max(title.implicitHeight, badge.implicitHeight) + 8
    spacing: 8

    Text {
        id: title
        Layout.leftMargin: header.inset
        text: "Tide"
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 15
        font.weight: Font.DemiBold
        elide: Text.ElideRight
    }

    Tide.ReportLink {
        objectName: "reportLink"
        Layout.leftMargin: 2
        Layout.maximumWidth: Math.max(0, header.width - title.implicitWidth - badge.implicitWidth - header.inset * 2 - 24)
        visible: header.reportText !== ""
        text: header.reportText
        unread: header.reportUnread
        pointSize: Tide.Tokens.fontSizePt - 1.5
        Accessible.name: Tide.Texts.reportAccessible(header.report, header.nowTs)
        onClicked: header.reportRequested()
    }

    Item { Layout.fillWidth: true }

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

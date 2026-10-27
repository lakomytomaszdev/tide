// Pasek widoków okna Tide (mock „Tide Tablica”, 200 px): logo „trzy fale” z napisem Tide, pozycje
// widoków z plakietkami z backendów, na dole railInfo bieżącego widoku (link i ramka, jeśli je podaje).
// Strzałki góra/dół przechodzą między pozycjami, Enter i spacja otwierają widok.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Rectangle {
    id: rail

    property var views: []
    property string current: ""
    property var shell: null
    property var info: null

    signal viewRequested(string id, string item)

    readonly property alias footer: footer  // dół paska (testy: link i ramka railInfo)

    implicitWidth: 200
    color: Tide.Tokens.backgroundView

    Accessible.role: Accessible.PageTabList
    Accessible.name: "Widoki Tide"

    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 12
        anchors.rightMargin: 13
        anchors.topMargin: 18
        anchors.bottomMargin: 18
        spacing: 4

        RowLayout {
            Layout.leftMargin: 10
            Layout.rightMargin: 10
            Layout.bottomMargin: 16
            spacing: 10
            TideWaves {
                Layout.preferredWidth: 26
                Layout.preferredHeight: 26
            }
            Text {
                text: "Tide"
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 17
                font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
            }
        }

        Repeater {
            id: items
            model: rail.views
            delegate: RailItem {
                required property var modelData
                required property int index
                readonly property var backend: rail.shell ? rail.shell.backend(modelData.id) : null
                Layout.fillWidth: true
                title: modelData.title
                icon: modelData.icon
                current: rail.current === modelData.id
                badgeText: backend && backend.badgeText ? backend.badgeText : ""
                badgeTone: backend && backend.badgeTone ? backend.badgeTone : "muted"
                onClicked: rail.viewRequested(modelData.id, "")
                Keys.onDownPressed: { const next = items.itemAt(index + 1); if (next) next.forceActiveFocus() }
                Keys.onUpPressed: { const prev = items.itemAt(index - 1); if (prev) prev.forceActiveFocus() }
            }
        }

        Item { Layout.fillHeight: true }

        RailInfo {
            id: footer
            Layout.fillWidth: true
            info: rail.info !== undefined ? rail.info : null
            onViewRequested: (id, item) => rail.viewRequested(id, item)
        }
    }
}

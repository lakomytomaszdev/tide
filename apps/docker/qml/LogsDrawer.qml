// Szuflada logów pod listą (mock „Tide Docker”): „Logi”, nazwa kontenera w sky, link „Otwórz w Konsoli
// (docker logs -f)” i ostatnie linie z Logs(id, 200) w Geist Mono; linie ze stderr w maśle. Lista
// przewija się na koniec, chyba że użytkownik czyta wyżej. Teksty z kontenera zawsze jako zwykły tekst.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide

Rectangle {
    id: drawer

    property string name: ""
    property var lines: []
    property string error: ""
    property bool loading: false
    signal openInKonsole()
    signal closeRequested()

    readonly property alias list: list

    implicitHeight: 236
    radius: Tide.Tokens.radiusTile
    color: Qt.darker(Tide.Tokens.backgroundDesktop, 1.18)
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)

    Accessible.role: Accessible.Pane
    Accessible.name: "Logi " + name

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 10
        anchors.topMargin: 12
        anchors.bottomMargin: 10
        spacing: 8

        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Text {
                text: "Logi"
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
                Accessible.role: Accessible.Heading
            }
            Text {
                Layout.fillWidth: true
                text: drawer.name + (drawer.loading ? "  ·  odświeżam…" : "")
                textFormat: Text.PlainText
                color: Tide.Tokens.done
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            LinkText {
                text: "Otwórz w Konsoli (docker logs -f)"
                onClicked: drawer.openInKonsole()
            }
            IconButton {
                implicitWidth: 24
                implicitHeight: 24
                icon: "close"
                name: "Zamknij logi"
                tint: Tide.Tokens.textMuted
                color: "transparent"
                onClicked: drawer.closeRequested()
            }
        }

        Text {
            objectName: "logsError"
            Layout.fillWidth: true
            visible: drawer.error !== ""
            text: drawer.error
            textFormat: Text.PlainText
            color: Tide.Tokens.negative
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            wrapMode: Text.WordWrap
        }
        Text {
            Layout.fillWidth: true
            visible: drawer.error === "" && drawer.lines.length === 0
            text: drawer.loading ? "Pobieram ostatnie linie…" : "Kontener nic nie wypisał."
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
        }

        ListView {
            id: list
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            model: drawer.lines
            boundsBehavior: Flickable.StopAtBounds
            property bool follow: true
            onMovementEnded: follow = atYEnd
            onCountChanged: if (follow) Qt.callLater(positionViewAtEnd)
            QQC2.ScrollBar.vertical: Tide.ScrollBar {}
            delegate: Text {
                required property var modelData
                width: ListView.view.width - 12
                text: modelData.text
                textFormat: Text.PlainText
                color: modelData.stderr ? Tide.Tokens.neutral : Tide.Tokens.textSecondary
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 12
                lineHeight: 1.35
                wrapMode: Text.WrapAnywhere
            }
        }
    }
}

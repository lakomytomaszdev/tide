// Widok, którego jeszcze nie ma (Tablica, Docker, Raporty): nagłówek jak w innych widokach i spokojny
// pusty stan pośrodku. Umowa widoku jak w SessionsView/WorktreesView; showItem nic nie robi.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Rectangle {
    id: view

    property var backend: null
    property bool active: true
    property var preview: ({})
    readonly property var railInfo: null
    property string title: ""
    property string icon: ""
    property string heading: ""
    property string text: ""

    function showItem(item) {}

    function takeFocus() {
        view.forceActiveFocus()
    }

    color: Tide.Tokens.backgroundWindow

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        anchors.topMargin: 20
        anchors.bottomMargin: 20
        spacing: 4

        Text {
            Layout.fillWidth: true
            text: view.title
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 21
            font.weight: Font.DemiBold
            Accessible.role: Accessible.Heading
        }
        Text {
            Layout.fillWidth: true
            text: "W przygotowaniu"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

            Rectangle {
                anchors.centerIn: parent
                anchors.verticalCenterOffset: -40
                width: Math.min(parent.width, 460)
                height: empty.implicitHeight + 56
                radius: Tide.Tokens.radiusCard
                color: Tide.Tokens.backgroundView
                border.width: 1
                border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)

                ColumnLayout {
                    id: empty
                    anchors.fill: parent
                    anchors.margins: 28
                    spacing: 10

                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        Layout.preferredWidth: 52
                        Layout.preferredHeight: 52
                        Layout.bottomMargin: 4
                        radius: Tide.Tokens.radiusTile
                        color: Tide.Theme.alpha(Tide.Tokens.accent, 0.10)
                        RailIcon {
                            anchors.centerIn: parent
                            width: 26
                            height: 26
                            name: view.icon
                            color: Tide.Tokens.accent
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        text: view.heading
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        wrapMode: Text.WordWrap
                        lineHeight: 1.2
                        text: view.text
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                    }
                }
            }
        }
    }
}

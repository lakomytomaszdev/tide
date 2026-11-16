import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import "tide" as Tide

QQC2.Popup {
    id: popup
    property string heading: ""
    property string message: ""
    signal accepted()
    anchors.centerIn: parent
    width: Math.min(540, parent.width - 32)
    padding: 22
    modal: true
    focus: true
    closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutside
    QQC2.Overlay.modal: Rectangle { color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.65) }
    background: Rectangle {
        color: Tide.Tokens.backgroundWindow
        radius: Tide.Tokens.radiusFrame
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.2)
    }
    contentItem: ColumnLayout {
        spacing: 16
        Label { Layout.fillWidth: true; text: popup.heading; font.pixelSize: 20; color: Tide.Tokens.textPrimary }
        Label { Layout.fillWidth: true; text: popup.message }
        RowLayout {
            Layout.alignment: Qt.AlignRight
            Tide.Button { text: "Anuluj"; onClicked: popup.close() }
            Tide.Button { text: "Potwierdź"; variant: "primary"; onClicked: { popup.close(); popup.accepted() } }
        }
    }
}

// Ciemna lista wyboru w szerokim widoku planu.
import QtQuick
import QtQuick.Controls as QQC2
import "tide" as Tide

QQC2.ComboBox {
    // Kółko myszy przewija panel, nie zmienia wyboru (03.11: przypadkowa zmiana wykonawcy).
    wheelEnabled: false
    id: control
    implicitHeight: 34
    font.family: Tide.Theme.uiFamily
    font.pixelSize: 12
    background: Rectangle {
        radius: 7
        color: Tide.Tokens.backgroundPanel
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, control.activeFocus ? 0.5 : 0.22)
    }
    contentItem: Text {
        leftPadding: 10
        rightPadding: 30
        text: control.displayText
        textFormat: Text.PlainText
        color: Tide.Tokens.textPrimary
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    indicator: Text {
        anchors.right: parent.right
        anchors.rightMargin: 11
        anchors.verticalCenter: parent.verticalCenter
        text: "⌄"
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.pixelSize: 17
    }
    delegate: QQC2.ItemDelegate {
        required property int index
        width: control.width
        height: 34
        contentItem: Text {
            text: control.textAt(index)
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font: control.font
            verticalAlignment: Text.AlignVCenter
            elide: Text.ElideRight
        }
        background: Rectangle {
            color: parent.hovered || parent.highlighted
                   ? Tide.Theme.alpha(Tide.Tokens.sky, 0.18) : Tide.Tokens.backgroundPanel
        }
    }
    popup: QQC2.Popup {
        y: control.height + 2
        width: control.width
        implicitHeight: Math.min(contentItem.implicitHeight, 250)
        padding: 4
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            QQC2.ScrollBar.vertical: Tide.ScrollBar {}
        }
        background: Rectangle {
            radius: 7
            color: Tide.Tokens.backgroundPanel
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.25)
        }
    }
}

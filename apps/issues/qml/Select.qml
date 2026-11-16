import QtQuick
import QtQuick.Controls as QQC2
import "tide" as Tide

QQC2.ComboBox {
    id: control
    textRole: "label"
    valueRole: "value"
    font.family: Tide.Theme.uiFamily
    font.pixelSize: 13
    padding: 10
    rightPadding: 26
    contentItem: Text {
        text: control.displayText
        textFormat: Text.PlainText
        color: Tide.Tokens.textPrimary
        font: control.font
        elide: Text.ElideRight
        verticalAlignment: Text.AlignVCenter
    }
    indicator: Text {
        x: control.width - width - 8
        anchors.verticalCenter: parent.verticalCenter
        text: "▾"
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
    }
    background: Rectangle {
        color: Tide.Tokens.backgroundCard
        radius: Tide.Tokens.radiusFrame
        border.width: 1
        border.color: control.activeFocus ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.16)
    }
    delegate: QQC2.ItemDelegate {
        width: control.width
        contentItem: Text {
            text: modelData.label
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font: control.font
            elide: Text.ElideRight
        }
        background: Rectangle {
            color: parent.highlighted ? Tide.Theme.alpha(Tide.Tokens.accent, 0.15) : Tide.Tokens.backgroundCard
        }
        highlighted: control.highlightedIndex === index
    }
    popup: QQC2.Popup {
        y: control.height
        width: control.width
        padding: 1
        implicitHeight: Math.min(contentItem.implicitHeight + 2, 300)
        background: Rectangle { color: Tide.Tokens.backgroundCard; border.color: Tide.Tokens.textMuted }
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            QQC2.ScrollBar.vertical: Tide.ScrollBar {}
        }
    }
}

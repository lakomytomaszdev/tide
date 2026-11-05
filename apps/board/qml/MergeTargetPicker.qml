import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Layouts
import "tide" as Tide

QQC2.Popup {
    id: menu
    objectName: "mergeTargetMenu"
    anchors.centerIn: parent
    width: Math.min(parent.width - 40, 900)
    height: Math.min(parent.height - 40, content.implicitHeight + 40)
    modal: true
    focus: true
    closePolicy: QQC2.Popup.CloseOnEscape
    property string cardId: ""
    property string contextKey: ""
    property string baseRef: ""
    property alias picker: picker
    signal requested(string text)
    signal previewRequested(var target)
    onClosed: picker.pendingTag = ""
    background: Rectangle {
        color: Tide.Tokens.backgroundPanel
        radius: Tide.Tokens.radiusCard
        border.color: Tide.Tokens.textMuted
    }
    contentItem: ColumnLayout {
        id: content
        spacing: 12
        BranchPicker {
            id: picker
            objectName: "mergeBranchPicker"
            Layout.fillWidth: true
            active: menu.visible
            contextKey: menu.contextKey
            baseRef: menu.baseRef
            purpose: "merge"
            onRequested: text => menu.requested(text)
        }
        Tide.TextButton {
            objectName: "mergeTargetPreview"
            text: "Podgląd wybranego celu"
            enabled: picker.ready
            onClicked: {
                const target = picker.selected
                menu.close()
                menu.previewRequested(target)
            }
        }
        Tide.TextButton {
            objectName: "mergeTargetCancel"
            text: "Anuluj"
            onClicked: menu.close()
        }
    }
}

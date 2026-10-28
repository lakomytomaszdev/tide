// Przełącznik kilku wartości w pastylce (mock: „Razem | Wiersz na projekt”): wybrana — tło akcentu
// ~0,16 i tekst w akcencie. options: [{value, label, disabled?, hint?}] — disabled: wyszarzona, bez wyboru
// (np. „⌂ Lokalny” bez dostępnego modelu), hint: podpowiedź po najechaniu (powód wyłączenia).
import QtQuick
import QtQuick.Controls as QQC2
import "tide" as Tide

Rectangle {
    id: segmented

    property var options: []
    property string current: ""
    property int pixelSize: 12
    property real itemHeight: 24
    signal picked(string value)

    implicitWidth: row.implicitWidth + 4
    implicitHeight: itemHeight + 4
    radius: height / 2
    color: Tide.Tokens.backgroundCard
    Accessible.role: Accessible.PageTabList

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 0
        Repeater {
            model: segmented.options
            Rectangle {
                id: option
                required property var modelData
                readonly property bool on: modelData.value === segmented.current
                readonly property bool off: modelData.disabled === true
                objectName: segmented.objectName ? segmented.objectName + "-" + modelData.value : ""
                implicitWidth: label.implicitWidth + 20
                implicitHeight: segmented.itemHeight
                radius: height / 2
                color: on ? Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                     : area.containsMouse && !off ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05) : "transparent"
                border.width: activeFocus ? 1 : 0
                border.color: Tide.Tokens.accent
                activeFocusOnTab: !off
                Accessible.role: Accessible.PageTab
                Accessible.name: modelData.label
                Accessible.description: off && modelData.hint ? modelData.hint : ""
                Accessible.selected: on
                Accessible.onPressAction: if (!off) segmented.picked(modelData.value)
                Keys.onSpacePressed: if (!off) segmented.picked(modelData.value)
                Keys.onReturnPressed: if (!off) segmented.picked(modelData.value)
                QQC2.ToolTip.visible: area.containsMouse && !!option.modelData.hint
                QQC2.ToolTip.text: option.modelData.hint || ""
                QQC2.ToolTip.delay: 300
                Text {
                    id: label
                    anchors.centerIn: parent
                    text: option.modelData.label
                    textFormat: Text.PlainText
                    color: option.off ? Tide.Tokens.textMuted : option.on ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: segmented.pixelSize
                    font.weight: option.on ? Font.DemiBold : Font.Normal
                }
                MouseArea {
                    id: area
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: option.off ? Qt.ArrowCursor : Qt.PointingHandCursor
                    onClicked: if (!option.off) segmented.picked(option.modelData.value)
                }
            }
        }
    }
}

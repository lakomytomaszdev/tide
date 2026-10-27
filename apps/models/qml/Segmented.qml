// Przełącznik kilku wartości w pastylce (mock: „Razem | Wiersz na projekt”): wybrana — tło akcentu
// ~0,16 i tekst w akcencie. options: [{value, label}]; current spoza opcji — nic nie jest wybrane.
import QtQuick
import "tide" as Tide

Rectangle {
    id: segmented

    property var options: []
    property string current: ""
    property int pixelSize: 12
    property real itemHeight: 24
    signal picked(string value)

    implicitWidth: row.implicitWidth + 4
    opacity: enabled ? 1 : 0.55
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
                implicitWidth: label.implicitWidth + 20
                implicitHeight: segmented.itemHeight
                radius: height / 2
                color: on ? Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                     : area.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05) : "transparent"
                border.width: activeFocus ? 1 : 0
                border.color: Tide.Tokens.accent
                activeFocusOnTab: true
                Accessible.role: Accessible.PageTab
                Accessible.name: modelData.label
                Accessible.selected: on
                Accessible.onPressAction: segmented.picked(modelData.value)
                Keys.onSpacePressed: segmented.picked(modelData.value)
                Keys.onReturnPressed: segmented.picked(modelData.value)
                Text {
                    id: label
                    anchors.centerIn: parent
                    text: option.modelData.label
                    textFormat: Text.PlainText
                    color: option.on ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: segmented.pixelSize
                    font.weight: option.on ? Font.DemiBold : Font.Normal
                }
                MouseArea {
                    id: area
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: segmented.picked(option.modelData.value)
                }
            }
        }
    }
}

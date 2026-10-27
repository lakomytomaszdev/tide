// Ramka na dole paska widoków, którą może podać widok (railInfo: {label, value, note}) —
// np. „Dane tablicy · ~/DevWorkspace/tide-board · lokalne repo · zapisano 12:04”.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Rectangle {
    id: box

    property var info: null

    implicitHeight: column.implicitHeight + 24
    radius: Tide.Tokens.radiusTile
    color: Tide.Tokens.backgroundWindow
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)

    ColumnLayout {
        id: column
        anchors.fill: parent
        anchors.margins: 12
        spacing: 4

        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: box.info && box.info.label ? box.info.label : ""
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
            elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: box.info && box.info.value ? box.info.value : ""
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.monoFamily
            font.pixelSize: 12
            elide: Text.ElideMiddle
        }
        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: box.info && box.info.note ? box.info.note : ""
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
            wrapMode: Text.WordWrap
        }
    }
}

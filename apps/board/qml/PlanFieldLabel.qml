// Podpis pola w szerokim widoku planu: nazwa i krótkie wyjaśnienie pod nią.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Column {
    property alias text: name.text
    property alias hint: hintText.text
    Layout.preferredWidth: 190
    Layout.alignment: Qt.AlignVCenter
    spacing: 1
    Text {
        id: name
        textFormat: Text.PlainText
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 13
        font.bold: true
    }
    Text {
        id: hintText
        width: parent.width
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 11
        wrapMode: Text.Wrap
    }
}

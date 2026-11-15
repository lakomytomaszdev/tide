// Nazwana sekcja podglądu scalenia (K-99, PT-403): tytuł i lista wierszy zwykłego tekstu, przewijana w obrębie sekcji
// (wysokość listy ma górną granicę), więc długa lista commitów albo plików nie wypycha reszty okna. Sekcja bez wierszy
// jest niewidoczna. Wiersze to gotowe napisy (komponent nie zna formatu odpowiedzi usługi); teksty z gita są zwykłym
// tekstem (Text.PlainText).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide

ColumnLayout {
    id: section

    property string title: ""
    property var rows: []                      // napisy wierszy
    property color rowColor: Tide.Tokens.textPrimary
    property color titleColor: Tide.Tokens.textSecondary
    property real maxListHeight: 160           // powyżej tej wysokości lista przewija się wewnątrz sekcji
    readonly property int rowCount: Array.isArray(rows) ? rows.length : 0
    property alias list: list

    visible: rowCount > 0
    spacing: 4

    Text {
        objectName: section.objectName + "Title"
        Layout.fillWidth: true
        text: section.title
        textFormat: Text.PlainText
        color: section.titleColor
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        font.weight: Font.DemiBold
        wrapMode: Text.Wrap
    }

    ListView {
        id: list
        objectName: section.objectName + "List"
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(contentHeight, section.maxListHeight)
        clip: true
        model: section.rows
        boundsBehavior: Flickable.StopAtBounds
        QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: list.contentHeight > list.height ? QQC2.ScrollBar.AlwaysOn : QQC2.ScrollBar.AlwaysOff }

        delegate: Text {
            required property var modelData
            width: ListView.view.width - 12
            text: String(modelData)
            textFormat: Text.PlainText
            color: section.rowColor
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            wrapMode: Text.WrapAnywhere
        }
    }
}

// Sekcja szczegółów ticketu: mały nagłówek wersalikami („CO TO JEST”, „CO ZROBIONO”, „TERAZ / REVIEW”),
// pełny tekst opisu (zwykły tekst, bez przycinania) i opcjonalny drobny dopisek pod spodem.
// heading — tekst jak tytuł (17 px, półgruby); tint — nazwa tokenu palety dla tekstu (np. peach).
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

ColumnLayout {
    id: section

    property string label: ""
    property string text: ""
    property string hint: ""
    property bool heading: false
    property string tint: ""

    spacing: 4

    Text {
        text: section.label.toUpperCase()
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 11
        font.weight: Font.DemiBold
        font.letterSpacing: 0.8
        Accessible.role: Accessible.Heading
    }
    Text {
        Layout.fillWidth: true
        text: section.text
        textFormat: Text.PlainText
        color: section.tint !== "" ? Tide.Tokens[section.tint]
             : section.heading ? Tide.Tokens.textPrimary : Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: section.heading ? 17 : 13
        font.weight: section.heading ? Font.DemiBold : Font.Normal
        lineHeight: section.heading ? 1.08 : 1.15
        wrapMode: Text.Wrap
    }
    Text {
        Layout.fillWidth: true
        visible: section.hint !== ""
        text: section.hint
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 11
        wrapMode: Text.Wrap
        maximumLineCount: 3
        elide: Text.ElideRight
    }
}

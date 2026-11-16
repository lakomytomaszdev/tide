// Plakietka przy możliwości decyzji (K-128, PT-506; makieta S-9 `.chip.teal` i `.chip.lav`): tekst w kolorze `tint`
// na tle tego koloru o kryciu 0,10 z obrysem 0,35. „polecane” — teal, „opiekun” — lawenda.
import QtQuick
import "../tide" as Tide

Rectangle {
    id: badge

    property string label: ""
    property color tint: Tide.Tokens.teal
    readonly property color ink: tint

    implicitWidth: badgeText.implicitWidth + 16
    implicitHeight: badgeText.implicitHeight + 4
    radius: height / 2
    color: Tide.Theme.alpha(tint, 0.10)
    border.width: 1
    border.color: Tide.Theme.alpha(tint, 0.35)
    Accessible.role: Accessible.StaticText
    Accessible.name: label

    Text {
        id: badgeText
        anchors.centerIn: parent
        text: badge.label
        textFormat: Text.PlainText
        color: badge.ink
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 0.85
        font.weight: Font.Medium
    }
}

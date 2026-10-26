// Kafelek paska bocznego Tide (lewy monitor): osobna karta Mist — tło `background.card`, ramka
// 1 px w kolorze tekstu o kryciu `opacity.border`, promień karty — z małym nagłówkiem i zawartością
// pod nim. Każdy widżet w pasku to jeden taki kafelek.
//
// Przerwa między kafelkami (gap) jest częścią kafelka: po połowie nad i pod kartą. Widżety leżą
// w panelu jeden pod drugim i nic o sobie nie wiedzą, a przerwy i tak są równe. Panel dokłada
// między apletami swoje 4 px (zmierzone w zagnieżdżonej sesji), razem ok. 14 px.
import QtQuick
import QtQuick.Layouts

Item {
    id: tile

    property string title: ""
    property real padding: 14
    property real gap: 10
    property alias spacing: body.spacing
    default property alias content: body.data
    // Elementy po prawej stronie nagłówka (krótkie podsumowanie, mały przycisk).
    property alias headerItems: extras.data

    implicitWidth: Math.ceil(body.implicitWidth + padding * 2)
    implicitHeight: Math.ceil(body.implicitHeight + padding * 2 + gap)

    Accessible.role: Accessible.Grouping
    Accessible.name: title

    Rectangle {
        id: card
        anchors.fill: parent
        anchors.topMargin: tile.gap / 2
        anchors.bottomMargin: tile.gap / 2
        radius: Tokens.radiusCard
        color: Tokens.backgroundCard
        border.width: 1
        border.color: Theme.alpha(Tokens.textPrimary, Tokens.opacityBorder)
    }

    ColumnLayout {
        id: body
        x: tile.padding
        y: tile.gap / 2 + tile.padding
        width: tile.width - tile.padding * 2
        spacing: 10

        RowLayout {
            Layout.fillWidth: true
            visible: tile.title !== ""
            spacing: 8

            Text {
                Layout.fillWidth: true
                text: tile.title
                color: Tokens.textSecondary
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 1
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }
            RowLayout {
                id: extras
                spacing: 8
            }
        }
    }
}

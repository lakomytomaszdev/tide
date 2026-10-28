// Lista profili graczy z rdzenia Streamer (`profiles` w stanie) do wyboru (PT-37): bieżący wyróżniony kropką
// i obwódką w kolorze akcentu, pod gamertagiem nazwa profilu. Wybór to sygnał picked(id) — widżet wysyła
// profile.set przez CoreSource. Popup kafelka „Profil gracza” i karta Stream w konsoli streamu.
import QtQuick
import QtQuick.Layouts
import "stream.js" as Stream

ColumnLayout {
    id: list

    property var view: null
    property real pointSize: Tokens.fontSizePt
    signal picked(string id)

    readonly property var items: Stream.profiles(view)

    spacing: 4

    Repeater {
        model: list.items
        TextButton {
            id: item
            required property var modelData
            objectName: "profile-" + modelData.id
            Layout.fillWidth: true
            variant: "outline"
            topPadding: 11
            bottomPadding: 11
            leftPadding: 12
            Accessible.name: "Profil gracza " + modelData.title + (modelData.current ? ", wybrany" : "")
            onClicked: if (!modelData.current) list.picked(modelData.id)
            contentItem: Row {
                spacing: 8
                Rectangle {
                    anchors.verticalCenter: parent.verticalCenter
                    width: 8
                    height: 8
                    radius: width / 2
                    color: item.modelData.current ? Tokens.accent : "transparent"
                    border.width: item.modelData.current ? 0 : 1
                    border.color: Tokens.textMuted
                }
                Text {
                    text: item.modelData.title
                    textFormat: Text.PlainText
                    color: Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: list.pointSize
                    font.weight: item.modelData.current ? Font.DemiBold : Font.Normal
                }
                Text {
                    visible: item.modelData.detail !== ""
                    text: "· " + item.modelData.detail
                    textFormat: Text.PlainText
                    color: Tokens.textMuted
                    font.family: Theme.uiFamily
                    font.pointSize: list.pointSize
                }
            }
            background: Rectangle {
                radius: Tokens.radiusFrame
                color: item.modelData.current ? Theme.alpha(Tokens.accent, 0.12)
                     : Theme.alpha(Tokens.textPrimary, item.lit ? 0.07 : 0)
                border.width: 1
                border.color: item.modelData.current ? Theme.alpha(Tokens.accent, 0.75)
                            : item.visualFocus ? Tokens.accent : "transparent"
            }
        }
    }

    Text {
        visible: list.items.length === 0
        text: "Rdzeń nie podał profili graczy"
        textFormat: Text.PlainText
        color: Tokens.textMuted
        font.family: Theme.uiFamily
        font.pointSize: list.pointSize
    }
}

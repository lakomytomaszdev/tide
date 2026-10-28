// Czat Twitcha na pulpicie lewego monitora w aktywności Stream (PT-37): duża czcionka (ok. 20 px), najnowsze
// na dole, tylko do czytania, alerty (follow, sub, bits, raid) w linii w ramce w kolorze akcentu. Nick w pastelu
// palety wybranym po nicku (Stream.nickTint) spośród pasteli innych niż kolory ostrzeżenia i błędu palety.
// Rdzeń nie daje jeszcze czatu przez D-Bus: wtedy spokojne „jeszcze niedostępny w rdzeniu”; wiersze to ListModel
// z CoreSource (Stream.chatLine z komunikatów w kształcie WebSocketu rdzenia; append i remove(0), bez przebudowy
// listy), więc po dodaniu GetChat i sygnału Chat widok się nie zmienia. Bez PlasmoidItem (podgląd w
// widgets/preview_stream.py).
import QtQuick
import QtQuick.Layouts
import "stream.js" as Stream

Rectangle {
    id: chat

    property var model: null              // ListModel {kind, id, user, text} z CoreSource.chatModel
    property bool available: false
    property bool coreUp: false
    property real messagePointSize: Tokens.fontSizePt * 1.5

    // Pastele palety na nicki, bez kolorów ostrzeżenia i błędu (semantic neutral i negative).
    readonly property var nickTints: Stream.nickTints(
        Stream.NICK_TINTS.reduce((all, name) => { all[name] = String(Tokens[name]); return all }, {}),
        [String(Tokens.neutral), String(Tokens.negative)])

    radius: Tokens.radiusCard
    color: Tokens.backgroundCard
    border.width: 1
    border.color: Theme.alpha(Tokens.textPrimary, 0.06)

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 12

        RowLayout {
            Layout.fillWidth: true
            Text {
                text: "Czat · Twitch"
                textFormat: Text.PlainText
                color: Tokens.textPrimary
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt + 3
                font.weight: Font.DemiBold
            }
            Item { Layout.fillWidth: true }
            Text {
                text: "najnowsze na dole · tylko do czytania"
                textFormat: Text.PlainText
                color: Tokens.textMuted
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 1
            }
        }

        // Najnowsze na dole: krótka lista przylega do dołu, dłuższa trzyma się końca, dopóki nie przewiniesz jej w górę.
        Item {
            visible: chat.available
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true

            ListView {
                id: list
                objectName: "chatList"
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                height: Math.min(parent.height, contentHeight)
                spacing: 12
                interactive: true
                model: chat.model
                // Na koniec tylko wtedy, gdy lista była na końcu — przewiniętej w górę (czytanie starszych) nie
                // szarpiemy przy każdej nowej wiadomości.
                property bool followEnd: true
                onMovementEnded: followEnd = atYEnd
                onCountChanged: if (followEnd) Qt.callLater(positionViewAtEnd)
                onHeightChanged: if (followEnd) Qt.callLater(positionViewAtEnd)
                delegate: Item {
                    id: entry
                    required property string kind
                    required property string user
                    required property string text
                    readonly property var modelData: ({ kind: kind, user: user, text: text,
                                                        tint: Stream.nickTint(user, chat.nickTints) })
                    readonly property bool alert: kind === "alert"
                    width: ListView.view.width
                    implicitHeight: alert ? alertBox.implicitHeight : message.implicitHeight

                    Text {
                        id: message
                        visible: !entry.alert
                        width: parent.width
                        text: entry.alert ? "" : Stream.chatHtml(entry.modelData, String(Tokens[entry.modelData.tint]))
                        textFormat: Text.StyledText
                        wrapMode: Text.Wrap
                        color: Tokens.textPrimary
                        font.family: Theme.uiFamily
                        font.pointSize: chat.messagePointSize
                    }

                    Rectangle {
                        id: alertBox
                        visible: entry.alert
                        width: parent.width
                        implicitHeight: alertText.implicitHeight + 20
                        radius: Tokens.radiusFrame
                        color: Theme.alpha(Tokens.accent, 0.14)
                        border.width: 1
                        border.color: Theme.alpha(Tokens.accent, 0.75)
                        Text {
                            id: alertText
                            anchors.verticalCenter: parent.verticalCenter
                            x: 14
                            width: parent.width - 28
                            text: entry.modelData.text
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            color: Tokens.textPrimary
                            font.family: Theme.uiFamily
                            font.pointSize: chat.messagePointSize - 2
                            font.weight: Font.Medium
                        }
                    }
                }
            }
        }

        Item {
            visible: !chat.available
            Layout.fillWidth: true
            Layout.fillHeight: true
            Column {
                anchors.centerIn: parent
                width: parent.width - 40
                spacing: 8
                Text {
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    text: chat.coreUp ? Stream.UNAVAILABLE.chat : "Rdzeń Streamer nie działa"
                    textFormat: Text.PlainText
                    color: chat.coreUp ? Tokens.textMuted : Tokens.neutral
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt + 2
                }
                Text {
                    visible: chat.coreUp
                    width: parent.width
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.Wrap
                    text: "Wiadomości i alerty pojawią się tutaj, gdy rdzeń zacznie je wysyłać przez D-Bus."
                    textFormat: Text.PlainText
                    color: Tokens.textMuted
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt - 1
                }
            }
        }
    }
}

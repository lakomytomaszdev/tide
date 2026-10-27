// Karta ticketu na tablicy: ID (sky, Geist Mono — tylko prawdziwe ID), tytuł po ludzku (do trzech linii),
// jedna linia „co zrobiono” albo „co teraz” (opis z describe.py), działający podagenci z rolą po polsku
// i czasem, a na dole stan słowami („✓ gotowe, review OK”, „gotowe, czeka na review”, „w trakcie”).
// Zaznaczona — tealowa obwódka; czekająca — brzoskwiniowe tło; gotowa — przygaszona.
// Teksty to dane: zawsze Text.PlainText.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "sessions.js" as Logic

Rectangle {
    id: card

    property var ticket: ({})
    property bool selected: false
    property real nowTs: Date.now() / 1000
    signal clicked()

    readonly property bool waiting: ticket.state === "czeka"
    readonly property bool finished: ticket.state === "gotowe" || ticket.state === "odrzucone"
    readonly property string line: Logic.cardLine(ticket)
    readonly property string stateLine: Logic.stateLine(ticket)
    readonly property var running: Logic.runningAgents(ticket)

    implicitHeight: body.implicitHeight + 20
    radius: 12
    color: waiting ? Tide.Theme.tinted(Tide.Tokens.backgroundCard, Tide.Tokens.peach, 0.07)
         : area.containsMouse ? Qt.lighter(Tide.Tokens.backgroundCard, 1.06) : Tide.Tokens.backgroundCard
    border.width: 1
    border.color: selected ? Tide.Theme.alpha(Tide.Tokens.accent, 0.55)
                : waiting ? Tide.Theme.alpha(Tide.Tokens.peach, 0.30) : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07)
    opacity: finished && !selected ? 0.78 : 1
    activeFocusOnTab: true

    Accessible.role: Accessible.Button
    Accessible.name: (ticket.id || "") + " " + (ticket.title || "") + ", " + (stateLine || ticket.word || "")
    Accessible.description: line
    Accessible.onPressAction: card.clicked()
    Keys.onReturnPressed: card.clicked()
    Keys.onSpacePressed: card.clicked()

    // Delikatna poświata zaznaczenia jak w mocku.
    Rectangle {
        visible: card.selected
        anchors.fill: parent
        anchors.margins: -3
        radius: card.radius + 3
        color: "transparent"
        border.width: 3
        border.color: Tide.Theme.alpha(Tide.Tokens.accent, 0.08)
    }

    ColumnLayout {
        id: body
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 11
        anchors.topMargin: 10
        spacing: 5

        Text {
            visible: (card.ticket.id || "") !== ""
            text: card.ticket.id || ""
            textFormat: Text.PlainText
            color: Tide.Tokens.sky
            font.family: Tide.Theme.monoFamily
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            text: card.ticket.title || ""
            textFormat: Text.PlainText
            color: card.ticket.state === "todo" ? Tide.Tokens.textSecondary : Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
            font.weight: Font.Medium
            lineHeight: 1.15
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            visible: card.line !== ""
            text: card.line
            textFormat: Text.PlainText
            color: card.waiting ? Tide.Tokens.peach : Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            lineHeight: 1.1
            wrapMode: Text.Wrap
            maximumLineCount: card.waiting ? 3 : 2
            elide: Text.ElideRight
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.topMargin: 2
            spacing: 4
            visible: card.running.length > 0
            Repeater {
                model: card.running
                RowLayout {
                    required property var modelData
                    Layout.fillWidth: true
                    spacing: 7
                    Dot {
                        Layout.alignment: Qt.AlignTop
                        Layout.topMargin: 5
                        tint: Logic.agentTint(modelData, card.ticket.state)
                        hollow: Logic.agentHollow(modelData)
                        pulse: true
                        size: 7
                    }
                    // Rola i czas w jednym tekście — w wąskiej kolumnie zawija się na granicy słów
                    // („implementacja ·” / „30 min”), zamiast łamać słowo.
                    Text {
                        Layout.fillWidth: true
                        text: Logic.agentWord(modelData) + " · " + Logic.agentTime(modelData, card.nowTs)
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        font.weight: Font.DemiBold
                        wrapMode: Text.WordWrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                }
            }
        }
        Text {
            Layout.fillWidth: true
            Layout.topMargin: 1
            visible: card.stateLine !== ""
            text: card.stateLine
            textFormat: Text.PlainText
            color: Tide.Tokens[Logic.stateTint(card.ticket)]
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            elide: Text.ElideRight
        }
    }

    MouseArea {
        id: area
        anchors.fill: parent
        hoverEnabled: true
        cursorShape: Qt.PointingHandCursor
        onClicked: card.clicked()
    }
}

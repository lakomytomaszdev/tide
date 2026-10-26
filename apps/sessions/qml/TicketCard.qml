// Karta ticketu na tablicy: ID (sky, Geist Mono), tytuł, do trzech ostatnich podagentów z czasem,
// a niżej — powód czekania (brzoskwinia), „w planie · bez agenta” albo „✓ review OK”.
// Zaznaczona — tealowa obwódka; czekająca — brzoskwiniowe tło; gotowa — przygaszona.
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
    readonly property string footer: Logic.cardFooter(ticket)

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
    Accessible.name: (ticket.id || "") + " " + (ticket.title || "") + ", " + (ticket.word || "")
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
        spacing: 6

        Text {
            visible: (card.ticket.id || "") !== ""
            text: card.ticket.id || ""
            color: Tide.Tokens.sky
            font.family: Tide.Theme.monoFamily
            font.pixelSize: 12
            font.weight: Font.DemiBold
        }
        Text {
            Layout.fillWidth: true
            text: card.ticket.title || ""
            color: card.ticket.state === "todo" ? Tide.Tokens.textSecondary : Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 13
            lineHeight: 1.15
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }
        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4
            visible: !card.finished && (card.ticket.agents || []).length > 0
            Repeater {
                model: card.finished ? [] : (card.ticket.agents || [])
                RowLayout {
                    required property var modelData
                    readonly property bool running: modelData.state === "running"
                    Layout.fillWidth: true
                    spacing: 7
                    Dot {
                        tint: Logic.agentTint(modelData, card.ticket.state)
                        hollow: Logic.agentHollow(modelData)
                        size: 7
                    }
                    Text {
                        Layout.fillWidth: true
                        text: modelData.label + (modelData.note && running ? " · " + modelData.note : "")
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        font.weight: running ? Font.DemiBold : Font.Normal
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                    Text {
                        text: Logic.agentTime(modelData, card.nowTs)
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 11
                    }
                }
            }
        }
        Text {
            Layout.fillWidth: true
            visible: card.waiting && (card.ticket.note || "") !== ""
            text: card.ticket.note || ""
            color: Tide.Tokens.peach
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            wrapMode: Text.Wrap
            maximumLineCount: 4
            elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            visible: card.footer !== ""
            text: card.footer
            color: card.ticket.state === "gotowe" ? Tide.Tokens.mint : Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            visible: !card.waiting && !card.finished && card.ticket.state !== "todo" && (card.ticket.note || "") !== ""
            text: card.ticket.note || ""
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            wrapMode: Text.Wrap
            maximumLineCount: 2
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

// Kafelek ticketu z sekcji „Plan” pełnej karty (PT-332): numer, tytuł (do 3 linii), stan w kolorze stanu i
// „wykonawca → recenzent”; obwódka w kolorze brzoskwini przy „czeka na Ciebie”. Wspólny dla Tablicy i Sesji
// (przez CardFullView). Niczego nie liczy: stan, etykieta i osoby przychodzą z usługi, a podpis stanu `merged`
// daje ticket.js.
//
// Umowa: `item` (`derived.tickets[]` albo null), `ticketId`; `ticketSettingsEnabled` (domyślnie false; true tylko
// w Tablicy, w Sesjach nie ma katalogu wykonawców) pokazuje przycisk „Ustawienia” i sygnał
// `ticketSettingsRequested(ticketId)`. Przycisk jest na każdym kafelku, także na kafelku jedynego ticketu karty
// „Od razu” (PT-341); co okno z nim zrobi (ticket nieruszony, trwający, scalony albo odrzucony), rozstrzyga okno
// ustawień na podstawie wiersza planu.
import QtQuick
import QtQuick.Layouts
import "../tide" as Tide
import "ticket.js" as TicketLib

Rectangle {
    id: tile

    property var item: null
    property string ticketId: ""
    property bool ticketSettingsEnabled: false

    readonly property string status: item ? item.status || "" : ""
    readonly property string statusLabel: TicketLib.statusLabel(item)

    signal ticketSettingsRequested(string ticketId)

    function statusTone(state) {
        return state === "merged" ? Tide.Tokens.mint : state === "running" || state === "fix" ? Tide.Tokens.teal
             : state === "review" || state === "merging" ? Tide.Tokens.lavender : state === "you" ? Tide.Tokens.peach
             : state === "limit" ? Tide.Tokens.butter : state === "rejected" ? Tide.Tokens.rose : Tide.Tokens.textMuted
    }

    objectName: "fullTicket"
    implicitHeight: tileColumn.implicitHeight + 18
    radius: Tide.Tokens.radiusTile
    color: Tide.Tokens.backgroundCard
    border.width: 1
    border.color: status === "you" ? Tide.Theme.alpha(Tide.Tokens.peach, 0.45)
                                   : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
    Accessible.role: Accessible.ListItem
    Accessible.name: ticketId + ": " + (item ? item.title : "") + " — " + statusLabel

    ColumnLayout {
        id: tileColumn
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 9
        anchors.leftMargin: 11
        spacing: 4
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Text {
                Layout.fillWidth: true
                text: tile.ticketId
                textFormat: Text.PlainText
                color: Tide.Tokens.sky
                font.family: Tide.Theme.monoFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.85
                font.weight: Font.DemiBold
            }
            Rectangle {
                id: settingsButton
                objectName: "fullTicketSettings"
                visible: tile.ticketSettingsEnabled
                implicitWidth: settingsLabel.implicitWidth + 16
                implicitHeight: settingsLabel.implicitHeight + 8
                radius: Tide.Tokens.radiusSmall
                color: settingsArea.containsMouse || activeFocus ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08) : "transparent"
                border.width: 1
                border.color: activeFocus ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.2)
                activeFocusOnTab: true
                Accessible.role: Accessible.Button
                Accessible.name: "Ustawienia ticketu " + tile.ticketId
                Accessible.onPressAction: tile.ticketSettingsRequested(tile.ticketId)
                Keys.onReturnPressed: tile.ticketSettingsRequested(tile.ticketId)
                Keys.onSpacePressed: tile.ticketSettingsRequested(tile.ticketId)
                Text {
                    id: settingsLabel
                    anchors.centerIn: parent
                    text: "Ustawienia"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.8
                }
                MouseArea {
                    id: settingsArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        settingsButton.forceActiveFocus()
                        tile.ticketSettingsRequested(tile.ticketId)
                    }
                }
            }
        }
        Text {
            Layout.fillWidth: true
            text: tile.item ? tile.item.title : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt * 0.95
            wrapMode: Text.Wrap
            maximumLineCount: 3
            elide: Text.ElideRight
        }
        Text {
            objectName: "fullTicketStatus"
            Layout.fillWidth: true
            text: tile.statusLabel
            textFormat: Text.PlainText
            color: tile.statusTone(tile.status)
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt * 0.85
            font.weight: Font.Medium
            elide: Text.ElideRight
        }
        Text {
            Layout.fillWidth: true
            visible: text !== ""
            text: tile.item ? [tile.item.executor, tile.item.reviewer].filter(x => !!x).join(" → ") : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt * 0.8
            elide: Text.ElideRight
        }
    }
}

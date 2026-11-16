// Plan karty jako tabela (K-128, PT-503, makieta S-9 „Plan karty”, wariant C): jeden ticket w wierszu, kolumny Fala,
// Ticket, Tytuł, Stan, Wykonawca → recenzent i ⋯ (ustawienia ticketu, tylko gdy `ticketSettingsEnabled`, czyli na
// Tablicy). Dane z tych samych pól co dawne kafelki fal: `derived.tickets[]` (`status`, `statusLabel`, `executor`,
// `reviewer`), `flow.plan.waves` i `flow.plan.rows[].note`; do tego pytanie ticketu czekającego z `flow.decisions[]`.
//
// Bieżąca fala (ticket.js `currentWave`: najniższa fala z ticketem w stanie innym niż gotowe/odrzucone) jest
// przypięta pod nagłówkiem tabeli: jej wiersze mają plakietkę „teraz” i tło brzoskwini ~0,07, a blok zajmuje najwyżej
// 40% wysokości tabeli (więcej — własne przewijanie). Wiersze przypięte nie powtarzają się niżej. Gdy wszystkie fale
// są zakończone, nic nie jest przypięte, a tabela zaczyna od pierwszej fali.
//
// Rozmiar: minimalna szerokość 720 px (węższy widok przewija tabelę w poziomie, `Tide.ScrollBar`); wysokość to
// `maxHeight` z własnym przewijaniem pionowym albo — przy `fill` — `availableHeight` (zakładka Plan). Numer ticketu
// ma stałą kolumnę i skracanie, więc nie nachodzi na sąsiednie elementy. Ticket spoza karty (`item` null) jest
// przygaszony. Niczego nie liczy poza wyborem przez ticket.js: stan i osoby przychodzą z usługi.
//
// Umowa: `waves`, `tickets`, `planRows`, `decisions`, `ticketSettingsEnabled`, sygnał `ticketSettingsRequested(id)`.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide
import "ticket.js" as TicketLib

Item {
    id: table
    objectName: "fullPlanTable"

    property var waves: []
    property var tickets: []
    property var planRows: []
    property var decisions: []
    property bool ticketSettingsEnabled: false
    // Zakładka Plan: tabela zajmuje `availableHeight`; w Przeglądzie najwyżej `maxHeight`.
    property bool fill: false
    property real availableHeight: 0
    property real maxHeight: 420
    readonly property real minTableWidth: 720

    readonly property var rows: TicketLib.planRows(waves, tickets, planRows, decisions)
    readonly property var pinnedRows: rows.filter(r => r.current)
    readonly property var otherRows: rows.filter(r => !r.current)
    readonly property bool wide: table.width < minTableWidth
    readonly property real barSpace: wide ? Tide.Tokens.scrollbarWidth + 4 : 0
    readonly property real headerHeight: 30
    readonly property real pinnedNatural: pinnedRows.length > 0 ? pinnedColumn.implicitHeight : 0
    readonly property real naturalHeight: headerHeight + pinnedNatural + otherColumn.implicitHeight + barSpace
    // Przypięty blok: najwyżej 40% wysokości tabeli, więcej tylko gdy reszta tabeli i tak nie wypełniłaby miejsca
    // (np. cały plan to jedna fala); dłuższy blok przewija się sam.
    readonly property real pinnedHeight: Math.min(pinnedNatural,
        Math.max(table.height * 0.4, table.height - headerHeight - barSpace - otherColumn.implicitHeight))
    readonly property alias pinnedFlick: pinnedFlick
    readonly property alias bodyFlick: bodyFlick

    // Kolumny (te same w nagłówku i wierszach).
    readonly property real waveWidth: 64
    readonly property real ticketWidth: 92
    readonly property real stateWidth: 140
    readonly property real peopleWidth: 176
    readonly property real actionWidth: 34
    readonly property real gap: 10
    readonly property real sidePadding: 12

    signal ticketSettingsRequested(string ticketId)

    implicitHeight: fill ? Math.max(availableHeight, 200) : Math.min(maxHeight, naturalHeight)
    Accessible.role: Accessible.Table
    Accessible.name: "Plan karty"

    function statusTone(state) {
        return state === "merged" ? Tide.Tokens.mint : state === "running" || state === "fix" ? Tide.Tokens.teal
             : state === "review" || state === "merging" ? Tide.Tokens.lavender : state === "you" ? Tide.Tokens.peach
             : state === "limit" || state === "card-limit" ? Tide.Tokens.butter : state === "rejected" ? Tide.Tokens.rose : Tide.Tokens.textMuted
    }

    // Jeden wiersz: obiekt `fullTicket` z polami dawnego kafelka (ticketId, item, status, statusLabel), by testy i
    // czytniki ekranu znajdowały ticket tak samo jak wcześniej.
    component PlanRow: Rectangle {
        id: row
        required property var modelData
        objectName: "fullTicket"
        readonly property string ticketId: modelData.id
        readonly property var item: modelData.item
        readonly property int wave: modelData.wave
        readonly property bool current: modelData.current
        readonly property string status: item ? item.status || "" : ""
        readonly property string statusLabel: TicketLib.statusLabel(item)
        readonly property alias statusText: statusLabelItem
        width: parent ? parent.width : 0
        implicitHeight: rowLayout.implicitHeight + 16
        color: current ? Tide.Theme.alpha(Tide.Tokens.peach, 0.07) : "transparent"
        opacity: item ? 1 : 0.55
        Accessible.role: Accessible.Row
        Accessible.name: ticketId + ": " + modelData.title + " — " + statusLabel

        Rectangle {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            height: 1
            color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
        }

        RowLayout {
            id: rowLayout
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.leftMargin: table.sidePadding
            anchors.rightMargin: table.sidePadding + Tide.Tokens.scrollbarWidth
            anchors.topMargin: 8
            spacing: table.gap

            ColumnLayout {
                Layout.preferredWidth: table.waveWidth
                Layout.minimumWidth: table.waveWidth
                Layout.maximumWidth: table.waveWidth
                Layout.alignment: Qt.AlignTop
                spacing: 3
                Text {
                    objectName: "fullPlanWave"
                    Layout.fillWidth: true
                    text: "Fala " + row.wave
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.9
                    elide: Text.ElideRight
                }
                Rectangle {
                    objectName: "fullPlanNow"
                    visible: row.current
                    implicitWidth: nowLabel.implicitWidth + 12
                    implicitHeight: nowLabel.implicitHeight + 4
                    radius: height / 2
                    color: Tide.Theme.alpha(Tide.Tokens.peach, 0.18)
                    Text {
                        id: nowLabel
                        anchors.centerIn: parent
                        text: "teraz"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.peach
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.78
                        font.weight: Font.DemiBold
                    }
                }
            }

            Text {
                objectName: "fullPlanTicket"
                Layout.preferredWidth: table.ticketWidth
                Layout.minimumWidth: table.ticketWidth
                Layout.maximumWidth: table.ticketWidth
                Layout.alignment: Qt.AlignTop
                text: row.ticketId
                textFormat: Text.PlainText
                color: Tide.Tokens.sky
                font.family: Tide.Theme.monoFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.85
                font.weight: Font.DemiBold
                elide: Text.ElideRight
            }

            ColumnLayout {
                objectName: "fullPlanTitleColumn"
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: 3
                Text {
                    objectName: "fullPlanTitle"
                    Layout.fillWidth: true
                    text: row.modelData.title
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.95
                    wrapMode: Text.Wrap
                }
                Text {
                    objectName: "fullPlanNote"
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: row.modelData.note
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.85
                    wrapMode: Text.Wrap
                }
                Text {
                    objectName: "fullPlanQuestion"
                    Layout.fillWidth: true
                    visible: text !== ""
                    text: row.modelData.question
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.85
                    font.italic: true
                    wrapMode: Text.Wrap
                }
            }

            Item {
                Layout.preferredWidth: table.stateWidth
                Layout.minimumWidth: table.stateWidth
                Layout.maximumWidth: table.stateWidth
                Layout.alignment: Qt.AlignTop
                implicitHeight: statusChip.height
                Rectangle {
                    id: statusChip
                    width: Math.min(parent.width, statusLabelItem.implicitWidth + 16)
                    height: statusLabelItem.height + 6
                    radius: height / 2
                    color: Tide.Theme.alpha(table.statusTone(row.status), 0.14)
                    Text {
                        id: statusLabelItem
                        objectName: "fullTicketStatus"
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 8
                        text: row.statusLabel
                        textFormat: Text.PlainText
                        color: table.statusTone(row.status)
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.85
                        font.weight: Font.Medium
                        wrapMode: Text.Wrap
                    }
                }
            }

            Text {
                objectName: "fullPlanPeople"
                Layout.preferredWidth: table.peopleWidth
                Layout.minimumWidth: table.peopleWidth
                Layout.maximumWidth: table.peopleWidth
                Layout.alignment: Qt.AlignTop
                text: row.item ? [row.item.executor, row.item.reviewer].filter(x => !!x).join(" → ") : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.85
                wrapMode: Text.Wrap
            }

            Item {
                Layout.preferredWidth: table.actionWidth
                Layout.minimumWidth: table.actionWidth
                Layout.maximumWidth: table.actionWidth
                Layout.alignment: Qt.AlignTop
                implicitHeight: 26
                Tide.Button {
                    objectName: "fullTicketSettings"
                    visible: table.ticketSettingsEnabled
                    anchors.right: parent.right
                    text: "⋯"
                    iconOnly: true
                    small: true
                    variant: "ghost"
                    toolTip: "Ustawienia ticketu " + row.ticketId
                    Accessible.name: "Ustawienia ticketu " + row.ticketId
                    onClicked: table.ticketSettingsRequested(row.ticketId)
                }
            }
        }
    }

    Flickable {
        id: sheetFlick
        objectName: "fullPlanScroll"
        anchors.fill: parent
        contentWidth: Math.max(table.width, table.minTableWidth)
        contentHeight: height
        flickableDirection: Flickable.HorizontalFlick
        interactive: table.wide
        boundsBehavior: Flickable.StopAtBounds
        clip: true
        QQC2.ScrollBar.horizontal: Tide.ScrollBar {}

        Item {
            id: sheet
            width: sheetFlick.contentWidth
            height: sheetFlick.height - table.barSpace

            // Nagłówek tabeli.
            Item {
                id: header
                objectName: "fullPlanHeader"
                width: parent.width
                height: table.headerHeight
                Rectangle {
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                    height: 1
                    color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityOutline)
                }
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: table.sidePadding
                    anchors.rightMargin: table.sidePadding + Tide.Tokens.scrollbarWidth
                    spacing: table.gap
                    component HeadCell: Text {
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.78
                        font.weight: Font.DemiBold
                        font.capitalization: Font.AllUppercase
                        font.letterSpacing: 0.3
                        elide: Text.ElideRight
                    }
                    HeadCell { text: "Fala"; Layout.preferredWidth: table.waveWidth; Layout.maximumWidth: table.waveWidth }
                    HeadCell { text: "Ticket"; Layout.preferredWidth: table.ticketWidth; Layout.maximumWidth: table.ticketWidth }
                    HeadCell { text: "Tytuł"; Layout.fillWidth: true }
                    HeadCell { text: "Stan"; Layout.preferredWidth: table.stateWidth; Layout.maximumWidth: table.stateWidth }
                    HeadCell { text: "Wykonawca → recenzent"; Layout.preferredWidth: table.peopleWidth; Layout.maximumWidth: table.peopleWidth }
                    Item { Layout.preferredWidth: table.actionWidth }
                }
            }

            // Przypięta bieżąca fala: najwyżej 40% wysokości tabeli, więcej — własne przewijanie.
            Flickable {
                id: pinnedFlick
                objectName: "fullPlanPinned"
                anchors.top: header.bottom
                width: parent.width
                height: table.pinnedHeight
                visible: table.pinnedRows.length > 0
                contentWidth: width
                contentHeight: pinnedColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                clip: true
                QQC2.ScrollBar.vertical: Tide.ScrollBar {}
                Column {
                    id: pinnedColumn
                    width: pinnedFlick.width
                    Repeater {
                        model: table.pinnedRows
                        delegate: PlanRow {}
                    }
                }
            }

            // Pozostałe fale od pierwszej.
            Flickable {
                id: bodyFlick
                objectName: "fullPlanBody"
                anchors.top: pinnedFlick.visible ? pinnedFlick.bottom : header.bottom
                anchors.bottom: parent.bottom
                width: parent.width
                contentWidth: width
                contentHeight: otherColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                clip: true
                QQC2.ScrollBar.vertical: Tide.ScrollBar {}
                Column {
                    id: otherColumn
                    width: bodyFlick.width
                    Repeater {
                        model: table.otherRows
                        delegate: PlanRow {}
                    }
                }
            }
        }
    }
}

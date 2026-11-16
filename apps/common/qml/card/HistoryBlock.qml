// Historia karty w stałym bloku (K-128, PT-503, makieta S-9 „Historia karty”, wariant A): `ListView` w ramce o stałej
// wysokości 340 px (`fill` — cała dostępna wysokość, zakładka Historia) z `Tide.ScrollBar`, więc strona nie rośnie
// z liczbą zdarzeń. Najnowsze u góry; w nagłówku sekcji licznik zdarzeń. „Pokaż starsze” (wcześniejsze zlecenia,
// tylko do odczytu, przygaszone) rozwija się wewnątrz bloku, na końcu listy. Wiersze: czas, rodzaj, zdanie
// (`Tide.Prose`), znacznik zdarzenia użytkownika albo opiekuna i — tylko dla kroków bieżącego zlecenia z pytaniem
// albo zatrzymanych (`canResume` z usługi) — „Wznów rozmowę”.
//
// Ten sam komponent pokazuje historię w widoku karty i w panelu karty na Tablicy (K-128 #5). Niczego nie liczy:
// kolejność i podpisy (`kindLabel`, `actorLabel`, `canResume`, `old`) przychodzą z usługi (`flow.history[]`).
//
// Umowa: `current` i `older` — wpisy `flow.history[]` od najnowszego (bieżące zlecenie i wcześniejsze), `olderOpen`,
// `fill`/`availableHeight`, sygnał `resumeRequested(step)`.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide
import "history.js" as HistoryTime

ColumnLayout {
    id: block
    objectName: "fullHistory"

    property var current: []
    property var older: []
    property bool olderOpen: false
    property bool fill: false
    property real availableHeight: 0
    property real frameHeight: 340
    readonly property int total: current.length + older.length
    // Wiersze listy: bieżące zlecenie, a po rozwinięciu wcześniejsze (przygaszone, bez wznawiania).
    readonly property var rows: current.map(h => Object.assign({}, h, {dimmed: false, resumable: h.canResume === true}))
        .concat(olderOpen ? older.map(h => Object.assign({}, h, {dimmed: true, resumable: false})) : [])
    readonly property alias list: list

    signal resumeRequested(string step)

    spacing: 8

    // `rows` jest nową tablicą, więc zmiana `olderOpen` resetuje model i `ListView` wraca na górę. Pozycję przewijania
    // zapamiętujemy przed zmianą i przywracamy po niej (po rozwinięciu starsze wpisy leżą tuż pod przyciskiem).
    function toggleOlder() {
        const y = list.contentY
        olderOpen = !olderOpen
        const restore = () => {
            list.contentY = y
            list.returnToBounds()
        }
        restore()
        Qt.callLater(restore)
    }

    function eventsLabel(n) {
        const last = n % 10, tens = n % 100
        return n + (n === 1 ? " zdarzenie" : last >= 2 && last <= 4 && (tens < 12 || tens > 14) ? " zdarzenia" : " zdarzeń")
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 10
        Text {
            text: "Historia"
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt * 1.1
            font.weight: Font.DemiBold
        }
        Text {
            objectName: "fullHistoryCount"
            Layout.fillWidth: true
            text: block.total > 0 ? block.eventsLabel(block.total) : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt * 0.95
        }
    }

    Rectangle {
        id: frame
        objectName: "fullHistoryFrame"
        Layout.fillWidth: true
        Layout.preferredHeight: block.fill ? Math.max(block.availableHeight, 160) : block.frameHeight
        radius: Tide.Tokens.radiusCard
        color: Tide.Tokens.backgroundCard
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
        clip: true

        Text {
            objectName: "fullHistoryEmpty"
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.margins: 14
            visible: block.total === 0
            text: "Nic jeszcze się nie wydarzyło."
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
        }

        ListView {
            id: list
            objectName: "fullHistoryList"
            anchors.fill: parent
            anchors.margins: 6
            model: block.rows
            spacing: 4
            clip: true
            cacheBuffer: 600
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: Tide.ScrollBar {}

            delegate: Rectangle {
                id: line
                required property var modelData
                objectName: "fullHistoryLine"
                readonly property string text: modelData.text
                readonly property bool byUser: modelData.actor === "user"
                readonly property bool byCaretaker: modelData.actor === "caretaker"
                readonly property string when: HistoryTime.when(modelData.ts)
                width: ListView.view.width - Tide.Tokens.scrollbarWidth - 4
                implicitHeight: lineRow.implicitHeight + 14
                radius: Tide.Tokens.radiusSmall
                color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.03)
                opacity: modelData.dimmed ? 0.7 : 1
                Accessible.role: Accessible.ListItem
                Accessible.name: [when, modelData.kindLabel, text, byUser ? "zdarzenie użytkownika" : "",
                              byCaretaker ? modelData.actorLabel : ""].filter(x => !!x).join(", ")
                RowLayout {
                    id: lineRow
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 10
                    spacing: 10
                    Text {
                        Layout.preferredWidth: 150
                        Layout.alignment: Qt.AlignTop
                        text: line.when
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.85
                        elide: Text.ElideRight
                    }
                    Text {
                        objectName: "historyKind"
                        Layout.preferredWidth: 70
                        Layout.alignment: Qt.AlignTop
                        text: line.modelData.kindLabel
                        textFormat: Text.PlainText
                        color: Tide.Tokens.lavender
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.85
                    }
                    Tide.Prose {
                        objectName: "historyText"
                        Layout.fillWidth: true
                        Layout.alignment: Qt.AlignTop
                        text: line.text
                        textColor: Tide.Tokens.textPrimary
                        pointSize: Tide.Tokens.fontSizePt * 0.95
                        measure: 120
                    }
                    Text {
                        objectName: "historyUser"
                        Layout.alignment: Qt.AlignTop
                        visible: line.byUser
                        text: "Ty"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.peach
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.85
                        font.weight: Font.DemiBold
                    }
                    Text {
                        objectName: "historyCaretaker"
                        Layout.alignment: Qt.AlignTop
                        visible: line.byCaretaker
                        text: line.modelData.actorLabel || ""   // podpis z usługi („Opiekun”) zamiast nazwy wykonawcy
                        textFormat: Text.PlainText
                        color: Tide.Tokens.teal
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.85
                        font.weight: Font.DemiBold
                    }
                    Tide.Button {
                        objectName: "historyResume"
                        Layout.alignment: Qt.AlignTop
                        visible: line.modelData.resumable
                        text: "Wznów rozmowę"
                        small: true
                        Accessible.name: "Wznów rozmowę w kroku " + line.modelData.text
                        onClicked: block.resumeRequested(line.modelData.step)
                    }
                }
            }

            footer: Item {
                width: list.width
                height: block.older.length > 0 ? olderButton.implicitHeight + 8 : 0
                visible: block.older.length > 0
                Tide.Button {
                    id: olderButton
                    objectName: "fullHistoryOld"
                    anchors.left: parent.left
                    anchors.top: parent.top
                    anchors.topMargin: 4
                    text: block.olderOpen ? "▾ Ukryj starsze (" + block.older.length + ")"
                                          : "▸ Pokaż starsze (" + block.older.length + ")"
                    variant: "ghost"
                    Accessible.name: "Starsze zlecenia: " + block.older.length + (block.olderOpen ? ", rozwinięte" : ", zwinięte")
                    onClicked: block.toggleOlder()
                }
            }
        }
    }
}

// Kolumna tablicy: nagłówek z kropką, nazwą i liczbą, karty i na dole „＋ Dodaj kartę” (kolumny
// ręczne). Do zrobienia ma przerywaną podpowiedź „Upuść kartę z Backlogu”, Gotowe — zwarte karty
// i „+N starszych”. Kolumna ręczna przyjmuje upuszczoną kartę: linia w akcencie pokazuje miejsce,
// a upuszczenie zgłasza dropped(id, przed którą kartą). W trybie fill karty przewijają się
// w kolumnie, inaczej (wiersz na projekt) kolumna rośnie z treścią.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "board.js" as Logic

Rectangle {
    id: columnRoot

    property var spec: ({})
    property var cards: []
    property string selectedId: ""
    property bool expanded: false
    property bool fill: true
    property bool showHeader: true
    property bool showAdd: true
    property bool canLaunch: false
    property bool canDiff: false
    property var tagTone: null
    property var dragHost: null
    signal cardClicked(string id)
    signal addRequested(string column)
    signal dropped(string id, string before)
    signal moveRequested(string id, string column)
    signal sessionRequested(string session)
    signal agentRequested(string session)
    signal launchRequested(string id)
    signal diffRequested(string id)
    signal keyNavigation(string id, int dx, int dy)
    signal keyMove(string id, int dx, int dy)

    readonly property bool compact: spec.id === "done"
    readonly property bool manual: spec.manual === true
    readonly property var shown: compact ? Logic.doneShown(cards, expanded) : cards
    readonly property bool dragging: dragHost !== null && dragHost.dragging
    readonly property bool hovered: drop.containsDrag && manual
    property string hoverBefore: ""
    readonly property alias flick: flick
    readonly property bool hintVisible: dropHint.visible

    // Model kart o stabilnej tożsamości (PT-324): lista samych `id` w ListModel, a treść karty w mapie `cardMap`.
    // Nowa migawka zmienia model tylko tam, gdzie karta doszła, zniknęła, zmieniła miejsce albo treść; karta
    // o niezmienionej treści zostaje tym samym obiektem, więc jej delegat nic nie przelicza.
    property var cardMap: ({})
    ListModel { id: cardIds }
    onShownChanged: syncCards()
    Component.onCompleted: syncCards()

    function syncCards() {
        const prev = []
        for (let i = 0; i < cardIds.count; i++) prev.push(cardMap[cardIds.get(i).cardId])
        const ops = Logic.diffCards(prev, shown)
        if (ops.length === 0) return
        // Karty usuwane zostają w mapie do końca operacji, żeby ich delegaty nie zobaczyły pustej karty.
        const map = Object.assign({}, cardMap)
        let removed = false
        for (const op of ops) {
            if (op.card) map[op.id] = op.card
            else if (op.op === "remove") removed = true
        }
        cardMap = map
        for (const op of ops) {
            if (op.op === "remove") cardIds.remove(op.index)
            else if (op.op === "insert") cardIds.insert(op.index, { cardId: op.id })
            else if (op.op === "move") cardIds.move(op.from, op.to, 1)
        }
        if (removed) {
            const kept = {}
            for (let i = 0; i < cardIds.count; i++) kept[cardIds.get(i).cardId] = map[cardIds.get(i).cardId]
            cardMap = kept
        }
    }

    // Karta pod pozycją y (we współrzędnych kolumny), przed którą wstawić upuszczaną: pierwsza,
    // której środek jest niżej; pusty — na koniec.
    function beforeAt(y) {
        for (let i = 0; i < repeater.count; i++) {
            const item = repeater.itemAt(i)
            if (!item || item.card.id === (dragHost ? dragHost.dragId : "")) continue
            const mid = item.mapToItem(columnRoot, 0, item.height / 2).y
            if (mid > y) return item.card.id
        }
        return ""
    }

    function cardItem(id) {
        for (let i = 0; i < repeater.count; i++) {
            const item = repeater.itemAt(i)
            if (item && item.card.id === id) return item
        }
        return null
    }

    function indexOfCard(id) {
        for (let i = 0; i < repeater.count; i++) {
            const item = repeater.itemAt(i)
            if (item && item.card.id === id) return i
        }
        return -1
    }

    function focusAt(index) {
        if (repeater.count === 0) return false
        const item = repeater.itemAt(Math.max(0, Math.min(repeater.count - 1, index)))
        if (!item) return false
        item.forceActiveFocus()
        reveal(item)
        return true
    }

    // Przewija kolumnę tak, żeby karta była widoczna.
    function reveal(item) {
        if (!item || !fill) return
        const y = item.mapToItem(cardsColumn, 0, 0).y
        if (y < flick.contentY) flick.contentY = Math.max(0, y - 6)
        else if (y + item.height > flick.contentY + flick.height)
            flick.contentY = Math.min(flick.contentHeight - flick.height, y + item.height - flick.height + 6)
    }

    // Linia miejsca upuszczenia: nad kartą „przed” albo pod ostatnią.
    function indicatorY() {
        const item = hoverBefore ? cardItem(hoverBefore) : null
        if (item) return item.mapToItem(columnRoot, 0, 0).y - 5
        let last = null
        for (let i = repeater.count - 1; i >= 0; i--) {
            const candidate = repeater.itemAt(i)
            if (candidate && candidate.card.id !== (dragHost ? dragHost.dragId : "")) { last = candidate; break }
        }
        return last ? last.mapToItem(columnRoot, 0, last.height).y + 3 : header.y + header.height + 8
    }

    implicitHeight: content.implicitHeight + 20
    radius: 16
    // Wiersz na projekt: komórki spokojniejsze niż pełne kolumny, żeby puste nie dominowały.
    color: Tide.Theme.alpha(Tide.Tokens.backgroundPanel, compact || !showHeader ? 0.35 : 0.55)
    border.width: hovered ? 1 : 0
    border.color: Tide.Theme.alpha(Tide.Tokens.accent, 0.45)
    opacity: dragging && !manual ? 0.55 : 1
    Behavior on opacity { NumberAnimation { duration: 120 } }

    Accessible.role: Accessible.List
    Accessible.name: (spec.label || "") + ", " + Logic.cards(cards.length)

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: 10
        spacing: 8

        RowLayout {
            id: header
            visible: columnRoot.showHeader
            Layout.fillWidth: true
            Layout.leftMargin: 2
            Layout.rightMargin: 2
            Layout.topMargin: 2
            Layout.bottomMargin: 2
            spacing: 7
            Dot {
                tint: columnRoot.spec.tint || "textMuted"
                hollow: columnRoot.spec.hollow === true
            }
            Text {
                Layout.fillWidth: true
                text: columnRoot.spec.label || ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                font.weight: Font.DemiBold
                elide: Text.ElideRight
                Accessible.role: Accessible.Heading
            }
            Text {
                text: columnRoot.cards.length
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 12
            }
        }

        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: columnRoot.fill
            Layout.preferredHeight: columnRoot.fill ? -1 : cardsColumn.implicitHeight
            clip: columnRoot.fill
            interactive: columnRoot.fill && contentHeight > height
            contentWidth: width
            contentHeight: cardsColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: QQC2.ScrollBar {
                policy: columnRoot.fill && flick.contentHeight > flick.height ? QQC2.ScrollBar.AsNeeded : QQC2.ScrollBar.AlwaysOff
            }

            ColumnLayout {
                id: cardsColumn
                width: flick.width
                spacing: columnRoot.compact ? 6 : 8

                Repeater {
                    id: repeater
                    model: cardIds
                    BoardCard {
                        required property string cardId
                        Layout.fillWidth: true
                        card: columnRoot.cardMap[cardId]
                        compact: columnRoot.compact
                        selected: cardId === columnRoot.selectedId
                        canLaunch: columnRoot.canLaunch
                        canDiff: columnRoot.canDiff
                        tagTone: columnRoot.tagTone
                        dragHost: columnRoot.dragHost
                        onClicked: columnRoot.cardClicked(cardId)
                        onMoveRequested: target => columnRoot.moveRequested(cardId, target)
                        onSessionRequested: session => columnRoot.sessionRequested(session)
                        onAgentRequested: session => columnRoot.agentRequested(session)
                        onLaunchRequested: columnRoot.launchRequested(cardId)
                        onDiffRequested: columnRoot.diffRequested(cardId)
                        onKeyNavigation: (dx, dy) => columnRoot.keyNavigation(cardId, dx, dy)
                        onKeyMove: (dx, dy) => columnRoot.keyMove(cardId, dx, dy)
                    }
                }

                // Gotowe: starsze zwinięte.
                Text {
                    objectName: "doneMore"
                    Layout.leftMargin: 4
                    visible: columnRoot.compact && columnRoot.cards.length > Logic.DONE_PREVIEW
                    text: columnRoot.expanded ? "pokaż mniej" : "+ " + (columnRoot.cards.length - columnRoot.shown.length) + " "
                                                            + Logic.plural(columnRoot.cards.length - columnRoot.shown.length,
                                                                           "starsza", "starsze", "starszych")
                    textFormat: Text.PlainText
                    color: moreArea.containsMouse ? Tide.Tokens.accent : Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 11
                    Accessible.role: Accessible.Button
                    Accessible.name: text
                    Accessible.onPressAction: columnRoot.expanded = !columnRoot.expanded
                    MouseArea {
                        id: moreArea
                        anchors.fill: parent
                        anchors.margins: -4
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: columnRoot.expanded = !columnRoot.expanded
                    }
                }

                // Do zrobienia: podpowiedź z mocka — tu upuść kartę z Backlogu.
                Item {
                    id: dropHint
                    objectName: "dropHint"
                    Layout.fillWidth: true
                    visible: columnRoot.spec.id === "todo" && (columnRoot.showHeader || columnRoot.hovered)
                    implicitHeight: hintColumn.implicitHeight + 28
                    DashedFrame {
                        anchors.fill: parent
                        radius: 12
                        strokeWidth: 1.5
                        strokeColor: Tide.Theme.alpha(Tide.Tokens.accent, columnRoot.hovered ? 0.8 : 0.45)
                        fillColor: columnRoot.hovered ? Tide.Theme.alpha(Tide.Tokens.accent, 0.06) : "transparent"
                    }
                    Column {
                        id: hintColumn
                        anchors.centerIn: parent
                        width: parent.width - 20
                        spacing: 4
                        Text {
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            text: "Upuść kartę z Backlogu"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.accent
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                            font.weight: Font.DemiBold
                            wrapMode: Text.Wrap
                        }
                        Text {
                            width: parent.width
                            horizontalAlignment: Text.AlignHCenter
                            text: columnRoot.canLaunch ? "zapytam, czy uruchomić agenta" : "trafi na koniec kolejki"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                    }
                }

                PillButton {
                    objectName: "addCard"
                    Layout.fillWidth: true
                    visible: columnRoot.showAdd && columnRoot.manual && !columnRoot.compact
                    implicitHeight: 30
                    flat: true
                    pillRadius: Tide.Tokens.radiusSmall
                    pixelSize: 12
                    horizontalPadding: 6
                    text: "＋ Dodaj kartę"
                    textColor: Tide.Tokens.textMuted
                    toolTip: "Nowa karta w kolumnie " + (columnRoot.spec.label || "")
                    leftAligned: true
                    onClicked: columnRoot.addRequested(columnRoot.spec.id)
                }

                Text {
                    Layout.leftMargin: 4
                    visible: columnRoot.cards.length === 0 && !columnRoot.manual && columnRoot.showHeader
                    text: columnRoot.spec.id === "progress" ? "tu karty pojawią się same, gdy ruszy agent"
                        : columnRoot.spec.id === "waiting" ? "nic nie czeka na ciebie"
                        : "tu trafi praca agentów do sprawdzenia"
                    textFormat: Text.PlainText
                    color: Tide.Theme.alpha(Tide.Tokens.textMuted, 0.75)
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                    Layout.fillWidth: true
                }
            }
        }

        // Bez przewijania (wiersz na projekt) komórka bywa wyższa niż karty — karty zostają u góry.
        Item {
            visible: !columnRoot.fill
            Layout.fillHeight: true
        }
    }

    // Miejsce upuszczenia.
    Rectangle {
        visible: columnRoot.hovered
        x: 12
        width: columnRoot.width - 24
        height: 2
        radius: 1
        y: columnRoot.hovered ? columnRoot.indicatorY() : 0
        color: Tide.Tokens.accent
    }

    DropArea {
        id: drop
        anchors.fill: parent
        keys: ["tide-card"]
        onEntered: drag => columnRoot.hoverBefore = columnRoot.beforeAt(drag.y)
        onPositionChanged: drag => {
            columnRoot.hoverBefore = columnRoot.beforeAt(drag.y)
            scroller.edge = !columnRoot.fill ? 0 : drag.y < 60 ? -1 : drag.y > columnRoot.height - 60 ? 1 : 0
        }
        onExited: scroller.edge = 0
        onDropped: drop => {
            scroller.edge = 0
            columnRoot.dropped(columnRoot.dragHost ? columnRoot.dragHost.dragId : "", columnRoot.beforeAt(drop.y))
        }
    }

    // Przewijanie kolumny, gdy karta jest przy jej górnej albo dolnej krawędzi.
    Timer {
        id: scroller
        property int edge: 0
        interval: 30
        repeat: true
        running: edge !== 0 && columnRoot.hovered
        onTriggered: flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, flick.contentY + edge * 12))
    }
}

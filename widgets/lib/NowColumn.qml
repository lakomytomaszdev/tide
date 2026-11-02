// Prawa kolumna konsoli Stream, wariant A (PT-105, decyzje 1, 4 i 5 K-29): rząd siedmiu kategorii, nagłówek
// „Teraz” (etap i tryb, karty schowane do przywrócenia, „Dodaj kartę”) — razem na karcie nagłówka (PT-107), karty „Teraz” z rdzenia
// (situation.cards) i na końcu karta Stream ze stanem streamu. Karta ma znacznik auto/przypięta, pinezkę
// (panel.pin) i × (panel.hide). Rdzeń bez tych pól: karty z situation.cards bez znaczników i przycisków.
// Kategorie otwierają popup nad kolumną (CategoryPopup) z ustawieniami kategorii (PT-106): sięga od rzędu kategorii
// do dołu kolumny, karty zostają pod spodem. Karta Stream ma tu same wiersze stanu — profil gracza jest w kategorii
// Przygotowanie, a przełączniki modułów w kategorii Na ekranie. Gdy karty się nie mieszczą,
// karta Stream przechodzi w jedną linię stanu, a przed streamem także karta wyniku w formę skróconą; dopiero
// potem karty się przewijają. Same sygnały — komendy wysyła StreamDesk/main.qml.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "stream.js" as Stream

Item {
    id: column

    property var view: null
    property bool coreUp: false
    property var obsMemory: ""
    property var chatModel: null
    property string status: ""
    property bool statusError: false
    property bool busy: false
    property string currentCategory: ""
    // Miejsce zostawiane pod kartami (skrócony plan gry w StreamDesk), gdy karty i tak się mieszczą.
    property real reserveBottom: 0
    // false: konsola zasłonięta (np. pełny plan gry) — popupy kolumny się zamykają.
    property bool active: true

    signal commandRequested(string name, var args)
    signal panelRequested()
    // Każda zmiana otwartej kategorii (id albo "" po zamknięciu).
    signal categoryOpened(string id)

    // Wynik ostatniej kontroli przed startem (kategoria Przygotowanie) — trzyma go konsola, jak tablet, do restartu.
    property var preflight: null
    property bool preflightRunning: false
    // Odpowiedź rdzenia na komendę (StreamDesk.commandDone): wynik kontroli przed startem (result — obiekt wyniku
    // albo null) i potwierdzenie zapisu formularza w popupie kategorii.
    function commandDone(name, ok, result) {
        if (name === "preflight.run") {
            preflightRunning = false
            if (ok && result && typeof result === "object") preflight = Object.assign({ at: Date.now() }, result)
        }
        categoryPopup.commandDone(name, ok)
    }

    readonly property var model: Stream.now(view, coreUp)
    readonly property bool categoryOpen: categoryPopup.opened
    readonly property bool addMenuOpen: addMenu.opened

    // Czy kolumna mieści karty w pełnej formie: wysokości pełnych kart Stream i wyniku mierzą niewidoczne
    // bliźniaki (zawsze świeże, niezależne od bieżącej formy), a lista profili do decyzji się nie liczy —
    // otwarta lista może przewinąć kolumnę, ale nie chowa karty, w której ją otwarto.
    readonly property real streamFullHeight: streamTwin.implicitHeight
    readonly property real scoreFullHeight: scoreTwin.active ? scoreTwin.implicitHeight : 0
    // Bieżąca wysokość karty wyniku prosto z jej slotu (Repeater tworzy delegaty od nowa przy zmianie kart).
    readonly property real scoreHeight: {
        for (let i = 0; i < slots.count; i++) {
            const slot = slots.itemAt(i)
            if (slot && slot.entry && slot.entry.id === "score" && slot.item) return slot.item.implicitHeight
        }
        return 0
    }
    readonly property real naturalFull: cards.implicitHeight - streamCard.implicitHeight + streamFullHeight
                                        - scoreHeight + scoreFullHeight
    // Decyzja zapada chwilę po zmianie (wysokości kolumny liczą się po polerowaniu układu), a z formy skróconej
    // wracamy dopiero z zapasem 16 px — bez migotania na granicy.
    property bool streamCompact: false
    readonly property bool scoreCompact: streamCompact && !(view && view.situation && view.situation.stage === "live")
    function evaluateCompact() {
        const room = flick.height
        if (streamFullHeight <= 0 || room <= 0) { streamCompact = false; return }
        if (streamCompact) { if (naturalFull < room - 16) streamCompact = false }
        else if (naturalFull > room) streamCompact = true
    }
    onNaturalFullChanged: compactTimer.restart()
    Timer {
        id: compactTimer
        interval: 60
        onTriggered: column.evaluateCompact()
    }
    readonly property var categories: Stream.categories(view, coreUp, preflight)
    // Dół zawartości (kart i karty Stream) w kolumnie — pod nim StreamDesk stawia skrócony plan gry.
    readonly property real contentBottom: flick.y + Math.min(flick.contentHeight, flick.height)
    // Wysokość kart: cała kolumna (bez martwego pasa na dole), a gdy zmieści się plan gry — kolumna bez niego.
    readonly property real fillHeight: cards.implicitHeight + reserveBottom <= flick.height
                                       ? flick.height - reserveBottom : flick.height
    // Karta, która rośnie i zabiera wolne miejsce — tylko taka, której wysokość coś daje: wynik z serią (rosną
    // cyfry) i ostatnie alerty (więcej wierszy). Mecz i karty z samym tekstem nigdy nie rosną; bez karty do
    // wzrostu wolne miejsce zostaje jedną przerwą nad kartą Stream przyklejoną do dołu.
    readonly property bool scoreSeries: !!Stream.scoreCard(view, coreUp).series
    readonly property string growId: {
        const ids = column.model.items.map(i => i.id)
        const order = (column.scoreSeries ? ["score"] : []).concat(column.manyAlerts ? ["alerts"] : [])
        return order.filter(id => ids.indexOf(id) >= 0)[0] || ""
    }
    // Alerty rosną dopiero, gdy jest co pokazać (co najmniej 4) — inaczej pusta karta zamiast przerwy.
    readonly property bool manyAlerts: {
        const m = column.chatModel
        if (!m || m.count === undefined) return false
        let n = 0
        for (let i = m.count - 1; i >= 0 && n < 4; i--) {
            const line = m.get(i)
            if (line && line.kind === "alert") n++
        }
        return n >= 4
    }
    readonly property int alertLimit: growId === "alerts" ? 12 : 3
    // Teksty ostatnich alertów z czatu (karta „Ostatnie alerty”), najnowsze pierwsze.
    readonly property var alerts: {
        const m = column.chatModel
        const out = []
        if (!m || m.count === undefined) return out
        for (let i = m.count - 1; i >= 0 && out.length < column.alertLimit; i--) {
            const line = m.get(i)
            if (line && line.kind === "alert") out.push(line.text)
        }
        return out
    }

    function closePopups() {
        categoryPopup.close()
        addMenu.close()
    }
    // Popupy nie mogą zostać nad Graniem, schowaną nakładką ani pełnym planem gry.
    onVisibleChanged: if (!visible) closePopups()
    onActiveChanged: if (!active) closePopups()
    onHeightChanged: compactTimer.restart()

    function openCategory(id) {
        currentCategory = currentCategory === id ? "" : id
        if (currentCategory === "") categoryPopup.close()
        else categoryPopup.open()
        categoryOpened(currentCategory)
    }

    function run(command) {
        column.commandRequested(command.name, command.args)
    }

    // Kategorie, nagłówek „Teraz” i schowane karty na wspólnym tle karty — gołe napisy ginęły na tapecie.
    Card {
        id: nowHeader
        objectName: "nowHeader"
        width: column.width
        padding: 10
        spacing: 8

        CategoryRow {
            id: categoryRow
            Layout.fillWidth: true
            view: column.view
            coreUp: column.coreUp
            current: column.currentCategory
            preflight: column.preflight
            onCategoryRequested: id => column.openCategory(id)
        }

        // Nagłówek „Teraz”.
        RowLayout {
            visible: column.coreUp
            Layout.fillWidth: true
            spacing: 8
            Text {
                text: "Teraz"
                textFormat: Text.PlainText
                color: Tokens.textPrimary
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt + 3
                font.weight: Font.DemiBold
            }
            Text {
                objectName: "nowStage"
                Layout.fillWidth: true
                text: column.model.stage
                textFormat: Text.PlainText
                elide: Text.ElideRight
                color: Tokens.textMuted
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 1
            }
            IconButton {
                id: addButton
                objectName: "nowAdd"
                visible: column.model.controls && column.model.addable.length > 0
                enabled: !column.busy
                framed: true
                iconName: "plus"
                iconSize: 13
                label: "Dodaj kartę"
                tip: "Dodaj kartę na panel (przypięcie w tym trybie)"
                onClicked: addMenu.opened ? addMenu.close() : addMenu.open()
                QQC2.Popup {
                    id: addMenu
                    objectName: "nowAddMenu"
                    y: addButton.height + 4
                    x: addButton.width - width
                    padding: 6
                    closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutsideParent
                    background: Rectangle {
                        radius: Tokens.radiusFrame
                        color: Tokens.backgroundCard
                        border.width: 1
                        border.color: Theme.alpha(Tokens.accent, 0.55)
                    }
                    contentItem: ColumnLayout {
                        spacing: 2
                        Repeater {
                            model: column.model.addable
                            TextButton {
                                required property var modelData
                                objectName: "nowAdd-" + modelData.id
                                Layout.fillWidth: true
                                variant: "outline"
                                topPadding: 6
                                bottomPadding: 6
                                text: modelData.title
                                textColor: Tokens.textPrimary
                                background: Rectangle {
                                    radius: Tokens.radiusSmall
                                    color: Theme.alpha(Tokens.textPrimary, parent.lit ? 0.10 : 0)
                                }
                                onClicked: {
                                    addMenu.close()
                                    column.run(Stream.pinCommand({ id: modelData.id, state: "auto" }))
                                }
                            }
                        }
                    }
                }
            }
        }

        // Karty schowane do końca streamu — kliknięcie przywraca (panel.hide hidden=false).
        Flow {
            visible: column.model.hidden.length > 0
            Layout.fillWidth: true
            spacing: 6
            Text {
                height: 24
                verticalAlignment: Text.AlignVCenter
                text: "Schowane:"
                textFormat: Text.PlainText
                color: Tokens.textMuted
                font.family: Theme.uiFamily
                font.pointSize: Tokens.fontSizePt - 1
            }
            Repeater {
                model: column.model.hidden
                TextButton {
                    required property var modelData
                    objectName: "nowRestore-" + modelData.id
                    variant: "outline"
                    enabled: !column.busy
                    topPadding: 4
                    bottomPadding: 4
                    text: modelData.title + " · przywróć"
                    textColor: Tokens.textSecondary
                    Accessible.name: "Przywróć kartę „" + modelData.title + "”"
                    background: Rectangle {
                        radius: height / 2
                        color: Theme.alpha(Tokens.textPrimary, parent.lit ? 0.10 : 0)
                        border.width: 1
                        border.color: Theme.alpha(Tokens.textPrimary, 0.22)
                    }
                    onClicked: column.run(Stream.hideCommand(modelData.id, false))
                }
            }
        }
    }

    Flickable {
        id: flick
        objectName: "nowFlick"
        y: nowHeader.height + 8
        width: column.width
        height: column.height - y
        contentWidth: width
        contentHeight: cards.height
        clip: true
        interactive: contentHeight > height
        boundsBehavior: Flickable.StopAtBounds

        ColumnLayout {
            id: cards
            width: flick.width
            height: Math.max(implicitHeight, column.fillHeight)
            spacing: 8

            Repeater {
                id: slots
                model: column.model.items
                Loader {
                    id: slot
                    required property var modelData
                    objectName: "nowSlot-" + modelData.id
                    Layout.fillWidth: true
                    Layout.fillHeight: modelData.id === column.growId
                    sourceComponent: modelData.id === "score" ? scoreCard : genericCard
                    // Komponenty karty czytają wpis przez slot.modelData.
                    readonly property var entry: modelData
                }
            }

            // Bez karty do wzrostu: jedna przerwa nad kartą Stream (przyklejoną do dołu kolumny).
            Item {
                objectName: "nowGap"
                visible: column.growId === ""
                Layout.fillHeight: true
            }

            StreamCard {
                id: streamCard
                objectName: "nowStreamCard"
                Layout.fillWidth: true
                compact: column.streamCompact
                onCompactChanged: if (compact) profilesOpen = false
                actions: false
                controls: false
                full: true
                view: column.view
                coreUp: column.coreUp
                obsMemory: column.obsMemory
                status: column.status
                statusError: column.statusError
                busy: column.busy
                onMicRequested: muted => column.commandRequested("mic.set", { muted: muted })
                onBrbRequested: column.commandRequested("brb.toggle", {})
                onModuleRequested: (key, visible) => column.commandRequested("module.set", { module: key, visible: visible })
                onProfileRequested: id => column.commandRequested("profile.set", { profile: id })
                onPanelRequested: column.panelRequested()
                onCommandRequested: (name, args) => column.commandRequested(name, args)
            }
        }
    }

    // Niewidoczne bliźniaki do pomiaru pełnej formy (przezroczyste, wyłączone, poza czytnikiem ekranu).
    Item {
        objectName: "nowTwins"
        width: column.width
        height: 0
        clip: true
        opacity: 0
        enabled: false
        Accessible.ignored: true
        StreamCard {
            id: streamTwin
            width: parent.width
            actions: false
            controls: false
            full: true
            view: column.view
            coreUp: column.coreUp
            obsMemory: column.obsMemory
            status: column.status
            statusError: column.statusError
        }
        ScoreCard {
            id: scoreTwin
            readonly property bool active: column.model.items.some(i => i.id === "score")
            visible: active
            width: parent.width
            item: column.model.items.filter(i => i.id === "score")[0]
                  || ({ id: "score", title: "Wynik", state: "", pinnable: false, hideable: false })
            view: column.view
            coreUp: column.coreUp
        }
    }

    // Wąski wskaźnik przewijania, gdy karty nie mieszczą się w kolumnie.
    Rectangle {
        objectName: "nowScroll"
        visible: flick.interactive
        x: column.width + 4
        y: flick.y + flick.visibleArea.yPosition * flick.height
        width: 3
        height: flick.visibleArea.heightRatio * flick.height
        radius: width / 2
        color: Theme.alpha(Tokens.textPrimary, 0.30)
    }

    Component {
        id: scoreCard
        ScoreCard {
            item: parent ? parent.entry : ({})
            compact: column.scoreCompact
            view: column.view
            coreUp: column.coreUp
            busy: column.busy
            onCommandRequested: (name, args) => column.commandRequested(name, args)
            onPinRequested: column.run(Stream.pinCommand(item))
            onHideRequested: column.run(Stream.hideCommand(item.id, true))
        }
    }

    Component {
        id: genericCard
        NowCard {
            id: generic
            item: parent ? parent.entry : ({})
            busy: column.busy
            readonly property var body: Stream.nowBody(column.view, column.coreUp, item.id, column.alerts)
            readonly property var linked: column.categories.filter(c => c.id === body.category)[0] || null
            // Karta z ustawieniami w kategorii otwiera jej popup (PT-106); akcje krytyczne — Panel streamera.
            linkText: linked ? linked.label : body.panel ? "Panel streamera" : ""
            onLinkClicked: {
                if (!linked) column.panelRequested()
                else if (column.currentCategory !== linked.id) column.openCategory(linked.id)
            }
            onPinRequested: column.run(Stream.pinCommand(item))
            onHideRequested: column.run(Stream.hideCommand(item.id, true))

            Repeater {
                model: generic.body.lines
                Text {
                    required property var modelData
                    Layout.fillWidth: true
                    text: modelData.text
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: Tokens[modelData.tone] || Tokens.textPrimary
                    font.family: Theme.uiFamily
                    font.pointSize: Tokens.fontSizePt
                }
            }
            Item { Layout.fillHeight: true }    // treść u góry, gdy karta rośnie
            TextButton {
                objectName: "now-action-" + generic.item.id
                visible: generic.body.action !== null
                variant: "outline"
                topPadding: 7
                bottomPadding: 7
                enabled: !column.busy
                text: generic.body.action ? generic.body.action.text : ""
                textColor: Tokens.textPrimary
                onClicked: column.run(generic.body.action)
            }
        }
    }

    // Od rzędu kategorii do dołu kolumny: nieprzezroczysty i bez do połowy zasłoniętej karty pod spodem.
    CategoryPopup {
        id: categoryPopup
        x: 0
        y: nowHeader.padding + categoryRow.y + categoryRow.height + 6
        width: column.width
        height: Math.max(160, column.height - y)
        category: column.categories.filter(c => c.id === column.currentCategory)[0] || null
        view: column.view
        coreUp: column.coreUp
        busy: column.busy
        status: column.status
        preflight: column.preflight
        preflightRunning: column.preflightRunning
        onCommandRequested: (name, args) => {
            if (name === "preflight.run") column.preflightRunning = true
            column.commandRequested(name, args)
        }
        onPanelRequested: column.panelRequested()
        onClosed: if (column.currentCategory !== "") { column.currentCategory = ""; column.categoryOpened("") }
    }
}

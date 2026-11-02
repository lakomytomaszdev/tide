// Prawa kolumna konsoli Stream, wariant A (PT-105, decyzje 1, 4 i 5 K-29): rząd siedmiu kategorii, nagłówek
// „Teraz” (etap i tryb, karty schowane do przywrócenia, „Dodaj kartę”), karty „Teraz” z rdzenia
// (situation.cards) i na końcu karta Stream ze stanem streamu. Karta ma znacznik auto/przypięta, pinezkę
// (panel.pin) i × (panel.hide). Rdzeń bez tych pól: karty z situation.cards bez znaczników i przycisków.
// Kategorie otwierają popup nad kolumną (CategoryPopup), karty zostają pod spodem. Karty przewijają się, gdy
// nie mieszczą się w kolumnie. Same sygnały — komendy wysyła StreamDesk/main.qml.
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

    signal commandRequested(string name, var args)
    signal panelRequested()
    // Hak dla PT-106: każda zmiana otwartej kategorii (id albo "" po zamknięciu).
    signal categoryOpened(string id)

    readonly property var model: Stream.now(view, coreUp)
    readonly property var categories: Stream.categories(view, coreUp)
    // Dół zawartości (kart i karty Stream) w kolumnie — pod nim StreamDesk stawia skrócony plan gry.
    readonly property real contentBottom: flick.y + Math.min(flick.contentHeight, flick.height)
    // Wysokość kart: cała kolumna (bez martwego pasa na dole), a gdy zmieści się plan gry — kolumna bez niego.
    readonly property real fillHeight: cards.implicitHeight + reserveBottom <= flick.height
                                       ? flick.height - reserveBottom : flick.height
    // Karta, która rośnie i zabiera wolne miejsce — tylko taka, której wysokość coś daje: wynik z serią (rosną
    // cyfry), ostatnie alerty (więcej wierszy), mecz. Karty z samym tekstem nigdy nie rosną; bez karty do
    // wzrostu wolne miejsce zostaje jedną przerwą nad kartą Stream przyklejoną do dołu.
    readonly property bool scoreSeries: !!Stream.scoreCard(view, coreUp).series
    readonly property string growId: {
        const ids = column.model.items.map(i => i.id)
        const order = (column.scoreSeries ? ["score"] : []).concat(["alerts", "match"])
        return order.filter(id => ids.indexOf(id) >= 0)[0] || ""
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

    function openCategory(id) {
        currentCategory = currentCategory === id ? "" : id
        if (currentCategory === "") categoryPopup.close()
        else categoryPopup.open()
        categoryOpened(currentCategory)
    }

    function run(command) {
        column.commandRequested(command.name, command.args)
    }

    CategoryRow {
        id: categoryRow
        width: column.width
        view: column.view
        coreUp: column.coreUp
        current: column.currentCategory
        onCategoryRequested: id => column.openCategory(id)
    }

    Flickable {
        id: flick
        objectName: "nowFlick"
        y: categoryRow.height + 12
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
            spacing: 12

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

            Repeater {
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
                Layout.fillWidth: true
                actions: false
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
            linkText: body.panel ? "Panel streamera" : ""
            onLinkClicked: column.panelRequested()
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

    CategoryPopup {
        id: categoryPopup
        x: 0
        y: categoryRow.height + 6
        width: column.width
        category: column.categories.filter(c => c.id === column.currentCategory)[0] || null
        onPanelRequested: column.panelRequested()
        onClosed: if (column.currentCategory !== "") { column.currentCategory = ""; column.categoryOpened("") }
    }
}

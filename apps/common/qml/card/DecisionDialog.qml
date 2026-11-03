// Okno decyzji karty (K-35, PT-132): wyśrodkowane nad stroną, jak pytanie w czacie. Pokazuje jedną decyzję
// z `derived.flow.decisions[]` (PT-123) naraz: plakietka rodzaju, karta · ticket · wykonawca, „co się stało”
// (`happened`), „dlaczego” (`why`), polecenie z `technical.command`, możliwości jako karty z jednym zdaniem
// skutku (kolejność i polecona — z usługi), na końcu „Inna odpowiedź…” (gdy `freeText`), zwinięte
// „Szczegóły techniczne”, stopka „Otwórz całą kartę”, „Później” (Esc) i „Wybierz” (Enter).
//
// Okno niczego nie liczy i niczego nie zaznacza z góry (D10): po otwarciu i po każdej odpowiedzi nic nie jest
// wybrane, „Wybierz” jest nieaktywne, a Enter bez zaznaczenia nic nie robi. Po odpowiedzi okno przechodzi do
// następnej decyzji (odpowiedziane czekają na nową migawkę ukryte); licznik „1 z N” tylko przy N > 1.
// Esc zamyka bez wysyłania — decyzje zostają w „Czeka na mnie”.
//
// Zgoda na łańcuch poleceń (PT-124): lista `commands[]` z flagami (tylko czyta, katalog kroku, zabronione)
// i powodem blokady; pole wyboru tylko przy poleceniach z `canAllow`, zaznaczenie początkowe z `preselected`
// usługi (zaznaczenie poleceń, nie możliwości). Możliwość z `params.allow` wysyła indeksy zaznaczonych; bez
// zaznaczenia „Wybierz” jest nieaktywne i okno mówi dlaczego. Pole własnej odpowiedzi ma podpis z `otherTitle`.
//
// Umowa: `cardId`, `decisions` (z migawki), `start()` — otwarcie od pierwszej decyzji, `failed(id, tekst)` —
// błąd usługi przy odpowiedzi (decyzja wraca), sygnały `answered(decisionId, method, params)` — żądanie
// gotowe do wysłania (metoda /Board i parametry z decyzji), `laterRequested()`, `fullCardRequested(notice)`
// (także dla możliwości, która wymaga wyboru w oknie, np. wykonawcy — na razie wybór jest w panelu karty)
// i `finished()` — nic już nie czeka (ostatnia odpowiedziana albo migawka bez decyzji).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide
import "decision.js" as Decision

Rectangle {
    id: dialog

    readonly property string otherId: "__other__"

    property string cardId: ""
    property var decisions: []
    property var answeredIds: []
    property int index: 0
    property string selected: ""
    property bool techOpen: false
    property string error: ""
    property var checks: []

    readonly property var pending: (decisions || []).filter(d => answeredIds.indexOf(d.id) < 0)
    readonly property var current: pending.length > 0 ? pending[Math.min(index, pending.length - 1)] : null
    readonly property string currentId: current ? current.id : ""
    readonly property var options: current ? current.options || [] : []
    readonly property bool freeText: !!current && current.freeText === true && !!current.reply
    readonly property bool onlyText: freeText && options.length === 0
    readonly property string choice: onlyText ? otherId : selected
    // Zgoda na polecenia z łańcucha (PT-124): lista `commands[]` z usługi, zaznaczenie poleceń w `checks`.
    readonly property var commands: current && current.commands ? current.commands : []
    readonly property bool showCommands: Decision.showsCommands(current)
    readonly property var chosenOption: options.find(o => o.id === choice) || null
    readonly property bool picksCommands: Decision.picksCommands(chosenOption)
    readonly property string otherTitle: current && current.otherTitle ? current.otherTitle : "Inna odpowiedź…"
    readonly property bool canChoose: choice === otherId ? answer.text.trim() !== ""
                                                         : choice !== "" && (!picksCommands || checks.length > 0)
    readonly property string counter: pending.length > 1 ? (Math.min(index, pending.length - 1) + 1) + " z " + pending.length : ""
    readonly property alias answerField: answer

    signal answered(string decisionId, string method, var params)
    signal laterRequested()
    signal fullCardRequested(string notice)
    signal finished()

    function start() {
        answeredIds = []
        index = 0
        reset()
        forceActiveFocus()
    }

    function reset() {
        selected = ""
        techOpen = false
        error = ""
        answer.text = ""
        checks = Decision.preselectedIndexes(current)
    }

    function toggleCommand(at) {
        checks = Decision.toggled(commands, checks, at)
    }

    function pick(id) {
        selected = id
        error = ""
        if (id === otherId) answer.forceActiveFocus()
    }

    function go(step) {
        const next = Math.max(0, Math.min(pending.length - 1, Math.min(index, pending.length - 1) + step))
        if (next !== index) index = next
    }

    // „Wybierz” i Enter: bez zaznaczenia (albo z pustą własną odpowiedzią) nic się nie dzieje.
    function choose() {
        if (!current || !canChoose) return
        let request = null
        if (choice === otherId) {
            request = Decision.replyRequest(current, answer.text.trim())
        } else {
            const option = options.find(o => o.id === choice)
            if (!option) return
            if (Decision.needsInput(option)) {
                fullCardRequested("„" + option.title + "”: wybierz wykonawcę w panelu karty.")
                return
            }
            request = Decision.optionRequest(option, checks)
        }
        if (!request || !request.method) return
        const id = current.id
        answeredIds = answeredIds.concat([id])
        answered(id, request.method, request.params)
        if (pending.length > 0) forceActiveFocus()
    }

    // Usługa odrzuciła odpowiedź: decyzja wraca do listy i pokazuje błąd.
    function failed(decisionId, message) {
        answeredIds = answeredIds.filter(id => id !== decisionId)
        const at = pending.findIndex(d => d.id === decisionId)
        if (at >= 0) index = at
        error = message
    }

    onCurrentIdChanged: reset()
    // Nic już nie czeka (ostatnia odpowiedź albo nowa migawka bez decyzji) — okno kończy.
    onPendingChanged: {
        if (index > 0 && index >= pending.length) index = Math.max(0, pending.length - 1)
        if (visible && pending.length === 0) finished()
    }

    color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.62)
    focus: visible
    Accessible.role: Accessible.Dialog
    Accessible.name: current ? current.happened : "Decyzja"

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            laterRequested()
            event.accepted = true
        } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            choose()
            event.accepted = true
        }
    }

    // Kliknięcia obok okna nic nie robią (okno modalne).
    MouseArea {
        anchors.fill: parent
        onWheel: wheel => wheel.accepted = true
    }

    Rectangle {
        id: panel
        objectName: "decisionPanel"
        anchors.centerIn: parent
        width: Math.min(720, parent.width - 48)
        height: Math.min(body.implicitHeight + 44 + footer.implicitHeight, parent.height - 32)
        radius: Tide.Tokens.radiusCard
        color: Tide.Tokens.backgroundWindow
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityOutline)
        clip: true

        MouseArea { anchors.fill: parent; onClicked: dialog.forceActiveFocus() }

        Flickable {
            id: flick
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: footer.top
            anchors.margins: 22
            anchors.bottomMargin: 10
            contentHeight: body.implicitHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: flick.contentHeight > flick.height ? QQC2.ScrollBar.AsNeeded : QQC2.ScrollBar.AlwaysOff }

            ColumnLayout {
                id: body
                width: flick.width
                spacing: 12

                // Nagłówek: plakietka rodzaju, kto pyta, licznik i nawigacja, zamknięcie.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Rectangle {
                        objectName: "decisionKind"
                        implicitWidth: kindText.implicitWidth + 16
                        implicitHeight: kindText.implicitHeight + 8
                        radius: Tide.Tokens.radiusSmall
                        color: Tide.Theme.alpha(Tide.Tokens.waiting, 0.14)
                        border.width: 1
                        border.color: Tide.Theme.alpha(Tide.Tokens.waiting, 0.4)
                        Text {
                            id: kindText
                            anchors.centerIn: parent
                            text: Decision.kindLabel(dialog.current)
                            textFormat: Text.PlainText
                            color: Tide.Tokens.waiting
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.9
                            font.weight: Font.DemiBold
                        }
                    }
                    Text {
                        objectName: "decisionWho"
                        Layout.fillWidth: true
                        text: Decision.who(dialog.cardId, dialog.current)
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.95
                        elide: Text.ElideRight
                    }
                    Tide.TextButton {
                        objectName: "decisionPrev"
                        visible: dialog.counter !== ""
                        enabled: dialog.index > 0
                        opacity: enabled ? 1 : 0.4
                        text: "‹"
                        Accessible.name: "Poprzednia decyzja"
                        onClicked: dialog.go(-1)
                    }
                    Text {
                        objectName: "decisionCounter"
                        visible: dialog.counter !== ""
                        text: dialog.counter
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.95
                        Accessible.role: Accessible.StaticText
                        Accessible.name: "Decyzja " + text
                    }
                    Tide.TextButton {
                        objectName: "decisionNext"
                        visible: dialog.counter !== ""
                        enabled: dialog.index < dialog.pending.length - 1
                        opacity: enabled ? 1 : 0.4
                        text: "›"
                        Accessible.name: "Następna decyzja"
                        onClicked: dialog.go(1)
                    }
                    Tide.TextButton {
                        objectName: "decisionClose"
                        text: "✕"
                        variant: "link"
                        accent: Tide.Tokens.textMuted
                        Accessible.name: "Później (Esc)"
                        onClicked: dialog.laterRequested()
                    }
                }

                Text {
                    objectName: "decisionHappened"
                    Layout.fillWidth: true
                    text: dialog.current ? dialog.current.happened : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 1.35
                    font.weight: Font.DemiBold
                    wrapMode: Text.Wrap
                }
                Text {
                    objectName: "decisionWhy"
                    Layout.fillWidth: true
                    text: dialog.current ? dialog.current.why : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 1.05
                    wrapMode: Text.Wrap
                }
                Rectangle {
                    objectName: "decisionCommand"
                    Layout.fillWidth: true
                    visible: commandText.text !== ""
                    implicitHeight: commandText.implicitHeight + 18
                    radius: Tide.Tokens.radiusFrame
                    color: Tide.Tokens.backgroundView
                    border.width: 1
                    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
                    Text {
                        id: commandText
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.margins: 12
                        text: dialog.current && dialog.current.technical ? Decision.text(dialog.current.technical.command) : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.monoFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.95
                        wrapMode: Text.WrapAnywhere
                    }
                }

                // Polecenia łańcucha (PT-124): pole wyboru tylko przy poleceniach, na które można zezwolić;
                // przy pozostałych zawsze widać powód. Flagi i zaznaczenie początkowe — z usługi.
                ColumnLayout {
                    objectName: "decisionCommands"
                    Layout.fillWidth: true
                    visible: dialog.showCommands
                    spacing: 6
                    Accessible.role: Accessible.List
                    Accessible.name: "Polecenia z łańcucha"

                    Repeater {
                        model: dialog.showCommands ? dialog.commands : []
                        delegate: Rectangle {
                            id: commandRow
                            required property var modelData
                            required property int index
                            readonly property bool allowable: modelData.canAllow === true
                            readonly property bool checked: dialog.checks.indexOf(index) >= 0
                            readonly property string text: modelData.text
                            readonly property string reason: modelData.reason || ""
                            readonly property var tags: Decision.commandTags(modelData)
                            objectName: "decisionCommandRow"
                            Layout.fillWidth: true
                            implicitHeight: commandColumn.implicitHeight + 16
                            radius: Tide.Tokens.radiusFrame
                            color: checked ? Tide.Theme.alpha(Tide.Tokens.accent, 0.09) : Tide.Tokens.backgroundView
                            border.width: 1
                            border.color: activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                      : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
                            activeFocusOnTab: allowable
                            Accessible.role: allowable ? Accessible.CheckBox : Accessible.StaticText
                            Accessible.name: text
                            Accessible.description: reason
                            Accessible.checkable: allowable
                            Accessible.checked: checked
                            Accessible.onToggleAction: dialog.toggleCommand(index)
                            Keys.onSpacePressed: dialog.toggleCommand(index)

                            MouseArea {
                                anchors.fill: parent
                                enabled: commandRow.allowable
                                cursorShape: commandRow.allowable ? Qt.PointingHandCursor : Qt.ArrowCursor
                                onClicked: dialog.toggleCommand(commandRow.index)
                            }

                            RowLayout {
                                id: commandColumn
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 10
                                spacing: 9
                                Rectangle {
                                    objectName: "decisionCommandCheck"
                                    visible: commandRow.allowable
                                    Layout.alignment: Qt.AlignTop
                                    implicitWidth: 14
                                    implicitHeight: 14
                                    radius: Tide.Tokens.radiusSmall / 3
                                    color: commandRow.checked ? Tide.Tokens.accent : "transparent"
                                    border.width: 1.5
                                    border.color: commandRow.checked ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.35)
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 3
                                    Text {
                                        objectName: "decisionCommandText"
                                        Layout.fillWidth: true
                                        text: commandRow.text
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textPrimary
                                        font.family: Tide.Theme.monoFamily
                                        font.pointSize: Tide.Tokens.fontSizePt * 0.95
                                        wrapMode: Text.WrapAnywhere
                                    }
                                    Text {
                                        objectName: "decisionCommandReason"
                                        Layout.fillWidth: true
                                        visible: commandRow.reason !== ""
                                        text: commandRow.reason
                                        textFormat: Text.PlainText
                                        color: commandRow.modelData.forbidden === true ? Tide.Tokens.negative : Tide.Tokens.textSecondary
                                        font.family: Tide.Theme.uiFamily
                                        font.pointSize: Tide.Tokens.fontSizePt * 0.9
                                        wrapMode: Text.Wrap
                                    }
                                }
                                Repeater {
                                    model: commandRow.tags
                                    delegate: Rectangle {
                                        id: tag
                                        required property string modelData
                                        objectName: "decisionCommandTag"
                                        readonly property string label: modelData
                                        Layout.alignment: Qt.AlignTop
                                        implicitWidth: tagText.implicitWidth + 12
                                        implicitHeight: tagText.implicitHeight + 4
                                        radius: Tide.Tokens.radiusSmall
                                        color: Tide.Theme.alpha(modelData === "zabronione" ? Tide.Tokens.negative : Tide.Tokens.textPrimary, 0.08)
                                        Text {
                                            id: tagText
                                            anchors.centerIn: parent
                                            text: tag.modelData
                                            textFormat: Text.PlainText
                                            color: tag.modelData === "zabronione" ? Tide.Tokens.negative : Tide.Tokens.textMuted
                                            font.family: Tide.Theme.uiFamily
                                            font.pointSize: Tide.Tokens.fontSizePt * 0.85
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                Text {
                    objectName: "decisionError"
                    Layout.fillWidth: true
                    visible: dialog.error !== ""
                    text: dialog.error
                    textFormat: Text.PlainText
                    color: Tide.Tokens.negative
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    wrapMode: Text.Wrap
                }

                // Możliwości w kolejności z usługi; „Inna odpowiedź…” zawsze ostatnia.
                ColumnLayout {
                    objectName: "decisionOptions"
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    spacing: 8
                    Accessible.role: Accessible.Grouping
                    Accessible.name: "Możliwości"

                    Repeater {
                        id: optionRepeater
                        model: dialog.options
                        DecisionOption {
                            objectName: "decisionOption"
                            Layout.fillWidth: true
                            title: modelData.title
                            effect: modelData.effect
                            recommended: modelData.recommended === true
                            checked: dialog.selected === modelData.id
                            onPicked: dialog.pick(modelData.id)
                        }
                    }

                    Rectangle {
                        id: other
                        objectName: "decisionOther"
                        Layout.fillWidth: true
                        visible: dialog.freeText
                        readonly property bool checked: dialog.choice === dialog.otherId
                        implicitHeight: otherColumn.implicitHeight + 24
                        radius: Tide.Tokens.radiusTile
                        color: checked && !dialog.onlyText ? Tide.Theme.alpha(Tide.Tokens.accent, 0.09) : Tide.Tokens.backgroundCard
                        border.width: 1
                        border.color: checked && !dialog.onlyText || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                                                 : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
                        activeFocusOnTab: !dialog.onlyText
                        Accessible.role: Accessible.RadioButton
                        Accessible.name: dialog.otherTitle
                        Accessible.checked: checked
                        Accessible.onPressAction: dialog.pick(dialog.otherId)
                        Keys.onSpacePressed: dialog.pick(dialog.otherId)

                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: dialog.pick(dialog.otherId)
                        }

                        ColumnLayout {
                            id: otherColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 12
                            anchors.leftMargin: 14
                            spacing: 8
                            RowLayout {
                                spacing: 9
                                visible: !dialog.onlyText
                                Rectangle {
                                    implicitWidth: 14
                                    implicitHeight: 14
                                    radius: width / 2
                                    color: "transparent"
                                    border.width: other.checked ? 4 : 1.5
                                    border.color: other.checked ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.35)
                                }
                                Text {
                                    objectName: "decisionOtherTitle"
                                    text: dialog.otherTitle
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textPrimary
                                    font.family: Tide.Theme.uiFamily
                                    font.pointSize: Tide.Tokens.fontSizePt * 1.1
                                    font.weight: Font.DemiBold
                                }
                            }
                            Text {
                                visible: dialog.onlyText
                                text: "Twoja odpowiedź"
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textSecondary
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt
                                font.weight: Font.Medium
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.leftMargin: dialog.onlyText ? 0 : 23
                                visible: dialog.onlyText || other.checked
                                implicitHeight: Math.max(64, answer.implicitHeight + 16)
                                radius: Tide.Tokens.radiusFrame
                                color: Tide.Tokens.backgroundView
                                border.width: 1
                                border.color: answer.activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                                 : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityOutline)
                                TextEdit {
                                    id: answer
                                    objectName: "decisionAnswer"
                                    anchors.fill: parent
                                    anchors.margins: 8
                                    textFormat: TextEdit.PlainText
                                    wrapMode: TextEdit.Wrap
                                    color: Tide.Tokens.textPrimary
                                    selectionColor: Tide.Theme.alpha(Tide.Tokens.accent, 0.4)
                                    font.family: Tide.Theme.uiFamily
                                    font.pointSize: Tide.Tokens.fontSizePt * 1.05
                                    activeFocusOnTab: true
                                    Accessible.role: Accessible.EditableText
                                    Accessible.name: "Inna odpowiedź"
                                    onActiveFocusChanged: if (activeFocus && !dialog.onlyText) dialog.selected = dialog.otherId
                                    // Enter wysyła jak w czacie, Shift+Enter — nowa linia; Esc zamyka okno.
                                    Keys.onPressed: event => {
                                        if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                                && !(event.modifiers & Qt.ShiftModifier)) {
                                            dialog.choose()
                                            event.accepted = true
                                        } else if (event.key === Qt.Key_Escape) {
                                            dialog.laterRequested()
                                            event.accepted = true
                                        }
                                    }
                                    Text {
                                        anchors.fill: parent
                                        visible: answer.text === ""
                                        text: dialog.current && dialog.current.otherHint ? dialog.current.otherHint
                                                                                         : "Odpowiedz własnymi słowami, jak w czacie z agentem."
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textMuted
                                        font: answer.font
                                        wrapMode: Text.Wrap
                                    }
                                }
                            }
                        }
                    }
                }

                // „Zezwól na wybrane” bez zaznaczonego polecenia: „Wybierz” jest nieaktywne, więc mówimy dlaczego.
                Text {
                    objectName: "decisionAllowHint"
                    Layout.fillWidth: true
                    visible: dialog.picksCommands && dialog.checks.length === 0
                    text: "Zaznacz co najmniej jedno polecenie powyżej, na które zezwalasz."
                    textFormat: Text.PlainText
                    color: Tide.Tokens.waiting
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    wrapMode: Text.Wrap
                }

                // Szczegóły techniczne — zwinięte.
                Tide.TextButton {
                    objectName: "decisionTechToggle"
                    visible: techRepeater.count > 0
                    text: (dialog.techOpen ? "▾ " : "▸ ") + "Szczegóły techniczne"
                    accent: Tide.Tokens.textMuted
                    onClicked: dialog.techOpen = !dialog.techOpen
                }
                GridLayout {
                    objectName: "decisionTech"
                    Layout.fillWidth: true
                    visible: dialog.techOpen
                    columns: 2
                    columnSpacing: 14
                    rowSpacing: 4
                    Repeater {
                        id: techRepeater
                        model: dialog.current ? Decision.techRows(dialog.current.technical) : []
                        delegate: Item {
                            id: techRow
                            required property var modelData
                            Layout.columnSpan: 2
                            Layout.fillWidth: true
                            implicitHeight: Math.max(techLabel.implicitHeight, techValue.implicitHeight)
                            Text {
                                id: techLabel
                                width: 150
                                text: techRow.modelData.label
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                                elide: Text.ElideRight
                            }
                            Text {
                                id: techValue
                                x: techLabel.width + 14
                                width: techRow.width - x
                                text: techRow.modelData.value
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textSecondary
                                font.family: Tide.Theme.monoFamily
                                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                                wrapMode: Text.WrapAnywhere
                            }
                        }
                    }
                }
            }
        }

        // Stopka: „Otwórz całą kartę” po lewej, „Później” i „Wybierz” po prawej.
        RowLayout {
            id: footer
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: 18
            anchors.leftMargin: 22
            spacing: 10
            Tide.TextButton {
                objectName: "decisionFullCard"
                text: "Otwórz całą kartę"
                onClicked: dialog.fullCardRequested("")
            }
            Item { Layout.fillWidth: true }
            Tide.TextButton {
                objectName: "decisionLater"
                text: "Później  Esc"
                variant: "outline"
                Accessible.name: "Później"
                onClicked: dialog.laterRequested()
            }
            Tide.TextButton {
                objectName: "decisionChoose"
                text: "Wybierz  ↵"
                variant: "filled"
                enabled: dialog.canChoose
                opacity: enabled ? 1 : 0.45
                Accessible.name: "Wybierz"
                onClicked: dialog.choose()
            }
        }
    }
}

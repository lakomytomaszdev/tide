// Okno ustawień ticketu (PT-334, PT-335, K-97). Otwiera się z przycisku „Ustawienia” na kafelku planu pełnej karty
// Tablicy. Trzy tryby, rozstrzygane z wiersza planu i kafelka ticketu:
// - ticket nieruszony (`row.editable === true`, PT-334): wykonawca, recenzent i klasa, zapis przez
//   `EditCardPlan {action: "edit", ticket, executor, reviewer, class, body}` z niezmienioną kartką;
// - ticket trwający (PT-335): klasa tylko do odczytu i dwa pola, każde z osobnym zapisem przez istniejące drogi
//   `ResolveCardStep`: „Autor kolejnych kroków” → `{choice: "reassign-author", ticket, executor}`, „Recenzent” →
//   `{choice: "reassign-reviewer", ticket, executor}`. Pole jest wyszarzone ze zdaniem, gdy usługa by odmówiła
//   (warunki: `Assignment.authorLock` / `reviewerLock`, liczone na bieżąco z `flow`);
// - gdy czeka decyzja `error` tego ticketu z możliwością `change-executor` (PT-341): trzecie pole „Ponów innym
//   wykonawcą”. Idzie tą samą drogą co okno decyzji: `params` możliwości z `flow.decisions[]` (w tym `step`) i wybrany
//   wykonawca, jedno `ResolveCardStep` (assignment.js, `retryRequest`). Bez takiej decyzji pola nie ma;
// - ticket scalony albo odrzucony (PT-341): wykonawca, recenzent i klasa tylko do odczytu ze zdaniem dlaczego, bez
//   żadnego zapisu; ticket bez wiersza planu i nieskończony: samo zdanie „już ruszył”;
// - karta „Od razu” (PT-341): strona podaje wiersz zastępczy (`noPlanRow: true`) — klasa to domyślny „standard”,
//   pokazany tylko do odczytu z dopiskiem, że karta nie ma wiersza planu.
//
// Umowa: `ticketId`, `row` (wiersz `derived.flow.plan.rows[]`), `flow` (`derived.flow`), `tickets`
// (`derived.tickets[]`), `modelsStatus` (Status() z /Models). `open(id, row)` otwiera okno, `close()` zamyka.
// Tryb nieruszony: `busy` i `error` ustawia strona; sygnał `saveRequested(request)`. Tryb trwający: sygnał
// `reassignRequested(field, request)` (`field` — "author", "reviewer" albo "retry"; dla "retry" `request` to
// `params` do `ResolveCardStep`), odpowiedź wraca przez `fieldReplied(field, error, note)` przy tym polu
// (sukces, `not-waiting`, `bad-executor`, `already-decided`, `no-step-question`…), okno się nie zamyka. Esc
// i „Anuluj” niczego nie wysyłają.
//
// Reguły przydziału: apps/common/qml/card/assignment.js (czyste funkcje). Listy mają wszystkie pozycje katalogu;
// niedozwolone są wyszarzone z powodem.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "card/assignment.js" as Assignment

Rectangle {
    id: dialog

    property string ticketId: ""
    property var row: null
    property var flow: null
    property var tickets: []
    property var modelsStatus: null
    property bool busy: false
    property string error: ""
    property var selection: ({ executor: "", reviewer: "", "class": "" })

    readonly property var ticket: Array.isArray(tickets) ? tickets.find(t => t && t.id === ticketId) || null : null
    readonly property bool editable: !!row && row.editable === true
    readonly property bool finished: !!ticket && (ticket.status === "merged" || ticket.status === "rejected")
    // Ticket trwający: ma wiersz planu, nie jest już edytowalny i nie jest scalony ani odrzucony.
    readonly property bool running: !!row && !editable && !finished
    readonly property bool started: !editable && !running && !finished
    // Karta „Od razu” nie ma wiersza planu z klasą: obowiązuje domyślny „standard” (założenie karty K-97).
    readonly property bool defaultClass: !!row && row.noPlanRow === true
    readonly property string planClass: row && row["class"] ? row["class"] : defaultClass ? "standard" : ""

    readonly property var lists: Assignment.options(modelsStatus, selection)
    readonly property var verdict: editable ? Assignment.verdict(modelsStatus, selection, {
        executor: row.executor || "", reviewer: row.reviewer || "", "class": row["class"] || "" }) : { ok: false, reason: "" }
    readonly property bool canSave: editable && verdict.ok && !busy

    // --- tryb trwający -----------------------------------------------------------------------------------------
    readonly property var current: ({
        author: ticket && ticket.executor || row && row.executor || "",
        reviewer: ticket && ticket.reviewer || row && row.reviewer || "" })
    property var runSelection: ({ author: "", reviewer: "", retry: "" })
    // Stan pól: {busy, error, note} osobno dla autora, recenzenta i „Ponów innym wykonawcą”.
    property var fields: ({ author: {}, reviewer: {}, retry: {} })
    readonly property var runLists: Assignment.runningOptions(modelsStatus, current, planClass)
    readonly property string authorLock: running ? Assignment.authorLock(flow, ticketId) : ""
    readonly property string reviewerLock: running ? Assignment.reviewerLock(flow, ticketId) : ""
    // „Ponów innym wykonawcą” (PT-341): decyzja błędu tego ticketu z możliwością `change-executor` albo null.
    // Po zapisie decyzja znika z migawki; pole zostaje z odpowiedzią usługi, dopóki okno jest otwarte.
    readonly property var liveRetry: running ? Assignment.retryDecision(flow, ticketId) : null
    property var retryKept: null
    onLiveRetryChanged: if (liveRetry) retryKept = liveRetry
    readonly property var retry: liveRetry || (fields.retry && (fields.retry.note || fields.retry.busy) ? retryKept : null)
    readonly property string retryLock: retry ? Assignment.retryLock(flow, ticketId) : ""
    readonly property var retryList: retry ? Assignment.retryOptions(modelsStatus, retry.decision, current) : []
    readonly property string finishedSentence: ticket && ticket.status === "rejected"
        ? "Ticket został odrzucony — wszystkie pola są tylko do odczytu."
        : "Ticket jest scalony do gałęzi karty — wszystkie pola są tylko do odczytu."

    signal saveRequested(var request)
    signal reassignRequested(string field, var request)
    signal closed()

    function open(id, planRow) {
        ticketId = id
        row = planRow
        error = ""
        busy = false
        selection = { executor: planRow && planRow.executor || "", reviewer: planRow && planRow.reviewer || "",
                      "class": planRow && planRow["class"] || "" }
        runSelection = { author: current.author, reviewer: current.reviewer, retry: "" }
        fields = { author: {}, reviewer: {}, retry: {} }
        retryKept = liveRetry
        visible = true
        forceActiveFocus()
    }

    function close() {
        if (!visible) return
        visible = false
        busy = false
        error = ""
        fields = { author: {}, reviewer: {}, retry: {} }
        retryKept = null
        closed()
    }

    // Zmiana wykonawcy albo klasy przelicza wyszarzenia; recenzent zostaje tylko wtedy, gdy nadal jest dozwolony.
    function choose(group, value) {
        if (busy) return
        const next = Object.assign({}, selection)
        next[group] = value
        selection = group === "reviewer" ? next : Assignment.keepReviewer(modelsStatus, next)
        error = ""
    }

    function save() {
        if (!canSave) return
        error = ""
        busy = true
        saveRequested(Assignment.editRequest(row, selection))
    }

    function lockOf(field) {
        return field === "author" ? authorLock : field === "retry" ? retryLock : reviewerLock
    }

    function fieldVerdict(field) {
        if (field === "retry")
            return retry ? Assignment.retryVerdict(modelsStatus, runSelection.retry, retry.decision, current)
                         : { ok: false, reason: "" }
        return Assignment.runningVerdict(modelsStatus, field, runSelection[field], current, planClass)
    }

    // „Ponów” po udanym zapisie jest zużyte (decyzja już zapisana); autora i recenzenta wolno zapisać ponownie.
    function canReassign(field) {
        return running && lockOf(field) === "" && !fields[field].busy && !(field === "retry" && fields.retry.note)
            && fieldVerdict(field).ok
    }

    function setField(field, state) {
        const next = Object.assign({}, fields)
        next[field] = state
        fields = next
    }

    function chooseRunning(field, value) {
        if (fields[field].busy || lockOf(field) !== "" || (field === "retry" && fields.retry.note)) return
        const next = Object.assign({}, runSelection)
        next[field] = value
        runSelection = next
        setField(field, {})
    }

    function reassign(field) {
        if (!canReassign(field)) return
        setField(field, { busy: true, sent: runSelection[field] })
        reassignRequested(field, field === "retry" ? Assignment.retryRequest(retry.option, runSelection.retry).params
                                                   : Assignment.reassignRequest(field, ticketId, runSelection[field]))
    }

    // Odpowiedź usługi przy polu: błąd (`not-waiting`, `bad-executor`, `already-decided`…) albo potwierdzenie
    // z nazwą wykonawcy z odpowiedzi (`name`; pusta — ten, który okno wysłało).
    function fieldReplied(field, failure, name) {
        if (!visible || !fields[field] || !fields[field].busy) return
        const who = name || fields[field].sent || ""
        setField(field, failure ? { error: failure } : { note: field === "author"
            ? "Zapisano: kolejne kroki autora poprowadzi " + who + "."
            : field === "retry" ? "Zapisano: krok ponowi " + who + "."
            : "Zapisano: review poprowadzi " + who + "." })
    }

    visible: false
    color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.62)
    focus: visible
    Keys.onEscapePressed: dialog.close()
    Keys.onReturnPressed: dialog.save()
    Keys.onEnterPressed: dialog.save()
    Accessible.role: Accessible.Dialog
    Accessible.name: "Ustawienia ticketu " + ticketId

    // Lista jednej grupy wyboru: wszystkie pozycje katalogu, niedozwolone wyszarzone z powodem. `locked` — całe pole
    // nieaktywne (ticket trwający, usługa by odmówiła).
    component ChoiceList: ColumnLayout {
        id: list
        property string groupKey: ""
        property string title: ""
        property var entries: []
        property string selected: ""
        property bool locked: false
        signal picked(string value)
        spacing: 4
        Text {
            text: list.title
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt * 0.9
            font.weight: Font.DemiBold
        }
        Repeater {
            model: list.entries.length
            delegate: Rectangle {
                id: choice
                required property int index
                readonly property var entry: list.entries[index]
                readonly property string groupKey: list.groupKey
                readonly property string value: entry.value
                readonly property bool disabled: entry.disabled
                readonly property string reason: entry.reason
                readonly property bool checked: list.selected === entry.value
                readonly property bool pickable: !disabled && !list.locked
                objectName: "ticketSettingsOption"
                Layout.fillWidth: true
                implicitHeight: choiceColumn.implicitHeight + 14
                radius: Tide.Tokens.radiusSmall
                color: checked ? Tide.Theme.alpha(Tide.Tokens.accent, 0.1) : "transparent"
                border.width: 1
                border.color: checked || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                     : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
                opacity: disabled || list.locked ? 0.55 : 1
                activeFocusOnTab: pickable
                Accessible.role: Accessible.RadioButton
                Accessible.name: value + (disabled ? " — niedostępne: " + reason : "")
                Accessible.checked: checked
                Accessible.onPressAction: pick()
                Keys.onSpacePressed: pick()
                function pick() {
                    if (pickable) list.picked(value)
                }
                ColumnLayout {
                    id: choiceColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.leftMargin: 12
                    anchors.rightMargin: 12
                    spacing: 2
                    Text {
                        Layout.fillWidth: true
                        text: (choice.checked ? "● " : "○ ") + choice.entry.label
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        elide: Text.ElideRight
                    }
                    Text {
                        objectName: "ticketSettingsReason"
                        Layout.fillWidth: true
                        Layout.leftMargin: 18
                        visible: choice.disabled
                        text: choice.reason
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.85
                        wrapMode: Text.Wrap
                    }
                }
                MouseArea {
                    anchors.fill: parent
                    enabled: choice.pickable
                    cursorShape: Qt.PointingHandCursor
                    onClicked: {
                        choice.forceActiveFocus()
                        choice.pick()
                    }
                }
            }
        }
    }

    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        onClicked: dialog.forceActiveFocus()
    }

    Rectangle {
        id: card
        objectName: "ticketSettingsCard"
        anchors.centerIn: parent
        width: Math.min(980, parent.width - 48)
        height: Math.min(parent.height - 48, body.implicitHeight + 40)
        radius: Tide.Tokens.radiusTile
        color: Tide.Tokens.backgroundPanel
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)

        ColumnLayout {
            id: body
            anchors.fill: parent
            anchors.margins: 20
            spacing: 12

            Text {
                objectName: "ticketSettingsTitle"
                Layout.fillWidth: true
                text: "Ustawienia ticketu " + dialog.ticketId + (dialog.row && dialog.row.title ? " — " + dialog.row.title : "")
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 1.15
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
                maximumLineCount: 2
                elide: Text.ElideRight
            }

            Text {
                objectName: "ticketSettingsStarted"
                Layout.fillWidth: true
                visible: dialog.started
                text: "Ticket już ruszył — zmiana w następnym kroku"
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                wrapMode: Text.Wrap
            }

            Text {
                objectName: "ticketSettingsClass"
                Layout.fillWidth: true
                visible: dialog.running
                text: "Klasa: " + (dialog.planClass || "standard") + " — w trakcie ticketu się nie zmienia"
                      + (dialog.defaultClass ? " (klasa domyślna: karta „Od razu” nie ma wiersza planu z klasą)" : "")
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                wrapMode: Text.Wrap
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }

            // Ticket scalony albo odrzucony (PT-341): wartości tylko do odczytu i zdanie dlaczego; nic się nie wysyła.
            Text {
                objectName: "ticketSettingsFinished"
                Layout.fillWidth: true
                visible: dialog.finished
                text: dialog.finishedSentence
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                wrapMode: Text.Wrap
                Accessible.role: Accessible.StaticText
                Accessible.name: text
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: dialog.finished
                spacing: 4
                Repeater {
                    model: dialog.finished ? [
                        { label: "Wykonawca", value: dialog.current.author },
                        { label: "Recenzent", value: dialog.current.reviewer },
                        { label: "Klasa", value: dialog.planClass + (dialog.defaultClass ? " (klasa domyślna)" : "") }
                    ] : []
                    delegate: Text {
                        required property var modelData
                        objectName: "ticketSettingsReadonly"
                        Layout.fillWidth: true
                        text: modelData.label + ": " + (modelData.value || "—")
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        wrapMode: Text.Wrap
                        Accessible.role: Accessible.StaticText
                        Accessible.name: text
                    }
                }
            }

            Flickable {
                id: scroller
                visible: dialog.editable || dialog.running
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredHeight: (dialog.running ? runningGroups.implicitHeight : groups.implicitHeight)
                contentHeight: dialog.running ? runningGroups.implicitHeight : groups.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                QQC2.ScrollBar.vertical: Tide.ScrollBar {}

                // Ticket nieruszony (PT-334): trzy grupy, jeden zapis.
                RowLayout {
                    id: groups
                    visible: dialog.editable
                    width: scroller.width
                    spacing: 12
                    Repeater {
                        model: dialog.editable ? [
                            { key: "executor", title: "Wykonawca", list: "executors" },
                            { key: "reviewer", title: "Recenzent", list: "reviewers" },
                            { key: "class", title: "Klasa", list: "classes" }
                        ] : []
                        delegate: ChoiceList {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            Layout.alignment: Qt.AlignTop
                            groupKey: modelData.key
                            title: modelData.title
                            entries: dialog.lists[modelData.list]
                            selected: dialog.selection[modelData.key]
                            onPicked: value => dialog.choose(groupKey, value)
                        }
                    }
                }

                // Ticket trwający (PT-335): dwa pola, każde z własnym zapisem i odpowiedzią usługi.
                RowLayout {
                    id: runningGroups
                    visible: dialog.running
                    width: scroller.width
                    spacing: 16
                    Repeater {
                        model: dialog.running ? [
                            { key: "author", title: "Autor kolejnych kroków", list: "authors", save: "Zapisz autora" },
                            { key: "reviewer", title: "Recenzent", list: "reviewers", save: "Zapisz recenzenta" }
                        ].concat(dialog.retry ? [
                            { key: "retry", title: "Ponów innym wykonawcą", list: "retry", save: "Ponów innym wykonawcą" }
                        ] : []) : []
                        delegate: ColumnLayout {
                            id: field
                            required property var modelData
                            readonly property string key: modelData.key
                            readonly property string lock: dialog.lockOf(key)
                            readonly property var info: dialog.fields[key] || {}
                            readonly property var check: dialog.fieldVerdict(key)
                            objectName: "ticketSettingsField"
                            Layout.fillWidth: true
                            Layout.preferredWidth: 1
                            Layout.alignment: Qt.AlignTop
                            spacing: 8
                            ChoiceList {
                                Layout.fillWidth: true
                                groupKey: field.key
                                title: field.modelData.title
                                entries: field.modelData.list === "retry" ? dialog.retryList
                                                                          : dialog.runLists[field.modelData.list]
                                selected: dialog.runSelection[field.key]
                                locked: field.lock !== ""
                                onPicked: value => dialog.chooseRunning(field.key, value)
                            }
                            Text {
                                objectName: "ticketSettingsLock"
                                Layout.fillWidth: true
                                visible: field.lock !== ""
                                text: field.lock
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textSecondary
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                                wrapMode: Text.Wrap
                            }
                            Text {
                                objectName: "ticketSettingsFieldWhy"
                                Layout.fillWidth: true
                                visible: field.lock === "" && !field.check.ok && !field.info.error
                                text: field.check.reason
                                textFormat: Text.PlainText
                                color: Tide.Tokens.butter
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                                wrapMode: Text.Wrap
                            }
                            Text {
                                objectName: "ticketSettingsFieldError"
                                Layout.fillWidth: true
                                visible: !!field.info.error
                                text: field.info.error || ""
                                textFormat: Text.PlainText
                                color: Tide.Tokens.rose
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                                wrapMode: Text.Wrap
                            }
                            Text {
                                objectName: "ticketSettingsFieldNote"
                                Layout.fillWidth: true
                                visible: !!field.info.note
                                text: field.info.note || ""
                                textFormat: Text.PlainText
                                color: Tide.Tokens.mint
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                                wrapMode: Text.Wrap
                            }
                            Tide.Button {
                                objectName: "ticketSettingsFieldSave"
                                property string fieldKey: field.key
                                Layout.alignment: Qt.AlignRight
                                text: field.info.busy ? "Zapisuję…" : field.modelData.save
                                enabled: dialog.canReassign(field.key)
                                onClicked: dialog.reassign(field.key)
                            }
                        }
                    }
                }
            }

            Text {
                objectName: "ticketSettingsWhy"
                Layout.fillWidth: true
                visible: dialog.editable && !dialog.verdict.ok && dialog.error === ""
                text: dialog.verdict.reason
                textFormat: Text.PlainText
                color: Tide.Tokens.butter
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                wrapMode: Text.Wrap
            }
            Text {
                objectName: "ticketSettingsError"
                Layout.fillWidth: true
                visible: dialog.error !== ""
                text: dialog.error
                textFormat: Text.PlainText
                color: Tide.Tokens.rose
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                wrapMode: Text.Wrap
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Item { Layout.fillWidth: true }
                Tide.Button {
                    objectName: "ticketSettingsCancel"
                    text: dialog.editable ? "Anuluj" : "Zamknij"
                    onClicked: dialog.close()
                }
                Tide.Button {
                    objectName: "ticketSettingsSave"
                    visible: dialog.editable
                    text: dialog.busy ? "Zapisuję…" : "Zapisz"
                    variant: "primary"
                    enabled: dialog.canSave
                    onClicked: dialog.save()
                }
            }
        }
    }
}

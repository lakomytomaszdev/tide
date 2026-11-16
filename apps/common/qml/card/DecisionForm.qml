// Treść jednej decyzji karty (K-35): „co się stało” (`happened`), „dlaczego” (`why`), polecenie z
// `technical.command`, uwagi recenzenta planu, możliwości jako karty z jednym zdaniem skutku (kolejność i polecona —
// z usługi), na końcu „Inna odpowiedź…” (gdy `freeText`) i zwinięte „Szczegóły techniczne”. Wspólna dla okna
// decyzji (DecisionDialog.qml, PT-132) i bloku „Twój ruch” pełnej karty (CardFullView.qml, PT-128).
//
// Formularz niczego nie liczy i niczego nie zaznacza z góry (D10): po zmianie decyzji nic nie jest wybrane, a
// `choose()` bez zaznaczenia (albo z pustą własną odpowiedzią) nic nie robi. Żądanie składa decision.js z gotowych
// pól `method` i `params` decyzji.
//
// Umowa: `decision` (jedna decyzja z `derived.flow.decisions[]`), `canChoose`, `choose()`, `error` (np. błąd usługi
// po odpowiedzi), sygnały `answered(decisionId, method, params)`, `inputNeeded(notice)` — możliwość wymaga wyboru
// spoza formularza (np. wykonawcy; na razie w panelu karty) i `escapePressed()` z pola odpowiedzi.
//
// Błąd Tide (PT-125): pole własnej odpowiedzi ma podpis z usługi („Opisz problem i ponów”, `reply.label`). Gdy
// `reply.preview` jest ustawione, pierwszy wybór wysyła `previewRequested(decisionId, method, params)`, właściciel
// oddaje odpowiedź usługi przez `showPreview(decisionId, reply)` (karta z opisem i wycinkiem dziennika), a dopiero
// drugi wybór („Zapisz i ponów”) wysyła `answered` z `reply.method`. Zmiana opisu unieważnia podgląd.
//
// Zgoda na łańcuch poleceń (PT-124): lista `commands[]` z flagami i powodem blokady, pole wyboru tylko przy
// poleceniach z `canAllow`, zaznaczenie początkowe z `preselected`; możliwość z `params.allow` wysyła indeksy
// zaznaczonych (`checks`), bez zaznaczenia nie da się wybrać. Podpis pola własnej odpowiedzi — `otherTitle`.
//
// Grupa zgód (PT-338, `decision.group` z decision.consentGroups): ta sama prośba poza piaskownicą z N kroków.
// Rozwijana lista ticketów i kroków grupy, możliwości z usługi z opisem dla N kroków, prognoza „ruszy od razu /
// poczeka” (`decision.forecast`, PT-340; pusta bez `outsideLoad` — wtedy zostaje sama liczba ticketów). Wybór emituje `groupAnswered(ids)`, a potem `answered` raz na każdą decyzję grupy (jej
// `method` i `params` z jej `step`). Pozycja `decision.receipt` to znak przyjęcia („Przyjęto · wysyłam N odpowiedzi”)
// bez możliwości. `errors` — powody odmowy usługi przy decyzjach, które wróciły z grupy osobno.
//
// Odstępstwo przy błędzie domknięcia (PT-339, deviation.js): gdy decyzja to błąd kroku `close` z możliwością `retry`
// usługi, po możliwościach usługi stoi własna możliwość okna „Przyjmij odstępstwo…” — niepolecona, niezaznaczona,
// z polem uzasadnienia i licznikiem. Bez uzasadnienia albo ponad limit „Wybierz” jest nieaktywne ze zdaniem dlaczego;
// wybór wysyła jedno `answered` z `retry` tej decyzji i `answer` ze stałą instrukcją i uzasadnieniem.
//
// Odświeżenie K-128 (PT-506, makieta S-9 „Prośba o zgodę”, wariant A) — tylko formatowanie i układ (granica K-109):
// te same metody, parametry, grupy i odpowiedzi. Tytuł jako pytanie (`Decision.questionTitle`, pod nim `happened`),
// jedno zalecenie zamiast bloku „Opiekun poleca” (`Decision.advice`: dopisek opiekuna przy opcji albo jedno zdanie
// pod możliwościami, komentarz rozwijany w CaretakerDetail.qml), polecenie w jednowierszowym Tide.CodeBlock
// z „Kopiuj”, zwinięty podgląd skryptu z `decision.script` (etap B; dziś usługa pola nie podaje), przyciski na
// Tide.Button, `moveChoice(±1)` dla ↑↓ gospodarza i `keysHint` obok „Wybierz”.
import QtQuick
import QtQuick.Layouts
import "../tide" as Tide
import "decision.js" as Decision
import "deviation.js" as Deviation

ColumnLayout {
    id: form

    readonly property string otherId: "__other__"

    property var decision: null
    property string selected: ""
    property bool techOpen: false
    // Rozwinięty komentarz opiekuna (przy opcji albo pod zdaniem opiekuna) — tylko widok, nic nie wybiera.
    property bool adviceOpen: false
    property string error: ""
    // Uwagi recenzenta zaznaczone do odesłania (id z `decision.findings`); domyślne zaznaczenie daje usługa.
    property var checkedIds: []
    // Podgląd karty z błędem (PT-125): odpowiedź usługi na `reply.preview`; null — jeszcze nie pokazany.
    property var preview: null
    property bool previewing: false
    // Zgoda na polecenia z łańcucha (PT-124): indeksy zaznaczonych poleceń z `decision.commands`.
    property var checks: []
    // Grupa zgód (PT-338): rozwinięta lista ticketów; powody odmowy usługi przy decyzjach odłączonych od grupy.
    property bool membersOpen: false
    property var errors: ({})

    readonly property string decisionId: decision ? decision.id : ""
    readonly property bool isGroup: !!decision && decision.group === true
    readonly property bool isReceipt: isGroup && decision.receipt === true
    readonly property var members: isGroup && Array.isArray(decision.members) ? decision.members : []
    readonly property var options: decision ? decision.options || [] : []
    readonly property bool freeText: !!decision && decision.freeText === true && !!decision.reply
    readonly property bool onlyText: freeText && options.length === 0
    readonly property string choice: onlyText ? otherId : selected
    // Jedno zalecenie (K-128, PT-506): tryb i oznaczenia opiekuna z decision.js (`advice`), tylko z pól usługi.
    readonly property var advice: Decision.advice(decision)
    readonly property var caretaker: advice.mode !== "none" ? decision.caretaker : null
    // Tytuł jako pytanie (pusty — zostaje dzisiejszy tytuł `happened`) i podgląd skryptu (`decision.script`, etap B).
    readonly property string question: Decision.questionTitle(decision)
    readonly property var script: Decision.script(decision)
    // Podpowiedź klawiszy obok „Wybierz” u gospodarza (okno decyzji, „Twój ruch”).
    readonly property string keysHint: "↑↓ wybór · Enter zatwierdza · Esc później"
    readonly property var commands: decision && decision.commands ? decision.commands : []
    readonly property bool showCommands: Decision.showsCommands(decision)
    readonly property var chosenOption: options.find(o => o.id === choice) || null
    readonly property bool picksCommands: Decision.picksCommands(chosenOption)
    // Podpis i podpowiedź pola własnej odpowiedzi: przy zgodzie na polecenia z `otherTitle`/`otherHint` (PT-124).
    readonly property string otherTitle: decision && decision.otherTitle ? decision.otherTitle : Decision.replyLabel(decision)
    readonly property string otherHint: decision && decision.otherHint ? decision.otherHint
                                        : decision && decision.reply && decision.reply.hint ? Decision.text(decision.reply.hint)
                                        : "Odpowiedz własnymi słowami, jak w czacie z agentem."
    // Pole „Opisz problem i ponów” (błąd Tide): przed zapisem formularz pokazuje podgląd karty z usługi.
    readonly property bool needsPreview: choice === otherId && Decision.previewMethod(decision) !== ""
    // Odstępstwo przy błędzie domknięcia (PT-339): możliwość okna i zdanie, dlaczego jeszcze nie można jej wybrać.
    readonly property bool deviationOffered: Deviation.offered(decision)
    readonly property bool deviationChosen: deviationOffered && choice === Deviation.OPTION_ID
    readonly property string deviationBlocker: deviationChosen ? Deviation.blocker(deviationReason.text) : ""
    readonly property bool canChoose: !previewing && (choice === otherId ? answer.text.trim() !== ""
                                                      : deviationChosen ? deviationBlocker === ""
                                                      : choice !== "" && (!picksCommands || checks.length > 0))
    // Napis przycisku wyboru w oknie i w pełnej karcie.
    readonly property string chooseLabel: needsPreview ? (preview ? (decision.reply.confirmLabel || "Zapisz i ponów") : "Pokaż podgląd") : "Wybierz"
    readonly property alias answerField: answer

    signal answered(string decisionId, string method, var params)
    signal groupAnswered(var ids)
    signal previewRequested(string decisionId, string method, var params)
    signal inputNeeded(string notice)
    signal escapePressed()

    function reset() {
        selected = ""
        techOpen = false
        adviceOpen = false
        scriptBlock.expanded = false
        membersOpen = false
        error = errors && errors[decisionId] ? String(errors[decisionId]) : ""
        preview = null
        previewing = false
        answer.text = ""
        deviationReason.text = ""
        checkedIds = Decision.defaultChecked(decision)
        checks = Decision.preselectedIndexes(decision)
    }

    function toggleCommand(at) {
        checks = Decision.toggled(commands, checks, at)
        error = ""
    }

    // Usługa odrzuciła odpowiedź albo podgląd: formularz pokazuje powód.
    function fail(message) {
        previewing = false
        error = message
    }

    // Odpowiedź usługi na podgląd karty z błędem; podgląd innej (już niewidocznej) decyzji jest pomijany.
    function showPreview(id, reply) {
        if (id !== decisionId) return
        previewing = false
        if (reply && reply.ok) {
            preview = reply
            error = ""
        } else {
            preview = null
            error = Decision.text(reply && (reply.message || reply.error)) || "Nie udało się przygotować podglądu."
        }
    }

    function toggleFinding(id) {
        const at = checkedIds.indexOf(id)
        checkedIds = at >= 0 ? checkedIds.filter(x => x !== id) : checkedIds.concat([id])
        error = ""
    }

    function pick(id) {
        selected = id
        error = ""
        if (id === otherId) answer.forceActiveFocus()
        else if (id === Deviation.OPTION_ID) deviationReason.forceActiveFocus()
    }

    // ↑↓ u gospodarza: wybór sąsiedniej możliwości w kolejności na ekranie (możliwości usługi, odstępstwo, „Inna
    // odpowiedź…”). To wybór użytkownika z klawiatury — bez zaznaczenia nic nie jest wybrane z góry (D10).
    function moveChoice(delta) {
        const ids = options.map(o => o.id)
        if (deviationOffered) ids.push(Deviation.OPTION_ID)
        if (freeText && !onlyText) ids.push(otherId)
        if (ids.length === 0) return
        const at = ids.indexOf(choice)
        const next = at < 0 ? (delta > 0 ? 0 : ids.length - 1) : Math.max(0, Math.min(ids.length - 1, at + delta))
        const id = ids[next]
        // „Inna odpowiedź…” bez fokusu pola: kolejne ↑↓ dalej chodzą po możliwościach.
        if (id === otherId) selected = form.otherId
        else pick(id)
    }

    // „Wybierz” i Enter: bez zaznaczenia (albo z pustą własną odpowiedzią) nic się nie dzieje.
    function choose() {
        if (!decision || !canChoose) return false
        let request = null
        if (choice === otherId) {
            request = Decision.replyRequest(decision, answer.text.trim())
            // Najpierw podgląd karty z błędem; dopiero drugi wybór („Zapisz i ponów”) zapisuje.
            if (request && needsPreview && !preview) {
                previewing = true
                error = ""
                previewRequested(decision.id, Decision.previewMethod(decision), request.params)
                return false
            }
        } else if (deviationChosen) {
            // Jedno `retry` tej decyzji ze stałą instrukcją i uzasadnieniem (deviation.js).
            request = Deviation.request(decision, deviationReason.text)
        } else {
            const option = options.find(o => o.id === choice)
            if (!option) return false
            if (Decision.needsInput(option)) {
                inputNeeded("„" + option.title + "”: wybierz wykonawcę w panelu karty.")
                return false
            }
            if (isGroup) {
                // Jedna odpowiedź na każdą decyzję grupy — żądanie z jej własnej możliwości (ten sam `id`).
                const requests = Decision.groupRequests(decision, option.id)
                if (requests.length === 0) return false
                groupAnswered(requests.map(r => r.id))
                for (const r of requests) answered(r.id, r.method, r.params)
                return true
            }
            request = Decision.optionRequest(option, checks)
        }
        if (!request || !request.method) return false
        // Odesłanie planu: do poprawy idą tylko zaznaczone uwagi i własny tekst (niezaznaczone nie).
        request = Decision.applyFindings(decision, request, checkedIds)
        if (request.params && request.params.action === "return" && decision.findings && decision.findings.length > 0
                && (request.params.findings || []).length === 0 && !request.params.feedback) {
            error = "Zaznacz uwagi do odesłania albo dopisz własne."
            return false
        }
        answered(decision.id, request.method, request.params)
        return true
    }

    onDecisionIdChanged: reset()

    spacing: 12

    // Tytuł jako pytanie (makieta S-9, „Prośba o zgodę”, wariant A): „Uruchomić make test poza piaskownicą?”. Zdanie
    // usługi (`happened`) stoi wtedy pod nim mniejszym tekstem; bez pytania `happened` jest tytułem jak dotąd.
    TextEdit {
        readOnly: true
        selectByMouse: true
        selectionColor: Tide.Theme.alpha(Tide.Tokens.sky, 0.35)
        objectName: "decisionTitle"
        Layout.fillWidth: true
        visible: form.question !== ""
        text: form.question
        textFormat: Text.PlainText
        color: Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 1.35
        font.weight: Font.DemiBold
        wrapMode: Text.Wrap
    }
    TextEdit {
        readOnly: true
        selectByMouse: true
        selectionColor: Tide.Theme.alpha(Tide.Tokens.sky, 0.35)
        objectName: "decisionHappened"
        Layout.fillWidth: true
        Layout.topMargin: form.question !== "" ? -6 : 0
        text: form.decision ? form.decision.happened : ""
        textFormat: Text.PlainText
        color: form.question !== "" ? Tide.Tokens.textSecondary : Tide.Tokens.textPrimary
        font.family: Tide.Theme.uiFamily
        font.pointSize: form.question !== "" ? Tide.Tokens.fontSizePt : Tide.Tokens.fontSizePt * 1.35
        font.weight: form.question !== "" ? Font.Normal : Font.DemiBold
        wrapMode: Text.Wrap
    }
    // „Dlaczego” z liniami „Uwaga:” i „Powód kroku:” (tekst agenta i usługi): standard tekstu K-128 — escapowany
    // tekst z wyróżnieniami ścieżek, poleceń, adresów, klawiszy i numerów kart, bez linków (Tide.Prose, prose.js).
    Tide.Prose {
        objectName: "decisionWhy"
        Layout.fillWidth: true
        text: form.decision ? form.decision.why : ""
    }
    // Polecenie z `technical.command`: jednowierszowy Tide.CodeBlock z „Kopiuj” (zwykły tekst).
    Tide.CodeBlock {
        objectName: "decisionCommand"
        Layout.fillWidth: true
        oneLine: true
        visible: text !== ""
        text: form.decision && form.decision.technical ? Decision.text(form.decision.technical.command) : ""
        copyName: "Kopiuj polecenie"
    }
    // Podgląd skryptu (`decision.script`, etap B karty K-128): zwinięty Tide.CodeBlock; bez pola — brak bloku.
    // Dziś usługa pola nie podaje, więc w produkcji bloku nie ma (działa na danych testowych).
    Tide.CodeBlock {
        id: scriptBlock
        objectName: "decisionScript"
        Layout.fillWidth: true
        visible: form.script !== null
        text: form.script ? form.script.text : ""
        path: form.script ? form.script.path : ""
        language: form.script ? form.script.language : ""
        lineCount: form.script ? form.script.lineCount : 0
        truncated: form.script ? form.script.truncated : false
        copyName: "Kopiuj treść skryptu"
    }

    // Grupa zgód (PT-338): znak przyjęcia po wyborze, miejsce na prognozę (#9) i rozwijana lista ticketów i kroków.
    Text {
        objectName: "decisionReceipt"
        Layout.fillWidth: true
        visible: form.isReceipt
        text: form.isReceipt ? Decision.text(form.decision.receiptText) : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.mint
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt * 1.05
        font.weight: Font.DemiBold
        wrapMode: Text.Wrap
        Accessible.role: Accessible.StaticText
        Accessible.name: text
    }
    Text {
        objectName: "decisionGroupForecast"
        Layout.fillWidth: true
        visible: form.isGroup && text !== ""
        text: form.isGroup ? Decision.text(form.decision.forecast) : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        wrapMode: Text.Wrap
    }
    Tide.Button {
        objectName: "decisionGroupToggle"
        visible: form.members.length > 0
        text: (form.membersOpen ? "▾ " : "▸ ") + "Tickety i kroki (" + form.members.length + ")"
        variant: "ghost"
        small: true
        onClicked: form.membersOpen = !form.membersOpen
    }
    ColumnLayout {
        objectName: "decisionGroupMembers"
        Layout.fillWidth: true
        visible: form.membersOpen && form.members.length > 0
        spacing: 4
        Accessible.role: Accessible.List
        Accessible.name: "Tickety i kroki grupy"
        Repeater {
            model: form.membersOpen ? form.members : []
            delegate: ColumnLayout {
                id: memberRow
                objectName: "decisionGroupMember"
                required property var modelData
                readonly property string label: [modelData.ticket, modelData.step, modelData.executor]
                                                .filter(x => !!x).join(" · ")
                readonly property string reason: modelData.reason || ""
                Layout.fillWidth: true
                spacing: 0
                Text {
                    Layout.fillWidth: true
                    text: memberRow.label
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.95
                    wrapMode: Text.Wrap
                }
                Text {
                    Layout.fillWidth: true
                    visible: memberRow.reason !== ""
                    text: memberRow.reason
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.9
                    wrapMode: Text.Wrap
                }
            }
        }
    }

    // Polecenia z treści pytania (04.11): gdy agent prosi o wpisanie czegoś w terminalu, polecenie stoi w osobnym
    // polu z przyciskiem „Kopiuj” zamiast w środku akapitu.
    Repeater {
        model: form.decision && form.decision.copyCommands ? form.decision.copyCommands : []
        delegate: Tide.CodeBlock {
            required property string modelData
            objectName: "decisionCopyCommand"
            Layout.fillWidth: true
            oneLine: true
            text: modelData
            copyName: "Kopiuj polecenie " + modelData
        }
    }

    // Polecenia łańcucha (PT-124): pole wyboru tylko przy poleceniach, na które można zezwolić;
    // przy pozostałych zawsze widać powód. Flagi i zaznaczenie początkowe — z usługi.
    ColumnLayout {
        objectName: "decisionCommands"
        Layout.fillWidth: true
        visible: form.showCommands
        spacing: 6
        Accessible.role: Accessible.List
        Accessible.name: "Polecenia z łańcucha"

        Repeater {
            model: form.showCommands ? form.commands : []
            delegate: Rectangle {
                id: commandRow
                required property var modelData
                required property int index
                readonly property bool allowable: modelData.canAllow === true
                readonly property bool checked: form.checks.indexOf(index) >= 0
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
                Accessible.onToggleAction: form.toggleCommand(index)
                Keys.onSpacePressed: form.toggleCommand(index)

                MouseArea {
                    anchors.fill: parent
                    enabled: commandRow.allowable
                    cursorShape: commandRow.allowable ? Qt.PointingHandCursor : Qt.ArrowCursor
                    onClicked: form.toggleCommand(commandRow.index)
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
        visible: form.error !== ""
        text: form.error
        textFormat: Text.PlainText
        color: Tide.Tokens.negative
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        wrapMode: Text.Wrap
    }

    // Uwagi recenzenta planu: waga, treść i tickety; do poprawy idą zaznaczone (nit domyślnie nie).
    ColumnLayout {
        objectName: "decisionFindings"
        Layout.fillWidth: true
        visible: !!form.decision && (form.decision.findings || []).length > 0
        spacing: 6
        Accessible.role: Accessible.Grouping
        Accessible.name: "Uwagi recenzenta"
        Text {
            Layout.fillWidth: true
            text: "Uwagi recenzenta — zaznaczone trafią do poprawy"
            textFormat: Text.PlainText
            color: Tide.Tokens.textSecondary
            font.family: Tide.Theme.uiFamily
            font.pointSize: Tide.Tokens.fontSizePt
            font.weight: Font.Medium
        }
        Repeater {
            model: form.decision ? form.decision.findings || [] : []
            delegate: DecisionFinding {
                objectName: "decisionFinding"
                required property var modelData
                Layout.fillWidth: true
                severity: modelData.severity
                text: modelData.text
                tickets: modelData.tickets
                checked: form.checkedIds.indexOf(modelData.id) >= 0
                onToggled: form.toggleFinding(modelData.id)
            }
        }
    }

    // Możliwości w kolejności z usługi; „Inna odpowiedź…” zawsze ostatnia.
    ColumnLayout {
        objectName: "decisionOptions"
        Layout.fillWidth: true
        Layout.topMargin: 4
        spacing: 8
        Accessible.role: Accessible.Grouping
        Accessible.name: "Możliwości"

        // Jedno zalecenie (K-128, PT-506): „polecane” z `recommended` usługi, oznaczenie opiekuna z decision.js.
        Repeater {
            model: form.options
            DecisionOption {
                id: optionCard
                readonly property var marks: Decision.optionAdvice(form.advice, modelData.id)
                objectName: "decisionOption"
                Layout.fillWidth: true
                title: modelData.title
                effect: modelData.effect
                recommended: modelData.recommended === true
                checked: form.selected === modelData.id
                caretakerBadge: marks.badge
                note: marks.note
                tone: marks.tone
                link: marks.link
                noteOpen: form.adviceOpen
                onPicked: form.pick(modelData.id)
                onNoteToggled: form.adviceOpen = !form.adviceOpen
                CaretakerDetail {
                    objectName: "decisionCaretakerDetail"
                    Layout.fillWidth: true
                    visible: optionCard.note !== "" && form.adviceOpen
                    rule: form.advice
                    care: optionCard.note !== "" ? form.caretaker : null
                }
            }
        }

        // „Przyjmij odstępstwo…” (PT-339): po możliwościach usługi, przed „Inną odpowiedzią…”; nigdy nie polecona.
        DecisionOption {
            objectName: "decisionDeviation"
            Layout.fillWidth: true
            visible: form.deviationOffered
            title: Deviation.TITLE
            effect: Deviation.EFFECT
            checked: form.deviationChosen
            onPicked: form.pick(Deviation.OPTION_ID)
        }
        ColumnLayout {
            objectName: "decisionDeviationBox"
            Layout.fillWidth: true
            Layout.leftMargin: 23
            visible: form.deviationChosen
            spacing: 4
            Text {
                Layout.fillWidth: true
                text: Deviation.FIELD_LABEL
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
                font.weight: Font.Medium
            }
            Rectangle {
                Layout.fillWidth: true
                implicitHeight: Math.max(64, deviationReason.implicitHeight + 16)
                radius: Tide.Tokens.radiusFrame
                color: Tide.Tokens.backgroundView
                border.width: 1
                border.color: form.deviationChosen && Deviation.tooLong(deviationReason.text)
                              ? Tide.Theme.alpha(Tide.Tokens.negative, 0.6)
                              : deviationReason.activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                              : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityOutline)
                TextEdit {
                    id: deviationReason
                    objectName: "decisionDeviationReason"
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
                    Accessible.name: Deviation.FIELD_LABEL
                    // Enter dodaje linię (uzasadnienie bywa dłuższe); wybór tylko przyciskiem. Esc oddaje do okna.
                    Keys.onEscapePressed: event => {
                        form.escapePressed()
                        event.accepted = true
                    }
                    Text {
                        anchors.fill: parent
                        visible: deviationReason.text === ""
                        text: Deviation.HINT
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font: deviationReason.font
                        wrapMode: Text.Wrap
                    }
                }
            }
            Text {
                objectName: "decisionDeviationCounter"
                Layout.fillWidth: true
                text: form.deviationChosen ? Deviation.counter(deviationReason.text) : ""
                textFormat: Text.PlainText
                color: form.deviationChosen && Deviation.tooLong(deviationReason.text) ? Tide.Tokens.negative
                                                                                     : Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                horizontalAlignment: Text.AlignRight
            }
        }

        Rectangle {
            id: other
            objectName: "decisionOther"
            Layout.fillWidth: true
            visible: form.freeText
            readonly property bool checked: form.choice === form.otherId
            implicitHeight: otherColumn.implicitHeight + 24
            radius: Tide.Tokens.radiusTile
            color: checked && !form.onlyText ? Tide.Theme.alpha(Tide.Tokens.accent, 0.09) : Tide.Tokens.backgroundCard
            border.width: 1
            border.color: checked && !form.onlyText || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                                   : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
            activeFocusOnTab: !form.onlyText
            Accessible.role: Accessible.RadioButton
            Accessible.name: form.otherTitle
            Accessible.checked: checked
            Accessible.onPressAction: form.pick(form.otherId)
            Keys.onSpacePressed: form.pick(form.otherId)

            MouseArea {
                anchors.fill: parent
                cursorShape: Qt.PointingHandCursor
                onClicked: form.pick(form.otherId)
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
                    visible: !form.onlyText
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
                        text: form.otherTitle
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 1.1
                        font.weight: Font.DemiBold
                    }
                }
                Text {
                    objectName: "decisionOtherEffect"
                    Layout.fillWidth: true
                    Layout.leftMargin: form.onlyText ? 0 : 23
                    visible: text !== ""
                    text: form.decision && form.decision.reply ? Decision.text(form.decision.reply.effect) : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    wrapMode: Text.Wrap
                }
                Text {
                    visible: form.onlyText
                    text: "Twoja odpowiedź"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    font.weight: Font.Medium
                }
                Rectangle {
                    Layout.fillWidth: true
                    Layout.leftMargin: form.onlyText ? 0 : 23
                    visible: form.onlyText || other.checked
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
                        Accessible.name: form.otherTitle
                        onActiveFocusChanged: if (activeFocus && !form.onlyText) form.selected = form.otherId
                        // Zmieniony opis — podgląd karty przestaje być aktualny.
                        onTextChanged: form.preview = null
                        // Enter wysyła jak w czacie, Shift+Enter — nowa linia; Esc oddaje do okna albo widoku.
                        Keys.onPressed: event => {
                            if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                    && !(event.modifiers & Qt.ShiftModifier)) {
                                form.choose()
                                event.accepted = true
                            } else if (event.key === Qt.Key_Escape) {
                                form.escapePressed()
                                event.accepted = true
                            }
                        }
                        Text {
                            anchors.fill: parent
                            visible: answer.text === ""
                            text: form.otherHint
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

    // Opiekun bez wskazanej opcji (tryb "note" reguły zalecenia): jedno zdanie pod możliwościami, a szczegóły
    // (streszczenie i lista prób `tried[]`) rozwijane na żądanie. Zastępuje dawny blok „Opiekun poleca”.
    ColumnLayout {
        objectName: "decisionCaretakerNote"
        Layout.fillWidth: true
        visible: form.advice.mode === "note"
        spacing: 6
        Accessible.role: Accessible.Grouping
        Accessible.name: "Opiekun"
        RowLayout {
            Layout.fillWidth: true
            spacing: 6
            Text {
                objectName: "decisionCaretakerTitle"
                Layout.fillWidth: true
                text: form.advice.sentence
                textFormat: Text.PlainText
                color: form.caretaker && form.caretaker.failed === true ? Tide.Tokens.peach : Tide.Tokens.teal
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.95
                wrapMode: Text.Wrap
            }
            Tide.Button {
                objectName: "decisionCaretakerToggle"
                visible: Decision.adviceDetail(form.advice, form.caretaker).summary !== ""
                         || Decision.triedRows(form.caretaker).length > 0
                Layout.alignment: Qt.AlignTop
                text: Decision.noteToggleLabel(form.adviceOpen)
                variant: "ghost"
                small: true
                onClicked: form.adviceOpen = !form.adviceOpen
            }
        }
        CaretakerDetail {
            objectName: "decisionCaretakerDetail"
            Layout.fillWidth: true
            visible: form.adviceOpen
            rule: form.advice
            care: form.advice.mode === "note" ? form.caretaker : null
        }
    }

    // „Zezwól na wybrane” bez zaznaczonego polecenia: „Wybierz” jest nieaktywne, więc mówimy dlaczego.
    Text {
        objectName: "decisionAllowHint"
        Layout.fillWidth: true
        visible: form.picksCommands && form.checks.length === 0
        text: "Zaznacz co najmniej jedno polecenie powyżej, na które zezwalasz."
        textFormat: Text.PlainText
        color: Tide.Tokens.waiting
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        wrapMode: Text.Wrap
    }

    // „Przyjmij odstępstwo…” bez uzasadnienia albo ponad limit: „Wybierz” jest nieaktywne, więc mówimy dlaczego.
    Text {
        objectName: "decisionDeviationHint"
        Layout.fillWidth: true
        visible: form.deviationBlocker !== ""
        text: form.deviationBlocker
        textFormat: Text.PlainText
        color: Tide.Tokens.waiting
        font.family: Tide.Theme.uiFamily
        font.pointSize: Tide.Tokens.fontSizePt
        wrapMode: Text.Wrap
    }

    // Podgląd karty z błędem przed zapisem (PT-125): treść liczy usługa, formularz tylko ją pokazuje.
    Rectangle {
        objectName: "decisionPreview"
        Layout.fillWidth: true
        visible: (form.preview !== null || form.previewing) && form.choice === form.otherId
        implicitHeight: previewColumn.implicitHeight + 24
        radius: Tide.Tokens.radiusFrame
        color: Tide.Tokens.backgroundView
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityOutline)
        Accessible.role: Accessible.Grouping
        Accessible.name: "Podgląd karty z błędem"
        ColumnLayout {
            id: previewColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 12
            spacing: 6
            Text {
                objectName: "decisionPreviewWhere"
                Layout.fillWidth: true
                text: form.previewing ? "Przygotowuję podgląd karty…" : Decision.previewWhere(form.preview)
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.95
                wrapMode: Text.Wrap
            }
            Text {
                objectName: "decisionPreviewTitle"
                Layout.fillWidth: true
                visible: text !== ""
                text: form.preview ? Decision.text(form.preview.title) : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 1.05
                font.weight: Font.DemiBold
                wrapMode: Text.Wrap
            }
            Text {
                objectName: "decisionPreviewBody"
                Layout.fillWidth: true
                visible: text !== ""
                text: form.preview ? Decision.text(form.preview.description) : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.monoFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.85
                wrapMode: Text.WrapAnywhere
            }
        }
    }

    // Szczegóły techniczne — zwinięte.
    Tide.Button {
        objectName: "decisionTechToggle"
        visible: techRepeater.count > 0
        text: (form.techOpen ? "▾ " : "▸ ") + "Szczegóły techniczne"
        variant: "ghost"
        small: true
        onClicked: form.techOpen = !form.techOpen
    }
    GridLayout {
        objectName: "decisionTech"
        Layout.fillWidth: true
        visible: form.techOpen
        columns: 2
        columnSpacing: 14
        rowSpacing: 4
        Repeater {
            id: techRepeater
            model: form.decision ? Decision.techRows(form.decision.technical) : []
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

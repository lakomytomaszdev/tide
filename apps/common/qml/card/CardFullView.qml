// Pełna karta na cały widok okna Tide (K-35, PT-128): „← Tablica” i Esc wracają, u góry pasek 7 faz, pod nim
// „Twój ruch” — pierwsza czekająca decyzja do rozstrzygnięcia w miejscu (ten sam DecisionForm co w oknie decyzji)
// albo jedno zdanie, gdy nic nie czeka — a niżej fale ticketów z ludzkim stanem i „Teraz w tle” z samymi
// trwającymi krokami.
//
// Widok czyta wyłącznie pola usługi: `derived.phases[]`, `derived.tickets[]` (`status`, `statusLabel`,
// `waitingOn`, `executor`, `reviewer`, `wave`), `derived.flow.decisions[]`, `flow.plan.waves`, `flow.running[]`
// i `flow.headline` (daemon/tide_daemon/cardflow/fullcard.py). Niczego nie liczy: kolory to tylko przekład stanu
// z usługi na paletę, a kolejność faz, fal, ticketów i decyzji przychodzi z usługi.
//
// Umowa: `card` (karta z migawki Tablicy), `start()` — otwarcie od pierwszej decyzji, `failed(id, tekst)` — błąd
// usługi przy odpowiedzi (decyzja wraca), `showPreview(id, odpowiedź)` i sygnał `previewRequested(decisionId,
// method, params)` — podgląd karty z błędem Tide przed zapisem (PT-125, DecisionForm.qml), sygnały
// `backRequested()`, `answered(decisionId, method, params)`,
// `decisionsRequested()` — wszystkie decyzje w oknie decyzji, `panelRequested(notice)` — panel karty (wybór
// wykonawcy zostaje tam), `descriptionRequested()` — szuflada szybkiej edycji opisu, `enterStepRequested(stepId)` —
// „Wejdź” przy trwającym kroku i „Wznów rozmowę” w historii (PT-129: `flow.history[]`, wcześniejsze zlecenia
// zwinięte i bez wznawiania).
//
// Rzadkie ustawienia (PT-134, US 14): kolumna, zgłoszenie w YouTracku, gałąź, wstrzymanie i archiwizacja zapisują się
// od razu — bez „Zapisz / Anuluj”. Każdy przycisk nazywa skutek („Przenieś do Gotowych”), a widok wysyła
// `settingRequested(kind, params)` z gotowym zdaniem skutku (`params.effect`), które Tablica pokazuje po zapisie.
// Archiwizacja i zmiana gałęzi najpierw pytają o potwierdzenie ze skutkiem; odmowa niczego nie wysyła. Sekcja
// „Ustawienia karty” i menu „Karta ▾” biorą akcje z jednej listy (`actions`), więc zawsze są te same.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide
import "decision.js" as Decision
import "settings.js" as Settings
import "ticket.js" as TicketLib

Rectangle {
    id: view

    property var card: null
    property var answeredIds: []
    // Reguły przenoszenia z Tablicy (PT-134): dokąd wolno przenieść kartę, czy wolno ją zarchiwizować, nazwy kolumn.
    property var moveTargets: ["backlog", "todo", "done"]
    property bool canArchive: true
    property var columnLabels: ({})

    readonly property var derived: card && card.derived ? card.derived : null
    readonly property var flow: derived && derived.flow ? derived.flow : null
    readonly property var decisions: flow && Array.isArray(flow.decisions) ? flow.decisions : []
    readonly property var pending: decisions.filter(d => answeredIds.indexOf(d.id) < 0)
    readonly property var current: pending.length > 0 ? pending[0] : null
    readonly property var phases: derived && Array.isArray(derived.phases) ? derived.phases : []
    readonly property var tickets: derived && Array.isArray(derived.tickets) ? derived.tickets : []
    // Karta „Od razu” (bez planu): jeden kafelek jej ticketu, bez nagłówka fali (PT-333).
    readonly property var nowTicket: TicketLib.tileModel(card, answeredIds)
    readonly property var waves: flow && flow.plan && Array.isArray(flow.plan.waves) ? flow.plan.waves : []
    readonly property var running: flow && Array.isArray(flow.running) ? flow.running : []
    readonly property string headline: flow && flow.headline ? flow.headline : ""
    readonly property var history: flow && Array.isArray(flow.history) ? flow.history : []
    readonly property var stepNotes: flow && Array.isArray(flow.stepNotes) ? flow.stepNotes : []
    readonly property var noteSteps: flow && Array.isArray(flow.steps) ? flow.steps.filter(
        s => s.canAddNote || stepNotes.some(n => n.step === s.id)) : []
    property var noteDrafts: ({})

    function noteKey(step) { return (card ? card.id : "") + ":" + (flow ? flow.orderSeq : 0) + ":" + step }
    function noteDraft(step) { return noteDrafts[noteKey(step)] || {text: "", requestId: "", busy: false, error: "", saved: ""} }
    function editNote(step, text) {
        const drafts = Object.assign({}, noteDrafts)
        const old = noteDraft(step)
        if (old.busy) return
        drafts[noteKey(step)] = {text: text, requestId: old.text === text ? old.requestId : "", busy: false, error: "", saved: ""}
        noteDrafts = drafts
    }
    function sendNote(step) {
        const old = noteDraft(step)
        if (old.busy || !old.text.trim()) return
        const requestId = old.requestId || "step-note-" + Date.now() + "-" + Math.random().toString(36).slice(2)
        const drafts = Object.assign({}, noteDrafts)
        drafts[noteKey(step)] = Object.assign({}, old, {requestId: requestId, busy: true, error: ""})
        noteDrafts = drafts
        answered("step-note:" + requestId, "AddCardStepNote",
                 {step: step, orderSeq: flow.orderSeq, requestId: requestId, text: old.text})
    }
    function noteReply(requestId, error, reply) {
        const drafts = Object.assign({}, noteDrafts)
        const key = Object.keys(drafts).find(k => drafts[k].requestId === requestId)
        if (!key) return
        drafts[key] = error ? Object.assign({}, drafts[key], {busy: false, error: error})
                            : {text: "", requestId: "", busy: false, error: "", saved: reply && reply.noteId || ""}
        noteDrafts = drafts
    }
    // Bieżące zlecenie od najnowszego wpisu; wcześniejsze (`old`) osobno, zwinięte.
    readonly property var currentHistory: history.filter(h => !h.old).reverse()
    readonly property var oldHistory: history.filter(h => h.old === true).reverse()
    property bool oldOpen: false
    readonly property var stoppedByLimit: flow && Array.isArray(flow.stoppedByLimit) ? flow.stoppedByLimit : []
    readonly property var otherProviders: flow && Array.isArray(flow.otherProviders) ? flow.otherProviders : []
    readonly property var limitTransfer: flow && flow.limitTransfer ? flow.limitTransfer : null
    property bool transferSent: false
    property string consentError: ""
    readonly property alias form: form

    signal backRequested()
    signal navigateRequested(string viewId, string item)
    signal answered(string decisionId, string method, var params)
    signal previewRequested(string decisionId, string method, var params)
    signal decisionsRequested()
    signal panelRequested(string notice)
    signal descriptionRequested()
    // kind: move {column} | update {field, value} | pause {paused} | archive {}; params.effect — zdanie o skutku.
    signal settingRequested(string kind, var params)
    signal enterStepRequested(string stepId)
    // Ustawienia ticketu (PT-332): kafelek przekazuje prośbę dalej; przycisk na kafelku dodaje kolejny ticket karty.
    property bool ticketSettingsEnabled: false
    signal ticketSettingsRequested(string ticketId)
    // Scalenie domkniętej karty do gałęzi głównej i podgląd zmian (03.11: po K-35 akcja była tylko w starym panelu).
    signal mergeMainRequested()
    signal previewMergeRequested(string target)
    signal chooseMergeRequested()
    readonly property string defaultMergeTarget: flow && flow.base && flow.base.status === "known"
                                                 && (flow.base.baseRef || "").indexOf("refs/heads/") === 0
                                                 ? flow.base.baseRef.substring(11) : ""
    signal baseChangeRequested()
    property bool baseChangeAvailable: false
    property bool mergePreviewAvailable: false
    signal branchDiffRequested()
    property bool mergeBusy: false
    readonly property var mainPhase: phases.find(p => p.id === "main-merge") || null
    signal caretakerTalkRequested()

    // --- rzadkie ustawienia (PT-134) -----------------------------------------------------------------
    readonly property string storedBranch: card && card.branch ? card.branch : ""
    readonly property string shownBranch: storedBranch || (flow && flow.cardBranch ? flow.cardBranch : "")
    readonly property string storedYoutrack: card && card.youtrack ? card.youtrack : ""
    // Kolumna widoczna na Tablicy (karta „Do sprawdzenia” ma zapisane todo).
    readonly property string shownColumn: derived && derived.column ? derived.column : (card ? card.column : "")
    readonly property bool paused: !!(flow && flow.paused)
    readonly property bool canPause: !!(derived && (!flow || !flow.plan || flow.plan.status !== "done")
                                        && derived.column !== "review" && !(flow && flow.merged))
    property bool menuOpen: false
    property string settingError: ""
    // Pytanie o potwierdzenie: null albo {kind, params, title, effect, yes}.
    property var confirming: null
    readonly property string typedBranch: branchField.text.trim()
    readonly property var typedYoutrack: Settings.issueKey(youtrackField.text)

    // Jedna lista akcji dla sekcji ustawień i menu „Karta ▾”: {id, label, effect, danger, input}.
    // Gałąź karty z przepływu ustala Tide (silnik jej używa, `card.branch` tylko dopasowuje pracę), więc jej nie zmieniamy.
    readonly property bool branchLocked: !!(flow && flow.cardBranch)
    readonly property var actions: card ? Settings.actions(card.id, shownColumn, storedYoutrack, canPause, paused,
                                                           moveTargets, canArchive, branchLocked, finish) : []
    // Skutek zakończenia karty z przepływu liczy usługa (`flow.finish`: trwające kroki, niescalone tickety).
    readonly property var finish: flow && flow.finish ? flow.finish : null
    readonly property string finishText: Settings.finishEffect(finish)

    // Podpisy przycisków z polem nazywają skutek tego, co wpisano; puste pole tylko daje fokus (odłączenie to osobna akcja).
    readonly property string youtrackButton: typedYoutrack === null || typedYoutrack === "" || typedYoutrack === storedYoutrack
        ? actionLabel("youtrack")
        : (storedYoutrack ? "Zmień zgłoszenie na " : "Powiąż ze zgłoszeniem ") + typedYoutrack
    readonly property string branchButton: typedBranch === "" || typedBranch === shownBranch
        ? actionLabel("branch") : "Zmień gałąź na " + typedBranch

    // Menu „Karta ▾” nigdy nie zapisuje pola na ślepo: pozycje z polem przewijają do niego i dają fokus.
    function focusAction(id) {
        menuOpen = false
        focusField(id === "youtrack" ? youtrackField : branchField)
    }

    function actionLabel(id) {
        const found = actions.find(a => a.id === id)
        return found ? found.label : ""
    }

    function run(id) {
        menuOpen = false
        if (!card || confirming) return
        if (id.indexOf("move:") === 0) {
            const column = id.slice(5)
            const params = { column: column,
                             effect: "Karta " + card.id + " przeniesiona do „" + Settings.columnName(columnLabels, column) + "”." }
            if (column === "done" && finishText !== "") askFinish(params)
            else settingRequested("move", params)
        } else if (id === "pause") {
            settingRequested("pause", { paused: !paused,
                                        effect: paused ? "Karta " + card.id + " wznowiona."
                                                       : "Karta " + card.id + " wstrzymana. Trwające kroki kończą się." })
        } else if (id === "archive") {
            askArchive()
        } else if (id === "youtrack") {
            // Puste pole albo to samo zgłoszenie: tylko fokus. Odłączenie jest osobną, jawną akcją.
            if (typedYoutrack === "" || typedYoutrack === storedYoutrack) focusField(youtrackField)
            else applyYoutrack()
        } else if (id === "youtrack-clear") {
            settingRequested("update", { field: "youtrack", value: "",
                effect: "Karta " + card.id + " odłączona od zgłoszenia " + storedYoutrack + "." })
        } else if (id === "branch") {
            if (typedBranch === "" || typedBranch === shownBranch) focusField(branchField)
            else askBranch()
        }
    }

    // Przewija do sekcji „Ustawienia karty”.
    function showSettings() {
        flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, settings.y - 12))
    }

    function showTop() {
        flick.contentY = 0
    }

    function focusField(field) {
        showSettings()
        field.forceActiveFocus()
    }

    function applyYoutrack() {
        const key = typedYoutrack
        if (key === null) {
            settingError = "Zgłoszenie w YouTracku to klucz, np. LEGAL-35 (albo adres zgłoszenia)."
            return
        }
        settingError = ""
        if (key === storedYoutrack || key === "") return
        settingRequested("update", { field: "youtrack", value: key,
            effect: "Karta " + card.id + " powiązana ze zgłoszeniem " + key + "." })
    }

    function askFinish(params) {
        confirming = { kind: "move", params: params,
                       title: "Zakończyć kartę " + card.id + "?",
                       effect: "Karta trafi do Gotowych, a przepływ się kończy: " + finishText + ".",
                       yes: "Zakończ kartę" }
    }

    function askArchive() {
        confirming = { kind: "archive", params: { effect: "Karta " + card.id + " zdjęta z tablicy." },
                       title: "Zarchiwizować kartę " + card.id + "?",
                       effect: "Karta zniknie z tablicy. Plik karty i historia zostają, więc da się ją odtworzyć z archiwum"
                               + (finishText !== "" ? "; przepływ się kończy: " + finishText + "." : "."),
                       yes: "Archiwizuj kartę" }
    }

    function askBranch() {
        const next = typedBranch
        if (next === "" || next === shownBranch) return
        confirming = { kind: "update", params: { field: "branch", value: next,
                                                 effect: "Gałąź karty " + card.id + ": " + next + "." },
                       title: "Zmienić gałąź karty " + card.id + "?",
                       effect: "Tide będzie wiązać z kartą pracę z gałęzi „" + next + "”"
                               + (shownBranch ? " zamiast „" + shownBranch + "”" : "")
                               + ". Gałęzie w repozytorium zostają bez zmian.",
                       yes: "Zmień gałąź na " + next }
    }

    function confirmYes() {
        const c = confirming
        confirming = null
        if (c) settingRequested(c.kind, c.params)
        view.forceActiveFocus()
    }

    function confirmNo() {
        confirming = null
        view.forceActiveFocus()
    }

    function providerLabel(provider) {
        return provider === "openai" ? "Codex" : provider === "anthropic" ? "Claude" : provider
    }

    function filesLabel(n) {
        const count = Number(n) || 0
        const few = count % 10 >= 2 && count % 10 <= 4 && (count % 100 < 12 || count % 100 > 14)
        return count + " " + (count === 1 ? "plik WIP" : few ? "pliki WIP" : "plików WIP")
    }

    // „Przenieś na Codex”: istniejąca ścieżka przekazania kroku (ResolveCardStep, choice transfer), po jednym
    // zdarzeniu na zatrzymany krok; koszt widać nad przyciskiem przed akcją.
    function requestTransfer() {
        if (!limitTransfer || limitTransfer.available !== true) return
        transferSent = true
        for (const stopped of stoppedByLimit)
            answered("limit-transfer:" + stopped.step, "ResolveCardStep",
                     { choice: "transfer", step: stopped.step, executor: limitTransfer.executor })
    }

    function start() {
        transferSent = false
        consentError = ""
        answeredIds = []
        confirming = null
        menuOpen = false
        settingError = ""
        branchField.text = ""
        youtrackField.text = ""
        form.reset()
        flick.contentY = 0
        forceActiveFocus()
    }

    // Usługa odrzuciła odpowiedź: decyzja wraca na wierzch z powodem.
    function failed(decisionId, message) {
        if (decisionId.indexOf("ticket-consent:") === 0) {
            consentError = message
            return
        }
        if (decisionId.indexOf("limit-transfer:") === 0) transferSent = false
        answeredIds = answeredIds.filter(id => id !== decisionId)
        form.fail(message)
    }

    // Odpowiedź usługi na podgląd karty z błędem (PT-125); podgląd innej decyzji formularz pomija.
    function showPreview(decisionId, reply) {
        form.showPreview(decisionId, reply)
    }

    function ticket(id) {
        return tickets.find(t => t.id === id) || null
    }

    // Przekład stanu z usługi na paletę (bez logiki: stan liczy usługa).
    function phaseTone(state) {
        return state === "done" ? Tide.Tokens.mint : state === "now" ? Tide.Tokens.teal
             : state === "you" ? Tide.Tokens.peach : state === "hold" ? Tide.Tokens.butter : Tide.Tokens.textMuted
    }

    function clock(value) {
        if (!value) return ""
        const when = typeof value === "number" ? new Date(value * 1000) : new Date(value)
        return isNaN(when.getTime()) ? "" : Qt.formatDateTime(when, "d.MM HH:mm")
    }

    // Czas po polsku bez znaczników ISO: „dziś 14:20”, „wczoraj 14:20”, starsze „24.10 14:20”.
    function absoluteTime(ts) {
        if (!ts) return ""
        const d = new Date(ts * 1000)
        if (isNaN(d.getTime())) return ""
        const now = new Date()
        const midnight = x => new Date(x.getFullYear(), x.getMonth(), x.getDate()).getTime()
        const days = Math.round((midnight(now) - midnight(d)) / 86400000)
        const time = Qt.formatDateTime(d, "HH:mm")
        return days <= 0 ? "dziś " + time : days === 1 ? "wczoraj " + time : Qt.formatDateTime(d, "d.MM HH:mm")
    }

    function relativeTime(ts) {
        if (!ts) return ""
        const minutes = Math.floor((Date.now() / 1000 - ts) / 60)
        if (minutes < 1) return "przed chwilą"
        if (minutes < 60) return minutes + " min temu"
        const hours = Math.floor(minutes / 60)
        return hours < 24 ? hours + " godz. temu" : ""
    }

    function historyWhen(ts) {
        return [absoluteTime(ts), relativeTime(ts)].filter(x => !!x).join(" · ")
    }

    function phaseCaption(phase) {
        if (phase.state === "skipped") return "nie dotyczy"
        const who = phase.executor === "user" ? "ty" : phase.executor
        return [who, clock(phase.ts), phase.note].filter(x => !!x).join(" · ")
    }

    // Jedna linia historii: czas, rodzaj, zdanie, znacznik zdarzenia użytkownika i — tylko dla kroków bieżącego
    // zlecenia z pytaniem albo zatrzymanych (`canResume` z usługi) — „Wznów rozmowę”.
    component HistoryLine: Rectangle {
        id: line
        required property var modelData
        property bool canResume: false
        property bool dimmed: false
        objectName: "fullHistoryLine"
        readonly property string text: modelData.text
        readonly property bool byUser: modelData.actor === "user"
        readonly property bool byCaretaker: modelData.actor === "caretaker"
        readonly property string when: view.historyWhen(modelData.ts)
        Layout.fillWidth: true
        implicitHeight: lineRow.implicitHeight + 14
        radius: Tide.Tokens.radiusTile
        color: Tide.Tokens.backgroundCard
        opacity: dimmed ? 0.7 : 1
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
                text: line.modelData.kindLabel
                textFormat: Text.PlainText
                color: Tide.Tokens.lavender
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.85
            }
            Text {
                objectName: "historyText"
                Layout.fillWidth: true
                text: line.text
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.95
                wrapMode: Text.Wrap
            }
            Text {
                objectName: "historyUser"
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
                visible: line.byCaretaker
                text: line.modelData.actorLabel || ""   // podpis z usługi („Opiekun”) zamiast nazwy wykonawcy
                textFormat: Text.PlainText
                color: Tide.Tokens.teal
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.85
                font.weight: Font.DemiBold
            }
            Tide.TextButton {
                objectName: "historyResume"
                visible: line.canResume
                text: "Wznów rozmowę"
                variant: "outline"
                Accessible.name: "Wznów rozmowę w kroku " + line.modelData.text
                onClicked: view.enterStepRequested(line.modelData.step)
            }
        }
    }

    color: Tide.Tokens.backgroundWindow
    focus: visible
    Accessible.role: Accessible.Pane
    Accessible.name: card ? "Karta " + card.id + ": " + card.title : "Karta"

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            if (confirming) confirmNo()
            else if (menuOpen) menuOpen = false
            else backRequested()
            event.accepted = true
        } else if (confirming) {
            event.accepted = true   // pytanie o potwierdzenie przejmuje klawiaturę: Enter nie potwierdza po cichu
        } else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && current) {
            form.choose()
            event.accepted = true
        }
    }

    MouseArea {
        anchors.fill: parent
        onClicked: view.forceActiveFocus()
    }

    Flickable {
        id: flick
        anchors.fill: parent
        anchors.margins: 24
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: flick.contentHeight > flick.height ? QQC2.ScrollBar.AsNeeded : QQC2.ScrollBar.AlwaysOff }

        ColumnLayout {
            id: content
            width: flick.width
            spacing: 18

            // Okruszki: powrót, projekt, gałąź karty, panel karty.
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Tide.TextButton {
                    objectName: "fullBack"
                    text: "← Tablica"
                    variant: "outline"
                    Accessible.name: "Wróć do Tablicy (Esc)"
                    onClicked: view.backRequested()
                }
                Text {
                    text: view.card ? view.card.projectName || view.card.project || "" : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                }
                Text {
                    Layout.fillWidth: true
                    text: view.flow ? view.flow.cardBranch || "" : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.lavender
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.9
                    elide: Text.ElideMiddle
                }
                Tide.TextButton {
                    objectName: "fullDescription"
                    text: "Edytuj opis"
                    variant: "outline"
                    Accessible.name: "Edytuj opis karty w szufladzie"
                    onClicked: view.descriptionRequested()
                }
                Tide.TextButton {
                    objectName: "fullSourceIssue"
                    visible: !!view.card && !!view.card.sourceIssue
                    text: view.card ? "Raport i dowody " + (view.card.sourceIssue || "") + " · wersja " + (view.card.sourceIssueVersion || 0) : ""
                    variant: "outline"
                    onClicked: view.navigateRequested("issues", view.card.sourceIssue)
                }
                Tide.TextButton {
                    id: menuButton
                    objectName: "fullMenu"
                    text: "Karta ▾"
                    variant: "outline"
                    Accessible.name: "Menu karty: ustawienia i akcje"
                    onClicked: view.menuOpen = !view.menuOpen
                }
            }

            Tide.TextButton {
                objectName: "talkToCaretaker"
                visible: !!(view.flow && view.flow.canTalkToCaretaker)
                text: "Porozmawiaj z opiekunem"
                variant: "outline"
                onClicked: view.caretakerTalkRequested()
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Text {
                    text: view.card ? view.card.id : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.sky
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 1.5
                    font.weight: Font.DemiBold
                }
                Text {
                    objectName: "fullTitle"
                    Layout.fillWidth: true
                    text: view.card ? view.card.title : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 1.5
                    font.weight: Font.DemiBold
                    wrapMode: Text.Wrap
                }
            }

            // Pasek 7 faz.
            RowLayout {
                objectName: "fullPhases"
                Layout.fillWidth: true
                spacing: 8
                Accessible.role: Accessible.List
                Accessible.name: "Przebieg karty"
                Repeater {
                    model: view.phases
                    delegate: ColumnLayout {
                        id: phaseItem
                        required property var modelData
                        objectName: "fullPhase"
                        readonly property string phaseId: modelData.id
                        readonly property string phaseState: modelData.state
                        readonly property string caption: view.phaseCaption(modelData)
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spacing: 4
                        Accessible.role: Accessible.ListItem
                        Accessible.name: modelData.label + ": " + caption
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 4
                            radius: height / 2
                            color: phaseItem.phaseState === "todo" || phaseItem.phaseState === "skipped"
                                   ? Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityOutline)
                                   : view.phaseTone(phaseItem.phaseState)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: phaseItem.modelData.label
                            textFormat: Text.PlainText
                            color: phaseItem.phaseState === "todo" || phaseItem.phaseState === "skipped" ? Tide.Tokens.textMuted
                                                                                             : Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.95
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: phaseItem.caption || " "
                            textFormat: Text.PlainText
                            color: phaseItem.phaseState === "you" || phaseItem.phaseState === "hold" ? view.phaseTone(phaseItem.phaseState)
                                                                                           : Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.85
                            wrapMode: Text.Wrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                visible: view.baseChangeAvailable && !!view.flow
                Text {
                    Layout.fillWidth: true
                    text: "Baza: " + (view.flow && view.flow.base && view.flow.base.baseRef || "unknown")
                          + " · " + (view.flow && view.flow.base && view.flow.base.baseCommit || "unknown")
                    textFormat: Text.PlainText
                    wrapMode: Text.WrapAnywhere
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                }
                Tide.TextButton {
                    objectName: "fullBaseChange"
                    text: "Podejrzyj korektę bazy"
                    onClicked: view.baseChangeRequested()
                }
            }
            ColumnLayout {
                objectName: "fullMainMerge"
                Layout.fillWidth: true
                visible: Boolean(view.mainPhase && view.mainPhase.state === "you")
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: "Karta jest domknięta na swojej gałęzi. Wybierz cel, sprawdź podgląd i potwierdź scalenie."
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.9
                    wrapMode: Text.Wrap
                }
                Tide.TextButton {
                    objectName: "fullBranchDiff"
                    text: "Diff gałęzi"
                    variant: "outline"
                    Accessible.name: "Pokaż zmiany gałęzi karty względem bazy"
                    onClicked: view.branchDiffRequested()
                }
                Tide.TextButton {
                    objectName: "fullPreviewMerge"
                    Layout.fillWidth: true
                    wrapText: true
                    visible: view.mergePreviewAvailable
                    text: "Podgląd scalenia do " + (view.defaultMergeTarget || "…")
                    enabled: view.defaultMergeTarget !== ""
                    variant: "outline"
                    onClicked: view.previewMergeRequested(view.defaultMergeTarget)
                }
                Tide.TextButton {
                    objectName: "fullMergeMain"
                    Layout.fillWidth: true
                    wrapText: true
                    text: view.defaultMergeTarget ? "Scal do " + view.defaultMergeTarget : "Scal…"
                    enabled: !view.mergeBusy
                    Accessible.name: text
                    onClicked: {
                        if (view.defaultMergeTarget) view.previewMergeRequested(view.defaultMergeTarget)
                        else view.chooseMergeRequested()
                    }
                }
                Tide.TextButton {
                    objectName: "fullMergeChoose"
                    text: "Scal…"
                    visible: view.defaultMergeTarget !== ""
                    onClicked: view.chooseMergeRequested()
                }
            }

            // Twój ruch: decyzja do rozstrzygnięcia w miejscu albo jedno zdanie.
            Rectangle {
                objectName: "fullMove"
                Layout.fillWidth: true
                implicitHeight: move.implicitHeight + 40
                radius: Tide.Tokens.radiusCard
                color: view.current ? Tide.Theme.alpha(Tide.Tokens.peach, 0.06) : Tide.Tokens.backgroundCard
                border.width: 1
                border.color: view.current ? Tide.Theme.alpha(Tide.Tokens.peach, 0.32)
                                           : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
                Accessible.role: Accessible.Grouping
                Accessible.name: "Twój ruch"

                ColumnLayout {
                    id: move
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 20
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Text {
                            text: "Twój ruch"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textSecondary
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt
                            font.weight: Font.DemiBold
                        }
                        DecisionKind {
                            objectName: "fullMoveKind"
                            visible: !!view.current
                            decision: view.current
                        }
                        Text {
                            objectName: "fullMoveWho"
                            Layout.fillWidth: true
                            visible: !!view.current
                            text: view.card ? Decision.who(view.card.id, view.current) : ""
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.95
                            elide: Text.ElideRight
                        }
                        Item { Layout.fillWidth: true; visible: !view.current }
                        Tide.TextButton {
                            objectName: "fullAllDecisions"
                            visible: view.pending.length > 1
                            text: "1 z " + view.pending.length + " · wszystkie w oknie decyzji"
                            variant: "link"
                            onClicked: view.decisionsRequested()
                        }
                    }

                    Text {
                        objectName: "fullHeadline"
                        Layout.fillWidth: true
                        visible: !view.current
                        text: view.headline
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 1.25
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                    }

                    DecisionForm {
                        id: form
                        objectName: "fullMoveForm"
                        Layout.fillWidth: true
                        visible: !!view.current
                        decision: view.current
                        onInputNeeded: notice => view.panelRequested(notice)
                        onEscapePressed: view.backRequested()
                        onPreviewRequested: (decisionId, method, params) => view.previewRequested(decisionId, method, params)
                        onAnswered: (decisionId, method, params) => {
                            if (!Decision.keepsPending(params))
                                view.answeredIds = view.answeredIds.concat([decisionId])
                            else form.reset()
                            view.answered(decisionId, method, params)
                            view.forceActiveFocus()
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: !!view.current
                        Item { Layout.fillWidth: true }
                        Tide.TextButton {
                            objectName: "fullChoose"
                            text: form.chooseLabel + "  ↵"
                            variant: "filled"
                            enabled: form.canChoose
                            opacity: enabled ? 1 : 0.45
                            Accessible.name: form.chooseLabel
                            onClicked: form.choose()
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: consentRows.count > 0
                spacing: 8
                Repeater {
                    id: consentRows
                    model: view.flow && view.flow.ticketConsents ? view.flow.ticketConsents : []
                    RowLayout {
                        required property var modelData
                        Layout.fillWidth: true
                        Text {
                            Layout.fillWidth: true
                            text: modelData.ticket + (modelData.active
                                  ? ": automatyczna zgoda na polecenia poza piaskownicą"
                                  : ": zgoda cofnięta — następne polecenie wymaga decyzji")
                            textFormat: Text.PlainText
                            wrapMode: Text.Wrap
                            color: Tide.Tokens.textSecondary
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt
                        }
                        Tide.TextButton {
                            objectName: "revokeTicketConsent"
                            visible: modelData.canRevoke
                            text: "Cofnij zgodę dla " + modelData.ticket
                            onClicked: {
                                view.consentError = ""
                                view.answered("ticket-consent:" + modelData.ticket, "RevokeTicketConsent",
                                    { ticket: modelData.ticket, orderSeq: modelData.orderSeq,
                                      baseVersion: modelData.baseVersion })
                            }
                        }
                    }
                }
                Text {
                    Layout.fillWidth: true
                    visible: view.consentError !== ""
                    text: view.consentError
                    textFormat: Text.PlainText
                    wrapMode: Text.Wrap
                    color: Tide.Tokens.peach
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 20

                // Fale ticketów.
                ColumnLayout {
                    objectName: "fullPlan"
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Text {
                            text: "Plan"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 1.1
                            font.weight: Font.DemiBold
                        }
                        Text {
                            Layout.fillWidth: true
                            text: view.flow && view.tickets.length > 0 ? view.flow.waveLabel || "" : ""
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.95
                        }
                    }
                    Text {
                        objectName: "fullPlanEmpty"
                        Layout.fillWidth: true
                        visible: view.tickets.length === 0 && !view.nowTicket
                        text: "Karta bez planu: jeden ticket, bez fal."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        wrapMode: Text.Wrap
                    }
                    TicketTile {
                        Layout.fillWidth: true
                        visible: !!view.nowTicket
                        ticketId: view.nowTicket ? view.nowTicket.id : ""
                        item: view.nowTicket
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Repeater {
                            model: view.waves
                            delegate: ColumnLayout {
                                id: wave
                                required property var modelData
                                required property int index
                                objectName: "fullWave"
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                Layout.alignment: Qt.AlignTop
                                spacing: 8
                                Text {
                                    text: "Fala " + (wave.index + 1)
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textSecondary
                                    font.family: Tide.Theme.uiFamily
                                    font.pointSize: Tide.Tokens.fontSizePt * 0.95
                                    font.weight: Font.DemiBold
                                }
                                Repeater {
                                    model: wave.modelData
                                    delegate: TicketTile {
                                        required property var modelData
                                        Layout.fillWidth: true
                                        ticketId: modelData
                                        item: view.ticket(modelData)
                                        ticketSettingsEnabled: view.ticketSettingsEnabled
                                        onTicketSettingsRequested: id => view.ticketSettingsRequested(id)
                                    }
                                }
                            }
                        }
                    }
                }

                // Teraz w tle: tylko trwające kroki.
                ColumnLayout {
                    objectName: "fullNowSection"
                    Layout.preferredWidth: 340
                    Layout.maximumWidth: 380
                    Layout.alignment: Qt.AlignTop
                    spacing: 8
                    Text {
                        text: "Teraz w tle"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 1.1
                        font.weight: Font.DemiBold
                    }
                    Text {
                        objectName: "fullNowEmpty"
                        Layout.fillWidth: true
                        visible: view.running.length === 0 && view.stoppedByLimit.length === 0
                        text: view.current ? "Nic nie pracuje w tle — Tide czeka na Twoją decyzję."
                                           : "Nic nie pracuje teraz w tle."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        wrapMode: Text.Wrap
                    }
                    // Kroki zatrzymane przez limit dostawcy (PT-130): czekają na odnowienie, to nie decyzja.
                    // Pola z usługi: `flow.stoppedByLimit[]`, `flow.otherProviders[]`, `flow.limitTransfer`.
                    ColumnLayout {
                        objectName: "fullLimitSection"
                        Layout.fillWidth: true
                        visible: view.stoppedByLimit.length > 0
                        spacing: 6
                        Text {
                            Layout.fillWidth: true
                            text: "Zatrzymane przez limit"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.butter
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt
                            font.weight: Font.DemiBold
                        }
                        Repeater {
                            model: view.stoppedByLimit
                            delegate: Text {
                                required property var modelData
                                objectName: "fullStopped"
                                readonly property string stepId: modelData.step
                                Layout.fillWidth: true
                                text: [modelData.ticket, modelData.step].filter(x => !!x).join(" · ")
                                      + " — " + view.filesLabel(modelData.wipFiles)
                                      + (modelData.until ? " · czeka do " + modelData.until : "")
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt * 0.95
                                wrapMode: Text.Wrap
                            }
                        }
                        Text {
                            objectName: "fullOtherProviders"
                            Layout.fillWidth: true
                            visible: view.otherProviders.length > 0
                            text: "Pozostały limit: " + view.otherProviders.map(
                                      p => view.providerLabel(p.provider) + " " + p.remaining + "%").join(", ")
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.9
                            wrapMode: Text.Wrap
                        }
                        Text {
                            objectName: "fullTransferCost"
                            Layout.fillWidth: true
                            visible: !!view.limitTransfer && view.limitTransfer.available === true
                            text: view.limitTransfer ? view.limitTransfer.cost || "" : ""
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.9
                            wrapMode: Text.Wrap
                        }
                        Tide.TextButton {
                            objectName: "fullTransfer"
                            visible: !!view.limitTransfer && view.limitTransfer.available === true
                            text: view.limitTransfer ? view.limitTransfer.text || "" : ""
                            variant: "outline"
                            enabled: !view.transferSent
                            Accessible.name: text + ". " + (view.limitTransfer ? view.limitTransfer.cost || "" : "")
                            onClicked: view.requestTransfer()
                        }
                        Text {
                            objectName: "fullTransferHidden"
                            Layout.fillWidth: true
                            visible: !!view.limitTransfer && view.limitTransfer.available === false
                            text: view.limitTransfer ? view.limitTransfer.reason || "" : ""
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.9
                            wrapMode: Text.Wrap
                        }
                    }
                    Repeater {
                        model: view.running
                        delegate: Rectangle {
                            id: step
                            required property var modelData
                            readonly property string stepId: modelData.id
                            readonly property string label: [modelData.ticket, modelData.kindLabel, modelData.executor]
                                                            .filter(x => !!x).join(" · ")
                            objectName: "fullNow"
                            Layout.fillWidth: true
                            implicitHeight: stepRow.implicitHeight + 18
                            radius: Tide.Tokens.radiusTile
                            color: Tide.Tokens.backgroundCard
                            Accessible.role: Accessible.ListItem
                            Accessible.name: label
                            RowLayout {
                                id: stepRow
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 10
                                spacing: 10
                                Rectangle {
                                    implicitWidth: 8
                                    implicitHeight: 8
                                    radius: width / 2
                                    color: step.modelData.kind === "review" ? Tide.Tokens.lavender : Tide.Tokens.teal
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Text {
                                        Layout.fillWidth: true
                                        text: step.label
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textPrimary
                                        font.family: Tide.Theme.uiFamily
                                        font.pointSize: Tide.Tokens.fontSizePt * 0.95
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: step.modelData.startedTs ? "od " + view.clock(step.modelData.startedTs) : ""
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textMuted
                                        font.family: Tide.Theme.uiFamily
                                        font.pointSize: Tide.Tokens.fontSizePt * 0.85
                                        elide: Text.ElideRight
                                    }
                                }
                                Tide.TextButton {
                                    objectName: "fullNowEnter"
                                    visible: step.modelData.canEnter === true
                                    text: "Przejmij w Konsoli"  // zatrzymuje pracę w tle i otwiera tę samą sesję (to nie podgląd)
                                    variant: "outline"
                                    Accessible.name: "Wejdź do kroku " + step.label
                                    onClicked: view.enterStepRequested(step.stepId)
                                }
                            }
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: view.noteSteps.length > 0
                Text {
                    text: "Uwagi do kroków"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 1.1
                }
                Repeater {
                    model: view.noteSteps
                    delegate: StepNoteForm {
                        required property var modelData
                        Layout.fillWidth: true
                        stepId: modelData.id
                        caption: [modelData.ticket, modelData.executor, modelData.id].filter(x => !!x).join(" · ")
                        canAdd: modelData.canAddNote === true
                        draft: view.noteDraft(stepId)
                        notes: view.stepNotes.filter(n => n.step === stepId)
                        onEdited: text => view.editNote(stepId, text)
                        onSubmitted: view.sendNote(stepId)
                    }
                }
            }

            // Historia ludzkim językiem: bieżące zlecenie na wierzchu, wcześniejsze zwinięte i tylko do odczytu.
            ColumnLayout {
                objectName: "fullHistory"
                Layout.fillWidth: true
                spacing: 8
                Text {
                    text: "Historia"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 1.1
                    font.weight: Font.DemiBold
                }
                Text {
                    objectName: "fullHistoryEmpty"
                    visible: view.history.length === 0
                    text: "Nic jeszcze się nie wydarzyło."
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                }
                Repeater {
                    model: view.currentHistory
                    delegate: HistoryLine { canResume: modelData.canResume === true }
                }
                Tide.TextButton {
                    objectName: "fullHistoryOld"
                    visible: view.oldHistory.length > 0
                    text: (view.oldOpen ? "▾ " : "▸ ") + "Wcześniejsze zlecenia (" + view.oldHistory.length + ")"
                    variant: "outline"
                    Accessible.name: "Wcześniejsze zlecenia: " + view.oldHistory.length + (view.oldOpen ? ", rozwinięte" : ", zwinięte")
                    onClicked: view.oldOpen = !view.oldOpen
                }
                Repeater {
                    model: view.oldOpen ? view.oldHistory : []
                    delegate: HistoryLine { canResume: false; dimmed: true }
                }
            }

            // Ustawienia karty (PT-134): zapisują się od razu, przyciski nazywają skutek.
            Rectangle {
                id: settings
                objectName: "fullSettings"
                Layout.fillWidth: true
                implicitHeight: settingsColumn.implicitHeight + 36
                radius: Tide.Tokens.radiusCard
                color: Tide.Tokens.backgroundCard
                border.width: 1
                border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
                Accessible.role: Accessible.Grouping
                Accessible.name: "Ustawienia karty"

                ColumnLayout {
                    id: settingsColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 18
                    spacing: 10
                    Text {
                        text: "Ustawienia karty"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 1.1
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "Zmiany zapisują się od razu — bez „Zapisz”. Archiwizacja i zmiana gałęzi najpierw pytają o potwierdzenie."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.9
                        wrapMode: Text.Wrap
                    }
                    Text {
                        objectName: "settingError"
                        Layout.fillWidth: true
                        visible: view.settingError !== ""
                        text: view.settingError
                        textFormat: Text.PlainText
                        color: Tide.Tokens.rose
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        wrapMode: Text.Wrap
                    }

                    // Akcje bez pola (kolumna, odłączenie od zgłoszenia, wstrzymanie, archiwizacja) — z tej samej listy co menu „Karta ▾”.
                    Repeater {
                        model: view.actions.filter(a => !a.input)
                        delegate: RowLayout {
                            id: setting
                            required property var modelData
                            objectName: "fullSetting"
                            Layout.fillWidth: true
                            spacing: 12
                            Tide.TextButton {
                                objectName: "settingButton"
                                Layout.preferredWidth: 220
                                text: setting.modelData.label
                                variant: "outline"
                                textColor: setting.modelData.danger ? Tide.Tokens.rose : Tide.Tokens.textSecondary
                                Accessible.name: setting.modelData.label
                                onClicked: view.run(setting.modelData.id)
                            }
                            Text {
                                Layout.fillWidth: true
                                text: setting.modelData.effect
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt * 0.85
                                wrapMode: Text.Wrap
                            }
                        }
                    }

                    // Zgłoszenie w YouTracku: pole poza Repeaterem, żeby wpisany tekst przeżył nową migawkę.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        QQC2.TextField {
                            id: youtrackField
                            objectName: "settingYoutrackField"
                            Layout.preferredWidth: 220
                            placeholderText: view.storedYoutrack || "np. LEGAL-35"
                            maximumLength: 200
                            selectByMouse: true
                            Accessible.name: "Zgłoszenie w YouTracku (teraz: " + (view.storedYoutrack || "brak") + ")"
                            onAccepted: view.run("youtrack")
                            onTextChanged: view.settingError = ""
                        }
                        Tide.TextButton {
                            objectName: "settingYoutrackButton"
                            text: view.youtrackButton
                            variant: "outline"
                            onClicked: view.run("youtrack")
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "Teraz: " + (view.storedYoutrack || "brak") + "."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.85
                            wrapMode: Text.Wrap
                        }
                    }

                    // Gałąź karty z przepływu ustala Tide: zmiany nie oferujemy, żeby nie obiecywać skutku, którego nie ma.
                    Text {
                        objectName: "settingBranchLocked"
                        Layout.fillWidth: true
                        visible: view.branchLocked
                        text: "Gałąź karty: " + view.shownBranch + ". Ustala ją Tide dla przepływu karty, więc nie da się jej tu zmienić."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.85
                        wrapMode: Text.Wrap
                    }

                    // Gałąź karty: zmiana pyta o potwierdzenie ze skutkiem.
                    RowLayout {
                        visible: !view.branchLocked
                        Layout.fillWidth: true
                        spacing: 12
                        QQC2.TextField {
                            id: branchField
                            objectName: "settingBranchField"
                            Layout.preferredWidth: 220
                            placeholderText: "nazwa gałęzi"
                            maximumLength: 200
                            selectByMouse: true
                            Accessible.name: "Gałąź karty (teraz: " + (view.shownBranch || "brak") + ")"
                            onAccepted: view.run("branch")
                        }
                        Tide.TextButton {
                            objectName: "settingBranchButton"
                            text: view.branchButton
                            variant: "outline"
                            onClicked: view.run("branch")
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "Teraz: " + (view.shownBranch || "brak") + ". Zapyta o potwierdzenie."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.85
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }
        }
    }

    // Kliknięcie poza menu „Karta ▾” zamyka je.
    MouseArea {
        anchors.fill: parent
        visible: view.menuOpen
        z: 4
        onClicked: view.menuOpen = false
    }

    // Menu „Karta ▾”: te same akcje co sekcja ustawień, zakotwiczone pod przyciskiem.
    Rectangle {
        id: cardMenu
        objectName: "fullMenuList"
        visible: view.menuOpen
        z: 5
        // Pozycja liczona przy każdym otwarciu (przycisk przewija się razem z treścią).
        onVisibleChanged: if (visible) {
            const p = menuButton.mapToItem(view, menuButton.width, menuButton.height)
            x = Math.max(8, p.x - width)
            y = p.y + 4
        }
        width: 300
        implicitHeight: menuColumn.implicitHeight + 16
        height: implicitHeight
        radius: Tide.Tokens.radiusCard
        color: Tide.Tokens.backgroundPanel
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
        Accessible.role: Accessible.PopupMenu
        Accessible.name: "Menu karty"
        ColumnLayout {
            id: menuColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 8
            spacing: 2
            Repeater {
                model: view.actions
                delegate: Tide.TextButton {
                    required property var modelData
                    objectName: "fullMenuItem"
                    Layout.fillWidth: true
                    text: modelData.label + (modelData.input || modelData.danger ? "…" : "")
                    accent: modelData.danger ? Tide.Tokens.rose : Tide.Tokens.textSecondary
                    textColor: modelData.danger ? Tide.Tokens.rose : Tide.Tokens.textPrimary
                    Accessible.name: modelData.label
                    onClicked: modelData.input ? view.focusAction(modelData.id) : view.run(modelData.id)
                }
            }
        }
    }

    // Potwierdzenie archiwizacji i zmiany gałęzi: opisuje skutek; odmowa (Esc, „Nie zmieniaj”) niczego nie wysyła.
    Rectangle {
        id: confirmLayer
        objectName: "settingConfirm"
        anchors.fill: parent
        z: 10
        visible: view.confirming !== null
        color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.62)
        MouseArea { anchors.fill: parent; onClicked: view.confirmNo() }
        Rectangle {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 480)
            implicitHeight: confirmColumn.implicitHeight + 40
            height: implicitHeight
            radius: Tide.Tokens.radiusCard
            color: Tide.Tokens.backgroundPanel
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
            Accessible.role: Accessible.AlertMessage
            Accessible.name: view.confirming ? view.confirming.title : ""
            MouseArea { anchors.fill: parent }
            ColumnLayout {
                id: confirmColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 20
                spacing: 12
                Text {
                    objectName: "settingConfirmTitle"
                    Layout.fillWidth: true
                    text: view.confirming ? view.confirming.title : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 1.15
                    font.weight: Font.DemiBold
                    wrapMode: Text.Wrap
                }
                Text {
                    objectName: "settingConfirmEffect"
                    Layout.fillWidth: true
                    text: view.confirming ? view.confirming.effect : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    wrapMode: Text.Wrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Item { Layout.fillWidth: true }
                    Tide.TextButton {
                        objectName: "settingConfirmNo"
                        text: "Nie zmieniaj (Esc)"
                        variant: "outline"
                        onClicked: view.confirmNo()
                    }
                    Tide.TextButton {
                        objectName: "settingConfirmYes"
                        text: view.confirming ? view.confirming.yes : ""
                        variant: "filled"
                        accent: view.confirming && view.confirming.kind === "archive" ? Tide.Tokens.rose : Tide.Tokens.accent
                        onClicked: view.confirmYes()
                    }
                }
            }
        }
    }
}

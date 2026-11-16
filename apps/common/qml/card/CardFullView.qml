// Pełna karta na cały widok okna Tide (K-35, PT-128): u góry pasek 7 faz, pod nim „Twój ruch” — pierwsza czekająca
// decyzja do rozstrzygnięcia w miejscu (ten sam DecisionForm co w oknie decyzji) albo jedno zdanie, gdy nic nie
// czeka — a niżej fale ticketów z ludzkim stanem i „Teraz w tle” z samymi trwającymi krokami.
//
// Stały pasek górny (K-128, PT-502, makieta S-9 „Rama i pasek”, wariant B): Tide.TopBar poza przewijaną treścią —
// powrót z nazwą gospodarza (`backText`: „Tablica” albo „Sesje”; klik i Esc wracają, gdy nie ma otwartego
// potwierdzenia ani menu), projekt, numer i tytuł karty, jedna akcja główna („Porozmawiaj z opiekunem”, gdy opiekun
// ma problem karty) i menu ⋯ z „Zmiany gałęzi”, „Edytuj opis”, „Raport i dowody” oraz wszystkimi pozycjami dawnego
// „Karta ▾” (z tymi samymi potwierdzeniami). Zakładki: Przegląd (cała strona), Plan i Historia (te same sekcje co
// w Przeglądzie, każda osobno — miejsce na tabelę planu i blok historii z PT-503), Opis (Tide.Prose w trybie
// Markdown; `markdownRenderer` gospodarza — Tablica — albo prose.js — Sesje) i Zmiany (ten sam widok zmian gałęzi
// co „Zmiany gałęzi”; bez gałęzi wyłączona z podpowiedzią).
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
// „Ustawienia karty” i menu ⋯ biorą akcje z jednej listy (`actions`), więc zawsze są te same.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide
import "decision.js" as Decision
import "move.js" as Move
import "settings.js" as Settings
import "../git/start.js" as Start
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
    // Zgrupowane zgody (PT-338): ta sama prośba poza piaskownicą z kilku kroków to jedna pozycja (decision.js).
    property var consent: Decision.consentStart()
    // Obciążenie poleceniami poza piaskownicą (decision.outsideLoad) dla prognozy grupy zgód; `null` — bez prognozy (Sesje).
    property var outsideLoad: null
    readonly property var pending: Decision.consentItems(decisions, answeredIds, consent, outsideLoad)
    readonly property var current: pending.length > 0 ? pending[0] : null
    readonly property var phases: derived && Array.isArray(derived.phases) ? derived.phases : []
    // Krycie paska etapu `now` (karta K-97): wspólna faza strony Tablicy; 1.0 = bez mrugania (Sesje nic nie przekazują).
    property real workPulse: 1.0
    readonly property bool hasNowPhase: phases.some(p => p.state === "now")
    readonly property var tickets: derived && Array.isArray(derived.tickets) ? derived.tickets : []
    // Karta „Od razu” (bez planu): jeden kafelek jej ticketu, bez nagłówka fali (PT-333).
    readonly property var nowTicket: TicketLib.tileModel(card, answeredIds)
    readonly property var waves: flow && flow.plan && Array.isArray(flow.plan.waves) ? flow.plan.waves : []
    readonly property var running: flow && Array.isArray(flow.running) ? flow.running : []
    // Zdanie usługi (`flow.headline`) albo nazwany trwający krok (move.js, PT-337).
    readonly property string headline: Move.moveHeadline(flow, phases, tickets)
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
    // „Pokaż starsze” rozwija wcześniejsze zlecenia wewnątrz bloku historii (HistoryBlock.olderOpen).
    property alias oldOpen: historyBlock.olderOpen
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
    // Jedno „Scal…” domkniętej, niescalonej karty (K-99, PT-401): gospodarz otwiera okno scalania z podglądem
    // dla domyślnego celu. Karta scalona pokazuje „Scalono do …” zamiast przycisku.
    signal mergeRequested()
    readonly property bool merged: Boolean(flow && flow.merged)
    signal baseChangeRequested()
    property bool baseChangeAvailable: false
    signal branchDiffRequested()
    property bool mergeBusy: false
    readonly property var mainPhase: phases.find(p => p.id === "main-merge") || null
    signal caretakerTalkRequested()
    signal linkRequested(string url)

    // --- pasek górny i zakładki (PT-502) -------------------------------------------------------------
    // Nazwa widoku-gospodarza dla powrotu („Tablica”, „Sesje”); pusta — bez powrotu.
    property string backText: ""
    // Renderer Markdown gospodarza (Tablica: backend.renderMarkdown); bez niego opis renderuje prose.js (Sesje).
    property var markdownRenderer: null
    // Bieżąca zakładka: overview | plan | history | description („Zmiany” otwiera osobny widok, nie zakładkę).
    property string tab: "overview"
    readonly property bool onOverview: tab === "overview"
    readonly property bool hasBranch: !!(flow && flow.cardBranch)
    readonly property string description: card && card.description ? card.description : ""
    readonly property var tabs: [
        { id: "overview", label: "Przegląd" },
        { id: "plan", label: "Plan" },
        { id: "history", label: "Historia" },
        { id: "description", label: "Opis" },
        { id: "changes", label: "Zmiany", enabled: hasBranch,
          hint: hasBranch ? "Zmiany gałęzi karty względem bazy"
                          : "Karta nie ma jeszcze gałęzi, więc nie ma zmian do pokazania." }
    ]
    // Menu ⋯: akcje dawnego nagłówka, a pod nimi ta sama lista co sekcja „Ustawienia karty” (`actions`).
    readonly property var menuItems: {
        const items = []
        if (hasBranch) items.push({ id: "branch-diff", label: "Zmiany gałęzi", name: "fullBranchDiff" })
        items.push({ id: "description", label: "Edytuj opis", name: "fullDescription" })
        if (card && card.sourceIssue)
            items.push({ id: "source-issue", name: "fullSourceIssue",
                         label: "Raport i dowody " + card.sourceIssue + " · wersja " + (card.sourceIssueVersion || 0) })
        for (const a of actions)
            items.push({ id: "setting:" + a.id, label: a.label + (a.input || a.danger ? "…" : ""), danger: !!a.danger })
        return items
    }

    function selectTab(id) {
        if (id === "changes") {
            if (hasBranch) branchDiffRequested()
            return
        }
        if (tab === id) return
        tab = id
        flick.contentY = 0
    }

    function menuAction(id) {
        if (!card) return
        if (id === "branch-diff") branchDiffRequested()
        else if (id === "description") descriptionRequested()
        else if (id === "source-issue") navigateRequested("issues", card.sourceIssue)
        else if (id.indexOf("setting:") === 0) {
            const action = actions.find(a => a.id === id.slice(8))
            if (!action) return
            if (action.input) focusAction(action.id)
            else run(action.id)
        }
    }

    // --- rzadkie ustawienia (PT-134) -----------------------------------------------------------------
    readonly property string storedBranch: card && card.branch ? card.branch : ""
    readonly property string shownBranch: storedBranch || (flow && flow.cardBranch ? flow.cardBranch : "")
    readonly property string storedYoutrack: card && card.youtrack ? card.youtrack : ""
    // Kolumna widoczna na Tablicy (karta „Do sprawdzenia” ma zapisane todo).
    readonly property string shownColumn: derived && derived.column ? derived.column : (card ? card.column : "")
    readonly property bool paused: !!(flow && flow.paused)
    readonly property bool canPause: !!(derived && (!flow || !flow.plan || flow.plan.status !== "done")
                                        && derived.column !== "review" && !(flow && flow.merged))
    property alias menuOpen: topBar.menuOpen
    property string settingError: ""
    // Pytanie o potwierdzenie: null albo {kind, params, title, effect, yes}.
    property var confirming: null
    readonly property string typedBranch: branchField.text.trim()
    readonly property var typedYoutrack: Settings.issueKey(youtrackField.text)
    // Tickety naraz (PT-375): nadpisanie karty (0 — brak, ustawienie domyślne) i limit obowiązujący z usługi.
    readonly property int storedMaxTickets: card && card.maxTickets ? card.maxTickets : 0
    readonly property int effectiveMaxTickets: flow && flow.ticketLimit ? flow.ticketLimit.limit : 0
    readonly property var typedMaxTickets: Settings.maxTickets(maxTicketsField.text)
    // Priorytet karty (PT-377): kolejność kroków czekających na miejsce u dostawcy; brak w migawce — normalny.
    readonly property string storedPriority: card && Settings.priorityLabel(card.priority) ? card.priority : "normal"

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

    // Menu ⋯ nigdy nie zapisuje pola na ślepo: pozycje z polem przewijają do niego i dają fokus.
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

    // Pola ustawień są w Przeglądzie: z innej zakładki najpierw przełącza, a przewija po ułożeniu treści.
    function focusField(field) {
        if (!onOverview) {
            tab = "overview"
            Qt.callLater(() => { showSettings(); field.forceActiveFocus() })
            return
        }
        showSettings()
        field.forceActiveFocus()
    }

    // Koniec treści bieżącej zakładki (zrzuty i testy „pasek stoi przy przewijaniu”).
    function showEnd() {
        flick.contentY = Math.max(0, flick.contentHeight - flick.height)
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

    // Pusta wartość usuwa nadpisanie (limit z ustawienia przepływu); zmniejszenie niczego nie zatrzymuje.
    function applyMaxTickets() {
        const value = typedMaxTickets
        if (value === null) {
            settingError = "Tickety naraz: liczba od 1 do 6 albo puste pole (ustawienie domyślne)."
            return
        }
        settingError = ""
        if (value === (storedMaxTickets || "")) return
        settingRequested("update", { field: "maxTickets", value: value,
            effect: value === "" ? "Karta " + card.id + ": tickety naraz wg ustawienia domyślnego."
                                 : "Karta " + card.id + ": najwyżej " + value + " tickety naraz. Trwające nie są zatrzymywane." })
    }

    // Priorytet zmienia tylko kolejność czekających kroków; trwających nie zatrzymuje i nie omija limitu dostawcy.
    function applyPriority(value) {
        settingError = ""
        if (!Settings.priorityLabel(value) || value === storedPriority) return
        settingRequested("update", { field: "priority", value: value,
            effect: "Karta " + card.id + ": priorytet " + Settings.priorityLabel(value).toLowerCase()
                    + ". Zmienia kolejność kroków czekających na miejsce u dostawcy." })
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
        consent = Decision.consentStart()
        confirming = null
        menuOpen = false
        settingError = ""
        branchField.text = ""
        youtrackField.text = ""
        form.reset()
        tab = "overview"
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
        // Krok z grupy zgód wraca sam, osobno i z powodem (`consent.reasons`, pokazany, gdy dojdzie na wierzch);
        // pozostałe odpowiedzi grupy zostają przyjęte.
        if (Decision.isSent(consent, decisionId)) {
            consent = Decision.consentFailed(consent, decisionId, message)
            if (current && current.id === decisionId) form.fail(message)
            return
        }
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

    function phaseCaption(phase) {
        if (phase.state === "skipped") return "nie dotyczy"
        const who = phase.executor === "user" ? "ty" : phase.executor
        return [who, clock(phase.ts), phase.note].filter(x => !!x).join(" · ")
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

    // Stały pasek górny: poza Flickable, więc przewijanie treści go nie przesuwa. Leży nad treścią (z), żeby menu ⋯
    // i jego warstwa „klik obok zamyka” przykrywały przewijaną stronę.
    Tide.TopBar {
        id: topBar
        objectName: "fullTopBar"
        name: "full"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        z: 3
        backText: view.backText
        crumbs: view.card && (view.card.projectName || view.card.project) ? [view.card.projectName || view.card.project] : []
        number: view.card ? view.card.id : ""
        title: view.card ? view.card.title : ""
        actionText: view.flow && view.flow.canTalkToCaretaker ? "Porozmawiaj z opiekunem" : ""
        actionName: "talkToCaretaker"
        menuItems: view.menuItems
        tabs: view.tabs
        currentTab: view.tab
        onBack: view.backRequested()
        onActionTriggered: view.caretakerTalkRequested()
        onMenuTriggered: id => view.menuAction(id)
        onTabTriggered: id => view.selectTab(id)
    }

    Flickable {
        id: flick
        objectName: "fullScroll"
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: topBar.bottom
        anchors.bottom: parent.bottom
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        anchors.topMargin: 16
        anchors.bottomMargin: 16
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        QQC2.ScrollBar.vertical: Tide.ScrollBar {}

        ColumnLayout {
            id: content
            width: flick.width
            spacing: 18

            // --- Przegląd: cała dzisiejsza strona karty ---------------------------------------------------
            RowLayout {
                Layout.fillWidth: true
                visible: view.onOverview
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
            // Gałąź karty (dawniej w wierszu okruszków).
            Text {
                objectName: "fullBranch"
                Layout.fillWidth: true
                Layout.topMargin: -10
                visible: view.onOverview && view.hasBranch
                text: view.hasBranch ? "Gałąź karty: " + view.flow.cardBranch : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.lavender
                font.family: Tide.Theme.monoFamily
                font.pointSize: Tide.Tokens.fontSizePt * 0.9
                elide: Text.ElideMiddle
            }

            // Pasek 7 faz.
            RowLayout {
                objectName: "fullPhases"
                Layout.fillWidth: true
                visible: view.onOverview
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
                            objectName: "fullPhaseBar"
                            opacity: phaseItem.phaseState === "now" ? view.workPulse : 1
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

            // „Start od: …” (K-99, PT-389) zastępuje surowe „Baza: refs/heads/… · SHA”.
            CardStartLine {
                objectName: "fullStartLine"
                Layout.fillWidth: true
                visible: view.onOverview
                derived: view.derived
                changeAllowed: view.baseChangeAvailable
                onChangeRequested: view.baseChangeRequested()
            }
            ColumnLayout {
                objectName: "fullMainMerge"
                Layout.fillWidth: true
                visible: view.onOverview && (view.merged || Boolean(view.mainPhase && view.mainPhase.state === "you"))
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    visible: !view.merged
                    text: "Karta jest domknięta na swojej gałęzi. Wybierz cel, sprawdź podgląd i potwierdź scalenie."
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.9
                    wrapMode: Text.Wrap
                }
                Tide.Button {
                    objectName: "fullMerge"
                    text: "Scal…"
                    visible: !view.merged
                    enabled: !view.mergeBusy
                    Accessible.name: text
                    onClicked: view.mergeRequested()
                }
                Text {
                    objectName: "fullMerged"
                    Layout.fillWidth: true
                    visible: view.merged
                    text: Start.mergedLine(view.flow)
                    textFormat: Text.PlainText
                    color: Tide.Tokens.mint
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.95
                    wrapMode: Text.Wrap
                    Accessible.name: text
                }
            }

            // Twój ruch: decyzja do rozstrzygnięcia w miejscu albo jedno zdanie.
            Rectangle {
                objectName: "fullMove"
                Layout.fillWidth: true
                visible: view.onOverview
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
                        Tide.Button {
                            objectName: "fullAllDecisions"
                            visible: view.pending.length > 1
                            text: "1 z " + view.pending.length + " · wszystkie w oknie decyzji"
                            variant: "ghost"
                            small: true
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
                        errors: view.consent.reasons
                        onInputNeeded: notice => view.panelRequested(notice)
                        onEscapePressed: view.backRequested()
                        onPreviewRequested: (decisionId, method, params) => view.previewRequested(decisionId, method, params)
                        onGroupAnswered: ids => view.consent = Decision.consentSent(view.consent, ids)
                        onAnswered: (decisionId, method, params) => {
                            if (Decision.isSent(view.consent, decisionId)) {
                                view.answered(decisionId, method, params)
                                return
                            }
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
                        // Jedyny przycisk główny widoku; pod pytaniem o potwierdzenie główny jest przycisk pytania.
                        Tide.Button {
                            objectName: "fullChoose"
                            text: form.chooseLabel
                            kbd: "↵"
                            variant: view.confirming ? "normal" : "primary"
                            enabled: form.canChoose
                            Accessible.name: form.chooseLabel
                            onClicked: form.choose()
                        }
                    }
                }
            }

            ColumnLayout {
                Layout.fillWidth: true
                visible: view.onOverview && consentRows.count > 0
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
                        Tide.Button {
                            objectName: "revokeTicketConsent"
                            visible: modelData.canRevoke
                            small: true
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

            // Plan i „Teraz w tle”: w Przeglądzie obok siebie; zakładka Plan pokazuje sam plan na całą szerokość.
            // Plan to tabela z przypiętą bieżącą falą (PlanTable.qml, PT-503), nie kafelki fal.
            RowLayout {
                Layout.fillWidth: true
                visible: view.onOverview || view.tab === "plan"
                spacing: 20

                // Plan karty.
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
                        // PT-341: „Ustawienia” jedynego ticketu karty „Od razu” — tylko tam, gdzie okno ma katalog
                        // wykonawców (Tablica); w Sesjach `ticketSettingsEnabled` zostaje false.
                        ticketSettingsEnabled: view.ticketSettingsEnabled
                        onTicketSettingsRequested: id => view.ticketSettingsRequested(id)
                    }
                    // Tabela planu (PT-503): w Przeglądzie do 420 px z własnym przewijaniem, w zakładce Plan na całą
                    // wysokość widoku (nagłówek sekcji i odstęp odejmujemy od dostępnej wysokości).
                    PlanTable {
                        Layout.fillWidth: true
                        visible: view.waves.length > 0
                        waves: view.waves
                        tickets: view.tickets
                        planRows: view.flow && view.flow.plan && Array.isArray(view.flow.plan.rows) ? view.flow.plan.rows : []
                        decisions: view.decisions.filter(d => view.answeredIds.indexOf(d.id) < 0)
                        ticketSettingsEnabled: view.ticketSettingsEnabled
                        fill: view.tab === "plan"
                        availableHeight: flick.height - 52
                        maxHeight: 420
                        onTicketSettingsRequested: id => view.ticketSettingsRequested(id)
                    }
                }

                // Teraz w tle: tylko trwające kroki.
                ColumnLayout {
                    objectName: "fullNowSection"
                    visible: view.onOverview
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
                        Tide.Button {
                            objectName: "fullTransfer"
                            visible: !!view.limitTransfer && view.limitTransfer.available === true
                            text: view.limitTransfer ? view.limitTransfer.text || "" : ""
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
                                Tide.Button {
                                    objectName: "fullNowEnter"
                                    visible: step.modelData.canEnter === true
                                    text: "Przejmij w Konsoli"  // zatrzymuje pracę w tle i otwiera tę samą sesję (to nie podgląd)
                                    small: true
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
                visible: view.onOverview && view.noteSteps.length > 0
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

            // Historia ludzkim językiem w stałym bloku (PT-503): bieżące zlecenie na wierzchu, wcześniejsze za
            // „Pokaż starsze” wewnątrz bloku; blok ma 340 px, w zakładce Historia — całą wysokość widoku.
            HistoryBlock {
                id: historyBlock
                Layout.fillWidth: true
                visible: view.onOverview || view.tab === "history"
                current: view.currentHistory
                older: view.oldHistory
                fill: view.tab === "history"
                availableHeight: flick.height - 40
                onResumeRequested: step => view.enterStepRequested(step)
            }

            // Ustawienia karty (PT-134): zapisują się od razu, przyciski nazywają skutek.
            Rectangle {
                id: settings
                objectName: "fullSettings"
                Layout.fillWidth: true
                visible: view.onOverview
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

                    // Akcje bez pola (kolumna, odłączenie od zgłoszenia, wstrzymanie, archiwizacja) — z tej samej listy co menu ⋯.
                    Repeater {
                        model: view.actions.filter(a => !a.input)
                        delegate: RowLayout {
                            id: setting
                            required property var modelData
                            objectName: "fullSetting"
                            Layout.fillWidth: true
                            spacing: 12
                            Tide.Button {
                                objectName: "settingButton"
                                Layout.preferredWidth: 220
                                text: setting.modelData.label
                                variant: setting.modelData.danger ? "danger" : "normal"
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
                        Tide.Button {
                            objectName: "settingYoutrackButton"
                            text: view.youtrackButton
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

                    // Tickety naraz (PT-375): puste pole = ustawienie domyślne; limit pilnuje usługa przy starcie ticketu.
                    RowLayout {
                        objectName: "settingTicketsRow"
                        Layout.fillWidth: true
                        spacing: 12
                        QQC2.TextField {
                            id: maxTicketsField
                            objectName: "settingMaxTicketsField"
                            Layout.preferredWidth: 220
                            placeholderText: view.effectiveMaxTickets > 0 ? "domyślnie " + view.effectiveMaxTickets : "domyślnie"
                            text: view.storedMaxTickets > 0 ? String(view.storedMaxTickets) : ""
                            maximumLength: 2
                            selectByMouse: true
                            inputMethodHints: Qt.ImhDigitsOnly
                            Accessible.name: "Tickety naraz (teraz: " + (view.storedMaxTickets > 0 ? view.storedMaxTickets : "domyślnie") + ")"
                            onAccepted: view.applyMaxTickets()
                            onTextChanged: view.settingError = ""
                        }
                        Tide.Button {
                            objectName: "settingMaxTicketsButton"
                            text: "Ustaw tickety naraz"
                            onClicked: view.applyMaxTickets()
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "Tickety naraz: " + (view.storedMaxTickets > 0 ? view.storedMaxTickets : "domyślnie"
                                  + (view.effectiveMaxTickets > 0 ? " (" + view.effectiveMaxTickets + ")" : ""))
                                  + ". Puste pole przywraca ustawienie domyślne."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.85
                            wrapMode: Text.Wrap
                        }
                    }

                    // Priorytet (PT-377): zwolnione miejsce u dostawcy bierze najpierw krok karty o wyższym priorytecie.
                    RowLayout {
                        objectName: "settingPriorityRow"
                        Layout.fillWidth: true
                        spacing: 12
                        Text {
                            text: "Priorytet"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textSecondary
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.9
                        }
                        Repeater {
                            model: Settings.PRIORITIES
                            // Bieżący priorytet to zwykły przycisk z obrysem, pozostałe — ciche.
                            delegate: Tide.Button {
                                required property string modelData
                                objectName: "settingPriority_" + modelData
                                text: Settings.priorityLabel(modelData)
                                small: true
                                variant: view.storedPriority === modelData ? "normal" : "ghost"
                                Accessible.name: "Priorytet " + Settings.priorityLabel(modelData).toLowerCase()
                                                 + (view.storedPriority === modelData ? " (teraz)" : "")
                                onClicked: view.applyPriority(modelData)
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "Zwolnione miejsce u dostawcy bierze najpierw krok karty o wyższym priorytecie, "
                                  + "a przy równym — krok czekający dłużej."
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
                        Tide.Button {
                            objectName: "settingBranchButton"
                            text: view.branchButton
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

            // --- Opis: opis karty jako dokument (Tide.Prose w trybie Markdown) --------------------------------
            ColumnLayout {
                objectName: "fullDescriptionTab"
                Layout.fillWidth: true
                visible: view.tab === "description"
                spacing: 12
                Tide.Prose {
                    id: descriptionProse
                    objectName: "fullDescriptionText"
                    Layout.fillWidth: true
                    visible: view.description !== ""
                    text: view.description
                    markdown: true
                    renderer: view.markdownRenderer
                    surface: Tide.Tokens.backgroundWindow
                    onLinkClicked: url => view.linkRequested(url)
                }
                Text {
                    objectName: "fullDescriptionEmpty"
                    Layout.fillWidth: true
                    visible: view.description === ""
                    text: "Karta nie ma opisu. Dodasz go z menu ⋯ → „Edytuj opis”."
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    wrapMode: Text.Wrap
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
                    Tide.Button {
                        objectName: "settingConfirmNo"
                        text: "Nie zmieniaj"
                        kbd: "Esc"
                        Accessible.name: "Nie zmieniaj (Esc)"
                        onClicked: view.confirmNo()
                    }
                    // Archiwizacja jest niszcząca (róża); pozostałe potwierdzenia — główny przycisk pytania.
                    Tide.Button {
                        objectName: "settingConfirmYes"
                        text: view.confirming ? view.confirming.yes : ""
                        variant: view.confirming && view.confirming.kind === "archive" ? "danger" : "primary"
                        onClicked: view.confirmYes()
                    }
                }
            }
        }
    }
}

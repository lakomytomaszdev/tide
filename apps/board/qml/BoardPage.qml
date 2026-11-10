// Zawartość widoku „Tablica” (mock zatwierdzony 27.10.2026): nagłówek z podsumowaniem, szukaniem
// i „＋ Karta”, pastylki tagów (filtr), filtr projektu i układ (Razem / Wiersz na projekt), kolumny
// Backlog, Do zrobienia, W toku, Czeka na mnie, Do sprawdzenia i Gotowe (równe szerokości). Karty przeciąga się
// między kolumnami ręcznymi (Backlog, Do zrobienia, Gotowe) i w obrębie kolumny; kliknięcie otwiera okno decyzji,
// pełną kartę (karta z dziennikiem, PT-128) albo panel karty (karta bez dziennika), „Projekty i tagi” — osobną stronę. Sam nie rozmawia z usługą: dostaje migawkę
// i zgłasza zapisy sygnałami z tagiem, a odpowiedzi dostaje przez handleReply(tag, odpowiedź).
// YouTrack (PT-7): przy nagłówku dyskretne notki z migawki (konflikty, „YouTrack niedostępny”, „N nowych
// zgłoszeń w YouTracku” — ta otwiera import na stronie „Projekty i tagi”).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "card" as CardUi
import "board.js" as Logic

Rectangle {
    id: page
    signal navigateRequested(string viewId, string item)

    property var snapshot: null
    property bool available: true
    property bool preview: false
    property string homeDir: ""
    property real nowTs: Date.now() / 1000
    property bool canLaunch: true        // okno „Co ma zrobić agent?” (LaunchDialog)
    property bool canDiff: false         // PT-5: diff gałęzi karty
    property var markdownRenderer: null  // backend.renderMarkdown: plan i specyfikacja w panelu karty jako bezpieczny HTML
    // Filtry i układ.
    property string tagFilter: ""
    property string projectFilter: ""
    property string query: ""
    property string layoutMode: "together"   // "together" | "lanes"
    property string selectedId: ""
    property var optimistic: []              // [{id, column, before, tag}] — przeniesienia w drodze do usługi
    // Panele.
    property bool managing: false
    property bool panelOpen: false
    property bool planManagerOpen: false
    property string panelCardId: ""          // "" — nowa karta
    property string panelColumn: "backlog"
    property string toast: ""
    property string toastTone: "rose"        // rose — błąd, butter — commit czeka
    // Okno decyzji (PT-132, apps/common/qml/card): karta, której decyzje pokazuje, i tagi wysłanych odpowiedzi.
    // Ustawienie okna Tide (PT-133, apps/common/windowconfig.py; wartość i zapis ma backend): "decision" | "full".
    property string cardOpen: "decision"
    signal cardOpenRequested(string value)
    property bool decisionOpen: false
    property string decisionCardId: ""
    // Pełna karta (PT-128, apps/common/qml/card/CardFullView.qml) na całą stronę.
    property bool fullOpen: false
    onFullOpenChanged: if (!fullOpen) ticketSettings.close()
    property string fullCardId: ""
    // Szuflada opisu (PT-134): panel karty tylko z opisem i „Zapisz opis”, otwierany z pełnej karty.
    property bool descriptionDrawer: false
    // Zdania o skutku rzadkich ustawień czekające na odpowiedź usługi: tag zapisu → zdanie.
    property var settingNotes: ({})
    property string pendingMoveNote: ""
    property bool movingFromFull: false
    // Nazwy kolumn z migawki (id → etykieta) dla podpisów pełnej karty.
    readonly property var columnLabelMap: {
        const out = {}
        for (const c of Logic.columns(snapshot)) out[c.id] = c.label
        return out
    }
    property var decisionTags: ({})          // tag zapisu → [karta, id decyzji]
    // Przeciąganie.
    // Wspólna faza mrugania segmentów „pracuje” (PT-325, PT-327): jeden Timer na stronę, nie na segment. Skacze co 500 ms
    // po trzech poziomach krycia (K-70 pkt 7: „w toku” ma mrugać), zamiast animacji `opacity` z pełną częstotliwością klatek,
    // która przerysowywała całe okno bez przerwy (≈ 206% CPU w pomiarze #5). Biegnie tylko, gdy widok jest aktywny
    // i okno widoczne (animationsActive od BoardView) oraz jest co mrugać.
    property bool animationsActive: false
    property real workPulse: 1.0
    property int workPulseStep: 0
    readonly property var workPulseLevels: [1.0, 0.7, 0.45, 0.7]
    readonly property bool hasWorking: Logic.hasWorkingSegment(allCards)
    // Pełna karta otwarta, widoczna i z etapem `now` na pasku przebiegu (karta K-97).
    readonly property bool fullNowActive: fullOpen && cardFullView.visible && cardFullView.hasNowPhase
    readonly property alias workPulseTimer: workPulseTimer
    Timer {
        id: workPulseTimer
        objectName: "workPulseTimer"
        interval: 500
        repeat: true
        running: page.animationsActive && (page.hasWorking || page.fullNowActive)
        onTriggered: {
            page.workPulseStep = (page.workPulseStep + 1) % page.workPulseLevels.length
            page.workPulse = page.workPulseLevels[page.workPulseStep]
        }
        onRunningChanged: if (!running) { page.workPulseStep = 0; page.workPulse = 1.0 }
    }
    property bool dragging: false
    property string dragId: ""
    readonly property alias ghost: ghost
    property int requestSerial: 0
    // Uruchomienie agenta z karty: okno „Co ma zrobić agent?” i przebieg UpdateCard (opis) →
    // RenderLaunch → /Tasks Start → UpdateCard (gałąź, worktree, wejście, tryb, narzędzie, sprawdzenie, chwila).
    // Pierwszy UpdateCard niesie też dodatkowe projekty wybrane w oknie (PT-28).
    property bool launchOpen: false
    property string launchCardId: ""
    property bool launchFromMove: false
    property int launchOriginIndex: -1        // miejsce karty w Backlogu przed przeniesieniem (Anuluj)
    property string launchOriginBefore: ""
    property var launchChoice: null
    property var launchFlow: null
    property string launchToast: ""
    property string launchDescription: ""
    // Zrzuty i model w oknie uruchomienia (PT-43, jak w oknie „Nowe zadanie”) — dane z backendu widoku.
    property var launchImages: []
    property bool canCapture: false
    property var modelsStatus: null
    property var limitsStatus: null
    property var flowSettings: null
    property var taskDefaults: ({})
    property var pasteImage: function (inTextField) { return false }
    // Start bez pewnej odpowiedzi (termin minął, usługa zniknęła): agent mógł ruszyć, więc zrzuty zostają.
    property bool launchUncertain: false

    signal createRequested(string tag, string request)
    signal updateRequested(string tag, string cardId, string request)
    signal moveRequested(string tag, string cardId, string column, int index)
    signal projectRequested(string tag, string request)
    signal suggestTestsRequested(string tag, string request)   // SuggestProjectTests (okno projektu)
    signal tagRequested(string tag, string request)
    signal sessionRequested(string session)  // PT-5: koordynator karty w widoku Sesje
    signal agentRequested(string session)    // okno agenta karty (Konsola, wątek Codexa)
    signal launchRequested(string cardId)    // otwarto „Co ma zrobić agent?” dla karty
    signal renderRequested(string tag, string cardId, string request)   // /Board RenderLaunch
    signal startRequested(string tag, string request)                   // /Tasks Start
    signal flowRequested(string tag, string cardId, string request)     // /Board StartCard (przepływ karty, „Od razu”)
    signal branchesRequested(string tag, string cardId, string request)
    signal suggestionsRequested(string tag, string cardId, string request)
    signal enterStepRequested(string tag, string cardId, string stepId) // /Board EnterStep
    signal abandonStepRequested(string tag, string cardId, string stepId) // /Board AbandonStep
    signal reviewResolveRequested(string tag, string cardId, string choice) // /Board ResolveCardReview
    signal stepResolveRequested(string tag, string cardId, string request) // /Board ResolveCardStep
    signal ticketConsentRevokeRequested(string tag, string cardId, string request)
    signal stepNoteRequested(string tag, string cardId, string request)
    signal caretakerResolveRequested(string tag, string cardId, string request)
    signal caretakerTalkRequested(string tag, string cardId, string request)
    signal faultPreviewRequested(string tag, string cardId, string request) // /Board PreviewStepFault (PT-125)
    signal faultReportRequested(string tag, string cardId, string request)  // /Board ReportStepFault (PT-125)
    signal flowSettingsSaveRequested(string tag, string request) // /Board SetCardFlowSettings
    signal flowSettingsRefreshRequested() // /Board GetCardFlowSettings
    signal pauseRequested(string tag, string cardId, bool paused)
    signal planEditRequested(string tag, string cardId, string request)
    signal planApproveRequested(string tag, string cardId)       // /Board ApproveCardPlan
    signal environmentApproveRequested(string tag, string cardId, string yaml, bool onlyPlan)   // /Board ApproveCardEnvironment
    signal branchDiffRequested(string tag, string cardId)
    signal mergeMainRequested(string tag, string cardId)
    signal previewMergeRequested(string tag, string cardId, string request)
    signal mergeRequested(string tag, string cardId, string request)
    signal previewBaseChangeRequested(string tag, string cardId, string request)
    signal requestBaseChangeRequested(string tag, string cardId, string request)

    function openBaseChange(cardId) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id === cardId)
        if (card && card.derived && card.derived.flow && card.derived.flow.baseDecisionPending) {
            baseChangePreview.show(cardId, null)
            baseChangePreview.busy = true
            const tag = nextTag("base-change:" + cardId)
            baseChangePreview.requestTag = tag
            previewBaseChangeRequested(tag, cardId, "{}")
        } else {
            baseChangePreview.select(cardId)
            baseChangePicker.reset()
        }
    }

    function mergeContext(cardId) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id === cardId)
        const project = card && (snapshot.projects || []).find(p => p.slug === card.project)
        return card ? cardId + ":" + card.project + ":" + (project && project.repo || "") : ""
    }
    function chooseMerge(cardId) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id === cardId)
        const base = card && card.derived && card.derived.flow && card.derived.flow.base
        mergeTargets.cardId = cardId
        mergeTargets.contextKey = mergeContext(cardId)
        mergeTargets.baseRef = base && base.status === "known" ? base.baseRef : ""
        mergeTargets.open()
    }
    function previewMerge(cardId, target, targetRef) {
        const tag = nextTag("preview-merge:" + cardId)
        mergePreview.show(tag, cardId, target)
        mergePreview.contextKey = mergeContext(cardId)
        mergePreview.targetRef = targetRef || "refs/heads/" + target
        previewMergeRequested(tag, cardId, JSON.stringify({targetRef: mergePreview.targetRef}))
    }
    signal launchClosed(bool started)        // okno uruchomienia zamknięte: zrzuty zostają agentowi albo znikają
    signal captureRequested()                // „Zrób zrzut” (Spectacle; widok chowa okno)
    signal removeImageRequested(string path)
    signal filesDropped(var urls)            // pliki obrazów przeciągnięte na okno uruchomienia
    signal modelsRequested()                 // widok Modele (rola `agent`, program agenta lokalnego)
    signal docsRequested(string cardId)      // /Board CardDocs — plan i specyfikacja w panelu karty
    signal fileRequested(string path, string how)   // dokument karty: "open" — edytor, "folder" — menedżer plików
    signal linkRequested(string url)         // link http(s) z planu albo specyfikacji
    signal youtrackIssuesRequested(string tag, bool refresh)            // /Board YouTrackIssues
    signal importRequested(string tag, string request)                  // /Board ImportYouTrack
    signal resolveRequested(string tag, string ident, string choice)    // /Board ResolveYouTrack

    readonly property var filters: ({ tag: tagFilter, project: projectFilter, query: query })
    // Bez filtrów `cards` to dokładnie `allCards` (jedno przeliczenie `Logic.visible` na zmianę migawki, nie dwa).
    readonly property var allCards: Logic.visible(snapshot, null, optimistic)
    readonly property var cards: filtered ? Logic.visible(snapshot, filters, optimistic) : allCards
    // Kolumny jako model Repeaterów: nowa tablica przebudowałaby je wszystkie, więc tożsamość zmienia się tylko
    // wtedy, gdy zmieniła się ich treść (PT-324).
    property var columnSpecs: Logic.columns(null)
    function refreshColumnSpecs() {
        const next = Logic.columns(snapshot)
        if (!Logic.sameValue(next, columnSpecs)) columnSpecs = next
    }
    Component.onCompleted: { refreshColumnSpecs(); refreshDerived() }
    readonly property bool exists: !!snapshot && snapshot.exists === true
    readonly property bool filtered: tagFilter !== "" || projectFilter !== "" || query.trim() !== ""

    // Wartości pochodne o stabilnej tożsamości (PT-326): każda jest liczona raz na zmianę migawki (wersja „Raw”),
    // a właściwość, którą widzą wiązania, dostaje nową wartość tylko wtedy, gdy zmieniła się jej treść. Niezmieniona
    // karta panelu, notki YouTracka czy wiersze nie odpalają więc zależnych wiązań ani nie przebudowują Repeaterów.
    readonly property var panelCardRaw: panelCardId ? (snapshot && snapshot.cards || []).find(c => c.id === panelCardId) || null : null
    readonly property var decisionCardRaw: decisionCardId ? (snapshot && snapshot.cards || []).find(c => c.id === decisionCardId) || null : null
    readonly property var fullCardRaw: fullCardId ? (snapshot && snapshot.cards || []).find(c => c.id === fullCardId) || null : null
    readonly property var errorLinesRaw: Logic.errorLines(snapshot, 3)
    readonly property var youtrackNotesRaw: Logic.youtrackNotes(snapshot)
    // Wiersze projektów liczone tylko w widoku wierszy; w „Razem” nikt ich nie czyta.
    readonly property var lanesRaw: layoutMode === "lanes" ? Logic.groupLanes(cards) : []
    property var panelCard: null
    property var decisionCard: null
    property var fullCard: null
    property var errorLines: []
    property var youtrackNotes: []
    property var lanes: []
    property var lanesByKey: ({})     // klucz wiersza → wiersz (te same obiekty co w `lanes`)
    // Model Repeatera wierszy: tylko {laneKey, laneName}, więc zmiana kart w wierszu nie niszczy jego delegata.
    ListModel { id: laneModel }
    onPanelCardRawChanged: panelCard = Logic.stable(panelCardRaw, panelCard)
    onDecisionCardRawChanged: decisionCard = Logic.stable(decisionCardRaw, decisionCard)
    onFullCardRawChanged: fullCard = Logic.stable(fullCardRaw, fullCard)
    onErrorLinesRawChanged: errorLines = Logic.stable(errorLinesRaw, errorLines)
    onYoutrackNotesRawChanged: youtrackNotes = Logic.stable(youtrackNotesRaw, youtrackNotes)
    onLanesRawChanged: refreshLanes()
    function refreshDerived() {
        panelCard = Logic.stable(panelCardRaw, panelCard)
        decisionCard = Logic.stable(decisionCardRaw, decisionCard)
        fullCard = Logic.stable(fullCardRaw, fullCard)
        errorLines = Logic.stable(errorLinesRaw, errorLines)
        youtrackNotes = Logic.stable(youtrackNotesRaw, youtrackNotes)
        refreshLanes()
    }
    function refreshLanes() {
        const next = Logic.stableLanes(lanesRaw, lanes)
        if (next === lanes) return
        lanes = next
        const byKey = {}
        for (const lane of next) byKey[lane.key] = lane
        lanesByKey = byKey
        // Model wierszy zmienia się tylko tam, gdzie wiersz doszedł, zniknął, zmienił miejsce albo nazwę.
        for (let i = 0; i < next.length; i++) {
            const lane = next[i]
            let at = -1
            for (let j = i; j < laneModel.count; j++) {
                if (laneModel.get(j).laneKey === lane.key) { at = j; break }
            }
            if (at < 0) laneModel.insert(i, { laneKey: lane.key, laneName: lane.name })
            else {
                if (at !== i) laneModel.move(at, i, 1)
                if (laneModel.get(i).laneName !== lane.name) laneModel.setProperty(i, "laneName", lane.name)
            }
        }
        if (laneModel.count > next.length) laneModel.remove(next.length, laneModel.count - next.length)
    }
    readonly property alias cardPanel: cardPanel
    readonly property alias launchDialog: launchDialog
    readonly property alias decisionDialog: decisionDialog
    readonly property alias cardFullView: cardFullView
    readonly property alias managePage: manage
    readonly property alias columnsRow: columnsRow

    function nextTag(prefix) {
        requestSerial++
        return prefix + ":" + requestSerial
    }

    // Kolor kropki tagu: pastel z palety po nazwie z migawki.
    function tagTone(name) {
        const color = Logic.tagColor(snapshot, name)
        if (color && Tide.Tokens[color] !== undefined) return Tide.Tokens[color]
        const hex = snapshot && snapshot.colors ? snapshot.colors[color] : ""
        return hex ? hex : Tide.Tokens.textMuted
    }

    // --- karty ----------------------------------------------------------------------------------

    function openCard(id) {
        selectedId = id
        panelCardId = id
        panelOpen = true
        cardPanel.load()
    }

    onPanelOpenChanged: if (!panelOpen) descriptionDrawer = false

    function newCard(column) {
        descriptionDrawer = false
        if (managing) managing = false
        panelCardId = ""
        panelColumn = column || "backlog"
        panelOpen = true
        cardPanel.load()
    }

    function closePanel() {
        panelOpen = false
        const item = findCard(selectedId)
        if (item) item.forceActiveFocus()
        else page.forceActiveFocus()
    }

    // Kliknięcie kafelka: karta z decyzjami w „Czeka na mnie” otwiera okno decyzji albo (ustawienie „Pełna karta”)
    // pełną kartę; inne karty z przepływem — zawsze pełną kartę (PT-128), karty bez dziennika — panel karty.
    function activateCard(id) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id === id)
        if (Logic.opensDecisions(card, cardOpen)) openDecisions(id)
        else if (!openFull(id)) openCard(id)
    }

    // --- pełna karta (PT-128) -------------------------------------------------------------------

    function openFull(id) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id === id)
        if (!Logic.hasFullCard(card)) return false
        panelOpen = false
        decisionOpen = false
        selectedId = id
        fullCardId = id
        fullOpen = true
        cardFullView.start()
        return true
    }

    function closeFull() {
        if (!fullOpen) return
        fullOpen = false
        const item = findCard(fullCardId)
        if (item) item.forceActiveFocus()
        else page.forceActiveFocus()
    }

    // Szuflada szybkiej edycji opisu nad pełną kartą (PT-134): tylko opis, „Zapisz opis”, Esc anuluje bez zapisu.
    function openDescription(id) {
        descriptionDrawer = true
        openCard(id)
    }

    // Rzadkie ustawienia z pełnej karty (PT-134) zapisują się od razu. Zdanie o skutku (`params.effect`) pokazuje
    // się po odpowiedzi usługi — błąd usługi też, więc żadna zmiana nie przechodzi po cichu.
    function applySetting(id, kind, params) {
        if (kind === "move" || kind === "archive") {
            // moveCard sam nadaje tag zapisu i może nic nie wysłać (karta już tam stoi) — notka czeka w pendingMoveNote.
            pendingMoveNote = params.effect || ""
            // Z pełnej karty przenosimy bez okna „Co ma zrobić agent?” (byłoby ukryte pod pełną kartą).
            movingFromFull = true
            const sent = moveCard(id, kind === "archive" ? "archived" : params.column, kind === "archive" ? "" : topOf(params.column))
            movingFromFull = false
            pendingMoveNote = ""
            if (kind === "archive" && sent) closeFull()
        } else if (kind === "pause") {
            const tag = nextTag("flow-pause:" + id)
            addSettingNote(tag, params.effect)
            pauseRequested(tag, id, params.paused)
        } else if (kind === "update") {
            const tag = nextTag("setting:" + id)
            addSettingNote(tag, params.effect)
            const request = {}
            request[params.field] = params.value
            updateRequested(tag, id, JSON.stringify(request))
        }
    }

    function addSettingNote(tag, text) {
        const notes = Object.assign({}, settingNotes)
        notes[tag] = text || ""
        settingNotes = notes
    }

    // Zdanie o skutku zapisanego ustawienia (raz): wraca do użytkownika po odpowiedzi usługi.
    function takeSettingNote(tag) {
        if (!(tag in settingNotes)) return null
        const notes = Object.assign({}, settingNotes)
        const text = notes[tag]
        delete notes[tag]
        settingNotes = notes
        return text
    }

    // Panel karty z pełnej karty: wybór wykonawcy (napis `notice`) zostaje tam.
    function panelFromFull(notice) {
        const id = fullCardId
        fullOpen = false
        openCard(id)
        if (notice) cardPanel.notice = notice
    }

    // Powiadomienie o karcie (Show z ID karty): zaznacza ją, a gdy czeka na decyzję — otwiera ją tak samo
    // jak klik (to samo ustawienie). Inne karty tylko zaznacza, jak dotąd.
    function openFromNotification(id) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id.toLowerCase() === String(id).toLowerCase())
        if (Logic.hasDecisions(card)) activateCard(card.id)
    }

    function setCardOpen(value) {
        if (value === cardOpen) return
        cardOpenRequested(value)
    }

    // --- ustawienia ticketu (PT-334) ------------------------------------------------------------

    property string ticketSettingsTag: ""
    // Tagi ostatnich przepięć trwającego ticketu (PT-335) osobno dla pól „author” i „reviewer”.
    property var ticketReassignTags: ({})

    // Wiersz planu ticketu z migawki pełnej karty (`editable` liczy usługa).
    function openTicketSettings(ticketId) {
        const plan = cardFullView.flow && cardFullView.flow.plan
        let row = plan && Array.isArray(plan.rows) ? plan.rows.find(r => (r.id || r.workId) === ticketId) : null
        // Karta „Od razu” (PT-341) nie ma wiersza planu: okno dostaje wiersz zastępczy z kafelka ticketu. `noPlanRow`
        // mówi mu, że klasa to domyślny „standard” (tylko do odczytu), a `editable: false` — że ticket już jest w toku.
        const now = cardFullView.nowTicket
        if (!row && now && now.id === ticketId)
            row = { id: now.id, workId: now.id, title: now.title, executor: now.executor, reviewer: now.reviewer,
                    editable: false, noPlanRow: true }
        ticketSettings.open(ticketId, row || null)
    }

    // --- okno decyzji (PT-132) ------------------------------------------------------------------

    function openDecisions(id) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id === id)
        if (!card || Logic.decisions(card).length === 0) return false
        panelOpen = false
        selectedId = id
        decisionCardId = id
        decisionOpen = true
        decisionDialog.start()
        return true
    }

    function closeDecisions() {
        if (!decisionOpen) return
        decisionOpen = false
        // Okno otwarte z pełnej karty („wszystkie w oknie decyzji”) wraca do niej.
        if (fullOpen && fullCardId === decisionCardId) {
            cardFullView.start()
            return
        }
        const item = findCard(decisionCardId)
        if (item) item.forceActiveFocus()
        else page.forceActiveFocus()
    }

    // Odpowiedź z okna: metoda /Board i parametry wprost z decyzji (bez liczenia na stronie).
    function sendDecision(cardId, decisionId, method, params) {
        const tag = nextTag("decision:" + cardId)
        const tags = Object.assign({}, decisionTags)
        tags[tag] = [cardId, decisionId]
        decisionTags = tags
        if (method === "ResolveCardStep") stepResolveRequested(tag, cardId, JSON.stringify(params))
        else if (method === "RevokeTicketConsent") ticketConsentRevokeRequested(tag, cardId, JSON.stringify(params))
        else if (method === "AddCardStepNote") stepNoteRequested(tag, cardId, JSON.stringify(params))
        else if (method === "ResolveCardCaretaker") caretakerResolveRequested(tag, cardId, JSON.stringify(params))
        else if (method === "ResolveCardReview") reviewResolveRequested(tag, cardId, params.choice || "")
        else if (method === "ApproveCardPlan") planApproveRequested(tag, cardId)
        else if (method === "EditCardPlan") planEditRequested(tag, cardId, JSON.stringify(params))
        else if (method === "EnterStep") enterStepRequested(tag, cardId, params.step || "")
        else if (method === "TalkToCaretaker") caretakerTalkRequested(tag, cardId, JSON.stringify(params))
        else if (method === "ReportStepFault") faultReportRequested(tag, cardId, JSON.stringify(params))
        else handleReply(tag, { ok: false, error: "unknown-method", message: "Tablica nie zna metody " + method + "." })
    }

    // Podgląd karty z błędem przed zapisem (PT-125): odpowiedź usługi wraca do okna decyzji.
    function previewFault(cardId, decisionId, method, params) {
        const tag = nextTag("fault-preview:" + cardId)
        const tags = Object.assign({}, decisionTags)
        tags[tag] = [cardId, decisionId]
        decisionTags = tags
        if (method === "PreviewStepFault") faultPreviewRequested(tag, cardId, JSON.stringify(params))
        else handleReply(tag, { ok: false, error: "unknown-method", message: "Tablica nie zna metody " + method + "." })
    }

    function handleFaultPreviewReply(tag, reply) {
        const sent = decisionTags[tag]
        const tags = Object.assign({}, decisionTags)
        delete tags[tag]
        decisionTags = tags
        if (!sent) return
        // Podgląd zamówiony w bloku „Twój ruch” pełnej karty wraca tam; z okna decyzji — do okna.
        if (fullOpen && !decisionOpen && sent[0] === fullCardId) cardFullView.showPreview(sent[1], reply)
        else if (sent[0] === decisionCardId) decisionDialog.showPreview(sent[1], reply)
    }

    function handleDecisionReply(tag, error, reply) {
        const sent = decisionTags[tag]
        const tags = Object.assign({}, decisionTags)
        delete tags[tag]
        decisionTags = tags
        if (sent && sent[1].indexOf("step-note:") === 0) {
            cardFullView.noteReply(sent[1].substring(10), error, reply)
            return
        }
        if (sent && !error && reply && reply.preview) baseChangePreview.show(sent[0], reply.preview)
        if (sent && !error && reply && reply.reported)
            showToast((reply.action === "append" ? "Wystąpienie dopisane do " : "Karta z błędem ") + reply.reported
                      + " w Backlogu " + (reply.project || "plasma-tide") + " — krok ponowiony.", "butter")
        if (!sent || !error) return
        // Odpowiedź z bloku „Twój ruch” pełnej karty: decyzja wraca tam z powodem.
        if (fullOpen && !decisionOpen && sent[0] === fullCardId) {
            cardFullView.failed(sent[1], error)
            return
        }
        // Usługa odrzuciła odpowiedź: decyzja wraca do okna z powodem (okno otwiera się ponownie, jeśli zamknięte).
        if (!decisionOpen && sent[0] === decisionCardId) {
            decisionOpen = true
            decisionDialog.forceActiveFocus()
        }
        if (sent[0] === decisionCardId) decisionDialog.failed(sent[1], error)
        else showToast("Nie wysłano odpowiedzi dla " + sent[0] + ": " + error, "rose")
    }

    function openPlanManager() {
        panelOpen = false
        planManager.error = ""
        planManager.notice = ""
        planManagerOpen = true
    }

    // Przeniesienie karty (przeciągnięcie, klawiatura, przycisk): pozycja liczona wśród kart
    // zapisanych w kolumnie docelowej, karta od razu stoi w nowym miejscu (optimistic).
    function moveCard(id, column, before) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id === id)
        if (!card) return false
        if (column !== "archived" && !Logic.isManual(column)) {
            showToast("Do kolumny „" + Logic.columnLabel(snapshot, column) + "” karta trafia sama, gdy pracuje nad nią agent.", "rose")
            return false
        }
        const confirming = Logic.confirmable(card)
        if (confirming && Logic.dropTargets(card).indexOf(column) < 0) {
            showToast("Kartę z „" + Logic.columnLabel(snapshot, Logic.displayColumn(card)) + "” przenosisz do Gotowych "
                      + "(sprawdzone) albo z powrotem do Do zrobienia (do poprawy).", "rose")
            return false
        }
        const index = column === "archived" ? 0 : Logic.moveIndex(snapshot, id, column, before)
        // Bez zmiany tylko wtedy, gdy karta już stoi w tej kolumnie także na ekranie (kolumna wyliczana
        // z koordynatora przy zapisanym todo to nie „ta sama kolumna”).
        if (column === Logic.displayColumn(card) && column === card.column && Logic.storedIndex(snapshot, id) === (index < 0
                ? snapshot.cards.filter(c => c.column === column).length - 1 : index)) return false
        const fromBacklog = column === "todo" && card.column === "backlog"
        const origin = fromBacklog ? Logic.storedIndex(snapshot, id) : -1
        const backlogList = fromBacklog ? Logic.column(allCards, "backlog") : []
        const at = backlogList.findIndex(c => c.id === id)
        const originBefore = at >= 0 && at + 1 < backlogList.length ? backlogList[at + 1].id : ""
        const tag = nextTag("move:" + id)
        if (pendingMoveNote) addSettingNote(tag, pendingMoveNote)
        optimistic = optimistic.concat([{ id: id, column: column, before: before || "", tag: tag }])
        moveRequested(tag, id, column, index)
        if (fromBacklog && !movingFromFull) launchHook(id, origin, originBefore)
        return true
    }

    // Gotowe dostają kartę na górę (najnowsze pierwsze), inne kolumny — na koniec.
    function topOf(column) {
        if (column !== "done") return ""
        const first = Logic.column(allCards, "done")[0]
        return first ? first.id : ""
    }

    // Po przeniesieniu z Backlogu do Do zrobienia: „Co ma zrobić agent?”.
    function launchHook(id, origin, originBefore) {
        if (canLaunch) openLaunch(id, true, origin, originBefore)
    }

    // --- uruchomienie agenta z karty --------------------------------------------------------------

    function openLaunch(id, fromMove, origin, originBefore) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id === id)
        if (!card) return
        if (launchOpen && launchDialog.busy) return   // trwa uruchomienie — nie ruszamy jego okna ani zrzutów
        if (launchOpen && launchCardId !== id) launchClosed(launchUncertain)   // zrzuty poprzedniej karty nie przechodzą dalej
        if (!launchOpen || launchCardId !== id) launchUncertain = false
        panelOpen = false
        managing = false
        selectedId = id
        launchCardId = id
        launchFromMove = !!fromMove
        launchOriginIndex = origin === undefined ? -1 : origin
        launchOriginBefore = originBefore || ""
        launchChoice = null
        launchFlow = null
        launchOpen = true
        launchDialog.load()
        launchDialog.forceActiveFocus()
        launchRequested(id)
    }

    // started: agent ruszył — zrzuty zostają w katalogu zadania dla niego; inaczej katalog znika.
    function closeLaunch(started) {
        launchOpen = false
        launchDialog.busy = false
        launchClosed(!!started || launchUncertain)
        const item = findCard(launchCardId)
        if (item) item.forceActiveFocus()
        else page.forceActiveFocus()
    }

    // Anuluj: po przeniesieniu karta wraca na swoje miejsce w Backlogu; z przycisku — tylko zamykamy.
    function cancelLaunch() {
        const id = launchCardId
        if (launchFromMove) {
            const tag = nextTag("move:" + id)
            optimistic = optimistic.filter(m => m.id !== id)
                .concat([{ id: id, column: "backlog", before: launchOriginBefore, tag: tag }])
            moveRequested(tag, id, "backlog", launchOriginIndex)
        }
        closeLaunch()
    }

    function startLaunch(choice, description, descriptionChanged) {
        launchChoice = choice
        launchDialog.error = ""
        launchDialog.busy = true
        const storedCard = (snapshot && snapshot.cards || []).find(c => c.id === launchCardId)
        const planChanged = choice.entry === "plan" && choice.planPath !== ((storedCard || {}).planPath || "")
        if (descriptionChanged || choice.projectsChanged || planChanged) {
            // Opis i dodatkowe projekty (PT-28; repozytorium spoza tablicy zakłada projekt) jednym zapisem.
            const request = {}
            if (descriptionChanged) request.description = description
            if (choice.projectsChanged) request.projects = choice.projects
            if (planChanged) request.planPath = choice.planPath
            launchDescription = descriptionChanged ? description : launchDialog.loadedDescription
            launchDialog.busyText = "Zapisuję zadanie w karcie…"
            updateRequested(nextTag("launch-desc:" + launchCardId), launchCardId, JSON.stringify(request))
        } else {
            continueLaunch()
        }
    }

    // Po zapisie opisu przepływ karty dostaje zlecenie przez /Board StartCard.
    function continueLaunch() {
        if (Logic.isFlowEntry(launchChoice.entry, launchChoice.mode)) {
            launchDialog.busyText = launchChoice.entry === "planning" && launchChoice.mode === "full"
                ? "Zakładam gałąź karty i otwieram Konsolę…" : "Zakładam gałąź karty i startuję krok w tle…"
            flowRequested(nextTag("launch-flow:" + launchCardId), launchCardId,
                          JSON.stringify(Logic.startCardRequest(launchChoice, launchChoice.images)))
        } else {
            renderLaunch()
        }
    }

    function renderLaunch() {
        const c = launchChoice
        launchDialog.busyText = "Przygotowuję polecenie…"
        const request = { entry: c.entry, tool: c.tool, check: c.check }
        if (c.entry === "spec") { request.baseRef = c.baseRef; request.baseCommit = c.baseCommit }
        if (c.entry === "planning") request.preferredProvider = c.preferredProvider
        if (c.entry !== "spec") request.mode = c.mode   // tryb dotyczy tylko Planowania
        if (c.entry !== "spec" && c.newWorktree && c.branch) request.branch = c.branch
        renderRequested(nextTag("launch-render:" + launchCardId), launchCardId, JSON.stringify(request))
    }

    function failLaunch(text) {
        launchDialog.busy = false
        launchDialog.error = text
    }

    function handleLaunchReply(tag, reply) {
        const error = reply && reply.ok ? "" : Logic.errorText(reply)
        const id = tag.split(":")[1]
        if (tag.indexOf("launch-project:") === 0) {
            if (error) launchDialog.error = "Nie przypisano projektu: " + error
            else Qt.callLater(() => launchDialog.reloadProject())
            return
        }
        if (id !== launchCardId) return
        if (tag.indexOf("launch-desc:") === 0) {
            if (error) return failLaunch("Nie zapisano zadania w karcie: " + error)
            launchDialog.loadedDescription = launchDescription   // ponowienie po błędzie nie zapisuje drugi raz
            continueLaunch()
        } else if (tag.indexOf("launch-flow:") === 0) {
            if (error && (!reply || reply.error === "no-service")) launchUncertain = true
            if (error) return failLaunch(error)
            const flowCard = (snapshot && snapshot.cards || []).find(c => c.id === launchCardId)
            if (flowCard && flowCard.column === "backlog" && !launchFromMove) {   // z popupu: karta z Backlogu idzie do kolejki
                const moveTag = nextTag("move:" + launchCardId)
                optimistic = optimistic.concat([{ id: launchCardId, column: "todo", before: "", tag: moveTag }])
                moveRequested(moveTag, launchCardId, "todo", -1)
            }
            if (launchChoice.entry === "plan") {
                launchToast = "Plan przejęty do karty " + launchCardId + "; trwa sprawdzenie i przygotowanie do zatwierdzenia"
            } else if (launchChoice.entry === "planning") {
                launchToast = launchChoice.mode === "full"
                    ? "Planowanie Pełne karty " + launchCardId + " ruszyło w Konsoli"
                    : (reply.queued ? "Planowanie czeka na wolne miejsce: karta "
                                    : "Ruszyło planowanie w tle karty ") + launchCardId
            } else {
                launchToast = (reply.queued ? "Krok czeka na wolne miejsce: " : "Ruszył krok w tle: ")
                              + (reply.ticket || "ticket") + " karty " + launchCardId
            }
            if (launchChoice.entry !== "plan" && !(launchChoice.entry === "planning" && launchChoice.mode === "full"))
                launchToast += " — " + (reply.executor || launchChoice.executor) + ", bez Konsoli"
            closeLaunch(true)
            showToast(launchToast, "mint")
        } else if (tag.indexOf("launch-render:") === 0) {
            if (error) return failLaunch("Nie przygotowano polecenia: " + error)
            launchFlow = reply
            launchDialog.busyText = launchChoice.entry === "spec" ? "Otwieram Konsolę ze wskazaną rewizją…"
                                                                  : "Zakładam worktree i otwieram kartę Konsoli z agentem…"
            startRequested(nextTag("launch-start:" + launchCardId), JSON.stringify(Logic.launchStartRequest(reply, launchChoice)))
        } else if (tag.indexOf("launch-start:") === 0) {
            if (error && (!reply || reply.error === "no-service")) launchUncertain = true
            if (error) return failLaunch(error)
            updateRequested(nextTag("launch-mark:" + launchCardId), launchCardId,
                            JSON.stringify(Logic.launchMarkRequest(reply, launchChoice, Logic.isoNow(Date.now()))))
            const card = (snapshot && snapshot.cards || []).find(c => c.id === launchCardId)
            if (card && card.column === "backlog" && !launchFromMove) {   // z popupu: karta z Backlogu idzie do kolejki
                const tag2 = nextTag("move:" + launchCardId)
                optimistic = optimistic.concat([{ id: launchCardId, column: "todo", before: "", tag: tag2 }])
                moveRequested(tag2, launchCardId, "todo", -1)
            }
            launchToast = "Agent ruszył: " + (reply.title || launchCardId) + " — " + Logic.launchToolName(reply.tool)
                + (reply.local && reply.local.model ? " na " + reply.local.model : "")
                + (reply.worktree ? ", worktree " + reply.branch : ", katalog projektu")
            closeLaunch(true)
            showToast(launchToast, "mint")
        } else if (tag.indexOf("launch-mark:") === 0) {
            if (error) showToast("Agent ruszył, ale karta " + id + " nie zapamiętała uruchomienia: " + error, "rose")
            else if (Logic.commitNote(reply)) showToast(Logic.commitNote(reply), "butter")
        }
    }

    // Import z YouTracka: strona „Projekty i tagi” z otwartą listą zgłoszeń (pobieraną od razu).
    function openImport() {
        panelOpen = false
        managing = true
        manage.openImport()
    }

    function saveCard(request) {
        if (panelCardId) updateRequested(nextTag("update:" + panelCardId), panelCardId, JSON.stringify(request))
        else createRequested(nextTag("create"), JSON.stringify(request))
    }

    function showToast(text, tone) {
        toast = text
        toastTone = tone || "rose"
        toastTimer.restart()
    }

    onManagingChanged: if (managing) flowSettingsRefreshRequested()

    // Odpowiedź usługi na zapis (tag nadany przy wysyłce).
    function handleReply(tag, reply) {
        if (tag.indexOf("merge-targets:") === 0) {
            mergeTargets.picker.receive(tag, reply)
            return
        }
        if (tag.indexOf("base-change-branches:") === 0) {
            baseChangePicker.receive(tag, reply)
            return
        }
        if (tag.indexOf("base-suggestions:") === 0) {
            launchDialog.baseSuggestions.receive(tag, reply)
            return
        }
        if (tag.indexOf("branches:") === 0) {
            launchDialog.basePicker.receive(tag, reply)
            return
        }
        if (tag.indexOf("suggest:") === 0) {   // podpowiedź testów projektu (odczyt): tylko okno projektu, bez toastów
            manage.handleReply(tag, reply)
            return
        }
        const error = reply && reply.ok ? "" : Logic.errorText(reply)
        const notice = Logic.commitNote(reply)
        // Rzadkie ustawienia z pełnej karty (PT-134): widoczny skutek albo powód odmowy usługi. Błąd przeniesienia
        // pokazuje niżej gałąź „move:”, więc tu go nie dublujemy.
        const settingNote = takeSettingNote(tag)
        if (settingNote !== null && settingNote !== "") {
            if (!error) showToast(settingNote, "mint")
            else if (tag.indexOf("move:") !== 0) showToast("Nie zapisano: " + error, "rose")
        }
        if (tag.indexOf("ticket-settings:") === 0) {
            // Błąd usługi (np. ticket-started, bad-assignment) zostaje w oknie; sukces zamyka je z potwierdzeniem.
            if (tag !== ticketSettingsTag || !ticketSettings.visible) return
            if (error) {
                ticketSettings.busy = false
                ticketSettings.error = error
            } else {
                const id = ticketSettings.ticketId
                ticketSettings.close()
                showToast("Ustawienia ticketu " + id + " zapisane.", "mint")
            }
            return
        }
        if (tag.indexOf("ticket-reassign:") === 0) {
            // Odpowiedź wraca do okna przy polu, które ją wysłało; okno się nie zamyka.
            const field = tag.split(":")[1]
            if (ticketReassignTags[field] !== tag) return
            // „retry” (PT-341): odpowiedź ResolveCardStep nie niesie nazwy wykonawcy — okno użyje wysłanej.
            ticketSettings.fieldReplied(field, error, reply && (field === "author" ? reply.executor
                                                                  : field === "reviewer" ? reply.reviewer : "") || "")
            return
        }
        if (tag.indexOf("base-change:") === 0) {
            if (baseChangePreview.requestTag !== tag) return
            baseChangePreview.busy = false
            baseChangePreview.error = error
            if (!error) baseChangePreview.show(baseChangePreview.cardId, reply.preview || reply)
        } else if (tag.indexOf("base-keep:") === 0 || tag.indexOf("base-confirm:") === 0) {
            if (baseChangePreview.requestTag !== tag) return
            baseChangePreview.busy = false
            baseChangePreview.error = error
            if (!error && (tag.indexOf("base-keep:") === 0 || reply.changed)) baseChangePreview.close()
            else if (!error) baseChangePreview.error = reply.reason || "Korekta czeka; zachowano dane podglądu."
        } else if (tag.indexOf("launch-") === 0) {
            handleLaunchReply(tag, reply)
        } else if (tag.indexOf("setting:") === 0) {
            if (notice) showToast(notice, "butter")
        } else if (tag.indexOf("fault-preview:") === 0) {
            handleFaultPreviewReply(tag, reply)
        } else if (tag.indexOf("decision:") === 0) {
            handleDecisionReply(tag, error, reply)
        } else if (tag.indexOf("move:") === 0) {
            if (error) {
                optimistic = optimistic.filter(m => m.tag !== tag)
                const id = tag.split(":")[1]
                showToast("Nie przeniesiono " + id + ": " + error, "rose")
            } else if (notice) {
                showToast(notice, "butter")
            }

        } else if (tag.indexOf("create") === 0 || tag.indexOf("update:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) {
                if (reply.id) selectedId = reply.id
                panelOpen = false
                if (notice) showToast(notice, "butter")
                Qt.callLater(() => revealCard(selectedId))
            }
        } else if (tag.indexOf("yt-resolve:") === 0) {
            if (error) showToast("YouTrack: " + error, "rose")
            else if (!managing) cardPanel.notice = reply.choice === "tide" ? "Zapisuję stan karty w YouTracku…"
                                                                           : "Zostaje stan z YouTracka."
            if (managing) manage.handleReply(tag, reply)
        } else if (tag.indexOf("caretaker-talk:") === 0) {
            showToast(error ? "Nie otwarto rozmowy: " + error : "Rozmowa z opiekunem otwarta w Konsoli.",
                      error ? "rose" : "mint")
        } else if (tag.indexOf("enter-step:") === 0 || tag.indexOf("abandon-step:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) cardPanel.notice = tag.indexOf("enter-step:") === 0
                ? "Krok otwarty w Konsoli." : "Krok porzucony."
        } else if (tag.indexOf("review-resolve:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) cardPanel.notice = reply.choice === "retry" ? "Uruchamiam kolejne poprawki."
                                                                      : "Ticket odrzucony; gałąź i worktree zostały do wglądu."
        } else if (tag.indexOf("step-resolve:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            planManager.busy = false
            planManager.error = error
            if (!error) {
                cardPanel.notice = reply.tracker ? "Ponawiam operację YouTracka."
                    : reply.choice === "wait-limit" ? "Krok czeka na odnowienie limitu."
                    : reply.choice === "transfer" ? "Przekazuję krok innemu dostawcy."
                    : reply.choice === "deny" ? "Odmowa zapisana. Krok zatrzymany."
                    : "Decyzja zapisana. Wznawiam krok w tle."
                planManager.notice = reply.choice === "transfer" ? "Przenoszę przegląd do wybranego modelu." : "Decyzja zapisana."
            }
        } else if (tag.indexOf("flow-pause:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) cardPanel.notice = reply.paused ? "Karta wstrzymana. Trwające kroki kończą się." : "Karta wznowiona."
        } else if (tag.indexOf("plan-edit:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) cardPanel.notice = "Zmiana planu zapisana."
            planManager.busy = false
            planManager.error = error
            if (!error) planManager.notice = "Zmiana planu zapisana."
        } else if (tag.indexOf("plan-approve:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) cardPanel.notice = "Plan zatwierdzony. Uruchamiam pierwszy gotowy ticket."
            planManager.busy = false
            planManager.error = error
            if (!error) planManager.notice = "Plan zatwierdzony. Uruchamiam pierwszy gotowy ticket."
        } else if (tag.indexOf("env-approve:") === 0) {
            // Odmowę waliduje usługa; panel pokazuje jej komunikat i pozycje (nowe do zaznaczenia), nic nie zgaduje.
            cardPanel.busy = false
            cardPanel.environmentError = error
            cardPanel.environmentReply = error || reply.exposure || reply.revoked ? reply : null
            if (!error) cardPanel.notice = reply.preview ? "Podgląd zakresu gotowy. Zaznacz zgodę po przeczytaniu."
                : reply.message ? reply.message : reply.onlyPlan
                ? "Plan zatwierdzony. Środowisko czeka na zatwierdzenie."
                : (reply.build ? "Środowisko zatwierdzone. Budowa pudełka trwa w tle." : "Środowisko zatwierdzone.")
        } else if (tag.indexOf("branch-diff:") === 0) {
            cardPanel.error = error
            if (!error) cardPanel.branchDiff = reply.diff || "(brak różnic)"
        } else if (tag.indexOf("preview-merge:") === 0) {
            if (mergePreview.requestTag !== tag) return
            mergePreview.busy = false
            mergePreview.error = error
            mergePreview.result = error ? null : reply
            if (!error) mergePreview.targetBranch = reply.targetBranch || mergePreview.targetBranch
        } else if (tag.indexOf("merge-card:") === 0) {
            if (mergePreview.requestTag !== tag) return
            mergePreview.busy = false
            mergePreview.error = error
            if (error) {
                // Ponowna zgoda wymaga nowego odczytu; stary przycisk nie może ponowić mutacji.
                mergePreview.result = null
            } else {
                mergePreview.close()
                showToast("Karta scalona do " + reply.targetBranch + ".", "mint")
            }
        } else if (tag.indexOf("merge-main:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) cardPanel.notice = "Karta scalona do gałęzi głównej."
            cardFullView.mergeBusy = false
            showToast(error ? "Scalenie nie powiodło się: " + error : "Karta scalona do gałęzi głównej.",
                      error ? "rose" : "mint")
        } else if (tag.indexOf("quicktag:") === 0) {
            tagEditorPopup.editorError = error
            if (!error) tagEditorPopup.close()
            else tagEditorPopup.busy = false
            if (!error && notice) showToast(notice, "butter")
        } else {
            manage.handleReply(tag, reply)
            if (!error && notice) showToast(notice, "butter")
        }
    }

    // Nowa migawka: przeniesienie w locie już w niej jest (albo przepadło).
    onSnapshotChanged: {
        refreshColumnSpecs()
        optimistic = []
        if (mergeTargets.visible && mergeTargets.contextKey !== mergeContext(mergeTargets.cardId)) mergeTargets.close()
        if (mergePreview.visible && mergePreview.contextKey !== mergeContext(mergePreview.cardId)) mergePreview.close()
        if (launchOpen && !launchDialog.busy) {
            const c = (snapshot && snapshot.cards || []).find(card => card.id === launchCardId)
            const exact = Logic.planCandidates(c).find(plan => plan.card === launchCardId)
            if (exact && !launchDialog.planPath) {
                launchDialog.planPath = exact.path
                launchDialog.entry = "plan"
            }
        }
    }

    // --- wyszukiwanie kart w kolumnach (fokus, przewijanie) ---------------------------------

    function columnItems() {
        const out = []
        if (layoutMode === "together") {
            for (let i = 0; i < columnRepeater.count; i++) out.push(columnRepeater.itemAt(i))
        }
        return out.filter(x => x)
    }

    function findCard(id) {
        if (!id) return null
        for (const column of columnItems()) {
            const item = column.cardItem(id)
            if (item) return item
        }
        return null
    }

    function revealCard(id) {
        if (!id) return false
        for (const column of columnItems()) {
            const item = column.cardItem(id)
            if (item) {
                column.reveal(item)
                return true
            }
        }
        return false
    }

    // showItem(id) z okna: zaznacza kartę i przewija do niej; filtr, który ją chowa, znika.
    function selectCard(id) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id.toLowerCase() === String(id).toLowerCase())
        if (!card) return false
        if (!Logic.matches(card, filters)) {
            tagFilter = ""
            projectFilter = ""
            query = ""
            search.text = ""
        }
        managing = false
        selectedId = card.id
        if (Logic.displayColumn(card) === "done") {
            const column = columnRepeater.itemAt(columnSpecs.findIndex(c => c.id === "done"))
            if (column) column.expanded = true
        }
        Qt.callLater(() => {
            revealCard(card.id)
            const item = findCard(card.id)
            // Okno decyzji albo panel otwarte z powiadomienia trzymają fokus (PT-133).
            if (item && !decisionOpen && !panelOpen) item.forceActiveFocus()
        })
        return true
    }

    // Strzałki na karcie: sąsiednia karta w kolumnie albo w kolumnie obok (ta sama wysokość).
    function navigate(id, dx, dy) {
        const items = columnItems()
        const from = items.findIndex(c => c.cardItem(id) !== null)
        if (from < 0) return
        const index = items[from].indexOfCard(id)
        if (dy !== 0) {
            items[from].focusAt(index + dy)
            return
        }
        for (let i = from + dx; i >= 0 && i < items.length; i += dx) {
            if (items[i].focusAt(index)) return
        }
    }

    // Ctrl+strzałki: ←/→ sąsiednia kolumna ręczna, ↑/↓ miejsce wyżej albo niżej.
    function keyMove(id, dx, dy) {
        const card = cards.find(c => c.id === id)
        if (!card || !Logic.draggable(card)) return
        const columnId = Logic.displayColumn(card)
        if (dx !== 0) {
            const target = Logic.confirmable(card) ? (dx > 0 ? "done" : "todo") : Logic.neighbourColumn(columnId, dx)
            if (target) moveCard(id, target, "")
            Qt.callLater(() => { const item = findCard(id); if (item) item.forceActiveFocus() })
            return
        }
        if (Logic.confirmable(card)) return   // w kolumnie wyliczanej kolejność ustala koordynator
        const list = Logic.column(cards, columnId)
        const at = list.findIndex(c => c.id === id)
        const target = at + dy
        if (target < 0 || target >= list.length) return
        const before = dy < 0 ? list[target].id : (target + 1 < list.length ? list[target + 1].id : "")
        moveCard(id, columnId, before)
        Qt.callLater(() => { const item = findCard(id); if (item) item.forceActiveFocus() })
    }

    // --- przeciąganie ---------------------------------------------------------------------------

    function prepareDrag(item, x, y) {
        ghost.card = item.card
        ghost.width = item.width
        const pos = item.mapToItem(dragLayer, 0, 0)
        ghost.x = pos.x
        ghost.y = pos.y
        ghost.Drag.hotSpot.x = x
        ghost.Drag.hotSpot.y = y
        dragId = item.card.id
    }

    function startDrag() {
        dragging = true
    }

    function finishDrag() {
        if (dragging) ghost.Drag.drop()
        dragging = false
        dragId = ""
    }

    function cancelDrag() {
        dragging = false
        dragId = ""
    }

    color: Tide.Tokens.backgroundWindow
    focus: true

    // --- tablica --------------------------------------------------------------------------------
    ColumnLayout {
        id: main
        anchors.fill: parent
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        anchors.topMargin: 20
        anchors.bottomMargin: 20
        spacing: 14
        visible: !page.managing

        // Nagłówek.
        RowLayout {
            Layout.fillWidth: true
            spacing: 14
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: "Tablica"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 21
                    font.weight: Font.DemiBold
                    Accessible.role: Accessible.Heading
                }
                Text {
                    objectName: "boardSummary"
                    Layout.fillWidth: true
                    text: !page.available ? "usługa tide-daemon nie odpowiada"
                        : !page.exists ? "jeszcze pusta"
                        : Logic.summary(page.cards) + (page.filtered ? " · filtr" : "")
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    elide: Text.ElideRight
                }
            }
            // YouTrack: konflikty, niedostępność, nowe zgłoszenia (PT-7) — małe, bez koloru tła.
            Repeater {
                model: page.youtrackNotes
                Rectangle {
                    id: ytNote
                    required property var modelData
                    objectName: "youtrackNote"
                    readonly property bool clickable: modelData.kind !== "offline"
                    implicitHeight: 26
                    implicitWidth: ytNoteText.implicitWidth + 18
                    radius: 13
                    color: ytNoteArea.containsMouse && clickable ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06) : "transparent"
                    border.width: 1
                    border.color: Tide.Theme.alpha(modelData.kind === "conflict" ? Tide.Tokens.rose
                                                 : modelData.kind === "new" ? Tide.Tokens.peach : Tide.Tokens.textMuted, 0.4)
                    activeFocusOnTab: clickable
                    Accessible.role: clickable ? Accessible.Button : Accessible.StaticText
                    Accessible.name: modelData.text
                    Accessible.onPressAction: ytNoteArea.clicked(null)
                    Keys.onReturnPressed: ytNoteArea.clicked(null)
                    Text {
                        id: ytNoteText
                        anchors.centerIn: parent
                        text: ytNote.modelData.text
                        textFormat: Text.PlainText
                        color: ytNote.modelData.kind === "conflict" ? Tide.Tokens.rose
                             : ytNote.modelData.kind === "new" ? Tide.Tokens.peach : Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                    }
                    MouseArea {
                        id: ytNoteArea
                        anchors.fill: parent
                        hoverEnabled: true
                        enabled: ytNote.clickable
                        cursorShape: ytNote.clickable ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: {
                            if (ytNote.modelData.kind === "new") page.openImport()
                            else {
                                page.panelOpen = false
                                page.managing = true
                            }
                        }
                    }
                }
            }
            Field {
                objectName: "searchBox"
                implicitWidth: 220
                implicitHeight: 36
                focused: search.activeFocus
                visible: page.exists
                RowLayout {
                    anchors.fill: parent
                    anchors.leftMargin: 12
                    anchors.rightMargin: 10
                    spacing: 8
                    // Lupa: kółko i rączka.
                    Item {
                        implicitWidth: 15
                        implicitHeight: 15
                        Rectangle {
                            x: 1; y: 1
                            width: 10; height: 10
                            radius: 5
                            color: "transparent"
                            border.width: 1.8
                            border.color: Tide.Tokens.textMuted
                        }
                        Rectangle {
                            x: 9.5; y: 11
                            width: 5; height: 1.8
                            radius: 1
                            rotation: 45
                            color: Tide.Tokens.textMuted
                        }
                    }
                    TextInput {
                        id: search
                        objectName: "searchInput"
                        Layout.fillWidth: true
                        color: Tide.Tokens.textPrimary
                        selectionColor: Tide.Tokens.accent
                        selectedTextColor: Tide.Tokens.backgroundDesktop
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        clip: true
                        onTextEdited: page.query = text
                        Keys.onEscapePressed: event => {
                            if (text !== "") {
                                text = ""
                                page.query = ""
                                event.accepted = true
                            } else {
                                page.forceActiveFocus()
                            }
                        }
                        Keys.onDownPressed: {
                            const first = page.cards[0]
                            const item = first ? page.findCard(first.id) : null
                            if (item) item.forceActiveFocus()
                        }
                        Accessible.role: Accessible.EditableText
                        Accessible.name: "Szukaj kart"
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: search.text === ""
                            text: "Szukaj kart…"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font: search.font
                        }
                    }
                }
            }
            PillButton {
                objectName: "newCardButton"
                visible: page.available
                text: "＋ Karta"
                strong: true
                horizontalPadding: 16
                fill: Tide.Tokens.accent
                textColor: Tide.Tokens.backgroundDesktop
                toolTip: "Nowa karta w Backlogu (Ctrl+N)"
                onClicked: page.newCard("backlog")
            }
        }

        // Filtry: tagi, projekt, układ.
        RowLayout {
            objectName: "filterRow"
            Layout.fillWidth: true
            visible: page.exists
            spacing: 6

            Flow {
                Layout.fillWidth: true
                spacing: 6
                TagChip {
                    large: true
                    showDot: false
                    name: "Wszystkie"
                    selectable: true
                    selected: page.tagFilter === ""
                    onClicked: page.tagFilter = ""
                }
                Repeater {
                    model: page.snapshot ? (page.snapshot.tags || []).filter(t => t.cards > 0 || t.defined) : []
                    TagChip {
                        required property var modelData
                        objectName: "tagFilter"
                        large: true
                        name: modelData.name
                        dotColor: page.tagTone(modelData.name)
                        selectable: true
                        selected: page.tagFilter.toLowerCase() === modelData.name.toLowerCase()
                        onClicked: page.tagFilter = selected ? "" : modelData.name
                    }
                }
                Item {
                    objectName: "addTagChip"
                    width: addTagLabel.implicitWidth + 20
                    height: 28
                    activeFocusOnTab: true
                    Accessible.role: Accessible.Button
                    Accessible.name: "Nowy tag"
                    Accessible.onPressAction: tagEditorPopup.openNew()
                    Keys.onReturnPressed: tagEditorPopup.openNew()
                    Keys.onSpacePressed: tagEditorPopup.openNew()
                    DashedFrame {
                        anchors.fill: parent
                        radius: 14
                        strokeColor: parent.activeFocus ? Tide.Tokens.accent
                                                        : Tide.Theme.alpha(Tide.Tokens.textPrimary, addTagArea.containsMouse ? 0.35 : 0.2)
                    }
                    Text {
                        id: addTagLabel
                        anchors.centerIn: parent
                        text: "＋ Tag"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                    }
                    MouseArea {
                        id: addTagArea
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: tagEditorPopup.openNew()
                    }
                    QQC2.Popup {
                        id: tagEditorPopup
                        property string editorError: ""
                        property bool busy: false
                        function openNew() {
                            editorError = ""
                            busy = false
                            open()
                            quickTag.reset()
                            quickTag.focusName()
                        }
                        y: parent.height + 6
                        width: 300
                        padding: 14
                        focus: true
                        closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutside
                        background: Rectangle {
                            radius: Tide.Tokens.radiusTile
                            color: Tide.Tokens.backgroundCard
                            border.width: 1
                            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
                        }
                        contentItem: ColumnLayout {
                            spacing: 10
                            Text {
                                text: "Nowy tag"
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                            }
                            TagEditor {
                                id: quickTag
                                Layout.fillWidth: true
                                pastels: Object.keys((page.snapshot && page.snapshot.colors) || {})
                                taken: ((page.snapshot && page.snapshot.tags) || []).map(t => t.color)
                                error: tagEditorPopup.editorError
                                busy: tagEditorPopup.busy
                                onSaveRequested: request => {
                                    tagEditorPopup.busy = true
                                    page.tagRequested(page.nextTag("quicktag"), JSON.stringify(request))
                                }
                                onCancelRequested: tagEditorPopup.close()
                            }
                        }
                    }
                }
            }

            // Filtr projektu.
            PillButton {
                id: projectButton
                objectName: "projectFilter"
                implicitHeight: 28
                pillRadius: 14
                pixelSize: 12
                horizontalPadding: 12
                text: "Projekt: " + (page.projectFilter === "" ? "wszystkie"
                                     : page.projectFilter === Logic.NO_PROJECT ? "bez projektu"
                                     : Logic.projectName(page.snapshot, page.projectFilter)) + " ▾"
                textColor: page.projectFilter ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                onClicked: projectMenu.opened ? projectMenu.close() : projectMenu.open()

                QQC2.Popup {
                    id: projectMenu
                    y: parent.height + 6
                    x: parent.width - width
                    width: 260
                    padding: 6
                    closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutsideParent
                    background: Rectangle {
                        radius: Tide.Tokens.radiusTile
                        color: Tide.Tokens.backgroundCard
                        border.width: 1
                        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
                    }
                    contentItem: ListView {
                        implicitHeight: Math.min(10, count) * 32
                        clip: true
                        boundsBehavior: Flickable.StopAtBounds
                        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                        model: {
                            const list = [{ key: "", name: "Wszystkie projekty", count: Logic.counts(page.allCards).total }]
                            for (const p of (page.snapshot && page.snapshot.projects) || [])
                                list.push({ key: p.slug, name: p.name,
                                            count: Logic.counts(page.allCards.filter(c => c.project === p.slug)).total })
                            const loose = page.allCards.filter(c => !c.project)
                            if (loose.length) list.push({ key: Logic.NO_PROJECT, name: "Bez projektu", count: Logic.counts(loose).total })
                            return list
                        }
                        delegate: Rectangle {
                            id: projectRow
                            required property var modelData
                            readonly property bool on: modelData.key === page.projectFilter
                            width: ListView.view.width
                            height: 32
                            radius: Tide.Tokens.radiusSmall - 2
                            color: on ? Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                                 : rowArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05) : "transparent"
                            Accessible.role: Accessible.MenuItem
                            Accessible.name: modelData.name
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 10
                                anchors.rightMargin: 10
                                Text {
                                    Layout.fillWidth: true
                                    text: projectRow.modelData.name
                                    textFormat: Text.PlainText
                                    color: projectRow.on ? Tide.Tokens.accent : Tide.Tokens.textPrimary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 13
                                    elide: Text.ElideRight
                                }
                                Text {
                                    text: projectRow.modelData.count
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.monoFamily
                                    font.pixelSize: 11
                                }
                            }
                            MouseArea {
                                id: rowArea
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                onClicked: {
                                    page.projectFilter = projectRow.modelData.key
                                    projectMenu.close()
                                }
                            }
                        }
                    }
                }
            }
            Segmented {
                objectName: "layoutSwitch"
                options: [{ value: "together", label: "Razem" }, { value: "lanes", label: "Wiersz na projekt" }]
                current: page.layoutMode
                onPicked: value => page.layoutMode = value
            }

            // Menu „Widok” (PT-133): ustawienie otwierania karty z „Czeka na mnie” — to samo pole jest w stopce okna decyzji.
            PillButton {
                id: viewButton
                objectName: "viewMenuButton"
                implicitHeight: 28
                pillRadius: 14
                pixelSize: 12
                horizontalPadding: 12
                text: "Widok ▾"
                textColor: Tide.Tokens.textSecondary
                Accessible.name: "Widok"
                onClicked: viewMenu.opened ? viewMenu.close() : viewMenu.open()

                QQC2.Popup {
                    id: viewMenu
                    objectName: "viewMenu"
                    y: parent.height + 6
                    x: parent.width - width
                    padding: 12
                    closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutsideParent
                    background: Rectangle {
                        radius: Tide.Tokens.radiusTile
                        color: Tide.Tokens.backgroundCard
                        border.width: 1
                        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
                    }
                    contentItem: CardUi.CardOpenSwitch {
                        objectName: "viewCardOpen"
                        current: page.cardOpen
                        onPicked: value => page.setCardOpen(value)
                    }
                }
            }
        }

        // Złe pliki i trwająca operacja gita (dyskretnie, nad kolumnami).
        Rectangle {
            id: errorBar
            objectName: "boardErrors"
            Layout.fillWidth: true
            visible: page.errorLines.length > 0
            implicitHeight: errorColumn.implicitHeight + 16
            radius: 10
            color: Tide.Theme.alpha(Tide.Tokens.butter, 0.06)
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.butter, 0.26)
            Accessible.role: Accessible.AlertMessage
            Accessible.name: page.errorLines.join(" ")
            Column {
                id: errorColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 12
                anchors.rightMargin: 12
                spacing: 4
                Repeater {
                    model: page.errorLines
                    Text {
                        required property var modelData
                        objectName: "boardErrorLine"
                        width: errorColumn.width
                        text: modelData
                        textFormat: Text.PlainText
                        color: Tide.Tokens.butter
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                        maximumLineCount: 2
                        elide: Text.ElideRight
                    }
                }
            }
        }

        // Wynik ostatniej akcji: błąd przeniesienia albo commit, który czeka.
        Rectangle {
            objectName: "boardToast"
            Layout.fillWidth: true
            visible: page.toast !== ""
            implicitHeight: toastRow.implicitHeight + 14
            radius: 10
            color: Tide.Theme.alpha(Tide.Tokens[page.toastTone], 0.10)
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens[page.toastTone], 0.40)
            Accessible.role: Accessible.AlertMessage
            Accessible.name: page.toast
            RowLayout {
                id: toastRow
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 12
                anchors.rightMargin: 6
                Text {
                    objectName: "boardToastText"
                    Layout.fillWidth: true
                    text: page.toast
                    textFormat: Text.PlainText
                    color: Tide.Tokens[page.toastTone]
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
                PillButton {
                    implicitWidth: 26
                    implicitHeight: 26
                    flat: true
                    text: "✕"
                    pixelSize: 11
                    toolTip: "Zamknij"
                    onClicked: page.toast = ""
                }
            }
            Timer {
                id: toastTimer
                interval: 9000
                onTriggered: page.toast = ""
            }
        }

        // Kolumny (Razem) albo wiersze projektów.
        Item {
            id: boardArea
            Layout.fillWidth: true
            Layout.fillHeight: true

            RowLayout {
                id: columnsRow
                objectName: "boardColumns"
                anchors.fill: parent
                spacing: 10
                visible: page.exists && page.layoutMode === "together"
                Repeater {
                    id: columnRepeater
                    model: page.columnSpecs
                    BoardColumn {
                        required property var modelData
                        objectName: "column-" + modelData.id
                        // Wszystkie kolumny dzielą szerokość po równo — Gotowe też (zwarte karty i „+N starszych”).
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        Layout.preferredWidth: 100
                        Layout.minimumWidth: 150
                        spec: modelData
                        cards: Logic.column(page.cards, modelData.id)
                        selectedId: page.selectedId
                        canLaunch: page.canLaunch
                        canDiff: page.canDiff
                        tagTone: page.tagTone
                        dragHost: page
                        onCardClicked: id => page.activateCard(id)
                        onAddRequested: column => page.newCard(column)
                        onDropped: (id, before) => page.moveCard(id, modelData.id, before)
                        onMoveRequested: (id, column) => page.moveCard(id, column, page.topOf(column))
                        onSessionRequested: session => page.sessionRequested(session)
                        onAgentRequested: session => page.agentRequested(session)
                        onLaunchRequested: id => page.openLaunch(id, false)
                        onKeyNavigation: (id, dx, dy) => page.navigate(id, dx, dy)
                        onKeyMove: (id, dx, dy) => page.keyMove(id, dx, dy)
                    }
                }
            }

            // Wiersz na projekt: nagłówki kolumn raz u góry, pod nimi pas każdego projektu.
            ColumnLayout {
                anchors.fill: parent
                spacing: 8
                visible: page.exists && page.layoutMode === "lanes"
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Repeater {
                        model: page.columnSpecs
                        RowLayout {
                            required property var modelData
                            Layout.fillWidth: true
                            Layout.preferredWidth: 100
                            Layout.minimumWidth: 150
                            Layout.leftMargin: 12
                            spacing: 7
                            Dot { tint: modelData.tint; hollow: modelData.hollow }
                            Text {
                                Layout.fillWidth: true
                                text: modelData.label
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                                elide: Text.ElideRight
                            }
                            Text {
                                Layout.rightMargin: 12
                                text: Logic.column(page.cards, modelData.id).length
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.monoFamily
                                font.pixelSize: 12
                            }
                        }
                    }
                }
                Flickable {
                    id: lanesFlick
                    objectName: "boardLanes"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    contentWidth: width
                    contentHeight: lanesColumn.implicitHeight
                    boundsBehavior: Flickable.StopAtBounds
                    QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                    ColumnLayout {
                        id: lanesColumn
                        width: lanesFlick.width
                        spacing: 14
                        Repeater {
                            model: laneModel
                            ColumnLayout {
                                id: lane
                                objectName: "boardLane-" + laneKey
                                required property string laneKey
                                required property string laneName
                                // Karty wiersza: ta sama tablica, dopóki treść wiersza się nie zmieni (PT-326).
                                readonly property var laneCards: (page.lanesByKey[laneKey] || { cards: [] }).cards
                                Layout.fillWidth: true
                                spacing: 6
                                RowLayout {
                                    Layout.leftMargin: 4
                                    spacing: 8
                                    Text {
                                        text: lane.laneName
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textSecondary
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                        Accessible.role: Accessible.Heading
                                    }
                                    Text {
                                        text: Logic.cards(Logic.counts(lane.laneCards).total)
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textMuted
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 12
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 10
                                    Repeater {
                                        model: page.columnSpecs
                                        BoardColumn {
                                            required property var modelData
                                            Layout.fillWidth: true
                                            Layout.preferredWidth: 100
                                            Layout.minimumWidth: 150
                                            Layout.fillHeight: true
                                            Layout.minimumHeight: 52
                                            fill: false
                                            showHeader: false
                                            showAdd: false
                                            spec: modelData
                                            cards: Logic.column(lane.laneCards, modelData.id)
                                            selectedId: page.selectedId
                                            canLaunch: page.canLaunch
                                            canDiff: page.canDiff
                                            tagTone: page.tagTone
                                            dragHost: page
                                            onCardClicked: id => page.activateCard(id)
                                            onDropped: (id, before) => page.moveCard(id, modelData.id, before)
                                            onMoveRequested: (id, column) => page.moveCard(id, column, page.topOf(column))
                                            onSessionRequested: session => page.sessionRequested(session)
                                            onAgentRequested: session => page.agentRequested(session)
                                            onLaunchRequested: id => page.openLaunch(id, false)
                                        }
                                    }
                                }
                            }
                        }
                        Text {
                            visible: page.lanes.length === 0
                            text: "Żadna karta nie pasuje do filtra."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                        }
                    }
                }
            }

            // Pusty stan: tablicy jeszcze nie ma albo usługa nie działa.
            Rectangle {
                objectName: "emptyState"
                anchors.centerIn: parent
                anchors.verticalCenterOffset: -40
                visible: !page.exists
                width: Math.min(parent.width - 40, 480)
                implicitHeight: emptyColumn.implicitHeight + 56
                radius: Tide.Tokens.radiusCard
                color: Tide.Tokens.backgroundView
                border.width: 1
                border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
                ColumnLayout {
                    id: emptyColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 28
                    spacing: 10
                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        implicitWidth: 52
                        implicitHeight: 52
                        radius: 14
                        color: Tide.Theme.alpha(Tide.Tokens.accent, 0.10)
                        Row {
                            anchors.centerIn: parent
                            spacing: 3
                            Repeater {
                                model: [16, 11, 7]
                                Rectangle {
                                    required property int modelData
                                    width: 6
                                    height: modelData
                                    radius: 2
                                    color: "transparent"
                                    border.width: 2
                                    border.color: Tide.Tokens.accent
                                }
                            }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: page.available ? "Tu będzie twój backlog" : "Usługa tide-daemon nie odpowiada"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 16
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                    }
                    Text {
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: page.available
                              ? "Karty ze wszystkich projektów w jednym miejscu: pomysły, poprawki, funkcje. Zapiszę je w lokalnym repozytorium git "
                                + Logic.shortPath(page.snapshot ? page.snapshot.boardDir : "", page.homeDir)
                                + " — założę je przy pierwszej karcie."
                              : "Tablica wróci sama, gdy usługa wstanie (systemctl --user start tide-daemon)."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        lineHeight: 1.15
                        wrapMode: Text.Wrap
                    }
                    PillButton {
                        objectName: "firstCardButton"
                        Layout.alignment: Qt.AlignHCenter
                        Layout.topMargin: 6
                        visible: page.available
                        text: "＋ Pierwsza karta"
                        strong: true
                        fill: Tide.Tokens.accent
                        textColor: Tide.Tokens.backgroundDesktop
                        onClicked: page.newCard("backlog")
                    }
                }
            }
        }
    }

    // --- Projekty i tagi ------------------------------------------------------------------------
    ManagePage {
        id: manage
        anchors.fill: parent
        visible: page.managing
        snapshot: page.snapshot
        homeDir: page.homeDir
        tagTone: page.tagTone
        modelsStatus: page.modelsStatus
        flowSettings: page.flowSettings
        onProjectRequested: (tag, request) => page.projectRequested(tag, JSON.stringify(request))
        onSuggestRequested: (tag, request) => page.suggestTestsRequested(tag, JSON.stringify(request))
        onTagRequested: (tag, request) => page.tagRequested(tag, JSON.stringify(request))
        onIssuesRequested: (tag, refresh) => page.youtrackIssuesRequested(tag, refresh)
        onImportRequested: (tag, keys) => page.importRequested(tag, JSON.stringify({ issues: keys }))
        onResolveRequested: (tag, ident, choice) => page.resolveRequested(tag, ident, choice)
        onFlowSettingsSaveRequested: (tag, request) => page.flowSettingsSaveRequested(tag, JSON.stringify(request))
        onBackRequested: {
            page.managing = false
            if (page.launchOpen) Qt.callLater(() => { page.launchDialog.reloadProject(); page.launchDialog.forceActiveFocus() })
        }
    }

    // --- panel karty ----------------------------------------------------------------------------
    MouseArea {
        anchors.fill: parent
        visible: page.panelOpen
        z: page.descriptionDrawer ? 36 : 0
        onClicked: page.closePanel()
        onWheel: wheel => wheel.accepted = false
    }
    CardPanel {
        onNavigateRequested: (viewId, item) => page.navigateRequested(viewId, item)
        id: cardPanel
        objectName: "cardPanel"
        z: page.descriptionDrawer ? 37 : 0
        descriptionOnly: page.descriptionDrawer
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        anchors.margins: 12
        visible: page.panelOpen
        snapshot: page.snapshot
        card: page.panelOpen ? page.panelCard : null
        column: page.panelColumn
        homeDir: page.homeDir
        nowTs: page.nowTs
        tagTone: page.tagTone
        modelsStatus: page.modelsStatus
        maxWidth: page.width - 24
        renderer: page.markdownRenderer
        onDocsRequested: cardId => page.docsRequested(cardId)
        onOpenFileRequested: path => page.fileRequested(path, "open")
        onShowInFolderRequested: path => page.fileRequested(path, "folder")
        onLinkRequested: url => page.linkRequested(url)
        onSaveRequested: request => page.saveCard(request)
        onYoutrackResolveRequested: (cardId, choice) => page.resolveRequested(page.nextTag("yt-resolve:" + cardId), cardId, choice)
        onEnterStepRequested: (cardId, stepId) => {
            cardPanel.busy = true
            page.enterStepRequested(page.nextTag("enter-step:" + cardId), cardId, stepId)
        }
        onAbandonStepRequested: (cardId, stepId) => {
            cardPanel.busy = true
            page.abandonStepRequested(page.nextTag("abandon-step:" + cardId), cardId, stepId)
        }
        onReviewResolveRequested: (cardId, choice) =>
            page.reviewResolveRequested(page.nextTag("review-resolve:" + cardId), cardId, choice)
        onStepResolveRequested: (cardId, request) =>
            page.stepResolveRequested(page.nextTag("step-resolve:" + cardId), cardId, JSON.stringify(request))
        onPauseRequested: (cardId, paused) => {
            cardPanel.busy = true
            page.pauseRequested(page.nextTag("flow-pause:" + cardId), cardId, paused)
        }
        onPlanEditRequested: (cardId, request) =>
            page.planEditRequested(page.nextTag("plan-edit:" + cardId), cardId, JSON.stringify(request))
        onPlanApproveRequested: cardId =>
            page.planApproveRequested(page.nextTag("plan-approve:" + cardId), cardId)
        onEnvironmentApproveRequested: (cardId, yaml, onlyPlan) => {
            cardPanel.environmentError = ""
            page.environmentApproveRequested(page.nextTag("env-approve:" + cardId), cardId, yaml, onlyPlan)
        }
        onPlanManagerRequested: page.openPlanManager()
        onBranchDiffRequested: cardId =>
            page.branchDiffRequested(page.nextTag("branch-diff:" + cardId), cardId)
        onMergeMainRequested: cardId =>
            page.previewMerge(cardId, cardPanel.card.derived.flow.mainBranch)
        onPreviewMergeRequested: (cardId, target) => page.previewMerge(cardId, target)
        onChooseMergeRequested: cardId => page.chooseMerge(cardId)
        onMoveRequested: column => {
            page.moveCard(page.panelCardId, column, page.topOf(column))
            if (column === "archived") page.panelOpen = false
        }
        onCloseRequested: page.closePanel()
    }

    Rectangle {
        anchors.fill: parent
        visible: page.planManagerOpen
        z: 30
        color: Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.72)
        MouseArea { anchors.fill: parent; onClicked: mouse => mouse.accepted = true }
    }
    PlanManager {
        id: planManager
        objectName: "planManager"
        anchors.fill: parent
        anchors.margins: 18
        visible: page.planManagerOpen
        z: 31
        card: page.panelCard
        modelsStatus: page.modelsStatus
        onCloseRequested: {
            page.planManagerOpen = false
            page.forceActiveFocus()
        }
        onCardRequested: {
            page.planManagerOpen = false
            page.panelOpen = true
            cardPanel.load()
        }
        onEditRequested: (cardId, request) => {
            planManager.busy = true
            page.planEditRequested(page.nextTag("plan-edit:" + cardId), cardId, JSON.stringify(request))
        }
        onTransferRequested: (cardId, request) => {
            planManager.busy = true
            page.stepResolveRequested(page.nextTag("step-resolve:" + cardId), cardId, JSON.stringify(request))
        }
        onApproveRequested: cardId => {
            planManager.busy = true
            page.planApproveRequested(page.nextTag("plan-approve:" + cardId), cardId)
        }
    }

    // --- „Co ma zrobić agent?” -------------------------------------------------------------------
    LaunchDialog {
        id: launchDialog
        objectName: "launchDialog"
        anchors.fill: parent
        visible: page.launchOpen && !page.managing
        snapshot: page.snapshot
        cardId: page.launchCardId
        fromMove: page.launchFromMove
        tagTone: page.tagTone
        images: page.launchImages
        canCapture: page.canCapture
        modelsStatus: page.modelsStatus
        limitsStatus: page.limitsStatus
        limitThresholdPercent: page.flowSettings && page.flowSettings.limitThresholdPercent !== undefined
                               ? page.flowSettings.limitThresholdPercent : 10
        taskDefaults: page.taskDefaults
        pasteImage: page.pasteImage
        onCaptureRequested: page.captureRequested()
        onRemoveImageRequested: path => page.removeImageRequested(path)
        onFilesDropped: urls => page.filesDropped(urls)
        onModelsRequested: page.modelsRequested()
        onBranchesRequested: text => {
            const tag = page.nextTag("branches:" + page.launchCardId)
            launchDialog.basePicker.pendingTag = tag
            page.branchesRequested(tag, page.launchCardId, JSON.stringify({purpose: "base", text: text, limit: 50}))
        }
        onSuggestionsRequested: request => {
            const tag = page.nextTag("base-suggestions:" + page.launchCardId)
            launchDialog.baseSuggestions.pendingTag = tag
            page.suggestionsRequested(tag, page.launchCardId, JSON.stringify(request))
        }
        onLaunchRequested: (choice, description, changed) => page.startLaunch(choice, description, changed)
        onCancelRequested: page.cancelLaunch()
        onMoveOnlyRequested: page.closeLaunch()
        onProjectChosen: (kind, key) => page.updateRequested(page.nextTag("launch-project:" + page.launchCardId),
                                                             page.launchCardId,
                                                             JSON.stringify(kind === "repo" ? { repo: key } : { project: key }))
        onManageRequested: slug => {
            page.managing = true
            page.managePage.openProject = slug
        }
    }

    // --- okno decyzji (PT-132) -------------------------------------------------------------------
    CardUi.DecisionDialog {
        id: decisionDialog
        objectName: "decisionDialog"
        anchors.fill: parent
        z: 40
        visible: page.decisionOpen && !page.managing
        cardId: page.decisionCardId
        decisions: Logic.decisions(page.decisionCard)
        cardOpen: page.cardOpen
        onCardOpenRequested: value => page.setCardOpen(value)
        onAnswered: (decisionId, method, params) => page.sendDecision(page.decisionCardId, decisionId, method, params)
        onPreviewRequested: (decisionId, method, params) => page.previewFault(page.decisionCardId, decisionId, method, params)
        onLaterRequested: {
            page.closeDecisions()
            page.showToast("Karta zostaje w „Czeka na mnie” — wrócisz do niej z kafelka.", "butter")
        }
        onFinished: page.closeDecisions()
        // „Otwórz całą kartę” — pełna karta (PT-128). Możliwość, która wymaga wyboru wykonawcy (napis `notice`),
        // otwiera panel karty, bo wybór wykonawcy jest na razie tylko tam.
        onFullCardRequested: notice => {
            const id = page.decisionCardId
            page.decisionOpen = false
            if (!notice && page.openFull(id)) return
            page.fullOpen = false
            page.openCard(id)
            if (notice) cardPanel.notice = notice
        }
    }

    // --- pełna karta (PT-128) --------------------------------------------------------------------
    CardUi.CardFullView {
        onNavigateRequested: (viewId, item) => page.navigateRequested(viewId, item)
        id: cardFullView
        onCaretakerTalkRequested: page.caretakerTalkRequested(page.nextTag("caretaker-talk:" + page.fullCardId),
                                                             page.fullCardId, "{}")
        objectName: "cardFullView"
        mergePreviewAvailable: true
        // Ustawienia ticketu (PT-334): tylko Tablica zna katalog wykonawców; w Sesjach przycisk jest ukryty.
        ticketSettingsEnabled: true
        onTicketSettingsRequested: id => page.openTicketSettings(id)
        baseChangeAvailable: true
        onBaseChangeRequested: page.openBaseChange(page.fullCardId)
        anchors.fill: parent
        z: 35
        visible: page.fullOpen && !page.managing
        card: page.fullOpen ? page.fullCard : null
        moveTargets: page.fullCard ? Logic.settingTargets(page.fullCard) : []
        canArchive: page.fullCard ? Logic.canArchive(page.fullCard) : false
        columnLabels: page.columnLabelMap
        workPulse: page.workPulse
        onBackRequested: page.closeFull()
        onAnswered: (decisionId, method, params) => page.sendDecision(page.fullCardId, decisionId, method, params)
        onPreviewRequested: (decisionId, method, params) => page.previewFault(page.fullCardId, decisionId, method, params)
        onDecisionsRequested: page.openDecisions(page.fullCardId)
        onPanelRequested: notice => page.panelFromFull(notice)
        onDescriptionRequested: page.openDescription(page.fullCardId)
        onSettingRequested: (kind, params) => page.applySetting(page.fullCardId, kind, params)
        onMergeMainRequested: page.previewMerge(page.fullCardId, cardFullView.flow.mainBranch)
        onPreviewMergeRequested: target => page.previewMerge(page.fullCardId, target)
        onChooseMergeRequested: page.chooseMerge(page.fullCardId)
        onBranchDiffRequested: page.panelFromFull("Diff gałęzi: kliknij „Diff gałęzi” w panelu karty.")
        onEnterStepRequested: stepId => page.enterStepRequested(page.nextTag("enter-step:" + page.fullCardId),
                                                                page.fullCardId, stepId)
    }

    // Okno ustawień ticketu nieruszonego (PT-334); odpowiedź na „Zapisz” wraca przez tag „ticket-settings:”.
    TicketSettingsDialog {
        id: ticketSettings
        objectName: "ticketSettingsDialog"
        anchors.fill: parent
        z: 45
        modelsStatus: page.modelsStatus
        // Ticket trwający (PT-335): blokady pól liczą się na bieżąco z migawki pełnej karty.
        flow: cardFullView.flow
        // Karta „Od razu” nie ma `derived.tickets`: jedyny ticket jest kafelkiem (`nowTicket`).
        tickets: cardFullView.tickets.length > 0 ? cardFullView.tickets
                                                 : (cardFullView.nowTicket ? [cardFullView.nowTicket] : [])
        onSaveRequested: request => {
            ticketSettingsTag = page.nextTag("ticket-settings:" + page.fullCardId)
            page.planEditRequested(ticketSettingsTag, page.fullCardId, JSON.stringify(request))
        }
        onReassignRequested: (field, request) => {
            const tag = page.nextTag("ticket-reassign:" + field + ":" + page.fullCardId)
            page.ticketReassignTags = Object.assign({}, page.ticketReassignTags, { [field]: tag })
            page.stepResolveRequested(tag, page.fullCardId, JSON.stringify(request))
        }
        onClosed: if (page.fullOpen) cardFullView.forceActiveFocus()
    }

    MergePreview {
        id: mergePreview
        parent: page
        z: 60
        onConfirmRequested: request => {
            requestTag = page.nextTag("merge-card:" + cardId)
            page.mergeRequested(requestTag, cardId, JSON.stringify(request))
        }
        onRefreshRequested: page.previewMerge(cardId, targetBranch, targetRef)
    }
    MergeTargetPicker {
        id: mergeTargets
        parent: page
        z: 60
        onRequested: text => {
            const tag = page.nextTag("merge-targets:" + cardId)
            picker.pendingTag = tag
            page.branchesRequested(tag, cardId, JSON.stringify({purpose: "merge", text: text, limit: 50}))
        }
        onPreviewRequested: target => page.previewMerge(cardId, target.name, target.ref)
    }
    CardUi.BaseChangePreview {
        id: baseChangePreview
        parent: page
        z: 65
        selectedBase: baseChangePicker.selected
        selectionReady: baseChangePicker.ready
        selectionContent: BranchPicker {
            id: baseChangePicker
            Layout.fillWidth: true
            active: baseChangePreview.visible && baseChangePreview.selecting
            contextKey: baseChangePreview.cardId
            onRequested: text => {
                const tag = page.nextTag("base-change-branches:" + baseChangePreview.cardId)
                pendingTag = tag
                page.branchesRequested(tag, baseChangePreview.cardId, JSON.stringify({purpose: "base", text: text}))
            }
        }
        onRequested: request => {
            requestTag = page.nextTag("base-change:" + cardId)
            page.requestBaseChangeRequested(requestTag, cardId, JSON.stringify(request))
        }
        onRefreshed: request => {
            requestTag = page.nextTag("base-change:" + cardId)
            page.previewBaseChangeRequested(requestTag, cardId, JSON.stringify(request))
        }
        onKeepRequested: step => {
            busy = true
            requestTag = page.nextTag("base-keep:" + cardId)
            page.stepResolveRequested(requestTag, cardId,
                                      JSON.stringify({choice: "answer", step: step, baseDecision: "keep"}))
        }
        onConfirmed: request => {
            requestTag = page.nextTag("base-confirm:" + cardId)
            page.stepResolveRequested(requestTag, cardId, JSON.stringify(request))
        }
        onFileRequested: path => page.fileRequested(path, "open")
    }

    // --- duch przeciąganej karty ----------------------------------------------------------------
    Item {
        id: dragLayer
        anchors.fill: parent
        BoardCard {
            id: ghost
            ghost: true
            visible: page.dragging
            opacity: 0.94
            scale: 1.02
            z: 10
            tagTone: page.tagTone
            Drag.active: page.dragging
            Drag.keys: ["tide-card"]
        }
    }

    // Skróty: Ctrl+N nowa karta, Ctrl+F szukaj, Esc zamyka okno decyzji, panel, pełną kartę albo stronę.
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            if (page.decisionOpen && !page.managing) page.closeDecisions()
            else if (page.launchOpen && !page.managing) page.cancelLaunch()
            else if (page.panelOpen) page.closePanel()
            else if (page.fullOpen && !page.managing) page.closeFull()
            else if (page.managing) page.managing = false
            else if (page.query !== "") { search.text = ""; page.query = "" }
            else return
            event.accepted = true
        }
    }

    function focusSearch() {
        search.forceActiveFocus()
        search.selectAll()
    }
}

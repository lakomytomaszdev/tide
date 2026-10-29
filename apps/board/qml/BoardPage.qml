// Zawartość widoku „Tablica” (mock zatwierdzony 27.10.2026): nagłówek z podsumowaniem, szukaniem
// i „＋ Karta”, pastylki tagów (filtr), filtr projektu i układ (Razem / Wiersz na projekt), kolumny
// Backlog, Do zrobienia, W toku, Czeka na mnie, Do sprawdzenia i Gotowe (równe szerokości). Karty przeciąga się
// między kolumnami ręcznymi (Backlog, Do zrobienia, Gotowe) i w obrębie kolumny; kliknięcie otwiera
// panel karty, „Projekty i tagi” — osobną stronę. Sam nie rozmawia z usługą: dostaje migawkę
// i zgłasza zapisy sygnałami z tagiem, a odpowiedzi dostaje przez handleReply(tag, odpowiedź).
// YouTrack (PT-7): przy nagłówku dyskretne notki z migawki (konflikty, „YouTrack niedostępny”, „N nowych
// zgłoszeń w YouTracku” — ta otwiera import na stronie „Projekty i tagi”).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "board.js" as Logic

Rectangle {
    id: page

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
    property string panelCardId: ""          // "" — nowa karta
    property string panelColumn: "backlog"
    property string toast: ""
    property string toastTone: "rose"        // rose — błąd, butter — commit czeka
    // Przeciąganie.
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
    signal tagRequested(string tag, string request)
    signal sessionRequested(string session)  // PT-5: koordynator karty w widoku Sesje
    signal agentRequested(string session)    // okno agenta karty (Konsola, wątek Codexa)
    signal launchRequested(string cardId)    // otwarto „Co ma zrobić agent?” dla karty
    signal renderRequested(string tag, string cardId, string request)   // /Board RenderLaunch
    signal startRequested(string tag, string request)                   // /Tasks Start
    signal flowRequested(string tag, string cardId, string request)     // /Board StartCard (przepływ karty, „Od razu”)
    signal enterStepRequested(string tag, string cardId, string stepId) // /Board EnterStep
    signal abandonStepRequested(string tag, string cardId, string stepId) // /Board AbandonStep
    signal reviewResolveRequested(string tag, string cardId, string choice) // /Board ResolveCardReview
    signal stepResolveRequested(string tag, string cardId, string request) // /Board ResolveCardStep
    signal flowSettingsSaveRequested(string tag, string request) // /Board SetCardFlowSettings
    signal flowSettingsRefreshRequested() // /Board GetCardFlowSettings
    signal pauseRequested(string tag, string cardId, bool paused)
    signal planEditRequested(string tag, string cardId, string request)
    signal planApproveRequested(string tag, string cardId)       // /Board ApproveCardPlan
    signal branchDiffRequested(string tag, string cardId)
    signal mergeMainRequested(string tag, string cardId)
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
    readonly property var cards: Logic.visible(snapshot, filters, optimistic)
    readonly property var allCards: Logic.visible(snapshot, null, optimistic)
    readonly property var columnSpecs: Logic.columns(snapshot)
    readonly property bool exists: !!snapshot && snapshot.exists === true
    readonly property bool filtered: tagFilter !== "" || projectFilter !== "" || query.trim() !== ""
    readonly property var panelCard: panelCardId ? (snapshot && snapshot.cards || []).find(c => c.id === panelCardId) || null : null
    readonly property var errorLines: Logic.errorLines(snapshot, 3)
    readonly property var youtrackNotes: Logic.youtrackNotes(snapshot)
    readonly property var lanes: {
        const groups = []
        const index = {}
        for (const card of cards) {
            const key = card.project || Logic.NO_PROJECT
            if (index[key] === undefined) {
                index[key] = groups.length
                groups.push({ key: key, name: card.projectName || (card.project ? card.project : "Bez projektu"), cards: [] })
            }
            groups[index[key]].cards.push(card)
        }
        return groups.sort((a, b) => a.key === Logic.NO_PROJECT ? 1 : b.key === Logic.NO_PROJECT ? -1
                                                                       : a.name.localeCompare(b.name))
    }
    readonly property alias cardPanel: cardPanel
    readonly property alias launchDialog: launchDialog
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

    function newCard(column) {
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

    // Przeniesienie karty (przeciągnięcie, klawiatura, przycisk): pozycja liczona wśród kart
    // zapisanych w kolumnie docelowej, karta od razu stoi w nowym miejscu (optimistic).
    function moveCard(id, column, before) {
        const card = (snapshot && snapshot.cards || []).find(c => c.id === id)
        if (!card) return
        if (column !== "archived" && !Logic.isManual(column)) {
            showToast("Do kolumny „" + Logic.columnLabel(snapshot, column) + "” karta trafia sama, gdy pracuje nad nią agent.", "rose")
            return
        }
        const confirming = Logic.confirmable(card)
        if (confirming && Logic.dropTargets(card).indexOf(column) < 0) {
            showToast("Kartę z „" + Logic.columnLabel(snapshot, Logic.displayColumn(card)) + "” przenosisz do Gotowych "
                      + "(sprawdzone) albo z powrotem do Do zrobienia (do poprawy).", "rose")
            return
        }
        const index = column === "archived" ? 0 : Logic.moveIndex(snapshot, id, column, before)
        // Bez zmiany tylko wtedy, gdy karta już stoi w tej kolumnie także na ekranie (kolumna wyliczana
        // z koordynatora przy zapisanym todo to nie „ta sama kolumna”).
        if (column === Logic.displayColumn(card) && column === card.column && Logic.storedIndex(snapshot, id) === (index < 0
                ? snapshot.cards.filter(c => c.column === column).length - 1 : index)) return
        const fromBacklog = column === "todo" && card.column === "backlog"
        const origin = fromBacklog ? Logic.storedIndex(snapshot, id) : -1
        const backlogList = fromBacklog ? Logic.column(allCards, "backlog") : []
        const at = backlogList.findIndex(c => c.id === id)
        const originBefore = at >= 0 && at + 1 < backlogList.length ? backlogList[at + 1].id : ""
        const tag = nextTag("move:" + id)
        optimistic = optimistic.concat([{ id: id, column: column, before: before || "", tag: tag }])
        moveRequested(tag, id, column, index)
        if (fromBacklog) launchHook(id, origin, originBefore)
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
            launchDialog.busyText = "Zakładam worktree i otwieram kartę Konsoli z agentem…"
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
        const error = reply && reply.ok ? "" : Logic.errorText(reply)
        const notice = Logic.commitNote(reply)
        if (tag.indexOf("launch-") === 0) {
            handleLaunchReply(tag, reply)
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
            if (!error) {
                cardPanel.notice = reply.tracker ? "Ponawiam operację YouTracka."
                    : reply.choice === "wait-limit" ? "Krok czeka na odnowienie limitu."
                    : reply.choice === "transfer" ? "Przekazuję krok innemu dostawcy."
                    : reply.choice === "deny" ? "Odmowa zapisana. Krok zatrzymany."
                    : "Decyzja zapisana. Wznawiam krok w tle."
            }
        } else if (tag.indexOf("flow-pause:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) cardPanel.notice = reply.paused ? "Karta wstrzymana. Trwające kroki kończą się." : "Karta wznowiona."
        } else if (tag.indexOf("plan-edit:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) cardPanel.notice = "Zmiana planu zapisana."
        } else if (tag.indexOf("plan-approve:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) cardPanel.notice = "Plan zatwierdzony. Uruchamiam pierwszy gotowy ticket."
        } else if (tag.indexOf("branch-diff:") === 0) {
            cardPanel.error = error
            if (!error) cardPanel.branchDiff = reply.diff || "(brak różnic)"
        } else if (tag.indexOf("merge-main:") === 0) {
            cardPanel.busy = false
            cardPanel.error = error
            if (!error) cardPanel.notice = "Karta scalona do gałęzi głównej."
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
        optimistic = []
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
            if (item) item.forceActiveFocus()
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
                        onCardClicked: id => page.openCard(id)
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
                            model: page.layoutMode === "lanes" ? page.lanes : []
                            ColumnLayout {
                                id: lane
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 6
                                RowLayout {
                                    Layout.leftMargin: 4
                                    spacing: 8
                                    Text {
                                        text: lane.modelData.name
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textSecondary
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 13
                                        font.weight: Font.DemiBold
                                        Accessible.role: Accessible.Heading
                                    }
                                    Text {
                                        text: Logic.cards(Logic.counts(lane.modelData.cards).total)
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
                                            cards: Logic.column(lane.modelData.cards, modelData.id)
                                            selectedId: page.selectedId
                                            canLaunch: page.canLaunch
                                            canDiff: page.canDiff
                                            tagTone: page.tagTone
                                            dragHost: page
                                            onCardClicked: id => page.openCard(id)
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
        onClicked: page.closePanel()
        onWheel: wheel => wheel.accepted = false
    }
    CardPanel {
        id: cardPanel
        objectName: "cardPanel"
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
        onBranchDiffRequested: cardId =>
            page.branchDiffRequested(page.nextTag("branch-diff:" + cardId), cardId)
        onMergeMainRequested: cardId =>
            page.mergeMainRequested(page.nextTag("merge-main:" + cardId), cardId)
        onMoveRequested: column => {
            page.moveCard(page.panelCardId, column, page.topOf(column))
            if (column === "archived") page.panelOpen = false
        }
        onCloseRequested: page.closePanel()
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

    // Skróty: Ctrl+N nowa karta, Ctrl+F szukaj, Esc zamyka panel albo stronę.
    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            if (page.launchOpen && !page.managing) page.cancelLaunch()
            else if (page.panelOpen) page.closePanel()
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

// Panel karty po prawej stronie tablicy: nowa karta („＋ Karta”, „＋ Dodaj kartę” w kolumnie, Ctrl+N)
// albo szczegóły i edycja istniejącej (kliknięcie karty). Pola: tytuł, projekt (projekty z tablicy
// i repozytoria — wybranie repozytorium zakłada projekt), tagi (tagi projektu są dziedziczone i stałe),
// opis, notatka. Istniejąca karta ma też przeniesienie między kolumnami ręcznymi i archiwizację.
// Błąd z usługi pokazuje się w panelu (komunikat po polsku), commitError — dyskretnie pod nim.
// Ctrl+Enter zapisuje, Esc zamyka. Teksty z karty to dane: zawsze zwykły tekst.
// PT-134: dla karty z pełnym widokiem panel jest tylko szufladą szybkiej edycji opisu (`descriptionOnly`: pole „Opis”,
// „Zapisz opis”, Esc anuluje bez zapisu) albo szufladą nowej karty; rzadkie ustawienia są w pełnej karcie
// (CardFullView). Pełny formularz zostaje na razie dla kart bez dziennika — sprząta go PT-135.
// YouTrack (PT-7): pole „Zgłoszenie w YouTracku” (klucz albo adres zgłoszenia), a przy połączonej karcie —
// stan zgłoszenia, stan synchronizacji, „Otwórz w YouTracku” i przy konflikcie wybór „Weź z YouTracka” /
// „Nadpisz z Tide” (/Board ResolveYouTrack).
// Plan i specyfikacja karty (PT-5): zakładki „Opis” | „Plan” | „Specyfikacja” — dwie ostatnie tylko,
// gdy /Board CardDocs(id) je znalazł. Wybrana zakładka zostaje dla kolejnych kart, póki działa okno
// (karta bez takiego dokumentu pokazuje „Opis”). Przy dokumencie panel się poszerza (do 860 px, „Rozwiń” — prawie
// całe okno). Dokumenty odświeżamy przy każdej nowej migawce (sygnał Changed) i co 5 s, póki panel
// jest otwarty — usługa czyta plik ponownie tylko po zmianie, a widok podmienia treść tylko wtedy,
// gdy odpowiedź jest inna (przewinięcie zostaje).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "board.js" as Logic
import "cardDocs.js" as Docs
import "card" as CardUi

Rectangle {
    id: panel

    property var snapshot: null
    property var card: null             // null — nowa karta
    property string column: "backlog"   // kolumna nowej karty
    property string homeDir: ""
    property real nowTs: Date.now() / 1000
    property string error: ""
    property string notice: ""
    property bool busy: false
    property var modelsStatus: null
    property string branchDiff: ""
    property var tagTone: null
    // Formularz.
    property string projectKind: ""
    property string project: ""
    property string projectLabel: ""
    property var tags: []
    // Karta z chwili otwarcia panelu: z nią porównujemy formularz. `card` śledzi żywą migawkę, więc
    // porównanie z nią wysłałoby stare pola i cofnęło zmianę zrobioną w tym czasie z zewnątrz.
    property var base: null
    // Plan i specyfikacja karty (CardDocs): odpowiedź usługi, wybrana zakładka i szerokość.
    property var docs: null
    property string docsText: ""
    property string docsPending: ""          // ID karty, na której CardDocs czekamy ("" — nic w drodze)
    property string tab: "opis"              // ostatni wybór użytkownika (opis | plan | spec) — także dla następnej karty
    property bool expanded: false
    property real maxWidth: 1200             // szerokość dostępna w oknie (ustawia BoardPage)
    property var renderer: null              // backend.renderMarkdown (przez BoardPage) albo null
    readonly property bool editing: card !== null
    // Szuflada szybkiej edycji opisu (PT-134): tylko pole „Opis” i „Zapisz opis” — rzadkie ustawienia są w pełnej karcie.
    property bool descriptionOnly: false
    readonly property string shownTab: editing && !descriptionOnly ? Docs.effectiveTab(docs, tab) : "opis"
    readonly property bool reading: shownTab !== "opis"
    readonly property var docTabs: Docs.tabs(docs)
    readonly property var flowPlan: editing && card && card.derived && card.derived.flow
                                    ? (card.derived.flow.plan || null) : null
    readonly property alias docView: docView
    readonly property alias flowRepeater: flowRepeater
    readonly property var inherited: projectKind === "project" ? Logic.inheritedTags(snapshot, project) : []
    readonly property var allTags: ((snapshot && snapshot.tags) || []).map(t => t.name)
    readonly property bool dirty: editing && base ? Object.keys(Logic.updateRequest(base, form())).length > 0
                                          : titleField.text.trim() !== ""
    signal saveRequested(var request)               // CreateCard albo UpdateCard (pola zmienione)
    signal moveRequested(string column)
    signal closeRequested()
    signal docsRequested(string cardId)            // /Board CardDocs
    signal openFileRequested(string path)          // „Otwórz plik”
    signal showInFolderRequested(string path)      // „Pokaż w katalogu”
    signal linkRequested(string url)               // link http(s) z Markdownu
    signal youtrackResolveRequested(string cardId, string choice)   // konflikt: youtrack | tide
    signal enterStepRequested(string cardId, string stepId)
    signal abandonStepRequested(string cardId, string stepId)
    signal reviewResolveRequested(string cardId, string choice)     // pytanie po limicie rund review
    signal stepResolveRequested(string cardId, var request)        // pytanie, zgoda albo błąd kroku
    signal pauseRequested(string cardId, bool paused)
    signal planEditRequested(string cardId, var request)
    property var planEditTicket: null
    signal planApproveRequested(string cardId)                      // zatwierdzenie propozycji planu
    signal environmentApproveRequested(string cardId, string yaml, bool onlyPlan)   // /Board ApproveCardEnvironment
    property string environmentError: ""
    property var environmentReply: null                             // odmowa usługi: pozycje do ponownego pokazania
    readonly property var flowEnvironment: (flowPlan && flowPlan.environment)
        || (card && card.derived && card.derived.flow && card.derived.flow.environment) || null
    signal planManagerRequested()                                   // szeroki widok planu karty
    signal branchDiffRequested(string cardId)
    signal mergeMainRequested(string cardId)
    signal previewMergeRequested(string cardId, string target)
    signal chooseMergeRequested(string cardId)
    readonly property var mergeBase: card && card.derived && card.derived.flow ? card.derived.flow.base : null
    readonly property string defaultMergeTarget: mergeBase && mergeBase.status === "known"
                                                 && (mergeBase.baseRef || "").indexOf("refs/heads/") === 0
                                                 ? mergeBase.baseRef.substring(11) : ""

    function eventLabel(event) {
        const words = {"card.started": "zlecenie", "plan.approved": "plan zatwierdzony", "step.started": "start kroku",
                       "step.finished": "koniec kroku", "step.waiting": "czeka", "review.verdict": "review",
                       "result": "scalenie", "card.paused": "karta wstrzymana", "card.resumed": "karta wznowiona",
                       "plan.finished": "plan gotowy", "card.stalled": "karta stoi",
                       "card.merged": "karta scalona", "card.main-merged": "karta scalona"}
        return [event.ts || "", event.ticket || event.key || "", words[event.type] || event.type,
                event.targetBranch || event.main || "",
                event.executor || "", event.verdict || event.reason || ""].filter(x => x).join(" · ")
    }

    function planEntries() {
        const plan = flowPlan
        if (!plan) return []
        const out = []
        for (let i = 0; i < (plan.waves || []).length; i++) {
            for (const id of plan.waves[i]) {
                const row = (plan.rows || []).find(r => (r.id || r.workId) === id)
                if (row) out.push(Object.assign({wave: i + 1}, row))
            }
        }
        return out
    }

    function form() {
        const key = Logic.issueKey(youtrackField.text)
        return { title: titleField.text, description: descriptionEdit.text, note: noteField.text, tags: tags,
                 project: project, projectKind: projectKind, column: column,
                 youtrack: key === null ? youtrackField.text.trim() : key }
    }

    // Wypełnia formularz kartą (albo pustym, dla nowej) — przy otwarciu panelu.
    function load() {
        error = ""
        notice = ""
        branchDiff = ""
        closingFeedback.text = ""
        environmentError = ""
        environmentReply = null
        cardEnvironment.consented = []
        cardEnvironment.toggleLine = ""
        cardEnvironment.editing = false
        busy = false
        base = card ? Object.assign({}, card, {tags: (card.tags || []).slice()}) : null
        titleField.text = card ? card.title : ""
        descriptionEdit.text = card ? card.description || "" : ""
        noteField.text = card ? card.note || "" : ""
        youtrackField.text = card ? card.youtrack || "" : ""
        tags = card ? (card.tags || []).slice() : []
        projectKind = card && card.project ? "project" : ""
        project = card ? card.project || "" : ""
        projectLabel = card ? card.projectName || card.project || "" : ""
        docs = null
        docsText = ""
        docsPending = ""
        requestDocs()   // `tab` zostaje: ostatnio wybrana zakładka, póki działa okno Tide
        if (descriptionOnly) descriptionEdit.forceActiveFocus()
        else titleField.focusInput()
    }

    // Bez kolejki: póki odpowiedź dla tej karty nie wróciła, kolejne odświeżenie (5 s, Changed) czeka.
    function requestDocs() {
        if (!editing || !card || docsPending === card.id) return
        docsPending = card.id
        docsRequested(card.id)
    }

    // Odpowiedź CardDocs: tylko dla karty w panelu i tylko inna niż poprzednia (bez skoku przewinięcia).
    function applyDocs(cardId, text) {
        if (cardId === docsPending) docsPending = ""
        if (!editing || !card || cardId !== card.id || text === docsText) return
        docsText = text
        const value = Logic.parse(text, null)
        docs = value && value.ok ? value : null
    }

    function save() {
        if (busy) return
        const values = form()
        if (values.title.trim() === "") {
            error = "Karta potrzebuje tytułu."
            titleField.focusInput()
            return
        }
        if (Logic.issueKey(youtrackField.text) === null) {
            error = "Zgłoszenie w YouTracku to klucz, np. LEGAL-35 (albo adres zgłoszenia)."
            youtrackField.focusInput()
            return
        }
        const request = editing ? Logic.updateRequest(base || card, values) : Logic.createRequest(values)
        if (editing && Object.keys(request).length === 0) {
            closeRequested()
            return
        }
        error = ""
        busy = true
        saveRequested(request)
    }

    function addTag(name) {
        const value = String(name || "").trim().replace(/,/g, "")
        if (!value || Logic.hasTag(tags, value) || Logic.hasTag(inherited, value)) return
        tags = tags.concat([value])
    }

    width: reading ? (expanded ? maxWidth : Math.min(maxWidth, 860)) : 420
    Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
    radius: 16
    color: Tide.Tokens.backgroundPanel
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.09)
    Accessible.role: Accessible.Dialog
    Accessible.name: editing ? "Karta " + card.id : "Nowa karta"

    Keys.onPressed: event => {
        if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) {
            panel.save()
            event.accepted = true
        } else if (event.key === Qt.Key_Escape) {
            panel.closeRequested()
            event.accepted = true
        }
    }

    // Klik w panel nie przechodzi do tablicy pod spodem.
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: wheel => wheel.accepted = true }

    // Plan i specyfikacja na żywo: po nowej migawce (Changed — także plik w docs/ tablicy) i co 5 s
    // (plan w repozytorium projektu nie zmienia migawki).
    onSnapshotChanged: if (visible && editing) docsSoon.restart()
    Timer { id: docsSoon; interval: 300; onTriggered: panel.requestDocs() }
    Timer {
        interval: 5000
        repeat: true
        running: panel.visible && panel.editing
        onTriggered: panel.requestDocs()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 14

        // Nagłówek.
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Text {
                visible: panel.editing
                text: panel.editing ? panel.card.id : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.sky
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: panel.editing ? Logic.columnLabel(panel.snapshot, Logic.displayColumn(panel.card))
                                    : "Nowa karta · " + Logic.columnLabel(panel.snapshot, panel.column)
                textFormat: Text.PlainText
                color: panel.editing ? Tide.Tokens.textMuted : Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: panel.editing ? 13 : 17
                font.weight: panel.editing ? Font.Normal : Font.DemiBold
                elide: Text.ElideRight
                Accessible.role: Accessible.Heading
            }
            PillButton {
                objectName: "cardExpand"
                visible: panel.reading
                implicitHeight: 30
                pixelSize: 12
                flat: true
                text: panel.expanded ? "Zwęź" : "Rozwiń"
                toolTip: panel.expanded ? "Panel o zwykłej szerokości" : "Panel na prawie całe okno"
                onClicked: panel.expanded = !panel.expanded
            }
            PillButton {
                implicitWidth: 30
                implicitHeight: 30
                flat: true
                text: "✕"
                toolTip: "Zamknij (Esc)"
                onClicked: panel.closeRequested()
            }
        }

        // Zakładki: Opis zawsze, Plan i Specyfikacja — gdy są.
        Segmented {
            objectName: "cardTabs"
            visible: panel.editing && !panel.descriptionOnly && panel.docTabs.length > 1
            itemHeight: 28
            options: panel.docTabs
            current: panel.shownTab
            onPicked: value => panel.tab = value
        }
        Text {
            objectName: "cardDocsWarnings"
            Layout.fillWidth: true
            visible: panel.reading && !!panel.docs && (panel.docs.warnings || []).length > 0
            text: panel.docs && panel.docs.warnings ? panel.docs.warnings.join(" · ") : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.butter
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
            wrapMode: Text.Wrap
        }

        ColumnLayout {
            objectName: "cardPlanProposal"
            Layout.fillWidth: true
            visible: !panel.descriptionOnly && !!panel.flowPlan && (panel.planEntries().length > 0 || panel.flowPlan.status !== "reviewing")
            spacing: 5
            PillButton {
                objectName: "cardOpenPlanManager"
                text: "Otwórz plan na całym ekranie"
                outline: true
                onClicked: panel.planManagerRequested()
            }
            Text {
                Layout.fillWidth: true
                text: panel.flowPlan && panel.flowPlan.status === "blocked" ? "Plan czeka na wybór recenzenta"
                      : panel.flowPlan && panel.flowPlan.status === "reviewing" ? "Przegląd planu w toku"
                      : panel.flowPlan && panel.flowPlan.status === "pending" ? "Plan do zatwierdzenia"
                      : panel.flowPlan && panel.flowPlan.status === "approved" ? "Zatwierdzony plan"
                      : panel.flowPlan && panel.flowPlan.status === "done" ? "Plan wykonany"
                      : "Błędy propozycji planu"
                textFormat: Text.PlainText
                color: Tide.Tokens.sky
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 15
                font.bold: true
            }
            Text {
                objectName: "cardPlanErrors"
                Layout.fillWidth: true
                visible: !!panel.flowPlan && (panel.flowPlan.errors || []).length > 0
                text: panel.flowPlan ? (panel.flowPlan.errors || []).join(" · ") : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.rose
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }
            Text {
                objectName: "cardPlanReviewWait"
                Layout.fillWidth: true
                visible: !!panel.flowPlan && panel.flowPlan.status === "blocked"
                text: panel.flowPlan && panel.flowPlan.reviewWait
                      ? panel.flowPlan.reviewWait.reason + ". Możesz poprawić przydziały ticketów i wybrać inny model do przeglądu. Implementacja ruszy dopiero po Twoim zatwierdzeniu planu."
                      : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.butter
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                wrapMode: Text.Wrap
            }
            RowLayout {
                Layout.fillWidth: true
                visible: !!panel.flowPlan && panel.flowPlan.status === "blocked"
                spacing: 8
                QQC2.ComboBox {
                    // Kółko myszy przewija panel, nie zmienia wyboru (03.11: przypadkowa zmiana wykonawcy).
                    wheelEnabled: false
                    id: planReviewPicker
                    objectName: "cardPlanReviewExecutor"
                    Layout.fillWidth: true
                    model: [{value: "", label: "Wybierz recenzenta"}].concat(Logic.planReviewerExecutors(
                        panel.modelsStatus, panel.flowPlan ? panel.flowPlan.planner : "",
                        panel.flowPlan && panel.flowPlan.reviewWait ? panel.flowPlan.reviewWait.provider : ""))
                    textRole: "label"
                    valueRole: "value"
                    currentIndex: -1
                    Accessible.name: "Model do przeglądu planu"
                }
                PillButton {
                    objectName: "cardPlanRunReview"
                    text: "Uruchom przegląd"
                    enabled: !panel.busy && !!planReviewPicker.currentValue
                    onClicked: panel.planEditRequested(panel.card.id,
                        {action: "review-executor", executor: planReviewPicker.currentValue})
                }
            }
            // Uwagi review bywają bardzo długie, a ten blok leży nad przewijaną częścią panelu — bez limitu
            // wysokości decyzje kroków (Zezwól, Ponów) znikały pod krawędzią (K-33). Po zatwierdzeniu planu
            // review jest historią i jest w widoku planu na całym ekranie.
            QQC2.ScrollView {
                id: planReviewScroll
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(planReviewText.implicitHeight, 110)
                visible: !!panel.flowPlan && !!panel.flowPlan.review && !!panel.flowPlan.review.verdict
                         && panel.flowPlan.status !== "approved" && panel.flowPlan.status !== "done"
                clip: true
                contentWidth: availableWidth
                Text {
                    id: planReviewText
                    objectName: "cardPlanReview"
                    width: planReviewScroll.availableWidth
                    text: panel.flowPlan && panel.flowPlan.review ? "Review planu: " + (panel.flowPlan.review.verdict || "")
                          + " · " + (panel.flowPlan.review.reviewer || "") + " · " + Logic.reviewFindingsText(panel.flowPlan.review.findings) : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    wrapMode: Text.Wrap
                }
            }
            Text {
                objectName: "cardPlanDifference"
                Layout.fillWidth: true
                visible: !!panel.flowPlan && !!panel.flowPlan.revision && !!panel.flowPlan.revision.pending
                text: {
                    if (!visible) return ""
                    const d = panel.flowPlan.revision.difference || {}
                    const fields = {title: "tytuł", executor: "wykonawca", reviewer: "recenzent", class: "klasa", blockers: "blokery", body: "kartka przekazania"}
                    let lines = []
                    if ((d.added || []).length) lines.push("Dodane tickety: " + d.added.join(", "))
                    if ((d.removed || []).length) lines.push("Usunięte tickety: " + d.removed.join(", "))
                    for (const change of (d.changed || [])) lines.push(change.ticket + ": " + change.fields.map(f => fields[f] || f).join(", "))
                    return "Różnica planu: " + (lines.join(" · ") || "bez zmian")
                }
                textFormat: Text.PlainText
                color: Tide.Tokens.textPrimary
                wrapMode: Text.Wrap
            }
            Flickable {
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(planRows.implicitHeight, 180)
                visible: panel.planEntries().length > 0
                contentWidth: width
                contentHeight: planRows.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                ColumnLayout {
                    id: planRows
                    width: parent.width
                    spacing: 4
                    Repeater {
                        model: panel.planEntries()
                        delegate: RowLayout {
                            required property var modelData
                            required property int index
                            property var ticket: modelData
                            Layout.fillWidth: true
                            spacing: 6
                            Text {
                                Layout.fillWidth: true
                                text: "Fala " + ticket.wave + " · " + (ticket.id || ticket.workId) + " " + ticket.title
                                      + " · robi " + ticket.executor + " · review " + ticket.reviewer
                                      + (ticket.blockers.length ? " · blokery " + ticket.blockers.join(", ") : "")
                                      + (ticket.note ? " · " + ticket.note : "")
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                wrapMode: Text.Wrap
                            }
                            PillButton {
                                objectName: "cardPlanTicket-" + (index + 1)
                                text: ticket.editable === false || panel.flowPlan.status === "reviewing" ? "Kartka" : "Popraw ticket"
                                outline: true
                                enabled: !!ticket.cardPath
                                onClicked: {
                                    if (ticket.editable !== true || panel.flowPlan.status === "reviewing") panel.openFileRequested(ticket.cardPath)
                                    else {
                                        panel.planEditTicket = ticket
                                        planClass.text = ticket.class || "standard"
                                        planExecutor.text = ticket.executor
                                        planReviewer.text = ticket.reviewer
                                        planBody.text = ticket.body || ""
                                    }
                                }
                            }
                        }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                visible: !!panel.planEditTicket
                QQC2.TextField { id: planClass; objectName: "cardPlanClass"; Layout.fillWidth: true; placeholderText: "Klasa: lekki, standard, mocny, lokalny" }
                QQC2.TextField { id: planExecutor; objectName: "cardPlanExecutor"; Layout.fillWidth: true; placeholderText: "Wykonawca" }
                QQC2.TextField { id: planReviewer; objectName: "cardPlanReviewer"; Layout.fillWidth: true; placeholderText: "Recenzent" }
                QQC2.TextArea { id: planBody; objectName: "cardPlanBody"; Layout.fillWidth: true; wrapMode: TextEdit.Wrap; placeholderText: "Kartka przekazania" }
                PillButton {
                    objectName: "cardPlanSaveTicket"
                    text: "Zapisz zmiany ticketu"
                    enabled: !panel.busy
                    onClicked: {
                        panel.planEditRequested(panel.card.id, {action: "edit", ticket: panel.planEditTicket.id || panel.planEditTicket.workId,
                            class: planClass.text, executor: planExecutor.text, reviewer: planReviewer.text, body: planBody.text})
                        panel.planEditTicket = null
                    }
                }
            }
            QQC2.TextArea {
                id: planFeedback
                objectName: "cardPlanFeedback"
                Layout.fillWidth: true
                visible: !!panel.flowPlan && !(panel.flowPlan.revision && panel.flowPlan.revision.kind === "append") && (panel.flowPlan.status === "pending" || panel.flowPlan.status === "approved" || Boolean(panel.flowPlan.revision && panel.flowPlan.revision.failed))
                placeholderText: "Uwagi do poprawy planu"
                wrapMode: TextEdit.Wrap
            }
            RowLayout {
                visible: planFeedback.visible
                PillButton {
                    objectName: "cardPlanReturn"
                    text: "Odeślij z uwagami"
                    enabled: !panel.busy && !!panel.flowPlan && planFeedback.text.trim().length > 0 && (!(panel.flowPlan.revision && panel.flowPlan.revision.seq) || panel.flowPlan.revision.failed)
                    onClicked: panel.planEditRequested(panel.card.id, {action: "return", feedback: planFeedback.text})
                }
                PillButton {
                    objectName: "cardPlanEnter"
                    text: "Wejdź w planowanie"
                    enabled: !panel.busy && !!panel.flowPlan && !!panel.flowPlan.plannerStep
                    onClicked: panel.planEditRequested(panel.card.id, {action: "enter"})
                }
            }
            PillButton {
                objectName: "cardPlanDiscardRevision"
                text: panel.flowPlan && panel.flowPlan.revision && panel.flowPlan.revision.kind === "append"
                      ? "Odrzuć dopisanie" : "Odrzuć błędną poprawkę"
                visible: !!panel.flowPlan && !!panel.flowPlan.revision && (!!panel.flowPlan.revision.failed ||
                         (panel.flowPlan.revision.kind === "append" && panel.flowPlan.status === "pending"))
                enabled: !panel.busy
                onClicked: panel.planEditRequested(panel.card.id, {action: "discard-revision"})
            }
            PillButton {
                objectName: "cardPlanApprove"
                text: panel.flowPlan && panel.flowPlan.revision && panel.flowPlan.revision.pending
                      ? "Zatwierdź różnicę" : "Zatwierdź plan i rozpocznij implementację"
                visible: !!panel.flowPlan && panel.flowPlan.status === "pending" && !panel.flowEnvironment
                enabled: !panel.busy
                onClicked: {
                    panel.busy = true
                    panel.planApproveRequested(panel.card.id)
                }
            }
        }

        CardUi.CardEnvironment {
            id: cardEnvironment
            Layout.fillWidth: true
            environment: panel.descriptionOnly ? null : panel.flowEnvironment
            planPending: !!panel.flowPlan && panel.flowPlan.status === "pending"
            busy: panel.busy
            error: panel.environmentError
            reply: panel.environmentReply
            onApproveRequested: (yaml, onlyPlan) => {
                panel.busy = true
                panel.environmentApproveRequested(panel.card.id, yaml, onlyPlan)
            }
        }

        ColumnLayout {
            objectName: "cardFlowClosing"
            Layout.fillWidth: true
            spacing: 6
            visible: Boolean(panel.editing && !panel.descriptionOnly && panel.card && panel.card.derived
                             && panel.card.derived.flow && panel.card.derived.flow.closed)
            Text {
                Layout.fillWidth: true
                text: "Karta do sprawdzenia"
                textFormat: Text.PlainText
                color: Tide.Tokens.sky
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 15
                font.bold: true
            }
            // Podsumowanie domknięcia bywa długie, a ta sekcja leży nad przewijaną częścią panelu — bez limitu
            // przyciski Diff/Scal i ich wynik znikały pod krawędzią (03.11, K-15).
            QQC2.ScrollView {
                id: closingSummaryScroll
                objectName: "cardClosingSummary"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(closingSummaryText.implicitHeight, 140)
                clip: true
                contentWidth: availableWidth
                Text {
                    id: closingSummaryText
                    width: closingSummaryScroll.availableWidth
                    text: panel.card && panel.card.derived && panel.card.derived.flow
                          ? panel.card.derived.flow.summary || "" : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 7
                PillButton {
                    objectName: "cardBranchDiff"
                    text: "Diff gałęzi"
                    enabled: !panel.busy
                    onClicked: panel.branchDiffRequested(panel.card.id)
                }
                PillButton {
                    objectName: "cardPreviewMerge"
                    Layout.fillWidth: true
                    wrapText: true
                    readonly property string target: panel.defaultMergeTarget
                    text: "Podgląd scalenia do " + (target || "…")
                    enabled: !panel.busy && target !== ""
                    onClicked: panel.previewMergeRequested(panel.card.id, target)
                }
                PillButton {
                    objectName: "cardMergeMain"
                    Layout.fillWidth: true
                    wrapText: true
                    readonly property string target: panel.defaultMergeTarget
                    text: target ? "Scal do " + target : "Scal…"
                    enabled: !panel.busy && !(panel.card && panel.card.derived && panel.card.derived.flow
                                                && panel.card.derived.flow.merged)
                    onClicked: {
                        if (target) panel.previewMergeRequested(panel.card.id, target)
                        else panel.chooseMergeRequested(panel.card.id)
                    }
                }
                PillButton {
                    objectName: "cardMergeChoose"
                    text: "Scal…"
                    visible: panel.defaultMergeTarget !== ""
                    onClicked: panel.chooseMergeRequested(panel.card.id)
                }
            }
            QQC2.TextArea {
                objectName: "cardBranchDiffText"
                Layout.fillWidth: true
                Layout.preferredHeight: 180
                visible: panel.branchDiff !== ""
                readOnly: true
                text: panel.branchDiff
                wrapMode: TextEdit.NoWrap
            }
            QQC2.TextArea {
                id: closingFeedback
                objectName: "cardClosingFeedback"
                Layout.fillWidth: true
                Layout.preferredHeight: 70
                visible: true
                placeholderText: "Co poprawić?"
                wrapMode: TextEdit.Wrap
            }
            PillButton {
                objectName: "cardReturnForChanges"
                text: "Do poprawy…"
                visible: true
                enabled: !panel.busy && closingFeedback.text.trim().length > 0
                onClicked: {
                    panel.busy = true
                    panel.planEditRequested(panel.card.id, {action: "return", feedback: closingFeedback.text})
                }
            }
        }

        CardDocView {
            id: docView
            objectName: "cardDocView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: panel.reading
            kind: panel.shownTab === "spec" ? "spec" : "plan"
            doc: panel.docs ? (panel.shownTab === "spec" ? panel.docs.spec : panel.docs.plan) : null
            others: panel.docs ? (panel.docs.others || []).filter(o => o.kind === kind) : []
            homeDir: panel.homeDir
            nowTs: panel.nowTs
            renderer: panel.renderer
            onOpenFile: path => panel.openFileRequested(path)
            onShowInFolder: path => panel.showInFolderRequested(path)
            onOpenLink: url => panel.linkRequested(url)
        }

        Flickable {
            id: formFlick
            visible: !panel.reading
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: formColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

            ColumnLayout {
                id: formColumn
                width: formFlick.width
                spacing: 6

                FieldLabel { text: "Tytuł"; visible: !panel.descriptionOnly }
                LineInput {
                    id: titleField
                    objectName: "cardTitle"
                    visible: !panel.descriptionOnly
                    Layout.fillWidth: true
                    placeholder: "Co jest do zrobienia?"
                    accessibleName: "Tytuł karty"
                    maximumLength: 200
                    pixelSize: 14
                    invalid: panel.error !== "" && text.trim() === ""
                    onAccepted: panel.save()
                }

                FieldLabel { text: "Projekt"; Layout.topMargin: 8; visible: !panel.descriptionOnly }
                ProjectPicker {
                    id: picker
                    objectName: "cardProject"
                    visible: !panel.descriptionOnly
                    Layout.fillWidth: true
                    snapshot: panel.snapshot
                    kind: panel.projectKind
                    key: panel.project
                    label: panel.projectLabel
                    onChosen: (kind, key, label) => {
                        panel.projectKind = kind
                        panel.project = key
                        panel.projectLabel = label
                    }
                }

                FieldLabel { text: "Tagi"; Layout.topMargin: 8; visible: !panel.descriptionOnly }
                Flow {
                    objectName: "cardTags"
                    visible: !panel.descriptionOnly
                    Layout.fillWidth: true
                    spacing: 6
                    Repeater {
                        model: panel.inherited
                        TagChip {
                            required property var modelData
                            name: modelData
                            dotColor: panel.tagTone ? panel.tagTone(modelData) : Tide.Tokens.textMuted
                            selectable: true
                            selected: true
                            locked: true
                            suffix: "z projektu"
                        }
                    }
                    Repeater {
                        // Wszystkie tagi tablicy (bez dziedziczonych) i nowe, dopisane w tej karcie.
                        model: panel.allTags.concat(panel.tags.filter(t => !Logic.hasTag(panel.allTags, t)))
                                            .filter(t => !Logic.hasTag(panel.inherited, t))
                        TagChip {
                            required property var modelData
                            name: modelData
                            dotColor: panel.tagTone ? panel.tagTone(modelData) : Tide.Tokens.textMuted
                            selectable: true
                            selected: Logic.hasTag(panel.tags, modelData)
                            onClicked: panel.tags = Logic.toggleTag(panel.tags, modelData)
                        }
                    }
                    Field {
                        width: 120
                        height: 22
                        radius: 11
                        focused: newTag.activeFocus
                        TextInput {
                            id: newTag
                            objectName: "cardNewTag"
                            anchors.fill: parent
                            anchors.leftMargin: 9
                            anchors.rightMargin: 9
                            verticalAlignment: TextInput.AlignVCenter
                            color: Tide.Tokens.textPrimary
                            selectionColor: Tide.Tokens.accent
                            selectedTextColor: Tide.Tokens.backgroundDesktop
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                            maximumLength: 40
                            clip: true
                            onAccepted: {
                                panel.addTag(text)
                                text = ""
                            }
                            Accessible.role: Accessible.EditableText
                            Accessible.name: "Nowy tag karty"
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: newTag.text === ""
                                text: "＋ nowy tag"
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font: newTag.font
                            }
                        }
                    }
                }

                FieldLabel { text: "Opis"; Layout.topMargin: 8 }
                Field {
                    Layout.fillWidth: true
                    implicitHeight: 150
                    focused: descriptionEdit.activeFocus
                    Flickable {
                        id: descriptionFlick
                        anchors.fill: parent
                        anchors.margins: 10
                        clip: true
                        contentWidth: width
                        contentHeight: descriptionEdit.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds
                        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                        TextEdit {
                            id: descriptionEdit
                            objectName: "cardDescription"
                            width: descriptionFlick.width
                            wrapMode: TextEdit.Wrap
                            textFormat: TextEdit.PlainText
                            color: Tide.Tokens.textPrimary
                            selectionColor: Tide.Tokens.accent
                            selectedTextColor: Tide.Tokens.backgroundDesktop
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                            activeFocusOnTab: true
                            Keys.onTabPressed: event => { noteField.focusInput(); event.accepted = true }
                            onCursorRectangleChanged: {
                                if (cursorRectangle.y < descriptionFlick.contentY) descriptionFlick.contentY = cursorRectangle.y
                                else if (cursorRectangle.y + cursorRectangle.height > descriptionFlick.contentY + descriptionFlick.height)
                                    descriptionFlick.contentY = cursorRectangle.y + cursorRectangle.height - descriptionFlick.height
                            }
                            Accessible.role: Accessible.EditableText
                            Accessible.name: "Opis karty"
                            Text {
                                visible: descriptionEdit.text === ""
                                text: "Szczegóły, linki, kryteria — Markdown jak w pliku karty"
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font: descriptionEdit.font
                                width: parent.width
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }

                FieldLabel { text: "Notatka na karcie"; Layout.topMargin: 8; visible: !panel.descriptionOnly }
                LineInput {
                    id: noteField
                    objectName: "cardNote"
                    visible: !panel.descriptionOnly
                    Layout.fillWidth: true
                    placeholder: "np. bez agenta, robię sam"
                    accessibleName: "Notatka na karcie"
                    maximumLength: 300
                    onAccepted: panel.save()
                }

                FieldLabel { text: "Zgłoszenie w YouTracku"; Layout.topMargin: 8; visible: !panel.descriptionOnly }
                LineInput {
                    id: youtrackField
                    objectName: "cardYouTrackField"
                    visible: !panel.descriptionOnly
                    Layout.fillWidth: true
                    placeholder: "np. LEGAL-35 — stan zgłoszenia pójdzie za kartą"
                    accessibleName: "Zgłoszenie w YouTracku"
                    maximumLength: 200
                    invalid: Logic.issueKey(text) === null
                    onAccepted: panel.save()
                }

                // Stan zgłoszenia i synchronizacji (PT-7) — tylko połączona karta.
                Rectangle {
                    id: ytBox
                    objectName: "cardYouTrackBox"
                    readonly property var sync: panel.editing && panel.card.youtrackSync ? panel.card.youtrackSync : null
                    readonly property bool conflict: !!sync && sync.status === "conflict"
                    Layout.fillWidth: true
                    Layout.topMargin: 4
                    visible: sync !== null && !panel.descriptionOnly
                    radius: Tide.Tokens.radiusSmall
                    color: conflict ? Tide.Theme.alpha(Tide.Tokens.rose, 0.10) : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.04)
                    border.width: conflict ? 1 : 0
                    border.color: Tide.Theme.alpha(Tide.Tokens.rose, 0.45)
                    implicitHeight: ytColumn.implicitHeight + 16
                    ColumnLayout {
                        id: ytColumn
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 8
                        anchors.leftMargin: 12
                        spacing: 6
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Text {
                                text: Logic.syncGlyph(ytBox.sync)
                                visible: text !== ""
                                textFormat: Text.PlainText
                                color: Tide.Tokens[Logic.syncTone(ytBox.sync)]
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 13
                                font.weight: Font.DemiBold
                            }
                            Text {
                                objectName: "cardYouTrackState"
                                Layout.fillWidth: true
                                text: ytBox.sync ? (ytBox.sync.issue || "") + (ytBox.sync.issueState ? " · " + ytBox.sync.issueState : "") : ""
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.monoFamily
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }
                            PillButton {
                                objectName: "cardYouTrackOpen"
                                visible: Logic.youtrackUrl(panel.card) !== ""
                                implicitHeight: 26
                                pixelSize: 11
                                flat: true
                                text: "Otwórz w YouTracku ↗"
                                toolTip: Logic.youtrackUrl(panel.card)
                                onClicked: panel.linkRequested(Logic.youtrackUrl(panel.card))
                            }
                        }
                        Text {
                            objectName: "cardYouTrackText"
                            Layout.fillWidth: true
                            visible: text !== ""
                            text: ytBox.sync ? (ytBox.sync.text || "")
                                               + (ytBox.sync.via === "ticket" && ytBox.sync.label ? " · stan według: " + ytBox.sync.label : "")
                                             : ""
                            textFormat: Text.PlainText
                            color: ytBox.conflict ? Tide.Tokens.rose : Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            visible: ytBox.conflict
                            spacing: 8
                            PillButton {
                                objectName: "cardYouTrackTake"
                                implicitHeight: 28
                                pixelSize: 12
                                outline: true
                                text: "Weź z YouTracka"
                                toolTip: "Zostaw stan z YouTracka — Tide zapisze dopiero przy następnej zmianie karty"
                                onClicked: panel.youtrackResolveRequested(panel.card.id, "youtrack")
                            }
                            PillButton {
                                objectName: "cardYouTrackOverwrite"
                                implicitHeight: 28
                                pixelSize: 12
                                outline: true
                                text: "Nadpisz z Tide"
                                toolTip: "Zapisz w YouTracku stan karty mimo zmiany ręcznej"
                                onClicked: panel.youtrackResolveRequested(panel.card.id, "tide")
                            }
                            Item { Layout.fillWidth: true }
                        }
                    }
                }

                // Istniejąca karta: kolumna, archiwum, metadane.
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 14
                    visible: panel.editing && !panel.descriptionOnly
                    spacing: 8

                    FieldLabel { text: "Kolumna" }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Segmented {
                            objectName: "cardColumn"
                            itemHeight: 28
                            options: [{ value: "backlog", label: "Backlog" }, { value: "todo", label: "Do zrobienia" },
                                      { value: "done", label: "Gotowe" }]
                            // Kolumna wyświetlana: karta „Do sprawdzenia” ma zwykle zapisane todo — „Do zrobienia”
                            // to wtedy odesłanie do poprawy, a nie brak zmiany.
                            current: panel.editing ? Logic.displayColumn(panel.card) : ""
                            onPicked: value => { if (panel.editing && value !== Logic.displayColumn(panel.card)) panel.moveRequested(value) }
                        }
                        Item { Layout.fillWidth: true }
                        PillButton {
                            objectName: "cardArchive"
                            implicitHeight: 30
                            pixelSize: 12
                            outline: true
                            text: "Archiwizuj"
                            textColor: Tide.Tokens.rose
                            toolTip: "Zdejmij kartę z tablicy (plik i historia zostają)"
                            onClicked: panel.moveRequested("archived")
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: panel.editing && panel.card.derived !== null && panel.card.derived !== undefined
                        text: "Kolumnę wyświetlaną ustawia teraz koordynator karty — ręczna zmiana dotyczy pliku."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    ColumnLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: 8
                        spacing: 5
                        visible: Boolean(panel.editing && panel.card.derived && panel.card.derived.flow)
                        Repeater {
                            id: flowRepeater
                            model: panel.editing && panel.card.derived && panel.card.derived.flow
                                   ? (panel.card.derived.flow.steps || []) : []
                            ColumnLayout {
                                id: flowStepRow
                                Layout.fillWidth: true
                                spacing: 5
                                readonly property var flowStep: modelData
                                property string transferExecutor: ""
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: 8
                                    ColumnLayout {
                                        Layout.fillWidth: true
                                        spacing: 1
                                        Text {
                                            Layout.fillWidth: true
                                            text: [flowStep.ticket, flowStep.kind === "review" ? "review" : flowStep.kind === "fix" ? "poprawki" : "ticket", flowStep.executor || "", flowStep.result || (flowStep.mode === "interactive" ? "w Konsoli" : "w toku")].join(" · ")
                                            textFormat: Text.PlainText
                                            color: Tide.Tokens.textMuted
                                            font.family: Tide.Theme.uiFamily
                                            font.pixelSize: 12
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            objectName: "flowStepTimes"
                                            Layout.fillWidth: true
                                            visible: !flowStep.result && Boolean(flowStep.startedTs)
                                            text: "od " + Logic.when(flowStep.startedTs, panel.nowTs)
                                                  + " · ostatnia zmiana " + Logic.when(flowStep.lastActivityTs || flowStep.startedTs, panel.nowTs)
                                            textFormat: Text.PlainText
                                            color: Tide.Tokens.textMuted
                                            font.family: Tide.Theme.uiFamily
                                            font.pixelSize: 11
                                            elide: Text.ElideRight
                                        }
                                    }
                                    PillButton {
                                        objectName: "enterStep"
                                        text: flowStep.result ? "Wznów rozmowę" : "Przejmij w Konsoli"
                                        toolTip: flowStep.result ? "Otwórz tę rozmowę w Konsoli, także po restarcie komputera" : "Otwórz krok w Konsoli"
                                        implicitHeight: 28
                                        pixelSize: 12
                                        outline: true
                                        enabled: !panel.busy && flowStep.canEnter
                                        onClicked: panel.enterStepRequested(panel.card.id, flowStep.id)
                                    }
                                    PillButton {
                                        objectName: "abandonStep"
                                        visible: flowStep.reason === "interactive-closed" && flowStep.result === "stopped"
                                        text: "Porzuć"
                                        implicitHeight: 28
                                        pixelSize: 12
                                        flat: true
                                        enabled: !panel.busy
                                        onClicked: panel.abandonStepRequested(panel.card.id, flowStep.id)
                                    }
                                }
                                RowLayout {
                                    Layout.fillWidth: true
                                    visible: Boolean(flowStep.canTransfer)
                                    spacing: 8
                                    QQC2.ComboBox {
                                        // Kółko myszy przewija panel, nie zmienia wyboru (03.11: przypadkowa zmiana wykonawcy).
                                        wheelEnabled: false
                                        id: manualTransferPicker
                                        objectName: "cardStepTransferExecutor"
                                        Layout.fillWidth: true
                                        model: Logic.transferExecutors(panel.modelsStatus, flowStep.provider ||
                                                (Logic.launchExecutor(panel.modelsStatus, flowStep.executor) || {}).provider)
                                        textRole: "label"
                                        valueRole: "value"
                                        currentIndex: -1
                                        onActivated: index => flowStepRow.transferExecutor = model[index].value
                                        Accessible.name: "Wykonawca do przekazania kroku"
                                    }
                                    PillButton {
                                        objectName: "cardStepTransfer"
                                        text: "Przekaż innemu dostawcy"
                                        outline: true
                                        enabled: !panel.busy && manualTransferPicker.model.some(
                                                     option => option.value === flowStepRow.transferExecutor)
                                        onClicked: {
                                            panel.busy = true
                                            panel.stepResolveRequested(panel.card.id,
                                                                       { choice: "transfer", step: flowStep.id, executor: flowStepRow.transferExecutor })
                                        }
                                    }
                                }
                            }
                        }
                    }
                    Text {
                        objectName: "cardReviewVerdict"
                        Layout.fillWidth: true
                        visible: Boolean(panel.editing && panel.card && panel.card.derived && panel.card.derived.flow
                                         && panel.card.derived.flow.verdict)
                        text: {
                            const flow = panel.card && panel.card.derived ? panel.card.derived.flow : null
                            return flow && flow.verdict
                                ? "Review " + flow.verdict + " · BLOCKER " + flow.blockers
                                  + " · MAJOR " + flow.majors + " · NIT " + flow.nits
                                  + " · runda " + flow.round
                                : ""
                        }
                        textFormat: Text.PlainText
                        color: panel.card && panel.card.derived && panel.card.derived.flow
                               && panel.card.derived.flow.verdict === "APPROVE" ? Tide.Tokens.mint : Tide.Tokens.peach
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        objectName: "cardReviewDecision"
                        Layout.fillWidth: true
                        visible: Boolean(panel.editing && panel.card && panel.card.derived && panel.card.derived.flow
                                         && panel.card.derived.flow.reviewQuestion === true)
                        spacing: 8
                        PillButton {
                            objectName: "cardReviewRetry"
                            text: "Ponów poprawki"
                            enabled: !panel.busy
                            onClicked: {
                                panel.busy = true
                                panel.reviewResolveRequested(panel.card.id, "retry")
                            }
                        }
                        PillButton {
                            // Gdy uwagi dotyczą środowiska, a nie kodu — scalenie zatwierdzonego przez Ciebie stanu.
                            objectName: "cardReviewAccept"
                            text: "Scal mimo uwag"
                            outline: true
                            enabled: !panel.busy
                            onClicked: {
                                panel.busy = true
                                panel.reviewResolveRequested(panel.card.id, "accept")
                            }
                        }
                        PillButton {
                            objectName: "cardReviewReject"
                            text: "Odrzuć ticket"
                            outline: true
                            enabled: !panel.busy
                            onClicked: {
                                panel.busy = true
                                panel.reviewResolveRequested(panel.card.id, "reject")
                            }
                        }
                        Item { Layout.fillWidth: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: Boolean(panel.editing && panel.card.derived && panel.card.derived.flow)
                        Text {
                            objectName: "cardWaveLabel"
                            Layout.fillWidth: true
                            text: panel.card && panel.card.derived && panel.card.derived.flow ? panel.card.derived.flow.waveLabel || "Przebieg karty" : ""
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                        }
                        PillButton {
                            objectName: "cardPause"
                            readonly property bool paused: Boolean(panel.card && panel.card.derived && panel.card.derived.flow && panel.card.derived.flow.paused)
                            visible: Boolean(panel.card && panel.card.derived && (!panel.flowPlan || panel.flowPlan.status !== "done") && panel.card.derived.column !== "review")
                            text: paused ? "Wznów" : "Wstrzymaj kartę"
                            outline: true
                            enabled: !panel.busy
                            onClicked: panel.pauseRequested(panel.card.id, !paused)
                        }
                    }
                    Repeater {
                        model: panel.card && panel.card.derived && panel.card.derived.flow ? panel.card.derived.flow.waiting || [] : []
                        Text {
                            Layout.fillWidth: true
                            text: modelData.ticket + " · " + modelData.reason
                            textFormat: Text.PlainText
                            visible: Boolean(modelData.reason)
                            color: Tide.Tokens.peach
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                        }
                    }
                    Repeater {
                        objectName: "cardTimeline"
                        model: panel.card && panel.card.derived && panel.card.derived.flow ? panel.card.derived.flow.timeline || [] : []
                        Text {
                            Layout.fillWidth: true
                            text: panel.eventLabel(modelData)
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        Layout.topMargin: 6
                        text: panel.editing ? "utworzona " + Logic.when(panel.card.createdTs, panel.nowTs)
                                              + (panel.card.updatedTs && panel.card.updatedTs !== panel.card.createdTs
                                                 ? " · zmieniona " + Logic.when(panel.card.updatedTs, panel.nowTs) : "")
                                            : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                    }
                    Text {
                        Layout.fillWidth: true
                        text: panel.editing ? Logic.shortPath(panel.card.path, panel.homeDir) : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 11
                        elide: Text.ElideMiddle
                    }
                }
            }
        }

        // Błąd z usługi i dopisek o commicie.
        Rectangle {
            objectName: "cardError"
            Layout.fillWidth: true
            visible: panel.error !== ""
            radius: Tide.Tokens.radiusSmall
            color: Tide.Theme.alpha(Tide.Tokens.rose, 0.10)
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.rose, 0.45)
            implicitHeight: errorText.implicitHeight + 16
            Text {
                id: errorText
                anchors.fill: parent
                anchors.margins: 8
                anchors.leftMargin: 12
                text: panel.error
                textFormat: Text.PlainText
                color: Tide.Tokens.rose
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                wrapMode: Text.Wrap
                verticalAlignment: Text.AlignVCenter
            }
        }
        Text {
            objectName: "cardNotice"
            Layout.fillWidth: true
            visible: panel.notice !== ""
            text: panel.notice
            textFormat: Text.PlainText
            color: Tide.Tokens.butter
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            visible: !panel.reading
            spacing: 8
            Text {
                Layout.fillWidth: true
                text: panel.descriptionOnly ? "Ctrl+Enter zapisuje opis · Esc anuluje bez zapisu" : "Ctrl+Enter zapisuje"
                textFormat: Text.PlainText
                color: Tide.Theme.alpha(Tide.Tokens.textMuted, 0.8)
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
            }
            PillButton {
                implicitHeight: 34
                flat: true
                text: "Anuluj"
                onClicked: panel.closeRequested()
            }
            PillButton {
                objectName: "cardSave"
                implicitHeight: 34
                strong: true
                enabled: !panel.busy && (panel.dirty || !panel.editing)
                text: panel.busy ? "Zapisuję…" : panel.descriptionOnly ? "Zapisz opis" : panel.editing ? "Zapisz" : "Dodaj kartę"
                fill: Tide.Tokens.accent
                textColor: Tide.Tokens.backgroundDesktop
                onClicked: panel.save()
            }
        }
    }
}

// Widok „Tablica” w oknie Tide: karty ze wszystkich projektów. Dane i zapisy daje obiekt backend
// z backend.py (D-Bus do tide-daemon /Board albo stała migawka z pliku przy --data).
//
// Umowa widoku okna Tide (apps/tide/qml/Main.qml): backend, active, preview (opcje podglądu
// z --preview: card — otwarty panel karty, new — panel nowej karty, manage — strona Projekty i tagi,
// tag, project, query — filtry, layout=lanes — wiersz na projekt), showItem(item) — otwarcie na
// karcie (K-12: zaznaczona i przewinięta; przed pierwszą migawką czeka na nią; launch:K-12 — do tego
// okno „Co ma zrobić agent?”, przycisk „Uruchom” w popupie kafelka), takeFocus(), tab=plan|spec
// i expand=1 — zakładka dokumentu w otwartym panelu karty (z card=K-12),
// railInfo — ramka „Dane tablicy” i link „Projekty i tagi” na dole paska; import=1 — strona „Projekty i tagi”
// z otwartym importem z YouTracka (PT-7). Okno uruchomienia (PT-43): launch=K-9[,light|spec][,codex][,local][,move][,manual],
// shots=/a.png,/b.png — miniatury zrzutów w oknie (tylko podgląd).
// Zrzuty ekranu i model okna „Co ma zrobić agent?” idą przez backend (imagesJson, modelsJson, tasksJson,
// pasteImage, addFiles, capture…); na czas „Zrób zrzut” widok chowa całe okno Tide i przywraca je po Spectacle.
import QtQuick
import QtQuick.Window
import "board.js" as Logic

Item {
    id: view

    property var backend: null
    property bool active: true
    property var preview: ({})
    property string pendingItem: ""
    property real nowTs: Date.now() / 1000
    property int captureVisibility: Window.Windowed   // stan okna sprzed schowania na „Zrób zrzut”
    readonly property alias page: page
    readonly property var snapshot: backend ? Logic.parse(backend.snapshotJson, null) : null
    readonly property bool available: backend ? backend.available : false
    readonly property var railInfo: ({
        label: "Dane tablicy",
        value: snapshot && snapshot.boardDir ? Logic.shortPath(snapshot.boardDir, backend ? backend.homeDir : "") : "",
        note: Logic.railNote(snapshot, nowTs),
        link: { text: "Projekty i tagi", activate: function () { view.openManage() } }
    })

    function openManage() {
        page.panelOpen = false
        page.managing = true
        page.managePage.forceActiveFocus()
    }

    // Karta przychodzi zwykle przed migawką — wtedy czeka na pierwszą niepustą.
    function showItem(item) {
        if (!item) return
        if (item === "projekty" || item === "manage") {
            openManage()
            return
        }
        const launch = String(item).toLowerCase().indexOf("launch:") === 0
        const id = launch ? String(item).slice(7) : item
        if (!page.selectCard(id)) {
            pendingItem = snapshot && (snapshot.cards || []).length > 0 ? "" : item
            return
        }
        pendingItem = ""
        if (launch) {
            const card = snapshot.cards.find(c => c.id.toLowerCase() === id.toLowerCase())
            if (card) page.openLaunch(card.id, false)
        }
    }

    function takeFocus() {
        if (page.launchOpen) page.launchDialog.forceActiveFocus()
        else if (page.panelOpen) page.cardPanel.forceActiveFocus()
        else page.forceActiveFocus()
    }

    function previewValue(key) {
        const value = preview ? preview[key] : undefined
        if (value === undefined || value === null) return ""
        // Z Pythona lista przychodzi jako QStringList — ma length, ale Array.isArray jej nie rozpoznaje.
        if (typeof value === "string") return value
        return value.length ? String(value[0]) : ""
    }

    Component.onCompleted: if (backend && backend.nowTs > 0) nowTs = backend.nowTs
    // Po migawce strona przelicza karty w tej samej chwili — karta czeka na następny obieg pętli.
    onSnapshotChanged: if (pendingItem !== "" && snapshot && (snapshot.cards || []).length > 0) Qt.callLater(() => showItem(pendingItem))

    BoardPage {
        id: page
        anchors.fill: parent
        snapshot: view.snapshot
        available: view.available
        preview: view.backend ? view.backend.preview : false
        homeDir: view.backend ? view.backend.homeDir : ""
        markdownRenderer: view.backend ? function (markdown, link) { return view.backend ? view.backend.renderMarkdown(markdown, link) : "" } : null
        nowTs: view.nowTs
        onCreateRequested: (tag, request) => view.backend.createCard(tag, request)
        onUpdateRequested: (tag, id, request) => view.backend.updateCard(tag, id, request)
        onMoveRequested: (tag, id, column, index) => view.backend.moveCard(tag, id, column, index)
        onProjectRequested: (tag, request) => view.backend.upsertProject(tag, request)
        onTagRequested: (tag, request) => view.backend.upsertTag(tag, request)
        onRenderRequested: (tag, id, request) => view.backend.renderLaunch(tag, id, request)
        onStartRequested: (tag, request) => view.backend.startTask(tag, request)
        onFlowRequested: (tag, id, request) => view.backend.startCard(tag, id, request)
        onEnterStepRequested: (tag, id, step) => view.backend.enterStep(tag, id, step)
        onAbandonStepRequested: (tag, id, step) => view.backend.abandonStep(tag, id, step)
        onReviewResolveRequested: (tag, id, choice) => view.backend.resolveCardReview(tag, id, choice)
        onStepResolveRequested: (tag, id, request) => view.backend.resolveCardStep(tag, id, request)
        onPauseRequested: (tag, id, paused) => view.backend.setCardPaused(tag, id, paused)
        onPlanEditRequested: (tag, id, request) => view.backend.editCardPlan(tag, id, request)
        onPlanApproveRequested: (tag, id) => view.backend.approveCardPlan(tag, id)
        onBranchDiffRequested: (tag, id) => view.backend.cardBranchDiff(tag, id)
        onMergeMainRequested: (tag, id) => view.backend.mergeCardToMain(tag, id)
        // Zrzuty ekranu i model w oknie uruchomienia (PT-43).
        launchImages: view.backend ? Logic.parse(view.backend.imagesJson, []) : []
        canCapture: view.backend ? view.backend.canCapture : false
        modelsStatus: view.backend ? Logic.parse(view.backend.modelsJson, null) : null
        taskDefaults: view.backend ? Logic.parse(view.backend.tasksJson, {}) : ({})
        pasteImage: function (inTextField) { return view.backend ? view.backend.pasteImage(!!inTextField) : false }
        // Zapamiętany model i programy w PATH mogły się zmienić od startu widoku (okno „Nowe zadanie”, instalacja).
        onLaunchRequested: cardId => { if (view.backend) view.backend.refreshTasks() }
        onLaunchClosed: started => {
            if (!view.backend) return
            if (started) {
                view.backend.keepImages()
                view.backend.refreshTasks()
            } else {
                view.backend.discardImages()
            }
        }
        onRemoveImageRequested: path => { if (view.backend) view.backend.removeImage(path) }
        onFilesDropped: urls => { if (view.backend) view.backend.addFiles(urls) }
        onCaptureRequested: {
            if (!view.backend) return
            // Okno chowa się na czas zaznaczania obszaru (Spectacle robi zrzut ekranu przy starcie).
            const w = view.Window.window
            if (w) {
                view.captureVisibility = w.visibility
                w.visible = false
            }
            captureDelay.start()
            captureGuard.restart()
        }
        onModelsRequested: {
            const problem = view.backend ? view.backend.openModels() : ""
            if (problem !== "") page.launchDialog.error = problem
        }
        // „Otwórz w Sesjach” — koordynator w widoku Sesje okna Tide; „Przejdź do agenta” — okno agenta
        // (karta Konsoli albo wątek Codexa), bez przełączania okna Tide.
        onSessionRequested: target => view.backend.showCoordinator(target)
        onAgentRequested: session => view.backend.activateSession(session)
        // Plan i specyfikacja karty: CardDocs i otwieranie plików/linków przez backend (tylko ścieżki
        // z odpowiedzi usługi i adresy http(s)).
        onDocsRequested: cardId => { if (view.backend) view.backend.cardDocs(cardId) }
        onFileRequested: (path, how) => {
            if (!view.backend) return
            if (how === "folder") view.backend.showInFolder(path)
            else view.backend.openFile(path)
        }
        onLinkRequested: url => { if (view.backend) view.backend.openLink(url) }
        // YouTrack (PT-7): lista do importu, import i konflikt ze zmianą ręczną.
        onYoutrackIssuesRequested: (tag, refresh) => { if (view.backend) view.backend.youtrackIssues(tag, refresh) }
        onImportRequested: (tag, request) => { if (view.backend) view.backend.importYouTrack(tag, request) }
        onResolveRequested: (tag, ident, choice) => { if (view.backend) view.backend.resolveYouTrack(tag, ident, choice) }
        Component.onCompleted: {
            if (view.previewValue("tag")) tagFilter = view.previewValue("tag")
            if (view.previewValue("project")) projectFilter = view.previewValue("project")
            if (view.previewValue("query")) query = view.previewValue("query")
            if (view.previewValue("layout")) layoutMode = view.previewValue("layout")
            if (view.previewValue("card")) openCard(view.previewValue("card"))
            if (view.previewValue("tab")) cardPanel.tab = view.previewValue("tab")
            if (view.previewValue("expand")) cardPanel.expanded = true
            if (view.previewValue("new")) newCard(view.previewValue("new"))
            if (view.previewValue("launch")) {
                const parts = view.previewValue("launch").split(",")   // launch=K-9[,light|spec][,codex][,move][,manual]
                openLaunch(parts[0], parts.indexOf("move") >= 0)
                for (const part of parts.slice(1)) {
                    if (part === "full" || part === "light") { launchDialog.entry = "planning"; launchDialog.mode = part }
                    if (part === "spec") launchDialog.entry = "spec"
                    if (part === "claude" || part === "codex") launchDialog.pickTool(part)
                    if (part === "manual") launchDialog.manualCheck = true
                    if (part === "local") launchDialog.model = "local"
                }
            }
            if (view.previewValue("shots") && view.backend) view.backend.previewImages(view.previewValue("shots").split(","))
            if (view.previewValue("manage")) {
                managing = true
                managePage.openProject = view.previewValue("manage") === "1" ? "" : view.previewValue("manage")
            }
            if (view.previewValue("import")) openImport()
        }
    }

    Connections {
        target: view.backend
        function onReplied(tag, text) {
            page.handleReply(tag, Logic.parse(text, null))
        }
        function onDocsReplied(cardId, text) {
            page.cardPanel.applyDocs(cardId, text)
        }
        function onCaptureFinished(problem) {
            captureGuard.stop()
            view.restoreWindow()
            if (problem !== "") page.launchDialog.error = problem
            if (page.launchOpen) page.launchDialog.forceActiveFocus()
        }
        function onImageProblem(problem) {
            page.launchDialog.error = problem
        }
    }

    Timer {
        id: captureDelay
        interval: 250
        onTriggered: if (view.backend) view.backend.capture()
    }
    // Zabezpieczenie: gdyby captureFinished nie przyszło (Spectacle wisi), okno Tide wraca samo po 2 min.
    Timer {
        id: captureGuard
        interval: 120000
        onTriggered: view.restoreWindow()
    }

    // Okno Tide po „Zrób zrzut” wraca w stanie sprzed schowania (np. zmaksymalizowane).
    function restoreWindow() {
        const w = view.Window.window
        if (!w || w.visible) return
        w.visibility = view.captureVisibility === Window.Hidden ? Window.Windowed : view.captureVisibility
        w.raise()
        w.requestActivate()
    }

    // Zegar dla „zapisano 12:04” i czasów w panelu karty.
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: if (!view.backend || view.backend.nowTs <= 0) view.nowTs = Date.now() / 1000
    }

    Shortcut {
        sequence: "Ctrl+N"
        enabled: view.active && view.available
        onActivated: page.newCard("backlog")
    }
    Shortcut {
        sequences: [StandardKey.Find]
        enabled: view.active && !page.managing
        onActivated: page.focusSearch()
    }
    Shortcut {
        sequences: [StandardKey.Refresh]
        enabled: view.active
        onActivated: if (view.backend) view.backend.reload()
    }
}

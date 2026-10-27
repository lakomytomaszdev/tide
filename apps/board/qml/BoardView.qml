// Widok „Tablica” w oknie Tide: karty ze wszystkich projektów. Dane i zapisy daje obiekt backend
// z backend.py (D-Bus do tide-daemon /Board albo stała migawka z pliku przy --data).
//
// Umowa widoku okna Tide (apps/tide/qml/Main.qml): backend, active, preview (opcje podglądu
// z --preview: card — otwarty panel karty, new — panel nowej karty, manage — strona Projekty i tagi,
// tag, project, query — filtry, layout=lanes — wiersz na projekt), showItem(item) — otwarcie na
// karcie (K-12: zaznaczona i przewinięta; przed pierwszą migawką czeka na nią; launch:K-12 — do tego
// okno „Co ma zrobić agent?”, przycisk „Uruchom” w popupie kafelka), takeFocus(),
// railInfo — ramka „Dane tablicy” i link „Projekty i tagi” na dole paska.
import QtQuick
import "board.js" as Logic

Item {
    id: view

    property var backend: null
    property bool active: true
    property var preview: ({})
    property string pendingItem: ""
    property real nowTs: Date.now() / 1000
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
        nowTs: view.nowTs
        onCreateRequested: (tag, request) => view.backend.createCard(tag, request)
        onUpdateRequested: (tag, id, request) => view.backend.updateCard(tag, id, request)
        onMoveRequested: (tag, id, column, index) => view.backend.moveCard(tag, id, column, index)
        onProjectRequested: (tag, request) => view.backend.upsertProject(tag, request)
        onTagRequested: (tag, request) => view.backend.upsertTag(tag, request)
        onRenderRequested: (tag, id, request) => view.backend.renderLaunch(tag, id, request)
        onStartRequested: (tag, request) => view.backend.startTask(tag, request)
        Component.onCompleted: {
            if (view.previewValue("tag")) tagFilter = view.previewValue("tag")
            if (view.previewValue("project")) projectFilter = view.previewValue("project")
            if (view.previewValue("query")) query = view.previewValue("query")
            if (view.previewValue("layout")) layoutMode = view.previewValue("layout")
            if (view.previewValue("card")) openCard(view.previewValue("card"))
            if (view.previewValue("new")) newCard(view.previewValue("new"))
            if (view.previewValue("launch")) {
                const parts = view.previewValue("launch").split(",")   // launch=K-9[,light|spec][,codex][,move][,manual]
                openLaunch(parts[0], parts.indexOf("move") >= 0)
                for (const part of parts.slice(1)) {
                    if (part === "full" || part === "light" || part === "spec") launchDialog.mode = part
                    if (part === "claude" || part === "codex") launchDialog.pickTool(part)
                    if (part === "manual") launchDialog.manualCheck = true
                }
            }
            if (view.previewValue("manage")) {
                managing = true
                managePage.openProject = view.previewValue("manage") === "1" ? "" : view.previewValue("manage")
            }
        }
    }

    Connections {
        target: view.backend
        function onReplied(tag, text) {
            page.handleReply(tag, Logic.parse(text, null))
        }
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

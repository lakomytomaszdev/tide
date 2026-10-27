// Widok „Worktrees” w oknie Tide (dawne okno „Tide Worktrees”). Dane i akcje daje obiekt backend
// z backend.py (D-Bus do tide-daemon albo stała lista z pliku przy --data).
//
// Umowa widoku okna Tide (apps/tide/qml/Main.qml): backend, active, preview (opcje podglądu
// z --preview: pick — zaznaczony worktree, można powtórzyć; confirm — otwarte potwierdzenie
// usunięcia), showItem(item) — otwarcie na worktree (ścieżka: szukanie po jego gałęzi, żeby był
// widoczny; niczego nie zaznacza), takeFocus(), railInfo (tu brak).
import QtQuick

Item {
    id: view

    property var backend: null
    property bool active: true
    property var preview: ({})
    readonly property var railInfo: null
    readonly property alias page: page
    property string pendingItem: ""     // element sprzed pierwszej listy z usługi
    property bool applyingItem: false
    property bool refreshed: false       // pełny przegląd (Refresh) przy pierwszym pokazaniu widoku

    readonly property var worktrees: {
        if (!backend) return []
        try {
            const parsed = JSON.parse(backend.listJson)
            return Array.isArray(parsed) ? parsed : []
        } catch (e) {
            return []
        }
    }
    readonly property var launchers: {
        if (!backend) return { terminal: false, rider: false }
        try { return JSON.parse(backend.launchersJson) } catch (e) { return { terminal: false, rider: false } }
    }

    // Element przychodzi zwykle przed listą z usługi — wtedy czeka na pierwszą niepustą listę (i tylko
    // na nią: nieznaleziony przepada) albo do chwili, gdy użytkownik sam zmieni szukanie. Niczego
    // nie zaznacza — tylko szuka po gałęzi worktree, żeby był widoczny.
    function showItem(item) {
        if (!item) return
        const w = worktrees.find(x => x.path === item)
        if (!w) {
            pendingItem = worktrees.length > 0 ? "" : item
            return
        }
        pendingItem = ""
        applyingItem = true
        page.query = w.branch || w.name || ""
        applyingItem = false
    }

    function refreshOnce() {
        if (refreshed || !active || !backend) return
        refreshed = true
        backend.refresh()
    }

    function takeFocus() {
        page.forceActiveFocus()
    }

    function previewValues(key) {
        const value = preview ? preview[key] : undefined
        if (value === undefined || value === null) return []
        // Z Pythona lista przychodzi jako QStringList — ma length, ale Array.isArray jej nie rozpoznaje.
        if (typeof value === "string") return [value]
        return Array.prototype.map.call(value, String)
    }

    onWorktreesChanged: if (pendingItem !== "" && worktrees.length > 0) showItem(pendingItem)
    onActiveChanged: refreshOnce()
    Component.onCompleted: refreshOnce()

    Connections {
        target: page
        function onQueryChanged() { if (!view.applyingItem) view.pendingItem = "" }
    }

    WorktreesPage {
        id: page
        anchors.fill: parent
        worktrees: view.worktrees
        available: view.backend ? view.backend.available : false
        launchers: view.launchers
        homeDir: view.backend ? view.backend.homeDir : ""
        onRemoveRequested: paths => view.backend.remove(paths)
        onRemoveForcedRequested: (paths, deleteBranches, confirmed) => view.backend.removeForced(paths, deleteBranches,
                                                                                                  confirmed)
        onOpenRequested: (path, app) => view.backend.open(path, app)
        onRefreshRequested: view.backend.refresh()
        onPruneRequested: repoPath => view.backend.prune(repoPath)
        Component.onCompleted: {
            for (const path of view.previewValues("pick")) toggle(path)
            if (view.previewValues("confirm").length > 0) askRemove()
        }
    }

    Connections {
        target: view.backend
        function onRemoveFinished(text) {
            let results = null
            try { results = JSON.parse(text) } catch (e) {}
            page.finishRemove(results)
        }
        function onPruneFinished(text) {
            let result = null
            try { result = JSON.parse(text) } catch (e) {}
            page.finishPrune(result)
        }
    }

    // Zegar dla „5 min temu”.
    Timer {
        interval: 60000
        running: true
        repeat: true
        onTriggered: page.nowTs = Date.now() / 1000
    }

    Shortcut { sequences: [StandardKey.Find]; enabled: view.active; onActivated: page.focusSearch() }
    Shortcut {
        sequences: [StandardKey.Refresh]
        enabled: view.active
        onActivated: if (view.backend) view.backend.refresh()
    }
}

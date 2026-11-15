// Widok „Worktrees” w oknie Tide (dawne okno „Tide Worktrees”). Dane i akcje daje obiekt backend
// z backend.py (D-Bus do tide-daemon albo stała lista z pliku przy --data).
//
// Umowa widoku okna Tide (apps/tide/qml/Main.qml): backend, active, preview (opcje podglądu
// z --preview: pick — zaznaczony worktree, można powtórzyć; confirm — otwarte potwierdzenie
// usunięcia), showItem(item) — otwarcie na worktree (ścieżka: szukanie po jego gałęzi, żeby był
// widoczny; niczego nie zaznacza), takeFocus(), railInfo (tu brak).
import QtQuick
import QtQuick.Layouts
import "tide" as Tide

Item {
    id: view

    property var backend: null
    property bool active: true
    property string mode: "dirs"        // „dirs” (Katalogi) albo „branches” (Gałęzie); zapamiętuje backend
    signal navigateRequested(string viewId, string item)   // „Otwórz kartę”: okno Tide przełącza widok
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
    function setMode(next) {
        if (next === mode) return
        if (backend && backend.setViewMode) backend.setViewMode(next)
        mode = next
    }

    function showItem(item) {
        if (!item) return
        if (mode !== "dirs") mode = "dirs"  // worktree jest w trybie Katalogi; zapamiętany tryb się nie zmienia
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
        if (mode === "branches" && branchesLoader.item) branchesLoader.item.forceActiveFocus()
        else page.forceActiveFocus()
    }

    function previewValues(key) {
        const value = preview ? preview[key] : undefined
        if (value === undefined || value === null) return []
        // Z Pythona lista przychodzi jako QStringList — ma length, ale Array.isArray jej nie rozpoznaje.
        if (typeof value === "string") return [value]
        return Array.prototype.map.call(value, String)
    }

    Connections {
        target: view.backend
        ignoreUnknownSignals: true
        function onViewModeChanged() { if (view.backend.viewMode) view.mode = view.backend.viewMode }
    }

    onWorktreesChanged: if (pendingItem !== "" && worktrees.length > 0) showItem(pendingItem)
    onActiveChanged: refreshOnce()
    Component.onCompleted: {
        if (backend && backend.viewMode) mode = backend.viewMode
        refreshOnce()
    }

    Connections {
        target: page
        function onQueryChanged() { if (!view.applyingItem) view.pendingItem = "" }
    }

    // Przełącznik „Katalogi | Gałęzie” (K-99, PT-388): tryb zapamiętuje backend w ustawieniach okna.
    // Tryb Katalogi to dotychczasowa strona bez zmian; Gałęzie — mapa gałęzi kart i ticketów.
    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            Layout.topMargin: 12
            Layout.bottomMargin: 2
            spacing: 6

            Repeater {
                model: [{ id: "dirs", label: "Katalogi" }, { id: "branches", label: "Gałęzie" }]
                PillButton {
                    required property var modelData
                    readonly property bool current: view.mode === modelData.id
                    objectName: "mode_" + modelData.id
                    text: modelData.label
                    implicitHeight: 30
                    fill: current ? Tide.Tokens.accent : Tide.Tokens.backgroundCard
                    textColor: current ? Tide.Tokens.backgroundDesktop : Tide.Tokens.textSecondary
                    strong: current
                    Accessible.role: Accessible.RadioButton
                    Accessible.checked: current
                    onClicked: view.setMode(modelData.id)
                }
            }
            Item { Layout.fillWidth: true }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true

    WorktreesPage {
        id: page
        anchors.fill: parent
        visible: view.mode === "dirs"
        worktrees: view.worktrees
        available: view.backend ? view.backend.available : false
        launchers: view.launchers
        homeDir: view.backend ? view.backend.homeDir : ""
        diffBackend: view.backend
        onRemoveRequested: paths => view.backend.remove(paths)
        onRemoveForcedRequested: (paths, deleteBranches, confirmed) => view.backend.removeForced(paths, deleteBranches,
                                                                                                  confirmed)
        onOpenRequested: (path, app) => view.backend.open(path, app)
        onRefreshRequested: view.backend.refresh()
        onPruneRequested: repoPath => view.backend.prune(repoPath)
        onFetchRequested: repoPath => view.backend.fetch(repoPath)
        onPullRequested: path => view.backend.pull(path)
        Component.onCompleted: {
            for (const path of view.previewValues("pick")) toggle(path)
            if (view.previewValues("confirm").length > 0) askRemove()
        }
    }

    Loader {
        id: branchesLoader
        anchors.fill: parent
        active: view.mode === "branches"
        sourceComponent: Component {
            BranchesPage {
                objectName: "branchesPage"
                backend: view.backend
                active: view.active
                onOpenCardRequested: card => view.navigateRequested("board", card)
                Component.onCompleted: forceActiveFocus()
            }
        }
    }
        }
    }

    Connections {
        target: view.backend
        function onRemoveFinished(text) {
            let results = null
            try { results = JSON.parse(text) } catch (e) {}
            page.finishRemove(results)
        }
        function onFetchFinished(repoPath, text) {
            let result = null
            try { result = JSON.parse(text) } catch (e) {}
            page.finishFetch(repoPath, result)
        }
        function onPullFinished(path, text) {
            let result = null
            try { result = JSON.parse(text) } catch (e) {}
            page.finishPull(path, result)
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

    Shortcut { sequences: [StandardKey.Find]; enabled: view.active && view.mode === "dirs"; onActivated: page.focusSearch() }
    Shortcut {
        sequences: [StandardKey.Refresh]
        enabled: view.active
        onActivated: {
            if (view.mode === "branches" && branchesLoader.item) branchesLoader.item.refresh()
            else if (view.backend) view.backend.refresh()
        }
    }
}

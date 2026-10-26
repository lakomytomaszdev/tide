// Okno „Tide Worktrees”. Dane i akcje daje obiekt backend z tide-worktrees.py (D-Bus do
// tide-daemon albo stały plik JSON przy --data). grabPath: zapis zrzutu okna i wyjście (podgląd).
import QtQuick
import QtQuick.Window
import "tide" as Tide

Window {
    id: win

    property var backend: null
    property string grabPath: ""

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

    width: 1180
    height: 820
    minimumWidth: 820
    minimumHeight: 520
    visible: true
    title: "Worktrees"
    color: Tide.Tokens.backgroundWindow

    WorktreesPage {
        id: page
        anchors.fill: parent
        worktrees: win.worktrees
        available: win.backend ? win.backend.available : false
        launchers: win.launchers
        homeDir: win.backend ? win.backend.homeDir : ""
        onRemoveRequested: paths => win.backend.remove(paths)
        onOpenRequested: (path, app) => win.backend.open(path, app)
        onRefreshRequested: win.backend.refresh()
        onPruneRequested: repoPath => win.backend.prune(repoPath)
    }

    Connections {
        target: win.backend
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
        function onRaiseRequested() {
            win.show()
            win.raise()
            win.requestActivate()
        }
    }

    // Zegar dla „5 min temu”.
    Timer {
        interval: 60000
        running: true
        repeat: true
        onTriggered: page.nowTs = Date.now() / 1000
    }

    Shortcut { sequence: StandardKey.Find; onActivated: page.focusSearch() }
    Shortcut { sequence: StandardKey.Refresh; onActivated: if (win.backend) win.backend.refresh() }
    Shortcut { sequence: StandardKey.Close; onActivated: win.close() }

    Timer {
        interval: 700
        running: win.grabPath !== ""
        onTriggered: page.grabToImage(function (result) {
            result.saveToFile(win.grabPath)
            Qt.quit()
        })
    }
}

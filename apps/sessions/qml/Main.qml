// Okno „Tide Sesje”. Dane i akcje daje obiekt backend z tide-sessions.py (D-Bus do tide-daemon
// albo stały plik JSON przy --data). grabPath: zapis zrzutu okna i wyjście (podgląd);
// preselect, preticket i preresult (--select, --ticket, --result): stan do podglądu.
import QtQuick
import QtQuick.Window
import "tide" as Tide
import "sessions.js" as Logic

Window {
    id: win

    property var backend: null
    property string grabPath: ""
    property string preticket: ""
    property string preresult: ""

    readonly property var coordinators: backend ? Logic.parse(backend.coordinatorsJson, []) : []
    readonly property var board: {
        if (!backend) return null
        const value = Logic.parse(backend.boardJson, null)
        return value && value.id ? value : null
    }
    readonly property var sessions: backend ? Logic.parse(backend.sessionsJson, []) : []
    readonly property var detail: {
        if (!backend) return null
        const value = Logic.parse(backend.detailJson, null)
        return value && value.id ? value : null
    }

    width: 1680
    height: 980
    minimumWidth: 1180
    minimumHeight: 620
    visible: true
    title: "Sesje"
    color: Tide.Tokens.backgroundWindow

    SessionsPage {
        id: page
        anchors.fill: parent
        coordinators: win.coordinators
        board: win.board
        sessions: win.sessions
        detail: win.detail
        available: win.backend ? win.backend.available : false
        selectedId: win.backend ? win.backend.selected : ""
        canDiff: win.backend ? win.backend.canDiff : false
        onSelectRequested: id => win.backend.select(id)
        onActivateRequested: id => win.backend.activate(id)
        onRefreshRequested: force => win.backend.refresh(force)
        onDiffRequested: (id, key) => win.backend.openDiff(id, key)
        onWorktreeRequested: (id, key) => win.backend.openWorktree(id, key)
        onResultRequested: (id, run) => win.backend.loadDetail(id, run)
        onUrlRequested: url => Qt.openUrlExternally(url)
        onSessionRequested: id => win.backend.activateSession(id)
        Component.onCompleted: {
            if (win.preticket !== "") selectedKey = win.preticket
            if (win.preresult !== "") showResult(win.preresult)
        }
    }

    Connections {
        target: win.backend
        function onRaiseRequested() {
            win.show()
            win.raise()
            win.requestActivate()
        }
    }

    // Zegar dla „teraz · 4 min” i czasów agentów.
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: page.nowTs = Date.now() / 1000
    }
    Component.onCompleted: if (win.backend && win.backend.nowTs > 0) page.nowTs = win.backend.nowTs

    Shortcut { sequences: [StandardKey.Refresh]; onActivated: if (win.backend) win.backend.refresh(true) }
    Shortcut { sequences: [StandardKey.Close]; onActivated: win.close() }
    Shortcut { sequence: "Esc"; enabled: page.showingResult; onActivated: page.showingResult = false }

    Timer {
        interval: 900
        running: win.grabPath !== ""
        onTriggered: page.grabToImage(function (result) {
            result.saveToFile(win.grabPath)
            Qt.quit()
        })
    }
}

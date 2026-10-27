// Widok „Docker” w oknie Tide (własny podgląd zamiast Docker Desktop, decyzja 58). Dane i akcje daje
// obiekt backend z backend.py (D-Bus do tide-daemon albo stałe dane z pliku przy --data).
//
// Umowa widoku okna Tide (apps/tide/qml/Main.qml): backend, active, preview (opcje podglądu z --preview:
// logs — kontener z otwartymi logami, pick — zaznaczona kategoria porządków (można powtórzyć),
// confirm — prune albo remove:<kontener>, tc — testcontainers widoczne, query — szukanie),
// showItem(item) — kontener (ID, początek ID, nazwa) albo grupa („compose:cookapp”): odsłania,
// przewija i podświetla, takeFocus(), railInfo (wersja silnika i socket).
//
// Statystyki (Stats) tylko wtedy, gdy widok jest na wierzchu, okno nie jest zminimalizowane i coś
// działa — co STATS_MS; Docker liczy je dla każdego kontenera osobno. Miejsce na dysku (Disk) przy
// pierwszym pokazaniu i co DISK_MS; logi w szufladzie co LOGS_MS, dopóki jest otwarta.
import QtQuick
import QtQuick.Window
import "docker.js" as Logic

Item {
    id: view

    property var backend: null
    property bool active: true
    property var preview: ({})
    readonly property alias page: page
    property string pendingItem: ""          // element sprzed pierwszej migawki z usługi
    property int statsMs: 4000
    property int logsMs: 3000
    property int diskMs: 120000

    readonly property var snapshot: backend ? Logic.parse(backend.snapshotJson, null) : null
    readonly property var stats: backend ? Logic.parse(backend.statsJson, null) : null
    readonly property var disk: backend ? Logic.parse(backend.diskJson, null) : null
    readonly property bool serviceUp: backend ? backend.available === true : false
    readonly property bool windowShown: Window.window ? Window.window.visibility !== Window.Minimized
                                                        && Window.window.visibility !== Window.Hidden : true
    readonly property int runningCount: snapshot && snapshot.summary ? (snapshot.summary.running || 0)
                                        + (page.showTc ? (snapshot.summary.testcontainersRunning || 0) : 0) : 0
    // Czy teraz pytać Dockera o statystyki (testy sprawdzają, że w tle — nie).
    readonly property bool polling: active && windowShown && serviceUp && page.ready && runningCount > 0
    readonly property var railInfo: {
        if (!snapshot || !snapshot.available || !snapshot.engine) return null
        const engine = snapshot.engine
        return {
            label: "Docker Engine",
            value: (engine.version || "?") + (engine.apiVersion ? " · API " + engine.apiVersion : ""),
            note: (snapshot.host || "").replace(/^unix:\/\//, "socket ")
                  + (snapshot.live ? " · zmiany na żywo" : " · odświeżanie co 10 s")
        }
    }

    function showItem(item) {
        if (!item) return
        if (page.ready && page.reveal(item)) {
            pendingItem = ""
            return
        }
        // Przed pierwszą migawką czekamy; w gotowej migawce nieznany element przepada.
        pendingItem = page.ready ? "" : item
    }

    function takeFocus() {
        page.forceActiveFocus()
    }

    function previewValues(key) {
        const value = preview ? preview[key] : undefined
        if (value === undefined || value === null) return []
        if (typeof value === "string") return [value]
        return Array.prototype.map.call(value, String)
    }

    function openUrl(url) {
        // Tylko adresy z migawki (http(s)://localhost:<port>) — nic innego okno nie otwiera.
        if (/^https?:\/\/localhost:\d+\/?$/.test(url)) Qt.openUrlExternally(url)
    }

    // Po wiązaniach strony (page.ready liczy się z nowej migawki dopiero po tym sygnale).
    onSnapshotChanged: if (pendingItem !== "") Qt.callLater(() => { if (pendingItem !== "" && page.ready) showItem(pendingItem) })

    DockerPage {
        id: page
        anchors.fill: parent
        snapshot: view.snapshot
        stats: view.stats
        disk: view.disk
        serviceUp: view.serviceUp
        preview: view.backend ? view.backend.preview === true : false
        onActionRequested: (id, action) => {
            if (action === "start") view.backend.start(id)
            else if (action === "stop") view.backend.stop(id)
            else if (action === "restart") view.backend.restart(id)
            else if (action === "remove") view.backend.remove(id)
        }
        onGroupActionRequested: (project, action) => view.backend.groupAction(project, action)
        onLogsRequested: id => view.backend.logs(id, 200)
        onOpenLogsRequested: id => view.backend.openLogs(id)
        onShellRequested: id => view.backend.openShell(id)
        onPruneRequested: categories => view.backend.prune(categories)
        onLinkRequested: url => view.openUrl(url)
        Component.onCompleted: {
            if (view.previewValues("tc").length > 0) showTc = true
            const query = view.previewValues("query")
            if (query.length > 0) page.query = query[0]
            for (const id of view.previewValues("pick")) togglePick(id)
            const logs = view.previewValues("logs")
            if (logs.length > 0) {
                const entry = Logic.findContainer(view.snapshot, logs[0])
                if (entry) openLogs(entry.container.id)
            }
            const confirm = view.previewValues("confirm")
            if (confirm.length > 0 && confirm[0] === "prune") askPrune()
            else if (confirm.length > 0 && confirm[0].indexOf("remove:") === 0) {
                const entry = Logic.findContainer(view.snapshot, confirm[0].slice(7))
                if (entry) containerAction(entry.container.id, "remove")
            }
        }
    }

    Connections {
        target: view.backend
        function onActionFinished(id, action, text) { page.finishAction(id, action, Logic.parse(text, null)) }
        function onGroupFinished(project, action, text) { page.finishGroup(project, action, Logic.parse(text, null)) }
        function onPruneFinished(text) { page.finishPrune(Logic.parse(text, null)) }
        function onLogsReady(id, text) { page.showLogs(id, Logic.parse(text, null)) }
        function onOpened(what, id, ok) { if (!ok) page.openFailed(what, id) }
    }

    Timer {
        id: statsTimer
        interval: view.statsMs
        repeat: true
        triggeredOnStart: true
        running: view.polling
        onTriggered: view.backend.refreshStats()
    }
    Timer {
        interval: view.diskMs
        repeat: true
        triggeredOnStart: true
        running: view.active && view.serviceUp && page.ready
        onTriggered: view.backend.refreshDisk()
    }
    Timer {
        interval: view.logsMs
        repeat: true
        running: view.active && view.windowShown && view.serviceUp && page.logsId !== ""
        onTriggered: page.refreshLogs()
    }
    // Zegar dla „13 h”, „21 h temu”.
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: page.nowTs = Date.now() / 1000
    }

    Shortcut { sequences: [StandardKey.Find]; enabled: view.active; onActivated: page.focusSearch() }
    Shortcut {
        sequences: [StandardKey.Refresh]
        enabled: view.active
        onActivated: if (view.backend) { view.backend.reload(); view.backend.refreshDisk() }
    }
}

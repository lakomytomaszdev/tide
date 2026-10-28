// Widok „Modele” w oknie Tide: serwery modeli lokalnych (ten komputer, Mac Studio), przełącznik używanego
// serwera, tryb raportu, role i modele na serwerze — bez edycji plików. Dane i zapis daje obiekt backend
// z backend.py (D-Bus do tide-daemon /Models albo stałe dane z pliku przy --data).
//
// Umowa widoku okna Tide (apps/tide/qml/Main.qml): backend, active, preview, showItem(item) — nazwa serwera
// (karta się podświetla, „Modele na serwerze” pokazuje jego modele), takeFocus(), railInfo (używany serwer).
// Kafelek „Modele” (PT-18) otwiera ten widok przez org.tide.Window Show("models", "").
//
// Opcje podglądu (--preview): add=<nazwa> i url=<adres> — otwarty formularz „Dodaj serwer”, edit=<serwer>
// (z url=<adres> — zmieniony adres w edycji),
// remove=<serwer> — potwierdzenie usunięcia, server=<serwer> — modele tego serwera, advanced=<rola> (można
// powtórzyć), note=<tag>|<tekst> — błąd usługi przy kontrolce, ok=<tag>|<tekst> — powodzenie (tagi jak
// w ModelsPage: add:test, server:<nazwa>:remove, role:summary, report, pull:<serwer>…).
import QtQuick
import "models.js" as Logic

Item {
    id: view

    property var backend: null
    property bool active: true
    property var preview: ({})
    readonly property alias page: page
    property string pendingItem: ""          // serwer sprzed pierwszego Status() z usługi

    readonly property var status: backend ? Logic.parse(backend.statusJson, null) : null
    readonly property bool serviceUp: backend ? backend.available === true : false
    readonly property var railInfo: {
        if (!page.ready) return null
        const s = Logic.defaultServer(status)
        if (!s) return null
        return {
            label: "Używany serwer",
            value: s.label,
            note: Logic.hostText(s.url) + " · " + s.state.text + (s.online ? " · " + Logic.serverFacts(s) : "")
        }
    }

    function showItem(item) {
        if (!item) return
        if (page.ready && page.reveal(item)) {
            pendingItem = ""
            return
        }
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

    onStatusChanged: if (pendingItem !== "") Qt.callLater(() => { if (pendingItem !== "" && page.ready) showItem(pendingItem) })

    ModelsPage {
        id: page
        anchors.fill: parent
        status: view.status
        serviceUp: view.serviceUp
        preview: view.backend ? view.backend.preview === true : false
        onRequested: (member, argument, tag) => view.backend.request(member, argument, tag)
        onRefreshRequested: view.backend.refresh()
        Component.onCompleted: {
            const servers = view.previewValues("server")
            if (servers.length > 0) chosenServer = servers[0]
            let advancedMap = {}
            for (const r of view.previewValues("advanced")) advancedMap[r] = true
            advanced = advancedMap
            const edits = view.previewValues("edit")
            if (edits.length > 0) {
                edit(edits[0])
                if (view.previewValues("url").length > 0) editFill(view.previewValues("url")[0])
            }
            const removes = view.previewValues("remove")
            if (removes.length > 0) askRemove(removes[0])
            const adds = view.previewValues("add")
            if (adds.length > 0) {
                openAdd()
                addFormFill(adds[0], view.previewValues("url")[0] || "")
            }
            for (const n of view.previewValues("note")) {
                const parts = n.split("|")
                setNote(parts[0], { text: parts.slice(1).join("|"), error: true })
            }
            for (const n of view.previewValues("ok")) {
                const parts = n.split("|")
                setNote(parts[0], { text: parts.slice(1).join("|"), error: false })
            }
        }
    }

    Connections {
        target: view.backend
        function onReplied(tag, member, text) { page.finish(tag, member, Logic.parse(text, null)) }
    }

    Shortcut {
        sequences: [StandardKey.Refresh]
        enabled: view.active
        onActivated: if (view.backend) view.backend.refresh()
    }
}

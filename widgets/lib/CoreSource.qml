// Źródło stanu streamu z rdzenia Streamer przez D-Bus (PT-37, decyzja 77 — wyjątek od „jednego demona”:
// stan streamu należy do rdzenia, nie do tide-daemona). Usługa org.streamer.Core, obiekt /org/streamer/Core,
// interfejs org.streamer.Core1: GetState() → JSON widoku, Execute(nazwa, argumenty JSON) → JSON wyniku
// i sygnał Changed(zdarzenie JSON), który niesie cały nowy widok w polu `state`.
//
// Jedno źródło na widżet: GetState przy starcie i po każdym pojawieniu się usługi na szynie, potem tylko
// sygnał Changed; rzadki odczyt zapasowy (fallbackInterval) na wypadek zgubionego sygnału. Gdy usługi nie ma,
// `view` jest pusty, a `coreUp` fałszywe — kafelek mówi „rdzeń nie działa”. Bez aktywacji usługi przez D-Bus:
// nic nie woła rdzenia, którego nie ma na szynie.
//
// Komendy idą tylko przez execute(), które przepuszcza wyłącznie komendy z Stream.ALLOWED (bez akcji
// krytycznych, te są tylko w Panelu streamera).
//
// Miejsce na to, czego rdzeń jeszcze nie daje (propozycja API w docs/decisions.md, decyzja 77):
// - czat i alerty: GetChat() → {"events": [...]} i sygnał Chat(s) z komunikatem w kształcie WebSocketu
//   ({type: "chat", message} albo {type: "alert", alert}); wantChat włącza próbę i nasłuch;
// - podgląd sceny: GetSceneScreenshot(s scena) → s (data:image/jpeg;base64,…, ok. 640 px szerokości);
//   wantPreview włącza próbę po pierwszym odczycie stanu i odświeżanie co previewInterval, tylko gdy rdzeń go ma.
// Bez tych metod (błąd UnknownMethod) widżet pokazuje spokojne „jeszcze niedostępne w rdzeniu”.
import QtQuick
import org.kde.plasma.workspace.dbus as DBus
import "stream.js" as Stream

Item {
    id: source

    property string service: "org.streamer.Core"
    property string path: "/org/streamer/Core"
    property string iface: "org.streamer.Core1"
    property int fallbackInterval: 300000

    readonly property bool coreUp: watcher.registered
    property var view: null

    property bool wantChat: false
    property int chatLimit: 200
    property bool chatAvailable: false
    property var chatEvents: []

    property bool wantPreview: false
    property int previewInterval: 2000
    property bool previewAvailable: false
    property string previewImage: ""
    property bool previewTried: false

    signal executed(string name, bool ok, string message)

    function errorText(error) {
        const e = error && error.error ? error.error : error
        if (e && e.message) return String(e.message)
        return "Rdzeń nie odpowiedział"
    }

    // Argument sygnału przychodzi z wtyczki D-Bus Plasmy jako obiekt {value: "…"} (typ STRING), nie jako napis.
    function text(arg) {
        if (typeof arg === "string") return arg
        return arg && arg.value !== undefined ? String(arg.value) : String(arg)
    }

    function message(member, args) {
        return { service: service, path: path, iface: iface, member: member, arguments: args || [] }
    }

    function accept(next) {
        if (!next || typeof next !== "object" || !next.core) return
        if (view && typeof next.seq === "number" && typeof view.seq === "number" && next.seq < view.seq) return
        view = next
        if (wantPreview && !previewTried) {
            previewTried = true
            refreshPreview()
        }
    }

    function refresh() {
        DBus.SessionBus.asyncCall(message("GetState"), function (reply) {
            try { source.accept(JSON.parse(reply.value)) } catch (e) {}
        }, function () {})
    }

    // Komenda rdzenia; done(ok, wynik JSON albo opis błędu po polsku). Komendy spoza Stream.ALLOWED nie wychodzą.
    function execute(name, args, done) {
        if (!Stream.allowed(name)) {
            const why = name + ": tylko w Panelu streamera"
            source.executed(name, false, why)
            if (done) done(false, why)
            return
        }
        DBus.SessionBus.asyncCall(message("Execute", [name, JSON.stringify(args || {})]), function (reply) {
            source.executed(name, true, String(reply.value))
            if (done) done(true, String(reply.value))
        }, function (error) {
            const why = source.errorText(error)
            source.executed(name, false, why)
            if (done) done(false, why)
        })
    }

    function pushChat(json) {
        let event = null
        try { event = JSON.parse(text(json)) } catch (e) { return }
        if (!event || typeof event !== "object") return
        chatAvailable = true
        const next = chatEvents.concat([event])
        chatEvents = next.length > chatLimit ? next.slice(next.length - chatLimit) : next
    }

    function probeChat() {
        if (!wantChat) return
        DBus.SessionBus.asyncCall(message("GetChat"), function (reply) {
            let events = []
            try { events = JSON.parse(reply.value).events || [] } catch (e) {}
            source.chatEvents = events.slice(-source.chatLimit)
            source.chatAvailable = true
        }, function () { source.chatAvailable = false })
    }

    function refreshPreview() {
        const scene = view && view.obs && view.obs.connected ? view.obs.scene : ""
        if (!wantPreview || !scene) return
        DBus.SessionBus.asyncCall(message("GetSceneScreenshot", [String(scene)]),
            function (reply) {
                source.previewImage = String(reply.value)
                source.previewAvailable = source.previewImage.indexOf("data:image/") === 0
            }, function () { source.previewAvailable = false; source.previewImage = "" })
    }

    function connected() {
        refresh()
        probeChat()
    }

    DBus.DBusServiceWatcher {
        id: watcher
        busType: DBus.BusType.Session
        watchedService: source.service
        onRegisteredChanged: {
            if (registered) {
                source.connected()
            } else {
                // Rdzeń zniknął: żadnego starego stanu na ekranie; po powrocie wszystko od nowa.
                source.view = null
                source.chatAvailable = false
                source.previewAvailable = false
                source.previewImage = ""
                source.previewTried = false
            }
        }
    }

    DBus.SignalWatcher {
        busType: DBus.BusType.Session
        service: source.service
        path: source.path
        iface: source.iface
        function dbusChanged(eventJson) {
            let event = null
            try { event = JSON.parse(source.text(eventJson)) } catch (e) {}
            if (event && event.state) source.accept(event.state)
            else source.refresh()
        }
        function dbusChat(eventJson) { if (source.wantChat) source.pushChat(eventJson) }
    }

    Timer {
        interval: source.fallbackInterval
        running: source.coreUp && source.fallbackInterval > 0
        repeat: true
        onTriggered: source.refresh()
    }

    Timer {
        interval: source.previewInterval
        running: source.coreUp && source.wantPreview && source.previewAvailable
        repeat: true
        onTriggered: source.refreshPreview()
    }

    Component.onCompleted: if (watcher.registered) connected()
}

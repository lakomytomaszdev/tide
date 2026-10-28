// Źródło stanu streamu z rdzenia Streamer przez D-Bus (PT-37, decyzja 79 — wyjątek od „jednego demona”:
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
// Miejsce na to, czego rdzeń jeszcze nie daje (propozycja API w docs/decisions.md, decyzja 79):
// - czat i alerty: GetChat() → {"events": [...]} i sygnał Chat(s) z komunikatem w kształcie WebSocketu
//   ({type: "chat", message} albo {type: "alert", alert}); wantChat włącza próbę i nasłuch, wiersze trafiają
//   do chatModel (append, najstarsze wypadają przez remove(0));
// - podgląd sceny: GetSceneScreenshot(s scena) → s (data:image/jpeg;base64,…, ok. 640 px szerokości);
//   wantPreview włącza zrzut przy każdej zmianie połączenia OBS albo sceny i odświeżanie co previewInterval,
//   tylko gdy widżet jest widoczny w bieżącej aktywności (previewActive).
// Tylko odpowiedź UnknownMethod/UnknownInterface znaczy „rdzeń tego nie ma” (do ponownej rejestracji usługi);
// inny błąd (OBS chwilowo nie odpowiedział) czyści tylko obraz, a następna próba idzie normalnie. Naraz idzie
// najwyżej jedno zapytanie o zrzut; odpowiedź na zapytanie o inną scenę albo sprzed zniknięcia rdzenia odrzucamy
// i od razu pytamy o bieżącą scenę.
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

    // Pamięć stanu OBS (PT-83, decyzja 82): ostatni klucz Stream.obsKey poza przerwą w rdzeniu. Dzięki niej rozłączony
    // OBS zaraz po antenie to awaria w trakcie streamu („lost”), a bez historii — zwykłe granie („off”). Tylko
    // w pamięci widżetu, przerwa w rdzeniu jej nie zmienia.
    property string obsMemory: ""
    readonly property string obsRawKey: Stream.obsKey(view, coreUp, "")
    onObsRawKeyChanged: obsMemory = Stream.obsMemory(obsMemory, obsRawKey)

    property bool wantChat: false
    property int chatLimit: 200
    property bool chatAvailable: false
    property ListModel chatModel: ListModel {}

    property bool wantPreview: false
    property bool previewActive: true
    property int previewInterval: 2000
    property bool previewSupported: true
    property string previewImage: ""
    readonly property bool previewAvailable: previewSupported && previewImage !== ""
    // Scena (przy połączonym OBS), dla której ostatnio prosiliśmy o zrzut — zmiana wyzwala nowy zrzut od razu.
    property string previewKey: ""
    property int previewRequest: 0       // numer zapytania w toku (0 — żadnego)
    property int previewCounter: 0
    property bool previewAgain: false    // podczas zapytania zmieniła się scena albo minął okres — zapytaj po nim
    property int coreGeneration: 0       // rośnie przy każdym zniknięciu rdzenia z szyny
    readonly property string sceneForPreview: view && view.obs && view.obs.connected === true && view.obs.scene
        ? String(view.obs.scene) : ""

    signal executed(string name, bool ok, string message)

    function errorOf(error) {
        return error && error.error ? error.error : error
    }

    function errorText(error) {
        const e = errorOf(error)
        if (e && e.message) return String(e.message)
        return "Rdzeń nie odpowiedział"
    }

    // „Rdzeń nie ma tej metody” — w odróżnieniu od chwilowego błędu (OBS, przekroczony czas).
    function missingMethod(error) {
        const e = errorOf(error)
        const name = e && e.name ? String(e.name) : ""
        return name === "org.freedesktop.DBus.Error.UnknownMethod" || name === "org.freedesktop.DBus.Error.UnknownInterface"
    }

    // Argument sygnału przychodzi z wtyczki D-Bus Plasmy jako obiekt {value: "…"} (typ STRING), nie jako napis.
    function text(arg) {
        if (typeof arg === "string") return arg
        return arg && arg.value !== undefined ? String(arg.value) : String(arg)
    }

    function message(member, args) {
        return { service: service, path: path, iface: iface, member: member, arguments: args || [] }
    }

    // Sygnały przychodzą po kolei, więc widok z Changed zawsze wygrywa (także po szybkim restarcie rdzenia, gdy seq
    // liczy się od nowa). Odpowiedź GetState mogła się spóźnić za sygnałem — starszej od bieżącego widoku nie bierzemy.
    function accept(next, fromReply) {
        if (!next || typeof next !== "object" || !next.core) return
        if (fromReply && view && typeof next.seq === "number" && typeof view.seq === "number" && next.seq < view.seq)
            return
        view = next
    }

    function refresh() {
        if (!coreUp) return
        DBus.SessionBus.asyncCall(message("GetState"), function (reply) {
            try { source.accept(JSON.parse(reply.value), true) } catch (e) {}
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

    function appendChat(event) {
        const line = Stream.chatLine(event)
        if (!line) return
        chatModel.append(line)
        while (chatModel.count > chatLimit) chatModel.remove(0)
    }

    function pushChat(json) {
        let event = null
        try { event = JSON.parse(text(json)) } catch (e) { return }
        chatAvailable = true
        appendChat(event)
    }

    function probeChat() {
        if (!wantChat || !coreUp) return
        DBus.SessionBus.asyncCall(message("GetChat"), function (reply) {
            let events = []
            try { events = JSON.parse(reply.value).events || [] } catch (e) {}
            source.chatModel.clear()
            for (const e of events.slice(-source.chatLimit)) source.appendChat(e)
            source.chatAvailable = true
        }, function (error) { if (source.missingMethod(error)) source.chatAvailable = false })
    }

    function refreshPreview() {
        const scene = sceneForPreview
        previewKey = scene
        if (!wantPreview || !previewSupported || !previewActive || !coreUp) return
        if (!scene) {
            previewImage = ""
            return
        }
        if (previewRequest !== 0) {
            previewAgain = true
            return
        }
        const id = ++previewCounter
        const generation = coreGeneration
        previewRequest = id
        // Odpowiedź liczy się tylko dla tego samego rdzenia i tej samej sceny.
        const current = () => generation === source.coreGeneration && scene === source.sceneForPreview
        DBus.SessionBus.asyncCall(message("GetSceneScreenshot", [scene]), function (reply) {
            if (source.previewRequest !== id) return
            source.previewRequest = 0
            if (current()) {
                const image = String(reply.value)
                source.previewImage = image.indexOf("data:image/") === 0 ? image : ""
            }
            source.previewFinished()
        }, function (error) {
            if (source.previewRequest !== id) return
            source.previewRequest = 0
            if (current()) {
                source.previewImage = ""
                if (source.missingMethod(error)) source.previewSupported = false
            }
            source.previewFinished()
        })
    }

    function previewFinished() {
        if (!previewAgain) return
        previewAgain = false
        refreshPreview()
    }

    // Każda zmiana połączenia OBS albo sceny — zrzut od razu (OBS mógł połączyć się po rdzeniu).
    onSceneForPreviewChanged: if (sceneForPreview !== previewKey) refreshPreview()
    onPreviewActiveChanged: if (previewActive) refreshPreview()

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
                // Rdzeń zniknął: żadnego starego stanu na ekranie; po powrocie wszystko od nowa (także próba,
                // czy nowa wersja rdzenia ma podgląd i czat).
                source.view = null
                source.chatAvailable = false
                source.chatModel.clear()
                source.previewImage = ""
                source.previewSupported = true
                source.previewKey = ""
                source.coreGeneration++
                source.previewRequest = 0
                source.previewAgain = false
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
            if (event && event.state) source.accept(event.state, false)
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
        running: source.coreUp && source.wantPreview && source.previewSupported && source.previewActive
                 && source.sceneForPreview !== ""
        repeat: true
        onTriggered: source.refreshPreview()
    }

    Component.onCompleted: if (watcher.registered) connected()
}

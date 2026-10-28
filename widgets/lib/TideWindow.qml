// „Otwórz w Tide” (kafelek Modele) i „Otwórz Modele” (kafelek „AI”, PT-41): okno Tide na widoku Modele przez
// org.tide.Window /Window Show("models", "") — wspólna umowa planu modeli lokalnych (widok przychodzi w PT-26).
// Usługa /Models nie ma ShowWindow, więc kafelek mówi z oknem wprost:
//   - okno działa i zna widok → Show zwraca true, popup się zamyka (opened),
//   - okno działa, ale widoku jeszcze nie ma → Show zwraca false; wtedy Show("", "") wyciąga okno na wierzch
//     na jego bieżącym widoku, a popup pokazuje notatkę (failed("noview")),
//   - okna nie ma (nazwa niezajęta) → uruchamiamy je jak menu aplikacji (kstart --application tide, okno
//     wstaje na swoim domyślnym widoku) i co pół sekundy ponawiamy Show, aż okno odpowie.
// Całość ma termin waitMs od kliknięcia — także wtedy, gdy okno wisi i wywołanie nie wraca (limit D-Bus to
// 25–50 s): po nim failed("timeout"), a spóźnione odpowiedzi są pomijane.
// showCall i launchCall można podmienić w testach (atrapa magistrali i uruchamiania).
import QtQuick
import org.kde.plasma.workspace.dbus as DBus
import org.kde.plasma.plasma5support as P5Support

Item {
    id: opener

    property string view: "models"
    property string launcher: "kstart --application tide"
    property int waitMs: 10000
    property int retryMs: 500
    // showCall(widok, element, ok(wartość), błąd()) i launchCall(polecenie) — domyślnie D-Bus i „executable”.
    property var showCall: null
    property var launchCall: null

    readonly property bool busy: phase !== ""
    property string phase: ""                // "", "asking", "launching"
    property bool _inFlight: false
    property real _deadline: 0
    property int _attempt: 0                 // numer kliknięcia — odpowiedzi z poprzednich są pomijane

    signal opened()
    signal failed(string kind)               // noview, timeout

    function open() {
        if (busy) return
        _attempt++
        _deadline = Date.now() + waitMs
        phase = "asking"
        tick.start()
        ask(view, function (value) {
            if (value === undefined) launch()
            else finish(value === true)
        })
    }

    function finish(ok) {
        phase = ""
        tick.stop()
        if (ok) {
            opened()
            return
        }
        // Okno bez widoku Modele: przynajmniej na wierzch, na jego bieżącym widoku.
        call("", function () {})
        failed("noview")
    }

    function call(target, done) {
        const reply = value => done(value)
        const error = () => done(undefined)
        if (typeof showCall === "function") {
            showCall(target, "", reply, error)
            return
        }
        DBus.SessionBus.asyncCall({ service: "org.tide.Window", path: "/Window", iface: "org.tide.Window",
                                    member: "Show", arguments: [target, ""] },
            r => reply(r.value), error)
    }

    // Show(widok) w ramach bieżącego kliknięcia: done tylko dla aktualnej próby, póki trwa.
    function ask(target, done) {
        const attempt = _attempt
        _inFlight = true
        call(target, function (value) {
            if (attempt !== opener._attempt || !opener.busy) return
            opener._inFlight = false
            done(value)
        })
    }

    function launch() {
        phase = "launching"
        if (typeof launchCall === "function") launchCall(launcher)
        else executable.connectSource(launcher)
    }

    Timer {
        id: tick
        interval: opener.retryMs
        repeat: true
        onTriggered: {
            if (Date.now() > opener._deadline) {
                stop()
                opener._inFlight = false
                opener.phase = ""
                opener.failed("timeout")
                return
            }
            if (opener.phase !== "launching" || opener._inFlight) return
            opener.ask(opener.view, function (value) {
                if (value !== undefined) opener.finish(value === true)
            })
        }
    }

    // kstart kończy się zaraz po uruchomieniu aplikacji — wtedy odłączamy źródło.
    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => disconnectSource(sourceName)
    }
}

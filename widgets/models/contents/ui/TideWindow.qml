// „Otwórz w Tide”: okno Tide na widoku Modele przez org.tide.Window /Window Show("models", "") — wspólna umowa
// planu modeli lokalnych (widok przychodzi w PT-26). Usługa /Models nie ma ShowWindow, więc kafelek mówi
// z oknem wprost:
//   - okno działa i zna widok → Show zwraca true, popup się zamyka (opened),
//   - okno działa, ale widoku jeszcze nie ma → Show zwraca false; nic się nie przełącza, popup pokazuje
//     notatkę (failed("noview")),
//   - okna nie ma (nazwa niezajęta) → uruchamiamy je jak menu aplikacji (kstart --application tide, okno
//     wstaje na swoim domyślnym widoku) i co pół sekundy ponawiamy Show, aż okno odpowie albo minie
//     waitMs (failed("timeout")). Przed PT-26 okno zostaje wtedy na widoku domyślnym, z notatką.
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

    signal opened()
    signal failed(string kind)               // noview, timeout

    function open() {
        if (busy) return
        phase = "asking"
        ask(function (value) {
            if (value === undefined) {
                launch()
                return
            }
            finish(value === true)
        })
    }

    function finish(ok) {
        phase = ""
        retry.stop()
        if (ok) opened()
        else failed("noview")
    }

    function ask(done) {
        _inFlight = true
        const reply = value => { _inFlight = false; done(value) }
        const error = () => { _inFlight = false; done(undefined) }
        if (typeof showCall === "function") {
            showCall(view, "", reply, error)
            return
        }
        DBus.SessionBus.asyncCall({ service: "org.tide.Window", path: "/Window", iface: "org.tide.Window",
                                    member: "Show", arguments: [view, ""] },
            r => reply(r.value), error)
    }

    function launch() {
        phase = "launching"
        _deadline = Date.now() + waitMs
        if (typeof launchCall === "function") launchCall(launcher)
        else executable.connectSource(launcher)
        retry.start()
    }

    Timer {
        id: retry
        interval: opener.retryMs
        repeat: true
        onTriggered: {
            if (opener._inFlight) return
            if (Date.now() > opener._deadline) {
                stop()
                opener.phase = ""
                opener.failed("timeout")
                return
            }
            opener.ask(function (value) {
                if (value !== undefined && opener.phase === "launching") opener.finish(value === true)
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

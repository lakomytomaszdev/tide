// Źródło danych z usługi Limity AI (QuotaApp): lokalne API na 127.0.0.1:<port>
// z nagłówkiem X-Limity-Token. GET /status co pollInterval (częściej, gdy usługa właśnie
// czyta strony), POST /refresh i POST /open?service=chatgpt|claude|all.
import QtQuick

Item {
    id: source

    property int port: 0
    property string token: ""
    property int pollInterval: 10000
    property int busyPollInterval: 2000

    property var snapshot: null          // ostatnia poprawna odpowiedź /status
    property bool available: false       // usługa odpowiedziała 200 przy ostatnim zapytaniu
    property bool checked: false         // było już co najmniej jedno zapytanie
    readonly property bool configured: token !== "" && port > 0
    readonly property bool busy: available && !!snapshot && snapshot.busy === true
    property int pending: 0

    signal updated()

    function request(method, path, done) {
        if (!configured) {
            if (done) done(0, "")
            return
        }
        const xhr = new XMLHttpRequest()
        xhr.open(method, "http://127.0.0.1:" + port + path)
        xhr.setRequestHeader("X-Limity-Token", token)
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            if (done) done(xhr.status, xhr.responseText)
        }
        xhr.send()
    }

    function updateStatus() {
        // Nie mnożymy zapytań, gdy usługa wisi; po trzech pominiętych próbujemy mimo to.
        if (pending > 0 && pending < 3) {
            pending++
            return
        }
        pending = 1
        request("GET", "/status", function (code, body) {
            source.pending = 0
            let ok = code === 200
            if (ok) {
                try {
                    source.snapshot = JSON.parse(body)
                } catch (e) {
                    ok = false
                }
            }
            source.available = ok
            source.checked = true
            source.updated()
        })
    }

    function refresh() {
        request("POST", "/refresh", function () { source.updateStatus() })
    }

    function open(service) {
        request("POST", "/open?service=" + encodeURIComponent(service), function () { source.updateStatus() })
    }

    // Dane jednej usługi (chatgpt, claude); stan „checking”, dopóki nic nie wiadomo.
    function service(key) {
        const value = snapshot && snapshot[key]
        return value || { state: "checking", session: {}, weekly: {}, lastRead: null }
    }

    // Pozostały procent limitu (session, weekly) albo null.
    function remaining(key, metric) {
        if (!available) return null
        const value = (service(key)[metric] || {}).remaining
        return typeof value === "number" ? value : null
    }

    // Na kafelku liczy się niższy z dostępnych limitów usługi.
    function panelValue(key) {
        const values = [remaining(key, "session"), remaining(key, "weekly")].filter(v => v !== null)
        return values.length ? Math.min.apply(null, values) : null
    }

    function reset(key, metric) {
        return (service(key)[metric] || {}).reset || null
    }

    // Godzina ostatniego odczytu z ISO „2026-10-26T16:41+01:00” → „16:41”.
    function lastRead(key) {
        const value = service(key).lastRead
        return typeof value === "string" && value.length >= 16 ? value.slice(11, 16) : null
    }

    Timer {
        interval: source.busy ? source.busyPollInterval : source.pollInterval
        running: source.pollInterval > 0 && source.configured
        repeat: true
        onTriggered: source.updateStatus()
    }

    Component.onCompleted: updateStatus()
}

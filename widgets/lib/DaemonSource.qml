// Źródło danych z tide-daemon przez D-Bus: wywołuje metodę zwracającą JSON i odświeża ją
// po sygnale Changed, po pojawieniu się usługi i — na wszelki wypadek — co pollInterval.
import QtQuick
import org.kde.plasma.workspace.dbus as DBus

Item {
    id: source

    property string service: "org.tide.Daemon"
    property string path: "/Agents"
    property string iface: "org.tide.Daemon.Agents"
    property string method: "Sessions"
    property int pollInterval: 15000

    property var result: null            // ostatnia poprawna odpowiedź (sparsowany JSON)
    readonly property bool available: watcher.registered && !failed
    property bool failed: false

    signal refreshed()

    function refresh() {
        // Wywołanie aktywuje usługę przez D-Bus, nawet jeśli jeszcze nie działa.
        DBus.SessionBus.asyncCall({ service: service, path: path, iface: iface, member: method, arguments: [] },
            function (reply) {
                try {
                    source.result = JSON.parse(reply.value)
                    source.failed = false
                    source.refreshed()
                } catch (e) {
                    source.failed = true
                }
            },
            function () { source.failed = true })
    }

    // Wywołanie dowolnej metody usługi, np. ActivateSession; done(wartość | undefined przy błędzie).
    function call(member, args, done) {
        DBus.SessionBus.asyncCall({ service: service, path: path, iface: iface, member: member, arguments: args },
            function (reply) { if (done) done(reply.value) },
            function () { if (done) done(undefined) })
    }

    DBus.DBusServiceWatcher {
        id: watcher
        busType: DBus.BusType.Session
        watchedService: source.service
        onRegisteredChanged: if (registered) source.refresh()
    }

    DBus.SignalWatcher {
        busType: DBus.BusType.Session
        service: source.service
        path: source.path
        iface: source.iface
        function dbusChanged() { source.refresh() }
    }

    Timer {
        interval: source.pollInterval
        running: source.pollInterval > 0
        repeat: true
        onTriggered: source.refresh()
    }

    Component.onCompleted: refresh()
}

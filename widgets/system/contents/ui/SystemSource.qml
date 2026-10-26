// Dane widżetu System. Procesor, pamięć, karta graficzna i dyski z czujników ksystemstats
// (moduł org.kde.ksysguard.sensors — te same co w Monitorze systemu), reszta z pomocnika
// ../code/sysinfo.py przez silnik „executable” Plasma5Support: mapowanie punktów montowania
// na identyfikatory czujników dysków, największe procesy, nazwa komputera, dostępne aplikacje.
//
// Identyfikatory czujników:
//   cpu/all/usage                                   procesor, %
//   memory/physical/used, total                     pamięć (used = total − MemAvailable)
//   gpu/<gpuId>/usage, usedVram, totalVram, name    karta graficzna (gpuId domyślnie gpu0)
//   disk/<UUID>/used, total, free                   partycja; UUID z pomocnika (mounts)
// Procenty pamięci i dysków liczymy z used/total (uzasadnienie przy czujnikach dysków).
import QtQuick
import org.kde.ksysguard.sensors as Sensors
import org.kde.ksysguard.formatter as KFormatter
import org.kde.plasma.plasma5support as P5Support
import "../code/system.js" as Logic
import "tide" as Tide

Item {
    id: source

    property var paths: ["/", "/home"]
    property string gpuId: "gpu0"
    property string cpuSensorId: "cpu/all/usage"   // od niego zależy też „czujniki gotowe”
    // Popup otwarty albo widok szczegółowy na pasku bocznym: dopiero wtedy czujniki karty
    // graficznej (ksystemstats odpytuje wtedy nvidia-smi) i lista procesów.
    property bool detailed: false
    property int sampleInterval: 2000
    property int historySeconds: 60
    property int processInterval: 4000
    property int mountsInterval: 60000
    property int readyTimeout: 10000
    property int processCount: 3
    property string helper: "/usr/bin/python3 " + Logic.shellQuote(Logic.localPath(Qt.resolvedUrl("../code/sysinfo.py")))
    property string launcher: "kstart"     // polecenie otwierające aplikacje (w testach atrapa)
    // Podpowiedź o worktrees z tide-daemon; testy ją wyłączają, żeby nie pytać prawdziwej usługi.
    property bool worktreesHint: true

    property var info: null            // sysinfo.py info
    property var mounts: []            // sysinfo.py mounts: partycje i identyfikatory czujników
    property var mountStats: ({})      // target → ostatni odczyt statvfs z pomocnika (zapas)
    property string _mountsIdentity: ""
    property var processes: []         // sysinfo.py top
    property bool waitedForSensors: false

    readonly property bool sensorsReady: cpuSensor.status === Sensors.Sensor.Ready
    // missing: brak pakietu ksystemstats; stalled: pakiet jest, ale czujniki milczą.
    readonly property string problem: sensorsReady ? ""
        : info && info.ksystemstats === false ? "missing"
        : waitedForSensors ? "stalled"
        : ""

    readonly property var cpuPercent: read(cpuSensor)
    readonly property var memoryUsed: read(memoryUsedSensor)
    readonly property var memoryTotal: read(memoryTotalSensor)
    // Procent z used/total (jak opis „X z Y”); total = 0 tuż po starcie czujnika to jeszcze brak danych.
    readonly property var memoryPercent: memoryUsed !== null && memoryTotal ? memoryUsed * 100 / memoryTotal : null
    readonly property var gpu: ({
        available: gpuUsageSensor.status === Sensors.Sensor.Ready,
        name: gpuNameSensor.status === Sensors.Sensor.Ready ? String(gpuNameSensor.value || "") : "",
        usage: read(gpuUsageSensor),
        usedVram: read(gpuUsedSensor),
        totalVram: read(gpuTotalSensor)
    })
    // Wszystkie punkty montowania w systemie (do przypisania worktrees do partycji).
    property var mountTargets: []
    readonly property var targets: mountTargets.length ? mountTargets : mounts.map(m => m.target)
    readonly property var disks: {
        const out = []
        for (let i = 0; i < diskSensors.count; i++) {
            const item = diskSensors.objectAt(i)
            if (item) out.push(item.disk)
        }
        return out
    }
    readonly property var fullerDisk: Logic.fullerDisk(disks)
    readonly property bool diskNearlyFull: disks.some(d => d.usedPercent !== null && d.usedPercent > Logic.HINT_THRESHOLD)
    // Lista worktrees z tide-daemon albo null, gdy usługa nie wystawia /Worktrees.
    readonly property var worktrees: worktreesLoader.item && worktreesLoader.item.available
        && Array.isArray(worktreesLoader.item.result) ? worktreesLoader.item.result : null
    readonly property alias history: cpuHistory
    readonly property var apps: info && info.apps ? info.apps : ({})

    function read(sensor) {
        return sensor.status === Sensors.Sensor.Ready ? Logic.number(Number(sensor.value)) : null
    }

    function formatBytes(bytes) {
        // ShowNull: zero też ma być widoczne („0 B”), a nie pustym napisem.
        return Logic.number(bytes) === null ? "—" : KFormatter.Formatter.formatValueShowNull(bytes, KFormatter.Units.UnitByte)
    }

    function diskHint(disk) {
        return Logic.diskHint(disk, worktrees, targets, formatBytes)
    }

    // Uruchomienie pomocnika; done(obiekt | null). To samo polecenie w toku nie jest powtarzane,
    // chyba że czeka dłużej niż 30 s (zgubiona odpowiedź nie blokuje kolejnych odczytów).
    property var _pending: ({})
    function runHelper(args, done) {
        const command = helper + " " + args
        const waiting = _pending[command]
        if (waiting && Date.now() - waiting.since < 30000) return
        if (waiting) executable.disconnectSource(command)
        _pending[command] = { done: done, since: Date.now() }
        executable.connectSource(command)
    }

    function refreshInfo() { runHelper("info", result => { if (result) source.info = result }) }
    function refreshMounts() {
        runHelper("mounts " + paths.map(Logic.shellQuote).join(" "),
                  result => {
                      if (!result || !Array.isArray(result.mounts)) return
                      // Model czujników zmieniamy tylko przy zmianie partycji — inaczej co minutę
                      // powstawałyby od nowa. Liczby ze statvfs idą osobno (mountStats).
                      const identity = JSON.stringify(result.mounts.map(m => [m.target, m.sensorId]))
                      if (identity !== source._mountsIdentity) {
                          source._mountsIdentity = identity
                          source.mounts = result.mounts
                      }
                      const stats = {}
                      result.mounts.forEach(m => { stats[m.target] = m })
                      source.mountStats = stats
                      source.mountTargets = Array.isArray(result.targets) ? result.targets : []
                  })
    }
    function refreshProcesses() {
        runHelper("top " + processCount,
                  result => { source.processes = result && Array.isArray(result.processes) ? result.processes : [] })
    }

    // Otwiera aplikację po identyfikatorze pliku .desktop (kstart przez KIO, własny zakres systemd).
    function launch(appId, url) {
        if (!appId) return
        let command = launcher + " --application " + Logic.shellQuote(appId)
        if (url) command += " --url " + Logic.shellQuote(url)
        executable.connectSource(command)
    }

    P5Support.DataSource {
        id: executable
        engine: "executable"
        connectedSources: []
        onNewData: (sourceName, data) => {
            const waiting = source._pending[sourceName]
            delete source._pending[sourceName]
            disconnectSource(sourceName)
            if (waiting && typeof waiting.done === "function") waiting.done(Logic.parseOutput(data))
        }
    }

    Sensors.Sensor { id: cpuSensor; sensorId: source.cpuSensorId; updateRateLimit: 1000 }
    Sensors.Sensor { id: memoryUsedSensor; sensorId: "memory/physical/used"; updateRateLimit: 2000 }
    Sensors.Sensor { id: memoryTotalSensor; sensorId: "memory/physical/total" }
    Sensors.Sensor { id: gpuUsageSensor; sensorId: "gpu/" + source.gpuId + "/usage"; enabled: source.detailed; updateRateLimit: 2000 }
    Sensors.Sensor { id: gpuUsedSensor; sensorId: "gpu/" + source.gpuId + "/usedVram"; enabled: source.detailed; updateRateLimit: 2000 }
    Sensors.Sensor { id: gpuTotalSensor; sensorId: "gpu/" + source.gpuId + "/totalVram"; enabled: source.detailed }
    Sensors.Sensor { id: gpuNameSensor; sensorId: "gpu/" + source.gpuId + "/name"; enabled: source.detailed }

    // Czujniki każdej partycji z pomocnika; bez nich (np. bez ksystemstats) liczby ze statvfs.
    Instantiator {
        id: diskSensors
        model: source.mounts
        delegate: QtObject {
            id: diskItem
            property var mount: modelData
            // Procent liczymy z used/total, a nie z czujnika usedPercent: ten w praktyce liczył
            // od rozmiaru partycji z Solid (100 GiB) zamiast systemu plików (97,9 GiB) i pokazywał
            // 88% zamiast 90% z df. total też startuje od rozmiaru partycji, ale po pierwszym
            // odczycie się poprawia — dlatego bez ograniczenia częstotliwości (rzadko się zmienia).
            property Sensors.Sensor usedSensor: Sensors.Sensor {
                sensorId: "disk/" + diskItem.mount.sensorId + "/used"
                updateRateLimit: 5000
            }
            property Sensors.Sensor totalSensor: Sensors.Sensor {
                sensorId: "disk/" + diskItem.mount.sensorId + "/total"
            }
            property Sensors.Sensor freeSensor: Sensors.Sensor {
                sensorId: "disk/" + diskItem.mount.sensorId + "/free"
                updateRateLimit: 5000
            }
            readonly property var sensorUsed: source.read(usedSensor)
            readonly property var sensorTotal: source.read(totalSensor)
            readonly property var sensorFree: source.read(freeSensor)
            readonly property bool fromSensor: sensorUsed !== null && sensorTotal !== null && sensorTotal > 0
            readonly property var stats: source.mountStats[mount.target] || mount
            readonly property var disk: ({
                target: mount.target,
                source: mount.source,
                sensorId: mount.sensorId,
                fromSensor: fromSensor,
                usedPercent: fromSensor ? sensorUsed * 100 / sensorTotal : Logic.number(stats.usedPercent),
                free: sensorFree !== null ? sensorFree : Logic.number(stats.free),
                size: fromSensor ? sensorTotal : Logic.number(stats.size)
            })
        }
    }

    Tide.History {
        id: cpuHistory
        capacity: Math.max(1, Math.round(source.historySeconds * 1000 / source.sampleInterval))
    }

    // Próbka co sampleInterval także przy zamkniętym popupie — po otwarciu wykres jest pełny.
    Timer {
        interval: source.sampleInterval
        running: source.sensorsReady
        repeat: true
        onTriggered: cpuHistory.push(source.cpuPercent)
    }

    Timer {
        interval: source.processInterval
        running: source.detailed
        repeat: true
        onTriggered: source.refreshProcesses()
    }

    Timer {
        interval: source.mountsInterval
        running: true
        repeat: true
        onTriggered: source.refreshMounts()
    }

    Timer {
        interval: source.readyTimeout
        running: !source.sensorsReady && !source.waitedForSensors
        onTriggered: source.waitedForSensors = true
    }

    // Podpowiedź o worktrees: obszar /Worktrees w tide-daemon, pytany tylko przy prawie pełnym dysku.
    // Bez tego obszaru (starsza usługa albo brak usługi) wywołanie kończy się błędem i zostaje
    // samo ostrzeżenie „Dysk prawie pełny”.
    Loader {
        id: worktreesLoader
        active: source.worktreesHint && source.diskNearlyFull
        sourceComponent: Tide.DaemonSource {
            path: "/Worktrees"
            iface: "org.tide.Daemon.Worktrees"
            method: "List"
            pollInterval: 60000
        }
    }

    // Po otwarciu od razu świeże procesy i lista aplikacji (mogły dojść od ostatniego razu).
    onDetailedChanged: if (detailed) Qt.callLater(() => {
        refreshInfo()
        refreshProcesses()
    })

    // Dopiero po pełnym utworzeniu: wcześniejsze connectSource silnik „executable” gubi.
    Component.onCompleted: Qt.callLater(() => {
        refreshInfo()
        refreshMounts()
        if (detailed) refreshProcesses()
    })
}

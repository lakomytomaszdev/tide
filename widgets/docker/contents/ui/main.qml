// Widżet Docker: kafelek w docku z lewej, zaraz po System („Docker · 6 projektów”, „● 9 działa · ● 1 padł”;
// CompactDocker), popup z liczbami, projektami compose i padniętymi kontenerami (DockerPopup), a w pasku
// bocznym zwarty kafelek (SidebarDocker). „Otwórz w Tide” i „Logi” otwierają okno Tide na widoku Docker
// (tide-daemon: ShowWindow(id)).
// Dane: tide-daemon przez D-Bus (org.tide.Daemon /Docker, docs/tide-daemon.md#docker). Stats() tylko
// przy otwartym popupie (co 5 s), Disk() przy otwarciu; zatrzymanie projektu to zadanie GroupAction,
// którego wynik przychodzi sygnałem ActionFinished.
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "../code/docker.js" as Logic
import "tide" as Tide

PlasmoidItem {
    id: root

    readonly property var snapshot: source.result && typeof source.result === "object" && !Array.isArray(source.result)
                                    ? source.result : null
    readonly property bool serviceUp: source.available
    readonly property bool dockerUp: Logic.status(snapshot, serviceUp) === "ok"
    property var stats: null
    property var disk: null
    property var busy: ({})                  // id grupy albo kontenera → akcja w toku
    property var notes: ({})                 // id grupy albo kontenera → {text, error}
    property var jobs: ({})                  // job → {key, action, ts}
    property real nowTs: Date.now() / 1000
    readonly property bool popupShown: expanded && !Plasmoid.configuration.sidebarMode

    function setIn(map, key, value) {
        const next = Object.assign({}, map)
        if (value === undefined || value === null || value === "") delete next[key]
        else next[key] = value
        return next
    }

    // Okno Tide na widoku Docker (pusty element — bez wyboru); popup się zamyka, gdy okno się otworzyło.
    function showWindow(item) {
        source.call("ShowWindow", [item || ""], function (ok) {
            if (ok === true && !Plasmoid.configuration.sidebarMode) root.expanded = false
        })
    }

    function groupAction(project, action) {
        const key = "compose:" + project
        if (busy[key]) return
        busy = setIn(busy, key, action)
        notes = setIn(notes, key, null)
        source.call("GroupAction", [project, action], function (value) {
            const started = Logic.parse(value)
            if (!started || !started.ok || !started.job) {
                root.finish(key, Logic.groupNote(action, started || { ok: false, error: "usługa tide-daemon nie odpowiada" }))
                return
            }
            root.jobs = root.setIn(root.jobs, started.job, { key: key, action: action, ts: Date.now() })
        })
    }

    function startContainer(id) {
        if (busy[id]) return
        busy = setIn(busy, id, "start")
        notes = setIn(notes, id, null)
        source.call("Start", [id], value => root.finish(id, Logic.startNote(Logic.parse(value))))
    }

    function finish(key, note) {
        busy = setIn(busy, key, null)
        notes = setIn(notes, key, note)
        noteTimer.restart()
    }

    function jobDone(job, text) {
        const entry = jobs[job]
        if (!entry) return
        jobs = setIn(jobs, job, null)
        finish(entry.key, Logic.groupNote(entry.action, Logic.parse(text)))
    }

    Plasmoid.title: "Docker"
    Plasmoid.icon: "docker"
    toolTipMainText: "Docker"
    toolTipSubText: Logic.toolTip(snapshot, serviceUp)
    // Pasek boczny Tide pokazuje zwarty kafelek na stałe (scripts/panels/sidebar.js).
    preferredRepresentation: Plasmoid.configuration.sidebarMode ? fullRepresentation : compactRepresentation

    Tide.DaemonSource {
        id: source
        path: "/Docker"
        iface: "org.tide.Daemon.Docker"
        method: "Snapshot"
        pollInterval: 60000
        onActionFinished: (job, text) => root.jobDone(job, text)
    }

    // Statystyki tylko przy otwartym popupie — liczenie ich kosztuje Dockera (decyzja 58).
    Timer {
        interval: 5000
        repeat: true
        triggeredOnStart: true
        running: root.popupShown && root.dockerUp
        onTriggered: source.call("Stats", [], value => { const s = Logic.parse(value); if (s) root.stats = s })
    }
    onPopupShownChanged: {
        if (!popupShown) return
        nowTs = Date.now() / 1000
        source.call("Disk", [], value => { const d = Logic.parse(value); if (d) root.disk = d })
    }
    // Wyniki akcji gasną po chwili; zadanie bez wyniku (usługa zniknęła) zwalnia przycisk po 10 min.
    Timer {
        id: noteTimer
        interval: 12000
        onTriggered: root.notes = ({})
    }
    Timer {
        interval: 30000
        repeat: true
        running: Object.keys(root.jobs).length > 0
        onTriggered: {
            for (const job in root.jobs) {
                if (Date.now() - root.jobs[job].ts > 600000)
                    root.jobDone(job, JSON.stringify({ ok: false, error: "brak wyniku z usługi — sprawdź stan w oknie Tide" }))
            }
        }
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? -1 : tile.implicitWidth + tile.dockGap
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? pills.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded
        Accessible.role: Accessible.Button
        Accessible.name: tile.Accessible.name

        CompactDocker {
            id: tile
            visible: !compact.vertical
            anchors.centerIn: parent
            height: Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            snapshot: root.snapshot
            serviceUp: root.serviceUp
            active: compact.containsMouse || root.expanded
        }

        Rectangle {
            visible: compact.vertical
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing / 2
            radius: Tide.Tokens.radiusSmall
            color: Tide.Tokens.textPrimary
            opacity: compact.containsMouse || root.expanded ? 0.08 : 0
            Behavior on opacity { NumberAnimation { duration: Kirigami.Units.shortDuration } }
        }

        // Pionowy panel: same liczniki.
        Column {
            id: pills
            anchors.centerIn: parent
            visible: compact.vertical
            spacing: Kirigami.Units.smallSpacing
            opacity: root.dockerUp ? 1 : 0.45
            Tide.CountPill {
                tint: Tide.Tokens.working
                count: Logic.summary(root.snapshot).running
                textColor: Kirigami.Theme.textColor
            }
            Tide.CountPill {
                visible: Logic.summary(root.snapshot).failed > 0
                tint: Tide.Tokens.negative
                count: Logic.summary(root.snapshot).failed
                emphasized: true
                textColor: Kirigami.Theme.textColor
            }
        }
    }

    fullRepresentation: Item {
        id: full

        readonly property bool sidebar: Plasmoid.configuration.sidebarMode
        readonly property real contentHeight: loader.item ? loader.item.implicitHeight : 0

        Layout.preferredWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 22
        Layout.minimumWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 18
        Layout.fillWidth: sidebar
        Layout.preferredHeight: contentHeight + (sidebar ? 0 : Kirigami.Units.largeSpacing * 2)
        Layout.minimumHeight: sidebar ? contentHeight : -1
        Layout.maximumHeight: sidebar ? contentHeight : Number.POSITIVE_INFINITY

        Loader {
            id: loader
            anchors.fill: parent
            anchors.margins: full.sidebar ? 0 : Kirigami.Units.largeSpacing
            sourceComponent: full.sidebar ? sidebarView : popupView
        }
        Component {
            id: popupView
            DockerPopup {
                snapshot: root.snapshot
                serviceUp: root.serviceUp
                stats: root.stats
                disk: root.disk
                nowTs: root.nowTs
                busy: root.busy
                notes: root.notes
                onOpenRequested: item => root.showWindow(item)
                onGroupActionRequested: (project, action) => root.groupAction(project, action)
                onStartRequested: id => root.startContainer(id)
            }
        }
        Component {
            id: sidebarView
            SidebarDocker {
                snapshot: root.snapshot
                serviceUp: root.serviceUp
                nowTs: root.nowTs
                onOpenRequested: item => root.showWindow(item)
            }
        }
    }
}

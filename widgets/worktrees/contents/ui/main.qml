// Widżet worktrees: kafelek z liczbą worktrees i liczbą do sprzątnięcia, popup z listą
// pogrupowaną według repozytoriów i sprzątaniem scalonych (z potwierdzeniem).
// Dane: tide-daemon przez D-Bus (org.tide.Daemon /Worktrees, opis w docs/tide-daemon.md).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "tide" as Tide

PlasmoidItem {
    id: root

    readonly property var worktrees: Array.isArray(source.result) ? source.result : []
    // Liczymy worktrees dodatkowe — główne katalogi repozytoriów to po prostu repozytoria.
    readonly property var linked: worktrees.filter(w => !w.isMain)
    readonly property var cleanable: worktrees.filter(w => w.removable)
    readonly property real cleanableBytes: cleanable.reduce((sum, w) => sum + Math.max(0, w.size), 0)
    property var launchers: ({ terminal: true, rider: false })
    property real nowTs: Date.now() / 1000
    property bool launchersLoaded: false

    // Sprzątanie: "" → "confirm" → "running" → "done".
    property string cleanup: ""
    property var cleanupResults: []

    function startCleanup() {
        const paths = cleanable.map(w => w.path)
        cleanup = "running"
        source.call("Remove", [paths], function (reply) {
            if (reply === undefined) {
                cleanupResults = [{ path: "", removed: false, message: "Brak odpowiedzi usługi — lista odświeży się sama." }]
            } else {
                try { cleanupResults = JSON.parse(reply) } catch (e) { cleanupResults = [] }
            }
            cleanup = "done"
            source.refresh()
        })
    }

    function open(path, app) {
        source.call("Open", [path, app], function (ok) {
            if (ok) root.expanded = false
        })
    }

    function loadLaunchers() {
        source.call("Launchers", [], function (reply) {
            if (reply === undefined) return
            try { root.launchers = JSON.parse(reply) } catch (e) {}
        })
    }

    Plasmoid.title: "Worktrees"
    Plasmoid.icon: "vcs-branch"
    toolTipMainText: "Worktrees"
    toolTipSubText: source.available
        ? "Worktrees: " + linked.length + " · do sprzątnięcia: " + cleanable.length
          + (cleanable.length > 0 ? " (" + Tide.Theme.formatBytes(cleanableBytes) + ")" : "")
        : "Usługa tide-daemon nie działa"
    preferredRepresentation: compactRepresentation

    onExpandedChanged: if (expanded) {
        source.call("Refresh", [], null)
        if (cleanup === "done") cleanup = ""
    }

    Tide.DaemonSource {
        id: source
        path: "/Worktrees"
        iface: "org.tide.Daemon.Worktrees"
        method: "List"
        pollInterval: 60000
        onRefreshed: if (!root.launchersLoaded) { root.launchersLoaded = true; root.loadLaunchers() }
    }

    Timer {
        interval: 60000
        running: true
        repeat: true
        onTriggered: root.nowTs = Date.now() / 1000
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? -1 : counters.implicitWidth + Kirigami.Units.smallSpacing * 2
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? counters.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded

        Rectangle {
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing / 2
            radius: Tide.Tokens.radiusSmall
            color: Tide.Tokens.textPrimary
            opacity: compact.containsMouse || root.expanded ? 0.08 : 0
            Behavior on opacity { NumberAnimation { duration: Kirigami.Units.shortDuration } }
        }

        Grid {
            id: counters
            anchors.centerIn: parent
            columns: compact.vertical ? 1 : 2
            spacing: Kirigami.Units.smallSpacing
            opacity: source.available ? 1 : 0.45

            Tide.CountPill {
                tint: Tide.Tokens.lavender
                count: root.linked.length
                textColor: Kirigami.Theme.textColor
            }
            Tide.CountPill {
                tint: Tide.Tokens.positive
                count: root.cleanable.length
                emphasized: root.cleanable.length > 0
                textColor: Kirigami.Theme.textColor
            }
        }
    }

    fullRepresentation: WorktreesView {
        worktrees: root.worktrees
        available: source.available
        launchers: root.launchers
        nowTs: root.nowTs
        cleanup: root.cleanup
        cleanupResults: root.cleanupResults
        onCleanupChangeRequested: state => root.cleanup = state
        onRemoveRequested: root.startCleanup()
        onOpenRequested: (path, app) => root.open(path, app)
    }
}

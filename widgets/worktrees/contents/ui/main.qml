// Widżet worktrees: kafelek z podsumowaniem („15 do sprzątnięcia · 16 GB”, ile w pracy; CompactWorktrees).
// Kliknięcie otwiera okno Tide na widoku Worktrees (tide-daemon: ShowWindow) — tam jest lista i sprzątanie.
// Mały popup pojawia się tylko wtedy, gdy okna nie da się otworzyć (nie zainstalowane, usługa stoi).
// Dane: tide-daemon przez D-Bus (org.tide.Daemon /Worktrees, opis w docs/tide-daemon.md).
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "tide" as Tide

PlasmoidItem {
    id: root

    readonly property var worktrees: Array.isArray(source.result) ? source.result : []
    // Liczymy worktrees dodatkowe — główne katalogi repozytoriów to po prostu repozytoria.
    readonly property var linked: worktrees.filter(w => !w.isMain)
    readonly property var cleanable: linked.filter(w => w.group === "clean")
    readonly property var active: linked.filter(w => w.group === "active")
    readonly property real cleanableBytes: cleanable.reduce((sum, w) => sum + Math.max(0, w.size), 0)
    // Powód, dla którego okno się nie otworzyło ("" gdy otwarte).
    property string windowProblem: ""

    function showWindow() {
        source.call("ShowWindow", [], function (ok) {
            root.windowProblem = ok === true ? "" : ok === false ? "not-installed" : "no-service"
            // W pasku bocznym podsumowanie jest zawsze widoczne — nie ma czego zwijać ani rozwijać.
            if (!Plasmoid.configuration.sidebarMode) {
                root.expanded = ok !== true
            }
        })
    }

    Plasmoid.title: "Worktrees"
    Plasmoid.icon: "vcs-branch"
    toolTipMainText: "Worktrees"
    toolTipSubText: Tide.Texts.worktreesToolTip(source.available, cleanable.length, cleanableBytes,
                                                active.length, linked.length)
    // Pasek boczny Tide pokazuje podsumowanie na stałe, z przyciskiem do okna (scripts/panels/sidebar.js).
    preferredRepresentation: Plasmoid.configuration.sidebarMode ? fullRepresentation : compactRepresentation

    Tide.DaemonSource {
        id: source
        path: "/Worktrees"
        iface: "org.tide.Daemon.Worktrees"
        method: "List"
        pollInterval: 60000
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? -1 : tile.implicitWidth + tile.dockGap
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? pills.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onClicked: root.showWindow()
        Accessible.role: Accessible.Button
        Accessible.name: "Worktrees: " + tile.summary + ", otwórz okno"

        // Poziomy dock: „Worktrees · 5 w pracy” i „15 do sprzątnięcia · 16 GB”.
        CompactWorktrees {
            id: tile
            visible: !compact.vertical
            anchors.centerIn: parent
            height: Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            cleanCount: root.cleanable.length
            cleanBytes: root.cleanableBytes
            activeCount: root.active.length
            available: source.available
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
            opacity: source.available ? 1 : 0.45
            Tide.CountPill {
                tint: Tide.Tokens.positive
                count: root.cleanable.length
                emphasized: root.cleanable.length > 0
                textColor: Kirigami.Theme.textColor
            }
            Tide.CountPill {
                tint: Tide.Tokens.working
                count: root.active.length
                textColor: Kirigami.Theme.textColor
            }
        }
    }

    // Mały popup (SummaryView), gdy okna nie da się otworzyć; w pasku bocznym kafelek SidebarWorktrees.
    fullRepresentation: Item {
        id: full

        readonly property bool sidebar: Plasmoid.configuration.sidebarMode
        readonly property var view: loader.item

        Layout.preferredWidth: sidebar ? -1 : (view ? view.Layout.preferredWidth : -1)
        Layout.minimumWidth: sidebar ? -1 : (view ? view.Layout.minimumWidth : -1)
        Layout.fillWidth: sidebar
        Layout.preferredHeight: view ? (sidebar ? view.implicitHeight : view.Layout.preferredHeight) : 0
        Layout.minimumHeight: sidebar ? Layout.preferredHeight : -1
        Layout.maximumHeight: sidebar ? Layout.preferredHeight : Number.POSITIVE_INFINITY

        Loader {
            id: loader
            anchors.fill: parent
            sourceComponent: full.sidebar ? sidebarView : popupView
        }
        Component {
            id: popupView
            SummaryView {
                available: source.available
                problem: root.windowProblem
                cleanCount: root.cleanable.length
                cleanBytes: root.cleanableBytes
                activeCount: root.active.length
                totalCount: root.linked.length
                onRetryRequested: root.showWindow()
            }
        }
        Component {
            id: sidebarView
            SidebarWorktrees {
                available: source.available
                problem: root.windowProblem
                cleanCount: root.cleanable.length
                cleanBytes: root.cleanableBytes
                activeCount: root.active.length
                onOpenRequested: root.showWindow()
            }
        }
    }
}

// Widżet worktrees: kafelek z podsumowaniem („16 do sprzątnięcia · 18 GB”, ile w pracy).
// Kliknięcie otwiera okno „Tide Worktrees” (tide-daemon: ShowWindow) — tam jest lista i sprzątanie.
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
            if (ok === true) {
                root.windowProblem = ""
                root.expanded = false
            } else {
                root.windowProblem = ok === false ? "not-installed" : "no-service"
                root.expanded = true
            }
        })
    }

    Plasmoid.title: "Worktrees"
    Plasmoid.icon: "vcs-branch"
    toolTipMainText: "Worktrees"
    toolTipSubText: source.available
        ? "Do sprzątnięcia: " + cleanable.length + " (" + Tide.Theme.formatBytes(cleanableBytes) + ")"
          + " · w pracy: " + active.length + " · razem: " + linked.length + "\nKliknij, aby otworzyć okno Worktrees"
        : "Usługa tide-daemon nie działa"
    preferredRepresentation: compactRepresentation

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

        Layout.minimumWidth: vertical ? -1 : lines.implicitWidth + Kirigami.Units.smallSpacing * 3
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? pills.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onClicked: root.showWindow()
        Accessible.role: Accessible.Button
        Accessible.name: "Worktrees: " + root.cleanable.length + " do sprzątnięcia, otwórz okno"

        Rectangle {
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing / 2
            radius: Tide.Tokens.radiusSmall
            color: Tide.Tokens.textPrimary
            opacity: compact.containsMouse || root.expanded ? 0.08 : 0
            Behavior on opacity { NumberAnimation { duration: Kirigami.Units.shortDuration } }
        }

        // Poziomy dock: dwie linie — „16 do sprzątnięcia” i „18 GB · 3 w pracy”.
        Column {
            id: lines
            anchors.centerIn: parent
            visible: !compact.vertical
            spacing: 1
            opacity: source.available ? 1 : 0.45

            Row {
                spacing: 6
                Tide.StateDot {
                    anchors.verticalCenter: parent.verticalCenter
                    color: root.cleanable.length > 0 ? Tide.Tokens.positive : Tide.Tokens.textMuted
                    size: 7
                }
                Text {
                    id: cleanCount
                    text: root.cleanable.length
                    color: root.cleanable.length > 0 ? Tide.Tokens.positive : Kirigami.Theme.textColor
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    font.weight: Font.DemiBold
                }
                Text {
                    text: "do sprzątnięcia"
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                    anchors.baseline: cleanCount.baseline
                }
            }
            Row {
                spacing: 6
                Text {
                    text: root.cleanable.length > 0 ? Tide.Theme.formatBytes(root.cleanableBytes) : "—"
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }
                Text {
                    text: "·"
                    color: Tide.Tokens.textMuted
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }
                Tide.StateDot {
                    anchors.verticalCenter: parent.verticalCenter
                    sessionState: root.active.some(w => w.hasAgent) ? "working" : ""
                    color: Tide.Tokens.working
                    size: 6
                }
                Text {
                    text: root.active.length + " w pracy"
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt - 1
                }
            }
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

    fullRepresentation: SummaryView {
        available: source.available
        problem: root.windowProblem
        cleanCount: root.cleanable.length
        cleanBytes: root.cleanableBytes
        activeCount: root.active.length
        totalCount: root.linked.length
        onRetryRequested: root.showWindow()
    }
}

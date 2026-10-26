// Widżet „Nowe zadanie”: kafelek „Zadanie · + nowe dla agenta” (CompactNewTask). Kliknięcie otwiera
// okno „Tide: nowe zadanie” przez tide-daemon (/Tasks ShowDialog) — tam repozytorium, narzędzie,
// polecenie, zrzuty i nowy worktree. Mały popup pojawia się tylko wtedy, gdy okna nie da się otworzyć.
// W pasku bocznym (sidebarMode) zwarty kafelek SidebarNewTask.
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "tide" as Tide

PlasmoidItem {
    id: root

    // "" — otwarte, "not-installed" — okna nie ma, "no-service" — usługa nie odpowiada.
    property string problem: ""
    readonly property var defaults: source.result && typeof source.result === "object" ? source.result : ({})

    function openDialog() {
        source.callOn("/Tasks", "org.tide.Daemon.Tasks", "ShowDialog", [], function (ok) {
            root.problem = ok === true ? "" : ok === false ? "not-installed" : "no-service"
            if (!Plasmoid.configuration.sidebarMode) {
                root.expanded = ok !== true
            }
        })
    }

    Plasmoid.title: "Nowe zadanie"
    Plasmoid.icon: "list-add"
    toolTipMainText: "Nowe zadanie dla agenta"
    toolTipSubText: !source.available ? "Usługa tide-daemon nie działa"
        : "Repozytorium, Claude Code albo Codex, polecenie i zrzuty ekranu; agent startuje w Konsoli"
          + "\nKliknij albo naciśnij Meta+Alt+A"
    preferredRepresentation: Plasmoid.configuration.sidebarMode ? fullRepresentation : compactRepresentation

    // Defaults() mówi, czy obszar /Tasks odpowiada (i które narzędzia są dostępne).
    Tide.DaemonSource {
        id: source
        path: "/Tasks"
        iface: "org.tide.Daemon.Tasks"
        method: "Defaults"
        pollInterval: 120000
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? -1 : tile.implicitWidth + tile.dockGap
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? plus.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onClicked: root.openDialog()
        Accessible.role: Accessible.Button
        Accessible.name: "Nowe zadanie dla agenta"

        CompactNewTask {
            id: tile
            visible: !compact.vertical
            anchors.centerIn: parent
            height: Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            available: source.available
            active: compact.containsMouse || root.expanded
        }

        // Pionowy panel: sam plus.
        Rectangle {
            visible: compact.vertical
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing / 2
            radius: Tide.Tokens.radiusSmall
            color: Tide.Tokens.textPrimary
            opacity: compact.containsMouse || root.expanded ? 0.08 : 0
        }
        Text {
            id: plus
            visible: compact.vertical
            anchors.centerIn: parent
            text: "+"
            color: Tide.Tokens.accent
            font.family: Tide.Theme.monoFamily
            font.pointSize: Tide.Tokens.fontSizePt + 4
            font.weight: Font.DemiBold
        }
    }

    fullRepresentation: Item {
        id: full

        readonly property bool sidebar: Plasmoid.configuration.sidebarMode

        Layout.preferredWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 18
        Layout.fillWidth: sidebar
        Layout.preferredHeight: sidebar ? sideTile.implicitHeight : popup.implicitHeight + Kirigami.Units.largeSpacing * 2
        Layout.minimumHeight: Layout.preferredHeight
        Layout.maximumHeight: Layout.preferredHeight

        SidebarNewTask {
            id: sideTile
            visible: full.sidebar
            anchors.left: parent.left
            anchors.right: parent.right
            available: source.available
            problem: root.problem
            onOpenRequested: root.openDialog()
        }

        // Popup kafelka: tylko gdy okna nie udało się otworzyć.
        ColumnLayout {
            id: popup
            visible: !full.sidebar
            anchors.fill: parent
            anchors.margins: Kirigami.Units.largeSpacing
            spacing: Kirigami.Units.smallSpacing * 2
            Text {
                text: "Nowe zadanie dla agenta"
                color: Kirigami.Theme.textColor
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 15
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                wrapMode: Text.WordWrap
                text: root.problem === "not-installed"
                    ? "Okno nie jest zainstalowane. Zainstaluj je: make install-new-task"
                    : "Usługa tide-daemon nie odpowiada.\nUruchom: systemctl --user start tide-daemon"
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pointSize: Tide.Tokens.fontSizePt
            }
            Tide.TextButton {
                text: "Spróbuj ponownie"
                onClicked: root.openDialog()
            }
        }
    }
}

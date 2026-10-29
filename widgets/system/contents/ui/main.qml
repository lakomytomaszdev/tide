// Widżet System: kafelek z miernikami procesora, pamięci i dysku (CompactSystem), popup ze szczegółami
// (FullSystem), a w pasku bocznym zwarty kafelek (SidebarSystem).
// Dane: czujniki ksystemstats i pomocnik ../code/sysinfo.py (opis w SystemSource.qml).
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "../code/system.js" as Logic
import "tide" as Tide

PlasmoidItem {
    id: root

    function diskLine() {
        const disks = systemSource.disks
        return disks.length ? "Dysk " + disks.map(d => d.target + " " + Logic.percentText(d.usedPercent)).join(" · ") : ""
    }

    Plasmoid.title: "System"
    Plasmoid.icon: "utilities-system-monitor"
    toolTipMainText: "System"
    toolTipSubText: Logic.toolTip(systemSource.problem === "missing", systemSource.cpuPercent,
                                  systemSource.memoryPercent, systemSource.fullerDisk) + "\n" + diskLine()
    // Pasek boczny Tide pokazuje widok szczegółowy na stałe (scripts/panels/sidebar.js).
    preferredRepresentation: Plasmoid.configuration.sidebarMode ? fullRepresentation : compactRepresentation

    // W aktywności Stream znika tylko kafelek z paska bocznego (pasek pracy); w docku System zostaje
    // (decyzja 80: dock DP-2 w Stream to System i zadania).
    Plasmoid.status: activityLook.workHidden && Plasmoid.configuration.sidebarMode ? PlasmaCore.Types.HiddenStatus
                                                                                  : PlasmaCore.Types.ActiveStatus
    Tide.ActivityLook { id: activityLook }

    SystemSource {
        id: systemSource
        detailed: root.expanded || (root.fullRepresentationItem ? root.fullRepresentationItem.shown === true : false)
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
        Layout.minimumWidth: vertical ? -1 : rings.implicitWidth + rings.dockGap
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? rings.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded
        Accessible.role: Accessible.Button
        Accessible.name: rings.Accessible.name

        // „System”, pod spodem „CPU 34%  RAM 61%  Dysk 88%” z paskami.
        CompactSystem {
            id: rings
            anchors.centerIn: parent
            height: compact.vertical ? implicitHeight : Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            width: compact.vertical ? Math.min(parent.width, implicitWidth) : implicitWidth
            source: systemSource
            vertical: compact.vertical
            active: compact.containsMouse || root.expanded
        }
    }

    // Popup kafelka w docku: FullSystem. W pasku bocznym (sidebarMode) zwarty kafelek SidebarSystem.
    fullRepresentation: Item {
        id: full

        readonly property bool sidebar: Plasmoid.configuration.sidebarMode
        // Dla SystemSource.detailed: czujniki karty graficznej tylko wtedy, gdy widok jest na ekranie.
        readonly property bool shown: loader.item ? loader.item.shown === true : false
        readonly property real contentHeight: loader.item ? loader.item.implicitHeight : 0

        Layout.preferredWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 22
        Layout.minimumWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 17
        Layout.fillWidth: sidebar
        Layout.preferredHeight: contentHeight
        Layout.minimumHeight: contentHeight
        Layout.maximumHeight: sidebar ? contentHeight : Number.POSITIVE_INFINITY

        Loader {
            id: loader
            anchors.fill: parent
            sourceComponent: full.sidebar ? sidebarView : popupView
        }
        Component {
            id: popupView
            FullSystem { source: systemSource }
        }
        Component {
            id: sidebarView
            SidebarSystem { source: systemSource }
        }
    }
}

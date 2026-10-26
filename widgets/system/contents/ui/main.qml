// Widżet System: kafelek z miernikami procesora, pamięci i dysku (CompactSystem), popup ze szczegółami.
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
    preferredRepresentation: compactRepresentation

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

    fullRepresentation: FullSystem {
        source: systemSource
        Layout.preferredWidth: Kirigami.Units.gridUnit * 22
        Layout.minimumWidth: Kirigami.Units.gridUnit * 17
        Layout.preferredHeight: implicitHeight
        Layout.minimumHeight: implicitHeight
    }
}

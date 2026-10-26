// Widżet System: kafelek z pierścieniami procesora, pamięci i dysku, popup ze szczegółami.
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
    toolTipSubText: systemSource.problem === "missing"
        ? "Zainstaluj pakiet ksystemstats\n" + diskLine()
        : "Procesor " + Logic.percentText(systemSource.cpuPercent)
          + " · Pamięć " + Logic.percentText(systemSource.memoryPercent) + "\n" + diskLine()
    preferredRepresentation: compactRepresentation

    SystemSource {
        id: systemSource
        detailed: root.expanded || (root.fullRepresentationItem ? root.fullRepresentationItem.shown === true : false)
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical
        // Dock ma ok. 64 px wysokości: pierścień z podpisem mieści się z marginesem.
        readonly property real ringSize: vertical
            ? Math.min(Kirigami.Units.gridUnit * 2.2, width * 0.7)
            : Math.max(Kirigami.Units.iconSizes.small, Math.min(Kirigami.Units.gridUnit * 2.4, height * 0.62))

        Layout.minimumWidth: vertical ? -1 : rings.implicitWidth + Kirigami.Units.smallSpacing * 4
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? rings.implicitHeight + Kirigami.Units.smallSpacing * 4 : -1
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

        CompactSystem {
            id: rings
            anchors.centerIn: parent
            source: systemSource
            vertical: compact.vertical
            ringSize: compact.ringSize
            textColor: Kirigami.Theme.textColor
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

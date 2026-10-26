// Widżet Limity AI: kafelek z pozostałym procentem ChatGPT i Claude (CompactLimits), popup ze szczegółami.
// Dane: usługa QuotaApp (lokalne API, opis w LimitsSource.qml). Port i token są w
// ../code/Config.js, generowanym przy instalacji z ~/.config/limity-ai-widget/config.json.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents3
import "../code/Config.js" as Config
import "tide" as Tide

PlasmoidItem {
    id: root

    readonly property var services: [
        { key: "chatgpt", name: "ChatGPT", sessionLabel: "Sesja", hideEmptySession: true },
        { key: "claude", name: "Claude", sessionLabel: "Sesja (5 h)", hideEmptySession: false }
    ]

    function percent(value) {
        return value === null ? "—" : value + "%"
    }

    function tooltipLine(service) {
        return service.name + ": sesja " + percent(limitsSource.remaining(service.key, "session"))
            + " · tydzień " + percent(limitsSource.remaining(service.key, "weekly"))
    }

    Plasmoid.title: "Limity AI"
    Plasmoid.icon: "view-statistics"
    toolTipMainText: "Limity AI"
    toolTipSubText: Tide.Texts.limitsToolTip(limitsSource.available, limitsSource.panelValue("chatgpt"),
                                             limitsSource.panelValue("claude"))
        + (limitsSource.available ? "\n" + services.map(tooltipLine).join("\n") : "")
    // Pasek boczny Tide pokazuje widok szczegółowy na stałe (scripts/panels/sidebar.js).
    preferredRepresentation: Plasmoid.configuration.sidebarMode ? fullRepresentation : compactRepresentation

    // Po otwarciu popupu od razu świeży stan, bez czekania na kolejny cykl.
    onExpandedChanged: if (expanded) limitsSource.updateStatus()

    LimitsSource {
        id: limitsSource
        port: Config.port
        token: Config.token
        pollInterval: 10000
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? -1 : limits.implicitWidth + limits.dockGap
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? limits.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded
        Accessible.role: Accessible.Button
        Accessible.name: limits.Accessible.name

        CompactLimits {
            id: limits
            anchors.centerIn: parent
            height: compact.vertical ? implicitHeight : Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            source: limitsSource
            vertical: compact.vertical
            active: compact.containsMouse || root.expanded
            opacity: limitsSource.available ? 1 : 0.45
        }
    }

    fullRepresentation: Item {
        Layout.preferredWidth: Kirigami.Units.gridUnit * 21
        Layout.minimumWidth: Kirigami.Units.gridUnit * 17
        Layout.preferredHeight: body.implicitHeight + Kirigami.Units.smallSpacing * 2
        Layout.minimumHeight: Layout.preferredHeight

        ColumnLayout {
            id: body
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing
            spacing: Kirigami.Units.largeSpacing

            RowLayout {
                Layout.fillWidth: true
                spacing: Kirigami.Units.smallSpacing

                Text {
                    Layout.fillWidth: true
                    text: "Limity AI"
                    color: Kirigami.Theme.textColor
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt + 3
                    font.weight: Font.DemiBold
                }
                PlasmaComponents3.BusyIndicator {
                    visible: limitsSource.busy
                    running: visible
                    Layout.preferredWidth: Kirigami.Units.iconSizes.smallMedium
                    Layout.preferredHeight: Kirigami.Units.iconSizes.smallMedium
                }
                PlasmaComponents3.ToolButton {
                    icon.name: "view-refresh"
                    text: "Odśwież"
                    display: QQC2.AbstractButton.IconOnly
                    enabled: limitsSource.available && !limitsSource.busy
                    onClicked: limitsSource.refresh()
                    PlasmaComponents3.ToolTip.text: text
                    PlasmaComponents3.ToolTip.visible: hovered
                    PlasmaComponents3.ToolTip.delay: Kirigami.Units.toolTipDelay
                }
            }

            StatusNotice {
                Layout.fillWidth: true
                source: limitsSource
            }

            Repeater {
                model: root.services
                delegate: ServiceCard {
                    required property var modelData
                    Layout.fillWidth: true
                    source: limitsSource
                    serviceKey: modelData.key
                    title: modelData.name
                    sessionLabel: modelData.sessionLabel
                    hideEmptySession: modelData.hideEmptySession
                    dense: Plasmoid.configuration.sidebarMode
                }
            }

            Item { Layout.fillHeight: true }
        }
    }
}

// Widżet „AI” (org.tide.limits; do PT-41 „Limity AI”): kafelek z pozostałym procentem ChatGPT i Claude
// i kropką modelu lokalnego (CompactLimits), popup z kartami usług i sekcją „Modele lokalne”
// (LocalModelsCard), w pasku bocznym zwarty kafelek (SidebarLimits).
// Dane: usługa QuotaApp (lokalne API, opis w LimitsSource.qml). Port i token są w
// ../code/Config.js, generowanym przy instalacji z ~/.config/limity-ai-widget/config.json.
// Prognoza wyczerpania pochodzi z tide-daemon (org.tide.Daemon /Limits, Forecast() i sygnał
// Changed) — bez usługi widżet działa jak dotąd, tylko bez prognoz. Model lokalny też z tide-daemon
// (/Models, Status() po sygnale Changed; stan i słowa z biblioteki: tide/ModelsState.qml, tide/models.js,
// te same co w kafelku Modele) — bez usługi kafelek po prostu nie ma kropki. „Otwórz Modele” otwiera widok
// Modele w oknie Tide przez org.tide.Window Show("models", "") (tide/TideWindow.qml).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import org.kde.plasma.components as PlasmaComponents3
import "../code/Config.js" as Config
import "tide/models.js" as ModelsLogic
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

    function tooltipPart(service, metric, word) {
        const forecast = Tide.Texts.forecastText(limitsSource.forecastFor(service.key, metric))
        return word + " " + percent(limitsSource.remaining(service.key, metric)) + (forecast ? " (" + forecast + ")" : "")
    }

    function tooltipLine(service) {
        return service.name + ": " + tooltipPart(service, "session", "sesja") + " · " + tooltipPart(service, "weekly", "tydzień")
    }

    readonly property var localInfo: ModelsLogic.indicator(modelsState.snapshot, modelsState.serviceUp)
    property string windowNote: ""

    Plasmoid.title: "AI"
    Plasmoid.icon: "view-statistics"
    toolTipMainText: "AI"
    toolTipSubText: Tide.Texts.limitsToolTip(limitsSource.available, limitsSource.panelValue("chatgpt"),
                                             limitsSource.panelValue("claude"))
        + (limitsSource.available ? "\n" + services.map(tooltipLine).join("\n") : "")
        + (localInfo.visible
           ? "\n" + ModelsLogic.toolTipHead(modelsState.snapshot, modelsState.serviceUp, modelsState.nowMs) : "")
    // Pasek boczny Tide pokazuje widok szczegółowy na stałe (scripts/panels/sidebar.js).
    preferredRepresentation: Plasmoid.configuration.sidebarMode ? fullRepresentation : compactRepresentation

    // Po otwarciu popupu od razu świeży stan, bez czekania na kolejny cykl.
    onExpandedChanged: if (expanded) {
        limitsSource.updateStatus()
        modelsState.touch()
    }

    LimitsSource {
        id: limitsSource
        port: Config.port
        token: Config.token
        pollInterval: 10000
        forecast: forecastSource.available ? forecastSource.result : null
    }

    // Prognoza z historii odczytów (tide-daemon). Usługa sama daje znać sygnałem Changed,
    // więc rzadkie odpytywanie jest tylko zabezpieczeniem.
    Plasmoid.status: activityLook.workHidden ? PlasmaCore.Types.HiddenStatus : PlasmaCore.Types.ActiveStatus
    // W aktywności Stream kafelek pracy znika z panelu (widgets/lib/ActivityLook.qml, decyzja 80).
    Tide.ActivityLook { id: activityLook }

    Tide.DaemonSource {
        id: forecastSource
        path: "/Limits"
        iface: "org.tide.Daemon.Limits"
        method: "Forecast"
        pollInterval: 120000
    }

    // Modele lokalne (tide-daemon /Models): Status() po sygnale Changed, a na wszelki wypadek co minutę —
    // w pasku bocznym co 5 min (pasek wisi cały dzień, zmiany i tak przychodzą sygnałem).
    Tide.DaemonSource {
        id: modelsSource
        path: "/Models"
        iface: "org.tide.Daemon.Models"
        method: "Status"
        pollInterval: Plasmoid.configuration.sidebarMode ? 300000 : 60000
    }

    Tide.ModelsState {
        id: modelsState
        source: modelsSource
    }

    // „zwolni pamięć za 4 min” i „od 2 min” w kolejce liczone na bieżąco przy otwartym popupie.
    Timer {
        interval: 30000
        repeat: true
        running: root.expanded
        onTriggered: modelsState.touch()
    }

    Timer {
        id: noteTimer
        interval: 30000
        onTriggered: root.windowNote = ""
    }

    Tide.TideWindow {
        id: tideWindow
        onOpened: {
            root.windowNote = ""
            root.expanded = false
        }
        onFailed: kind => {
            root.windowNote = ModelsLogic.windowNote(kind)
            noteTimer.restart()
        }
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? -1 : limits.implicitWidth + limits.dockGap
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? limits.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onContainsMouseChanged: if (containsMouse) modelsState.touch()
        onClicked: root.expanded = !root.expanded
        Accessible.role: Accessible.Button
        Accessible.name: limits.Accessible.name

        CompactLimits {
            id: limits
            anchors.centerIn: parent
            height: compact.vertical ? implicitHeight : Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            source: limitsSource
            models: modelsState
            vertical: compact.vertical
            active: compact.containsMouse || root.expanded
            opacity: limitsSource.available || limits.local.visible ? 1 : 0.45
        }
    }

    // Popup kafelka w docku: karty usług i sekcja „Modele lokalne”. W pasku bocznym (sidebarMode) zwarty
    // kafelek SidebarLimits.
    fullRepresentation: Item {
        id: full

        readonly property bool sidebar: Plasmoid.configuration.sidebarMode
        readonly property real contentHeight: loader.item ? loader.item.implicitHeight : 0

        Layout.preferredWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 21
        Layout.minimumWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 17
        Layout.fillWidth: sidebar
        Layout.preferredHeight: sidebar ? contentHeight : contentHeight + Kirigami.Units.smallSpacing * 2
        Layout.minimumHeight: Layout.preferredHeight
        Layout.maximumHeight: sidebar ? contentHeight : Number.POSITIVE_INFINITY

        Loader {
            id: loader
            anchors.fill: parent
            anchors.margins: full.sidebar ? 0 : Kirigami.Units.smallSpacing
            sourceComponent: full.sidebar ? sidebarView : popupView
        }

        Component {
            id: sidebarView
            SidebarLimits {
                source: limitsSource
                models: modelsState
                windowNote: root.windowNote
                onOpenModelsRequested: tideWindow.open()
            }
        }

        Component {
            id: popupView
            ColumnLayout {
                id: body
                spacing: Kirigami.Units.largeSpacing

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing

                    Text {
                        Layout.fillWidth: true
                        text: "AI"
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
                        // Odświeża te usługi, które działają — przycisk działa, gdy da się odświeżyć choć jedną.
                        readonly property bool limitsReady: limitsSource.available && !limitsSource.busy
                        readonly property bool modelsReady: modelsState.serviceUp && !modelsState.refreshing
                        enabled: limitsReady || modelsReady
                        onClicked: {
                            if (limitsReady) limitsSource.refresh()
                            if (modelsReady) modelsState.refresh()
                        }
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
                    }
                }

                LocalModelsCard {
                    Layout.fillWidth: true
                    snapshot: modelsState.snapshot
                    serviceUp: modelsState.serviceUp
                    nowMs: modelsState.nowMs
                    windowNote: root.windowNote
                    onOpenRequested: tideWindow.open()
                }

                Item { Layout.fillHeight: true }
            }
        }
    }
}

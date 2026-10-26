// Widżet agentów: kafelek „Agenci · 2 pracują · 1 czeka · 2 gotowe” (CompactAgents), popup z listą
// sesji (SessionsView), a w pasku bocznym kafelek z jedną linią na sesję (SidebarAgents).
// Dane: tide-daemon przez D-Bus (org.tide.Daemon /Agents, opis w docs/tide-daemon.md).
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "tide" as Tide

PlasmoidItem {
    id: root

    readonly property var sessions: Array.isArray(source.result) ? source.result : []
    readonly property int workingCount: countOf("working")
    readonly property int waitingCount: countOf("waiting")
    readonly property int doneCount: countOf("done")
    property bool showEnded: false
    property real nowTs: Date.now() / 1000
    // Okienko z pełną listą otwarte z paska bocznego („+N więcej”).
    property bool morePopupOpen: false

    // Stan liczony przez Theme.sessionState: starsza usługa zgłaszała koniec tury jako „czeka”.
    function countOf(state) {
        return sessions.filter(s => Tide.Theme.sessionState(s) === state).length
    }

    // Okno „Tide: nowe zadanie” otwiera usługa (/Tasks ShowDialog); drugie otwarcie tylko je pokazuje.
    function newTask() {
        source.callOn("/Tasks", "org.tide.Daemon.Tasks", "ShowDialog", [], function (ok) {
            if (ok === true) root.expanded = false
        })
    }

    // Okno „Tide Sesje” (tablica koordynatora) otwiera usługa (/Coordination ShowWindow).
    function showSessions(coordinatorId) {
        source.callOn("/Coordination", "org.tide.Daemon.Coordination", "ShowWindow", [coordinatorId || ""],
                      function (ok) {
            if (ok === true) root.expanded = false
        })
    }

    function activate(sessionId) {
        source.call("ActivateSession", [sessionId], function (ok) {
            if (ok) root.expanded = false
        })
    }

    Plasmoid.title: "Sesje agentów"
    Plasmoid.icon: "utilities-terminal"
    // Uwagi wymaga tylko „czeka” — skończone sesje niczego nie blokują.
    // W pasku bocznym (sidebarMode) bez NeedsAttention: chowany panel nie chowa się, dopóki
    // któryś aplet wymaga uwagi, więc czekający agent trzymałby pasek na ekranie. Otwarte
    // okienko z pełną listą trzyma pasek celowo, tak jak popup apletu (CompactApplet.qml).
    Plasmoid.status: waitingCount > 0 && !Plasmoid.configuration.sidebarMode
        ? PlasmaCore.Types.NeedsAttentionStatus
        : morePopupOpen ? PlasmaCore.Types.RequiresAttentionStatus : PlasmaCore.Types.ActiveStatus
    toolTipMainText: "Sesje agentów"
    toolTipSubText: Tide.Texts.agentsToolTip(source.available, workingCount, waitingCount, doneCount)
    // Pasek boczny Tide pokazuje widok szczegółowy na stałe (scripts/panels/sidebar.js).
    preferredRepresentation: Plasmoid.configuration.sidebarMode ? fullRepresentation : compactRepresentation

    Tide.DaemonSource {
        id: source
        path: "/Agents"
        iface: "org.tide.Daemon.Agents"
        method: "Sessions"
    }

    // Czas „od ostatniej aktywności” liczony na bieżąco, bez pytania usługi.
    Timer {
        interval: 15000
        running: true
        repeat: true
        onTriggered: root.nowTs = Date.now() / 1000
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? -1 : tile.implicitWidth + tile.dockGap
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? counters.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded
        Accessible.role: Accessible.Button
        Accessible.name: "Agenci: " + tile.summary

        // Poziomy dock: kafelek z etykietą i słowami.
        CompactAgents {
            id: tile
            visible: !compact.vertical
            anchors.centerIn: parent
            height: Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            workingCount: root.workingCount
            waitingCount: root.waitingCount
            doneCount: root.doneCount
            available: source.available
            active: compact.containsMouse || root.expanded
            onNewTaskRequested: root.newTask()
        }

        // Pionowy panel: same liczniki z kropkami.
        Rectangle {
            visible: compact.vertical
            anchors.fill: parent
            anchors.margins: Kirigami.Units.smallSpacing / 2
            radius: Tide.Tokens.radiusSmall
            color: Tide.Tokens.textPrimary
            opacity: compact.containsMouse || root.expanded ? 0.08 : 0
            Behavior on opacity { NumberAnimation { duration: Kirigami.Units.shortDuration } }
        }

        Grid {
            id: counters
            visible: compact.vertical
            anchors.centerIn: parent
            columns: 1
            spacing: Kirigami.Units.smallSpacing
            opacity: source.available ? 1 : 0.45

            Tide.CountPill {
                sessionState: "working"
                count: root.workingCount
                textColor: Kirigami.Theme.textColor
            }
            Tide.CountPill {
                sessionState: "waiting"
                count: root.waitingCount
                emphasized: root.waitingCount > 0
                textColor: Kirigami.Theme.textColor
            }
            Tide.CountPill {
                visible: root.doneCount > 0
                sessionState: "done"
                count: root.doneCount
                textColor: Kirigami.Theme.textColor
            }
        }
    }

    // Popup kafelka w docku: pełna lista sesji. W pasku bocznym (sidebarMode) kafelek z jedną
    // linią na sesję (SidebarAgents) — cała lista otwiera się w okienku obok paska („+N więcej”).
    fullRepresentation: Item {
        id: full

        readonly property bool sidebar: Plasmoid.configuration.sidebarMode
        readonly property real sidebarHeight: loader.item ? loader.item.implicitHeight : 0

        Layout.preferredWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 26
        Layout.preferredHeight: sidebar ? sidebarHeight : Kirigami.Units.gridUnit * 28
        Layout.minimumWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 18
        Layout.minimumHeight: sidebar ? sidebarHeight : Kirigami.Units.gridUnit * 12
        Layout.maximumHeight: sidebar ? sidebarHeight : Number.POSITIVE_INFINITY
        Layout.fillWidth: sidebar

        Loader {
            id: loader
            anchors.fill: parent
            anchors.margins: full.sidebar ? 0 : Kirigami.Units.smallSpacing
            sourceComponent: full.sidebar ? sidebarView : popupView
        }

        Component {
            id: popupView
            SessionsView {
                sessions: root.sessions
                available: source.available
                showEnded: root.showEnded
                nowTs: root.nowTs
                onActivateRequested: id => root.activate(id)
                onShowEndedToggled: root.showEnded = !root.showEnded
                onNewTaskRequested: root.newTask()
                onSessionsRequested: id => root.showSessions(id)
            }
        }

        Component {
            id: sidebarView
            SidebarAgents {
                id: side
                sessions: root.sessions
                available: source.available
                showEnded: root.showEnded
                nowTs: root.nowTs
                onActivateRequested: id => root.activate(id)
                onShowEndedToggled: root.showEnded = !root.showEnded
                onMoreRequested: morePopup.visible = !morePopup.visible
                onNewTaskRequested: root.newTask()
                onSessionsRequested: id => root.showSessions(id)

                // Pełna lista sesji w okienku obok paska (pasek jest przy lewej krawędzi,
                // więc okienko otwiera się na prawo od „+N więcej”).
                PlasmaCore.Dialog {
                    id: morePopup
                    visualParent: side.moreButton
                    location: Plasmoid.location
                    type: PlasmaCore.Dialog.PopupMenu
                    hideOnWindowDeactivate: true
                    onVisibleChanged: root.morePopupOpen = visible
                    mainItem: SessionsView {
                        width: Kirigami.Units.gridUnit * 26
                        height: Kirigami.Units.gridUnit * 28
                        sessions: root.sessions
                        available: source.available
                        showEnded: root.showEnded
                        nowTs: root.nowTs
                        onActivateRequested: id => {
                            morePopup.visible = false
                            root.activate(id)
                        }
                        onShowEndedToggled: root.showEnded = !root.showEnded
                        onNewTaskRequested: {
                            morePopup.visible = false
                            root.newTask()
                        }
                        onSessionsRequested: id => {
                            morePopup.visible = false
                            root.showSessions(id)
                        }
                    }
                }
            }
        }
    }
}

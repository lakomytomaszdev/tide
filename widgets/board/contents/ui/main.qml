// Widżet tablicy: kafelek „Tablica · 11 kart” z „● 2 w toku · ● 1 czeka · ● 1 do sprawdzenia”
// (CompactBoard), popup z tym, co czeka, jest w toku i do sprawdzenia, oraz szybkim dopisaniem do
// backlogu (BoardPopup), a w pasku bocznym zwarty kafelek (SidebarBoard). Kliknięcie wiersza albo
// „Otwórz w Tide ↗” otwiera okno Tide na widoku Tablica (tide-daemon /Board ShowWindow(id)), a „Uruchom”
// przy karcie do zrobienia — od razu z oknem „Co ma zrobić agent?” (ShowWindow("launch:K-12")).
// Dane: tide-daemon przez D-Bus (org.tide.Daemon /Board Snapshot(), opis w docs/tide-daemon.md).
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "tide" as Tide
import "boardtexts.js" as Texts

PlasmoidItem {
    id: root

    readonly property var snapshot: source.result && typeof source.result === "object" ? source.result : null
    readonly property bool exists: !!snapshot && snapshot.exists === true
    readonly property var counts: Texts.counts(snapshot)
    property string windowProblem: ""
    property string status: ""
    property bool statusError: false
    property bool busy: false
    signal cardAdded()                   // popup czyści pole szybkiego dopisania

    // Okno Tide na widoku Tablica (z kartą albo bez); popup zamyka się, gdy okno się otworzyło.
    function showWindow(cardId) {
        source.call("ShowWindow", [cardId || ""], function (ok) {
            root.windowProblem = ok === true ? "" : ok === false ? "not-installed" : "no-service"
            if (ok === true && !Plasmoid.configuration.sidebarMode) root.expanded = false
        })
    }

    // „Przejdź” przy czekającej karcie: okno agenta (/Agents ActivateSession), jak „Przejdź do agenta”
    // w oknie Tide; gdy sesji nie da się aktywować — karta w oknie Tide.
    function activateAgent(sessionId, cardId) {
        source.callOn("/Agents", "org.tide.Daemon.Agents", "ActivateSession", [sessionId], function (ok) {
            if (ok === true) {
                if (!Plasmoid.configuration.sidebarMode) root.expanded = false
            } else {
                root.showWindow(cardId)
            }
        })
    }

    // Szybkie dopisanie: karta trafia na górę Backlogu wybranego projektu.
    function addCard(title, project) {
        const request = Object.assign({ title: title, column: "backlog" }, Texts.projectRequest(project))
        busy = true
        source.call("CreateCard", [JSON.stringify(request)], function (value) {
            root.busy = false
            const error = value === undefined ? Texts.replyError("") : Texts.replyError(value)
            root.statusError = error !== ""
            if (error) {
                root.status = error
                return
            }
            let id = ""
            try { id = JSON.parse(value).id || "" } catch (e) {}
            root.status = "Dopisano " + id + " do backlogu"
            root.cardAdded()
            statusTimer.restart()
            source.refresh()
        })
    }

    Plasmoid.title: "Tablica"
    Plasmoid.icon: "view-list-details"
    toolTipMainText: "Tablica"
    toolTipSubText: Texts.toolTip(source.available, root.exists, root.counts)
    // Pasek boczny Tide pokazuje zwarty kafelek na stałe (scripts/panels/sidebar.js).
    preferredRepresentation: Plasmoid.configuration.sidebarMode ? fullRepresentation : compactRepresentation
    onExpandedChanged: if (expanded) { status = ""; source.refresh() }

    Plasmoid.status: activityLook.workHidden ? PlasmaCore.Types.HiddenStatus : PlasmaCore.Types.ActiveStatus
    // W aktywności Stream kafelek pracy znika z panelu (widgets/lib/ActivityLook.qml, decyzja 80).
    Tide.ActivityLook { id: activityLook }

    Tide.DaemonSource {
        id: source
        path: "/Board"
        iface: "org.tide.Daemon.Board"
        method: "Snapshot"
        pollInterval: 60000
    }

    Timer {
        id: statusTimer
        interval: 4000
        onTriggered: if (!root.statusError) root.status = ""
    }

    compactRepresentation: MouseArea {
        id: compact

        readonly property bool vertical: Plasmoid.formFactor === PlasmaCore.Types.Vertical

        Layout.minimumWidth: vertical ? -1 : tile.implicitWidth + tile.dockGap
        Layout.preferredWidth: Layout.minimumWidth
        Layout.minimumHeight: vertical ? pills.implicitHeight + Kirigami.Units.smallSpacing * 2 : -1
        hoverEnabled: true
        onClicked: root.expanded = !root.expanded
        Accessible.role: Accessible.Button
        Accessible.name: "Tablica: " + tile.summary

        CompactBoard {
            id: tile
            visible: !compact.vertical
            anchors.centerIn: parent
            height: Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            counts: root.counts
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

        // Pionowy panel: same liczniki (w toku, czeka).
        Column {
            id: pills
            anchors.centerIn: parent
            visible: compact.vertical
            spacing: Kirigami.Units.smallSpacing
            opacity: source.available ? 1 : 0.45
            Tide.CountPill {
                sessionState: "working"
                count: root.counts.progress
                textColor: Kirigami.Theme.textColor
            }
            Tide.CountPill {
                sessionState: "waiting"
                count: root.counts.waiting
                emphasized: root.counts.waiting > 0
                textColor: Kirigami.Theme.textColor
            }
        }
    }

    fullRepresentation: Item {
        id: full

        readonly property bool sidebar: Plasmoid.configuration.sidebarMode
        readonly property real contentHeight: popupLoader.item ? popupLoader.item.implicitHeight : 0

        Layout.preferredWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 23
        Layout.minimumWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 19
        Layout.preferredHeight: contentHeight + (sidebar ? 0 : Kirigami.Units.smallSpacing * 2)
        Layout.minimumHeight: Layout.preferredHeight
        Layout.maximumHeight: sidebar ? Layout.preferredHeight : Number.POSITIVE_INFINITY
        Layout.fillWidth: sidebar

        Loader {
            id: popupLoader
            anchors.fill: parent
            anchors.margins: full.sidebar ? 0 : Kirigami.Units.smallSpacing
            sourceComponent: full.sidebar ? sidebarView : popupView
        }

        Component {
            id: popupView
            BoardPopup {
                snapshot: root.snapshot
                available: source.available
                quickProject: Plasmoid.configuration.quickProject
                status: root.status
                statusError: root.statusError
                busy: root.busy
                onOpenRequested: id => root.showWindow(id)
                onLaunchRequested: id => root.showWindow("launch:" + id)
                onAgentRequested: (session, id) => root.activateAgent(session, id)
                onAddRequested: (title, project) => root.addCard(title, project)
                onProjectChosen: slug => Plasmoid.configuration.quickProject = slug
                Component.onCompleted: input.forceActiveFocus()
                Connections {
                    target: root
                    function onCardAdded() { clearInput() }
                }
            }
        }

        Component {
            id: sidebarView
            SidebarBoard {
                snapshot: root.snapshot
                available: source.available
                problem: root.windowProblem
                onOpenRequested: id => root.showWindow(id)
            }
        }
    }
}

// Widżet Praca (PT-40): jeden kafelek zamiast Tablicy i Agentów — karty tablicy i sesje agentów bez karty
// z jednej listy usługi, więc liczby zawsze zgadzają się z Tablicą. Kafelek „Praca · 10” z „● 3 w toku ·
// ● 2 czeka · ● 2 do spr.” i „＋” (CompactWork), popup z grupami (WorkPopup), w pasku bocznym zwarty
// kafelek (SidebarWork) z pełną listą w okienku obok paska.
// Dane: tide-daemon przez D-Bus (org.tide.Daemon /Board Work(), opis w docs/tide-daemon.md); „Przejdź” —
// /Agents ActivateSession, a gdy sesji nie da się aktywować: karta w oknie Tide (/Board ShowWindow),
// sesja bez karty — okno Tide na widoku Sesje (/Coordination ShowWindow). „+ karta” — /Board CreateCard
// z żądaniem przygotowanym przez usługę; „＋” — /Tasks ShowDialog; link „Raport” — /Reports Status(), Open().
import QtQuick
import QtQuick.Layouts
import org.kde.kirigami as Kirigami
import org.kde.plasma.plasmoid
import org.kde.plasma.core as PlasmaCore
import "tide" as Tide
import "worktexts.js" as Texts

PlasmoidItem {
    id: root

    readonly property var work: source.result && typeof source.result === "object" ? source.result : null
    readonly property var counts: Texts.counts(work)
    property bool showFinished: false
    property real nowTs: Date.now() / 1000
    property string windowProblem: ""
    property string status: ""
    property bool statusError: false
    property bool busy: false
    // Okienko z pełną listą otwarte z paska bocznego („+N więcej”).
    property bool morePopupOpen: false

    function close() {
        if (!Plasmoid.configuration.sidebarMode) root.expanded = false
    }

    // Okno Tide na widoku Tablica (z kartą albo bez); popup zamyka się, gdy okno się otworzyło.
    function showBoard(cardId) {
        source.call("ShowWindow", [cardId || ""], function (ok) {
            root.windowProblem = ok === true ? "" : ok === false ? "not-installed" : "no-service"
            if (ok === true) root.close()
        })
    }

    // Okno Tide na widoku Sesje (z sesją albo bez) — usługa (/Coordination ShowWindow).
    function showSessions(sessionId) {
        source.callOn("/Coordination", "org.tide.Daemon.Coordination", "ShowWindow", [sessionId || ""], function (ok) {
            if (ok === true) root.close()
        })
    }

    // „Przejdź”: okno agenta (/Agents ActivateSession); gdy sesji nie da się aktywować — karta w oknie
    // Tide, a sesja bez karty — okno Tide na widoku Sesje.
    function activateAgent(sessionId, cardId) {
        if (!sessionId) {
            root.showBoard(cardId)
            return
        }
        source.callOn("/Agents", "org.tide.Daemon.Agents", "ActivateSession", [sessionId], function (ok) {
            if (ok === true) root.close()
            else if (cardId) root.showBoard(cardId)
            else root.showSessions(sessionId)
        })
    }

    // Okno „Tide: nowe zadanie” otwiera usługa (/Tasks ShowDialog); drugie otwarcie tylko je pokazuje.
    function newTask() {
        source.callOn("/Tasks", "org.tide.Daemon.Tasks", "ShowDialog", [], function (ok) {
            if (ok === true) root.close()
        })
    }

    // „+ karta”: karta z sesji bez karty — żądanie CreateCard przygotowała usługa (tytuł, repozytorium,
    // sesja), więc nowa karta od razu łączy się z sesją i sesja znika z „bez karty”.
    function addCard(request) {
        if (!request || busy) return
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
            root.status = "Założono kartę " + id
            statusTimer.restart()
            source.refresh()
        })
    }

    // Raport poranny w przeglądarce; usługa oznacza go jako przeczytany (kropka przy „Raport” gaśnie).
    function openReport() {
        reports.call("Open", [], function (ok) {
            if (ok === true) {
                root.close()
                reports.refresh()
            }
        })
    }

    Plasmoid.title: "Praca"
    Plasmoid.icon: "view-task"
    // Uwagi wymaga tylko „czeka” — reszta niczego nie blokuje (ta sama reguła co dawny kafelek Agentów).
    // W pasku bocznym (sidebarMode) bez NeedsAttention: chowany panel nie chowa się, dopóki któryś aplet
    // wymaga uwagi, więc czekający agent trzymałby pasek na ekranie. Otwarte okienko z pełną listą trzyma
    // pasek celowo, tak jak popup apletu (CompactApplet.qml).
    Plasmoid.status: counts.waiting > 0 && !Plasmoid.configuration.sidebarMode
        ? PlasmaCore.Types.NeedsAttentionStatus
        : morePopupOpen ? PlasmaCore.Types.RequiresAttentionStatus : PlasmaCore.Types.ActiveStatus
    toolTipMainText: "Praca"
    toolTipSubText: Texts.toolTip(source.available, counts)
    // Pasek boczny Tide pokazuje zwarty kafelek na stałe (scripts/panels/sidebar.js).
    preferredRepresentation: Plasmoid.configuration.sidebarMode ? fullRepresentation : compactRepresentation
    onExpandedChanged: if (expanded) {
        status = ""
        source.refresh()
        if (!Plasmoid.configuration.sidebarMode) reports.refresh()
    }

    Tide.DaemonSource {
        id: source
        path: "/Board"
        iface: "org.tide.Daemon.Board"
        method: "Work"
        pollInterval: 60000
    }

    // Najnowszy raport poranny — tylko do stopki popupu w docku (w pasku bocznym jest w nagłówku paska).
    Tide.DaemonSource {
        id: reports
        path: "/Reports"
        iface: "org.tide.Daemon.Reports"
        method: "Status"
        pollInterval: Plasmoid.configuration.sidebarMode ? 0 : 60000
    }

    // „bezczynny 1 d”, „skończył 5 min temu” liczone na bieżąco, bez pytania usługi.
    Timer {
        interval: 15000
        running: true
        repeat: true
        onTriggered: root.nowTs = Date.now() / 1000
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
        Accessible.name: "Praca: " + tile.summary

        CompactWork {
            id: tile
            visible: !compact.vertical
            anchors.centerIn: parent
            height: Math.min(parent.height, Math.max(implicitHeight, dockHeight))
            counts: root.counts
            available: source.available
            active: compact.containsMouse || root.expanded
            onNewTaskRequested: root.newTask()
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

        // Pionowy panel: same liczniki (w toku, czeka, do sprawdzenia).
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
            Tide.CountPill {
                visible: root.counts.review > 0
                tint: Tide.Tokens.lavender
                count: root.counts.review
                textColor: Kirigami.Theme.textColor
            }
        }
    }

    fullRepresentation: Item {
        id: full

        readonly property bool sidebar: Plasmoid.configuration.sidebarMode
        readonly property real contentHeight: loader.item ? loader.item.implicitHeight : 0

        Layout.preferredWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 25
        Layout.minimumWidth: sidebar ? -1 : Kirigami.Units.gridUnit * 20
        Layout.preferredHeight: contentHeight + (sidebar ? 0 : Kirigami.Units.smallSpacing * 2)
        Layout.minimumHeight: Layout.preferredHeight
        Layout.maximumHeight: sidebar ? Layout.preferredHeight : Number.POSITIVE_INFINITY
        Layout.fillWidth: sidebar

        Loader {
            id: loader
            anchors.fill: parent
            anchors.margins: full.sidebar ? 0 : Kirigami.Units.smallSpacing
            sourceComponent: full.sidebar ? sidebarView : popupView
        }

        Component {
            id: popupView
            WorkPopup {
                work: root.work
                available: source.available
                nowTs: root.nowTs
                showFinished: root.showFinished
                status: root.status
                statusError: root.statusError
                busy: root.busy
                report: reports.available ? reports.result : null
                onGoRequested: (session, card) => root.activateAgent(session, card)
                onOpenCardRequested: card => root.showBoard(card)
                onLaunchRequested: card => root.showBoard("launch:" + card)
                onAddCardRequested: request => root.addCard(request)
                onNewTaskRequested: root.newTask()
                onBoardRequested: root.showBoard("")
                onSessionsRequested: root.showSessions("")
                onReportRequested: root.openReport()
                onFinishedToggled: root.showFinished = !root.showFinished
            }
        }

        Component {
            id: sidebarView
            SidebarWork {
                id: side
                work: root.work
                available: source.available
                nowTs: root.nowTs
                problem: root.windowProblem
                onGoRequested: (session, card) => root.activateAgent(session, card)
                onOpenCardRequested: card => root.showBoard(card)
                onLaunchRequested: card => root.showBoard("launch:" + card)
                onNewTaskRequested: root.newTask()
                onBoardRequested: root.showBoard("")
                onSessionsRequested: root.showSessions("")
                onMoreRequested: morePopup.visible = !morePopup.visible

                // Pełna lista w okienku obok paska (pasek jest przy lewej krawędzi, więc okienko otwiera
                // się na prawo od „+N więcej”).
                PlasmaCore.Dialog {
                    id: morePopup
                    visualParent: side.moreButton
                    location: Plasmoid.location
                    type: PlasmaCore.Dialog.PopupMenu
                    hideOnWindowDeactivate: true
                    onVisibleChanged: root.morePopupOpen = visible
                    mainItem: WorkPopup {
                        width: Kirigami.Units.gridUnit * 25
                        work: root.work
                        available: source.available
                        nowTs: root.nowTs
                        showFinished: root.showFinished
                        status: root.status
                        statusError: root.statusError
                        busy: root.busy
                        // Przejście gdzie indziej zamyka okienko; „+ karta” i Skończone zostawiają je otwarte.
                        onGoRequested: (session, card) => { morePopup.visible = false; root.activateAgent(session, card) }
                        onOpenCardRequested: card => { morePopup.visible = false; root.showBoard(card) }
                        onLaunchRequested: card => { morePopup.visible = false; root.showBoard("launch:" + card) }
                        onAddCardRequested: request => root.addCard(request)
                        onNewTaskRequested: { morePopup.visible = false; root.newTask() }
                        onBoardRequested: { morePopup.visible = false; root.showBoard("") }
                        onSessionsRequested: { morePopup.visible = false; root.showSessions("") }
                        onFinishedToggled: root.showFinished = !root.showFinished
                    }
                }
            }
        }
    }
}

// Widok „Sesje” w oknie Tide (dawne okno „Tide Sesje”). Dane i akcje daje obiekt backend
// z backend.py (D-Bus do tide-daemon albo stały plik JSON przy --data).
//
// Umowa widoku okna Tide (apps/tide/qml/Main.qml): backend, active (widok jest na wierzchu),
// preview (opcje podglądu z --preview: ticket, result), showItem(item) — otwarcie na elemencie
// (tu: koordynator, np. claude:<sesja>), takeFocus() po przełączeniu, railInfo — opcjonalna
// ramka na dole paska widoków (tu brak).
import QtQuick
import "sessions.js" as Logic

Item {
    id: view

    property var backend: null
    property bool active: true
    property var preview: ({})
    readonly property var railInfo: null
    readonly property alias page: page

    readonly property var coordinators: backend ? Logic.parse(backend.coordinatorsJson, []) : []
    readonly property var board: {
        if (!backend) return null
        const value = Logic.parse(backend.boardJson, null)
        return value && value.id ? value : null
    }
    readonly property var sessions: backend ? Logic.parse(backend.sessionsJson, []) : []
    readonly property var flowCards: backend ? Logic.parse(backend.flowCardsJson, []) : []
    readonly property var detail: {
        if (!backend) return null
        const value = Logic.parse(backend.detailJson, null)
        return value && value.id ? value : null
    }

    function showItem(item) {
        if (item && backend) backend.select(item)
    }

    function takeFocus() {
        page.forceActiveFocus()
    }

    // Pierwsza wartość opcji podglądu (--preview klucz=wartość, można powtórzyć).
    function previewValue(key) {
        const value = preview ? preview[key] : undefined
        if (value === undefined || value === null) return ""
        // Z Pythona lista przychodzi jako QStringList — ma length, ale Array.isArray jej nie rozpoznaje.
        if (typeof value === "string") return value
        return value.length ? String(value[0]) : ""
    }

    SessionsPage {
        id: page
        anchors.fill: parent
        coordinators: view.coordinators
        board: view.board
        sessions: view.sessions
        flowCards: view.flowCards
        detail: view.detail
        available: view.backend ? view.backend.available : false
        selectedId: view.backend ? view.backend.selected : ""
        canDiff: view.backend ? view.backend.canDiff : false
        cardOpen: view.backend ? view.backend.cardOpen : "decision"
        onCardOpenRequested: value => { if (view.backend) view.backend.setCardOpen(value) }
        onCardRequested: (tag, cardId, method, params) => view.sendCardRequest(tag, cardId, method, params)
        onSelectRequested: id => view.backend.select(id)
        onActivateRequested: id => view.backend.activate(id)
        onRefreshRequested: force => view.backend.refresh(force)
        onDiffRequested: (id, key) => view.backend.openDiff(id, key)
        onWorktreeRequested: (id, key) => view.backend.openWorktree(id, key)
        onResultRequested: (id, run) => view.backend.loadDetail(id, run)
        onUrlRequested: url => Qt.openUrlExternally(url)
        onSessionRequested: id => view.backend.activateSession(id)
        Component.onCompleted: {
            const ticket = view.previewValue("ticket")
            const result = view.previewValue("result")
            if (ticket !== "") selectedKey = ticket
            if (result !== "") showResult(result)
            const card = view.previewValue("card")
            if (card !== "") openCard(card)
        }
    }

    // Metoda /Board i parametry przychodzą z decyzji gotowe; tu tylko trafiają do właściwego wywołania backendu.
    function sendCardRequest(tag, cardId, method, params) {
        const b = view.backend
        if (!b) return
        if (method === "ResolveCardStep") b.resolveCardStep(tag, cardId, JSON.stringify(params))
        else if (method === "ResolveCardCaretaker") b.resolveCardCaretaker(tag, cardId, JSON.stringify(params))
        else if (method === "ResolveCardReview") b.resolveCardReview(tag, cardId, params.choice || "")
        else if (method === "ApproveCardPlan") b.approveCardPlan(tag, cardId)
        else if (method === "EditCardPlan") b.editCardPlan(tag, cardId, JSON.stringify(params))
        else if (method === "EnterStep") b.enterStep(tag, cardId, params.step || "")
        else if (method === "TalkToCaretaker") b.talkToCaretaker(tag, cardId, JSON.stringify(params))
        else if (method === "ReportStepFault") b.reportStepFault(tag, cardId, JSON.stringify(params))
        else if (method === "PreviewStepFault") b.previewStepFault(tag, cardId, JSON.stringify(params))
        else page.handleReply(tag, { ok: false, error: "unknown-method", message: "Sesje nie znają metody " + method + "." })
    }

    Connections {
        target: view.backend
        function onReplied(tag, text) {
            page.handleReply(tag, Logic.parse(text, null))
        }
    }

    // Zegar dla „teraz · 4 min” i czasów agentów.
    Timer {
        interval: 30000
        running: true
        repeat: true
        onTriggered: page.nowTs = Date.now() / 1000
    }
    Component.onCompleted: if (view.backend && view.backend.nowTs > 0) page.nowTs = view.backend.nowTs

    Shortcut {
        sequences: [StandardKey.Refresh]
        enabled: view.active
        onActivated: if (view.backend) view.backend.refresh(true)
    }
    Shortcut {
        sequence: "Esc"
        enabled: view.active && page.showingResult
        onActivated: page.showingResult = false
    }
}

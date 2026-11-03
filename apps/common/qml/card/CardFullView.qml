// Pełna karta na cały widok okna Tide (K-35, PT-128): „← Tablica” i Esc wracają, u góry pasek 7 faz, pod nim
// „Twój ruch” — pierwsza czekająca decyzja do rozstrzygnięcia w miejscu (ten sam DecisionForm co w oknie decyzji)
// albo jedno zdanie, gdy nic nie czeka — a niżej fale ticketów z ludzkim stanem i „Teraz w tle” z samymi
// trwającymi krokami.
//
// Widok czyta wyłącznie pola usługi: `derived.phases[]`, `derived.tickets[]` (`status`, `statusLabel`,
// `waitingOn`, `executor`, `reviewer`, `wave`), `derived.flow.decisions[]`, `flow.plan.waves`, `flow.running[]`
// i `flow.headline` (daemon/tide_daemon/cardflow/fullcard.py). Niczego nie liczy: kolory to tylko przekład stanu
// z usługi na paletę, a kolejność faz, fal, ticketów i decyzji przychodzi z usługi.
//
// Umowa: `card` (karta z migawki Tablicy), `start()` — otwarcie od pierwszej decyzji, `failed(id, tekst)` — błąd
// usługi przy odpowiedzi (decyzja wraca), `showPreview(id, odpowiedź)` i sygnał `previewRequested(decisionId,
// method, params)` — podgląd karty z błędem Tide przed zapisem (PT-125, DecisionForm.qml), sygnały
// `backRequested()`, `answered(decisionId, method, params)`,
// `decisionsRequested()` — wszystkie decyzje w oknie decyzji, `panelRequested(notice)` — panel karty (wybór
// wykonawcy zostaje tam), `descriptionRequested()` — szuflada szybkiej edycji opisu, `enterStepRequested(stepId)` —
// „Wejdź” przy trwającym kroku.
//
// Rzadkie ustawienia (PT-134, US 14): kolumna, zgłoszenie w YouTracku, gałąź, wstrzymanie i archiwizacja zapisują się
// od razu — bez „Zapisz / Anuluj”. Każdy przycisk nazywa skutek („Przenieś do Gotowych”), a widok wysyła
// `settingRequested(kind, params)` z gotowym zdaniem skutku (`params.effect`), które Tablica pokazuje po zapisie.
// Archiwizacja i zmiana gałęzi najpierw pytają o potwierdzenie ze skutkiem; odmowa niczego nie wysyła. Sekcja
// „Ustawienia karty” i menu „Karta ▾” biorą akcje z jednej listy (`actions`), więc zawsze są te same.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "../tide" as Tide
import "decision.js" as Decision
import "settings.js" as Settings

Rectangle {
    id: view

    property var card: null
    property var answeredIds: []

    readonly property var derived: card && card.derived ? card.derived : null
    readonly property var flow: derived && derived.flow ? derived.flow : null
    readonly property var decisions: flow && Array.isArray(flow.decisions) ? flow.decisions : []
    readonly property var pending: decisions.filter(d => answeredIds.indexOf(d.id) < 0)
    readonly property var current: pending.length > 0 ? pending[0] : null
    readonly property var phases: derived && Array.isArray(derived.phases) ? derived.phases : []
    readonly property var tickets: derived && Array.isArray(derived.tickets) ? derived.tickets : []
    readonly property var waves: flow && flow.plan && Array.isArray(flow.plan.waves) ? flow.plan.waves : []
    readonly property var running: flow && Array.isArray(flow.running) ? flow.running : []
    readonly property string headline: flow && flow.headline ? flow.headline : ""
    readonly property alias form: form

    signal backRequested()
    signal answered(string decisionId, string method, var params)
    signal previewRequested(string decisionId, string method, var params)
    signal decisionsRequested()
    signal panelRequested(string notice)
    signal descriptionRequested()
    // kind: move {column} | update {field, value} | pause {paused} | archive {}; params.effect — zdanie o skutku.
    signal settingRequested(string kind, var params)
    signal enterStepRequested(string stepId)

    // --- rzadkie ustawienia (PT-134) -----------------------------------------------------------------
    readonly property string storedBranch: card && card.branch ? card.branch : ""
    readonly property string shownBranch: storedBranch || (flow && flow.cardBranch ? flow.cardBranch : "")
    readonly property string storedYoutrack: card && card.youtrack ? card.youtrack : ""
    // Kolumna widoczna na Tablicy (karta „Do sprawdzenia” ma zapisane todo).
    readonly property string shownColumn: derived && derived.column ? derived.column : (card ? card.column : "")
    readonly property bool paused: !!(flow && flow.paused)
    readonly property bool canPause: !!(derived && (!flow || !flow.plan || flow.plan.status !== "done")
                                        && derived.column !== "review")
    property bool menuOpen: false
    property string settingError: ""
    // Pytanie o potwierdzenie: null albo {kind, params, title, effect, yes}.
    property var confirming: null
    readonly property string typedBranch: branchField.text.trim()
    readonly property var typedYoutrack: Settings.issueKey(youtrackField.text)

    // Jedna lista akcji dla sekcji ustawień i menu „Karta ▾”: {id, label, effect, danger, input}.
    readonly property var actions: card ? Settings.actions(card.id, shownColumn, storedYoutrack, canPause, paused) : []

    // Podpisy przycisków z polem nazywają skutek tego, co wpisano („Odłącz od zgłoszenia LEGAL-35”).
    readonly property string youtrackButton: typedYoutrack === null || typedYoutrack === storedYoutrack
        ? actionLabel("youtrack")
        : typedYoutrack === "" ? "Odłącz od zgłoszenia " + storedYoutrack
        : (storedYoutrack ? "Zmień zgłoszenie na " : "Powiąż ze zgłoszeniem ") + typedYoutrack
    readonly property string branchButton: typedBranch === "" || typedBranch === shownBranch
        ? actionLabel("branch") : "Zmień gałąź na " + typedBranch

    // Menu „Karta ▾” nigdy nie zapisuje pola na ślepo: pozycje z polem przewijają do niego i dają fokus.
    function focusAction(id) {
        menuOpen = false
        focusField(id === "youtrack" ? youtrackField : branchField)
    }

    function actionLabel(id) {
        const found = actions.find(a => a.id === id)
        return found ? found.label : ""
    }

    function run(id) {
        menuOpen = false
        if (!card || confirming) return
        if (id.indexOf("move:") === 0) {
            const column = id.slice(5)
            settingRequested("move", { column: column,
                                       effect: "Karta " + card.id + " przeniesiona do „" + Settings.columnName(column) + "”." })
        } else if (id === "pause") {
            settingRequested("pause", { paused: !paused,
                                        effect: paused ? "Karta " + card.id + " wznowiona."
                                                       : "Karta " + card.id + " wstrzymana. Trwające kroki kończą się." })
        } else if (id === "archive") {
            askArchive()
        } else if (id === "youtrack") {
            if (typedYoutrack === "" && storedYoutrack === "") focusField(youtrackField)
            else applyYoutrack()
        } else if (id === "branch") {
            if (typedBranch === "" || typedBranch === shownBranch) focusField(branchField)
            else askBranch()
        }
    }

    // Przewija do sekcji „Ustawienia karty”.
    function showSettings() {
        flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, settings.y - 12))
    }

    function showTop() {
        flick.contentY = 0
    }

    function focusField(field) {
        showSettings()
        field.forceActiveFocus()
    }

    function applyYoutrack() {
        const key = typedYoutrack
        if (key === null) {
            settingError = "Zgłoszenie w YouTracku to klucz, np. LEGAL-35 (albo adres zgłoszenia)."
            return
        }
        settingError = ""
        if (key === storedYoutrack) return
        settingRequested("update", { field: "youtrack", value: key,
            effect: key === "" ? "Karta " + card.id + " odłączona od zgłoszenia " + storedYoutrack + "."
                               : "Karta " + card.id + " powiązana ze zgłoszeniem " + key + "." })
    }

    function askArchive() {
        confirming = { kind: "archive", params: { effect: "Karta " + card.id + " zdjęta z tablicy." },
                       title: "Zarchiwizować kartę " + card.id + "?",
                       effect: "Karta zniknie z tablicy. Plik karty i historia zostają, więc da się ją odtworzyć z archiwum; "
                               + "trwające kroki nie są przerywane.",
                       yes: "Archiwizuj kartę" }
    }

    function askBranch() {
        const next = typedBranch
        if (next === "" || next === shownBranch) return
        confirming = { kind: "update", params: { field: "branch", value: next,
                                                 effect: "Gałąź karty " + card.id + ": " + next + "." },
                       title: "Zmienić gałąź karty " + card.id + "?",
                       effect: "Tide będzie wiązać z kartą pracę z gałęzi „" + next + "”"
                               + (shownBranch ? " zamiast „" + shownBranch + "”" : "")
                               + ". Gałęzie w repozytorium zostają bez zmian.",
                       yes: "Zmień gałąź na " + next }
    }

    function confirmYes() {
        const c = confirming
        confirming = null
        if (c) settingRequested(c.kind, c.params)
        view.forceActiveFocus()
    }

    function confirmNo() {
        confirming = null
        view.forceActiveFocus()
    }

    function start() {
        answeredIds = []
        confirming = null
        menuOpen = false
        settingError = ""
        branchField.text = ""
        youtrackField.text = ""
        form.reset()
        flick.contentY = 0
        forceActiveFocus()
    }

    // Usługa odrzuciła odpowiedź: decyzja wraca na wierzch z powodem.
    function failed(decisionId, message) {
        answeredIds = answeredIds.filter(id => id !== decisionId)
        form.fail(message)
    }

    // Odpowiedź usługi na podgląd karty z błędem (PT-125); podgląd innej decyzji formularz pomija.
    function showPreview(decisionId, reply) {
        form.showPreview(decisionId, reply)
    }

    function ticket(id) {
        return tickets.find(t => t.id === id) || null
    }

    // Przekład stanu z usługi na paletę (bez logiki: stan liczy usługa).
    function phaseTone(state) {
        return state === "done" ? Tide.Tokens.mint : state === "now" ? Tide.Tokens.teal
             : state === "you" ? Tide.Tokens.peach : state === "hold" ? Tide.Tokens.butter : Tide.Tokens.textMuted
    }

    function statusTone(status) {
        return status === "merged" ? Tide.Tokens.mint : status === "running" || status === "fix" ? Tide.Tokens.teal
             : status === "review" || status === "merging" ? Tide.Tokens.lavender : status === "you" ? Tide.Tokens.peach
             : status === "limit" ? Tide.Tokens.butter : status === "rejected" ? Tide.Tokens.rose : Tide.Tokens.textMuted
    }

    function clock(value) {
        if (!value) return ""
        const when = typeof value === "number" ? new Date(value * 1000) : new Date(value)
        return isNaN(when.getTime()) ? "" : Qt.formatDateTime(when, "d.MM HH:mm")
    }

    function phaseCaption(phase) {
        if (phase.state === "skipped") return "nie dotyczy"
        const who = phase.executor === "user" ? "ty" : phase.executor
        return [who, clock(phase.ts), phase.note].filter(x => !!x).join(" · ")
    }

    color: Tide.Tokens.backgroundWindow
    focus: visible
    Accessible.role: Accessible.Pane
    Accessible.name: card ? "Karta " + card.id + ": " + card.title : "Karta"

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            if (confirming) confirmNo()
            else if (menuOpen) menuOpen = false
            else backRequested()
            event.accepted = true
        } else if (confirming) {
            event.accepted = true   // pytanie o potwierdzenie przejmuje klawiaturę: Enter nie potwierdza po cichu
        } else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && current) {
            form.choose()
            event.accepted = true
        }
    }

    MouseArea {
        anchors.fill: parent
        onClicked: view.forceActiveFocus()
    }

    Flickable {
        id: flick
        anchors.fill: parent
        anchors.margins: 24
        contentHeight: content.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: flick.contentHeight > flick.height ? QQC2.ScrollBar.AsNeeded : QQC2.ScrollBar.AlwaysOff }

        ColumnLayout {
            id: content
            width: flick.width
            spacing: 18

            // Okruszki: powrót, projekt, gałąź karty, panel karty.
            RowLayout {
                Layout.fillWidth: true
                spacing: 12
                Tide.TextButton {
                    objectName: "fullBack"
                    text: "← Tablica"
                    variant: "outline"
                    Accessible.name: "Wróć do Tablicy (Esc)"
                    onClicked: view.backRequested()
                }
                Text {
                    text: view.card ? view.card.projectName || view.card.project || "" : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                }
                Text {
                    Layout.fillWidth: true
                    text: view.flow ? view.flow.cardBranch || "" : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.lavender
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 0.9
                    elide: Text.ElideMiddle
                }
                Tide.TextButton {
                    objectName: "fullDescription"
                    text: "Edytuj opis"
                    variant: "outline"
                    Accessible.name: "Edytuj opis karty w szufladzie"
                    onClicked: view.descriptionRequested()
                }
                Tide.TextButton {
                    objectName: "fullMenu"
                    text: "Karta ▾"
                    variant: "outline"
                    Accessible.name: "Menu karty: ustawienia i akcje"
                    onClicked: view.menuOpen = !view.menuOpen
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 10
                Text {
                    text: view.card ? view.card.id : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.sky
                    font.family: Tide.Theme.monoFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 1.5
                    font.weight: Font.DemiBold
                }
                Text {
                    objectName: "fullTitle"
                    Layout.fillWidth: true
                    text: view.card ? view.card.title : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 1.5
                    font.weight: Font.DemiBold
                    wrapMode: Text.Wrap
                }
            }

            // Pasek 7 faz.
            RowLayout {
                objectName: "fullPhases"
                Layout.fillWidth: true
                spacing: 8
                Accessible.role: Accessible.List
                Accessible.name: "Przebieg karty"
                Repeater {
                    model: view.phases
                    delegate: ColumnLayout {
                        id: phaseItem
                        required property var modelData
                        objectName: "fullPhase"
                        readonly property string phaseId: modelData.id
                        readonly property string phaseState: modelData.state
                        readonly property string caption: view.phaseCaption(modelData)
                        Layout.fillWidth: true
                        Layout.preferredWidth: 1
                        spacing: 4
                        Accessible.role: Accessible.ListItem
                        Accessible.name: modelData.label + ": " + caption
                        Rectangle {
                            Layout.fillWidth: true
                            implicitHeight: 4
                            radius: height / 2
                            color: phaseItem.phaseState === "todo" || phaseItem.phaseState === "skipped"
                                   ? Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityOutline)
                                   : view.phaseTone(phaseItem.phaseState)
                        }
                        Text {
                            Layout.fillWidth: true
                            text: phaseItem.modelData.label
                            textFormat: Text.PlainText
                            color: phaseItem.phaseState === "todo" || phaseItem.phaseState === "skipped" ? Tide.Tokens.textMuted
                                                                                             : Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.95
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                        }
                        Text {
                            Layout.fillWidth: true
                            text: phaseItem.caption || " "
                            textFormat: Text.PlainText
                            color: phaseItem.phaseState === "you" || phaseItem.phaseState === "hold" ? view.phaseTone(phaseItem.phaseState)
                                                                                           : Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.85
                            wrapMode: Text.Wrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }
                    }
                }
            }

            // Twój ruch: decyzja do rozstrzygnięcia w miejscu albo jedno zdanie.
            Rectangle {
                objectName: "fullMove"
                Layout.fillWidth: true
                implicitHeight: move.implicitHeight + 40
                radius: Tide.Tokens.radiusCard
                color: view.current ? Tide.Theme.alpha(Tide.Tokens.peach, 0.06) : Tide.Tokens.backgroundCard
                border.width: 1
                border.color: view.current ? Tide.Theme.alpha(Tide.Tokens.peach, 0.32)
                                           : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
                Accessible.role: Accessible.Grouping
                Accessible.name: "Twój ruch"

                ColumnLayout {
                    id: move
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 20
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Text {
                            text: "Twój ruch"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textSecondary
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt
                            font.weight: Font.DemiBold
                        }
                        DecisionKind {
                            objectName: "fullMoveKind"
                            visible: !!view.current
                            decision: view.current
                        }
                        Text {
                            objectName: "fullMoveWho"
                            Layout.fillWidth: true
                            visible: !!view.current
                            text: view.card ? Decision.who(view.card.id, view.current) : ""
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.95
                            elide: Text.ElideRight
                        }
                        Item { Layout.fillWidth: true; visible: !view.current }
                        Tide.TextButton {
                            objectName: "fullAllDecisions"
                            visible: view.pending.length > 1
                            text: "1 z " + view.pending.length + " · wszystkie w oknie decyzji"
                            variant: "link"
                            onClicked: view.decisionsRequested()
                        }
                    }

                    Text {
                        objectName: "fullHeadline"
                        Layout.fillWidth: true
                        visible: !view.current
                        text: view.headline
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 1.25
                        font.weight: Font.DemiBold
                        wrapMode: Text.Wrap
                    }

                    DecisionForm {
                        id: form
                        objectName: "fullMoveForm"
                        Layout.fillWidth: true
                        visible: !!view.current
                        decision: view.current
                        onInputNeeded: notice => view.panelRequested(notice)
                        onEscapePressed: view.backRequested()
                        onPreviewRequested: (decisionId, method, params) => view.previewRequested(decisionId, method, params)
                        onAnswered: (decisionId, method, params) => {
                            view.answeredIds = view.answeredIds.concat([decisionId])
                            view.answered(decisionId, method, params)
                            view.forceActiveFocus()
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        visible: !!view.current
                        Item { Layout.fillWidth: true }
                        Tide.TextButton {
                            objectName: "fullChoose"
                            text: form.chooseLabel + "  ↵"
                            variant: "filled"
                            enabled: form.canChoose
                            opacity: enabled ? 1 : 0.45
                            Accessible.name: form.chooseLabel
                            onClicked: form.choose()
                        }
                    }
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: 20

                // Fale ticketów.
                ColumnLayout {
                    objectName: "fullPlan"
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    spacing: 10
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Text {
                            text: "Plan"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 1.1
                            font.weight: Font.DemiBold
                        }
                        Text {
                            Layout.fillWidth: true
                            text: view.flow && view.tickets.length > 0 ? view.flow.waveLabel || "" : ""
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.95
                        }
                    }
                    Text {
                        objectName: "fullPlanEmpty"
                        Layout.fillWidth: true
                        visible: view.tickets.length === 0
                        text: "Karta bez planu: jeden ticket, bez fal."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Repeater {
                            model: view.waves
                            delegate: ColumnLayout {
                                id: wave
                                required property var modelData
                                required property int index
                                objectName: "fullWave"
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                Layout.alignment: Qt.AlignTop
                                spacing: 8
                                Text {
                                    text: "Fala " + (wave.index + 1)
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textSecondary
                                    font.family: Tide.Theme.uiFamily
                                    font.pointSize: Tide.Tokens.fontSizePt * 0.95
                                    font.weight: Font.DemiBold
                                }
                                Repeater {
                                    model: wave.modelData
                                    delegate: Rectangle {
                                        id: tile
                                        required property var modelData
                                        readonly property var item: view.ticket(modelData)
                                        readonly property string ticketId: modelData
                                        readonly property string status: item ? item.status || "" : ""
                                        readonly property string statusLabel: item ? item.statusLabel || "" : ""
                                        objectName: "fullTicket"
                                        Layout.fillWidth: true
                                        implicitHeight: tileColumn.implicitHeight + 18
                                        radius: Tide.Tokens.radiusTile
                                        color: Tide.Tokens.backgroundCard
                                        border.width: 1
                                        border.color: status === "you" ? Tide.Theme.alpha(Tide.Tokens.peach, 0.45)
                                                                       : Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
                                        Accessible.role: Accessible.ListItem
                                        Accessible.name: ticketId + ": " + (item ? item.title : "") + " — " + statusLabel
                                        ColumnLayout {
                                            id: tileColumn
                                            anchors.left: parent.left
                                            anchors.right: parent.right
                                            anchors.top: parent.top
                                            anchors.margins: 9
                                            anchors.leftMargin: 11
                                            spacing: 4
                                            Text {
                                                text: tile.ticketId
                                                textFormat: Text.PlainText
                                                color: Tide.Tokens.sky
                                                font.family: Tide.Theme.monoFamily
                                                font.pointSize: Tide.Tokens.fontSizePt * 0.85
                                                font.weight: Font.DemiBold
                                            }
                                            Text {
                                                Layout.fillWidth: true
                                                text: tile.item ? tile.item.title : ""
                                                textFormat: Text.PlainText
                                                color: Tide.Tokens.textPrimary
                                                font.family: Tide.Theme.uiFamily
                                                font.pointSize: Tide.Tokens.fontSizePt * 0.95
                                                wrapMode: Text.Wrap
                                                maximumLineCount: 3
                                                elide: Text.ElideRight
                                            }
                                            Text {
                                                objectName: "fullTicketStatus"
                                                Layout.fillWidth: true
                                                text: tile.statusLabel
                                                textFormat: Text.PlainText
                                                color: view.statusTone(tile.status)
                                                font.family: Tide.Theme.uiFamily
                                                font.pointSize: Tide.Tokens.fontSizePt * 0.85
                                                font.weight: Font.Medium
                                                elide: Text.ElideRight
                                            }
                                            Text {
                                                Layout.fillWidth: true
                                                visible: text !== ""
                                                text: tile.item ? [tile.item.executor, tile.item.reviewer].filter(x => !!x).join(" → ") : ""
                                                textFormat: Text.PlainText
                                                color: Tide.Tokens.textMuted
                                                font.family: Tide.Theme.uiFamily
                                                font.pointSize: Tide.Tokens.fontSizePt * 0.8
                                                elide: Text.ElideRight
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Teraz w tle: tylko trwające kroki.
                ColumnLayout {
                    objectName: "fullNowSection"
                    Layout.preferredWidth: 340
                    Layout.maximumWidth: 380
                    Layout.alignment: Qt.AlignTop
                    spacing: 8
                    Text {
                        text: "Teraz w tle"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 1.1
                        font.weight: Font.DemiBold
                    }
                    Text {
                        objectName: "fullNowEmpty"
                        Layout.fillWidth: true
                        visible: view.running.length === 0
                        text: view.current ? "Nic nie pracuje w tle — Tide czeka na Twoją decyzję."
                                           : "Nic nie pracuje teraz w tle."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        wrapMode: Text.Wrap
                    }
                    Repeater {
                        model: view.running
                        delegate: Rectangle {
                            id: step
                            required property var modelData
                            readonly property string stepId: modelData.id
                            readonly property string label: [modelData.ticket, modelData.kindLabel, modelData.executor]
                                                            .filter(x => !!x).join(" · ")
                            objectName: "fullNow"
                            Layout.fillWidth: true
                            implicitHeight: stepRow.implicitHeight + 18
                            radius: Tide.Tokens.radiusTile
                            color: Tide.Tokens.backgroundCard
                            Accessible.role: Accessible.ListItem
                            Accessible.name: label
                            RowLayout {
                                id: stepRow
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 10
                                spacing: 10
                                Rectangle {
                                    implicitWidth: 8
                                    implicitHeight: 8
                                    radius: width / 2
                                    color: step.modelData.kind === "review" ? Tide.Tokens.lavender : Tide.Tokens.teal
                                }
                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 2
                                    Text {
                                        Layout.fillWidth: true
                                        text: step.label
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textPrimary
                                        font.family: Tide.Theme.uiFamily
                                        font.pointSize: Tide.Tokens.fontSizePt * 0.95
                                        font.weight: Font.DemiBold
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: step.modelData.startedTs ? "od " + view.clock(step.modelData.startedTs) : ""
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textMuted
                                        font.family: Tide.Theme.uiFamily
                                        font.pointSize: Tide.Tokens.fontSizePt * 0.85
                                        elide: Text.ElideRight
                                    }
                                }
                                Tide.TextButton {
                                    objectName: "fullNowEnter"
                                    visible: step.modelData.canEnter === true
                                    text: "Wejdź"
                                    variant: "outline"
                                    Accessible.name: "Wejdź do kroku " + step.label
                                    onClicked: view.enterStepRequested(step.stepId)
                                }
                            }
                        }
                    }
                }
            }

            // Ustawienia karty (PT-134): zapisują się od razu, przyciski nazywają skutek.
            Rectangle {
                id: settings
                objectName: "fullSettings"
                Layout.fillWidth: true
                implicitHeight: settingsColumn.implicitHeight + 36
                radius: Tide.Tokens.radiusCard
                color: Tide.Tokens.backgroundCard
                border.width: 1
                border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
                Accessible.role: Accessible.Grouping
                Accessible.name: "Ustawienia karty"

                ColumnLayout {
                    id: settingsColumn
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.top: parent.top
                    anchors.margins: 18
                    spacing: 10
                    Text {
                        text: "Ustawienia karty"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 1.1
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "Zmiany zapisują się od razu — bez „Zapisz”. Archiwizacja i zmiana gałęzi najpierw pytają o potwierdzenie."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt * 0.9
                        wrapMode: Text.Wrap
                    }
                    Text {
                        objectName: "settingError"
                        Layout.fillWidth: true
                        visible: view.settingError !== ""
                        text: view.settingError
                        textFormat: Text.PlainText
                        color: Tide.Tokens.rose
                        font.family: Tide.Theme.uiFamily
                        font.pointSize: Tide.Tokens.fontSizePt
                        wrapMode: Text.Wrap
                    }

                    // Akcje bez pola (kolumna, wstrzymanie, archiwizacja) — z tej samej listy co menu „Karta ▾”.
                    Repeater {
                        model: view.actions.filter(a => !a.input)
                        delegate: RowLayout {
                            id: setting
                            required property var modelData
                            objectName: "fullSetting"
                            Layout.fillWidth: true
                            spacing: 12
                            Tide.TextButton {
                                objectName: "settingButton"
                                Layout.preferredWidth: 220
                                text: setting.modelData.label
                                variant: "outline"
                                textColor: setting.modelData.danger ? Tide.Tokens.rose : Tide.Tokens.textSecondary
                                Accessible.name: setting.modelData.label
                                onClicked: view.run(setting.modelData.id)
                            }
                            Text {
                                Layout.fillWidth: true
                                text: setting.modelData.effect
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pointSize: Tide.Tokens.fontSizePt * 0.85
                                wrapMode: Text.Wrap
                            }
                        }
                    }

                    // Zgłoszenie w YouTracku: pole poza Repeaterem, żeby wpisany tekst przeżył nową migawkę.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        QQC2.TextField {
                            id: youtrackField
                            objectName: "settingYoutrackField"
                            Layout.preferredWidth: 220
                            placeholderText: view.storedYoutrack || "np. LEGAL-35"
                            maximumLength: 200
                            selectByMouse: true
                            Accessible.name: "Zgłoszenie w YouTracku (teraz: " + (view.storedYoutrack || "brak") + ")"
                            onAccepted: view.run("youtrack")
                            onTextChanged: view.settingError = ""
                        }
                        Tide.TextButton {
                            objectName: "settingYoutrackButton"
                            text: view.youtrackButton
                            variant: "outline"
                            onClicked: view.run("youtrack")
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "Teraz: " + (view.storedYoutrack || "brak") + ". Puste pole odłącza kartę od zgłoszenia."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.85
                            wrapMode: Text.Wrap
                        }
                    }

                    // Gałąź karty: zmiana pyta o potwierdzenie ze skutkiem.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        QQC2.TextField {
                            id: branchField
                            objectName: "settingBranchField"
                            Layout.preferredWidth: 220
                            placeholderText: "nazwa gałęzi"
                            maximumLength: 200
                            selectByMouse: true
                            Accessible.name: "Gałąź karty (teraz: " + (view.shownBranch || "brak") + ")"
                            onAccepted: view.run("branch")
                        }
                        Tide.TextButton {
                            objectName: "settingBranchButton"
                            text: view.branchButton
                            variant: "outline"
                            onClicked: view.run("branch")
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "Teraz: " + (view.shownBranch || "brak") + ". Zapyta o potwierdzenie."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pointSize: Tide.Tokens.fontSizePt * 0.85
                            wrapMode: Text.Wrap
                        }
                    }
                }
            }
        }
    }

    // Menu „Karta ▾”: te same akcje co sekcja ustawień.
    Rectangle {
        id: cardMenu
        objectName: "fullMenuList"
        visible: view.menuOpen
        z: 5
        x: view.width - width - 24
        y: 24 + 44
        width: 300
        implicitHeight: menuColumn.implicitHeight + 16
        height: implicitHeight
        radius: Tide.Tokens.radiusCard
        color: Tide.Tokens.backgroundPanel
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
        Accessible.role: Accessible.PopupMenu
        Accessible.name: "Menu karty"
        ColumnLayout {
            id: menuColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 8
            spacing: 2
            Repeater {
                model: view.actions
                delegate: Tide.TextButton {
                    required property var modelData
                    objectName: "fullMenuItem"
                    Layout.fillWidth: true
                    text: modelData.label + (modelData.input || modelData.danger ? "…" : "")
                    accent: modelData.danger ? Tide.Tokens.rose : Tide.Tokens.textSecondary
                    textColor: modelData.danger ? Tide.Tokens.rose : Tide.Tokens.textPrimary
                    Accessible.name: modelData.label
                    onClicked: modelData.input ? view.focusAction(modelData.id) : view.run(modelData.id)
                }
            }
        }
    }

    // Potwierdzenie archiwizacji i zmiany gałęzi: opisuje skutek; odmowa (Esc, „Nie zmieniaj”) niczego nie wysyła.
    Rectangle {
        id: confirmLayer
        objectName: "settingConfirm"
        anchors.fill: parent
        z: 10
        visible: view.confirming !== null
        color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.62)
        MouseArea { anchors.fill: parent; onClicked: view.confirmNo() }
        Rectangle {
            anchors.centerIn: parent
            width: Math.min(parent.width - 48, 480)
            implicitHeight: confirmColumn.implicitHeight + 40
            height: implicitHeight
            radius: Tide.Tokens.radiusCard
            color: Tide.Tokens.backgroundPanel
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, Tide.Tokens.opacityBorder)
            Accessible.role: Accessible.AlertMessage
            Accessible.name: view.confirming ? view.confirming.title : ""
            MouseArea { anchors.fill: parent }
            ColumnLayout {
                id: confirmColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 20
                spacing: 12
                Text {
                    objectName: "settingConfirmTitle"
                    Layout.fillWidth: true
                    text: view.confirming ? view.confirming.title : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt * 1.15
                    font.weight: Font.DemiBold
                    wrapMode: Text.Wrap
                }
                Text {
                    objectName: "settingConfirmEffect"
                    Layout.fillWidth: true
                    text: view.confirming ? view.confirming.effect : ""
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.uiFamily
                    font.pointSize: Tide.Tokens.fontSizePt
                    wrapMode: Text.Wrap
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Item { Layout.fillWidth: true }
                    Tide.TextButton {
                        objectName: "settingConfirmNo"
                        text: "Nie zmieniaj (Esc)"
                        variant: "outline"
                        onClicked: view.confirmNo()
                    }
                    Tide.TextButton {
                        objectName: "settingConfirmYes"
                        text: view.confirming ? view.confirming.yes : ""
                        variant: "filled"
                        accent: view.confirming && view.confirming.kind === "archive" ? Tide.Tokens.rose : Tide.Tokens.accent
                        onClicked: view.confirmYes()
                    }
                }
            }
        }
    }
}

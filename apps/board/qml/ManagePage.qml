// „Projekty i tagi” (link na dole paska okna): projekty z tagami, miejscem dokumentów AI, domyślnym
// narzędziem i trybem; repozytoria z katalogu projektów, które można dodać jako projekt; tagi z kolorami
// z palety. Każdy zapis to jeden UpsertProject albo UpsertTag w usłudze (commit w repozytorium
// tablicy); błąd pokazuje się przy formularzu, który go wywołał. Esc albo „← Tablica” wraca.
// YouTrack (PT-7): sekcja nad projektami — stan synchronizacji, mapa stanów, konflikty ze zmianą ręczną
// („Weź z YouTracka” / „Nadpisz z Tide”, także dla ticketów planów bez karty) i „Importuj z YouTracka”
// (ImportPanel). W formularzu projektu: klucz projektu YouTracka i przełącznik synchronizacji stanu.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "board.js" as Logic

Rectangle {
    id: page

    property var snapshot: null
    property string homeDir: ""
    property var tagTone: null
    property var modelsStatus: null
    property var flowSettings: null
    property string limitTransferMode: "ask"
    property string limitTransferExecutor: ""
    property bool flowSettingsDirty: false
    property bool flowSettingsBusy: false
    property string flowSettingsError: ""
    // Opiekun karty (PT-151): wyłącznik, wykonawca („” — domyślny), limit działań i minuty czekania; zapis jednym
    // SetCardFlowSettings z czterema kluczami, walidacja zakresów w usłudze (tu tylko granice pól).
    property bool caretakerEnabled: true
    property string caretakerExecutor: ""
    property int caretakerActionLimit: 3
    property int caretakerDecisionMinutes: 10
    property bool caretakerDirty: false
    property bool caretakerBusy: false
    property string caretakerError: ""
    property string openProject: ""     // slug projektu z otwartym formularzem
    property string openTag: ""         // nazwa tagu z otwartym formularzem ("+" — nowy)
    property string projectError: ""
    property string testsError: ""      // błąd zapisu albo zatwierdzenia kontraktu testów (sekcja „Testy projektu”)
    property bool testsInRequest: false // ostatni zapis projektu niósł pole tests
    property string tagError: ""
    property string repoError: ""
    property string busyTag: ""         // tag żądania w toku
    property bool importOpen: false
    property int serial: 0
    readonly property var youtrack: (snapshot && snapshot.youtrack) || null
    readonly property alias importPanel: importPanel
    readonly property var projects: (snapshot && snapshot.projects) || []
    readonly property var tags: (snapshot && snapshot.tags) || []
    readonly property var repos: (snapshot && snapshot.repos) || []
    readonly property var pastels: Object.keys((snapshot && snapshot.colors) || {})
    signal projectRequested(string tag, var request)
    signal suggestRequested(string tag, var request)       // /Board SuggestProjectTests (tylko odczyt)
    signal tagRequested(string tag, var request)
    signal backRequested()
    signal issuesRequested(string tag, bool refresh)       // /Board YouTrackIssues
    signal importRequested(string tag, var keys)           // /Board ImportYouTrack
    signal resolveRequested(string tag, string ident, string choice)   // /Board ResolveYouTrack
    signal flowSettingsSaveRequested(string tag, var request) // /Board SetCardFlowSettings

    function loadFlowSettings() {
        const value = flowSettings && flowSettings.limitTransfer ? flowSettings.limitTransfer : {}
        limitTransferMode = value.mode === "auto" ? "auto" : "ask"
        limitTransferExecutor = value.executor || ""
        flowSettingsDirty = false
    }

    function loadCaretakerSettings() {
        const value = flowSettings || {}
        caretakerEnabled = value.caretakerEnabled !== false
        caretakerExecutor = typeof value.caretakerExecutor === "string" ? value.caretakerExecutor : ""
        caretakerActionLimit = Number.isInteger(value.caretakerActionLimit) ? value.caretakerActionLimit : 3
        caretakerDecisionMinutes = Number.isInteger(value.caretakerDecisionMinutes) ? value.caretakerDecisionMinutes : 10
        caretakerDirty = false
    }

    function saveCaretakerSettings() {
        caretakerBusy = true
        caretakerError = ""
        flowSettingsSaveRequested(nextTag("caretaker-settings"),
            { caretakerEnabled: caretakerEnabled, caretakerExecutor: caretakerExecutor,
              caretakerActionLimit: caretakerActionLimit, caretakerDecisionMinutes: caretakerDecisionMinutes })
    }

    onFlowSettingsChanged: {
        if (!flowSettingsDirty && !flowSettingsBusy) loadFlowSettings()
        if (!caretakerDirty && !caretakerBusy) loadCaretakerSettings()
    }
    onVisibleChanged: if (visible) {
        flowSettingsDirty = false
        loadFlowSettings()
        caretakerDirty = false
        loadCaretakerSettings()
    }

    function nextTag(prefix) {
        serial++
        return prefix + ":" + serial
    }

    // Wiersz projektu po slugu (testy i skróty): ProjectRow z formularzem.
    function projectRow(slug) {
        for (let i = 0; i < projectRepeater.count; i++) {
            const item = projectRepeater.itemAt(i)
            if (item && item.project && item.project.slug === slug) return item
        }
        return null
    }

    function openImport() {
        importOpen = true
        importPanel.error = ""
        importPanel.result = ""
        refreshImport()
    }

    function refreshImport() {
        importPanel.loading = true
        issuesRequested(nextTag("yt-issues"), true)
    }

    // Odpowiedzi SuggestProjectTests po slugu: źródło obowiązującego kontraktu i podpowiedź liczy usługa.
    property var suggestions: ({})
    property int suggestSerial: 0

    function requestSuggestion(slug) {
        if (slug) suggestRequested("suggest:" + slug + ":" + (++suggestSerial), { slug: slug })
    }

    // Odpowiedź usługi na zapis z tej strony (tag z prefiksem project:/tag:/repo:).
    function handleReply(tag, reply) {
        if (tag.indexOf("suggest:") === 0) {
            const next = Object.assign({}, suggestions)
            next[tag.split(":")[1]] = reply || ({ ok: false })
            suggestions = next
            return
        }
        if (tag === busyTag) busyTag = ""
        const error = reply && reply.ok ? "" : Logic.errorText(reply)
        if (tag.indexOf("rules:") === 0) {
            assignmentError = error
        } else if (tag.indexOf("project:") === 0) {
            const approval = !error && reply.approvalError ? String(reply.approvalError) : ""
            projectError = error || approval
            testsError = testsInRequest ? projectError : ""
            if (!projectError) openProject = ""
        } else if (tag.indexOf("tests:") === 0) {
            testsError = error || (reply.approvalError ? String(reply.approvalError) : "")
        } else if (tag.indexOf("tag:") === 0) {
            tagError = error
            if (!error) openTag = ""
        } else if (tag.indexOf("repo:") === 0) {
            repoError = error
            // Dodanie repozytorium jako projektu otwiera jego formularz: tam jest podpowiedź testów z repozytorium.
            if (!error && reply.slug && tag.indexOf("repo:/") === 0) openProject = reply.slug
        } else if (tag.indexOf("yt-issues:") === 0) {
            importPanel.loading = false
            importPanel.error = error
            if (!error) {
                importPanel.issues = reply.issues || []
                const keep = {}
                for (const key of Object.keys(importPanel.picked))
                    if (importPanel.issues.some(i => i.id === key && !i.card)) keep[key] = true
                importPanel.picked = keep
            }
        } else if (tag.indexOf("yt-import:") === 0) {
            importPanel.busy = false
            importPanel.error = error
            if (!error) {
                const created = reply.created || []
                const skipped = reply.skipped || []
                importPanel.result = created.length
                    ? "Dodane do Backlogu: " + created.map(c => c.issue + " → " + c.card).join(", ")
                      + (skipped.length ? " · pominięte: " + skipped.length : "")
                    : "Nic nowego — " + skipped.map(x => x.issue + " (" + x.reason + ")").join(", ")
                importPanel.picked = ({})
                issuesRequested(nextTag("yt-issues"), false)
            }
        } else if (tag.indexOf("flow-settings:") === 0) {
            flowSettingsBusy = false
            flowSettingsError = error
            if (!error) flowSettingsDirty = false
        } else if (tag.indexOf("caretaker-settings:") === 0) {
            caretakerBusy = false
            caretakerError = error
            if (!error) caretakerDirty = false
        } else if (tag.indexOf("yt-resolve:") === 0) {
            youtrackError = error
        }
    }
    property string youtrackError: ""
    property string assignmentError: ""

    function send(kind, key, request) {
        busyTag = kind + ":" + key
        if (kind === "project") { testsInRequest = !!(request && request.tests); testsError = "" }
        if (kind === "tests") testsError = ""
        if (kind === "tag") tagRequested(busyTag, request)
        else projectRequested(busyTag, request)
    }

    color: Tide.Tokens.backgroundWindow
    Accessible.role: Accessible.Pane
    Accessible.name: "Projekty i tagi"

    component SectionTitle: Text {
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
        font.letterSpacing: 0.9
        font.weight: Font.DemiBold
        Accessible.role: Accessible.Heading
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        anchors.topMargin: 20
        anchors.bottomMargin: 20
        spacing: 16

        RowLayout {
            Layout.fillWidth: true
            spacing: 14
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: "Projekty i tagi"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 21
                    font.weight: Font.DemiBold
                    Accessible.role: Accessible.Heading
                }
                Text {
                    Layout.fillWidth: true
                    text: page.projects.length + " " + Logic.plural(page.projects.length, "projekt", "projekty", "projektów")
                          + " · " + page.tags.length + " " + Logic.plural(page.tags.length, "tag", "tagi", "tagów")
                          + " · tagi projektu dziedziczy każda jego karta"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                }
            }
            PillButton {
                objectName: "manageBack"
                text: "← Tablica"
                toolTip: "Wróć do tablicy (Esc)"
                onClicked: page.backRequested()
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 18

            // --- projekty --------------------------------------------------------------------------
            Flickable {
                id: projectsFlick
                objectName: "manageProjectsFlick"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 3
                clip: true
                contentWidth: width
                contentHeight: projectsColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

                ColumnLayout {
                    id: projectsColumn
                    width: projectsFlick.width - 12
                    spacing: 8

                    SectionTitle { text: "PRZEPŁYW KARTY · LIMITY" }
                    Text {
                        Layout.fillWidth: true
                        text: "Po wyczerpaniu limitu zapytaj o przeniesienie kroku albo przenieś go automatycznie do wybranego wykonawcy."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        QQC2.ComboBox {
                            // Kółko myszy przewija panel, nie zmienia wyboru (03.11: przypadkowa zmiana wykonawcy).
                            wheelEnabled: false
                            objectName: "limitTransferMode"
                            Layout.fillWidth: true
                            model: [{value: "ask", label: "Zapytaj mnie"},
                                    {value: "auto", label: "Przenieś automatycznie"}]
                            textRole: "label"
                            valueRole: "value"
                            currentIndex: page.limitTransferMode === "auto" ? 1 : 0
                            onActivated: index => {
                                page.limitTransferMode = model[index].value
                                page.flowSettingsDirty = true
                            }
                            Accessible.name: "Reakcja na wyczerpany limit"
                        }
                        QQC2.ComboBox {
                            // Kółko myszy przewija panel, nie zmienia wyboru (03.11: przypadkowa zmiana wykonawcy).
                            wheelEnabled: false
                            objectName: "limitTransferExecutor"
                            Layout.fillWidth: true
                            visible: page.limitTransferMode === "auto"
                            model: Logic.transferSettingsExecutors(page.modelsStatus)
                            textRole: "label"
                            valueRole: "value"
                            currentIndex: model.findIndex(option => option.value === page.limitTransferExecutor)
                            onActivated: index => {
                                page.limitTransferExecutor = model[index].value
                                page.flowSettingsDirty = true
                            }
                            Accessible.name: "Wykonawca docelowy przy limicie"
                        }
                        PillButton {
                            objectName: "limitTransferSave"
                            text: "Zapisz"
                            enabled: page.flowSettingsDirty && !page.flowSettingsBusy &&
                                     (page.limitTransferMode !== "auto" || page.limitTransferExecutor !== "")
                            onClicked: {
                                page.flowSettingsBusy = true
                                page.flowSettingsError = ""
                                page.flowSettingsSaveRequested(page.nextTag("flow-settings"),
                                    { limitTransfer: { mode: page.limitTransferMode,
                                                       executor: page.limitTransferMode === "auto" ? page.limitTransferExecutor : "" } })
                            }
                        }
                    }
                    Text {
                        objectName: "limitTransferError"
                        Layout.fillWidth: true
                        visible: page.flowSettingsError !== ""
                        text: page.flowSettingsError
                        textFormat: Text.PlainText
                        color: Tide.Tokens.rose
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }

                    SectionTitle { text: "PRZEPŁYW KARTY · OPIEKUN" }
                    Text {
                        Layout.fillWidth: true
                        text: "Opiekun karty sprawdza problem karty, zanim Tide zapyta Ciebie. Karta „Tylko lokalnie” zawsze dostaje wykonawcę lokalnego."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Toggle {
                            objectName: "caretakerEnabled"
                            checked: page.caretakerEnabled
                            accessibleName: "Opiekun karty"
                            onToggled: value => {
                                page.caretakerEnabled = value
                                page.caretakerDirty = true
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "Opiekun karty"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                        }
                    }
                    GridLayout {
                        Layout.fillWidth: true
                        columns: 2
                        columnSpacing: 8
                        rowSpacing: 6
                        Text {
                            text: "Wykonawca"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                        }
                        QQC2.ComboBox {
                            // Kółko myszy przewija panel, nie zmienia wyboru (03.11: przypadkowa zmiana wykonawcy).
                            wheelEnabled: false
                            objectName: "caretakerExecutor"
                            Layout.fillWidth: true
                            model: Logic.caretakerExecutorOptions(page.modelsStatus, page.caretakerExecutor)
                            textRole: "label"
                            valueRole: "value"
                            currentIndex: Math.max(0, model.findIndex(option => option.value === page.caretakerExecutor))
                            onActivated: index => {
                                page.caretakerExecutor = model[index].value
                                page.caretakerDirty = true
                            }
                            Accessible.name: "Wykonawca opiekuna karty"
                        }
                        Text {
                            text: "Limit działań"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                        }
                        QQC2.SpinBox {
                            objectName: "caretakerActionLimit"
                            wheelEnabled: false
                            editable: true
                            from: 1
                            to: 10
                            value: page.caretakerActionLimit
                            onValueModified: {
                                page.caretakerActionLimit = value
                                page.caretakerDirty = true
                            }
                            Accessible.name: "Limit działań opiekuna na problem"
                        }
                        Text {
                            text: "Minuty czekania"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                        }
                        QQC2.SpinBox {
                            objectName: "caretakerDecisionMinutes"
                            wheelEnabled: false
                            editable: true
                            from: 1
                            to: 1440
                            value: page.caretakerDecisionMinutes
                            onValueModified: {
                                page.caretakerDecisionMinutes = value
                                page.caretakerDirty = true
                            }
                            Accessible.name: "Minuty czekania decyzji na opiekuna"
                        }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        Item { Layout.fillWidth: true }
                        PillButton {
                            objectName: "caretakerSave"
                            text: "Zapisz"
                            enabled: page.caretakerDirty && !page.caretakerBusy
                            onClicked: page.saveCaretakerSettings()
                        }
                    }
                    Text {
                        objectName: "caretakerError"
                        Layout.fillWidth: true
                        visible: page.caretakerError !== ""
                        text: page.caretakerError
                        textFormat: Text.PlainText
                        color: Tide.Tokens.rose
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }

                    SectionTitle { text: "ZASADY PRZYDZIAŁU · WSPÓLNE" }
                    QQC2.TextArea {
                        id: globalRules
                        objectName: "globalAssignmentRules"
                        Layout.fillWidth: true
                        implicitHeight: 130
                        text: (page.snapshot && page.snapshot.assignmentRules) || ""
                        wrapMode: TextEdit.Wrap
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        Accessible.name: "Wspólne zasady przydziału"
                        background: Rectangle { radius: Tide.Tokens.radiusTile; color: Tide.Tokens.backgroundPanel }
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: page.assignmentError !== ""
                        text: page.assignmentError
                        textFormat: Text.PlainText
                        color: Tide.Tokens.rose
                        wrapMode: Text.Wrap
                    }
                    PillButton {
                        objectName: "globalAssignmentRulesSave"
                        text: "Zapisz wspólne zasady"
                        enabled: page.busyTag === ""
                        onClicked: page.send("rules", "global", { globalAssignmentRules: globalRules.text.trim() })
                    }
                    // --- YouTrack (PT-7) -------------------------------------------------------------
                    RowLayout {
                        Layout.fillWidth: true
                        visible: page.youtrack !== null
                        SectionTitle { Layout.fillWidth: true; text: "YOUTRACK" }
                        PillButton {
                            objectName: "importOpen"
                            implicitHeight: 28
                            pixelSize: 12
                            outline: true
                            text: "Importuj z YouTracka" + (page.youtrack && page.youtrack.newIssues > 0
                                                            ? " · " + page.youtrack.newIssues + " "
                                                              + Logic.plural(page.youtrack.newIssues, "nowe", "nowe", "nowych") : "")
                            toolTip: "Twoje zgłoszenia z YouTracka jako karty w Backlogu (bez duplikatów)"
                            onClicked: page.importOpen ? page.importOpen = false : page.openImport()
                        }
                    }
                    Text {
                        objectName: "youtrackStatus"
                        Layout.fillWidth: true
                        visible: page.youtrack !== null
                        text: Logic.youtrackSummary(page.youtrack, page.projects)
                        textFormat: Text.PlainText
                        color: page.youtrack && page.youtrack.enabled && !page.youtrack.available ? Tide.Tokens.butter
                                                                                                    : Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    ImportPanel {
                        id: importPanel
                        objectName: "importPanel"
                        Layout.fillWidth: true
                        visible: page.importOpen
                        onRefreshRequested: page.refreshImport()
                        onCloseRequested: page.importOpen = false
                        onImportRequested: keys => {
                            busy = true
                            result = ""
                            page.importRequested(page.nextTag("yt-import"), keys)
                        }
                    }
                    Repeater {
                        model: page.youtrack ? page.youtrack.conflictItems || [] : []
                        Rectangle {
                            id: conflictRow
                            required property var modelData
                            objectName: "youtrackConflict"
                            Layout.fillWidth: true
                            implicitHeight: conflictLayout.implicitHeight + 16
                            radius: 10
                            color: Tide.Theme.alpha(Tide.Tokens.rose, 0.08)
                            border.width: 1
                            border.color: Tide.Theme.alpha(Tide.Tokens.rose, 0.35)
                            RowLayout {
                                id: conflictLayout
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.leftMargin: 12
                                anchors.rightMargin: 8
                                spacing: 8
                                Text {
                                    Layout.fillWidth: true
                                    text: conflictRow.modelData.issue + " (" + conflictRow.modelData.label + "): w YouTracku „"
                                          + (conflictRow.modelData.youtrack || "bez stanu") + "”, Tide: „" + conflictRow.modelData.tide + "”"
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.rose
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 12
                                    wrapMode: Text.Wrap
                                }
                                PillButton {
                                    implicitHeight: 26
                                    pixelSize: 11
                                    outline: true
                                    text: "Weź z YouTracka"
                                    onClicked: page.resolveRequested(page.nextTag("yt-resolve"), conflictRow.modelData.issue, "youtrack")
                                }
                                PillButton {
                                    implicitHeight: 26
                                    pixelSize: 11
                                    outline: true
                                    text: "Nadpisz z Tide"
                                    onClicked: page.resolveRequested(page.nextTag("yt-resolve"), conflictRow.modelData.issue, "tide")
                                }
                            }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: page.youtrackError !== ""
                        text: page.youtrackError
                        textFormat: Text.PlainText
                        color: Tide.Tokens.rose
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }

                    SectionTitle { text: "PROJEKTY"; Layout.topMargin: page.youtrack !== null ? 14 : 0 }
                    Text {
                        Layout.fillWidth: true
                        visible: page.projects.length === 0
                        text: "Jeszcze nie ma projektów. Dodaj repozytorium poniżej albo wybierz je przy nowej karcie."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        wrapMode: Text.Wrap
                    }
                    Repeater {
                        id: projectRepeater
                        model: page.projects
                        ProjectRow {
                            required property var modelData
                            Layout.fillWidth: true
                            project: modelData
                            open: page.openProject === modelData.slug
                        }
                    }

                    SectionTitle {
                        Layout.topMargin: 14
                        text: "REPOZYTORIA BEZ PROJEKTU"
                        visible: page.repos.length > 0
                    }
                    Repeater {
                        model: page.repos
                        Rectangle {
                            id: repoRow
                            required property var modelData
                            Layout.fillWidth: true
                            implicitHeight: 44
                            radius: 12
                            color: Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.55)
                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 14
                                anchors.rightMargin: 8
                                spacing: 10
                                Text {
                                    text: repoRow.modelData.name
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textPrimary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 13
                                    font.weight: Font.Medium
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: Logic.shortPath(repoRow.modelData.path, page.homeDir)
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.monoFamily
                                    font.pixelSize: 11
                                    elide: Text.ElideMiddle
                                }
                                PillButton {
                                    implicitHeight: 28
                                    pixelSize: 12
                                    outline: true
                                    text: "＋ Dodaj jako projekt"
                                    enabled: page.busyTag === ""
                                    onClicked: page.send("repo", repoRow.modelData.path, { repo: repoRow.modelData.path })
                                }
                            }
                        }
                    }

                    SectionTitle { Layout.topMargin: 14; text: "PROJEKT BEZ REPOZYTORIUM" }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        LineInput {
                            id: newProject
                            objectName: "newProjectName"
                            Layout.fillWidth: true
                            placeholder: "Nazwa, np. dom albo nauka"
                            maximumLength: 80
                            onAccepted: addProject.clicked()
                        }
                        PillButton {
                            id: addProject
                            text: "Dodaj projekt"
                            enabled: page.busyTag === ""
                            onClicked: {
                                const name = newProject.text.trim()
                                if (!name) {
                                    page.repoError = "Podaj nazwę projektu."
                                    return
                                }
                                page.send("repo", name, { name: name })
                                newProject.text = ""
                            }
                        }
                    }
                    Text {
                        objectName: "repoError"
                        Layout.fillWidth: true
                        visible: page.repoError !== ""
                        text: page.repoError
                        textFormat: Text.PlainText
                        color: Tide.Tokens.rose
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                }
            }

            Rectangle {
                Layout.fillHeight: true
                implicitWidth: 1
                color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07)
            }

            // --- tagi ------------------------------------------------------------------------------
            Flickable {
                id: tagsFlick
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.preferredWidth: 2
                clip: true
                contentWidth: width
                contentHeight: tagsColumn.implicitHeight
                boundsBehavior: Flickable.StopAtBounds
                QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

                ColumnLayout {
                    id: tagsColumn
                    width: tagsFlick.width - 12
                    spacing: 8

                    RowLayout {
                        Layout.fillWidth: true
                        SectionTitle { Layout.fillWidth: true; text: "TAGI" }
                        PillButton {
                            objectName: "newTagButton"
                            implicitHeight: 28
                            pixelSize: 12
                            outline: true
                            text: "＋ Nowy tag"
                            onClicked: {
                                page.tagError = ""
                                page.openTag = page.openTag === "+" ? "" : "+"
                            }
                        }
                    }
                    Rectangle {
                        Layout.fillWidth: true
                        visible: page.openTag === "+"
                        radius: 12
                        color: Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.55)
                        implicitHeight: newTagEditor.implicitHeight + 24
                        TagEditor {
                            id: newTagEditor
                            anchors.fill: parent
                            anchors.margins: 12
                            pastels: page.pastels
                            taken: page.tags.map(t => t.color)
                            error: page.openTag === "+" ? page.tagError : ""
                            busy: page.busyTag !== ""
                            onSaveRequested: request => page.send("tag", "+", request)
                            onCancelRequested: page.openTag = ""
                            onVisibleChanged: if (visible) { reset(); focusName() }
                        }
                    }
                    Repeater {
                        model: page.tags
                        ColumnLayout {
                            id: tagItem
                            required property var modelData
                            readonly property bool open: page.openTag === modelData.name
                            Layout.fillWidth: true
                            spacing: 0
                            Rectangle {
                                Layout.fillWidth: true
                                implicitHeight: 44
                                radius: 12
                                color: tagArea.containsMouse || tagItem.open ? Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.8)
                                                                             : Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.55)
                                activeFocusOnTab: true
                                border.width: activeFocus ? 1 : 0
                                border.color: Tide.Tokens.accent
                                Accessible.role: Accessible.Button
                                Accessible.name: "Tag " + tagItem.modelData.name + ", " + Logic.cards(tagItem.modelData.cards)
                                Accessible.onPressAction: tagArea.clicked(null)
                                Keys.onReturnPressed: tagArea.clicked(null)
                                RowLayout {
                                    anchors.fill: parent
                                    anchors.leftMargin: 14
                                    anchors.rightMargin: 14
                                    spacing: 10
                                    Dot {
                                        colorOverride: page.tagTone ? page.tagTone(tagItem.modelData.name) : Tide.Tokens.textMuted
                                    }
                                    Text {
                                        text: tagItem.modelData.name
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textPrimary
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 13
                                        font.weight: Font.Medium
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: tagItem.modelData.defined ? tagItem.modelData.description || ""
                                                                        : "bez pliku — kolor z nazwy"
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textMuted
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 12
                                        elide: Text.ElideRight
                                    }
                                    Text {
                                        text: Logic.cards(tagItem.modelData.cards)
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textMuted
                                        font.family: Tide.Theme.monoFamily
                                        font.pixelSize: 11
                                    }
                                }
                                MouseArea {
                                    id: tagArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: {
                                        page.tagError = ""
                                        page.openTag = tagItem.open ? "" : tagItem.modelData.name
                                    }
                                }
                            }
                            Rectangle {
                                Layout.fillWidth: true
                                Layout.topMargin: 6
                                visible: tagItem.open
                                radius: 12
                                color: Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.55)
                                implicitHeight: tagEditor.implicitHeight + 24
                                TagEditor {
                                    id: tagEditor
                                    anchors.fill: parent
                                    anchors.margins: 12
                                    tag: tagItem.open ? tagItem.modelData : null
                                    pastels: page.pastels
                                    error: tagItem.open ? page.tagError : ""
                                    busy: page.busyTag !== ""
                                    onSaveRequested: request => page.send("tag", tagItem.modelData.name, request)
                                    onCancelRequested: page.openTag = ""
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // Wiersz projektu z formularzem pod spodem (otwiera się kliknięciem).
    component ProjectRow: ColumnLayout {
        id: row
        property var project: ({})
        property bool open: false
        property var tags: []
        property string aiDocs: ""
        property string tool: ""
        property string mode: ""
        property string entry: ""
        property bool youtrackSync: true
        property string reviewIndependence: "free"
        property string allowedCommands: ""
        property string deniedCommands: ""
        property string readDirs: ""
        property string testsChanged: ""
        property string testsFull: ""
        readonly property var savedTests: (project.tests || {})
        readonly property bool testsPending: project.testsApproved === false && !project.testsError
        // Odpowiedź SuggestProjectTests dla projektu (null, dopóki usługa nie odpowie).
        readonly property var suggestion: page.suggestions[project.slug] || null
        readonly property bool suggestionOk: suggestion !== null && suggestion.ok === true
        readonly property bool noSavedContract: !savedTests.changed && !savedTests.full
        readonly property bool hasSuggestion: suggestionOk && !!(suggestion.changed || suggestion.full)
        // Zmiana zapisanego kontraktu, zgody albo repozytorium odświeża źródło i podpowiedź z usługi.
        readonly property string contractKey: JSON.stringify([savedTests.changed || "", savedTests.full || "",
                                                              project.testsApproved, project.repo || "", !!project.repoMissing])
        onContractKeyChanged: if (open) page.requestSuggestion(project.slug)
        Component.onCompleted: if (open) { reset(); page.requestSuggestion(project.slug) }
        property alias youtrackKey: youtrackField.text
        readonly property bool youtrackToggleEnabled: youtrackToggle.enabled
        readonly property string youtrackSummary: youtrackLabel.visible ? youtrackLabel.text : ""

        function reset() {
            tags = (project.tags || []).slice()
            aiDocs = project.aiDocs || "tide"
            tool = project.tool || "claude"
            const d = Logic.launchDefaults(project)   // dawne mode: spec to wejście Tylko specyfikacja
            entry = d.entry
            mode = d.mode
            nameField.text = project.name || ""
            descriptionField.text = project.description || ""
            youtrackField.text = project.youtrack || ""
            youtrackSync = project.youtrack ? !!project.youtrackSync : true
            reviewIndependence = project.reviewIndependence || "free"
            reviewClassesField.text = (project.reviewIndependenceClasses || []).join(", ")
            allowedCommands = (project.allowedCommands || []).join("\n")
            deniedCommands = (project.deniedCommands || []).join("\n")
            allowedCommandsArea.text = allowedCommands
            deniedCommandsArea.text = deniedCommands
            readDirs = (project.readDirs || []).join("\n")
            readDirsArea.text = readDirs
            assignmentRulesArea.text = project.assignmentRules || ""
            testsChanged = savedTests.changed || ""
            testsFull = savedTests.full || ""
            testsChangedField.text = testsChanged
            testsFullField.text = testsFull
            // testsError nie jest tu czyszczony: odświeżenie migawki odtwarza delegaty i zgubiłoby odmowę zatwierdzenia.
        }

        // Bieżące pola kontraktu; wysyłane tylko z jawnej akcji użytkownika (zapis zmiany albo „Zatwierdź kontrakt”).
        function testsValue() {
            return { changed: testsChanged.trim(), full: testsFull.trim() }
        }

        // „Użyj podpowiedzi”: tylko wypełnia pola formularza; zapis i zatwierdzenie dopiero z „Zapisz” albo „Zatwierdź”.
        function useSuggestion() {
            if (!hasSuggestion) return
            testsChangedField.text = suggestion.changed || ""
            testsFullField.text = suggestion.full || ""
        }

        function approveTests() {
            page.send("tests", project.slug, { slug: project.slug, tests: testsValue() })
        }

        function commandLines(value) {
            return value.split("\n").map(s => s.trim()).filter(s => s.length > 0)
        }

        function save() {
            const request = { slug: project.slug }
            const name = nameField.text.trim()
            if (name && name !== project.name) request.name = name
            if (tags.join(",") !== (project.tags || []).join(",")) request.tags = tags
            if (aiDocs !== project.aiDocs || project.aiDocsDefault) request.aiDocs = aiDocs
            if (tool !== project.tool) request.tool = tool
            const d = Logic.launchDefaults(project)
            if (entry !== d.entry) request.entry = entry
            if (mode !== d.mode) request.mode = mode
            if (descriptionField.text.trim() !== (project.description || "")) request.description = descriptionField.text.trim()
            const key = youtrackField.text.trim().toUpperCase()
            if (key !== (project.youtrack || "")) request.youtrack = key
            if (key && youtrackSync !== (project.youtrack ? !!project.youtrackSync : true)) request.youtrackSync = youtrackSync
            if (reviewIndependence !== (project.reviewIndependence || "free")) request.reviewIndependence = reviewIndependence
            const reviewClasses = reviewClassesField.text.split(",").map(s => s.trim().toLowerCase()).filter(s => s.length > 0)
            if (JSON.stringify(reviewClasses) !== JSON.stringify(project.reviewIndependenceClasses || []))
                request.reviewIndependenceClasses = reviewClasses
            const allowed = commandLines(allowedCommands)
            const denied = commandLines(deniedCommands)
            if (JSON.stringify(allowed) !== JSON.stringify(project.allowedCommands || [])) request.allowedCommands = allowed
            if (JSON.stringify(denied) !== JSON.stringify(project.deniedCommands || [])) request.deniedCommands = denied
            const dirs = commandLines(readDirs)
            if (JSON.stringify(dirs) !== JSON.stringify(project.readDirs || [])) request.readDirs = dirs
            if (assignmentRulesArea.text.trim() !== (project.assignmentRules || "")) request.assignmentRules = assignmentRulesArea.text.trim()
            const tests = testsValue()
            if (tests.changed !== (savedTests.changed || "") || tests.full !== (savedTests.full || "")) request.tests = tests
            page.send("project", project.slug, request)
        }

        onOpenChanged: if (open) { reset(); page.requestSuggestion(project.slug) }
        spacing: 0

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: info.implicitHeight + 22
            radius: 12
            color: area.containsMouse || row.open ? Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.8)
                                                  : Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.55)
            activeFocusOnTab: true
            border.width: activeFocus ? 1 : 0
            border.color: Tide.Tokens.accent
            Accessible.role: Accessible.Button
            Accessible.name: "Projekt " + (row.project.name || "")
            Accessible.onPressAction: area.clicked(null)
            Keys.onReturnPressed: area.clicked(null)

            ColumnLayout {
                id: info
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 14
                anchors.rightMargin: 14
                spacing: 5
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        text: row.project.name || ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }
                    Repeater {
                        model: row.project.tags || []
                        TagChip {
                            required property var modelData
                            name: modelData
                            dotColor: page.tagTone ? page.tagTone(modelData) : Tide.Tokens.textMuted
                        }
                    }
                    Item { Layout.fillWidth: true }
                    Text {
                        text: Logic.cards(row.project.cards || 0)
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 11
                    }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        Layout.fillWidth: true
                        text: row.project.repo ? Logic.shortPath(row.project.repo, page.homeDir)
                                                 + (row.project.repoMissing ? " (nie ma katalogu)" : "")
                                               : "bez repozytorium"
                        textFormat: Text.PlainText
                        color: row.project.repoMissing ? Tide.Tokens.rose : Tide.Tokens.textMuted
                        font.family: row.project.repo ? Tide.Theme.monoFamily : Tide.Theme.uiFamily
                        font.pixelSize: 11
                        elide: Text.ElideMiddle
                    }
                    Text {
                        id: youtrackLabel
                        objectName: "projectYouTrack"
                        visible: !!row.project.youtrack
                        text: "YouTrack " + (row.project.youtrack || "") + (row.project.youtrackSync ? " ⟲" : " · bez synchronizacji")
                        textFormat: Text.PlainText
                        color: row.project.youtrackSync ? Tide.Tokens.sky : Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                    }
                    Text {
                        text: (row.project.aiDocs === "repo" ? "dokumenty AI w repozytorium" : "dokumenty AI w tablicy")
                              + " · " + Logic.toolName(row.project.tool) + " · "
                              + (Logic.launchDefaults(row.project).entry === "spec" ? "tylko specyfikacja"
                                 : ({ full: "planowanie, pełny", light: "planowanie, lekki" }[Logic.launchDefaults(row.project).mode] || ""))
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                    }
                }
            }
            MouseArea {
                id: area
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: {
                    page.projectError = ""
                    page.testsError = ""
                    page.openProject = row.open ? "" : row.project.slug
                }
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.topMargin: 6
            visible: row.open
            radius: 12
            color: Tide.Theme.alpha(Tide.Tokens.backgroundPanel, 0.55)
            implicitHeight: form.implicitHeight + 28

            GridLayout {
                id: form
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: 14
                columns: 2
                columnSpacing: 14
                rowSpacing: 10

                FieldLabel { text: "Nazwa" }
                LineInput {
                    id: nameField
                    objectName: "projectName"
                    Layout.fillWidth: true
                    maximumLength: 80
                    onAccepted: row.save()
                }
                FieldLabel { text: "Tagi"; Layout.alignment: Qt.AlignTop; Layout.topMargin: 4 }
                Flow {
                    Layout.fillWidth: true
                    spacing: 6
                    Repeater {
                        model: page.tags
                        TagChip {
                            required property var modelData
                            name: modelData.name
                            dotColor: page.tagTone ? page.tagTone(modelData.name) : Tide.Tokens.textMuted
                            selectable: true
                            selected: Logic.hasTag(row.tags, modelData.name)
                            onClicked: row.tags = Logic.toggleTag(row.tags, modelData.name)
                        }
                    }
                }
                FieldLabel { text: "Dokumenty AI" }
                Segmented {
                    itemHeight: 28
                    options: [{ value: "repo", label: "w repozytorium (docs/agents)" }, { value: "tide", label: "w tablicy" }]
                    current: row.aiDocs
                    onPicked: value => row.aiDocs = value
                }
                FieldLabel { text: "Narzędzie" }
                Segmented {
                    itemHeight: 28
                    options: [{ value: "claude", label: "Claude" }, { value: "codex", label: "Codex" }]
                    current: row.tool
                    onPicked: value => row.tool = value
                }
                FieldLabel { text: "Domyślne wejście" }
                Segmented {
                    objectName: "projectEntry"
                    itemHeight: 28
                    options: [{ value: "planning", label: "Planowanie" }, { value: "spec", label: "Tylko specyfikacja" }]
                    current: row.entry
                    onPicked: value => row.entry = value
                }
                FieldLabel { text: "Tryb"; visible: Logic.entryHasModes(row.entry) }
                Segmented {
                    objectName: "projectMode"
                    visible: Logic.entryHasModes(row.entry)
                    itemHeight: 28
                    options: [{ value: "full", label: "Pełny" }, { value: "light", label: "Lekki" }]
                    current: row.mode
                    onPicked: value => row.mode = value
                }
                FieldLabel { text: "YouTrack" }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    LineInput {
                        id: youtrackField
                        objectName: "projectYouTrackKey"
                        implicitWidth: 110
                        mono: true
                        placeholder: "np. LOG"
                        accessibleName: "Klucz projektu w YouTracku"
                        maximumLength: 20
                        invalid: text.trim() !== "" && !/^[A-Za-z][A-Za-z0-9_]{0,19}$/.test(text.trim())
                        onAccepted: row.save()
                    }
                    Toggle {
                        id: youtrackToggle
                        objectName: "projectYouTrackSync"
                        enabled: youtrackField.text.trim() !== ""
                        checked: row.youtrackSync && enabled
                        accessibleName: "Synchronizuj stan zgłoszeń z YouTrackiem"
                        onToggled: value => row.youtrackSync = value
                    }
                    Text {
                        Layout.fillWidth: true
                        text: youtrackField.text.trim() === "" ? "bez klucza — karty i tickety nie zmieniają YouTracka"
                            : row.youtrackSync ? "stan kart i ticketów idzie do YouTracka"
                            : "tylko odnośniki, bez zmiany stanu"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                }
                // Polityka niezależności review projektu (K-88/PT-330, decyzja 96 z 10.11); zawsze nowa sesja recenzenta.
                FieldLabel { text: "Recenzent" }
                Segmented {
                    objectName: "projectReviewIndependence"
                    itemHeight: 28
                    options: [{ value: "free", label: "Dowolny" }, { value: "other-model", label: "Inny model" },
                              { value: "other-family", label: "Inna rodzina" }]
                    current: row.reviewIndependence
                    onPicked: value => row.reviewIndependence = value
                }
                FieldLabel { text: "Klasy"; visible: row.reviewIndependence === "other-family" }
                RowLayout {
                    visible: row.reviewIndependence === "other-family"
                    Layout.fillWidth: true
                    spacing: 10
                    LineInput {
                        id: reviewClassesField
                        objectName: "projectReviewClasses"
                        implicitWidth: 180
                        placeholder: "np. mocny"
                        accessibleName: "Klasy ticketów z wymogiem innej rodziny recenzenta"
                        onAccepted: row.save()
                    }
                    Text {
                        Layout.fillWidth: true
                        text: reviewClassesField.text.trim() === "" ? "wymóg dla wszystkich klas ticketów"
                            : "tylko te klasy; pozostałe — dowolny recenzent"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                }
                FieldLabel { text: "Opis" }
                LineInput {
                    id: descriptionField
                    Layout.fillWidth: true
                    placeholder: "Krótko, czego dotyczy projekt"
                    onAccepted: row.save()
                }
                FieldLabel { text: "Zasady przydziału"; Layout.alignment: Qt.AlignTop; Layout.topMargin: 6 }
                QQC2.TextArea {
                    id: assignmentRulesArea
                    objectName: "projectAssignmentRules"
                    Layout.fillWidth: true
                    implicitHeight: 100
                    placeholderText: "Puste pole: dziedzicz wspólne zasady. Klasy: lekki, standard, mocny, lokalny."
                    wrapMode: TextEdit.Wrap
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    Accessible.name: "Zasady przydziału projektu, puste dziedziczą wspólne"
                    background: Rectangle { radius: Tide.Tokens.radiusTile; color: Tide.Tokens.backgroundPanel }
                }
                FieldLabel { text: "Katalogi tylko do odczytu"; Layout.alignment: Qt.AlignTop; Layout.topMargin: 6 }
                QQC2.TextArea {
                    id: readDirsArea
                    objectName: "projectReadDirs"
                    Layout.fillWidth: true
                    implicitHeight: 90
                    text: row.readDirs
                    onTextChanged: row.readDirs = text
                    placeholderText: "Np. ~/.cache/hud-timeline — po jednym katalogu w wierszu, maksymalnie 32"
                    wrapMode: TextEdit.NoWrap
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 12
                    Accessible.name: "Katalogi projektu tylko do odczytu, po jednym w wierszu"
                    background: Rectangle { radius: Tide.Tokens.radiusTile; color: Tide.Tokens.backgroundPanel }
                }
                Text {
                    Layout.columnSpan: 2
                    Layout.fillWidth: true
                    text: "Dane udostępniane krokom tego projektu i krokom uprawnionym do czytania go jako projektu dodatkowego. Zapis, usuwanie i zmiana nazwy są zabronione. Zmiana listy działa od następnego startu lub wznowienia, także w Konsoli; działający proces zachowuje swój profil."
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }
                FieldLabel { text: "Dozwolone polecenia"; Layout.alignment: Qt.AlignTop; Layout.topMargin: 6 }
                QQC2.TextArea {
                    id: allowedCommandsArea
                    objectName: "projectAllowedCommands"
                    Layout.fillWidth: true
                    implicitHeight: 116
                    text: row.allowedCommands
                    onTextChanged: row.allowedCommands = text
                    wrapMode: TextEdit.NoWrap
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 12
                    Accessible.name: "Dozwolone polecenia projektu, po jednym w wierszu"
                    background: Rectangle { radius: 6; color: Tide.Tokens.backgroundPanel }
                }
                FieldLabel { text: "Zabronione polecenia"; Layout.alignment: Qt.AlignTop; Layout.topMargin: 6 }
                QQC2.TextArea {
                    id: deniedCommandsArea
                    objectName: "projectDeniedCommands"
                    Layout.fillWidth: true
                    implicitHeight: 68
                    text: row.deniedCommands
                    onTextChanged: row.deniedCommands = text
                    wrapMode: TextEdit.NoWrap
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 12
                    Accessible.name: "Zabronione polecenia projektu, po jednym w wierszu"
                    background: Rectangle { radius: 6; color: Tide.Tokens.backgroundPanel }
                }
                Text {
                    Layout.columnSpan: 2
                    Layout.fillWidth: true
                    text: "Claude stosuje listy poleceń w uprawnieniach CLI. Dla Codexa są wskazówką w kartce; jego piaskownica ogranicza zapis, ale nie blokuje każdego polecenia spoza listy."
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 11
                    wrapMode: Text.Wrap
                }
                FieldLabel { text: "Testy projektu"; Layout.alignment: Qt.AlignTop; Layout.topMargin: 6 }
                ColumnLayout {
                    objectName: "projectTests"
                    Layout.fillWidth: true
                    spacing: 6
                    Text {
                        objectName: "projectTestsSource"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: Logic.contractSourceText(row.suggestion)
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    Text {
                        objectName: "projectTestsUnapproved"
                        Layout.fillWidth: true
                        visible: row.suggestionOk && row.suggestion.unapproved === true
                        text: "Zapisany kontrakt nie jest zatwierdzony, więc nie obowiązuje — zatwierdź go albo zapisz zmiany."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.butter
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "Testy dobrane do zmian"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                    }
                    LineInput {
                        id: testsChangedField
                        objectName: "projectTestsChanged"
                        Layout.fillWidth: true
                        mono: true
                        placeholder: "np. make test-changed BASE={base}"
                        accessibleName: "Testy dobrane do zmian"
                        onTextChanged: row.testsChanged = text
                        onAccepted: row.save()
                    }
                    Text {
                        Layout.fillWidth: true
                        text: "{base} to przypięty punkt startu ticketu na gałęzi karty; Tide podstawia go przy uruchomieniu."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    Text {
                        objectName: "projectTestsChangedNote"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: (row.project.testsGrantNote || {}).changed || ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.butter
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    Text {
                        Layout.fillWidth: true
                        Layout.topMargin: 4
                        text: "Pełne testy przy domknięciu"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                    }
                    LineInput {
                        id: testsFullField
                        objectName: "projectTestsFull"
                        Layout.fillWidth: true
                        mono: true
                        placeholder: "np. make test"
                        accessibleName: "Pełne testy przy domknięciu"
                        onTextChanged: row.testsFull = text
                        onAccepted: row.save()
                    }
                    Text {
                        objectName: "projectTestsFullNote"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: (row.project.testsGrantNote || {}).full || ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.butter
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    Text {
                        objectName: "projectTestsEmptyHint"
                        Layout.fillWidth: true
                        visible: row.testsChanged.trim() === "" && row.testsFull.trim() === ""
                        text: "Bez kontraktu kartki odeślą do zasad projektu (CLAUDE.md, AGENTS.md) albo użyją celów wykrytych z Makefile."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        objectName: "projectTestsSuggestion"
                        Layout.fillWidth: true
                        visible: row.noSavedContract && row.hasSuggestion
                        spacing: 8
                        Text {
                            objectName: "projectTestsSuggestionText"
                            Layout.fillWidth: true
                            text: "Podpowiedź z repozytorium (Makefile): "
                                  + (row.hasSuggestion ? [row.suggestion.changed, row.suggestion.full].filter(s => s).join(" · ") : "")
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textSecondary
                            font.family: Tide.Theme.monoFamily
                            font.pixelSize: 11
                            wrapMode: Text.Wrap
                        }
                        PillButton {
                            objectName: "projectTestsUseSuggestion"
                            implicitHeight: 30
                            pixelSize: 12
                            outline: true
                            enabled: row.hasSuggestion && (row.testsChanged !== (row.suggestion.changed || "")
                                                           || row.testsFull !== (row.suggestion.full || ""))
                            text: "Użyj podpowiedzi"
                            onClicked: row.useSuggestion()
                        }
                    }
                    Text {
                        objectName: "projectTestsNoSuggestion"
                        Layout.fillWidth: true
                        visible: row.noSavedContract && row.suggestionOk && !row.hasSuggestion
                        text: row.suggestionOk ? (row.suggestion.message || "") : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    Text {
                        objectName: "projectTestsProfileError"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: row.project.testsError || ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.rose
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    RowLayout {
                        objectName: "projectTestsPending"
                        Layout.fillWidth: true
                        visible: row.testsPending
                        spacing: 8
                        Text {
                            objectName: "projectTestsWarning"
                            Layout.fillWidth: true
                            text: "kontrakt zmieniony poza oknem projektu — sprawdź i zatwierdź"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.butter
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                            wrapMode: Text.Wrap
                        }
                        PillButton {
                            objectName: "projectTestsApprove"
                            implicitHeight: 30
                            pixelSize: 12
                            enabled: page.busyTag === ""
                            text: "Zatwierdź kontrakt"
                            onClicked: row.approveTests()
                        }
                    }
                    Text {
                        objectName: "projectTestsError"
                        Layout.fillWidth: true
                        visible: text !== ""
                        text: row.open ? page.testsError : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.rose
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                }
                Item { implicitWidth: 1 }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    Text {
                        objectName: "projectError"
                        Layout.fillWidth: true
                        text: row.open ? page.projectError : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.rose
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    PillButton {
                        implicitHeight: 30
                        pixelSize: 12
                        flat: true
                        text: "Anuluj"
                        onClicked: { page.testsError = ""; page.openProject = "" }
                    }
                    PillButton {
                        objectName: "projectSave"
                        implicitHeight: 30
                        pixelSize: 12
                        strong: true
                        enabled: page.busyTag === ""
                        text: "Zapisz projekt"
                        fill: Tide.Tokens.accent
                        textColor: Tide.Tokens.backgroundDesktop
                        onClicked: row.save()
                    }
                }
            }
        }
    }
}

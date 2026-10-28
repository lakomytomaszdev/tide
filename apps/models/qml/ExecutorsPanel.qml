// Wykonawcy i dostawcy w widoku Modele (PT-60): „Gdzie idzie praca” (lokalnie czy chmura — role modeli
// i wykonawcy, wg stanu usługi), katalog wykonawców (dodaj, zmień, usuń) i inni dostawcy (punkt zgodny z API
// OpenAI albo Anthropic, token w pliku — okno nigdy nie zna zapisanego tokenu). Sam nie rozmawia z usługą:
// zapisy idą przez page.send(metoda, JSON, tag), wyniki wracają do notes[tag].
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "models.js" as Logic

ColumnLayout {
    id: panel

    property var status: null
    property var page: null
    property string editingExecutor: ""       // nazwa wykonawcy w edycji; "+" — nowy
    property string editingProvider: ""       // nazwa dostawcy w edycji; "+" — nowy
    property string removingExecutor: ""
    property var problem: null                // {field, text} z ostatniej próby
    property var classes: []
    property string exProvider: "anthropic"
    property string exProgram: "claude"
    property string exEndpoint: ""
    property string apiKind: "openai"
    property bool clearToken: false
    property bool providerAuth: false

    readonly property var rows: status && status.placement ? status.placement : []
    readonly property var list: Logic.executors(status)
    readonly property var others: Logic.otherProviders(status)

    function note(tag) {
        const n = page ? page.notes[tag] : null
        return n && n.error ? n.text : ""
    }

    function openExecutor(name) {
        const e = name === "+" ? null : Logic.executorByName(status, name)
        problem = null
        removingExecutor = ""
        exName.text = e ? e.name : ""
        exModel.text = e && e.modelSet ? e.model : ""
        exProvider = e ? e.provider : "anthropic"
        exProgram = e ? (e.programSet ? e.program : "") : "claude"
        exEndpoint = e && e.endpointSet ? e.endpoint : ""
        classes = e ? e.classes.slice() : []
        editingExecutor = name
    }

    function toggleClass(c) {
        const next = classes.slice()
        const i = next.indexOf(c)
        if (i >= 0) next.splice(i, 1)
        else next.push(c)
        classes = next
    }

    function saveExecutor() {
        const form = { name: exName.text, model: exModel.text, provider: exProvider, program: exProgram,
                       endpoint: exEndpoint, classes: classes }
        problem = Logic.executorProblem(form, status, editingExecutor === "+")
        if (problem === null)
            page.send("SetExecutor", Logic.executorRequest(form, editingExecutor === "+" ? "" : editingExecutor),
                      "executor:save")
    }

    function openProvider(name) {
        const p = name === "+" ? null : status.providers[name]
        problem = null
        prName.text = p ? name : ""
        prLabel.text = p ? p.label : ""
        prUrl.text = p ? p.url : ""
        prToken.text = ""
        apiKind = p ? p.api : "openai"
        providerAuth = p ? !!p.auth : false
        clearToken = false
        editingProvider = name
    }

    function saveProvider() {
        const form = { name: prName.text, label: prLabel.text, url: prUrl.text, api: apiKind, token: prToken.text,
                       clearToken: clearToken }
        problem = Logic.providerProblem(form, status, editingProvider === "+")
        if (problem === null) page.send("SetProvider", Logic.providerRequest(form), "provider:save")
    }

    // Po udanym zapisie formularze się zamykają (ModelsPage.finished).
    function done(tag, ok) {
        if (!ok) return
        if (tag === "executor:save") editingExecutor = ""
        else if (tag === "provider:save") { editingProvider = ""; prToken.text = "" }
        else if (tag.indexOf("executor:") === 0 && tag.endsWith(":remove")) removingExecutor = ""
    }

    spacing: 10

    // --- gdzie idzie praca ---------------------------------------------------------------------
    SectionTitle { text: "Gdzie idzie praca" }
    Note { Layout.fillWidth: true; text: "Lokalnie zostaje na twoim sprzęcie; „Chmura” wychodzi do dostawcy. Wykonawca lokalny nigdy nie przechodzi do chmury." }
    Repeater {
        model: panel.rows
        RowLayout {
            id: placeRow
            required property var modelData
            objectName: "placement_" + modelData.id
            Layout.fillWidth: true
            spacing: 8
            Text {
                objectName: "placementWhere"
                Layout.preferredWidth: 78
                Layout.alignment: Qt.AlignTop
                text: Logic.whereLabel(placeRow.modelData.where)
                textFormat: Text.PlainText
                color: placeRow.modelData.where === "local" ? Tide.Tokens.positive
                     : placeRow.modelData.where === "cloud" ? Tide.Tokens.accent : Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                font.weight: Font.DemiBold
            }
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 1
                Text {
                    Layout.fillWidth: true
                    text: placeRow.modelData.title
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    elide: Text.ElideRight
                }
                Text {
                    objectName: "placementDetail"
                    Layout.fillWidth: true
                    text: placeRow.modelData.detail
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }
                Note {
                    objectName: "placementError"
                    Layout.fillWidth: true
                    tone: "warn"
                    text: placeRow.modelData.ok ? "" : "● niedostępny" + (placeRow.modelData.error ? ": " + placeRow.modelData.error : "")
                }
            }
        }
    }

    // --- katalog wykonawców --------------------------------------------------------------------
    Rectangle { Layout.fillWidth: true; Layout.topMargin: 6; implicitHeight: 1; color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07) }
    SectionTitle { text: "Wykonawcy" }
    Note {
        objectName: "catalogOld"
        Layout.fillWidth: true
        tone: "warn"
        text: Logic.catalogAvailable(panel.status) ? "" : "Ta wersja usługi nie ma katalogu wykonawców — zaktualizuj usługę Tide (make install-daemon)."
    }
    Repeater {
        model: panel.list
        ColumnLayout {
            id: exRow
            required property var modelData
            objectName: "executor_" + modelData.name
            Layout.fillWidth: true
            spacing: 2
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    objectName: "executorName"
                    Layout.fillWidth: true
                    text: exRow.modelData.name
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                LinkText { objectName: "executorEdit"; text: "Zmień"; onClicked: panel.openExecutor(exRow.modelData.name) }
                LinkText {
                    objectName: "executorRemove"
                    text: "Usuń"
                    tint: Tide.Tokens.negative
                    onClicked: { panel.removingExecutor = exRow.modelData.name }
                }
            }
            Text {
                objectName: "executorLine"
                Layout.fillWidth: true
                text: Logic.executorLine(exRow.modelData) + " · " + Logic.whereLabel(exRow.modelData.where).toLowerCase()
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Text {
                Layout.fillWidth: true
                text: Logic.classesText(exRow.modelData)
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            RowLayout {
                objectName: "executorRemoveConfirm"
                visible: panel.removingExecutor === exRow.modelData.name
                spacing: 8
                Text {
                    text: "Usunąć z katalogu?"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.neutral
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                }
                LinkText {
                    objectName: "executorRemoveYes"
                    text: "Usuń"
                    tint: Tide.Tokens.negative
                    onClicked: panel.page.send("RemoveExecutor", exRow.modelData.name, "executor:" + exRow.modelData.name + ":remove")
                }
                LinkText { text: "Anuluj"; onClicked: panel.removingExecutor = "" }
            }
            Note {
                Layout.fillWidth: true
                tone: "error"
                text: panel.note("executor:" + exRow.modelData.name + ":remove")
            }
        }
    }
    LinkText {
        objectName: "executorAdd"
        visible: Logic.catalogAvailable(panel.status) && panel.editingExecutor === ""
        text: "+ Dodaj wykonawcę"
        onClicked: panel.openExecutor("+")
    }

    ColumnLayout {
        objectName: "executorForm"
        Layout.fillWidth: true
        visible: panel.editingExecutor !== ""
        spacing: 6
        FieldLabel { text: panel.editingExecutor === "+" ? "Nowy wykonawca — nazwa" : "Nazwa" }
        LineInput {
            id: exName
            objectName: "exName"
            Layout.fillWidth: true
            placeholder: "np. Sonnet"
            accessibleName: "Nazwa wykonawcy"
            maximumLength: 60
            invalid: panel.problem !== null && panel.problem.field === "name"
            onEdited: panel.problem = null
            onAccepted: panel.saveExecutor()
            onEscaped: panel.editingExecutor = ""
        }
        FieldLabel { text: "Dostawca" }
        Segmented {
            objectName: "exProvider"
            options: Logic.providerOptions(panel.status)
            current: panel.exProvider
            itemHeight: 26
            onPicked: value => {
                panel.exProvider = value
                const fixed = Logic.programFor(panel.status, value)
                panel.exProgram = fixed !== "" ? fixed : (panel.exProgram || "codex")
            }
        }
        FieldLabel { text: "Program" }
        Segmented {
            objectName: "exProgram"
            options: [{ value: "claude", label: "Claude Code" }, { value: "codex", label: "Codex" }]
            current: panel.exProgram
            itemHeight: 26
            enabled: Logic.programFor(panel.status, panel.exProvider) === ""
            onPicked: value => panel.exProgram = value
        }
        FieldLabel { text: panel.exProvider === "local" ? "Model (puste — model agenta lokalnego)" : "Model" }
        LineInput {
            id: exModel
            objectName: "exModel"
            Layout.fillWidth: true
            mono: true
            placeholder: panel.exProvider === "local" ? "jak rola Agent" : "np. sonnet"
            accessibleName: "Model wykonawcy"
            invalid: panel.problem !== null && panel.problem.field === "model"
            onEdited: panel.problem = null
            onAccepted: panel.saveExecutor()
            onEscaped: panel.editingExecutor = ""
        }
        FieldLabel { visible: panel.exProvider === "local"; text: "Serwer" }
        Segmented {
            objectName: "exEndpoint"
            visible: panel.exProvider === "local"
            options: [{ value: "", label: "jak Agent" }].concat(panel.status && panel.status.endpoints
                     ? Object.keys(panel.status.endpoints).map(n => ({ value: n, label: panel.status.endpoints[n].label || n })) : [])
            current: panel.exEndpoint
            itemHeight: 26
            onPicked: value => panel.exEndpoint = value
        }
        FieldLabel { text: "Klasy, które obsługuje" }
        Flow {
            Layout.fillWidth: true
            spacing: 6
            Repeater {
                model: Logic.classOptions()
                PillButton {
                    required property var modelData
                    objectName: "class_" + modelData.value
                    text: (panel.classes.indexOf(modelData.value) >= 0 ? "✓ " : "") + modelData.label
                    implicitHeight: 28
                    fill: panel.classes.indexOf(modelData.value) >= 0 ? Tide.Theme.alpha(Tide.Tokens.accent, 0.2) : Tide.Tokens.backgroundCard
                    onClicked: panel.toggleClass(modelData.value)
                }
            }
        }
        Note { objectName: "exProblem"; Layout.fillWidth: true; tone: "error"; text: panel.problem && panel.editingExecutor !== "" ? panel.problem.text : "" }
        Note { objectName: "exSaveNote"; Layout.fillWidth: true; tone: "error"; text: panel.note("executor:save") }
        RowLayout {
            spacing: 8
            Item { Layout.fillWidth: true }
            PillButton { text: "Anuluj"; implicitHeight: 32; onClicked: panel.editingExecutor = "" }
            PillButton {
                objectName: "exSave"
                text: panel.page && panel.page.busy["executor:save"] ? "Zapisuję…" : "Zapisz"
                implicitHeight: 32
                strong: true
                fill: Tide.Tokens.accent
                textColor: Tide.Tokens.backgroundDesktop
                onClicked: panel.saveExecutor()
            }
        }
    }

    // --- inni dostawcy -------------------------------------------------------------------------
    Rectangle { Layout.fillWidth: true; Layout.topMargin: 6; implicitHeight: 1; color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07) }
    SectionTitle { text: "Inni dostawcy" }
    Note {
        Layout.fillWidth: true
        text: "Punkt zgodny z API OpenAI (pracuje w nim Codex) albo Anthropic (Claude Code). Token trafia do pliku obok ustawień, nigdy do poleceń agenta. Dostawcę wybierasz potem jako preferowanego."
    }
    Repeater {
        model: panel.others
        ColumnLayout {
            id: prRow
            required property var modelData
            objectName: "provider_" + modelData.name
            Layout.fillWidth: true
            spacing: 2
            RowLayout {
                Layout.fillWidth: true
                spacing: 8
                Text {
                    Layout.fillWidth: true
                    text: prRow.modelData.label + (prRow.modelData.label !== prRow.modelData.name ? " (" + prRow.modelData.name + ")" : "")
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                }
                LinkText { objectName: "providerEdit"; text: "Zmień"; onClicked: panel.openProvider(prRow.modelData.name) }
                LinkText {
                    objectName: "providerRemove"
                    text: "Usuń"
                    tint: Tide.Tokens.negative
                    onClicked: panel.page.send("RemoveProvider", prRow.modelData.name, "provider:" + prRow.modelData.name + ":remove")
                }
            }
            Text {
                Layout.fillWidth: true
                text: prRow.modelData.url + " · API " + (prRow.modelData.api === "openai" ? "OpenAI" : "Anthropic")
                      + (prRow.modelData.auth ? " · token zapisany" : " · bez tokenu")
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Note { Layout.fillWidth: true; tone: "error"; text: panel.note("provider:" + prRow.modelData.name + ":remove") }
        }
    }
    LinkText {
        objectName: "providerAdd"
        visible: Logic.catalogAvailable(panel.status) && panel.editingProvider === ""
        text: "+ Dodaj dostawcę"
        onClicked: panel.openProvider("+")
    }
    ColumnLayout {
        objectName: "providerForm"
        Layout.fillWidth: true
        visible: panel.editingProvider !== ""
        spacing: 6
        FieldLabel { text: "Nazwa" }
        LineInput {
            id: prName
            objectName: "prName"
            Layout.fillWidth: true
            enabled: panel.editingProvider === "+"
            placeholder: "np. groq"
            accessibleName: "Nazwa dostawcy"
            maximumLength: 40
            invalid: panel.problem !== null && panel.problem.field === "name" && panel.editingProvider !== ""
            onEdited: panel.problem = null
            onEscaped: panel.editingProvider = ""
        }
        FieldLabel { text: "Nazwa do wyświetlania (opcjonalnie)" }
        LineInput { id: prLabel; objectName: "prLabel"; Layout.fillWidth: true; placeholder: "np. Groq"; accessibleName: "Etykieta dostawcy"; maximumLength: 60; onEscaped: panel.editingProvider = "" }
        FieldLabel { text: "Adres punktu" }
        LineInput {
            id: prUrl
            objectName: "prUrl"
            Layout.fillWidth: true
            mono: true
            placeholder: "https://api.example.com"
            accessibleName: "Adres dostawcy"
            invalid: panel.problem !== null && panel.problem.field === "url"
            onEdited: panel.problem = null
            onEscaped: panel.editingProvider = ""
        }
        FieldLabel { text: "Zgodny z API" }
        Segmented {
            objectName: "prApi"
            options: [{ value: "openai", label: "OpenAI" }, { value: "anthropic", label: "Anthropic" }]
            current: panel.apiKind
            itemHeight: 26
            onPicked: value => panel.apiKind = value
        }
        FieldLabel { text: panel.providerAuth ? "Token (zapisany)" : "Token (opcjonalnie)" }
        LineInput {
            id: prToken
            objectName: "prToken"
            Layout.fillWidth: true
            mono: true
            echoMode: TextInput.Password
            enabled: !panel.clearToken
            placeholder: panel.providerAuth ? "zostaw puste — bez zmiany" : "bez tokenu"
            accessibleName: "Token dostawcy"
            onEscaped: panel.editingProvider = ""
        }
        LinkText {
            objectName: "prClearToken"
            visible: panel.providerAuth
            text: panel.clearToken ? "Cofnij — zostaw zapisany token" : "Usuń token"
            tint: panel.clearToken ? Tide.Tokens.accent : Tide.Tokens.negative
            onClicked: { panel.clearToken = !panel.clearToken; if (panel.clearToken) prToken.text = "" }
        }
        Note { objectName: "prProblem"; Layout.fillWidth: true; tone: "error"; text: panel.problem && panel.editingProvider !== "" ? panel.problem.text : "" }
        Note { objectName: "prSaveNote"; Layout.fillWidth: true; tone: "error"; text: panel.note("provider:save") }
        RowLayout {
            spacing: 8
            Item { Layout.fillWidth: true }
            PillButton { text: "Anuluj"; implicitHeight: 32; onClicked: panel.editingProvider = "" }
            PillButton {
                objectName: "prSave"
                text: panel.page && panel.page.busy["provider:save"] ? "Zapisuję…" : "Zapisz"
                implicitHeight: 32
                strong: true
                fill: Tide.Tokens.accent
                textColor: Tide.Tokens.backgroundDesktop
                onClicked: panel.saveProvider()
            }
        }
    }
}

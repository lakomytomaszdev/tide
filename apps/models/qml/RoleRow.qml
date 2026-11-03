// Rola modelu (Raport, Opisy kart, Rozpoznawanie pytań, Agent, Wyszukiwanie): wybór modelu z zainstalowanych na serwerze roli, ostrzeżenie,
// gdy wybranego modelu tam nie ma (z „Pobierz teraz”), i „Zaawansowane”: przypięcie do serwera albo
// „za domyślnym” oraz kontekst (puste — domyślny). Każda zmiana to SetRole z serwerem przypięcia (bez niego
// usługa odpina rolę); wynik obok kontrolki. readOnly — usługa nie podaje roles.*.pinned: bez zgadywania,
// wiersz tylko do odczytu. Wpisany, niezapisany kontekst nie wraca do zapisu przy nowym Status().
// Wiersz „Agent” ma też program agenta na modelu lokalnym (Codex / Claude Code, SetAgentProgram) — jeden
// dla wszystkich serwerów; okno „Nowe zadanie” w trybie „Lokalny” bierze go stąd.
// Wiersz „Wyszukiwanie” (rola `embed`, PT-19) ma przełącznik Włączone / Wyłączone (SetSemanticSearch) i jedną
// linię stanu indeksu KRunnera (Status().search).
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "models.js" as Logic

ColumnLayout {
    id: role

    property var row: null                   // pozycja z Logic.roleRows()
    property var status: null
    property var page: null
    property bool advanced: false
    property bool readOnly: false
    property bool ctxDirty: false            // kontekst wpisany, jeszcze niezapisany
    readonly property string tag: "role:" + (row ? row.id : "")
    readonly property string warning: row ? Logic.roleWarning(row) : ""
    readonly property var server: row && status ? Logic.server(status, row.endpoint) : null
    readonly property var pull: row && status ? Logic.pulls(status, row.endpoint).find(p => p.model === row.model && !p.done) || null : null
    readonly property bool canPull: !!server && server.online && server.kind !== "openai" && !!row && row.installed === false
                                    && !(pull && pull.running)

    function note(suffix) {
        return page ? page.notes[tag + (suffix ? ":" + suffix : "")] || null : null
    }
    function busy(suffix) {
        return page ? page.busy[tag + (suffix ? ":" + suffix : "")] === true : false
    }
    function pinValue() {
        return row && row.pinned ? row.endpoint : ""
    }
    function syncCtx() {
        if (!ctxDirty) ctxField.text = row ? Logic.ctxText(row.id, row.ctx) : ""
    }
    function setModel(name) {
        if (readOnly) return
        page.send("SetRole", Logic.roleRequest(row.id, name, Logic.rolePin(row, name, pinValue()), undefined), tag)
    }
    function setPin(endpoint) {
        if (readOnly || !row.model) return
        page.send("SetRole", Logic.roleRequest(row.id, row.model, endpoint, undefined), tag + ":pin")
    }
    function setSearch(value) {
        if (!page || value === (Logic.searchEnabled(status) ? "on" : "off")) return
        page.send("SetSemanticSearch", value, tag + ":search")
    }
    function setProgram(value) {
        if (!page || value === Logic.agentProgram(status)) return
        page.send("SetAgentProgram", value, tag + ":program")
    }
    function saveCtx() {
        if (readOnly) return
        const value = Logic.ctxValue(ctxField.text)
        if (value === undefined) {
            page.setNote(tag + ":ctx", { text: "kontekst: liczba od 0 do 1048576 albo puste (domyślny)", error: true })
            return
        }
        page.send("SetRole", Logic.roleRequest(row.id, row.model, pinValue(), value), tag + ":ctx")
    }

    spacing: 6

    onRowChanged: syncCtx()
    Component.onCompleted: syncCtx()

    Connections {
        target: role.page
        function onFinished(tag, ok) {
            if (tag === role.tag + ":ctx" && ok) {
                role.ctxDirty = false
                role.syncCtx()
            }
        }
    }

    RowLayout {
        Layout.fillWidth: true
        spacing: 10
        Text {
            text: role.row ? role.row.title : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.textPrimary
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 14
            font.weight: Font.DemiBold
            Accessible.role: Accessible.Heading
        }
        Text {
            objectName: "roleServer"
            Layout.fillWidth: true
            horizontalAlignment: Text.AlignRight
            text: !role.row ? "" : "na: " + role.row.serverLabel
                  + (!role.row.pinKnown ? "" : role.row.pinned ? " · przypięta" : " · za domyślnym")
            textFormat: Text.PlainText
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 12
            elide: Text.ElideLeft
        }
    }
    Text {
        Layout.fillWidth: true
        text: role.row ? role.row.hint : ""
        textFormat: Text.PlainText
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
        wrapMode: Text.WordWrap
    }
    // Wyszukiwanie po znaczeniu (tylko wiersz „Wyszukiwanie”): włączone / wyłączone i stan indeksu.
    ColumnLayout {
        objectName: "search"
        Layout.fillWidth: true
        visible: !!role.row && role.row.id === "embed"
        spacing: 6
        readonly property var enabledNow: Logic.searchEnabled(role.status)
        readonly property var line: Logic.searchLine(role.status)
        Segmented {
            objectName: "searchSwitch"
            options: Logic.searchOptions()
            current: parent.enabledNow === null ? "" : parent.enabledNow ? "on" : "off"
            enabled: parent.enabledNow !== null && !role.busy("search")
            itemHeight: 26
            onPicked: value => role.setSearch(value)
        }
        Note {
            objectName: "searchState"
            Layout.fillWidth: true
            tone: parent.line.tone
            text: parent.line.text
        }
        Note {
            objectName: "searchNote"
            Layout.fillWidth: true
            tone: "error"
            text: role.note("search") && role.note("search").error ? role.note("search").text : ""
        }
    }
    ModelPicker {
        objectName: "modelPicker"
        Layout.fillWidth: true
        Layout.topMargin: 2
        model: role.row ? role.row.model : ""
        options: role.row ? role.row.models : []
        enabled: !role.readOnly && !role.busy("")
        emptyText: role.row && !role.row.exists ? "nie ustawiony — wybierz model" : "wybierz model"
        accessibleName: role.row ? "Model: " + role.row.title : "Model"
        onChosen: name => role.setModel(name)
    }
    // Rozpoznawanie pytań: bez modelu ocena jest wyłączona (liczą reguły); model w chmurze ma znacznik chmury.
    Note {
        objectName: "roleState"
        Layout.fillWidth: true
        tone: role.row && role.row.cloud ? "warn" : "muted"
        text: role.row ? Logic.roleState(role.row) : ""
    }
    Note {
        objectName: "listNote"
        Layout.fillWidth: true
        text: role.row && role.row.models.length === 0
              ? (role.row.serverState.kind === "online" ? "Na serwerze „" + role.row.serverLabel + "” nie ma jeszcze modeli — pobierz któryś niżej."
                 : role.row.serverState.kind === "checking" ? "Sprawdzam serwer „" + role.row.serverLabel + "”…"
                 : "") : ""
    }
    RowLayout {
        Layout.fillWidth: true
        visible: role.warning !== ""
        spacing: 10
        Note {
            objectName: "roleWarning"
            Layout.fillWidth: true
            tone: "warn"
            text: role.warning !== "" ? "⚠ " + role.warning : ""
        }
        LinkText {
            objectName: "pullNow"
            visible: role.canPull && !role.readOnly
            text: role.busy("pull") ? "Zaczynam…" : "Pobierz teraz"
            enabled: !role.busy("pull")
            onClicked: page.send("PullModel", Logic.pullRequest(role.row.endpoint, role.row.model), role.tag + ":pull")
        }
    }
    Note {
        objectName: "rolePull"
        Layout.fillWidth: true
        tone: role.pull && role.pull.error ? "error" : "muted"
        text: role.pull ? "Pobieranie " + role.pull.model + ": " + role.pull.text : ""
    }
    Note {
        objectName: "roleNote"
        Layout.fillWidth: true
        tone: "error"
        text: [role.note(""), role.note("pull")].filter(n => n && n.error).map(n => n.text).join("\n")
    }

    // Program agenta (tylko wiersz „Agent”): Codex albo Claude Code, z jednym uczciwym zdaniem o wybranym.
    ColumnLayout {
        objectName: "agentProgram"
        Layout.fillWidth: true
        Layout.topMargin: 4
        visible: !!role.row && role.row.id === "agent"
        spacing: 6
        readonly property string program: Logic.agentProgram(role.status)
        FieldLabel { text: "Program" }
        Segmented {
            objectName: "programSwitch"
            options: Logic.agentPrograms()
            current: parent.program
            enabled: parent.program !== "" && !role.busy("program")
            itemHeight: 26
            onPicked: value => role.setProgram(value)
        }
        Note {
            objectName: "programHint"
            Layout.fillWidth: true
            tone: parent.program === "" ? "warn" : "muted"
            text: Logic.agentProgramHint(parent.program)
        }
        Note {
            objectName: "programNote"
            Layout.fillWidth: true
            tone: "error"
            text: role.note("program") && role.note("program").error ? role.note("program").text : ""
        }
    }

    LinkText {
        objectName: "advancedLink"
        text: role.advanced ? "Zaawansowane ▾" : "Zaawansowane ▸"
        tint: Tide.Tokens.textSecondary
        onClicked: role.advanced = !role.advanced
    }

    ColumnLayout {
        objectName: "advanced"
        Layout.fillWidth: true
        Layout.leftMargin: 12
        visible: role.advanced
        spacing: 6

        FieldLabel { text: "Serwer tej roli" }
        Segmented {
            objectName: "pinSwitch"
            Layout.maximumWidth: role.width - 12
            options: role.status ? Logic.pinOptions(role.status) : []
            current: role.pinValue()
            enabled: !role.readOnly && !!role.row && role.row.model !== "" && !role.busy("pin")
            itemHeight: 26
            onPicked: value => { if (value !== role.pinValue()) role.setPin(value) }
        }
        Note {
            objectName: "pinNote"
            Layout.fillWidth: true
            visible: !role.readOnly
            text: role.row && role.row.pinned
                  ? "Przypięta: zostaje na tym serwerze, gdy przełączysz używany serwer."
                  : "Idzie za używanym serwerem — przełączenie używanego serwera przeniesie ją razem z innymi."
        }
        Note {
            Layout.fillWidth: true
            tone: "error"
            text: role.note("pin") && role.note("pin").error ? role.note("pin").text : ""
        }

        FieldLabel { Layout.topMargin: 4; text: "Kontekst (tokeny)" }
        RowLayout {
            spacing: 8
            LineInput {
                id: ctxField
                objectName: "ctxField"
                Layout.preferredWidth: 190
                implicitHeight: 32
                mono: true
                placeholder: role.row ? Logic.ctxPlaceholder(role.row.id) : ""
                accessibleName: "Kontekst roli " + (role.row ? role.row.title : "")
                enabled: !role.readOnly
                onEdited: role.ctxDirty = true
                onAccepted: role.saveCtx()
            }
            PillButton {
                objectName: "ctxSave"
                text: role.busy("ctx") ? "Zapisuję…" : "Zapisz"
                enabled: !role.readOnly && !!role.row && role.row.model !== "" && !role.busy("ctx")
                implicitHeight: 32
                fill: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)
                onClicked: role.saveCtx()
            }
        }
        Note {
            Layout.fillWidth: true
            text: "Puste — domyślny. Większy kontekst to dłuższe wejście, ale model może przestać mieścić się na karcie."
        }
        Note {
            objectName: "ctxNote"
            Layout.fillWidth: true
            tone: role.note("ctx") && role.note("ctx").error ? "error" : "ok"
            text: role.note("ctx") ? role.note("ctx").text : ""
        }
    }
}

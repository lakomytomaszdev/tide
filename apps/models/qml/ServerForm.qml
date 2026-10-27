// Formularz serwera: dodanie (nazwa, adres, rodzaj, token) albo edycja zapisanego (adres, rodzaj, token —
// nazwa zostaje; zmiana nazwy to nowy serwer i usunięcie starego). Okno nigdy nie zna zapisanego tokenu:
// puste pole = token bez zmiany, „Usuń token” = clearToken. „Sprawdź” to TestEndpoint bez zapisu.
// Sam nie rozmawia z usługą: prośby idą sygnałami, wyniki wracają przez testNote i saveNote.
import QtQuick
import QtQuick.Layouts
import "tide" as Tide
import "models.js" as Logic

ColumnLayout {
    id: form

    property bool adding: true
    property var status: null
    property string name: ""                 // edycja: nazwa zapisanego serwera
    property bool auth: false                // edycja: serwer ma zapisany token
    property string originalUrl: ""          // edycja: zapisany adres (token idzie za zmienionym adresem)
    property bool testing: false
    property bool saving: false
    property var testNote: null              // {text, error}
    property var saveNote: null
    property alias nameText: nameField.text
    property alias urlText: urlField.text
    property alias tokenText: tokenField.text
    property string kind: "ollama"
    property bool clearToken: false
    property bool showToken: false
    property var problem: null               // {field, text} z ostatniej próby wysłania

    signal edited()                          // pole zmienione ręcznie — szkic do zachowania w ModelsPage
    signal testRequested(string request)
    signal saveRequested(string request)
    signal cancelled()

    readonly property var values: ({ name: adding ? nameField.text : name, url: urlField.text, kind: kind,
                                     token: tokenField.text, clearToken: clearToken })
    readonly property string savedName: adding ? Logic.slug(nameField.text) : name
    // Zapisany token zostaje przy serwerze także po zmianie adresu — i poleci pod nowy adres (próba też).
    readonly property bool tokenMoves: !adding && auth && !clearToken && tokenField.text === ""
                                       && urlField.text.trim() !== "" && urlField.text.trim() !== originalUrl.trim()

    // Zapisany serwer (albo nic — dodawanie) i niezapisany szkic z ModelsPage, jeśli jest: nowy Status()
    // odtwarza karty, a wpisany adres i token nie mogą wtedy przepaść.
    function load(endpoint, draft) {
        name = endpoint ? endpoint.name : ""
        auth = endpoint ? !!endpoint.auth : false
        originalUrl = endpoint ? endpoint.url : ""
        nameField.text = draft ? draft.name || "" : ""
        urlField.text = draft ? draft.url : originalUrl
        kind = draft ? draft.kind : endpoint && endpoint.kind ? endpoint.kind : "ollama"
        tokenField.text = draft ? draft.token || "" : ""
        clearToken = draft ? !!draft.clearToken : false
        showToken = draft ? !!draft.showToken : false
        problem = null
    }

    // Stan pól do zachowania (token tylko w pamięci okna).
    function draft() {
        return { name: nameField.text, url: urlField.text, kind: kind, token: tokenField.text, clearToken: clearToken,
                 showToken: showToken }
    }

    function focusFirst() {
        if (adding) nameField.focusInput()
        else urlField.focusInput()
    }

    function check() {
        problem = Logic.formProblem(values, status, adding)
        return problem === null
    }

    function test() {
        const p = Logic.formProblem(Object.assign({}, values, { name: adding ? "x" : name }), null, false)
        problem = p
        if (p === null) testRequested(Logic.testRequest(values, adding))
    }

    function save() {
        if (check()) saveRequested(Logic.endpointRequest(values, adding))
    }

    spacing: 8

    // Nazwa — tylko przy dodawaniu.
    FieldLabel { visible: form.adding; text: "Nazwa" }
    LineInput {
        id: nameField
        objectName: "nameField"
        Layout.fillWidth: true
        visible: form.adding
        placeholder: "np. Mac Studio"
        accessibleName: "Nazwa serwera"
        maximumLength: 60
        invalid: form.problem !== null && form.problem.field === "name"
        onEdited: { form.problem = null; form.edited() }
        onAccepted: form.save()
        onEscaped: form.cancelled()
    }
    Note {
        Layout.fillWidth: true
        visible: form.adding && nameField.text.trim() !== "" && form.savedName !== nameField.text.trim()
                 && form.savedName !== ""
        text: "zapiszę jako „" + form.savedName + "”"
    }

    FieldLabel { text: "Adres serwera" }
    LineInput {
        id: urlField
        objectName: "urlField"
        Layout.fillWidth: true
        placeholder: "http://mac-studio:11434"
        accessibleName: "Adres serwera"
        mono: true
        invalid: form.problem !== null && form.problem.field === "url"
        onEdited: { form.problem = null; form.edited() }
        onAccepted: form.save()
        onEscaped: form.cancelled()
    }

    FieldLabel { text: "Rodzaj" }
    Segmented {
        objectName: "kindSwitch"
        options: [{ value: "ollama", label: "Ollama" }, { value: "openai", label: "Zgodny z OpenAI" }]
        current: form.kind
        itemHeight: 26
        onPicked: value => { form.kind = value; form.edited() }
    }
    Note {
        Layout.fillWidth: true
        text: form.kind === "openai" ? "MLX, LM Studio, llama.cpp — serwer z /v1/chat/completions. Modele pobiera się w jego programie."
                                     : ""
    }

    FieldLabel { text: form.auth ? "Token (zapisany)" : "Token (opcjonalnie)" }
    RowLayout {
        Layout.fillWidth: true
        spacing: 8
        LineInput {
            id: tokenField
            objectName: "tokenField"
            Layout.fillWidth: true
            enabled: !form.clearToken
            mono: true
            echoMode: form.showToken ? TextInput.Normal : TextInput.Password
            placeholder: form.clearToken ? "token zostanie usunięty"
                       : form.auth ? "zostaw puste — bez zmiany" : "bez tokenu"
            accessibleName: "Token serwera"
            invalid: form.problem !== null && form.problem.field === "token"
            onEdited: { form.problem = null; form.edited() }
            onAccepted: form.save()
            onEscaped: form.cancelled()
        }
        Rectangle {
            objectName: "showToken"
            implicitWidth: 36
            implicitHeight: 36
            radius: Tide.Tokens.radiusFrame
            color: showArea.containsMouse || activeFocus ? Qt.lighter(Tide.Tokens.backgroundCard, 1.15) : Tide.Tokens.backgroundCard
            border.width: activeFocus ? 1 : 0
            border.color: Tide.Tokens.accent
            activeFocusOnTab: true
            Accessible.role: Accessible.CheckBox
            Accessible.name: "Pokaż token"
            Accessible.checked: form.showToken
            Accessible.onToggleAction: form.showToken = !form.showToken
            Accessible.onPressAction: form.showToken = !form.showToken
            Keys.onSpacePressed: form.showToken = !form.showToken
            Glyph {
                anchors.centerIn: parent
                width: 16
                height: 16
                name: form.showToken ? "eyeOff" : "eye"
                color: Tide.Tokens.textSecondary
            }
            MouseArea {
                id: showArea
                anchors.fill: parent
                hoverEnabled: true
                cursorShape: Qt.PointingHandCursor
                onClicked: form.showToken = !form.showToken
            }
        }
    }
    Note {
        objectName: "tokenMoves"
        Layout.fillWidth: true
        tone: "warn"
        text: form.tokenMoves ? "⚠ Zapisany token zostanie wysłany pod nowy adres (także przy „Sprawdź”). Jeśli to inny "
                                + "serwer, usuń token albo wpisz nowy." : ""
    }
    LinkText {
        objectName: "clearToken"
        visible: form.auth
        text: form.clearToken ? "Cofnij — zostaw zapisany token" : "Usuń token"
        tint: form.clearToken ? Tide.Tokens.accent : Tide.Tokens.negative
        onClicked: {
            form.clearToken = !form.clearToken
            if (form.clearToken) tokenField.text = ""
            form.edited()
        }
    }

    Note {
        objectName: "formProblem"
        Layout.fillWidth: true
        tone: "error"
        text: form.problem ? form.problem.text : ""
    }
    Note {
        objectName: "testNote"
        Layout.fillWidth: true
        tone: form.testing ? "muted" : form.testNote && form.testNote.error ? "error" : "ok"
        text: form.testing ? "sprawdzam połączenie… (do kilku sekund)" : form.testNote ? form.testNote.text : ""
    }
    Note {
        objectName: "saveNote"
        Layout.fillWidth: true
        tone: "error"
        text: form.saveNote && form.saveNote.error ? form.saveNote.text : ""
    }

    RowLayout {
        Layout.fillWidth: true
        Layout.topMargin: 2
        spacing: 8
        PillButton {
            objectName: "testButton"
            text: form.testing ? "Sprawdzam…" : "Sprawdź"
            enabled: !form.testing
            implicitHeight: 32
            onClicked: form.test()
        }
        Item { Layout.fillWidth: true }
        PillButton {
            text: "Anuluj"
            implicitHeight: 32
            fill: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
            onClicked: form.cancelled()
        }
        PillButton {
            objectName: "saveButton"
            text: form.saving ? "Zapisuję…" : form.adding ? "Dodaj serwer" : "Zapisz"
            enabled: !form.saving
            implicitHeight: 32
            strong: true
            fill: Tide.Tokens.accent
            textColor: Tide.Tokens.backgroundDesktop
            onClicked: form.save()
        }
    }
}

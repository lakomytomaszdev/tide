// Zawartość okna „Nowe zadanie” (układ z mocka #22): „Co ma zrobić agent?” ze skrótem, repozytorium
// (lista rozwijana od ostatnio aktywnych, z szukaniem po wpisaniu) i agent (Claude Code / Codex),
// model (chmura albo lokalny z roli `agent` w /Models), zadanie z licznikiem znaków, zrzuty ekranu (miniatury, „Zrób zrzut”, przeciąganie, Ctrl+V),
// karta „Nowy worktree” z gałęzią do zmiany, błąd w miejscu i „Uruchom agenta”.
// Sam nie rozmawia z usługą — dostaje dane i zgłasza prośby sygnałami, dzięki czemu da się go
// załadować w teście z przykładowymi danymi.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "newtask.js" as Logic

Rectangle {
    id: page

    property var repos: []
    property var defaults: ({})
    property var suggestion: ({})
    property var images: []
    property bool available: true
    property bool busy: false
    property string error: ""
    property string homeDir: ""
    property bool canCapture: false
    property string shortcutText: "Meta+Alt+A"
    property real nowTs: Date.now() / 1000
    // Wklejenie obrazu ze schowka: funkcja() → true, gdy schowek miał obraz (albo pliki obrazów)
    // i trafił do zrzutów; wtedy pole tekstowe już nic nie wkleja.
    property var pasteImage: function () { return false }
    // Stan modeli lokalnych: Status() obiektu /Models (null — usługa go nie podaje).
    property var modelsStatus: null

    signal startRequested(string request)
    signal suggestRequested(string repoPath, string tool, string prompt)
    signal captureRequested()
    signal removeImageRequested(string path)
    signal filesDropped(var urls)
    signal cancelRequested()
    signal modelsRequested()   // „ustaw w Tide → Modele”: okno Tide na widoku Modele

    // Stan formularza.
    property string query: ""
    property string repoPath: ""
    property string tool: "claude"
    property string model: "cloud"
    // Zapamiętany „Lokalny” czeka na pierwszy odczyt serwera — wraca tylko, gdy model jest dostępny.
    property bool rememberedLocal: false
    property alias prompt: promptEdit.text
    property bool newWorktree: true
    property string branch: ""
    property bool branchEdited: false
    property bool editingBranch: false
    property bool defaultsApplied: false
    readonly property alias repoListOpen: repoPopup.opened

    readonly property var shownRepos: Logic.filterRepos(repos, query)
    readonly property var repo: Logic.findRepo(repos, repoPath)
    readonly property var tools: defaults && defaults.tools ? defaults.tools : ({})
    readonly property var localOption: Logic.localOption(modelsStatus)
    readonly property string modelNote: Logic.modelNote(localOption, model, tool)
    readonly property var formState: ({
        available: available, repo: repo, tool: tool, tools: tools, prompt: prompt, newWorktree: newWorktree,
        branch: branch, images: images, model: model, local: localOption
    })
    readonly property string blocker: Logic.blocker(formState)
    readonly property bool canStart: blocker === "" && !busy
    readonly property var hint: Logic.worktreeHint(repo, newWorktree)
    readonly property string worktreePath: branchEdited || !suggestion || !suggestion.path
                                           ? Logic.pathForBranch(repo, branch) : suggestion.path

    function applyDefaults() {
        if (!defaults || defaults.tool === undefined) return
        if (!defaultsApplied) {
            tool = defaults.tool || "claude"
            newWorktree = defaults.newWorktree !== false
            rememberedLocal = defaults.model === "local"
            applyRememberedModel()
            defaultsApplied = true
        }
        if (repoPath === "" && repos.length > 0) {
            const remembered = Logic.findRepo(repos, defaults.repoPath)
            repoPath = remembered ? defaults.repoPath : repos[0].path
            promptEdit.forceActiveFocus()
        }
    }
    function applyRememberedModel() {
        if (!rememberedLocal) return
        if (modelsStatus && modelsStatus.checked === false) return  // jeszcze nie wiadomo
        // Wprost ze stanu: wiązanie localOption mogło jeszcze nie zauważyć zmiany modelsStatus.
        model = Logic.effectiveModel("local", Logic.localOption(modelsStatus))
        rememberedLocal = false
    }
    onModelsStatusChanged: applyRememberedModel()
    onDefaultsChanged: applyDefaults()
    onReposChanged: applyDefaults()

    function start() {
        if (!canStart) return
        startRequested(Logic.request(formState))
    }
    function focusPrompt() { promptEdit.forceActiveFocus() }
    function openRepos(text) {
        query = text || ""
        repoPopup.open()
        search.text = query
        search.forceActiveFocus()
    }
    function focusSearch() { openRepos("") }
    function moveSelection(step) {
        const list = shownRepos
        if (!list.length) return
        let i = list.findIndex(r => r.path === repoPath)
        i = Math.max(0, Math.min(list.length - 1, (i < 0 ? -1 : i) + step))
        repoPath = list[i].path
        repoList.positionViewAtIndex(i, ListView.Contain)
    }
    function chooseRepo(path) {
        repoPath = path
        repoPopup.close()
        query = ""
        focusPrompt()
    }

    // Propozycja gałęzi z polecenia — z opóźnieniem, żeby nie pytać usługi przy każdym znaku.
    function askSuggestion() { if (newWorktree && repoPath !== "") suggestTimer.restart() }
    onRepoPathChanged: askSuggestion()
    onToolChanged: askSuggestion()
    onNewWorktreeChanged: askSuggestion()
    onPromptChanged: askSuggestion()
    onSuggestionChanged: if (!branchEdited && suggestion && suggestion.branch) branch = suggestion.branch
    Timer {
        id: suggestTimer
        interval: 300
        onTriggered: page.suggestRequested(page.repoPath, page.tool, page.prompt)
    }

    color: Tide.Tokens.backgroundWindow

    component Label: Text {
        color: Tide.Tokens.textSecondary
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
        font.weight: Font.Medium
    }
    component Muted: Text {
        color: Tide.Tokens.textMuted
        font.family: Tide.Theme.uiFamily
        font.pixelSize: 12
    }
    component Field: Rectangle {
        property bool focused: false
        radius: Tide.Tokens.radiusFrame + 2
        color: Tide.Tokens.backgroundView
        border.width: 1
        border.color: focused ? Tide.Theme.alpha(Tide.Tokens.accent, 0.75) : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.09)
    }

    DropArea {
        id: drop
        anchors.fill: parent
        keys: ["text/uri-list"]
        onDropped: event => {
            page.filesDropped(event.urls.map(u => u.toString()))
            event.accept()
        }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 30
        anchors.rightMargin: 30
        anchors.topMargin: 26
        anchors.bottomMargin: 24
        spacing: 8

        // Nagłówek ze skrótem.
        RowLayout {
            Layout.fillWidth: true
            Layout.bottomMargin: 10
            Text {
                Layout.fillWidth: true
                text: "Co ma zrobić agent?"
                color: Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 21
                font.weight: Font.DemiBold
            }
            Rectangle {
                visible: page.shortcutText !== ""
                radius: 6
                color: "transparent"
                border.width: 1
                border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.14)
                implicitWidth: keys.implicitWidth + 14
                implicitHeight: keys.implicitHeight + 6
                Text {
                    id: keys
                    anchors.centerIn: parent
                    text: page.shortcutText
                    color: Tide.Tokens.textSecondary
                    font.family: Tide.Theme.monoFamily
                    font.pixelSize: 11
                }
            }
        }

        // Repozytorium i agent.
        RowLayout {
            Layout.fillWidth: true
            spacing: 16
            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 3
                spacing: 6
                Label { text: "Repozytorium" }
                Field {
                    id: repoSelect
                    objectName: "repoSelect"
                    Layout.fillWidth: true
                    implicitHeight: 50
                    focused: activeFocus || repoPopup.opened
                    activeFocusOnTab: true
                    Accessible.role: Accessible.ComboBox
                    Accessible.name: "Repozytorium: " + (page.repo ? page.repo.name : "nie wybrano")
                    Keys.onPressed: event => {
                        if (event.key === Qt.Key_Down || event.key === Qt.Key_Return || event.key === Qt.Key_Space) {
                            page.openRepos("")
                            event.accepted = true
                        } else if (event.text && event.text.trim() !== "" && !(event.modifiers & Qt.ControlModifier)) {
                            // Pisanie na zamkniętej liście od razu ją otwiera i szuka.
                            page.openRepos(event.text)
                            event.accepted = true
                        }
                    }
                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: 14
                        anchors.rightMargin: 14
                        spacing: 10
                        Rectangle {
                            implicitWidth: 9
                            implicitHeight: 9
                            rotation: 45
                            radius: 2
                            color: Tide.Tokens.accent
                            opacity: page.repo ? 1 : 0.4
                        }
                        Text {
                            text: page.repo ? page.repo.name : page.available ? "Wybierz repozytorium" : "—"
                            color: page.repo ? Tide.Tokens.textPrimary : Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 14
                            font.weight: Font.DemiBold
                            elide: Text.ElideRight
                            Layout.maximumWidth: repoSelect.width * 0.55
                        }
                        Muted {
                            Layout.fillWidth: true
                            text: page.repo ? Logic.changesAgo(page.repo.lastActivityTs, page.nowTs) : ""
                            elide: Text.ElideRight
                        }
                        Text {
                            text: "⌄"
                            color: Tide.Tokens.textMuted
                            font.pixelSize: 15
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: repoPopup.opened ? repoPopup.close() : page.openRepos("")
                    }

                    QQC2.Popup {
                        id: repoPopup
                        y: repoSelect.height + 4
                        width: repoSelect.width
                        padding: 6
                        focus: true
                        closePolicy: QQC2.Popup.CloseOnEscape | QQC2.Popup.CloseOnPressOutsideParent
                        onClosed: { page.query = ""; search.text = "" }
                        background: Rectangle {
                            radius: Tide.Tokens.radiusFrame + 2
                            color: Tide.Tokens.backgroundCard
                            border.width: 1
                            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
                        }
                        contentItem: ColumnLayout {
                            spacing: 6
                            Field {
                                Layout.fillWidth: true
                                implicitHeight: 32
                                focused: search.activeFocus
                                TextInput {
                                    id: search
                                    anchors.fill: parent
                                    anchors.leftMargin: 10
                                    anchors.rightMargin: 10
                                    verticalAlignment: TextInput.AlignVCenter
                                    color: Tide.Tokens.textPrimary
                                    selectionColor: Tide.Tokens.accent
                                    selectedTextColor: Tide.Tokens.backgroundDesktop
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 13
                                    clip: true
                                    onTextEdited: {
                                        page.query = text
                                        if (page.shownRepos.length && !page.shownRepos.some(r => r.path === page.repoPath))
                                            page.repoPath = page.shownRepos[0].path
                                    }
                                    Keys.onDownPressed: page.moveSelection(1)
                                    Keys.onUpPressed: page.moveSelection(-1)
                                    Keys.onReturnPressed: page.chooseRepo(page.repoPath)
                                    Keys.onEnterPressed: page.chooseRepo(page.repoPath)
                                    Accessible.role: Accessible.EditableText
                                    Accessible.name: "Szukaj repozytorium"
                                    Muted {
                                        anchors.verticalCenter: parent.verticalCenter
                                        visible: search.text === ""
                                        text: "Szukaj repozytorium…"
                                        font.pixelSize: 13
                                    }
                                }
                            }
                            ListView {
                                id: repoList
                                Layout.fillWidth: true
                                implicitHeight: Math.min(6, Math.max(1, count)) * 34
                                clip: true
                                model: page.shownRepos
                                boundsBehavior: Flickable.StopAtBounds
                                QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                                delegate: Rectangle {
                                    id: row
                                    required property var modelData
                                    readonly property bool current: modelData.path === page.repoPath
                                    width: ListView.view.width
                                    height: 34
                                    radius: Tide.Tokens.radiusSmall - 2
                                    color: current ? Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                                         : rowArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05)
                                         : "transparent"
                                    Accessible.role: Accessible.ListItem
                                    Accessible.name: modelData.name
                                    Accessible.selected: current
                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: 10
                                        anchors.rightMargin: 10
                                        spacing: 8
                                        Text {
                                            text: row.modelData.name
                                            color: row.current ? Tide.Tokens.accent : Tide.Tokens.textPrimary
                                            font.family: Tide.Theme.uiFamily
                                            font.pixelSize: 13
                                            font.weight: Font.Medium
                                        }
                                        Muted {
                                            Layout.fillWidth: true
                                            text: Logic.tildePath(row.modelData.path, page.homeDir)
                                            font.pixelSize: 11
                                            elide: Text.ElideMiddle
                                        }
                                        Text {
                                            visible: row.modelData.dirty > 0
                                            text: "● " + row.modelData.dirty
                                            color: Tide.Tokens.butter
                                            font.family: Tide.Theme.monoFamily
                                            font.pixelSize: 11
                                        }
                                        Muted {
                                            text: Logic.shortAgo(row.modelData.lastActivityTs, page.nowTs)
                                            font.family: Tide.Theme.monoFamily
                                            font.pixelSize: 11
                                        }
                                    }
                                    MouseArea {
                                        id: rowArea
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: page.chooseRepo(row.modelData.path)
                                    }
                                }
                                Muted {
                                    anchors.centerIn: parent
                                    visible: repoList.count === 0
                                    text: page.repos.length === 0 ? "Wczytuję repozytoria…"
                                                                  : "Nic nie pasuje do „" + page.query + "”"
                                }
                            }
                        }
                    }
                }
            }
            ColumnLayout {
                Layout.fillWidth: true
                Layout.preferredWidth: 2
                spacing: 6
                Label { text: "Agent" }
                Field {
                    Layout.fillWidth: true
                    implicitHeight: 50
                    RowLayout {
                        anchors.fill: parent
                        anchors.margins: 4
                        spacing: 4
                        Repeater {
                            model: Logic.TOOLS
                            delegate: Rectangle {
                                id: segment
                                required property var modelData
                                readonly property bool chosen: page.tool === modelData.id
                                readonly property bool missing: page.tools[modelData.id] === false
                                Layout.fillWidth: true
                                Layout.fillHeight: true
                                radius: Tide.Tokens.radiusFrame
                                color: chosen ? Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                                     : segArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05)
                                     : "transparent"
                                border.width: chosen || activeFocus ? 1 : 0
                                border.color: activeFocus ? Tide.Tokens.textPrimary : Tide.Theme.alpha(Tide.Tokens.accent, 0.55)
                                activeFocusOnTab: true
                                Accessible.role: Accessible.RadioButton
                                Accessible.name: modelData.label
                                Accessible.checked: chosen
                                Keys.onSpacePressed: page.tool = modelData.id
                                Text {
                                    anchors.centerIn: parent
                                    text: (segment.modelData.id === "codex" ? "◎ " : "✻ ") + segment.modelData.label
                                          + (segment.missing ? " · brak" : "")
                                    color: segment.missing ? Tide.Tokens.textMuted
                                         : segment.chosen ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 14
                                    font.weight: segment.chosen ? Font.DemiBold : Font.Medium
                                }
                                MouseArea {
                                    id: segArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: page.tool = segment.modelData.id
                                }
                            }
                        }
                    }
                }
            }
        }

        // Model: chmura narzędzia albo model lokalny z roli `agent`.
        Label { text: "Model"; Layout.topMargin: 10 }
        Field {
            Layout.fillWidth: true
            implicitHeight: 44
            RowLayout {
                anchors.fill: parent
                anchors.margins: 4
                spacing: 4
                Repeater {
                    model: Logic.MODELS
                    delegate: Rectangle {
                        id: modelSegment
                        required property var modelData
                        readonly property bool isLocal: modelData.id === "local"
                        readonly property bool chosen: page.model === modelData.id
                        readonly property bool disabled: isLocal && !page.localOption.enabled
                        objectName: "model-" + modelData.id
                        Layout.fillWidth: true
                        Layout.preferredWidth: isLocal ? 3 : 1
                        Layout.fillHeight: true
                        radius: Tide.Tokens.radiusFrame
                        color: chosen ? Tide.Theme.alpha(Tide.Tokens.accent, 0.16)
                             : modelArea.containsMouse && !disabled ? Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.05)
                             : "transparent"
                        border.width: chosen || activeFocus ? 1 : 0
                        border.color: activeFocus ? Tide.Tokens.textPrimary : Tide.Theme.alpha(Tide.Tokens.accent, 0.55)
                        activeFocusOnTab: !disabled
                        Accessible.role: Accessible.RadioButton
                        Accessible.name: isLocal ? page.localOption.label : "Chmura"
                        Accessible.checked: chosen
                        Accessible.description: disabled ? page.localOption.reason : ""
                        Keys.onSpacePressed: if (!disabled) page.model = modelData.id
                        QQC2.ToolTip.visible: modelArea.containsMouse && disabled
                        QQC2.ToolTip.text: page.localOption.reason
                        QQC2.ToolTip.delay: 300
                        Text {
                            anchors.centerIn: parent
                            width: Math.min(implicitWidth, parent.width - 16)
                            elide: Text.ElideRight
                            text: (modelSegment.isLocal ? "⌂ " + page.localOption.label : "☁ Chmura")
                            color: modelSegment.disabled ? Tide.Tokens.textMuted
                                 : modelSegment.chosen ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 14
                            font.weight: modelSegment.chosen ? Font.DemiBold : Font.Medium
                        }
                        MouseArea {
                            id: modelArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: modelSegment.disabled ? Qt.ArrowCursor : Qt.PointingHandCursor
                            onClicked: if (!modelSegment.disabled) page.model = modelSegment.modelData.id
                        }
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            visible: page.modelNote !== ""
            spacing: 6
            Text {
                objectName: "modelNote"
                Layout.fillWidth: !page.localOption.hint || page.localOption.enabled
                text: page.localOption.enabled ? page.modelNote : page.localOption.reason
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            Text {
                objectName: "modelsLink"
                visible: !page.localOption.enabled && page.localOption.hint
                Layout.fillWidth: true
                text: "— " + Logic.MODELS_HINT
                color: linkArea.containsMouse ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                font.underline: linkArea.containsMouse
                elide: Text.ElideRight
                Accessible.role: Accessible.Link
                Accessible.name: "Otwórz Tide → Modele"
                MouseArea {
                    id: linkArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: page.modelsRequested()
                }
            }
        }

        // Zadanie.
        Label { text: "Zadanie"; Layout.topMargin: 10 }
        Field {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 130
            focused: promptEdit.activeFocus
            Flickable {
                id: promptFlick
                anchors.fill: parent
                anchors.margins: 16
                contentWidth: width
                contentHeight: promptEdit.implicitHeight
                clip: true
                boundsBehavior: Flickable.StopAtBounds
                QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                function ensureVisible(r) {
                    if (contentY >= r.y) contentY = r.y
                    else if (contentY + height <= r.y + r.height) contentY = r.y + r.height - height
                }
                TextEdit {
                    id: promptEdit
                    width: promptFlick.width
                    wrapMode: TextEdit.Wrap
                    color: Tide.Tokens.textPrimary
                    selectionColor: Tide.Tokens.accent
                    selectedTextColor: Tide.Tokens.backgroundDesktop
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 14
                    textFormat: TextEdit.PlainText
                    persistentSelection: true
                    onCursorRectangleChanged: promptFlick.ensureVisible(cursorRectangle)
                    Accessible.role: Accessible.EditableText
                    Accessible.name: "Zadanie dla agenta"
                    Keys.onPressed: event => {
                        if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter)
                                && (event.modifiers & Qt.ControlModifier)) {
                            page.start()
                            event.accepted = true
                        } else if (event.matches(StandardKey.Paste) && page.pasteImage()) {
                            event.accepted = true  // obraz ze schowka trafił do zrzutów
                        }
                    }
                    Muted {
                        visible: promptEdit.text === ""
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: "Opisz zadanie tak, jak napisałbyś je agentowi — co zrobić, gdzie, jak sprawdzić."
                        font: promptEdit.font
                    }
                }
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Muted { Layout.fillWidth: true; text: "Ctrl+Enter uruchamia · Ctrl+V wkleja zrzut ze schowka" }
            Muted { text: Logic.charCount(page.prompt) }
        }

        // Zrzuty ekranu.
        Label { text: "Zrzuty ekranu"; Layout.topMargin: 8 }
        RowLayout {
            Layout.fillWidth: true
            spacing: 12
            Repeater {
                model: page.images
                delegate: Rectangle {
                    id: thumb
                    required property string modelData
                    implicitWidth: 150
                    implicitHeight: 92
                    radius: 10
                    color: Tide.Tokens.backgroundCard
                    border.width: 1
                    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.12)
                    clip: true
                    Accessible.role: Accessible.Graphic
                    Accessible.name: "Zrzut " + Logic.imageName(modelData)
                    Image {
                        anchors.fill: parent
                        anchors.margins: 1
                        source: "file://" + thumb.modelData
                        sourceSize.width: 300
                        fillMode: Image.PreserveAspectCrop
                        asynchronous: true
                        cache: false
                    }
                    Rectangle {
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.bottom: parent.bottom
                        anchors.margins: 1
                        height: 22
                        color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.82)
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            x: 8
                            width: parent.width - 16
                            text: Logic.imageName(thumb.modelData)
                            color: Tide.Tokens.textSecondary
                            font.family: Tide.Theme.monoFamily
                            font.pixelSize: 11
                            elide: Text.ElideMiddle
                        }
                    }
                    Rectangle {
                        anchors.top: parent.top
                        anchors.right: parent.right
                        anchors.margins: 6
                        width: 18
                        height: 18
                        radius: 9
                        color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.85)
                        Accessible.role: Accessible.Button
                        Accessible.name: "Usuń zrzut " + Logic.imageName(thumb.modelData)
                        Text {
                            anchors.centerIn: parent
                            text: "×"
                            color: Tide.Tokens.textPrimary
                            font.pixelSize: 13
                        }
                        MouseArea {
                            anchors.fill: parent
                            anchors.margins: -4
                            cursorShape: Qt.PointingHandCursor
                            onClicked: page.removeImageRequested(thumb.modelData)
                        }
                    }
                }
            }
            // „Zrób zrzut”: przerywana ramka, okno chowa się na czas zaznaczania obszaru.
            Item {
                id: captureTile
                visible: page.canCapture && page.images.length < 10
                implicitWidth: 150
                implicitHeight: 92
                activeFocusOnTab: true
                Accessible.role: Accessible.Button
                Accessible.name: "Zrób zrzut zaznaczonego obszaru ekranu"
                Keys.onSpacePressed: if (!page.busy) page.captureRequested()
                Keys.onReturnPressed: if (!page.busy) page.captureRequested()
                Canvas {
                    id: dashes
                    anchors.fill: parent
                    property color stroke: captureArea.containsMouse || captureTile.activeFocus
                                           ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.28)
                    onStrokeChanged: requestPaint()
                    onPaint: {
                        const ctx = getContext("2d")
                        ctx.reset()
                        ctx.strokeStyle = stroke
                        ctx.lineWidth = 1
                        ctx.setLineDash([4, 4])
                        const r = 10, w = width - 1, h = height - 1
                        ctx.beginPath()
                        ctx.moveTo(r + 0.5, 0.5)
                        ctx.arcTo(w + 0.5, 0.5, w + 0.5, h + 0.5, r)
                        ctx.arcTo(w + 0.5, h + 0.5, 0.5, h + 0.5, r)
                        ctx.arcTo(0.5, h + 0.5, 0.5, 0.5, r)
                        ctx.arcTo(0.5, 0.5, w + 0.5, 0.5, r)
                        ctx.closePath()
                        ctx.stroke()
                    }
                }
                Column {
                    anchors.centerIn: parent
                    spacing: 6
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: "⌖"
                        color: Tide.Tokens.accent
                        font.pixelSize: 16
                    }
                    Text {
                        anchors.horizontalCenter: parent.horizontalCenter
                        text: "Zrób zrzut"
                        color: Tide.Tokens.textSecondary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                    }
                }
                MouseArea {
                    id: captureArea
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: if (!page.busy) page.captureRequested()
                }
            }
            Muted {
                Layout.fillWidth: true
                text: drop.containsDrag ? "Upuść, aby dołączyć" : "albo przeciągnij plik\nalbo wklej Ctrl+V"
                color: drop.containsDrag ? Tide.Tokens.accent : Tide.Tokens.textMuted
                lineHeight: 1.3
            }
        }

        // Nowy worktree.
        Rectangle {
            Layout.fillWidth: true
            Layout.topMargin: 10
            implicitHeight: 62
            radius: Tide.Tokens.radiusFrame + 2
            color: Tide.Tokens.backgroundCard
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07)
            RowLayout {
                anchors.fill: parent
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 14
                Rectangle {
                    id: toggle
                    objectName: "worktreeToggle"
                    implicitWidth: 38
                    implicitHeight: 22
                    radius: 11
                    color: page.newWorktree ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.16)
                    border.width: activeFocus ? 2 : 0
                    border.color: Tide.Tokens.textPrimary
                    activeFocusOnTab: true
                    Accessible.role: Accessible.CheckBox
                    Accessible.name: "Nowy worktree"
                    Accessible.checked: page.newWorktree
                    Keys.onSpacePressed: page.newWorktree = !page.newWorktree
                    Rectangle {
                        width: 16
                        height: 16
                        radius: 8
                        y: 3
                        x: page.newWorktree ? parent.width - width - 3 : 3
                        color: page.newWorktree ? Tide.Tokens.backgroundDesktop : Tide.Tokens.textSecondary
                        Behavior on x { NumberAnimation { duration: 120 } }
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: page.newWorktree = !page.newWorktree
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 2
                    Text {
                        text: "Nowy worktree"
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }
                    Text {
                        Layout.fillWidth: true
                        text: page.hint.text
                        color: page.hint.warn ? Tide.Tokens.butter : Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        elide: Text.ElideRight
                    }
                }
                // Gałąź jako znacznik; „zmień” zamienia go w pole tekstowe.
                Rectangle {
                    id: branchChip
                    objectName: "branchChip"
                    visible: page.newWorktree
                    Layout.maximumWidth: 460
                    implicitWidth: page.editingBranch ? 380 : chipRow.implicitWidth + 24
                    implicitHeight: 34
                    radius: 8
                    color: Tide.Theme.alpha(Tide.Tokens.lavender, page.editingBranch ? 0.10 : 0.14)
                    border.width: page.editingBranch ? 1 : 0
                    border.color: Tide.Theme.alpha(Tide.Tokens.lavender, 0.6)
                    QQC2.ToolTip.visible: chipArea.containsMouse && page.worktreePath !== ""
                    QQC2.ToolTip.text: Logic.tildePath(page.worktreePath, page.homeDir)
                    QQC2.ToolTip.delay: 500
                    Row {
                        id: chipRow
                        visible: !page.editingBranch
                        anchors.verticalCenter: parent.verticalCenter
                        x: 12
                        spacing: 8
                        Text {
                            text: "⑂ " + (page.branch || "…")
                            color: Tide.Tokens.lavender
                            font.family: Tide.Theme.monoFamily
                            font.pixelSize: 13
                            width: Math.min(implicitWidth, 360)
                            elide: Text.ElideMiddle
                        }
                        Text {
                            text: "✎ zmień"
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                            anchors.baseline: parent.children[0].baseline
                        }
                    }
                    MouseArea {
                        id: chipArea
                        anchors.fill: parent
                        visible: !page.editingBranch
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: {
                            page.editingBranch = true
                            branchInput.forceActiveFocus()
                            branchInput.selectAll()
                        }
                    }
                    TextInput {
                        id: branchInput
                        visible: page.editingBranch
                        anchors.fill: parent
                        anchors.leftMargin: 12
                        anchors.rightMargin: 12
                        verticalAlignment: TextInput.AlignVCenter
                        color: Tide.Tokens.textPrimary
                        selectionColor: Tide.Tokens.lavender
                        selectedTextColor: Tide.Tokens.backgroundDesktop
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 13
                        clip: true
                        text: page.branch
                        onTextEdited: {
                            page.branch = text
                            // Wyczyszczone pole wraca do propozycji z polecenia.
                            page.branchEdited = text.trim() !== ""
                            if (!page.branchEdited) page.askSuggestion()
                        }
                        onActiveFocusChanged: if (!activeFocus) page.editingBranch = false
                        Keys.onReturnPressed: page.focusPrompt()
                        Keys.onEscapePressed: page.focusPrompt()
                        Accessible.role: Accessible.EditableText
                        Accessible.name: "Nazwa gałęzi"
                    }
                }
            }
        }

        // Błąd z usługi.
        Rectangle {
            Layout.fillWidth: true
            Layout.topMargin: 6
            visible: page.error !== ""
            objectName: "errorBox"
            radius: Tide.Tokens.radiusSmall
            color: Tide.Theme.alpha(Tide.Tokens.rose, 0.10)
            border.width: 1
            border.color: Tide.Theme.alpha(Tide.Tokens.rose, 0.45)
            implicitHeight: errorText.implicitHeight + 16
            Text {
                id: errorText
                anchors.fill: parent
                anchors.margins: 8
                anchors.leftMargin: 12
                text: page.error
                color: Tide.Tokens.rose
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                wrapMode: Text.WordWrap
            }
        }

        // Co się stanie i przyciski.
        RowLayout {
            Layout.fillWidth: true
            Layout.topMargin: 12
            spacing: 12
            Text {
                objectName: "outcome"
                Layout.fillWidth: true
                text: page.busy ? "Zakładam worktree i otwieram Konsolę…"
                    : page.blocker !== "" ? page.blocker : Logic.outcome(page.repo, page.tool, page.newWorktree,
                                                                                page.model === "local" ? page.localOption : null)
                color: page.blocker !== "" && !page.busy ? Tide.Tokens.textMuted : Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                elide: Text.ElideRight
            }
            PillButton {
                text: "Anuluj"
                implicitHeight: 40
                horizontalPadding: 20
                pixelSize: 14
                fill: "transparent"
                textColor: Tide.Tokens.textPrimary
                border.width: 1
                border.color: activeFocus ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.16)
                enabled: !page.busy
                onClicked: page.cancelRequested()
            }
            Rectangle {
                id: startButton
                objectName: "startButton"
                implicitHeight: 40
                implicitWidth: startRow.implicitWidth + 40
                radius: Tide.Tokens.radiusFrame
                color: !page.canStart ? Tide.Theme.alpha(Tide.Tokens.accent, 0.35)
                     : startArea.pressed ? Qt.darker(Tide.Tokens.accent, 1.08)
                     : startArea.containsMouse || activeFocus ? Qt.lighter(Tide.Tokens.accent, 1.06) : Tide.Tokens.accent
                border.width: activeFocus ? 2 : 0
                border.color: Tide.Tokens.textPrimary
                activeFocusOnTab: true
                Accessible.role: Accessible.Button
                Accessible.name: "Uruchom agenta"
                Keys.onReturnPressed: page.start()
                Keys.onSpacePressed: page.start()
                Row {
                    id: startRow
                    anchors.centerIn: parent
                    spacing: 10
                    Text {
                        id: startLabel
                        text: page.busy ? "Uruchamiam…" : "Uruchom agenta"
                        color: Tide.Tokens.backgroundDesktop
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 14
                        font.weight: Font.DemiBold
                    }
                    Text {
                        anchors.baseline: startLabel.baseline
                        text: "Ctrl+↵"
                        color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.7)
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 11
                    }
                }
                MouseArea {
                    id: startArea
                    anchors.fill: parent
                    hoverEnabled: true
                    enabled: page.canStart
                    cursorShape: Qt.PointingHandCursor
                    onClicked: page.start()
                }
            }
        }
    }
}

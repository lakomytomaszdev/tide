// Okno „Co ma zrobić agent?” (mock zatwierdzony 27.10.2026) — modalnie nad tablicą, po przeniesieniu
// karty z Backlogu do Do zrobienia albo z „Uruchom agenta…” na karcie. Karta (ID, ◆ projekt, tagi),
// zadanie z opisu karty (zmiany zapiszą się w karcie), narzędzie, wejście („Jak zacząć?”: Planowanie albo Tylko specyfikacja), tryb Planowania (Pełny, Lekki) z paskiem kroków,
// miejsce dokumentów AI z linkiem do ustawień projektu, „Dodatkowe projekty” (PT-28: chipy i wybór
// z projektów tablicy oraz repozytoriów — agent dostaje je przez --add-dir), „Nowy worktree” z gałęzią (przy wejściu Tylko
// specyfikacja wyłączony), „Test ręczny po zakończeniu”, zdanie „co się stanie” i przyciski Anuluj, Tylko przenieś,
// Uruchom agenta (Ctrl+Enter). Karta bez projektu z repozytorium — wyjaśnienie i wybór projektu.
// To, co ma okno „Nowe zadanie” (PT-43): zrzuty ekranu pod zadaniem (Tide.ScreenshotStrip z widgets/lib —
// miniatury, „Zrób zrzut”, przeciąganie plików na okno, Ctrl+V z obrazem w schowku) i model obok narzędzia
// (☁ Chmura / ⌂ Lokalny z roli `agent` w /Models — logika w widgets/lib/agentmodel.js; przy „Lokalny” zamiast
// przełącznika narzędzia linia „Codex na … · Ten komputer” z programem z Tide → Modele, uwaga pod spodem;
// brak programu albo niedostępny model blokuje start z powodem w stopce).
// Sam nie rozmawia z usługą: zgłasza sygnały, a stronę tablicy (BoardPage) prowadzi przebieg
// UpdateCard (opis, dodatkowe projekty) → RenderLaunch → /Tasks Start (z `images` i `model`) → UpdateCard.
// Teksty z danych — zawsze PlainText.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "tide/agentmodel.js" as AgentModel
import "board.js" as Logic

Rectangle {
    id: dialog

    property var snapshot: null
    property string cardId: ""
    property bool fromMove: false
    property bool busy: false
    property string error: ""
    property string busyText: "Uruchamiam…"
    property var tagTone: null
    // Wybór w oknie.
    property string entry: "planning"      // wejście („Jak zacząć?”): planning | spec
    property string mode: "full"           // tryb Planowania: full | light (przy innym wejściu zapamiętany)
    property string tool: "claude"
    property string branch: ""
    property bool branchEdited: false
    property bool editingBranch: false
    property bool newWorktree: true
    property bool manualCheck: false
    property var extras: []                // dodatkowe projekty: [{kind: "project"|"repo", key, name}]
    property string loadedDescription: ""
    // Jak w oknie „Nowe zadanie” (PT-43): zrzuty (ścieżki z backendu), model i dane /Tasks.
    property string model: "cloud"         // "cloud" | "local"
    property var images: []
    property bool canCapture: false
    property var modelsStatus: null        // Status() z /Models; {pending: true} — czekam; null — usługa nie podaje
    property var taskDefaults: ({})        // /Tasks Defaults: tools, model (zapamiętany), imagesDir
    property bool modelTouched: false      // użytkownik wybrał model w tym oknie — Defaults() już go nie zmienia
    // Ctrl+V: funkcja() → true, gdy schowek miał obraz (albo pliki obrazów) i trafił do zrzutów.
    property var pasteImage: function (inTextField) { return false }
    readonly property var localOption: AgentModel.localOption(modelsStatus)
    readonly property var localLine: AgentModel.localLine(localOption)
    readonly property string modelNote: AgentModel.modelNote(localOption, model)
    // Wejście przepływu karty („Od razu”, PT-58): krok w tle z wykonawcą z katalogu zamiast modelu i narzędzia.
    readonly property bool flowEntry: Logic.isFlowEntry(entry, mode)
    property string executor: ""
    property string preferredProvider: "anthropic"
    readonly property var providerOptions: Logic.preferredProviders(modelsStatus)
    readonly property var executorOptions: entry === "planning" ? Logic.planningExecutors(modelsStatus, preferredProvider)
                                                                  : Logic.launchExecutors(modelsStatus, preferredProvider)
    readonly property var selectedExecutor: Logic.launchExecutor(modelsStatus, executor)
    property string reviewer: ""
    readonly property var reviewerOptions: Logic.reviewExecutors(modelsStatus, executor, preferredProvider)
    // Program, który ruszy: przy „Lokalny” z Tide → Modele (agentProgram), w chmurze — przełącznik narzędzia;
    // przy „Od razu” — program wybranego wykonawcy z katalogu.
    readonly property string agentTool: flowEntry ? (selectedExecutor ? selectedExecutor.program : "")
                                                  : AgentModel.agentTool(model, tool, localOption)
    readonly property var tools: taskDefaults && taskDefaults.tools ? taskDefaults.tools : ({})
    readonly property string modelBlocker: flowEntry && executor === "" ? "Brak wykonawcy u wybranego dostawcy (Tide → Modele)."
        : flowEntry && selectedExecutor && selectedExecutor.available === false
          ? (selectedExecutor.error || "Wykonawca lokalny jest niedostępny.")
        : tools[agentTool] === false ? "Nie znaleziono programu " + Logic.launchToolName(agentTool) + " — zainstaluj go albo wybierz inny."
        : entry === "now" && reviewer === "" ? "Brak recenzenta z innym modelem (Tide → Modele)."
        : !flowEntry && model === "local" && !localOption.enabled
          ? (localOption.pending ? localOption.reason : "Model lokalny niedostępny: " + localOption.reason) : ""
    readonly property var card: cardId ? ((snapshot && snapshot.cards) || []).find(c => c.id === cardId) || null : null
    readonly property var project: card && card.project ? Logic.projectBySlug(snapshot, card.project) : null
    readonly property var workProject: Logic.launchProject(snapshot, card)
    readonly property string blocker: Logic.launchBlocker(snapshot, card)
    readonly property bool canStart: blocker === "" && modelBlocker === "" && !busy && taskText.trim() !== ""
    readonly property string taskText: taskEdit.text
    readonly property var launchState: ({
        cardId: cardId, entry: entry, mode: mode, tool: agentTool, branch: branch, newWorktree: newWorktree,
        executor: executor, reviewer: reviewer, preferredProvider: preferredProvider,
        model: model === "local" ? "local" : "cloud", localModel: model === "local" ? localOption.model : "",
        images: images,
        check: manualCheck ? "manual" : "auto", aiDocs: workProject ? workProject.aiDocs : "",
        slug: workProject ? workProject.slug : "",
        repoName: workProject ? String(workProject.repo).split("/").filter(x => x).pop() : "",
        extras: extras, projects: extras.map(p => p.key), projectsChanged: Logic.extrasChanged(card, extras)
    })
    readonly property alias taskEdit: taskEdit

    // Karta wyboru (wejście albo tryb): radio z tytułem, opisem i uwagą; strzałki przesuwają wybór.
    component ChoiceCard: Rectangle {
        id: choiceCard
        property string choiceId: ""
        property var choices: []
        property string selected: ""
        property string title: ""
        property string desc: ""
        property string meta: ""
        readonly property bool on: choiceId === selected
        signal picked(string id)
        Layout.fillWidth: true
        Layout.preferredWidth: 1
        Layout.fillHeight: true
        implicitHeight: choiceColumn.implicitHeight + 28
        radius: Tide.Tokens.radiusTile
        color: on ? Tide.Theme.alpha(Tide.Tokens.accent, 0.09)
             : choiceArea.containsMouse ? Qt.lighter(Tide.Tokens.backgroundCard, 1.06) : Tide.Tokens.backgroundCard
        border.width: 1
        border.color: on || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                        : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
        activeFocusOnTab: true
        Accessible.role: Accessible.RadioButton
        Accessible.name: title
        Accessible.checked: on
        Accessible.onPressAction: picked(choiceId)
        Keys.onSpacePressed: picked(choiceId)
        Keys.onLeftPressed: picked(choices[Math.max(0, choices.indexOf(selected) - 1)])
        Keys.onRightPressed: picked(choices[Math.min(choices.length - 1, choices.indexOf(selected) + 1)])
        ColumnLayout {
            id: choiceColumn
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.margins: 14
            spacing: 8
            RowLayout {
                spacing: 8
                Rectangle {
                    implicitWidth: 14
                    implicitHeight: 14
                    radius: 7
                    color: "transparent"
                    border.width: choiceCard.on ? 4 : 1.5
                    border.color: choiceCard.on ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.35)
                }
                Text {
                    text: choiceCard.title
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 15
                    font.weight: Font.DemiBold
                }
            }
            Text {
                Layout.fillWidth: true
                text: choiceCard.desc
                textFormat: Text.PlainText
                color: Tide.Tokens.textSecondary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                lineHeight: 1.1
                wrapMode: Text.Wrap
            }
            Text {
                Layout.fillWidth: true
                text: choiceCard.meta
                textFormat: Text.PlainText
                color: Tide.Tokens.textMuted
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
                wrapMode: Text.Wrap
            }
        }
        MouseArea {
            id: choiceArea
            anchors.fill: parent
            hoverEnabled: true
            cursorShape: Qt.PointingHandCursor
            onClicked: choiceCard.picked(choiceCard.choiceId)
        }
    }

    signal launchRequested(var choice, string description, bool descriptionChanged)
    signal cancelRequested()
    signal moveOnlyRequested()
    signal projectChosen(string kind, string key)
    signal manageRequested(string slug)
    signal captureRequested()
    signal removeImageRequested(string path)
    signal filesDropped(var urls)
    signal modelsRequested()   // „ustaw/zmień w Tide → Modele”: widok Modele okna Tide

    function tone(name) {
        return tagTone ? tagTone(name) : Tide.Tokens.textMuted
    }

    // Domyślne wejście i tryb projektu (dawne `mode: spec` to wejście Tylko specyfikacja).
    function applyProjectDefaults(p) {
        const d = Logic.launchDefaults(p)
        entry = d.entry
        mode = d.mode
    }

    // Wartości z karty i projektu przy otwarciu (i po wybraniu projektu dla karty bez niego).
    function load() {
        const c = card
        const p = Logic.launchProject(snapshot, c)
        branchEdited = false
        editingBranch = false
        applyProjectDefaults(p)
        // Model jak w oknie „Nowe zadanie”: zapamiętany wybór z ostatniego startu (Defaults().model).
        modelTouched = false
        model = taskDefaults && taskDefaults.model === "local" ? "local" : "cloud"
        tool = p ? p.tool || "claude" : "claude"
        branch = Logic.launchBranch(c, agentTool)
        newWorktree = true
        manualCheck = false
        extras = Logic.cardExtras(snapshot, c)
        error = ""
        busy = false
        loadedDescription = c ? (String(c.description || "").trim() || c.title || "") : ""
        taskEdit.text = loadedDescription
        Qt.callLater(() => taskEdit.forceActiveFocus())
    }

    // Po wybraniu projektu: narzędzie, tryb i gałąź z projektu, zadanie zostaje.
    function reloadProject() {
        const p = Logic.launchProject(snapshot, card)
        if (!p) return
        applyProjectDefaults(p)
        tool = p.tool || "claude"
        if (!branchEdited) branch = Logic.launchBranch(card, agentTool)
    }

    function pickTool(value) {
        tool = value
    }

    // Katalog wykonawców przychodzi z Status() (/Models) także po otwarciu okna: wybór zostaje, gdy jest na liście.
    onExecutorOptionsChanged: {
        if (!executorOptions.some(o => o.value === executor)) executor = Logic.defaultExecutor(executorOptions)
    }
    onReviewerOptionsChanged: {
        if (!reviewerOptions.some(o => o.value === reviewer)) reviewer = Logic.defaultReviewer(executor, reviewerOptions)
    }
    Component.onCompleted: {
        executor = Logic.defaultExecutor(executorOptions)
        reviewer = Logic.defaultReviewer(executor, reviewerOptions)
    }

    // Odświeżone Defaults() (przy otwarciu okna) przychodzą po load(): zapamiętany model, dopóki nikt go tu nie zmienił.
    onTaskDefaultsChanged: {
        if (!modelTouched && !busy) model = taskDefaults && taskDefaults.model === "local" ? "local" : "cloud"
    }

    // Prefiks gałęzi idzie za programem, który ruszy (przełącznik narzędzia albo program agenta lokalnego).
    onAgentToolChanged: {
        if (!card) return
        if (!branchEdited) branch = Logic.launchBranch(card, agentTool)
        else branch = Logic.retoolBranch(branch, agentTool)
    }

    function start() {
        if (!canStart) return
        error = ""
        const text = taskEdit.text
        launchRequested(launchState, text, text.trim() !== loadedDescription.trim())
    }

    color: Tide.Theme.alpha(Tide.Tokens.backgroundDesktop, 0.62)
    focus: visible
    Accessible.role: Accessible.Dialog
    Accessible.name: "Co ma zrobić agent?"

    Keys.onPressed: event => {
        if (event.key === Qt.Key_Escape) {
            if (editingBranch) editingBranch = false
            else if (!busy) cancelRequested()
            event.accepted = true
        } else if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) {
            start()
            event.accepted = true
        } else if (event.matches(StandardKey.Paste) && !busy && pasteImage(false)) {
            event.accepted = true   // obraz ze schowka trafił do zrzutów (fokus poza polem tekstowym)
        }
    }

    // Kliknięcia obok okna nic nie robią (okno modalne).
    MouseArea {
        anchors.fill: parent
        onWheel: wheel => wheel.accepted = true
    }

    Rectangle {
        id: panel
        objectName: "launchPanel"
        anchors.centerIn: parent
        width: Math.min(900, parent.width - 48)
        height: Math.min(bodyColumn.implicitHeight + 40 + footer.implicitHeight, parent.height - 32)
        radius: Tide.Tokens.radiusCard
        color: Tide.Tokens.backgroundWindow
        border.width: 1
        border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)
        clip: true

        MouseArea { anchors.fill: parent }

        Flickable {
            id: bodyFlick
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: footer.top
            contentWidth: width
            contentHeight: bodyColumn.implicitHeight + 40
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            interactive: contentHeight > height
            QQC2.ScrollBar.vertical: QQC2.ScrollBar { policy: bodyFlick.interactive ? QQC2.ScrollBar.AsNeeded : QQC2.ScrollBar.AlwaysOff }

            ColumnLayout {
                id: bodyColumn
                x: 28
                y: 20
                width: bodyFlick.width - 56
                spacing: 16

                // Nagłówek.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 12
                    Text {
                        Layout.fillWidth: true
                        text: "Co ma zrobić agent?"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 21
                        font.weight: Font.DemiBold
                        Accessible.role: Accessible.Heading
                    }
                    Text {
                        objectName: "launchWhere"
                        text: dialog.fromMove ? "Backlog → Do zrobienia" : "Do zrobienia"
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                    }
                }

                // Karta i zadanie.
                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: cardColumn.implicitHeight + 28
                    radius: Tide.Tokens.radiusTile
                    color: Tide.Tokens.backgroundView
                    border.width: 1
                    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.08)
                    ColumnLayout {
                        id: cardColumn
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 14
                        spacing: 10
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Text {
                                objectName: "launchCardId"
                                text: dialog.cardId
                                textFormat: Text.PlainText
                                color: Tide.Tokens.sky
                                font.family: Tide.Theme.monoFamily
                                font.pixelSize: 12
                                font.weight: Font.DemiBold
                            }
                            Text {
                                visible: dialog.project !== null
                                text: "◆"
                                textFormat: Text.PlainText
                                color: Tide.Tokens.accent
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 14
                            }
                            Text {
                                objectName: "launchProject"
                                Layout.leftMargin: -4
                                visible: dialog.project !== null
                                text: dialog.project ? dialog.project.name : ""
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textPrimary
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                            }
                            Repeater {
                                model: dialog.card ? dialog.card.resolvedTags || [] : []
                                TagChip {
                                    required property var modelData
                                    name: modelData
                                    dotColor: dialog.tone(modelData)
                                }
                            }
                            Text {
                                Layout.fillWidth: true
                                horizontalAlignment: Text.AlignRight
                                text: dialog.card ? "tytuł z karty: " + dialog.card.title : ""
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }
                        }

                        // Karta bez projektu z repozytorium: wyjaśnienie i wybór.
                        Rectangle {
                            objectName: "launchBlocker"
                            Layout.fillWidth: true
                            visible: dialog.blocker !== "" && dialog.card !== null
                            implicitHeight: blockerColumn.implicitHeight + 20
                            radius: 10
                            color: Tide.Theme.alpha(Tide.Tokens.butter, 0.07)
                            border.width: 1
                            border.color: Tide.Theme.alpha(Tide.Tokens.butter, 0.3)
                            ColumnLayout {
                                id: blockerColumn
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: 12
                                spacing: 8
                                Text {
                                    objectName: "launchBlockerText"
                                    Layout.fillWidth: true
                                    text: dialog.blocker
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.butter
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 12
                                    wrapMode: Text.Wrap
                                }
                                ProjectPicker {
                                    id: picker
                                    objectName: "launchProjectPicker"
                                    Layout.preferredWidth: 360
                                    snapshot: dialog.snapshot
                                    kind: ""
                                    key: ""
                                    label: ""
                                    onChosen: (kind, key, label) => { if (kind) dialog.projectChosen(kind, key) }
                                }
                            }
                        }

                        FieldLabel { text: "Zadanie"; color: Tide.Tokens.textSecondary }
                        Field {
                            Layout.fillWidth: true
                            implicitHeight: 96
                            focused: taskEdit.activeFocus
                            Flickable {
                                id: taskFlick
                                anchors.fill: parent
                                anchors.margins: 10
                                clip: true
                                contentWidth: width
                                contentHeight: taskEdit.implicitHeight
                                boundsBehavior: Flickable.StopAtBounds
                                QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                                TextEdit {
                                    id: taskEdit
                                    objectName: "launchTask"
                                    width: taskFlick.width
                                    wrapMode: TextEdit.Wrap
                                    textFormat: TextEdit.PlainText
                                    color: Tide.Tokens.textPrimary
                                    selectionColor: Tide.Tokens.accent
                                    selectedTextColor: Tide.Tokens.backgroundDesktop
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 13
                                    activeFocusOnTab: true
                                    // Tab: „Zrób zrzut” (gdy jest), potem model i dalej.
                                    Keys.onTabPressed: event => {
                                        if (!shots.focusCapture()) modelSwitch.forceActiveFocus()
                                        event.accepted = true
                                    }
                                    Keys.onPressed: event => {
                                        // W polu: schowek z tekstem i obrazem (komórki LibreOffice) wkleja tekst.
                                        if (event.matches(StandardKey.Paste) && !dialog.busy && dialog.pasteImage(true))
                                            event.accepted = true   // obraz ze schowka trafił do zrzutów
                                    }
                                    onCursorRectangleChanged: {
                                        if (cursorRectangle.y < taskFlick.contentY) taskFlick.contentY = cursorRectangle.y
                                        else if (cursorRectangle.y + cursorRectangle.height > taskFlick.contentY + taskFlick.height)
                                            taskFlick.contentY = cursorRectangle.y + cursorRectangle.height - taskFlick.height
                                    }
                                    Accessible.role: Accessible.EditableText
                                    Accessible.name: "Zadanie dla agenta"
                                }
                            }
                        }
                        Text {
                            Layout.fillWidth: true
                            text: "Opis z karty. Zmiany tutaj zapiszą się też w karcie. Ctrl+V wkleja zrzut ze schowka."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                        }

                        // Zrzuty ekranu (wspólny pasek z oknem „Nowe zadanie”) — trafiają do agenta razem z poleceniem.
                        FieldLabel { text: "Zrzuty ekranu"; color: Tide.Tokens.textSecondary; Layout.topMargin: 2 }
                        Tide.ScreenshotStrip {
                            id: shots
                            objectName: "launchShots"
                            Layout.fillWidth: true
                            images: dialog.images
                            canCapture: dialog.canCapture
                            busy: dialog.busy
                            dropHover: drop.containsDrag
                            thumbWidth: 128
                            thumbHeight: 78
                            onCaptureRequested: dialog.captureRequested()
                            onRemoveRequested: path => dialog.removeImageRequested(path)
                        }
                    }
                }

                // Model i narzędzie (PT-43: jak w oknie „Nowe zadanie”) — najpierw model, potem program. Przy „Od razu”
                // zamiast nich wykonawca (niżej, przy wejściach).
                RowLayout {
                    Layout.fillWidth: true
                    visible: !dialog.flowEntry
                    spacing: 10
                    FieldLabel { text: "Model"; color: Tide.Tokens.textSecondary }
                    Segmented {
                        id: modelSwitch
                        objectName: "launchModel"
                        options: [{ value: "cloud", label: "☁ Chmura" },
                                  { value: "local", label: "⌂ Lokalny", disabled: !dialog.localOption.enabled,
                                    hint: dialog.localOption.enabled ? dialog.localOption.label : dialog.localOption.reason }]
                        current: dialog.model
                        itemHeight: 30
                        pixelSize: 13
                        onPicked: value => {
                            dialog.modelTouched = true
                            dialog.model = value
                        }
                    }
                    FieldLabel { text: "Narzędzie"; color: Tide.Tokens.textSecondary; Layout.leftMargin: 14 }
                    Segmented {
                        id: toolSwitch
                        objectName: "launchTool"
                        visible: dialog.model !== "local"
                        options: Logic.LAUNCH_TOOLS.map(t => ({ value: t.value, label: t.label + (dialog.tools[t.value] === false ? " · brak" : "") }))
                        current: dialog.tool
                        itemHeight: 30
                        pixelSize: 13
                        onPicked: value => dialog.pickTool(value)
                    }
                    // Przy „Lokalny”: program z Tide → Modele, model i serwer (program to nie model — tu tylko go widać).
                    Text {
                        objectName: "launchLocalProgram"
                        visible: dialog.model === "local"
                        text: (dialog.localOption.program === "codex" ? "◎ " : "✻ ") + dialog.localLine.program
                              + (dialog.tools[dialog.localOption.program] === false ? " · brak" : "")
                        textFormat: Text.PlainText
                        color: dialog.localOption.enabled ? Tide.Tokens.accent : Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        font.weight: Font.DemiBold
                    }
                    Text {
                        objectName: "launchLocalDetail"
                        Layout.fillWidth: true
                        Layout.leftMargin: -6
                        visible: dialog.model === "local"
                        text: dialog.localLine.detail
                        textFormat: Text.PlainText
                        color: dialog.localOption.enabled ? Tide.Tokens.textSecondary : Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        elide: Text.ElideRight
                    }
                    Text {
                        objectName: "launchProgramLink"
                        visible: dialog.model === "local"
                        text: dialog.localLine.link
                        textFormat: Text.PlainText
                        color: programArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.accent, 0.8) : Tide.Tokens.accent
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        Accessible.role: Accessible.Link
                        Accessible.name: dialog.localLine.link === AgentModel.PROGRAM_HINT ? "Zmień program agenta w Tide → Modele"
                                                                                           : "Ustaw model agenta w Tide → Modele"
                        Accessible.onPressAction: dialog.modelsRequested()
                        MouseArea {
                            id: programArea
                            anchors.fill: parent
                            anchors.margins: -4
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: dialog.modelsRequested()
                        }
                    }
                    Item { Layout.fillWidth: true; visible: dialog.model !== "local" }
                }
                // Uwaga o modelu przy „Lokalny”: powód niedostępności albo uczciwa uwaga o jakości (jak w „Nowe zadanie”).
                // W chmurze okno zostaje ciche (tablica jest gęstsza): powód wyłączenia „Lokalnego” jest w jego podpowiedzi,
                // a odnośnik do Tide → Modele — w linii agenta.
                Text {
                    objectName: "launchModelNote"
                    Layout.fillWidth: true
                    Layout.topMargin: -8
                    visible: dialog.modelNote !== "" && dialog.model === "local" && !dialog.flowEntry
                    text: dialog.modelNote
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    elide: Text.ElideRight
                }

                // Dodatkowe projekty (PT-28): zadanie, które zmienia kilka repozytoriów.
                RowLayout {
                    objectName: "launchExtras"
                    Layout.fillWidth: true
                    visible: dialog.workProject !== null && dialog.entry !== "now"   // krok w tle: jeden worktree ticketu
                    spacing: 10
                    FieldLabel { text: "Dodatkowe projekty"; color: Tide.Tokens.textSecondary }
                    Flow {
                        Layout.fillWidth: true
                        spacing: 6
                        Repeater {
                            model: dialog.extras
                            Rectangle {
                                id: extraChip
                                required property var modelData
                                objectName: "launchExtra-" + modelData.key
                                implicitWidth: extraRow.implicitWidth + 16
                                implicitHeight: 28
                                radius: 7
                                color: Tide.Tokens.backgroundCard
                                border.width: 1
                                border.color: Tide.Theme.alpha(modelData.kind === "repo" ? Tide.Tokens.butter : Tide.Tokens.accent, 0.35)
                                Accessible.role: Accessible.StaticText
                                Accessible.name: "Dodatkowy projekt " + modelData.name
                                Row {
                                    id: extraRow
                                    anchors.centerIn: parent
                                    spacing: 6
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "◆"
                                        textFormat: Text.PlainText
                                        color: extraChip.modelData.kind === "repo" ? Tide.Tokens.butter : Tide.Tokens.accent
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 10
                                    }
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: extraChip.modelData.name + (extraChip.modelData.kind === "repo" ? " · nowy projekt" : "")
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textPrimary
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 12
                                    }
                                    Text {
                                        objectName: "launchExtraRemove-" + extraChip.modelData.key
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: "✕"
                                        textFormat: Text.PlainText
                                        color: removeArea.containsMouse ? Tide.Tokens.rose : Tide.Tokens.textMuted
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 11
                                        Accessible.role: Accessible.Button
                                        Accessible.name: "Usuń " + extraChip.modelData.name
                                        Accessible.onPressAction: dialog.extras = Logic.removeExtra(dialog.extras, extraChip.modelData.key)
                                        MouseArea {
                                            id: removeArea
                                            anchors.fill: parent
                                            anchors.margins: -4
                                            enabled: !dialog.busy
                                            hoverEnabled: true
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: dialog.extras = Logic.removeExtra(dialog.extras, extraChip.modelData.key)
                                        }
                                    }
                                }
                            }
                        }
                        ProjectPicker {
                            id: extraPicker
                            objectName: "launchExtraPicker"
                            visible: dialog.extras.length < Logic.EXTRA_MAX
                            width: 230
                            implicitHeight: 28
                            snapshot: dialog.snapshot
                            kind: ""
                            key: ""
                            label: ""
                            placeholder: "+ dodaj projekt albo repozytorium"
                            optionSource: query => Logic.extraOptions(dialog.snapshot, dialog.card, dialog.extras, query)
                            onChosen: (kind, key, label) => {
                                if (kind) dialog.extras = Logic.addExtra(dialog.extras, kind, key, label)
                            }
                        }
                    }
                }

                // Tryb pracy.
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: 8
                    // Nagłówek trybu z miejscem dokumentów AI (tam agent pisze specyfikację i plan).
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        FieldLabel { text: "Jak zacząć?"; color: Tide.Tokens.textSecondary }
                        Item { Layout.fillWidth: true }
                        RowLayout {
                            objectName: "launchDocsRow"
                            spacing: 8
                            visible: dialog.workProject !== null
                            FieldLabel { text: "Dokumenty AI"; color: Tide.Tokens.textSecondary }
                            Rectangle {
                                implicitWidth: docsText.implicitWidth + 16
                                implicitHeight: docsText.implicitHeight + 8
                                radius: 7
                                color: Tide.Tokens.backgroundCard
                                Text {
                                    id: docsText
                                    objectName: "launchDocs"
                                    anchors.centerIn: parent
                                    text: Logic.docsLabel(dialog.workProject)
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textSecondary
                                    font.family: Tide.Theme.monoFamily
                                    font.pixelSize: 12
                                }
                            }
                            Text {
                                objectName: "launchManageLink"
                                text: "zmień w projekcie"
                                textFormat: Text.PlainText
                                color: manageArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.accent, 0.8) : Tide.Tokens.accent
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                Accessible.role: Accessible.Link
                                Accessible.name: "Zmień dokumenty AI w ustawieniach projektu"
                                Accessible.onPressAction: dialog.manageRequested(dialog.workProject.slug)
                                MouseArea {
                                    id: manageArea
                                    anchors.fill: parent
                                    anchors.margins: -4
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: dialog.manageRequested(dialog.workProject.slug)
                                }
                            }
                        }
                    }
                    // Wejście: sposób rozpoczęcia zlecenia (Planowanie, Tylko specyfikacja; kolejne dojdą tu same).
                    RowLayout {
                        objectName: "launchEntries"
                        Layout.fillWidth: true
                        spacing: 10
                        Repeater {
                            model: Logic.LAUNCH_ENTRIES
                            ChoiceCard {
                                required property var modelData
                                objectName: "launchEntry-" + modelData.id
                                choiceId: modelData.id
                                choices: Logic.LAUNCH_ENTRIES.map(e => e.id)
                                selected: dialog.entry
                                title: modelData.title
                                desc: modelData.desc
                                meta: modelData.meta
                                onPicked: id => dialog.entry = id
                            }
                        }
                    }
                    // Tryb (Pełny, Lekki): tylko przy wejściu z wyborem trybu, czyli Planowaniu.
                    ColumnLayout {
                        objectName: "launchModes"
                        Layout.fillWidth: true
                        spacing: 8
                        visible: Logic.entryHasModes(dialog.entry)
                        FieldLabel { text: "Tryb"; color: Tide.Tokens.textSecondary }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 10
                            Repeater {
                                model: Logic.LAUNCH_MODES
                                ChoiceCard {
                                    required property var modelData
                                    objectName: "launchMode-" + modelData.id
                                    choiceId: modelData.id
                                    choices: Logic.LAUNCH_MODES.map(m => m.id)
                                    selected: dialog.mode
                                    title: modelData.title
                                    desc: modelData.desc
                                    meta: modelData.meta
                                    onPicked: id => dialog.mode = id
                                }
                            }
                        }
                    }
                    // Dostawca jest wspólnym wyborem dla planowania i kroku „Od razu”; wykonawcę wybiera się z
                    // katalogu tego dostawcy. Tryb mieszany pokazuje cały katalog.
                    RowLayout {
                        Layout.fillWidth: true
                        visible: dialog.entry !== "spec"
                        spacing: 10
                        FieldLabel { text: "Preferowany dostawca"; color: Tide.Tokens.textSecondary }
                        Segmented {
                            objectName: "launchPreferredProvider"
                            options: dialog.providerOptions
                            current: dialog.preferredProvider
                            itemHeight: 30
                            pixelSize: 12
                            onPicked: value => dialog.preferredProvider = value
                        }
                        Item { Layout.fillWidth: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: dialog.entry === "planning" && dialog.mode === "light"
                        spacing: 10
                        FieldLabel { text: "Planujący"; color: Tide.Tokens.textSecondary }
                        Segmented {
                            objectName: "launchPlanner"
                            options: dialog.executorOptions
                            current: dialog.executor
                            itemHeight: 30
                            pixelSize: 13
                            onPicked: value => dialog.executor = value
                        }
                        Item { Layout.fillWidth: true }
                    }
                    // Wykonawca kroku „Od razu” z katalogu wybranego dostawcy.
                    // Wykonawca ticketu i niezależny recenzent z katalogu.
                    RowLayout {
                        Layout.fillWidth: true
                        visible: dialog.entry === "now"
                        spacing: 10
                        FieldLabel { text: "Wykonawca"; color: Tide.Tokens.textSecondary }
                        Segmented {
                            objectName: "launchExecutor"
                            options: dialog.executorOptions
                            current: dialog.executor
                            itemHeight: 30
                            pixelSize: 13
                            onPicked: value => dialog.executor = value
                        }
                        Item { Layout.fillWidth: true }
                    }
                    RowLayout {
                        Layout.fillWidth: true
                        visible: dialog.entry === "now"
                        spacing: 10
                        FieldLabel { text: "Recenzent"; color: Tide.Tokens.textSecondary }
                        Segmented {
                            objectName: "launchReviewer"
                            options: dialog.reviewerOptions
                            current: dialog.reviewer
                            itemHeight: 30
                            pixelSize: 13
                            onPicked: value => dialog.reviewer = value
                        }
                        Item { Layout.fillWidth: true }
                    }
                    Text {
                        objectName: "launchCodexCommandPolicyNotice"
                        Layout.fillWidth: true
                        visible: Boolean(dialog.flowEntry && dialog.selectedExecutor
                                         && dialog.selectedExecutor.program === "codex")
                        text: "Codex działa w piaskownicy. Listy poleceń projektu są dla niego wskazówką, bez automatycznej blokady każdego polecenia."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.Wrap
                    }
                    Flow {
                        objectName: "launchSteps"
                        Layout.fillWidth: true
                        Layout.topMargin: 4
                        spacing: 6
                        Repeater {
                            model: Logic.launchSteps(dialog.entry, dialog.mode)
                            Row {
                                id: stepItem
                                required property var modelData
                                required property int index
                                spacing: 6
                                Text {
                                    visible: stepItem.index > 0
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "→"
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textMuted
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 12
                                }
                                Rectangle {
                                    implicitWidth: stepRow.implicitWidth + 20
                                    implicitHeight: stepRow.implicitHeight + 8
                                    radius: height / 2
                                    color: Tide.Tokens.backgroundCard
                                    Row {
                                        id: stepRow
                                        anchors.centerIn: parent
                                        spacing: 5
                                        Text {
                                            text: String(stepItem.index + 1)
                                            textFormat: Text.PlainText
                                            color: Tide.Tokens.accent
                                            font.family: Tide.Theme.monoFamily
                                            font.pixelSize: 12
                                        }
                                        Text {
                                            text: stepItem.modelData
                                            textFormat: Text.PlainText
                                            color: Tide.Tokens.textSecondary
                                            font.family: Tide.Theme.uiFamily
                                            font.pixelSize: 12
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                // Nowy worktree i test ręczny.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Rectangle {
                        objectName: "launchWorktree"
                        readonly property bool spec: dialog.entry === "spec"
                        visible: !dialog.flowEntry   // gałęzie i worktree karty i ticketu zakłada usługa
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        implicitHeight: worktreeColumn.implicitHeight + 28
                        radius: Tide.Tokens.radiusTile
                        color: Tide.Tokens.backgroundCard
                        opacity: spec ? 0.5 : 1
                        ColumnLayout {
                            id: worktreeColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 14
                            spacing: 8
                            RowLayout {
                                Layout.fillWidth: true
                                spacing: 12
                                Toggle {
                                    objectName: "launchWorktreeToggle"
                                    enabled: dialog.entry !== "spec"
                                    checked: dialog.newWorktree && dialog.entry !== "spec"
                                    accessibleName: "Nowy worktree"
                                    onToggled: value => dialog.newWorktree = value
                                }
                                Text {
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: implicitWidth
                                    text: "Nowy worktree"
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textPrimary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 14
                                    font.weight: Font.DemiBold
                                }
                                Rectangle {
                                    visible: !dialog.editingBranch
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 80
                                    Layout.maximumWidth: branchText.implicitWidth + 16
                                    implicitWidth: branchText.implicitWidth + 16
                                    implicitHeight: branchText.implicitHeight + 8
                                    radius: 6
                                    color: Tide.Theme.alpha(Tide.Tokens.lavender, 0.10)
                                    Text {
                                        id: branchText
                                        objectName: "launchBranch"
                                        anchors.centerIn: parent
                                        width: Math.min(implicitWidth, parent.width - 16)
                                        text: dialog.entry === "spec" || !dialog.newWorktree ? "bez gałęzi" : dialog.branch
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.lavender
                                        font.family: Tide.Theme.monoFamily
                                        font.pixelSize: 12
                                        elide: Text.ElideMiddle
                                    }
                                }
                                LineInput {
                                    id: branchInput
                                    objectName: "launchBranchInput"
                                    visible: dialog.editingBranch
                                    Layout.preferredWidth: 330
                                    implicitHeight: 30
                                    mono: true
                                    pixelSize: 12
                                    accessibleName: "Gałąź"
                                    onEdited: { dialog.branch = text.trim(); dialog.branchEdited = true }
                                    onAccepted: dialog.editingBranch = false
                                }
                                Text {
                                    objectName: "launchBranchEdit"
                                    visible: dialog.entry !== "spec" && dialog.newWorktree
                                    text: dialog.editingBranch ? "✓ gotowe" : "✎ zmień"
                                    textFormat: Text.PlainText
                                    color: branchArea.containsMouse ? Tide.Theme.alpha(Tide.Tokens.accent, 0.8) : Tide.Tokens.accent
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 12
                                    Accessible.role: Accessible.Button
                                    Accessible.name: dialog.editingBranch ? "Zatwierdź gałąź" : "Zmień gałąź"
                                    MouseArea {
                                        id: branchArea
                                        anchors.fill: parent
                                        anchors.margins: -4
                                        hoverEnabled: true
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: {
                                            dialog.editingBranch = !dialog.editingBranch
                                            if (dialog.editingBranch) {
                                                branchInput.text = dialog.branch
                                                branchInput.focusInput()
                                            }
                                        }
                                    }
                                }
                            }
                            Text {
                                objectName: "launchWorktreeNote"
                                Layout.fillWidth: true
                                text: Logic.worktreeNote(dialog.launchState)
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                    Rectangle {
                        objectName: "launchCheck"
                        Layout.preferredWidth: 280
                        Layout.fillHeight: true
                        implicitHeight: checkColumn.implicitHeight + 28
                        radius: Tide.Tokens.radiusTile
                        color: Tide.Tokens.backgroundCard
                        ColumnLayout {
                            id: checkColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 14
                            spacing: 8
                            RowLayout {
                                spacing: 12
                                Toggle {
                                    objectName: "launchCheckToggle"
                                    checked: dialog.manualCheck
                                    accessibleName: "Test ręczny po zakończeniu"
                                    onToggled: value => dialog.manualCheck = value
                                }
                                Text {
                                    text: "Test ręczny po zakończeniu"
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textPrimary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 14
                                    font.weight: Font.DemiBold
                                }
                            }
                            Text {
                                objectName: "launchCheckNote"
                                Layout.fillWidth: true
                                text: dialog.entry === "now"
                                      ? (dialog.manualCheck
                                         ? "Po review u wybranego recenzenta i scaleniu ticketu karta trafi do Do sprawdzenia i poczeka na Twój test ręczny."
                                         : "Po review u wybranego recenzenta ticket zostanie scalony z gałęzią karty i trafi do Do sprawdzenia.")
                                      : (dialog.manualCheck
                                         ? "Po review AI agent zostawi listę „Co przetestować ręcznie i jak” i nie scali gałęzi."
                                         : "Wystarczy review AI — agent kończy, gdy recenzent zatwierdzi.")
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }
            }
        }

        // Stopka: co się stanie, błąd i przyciski.
        Rectangle {
            id: footer
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            implicitHeight: footerColumn.implicitHeight + 28
            color: Tide.Tokens.backgroundView
            radius: Tide.Tokens.radiusCard
            Rectangle {   // górne rogi proste, linia oddzielająca
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: parent.radius
                color: parent.color
            }
            Rectangle {
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                height: 1
                color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
            }
            ColumnLayout {
                id: footerColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 28
                anchors.rightMargin: 28
                spacing: 10
                Text {
                    objectName: "launchError"
                    Layout.fillWidth: true
                    visible: dialog.error !== ""
                    text: dialog.error
                    textFormat: Text.PlainText
                    color: Tide.Tokens.rose
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 12
                    wrapMode: Text.Wrap
                    Accessible.role: Accessible.AlertMessage
                    Accessible.name: text
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 10
                    Text {
                        objectName: "launchFooter"
                        Layout.fillWidth: true
                        text: dialog.busy ? dialog.busyText
                            : dialog.blocker !== "" ? "Najpierw projekt z repozytorium — wtedy agent będzie wiedział, gdzie pracować."
                            : dialog.modelBlocker !== "" ? dialog.modelBlocker
                            : Logic.launchFooter(dialog.launchState)
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        lineHeight: 1.1
                        wrapMode: Text.Wrap
                    }
                    PillButton {
                        objectName: "launchCancel"
                        enabled: !dialog.busy
                        outline: true
                        text: "Anuluj"
                        toolTip: dialog.fromMove ? "Karta wraca do Backlogu (Esc)" : "Zamknij bez uruchamiania (Esc)"
                        onClicked: dialog.cancelRequested()
                    }
                    PillButton {
                        objectName: "launchMoveOnly"
                        visible: dialog.fromMove
                        enabled: !dialog.busy
                        outline: true
                        text: "Tylko przenieś"
                        toolTip: "Karta zostaje w Do zrobienia, bez agenta"
                        onClicked: dialog.moveOnlyRequested()
                    }
                    PillButton {
                        objectName: "launchStart"
                        enabled: dialog.canStart
                        strong: true
                        horizontalPadding: 16
                        text: dialog.busy ? "Uruchamiam…" : "Uruchom agenta  Ctrl+↵"
                        fill: Tide.Tokens.accent
                        textColor: Tide.Tokens.backgroundDesktop
                        toolTip: "Uruchom agenta (Ctrl+Enter)"
                        onClicked: dialog.start()
                    }
                }
            }
        }

        // Pliki obrazów przeciągnięte na okno — zrzuty zadania. Na wierzchu (także nad polem zadania, żeby
        // upuszczony plik nie wkleił się jako ścieżka); DropArea nie zabiera myszy ani klawiatury.
        DropArea {
            id: drop
            anchors.fill: parent
            keys: ["text/uri-list"]
            enabled: !dialog.busy
            onDropped: event => {
                dialog.filesDropped(event.urls.map(u => u.toString()))
                event.accept()
            }
        }
    }
}

// Okno „Co ma zrobić agent?” (mock zatwierdzony 27.10.2026) — modalnie nad tablicą, po przeniesieniu
// karty z Backlogu do Do zrobienia albo z „Uruchom agenta…” na karcie. Karta (ID, ◆ projekt, tagi),
// zadanie z opisu karty (zmiany zapiszą się w karcie), narzędzie, tryb pracy z paskiem kroków,
// miejsce dokumentów AI z linkiem do ustawień projektu, „Dodatkowe projekty” (PT-28: chipy i wybór
// z projektów tablicy oraz repozytoriów — agent dostaje je przez --add-dir), „Nowy worktree” z gałęzią (w trybie spec
// wyłączony), „Test ręczny po zakończeniu”, zdanie „co się stanie” i przyciski Anuluj, Tylko przenieś,
// Uruchom agenta (Ctrl+Enter). Karta bez projektu z repozytorium — wyjaśnienie i wybór projektu.
// Sam nie rozmawia z usługą: zgłasza sygnały, a stronę tablicy (BoardPage) prowadzi przebieg
// UpdateCard (opis, dodatkowe projekty) → RenderLaunch → /Tasks Start → UpdateCard. Teksty z danych —
// zawsze PlainText.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
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
    property string mode: "full"
    property string tool: "claude"
    property string branch: ""
    property bool branchEdited: false
    property bool editingBranch: false
    property bool newWorktree: true
    property bool manualCheck: false
    property var extras: []                // dodatkowe projekty: [{kind: "project"|"repo", key, name}]
    property string loadedDescription: ""
    readonly property var card: cardId ? ((snapshot && snapshot.cards) || []).find(c => c.id === cardId) || null : null
    readonly property var project: card && card.project ? Logic.projectBySlug(snapshot, card.project) : null
    readonly property var workProject: Logic.launchProject(snapshot, card)
    readonly property string blocker: Logic.launchBlocker(snapshot, card)
    readonly property bool canStart: blocker === "" && !busy && taskText.trim() !== ""
    readonly property string taskText: taskEdit.text
    readonly property var launchState: ({
        cardId: cardId, mode: mode, tool: tool, branch: branch, newWorktree: newWorktree,
        check: manualCheck ? "manual" : "auto", aiDocs: workProject ? workProject.aiDocs : "",
        slug: workProject ? workProject.slug : "",
        repoName: workProject ? String(workProject.repo).split("/").filter(x => x).pop() : "",
        extras: extras, projects: extras.map(p => p.key), projectsChanged: Logic.extrasChanged(card, extras)
    })
    readonly property alias taskEdit: taskEdit

    signal launchRequested(var choice, string description, bool descriptionChanged)
    signal cancelRequested()
    signal moveOnlyRequested()
    signal projectChosen(string kind, string key)
    signal manageRequested(string slug)

    function tone(name) {
        return tagTone ? tagTone(name) : Tide.Tokens.textMuted
    }

    // Wartości z karty i projektu przy otwarciu (i po wybraniu projektu dla karty bez niego).
    function load() {
        const c = card
        const p = Logic.launchProject(snapshot, c)
        mode = p ? p.mode || "full" : "full"
        tool = p ? p.tool || "claude" : "claude"
        branch = Logic.launchBranch(c, tool)
        branchEdited = false
        editingBranch = false
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
        mode = p.mode || "full"
        tool = p.tool || "claude"
        if (!branchEdited) branch = Logic.launchBranch(card, tool)
    }

    function pickTool(value) {
        tool = value
        if (!branchEdited) branch = Logic.launchBranch(card, value)
        else branch = Logic.retoolBranch(branch, value)
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
                                    Keys.onTabPressed: event => { toolSwitch.forceActiveFocus(); event.accepted = true }
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
                            text: "Opis z karty. Zmiany tutaj zapiszą się też w karcie."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                        }
                    }
                }

                // Narzędzie i dokumenty AI.
                RowLayout {
                    Layout.fillWidth: true
                    spacing: 24
                    RowLayout {
                        spacing: 10
                        FieldLabel { text: "Narzędzie"; color: Tide.Tokens.textSecondary }
                        Segmented {
                            id: toolSwitch
                            objectName: "launchTool"
                            options: Logic.LAUNCH_TOOLS
                            current: dialog.tool
                            itemHeight: 30
                            pixelSize: 13
                            onPicked: value => dialog.pickTool(value)
                        }
                    }
                    Item { Layout.fillWidth: true }
                    RowLayout {
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

                // Dodatkowe projekty (PT-28): zadanie, które zmienia kilka repozytoriów.
                RowLayout {
                    objectName: "launchExtras"
                    Layout.fillWidth: true
                    visible: dialog.workProject !== null
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
                    FieldLabel { text: "Tryb pracy"; color: Tide.Tokens.textSecondary }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 10
                        Repeater {
                            model: Logic.LAUNCH_MODES
                            Rectangle {
                                id: modeCard
                                required property var modelData
                                readonly property bool on: modelData.id === dialog.mode
                                objectName: "launchMode-" + modelData.id
                                Layout.fillWidth: true
                                Layout.preferredWidth: 1
                                Layout.fillHeight: true
                                implicitHeight: modeColumn.implicitHeight + 28
                                radius: Tide.Tokens.radiusTile
                                color: on ? Tide.Theme.alpha(Tide.Tokens.accent, 0.09)
                                     : modeArea.containsMouse ? Qt.lighter(Tide.Tokens.backgroundCard, 1.06) : Tide.Tokens.backgroundCard
                                border.width: 1
                                border.color: on || activeFocus ? Tide.Theme.alpha(Tide.Tokens.accent, 0.6)
                                                                : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
                                activeFocusOnTab: true
                                Accessible.role: Accessible.RadioButton
                                Accessible.name: modelData.title
                                Accessible.checked: on
                                Accessible.onPressAction: dialog.mode = modelData.id
                                Keys.onSpacePressed: dialog.mode = modelData.id
                                Keys.onLeftPressed: dialog.mode = Logic.LAUNCH_MODES[Math.max(0, Logic.LAUNCH_MODES.findIndex(m => m.id === dialog.mode) - 1)].id
                                Keys.onRightPressed: dialog.mode = Logic.LAUNCH_MODES[Math.min(2, Logic.LAUNCH_MODES.findIndex(m => m.id === dialog.mode) + 1)].id
                                ColumnLayout {
                                    id: modeColumn
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
                                            border.width: modeCard.on ? 4 : 1.5
                                            border.color: modeCard.on ? Tide.Tokens.accent : Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.35)
                                        }
                                        Text {
                                            text: modeCard.modelData.title
                                            textFormat: Text.PlainText
                                            color: Tide.Tokens.textPrimary
                                            font.family: Tide.Theme.uiFamily
                                            font.pixelSize: 15
                                            font.weight: Font.DemiBold
                                        }
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: modeCard.modelData.desc
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textSecondary
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 13
                                        lineHeight: 1.1
                                        wrapMode: Text.Wrap
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: modeCard.modelData.meta
                                        textFormat: Text.PlainText
                                        color: Tide.Tokens.textMuted
                                        font.family: Tide.Theme.uiFamily
                                        font.pixelSize: 11
                                        wrapMode: Text.Wrap
                                    }
                                }
                                MouseArea {
                                    id: modeArea
                                    anchors.fill: parent
                                    hoverEnabled: true
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: dialog.mode = modeCard.modelData.id
                                }
                            }
                        }
                    }
                    Flow {
                        objectName: "launchSteps"
                        Layout.fillWidth: true
                        Layout.topMargin: 4
                        spacing: 6
                        Repeater {
                            model: Logic.launchMode(dialog.mode).steps
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
                        readonly property bool spec: dialog.mode === "spec"
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
                                    enabled: dialog.mode !== "spec"
                                    checked: dialog.newWorktree && dialog.mode !== "spec"
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
                                        text: dialog.mode === "spec" || !dialog.newWorktree ? "bez gałęzi" : dialog.branch
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
                                    visible: dialog.mode !== "spec" && dialog.newWorktree
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
                                Layout.fillWidth: true
                                text: dialog.manualCheck ? "Po review AI agent zostawi listę „Co przetestować ręcznie i jak” i nie scali gałęzi."
                                                         : "Wystarczy review AI — agent kończy, gdy recenzent zatwierdzi."
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
    }
}

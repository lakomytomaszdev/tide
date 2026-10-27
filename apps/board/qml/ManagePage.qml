// „Projekty i tagi” (link na dole paska okna): projekty z tagami, miejscem dokumentów AI, domyślnym
// narzędziem i trybem; repozytoria z katalogu projektów, które można dodać jako projekt; tagi z kolorami
// z palety. Każdy zapis to jeden UpsertProject albo UpsertTag w usłudze (commit w repozytorium
// tablicy); błąd pokazuje się przy formularzu, który go wywołał. Esc albo „← Tablica” wraca.
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
    property string openProject: ""     // slug projektu z otwartym formularzem
    property string openTag: ""         // nazwa tagu z otwartym formularzem ("+" — nowy)
    property string projectError: ""
    property string tagError: ""
    property string repoError: ""
    property string busyTag: ""         // tag żądania w toku
    readonly property var projects: (snapshot && snapshot.projects) || []
    readonly property var tags: (snapshot && snapshot.tags) || []
    readonly property var repos: (snapshot && snapshot.repos) || []
    readonly property var pastels: Object.keys((snapshot && snapshot.colors) || {})
    signal projectRequested(string tag, var request)
    signal tagRequested(string tag, var request)
    signal backRequested()

    // Odpowiedź usługi na zapis z tej strony (tag z prefiksem project:/tag:/repo:).
    function handleReply(tag, reply) {
        if (tag === busyTag) busyTag = ""
        const error = reply && reply.ok ? "" : Logic.errorText(reply)
        if (tag.indexOf("project:") === 0) {
            projectError = error
            if (!error) openProject = ""
        } else if (tag.indexOf("tag:") === 0) {
            tagError = error
            if (!error) openTag = ""
        } else if (tag.indexOf("repo:") === 0) {
            repoError = error
        }
    }

    function send(kind, key, request) {
        busyTag = kind + ":" + key
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

                    SectionTitle { text: "PROJEKTY" }
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

        function reset() {
            tags = (project.tags || []).slice()
            aiDocs = project.aiDocs || "tide"
            tool = project.tool || "claude"
            mode = project.mode || "full"
            nameField.text = project.name || ""
            descriptionField.text = project.description || ""
        }

        function save() {
            const request = { slug: project.slug }
            const name = nameField.text.trim()
            if (name && name !== project.name) request.name = name
            if (tags.join(",") !== (project.tags || []).join(",")) request.tags = tags
            if (aiDocs !== project.aiDocs || project.aiDocsDefault) request.aiDocs = aiDocs
            if (tool !== project.tool) request.tool = tool
            if (mode !== project.mode) request.mode = mode
            if (descriptionField.text.trim() !== (project.description || "")) request.description = descriptionField.text.trim()
            page.send("project", project.slug, request)
        }

        onOpenChanged: if (open) reset()
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
                        text: (row.project.aiDocs === "repo" ? "dokumenty AI w repozytorium" : "dokumenty AI w tablicy")
                              + " · " + Logic.toolName(row.project.tool) + " · "
                              + ({ full: "pełny", light: "lekki", spec: "tylko spec" }[row.project.mode] || row.project.mode || "")
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
                FieldLabel { text: "Tryb" }
                Segmented {
                    itemHeight: 28
                    options: [{ value: "full", label: "Pełny" }, { value: "light", label: "Lekki" },
                              { value: "spec", label: "Tylko spec" }]
                    current: row.mode
                    onPicked: value => row.mode = value
                }
                FieldLabel { text: "Opis" }
                LineInput {
                    id: descriptionField
                    Layout.fillWidth: true
                    placeholder: "Krótko, czego dotyczy projekt"
                    onAccepted: row.save()
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
                        onClicked: page.openProject = ""
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

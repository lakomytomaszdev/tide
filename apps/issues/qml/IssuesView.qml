// Sprawa ma własny bufor edycji w backendzie; zmiana zaznaczenia go nie usuwa.
import QtQuick
import QtQuick.Controls as QQC2
import QtQuick.Dialogs
import QtQuick.Layouts
import "tide" as Tide
import "issues.js" as Logic

Item {
    id: view
    property var backend: null
    property bool active: true
    property var preview: ({})
    readonly property var modelState: backend ? JSON.parse(backend.stateJson) : ({})
    readonly property var issue: modelState.issue || null
    readonly property var draft: modelState.draft || ({})
    readonly property bool editable: !!issue && ["draft", "ready"].indexOf(issue.state) >= 0
    readonly property bool canAct: !!backend && !backend.preview && !!modelState.available && !modelState.busy && !modelState.conflict && !modelState.error
    readonly property var rows: Logic.rows(modelState.issues, search.text, projectFilter.currentValue || "", stateFilter.currentValue || "open")
    readonly property var railInfo: ({label: "Sprawy", value: (modelState.issues || []).length + " raportów",
        note: "Szkic → gotowa do przyjęcia. Przyjęcie będzie dostępne w kolejnym etapie."})
    property bool editing: false
    property string pendingDetach: ""
    readonly property var projectChoices: {
        const options = Logic.projectOptions(modelState.projects, "Bez projektu")
        if (draft.project && !options.some(p => p.value === draft.project))
            options.push({label: "Nieznany projekt: " + draft.project, value: draft.project})
        return options
    }

    function showItem(item) { if (backend) { backend.selectIssue(item); editing = false } }
    function takeFocus() { search.forceActiveFocus() }
    function chooseFirst() {
        if (!modelState.selected && rows.length > 0) showItem(rows[0].id)
    }
    onRowsChanged: chooseFirst()
    Component.onCompleted: chooseFirst()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 24
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            Label { text: "Sprawy"; font.pixelSize: 28; color: Tide.Tokens.textPrimary }
            Label { text: backend && backend.preview ? "Podgląd · akcje wyłączone" : "Raporty rozmów" }
            Item { Layout.fillWidth: true }
            Action { text: "Odśwież"; enabled: !!backend && !modelState.busy; onClicked: backend.reload() }
        }
        Label {
            Layout.fillWidth: true
            visible: !modelState.available
            text: "Usługa Tide jest niedostępna lub nie udostępnia spraw. Ponów odczyt przyciskiem Odśwież."
            color: Tide.Tokens.waiting
        }
        Label {
            objectName: "issueNotice"
            Layout.fillWidth: true
            visible: !!modelState.notice
            text: modelState.notice || ""
            color: Tide.Tokens.waiting
        }
        RowLayout {
            Layout.fillWidth: true
            Field {
                id: search
                objectName: "issueSearch"
                Layout.fillWidth: true
                placeholderText: "Szukaj w tytule, numerze i projekcie…"
                Accessible.name: "Szukaj sprawy"
            }
            Select {
                id: projectFilter
                objectName: "issueProjectFilter"
                Layout.preferredWidth: 220
                model: Logic.projectOptions(modelState.projects, "Wszystkie projekty")
                Accessible.name: "Filtr projektu"
            }
            Select {
                id: stateFilter
                objectName: "issueStateFilter"
                Layout.preferredWidth: 200
                model: [{label: "Szkice i gotowe", value: "open"}, {label: "Szkice", value: "draft"},
                    {label: "Gotowe", value: "ready"}, {label: "Przyjęte", value: "accepted"},
                    {label: "Odrzucone", value: "rejected"}, {label: "Archiwalne", value: "archived"},
                    {label: "Wszystkie stany", value: "all"}]
                Accessible.name: "Filtr stanu"
            }
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 24
            ColumnLayout {
                Layout.preferredWidth: 310
                Layout.fillHeight: true
                ListView {
                    id: list
                    objectName: "issueList"
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    clip: true
                    spacing: 8
                    model: view.rows
                    delegate: Rectangle {
                        required property var modelData
                        width: list.width
                        height: rowContent.implicitHeight + 24
                        radius: Tide.Tokens.radiusFrame
                        color: Tide.Tokens.backgroundCard
                        border.width: 1
                        border.color: view.modelState.selected === modelData.id ? Tide.Tokens.accent : "transparent"
                        activeFocusOnTab: true
                        Accessible.role: Accessible.Button
                        Accessible.name: modelData.id + " " + modelData.title
                        Keys.onReturnPressed: view.showItem(modelData.id)
                        ColumnLayout {
                            id: rowContent
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: 12
                            Label { text: modelData.id + " · " + Logic.stateLabel(modelData.state); color: Tide.Tokens.sky }
                            Label { Layout.fillWidth: true; text: modelData.title; font.pixelSize: 15; color: Tide.Tokens.textPrimary }
                            Label { text: modelData.project || "Bez projektu"; color: Tide.Tokens.textMuted }
                        }
                        MouseArea { anchors.fill: parent; onClicked: view.showItem(modelData.id) }
                    }
                    QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                }
                Label { visible: view.rows.length === 0; text: "Brak spraw dla wybranych filtrów." }
                Repeater {
                    model: modelState.listErrors || []
                    Label {
                        required property var modelData
                        Layout.fillWidth: true
                        text: modelData.id + ": " + (modelData.message || "Uszkodzona sprawa")
                        color: Tide.Tokens.waiting
                    }
                }
            }
            QQC2.ScrollView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                contentWidth: availableWidth
                ColumnLayout {
                    width: parent.width
                    spacing: 14
                    Label {
                        objectName: "issueError"
                        Layout.fillWidth: true
                        visible: !!modelState.error
                        text: modelState.error || ""
                        color: Tide.Tokens.waiting
                    }
                    Label { visible: !view.issue && !modelState.error; text: "Wybierz sprawę z listy." }
                    ColumnLayout {
                        Layout.fillWidth: true
                        visible: !!view.issue
                        spacing: 12
                        Label {
                            objectName: "issueMetadata"
                            Layout.fillWidth: true
                            text: view.issue ? view.issue.id + " · " + Logic.stateLabel(view.issue.state) + " · wersja " + view.issue.version : ""
                            color: Tide.Tokens.sky
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: !view.editing
                            text: draft.title || ""
                            font.pixelSize: 24
                            color: Tide.Tokens.textPrimary
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: !view.editing
                            text: "Projekt: " + (draft.project || "bez przypisania")
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: !!draft.project && !(modelState.projects || []).some(p => p.slug === draft.project)
                            text: "Nieznany lub usunięty projekt. Wybierz istniejący projekt albo wyczyść przypisanie przed submit."
                            color: Tide.Tokens.waiting
                        }
                        RowLayout {
                            Action { text: view.editing ? "Pokaż raport" : "Edytuj szkic"; visible: view.editable; onClicked: view.editing = !view.editing }
                            Label { text: modelState.dirty ? "Lokalne zmiany · zachowane do zamknięcia okna" : "Zapisana wersja" }
                        }
                        Label {
                            Layout.fillWidth: true
                            visible: !!view.issue && view.issue.state === "ready" && view.editable
                            text: "Zmiana tytułu, raportu, projektu lub dowodów przywróci szkic. Potem przygotuj sprawę ponownie."
                            color: Tide.Tokens.waiting
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: view.editing && view.editable
                            Label { text: "Tytuł" }
                            Field {
                                objectName: "issueTitle"
                                Layout.fillWidth: true
                                text: draft.title || ""
                                enabled: !modelState.busy
                                maximumLength: 200
                                onTextEdited: if (backend) backend.updateDraft("title", text)
                            }
                            Label { text: "Projekt" }
                            Select {
                                objectName: "issueProject"
                                Layout.fillWidth: true
                                model: view.projectChoices
                                currentIndex: Math.max(0, view.projectChoices.findIndex(p => p.value === (draft.project || "")))
                                enabled: !modelState.busy
                                onActivated: if (backend) backend.updateDraft("project", currentValue)
                            }
                            Label { text: "Raport Markdown (bez frontmatteru)" }
                            QQC2.TextArea {
                                objectName: "issueReportEditor"
                                Layout.fillWidth: true
                                Layout.minimumHeight: 280
                                text: draft.report || ""
                                enabled: !modelState.busy
                                textFormat: TextEdit.PlainText
                                wrapMode: TextEdit.Wrap
                                selectByMouse: true
                                color: Tide.Tokens.textPrimary
                                selectionColor: Tide.Tokens.accent
                                selectedTextColor: Tide.Tokens.backgroundDesktop
                                font.family: Tide.Theme.monoFamily
                                font.pixelSize: 13
                                padding: 12
                                background: Rectangle { color: Tide.Tokens.backgroundCard; radius: Tide.Tokens.radiusFrame }
                                onTextChanged: if (activeFocus && backend) backend.updateDraft("report", text)
                            }
                        }
                        TextEdit {
                            objectName: "issueReport"
                            Layout.fillWidth: true
                            visible: !view.editing
                            // changed obejmuje także asynchroniczne zakończenie odczytu obrazów.
                            text: { view.modelState; return backend ? backend.renderReport(draft.report || "", Tide.Tokens.sky) : "" }
                            textFormat: TextEdit.RichText
                            readOnly: true
                            selectByMouse: true
                            wrapMode: TextEdit.Wrap
                            color: Tide.Tokens.textSecondary
                            selectionColor: Tide.Tokens.accent
                            selectedTextColor: Tide.Tokens.backgroundDesktop
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 15
                            onLinkActivated: link => { if (backend && /^https?:\/\/[^\s<>]+$/i.test(link)) backend.openLink(link) }
                            QQC2.ToolTip.visible: hoveredLink !== ""
                            QQC2.ToolTip.text: hoveredLink
                        }
                        ColumnLayout {
                            Layout.fillWidth: true
                            visible: !!modelState.conflict
                            Label {
                                objectName: "issueConflict"
                                Layout.fillWidth: true
                                color: Tide.Tokens.waiting
                                text: "Konflikt · aktualna wersja " + modelState.conflict + ". Lokalny tekst zachowano. Porównaj go przed ponownym wczytaniem."
                            }
                            Label { text: modelState.current ? "Aktualny tytuł: " + modelState.current.title + " · projekt: " + (modelState.current.project || "bez przypisania") : "Pobieranie aktualnej wersji…" }
                            QQC2.TextArea {
                                objectName: "issueCurrentReport"
                                Layout.fillWidth: true
                                text: modelState.current ? modelState.current.report : ""
                                textFormat: TextEdit.PlainText
                                readOnly: true
                                selectByMouse: true
                                wrapMode: TextEdit.Wrap
                                color: Tide.Tokens.textSecondary
                                selectionColor: Tide.Tokens.accent
                                selectedTextColor: Tide.Tokens.backgroundDesktop
                                font.family: Tide.Theme.uiFamily
                                background: Rectangle { color: Tide.Tokens.backgroundCard }
                            }
                        }
                        Flow {
                            Layout.fillWidth: true
                            spacing: 8
                            Action {
                                objectName: "issueSave"
                                text: "Zapisz zmiany"
                                primary: true
                                visible: view.editable
                                enabled: view.canAct && !!modelState.dirty
                                onClicked: backend.save()
                            }
                            Action {
                                objectName: "issueReload"
                                text: "Wczytaj ponownie"
                                enabled: !!backend && !backend.preview && !modelState.busy
                                onClicked: discardDialog.open()
                            }
                            Action {
                                objectName: "issueSubmit"
                                text: "Przygotuj do przyjęcia"
                                visible: !!view.issue && view.issue.state === "draft"
                                enabled: view.canAct && !modelState.dirty
                                onClicked: backend.transition("Submit")
                            }
                            Action {
                                objectName: "issueReject"
                                text: "Odrzuć"
                                visible: view.editable
                                enabled: view.canAct && !modelState.dirty
                                onClicked: rejectDialog.open()
                            }
                            Action {
                                objectName: "issueReopen"
                                text: "Otwórz jako szkic"
                                visible: !!view.issue && view.issue.state === "rejected"
                                enabled: view.canAct && !modelState.dirty
                                onClicked: backend.transition("Reopen")
                            }
                        }
                        Label { text: "Dowody"; font.pixelSize: 18; color: Tide.Tokens.textPrimary }
                        Label {
                            Layout.fillWidth: true
                            text: "Braki opisane w raporcie pozostają częścią jego treści. Sprawdzamy spójność istniejącego manifestu; nie wyprowadzamy listy wymaganych plików z prozy."
                        }
                        Label {
                            objectName: "issueEmptyManifest"
                            visible: !!view.issue && view.issue.attachments.length === 0
                            text: "Manifest jest pusty i poprawny. Brak dołączonych dowodów."
                        }
                        Repeater {
                            model: view.issue ? view.issue.attachments : []
                            ColumnLayout {
                                required property var modelData
                                Layout.fillWidth: true
                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.name + " · " + modelData.type + " · " + modelData.size + " B"
                                }
                                Label {
                                    Layout.fillWidth: true
                                    text: modelData.id + " · " + ((modelState.evidence || {})[modelData.id] || "Oczekuje na sprawdzenie")
                                    color: Tide.Tokens.textMuted
                                }
                                RowLayout {
                                    Action {
                                        text: ["image/png", "image/jpeg", "image/webp"].indexOf(modelData.type) >= 0 ? "Otwórz obraz" : "Otwórz jako tekst"
                                        enabled: view.canAct
                                        onClicked: backend.openAttachment(modelData.id)
                                    }
                                    Action {
                                        text: "Odłącz"
                                        visible: view.editable
                                        enabled: view.canAct && !modelState.dirty
                                        onClicked: { view.pendingDetach = modelData.id; detachDialog.open() }
                                    }
                                }
                            }
                        }
                        Action {
                            objectName: "issueAttach"
                            text: "Dołącz plik…"
                            visible: view.editable
                            enabled: view.canAct && !modelState.dirty
                            onClicked: attachmentDialog.open()
                        }
                    }
                }
            }
        }
    }
    FileDialog {
        id: attachmentDialog
        title: "Dołącz trwały dowód sprawy"
        fileMode: FileDialog.OpenFile
        onAccepted: if (view.backend && !view.backend.preview) view.backend.attach(selectedFile)
    }
    Confirm {
        id: discardDialog
        heading: "Wczytać zapisaną wersję?"
        message: "Lokalne zmiany tej sprawy zostaną zastąpione aktualną wersją z usługi."
        onAccepted: if (view.backend) view.backend.reloadSelected()
    }
    Confirm {
        id: rejectDialog
        heading: "Odrzucić sprawę?"
        message: "Raport i dowody pozostaną do odczytu. Możesz później otworzyć sprawę jako szkic."
        onAccepted: if (view.backend) view.backend.transition("Reject")
    }
    Confirm {
        id: detachDialog
        heading: "Odłączyć dowód?"
        message: "Dowód zniknie z bieżącego manifestu. Sprawa wróci do szkicu."
        onAccepted: if (view.backend) view.backend.detach(view.pendingDetach)
    }
}

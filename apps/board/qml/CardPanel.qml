// Panel karty po prawej stronie tablicy: nowa karta („＋ Karta”, „＋ Dodaj kartę” w kolumnie, Ctrl+N)
// albo szczegóły i edycja istniejącej (kliknięcie karty). Pola: tytuł, projekt (projekty z tablicy
// i repozytoria — wybranie repozytorium zakłada projekt), tagi (tagi projektu są dziedziczone i stałe),
// opis, notatka. Istniejąca karta ma też przeniesienie między kolumnami ręcznymi i archiwizację.
// Błąd z usługi pokazuje się w panelu (komunikat po polsku), commitError — dyskretnie pod nim.
// Ctrl+Enter zapisuje, Esc zamyka. Teksty z karty to dane: zawsze zwykły tekst.
// Plan i specyfikacja karty (PT-5): zakładki „Opis” | „Plan” | „Specyfikacja” — dwie ostatnie tylko,
// gdy /Board CardDocs(id) je znalazł. Wybrana zakładka zostaje dla kolejnych kart, póki działa okno
// (karta bez takiego dokumentu pokazuje „Opis”). Przy dokumencie panel się poszerza (do 860 px, „Rozwiń” — prawie
// całe okno). Dokumenty odświeżamy przy każdej nowej migawce (sygnał Changed) i co 5 s, póki panel
// jest otwarty — usługa czyta plik ponownie tylko po zmianie, a widok podmienia treść tylko wtedy,
// gdy odpowiedź jest inna (przewinięcie zostaje).
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "board.js" as Logic
import "cardDocs.js" as Docs

Rectangle {
    id: panel

    property var snapshot: null
    property var card: null             // null — nowa karta
    property string column: "backlog"   // kolumna nowej karty
    property string homeDir: ""
    property real nowTs: Date.now() / 1000
    property string error: ""
    property string notice: ""
    property bool busy: false
    property var tagTone: null
    // Formularz.
    property string projectKind: ""
    property string project: ""
    property string projectLabel: ""
    property var tags: []
    // Karta z chwili otwarcia panelu: z nią porównujemy formularz. `card` śledzi żywą migawkę, więc
    // porównanie z nią wysłałoby stare pola i cofnęło zmianę zrobioną w tym czasie z zewnątrz.
    property var base: null
    // Plan i specyfikacja karty (CardDocs): odpowiedź usługi, wybrana zakładka i szerokość.
    property var docs: null
    property string docsText: ""
    property string docsPending: ""          // ID karty, na której CardDocs czekamy ("" — nic w drodze)
    property string tab: "opis"              // ostatni wybór użytkownika (opis | plan | spec) — także dla następnej karty
    property bool expanded: false
    property real maxWidth: 1200             // szerokość dostępna w oknie (ustawia BoardPage)
    property var renderer: null              // backend.renderMarkdown (przez BoardPage) albo null
    readonly property bool editing: card !== null
    readonly property string shownTab: editing ? Docs.effectiveTab(docs, tab) : "opis"
    readonly property bool reading: shownTab !== "opis"
    readonly property var docTabs: Docs.tabs(docs)
    readonly property alias docView: docView
    readonly property var inherited: projectKind === "project" ? Logic.inheritedTags(snapshot, project) : []
    readonly property var allTags: ((snapshot && snapshot.tags) || []).map(t => t.name)
    readonly property bool dirty: editing && base ? Object.keys(Logic.updateRequest(base, form())).length > 0
                                          : titleField.text.trim() !== ""
    signal saveRequested(var request)               // CreateCard albo UpdateCard (pola zmienione)
    signal moveRequested(string column)
    signal closeRequested()
    signal docsRequested(string cardId)            // /Board CardDocs
    signal openFileRequested(string path)          // „Otwórz plik”
    signal showInFolderRequested(string path)      // „Pokaż w katalogu”
    signal linkRequested(string url)               // link http(s) z Markdownu

    function form() {
        return { title: titleField.text, description: descriptionEdit.text, note: noteField.text, tags: tags,
                 project: project, projectKind: projectKind, column: column }
    }

    // Wypełnia formularz kartą (albo pustym, dla nowej) — przy otwarciu panelu.
    function load() {
        error = ""
        notice = ""
        busy = false
        base = card ? Object.assign({}, card, {tags: (card.tags || []).slice()}) : null
        titleField.text = card ? card.title : ""
        descriptionEdit.text = card ? card.description || "" : ""
        noteField.text = card ? card.note || "" : ""
        tags = card ? (card.tags || []).slice() : []
        projectKind = card && card.project ? "project" : ""
        project = card ? card.project || "" : ""
        projectLabel = card ? card.projectName || card.project || "" : ""
        docs = null
        docsText = ""
        docsPending = ""
        requestDocs()   // `tab` zostaje: ostatnio wybrana zakładka, póki działa okno Tide
        titleField.focusInput()
    }

    // Bez kolejki: póki odpowiedź dla tej karty nie wróciła, kolejne odświeżenie (5 s, Changed) czeka.
    function requestDocs() {
        if (!editing || !card || docsPending === card.id) return
        docsPending = card.id
        docsRequested(card.id)
    }

    // Odpowiedź CardDocs: tylko dla karty w panelu i tylko inna niż poprzednia (bez skoku przewinięcia).
    function applyDocs(cardId, text) {
        if (cardId === docsPending) docsPending = ""
        if (!editing || !card || cardId !== card.id || text === docsText) return
        docsText = text
        const value = Logic.parse(text, null)
        docs = value && value.ok ? value : null
    }

    function save() {
        if (busy) return
        const values = form()
        if (values.title.trim() === "") {
            error = "Karta potrzebuje tytułu."
            titleField.focusInput()
            return
        }
        const request = editing ? Logic.updateRequest(base || card, values) : Logic.createRequest(values)
        if (editing && Object.keys(request).length === 0) {
            closeRequested()
            return
        }
        error = ""
        busy = true
        saveRequested(request)
    }

    function addTag(name) {
        const value = String(name || "").trim().replace(/,/g, "")
        if (!value || Logic.hasTag(tags, value) || Logic.hasTag(inherited, value)) return
        tags = tags.concat([value])
    }

    width: reading ? (expanded ? maxWidth : Math.min(maxWidth, 860)) : 420
    Behavior on width { NumberAnimation { duration: 140; easing.type: Easing.OutCubic } }
    radius: 16
    color: Tide.Tokens.backgroundPanel
    border.width: 1
    border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.09)
    Accessible.role: Accessible.Dialog
    Accessible.name: editing ? "Karta " + card.id : "Nowa karta"

    Keys.onPressed: event => {
        if ((event.key === Qt.Key_Return || event.key === Qt.Key_Enter) && (event.modifiers & Qt.ControlModifier)) {
            panel.save()
            event.accepted = true
        } else if (event.key === Qt.Key_Escape) {
            panel.closeRequested()
            event.accepted = true
        }
    }

    // Klik w panel nie przechodzi do tablicy pod spodem.
    MouseArea { anchors.fill: parent; acceptedButtons: Qt.AllButtons; onWheel: wheel => wheel.accepted = true }

    // Plan i specyfikacja na żywo: po nowej migawce (Changed — także plik w docs/ tablicy) i co 5 s
    // (plan w repozytorium projektu nie zmienia migawki).
    onSnapshotChanged: if (visible && editing) docsSoon.restart()
    Timer { id: docsSoon; interval: 300; onTriggered: panel.requestDocs() }
    Timer {
        interval: 5000
        repeat: true
        running: panel.visible && panel.editing
        onTriggered: panel.requestDocs()
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: 18
        spacing: 14

        // Nagłówek.
        RowLayout {
            Layout.fillWidth: true
            spacing: 10
            Text {
                visible: panel.editing
                text: panel.editing ? panel.card.id : ""
                textFormat: Text.PlainText
                color: Tide.Tokens.sky
                font.family: Tide.Theme.monoFamily
                font.pixelSize: 14
                font.weight: Font.DemiBold
            }
            Text {
                Layout.fillWidth: true
                text: panel.editing ? Logic.columnLabel(panel.snapshot, Logic.displayColumn(panel.card))
                                    : "Nowa karta · " + Logic.columnLabel(panel.snapshot, panel.column)
                textFormat: Text.PlainText
                color: panel.editing ? Tide.Tokens.textMuted : Tide.Tokens.textPrimary
                font.family: Tide.Theme.uiFamily
                font.pixelSize: panel.editing ? 13 : 17
                font.weight: panel.editing ? Font.Normal : Font.DemiBold
                elide: Text.ElideRight
                Accessible.role: Accessible.Heading
            }
            PillButton {
                objectName: "cardExpand"
                visible: panel.reading
                implicitHeight: 30
                pixelSize: 12
                flat: true
                text: panel.expanded ? "Zwęź" : "Rozwiń"
                toolTip: panel.expanded ? "Panel o zwykłej szerokości" : "Panel na prawie całe okno"
                onClicked: panel.expanded = !panel.expanded
            }
            PillButton {
                implicitWidth: 30
                implicitHeight: 30
                flat: true
                text: "✕"
                toolTip: "Zamknij (Esc)"
                onClicked: panel.closeRequested()
            }
        }

        // Zakładki: Opis zawsze, Plan i Specyfikacja — gdy są.
        Segmented {
            objectName: "cardTabs"
            visible: panel.editing && panel.docTabs.length > 1
            itemHeight: 28
            options: panel.docTabs
            current: panel.shownTab
            onPicked: value => panel.tab = value
        }
        Text {
            objectName: "cardDocsWarnings"
            Layout.fillWidth: true
            visible: panel.reading && !!panel.docs && (panel.docs.warnings || []).length > 0
            text: panel.docs && panel.docs.warnings ? panel.docs.warnings.join(" · ") : ""
            textFormat: Text.PlainText
            color: Tide.Tokens.butter
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
            wrapMode: Text.Wrap
        }

        CardDocView {
            id: docView
            objectName: "cardDocView"
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: panel.reading
            kind: panel.shownTab === "spec" ? "spec" : "plan"
            doc: panel.docs ? (panel.shownTab === "spec" ? panel.docs.spec : panel.docs.plan) : null
            others: panel.docs ? (panel.docs.others || []).filter(o => o.kind === kind) : []
            homeDir: panel.homeDir
            nowTs: panel.nowTs
            renderer: panel.renderer
            onOpenFile: path => panel.openFileRequested(path)
            onShowInFolder: path => panel.showInFolderRequested(path)
            onOpenLink: url => panel.linkRequested(url)
        }

        Flickable {
            id: formFlick
            visible: !panel.reading
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: formColumn.implicitHeight
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

            ColumnLayout {
                id: formColumn
                width: formFlick.width
                spacing: 6

                FieldLabel { text: "Tytuł" }
                LineInput {
                    id: titleField
                    objectName: "cardTitle"
                    Layout.fillWidth: true
                    placeholder: "Co jest do zrobienia?"
                    accessibleName: "Tytuł karty"
                    maximumLength: 200
                    pixelSize: 14
                    invalid: panel.error !== "" && text.trim() === ""
                    onAccepted: panel.save()
                }

                FieldLabel { text: "Projekt"; Layout.topMargin: 8 }
                ProjectPicker {
                    id: picker
                    objectName: "cardProject"
                    Layout.fillWidth: true
                    snapshot: panel.snapshot
                    kind: panel.projectKind
                    key: panel.project
                    label: panel.projectLabel
                    onChosen: (kind, key, label) => {
                        panel.projectKind = kind
                        panel.project = key
                        panel.projectLabel = label
                    }
                }

                FieldLabel { text: "Tagi"; Layout.topMargin: 8 }
                Flow {
                    objectName: "cardTags"
                    Layout.fillWidth: true
                    spacing: 6
                    Repeater {
                        model: panel.inherited
                        TagChip {
                            required property var modelData
                            name: modelData
                            dotColor: panel.tagTone ? panel.tagTone(modelData) : Tide.Tokens.textMuted
                            selectable: true
                            selected: true
                            locked: true
                            suffix: "z projektu"
                        }
                    }
                    Repeater {
                        // Wszystkie tagi tablicy (bez dziedziczonych) i nowe, dopisane w tej karcie.
                        model: panel.allTags.concat(panel.tags.filter(t => !Logic.hasTag(panel.allTags, t)))
                                            .filter(t => !Logic.hasTag(panel.inherited, t))
                        TagChip {
                            required property var modelData
                            name: modelData
                            dotColor: panel.tagTone ? panel.tagTone(modelData) : Tide.Tokens.textMuted
                            selectable: true
                            selected: Logic.hasTag(panel.tags, modelData)
                            onClicked: panel.tags = Logic.toggleTag(panel.tags, modelData)
                        }
                    }
                    Field {
                        width: 120
                        height: 22
                        radius: 11
                        focused: newTag.activeFocus
                        TextInput {
                            id: newTag
                            objectName: "cardNewTag"
                            anchors.fill: parent
                            anchors.leftMargin: 9
                            anchors.rightMargin: 9
                            verticalAlignment: TextInput.AlignVCenter
                            color: Tide.Tokens.textPrimary
                            selectionColor: Tide.Tokens.accent
                            selectedTextColor: Tide.Tokens.backgroundDesktop
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 11
                            maximumLength: 40
                            clip: true
                            onAccepted: {
                                panel.addTag(text)
                                text = ""
                            }
                            Accessible.role: Accessible.EditableText
                            Accessible.name: "Nowy tag karty"
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: newTag.text === ""
                                text: "＋ nowy tag"
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font: newTag.font
                            }
                        }
                    }
                }

                FieldLabel { text: "Opis"; Layout.topMargin: 8 }
                Field {
                    Layout.fillWidth: true
                    implicitHeight: 150
                    focused: descriptionEdit.activeFocus
                    Flickable {
                        id: descriptionFlick
                        anchors.fill: parent
                        anchors.margins: 10
                        clip: true
                        contentWidth: width
                        contentHeight: descriptionEdit.implicitHeight
                        boundsBehavior: Flickable.StopAtBounds
                        QQC2.ScrollBar.vertical: QQC2.ScrollBar {}
                        TextEdit {
                            id: descriptionEdit
                            objectName: "cardDescription"
                            width: descriptionFlick.width
                            wrapMode: TextEdit.Wrap
                            textFormat: TextEdit.PlainText
                            color: Tide.Tokens.textPrimary
                            selectionColor: Tide.Tokens.accent
                            selectedTextColor: Tide.Tokens.backgroundDesktop
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 13
                            activeFocusOnTab: true
                            Keys.onTabPressed: event => { noteField.focusInput(); event.accepted = true }
                            onCursorRectangleChanged: {
                                if (cursorRectangle.y < descriptionFlick.contentY) descriptionFlick.contentY = cursorRectangle.y
                                else if (cursorRectangle.y + cursorRectangle.height > descriptionFlick.contentY + descriptionFlick.height)
                                    descriptionFlick.contentY = cursorRectangle.y + cursorRectangle.height - descriptionFlick.height
                            }
                            Accessible.role: Accessible.EditableText
                            Accessible.name: "Opis karty"
                            Text {
                                visible: descriptionEdit.text === ""
                                text: "Szczegóły, linki, kryteria — Markdown jak w pliku karty"
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font: descriptionEdit.font
                                width: parent.width
                                wrapMode: Text.Wrap
                            }
                        }
                    }
                }

                FieldLabel { text: "Notatka na karcie"; Layout.topMargin: 8 }
                LineInput {
                    id: noteField
                    objectName: "cardNote"
                    Layout.fillWidth: true
                    placeholder: "np. bez agenta, robię sam"
                    accessibleName: "Notatka na karcie"
                    maximumLength: 300
                    onAccepted: panel.save()
                }

                // Istniejąca karta: kolumna, archiwum, metadane.
                ColumnLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: 14
                    visible: panel.editing
                    spacing: 8

                    FieldLabel { text: "Kolumna" }
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 8
                        Segmented {
                            objectName: "cardColumn"
                            itemHeight: 28
                            options: [{ value: "backlog", label: "Backlog" }, { value: "todo", label: "Do zrobienia" },
                                      { value: "done", label: "Gotowe" }]
                            // Kolumna wyświetlana: karta „Do sprawdzenia” ma zwykle zapisane todo — „Do zrobienia”
                            // to wtedy odesłanie do poprawy, a nie brak zmiany.
                            current: panel.editing ? Logic.displayColumn(panel.card) : ""
                            onPicked: value => { if (panel.editing && value !== Logic.displayColumn(panel.card)) panel.moveRequested(value) }
                        }
                        Item { Layout.fillWidth: true }
                        PillButton {
                            objectName: "cardArchive"
                            implicitHeight: 30
                            pixelSize: 12
                            outline: true
                            text: "Archiwizuj"
                            textColor: Tide.Tokens.rose
                            toolTip: "Zdejmij kartę z tablicy (plik i historia zostają)"
                            onClicked: panel.moveRequested("archived")
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: panel.editing && panel.card.derived !== null && panel.card.derived !== undefined
                        text: "Kolumnę wyświetlaną ustawia teraz koordynator karty — ręczna zmiana dotyczy pliku."
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                        wrapMode: Text.Wrap
                    }
                    Text {
                        Layout.fillWidth: true
                        Layout.topMargin: 6
                        text: panel.editing ? "utworzona " + Logic.when(panel.card.createdTs, panel.nowTs)
                                              + (panel.card.updatedTs && panel.card.updatedTs !== panel.card.createdTs
                                                 ? " · zmieniona " + Logic.when(panel.card.updatedTs, panel.nowTs) : "")
                                            : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 11
                    }
                    Text {
                        Layout.fillWidth: true
                        text: panel.editing ? Logic.shortPath(panel.card.path, panel.homeDir) : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.monoFamily
                        font.pixelSize: 11
                        elide: Text.ElideMiddle
                    }
                }
            }
        }

        // Błąd z usługi i dopisek o commicie.
        Rectangle {
            objectName: "cardError"
            Layout.fillWidth: true
            visible: panel.error !== ""
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
                text: panel.error
                textFormat: Text.PlainText
                color: Tide.Tokens.rose
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
                wrapMode: Text.Wrap
                verticalAlignment: Text.AlignVCenter
            }
        }
        Text {
            objectName: "cardNotice"
            Layout.fillWidth: true
            visible: panel.notice !== ""
            text: panel.notice
            textFormat: Text.PlainText
            color: Tide.Tokens.butter
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 11
            wrapMode: Text.Wrap
        }

        RowLayout {
            Layout.fillWidth: true
            visible: !panel.reading
            spacing: 8
            Text {
                Layout.fillWidth: true
                text: "Ctrl+Enter zapisuje"
                textFormat: Text.PlainText
                color: Tide.Theme.alpha(Tide.Tokens.textMuted, 0.8)
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 11
            }
            PillButton {
                implicitHeight: 34
                flat: true
                text: "Anuluj"
                onClicked: panel.closeRequested()
            }
            PillButton {
                objectName: "cardSave"
                implicitHeight: 34
                strong: true
                enabled: !panel.busy && (panel.dirty || !panel.editing)
                text: panel.busy ? "Zapisuję…" : panel.editing ? "Zapisz" : "Dodaj kartę"
                fill: Tide.Tokens.accent
                textColor: Tide.Tokens.backgroundDesktop
                onClicked: panel.save()
            }
        }
    }
}

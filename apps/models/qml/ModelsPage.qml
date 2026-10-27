// Zawartość widoku „Modele”: nagłówek z używanym serwerem, to, co wymaga uwagi, po lewej „Serwery modeli”
// (karty z przełącznikiem „Używaj tego serwera”, „Dodaj serwer”) i „Modele na serwerze”, po prawej „Do czego
// używamy modeli” (tryb raportu i role). Sam nie rozmawia z usługą — dostaje Status() i zgłasza zapisy
// sygnałem requested(metoda, argument, tag); wyniki wracają przez finish(tag, metoda, wynik). Tag mówi,
// przy której kontrolce pokazać wynik (notes[tag]) i co ma kręcić się w trakcie (busy[tag]). Dzięki temu
// da się go załadować w teście z przykładowymi danymi.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "models.js" as Logic

Rectangle {
    id: root

    property var status: null
    property bool serviceUp: true
    property bool preview: false

    signal requested(string member, string argument, string tag)
    signal refreshRequested()

    // Stan widoku.
    property var notes: ({})                 // tag → {text, error}
    property var busy: ({})                  // tag → true
    property bool adding: false
    property string editing: ""              // nazwa serwera w edycji
    property string removing: ""             // nazwa serwera z potwierdzeniem usunięcia
    property string flashServer: ""
    property string chosenServer: ""         // „Modele na serwerze” — wybrany ręcznie
    property var advanced: ({})              // rola → true (otwarte „Zaawansowane”)

    readonly property var problem: Logic.problem(status, serviceUp)
    readonly property bool ready: problem === null
    readonly property var serverList: ready ? Logic.servers(status) : []
    readonly property var roles: ready ? Logic.roleRows(status) : []
    readonly property var issues: ready && status.problems ? status.problems : []
    readonly property var warnings: ready && status.config ? (status.config.warnings || []) : []
    readonly property string reportMode: ready && status.report ? status.report.summary : ""
    readonly property string modelsServer: serverList.some(s => s.name === chosenServer) ? chosenServer
                                           : status && status.default ? status.default : ""
    readonly property int columns: Math.max(1, Math.min(3, Math.floor((serversColumn.width + 14) / (330 + 14))))
    readonly property alias flick: flick

    function setIn(map, key, value) {
        const next = Object.assign({}, map)
        if (value === undefined || value === null || value === "" || value === false) delete next[key]
        else next[key] = value
        return next
    }

    function setNote(tag, note) {
        notes = setIn(notes, tag, note)
    }

    // Zapis albo próba: metoda /Models z jednym argumentem; wynik przyjdzie do finish(tag, …).
    function send(member, argument, tag) {
        if (busy[tag]) return
        busy = setIn(busy, tag, true)
        notes = setIn(notes, tag, null)
        requested(member, argument, tag)
    }

    function finish(tag, member, result) {
        busy = setIn(busy, tag, null)
        const ok = !!(result && result.ok)
        let note = null
        if (!ok) note = { text: result && result.error ? result.error : "usługa nie odpowiedziała", error: true }
        else if (member === "TestEndpoint") note = Logic.testText(result)
        else if (member === "PullModel") note = { text: "Pobieranie ruszyło — postęp jest na liście modeli serwera.", error: false }
        else if (tag.endsWith(":ctx")) note = { text: "Zapisano", error: false }
        notes = setIn(notes, tag, note)
        if (!ok) return
        if (tag === "add:save") {
            adding = false
            notes = setIn(setIn(notes, "add:test", null), "add:save", null)
        } else if (tag.endsWith(":save") && tag.indexOf("server:") === 0) {
            editing = ""
            notes = setIn(notes, tag.replace(/:save$/, ":edit-test"), null)
        } else if (tag.endsWith(":remove")) {
            removing = ""
        }
    }

    function openAdd() {
        adding = true
        notes = setIn(setIn(notes, "add:test", null), "add:save", null)
        addForm.load(null)
        Qt.callLater(() => { addForm.focusFirst(); scrollTo(addCard) })
    }

    // Podgląd: formularz z wpisanymi polami.
    function addFormFill(name, url) {
        addForm.nameText = name
        addForm.urlText = url
    }

    // Podgląd: adres wpisany w edycji serwera (po załadowaniu formularza).
    function editFill(url) {
        Qt.callLater(() => {
            for (let i = 0; i < cards.count; i++) {
                const card = cards.itemAt(i)
                if (card && card.editing) card.fillUrl(url)
            }
        })
    }

    function edit(name) {
        removing = ""
        editing = name
    }

    function askRemove(name) {
        editing = ""
        notes = setIn(notes, "server:" + name + ":remove", null)
        removing = name
    }

    // Serwer po nazwie (showItem): przewija do karty, podświetla ją i wybiera jego modele. false — nie ma.
    function reveal(name) {
        if (!serverList.some(s => s.name === name)) return false
        flashServer = name
        chosenServer = name
        flashTimer.restart()
        Qt.callLater(() => {
            for (let i = 0; i < cards.count; i++) {
                const card = cards.itemAt(i)
                if (card && card.server && card.server.name === name) scrollTo(card)
            }
        })
        return true
    }

    function scrollTo(item) {
        if (!item) return
        const y = item.mapToItem(content, 0, 0).y
        flick.contentY = Math.max(0, Math.min(y - 24, flick.contentHeight - flick.height))
    }

    color: Tide.Tokens.backgroundWindow
    focus: true
    Keys.onEscapePressed: {
        if (removing !== "") removing = ""
        else if (editing !== "") editing = ""
        else if (adding) adding = false
    }

    Timer {
        id: flashTimer
        interval: 2600
        onTriggered: root.flashServer = ""
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 24
        anchors.rightMargin: 24
        anchors.topMargin: 20
        anchors.bottomMargin: 20
        spacing: 14

        // --- nagłówek ------------------------------------------------------------------------
        RowLayout {
            Layout.fillWidth: true
            spacing: 14
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 4
                Text {
                    text: "Modele"
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textPrimary
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 21
                    font.weight: Font.DemiBold
                    Accessible.role: Accessible.Heading
                }
                Text {
                    objectName: "summary"
                    Layout.fillWidth: true
                    text: root.ready ? Logic.summaryText(root.status) : root.problem.title
                    textFormat: Text.PlainText
                    color: Tide.Tokens.textMuted
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                    elide: Text.ElideRight
                }
            }
            PillButton {
                objectName: "refreshButton"
                visible: root.ready
                text: "Sprawdź serwery teraz"
                toolTip: "Odczyt stanu wszystkich serwerów bez czekania (co 15 s robi to usługa)"
                onClicked: root.refreshRequested()
            }
        }

        // --- co wymaga uwagi -----------------------------------------------------------------
        Rectangle {
            objectName: "issues"
            Layout.fillWidth: true
            visible: root.ready && (root.issues.length > 0 || root.warnings.length > 0)
            implicitHeight: issuesColumn.implicitHeight + 24
            radius: Tide.Tokens.radiusTile
            color: Tide.Theme.tinted(Tide.Tokens.backgroundWindow, root.issues.length > 0 ? Tide.Tokens.waiting : Tide.Tokens.neutral, 0.08)
            border.width: 1
            border.color: Tide.Theme.alpha(root.issues.length > 0 ? Tide.Tokens.waiting : Tide.Tokens.neutral, 0.3)
            ColumnLayout {
                id: issuesColumn
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                anchors.leftMargin: 16
                anchors.rightMargin: 16
                spacing: 4
                Repeater {
                    model: root.issues
                    Text {
                        required property var modelData
                        objectName: "issue"
                        Layout.fillWidth: true
                        text: "● " + modelData.text
                        textFormat: Text.PlainText
                        color: Tide.Tokens.waiting
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                    }
                }
                Repeater {
                    model: root.warnings
                    Text {
                        required property var modelData
                        objectName: "configWarning"
                        Layout.fillWidth: true
                        text: "⚠ models.json: " + modelData
                        textFormat: Text.PlainText
                        color: Tide.Tokens.neutral
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 12
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }

        // --- treść ---------------------------------------------------------------------------
        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: root.ready
            clip: true
            contentWidth: width
            contentHeight: content.implicitHeight + 8
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: QQC2.ScrollBar {}

            RowLayout {
                id: content
                width: flick.width - 12
                spacing: 20

                // Serwery i modele na serwerze.
                ColumnLayout {
                    id: serversColumn
                    Layout.fillWidth: true
                    Layout.alignment: Qt.AlignTop
                    spacing: 12

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: 12
                        SectionTitle { text: "Serwery modeli" }
                        Text {
                            Layout.fillWidth: true
                            text: "Używany serwer dostaje raport, opisy kart i agenta — chyba że rola jest przypięta do innego."
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textMuted
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                            elide: Text.ElideRight
                        }
                    }

                    GridLayout {
                        Layout.fillWidth: true
                        columns: root.columns
                        columnSpacing: 14
                        rowSpacing: 14
                        Repeater {
                            id: cards
                            model: root.serverList
                            ServerCard {
                                required property var modelData
                                Layout.fillWidth: true
                                Layout.alignment: Qt.AlignTop
                                Layout.preferredWidth: 330
                                // Jeden serwer nie rozciąga się na całą szerokość — karta ma szerokość kolumny siatki.
                                Layout.maximumWidth: Math.max(330, (serversColumn.width - 14 * (root.columns - 1)) / root.columns)
                                server: modelData
                                status: root.status
                                page: root
                                editing: root.editing === modelData.name
                                confirmingRemove: root.removing === modelData.name
                                flash: root.flashServer === modelData.name
                                onEditRequested: root.edit(modelData.name)
                                onEditClosed: root.editing = ""
                                onRemoveAsked: root.askRemove(modelData.name)
                                onRemoveCancelled: root.removing = ""
                            }
                        }
                    }

                    // Dodanie serwera: przerywana ramka albo formularz.
                    Item {
                        id: addCard
                        objectName: "addCard"
                        Layout.fillWidth: true
                        implicitHeight: root.adding ? addBox.implicitHeight : addPrompt.implicitHeight

                        DashedFrame {
                            anchors.fill: parent
                            visible: !root.adding
                            radius: Tide.Tokens.radiusBar
                            strokeColor: Tide.Theme.alpha(addArea.containsMouse ? Tide.Tokens.accent : Tide.Tokens.textPrimary,
                                                          addArea.containsMouse ? 0.5 : 0.16)
                        }
                        RowLayout {
                            id: addPrompt
                            anchors.left: parent.left
                            anchors.right: parent.right
                            visible: !root.adding
                            spacing: 12
                            Item { implicitWidth: 4; implicitHeight: 52 }
                            Glyph {
                                Layout.preferredWidth: 16
                                Layout.preferredHeight: 16
                                name: "plus"
                                color: Tide.Tokens.accent
                            }
                            Text {
                                text: "Dodaj serwer"
                                textFormat: Text.PlainText
                                color: Tide.Tokens.accent
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 14
                                font.weight: Font.DemiBold
                            }
                            Text {
                                Layout.fillWidth: true
                                text: "Tu dodasz Mac Studio, gdy stanie w sieci — wystarczy jego adres, np. http://mac-studio:11434."
                                textFormat: Text.PlainText
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                                elide: Text.ElideRight
                            }
                            Item { implicitWidth: 4 }
                        }
                        MouseArea {
                            id: addArea
                            anchors.fill: parent
                            visible: !root.adding
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: root.openAdd()
                        }
                        Rectangle {
                            id: addBox
                            objectName: "addBox"
                            anchors.left: parent.left
                            anchors.right: parent.right
                            visible: root.adding
                            implicitHeight: addColumn.implicitHeight + 32
                            radius: Tide.Tokens.radiusBar
                            color: Tide.Tokens.backgroundView
                            border.width: 1
                            border.color: Tide.Theme.alpha(Tide.Tokens.accent, 0.35)
                            ColumnLayout {
                                id: addColumn
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.top: parent.top
                                anchors.margins: 16
                                spacing: 6
                                Text {
                                    text: "Nowy serwer modeli"
                                    textFormat: Text.PlainText
                                    color: Tide.Tokens.textPrimary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 15
                                    font.weight: Font.DemiBold
                                }
                                Note {
                                    Layout.fillWidth: true
                                    text: "Na przykład Mac Studio w sieci. „Sprawdź” łączy się bez zapisywania; po dodaniu "
                                          + "przełączysz się na niego przyciskiem „Używaj tego serwera”."
                                }
                                ServerForm {
                                    id: addForm
                                    objectName: "addForm"
                                    Layout.fillWidth: true
                                    Layout.maximumWidth: 560
                                    Layout.topMargin: 4
                                    adding: true
                                    status: root.status
                                    testing: root.busy["add:test"] === true
                                    saving: root.busy["add:save"] === true
                                    testNote: root.notes["add:test"] || null
                                    saveNote: root.notes["add:save"] || null
                                    onTestRequested: request => root.send("TestEndpoint", request, "add:test")
                                    onSaveRequested: request => root.send("SetEndpoint", request, "add:save")
                                    onCancelled: root.adding = false
                                }
                            }
                        }
                    }

                    ServerModels {
                        id: serverModels
                        Layout.fillWidth: true
                        Layout.topMargin: 12
                        status: root.status
                        page: root
                        selected: root.modelsServer
                        onServerPicked: name => root.chosenServer = name
                    }
                }

                // Do czego używamy modeli.
                Rectangle {
                    objectName: "usage"
                    Layout.preferredWidth: 420
                    Layout.maximumWidth: 420
                    Layout.alignment: Qt.AlignTop
                    implicitHeight: usage.implicitHeight + 40
                    radius: Tide.Tokens.radiusCard
                    color: Tide.Tokens.backgroundCard

                    ColumnLayout {
                        id: usage
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: 20
                        spacing: 10

                        SectionTitle { text: "Do czego używamy modeli" }
                        Text {
                            Layout.topMargin: 4
                            text: "Raport poranny i opisy kart"
                            textFormat: Text.PlainText
                            color: Tide.Tokens.textPrimary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 15
                            font.weight: Font.DemiBold
                        }
                        Segmented {
                            objectName: "reportSwitch"
                            options: Logic.reportModes()
                            current: root.reportMode
                            pixelSize: 13
                            itemHeight: 30
                            enabled: root.status && !!root.status.report && !root.busy["report"]
                            onPicked: value => { if (value !== root.reportMode) root.send("SetReportSummary", value, "report") }
                        }
                        Repeater {
                            model: Logic.reportModes()
                            RowLayout {
                                id: modeRow
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 8
                                Text {
                                    Layout.alignment: Qt.AlignTop
                                    Layout.preferredWidth: 78
                                    text: modeRow.modelData.label
                                    textFormat: Text.PlainText
                                    color: modeRow.modelData.value === root.reportMode ? Tide.Tokens.accent : Tide.Tokens.textSecondary
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 12
                                    font.weight: modeRow.modelData.value === root.reportMode ? Font.DemiBold : Font.Normal
                                }
                                Text {
                                    objectName: "modeText"
                                    Layout.fillWidth: true
                                    text: Logic.reportText(root.status, modeRow.modelData.value)
                                    textFormat: Text.PlainText
                                    color: modeRow.modelData.value === root.reportMode ? Tide.Tokens.textPrimary : Tide.Tokens.textMuted
                                    font.family: Tide.Theme.uiFamily
                                    font.pixelSize: 12
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }
                        Repeater {
                            model: root.ready ? Logic.reportNotes(root.status) : []
                            Note {
                                required property var modelData
                                objectName: "reportNote"
                                Layout.fillWidth: true
                                tone: modelData.tone
                                text: modelData.text
                            }
                        }
                        Note {
                            objectName: "reportError"
                            Layout.fillWidth: true
                            tone: "error"
                            text: root.notes["report"] && root.notes["report"].error ? root.notes["report"].text : ""
                        }

                        Repeater {
                            model: root.roles
                            ColumnLayout {
                                id: roleBlock
                                required property var modelData
                                Layout.fillWidth: true
                                spacing: 12
                                Rectangle {
                                    Layout.fillWidth: true
                                    Layout.topMargin: 4
                                    implicitHeight: 1
                                    color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.07)
                                }
                                RoleRow {
                                    objectName: "role_" + roleBlock.modelData.id
                                    Layout.fillWidth: true
                                    row: roleBlock.modelData
                                    status: root.status
                                    page: root
                                    advanced: root.advanced[roleBlock.modelData.id] === true
                                    onAdvancedChanged: if (advanced !== (root.advanced[roleBlock.modelData.id] === true))
                                                           root.advanced = root.setIn(root.advanced, roleBlock.modelData.id, advanced)
                                }
                            }
                        }
                    }
                }
            }
        }

        // Pusty stan: usługa nie działa albo nie podała serwerów.
        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            visible: !root.ready

            Rectangle {
                objectName: "problemCard"
                anchors.centerIn: parent
                width: Math.min(500, parent.width - 48)
                height: problemBody.implicitHeight + 56
                radius: Tide.Tokens.radiusCard
                color: Tide.Tokens.backgroundView
                border.width: 1
                border.color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)

                ColumnLayout {
                    id: problemBody
                    anchors.left: parent.left
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.margins: 28
                    spacing: 10
                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter
                        implicitWidth: 52
                        implicitHeight: 52
                        radius: Tide.Tokens.radiusTile
                        color: Tide.Theme.alpha(Tide.Tokens.textMuted, 0.10)
                        Glyph {
                            anchors.centerIn: parent
                            width: 26
                            height: 26
                            name: "chip"
                            color: Tide.Tokens.textMuted
                        }
                    }
                    Text {
                        objectName: "problemTitle"
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: root.problem ? root.problem.title : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textPrimary
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 15
                        font.weight: Font.DemiBold
                        wrapMode: Text.WordWrap
                    }
                    Text {
                        objectName: "problemText"
                        Layout.fillWidth: true
                        horizontalAlignment: Text.AlignHCenter
                        text: root.problem ? root.problem.text : ""
                        textFormat: Text.PlainText
                        color: Tide.Tokens.textMuted
                        font.family: Tide.Theme.uiFamily
                        font.pixelSize: 13
                        wrapMode: Text.WordWrap
                    }
                }
            }
        }
    }
}

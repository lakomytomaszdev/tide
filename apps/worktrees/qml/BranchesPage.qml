// Tryb „Gałęzie” okna Worktrees (K-99, PT-388): wybór projektu Tablicy i drzewo mapy gałęzi z `Git.BranchMap`
// (baza → gałęzie kart → gałęzie ticketów, z grupą „baza nieznana”). Akcje zaznaczonej gałęzi karty: „Otwórz kartę”
// (sygnał dla okna — przełącza na Tablicę) i „Zmiany gałęzi” (widok diffa K-36, `Git.Diff` źródła `card`).
// Strona sama nie rozmawia z usługą: backend daje branchMap(tag, żądanie), branchMapReplied(tag, json), gitDiff,
// replied (widok diffa), boardChanged, branchProject i setBranchProject. Teksty z danych (tytuły, nazwy gałęzi)
// są zwykłym tekstem, ścieżki repozytorium z UI nie ma — projekt wskazuje slug.
//
// „Scal…” (PT-395) przy domkniętej, niescalonej karcie otwiera wspólne okno scalania (git/MergeWindow.qml) — ten sam
// kontrakt co w Tablicy. Strona tylko przekłada jego sygnały na listCardBranches / previewCardMerge / mergeCard
// backendu (z tagiem przypiętym przez `track`) i oddaje mu odpowiedzi z `boardReplied`; zgodę wysyła wyłącznie
// „Scal do <cel>” okna. Zmiana projektu i zniknięcie karty z mapy zamykają okno; odświeżenie mapy go nie zamyka.
// Po scaleniu mapa czyta się od nowa, a węzeł pokazuje „scalona do <cel>”. Bez „Zleć rozwiązanie konfliktów”.
import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2
import "tide" as Tide
import "git" as Git
import "branches.js" as Branches

Rectangle {
    id: page

    property var backend: null
    property bool active: true

    signal openCardRequested(string card)

    property var projects: []              // z Git.BranchMap({}): [{slug, name, repo, cards}]
    property bool projectsLoaded: false
    property string projectsError: ""
    property string project: ""            // pokazywany projekt (slug)
    property var reply: null               // ostatnia odpowiedź Git.BranchMap({project}) albo null
    property bool loading: false
    property string selectedCard: ""       // ID zaznaczonej gałęzi karty
    property string note: ""               // problem z zapisem ustawień (wybór działa do końca sesji)
    property var diffRequest: null
    property bool stale: false             // Tablica zmieniła się, gdy widok był ukryty
    property int serial: 0                 // numer żądania: spóźnione odpowiedzi są pomijane
    property int mergeSerial: 0            // numer żądania okna scalania (tag; stare odpowiedzi pomija samo okno)
    property string mergeNotice: ""        // „Scalono K-5 do main.” po udanym scaleniu z mapy
    property alias mergeWindow: mergeWindow

    readonly property bool diffOpen: diffRequest !== null
    readonly property var selectedNode: {
        const row = selectedCard !== "" ? treeRows.find(r => r.node.kind === "card" && r.node.card === selectedCard) : null
        return row ? row.node : null
    }
    readonly property bool mergeAvailable: Branches.canMerge(selectedNode)
    readonly property var treeRows: reply && reply.ok === true ? Branches.rows(reply.nodes, reply.edges) : []
    readonly property string truncatedText: reply && reply.ok === true ? Branches.shownText(reply) : ""
    readonly property bool failed: reply !== null && reply.ok !== true
    readonly property bool empty: reply !== null && reply.ok === true && !Branches.hasBranches(reply)

    function refresh() {
        if (!backend) return
        stale = false
        serial += 1
        backend.branchMap("projects:" + serial, "{}")
    }

    function loadMap() {
        if (!backend || project === "") return
        loading = true
        serial += 1
        backend.branchMap("map:" + serial, JSON.stringify(Branches.mapRequest(project)))
    }

    function choose(slug) {
        if (slug === project) return
        mergeWindow.close()   // okno dotyczy karty innego projektu: zamknięcie nie wysyła zgody
        mergeNotice = ""
        project = slug
        reply = null
        selectedCard = ""
        note = backend ? backend.setBranchProject(slug) : ""
        loadMap()
    }

    function select(card) {
        selectedCard = selectedCard === card ? "" : card
    }

    function openCard() {
        if (selectedCard !== "") openCardRequested(selectedCard)
    }

    function showChanges() {
        if (selectedCard !== "") diffRequest = Branches.cardDiffRequest(selectedCard)
    }

    function closeDiff() {
        diffRequest = null
        if (mergeWindow.opened) mergeWindow.forceActiveFocus()   // „Zmiany gałęzi” z okna scalania wracają do okna
    }

    // „Scal…”: okno bez wskazanego celu — domyślny cel i skróty bierze z odpowiedzi `ListCardBranches`.
    function openMerge() {
        const node = selectedNode
        if (!Branches.canMerge(node)) return
        mergeNotice = ""
        mergeWindow.contextKey = project + ":" + node.card
        mergeWindow.baseRef = Branches.cardBaseRef(node)
        mergeWindow.startLine = Branches.cardStartLine(node)
        mergeWindow.open(node.card, "", "")
    }

    function mergeTag(kind) {
        mergeSerial += 1
        return kind + ":" + mergeWindow.cardId + ":" + mergeSerial
    }

    // Odpowiedź `/Board` dla okna scalania; okno samo pomija odpowiedź z innym tagiem niż bieżące żądanie.
    function gotBoardReply(tag, text) {
        const reply = parse(text)
        if (tag.indexOf("merge-targets:") === 0) {
            mergeWindow.receiveBranches(tag, reply)
            return
        }
        if (tag.indexOf("preview-merge:") !== 0 && tag.indexOf("merge-card:") !== 0) return
        const merged = mergeWindow.receive(tag, Branches.mergeErrorText(reply), reply)
        if (merged) {
            mergeNotice = Branches.mergedText(merged, reply)
            loadMap()   // węzeł pokazuje „scalona do <cel>” z dziennika karty
        }
    }

    function parse(text) {
        try {
            const value = JSON.parse(text)
            return value !== null && typeof value === "object" ? value : null
        } catch (e) {
            return null
        }
    }

    function gotReply(tag, text) {
        const isProjects = tag.indexOf("projects:") === 0
        if (!isProjects && tag.indexOf("map:") !== 0) return
        if (Number(tag.slice(tag.indexOf(":") + 1)) !== serial) return  // spóźniona odpowiedź
        const value = parse(text)
        if (isProjects) {
            projectsLoaded = true
            if (value === null || value.ok !== true) {
                projectsError = Branches.errorText(value)
                projects = []
                reply = null
                loading = false
                return
            }
            projectsError = ""
            projects = Array.isArray(value.projects) ? value.projects : []
            const next = Branches.pickProject(projects, backend ? backend.branchProject : "")
            if (next !== project) {
                project = next
                selectedCard = ""
            }
            if (project === "") {
                reply = null
                loading = false
                return
            }
            loadMap()
            return
        }
        loading = false
        reply = value !== null ? value : { ok: false, message: "" }
        if (selectedCard !== "" && !treeRows.some(r => r.node.kind === "card" && r.node.card === selectedCard))
            selectedCard = ""
        // Karta okna scalania zniknęła z poprawnie odczytanej mapy: zamknij okno (bez zgody).
        if (mergeWindow.opened && reply.ok === true
                && !treeRows.some(r => r.node.kind === "card" && r.node.card === mergeWindow.cardId))
            mergeWindow.close()
    }

    onActiveChanged: if (active && stale) refresh()
    Component.onCompleted: refresh()

    color: Tide.Tokens.backgroundWindow
    focus: true
    Keys.onEscapePressed: if (!diffOpen) selectedCard = ""

    Connections {
        target: page.backend
        function onBranchMapReplied(tag, text) { page.gotReply(tag, text) }
        function onBoardReplied(tag, text) { page.gotBoardReply(tag, text) }
        function onBoardChanged() {
            if (page.active) refreshTimer.restart()
            else page.stale = true
        }
    }

    // Kilka zmian Tablicy pod rząd (zapisy dzienników) to jedno odświeżenie.
    Timer {
        id: refreshTimer
        interval: 300
        onTriggered: page.refresh()
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // --- nagłówek: tytuł, wybór projektu, „pokazano N z M” ----------------------------------
        ColumnLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            Layout.topMargin: 18
            Layout.bottomMargin: 12
            spacing: 10

            RowLayout {
                Layout.fillWidth: true
                spacing: 16
                Text {
                    Layout.fillWidth: true
                    text: "Gałęzie kart i ticketów"
                    color: Tide.Tokens.textPrimary
                    textFormat: Text.PlainText
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 20
                    font.weight: Font.DemiBold
                    elide: Text.ElideRight
                    Accessible.role: Accessible.Heading
                }
                Text {
                    objectName: "branchTruncated"
                    visible: text !== ""
                    text: page.truncatedText
                    color: Tide.Tokens.textMuted
                    textFormat: Text.PlainText
                    font.family: Tide.Theme.uiFamily
                    font.pixelSize: 13
                }
            }

            Flow {
                Layout.fillWidth: true
                spacing: 6
                visible: page.projects.length > 0

                Repeater {
                    model: page.projects
                    Rectangle {
                        objectName: "branchProject"
                        required property var modelData
                        readonly property bool current: page.project === modelData.slug
                        readonly property string label: chipLabel.text
                        implicitHeight: 28
                        implicitWidth: chipLabel.implicitWidth + 24
                        radius: height / 2
                        color: current ? Tide.Tokens.accent
                             : chipArea.containsMouse ? Qt.lighter(Tide.Tokens.backgroundCard, 1.12) : Tide.Tokens.backgroundCard
                        Accessible.role: Accessible.RadioButton
                        Accessible.name: chipLabel.text
                        Accessible.checked: current
                        Accessible.onPressAction: page.choose(modelData.slug)
                        Text {
                            id: chipLabel
                            anchors.centerIn: parent
                            text: modelData.name + " · " + modelData.cards
                            textFormat: Text.PlainText
                            color: current ? Tide.Tokens.backgroundDesktop : Tide.Tokens.textSecondary
                            font.family: Tide.Theme.uiFamily
                            font.pixelSize: 12
                            font.weight: current ? Font.DemiBold : Font.Normal
                        }
                        MouseArea {
                            id: chipArea
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: page.choose(modelData.slug)
                        }
                    }
                }
            }

            Text {
                objectName: "branchNote"
                Layout.fillWidth: true
                visible: text !== ""
                text: page.note
                wrapMode: Text.WordWrap
                color: Tide.Tokens.negative
                textFormat: Text.PlainText
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 12
            }
            Text {
                objectName: "branchMergeNotice"
                Layout.fillWidth: true
                visible: text !== ""
                text: page.mergeNotice
                wrapMode: Text.WrapAnywhere
                color: Tide.Tokens.positive
                textFormat: Text.PlainText
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                Accessible.role: Accessible.StaticText
            }
        }

        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
        }

        // --- komunikaty: błąd, pusty stan, ładowanie -----------------------------------------------
        Text {
            objectName: "branchError"
            Layout.fillWidth: true
            Layout.margins: 24
            visible: page.projectsError !== "" || page.failed
            text: page.projectsError !== "" ? page.projectsError : Branches.errorText(page.reply)
            wrapMode: Text.WordWrap
            color: Tide.Tokens.negative
            textFormat: Text.PlainText
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 14
            Accessible.role: Accessible.StaticText
        }
        Text {
            objectName: "branchEmpty"
            Layout.fillWidth: true
            Layout.margins: 24
            visible: page.empty || (page.projectsLoaded && page.projectsError === "" && page.projects.length === 0)
            text: page.projects.length === 0 ? "Żaden projekt Tablicy nie ma repozytorium."
                                             : "Ten projekt nie ma jeszcze kart w przepływie."
            wrapMode: Text.WordWrap
            color: Tide.Tokens.textMuted
            textFormat: Text.PlainText
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 14
        }
        Text {
            objectName: "branchLoading"
            Layout.fillWidth: true
            Layout.margins: 24
            visible: page.loading && page.reply === null
            text: "Czytam mapę gałęzi…"
            color: Tide.Tokens.textMuted
            font.family: Tide.Theme.uiFamily
            font.pixelSize: 14
        }

        // --- drzewo ------------------------------------------------------------------------------
        Flickable {
            id: flick
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            contentWidth: width
            contentHeight: tree.implicitHeight + 24
            boundsBehavior: Flickable.StopAtBounds
            QQC2.ScrollBar.vertical: Tide.ScrollBar {}

            Column {
                id: tree
                x: 24
                y: 12
                width: flick.width - 48
                spacing: 4

                Repeater {
                    model: page.treeRows
                    Rectangle {
                        id: node
                        objectName: "branchNode"
                        required property var modelData
                        readonly property var info: modelData.node
                        readonly property bool isCard: info.kind === "card"
                        readonly property bool isBranch: isCard || info.kind === "ticket"
                        readonly property bool current: isCard && page.selectedCard === info.card
                        readonly property string titleText: Branches.titleText(info)
                        readonly property string detailText: Branches.detailText(info)
                        x: modelData.depth * 24
                        width: tree.width - x
                        implicitHeight: isBranch ? 52 : 34
                        height: implicitHeight
                        radius: Tide.Tokens.radiusFrame
                        color: !isBranch ? "transparent"
                             : nodeArea.containsMouse && isCard ? Qt.lighter(Tide.Tokens.backgroundCard, 1.1)
                             : Tide.Tokens.backgroundCard
                        border.width: current ? 1 : 0
                        border.color: Tide.Tokens.accent
                        Accessible.role: isCard ? Accessible.RadioButton : Accessible.ListItem
                        Accessible.name: title.text + (detail.text !== "" ? ", " + detail.text : "")
                        Accessible.checked: current
                        Accessible.onPressAction: if (isCard) page.select(info.card)

                        MouseArea {
                            id: nodeArea
                            anchors.fill: parent
                            enabled: node.isCard
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: page.select(node.info.card)
                        }

                        Column {
                            anchors.left: parent.left
                            anchors.leftMargin: 12
                            anchors.right: parent.right
                            anchors.rightMargin: 12
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Text {
                                id: title
                                objectName: "branchTitle"
                                width: parent.width
                                text: node.titleText
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: node.isBranch ? Tide.Tokens.textPrimary : Tide.Tokens.textSecondary
                                font.family: node.info.kind === "base" ? Tide.Theme.monoFamily : Tide.Theme.uiFamily
                                font.pixelSize: node.isBranch ? 14 : 13
                                font.weight: node.isBranch ? Font.Medium : Font.DemiBold
                            }
                            Text {
                                id: detail
                                objectName: "branchState"
                                width: parent.width
                                visible: text !== ""
                                text: node.detailText
                                textFormat: Text.PlainText
                                elide: Text.ElideRight
                                color: Tide.Tokens.textMuted
                                font.family: Tide.Theme.uiFamily
                                font.pixelSize: 12
                            }
                        }
                    }
                }
            }
        }

        // --- akcje zaznaczonej gałęzi karty ---------------------------------------------------------
        Rectangle {
            Layout.fillWidth: true
            implicitHeight: 1
            color: Tide.Theme.alpha(Tide.Tokens.textPrimary, 0.06)
        }
        RowLayout {
            Layout.fillWidth: true
            Layout.leftMargin: 24
            Layout.rightMargin: 24
            Layout.topMargin: 10
            Layout.bottomMargin: 10
            spacing: 10

            Text {
                Layout.fillWidth: true
                text: page.selectedCard !== "" ? "Zaznaczono kartę " + page.selectedCard
                                               : "Zaznacz gałąź karty, żeby ją otworzyć albo zobaczyć jej zmiany."
                color: Tide.Tokens.textMuted
                textFormat: Text.PlainText
                font.family: Tide.Theme.uiFamily
                font.pixelSize: 13
                elide: Text.ElideRight
            }
            Tide.Button {
                objectName: "branchOpenCard"
                text: "Otwórz kartę"
                enabled: page.selectedCard !== ""
                onClicked: page.openCard()
            }
            Tide.Button {
                objectName: "branchChanges"
                text: "Zmiany gałęzi"
                enabled: page.selectedCard !== ""
                onClicked: page.showChanges()
            }
            Tide.Button {
                objectName: "branchMerge"
                visible: page.mergeAvailable
                text: "Scal…"
                onClicked: page.openMerge()
            }
        }
    }

    // Okno scalania (wspólny komponent git/MergeWindow.qml): strona tylko przekłada sygnały na wywołania usługi.
    // Pod widokiem diffa: „Zmiany gałęzi” z okna otwierają diff nad nim, a Esc wraca do okna.
    Git.MergeWindow {
        id: mergeWindow
        anchors.fill: parent
        z: 90
        onBranchesRequested: text => {
            const tag = page.mergeTag("merge-targets")
            trackBranches(tag)
            page.backend.listCardBranches(tag, cardId, JSON.stringify({purpose: "merge", text: text, limit: 50}))
        }
        onPreviewRequested: targetRef => {
            const tag = page.mergeTag("preview-merge")
            track(tag)
            page.backend.previewCardMerge(tag, cardId, JSON.stringify({targetRef: targetRef}))
        }
        onMergeRequested: request => {
            const tag = page.mergeTag("merge-card")
            track(tag)
            page.backend.mergeCard(tag, cardId, JSON.stringify(request))
        }
        onChangesRequested: cardId => { page.diffRequest = Branches.cardDiffRequest(cardId) }
        onClosed: page.forceActiveFocus()
    }

    // Zmiany gałęzi karty w widoku diffa (K-36): nakładka na stronę; Esc i „Zamknij” wracają do mapy.
    Loader {
        id: diffLoader
        objectName: "branchDiffOverlay"
        anchors.fill: parent
        z: 100
        active: page.diffRequest !== null
        sourceComponent: Component {
            Git.DiffView {
                objectName: "branchDiffView"
                request: page.diffRequest
                backend: page.backend
                onCloseRequested: page.closeDiff()
                Component.onCompleted: forceActiveFocus()
            }
        }
    }
}
